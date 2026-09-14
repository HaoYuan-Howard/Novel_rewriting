#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""已拒绝的 Codex 输出配置，以剩余 reviewer 轮次开启新会话。

这里只证明服务保留的原请求在生成回答前被供应商拒绝，不推断语义决定，也
不把有 wire、计量未知的失败改成零成本。原席位、session、cost、log 不改写；
调用方在 owner-stopped lease 内复验，再由 seat.reserve 原子追加耗额恢复轮。
"""
from __future__ import annotations

import hashlib
import copy
import json
import re
import time
from contextlib import nullcontext
from pathlib import Path
from collections.abc import Callable, Mapping
from typing import Any

from contracts.schema.orchestration import (
    AgentProfile, AgentRunRecord, ReviewerActionRecord, ReviewerSchemaRecoveryProof, ReviewerSeatLedger,
)
from novel import paths
from novel.agents import session_audit as agent_session_audit
from novel.agents.base import decode_wire_json
from novel.llm.ledger import ledger_path as cost_ledger_path, read_jsonl_rows
from novel.orchestrator import queue, reviewer_tasks
from novel.orchestrator.reviewer_protocol_recovery import parent_proof_projection_v1


WAITING_ON = frozenset({
    queue.WAITING_ON_STAGE4_DESIGN_REVIEW,
    queue.WAITING_ON_STAGE4_BINDING_RESOLUTION,
    queue.WAITING_ON_STAGE4_STRUCTURE_REPLAN,
    queue.WAITING_ON_STAGE4_STRUCTURE_REVIEW,
})
_USAGE_FIELDS = (
    'prompt_tokens', 'prompt_cache_hit_tokens', 'prompt_cache_miss_tokens',
    'completion_tokens', 'reasoning_tokens', 'total_tokens',
)


def canonical_sha256(value: Any) -> str:
    return hashlib.sha256(json.dumps(
        value, ensure_ascii=False, sort_keys=True, separators=(',', ':'),
        allow_nan=False).encode('utf-8')).hexdigest()


def _refuse(reason: str) -> None:
    raise reviewer_tasks.ReviewerTaskError(f'Reviewer schema 恢复证据不闭合：{reason}')


def _unique(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for key, value in pairs:
        if key in result:
            _refuse('来源 JSON 含重复键')
        result[key] = value
    return result


def _strict(text: str) -> Any:
    def invalid_constant(_value: str) -> None:
        _refuse('来源不是标准 JSON')
    try:
        return json.loads(text, object_pairs_hook=_unique, parse_constant=invalid_constant)
    except (ValueError, TypeError) as exc:
        _refuse(f'来源不是完整 JSON：{type(exc).__name__}')


def _log_bytes(base: str, path: Path) -> bytes:
    root = paths.service_dir(base).absolute()
    if root.is_symlink() or path.is_symlink() or not path.is_file():
        _refuse('原日志不是 service 内普通文件')
    try:
        path.resolve().relative_to(root.resolve())
    except ValueError:
        _refuse('原日志越出 service')
    return path.read_bytes()


def is_recovery_candidate(ledger: ReviewerSeatLedger) -> bool:
    if (ledger.waiting_on not in WAITING_ON or not ledger.turns
            or ledger.pending_correction is not None
            or len(ledger.turns) - 1 >= ledger.max_corrections):
        return False
    turn = ledger.turns[-1]
    return (
        turn.outcome == 'failed' and turn.exit_code == 1
        and turn.finished_at is not None and turn.provider_response_sha256 is None
        and len(turn.attempts) == 1
        and turn.attempts[0].exit_code == 1
        and turn.attempts[0].error_kind in {'agent_command', 'agent_schema'}
        and turn.attempts[0].wire_evidence
        and turn.attempts[0].provider_session_id is not None
        and sum(row.session_run_id == turn.session_run_id for row in ledger.turns) == 1)


def rejection_stream(
    raw: bytes, provider_id: str, *, schema_sha256: str | None = None,
) -> list[dict[str, Any]] | None:
    """严格限制为一次首发的四个事件，不从 stderr/小说字符串猜错误。"""
    blocks: list[list[str]] = [[]]
    for line in raw.decode('utf-8').splitlines():
        if not line.strip():
            continue
        if line.startswith('$ '):
            blocks.append([])
        blocks[-1].append(line)
    selected: list[tuple[int, list[str], bool]] = []
    for block_index, block in enumerate(blocks):
        lines: list[str] = []
        channel: str | None = None
        unexpected = False
        for line in block:
            if line.startswith('$ '):
                continue
            if line in {'[stdout protocol]', '[stderr]'}:
                channel = line
            elif channel == '[stdout protocol]':
                # 一个 read1 chunk 可以含多条完整 JSONL；每行都必须消费。
                # 旧日志若在 JSON 中间分 chunk，被补的换行无法可靠还原，关闭。
                lines.append(line)
            else:
                unexpected = True
        events = []
        for line in lines:
            try:
                events.append(decode_wire_json(line))
            except (ValueError, TypeError):
                events.append(None)
        if any(isinstance(event, dict) and event.get('type') == 'thread.started'
               and event.get('thread_id') == provider_id for event in events):
            selected.append((block_index, lines, unexpected))
    if len(selected) != 1:
        _refuse('原 provider 调用日志不唯一')
    block_index, lines, unexpected = selected[0]
    if not any('invalid_json_schema' in line for line in lines):
        return None
    if unexpected:
        _refuse('原调用含 stderr 或来源不明的日志行')
    if block_index != len(blocks) - 1:
        _refuse('原失败后还有调用块或未知命令行')
    if schema_sha256 is not None:
        command = blocks[block_index][0]
        if ('--output-schema' not in command
                or f'.commander-output-schema-{schema_sha256}.json' not in command):
            _refuse('原命令没有使用审计绑定的输出 schema')
    events = [_strict(line) for line in lines]
    if (len(events) != 4 or not all(isinstance(event, dict) for event in events)
            or events[0] != {'type': 'thread.started', 'thread_id': provider_id}
            or events[1] != {'type': 'turn.started'}
            or set(events[2]) != {'type', 'message'} or events[2]['type'] != 'error'
            or set(events[3]) != {'type', 'error'} or events[3]['type'] != 'turn.failed'
            or not isinstance(events[3]['error'], dict)
            or set(events[3]['error']) != {'message'}
            or not isinstance(events[2]['message'], str)
            or events[2]['message'] != events[3]['error']['message']):
        _refuse('原调用不是无回答、无工具、无计量的完整 schema 拒绝')
    rejected = _strict(events[2]['message'])
    if (not isinstance(rejected, dict) or set(rejected) != {'type', 'error', 'status'}
            or rejected['type'] != 'error' or type(rejected['status']) is not int
            or rejected['status'] != 400 or not isinstance(rejected['error'], dict)
            or set(rejected['error']) != {'type', 'code', 'message', 'param'}
            or rejected['error']['type'] != 'invalid_request_error'
            or rejected['error']['code'] != 'invalid_json_schema'
            or rejected['error']['param'] != 'text.format.schema'
            or not isinstance(rejected['error']['message'], str)
            or not rejected['error']['message'].startswith(
                "Invalid schema for response_format 'codex_output_schema':")):
        _refuse('原 provider 未明确拒绝 Codex 输出 schema')
    return events


def inspect_rejection(
    base: str, sid: str, *, ledger: ReviewerSeatLedger,
    parent: AgentRunRecord, log_path: Path, profile: AgentProfile,
    replacement_schema_sha256: str, validation_code_version: str,
    replacement_subject_sha256: str,
) -> ReviewerSchemaRecoveryProof | None:
    """只读形成可 CAS 的证明；同 schema 或未登记失败不恢复。"""
    if not is_recovery_candidate(ledger):
        return None
    if re.fullmatch(r'[0-9a-f]{40}', validation_code_version) is None:
        # 开新 wire 的配置修复必须已经冻结；unknown 或脏树不能冒充修复身份。
        return None
    turn = ledger.turns[-1]
    attempt = turn.attempts[0]
    if (ledger.sid != sid or parent.start.sid != sid
            or parent.run_id != turn.parent_run_id or parent.ended is None
            or parent.active_epoch_id is not None or parent.exit_code != 1
            or parent.contract is None or parent.contract.profiles.commander != profile
            or profile.driver != 'codex'
            or any(row.outcome == 'started' for row in ledger.turns)):
        _refuse('原 parent/配置/席位没有以失败终态闭合')
    epochs = [row for row in parent.epochs if row.epoch_id == turn.epoch_id]
    if len(epochs) != 1:
        _refuse('原 epoch 不唯一')
    epoch = epochs[0]
    all_events = agent_session_audit.read_events(base)
    all_sessions = agent_session_audit.fold(all_events)
    events = [event for event in all_events
              if event.session_run_id == turn.session_run_id]
    if [event.kind for event in events] != ['started', 'attempt', 'turn', 'ended']:
        _refuse('原 session 不是唯一失败首发的完整四事件审计')
    sessions = [row for row in all_sessions if row.session_run_id == turn.session_run_id]
    if len(sessions) != 1:
        _refuse('原 session/provider 全局归属不唯一')
    session = sessions[0]
    start, audit_attempt, audit_turn, end = events
    if (start.schema_sha256 is None or start.prompt_sha256 is None
            or end.process_state != 'failed' or end.delivery_state != 'unknown'
            or end.error_kind != attempt.error_kind
            or end.end_code_version != epoch.code_version
            or audit_attempt.attempt_id != attempt.attempt_id
            or audit_turn.attempt_id != attempt.attempt_id
            or audit_attempt.delivery_attempts != 1 or audit_turn.turn_index != 1
            or attempt.kind != 'launch' or attempt.finished_at is None
            or audit_attempt.ts != attempt.finished_at
            or turn.finished_at is None or audit_turn.ts > turn.finished_at
            or parent.ended < end.ts or end.ts < audit_turn.ts
            or session.provider_session_ids != (attempt.provider_session_id,)):
        _refuse('原 attempt/turn/provider/终态时序不闭合')
    for event in events:
        if (event.parent_run_id != parent.run_id or event.role != 'commander'
                or event.node is not None or event.profile != profile
                or event.requested_model != profile.model
                or event.requested_effort != profile.reasoning_effort
                or event.protocol_version != 'codex-jsonl-v1'
                or event.start_code_version != epoch.code_version
                or event.skill_sha256 != epoch.skill_sha256
                or event.subject_sha256 != start.prompt_sha256
                or event.prompt_sha256 != start.prompt_sha256
                or event.schema_sha256 != start.schema_sha256
                or event.usage is not None
                or event.actual_model is not None or event.actual_effort is not None
                or event.cli_version != start.cli_version
                or event.kind != 'started' and (
                    event.provider_session_id != attempt.provider_session_id
                    or event.error_kind != attempt.error_kind)):
            _refuse('原 session 请求、计量、schema、代码或 profile 身份漂移')
    costs = [row for row in read_jsonl_rows(cost_ledger_path(base))
             if row.get('session_run_id') == turn.session_run_id
             or row.get('attempt_id') == attempt.attempt_id]
    if len(costs) != 1:
        _refuse('原成本 attempt 不唯一')
    expected = {
        'run_id': parent.run_id, 'sid': sid, 'role': 'commander', 'node': None,
        'session_run_id': turn.session_run_id, 'attempt_id': attempt.attempt_id,
        'activity': f'reviewer:{ledger.waiting_on}',
        'provider_session_id': attempt.provider_session_id,
        'outcome': 'error', 'error_kind': attempt.error_kind, 'exit_code': 1,
        'response_sha256': None, 'code': epoch.code_version,
        'request_model': profile.model, 'reasoning_effort': profile.reasoning_effort,
        'model': None, 'actual_reasoning_effort': None,
        'protocol_version': 'codex-jsonl-v1', 'cli_version': start.cli_version,
        **{key: None for key in _USAGE_FIELDS},
    }
    if any(key not in costs[0] or costs[0][key] != value for key, value in expected.items()):
        _refuse('原成本与失败首发、空回答及未知计量不一致')
    raw = _log_bytes(base, log_path)
    if rejection_stream(raw, str(attempt.provider_session_id),
                        schema_sha256=start.schema_sha256) is None:
        return None
    if (replacement_schema_sha256 == start.schema_sha256
            or validation_code_version == epoch.code_version):
        return None
    return ReviewerSchemaRecoveryProof(
        source_turn_id=turn.turn_id, source_session_run_id=turn.session_run_id,
        source_attempt_id=attempt.attempt_id, source_parent_run_id=parent.run_id,
        source_epoch_id=epoch.epoch_id, provider_session_id=str(attempt.provider_session_id),
        source_schema_sha256=start.schema_sha256,
        replacement_schema_sha256=replacement_schema_sha256,
        replacement_subject_sha256=replacement_subject_sha256,
        source_code_version=epoch.code_version, validation_code_version=validation_code_version,
        source_ledger_sha256=canonical_sha256(ledger.model_dump(mode='json')),
        source_parent_sha256=canonical_sha256(parent_proof_projection_v1(parent)),
        source_session_sha256=canonical_sha256([event.to_dict() for event in events]),
        source_cost_sha256=canonical_sha256(costs),
        source_log_sha256=hashlib.sha256(raw).hexdigest())



def inspect_work_unit_rejection(
    base: str, sid: str, *, budget: Any, parent: AgentRunRecord,
    log_path: Path, profile: AgentProfile, replacement_schema_sha256: str,
    validation_code_version: str, replacement_subject_sha256: str,
    source_turn_id: str | None = None,
) -> ReviewerSchemaRecoveryProof | None:
    """新预算只借用已有严格拒绝证明器；下列临时投影不落盘、不分配额度。"""
    turn = budget.turn(source_turn_id) if source_turn_id is not None else budget.latest_turn()
    if (turn is None or turn.response_sha256 is not None
            or source_turn_id is None and budget.remaining == 0
            or turn.state not in ('unknown', 'failed') or len(turn.attempts) != 1):
        return None
    request = budget.request_payload(turn.turn_id)
    waiting_on = request.get('waiting_on')
    if waiting_on not in WAITING_ON:
        return None
    sessions = [item for item in agent_session_audit.fold(agent_session_audit.read_events(base))
                if item.session_run_id == turn.session_run_id]
    if len(sessions) != 1 or sessions[0].end is None:
        return None
    session = sessions[0]
    if len(session.attempts) != 1 or len(session.turns) != 1:
        return None
    observed = session.attempts[0]
    if (observed.attempt_id != turn.attempts[0].attempt_id
            or observed.provider_session_id != turn.attempts[0].provider_session_id
            or observed.error_kind not in {'agent_command', 'agent_schema'}):
        return None
    # 原证明器核查的 ordinal 是投影内序号。实际额度只认 budget.used，
    # 原全局 turn_id / parent / session / attempt 身份原样保留。
    source = ReviewerSeatLedger.model_validate(dict(
        sid=sid, waiting_on=waiting_on, business_sha256=request['business_sha256'],
        max_corrections=1, turns=[dict(
            turn_id=turn.turn_id, ordinal=1, purpose='review',
            session_run_id=turn.session_run_id, parent_run_id=turn.run_id,
            epoch_id=request['epoch_id'], started_at=turn.created_at,
            finished_at=session.end.ts, exit_code=1, outcome='failed',
            attempts=[dict(attempt_id=observed.attempt_id, kind='launch',
                           started_at=turn.created_at, finished_at=observed.ts,
                           exit_code=1, error_kind=observed.error_kind,
                           provider_session_id=observed.provider_session_id,
                           wire_evidence=observed.provider_session_id is not None)])]))
    return inspect_rejection(
        base, sid, ledger=source, parent=parent, log_path=log_path, profile=profile,
        replacement_schema_sha256=replacement_schema_sha256,
        validation_code_version=validation_code_version,
        replacement_subject_sha256=replacement_subject_sha256)


def validate_work_unit_recovery(base: str, sid: str, *, budget: Any, turn_id: str) -> None:
    """重放新工作单元决定前复验整个配置恢复链，不让保存的 proof 自证。"""
    from novel.orchestrator import admission, agent

    ledger = budget._read()
    target = budget.turn(turn_id)
    parents = {row.run_id: row for row in agent.records(base)}
    for index, turn in enumerate(ledger.turns[:target.ordinal]):
        request = budget.request_payload(turn.turn_id)
        raw_proof = request.get('schema_recovery')
        if raw_proof is None:
            continue
        proof = ReviewerSchemaRecoveryProof.model_validate(raw_proof)
        if index == 0 or proof.source_turn_id != ledger.turns[index - 1].turn_id:
            _refuse('配置恢复没有绑定同一工作单元紧邻的原失败轮')
        parent = parents.get(proof.source_parent_run_id)
        replacement = parents.get(turn.run_id)
        if parent is None or replacement is None or replacement.contract is None:
            _refuse('配置恢复缺原运行或恢复运行')
        epochs = [row for row in replacement.epochs if row.epoch_id == request.get('epoch_id')]
        if (len(epochs) != 1 or epochs[0].code_version != proof.validation_code_version
                or request.get('schema_sha256') != proof.replacement_schema_sha256
                or not isinstance(request.get('prompt'), str)
                or hashlib.sha256(request['prompt'].encode('utf-8')).hexdigest()
                != proof.replacement_subject_sha256):
            _refuse('配置恢复的代码、schema 或实际请求与证明漂移')
        with admission.require_owner_stopped(base, parent.run_id):
            observed = inspect_work_unit_rejection(
                base, sid, budget=budget, source_turn_id=proof.source_turn_id,
                parent=parent, log_path=agent.run_log_path(base, parent.run_id),
                profile=replacement.contract.profiles.commander,
                replacement_schema_sha256=proof.replacement_schema_sha256,
                validation_code_version=proof.validation_code_version,
                replacement_subject_sha256=proof.replacement_subject_sha256)
        if observed != proof:
            _refuse('配置恢复的原拒绝日志、会话、成本或父运行证据已漂移')

def source_prefix(ledger: ReviewerSeatLedger, recovery_ordinal: int) -> ReviewerSeatLedger:
    """证明只绑定追加前的原账，后续成功/纠错不改变这份来源闭集。"""
    return ReviewerSeatLedger.model_validate({
        **ledger.model_dump(mode='json'),
        'turns': [row.model_dump(mode='json') for row in ledger.turns[:recovery_ordinal - 1]],
        'pending_correction': None,
    })


def validate_recovery_chain(
    base: str, sid: str, *, ledger: ReviewerSeatLedger,
    parents: Mapping[str, AgentRunRecord], log_path_for: Callable[[str], Path],
) -> None:
    """顺序复验每条会话转换及其原账，不让 proof 自己证明自己。"""
    from novel.orchestrator import admission

    all_events = agent_session_audit.read_events(base)
    agent_session_audit.fold(all_events)  # 同时查跨 logical/provider 的占用冲突。
    for turn in ledger.turns:
        proof = turn.schema_recovery
        if proof is None:
            continue
        parent = parents.get(proof.source_parent_run_id)
        if parent is None or parent.contract is None:
            _refuse('历史 schema recovery 原 parent 契约缺失')
        with admission.require_owner_stopped(base, parent.run_id):
            checked = inspect_rejection(
                base, sid, ledger=source_prefix(ledger, turn.ordinal), parent=parent,
                log_path=log_path_for(parent.run_id), profile=parent.contract.profiles.commander,
                replacement_schema_sha256=proof.replacement_schema_sha256,
                validation_code_version=proof.validation_code_version,
                replacement_subject_sha256=proof.replacement_subject_sha256)
            if checked != proof:
                _refuse('历史 schema recovery 来源 proof 已漂移')
        starts = [event for event in all_events
                  if event.session_run_id == turn.session_run_id and event.kind == 'started']
        if (len(starts) != 1 or starts[0].parent_run_id != turn.parent_run_id
                or starts[0].profile != parent.contract.profiles.commander
                or starts[0].schema_sha256 != proof.replacement_schema_sha256
                or starts[0].subject_sha256 != proof.replacement_subject_sha256
                or starts[0].prompt_sha256 != proof.replacement_subject_sha256
                or starts[0].start_code_version != proof.validation_code_version):
            _refuse('schema recovery 新 session 未精确使用已授权的修正配置与完整工作包')


def validate_action_record(
    base: str, sid: str, *, record: ReviewerActionRecord,
    parents: Mapping[str, AgentRunRecord], log_path_for: Callable[[str], Path],
    current_parent_run_id: str | None,
) -> None:
    """新会话的正式决定必须与实际 turn/session/成本一致；独立证据可补成功尾。"""
    from novel.orchestrator import admission, reviewer_seat_ledger, stage4_reviewer_wire

    if record.work_unit_id is not None:
        # 新工作单元的原响应和 typed 决定已在统一耐久账绑定；不为配置恢复
        # 另建历史 seat ledger，也不要求业务连续会话先被标成 ended。
        from novel.work_units import validate_reviewer_record
        validate_reviewer_record(base, sid, record)
        return

    same_parent = record.run_id == current_parent_run_id
    guard = nullcontext() if same_parent else admission.require_owner_stopped(base, record.run_id)
    with guard:
        ledger = reviewer_seat_ledger.read(
            base, sid, waiting_on=record.waiting_on, business_sha256=record.business_sha256)
        if (ledger is None or not ledger.turns
                or not any(row.schema_recovery is not None for row in ledger.turns)
                or record.waiting_on not in WAITING_ON
                or record.schema_recovery_response is None or record.payload is None
                or record.surface not in {'design', 'structure'}):
            _refuse('配置恢复决定没有混合席位账与原 native 回答')
        validate_recovery_chain(base, sid, ledger=ledger, parents=parents, log_path_for=log_path_for)
        turn = ledger.turns[-1]
        parent = parents.get(record.run_id)
        if (parent is None or parent.contract is None or parent.start.sid != sid
                or not same_parent and parent.ended is None
                or record.sid != sid or record.reviewer_seat_turn_id != turn.turn_id
                or record.reviewer_session_run_id != turn.session_run_id
                or record.run_id != turn.parent_run_id or record.epoch_id != turn.epoch_id
                or record.provider_response_sha256 != turn.provider_response_sha256
                or not turn.attempts):
            _refuse('决定未绑定实际最新 reviewer turn/session/parent/response')
        pending = ledger.pending_correction
        if pending is not None and not (
                turn.purpose == 'correction' and len(ledger.turns) >= 2
                and pending.source_turn_id == ledger.turns[-2].turn_id
                and pending.session_run_id == turn.session_run_id):
            _refuse('决定已有同轮或更新的待纠错事实')
        bundle = record.schema_recovery_response
        if (set(bundle) != {'response', 'exact_request'}
                or not isinstance(bundle['response'], dict)
                or not isinstance(bundle['exact_request'], dict)):
            _refuse('原 native 回答与 immutable option 必须完整成组保存')
        document, exact_request = bundle['response'], bundle['exact_request']
        selection = document.get('selection')
        if (set(document) != {'schema', 'selection'}
                or document['schema'] != stage4_reviewer_wire.WIRE_SCHEMA
                or not isinstance(selection, dict)
                or set(selection) != {'option_id', 'expected_exact_request_sha256', 'payload'}
                or selection['option_id'] != f'approval:{record.surface}'
                or not isinstance(selection['expected_exact_request_sha256'], str)
                or selection['expected_exact_request_sha256'] != canonical_sha256(exact_request)
                or exact_request.get('schema') != reviewer_tasks.REQUEST_SCHEMA
                or exact_request.get('waiting_on') != record.waiting_on
                or exact_request.get('surface_seal') != record.surface_seal
                or record.review_token != record.payload.get('review_token')):
            _refuse('原 native 回答不是此审批面的完整选择')
        mutable_fields = ('general_reason', 'strategy') if record.surface == 'design' else ('reason',)
        chapter_rows = record.payload.get('chapters')
        if (not isinstance(chapter_rows, list)
                or any(not isinstance(row, dict) or type(row.get('output_ordinal')) is not int
                       for row in chapter_rows)
                or len({row['output_ordinal'] for row in chapter_rows}) != len(chapter_rows)
                or any(key not in record.payload for key in mutable_fields)):
            _refuse('正式决定没有完整唯一的章节集与可编辑载荷')
        projected = {key: record.payload[key] for key in mutable_fields}
        projected['chapters'] = {
            str(row['output_ordinal']): {key: value for key, value in row.items()
                                         if key != 'output_ordinal'} for row in chapter_rows}
        response_sha256 = hashlib.sha256(json.dumps(
            document, ensure_ascii=False, sort_keys=True, separators=(',', ':'),
            allow_nan=False).encode('utf-8') + b'\n').hexdigest()
        if (selection['payload'] != projected
                or response_sha256 != record.provider_response_sha256):
            _refuse('正式决定与独立绑定的原 native 回答不同')
        exact_action = exact_request.get('action')
        if (not isinstance(exact_action, dict) or exact_action.get('kind') != 'approval'
                or exact_action.get('surface') != record.surface
                or not isinstance(exact_action.get('payload'), dict)):
            _refuse('原 immutable option 不属于当前完整审批面')
        assembled = copy.deepcopy(exact_action['payload'])
        original_chapters = assembled.get('chapters')
        if (not isinstance(original_chapters, list)
                or any(not isinstance(row, dict) or type(row.get('output_ordinal')) is not int
                       for row in original_chapters)
                or [row['output_ordinal'] for row in original_chapters]
                != [row['output_ordinal'] for row in chapter_rows]):
            _refuse('原 immutable option 完整章集/顺序已变化')
        assembled.update({key: selection['payload'][key] for key in mutable_fields})
        for row in original_chapters:
            row.update(selection['payload']['chapters'][str(row['output_ordinal'])])
        if assembled != record.payload:
            _refuse('正式决定的 actor/token/完整身份与原 immutable option 不一致')
        attempt = turn.attempts[-1]
        sessions = [row for row in agent_session_audit.fold(agent_session_audit.read_events(base))
                    if row.session_run_id == turn.session_run_id]
        if (len(sessions) != 1 or attempt.finished_at is None or attempt.exit_code != 0
                or attempt.error_kind is not None or not attempt.wire_evidence
                or not attempt.provider_session_id):
            _refuse('原决定缺唯一真实 exit0 attempt/session')
        session = sessions[0]
        epochs = [row for row in parent.epochs if row.epoch_id == turn.epoch_id]
        if (len(epochs) != 1 or not session.attempts or not session.turns
                or session.start.parent_run_id != parent.run_id
                or session.start.profile != parent.contract.profiles.commander
                or session.start.start_code_version != epochs[0].code_version
                or session.start.skill_sha256 != epochs[0].skill_sha256
                or session.provider_session_ids != (attempt.provider_session_id,)):
            _refuse('原成功 session 的 parent/epoch/profile/provider 身份漂移')
        audit_attempt, audit_turn = session.attempts[-1], session.turns[-1]
        # provider attempt 已成功且原回答有独立 digest；只有登记的后继盘面
        # 观测失败能保留决定。它不把该次交付 unknown 或原成本错误改成成功。
        allowed_delivery_errors = {None, 'commander_next_action_unavailable'}
        if (audit_attempt.error_kind is not None
                or audit_turn.error_kind not in allowed_delivery_errors):
            _refuse('原 provider 调用或后继失败不属于可保留决定的登记类型')
        for event in (audit_attempt, audit_turn):
            if (event.attempt_id != attempt.attempt_id
                    or event.parent_run_id != parent.run_id or event.role != 'commander'
                    or event.node is not None or event.profile != session.start.profile
                    or event.provider_session_id != attempt.provider_session_id
                    or event.requested_model != session.start.requested_model
                    or event.requested_effort != session.start.requested_effort
                    or event.start_code_version != session.start.start_code_version
                    or event.schema_sha256 != session.start.schema_sha256
                    or event.subject_sha256 != session.start.subject_sha256
                    or event.skill_sha256 != session.start.skill_sha256
                    or event.usage is None or event.usage != audit_turn.usage
                    or event.prompt_sha256 != audit_turn.prompt_sha256):
                _refuse('原决定的成功 audit attempt/turn 不闭合')
        costs = [row for row in read_jsonl_rows(cost_ledger_path(base))
                 if row.get('attempt_id') == attempt.attempt_id]
        expected = {
            'run_id': parent.run_id, 'sid': sid, 'role': 'commander', 'node': None,
            'session_run_id': turn.session_run_id, 'activity': f'reviewer:{record.waiting_on}',
            'provider_session_id': attempt.provider_session_id, 'outcome': 'ok',
            'error_kind': audit_turn.error_kind, 'exit_code': 0, 'response_sha256': response_sha256,
            'code': epochs[0].code_version, 'request_model': audit_turn.requested_model,
            'reasoning_effort': audit_turn.requested_effort, 'model': audit_turn.actual_model,
            'actual_reasoning_effort': audit_turn.actual_effort,
            'protocol_version': audit_turn.protocol_version, 'cli_version': audit_turn.cli_version,
            **audit_turn.usage.to_dict(),
        }
        if (len(costs) != 1 or any(key not in costs[0] or costs[0][key] != value
                                   for key, value in expected.items())):
            _refuse('原决定的成功成本账与独立响应摘要不闭合')
        if session.end is not None and (
                session.end.process_state != 'ok' or session.end.error_kind != audit_turn.error_kind
                or session.end.delivery_state not in {'unknown', 'blocked'}):
            _refuse('原成功回答的 session 终态矛盾')
        if turn.outcome == 'started' and same_parent:
            _refuse('当前 owner 的 started 轮不能由重放从旁补尾')
        if (turn.outcome not in {'ok', 'started'}
                and not (turn.outcome == 'failed' and turn.exit_code == 70)):
            _refuse('原决定的席位终态与真实成功回答矛盾')
        if session.end is None and not same_parent:
            # 原 provider 的 actual model 允许未知，不能为了调用普通checkpoint
            # helper 填 requested model。新 ended 完整保留这次真实观测的未知项。
            end = {
                **session.start.to_dict(), 'kind': 'ended', 'ts': int(time.time()),
                'provider_session_id': attempt.provider_session_id,
                'actual_model': audit_turn.actual_model, 'actual_effort': audit_turn.actual_effort,
                'process_state': 'ok', 'delivery_state': 'unknown',
                'error_kind': audit_turn.error_kind,
                'end_code_version': session.start.start_code_version,
            }
            agent_session_audit.append_event(base, agent_session_audit.AgentSessionEvent.from_dict(end))
        if turn.outcome == 'started':
            reviewer_seat_ledger.finish(
                base, sid, waiting_on=record.waiting_on, business_sha256=record.business_sha256,
                turn_id=turn.turn_id, exit_code=0)
