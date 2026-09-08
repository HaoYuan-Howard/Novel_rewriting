#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Reviewer typed action 的持久来源证明。

Stage5 reviewer rewrite request 会把一次 Commander 决定升级成后续付费 writer 的
canonical 权限。临时 trusted JSON 只能运输这份决定，不能靠自报的 record/session/
seat 摘要铸权；本模块因此在任何 request 首写、current probe 与付费执行前，统一复核
内容寻址 action record、稳定 business、Reviewer seat、logical session、provider
attempt 与成本账。模块刻意不 import ``agent``，使 CLI producer 能复用而不形成环。
"""
from __future__ import annotations

import hashlib
import json
import os
import time
import uuid
from contextlib import contextmanager, nullcontext
from collections.abc import Iterator
from pathlib import Path

from contracts.schema.api import Stage5ReviewerRewriteRequest
from contracts.artifacts import STAGE5_REVIEWER_REWRITE_REQUEST
from contracts.schema.orchestration import (
    ReviewerActionRecord, ReviewerRewriteAuthorityReceipt,
    reviewer_rewrite_authority_receipt_sha256,
)
from contracts.schema.stage5 import (
    ReviewerRewriteBatchRequest, ReviewerRewriteBatchTrustedInput,
)
from novel import agent_session_audit, paths, prov, work_units
from novel.appendlog import path_transaction_lock
from novel.cli.exit_codes import CRASH
from novel.errors import ArtifactUnreadable, NovelError
from novel.llm.agent_runs import canonical_sha256
from novel.llm.ledger import ledger_path as cost_ledger_path, read_jsonl_rows
from novel.orchestrator import (
    admission, queue, reviewer_contract, reviewer_seat_ledger,
    reviewer_tasks, uploads,
)


def recorded_response_sha256(record: ReviewerActionRecord) -> str:
    """从 durable full action 复原 Commander 原回答的 canonical digest。"""
    if record.node is None:
        raise reviewer_tasks.ReviewerTaskError(
            'Stage5 durable disposition record 缺 node action')
    action: dict[str, object] = {
        'kind': 'node',
        'node': record.node,
        'flags': list(record.flags or ()),
    }
    if record.input_payload is not None:
        action['input_payload'] = record.input_payload
    document = {
        'schema': reviewer_tasks.REQUEST_SCHEMA,
        'surface_seal': record.surface_seal,
        'waiting_on': record.waiting_on,
        'action': action,
    }
    raw = json.dumps(
        document, ensure_ascii=False, sort_keys=True,
        separators=(',', ':'), allow_nan=False,
    ).encode('utf-8') + b'\n'
    return hashlib.sha256(raw).hexdigest()


@contextmanager
def record_owner_guard(
    base: str | os.PathLike[str], *, record_parent_run_id: str,
    current_parent_run_id: str | None,
) -> Iterator[bool]:
    """同 parent 可消费自己的闭席；跨 parent 必须持 owner-stopped lease。"""
    same_parent = record_parent_run_id == current_parent_run_id
    if same_parent:
        yield True
        return
    with admission.require_owner_stopped(base, record_parent_run_id):
        yield False


def _validated_work_unit(
    base: str | os.PathLike[str], sid: str, record: ReviewerActionRecord,
) -> work_units.WorkUnitBudget:
    """保持 typed action 消费边界，工作单元损坏仍按审阅任务错误拒绝。"""
    try:
        return work_units.validate_reviewer_record(base, sid, record)
    except (work_units.WorkUnitRefused, OSError, ValueError, NovelError) as exc:
        raise reviewer_tasks.ReviewerTaskError(str(exc)) from exc


def _work_unit_evidence_hashes(
    base: str | os.PathLike[str], sid: str, record: ReviewerActionRecord,
) -> tuple[str, str, str]:
    """封存本次内容响应的闭集；后续连续审阅不改变已授权响应的证据。"""
    budget = _validated_work_unit(base, sid, record)
    ledger = budget._read()
    turn = budget.turn(record.work_unit_turn_id)
    if (
        ledger.node != 'stage5.review' or ledger.scope != 'closure'
        or turn.state not in {'delivered', 'adopted'}
        or record.reviewer_seat_turn_id != turn.turn_id
        or not turn.attempts
    ):
        raise reviewer_tasks.ReviewerTaskError(
            'Stage5 durable disposition 缺当前工作单元的已交付内容证据')
    attempt = turn.attempts[-1]
    if attempt.outcome != 'ok' or not attempt.provider_session_id:
        raise reviewer_tasks.ReviewerTaskError(
            'Stage5 durable disposition 内容轮缺成功 provider attempt')
    sessions = [session for session in agent_session_audit.fold(
        agent_session_audit.read_events(base))
        if session.session_run_id == record.reviewer_session_run_id]
    if len(sessions) != 1:
        raise reviewer_tasks.ReviewerTaskError('Stage5 工作单元缺唯一 logical session')
    session = sessions[0]
    attempts = [row for row in session.attempts if row.attempt_id == attempt.attempt_id]
    turns = [row for row in session.turns if row.attempt_id == attempt.attempt_id]
    if (session.start.role != 'commander' or session.start.node is not None
            or len(attempts) != 1 or len(turns) != 1):
        raise reviewer_tasks.ReviewerTaskError('Stage5 工作单元 session/attempt/turn 不唯一')
    audit_attempt, audit_turn = attempts[0], turns[0]
    if (
        audit_attempt.error_kind is not None or audit_turn.error_kind is not None
        or audit_attempt.parent_run_id != turn.run_id
        or audit_turn.parent_run_id != turn.run_id
        or audit_attempt.provider_session_id != attempt.provider_session_id
        or audit_turn.provider_session_id != attempt.provider_session_id
        or audit_attempt.prompt_sha256 != audit_turn.prompt_sha256
        or audit_attempt.schema_sha256 != audit_turn.schema_sha256
        or audit_attempt.profile != audit_turn.profile
        or audit_attempt.requested_model != audit_turn.requested_model
        or audit_attempt.requested_effort != audit_turn.requested_effort
    ):
        raise reviewer_tasks.ReviewerTaskError('Stage5 工作单元 attempt 与 provider turn 不闭合')
    costs = [row for row in read_jsonl_rows(cost_ledger_path(base))
             if row.get('run_id') == turn.run_id
             and row.get('session_run_id') == record.reviewer_session_run_id
             and row.get('sid') == sid and row.get('role') == 'commander'
             and row.get('attempt_id') == attempt.attempt_id
             and row.get('activity') == f'reviewer:{queue.WAITING_ON_STAGE5_CLOSURE}']
    if len(costs) != 1:
        raise reviewer_tasks.ReviewerTaskError('Stage5 工作单元成本证据不唯一')
    cost = costs[0]
    if (
        cost.get('outcome') != 'ok' or cost.get('exit_code') != 0
        or cost.get('error_kind') is not None
        or cost.get('response_sha256') != record.provider_response_sha256
        or cost.get('provider_session_id') != attempt.provider_session_id
        or cost.get('code') != audit_turn.start_code_version
        or cost.get('request_model') != audit_turn.requested_model
        or cost.get('model') != audit_turn.actual_model
        or cost.get('reasoning_effort') != audit_turn.requested_effort
        or cost.get('actual_reasoning_effort') != audit_turn.actual_effort
        or cost.get('protocol_version') != audit_turn.protocol_version
        or cost.get('cli_version') != audit_turn.cli_version
    ):
        raise reviewer_tasks.ReviewerTaskError(
            'Stage5 工作单元成本账未证明同 attempt/response exit 0')
    # logical session 可继续，内容轮也会从 delivered 转 adopted；封存只绑定
    # 原请求、响应、typed 决定与该次 attempt，不绑定整本可变预算账或 session ended。
    return (
        canonical_sha256({
            'unit_id': budget.unit_id, 'task_id': ledger.task_id,
            'turn_id': turn.turn_id, 'ordinal': turn.ordinal,
            'run_id': turn.run_id, 'session_run_id': turn.session_run_id,
            'request_sha256': turn.request_sha256,
            'request_payload_sha256': turn.request_payload_sha256,
            'response_sha256': turn.response_sha256,
            'recovery_sha256': turn.recovery_sha256,
            'attempts': [row.model_dump(mode='json') for row in turn.attempts],
        }),
        canonical_sha256({
            'session_run_id': session.session_run_id,
            'start': session.start.to_dict(),
            'attempt': audit_attempt.to_dict(), 'turn': audit_turn.to_dict(),
        }),
        canonical_sha256(cost),
    )


def _record_evidence_path(
    base: str | os.PathLike[str], sid: str, record: ReviewerActionRecord,
) -> Path:
    if record.work_unit_id is not None:
        return _validated_work_unit(base, sid, record).path
    return reviewer_seat_ledger.ledger_path(
        base, sid, waiting_on=record.waiting_on,
        business_sha256=record.business_sha256)


def _record_owner_run_id(
    base: str | os.PathLike[str], sid: str, record: ReviewerActionRecord,
) -> str:
    if record.work_unit_id is not None:
        budget = _validated_work_unit(base, sid, record)
        return budget.turn(record.work_unit_turn_id).run_id
    return record.run_id


def validate_stage5_action_record_ledger(
    base: str | os.PathLike[str], sid: str, *,
    record: ReviewerActionRecord,
    current_parent_run_id: str | None = None,
    read_only: bool = False,
    _owner_guard_same_parent: bool | None = None,
) -> None:
    """复核 Stage5 durable-first 席位；恢复调用可幂等补尾。

    ``read_only`` 只供已经 sealed 的 authority 复读：不借任何 parent 身份、
    不获取 owner-stopped lease、也绝不补 session/seat；调用方随后还要要求
    成功闭席、session ended、无 pending 的 sealed 闭集。

    ``_owner_guard_same_parent`` 只供已经在 ``record_owner_guard`` 内的
    seal 事务传入；它让账本补尾复用外层 lease，不能在永久凭据提交前提前
    释放 old-owner-stopped 证明。
    """
    if (
        record.node != 'stage5.review'
        or record.flags not in {
            ('--retain-pretrim-batch', '@reviewer-input'),
            ('--request-reviewer-rewrite', '@reviewer-input'),
        }
        or record.waiting_on != queue.WAITING_ON_STAGE5_CLOSURE
        or record.reviewer_session_run_id is None
        or record.reviewer_seat_turn_id is None
        or record.provider_response_sha256 is None
    ):
        raise reviewer_tasks.ReviewerTaskError(
            'Stage5 durable disposition record 缺完整席位恢复身份')
    if (record.work_unit_id is None
            and recorded_response_sha256(record) != record.provider_response_sha256):
        raise reviewer_tasks.ReviewerTaskError(
            'Stage5 durable disposition record 与 provider 原回答 digest 不一致')
    if read_only and current_parent_run_id is not None:
        raise reviewer_tasks.ReviewerTaskError(
            'sealed reviewer authority 只读复核不得自报 parent 身份')
    if read_only and _owner_guard_same_parent is not None:
        raise reviewer_tasks.ReviewerTaskError(
            'sealed reviewer authority 只读复核不得借用 owner guard')
    expected_same_parent = _record_owner_run_id(base, sid, record) == current_parent_run_id
    if (
        _owner_guard_same_parent is not None
        and (
            not isinstance(_owner_guard_same_parent, bool)
            or _owner_guard_same_parent != expected_same_parent
        )
    ):
        raise reviewer_tasks.ReviewerTaskError(
            'Stage5 durable disposition 外层 owner guard 身份不闭合')
    same_parent = read_only or expected_same_parent
    try:
        if record.work_unit_id is not None:
            budget = _validated_work_unit(base, sid, record)
            source = budget.turn(record.work_unit_turn_id)
            # 恢复 record 的 transport parent 可能已改变；许可必须以实际
            # 内容调用的 owner 为准，不能用新包装冒充旧调用已结束。
            source_guard = (nullcontext(True) if read_only
                            or _owner_guard_same_parent is not None
                            else record_owner_guard(
                                base, record_parent_run_id=source.run_id,
                                current_parent_run_id=current_parent_run_id))
            with source_guard:
                _work_unit_evidence_hashes(base, sid, record)
            return
        if read_only:
            owner_guard = nullcontext(True)
        elif _owner_guard_same_parent is not None:
            owner_guard = nullcontext(_owner_guard_same_parent)
        else:
            owner_guard = record_owner_guard(
                base,
                record_parent_run_id=record.run_id,
                current_parent_run_id=current_parent_run_id,
            )
        with owner_guard:
            ledger = reviewer_seat_ledger.read(
                base, sid,
                waiting_on=record.waiting_on,
                business_sha256=record.business_sha256,
            )
            if ledger is None:
                raise reviewer_tasks.ReviewerTaskError(
                    'Stage5 durable disposition record 找不到原 reviewer 席位账')
            turns = [
                turn for turn in ledger.turns
                if turn.turn_id == record.reviewer_seat_turn_id
            ]
            if len(turns) != 1:
                raise reviewer_tasks.ReviewerTaskError(
                    'Stage5 durable disposition record 找不到唯一原 seat turn')
            turn = turns[0]
            if (
                turn.session_run_id != record.reviewer_session_run_id
                or turn.parent_run_id != record.run_id
                or turn.epoch_id != record.epoch_id
                or turn.purpose not in {'review', 'correction'}
                or ledger.waiting_on != record.waiting_on
                or ledger.business_sha256 != record.business_sha256
                or ledger.turns[-1].turn_id != turn.turn_id
                or turn.provider_response_sha256
                != record.provider_response_sha256
            ):
                raise reviewer_tasks.ReviewerTaskError(
                    'Stage5 durable disposition record 与原 seat/response 身份不闭合，'
                    '或已有更新 turn')
            pending = ledger.pending_correction
            if pending is not None:
                source_rows = [
                    row for row in ledger.turns
                    if row.turn_id == pending.source_turn_id
                ]
                if len(source_rows) != 1:
                    raise reviewer_tasks.ReviewerTaskError(
                        'Stage5 durable disposition pending correction 来源不唯一')
                source = source_rows[0]
                # correction 预留后、清旧 pending 前的唯一合法硬杀窗：pending
                # 恰指紧邻上一轮。指向本轮或更新轮说明拒因已经消费 action。
                if not (
                    turn.purpose == 'correction'
                    and source.ordinal + 1 == turn.ordinal
                    and source.session_run_id == turn.session_run_id
                ):
                    raise reviewer_tasks.ReviewerTaskError(
                        'Stage5 durable disposition 已有不早于原 action 的 pending '
                        'correction，拒绝重放旧回答')
            if not turn.attempts:
                raise reviewer_tasks.ReviewerTaskError(
                    'Stage5 durable disposition 原 turn 缺进程 attempt 证据')
            last_attempt = turn.attempts[-1]
            if (
                last_attempt.finished_at is None
                or last_attempt.exit_code != 0
                or last_attempt.error_kind is not None
                or not last_attempt.wire_evidence
                or not last_attempt.provider_session_id
            ):
                raise reviewer_tasks.ReviewerTaskError(
                    'Stage5 durable disposition 原 turn 没有 wire+exit0 attempt')

            sessions = [
                session for session in agent_session_audit.fold(
                    agent_session_audit.read_events(base))
                if session.session_run_id == record.reviewer_session_run_id
            ]
            if len(sessions) != 1:
                raise reviewer_tasks.ReviewerTaskError(
                    'Stage5 durable disposition 没有唯一 logical session 审计')
            session = sessions[0]
            audit_attempts = [
                attempt for attempt in session.attempts
                if attempt.attempt_id == last_attempt.attempt_id
            ]
            audit_turns = [
                audit_turn for audit_turn in session.turns
                if audit_turn.attempt_id == last_attempt.attempt_id
            ]
            if (
                session.start.parent_run_id != record.run_id
                or session.start.role != 'commander'
                or session.start.node is not None
                or len(audit_attempts) != 1
                or len(audit_turns) != 1
            ):
                raise reviewer_tasks.ReviewerTaskError(
                    'Stage5 durable disposition logical session/attempt/turn 不唯一')
            audit_attempt = audit_attempts[0]
            audit_turn = audit_turns[0]
            if (
                session.attempts[-1].attempt_id != last_attempt.attempt_id
                or session.turns[-1].attempt_id != last_attempt.attempt_id
                or audit_attempt.error_kind is not None
                or audit_turn.error_kind is not None
                or audit_attempt.parent_run_id != record.run_id
                or audit_turn.parent_run_id != record.run_id
                or audit_attempt.provider_session_id
                != last_attempt.provider_session_id
                or audit_turn.provider_session_id
                != last_attempt.provider_session_id
                or audit_attempt.prompt_sha256 != audit_turn.prompt_sha256
                or audit_attempt.schema_sha256 != audit_turn.schema_sha256
                or audit_attempt.profile != audit_turn.profile
                or audit_attempt.requested_model != audit_turn.requested_model
                or audit_attempt.requested_effort != audit_turn.requested_effort
            ):
                raise reviewer_tasks.ReviewerTaskError(
                    'Stage5 durable disposition attempt 与 provider turn 身份不闭合')

            cost_rows = [
                row for row in read_jsonl_rows(cost_ledger_path(base))
                if row.get('run_id') == record.run_id
                and row.get('session_run_id')
                == record.reviewer_session_run_id
                and row.get('sid') == sid
                and row.get('role') == 'commander'
                and row.get('attempt_id') == last_attempt.attempt_id
                and row.get('activity')
                == f'reviewer:{queue.WAITING_ON_STAGE5_CLOSURE}'
            ]
            if (
                len(cost_rows) != 1
                or cost_rows[0].get('outcome') != 'ok'
                or cost_rows[0].get('exit_code') != 0
                or cost_rows[0].get('error_kind') is not None
                or cost_rows[0].get('response_sha256')
                != record.provider_response_sha256
                or cost_rows[0].get('provider_session_id')
                != last_attempt.provider_session_id
                or cost_rows[0].get('code') != audit_turn.start_code_version
                or cost_rows[0].get('request_model')
                != audit_turn.requested_model
                or cost_rows[0].get('model') != audit_turn.actual_model
                or cost_rows[0].get('reasoning_effort')
                != audit_turn.requested_effort
                or cost_rows[0].get('actual_reasoning_effort')
                != audit_turn.actual_effort
                or cost_rows[0].get('protocol_version')
                != audit_turn.protocol_version
                or cost_rows[0].get('cli_version') != audit_turn.cli_version
            ):
                raise reviewer_tasks.ReviewerTaskError(
                    'Stage5 durable disposition 成本账未证明同 attempt/response exit 0')

            if session.end is None and not same_parent:
                if (
                    audit_turn.turn_index is None
                    or audit_turn.provider_session_id is None
                    or session.start.subject_sha256 is None
                    or session.start.profile is None
                    or audit_turn.actual_model is None
                    or audit_turn.prompt_sha256 is None
                    or audit_turn.schema_sha256 is None
                    or session.start.start_code_version is None
                ):
                    raise reviewer_tasks.ReviewerTaskError(
                        'Stage5 durable disposition open session 缺补尾所需真实 turn 身份')
                agent_session_audit.finish_restored_session(
                    base,
                    {
                        'session_run_id': record.reviewer_session_run_id,
                        'attempt_id': last_attempt.attempt_id,
                        'provider_session_id': audit_turn.provider_session_id,
                        'accepted_turn_index': audit_turn.turn_index,
                        'subject_sha256': session.start.subject_sha256,
                        'profile': session.start.profile.model_dump(mode='json'),
                        'model': audit_turn.actual_model,
                        'accepted_prompt_sha256': audit_turn.prompt_sha256,
                        'accepted_schema_sha256': audit_turn.schema_sha256,
                    },
                    process_state='ok',
                    delivery_state='unknown',
                    end_code_version=session.start.start_code_version,
                    only_if_open=True,
                )
                sessions = [
                    row for row in agent_session_audit.fold(
                        agent_session_audit.read_events(base))
                    if row.session_run_id == record.reviewer_session_run_id
                ]
                if len(sessions) != 1:
                    raise reviewer_tasks.ReviewerTaskError(
                        'Stage5 durable disposition session 补尾后不唯一')
                session = sessions[0]
            if session.end is not None and (
                session.process_state != 'ok'
                or session.error_kind is not None
                or session.delivery_state not in {'unknown', 'blocked'}
            ):
                raise reviewer_tasks.ReviewerTaskError(
                    'Stage5 durable disposition logical session 未证明 process ok')
            if session.end is None and not same_parent:
                raise reviewer_tasks.ReviewerTaskError(
                    'Stage5 durable disposition 跨 parent 恢复后 session 仍未闭合')

            recoverable_failed = (
                turn.outcome == 'failed' and turn.exit_code == CRASH
            )
            if turn.outcome == 'started' and same_parent:
                raise reviewer_tasks.ReviewerTaskError(
                    'Stage5 durable disposition 同 parent 仍有 started turn，不得从旁补席')
            if turn.outcome == 'started':
                turn = reviewer_seat_ledger.finish(
                    base, sid,
                    waiting_on=record.waiting_on,
                    business_sha256=record.business_sha256,
                    turn_id=record.reviewer_seat_turn_id,
                    exit_code=0,
                )
            elif not (
                turn.outcome == 'ok' and turn.exit_code == 0
                or recoverable_failed
            ):
                raise reviewer_tasks.ReviewerTaskError(
                    'Stage5 durable disposition 原 seat turn 既非成功闭账，也非'
                    '有独立 wire 成功证据的 durable/promote 故障')

            after = reviewer_seat_ledger.read(
                base, sid,
                waiting_on=record.waiting_on,
                business_sha256=record.business_sha256,
            )
            if after is None or any(
                row.outcome == 'started' for row in after.turns
            ):
                raise reviewer_tasks.ReviewerTaskError(
                    'Stage5 durable disposition 恢复后仍有 started turn')
            final_rows = [
                row for row in after.turns
                if row.turn_id == record.reviewer_seat_turn_id
            ]
            if (
                len(final_rows) != 1
                or final_rows[0].provider_response_sha256
                != record.provider_response_sha256
                or not (
                    final_rows[0].outcome == 'ok'
                    and final_rows[0].exit_code == 0
                    or final_rows[0].outcome == 'failed'
                    and final_rows[0].exit_code == CRASH
                )
            ):
                raise reviewer_tasks.ReviewerTaskError(
                    'Stage5 durable disposition 恢复后原 seat 终态不闭合')
    except reviewer_tasks.ReviewerTaskError:
        raise
    except (
        NovelError,
        OSError,
        ValueError,
        reviewer_seat_ledger.ReviewerSeatRefused,
    ) as exc:
        raise reviewer_tasks.ReviewerTaskError(
            'Stage5 durable disposition 原席位账无法确定性补完：'
            f'{type(exc).__name__}: {exc}') from exc


def _public_request(trusted: ReviewerRewriteBatchTrustedInput) -> Stage5ReviewerRewriteRequest:
    return Stage5ReviewerRewriteRequest.model_validate(trusted.model_dump(
        mode='json', include={'actor', 'reason', 'review_sha256',
                             'unresolved_trim_items', 'targets'}))


_AUTHORITY_DIRNAME = 'reviewer_action_authorities'


def _authority_root(
    base: str | os.PathLike[str], sid: str, *, create: bool = False,
) -> Path:
    """返回 authority 目录，并拒绝 `_service` 下任一级链接/非目录。"""
    checked_sid = paths.check_sid(sid)
    service_root = paths.service_dir(base).absolute()
    runs_root = paths.service_runs_dir(base).absolute()
    sid_root = runs_root / checked_sid
    authority_root = sid_root / _AUTHORITY_DIRNAME
    chain = (service_root, runs_root, sid_root, authority_root)

    def validate_chain() -> None:
        for directory in chain:
            if directory.is_symlink():
                raise reviewer_tasks.ReviewerTaskError(
                    f'reviewer rewrite authority 路径目录不得是符号链接：{directory}')
            if directory.exists() and not directory.is_dir():
                raise reviewer_tasks.ReviewerTaskError(
                    f'reviewer rewrite authority 路径祖先不是普通目录：{directory}')
        try:
            authority_root.resolve().relative_to(service_root.resolve())
        except (OSError, ValueError) as exc:
            raise reviewer_tasks.ReviewerTaskError(
                'reviewer rewrite authority 路径越出 _service') from exc

    validate_chain()
    if create:
        try:
            authority_root.mkdir(parents=True, exist_ok=True)
        except OSError as exc:
            raise ArtifactUnreadable(
                f'创建 reviewer rewrite authority 目录失败：{exc!r}') from exc
        # mkdir 的父链也可能由并发方预先创建；实际写前必须重新 lstat/resolve。
        validate_chain()
    return authority_root


def _authority_path(
    base: str | os.PathLike[str], sid: str, record_sha256: str,
) -> Path:
    if (
        len(record_sha256) != 64
        or any(char not in '0123456789abcdef' for char in record_sha256)
    ):
        raise reviewer_tasks.ReviewerTaskError(
            'reviewer rewrite authority record SHA 形状非法')
    return _authority_root(base, sid) / f'reviewer-action-{record_sha256}.json'


def _authority_receipt_path(
    base: str | os.PathLike[str], sid: str, record_sha256: str,
) -> Path:
    authority = _authority_path(base, sid, record_sha256)
    return authority.with_name(
        f'reviewer-action-{record_sha256}.receipt.json')


class ReviewerRewriteAuthorityPending(reviewer_tasks.ReviewerTaskError):
    """canonical request 已存在，但来源 action 尚未完成 immutable seal。"""


def _read_archived_authority(path: Path, record_sha256: str) -> ReviewerActionRecord:
    try:
        if path.is_symlink() or not path.is_file():
            raise reviewer_tasks.ReviewerTaskError(
                'reviewer rewrite archived authority 不是普通文件')
        raw = path.read_bytes()
        if hashlib.sha256(raw).hexdigest() != record_sha256:
            raise reviewer_tasks.ReviewerTaskError(
                'reviewer rewrite archived authority 内容地址不闭合')
        return ReviewerActionRecord.model_validate_json(raw)
    except reviewer_tasks.ReviewerTaskError:
        raise
    except Exception as exc:  # noqa: BLE001 —— 坏永久凭据必须关闭式报告
        raise reviewer_tasks.ReviewerTaskError(
            'reviewer rewrite archived authority 无法 typed 读取') from exc


def _read_authority_receipt(path: Path) -> ReviewerRewriteAuthorityReceipt:
    try:
        if path.is_symlink() or not path.is_file():
            raise reviewer_tasks.ReviewerTaskError(
                'reviewer rewrite authority receipt 不是普通文件')
        return ReviewerRewriteAuthorityReceipt.model_validate_json(
            path.read_bytes())
    except reviewer_tasks.ReviewerTaskError:
        raise
    except Exception as exc:  # noqa: BLE001 —— 坏永久 seal 不能降级为 active
        raise reviewer_tasks.ReviewerTaskError(
            'reviewer rewrite authority receipt 无法 typed 读取') from exc


def _active_authority(
    base: str | os.PathLike[str], sid: str, record_sha256: str,
) -> tuple[Path, ReviewerActionRecord] | None:
    expected_name = f'reviewer-action-{record_sha256}.json'
    matches = [
        (path, record)
        for path, record in uploads.load_reviewer_actions(base, sid)
        if path.name == expected_name
    ]
    if len(matches) > 1:  # pragma: no cover —— 同目录同名结构上不可能
        raise reviewer_tasks.ReviewerTaskError(
            'reviewer rewrite active authority 不唯一')
    return matches[0] if matches else None


def load_reviewer_rewrite_authority(
    base: str | os.PathLike[str], sid: str, record_sha256: str,
) -> ReviewerActionRecord:
    """读取 active/archive 原记录；消费方仍须显式选择 active 或 sealed。"""
    active_entry = _active_authority(base, sid, record_sha256)
    archive_path = _authority_path(base, sid, record_sha256)
    archived = _read_archived_authority(
        archive_path, record_sha256) if archive_path.is_file() else None
    records = [
        record for record in (
            None if active_entry is None else active_entry[1], archived)
        if record is not None
    ]
    if not records or any(record != records[0] for record in records[1:]):
        raise reviewer_tasks.ReviewerTaskError(
            'reviewer rewrite 找不到唯一一致的 content-addressed authority')
    return records[0]


def _atomic_write_once(
    base: str | os.PathLike[str], sid: str, path: Path, raw: bytes, *,
    label: str,
) -> None:
    authority_root = _authority_root(base, sid, create=True)
    if path.absolute().parent != authority_root:
        raise reviewer_tasks.ReviewerTaskError(
            f'{label} 目标不在 reviewer rewrite authority 目录')
    if path.is_symlink():
        raise reviewer_tasks.ReviewerTaskError(f'{label} 不是普通文件')
    if path.is_file():
        try:
            existing = path.read_bytes()
        except OSError as exc:
            raise ArtifactUnreadable(f'读不了已有 {label}：{exc!r}') from exc
        if existing != raw:
            raise reviewer_tasks.ReviewerTaskError(
                f'immutable {label} 已存在不同字节')
        return
    if path.exists():
        raise reviewer_tasks.ReviewerTaskError(f'{label} 不是普通文件')
    tmp = path.with_name(f'.{path.name}.{uuid.uuid4().hex}.tmp')
    try:
        tmp.write_bytes(raw)
        os.replace(tmp, path)
    except OSError as exc:
        raise ArtifactUnreadable(f'保存 {label} 失败：{exc!r}') from exc
    finally:
        try:
            tmp.unlink(missing_ok=True)
        except OSError:
            pass


def _trusted_from_request(
    request: ReviewerRewriteBatchRequest,
) -> ReviewerRewriteBatchTrustedInput:
    return request.trusted()


def _require_record_binding(
    record: ReviewerActionRecord,
    trusted: ReviewerRewriteBatchTrustedInput,
    *,
    require_current_contract: bool,
) -> None:
    public = _public_request(trusted)
    if (
        record.node != 'stage5.review'
        or record.flags != (
            '--request-reviewer-rewrite', '@reviewer-input')
        or record.input_payload != public.model_dump(mode='json')
        or record.business_sha256 != trusted.business_sha256
        or record.stage5_review_sha256 != trusted.review_sha256
        or record.stage5_reviewer_contract_sha256
        != trusted.reviewer_contract_sha256
        or record.reviewer_session_run_id
        != trusted.reviewer_session_run_id
        or record.reviewer_seat_turn_id != trusted.reviewer_seat_turn_id
        or record.provider_response_sha256
        != trusted.provider_response_sha256
    ):
        raise reviewer_tasks.ReviewerTaskError(
            'reviewer rewrite trusted input 与 durable action 身份不闭合')
    if require_current_contract:
        current_contract_sha256 = hashlib.sha256(
            reviewer_contract.for_blocker(
                queue.WAITING_ON_STAGE5_CLOSURE).encode('utf-8')
        ).hexdigest()
        if trusted.reviewer_contract_sha256 != current_contract_sha256:
            raise reviewer_tasks.ReviewerTaskError(
                'reviewer rewrite authority 所绑 Reviewer contract 已非 current')
    expected_business = canonical_sha256({
        'kind': 'stage5-top-review-business-v3',
        'waiting_on': queue.WAITING_ON_STAGE5_CLOSURE,
        'action_mode': 'trim_disposition',
        'review_sha256': trusted.review_sha256,
        'unresolved_trim_items': [
            row.model_dump(mode='json') for row in trusted.unresolved_trim_items
        ],
        'reviewer_contract_sha256': trusted.reviewer_contract_sha256,
    })
    if trusted.business_sha256 != expected_business:
        raise reviewer_tasks.ReviewerTaskError(
            'reviewer rewrite trusted input 的稳定 business identity 不闭合')


def _closed_evidence_hashes(
    base: str | os.PathLike[str], sid: str, record: ReviewerActionRecord,
) -> tuple[str, str, str]:
    """只读复核 sealed 闭集并返回 seat/session/cost 三份稳定摘要。"""
    validate_stage5_action_record_ledger(
        base, sid, record=record, read_only=True)
    if record.work_unit_id is not None:
        return _work_unit_evidence_hashes(base, sid, record)
    ledger = reviewer_seat_ledger.read(
        base, sid, waiting_on=record.waiting_on,
        business_sha256=record.business_sha256)
    if ledger is None:
        raise reviewer_tasks.ReviewerTaskError(
            'reviewer rewrite sealed authority 缺 seat ledger')
    turns = [
        row for row in ledger.turns
        if row.turn_id == record.reviewer_seat_turn_id]
    if (
        len(turns) != 1
        or ledger.turns[-1].turn_id != turns[0].turn_id
        or turns[0].outcome != 'ok'
        or turns[0].exit_code != 0
        or ledger.pending_correction is not None
        or any(row.outcome == 'started' for row in ledger.turns)
    ):
        raise reviewer_tasks.ReviewerTaskError(
            'reviewer rewrite authority 只能封存最后一轮 ok/0 且无 pending 的席位')
    sessions = [
        session for session in agent_session_audit.fold(
            agent_session_audit.read_events(base))
        if session.session_run_id == record.reviewer_session_run_id
    ]
    if len(sessions) != 1:
        raise reviewer_tasks.ReviewerTaskError(
            'reviewer rewrite sealed authority 找不到唯一 logical session')
    session = sessions[0]
    if (
        session.end is None
        or session.process_state != 'ok'
        or session.error_kind is not None
        or session.delivery_state not in {'unknown', 'blocked'}
    ):
        raise reviewer_tasks.ReviewerTaskError(
            'reviewer rewrite authority seal 前 logical session 未成功闭合')
    last_attempt = turns[0].attempts[-1]
    cost_rows = [
        row for row in read_jsonl_rows(cost_ledger_path(base))
        if row.get('run_id') == record.run_id
        and row.get('session_run_id') == record.reviewer_session_run_id
        and row.get('sid') == sid
        and row.get('role') == 'commander'
        and row.get('attempt_id') == last_attempt.attempt_id
        and row.get('activity')
        == f'reviewer:{queue.WAITING_ON_STAGE5_CLOSURE}'
    ]
    if len(cost_rows) != 1:
        raise reviewer_tasks.ReviewerTaskError(
            'reviewer rewrite sealed authority 成本证据不唯一')
    return (
        canonical_sha256(ledger.model_dump(mode='json')),
        # folded record 是 dataclass；事件的唯一 JSON 形状由 to_dict 给出，
        # 不能把派生会话当 Pydantic model，否则 request 落盘后的 seal 必崩。
        canonical_sha256({
            'session_run_id': session.session_run_id,
            'start': session.start.to_dict(),
            'turns': [event.to_dict() for event in session.turns],
            'end': session.end.to_dict(),
            'provider_session_ids': list(session.provider_session_ids),
            'attempts': [event.to_dict() for event in session.attempts],
        }),
        canonical_sha256(cost_rows[0]),
    )


def _require_request_file(
    base: str | os.PathLike[str], sid: str,
    request: ReviewerRewriteBatchRequest, request_file_sha256: str,
) -> None:
    from novel.orchestrator.state import readonly_ctx
    from novel.store import ArtifactStore

    novel_dir = paths.resolve_novel_dir(base, sid)
    path = paths.artifact_path(novel_dir, STAGE5_REVIEWER_REWRITE_REQUEST)
    try:
        if path.is_symlink() or not path.is_file():
            raise reviewer_tasks.ReviewerTaskError(
                'canonical reviewer rewrite request 不是普通文件')
        store = ArtifactStore(readonly_ctx(novel_dir, 'stage5.review', prov.code_version()))
        current, digest = store.read_own_with_sha256(
            ReviewerRewriteBatchRequest, STAGE5_REVIEWER_REWRITE_REQUEST)
        if digest != request_file_sha256:
            raise reviewer_tasks.ReviewerTaskError(
                'canonical reviewer rewrite request raw SHA 漂移')
        if current != request:
            raise reviewer_tasks.ReviewerTaskError(
                'canonical reviewer rewrite request typed bytes 漂移')
    except reviewer_tasks.ReviewerTaskError:
        raise
    except Exception as exc:  # noqa: BLE001 —— request 是后续付费 authority
        raise reviewer_tasks.ReviewerTaskError(
            'canonical reviewer rewrite request 无法完整复读') from exc


def _require_receipt_matches(
    receipt: ReviewerRewriteAuthorityReceipt,
    record: ReviewerActionRecord,
    *, record_sha256: str,
    seat_sha256: str, session_sha256: str, cost_sha256: str,
    request: ReviewerRewriteBatchRequest | None = None,
    request_file_sha256: str | None = None,
) -> None:
    # record_sha256 已由 _read_archived_authority 对原文件字节复验。typed
    # decode 会补新增可选字段，JSON 格式也不必等于今天的 serializer；重序列化
    # 摘要不能冒充历史文件摘要，否则合法 sealed authority 在升级后会永久误拒。
    if (
        receipt.sid != record.sid
        or receipt.reviewer_action_record_sha256 != record_sha256
        or receipt.business_sha256 != record.business_sha256
        or receipt.reviewer_contract_sha256
        != record.stage5_reviewer_contract_sha256
        or receipt.reviewer_session_run_id
        != record.reviewer_session_run_id
        or receipt.reviewer_seat_turn_id != record.reviewer_seat_turn_id
        or receipt.provider_response_sha256
        != record.provider_response_sha256
        or receipt.seat_evidence_sha256 != seat_sha256
        or receipt.session_evidence_sha256 != session_sha256
        or receipt.cost_evidence_sha256 != cost_sha256
        or receipt.request_rel != STAGE5_REVIEWER_REWRITE_REQUEST
    ):
        raise reviewer_tasks.ReviewerTaskError(
            'reviewer rewrite authority receipt 与永久来源证据不闭合')
    if request is not None and (
        request_file_sha256 is None
        or receipt.request_file_sha256 != request_file_sha256
        or receipt.request_sha256 != request.request_sha256
        or receipt.reviewer_action_record_sha256
        != request.requests[0].reviewer_action_record_sha256
        or receipt.business_sha256 != request.requests[0].business_sha256
        or receipt.reviewer_contract_sha256
        != request.requests[0].reviewer_contract_sha256
        or receipt.reviewer_session_run_id
        != request.requests[0].reviewer_session_run_id
        or receipt.reviewer_seat_turn_id
        != request.requests[0].reviewer_seat_turn_id
        or receipt.provider_response_sha256
        != request.requests[0].provider_response_sha256
    ):
        raise reviewer_tasks.ReviewerTaskError(
            'reviewer rewrite authority receipt 与 canonical request 不闭合')


def reviewer_rewrite_authority_sealed(
    base: str | os.PathLike[str], sid: str, record_sha256: str,
) -> bool:
    """只读判断 authority 是否已有完整 seal；active-only 恒为 ``False``。"""
    archive_path = _authority_path(base, sid, record_sha256)
    receipt_path = _authority_receipt_path(base, sid, record_sha256)
    active = _active_authority(base, sid, record_sha256)
    archive_exists = archive_path.is_file()
    receipt_exists = receipt_path.is_file()
    if not archive_exists and not receipt_exists:
        return False
    if archive_exists != receipt_exists:
        # copy archive → receipt 的可恢复硬杀窗只有 active 仍在时才可信；
        # readonly planner 不补写，只投影 pending/unknown。
        if archive_exists and active is not None:
            return False
        raise reviewer_tasks.ReviewerTaskError(
            'reviewer rewrite authority archive/receipt 半组且无 active 恢复凭据')
    record = _read_archived_authority(archive_path, record_sha256)
    if active is not None and active[1] != record:
        raise reviewer_tasks.ReviewerTaskError(
            'reviewer rewrite active/archive authority 字节冲突')
    receipt = _read_authority_receipt(receipt_path)
    seat_path = _record_evidence_path(base, sid, record)
    with path_transaction_lock(seat_path):
        evidence = _closed_evidence_hashes(base, sid, record)
        # seal 写完后只准出现 exact active 尾巴；同锁再读一次 seat 与永久字节，
        # 防止把一次跨 CAS 的拼接快照当作 authority。
        record_after = _read_archived_authority(archive_path, record_sha256)
        receipt_after = _read_authority_receipt(receipt_path)
        if record_after != record or receipt_after != receipt:
            raise reviewer_tasks.ReviewerTaskError(
                'reviewer rewrite authority 只读双采之间漂移')
        evidence_after = _closed_evidence_hashes(base, sid, record)
        if evidence_after != evidence:
            raise reviewer_tasks.ReviewerTaskError(
                'reviewer rewrite authority 三账双采之间漂移')
    _require_receipt_matches(
        receipt, record, record_sha256=record_sha256,
        seat_sha256=evidence[0], session_sha256=evidence[1],
        cost_sha256=evidence[2])
    return True


def require_sealed_reviewer_rewrite_authority(
    base: str | os.PathLike[str], sid: str,
    request: ReviewerRewriteBatchRequest, request_file_sha256: str,
) -> ReviewerActionRecord:
    """付费、恢复、terminal probe 共用的 immutable authority 读闸。"""
    _require_request_file(base, sid, request, request_file_sha256)
    digest = request.requests[0].reviewer_action_record_sha256
    if not reviewer_rewrite_authority_sealed(base, sid, digest):
        raise ReviewerRewriteAuthorityPending(
            'canonical reviewer rewrite request 尚未完成来源 authority seal')
    record = _read_archived_authority(
        _authority_path(base, sid, digest), digest)
    _require_record_binding(
        record, _trusted_from_request(request),
        require_current_contract=False)
    receipt = _read_authority_receipt(
        _authority_receipt_path(base, sid, digest))
    evidence = _closed_evidence_hashes(base, sid, record)
    _require_receipt_matches(
        receipt, record, record_sha256=digest,
        seat_sha256=evidence[0], session_sha256=evidence[1],
        cost_sha256=evidence[2], request=request,
        request_file_sha256=request_file_sha256)
    return record


def require_current_sealed_reviewer_rewrite_authority(
    base: str | os.PathLike[str], sid: str,
    request: ReviewerRewriteBatchRequest, request_file_sha256: str,
) -> ReviewerActionRecord:
    """执行层 authority：历史 seal 完整，且 Reviewer contract 仍 current。"""
    record = require_sealed_reviewer_rewrite_authority(
        base, sid, request, request_file_sha256)
    _require_record_binding(
        record, _trusted_from_request(request),
        require_current_contract=True)
    return record


def seal_reviewer_rewrite_authority(
    base: str | os.PathLike[str], sid: str,
    request: ReviewerRewriteBatchRequest, request_file_sha256: str, *,
    current_parent_run_id: str,
) -> ReviewerRewriteAuthorityReceipt:
    """在真实 Agent parent 内把 active action 两相封成永久 authority。"""
    if not current_parent_run_id:
        raise reviewer_tasks.ReviewerTaskError(
            'reviewer rewrite authority seal 缺真实 current parent')
    _require_request_file(base, sid, request, request_file_sha256)
    digest = request.requests[0].reviewer_action_record_sha256
    active = _active_authority(base, sid, digest)
    archive_path = _authority_path(base, sid, digest)
    receipt_path = _authority_receipt_path(base, sid, digest)
    archive_exists = archive_path.exists() or archive_path.is_symlink()
    receipt_exists = receipt_path.exists() or receipt_path.is_symlink()
    if archive_exists and receipt_exists:
        # receipt 已是 authority 的 commit point。此后的幂等 reconcile 只复验
        # 当时封存的历史闭集；否则 Reviewer contract 换代会在 terminal/expiry
        # probe 之前把一份已经合法 sealed 的 request 永久卡住。这里绝不重写
        # receipt；若还有 exact active 尾，只在同 parent 或 old-owner-stopped 下
        # 免费退休它。
        record = require_sealed_reviewer_rewrite_authority(
            base, sid, request, request_file_sha256)
        receipt = _read_authority_receipt(receipt_path)
        if active is not None:
            with record_owner_guard(
                    base, record_parent_run_id=_record_owner_run_id(base, sid, record),
                    current_parent_run_id=current_parent_run_id):
                current_active = _active_authority(base, sid, digest)
                if current_active is not None:
                    if current_active[1] != record:
                        raise reviewer_tasks.ReviewerTaskError(
                            'reviewer rewrite sealed authority 的 active 尾漂移')
                    uploads.discard_edit_plan(base, current_active[0])
        require_sealed_reviewer_rewrite_authority(
            base, sid, request, request_file_sha256)
        return receipt
    if receipt_exists and not archive_exists:
        # producer 固定先 archive 后 receipt；反向半组不是任何合法硬杀窗。
        raise reviewer_tasks.ReviewerTaskError(
            'reviewer rewrite authority receipt 缺 archive，拒绝反向补铸')
    if active is None:
        raise reviewer_tasks.ReviewerTaskError(
            'reviewer rewrite authority seal 找不到 active action record')
    initial_record = active[1]
    # 跨 parent 的 owner-stopped lease 必须包住整个 seal，而不只是账本补尾：
    # 否则旧 parent 可在 validate 返回后恢复并更新 active，恢复者仍会拿旧快照
    # 封存并删除。完整 seal 的历史幂等分支在上方单独处理；这里首次/半组 seal
    # 都必须用 current contract，且直到永久 commit、active exact 清理及历史复验
    # 全部结束才释放 lease。
    with record_owner_guard(
            base, record_parent_run_id=_record_owner_run_id(base, sid, initial_record),
            current_parent_run_id=current_parent_run_id) as same_parent:
        archive_exists = archive_path.exists() or archive_path.is_symlink()
        receipt_exists = receipt_path.exists() or receipt_path.is_symlink()
        current_active = _active_authority(base, sid, digest)
        if archive_exists and receipt_exists:
            # 另一恢复者可能在本调用等 lease 期间完成了 seal。此时只按历史
            # authority 幂等收尾，不能拿新 contract 否定已经提交的旧 receipt。
            record = require_sealed_reviewer_rewrite_authority(
                base, sid, request, request_file_sha256)
            receipt = _read_authority_receipt(receipt_path)
            if current_active is not None:
                if current_active[1] != record:
                    raise reviewer_tasks.ReviewerTaskError(
                        'reviewer rewrite sealed authority 的 active 尾漂移')
                uploads.discard_edit_plan(base, current_active[0])
            require_sealed_reviewer_rewrite_authority(
                base, sid, request, request_file_sha256)
            return receipt
        if receipt_exists and not archive_exists:
            raise reviewer_tasks.ReviewerTaskError(
                'reviewer rewrite authority receipt 缺 archive，拒绝反向补铸')
        if current_active is None:
            raise reviewer_tasks.ReviewerTaskError(
                'reviewer rewrite authority seal 入锁后 active action record 已漂移')
        record = current_active[1]
        if record != initial_record:
            raise reviewer_tasks.ReviewerTaskError(
                'reviewer rewrite authority seal 入锁后 active action record 已漂移')
        _require_record_binding(
            record, _trusted_from_request(request),
            require_current_contract=True)
        # 跨 parent 的 request-written/seat-finish 硬杀窗在同一个外层证明内
        # 幂等补尾；同 parent 必须已由 Agent 正常 close seat/session。
        validate_stage5_action_record_ledger(
            base, sid, record=record,
            current_parent_run_id=current_parent_run_id,
            _owner_guard_same_parent=same_parent)
        seat_path = _record_evidence_path(base, sid, record)
        with path_transaction_lock(receipt_path):
            with path_transaction_lock(seat_path):
                # 永久字节只能来自锁内重新读取的 exact active；不能沿用取得
                # old-owner-stopped 证明前的 Path/record 快照。
                current_active = _active_authority(base, sid, digest)
                if current_active is None or current_active[1] != record:
                    raise reviewer_tasks.ReviewerTaskError(
                        '待 seal reviewer rewrite active authority 已漂移')
                try:
                    active_raw = current_active[0].read_bytes()
                except OSError as exc:
                    raise ArtifactUnreadable(
                        f'读不了 reviewer rewrite active authority：{exc!r}') from exc
                if hashlib.sha256(active_raw).hexdigest() != digest:
                    raise reviewer_tasks.ReviewerTaskError(
                        '待 seal reviewer rewrite active authority 内容地址不闭合')
                _atomic_write_once(
                    base, sid, archive_path, active_raw,
                    label='reviewer rewrite authority archive')
                evidence = _closed_evidence_hashes(base, sid, record)
                archived = _read_archived_authority(archive_path, digest)
                if archived != record:
                    raise reviewer_tasks.ReviewerTaskError(
                        'reviewer rewrite authority archive 与 active record 不一致')
                if receipt_path.is_file():
                    receipt = _read_authority_receipt(receipt_path)
                else:
                    payload: dict[str, object] = {
                        'kind': 'novel-stage5-reviewer-rewrite-authority-v1',
                        'sid': sid,
                        'reviewer_action_record_sha256': digest,
                        'request_rel': STAGE5_REVIEWER_REWRITE_REQUEST,
                        'request_file_sha256': request_file_sha256,
                        'request_sha256': request.request_sha256,
                        'business_sha256': request.requests[0].business_sha256,
                        'reviewer_contract_sha256': request.requests[0].reviewer_contract_sha256,
                        'reviewer_session_run_id': request.requests[0].reviewer_session_run_id,
                        'reviewer_seat_turn_id': request.requests[0].reviewer_seat_turn_id,
                        'provider_response_sha256': request.requests[0].provider_response_sha256,
                        'seat_evidence_sha256': evidence[0],
                        'session_evidence_sha256': evidence[1],
                        'cost_evidence_sha256': evidence[2],
                        'sealed_at': int(time.time()),
                        'receipt_sha256': '0' * 64,
                    }
                    payload['receipt_sha256'] = (
                        reviewer_rewrite_authority_receipt_sha256(payload))
                    receipt = ReviewerRewriteAuthorityReceipt.model_validate(payload)
                    _atomic_write_once(
                        base, sid, receipt_path,
                        (receipt.model_dump_json() + '\n').encode('utf-8'),
                        label='reviewer rewrite authority receipt')
                _require_receipt_matches(
                    receipt, record, record_sha256=digest,
                    seat_sha256=evidence[0], session_sha256=evidence[1],
                    cost_sha256=evidence[2], request=request,
                    request_file_sha256=request_file_sha256)
        # receipt 是 seal 的 commit point；active 删除只负责离开 replay inbox。
        # 删除前再次 exact readback，防止沿用 archive 前的陈旧路径误删；删除失败
        # 会保留两份 exact 字节，下一次 reconcile 可幂等完成。
        current_active = _active_authority(base, sid, digest)
        if current_active is not None:
            if current_active[1] != record:
                raise reviewer_tasks.ReviewerTaskError(
                    'reviewer rewrite authority commit 后 active 尾漂移')
            uploads.discard_edit_plan(base, current_active[0])
        require_sealed_reviewer_rewrite_authority(
            base, sid, request, request_file_sha256)
        return receipt


def require_reviewer_rewrite_authority(
    base: str | os.PathLike[str], sid: str,
    trusted: ReviewerRewriteBatchTrustedInput, *,
    current_parent_run_id: str | None = None,
) -> ReviewerActionRecord:
    """phase A：证明 trusted envelope 来自 active typed Commander action。

    这里必须收到调用方真实 parent；跨 parent 时按该身份取得 old-owner-stopped
    并补尾，绝不把 ``record.run_id`` 伪装成当前 parent。readonly consumer 只能
    走 ``require_sealed_reviewer_rewrite_authority``。
    """
    if current_parent_run_id is None:
        raise reviewer_tasks.ReviewerTaskError(
            'active reviewer rewrite authority 必须由真实 Agent parent 消费')
    active = _active_authority(
        base, sid, trusted.reviewer_action_record_sha256)
    if active is None:
        raise reviewer_tasks.ReviewerTaskError(
            'active reviewer rewrite authority 已缺失或已被 seal 消费')
    record = active[1]
    _require_record_binding(
        record, trusted, require_current_contract=True)
    validate_stage5_action_record_ledger(
        base, sid, record=record,
        current_parent_run_id=current_parent_run_id)
    return record


__all__ = [
    'ReviewerRewriteAuthorityPending',
    'load_reviewer_rewrite_authority',
    'record_owner_guard',
    'recorded_response_sha256',
    'require_sealed_reviewer_rewrite_authority',
    'require_current_sealed_reviewer_rewrite_authority',
    'require_reviewer_rewrite_authority',
    'reviewer_rewrite_authority_sealed',
    'seal_reviewer_rewrite_authority',
    'validate_stage5_action_record_ledger',
]
