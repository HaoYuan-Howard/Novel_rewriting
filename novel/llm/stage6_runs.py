# -*- coding: utf-8 -*-
"""Stage6 双盲 agent 的验证后 checkpoint 与提交尾窗 journal。

这里保存的是已经过 wire/schema/业务 validator 的 A/B 响应正文，不是正式
artifact，也不是可绕过校验的响应缓存。恢复时必须重新闭合当前代码、输入投影、
两份真实 prompt、response schema、resolved profile、成本账与 agent session 审计；
任何一项漂移都关闭式拒绝，不能退化成 miss 后悄悄再花一次钱。

正式写盘仍只属于 Stage6 producer/``ArtifactStore``。本模块只提供一枚跨线程、
跨进程的提交所有权锁，以及 ``prepared → committed`` journal：调用方必须在同一
所有权临界区内选择 seq、prepare、正式 write、typed readback、写两条 adoption，
最后携 readback payload 调 ``mark_committed``。这样进程死在任一尾窗，下一次同
run 都能补齐，而不会覆盖另一进程刚选中的 ``semantic_<n>.json``。
"""
from __future__ import annotations

from novel import runtime_layout
import hashlib
import json
import os
import re
import tempfile
import threading
from collections.abc import Callable, Mapping
from contextlib import contextmanager, nullcontext
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, TypeVar

from pydantic import BaseModel, ValidationError

from contracts.schema.orchestration import AgentProfile
from novel import paths, prov
from novel.agents import session_audit as agent_session_audit
from novel.agents.capabilities import validate_profile
from novel.agents.model_identity import (
    resolve_ledger_model_identity, validate_checkpoint_model_identity,
)
from novel.agents.base import render_structured_agent_stdin_bytes
from novel.agents.session_client import (
    SESSION_PROTOCOL_VERSION, SessionAgentClient, agent_json_schema,
    validate_agent_session_request_binding,
)
from novel.appendlog import process_append_lock
from novel.errors import ArtifactUnreadable, ProvenanceRefused, UpstreamMissing
from novel.llm.json_text import normalize_json_text
from novel.llm.prompts import missing_required_fields, required_top_level
from novel.llm.transport import ChatResponse
from novel.run_identity import check_run_id


M = TypeVar('M', bound=BaseModel)

VERSION = 1
RUN_FILE = 'run.json'
COMMIT_FILE = 'committed.json'
JUDGE_ROLES = ('A', 'B')
RECOVERY_POLICY = 'stage6-semantic-v1'
_SEMANTIC_ARTIFACT_RE = re.compile(r'stage6/semantic_([1-9]\d*)\.json\Z')

_LOCK = threading.RLock()
_OWNERSHIP_REGISTRY_LOCK = threading.Lock()
_OWNERSHIP_THREAD_LOCKS: dict[str, threading.RLock] = {}
_OWNERSHIP_LOCAL = threading.local()


def _history_files(ctx: Any, pattern: str) -> tuple[Path, ...]:
    """只枚举当前 service 历史；旧书内布局不得参与采用或提交。"""
    found: dict[str, Path] = {}
    for root in runtime_layout.stage_run_roots(ctx.novel_dir, 'stage6'):
        if not root.is_dir():
            continue
        for target in root.glob(pattern):
            found.setdefault(target.relative_to(root).as_posix(), target)
    return tuple(found[key] for key in sorted(found))

_AGENT_POLICY_FIELDS = {
    'protocol_version', 'profile', 'session_isolation',
    'recovery_policy', 'phases',
}
_AGENT_PHASE_FIELDS = {
    'phase', 'response_model', 'response_schema_sha256',
    'requested_output_token_ceiling',
    'prefer_content_over_reasoning_salvage',
}
_AGENT_ACCEPTANCE_FIELDS = {
    'acceptance_protocol', 'attempt_id', 'request_fingerprint', 'model',
    'ledger_response_sha256', 'accepted_channel', 'accepted_text',
    'normalized_text', 'attempt_links', 'profile', 'subject_sha256',
    'session_run_id', 'provider_session_id', 'accepted_turn_index',
    'accepted_prompt_sha256', 'accepted_schema_sha256', 'accepted_request',
    'request_mode', 'recovery_from_attempts', 'salvaged',
}
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
_ROLE_REQUEST_FIELDS = {
    'role', 'purpose', 'system_sha256', 'user_sha256',
    'initial_prompt_sha256', 'response_schema_sha256',
}
_EXPERIMENT_ARM_FIELDS = {'session_policy', 'judge_stagger_ms'}
_RUN_FIELDS = {
    'version', 'run_id', 'sid', 'node', 'code', 'input_sha256',
    'request_policy', 'request_policy_sha256', 'requests',
    'experiment_arm',
    'identity_sha256', 'created_at',
}
_CHECKPOINT_EXTRA_FIELDS = {
    'version', 'created_at', 'run_id', 'sid', 'node', 'code',
    'run_sha256', 'role', 'purpose', 'input_sha256', 'response_model',
    'parsed_payload', 'parsed_payload_sha256', 'response_payload',
    'response_payload_sha256', 'checkpoint_sha256',
}
_COMMIT_FIELDS = {
    'version', 'state', 'prepared_at', 'committed_at', 'run_id', 'sid',
    'node', 'code', 'run_sha256', 'artifact', 'artifact_fingerprint',
    'artifact_sha256', 'finalization_sha256', 'attempts', 'adoptions',
    'commit_sha256',
}


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec='seconds')


def _canonical_bytes(value: object) -> bytes:
    try:
        return json.dumps(
            value, ensure_ascii=False, sort_keys=True, separators=(',', ':'),
            allow_nan=False,
        ).encode('utf-8')
    except (TypeError, ValueError) as exc:
        raise ProvenanceRefused(f'Stage6 run 状态不能 canonical JSON 化：{exc}') from exc


def _sha(value: object) -> str:
    payload = value if isinstance(value, bytes) else _canonical_bytes(value)
    return hashlib.sha256(payload).hexdigest()


def _is_sha256(value: object) -> bool:
    return (isinstance(value, str) and len(value) == 64
            and all(char in '0123456789abcdef' for char in value))


def _is_attempt_id(value: object) -> bool:
    return (isinstance(value, str) and len(value) == 16
            and all(char in '0123456789abcdef' for char in value))


def _role(role: str) -> str:
    if role not in JUDGE_ROLES:
        raise ProvenanceRefused(
            f'Stage6 semantic judge role 必须属于 {JUDGE_ROLES}，实得 {role!r}')
    return role


def _purpose(role: str) -> str:
    return f'stage6.semantic.{_role(role)}'


def _positive_ceiling(value: int) -> int:
    if not isinstance(value, int) or isinstance(value, bool) or value <= 0:
        raise ProvenanceRefused('Stage6 requested output token ceiling 必须是正整数')
    return value


def semantic_experiment_arm(*, judge_stagger_ms: int = 0) -> dict[str, Any]:
    """冻结不改变请求字节、却会改变 A/B 启动时序的实验臂。

    旧 ``run.json`` 没有这格；它只能解释为历史默认的 0ms 独立并行臂。
    非默认错峰必须显式进不可变 run 身份，不能借旧 checkpoint 冒充本轮实验。
    """
    if (not isinstance(judge_stagger_ms, int)
            or isinstance(judge_stagger_ms, bool)
            or judge_stagger_ms < 0):
        raise ProvenanceRefused('Stage6 semantic judge_stagger_ms 必须是非负整数')
    return {
        'session_policy': (
            'independent-concurrent-stagger/v1'
            if judge_stagger_ms else 'independent-parallel'),
        'judge_stagger_ms': judge_stagger_ms,
    }


def _experiment_arm(value: object | None) -> dict[str, Any]:
    if value is None:
        return semantic_experiment_arm()
    if not isinstance(value, dict) or set(value) != _EXPERIMENT_ARM_FIELDS:
        raise ProvenanceRefused(
            'Stage6 semantic experiment arm 字段不完整或含未登记字段')
    stagger = value.get('judge_stagger_ms')
    expected = semantic_experiment_arm(judge_stagger_ms=stagger)
    if value != expected:
        raise ProvenanceRefused(
            'Stage6 semantic experiment arm 的时序与 session policy 漂移')
    return expected


def _atomic_json(path: Path, payload: Mapping[str, Any]) -> None:
    """同卷临时文件 + fsync + replace；完成哨兵永远不出现半份 JSON。"""
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


def _read_json(path: Path) -> dict[str, Any]:
    try:
        raw = path.read_bytes()
        value = json.loads(raw.decode('utf-8'))
    except FileNotFoundError:
        raise UpstreamMissing(f'{path} 不在盘上') from None
    except (OSError, UnicodeError, json.JSONDecodeError) as exc:
        raise ArtifactUnreadable(f'Stage6 run 状态 {path} 读不出来：{exc}') from exc
    if not isinstance(value, dict) or value.get('version') != VERSION:
        raise ArtifactUnreadable(
            f'Stage6 run 状态 {path} 不是当前内部 v{VERSION} 对象；不猜旧形状')
    return value


def _run_root(ctx: Any) -> Path:
    runtime_layout.stage_run_roots(ctx.novel_dir, 'stage6')
    return paths.stage6_semantic_run_path(ctx.novel_dir, check_run_id(ctx.run_id))


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
        ctx: Any, response_model: type[BaseModel], max_tokens: int,
) -> dict[str, Any]:
    profile = getattr(ctx, 'executor_profile', None)
    if profile is None:
        raise ProvenanceRefused('Stage6 agent run 缺显式 executor profile')
    return SessionAgentClient.build_json_request_policy(
        profile,
        [(role, response_model, _positive_ceiling(max_tokens), False)
         for role in JUDGE_ROLES],
        recovery_policy=RECOVERY_POLICY,
    )


def _validate_policy(
        ctx: Any, value: object, *, response_model: type[BaseModel] | None = None,
        max_tokens: int | None = None,
) -> dict[str, Any]:
    if not isinstance(value, dict) or set(value) != _AGENT_POLICY_FIELDS:
        raise ProvenanceRefused('Stage6 agent run 缺完整 request policy')
    if (value.get('protocol_version') != SESSION_PROTOCOL_VERSION
            or value.get('session_isolation') != 'per-purpose'
            or value.get('recovery_policy') != RECOVERY_POLICY):
        raise ProvenanceRefused(
            'Stage6 agent run 不是当前 stage6-semantic-v1/per-purpose 协议')
    _resolved_profile(ctx, value.get('profile'), label='Stage6 request policy')
    phases = value.get('phases')
    if not isinstance(phases, list) or len(phases) != 2:
        raise ProvenanceRefused('Stage6 agent request policy 必须恰有 A/B 两相')
    seen: set[str] = set()
    for row in phases:
        if not isinstance(row, dict) or set(row) != _AGENT_PHASE_FIELDS:
            raise ProvenanceRefused('Stage6 agent policy phase 字段漂移')
        phase = row.get('phase')
        ceiling = row.get('requested_output_token_ceiling')
        if (phase not in JUDGE_ROLES or phase in seen
                or not isinstance(row.get('response_model'), str)
                or not row['response_model']
                or not _is_sha256(row.get('response_schema_sha256'))
                or not isinstance(ceiling, int) or isinstance(ceiling, bool)
                or ceiling <= 0
                or row.get('prefer_content_over_reasoning_salvage') is not False):
            raise ProvenanceRefused('Stage6 agent policy phase 身份或 ceiling 不合法')
        seen.add(str(phase))
    if seen != set(JUDGE_ROLES):
        raise ProvenanceRefused('Stage6 agent policy 未恰好覆盖 A/B')
    normalized = json.loads(_canonical_bytes(value))
    if response_model is not None:
        if max_tokens is None:
            raise ProvenanceRefused('Stage6 当前 response model 缺 requested ceiling')
        expected = _expected_policy(
            ctx, response_model, _positive_ceiling(max_tokens))
        if normalized != expected:
            raise ProvenanceRefused(
                'Stage6 agent policy 的 profile/schema/ceiling 与当前请求漂移')
    return normalized


def _request_rows(
        system: str, users: Mapping[str, str], response_model: type[BaseModel],
        *, driver: str,
) -> list[dict[str, Any]]:
    if set(users) != set(JUDGE_ROLES):
        raise ProvenanceRefused('Stage6 judge users 必须恰好提供 A/B，不能缺失或多出')
    schema_sha = _sha(agent_json_schema(response_model))
    rows: list[dict[str, Any]] = []
    for role in JUDGE_ROLES:
        user = users[role]
        if not isinstance(user, str) or not isinstance(system, str):
            raise ProvenanceRefused('Stage6 judge system/user 必须是字符串')
        rows.append({
            'role': role,
            'purpose': _purpose(role),
            'system_sha256': _sha(system.encode('utf-8')),
            'user_sha256': _sha(user.encode('utf-8')),
            'initial_prompt_sha256': _sha(
                render_structured_agent_stdin_bytes(
                    system, user, driver=driver)),
            'response_schema_sha256': schema_sha,
        })
    return rows


def _expected_role_request(
        system: str, user: str, response_model: type[BaseModel], role: str,
        *, driver: str,
) -> dict[str, Any]:
    if not isinstance(system, str) or not isinstance(user, str):
        raise ProvenanceRefused('Stage6 judge system/user 必须是字符串')
    schema_sha = _sha(agent_json_schema(response_model))
    return {
        'role': _role(role),
        'purpose': _purpose(role),
        'system_sha256': _sha(system.encode('utf-8')),
        'user_sha256': _sha(user.encode('utf-8')),
        'initial_prompt_sha256': _sha(
            render_structured_agent_stdin_bytes(
                system, user, driver=driver)),
        'response_schema_sha256': schema_sha,
    }


def _expected_run_identity(
        ctx: Any, *, input_sha256: str, system: str,
        users: Mapping[str, str], response_model: type[BaseModel],
        max_tokens: int, experiment_arm: Mapping[str, Any] | None = None,
) -> dict[str, Any]:
    if ctx.node != 'stage6.semantic':
        raise ProvenanceRefused(
            f'Stage6 semantic run 不能由 node={ctx.node!r} 创建或恢复')
    if not _is_sha256(input_sha256):
        raise ProvenanceRefused('Stage6 semantic input_sha256 必须是完整小写 SHA-256')
    policy = _expected_policy(ctx, response_model, max_tokens)
    return {
        'version': VERSION,
        'run_id': check_run_id(ctx.run_id),
        'sid': ctx.sid,
        'node': ctx.node,
        'code': ctx.code,
        'input_sha256': input_sha256,
        'request_policy': policy,
        'request_policy_sha256': _sha(policy),
        'experiment_arm': _experiment_arm(
            None if experiment_arm is None else dict(experiment_arm)),
        'requests': _request_rows(
            system, users, response_model,
            driver=str(policy['profile']['driver'])),
    }


def _validate_run_record(
        ctx: Any, record: Mapping[str, Any], *,
        response_model: type[BaseModel] | None = None,
        max_tokens: int | None = None,
) -> dict[str, Any]:
    fields = set(record)
    if fields != _RUN_FIELDS:
        raise ArtifactUnreadable('Stage6 run.json 字段不完整或含未登记字段')
    identity = {
        key: record[key] for key in fields
        if key not in ('identity_sha256', 'created_at')
    }
    if record.get('identity_sha256') != _sha(identity):
        raise ArtifactUnreadable('Stage6 run.json 的 identity_sha256 不闭合')
    expected_ctx = {
        'version': VERSION,
        'run_id': check_run_id(ctx.run_id),
        'sid': ctx.sid,
        'node': ctx.node,
        'code': ctx.code,
    }
    if {key: record.get(key) for key in expected_ctx} != expected_ctx:
        raise ProvenanceRefused('Stage6 run.json 不属于当前 run/sid/node/code')
    if not _is_sha256(record.get('input_sha256')):
        raise ArtifactUnreadable('Stage6 run.json 缺合法 input_sha256')
    policy = _validate_policy(
        ctx, record.get('request_policy'), response_model=response_model,
        max_tokens=max_tokens)
    if record.get('request_policy_sha256') != _sha(policy):
        raise ArtifactUnreadable('Stage6 request_policy_sha256 不闭合')
    requests = record.get('requests')
    if not isinstance(requests, list) or len(requests) != 2:
        raise ArtifactUnreadable('Stage6 run.json 必须恰有 A/B 两份 request identity')
    seen: set[str] = set()
    for row in requests:
        if (not isinstance(row, dict) or set(row) != _ROLE_REQUEST_FIELDS
                or row.get('role') not in JUDGE_ROLES
                or row.get('role') in seen
                or row.get('purpose') != _purpose(str(row.get('role')))
                or any(not _is_sha256(row.get(field)) for field in (
                    'system_sha256', 'user_sha256', 'initial_prompt_sha256',
                    'response_schema_sha256'))):
            raise ArtifactUnreadable('Stage6 run.json 的 judge request 身份不闭合')
        seen.add(str(row['role']))
    if seen != set(JUDGE_ROLES):
        raise ArtifactUnreadable('Stage6 run.json 的 requests 未恰好覆盖 A/B')
    normalized = dict(record)
    # 创建新 run 可选默认配置；读取既有记录必须核对显式写入的完整配置。
    # 缺席或 null 都不能推断成默认臂，更不能重签已有 checkpoint。
    if not isinstance(record.get('experiment_arm'), dict):
        raise ArtifactUnreadable('Stage6 run.json 缺合法 experiment_arm')
    normalized['experiment_arm'] = _experiment_arm(record['experiment_arm'])
    return normalized


def ensure_run(
        ctx: Any, *, input_sha256: str, system: str,
        users: Mapping[str, str], response_model: type[BaseModel],
        max_tokens: int, experiment_arm: Mapping[str, Any] | None = None,
) -> dict[str, Any]:
    """创建或逐字段核验不可变双盲 run；漂移不能退化成 checkpoint miss。"""
    identity = _expected_run_identity(
        ctx, input_sha256=input_sha256, system=system, users=users,
        response_model=response_model, max_tokens=max_tokens,
        experiment_arm=experiment_arm)
    target = _run_root(ctx) / RUN_FILE
    with _LOCK:
        with process_append_lock(target):
            if target.is_file():
                current = _validate_run_record(
                    ctx, _read_json(target), response_model=response_model,
                    max_tokens=max_tokens)
                comparable = {
                    key: current.get(key) for key in identity
                }
                if comparable != identity:
                    raise ProvenanceRefused(
                        f'Stage6 run={ctx.run_id} 的输入/prompt/schema/profile/'
                        'experiment arm 已漂移；'
                        '拒绝恢复旧判官或重新付费')
                return current
            payload = {
                **identity,
                'identity_sha256': _sha(identity),
                'created_at': _now(),
            }
            _atomic_json(target, payload)
            return payload


def _role_request(record: Mapping[str, Any], role: str) -> dict[str, Any]:
    rows = [
        row for row in record.get('requests', [])
        if isinstance(row, dict) and row.get('role') == _role(role)
    ]
    if len(rows) != 1:
        raise ArtifactUnreadable(f'Stage6 run.json 对 judge {role} 没有唯一请求')
    return dict(rows[0])


def _validate_role_run(
        ctx: Any, *, role: str, input_sha256: str, system: str, user: str,
        response_model: type[BaseModel], max_tokens: int,
) -> tuple[dict[str, Any], dict[str, Any]]:
    target = _run_root(ctx) / RUN_FILE
    run = _validate_run_record(
        ctx, _read_json(target), response_model=response_model,
        max_tokens=max_tokens)
    if run.get('input_sha256') != input_sha256 or not _is_sha256(input_sha256):
        raise ProvenanceRefused('Stage6 当前输入投影与不可变 run.json 漂移')
    policy = run.get('request_policy')
    if not isinstance(policy, dict):
        raise ArtifactUnreadable('Stage6 run.json 缺 request policy')
    profile = _resolved_profile(
        ctx, policy.get('profile'), label='Stage6 request policy')
    expected = _expected_role_request(
        system, user, response_model, role,
        driver=str(profile['driver']))
    # 另一 judge 的 user 不在此窄口重造；run.json 自身的 identity_sha256 已保护它。
    current = _role_request(run, role)
    for field in _ROLE_REQUEST_FIELDS:
        if field == 'role' or field == 'purpose':
            continue
        if current.get(field) != expected.get(field):
            raise ProvenanceRefused(
                f'Stage6 judge {role} 的 prompt/schema 与不可变 run.json 漂移')
    return run, current


def _validate_session_turn(
        ctx: Any, *, purpose: str, acceptance: Mapping[str, Any],
        profile: Mapping[str, Any], system: str, user: str,
) -> None:
    """把 accepted attempt 唯一联到真实、独立的 executor session turn。"""
    try:
        events = agent_session_audit.read_events(ctx.novel_dir.parent)
        agent_session_audit.fold(events)
    except (OSError, UnicodeError, ValueError) as exc:
        raise ProvenanceRefused(
            f'{purpose}: agent session 审计不可验证：{exc}') from exc
    attempt_id = acceptance.get('attempt_id')
    turns = [
        event for event in events
        if event.kind == 'turn' and event.attempt_id == attempt_id
    ]
    if len(turns) != 1:
        raise ProvenanceRefused(
            f'{purpose}: accepted attempt={attempt_id!r} 必须唯一联结一条 session turn')
    turn = turns[0]
    starts = [
        event for event in events
        if (event.kind == 'started'
            and event.session_run_id == acceptance.get('session_run_id'))
    ]
    if len(starts) != 1:
        raise ProvenanceRefused(f'{purpose}: logical session 缺唯一 started 事件')
    start = starts[0]
    start_profile = (
        None if start.profile is None else start.profile.model_dump(mode='json'))
    turn_profile = (
        None if turn.profile is None else turn.profile.model_dump(mode='json'))
    current_session = validate_agent_session_request_binding(
        events, acceptance,
        purpose=purpose,
        profile=profile,
        original_prompt_sha256=_sha(
            render_structured_agent_stdin_bytes(
                system, user, driver=str(profile.get('driver') or ''))),
        original_schema_sha256=str(acceptance.get('accepted_schema_sha256')),
    )
    first_turn = current_session.turns[0]
    expected_start = {
        'wire_version': agent_session_audit.SESSION_AUDIT_WIRE_VERSION,
        'session_run_id': acceptance.get('session_run_id'),
        'parent_run_id': check_run_id(ctx.run_id),
        'role': 'executor', 'node': ctx.node, 'chapter': None,
        'subject_sha256': acceptance.get('subject_sha256'),
        'profile': dict(profile), 'provider_session_id': None,
        'requested_model': profile.get('model'), 'actual_model': None,
        'requested_effort': profile.get('reasoning_effort'), 'actual_effort': None,
        'protocol_version': SESSION_PROTOCOL_VERSION,
        'start_code_version': ctx.code,
        'prompt_sha256': first_turn.prompt_sha256,
        'schema_sha256': first_turn.schema_sha256,
    }
    actual_start = {
        'wire_version': start.wire_version,
        'session_run_id': start.session_run_id,
        'parent_run_id': start.parent_run_id,
        'role': start.role, 'node': start.node, 'chapter': start.chapter,
        'subject_sha256': start.subject_sha256,
        'profile': start_profile,
        'provider_session_id': start.provider_session_id,
        'requested_model': start.requested_model,
        'actual_model': start.actual_model,
        'requested_effort': start.requested_effort,
        'actual_effort': start.actual_effort,
        'protocol_version': start.protocol_version,
        'start_code_version': start.start_code_version,
        'prompt_sha256': start.prompt_sha256,
        'schema_sha256': start.schema_sha256,
    }
    if actual_start != expected_start:
        raise ProvenanceRefused(f'{purpose}: started 事件与当前 subject 漂移')
    model_identity = validate_checkpoint_model_identity(
        profile,
        checkpoint_model=acceptance.get('model'),
        actual_model=turn.actual_model,
        cli_version=turn.cli_version,
        require_driver_version=True,
    )
    expected_turn = {
        'wire_version': agent_session_audit.SESSION_AUDIT_WIRE_VERSION,
        'session_run_id': acceptance.get('session_run_id'),
        'parent_run_id': check_run_id(ctx.run_id),
        'role': 'executor', 'node': ctx.node, 'chapter': None,
        'subject_sha256': acceptance.get('subject_sha256'),
        'profile': dict(profile),
        'provider_session_id': acceptance.get('provider_session_id'),
        'turn_index': acceptance.get('accepted_turn_index'),
        'attempt_id': attempt_id,
        'requested_model': profile.get('model'),
        'actual_model': model_identity.actual_model,
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
        'subject_sha256': turn.subject_sha256,
        'profile': turn_profile,
        'provider_session_id': turn.provider_session_id,
        'turn_index': turn.turn_index, 'attempt_id': turn.attempt_id,
        'requested_model': turn.requested_model,
        'actual_model': turn.actual_model,
        'requested_effort': turn.requested_effort,
        'protocol_version': turn.protocol_version,
        'start_code_version': turn.start_code_version,
        'prompt_sha256': turn.prompt_sha256,
        'schema_sha256': turn.schema_sha256,
    }
    if actual_turn != expected_turn:
        raise ProvenanceRefused(f'{purpose}: checkpoint 与唯一 session turn 漂移')
    if (turn.actual_effort is not None
            and turn.actual_effort != profile.get('reasoning_effort')):
        raise ProvenanceRefused(f'{purpose}: actual effort 与 resolved profile 漂移')


def _validate_acceptance(
        ctx: Any, *, role: str, system: str, user: str,
        response_model: type[BaseModel], max_tokens: int,
        response_payload: Mapping[str, Any], acceptance: Mapping[str, Any],
        claim_prior: bool,
) -> dict[str, Any]:
    purpose = _purpose(role)
    if (set(acceptance) != _AGENT_ACCEPTANCE_FIELDS
            or acceptance.get('acceptance_protocol') != SESSION_PROTOCOL_VERSION):
        raise ProvenanceRefused(
            f'{purpose}: acceptance 不是当前精确 20-key node-executor/v1')
    profile = _resolved_profile(
        ctx, acceptance.get('profile'), label=f'{purpose}: acceptance')
    policy = _expected_policy(ctx, response_model, max_tokens)
    if policy.get('profile') != profile:
        raise ProvenanceRefused(f'{purpose}: acceptance profile 与 run policy 漂移')
    attempt_id = acceptance.get('attempt_id')
    request_fingerprint = acceptance.get('request_fingerprint')
    model = acceptance.get('model')
    response_sha = acceptance.get('ledger_response_sha256')
    accepted_text = acceptance.get('accepted_text')
    normalized_text = acceptance.get('normalized_text')
    links = acceptance.get('attempt_links')
    recovery = acceptance.get('recovery_from_attempts')
    turn_index = acceptance.get('accepted_turn_index')
    schema_sha = _sha(agent_json_schema(response_model))
    binding = acceptance.get('accepted_request')
    if (not _is_attempt_id(attempt_id)
            or not _is_sha256(request_fingerprint)
            or not isinstance(model, str) or model != profile['model']
            or not _is_sha256(response_sha)
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
            or not _is_sha256(acceptance.get('accepted_prompt_sha256'))
            or acceptance.get('accepted_schema_sha256') != schema_sha
            or not isinstance(binding, dict)
            or set(binding) != _AGENT_REQUEST_FIELDS
            or not isinstance(links, list) or not links
            or not isinstance(recovery, list)):
        raise ProvenanceRefused(
            f'{purpose}: acceptance 缺完整 stdout/session/request/成本身份')
    expected_binding = {
        'node': ctx.node,
        'chapter': None,
        'purpose': purpose,
        'system_sha256': _sha(system.encode('utf-8')),
        'user_sha256': _sha(user.encode('utf-8')),
        'schema_sha256': schema_sha,
        'requested_model': profile['model'],
        'requested_effort': profile['reasoning_effort'],
        'requested_output_token_ceiling': _positive_ceiling(max_tokens),
        'protocol_version': SESSION_PROTOCOL_VERSION,
    }
    if binding != expected_binding:
        raise ProvenanceRefused(f'{purpose}: accepted_request 与当前请求漂移')
    if acceptance.get('subject_sha256') != _sha(expected_binding):
        raise ProvenanceRefused(f'{purpose}: logical subject SHA-256 不闭合')
    _validate_session_turn(
        ctx, purpose=purpose, acceptance=acceptance, profile=profile,
        system=system, user=user)
    if normalized_text != normalize_json_text(accepted_text):
        raise ProvenanceRefused(f'{purpose}: normalized stdout 不是原文唯一归一结果')
    if response_sha != _sha(accepted_text.encode('utf-8')):
        raise ProvenanceRefused(f'{purpose}: stdout SHA 与 ledger response 不闭合')
    try:
        parsed_payload = json.loads(normalized_text)
    except json.JSONDecodeError as exc:
        raise ProvenanceRefused(f'{purpose}: normalized stdout 不是合法 JSON：{exc}') from exc
    if not isinstance(parsed_payload, dict):
        raise ProvenanceRefused(f'{purpose}: normalized stdout 不是 JSON object')
    missing = missing_required_fields(
        parsed_payload, required_top_level(response_model), response_model)
    if missing:
        raise ProvenanceRefused(f'{purpose}: stdout 当前缺必填或非空键 {missing}')
    try:
        typed = response_model.model_validate(parsed_payload)
        typed_payload = typed.model_dump(by_alias=True)
    except ValidationError as exc:
        raise ProvenanceRefused(
            f'{purpose}: stdout 不再通过当前 {response_model.__name__}：{exc}') from exc
    payload = dict(response_payload)
    if typed_payload != payload:
        raise ProvenanceRefused(
            f'{purpose}: stdout 经当前 schema 归一后与验证后 payload 不闭合')

    normalized_links: list[dict[str, Any]] = []
    attempt_ids: list[str] = []
    fingerprints: list[str] = []
    for link in links:
        if (not isinstance(link, dict) or set(link) != _LEDGER_IDENTITY_FIELDS
                or not _is_attempt_id(link.get('attempt_id'))
                or not _is_sha256(link.get('request_fingerprint'))):
            raise ProvenanceRefused(f'{purpose}: attempt_links 身份不完整')
        aid = str(link['attempt_id'])
        fingerprint = str(link['request_fingerprint'])
        if (link.get('request_model') != profile['model']
                or link.get('reasoning_effort') != profile['reasoning_effort']
                or link.get('thinking') is not None
                or link.get('json_mode') is not True
                or link.get('max_tokens') is not None):
            raise ProvenanceRefused(
                f'{purpose}: attempt={aid} 混入 legacy thinking/provider max_tokens')
        resolve_ledger_model_identity(profile, link.get('model'))
        normalized_links.append(dict(link))
        attempt_ids.append(aid)
        fingerprints.append(fingerprint)
    if len(set(attempt_ids)) != len(attempt_ids) or attempt_id not in attempt_ids:
        raise ProvenanceRefused(f'{purpose}: contributing attempts 不唯一或漏 accepted attempt')
    normalized_links.sort(key=lambda row: (
        str(row['attempt_id']), str(row['request_fingerprint'])))
    expected_recovery = [
        str(row['attempt_id']) for row in normalized_links
        if row['attempt_id'] != attempt_id
    ]
    if (recovery != expected_recovery or turn_index != len(normalized_links)
            or acceptance.get('request_mode') != (
                'original' if turn_index == 1 else 'agent_session_resume')):
        raise ProvenanceRefused(f'{purpose}: turn/recovery/attempt_links 不闭合')
    if claim_prior:
        ctx.ledger.claim_prior_attempts(
            attempt_ids, sid=ctx.sid, node=ctx.node, purpose=purpose,
            request_fingerprints=fingerprints)
    for link in normalized_links:
        aid = str(link['attempt_id'])
        if link != ctx.ledger.request_identity_for_attempt(aid):
            raise ProvenanceRefused(f'{purpose}: attempt={aid} 与成本账不闭合')
    accepted_link = next(
        row for row in normalized_links if row['attempt_id'] == attempt_id)
    validate_checkpoint_model_identity(
        profile,
        checkpoint_model=model,
        actual_model=accepted_link.get('model'),
        actual_is_ledger=True,
    )
    if (accepted_link.get('request_fingerprint') != request_fingerprint
            or accepted_link.get('response_sha256') != response_sha):
        raise ProvenanceRefused(f'{purpose}: accepted attempt 与成本账响应不闭合')
    return {
        'acceptance': {
            **{key: acceptance[key] for key in _AGENT_ACCEPTANCE_FIELDS},
            'profile': profile,
            'accepted_request': dict(binding),
            'attempt_links': normalized_links,
            'recovery_from_attempts': expected_recovery,
        },
        'parsed_payload': parsed_payload,
        'response_payload': payload,
        'typed': typed,
    }


def _checkpoint_hash(payload: Mapping[str, Any]) -> str:
    return _sha({
        key: value for key, value in payload.items()
        if key not in ('created_at', 'checkpoint_sha256')
    })


def _validate_checkpoint_record(
        ctx: Any, path: Path, record: Mapping[str, Any], *, run_sha: str,
) -> dict[str, Any]:
    if set(record) != _CHECKPOINT_EXTRA_FIELDS | _AGENT_ACCEPTANCE_FIELDS:
        raise ArtifactUnreadable(f'{path} 字段不完整或含未登记字段')
    if record.get('checkpoint_sha256') != _checkpoint_hash(record):
        raise ArtifactUnreadable(f'{path} 的 checkpoint_sha256 不闭合')
    expected = {
        'version': VERSION, 'run_id': check_run_id(ctx.run_id),
        'sid': ctx.sid, 'node': ctx.node, 'code': ctx.code,
        'run_sha256': run_sha,
    }
    if {key: record.get(key) for key in expected} != expected:
        raise ProvenanceRefused(f'{path} 不属于当前 run/sid/node/code/run.json')
    role = record.get('role')
    if (role not in JUDGE_ROLES or record.get('purpose') != _purpose(str(role))
            or not _is_sha256(record.get('input_sha256'))
            or not isinstance(record.get('response_model'), str)
            or not record['response_model']):
        raise ArtifactUnreadable(f'{path} 的 judge/input/response model 身份不完整')
    run_path = _run_root(ctx) / RUN_FILE
    run = _validate_run_record(ctx, _read_json(run_path))
    if (_sha(run_path.read_bytes()) != run_sha
            or record.get('input_sha256') != run.get('input_sha256')):
        raise ProvenanceRefused(f'{path} 的 run/input 绑定漂移')
    profile = _resolved_profile(
        ctx, record.get('profile'), label=f'{path}: acceptance')
    policy = _validate_policy(ctx, run.get('request_policy'))
    phases = [
        row for row in policy['phases']
        if isinstance(row, dict) and row.get('phase') == role
    ]
    if len(phases) != 1:
        raise ArtifactUnreadable(f'{path} 的 judge policy 不唯一')
    phase = phases[0]
    request = _role_request(run, str(role))
    binding = record.get('accepted_request')
    expected_binding = {
        'node': ctx.node, 'chapter': None, 'purpose': _purpose(str(role)),
        'system_sha256': request['system_sha256'],
        'user_sha256': request['user_sha256'],
        'schema_sha256': request['response_schema_sha256'],
        'requested_model': profile['model'],
        'requested_effort': profile['reasoning_effort'],
        'requested_output_token_ceiling': phase['requested_output_token_ceiling'],
        'protocol_version': SESSION_PROTOCOL_VERSION,
    }
    links = record.get('attempt_links')
    attempt_id = record.get('attempt_id')
    accepted_text = record.get('accepted_text')
    normalized_text = record.get('normalized_text')
    turn_index = record.get('accepted_turn_index')
    recovery = record.get('recovery_from_attempts')
    if (record.get('acceptance_protocol') != SESSION_PROTOCOL_VERSION
            or record.get('accepted_channel') != 'content'
            or record.get('salvaged') is not False
            or record.get('model') != profile['model']
            or record.get('response_model') != phase['response_model']
            or record.get('accepted_schema_sha256')
            != phase['response_schema_sha256']
            or binding != expected_binding
            or record.get('subject_sha256') != _sha(expected_binding)
            or not _is_attempt_id(attempt_id)
            or not isinstance(accepted_text, str)
            or not isinstance(normalized_text, str)
            or normalized_text != normalize_json_text(accepted_text)
            or record.get('ledger_response_sha256')
            != _sha(accepted_text.encode('utf-8'))
            or not isinstance(record.get('session_run_id'), str)
            or not record['session_run_id']
            or not isinstance(record.get('provider_session_id'), str)
            or not record['provider_session_id']
            or not _is_sha256(record.get('accepted_prompt_sha256'))
            or not isinstance(turn_index, int) or isinstance(turn_index, bool)
            or turn_index < 1
            or not isinstance(links, list) or not links
            or not isinstance(recovery, list)):
        raise ProvenanceRefused(f'{path} 的 agent acceptance 与 run policy 漂移')
    identities: list[dict[str, Any]] = []
    for link in links:
        if (not isinstance(link, dict) or set(link) != _LEDGER_IDENTITY_FIELDS
                or not _is_attempt_id(link.get('attempt_id'))
                or not _is_sha256(link.get('request_fingerprint'))):
            raise ArtifactUnreadable(f'{path} 的 attempt_links 身份不完整')
        resolve_ledger_model_identity(profile, link.get('model'))
        identities.append(dict(link))
    accepted = [row for row in identities if row['attempt_id'] == attempt_id]
    expected_recovery = sorted(
        str(row['attempt_id']) for row in identities
        if row['attempt_id'] != attempt_id)
    if len(accepted) == 1:
        validate_checkpoint_model_identity(
            profile,
            checkpoint_model=record.get('model'),
            actual_model=accepted[0].get('model'),
            actual_is_ledger=True,
        )
    if (len(accepted) != 1
            or accepted[0].get('request_fingerprint')
            != record.get('request_fingerprint')
            or accepted[0].get('response_sha256')
            != record.get('ledger_response_sha256')
            or recovery != expected_recovery
            or turn_index != len(identities)
            or record.get('request_mode') != (
                'original' if turn_index == 1 else 'agent_session_resume')):
        raise ProvenanceRefused(f'{path} 的 accepted attempt 与 attempt_links 漂移')
    for name in ('parsed_payload', 'response_payload'):
        payload = record.get(name)
        if (not isinstance(payload, dict)
                or record.get(f'{name}_sha256') != _sha(payload)):
            raise ArtifactUnreadable(f'{path} 的 {name} SHA-256 不闭合')
    return dict(record)


def write_checkpoint(
        ctx: Any, *, role: str, input_sha256: str, system: str, user: str,
        response_model: type[BaseModel], max_tokens: int,
        response_payload: Mapping[str, Any], acceptance: Mapping[str, Any],
) -> Path:
    """保存一名判官的验证后 stdout；同 role 首份不可变，重写只能逐字幂等。"""
    _validate_role_run(
        ctx, role=role, input_sha256=input_sha256, system=system, user=user,
        response_model=response_model, max_tokens=max_tokens)
    run_path = _run_root(ctx) / RUN_FILE
    run_sha = _sha(run_path.read_bytes())
    validated = _validate_acceptance(
        ctx, role=role, system=system, user=user,
        response_model=response_model, max_tokens=max_tokens,
        response_payload=response_payload, acceptance=acceptance,
        claim_prior=False)
    normalized_acceptance = validated['acceptance']
    record: dict[str, Any] = {
        'version': VERSION, 'created_at': _now(),
        'run_id': check_run_id(ctx.run_id), 'sid': ctx.sid,
        'node': ctx.node, 'code': ctx.code, 'run_sha256': run_sha,
        'role': _role(role), 'purpose': _purpose(role),
        'input_sha256': input_sha256,
        'response_model': response_model.__name__,
        **normalized_acceptance,
        'parsed_payload': validated['parsed_payload'],
        'parsed_payload_sha256': _sha(validated['parsed_payload']),
        'response_payload': validated['response_payload'],
        'response_payload_sha256': _sha(validated['response_payload']),
    }
    record['checkpoint_sha256'] = _checkpoint_hash(record)
    target = paths.stage6_semantic_checkpoint_path(
        ctx.novel_dir, ctx.run_id, role)
    with _LOCK:
        with process_append_lock(target):
            if target.is_file():
                current = _validate_checkpoint_record(
                    ctx, target, _read_json(target), run_sha=run_sha)
                comparable = {
                    key: value for key, value in current.items()
                    if key != 'created_at'
                }
                expected = {
                    key: value for key, value in record.items()
                    if key != 'created_at'
                }
                if comparable != expected:
                    raise ProvenanceRefused(
                        f'{target} 已绑定另一份 accepted response；拒绝覆盖')
                return target
            _atomic_json(target, record)
    return target


def restore_checkpoint(
        ctx: Any, *, role: str, input_sha256: str, system: str, user: str,
        response_model: type[M], max_tokens: int,
        validator: Callable[[M, ChatResponse], str | None] | None = None,
) -> tuple[M, dict[str, Any]] | None:
    """恢复一名判官；重跑当前 schema/validator，并返回 typed 值与完整 acceptance。"""
    target = paths.stage6_semantic_checkpoint_path(
        ctx.novel_dir, ctx.run_id, role)
    if not target.is_file():
        return None
    _validate_role_run(
        ctx, role=role, input_sha256=input_sha256, system=system, user=user,
        response_model=response_model, max_tokens=max_tokens)
    run_path = _run_root(ctx) / RUN_FILE
    run_sha = _sha(run_path.read_bytes())
    record = _validate_checkpoint_record(
        ctx, target, _read_json(target), run_sha=run_sha)
    if (record.get('role') != role
            or record.get('purpose') != _purpose(role)
            or record.get('input_sha256') != input_sha256
            or record.get('response_model') != response_model.__name__):
        raise ProvenanceRefused(f'{target} 的 role/input/schema 与当前请求漂移')
    acceptance = {
        key: record[key] for key in _AGENT_ACCEPTANCE_FIELDS
    }
    validated = _validate_acceptance(
        ctx, role=role, system=system, user=user,
        response_model=response_model, max_tokens=max_tokens,
        response_payload=record['response_payload'], acceptance=acceptance,
        claim_prior=True)
    if validated['parsed_payload'] != record['parsed_payload']:
        raise ProvenanceRefused(f'{target} 的 stdout 与封存 parsed payload 漂移')
    typed = validated['typed']
    if validator is not None:
        why = validator(typed, ChatResponse(
            content=str(acceptance['accepted_text']), finish_reason='stop',
            usage={}, model=str(acceptance['model'])))
        if why is not None:
            raise ProvenanceRefused(
                f'{_purpose(role)}: 恢复候选不再通过当前业务 validator：{why}')
    return typed, validated['acceptance']


def _ownership_thread_lock(path: Path) -> threading.RLock:
    key = os.path.normcase(str(path.resolve()))
    with _OWNERSHIP_REGISTRY_LOCK:
        lock = _OWNERSHIP_THREAD_LOCKS.get(key)
        if lock is None:
            lock = _OWNERSHIP_THREAD_LOCKS[key] = threading.RLock()
        return lock


def _ownership_key(novel_dir: Path) -> str:
    return os.path.normcase(str(
        paths.stage6_semantic_commit_lock_path(novel_dir).resolve()))


@contextmanager
def commit_ownership(novel_dir: Path):
    """同一本书选择 semantic seq 与提交尾窗共用的线程/进程所有权锁。"""
    target = paths.stage6_semantic_commit_lock_path(novel_dir)
    key = _ownership_key(Path(novel_dir))
    held = getattr(_OWNERSHIP_LOCAL, 'depths', None)
    if held is None:
        held = _OWNERSHIP_LOCAL.depths = {}
    with _ownership_thread_lock(target):
        # 同一线程的 producer helper 允许嵌套进入，但不能在 Windows 上对同一
        # byte-range 再拿一只新文件句柄；第二次 msvcrt.locking 会自锁到超时。
        if held.get(key, 0):
            held[key] += 1
            try:
                yield
            finally:
                held[key] -= 1
            return
        with process_append_lock(target):
            held[key] = 1
            try:
                yield
            finally:
                del held[key]


def _require_ownership(ctx: Any) -> None:
    held = getattr(_OWNERSHIP_LOCAL, 'depths', {})
    if held.get(_ownership_key(Path(ctx.novel_dir)), 0) < 1:
        raise ProvenanceRefused(
            'Stage6 semantic commit 必须在 commit_ownership(novel_dir) '
            '临界区内完成 seq 选择、写盘、readback、adoption 与 committed')


def _commit_hash(payload: Mapping[str, Any]) -> str:
    return _sha({
        key: value for key, value in payload.items()
        if key not in ('prepared_at', 'committed_at', 'commit_sha256')
    })


def _validate_commit_state(ctx: Any, target: Path) -> dict[str, Any]:
    record = _read_json(target)
    if set(record) != _COMMIT_FIELDS:
        raise ArtifactUnreadable(f'{target} 字段不完整或含未登记字段')
    if record.get('state') not in ('prepared', 'committed'):
        raise ArtifactUnreadable(f'{target} 缺合法 state=prepared|committed')
    if record.get('commit_sha256') != _commit_hash(record):
        raise ArtifactUnreadable(f'{target} 的 commit_sha256 不闭合')
    expected = {
        'version': VERSION, 'run_id': check_run_id(ctx.run_id),
        'sid': ctx.sid, 'node': ctx.node, 'code': ctx.code,
    }
    if {key: record.get(key) for key in expected} != expected:
        raise ProvenanceRefused(f'{target} 不属于当前 run/sid/node/code')
    run_path = _run_root(ctx) / RUN_FILE
    try:
        run_sha = _sha(run_path.read_bytes())
    except OSError as exc:
        raise ProvenanceRefused(f'{target} 绑定的 run.json 不可读：{exc}') from exc
    if record.get('run_sha256') != run_sha:
        raise ProvenanceRefused(f'{target} 绑定的 run.json 已漂移')
    _validate_run_record(ctx, _read_json(run_path))
    if (not isinstance(record.get('artifact'), str)
            or _SEMANTIC_ARTIFACT_RE.fullmatch(record['artifact']) is None
            or not isinstance(record.get('artifact_fingerprint'), str)
            or len(record['artifact_fingerprint']) != 10
            or any(char not in '0123456789abcdef'
                   for char in record['artifact_fingerprint'])
            or not _is_sha256(record.get('artifact_sha256'))
            or not _is_sha256(record.get('finalization_sha256'))):
        raise ArtifactUnreadable(f'{target} 缺完整 artifact/finalization 身份')
    attempts = record.get('attempts')
    rows = record.get('adoptions')
    if not isinstance(attempts, list) or not isinstance(rows, list) or len(rows) != 2:
        raise ArtifactUnreadable(f'{target} 必须恰有两份 judge adoption')
    seen_roles: set[str] = set()
    seen_attempts: set[str] = set()
    for row in rows:
        if not isinstance(row, dict) or set(row) != {
                'role', 'purpose', 'attempt_id', 'request_identity'}:
            raise ArtifactUnreadable(f'{target} 的 adoption 字段漂移')
        role = row.get('role')
        attempt_id = row.get('attempt_id')
        identity = row.get('request_identity')
        if (role not in JUDGE_ROLES or role in seen_roles
                or row.get('purpose') != _purpose(str(role))
                or not _is_attempt_id(attempt_id)
                or attempt_id in seen_attempts
                or not isinstance(identity, dict)
                or set(identity) != _LEDGER_IDENTITY_FIELDS
                or identity.get('attempt_id') != attempt_id):
            raise ArtifactUnreadable(f'{target} 的 adoption 身份不闭合')
        checkpoint_path = paths.stage6_semantic_checkpoint_path(
            ctx.novel_dir, ctx.run_id, str(role))
        checkpoint = _validate_checkpoint_record(
            ctx, checkpoint_path, _read_json(checkpoint_path), run_sha=run_sha)
        validate_checkpoint_model_identity(
            checkpoint.get('profile'),
            checkpoint_model=checkpoint.get('model'),
            actual_model=identity.get('model'),
            actual_is_ledger=True,
        )
        if (checkpoint.get('attempt_id') != attempt_id
                or checkpoint.get('purpose') != row.get('purpose')
                or checkpoint.get('request_fingerprint')
                != identity.get('request_fingerprint')
                or checkpoint.get('ledger_response_sha256')
                != identity.get('response_sha256')):
            raise ProvenanceRefused(
                f'{target} 的 judge {role} adoption 与不可变 checkpoint 漂移')
        seen_roles.add(str(role))
        seen_attempts.add(str(attempt_id))
    if seen_roles != set(JUDGE_ROLES) or attempts != sorted(seen_attempts):
        raise ArtifactUnreadable(f'{target} 的 A/B attempts 与 adoptions 不闭合')
    if (record['state'] == 'prepared'
            and (not isinstance(record.get('prepared_at'), str)
                 or record.get('committed_at') is not None)):
        raise ArtifactUnreadable(f'{target} 的 prepared 时间字段不闭合')
    if (record['state'] == 'committed'
            and (not isinstance(record.get('prepared_at'), str)
                 or not isinstance(record.get('committed_at'), str))):
        raise ArtifactUnreadable(f'{target} 的 committed 时间字段不闭合')
    return record


def _commit_state(ctx: Any) -> dict[str, Any] | None:
    target = _run_root(ctx) / COMMIT_FILE
    return _validate_commit_state(ctx, target) if target.is_file() else None


def prepared(ctx: Any) -> dict[str, Any] | None:
    """返回已验证的 prepared 尾窗；它不代表正式产物已经交付。"""
    record = _commit_state(ctx)
    return record if record is not None and record.get('state') == 'prepared' else None


def committed(ctx: Any) -> dict[str, Any] | None:
    """只返回完成正式 readback 与两条 adoption 后的 committed journal。"""
    record = _commit_state(ctx)
    return record if record is not None and record.get('state') == 'committed' else None


def _refuse_foreign_commit_reservation(
        ctx: Any, artifact: str, *, read_only: bool = False,
) -> None:
    """prepared 跨进程释放锁后仍保留 seq 所有权，直到同 run 补完尾窗。

    只靠活锁不够：进程可能死在 prepare 与正式 write 之间。若另一个 run 随即
    取得锁并复用同一 seq，旧 run 的有效付费结果会永久失去恢复位。因而同一本书
    任一 foreign prepared 都会阻止新 journal；foreign committed 只在调用方仍选到
    同一 artifact 时证明 seq 选择已过期。
    """
    current_root = (paths.stage6_semantic_run_path(ctx.novel_dir, ctx.run_id)
                    if read_only else _run_root(ctx))
    current = (current_root / COMMIT_FILE).resolve()
    for target in _history_files(ctx, f'*/semantic/{COMMIT_FILE}'):
        foreign_run_id = target.parent.parent.name
        if foreign_run_id == ctx.run_id or target.resolve() == current:
            continue
        try:
            check_run_id(foreign_run_id)
            with nullcontext() if read_only else process_append_lock(target):
                record = _read_json(target)
        except (ValueError, OSError) as exc:
            raise ProvenanceRefused(
                f'Stage6 foreign commit journal {target} 不可验证：{exc}') from exc
        if (set(record) != _COMMIT_FIELDS
                or record.get('commit_sha256') != _commit_hash(record)
                or record.get('run_id') != foreign_run_id
                or record.get('sid') != ctx.sid
                or record.get('node') != 'stage6.semantic'
                or record.get('state') not in ('prepared', 'committed')):
            raise ArtifactUnreadable(
                f'Stage6 foreign commit journal {target} 身份/哈希不闭合')
        if record['state'] == 'prepared':
            raise ProvenanceRefused(
                f'Stage6 run={foreign_run_id} 仍持有 prepared semantic seq；'
                '必须先用原 run 补完提交尾窗，不能让新 run 越过它')
        if record.get('artifact') == artifact:
            raise ProvenanceRefused(
                f'Stage6 artifact={artifact} 已由 run={foreign_run_id} committed；'
                '当前 seq 选择已过期')


def refuse_foreign_semantic_prepared(ctx: Any) -> None:
    """供共用 Stage6 锁内的 hook producer 拒绝 semantic prepared 尾窗。"""
    _require_ownership(ctx)
    for target in _history_files(ctx, f'*/semantic/{COMMIT_FILE}'):
        foreign_run_id = target.parent.parent.name
        # 即使 run_id 相同，semantic 与 hook 仍是两个独立写 lane：semantic
        # prepared 已冻结当前 hook 输入，hook 不能借“同 run”越过尾窗。
        try:
            check_run_id(foreign_run_id)
            with process_append_lock(target):
                record = _read_json(target)
        except (ValueError, OSError) as exc:
            raise ProvenanceRefused(
                f'Stage6 semantic commit journal {target} 不可验证：{exc}') from exc
        if (set(record) != _COMMIT_FIELDS
                or record.get('commit_sha256') != _commit_hash(record)
                or record.get('run_id') != foreign_run_id
                or record.get('sid') != ctx.sid
                or record.get('node') != 'stage6.semantic'
                or record.get('state') not in ('prepared', 'committed')):
            raise ArtifactUnreadable(
                f'Stage6 semantic commit journal {target} 身份/哈希不闭合')
        if record['state'] == 'prepared':
            raise ProvenanceRefused(
                f'Stage6 semantic run={foreign_run_id} 仍持有 prepared 尾窗；'
                '必须先用原 run 补完，hook 不能改写它冻结的输入')


def _refuse_foreign_hook_prepared(ctx: Any) -> None:
    """semantic 提交前拒绝另一 hook run 的通用 prepared journal。"""
    from novel.llm import agent_runs

    for target in _history_files(ctx, f'*/hook/{agent_runs.COMMIT_FILE}'):
        run_id = target.parent.parent.name
        # 同一 parent/run 下的 hook prepared 也会冻结 semantic 输入；这里只
        # 比的是跨 lane 所有权，不能套用同 lane 幂等恢复的 run_id 豁免。
        record = agent_runs.validate_foreign_commit_envelope(
            target, label='Stage6 hook foreign commit', run_id=run_id,
            sid=ctx.sid, node='stage6.hook')
        if record['state'] == 'prepared':
            raise ProvenanceRefused(
                f'Stage6 hook run={run_id} 仍持有 prepared 尾窗；'
                '必须先用原 run 补完，semantic 不能冻结旧 hook 输入')


def inspect_foreign_commit_reservations(ctx: Any, artifact: str) -> None:
    """只读预约快照；不拿写锁、不迁移 legacy 文件，提交时仍须锁内重验。"""
    if (not isinstance(artifact, str)
            or _SEMANTIC_ARTIFACT_RE.fullmatch(artifact) is None):
        raise ProvenanceRefused('Stage6 commit artifact 必须是 stage6/semantic_<正整数>.json')
    _refuse_foreign_hook_prepared(ctx)
    _refuse_foreign_commit_reservation(ctx, artifact, read_only=True)


def refuse_foreign_commit_reservations(ctx: Any, artifact: str) -> None:
    """在全局所有权锁内证明当前 semantic seq 没被另一 run 预约或提交。

    legacy LlmClient 不写本 run journal，但仍必须调用本口；否则它能在 agent
    prepared 崩溃尾窗中抢走同一 seq，让那两份已付费 checkpoint 无法恢复。
    """
    _require_ownership(ctx)
    if (not isinstance(artifact, str)
            or _SEMANTIC_ARTIFACT_RE.fullmatch(artifact) is None):
        raise ProvenanceRefused(
            'Stage6 commit artifact 必须是 stage6/semantic_<正整数>.json')
    _refuse_foreign_hook_prepared(ctx)
    _refuse_foreign_commit_reservation(ctx, artifact)


def prepare_commit(
        ctx: Any, *, artifact: str, artifact_payload: Mapping[str, Any],
        finalization_payload: Mapping[str, Any], adoptions: Mapping[str, str],
) -> dict[str, Any]:
    """正式写盘前冻结 typed payload 与 A/B accepted attempt 映射。"""
    _require_ownership(ctx)
    if set(adoptions) != set(JUDGE_ROLES):
        raise ProvenanceRefused('Stage6 commit adoptions 必须恰好是 A/B')
    refuse_foreign_commit_reservations(ctx, artifact)
    run_path = _run_root(ctx) / RUN_FILE
    _validate_run_record(ctx, _read_json(run_path))
    run_sha = _sha(run_path.read_bytes())
    rows: list[dict[str, Any]] = []
    for role in JUDGE_ROLES:
        purpose = _purpose(role)
        attempt_id = adoptions[role]
        if not _is_attempt_id(attempt_id):
            raise ProvenanceRefused(f'{purpose}: adoption attempt_id 不合法')
        if attempt_id not in ctx.ledger.attempt_ids_for_purpose(purpose):
            raise ProvenanceRefused(f'{purpose}: attempt 不属于当前 ledger purpose')
        checkpoint_path = paths.stage6_semantic_checkpoint_path(
            ctx.novel_dir, ctx.run_id, role)
        checkpoint = _validate_checkpoint_record(
            ctx, checkpoint_path, _read_json(checkpoint_path), run_sha=run_sha)
        if (checkpoint.get('attempt_id') != attempt_id
                or checkpoint.get('purpose') != purpose):
            raise ProvenanceRefused(f'{purpose}: adoption 与不可变 checkpoint 漂移')
        identity = ctx.ledger.request_identity_for_attempt(attempt_id)
        if set(identity) != _LEDGER_IDENTITY_FIELDS:
            raise ProvenanceRefused(f'{purpose}: ledger 请求身份字段漂移')
        validate_checkpoint_model_identity(
            checkpoint.get('profile'),
            checkpoint_model=checkpoint.get('model'),
            actual_model=identity.get('model'),
            actual_is_ledger=True,
        )
        if (checkpoint.get('request_fingerprint')
                != identity.get('request_fingerprint')
                or checkpoint.get('ledger_response_sha256')
                != identity.get('response_sha256')):
            raise ProvenanceRefused(
                f'{purpose}: checkpoint 与当前 ledger 响应身份不闭合')
        rows.append({
            'role': role, 'purpose': purpose, 'attempt_id': attempt_id,
            'request_identity': identity,
        })
    attempts = sorted(str(value) for value in adoptions.values())
    if len(set(attempts)) != 2:
        raise ProvenanceRefused('Stage6 A/B 不能采纳同一个 attempt/session 响应')
    candidate = dict(artifact_payload)
    finalization = dict(finalization_payload)
    payload: dict[str, Any] = {
        'version': VERSION, 'state': 'prepared', 'prepared_at': _now(),
        'committed_at': None, 'run_id': check_run_id(ctx.run_id),
        'sid': ctx.sid, 'node': ctx.node, 'code': ctx.code,
        'run_sha256': run_sha, 'artifact': artifact,
        'artifact_fingerprint': prov.projection_fingerprint(candidate),
        'artifact_sha256': _sha(candidate),
        'finalization_sha256': _sha(finalization),
        'attempts': attempts, 'adoptions': rows,
    }
    payload['commit_sha256'] = _commit_hash(payload)
    target = _run_root(ctx) / COMMIT_FILE
    with _LOCK:
        with process_append_lock(target):
            if target.is_file():
                current = _validate_commit_state(ctx, target)
                comparable = {
                    key: value for key, value in current.items()
                    if key not in ('state', 'prepared_at', 'committed_at', 'commit_sha256')
                }
                expected = {
                    key: value for key, value in payload.items()
                    if key not in ('state', 'prepared_at', 'committed_at', 'commit_sha256')
                }
                if comparable != expected:
                    raise ProvenanceRefused(
                        f'{target} 已绑定另一份 artifact/adoption 集合；拒绝覆盖')
                return current
            _atomic_json(target, payload)
    return payload


def verify_commit_artifact(
        record: Mapping[str, Any], artifact_payload: Mapping[str, Any],
) -> bool:
    """正式 typed readback 是否逐 canonical byte 命中 prepared journal。"""
    return record.get('artifact_sha256') == _sha(dict(artifact_payload))


def verify_commit_finalization(
        record: Mapping[str, Any], finalization_payload: Mapping[str, Any],
) -> bool:
    """提交时的 seq/输入/上游投影是否仍与 prepare 时闭合。"""
    return record.get('finalization_sha256') == _sha(dict(finalization_payload))


def restore_commit_adoptions(
        ctx: Any, record: Mapping[str, Any],
) -> tuple[tuple[str, str], ...]:
    """恢复 A/B attempts 并逐项重验盘上 ledger 请求身份。"""
    target = _run_root(ctx) / COMMIT_FILE
    current = _validate_commit_state(ctx, target)
    if dict(record) != current:
        raise ProvenanceRefused('传入的 Stage6 commit record 不是当前盘上 journal')
    restored: list[tuple[str, str]] = []
    for row in current['adoptions']:
        purpose = str(row['purpose'])
        attempt_id = str(row['attempt_id'])
        identity = row['request_identity']
        assert isinstance(identity, dict)
        ctx.ledger.claim_prior_attempts(
            [attempt_id], sid=ctx.sid, node=ctx.node, purpose=purpose,
            request_fingerprints=[str(identity['request_fingerprint'])])
        if ctx.ledger.request_identity_for_attempt(attempt_id) != identity:
            raise ProvenanceRefused(
                f'{purpose}: commit journal attempt 与盘上 ledger 不闭合')
        restored.append((purpose, attempt_id))
    return tuple(restored)


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
        raise ProvenanceRefused(f'读不了 Stage6 adoption 审计账：{exc}') from exc
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


def _require_two_adoptions(ctx: Any, record: Mapping[str, Any]) -> None:
    events = _read_adoption_events(ctx)
    for expected in record['adoptions']:
        matches = [
            row for row in events
            if (row.get('kind') == 'artifact_adoption'
                and row.get('run_id') == ctx.run_id
                and row.get('sid') == ctx.sid
                and row.get('node') == ctx.node
                and row.get('purpose') == expected['purpose']
                and row.get('attempt_id') == expected['attempt_id']
                and row.get('artifact') == record['artifact']
                and row.get('artifact_fingerprint') == record['artifact_fingerprint']
                and row.get('chapter') is None)
        ]
        if len(matches) != 1:
            raise ProvenanceRefused(
                f"{expected['purpose']}: committed 前必须恰有一条当前 artifact adoption，"
                f'实际 {len(matches)}')


def mark_committed(
        ctx: Any, *, artifact_payload: Mapping[str, Any],
        finalization_payload: Mapping[str, Any],
) -> Path:
    """typed readback 与两条 adoption 都闭合后，原子提升为 committed。"""
    _require_ownership(ctx)
    target = _run_root(ctx) / COMMIT_FILE
    with _LOCK:
        with process_append_lock(target):
            if not target.is_file():
                raise ProvenanceRefused(
                    f'{target} 缺 prepared journal；不能在写盘后补猜来源')
            current = _validate_commit_state(ctx, target)
            if not verify_commit_artifact(current, artifact_payload):
                raise ProvenanceRefused('Stage6 正式 typed readback 与 prepared payload 漂移')
            if not verify_commit_finalization(current, finalization_payload):
                raise ProvenanceRefused('Stage6 提交时输入/seq 与 prepared finalization 漂移')
            _require_two_adoptions(ctx, current)
            if current.get('state') == 'committed':
                return target
            payload = {**current, 'state': 'committed', 'committed_at': _now()}
            payload['commit_sha256'] = _commit_hash(payload)
            _atomic_json(target, payload)
    return target


__all__ = [
    'JUDGE_ROLES', 'RECOVERY_POLICY', 'commit_ownership', 'committed',
    'ensure_run', 'mark_committed', 'prepare_commit', 'prepared',
    'refuse_foreign_commit_reservations',
    'refuse_foreign_semantic_prepared',
    'restore_checkpoint', 'restore_commit_adoptions', 'semantic_experiment_arm',
    'verify_commit_artifact', 'verify_commit_finalization', 'write_checkpoint',
]
