#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
stage2_runs.py —— Stage2 blueprint 的验证后章节检查点与一次性 repair workpack。

这里的状态既不是正式 artifact，也不是普通响应缓存：正式完成度、freshness 与 schema
注册表都不看它；只有**同一个 canonical run_id** 的 Stage2 producer 会在重新执行当前
schema、动态 validator、章节 scope、整书 compile 与联合 HARD 的前提下恢复它。

为什么单独成模块：检查点会直接保存模型响应正文，必须按 sid 隔离在
``_service/runs/<sid>/stage2``，且不能借 ``ArtifactStore`` 冒充正式产物。把所有裸 IO
收进这一处，分层守卫便能给这一种内部状态一个窄豁免，而不必把最长的
``extract.py`` 或 ``client.py`` 整体放进写盘白名单。旧书内 ``_stage2_runs`` 不再接入恢复。
"""
from __future__ import annotations

from novel import runtime_layout
import copy
import hashlib
import json
import os
import tempfile
import threading
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterable, Mapping, NamedTuple, Sequence

from pydantic import BaseModel, ValidationError

from contracts.schema.orchestration import AgentProfile
from novel import agent_session_audit, paths, work_units
from novel.agent_capabilities import validate_profile
from novel.agent_model_identity import (
    resolve_ledger_model_identity, validate_checkpoint_model_identity,
)
from novel.agents.base import render_structured_agent_stdin_bytes
from novel.agents.session_client import (
    SESSION_PROTOCOL_VERSION, SAME_RUN_RESUMABLE_AGENT_FAILURES, agent_json_schema,
    validate_agent_session_request_binding,
)
from novel.config import ENV_KEYS
from novel.appendlog import process_append_lock
from novel.errors import (
    ArtifactUnreadable, ProvenanceRefused, TerminalState, UpstreamMissing,
)
from novel.llm.json_text import normalize_json_text
from novel.run_identity import check_run_id

VERSION = 1
RUN_FILE = 'run.json'
MANIFEST_FILE = 'repair_manifest.json'
RESOLUTION_FILE = 'batch_resolution.json'
COMMITTED_FILE = 'committed.json'
DELIVERY_RECOVERY_FILE = 'delivery_recovery.json'

_LOCK = threading.RLock()
_OWNERSHIP_REGISTRY_LOCK = threading.Lock()
_OWNERSHIP_THREAD_LOCKS: dict[str, threading.RLock] = {}
OWNERSHIP_LOCK_TARGET = '.repair-ownership'
BLUEPRINT_CONTRACT_ENV = ENV_KEYS['stage2_blueprint_contract']
AGENT_PROTOCOL_VERSION = SESSION_PROTOCOL_VERSION

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
_LEGACY_PHASE_FIELDS = {
    'phase', 'response_model', 'response_schema_sha256', 'max_tokens',
    'prefer_content_over_reasoning_salvage',
}
_LEGACY_REQUEST_FIELDS = {
    'request_model', 'reasoning_effort', 'thinking', 'json_mode', 'max_tokens',
    'temperature', 'system_sha256', 'user_sha256', 'request_fingerprint',
}
_LEDGER_IDENTITY_FIELDS = {
    'attempt_id', 'request_fingerprint', 'model', 'request_model',
    'reasoning_effort', 'thinking', 'json_mode', 'max_tokens',
    'response_sha256',
}
#: 只有这些 phase 的响应形状才是完整 Blueprint detail；boundary 响应没有
#: ``fact_atoms``，即使被重签成同一个 response model 也不能进入恢复候选。
_BLUEPRINT_RECOVERY_PHASES = frozenset({'blueprint', 'detail', 'repair'})


class VerifiedBlueprintCheckpoint(NamedTuple):
    """可供 0-wire fact atom 恢复使用的一章已验证响应。

    这里只暴露 typed payload 与不可变身份摘要，不把 checkpoint 文件路径扩散到
    producer。调用方仍须用当前 main/global 重编译，并证明它与 INVALID 业务章逐字段
    闭合后，才能取其中的 ``evidence_phrase``。
    """

    run_id: str
    phase: str
    chapter: int
    purpose: str
    checkpoint_sha256: str
    response_payload_sha256: str
    accepted_attempt_id: str
    accepted_model: str
    response_payload: dict[str, Any]


def _ownership_thread_lock(path: Path) -> threading.RLock:
    key = os.path.normcase(str(path.resolve()))
    with _OWNERSHIP_REGISTRY_LOCK:
        lock = _OWNERSHIP_THREAD_LOCKS.get(key)
        if lock is None:
            lock = _OWNERSHIP_THREAD_LOCKS[key] = threading.RLock()
        return lock


@contextmanager
def repair_ownership_lock(novel_dir: Path):
    """同一本书的 batch 收编与单项 resolution 共用的线程/进程所有权锁。

    锁顺序恒为 ownership thread → ownership process → 各自状态文件锁；调用方
    不得在已持有单项/manifest 文件锁时反向进入这里。
    """
    target = paths.service_stage_runs_dir(novel_dir, 'stage2') / OWNERSHIP_LOCK_TARGET
    with _ownership_thread_lock(target):
        with process_append_lock(target):
            yield


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec='seconds')


def _canonical_bytes(value: Any) -> bytes:
    return json.dumps(
        value, ensure_ascii=False, sort_keys=True, separators=(',', ':'),
    ).encode('utf-8')


def _sha(value: Any) -> str:
    return hashlib.sha256(
        value if isinstance(value, bytes) else _canonical_bytes(value)
    ).hexdigest()


def _is_sha256(value: Any) -> bool:
    return (isinstance(value, str) and len(value) == 64
            and all(ch in '0123456789abcdef' for ch in value))


def _is_attempt_id(value: Any) -> bool:
    return (isinstance(value, str) and len(value) == 16
            and all(ch in '0123456789abcdef' for ch in value))


def _materialized_profile_dict(value: Any, *, label: str) -> dict[str, Any]:
    """证明盘上保存的是当前 registry 认可的已物化 executor profile。

    历史证据只需要回答“当时冻结的是哪一份合法 profile”；它不准备启动
    provider，也不应要求该 profile 等于本次 0 成本读取上下文的配置。当前
    executor 的等同性由下面 ``_resolved_profile_dict`` 单独负责。
    """
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
    return normalized


def _resolved_profile_dict(
        ctx: Any, value: Any, *, label: str,
) -> dict[str, Any]:
    """证明 policy/checkpoint 保存的是当前 executor 的已物化 profile。"""
    normalized = _materialized_profile_dict(value, label=label)

    current = getattr(ctx, 'executor_profile', None)
    if current is None:
        raise ProvenanceRefused(
            f'{label} 声明 agent 协议，但当前 RunContext 没有 executor profile')
    try:
        current_resolved = validate_profile(current, role='executor')
    except (ValidationError, ValueError) as exc:
        raise ProvenanceRefused(
            f'{label} 的当前 executor profile 不可执行：{exc}') from exc
    if current_resolved.model_dump(mode='json') != normalized:
        raise ProvenanceRefused(
            f'{label} 的 resolved profile 与当前 executor 配置漂移')
    return normalized


def _normalize_request_policy(
        ctx: Any, request_policy: Mapping[str, Any], *,
        standalone_historical_evidence: bool = False,
) -> dict[str, Any]:
    """按 discriminator 校验 run policy；没有 discriminator 就是逐字 legacy v1。

    ``standalone_historical_evidence`` 只供 0-wire 历史证据扫描：校验盘上
    profile 自身已物化且仍在 registry 中，但不把它绑定到本次 ``ctx``。其余
    写 checkpoint、live restore 与同 run resume 全部走默认严格分支。
    """
    try:
        normalized = json.loads(_canonical_bytes(dict(request_policy)))
    except (TypeError, ValueError) as exc:
        raise ProvenanceRefused(
            f'Stage2 request policy 不能 canonical JSON 化：{exc}') from exc
    if not isinstance(normalized, dict):
        raise ProvenanceRefused('Stage2 run 缺完整、登记过的 JSON request policy')

    if 'protocol_version' not in normalized:
        # 旧 LlmClient v1 的形状与判据逐字保留；不能借 agent 迁移替旧 run
        # 增删字段，否则盘上 run.json 会被误判为请求漂移。
        required = {
            'model_chain', 'rungs', 'json_mode', 'temperature',
            'recovery_policy', 'phases',
        }
        if (set(normalized) != required
                or not isinstance(normalized.get('model_chain'), list)
                or not normalized['model_chain']
                or not isinstance(normalized.get('rungs'), list)
                or not normalized['rungs']
                or normalized.get('json_mode') is not True
                or normalized.get('temperature') is not None
                or normalized.get('recovery_policy') != 'stage2-blueprint-v1'
                or not isinstance(normalized.get('phases'), list)
                or not normalized['phases']):
            raise ProvenanceRefused(
                'Stage2 run 缺完整、登记过的 JSON request policy')
        return normalized

    if (set(normalized) != _AGENT_POLICY_FIELDS
            or normalized.get('protocol_version') != AGENT_PROTOCOL_VERSION
            or normalized.get('session_isolation') != 'per-purpose-chapter'
            or normalized.get('recovery_policy') != 'stage2-blueprint-v1'):
        raise ProvenanceRefused(
            'Stage2 agent run 缺完整、登记过的 node-executor/v1 request policy')
    if standalone_historical_evidence:
        _materialized_profile_dict(
            normalized.get('profile'), label='历史 Stage2 agent request policy')
    else:
        _resolved_profile_dict(
            ctx, normalized.get('profile'), label='Stage2 agent request policy')
    phases = normalized.get('phases')
    if not isinstance(phases, list) or not phases:
        raise ProvenanceRefused('Stage2 agent request policy 缺非空 phases')
    seen: set[str] = set()
    for row in phases:
        if not isinstance(row, dict) or set(row) != _AGENT_PHASE_FIELDS:
            raise ProvenanceRefused(
                'Stage2 agent request policy phase 字段不完整或含未登记字段')
        phase = row.get('phase')
        ceiling = row.get('requested_output_token_ceiling')
        if (not isinstance(phase, str) or not phase or phase in seen
                or not isinstance(row.get('response_model'), str)
                or not row['response_model']
                or not _is_sha256(row.get('response_schema_sha256'))
                or not isinstance(ceiling, int) or isinstance(ceiling, bool)
                or ceiling <= 0
                or not isinstance(
                    row.get('prefer_content_over_reasoning_salvage'), bool)):
            raise ProvenanceRefused(
                'Stage2 agent request policy phase 身份或 requested ceiling 不合法')
        seen.add(phase)
    return normalized


def _agent_phase_policy(
        ctx: Any, *, phase: str, response_model: type[BaseModel],
) -> tuple[dict[str, Any], int]:
    """从当前不可变 run.json 取该 phase 的 profile/schema/requested ceiling。"""
    run_path = _run_root(ctx) / RUN_FILE
    run = _read_json(run_path)
    expected_run = {
        'run_id': check_run_id(ctx.run_id),
        'sid': ctx.sid,
        'node': ctx.node,
        'code': ctx.code,
    }
    if {key: run.get(key) for key in expected_run} != expected_run:
        raise ProvenanceRefused(
            f'{run_path} 不属于当前 run/sid/node/code，不能铸 agent checkpoint')
    raw_policy = run.get('request_policy')
    if not isinstance(raw_policy, dict):
        raise ArtifactUnreadable(f'{run_path} 缺 request_policy 对象')
    policy = _normalize_request_policy(ctx, raw_policy)
    if (policy.get('protocol_version') != AGENT_PROTOCOL_VERSION
            or run.get('request_policy_sha256') != _sha(policy)):
        raise ProvenanceRefused(
            f'{run_path} 不是当前 node-executor/v1 policy 或 policy SHA 已漂移')
    rows = [
        row for row in policy['phases']
        if isinstance(row, dict) and row.get('phase') == phase
    ]
    if len(rows) != 1:
        raise ProvenanceRefused(
            f'{run_path} 对 phase={phase!r} 必须恰有一份 agent policy')
    row = rows[0]
    # Agent 的身份必须绑定真正下发给 CLI 的 schema。该构造口会剥掉
    # AJV strict 不接受、但不改变分支约束的 discriminator；直接对
    # Pydantic 原始 schema 求 SHA 会在出现 discriminated union 时把
    # 同一份契约误判成漂移。
    current_schema_sha = _sha(agent_json_schema(response_model))
    if (row.get('response_model') != response_model.__name__
            or row.get('response_schema_sha256') != current_schema_sha):
        raise ProvenanceRefused(
            f'{run_path} 的 phase={phase!r} response schema 与当前代码漂移')
    ceiling = row['requested_output_token_ceiling']
    assert isinstance(ceiling, int) and not isinstance(ceiling, bool)
    profile = _resolved_profile_dict(
        ctx, policy.get('profile'), label=f'{run_path}: agent request policy')
    return profile, ceiling


def _validate_agent_session_turn(
        ctx: Any, *, purpose: str, chapter: int,
        acceptance: Mapping[str, Any], profile: Mapping[str, Any],
        system: str, user: str,
) -> None:
    """把 checkpoint 的 accepted attempt 唯一联到真实 session turn 事件。"""
    try:
        events = agent_session_audit.read_events(ctx.novel_dir.parent)
        # ``fold`` 额外证明 started 在先、静态身份不漂移、attempt/provider
        # 没有跨 logical session 重复；checkpoint 写入时不要求 ended。
        agent_session_audit.fold(events)
    except (OSError, UnicodeError, ValueError) as exc:
        raise ProvenanceRefused(
            f'{purpose}: agent session 审计不可验证：{exc}') from exc
    attempt_id = acceptance.get('attempt_id')
    matches = [
        event for event in events
        if event.kind == 'turn' and event.attempt_id == attempt_id
    ]
    if len(matches) != 1:
        raise ProvenanceRefused(
            f'{purpose}: accepted attempt={attempt_id!r} 必须唯一联结一条 session turn，'
            f'实际 {len(matches)}')
    turn = matches[0]
    starts = [
        event for event in events
        if (event.kind == 'started'
            and event.session_run_id == acceptance.get('session_run_id'))
    ]
    if len(starts) != 1:
        raise ProvenanceRefused(
            f'{purpose}: accepted logical session 必须唯一联结一条 started，'
            f'实际 {len(starts)}')
    start = starts[0]
    turn_profile = (
        None if turn.profile is None else turn.profile.model_dump(mode='json'))
    start_profile = (
        None if start.profile is None else start.profile.model_dump(mode='json'))
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
        'role': 'executor',
        'node': ctx.node,
        'chapter': int(chapter),
        'subject_sha256': acceptance.get('subject_sha256'),
        'profile': dict(profile),
        'provider_session_id': None,
        'requested_model': profile.get('model'),
        'actual_model': None,
        'requested_effort': profile.get('reasoning_effort'),
        'actual_effort': None,
        'protocol_version': AGENT_PROTOCOL_VERSION,
        'start_code_version': ctx.code,
        'prompt_sha256': first_turn.prompt_sha256,
        'schema_sha256': first_turn.schema_sha256,
    }
    actual_start = {
        'wire_version': start.wire_version,
        'session_run_id': start.session_run_id,
        'parent_run_id': start.parent_run_id,
        'role': start.role,
        'node': start.node,
        'chapter': start.chapter,
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
        raise ProvenanceRefused(
            f'{purpose}: agent started 事件与当前 logical subject 漂移')
    model_identity = validate_checkpoint_model_identity(
        profile,
        checkpoint_model=acceptance.get('model'),
        actual_model=turn.actual_model,
        cli_version=turn.cli_version,
        require_driver_version=True,
    )
    expected = {
        'wire_version': agent_session_audit.SESSION_AUDIT_WIRE_VERSION,
        'session_run_id': acceptance.get('session_run_id'),
        'parent_run_id': check_run_id(ctx.run_id),
        'role': 'executor',
        'node': ctx.node,
        'chapter': int(chapter),
        'subject_sha256': acceptance.get('subject_sha256'),
        'profile': dict(profile),
        'provider_session_id': acceptance.get('provider_session_id'),
        'turn_index': acceptance.get('accepted_turn_index'),
        'attempt_id': attempt_id,
        'requested_model': profile.get('model'),
        # actual 与 adopted identity 分开：公开协议不回显时必须保持 null，
        # 采用依据由上面的统一 driver capability 判据重算。
        'actual_model': model_identity.actual_model,
        'requested_effort': profile.get('reasoning_effort'),
        'protocol_version': AGENT_PROTOCOL_VERSION,
        'start_code_version': ctx.code,
        'prompt_sha256': acceptance.get('accepted_prompt_sha256'),
        'schema_sha256': acceptance.get('accepted_schema_sha256'),
    }
    actual = {
        'wire_version': turn.wire_version,
        'session_run_id': turn.session_run_id,
        'parent_run_id': turn.parent_run_id,
        'role': turn.role,
        'node': turn.node,
        'chapter': turn.chapter,
        'subject_sha256': turn.subject_sha256,
        'profile': turn_profile,
        'provider_session_id': turn.provider_session_id,
        'turn_index': turn.turn_index,
        'attempt_id': turn.attempt_id,
        'requested_model': turn.requested_model,
        'actual_model': turn.actual_model,
        'requested_effort': turn.requested_effort,
        'protocol_version': turn.protocol_version,
        'start_code_version': turn.start_code_version,
        'prompt_sha256': turn.prompt_sha256,
        'schema_sha256': turn.schema_sha256,
    }
    if actual != expected:
        raise ProvenanceRefused(
            f'{purpose}: accepted checkpoint 与唯一 session turn 身份漂移')
    if (turn.actual_effort is not None
            and turn.actual_effort != profile.get('reasoning_effort')):
        raise ProvenanceRefused(
            f'{purpose}: session turn actual effort 与 resolved profile 漂移')


def _atomic_json(path: Path, payload: Mapping[str, Any]) -> None:
    """同卷临时文件 + replace；完成哨兵永远不会以半份 JSON 出现。"""
    path.parent.mkdir(parents=True, exist_ok=True)
    data = json.dumps(
        dict(payload), ensure_ascii=False, indent=2, sort_keys=True,
    ).encode('utf-8') + b'\n'
    fd, temp_name = tempfile.mkstemp(
        prefix=f'.{path.name}.', suffix='.tmp', dir=str(path.parent),
    )
    try:
        with os.fdopen(fd, 'wb') as fh:
            fh.write(data)
            fh.flush()
            os.fsync(fh.fileno())
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
        raise ArtifactUnreadable(f'Stage2 run 状态 {path} 读不出来：{exc}') from exc
    if not isinstance(value, dict) or value.get('version') != VERSION:
        raise ArtifactUnreadable(
            f'Stage2 run 状态 {path} 不是当前内部 v{VERSION} 对象；不猜旧形状')
    return value


def _materialize_run_root(novel_dir: Path, run_id: str) -> Path:
    """只使用当前 service 落点；旧书内状态保留原位，不迁移或据此重买。"""
    run_id = check_run_id(run_id)
    legacy = paths.legacy_stage2_blueprint_run_path(novel_dir, run_id)
    if legacy.exists():
        raise ProvenanceRefused('旧书内 Stage2 checkpoint 已退役；须配对冻结代码恢复，不能移入当前根')
    return paths.stage2_blueprint_run_path(novel_dir, run_id)


def _run_root(ctx: Any) -> Path:
    return _materialize_run_root(Path(ctx.novel_dir), check_run_id(ctx.run_id))


def _existing_run_state(root: Path) -> tuple[Path, ...]:
    """列出会让主动刷新必须换 ``run_id`` 的既有权威状态。

    ``process_append_lock`` 会先创建 ``*.lock``；临时/锁文件不代表一次逻辑运行
    已经开始，不能拿它们制造假冲突。这里因此只认登记过的 JSON 状态与章节
    checkpoint，而不是递归扫目录里的任意文件。
    """
    candidates = [
        root / RUN_FILE,
        root / MANIFEST_FILE,
        root / RESOLUTION_FILE,
        root / COMMITTED_FILE,
        root / DELIVERY_RECOVERY_FILE,
    ]
    checkpoint_dir = root / 'checkpoints'
    try:
        checkpoints = (
            sorted(checkpoint_dir.glob('*.json'))
            if checkpoint_dir.is_dir() else []
        )
    except OSError as exc:
        raise ProvenanceRefused(
            f'Stage2 run 状态目录 {checkpoint_dir} 无法核验：{exc}') from exc
    return tuple(path for path in [*candidates, *checkpoints] if path.is_file())


def refuse_refresh_reuse(ctx: Any, *, operation: str) -> None:
    """主动重采不得消费或覆盖同一 logical run 的恢复状态。"""
    root = _run_root(ctx)
    evidence = _existing_run_state(root)
    if not evidence:
        return
    names = ', '.join(path.relative_to(root).as_posix() for path in evidence[:4])
    suffix = ' …' if len(evidence) > 4 else ''
    raise ProvenanceRefused(
        f'{operation} 不能复用已有 Stage2 run_id={ctx.run_id}；盘上已有 '
        f'{names}{suffix}。first → batch → resume 才能共用 run_id；主动刷新请换新 '
        '`--run-id`。拒绝覆盖旧 checkpoint 或消费旧 repair decision')


def preflight_refresh(ctx: Any, *, operation: str) -> None:
    """在读取正式输入前区分“新 refresh run / 本轮 resume / 旧 run 复用”。

    详细 subject/request 绑定要等 producer 读完输入后由 :func:`ensure_run`
    核验；这里仅保证普通整跑或 orphan sidecar 不会抢先影响定向刷新入口。
    """
    root = _run_root(ctx)
    evidence = _existing_run_state(root)
    if not evidence:
        return
    run_path = root / RUN_FILE
    if not run_path.is_file():
        refuse_refresh_reuse(ctx, operation=operation)
    current = _read_json(run_path)
    if current.get('refresh') is not True:
        refuse_refresh_reuse(ctx, operation=operation)
    if not human_batch_resolved(ctx.novel_dir, ctx.run_id) \
            and commit_state(ctx) is None:
        raise ProvenanceRefused(
            f'{operation} 的 refresh run_id={ctx.run_id} 已经开始，但尚无已封存、'
            '可验证的 batch resolution；不能把再次执行冒充唯一人工 resume。'
            '先完成 manifest 的整包决定；若进程已进入正式提交尾窗，则必须有完整的 '
            'committed journal；否则对新的主动刷新换新 `--run-id`')


def _request_rows(
        requests: Sequence[tuple[int | None, str, str]],
) -> list[dict[str, Any]]:
    return [
        {
            'chapter': chapter,
            'system_sha256': _sha(system.encode('utf-8')),
            'user_sha256': _sha(user.encode('utf-8')),
        }
        for chapter, system, user in requests
    ]


def ensure_run(
        ctx: Any, *, architecture: str, topology: str,
        requests: Sequence[tuple[int | None, str, str]],
        request_policy: Mapping[str, Any], refresh: bool = False,
        operation: str = 'full', subject_fingerprint: str | None = None,
        resume_flags: Sequence[str] = (),
        input_projections: Mapping[str, str] | None = None,
) -> dict[str, Any]:
    """创建或逐字段核验不可变 run 绑定；输入/prompt 漂移绝不退化成 cache miss。

    ``refresh`` 只改变这一个 run 的起点语义：首次进入必须使用全新 run_id；后续
    只有已封存且通过验证的唯一 batch resolution 才取得 resume 资格，并继续核对
    完全相同的 operation/subject/request 后恢复**本轮新采**的 checkpoint。普通 crash
    不会被猜成人工 resume；普通整跑或另一轮刷新留下的 checkpoint 也不能被
    ``--chapters`` 消费。
    """
    normalized_policy = _normalize_request_policy(ctx, request_policy)
    root = _run_root(ctx)
    target = root / RUN_FILE
    normalized_resume_flags = [str(flag) for flag in resume_flags]
    if any(not flag for flag in normalized_resume_flags):
        raise ProvenanceRefused('Stage2 resume flags 不得含空字符串')
    resume = {
        'node': 'stage2.blueprint',
        'flags': normalized_resume_flags,
        # 架构没有 CLI flag；它由运行时环境选择。把要求写进不可变 run.json，
        # CLI 才能在提交 batch 后明确提醒 operator 复用同一环境，而不是发明参数。
        'required_environment': {BLUEPRINT_CONTRACT_ENV: architecture},
    }
    identity = {
        'version': VERSION,
        'run_id': check_run_id(ctx.run_id),
        'sid': ctx.sid,
        'node': ctx.node,
        'code': ctx.code,
        'architecture': architecture,
        'topology': topology,
        'operation': operation,
        'refresh': bool(refresh),
        'subject_fingerprint': subject_fingerprint,
        'resume': resume,
        'initial_requests_sha256': _sha(_request_rows(requests)),
        'request_policy': normalized_policy,
        'request_policy_sha256': _sha(normalized_policy),
    }
    if input_projections is not None:
        identity['input_projections'] = dict(input_projections)

    def note_pending() -> None:
        if getattr(ctx, 'readonly', False):
            return
        from novel.work_units import budget_for
        for chapter, system, user in requests:
            if chapter is not None:
                digest = _sha(_request_rows([(chapter, system, user)]))
                budget_for(ctx, scope=f'chapter:{chapter}').note_processing(
                    run_id=ctx.run_id, input_sha256=digest, kind='pending',
                    evidence_sha256=identity['initial_requests_sha256'])
    with _LOCK:
        with process_append_lock(target):
            if target.is_file():
                current = _read_json(target)
                if refresh and current.get('refresh') is not True:
                    # 旧整跑 run 即使恰好有同一组 prompt，也不是这轮主动刷新。
                    # 在比较/恢复 checkpoint 之前先给出明确的换身份指令。
                    refuse_refresh_reuse(
                        ctx, operation='Stage2 blueprint 主动重采')
                if (refresh and not human_batch_resolved(
                        ctx.novel_dir, ctx.run_id)
                        and commit_state(ctx) is None):
                    raise ProvenanceRefused(
                        f'Stage2 refresh run_id={ctx.run_id} 尚无已封存、可验证的 '
                        'batch resolution 或提交尾窗 journal；拒绝把再次执行冒充唯一'
                        '人工 resume/幂等收尾')
                comparable = {key: current.get(key) for key in identity}
                if comparable != identity:
                    raise ProvenanceRefused(
                        f'Stage2 run={ctx.run_id} 的代码/契约/输入绑定已漂移；'
                        f'盘上={comparable}，当前={identity}。拒绝把旧章接到新请求上')
                note_pending()
                return current
            if refresh:
                # 没有 run.json 却有 orphan checkpoint/manifest 同样不能当新刷新起点。
                # 反之，空目录只可能是锁/临时文件，不构成一次旧运行。
                refuse_refresh_reuse(
                    ctx, operation='Stage2 blueprint 主动重采')
            payload = {**identity, 'created_at': _now()}
            _atomic_json(target, payload)
            note_pending()
            return payload


def clear_delivery_recovery(ctx: Any) -> None:
    """清掉当前 run 的瞬态交付等待标记；逐次失败事实仍完整留在 ledger。"""
    target = _run_root(ctx) / DELIVERY_RECOVERY_FILE
    with _LOCK:
        with process_append_lock(target):
            try:
                target.unlink()
            except FileNotFoundError:
                pass


def write_delivery_recovery(
    ctx: Any,
    *,
    phase: str,
    chapters: Sequence[int],
    completed_chapters: Iterable[int],
    failures: Sequence[Mapping[str, Any]],
) -> dict[str, Any]:
    """冻结 Stage2 Agent 交付失败的同 run 恢复面，不保存异常正文。"""
    ordered = [int(chapter) for chapter in chapters]
    completed = sorted({int(chapter) for chapter in completed_chapters})
    if not ordered or len(ordered) != len(set(ordered)):
        raise ProvenanceRefused('Stage2 delivery recovery 的章节集合为空或重复')
    if any(chapter not in ordered for chapter in completed):
        raise ProvenanceRefused('Stage2 delivery recovery 的 completed 章越出本批请求')
    normalized_failures: list[dict[str, Any]] = []
    for row in failures:
        chapter = row.get('chapter')
        kind = row.get('error_kind')
        attempts = row.get('attempts')
        status = row.get('status')
        if (not isinstance(chapter, int) or isinstance(chapter, bool)
                or chapter not in ordered or chapter in completed
                or kind not in SAME_RUN_RESUMABLE_AGENT_FAILURES
                or not isinstance(attempts, int) or isinstance(attempts, bool)
                or attempts < 1
                or (status is not None and (
                    not isinstance(status, int) or isinstance(status, bool)
                    or status < 100 or status > 599))):
            raise ProvenanceRefused(
                f'Stage2 delivery recovery failure 身份不合法：{row!r}')
        normalized_failures.append({
            'chapter': chapter,
            'error_kind': kind,
            'status': status,
            'attempts': attempts,
        })
    normalized_failures.sort(key=lambda row: row['chapter'])
    pending = [row['chapter'] for row in normalized_failures]
    if len(pending) != len(set(pending)) or sorted(completed + pending) != sorted(ordered):
        raise ProvenanceRefused(
            'Stage2 delivery recovery 的 completed/pending 未精确覆盖本批章节')

    run_path = _run_root(ctx) / RUN_FILE
    run = _read_json(run_path)
    identity = {
        'run_id': check_run_id(ctx.run_id),
        'sid': ctx.sid,
        'node': ctx.node,
        'code': ctx.code,
    }
    if {key: run.get(key) for key in identity} != identity:
        raise ProvenanceRefused(
            'Stage2 delivery recovery 不能绑定漂移或外来的 run.json')
    policy = run.get('request_policy')
    policy_phases = (
        policy.get('phases') if isinstance(policy, Mapping) else None)
    phase_names = {
        row.get('phase') for row in policy_phases or ()
        if isinstance(row, Mapping)
    }
    if not isinstance(phase, str) or not phase or phase not in phase_names:
        raise ProvenanceRefused(
            'Stage2 delivery recovery 的 phase 未登记在当前 run policy')
    payload = {
        'version': VERSION,
        'state': 'waiting',
        **identity,
        'phase': str(phase),
        'total_chapters': len(ordered),
        'completed_chapters': completed,
        'pending_chapters': pending,
        'failures': normalized_failures,
        'run_sha256': _sha(run_path.read_bytes()),
        'updated_at': _now(),
    }
    target = _run_root(ctx) / DELIVERY_RECOVERY_FILE
    with _LOCK:
        with process_append_lock(target):
            _atomic_json(target, payload)
    return payload


def read_delivery_recovery(
    novel_dir: Path,
    run_id: str,
    *,
    sid: str,
    code: str,
) -> dict[str, Any] | None:
    """读取并严格核验 Supervisor 可据以暂停的 Stage2 恢复凭据。"""
    root = _materialize_run_root(Path(novel_dir), check_run_id(run_id))
    target = root / DELIVERY_RECOVERY_FILE
    if not target.is_file():
        return None
    payload = _read_json(target)
    required = {
        'version', 'state', 'run_id', 'sid', 'node', 'code', 'phase',
        'total_chapters', 'completed_chapters', 'pending_chapters',
        'failures', 'run_sha256', 'updated_at',
    }
    if set(payload) != required or payload.get('state') != 'waiting':
        raise ArtifactUnreadable(
            f'{target} 不是当前 Stage2 delivery recovery 形状')
    expected = {
        'run_id': check_run_id(run_id), 'sid': sid,
        'node': 'stage2.blueprint', 'code': code,
    }
    if {key: payload.get(key) for key in expected} != expected:
        raise ProvenanceRefused(f'{target} 与当前 execution 身份不一致')
    run_path = root / RUN_FILE
    if not run_path.is_file():
        raise ProvenanceRefused(f'{target} 绑定的 run.json 已缺失或漂移')
    run_bytes = run_path.read_bytes()
    if payload.get('run_sha256') != _sha(run_bytes):
        raise ProvenanceRefused(f'{target} 绑定的 run.json 已缺失或漂移')
    run = _read_json(run_path)
    total = payload.get('total_chapters')
    completed = payload.get('completed_chapters')
    pending = payload.get('pending_chapters')
    failures = payload.get('failures')
    phase = payload.get('phase')
    policy = run.get('request_policy')
    policy_phases = (
        policy.get('phases') if isinstance(policy, Mapping) else None)
    phase_names = {
        row.get('phase') for row in policy_phases or ()
        if isinstance(row, Mapping)
    }
    if (not isinstance(phase, str) or not phase or phase not in phase_names
            or not _is_sha256(payload.get('run_sha256'))
            or not isinstance(payload.get('updated_at'), str)
            or not payload['updated_at']
            or not isinstance(total, int) or isinstance(total, bool) or total < 1
            or not isinstance(completed, list) or not isinstance(pending, list)
            or not isinstance(failures, list)
            or any(not isinstance(chapter, int) or isinstance(chapter, bool)
                   or chapter < 1 for chapter in completed + pending)
            or completed != sorted(completed) or pending != sorted(pending)
            or len(completed) + len(pending) != total
            or len(set(completed + pending)) != total
            or set(completed).intersection(pending)
            or len(failures) != len(pending)):
        raise ArtifactUnreadable(f'{target} 的章节计数不闭合')
    normalized_failures: list[dict[str, Any]] = []
    for row in failures:
        if not isinstance(row, dict):
            raise ArtifactUnreadable(f'{target} 的 failures 含非对象')
        chapter = row.get('chapter')
        kind = row.get('error_kind')
        status = row.get('status')
        attempts = row.get('attempts')
        if (set(row) != {'chapter', 'error_kind', 'status', 'attempts'}
                or chapter not in pending
                or kind not in SAME_RUN_RESUMABLE_AGENT_FAILURES
                or not isinstance(attempts, int) or isinstance(attempts, bool)
                or attempts < 1
                or (status is not None and (
                    not isinstance(status, int) or isinstance(status, bool)
                    or status < 100 or status > 599))):
            raise ArtifactUnreadable(f'{target} 的 failure 身份不合法')
        normalized_failures.append(row)
    if sorted(row['chapter'] for row in normalized_failures) != sorted(pending):
        raise ArtifactUnreadable(f'{target} 的 failure 与 pending 章不闭合')
    return payload


def _checkpoint_identity(
        ctx: Any, *, phase: str, chapter: int, purpose: str,
        architecture: str, response_model: str, system: str, user: str,
) -> dict[str, Any]:
    identity = {
        'version': VERSION,
        'run_id': check_run_id(ctx.run_id),
        'sid': ctx.sid,
        'node': ctx.node,
        'code': ctx.code,
        'architecture': architecture,
        'phase': phase,
        'chapter': int(chapter),
        'purpose': purpose,
        'response_model': response_model,
        'system_sha256': _sha(system.encode('utf-8')),
        'user_sha256': _sha(user.encode('utf-8')),
    }
    if architecture == 'plot-blueprint-v1':
        run = _read_json(_run_root(ctx) / RUN_FILE)
        identity['input_projection_sha256'] = (run.get('input_projections') or {}).get(str(chapter))
    return identity


def _checkpoint_hash(payload: Mapping[str, Any]) -> str:
    return _sha({key: value for key, value in payload.items()
                 if key not in ('created_at', 'checkpoint_sha256')})


def plot_checkpoint_progress(ctx: Any, *, run_id: str, chapter: int,
                             input_sha256: str) -> dict[str, Any]:
    """只读校验检查点载体及当前输入。不是已购响应的恢复许可或语义接受证明。"""
    result = dict(chapter_ordinal=chapter, run_id=run_id, state='missing', reason=None)
    root = paths.stage2_blueprint_run_path(ctx.novel_dir, check_run_id(run_id))
    try:
        run = _read_json(root / RUN_FILE)
        if (run.get('architecture') != 'plot-blueprint-v1'
                or run.get('sid') != ctx.sid or run.get('run_id') != run_id):
            raise ProvenanceRefused('章节运行记录的书或契约身份不一致')
        projected = (run.get('input_projections') or {}).get(str(chapter))
        if projected is None:
            return {**result, 'state': 'unver', 'reason': '运行记录缺章节消费身份'}
        if projected != input_sha256:
            return {**result, 'state': 'expired', 'reason': '当前原文或共享输入已变化'}
        phase = 'repair' if run.get('operation') in ('chapters-refresh', 'commander-rework') else 'blueprint'
        target = paths.stage2_blueprint_checkpoint_path(ctx.novel_dir, run_id, phase, chapter)
        if not target.exists():
            return result
        record = _read_json(target)
        if (record.get('checkpoint_sha256') != _checkpoint_hash(record)
                or record.get('response_payload_sha256') != _sha(record.get('response_payload'))
                or record.get('run_id') != run_id or record.get('sid') != ctx.sid
                or record.get('chapter') != chapter or record.get('phase') != phase
                or record.get('architecture') != 'plot-blueprint-v1'
                or record.get('input_projection_sha256') != projected):
            raise ArtifactUnreadable('章节检查点内容与身份摘要不闭合')
        if record.get('code') != ctx.code:
            return {**result, 'state': 'unver', 'reason': '代码验证身份变化，等待免费复检'}
        if record.get('scope_violation') is not None:
            return {**result, 'state': 'unver', 'reason': '检查点记录了章节范围问题'}
        return {**result, 'state': 'current'}
    except (TerminalState, OSError, ValueError) as exc:
        return {**result, 'state': 'unver', 'reason': str(exc)}


def _validate_channel_text(
        *, label: str, accepted_text: str, normalized_text: str,
        channel: str, salvaged: Any,
) -> None:
    """证明实际解析文本由已登记通道原文按 producer 同一规则得到。"""
    if not isinstance(salvaged, bool) or salvaged != (channel == 'reasoning'):
        raise ProvenanceRefused(
            f'{label}: accepted_channel={channel!r} 与 salvaged={salvaged!r} 不闭合')
    expected = (normalize_json_text(accepted_text)
                if channel in ('content', 'reasoning') else accepted_text)
    if normalized_text != expected:
        raise ProvenanceRefused(
            f'{label}: normalized accepted text 不是 accepted channel 原文的唯一归一结果')


def _repair_resolution_binding(
        novel_dir: Path, attempt_id: str, channel: str,
) -> dict[str, Any] | None:
    """把 agent/deterministic 采用结果绑定到当前不可变 resolution 文件与候选。"""
    if channel not in ('agent', 'deterministic'):
        return None
    from novel.llm import response_repair

    record = response_repair.find_attempt(Path(novel_dir), attempt_id)
    resolution = record.resolution
    if not isinstance(resolution, dict):
        raise ProvenanceRefused(
            f'attempt={attempt_id}: {channel} checkpoint 缺不可变 repair resolution')
    expected_action = 'submit' if channel == 'agent' else 'deterministic'
    if (resolution.get('attempt_id') != attempt_id
            or resolution.get('action') != expected_action):
        raise ProvenanceRefused(
            f'attempt={attempt_id}: checkpoint channel={channel!r} 与 '
            f'resolution action={resolution.get("action")!r} 不闭合')
    candidate = resolution.get('candidate')
    if not isinstance(candidate, dict):
        raise ProvenanceRefused(
            f'attempt={attempt_id}: repair resolution 缺已封存 object candidate')
    sealed_candidate_sha = resolution.get('candidate_sha256')
    if (not isinstance(sealed_candidate_sha, str)
            or len(sealed_candidate_sha) != 64
            or any(ch not in '0123456789abcdef' for ch in sealed_candidate_sha)):
        raise ProvenanceRefused(
            f'attempt={attempt_id}: repair resolution 的 candidate_sha256 不合法')

    physical = record.root / response_repair.RESOLUTION_FILE
    if physical.is_file():
        source = 'single'
        source_path = physical
    else:
        owner = batch_owner(Path(novel_dir), attempt_id)
        if owner is None:
            raise ProvenanceRefused(
                f'attempt={attempt_id}: repair resolution 没有单项或 batch 权威来源')
        source = 'batch'
        source_path = owner[0].with_name(RESOLUTION_FILE)
    root = Path(novel_dir).resolve()
    try:
        source_abs = source_path.resolve()
        try:
            # 旧 run 的 resolution 仍在书目录内，保留原有相对身份。
            source_rel = source_abs.relative_to(root).as_posix()
        except ValueError:
            # 新 run 已迁到同一 output base 的受管 `_service/runs/<sid>`。
            # 只放宽到书目录的父目录，不能把任意外部文件登记成恢复依据。
            source_rel = source_abs.relative_to(root.parent).as_posix()
        source_raw = source_path.read_bytes()
    except (ValueError, OSError) as exc:
        raise ProvenanceRefused(
            f'attempt={attempt_id}: repair resolution 来源缺失、越界或不可读：{exc}') from exc
    return {
        'source': source,
        'source_rel': source_rel,
        'source_sha256': _sha(source_raw),
        'attempt_id': attempt_id,
        'action': expected_action,
        'sealed_candidate_sha256': sealed_candidate_sha,
        'candidate_payload_sha256': _sha(candidate),
    }


def _validated_agent_acceptance(
        ctx: Any, *, phase: str, purpose: str, chapter: int,
        response_model: type[BaseModel], system: str, user: str,
        response_payload: Mapping[str, Any], acceptance: Mapping[str, Any],
        claim_prior: bool,
) -> dict[str, Any]:
    """验证 ``node-executor/v1`` stdout/session/ledger/typed payload 闭环。"""
    if (set(acceptance) != _AGENT_ACCEPTANCE_FIELDS
            or acceptance.get('acceptance_protocol') != AGENT_PROTOCOL_VERSION):
        raise ProvenanceRefused(
            f'{purpose}: agent acceptance 字段不完整、含未登记字段或协议不匹配')
    profile = _resolved_profile_dict(
        ctx, acceptance.get('profile'), label=f'{purpose}: agent acceptance')
    policy_profile, requested_ceiling = _agent_phase_policy(
        ctx, phase=phase, response_model=response_model)
    if profile != policy_profile:
        raise ProvenanceRefused(
            f'{purpose}: agent acceptance profile 与不可变 run policy 漂移')
    attempt_id = acceptance.get('attempt_id')
    request_fingerprint = acceptance.get('request_fingerprint')
    model = acceptance.get('model')
    ledger_response_sha256 = acceptance.get('ledger_response_sha256')
    accepted_text = acceptance.get('accepted_text')
    normalized_text = acceptance.get('normalized_text')
    links = acceptance.get('attempt_links')
    recovery = acceptance.get('recovery_from_attempts')
    turn_index = acceptance.get('accepted_turn_index')
    prompt_sha = acceptance.get('accepted_prompt_sha256')
    schema_sha = acceptance.get('accepted_schema_sha256')
    binding = acceptance.get('accepted_request')
    if (not _is_attempt_id(attempt_id)
            or not _is_sha256(request_fingerprint)
            or not isinstance(model, str) or model != profile['model']
            or not _is_sha256(ledger_response_sha256)
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
            or not _is_sha256(prompt_sha)
            or not _is_sha256(schema_sha)
            or not isinstance(binding, dict)
            or set(binding) != _AGENT_REQUEST_FIELDS
            or not isinstance(links, list) or not links
            or not isinstance(recovery, list)):
        raise ProvenanceRefused(
            f'{purpose}: agent acceptance 缺完整 stdout/session/request/成本身份')

    current_schema_sha = _sha(agent_json_schema(response_model))
    expected_binding = {
        'node': ctx.node,
        'chapter': int(chapter),
        'purpose': purpose,
        'system_sha256': _sha(system.encode('utf-8')),
        'user_sha256': _sha(user.encode('utf-8')),
        'schema_sha256': current_schema_sha,
        'requested_model': profile['model'],
        'requested_effort': profile['reasoning_effort'],
        'requested_output_token_ceiling': requested_ceiling,
        'protocol_version': AGENT_PROTOCOL_VERSION,
    }
    if binding != expected_binding:
        raise ProvenanceRefused(
            f'{purpose}: agent accepted_request 与当前 prompt/profile/schema 不闭合')
    if schema_sha != current_schema_sha:
        raise ProvenanceRefused(
            f'{purpose}: accepted schema SHA 与当前 {response_model.__name__} 不同')
    if acceptance.get('subject_sha256') != _sha(binding):
        raise ProvenanceRefused(
            f'{purpose}: logical subject SHA-256 与 accepted_request 不闭合')
    _validate_agent_session_turn(
        ctx, purpose=purpose, chapter=chapter,
        acceptance=acceptance, profile=profile, system=system, user=user)

    _validate_channel_text(
        label=purpose, accepted_text=accepted_text,
        normalized_text=normalized_text, channel='content', salvaged=False)
    if ledger_response_sha256 != _sha(accepted_text.encode('utf-8')):
        raise ProvenanceRefused(
            f'{purpose}: accepted stdout SHA 与 ledger response 身份不闭合')
    try:
        parsed_payload = json.loads(normalized_text)
    except json.JSONDecodeError as exc:
        raise ProvenanceRefused(
            f'{purpose}: normalized agent stdout 不是合法 JSON：{exc}') from exc
    if not isinstance(parsed_payload, dict):
        raise ProvenanceRefused(
            f'{purpose}: normalized agent stdout 不是 JSON object')
    try:
        typed_payload = response_model.model_validate(
            parsed_payload).model_dump(by_alias=True)
    except ValidationError as exc:
        raise ProvenanceRefused(
            f'{purpose}: agent stdout 不再通过当前 {response_model.__name__}：{exc}') from exc
    payload = dict(response_payload)
    if typed_payload != payload:
        raise ProvenanceRefused(
            f'{purpose}: agent stdout 经当前 schema 归一后与验证后 payload 不闭合')

    normalized_links: list[dict[str, Any]] = []
    attempt_ids: list[str] = []
    fingerprints: list[str] = []
    for link in links:
        if (not isinstance(link, dict)
                or set(link) != _LEDGER_IDENTITY_FIELDS
                or not _is_attempt_id(link.get('attempt_id'))
                or not _is_sha256(link.get('request_fingerprint'))):
            raise ProvenanceRefused(
                f'{purpose}: agent attempt_links 含不完整或不合法身份：{link!r}')
        aid = str(link['attempt_id'])
        fingerprint = str(link['request_fingerprint'])
        if (link.get('request_model') != profile['model']
                or link.get('reasoning_effort') != profile['reasoning_effort']
                or link.get('thinking') is not None
                or link.get('json_mode') is not True
                or link.get('max_tokens') is not None):
            raise ProvenanceRefused(
                f'{purpose}: agent attempt={aid} 的实际/请求 model 或 effort '
                '与 resolved profile 漂移，或伪造了 legacy '
                'thinking/provider max_tokens')
        resolve_ledger_model_identity(profile, link.get('model'))
        normalized_links.append(dict(link))
        attempt_ids.append(aid)
        fingerprints.append(fingerprint)
    if len(set(attempt_ids)) != len(attempt_ids):
        raise ProvenanceRefused(f'{purpose}: agent attempt_links 含重复 attempt_id')
    if attempt_id not in attempt_ids:
        raise ProvenanceRefused(
            f'{purpose}: accepted agent attempt 未被 contributing attempts 联结')
    normalized_links.sort(key=lambda row: (
        str(row['attempt_id']), str(row['request_fingerprint'])))
    expected_recovery = [
        str(row['attempt_id']) for row in normalized_links
        if row['attempt_id'] != attempt_id
    ]
    if (recovery != expected_recovery
            or turn_index != len(normalized_links)
            or acceptance.get('request_mode') != (
                'original' if turn_index == 1 else 'agent_session_resume')):
        raise ProvenanceRefused(
            f'{purpose}: agent turn/recovery/attempt_links 集合不闭合')

    if claim_prior:
        ctx.ledger.claim_prior_attempts(
            attempt_ids, sid=ctx.sid, node=ctx.node, purpose=purpose,
            request_fingerprints=fingerprints)
    for link in normalized_links:
        aid = str(link['attempt_id'])
        if link != ctx.ledger.request_identity_for_attempt(aid):
            raise ProvenanceRefused(
                f'{purpose}: agent attempt={aid} 请求配置/响应 SHA 与 ledger 不闭合')
    accepted_link = next(
        row for row in normalized_links if row['attempt_id'] == attempt_id)
    validate_checkpoint_model_identity(
        profile,
        checkpoint_model=model,
        actual_model=accepted_link.get('model'),
        actual_is_ledger=True,
    )
    if (accepted_link.get('request_fingerprint') != request_fingerprint
            or accepted_link.get('response_sha256') != ledger_response_sha256):
        raise ProvenanceRefused(
            f'{purpose}: accepted agent attempt/model/response 与 ledger 不闭合')

    return {
        **{key: acceptance[key] for key in _AGENT_ACCEPTANCE_FIELDS},
        'profile': profile,
        'accepted_request': dict(binding),
        'attempt_links': normalized_links,
        'recovery_from_attempts': expected_recovery,
        'parsed_payload': parsed_payload,
        'response_payload': payload,
    }


def _persist_checkpoint(
        ctx: Any, *, phase: str, chapter: int, record: dict[str, Any],
) -> Path:
    """保持 v1 的首次写入不可变语义；agent 与 legacy 共用这一把口。"""
    record['checkpoint_sha256'] = _checkpoint_hash(record)
    target = paths.stage2_blueprint_checkpoint_path(
        ctx.novel_dir, ctx.run_id, phase, chapter)
    with _LOCK:
        with process_append_lock(target):
            if target.is_file():
                current = _read_json(target)
                if _sha({k: v for k, v in current.items() if k != 'created_at'}) \
                        != _sha({k: v for k, v in record.items() if k != 'created_at'}):
                    raise ProvenanceRefused(
                        f'{target} 已有与本次 accepted attempt 不同的检查点；拒绝覆盖')
            else:
                _atomic_json(target, record)
    if 'acceptance_protocol' in record:
        work_units.budget_for(ctx, scope=f'chapter:{chapter}').mark_adopted_for_attempt(
            str(record['attempt_id']))
    return target


def write_checkpoint(
        ctx: Any, *, phase: str, chapter: int, purpose: str,
        architecture: str, response_model: type[BaseModel], system: str, user: str,
        response_payload: Mapping[str, Any], acceptance: Mapping[str, Any],
        scope_violation: str | None,
) -> Path:
    """原子保存一章；调用方只能在 schema/validator/scope 全部走完后调用。"""
    identity = _checkpoint_identity(
        ctx, phase=phase, chapter=chapter, purpose=purpose,
        architecture=architecture, response_model=response_model.__name__,
        system=system, user=user,
    )
    if 'acceptance_protocol' in acceptance:
        agent = _validated_agent_acceptance(
            ctx, phase=phase, purpose=purpose, chapter=chapter,
            response_model=response_model, system=system, user=user,
            response_payload=response_payload, acceptance=acceptance,
            claim_prior=False)
        record = {
            **identity,
            'created_at': _now(),
            **{key: agent[key] for key in _AGENT_ACCEPTANCE_FIELDS},
            'parsed_payload': agent['parsed_payload'],
            'parsed_payload_sha256': _sha(agent['parsed_payload']),
            'response_payload': agent['response_payload'],
            'response_payload_sha256': _sha(agent['response_payload']),
            'scope_violation': scope_violation,
        }
        return _persist_checkpoint(
            ctx, phase=phase, chapter=chapter, record=record)

    attempt_id = str(acceptance.get('attempt_id') or '')
    request_fingerprint = str(acceptance.get('request_fingerprint') or '')
    model = str(acceptance.get('model') or '')
    accepted_text = acceptance.get('accepted_text')
    normalized_text = acceptance.get('normalized_text')
    channel = acceptance.get('accepted_channel')
    links = acceptance.get('attempt_links')
    accepted_request = acceptance.get('accepted_request')
    model_chain = acceptance.get('model_chain')
    rungs = acceptance.get('rungs')
    ledger_response_sha256 = acceptance.get('ledger_response_sha256')
    request_mode = acceptance.get('request_mode')
    recovery_from_attempts = acceptance.get('recovery_from_attempts')
    salvaged = acceptance.get('salvaged')
    if (len(attempt_id) != 16
            or any(ch not in '0123456789abcdef' for ch in attempt_id)
            or len(request_fingerprint) != 64
            or any(ch not in '0123456789abcdef' for ch in request_fingerprint)
            or not model or not isinstance(accepted_text, str)
            or not isinstance(normalized_text, str)
            or channel not in ('content', 'reasoning', 'agent', 'deterministic')
            or not isinstance(links, list) or not links
            or not isinstance(accepted_request, dict)
            or accepted_request.get('request_fingerprint') != request_fingerprint
            or not isinstance(model_chain, list) or not model_chain
            or not all(isinstance(item, str) and item for item in model_chain)
            or not isinstance(rungs, list) or not rungs
            or not all(isinstance(item, list) and len(item) == 2
                       and isinstance(item[0], str) and isinstance(item[1], bool)
                       for item in rungs)
            or (ledger_response_sha256 is not None
                and (not isinstance(ledger_response_sha256, str)
                     or len(ledger_response_sha256) != 64))
            or not isinstance(request_mode, str) or not request_mode
            or not isinstance(recovery_from_attempts, list)
            or not all(isinstance(item, str) for item in recovery_from_attempts)):
        raise ProvenanceRefused(
            f'{purpose}: accepted attempt/model/channel/原文/成本联结不完整，不能铸检查点')
    _validate_channel_text(
        label=purpose, accepted_text=accepted_text,
        normalized_text=normalized_text, channel=str(channel), salvaged=salvaged)
    normalized_links: list[dict[str, object]] = []
    for link in links:
        if not isinstance(link, dict):
            raise ProvenanceRefused(f'{purpose}: attempt_links 含非对象')
        aid = str(link.get('attempt_id') or '')
        fingerprint = str(link.get('request_fingerprint') or '')
        if (len(aid) != 16 or any(ch not in '0123456789abcdef' for ch in aid)
                or len(fingerprint) != 64
                or any(ch not in '0123456789abcdef' for ch in fingerprint)):
            raise ProvenanceRefused(f'{purpose}: attempt_links 身份不合法：{link!r}')
        current = ctx.ledger.request_identity_for_attempt(aid)
        if link != current:
            raise ProvenanceRefused(
                f'{purpose}: attempt={aid} 的 checkpoint 成本配置与 ledger 不闭合')
        normalized_links.append(dict(current))
    if len({row['attempt_id'] for row in normalized_links}) != len(normalized_links):
        raise ProvenanceRefused(f'{purpose}: attempt_links 含重复 attempt_id')
    if attempt_id not in {row['attempt_id'] for row in normalized_links}:
        raise ProvenanceRefused(f'{purpose}: accepted attempt 没有出现在 contributing attempts')
    if channel == 'content' and ledger_response_sha256 != _sha(
            accepted_text.encode('utf-8')):
        raise ProvenanceRefused(
            f'{purpose}: content accepted 原文 SHA 与 ledger response 不闭合')
    if channel == 'reasoning':
        _validate_reasoning_acceptance(ctx, run_id=ctx.run_id, purpose=purpose,
            chapter=chapter, attempt_id=attempt_id, model=model, accepted_text=accepted_text,
            ledger_response_sha256=ledger_response_sha256, accepted_request=accepted_request)

    payload = dict(response_payload)
    try:
        parsed_payload = json.loads(normalized_text)
    except json.JSONDecodeError as exc:
        raise ProvenanceRefused(
            f'{purpose}: normalized accepted text 不是合法 JSON：{exc}') from exc
    if not isinstance(parsed_payload, dict):
        raise ProvenanceRefused(
            f'{purpose}: normalized accepted text 不是 JSON object')
    try:
        typed_payload = response_model.model_validate(
            parsed_payload).model_dump(by_alias=True)
    except ValidationError as exc:
        raise ProvenanceRefused(
            f'{purpose}: parsed response 不再通过当前 {response_model.__name__}：{exc}') from exc
    if typed_payload != payload:
        raise ProvenanceRefused(
            f'{purpose}: parsed response 经当前 schema 归一后与验证后 payload 不闭合')
    repair_resolution = _repair_resolution_binding(
        ctx.novel_dir, attempt_id, str(channel))
    if (repair_resolution is not None
            and repair_resolution['candidate_payload_sha256'] != _sha(parsed_payload)):
        raise ProvenanceRefused(
            f'{purpose}: repair resolution 封存候选与 parsed response 不闭合')
    record = {
        **identity,
        'created_at': _now(),
        'attempt_id': attempt_id,
        'model': model,
        'request_fingerprint': request_fingerprint,
        'accepted_channel': channel,
        'accepted_text': accepted_text,
        'accepted_text_sha256': _sha(accepted_text.encode('utf-8')),
        'normalized_text': normalized_text,
        'normalized_text_sha256': _sha(normalized_text.encode('utf-8')),
        'accepted_request': dict(accepted_request),
        'model_chain': list(model_chain),
        'rungs': [list(item) for item in rungs],
        'ledger_response_sha256': ledger_response_sha256,
        'request_mode': request_mode,
        'recovery_from_attempts': list(recovery_from_attempts),
        'parsed_payload': parsed_payload,
        'parsed_payload_sha256': _sha(parsed_payload),
        'response_payload': payload,
        'response_payload_sha256': _sha(payload),
        'attempt_links': sorted(
            normalized_links, key=lambda row: (
                str(row['attempt_id']), str(row['request_fingerprint']))),
        'repair_resolution': repair_resolution,
        'recovery_mode': str(acceptance.get('recovery_mode') or 'original'),
        'salvaged': salvaged,
        'scope_violation': scope_violation,
    }
    return _persist_checkpoint(
        ctx, phase=phase, chapter=chapter, record=record)


def restore_checkpoint(
        ctx: Any, *, phase: str, chapter: int, purpose: str,
        architecture: str, response_model: type[BaseModel], system: str, user: str,
) -> dict[str, Any] | None:
    """只恢复字节与成本联结；当前 Pydantic/validator/scope 由调用点随即重跑。"""
    target = paths.stage2_blueprint_checkpoint_path(
        ctx.novel_dir, ctx.run_id, phase, chapter)
    if not target.exists():
        return None
    record = _read_json(target)
    if record.get('checkpoint_sha256') != _checkpoint_hash(record):
        raise ArtifactUnreadable(f'{target} 的 checkpoint_sha256 不闭合')
    expected = _checkpoint_identity(
        ctx, phase=phase, chapter=chapter, purpose=purpose,
        architecture=architecture, response_model=response_model.__name__,
        system=system, user=user,
    )
    comparable = {key: record.get(key) for key in expected}
    if comparable != expected:
        raise ProvenanceRefused(
            f'{target} 的 run/代码/契约/输入绑定与当前请求不同；'
            '现存检查点不允许退化成 miss 后重新付费')
    if 'acceptance_protocol' in record:
        if any(key in record for key in (
                'model_chain', 'rungs', 'repair_resolution', 'recovery_mode')):
            raise ProvenanceRefused(
                f'{target} 的 agent checkpoint 混入 legacy LLM 字段')
        parsed_payload = record.get('parsed_payload')
        payload = record.get('response_payload')
        if (not isinstance(parsed_payload, dict)
                or record.get('parsed_payload_sha256') != _sha(parsed_payload)
                or not isinstance(payload, dict)
                or record.get('response_payload_sha256') != _sha(payload)):
            raise ArtifactUnreadable(
                f'{target} 的 agent parsed/typed payload SHA-256 不闭合')
        acceptance = {
            key: record.get(key) for key in _AGENT_ACCEPTANCE_FIELDS
        }
        agent = _validated_agent_acceptance(
            ctx, phase=phase, purpose=purpose, chapter=chapter,
            response_model=response_model, system=system, user=user,
            response_payload=payload, acceptance=acceptance,
            claim_prior=True)
        if agent['parsed_payload'] != parsed_payload:
            raise ProvenanceRefused(
                f'{target} 的 normalized agent stdout 与 parsed payload 指向不同内容')
        return record

    accepted_text = record.get('accepted_text')
    normalized_text = record.get('normalized_text')
    parsed_payload = record.get('parsed_payload')
    payload = record.get('response_payload')
    channel = record.get('accepted_channel')
    salvaged = record.get('salvaged')
    if (not isinstance(accepted_text, str)
            or record.get('accepted_text_sha256') != _sha(accepted_text.encode('utf-8'))
            or not isinstance(normalized_text, str)
            or record.get('normalized_text_sha256')
            != _sha(normalized_text.encode('utf-8'))
            or channel not in ('content', 'reasoning', 'agent', 'deterministic')
            or not isinstance(parsed_payload, dict)
            or record.get('parsed_payload_sha256') != _sha(parsed_payload)
            or not isinstance(payload, dict)
            or record.get('response_payload_sha256') != _sha(payload)):
        raise ArtifactUnreadable(f'{target} 的响应正文或 payload SHA-256 不闭合')
    _validate_channel_text(
        label=str(target), accepted_text=accepted_text,
        normalized_text=normalized_text, channel=str(channel), salvaged=salvaged)
    try:
        normalized_payload = json.loads(normalized_text)
    except json.JSONDecodeError as exc:
        raise ArtifactUnreadable(
            f'{target} 的 normalized accepted text 已不是合法 JSON：{exc}') from exc
    if not isinstance(normalized_payload, dict):
        raise ArtifactUnreadable(f'{target} 的 normalized accepted text 不是 JSON 对象')
    if normalized_payload != parsed_payload:
        raise ProvenanceRefused(
            f'{target} 的 normalized accepted text 与 parsed payload 指向不同内容')
    try:
        current_payload = response_model.model_validate(
            parsed_payload).model_dump(by_alias=True)
    except ValidationError as exc:
        raise ArtifactUnreadable(
            f'{target} 的 parsed payload 不再通过当前 {response_model.__name__}：{exc}') from exc
    if current_payload != payload:
        raise ProvenanceRefused(
            f'{target} 的 parsed payload 经当前 schema 归一后与 typed payload 不同')
    stored_resolution = record.get('repair_resolution')
    if channel in ('agent', 'deterministic'):
        if not isinstance(stored_resolution, dict):
            raise ProvenanceRefused(f'{target} 缺不可变 repair resolution 联结')
        current_resolution = _repair_resolution_binding(
            ctx.novel_dir, str(record.get('attempt_id') or ''), str(channel))
        if current_resolution != stored_resolution:
            raise ProvenanceRefused(f'{target} 的 repair resolution 身份或字节已漂移')
        assert current_resolution is not None
        if current_resolution['candidate_payload_sha256'] != _sha(parsed_payload):
            raise ProvenanceRefused(
                f'{target} 的 repair resolution 候选与 parsed payload 不同')
    elif stored_resolution is not None:
        raise ProvenanceRefused(
            f'{target} 的供应商通道不应携带 repair resolution 联结')
    links = record.get('attempt_links')
    if not isinstance(links, list) or not links:
        raise ArtifactUnreadable(f'{target} 缺 contributing attempt 成本联结')
    attempt_ids: list[str] = []
    fingerprints: list[str] = []
    for link in links:
        if not isinstance(link, dict):
            raise ArtifactUnreadable(f'{target} 的 attempt_links 含非对象')
        attempt_ids.append(str(link.get('attempt_id') or ''))
        fingerprints.append(str(link.get('request_fingerprint') or ''))
    if len(set(attempt_ids)) != len(attempt_ids):
        raise ArtifactUnreadable(f'{target} 的 attempt_links 含重复 attempt_id')
    ctx.ledger.claim_prior_attempts(
        attempt_ids, sid=ctx.sid, node=ctx.node, purpose=purpose,
        request_fingerprints=fingerprints)
    for link in links:
        assert isinstance(link, dict)
        attempt = str(link.get('attempt_id') or '')
        if link != ctx.ledger.request_identity_for_attempt(attempt):
            raise ProvenanceRefused(
                f'{target} 的 attempt={attempt} 请求配置/响应 SHA 与 ledger 不闭合')
    accepted = str(record.get('attempt_id') or '')
    if accepted not in attempt_ids:
        raise ProvenanceRefused(f'{target} 的 accepted attempt 未被成本账联结')
    if ctx.ledger.request_fingerprint_for_attempt(accepted) \
            != record.get('request_fingerprint'):
        raise ProvenanceRefused(f'{target} 的 accepted request fingerprint 与 ledger 不同')
    if ctx.ledger.model_for_attempt(accepted) != record.get('model'):
        raise ProvenanceRefused(f'{target} 的 accepted model 与 ledger 不同')
    if ctx.ledger.request_identity_for_attempt(accepted).get('response_sha256') \
            != record.get('ledger_response_sha256'):
        raise ProvenanceRefused(f'{target} 的 accepted response SHA 与 ledger 不同')
    if channel == 'content' and record.get('ledger_response_sha256') != _sha(
            accepted_text.encode('utf-8')):
        raise ProvenanceRefused(f'{target} 的 content accepted 原文与 ledger response 不同')
    if channel == 'reasoning':
        binding = record.get('accepted_request')
        if not isinstance(binding, dict):
            raise ProvenanceRefused(f'{target} 缺 reasoning 原请求身份')
        _validate_reasoning_acceptance(ctx, run_id=ctx.run_id, purpose=purpose,
            chapter=chapter, attempt_id=accepted, model=str(record['model']),
            accepted_text=accepted_text, ledger_response_sha256=record['ledger_response_sha256'],
            accepted_request=binding)
    return record


def _recovery_phase_policy(
        ctx: Any, *, run: Mapping[str, Any], phase: str,
        response_model: type[BaseModel],
) -> tuple[dict[str, Any], dict[str, Any]]:
    """取历史 run 中与 detail checkpoint **同名且唯一**的请求策略。"""
    raw_policy = run.get('request_policy')
    if not isinstance(raw_policy, dict):
        raise ProvenanceRefused('历史 Stage2 run 缺 request_policy 对象')
    policy = _normalize_request_policy(
        ctx, raw_policy, standalone_historical_evidence=True)
    if run.get('request_policy_sha256') != _sha(policy):
        raise ProvenanceRefused('历史 Stage2 run 的 request_policy SHA-256 不闭合')
    rows = [
        row for row in policy.get('phases', [])
        if isinstance(row, dict) and row.get('phase') == phase
    ]
    if len(rows) != 1:
        raise ProvenanceRefused(
            f'历史 Stage2 run 对 phase={phase!r} 没有唯一请求策略')
    row = rows[0]
    expected_fields = (
        _AGENT_PHASE_FIELDS
        if policy.get('protocol_version') == AGENT_PROTOCOL_VERSION
        else _LEGACY_PHASE_FIELDS
    )
    current_schema_sha = _sha(
        agent_json_schema(response_model)
        if policy.get('protocol_version') == AGENT_PROTOCOL_VERSION
        else response_model.model_json_schema(by_alias=True))
    if (set(row) != expected_fields
            or row.get('response_model') != response_model.__name__
            or row.get('response_schema_sha256') != current_schema_sha):
        raise ProvenanceRefused(
            f'历史 Stage2 phase={phase!r} 的字段或 response schema 与当前契约漂移')
    if policy.get('protocol_version') != AGENT_PROTOCOL_VERSION:
        max_tokens = row.get('max_tokens')
        if (not isinstance(max_tokens, int) or isinstance(max_tokens, bool)
                or max_tokens <= 0
                or not isinstance(
                    row.get('prefer_content_over_reasoning_salvage'), bool)):
            raise ProvenanceRefused(
                f'历史 Stage2 phase={phase!r} 的 legacy token/salvage 策略不合法')
    return policy, row


def _validate_historical_agent_session(
        ctx: Any, *, record: Mapping[str, Any], run_id: str,
        purpose: str, chapter: int, artifact_code: str,
        policy: Mapping[str, Any], phase_policy: Mapping[str, Any],
        response_model: type[BaseModel],
) -> None:
    """不 claim 当前 run，仍把 agent checkpoint 联回历史 session turn 链。"""
    acceptance = {key: record.get(key) for key in _AGENT_ACCEPTANCE_FIELDS}
    profile = _materialized_profile_dict(
        policy.get('profile'), label='历史 Stage2 agent request policy')
    binding = acceptance.get('accepted_request')
    schema_sha = _sha(agent_json_schema(response_model))
    ceiling = phase_policy.get('requested_output_token_ceiling')
    expected_binding = {
        'node': 'stage2.blueprint',
        'chapter': chapter,
        'purpose': purpose,
        'system_sha256': record.get('system_sha256'),
        'user_sha256': record.get('user_sha256'),
        'schema_sha256': schema_sha,
        'requested_model': profile['model'],
        'requested_effort': profile['reasoning_effort'],
        'requested_output_token_ceiling': ceiling,
        'protocol_version': AGENT_PROTOCOL_VERSION,
    }
    if (not isinstance(binding, dict) or set(binding) != _AGENT_REQUEST_FIELDS
            or binding != expected_binding
            or acceptance.get('acceptance_protocol') != AGENT_PROTOCOL_VERSION
            or acceptance.get('profile') != profile
            or acceptance.get('model') != profile['model']
            or acceptance.get('accepted_channel') != 'content'
            or acceptance.get('salvaged') is not False
            or acceptance.get('accepted_schema_sha256') != schema_sha
            or acceptance.get('subject_sha256') != _sha(binding)):
        raise ProvenanceRefused(
            f'{purpose}: 历史 agent accepted_request/profile/schema 不闭合')

    try:
        events = agent_session_audit.read_events(ctx.novel_dir.parent)
        sessions = agent_session_audit.fold(events)
    except (OSError, UnicodeError, ValueError) as exc:
        raise ProvenanceRefused(
            f'{purpose}: 历史 agent session 审计不可验证：{exc}') from exc
    matches = [
        session for session in sessions
        if session.session_run_id == acceptance.get('session_run_id')
    ]
    if len(matches) != 1 or not matches[0].turns:
        raise ProvenanceRefused(f'{purpose}: 历史 agent session 不是唯一非空 turn 链')
    session = validate_agent_session_request_binding(
        events,
        acceptance,
        purpose=purpose,
        profile=profile,
        # 历史 user 正文不重新落第二份；首轮 prompt SHA 已由 append-only session
        # 事件保存，accepted_request 另以 user/system hash 绑定当前 checkpoint。
        original_prompt_sha256=str(matches[0].turns[0].prompt_sha256),
        original_schema_sha256=schema_sha,
    )
    accepted = [
        turn for turn in session.turns
        if turn.attempt_id == acceptance.get('attempt_id')
    ]
    start = session.start
    if len(accepted) != 1:
        raise ProvenanceRefused(f'{purpose}: accepted attempt 没有唯一 session turn')
    turn = accepted[0]
    model_identity = validate_checkpoint_model_identity(
        profile,
        checkpoint_model=acceptance.get('model'),
        actual_model=turn.actual_model,
        cli_version=turn.cli_version,
        require_driver_version=True,
    )
    start_profile = (
        None if start.profile is None else start.profile.model_dump(mode='json'))
    turn_profile = (
        None if turn.profile is None else turn.profile.model_dump(mode='json'))
    if ({
            'parent_run_id': start.parent_run_id,
            'role': start.role,
            'node': start.node,
            'chapter': start.chapter,
            'subject_sha256': start.subject_sha256,
            'profile': start_profile,
            'requested_model': start.requested_model,
            'requested_effort': start.requested_effort,
            'protocol_version': start.protocol_version,
            'start_code_version': start.start_code_version,
        } != {
            'parent_run_id': run_id,
            'role': 'executor',
            'node': 'stage2.blueprint',
            'chapter': chapter,
            'subject_sha256': acceptance.get('subject_sha256'),
            'profile': profile,
            'requested_model': profile['model'],
            'requested_effort': profile['reasoning_effort'],
            'protocol_version': AGENT_PROTOCOL_VERSION,
            'start_code_version': artifact_code,
        } or {
            'parent_run_id': turn.parent_run_id,
            'role': turn.role,
            'node': turn.node,
            'chapter': turn.chapter,
            'subject_sha256': turn.subject_sha256,
            'profile': turn_profile,
            'provider_session_id': turn.provider_session_id,
            'turn_index': turn.turn_index,
            'requested_model': turn.requested_model,
            'actual_model': turn.actual_model,
            'requested_effort': turn.requested_effort,
            'protocol_version': turn.protocol_version,
            'start_code_version': turn.start_code_version,
            'prompt_sha256': turn.prompt_sha256,
            'schema_sha256': turn.schema_sha256,
        } != {
            'parent_run_id': run_id,
            'role': 'executor',
            'node': 'stage2.blueprint',
            'chapter': chapter,
            'subject_sha256': acceptance.get('subject_sha256'),
            'profile': profile,
            'provider_session_id': acceptance.get('provider_session_id'),
            'turn_index': acceptance.get('accepted_turn_index'),
            'requested_model': profile['model'],
            'actual_model': model_identity.actual_model,
            'requested_effort': profile['reasoning_effort'],
            'protocol_version': AGENT_PROTOCOL_VERSION,
            'start_code_version': artifact_code,
            'prompt_sha256': acceptance.get('accepted_prompt_sha256'),
            'schema_sha256': schema_sha,
        }):
        raise ProvenanceRefused(
            f'{purpose}: 历史 agent checkpoint 与 started/turn 身份漂移')


def _validate_recovery_request_binding(
        ctx: Any, *, record: Mapping[str, Any], run_id: str,
        purpose: str, chapter: int, artifact_code: str,
        policy: Mapping[str, Any], phase_policy: Mapping[str, Any],
        response_model: type[BaseModel],
        accepted_link: Mapping[str, Any], links: Sequence[Mapping[str, Any]],
) -> None:
    """复验 accepted_request；checkpoint 自签名不能替代独立 ledger/session。"""
    if policy.get('protocol_version') == AGENT_PROTOCOL_VERSION:
        profile = _materialized_profile_dict(
            policy.get('profile'), label='历史 Stage2 agent request policy')
        for link in links:
            if (link.get('request_model') != profile['model']
                    or link.get('reasoning_effort') != profile['reasoning_effort']
                    or link.get('thinking') is not None
                    or link.get('json_mode') is not True
                    or link.get('max_tokens') is not None):
                raise ProvenanceRefused(
                    f'{purpose}: 历史 agent attempt link 与 run profile 漂移')
            resolve_ledger_model_identity(profile, link.get('model'))
        _validate_historical_agent_session(
            ctx,
            record=record,
            run_id=run_id,
            purpose=purpose,
            chapter=chapter,
            artifact_code=artifact_code,
            policy=policy,
            phase_policy=phase_policy,
            response_model=response_model,
        )
        return

    binding = record.get('accepted_request')
    chain = policy.get('model_chain')
    rungs = policy.get('rungs')
    max_tokens = phase_policy.get('max_tokens')
    if (not isinstance(binding, dict) or set(binding) != _LEGACY_REQUEST_FIELDS
            or not isinstance(chain, list) or not chain
            or any(not isinstance(item, str) or not item for item in chain)
            or len(set(chain)) != len(chain)
            or not isinstance(rungs, list) or not rungs
            or record.get('model_chain') != chain
            or record.get('rungs') != rungs
            or binding.get('system_sha256') != record.get('system_sha256')
            or not _is_sha256(binding.get('user_sha256'))
            or binding.get('request_fingerprint') != record.get('request_fingerprint')
            or binding.get('request_model') != accepted_link.get('request_model')
            or binding.get('reasoning_effort') != accepted_link.get('reasoning_effort')
            or binding.get('thinking') != accepted_link.get('thinking')
            or binding.get('json_mode') != accepted_link.get('json_mode')
            or binding.get('max_tokens') != accepted_link.get('max_tokens')
            or binding.get('max_tokens') != max_tokens
            or binding.get('temperature') is not None):
        raise ProvenanceRefused(
            f'{purpose}: 历史 legacy accepted_request 与 phase/ledger 不闭合')
    valid_rungs = [
        row for row in rungs
        if (isinstance(row, list) and len(row) == 2
            and isinstance(row[0], str) and row[0]
            and isinstance(row[1], bool))
    ]
    allowed_rungs = {(row[0], row[1]) for row in valid_rungs}
    if len(valid_rungs) != len(rungs) or len(allowed_rungs) != len(rungs):
        raise ProvenanceRefused(f'{purpose}: 历史 run 的 rungs 不是关闭式二元组')
    for link in links:
        if (link.get('model') not in chain
                or link.get('request_model') not in chain
                or (link.get('reasoning_effort'), link.get('thinking'))
                not in allowed_rungs
                or link.get('json_mode') is not True
                or link.get('max_tokens') != max_tokens):
            raise ProvenanceRefused(
                f'{purpose}: 历史 legacy attempt link 不属于 run phase 策略')
    recovery = record.get('recovery_from_attempts')
    request_mode = record.get('request_mode')
    link_ids = {str(link.get('attempt_id') or '') for link in links}
    if (not isinstance(request_mode, str) or not request_mode
            or not isinstance(recovery, list)
            or any(not isinstance(item, str) for item in recovery)
            or len(set(recovery)) != len(recovery)
            or not set(recovery).issubset(
                link_ids - {str(record.get('attempt_id') or '')})):
        raise ProvenanceRefused(f'{purpose}: 历史 legacy recovery chain 不闭合')
    if request_mode in ('original', 'original:budget_fallback'):
        if (request_mode == 'original' and recovery
                or binding.get('user_sha256') != record.get('user_sha256')):
            raise ProvenanceRefused(
                f'{purpose}: 原始请求的 user/recovery 身份漂移')
    elif not recovery:
        raise ProvenanceRefused(
            f'{purpose}: 动态恢复请求没有 contributing attempts')


def _validate_reasoning_acceptance(
        ctx: Any, *, run_id: str, purpose: str, chapter: int,
        attempt_id: str, model: str, accepted_text: str,
        ledger_response_sha256: str, accepted_request: Mapping[str, Any],
) -> None:
    """reasoning 救回稿须与书内留证及 append-only 审计逐格闭合。

    成本账的 ``response_sha256`` 历史上只绑定供应商 ``content``；reasoning
    不能拿 checkpoint 的可重签 self-hash 冒充原响应身份。因此这里同时要求：
    留证的 content 仍命中 ledger hash、reasoning 与 accepted_text 逐字相同，且
    唯一 answer_lost 审计行指向这份留证。
    """
    dump_rel = paths.answer_lost_rel(attempt_id)
    dump_path = paths.answer_lost_path(ctx.novel_dir, attempt_id)
    try:
        dump = json.loads(dump_path.read_text(encoding='utf-8'))
    except (OSError, UnicodeError, json.JSONDecodeError) as exc:
        raise ProvenanceRefused(
            f'{purpose}: reasoning answer_lost 留证不可读：{exc}') from exc
    if not isinstance(dump, dict):
        raise ProvenanceRefused(f'{purpose}: reasoning answer_lost 留证不是 JSON 对象')
    content = dump.get('content')
    raw_content = content if isinstance(content, str) else '' if content is None else None
    if (raw_content is None
            or _sha(raw_content.encode('utf-8')) != ledger_response_sha256
            or dump.get('reasoning') != accepted_text
            or dump.get('run_id') not in (None, run_id)
            or dump.get('sid') != ctx.sid
            or dump.get('node') != 'stage2.blueprint'
            or dump.get('purpose') != purpose
            or dump.get('attempt_id') != attempt_id
            or dump.get('chapter') != chapter
            or dump.get('salvaged') is not True
            or dump.get('model') != model
            or dump.get('reasoning_effort') != accepted_request.get('reasoning_effort')
            or dump.get('thinking') != accepted_request.get('thinking')
            or dump.get('json_mode') != accepted_request.get('json_mode')):
        raise ProvenanceRefused(
            f'{purpose}: reasoning accepted text 与 answer_lost/ledger 留证不闭合')

    if ctx.ledger.path is None:
        rows = list(ctx.ledger.audit_events)
    else:
        from novel.llm.ledger import AUDIT_REL

        target = ctx.ledger.path.with_name(AUDIT_REL[-1])
        try:
            with process_append_lock(target):
                lines = target.read_text(encoding='utf-8').splitlines()
        except OSError as exc:
            raise ProvenanceRefused(
                f'{purpose}: reasoning 留证的 LLM audit 不可读：{exc}') from exc
        rows = []
        for index, line in enumerate(lines):
            if not line.strip():
                continue
            try:
                row = json.loads(line)
            except json.JSONDecodeError as exc:
                raise ProvenanceRefused(
                    f'{purpose}: LLM audit 第 {index + 1} 行损坏') from exc
            if isinstance(row, dict):
                rows.append(row)
    matches = [
        row for row in rows
        if (row.get('kind') == 'answer_lost'
            and row.get('run_id') == run_id
            and row.get('sid') == ctx.sid
            and row.get('node') == 'stage2.blueprint'
            and row.get('purpose') == purpose
            and row.get('attempt_id') == attempt_id
            and row.get('chapter') == chapter
            and row.get('salvaged') is True)
    ]
    if len(matches) != 1:
        raise ProvenanceRefused(
            f'{purpose}: reasoning accepted attempt 没有唯一 answer_lost 审计行')
    audit = matches[0]
    if (audit.get('dump') != dump_rel
            or audit.get('dump_error') is not None
            or audit.get('source') != dump.get('source')
            or audit.get('finish_reason') != dump.get('finish_reason')
            or audit.get('reasoning_effort') != dump.get('reasoning_effort')
            or audit.get('thinking') != dump.get('thinking')
            or audit.get('json_mode') != dump.get('json_mode')
            or audit.get('content_chars') != len(raw_content)
            or audit.get('reasoning_chars') != len(accepted_text)):
        raise ProvenanceRefused(
            f'{purpose}: reasoning answer_lost 审计指针/读数与留证漂移')


def _usage_covers(
        whole: Mapping[str, Any], part: BaseModel,
) -> bool:
    """产物总账至少覆盖一章 checkpoint 所属 purpose 的完整成本。"""
    row = part.model_dump(mode='json')
    for key in ('calls_made', 'calls_cached'):
        total = whole.get(key)
        value = row.get(key)
        if (not isinstance(total, int) or isinstance(total, bool)
                or not isinstance(value, int) or isinstance(value, bool)
                or value > total):
            return False
    for key in ('prompt_tokens', 'completion_tokens', 'reasoning_tokens'):
        value = row.get(key)
        if value is None:
            continue
        total = whole.get(key)
        if (not isinstance(total, int) or isinstance(total, bool)
                or not isinstance(value, int) or isinstance(value, bool)
                or value > total):
            return False
    return True


def _verified_recovery_checkpoint(
        ctx: Any, *, target: Path, run: Mapping[str, Any],
        architecture: str, response_model: type[BaseModel], system: str,
        artifact_code: str, accepted_models: frozenset[str],
        artifact_usage: Mapping[str, Any], chapters: frozenset[int],
) -> VerifiedBlueprintCheckpoint | None:
    """核验一份历史 checkpoint；任一联结不闭合就不给恢复材料。"""
    record = _read_json(target)
    if record.get('checkpoint_sha256') != _checkpoint_hash(record):
        raise ArtifactUnreadable(f'{target} 的 checkpoint_sha256 不闭合')

    run_id = str(run.get('run_id') or '')
    chapter = record.get('chapter')
    phase = record.get('phase')
    purpose = record.get('purpose')
    if (not isinstance(chapter, int) or isinstance(chapter, bool)
            or chapter not in chapters
            or not isinstance(phase, str)
            or not isinstance(purpose, str) or not purpose.strip()
            or phase not in _BLUEPRINT_RECOVERY_PHASES
            or purpose != f'stage2.blueprint:ch{chapter}'
            or record.get('run_id') != run_id
            or record.get('sid') != ctx.sid
            or record.get('node') != 'stage2.blueprint'
            or record.get('code') != artifact_code
            or record.get('architecture') != architecture
            or record.get('response_model') != response_model.__name__
            or record.get('system_sha256') != _sha(system.encode('utf-8'))):
        return None
    if target.resolve() != paths.stage2_blueprint_checkpoint_path(
            ctx.novel_dir, run_id, phase, chapter).resolve():
        return None
    policy, phase_policy = _recovery_phase_policy(
        ctx, run=run, phase=phase, response_model=response_model)

    parsed_payload = record.get('parsed_payload')
    response_payload = record.get('response_payload')
    accepted_text = record.get('accepted_text')
    normalized_text = record.get('normalized_text')
    channel = record.get('accepted_channel')
    salvaged = record.get('salvaged')
    if (not isinstance(parsed_payload, dict)
            or record.get('parsed_payload_sha256') != _sha(parsed_payload)
            or not isinstance(response_payload, dict)
            or record.get('response_payload_sha256') != _sha(response_payload)
            or not isinstance(accepted_text, str)
            or not isinstance(normalized_text, str)
            or channel not in ('content', 'reasoning', 'agent', 'deterministic')):
        raise ArtifactUnreadable(f'{target} 的响应正文或 payload SHA-256 不闭合')
    if 'acceptance_protocol' not in record:
        if (record.get('accepted_text_sha256')
                != _sha(accepted_text.encode('utf-8'))
                or record.get('normalized_text_sha256')
                != _sha(normalized_text.encode('utf-8'))):
            raise ArtifactUnreadable(f'{target} 的 accepted/normalized 文本 SHA-256 不闭合')
    _validate_channel_text(
        label=str(target), accepted_text=accepted_text,
        normalized_text=normalized_text, channel=str(channel), salvaged=salvaged)
    try:
        decoded = json.loads(normalized_text)
    except json.JSONDecodeError as exc:
        raise ArtifactUnreadable(
            f'{target} 的 normalized accepted text 已不是合法 JSON：{exc}') from exc
    if decoded != parsed_payload:
        raise ProvenanceRefused(
            f'{target} 的 normalized accepted text 与 parsed payload 不同')
    try:
        current_payload = response_model.model_validate(
            parsed_payload).model_dump(by_alias=True)
    except ValidationError as exc:
        raise ArtifactUnreadable(
            f'{target} 的 parsed payload 不再通过当前 {response_model.__name__}：{exc}') from exc
    if current_payload != response_payload:
        raise ProvenanceRefused(
            f'{target} 的 parsed payload 经当前 schema 归一后与 typed payload 不同')

    attempt_id = record.get('attempt_id')
    model = record.get('model')
    fingerprint = record.get('request_fingerprint')
    ledger_response_sha = record.get('ledger_response_sha256')
    links = record.get('attempt_links')
    if (not _is_attempt_id(attempt_id)
            or not isinstance(model, str) or model not in accepted_models
            or not _is_sha256(fingerprint)
            or not _is_sha256(ledger_response_sha)
            or not isinstance(links, list) or not links):
        raise ProvenanceRefused(f'{target} 缺 accepted attempt/model/ledger 身份')
    normalized_links: list[dict[str, Any]] = []
    for link in links:
        if (not isinstance(link, dict)
                or set(link) != _LEDGER_IDENTITY_FIELDS
                or not _is_attempt_id(link.get('attempt_id'))
                or not _is_sha256(link.get('request_fingerprint'))
                or not _is_sha256(link.get('response_sha256'))):
            raise ProvenanceRefused(f'{target} 的 attempt_links 含不完整身份')
        normalized_links.append(dict(link))
    ids = [str(link['attempt_id']) for link in normalized_links]
    if len(set(ids)) != len(ids) or attempt_id not in ids:
        raise ProvenanceRefused(f'{target} 的 contributing attempts 重复或缺 accepted attempt')
    accepted_link = next(
        link for link in normalized_links if link['attempt_id'] == attempt_id)
    if policy.get('protocol_version') == AGENT_PROTOCOL_VERSION:
        validate_checkpoint_model_identity(
            _materialized_profile_dict(
                policy.get('profile'),
                label='历史 Stage2 agent request policy'),
            checkpoint_model=model,
            actual_model=accepted_link.get('model'),
            actual_is_ledger=True,
        )
    elif accepted_link.get('model') != model:
        raise ProvenanceRefused(f'{target} 的 accepted model 与封存 identity 不闭合')
    if (accepted_link.get('request_fingerprint') != fingerprint
            or accepted_link.get('response_sha256') != ledger_response_sha):
        raise ProvenanceRefused(f'{target} 的 accepted attempt 与封存 identity 不闭合')
    _validate_recovery_request_binding(
        ctx,
        record=record,
        run_id=run_id,
        purpose=purpose,
        chapter=chapter,
        artifact_code=artifact_code,
        policy=policy,
        phase_policy=phase_policy,
        response_model=response_model,
        accepted_link=accepted_link,
        links=normalized_links,
    )
    if channel == 'content' and ledger_response_sha != _sha(
            accepted_text.encode('utf-8')):
        raise ProvenanceRefused(f'{target} 的 content 原文与 ledger response 不同')

    stored_resolution = record.get('repair_resolution')
    if channel in ('agent', 'deterministic'):
        if not isinstance(stored_resolution, dict):
            raise ProvenanceRefused(f'{target} 缺不可变 repair resolution 联结')
        current_resolution = _repair_resolution_binding(
            ctx.novel_dir, str(attempt_id), str(channel))
        if (current_resolution != stored_resolution
                or current_resolution is None
                or current_resolution['candidate_payload_sha256']
                != _sha(parsed_payload)):
            raise ProvenanceRefused(f'{target} 的 repair resolution 已漂移')
    elif stored_resolution is not None:
        raise ProvenanceRefused(f'{target} 的供应商通道不应携带 repair resolution')

    # CostLedger 的完整 purpose 口会发现同一 logical purpose 下未被 checkpoint
    # links 列出的失败轮；恢复不能只核采用的那一次，更不能污染当前 run 的计数器。
    if ctx.ledger.path is None:
        if ctx.ledger.run_id != run_id:
            return None
        ledger = ctx.ledger
    else:
        from novel.llm.ledger import CostLedger
        ledger = CostLedger(path=ctx.ledger.path, run_id=run_id)
    usage = ledger.usage_for_complete_purposes(
        [purpose], sid=ctx.sid, node='stage2.blueprint',
        required_identities=[(purpose, link) for link in normalized_links])
    if usage is None or not _usage_covers(artifact_usage, usage):
        return None
    if channel == 'reasoning':
        binding = record.get('accepted_request')
        assert isinstance(binding, dict)
        _validate_reasoning_acceptance(
            ctx,
            run_id=run_id,
            purpose=purpose,
            chapter=chapter,
            attempt_id=str(attempt_id),
            model=str(model),
            accepted_text=accepted_text,
            ledger_response_sha256=str(ledger_response_sha),
            accepted_request=binding,
        )

    return VerifiedBlueprintCheckpoint(
        run_id=run_id,
        phase=phase,
        chapter=chapter,
        purpose=purpose,
        checkpoint_sha256=str(record['checkpoint_sha256']),
        response_payload_sha256=str(record['response_payload_sha256']),
        accepted_attempt_id=str(attempt_id),
        accepted_model=str(model),
        response_payload=copy.deepcopy(response_payload),
    )


def verified_blueprint_recovery_checkpoints(
        ctx: Any, *, architecture: str, response_model: type[BaseModel],
        system: str, artifact_code: str, accepted_models: Sequence[str],
        artifact_usage: Mapping[str, Any], chapters: Iterable[int],
) -> tuple[VerifiedBlueprintCheckpoint, ...]:
    """扫描与当前 INVALID 来源账相容的历史章节响应，且全程只读、零 wire。

    checkpoint 本身没有资格直接修正式产物。本口只核其 run/schema/system/hash、
    accepted attempt 与完整 purpose 成本账；producer 随后还必须用当前 main/global
    编译，并与 INVALID 章逐字段闭合。坏的或无法对证的历史状态按 miss 处理，不能
    因为“看起来像那次响应”就猜 locator。
    """
    wanted = frozenset(int(chapter) for chapter in chapters)
    models = frozenset(str(model) for model in accepted_models if str(model))
    if not wanted or not models:
        return ()
    run_dir_by_id: dict[str, Path] = {}
    try:
        for root in runtime_layout.stage_run_roots(ctx.novel_dir, 'stage2'):
            if root.is_dir():
                for path in root.iterdir():
                    if not path.is_dir() or path.name in run_dir_by_id:
                        continue
                    run_dir_by_id[path.name] = path
    except OSError:
        return ()
    run_dirs = [run_dir_by_id[key] for key in sorted(run_dir_by_id)]

    found: list[VerifiedBlueprintCheckpoint] = []
    for run_dir in run_dirs:
        try:
            run_id = check_run_id(run_dir.name)
            run_root = run_dir / 'blueprint'
            run = _read_json(run_root / RUN_FILE)
            policy = run.get('request_policy')
            phases = policy.get('phases') if isinstance(policy, dict) else None
            current_schema_sha = _sha(
                agent_json_schema(response_model)
                if isinstance(policy, dict)
                and policy.get('protocol_version') == AGENT_PROTOCOL_VERSION
                else response_model.model_json_schema(by_alias=True))
            if ({
                    'run_id': run.get('run_id'),
                    'sid': run.get('sid'),
                    'node': run.get('node'),
                    'code': run.get('code'),
                    'architecture': run.get('architecture'),
                } != {
                    'run_id': run_id,
                    'sid': ctx.sid,
                    'node': 'stage2.blueprint',
                    'code': artifact_code,
                    'architecture': architecture,
                }
                    or not isinstance(policy, dict)
                    or run.get('request_policy_sha256') != _sha(policy)
                    or not isinstance(phases, list)
                    or not any(
                        isinstance(row, dict)
                        and row.get('response_model') == response_model.__name__
                        and row.get('response_schema_sha256') == current_schema_sha
                        for row in phases)):
                continue
            checkpoint_dir = run_root / 'checkpoints'
            targets = sorted(checkpoint_dir.glob('*.json')) \
                if checkpoint_dir.is_dir() else []
            for target in targets:
                try:
                    candidate = _verified_recovery_checkpoint(
                        ctx,
                        target=target,
                        run=run,
                        architecture=architecture,
                        response_model=response_model,
                        system=system,
                        artifact_code=artifact_code,
                        accepted_models=models,
                        artifact_usage=artifact_usage,
                        chapters=wanted,
                    )
                except (ArtifactUnreadable, ProvenanceRefused, UpstreamMissing,
                        OSError, UnicodeError, ValueError, TypeError):
                    candidate = None
                if candidate is not None:
                    found.append(candidate)
        except (ArtifactUnreadable, ProvenanceRefused, UpstreamMissing,
                OSError, UnicodeError, ValueError, TypeError):
            continue
    return tuple(found)


def _manifest_hash(payload: Mapping[str, Any]) -> str:
    return _sha({key: value for key, value in payload.items()
                 if key != 'manifest_sha256'})


def _resolution_hash(payload: Mapping[str, Any]) -> str:
    return _sha({key: value for key, value in payload.items()
                 if key != 'resolution_sha256'})


def _validate_manifest(path: Path) -> dict[str, Any]:
    manifest = _read_json(path)
    if manifest.get('manifest_sha256') != _manifest_hash(manifest):
        raise ArtifactUnreadable(f'{path} 的 manifest_sha256 不闭合')
    items = manifest.get('items')
    if not isinstance(items, list) or not items:
        raise ArtifactUnreadable(f'{path} 没有非空 repair items')
    attempts = [item.get('attempt_id') for item in items if isinstance(item, dict)]
    if len(attempts) != len(items) or len(set(attempts)) != len(attempts):
        raise ArtifactUnreadable(f'{path} 的 attempt 集合含缺项或重复')
    return manifest


def manifest_resume_metadata(path: Path) -> dict[str, Any]:
    """从 manifest 绑定的 immutable ``run.json`` 读取 canonical resume 身份。

    不能根据当前 CLI/default 反推 flags：尤其 ``--chapters`` 是主动刷新语义，
    丢掉它会把同一 run 改成整书操作。manifest 已封存 run.json 的字节 SHA，
    因而这里先核 hash，再只接受登记过的 node/flags/env 形状。
    """
    manifest_path = Path(path).resolve()
    manifest = _validate_manifest(manifest_path)
    run_path = manifest_path.with_name(RUN_FILE)
    try:
        run_bytes = run_path.read_bytes()
    except OSError as exc:
        raise ProvenanceRefused(
            f'{manifest_path} 绑定的 run.json 已缺失/不可读：{exc}') from exc
    if manifest.get('run_sha256') != _sha(run_bytes):
        raise ProvenanceRefused(f'{manifest_path} 绑定的 run.json 已漂移')
    run = _read_json(run_path)
    run_id = check_run_id(str(manifest.get('run_id') or ''))
    if (run.get('run_id') != run_id
            or run.get('sid') != manifest.get('sid')
            or run.get('node') != manifest.get('node')
            or run.get('code') != manifest.get('code')
            or run.get('architecture') != manifest.get('architecture')):
        raise ProvenanceRefused(
            f'{manifest_path} 与绑定的 run.json 身份不一致')
    resume = run.get('resume')
    if not isinstance(resume, dict) or set(resume) != {
            'node', 'flags', 'required_environment'}:
        raise ArtifactUnreadable(f'{run_path} 缺 canonical resume metadata')
    flags = resume.get('flags')
    required_environment = resume.get('required_environment')
    if (resume.get('node') != 'stage2.blueprint'
            or not isinstance(flags, list)
            or any(not isinstance(flag, str) or not flag for flag in flags)
            or required_environment != {
                BLUEPRINT_CONTRACT_ENV: manifest.get('architecture')}):
        raise ArtifactUnreadable(f'{run_path} 的 canonical resume metadata 不合法')
    return {
        'run_id': run_id,
        'node': 'stage2.blueprint',
        'flags': tuple(flags),
        'required_environment': dict(required_environment),
        'architecture': str(manifest.get('architecture') or ''),
    }


def create_repair_manifest(
        ctx: Any, errors: Iterable[BaseException], *, architecture: str,
) -> Path:
    """稳定打包同一并发波次的全部 pending；一个 run 永远只有这一份 workpack。"""
    with repair_ownership_lock(ctx.novel_dir):
        return _create_repair_manifest_locked(
            ctx, errors, architecture=architecture)


def _create_repair_manifest_locked(
        ctx: Any, errors: Iterable[BaseException], *, architecture: str,
) -> Path:
    """调用方已持有书级 ownership 锁；在同一临界区判定并铸造 batch。"""
    from novel.llm import response_repair

    attempt_ids = sorted({
        str(getattr(error, 'attempt_id')) for error in errors
        if getattr(error, 'attempt_id', None)
    })
    if not attempt_ids:
        raise ValueError('create_repair_manifest 至少要一份 pending attempt')
    root = _run_root(ctx)
    run_path = root / RUN_FILE
    if not run_path.is_file():
        raise ProvenanceRefused('先登记 Stage2 run 绑定，才能生成 repair manifest')
    items: list[dict[str, Any]] = []
    for attempt_id in attempt_ids:
        record = response_repair.find_attempt(ctx.novel_dir, attempt_id)
        rejection = record.rejection
        if record.resolution is not None:
            raise TerminalState(
                f'attempt={attempt_id} 已有单项或 batch resolution，'
                '不能再铸入新的 batch manifest')
        if not record.eligible:
            raise TerminalState(
                f'attempt={attempt_id} 不是 pending 可修复响应，不能进入人工 batch')
        if (rejection.get('run_id') != ctx.run_id
                or rejection.get('sid') != ctx.sid
                or rejection.get('node') != ctx.node
                or rejection.get('code') != ctx.code):
            raise ProvenanceRefused(
                f'attempt={attempt_id} 不属于当前 run/sid/node/code；'
                '拒绝跨运行打包响应修复')
        rejection_path = record.root / response_repair.REJECTION_FILE
        try:
            rejection_sha = _sha(rejection_path.read_bytes())
        except OSError as exc:
            raise ArtifactUnreadable(f'读不了 repair rejection {rejection_path}: {exc}') from exc
        items.append({
            'attempt_id': attempt_id,
            'chain_id': str(rejection.get('chain_id') or ''),
            'chapter': rejection.get('chapter'),
            'purpose': str(rejection.get('purpose') or ''),
            'gate': str(rejection.get('gate') or ''),
            'rejection_sha256': rejection_sha,
            'candidate': str(response_repair.candidate_path(record)),
        })
    payload: dict[str, Any] = {
        'version': VERSION,
        'created_at': _now(),
        'run_id': check_run_id(ctx.run_id),
        'sid': ctx.sid,
        'node': ctx.node,
        'code': ctx.code,
        'architecture': architecture,
        'run_sha256': _sha(run_path.read_bytes()),
        'items': sorted(items, key=lambda row: (
            int(row['chapter']) if isinstance(row['chapter'], int) else 0,
            row['purpose'], row['attempt_id'])),
    }
    payload['manifest_sha256'] = _manifest_hash(payload)
    target = root / MANIFEST_FILE
    with _LOCK:
        with process_append_lock(target):
            if target.is_file():
                current = _validate_manifest(target)
                current_ids = {item['attempt_id'] for item in current['items']}
                if current_ids != set(attempt_ids):
                    raise TerminalState(
                        f'run={ctx.run_id} 已经生成过唯一 repair workpack；'
                        f'原集合={sorted(current_ids)}，新失败={attempt_ids}。'
                        '本 run 不再形成第二次人工暂停')
                return target
            _atomic_json(target, payload)
    return target


def _manifest_paths(novel_dir: Path) -> Iterable[Path]:
    found: dict[str, Path] = {}
    for root in runtime_layout.stage_run_roots(novel_dir, 'stage2'):
        if root.is_dir():
            for target in root.glob(f'*/blueprint/{MANIFEST_FILE}'):
                run_id = target.parent.parent.name
                prior = found.get(run_id)
                if prior is not None:
                    continue
                found[run_id] = target
    return tuple(found[key] for key in sorted(found))


def batch_owner(novel_dir: Path, attempt_id: str) -> tuple[Path, dict[str, Any]] | None:
    """attempt 是否已被某一不可变 batch manifest 收编；多份所有权关闭式失败。"""
    owners: list[tuple[Path, dict[str, Any]]] = []
    for manifest_path in _manifest_paths(Path(novel_dir)):
        manifest = _validate_manifest(manifest_path)
        if any(item.get('attempt_id') == attempt_id for item in manifest['items']):
            owners.append((manifest_path, manifest))
    if len(owners) > 1:
        raise ProvenanceRefused(
            f'attempt_id={attempt_id} 同时属于 {len(owners)} 份 Stage2 batch manifest')
    return owners[0] if owners else None


def batch_resolution_for_attempt(
        novel_dir: Path, attempt_id: str,
) -> dict[str, Any] | None:
    """把 batch 中的一项投影成 LlmClient 已有的 immutable resolution 形状。"""
    owner = batch_owner(Path(novel_dir), attempt_id)
    if owner is None:
        return None
    manifest_path, manifest = owner
    resolution_path = manifest_path.with_name(RESOLUTION_FILE)
    if not resolution_path.is_file():
        return None
    _validate_manifest_sources(Path(novel_dir), manifest_path, manifest)
    resolution = _validate_resolution(resolution_path, manifest)
    matches = [item for item in resolution.get('decisions', [])
               if isinstance(item, dict) and item.get('attempt_id') == attempt_id]
    if len(matches) != 1:
        raise ArtifactUnreadable(
            f'{resolution_path} 对 attempt={attempt_id} 必须恰好一项，实际 {len(matches)}')
    return {
        **matches[0],
        'version': VERSION,
        'submitted_at': resolution.get('submitted_at'),
        'actor': resolution.get('actor'),
        'batch_manifest': str(manifest_path),
        'batch_resolution': str(resolution_path),
    }


def _validate_resolution(
        resolution_path: Path, manifest: Mapping[str, Any],
) -> dict[str, Any]:
    resolution = _read_json(resolution_path)
    if resolution.get('resolution_sha256') != _resolution_hash(resolution):
        raise ArtifactUnreadable(f'{resolution_path} 的 resolution_sha256 不闭合')
    if (resolution.get('run_id') != manifest.get('run_id')
            or resolution.get('manifest_sha256') != manifest.get('manifest_sha256')):
        raise ProvenanceRefused(f'{resolution_path} 没有绑定当前 immutable manifest')
    decisions = resolution.get('decisions')
    if not isinstance(decisions, list):
        raise ArtifactUnreadable(f'{resolution_path} 缺 decisions 数组')
    expected = {item['attempt_id'] for item in manifest['items']}
    attempts = [row.get('attempt_id') for row in decisions if isinstance(row, dict)]
    if len(attempts) != len(decisions) or set(attempts) != expected \
            or len(attempts) != len(set(attempts)):
        raise ArtifactUnreadable(
            f'{resolution_path} 的 decision attempt 集合不等于 manifest')
    for row in decisions:
        action = row.get('action')
        if action not in ('submit', 'decline') or not str(row.get('reason') or '').strip():
            raise ArtifactUnreadable(f'{resolution_path} 含不合法 action/reason')
        if action == 'submit':
            candidate = row.get('candidate')
            if not isinstance(candidate, dict):
                raise ArtifactUnreadable(
                    f'{resolution_path} 的 submit 决定缺封存 candidate')
            if row.get('candidate_sha256') != _sha(candidate):
                raise ArtifactUnreadable(
                    f'{resolution_path} 的 embedded candidate SHA-256 不闭合')
    return resolution


def _validate_manifest_sources(
        novel_dir: Path, manifest_path: Path, manifest: Mapping[str, Any],
) -> None:
    """消费 batch 决定时重验它封存的 run/rejection 字节，堵住提交后的 TOCTOU。"""
    run_path = manifest_path.with_name(RUN_FILE)
    try:
        run_sha = _sha(run_path.read_bytes())
    except OSError as exc:
        raise ProvenanceRefused(f'{manifest_path} 绑定的 run.json 已缺失/不可读：{exc}') from exc
    if manifest.get('run_sha256') != run_sha:
        raise ProvenanceRefused(f'{manifest_path} 绑定的 run.json 已漂移')
    from novel.llm import response_repair
    for item in manifest.get('items', []):
        if not isinstance(item, dict):
            raise ArtifactUnreadable(f'{manifest_path} 的 repair item 不是对象')
        rejection_path = (
            Path(novel_dir) / paths.REJECTED_RESPONSE_DIRNAME
            / str(item.get('chain_id') or '')
            / str(item.get('attempt_id') or '')
            / response_repair.REJECTION_FILE)
        try:
            rejection_sha = _sha(rejection_path.read_bytes())
        except OSError as exc:
            raise ProvenanceRefused(
                f'{manifest_path} 绑定的 rejection 已缺失/不可读：{rejection_path}: {exc}') from exc
        if item.get('rejection_sha256') != rejection_sha:
            raise ProvenanceRefused(
                f'{manifest_path} 绑定的 rejection.json 已漂移：{rejection_path}')


def human_batch_resolved(novel_dir: Path, run_id: str) -> bool:
    root = _materialize_run_root(Path(novel_dir), run_id)
    manifest_path = root / MANIFEST_FILE
    resolution_path = root / RESOLUTION_FILE
    if not manifest_path.is_file() or not resolution_path.is_file():
        return False
    manifest = _validate_manifest(manifest_path)
    _validate_manifest_sources(Path(novel_dir), manifest_path, manifest)
    _validate_resolution(resolution_path, manifest)
    return True


def submit_batch(
        novel_dir: Path, manifest_path: Path, decisions_path: Path, *, actor: str,
) -> Path:
    """先全量校验，再原子封存一份 batch resolution；没有逐 attempt 半提交。"""
    from novel.llm import response_repair

    actor = actor.strip()
    if not actor:
        raise TerminalState('批量响应修复必须显式给非空 actor')
    root = Path(novel_dir).resolve()
    supplied = Path(manifest_path).resolve()
    # 先从 manifest 外壳取 run_id，再用当前 service 唯一落点重锚。
    supplied_manifest = _validate_manifest(supplied)
    run_id = check_run_id(str(supplied_manifest.get('run_id') or ''))
    current_root = _materialize_run_root(root, run_id).resolve()
    current_manifest = current_root / MANIFEST_FILE
    if supplied != current_manifest.resolve():
        raise TerminalState(f'{supplied} 不是登记的 Stage2 blueprint manifest 落点')
    manifest_path = current_manifest.resolve()
    manifest = _validate_manifest(manifest_path)
    if paths.stage2_blueprint_run_path(root, run_id) != manifest_path.parent:
        raise ProvenanceRefused(f'{manifest_path} 自报 run_id 与路径不一致')
    run_path = manifest_path.with_name(RUN_FILE)
    if not run_path.is_file() or manifest.get('run_sha256') != _sha(run_path.read_bytes()):
        raise ProvenanceRefused(f'{manifest_path} 绑定的 run.json 已漂移或缺失')
    try:
        decisions_raw = json.loads(Path(decisions_path).read_text(encoding='utf-8'))
    except FileNotFoundError:
        raise UpstreamMissing(f'decisions 文件 {decisions_path} 不在盘上') from None
    except (OSError, UnicodeError, json.JSONDecodeError) as exc:
        raise ArtifactUnreadable(f'decisions {decisions_path} 不是合法 UTF-8 JSON：{exc}') from exc
    rows = decisions_raw.get('decisions') if isinstance(decisions_raw, dict) else decisions_raw
    if not isinstance(rows, list):
        raise TerminalState('decisions 必须是数组，或含 decisions 数组的对象')
    by_attempt: dict[str, dict[str, Any]] = {}
    for row in rows:
        if not isinstance(row, dict):
            raise TerminalState('decisions 每项必须是对象')
        attempt_id = str(row.get('attempt_id') or '')
        if attempt_id in by_attempt:
            raise TerminalState(f'decisions 重复 attempt_id={attempt_id}')
        action = row.get('action')
        reason = str(row.get('reason') or '').strip()
        if action not in ('submit', 'decline') or not reason:
            raise TerminalState(
                f'attempt={attempt_id} 必须明确 action=submit|decline 与非空 reason')
        by_attempt[attempt_id] = {
            'attempt_id': attempt_id, 'action': action, 'reason': reason,
        }
    expected = {item['attempt_id'] for item in manifest['items']}
    if set(by_attempt) != expected:
        raise TerminalState(
            f'decisions attempt 集合必须与 manifest 完全一致；'
            f'缺={sorted(expected - set(by_attempt))}，多={sorted(set(by_attempt) - expected)}')

    sealed: list[dict[str, Any]] = []
    manifest_items = {item['attempt_id']: item for item in manifest['items']}
    for attempt_id in sorted(expected):
        item = manifest_items[attempt_id]
        record = response_repair.find_attempt(root, attempt_id)
        rejection_path = record.root / response_repair.REJECTION_FILE
        if item.get('rejection_sha256') != _sha(rejection_path.read_bytes()):
            raise ProvenanceRefused(f'attempt={attempt_id} 的 rejection SHA 已漂移')
        if (record.root / response_repair.RESOLUTION_FILE).is_file():
            raise TerminalState(
                f'attempt={attempt_id} 已有旧单项 resolution，不能混入 batch 双权威源')
        decision = dict(by_attempt[attempt_id])
        if decision['action'] == 'submit':
            candidate_path = response_repair.candidate_path(record)
            try:
                raw = candidate_path.read_bytes()
                candidate = json.loads(raw.decode('utf-8'))
            except FileNotFoundError:
                raise UpstreamMissing(f'请先创建/编辑固定候选文件 {candidate_path}') from None
            except (OSError, UnicodeError, json.JSONDecodeError) as exc:
                raise ArtifactUnreadable(
                    f'候选 {candidate_path} 不是合法 UTF-8 JSON：{exc}') from exc
            if not isinstance(candidate, dict):
                raise TerminalState(
                    f'候选 {candidate_path} 必须是响应顶层 object，不能是标量/数组')
            decision['candidate'] = candidate
            # 一个 SHA 绑定实际封存并会被消费的 canonical candidate；另一个只
            # 留下 operator 提交字节的证据。读取时绝不依赖仍可编辑的草稿。
            decision['candidate_sha256'] = _sha(candidate)
            decision['candidate_source_sha256'] = _sha(raw)
        sealed.append(decision)

    target = manifest_path.with_name(RESOLUTION_FILE)
    payload = {
        'version': VERSION,
        'submitted_at': _now(),
        'run_id': run_id,
        'sid': manifest.get('sid'),
        'node': manifest.get('node'),
        'manifest_sha256': manifest.get('manifest_sha256'),
        'actor': actor,
        'decisions': sealed,
    }
    payload['resolution_sha256'] = _resolution_hash(payload)
    with _LOCK:
        with process_append_lock(target):
            if target.is_file():
                raise TerminalState(
                    f'{target} 已有不可变 batch resolution；每个 run 最多人工 resume 一次')
            _atomic_json(target, payload)
    return target


def _commit_hash(payload: Mapping[str, Any]) -> str:
    return _sha({
        key: value for key, value in payload.items()
        if key not in ('prepared_at', 'committed_at', 'commit_sha256')
    })


def _validate_commit_state(ctx: Any, target: Path) -> dict[str, Any]:
    record = _read_json(target)
    if record.get('state') not in ('prepared', 'committed'):
        raise ArtifactUnreadable(f'{target} 缺合法 state=prepared|committed')
    if record.get('commit_sha256') != _commit_hash(record):
        raise ArtifactUnreadable(f'{target} 的 commit_sha256 不闭合')
    expected_identity = {
        'run_id': check_run_id(ctx.run_id),
        'sid': ctx.sid,
        'node': ctx.node,
        'code': ctx.code,
    }
    if {key: record.get(key) for key in expected_identity} != expected_identity:
        raise ProvenanceRefused(f'{target} 不属于当前 run/sid/node/code')
    run_path = target.with_name(RUN_FILE)
    try:
        current_run_sha = _sha(run_path.read_bytes())
    except OSError as exc:
        raise ProvenanceRefused(
            f'{target} 绑定的 run.json 已缺失或不可读：{exc}') from exc
    if record.get('run_sha256') != current_run_sha:
        raise ProvenanceRefused(f'{target} 绑定的 run.json 已漂移')
    artifact_sha = record.get('artifact_sha256')
    finalization_sha = record.get('finalization_sha256')
    if (not isinstance(record.get('artifact'), str)
            or not isinstance(record.get('artifact_fingerprint'), str)
            or not isinstance(artifact_sha, str) or len(artifact_sha) != 64
            or any(ch not in '0123456789abcdef' for ch in artifact_sha)
            or not isinstance(finalization_sha, str) or len(finalization_sha) != 64
            or any(ch not in '0123456789abcdef' for ch in finalization_sha)):
        raise ArtifactUnreadable(f'{target} 缺完整 artifact 身份')
    artifact_payload = record.get('artifact_payload')
    finalization_payload = record.get('finalization_payload')
    if artifact_payload is not None or finalization_payload is not None:
        if (not isinstance(artifact_payload, dict)
                or not isinstance(finalization_payload, dict)):
            raise ArtifactUnreadable(
                f'{target} 的提交候选/终态快照必须成对出现')
        if record.get('artifact_sha256') != _sha(artifact_payload):
            raise ArtifactUnreadable(f'{target} 的 artifact_payload 指纹不闭合')
        if record.get('finalization_sha256') != _sha(finalization_payload):
            raise ArtifactUnreadable(
                f'{target} 的 finalization_payload 指纹不闭合')
    rows = record.get('adoptions')
    attempts = record.get('attempts')
    if (not isinstance(rows, list) or not rows
            or not isinstance(attempts, list) or not attempts):
        raise ArtifactUnreadable(f'{target} 缺非空 adoptions/attempts 数组')
    seen_purposes: set[str] = set()
    seen_attempts: set[str] = set()
    for row in rows:
        if not isinstance(row, dict):
            raise ArtifactUnreadable(f'{target} 的 adoption 不是对象')
        purpose = row.get('purpose')
        attempt_id = row.get('attempt_id')
        chapter = row.get('chapter')
        identity = row.get('request_identity')
        if (not isinstance(purpose, str) or not purpose
                or not isinstance(attempt_id, str) or not attempt_id
                or (chapter is not None
                    and (not isinstance(chapter, int) or isinstance(chapter, bool)))
                or not isinstance(identity, dict)
                or identity.get('attempt_id') != attempt_id):
            raise ArtifactUnreadable(f'{target} 的 adoption 身份不完整：{row!r}')
        if purpose in seen_purposes or attempt_id in seen_attempts:
            raise ArtifactUnreadable(f'{target} 的 adoption purpose/attempt 重复')
        seen_purposes.add(purpose)
        seen_attempts.add(attempt_id)
    if attempts != sorted(seen_attempts):
        raise ArtifactUnreadable(f'{target} 的 attempts 与 adoptions 不闭合')
    if record['state'] == 'prepared' and not isinstance(record.get('prepared_at'), str):
        raise ArtifactUnreadable(f'{target} 的 prepared 状态缺 prepared_at')
    if record['state'] == 'committed' and not isinstance(record.get('committed_at'), str):
        raise ArtifactUnreadable(f'{target} 的 committed 状态缺 committed_at')
    return record


def commit_state(ctx: Any) -> dict[str, Any] | None:
    """返回已验证的提交 journal；prepared 只授权幂等尾窗，不代表阶段完成。"""
    target = _run_root(ctx) / COMMITTED_FILE
    return _validate_commit_state(ctx, target) if target.is_file() else None


def committed(ctx: Any) -> dict[str, Any] | None:
    record = commit_state(ctx)
    return record if record is not None and record.get('state') == 'committed' else None


def prepare_commit(
        ctx: Any, *, artifact: str, artifact_fingerprint: str,
        artifact_payload: Mapping[str, Any],
        finalization_payload: Mapping[str, Any],
        adoptions: Iterable[tuple[str, int | None, str]],
) -> dict[str, Any]:
    """在正式写盘前封存候选 SHA 与 adoption 映射，供提交尾窗幂等恢复。"""
    rows: list[dict[str, Any]] = []
    for purpose, chapter, attempt_id in sorted(
            adoptions, key=lambda row: (row[0], row[2])):
        if attempt_id not in ctx.ledger.attempt_ids_for_purpose(purpose):
            raise ProvenanceRefused(
                f'{purpose}: accepted attempt={attempt_id} 不属于当前 ledger purpose')
        rows.append({
            'purpose': purpose,
            'chapter': chapter,
            'attempt_id': attempt_id,
            'request_identity': ctx.ledger.request_identity_for_attempt(attempt_id),
        })
    if not rows:
        raise ProvenanceRefused('Stage2 commit journal 至少要联结一份 accepted attempt')
    attempts = sorted(row['attempt_id'] for row in rows)
    if len(attempts) != len(set(attempts)):
        raise ProvenanceRefused('一份 Stage2 commit journal 不能让同一 attempt 归属多个 purpose')
    run_path = _run_root(ctx) / RUN_FILE
    try:
        run_sha = _sha(run_path.read_bytes())
    except OSError as exc:
        raise ProvenanceRefused(
            f'缺少可验证的 {run_path}，不能准备正式提交：{exc}') from exc
    payload: dict[str, Any] = {
        'version': VERSION,
        'state': 'prepared',
        'prepared_at': _now(),
        'run_id': check_run_id(ctx.run_id),
        'sid': ctx.sid,
        'node': ctx.node,
        'code': ctx.code,
        'run_sha256': run_sha,
        'artifact': artifact,
        'artifact_fingerprint': artifact_fingerprint,
        'artifact_sha256': _sha(dict(artifact_payload)),
        'finalization_sha256': _sha(dict(finalization_payload)),
        # 候选正文封存在按 sid 隔离的 `_service/runs/<sid>/stage2`，
        # 不混入正式 artifact。只存 hash 会在 prepare→formal 崩溃时丢掉
        # 那份已付费、已通过代码校验的唯一候选，重启后无法零 wire 收尾；
        # 因此它属于默认不删的恢复 checkpoint，而不是可随意轮转的日志。
        'artifact_payload': copy.deepcopy(dict(artifact_payload)),
        'finalization_payload': copy.deepcopy(dict(finalization_payload)),
        'attempts': attempts,
        'adoptions': rows,
    }
    payload['commit_sha256'] = _commit_hash(payload)
    target = _run_root(ctx) / COMMITTED_FILE
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
                        f'{target} 已绑定另一份 artifact/adoption 集合；拒绝覆盖提交 journal')
                return current
            _atomic_json(target, payload)
    return payload


def verify_commit_artifact(
        record: Mapping[str, Any], artifact_payload: Mapping[str, Any],
) -> bool:
    """typed artifact 是否逐 canonical byte 命中提交 journal。"""
    return record.get('artifact_sha256') == _sha(dict(artifact_payload))


def verify_commit_finalization(
        record: Mapping[str, Any], finalization_payload: Mapping[str, Any],
) -> bool:
    """提交尾窗的当前上游/目标集是否仍与 prepare 时逐字闭合。"""
    return record.get('finalization_sha256') == _sha(dict(finalization_payload))


def restore_commit_payloads(
        ctx: Any, record: Mapping[str, Any] | None = None,
) -> tuple[dict[str, Any], dict[str, Any]]:
    """读回 prepare 时封存的已校验候选与终态快照。

    旧 journal 只存指纹，只能在 formal 已落时补尾；它不能在 formal
    之前凭 hash 还原正文。新 journal 缺任一 payload 一律关闭式拒绝，
    绝不重调模型猜一份“大概相同”的结果。
    """
    current = dict(record) if record is not None else commit_state(ctx)
    if current is None:
        raise ProvenanceRefused(
            f'run={ctx.run_id} 缺 commit journal，不能恢复提交候选')
    target = _run_root(ctx) / COMMITTED_FILE
    validated = _validate_commit_state(ctx, target)
    if current != validated:
        raise ProvenanceRefused(
            f'{target} 在读取提交候选期间已漂移')
    artifact_payload = validated.get('artifact_payload')
    finalization_payload = validated.get('finalization_payload')
    if (not isinstance(artifact_payload, dict)
            or not isinstance(finalization_payload, dict)):
        raise ProvenanceRefused(
            f'{target} 是旧的指纹-only journal；formal 尚未落盘时'
            '无法零 wire 还原候选，拒绝补猜')
    return copy.deepcopy(artifact_payload), copy.deepcopy(finalization_payload)


def restore_commit_adoptions(
        ctx: Any, record: Mapping[str, Any],
) -> tuple[tuple[str, int | None, str], ...]:
    """从 journal 恢复 accepted attempts，并逐项重验盘上 ledger 请求身份。"""
    restored: list[tuple[str, int | None, str]] = []
    for row in record.get('adoptions', []):
        assert isinstance(row, dict)
        purpose = str(row['purpose'])
        attempt_id = str(row['attempt_id'])
        identity = row['request_identity']
        assert isinstance(identity, dict)
        fingerprint = str(identity.get('request_fingerprint') or '')
        ctx.ledger.claim_prior_attempts(
            [attempt_id], sid=ctx.sid, node=ctx.node, purpose=purpose,
            request_fingerprints=[fingerprint])
        if ctx.ledger.request_identity_for_attempt(attempt_id) != identity:
            raise ProvenanceRefused(
                f'{purpose}: commit journal 的 attempt={attempt_id} 与当前 ledger 不闭合')
        chapter = row.get('chapter')
        restored.append((purpose, chapter if isinstance(chapter, int) else None, attempt_id))
    return tuple(restored)


def mark_committed(ctx: Any) -> Path:
    """正式 artifact/adoption 完成后，把 prepared journal 原子提升成 committed。"""
    target = _run_root(ctx) / COMMITTED_FILE
    with _LOCK:
        with process_append_lock(target):
            if not target.is_file():
                raise ProvenanceRefused(
                    f'{target} 缺 prepared commit journal；不能在写盘后补猜 artifact/attempt')
            current = _validate_commit_state(ctx, target)
            if current.get('state') == 'committed':
                return target
            payload = {
                **current,
                'state': 'committed',
                'committed_at': _now(),
            }
            payload['commit_sha256'] = _commit_hash(payload)
            _atomic_json(target, payload)
    return target
