# -*- coding: utf-8 -*-
"""单节点、单 logical session executor 的 provider-neutral 恢复核心。

业务模块只声明 ``SingleSessionRunSpec``（node/purpose/policy/路径）与可选的
``CommitBundleSpec``（正式主产物及允许触碰的路径集合）。本模块统一完成：

* run 对 input/prompt/schema/resolved profile/code 的不可变绑定；
* 精确 20-key ``node-executor/v1`` acceptance、stdout、ledger、session audit 闭环；
* 验证后 checkpoint 的 append-only revision 与零 wire 恢复；
* 主产物 + 确定性派生 bundle 的 baseline-bound ``prepared -> committed`` journal。

它不认识 Stage4/5/6/7 的业务 schema、HARD、正式落点或恢复决策。正式 artifact
始终由各 stage producer 经 ``ArtifactStore`` 写；业务 adapter 也必须自己提供跨 run
所有权/foreign reservation，不能把本模块的单 journal 文件锁误当作业务提交锁。
"""
from __future__ import annotations

import hashlib
import json
import os
import re
import tempfile
import threading
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Generic, TypeVar

from pydantic import BaseModel, ValidationError

from contracts.schema.orchestration import AgentProfile
from novel import agent_session_audit, prov
from novel.agent_capabilities import validate_profile
from novel.agents.session_client import SESSION_PROTOCOL_VERSION
from novel.appendlog import process_append_lock
from novel.errors import ArtifactUnreadable, ProvenanceRefused, UpstreamMissing
from novel.llm.json_text import normalize_json_text
from novel.llm.prompts import required_top_level
from novel.llm.transport import ChatResponse
from novel.run_identity import check_run_id


M = TypeVar('M', bound=BaseModel)
VERSION = 1
RUN_FILE = 'run.json'
COMMIT_FILE = 'committed.json'

_LOCK = threading.RLock()

_AGENT_POLICY_FIELDS = {
    'protocol_version', 'profile', 'session_isolation',
    'recovery_policy', 'phases',
}
_AGENT_PHASE_FIELDS = {
    'phase', 'response_model', 'response_schema_sha256',
    'requested_output_token_ceiling',
    'prefer_content_over_reasoning_salvage',
}
AGENT_ACCEPTANCE_FIELDS = frozenset({
    'acceptance_protocol', 'attempt_id', 'request_fingerprint', 'model',
    'ledger_response_sha256', 'accepted_channel', 'accepted_text',
    'normalized_text', 'attempt_links', 'profile', 'subject_sha256',
    'session_run_id', 'provider_session_id', 'accepted_turn_index',
    'accepted_prompt_sha256', 'accepted_schema_sha256', 'accepted_request',
    'request_mode', 'recovery_from_attempts', 'salvaged',
})
_AGENT_REQUEST_FIELDS = {
    'node', 'chapter', 'purpose', 'system_sha256', 'user_sha256',
    'schema_sha256', 'requested_model', 'requested_effort',
    'requested_output_token_ceiling', 'protocol_version',
}
_LEDGER_IDENTITY_FIELDS = {
    'attempt_id', 'request_fingerprint', 'model', 'request_model',
    'reasoning_effort', 'thinking', 'json_mode', 'max_tokens',
    'response_sha256',
}
_REQUEST_FIELDS = {
    'purpose', 'system_sha256', 'user_sha256', 'initial_prompt_sha256',
    'response_schema_sha256',
}
_RUN_FIELDS = {
    'version', 'run_id', 'sid', 'node', 'session_owner', 'chapter',
    'purpose', 'code', 'input_sha256',
    'request_policy', 'request_policy_sha256', 'request',
    'identity_sha256', 'created_at',
}
_CHECKPOINT_EXTRA_FIELDS = {
    'version', 'created_at', 'run_id', 'sid', 'node', 'code',
    'session_owner', 'chapter', 'run_sha256', 'purpose', 'input_sha256',
    'revision', 'revision_key', 'previous_checkpoint_sha256',
    'state_payload', 'state_payload_sha256', 'response_model',
    'parsed_payload', 'parsed_payload_sha256', 'response_payload',
    'response_payload_sha256', 'checkpoint_sha256',
}
_COMMIT_FIELDS = {
    'version', 'state', 'prepared_at', 'committed_at', 'run_id', 'sid',
    'node', 'code', 'run_sha256', 'primary_artifact',
    'primary_payload_sha256', 'primary_payload_fingerprint',
    'finalization_sha256', 'touched_paths', 'baselines', 'purpose',
    'attempt_id', 'request_identity', 'landed_payload_sha256s',
    'post_file_sha256s', 'commit_sha256',
}


@dataclass(frozen=True)
class SingleSessionRunSpec:
    """一个业务 owner 唯一 logical request 的冻结声明。

    ``purpose`` / ``chapter`` / ``session_owner`` 由业务 adapter 在构造
    spec 时一次性冻结，不是 checkpoint 读出后再猜。因此同一
    node 下的逐章 executor 可各有 purpose/session，不会串章。
    """

    label: str
    node: str
    session_owner: str
    purpose: str
    chapter: int | None
    phase: str
    recovery_policy: str
    session_isolation: str
    run_path: Callable[[Any], Path]
    checkpoint_dir: Callable[[Any], Path]
    incompatible_session_nodes: tuple[str, ...] = ()


@dataclass(frozen=True)
class CommitBundleSpec:
    """一份付费主产物及其确定性派生 bundle 的 journal 声明。"""

    run: SingleSessionRunSpec
    commit_path: Callable[[Any], Path]
    primary_artifact: str
    allowed_touched: tuple[tuple[str, ...], ...]
    nonformal_paths: tuple[str, ...] = ()


@dataclass(frozen=True)
class RestoredCheckpoint(Generic[M]):
    """已完整复验的 latest checkpoint revision。

    ``state_payload`` 由业务 producer 解释；core 只保证它是不可变、
    hash-bound 的 JSON object。恢复不代表 provider session 仍在线，后续
    候选必须由调用方新建 session，并从 ``accepted_turn_index`` 继续扣预算。
    """

    value: M
    acceptance: dict[str, Any]
    state_payload: dict[str, Any]
    revision: int
    revision_key: str
    checkpoint_sha256: str


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec='seconds')


def canonical_bytes(value: object) -> bytes:
    try:
        return json.dumps(
            value, ensure_ascii=False, sort_keys=True, separators=(',', ':'),
            allow_nan=False,
        ).encode('utf-8')
    except (TypeError, ValueError) as exc:
        raise ProvenanceRefused(
            f'agent run 状态不能 canonical JSON 化：{exc}') from exc


def canonical_sha256(value: object) -> str:
    payload = value if isinstance(value, bytes) else canonical_bytes(value)
    return hashlib.sha256(payload).hexdigest()


def is_sha256(value: object) -> bool:
    return (isinstance(value, str) and len(value) == 64
            and all(char in '0123456789abcdef' for char in value))


def _is_projection_fingerprint(value: object) -> bool:
    """``prov.projection_fingerprint`` 的现役 10 位十六进制形状。"""
    return (isinstance(value, str) and len(value) == 10
            and all(char in '0123456789abcdef' for char in value))


def _is_attempt_id(value: object) -> bool:
    return (isinstance(value, str) and len(value) == 16
            and all(char in '0123456789abcdef' for char in value))


def _positive_ceiling(value: int) -> int:
    if not isinstance(value, int) or isinstance(value, bool) or value <= 0:
        raise ProvenanceRefused('agent requested output token ceiling 必须是正整数')
    return value


def _validate_spec(ctx: Any, spec: SingleSessionRunSpec) -> None:
    values = {
        'label': spec.label, 'node': spec.node,
        'session_owner': spec.session_owner, 'purpose': spec.purpose,
        'phase': spec.phase, 'recovery_policy': spec.recovery_policy,
        'session_isolation': spec.session_isolation,
    }
    if any(
            not isinstance(value, str) or not value or len(value) > 160
            or any(ord(char) < 32 for char in value)
            for value in values.values()):
        raise ProvenanceRefused(f'agent run spec 含空身份：{values}')
    if ctx.node != spec.node:
        raise ProvenanceRefused(
            f'{spec.label} 只属于 node={spec.node!r}，不能由 {ctx.node!r} 使用')
    chapter = spec.chapter
    if (chapter is not None
            and (not isinstance(chapter, int) or isinstance(chapter, bool)
                 or chapter < 1)):
        raise ProvenanceRefused(
            f'{spec.label}: chapter 必须是 None 或正整数')


def _atomic_json(path: Path, payload: Mapping[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    data = json.dumps(
        dict(payload), ensure_ascii=False, indent=2, sort_keys=True,
        allow_nan=False,
    ).encode('utf-8') + b'\n'
    fd, temp_name = tempfile.mkstemp(
        prefix=f'.{path.name}.', suffix='.tmp', dir=str(path.parent))
    try:
        with os.fdopen(fd, 'wb') as handle:
            handle.write(data)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temp_name, path)
    except BaseException:
        try:
            os.unlink(temp_name)
        except FileNotFoundError:
            pass
        raise


def _read_json(path: Path, *, label: str) -> dict[str, Any]:
    try:
        raw = path.read_bytes()
        value = json.loads(raw.decode('utf-8'))
    except FileNotFoundError:
        raise UpstreamMissing(f'{path} 不在盘上') from None
    except (OSError, UnicodeError, json.JSONDecodeError) as exc:
        raise ArtifactUnreadable(f'{label} {path} 读不出来：{exc}') from exc
    if not isinstance(value, dict) or value.get('version') != VERSION:
        raise ArtifactUnreadable(
            f'{label} {path} 不是当前内部 v{VERSION} 对象；不猜旧形状')
    return value


def _resolved_profile(ctx: Any, value: object, *, label: str) -> dict[str, Any]:
    if not isinstance(value, dict):
        raise ProvenanceRefused(f'{label} 缺 resolved agent profile 对象')
    try:
        resolved = validate_profile(
            AgentProfile.model_validate(value), role='executor')
    except (ValidationError, ValueError) as exc:
        raise ProvenanceRefused(f'{label} 的 agent profile 不可执行：{exc}') from exc
    normalized = resolved.model_dump(mode='json')
    if value != normalized:
        raise ProvenanceRefused(
            f'{label} 的 agent profile 尚未物化 execution_mode，不能冻结')
    current = getattr(ctx, 'executor_profile', None)
    if current is None:
        raise ProvenanceRefused(
            f'{label} 声明 agent 协议，但当前 RunContext 没有 executor profile')
    try:
        current_resolved = validate_profile(current, role='executor')
    except (ValidationError, ValueError) as exc:
        raise ProvenanceRefused(f'当前 executor profile 不可执行：{exc}') from exc
    if current_resolved.model_dump(mode='json') != normalized:
        raise ProvenanceRefused(f'{label} 的 resolved profile 与当前配置漂移')
    return normalized


def _expected_policy(
        ctx: Any, spec: SingleSessionRunSpec,
        response_model: type[BaseModel], max_tokens: int,
) -> dict[str, Any]:
    _validate_spec(ctx, spec)
    current = getattr(ctx, 'executor_profile', None)
    if current is None:
        raise ProvenanceRefused(f'{spec.label} 缺 executor profile')
    try:
        resolved = validate_profile(current, role='executor')
    except (ValidationError, ValueError) as exc:
        raise ProvenanceRefused(f'{spec.label} executor profile 不可执行：{exc}') from exc
    return {
        'protocol_version': SESSION_PROTOCOL_VERSION,
        'profile': resolved.model_dump(mode='json'),
        'session_isolation': spec.session_isolation,
        'recovery_policy': spec.recovery_policy,
        'phases': [{
            'phase': spec.phase,
            'response_model': response_model.__name__,
            'response_schema_sha256': canonical_sha256(
                response_model.model_json_schema(by_alias=True)),
            'requested_output_token_ceiling': _positive_ceiling(max_tokens),
            'prefer_content_over_reasoning_salvage': False,
        }],
    }


def _validate_policy(
        ctx: Any, spec: SingleSessionRunSpec, value: object, *,
        response_model: type[BaseModel] | None = None,
        max_tokens: int | None = None,
) -> dict[str, Any]:
    if (not isinstance(value, dict) or set(value) != _AGENT_POLICY_FIELDS
            or value.get('protocol_version') != SESSION_PROTOCOL_VERSION
            or value.get('session_isolation') != spec.session_isolation
            or value.get('recovery_policy') != spec.recovery_policy):
        raise ProvenanceRefused(f'{spec.label} request policy discriminator 漂移')
    _resolved_profile(ctx, value.get('profile'), label=f'{spec.label} request policy')
    phases = value.get('phases')
    if not isinstance(phases, list) or len(phases) != 1:
        raise ProvenanceRefused(f'{spec.label} request policy 必须恰有一相')
    row = phases[0]
    if (not isinstance(row, dict) or set(row) != _AGENT_PHASE_FIELDS
            or row.get('phase') != spec.phase
            or not isinstance(row.get('response_model'), str)
            or not row['response_model']
            or not is_sha256(row.get('response_schema_sha256'))
            or not isinstance(row.get('requested_output_token_ceiling'), int)
            or isinstance(row.get('requested_output_token_ceiling'), bool)
            or row['requested_output_token_ceiling'] <= 0
            or row.get('prefer_content_over_reasoning_salvage') is not False):
        raise ProvenanceRefused(f'{spec.label} policy phase 身份或 ceiling 不合法')
    normalized = json.loads(canonical_bytes(value))
    if response_model is not None:
        if max_tokens is None:
            raise ProvenanceRefused(f'{spec.label} 当前 response model 缺 ceiling')
        if normalized != _expected_policy(ctx, spec, response_model, max_tokens):
            raise ProvenanceRefused(
                f'{spec.label} policy 的 profile/schema/ceiling 与当前请求漂移')
    return normalized


def _request_identity(
        spec: SingleSessionRunSpec, system: str, user: str,
        response_model: type[BaseModel],
) -> dict[str, Any]:
    if not isinstance(system, str) or not isinstance(user, str):
        raise ProvenanceRefused(f'{spec.label} system/user 必须是字符串')
    return {
        'purpose': spec.purpose,
        'system_sha256': canonical_sha256(system.encode('utf-8')),
        'user_sha256': canonical_sha256(user.encode('utf-8')),
        'initial_prompt_sha256': canonical_sha256(
            f'{system}\n\n--- 本轮任务 ---\n\n{user}'.encode('utf-8')),
        'response_schema_sha256': canonical_sha256(
            response_model.model_json_schema(by_alias=True)),
    }


def _expected_run_identity(
        ctx: Any, spec: SingleSessionRunSpec, *, input_sha256: str,
        system: str, user: str, response_model: type[BaseModel], max_tokens: int,
) -> dict[str, Any]:
    _validate_spec(ctx, spec)
    if not is_sha256(input_sha256):
        raise ProvenanceRefused(f'{spec.label} input_sha256 必须是完整小写 SHA-256')
    policy = _expected_policy(ctx, spec, response_model, max_tokens)
    return {
        'version': VERSION, 'run_id': check_run_id(ctx.run_id),
        'sid': ctx.sid, 'node': ctx.node,
        'session_owner': spec.session_owner, 'chapter': spec.chapter,
        'purpose': spec.purpose, 'code': ctx.code,
        'input_sha256': input_sha256,
        'request_policy': policy,
        'request_policy_sha256': canonical_sha256(policy),
        'request': _request_identity(spec, system, user, response_model),
    }


def _validate_run_record(
        ctx: Any, spec: SingleSessionRunSpec, record: Mapping[str, Any], *,
        response_model: type[BaseModel] | None = None,
        max_tokens: int | None = None,
) -> dict[str, Any]:
    _validate_spec(ctx, spec)
    if set(record) != _RUN_FIELDS:
        raise ArtifactUnreadable(f'{spec.label} run.json 字段漂移')
    identity = {
        key: record[key] for key in _RUN_FIELDS
        if key not in ('identity_sha256', 'created_at')
    }
    if record.get('identity_sha256') != canonical_sha256(identity):
        raise ArtifactUnreadable(f'{spec.label} run.json identity SHA 不闭合')
    expected = {
        'version': VERSION, 'run_id': check_run_id(ctx.run_id),
        'sid': ctx.sid, 'node': spec.node,
        'session_owner': spec.session_owner, 'chapter': spec.chapter,
        'purpose': spec.purpose, 'code': ctx.code,
    }
    if {key: record.get(key) for key in expected} != expected:
        raise ProvenanceRefused(
            f'{spec.label} run.json 不属于当前 run/sid/node/code')
    if not is_sha256(record.get('input_sha256')):
        raise ArtifactUnreadable(f'{spec.label} run.json 缺合法 input SHA')
    policy = _validate_policy(
        ctx, spec, record.get('request_policy'), response_model=response_model,
        max_tokens=max_tokens)
    if record.get('request_policy_sha256') != canonical_sha256(policy):
        raise ArtifactUnreadable(f'{spec.label} request policy SHA 不闭合')
    request = record.get('request')
    if (not isinstance(request, dict) or set(request) != _REQUEST_FIELDS
            or request.get('purpose') != spec.purpose
            or any(not is_sha256(request.get(field)) for field in (
                'system_sha256', 'user_sha256', 'initial_prompt_sha256',
                'response_schema_sha256'))):
        raise ArtifactUnreadable(f'{spec.label} request identity 不闭合')
    return dict(record)


def ensure_run(
        ctx: Any, spec: SingleSessionRunSpec, *, input_sha256: str,
        system: str, user: str, response_model: type[BaseModel], max_tokens: int,
) -> dict[str, Any]:
    """创建或逐字段核验不可变 single-session run。"""
    identity = _expected_run_identity(
        ctx, spec, input_sha256=input_sha256, system=system, user=user,
        response_model=response_model, max_tokens=max_tokens)
    target = spec.run_path(ctx) / RUN_FILE
    with _LOCK:
        with process_append_lock(target):
            if target.is_file():
                current = _validate_run_record(
                    ctx, spec, _read_json(target, label=spec.label),
                    response_model=response_model, max_tokens=max_tokens)
                if {key: current.get(key) for key in identity} != identity:
                    raise ProvenanceRefused(
                        f'{spec.label} run={ctx.run_id} 输入/prompt/schema/profile 漂移；'
                        '拒绝恢复或重新付费')
                return current
            payload = {
                **identity, 'identity_sha256': canonical_sha256(identity),
                'created_at': _now(),
            }
            _atomic_json(target, payload)
            return payload


def _validate_request_against_run(
        ctx: Any, spec: SingleSessionRunSpec, *, input_sha256: str,
        system: str, user: str, response_model: type[BaseModel], max_tokens: int,
) -> tuple[dict[str, Any], dict[str, Any]]:
    run_path = spec.run_path(ctx) / RUN_FILE
    run = _validate_run_record(
        ctx, spec, _read_json(run_path, label=spec.label),
        response_model=response_model, max_tokens=max_tokens)
    if run.get('input_sha256') != input_sha256 or not is_sha256(input_sha256):
        raise ProvenanceRefused(f'{spec.label} 当前输入与 run.json 漂移')
    expected = _request_identity(spec, system, user, response_model)
    if run['request'] != expected:
        raise ProvenanceRefused(f'{spec.label} 当前 prompt/schema 与 run.json 漂移')
    return run, dict(expected)


def _validate_session_turn(
        ctx: Any, spec: SingleSessionRunSpec, *,
        acceptance: Mapping[str, Any], profile: Mapping[str, Any],
        system: str, user: str,
) -> None:
    try:
        events = agent_session_audit.read_events(ctx.novel_dir.parent)
        agent_session_audit.fold(events)
    except (OSError, UnicodeError, ValueError) as exc:
        raise ProvenanceRefused(
            f'{spec.label}: agent session 审计不可验证：{exc}') from exc
    attempt_id = acceptance.get('attempt_id')
    turns = [
        event for event in events
        if event.kind == 'turn' and event.attempt_id == attempt_id
    ]
    if len(turns) != 1:
        raise ProvenanceRefused(
            f'{spec.label}: accepted attempt 必须唯一联结一条 session turn')
    turn = turns[0]
    starts = [
        event for event in events
        if (event.kind == 'started'
            and event.session_run_id == acceptance.get('session_run_id'))
    ]
    if len(starts) != 1:
        raise ProvenanceRefused(f'{spec.label}: logical session 缺唯一 started')
    start = starts[0]
    start_profile = (
        None if start.profile is None else start.profile.model_dump(mode='json'))
    turn_profile = (
        None if turn.profile is None else turn.profile.model_dump(mode='json'))
    initial_prompt_sha = canonical_sha256(
        f'{system}\n\n--- 本轮任务 ---\n\n{user}'.encode('utf-8'))
    expected_start = {
        'wire_version': agent_session_audit.SESSION_AUDIT_WIRE_VERSION,
        'session_run_id': acceptance.get('session_run_id'),
        'parent_run_id': check_run_id(ctx.run_id),
        'role': 'executor', 'node': spec.node, 'chapter': spec.chapter,
        'subject_sha256': acceptance.get('subject_sha256'),
        'profile': dict(profile), 'provider_session_id': None,
        'requested_model': profile.get('model'), 'actual_model': None,
        'requested_effort': profile.get('reasoning_effort'), 'actual_effort': None,
        'protocol_version': SESSION_PROTOCOL_VERSION,
        'start_code_version': ctx.code,
        'prompt_sha256': initial_prompt_sha,
        'schema_sha256': acceptance.get('accepted_schema_sha256'),
    }
    actual_start = {
        'wire_version': start.wire_version,
        'session_run_id': start.session_run_id,
        'parent_run_id': start.parent_run_id,
        'role': start.role, 'node': start.node, 'chapter': start.chapter,
        'subject_sha256': start.subject_sha256, 'profile': start_profile,
        'provider_session_id': start.provider_session_id,
        'requested_model': start.requested_model, 'actual_model': start.actual_model,
        'requested_effort': start.requested_effort, 'actual_effort': start.actual_effort,
        'protocol_version': start.protocol_version,
        'start_code_version': start.start_code_version,
        'prompt_sha256': start.prompt_sha256, 'schema_sha256': start.schema_sha256,
    }
    if actual_start != expected_start:
        raise ProvenanceRefused(f'{spec.label}: started 事件与 subject 漂移')
    expected_turn = {
        'wire_version': agent_session_audit.SESSION_AUDIT_WIRE_VERSION,
        'session_run_id': acceptance.get('session_run_id'),
        'parent_run_id': check_run_id(ctx.run_id),
        'role': 'executor', 'node': spec.node, 'chapter': spec.chapter,
        'subject_sha256': acceptance.get('subject_sha256'),
        'profile': dict(profile),
        'provider_session_id': acceptance.get('provider_session_id'),
        'turn_index': acceptance.get('accepted_turn_index'),
        'attempt_id': attempt_id,
        'requested_model': profile.get('model'),
        'actual_model': acceptance.get('model'),
        'requested_effort': profile.get('reasoning_effort'),
        'protocol_version': SESSION_PROTOCOL_VERSION,
        'start_code_version': ctx.code,
        'prompt_sha256': acceptance.get('accepted_prompt_sha256'),
        'schema_sha256': acceptance.get('accepted_schema_sha256'),
    }
    actual_turn = {
        'wire_version': turn.wire_version,
        'session_run_id': turn.session_run_id,
        'parent_run_id': turn.parent_run_id,
        'role': turn.role, 'node': turn.node, 'chapter': turn.chapter,
        'subject_sha256': turn.subject_sha256, 'profile': turn_profile,
        'provider_session_id': turn.provider_session_id,
        'turn_index': turn.turn_index, 'attempt_id': turn.attempt_id,
        'requested_model': turn.requested_model, 'actual_model': turn.actual_model,
        'requested_effort': turn.requested_effort,
        'protocol_version': turn.protocol_version,
        'start_code_version': turn.start_code_version,
        'prompt_sha256': turn.prompt_sha256, 'schema_sha256': turn.schema_sha256,
    }
    if actual_turn != expected_turn:
        raise ProvenanceRefused(f'{spec.label}: checkpoint 与唯一 session turn 漂移')
    if turn.actual_model != profile.get('model'):
        raise ProvenanceRefused(f'{spec.label}: actual model 与 resolved profile 漂移')
    if (turn.actual_effort is not None
            and turn.actual_effort != profile.get('reasoning_effort')):
        raise ProvenanceRefused(f'{spec.label}: actual effort 与 resolved profile 漂移')
    for event in events:
        event_profile = (
            None if event.profile is None else event.profile.model_dump(mode='json'))
        incompatible_owner = (
            event.node in spec.incompatible_session_nodes
            or (spec.chapter is not None
                and event.node == spec.node
                and event.chapter != spec.chapter)
        )
        if (event.kind == 'turn'
                and incompatible_owner
                and event.provider_session_id == acceptance.get('provider_session_id')
                and isinstance(event_profile, dict)
                and event_profile.get('driver') == profile.get('driver')):
            raise ProvenanceRefused(
                f'{spec.label}: 与互斥节点 {event.node} 复用了 provider session')


def _validate_acceptance(
        ctx: Any, spec: SingleSessionRunSpec, *, system: str, user: str,
        response_model: type[M], max_tokens: int,
        response_payload: Mapping[str, Any], acceptance: Mapping[str, Any],
        claim_prior: bool,
) -> dict[str, Any]:
    if (set(acceptance) != AGENT_ACCEPTANCE_FIELDS
            or acceptance.get('acceptance_protocol') != SESSION_PROTOCOL_VERSION):
        raise ProvenanceRefused(
            f'{spec.label}: acceptance 不是精确 20-key node-executor/v1')
    profile = _resolved_profile(
        ctx, acceptance.get('profile'), label=f'{spec.label}: acceptance')
    if _expected_policy(ctx, spec, response_model, max_tokens)['profile'] != profile:
        raise ProvenanceRefused(f'{spec.label}: acceptance profile 与 policy 漂移')
    attempt_id = acceptance.get('attempt_id')
    request_fingerprint = acceptance.get('request_fingerprint')
    model = acceptance.get('model')
    response_sha = acceptance.get('ledger_response_sha256')
    accepted_text = acceptance.get('accepted_text')
    normalized_text = acceptance.get('normalized_text')
    links = acceptance.get('attempt_links')
    recovery = acceptance.get('recovery_from_attempts')
    turn_index = acceptance.get('accepted_turn_index')
    schema_sha = canonical_sha256(response_model.model_json_schema(by_alias=True))
    binding = acceptance.get('accepted_request')
    if (not _is_attempt_id(attempt_id)
            or not is_sha256(request_fingerprint)
            or model != profile['model']
            or not is_sha256(response_sha)
            or acceptance.get('accepted_channel') != 'content'
            or not isinstance(accepted_text, str)
            or not isinstance(normalized_text, str)
            or acceptance.get('salvaged') is not False
            or not isinstance(acceptance.get('session_run_id'), str)
            or not acceptance['session_run_id']
            or not isinstance(acceptance.get('provider_session_id'), str)
            or not acceptance['provider_session_id']
            or not isinstance(turn_index, int) or isinstance(turn_index, bool)
            or turn_index < 1
            or not is_sha256(acceptance.get('accepted_prompt_sha256'))
            or acceptance.get('accepted_schema_sha256') != schema_sha
            or not isinstance(binding, dict) or set(binding) != _AGENT_REQUEST_FIELDS
            or not isinstance(links, list) or not links
            or not isinstance(recovery, list)):
        raise ProvenanceRefused(
            f'{spec.label}: acceptance 缺 stdout/session/request/成本身份')
    expected_binding = {
        'node': spec.node, 'chapter': spec.chapter, 'purpose': spec.purpose,
        'system_sha256': canonical_sha256(system.encode('utf-8')),
        'user_sha256': canonical_sha256(user.encode('utf-8')),
        'schema_sha256': schema_sha,
        'requested_model': profile['model'],
        'requested_effort': profile['reasoning_effort'],
        'requested_output_token_ceiling': _positive_ceiling(max_tokens),
        'protocol_version': SESSION_PROTOCOL_VERSION,
    }
    if binding != expected_binding:
        raise ProvenanceRefused(f'{spec.label}: accepted_request 与当前请求漂移')
    if acceptance.get('subject_sha256') != canonical_sha256(expected_binding):
        raise ProvenanceRefused(f'{spec.label}: logical subject SHA 不闭合')
    _validate_session_turn(
        ctx, spec, acceptance=acceptance, profile=profile,
        system=system, user=user)
    if normalized_text != normalize_json_text(accepted_text):
        raise ProvenanceRefused(f'{spec.label}: normalized stdout 不是唯一归一结果')
    if response_sha != canonical_sha256(accepted_text.encode('utf-8')):
        raise ProvenanceRefused(f'{spec.label}: stdout SHA 与 ledger 不闭合')
    try:
        parsed_payload = json.loads(normalized_text)
    except json.JSONDecodeError as exc:
        raise ProvenanceRefused(f'{spec.label}: stdout 不是 JSON：{exc}') from exc
    if not isinstance(parsed_payload, dict):
        raise ProvenanceRefused(f'{spec.label}: stdout 不是 JSON object')
    missing = [
        key for key in required_top_level(response_model)
        if key not in parsed_payload or parsed_payload[key] in (None, '', [], {})
    ]
    if missing:
        raise ProvenanceRefused(f'{spec.label}: stdout 缺必填/非空键 {missing}')
    try:
        typed = response_model.model_validate(parsed_payload)
        typed_payload = typed.model_dump(by_alias=True)
    except ValidationError as exc:
        raise ProvenanceRefused(
            f'{spec.label}: stdout 不再通过 {response_model.__name__}：{exc}') from exc
    payload = dict(response_payload)
    if typed_payload != payload:
        raise ProvenanceRefused(f'{spec.label}: stdout schema 归一与验证后 payload 漂移')

    identities: list[dict[str, Any]] = []
    attempt_ids: list[str] = []
    fingerprints: list[str] = []
    for link in links:
        if (not isinstance(link, dict) or set(link) != _LEDGER_IDENTITY_FIELDS
                or not _is_attempt_id(link.get('attempt_id'))
                or not is_sha256(link.get('request_fingerprint'))):
            raise ProvenanceRefused(f'{spec.label}: attempt_links 身份不完整')
        aid = str(link['attempt_id'])
        fingerprint = str(link['request_fingerprint'])
        if (link.get('request_model') != profile['model']
                or link.get('reasoning_effort') != profile['reasoning_effort']
                or link.get('thinking') is not None
                or link.get('json_mode') is not True
                or link.get('max_tokens') is not None):
            raise ProvenanceRefused(
                f'{spec.label}: attempt={aid} 混入 legacy 请求字段')
        identities.append(dict(link))
        attempt_ids.append(aid)
        fingerprints.append(fingerprint)
    if len(set(attempt_ids)) != len(attempt_ids) or attempt_id not in attempt_ids:
        raise ProvenanceRefused(
            f'{spec.label}: contributing attempts 不唯一或漏 accepted attempt')
    # CostLedger 的窄面故意按随机 attempt_id 排序，link 顺序不是
    # provider turn 顺序。这里只冻结集合与 accepted 唯一性，不伪造时序。
    identities.sort(key=lambda row: (
        str(row['attempt_id']), str(row['request_fingerprint'])))
    attempt_ids = [str(row['attempt_id']) for row in identities]
    fingerprints = [str(row['request_fingerprint']) for row in identities]
    expected_recovery = [aid for aid in attempt_ids if aid != attempt_id]
    if (recovery != expected_recovery
            or turn_index != len(identities)
            or acceptance.get('request_mode') != (
                'original' if turn_index == 1 else 'agent_session_resume')):
        raise ProvenanceRefused(
            f'{spec.label}: turn/recovery/attempt_links 顺序不闭合')
    if claim_prior:
        ctx.ledger.claim_prior_attempts(
            attempt_ids, sid=ctx.sid, node=spec.node, purpose=spec.purpose,
            request_fingerprints=fingerprints)
    for link in identities:
        aid = str(link['attempt_id'])
        if link != ctx.ledger.request_identity_for_attempt(aid):
            raise ProvenanceRefused(f'{spec.label}: attempt={aid} 与 ledger 漂移')
    accepted_link = next(
        row for row in identities if row['attempt_id'] == attempt_id)
    if (accepted_link.get('request_fingerprint') != request_fingerprint
            or accepted_link.get('model') != model
            or accepted_link.get('response_sha256') != response_sha):
        raise ProvenanceRefused(f'{spec.label}: accepted attempt 与 ledger 响应漂移')
    return {
        'acceptance': {
            **{key: acceptance[key] for key in AGENT_ACCEPTANCE_FIELDS},
            'profile': profile, 'accepted_request': dict(binding),
            'attempt_links': identities,
            'recovery_from_attempts': expected_recovery,
        },
        'parsed_payload': parsed_payload,
        'response_payload': payload,
        'typed': typed,
    }


def _checkpoint_hash(payload: Mapping[str, Any]) -> str:
    return canonical_sha256({
        key: value for key, value in payload.items()
        if key not in ('created_at', 'checkpoint_sha256')
    })


# 写口使用至少四位零填充（rev-0001.json）；读口先提取数字，再由下方
# ``expected_path`` 精确核 canonical 文件名。这里若禁止前导零，会把自己刚写的
# 第一份 checkpoint 当成“未登记 JSON”，导致任何二轮/恢复/commit 都永久失败。
_REVISION_FILE_RX = re.compile(r'rev-([0-9]+)\.json')
_REVISION_KEY_RX = re.compile(r'[a-z][a-z0-9._:-]{0,63}')


def _checkpoint_revision_path(directory: Path, revision: int) -> Path:
    if (not isinstance(revision, int) or isinstance(revision, bool)
            or revision < 1):
        raise ProvenanceRefused('agent checkpoint revision 必须是正整数')
    return directory / f'rev-{revision:04d}.json'


def _checkpoint_lock_path(spec: SingleSessionRunSpec, ctx: Any) -> Path:
    return spec.checkpoint_dir(ctx) / '.revisions'


def _validate_checkpoint_envelope(
        ctx: Any, spec: SingleSessionRunSpec, path: Path,
        record: Mapping[str, Any], *, run_sha: str, revision: int,
        previous_checkpoint_sha256: str | None,
) -> dict[str, Any]:
    if set(record) != _CHECKPOINT_EXTRA_FIELDS | AGENT_ACCEPTANCE_FIELDS:
        raise ArtifactUnreadable(f'{path} checkpoint 字段漂移')
    if record.get('checkpoint_sha256') != _checkpoint_hash(record):
        raise ArtifactUnreadable(f'{path} checkpoint SHA 不闭合')
    expected = {
        'version': VERSION, 'run_id': check_run_id(ctx.run_id),
        'sid': ctx.sid, 'node': spec.node, 'code': ctx.code,
        'session_owner': spec.session_owner, 'chapter': spec.chapter,
        'run_sha256': run_sha, 'purpose': spec.purpose,
        'revision': revision,
        'previous_checkpoint_sha256': previous_checkpoint_sha256,
    }
    if {key: record.get(key) for key in expected} != expected:
        raise ProvenanceRefused(f'{path} 不属于当前 run/sid/node/code/run.json')
    run_path = spec.run_path(ctx) / RUN_FILE
    run = _validate_run_record(
        ctx, spec, _read_json(run_path, label=spec.label))
    if (canonical_sha256(run_path.read_bytes()) != run_sha
            or record.get('input_sha256') != run.get('input_sha256')):
        raise ProvenanceRefused(f'{path} 的 run/input 绑定漂移')
    policy = _validate_policy(ctx, spec, run.get('request_policy'))
    phase = policy['phases'][0]
    profile = _resolved_profile(
        ctx, record.get('profile'), label=f'{path}: acceptance')
    request = run['request']
    expected_binding = {
        'node': spec.node, 'chapter': spec.chapter, 'purpose': spec.purpose,
        'system_sha256': request['system_sha256'],
        'user_sha256': request['user_sha256'],
        'schema_sha256': request['response_schema_sha256'],
        'requested_model': profile['model'],
        'requested_effort': profile['reasoning_effort'],
        'requested_output_token_ceiling': phase['requested_output_token_ceiling'],
        'protocol_version': SESSION_PROTOCOL_VERSION,
    }
    links = record.get('attempt_links')
    recovery = record.get('recovery_from_attempts')
    attempt_id = record.get('attempt_id')
    turn_index = record.get('accepted_turn_index')
    identities: list[dict[str, Any]] = []
    if isinstance(links, list):
        for link in links:
            if (not isinstance(link, dict)
                    or set(link) != _LEDGER_IDENTITY_FIELDS
                    or not _is_attempt_id(link.get('attempt_id'))
                    or not is_sha256(link.get('request_fingerprint'))):
                raise ArtifactUnreadable(
                    f'{path} checkpoint attempt_links 身份不完整')
            identities.append(dict(link))
    attempt_ids = [str(link['attempt_id']) for link in identities]
    normalized_links = sorted(
        identities,
        key=lambda row: (str(row['attempt_id']), str(row['request_fingerprint'])),
    )
    expected_recovery = [
        str(link['attempt_id']) for link in normalized_links
        if link['attempt_id'] != attempt_id
    ]
    if (record.get('acceptance_protocol') != SESSION_PROTOCOL_VERSION
            or record.get('accepted_channel') != 'content'
            or record.get('salvaged') is not False
            or record.get('model') != profile['model']
            or record.get('response_model') != phase['response_model']
            or record.get('accepted_schema_sha256')
            != phase['response_schema_sha256']
            or record.get('accepted_request') != expected_binding
            or record.get('subject_sha256') != canonical_sha256(expected_binding)
            or not _is_attempt_id(attempt_id)
            or not isinstance(record.get('accepted_text'), str)
            or not isinstance(record.get('normalized_text'), str)
            or record.get('normalized_text')
            != normalize_json_text(record['accepted_text'])
            or record.get('ledger_response_sha256')
            != canonical_sha256(record['accepted_text'].encode('utf-8'))
            or not isinstance(record.get('session_run_id'), str)
            or not record['session_run_id']
            or not isinstance(record.get('provider_session_id'), str)
            or not record['provider_session_id']
            or not isinstance(turn_index, int) or isinstance(turn_index, bool)
            or turn_index < 1 or turn_index != len(identities)
            or len(attempt_ids) != len(set(attempt_ids))
            or attempt_ids.count(str(attempt_id)) != 1
            or not isinstance(recovery, list)
            or recovery != expected_recovery
            or record.get('request_mode') != (
                'original' if turn_index == 1 else 'agent_session_resume')
            or not isinstance(record.get('revision_key'), str)
            or _REVISION_KEY_RX.fullmatch(record['revision_key']) is None):
        raise ProvenanceRefused(f'{path} acceptance 与 run policy 漂移')
    for name in ('parsed_payload', 'response_payload', 'state_payload'):
        payload = record.get(name)
        if (not isinstance(payload, dict)
                or record.get(f'{name}_sha256') != canonical_sha256(payload)):
            raise ArtifactUnreadable(f'{path} 的 {name} SHA 不闭合')
    return dict(record)


def _read_checkpoint_chain(
        ctx: Any, spec: SingleSessionRunSpec, *, run_sha: str,
) -> list[tuple[Path, dict[str, Any]]]:
    """读取严格无缺口的 revision 链；多一份陌生 JSON 也拒绝猜。"""
    directory = spec.checkpoint_dir(ctx)
    if not directory.is_dir():
        return []
    numbered: dict[int, Path] = {}
    try:
        children = tuple(directory.iterdir())
    except OSError as exc:
        raise ArtifactUnreadable(
            f'{spec.label} checkpoint 目录不可读：{exc}') from exc
    for child in children:
        if not child.is_file() or child.suffix != '.json':
            continue
        match = _REVISION_FILE_RX.fullmatch(child.name)
        if match is None:
            raise ArtifactUnreadable(
                f'{spec.label} checkpoint 目录含未登记 JSON：{child}')
        revision = int(match.group(1))
        if revision in numbered:
            raise ArtifactUnreadable(
                f'{spec.label} checkpoint revision={revision} 重复')
        numbered[revision] = child
    if not numbered:
        return []
    expected_numbers = list(range(1, max(numbered) + 1))
    if sorted(numbered) != expected_numbers:
        raise ArtifactUnreadable(
            f'{spec.label} checkpoint revision 链有缺口：{sorted(numbered)}')
    records: list[tuple[Path, dict[str, Any]]] = []
    previous: str | None = None
    for revision in expected_numbers:
        path = numbered[revision]
        expected_path = _checkpoint_revision_path(directory, revision)
        if path.name != expected_path.name:
            raise ArtifactUnreadable(
                f'{path} checkpoint revision 文件名不是 canonical 形状')
        record = _validate_checkpoint_envelope(
            ctx, spec, path, _read_json(path, label=spec.label),
            run_sha=run_sha, revision=revision,
            previous_checkpoint_sha256=previous,
        )
        previous = str(record['checkpoint_sha256'])
        records.append((path, record))
    return records


def _latest_checkpoint(
        ctx: Any, spec: SingleSessionRunSpec, *, run_sha: str,
) -> tuple[Path, dict[str, Any]] | None:
    with process_append_lock(_checkpoint_lock_path(spec, ctx)):
        records = _read_checkpoint_chain(ctx, spec, run_sha=run_sha)
    return records[-1] if records else None


def write_checkpoint(
        ctx: Any, spec: SingleSessionRunSpec, *, input_sha256: str,
        system: str, user: str, response_model: type[BaseModel], max_tokens: int,
        response_payload: Mapping[str, Any], acceptance: Mapping[str, Any],
        revision_key: str, state_payload: Mapping[str, Any],
) -> Path:
    """追加一份验证后候选；同 attempt 只允许逐字幂等。"""
    if (not isinstance(revision_key, str)
            or _REVISION_KEY_RX.fullmatch(revision_key) is None):
        raise ProvenanceRefused(
            f'{spec.label}: revision_key 必须是已登记形状，实得 {revision_key!r}')
    if not isinstance(state_payload, Mapping):
        raise ProvenanceRefused(f'{spec.label}: state_payload 必须是 JSON object')
    state = dict(state_payload)
    # 在取锁前就拒绝不能 canonical 的业务状态。
    canonical_bytes(state)
    _validate_request_against_run(
        ctx, spec, input_sha256=input_sha256, system=system, user=user,
        response_model=response_model, max_tokens=max_tokens)
    run_path = spec.run_path(ctx) / RUN_FILE
    run_sha = canonical_sha256(run_path.read_bytes())
    validated = _validate_acceptance(
        ctx, spec, system=system, user=user, response_model=response_model,
        max_tokens=max_tokens, response_payload=response_payload,
        acceptance=acceptance, claim_prior=False)
    with _LOCK:
        with process_append_lock(_checkpoint_lock_path(spec, ctx)):
            records = _read_checkpoint_chain(ctx, spec, run_sha=run_sha)
            latest = records[-1][1] if records else None
            semantic = {
                'run_id': check_run_id(ctx.run_id), 'sid': ctx.sid,
                'node': spec.node, 'code': ctx.code,
                'session_owner': spec.session_owner, 'chapter': spec.chapter,
                'run_sha256': run_sha, 'purpose': spec.purpose,
                'input_sha256': input_sha256, 'revision_key': revision_key,
                'state_payload': state,
                'state_payload_sha256': canonical_sha256(state),
                'response_model': response_model.__name__,
                **validated['acceptance'],
                'parsed_payload': validated['parsed_payload'],
                'parsed_payload_sha256': canonical_sha256(
                    validated['parsed_payload']),
                'response_payload': validated['response_payload'],
                'response_payload_sha256': canonical_sha256(
                    validated['response_payload']),
            }
            if latest is not None and latest.get('attempt_id') == semantic['attempt_id']:
                ignored = {
                    'version', 'created_at', 'revision',
                    'previous_checkpoint_sha256', 'checkpoint_sha256',
                }
                current_semantic = {
                    key: value for key, value in latest.items() if key not in ignored
                }
                if current_semantic != semantic:
                    raise ProvenanceRefused(
                        f'{records[-1][0]} 的 accepted attempt 已绑定另一份 '
                        'response/state；拒绝改写')
                return records[-1][0]
            if latest is not None:
                old_turn = latest.get('accepted_turn_index')
                new_turn = semantic.get('accepted_turn_index')
                if (not isinstance(old_turn, int) or not isinstance(new_turn, int)
                        or new_turn <= old_turn):
                    raise ProvenanceRefused(
                        f'{spec.label}: 新 revision 没有消费更大的 accepted turn')
                old_attempts = {
                    str(row['attempt_id']) for row in latest['attempt_links']
                }
                new_attempts = {
                    str(row['attempt_id']) for row in semantic['attempt_links']
                }
                if not old_attempts.issubset(new_attempts):
                    raise ProvenanceRefused(
                        f'{spec.label}: 新 revision 丢失了 prior ledger attempts')
            revision = len(records) + 1
            record: dict[str, Any] = {
                'version': VERSION, 'created_at': _now(),
                'revision': revision,
                'previous_checkpoint_sha256': (
                    None if latest is None else latest['checkpoint_sha256']),
                **semantic,
            }
            record['checkpoint_sha256'] = _checkpoint_hash(record)
            target = _checkpoint_revision_path(spec.checkpoint_dir(ctx), revision)
            if target.exists():
                raise ProvenanceRefused(
                    f'{target} 在链外已存在；拒绝覆盖')
            _atomic_json(target, record)
    return target


def restore_checkpoint(
        ctx: Any, spec: SingleSessionRunSpec, *, input_sha256: str,
        system: str, user: str, response_model: type[M], max_tokens: int,
        validator: Callable[[M, ChatResponse], str | None] | None = None,
) -> RestoredCheckpoint[M] | None:
    _validate_request_against_run(
        ctx, spec, input_sha256=input_sha256, system=system, user=user,
        response_model=response_model, max_tokens=max_tokens)
    run_path = spec.run_path(ctx) / RUN_FILE
    run_sha = canonical_sha256(run_path.read_bytes())
    with process_append_lock(_checkpoint_lock_path(spec, ctx)):
        records = _read_checkpoint_chain(ctx, spec, run_sha=run_sha)
    if not records:
        return None
    target, record = records[-1]
    if (record.get('input_sha256') != input_sha256
            or record.get('response_model') != response_model.__name__):
        raise ProvenanceRefused(f'{target} 的 input/schema 与当前请求漂移')
    acceptance = {key: record[key] for key in AGENT_ACCEPTANCE_FIELDS}
    validated = _validate_acceptance(
        ctx, spec, system=system, user=user, response_model=response_model,
        max_tokens=max_tokens, response_payload=record['response_payload'],
        acceptance=acceptance, claim_prior=True)
    if validated['parsed_payload'] != record['parsed_payload']:
        raise ProvenanceRefused(f'{target} 的 stdout 与封存 parsed payload 漂移')
    typed = validated['typed']
    if validator is not None:
        why = validator(typed, ChatResponse(
            content=str(acceptance['accepted_text']), finish_reason='stop',
            usage={}, model=str(acceptance['model'])))
        if why is not None:
            raise ProvenanceRefused(
                f'{spec.label}: 恢复候选不再通过当前业务 validator：{why}')
    return RestoredCheckpoint(
        value=typed,
        acceptance=validated['acceptance'],
        state_payload=dict(record['state_payload']),
        revision=int(record['revision']),
        revision_key=str(record['revision_key']),
        checkpoint_sha256=str(record['checkpoint_sha256']),
    )


def _commit_hash(payload: Mapping[str, Any]) -> str:
    return canonical_sha256({
        key: value for key, value in payload.items()
        if key not in ('prepared_at', 'committed_at', 'commit_sha256')
    })


def file_sha256(ctx: Any, rel: str) -> str | None:
    target = Path(ctx.novel_dir) / rel
    try:
        return canonical_sha256(target.read_bytes())
    except FileNotFoundError:
        return None
    except OSError as exc:
        raise ProvenanceRefused(f'agent commit 目标 {rel} 不可读：{exc}') from exc


def _normalized_touched(spec: CommitBundleSpec, values: Sequence[str]) -> list[str]:
    touched = sorted(str(value) for value in values)
    if len(touched) != len(set(touched)):
        raise ProvenanceRefused(f'{spec.run.label}: touched_paths 不能重复')
    allowed = tuple(tuple(sorted(group)) for group in spec.allowed_touched)
    if tuple(touched) not in allowed:
        raise ProvenanceRefused(
            f'{spec.run.label}: touched_paths={touched} 不是登记过的终态集合')
    if spec.primary_artifact not in touched:
        raise ProvenanceRefused(f'{spec.run.label}: primary artifact 未在 touched_paths')
    return touched


def _formal_paths(spec: CommitBundleSpec, record: Mapping[str, Any]) -> list[str]:
    nonformal = set(spec.nonformal_paths)
    return [str(rel) for rel in record['touched_paths'] if rel not in nonformal]


def _validate_commit_state(
        ctx: Any, spec: CommitBundleSpec, target: Path,
) -> dict[str, Any]:
    run_spec = spec.run
    record = _read_json(target, label=f'{run_spec.label} commit')
    if set(record) != _COMMIT_FIELDS:
        raise ArtifactUnreadable(f'{target} commit journal 字段漂移')
    if record.get('commit_sha256') != _commit_hash(record):
        raise ArtifactUnreadable(f'{target} commit SHA 不闭合')
    expected = {
        'version': VERSION, 'run_id': check_run_id(ctx.run_id),
        'sid': ctx.sid, 'node': run_spec.node, 'code': ctx.code,
    }
    if {key: record.get(key) for key in expected} != expected:
        raise ProvenanceRefused(f'{target} 不属于当前 run/sid/node/code')
    if record.get('state') not in ('prepared', 'committed'):
        raise ArtifactUnreadable(f'{target} commit state 非法')
    run_path = run_spec.run_path(ctx) / RUN_FILE
    run = _validate_run_record(
        ctx, run_spec, _read_json(run_path, label=run_spec.label))
    run_sha = canonical_sha256(run_path.read_bytes())
    if record.get('run_sha256') != run_sha:
        raise ProvenanceRefused(f'{target} 与 run.json 漂移')
    touched = _normalized_touched(spec, record.get('touched_paths') or ())
    baselines = record.get('baselines')
    if (not isinstance(baselines, dict) or sorted(baselines) != touched
            or any(value is not None and not is_sha256(value)
                   for value in baselines.values())):
        raise ArtifactUnreadable(f'{target} 写前基线不闭合')
    identity = record.get('request_identity')
    if (record.get('primary_artifact') != spec.primary_artifact
            or not is_sha256(record.get('primary_payload_sha256'))
            or not _is_projection_fingerprint(
                record.get('primary_payload_fingerprint'))
            or not is_sha256(record.get('finalization_sha256'))
            or record.get('purpose') != run_spec.purpose
            or not _is_attempt_id(record.get('attempt_id'))
            or not isinstance(identity, dict)
            or set(identity) != _LEDGER_IDENTITY_FIELDS
            or identity.get('attempt_id') != record.get('attempt_id')):
        raise ArtifactUnreadable(f'{target} 主产物/adoption 身份不闭合')
    latest = _latest_checkpoint(ctx, run_spec, run_sha=run_sha)
    if latest is None:
        raise ProvenanceRefused(f'{target} 所指 agent run 缺 checkpoint revision')
    _checkpoint_path, checkpoint = latest
    if (checkpoint.get('attempt_id') != record.get('attempt_id')
            or checkpoint.get('request_fingerprint')
            != identity.get('request_fingerprint')
            or checkpoint.get('model') != identity.get('model')
            or checkpoint.get('ledger_response_sha256')
            != identity.get('response_sha256')):
        raise ProvenanceRefused(f'{target} adoption 与 checkpoint 漂移')
    landed = record.get('landed_payload_sha256s')
    post = record.get('post_file_sha256s')
    if record['state'] == 'prepared':
        if (not isinstance(record.get('prepared_at'), str)
                or record.get('committed_at') is not None
                or landed is not None or post is not None):
            raise ArtifactUnreadable(f'{target} prepared 字段不闭合')
    else:
        formal = _formal_paths(spec, record)
        if (not isinstance(record.get('prepared_at'), str)
                or not isinstance(record.get('committed_at'), str)
                or not isinstance(landed, dict) or sorted(landed) != sorted(formal)
                or any(not is_sha256(value) for value in landed.values())
                or not isinstance(post, dict) or sorted(post) != touched
                or any(value is not None and not is_sha256(value)
                       for value in post.values())):
            raise ArtifactUnreadable(f'{target} committed 字段不闭合')
    return record


def commit_state(ctx: Any, spec: CommitBundleSpec) -> dict[str, Any] | None:
    target = spec.commit_path(ctx)
    return _validate_commit_state(ctx, spec, target) if target.is_file() else None


def validate_foreign_commit_envelope(
        path: Path, *, label: str, run_id: str, sid: str, node: str,
) -> dict[str, Any]:
    """跨 run reservation 扫描只验证不依赖当前 profile/ledger 的不可变外壳。

    业务 adapter 仍须在自己的全书所有权锁内调用，并决定 foreign prepared
    究竟阻止哪些节点。损坏记录不能当作“不存在”跳过。
    """
    checked_run_id = check_run_id(run_id)
    record = _read_json(path, label=label)
    if (set(record) != _COMMIT_FIELDS
            or record.get('commit_sha256') != _commit_hash(record)
            or record.get('version') != VERSION
            or record.get('run_id') != checked_run_id
            or record.get('sid') != sid
            or record.get('node') != node
            or record.get('state') not in ('prepared', 'committed')):
        raise ArtifactUnreadable(f'{label} {path} 身份/哈希不闭合')
    return record


def prepare_commit(
        ctx: Any, spec: CommitBundleSpec, *,
        primary_payload: Mapping[str, Any],
        finalization_payload: Mapping[str, Any],
        touched_paths: Sequence[str], attempt_id: str,
) -> dict[str, Any]:
    run_spec = spec.run
    _validate_spec(ctx, run_spec)
    touched = _normalized_touched(spec, touched_paths)
    if not _is_attempt_id(attempt_id):
        raise ProvenanceRefused(f'{run_spec.label}: attempt_id 不合法')
    run_path = run_spec.run_path(ctx) / RUN_FILE
    _validate_run_record(
        ctx, run_spec, _read_json(run_path, label=run_spec.label))
    run_sha = canonical_sha256(run_path.read_bytes())
    latest = _latest_checkpoint(ctx, run_spec, run_sha=run_sha)
    if latest is None:
        raise ProvenanceRefused(f'{run_spec.label}: commit 前缺 checkpoint revision')
    _checkpoint_path, checkpoint = latest
    if checkpoint.get('attempt_id') != attempt_id:
        raise ProvenanceRefused(f'{run_spec.label}: adoption 与 checkpoint 漂移')
    identity = ctx.ledger.request_identity_for_attempt(attempt_id)
    if set(identity) != _LEDGER_IDENTITY_FIELDS:
        raise ProvenanceRefused(f'{run_spec.label}: ledger identity 字段漂移')
    if (identity.get('request_fingerprint') != checkpoint.get('request_fingerprint')
            or identity.get('model') != checkpoint.get('model')
            or identity.get('response_sha256')
            != checkpoint.get('ledger_response_sha256')):
        raise ProvenanceRefused(
            f'{run_spec.label}: commit attempt 与 checkpoint/ledger 不闭合')
    primary = dict(primary_payload)
    finalization = dict(finalization_payload)
    payload: dict[str, Any] = {
        'version': VERSION, 'state': 'prepared', 'prepared_at': _now(),
        'committed_at': None, 'run_id': check_run_id(ctx.run_id),
        'sid': ctx.sid, 'node': run_spec.node, 'code': ctx.code,
        'run_sha256': run_sha, 'primary_artifact': spec.primary_artifact,
        'primary_payload_sha256': canonical_sha256(primary),
        'primary_payload_fingerprint': prov.projection_fingerprint(primary),
        'finalization_sha256': canonical_sha256(finalization),
        'touched_paths': touched,
        'baselines': {rel: file_sha256(ctx, rel) for rel in touched},
        'purpose': run_spec.purpose, 'attempt_id': attempt_id,
        'request_identity': identity,
        'landed_payload_sha256s': None, 'post_file_sha256s': None,
    }
    payload['commit_sha256'] = _commit_hash(payload)
    target = spec.commit_path(ctx)
    with _LOCK:
        with process_append_lock(target):
            if target.is_file():
                current = _validate_commit_state(ctx, spec, target)
                ignored = {
                    'state', 'prepared_at', 'committed_at', 'commit_sha256',
                    'baselines', 'landed_payload_sha256s', 'post_file_sha256s',
                }
                left = {key: value for key, value in current.items() if key not in ignored}
                right = {key: value for key, value in payload.items() if key not in ignored}
                if left != right:
                    raise ProvenanceRefused(
                        f'{target} 已绑定另一份主产物/提交输入/adoption；拒绝覆盖')
                return current
            _atomic_json(target, payload)
    return payload


def target_state(
        ctx: Any, spec: CommitBundleSpec, record: Mapping[str, Any], rel: str, *,
        expected_file_sha256: str | None = None,
        expected_absent: bool = False,
) -> str:
    current_record = _validate_commit_state(ctx, spec, spec.commit_path(ctx))
    if dict(record) != current_record:
        raise ProvenanceRefused('传入的 agent commit record 不是当前盘上 journal')
    if rel not in current_record['baselines']:
        raise ProvenanceRefused(f'{rel} 不属于当前 commit touched_paths')
    if expected_absent and expected_file_sha256 is not None:
        raise ProvenanceRefused(
            f'{rel} 不能同时声明 expected SHA 与 expected absent')
    if not isinstance(expected_absent, bool):
        raise ProvenanceRefused(f'{rel} expected_absent 必须是 bool')
    if expected_file_sha256 is not None and not is_sha256(expected_file_sha256):
        raise ProvenanceRefused(f'{rel} expected file SHA 非法')
    current_sha = file_sha256(ctx, rel)
    if expected_absent and current_sha is None:
        return 'expected'
    if expected_file_sha256 is not None and current_sha == expected_file_sha256:
        return 'expected'
    if current_sha == current_record['baselines'][rel]:
        return 'baseline'
    raise ProvenanceRefused(
        f'{rel} 在 prepared 尾窗中既不是写前基线，也不是本 run 已验证目标；'
        '拒绝覆盖第三方漂移')


def verify_commit_primary(record: Mapping[str, Any], payload: Mapping[str, Any]) -> bool:
    return record.get('primary_payload_sha256') == canonical_sha256(dict(payload))


def verify_commit_finalization(
        record: Mapping[str, Any], payload: Mapping[str, Any],
) -> bool:
    return record.get('finalization_sha256') == canonical_sha256(dict(payload))


def restore_commit_adoption(
        ctx: Any, spec: CommitBundleSpec, record: Mapping[str, Any],
) -> tuple[str, str]:
    current = _validate_commit_state(ctx, spec, spec.commit_path(ctx))
    if dict(record) != current:
        raise ProvenanceRefused('传入的 agent commit record 不是当前盘上 journal')
    identity = current['request_identity']
    assert isinstance(identity, dict)
    attempt_id = str(current['attempt_id'])
    ctx.ledger.claim_prior_attempts(
        [attempt_id], sid=ctx.sid, node=spec.run.node,
        purpose=spec.run.purpose,
        request_fingerprints=[str(identity['request_fingerprint'])])
    if ctx.ledger.request_identity_for_attempt(attempt_id) != identity:
        raise ProvenanceRefused(
            f'{spec.run.label}: commit attempt 与 ledger 不闭合')
    return spec.run.purpose, attempt_id


def _read_adoption_events(ctx: Any) -> list[dict[str, Any]]:
    if ctx.ledger.path is None:
        return [dict(row) for row in ctx.ledger.audit_events]
    target = ctx.ledger.path.with_name('llm_audit.jsonl')
    if not target.is_file():
        return []
    try:
        with process_append_lock(target):
            raw = target.read_bytes()
    except OSError as exc:
        raise ProvenanceRefused(f'读不了 adoption 审计账：{exc}') from exc
    lines = raw.split(b'\n')
    partial_tail = bool(raw) and not raw.endswith(b'\n')
    rows: list[dict[str, Any]] = []
    for index, encoded in enumerate(lines):
        if not encoded.strip():
            continue
        try:
            value = json.loads(encoded.decode('utf-8'))
        except (UnicodeError, json.JSONDecodeError) as exc:
            if partial_tail and index == len(lines) - 1:
                break
            raise ArtifactUnreadable(
                f'LLM adoption 审计第 {index + 1} 行损坏：{exc}') from exc
        if isinstance(value, dict):
            rows.append(value)
    return rows


def _require_adoption(ctx: Any, spec: CommitBundleSpec, record: Mapping[str, Any]) -> None:
    run_spec = spec.run
    matches = [
        row for row in _read_adoption_events(ctx)
        if (row.get('kind') == 'artifact_adoption'
            and row.get('run_id') == ctx.run_id
            and row.get('sid') == ctx.sid
            and row.get('node') == run_spec.node
            and row.get('purpose') == run_spec.purpose
            and row.get('attempt_id') == record['attempt_id']
            and row.get('artifact') == spec.primary_artifact
            and row.get('artifact_fingerprint')
            == record['primary_payload_fingerprint']
            and row.get('chapter') == run_spec.chapter)
    ]
    if len(matches) != 1:
        raise ProvenanceRefused(
            f'{run_spec.label}: committed 前必须恰有一条主产物 adoption，'
            f'实际 {len(matches)}')


def mark_committed(
        ctx: Any, spec: CommitBundleSpec, *,
        primary_payload: Mapping[str, Any],
        finalization_payload: Mapping[str, Any],
        landed_payloads: Mapping[str, Mapping[str, Any]],
) -> Path:
    target = spec.commit_path(ctx)
    with _LOCK:
        with process_append_lock(target):
            if not target.is_file():
                raise ProvenanceRefused(f'{target} 缺 prepared journal')
            current = _validate_commit_state(ctx, spec, target)
            if not verify_commit_primary(current, primary_payload):
                raise ProvenanceRefused('agent 主产物 readback 与 prepared payload 漂移')
            if not verify_commit_finalization(current, finalization_payload):
                raise ProvenanceRefused('agent finalization 与 prepared 漂移')
            formal = sorted(_formal_paths(spec, current))
            if sorted(landed_payloads) != formal:
                raise ProvenanceRefused(f'landed payloads 必须恰好覆盖 {formal}')
            landed_sha = {
                rel: canonical_sha256(dict(landed_payloads[rel])) for rel in formal
            }
            post = {rel: file_sha256(ctx, rel) for rel in current['touched_paths']}
            if any(post[rel] is None for rel in formal):
                raise ProvenanceRefused('committed 前正式 artifact 仍有缺失')
            _require_adoption(ctx, spec, current)
            if current['state'] == 'committed':
                if (current.get('landed_payload_sha256s') != landed_sha
                        or current.get('post_file_sha256s') != post):
                    raise ProvenanceRefused(
                        'committed journal 与当前 typed/file readback 漂移')
                return target
            payload = {
                **current, 'state': 'committed', 'committed_at': _now(),
                'landed_payload_sha256s': landed_sha,
                'post_file_sha256s': post,
            }
            payload['commit_sha256'] = _commit_hash(payload)
            _atomic_json(target, payload)
    return target


def verify_committed_delivery(
        ctx: Any, spec: CommitBundleSpec, record: Mapping[str, Any], *,
        primary_payload: Mapping[str, Any],
        finalization_payload: Mapping[str, Any],
        landed_payloads: Mapping[str, Mapping[str, Any]],
) -> None:
    current = _validate_commit_state(ctx, spec, spec.commit_path(ctx))
    if dict(record) != current or current['state'] != 'committed':
        raise ProvenanceRefused('传入的 journal 不是当前 committed 记录')
    if (not verify_commit_primary(current, primary_payload)
            or not verify_commit_finalization(current, finalization_payload)):
        raise ProvenanceRefused('committed 主产物或 finalization 漂移')
    formal = sorted(_formal_paths(spec, current))
    if sorted(landed_payloads) != formal:
        raise ProvenanceRefused('committed typed readback 集合漂移')
    landed_sha = {
        rel: canonical_sha256(dict(landed_payloads[rel])) for rel in formal
    }
    post = {rel: file_sha256(ctx, rel) for rel in current['touched_paths']}
    if (landed_sha != current.get('landed_payload_sha256s')
            or post != current.get('post_file_sha256s')):
        raise ProvenanceRefused('committed 后正式 payload/file 被第三方改写')
    _require_adoption(ctx, spec, current)


__all__ = [
    'AGENT_ACCEPTANCE_FIELDS', 'COMMIT_FILE', 'CommitBundleSpec', 'RUN_FILE',
    'RestoredCheckpoint', 'SingleSessionRunSpec', 'canonical_bytes', 'canonical_sha256',
    'commit_state', 'ensure_run', 'file_sha256', 'is_sha256', 'mark_committed',
    'prepare_commit', 'restore_checkpoint', 'restore_commit_adoption',
    'target_state', 'verify_commit_finalization', 'verify_commit_primary',
    'validate_foreign_commit_envelope', 'verify_committed_delivery',
    'write_checkpoint',
]
