# -*- coding: utf-8 -*-
"""Stage5 逐章 executor 对通用 single-session agent run core 的薄适配。

``agent_runs`` 统一校验 resolved profile、prompt/schema、stdout、成本账、会话账、
append-only checkpoint 与 ``prepared -> committed`` journal。本模块只声明 Stage5
的业务边界：一章一个 logical/provider session；同章正式 bundle 独占提交锁；
foreign ``prepared`` 必须由原 run 补完；正式主产物之外的 INVALID、拒稿与 trim
状态只是同一事务的确定性伴生路径。

问题稿与修订拒稿不记 adoption，因此不伪装成 formal commit。它们的付费候选已由
append-only checkpoint 保存，进程重启后可以零 wire 重评并再次走原有代码写盘口。
"""
from __future__ import annotations

import os
import threading
from collections.abc import Callable, Mapping, Sequence
from contextlib import contextmanager
from pathlib import Path
from typing import Any, TypeVar

from pydantic import BaseModel

from contracts.artifacts import artifact_rel
from novel import paths
from novel.appendlog import process_append_lock
from novel.errors import ArtifactUnreadable, ProvenanceRefused
from novel.llm import agent_runs
from novel.llm.transport import ChatResponse


M = TypeVar('M', bound=BaseModel)

RECOVERY_POLICY = 'stage5-write-v1'
SESSION_ISOLATION = 'per-chapter'

_STATE_FIELDS = {
    'version', 'mode', 'outcome', 'input_sha256', 'candidate_sha256',
    'accepted_turn_index', 'prior_attempts', 'hard_rules', 'regressions',
}
_STATE_OUTCOMES = {
    'passed', 'hard', 'regressed', 'hard_and_regressed',
}
_OWNERSHIP_REGISTRY_LOCK = threading.Lock()
_OWNERSHIP_THREAD_LOCKS: dict[str, threading.RLock] = {}
_OWNERSHIP_LOCAL = threading.local()


def _chapter(chapter: int) -> int:
    if (not isinstance(chapter, int) or isinstance(chapter, bool)
            or chapter < 1):
        raise ProvenanceRefused(
            f'Stage5 agent chapter 必须是正整数，实得 {chapter!r}')
    return chapter


def purpose_of(chapter: int) -> str:
    """Stage5 逐章请求的唯一 purpose 形状。"""
    return f'stage5.write·ch{_chapter(chapter)}'


def _run_spec(chapter: int) -> agent_runs.SingleSessionRunSpec:
    ordinal = _chapter(chapter)
    return agent_runs.SingleSessionRunSpec(
        label=f'Stage5 chapter {ordinal}',
        node='stage5.write',
        session_owner=f'ch{ordinal}',
        purpose=purpose_of(ordinal),
        chapter=ordinal,
        phase='write',
        recovery_policy=RECOVERY_POLICY,
        session_isolation=SESSION_ISOLATION,
        run_path=lambda ctx: paths.stage5_chapter_run_path(
            ctx.novel_dir, ctx.run_id, ordinal),
        checkpoint_dir=lambda ctx: paths.stage5_chapter_checkpoint_dir(
            ctx.novel_dir, ctx.run_id, ordinal),
    )


def touched_paths(chapter: int) -> tuple[str, ...]:
    """正式 Chapter 写事务会建立、替换或退休的全部 live 路径。"""
    ordinal = _chapter(chapter)
    formal = artifact_rel('stage5.chapter', nn=ordinal)
    return (
        formal,
        paths.invalid_of(formal),
        artifact_rel('stage5.rejected_revision', nn=ordinal),
        artifact_rel('stage5.trim_audit', nn=ordinal),
        artifact_rel('stage5.trim_resolution', nn=ordinal),
    )


def _commit_spec(chapter: int) -> agent_runs.CommitBundleSpec:
    ordinal = _chapter(chapter)
    touched = touched_paths(ordinal)
    primary = artifact_rel('stage5.chapter', nn=ordinal)
    return agent_runs.CommitBundleSpec(
        run=_run_spec(ordinal),
        commit_path=lambda ctx: (
            paths.stage5_chapter_run_path(
                ctx.novel_dir, ctx.run_id, ordinal)
            / agent_runs.COMMIT_FILE),
        primary_artifact=primary,
        allowed_touched=(touched,),
        nonformal_paths=tuple(rel for rel in touched if rel != primary),
    )


def _validate_state_payload(
        state_payload: Mapping[str, Any], *, chapter: int,
        input_sha256: str, accepted_turn_index: int | None = None,
) -> dict[str, Any]:
    """验证 Stage5 业务 checkpoint；不重复 core 的 acceptance 校验。"""
    state = dict(state_payload)
    if set(state) != _STATE_FIELDS or state.get('version') != 1:
        raise ProvenanceRefused(
            f'Stage5 第 {chapter} 章 checkpoint state 字段漂移')
    mode = state.get('mode')
    outcome = state.get('outcome')
    turn = state.get('accepted_turn_index')
    prior = state.get('prior_attempts')
    hard_rules = state.get('hard_rules')
    regressions = state.get('regressions')
    if (mode not in ('write', 'revise')
            or outcome not in _STATE_OUTCOMES
            or state.get('input_sha256') != input_sha256
            or not agent_runs.is_sha256(input_sha256)
            or not agent_runs.is_sha256(state.get('candidate_sha256'))
            or not isinstance(turn, int) or isinstance(turn, bool) or turn < 1
            or (accepted_turn_index is not None and turn != accepted_turn_index)
            or not isinstance(prior, list)
            or len(prior) > turn - 1
            or not isinstance(hard_rules, list)
            or not isinstance(regressions, list)):
        raise ProvenanceRefused(
            f'Stage5 第 {chapter} 章 checkpoint state 身份/状态不闭合')
    normalized_prior: list[dict[str, Any]] = []
    for item in prior:
        if (not isinstance(item, dict) or set(item) != {'chars', 'text'}
                or not isinstance(item.get('chars'), int)
                or isinstance(item.get('chars'), bool) or item['chars'] < 0
                or not isinstance(item.get('text'), str) or not item['text'].strip()
                # 与 Stage5 ``chars_of`` 的唯一口径相同：含内部空白，只裁两端。
                or item['chars'] != len(item['text'].strip())):
            raise ProvenanceRefused(
                f'Stage5 第 {chapter} 章 checkpoint prior attempt 不合法')
        normalized_prior.append({'chars': item['chars'], 'text': item['text']})
    if (any(not isinstance(rule, str) or not rule for rule in hard_rules)
            or hard_rules != sorted(set(hard_rules))
            or any(not isinstance(item, str) or not item for item in regressions)):
        raise ProvenanceRefused(
            f'Stage5 第 {chapter} 章 checkpoint HARD/regression 投影不合法')
    expected_outcome = (
        'hard_and_regressed' if hard_rules and regressions
        else 'hard' if hard_rules
        else 'regressed' if regressions
        else 'passed')
    if outcome != expected_outcome:
        raise ProvenanceRefused(
            f'Stage5 第 {chapter} 章 checkpoint outcome 与判定投影矛盾')
    return {
        **state,
        'prior_attempts': normalized_prior,
        'hard_rules': list(hard_rules),
        'regressions': list(regressions),
    }


def ensure_run(
        ctx: Any, *, chapter: int, input_sha256: str,
        system: str, user: str, response_model: type[BaseModel],
        max_tokens: int,
) -> dict[str, Any]:
    return agent_runs.ensure_run(
        ctx, _run_spec(chapter), input_sha256=input_sha256,
        system=system, user=user, response_model=response_model,
        max_tokens=max_tokens)


def write_checkpoint(
        ctx: Any, *, chapter: int, input_sha256: str,
        system: str, user: str, response_model: type[BaseModel],
        max_tokens: int, response_payload: Mapping[str, Any],
        acceptance: Mapping[str, Any], state_payload: Mapping[str, Any],
) -> Path:
    turn = acceptance.get('accepted_turn_index')
    if not isinstance(turn, int) or isinstance(turn, bool) or turn < 1:
        raise ProvenanceRefused(
            f'Stage5 第 {chapter} 章 acceptance 缺 accepted turn')
    state = _validate_state_payload(
        state_payload, chapter=_chapter(chapter), input_sha256=input_sha256,
        accepted_turn_index=turn)
    return agent_runs.write_checkpoint(
        ctx, _run_spec(chapter), input_sha256=input_sha256,
        system=system, user=user, response_model=response_model,
        max_tokens=max_tokens, response_payload=response_payload,
        acceptance=acceptance, revision_key=f'turn-{turn:04d}',
        state_payload=state)


def restore_checkpoint(
        ctx: Any, *, chapter: int, input_sha256: str,
        system: str, user: str, response_model: type[M], max_tokens: int,
        validator: Callable[[M, ChatResponse], str | None] | None = None,
) -> agent_runs.RestoredCheckpoint[M] | None:
    restored = agent_runs.restore_checkpoint(
        ctx, _run_spec(chapter), input_sha256=input_sha256,
        system=system, user=user, response_model=response_model,
        max_tokens=max_tokens, validator=validator)
    if restored is None:
        return None
    state = _validate_state_payload(
        restored.state_payload, chapter=_chapter(chapter),
        input_sha256=input_sha256,
        accepted_turn_index=restored.acceptance.get('accepted_turn_index'))
    return agent_runs.RestoredCheckpoint(
        value=restored.value,
        acceptance=restored.acceptance,
        state_payload=state,
        revision=restored.revision,
        revision_key=restored.revision_key,
        checkpoint_sha256=restored.checkpoint_sha256,
    )


def _ownership_thread_lock(path: Path) -> threading.RLock:
    key = os.path.normcase(str(path.resolve()))
    with _OWNERSHIP_REGISTRY_LOCK:
        lock = _OWNERSHIP_THREAD_LOCKS.get(key)
        if lock is None:
            lock = _OWNERSHIP_THREAD_LOCKS[key] = threading.RLock()
        return lock


def _ownership_key(novel_dir: Path, chapter: int) -> str:
    return os.path.normcase(str(
        paths.stage5_chapter_commit_lock_path(novel_dir, chapter).resolve()))


@contextmanager
def commit_ownership(novel_dir: Path, chapter: int):
    """同一本书同一章的正式写盘/恢复共用线程与进程锁；兄弟章互不阻塞。"""
    ordinal = _chapter(chapter)
    target = paths.stage5_chapter_commit_lock_path(novel_dir, ordinal)
    key = _ownership_key(Path(novel_dir), ordinal)
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


def _require_ownership(ctx: Any, chapter: int) -> None:
    held = getattr(_OWNERSHIP_LOCAL, 'depths', {})
    if held.get(_ownership_key(Path(ctx.novel_dir), chapter), 0) < 1:
        raise ProvenanceRefused(
            f'Stage5 第 {chapter} 章 commit 必须在逐章 commit_ownership 临界区内完成')


def prepared(ctx: Any, chapter: int) -> dict[str, Any] | None:
    record = agent_runs.commit_state(ctx, _commit_spec(chapter))
    return record if record is not None and record.get('state') == 'prepared' else None


def committed(ctx: Any, chapter: int) -> dict[str, Any] | None:
    record = agent_runs.commit_state(ctx, _commit_spec(chapter))
    return record if record is not None and record.get('state') == 'committed' else None


def refuse_foreign_commit_reservations(ctx: Any, chapter: int) -> None:
    """同章 foreign prepared 必须由原 run 补完；兄弟章与已完成旧轮不阻断。"""
    ordinal = _chapter(chapter)
    _require_ownership(ctx, ordinal)
    root = Path(ctx.novel_dir) / paths.STAGE5_RUNS_DIRNAME
    current = _commit_spec(ordinal).commit_path(ctx).resolve()
    if not root.is_dir():
        return
    dirname = f'ch{ordinal:04d}'
    for target in root.glob(f'*/{dirname}/{agent_runs.COMMIT_FILE}'):
        if target.resolve() == current:
            continue
        if target.parent.name != dirname:
            raise ProvenanceRefused(
                f'Stage5 foreign commit 章节目录不闭合：{target}')
        run_id = target.parent.parent.name
        try:
            with process_append_lock(target):
                record = agent_runs.validate_foreign_commit_envelope(
                    target, label='Stage5 foreign commit', run_id=run_id,
                    sid=ctx.sid, node='stage5.write')
        except OSError as exc:
            raise ProvenanceRefused(
                f'Stage5 foreign commit journal {target} 不可验证：{exc}') from exc
        if record['state'] == 'prepared':
            raise ProvenanceRefused(
                f'Stage5 第 {ordinal} 章 run={run_id} 仍持有 prepared 正式写盘尾窗；'
                '必须先用原 run 补完，不能由新 run 覆盖')


def prepare_commit(
        ctx: Any, *, chapter: int, primary_payload: Mapping[str, Any],
        finalization_payload: Mapping[str, Any], attempt_id: str,
) -> dict[str, Any]:
    ordinal = _chapter(chapter)
    _require_ownership(ctx, ordinal)
    refuse_foreign_commit_reservations(ctx, ordinal)
    return agent_runs.prepare_commit(
        ctx, _commit_spec(ordinal), primary_payload=primary_payload,
        finalization_payload=finalization_payload,
        touched_paths=touched_paths(ordinal), attempt_id=attempt_id)


def target_state(
        ctx: Any, chapter: int, record: Mapping[str, Any], rel: str, *,
        expected_file_sha256: str | None = None,
        expected_absent: bool = False,
) -> str:
    ordinal = _chapter(chapter)
    _require_ownership(ctx, ordinal)
    return agent_runs.target_state(
        ctx, _commit_spec(ordinal), record, rel,
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
        ctx: Any, chapter: int, record: Mapping[str, Any],
) -> tuple[str, str]:
    ordinal = _chapter(chapter)
    _require_ownership(ctx, ordinal)
    return agent_runs.restore_commit_adoption(
        ctx, _commit_spec(ordinal), record)


def mark_committed(
        ctx: Any, *, chapter: int, primary_payload: Mapping[str, Any],
        finalization_payload: Mapping[str, Any],
) -> Path:
    ordinal = _chapter(chapter)
    _require_ownership(ctx, ordinal)
    primary = artifact_rel('stage5.chapter', nn=ordinal)
    return agent_runs.mark_committed(
        ctx, _commit_spec(ordinal), primary_payload=primary_payload,
        finalization_payload=finalization_payload,
        landed_payloads={primary: dict(primary_payload)})


def verify_committed_delivery(
        ctx: Any, chapter: int, record: Mapping[str, Any], *,
        primary_payload: Mapping[str, Any],
        finalization_payload: Mapping[str, Any],
) -> None:
    ordinal = _chapter(chapter)
    _require_ownership(ctx, ordinal)
    primary = artifact_rel('stage5.chapter', nn=ordinal)
    agent_runs.verify_committed_delivery(
        ctx, _commit_spec(ordinal), record,
        primary_payload=primary_payload,
        finalization_payload=finalization_payload,
        landed_payloads={primary: dict(primary_payload)})


__all__ = [
    'RECOVERY_POLICY', 'SESSION_ISOLATION', 'commit_ownership', 'committed',
    'ensure_run', 'file_sha256', 'mark_committed', 'prepare_commit', 'prepared',
    'purpose_of', 'refuse_foreign_commit_reservations', 'restore_checkpoint',
    'restore_commit_adoption', 'target_state', 'touched_paths',
    'verify_commit_finalization', 'verify_commit_primary',
    'verify_committed_delivery', 'write_checkpoint',
]
