# -*- coding: utf-8 -*-
"""Stage2 Global 对通用 single-session checkpoint/commit core 的薄适配。

这里仅冻结 ``stage2.global`` 的恢复策略、路径集合与跨 run 正式提交所有权。
stdout/acceptance/ledger/session-audit 的逐字段闭环全部复用
:mod:`novel.llm.agent_runs`，正式 artifact 仍只能由 Stage2 producer 写入。
"""
from __future__ import annotations

import os
import threading
from collections.abc import Callable, Mapping, Sequence
from contextlib import contextmanager
from pathlib import Path
from typing import Any, TypeVar

from pydantic import BaseModel

from contracts.artifacts import (
    STAGE2_BLUEPRINT_CHARACTER_PROJECTION,
    STAGE2_GLOBAL_GROUNDING,
    STAGE2_GLOBAL_REVIEW,
)
from novel import paths
from novel.appendlog import process_append_lock
from novel.errors import ProvenanceRefused
from novel.llm import agent_runs
from novel.llm.transport import ChatResponse


M = TypeVar('M', bound=BaseModel)
RECOVERY_POLICY = 'stage2-global-v1'
_INVALID = paths.invalid_of(STAGE2_GLOBAL_GROUNDING)
_OWNERSHIP_REGISTRY_LOCK = threading.Lock()
_OWNERSHIP_THREAD_LOCKS: dict[str, threading.RLock] = {}
_OWNERSHIP_LOCAL = threading.local()


def _run_spec() -> agent_runs.SingleSessionRunSpec:
    return agent_runs.SingleSessionRunSpec(
        label='Stage2 Global', node='stage2.global',
        session_owner='stage2.global', purpose='stage2.global',
        chapter=None, phase='global', recovery_policy=RECOVERY_POLICY,
        session_isolation='per-node',
        run_path=lambda ctx: paths.stage2_global_run_path(
            ctx.novel_dir, ctx.run_id),
        checkpoint_dir=lambda ctx: paths.stage2_global_checkpoint_dir(
            ctx.novel_dir, ctx.run_id),
        legacy_run_path=lambda ctx: paths.legacy_stage2_global_run_path(
            ctx.novel_dir, ctx.run_id),
        legacy_checkpoint_dir=lambda ctx: paths.legacy_stage2_global_checkpoint_dir(
            ctx.novel_dir, ctx.run_id),
    )


def _commit_spec() -> agent_runs.CommitBundleSpec:
    return agent_runs.CommitBundleSpec(
        run=_run_spec(),
        commit_path=lambda ctx: (
            paths.stage2_global_run_path(ctx.novel_dir, ctx.run_id)
            / agent_runs.COMMIT_FILE),
        primary_artifact=STAGE2_GLOBAL_GROUNDING,
        allowed_touched=((
            STAGE2_GLOBAL_GROUNDING,
            STAGE2_GLOBAL_REVIEW,
            STAGE2_BLUEPRINT_CHARACTER_PROJECTION,
            _INVALID,
        ),),
        nonformal_paths=(_INVALID,),
    )


def ensure_run(
        ctx: Any, *, input_sha256: str, system: str, user: str,
        response_model: type[BaseModel], max_tokens: int,
) -> dict[str, Any]:
    return agent_runs.ensure_run(
        ctx, _run_spec(), input_sha256=input_sha256,
        system=system, user=user, response_model=response_model,
        max_tokens=max_tokens)


def write_checkpoint(
        ctx: Any, *, input_sha256: str, system: str, user: str,
        response_model: type[BaseModel], max_tokens: int,
        response_payload: Mapping[str, Any], acceptance: Mapping[str, Any],
        revision_key: str, state_payload: Mapping[str, Any],
) -> Path:
    return agent_runs.write_checkpoint(
        ctx, _run_spec(), input_sha256=input_sha256,
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
        ctx, _run_spec(), input_sha256=input_sha256,
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
    return os.path.normcase(str(
        paths.stage2_global_commit_lock_path(novel_dir).resolve()))


@contextmanager
def commit_ownership(novel_dir: Path):
    """同一本书所有 Global run 的正式尾窗共用线程/进程锁。"""
    target = paths.stage2_global_commit_lock_path(novel_dir)
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
            'Stage2 Global commit 必须在 commit_ownership(novel_dir) 临界区内完成')


def prepared(ctx: Any) -> dict[str, Any] | None:
    record = agent_runs.commit_state(ctx, _commit_spec())
    return record if record is not None and record.get('state') == 'prepared' else None


def committed(ctx: Any) -> dict[str, Any] | None:
    record = agent_runs.commit_state(ctx, _commit_spec())
    return record if record is not None and record.get('state') == 'committed' else None


def _refuse_commit_reservations(
        ctx: Any, *, include_current: bool,
) -> None:
    _require_ownership(ctx)
    current = _commit_spec().commit_path(ctx).resolve()
    targets: dict[str, Path] = {}
    for root in paths.stage_run_read_roots(ctx.novel_dir, 'stage2'):
        if not root.is_dir():
            continue
        for candidate in root.glob(f'*/global/{agent_runs.COMMIT_FILE}'):
            targets.setdefault(candidate.parent.parent.name, candidate)
    for target in targets.values():
        is_current = target.resolve() == current
        if is_current and not include_current:
            continue
        run_id = target.parent.parent.name
        try:
            with process_append_lock(target):
                record = agent_runs.validate_foreign_commit_envelope(
                    target, label=(
                        'Stage2 Global current commit' if is_current else
                        'Stage2 Global foreign commit'),
                    run_id=run_id, sid=ctx.sid, node='stage2.global')
        except OSError as exc:
            raise ProvenanceRefused(
                f'Stage2 Global commit journal {target} 不可验证：{exc}') from exc
        if record['state'] == 'prepared':
            raise ProvenanceRefused(
                f'Stage2 Global run={run_id} 仍持有 prepared 正式写盘尾窗；'
                '必须先用原 run 补完，不能由旁路写口覆盖')


def refuse_foreign_commit_reservations(ctx: Any) -> None:
    """Producer 只排斥别的 run；当前 run 的 prepared 用于幂等补尾。"""
    _refuse_commit_reservations(ctx, include_current=False)


def refuse_current_agent_state_for_legacy(ctx: Any) -> None:
    """legacy LlmClient 不得接管同 run 的任何 agent durable state。

    ``run.json`` 在首个 wire 前就冻结身份，checkpoint 又可能早于 commit journal。
    只查 ``committed.json`` 会让 agent 崩在 accepted checkpoint 后，被同 run 的
    legacy direct 再发一次 wire。这里不解析/接管状态：精确 run 目录一旦存在，
    无论完整、空目录或损坏都关闭式交还原 agent 恢复。
    """
    _require_ownership(ctx)
    target = paths.stage2_global_run_path(ctx.novel_dir, ctx.run_id)
    if not target.exists():
        return
    raise ProvenanceRefused(
        'stage2.global legacy LlmClient 不能接管当前 run 的 agent '
        'run/checkpoint/commit 状态；请用原 agent run 幂等恢复')


@contextmanager
def mutation_ownership(ctx: Any, *, legacy_wire: bool = False):
    """Global 旁路写口共用的同书所有权与 prepared 排斥。

    Agent producer 必须允许当前 run 补自己的 prepared，因此继续直接使用
    :func:`commit_ownership` + :func:`refuse_foreign_commit_reservations`。
    人工/确定性写口不消费 commit journal，当前或 foreign 的 prepared 都不能踩；
    付费 legacy 写口还不能把同 run 的 checkpoint/committed 当作自己的状态。
    """
    with commit_ownership(Path(ctx.novel_dir)):
        _refuse_commit_reservations(ctx, include_current=True)
        if legacy_wire:
            refuse_current_agent_state_for_legacy(ctx)
        yield


def refuse_legacy_mutation_before_wire(ctx: Any) -> None:
    """付费 legacy Global 旁路在首个 provider wire 前复用正式写口守卫。"""
    with mutation_ownership(ctx, legacy_wire=True):
        return


def prepare_commit(
        ctx: Any, *, primary_payload: Mapping[str, Any],
        finalization_payload: Mapping[str, Any],
        touched_paths: Sequence[str], attempt_id: str,
) -> dict[str, Any]:
    _require_ownership(ctx)
    refuse_foreign_commit_reservations(ctx)
    return agent_runs.prepare_commit(
        ctx, _commit_spec(), primary_payload=primary_payload,
        finalization_payload=finalization_payload,
        touched_paths=touched_paths, attempt_id=attempt_id)


def target_state(
        ctx: Any, record: Mapping[str, Any], rel: str, *,
        expected_file_sha256: str | None = None,
        expected_absent: bool = False,
) -> str:
    _require_ownership(ctx)
    return agent_runs.target_state(
        ctx, _commit_spec(), record, rel,
        expected_file_sha256=expected_file_sha256,
        expected_absent=expected_absent)


def verify_commit_primary(record: Mapping[str, Any], payload: Mapping[str, Any]) -> bool:
    return agent_runs.verify_commit_primary(record, payload)


def verify_commit_finalization(
        record: Mapping[str, Any], payload: Mapping[str, Any],
) -> bool:
    return agent_runs.verify_commit_finalization(record, payload)


def restore_commit_adoption(
        ctx: Any, record: Mapping[str, Any],
) -> tuple[str, str]:
    _require_ownership(ctx)
    return agent_runs.restore_commit_adoption(ctx, _commit_spec(), record)


def mark_committed(
        ctx: Any, *, primary_payload: Mapping[str, Any],
        finalization_payload: Mapping[str, Any],
        landed_payloads: Mapping[str, Mapping[str, Any]],
) -> Path:
    _require_ownership(ctx)
    return agent_runs.mark_committed(
        ctx, _commit_spec(), primary_payload=primary_payload,
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
        ctx, _commit_spec(), record,
        primary_payload=primary_payload,
        finalization_payload=finalization_payload,
        landed_payloads=landed_payloads)


__all__ = [
    'RECOVERY_POLICY', 'commit_ownership', 'committed', 'ensure_run',
    'mark_committed', 'mutation_ownership', 'prepare_commit', 'prepared',
    'refuse_current_agent_state_for_legacy', 'refuse_foreign_commit_reservations',
    'refuse_legacy_mutation_before_wire', 'restore_checkpoint',
    'restore_commit_adoption', 'target_state', 'verify_commit_finalization',
    'verify_commit_primary', 'verify_committed_delivery', 'write_checkpoint',
]
