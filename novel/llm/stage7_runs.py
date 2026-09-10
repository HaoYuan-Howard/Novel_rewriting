# -*- coding: utf-8 -*-
"""Stage7 rename 对通用 single-session agent run core 的薄适配。

本模块只声明 ``stage7.rename`` 特有的 purpose、append-only checkpoint、动态
成书路径与正式提交所有权。acceptance/stdout/ledger/session-audit 的逐字段闭环
全部复用 :mod:`novel.llm.agent_runs`，这里不复制第二份协议。
"""
from __future__ import annotations

from novel import runtime_layout
import os
import threading
from collections.abc import Callable, Mapping, Sequence
from contextlib import contextmanager
from pathlib import Path
from typing import Any, TypeVar

from pydantic import BaseModel

from contracts.artifacts import STAGE7_NAME_MAP
from novel import paths
from novel.appendlog import process_append_lock
from novel.errors import ProvenanceRefused
from novel.llm import agent_runs
from novel.llm.transport import ChatResponse


M = TypeVar('M', bound=BaseModel)

RECOVERY_POLICY = 'stage7-rename-v1'
_INVALID = paths.invalid_of(STAGE7_NAME_MAP)
_OWNERSHIP_REGISTRY_LOCK = threading.Lock()
_OWNERSHIP_THREAD_LOCKS: dict[str, threading.RLock] = {}
_OWNERSHIP_LOCAL = threading.local()


def _run_spec() -> agent_runs.SingleSessionRunSpec:
    return agent_runs.SingleSessionRunSpec(
        label='Stage7 rename',
        node='stage7.rename',
        session_owner='stage7.rename',
        purpose='stage7.rename',
        chapter=None,
        phase='rename',
        recovery_policy=RECOVERY_POLICY,
        session_isolation='per-node',
        run_path=lambda ctx: paths.stage7_rename_run_path(
            ctx.novel_dir, ctx.run_id),
        checkpoint_dir=lambda ctx: paths.stage7_rename_checkpoint_dir(
            ctx.novel_dir, ctx.run_id),
    )


def _book_txt_rel(ctx: Any) -> str:
    """动态交付路径只从路径层权威函数现算，不在 adapter 手拼书名。"""
    return paths.book_txt_rel(ctx.novel_dir)


def _commit_spec(ctx: Any) -> agent_runs.CommitBundleSpec:
    book_txt = _book_txt_rel(ctx)
    return agent_runs.CommitBundleSpec(
        run=_run_spec(),
        commit_path=lambda current: (
            paths.stage7_rename_run_path(
                current.novel_dir, current.run_id)
            / agent_runs.COMMIT_FILE),
        primary_artifact=STAGE7_NAME_MAP,
        # 正式改名会同时使旧问题稿与旧成书退出 live 路径；即便二者写前
        # 已缺失，也必须进入 baseline-bound journal，不能留无所有权空窗。
        allowed_touched=((STAGE7_NAME_MAP, _INVALID, book_txt),),
        nonformal_paths=(_INVALID, book_txt),
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
        paths.stage7_rename_commit_lock_path(novel_dir).resolve()))


@contextmanager
def commit_ownership(novel_dir: Path):
    """同一本书所有 rename run 的正式尾窗共用线程/进程锁。"""
    target = paths.stage7_rename_commit_lock_path(novel_dir)
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
            'Stage7 rename commit 必须在 commit_ownership(novel_dir) 临界区内完成 '
            'prepare、正式写盘、readback、adoption 与 committed')


def prepared(ctx: Any) -> dict[str, Any] | None:
    record = agent_runs.commit_state(ctx, _commit_spec(ctx))
    return record if record is not None and record.get('state') == 'prepared' else None


def committed(ctx: Any) -> dict[str, Any] | None:
    record = agent_runs.commit_state(ctx, _commit_spec(ctx))
    return record if record is not None and record.get('state') == 'committed' else None


def refuse_foreign_commit_reservations(ctx: Any) -> None:
    """另一 rename run 的 prepared 尾窗必须先由原 run 补完。"""
    _require_ownership(ctx)
    inspect_foreign_commit_reservations(ctx)


def inspect_foreign_commit_reservations(ctx: Any) -> None:
    """只读核对当前 reservation 快照；实际提交必须在书锁内通过同一校验。

    journal 原子替换且自带摘要，不为读取创建 .lock；扫描期间状态可能继续推进，
    因此此结果只供恢复投影，不能代替写入临界区的 refuse 守卫。
    """
    current = _commit_spec(ctx).commit_path(ctx).resolve()
    found: dict[str, Path] = {}
    for root in runtime_layout.stage_run_roots(ctx.novel_dir, 'stage7'):
        if root.is_dir():
            for target in root.glob(f'*/rename/{agent_runs.COMMIT_FILE}'):
                found.setdefault(target.parent.parent.name, target)
    for run_id, target in sorted(found.items()):
        if run_id == ctx.run_id or target.resolve() == current:
            continue
        try:
            record = agent_runs.validate_foreign_commit_envelope(
                target, label='Stage7 rename foreign commit',
                run_id=run_id, sid=ctx.sid, node='stage7.rename')
        except OSError as exc:
            raise ProvenanceRefused(
                f'Stage7 foreign commit journal {target} 不可验证：{exc}') from exc
        if record['state'] == 'prepared':
            raise ProvenanceRefused(
                f'Stage7 rename run={run_id} 仍持有 prepared 正式写盘尾窗；'
                '必须先用原 run 补完，不能由新 run 覆盖')


def prepare_commit(
        ctx: Any, *, primary_payload: Mapping[str, Any],
        finalization_payload: Mapping[str, Any],
        touched_paths: Sequence[str], attempt_id: str,
) -> dict[str, Any]:
    _require_ownership(ctx)
    refuse_foreign_commit_reservations(ctx)
    return agent_runs.prepare_commit(
        ctx, _commit_spec(ctx), primary_payload=primary_payload,
        finalization_payload=finalization_payload,
        touched_paths=touched_paths, attempt_id=attempt_id)


def target_state(
        ctx: Any, record: Mapping[str, Any], rel: str, *,
        expected_file_sha256: str | None = None,
        expected_absent: bool = False,
) -> str:
    _require_ownership(ctx)
    return agent_runs.target_state(
        ctx, _commit_spec(ctx), record, rel,
        expected_file_sha256=expected_file_sha256,
        expected_absent=expected_absent)


def file_sha256(ctx: Any, rel: str) -> str | None:
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
    return agent_runs.restore_commit_adoption(ctx, _commit_spec(ctx), record)


def mark_committed(
        ctx: Any, *, primary_payload: Mapping[str, Any],
        finalization_payload: Mapping[str, Any],
        landed_payloads: Mapping[str, Mapping[str, Any]],
) -> Path:
    _require_ownership(ctx)
    return agent_runs.mark_committed(
        ctx, _commit_spec(ctx), primary_payload=primary_payload,
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
        ctx, _commit_spec(ctx), record,
        primary_payload=primary_payload,
        finalization_payload=finalization_payload,
        landed_payloads=landed_payloads)


__all__ = [
    'RECOVERY_POLICY', 'commit_ownership', 'committed', 'ensure_run',
    'inspect_foreign_commit_reservations',
    'file_sha256', 'mark_committed', 'prepare_commit', 'prepared',
    'refuse_foreign_commit_reservations', 'restore_checkpoint',
    'restore_commit_adoption', 'target_state', 'verify_commit_finalization',
    'verify_commit_primary', 'verify_committed_delivery', 'write_checkpoint',
]
