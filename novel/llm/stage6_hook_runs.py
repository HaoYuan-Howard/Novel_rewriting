# -*- coding: utf-8 -*-
"""Stage6 hook 对通用 single-session agent run core 的薄适配。

本模块只声明 hook 特有的路径、purpose、完整 replacement revision 与正式
``hook.json`` bundle；acceptance/stdout/ledger/session-audit 的逐字段校验全部
复用 ``novel.llm.run_journal``，不在这里复制第二份协议。
"""
from __future__ import annotations

import threading
from collections.abc import Callable, Mapping, Sequence
from contextlib import contextmanager
from pathlib import Path
from typing import Any, TypeVar

from pydantic import BaseModel

from contracts.artifacts import STAGE6_HOOK
from novel import paths
from novel.errors import ProvenanceRefused
from novel.llm import run_journal as agent_runs
from novel.appendlog import process_append_lock
from novel.llm.transport import ChatResponse
from novel import runtime_layout


M = TypeVar('M', bound=BaseModel)

RECOVERY_POLICY = 'stage6-hook-v1'
_INVALID = paths.invalid_of(STAGE6_HOOK)
_OWNERSHIP_LOCAL = threading.local()


def _run_spec() -> agent_runs.SingleSessionRunSpec:
    return agent_runs.SingleSessionRunSpec(
        label='Stage6 hook',
        node='stage6.hook',
        session_owner='stage6.hook',
        purpose='stage6.hook',
        chapter=None,
        phase='hook',
        recovery_policy=RECOVERY_POLICY,
        session_isolation='per-node',
        run_path=lambda ctx: paths.stage6_hook_run_path(
            ctx.novel_dir, ctx.run_id),
        checkpoint_dir=lambda ctx: paths.stage6_hook_checkpoint_dir(
            ctx.novel_dir, ctx.run_id),
        incompatible_session_nodes=('stage6.semantic',),
    )


def _commit_spec() -> agent_runs.CommitBundleSpec:
    return agent_runs.CommitBundleSpec(
        run=_run_spec(),
        commit_path=lambda ctx: (
            paths.stage6_hook_run_path(ctx.novel_dir, ctx.run_id)
            / agent_runs.COMMIT_FILE),
        primary_artifact=STAGE6_HOOK,
        allowed_touched=((STAGE6_HOOK,), (STAGE6_HOOK, _INVALID)),
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


def _history_files(ctx: Any, pattern: str) -> tuple[Path, ...]:
    """只枚举当前 service 历史；旧书内布局不得参与采用或提交。"""
    found: dict[str, Path] = {}
    for root in runtime_layout.stage_run_roots(ctx.novel_dir, 'stage6'):
        if not root.is_dir():
            continue
        for target in root.glob(pattern):
            found.setdefault(target.relative_to(root).as_posix(), target)
    return tuple(found[key] for key in sorted(found))


@contextmanager
def commit_ownership(novel_dir: Path):
    """Stage6 hook 正式写盘的线程/进程所有权锁。

    原与 semantic 判官账共锁（同书 lane 互斥）；判官退役（2026-09-15）后
    hook 是 stage6 唯一正式写 lane，独立持锁。
    """
    target = paths.stage6_hook_commit_lock_path(novel_dir)
    key = str(target.resolve()).casefold()
    depths = getattr(_OWNERSHIP_LOCAL, 'depths', None)
    if depths is None:
        depths = _OWNERSHIP_LOCAL.depths = {}
    with process_append_lock(target):
        depths[key] = depths.get(key, 0) + 1
        try:
            yield
        finally:
            depths[key] -= 1
            if not depths[key]:
                del depths[key]


def _require_ownership(ctx: Any) -> None:
    key = str(paths.stage6_hook_commit_lock_path(
        ctx.novel_dir).resolve()).casefold()
    if getattr(_OWNERSHIP_LOCAL, 'depths', {}).get(key, 0) < 1:
        raise ProvenanceRefused(
            'Stage6 hook commit 必须在共用 Stage6 commit_ownership 临界区内完成')


def prepared(ctx: Any) -> dict[str, Any] | None:
    record = agent_runs.commit_state(ctx, _commit_spec())
    return record if record is not None and record.get('state') == 'prepared' else None


def committed(ctx: Any) -> dict[str, Any] | None:
    record = agent_runs.commit_state(ctx, _commit_spec())
    return record if record is not None and record.get('state') == 'committed' else None


def refuse_foreign_commit_reservations(ctx: Any) -> None:
    """另一 hook run 的 prepared 尾窗必须先由原 run 补完。"""
    _require_ownership(ctx)
    current = _commit_spec().commit_path(ctx).resolve()
    for target in _history_files(
            ctx, f'*/hook/{agent_runs.COMMIT_FILE}'):
        run_id = target.parent.parent.name
        if run_id == ctx.run_id or target.resolve() == current:
            continue
        record = agent_runs.validate_foreign_commit_envelope(
            target, label='Stage6 hook foreign commit', run_id=run_id,
            sid=ctx.sid, node='stage6.hook')
        if record['state'] == 'prepared':
            raise ProvenanceRefused(
                f'Stage6 hook run={run_id} 仍持有 prepared 正式写盘尾窗；'
                '必须先用原 run 补完，不能由新 run 覆盖')


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


def file_sha256(ctx: Any, rel: str) -> str | None:
    """提交恢复只经通用 core 读取 raw file identity。"""
    return agent_runs.file_sha256(ctx, rel)


def verify_commit_primary(
        record: Mapping[str, Any], payload: Mapping[str, Any],
) -> bool:
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
    'RECOVERY_POLICY', 'commit_ownership', 'committed', 'ensure_run', 'file_sha256',
    'mark_committed', 'prepare_commit', 'prepared',
    'refuse_foreign_commit_reservations', 'restore_checkpoint',
    'restore_commit_adoption', 'target_state', 'verify_commit_finalization',
    'verify_commit_primary', 'verify_committed_delivery', 'write_checkpoint',
]
