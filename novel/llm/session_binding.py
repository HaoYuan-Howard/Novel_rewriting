#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Executor session 协议常量与 checkpoint↔session 账绑定。

生产 journal / stage 恢复只经本模块；live SessionAgentClient 仍可 re-export。
"""
from __future__ import annotations

import hashlib
import json
from collections.abc import Mapping, Sequence
from typing import Any

from contracts.schema.orchestration import AgentProfile
from novel.errors import AgentModelIdentityRefused, ProvenanceRefused
from novel.llm import session_audit
from novel.llm.model_identity import resolve_agent_model_identity

SESSION_PROTOCOL_VERSION = 'node-executor/v1'

# 这些失败没有形成一份可交付模型响应；同 run 的 checkpoint 仍可在外部状态
# 恢复后继续消费。未知 CLI/protocol 错误不在这里，避免把代码 bug 冒充网络波动。
SAME_RUN_RESUMABLE_AGENT_FAILURES = frozenset({
    'authentication', 'connection', 'provider_transient', 'rate_limit',
    'server_error', 'timeout', 'usage_limit',
})

def _sha256_bytes(payload: bytes) -> str:
    return hashlib.sha256(payload).hexdigest()


def _canonical_sha256(value: object) -> str:
    return _sha256_bytes(json.dumps(
        value, ensure_ascii=False, sort_keys=True,
        separators=(',', ':'), allow_nan=False,
    ).encode('utf-8'))


def agent_request_fingerprint(
    *,
    profile: AgentProfile | Mapping[str, object],
    subject_sha256: str,
    provider_session_id: str | None,
    system_sha256: str | None,
    prompt_sha256: str,
    schema_sha256: str,
    purpose: str,
    turn_index: int,
) -> str:
    """agent executor 每一轮真实请求指纹的唯一公式。"""
    profile_payload = (
        profile.model_dump() if isinstance(profile, AgentProfile)
        else dict(profile)
    )
    return _canonical_sha256({
        'protocol_version': SESSION_PROTOCOL_VERSION,
        'profile': profile_payload,
        'subject_sha256': subject_sha256,
        'provider_session_id': provider_session_id,
        'system_sha256': system_sha256,
        'prompt_sha256': prompt_sha256,
        'schema_sha256': schema_sha256,
        'purpose': purpose,
        'turn_index': turn_index,
    })


def validate_agent_session_request_binding(
    events: Sequence[session_audit.AgentSessionEvent],
    acceptance: Mapping[str, object],
    *,
    purpose: str,
    profile: Mapping[str, object],
    original_prompt_sha256: str,
    original_schema_sha256: str,
) -> session_audit.AgentSessionRecord:
    """把 checkpoint 当前 session 的 prompt/schema/turn 绑回成本账。

    正常首发的第 1 轮必须等于冻结原请求；只有新 session
    继承了旧 checkpoint 轮次时，它的首轮才可是 recovery envelope。
    无论哪一支，当前 session 每个 turn 都必须用生产同一公式复算
    ledger request fingerprint；因而同时改 started/turn/acceptance SHA 也
    不能自洽过闸。
    """
    session_run_id = acceptance.get('session_run_id')
    matches = [
        record for record in session_audit.fold(events)
        if record.session_run_id == session_run_id
    ]
    if len(matches) != 1 or not matches[0].turns:
        raise ProvenanceRefused(
            f'{purpose}: accepted logical session 缺唯一可折叠 turn 链')
    record = matches[0]
    all_turns = list(record.turns)
    accepted_index = acceptance.get('accepted_turn_index')
    if not isinstance(accepted_index, int) or isinstance(accepted_index, bool):
        raise ProvenanceRefused(f'{purpose}: accepted turn_index 不合法')
    # acceptance 可合法指向较早的可物化候选；其后 malformed
    # patch/parse/schema 失败的 turns 仍留在成本与 session 账，但不得被
    # 硬塞进该 acceptance 的 attempt_links。
    turns = [
        turn for turn in all_turns
        if isinstance(turn.turn_index, int) and turn.turn_index <= accepted_index
    ]
    if not turns:
        raise ProvenanceRefused(
            f'{purpose}: accepted turn 早于 current session 首轮')
    indices = [turn.turn_index for turn in turns]
    if any(not isinstance(index, int) or isinstance(index, bool) or index < 1
           for index in indices):
        raise ProvenanceRefused(f'{purpose}: current session turn_index 不合法')
    numeric_indices = [int(index) for index in indices]
    first_index = numeric_indices[0]
    accepted_attempt = acceptance.get('attempt_id')
    if (numeric_indices != list(range(first_index, accepted_index + 1))
            or turns[-1].attempt_id != accepted_attempt):
        raise ProvenanceRefused(
            f'{purpose}: current session turns 与 accepted turn 不连续')

    # trailing rejected turn 不属于该 acceptance，但仍在同一 logical
    # subject/provider session 内；schema 因此对整条 session 恒定。
    if any(turn.schema_sha256 != original_schema_sha256 for turn in all_turns):
        raise ProvenanceRefused(
            f'{purpose}: current session response schema 与冻结请求漂移')
    if (first_index == 1
            and turns[0].prompt_sha256 != original_prompt_sha256):
        raise ProvenanceRefused(
            f'{purpose}: 正常首发 stdin 与冻结原请求漂移')

    links = acceptance.get('attempt_links')
    recovery = acceptance.get('recovery_from_attempts')
    if (not isinstance(links, list) or not isinstance(recovery, list)
            or any(not isinstance(link, dict) for link in links)
            or any(not isinstance(item, str) for item in recovery)):
        raise ProvenanceRefused(
            f'{purpose}: checkpoint 恢复链缺 attempt links')
    link_ids = [link.get('attempt_id') for link in links]
    current_ids = [turn.attempt_id for turn in turns]
    if (any(not isinstance(item, str) or not item for item in link_ids)
            or any(not isinstance(item, str) or not item for item in current_ids)
            or len(link_ids) != len(set(link_ids))
            or not set(current_ids).issubset(set(link_ids))):
        raise ProvenanceRefused(
            f'{purpose}: turn/recovery/attempt_links 与 current session 不闭合')
    linked_turns = [
        event for event in events
        if event.kind == 'turn' and event.attempt_id in set(link_ids)
    ]
    linked_turn_ids = [event.attempt_id for event in linked_turns]
    if (len(linked_turns) != len(link_ids)
            or set(linked_turn_ids) != set(link_ids)):
        raise ProvenanceRefused(
            f'{purpose}: attempt_links 没有逐项联到唯一 session turn')
    for linked_turn in linked_turns:
        if linked_turn.requested_model != profile.get('model'):
            raise ProvenanceRefused(
                f'{purpose}: session turn requested model 与 resolved profile 漂移')
        try:
            identity = resolve_agent_model_identity(
                profile, linked_turn.actual_model,
                cli_version=linked_turn.cli_version,
                require_driver_version=True,
            )
        except AgentModelIdentityRefused as exc:
            raise ProvenanceRefused(
                f'{purpose}: session turn 模型证据不成立：{exc}') from exc
        if linked_turn.attempt_id == accepted_attempt \
                and acceptance.get('model') != identity.model:
            raise ProvenanceRefused(
                f'{purpose}: checkpoint adopted model 与 accepted session turn 不闭合')
    prior_ids = set(link_ids) - set(current_ids)
    if len(prior_ids) != first_index - 1:
        raise ProvenanceRefused(
            f'{purpose}: checkpoint 历史 attempts 与新 session turn offset 不闭合')
    if first_index > 1:
        if (not prior_ids or not prior_ids.issubset(set(recovery))
                or acceptance.get('request_mode') != 'agent_session_resume'):
            raise ProvenanceRefused(
                f'{purpose}: 非首轮 session 缺 dirty-checkpoint 恢复证据')
        audit_prior_ids = {
            event.attempt_id for event in events
            if (event.kind == 'turn'
                and event.session_run_id != session_run_id)
        }
        if not prior_ids.issubset(audit_prior_ids):
            raise ProvenanceRefused(
                f'{purpose}: dirty-checkpoint prior attempts 缺历史 session turn')
    elif prior_ids:
        raise ProvenanceRefused(f'{purpose}: 正常首发不应携带 prior session')

    binding = acceptance.get('accepted_request')
    system_sha256 = (
        binding.get('system_sha256') if isinstance(binding, dict) else None)
    provider_session_id = acceptance.get('provider_session_id')
    subject_sha256 = acceptance.get('subject_sha256')
    if (not isinstance(system_sha256, str)
            or not isinstance(provider_session_id, str)
            or not isinstance(subject_sha256, str)):
        raise ProvenanceRefused(
            f'{purpose}: checkpoint 缺 fingerprint 所需 session/subject 身份')
    link_by_id = {str(link['attempt_id']): link for link in links}
    for position, turn in enumerate(turns):
        assert turn.turn_index is not None and turn.attempt_id is not None
        assert turn.prompt_sha256 is not None and turn.schema_sha256 is not None
        expected_fingerprint = agent_request_fingerprint(
            profile=profile,
            subject_sha256=subject_sha256,
            provider_session_id=(None if position == 0 else provider_session_id),
            system_sha256=(system_sha256 if position == 0 else None),
            prompt_sha256=turn.prompt_sha256,
            schema_sha256=turn.schema_sha256,
            purpose=purpose,
            turn_index=turn.turn_index,
        )
        # restore 时 CostLedger 尚未 claim prior attempts；先与 hash-bound
        # checkpoint link 对账，调用方随后会 claim 并把每条 link 与盘上
        # ledger 精确比较，两步合起来仍是真实成本账绑定。
        linked_identity = link_by_id[turn.attempt_id]
        if linked_identity.get('request_fingerprint') != expected_fingerprint:
            raise ProvenanceRefused(
                f'{purpose}: session turn 与 ledger request fingerprint 不闭合')
    return record



__all__ = [
    'SESSION_PROTOCOL_VERSION', 'SAME_RUN_RESUMABLE_AGENT_FAILURES',
    'agent_request_fingerprint', 'validate_agent_session_request_binding',
]
