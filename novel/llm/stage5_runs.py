# -*- coding: utf-8 -*-
"""Stage5 逐章 executor 对通用 single-session agent run core 的薄适配。

``agent_runs`` 统一校验 resolved profile、prompt/schema、stdout、成本账、会话账、
append-only checkpoint 与 ``prepared -> committed`` journal。本模块只声明 Stage5
的业务边界：一章一个 logical/provider session；所有 Stage5 writer 先占全书锁，
同章正式 bundle 再占逐章提交锁；
foreign ``prepared`` 默认必须由原 run 补完；只有显式新 run 在当前代码下完成
``prepared checkpoint recovery``，且 source raw+self sidecar、adoption audit、typed
canonical 与 committed replacement lineage 全部闭合时，旧 reservation 才算 superseded。
正式主产物之外的 INVALID、拒稿与 trim 状态只是同一事务的确定性伴生路径。

问题稿与修订拒稿不记 adoption，因此不伪装成 formal commit。它们的付费候选已由
append-only checkpoint 保存，进程重启后可以零 wire 重评并再次走原有代码写盘口。
"""
from __future__ import annotations

from novel import runtime_layout
import json
import os
import re
import stat
import threading
from collections.abc import Callable, Mapping, Sequence
from contextlib import ExitStack, contextmanager, nullcontext
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, TypeVar

from pydantic import BaseModel, ValidationError

from contracts.artifacts import artifact_rel
from contracts.schema.stage5 import (
    Chapter, PreparedRecoveryAction, PreparedRecoveryChapter,
    PreparedRecoveryJournal, PreparedRecoveryManifest,
    ReviewerRewriteDisposition, ReviewerRewriteExecutionJournal,
    ReviewerRewriteRecoveryJournal, ReviewerRewriteSuccessorJournal,
    prepared_recovery_workpack_subject,
    reviewer_rewrite_execution_journal_sha256,
    reviewer_rewrite_disposition_sha256,
    reviewer_rewrite_recovery_journal_sha256,
    reviewer_rewrite_successor_journal_sha256,
)
from novel import paths, prov
from novel.llm import session_audit as agent_session_audit
from novel.appendlog import process_append_lock
from novel.errors import ArtifactUnreadable, ProvenanceRefused
from novel.llm import run_journal as agent_runs, ledger as ledger_module
from novel.llm.transport import ChatResponse
from novel.run_identity import check_run_id


M = TypeVar('M', bound=BaseModel)

RECOVERY_POLICY = 'stage5-write-v2'
REVIEWER_REWRITE_RECOVERY_POLICY = 'stage5-reviewer-rewrite-v2'
SESSION_ISOLATION = 'per-chapter'
RECOVERY_MANIFEST_VERSION = 1

_STATE_FIELDS = {
    'version', 'mode', 'outcome', 'input_sha256', 'candidate_sha256',
    'accepted_turn_index', 'prior_attempts', 'hard_rules', 'regressions',
}
_REVIEWER_REWRITE_STATE_FIELDS = _STATE_FIELDS | {
    'reviewer_rewrite_request_sha256',
    'reviewer_rewrite_request_file_sha256',
}
_STATE_OUTCOMES = {
    'passed', 'hard', 'regressed', 'hard_and_regressed',
}
_OWNERSHIP_REGISTRY_LOCK = threading.Lock()
_OWNERSHIP_THREAD_LOCKS: dict[str, threading.RLock] = {}
_OWNERSHIP_LOCAL = threading.local()


def _history_files(ctx: Any, pattern: str) -> tuple[Path, ...]:
    """Stage5 service/legacy 只读合并，当前空间优先。"""
    found: dict[str, Path] = {}
    for root in runtime_layout.stage_run_roots(ctx.novel_dir, 'stage5'):
        if not root.is_dir():
            continue
        for target in root.glob(pattern):
            key = target.relative_to(root).as_posix()
            prior = found.get(key)
            if prior is not None:
                continue
            found[key] = target
    return tuple(found[key] for key in sorted(found))


@dataclass(frozen=True)
class HistoricalPrepared:
    """通过 source 自身 code/profile/session/ledger 与当前请求双重校验的 checkpoint。"""

    historical: agent_runs.RestoredHistoricalRun[Any]
    commit: dict[str, Any]


@dataclass(frozen=True)
class HistoricalReviewerRewrite:
    """精确 request 的历史 accepted checkpoint；commit 可尚未 prepare。"""

    historical: agent_runs.RestoredHistoricalRun[Any]
    commit: dict[str, Any] | None
    state_payload: dict[str, Any]


@dataclass(frozen=True)
class HistoricalReviewerRewriteTerminal:
    """不借当前 prompt/schema/validator 解释的历史 terminal 三账快照。"""

    run_record: dict[str, Any]
    run_sha256: str
    checkpoint: dict[str, Any]
    commit: dict[str, Any] | None


def _chapter(chapter: int) -> int:
    if (not isinstance(chapter, int) or isinstance(chapter, bool)
            or chapter < 1):
        raise ProvenanceRefused(
            f'Stage5 agent chapter 必须是正整数，实得 {chapter!r}')
    return chapter


def purpose_of(chapter: int, mode: str = 'write') -> str:
    """Stage5 逐章请求的唯一 purpose 形状。"""
    ordinal = _chapter(chapter)
    if mode == 'write' or mode == 'revise':
        # revise 沿用历史 purpose；两者仍由 checkpoint state.mode 分开。
        return f'stage5.write·ch{ordinal}'
    if mode == 'reviewer_rewrite':
        return f'stage5.reviewer_rewrite·ch{ordinal}'
    raise ProvenanceRefused(f'Stage5 未登记 writer mode：{mode!r}')


def recovery_policy_of(mode: str) -> str:
    if mode in ('write', 'revise'):
        return RECOVERY_POLICY
    if mode == 'reviewer_rewrite':
        return REVIEWER_REWRITE_RECOVERY_POLICY
    raise ProvenanceRefused(f'Stage5 未登记 writer mode：{mode!r}')


def _run_spec(
        chapter: int, mode: str = 'write',
) -> agent_runs.SingleSessionRunSpec:
    ordinal = _chapter(chapter)
    purpose = purpose_of(ordinal, mode)
    return agent_runs.SingleSessionRunSpec(
        label=f'Stage5 chapter {ordinal}',
        node='stage5.write',
        session_owner=f'ch{ordinal}',
        purpose=purpose,
        chapter=ordinal,
        phase='reviewer_rewrite' if mode == 'reviewer_rewrite' else 'write',
        recovery_policy=recovery_policy_of(mode),
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


def _commit_spec(
        chapter: int, mode: str = 'write',
) -> agent_runs.CommitBundleSpec:
    ordinal = _chapter(chapter)
    touched = touched_paths(ordinal)
    primary = artifact_rel('stage5.chapter', nn=ordinal)
    return agent_runs.CommitBundleSpec(
        run=_run_spec(ordinal, mode),
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
        mode: str = 'write', reviewer_rewrite_request_sha256: str | None = None,
        reviewer_rewrite_request_file_sha256: str | None = None,
) -> dict[str, Any]:
    """验证 Stage5 业务 checkpoint；不重复 core 的 acceptance 校验。"""
    state = dict(state_payload)
    expected_fields = (
        _REVIEWER_REWRITE_STATE_FIELDS
        if mode == 'reviewer_rewrite' else _STATE_FIELDS)
    if set(state) != expected_fields or state.get('version') != 2:
        raise ProvenanceRefused(
            f'Stage5 第 {chapter} 章 checkpoint state 字段漂移')
    expected_mode = mode
    outcome = state.get('outcome')
    turn = state.get('accepted_turn_index')
    prior = state.get('prior_attempts')
    hard_rules = state.get('hard_rules')
    regressions = state.get('regressions')
    recorded_mode = state.get('mode')
    if (recorded_mode not in ('write', 'revise', 'reviewer_rewrite')
            or recorded_mode != expected_mode
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
    if expected_mode == 'reviewer_rewrite':
        if (not agent_runs.is_sha256(reviewer_rewrite_request_sha256)
                or state.get('reviewer_rewrite_request_sha256')
                != reviewer_rewrite_request_sha256
                or not agent_runs.is_sha256(
                    reviewer_rewrite_request_file_sha256)
                or state.get('reviewer_rewrite_request_file_sha256')
                != reviewer_rewrite_request_file_sha256):
            raise ProvenanceRefused(
                f'Stage5 第 {chapter} 章 reviewer rewrite checkpoint request 身份不闭合')
    from contracts.schema.stage5 import RejectedAttempt
    normalized_prior: list[dict[str, Any]] = []
    for item in prior:
        try:
            candidate = RejectedAttempt.model_validate(item)
        except (ValueError, TypeError) as exc:
            raise ProvenanceRefused(
                f'Stage5 第 {chapter} 章 checkpoint prior attempt 不合法') from exc
        normalized_prior.append(candidate.model_dump(mode='json'))
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
        max_tokens: int, mode: str = 'write',
) -> dict[str, Any]:
    return agent_runs.ensure_run(
        ctx, _run_spec(chapter, mode), input_sha256=input_sha256,
        system=system, user=user, response_model=response_model,
        max_tokens=max_tokens)


def write_checkpoint(
        ctx: Any, *, chapter: int, input_sha256: str,
        system: str, user: str, response_model: type[BaseModel],
        max_tokens: int, response_payload: Mapping[str, Any],
        acceptance: Mapping[str, Any], state_payload: Mapping[str, Any],
        mode: str = 'write', reviewer_rewrite_request_sha256: str | None = None,
        reviewer_rewrite_request_file_sha256: str | None = None,
) -> Path:
    turn = acceptance.get('accepted_turn_index')
    if not isinstance(turn, int) or isinstance(turn, bool) or turn < 1:
        raise ProvenanceRefused(
            f'Stage5 第 {chapter} 章 acceptance 缺 accepted turn')
    state = _validate_state_payload(
        state_payload, chapter=_chapter(chapter), input_sha256=input_sha256,
        accepted_turn_index=turn, mode=mode,
        reviewer_rewrite_request_sha256=reviewer_rewrite_request_sha256,
        reviewer_rewrite_request_file_sha256=(
            reviewer_rewrite_request_file_sha256))
    return agent_runs.write_checkpoint(
        ctx, _run_spec(chapter, mode), input_sha256=input_sha256,
        system=system, user=user, response_model=response_model,
        max_tokens=max_tokens, response_payload=response_payload,
        acceptance=acceptance, revision_key=f'turn-{turn:04d}',
        state_payload=state)


def read_checkpoint_sha256(path: Path) -> str:
    """服务审计 IO 与 checkpoint 摘要规则仍由通用 run core 承担。"""
    return agent_runs.read_checkpoint_sha256(path)


def restore_checkpoint(
        ctx: Any, *, chapter: int, input_sha256: str,
        system: str, user: str, response_model: type[M], max_tokens: int,
        validator: Callable[[M, ChatResponse], str | None] | None = None,
        mode: str = 'write', reviewer_rewrite_request_sha256: str | None = None,
        reviewer_rewrite_request_file_sha256: str | None = None,
) -> agent_runs.RestoredCheckpoint[M] | None:
    restored = agent_runs.restore_checkpoint(
        ctx, _run_spec(chapter, mode), input_sha256=input_sha256,
        system=system, user=user, response_model=response_model,
        max_tokens=max_tokens, validator=validator)
    if restored is None:
        return None
    state = _validate_state_payload(
        restored.state_payload, chapter=_chapter(chapter),
        input_sha256=input_sha256,
        accepted_turn_index=restored.acceptance.get('accepted_turn_index'),
        mode=mode,
        reviewer_rewrite_request_sha256=reviewer_rewrite_request_sha256,
        reviewer_rewrite_request_file_sha256=(
            reviewer_rewrite_request_file_sha256))
    return agent_runs.RestoredCheckpoint(
        value=restored.value,
        acceptance=restored.acceptance,
        state_payload=state,
        revision=restored.revision,
        revision_key=restored.revision_key,
        checkpoint_sha256=restored.checkpoint_sha256,
    )


def restore_historical_prepared(
        ctx: Any, *, source_run_id: str, chapter: int,
        input_sha256: str, system: str, user: str,
        response_model: type[M], max_tokens: int,
        validator: Callable[[M, ChatResponse], str | None] | None = None,
) -> HistoricalPrepared:
    """用旧 run 自己的环境验 session/ledger，再用当前请求与业务 validator 重判。"""
    ordinal = _chapter(chapter)
    historical = agent_runs.restore_historical_run(
        ctx, _run_spec(ordinal), source_run_id=source_run_id,
        input_sha256=input_sha256, system=system, user=user,
        response_model=response_model, max_tokens=max_tokens,
        validator=validator,
    )
    state = _validate_state_payload(
        historical.checkpoint.state_payload,
        chapter=ordinal,
        input_sha256=input_sha256,
        accepted_turn_index=historical.checkpoint.acceptance.get(
            'accepted_turn_index'),
    )
    if (state['mode'] != 'write' or state['outcome'] != 'passed'
            or state['hard_rules'] or state['regressions']):
        raise ProvenanceRefused(
            f'Stage5 第 {ordinal} 章 prepared recovery 只接受已通过的 initial write checkpoint')
    commit = agent_runs.commit_state(
        historical.context, _commit_spec(ordinal))
    if commit is None or commit.get('state') != 'prepared':
        raise ProvenanceRefused(
            f'Stage5 第 {ordinal} 章 source run 不是 unresolved prepared')
    if (commit.get('attempt_id')
            != historical.checkpoint.acceptance.get('attempt_id')):
        raise ProvenanceRefused(
            f'Stage5 第 {ordinal} 章 source commit/checkpoint attempt 漂移')
    return HistoricalPrepared(historical=historical, commit=commit)


def restore_historical_reviewer_rewrite(
        ctx: Any, *, source_run_id: str, chapter: int,
        input_sha256: str, system: str, user: str,
        request_sha256: str, request_file_sha256: str,
        response_model: type[M], max_tokens: int,
        validator: Callable[[M, ChatResponse], str | None] | None = None,
) -> HistoricalReviewerRewrite:
    """复验 formal reviewer rewrite 的历史 checkpoint；source commit 可不存在。

    与旧 ``restore_historical_prepared`` 的人工 initial-write 面分开：这里的授权
    来自仍逐字相同的 canonical formal request，且只允许 0-wire 自动补尾。
    """
    ordinal = _chapter(chapter)
    if not agent_runs.is_sha256(request_sha256):
        raise ProvenanceRefused('reviewer rewrite recovery request SHA 不合法')
    if not agent_runs.is_sha256(request_file_sha256):
        raise ProvenanceRefused('reviewer rewrite recovery request file SHA 不合法')
    historical = agent_runs.restore_historical_run(
        ctx, _run_spec(ordinal, 'reviewer_rewrite'),
        source_run_id=source_run_id,
        input_sha256=input_sha256, system=system, user=user,
        response_model=response_model, max_tokens=max_tokens,
        validator=validator,
        ledger_source_path=ledger_module.ledger_path(Path(ctx.novel_dir).parent),
    )
    state = _validate_state_payload(
        historical.checkpoint.state_payload,
        chapter=ordinal,
        input_sha256=input_sha256,
        accepted_turn_index=historical.checkpoint.acceptance.get(
            'accepted_turn_index'),
        mode='reviewer_rewrite',
        reviewer_rewrite_request_sha256=request_sha256,
        reviewer_rewrite_request_file_sha256=request_file_sha256,
    )
    commit = agent_runs.commit_state(
        historical.context, _commit_spec(ordinal, 'reviewer_rewrite'))
    if commit is not None:
        if commit.get('attempt_id') != historical.checkpoint.acceptance.get('attempt_id'):
            raise ProvenanceRefused(
                f'Stage5 第 {ordinal} 章 reviewer rewrite source commit/checkpoint '
                'attempt 漂移')
        if commit.get('state') not in ('prepared', 'committed'):
            raise ProvenanceRefused(
                f'Stage5 第 {ordinal} 章 reviewer rewrite source commit 状态非法')
    return HistoricalReviewerRewrite(
        historical=historical, commit=commit, state_payload=state)


def source_prepared_envelopes(
        ctx: Any, source_run_id: str,
) -> dict[int, dict[str, Any]]:
    """枚举 source run 的 prepared commit 外壳；深验在逐章恢复时完成。"""
    from novel.run_identity import check_run_id

    source = check_run_id(source_run_id)
    if source == ctx.run_id:
        raise ProvenanceRefused('prepared recovery source run 必须不同于当前 target run')
    out: dict[int, dict[str, Any]] = {}
    targets = _history_files(ctx, f'{source}/ch*/{agent_runs.COMMIT_FILE}')
    if not targets:
        raise ProvenanceRefused(f'Stage5 source run={source} 不存在')
    for target in targets:
        child = target.parent
        suffix = child.name[2:] if child.name.startswith('ch') else ''
        if len(suffix) != 4 or not suffix.isdigit() or int(suffix) < 1:
            raise ArtifactUnreadable(
                f'Stage5 source run 含非 canonical 章节目录：{child}')
        ordinal = int(suffix)
        with process_append_lock(target):
            record = agent_runs.validate_foreign_commit_envelope(
                target, label='Stage5 source commit', run_id=source,
                sid=ctx.sid, node='stage5.write')
        if record['state'] == 'prepared':
            out[ordinal] = record
    if not out:
        raise ProvenanceRefused(
            f'Stage5 source run={source} 没有 unresolved prepared commit')
    return out


def _ownership_thread_lock(path: Path) -> threading.RLock:
    key = os.path.normcase(str(path.resolve()))
    with _OWNERSHIP_REGISTRY_LOCK:
        lock = _OWNERSHIP_THREAD_LOCKS.get(key)
        if lock is None:
            lock = _OWNERSHIP_THREAD_LOCKS[key] = threading.RLock()
        return lock


def _ownership_key(path: Path) -> str:
    return os.path.normcase(str(path.resolve()))


@contextmanager
def _reentrant_ownership(target: Path):
    """同一路径可重入的线程 + 进程锁；进程锁只由最外层获取一次。"""
    key = _ownership_key(target)
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


@contextmanager
def book_commit_ownership(novel_dir: Path):
    """Stage5 写者总锁；锁序恒为书 → 章（升序）→ append-only 账。"""
    target = paths.stage5_book_commit_lock_path(Path(novel_dir))
    with _reentrant_ownership(target):
        yield


@contextmanager
def commit_ownership(novel_dir: Path, chapter: int):
    """同书写者锁内再取逐章锁；兄弟章不再与整书快照/提交交错。"""
    ordinal = _chapter(chapter)
    target = paths.stage5_chapter_commit_lock_path(novel_dir, ordinal)
    with book_commit_ownership(Path(novel_dir)):
        with _reentrant_ownership(target):
            yield


def _reviewer_execution_lock_path(
        novel_dir: Path, request_sha256: str,
) -> Path:
    if not agent_runs.is_sha256(request_sha256):
        raise ProvenanceRefused('reviewer rewrite execution lock request SHA 非法')
    return (paths.service_stage_runs_dir(novel_dir, 'stage5')
            / '.reviewer-rewrite-execution' / request_sha256)


@contextmanager
def reviewer_rewrite_execution_ownership(
        novel_dir: Path, request_sha256: str,
):
    """同一 formal request 从 preclaim 到 terminal 全程唯一的跨进程 lease。"""
    target = _reviewer_execution_lock_path(
        Path(novel_dir), request_sha256)
    with _reentrant_ownership(target):
        yield


def _require_reviewer_execution_ownership(
        novel_dir: Path, request_sha256: str,
) -> None:
    held = getattr(_OWNERSHIP_LOCAL, 'depths', {})
    target = _reviewer_execution_lock_path(
        Path(novel_dir), request_sha256)
    if held.get(_ownership_key(target), 0) < 1:
        raise ProvenanceRefused(
            'reviewer rewrite claim/reclaim 必须持 request execution lease')


def _require_ownership(ctx: Any, chapter: int) -> None:
    held = getattr(_OWNERSHIP_LOCAL, 'depths', {})
    target = paths.stage5_chapter_commit_lock_path(ctx.novel_dir, chapter)
    if held.get(_ownership_key(target), 0) < 1:
        raise ProvenanceRefused(
            f'Stage5 第 {chapter} 章 commit 必须在逐章 commit_ownership 临界区内完成')


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec='seconds')


def _file_sha256(path: Path) -> str:
    try:
        return agent_runs.canonical_sha256(path.read_bytes())
    except OSError as exc:
        raise ProvenanceRefused(f'prepared recovery sidecar {path} 不可读：{exc}') from exc


def _typed_json(path: Path, model: type[M], *, label: str) -> M:
    try:
        value = json.loads(path.read_text(encoding='utf-8'))
        return model.model_validate(value)
    except FileNotFoundError:
        raise ProvenanceRefused(f'{label} {path} 不存在') from None
    except (OSError, UnicodeError, json.JSONDecodeError, ValidationError) as exc:
        raise ArtifactUnreadable(f'{label} {path} 不符合 typed 契约：{exc}') from exc


def _strict_json_object_bytes(raw: bytes, *, label: str) -> dict[str, Any]:
    """恢复权威 JSON object 的唯一严格 decoder。"""
    def reject_constant(value: str) -> None:
        raise ValueError(f'JSON 不接受 {value}')

    def reject_duplicate(pairs: list[tuple[str, object]]) -> dict[str, object]:
        value: dict[str, object] = {}
        for key, item in pairs:
            if key in value:
                raise ValueError(f'JSON object 含重复键 {key!r}')
            value[key] = item
        return value

    try:
        value = json.loads(
            raw.decode('utf-8'), parse_constant=reject_constant,
            object_pairs_hook=reject_duplicate)
    except (UnicodeError, ValueError, json.JSONDecodeError) as exc:
        raise ArtifactUnreadable(
            f'{label} 不符合严格 JSON 契约：{exc}') from exc
    if not isinstance(value, dict):
        raise ArtifactUnreadable(f'{label} 不是 JSON object')
    return value


def _require_plain_contained_path(
        path: Path, root: Path, *, label: str, leaf: str,
        allow_missing: bool = False,
) -> None:
    """拒绝 sidecar 路径链上的 symlink/junction 与非预期文件类型。"""
    path = Path(path)
    root = Path(root)
    try:
        relative = path.relative_to(root)
    except ValueError:
        raise ArtifactUnreadable(f'{label} {path} 越出登记根 {root}') from None
    current = root
    nodes = (root, *(root / Path(*relative.parts[:index])
                     for index in range(1, len(relative.parts) + 1)))
    for index, node in enumerate(nodes):
        try:
            info = node.lstat()
        except FileNotFoundError:
            if allow_missing:
                break
            raise ProvenanceRefused(f'{label} {node} 不存在') from None
        except OSError as exc:
            raise ArtifactUnreadable(f'{label} {node} 无法 lstat：{exc}') from exc
        if stat.S_ISLNK(info.st_mode) or node.is_symlink():
            raise ArtifactUnreadable(f'{label} 路径目录/leaf 不得是符号链接：{node}')
        is_last = index == len(nodes) - 1
        if is_last:
            expected = stat.S_ISREG(info.st_mode) if leaf == 'file' else stat.S_ISDIR(info.st_mode)
            if not expected:
                raise ArtifactUnreadable(f'{label} {node} 不是普通{leaf}')
        elif not stat.S_ISDIR(info.st_mode):
            raise ArtifactUnreadable(f'{label} 祖先 {node} 不是普通目录')
    try:
        root_resolved = root.resolve(strict=not allow_missing)
        probe = path if path.exists() else path.parent
        resolved = probe.resolve(strict=not allow_missing)
        resolved.relative_to(root_resolved)
    except (OSError, RuntimeError, ValueError) as exc:
        raise ArtifactUnreadable(
            f'{label} {path} resolve 后越出登记根/不可验证：{exc}') from exc


def _strict_typed_json(
        path: Path, model: type[M], *, label: str, root: Path,
) -> M:
    """恢复权威 JSON 的严格 decoder；拒绝 duplicate keys 与非有限数字。"""
    try:
        _require_plain_contained_path(path, root, label=label, leaf='file')
        raw = path.read_bytes()
        value = _strict_json_object_bytes(raw, label=f'{label} {path}')
        typed = model.model_validate(value)
        canonical = json.dumps(
            typed.model_dump(by_alias=True, mode='json'),
            ensure_ascii=False, indent=2, sort_keys=True,
            allow_nan=False,
        ).encode('utf-8') + b'\n'
        if raw != canonical:
            raise ArtifactUnreadable(
                f'{label} {path} 不是 producer 唯一 canonical JSON 原字节')
        return typed
    except FileNotFoundError:
        raise ProvenanceRefused(f'{label} {path} 不存在') from None
    except OSError as exc:
        raise ArtifactUnreadable(f'{label} {path} 不可读：{exc}') from exc
    except ValidationError as exc:
        raise ArtifactUnreadable(
            f'{label} {path} 不符合严格 typed 契约：{exc}') from exc


_REVIEWER_EXECUTION_DIR = 'reviewer_rewrite_execution'
_REVIEWER_EXECUTION_FILE_RE = re.compile(r'^rev-([0-9]{4,})\.json$')
_REVIEWER_RECOVERY_FILE = 'reviewer_rewrite_recovery.json'
_REVIEWER_DISPOSITION_DIR = 'reviewer_rewrite_dispositions'
_REVIEWER_SUCCESSOR_DIR = 'reviewer_rewrite_successors'


def _reviewer_execution_dir(
        novel_dir: Path, run_id: str, chapter: int,
) -> Path:
    return (paths.stage5_chapter_run_path(
        novel_dir, run_id, _chapter(chapter)) / _REVIEWER_EXECUTION_DIR)


def _validated_reviewer_execution_revision(
        path: Path, *, sid: str,
) -> ReviewerRewriteExecutionJournal:
    row = _strict_typed_json(
        path, ReviewerRewriteExecutionJournal,
        label='Stage5 reviewer rewrite execution', root=path.parents[3])
    match = _REVIEWER_EXECUTION_FILE_RE.fullmatch(path.name)
    if (match is None or int(match.group(1)) != row.sequence
            or path.name != f'rev-{row.sequence:04d}.json'):
        raise ArtifactUnreadable(
            f'{path} reviewer rewrite execution revision/path 漂移')
    run_dir = path.parent.parent.parent
    chapter_dir = path.parent.parent
    suffix = chapter_dir.name[2:] if chapter_dir.name.startswith('ch') else ''
    if (not suffix.isdigit() or int(suffix) != row.output_ordinal
            or run_dir.name != row.run_id or row.sid != sid
            or row.node != 'stage5.write'):
        raise ArtifactUnreadable(
            f'{path} reviewer rewrite execution 物理路径/主体漂移')
    return row


def _reviewer_execution_files(
        ctx: Any, *, run_id: str, chapter: int,
) -> tuple[Path, ...]:
    source = check_run_id(run_id)
    dirname = f'ch{_chapter(chapter):04d}'
    found: dict[int, Path] = {}
    for root in runtime_layout.stage_run_roots(ctx.novel_dir, 'stage5'):
        if root.exists():
            _require_plain_contained_path(
                root, root, label='Stage5 run read root', leaf='dir')
        directory = root / source / dirname / _REVIEWER_EXECUTION_DIR
        if not directory.exists():
            continue
        _require_plain_contained_path(
            directory, root, label='Stage5 reviewer rewrite execution dir',
            leaf='dir')
        for candidate in directory.glob('rev-*.json'):
            _require_plain_contained_path(
                candidate, root, label='Stage5 reviewer rewrite execution',
                leaf='file')
            match = _REVIEWER_EXECUTION_FILE_RE.fullmatch(candidate.name)
            if match is None:
                raise ArtifactUnreadable(
                    f'{candidate} reviewer rewrite execution 文件名不 canonical')
            sequence = int(match.group(1))
            prior = found.get(sequence)
            if prior is not None and prior.read_bytes() != candidate.read_bytes():
                raise ArtifactUnreadable(
                    'service/legacy reviewer rewrite execution 同 revision 字节冲突')
            found.setdefault(sequence, candidate)
    return tuple(found[key] for key in sorted(found))


def reviewer_rewrite_execution_chain(
        ctx: Any, *, run_id: str, chapter: int,
) -> tuple[ReviewerRewriteExecutionJournal, ...]:
    """读取并关闭式验证一个 run 的 append-only execution chain。"""
    files = _reviewer_execution_files(
        ctx, run_id=run_id, chapter=chapter)
    rows = tuple(
        _validated_reviewer_execution_revision(path, sid=ctx.sid)
        for path in files)
    if not rows:
        return ()
    identity = {
        'sid': rows[0].sid,
        'node': rows[0].node,
        'run_id': rows[0].run_id,
        'parent_run_id': rows[0].parent_run_id,
        'claim_predecessor_journal_sha256':
            rows[0].claim_predecessor_journal_sha256,
        'code': rows[0].code,
        'output_ordinal': rows[0].output_ordinal,
        'mode': rows[0].mode,
        'purpose': rows[0].purpose,
        'request_rel': rows[0].request_rel,
        'request_file_sha256': rows[0].request_file_sha256,
        'request_sha256': rows[0].request_sha256,
        'input_sha256': rows[0].input_sha256,
    }
    previous: ReviewerRewriteExecutionJournal | None = None
    terminal_seen = False
    allowed_next = {
        'started': {'checkpointed', 'reclaimed_unstarted'},
        'checkpointed': {
            'retry_started', 'delivery_prepared',
            'landed_invalid', 'landed_rejected'},
        'retry_started': {
            'checkpointed', 'delivery_prepared',
            'landed_invalid', 'landed_rejected'},
        'delivery_prepared': {
            'landed_formal', 'landed_invalid', 'landed_rejected'},
        'landed_formal': set(),
        'landed_invalid': set(),
        'landed_rejected': set(),
        'reclaimed_unstarted': set(),
    }
    for index, row in enumerate(rows, 1):
        if row.sequence != index:
            raise ArtifactUnreadable(
                f'reviewer rewrite execution chain 缺 revision {index}')
        if any(getattr(row, key) != value for key, value in identity.items()):
            raise ArtifactUnreadable('reviewer rewrite execution chain 主体中途漂移')
        if previous is not None and row.previous_journal_sha256 != previous.journal_sha256:
            raise ArtifactUnreadable('reviewer rewrite execution previous SHA 断链')
        if previous is None and row.event != 'started':
            raise ArtifactUnreadable('reviewer rewrite execution 首 revision 必须是 started')
        if previous is not None and row.event not in allowed_next[previous.event]:
            raise ArtifactUnreadable(
                f'reviewer rewrite execution 非法跃迁：{previous.event}->{row.event}')
        if (previous is not None
                and previous.checkpoint_sha256 is not None
                and not (previous.event == 'retry_started'
                         and row.event == 'checkpointed')):
            if row.checkpoint_sha256 != previous.checkpoint_sha256:
                raise ArtifactUnreadable(
                    'reviewer rewrite execution checkpoint SHA 中途漂移')
        if (previous is not None and previous.commit_sha256 is not None
                and row.commit_sha256 != previous.commit_sha256
                and not (previous.event == 'delivery_prepared'
                         and row.event == 'landed_formal')):
            raise ArtifactUnreadable(
                'reviewer rewrite execution commit SHA 中途漂移')
        if terminal_seen:
            raise ArtifactUnreadable('reviewer rewrite terminal 后仍出现新 revision')
        terminal_seen = row.event.startswith('landed_') or (
            row.event == 'reclaimed_unstarted')
        previous = row
    latest = rows[-1]
    if latest.checkpoint_sha256 is not None:
        # self SHA 必须唯一命中一份仍可读的 append-only checkpoint；仅凭 journal
        # 抄一个摘要不能把丢失/损坏的模型响应伪装成 recoverable。
        reviewer_rewrite_checkpoint_file(
            ctx, run_id=latest.run_id, chapter=latest.output_ordinal,
            checkpoint_sha256=latest.checkpoint_sha256)
    if latest.commit_sha256 is not None:
        commit = reviewer_rewrite_commit_envelope(
            ctx, run_id=latest.run_id, chapter=latest.output_ordinal)
        if commit is None:
            raise ArtifactUnreadable('reviewer rewrite execution 缺 commit 原字节')
        prepared_sha = agent_runs.prepared_commit_sha256(commit)
        for row in rows:
            if row.commit_sha256 is None:
                continue
            expected = (commit['commit_sha256'] if row.event == 'landed_formal'
                        else prepared_sha)
            if row.commit_sha256 != expected:
                raise ArtifactUnreadable('reviewer rewrite execution commit SHA 无法闭合原字节')
    return rows


def reviewer_rewrite_execution_chains(
        ctx: Any, *, chapter: int, request_sha256: str,
        request_file_sha256: str,
) -> tuple[tuple[ReviewerRewriteExecutionJournal, ...], ...]:
    """枚举一本书内绑定同一 formal request 的全部 run chain。"""
    ordinal = _chapter(chapter)
    run_ids: set[str] = set()
    dirname = f'ch{ordinal:04d}'
    for root in runtime_layout.stage_run_roots(ctx.novel_dir, 'stage5'):
        if not root.exists():
            continue
        _require_plain_contained_path(
            root, root, label='Stage5 run read root', leaf='dir')
        for directory in root.glob(
                f'*/{dirname}/{_REVIEWER_EXECUTION_DIR}'):
            _require_plain_contained_path(
                directory, root, label='Stage5 reviewer rewrite execution dir',
                leaf='dir')
            run_ids.add(directory.parent.parent.name)
    matches: list[tuple[ReviewerRewriteExecutionJournal, ...]] = []
    for run_id in sorted(run_ids):
        chain = reviewer_rewrite_execution_chain(
            ctx, run_id=run_id, chapter=ordinal)
        if not chain:
            continue
        first = chain[0]
        if first.request_sha256 == request_sha256:
            if first.request_file_sha256 != request_file_sha256:
                raise ProvenanceRefused(
                    '同一 reviewer rewrite self SHA 出现不同 canonical request 原字节')
            matches.append(chain)
    return tuple(matches)


def reviewer_rewrite_execution_lineage(
        chains: tuple[tuple[ReviewerRewriteExecutionJournal, ...], ...],
) -> tuple[tuple[ReviewerRewriteExecutionJournal, ...], ...]:
    """把同 request 的跨-run claim 关闭成唯一无分叉单链。

    ``reclaimed_unstarted`` 与下一 run 的 ``started`` 分属两个文件，不能原子
    os.replace。下一 claim 因而显式指向前一条 reclaimed revision；硬杀在两次
    写之间只留下一个合法的 dangling tail，任意后续 run 都可继续接在该 tail，
    而不是靠时间戳或 run 名猜“最新”。
    """
    if not chains:
        return ()
    by_run: dict[str, tuple[ReviewerRewriteExecutionJournal, ...]] = {}
    latest_by_sha: dict[str, tuple[ReviewerRewriteExecutionJournal, ...]] = {}
    for chain in chains:
        if not chain or chain[0].run_id in by_run:
            raise ProvenanceRefused('reviewer rewrite execution lineage 含空链/重复 run')
        by_run[chain[0].run_id] = chain
        latest_by_sha[chain[-1].journal_sha256] = chain
    if len(latest_by_sha) != len(chains):
        raise ProvenanceRefused('reviewer rewrite execution lineage tail SHA 冲突')
    next_by_run: dict[str, tuple[ReviewerRewriteExecutionJournal, ...]] = {}
    heads: list[tuple[ReviewerRewriteExecutionJournal, ...]] = []
    for chain in chains:
        predecessor_sha = chain[0].claim_predecessor_journal_sha256
        if predecessor_sha is None:
            heads.append(chain)
            continue
        predecessor = latest_by_sha.get(predecessor_sha)
        if predecessor is None:
            raise ProvenanceRefused(
                'reviewer rewrite claim predecessor 未命中 exact execution tail')
        if predecessor[-1].event != 'reclaimed_unstarted':
            raise ProvenanceRefused(
                'reviewer rewrite 新 claim 只能接在 reclaimed_unstarted 后')
        predecessor_run = predecessor[0].run_id
        if predecessor_run in next_by_run:
            raise ProvenanceRefused('reviewer rewrite execution lineage 出现分叉')
        next_by_run[predecessor_run] = chain
    if len(heads) != 1:
        raise ProvenanceRefused('reviewer rewrite execution lineage 必须恰有一个起点')
    ordered: list[tuple[ReviewerRewriteExecutionJournal, ...]] = []
    seen: set[str] = set()
    current = heads[0]
    while True:
        run_id = current[0].run_id
        if run_id in seen:
            raise ProvenanceRefused('reviewer rewrite execution lineage 成环')
        seen.add(run_id)
        ordered.append(current)
        successor = next_by_run.get(run_id)
        if successor is None:
            break
        current = successor
    if len(ordered) != len(chains):
        raise ProvenanceRefused('reviewer rewrite execution lineage 含孤儿 claim')
    if any(chain[-1].event != 'reclaimed_unstarted' for chain in ordered[:-1]):
        raise ProvenanceRefused('reviewer rewrite 非尾 claim 未被 zero-wire reclaim')
    return tuple(ordered)


def record_reviewer_rewrite_execution(
        ctx: Any, *, chapter: int, request_rel: str,
        request_file_sha256: str, request_sha256: str, input_sha256: str,
        event: str, checkpoint_sha256: str | None = None,
        commit_sha256: str | None = None, outcome: str | None = None,
        landed_rel: str | None = None, landed_file_sha256: str | None = None,
        landed_payload_sha256: str | None = None,
        claim_predecessor_journal_sha256: str | None = None,
        reclaimed_by_run_id: str | None = None,
        reclaimed_by_parent_run_id: str | None = None,
        reclaim_evidence: Mapping[str, Any] | None = None,
        reclaim_evidence_sha256: str | None = None,
) -> ReviewerRewriteExecutionJournal:
    """为当前 run 原子追加一条 reviewer rewrite 执行 revision。"""
    ordinal = _chapter(chapter)
    if ctx.node != 'stage5.write':
        raise ProvenanceRefused('reviewer rewrite execution 只属于 stage5.write')
    _require_reviewer_execution_ownership(
        Path(ctx.novel_dir), request_sha256)
    target_dir = _reviewer_execution_dir(
        Path(ctx.novel_dir), ctx.run_id, ordinal)
    _require_plain_contained_path(
        target_dir, paths.service_stage_runs_dir(ctx.novel_dir, 'stage5'),
        label='Stage5 reviewer rewrite execution target dir', leaf='dir',
        allow_missing=True)
    lock_path = target_dir / '.append.lock'
    with process_append_lock(lock_path):
        chain = reviewer_rewrite_execution_chain(
            ctx, run_id=ctx.run_id, chapter=ordinal)
        latest = chain[-1] if chain else None
        # predecessor 属于整条 claim，后续 checkpoint/terminal 继承已验证首行；
        # 不要求每个消费者再传一份同义参数，也不允许显式换成别的 predecessor。
        if latest is not None and claim_predecessor_journal_sha256 is None:
            claim_predecessor_journal_sha256 = latest.claim_predecessor_journal_sha256
        identity = {
            'request_rel': request_rel,
            'request_file_sha256': request_file_sha256,
            'request_sha256': request_sha256,
            'input_sha256': input_sha256,
            'claim_predecessor_journal_sha256':
                claim_predecessor_journal_sha256,
        }
        if latest is not None and any(
                getattr(latest, key) != value for key, value in identity.items()):
            raise ProvenanceRefused(
                '当前 Stage5 run/chapter 已绑定另一份 reviewer rewrite request/input')
        proposed_tail = {
            'event': event,
            'checkpoint_sha256': checkpoint_sha256,
            'commit_sha256': commit_sha256,
            'outcome': outcome,
            'landed_rel': landed_rel,
            'landed_file_sha256': landed_file_sha256,
            'landed_payload_sha256': landed_payload_sha256,
            'reclaimed_by_run_id': reclaimed_by_run_id,
            'reclaimed_by_parent_run_id': reclaimed_by_parent_run_id,
            'reclaim_evidence': (
                None if reclaim_evidence is None else dict(reclaim_evidence)),
            'reclaim_evidence_sha256': reclaim_evidence_sha256,
        }
        if latest is not None and all(
                getattr(latest, key) == value for key, value in proposed_tail.items()):
            return latest
        if latest is not None and latest.event.startswith('landed_'):
            raise ProvenanceRefused('reviewer rewrite execution 已终态，拒绝继续追加')
        transitions = {
            None: {'started'},
            'started': {'checkpointed', 'reclaimed_unstarted'},
            'checkpointed': {
                'retry_started', 'delivery_prepared',
                'landed_invalid', 'landed_rejected'},
            'retry_started': {
                'checkpointed', 'delivery_prepared',
                'landed_invalid', 'landed_rejected'},
            'delivery_prepared': {
                'landed_formal', 'landed_invalid', 'landed_rejected'},
        }
        prior_event = None if latest is None else latest.event
        if event not in transitions.get(prior_event, set()):
            raise ProvenanceRefused(
                f'reviewer rewrite execution 非法跃迁：{prior_event}->{event}')
        if (latest is not None and latest.checkpoint_sha256 is not None
                and not (latest.event == 'retry_started'
                         and event == 'checkpointed')):
            if checkpoint_sha256 != latest.checkpoint_sha256:
                raise ProvenanceRefused(
                    'reviewer rewrite execution checkpoint SHA 不得漂移')
        if latest is not None and latest.commit_sha256 is not None:
            if commit_sha256 != latest.commit_sha256:
                commit = reviewer_rewrite_commit_envelope(
                    ctx, run_id=ctx.run_id, chapter=ordinal)
                if (latest.event != 'delivery_prepared' or event != 'landed_formal'
                        or commit is None or commit.get('state') != 'committed'
                        or commit_sha256 != commit['commit_sha256']
                        or latest.commit_sha256 != agent_runs.prepared_commit_sha256(commit)):
                    raise ProvenanceRefused('reviewer rewrite execution commit SHA 不得漂移')
        sequence = 1 if latest is None else latest.sequence + 1
        raw: dict[str, Any] = {
            'version': 1,
            'kind': 'stage5-reviewer-rewrite-execution-v1',
            'sequence': sequence,
            'recorded_at': _now(),
            'previous_journal_sha256': (
                None if latest is None else latest.journal_sha256),
            'sid': ctx.sid,
            'node': 'stage5.write',
            'run_id': ctx.run_id,
            'parent_run_id': ctx.parent_run_id,
            'code': ctx.code,
            'output_ordinal': ordinal,
            'mode': 'reviewer_rewrite',
            'purpose': purpose_of(ordinal, 'reviewer_rewrite'),
            **identity,
            **proposed_tail,
            'journal_sha256': '0' * 64,
        }
        raw['journal_sha256'] = reviewer_rewrite_execution_journal_sha256(raw)
        row = ReviewerRewriteExecutionJournal.model_validate(raw)
        target = target_dir / f'rev-{sequence:04d}.json'
        if target.exists():
            current = _validated_reviewer_execution_revision(
                target, sid=ctx.sid)
            if current != row:
                raise ProvenanceRefused(
                    f'{target} reviewer rewrite execution revision 已被其他主体占用')
            return current
        agent_runs._atomic_json(target, row.model_dump(mode='json'))
        readback = _validated_reviewer_execution_revision(target, sid=ctx.sid)
        if readback != row:
            raise ProvenanceRefused('reviewer rewrite execution 写后 readback 漂移')
        return readback


def reviewer_rewrite_unstarted_evidence(
        ctx: Any, chain: tuple[ReviewerRewriteExecutionJournal, ...],
) -> dict[str, Any]:
    """证明一个 ``started`` claim 尚未建立 session/attempt/cost/wire/checkpoint。"""
    if (not chain or chain[0].event != 'started'
            or len(chain) not in (1, 2)
            or (len(chain) == 2
                and chain[-1].event != 'reclaimed_unstarted')):
        raise ProvenanceRefused('只有 started[/reclaimed] chain 才可能证明 zero-execution')
    row = chain[0]
    run_path, run_file_sha256 = _unique_reviewer_history_file(
        ctx, run_id=row.run_id, chapter=row.output_ordinal,
        filename=agent_runs.RUN_FILE)
    record = _strict_json_object_bytes(
        run_path.read_bytes(), label='reviewer rewrite unstarted run.json')
    identity = {
        key: record[key] for key in record
        if key not in ('identity_sha256', 'created_at')
    }
    request = record.get('request')
    policy = record.get('request_policy')
    profile = policy.get('profile') if isinstance(policy, dict) else None
    if (record.get('identity_sha256') != agent_runs.canonical_sha256(identity)
            or record.get('run_id') != row.run_id
            or record.get('sid') != ctx.sid
            or record.get('node') != 'stage5.write'
            or record.get('chapter') != row.output_ordinal
            or record.get('purpose') != row.purpose
            or record.get('code') != row.code
            or record.get('input_sha256') != row.input_sha256
            or not isinstance(request, dict)
            or not isinstance(policy, dict)
            or not isinstance(profile, dict)):
        raise ProvenanceRefused('reviewer rewrite unstarted run identity 不闭合')
    phases = policy.get('phases')
    if (set(policy) != {
            'protocol_version', 'profile', 'session_isolation',
            'recovery_policy', 'phases'}
            or not isinstance(policy.get('protocol_version'), str)
            or not policy['protocol_version']
            or not isinstance(phases, list) or len(phases) != 1
            or not isinstance(phases[0], dict)):
        raise ProvenanceRefused(
            'reviewer rewrite unstarted run request policy 历史形状不闭合')
    phase = phases[0]
    session_subject = agent_runs.canonical_sha256({
        'node': 'stage5.write',
        'chapter': row.output_ordinal,
        'purpose': row.purpose,
        'system_sha256': request.get('system_sha256'),
        'user_sha256': request.get('user_sha256'),
        'schema_sha256': request.get('response_schema_sha256'),
        'requested_model': profile.get('model'),
        'requested_effort': profile.get('reasoning_effort'),
        'requested_output_token_ceiling': phase.get(
            'requested_output_token_ceiling'),
        # expiry 是历史事实；协议值必须来自已由 run identity/self SHA 冻结的
        # request policy，不能用今天的客户端常量重算旧 receipt。
        'protocol_version': policy['protocol_version'],
    })
    dirname = f'ch{row.output_ordinal:04d}'
    forbidden_files: list[str] = []
    for root in runtime_layout.stage_run_roots(ctx.novel_dir, 'stage5'):
        chapter_dir = root / row.run_id / dirname
        for candidate in (
                chapter_dir / agent_runs.COMMIT_FILE,
                chapter_dir / _REVIEWER_RECOVERY_FILE):
            if candidate.exists():
                forbidden_files.append(str(candidate))
        checkpoint_dir = chapter_dir / 'checkpoints'
        if checkpoint_dir.exists():
            forbidden_files.extend(str(path) for path in checkpoint_dir.iterdir())
    if forbidden_files:
        raise ProvenanceRefused(
            'reviewer rewrite started claim 已出现 checkpoint/commit/recovery 证据')
    base = Path(ctx.novel_dir).parent
    cost_rows = _strict_jsonl_objects(
        ledger_module.ledger_path(base), label='reviewer rewrite reclaim 成本账')
    if any(item.get('run_id') == row.run_id for item in cost_rows):
        raise ProvenanceRefused('reviewer rewrite started claim 已出现 cost/attempt 证据')
    session_rows = _strict_jsonl_objects(
        agent_session_audit.audit_path(base),
        label='reviewer rewrite reclaim session 账')
    for index, item in enumerate(session_rows):
        try:
            event = agent_session_audit.AgentSessionEvent.from_dict(item)
        except (TypeError, ValueError) as exc:
            raise ProvenanceRefused(
                f'reviewer rewrite reclaim session 账第 {index + 1} 行不合法') from exc
        if event.parent_run_id == row.run_id:
            raise ProvenanceRefused(
                'reviewer rewrite started claim 已出现 logical session/wire 证据；'
                '身份即使漂移也不能洗成 zero-execution')
    evidence = {
        'kind': 'stage5-reviewer-rewrite-unstarted-evidence-v1',
        'sid': ctx.sid,
        'source_run_id': row.run_id,
        'source_parent_run_id': row.parent_run_id,
        'source_run_file_sha256': run_file_sha256,
        'source_run_identity_sha256': record['identity_sha256'],
        'source_execution_journal_sha256': row.journal_sha256,
        'request_file_sha256': row.request_file_sha256,
        'request_sha256': row.request_sha256,
        'input_sha256': row.input_sha256,
        'session_subject_sha256': session_subject,
        'checkpoint_count': 0,
        'commit_count': 0,
        'recovery_wal_count': 0,
        'matching_session_count': 0,
        'matching_cost_count': 0,
    }
    if len(chain) == 2:
        reclaimed = chain[-1]
        if (reclaimed.reclaim_evidence != evidence
                or reclaimed.reclaim_evidence_sha256
                != agent_runs.canonical_sha256(evidence)):
            raise ProvenanceRefused(
                'reviewer rewrite reclaimed chain 与 current zero-execution 证据漂移')
    return evidence


def reclaim_reviewer_rewrite_unstarted(
        ctx: Any, chain: tuple[ReviewerRewriteExecutionJournal, ...],
) -> ReviewerRewriteExecutionJournal:
    """在 old-owner-stopped lease 内退休零执行 claim，允许新 run 再 claim。"""
    _require_ownership(ctx, chain[0].output_ordinal if chain else 0)
    if chain:
        _require_reviewer_execution_ownership(
            Path(ctx.novel_dir), chain[0].request_sha256)
    if not chain or chain[0].run_id == ctx.run_id:
        raise ProvenanceRefused('reviewer rewrite reclaim 必须由不同的新 run 执行')
    source = chain[0]
    with reviewer_rewrite_reclaim_owner_stopped(ctx, source):
        current = reviewer_rewrite_execution_chain(
            ctx, run_id=source.run_id, chapter=source.output_ordinal)
        if current != chain:
            raise ProvenanceRefused('reviewer rewrite reclaim 前 source chain 漂移')
        evidence = reviewer_rewrite_unstarted_evidence(ctx, current)
        evidence_sha256 = agent_runs.canonical_sha256(evidence)
        target_dir = _reviewer_execution_dir(
            Path(ctx.novel_dir), source.run_id, source.output_ordinal)
        _require_plain_contained_path(
            target_dir, paths.service_stage_runs_dir(ctx.novel_dir, 'stage5'),
            label='Stage5 reviewer rewrite reclaim target dir', leaf='dir',
            allow_missing=True)
        target = target_dir / 'rev-0002.json'
        with process_append_lock(target_dir / '.append.lock'):
            current = reviewer_rewrite_execution_chain(
                ctx, run_id=source.run_id, chapter=source.output_ordinal)
            if len(current) == 2 and current[-1].event == 'reclaimed_unstarted':
                return current[-1]
            if current != chain:
                raise ProvenanceRefused('reviewer rewrite reclaim 写前 source chain 漂移')
            raw = source.model_dump(mode='json')
            raw.update({
                'sequence': 2,
                'recorded_at': _now(),
                'previous_journal_sha256': source.journal_sha256,
                'event': 'reclaimed_unstarted',
                'checkpoint_sha256': None,
                'commit_sha256': None,
                'outcome': None,
                'landed_rel': None,
                'landed_file_sha256': None,
                'landed_payload_sha256': None,
                'reclaimed_by_run_id': check_run_id(ctx.run_id),
                'reclaimed_by_parent_run_id': check_run_id(ctx.parent_run_id),
                'reclaim_evidence': evidence,
                'reclaim_evidence_sha256': evidence_sha256,
                'journal_sha256': '0' * 64,
            })
            raw['journal_sha256'] = reviewer_rewrite_execution_journal_sha256(raw)
            reclaimed = ReviewerRewriteExecutionJournal.model_validate(raw)
            agent_runs._atomic_json(target, reclaimed.model_dump(mode='json'))
            return _validated_reviewer_execution_revision(target, sid=ctx.sid)


@contextmanager
def reviewer_rewrite_reclaim_owner_stopped(
        ctx: Any, row: ReviewerRewriteExecutionJournal,
):
    """证明旧 claim owner 已停止；direct CLI 由 request OS lease 自证。

    AgentRun 的 child ``run_id`` 与稳定 ``parent_run_id`` 不同，仍须闭合外层
    owner state。直接 CLI 二者相同且没有 AgentRun state；此时调用者已经拿到同一
    request 的跨进程排他锁，能拿到锁本身就证明旧 CLI 不再持有执行窗。
    """
    _require_reviewer_execution_ownership(
        Path(ctx.novel_dir), row.request_sha256)
    if row.parent_run_id == row.run_id:
        yield
        return
    from novel.orchestrator import admission
    with admission.require_owner_stopped(
            Path(ctx.novel_dir).parent, row.parent_run_id):
        yield


def reviewer_rewrite_checkpoint_file(
        ctx: Any, *, run_id: str, chapter: int, checkpoint_sha256: str,
) -> Path:
    """按 self SHA 唯一定位历史 checkpoint 原字节。"""
    if not agent_runs.is_sha256(checkpoint_sha256):
        raise ProvenanceRefused('reviewer rewrite checkpoint SHA 不合法')
    source = check_run_id(run_id)
    dirname = f'ch{_chapter(chapter):04d}'
    matches: list[Path] = []
    for root in runtime_layout.stage_run_roots(ctx.novel_dir, 'stage5'):
        directory = root / source / dirname / 'checkpoints'
        if not directory.is_dir():
            continue
        for candidate in sorted(directory.glob('rev-*.json')):
            try:
                payload = _strict_json_object_bytes(
                    candidate.read_bytes(),
                    label=f'{candidate} reviewer rewrite checkpoint')
            except OSError as exc:
                raise ArtifactUnreadable(
                    f'{candidate} reviewer rewrite checkpoint 不可读：{exc}') from exc
            if (isinstance(payload, dict)
                    and payload.get('checkpoint_sha256') == checkpoint_sha256):
                matches.append(candidate)
    unique = {path.read_bytes(): path for path in matches}
    if len(unique) != 1:
        raise ProvenanceRefused(
            'reviewer rewrite checkpoint self SHA 必须唯一命中同一份原字节')
    return next(iter(unique.values()))


def reviewer_rewrite_commit_envelope(
        ctx: Any, *, run_id: str, chapter: int,
) -> dict[str, Any] | None:
    """只读一个历史 reviewer rewrite generic commit 的 hash-closed 外壳。"""
    source = check_run_id(run_id)
    ordinal = _chapter(chapter)
    candidates: list[Path] = []
    dirname = f'ch{ordinal:04d}'
    for root in runtime_layout.stage_run_roots(ctx.novel_dir, 'stage5'):
        target = root / source / dirname / agent_runs.COMMIT_FILE
        if target.is_file():
            candidates.append(target)
    if not candidates:
        return None
    raw_values = {target.read_bytes() for target in candidates}
    if len(raw_values) != 1:
        raise ArtifactUnreadable(
            'service/legacy reviewer rewrite commit 字节冲突')
    target = candidates[0]
    with process_append_lock(target):
        record = agent_runs.validate_foreign_commit_envelope(
            target, label='Stage5 reviewer rewrite commit',
            run_id=source, sid=ctx.sid, node='stage5.write')
    if record.get('purpose') != purpose_of(ordinal, 'reviewer_rewrite'):
        raise ProvenanceRefused(
            f'{target} 不是 reviewer rewrite commit reservation')
    return record


def _validate_historical_reviewer_session_turn(
        historical_ctx: Any, spec: agent_runs.SingleSessionRunSpec, *,
        run_record: Mapping[str, Any], checkpoint: Mapping[str, Any],
) -> None:
    """按 source run 冻结的 prompt/schema 摘要验证 session，不借当前源码重建。"""
    try:
        events = agent_session_audit.read_events(historical_ctx.novel_dir.parent)
        agent_session_audit.fold(events)
    except (OSError, UnicodeError, ValueError) as exc:
        raise ProvenanceRefused(
            f'{spec.label}: historical session 审计不可验证：{exc}') from exc
    acceptance = {
        key: checkpoint[key] for key in agent_runs.AGENT_ACCEPTANCE_FIELDS}
    profile = acceptance.get('profile')
    request = run_record.get('request')
    if not isinstance(profile, dict) or not isinstance(request, dict):
        raise ArtifactUnreadable('reviewer rewrite historical acceptance/run 身份残缺')
    attempt_id = acceptance.get('attempt_id')
    turns = [
        event for event in events
        if event.kind == 'turn' and event.attempt_id == attempt_id]
    starts = [
        event for event in events
        if (event.kind == 'started'
            and event.session_run_id == acceptance.get('session_run_id'))]
    if len(turns) != 1 or len(starts) != 1:
        raise ProvenanceRefused(
            f'{spec.label}: historical checkpoint 缺唯一 session started/turn')
    turn = turns[0]
    start = starts[0]
    current_session = agent_runs.validate_agent_session_request_binding(
        events, acceptance, purpose=spec.purpose, profile=profile,
        original_prompt_sha256=str(request.get('initial_prompt_sha256')),
        original_schema_sha256=str(request.get('response_schema_sha256')),
    )
    first_turn = current_session.turns[0]
    start_profile = (
        None if start.profile is None else start.profile.model_dump(mode='json'))
    turn_profile = (
        None if turn.profile is None else turn.profile.model_dump(mode='json'))
    expected_start = {
        'wire_version': agent_session_audit.SESSION_AUDIT_WIRE_VERSION,
        'session_run_id': acceptance.get('session_run_id'),
        'parent_run_id': check_run_id(historical_ctx.run_id),
        'role': 'executor', 'node': spec.node, 'chapter': spec.chapter,
        'subject_sha256': acceptance.get('subject_sha256'),
        'profile': dict(profile), 'provider_session_id': None,
        'requested_model': profile.get('model'), 'actual_model': None,
        'requested_effort': profile.get('reasoning_effort'),
        'actual_effort': None,
        'protocol_version': agent_runs.SESSION_PROTOCOL_VERSION,
        'start_code_version': historical_ctx.code,
        'prompt_sha256': first_turn.prompt_sha256,
        'schema_sha256': first_turn.schema_sha256,
    }
    actual_start = {
        'wire_version': start.wire_version,
        'session_run_id': start.session_run_id,
        'parent_run_id': start.parent_run_id,
        'role': start.role, 'node': start.node, 'chapter': start.chapter,
        'subject_sha256': start.subject_sha256, 'profile': start_profile,
        'provider_session_id': start.provider_session_id,
        'requested_model': start.requested_model, 'actual_model': start.actual_model,
        'requested_effort': start.requested_effort,
        'actual_effort': start.actual_effort,
        'protocol_version': start.protocol_version,
        'start_code_version': start.start_code_version,
        'prompt_sha256': start.prompt_sha256,
        'schema_sha256': start.schema_sha256,
    }
    if actual_start != expected_start:
        raise ProvenanceRefused(
            f'{spec.label}: historical started 与 source subject 漂移')
    model_identity = agent_runs.validate_checkpoint_model_identity(
        profile, checkpoint_model=acceptance.get('model'),
        actual_model=turn.actual_model, cli_version=turn.cli_version,
        require_driver_version=True)
    expected_turn = {
        'wire_version': agent_session_audit.SESSION_AUDIT_WIRE_VERSION,
        'session_run_id': acceptance.get('session_run_id'),
        'parent_run_id': check_run_id(historical_ctx.run_id),
        'role': 'executor', 'node': spec.node, 'chapter': spec.chapter,
        'subject_sha256': acceptance.get('subject_sha256'),
        'profile': dict(profile),
        'provider_session_id': acceptance.get('provider_session_id'),
        'turn_index': acceptance.get('accepted_turn_index'),
        'attempt_id': attempt_id,
        'requested_model': profile.get('model'),
        'actual_model': model_identity.actual_model,
        'requested_effort': profile.get('reasoning_effort'),
        'protocol_version': agent_runs.SESSION_PROTOCOL_VERSION,
        'start_code_version': historical_ctx.code,
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
        'prompt_sha256': turn.prompt_sha256,
        'schema_sha256': turn.schema_sha256,
    }
    if actual_turn != expected_turn:
        raise ProvenanceRefused(
            f'{spec.label}: historical checkpoint 与唯一 session turn 漂移')
    if (turn.actual_effort is not None
            and turn.actual_effort != profile.get('reasoning_effort')):
        raise ProvenanceRefused(
            f'{spec.label}: historical actual effort 与 profile 漂移')
    for event in events:
        event_profile = (
            None if event.profile is None else event.profile.model_dump(mode='json'))
        incompatible_owner = (
            event.node in spec.incompatible_session_nodes
            or (spec.chapter is not None and event.node == spec.node
                and event.chapter != spec.chapter))
        if (event.kind == 'turn' and incompatible_owner
                and event.provider_session_id == acceptance.get('provider_session_id')
                and isinstance(event_profile, dict)
                and event_profile.get('driver') == profile.get('driver')):
            raise ProvenanceRefused(
                f'{spec.label}: historical provider session 被互斥节点复用')


def verify_historical_reviewer_rewrite_terminal(
        ctx: Any, *, chain: tuple[ReviewerRewriteExecutionJournal, ...],
        request_sha256: str, request_file_sha256: str,
) -> HistoricalReviewerRewriteTerminal:
    """只用 source 当时的 run/checkpoint/session/cost 验证 terminal execution。

    current writer prompt、ChapterResponse schema、validator 与 plan 都不是历史事实；
    终态读取若要求它们仍与今天一致，会把合法落盘在代码换代后永久降成 unknown。
    """
    if not chain or chain[-1].event in {'started', 'reclaimed_unstarted', 'retry_started'}:
        raise ProvenanceRefused(
            'historical terminal 验证需要已关闭 wire intent 的 accepted checkpoint chain')
    first, tail = chain[0], chain[-1]
    if (first.request_sha256 != request_sha256
            or first.request_file_sha256 != request_file_sha256):
        raise ProvenanceRefused('historical terminal chain 与 request 身份漂移')
    run_path, run_sha = _unique_reviewer_history_file(
        ctx, run_id=first.run_id, chapter=first.output_ordinal,
        filename=agent_runs.RUN_FILE)
    try:
        run_raw = run_path.read_bytes()
    except OSError as exc:
        raise ArtifactUnreadable(
            f'reviewer rewrite historical run 不可读：{exc}') from exc
    record = _strict_json_object_bytes(
        run_raw, label='reviewer rewrite historical run.json')
    policy = record.get('request_policy')
    profile_payload = policy.get('profile') if isinstance(policy, dict) else None
    try:
        profile = agent_runs.validate_profile(
            agent_runs.AgentProfile.model_validate(profile_payload))
    except (ValidationError, ValueError, TypeError) as exc:
        raise ProvenanceRefused(
            f'reviewer rewrite historical profile 不可验证：{exc}') from exc
    # terminal/probe 是只读事实查询，不能要求 readonly_ctx 的内存账带生产写路径。
    # 沿 Stage4 同类恢复口从本书 base 读取持久成本证据，绝不替换调用方 ledger。
    ledger_path = ledger_module.ledger_path(Path(ctx.novel_dir).parent)
    spec = _run_spec(first.output_ordinal, 'reviewer_rewrite')
    historical = agent_runs._HistoricalContext(
        novel_dir=Path(ctx.novel_dir), node='stage5.write',
        code=str(record.get('code') or ''), executor_profile=profile,
        run_id=first.run_id,
        ledger=agent_runs.CostLedger(path=Path(ledger_path), run_id=first.run_id),
        _sid=str(ctx.sid),
    )
    checked = agent_runs._validate_run_record(historical, spec, record)
    if (checked.get('input_sha256') != first.input_sha256
            or run_sha != agent_runs.canonical_sha256(run_raw)):
        raise ProvenanceRefused('reviewer rewrite historical run/input 原字节漂移')
    with process_append_lock(agent_runs._checkpoint_lock_path(spec, historical)):
        checkpoint_rows = agent_runs._read_checkpoint_chain(
            historical, spec, run_sha=run_sha)
    checkpoint_journals = [row for row in chain if row.event == 'checkpointed']
    if (not checkpoint_rows
            or [row.checkpoint_sha256 for row in checkpoint_journals]
               != [str(payload['checkpoint_sha256'])
                   for _path, payload in checkpoint_rows]):
        raise ProvenanceRefused(
            'reviewer rewrite execution revisions 未逐轮闭合 checkpoint 链')
    # terminal 前最后一个 intent 必须已经被随后 checkpoint 关闭。否则可能有
    # 新 wire/扣费而 terminal 仍偷用了旧候选。
    last_checkpoint_pos = max(
        index for index, row in enumerate(chain) if row.event == 'checkpointed')
    if any(row.event == 'retry_started' for row in chain[last_checkpoint_pos + 1:-1]):
        raise ProvenanceRefused(
            'reviewer rewrite terminal 前存在未由新 checkpoint 关闭的 retry intent')
    for _path, checkpoint in checkpoint_rows:
        turn = checkpoint.get('accepted_turn_index')
        _validate_state_payload(
            checkpoint['state_payload'], chapter=first.output_ordinal,
            input_sha256=first.input_sha256, accepted_turn_index=turn,
            mode='reviewer_rewrite',
            reviewer_rewrite_request_sha256=request_sha256,
            reviewer_rewrite_request_file_sha256=request_file_sha256)
        _validate_historical_reviewer_session_turn(
            historical, spec, run_record=checked, checkpoint=checkpoint)
    latest = checkpoint_rows[-1][1]
    links = latest.get('attempt_links')
    if not isinstance(links, list):
        raise ProvenanceRefused('reviewer rewrite historical checkpoint 缺 attempt links')
    historical.ledger.usage_for_complete_purposes(
        [spec.purpose], sid=historical.sid, node=spec.node,
        required_identities=[(spec.purpose, row) for row in links])
    if (tail.checkpoint_sha256 != latest.get('checkpoint_sha256')
            or tail.input_sha256 != checked.get('input_sha256')):
        raise ProvenanceRefused('reviewer rewrite terminal journal/latest checkpoint 漂移')
    commit = reviewer_rewrite_commit_envelope(
        ctx, run_id=first.run_id, chapter=first.output_ordinal)
    if tail.event == 'landed_formal':
        if (commit is None or commit.get('state') != 'committed'
                or commit.get('commit_sha256') != tail.commit_sha256
                or commit.get('attempt_id') != latest.get('attempt_id')):
            raise ProvenanceRefused('reviewer rewrite formal terminal commit 未闭合')
    elif tail.event in {'landed_invalid', 'landed_rejected'} and commit is not None:
        raise ProvenanceRefused('reviewer rewrite 负终态不得混入 generic commit')
    return HistoricalReviewerRewriteTerminal(
        run_record=checked, run_sha256=run_sha,
        checkpoint=dict(latest), commit=commit)


def _unique_reviewer_history_file(
        ctx: Any, *, run_id: str, chapter: int, filename: str,
) -> tuple[Path, str]:
    """在 service/legacy roots 中按原字节唯一定位一份 source sidecar。"""
    source = check_run_id(run_id)
    dirname = f'ch{_chapter(chapter):04d}'
    candidates = [
        root / source / dirname / filename
        for root in runtime_layout.stage_run_roots(ctx.novel_dir, 'stage5')
        if (root / source / dirname / filename).is_file()
    ]
    if not candidates:
        raise ProvenanceRefused(
            f'reviewer rewrite source {source}/{dirname}/{filename} 不存在')
    try:
        values = {candidate.read_bytes() for candidate in candidates}
    except OSError as exc:
        raise ArtifactUnreadable(
            f'reviewer rewrite source {filename} 不可读：{exc}') from exc
    if len(values) != 1:
        raise ArtifactUnreadable(
            f'reviewer rewrite source {filename} 在 service/legacy 字节冲突')
    raw = next(iter(values))
    return candidates[0], agent_runs.canonical_sha256(raw)


def reviewer_rewrite_source_adoption_id(
        *, sid: str, source_run_id: str, chapter: int,
        request_sha256: str, source_run_identity_sha256: str,
        source_run_file_sha256: str, source_checkpoint_sha256: str,
        source_checkpoint_file_sha256: str,
        source_commit_sha256: str | None,
        source_commit_file_sha256: str | None,
        source_response_sha256: str,
) -> str:
    """与 target run 无关的 exact reviewer checkpoint 采用身份。"""
    return agent_runs.canonical_sha256({
        'sid': sid,
        'node': 'stage5.write',
        'source_run_id': check_run_id(source_run_id),
        'chapter': _chapter(chapter),
        'request_sha256': request_sha256,
        'source_run_identity_sha256': source_run_identity_sha256,
        'source_run_file_sha256': source_run_file_sha256,
        'source_checkpoint_sha256': source_checkpoint_sha256,
        'source_checkpoint_file_sha256': source_checkpoint_file_sha256,
        'source_commit_sha256': source_commit_sha256,
        'source_commit_file_sha256': source_commit_file_sha256,
        'source_response_sha256': source_response_sha256,
    })


def reviewer_rewrite_source_file_sha256(
        ctx: Any, *, run_id: str, chapter: int, filename: str,
) -> str:
    """公开 source sidecar 原字节 SHA；仍复用 service/legacy 唯一性闸。"""
    _path, digest = _unique_reviewer_history_file(
        ctx, run_id=run_id, chapter=chapter, filename=filename)
    return digest


def _reviewer_recovery_path(
        novel_dir: Path, run_id: str, chapter: int,
) -> Path:
    return (paths.stage5_chapter_run_path(
        novel_dir, check_run_id(run_id), _chapter(chapter))
        / _REVIEWER_RECOVERY_FILE)


def _validated_reviewer_recovery_journal(
        path: Path, *, sid: str,
) -> ReviewerRewriteRecoveryJournal:
    journal = _strict_typed_json(
        path, ReviewerRewriteRecoveryJournal,
        label='Stage5 reviewer rewrite recovery', root=path.parents[2])
    dirname = f'ch{journal.output_ordinal:04d}'
    if (path.name != _REVIEWER_RECOVERY_FILE
            or path.parent.name != dirname
            or path.parent.parent.name != journal.target_run_id
            or journal.sid != sid or journal.node != 'stage5.write'):
        raise ArtifactUnreadable(
            f'{path} reviewer rewrite recovery 物理路径/主体漂移')
    return journal


def reviewer_rewrite_recovery_journals(
        ctx: Any, *, chapter: int, request_sha256: str,
        request_file_sha256: str,
) -> tuple[ReviewerRewriteRecoveryJournal, ...]:
    """枚举并严格验证同一 request 的全部 target recovery WAL。"""
    ordinal = _chapter(chapter)
    dirname = f'ch{ordinal:04d}'
    found: dict[str, tuple[bytes, Path]] = {}
    for root in runtime_layout.stage_run_roots(ctx.novel_dir, 'stage5'):
        if not root.exists():
            continue
        _require_plain_contained_path(
            root, root, label='Stage5 run read root', leaf='dir')
        for path in root.glob(f'*/{dirname}/{_REVIEWER_RECOVERY_FILE}'):
            _require_plain_contained_path(
                path, root, label='Stage5 reviewer rewrite recovery',
                leaf='file')
            try:
                raw = path.read_bytes()
            except OSError as exc:
                raise ArtifactUnreadable(
                    f'{path} reviewer rewrite recovery 不可读：{exc}') from exc
            run_id = path.parent.parent.name
            prior = found.get(run_id)
            if prior is not None and prior[0] != raw:
                raise ArtifactUnreadable(
                    'service/legacy reviewer rewrite recovery 同 run 字节冲突')
            found.setdefault(run_id, (raw, path))
    matches: list[ReviewerRewriteRecoveryJournal] = []
    for run_id in sorted(found):
        journal = _validated_reviewer_recovery_journal(
            found[run_id][1], sid=ctx.sid)
        if journal.request_sha256 == request_sha256:
            if journal.request_file_sha256 != request_file_sha256:
                raise ProvenanceRefused(
                    '同一 reviewer rewrite self SHA 出现不同 recovery request 原字节')
            matches.append(journal)
    return tuple(matches)


def reviewer_rewrite_recovery_state(
        ctx: Any, *, run_id: str, chapter: int,
) -> ReviewerRewriteRecoveryJournal | None:
    target = _reviewer_recovery_path(
        Path(ctx.novel_dir), run_id, chapter)
    service_root = paths.service_stage_runs_dir(ctx.novel_dir, 'stage5')
    _require_plain_contained_path(
        target, service_root, label='Stage5 reviewer rewrite recovery target',
        leaf='file', allow_missing=True)
    runtime_layout.stage_run_roots(ctx.novel_dir, 'stage5')
    if not target.exists():
        return None
    journal = _validated_reviewer_recovery_journal(target, sid=ctx.sid)
    if journal.target_run_id != check_run_id(run_id):
        raise ProvenanceRefused('reviewer rewrite recovery journal target run 漂移')
    return journal


def require_reviewer_rewrite_recovery_identity(
        ctx: Any, *, target_run_id: str, chapter: int,
        journal_sha256: str,
) -> ReviewerRewriteRecoveryJournal:
    """按 target run + WAL self SHA 读取唯一 prepared/committed 原子状态。"""
    if not agent_runs.is_sha256(journal_sha256):
        raise ProvenanceRefused('reviewer rewrite recovery journal SHA 形状非法')
    journal = reviewer_rewrite_recovery_state(
        ctx, run_id=check_run_id(target_run_id), chapter=_chapter(chapter))
    if journal is None or journal.journal_sha256 != journal_sha256:
        raise ProvenanceRefused(
            'reviewer rewrite recovery target run/journal SHA 不闭合')
    return journal


def _reviewer_disposition_path(
        novel_dir: Path, request_sha256: str,
) -> Path:
    if not agent_runs.is_sha256(request_sha256):
        raise ProvenanceRefused('reviewer rewrite disposition request SHA 形状非法')
    return (paths.service_stage_runs_dir(novel_dir, 'stage5')
            / _REVIEWER_DISPOSITION_DIR
            / f'request-{request_sha256}.json')


def _reviewer_successor_path(
        novel_dir: Path, request_sha256: str,
) -> Path:
    if not agent_runs.is_sha256(request_sha256):
        raise ProvenanceRefused('reviewer rewrite successor request SHA 形状非法')
    return (paths.service_stage_runs_dir(novel_dir, 'stage5')
            / _REVIEWER_SUCCESSOR_DIR
            / f'request-{request_sha256}.json')


def reviewer_rewrite_successor_journal(
        ctx: Any, *, request_sha256: str,
) -> ReviewerRewriteSuccessorJournal | None:
    target = _reviewer_successor_path(Path(ctx.novel_dir), request_sha256)
    root = paths.service_stage_runs_dir(ctx.novel_dir, 'stage5')
    _require_plain_contained_path(
        target, root, label='Stage5 reviewer rewrite successor WAL',
        leaf='file', allow_missing=True)
    if not target.exists():
        return None
    row = _strict_typed_json(
        target, ReviewerRewriteSuccessorJournal,
        label='Stage5 reviewer rewrite successor WAL', root=root)
    if (target.name != f'request-{row.request_sha256}.json'
            or row.sid != ctx.sid or row.request_sha256 != request_sha256):
        raise ArtifactUnreadable(
            f'{target} reviewer rewrite successor WAL 物理路径/主体漂移')
    return row


def prepare_reviewer_rewrite_successor(
        ctx: Any, *, request_rel: str, request_file_sha256: str,
        request_sha256: str, reviewer_action_record_sha256: str,
        target_ordinal: int, terminal_event: str, terminal_rel: str,
        terminal_file_sha256: str, terminal_payload: Mapping[str, Any],
        successor_kind: str, successor_rel: str,
        successor_file_sha256: str, successor_payload: Mapping[str, Any],
        action_evidence: Mapping[str, Any], touched_paths: Sequence[str],
) -> ReviewerRewriteSuccessorJournal:
    """任何 0-wire successor mutation 前冻结 terminal/raw/action/expected target。"""
    if ctx.node != 'stage5.write' or ctx.paid or ctx.executor_profile is not None:
        raise ProvenanceRefused(
            'reviewer rewrite successor WAL 只属于无 executor 的 free stage5.write')
    _require_reviewer_execution_ownership(Path(ctx.novel_dir), request_sha256)
    ordinal = _chapter(target_ordinal)
    _require_ownership(ctx, ordinal)
    touched = tuple(sorted(set(touched_paths)))
    if not touched or successor_rel not in touched or terminal_rel not in touched:
        raise ProvenanceRefused(
            'reviewer rewrite successor WAL 必须覆盖 terminal 与 successor live 路径')
    terminal = dict(terminal_payload)
    successor = dict(successor_payload)
    evidence = dict(action_evidence)
    raw: dict[str, Any] = {
        'version': 1,
        'kind': 'stage5-reviewer-rewrite-successor-v1',
        'state': 'prepared',
        'prepared_at': _now(),
        'committed_at': None,
        'sid': ctx.sid,
        'node': 'stage5.write',
        'prepare_run_id': check_run_id(ctx.run_id),
        'prepare_parent_run_id': check_run_id(ctx.parent_run_id),
        'prepare_code': ctx.code,
        'commit_run_id': None,
        'commit_parent_run_id': None,
        'commit_code': None,
        'request_rel': request_rel,
        'request_file_sha256': request_file_sha256,
        'request_sha256': request_sha256,
        'reviewer_action_record_sha256': reviewer_action_record_sha256,
        'target_ordinal': ordinal,
        'terminal_event': terminal_event,
        'terminal_rel': terminal_rel,
        'terminal_file_sha256': terminal_file_sha256,
        'terminal_payload': terminal,
        'terminal_payload_sha256': agent_runs.canonical_sha256(terminal),
        'successor_kind': successor_kind,
        'successor_rel': successor_rel,
        'successor_file_sha256': successor_file_sha256,
        'successor_payload': successor,
        'successor_payload_sha256': agent_runs.canonical_sha256(successor),
        'action_evidence': evidence,
        'action_evidence_sha256': agent_runs.canonical_sha256(evidence),
        'touched_paths': touched,
        'baselines': {rel: agent_runs.file_sha256(ctx, rel) for rel in touched},
        'post_file_sha256s': None,
        'journal_sha256': '0' * 64,
    }
    raw['journal_sha256'] = reviewer_rewrite_successor_journal_sha256(raw)
    proposed = ReviewerRewriteSuccessorJournal.model_validate(raw)
    target = _reviewer_successor_path(Path(ctx.novel_dir), request_sha256)
    _require_plain_contained_path(
        target, paths.service_stage_runs_dir(ctx.novel_dir, 'stage5'),
        label='Stage5 reviewer rewrite successor WAL target', leaf='file',
        allow_missing=True)
    with process_append_lock(target):
        if target.exists():
            current = reviewer_rewrite_successor_journal(
                ctx, request_sha256=request_sha256)
            assert current is not None
            ignored = {
                'prepared_at', 'prepare_run_id', 'prepare_parent_run_id',
                'prepare_code', 'journal_sha256'}
            if ({key: value for key, value in current.model_dump(mode='json').items()
                 if key not in ignored}
                    != {key: value for key, value in proposed.model_dump(mode='json').items()
                        if key not in ignored}):
                raise ProvenanceRefused(
                    'reviewer rewrite successor WAL 已绑定另一 terminal/action/target')
            return current
        agent_runs._atomic_json(target, proposed.model_dump(mode='json'))
    return proposed


def mark_reviewer_rewrite_successor_committed(
        ctx: Any, *, journal: ReviewerRewriteSuccessorJournal,
) -> ReviewerRewriteSuccessorJournal:
    _require_reviewer_execution_ownership(
        Path(ctx.novel_dir), journal.request_sha256)
    _require_ownership(ctx, journal.target_ordinal)
    target = _reviewer_successor_path(
        Path(ctx.novel_dir), journal.request_sha256)
    _require_plain_contained_path(
        target, paths.service_stage_runs_dir(ctx.novel_dir, 'stage5'),
        label='Stage5 reviewer rewrite successor WAL target', leaf='file',
        allow_missing=True)
    with process_append_lock(target):
        current = reviewer_rewrite_successor_journal(
            ctx, request_sha256=journal.request_sha256)
        if current != journal:
            raise ProvenanceRefused('reviewer rewrite successor committed 前 WAL 漂移')
        post = {
            rel: agent_runs.file_sha256(ctx, rel) for rel in current.touched_paths}
        if post.get(current.successor_rel) != current.successor_file_sha256:
            raise ProvenanceRefused(
                'reviewer rewrite successor committed 前 exact target 尚未落稳')
        if current.state == 'committed':
            return current
        raw = current.model_dump(mode='json')
        raw.update(
            state='committed', committed_at=_now(),
            commit_run_id=check_run_id(ctx.run_id),
            commit_parent_run_id=check_run_id(ctx.parent_run_id),
            commit_code=ctx.code, post_file_sha256s=post,
            journal_sha256='0' * 64)
        raw['journal_sha256'] = reviewer_rewrite_successor_journal_sha256(raw)
        committed = ReviewerRewriteSuccessorJournal.model_validate(raw)
        agent_runs._atomic_json(target, committed.model_dump(mode='json'))
    return committed


def reviewer_rewrite_disposition(
        ctx: Any, *, request_sha256: str,
) -> ReviewerRewriteDisposition | None:
    """读取 immutable retirement/successor receipt；缺失与坏件严格分开。"""
    target = _reviewer_disposition_path(Path(ctx.novel_dir), request_sha256)
    _require_plain_contained_path(
        target, paths.service_stage_runs_dir(ctx.novel_dir, 'stage5'),
        label='Stage5 reviewer rewrite disposition target', leaf='file',
        allow_missing=True)
    root = paths.service_stage_runs_dir(ctx.novel_dir, 'stage5')
    _require_plain_contained_path(
        target, root, label='Stage5 reviewer rewrite disposition',
        leaf='file', allow_missing=True)
    if not target.exists():
        return None
    row = _strict_typed_json(
        target, ReviewerRewriteDisposition,
        label='Stage5 reviewer rewrite disposition', root=root)
    if (target.name != f'request-{row.request_sha256}.json'
            or row.sid != ctx.sid
            or row.request_sha256 != request_sha256):
        raise ArtifactUnreadable(
            f'{target} reviewer rewrite disposition 物理路径/主体漂移')
    return row


def write_reviewer_rewrite_disposition(
        ctx: Any, *, request_rel: str, request_file_sha256: str,
        request_sha256: str, reviewer_action_record_sha256: str,
        target_ordinal: int, unstarted_evidence: Mapping[str, Any],
) -> ReviewerRewriteDisposition:
    """在书锁内单原子落 expired receipt；canonical request 保留到下次换代。"""
    if ctx.node != 'stage5.write' or ctx.paid or ctx.executor_profile is not None:
        raise ProvenanceRefused(
            'reviewer rewrite disposition 只属于无 executor 的 free stage5.write')
    _require_reviewer_execution_ownership(
        Path(ctx.novel_dir), request_sha256)
    evidence = dict(unstarted_evidence)
    raw: dict[str, Any] = {
        'version': 1,
        'kind': 'stage5-reviewer-rewrite-disposition-v1',
        'disposition': 'expired_unstarted',
        'recorded_at': _now(),
        'sid': ctx.sid,
        'node': 'stage5.write',
        'retire_run_id': check_run_id(ctx.run_id),
        'retire_parent_run_id': check_run_id(ctx.parent_run_id),
        'retire_code': ctx.code,
        'request_rel': request_rel,
        'request_file_sha256': request_file_sha256,
        'request_sha256': request_sha256,
        'reviewer_action_record_sha256': reviewer_action_record_sha256,
        'target_ordinal': _chapter(target_ordinal),
        'unstarted_evidence': evidence,
        'unstarted_evidence_sha256': agent_runs.canonical_sha256(evidence),
        'terminal_event': None,
        'terminal_rel': None,
        'terminal_file_sha256': None,
        'terminal_payload_sha256': None,
        'successor_kind': None,
        'successor_rel': None,
        'successor_file_sha256': None,
        'successor_payload_sha256': None,
        'successor_evidence': None,
        'successor_evidence_sha256': None,
        'disposition_sha256': '0' * 64,
    }
    raw['disposition_sha256'] = reviewer_rewrite_disposition_sha256(raw)
    proposed = ReviewerRewriteDisposition.model_validate(raw)
    target = _reviewer_disposition_path(Path(ctx.novel_dir), request_sha256)
    _require_plain_contained_path(
        target, paths.service_stage_runs_dir(ctx.novel_dir, 'stage5'),
        label='Stage5 reviewer rewrite disposition target', leaf='file',
        allow_missing=True)
    with process_append_lock(target):
        if target.exists():
            current = reviewer_rewrite_disposition(
                ctx, request_sha256=request_sha256)
            if current != proposed:
                # recorded_at/run 是本次审计身份；同 request 的第二次退休不是幂等重入。
                raise ProvenanceRefused(
                    'reviewer rewrite disposition 已由另一 retire run 落盘')
            return current
        agent_runs._atomic_json(target, proposed.model_dump(mode='json'))
    landed = reviewer_rewrite_disposition(ctx, request_sha256=request_sha256)
    if landed != proposed:
        raise ProvenanceRefused('reviewer rewrite disposition 写后 readback 漂移')
    return landed


def write_reviewer_rewrite_successor_disposition(
        ctx: Any, *, request_rel: str, request_file_sha256: str,
        request_sha256: str, reviewer_action_record_sha256: str,
        target_ordinal: int, terminal_event: str, terminal_rel: str,
        terminal_file_sha256: str, terminal_payload_sha256: str,
        successor_kind: str, successor_rel: str,
        successor_file_sha256: str, successor_payload_sha256: str,
        successor_evidence: Mapping[str, Any],
) -> ReviewerRewriteDisposition:
    """在合法 0-wire 后继完整落稳后写 request-specific consumption receipt。"""
    if ctx.node != 'stage5.write' or ctx.paid or ctx.executor_profile is not None:
        raise ProvenanceRefused(
            'reviewer rewrite successor receipt 只属于无 executor 的 free stage5.write')
    _require_reviewer_execution_ownership(
        Path(ctx.novel_dir), request_sha256)
    evidence = dict(successor_evidence)
    raw: dict[str, Any] = {
        'version': 1,
        'kind': 'stage5-reviewer-rewrite-disposition-v1',
        'disposition': 'consumed_resolved',
        'recorded_at': _now(),
        'sid': ctx.sid,
        'node': 'stage5.write',
        'retire_run_id': check_run_id(ctx.run_id),
        'retire_parent_run_id': check_run_id(ctx.parent_run_id),
        'retire_code': ctx.code,
        'request_rel': request_rel,
        'request_file_sha256': request_file_sha256,
        'request_sha256': request_sha256,
        'reviewer_action_record_sha256': reviewer_action_record_sha256,
        'target_ordinal': _chapter(target_ordinal),
        'unstarted_evidence': None,
        'unstarted_evidence_sha256': None,
        'terminal_event': terminal_event,
        'terminal_rel': terminal_rel,
        'terminal_file_sha256': terminal_file_sha256,
        'terminal_payload_sha256': terminal_payload_sha256,
        'successor_kind': successor_kind,
        'successor_rel': successor_rel,
        'successor_file_sha256': successor_file_sha256,
        'successor_payload_sha256': successor_payload_sha256,
        'successor_evidence': evidence,
        'successor_evidence_sha256': agent_runs.canonical_sha256(evidence),
        'disposition_sha256': '0' * 64,
    }
    raw['disposition_sha256'] = reviewer_rewrite_disposition_sha256(raw)
    proposed = ReviewerRewriteDisposition.model_validate(raw)
    target = _reviewer_disposition_path(Path(ctx.novel_dir), request_sha256)
    with process_append_lock(target):
        if target.exists():
            current = reviewer_rewrite_disposition(
                ctx, request_sha256=request_sha256)
            assert current is not None
            immutable = {
                key for key in proposed.model_fields
                if key not in {
                    'recorded_at', 'retire_run_id', 'retire_parent_run_id',
                    'retire_code', 'disposition_sha256'}
            }
            if current.model_dump(include=immutable, mode='json') \
                    != proposed.model_dump(include=immutable, mode='json'):
                raise ProvenanceRefused(
                    'reviewer rewrite request 已由另一 disposition/后继占用')
            return current
        agent_runs._atomic_json(target, proposed.model_dump(mode='json'))
    landed = reviewer_rewrite_disposition(ctx, request_sha256=request_sha256)
    if landed != proposed:
        raise ProvenanceRefused('reviewer rewrite successor receipt 写后 readback 漂移')
    return landed


def _require_reviewer_recovery_source(
        ctx: Any, journal: ReviewerRewriteRecoveryJournal,
) -> None:
    """复验 WAL 所绑 source run/checkpoint/optional commit 的原字节与 self SHA。"""
    _run_path, run_file_sha = _unique_reviewer_history_file(
        ctx, run_id=journal.source_run_id, chapter=journal.output_ordinal,
        filename=agent_runs.RUN_FILE)
    if run_file_sha != journal.source_run_file_sha256:
        raise ProvenanceRefused('reviewer rewrite recovery source run 原字节漂移')
    checkpoint_path = reviewer_rewrite_checkpoint_file(
        ctx, run_id=journal.source_run_id, chapter=journal.output_ordinal,
        checkpoint_sha256=journal.source_checkpoint_sha256)
    if _file_sha256(checkpoint_path) != journal.source_checkpoint_file_sha256:
        raise ProvenanceRefused('reviewer rewrite recovery source checkpoint 原字节漂移')
    commit = reviewer_rewrite_commit_envelope(
        ctx, run_id=journal.source_run_id, chapter=journal.output_ordinal)
    if journal.source_commit_sha256 is None:
        if commit is not None:
            raise ProvenanceRefused(
                'reviewer rewrite recovery source commit 在 prepare 后凭空出现')
    else:
        if (commit is None
                or commit.get('commit_sha256') != journal.source_commit_sha256):
            raise ProvenanceRefused('reviewer rewrite recovery source commit self SHA 漂移')
        commit_path, commit_file_sha = _unique_reviewer_history_file(
            ctx, run_id=journal.source_run_id, chapter=journal.output_ordinal,
            filename=agent_runs.COMMIT_FILE)
        del commit_path
        if commit_file_sha != journal.source_commit_file_sha256:
            raise ProvenanceRefused('reviewer rewrite recovery source commit 原字节漂移')


def verify_reviewer_rewrite_recovery_source(
        ctx: Any, journal: ReviewerRewriteRecoveryJournal,
) -> None:
    """供 queue/producer 在不持 target 写锁时做 immutable source 只读复验。"""
    _require_reviewer_recovery_source(ctx, journal)


def prepare_reviewer_rewrite_recovery(
        ctx: Any, *, source: HistoricalReviewerRewrite,
        request_rel: str, request_file_sha256: str, request_sha256: str,
        reviewer_action_record_sha256: str, business_sha256: str,
        reviewer_contract_sha256: str, input_sha256: str,
        delivery_outcome: str, delivery_rel: str,
        delivery_payload: Mapping[str, Any],
        finalization_payload: Mapping[str, Any],
) -> ReviewerRewriteRecoveryJournal:
    """在任何 target live 写入前冻结 source、authority、baseline 与目标 payload。"""
    ordinal = _chapter(source.historical.run_record.get('chapter'))
    _require_ownership(ctx, ordinal)
    if (ctx.node != 'stage5.write' or ctx.paid
            or ctx.executor_profile is not None):
        raise ProvenanceRefused(
            'reviewer rewrite recovery target 必须是无 executor 的 free stage5.write')
    source_run_id = check_run_id(source.historical.context.run_id)
    if source_run_id == check_run_id(ctx.run_id):
        raise ProvenanceRefused('reviewer rewrite recovery 必须跨 run')
    acceptance = source.historical.checkpoint.acceptance
    run_record = source.historical.run_record
    checkpoint = source.historical.checkpoint
    checkpoint_path = reviewer_rewrite_checkpoint_file(
        ctx, run_id=source_run_id, chapter=ordinal,
        checkpoint_sha256=checkpoint.checkpoint_sha256)
    checkpoint_file_sha = _file_sha256(checkpoint_path)
    source_commit_sha: str | None = None
    source_commit_file_sha: str | None = None
    if source.commit is not None:
        if source.commit.get('state') != 'prepared':
            raise ProvenanceRefused(
                'reviewer rewrite recovery 只接受 absent/prepared source commit')
        source_commit_sha = str(source.commit['commit_sha256'])
        _commit_path, source_commit_file_sha = _unique_reviewer_history_file(
            ctx, run_id=source_run_id, chapter=ordinal,
            filename=agent_runs.COMMIT_FILE)
    source_response_sha = str(acceptance['ledger_response_sha256'])
    source_adoption_id = reviewer_rewrite_source_adoption_id(
        sid=ctx.sid, source_run_id=source_run_id, chapter=ordinal,
        request_sha256=request_sha256,
        source_run_identity_sha256=str(run_record['identity_sha256']),
        source_run_file_sha256=source.historical.run_sha256,
        source_checkpoint_sha256=checkpoint.checkpoint_sha256,
        source_checkpoint_file_sha256=checkpoint_file_sha,
        source_commit_sha256=source_commit_sha,
        source_commit_file_sha256=source_commit_file_sha,
        source_response_sha256=source_response_sha,
    )
    matches = reviewer_rewrite_recovery_journals(
        ctx, chapter=ordinal, request_sha256=request_sha256,
        request_file_sha256=request_file_sha256)
    foreign = [row for row in matches if row.target_run_id != ctx.run_id]
    if foreign:
        raise ProvenanceRefused(
            'reviewer rewrite checkpoint 已由另一 target recovery run 持有')
    local = [row for row in matches if row.target_run_id == ctx.run_id]
    if len(local) > 1:
        raise ProvenanceRefused('当前 target 有多份 reviewer rewrite recovery WAL')
    touched = tuple(sorted(touched_paths(ordinal)))
    payload = dict(delivery_payload)
    finalization = dict(finalization_payload)
    payload_sha256 = agent_runs.canonical_sha256(payload)
    payload_fingerprint = prov.projection_fingerprint(payload)
    finalization_sha256 = agent_runs.canonical_sha256(finalization)
    if local:
        current = local[0]
        expected = {
            'sid': ctx.sid,
            'source_run_id': source_run_id,
            'target_run_id': ctx.run_id,
            'source_code': str(run_record['code']),
            'target_code': ctx.code,
            'output_ordinal': ordinal,
            'request_rel': request_rel,
            'request_file_sha256': request_file_sha256,
            'request_sha256': request_sha256,
            'reviewer_action_record_sha256': reviewer_action_record_sha256,
            'business_sha256': business_sha256,
            'reviewer_contract_sha256': reviewer_contract_sha256,
            'input_sha256': input_sha256,
            'source_run_identity_sha256': str(run_record['identity_sha256']),
            'source_run_file_sha256': source.historical.run_sha256,
            'source_checkpoint_sha256': checkpoint.checkpoint_sha256,
            'source_checkpoint_file_sha256': checkpoint_file_sha,
            'source_checkpoint_revision': checkpoint.revision,
            'source_commit_sha256': source_commit_sha,
            'source_commit_file_sha256': source_commit_file_sha,
            'source_attempt_id': str(acceptance['attempt_id']),
            'source_response_sha256': source_response_sha,
            'source_request_fingerprint': str(acceptance['request_fingerprint']),
            'source_session_run_id': str(acceptance['session_run_id']),
            'source_provider_session_id': str(acceptance['provider_session_id']),
            'source_adoption_id': source_adoption_id,
            'delivery_outcome': delivery_outcome,
            'delivery_rel': delivery_rel,
            'delivery_payload': payload,
            'delivery_payload_sha256': payload_sha256,
            'delivery_payload_fingerprint': payload_fingerprint,
            'finalization_payload': finalization,
            'finalization_sha256': finalization_sha256,
            'touched_paths': touched,
        }
        if any(getattr(current, key) != value for key, value in expected.items()):
            raise ProvenanceRefused(
                '既有 reviewer rewrite recovery WAL 与 source/request/目标漂移')
        _require_reviewer_recovery_source(ctx, current)
        return current
    baselines = {rel: agent_runs.file_sha256(ctx, rel) for rel in touched}
    raw: dict[str, Any] = {
        'version': 1,
        'kind': 'stage5-reviewer-rewrite-recovery-v1',
        'state': 'prepared',
        'prepared_at': _now(),
        'committed_at': None,
        'sid': ctx.sid,
        'node': 'stage5.write',
        'source_run_id': source_run_id,
        'target_run_id': ctx.run_id,
        'source_code': str(run_record['code']),
        'target_code': ctx.code,
        'output_ordinal': ordinal,
        'request_rel': request_rel,
        'request_file_sha256': request_file_sha256,
        'request_sha256': request_sha256,
        'reviewer_action_record_sha256': reviewer_action_record_sha256,
        'business_sha256': business_sha256,
        'reviewer_contract_sha256': reviewer_contract_sha256,
        'input_sha256': input_sha256,
        'source_run_identity_sha256': str(run_record['identity_sha256']),
        'source_run_file_sha256': source.historical.run_sha256,
        'source_checkpoint_sha256': checkpoint.checkpoint_sha256,
        'source_checkpoint_file_sha256': checkpoint_file_sha,
        'source_checkpoint_revision': checkpoint.revision,
        'source_commit_sha256': source_commit_sha,
        'source_commit_file_sha256': source_commit_file_sha,
        'source_attempt_id': str(acceptance['attempt_id']),
        'source_response_sha256': source_response_sha,
        'source_request_fingerprint': str(acceptance['request_fingerprint']),
        'source_session_run_id': str(acceptance['session_run_id']),
        'source_provider_session_id': str(acceptance['provider_session_id']),
        'source_adoption_id': source_adoption_id,
        'delivery_outcome': delivery_outcome,
        'delivery_rel': delivery_rel,
        'delivery_payload': payload,
        'delivery_payload_sha256': payload_sha256,
        'delivery_payload_fingerprint': payload_fingerprint,
        'finalization_payload': finalization,
        'finalization_sha256': finalization_sha256,
        'touched_paths': touched,
        'baselines': baselines,
        'landed_file_sha256': None,
        'post_file_sha256s': None,
        'journal_sha256': '0' * 64,
    }
    raw['journal_sha256'] = reviewer_rewrite_recovery_journal_sha256(raw)
    proposed = ReviewerRewriteRecoveryJournal.model_validate(raw)
    target = _reviewer_recovery_path(
        Path(ctx.novel_dir), ctx.run_id, ordinal)
    _require_plain_contained_path(
        target, paths.service_stage_runs_dir(ctx.novel_dir, 'stage5'),
        label='Stage5 reviewer rewrite recovery target', leaf='file',
        allow_missing=True)
    with process_append_lock(target):
        if target.is_file():
            current = _validated_reviewer_recovery_journal(
                target, sid=ctx.sid)
            ignored = {
                'state', 'prepared_at', 'committed_at', 'journal_sha256',
                'landed_file_sha256', 'post_file_sha256s',
            }
            if ({k: v for k, v in current.model_dump(mode='json').items()
                 if k not in ignored}
                    != {k: v for k, v in proposed.model_dump(mode='json').items()
                        if k not in ignored}):
                raise ProvenanceRefused(
                    'target run reviewer rewrite recovery WAL 已绑定另一主体/目标')
            return current
        agent_runs._atomic_json(target, proposed.model_dump(mode='json'))
    return proposed


def reviewer_rewrite_recovery_target_state(
        ctx: Any, journal: ReviewerRewriteRecoveryJournal, rel: str, *,
        expected_file_sha256: str | None = None,
        expected_absent: bool = False,
) -> str:
    _require_ownership(ctx, journal.output_ordinal)
    current = reviewer_rewrite_recovery_state(
        ctx, run_id=journal.target_run_id, chapter=journal.output_ordinal)
    if current != journal or rel not in journal.baselines:
        raise ProvenanceRefused('reviewer rewrite recovery WAL/target 身份漂移')
    if expected_absent and expected_file_sha256 is not None:
        raise ProvenanceRefused('reviewer rewrite recovery target 不能同时 expected/absent')
    actual = agent_runs.file_sha256(ctx, rel)
    if expected_absent and actual is None:
        return 'expected'
    if expected_file_sha256 is not None and actual == expected_file_sha256:
        return 'expected'
    if actual == journal.baselines[rel]:
        return 'baseline'
    raise ProvenanceRefused(
        f'{rel} 在 reviewer rewrite recovery 尾窗既非 baseline 也非目标')


def verify_reviewer_rewrite_recovery_payload(
        journal: ReviewerRewriteRecoveryJournal,
        *, delivery_payload: Mapping[str, Any],
        finalization_payload: Mapping[str, Any],
) -> None:
    payload = dict(delivery_payload)
    finalization = dict(finalization_payload)
    if (journal.delivery_payload != payload
            or journal.finalization_payload != finalization
            or journal.delivery_payload_sha256
            != agent_runs.canonical_sha256(payload)
            or journal.delivery_payload_fingerprint
            != prov.projection_fingerprint(payload)
            or journal.finalization_sha256
            != agent_runs.canonical_sha256(finalization)):
        raise ProvenanceRefused(
            'reviewer rewrite recovery WAL 与目标 payload/finalization 漂移')


def mark_reviewer_rewrite_recovery_committed(
        ctx: Any, *, journal: ReviewerRewriteRecoveryJournal,
        delivery_payload: Mapping[str, Any],
        finalization_payload: Mapping[str, Any],
) -> ReviewerRewriteRecoveryJournal:
    """typed delivery 完成后把 prepared WAL 原子推进 committed。"""
    ordinal = journal.output_ordinal
    _require_ownership(ctx, ordinal)
    verify_reviewer_rewrite_recovery_payload(
        journal, delivery_payload=delivery_payload,
        finalization_payload=finalization_payload)
    _require_reviewer_recovery_source(ctx, journal)
    if agent_runs.file_sha256(ctx, journal.request_rel) != journal.request_file_sha256:
        raise ProvenanceRefused('reviewer rewrite recovery request 原字节漂移')
    target = _reviewer_recovery_path(
        Path(ctx.novel_dir), journal.target_run_id, ordinal)
    _require_plain_contained_path(
        target, paths.service_stage_runs_dir(ctx.novel_dir, 'stage5'),
        label='Stage5 reviewer rewrite recovery target', leaf='file')
    with process_append_lock(target):
        current = _validated_reviewer_recovery_journal(target, sid=ctx.sid)
        if current != journal:
            raise ProvenanceRefused('reviewer rewrite recovery committed 前 WAL 漂移')
        post = {
            rel: agent_runs.file_sha256(ctx, rel)
            for rel in current.touched_paths
        }
        landed_sha = post[current.delivery_rel]
        if landed_sha is None:
            raise ProvenanceRefused('reviewer rewrite recovery committed 前 delivery 缺失')
        if current.state == 'committed':
            return current
        raw = current.model_dump(mode='json')
        raw.update(
            state='committed', committed_at=_now(),
            landed_file_sha256=landed_sha,
            post_file_sha256s=post,
            journal_sha256='0' * 64,
        )
        raw['journal_sha256'] = reviewer_rewrite_recovery_journal_sha256(raw)
        committed = ReviewerRewriteRecoveryJournal.model_validate(raw)
        agent_runs._atomic_json(target, committed.model_dump(mode='json'))
    return committed


def verify_reviewer_rewrite_recovery_committed(
        ctx: Any, *, journal: ReviewerRewriteRecoveryJournal,
        delivery_payload: Mapping[str, Any],
        finalization_payload: Mapping[str, Any],
) -> None:
    verify_reviewer_rewrite_recovery_payload(
        journal, delivery_payload=delivery_payload,
        finalization_payload=finalization_payload)
    current = reviewer_rewrite_recovery_state(
        ctx, run_id=journal.target_run_id, chapter=journal.output_ordinal)
    if current != journal or journal.state != 'committed':
        raise ProvenanceRefused('reviewer rewrite recovery committed WAL 漂移')
    _require_reviewer_recovery_source(ctx, journal)
    post = {
        rel: agent_runs.file_sha256(ctx, rel)
        for rel in journal.touched_paths
    }
    if (post != journal.post_file_sha256s
            or post.get(journal.delivery_rel) != journal.landed_file_sha256):
        raise ProvenanceRefused('reviewer rewrite recovery committed live bytes 漂移')


def workpack_subject(payload: Mapping[str, Any]) -> str:
    """运行时只代理契约层算法，不保留第二份 subject 尺子。"""
    return prepared_recovery_workpack_subject(dict(payload))


def require_workpack_subject(workpack: Any) -> None:
    """在 manifest/read/apply 信任边界重算 workpack subject。"""
    payload = workpack.model_dump(mode='json')
    if workpack.subject_sha256 != workpack_subject(payload):
        raise ProvenanceRefused('prepared recovery workpack subject SHA 不闭合')


def manifest_sha256(payload: Mapping[str, Any]) -> str:
    return agent_runs.canonical_sha256({
        key: value for key, value in dict(payload).items()
        if key != 'manifest_sha256'
    })


def _journal_sha256(payload: Mapping[str, Any]) -> str:
    return agent_runs.canonical_sha256({
        key: value for key, value in dict(payload).items()
        if key != 'journal_sha256'
    })


def source_adoption_id(
        *, sid: str, source_run_id: str, chapter: int,
        source_run_identity_sha256: str, source_run_file_sha256: str,
        source_commit_sha256: str, source_commit_file_sha256: str,
        source_checkpoint_sha256: str, source_checkpoint_file_sha256: str,
        source_response_sha256: str,
) -> str:
    """与 target run/actor 无关的 source 唯一采用身份，跨新 run 只能收口一次。"""
    return agent_runs.canonical_sha256({
        'sid': sid, 'node': 'stage5.write',
        'source_run_id': source_run_id, 'chapter': _chapter(chapter),
        'source_run_identity_sha256': source_run_identity_sha256,
        'source_run_file_sha256': source_run_file_sha256,
        'source_commit_sha256': source_commit_sha256,
        'source_commit_file_sha256': source_commit_file_sha256,
        'source_checkpoint_sha256': source_checkpoint_sha256,
        'source_checkpoint_file_sha256': source_checkpoint_file_sha256,
        'source_response_sha256': source_response_sha256,
    })


def _validated_manifest(path: Path) -> PreparedRecoveryManifest:
    manifest = _typed_json(
        path, PreparedRecoveryManifest, label='Stage5 recovery manifest')
    require_workpack_subject(manifest.workpack)
    payload = manifest.model_dump(mode='json')
    if manifest.manifest_sha256 != manifest_sha256(payload):
        raise ArtifactUnreadable(f'{path} recovery manifest SHA 不闭合')
    return manifest


def _recovery_manifest_path(novel_dir: Path, run_id: str) -> Path:
    """target manifest 只位于当前 service 根。"""
    runtime_layout.stage_run_roots(novel_dir, 'stage5')
    return paths.stage5_prepared_recovery_manifest_path(novel_dir, run_id)


def write_recovery_manifest(
        ctx: Any, manifest: PreparedRecoveryManifest,
) -> Path:
    """首次 apply 原子落不可变 manifest；同 target run 只允许逐字幂等。"""
    require_workpack_subject(manifest.workpack)
    target = _recovery_manifest_path(ctx.novel_dir, ctx.run_id)
    if (manifest.workpack.target_run_id != ctx.run_id
            or manifest.workpack.target_code != ctx.code
            or manifest.workpack.sid != ctx.sid):
        raise ProvenanceRefused('prepared recovery manifest 不属于当前 target run/code/sid')
    payload = manifest.model_dump(mode='json')
    if manifest.manifest_sha256 != manifest_sha256(payload):
        raise ProvenanceRefused('prepared recovery manifest SHA 不闭合')
    # manifest 是整书 sidecar，却绑定逐章 stable adoption。按章序同时持有全部
    # ownership，逐章完成全局 audit 0-or-exact preclaim，随后才允许第一笔写盘。
    # 这样旧 target journal 丢失、只剩 append-only audit 时，连新 manifest 都不会出现。
    with ExitStack() as stack:
        for chapter in manifest.workpack.chapters:
            stack.enter_context(commit_ownership(
                ctx.novel_dir, chapter.output_ordinal))
        for chapter in manifest.workpack.chapters:
            preclaim_recovery_adoption(
                ctx, chapter=chapter, action=manifest.action,
                manifest=manifest)
        with process_append_lock(target):
            if target.is_file():
                current = _validated_manifest(target)
                if current != manifest:
                    raise ProvenanceRefused(
                        f'{target} 已绑定另一份 recovery workpack/action；拒绝覆盖')
                return target
            agent_runs._atomic_json(target, payload)
    return target


def read_recovery_manifest(ctx: Any) -> PreparedRecoveryManifest | None:
    target = _recovery_manifest_path(ctx.novel_dir, ctx.run_id)
    if not target.is_file():
        return None
    manifest = _validated_manifest(target)
    if (manifest.workpack.target_run_id != ctx.run_id
            or manifest.workpack.target_code != ctx.code
            or manifest.workpack.sid != ctx.sid):
        raise ProvenanceRefused(f'{target} 不属于当前 target run/code/sid')
    return manifest


def _strict_jsonl_objects(path: Path, *, label: str) -> list[dict[str, Any]]:
    """零调用恢复的污染扫描：末行半截也不能当成「没发生过」。"""
    try:
        with process_append_lock(path):
            if not path.is_file():
                return []
            raw = path.read_bytes()
    except OSError as exc:
        raise ProvenanceRefused(f'{label} 不可验证：{exc}') from exc
    if raw and not raw.endswith(b'\n'):
        raise ProvenanceRefused(
            f'{label} 存在未闭合末行，不能证明 target run 从未 wire/cache/session')

    def reject_constant(value: str) -> None:
        raise ValueError(f'JSON 不接受 {value}')

    def reject_duplicate(pairs: list[tuple[str, object]]) -> dict[str, object]:
        value: dict[str, object] = {}
        for key, item in pairs:
            if key in value:
                raise ValueError(f'JSON object 含重复键 {key!r}')
            value[key] = item
        return value

    rows: list[dict[str, Any]] = []
    for index, encoded in enumerate(raw.split(b'\n')):
        if not encoded.strip():
            continue
        try:
            value = json.loads(
                encoded.decode('utf-8'), parse_constant=reject_constant,
                object_pairs_hook=reject_duplicate)
        except (UnicodeError, ValueError, json.JSONDecodeError) as exc:
            raise ProvenanceRefused(
                f'{label} 第 {index + 1} 行损坏：{exc}') from exc
        if not isinstance(value, dict):
            raise ProvenanceRefused(f'{label} 第 {index + 1} 行不是 JSON object')
        rows.append(value)
    return rows


def require_prepared_recovery_target_clean(
        ctx: Any, *, target_run_id: str | None = None,
) -> None:
    """证明指定 target run 从未留下 wire、cache 或 agent session 持久事实。

    内存计数只挡同进程污染；crash/restart 后还必须按全局 ``run_id`` 扫两份
    append-only 账。agent session 事件没有 sid；target run 是全局身份，因此同一
    base 中任意 ``parent_run_id`` 命中都拒绝，也不能靠错/缺 node 或「当前书没
    sidecar」把既有 session 洗掉。
    """
    if ctx.node != 'stage5.write' or ctx.paid:
        raise ProvenanceRefused('prepared recovery 只属于 free stage5.write run')
    if ctx.executor_profile is not None:
        raise ProvenanceRefused('prepared recovery 禁止 executor profile/driver')
    target_run = check_run_id(target_run_id or ctx.run_id)
    if target_run == ctx.run_id and (ctx.ledger.calls_made or ctx.ledger.calls_cached):
        raise ProvenanceRefused(
            'prepared recovery target run 已出现内存 wire/cache 计数；拒绝冒充 0-wire 恢复')

    base = Path(ctx.novel_dir).parent
    cost_rows = _strict_jsonl_objects(
        ledger_module.ledger_path(base), label='prepared recovery 成本账')
    if any(row.get('run_id') == target_run for row in cost_rows):
        raise ProvenanceRefused(
            'prepared recovery target run 已有任意持久 ledger 行；'
            '拒绝冒充 0-wire 恢复')

    session_rows = _strict_jsonl_objects(
        agent_session_audit.audit_path(base),
        label='prepared recovery agent session 账')
    for index, row in enumerate(session_rows):
        try:
            event = agent_session_audit.AgentSessionEvent.from_dict(row)
        except (TypeError, ValueError) as exc:
            raise ProvenanceRefused(
                f'prepared recovery agent session 账第 {index + 1} 行不合法：{exc}') from exc
        if event.parent_run_id == target_run:
            raise ProvenanceRefused(
                'prepared recovery target run 已有任意持久 agent session 事件；'
                '拒绝冒充 0-wire 恢复')


def _validated_recovery_journal(path: Path) -> PreparedRecoveryJournal:
    journal = _typed_json(
        path, PreparedRecoveryJournal, label='Stage5 recovery journal')
    payload = journal.model_dump(mode='json')
    if journal.journal_sha256 != _journal_sha256(payload):
        raise ArtifactUnreadable(f'{path} recovery journal SHA 不闭合')
    expected_dir = f'ch{journal.output_ordinal:04d}'
    if (path.parent.name != expected_dir
            or path.parent.parent.name != journal.target_run_id):
        raise ArtifactUnreadable(f'{path} recovery journal 路径身份不闭合')
    return journal


def recovery_state(ctx: Any, chapter: int) -> PreparedRecoveryJournal | None:
    ordinal = _chapter(chapter)
    target = paths.stage5_chapter_recovery_journal_path(
        ctx.novel_dir, ctx.run_id, ordinal)
    runtime_layout.stage_run_roots(ctx.novel_dir, 'stage5')
    if not target.is_file():
        return None
    current = _validated_recovery_journal(target)
    if (current.sid != ctx.sid or current.node != 'stage5.write'
            or current.target_run_id != ctx.run_id or current.code != ctx.code):
        raise ProvenanceRefused(f'{target} 不属于当前 target run/sid/node/code')
    return current


def _matching_recovery_journals(
        ctx: Any, *, source_run_id: str, chapter: int,
        source_commit_sha256: str,
) -> list[tuple[Path, PreparedRecoveryJournal]]:
    dirname = f'ch{_chapter(chapter):04d}'
    matches: list[tuple[Path, PreparedRecoveryJournal]] = []
    for target in _history_files(
            ctx, f'*/{dirname}/prepared_recovery.json'):
        journal = _validated_recovery_journal(target)
        if journal.sid != ctx.sid or journal.node != 'stage5.write':
            raise ArtifactUnreadable(f'{target} recovery journal 跨书/节点')
        if (journal.source_run_id == source_run_id
                and journal.output_ordinal == chapter
                and journal.source_commit_sha256 == source_commit_sha256):
            matches.append((target, journal))
    matches.sort(key=lambda item: str(item[0]))
    return matches


def _source_sidecars_match_journal(
        ctx: Any, journal: PreparedRecoveryJournal,
        source_record: Mapping[str, Any],
) -> bool:
    source_dir = paths.stage5_chapter_run_path(
        ctx.novel_dir, journal.source_run_id, journal.output_ordinal)
    run_path = source_dir / agent_runs.RUN_FILE
    commit_path = source_dir / agent_runs.COMMIT_FILE
    # checkpoint revision 不能从章号推；从 run 的 append-only 目录中唯一找 self-hash。
    checkpoint_dir = source_dir / 'checkpoints'
    candidates = sorted(checkpoint_dir.glob('rev-*.json')) if checkpoint_dir.is_dir() else []
    matching_checkpoint: Path | None = None
    for candidate in candidates:
        try:
            raw = json.loads(candidate.read_text(encoding='utf-8'))
        except (OSError, UnicodeError, json.JSONDecodeError) as exc:
            raise ArtifactUnreadable(f'{candidate} source checkpoint 不可读：{exc}') from exc
        if (isinstance(raw, dict)
                and raw.get('checkpoint_sha256') == journal.source_checkpoint_sha256):
            if matching_checkpoint is not None:
                raise ProvenanceRefused('source checkpoint self-hash 在 revision 链中重复')
            matching_checkpoint = candidate
    if matching_checkpoint is None:
        return False
    try:
        run_payload = json.loads(run_path.read_text(encoding='utf-8'))
        checkpoint_payload = json.loads(matching_checkpoint.read_text(encoding='utf-8'))
    except (OSError, UnicodeError, json.JSONDecodeError) as exc:
        raise ArtifactUnreadable(f'Stage5 source sidecar 不可读：{exc}') from exc
    return bool(
        source_record.get('state') == 'prepared'
        and source_record.get('commit_sha256') == journal.source_commit_sha256
        and source_record.get('attempt_id') == journal.source_attempt_id
        and _file_sha256(commit_path) == journal.source_commit_file_sha256
        and isinstance(run_payload, dict)
        and run_payload.get('identity_sha256') == journal.source_run_identity_sha256
        and run_payload.get('code') == journal.source_code
        and _file_sha256(run_path) == journal.source_run_file_sha256
        and isinstance(checkpoint_payload, dict)
        and checkpoint_payload.get('checkpoint_sha256')
        == journal.source_checkpoint_sha256
        and checkpoint_payload.get('attempt_id') == journal.source_attempt_id
        and checkpoint_payload.get('ledger_response_sha256')
        == journal.source_response_sha256
        and _file_sha256(matching_checkpoint)
        == journal.source_checkpoint_file_sha256
        and source_adoption_id(
            sid=journal.sid,
            source_run_id=journal.source_run_id,
            chapter=journal.output_ordinal,
            source_run_identity_sha256=journal.source_run_identity_sha256,
            source_run_file_sha256=journal.source_run_file_sha256,
            source_commit_sha256=journal.source_commit_sha256,
            source_commit_file_sha256=journal.source_commit_file_sha256,
            source_checkpoint_sha256=journal.source_checkpoint_sha256,
            source_checkpoint_file_sha256=journal.source_checkpoint_file_sha256,
            source_response_sha256=journal.source_response_sha256,
        ) == journal.source_adoption_id
    )


def _expected_recovery_adoption(
        journal: PreparedRecoveryJournal,
) -> dict[str, Any]:
    return {
        'source_run_id': journal.source_run_id,
        'target_run_id': journal.target_run_id,
        'source_code': journal.source_code,
        'source_run_identity_sha256': journal.source_run_identity_sha256,
        'source_run_file_sha256': journal.source_run_file_sha256,
        'source_commit_sha256': journal.source_commit_sha256,
        'source_commit_file_sha256': journal.source_commit_file_sha256,
        'source_checkpoint_sha256': journal.source_checkpoint_sha256,
        'source_checkpoint_file_sha256': journal.source_checkpoint_file_sha256,
        'source_attempt_id': journal.source_attempt_id,
        'source_response_sha256': journal.source_response_sha256,
        'source_adoption_id': journal.source_adoption_id,
        'action_subject_sha256': journal.subject_sha256,
        # 普通 prepared recovery 没有 reviewer request，仍须与 typed provenance 的
        # 完整形状相等；不能因新增可选字段把既有合法 adoption 判成失联。
        'reviewer_rewrite_request_sha256': None,
        'actor': journal.actor, 'reason': journal.reason,
    }


def _expected_recovery_audit(
        journal: PreparedRecoveryJournal,
) -> dict[str, object]:
    return ledger_module.prepared_recovery_audit_row(
        target_run_id=journal.target_run_id,
        sid=journal.sid,
        purpose=purpose_of(journal.output_ordinal),
        chapter=journal.output_ordinal,
        source_run_id=journal.source_run_id,
        source_code=journal.source_code,
        source_run_identity_sha256=journal.source_run_identity_sha256,
        source_run_file_sha256=journal.source_run_file_sha256,
        source_commit_sha256=journal.source_commit_sha256,
        source_commit_file_sha256=journal.source_commit_file_sha256,
        source_checkpoint_sha256=journal.source_checkpoint_sha256,
        source_checkpoint_file_sha256=journal.source_checkpoint_file_sha256,
        source_attempt_id=journal.source_attempt_id,
        source_response_sha256=journal.source_response_sha256,
        source_adoption_id=journal.source_adoption_id,
        subject_sha256=journal.subject_sha256,
        actor=journal.actor,
        reason=journal.reason,
        artifact=journal.primary_artifact,
        artifact_fingerprint=journal.primary_payload_fingerprint,
    )


def _chapter_action_closes_journal(
        chapter: PreparedRecoveryChapter,
        action: PreparedRecoveryAction,
        journal: PreparedRecoveryJournal,
) -> bool:
    """workpack 本章/action 中所有被 journal 携带的事实必须逐字段相等。"""
    return bool(
        chapter.output_ordinal == journal.output_ordinal
        and chapter.purpose == purpose_of(journal.output_ordinal)
        and chapter.source_code == journal.source_code
        and chapter.source_run_identity_sha256
        == journal.source_run_identity_sha256
        and chapter.source_run_file_sha256 == journal.source_run_file_sha256
        and chapter.source_commit_sha256 == journal.source_commit_sha256
        and chapter.source_commit_file_sha256
        == journal.source_commit_file_sha256
        and chapter.source_checkpoint_sha256
        == journal.source_checkpoint_sha256
        and chapter.source_checkpoint_file_sha256
        == journal.source_checkpoint_file_sha256
        and chapter.source_attempt_id == journal.source_attempt_id
        and chapter.source_response_sha256 == journal.source_response_sha256
        and chapter.source_adoption_id == journal.source_adoption_id
        and chapter.touched_paths == journal.touched_paths
        and chapter.current_baselines == journal.baselines
        and action.source_run_id == journal.source_run_id
        and action.target_run_id == journal.target_run_id
        and action.subject_sha256 == journal.subject_sha256
        and action.actor == journal.actor
        and action.reason == journal.reason
        and journal.primary_artifact
        == artifact_rel('stage5.chapter', nn=journal.output_ordinal)
        and journal.touched_paths == tuple(sorted(touched_paths(
            journal.output_ordinal)))
    )


def _manifest_exact_chapter(
        manifest: PreparedRecoveryManifest, chapter: PreparedRecoveryChapter,
        action: PreparedRecoveryAction,
) -> bool:
    rows = [
        row for row in manifest.workpack.chapters
        if row.output_ordinal == chapter.output_ordinal
    ]
    return bool(
        manifest.action == action
        and len(rows) == 1
        and rows[0] == chapter
    )


def _recovery_manifest_closes_journal(
        ctx: Any, journal: PreparedRecoveryJournal, *,
        manifest: PreparedRecoveryManifest | None = None,
) -> bool:
    """target manifest 自哈希、整书身份、action 与本章 row 的完整闭合。"""
    current = manifest
    if current is None:
        current = _validated_manifest(
            _recovery_manifest_path(
                ctx.novel_dir, journal.target_run_id))
    rows = [
        row for row in current.workpack.chapters
        if row.output_ordinal == journal.output_ordinal
    ]
    return bool(
        current.workpack.sid == journal.sid == ctx.sid
        and current.workpack.source_run_id == journal.source_run_id
        and current.workpack.target_run_id == journal.target_run_id
        and current.workpack.source_code == journal.source_code
        and current.workpack.target_code == journal.code
        and current.action.source_run_id == journal.source_run_id
        and current.action.target_run_id == journal.target_run_id
        and current.action.subject_sha256 == journal.subject_sha256
        and current.action.actor == journal.actor
        and current.action.reason == journal.reason
        and len(rows) == 1
        and _chapter_action_closes_journal(rows[0], current.action, journal)
    )


def preclaim_recovery_adoption(
        ctx: Any, *, chapter: PreparedRecoveryChapter,
        action: PreparedRecoveryAction,
        manifest: PreparedRecoveryManifest | None = None,
) -> bool:
    """逐章 ownership 内，在任何恢复写盘前做全局 audit 0-or-exact 检查。"""
    ordinal = _chapter(chapter.output_ordinal)
    _require_ownership(ctx, ordinal)
    current_manifest = manifest
    if current_manifest is None:
        current_manifest = _validated_manifest(
            _recovery_manifest_path(
                ctx.novel_dir, ctx.run_id))
    if (current_manifest.workpack.sid != ctx.sid
            or current_manifest.workpack.target_run_id != ctx.run_id
            or current_manifest.workpack.target_code != ctx.code
            or not _manifest_exact_chapter(
                current_manifest, chapter, action)):
        raise ProvenanceRefused(
            f'Stage5 第 {ordinal} 章 recovery manifest/workpack/action 与 preclaim 漂移')
    expected_adoption_id = source_adoption_id(
        sid=ctx.sid,
        source_run_id=action.source_run_id,
        chapter=ordinal,
        source_run_identity_sha256=chapter.source_run_identity_sha256,
        source_run_file_sha256=chapter.source_run_file_sha256,
        source_commit_sha256=chapter.source_commit_sha256,
        source_commit_file_sha256=chapter.source_commit_file_sha256,
        source_checkpoint_sha256=chapter.source_checkpoint_sha256,
        source_checkpoint_file_sha256=chapter.source_checkpoint_file_sha256,
        source_response_sha256=chapter.source_response_sha256,
    )
    if chapter.source_adoption_id != expected_adoption_id:
        raise ProvenanceRefused(
            f'Stage5 第 {ordinal} 章 workpack source_adoption_id 不闭合')

    matches = _matching_recovery_journals(
        ctx, source_run_id=action.source_run_id, chapter=ordinal,
        source_commit_sha256=chapter.source_commit_sha256)
    foreign = [
        journal for _path, journal in matches
        if journal.target_run_id != ctx.run_id
    ]
    if foreign:
        raise ProvenanceRefused(
            f'Stage5 第 {ordinal} 章 source adoption 已由 target run='
            f'{foreign[0].target_run_id} 持有；manifest 写盘前拒绝跨 run 重放')
    local = [
        journal for _path, journal in matches
        if journal.target_run_id == ctx.run_id
    ]
    if len(local) > 1:
        raise ProvenanceRefused(
            f'Stage5 第 {ordinal} 章当前 target 有多条 recovery journal')
    expected_row: dict[str, object] | None = None
    if local:
        journal = local[0]
        if (not _chapter_action_closes_journal(chapter, action, journal)
                or not _recovery_manifest_closes_journal(
                    ctx, journal, manifest=current_manifest)):
            raise ProvenanceRefused(
                f'Stage5 第 {ordinal} 章既有 recovery journal 与 manifest/workpack 漂移')
        expected_row = _expected_recovery_audit(journal)
    return ctx.ledger.preclaim_prepared_recovery_adoption(
        source_adoption_id=chapter.source_adoption_id,
        expected_row=expected_row,
    )


def preclaim_recovery_journal(
        ctx: Any, journal: PreparedRecoveryJournal,
) -> bool:
    """prepared journal 已存在时，从 manifest 还原 exact row/action 再 preclaim。"""
    ordinal = _chapter(journal.output_ordinal)
    _require_ownership(ctx, ordinal)
    manifest = _validated_manifest(
        _recovery_manifest_path(
            ctx.novel_dir, journal.target_run_id))
    rows = [
        row for row in manifest.workpack.chapters
        if row.output_ordinal == ordinal
    ]
    if len(rows) != 1:
        raise ProvenanceRefused(
            f'Stage5 第 {ordinal} 章 target manifest 缺唯一 workpack row')
    return preclaim_recovery_adoption(
        ctx, chapter=rows[0], action=manifest.action, manifest=manifest)


def _artifact_adoption_matches(
        artifact: Chapter, journal: PreparedRecoveryJournal,
) -> bool:
    adoption = getattr(
        artifact.provenance, 'prepared_checkpoint_adoption', None)
    return bool(
        adoption is not None
        and adoption.model_dump(mode='json')
        == _expected_recovery_adoption(journal)
    )


def _artifact_is_exact_recovered_payload(
        artifact: Chapter, journal: PreparedRecoveryJournal,
) -> bool:
    payload = artifact.model_dump(by_alias=True, mode='json')
    return bool(
        _artifact_adoption_matches(artifact, journal)
        and agent_runs.canonical_sha256(payload)
        == journal.primary_payload_sha256
        and prov.projection_fingerprint(payload)
        == journal.primary_payload_fingerprint
    )


def _persistent_recovery_carrier_exists(
        ctx: Any, journal: PreparedRecoveryJournal,
) -> bool:
    """后继 rewrite/recheck 后，live adoption 或 exact 历史轮次仍证明采用事实。"""
    from novel.store import ArtifactStore

    store = ArtifactStore(ctx)
    if store.exists(journal.primary_artifact):
        current, _raw = store.read_own(
            Chapter, journal.primary_artifact, require_current=False)
        if _artifact_adoption_matches(current, journal):
            return True
    rounds_dir = store.path(paths.rounds_dir_of(journal.primary_artifact))
    stem = paths.stem_of(journal.primary_artifact)
    for round_no in sorted(paths.archived_rounds(rounds_dir, stem)):
        archived, _raw = store.read_own(
            Chapter,
            paths.round_rel(journal.primary_artifact, round_no),
            require_current=False,
        )
        # ``run_recheck`` 会原位刷新派生字段而不归档最初 recovery bytes；随后
        # paid/apply rewrite 只会归档那份 rechecked Chapter。它仍携 exact typed
        # adoption，足以证明采用事实，不应再强求 journal 的原始 payload SHA。
        if _artifact_adoption_matches(archived, journal):
            return True
    return False


def _recovery_audit_closes(
        ctx: Any, journal: PreparedRecoveryJournal, *, current_target: bool,
) -> bool:
    expected = _expected_recovery_audit(journal)
    try:
        if current_target:
            ctx.ledger.require_prepared_recovery_adoption(expected_row=expected)
        else:
            ctx.ledger.require_prepared_recovery_lineage(expected_row=expected)
    except ProvenanceRefused:
        return False
    return True


def _committed_recovery_closes_live_now(
        ctx: Any, journal: PreparedRecoveryJournal,
        source_record: Mapping[str, Any],
) -> bool:
    """journal 提交当下的严格闭合；五个 live 路径必须仍是冻结 post。"""
    if (journal.state != 'committed'
            or journal.landed_payload_sha256 != journal.primary_payload_sha256
            or not _source_sidecars_match_journal(ctx, journal, source_record)
            or not _recovery_manifest_closes_journal(ctx, journal)):
        return False
    post = journal.post_file_sha256s
    if post is None or any(
            agent_runs.file_sha256(ctx, rel) != expected
            for rel, expected in post.items()):
        return False
    from novel.store import ArtifactStore
    store = ArtifactStore(ctx)
    if not store.exists(journal.primary_artifact):
        return False
    artifact, _raw = store.read_own(
        Chapter, journal.primary_artifact, require_current=False)
    return bool(
        _artifact_is_exact_recovered_payload(artifact, journal)
        and _recovery_audit_closes(ctx, journal, current_target=True)
    )


def _committed_recovery_closes_live(
        ctx: Any, journal: PreparedRecoveryJournal,
        source_record: Mapping[str, Any],
) -> bool:
    """后继 guard 的持久 lineage：不把后来合法 rewrite/recheck 当成失约。"""
    if (journal.state != 'committed'
            or journal.landed_payload_sha256 != journal.primary_payload_sha256
            or not _source_sidecars_match_journal(ctx, journal, source_record)
            or not _recovery_manifest_closes_journal(ctx, journal)
            or not _recovery_audit_closes(ctx, journal, current_target=False)):
        return False
    return _persistent_recovery_carrier_exists(ctx, journal)


def _replacement_status(
        ctx: Any, *, source_run_id: str, chapter: int,
        source_record: Mapping[str, Any],
) -> tuple[str, str | None]:
    matches = _matching_recovery_journals(
        ctx, source_run_id=source_run_id, chapter=chapter,
        source_commit_sha256=str(source_record.get('commit_sha256') or ''))
    if len(matches) > 1:
        raise ProvenanceRefused(
            f'Stage5 第 {chapter} 章 source run={source_run_id} 有多条 recovery lineage')
    if not matches:
        return 'unresolved', None
    _path, journal = matches[0]
    if journal.state == 'prepared':
        return 'recovering', journal.target_run_id
    if _committed_recovery_closes_live(ctx, journal, source_record):
        return 'superseded', journal.target_run_id
    raise ProvenanceRefused(
        f'Stage5 第 {chapter} 章 committed recovery lineage/live artifact 不闭合')


def unresolved_prepared_envelopes(
        ctx: Any, source_run_id: str,
) -> dict[int, dict[str, Any]]:
    source = source_prepared_envelopes(ctx, source_run_id)
    unresolved: dict[int, dict[str, Any]] = {}
    for chapter, record in source.items():
        status, target = _replacement_status(
            ctx, source_run_id=source_run_id, chapter=chapter,
            source_record=record)
        if status == 'superseded':
            continue
        if status == 'recovering' and target != ctx.run_id:
            raise ProvenanceRefused(
                f'Stage5 第 {chapter} 章已由 target run={target} 持有 prepared recovery；'
                '必须复用该 run 补完')
        unresolved[chapter] = record
    if not unresolved:
        raise ProvenanceRefused(
            f'Stage5 source run={source_run_id} 没有尚未 supersede 的 prepared')
    return unresolved


def _source_record_for_journal(
        ctx: Any, journal: PreparedRecoveryJournal,
) -> dict[str, Any]:
    target = (paths.stage5_chapter_run_path(
        ctx.novel_dir, journal.source_run_id, journal.output_ordinal)
        / agent_runs.COMMIT_FILE)
    with process_append_lock(target):
        return agent_runs.validate_foreign_commit_envelope(
            target, label='Stage5 recovery source commit',
            run_id=journal.source_run_id, sid=ctx.sid, node='stage5.write')


def prepare_recovery_journal(
        ctx: Any, *, chapter: PreparedRecoveryChapter,
        action: PreparedRecoveryAction,
        primary_payload: Mapping[str, Any],
        finalization_payload: Mapping[str, Any],
) -> PreparedRecoveryJournal:
    """冻结 replacement 主体与五个 live baseline；source sidecar 永不写。"""
    ordinal = _chapter(chapter.output_ordinal)
    _require_ownership(ctx, ordinal)
    if (action.target_run_id != ctx.run_id
            or action.source_run_id == ctx.run_id):
        raise ProvenanceRefused('prepared recovery action 不属于当前 target/source run')
    # 这是第一份逐章 recovery sidecar；任何 journal 字节出现前先在同一
    # chapter ownership 内完成全局 source adoption 0-or-exact 检查。
    preclaim_recovery_adoption(ctx, chapter=chapter, action=action)
    source_record_path = (paths.stage5_chapter_run_path(
        ctx.novel_dir, action.source_run_id, ordinal) / agent_runs.COMMIT_FILE)
    with process_append_lock(source_record_path):
        source_record = agent_runs.validate_foreign_commit_envelope(
            source_record_path, label='Stage5 recovery source commit',
            run_id=action.source_run_id, sid=ctx.sid, node='stage5.write')
    if (source_record.get('state') != 'prepared'
            or source_record.get('commit_sha256') != chapter.source_commit_sha256):
        raise ProvenanceRefused('prepared recovery source commit 已漂移或不再 prepared')
    expected_adoption_id = source_adoption_id(
        sid=ctx.sid, source_run_id=action.source_run_id, chapter=ordinal,
        source_run_identity_sha256=chapter.source_run_identity_sha256,
        source_run_file_sha256=chapter.source_run_file_sha256,
        source_commit_sha256=chapter.source_commit_sha256,
        source_commit_file_sha256=chapter.source_commit_file_sha256,
        source_checkpoint_sha256=chapter.source_checkpoint_sha256,
        source_checkpoint_file_sha256=chapter.source_checkpoint_file_sha256,
        source_response_sha256=chapter.source_response_sha256,
    )
    if expected_adoption_id != chapter.source_adoption_id:
        raise ProvenanceRefused('prepared recovery source_adoption_id 不闭合')
    matches = _matching_recovery_journals(
        ctx, source_run_id=action.source_run_id, chapter=ordinal,
        source_commit_sha256=chapter.source_commit_sha256)
    foreign = [journal for _path, journal in matches
               if journal.target_run_id != ctx.run_id]
    if foreign:
        raise ProvenanceRefused(
            f'Stage5 第 {ordinal} 章 source adoption 已由 target run='
            f'{foreign[0].target_run_id} 持有；拒绝跨新 run 重放')
    touched = tuple(sorted(touched_paths(ordinal)))
    if chapter.touched_paths != touched:
        raise ProvenanceRefused('prepared recovery workpack touched_paths 与 producer 漂移')
    current_baselines = {
        rel: agent_runs.file_sha256(ctx, rel) for rel in touched
    }
    target = paths.stage5_chapter_recovery_journal_path(
        ctx.novel_dir, ctx.run_id, ordinal)
    if not target.is_file() and current_baselines != chapter.current_baselines:
        raise ProvenanceRefused(
            f'Stage5 第 {ordinal} 章 live baseline 在 workpack 审阅后漂移')
    primary = dict(primary_payload)
    finalization = dict(finalization_payload)
    raw: dict[str, Any] = {
        'version': 1,
        'kind': 'stage5-prepared-recovery-journal-v1',
        'state': 'prepared', 'prepared_at': _now(), 'committed_at': None,
        'sid': ctx.sid, 'node': 'stage5.write',
        'source_run_id': action.source_run_id,
        'target_run_id': ctx.run_id, 'code': ctx.code,
        'source_code': chapter.source_code,
        'output_ordinal': ordinal, 'subject_sha256': action.subject_sha256,
        'source_adoption_id': chapter.source_adoption_id,
        'source_run_identity_sha256': chapter.source_run_identity_sha256,
        'source_run_file_sha256': chapter.source_run_file_sha256,
        'source_commit_sha256': chapter.source_commit_sha256,
        'source_commit_file_sha256': chapter.source_commit_file_sha256,
        'source_checkpoint_sha256': chapter.source_checkpoint_sha256,
        'source_checkpoint_file_sha256': chapter.source_checkpoint_file_sha256,
        'source_attempt_id': chapter.source_attempt_id,
        'source_response_sha256': chapter.source_response_sha256,
        'actor': action.actor, 'reason': action.reason,
        'primary_artifact': artifact_rel('stage5.chapter', nn=ordinal),
        'primary_payload_sha256': agent_runs.canonical_sha256(primary),
        'primary_payload_fingerprint': prov.projection_fingerprint(primary),
        'finalization_sha256': agent_runs.canonical_sha256(finalization),
        'touched_paths': touched,
        'baselines': dict(chapter.current_baselines),
        'landed_payload_sha256': None, 'post_file_sha256s': None,
        'journal_sha256': '0' * 64,
    }
    raw['journal_sha256'] = _journal_sha256(raw)
    proposed = PreparedRecoveryJournal.model_validate(raw)
    with process_append_lock(target):
        if target.is_file():
            current = _validated_recovery_journal(target)
            ignored = {
                'state', 'prepared_at', 'committed_at', 'journal_sha256',
                'landed_payload_sha256', 'post_file_sha256s',
            }
            left = {
                key: value for key, value in current.model_dump(mode='json').items()
                if key not in ignored
            }
            right = {
                key: value for key, value in proposed.model_dump(mode='json').items()
                if key not in ignored
            }
            if left != right:
                raise ProvenanceRefused(
                    f'{target} 已绑定另一份 recovery 主体/基线/action')
            return current
        agent_runs._atomic_json(target, proposed.model_dump(mode='json'))
    return proposed


def recovery_target_state(
        ctx: Any, chapter: int, journal: PreparedRecoveryJournal,
        rel: str, *, expected_file_sha256: str | None = None,
        expected_absent: bool = False,
) -> str:
    ordinal = _chapter(chapter)
    _require_ownership(ctx, ordinal)
    current = recovery_state(ctx, ordinal)
    if current is None or current != journal:
        raise ProvenanceRefused('传入的 recovery journal 不是当前盘上记录')
    if rel not in current.baselines:
        raise ProvenanceRefused(f'{rel} 不属于 recovery touched_paths')
    if expected_absent and expected_file_sha256 is not None:
        raise ProvenanceRefused(f'{rel} 不能同时声明 expected SHA 与 absent')
    actual = agent_runs.file_sha256(ctx, rel)
    if expected_absent and actual is None:
        return 'expected'
    if expected_file_sha256 is not None and actual == expected_file_sha256:
        return 'expected'
    if actual == current.baselines[rel]:
        return 'baseline'
    raise ProvenanceRefused(
        f'{rel} 在 prepared recovery 尾窗中既不是写前基线，也不是已验证目标')


def verify_recovery_primary(
        journal: PreparedRecoveryJournal, payload: Mapping[str, Any],
) -> bool:
    return journal.primary_payload_sha256 == agent_runs.canonical_sha256(dict(payload))


def verify_recovery_source(
        ctx: Any, chapter: int, journal: PreparedRecoveryJournal,
) -> None:
    """任何 live 写入前复核旧 run 的 raw+self sidecar 仍与 journal 完全一致。"""
    ordinal = _chapter(chapter)
    _require_ownership(ctx, ordinal)
    current = recovery_state(ctx, ordinal)
    if current is None or current != journal:
        raise ProvenanceRefused('传入的 recovery journal 不是当前盘上记录')
    source_record = _source_record_for_journal(ctx, current)
    if not _source_sidecars_match_journal(ctx, current, source_record):
        raise ProvenanceRefused(
            f'Stage5 第 {ordinal} 章 source run/checkpoint/commit raw+self 已漂移')


def verify_recovery_finalization(
        journal: PreparedRecoveryJournal, payload: Mapping[str, Any],
) -> bool:
    return journal.finalization_sha256 == agent_runs.canonical_sha256(dict(payload))


def mark_recovery_committed(
        ctx: Any, *, chapter: int, primary_payload: Mapping[str, Any],
        finalization_payload: Mapping[str, Any],
) -> Path:
    ordinal = _chapter(chapter)
    _require_ownership(ctx, ordinal)
    target = paths.stage5_chapter_recovery_journal_path(
        ctx.novel_dir, ctx.run_id, ordinal)
    snapshot = recovery_state(ctx, ordinal)
    if snapshot is None:
        raise ProvenanceRefused('recovery committed 前 prepared journal 不存在')
    # prepared -> committed 也是 journal 写盘；此时 adoption audit 应已落下，
    # 必须与 manifest/journal 导出的 canonical row 完全一致。
    preclaim_recovery_journal(ctx, snapshot)
    with process_append_lock(target):
        current = _validated_recovery_journal(target)
        if current != snapshot:
            raise ProvenanceRefused('recovery committed 前 journal 在 preclaim 后漂移')
        if (current.target_run_id != ctx.run_id or current.code != ctx.code
                or not verify_recovery_primary(current, primary_payload)
                or not verify_recovery_finalization(current, finalization_payload)):
            raise ProvenanceRefused('recovery committed 前主产物/finalization/target 漂移')
        source_record = _source_record_for_journal(ctx, current)
        if not _source_sidecars_match_journal(ctx, current, source_record):
            raise ProvenanceRefused('recovery committed 前 source sidecar 字节或 self-hash 漂移')
        if not _recovery_audit_closes(ctx, current, current_target=True):
            raise ProvenanceRefused(
                'recovery committed 前 adoption audit 与 journal 整行不闭合')
        post = {
            rel: agent_runs.file_sha256(ctx, rel) for rel in current.touched_paths
        }
        if post[current.primary_artifact] is None:
            raise ProvenanceRefused('recovery committed 前 canonical 主产物缺失')
        landed_sha = agent_runs.canonical_sha256(dict(primary_payload))
        if current.state == 'committed':
            if (current.landed_payload_sha256 != landed_sha
                    or current.post_file_sha256s != post
                    or not _committed_recovery_closes_live_now(
                        ctx, current, source_record)):
                raise ProvenanceRefused('committed recovery journal 与 live state 漂移')
            return target
        raw = current.model_dump(mode='json')
        raw.update({
            'state': 'committed', 'committed_at': _now(),
            'landed_payload_sha256': landed_sha,
            'post_file_sha256s': post, 'journal_sha256': '0' * 64,
        })
        raw['journal_sha256'] = _journal_sha256(raw)
        committed = PreparedRecoveryJournal.model_validate(raw)
        if not _committed_recovery_closes_live_now(
                ctx, committed, source_record):
            raise ProvenanceRefused(
                'recovery committed 落账前 source/live/adoption audit 未严格闭合')
        agent_runs._atomic_json(target, committed.model_dump(mode='json'))
    return target


def verify_recovery_committed(
        ctx: Any, chapter: int, journal: PreparedRecoveryJournal, *,
        primary_payload: Mapping[str, Any],
        finalization_payload: Mapping[str, Any],
) -> None:
    ordinal = _chapter(chapter)
    _require_ownership(ctx, ordinal)
    current = recovery_state(ctx, ordinal)
    if (current is None or current != journal or current.state != 'committed'
            or not verify_recovery_primary(current, primary_payload)
            or not verify_recovery_finalization(current, finalization_payload)):
        raise ProvenanceRefused('recovery committed readback 身份/主产物漂移')
    source_record = _source_record_for_journal(ctx, current)
    if not _committed_recovery_closes_live_now(ctx, current, source_record):
        raise ProvenanceRefused('recovery committed lineage 没有与 source/live artifact 闭合')


def prepared(
        ctx: Any, chapter: int, *, mode: str = 'write',
) -> dict[str, Any] | None:
    record = agent_runs.commit_state(ctx, _commit_spec(chapter, mode))
    return record if record is not None and record.get('state') == 'prepared' else None


def committed(
        ctx: Any, chapter: int, *, mode: str = 'write',
) -> dict[str, Any] | None:
    record = agent_runs.commit_state(ctx, _commit_spec(chapter, mode))
    return record if record is not None and record.get('state') == 'committed' else None


def refuse_foreign_commit_reservations(
        ctx: Any, chapter: int, *, mode: str = 'write',
) -> None:
    """同章 foreign prepared 仅可由精确 committed recovery supersede。"""
    ordinal = _chapter(chapter)
    _require_ownership(ctx, ordinal)
    current = _commit_spec(ordinal, mode).commit_path(ctx).resolve()
    dirname = f'ch{ordinal:04d}'
    for target in _history_files(ctx, f'*/{dirname}/{agent_runs.COMMIT_FILE}'):
        if target.parent.name != dirname:
            raise ProvenanceRefused(
                f'Stage5 foreign commit 章节目录不闭合：{target}')
        run_id = target.parent.parent.name
        if run_id == ctx.run_id or target.resolve() == current:
            continue
        try:
            with process_append_lock(target):
                record = agent_runs.validate_foreign_commit_envelope(
                    target, label='Stage5 foreign commit', run_id=run_id,
                    sid=ctx.sid, node='stage5.write')
        except OSError as exc:
            raise ProvenanceRefused(
                f'Stage5 foreign commit journal {target} 不可验证：{exc}') from exc
        if record['state'] == 'prepared':
            if mode == 'reviewer_rewrite':
                raise ProvenanceRefused(
                    f'Stage5 第 {ordinal} 章 run={run_id} 留有 foreign reviewer '
                    'rewrite prepared；只能按同一 formal request 走 0-wire recovery，'
                    '禁止新建 driver/wire')
            validation_reads = getattr(ctx, 'validation_reads', None)
            scope = validation_reads() if callable(validation_reads) else nullcontext()
            # supersession 会 typed-read live canonical；它只用于 foreign guard，
            # 不能泄漏进随后新正文的 provenance.inputs。
            with scope:
                status, recovery_run = _replacement_status(
                    ctx, source_run_id=run_id, chapter=ordinal,
                    source_record=record)
            if status == 'superseded':
                continue
            if status == 'recovering':
                raise ProvenanceRefused(
                    f'Stage5 第 {ordinal} 章 source run={run_id} 已由 target run='
                    f'{recovery_run} 持有 prepared recovery；请复用该 target run '
                    '`--recover-prepared` 补完，禁止启动新 driver/wire')
            raise ProvenanceRefused(
                f'Stage5 第 {ordinal} 章 run={run_id} 仍持有 foreign prepared；'
                f'先运行 `stage5.write --emit-prepared-recovery-workpack {run_id}`，'
                f'审阅后用新 run 执行 `--recover-prepared {run_id}`。'
                '在 committed recovery lineage 与 live artifact 闭合前禁止新 driver/wire')


def refuse_foreign_prepared_before_wire(
        ctx: Any, chapters: str | None = None) -> None:
    """全书/定向付费写作在 client、driver factory 之前共用的 reservation guard。"""
    selected: set[int] | None = None
    if chapters is not None:
        try:
            selected = {
                _chapter(int(item)) for item in chapters.split(',') if item.strip()
            }
        except ValueError as exc:
            raise ProvenanceRefused(f'--chapters 含非整数：{chapters!r}') from exc
        if not selected:
            raise ProvenanceRefused('--chapters 不能为空')
    found: set[int] = set()
    for target in _history_files(ctx, f'*/ch*/{agent_runs.COMMIT_FILE}'):
        if target.parent.parent.name == ctx.run_id:
            continue
        suffix = target.parent.name[2:] if target.parent.name.startswith('ch') else ''
        if len(suffix) != 4 or not suffix.isdigit() or int(suffix) < 1:
            raise ArtifactUnreadable(f'Stage5 foreign commit 路径不闭合：{target}')
        ordinal = int(suffix)
        if selected is not None and ordinal not in selected:
            continue
        found.add(ordinal)
    for ordinal in sorted(found):
        with commit_ownership(ctx.novel_dir, ordinal):
            refuse_foreign_commit_reservations(ctx, ordinal)


def prepare_commit(
        ctx: Any, *, chapter: int, primary_payload: Mapping[str, Any],
        finalization_payload: Mapping[str, Any], attempt_id: str,
        mode: str = 'write',
) -> dict[str, Any]:
    ordinal = _chapter(chapter)
    _require_ownership(ctx, ordinal)
    refuse_foreign_commit_reservations(ctx, ordinal, mode=mode)
    return agent_runs.prepare_commit(
        ctx, _commit_spec(ordinal, mode), primary_payload=primary_payload,
        finalization_payload=finalization_payload,
        touched_paths=touched_paths(ordinal), attempt_id=attempt_id)


def target_state(
        ctx: Any, chapter: int, record: Mapping[str, Any], rel: str, *,
        expected_file_sha256: str | None = None,
        expected_absent: bool = False,
        mode: str = 'write',
) -> str:
    ordinal = _chapter(chapter)
    _require_ownership(ctx, ordinal)
    return agent_runs.target_state(
        ctx, _commit_spec(ordinal, mode), record, rel,
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
        ctx: Any, chapter: int, record: Mapping[str, Any], *,
        mode: str = 'write',
) -> tuple[str, str]:
    ordinal = _chapter(chapter)
    _require_ownership(ctx, ordinal)
    return agent_runs.restore_commit_adoption(
        ctx, _commit_spec(ordinal, mode), record)


def mark_committed(
        ctx: Any, *, chapter: int, primary_payload: Mapping[str, Any],
        finalization_payload: Mapping[str, Any],
        mode: str = 'write',
) -> Path:
    ordinal = _chapter(chapter)
    _require_ownership(ctx, ordinal)
    primary = artifact_rel('stage5.chapter', nn=ordinal)
    return agent_runs.mark_committed(
        ctx, _commit_spec(ordinal, mode), primary_payload=primary_payload,
        finalization_payload=finalization_payload,
        landed_payloads={primary: dict(primary_payload)})


def verify_committed_delivery(
        ctx: Any, chapter: int, record: Mapping[str, Any], *,
        primary_payload: Mapping[str, Any],
        finalization_payload: Mapping[str, Any],
        mode: str = 'write',
) -> None:
    ordinal = _chapter(chapter)
    _require_ownership(ctx, ordinal)
    primary = artifact_rel('stage5.chapter', nn=ordinal)
    agent_runs.verify_committed_delivery(
        ctx, _commit_spec(ordinal, mode), record,
        primary_payload=primary_payload,
        finalization_payload=finalization_payload,
        landed_payloads={primary: dict(primary_payload)})


__all__ = [
    'HistoricalPrepared', 'HistoricalReviewerRewrite', 'RECOVERY_POLICY',
    'REVIEWER_REWRITE_RECOVERY_POLICY', 'SESSION_ISOLATION',
    'book_commit_ownership', 'commit_ownership', 'committed', 'ensure_run',
    'manifest_sha256', 'mark_committed', 'mark_recovery_committed',
    'preclaim_recovery_adoption', 'preclaim_recovery_journal',
    'prepare_commit', 'prepare_recovery_journal', 'prepared', 'purpose_of',
    'recovery_policy_of',
    'read_recovery_manifest', 'recovery_state', 'recovery_target_state',
    'require_prepared_recovery_target_clean', 'require_workpack_subject',
    'refuse_foreign_commit_reservations',
    'refuse_foreign_prepared_before_wire', 'restore_checkpoint',
    'restore_commit_adoption', 'restore_historical_prepared',
    'restore_historical_reviewer_rewrite',
    'prepare_reviewer_rewrite_recovery',
    'mark_reviewer_rewrite_recovery_committed',
    'record_reviewer_rewrite_execution',
    'reviewer_rewrite_checkpoint_file',
    'reviewer_rewrite_commit_envelope',
    'reviewer_rewrite_execution_chain',
    'reviewer_rewrite_execution_chains',
    'reviewer_rewrite_recovery_journals',
    'reviewer_rewrite_recovery_state',
    'reviewer_rewrite_recovery_target_state',
    'reviewer_rewrite_source_adoption_id',
    'reviewer_rewrite_source_file_sha256',
    'verify_reviewer_rewrite_recovery_committed',
    'verify_reviewer_rewrite_recovery_payload',
    'verify_reviewer_rewrite_recovery_source',
    'source_adoption_id', 'source_prepared_envelopes', 'target_state',
    'touched_paths', 'unresolved_prepared_envelopes',
    'verify_recovery_committed', 'verify_recovery_finalization',
    'verify_recovery_primary', 'verify_recovery_source',
    'workpack_subject', 'write_recovery_manifest',
    'verify_commit_finalization', 'verify_commit_primary',
    'verify_committed_delivery', 'write_checkpoint',
]
