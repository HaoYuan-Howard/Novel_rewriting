# -*- coding: utf-8 -*-
"""Stage4 design/plan 对通用 single-session agent run core 的薄适配。

通用的 profile/prompt/schema/stdout/ledger/session-audit/checkpoint/commit journal
闭环在 ``novel.llm.agent_runs``。本模块只声明 Stage4 特有语义：两个付费
node/purpose/provider session 互相隔离；typed infeasible 只有 binding；bound bundle
还含 plan/context；同书任一 foreign ``prepared`` 都阻止另一 run/node 写正式路径。
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
    STAGE4_BLIND_CANDIDATE, STAGE4_EVIDENCE_BINDING,
    STAGE4_PLAN_CHAPTERS, STAGE4_WRITER_CONTEXT,
)
from novel import paths
from novel.appendlog import process_append_lock
from novel.errors import ProvenanceRefused
from novel.llm import agent_runs
from novel.llm.transport import ChatResponse


M = TypeVar('M', bound=BaseModel)

RECOVERY_POLICIES = {
    'stage4.design': 'stage4-design-v1',
    'stage4.plan': 'stage4-plan-v1',
}

_PLAN_INVALID = paths.invalid_of(STAGE4_PLAN_CHAPTERS)
_OWNERSHIP_REGISTRY_LOCK = threading.Lock()
_OWNERSHIP_THREAD_LOCKS: dict[str, threading.RLock] = {}
_OWNERSHIP_LOCAL = threading.local()


def recovery_policy(node: str) -> str:
    """返回 Stage4 节点唯一恢复策略；节点名不是自由输入。"""
    try:
        return RECOVERY_POLICIES[node]
    except KeyError:
        raise ProvenanceRefused(
            f'Stage4 agent node 必须属于 {tuple(RECOVERY_POLICIES)}，实得 {node!r}') from None


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


def prepared(ctx: Any) -> dict[str, Any] | None:
    record = agent_runs.commit_state(ctx, _commit_spec(ctx.node))
    return record if record is not None and record.get('state') == 'prepared' else None


def committed(ctx: Any) -> dict[str, Any] | None:
    record = agent_runs.commit_state(ctx, _commit_spec(ctx.node))
    return record if record is not None and record.get('state') == 'committed' else None


def refuse_foreign_commit_reservations(ctx: Any) -> None:
    """同书任一 foreign prepared 必须先补完，不能跨节点覆盖正式路径。"""
    _require_ownership(ctx)
    root = Path(ctx.novel_dir) / paths.STAGE4_RUNS_DIRNAME
    current = _commit_spec(ctx.node).commit_path(ctx).resolve()
    if not root.is_dir():
        return
    for target in root.glob(f'*/*/{agent_runs.COMMIT_FILE}'):
        if target.resolve() == current:
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
    'RECOVERY_POLICIES', 'commit_ownership', 'committed', 'ensure_run',
    'mark_committed', 'prepare_commit', 'prepared', 'recovery_policy',
    'refuse_foreign_commit_reservations', 'restore_checkpoint',
    'restore_commit_adoption', 'target_state', 'verify_commit_finalization',
    'verify_commit_primary', 'verify_committed_delivery', 'write_checkpoint',
]
