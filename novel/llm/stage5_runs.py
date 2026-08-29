# -*- coding: utf-8 -*-
"""Stage5 逐章 executor 对通用 single-session agent run core 的薄适配。

``agent_runs`` 统一校验 resolved profile、prompt/schema、stdout、成本账、会话账、
append-only checkpoint 与 ``prepared -> committed`` journal。本模块只声明 Stage5
的业务边界：一章一个 logical/provider session；同章正式 bundle 独占提交锁；
foreign ``prepared`` 默认必须由原 run 补完；只有显式新 run 在当前代码下完成
``prepared checkpoint recovery``，且 source raw+self sidecar、adoption audit、typed
canonical 与 committed replacement lineage 全部闭合时，旧 reservation 才算 superseded。
正式主产物之外的 INVALID、拒稿与 trim 状态只是同一事务的确定性伴生路径。

问题稿与修订拒稿不记 adoption，因此不伪装成 formal commit。它们的付费候选已由
append-only checkpoint 保存，进程重启后可以零 wire 重评并再次走原有代码写盘口。
"""
from __future__ import annotations

import json
import os
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
    prepared_recovery_workpack_subject,
)
from novel import agent_session_audit, paths, prov
from novel.appendlog import process_append_lock
from novel.errors import ArtifactUnreadable, ProvenanceRefused
from novel.llm import agent_runs, ledger as ledger_module
from novel.llm.transport import ChatResponse


M = TypeVar('M', bound=BaseModel)

RECOVERY_POLICY = 'stage5-write-v1'
SESSION_ISOLATION = 'per-chapter'
RECOVERY_MANIFEST_VERSION = 1

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


@dataclass(frozen=True)
class HistoricalPrepared:
    """通过 source 自身 code/profile/session/ledger 与当前请求双重校验的 checkpoint。"""

    historical: agent_runs.RestoredHistoricalRun[Any]
    commit: dict[str, Any]


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


def source_prepared_envelopes(
        ctx: Any, source_run_id: str,
) -> dict[int, dict[str, Any]]:
    """枚举 source run 的 prepared commit 外壳；深验在逐章恢复时完成。"""
    from novel.run_identity import check_run_id

    source = check_run_id(source_run_id)
    if source == ctx.run_id:
        raise ProvenanceRefused('prepared recovery source run 必须不同于当前 target run')
    root = Path(ctx.novel_dir) / paths.STAGE5_RUNS_DIRNAME / source
    if not root.is_dir():
        raise ProvenanceRefused(f'Stage5 source run={source} 不存在')
    out: dict[int, dict[str, Any]] = {}
    for child in sorted(root.iterdir(), key=lambda path: path.name):
        if not child.is_dir() or not child.name.startswith('ch'):
            continue
        suffix = child.name[2:]
        if len(suffix) != 4 or not suffix.isdigit() or int(suffix) < 1:
            raise ArtifactUnreadable(
                f'Stage5 source run 含非 canonical 章节目录：{child}')
        ordinal = int(suffix)
        target = child / agent_runs.COMMIT_FILE
        if not target.is_file():
            continue
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


def write_recovery_manifest(
        ctx: Any, manifest: PreparedRecoveryManifest,
) -> Path:
    """首次 apply 原子落不可变 manifest；同 target run 只允许逐字幂等。"""
    require_workpack_subject(manifest.workpack)
    target = paths.stage5_prepared_recovery_manifest_path(
        ctx.novel_dir, ctx.run_id)
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
    target = paths.stage5_prepared_recovery_manifest_path(
        ctx.novel_dir, ctx.run_id)
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


def require_prepared_recovery_target_clean(ctx: Any) -> None:
    """证明本 target run 从未留下 wire、cache 或 agent session 持久事实。

    内存计数只挡同进程污染；crash/restart 后还必须按全局 ``run_id`` 扫两份
    append-only 账。agent session 事件没有 sid；target run 是全局身份，因此同一
    base 中任意 ``parent_run_id`` 命中都拒绝，也不能靠错/缺 node 或「当前书没
    sidecar」把既有 session 洗掉。
    """
    if ctx.node != 'stage5.write' or ctx.paid:
        raise ProvenanceRefused('prepared recovery 只属于 free stage5.write run')
    if ctx.executor_profile is not None:
        raise ProvenanceRefused('prepared recovery 禁止 executor profile/driver')
    if ctx.ledger.calls_made or ctx.ledger.calls_cached:
        raise ProvenanceRefused(
            'prepared recovery target run 已出现内存 wire/cache 计数；拒绝冒充 0-wire 恢复')

    base = Path(ctx.novel_dir).parent
    cost_rows = _strict_jsonl_objects(
        ledger_module.ledger_path(base), label='prepared recovery 成本账')
    if any(row.get('run_id') == ctx.run_id for row in cost_rows):
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
        if event.parent_run_id == ctx.run_id:
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
    root = Path(ctx.novel_dir) / paths.STAGE5_RUNS_DIRNAME
    dirname = f'ch{_chapter(chapter):04d}'
    matches: list[tuple[Path, PreparedRecoveryJournal]] = []
    if not root.is_dir():
        return matches
    for target in root.glob(
            f'*/{dirname}/{paths.stage5_chapter_recovery_journal_path(ctx.novel_dir, ctx.run_id, chapter).name}'):
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
            paths.stage5_prepared_recovery_manifest_path(
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
            paths.stage5_prepared_recovery_manifest_path(
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
        paths.stage5_prepared_recovery_manifest_path(
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


def prepared(ctx: Any, chapter: int) -> dict[str, Any] | None:
    record = agent_runs.commit_state(ctx, _commit_spec(chapter))
    return record if record is not None and record.get('state') == 'prepared' else None


def committed(ctx: Any, chapter: int) -> dict[str, Any] | None:
    record = agent_runs.commit_state(ctx, _commit_spec(chapter))
    return record if record is not None and record.get('state') == 'committed' else None


def refuse_foreign_commit_reservations(ctx: Any, chapter: int) -> None:
    """同章 foreign prepared 仅可由精确 committed recovery supersede。"""
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
    root = Path(ctx.novel_dir) / paths.STAGE5_RUNS_DIRNAME
    if not root.is_dir():
        return
    found: set[int] = set()
    for target in root.glob(f'*/ch*/{agent_runs.COMMIT_FILE}'):
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
    'HistoricalPrepared', 'RECOVERY_POLICY', 'SESSION_ISOLATION',
    'commit_ownership', 'committed', 'ensure_run', 'file_sha256',
    'manifest_sha256', 'mark_committed', 'mark_recovery_committed',
    'preclaim_recovery_adoption', 'preclaim_recovery_journal',
    'prepare_commit', 'prepare_recovery_journal', 'prepared', 'purpose_of',
    'read_recovery_manifest', 'recovery_state', 'recovery_target_state',
    'require_prepared_recovery_target_clean', 'require_workpack_subject',
    'refuse_foreign_commit_reservations',
    'refuse_foreign_prepared_before_wire', 'restore_checkpoint',
    'restore_commit_adoption', 'restore_historical_prepared',
    'source_adoption_id', 'source_prepared_envelopes', 'target_state',
    'touched_paths', 'unresolved_prepared_envelopes',
    'verify_recovery_committed', 'verify_recovery_finalization',
    'verify_recovery_primary', 'verify_recovery_source',
    'workpack_subject', 'write_recovery_manifest',
    'verify_commit_finalization', 'verify_commit_primary',
    'verify_committed_delivery', 'write_checkpoint',
]
