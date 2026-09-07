#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""零模型复验已结束 Commander 的登记协议故障。

只读取本服务保留的协议日志，不读取 CLI 私有 rollout，不调用模型、不改旧账。
这不是一般的失败重试：只接受传输 error item 误判，或 Stage4 完整请求后精确
重复 option id 的尾部包装错误。当前驱动必须重新证明已完成、无工具的响应。
后者按席位顺序复验全部计费轮，只采用最新轮，绝不挑选历史有利决定或返还预算。
调用方还须持 source owner-stopped lease，
按当前业务/token 走 typed loader，先 durable 再交 canonical approval。
"""
from __future__ import annotations

import hashlib
import json
import os
import uuid
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Sequence

from pydantic import ValidationError

from contracts.schema.api import DesignReviewRequest, StructureApprovalRequest
from contracts.schema.orchestration import (
    AgentProfile, AgentRunRecord, ReviewerActionRecord,
    ReviewerProtocolRecoveryProof, ReviewerSeatLedger,
)
from novel import agent_session_audit, paths
from novel.agents.base import decode_wire_json
from novel.commanders.codex import CodexCommanderDriver
from novel.llm.ledger import ledger_path as cost_ledger_path, read_jsonl_rows
from novel.orchestrator import queue, reviewer_seat_ledger, reviewer_tasks


_LEGACY_REJECTION = (
    'novel.commanders.base.CommanderProtocolError: '
    'Codex structured reviewer 触发了禁止的工具/未知 item：error')
_SUFFIX_REJECTION = 'Commander 最终回答不是合法的裸 JSON object'
_SUFFIX_POLICY = 'codex-redundant-option-suffix-v1'
_STAGE4_SURFACES = {
    queue.WAITING_ON_STAGE4_DESIGN_REVIEW: 'design',
    queue.WAITING_ON_STAGE4_BINDING_RESOLUTION: 'design',
    queue.WAITING_ON_STAGE4_STRUCTURE_REPLAN: 'design',
    queue.WAITING_ON_STAGE4_STRUCTURE_REVIEW: 'structure',
}


def _digest(raw: bytes) -> str:
    return hashlib.sha256(raw).hexdigest()


def _canonical(value: Any) -> str:
    return _digest(json.dumps(
        value, ensure_ascii=False, sort_keys=True, separators=(',', ':'),
        allow_nan=False).encode('utf-8'))


def _refuse(reason: str) -> None:
    raise reviewer_tasks.ReviewerTaskError(f'Commander 协议恢复证据不闭合：{reason}')


def _plain_bytes(base: str, path: Path) -> bytes:
    root = paths.service_dir(base).absolute()
    if path.is_symlink() or not path.is_file() or root.is_symlink():
        _refuse('来源不是 service 内普通文件')
    try:
        path.resolve().relative_to(root.resolve())
    except ValueError:
        _refuse('来源越出 service')
    return path.read_bytes()


def has_registered_rejection(base: str, log_path: Path) -> bool:
    """未登记的协议失败沿用原停机路由，不能被恢复策略误接管。"""
    if not log_path.exists():
        return False
    return _LEGACY_REJECTION in _plain_bytes(base, log_path).decode('utf-8').splitlines()


def is_recovery_candidate(ledger: ReviewerSeatLedger) -> bool:
    """只做无 I/O 路由；命中后仍须检查原日志、审计、成本与当前 typed option。"""
    # 旧响应恢复证明覆盖一条 logical session；配置拒绝后的混合账不能
    # 删掉原失败轮再借这条策略恢复。它只走自己的新会话与 typed 交付链。
    if any(row.schema_recovery is not None for row in ledger.turns):
        return False
    pending = ledger.pending_correction
    if (ledger.waiting_on in _STAGE4_SURFACES and ledger.turns
            and pending is not None and pending.reason == _SUFFIX_REJECTION):
        latest = ledger.turns[-1]
        return (pending.source_turn_id == latest.turn_id
                and latest.outcome == 'ok' and latest.exit_code == 0)
    if len(ledger.turns) != 1 or pending is not None:
        return False
    turn = ledger.turns[0]
    return (turn.purpose == 'review' and turn.outcome == 'failed'
            and turn.exit_code == 70 and turn.provider_response_sha256 is None
            and len(turn.attempts) == 1 and turn.attempts[0].error_kind == 'protocol')


def _unique_object(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    document: dict[str, Any] = {}
    for key, value in pairs:
        if key in document:
            _refuse('原 JSON 包含重复 object key')
        document[key] = value
    return document


def _suffix_document(
    response: str, submission_options: Sequence[dict[str, Any]],
) -> dict[str, Any]:
    """只去掉精确登记的冗余 option 尾巴，不补字段、不修语义、不截取候选。"""
    source = response.strip()
    try:
        document, end = json.JSONDecoder(object_pairs_hook=_unique_object).raw_decode(source)
    except (ValueError, TypeError) as exc:
        _refuse(f'原回答没有唯一完整 JSON 前缀（{type(exc).__name__}）')
    if (not isinstance(document, dict)
            or set(document) != {'schema', 'waiting_on', 'surface_seal', 'action'}
            or document.get('schema') != reviewer_tasks.REQUEST_SCHEMA
            or not isinstance(document.get('surface_seal'), str)
            or not document['surface_seal']):
        _refuse('原回答前缀不是完整 typed wrapper')
    waiting_on = document.get('waiting_on')
    surface = _STAGE4_SURFACES.get(waiting_on) if isinstance(waiting_on, str) else None
    action = document.get('action')
    if (surface is None or not isinstance(action, dict)
            or set(action) != {'kind', 'surface', 'payload'}
            or action.get('kind') != 'approval' or action.get('surface') != surface):
        _refuse('冗余 option 恢复只接受 Stage4 当前 approval')
    option_id = f'approval:{surface}'
    if source[end:] != ',"id":' + json.dumps(option_id) + '}':
        _refuse('原回答尾部不是精确登记的冗余 option id')
    options = [option for option in submission_options if option.get('id') == option_id]
    if len(options) != 1:
        _refuse('当前 Stage4 approval option 不唯一')
    expected = options[0].get('exact_request')
    if (not isinstance(expected, dict)
            or expected.get('schema') != document['schema']
            or expected.get('waiting_on') != document['waiting_on']
            or not isinstance(expected.get('action'), dict)
            or expected['action'].get('kind') != 'approval'
            or expected['action'].get('surface') != surface):
        _refuse('冗余 option 与当前 exact request 不匹配')
    try:
        model = DesignReviewRequest if surface == 'design' else StructureApprovalRequest
        canonical_payload = model.model_validate(action['payload']).model_dump(mode='json')
        if _canonical(canonical_payload) != _canonical(action['payload']):
            _refuse('原回答 payload 需要补字段或改变值，超出尾部包装恢复范围')
        _canonical(document)  # NaN/Infinity 等非 JSON 取值同样不得恢复。
    except (ValidationError, TypeError, ValueError) as exc:
        _refuse(f'原回答前缀没有通过现役 Stage4 typed 形状（{type(exc).__name__}）')
    return document


def _single_attempt_stream(raw: bytes, provider_id: str) -> str:
    """日志的明确 stdout 标签才是 wire；stderr、traceback 和小说字符串不是。"""
    blocks: list[list[str]] = [[]]
    for line in raw.decode('utf-8').splitlines():
        # Windows 日志可能含 CRCRLF；空行不是另一条协议事件。
        if not line.strip():
            continue
        if line.startswith('$ '):
            blocks.append([])
        blocks[-1].append(line)
    matches: list[tuple[list[str], list[str]]] = []
    for block in blocks:
        lines = [block[i + 1] for i, line in enumerate(block[:-1])
                 if line == '[stdout protocol]'
                 and block[i + 1] != '[stdout protocol]']
        events = [decode_wire_json(line) for line in lines]
        if any(isinstance(event, dict)
               and event.get('type') == 'thread.started'
               and event.get('thread_id') == provider_id for event in events):
            matches.append((block, lines))
    if len(matches) != 1:
        _refuse('原 provider 对应的调用日志不唯一')
    block, lines = matches[0]
    if block.count(_LEGACY_REJECTION) != 1:
        _refuse('没有唯一已登记的旧协议误拒')
    events = [decode_wire_json(line) for line in lines]
    if (sum(isinstance(e, dict) and e.get('type') == 'thread.started'
            for e in events) != 1
            or sum(isinstance(e, dict) and e.get('type') == 'turn.completed'
                   for e in events) != 1
            or sum(isinstance(e, dict) and e.get('type') == 'item.completed'
                   and isinstance(e.get('item'), dict)
                   and e['item'].get('type') == 'agent_message'
                   for e in events) != 1
            or not any(isinstance(e, dict) and isinstance(e.get('item'), dict)
                       and e['item'].get('type') == 'error' for e in events)):
        _refuse('缺唯一成功终态、最终回答或传输通知')
    return '\n'.join(lines) + '\n'


@dataclass(frozen=True)
class RecoveredReviewerResponse:
    document: dict[str, Any]
    proof: ReviewerProtocolRecoveryProof
    raw_response: str


def decode_recovered_response(
    recovered: RecoveredReviewerResponse,
    submission_options: Sequence[dict[str, Any]] = (),
) -> dict[str, Any]:
    """durable 重放时从原 wire 再解码，不把已恢复 document 当独立证据。"""
    if recovered.proof.policy == _SUFFIX_POLICY:
        document = _suffix_document(recovered.raw_response, submission_options)
    else:
        try:
            document = json.JSONDecoder(object_pairs_hook=_unique_object).decode(
                recovered.raw_response)
        except (TypeError, ValueError) as exc:
            _refuse(f'原回答不是严格 JSON（{type(exc).__name__}）')
        if not isinstance(document, dict):
            _refuse('原回答不是 JSON object')
    if (_canonical(document) != recovered.proof.response_sha256
            or document != recovered.document
            or _digest(recovered.raw_response.encode('utf-8'))
            != recovered.proof.raw_response_sha256):
        _refuse('重新解码的回答与恢复 proof 不一致')
    return document


def _seat_streams(raw: bytes, provider_id: str, count: int) -> list[str]:
    """同 provider 的全部调用必须与席位轮次一一对应，不能按内容挑选回答。"""
    blocks: list[list[str]] = [[]]
    for line in raw.decode('utf-8').splitlines():
        if not line.strip():
            continue
        if line.startswith('$ '):
            blocks.append([])
        blocks[-1].append(line)
    streams: list[str] = []
    for block in blocks:
        lines = [block[i + 1] for i, line in enumerate(block[:-1])
                 if line == '[stdout protocol]' and block[i + 1] != '[stdout protocol]']
        events = [decode_wire_json(line) for line in lines]
        if not any(isinstance(event, dict)
                   and event.get('type') == 'thread.started'
                   and event.get('thread_id') == provider_id for event in events):
            continue
        # 先按可观察 provider 选调用块，再严格复验整个块的每一行。
        try:
            events = [json.JSONDecoder(object_pairs_hook=_unique_object).decode(line)
                      for line in lines]
        except (TypeError, ValueError) as exc:
            _refuse(f'原协议事件不是严格 JSON（{type(exc).__name__}）')
        if (sum(isinstance(e, dict) and e.get('type') == 'thread.started'
                for e in events) != 1
                or sum(isinstance(e, dict) and e.get('type') == 'turn.completed'
                       for e in events) != 1
                or sum(isinstance(e, dict) and e.get('type') == 'item.completed'
                       and isinstance(e.get('item'), dict)
                       and e['item'].get('type') == 'agent_message'
                       for e in events) != 1):
            _refuse('原调用缺唯一 provider、完成终态或最终回答')
        streams.append('\n'.join(lines) + '\n')
    if len(streams) != count:
        _refuse('原 provider 调用数与 reviewer 席位轮数不相等')
    return streams


def _inspect_suffix_response(
    base: str, sid: str, *, ledger: ReviewerSeatLedger,
    parent: AgentRunRecord, log_path: Path, profile: AgentProfile,
    validation_code: str, submission_options: Sequence[dict[str, Any]],
) -> RecoveredReviewerResponse:
    pending = ledger.pending_correction
    assert pending is not None
    turn = ledger.turns[-1]
    if (ledger.sid != sid or parent.start.sid != sid
            or parent.ended is None or parent.active_epoch_id is not None
            or parent.exit_code != 2 or parent.contract is None
            or parent.contract.profiles.commander != profile or profile.driver != 'codex'
            or pending.source_turn_id != turn.turn_id
            or pending.session_run_id != turn.session_run_id
            or any(row.parent_run_id != parent.run_id or row.epoch_id != turn.epoch_id
                   or row.outcome != 'ok' or row.exit_code != 0
                   or row.finished_at is None or len(row.attempts) != 1
                   or row.provider_response_sha256 is not None for row in ledger.turns)):
        _refuse('原 Stage4 parent/epoch/席位未证明已结束的裸 JSON 拒绝')
    epochs = [epoch for epoch in parent.epochs if epoch.epoch_id == turn.epoch_id]
    if len(epochs) != 1:
        _refuse('原 epoch 不唯一')
    epoch = epochs[0]
    events = [event for event in agent_session_audit.read_events(base)
              if event.session_run_id == turn.session_run_id]
    expected_kinds = ['started', *['attempt', 'turn'] * len(ledger.turns), 'ended']
    if [event.kind for event in events] != expected_kinds:
        _refuse('原 session 事件与完整席位轮次不一一对应')
    sessions = agent_session_audit.fold(events)
    if len(sessions) != 1:
        _refuse('原 logical session 不唯一')
    session = sessions[0]
    start, end = session.start, session.end
    if (end is None or end.process_state != 'ok' or end.delivery_state != 'blocked'
            or end.error_kind != 'commander_reviewer_request'
            or end.provider_session_id != pending.provider_session_id
            or end.end_code_version != epoch.code_version
            or start.prompt_sha256 is None or start.schema_sha256 is None
            or end.prompt_sha256 != start.prompt_sha256):
        _refuse('原 session 缺完整已结束的 typed 拒绝终态')
    for event in events:
        if (event.parent_run_id != parent.run_id or event.role != 'commander'
                or event.node is not None or event.profile != profile
                or event.requested_model != profile.model
                or event.requested_effort != profile.reasoning_effort
                or event.protocol_version != 'codex-jsonl-v1'
                or event.start_code_version != epoch.code_version
                or event.skill_sha256 != epoch.skill_sha256
                or event.subject_sha256 != start.prompt_sha256
                or event.schema_sha256 != start.schema_sha256):
            _refuse('原 session 请求、schema、代码或 profile 身份漂移')
    attempt_ids = {row.attempts[0].attempt_id for row in ledger.turns}
    costs = [row for row in read_jsonl_rows(cost_ledger_path(base))
             if row.get('session_run_id') == turn.session_run_id
             or row.get('attempt_id') in attempt_ids]
    if len(costs) != len(ledger.turns):
        _refuse('原成本与席位轮数不一一对应')
    raw = _plain_bytes(base, log_path)
    streams = _seat_streams(raw, pending.provider_session_id, len(ledger.turns))
    responses: list[str] = []
    ordered_costs: list[dict[str, Any]] = []
    driver = CodexCommanderDriver(executable='codex.exe')
    for index, (seat, stdout) in enumerate(zip(ledger.turns, streams, strict=True)):
        attempt = seat.attempts[0]
        audit_attempt, audit_turn = session.attempts[index], session.turns[index]
        if (attempt.exit_code != 0 or attempt.error_kind is not None
                or attempt.finished_at is None or not attempt.wire_evidence
                or attempt.provider_session_id != pending.provider_session_id
                or audit_attempt.attempt_id != attempt.attempt_id
                or audit_turn.attempt_id != attempt.attempt_id
                or audit_attempt.provider_session_id != pending.provider_session_id
                or audit_turn.provider_session_id != pending.provider_session_id
                or audit_turn.turn_index != seat.ordinal
                or audit_attempt.error_kind is not None or audit_turn.error_kind is not None
                or audit_attempt.usage is None or audit_attempt.usage != audit_turn.usage
                or audit_attempt.prompt_sha256 is None
                or audit_attempt.prompt_sha256 != audit_turn.prompt_sha256
                or audit_attempt.schema_sha256 != audit_turn.schema_sha256
                or audit_attempt.ts != attempt.finished_at
                or audit_turn.ts > seat.finished_at):
            _refuse('原 attempt/turn/provider/请求/计量与席位不一致')
        matching_costs = [cost for cost in costs if cost.get('attempt_id') == attempt.attempt_id]
        if len(matching_costs) != 1:
            _refuse('原成本 attempt 不唯一')
        cost = matching_costs[0]
        # 调用纯 decoder；它复验无工具与成功终态，不触碰 probe/run/resume。
        observation, response = driver._decode(
            stdout, profile=profile, exit_code=0,
            expected_provider_session_id=pending.provider_session_id,
            structured_response=True)
        if response is None or observation.usage is None:
            _refuse('原调用没有完整回答与计量')
        expected = {
            'run_id': parent.run_id, 'sid': sid, 'role': 'commander', 'node': None,
            'session_run_id': turn.session_run_id,
            'activity': f'reviewer:{ledger.waiting_on}',
            'provider_session_id': pending.provider_session_id,
            'outcome': 'ok', 'error_kind': None, 'exit_code': 0,
            'response_sha256': _digest(response.encode('utf-8')),
            'code': epoch.code_version, 'request_model': profile.model,
            'reasoning_effort': profile.reasoning_effort,
            'model': audit_turn.actual_model,
            'actual_reasoning_effort': audit_turn.actual_effort,
            'protocol_version': audit_turn.protocol_version,
            'cli_version': audit_turn.cli_version, **audit_turn.usage.to_dict(),
        }
        usage = {key: getattr(observation.usage, key) for key in audit_turn.usage.to_dict()}
        if (any(key not in cost or cost[key] != value for key, value in expected.items())
                or usage != audit_turn.usage.to_dict()
                or observation.actual_model != audit_turn.actual_model
                or observation.actual_effort != audit_turn.actual_effort):
            _refuse('原日志回答/计量与成本及 session 观测不一致')
        responses.append(response)
        ordered_costs.append(cost)
    response = responses[-1]
    if _digest(response.strip().encode('utf-8')) != pending.request_sha256:
        _refuse('最新原始回答与 pending 拒因身份不一致')
    document = _suffix_document(response, submission_options)
    if document['waiting_on'] != ledger.waiting_on:
        _refuse('原回答不是当前 blocker')
    seat_path = reviewer_seat_ledger.ledger_path(
        base, sid, waiting_on=ledger.waiting_on, business_sha256=ledger.business_sha256)
    return RecoveredReviewerResponse(document, ReviewerProtocolRecoveryProof(
        policy=_SUFFIX_POLICY, session_run_id=turn.session_run_id, turn_id=turn.turn_id,
        attempt_id=turn.attempts[0].attempt_id,
        provider_session_id=pending.provider_session_id,
        parent_sha256=_canonical(parent.model_dump(mode='json')),
        seat_sha256=_digest(_plain_bytes(base, seat_path)),
        session_sha256=_canonical([event.to_dict() for event in events]),
        cost_sha256=_canonical(ordered_costs), log_sha256=_digest(raw),
        stdout_sha256=_canonical(streams), raw_response_sha256=_digest(response.encode('utf-8')),
        response_sha256=_canonical(document), validation_code_version=validation_code), response)


def inspect_response(
    base: str, sid: str, *, ledger: ReviewerSeatLedger,
    parent: AgentRunRecord, log_path: Path, profile: AgentProfile,
    validation_code: str, submission_options: Sequence[dict[str, Any]] = (),
) -> RecoveredReviewerResponse | None:
    """已知失败入口返回可复验原回答；无此类失败返回 None，证据矛盾则关闭。"""
    if not is_recovery_candidate(ledger):
        return None
    if ledger.pending_correction is not None:
        return _inspect_suffix_response(
            base, sid, ledger=ledger, parent=parent, log_path=log_path, profile=profile,
            validation_code=validation_code, submission_options=submission_options)
    if (len(ledger.turns) != 1 or ledger.pending_correction is not None
            or ledger.turns[0].purpose != 'review'):
        return None
    turn = ledger.turns[0]
    if (turn.outcome != 'failed' or turn.exit_code != 70
            or turn.provider_response_sha256 is not None
            or len(turn.attempts) != 1):
        return None
    attempt = turn.attempts[0]
    if attempt.error_kind != 'protocol':
        return None
    if (ledger.sid != sid or turn.parent_run_id != parent.run_id
            or parent.start.sid != sid or parent.ended is None
            or parent.active_epoch_id is not None or parent.exit_code != 70
            or parent.contract is None or parent.contract.profiles.commander != profile
            or profile.driver != 'codex'
            or attempt.finished_at is None or attempt.exit_code != 70
            or not attempt.wire_evidence or not attempt.provider_session_id):
        _refuse('原 parent/epoch/profile/attempt 未证明已结束的协议失败')
    epochs = [epoch for epoch in parent.epochs if epoch.epoch_id == turn.epoch_id]
    if len(epochs) != 1:
        _refuse('原 epoch 不唯一')
    epoch = epochs[0]
    session_events = [event for event in agent_session_audit.read_events(base)
                      if event.session_run_id == turn.session_run_id]
    if (len(session_events) != 4
            or sorted(event.kind for event in session_events)
            != ['attempt', 'ended', 'started', 'turn']):
        _refuse('原 session 事件不是单次调用的完整四事件')
    sessions = agent_session_audit.fold(session_events)
    if len(sessions) != 1:
        _refuse('原 logical session 不唯一')
    session = sessions[0]
    if (session.end is None or len(session.attempts) != 1 or len(session.turns) != 1
            or session.process_state != 'failed'
            or session.error_kind != 'commander_protocol'):
        _refuse('原 session 没有完整协议失败终态')
    audit_attempt, audit_turn = session.attempts[0], session.turns[0]
    if (session.start.prompt_sha256 is None or session.start.schema_sha256 is None
            or session.end.end_code_version != epoch.code_version):
        _refuse('原 session 缺请求/schema/结束代码身份')
    for event in (session.start, audit_attempt, audit_turn, session.end):
        if (event.parent_run_id != parent.run_id or event.role != 'commander'
                or event.node is not None or event.profile != profile
                or event.protocol_version != 'codex-jsonl-v1'
                or event.start_code_version != epoch.code_version
                or event.prompt_sha256 != session.start.prompt_sha256
                or event.schema_sha256 != session.start.schema_sha256
                or event.skill_sha256 != epoch.skill_sha256):
            _refuse('session 的请求、schema、代码或 profile 身份漂移')
    if (audit_attempt.error_kind != 'protocol'
            or audit_turn.error_kind != 'commander_protocol'
            or audit_attempt.attempt_id != attempt.attempt_id
            or audit_turn.attempt_id != attempt.attempt_id
            or audit_attempt.provider_session_id != attempt.provider_session_id
            or audit_turn.provider_session_id != attempt.provider_session_id
            or session.end.provider_session_id != attempt.provider_session_id
            or audit_attempt.usage is None or audit_attempt.usage != audit_turn.usage):
        _refuse('attempt/turn/provider/计量不一致')
    costs = [row for row in read_jsonl_rows(cost_ledger_path(base))
             if row.get('attempt_id') == attempt.attempt_id]
    if len(costs) != 1:
        _refuse('成本 attempt 不唯一')
    cost = costs[0]
    expected = {
        'run_id': parent.run_id, 'sid': sid, 'role': 'commander', 'node': None,
        'session_run_id': turn.session_run_id,
        'activity': f'reviewer:{ledger.waiting_on}',
        'provider_session_id': attempt.provider_session_id,
        'outcome': 'error', 'error_kind': 'commander_protocol', 'exit_code': 70,
        'response_sha256': None, 'code': epoch.code_version,
        'request_model': profile.model, 'reasoning_effort': profile.reasoning_effort,
        'model': audit_turn.actual_model,
        'actual_reasoning_effort': audit_turn.actual_effort,
        'protocol_version': audit_turn.protocol_version, 'cli_version': audit_turn.cli_version,
        **audit_turn.usage.to_dict(),
    }
    if any(key not in cost or cost[key] != value for key, value in expected.items()):
        _refuse('成本与原失败调用身份/计量不一致')
    raw = _plain_bytes(base, log_path)
    stdout = _single_attempt_stream(raw, attempt.provider_session_id)
    driver = CodexCommanderDriver(executable='codex.exe')
    observation, response = driver._decode(
        stdout, profile=profile, exit_code=0,
        expected_provider_session_id=attempt.provider_session_id,
        structured_response=True)
    # 调用的只有纯 decoder，绝不 probe/run/resume。旧 failure/费用/席位原样保留。
    if response is None or observation.usage is None:
        _refuse('当前 decoder 没有完整回答')
    assert response is not None and observation.usage is not None
    usage = {key: getattr(observation.usage, key)
             for key in audit_turn.usage.to_dict()}
    if (usage != audit_turn.usage.to_dict()
            or observation.actual_model != audit_turn.actual_model
            or observation.actual_effort != audit_turn.actual_effort):
        _refuse('日志与已记账的 provider 观测不一致')
    document = decode_wire_json(response)
    if (not isinstance(document, dict)
            or set(document) != {'schema', 'waiting_on', 'surface_seal', 'action'}
            or document.get('schema') != reviewer_tasks.REQUEST_SCHEMA
            or document.get('waiting_on') != ledger.waiting_on):
        _refuse('原回答不是当前 blocker 的完整 typed wrapper')
    action = document.get('action')
    if (not isinstance(action, dict) or action.get('kind') != 'approval'
            or action.get('surface') == 'stage2_final_review'
            or not isinstance(action.get('payload'), dict)
            or not action['payload'].get('review_token')):
        _refuse('原回答不是有 token 的普通 approval')
    seat_path = reviewer_seat_ledger.ledger_path(
        base, sid, waiting_on=ledger.waiting_on,
        business_sha256=ledger.business_sha256)
    return RecoveredReviewerResponse(document, ReviewerProtocolRecoveryProof(
        session_run_id=turn.session_run_id, turn_id=turn.turn_id,
        attempt_id=attempt.attempt_id, provider_session_id=attempt.provider_session_id,
        parent_sha256=_canonical(parent.model_dump(mode='json')),
        seat_sha256=_digest(_plain_bytes(base, seat_path)),
        session_sha256=_canonical([event.to_dict() for event in session_events]),
        cost_sha256=_canonical(cost), log_sha256=_digest(raw),
        stdout_sha256=_digest(stdout.encode('utf-8')),
        raw_response_sha256=_digest(response.encode('utf-8')),
        response_sha256=_canonical(document), validation_code_version=validation_code), response)


def validate_record(record: ReviewerActionRecord, recovered: RecoveredReviewerResponse) -> None:
    """durable record 再消费时重验原来源及原判断，不能只信它自报已恢复。"""
    if record.protocol_recovery != recovered.proof:
        _refuse('恢复 proof 与当前原始来源字节不一致')
    document = {
        'schema': reviewer_tasks.REQUEST_SCHEMA, 'waiting_on': record.waiting_on,
        'surface_seal': record.surface_seal,
        'action': {'kind': 'approval', 'surface': record.surface, 'payload': record.payload},
    }
    if _canonical(document) != recovered.proof.response_sha256:
        _refuse('durable 决定改变了原模型判断')


def archive_record(base: str, sid: str, record: ReviewerActionRecord) -> Path:
    """先保存永久内容地址副本；活动 action 退休后仍可审计此次机械复验。"""
    if record.sid != sid or record.protocol_recovery is None:
        _refuse('审计归档缺同书协议恢复 proof')
    root = paths.reviewer_protocol_recoveries_dir(base, sid).absolute()
    service = paths.service_dir(base).absolute()
    for part in (root, *root.parents):
        if part.is_symlink():
            _refuse('审计目录含 symlink')
        if part == service:
            break
    root.mkdir(parents=True, exist_ok=True)
    raw = (record.model_dump_json() + '\n').encode('utf-8')
    target = root / f'reviewer-action-{_digest(raw)}.json'
    if target.exists() or target.is_symlink():
        if _plain_bytes(base, target) != raw:
            _refuse('审计内容地址已有不同字节')
        return target
    temporary = target.with_name(f'.{target.name}.{uuid.uuid4().hex}.tmp')
    try:
        temporary.write_bytes(raw)
        os.replace(temporary, target)
    finally:
        temporary.unlink(missing_ok=True)
    return target
