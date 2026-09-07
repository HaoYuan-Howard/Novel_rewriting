#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""复验已完成却被旧 Codex 工具守卫误拒的传输切换响应。

只读取本服务保留的协议日志，不读取 CLI 私有 rollout，不调用模型、不改旧账。
这不是一般的失败重试：唯一允许的旧拒因是 error item 误判，且当前驱动必须
重新证明单次已完成、无工具的响应。调用方还须持 source owner-stopped lease，
按当前业务/token 走 typed loader，先 durable 再交 canonical approval。
"""
from __future__ import annotations

import hashlib
import json
import os
import uuid
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from contracts.schema.orchestration import (
    AgentProfile, AgentRunRecord, ReviewerActionRecord,
    ReviewerProtocolRecoveryProof, ReviewerSeatLedger,
)
from novel import agent_session_audit, paths
from novel.agents.base import decode_wire_json
from novel.commanders.codex import CodexCommanderDriver
from novel.llm.ledger import ledger_path as cost_ledger_path, read_jsonl_rows
from novel.orchestrator import reviewer_seat_ledger, reviewer_tasks


_LEGACY_REJECTION = (
    'novel.commanders.base.CommanderProtocolError: '
    'Codex structured reviewer 触发了禁止的工具/未知 item：error')


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


def inspect_response(
    base: str, sid: str, *, ledger: ReviewerSeatLedger,
    parent: AgentRunRecord, log_path: Path, profile: AgentProfile,
    validation_code: str,
) -> RecoveredReviewerResponse | None:
    """已知失败入口返回可复验原回答；无此类失败返回 None，证据矛盾则关闭。"""
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
