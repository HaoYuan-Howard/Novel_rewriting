# -*- coding: utf-8 -*-
"""Stage4 design/plan 对通用 single-session agent run core 的业务适配。

``stage4.plan`` 仍是一个独立 single-session run；``stage4.design`` v2 则是
strategy + ch1..N 各一条 ``SingleSessionRunSpec``，单元通过后就落
append-only checkpoint，最后由本模块的 aggregate journal 冻结 N+1 份
adoption 与整书候选。通用 profile/prompt/schema/stdout/ledger/session-audit
闭环仍由 ``novel.llm.agent_runs`` 唯一实现。

typed infeasible 只有 binding；bound bundle 还含 plan/context；同书任一
foreign ``prepared`` 都阻止另一 run/node 写正式路径。
"""
from __future__ import annotations

import copy
import json
import os
import threading
from collections.abc import Callable, Mapping, Sequence
from contextlib import contextmanager
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, TypeVar

from pydantic import BaseModel

from contracts.artifacts import (
    STAGE4_BLIND_CANDIDATE, STAGE4_EVIDENCE_BINDING,
    STAGE4_PLAN_CHAPTERS, STAGE4_WRITER_CONTEXT,
)
from contracts.invariants import STAGE4_DESIGN_MAX_CONCURRENCY
from contracts.schema import Usage
from novel import paths, prov
from novel.appendlog import process_append_lock
from novel.errors import ArtifactUnreadable, ProvenanceRefused
from novel.llm import agent_runs
from novel.llm.transport import ChatResponse
from novel.run_identity import check_run_id


M = TypeVar('M', bound=BaseModel)

RECOVERY_POLICIES = {
    'stage4.design': 'stage4-design-v1',
    'stage4.plan': 'stage4-plan-v1',
}

# design v2 不再是一个全书 session；策略和各章 purpose 各自恢复。
# 该 discriminator 由后续 producer 接入 SessionAgentClient 时一并登记，
# 本模块先用它冻结 run.json，避免新旧架构被当成同一恢复链。
DESIGN_UNIT_RECOVERY_POLICY = 'stage4-design-v2'
DESIGN_MAX_CONCURRENCY = STAGE4_DESIGN_MAX_CONCURRENCY
DESIGN_AGGREGATE_COMMIT_FILE = 'aggregate-committed.json'

_DESIGN_AGGREGATE_VERSION = 1
_DESIGN_AGGREGATE_KIND = 'stage4-design-aggregate/v1'
_DESIGN_AGGREGATE_FIELDS = {
    'version', 'kind', 'state', 'prepared_at', 'committed_at',
    'run_id', 'sid', 'node', 'code', 'primary_artifact',
    'primary_payload_sha256', 'primary_payload_fingerprint',
    'primary_payload', 'finalization_sha256', 'finalization_payload',
    'touched_paths', 'baselines', 'attempts', 'adoptions',
    'landed_payload_sha256s', 'post_file_sha256s', 'commit_sha256',
}
_DESIGN_ADOPTION_FIELDS = {
    'unit_key', 'purpose', 'chapter', 'attempt_id',
    'run_sha256', 'checkpoint_revision', 'checkpoint_sha256',
    'checkpoint_file_sha256', 'provider_session_id', 'request_identity',
}
_LEDGER_IDENTITY_FIELDS = {
    'attempt_id', 'request_fingerprint', 'model', 'request_model',
    'reasoning_effort', 'thinking', 'json_mode', 'max_tokens',
    'response_sha256',
}

_PLAN_INVALID = paths.invalid_of(STAGE4_PLAN_CHAPTERS)
_OWNERSHIP_REGISTRY_LOCK = threading.Lock()
_OWNERSHIP_THREAD_LOCKS: dict[str, threading.RLock] = {}
_OWNERSHIP_LOCAL = threading.local()
_DESIGN_AGGREGATE_LOCK = threading.RLock()


@dataclass(frozen=True)
class DesignUnitRequest:
    """一份 strategy/单章请求的完整恢复身份。

    聚合提交不从当前默认配置反推 prompt/schema；调用方把当前
    确定性构建出的请求传入，本模块逐项对照 immutable run/checkpoint。
    """

    chapter: int | None
    input_sha256: str
    system: str
    user: str
    response_model: type[BaseModel]
    max_tokens: int


def recovery_policy(node: str) -> str:
    """返回 Stage4 节点唯一恢复策略；节点名不是自由输入。"""
    try:
        return RECOVERY_POLICIES[node]
    except KeyError:
        raise ProvenanceRefused(
            f'Stage4 agent node 必须属于 {tuple(RECOVERY_POLICIES)}，实得 {node!r}') from None


def design_unit_key(chapter: int | None) -> str:
    """strategy/单章的稳定聚合键，与路径片段完全一致。"""
    return paths.stage4_design_unit_dirname(chapter)


def design_unit_purpose(chapter: int | None) -> str:
    """每个 design unit 的 ledger/session purpose；不与旧全书 purpose 混用。"""
    return f'stage4.design:{design_unit_key(chapter)}'


def design_unit_run_spec(chapter: int | None) -> agent_runs.SingleSessionRunSpec:
    """返回 strategy/单章独立的通用 single-session run 声明。"""
    key = design_unit_key(chapter)
    purpose = design_unit_purpose(chapter)
    return agent_runs.SingleSessionRunSpec(
        label=f'Stage4 design unit {key}',
        node='stage4.design',
        session_owner=purpose,
        purpose=purpose,
        chapter=chapter,
        phase='strategy' if chapter is None else 'chapter',
        recovery_policy=DESIGN_UNIT_RECOVERY_POLICY,
        session_isolation='per-purpose',
        run_path=lambda ctx: paths.stage4_design_unit_run_path(
            ctx.novel_dir, ctx.run_id, chapter),
        checkpoint_dir=lambda ctx: paths.stage4_design_unit_checkpoint_dir(
            ctx.novel_dir, ctx.run_id, chapter),
        incompatible_session_nodes=('stage4.plan',),
    )


def ensure_design_unit_run(
        ctx: Any, request: DesignUnitRequest,
) -> dict[str, Any]:
    """创建或复验一份 strategy/单章 immutable run。"""
    return agent_runs.ensure_run(
        ctx, design_unit_run_spec(request.chapter),
        input_sha256=request.input_sha256,
        system=request.system, user=request.user,
        response_model=request.response_model,
        max_tokens=request.max_tokens)


def write_design_unit_checkpoint(
        ctx: Any, request: DesignUnitRequest, *,
        response_payload: Mapping[str, Any], acceptance: Mapping[str, Any],
        state_payload: Mapping[str, Any], revision_key: str = 'terminal',
) -> Path:
    """一份 unit 业务验证通过后立即追加检查点。"""
    return agent_runs.write_checkpoint(
        ctx, design_unit_run_spec(request.chapter),
        input_sha256=request.input_sha256,
        system=request.system, user=request.user,
        response_model=request.response_model,
        max_tokens=request.max_tokens,
        response_payload=response_payload,
        acceptance=acceptance,
        revision_key=revision_key,
        state_payload=state_payload)


def restore_design_unit_checkpoint(
        ctx: Any, request: DesignUnitRequest, *,
        validator: Callable[[BaseModel, ChatResponse], str | None] | None = None,
) -> agent_runs.RestoredCheckpoint[BaseModel] | None:
    """只恢复该 unit 的 latest accepted revision；其他章失败不影响它。"""
    return agent_runs.restore_checkpoint(
        ctx, design_unit_run_spec(request.chapter),
        input_sha256=request.input_sha256,
        system=request.system, user=request.user,
        response_model=request.response_model,
        max_tokens=request.max_tokens,
        validator=validator)


def design_aggregate_usage(
        ctx: Any, units: Sequence[DesignUnitRequest],
) -> Usage:
    """精确聚合 strategy + ch1..N 这一 run 的全部调用。

    accepted checkpoint 的 ``attempt_links`` 是可核验的最终采纳链，
    但不是完整成本边界：某 unit 曾在没有 checkpoint 时失败，
    同 run 新进程里成功落下的 checkpoint 不会知道旧进程那些调用。

    本口先用当前 prompt/schema/profile 完整恢复每份 checkpoint，
    再把 N+1 个稳定 purpose 与封存的 links 交给
    ``CostLedger.usage_for_complete_purposes``。后者以
    ``run_id + sid + node + purpose`` 选出盘上（或 path=None 时的内存）
    全部 attempts，links 只作为必须存在且完全相等的子集锚点。
    因此结果不受当前 ``RunContext`` 的
    UsageMark、已恢复单元顺序或不相关调用影响，可直接作为
    ``BlindCandidateProvenance.usage``。
    """
    normalized_units = _normalized_design_units(units)
    identities: list[tuple[str, Mapping[str, object]]] = []
    seen_attempts: set[str] = set()
    accepted_attempts: set[str] = set()
    provider_sessions: set[str] = set()
    for unit in normalized_units:
        purpose = design_unit_purpose(unit.chapter)
        restored = restore_design_unit_checkpoint(ctx, unit)
        if restored is None:
            raise ProvenanceRefused(
                f'{purpose}: usage 聚合前缺 accepted checkpoint')
        acceptance = restored.acceptance
        accepted_attempt = str(acceptance.get('attempt_id') or '')
        provider_session = str(acceptance.get('provider_session_id') or '')
        links = acceptance.get('attempt_links')
        if (not _is_attempt_id(accepted_attempt)
                or accepted_attempt in accepted_attempts
                or not provider_session
                or provider_session in provider_sessions
                or not isinstance(links, list) or not links):
            raise ProvenanceRefused(
                f'{purpose}: accepted attempt/session/attempt_links 不闭合')
        accepted_attempts.add(accepted_attempt)
        provider_sessions.add(provider_session)
        linked_attempts: set[str] = set()
        for link in links:
            if not isinstance(link, Mapping):
                raise ProvenanceRefused(f'{purpose}: attempt_links 含非对象')
            identity = dict(link)
            attempt_id = identity.get('attempt_id')
            if (not _is_attempt_id(attempt_id)
                    or attempt_id in linked_attempts
                    or attempt_id in seen_attempts):
                raise ProvenanceRefused(
                    f'{purpose}: attempt_links 含重复/不合法 attempt')
            linked_attempts.add(str(attempt_id))
            seen_attempts.add(str(attempt_id))
            identities.append((purpose, identity))
        if accepted_attempt not in linked_attempts:
            raise ProvenanceRefused(
                f'{purpose}: accepted attempt 未被 attempt_links 联结')
    if len(accepted_attempts) != len(normalized_units):
        raise ProvenanceRefused('Stage4 design usage 缺 N+1 accepted attempts')
    usage = ctx.ledger.usage_for_complete_purposes(
        [design_unit_purpose(unit.chapter) for unit in normalized_units],
        sid=ctx.sid, node='stage4.design', required_identities=identities)
    if usage is None:
        raise ProvenanceRefused(
            'Stage4 design 已有 N+1 accepted checkpoints，但精确 usage 为空')
    return usage


def _run_spec(node: str) -> agent_runs.SingleSessionRunSpec:
    recovery = recovery_policy(node)
    other = tuple(value for value in RECOVERY_POLICIES if value != node)
    return agent_runs.SingleSessionRunSpec(
        label=f'Stage4 {node.removeprefix("stage4.")}',
        node=node,
        session_owner=node,
        purpose=node,
        chapter=None,
        phase=node.removeprefix('stage4.'),
        recovery_policy=recovery,
        session_isolation='per-node',
        run_path=lambda ctx: paths.stage4_run_path(
            ctx.novel_dir, ctx.run_id, node),
        checkpoint_dir=lambda ctx: paths.stage4_checkpoint_dir(
            ctx.novel_dir, ctx.run_id, node),
        incompatible_session_nodes=other,
    )


def _commit_spec(node: str) -> agent_runs.CommitBundleSpec:
    run = _run_spec(node)
    if node == 'stage4.design':
        primary = STAGE4_BLIND_CANDIDATE
        allowed = ((primary,),)
        nonformal: tuple[str, ...] = ()
    else:
        primary = STAGE4_EVIDENCE_BINDING
        allowed = (
            (primary,),
            (primary, _PLAN_INVALID),
            (primary, STAGE4_PLAN_CHAPTERS, STAGE4_WRITER_CONTEXT, _PLAN_INVALID),
        )
        nonformal = (_PLAN_INVALID,)
    return agent_runs.CommitBundleSpec(
        run=run,
        commit_path=lambda ctx: (
            paths.stage4_run_path(ctx.novel_dir, ctx.run_id, node)
            / agent_runs.COMMIT_FILE),
        primary_artifact=primary,
        allowed_touched=allowed,
        nonformal_paths=nonformal,
    )


def ensure_run(
        ctx: Any, *, input_sha256: str, system: str, user: str,
        response_model: type[BaseModel], max_tokens: int,
) -> dict[str, Any]:
    return agent_runs.ensure_run(
        ctx, _run_spec(ctx.node), input_sha256=input_sha256,
        system=system, user=user, response_model=response_model,
        max_tokens=max_tokens)


def write_checkpoint(
        ctx: Any, *, input_sha256: str, system: str, user: str,
        response_model: type[BaseModel], max_tokens: int,
        response_payload: Mapping[str, Any], acceptance: Mapping[str, Any],
        state_payload: Mapping[str, Any], revision_key: str = 'terminal',
) -> Path:
    """追加 Stage4 accepted 候选。

    design 只有终态 revision，沿用默认 ``terminal``；plan 的业务 HARD
    返工循环用 ``candidate``，每一份可物化候选都保留在同一哈希链里。
    """
    return agent_runs.write_checkpoint(
        ctx, _run_spec(ctx.node), input_sha256=input_sha256,
        system=system, user=user, response_model=response_model,
        max_tokens=max_tokens, response_payload=response_payload,
        acceptance=acceptance, revision_key=revision_key,
        state_payload=state_payload)


def restore_checkpoint(
        ctx: Any, *, input_sha256: str, system: str, user: str,
        response_model: type[M], max_tokens: int,
        validator: Callable[[M, ChatResponse], str | None] | None = None,
) -> agent_runs.RestoredCheckpoint[M] | None:
    return agent_runs.restore_checkpoint(
        ctx, _run_spec(ctx.node), input_sha256=input_sha256,
        system=system, user=user, response_model=response_model,
        max_tokens=max_tokens, validator=validator)


def _ownership_thread_lock(path: Path) -> threading.RLock:
    key = os.path.normcase(str(path.resolve()))
    with _OWNERSHIP_REGISTRY_LOCK:
        lock = _OWNERSHIP_THREAD_LOCKS.get(key)
        if lock is None:
            lock = _OWNERSHIP_THREAD_LOCKS[key] = threading.RLock()
        return lock


def _ownership_key(novel_dir: Path) -> str:
    return os.path.normcase(str(paths.stage4_commit_lock_path(novel_dir).resolve()))


@contextmanager
def commit_ownership(novel_dir: Path):
    """同一本书 design/plan 的正式写盘与恢复共用线程/进程锁。"""
    target = paths.stage4_commit_lock_path(novel_dir)
    key = _ownership_key(Path(novel_dir))
    held = getattr(_OWNERSHIP_LOCAL, 'depths', None)
    if held is None:
        held = _OWNERSHIP_LOCAL.depths = {}
    with _ownership_thread_lock(target):
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
            'Stage4 commit 必须在 commit_ownership(novel_dir) 临界区内完成 '
            'prepare、正式写盘、readback、adoption 与 committed')


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec='seconds')


def _atomic_json(path: Path, payload: Mapping[str, Any]) -> None:
    # 原子 JSON 写口仍只在通用 agent run core 一处；Stage4 只定义
    # aggregate 业务状态，不再复制 tempfile/fsync/replace 协议。
    agent_runs._atomic_json(path, payload)


def _read_object(path: Path, *, label: str) -> dict[str, Any]:
    try:
        raw = path.read_bytes()
        value = json.loads(raw.decode('utf-8'))
    except FileNotFoundError:
        raise ProvenanceRefused(f'{label} {path} 不在盘上') from None
    except (OSError, UnicodeError, json.JSONDecodeError) as exc:
        raise ArtifactUnreadable(f'{label} {path} 读不出来：{exc}') from exc
    if not isinstance(value, dict):
        raise ArtifactUnreadable(f'{label} {path} 不是 JSON object')
    return value


def _design_aggregate_commit_path(ctx: Any) -> Path:
    return (paths.stage4_run_path(
        ctx.novel_dir, ctx.run_id, 'stage4.design')
        / DESIGN_AGGREGATE_COMMIT_FILE)


def _design_aggregate_hash(payload: Mapping[str, Any]) -> str:
    return agent_runs.canonical_sha256({
        key: value for key, value in payload.items()
        if key not in ('prepared_at', 'committed_at', 'commit_sha256')
    })


def _is_attempt_id(value: object) -> bool:
    return (isinstance(value, str) and len(value) == 16
            and all(char in '0123456789abcdef' for char in value))


def _normalized_design_units(
        units: Sequence[DesignUnitRequest],
) -> tuple[DesignUnitRequest, ...]:
    if not isinstance(units, Sequence) or isinstance(units, (str, bytes)):
        raise ProvenanceRefused('Stage4 design units 必须是非空请求序列')
    by_key: dict[str, DesignUnitRequest] = {}
    for unit in units:
        if not isinstance(unit, DesignUnitRequest):
            raise ProvenanceRefused(
                'Stage4 design units 每项必须是 DesignUnitRequest')
        key = design_unit_key(unit.chapter)
        if key in by_key:
            raise ProvenanceRefused(f'Stage4 design unit={key} 重复')
        by_key[key] = unit
    strategy = by_key.get('strategy')
    chapters = sorted(
        unit.chapter for unit in by_key.values() if unit.chapter is not None)
    if strategy is None or not chapters:
        raise ProvenanceRefused(
            'Stage4 design aggregate 必须恰有一份 strategy 与至少一章')
    expected = list(range(1, chapters[-1] + 1))
    if chapters != expected:
        raise ProvenanceRefused(
            f'Stage4 design aggregate 章集必须闭合为 1..N，实得 {chapters}')
    return (strategy, *(by_key[f'ch{chapter:04d}'] for chapter in chapters))


def _checkpoint_revision_path(ctx: Any, chapter: int | None, revision: int) -> Path:
    return (paths.stage4_design_unit_checkpoint_dir(
        ctx.novel_dir, ctx.run_id, chapter) / f'rev-{revision:04d}.json')


def _validate_design_adoption_source(
        ctx: Any, target: Path, row: Mapping[str, Any],
) -> None:
    chapter = row.get('chapter')
    run_path = paths.stage4_design_unit_run_path(
        ctx.novel_dir, ctx.run_id,
        chapter if isinstance(chapter, int) else None) / agent_runs.RUN_FILE
    try:
        run_bytes = run_path.read_bytes()
    except OSError as exc:
        raise ProvenanceRefused(
            f'{target} 绑定的 unit run 不可读：{run_path}: {exc}') from exc
    if row.get('run_sha256') != agent_runs.canonical_sha256(run_bytes):
        raise ProvenanceRefused(f'{target} 绑定的 unit run.json 已漂移')
    run = _read_object(run_path, label='Stage4 design unit run')
    expected_run = {
        'run_id': check_run_id(ctx.run_id), 'sid': ctx.sid,
        'node': 'stage4.design', 'code': ctx.code,
        'chapter': chapter, 'purpose': row.get('purpose'),
        'session_owner': row.get('purpose'),
    }
    if {key: run.get(key) for key in expected_run} != expected_run:
        raise ProvenanceRefused(f'{run_path} 不属于当前 unit/run/sid/code')

    revision = int(row['checkpoint_revision'])
    checkpoint_path = _checkpoint_revision_path(ctx, chapter, revision)
    try:
        checkpoint_bytes = checkpoint_path.read_bytes()
    except OSError as exc:
        raise ProvenanceRefused(
            f'{target} 绑定的 unit checkpoint 不可读：'
            f'{checkpoint_path}: {exc}') from exc
    if row.get('checkpoint_file_sha256') != agent_runs.canonical_sha256(
            checkpoint_bytes):
        raise ProvenanceRefused(f'{checkpoint_path} 已被改写')
    checkpoint = _read_object(
        checkpoint_path, label='Stage4 design unit checkpoint')
    identity = row.get('request_identity')
    expected_checkpoint = {
        'run_id': check_run_id(ctx.run_id), 'sid': ctx.sid,
        'node': 'stage4.design', 'code': ctx.code,
        'chapter': chapter, 'purpose': row.get('purpose'),
        'run_sha256': row.get('run_sha256'),
        'revision': revision,
        'checkpoint_sha256': row.get('checkpoint_sha256'),
        'attempt_id': row.get('attempt_id'),
        'request_fingerprint': identity.get('request_fingerprint'),
        'model': identity.get('model'),
        'ledger_response_sha256': identity.get('response_sha256'),
        'provider_session_id': row.get('provider_session_id'),
    }
    if {key: checkpoint.get(key) for key in expected_checkpoint} != expected_checkpoint:
        raise ProvenanceRefused(
            f'{checkpoint_path} 与 aggregate adoption 身份漂移')


def _validate_design_aggregate_state(
        ctx: Any, target: Path,
) -> dict[str, Any]:
    if ctx.node != 'stage4.design':
        raise ProvenanceRefused('design aggregate journal 只属于 stage4.design')
    record = _read_object(target, label='Stage4 design aggregate commit')
    if set(record) != _DESIGN_AGGREGATE_FIELDS:
        raise ArtifactUnreadable(f'{target} aggregate commit 字段漂移')
    if record.get('commit_sha256') != _design_aggregate_hash(record):
        raise ArtifactUnreadable(f'{target} aggregate commit SHA 不闭合')
    expected = {
        'version': _DESIGN_AGGREGATE_VERSION,
        'kind': _DESIGN_AGGREGATE_KIND,
        'run_id': check_run_id(ctx.run_id), 'sid': ctx.sid,
        'node': 'stage4.design', 'code': ctx.code,
        'primary_artifact': STAGE4_BLIND_CANDIDATE,
    }
    if {key: record.get(key) for key in expected} != expected:
        raise ProvenanceRefused(f'{target} 不属于当前 design run/sid/code')
    if record.get('state') not in ('prepared', 'committed'):
        raise ArtifactUnreadable(f'{target} aggregate state 非法')

    primary = record.get('primary_payload')
    finalization = record.get('finalization_payload')
    if (not isinstance(primary, dict) or not isinstance(finalization, dict)
            or record.get('primary_payload_sha256')
            != agent_runs.canonical_sha256(primary)
            or record.get('primary_payload_fingerprint')
            != prov.projection_fingerprint(primary)
            or record.get('finalization_sha256')
            != agent_runs.canonical_sha256(finalization)):
        raise ArtifactUnreadable(f'{target} 封存的候选/finalization 不闭合')
    touched = record.get('touched_paths')
    baselines = record.get('baselines')
    if (touched != [STAGE4_BLIND_CANDIDATE]
            or not isinstance(baselines, dict)
            or set(baselines) != {STAGE4_BLIND_CANDIDATE}
            or (baselines[STAGE4_BLIND_CANDIDATE] is not None
                and not agent_runs.is_sha256(
                    baselines[STAGE4_BLIND_CANDIDATE]))):
        raise ArtifactUnreadable(f'{target} touched/baseline 不闭合')

    rows = record.get('adoptions')
    attempts = record.get('attempts')
    if (not isinstance(rows, list) or len(rows) < 2
            or not isinstance(attempts, list)):
        raise ArtifactUnreadable(f'{target} 缺 N+1 aggregate adoptions')
    seen_keys: set[str] = set()
    seen_purposes: set[str] = set()
    seen_attempts: set[str] = set()
    seen_sessions: set[str] = set()
    chapters: list[int] = []
    strategy_count = 0
    for row in rows:
        if not isinstance(row, dict) or set(row) != _DESIGN_ADOPTION_FIELDS:
            raise ArtifactUnreadable(f'{target} adoption 字段漂移')
        chapter = row.get('chapter')
        if (chapter is not None
                and (not isinstance(chapter, int) or isinstance(chapter, bool)
                     or chapter < 1)):
            raise ArtifactUnreadable(f'{target} adoption 章号非法')
        key = design_unit_key(chapter)
        purpose = design_unit_purpose(chapter)
        attempt_id = row.get('attempt_id')
        session_id = row.get('provider_session_id')
        identity = row.get('request_identity')
        revision = row.get('checkpoint_revision')
        if (row.get('unit_key') != key or row.get('purpose') != purpose
                or key in seen_keys or purpose in seen_purposes
                or not _is_attempt_id(attempt_id)
                or attempt_id in seen_attempts
                or not isinstance(session_id, str) or not session_id
                or session_id in seen_sessions
                or not isinstance(revision, int) or isinstance(revision, bool)
                or revision < 1
                or not agent_runs.is_sha256(row.get('run_sha256'))
                or not agent_runs.is_sha256(row.get('checkpoint_sha256'))
                or not agent_runs.is_sha256(row.get('checkpoint_file_sha256'))
                or not isinstance(identity, dict)
                or set(identity) != _LEDGER_IDENTITY_FIELDS
                or identity.get('attempt_id') != attempt_id):
            raise ArtifactUnreadable(f'{target} adoption 身份不闭合')
        if chapter is None:
            strategy_count += 1
        else:
            chapters.append(chapter)
        seen_keys.add(key)
        seen_purposes.add(purpose)
        seen_attempts.add(str(attempt_id))
        seen_sessions.add(session_id)
        _validate_design_adoption_source(ctx, target, row)
    if (strategy_count != 1
            or sorted(chapters) != list(range(1, len(chapters) + 1))
            or attempts != sorted(seen_attempts)):
        raise ArtifactUnreadable(
            f'{target} strategy/1..N/attempts 集合不闭合')

    landed = record.get('landed_payload_sha256s')
    post = record.get('post_file_sha256s')
    if record['state'] == 'prepared':
        if (not isinstance(record.get('prepared_at'), str)
                or record.get('committed_at') is not None
                or landed is not None or post is not None):
            raise ArtifactUnreadable(f'{target} prepared 字段不闭合')
    elif (not isinstance(record.get('prepared_at'), str)
          or not isinstance(record.get('committed_at'), str)
          or not isinstance(landed, dict)
          or set(landed) != {STAGE4_BLIND_CANDIDATE}
          or not agent_runs.is_sha256(landed[STAGE4_BLIND_CANDIDATE])
          or not isinstance(post, dict)
          or set(post) != {STAGE4_BLIND_CANDIDATE}
          or not agent_runs.is_sha256(post[STAGE4_BLIND_CANDIDATE])):
        raise ArtifactUnreadable(f'{target} committed 字段不闭合')
    return record


def design_aggregate_state(ctx: Any) -> dict[str, Any] | None:
    """返回当前 design aggregate journal；prepared 只授权补完尾窗。"""
    target = _design_aggregate_commit_path(ctx)
    return _validate_design_aggregate_state(ctx, target) if target.is_file() else None


def design_aggregate_prepared(ctx: Any) -> dict[str, Any] | None:
    record = design_aggregate_state(ctx)
    return record if record is not None and record.get('state') == 'prepared' else None


def design_aggregate_committed(ctx: Any) -> dict[str, Any] | None:
    record = design_aggregate_state(ctx)
    return record if record is not None and record.get('state') == 'committed' else None


def prepared(ctx: Any) -> dict[str, Any] | None:
    record = agent_runs.commit_state(ctx, _commit_spec(ctx.node))
    return record if record is not None and record.get('state') == 'prepared' else None


def committed(ctx: Any) -> dict[str, Any] | None:
    record = agent_runs.commit_state(ctx, _commit_spec(ctx.node))
    return record if record is not None and record.get('state') == 'committed' else None


def _validate_foreign_design_aggregate_envelope(
        target: Path, *, run_id: str, sid: str,
) -> dict[str, Any]:
    """跨 run 扫描只验不依赖当前 profile/ledger 的聚合提交外壳。"""
    record = _read_object(target, label='Stage4 foreign design aggregate')
    if (set(record) != _DESIGN_AGGREGATE_FIELDS
            or record.get('commit_sha256') != _design_aggregate_hash(record)
            or record.get('version') != _DESIGN_AGGREGATE_VERSION
            or record.get('kind') != _DESIGN_AGGREGATE_KIND
            or record.get('run_id') != check_run_id(run_id)
            or record.get('sid') != sid
            or record.get('node') != 'stage4.design'
            or record.get('state') not in ('prepared', 'committed')):
        raise ArtifactUnreadable(
            f'Stage4 foreign design aggregate {target} 身份/哈希不闭合')
    return record


def refuse_foreign_commit_reservations(ctx: Any) -> None:
    """同书任一 foreign prepared 必须先补完，不能跨节点覆盖正式路径。"""
    _require_ownership(ctx)
    root = Path(ctx.novel_dir) / paths.STAGE4_RUNS_DIRNAME
    current = {_commit_spec(ctx.node).commit_path(ctx).resolve()}
    if ctx.node == 'stage4.design':
        current.add(_design_aggregate_commit_path(ctx).resolve())
    if not root.is_dir():
        return
    for target in root.glob(f'*/*/{agent_runs.COMMIT_FILE}'):
        if target.resolve() in current:
            continue
        run_id = target.parent.parent.name
        node = 'stage4.' + target.parent.name
        if node not in RECOVERY_POLICIES:
            raise ProvenanceRefused(
                f'Stage4 foreign commit 目录不是 design/plan：{target}')
        try:
            with process_append_lock(target):
                record = agent_runs.validate_foreign_commit_envelope(
                    target, label='Stage4 foreign commit', run_id=run_id,
                    sid=ctx.sid, node=node)
        except OSError as exc:
            raise ProvenanceRefused(
                f'Stage4 foreign commit journal {target} 不可验证：{exc}') from exc
        if record['state'] == 'prepared':
            raise ProvenanceRefused(
                f'Stage4 run={run_id}/{node} 仍持有 prepared 正式写盘尾窗；'
                '必须先用原 run 补完，不能由新 design/plan 覆盖')
    for target in root.glob(f'*/design/{DESIGN_AGGREGATE_COMMIT_FILE}'):
        if target.resolve() in current:
            continue
        run_id = target.parent.parent.name
        try:
            with process_append_lock(target):
                record = _validate_foreign_design_aggregate_envelope(
                    target, run_id=run_id, sid=ctx.sid)
        except OSError as exc:
            raise ProvenanceRefused(
                f'Stage4 foreign design aggregate {target} 不可验证：{exc}') from exc
        if record['state'] == 'prepared':
            raise ProvenanceRefused(
                f'Stage4 run={run_id}/stage4.design 仍持有 prepared '
                'aggregate 写盘尾窗；必须先用原 run 补完')


def _design_adoption_row(
        ctx: Any, unit: DesignUnitRequest,
) -> dict[str, Any]:
    restored = restore_design_unit_checkpoint(ctx, unit)
    if restored is None:
        raise ProvenanceRefused(
            f'{design_unit_purpose(unit.chapter)}: aggregate commit 前缺 '
            'accepted checkpoint')
    attempt_id = str(restored.acceptance['attempt_id'])
    identity = ctx.ledger.request_identity_for_attempt(attempt_id)
    if (set(identity) != _LEDGER_IDENTITY_FIELDS
            or identity.get('attempt_id') != attempt_id
            or identity.get('request_fingerprint')
            != restored.acceptance.get('request_fingerprint')
            or identity.get('model') != restored.acceptance.get('model')
            or identity.get('response_sha256')
            != restored.acceptance.get('ledger_response_sha256')):
        raise ProvenanceRefused(
            f'{design_unit_purpose(unit.chapter)}: checkpoint/ledger 请求身份不闭合')
    run_path = paths.stage4_design_unit_run_path(
        ctx.novel_dir, ctx.run_id, unit.chapter) / agent_runs.RUN_FILE
    checkpoint_path = _checkpoint_revision_path(
        ctx, unit.chapter, restored.revision)
    try:
        run_sha = agent_runs.canonical_sha256(run_path.read_bytes())
        checkpoint_file_sha = agent_runs.canonical_sha256(
            checkpoint_path.read_bytes())
    except OSError as exc:
        raise ProvenanceRefused(
            f'{design_unit_purpose(unit.chapter)}: 读不了已验证 run/checkpoint：'
            f'{exc}') from exc
    return {
        'unit_key': design_unit_key(unit.chapter),
        'purpose': design_unit_purpose(unit.chapter),
        'chapter': unit.chapter,
        'attempt_id': attempt_id,
        'run_sha256': run_sha,
        'checkpoint_revision': restored.revision,
        'checkpoint_sha256': restored.checkpoint_sha256,
        'checkpoint_file_sha256': checkpoint_file_sha,
        'provider_session_id': restored.acceptance['provider_session_id'],
        'request_identity': identity,
    }


def prepare_design_aggregate_commit(
        ctx: Any, *, units: Sequence[DesignUnitRequest],
        primary_payload: Mapping[str, Any],
        finalization_payload: Mapping[str, Any],
        touched_paths: Sequence[str] = (STAGE4_BLIND_CANDIDATE,),
) -> dict[str, Any]:
    """冻结 strategy + ch1..N 的最新 accepted revisions 与整书候选。

    这一步必须发生在正式 artifact 写盘前；journal 内封存候选
    payload，即使进程死在 prepare→formal 尾窗，重启也能零 wire 收尾。
    """
    _require_ownership(ctx)
    if ctx.node != 'stage4.design':
        raise ProvenanceRefused('design aggregate commit 只属于 stage4.design')
    normalized_units = _normalized_design_units(units)
    touched = sorted(str(value) for value in touched_paths)
    if touched != [STAGE4_BLIND_CANDIDATE]:
        raise ProvenanceRefused(
            f'design aggregate touched_paths 必须恰为 '
            f'[{STAGE4_BLIND_CANDIDATE!r}]')
    refuse_foreign_commit_reservations(ctx)
    legacy_root = paths.stage4_run_path(
        ctx.novel_dir, ctx.run_id, 'stage4.design')
    legacy_run = legacy_root / agent_runs.RUN_FILE
    legacy_commit = _commit_spec('stage4.design').commit_path(ctx)
    if legacy_run.is_file() or legacy_commit.is_file():
        raise ProvenanceRefused(
            f'{legacy_root} 已绑定旧 single-session design run/commit；'
            '同一 run 不能再建 aggregate 第二权威源')

    rows = [_design_adoption_row(ctx, unit) for unit in normalized_units]
    attempts = sorted(str(row['attempt_id']) for row in rows)
    sessions = [str(row['provider_session_id']) for row in rows]
    if len(attempts) != len(set(attempts)):
        raise ProvenanceRefused(
            'Stage4 design N+1 units 不能采纳同一 attempt')
    if len(sessions) != len(set(sessions)):
        raise ProvenanceRefused(
            'Stage4 design strategy/各章必须使用互异 provider session')
    primary = copy.deepcopy(dict(primary_payload))
    finalization = copy.deepcopy(dict(finalization_payload))
    # 在写任何 journal 前先证明两份快照可 canonical JSON 化。
    primary_sha = agent_runs.canonical_sha256(primary)
    finalization_sha = agent_runs.canonical_sha256(finalization)
    payload: dict[str, Any] = {
        'version': _DESIGN_AGGREGATE_VERSION,
        'kind': _DESIGN_AGGREGATE_KIND,
        'state': 'prepared', 'prepared_at': _now(), 'committed_at': None,
        'run_id': check_run_id(ctx.run_id), 'sid': ctx.sid,
        'node': 'stage4.design', 'code': ctx.code,
        'primary_artifact': STAGE4_BLIND_CANDIDATE,
        'primary_payload_sha256': primary_sha,
        'primary_payload_fingerprint': prov.projection_fingerprint(primary),
        'primary_payload': primary,
        'finalization_sha256': finalization_sha,
        'finalization_payload': finalization,
        'touched_paths': touched,
        'baselines': {
            STAGE4_BLIND_CANDIDATE:
                agent_runs.file_sha256(ctx, STAGE4_BLIND_CANDIDATE),
        },
        'attempts': attempts,
        'adoptions': rows,
        'landed_payload_sha256s': None,
        'post_file_sha256s': None,
    }
    payload['commit_sha256'] = _design_aggregate_hash(payload)
    target = _design_aggregate_commit_path(ctx)
    with _DESIGN_AGGREGATE_LOCK:
        with process_append_lock(target):
            if target.is_file():
                current = _validate_design_aggregate_state(ctx, target)
                ignored = {
                    'state', 'prepared_at', 'committed_at', 'commit_sha256',
                    'baselines', 'landed_payload_sha256s', 'post_file_sha256s',
                }
                left = {
                    key: value for key, value in current.items()
                    if key not in ignored
                }
                right = {
                    key: value for key, value in payload.items()
                    if key not in ignored
                }
                if left != right:
                    raise ProvenanceRefused(
                        f'{target} 已绑定另一份候选/unit adoption '
                        '集合；拒绝覆盖')
                return current
            _atomic_json(target, payload)
    return payload


def verify_design_aggregate_primary(
        record: Mapping[str, Any], primary_payload: Mapping[str, Any],
) -> bool:
    return record.get('primary_payload_sha256') == agent_runs.canonical_sha256(
        dict(primary_payload))


def verify_design_aggregate_finalization(
        record: Mapping[str, Any], finalization_payload: Mapping[str, Any],
) -> bool:
    return record.get('finalization_sha256') == agent_runs.canonical_sha256(
        dict(finalization_payload))


def restore_design_aggregate_payloads(
        ctx: Any, record: Mapping[str, Any] | None = None,
) -> tuple[dict[str, Any], dict[str, Any]]:
    """恢复 prepare 时封存的已验证整书候选与确定性终态。"""
    current = dict(record) if record is not None else design_aggregate_state(ctx)
    if current is None:
        raise ProvenanceRefused(
            f'run={ctx.run_id} 缺 design aggregate journal，不能恢复提交候选')
    validated = _validate_design_aggregate_state(
        ctx, _design_aggregate_commit_path(ctx))
    if current != validated:
        raise ProvenanceRefused('design aggregate journal 在读取候选期间漂移')
    return (
        copy.deepcopy(validated['primary_payload']),
        copy.deepcopy(validated['finalization_payload']),
    )


def restore_design_aggregate_adoptions(
        ctx: Any, record: Mapping[str, Any], *,
        units: Sequence[DesignUnitRequest],
) -> tuple[tuple[str, int | None, str], ...]:
    """恢复 N+1 attempts，并用当前请求逐项重验 run/checkpoint/ledger。"""
    _require_ownership(ctx)
    current = _validate_design_aggregate_state(
        ctx, _design_aggregate_commit_path(ctx))
    if dict(record) != current:
        raise ProvenanceRefused('传入的 design aggregate 不是当前盘上 journal')
    normalized_units = _normalized_design_units(units)
    by_key = {str(row['unit_key']): row for row in current['adoptions']}
    if set(by_key) != {design_unit_key(unit.chapter) for unit in normalized_units}:
        raise ProvenanceRefused('design aggregate 的 unit 集合与当前请求漂移')
    restored_rows: list[tuple[str, int | None, str]] = []
    for unit in normalized_units:
        restored = restore_design_unit_checkpoint(ctx, unit)
        if restored is None:
            raise ProvenanceRefused(
                f'{design_unit_purpose(unit.chapter)}: aggregate 所指 checkpoint 缺失')
        row = by_key[design_unit_key(unit.chapter)]
        attempt_id = str(row['attempt_id'])
        identity = row['request_identity']
        if (restored.acceptance.get('attempt_id') != attempt_id
                or restored.revision != row.get('checkpoint_revision')
                or restored.checkpoint_sha256 != row.get('checkpoint_sha256')
                or restored.acceptance.get('provider_session_id')
                != row.get('provider_session_id')
                or ctx.ledger.request_identity_for_attempt(attempt_id)
                != identity):
            raise ProvenanceRefused(
                f'{design_unit_purpose(unit.chapter)}: aggregate '
                'adoption 与当前 checkpoint/ledger 漂移')
        restored_rows.append((
            design_unit_purpose(unit.chapter), unit.chapter, attempt_id))
    return tuple(restored_rows)


def design_aggregate_target_state(
        ctx: Any, record: Mapping[str, Any], rel: str, *,
        expected_file_sha256: str | None = None,
        expected_absent: bool = False,
) -> str:
    """prepared 尾窗中只允许写前基线或本 run 已验证目标。"""
    _require_ownership(ctx)
    current = _validate_design_aggregate_state(
        ctx, _design_aggregate_commit_path(ctx))
    if dict(record) != current:
        raise ProvenanceRefused('传入的 design aggregate 不是当前盘上 journal')
    if rel != STAGE4_BLIND_CANDIDATE:
        raise ProvenanceRefused(f'{rel} 不属于 design aggregate touched_paths')
    if expected_absent and expected_file_sha256 is not None:
        raise ProvenanceRefused(f'{rel} 不能同时声明 expected SHA/absent')
    if not isinstance(expected_absent, bool):
        raise ProvenanceRefused(f'{rel} expected_absent 必须是 bool')
    if (expected_file_sha256 is not None
            and not agent_runs.is_sha256(expected_file_sha256)):
        raise ProvenanceRefused(f'{rel} expected file SHA 非法')
    current_sha = agent_runs.file_sha256(ctx, rel)
    if expected_absent and current_sha is None:
        return 'expected'
    if expected_file_sha256 is not None and current_sha == expected_file_sha256:
        return 'expected'
    if current_sha == current['baselines'][rel]:
        return 'baseline'
    raise ProvenanceRefused(
        f'{rel} 在 design aggregate prepared 尾窗中既不是基线，'
        '也不是本 run 已验证目标；拒绝覆盖第三方漂移')


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
        raise ProvenanceRefused(f'读不了 Stage4 adoption 审计账：{exc}') from exc
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


def _require_design_aggregate_adoptions(
        ctx: Any, record: Mapping[str, Any],
) -> None:
    expected = {
        (str(row['purpose']), str(row['attempt_id']), row.get('chapter'))
        for row in record['adoptions']
    }
    relevant = [
        row for row in _read_adoption_events(ctx)
        if (row.get('kind') == 'artifact_adoption'
            and row.get('run_id') == ctx.run_id
            and row.get('sid') == ctx.sid
            and row.get('node') == 'stage4.design'
            and row.get('artifact') == STAGE4_BLIND_CANDIDATE
            and row.get('artifact_fingerprint')
            == record['primary_payload_fingerprint'])
    ]
    actual = {
        (str(row.get('purpose')), str(row.get('attempt_id')), row.get('chapter'))
        for row in relevant
    }
    if len(relevant) != len(expected) or actual != expected:
        raise ProvenanceRefused(
            'Stage4 design committed 前必须恰有 strategy + ch1..N '
            f'各一条当前主产物 adoption；期望 {len(expected)}，'
            f'实得 {len(relevant)}')


def mark_design_aggregate_committed(
        ctx: Any, *, primary_payload: Mapping[str, Any],
        finalization_payload: Mapping[str, Any],
        landed_payloads: Mapping[str, Mapping[str, Any]],
) -> Path:
    """typed readback 与 N+1 adoptions 都闭合后原子提升为 committed。"""
    _require_ownership(ctx)
    target = _design_aggregate_commit_path(ctx)
    with _DESIGN_AGGREGATE_LOCK:
        with process_append_lock(target):
            if not target.is_file():
                raise ProvenanceRefused(f'{target} 缺 prepared aggregate journal')
            current = _validate_design_aggregate_state(ctx, target)
            if (not verify_design_aggregate_primary(current, primary_payload)
                    or not verify_design_aggregate_finalization(
                        current, finalization_payload)):
                raise ProvenanceRefused(
                    'design aggregate 正式 readback/finalization 与 prepared 漂移')
            if set(landed_payloads) != {STAGE4_BLIND_CANDIDATE}:
                raise ProvenanceRefused(
                    f'landed payloads 必须恰好覆盖 {STAGE4_BLIND_CANDIDATE}')
            landed_sha = {
                STAGE4_BLIND_CANDIDATE: agent_runs.canonical_sha256(
                    dict(landed_payloads[STAGE4_BLIND_CANDIDATE])),
            }
            post = {
                STAGE4_BLIND_CANDIDATE:
                    agent_runs.file_sha256(ctx, STAGE4_BLIND_CANDIDATE),
            }
            if post[STAGE4_BLIND_CANDIDATE] is None:
                raise ProvenanceRefused('committed 前 blind candidate 仍未落盘')
            _require_design_aggregate_adoptions(ctx, current)
            if current['state'] == 'committed':
                if (current.get('landed_payload_sha256s') != landed_sha
                        or current.get('post_file_sha256s') != post):
                    raise ProvenanceRefused(
                        'committed aggregate 与当前 typed/file readback 漂移')
                return target
            payload = {
                **current, 'state': 'committed', 'committed_at': _now(),
                'landed_payload_sha256s': landed_sha,
                'post_file_sha256s': post,
            }
            payload['commit_sha256'] = _design_aggregate_hash(payload)
            _atomic_json(target, payload)
    return target


def verify_design_aggregate_committed_delivery(
        ctx: Any, record: Mapping[str, Any], *,
        primary_payload: Mapping[str, Any],
        finalization_payload: Mapping[str, Any],
        landed_payloads: Mapping[str, Mapping[str, Any]],
) -> None:
    _require_ownership(ctx)
    current = _validate_design_aggregate_state(
        ctx, _design_aggregate_commit_path(ctx))
    if dict(record) != current or current['state'] != 'committed':
        raise ProvenanceRefused('传入的 journal 不是当前 committed aggregate')
    if (not verify_design_aggregate_primary(current, primary_payload)
            or not verify_design_aggregate_finalization(
                current, finalization_payload)):
        raise ProvenanceRefused('committed aggregate 主产物/finalization 漂移')
    if set(landed_payloads) != {STAGE4_BLIND_CANDIDATE}:
        raise ProvenanceRefused('committed aggregate typed readback 集合漂移')
    landed_sha = {
        STAGE4_BLIND_CANDIDATE: agent_runs.canonical_sha256(
            dict(landed_payloads[STAGE4_BLIND_CANDIDATE])),
    }
    post = {
        STAGE4_BLIND_CANDIDATE:
            agent_runs.file_sha256(ctx, STAGE4_BLIND_CANDIDATE),
    }
    if (landed_sha != current.get('landed_payload_sha256s')
            or post != current.get('post_file_sha256s')):
        raise ProvenanceRefused('committed aggregate 后正式 payload/file 被改写')
    _require_design_aggregate_adoptions(ctx, current)


def prepare_commit(
        ctx: Any, *, primary_artifact: str,
        primary_payload: Mapping[str, Any],
        finalization_payload: Mapping[str, Any],
        touched_paths: Sequence[str], attempt_id: str,
) -> dict[str, Any]:
    _require_ownership(ctx)
    spec = _commit_spec(ctx.node)
    if primary_artifact != spec.primary_artifact:
        raise ProvenanceRefused(
            f'{ctx.node}: primary artifact 必须是 {spec.primary_artifact}')
    refuse_foreign_commit_reservations(ctx)
    return agent_runs.prepare_commit(
        ctx, spec, primary_payload=primary_payload,
        finalization_payload=finalization_payload,
        touched_paths=touched_paths, attempt_id=attempt_id)


def target_state(
        ctx: Any, record: Mapping[str, Any], rel: str, *,
        expected_file_sha256: str | None = None,
        expected_absent: bool = False,
) -> str:
    _require_ownership(ctx)
    return agent_runs.target_state(
        ctx, _commit_spec(ctx.node), record, rel,
        expected_file_sha256=expected_file_sha256,
        expected_absent=expected_absent)


def verify_commit_primary(
        record: Mapping[str, Any], primary_payload: Mapping[str, Any],
) -> bool:
    return agent_runs.verify_commit_primary(record, primary_payload)


def verify_commit_finalization(
        record: Mapping[str, Any], finalization_payload: Mapping[str, Any],
) -> bool:
    return agent_runs.verify_commit_finalization(record, finalization_payload)


def restore_commit_adoption(
        ctx: Any, record: Mapping[str, Any],
) -> tuple[str, str]:
    _require_ownership(ctx)
    return agent_runs.restore_commit_adoption(ctx, _commit_spec(ctx.node), record)


def mark_committed(
        ctx: Any, *, primary_payload: Mapping[str, Any],
        finalization_payload: Mapping[str, Any],
        landed_payloads: Mapping[str, Mapping[str, Any]],
) -> Path:
    _require_ownership(ctx)
    return agent_runs.mark_committed(
        ctx, _commit_spec(ctx.node), primary_payload=primary_payload,
        finalization_payload=finalization_payload,
        landed_payloads=landed_payloads)


def verify_committed_delivery(
        ctx: Any, record: Mapping[str, Any], *,
        primary_payload: Mapping[str, Any],
        finalization_payload: Mapping[str, Any],
        landed_payloads: Mapping[str, Mapping[str, Any]],
) -> None:
    _require_ownership(ctx)
    agent_runs.verify_committed_delivery(
        ctx, _commit_spec(ctx.node), record,
        primary_payload=primary_payload,
        finalization_payload=finalization_payload,
        landed_payloads=landed_payloads)


__all__ = [
    'DESIGN_AGGREGATE_COMMIT_FILE', 'DESIGN_MAX_CONCURRENCY',
    'DESIGN_UNIT_RECOVERY_POLICY', 'DesignUnitRequest', 'RECOVERY_POLICIES',
    'commit_ownership', 'committed', 'design_aggregate_committed',
    'design_aggregate_prepared', 'design_aggregate_state',
    'design_aggregate_target_state', 'design_unit_key',
    'design_aggregate_usage', 'design_unit_purpose', 'design_unit_run_spec',
    'ensure_design_unit_run', 'ensure_run', 'mark_committed',
    'mark_design_aggregate_committed', 'prepare_commit',
    'prepare_design_aggregate_commit', 'prepared', 'recovery_policy',
    'refuse_foreign_commit_reservations', 'restore_checkpoint',
    'restore_commit_adoption', 'restore_design_aggregate_adoptions',
    'restore_design_aggregate_payloads', 'restore_design_unit_checkpoint',
    'target_state', 'verify_commit_finalization', 'verify_commit_primary',
    'verify_committed_delivery', 'verify_design_aggregate_committed_delivery',
    'verify_design_aggregate_finalization', 'verify_design_aggregate_primary',
    'write_checkpoint', 'write_design_unit_checkpoint',
]
