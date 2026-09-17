"""核对已排空、无回答的 Commander 连接失败；免费收口，不退款或生成决定。

工程入口先 preview，再用相同 token、actor/reason apply。成本、session、原请求、
日志与 owner 结束证明必须闭合；未知用量仍保留未知。运行代码不自动批准此动作。

Commander 受托审阅已退役（2026-09-15）；本 CLI 只服务存量书历史工作单元的
owner 停止证明与失败收口，不再产生新的 commander 工作单元。原 commander
profile 自 profiles v3 读边界起不可恢复，改用 append-only started 原字节核对。
"""
from __future__ import annotations

import argparse
from contextlib import ExitStack
from dataclasses import dataclass
import hashlib
import json
import os
from pathlib import Path
from uuid import uuid4

from contracts.schema.work_unit_recovery import (
    CommanderFailureRecoveryPreview, CommanderFailureRecoveryReceipt,
    CommanderFailureRecoveryRequest,
)
from contracts.schema.workflow import WorkUnitBlobV1, WorkUnitContentTurn, WorkUnitLedgerV2
from novel import prov, work_units
from novel.llm import session_audit as agent_session_audit
from novel.orchestrator import execution_context
from novel.appendlog import path_transaction_lock
from novel.llm.json_text import decode_wire_json
from novel.errors import NovelError
from novel.orchestrator import admission, approvals, jobs
from novel.orchestrator.work_unit_evidence import (
    cost_evidence, load_budget, owner_stop_proof, read_rows,
)
from novel.work_unit_usage import summarize

#: 同席 resume 的续接提示（原 ``agent._REVIEWER_RESUME_PROMPT``，随 Commander
#: 执行链退役搬入本模块）：上一进程已因网络/瞬态故障终止，续接同一 provider
#: 会话完成同一 immutable workpack。历史失败收口仍按这串字节核对 prompt SHA。
_REVIEWER_RESUME_PROMPT = (
    '上一进程因网络或瞬态故障中断，本轮是同一席位的续接。请继续同一 immutable '
    'workpack 与同一 typed 请求文件路径完成审阅并写出请求；不要重读盘上产物，'
    '不要更改请求文件路径，也不要重复已经写出的请求。'
)


@dataclass(frozen=True)
class _Inspection:
    view: CommanderFailureRecoveryPreview
    budget: work_units.WorkUnitBudget
    ledger: WorkUnitLedgerV2
    evidence: dict
    owners: tuple[str, ...]


def _no_saved_answer(budget: work_units.WorkUnitBudget, turn: WorkUnitContentTurn) -> None:
    budget.request_payload(turn.turn_id)
    # save_response 先写 blob 再登记摘要；原子替换前中断留下的孤儿回答也要挡住。
    for path in budget.directory.glob('*.json'):
        if path == budget.path:
            continue
        raw = path.read_bytes()
        if hashlib.sha256(raw).hexdigest() != path.stem:
            raise work_units.WorkUnitRefused('工作单元 blob 字节身份损坏')
        try:
            blob = WorkUnitBlobV1.model_validate_json(raw)
        except ValueError as exc:
            raise work_units.WorkUnitRefused('工作单元含无法识别的恢复材料') from exc
        if blob.unit_id != budget.unit_id:
            raise work_units.WorkUnitRefused('工作单元目录混入其他主体')
        if blob.turn_id != turn.turn_id or path.stem == turn.request_payload_sha256:
            continue
        # apply 在 blob 已写、账未发布时中断，可以重放同一收口证明。
        try:
            prior = CommanderFailureRecoveryReceipt.model_validate(blob.payload)
        except ValueError as exc:
            raise work_units.WorkUnitRefused('已有未登记回答或其他恢复材料，须先恢复原结果') from exc
        if prior.before != turn or prior.unit_id != budget.unit_id:
            raise work_units.WorkUnitRefused('已有未登记回答或其他恢复材料，须先恢复原结果')


def _inspect(base: str | Path, sid: str, unit_id: str, turn_id: str) -> _Inspection:
    from novel.orchestrator import agent
    budget = load_budget(base, sid, unit_id)
    ledger = budget._read()
    turn = budget._turn(ledger, turn_id)
    if (ledger.role != 'commander' or turn != ledger.turns[-1]
            or turn.state != 'unknown' or turn.reason_code != 'commander_transport_failed'
            or any((turn.response_sha256, turn.recovery_sha256, turn.producer_sha256,
                    turn.feedback_sha256, ledger.group_producer_sha256))
            or not turn.attempts or any(row.outcome != 'error' for row in turn.attempts)):
        raise work_units.WorkUnitRefused('只可收口最后一轮、无回答且全部尝试已结束的 Commander 传输失败')
    _no_saved_answer(budget, turn)
    _cost_view, all_costs, owners = cost_evidence(budget, ledger)
    owner_proof = owner_stop_proof(budget, ledger, owners, all_costs)
    attempt_ids = {row.attempt_id for row in turn.attempts}
    costs = [row for row in all_costs if row.get('attempt_id') in attempt_ids]
    sessions = agent_session_audit.fold([
        agent_session_audit.AgentSessionEvent.from_dict(row) for row in
        read_rows(agent_session_audit.audit_path(base), 'Commander 失败收口 session 账')])
    matches = [row for row in sessions if row.session_run_id == turn.session_run_id]
    if len(matches) != 1 or matches[0].end is None or matches[0].end.process_state != 'failed':
        raise work_units.WorkUnitRefused('原 Commander 会话缺明确失败终态')
    session = matches[0]
    request_payload = budget.request_payload(turn.turn_id)
    prompt = request_payload.get('prompt')
    if (not isinstance(prompt, str)
            or session.start.prompt_sha256 != hashlib.sha256(prompt.encode('utf-8')).hexdigest()
            or session.start.schema_sha256 != request_payload.get('schema_sha256')):
        raise work_units.WorkUnitRefused('原请求与会话起点的 prompt/schema 不闭合')
    runs = [row for row in owner_proof['runs'] if row['run_id'] == turn.run_id]
    # owner_proof 的 runs 来自 v3 fold，已不携带 commander 字段；冻结配置只能
    # 从 append-only started 原字节读取，读不到即关闭式拒绝，不得伪造通过。
    started_rows = [row for row in read_rows(agent.runs_path(base), 'Commander 失败收口 AgentRun 账')
                    if row.get('run_id') == turn.run_id and row.get('kind') == 'started']
    frozen_commander = (
        started_rows[0].get('start', {}).get('profiles', {}).get('commander')
        if len(started_rows) == 1 else None)
    if (len(runs) != 1 or session.start.profile is None
            or not isinstance(frozen_commander, dict)
            or session.start.profile.model_dump(mode='json') != frozen_commander):
        raise work_units.WorkUnitRefused('原失败会话与冻结的 Commander 配置不一致')
    if {row.attempt_id for row in session.attempts} != attempt_ids:
        raise work_units.WorkUnitRefused('原会话与工作单元的尝试分母不一致')
    for index, attempt in enumerate(turn.attempts):
        [row] = [item for item in costs if item.get('attempt_id') == attempt.attempt_id]
        [event] = [item for item in session.attempts if item.attempt_id == attempt.attempt_id]
        turns = [item for item in session.turns if item.attempt_id == attempt.attempt_id]
        if (row.get('outcome') != 'error' or row.get('cached') is not False
                or type(row.get('exit_code')) is not int or row['exit_code'] == 0
                or row.get('error_kind') not in ('connection', 'timeout')
                or row.get('error_kind') != event.error_kind
                or row.get('session_run_id') != turn.session_run_id
                or row.get('provider_session_id') != event.provider_session_id
                or event.provider_session_id != attempt.provider_session_id
                or row.get('response_sha256') is not None
                or (len(turns) != 1 if event.provider_session_id else bool(turns))
                or any(item.error_kind != event.error_kind for item in turns)):
            raise work_units.WorkUnitRefused('原失败的成本、attempt 与 provider turn 证据不闭合')
        metadata = {
            'request_model': event.requested_model,
            'reasoning_effort': event.requested_effort,
            'model': event.actual_model,
            'actual_reasoning_effort': event.actual_effort,
            'protocol_version': event.protocol_version,
            'code': event.start_code_version,
            'cli_version': event.cli_version,
        }
        usage = {} if event.usage is None else event.usage.to_dict()
        has_prior_session = any(row.provider_session_id is not None for row in turn.attempts[:index])
        expected_prompt = _REVIEWER_RESUME_PROMPT if has_prior_session else prompt
        if (any(row.get(key) != value for key, value in metadata.items())
                or event.prompt_sha256 != hashlib.sha256(expected_prompt.encode('utf-8')).hexdigest()
                or any(row.get(key) != usage.get(key) for key in (
                    'prompt_tokens', 'completion_tokens', 'total_tokens',
                    'prompt_cache_hit_tokens', 'prompt_cache_miss_tokens', 'reasoning_tokens'))
                or any(getattr(event, key) != getattr(session.start, key) for key in (
                    'profile', 'parent_run_id', 'role', 'subject_sha256',
                    'schema_sha256', 'start_code_version', 'protocol_version'))
                or any(any(getattr(item, key) != getattr(event, key) for key in (
                    'profile', 'provider_session_id', 'actual_model', 'actual_effort',
                    'usage', 'prompt_sha256', 'schema_sha256', 'protocol_version', 'start_code_version')) for item in turns)):
            raise work_units.WorkUnitRefused('原失败的模型、代码、协议或用量证据不一致')
    # 此窄入口只处理整次 run 没有任何完整模型回答的情况；不丢弃日志中的已购回答。
    raw_log = agent.run_log_path(base, turn.run_id).read_bytes()
    for line in raw_log.decode('utf-8').splitlines():
        line = line.lstrip().removeprefix('[stderr]').removeprefix('[stdout]').lstrip()
        if not line.startswith('{'):
            continue
        event = decode_wire_json(line)
        if not isinstance(event, dict):
            raise work_units.WorkUnitRefused('原运行日志协议形状不明')
        item = event.get('item')
        if item is not None and not isinstance(item, dict):
            raise work_units.WorkUnitRefused('原运行日志协议形状不明')
        if (event.get('type') in ('turn.completed', 'assistant')
                or isinstance(item, dict) and item.get('type') == 'agent_message'
                or event.get('type') == 'result' and (
                    event.get('subtype') == 'success'
                    or event.get('structured_output') is not None)):
            raise work_units.WorkUnitRefused('原运行日志已有模型回答或成功终态，须恢复原材料')
    evidence = dict(kind='commander-failure-reconciliation-v1', sid=sid,
        unit_id=unit_id, turn_id=turn_id, code_version=prov.code_version(),
        ledger=ledger.model_dump(mode='json'), costs=all_costs, owner_proof=owner_proof,
        session=[row.to_dict() for row in (session.start, *session.attempts, *session.turns, session.end)],
        log_sha256=hashlib.sha256(raw_log).hexdigest())
    # 首次结果与 JSON 盘上重放采用同一种嵌套形状（owner proof 含 tuple）。
    evidence = json.loads(json.dumps(evidence, ensure_ascii=False))
    token = work_units._digest(evidence)
    view = CommanderFailureRecoveryPreview(sid=sid, unit_id=unit_id, turn_id=turn_id,
        available=True, reason='原请求已失败并排空；免费确认失败，保留额度消耗及未知用量',
        preview_token=token, before=turn, usage=summarize(costs))
    return _Inspection(view, budget, ledger, evidence, tuple(sorted(owners)))


def inspect(base: str | Path, sid: str, unit_id: str, turn_id: str) -> CommanderFailureRecoveryPreview:
    try:
        return _inspect(base, sid, unit_id, turn_id).view
    except (NovelError, OSError, ValueError, KeyError) as exc:
        return CommanderFailureRecoveryPreview(sid=sid, unit_id=unit_id, turn_id=turn_id,
            available=False, reason='不能收口，保留 unknown：' + (
                str(exc) if isinstance(exc, (NovelError, OSError)) else '来源形状不符合契约'))


def apply(base: str | Path, sid: str, unit_id: str, turn_id: str,
          request: CommanderFailureRecoveryRequest) -> CommanderFailureRecoveryReceipt:
    if any(key in os.environ for key in (
            execution_context.COMMANDER_RUN_ID_ENV, execution_context.COMMANDER_CONTRACT_SHA256_ENV,
            execution_context.COMMANDER_EPOCH_ID_ENV, execution_context.COMMANDER_EPOCH_SHA256_ENV,
            execution_context.COMMANDER_ADMISSION_ID_ENV)):
        raise work_units.WorkUnitRefused('Commander 子上下文不能执行工程失败收口')
    request = CommanderFailureRecoveryRequest.model_validate(request.model_dump(mode='json'))
    budget = load_budget(base, sid, unit_id)
    writer_id = 'failure-reconcile-' + uuid4().hex
    key = jobs.acquire_job_writer(base, sid, budget.identity['node'], writer_id)
    try:
        with approvals.approval_file_lock(base, sid), path_transaction_lock(budget.path):
            ledger = budget._read()
            turn = budget._turn(ledger, turn_id)
            if turn.reason_code == work_units.COMMANDER_FAILURE_RECONCILED:
                receipt = budget.failure_recovery_receipt(ledger, turn)
                if receipt.request != request:
                    raise work_units.WorkUnitRefused('已收口的主体绑定不同请求，不能改写原裁决')
                return receipt
            current = _inspect(base, sid, unit_id, turn_id)
            if current.view.preview_token != request.preview_token:
                raise work_units.WorkUnitRefused('失败收口预览已经漂移')
            with ExitStack() as stopped:
                for owner in current.owners:
                    stopped.enter_context(admission.require_owner_stopped(base, owner))
                checked = _inspect(base, sid, unit_id, turn_id)
                if checked.view.preview_token != request.preview_token:
                    raise work_units.WorkUnitRefused('失败收口在 owner lease 内复验时漂移')
                receipt = CommanderFailureRecoveryReceipt(sid=sid, unit_id=unit_id,
                    before=budget._turn(checked.ledger, turn_id), request=request,
                    evidence=checked.evidence)
                budget.reconcile_failure_under_lock(receipt)
                return receipt
    finally:
        jobs.REGISTRY.release(key, writer_id)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--base', required=True)
    parser.add_argument('--sid', required=True)
    parser.add_argument('--unit-id', required=True)
    parser.add_argument('--turn-id', required=True)
    parser.add_argument('--apply-request', type=Path, help='含 preview_token、actor、reason 的 JSON；省略只读')
    args = parser.parse_args(argv)
    try:
        if args.apply_request is None:
            view = inspect(args.base, args.sid, args.unit_id, args.turn_id)
            print(view.model_dump_json(indent=2))
            return 0 if view.available else 2
        request = CommanderFailureRecoveryRequest.model_validate_json(args.apply_request.read_bytes())
        receipt = apply(args.base, args.sid, args.unit_id, args.turn_id, request)
        print(json.dumps(dict(sid=args.sid, unit_id=args.unit_id, turn_id=args.turn_id,
                             state='failed', preview_token=receipt.request.preview_token), ensure_ascii=False))
        return 0
    except (NovelError, OSError, ValueError, KeyError) as exc:
        print(json.dumps({'error': str(exc) if isinstance(exc, (NovelError, OSError))
                         else '恢复请求或来源形状不符合契约'}, ensure_ascii=False))
        return 2


if __name__ == '__main__':
    raise SystemExit(main())
