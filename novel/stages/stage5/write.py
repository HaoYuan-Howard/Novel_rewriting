#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
write.py —— `stage5.write`：**逐章写作**。全链唯一产出「书」的层，也是唯一带 LLM 返工回路的层。

十条路：
    默认            逐章一次调用，落 `stage5/chNN.json`          **付费**
    `--revise`      把可反哺的结论连同上一轮成稿发回写手           **付费**
    `--chapters`    只写这几章                                      付费路的**参数**
    `--dry-run`     只打印将要发出去的东西                        0 成本
    `--recheck`     按当前判据重判存量成稿并写回指标                0 成本
    `--revalidate`  把合格的 `.INVALID` 提升为正式产物              0 成本
    `--apply-edit`  把人工改好的那一份落成新的一轮                  0 成本
    `--emit-prepared-recovery-workpack`  只读复验旧 run 的 prepared   0 成本
    `--recover-prepared`                 按已审工作包重铸正式章         0 wire

⚠⚠ **本层只有闭卷这一条写作路**。源章原文只供确定性校验，
不进 prompt，也没有可以把它加回去的 mode 参数。

逐章写作走 `ask_json` + `ChapterResponse`：producer-owned slot 必须恰好各出现一次，
模型数组顺序不参与组装。机械把关仍在落盘前运行，但响应载体越界会更早在
Pydantic 边界被拒绝，不能形成正常或 `.INVALID` 章产物。

⚠⚠ **收缩臂已整臂下线，本层不再有第二条付费回路。**
这里曾有「同槽结构化候选生成（`TRIM_ROUNDS` 轮） → 确定性 claims 对账 → A/B 双盲语义裁决
→ 采用候选」一整条路，由 `require_enabled('trim_equivalence', …)` 开闸。那个闸从未在任何
宿主上打开过——本仓没有标定过的 evaluator profile，且那套装置已连同 profile 一起删除，
所以**没有任何路径能采用一个收缩候选**。超上限章现在一律零调用落 identity `TrimAudit`
（`_over_length_audit`）转 `verification`，由 `trim_resolution.retain_pretrim` 人工销账，
或由 `stage5.write --apply-edit` 改写成新的一章。

恢复需要一起恢复的东西：`contracts/schema/evaluation.py` 的 `Claim` / `TrimEvidenceSpan` /
`TrimJudgeResponse` 与 `TrimAudit` 的六个字段、`novel.quality.profile`（已删）、
`prompts.trim_system` / `trim_judge_system` / `trim_user` / `trim_judge_user`，
以及本文件的 `_trim_claims` / `_claim_regressions` / `_trim_deterministic_regressions` /
`_trim_profile_binding` / `_judge_trim_candidate`。恢复它等于重新承诺去做那场标定战役。
"""
from __future__ import annotations

from contracts.artifacts import STAGE5_REVIEWER_REWRITE_REQUEST, artifact_rel

import copy
import glob
import hashlib
import json
import os
import re
import threading
from collections.abc import Callable, Iterator, Mapping, Sequence
from concurrent.futures import CancelledError, ThreadPoolExecutor, as_completed
from contextlib import contextmanager, nullcontext
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any, Protocol

from contracts.invariants import (
    STAGE5_COMMIT_PHASES, STAGE5_EDIT_CONFIRMATIONS, STAGE5_WRITE_MAX_CONCURRENCY,
    STAGE5_REVIEWER_REWRITE_DIRECTIVE_CODES, STOP_REWRITE_AFTER,
)
from novel.agent_session_audit import sanitize_failure_detail
from novel.config import MAX_CONCURRENCY, DEFAULT_CONCURRENCY
from contracts.rules import REGISTRY
from contracts.schema.common import (
    Finding, InputEdge, PreparedCheckpointAdoption,
    RoundFreeProvenance, RoundLlmProvenance,
)
from contracts.schema.api import DraftEditPreview
from contracts.schema.report import CheckReport
from contracts.schema.evaluation import TrimAudit
from contracts.schema.stage4 import CtxChapter
from contracts.schema.stage5 import (
    Chapter, ChapterResponse, ChapterSegment, PreparedRecoveryAction,
    PreparedRecoveryChapter, PreparedRecoveryJournal,
    PreparedRecoveryManifest, PreparedRecoveryWorkpack, RejectedAttempt, RejectedRevision,
    ReviewerRewriteRecoveryJournal, ReviewerRewriteRequest,
)
from novel import human_actions, paths, prov
from novel.errors import (
    ArtifactUnreadable, GateBlocked, ProvenanceRefused, ResponseRepairRequired,
    SourceConflict, TerminalState, UpstreamMissing,
)
from novel.llm.cache import CachePolicy
from novel.llm.client import LlmClient, LlmFailed
from novel.llm import stage5_runs
from novel.llm.privacy import assert_prompt_private_absent
from novel.llm.transport import ChatResponse
from novel.observability import ChapterProgressTracker, RequestProgress
from novel.report.builder import build
from novel.report.findings import unverifiable
from novel.run import RunContext
from novel.stages.stage5 import checks
from novel.stages.stage5 import measure as M
from novel.stages.stage5 import prompts, review
from novel.stages.stage5 import reviewer_rewrite
from novel.store import ArtifactStore

#: 一次逐章调用的上限。⚠ 它进 `_provenance.params`——换了这个数，产物里说得出来。
MAX_TOKENS = 16000

# formal reviewer rewrite 的 code→指令只在这里渲染一次；schema/transport 的取值
# 域均直接消费 invariant 常量。actor/reason 是审计信息，刻意不进入任何 prompt。
_REVIEWER_REWRITE_DIRECTIVE_INSTRUCTIONS = {
    'remove_repetition': '通读全章并删去语义重复，不得删除只出现一次的事实或义务。',
    'compress_exposition': '压缩全章的解释性铺陈，用更短正文保留全部事实与因果。',
    'repair_wording': '修正全章不自然或不准确的措辞，不改变逐字义务与事实含义。',
    'smooth_transition': '平滑全章动作、场景或时间转换的衔接，不得改变既定时序。',
}
if tuple(_REVIEWER_REWRITE_DIRECTIVE_INSTRUCTIONS) != tuple(
        STAGE5_REVIEWER_REWRITE_DIRECTIVE_CODES):
    raise RuntimeError('Stage5 reviewer rewrite 指令映射与 invariant 取值域漂移')
_REVIEWER_REWRITE_PROMPT_CONTRACT = json.dumps(
    _REVIEWER_REWRITE_DIRECTIVE_INSTRUCTIONS,
    ensure_ascii=False, sort_keys=True, separators=(',', ':'))

# ``RunContext.validation_reads`` 保存/恢复实例上的三张来源账，不是线程局部。
# Stage5 各章模型请求仍可并发；只有极短的正式上游 currentness 重读必须串行。
_STAGE5_VALIDATION_LOCK = threading.RLock()


@dataclass(frozen=True)
class _CommitOutcomeDiagnostics:
    """一次终态收口要写进 session ended 的提交诊断投影。

    ``attempts`` 是本章发生过的正式提交调用数（首次 1、sweep 恢复后 2）；
    ``recovered=True`` 表示首次提交异常后 same-run 重放成功。它们只描述
    已发生的提交史，不参与判定。
    """

    attempts: int
    recovered: bool
    failure_phase: str | None
    failure_detail: str | None


@dataclass
class _CommitDiagnostics:
    """一次正式提交调用的阶段诊断，喂给 session audit wire v2 的 ended 事件。

    ``phase`` 是「当前执行段」游标（嵌套段退出时恢复上一段）；``failure_phase``
    与 ``detail`` 在**首次**异常时定格（取值面 ``STAGE5_COMMIT_PHASES``，摘要经
    :func:`sanitize_failure_detail` 截断脱敏），不被段恢复覆盖——收口方读的是
    这两个格子。它只描述失败，不参与任何判定——拿它分支业务逻辑是误用。
    """

    phase: str | None = None
    failure_phase: str | None = None
    detail: str | None = None

    def fail(self, exc: BaseException, *, phase: str) -> None:
        if self.failure_phase is not None:
            return  # 首个异常才是「第一次提交停在哪」；重入不覆盖
        self.failure_phase = phase
        self.detail = sanitize_failure_detail(f'{type(exc).__name__}: {exc}')


@contextmanager
def _commit_phase(
    box: _CommitDiagnostics | None, phase: str,
) -> Iterator[None]:
    """把一段提交代码标进 ``STAGE5_COMMIT_PHASES``。

    提交失败必须能说出发生在哪一段；只留笼统的 ``artifact_verification``
    曾让一次 1/17 落盘事故无账可查。段名拼错时在进入阶段即刻终态，而不是
    等真失败了才发现诊断面没登记。退出时恢复上一个段名：嵌套段（事务内的
    typed_readback）失败后，``phase`` 必须回答「异常发生时正在执行哪段」。
    """
    if phase not in STAGE5_COMMIT_PHASES:
        raise ValueError(f'提交阶段未登记：{phase!r}')
    if box is None:
        yield
        return
    previous = box.phase
    box.phase = phase
    try:
        yield
    except BaseException as exc:
        box.fail(exc, phase=phase)
        raise
    finally:
        box.phase = previous


class _ChapterResponseSource(Protocol):
    """逐章候选循环真正消费的窄面。

    agent 路径由节点层提供按 ``purpose/chapter`` 路由的 pool；pool 背后每章必须是
    独立 ``SessionAgentClient``。producer 不接触 driver，也不持有跨章会话表。
    legacy ``LlmClient`` 仍可满足首轮请求与 accepted identity 那一半接口。
    """

    model: str
    concurrency: int

    def ask_json(self, system: str, user: str, **kwargs: Any) -> Any: ...

    def accepted_attempt(self, purpose: str) -> str | None: ...

    def accepted_model(self, purpose: str) -> str | None: ...

    def accepted_models(self, *purposes: str) -> tuple[str, ...]: ...

    def accepted_request_fingerprint(self, purpose: str) -> str | None: ...

    def accepted_checkpoint(self, purpose: str) -> dict[str, Any]: ...

    def restore_checkpoint_acceptance(
        self, purpose: str, record: Mapping[str, Any], **kwargs: Any,
    ) -> None: ...

    def queue_code_validation_feedback(
        self,
        rows: Sequence[Mapping[str, object]],
        *,
        purpose: str | None = None,
    ) -> bool: ...

    def queue_restored_code_validation_feedback(
        self,
        rows: Sequence[Mapping[str, object]],
        *,
        purpose: str | None = None,
    ) -> bool: ...

    def purpose_can_resume(self, purpose: str) -> bool: ...

    def purpose_driver_failed(self, purpose: str) -> bool: ...

    def finish_chapter_session(
        self, chapter: int, *, outcome: str,
        delivery_attempts: int | None = None,
        same_run_recovered: bool | None = None,
        first_failure_phase: str | None = None,
        first_failure_detail: str | None = None,
    ) -> None: ...


@dataclass(frozen=True)
class ChapterCandidateFeedback:
    """可安全交回同一写手 session 的代码拒因。

    Stage5 的机械 findings 可能包含未下发的源文字节；通常只给稳定规则元数据、
    数量和注册表里的通用指令。逐字义务缺失仅可回指当前不可变 given 中确实已下发的
    原串；不能把整个 ``Finding.detail/evidence`` 当作反馈，扩张闭卷暴露面。
    revision regression 的 ``changes`` 只描述旧稿/新稿的规则计数变化；旧稿本来就在
    revise prompt 中，不增加闭卷暴露面。
    """

    rule: str
    item: str
    requirement: str
    instruction: str
    count: int
    changes: tuple[str, ...] = ()
    #: 只给数字/槽位账的补充行（如段落计划的对账读数）。它们来自代码确定性
    #: 计算，不含源文字节，与 privacy 边界一致。
    details: tuple[str, ...] = ()
    #: 仅 verbatim_missing 使用；每项须逐字存在于本章已下发材料，顺序稳定且不重复。
    required_materials: tuple[str, ...] = ()

    def to_dict(self) -> dict[str, object]:
        out: dict[str, object] = {
            'type': 'business_rule',
            'rule': self.rule,
            'item': self.item,
            'requirement': self.requirement,
            'instruction': self.instruction,
            'count': self.count,
        }
        if self.changes:
            out['changes'] = list(self.changes)
        if self.details:
            out['details'] = list(self.details)
        if self.required_materials:
            out['required_materials'] = list(self.required_materials)
        return out


@dataclass(frozen=True)
class ChapterCandidateContext:
    """一章 executor session 的冻结任务书；不含其他章成稿或正式落盘能力。"""

    ordinal: int
    system: str
    user: str
    given: str
    purpose: str
    mode: str
    revision: bool
    _entry: dict[str, Any] = field(repr=False)
    _book: M.Book = field(repr=False)
    _prev_text: str = field(default='', repr=False)
    _fixes: tuple[tuple[str, str], ...] = field(default=(), repr=False)
    _review_fp: str = field(default='', repr=False)
    _reviewer_rewrite_request: ReviewerRewriteRequest | None = field(
        default=None, repr=False)
    _reviewer_rewrite_request_file_sha256: str = field(
        default='', repr=False)
    _input_sha256: str = field(default='', repr=False)


@dataclass(frozen=True)
class ChapterCandidateEvaluation:
    """一轮尚未落盘、尚未结算的逐章候选与代码判定。"""

    ordinal: int
    accepted_purpose: str
    requested_model: str
    adopted_model: str
    source_attempt_id: str
    request_fingerprint: str
    segments: tuple[ChapterSegment, ...]
    text: str
    findings: tuple[Finding, ...]
    errors: tuple[str, ...]
    warnings: tuple[str, ...]
    regressions: tuple[str, ...]
    feedback: tuple[ChapterCandidateFeedback, ...]
    attempts: tuple[RejectedAttempt, ...]
    _account: M.NormalizeAccount = field(repr=False)
    _response: ChapterResponse = field(repr=False)
    _input_sha256: str = field(repr=False)
    _candidate_sha256: str = field(repr=False)

    @property
    def passed(self) -> bool:
        return not self.errors and not self.regressions

    def feedback_rows(self) -> list[dict[str, object]]:
        return [row.to_dict() for row in self.feedback]


@dataclass(frozen=True)
class ChapterCandidateDelivery:
    """一章终态：正式稿、问题稿或 revision 拒稿三者恰居其一。"""

    outcome: str  # formal | invalid | rejected_revision
    where: str
    artifact: Chapter | RejectedRevision
    findings: tuple[Finding, ...] = ()
    errors: tuple[str, ...] = ()
    trim_notes: tuple[str, ...] = ()


@dataclass(frozen=True)
class _HistoricalCheckpointSource:
    """只把已由 historical validator 复验的 acceptance 暴露给纯评估链。"""

    model: str
    purpose: str
    acceptance: Mapping[str, Any] = field(repr=False)
    concurrency: int = 1

    def _for(self, purpose: str, key: str) -> str | None:
        if purpose != self.purpose:
            raise ProvenanceRefused(
                f'historical checkpoint purpose 漂移：{purpose!r} != {self.purpose!r}')
        value = self.acceptance.get(key)
        return str(value) if isinstance(value, str) and value else None

    def accepted_attempt(self, purpose: str) -> str | None:
        return self._for(purpose, 'attempt_id')

    def accepted_model(self, purpose: str) -> str | None:
        return self._for(purpose, 'model')

    def accepted_request_fingerprint(self, purpose: str) -> str | None:
        return self._for(purpose, 'request_fingerprint')


def _rel(ordinal: int) -> str:
    return artifact_rel('stage5.chapter', nn=ordinal)


def _retire_trim_state(store: ArtifactStore, ordinal: int) -> None:
    """A chapter rewrite makes both the live trim audit and its closure stale."""
    for kind in ('stage5.trim_audit', 'stage5.trim_resolution'):
        rel = artifact_rel(kind, nn=ordinal)
        if store.exists(rel):
            store.archive_round(rel)


def _commit_canonical_chapter(
    store: ArtifactStore,
    chapter: Chapter,
    *,
    expected_ordinal: int,
    entry: dict[str, Any],
    commit: Callable[[str, Chapter], Any],
) -> tuple[Any, list[str]]:
    """提交一份 canonical Chapter，并让 trim 状态精确描述这一份正文。

    这是 Stage5 替换/提升 ``chNN.json`` 的唯一提交口。调用方必须同时交出它正在
    处理的 ``expected_ordinal``；本函数在任何写盘前将其与载体主体闭合，再由这一个
    章号同时推导 canonical 与两份 trim 路径。entry 必须与该章主体及槽位闭合；
    预算由调用方已验证的任务书派生，正文只从 Chapter 读取。完全匹配的 audit
    刻意保留：
    hash-bound ``retain_pretrim``
    裁决的是正文，不是 ``warns`` 或 provenance 之类的元数据。正文/audit 一变，
    旧 audit 与 resolution 就在正文提交的同一回滚域里退休。

    ``.INVALID`` 不经过这里；失败候选不得扰动仍然在位的旧 canonical trim 状态。
    """
    if chapter.output_ordinal != expected_ordinal:
        raise ProvenanceRefused(
            f'Stage5 canonical 提交主体错配：目标是第 {expected_ordinal} 章，'
            f'载体却自报第 {chapter.output_ordinal} 章。'
            '不得借 recheck/revalidate 把一章写进另一章路径或更新错误的 trim 状态')
    ordinal = expected_ordinal
    chapter_rel = _rel(ordinal)
    audit_rel = artifact_rel('stage5.trim_audit', nn=ordinal)
    resolution_rel = artifact_rel('stage5.trim_resolution', nn=ordinal)
    segments = [segment.model_dump() for segment in chapter.segments]
    if entry.get('output_ordinal') != ordinal:
        raise ProvenanceRefused('Stage5 canonical 提交的任务书章号与目标不一致')
    _require_canonical_slots(segments, entry)
    trim_notes, desired_audit = _over_length_audit(segments, entry)

    matching_audit: tuple[TrimAudit, str] | None = None
    if desired_audit is not None and store.exists(audit_rel):
        try:
            current_audit, current_sha256 = store.read_own_with_sha256(
                TrimAudit, audit_rel)
        except ArtifactUnreadable:
            pass
        else:
            if current_audit == desired_audit:
                matching_audit = current_audit, current_sha256

    with store.history_transaction(chapter_rel, audit_rel, resolution_rel):
        # 正文 CAS/授权终验仍消费旧 audit；先替换正文，再退休旧 trim 状态。
        # 全部仍在同一回滚域。硬杀时 old/new 路径组合由 prepared WAL 收口，
        # 不能先删旧 audit 再让 commit 回调验证一份已经被自己拆掉的授权。
        where = commit(chapter_rel, chapter)
        if desired_audit is None:
            _retire_trim_state(store, ordinal)
        elif matching_audit is None:
            # 新 audit 最终仍应存在，不能先 move 旧 audit 再写新稿：进程若在
            # 两步之间退出，live 会落在既非 journal baseline、也非 expected 的
            # 第三状态。先留一份 immutable copy、保持旧 live，待下面原子替换；
            # resolution 的目标本来就是 absent，可以照常退休。
            if store.exists(audit_rel):
                store.archive_round(audit_rel, keep=True)
            if store.exists(resolution_rel):
                store.archive_round(resolution_rel)

        if desired_audit is not None and matching_audit is None:
            store.write(audit_rel, desired_audit)
        elif matching_audit is not None and store.exists(resolution_rel):
            # 局部 import：current hash-bound closure 的唯一判定归
            # trim_resolution 所有，而那个模块本身依赖 Chapter。
            from novel.stages.stage5 import trim_resolution
            current_audit, current_sha256 = matching_audit
            if not trim_resolution.matches_current_audit(
                store,
                current_audit,
                current_sha256,
                ordinal=ordinal,
            ):
                store.archive_round(resolution_rel)

    return where, trim_notes


def _rejected_rel(ordinal: int) -> str:
    return artifact_rel('stage5.rejected_revision', nn=ordinal)


def require_canonical_layout(store: ArtifactStore) -> None:
    """旧双臂盘面是显式终态；任何付费/写回入口都必须先经过这里。"""
    legacy = M.legacy_stage5_paths(store)
    if legacy:
        raise TerminalState(
            f'legacy_incompatible：发现旧 Stage5 双臂产物 {legacy[:8]}。'
            '旧 base 只作历史证据，不在原地迁移、选臂或静默触发付费重跑；'
            '请在独立 canonical base 从 Stage5 重建')


def _chapters_of(store: ArtifactStore, book: M.Book,
                 spec: str | None) -> list[dict[str, Any]]:
    """这一次要写哪几章。

    ⚠⚠ **不给 `--chapters` 时只补还没有 canonical 成稿的章**，不是全部重写。
    完成度的口径是逐章的（`R-14.2`），默认路必须与它一致——否则一次「续跑」会把已经
    付过钱的章再写一遍。
    ⚠ **`--chapters` 是定向重写的唯一载体**：给了就照给的写（哪怕那一章
    已经有稿——那是人明确要重写）。
    """
    plan = book.chapters
    if spec:
        want = list(dict.fromkeys(int(x) for x in spec.split(',') if x.strip()))
        if (missing := [w for w in want if w not in plan]):
            raise TerminalState(
                f'--chapters 指定的输出章不存在：{missing}（计划共 {len(plan)} 章）')
        return [plan[w] for w in want]
    return [plan[o] for o in sorted(plan) if not store.exists(_rel(o))]


# ─────────────────────────────────────────────────────────────────────────────
# 一、下发面 —— `--dry-run` 与真跑**共用这一个入口**
# ─────────────────────────────────────────────────────────────────────────────

def build_prompt(ctx: RunContext, chapters: str | None = None
                 ) -> tuple[str, str, prompts.Material]:
    """这一次**要发出去的** `(system, user, 下发面)`。

    ⚠ 它与真跑共用同一个构造入口（`R-11.8`）——否则「预览里看到的」与「真发出去的」
    是两件事，而那种分家没有任何东西会报错。
    ⚠ `--dry-run` 只摊**这一次要写的第一章**——摊全部没人读得完，而形状是一样的。
    """
    store = ArtifactStore(ctx)
    require_canonical_layout(store)
    book = review.load_book(ctx, store)
    todo = _chapters_of(store, book, chapters)
    if not todo:
        raise TerminalState(
            '这一次没有要写的章：计划里的每一章都已经有 canonical 成稿了。'
            '⚠ 这**不是**「没问题」——要重写某一章就用 `--chapters N` 明说')
    entry = todo[0]
    mat = prompts.build_material(entry, book)
    system, user = prompts.write_system(), prompts.build_user(mat)
    assert_prompt_private_absent(
        CtxChapter.model_validate(entry), system=system, user=user)
    return system, user, mat


# ─────────────────────────────────────────────────────────────────────────────
# 二、⭐ 落盘 —— 归一 → 判据 → 闸门。**唯一写成稿的地方。**
# ─────────────────────────────────────────────────────────────────────────────

def _judge(text: str, entry: dict[str, Any], book: M.Book, *, mat: str,
           account: M.NormalizeAccount,
           segments: list[dict[str, str]] | None = None) -> tuple[list, list[str], list[str]]:
    """归一之后的字节 → `(findings, errs, warns)`。**落盘闸门与验收相共用同一份判据。**"""
    fs = checks.chapter_findings(text, entry, book, given=mat,
                                 quote_problems=account.quote_problems,
                                 segments=segments)
    return fs, checks.blocking(fs), checks.warnings_of(fs)



def _chapter_record(
    entry: dict[str, Any], segments: list[dict[str, str]], *, provenance: Any,
) -> Chapter:
    """正式稿与 INVALID 共用最小章载体；检查结论由调用方交给报告。"""
    _require_canonical_slots(segments, entry)
    return Chapter(
        output_ordinal=int(entry['output_ordinal']),
        segments=[ChapterSegment.model_validate(segment) for segment in segments],
        _provenance=provenance,
    )


def _inspection_account(
    segments: list[dict[str, str]], book: M.Book,
) -> M.NormalizeAccount:
    """只观察依赖归一器的引号异常，不采用它返回的改写正文。"""
    _normalized, observed = _normalize_segments(segments, book)
    return M.NormalizeAccount(quote_problems=list(observed.quote_problems))





def _require_revalidation_task_identity(
    candidate: Chapter,
    baseline: Chapter,
) -> None:
    """INVALID 可改正文，但不能借人工编辑改写冻结的付费任务身份。"""
    fields = ('output_ordinal',)
    changed = [
        field for field in fields
        if getattr(candidate, field) != getattr(baseline, field)
    ]
    if changed:
        raise ProvenanceRefused(
            'Stage5 INVALID 的任务身份字段不得人工改写：'
            + ', '.join(changed)
            + '。正文可通过 segments 修订；任务快照必须继承不可变创建基线')


def _write_chapter(ctx: RunContext, store: ArtifactStore, entry: dict[str, Any],
                   book: M.Book, *, segments: list[dict[str, str]], mat: str,
                   account: M.NormalizeAccount, provenance: Any,
                   before_commit: Callable[[Chapter, M.NormalizeAccount, bool], None] | None = None,
                   ) -> tuple[str, list[str], list, list[str]]:
    """归一 → 判据 → 闸门 → 落盘。返回 `(落点, errs, findings, trim notes)`。

    ⚠⚠ **归一必须跑在收缩【之前】**（调用方保证），而**再归一一遍**在这里：链子是
    「归一 → 收缩(LLM) → 再归一 → 校验」，三道门与落盘看的是**同一份最终字节**。
    两遍的账**相加不覆盖**——只留后一份会让「首稿修了 9 处引号」凭空消失。

    ⚠ 有 `errs` ⇒ **不覆盖已有成稿**，问题稿存 `.INVALID`，**一章失败不中断整批**。
    """
    segments2, acc2 = _normalize_segments(segments, book)
    text2 = _segments_text(segments2)
    account = account + acc2
    for k, v in account.counters().items():
        ctx.account(k, int(ctx.accounting.get(k) or 0) + v)
    fs, errs, warns = _judge(
        text2, entry, book, mat=mat, account=account,
        segments=segments2)
    rel = _rel(entry['output_ordinal'])
    ordinal = int(entry['output_ordinal'])
    rejected_rel = _rejected_rel(ordinal)
    art = _chapter_record(
        entry,
        segments2,
        provenance=provenance,
    )
    with stage5_runs.commit_ownership(ctx.novel_dir, ordinal):
        if before_commit is not None:
            before_commit(art, account, bool(errs))
        with store.history_transaction(rel, rejected_rel):
            if store.exists(rejected_rel):
                store.archive_round(rejected_rel)
            if errs:
                # ⚠ `gates_skipped` 是**空列表也要写**的那一格：
                # 「一条都没跳过」与「没人说」是两件事。
                p = store.write_invalid(rel, art, gates_skipped=[])
                return str(p), errs, fs, []
            where, trim_notes = _commit_canonical_chapter(
                store,
                art,
                expected_ordinal=ordinal,
                entry=entry,
                commit=lambda canonical_rel, artifact: store.write_round(canonical_rel, artifact),
            )
            return str(where), [], fs, trim_notes


# ─────────────────────────────────────────────────────────────────────────────
# 三、付费路
# ─────────────────────────────────────────────────────────────────────────────

def _reject_length_capped(_text: str, response: ChatResponse) -> str | None:
    """`finish_reason=length` 是协议层明确说「答案被截断」，非空也不能当成正文。"""
    if response.finish_reason == 'length':
        return 'finish_reason=length（答案被 token 上限截断）'
    return None


def _expected_slots(entry: dict[str, Any]) -> tuple[str, ...]:
    return tuple(str(scene.get('slot') or '') for scene in (entry.get('scenes') or []))


def _slot_contract_problem(
    segments: list[dict[str, str]] | list[ChapterSegment],
    entry: dict[str, Any],
    *,
    exact_order: bool,
) -> str | None:
    """同一份 slot 集合尺子；是否允许 producer 归序由 ingress 明说。"""
    expected = _expected_slots(entry)
    actual = tuple(
        segment.slot if isinstance(segment, ChapterSegment)
        else str(segment.get('slot') or '')
        for segment in segments
    )
    if len(actual) != len(expected) or set(actual) != set(expected):
        return f'slot 必须恰好为 {list(expected)}，实际为 {list(actual)}'
    if exact_order and actual != expected:
        return f'slot 顺序必须为 {list(expected)}，实际为 {list(actual)}'
    return None


def _require_canonical_slots(
    segments: list[dict[str, str]] | list[ChapterSegment],
    entry: dict[str, Any],
) -> None:
    """拒绝无法在不改正文的前提下保持 producer slot 身份的 canonical 候选。"""
    if problem := _slot_contract_problem(segments, entry, exact_order=True):
        raise TerminalState(f'Stage5 canonical segment 契约不成立：{problem}')


def _response_problem(
    response: ChapterResponse,
    wire: ChatResponse,
    entry: dict[str, Any],
) -> str | None:
    if wire.finish_reason == 'length':
        return 'finish_reason=length（答案被 token 上限截断）'
    # 模型响应可乱序：producer 会在落盘前按计划归序；这里只要求集合闭合。
    return _slot_contract_problem(response.segments, entry, exact_order=False)


def _ordered_segments(
    response: ChapterResponse,
    entry: dict[str, Any],
) -> list[dict[str, str]]:
    by_slot = {segment.slot: segment.text.strip() for segment in response.segments}
    return [{'slot': slot, 'text': by_slot[slot]} for slot in _expected_slots(entry)]


def _segments_text(segments: list[dict[str, str]] | list[ChapterSegment]) -> str:
    def text_of(item: dict[str, str] | ChapterSegment) -> str:
        return item.text if isinstance(item, ChapterSegment) else str(item.get('text') or '')
    return '\n\n'.join(text_of(item).strip() for item in segments).strip()


def _normalize_segments(
    segments: list[dict[str, str]],
    book: M.Book,
) -> tuple[list[dict[str, str]], M.NormalizeAccount]:
    out: list[dict[str, str]] = []
    account = M.NormalizeAccount()
    for segment in segments:
        text, one = M.normalize_chapter(
            segment['text'], quote_style=book.quote_style, sep_chars=book.sep_chars)
        if not text.strip():
            raise TerminalState(f"slot {segment['slot']} 归一后为空，拒绝拼成缺段章节")
        out.append({'slot': segment['slot'], 'text': text})
        account = account + one
    return out, account


def _sha256_text(text: str) -> str:
    return hashlib.sha256(text.encode('utf-8')).hexdigest()


def _over_length_audit(segments: list[dict[str, str]],
                       entry: dict[str, Any]) -> tuple[list[str], TrimAudit | None]:
    """超上限 ⇒ **零调用**落一份 identity `TrimAudit`，正文原样保留。

    ⚠ 签名里刻意没有 `client`：本层不再有第二条付费回路，这个函数在结构上就花不了钱。
    这里曾经会退回去做「只删不改」的收缩，再由校准双盲裁决要不要采用候选；那条路的
    开闸条件（`require_enabled('trim_equivalence', …)`）在本仓永远不成立，现已连同
    evaluator profile 整批删除（模块头记了删的是什么、怎么恢复）。

    ⇒ 超上限的唯一出路是**人看**，两条都 0 成本：
      · 接受当前原稿 ⇒ `stage5.review --retain-pretrim`（写 hash-bound resolution）；
      · 确需改写     ⇒ `stage5.write --apply-edit`（落成新的一轮）。

    ⚠ `length_over` 是提示级判据，压不下来**照样落盘**——所以这份 audit 才是那道门：
    它把「这一章超了，且还没有人处理过」变成 `verification` 里一条会赖着不走的待办，
    而不是一行读完就忘的提示。
    """
    text = _segments_text(segments)
    chars, hi = M.chars_of(text), M.target_range(entry)[1]
    if chars <= hi:
        return [], None
    reason = (
        f'超上限 {chars - hi} 字（{chars}/{hi}）；本仓没有自动收缩路径，'
        f'保留 pretrim，转 verification')
    return [reason], TrimAudit(
        before_sha256=_sha256_text(text),
        reason=reason,
    )


def ctx_purpose(entry: dict[str, Any]) -> str:
    ordinal = entry.get('output_ordinal')
    if not isinstance(ordinal, int) or isinstance(ordinal, bool):
        raise ProvenanceRefused(
            f'Stage5 purpose 缺合法 output_ordinal：{ordinal!r}')
    return stage5_runs.purpose_of(ordinal)


def _canonical_sha256(value: object) -> str:
    return hashlib.sha256(json.dumps(
        value, sort_keys=True, ensure_ascii=False,
        separators=(',', ':'),
    ).encode('utf-8')).hexdigest()


def _book_validation_projection(book: M.Book) -> dict[str, object]:
    """Stage5 prompt + 私有机械校验真正消费的书级投影。

    原文与 secrets 只在内存里参与 hash，不进入 agent prompt、checkpoint 或 service audit。
    用内容 hash 而非正文保存，是为了让长 session 期间的私有校验输入漂移也关闭式失败。
    """
    from novel.consumption import stage5_context
    source = getattr(book, 'source_text', {}) or {}
    return {
        'title': getattr(book, 'title', ''),
        'writer_context_sha256': _canonical_sha256(
            stage5_context(getattr(book, 'ctx', {}) or {})),
        'measurable_sha256': _canonical_sha256(
            getattr(book, 'measurable', {}) or {}),
        'source_text_sha256': [
            [int(ordinal), _sha256_text(str(text))]
            for ordinal, text in sorted(source.items())
        ],
        'secrets_sha256': _canonical_sha256(
            getattr(book, 'secrets', []) or []),
        'characters_sha256': _canonical_sha256(
            getattr(book, 'characters', {}) or {}),
    }


def _chapter_input_sha256(
    *,
    entry: Mapping[str, Any],
    book: M.Book,
    system: str,
    user: str,
    given: str,
    prev_text: str,
    fixes: Sequence[Sequence[str]],
    review_fp: str,
    mode: str = 'write',
    reviewer_rewrite_request_sha256: str | None = None,
    reviewer_rewrite_request_file_sha256: str | None = None,
) -> str:
    payload: dict[str, object] = {
        'entry': dict(entry),
        'book': _book_validation_projection(book),
        'system_sha256': _sha256_text(system),
        'user_sha256': _sha256_text(user),
        'given_sha256': _sha256_text(given),
        'revision_of': _sha256_text(prev_text) if prev_text else None,
        'fixes': [list(item) for item in fixes],
        'review': review_fp or None,
    }
    # 保留 initial write/revise 历史 input digest 字节；新 mode 才扩展身份面。
    if mode == 'reviewer_rewrite':
        payload.update({
            'mode': mode,
            'reviewer_rewrite_request_sha256':
                reviewer_rewrite_request_sha256,
            'reviewer_rewrite_request_file_sha256':
                reviewer_rewrite_request_file_sha256,
        })
    return _canonical_sha256(payload)


def _reviewer_rewrite_user(
        mat: prompts.Material, prev_text: str,
        request: ReviewerRewriteRequest, *, budget_chars: int,
) -> str:
    """正式单章语义改写 prompt；审计定位与内部枚举永不外发。"""
    directive_lines = [
        f'· {_REVIEWER_REWRITE_DIRECTIVE_INSTRUCTIONS[row.code]}'
        for row in request.directives
    ]
    obligations = (
        '【formal reviewer rewrite 固定义务】\n'
        '- 输出完整本章的全部 producer slots，不得输出 diff、说明或额外字段；\n'
        f'- 归一后的整章字数不得超过冻结预算上限 {budget_chars} 字；\n'
        '- 保留计划中的 slot 集合与顺序，保留全部 required facts、因果、人物状态与时序；\n'
        '- 逐字保留任务材料中标成必须逐字出现的文本，不得改写、缩写或挪作别义；\n'
        '- 只围绕下列关闭式指令做语义整章改写，不得借压缩删掉唯一事实。'
    )
    return '\n\n'.join([
        prompts.build_user(mat),
        '── 当前正式章正文（本轮必须输出改写后的完整一章）──\n' + prev_text,
        obligations,
        '── 本轮关闭式自然语言指令 ──\n'
        + '\n'.join(directive_lines),
    ])


def prepare_chapter_candidate(
    entry: dict[str, Any],
    book: M.Book,
    *,
    revision: tuple[str, list[list[str]], str] | None,
    reviewer_rewrite_request: tuple[
        ReviewerRewriteRequest, str, str,
    ] | None = None,
    system: str | None = None,
) -> ChapterCandidateContext:
    """冻结一章闭卷任务；不调用模型、不铸 provenance、不写盘。

    ``revision`` 只允许是本章旧正式稿与当前 Review 选出的 fixes。调用方每章各建一份
    context；本结构没有上一章成稿字段，天然不能把它串进下一章 prompt。
    """
    typed_entry = CtxChapter.model_validate(entry)
    entry_snapshot = typed_entry.model_dump(by_alias=True, mode='json')
    ordinal = int(entry_snapshot['output_ordinal'])
    book_snapshot = copy.deepcopy(book)
    mat = prompts.build_material(entry_snapshot, book_snapshot)
    if revision is not None and reviewer_rewrite_request is not None:
        raise ValueError('普通 revise 与 formal reviewer rewrite 不能同时启用')
    request: ReviewerRewriteRequest | None = None
    request_file_sha256 = ''
    if reviewer_rewrite_request is not None:
        request, request_file_sha256, prev_text = reviewer_rewrite_request
        if request.target_ordinal != ordinal:
            raise ProvenanceRefused(
                f'reviewer rewrite request target={request.target_ordinal} '
                f'不能用于第 {ordinal} 章')
        # request SHA 已冻结 code/evidence/actor/reason 的审计身份；writer 输入只消费
        # code 对应的自然语言指令。这里保持空，避免内部枚举或定位证据经 input/context
        # 的 legacy ``fixes`` 面二次泄漏给写手。
        raw_fixes = []
        review_fp = request.review_sha256
        rendered_system = system if system is not None else prompts.write_system()
        user = _reviewer_rewrite_user(
            mat, prev_text, request,
            budget_chars=M.target_range(entry_snapshot)[1])
        mode = 'reviewer_rewrite'
    elif revision is None:
        prev_text, raw_fixes, review_fp = '', [], ''
        rendered_system = system if system is not None else prompts.write_system()
        user = prompts.build_user(mat)
        mode = 'write'
    else:
        prev_text, raw_fixes, review_fp = revision
        rendered_system = (
            system if system is not None else prompts.write_system(revise=True))
        user = prompts.revise_user(mat, prev_text, _fix_lines(raw_fixes))
        mode = 'revise'
    fixes = tuple((str(item[0]), str(item[1])) for item in raw_fixes)
    assert_prompt_private_absent(
        typed_entry, system=rendered_system, user=user)
    input_sha256 = _chapter_input_sha256(
        entry=entry_snapshot,
        book=book_snapshot,
        system=rendered_system,
        user=user,
        given=mat.given,
        prev_text=prev_text,
        fixes=fixes,
        review_fp=review_fp,
        mode=mode,
        reviewer_rewrite_request_sha256=(
            None if request is None else request.request_sha256),
        reviewer_rewrite_request_file_sha256=(
            None if request is None else request_file_sha256),
    )
    return ChapterCandidateContext(
        ordinal=ordinal,
        system=rendered_system,
        user=user,
        given=mat.given,
        purpose=stage5_runs.purpose_of(ordinal, mode),
        mode=mode,
        revision=mode != 'write',
        _entry=entry_snapshot,
        _book=book_snapshot,
        _prev_text=prev_text,
        _fixes=fixes,
        _review_fp=review_fp,
        _reviewer_rewrite_request=request,
        _reviewer_rewrite_request_file_sha256=request_file_sha256,
        _input_sha256=input_sha256,
    )


def _assert_chapter_context_memory(prepared: ChapterCandidateContext) -> None:
    """冻结 dataclass 里的可变容器也不得在评估后被偷偷修改。"""
    actual = _chapter_input_sha256(
        entry=prepared._entry,
        book=prepared._book,
        system=prepared.system,
        user=prepared.user,
        given=prepared.given,
        prev_text=prepared._prev_text,
        fixes=prepared._fixes,
        review_fp=prepared._review_fp,
        mode=prepared.mode,
        reviewer_rewrite_request_sha256=(
            None if prepared._reviewer_rewrite_request is None
            else prepared._reviewer_rewrite_request.request_sha256),
        reviewer_rewrite_request_file_sha256=(
            prepared._reviewer_rewrite_request_file_sha256 or None),
    )
    if actual != prepared._input_sha256:
        raise ProvenanceRefused(
            f'Stage5 第 {prepared.ordinal} 章冻结任务在内存中被修改；'
            '拒绝把候选接到另一份任务书')


def assert_chapter_candidate_current(
    ctx: RunContext,
    prepared: ChapterCandidateContext,
    *,
    after_formal_commit: bool = False,
) -> None:
    """反馈与终态前重载正式上游，证明长 session 仍回答同一章任务。"""
    if ctx.node != 'stage5.write':
        raise ValueError(
            f'assert_chapter_candidate_current 只接受 stage5.write，实得 {ctx.node!r}')
    _assert_chapter_context_memory(prepared)
    if prov.code_version() != ctx.code:
        raise ProvenanceRefused(
            f'Stage5 第 {prepared.ordinal} 章 executor session 期间代码版本漂移；'
            '拒绝结算 usage 或写入正式产物')
    store = ArtifactStore(ctx)
    with _STAGE5_VALIDATION_LOCK:
        with ctx.validation_reads():
            current_book = review.load_book(ctx, store)
            current_entry = current_book.chapters.get(prepared.ordinal)
            if current_entry is None:
                raise ProvenanceRefused(
                    f'Stage5 第 {prepared.ordinal} 章在 executor session 期间已离开当前计划')
            revision: tuple[str, list[list[str]], str] | None = None
            rewrite_input: tuple[ReviewerRewriteRequest, str, str] | None = None
            if prepared.mode == 'reviewer_rewrite':
                request = prepared._reviewer_rewrite_request
                if request is None:
                    raise ProvenanceRefused(
                        f'Stage5 第 {prepared.ordinal} 章 reviewer rewrite '
                        '冻结任务缺 formal request')
                if after_formal_commit:
                    # 正文/Review/trim 正应因 successful rewrite 失效；这里只允许
                    # 同一 request 原字节 + 冻结旧正文重构原 prompt，不能误要求重跑 Review。
                    current_request, current_request_file_sha256 = (
                        reviewer_rewrite.load_reviewer_rewrite_request(
                            ctx, STAGE5_REVIEWER_REWRITE_REQUEST,
                            request_sha256=request.request_sha256))
                else:
                    current_request, current_request_file_sha256 = (
                        reviewer_rewrite.require_current_reviewer_rewrite_request(
                            ctx, STAGE5_REVIEWER_REWRITE_REQUEST,
                            request_sha256=request.request_sha256))
                if (current_request != request
                        or current_request_file_sha256
                        != prepared._reviewer_rewrite_request_file_sha256):
                    raise ProvenanceRefused(
                        f'Stage5 第 {prepared.ordinal} 章 reviewer rewrite request '
                        '在 executor session 期间漂移')
                if current_request.target_ordinal != prepared.ordinal:
                    raise ProvenanceRefused('reviewer rewrite request target 漂移')
                if after_formal_commit:
                    previous_text = prepared._prev_text
                else:
                    old, _raw = store.read_own(
                        Chapter, _rel(prepared.ordinal), require_current=True)
                    previous_text = old.text
                rewrite_input = (
                    current_request, current_request_file_sha256, previous_text)
            elif prepared.revision:
                # 提交前旧 Review 必须仍能代表旧正式稿；正式替换后它按既有业务语义
                # 必然 stale，此时只要求同一份 Review 字节仍在，不能拿“应当 stale”
                # 反过来否定刚完成的 revision delivery。
                if not after_formal_commit:
                    review.require_fresh_review(ctx, store)
                if after_formal_commit:
                    # 正式 readback 已经取代旧稿；此时仍要复核 Review/Stage4/私有基线，
                    # 但 revision prompt 的旧稿身份从冻结值取，并由新稿 provenance 再闭合。
                    previous_text = prepared._prev_text
                else:
                    old, _raw = store.read_own(
                        Chapter, _rel(prepared.ordinal), require_current=True)
                    previous_text = old.text
                latest = review.latest_review(store)
                if latest is None:
                    raise ProvenanceRefused(
                        f'Stage5 第 {prepared.ordinal} 章修订期间当前 Review 消失')
                seq, _artifact = latest
                review_fp = prov.content_sha1(
                    store.path(review.REVIEW_TPL.format(n=seq)).read_bytes())
                revision = (
                    previous_text,
                    [list(item) for item in prepared._fixes],
                    review_fp,
                )
            current = prepare_chapter_candidate(
                current_entry,
                current_book,
                revision=revision,
                reviewer_rewrite_request=rewrite_input,
                system=prepared.system,
            )
    if current._input_sha256 != prepared._input_sha256:
        raise ProvenanceRefused(
            f'Stage5 第 {prepared.ordinal} 章 executor session 期间输入/prompt/'
            '私有校验基线发生漂移；须以新 session 重跑')


def _accepted_request_fingerprint(
    source: _ChapterResponseSource,
    purpose: str,
) -> str:
    direct = getattr(source, 'accepted_request_fingerprint', None)
    value = direct(purpose) if callable(direct) else None
    if not isinstance(value, str) or not value:
        checkpoint = getattr(source, 'accepted_checkpoint', None)
        record = checkpoint(purpose) if callable(checkpoint) else None
        value = record.get('request_fingerprint') if isinstance(record, Mapping) else None
    if not isinstance(value, str) or not value:
        raise ProvenanceRefused(
            f'{purpose}: candidate 已通过 wire/schema，但缺 accepted request fingerprint')
    return value


def _accepted_model(source: _ChapterResponseSource, purpose: str) -> str:
    direct = getattr(source, 'accepted_model', None)
    value = direct(purpose) if callable(direct) else None
    if not isinstance(value, str) or not value:
        many = getattr(source, 'accepted_models', None)
        models = many(purpose) if callable(many) else ()
        if len(models) == 1:
            value = models[0]
    if not isinstance(value, str) or not value:
        raise ProvenanceRefused(
            f'{purpose}: candidate 已通过 wire/schema，但缺唯一 adopted model')
    return value


def _accepted_attempt(source: _ChapterResponseSource, purpose: str) -> str:
    getter = getattr(source, 'accepted_attempt', None)
    value = getter(purpose) if callable(getter) else None
    if not isinstance(value, str) or not value:
        raise ProvenanceRefused(
            f'{purpose}: candidate 已通过 wire/schema，但缺 accepted attempt')
    return value


def _paragraph_budget_details(
    findings: Sequence[Finding],
    segments: Sequence[ChapterSegment],
    entry: dict[str, Any],
    book: M.Book,
) -> tuple[str, ...]:
    """`paragraph_budget_missed` 反馈附带的精确槽位账（全部为代码现算读数）。

    实际段数与合法范围、至少要合并/拆分多少段、各 slot 实际值 vs
    `paragraph_slot_plan` 的目标值——写手拿到的是「差多少、差在哪」，而不是
    一句笼统的「段数不对」。
    """
    if not any(f.rule == 'paragraph_budget_missed' for f in findings):
        return ()
    try:
        plan = M.paragraph_slot_plan(entry, book)
    except Exception:
        plan = None
    lo, hi = M.target_paragraphs(entry, book)
    lines: list[str] = []

    def paras(text: str) -> int:
        return len([line for line in str(text).split('\n') if line.strip()])

    actual_total = sum(paras(segment.text) for segment in segments)
    lines.append(f'实际 {actual_total} 段，合法范围 {lo}~{hi} 段')
    if actual_total < lo:
        lines.append(f'至少需要拆分 {lo - actual_total} 段来增加段数')
    elif actual_total > hi:
        lines.append(f'至少需要合并 {actual_total - hi} 段来减少段数')
    scenes = [scene for scene in (entry.get('scenes') or []) if scene]
    if plan is not None and scenes and len(segments) == len(scenes):
        for index, (segment, scene) in enumerate(zip(segments, scenes)):
            lines.append(
                f"slot {scene.get('slot')}：实际 {paras(segment.text)} 段 / 目标 {plan[index]} 段")
    lines.append('只调整段落组织（合并或拆分段落），保留全部事实、场景内容与文字。')
    return tuple(lines)


def _chapter_feedback(
    findings: Sequence[Finding],
    regressions: Sequence[str],
    *,
    paragraph_details: Sequence[str] = (),
    given: str = '',
) -> tuple[ChapterCandidateFeedback, ...]:
    counts: dict[str, int] = {}
    for finding in findings:
        if REGISTRY.get(finding.rule).sev == 'hard':
            counts[finding.rule] = counts.get(finding.rule, 0) + 1
    rows: list[ChapterCandidateFeedback] = []
    for rule_id in sorted(counts):
        rule = REGISTRY.get(rule_id)
        rows.append(ChapterCandidateFeedback(
            rule=rule_id,
            item=rule.item,
            requirement=rule.requirement,
            instruction=rule.directive or rule.item,
            count=counts[rule_id],
            details=tuple(paragraph_details) if rule_id == 'paragraph_budget_missed' else (),
            # 校验器的完整原文不属于写手输入。只回指已下发的精确原串，既让
            # 有界修订知道具体缺了什么，也不靠 norm/近似匹配放进新文字。
            required_materials=tuple(dict.fromkeys(
                evidence
                for finding in findings if finding.rule == rule_id
                for evidence in finding.evidence
                if evidence.strip() and evidence in given
            )) if rule_id == 'verbatim_missing' else (),
        ))
    if regressions:
        rows.append(ChapterCandidateFeedback(
            rule='revision_regression',
            item='修订候选不得让旧正式稿的确定性保真项退化',
            requirement='R-7.12',
            instruction='保留旧稿已满足的原话、数值、结尾、对白与必须在场物件后再完成修订。',
            count=len(regressions),
            changes=tuple(regressions),
        ))
    return tuple(rows)


def _evaluation_payload(evaluation: ChapterCandidateEvaluation) -> dict[str, object]:
    return {
        'ordinal': evaluation.ordinal,
        'accepted_purpose': evaluation.accepted_purpose,
        'requested_model': evaluation.requested_model,
        'adopted_model': evaluation.adopted_model,
        'source_attempt_id': evaluation.source_attempt_id,
        'request_fingerprint': evaluation.request_fingerprint,
        'segments': [segment.model_dump(mode='json') for segment in evaluation.segments],
        'text_sha256': _sha256_text(evaluation.text),
        'findings': [finding.model_dump(mode='json') for finding in evaluation.findings],
        'errors': list(evaluation.errors),
        'warnings': list(evaluation.warnings),
        'regressions': list(evaluation.regressions),
        'attempts': [_canonical_sha256(item.model_dump(mode='json'))
                     for item in evaluation.attempts],
        'account': {
            **evaluation._account.counters(),
            'quote_problems': list(evaluation._account.quote_problems),
        },
        'input_sha256': evaluation._input_sha256,
    }


def _hard_rule_ids(
    evaluation: ChapterCandidateEvaluation,
) -> list[str]:
    return sorted({
        finding.rule for finding in evaluation.findings
        if REGISTRY.get(finding.rule).sev == 'hard'
    })


def _checkpoint_outcome(
    *, hard_rules: Sequence[str], regressions: Sequence[str],
) -> str:
    if hard_rules and regressions:
        return 'hard_and_regressed'
    if hard_rules:
        return 'hard'
    if regressions:
        return 'regressed'
    return 'passed'


def _chapter_checkpoint_state(
    prepared: ChapterCandidateContext,
    evaluation: ChapterCandidateEvaluation,
    *,
    accepted_turn_index: int,
) -> dict[str, object]:
    """Stage5 checkpoint 只补业务判定；wire/ledger/session 身份归通用 core。"""
    hard_rules = _hard_rule_ids(evaluation)
    regressions = list(evaluation.regressions)
    return {
        'version': 2,
        'mode': prepared.mode,
        'outcome': _checkpoint_outcome(
            hard_rules=hard_rules, regressions=regressions),
        'input_sha256': prepared._input_sha256,
        'candidate_sha256': evaluation._candidate_sha256,
        'accepted_turn_index': accepted_turn_index,
        # 当前响应已由 generic checkpoint 的 accepted_text/response_payload 保存；
        #这里只携带前序业务候选，供 revision 拒稿在 crash 后恢复首轮与终轮正文。
        'prior_attempts': [
            item.model_dump(mode='json') for item in evaluation.attempts[:-1]
        ],
        'hard_rules': hard_rules,
        'regressions': regressions,
        **({
            'reviewer_rewrite_request_sha256':
                prepared._reviewer_rewrite_request.request_sha256,
            'reviewer_rewrite_request_file_sha256':
                prepared._reviewer_rewrite_request_file_sha256,
        } if prepared._reviewer_rewrite_request is not None else {}),
    }


def _accepted_turn_index(
    source: _ChapterResponseSource,
    purpose: str,
) -> tuple[int, dict[str, Any]]:
    record = source.accepted_checkpoint(purpose)
    turn = record.get('accepted_turn_index')
    if not isinstance(turn, int) or isinstance(turn, bool) or turn < 1:
        raise ProvenanceRefused(
            f'{purpose}: accepted checkpoint 缺正整数 turn index')
    return turn, record


def _write_chapter_checkpoint(
    ctx: RunContext,
    source: _ChapterResponseSource,
    prepared: ChapterCandidateContext,
    evaluation: ChapterCandidateEvaluation,
) -> None:
    turn, acceptance = _accepted_turn_index(source, prepared.purpose)
    target = stage5_runs.write_checkpoint(
        ctx,
        chapter=prepared.ordinal,
        input_sha256=prepared._input_sha256,
        system=prepared.system,
        user=prepared.user,
        response_model=ChapterResponse,
        max_tokens=MAX_TOKENS,
        response_payload=evaluation._response.model_dump(
            by_alias=True, mode='json'),
        acceptance=acceptance,
        state_payload=_chapter_checkpoint_state(
            prepared, evaluation, accepted_turn_index=turn),
        mode=prepared.mode,
        reviewer_rewrite_request_sha256=(
            None if prepared._reviewer_rewrite_request is None
            else prepared._reviewer_rewrite_request.request_sha256),
        reviewer_rewrite_request_file_sha256=(
            prepared._reviewer_rewrite_request_file_sha256 or None),
    )
    if prepared.mode == 'reviewer_rewrite':
        digest = stage5_runs.read_checkpoint_sha256(target)
        request = prepared._reviewer_rewrite_request
        assert request is not None
        stage5_runs.record_reviewer_rewrite_execution(
            ctx, chapter=prepared.ordinal,
            request_rel=STAGE5_REVIEWER_REWRITE_REQUEST,
            request_file_sha256=prepared._reviewer_rewrite_request_file_sha256,
            request_sha256=request.request_sha256,
            input_sha256=prepared._input_sha256,
            event='checkpointed', checkpoint_sha256=digest,
        )


def _restore_chapter_checkpoint(
    ctx: RunContext,
    source: _ChapterResponseSource,
    prepared: ChapterCandidateContext,
) -> ChapterCandidateEvaluation | None:
    """零 wire 恢复最新验证后候选；恢复记录只终态化，不伪造 provider resume。"""
    restored = stage5_runs.restore_checkpoint(
        ctx,
        chapter=prepared.ordinal,
        input_sha256=prepared._input_sha256,
        system=prepared.system,
        user=prepared.user,
        response_model=ChapterResponse,
        max_tokens=MAX_TOKENS,
        validator=lambda obj, wire: _response_problem(
            obj, wire, prepared._entry),
        mode=prepared.mode,
        reviewer_rewrite_request_sha256=(
            None if prepared._reviewer_rewrite_request is None
            else prepared._reviewer_rewrite_request.request_sha256),
        reviewer_rewrite_request_file_sha256=(
            prepared._reviewer_rewrite_request_file_sha256 or None),
    )
    if restored is None:
        return None
    restore = getattr(source, 'restore_checkpoint_acceptance', None)
    if not callable(restore):
        raise ProvenanceRefused(
            f'{prepared.purpose}: agent source 缺 checkpoint acceptance 恢复口')
    restore(
        prepared.purpose,
        restored.acceptance,
        system=prepared.system,
        user=prepared.user,
        max_tokens=MAX_TOKENS,
        recovery_policy=stage5_runs.recovery_policy_of(prepared.mode),
    )
    prior_attempts = tuple(
        RejectedAttempt.model_validate(item)
        for item in restored.state_payload['prior_attempts']
    )
    evaluation = evaluate_chapter_candidate(
        source,
        prepared,
        restored.value,
        accepted_purpose=prepared.purpose,
        prior_attempts=prior_attempts,
    )
    turn = restored.acceptance.get('accepted_turn_index')
    assert isinstance(turn, int)
    expected_state = _chapter_checkpoint_state(
        prepared, evaluation, accepted_turn_index=turn)
    if expected_state != restored.state_payload:
        raise ProvenanceRefused(
            f'Stage5 第 {prepared.ordinal} 章恢复候选的当前业务判定与 checkpoint 漂移')
    if prepared.mode == 'reviewer_rewrite':
        request = prepared._reviewer_rewrite_request
        assert request is not None
        stage5_runs.record_reviewer_rewrite_execution(
            ctx, chapter=prepared.ordinal,
            request_rel=STAGE5_REVIEWER_REWRITE_REQUEST,
            request_file_sha256=prepared._reviewer_rewrite_request_file_sha256,
            request_sha256=request.request_sha256,
            input_sha256=prepared._input_sha256,
            event='checkpointed',
            checkpoint_sha256=restored.checkpoint_sha256,
        )
    return evaluation


def _record_reviewer_retry_started(
        ctx: RunContext, prepared: ChapterCandidateContext,
) -> None:
    """在每次后续 ask 前 durable 标出“旧 checkpoint 已不能自动采用”。"""
    if prepared.mode != 'reviewer_rewrite':
        return
    request = prepared._reviewer_rewrite_request
    if request is None:
        raise ProvenanceRefused('reviewer rewrite retry intent 缺 formal request')
    chain = stage5_runs.reviewer_rewrite_execution_chain(
        ctx, run_id=ctx.run_id, chapter=prepared.ordinal)
    if not chain or chain[-1].event != 'checkpointed':
        raise ProvenanceRefused(
            'reviewer rewrite 后续 wire 只能从 exact checkpointed tail 开始')
    checkpoint_sha256 = chain[-1].checkpoint_sha256
    if checkpoint_sha256 is None:
        raise ProvenanceRefused('reviewer rewrite retry intent 缺 checkpoint SHA')
    stage5_runs.record_reviewer_rewrite_execution(
        ctx, chapter=prepared.ordinal,
        request_rel=STAGE5_REVIEWER_REWRITE_REQUEST,
        request_file_sha256=prepared._reviewer_rewrite_request_file_sha256,
        request_sha256=request.request_sha256,
        input_sha256=prepared._input_sha256,
        event='retry_started', checkpoint_sha256=checkpoint_sha256,
    )


def _latest_reviewer_execution_checkpoint(
        ctx: RunContext, prepared: ChapterCandidateContext,
) -> str:
    request = prepared._reviewer_rewrite_request
    if request is None or prepared.mode != 'reviewer_rewrite':
        raise ProvenanceRefused('当前 candidate 不是 reviewer rewrite')
    chain = stage5_runs.reviewer_rewrite_execution_chain(
        ctx, run_id=ctx.run_id, chapter=prepared.ordinal)
    if not chain or chain[-1].checkpoint_sha256 is None:
        raise ProvenanceRefused(
            f'Stage5 第 {prepared.ordinal} 章 reviewer rewrite 终态前缺 checkpoint journal')
    return chain[-1].checkpoint_sha256


def _record_reviewer_delivery_prepared(
        ctx: RunContext, prepared: ChapterCandidateContext, *,
        commit_sha256: str | None,
) -> None:
    request = prepared._reviewer_rewrite_request
    if request is None:
        return
    stage5_runs.record_reviewer_rewrite_execution(
        ctx, chapter=prepared.ordinal,
        request_rel=STAGE5_REVIEWER_REWRITE_REQUEST,
        request_file_sha256=prepared._reviewer_rewrite_request_file_sha256,
        request_sha256=request.request_sha256,
        input_sha256=prepared._input_sha256,
        event='delivery_prepared',
        checkpoint_sha256=_latest_reviewer_execution_checkpoint(ctx, prepared),
        commit_sha256=commit_sha256,
    )


def _record_reviewer_terminal(
        ctx: RunContext, prepared: ChapterCandidateContext, *,
        outcome: str, rel: str, artifact: Chapter | RejectedRevision,
        commit_sha256: str | None = None,
) -> None:
    request = prepared._reviewer_rewrite_request
    if request is None:
        return
    event = {
        'formal': 'landed_formal',
        'invalid': 'landed_invalid',
        'rejected': 'landed_rejected',
    }[outcome]
    landed_file_sha256 = stage5_runs.file_sha256(ctx, rel)
    if landed_file_sha256 is None:
        raise ProvenanceRefused(
            f'reviewer rewrite {outcome} terminal 文件在记账前消失')
    stage5_runs.record_reviewer_rewrite_execution(
        ctx, chapter=prepared.ordinal,
        request_rel=STAGE5_REVIEWER_REWRITE_REQUEST,
        request_file_sha256=prepared._reviewer_rewrite_request_file_sha256,
        request_sha256=request.request_sha256,
        input_sha256=prepared._input_sha256,
        event=event,
        checkpoint_sha256=_latest_reviewer_execution_checkpoint(ctx, prepared),
        commit_sha256=commit_sha256,
        outcome=outcome,
        landed_rel=rel,
        landed_file_sha256=landed_file_sha256,
        landed_payload_sha256=_canonical_sha256(
            artifact.model_dump(by_alias=True, mode='json')),
    )


def evaluate_chapter_candidate(
    source: _ChapterResponseSource,
    prepared: ChapterCandidateContext,
    response: ChapterResponse,
    *,
    accepted_purpose: str | None = None,
    prior_attempts: Sequence[RejectedAttempt] = (),
) -> ChapterCandidateEvaluation:
    """归一并运行 revision + 正式章判据；不写盘、不归档、不结算 usage。"""
    _assert_chapter_context_memory(prepared)
    checked = ChapterResponse.model_validate(response)
    if problem := _response_problem(
            checked,
            ChatResponse(content=checked.model_dump_json(), finish_reason='stop'),
            prepared._entry):
        raise ProvenanceRefused(
            f'Stage5 第 {prepared.ordinal} 章候选绕过 wire validator：{problem}')

    # 复刻既有「响应后归一一次、正式闸前再归一一次」的字节与记账语义；
    # 两次都在纯评估里完成，agent HARD 反馈与最终写盘因而看同一份 canonical 字节。
    first, first_account = _normalize_segments(
        _ordered_segments(checked, prepared._entry), prepared._book)
    segments, second_account = _normalize_segments(first, prepared._book)
    account = first_account + second_account
    text = _segments_text(segments)
    findings, errors, warnings = _judge(
        text,
        prepared._entry,
        prepared._book,
        mat=prepared.given,
        account=account,
        segments=segments,
    )
    regressions = (
        checks.trim_regressions(
            prepared._prev_text, text, prepared._entry, prepared._book)
        if prepared.revision else [])
    if (prepared.mode == 'reviewer_rewrite'
            and text == prepared._prev_text
            and 'formal reviewer rewrite 未改变正文' not in regressions):
        regressions.append('formal reviewer rewrite 未改变正文')
    if prepared.mode == 'reviewer_rewrite':
        upper = M.target_range(prepared._entry)[1]
        chars = M.chars_of(text)
        if chars > upper:
            regressions.append(
                f'formal reviewer rewrite 仍超冻结上限：{chars}>{upper}')
    purpose = accepted_purpose or prepared.purpose
    attempts = (*tuple(prior_attempts), RejectedAttempt.model_validate({'segments': segments}))
    provisional = ChapterCandidateEvaluation(
        ordinal=prepared.ordinal,
        accepted_purpose=purpose,
        requested_model=str(source.model),
        adopted_model=_accepted_model(source, purpose),
        source_attempt_id=_accepted_attempt(source, purpose),
        request_fingerprint=_accepted_request_fingerprint(source, purpose),
        segments=tuple(ChapterSegment.model_validate(item) for item in segments),
        text=text,
        findings=tuple(findings),
        errors=tuple(errors),
        warnings=tuple(warnings),
        regressions=tuple(regressions),
        feedback=_chapter_feedback(
            findings, regressions,
            given=prepared.given,
            paragraph_details=_paragraph_budget_details(
                findings,
                [ChapterSegment.model_validate(item) for item in segments],
                prepared._entry, prepared._book)),
        attempts=attempts,
        _account=M.NormalizeAccount(
            punct_fixed=account.punct_fixed,
            quotes_fixed=account.quotes_fixed,
            paras_fixed=account.paras_fixed,
            quote_problems=list(account.quote_problems),
        ),
        _response=checked.model_copy(deep=True),
        _input_sha256=prepared._input_sha256,
        _candidate_sha256='',
    )
    return ChapterCandidateEvaluation(
        **{
            **provisional.__dict__,
            '_candidate_sha256': _canonical_sha256(
                _evaluation_payload(provisional)),
        },
    )


def _assert_chapter_evaluation_identity(
    source: _ChapterResponseSource,
    prepared: ChapterCandidateContext,
    evaluation: ChapterCandidateEvaluation,
) -> None:
    _assert_chapter_context_memory(prepared)
    if evaluation.ordinal != prepared.ordinal:
        raise ProvenanceRefused('Stage5 candidate ordinal 与冻结任务不一致')
    if evaluation.accepted_purpose != prepared.purpose:
        raise ProvenanceRefused(
            f'Stage5 第 {prepared.ordinal} 章 agent 候选 purpose 漂移：'
            f'{evaluation.accepted_purpose!r} != {prepared.purpose!r}')
    if evaluation._input_sha256 != prepared._input_sha256:
        raise ProvenanceRefused(
            f'Stage5 第 {prepared.ordinal} 章 candidate 不属于当前冻结输入')
    if str(source.model) != evaluation.requested_model:
        raise ProvenanceRefused(
            f'Stage5 第 {prepared.ordinal} 章评估后 requested model 漂移')
    if _accepted_model(source, evaluation.accepted_purpose) != evaluation.adopted_model:
        raise ProvenanceRefused(
            f'Stage5 第 {prepared.ordinal} 章评估后 adopted model 漂移')
    if _accepted_attempt(source, evaluation.accepted_purpose) != evaluation.source_attempt_id:
        raise ProvenanceRefused(
            f'Stage5 第 {prepared.ordinal} 章评估后 accepted attempt 漂移')
    if (_accepted_request_fingerprint(source, evaluation.accepted_purpose)
            != evaluation.request_fingerprint):
        raise ProvenanceRefused(
            f'Stage5 第 {prepared.ordinal} 章评估后 request fingerprint 漂移')
    if _canonical_sha256(_evaluation_payload(evaluation)) != evaluation._candidate_sha256:
        raise ProvenanceRefused(
            f'Stage5 第 {prepared.ordinal} 章 provisional evaluation 在评估后被修改')


def _record_normalization_account(
    ctx: RunContext,
    ordinal: int,
    account: M.NormalizeAccount,
) -> None:
    """把一章的归一读数并进批级 accounting；同章一次 run 只并一次。

    ``RunContext.accounting`` 是批级累计读数，same-run sweep 重放同一章时
    不得把同一份响应的读数并两遍；反过来，首次提交若在记账前就失败，
    重放仍要能补上——去重键是「章」，不是「第几次调用」。
    """
    if not ctx.once(f'stage5.normalize.{int(ordinal)}'):
        return
    for key, value in account.counters().items():
        ctx.account(key, int(ctx.accounting.get(key) or 0) + value)



def _rejected_attempts(
    attempts: Sequence[RejectedAttempt],
) -> list[dict[str, object]]:
    """RejectedRevision v2 保留首轮与终轮的原场景槽位，正文不被重新分段。"""
    selected = list(attempts)
    if len(selected) > 2:
        selected = [selected[0], selected[-1]]
    return [item.model_dump(mode='json') for item in selected]


def verify_rejected_revision_delivery(
    ctx: RunContext,
    prepared: ChapterCandidateContext,
    expected: RejectedRevision,
    *,
    rel: str,
) -> RejectedRevision:
    """拒稿证据 readback；同时证明旧正式稿没有被本提交替换。"""
    if rel != _rejected_rel(prepared.ordinal):
        raise ProvenanceRefused(
            f'Stage5 第 {prepared.ordinal} 章拒稿落点非法：{rel!r}')
    assert_chapter_candidate_current(ctx, prepared)
    store = ArtifactStore(ctx)
    landed, _raw = store.read_own(
        RejectedRevision, rel, require_current=True)
    if (landed.model_dump(mode='json')
            != expected.model_dump(mode='json')):
        raise ProvenanceRefused(
            f'Stage5 第 {prepared.ordinal} 章拒稿 readback 与终态证据不等值')
    if prepared.revision:
        formal, _formal_raw = store.read_own(
            Chapter, _rel(prepared.ordinal), require_current=True)
        if formal.text != prepared._prev_text:
            raise ProvenanceRefused(
                f'Stage5 第 {prepared.ordinal} 章拒稿提交却改动了旧正式稿')
    return landed


def verify_chapter_delivery(
    ctx: RunContext,
    prepared: ChapterCandidateContext,
    expected: Chapter,
    *,
    rel: str,
) -> Chapter:
    """正式/INVALID 提交后的 typed readback + 等值 + currentness + trim 终验。"""
    formal_rel = _rel(prepared.ordinal)
    invalid_rel = paths.invalid_of(formal_rel)
    if rel not in {formal_rel, invalid_rel}:
        raise ProvenanceRefused(
            f'Stage5 第 {prepared.ordinal} 章 readback 落点非法：{rel!r}')
    assert_chapter_candidate_current(
        ctx, prepared, after_formal_commit=rel == formal_rel)
    store = ArtifactStore(ctx)
    # typed readback 与 TrimAudit/Resolution 核验是「能不能交付」的判定读，
    # 不是本产物的实质输入：全部关进 validation_reads，任何一处开始记来源边
    # 都会立刻被 finalize 的提交前后边快照断言抓成假依赖（P0-1）。
    with _STAGE5_VALIDATION_LOCK, ctx.validation_reads():
        landed, _raw = store.read_own(Chapter, rel, require_current=True)
        expected_payload = expected.model_dump(by_alias=True, mode='json')
        landed_payload = landed.model_dump(by_alias=True, mode='json')
        if (expected_payload != landed_payload
                or _canonical_sha256(expected_payload)
                != _canonical_sha256(landed_payload)):
            raise ProvenanceRefused(
                f'Stage5 第 {prepared.ordinal} 章 readback 与本次终态候选不完全相等')
        _require_canonical_slots(landed.segments, prepared._entry)
        inspected = _inspection_account(
            [segment.model_dump() for segment in landed.segments], prepared._book)
        findings, errors, _warnings = _judge(
            landed.text,
            prepared._entry,
            prepared._book,
            mat=prepared.given,
            account=inspected,
            segments=[segment.model_dump() for segment in landed.segments],
        )
        del findings
        if rel == invalid_rel and not errors:
            raise ProvenanceRefused(
                f'Stage5 第 {prepared.ordinal} 章 INVALID readback 未复现阻断依据')
        if rel == formal_rel:
            if prepared.revision:
                revision_of = getattr(landed.provenance, 'revision_of', None)
                if revision_of != prov.fingerprint(prepared._prev_text):
                    raise ProvenanceRefused(
                        f'Stage5 第 {prepared.ordinal} 章正式 readback 未绑定冻结旧稿')
            if errors:
                raise ProvenanceRefused(
                    f'Stage5 第 {prepared.ordinal} 章模型正式稿 readback 仍命中 HARD')
            if store.exists(invalid_rel):
                raise ProvenanceRefused(
                    f'Stage5 第 {prepared.ordinal} 章正式提交后仍有 live INVALID')
            freshness = store.freshness(formal_rel)
            if freshness.state != 'fresh':
                raise ProvenanceRefused(
                    f'Stage5 第 {prepared.ordinal} 章 freshness='
                    f'{freshness.state}/{freshness.reason}；拒绝 delivered')
            expected_round = int(getattr(landed.provenance, 'round', 0) or 0)
            if store.next_round(formal_rel) != expected_round + 1:
                raise ProvenanceRefused(
                    f'Stage5 第 {prepared.ordinal} 章 readback 不是最新正式轮次')
            audit_rel = artifact_rel('stage5.trim_audit', nn=prepared.ordinal)
            resolution_rel = artifact_rel(
                'stage5.trim_resolution', nn=prepared.ordinal)
            _notes, desired_audit = _over_length_audit(
                [segment.model_dump() for segment in landed.segments],
                prepared._entry,
            )
            if desired_audit is None:
                if store.exists(audit_rel) or store.exists(resolution_rel):
                    raise ProvenanceRefused(
                        f'Stage5 第 {prepared.ordinal} 章正文未超限却残留 live trim 状态')
            else:
                if not store.exists(audit_rel):
                    raise ProvenanceRefused(
                        f'Stage5 第 {prepared.ordinal} 章超限正文缺 live TrimAudit')
                actual_audit, actual_sha256 = store.read_own_with_sha256(
                    TrimAudit, audit_rel)
                if actual_audit != desired_audit:
                    raise ProvenanceRefused(
                        f'Stage5 第 {prepared.ordinal} 章 TrimAudit 未绑定 readback 正文')
                if store.exists(resolution_rel):
                    from novel.stages.stage5 import trim_resolution
                    if not trim_resolution.matches_current_audit(
                        store,
                        actual_audit,
                        actual_sha256,
                        ordinal=prepared.ordinal,
                    ):
                        raise ProvenanceRefused(
                            f'Stage5 第 {prepared.ordinal} 章 trim resolution 已过期')
        elif not errors:
            raise ProvenanceRefused(
                f'Stage5 第 {prepared.ordinal} 章 INVALID readback 已无 HARD；'
                '不能把可正式化候选留在问题路径')
        return landed


def _formal_artifact(
    ctx: RunContext,
    prepared: ChapterCandidateContext,
    evaluation: ChapterCandidateEvaluation,
    *,
    round_number: int,
    resume_claim: bool = False,
) -> Chapter:
    """从已 checkpoint 的终态候选铸一份正式 Chapter；调用即结算本章 usage。

    ``resume_claim=True`` 只由 same-run deterministic sweep 的重放传入：第一次
    提交可能在 usage 结算之后、commit journal 落盘之前失败，重放时按同一
    checkpoint/候选接续那笔结算，而不是重新claim或伪造零 usage。
    """
    extra: dict[str, Any] = {
        'round': round_number,
        'request_fingerprint': evaluation.request_fingerprint,
    }
    if prepared.mode == 'reviewer_rewrite':
        request = prepared._reviewer_rewrite_request
        if request is None:
            raise ProvenanceRefused('reviewer rewrite formal 缺 request')
        extra.update(
            revision_of=prov.fingerprint(prepared._prev_text),
            review=None,
            fixes=None,
            reviewer_rewrite_request_sha256=request.request_sha256,
        )
    elif prepared.revision:
        extra.update(
            revision_of=prov.fingerprint(prepared._prev_text),
            review=prepared._review_fp,
            fixes=[list(item) for item in prepared._fixes],
        )
    prompt_record = {'SYS': prepared.system}
    prompt_evidence = (
        {**prompt_record,
         'REVIEWER_REWRITE': _REVIEWER_REWRITE_PROMPT_CONTRACT}
        if prepared.mode == 'reviewer_rewrite'
        else ({**prompt_record, 'REVISE': prompts.REVISE_TAIL}
              if prepared.revision else prompt_record))
    provenance = ctx.llm_provenance_for_chapter(
        prepared.ordinal,
        RoundLlmProvenance,
        prompts=prompt_evidence,
        model=evaluation.requested_model,
        adopted_models=[evaluation.adopted_model],
        resume_claim=resume_claim,
        **extra,
    )
    return _chapter_record(
        prepared._entry,
        [segment.model_dump() for segment in evaluation.segments],
        provenance=provenance,
    )


def _formal_finalization_payload(
    prepared: ChapterCandidateContext,
    evaluation: ChapterCandidateEvaluation,
    artifact: Chapter,
) -> dict[str, object]:
    """可在 crash 后由 typed formal readback + checkpoint 候选精确重算的投影。"""
    _notes, desired_audit = _over_length_audit(
        [segment.model_dump() for segment in artifact.segments],
        prepared._entry,
    )
    source = artifact.provenance
    return {
        'version': 1,
        'artifact': _rel(prepared.ordinal),
        'chapter': prepared.ordinal,
        'mode': prepared.mode,
        'input_sha256': prepared._input_sha256,
        'candidate_sha256': evaluation._candidate_sha256,
        'source_attempt_id': evaluation.source_attempt_id,
        'request_fingerprint': evaluation.request_fingerprint,
        'round': source.round,
        'revision_of': source.revision_of,
        'review': source.review,
        'fixes': (
            None if source.fixes is None
            else [list(item) for item in source.fixes]),
        'reviewer_rewrite_request_sha256':
            source.reviewer_rewrite_request_sha256,
        'reviewer_rewrite_request_file_sha256': (
            prepared._reviewer_rewrite_request_file_sha256 or None),
        'trim_audit': (
            None if desired_audit is None
            else desired_audit.model_dump(by_alias=True, mode='json')),
    }


def _journal_landed_primary(
    store: ArtifactStore,
    record: Mapping[str, Any],
    *,
    rel: str,
) -> Chapter | None:
    """若 live formal 已是 journal 目标则读回；baseline 旧稿返回 None。"""
    if not store.exists(rel):
        return None
    current, _raw = store.read_own(Chapter, rel, require_current=False)
    payload = current.model_dump(by_alias=True, mode='json')
    return current if stage5_runs.verify_commit_primary(record, payload) else None


def _trim_preflight_expectations(
    ctx: RunContext,
    store: ArtifactStore,
    prepared: ChapterCandidateContext,
    artifact: Chapter,
) -> tuple[TrimAudit | None, str | None, str | None]:
    """返回 desired audit，以及当前已经安全等于目标的 audit/resolution file SHA。

    只服务写前/写后核验：整段读进 ``validation_reads``，TrimAudit/Resolution
    的字节读不得冒充本产物的实质输入。
    """
    with _STAGE5_VALIDATION_LOCK, ctx.validation_reads():
        return _trim_preflight_expectations_locked(ctx, store, prepared, artifact)


def _trim_preflight_expectations_locked(
    ctx: RunContext,
    store: ArtifactStore,
    prepared: ChapterCandidateContext,
    artifact: Chapter,
) -> tuple[TrimAudit | None, str | None, str | None]:
    _notes, desired_audit = _over_length_audit(
        [segment.model_dump() for segment in artifact.segments],
        prepared._entry,
    )
    audit_rel = artifact_rel('stage5.trim_audit', nn=prepared.ordinal)
    resolution_rel = artifact_rel('stage5.trim_resolution', nn=prepared.ordinal)
    audit_sha: str | None = None
    current_audit: TrimAudit | None = None
    current_audit_sha: str | None = None
    if desired_audit is not None and store.exists(audit_rel):
        try:
            candidate, candidate_sha = store.read_own_with_sha256(
                TrimAudit, audit_rel)
        except ArtifactUnreadable:
            pass
        else:
            if candidate == desired_audit:
                current_audit = candidate
                current_audit_sha = candidate_sha
                audit_sha = stage5_runs.file_sha256(ctx, audit_rel)

    resolution_sha: str | None = None
    if (current_audit is not None and current_audit_sha is not None
            and store.exists(resolution_rel)):
        from novel.stages.stage5 import trim_resolution
        if trim_resolution.matches_current_audit(
            store,
            current_audit,
            current_audit_sha,
            ordinal=prepared.ordinal,
        ):
            resolution_sha = stage5_runs.file_sha256(ctx, resolution_rel)
    return desired_audit, audit_sha, resolution_sha


def _preflight_formal_commit_targets(
    ctx: RunContext,
    store: ArtifactStore,
    prepared: ChapterCandidateContext,
    artifact: Chapter,
    record: Mapping[str, Any],
) -> None:
    """任何写前证明五个 live 路径都仍是 baseline 或本 journal 的安全目标。"""
    with _STAGE5_VALIDATION_LOCK, ctx.validation_reads():
        _preflight_formal_commit_targets_locked(
            ctx, store, prepared, artifact, record)


def _preflight_formal_commit_targets_locked(
    ctx: RunContext,
    store: ArtifactStore,
    prepared: ChapterCandidateContext,
    artifact: Chapter,
    record: Mapping[str, Any],
) -> None:
    rel = _rel(prepared.ordinal)
    invalid_rel = paths.invalid_of(rel)
    rejected_rel = _rejected_rel(prepared.ordinal)
    audit_rel = artifact_rel('stage5.trim_audit', nn=prepared.ordinal)
    resolution_rel = artifact_rel('stage5.trim_resolution', nn=prepared.ordinal)
    primary_sha: str | None = None
    landed = _journal_landed_primary(store, record, rel=rel)
    if landed is not None:
        primary_sha = stage5_runs.file_sha256(ctx, rel)
    desired_audit, audit_sha, resolution_sha = _trim_preflight_expectations(
        ctx, store, prepared, artifact)
    stage5_runs.target_state(
        ctx, prepared.ordinal, record, rel,
        expected_file_sha256=primary_sha, mode=prepared.mode)
    stage5_runs.target_state(
        ctx, prepared.ordinal, record, invalid_rel, expected_absent=True,
        mode=prepared.mode)
    stage5_runs.target_state(
        ctx, prepared.ordinal, record, rejected_rel, expected_absent=True,
        mode=prepared.mode)
    stage5_runs.target_state(
        ctx, prepared.ordinal, record, audit_rel,
        expected_file_sha256=audit_sha,
        expected_absent=desired_audit is None, mode=prepared.mode)
    stage5_runs.target_state(
        ctx, prepared.ordinal, record, resolution_rel,
        expected_file_sha256=resolution_sha,
        expected_absent=resolution_sha is None, mode=prepared.mode)


def _verify_formal_commit_targets(
    ctx: RunContext,
    store: ArtifactStore,
    prepared: ChapterCandidateContext,
    artifact: Chapter,
    record: Mapping[str, Any],
) -> None:
    """readback 后把 journal 的全部 live touched path 收敛到唯一 post state。"""
    with _STAGE5_VALIDATION_LOCK, ctx.validation_reads():
        _verify_formal_commit_targets_locked(
            ctx, store, prepared, artifact, record)


def _verify_formal_commit_targets_locked(
    ctx: RunContext,
    store: ArtifactStore,
    prepared: ChapterCandidateContext,
    artifact: Chapter,
    record: Mapping[str, Any],
) -> None:
    rel = _rel(prepared.ordinal)
    invalid_rel = paths.invalid_of(rel)
    rejected_rel = _rejected_rel(prepared.ordinal)
    audit_rel = artifact_rel('stage5.trim_audit', nn=prepared.ordinal)
    resolution_rel = artifact_rel('stage5.trim_resolution', nn=prepared.ordinal)
    primary_sha = stage5_runs.file_sha256(ctx, rel)
    if primary_sha is None:
        raise ProvenanceRefused(
            f'Stage5 第 {prepared.ordinal} 章正式 readback 后文件消失')
    desired_audit, audit_sha, resolution_sha = _trim_preflight_expectations(
        ctx, store, prepared, artifact)
    states = [
        stage5_runs.target_state(
            ctx, prepared.ordinal, record, rel,
            expected_file_sha256=primary_sha, mode=prepared.mode),
        stage5_runs.target_state(
            ctx, prepared.ordinal, record, invalid_rel,
            expected_absent=True, mode=prepared.mode),
        stage5_runs.target_state(
            ctx, prepared.ordinal, record, rejected_rel,
            expected_absent=True, mode=prepared.mode),
        stage5_runs.target_state(
            ctx, prepared.ordinal, record, audit_rel,
            expected_file_sha256=audit_sha,
            expected_absent=desired_audit is None, mode=prepared.mode),
        stage5_runs.target_state(
            ctx, prepared.ordinal, record, resolution_rel,
            expected_file_sha256=resolution_sha,
            expected_absent=resolution_sha is None, mode=prepared.mode),
    ]
    if any(state != 'expected' for state in states):
        raise ProvenanceRefused(
            f'Stage5 第 {prepared.ordinal} 章正式事务仍有 live touched path 留在 baseline')


def _assert_commit_edges_stable(
    ctx: RunContext,
    snapshot: Mapping[str, InputEdge],
    ordinal: int,
    boundary: str,
) -> None:
    """提交链前后断言来源边集合未变化。

    正式提交段里的所有核验读都必须是 validation_reads（判定读，不是实质输入）；
    任何一处开始记边，都会在下一章的 provenance 里冒充成假依赖——这正是
    「前章自有审计污染后章 provenance」那类静默事故的形状。发现即关闭式失败，
    而不是把污染的账落盘。
    """
    current = ctx.edges
    if current != dict(snapshot):
        added = sorted(set(current) - set(snapshot))
        removed = sorted(set(snapshot) - set(current))
        changed = sorted(
            key for key in set(current) & set(snapshot)
            if current[key] != snapshot[key])
        raise ProvenanceRefused(
            f'Stage5 第 {ordinal} 章提交链 {boundary} 处来源边漂移：'
            f'新增={added} 消失={removed} 变更={changed}；'
            '核验读必须走 validation_reads，不得把自有审计或他章状态记成本章输入')


def _finalize_formal_agent_candidate(
    ctx: RunContext,
    prepared: ChapterCandidateContext,
    evaluation: ChapterCandidateEvaluation,
    *,
    diagnostics: _CommitDiagnostics | None = None,
    resume_claim: bool = False,
) -> ChapterCandidateDelivery:
    """逐章所有权锁内补完 prepared 尾窗，直至 typed delivery + adoption + committed。

    ``resume_claim`` 语义见 :func:`_formal_artifact`；``diagnostics`` 记录本调用
    停在 ``STAGE5_COMMIT_PHASES`` 的哪一段。本函数**零 wire**：不建 driver、
    不探针、不发任何模型请求，因此它同时是首次提交与 same-run sweep 的唯一重放体。
    """
    store = ArtifactStore(ctx)
    rel = _rel(prepared.ordinal)
    rejected_rel = _rejected_rel(prepared.ordinal)
    audit_rel = artifact_rel('stage5.trim_audit', nn=prepared.ordinal)
    resolution_rel = artifact_rel('stage5.trim_resolution', nn=prepared.ordinal)
    with stage5_runs.commit_ownership(ctx.novel_dir, prepared.ordinal):
        with _commit_phase(diagnostics, 'prepare_commit'):
            _assert_finalization_current(ctx, prepared)
            stage5_runs.refuse_foreign_commit_reservations(
                ctx, prepared.ordinal, mode=prepared.mode)
            record = (
                stage5_runs.committed(
                    ctx, prepared.ordinal, mode=prepared.mode)
                or stage5_runs.prepared(
                    ctx, prepared.ordinal, mode=prepared.mode))
            artifact = (
                None if record is None
                else _journal_landed_primary(store, record, rel=rel))
            if artifact is None:
                artifact = _formal_artifact(
                    ctx,
                    prepared,
                    evaluation,
                    round_number=store.next_round(rel),
                    resume_claim=resume_claim,
                )
            payload = artifact.model_dump(by_alias=True, mode='json')
            finalization = _formal_finalization_payload(
                prepared, evaluation, artifact)
            if record is None:
                _assert_finalization_current(ctx, prepared)
                record = stage5_runs.prepare_commit(
                    ctx,
                    chapter=prepared.ordinal,
                    primary_payload=payload,
                    finalization_payload=finalization,
                    attempt_id=evaluation.source_attempt_id,
                    mode=prepared.mode,
                )
            elif (not stage5_runs.verify_commit_primary(record, payload)
                  or not stage5_runs.verify_commit_finalization(
                      record, finalization)):
                raise ProvenanceRefused(
                    f'Stage5 第 {prepared.ordinal} 章 prepared journal 已绑定另一份候选')
            if prepared.mode == 'reviewer_rewrite' and record.get('state') == 'prepared':
                commit_sha256 = record.get('commit_sha256')
                if not isinstance(commit_sha256, str):
                    raise ProvenanceRefused('reviewer rewrite commit 缺 self SHA')
                _record_reviewer_delivery_prepared(
                    ctx, prepared, commit_sha256=commit_sha256)
        # 候选与 provenance 此刻已完全铸定；从这里到提交结束，来源边不得再变。
        edges_snapshot = ctx.edges

        if record.get('state') == 'committed':
            with _commit_phase(diagnostics, 'adoption'):
                purpose, attempt_id = stage5_runs.restore_commit_adoption(
                    ctx, prepared.ordinal, record, mode=prepared.mode)
                if (purpose != prepared.purpose
                        or attempt_id != evaluation.source_attempt_id):
                    raise ProvenanceRefused(
                        f'Stage5 第 {prepared.ordinal} 章 committed adoption 与候选漂移')
            with _commit_phase(diagnostics, 'typed_readback'):
                landed = verify_chapter_delivery(
                    ctx, prepared, artifact, rel=rel)
            with _commit_phase(diagnostics, 'committed_verify'):
                finalization = _formal_finalization_payload(
                    prepared, evaluation, landed)
                stage5_runs.verify_committed_delivery(
                    ctx,
                    prepared.ordinal,
                    record,
                    primary_payload=landed.model_dump(
                        by_alias=True, mode='json'),
                    finalization_payload=finalization,
                    mode=prepared.mode,
                )
                trim_notes, _desired = _over_length_audit(
                    [segment.model_dump() for segment in landed.segments],
                    prepared._entry,
                )
                _record_normalization_account(
                    ctx, prepared.ordinal, evaluation._account)
                _assert_commit_edges_stable(
                    ctx, edges_snapshot, prepared.ordinal, 'committed 恢复收尾')
                _record_reviewer_terminal(
                    ctx, prepared, outcome='formal', rel=rel,
                    artifact=landed,
                    commit_sha256=str(record['commit_sha256']))
            return ChapterCandidateDelivery(
                outcome='formal', where=str(store.path(rel)), artifact=landed,
                findings=evaluation.findings,
                trim_notes=tuple(trim_notes),
            )

        with _commit_phase(diagnostics, 'formal_write'):
            _preflight_formal_commit_targets(
                ctx, store, prepared, artifact, record)
            _assert_finalization_current(ctx, prepared)
            with store.history_transaction(
                    rel, rejected_rel, audit_rel, resolution_rel):
                if store.exists(rejected_rel):
                    store.archive_round(rejected_rel)

                def commit(canonical_rel: str, candidate: Chapter) -> Any:
                    current = _journal_landed_primary(
                        store, record, rel=canonical_rel)
                    if current is not None:
                        # crash 可能发生在 formal replace 与 INVALID 退休之间；跳过
                        # 新轮写入，只补同名问题稿的确定性尾巴。
                        if store.exists(paths.invalid_of(canonical_rel)):
                            store.retire_invalid(canonical_rel)
                        return store.path(canonical_rel)
                    _assert_finalization_current(ctx, prepared)
                    return store.write_round(canonical_rel, candidate)

                where_path, trim_notes = _commit_canonical_chapter(
                    store,
                    artifact,
                    expected_ordinal=prepared.ordinal,
                    entry=prepared._entry,
                    commit=commit,
                )
                # typed readback 留在回滚域内：核验失败 = 本次写盘整体回滚，
                # live 停在 baseline，sweep 才能按 prepared journal 原样重放。
                with _commit_phase(diagnostics, 'typed_readback'):
                    landed = verify_chapter_delivery(
                        ctx, prepared, artifact, rel=rel)
                    _verify_formal_commit_targets(
                        ctx, store, prepared, landed, record)

        with _commit_phase(diagnostics, 'adoption'):
            _record_normalization_account(
                ctx, prepared.ordinal, evaluation._account)
            assert_chapter_candidate_current(
                ctx, prepared, after_formal_commit=True)
            fingerprint = prov.projection_fingerprint(
                landed.model_dump(by_alias=True, mode='json'))
            ctx.ledger.note_adoption(
                sid=ctx.sid,
                node=ctx.node,
                purpose=evaluation.accepted_purpose,
                attempt_id=evaluation.source_attempt_id,
                artifact=rel,
                artifact_fingerprint=fingerprint,
                chapter=prepared.ordinal,
            )
            assert_chapter_candidate_current(
                ctx, prepared, after_formal_commit=True)
        with _commit_phase(diagnostics, 'mark_committed'):
            landed_payload = landed.model_dump(by_alias=True, mode='json')
            finalization = _formal_finalization_payload(
                prepared, evaluation, landed)
            stage5_runs.mark_committed(
                ctx,
                chapter=prepared.ordinal,
                primary_payload=landed_payload,
                finalization_payload=finalization,
                mode=prepared.mode,
            )
        with _commit_phase(diagnostics, 'committed_verify'):
            committed = stage5_runs.committed(
                ctx, prepared.ordinal, mode=prepared.mode)
            if committed is None:
                raise ProvenanceRefused(
                    f'Stage5 第 {prepared.ordinal} 章 mark committed 后 journal 消失')
            stage5_runs.verify_committed_delivery(
                ctx,
                prepared.ordinal,
                committed,
                primary_payload=landed_payload,
                finalization_payload=finalization,
                mode=prepared.mode,
            )
            _assert_commit_edges_stable(
                ctx, edges_snapshot, prepared.ordinal, 'committed 终验')
            _record_reviewer_terminal(
                ctx, prepared, outcome='formal', rel=rel,
                artifact=landed,
                commit_sha256=str(committed['commit_sha256']))
    return ChapterCandidateDelivery(
        outcome='formal', where=str(where_path), artifact=landed,
        findings=evaluation.findings,
        trim_notes=tuple(trim_notes),
    )


def finalize_chapter_candidate(
    ctx: RunContext,
    source: _ChapterResponseSource,
    prepared: ChapterCandidateContext,
    evaluation: ChapterCandidateEvaluation,
    *,
    diagnostics: _CommitDiagnostics | None = None,
    same_run_replay: bool = False,
) -> ChapterCandidateDelivery:
    """终态只提交一次；中间 agent HARD 候选不得调用本函数。

    ``same_run_replay=True`` 只由 ``_run_group_parallel`` 的 same-run
    deterministic sweep 传入：表示这是同一 run 内、同一 checkpoint 候选的
    有界重放（usage 结算接续、归一记账去重），不是一次新的生成尝试。
    本函数零 wire，重放不会产生任何模型请求。
    """
    with _reviewer_unit_ownership(ctx, prepared):
        return _finalize_chapter_candidate_owned(
            ctx, source, prepared, evaluation,
            diagnostics=diagnostics, same_run_replay=same_run_replay)


def _reviewer_unit_ownership(ctx: RunContext, prepared: ChapterCandidateContext):
    """线程本地 lease 不继承；worker/提交线程分别持有本章执行身份。"""
    request = prepared._reviewer_rewrite_request
    return (nullcontext() if request is None
            else stage5_runs.reviewer_rewrite_execution_ownership(
                ctx.novel_dir, request.request_sha256))


def _assert_finalization_current(ctx: RunContext, prepared: ChapterCandidateContext) -> None:
    """同 run sweep 可收口自己已替换的 exact WAL 主稿，不能接受外部改稿。"""
    after_formal = False
    if prepared.mode == 'reviewer_rewrite':
        record = (stage5_runs.committed(ctx, prepared.ordinal, mode=prepared.mode)
                  or stage5_runs.prepared(ctx, prepared.ordinal, mode=prepared.mode))
        if record is not None:
            landed = _journal_landed_primary(ArtifactStore(ctx), record, rel=_rel(prepared.ordinal))
            if landed is not None:
                request = prepared._reviewer_rewrite_request
                if (request is None or not isinstance(landed.provenance, RoundLlmProvenance)
                        or landed.provenance.reviewer_rewrite_request_sha256 != request.request_sha256):
                    raise ProvenanceRefused('reviewer rewrite WAL 主稿不属于当前执行单元')
                after_formal = True
    assert_chapter_candidate_current(ctx, prepared, after_formal_commit=after_formal)


def _finalize_chapter_candidate_owned(
    ctx: RunContext, source: _ChapterResponseSource,
    prepared: ChapterCandidateContext, evaluation: ChapterCandidateEvaluation, *,
    diagnostics: _CommitDiagnostics | None = None, same_run_replay: bool = False,
) -> ChapterCandidateDelivery:
    if ctx.node != 'stage5.write':
        raise ValueError(
            f'finalize_chapter_candidate 只接受 stage5.write，实得 {ctx.node!r}')
    _assert_chapter_evaluation_identity(source, prepared, evaluation)
    _assert_finalization_current(ctx, prepared)
    # 同一 response 再走一次纯函数链，先证明判据确定，再推进 chapter usage settlement。
    repeated = evaluate_chapter_candidate(
        source,
        prepared,
        evaluation._response,
        accepted_purpose=evaluation.accepted_purpose,
        prior_attempts=evaluation.attempts[:-1],
    )
    if repeated._candidate_sha256 != evaluation._candidate_sha256:
        raise ProvenanceRefused(
            f'Stage5 第 {prepared.ordinal} 章 provisional/final 评估不一致')

    store = ArtifactStore(ctx)
    if evaluation.regressions:
        why = list(evaluation.regressions)
        if len(evaluation.attempts) > 2:
            why.append(
                f'agent 共返回 {len(evaluation.attempts)} 份退化候选；'
                'RejectedRevision 契约只保留首轮与终轮正文，其余轮次只留会话/成本审计')
        artifact = RejectedRevision(
            output_ordinal=prepared.ordinal,
            why=why,
            fixes=[list(item) for item in prepared._fixes],
            attempts=_rejected_attempts(evaluation.attempts),
            reviewer_rewrite_request_sha256=(
                None if prepared._reviewer_rewrite_request is None
                else prepared._reviewer_rewrite_request.request_sha256),
        )
        rel = _rejected_rel(prepared.ordinal)
        with stage5_runs.commit_ownership(ctx.novel_dir, prepared.ordinal):
            _record_reviewer_delivery_prepared(
                ctx, prepared, commit_sha256=None)
            if store.exists(rel):
                prior, _sha = store.read_own(RejectedRevision, rel)
            else:
                prior = None
            where = str(
                store.path(rel) if prior == artifact
                else store.write_round(rel, artifact))
            landed = verify_rejected_revision_delivery(
                ctx, prepared, artifact, rel=rel)
            _record_reviewer_terminal(
                ctx, prepared, outcome='rejected', rel=rel,
                artifact=landed)
        return ChapterCandidateDelivery(
            outcome='rejected_revision', where=where, artifact=landed,
            errors=tuple(evaluation.regressions),
            findings=evaluation.findings,
        )

    if not evaluation.errors:
        return _finalize_formal_agent_candidate(
            ctx, prepared, evaluation,
            diagnostics=diagnostics, resume_claim=same_run_replay)

    rel = _rel(prepared.ordinal)
    round_number = store.next_round(rel)
    extra: dict[str, Any] = {
        'round': round_number,
        'request_fingerprint': evaluation.request_fingerprint,
    }
    if prepared.mode == 'reviewer_rewrite':
        request = prepared._reviewer_rewrite_request
        if request is None:
            raise ProvenanceRefused('reviewer rewrite INVALID 缺 request')
        extra.update(
            revision_of=prov.fingerprint(prepared._prev_text),
            review=None,
            fixes=None,
            reviewer_rewrite_request_sha256=request.request_sha256,
        )
    elif prepared.revision:
        extra.update(
            revision_of=prov.fingerprint(prepared._prev_text),
            review=prepared._review_fp,
            fixes=[list(item) for item in prepared._fixes],
        )
    prompt_record = {'SYS': prepared.system}
    prompt_evidence = (
        {**prompt_record,
         'REVIEWER_REWRITE': _REVIEWER_REWRITE_PROMPT_CONTRACT}
        if prepared.mode == 'reviewer_rewrite'
        else ({**prompt_record, 'REVISE': prompts.REVISE_TAIL}
              if prepared.revision else prompt_record))
    provenance = ctx.llm_provenance_for_chapter(
        prepared.ordinal,
        RoundLlmProvenance,
        prompts=prompt_evidence,
        model=evaluation.requested_model,
        adopted_models=[evaluation.adopted_model],
        resume_claim=same_run_replay,
        **extra,
    )
    _record_normalization_account(ctx, prepared.ordinal, evaluation._account)
    segments = [segment.model_dump() for segment in evaluation.segments]
    artifact = _chapter_record(
        prepared._entry,
        segments,
        provenance=provenance,
    )
    rejected_rel = _rejected_rel(prepared.ordinal)
    audit_rel = artifact_rel('stage5.trim_audit', nn=prepared.ordinal)
    resolution_rel = artifact_rel('stage5.trim_resolution', nn=prepared.ordinal)
    with stage5_runs.commit_ownership(ctx.novel_dir, prepared.ordinal):
        _record_reviewer_delivery_prepared(
            ctx, prepared, commit_sha256=None)
        with store.history_transaction(
                rel, rejected_rel, audit_rel, resolution_rel):
            if store.exists(rejected_rel):
                store.archive_round(rejected_rel)
            invalid_rel = paths.invalid_of(rel)
            if store.exists(invalid_rel):
                prior, _sha = store.read_own(Chapter, invalid_rel)
            else:
                prior = None
            where = str(
                store.path(invalid_rel) if prior == artifact
                else store.write_invalid(rel, artifact, gates_skipped=[]))
            landed = verify_chapter_delivery(
                ctx, prepared, artifact, rel=invalid_rel)
        _record_reviewer_terminal(
            ctx, prepared, outcome='invalid', rel=invalid_rel,
            artifact=landed)
    return ChapterCandidateDelivery(
        outcome='invalid', where=where, artifact=landed,
        findings=evaluation.findings,
        errors=evaluation.errors,
    )


def run_write(ctx: RunContext, client: LlmClient,
              chapters: str | None = None) -> CheckReport:
    """默认付费路：逐章一次调用。**一章失败不中断整批。**

    ⚠ 连贯性靠**上一章的计划**传递，**不给上一章的成稿**——否则写手会顺着自己刚写的续，
    偏差沿章累积，章与章也变成串行依赖（`prompts.prev_context`）。
    """
    # 现役 agent profile 有逐章 checkpoint 主体与锁内 currentness 复核，模型请求不占锁；
    # legacy LlmClient 没有可复核的候选 journal，只能把它从盘面快照到最后一章提交
    # 整段纳入书锁，不能在长请求后盲盖并发人工稿。
    ownership = (
        nullcontext() if _uses_agent_sessions(ctx)
        else stage5_runs.book_commit_ownership(ctx.novel_dir)
    )
    with ownership:
        store = ArtifactStore(ctx)
        require_canonical_layout(store)
        # 生产入口本身必须挡 foreign prepared。CLI/build_node_agent 只是更早反馈；
        # legacy 形状先由零调用 canonical-layout 闸给出更精确终态，之后仍早于
        # load_book、_run_group 以及 direct Stage5WriteAgent 的 driver/probe/session。
        stage5_runs.refuse_foreign_prepared_before_wire(ctx, chapters)
        book = review.load_book(ctx, store)
        _reconcile_legacy_chapter_deliveries(ctx, store, book, client)
        todo = _chapters_of(store, book, chapters)
        if not todo:
            raise TerminalState(
                '这一次没有要写的章：计划里的每一章都已经有 canonical 成稿了。'
                '⚠ 这**不是**「没问题」——要重写某一章就用 `--chapters N` 明说')
        return _run_group(ctx, store, book, todo, client, revise=None)


def _path_sha256(path: os.PathLike[str] | str) -> str:
    try:
        return hashlib.sha256(Path(path).read_bytes()).hexdigest()
    except OSError as exc:
        raise ProvenanceRefused(f'prepared recovery sidecar {path} 不可读：{exc}') from exc


def _historical_recovery_unit(
    ctx: RunContext,
    book: M.Book,
    *,
    source_run_id: str,
    ordinal: int,
) -> tuple[PreparedRecoveryChapter, ChapterCandidateContext, ChapterCandidateEvaluation]:
    """把一章 source prepared 经现有 historical + current 纯函数链重判。"""
    entry = book.chapters.get(ordinal)
    if entry is None:
        raise ProvenanceRefused(
            f'Stage5 source prepared 第 {ordinal} 章已不在当前计划；拒绝跨计划采用')
    prepared = prepare_chapter_candidate(entry, book, revision=None)
    assert_chapter_candidate_current(ctx, prepared)
    historical = stage5_runs.restore_historical_prepared(
        ctx,
        source_run_id=source_run_id,
        chapter=ordinal,
        input_sha256=prepared._input_sha256,
        system=prepared.system,
        user=prepared.user,
        response_model=ChapterResponse,
        max_tokens=MAX_TOKENS,
        validator=lambda obj, wire: _response_problem(obj, wire, prepared._entry),
    )
    acceptance = historical.historical.checkpoint.acceptance
    run_record = historical.historical.run_record
    profile = run_record['request_policy']['profile']
    source = _HistoricalCheckpointSource(
        model=str(profile['model']), purpose=prepared.purpose,
        acceptance=acceptance)
    prior = tuple(
        RejectedAttempt.model_validate(item)
        for item in historical.historical.checkpoint.state_payload['prior_attempts']
    )
    evaluation = evaluate_chapter_candidate(
        source,
        prepared,
        historical.historical.checkpoint.value,
        accepted_purpose=prepared.purpose,
        prior_attempts=prior,
    )
    _assert_chapter_evaluation_identity(source, prepared, evaluation)
    if not evaluation.passed:
        raise ProvenanceRefused(
            f'Stage5 第 {ordinal} 章历史 checkpoint 在当前代码下未通过：'
            f'HARD={list(evaluation.errors)} regressions={list(evaluation.regressions)}')
    source_dir = paths.stage5_chapter_run_path(
        ctx.novel_dir, source_run_id, ordinal)
    run_path = source_dir / 'run.json'
    commit_path = source_dir / 'committed.json'
    checkpoint_path = (source_dir / 'checkpoints'
                       / f'rev-{historical.historical.checkpoint.revision:04d}.json')
    commit = historical.commit
    run_identity_sha = str(run_record['identity_sha256'])
    run_file_sha = _path_sha256(run_path)
    commit_sha = str(commit['commit_sha256'])
    commit_file_sha = _path_sha256(commit_path)
    checkpoint_sha = historical.historical.checkpoint.checkpoint_sha256
    checkpoint_file_sha = _path_sha256(checkpoint_path)
    response_sha = str(acceptance['ledger_response_sha256'])
    adoption_id = stage5_runs.source_adoption_id(
        sid=ctx.sid, source_run_id=source_run_id, chapter=ordinal,
        source_run_identity_sha256=run_identity_sha,
        source_run_file_sha256=run_file_sha,
        source_commit_sha256=commit_sha,
        source_commit_file_sha256=commit_file_sha,
        source_checkpoint_sha256=checkpoint_sha,
        source_checkpoint_file_sha256=checkpoint_file_sha,
        source_response_sha256=response_sha,
    )
    touched = tuple(sorted(stage5_runs.touched_paths(ordinal)))
    row = PreparedRecoveryChapter(
        output_ordinal=ordinal,
        mode='write',
        purpose=prepared.purpose,
        source_code=str(run_record['code']),
        source_run_identity_sha256=run_identity_sha,
        source_run_file_sha256=run_file_sha,
        source_commit_sha256=commit_sha,
        source_commit_file_sha256=commit_file_sha,
        source_checkpoint_sha256=checkpoint_sha,
        source_checkpoint_file_sha256=checkpoint_file_sha,
        source_checkpoint_revision=historical.historical.checkpoint.revision,
        source_attempt_id=evaluation.source_attempt_id,
        source_response_sha256=response_sha,
        source_request_fingerprint=evaluation.request_fingerprint,
        source_session_run_id=str(acceptance['session_run_id']),
        source_provider_session_id=str(acceptance['provider_session_id']),
        source_adoption_id=adoption_id,
        input_sha256=prepared._input_sha256,
        system_sha256=_sha256_text(prepared.system),
        user_sha256=_sha256_text(prepared.user),
        response_schema_sha256=_canonical_sha256(
            ChapterResponse.model_json_schema(by_alias=True)),
        response_model=ChapterResponse.__name__,
        requested_output_token_ceiling=MAX_TOKENS,
        profile=profile,
        candidate_sha256=evaluation._candidate_sha256,
        touched_paths=touched,
        source_baselines={
            rel: commit['baselines'][rel] for rel in touched
        },
        current_baselines={
            rel: stage5_runs.file_sha256(ctx, rel) for rel in touched
        },
        hard_rules=tuple(evaluation.errors),
        regressions=tuple(evaluation.regressions),
    )
    return row, prepared, evaluation


def build_prepared_recovery_workpack(
        ctx: RunContext, source_run_id: str,
) -> PreparedRecoveryWorkpack:
    """纯读构造绑定 source prepared 与 current producer 的整书工作包。"""
    from novel.run_identity import check_run_id

    source = check_run_id(source_run_id)
    if ctx.node != 'stage5.write' or ctx.paid:
        raise ProvenanceRefused('prepared recovery workpack 只属于 free stage5.write run')
    if ctx.executor_profile is not None:
        raise ProvenanceRefused('prepared recovery workpack 禁止 executor profile/driver')
    existing = stage5_runs.read_recovery_manifest(ctx)
    if existing is not None:
        if existing.workpack.source_run_id != source:
            raise ProvenanceRefused('当前 target run manifest 已绑定另一 source run')
        stage5_runs.require_workpack_subject(existing.workpack)
        return existing.workpack
    store = ArtifactStore(ctx)
    require_canonical_layout(store)
    book = review.load_book(ctx, store)
    unresolved = stage5_runs.unresolved_prepared_envelopes(ctx, source)
    rows: list[PreparedRecoveryChapter] = []
    for ordinal in sorted(unresolved):
        row, _prepared, _evaluation = _historical_recovery_unit(
            ctx, book, source_run_id=source, ordinal=ordinal)
        if row.source_commit_sha256 != unresolved[ordinal]['commit_sha256']:
            raise ProvenanceRefused(
                f'Stage5 第 {ordinal} 章 source envelope/deep validation 漂移')
        rows.append(row)
    source_codes = {row.source_code for row in rows}
    if len(source_codes) != 1:
        raise ProvenanceRefused(
            f'Stage5 source run 各章 code 不一致：{sorted(source_codes)}')
    raw: dict[str, Any] = {
        'version': 1,
        'kind': 'stage5-prepared-recovery-workpack-v1',
        'read_only': True,
        'sid': ctx.sid,
        'node': 'stage5.write',
        'source_run_id': source,
        'target_run_id': ctx.run_id,
        'source_code': next(iter(source_codes)),
        'target_code': ctx.code,
        'chapters': [row.model_dump(mode='json') for row in rows],
    }
    raw['subject_sha256'] = stage5_runs.workpack_subject(raw)
    return PreparedRecoveryWorkpack.model_validate(raw)


def _recovered_formal_artifact(
    ctx: RunContext,
    prepared: ChapterCandidateContext,
    evaluation: ChapterCandidateEvaluation,
    row: PreparedRecoveryChapter,
    action: PreparedRecoveryAction,
    *,
    round_number: int,
) -> Chapter:
    adoption = PreparedCheckpointAdoption(
        source_run_id=action.source_run_id,
        target_run_id=action.target_run_id,
        source_code=row.source_code,
        source_run_identity_sha256=row.source_run_identity_sha256,
        source_run_file_sha256=row.source_run_file_sha256,
        source_commit_sha256=row.source_commit_sha256,
        source_commit_file_sha256=row.source_commit_file_sha256,
        source_checkpoint_sha256=row.source_checkpoint_sha256,
        source_checkpoint_file_sha256=row.source_checkpoint_file_sha256,
        source_attempt_id=row.source_attempt_id,
        source_response_sha256=row.source_response_sha256,
        source_adoption_id=row.source_adoption_id,
        action_subject_sha256=action.subject_sha256,
        actor=action.actor,
        reason=action.reason,
    )
    provenance = ctx.recovered_llm_provenance(
        RoundLlmProvenance,
        prompts={'SYS': prepared.system},
        model=evaluation.requested_model,
        adopted_models=[evaluation.adopted_model],
        round=round_number,
        request_fingerprint=evaluation.request_fingerprint,
        prepared_checkpoint_adoption=adoption,
    )
    return _chapter_record(
        prepared._entry,
        [segment.model_dump() for segment in evaluation.segments],
        provenance=provenance,
    )


def _recovery_landed_primary(
        store: ArtifactStore, journal: PreparedRecoveryJournal, *, rel: str,
) -> Chapter | None:
    """若 live canonical 已是 recovery journal 目标则读回，否则仍按 baseline 处理。"""
    if not store.exists(rel):
        return None
    current, _raw = store.read_own(Chapter, rel, require_current=False)
    payload = current.model_dump(by_alias=True, mode='json')
    return current if stage5_runs.verify_recovery_primary(journal, payload) else None


def _preflight_recovery_targets(
    ctx: RunContext,
    store: ArtifactStore,
    prepared: ChapterCandidateContext,
    artifact: Chapter,
    journal: PreparedRecoveryJournal,
) -> None:
    """恢复写盘前证明五个 live 路径只处于冻结 baseline 或本 journal 目标。"""
    rel = _rel(prepared.ordinal)
    invalid_rel = paths.invalid_of(rel)
    rejected_rel = _rejected_rel(prepared.ordinal)
    audit_rel = artifact_rel('stage5.trim_audit', nn=prepared.ordinal)
    resolution_rel = artifact_rel('stage5.trim_resolution', nn=prepared.ordinal)
    primary_sha: str | None = None
    if _recovery_landed_primary(store, journal, rel=rel) is not None:
        primary_sha = stage5_runs.file_sha256(ctx, rel)
    desired_audit, audit_sha, resolution_sha = _trim_preflight_expectations(
        ctx, store, prepared, artifact)
    stage5_runs.recovery_target_state(
        ctx, prepared.ordinal, journal, rel,
        expected_file_sha256=primary_sha)
    stage5_runs.recovery_target_state(
        ctx, prepared.ordinal, journal, invalid_rel, expected_absent=True)
    stage5_runs.recovery_target_state(
        ctx, prepared.ordinal, journal, rejected_rel, expected_absent=True)
    stage5_runs.recovery_target_state(
        ctx, prepared.ordinal, journal, audit_rel,
        expected_file_sha256=audit_sha,
        expected_absent=desired_audit is None)
    stage5_runs.recovery_target_state(
        ctx, prepared.ordinal, journal, resolution_rel,
        expected_file_sha256=resolution_sha,
        expected_absent=resolution_sha is None)


def _verify_recovery_targets(
    ctx: RunContext,
    store: ArtifactStore,
    prepared: ChapterCandidateContext,
    artifact: Chapter,
    journal: PreparedRecoveryJournal,
) -> None:
    """正式 readback 后证明 recovery journal 的五个 touched path 全部收口。"""
    rel = _rel(prepared.ordinal)
    invalid_rel = paths.invalid_of(rel)
    rejected_rel = _rejected_rel(prepared.ordinal)
    audit_rel = artifact_rel('stage5.trim_audit', nn=prepared.ordinal)
    resolution_rel = artifact_rel('stage5.trim_resolution', nn=prepared.ordinal)
    primary_sha = stage5_runs.file_sha256(ctx, rel)
    if primary_sha is None:
        raise ProvenanceRefused(
            f'Stage5 第 {prepared.ordinal} 章 recovery readback 后文件消失')
    desired_audit, audit_sha, resolution_sha = _trim_preflight_expectations(
        ctx, store, prepared, artifact)
    states = [
        stage5_runs.recovery_target_state(
            ctx, prepared.ordinal, journal, rel,
            expected_file_sha256=primary_sha),
        stage5_runs.recovery_target_state(
            ctx, prepared.ordinal, journal, invalid_rel,
            expected_absent=True),
        stage5_runs.recovery_target_state(
            ctx, prepared.ordinal, journal, rejected_rel,
            expected_absent=True),
        stage5_runs.recovery_target_state(
            ctx, prepared.ordinal, journal, audit_rel,
            expected_file_sha256=audit_sha,
            expected_absent=desired_audit is None),
        stage5_runs.recovery_target_state(
            ctx, prepared.ordinal, journal, resolution_rel,
            expected_file_sha256=resolution_sha,
            expected_absent=resolution_sha is None),
    ]
    if any(state != 'expected' for state in states):
        raise ProvenanceRefused(
            f'Stage5 第 {prepared.ordinal} 章 recovery 仍有 touched path 留在 baseline')


def _finalize_recovered_prepared(
    ctx: RunContext,
    prepared: ChapterCandidateContext,
    evaluation: ChapterCandidateEvaluation,
    row: PreparedRecoveryChapter,
    action: PreparedRecoveryAction,
) -> ChapterCandidateDelivery:
    """在逐章锁内把 source prepared 补成 typed canonical + committed lineage。"""
    store = ArtifactStore(ctx)
    rel = _rel(prepared.ordinal)
    rejected_rel = _rejected_rel(prepared.ordinal)
    audit_rel = artifact_rel('stage5.trim_audit', nn=prepared.ordinal)
    resolution_rel = artifact_rel('stage5.trim_resolution', nn=prepared.ordinal)
    with stage5_runs.commit_ownership(ctx.novel_dir, prepared.ordinal):
        assert_chapter_candidate_current(ctx, prepared)
        journal = stage5_runs.recovery_state(ctx, prepared.ordinal)
        artifact = (
            None if journal is None
            else _recovery_landed_primary(store, journal, rel=rel))
        if artifact is None:
            artifact = _recovered_formal_artifact(
                ctx, prepared, evaluation, row, action,
                round_number=store.next_round(rel))
        payload = artifact.model_dump(by_alias=True, mode='json')
        finalization = _formal_finalization_payload(
            prepared, evaluation, artifact)
        journal = stage5_runs.prepare_recovery_journal(
            ctx,
            chapter=row,
            action=action,
            primary_payload=payload,
            finalization_payload=finalization,
        )
        stage5_runs.verify_recovery_source(
            ctx, prepared.ordinal, journal)
        # journal 已存在、canonical 尚未写：再次从 manifest 还原本章 exact row，
        # 以全局 source_adoption_id 做 0-or-exact 检查，堵住 manifest/journal
        # 之间或 journal/live 之间出现的旧 target audit 尾窗。
        stage5_runs.preclaim_recovery_journal(ctx, journal)

        if journal.state == 'committed':
            landed = verify_chapter_delivery(
                ctx, prepared, artifact, rel=rel)
            finalization = _formal_finalization_payload(
                prepared, evaluation, landed)
            stage5_runs.verify_recovery_committed(
                ctx,
                prepared.ordinal,
                journal,
                primary_payload=landed.model_dump(
                    by_alias=True, mode='json'),
                finalization_payload=finalization,
            )
            trim_notes, _desired = _over_length_audit(
                [segment.model_dump() for segment in landed.segments],
                prepared._entry,
            )
            _record_normalization_account(
                ctx, prepared.ordinal, evaluation._account)
            return ChapterCandidateDelivery(
                outcome='formal', where=str(store.path(rel)), artifact=landed,
                findings=evaluation.findings, trim_notes=tuple(trim_notes))

        _preflight_recovery_targets(
            ctx, store, prepared, artifact, journal)
        assert_chapter_candidate_current(ctx, prepared)
        with store.history_transaction(
                rel, rejected_rel, audit_rel, resolution_rel):
            if store.exists(rejected_rel):
                store.archive_round(rejected_rel)

            def commit(canonical_rel: str, candidate: Chapter) -> Any:
                current = _recovery_landed_primary(
                    store, journal, rel=canonical_rel)
                if current is not None:
                    if store.exists(paths.invalid_of(canonical_rel)):
                        store.retire_invalid(canonical_rel)
                    return store.path(canonical_rel)
                assert_chapter_candidate_current(ctx, prepared)
                return store.write_round(canonical_rel, candidate)

            where_path, trim_notes = _commit_canonical_chapter(
                store,
                artifact,
                expected_ordinal=prepared.ordinal,
                entry=prepared._entry,
                commit=commit,
            )
            landed = verify_chapter_delivery(
                ctx, prepared, artifact, rel=rel)
            _verify_recovery_targets(
                ctx, store, prepared, landed, journal)

        _record_normalization_account(
            ctx, prepared.ordinal, evaluation._account)
        assert_chapter_candidate_current(
            ctx, prepared, after_formal_commit=True)
        landed_payload = landed.model_dump(by_alias=True, mode='json')
        fingerprint = prov.projection_fingerprint(landed_payload)
        ctx.ledger.note_prepared_recovery_adoption(
            sid=ctx.sid,
            purpose=row.purpose,
            chapter=prepared.ordinal,
            source_run_id=action.source_run_id,
            source_code=row.source_code,
            source_run_identity_sha256=row.source_run_identity_sha256,
            source_run_file_sha256=row.source_run_file_sha256,
            source_commit_sha256=row.source_commit_sha256,
            source_commit_file_sha256=row.source_commit_file_sha256,
            source_checkpoint_sha256=row.source_checkpoint_sha256,
            source_checkpoint_file_sha256=row.source_checkpoint_file_sha256,
            source_attempt_id=row.source_attempt_id,
            source_response_sha256=row.source_response_sha256,
            source_adoption_id=row.source_adoption_id,
            subject_sha256=action.subject_sha256,
            actor=action.actor,
            reason=action.reason,
            artifact=rel,
            artifact_fingerprint=fingerprint,
        )
        assert_chapter_candidate_current(
            ctx, prepared, after_formal_commit=True)
        finalization = _formal_finalization_payload(
            prepared, evaluation, landed)
        stage5_runs.mark_recovery_committed(
            ctx,
            chapter=prepared.ordinal,
            primary_payload=landed_payload,
            finalization_payload=finalization,
        )
        committed = stage5_runs.recovery_state(ctx, prepared.ordinal)
        if committed is None or committed.state != 'committed':
            raise ProvenanceRefused(
                f'Stage5 第 {prepared.ordinal} 章 recovery mark committed 后 journal 消失')
        stage5_runs.verify_recovery_committed(
            ctx,
            prepared.ordinal,
            committed,
            primary_payload=landed_payload,
            finalization_payload=finalization,
        )
    return ChapterCandidateDelivery(
        outcome='formal', where=str(where_path), artifact=landed,
        findings=evaluation.findings, trim_notes=tuple(trim_notes))


def _require_prepared_recovery_zero_wire(ctx: RunContext) -> None:
    stage5_runs.require_prepared_recovery_target_clean(ctx)


def run_prepared_recovery(
    ctx: RunContext,
    source_run_id: str,
    *,
    actor: str,
    reason: str,
    subject_sha256: str,
) -> CheckReport:
    """显式采用整书 unresolved source prepared；只跑复验、正式写回与审计。"""
    _require_prepared_recovery_zero_wire(ctx)
    workpack = build_prepared_recovery_workpack(ctx, source_run_id)
    stage5_runs.require_workpack_subject(workpack)
    action = PreparedRecoveryAction(
        actor=actor,
        reason=reason,
        subject_sha256=subject_sha256,
        source_run_id=workpack.source_run_id,
        target_run_id=ctx.run_id,
    )
    if action.subject_sha256 != workpack.subject_sha256:
        raise ProvenanceRefused(
            'prepared recovery --subject-sha256 与当前工作包不一致；请重新 emit 并审阅')
    # API prepare 只是早反馈；producer 仍是不能绕过的最终授权口。恢复会让旧
    # Stage5 Review stale，所以此处只验证当前 s4/writing/写作主体与计划章集。
    from novel.orchestrator import approvals
    approvals.authorize_prepared_recovery(
        ctx.novel_dir.parent,
        ctx.sid,
        [row.output_ordinal for row in workpack.chapters],
    )

    manifest_raw: dict[str, Any] = {
        'version': 1,
        'kind': 'stage5-prepared-recovery-manifest-v1',
        'workpack': workpack.model_dump(mode='json'),
        'action': action.model_dump(mode='json'),
        'manifest_sha256': '0' * 64,
    }
    manifest_raw['manifest_sha256'] = stage5_runs.manifest_sha256(manifest_raw)
    manifest = PreparedRecoveryManifest.model_validate(manifest_raw)
    stage5_runs.write_recovery_manifest(ctx, manifest)

    source_scope = set(stage5_runs.source_prepared_envelopes(
        ctx, workpack.source_run_id))
    reviewed_scope = {row.output_ordinal for row in workpack.chapters}
    if source_scope != reviewed_scope:
        raise ProvenanceRefused(
            'prepared recovery source run 的 prepared 章集在工作包冻结后漂移：'
            f'已审={sorted(reviewed_scope)} 当前={sorted(source_scope)}')

    store = ArtifactStore(ctx)
    require_canonical_layout(store)
    book = review.load_book(ctx, store)
    written: list[str] = []
    blocked: list[str] = []
    judged: dict[int, list[Finding]] = {}
    for row in workpack.chapters:
        current_row, prepared, evaluation = _historical_recovery_unit(
            ctx,
            book,
            source_run_id=workpack.source_run_id,
            ordinal=row.output_ordinal,
        )
        journal = stage5_runs.recovery_state(ctx, row.output_ordinal)
        comparable = current_row
        if journal is not None:
            # 第一次 prepared 后 live 可以已经是 baseline 或本 journal 目标；
            # 这个漂移由 recovery_target_state 精确判，不用旧 workpack 假装重采样。
            comparable = current_row.model_copy(update={
                'current_baselines': dict(row.current_baselines),
            })
        if comparable != row:
            raise ProvenanceRefused(
                f'Stage5 第 {row.output_ordinal} 章 source/current producer '
                '与已审 recovery workpack 漂移')
        delivery = _finalize_recovered_prepared(
            ctx, prepared, evaluation, row, action)
        written.append(f'第 {row.output_ordinal} 章 → {delivery.where}')
        blocked.extend(delivery.trim_notes)
        judged[row.output_ordinal] = list(delivery.findings)
        _require_prepared_recovery_zero_wire(ctx)
    _require_prepared_recovery_zero_wire(ctx)
    return _report(
        ctx, store, written=written, blocked=blocked,
        book=book, judged=judged)


def _uses_agent_sessions(ctx: RunContext) -> bool:
    """执行协议只由一次 run 冻结的显式 profile 决定，不靠 client 猜。"""
    return getattr(ctx, 'executor_profile', None) is not None


def _require_agent_chapter_source(source: _ChapterResponseSource) -> None:
    required = (
        'ask_json', 'accepted_attempt', 'accepted_checkpoint',
        'restore_checkpoint_acceptance', 'queue_code_validation_feedback',
        'queue_restored_code_validation_feedback',
        'purpose_can_resume', 'purpose_driver_failed',
        'finish_chapter_session',
    )
    missing = [name for name in required if not callable(getattr(source, name, None))]
    if not (callable(getattr(source, 'accepted_model', None))
            or callable(getattr(source, 'accepted_models', None))):
        missing.append('accepted_model(s)')
    if not (callable(getattr(source, 'accepted_request_fingerprint', None))
            or callable(getattr(source, 'accepted_checkpoint', None))):
        missing.append('accepted_request_fingerprint/checkpoint')
    if missing:
        raise ProvenanceRefused(
            f'stage5.write agent source 缺逐章 bridge {missing}；已在首个 wire 前停止')


def _record_reflow_shadow(
    ctx: RunContext,
    prepared: ChapterCandidateContext,
    evaluation: ChapterCandidateEvaluation,
) -> None:
    """P1-1 的 reflow shadow：只在观测层记录 whitespace-only 重排的判据差异。

    段落 HARD 命中时，按 `paragraph_slot_plan` 的目标段数对候选做一次
    shadow 重排，并用同一张判据表重判，把「可行与否 / HARD 前后差」写进
    observer 事件流。**它不写任何产物、不改变 evaluation 的任何结论**——
    在 TODO-21 语料标定完成前，reflow 不得被默认启用。
    """
    if not any(f.rule == 'paragraph_budget_missed' for f in evaluation.findings):
        return
    try:
        plan = M.paragraph_slot_plan(prepared._entry, prepared._book)
    except Exception:
        return
    target = sum(plan)
    shadow_text = M.reflow_shadow(evaluation.text, target=target)
    detail: dict[str, Any] = {
        'chapter': prepared.ordinal,
        'target_paragraphs': target,
        'actual_paragraphs': sum(
            1 for line in evaluation.text.split(M.NL) if line.strip()),
        'feasible': shadow_text is not None,
    }
    if shadow_text is not None:
        _findings, errors, _warnings = _judge(
            shadow_text,
            prepared._entry,
            prepared._book,
            mat=prepared.given,
            account=M.NormalizeAccount(),
            segments=[segment.model_dump() for segment in evaluation.segments],
        )
        detail['hard_before'] = len(evaluation.errors)
        detail['hard_after'] = len(errors)
    ctx.observe('reflow_shadow', **detail)


def _generate_chapter_with_agent(
    ctx: RunContext,
    prepared: ChapterCandidateContext,
    source: _ChapterResponseSource,
    *,
    progress: RequestProgress,
) -> dict[str, Any]:
    """同一章同一 purpose 内循环；schema/dynamic/code HARD 都复用 provider session。"""
    _require_agent_chapter_source(source)
    assert_chapter_candidate_current(ctx, prepared)
    stage5_runs.ensure_run(
        ctx,
        chapter=prepared.ordinal,
        input_sha256=prepared._input_sha256,
        system=prepared.system,
        user=prepared.user,
        response_model=ChapterResponse,
        max_tokens=MAX_TOKENS,
        mode=prepared.mode,
    )
    restored = _restore_chapter_checkpoint(ctx, source, prepared)
    last_rejected: ChapterCandidateEvaluation | None = None
    if restored is not None:
        if restored.passed:
            return {
                'prepared': prepared,
                'evaluation': restored,
                'restored_checkpoint': True,
            }
        # 旧 provider session 不假定仍在线。pool 只在确有业务 HARD 且尚有总轮次
        # 预算时新建 driver/session；恢复 acceptance 后把安全反馈排为新 session 首轮，
        # subject 仍绑定原 system/user，prior turns/attempt links 不会重置。
        assert_chapter_candidate_current(ctx, prepared)
        queued = source.queue_restored_code_validation_feedback(
            restored.feedback_rows(), purpose=prepared.purpose)
        if not queued:
            return {
                'prepared': prepared,
                'evaluation': restored,
                'restored_checkpoint': True,
            }
        last_rejected = restored
        prior_attempts = restored.attempts
    else:
        prior_attempts = ()
    while True:
        # 输入漂移不能再花一轮后才发现。SessionAgentClient 另会在 wire 前复核代码/root；
        # 这里补的是 Stage5 私有判据真正消费的正文、secret、Review 与旧正式稿。
        assert_chapter_candidate_current(ctx, prepared)
        if last_rejected is not None:
            _record_reviewer_retry_started(ctx, prepared)
        try:
            response = source.ask_json(
                prepared.system,
                prepared.user,
                model_cls=ChapterResponse,
                purpose=prepared.purpose,
                max_tokens=MAX_TOKENS,
                validator=lambda obj, wire: _response_problem(
                    obj, wire, prepared._entry),
                progress=progress,
                recovery_policy=stage5_runs.recovery_policy_of(prepared.mode),
            )
        except ResponseRepairRequired as exc:
            return {'response_repair_required': exc}
        except LlmFailed as exc:
            # 业务坏候选已经是 wire/schema 合格且已 checkpoint 的事实。后续
            # parse/schema/dynamic 返工耗尽时，用它走一次既有 INVALID/rejected
            # 终态；driver/protocol 失败仍保留为未知，不能拿旧候选掩盖进程失败。
            if (prepared.mode != 'reviewer_rewrite'
                    and last_rejected is not None
                    and not source.purpose_driver_failed(prepared.purpose)
                    and not source.purpose_can_resume(prepared.purpose)):
                return {
                    'prepared': prepared,
                    'evaluation': last_rejected,
                    'response_validation_exhausted': True,
                }
            # reviewer rewrite 已在本轮 wire 前 durable 落 ``retry_started``；
            # 没有随后 exact checkpoint 就可能已经外发/扣费。即便客户端认为
            # 返工耗尽，也不能拿上一候选落终态掩盖这一未知窗。
            return {'error': str(exc).splitlines()[0]}
        evaluation = evaluate_chapter_candidate(
            source,
            prepared,
            response,
            prior_attempts=prior_attempts,
        )
        # 每一份通过 wire/schema/dynamic validator 的候选都先进入 append-only
        # checkpoint，再决定继续反馈还是终态提交；崩溃不会迫使同章重复付费。
        assert_chapter_candidate_current(ctx, prepared)
        _record_reflow_shadow(ctx, prepared, evaluation)
        _write_chapter_checkpoint(ctx, source, prepared, evaluation)
        if evaluation.passed:
            return {'prepared': prepared, 'evaluation': evaluation}
        last_rejected = evaluation
        assert_chapter_candidate_current(ctx, prepared)
        if source.queue_code_validation_feedback(
                evaluation.feedback_rows(), purpose=prepared.purpose):
            prior_attempts = evaluation.attempts
            continue
        # turn budget 用尽是本章终态：revision 保留旧正式稿并落拒稿证据；普通写作
        # 最后一份 HARD 候选交给 finalize 一次写 INVALID。两者都不在这里写盘。
        return {'prepared': prepared, 'evaluation': evaluation}


def _generate_chapter(
    entry: dict[str, Any],
    book: M.Book,
    client: LlmClient,
    *,
    revise: dict[int, tuple[str, list[list[str]], str]] | None,
    system: str,
    progress: RequestProgress,
    ctx: RunContext | None = None,
    agent_sessions: bool = False,
    prepared_context: ChapterCandidateContext | None = None,
) -> dict[str, Any]:
    ordinal = int(entry['output_ordinal'])
    if agent_sessions:
        if ctx is None:
            raise ValueError('Stage5 agent 逐章候选循环缺 RunContext')
        if prepared_context is None:
            revision = None if revise is None else revise[ordinal]
            prepared = prepare_chapter_candidate(
                entry, book, revision=revision, system=system)
        else:
            prepared = prepared_context
            if prepared.ordinal != ordinal:
                raise ProvenanceRefused(
                    '预建 Stage5 candidate context 与目标章号漂移')
        return _generate_chapter_with_agent(
            ctx, prepared, client, progress=progress)

    # legacy LlmClient 的单轮/唯一一次 refresh 重采样语义逐字保留。它不伪装成
    # provider session，也不创建 agent checkpoint 或 adoption sidecar。
    mat = prompts.build_material(entry, book)
    if revise is not None:
        prev_text, fixes, review_fp = revise[ordinal]
        user = prompts.revise_user(mat, prev_text, _fix_lines(fixes))
    else:
        prev_text, fixes, review_fp = '', [], ''
        user = prompts.build_user(mat)
    typed_entry = CtxChapter.model_validate(entry)
    assert_prompt_private_absent(typed_entry, system=system, user=user)
    accepted_purpose = ctx_purpose(entry)
    try:
        got = client.ask_json(
            system, user, model_cls=ChapterResponse,
            purpose=accepted_purpose, max_tokens=MAX_TOKENS,
            validator=lambda obj, wire: _response_problem(obj, wire, entry),
            progress=progress)
    except ResponseRepairRequired as exc:
        # 这是本章在**下一采之前**的恢复暂停，不是全批网络故障。把终态带回
        # 汇总线程，让其他已起跑/待起跑章节照常完成并落下自己的成功结果。
        return {'response_repair_required': exc}
    except LlmFailed as exc:
        return {'error': str(exc).splitlines()[0]}
    segments = _ordered_segments(got, entry)
    segments, account = _normalize_segments(segments, book)
    text = _segments_text(segments)
    if revise is not None:
        attempts = [{'segments': copy.deepcopy(segments)}]
        regressions = checks.trim_regressions(prev_text, text, entry, book)
        if regressions:
            feedback = getattr(client, 'record_candidate_feedback', None)
            if callable(feedback):
                feedback(accepted_purpose, {
                    'kind': 'chapter_regression_review',
                    'chapter_ordinal': ordinal, 'regressions': list(regressions),
                    'action': 'targeted_revision',
                })
            accepted_purpose = f'{ctx_purpose(entry)}·退化重采样'
            try:
                got = client.ask_json(
                    system, user, model_cls=ChapterResponse,
                    purpose=accepted_purpose,
                    max_tokens=MAX_TOKENS,
                    validator=lambda obj, wire: _response_problem(obj, wire, entry),
                    progress=progress, cache_policy=CachePolicy.refresh())
            except ResponseRepairRequired as exc:
                return {'response_repair_required': exc}
            except LlmFailed as exc:
                return {
                    'rejected': {
                        'output_ordinal': ordinal,
                        'why': [*regressions,
                                '唯一一次绕缓存重采样没有拿到完整正文：'
                                + str(exc).splitlines()[0]],
                        'fixes': fixes,
                        'attempts': attempts,
                    },
                }
            retry_segments = _ordered_segments(got, entry)
            retry_segments, retry_account = _normalize_segments(retry_segments, book)
            retry_text = _segments_text(retry_segments)
            attempts.append({'segments': copy.deepcopy(retry_segments)})
            retry_regressions = checks.trim_regressions(
                prev_text, retry_text, entry, book)
            if retry_regressions:
                if callable(feedback):
                    feedback(accepted_purpose, {
                        'kind': 'chapter_regression_review',
                        'chapter_ordinal': ordinal,
                        'regressions': list(retry_regressions), 'action': 'stop',
                    })
                return {
                    'rejected': {
                        'output_ordinal': ordinal,
                        'why': retry_regressions,
                        'fixes': fixes,
                        'attempts': attempts,
                    },
                }
            segments, text, account = retry_segments, retry_text, retry_account
    return {
        'entry': entry, 'ordinal': ordinal, 'mat': mat, 'user': user,
        'segments': segments, 'text': text, 'account': account,
        'prev_text': prev_text, 'fixes': fixes, 'review_fp': review_fp,
        'accepted_purpose': accepted_purpose,
    }


def _record_legacy_chapter_delivery(
        ctx: RunContext, store: ArtifactStore, client: LlmClient, *,
        ordinal: int, purpose: str, formal: bool,
) -> None:
    """按已写章节与真实尝试闭合来源账，INVALID 只退休已持久候选。"""
    attempt_id = client.accepted_attempt(purpose)
    if attempt_id is None:
        raise ProvenanceRefused('Stage5 已落盘候选缺真实 accepted attempt')
    if formal:
        # 采纳后的正式读回是交付验证，不是同批其他章的写作输入。
        # 否则共享 ctx 会把先提交章的正文混入随后章节的 provenance。
        with _STAGE5_VALIDATION_LOCK, ctx.validation_reads():
            landed, _raw = store.read_own(Chapter, _rel(ordinal), require_current=True)
        ctx.ledger.note_adoption(
            sid=ctx.sid, node=ctx.node, purpose=purpose,
            attempt_id=attempt_id, artifact=_rel(ordinal), chapter=ordinal,
            artifact_fingerprint=prov.projection_fingerprint(
                landed.model_dump(by_alias=True, mode='json')))
    checkpointed = getattr(client, 'mark_checkpointed', None)
    if callable(checkpointed):
        checkpointed(attempt_id)


def _prepare_legacy_chapter_delivery(
        ctx: RunContext, store: ArtifactStore, client: LlmClient, *,
        entry: dict[str, Any], artifact: Chapter, account: M.NormalizeAccount,
        purpose: str, invalid: bool,
) -> None:
    """首个产物写入前，把当前输入及最终 typed 候选绑回原模型回答。"""
    save = getattr(client, 'save_producer_recovery', None)
    if not callable(save):
        return
    attempt_id = client.accepted_attempt(purpose)
    if attempt_id is None:
        raise ProvenanceRefused('Stage5 候选提交前缺真实 accepted attempt')
    rel = _rel(artifact.output_ordinal)
    if invalid:
        rel = paths.invalid_of(rel)
    ordinal = artifact.output_ordinal
    _notes, desired_audit = _over_length_audit(
        [segment.model_dump() for segment in artifact.segments],
        entry)
    audit_rel = artifact_rel('stage5.trim_audit', nn=ordinal)
    companion_baselines = {
        path: store.full_sha256_or_none(path)
        for path in (audit_rel, artifact_rel('stage5.trim_resolution', nn=ordinal),
                     _rejected_rel(ordinal))}
    save(attempt_id, {
        'kind': 'stage5-legacy-delivery-v1', 'code': ctx.code,
        'purpose': purpose, 'attempt_id': attempt_id,
        'entry': copy.deepcopy(entry),
        'inputs': {key: edge.model_dump(mode='json') for key, edge in ctx.edges.items()},
        'target_rel': rel, 'target_sha256': store.prospective_sha256(rel, artifact),
        'baseline_sha256': store.full_sha256_or_none(rel),
        'target_payload': artifact.model_dump(by_alias=True, mode='json'),
        'account': asdict(account),
        'companion_baselines': companion_baselines,
        'target_audit_sha256': (
            store.prospective_sha256(audit_rel, desired_audit)
            if desired_audit is not None else None),
    })


def _reconcile_legacy_chapter_deliveries(
        ctx: RunContext, store: ArtifactStore, book: M.Book, client: LlmClient,
) -> None:
    """默认增量筛章前补原候选的提交账；文件存在本身不构成采纳证明。"""
    if _uses_agent_sessions(ctx) or not callable(getattr(client, 'save_producer_recovery', None)):
        return
    from novel.work_units import budget_for

    for ordinal, entry in sorted(book.chapters.items()):
        budget = budget_for(ctx, scope=f'chapter:{ordinal}')
        pending = budget.latest_pending_response()
        if pending is None:
            continue
        reservation, raw = pending
        proof = budget.producer_recovery(reservation.turn_id)
        if proof is None:
            continue
        if proof.get('kind') != 'stage5-legacy-delivery-v1':
            raise ProvenanceRefused('Stage5 legacy 候选的 producer 恢复协议不匹配')
        rel = str(proof['target_rel'])
        if rel not in (_rel(ordinal), paths.invalid_of(_rel(ordinal))):
            raise ProvenanceRefused('Stage5 producer 回执越过原章节范围')
        current_sha = store.full_sha256_or_none(rel)
        if current_sha != proof['target_sha256']:
            if current_sha == proof['baseline_sha256']:
                continue
            raise ProvenanceRefused('Stage5 已提交候选处于第三方状态，不能补采纳')
        current_inputs = {
            key: edge.model_dump(mode='json') for key, edge in ctx.edges.items()}
        with ctx.validation_reads():
            artifact, _ = store.read_own(Chapter, rel, require_current=True)
            if (proof['code'] != ctx.code or proof['entry'] != entry
                    or proof['inputs'] != current_inputs
                    or artifact.model_dump(by_alias=True, mode='json') != proof['target_payload']):
                raise ProvenanceRefused('Stage5 提交回执与当前代码、输入或 typed 章不一致')
            attempt_id = str(proof['attempt_id'])
            turn = budget.turn(reservation.turn_id)
            if (raw.get('attempt_id') != attempt_id
                    or artifact.provenance.request_fingerprint != turn.request_sha256):
                raise ProvenanceRefused('Stage5 正式章没有绑定原始模型请求')
            # 用同一机械判据重验当前任务；复用首写前实际归一读数，不能拿零值
            # 掩盖原引号/段落诊断，也不再对已正式提交的文本做二次改写。
            _findings, errors, _warnings = _judge(
                artifact.text, entry, book,
                mat=prompts.build_material(entry, book).given,
                account=M.NormalizeAccount(**proof['account']),
                segments=[segment.model_dump() for segment in artifact.segments])
            if rel == _rel(ordinal) and errors:
                raise ProvenanceRefused('Stage5 待补采纳的正式章未通过当前机械校验')
            ctx.require_observed_roots_current()
        # canonical replace 之后、trim 写回之前硬杀也可补尾；只接受首写前
        # 基线或本候选确定性目标，不覆盖第三方更改的伴随状态。
        audit_rel = artifact_rel('stage5.trim_audit', nn=ordinal)
        for companion, baseline_sha in proof['companion_baselines'].items():
            target_sha = proof['target_audit_sha256'] if companion == audit_rel else None
            if store.full_sha256_or_none(companion) not in (baseline_sha, target_sha):
                raise ProvenanceRefused('Stage5 提交伴随状态已经被第三方改写')
        with ctx.validation_reads():
            if rel == _rel(ordinal):
                _commit_canonical_chapter(
                    store,
                    artifact,
                    expected_ordinal=ordinal,
                    entry=entry,
                    commit=lambda _rel, _artifact: _rel,
                )
            rejected_rel = _rejected_rel(ordinal)
            if store.exists(rejected_rel):
                store.archive_round(rejected_rel)
        receipt = raw.get('prepared_receipt')
        if receipt is not None:
            ctx.ledger.reconcile_work_unit_attempt(budget, reservation.turn_id, receipt)
        ctx.ledger.claim_work_unit_attempts(
            budget, reservation.turn_id, purpose=str(proof['purpose']))
        if rel == _rel(ordinal):
            ctx.ledger.bind_work_unit_commit(attempt_id, budget, reservation.turn_id)
            ctx.ledger.note_adoption(
                sid=ctx.sid, node=ctx.node, purpose=str(proof['purpose']),
                attempt_id=attempt_id, artifact=rel, chapter=ordinal,
                artifact_fingerprint=prov.projection_fingerprint(
                    artifact.model_dump(by_alias=True, mode='json')))
        budget.mark_adopted(reservation.turn_id)


def _run_group_parallel(
    ctx: RunContext,
    store: ArtifactStore,
    book: M.Book,
    todo: list[dict[str, Any]],
    client: LlmClient,
    *,
    revise: dict[int, tuple[str, list[list[str]], str]] | None,
    prepared_contexts: Mapping[int, ChapterCandidateContext] | None = None,
) -> CheckReport:
    system = prompts.write_system(revise=revise is not None)
    agent_sessions = _uses_agent_sessions(ctx)
    judged: dict[int, list] = {}
    written_by: dict[int, str] = {}
    blocked_by: dict[int, str] = {}
    agent_candidates: dict[
        int, tuple[ChapterCandidateContext, ChapterCandidateEvaluation],
    ] = {}
    ordinals = [int(entry['output_ordinal']) for entry in todo]
    tracker = ChapterProgressTracker(ctx.observe, ordinals)
    if agent_sessions:
        # reviewer rewrite 的 durable ``started`` 必须先于 driver factory/probe/wire。
        # run.json 同样在这里先闭合；工作线程里的 ensure_run 仅作幂等复验。
        for ordinal in ordinals:
            prepared = (
                None if prepared_contexts is None
                else prepared_contexts.get(ordinal))
            if prepared is None:
                continue
            with _reviewer_unit_ownership(ctx, prepared):
                assert_chapter_candidate_current(ctx, prepared)
                stage5_runs.ensure_run(
                    ctx, chapter=ordinal,
                    input_sha256=prepared._input_sha256,
                    system=prepared.system, user=prepared.user,
                    response_model=ChapterResponse, max_tokens=MAX_TOKENS,
                    mode=prepared.mode,
                )
                if prepared.mode == 'reviewer_rewrite':
                    request = prepared._reviewer_rewrite_request
                    assert request is not None
                    stage5_runs.record_reviewer_rewrite_execution(
                        ctx, chapter=ordinal,
                        request_rel=STAGE5_REVIEWER_REWRITE_REQUEST,
                        request_file_sha256=(
                            prepared._reviewer_rewrite_request_file_sha256),
                        request_sha256=request.request_sha256,
                        input_sha256=prepared._input_sha256,
                        event='started',
                    )
        prepare_sessions = getattr(client, 'prepare_initial_sessions', None)
        if not callable(prepare_sessions):
            raise ProvenanceRefused(
                'stage5.write agent pool 缺 prepare_initial_sessions；'
                '不能在部分章节已 start 后才发现跨章 driver 复用')
        prepare_sessions(tuple(ordinals))
    workers = min(
        STAGE5_WRITE_MAX_CONCURRENCY,
        max(1, int(getattr(client, 'concurrency', DEFAULT_CONCURRENCY))),
        len(todo),
    )
    fatal: Exception | None = None

    def finish_agent_session(
        ordinal: int, outcome: str, *,
        delivery_attempts: int | None = None,
        same_run_recovered: bool | None = None,
        first_failure_phase: str | None = None,
        first_failure_detail: str | None = None,
    ) -> None:
        """ended 审计失败只阻断最终返回，不取消尚在运行的兄弟章。"""
        nonlocal fatal
        if not agent_sessions:
            return
        try:
            client.finish_chapter_session(
                ordinal, outcome=outcome,
                delivery_attempts=delivery_attempts,
                same_run_recovered=same_run_recovered,
                first_failure_phase=first_failure_phase,
                first_failure_detail=first_failure_detail,
            )
        except Exception as exc:
            if fatal is None:
                fatal = exc
            blocked_by.setdefault(
                ordinal,
                f'第 {ordinal} 章 session 终态审计失败：'
                f'{type(exc).__name__}: {str(exc).splitlines()[0]}',
            )

    def generate(entry: dict[str, Any]) -> dict[str, Any]:
        ordinal = int(entry['output_ordinal'])
        prepared = (None if prepared_contexts is None
                    else prepared_contexts.get(ordinal))
        lease = (nullcontext() if prepared is None
                 else _reviewer_unit_ownership(ctx, prepared))
        with lease:
            return _generate_chapter(
                entry, book, client, revise=revise, system=system,
                progress=tracker.request(ordinal), ctx=ctx,
                agent_sessions=agent_sessions, prepared_context=prepared)

    with ThreadPoolExecutor(max_workers=workers) as pool:
        futures = {
            pool.submit(generate, entry): int(entry['output_ordinal'])
            for entry in todo
        }
        for future in as_completed(futures):
            ordinal = futures[future]
            try:
                generated = future.result()
            except CancelledError:
                finish_agent_session(ordinal, 'executor_failed')
                blocked_by[ordinal] = (
                    f'第 {ordinal} 章因同批网络故障在发请求前取消')
                tracker.finish(
                    ordinal, outcome='failed', detail='同批网络故障，未发请求')
                continue
            except Exception as exc:
                if fatal is None:
                    fatal = exc
                    if not agent_sessions:
                        for pending in futures:
                            if pending is not future:
                                pending.cancel()
                finish_agent_session(ordinal, 'executor_failed')
                blocked_by[ordinal] = (
                    f'第 {ordinal} 章网络级失败：{type(exc).__name__}: '
                    f'{str(exc).splitlines()[0]}')
                tracker.finish(
                    ordinal, outcome='failed', detail=str(exc).splitlines()[0])
                continue

            if error := generated.get('error'):
                finish_agent_session(ordinal, 'response_failed')
                blocked_by[ordinal] = (
                    f'第 {ordinal} 章没拿到正文' if error == '没有拿到正文'
                    else f'第 {ordinal} 章没拿到完整正文：{error}')
                tracker.finish(ordinal, outcome='failed', detail=error)
                continue

            if repair_pause := generated.get('response_repair_required'):
                if fatal is None:
                    fatal = repair_pause
                blocked_by[ordinal] = (
                    f'第 {ordinal} 章已停在下一采之前，等待受托响应修复：'
                    f'{str(repair_pause).splitlines()[0]}')
                finish_agent_session(ordinal, 'response_failed')
                tracker.finish(
                    ordinal, outcome='failed',
                    detail='等待受托响应修复；同批其他章节继续')
                continue

            if agent_sessions:
                prepared = generated.get('prepared')
                evaluation = generated.get('evaluation')
                if (not isinstance(prepared, ChapterCandidateContext)
                        or not isinstance(evaluation, ChapterCandidateEvaluation)):
                    exc = ProvenanceRefused(
                        f'第 {ordinal} 章 agent producer 没有返回 typed prepared/evaluation')
                    if fatal is None:
                        fatal = exc
                    finish_agent_session(ordinal, 'executor_failed')
                    blocked_by[ordinal] = str(exc)
                    tracker.finish(
                        ordinal, outcome='failed', detail=str(exc).splitlines()[0])
                    continue
                # 先等全批 model futures 都完成，才允许任一章正式落盘。否则第二章
                # 稍晚回显了与第一章相同的 provider session id 时，第一章可能已经
                # 被误写成 formal。候选已各自 checkpoint，延后这里只增加确定性终验。
                agent_candidates[ordinal] = (prepared, evaluation)
                continue

            if rejected := generated.get('rejected'):
                rel = _rejected_rel(ordinal)
                artifact = RejectedRevision.model_validate(rejected)
                with stage5_runs.commit_ownership(ctx.novel_dir, ordinal):
                    where = store.write_round(rel, artifact)
                line = (
                    f'第 {ordinal} 章两次修订候选都相对旧稿退化，旧正式稿未覆盖；'
                    f'证据 → {where}（{"；".join(artifact.why)}）')
                blocked_by[ordinal] = line
                tracker.finish(ordinal, outcome='blocked', detail=artifact.why[0])
                continue

            entry = generated['entry']
            round_number = store.next_round(_rel(ordinal))
            extra: dict[str, Any] = {'round': round_number}
            if revise is not None:
                extra.update(
                    revision_of=prov.fingerprint(generated['prev_text']),
                    review=generated['review_fp'],
                    fixes=[list(item) for item in generated['fixes']],
                )
            request_fingerprint = client.accepted_request_fingerprint(
                generated['accepted_purpose'])
            if request_fingerprint is None:
                raise ProvenanceRefused(
                    f'第 {ordinal} 章已经返回正文，但 purpose='
                    f'{generated["accepted_purpose"]!r} 没有 accepted attempt；'
                    '拒绝估算或截断 request fingerprint')
            accepted_model = client.accepted_model(generated['accepted_purpose'])
            if accepted_model is None:
                raise ProvenanceRefused(
                    f'第 {ordinal} 章已经返回正文，但 purpose='
                    f'{generated["accepted_purpose"]!r} 没有 accepted model；'
                    '拒绝按主模型配置估算')
            extra['request_fingerprint'] = request_fingerprint
            prompt_record = {'SYS': system}
            provenance = ctx.llm_provenance_for_chapter(
                ordinal,
                RoundLlmProvenance,
                prompts=(
                    {**prompt_record, 'REVISE': prompts.REVISE_TAIL}
                    if revise is not None else prompt_record),
                model=client.model,
                adopted_models=[accepted_model],
                **extra,
            )
            where, errors, findings, trim_notes = _write_chapter(
                ctx, store, entry, book,
                segments=generated['segments'], mat=generated['mat'].given,
                account=generated['account'], provenance=provenance,
                before_commit=lambda artifact, account, invalid: _prepare_legacy_chapter_delivery(
                    ctx, store, client, entry=entry, artifact=artifact,
                    account=account, purpose=generated['accepted_purpose'], invalid=invalid))
            _record_legacy_chapter_delivery(
                ctx, store, client, ordinal=ordinal,
                purpose=generated['accepted_purpose'], formal=not errors)
            judged[ordinal] = findings
            line = (
                f'第 {ordinal} 章 → {where}'
                + (f'（{len(errors)} 条阻断级）' if errors else '')
                + (f'　⚠ {"；".join(trim_notes)}' if trim_notes else '')
            )
            (blocked_by if errors else written_by)[ordinal] = line
            tracker.finish(
                ordinal,
                outcome='blocked' if errors else 'ok',
                detail=(str(errors[0]) if errors
                        else (trim_notes[0] if trim_notes else None)),
            )

    if agent_sessions:
        if bool(getattr(client, 'isolation_failed', False)):
            # provider/driver 跨章共享污染的是会话身份，不是某一条业务 finding。
            # 本批任何候选都不先落 formal；checkpoint 留给修好 factory 后恢复。
            for ordinal in sorted(agent_candidates):
                finish_agent_session(ordinal, 'artifact_failed')
                blocked_by[ordinal] = (
                    f'第 {ordinal} 章候选已 checkpoint，但同批 session 隔离失败；'
                    '本次未写正式章')
                tracker.finish(
                    ordinal, outcome='failed', detail='同批 session 隔离失败')
        else:
            first_pass_failures: dict[int, tuple[_CommitDiagnostics, Exception]] = {}

            def settle_delivery(
                ordinal: int,
                delivery: ChapterCandidateDelivery,
                *,
                commit: _CommitOutcomeDiagnostics | None,
            ) -> None:
                """一次终态 delivery 的唯一收口：报告行 + tracker + ended 审计。

                ``commit`` 非 None 表示本章经历过提交重放并成功；首次提交即
                成功也如实记 ``delivery_attempts=1/same_run_recovered=False``。
                只有从未进入提交链的章（隔离失败、发请求前取消）才留 None。
                session 只在这里落一次终态。
                """
                judged[ordinal] = list(delivery.findings)
                if delivery.outcome == 'rejected_revision':
                    artifact = delivery.artifact
                    assert isinstance(artifact, RejectedRevision)
                    finish_agent_session(
                        ordinal, 'blocked',
                        delivery_attempts=1 if commit is None else commit.attempts,
                        same_run_recovered=False if commit is None else commit.recovered,
                        first_failure_phase=commit.failure_phase if commit else None,
                        first_failure_detail=(
                            commit.failure_detail if commit else None),
                    )
                    line = (
                        f'第 {ordinal} 章 agent 修订候选终态仍相对旧稿退化，'
                        f'旧正式稿未覆盖；证据 → {delivery.where}'
                        f'（{"；".join(artifact.why)}）')
                    blocked_by[ordinal] = line
                    tracker.finish(
                        ordinal, outcome='blocked', detail=artifact.why[0])
                    return
                line = (
                    f'第 {ordinal} 章 → {delivery.where}'
                    + (f'（{len(delivery.errors)} 条阻断级）'
                       if delivery.errors else '')
                    + (f'　⚠ {"；".join(delivery.trim_notes)}'
                       if delivery.trim_notes else ''))
                finish_agent_session(
                    ordinal,
                    'blocked' if delivery.errors else 'delivered',
                    delivery_attempts=1 if commit is None else commit.attempts,
                    same_run_recovered=False if commit is None else commit.recovered,
                    first_failure_phase=commit.failure_phase if commit else None,
                    first_failure_detail=(
                        commit.failure_detail if commit else None),
                )
                (blocked_by if delivery.errors else written_by)[ordinal] = line
                tracker.finish(
                    ordinal,
                    outcome='blocked' if delivery.errors else 'ok',
                    detail=(delivery.errors[0] if delivery.errors
                            else (delivery.trim_notes[0]
                                  if delivery.trim_notes else None)),
                )

            # ── 第一次提交：逐章走完整提交链。提交异常**不**立即结束 session，
            # 也不落 blocked；失败章与失败阶段留给下面的 same-run sweep。
            for ordinal in sorted(agent_candidates):
                prepared, evaluation = agent_candidates[ordinal]
                diagnostics = _CommitDiagnostics()
                try:
                    delivery = finalize_chapter_candidate(
                        ctx, client, prepared, evaluation,
                        diagnostics=diagnostics)
                except Exception as exc:
                    first_pass_failures[ordinal] = (diagnostics, exc)
                    continue
                settle_delivery(ordinal, delivery, commit=None)

            # ── same-run deterministic sweep：每个失败章一次有界重放。
            # finalize_chapter_candidate 本身零 wire（不建 driver/probe/session、
            # 不查缓存、不发模型请求），重放体与首次提交完全相同，因此这里
            # 天然是 deterministic 的；不再叠加第二次重试，防止把内容缺陷
            # 掩成基础设施抖动。
            for ordinal, (first_box, first_exc) in sorted(first_pass_failures.items()):
                prepared, evaluation = agent_candidates[ordinal]
                retry_box = _CommitDiagnostics()
                try:
                    delivery = finalize_chapter_candidate(
                        ctx, client, prepared, evaluation,
                        diagnostics=retry_box,
                        same_run_replay=True)
                except Exception:
                    if fatal is None:
                        fatal = first_exc
                    finish_agent_session(
                        ordinal, 'artifact_failed',
                        delivery_attempts=2,
                        same_run_recovered=False,
                        first_failure_phase=first_box.failure_phase,
                        first_failure_detail=first_box.detail,
                    )
                    blocked_by[ordinal] = (
                        f'第 {ordinal} 章终态提交在 {first_box.phase} 失败，'
                        f'same-run 重放仍未恢复：{type(first_exc).__name__}: '
                        f'{str(first_exc).splitlines()[0]}')
                    tracker.finish(
                        ordinal, outcome='failed',
                        detail=str(first_exc).splitlines()[0])
                    continue
                settle_delivery(
                    ordinal, delivery,
                    commit=_CommitOutcomeDiagnostics(
                        attempts=2,
                        recovered=True,
                        failure_phase=first_box.failure_phase,
                        failure_detail=first_box.detail,
                    ),
                )

    written = [written_by[key] for key in sorted(written_by)]
    blocked = [blocked_by[key] for key in sorted(blocked_by)]
    if fatal is not None:
        raise fatal
    report = _report(
        ctx, store, written=written, blocked=blocked, book=book, judged=judged)
    if blocked and not written:
        raise GateBlocked(
            f'这一批 {len(todo)} 章全部没落成正式稿：' + '；'.join(blocked)
            + '。人修好对应 `.INVALID` 后跑 `stage5.write '
              '--revalidate --actor <id> --reason <why>`'
              '（0 LLM、同轮重判并留不可变审计）',
            invalid_path=str(store.path('stage5')))
    return report


def _run_group(ctx: RunContext, store: ArtifactStore, book: M.Book,
               todo: list[dict[str, Any]], client: LlmClient, *,
               revise: dict[int, tuple[str, list[list[str]], str]] | None,
               prepared_contexts: Mapping[int, ChapterCandidateContext] | None = None,
               ) -> CheckReport:
    return _run_group_parallel(
        ctx, store, book, todo, client, revise=revise,
        prepared_contexts=prepared_contexts)


def _fix_lines(fixes: list[list[str]]) -> list[str]:
    """`[[rule, evidence_key], …]` → 发给写手的指令行。

    ⚠⚠ **指令的唯一落点是 `Rule.directive`**：`_provenance.fixes` 只存**引用**，
    存第二份渲染后的文本就会跟着判据表漂，而**下一轮到底发了哪句话**再也说不清。
    """
    out: list[str] = []
    for rule, ev in fixes:
        r = REGISTRY.get(rule)
        out.append(f'· {r.directive}' + (f'　〔{ev}〕' if ev else ''))
    return out


# ─────────────────────────────────────────────────────────────────────────────
# 四、`--revise` —— 全链唯一的 LLM 返工回路
# ─────────────────────────────────────────────────────────────────────────────

def revise_targets(store: ArtifactStore
                   ) -> tuple[dict[int, list[list[str]]], list[str], str]:
    """最近一份验收 → 这一轮要发回写手的条目。返回 `(按章的 fixes, 跳过的理由, 报告指纹)`。

    **四道筛子**，一条都不能省：
      ① `actionable`（根因在写作层——根因不在写手身上的条目重写一百遍也修不好）；
      ② **`repair == 'UNIQUE'`**：满足这条判据的路径唯一。密度 / 比率 / 档位类一律不进
         ——满足它们的路径有无穷多条，而**最省力的那条是删内容或编内容**。
         实测代价：可反哺条目 **36 → 9（转人工 27 条，75%）**，其中一本是 10 → 0；
      ③ `unresolved_rounds < STOP_REWRITE_AFTER`（`R-8.5`）：**连续 2 轮未消 ⇒ 判「根因
         不在写手」，主动跳过**。无收敛证据的循环重写是在烧钱抽奖。

    ⚠ **跳过的两类都要报出来**（`stalled` / 非 `UNIQUE`）：「不进反哺」不等于「不管」，
    它们照常列在报告与界面上等人看。
    """
    got = review.latest_review(store)
    if got is None:
        raise TerminalState(
            '盘上一份验收报告都没有——`--revise` 要拿它挑目标。先跑 `stage5.review`（0 成本）')
    seq, rev = got
    review_bytes = store.path(review.REVIEW_TPL.format(n=seq)).read_bytes()
    picked: dict[int, list[list[str]]] = {}
    skipped: list[str] = []
    for row in rev.chapters:
        if row.output_ordinal in review.missing_draft_ordinals(rev):
            continue
        for f in row.findings:
            if not f.actionable:
                continue
            if REGISTRY.get(f.rule).repair != 'UNIQUE':
                skipped.append(f'第 {row.output_ordinal} 章 {f.rule}：不进付费自救'
                               f'（满足路径不唯一）⇒ 转人工')
                continue
            if f.unresolved_rounds >= STOP_REWRITE_AFTER:
                skipped.append(f'第 {row.output_ordinal} 章 {f.rule}：连续 '
                               f'{f.unresolved_rounds} 轮未消 ⇒ 判「根因不在写手」，跳过')
                continue
            picked.setdefault(row.output_ordinal, []).append([f.rule, f.evidence_key])
    # ⚠ 这里曾并入 `stage5.semantic` 的可反哺结论（`semantic.revision_targets`）：
    #   语义判据的 repair 是 `UNIQUE`，泄底证据可以只把正文片段发回写手。
    #   该节点于 2026-08-17 下线（用户裁决）⇒ 反哺面收敛成「机械验收里的
    #   `UNIQUE` 结论」这一侧。指纹随之只由验收字节决定。
    fp = prov.content_sha1(review_bytes)
    return picked, skipped, fp


def run_revise(ctx: RunContext, client: LlmClient,
               chapters: str | None = None) -> CheckReport:
    """`--revise`：把可反哺的结论连同上一轮成稿发回写手。**付费。**"""
    ownership = (
        nullcontext() if _uses_agent_sessions(ctx)
        else stage5_runs.book_commit_ownership(ctx.novel_dir)
    )
    with ownership:
        store = ArtifactStore(ctx)
        require_canonical_layout(store)
        # 与 initial write 共用同一 producer 级 pre-wire reservation guard；
        # 不能依赖 CLI，因为 NodeAgent/direct caller 也会走到这里。
        stage5_runs.refuse_foreign_prepared_before_wire(ctx, chapters)
        book = review.load_book(ctx, store)
        # legacy 没有候选 journal，书锁覆盖本次 Review/旧稿快照直到全部提交；
        # agent profile 则在每轮 wire 前、逐章正式提交前另核 typed candidate 主体。
        _reconcile_legacy_chapter_deliveries(ctx, store, book, client)
        review.require_fresh_review(ctx, store)
        picked, skipped, fp = revise_targets(store)
        if (spec := chapters):
            want = {int(x) for x in spec.split(',') if x.strip()}
            picked = {o: v for o, v in picked.items() if o in want}
        if not picked:
            # **显式终态并明说「这不是『没问题』」。**
            raise TerminalState(
                '这一轮没有可自救的条目。⚠ **这不是「没问题」**：'
                + (f'有 {len(skipped)} 条被跳过——' + '；'.join(skipped[:6])
                   if skipped else '要么全部转人工，要么连续未消已到上限')
                + '\n它们照常列在验收报告与界面上等人看。')
        todo: list[dict[str, Any]] = []
        revise: dict[int, tuple[str, list[list[str]], str]] = {}
        for o, fixes in sorted(picked.items()):
            entry = book.chapters.get(o)
            if entry is None:
                continue
            prev_art, _raw = store.read_own(
                Chapter, _rel(o), require_current=True)
            todo.append(entry)
            revise[o] = (prev_art.text, fixes, fp)
        for message in skipped:
            print(f'    · 跳过：{message}', flush=True)
        return _run_group(ctx, store, book, todo, client, revise=revise)


def run_reviewer_rewrite(
        ctx: RunContext, client: LlmClient,
        request_rel: str = STAGE5_REVIEWER_REWRITE_REQUEST,
) -> CheckReport:
    """整批持有 lease；逐章先 claim，再按写作并发上限生成，全部完成后统一审阅。"""
    if not ctx.paid:
        raise ProvenanceRefused('reviewer rewrite paid runner 必须持显式付费授权')
    batch, digest = reviewer_rewrite.load_reviewer_rewrite_batch(ctx, request_rel)
    with stage5_runs.reviewer_rewrite_execution_ownership(
            ctx.novel_dir, batch.request_sha256):
        if reviewer_rewrite.load_reviewer_rewrite_batch(ctx, request_rel) != (batch, digest):
            raise ProvenanceRefused('reviewer rewrite 批 request 在取锁前漂移')
        targets = reviewer_rewrite.ready_reviewer_rewrite_targets(ctx, request_rel)
        store = ArtifactStore(ctx)
        require_canonical_layout(store)
        book = review.load_book(ctx, store)
        entries: list[dict[str, Any]] = []
        prepared_contexts: dict[int, ChapterCandidateContext] = {}
        for request in batch.requests:
            if request.target_ordinal not in targets:
                continue
            with stage5_runs.reviewer_rewrite_execution_ownership(
                    ctx.novel_dir, request.request_sha256):
                entry, prepared = _preclaim_reviewer_rewrite_unit(
                    ctx, store, book, request, digest, request_rel)
            entries.append(entry)
            prepared_contexts[request.target_ordinal] = prepared
        return _run_group(
            ctx, store, book, entries, client, revise=None,
            prepared_contexts=prepared_contexts)


def _preclaim_reviewer_rewrite_unit(
        ctx: RunContext, store: ArtifactStore, book: M.Book,
        request: ReviewerRewriteRequest, request_file_sha256: str, request_rel: str,
) -> tuple[dict[str, Any], ChapterCandidateContext]:
    """全批 preclaim 完成之前不创建 driver；章执行链各自封存。"""
    with stage5_runs.commit_ownership(
            ctx.novel_dir, request.target_ordinal):
        state = reviewer_rewrite.reviewer_rewrite_execution_state(
            ctx, request_rel, request_sha256=request.request_sha256)
        if (state is None or state.state != 'ready_paid'
                or state.request_sha256 != request.request_sha256
                or state.request_file_sha256 != request_file_sha256):
            actual = 'absent' if state is None else state.state
            raise TerminalState(
                'reviewer rewrite request 当前执行态是 '
                f'{actual}；拒绝新建第二次 paid execution')
        if state.source_run_id is not None and state.source_run_id != ctx.run_id:
            matching = stage5_runs.reviewer_rewrite_execution_lineage(
                stage5_runs.reviewer_rewrite_execution_chains(
                    ctx, chapter=request.target_ordinal,
                    request_sha256=request.request_sha256,
                    request_file_sha256=request_file_sha256))
            active = tuple(
                chain for chain in matching
                if chain[-1].event != 'reclaimed_unstarted')
            if (len(active) != 1
                    or active[0][0].run_id != state.source_run_id):
                raise ProvenanceRefused(
                    'reviewer rewrite zero-execution reclaim 主体漂移')
            stage5_runs.reclaim_reviewer_rewrite_unstarted(ctx, active[0])
            reclaimed = reviewer_rewrite.reviewer_rewrite_execution_state(
                ctx, request_rel, request_sha256=request.request_sha256)
            if (reclaimed is None or reclaimed.state != 'ready_paid'
                    or reclaimed.source_run_id is not None):
                raise ProvenanceRefused(
                    'reviewer rewrite zero-execution reclaim 后未回到唯一 ready')
        request, request_file_sha256 = (
            reviewer_rewrite.require_current_reviewer_rewrite_request(
                ctx, request_rel, request_sha256=request.request_sha256))
        stage5_runs.refuse_foreign_commit_reservations(
            ctx, request.target_ordinal, mode='reviewer_rewrite')
        entry = book.chapters.get(request.target_ordinal)
        if entry is None:
            raise ProvenanceRefused('reviewer rewrite target 已不在 current plan')
        old, old_file_sha256 = store.read_own_with_sha256(
            Chapter, _rel(request.target_ordinal), require_current=True)
        target = next(
            row for row in request.unresolved_trim_items
            if row.output_ordinal == request.target_ordinal)
        if old_file_sha256 != request.target_chapter_file_sha256:
            raise ProvenanceRefused('reviewer rewrite target old chapter 完整文件 SHA 漂移')
        if hashlib.sha256(old.text.encode('utf-8')).hexdigest() \
                != target.chapter_sha256:
            raise ProvenanceRefused('reviewer rewrite target old chapter 正文 SHA 漂移')
        prepared = prepare_chapter_candidate(
            entry, book, revision=None,
            reviewer_rewrite_request=(
                request, request_file_sha256, old.text),
        )
        stage5_runs.ensure_run(
            ctx, chapter=request.target_ordinal,
            input_sha256=prepared._input_sha256,
            system=prepared.system, user=prepared.user,
            response_model=ChapterResponse, max_tokens=MAX_TOKENS,
            mode='reviewer_rewrite')
        lineage = stage5_runs.reviewer_rewrite_execution_lineage(
            stage5_runs.reviewer_rewrite_execution_chains(
                ctx, chapter=request.target_ordinal,
                request_sha256=request.request_sha256,
                request_file_sha256=request_file_sha256))
        claim_predecessor = None
        if lineage:
            tail = lineage[-1][-1]
            if tail.event != 'reclaimed_unstarted':
                raise ProvenanceRefused(
                    'reviewer rewrite 新 claim 前仍存在未退休 execution tail')
            claim_predecessor = tail.journal_sha256
        stage5_runs.record_reviewer_rewrite_execution(
            ctx, chapter=request.target_ordinal,
            request_rel=request_rel,
            request_file_sha256=request_file_sha256,
            request_sha256=request.request_sha256,
            input_sha256=prepared._input_sha256,
            event='started',
            claim_predecessor_journal_sha256=claim_predecessor)
    return entry, prepared


def _reviewer_recovery_unit(
        ctx: RunContext, request: ReviewerRewriteRequest,
        request_file_sha256: str, source_run_id: str,
) -> tuple[
        ChapterCandidateContext, ChapterCandidateEvaluation,
        stage5_runs.HistoricalReviewerRewrite,
]:
    """从 frozen request 历史原字节重建 prompt，并在当前代码下重判 checkpoint。"""
    restored, prepared = reviewer_rewrite._restore_frozen_checkpoint(
        ctx, request, request_file_sha256, source_run_id)
    acceptance = restored.historical.checkpoint.acceptance
    profile = restored.historical.run_record['request_policy']['profile']
    source = _HistoricalCheckpointSource(
        model=str(profile['model']), purpose=prepared.purpose,
        acceptance=acceptance)
    prior = tuple(
        RejectedAttempt.model_validate(item)
        for item in restored.state_payload['prior_attempts'])
    evaluation = evaluate_chapter_candidate(
        source, prepared, restored.historical.checkpoint.value,
        accepted_purpose=prepared.purpose, prior_attempts=prior)
    _assert_chapter_evaluation_identity(source, prepared, evaluation)
    return prepared, evaluation, restored


def _reviewer_recovery_adoption(
        ctx: RunContext, request: ReviewerRewriteRequest,
        restored: stage5_runs.HistoricalReviewerRewrite,
) -> PreparedCheckpointAdoption:
    checkpoint = restored.historical.checkpoint
    acceptance = checkpoint.acceptance
    run_record = restored.historical.run_record
    source_run_id = restored.historical.context.run_id
    checkpoint_path = stage5_runs.reviewer_rewrite_checkpoint_file(
        ctx, run_id=source_run_id, chapter=request.target_ordinal,
        checkpoint_sha256=checkpoint.checkpoint_sha256)
    checkpoint_file_sha = _path_sha256(checkpoint_path)
    commit_sha: str | None = None
    commit_file_sha: str | None = None
    if restored.commit is not None:
        commit_sha = str(restored.commit['commit_sha256'])
        commit_file_sha = stage5_runs.reviewer_rewrite_source_file_sha256(
            ctx, run_id=source_run_id, chapter=request.target_ordinal,
            filename='committed.json')
    response_sha = str(acceptance['ledger_response_sha256'])
    adoption_id = stage5_runs.reviewer_rewrite_source_adoption_id(
        sid=ctx.sid, source_run_id=source_run_id,
        chapter=request.target_ordinal,
        request_sha256=request.request_sha256,
        source_run_identity_sha256=str(run_record['identity_sha256']),
        source_run_file_sha256=restored.historical.run_sha256,
        source_checkpoint_sha256=checkpoint.checkpoint_sha256,
        source_checkpoint_file_sha256=checkpoint_file_sha,
        source_commit_sha256=commit_sha,
        source_commit_file_sha256=commit_file_sha,
        source_response_sha256=response_sha,
    )
    return PreparedCheckpointAdoption(
        actor=request.actor,
        reason=request.reason,
        source_run_id=source_run_id,
        target_run_id=ctx.run_id,
        source_code=str(run_record['code']),
        source_run_identity_sha256=str(run_record['identity_sha256']),
        source_run_file_sha256=restored.historical.run_sha256,
        source_commit_sha256=commit_sha,
        source_commit_file_sha256=commit_file_sha,
        source_checkpoint_sha256=checkpoint.checkpoint_sha256,
        source_checkpoint_file_sha256=checkpoint_file_sha,
        source_attempt_id=str(acceptance['attempt_id']),
        source_response_sha256=response_sha,
        source_adoption_id=adoption_id,
        action_subject_sha256=request.request_sha256,
        reviewer_rewrite_request_sha256=request.request_sha256,
    )


def _reviewer_recovered_chapter(
        ctx: RunContext, request: ReviewerRewriteRequest,
        prepared: ChapterCandidateContext,
        evaluation: ChapterCandidateEvaluation,
        restored: stage5_runs.HistoricalReviewerRewrite,
        *, errors: tuple[str, ...] | None,
) -> Chapter:
    adoption = _reviewer_recovery_adoption(ctx, request, restored)
    prompt_evidence = {
        'SYS': prepared.system,
        'REVIEWER_REWRITE': _REVIEWER_REWRITE_PROMPT_CONTRACT,
    }
    provenance = ctx.recovered_llm_provenance(
        RoundLlmProvenance,
        prompts=prompt_evidence,
        model=evaluation.requested_model,
        adopted_models=[evaluation.adopted_model],
        round=ArtifactStore(ctx).next_round(_rel(prepared.ordinal)),
        request_fingerprint=evaluation.request_fingerprint,
        prepared_checkpoint_adoption=adoption,
        revision_of=prov.fingerprint(prepared._prev_text),
        review=None,
        fixes=None,
        reviewer_rewrite_request_sha256=request.request_sha256,
    )
    return _chapter_record(
        prepared._entry,
        [segment.model_dump() for segment in evaluation.segments],
        provenance=provenance,
    )


def _reviewer_recovery_artifact(
        ctx: RunContext, request: ReviewerRewriteRequest,
        prepared: ChapterCandidateContext,
        evaluation: ChapterCandidateEvaluation,
        restored: stage5_runs.HistoricalReviewerRewrite,
) -> tuple[str, str, Chapter | RejectedRevision]:
    if evaluation.regressions:
        return 'rejected', _rejected_rel(prepared.ordinal), RejectedRevision(
            output_ordinal=prepared.ordinal,
            why=list(evaluation.regressions),
            fixes=[list(item) for item in prepared._fixes],
            attempts=_rejected_attempts(evaluation.attempts),
            reviewer_rewrite_request_sha256=request.request_sha256,
        )
    if evaluation.errors:
        return 'invalid', paths.invalid_of(_rel(prepared.ordinal)), \
            _reviewer_recovered_chapter(
                ctx, request, prepared, evaluation, restored,
                errors=evaluation.errors)
    return 'formal', _rel(prepared.ordinal), _reviewer_recovered_chapter(
        ctx, request, prepared, evaluation, restored, errors=None)


def _reviewer_recovery_finalization(
        prepared: ChapterCandidateContext,
        evaluation: ChapterCandidateEvaluation,
        outcome: str,
        artifact: Chapter | RejectedRevision,
) -> dict[str, object]:
    if isinstance(artifact, Chapter):
        return {
            **_formal_finalization_payload(prepared, evaluation, artifact),
            'delivery_outcome': outcome,
        }
    return {
        'version': 1,
        'artifact': _rejected_rel(prepared.ordinal),
        'chapter': prepared.ordinal,
        'mode': 'reviewer_rewrite',
        'delivery_outcome': 'rejected',
        'input_sha256': prepared._input_sha256,
        'candidate_sha256': evaluation._candidate_sha256,
        'source_attempt_id': evaluation.source_attempt_id,
        'request_fingerprint': evaluation.request_fingerprint,
        'reviewer_rewrite_request_sha256': (
            prepared._reviewer_rewrite_request.request_sha256
            if prepared._reviewer_rewrite_request is not None else None),
    }


def _existing_reviewer_recovery_delivery(
        store: ArtifactStore, request: ReviewerRewriteRequest,
        outcome: str, rel: str,
) -> Chapter | RejectedRevision | None:
    if not store.exists(rel):
        return None
    model = RejectedRevision if outcome == 'rejected' else Chapter
    artifact, _sha = store.read_own(model, rel, require_current=False)
    digest = (
        artifact.reviewer_rewrite_request_sha256
        if isinstance(artifact, RejectedRevision)
        else getattr(artifact.provenance, 'reviewer_rewrite_request_sha256', None))
    return artifact if digest == request.request_sha256 else None


def _reviewer_recovery_preflight(
        ctx: RunContext, wal: ReviewerRewriteRecoveryJournal,
        artifact: Chapter | RejectedRevision,
) -> None:
    store = ArtifactStore(ctx)
    delivery_sha = store.prospective_sha256(wal.delivery_rel, artifact)
    absent_after = {
        'formal': set(wal.touched_paths) - {wal.delivery_rel},
        'invalid': {_rejected_rel(wal.output_ordinal)},
        'rejected': set(),
    }[wal.delivery_outcome]
    for rel in wal.touched_paths:
        stage5_runs.reviewer_rewrite_recovery_target_state(
            ctx, wal, rel,
            expected_file_sha256=(
                delivery_sha if rel == wal.delivery_rel else None),
            expected_absent=rel in absent_after,
        )


def _reviewer_recovery_artifact_from_wal(
        request: ReviewerRewriteRequest,
        wal: ReviewerRewriteRecoveryJournal,
) -> Chapter | RejectedRevision:
    """把 foreign prepared WAL 的 frozen payload 读成 typed exact delivery。"""
    model = RejectedRevision if wal.delivery_outcome == 'rejected' else Chapter
    try:
        artifact = model.model_validate(wal.delivery_payload)
    except Exception as exc:
        raise ArtifactUnreadable(
            f'reviewer rewrite recovery WAL delivery payload 不可 typed：{exc}') from exc
    payload = artifact.model_dump(by_alias=True, mode='json')
    if (payload != wal.delivery_payload
            or stage5_runs.agent_runs.canonical_sha256(payload)
               != wal.delivery_payload_sha256):
        raise ProvenanceRefused('reviewer rewrite recovery WAL typed payload 漂移')
    digest = (
        artifact.reviewer_rewrite_request_sha256
        if isinstance(artifact, RejectedRevision)
        else artifact.provenance.reviewer_rewrite_request_sha256)
    if digest != request.request_sha256:
        raise ProvenanceRefused('reviewer rewrite recovery WAL payload 未绑定 request')
    finalization = wal.finalization_payload
    expected_common = {
        'artifact': wal.delivery_rel,
        'chapter': wal.output_ordinal,
        'mode': 'reviewer_rewrite',
        'delivery_outcome': wal.delivery_outcome,
        'input_sha256': wal.input_sha256,
        'source_attempt_id': wal.source_attempt_id,
        'request_fingerprint': wal.source_request_fingerprint,
        'reviewer_rewrite_request_sha256': wal.request_sha256,
    }
    if any(finalization.get(key) != value for key, value in expected_common.items()):
        raise ProvenanceRefused(
            'reviewer rewrite recovery WAL finalization/source/request 漂移')
    candidate_sha = finalization.get('candidate_sha256')
    if not isinstance(candidate_sha, str) or len(candidate_sha) != 64:
        raise ProvenanceRefused('reviewer rewrite recovery WAL 缺 candidate SHA')
    if isinstance(artifact, Chapter):
        adoption = artifact.provenance.prepared_checkpoint_adoption
        if (adoption is None
                or adoption.source_run_id != wal.source_run_id
                or adoption.target_run_id != wal.target_run_id
                or adoption.source_code != wal.source_code
                or adoption.source_run_identity_sha256
                   != wal.source_run_identity_sha256
                or adoption.source_run_file_sha256 != wal.source_run_file_sha256
                or adoption.source_commit_sha256 != wal.source_commit_sha256
                or adoption.source_commit_file_sha256
                   != wal.source_commit_file_sha256
                or adoption.source_checkpoint_sha256
                   != wal.source_checkpoint_sha256
                or adoption.source_checkpoint_file_sha256
                   != wal.source_checkpoint_file_sha256
                or adoption.source_attempt_id != wal.source_attempt_id
                or adoption.source_response_sha256 != wal.source_response_sha256
                or adoption.source_adoption_id != wal.source_adoption_id
                or adoption.action_subject_sha256 != wal.request_sha256
                or adoption.reviewer_rewrite_request_sha256
                   != wal.request_sha256):
            raise ProvenanceRefused(
                'reviewer rewrite recovery WAL Chapter adoption/source 身份漂移')
        if artifact.provenance.code != wal.target_code:
            raise ProvenanceRefused(
                'reviewer rewrite recovery WAL Chapter target code 漂移')
    return artifact


def _finalize_reviewer_rewrite_recovery(
        ctx: RunContext, request: ReviewerRewriteRequest,
        request_file_sha256: str,
        prepared: ChapterCandidateContext,
        evaluation: ChapterCandidateEvaluation,
        restored: stage5_runs.HistoricalReviewerRewrite,
        *, existing_wal: ReviewerRewriteRecoveryJournal | None = None,
) -> ChapterCandidateDelivery:
    store = ArtifactStore(ctx)
    if existing_wal is None:
        outcome, rel, proposed = _reviewer_recovery_artifact(
            ctx, request, prepared, evaluation, restored)
        existing = _existing_reviewer_recovery_delivery(
            store, request, outcome, rel)
        if existing is not None:
            expected_raw_sha256 = store.prospective_sha256(rel, proposed)
            actual_raw_sha256 = _path_sha256(store.path(rel))
            if existing != proposed or actual_raw_sha256 != expected_raw_sha256:
                raise ProvenanceRefused(
                    'reviewer rewrite recovery 既有 carrier 虽自报同 request，'
                    '但不等于 checkpoint 确定性推导的 exact typed/raw payload')
        artifact = proposed if existing is None else existing
        payload = artifact.model_dump(by_alias=True, mode='json')
        finalization = _reviewer_recovery_finalization(
            prepared, evaluation, outcome, artifact)
    else:
        if (existing_wal.state != 'prepared'
                or existing_wal.source_run_id
                   != restored.historical.context.run_id
                or existing_wal.request_file_sha256 != request_file_sha256
                or existing_wal.request_sha256 != request.request_sha256
                or existing_wal.input_sha256 != prepared._input_sha256):
            raise ProvenanceRefused(
                'foreign reviewer rewrite WAL 与 source/request/current recovery 漂移')
        outcome = existing_wal.delivery_outcome
        rel = existing_wal.delivery_rel
        artifact = _reviewer_recovery_artifact_from_wal(request, existing_wal)
        payload = artifact.model_dump(by_alias=True, mode='json')
        finalization = dict(existing_wal.finalization_payload)
    with stage5_runs.commit_ownership(ctx.novel_dir, prepared.ordinal):
        if existing_wal is None:
            wal = stage5_runs.prepare_reviewer_rewrite_recovery(
                ctx,
                source=restored,
                request_rel=STAGE5_REVIEWER_REWRITE_REQUEST,
                request_file_sha256=request_file_sha256,
                request_sha256=request.request_sha256,
                reviewer_action_record_sha256=request.reviewer_action_record_sha256,
                business_sha256=request.business_sha256,
                reviewer_contract_sha256=request.reviewer_contract_sha256,
                input_sha256=prepared._input_sha256,
                delivery_outcome=outcome,
                delivery_rel=rel,
                delivery_payload=payload,
                finalization_payload=finalization,
            )
        else:
            wal = stage5_runs.require_reviewer_rewrite_recovery_identity(
                ctx, target_run_id=existing_wal.target_run_id,
                chapter=prepared.ordinal,
                journal_sha256=existing_wal.journal_sha256)
        stage5_runs.verify_reviewer_rewrite_recovery_payload(
            wal, delivery_payload=payload,
            finalization_payload=finalization)
        _reviewer_recovery_preflight(ctx, wal, artifact)
        formal_rel = _rel(prepared.ordinal)
        rejected_rel = _rejected_rel(prepared.ordinal)
        audit_rel = artifact_rel('stage5.trim_audit', nn=prepared.ordinal)
        resolution_rel = artifact_rel(
            'stage5.trim_resolution', nn=prepared.ordinal)
        if outcome == 'formal':
            assert isinstance(artifact, Chapter)
            with store.history_transaction(
                    formal_rel, rejected_rel, audit_rel, resolution_rel):
                if store.exists(rejected_rel):
                    store.archive_round(rejected_rel)

                def commit(canonical_rel: str, candidate: Chapter) -> Any:
                    current = _existing_reviewer_recovery_delivery(
                        store, request, 'formal', canonical_rel)
                    return (
                        store.path(canonical_rel) if current == candidate
                        else store.write_round(canonical_rel, candidate))

                where, trim_notes = _commit_canonical_chapter(
                    store,
                    artifact,
                    expected_ordinal=prepared.ordinal,
                    entry=prepared._entry,
                    commit=commit,
                )
            landed = verify_chapter_delivery(
                ctx, prepared, artifact, rel=formal_rel)
        elif outcome == 'invalid':
            assert isinstance(artifact, Chapter)
            with store.history_transaction(
                    formal_rel, rejected_rel, audit_rel, resolution_rel):
                if store.exists(rejected_rel):
                    store.archive_round(rejected_rel)
                current = _existing_reviewer_recovery_delivery(
                    store, request, 'invalid', rel)
                where = (
                    store.path(rel) if current == artifact
                    else store.write_invalid(
                        formal_rel, artifact, gates_skipped=[]))
                landed = verify_chapter_delivery(
                    ctx, prepared, artifact, rel=rel)
            trim_notes = []
        else:
            assert isinstance(artifact, RejectedRevision)
            current = _existing_reviewer_recovery_delivery(
                store, request, 'rejected', rel)
            where = (
                store.path(rel) if current == artifact
                else store.write_round(rel, artifact))
            landed = verify_rejected_revision_delivery(
                ctx, prepared, artifact, rel=rel)
            trim_notes = []
        _record_normalization_account(
            ctx, prepared.ordinal, evaluation._account)
        landed_payload = landed.model_dump(by_alias=True, mode='json')
        finalization = (
            dict(wal.finalization_payload)
            if existing_wal is not None
            else _reviewer_recovery_finalization(
                prepared, evaluation, outcome, landed))
        committed = stage5_runs.mark_reviewer_rewrite_recovery_committed(
            ctx, journal=wal,
            delivery_payload=landed_payload,
            finalization_payload=finalization)
        stage5_runs.verify_reviewer_rewrite_recovery_committed(
            ctx, journal=committed,
            delivery_payload=landed_payload,
            finalization_payload=finalization)
    return ChapterCandidateDelivery(
        outcome=('rejected_revision' if outcome == 'rejected' else outcome),
        where=str(where), artifact=landed,
        findings=evaluation.findings,
        errors=(evaluation.regressions if outcome == 'rejected'
                else evaluation.errors),
        trim_notes=tuple(trim_notes),
    )


def run_reviewer_rewrite_prepared_recovery(
        ctx: RunContext, source_run_id: str,
        request_sha256: str,
        recovery_target_run_id: str | None = None,
        recovery_journal_sha256: str | None = None,
        request_rel: str = STAGE5_REVIEWER_REWRITE_REQUEST,
) -> CheckReport:
    """跨 run 0-wire 收口 exact formal reviewer rewrite checkpoint。"""
    if (recovery_target_run_id is None) != (recovery_journal_sha256 is None):
        raise ProvenanceRefused(
            'reviewer rewrite recovery target run/WAL SHA 必须同有同无')
    request, initial_file_sha256 = reviewer_rewrite.load_reviewer_rewrite_request(
        ctx, request_rel, request_sha256=request_sha256)
    if request.request_sha256 != request_sha256:
        raise ProvenanceRefused(
            'reviewer rewrite recovery request SHA 与 canonical 漂移')
    # 锁序：request execution → book/chapter。lease 贯穿 state/source/WAL、
    # 每一件 delivery 写入与 committed 尾写，两个 direct recovery 不可交错。
    batch, _digest = reviewer_rewrite.load_reviewer_rewrite_batch(ctx, request_rel)
    with (stage5_runs.reviewer_rewrite_execution_ownership(
            ctx.novel_dir, batch.request_sha256),
          stage5_runs.reviewer_rewrite_execution_ownership(
            ctx.novel_dir, request.request_sha256)):
        current, request_file_sha256 = (
            reviewer_rewrite.load_reviewer_rewrite_request(ctx, request_rel, request_sha256=request_sha256))
        if current != request or request_file_sha256 != initial_file_sha256:
            raise ProvenanceRefused(
                'reviewer rewrite recovery 取 execution lease 后 request 漂移')
        stage5_runs.require_prepared_recovery_target_clean(ctx)
        state = reviewer_rewrite.reviewer_rewrite_execution_state(ctx, request_rel, request_sha256=request_sha256)
        if (state is None
                or state.state not in (
                    'recoverable_checkpoint', 'recoverable_prepared')
                or state.source_run_id != source_run_id
                or state.request_sha256 != request_sha256
                or state.request_file_sha256 != request_file_sha256):
            raise ProvenanceRefused(
                'reviewer rewrite recovery 参数与 current exact recoverable state 不闭合')
        existing_wal: ReviewerRewriteRecoveryJournal | None = None
        if state.state == 'recoverable_prepared':
            if (recovery_target_run_id != state.recovery_target_run_id
                    or recovery_journal_sha256 != state.recovery_journal_sha256
                    or recovery_target_run_id is None
                    or recovery_journal_sha256 is None):
                raise ProvenanceRefused(
                    'reviewer rewrite prepared recovery 必须续 queue 给出的 exact target WAL')
            existing_wal = stage5_runs.require_reviewer_rewrite_recovery_identity(
                ctx, target_run_id=recovery_target_run_id,
                chapter=request.target_ordinal,
                journal_sha256=recovery_journal_sha256)
            if existing_wal.state != 'prepared':
                raise ProvenanceRefused(
                    'reviewer rewrite recovery 指定 WAL 已非 prepared')
            stage5_runs.require_prepared_recovery_target_clean(
                ctx, target_run_id=existing_wal.target_run_id)
        elif recovery_target_run_id is not None:
            raise ProvenanceRefused(
                'recoverable_checkpoint 不得伪传既有 recovery WAL')
        prepared, evaluation, restored = _reviewer_recovery_unit(
            ctx, request, request_file_sha256, source_run_id)
        delivery = _finalize_reviewer_rewrite_recovery(
            ctx, request, request_file_sha256,
            prepared, evaluation, restored, existing_wal=existing_wal)
        book = prepared._book
        blocked = [] if delivery.outcome == 'formal' else [
            f'第 {prepared.ordinal} 章 reviewer rewrite 0-wire 恢复落为 '
            f'{delivery.outcome}：{delivery.where}']
        written = ([f'第 {prepared.ordinal} 章 → {delivery.where}']
                   if delivery.outcome == 'formal' else [])
        report = _report(
            ctx, ArtifactStore(ctx), written=written, blocked=blocked,
            book=book, judged={prepared.ordinal: list(delivery.findings)})
        if blocked:
            raise GateBlocked(
                blocked[0], invalid_path=delivery.where)
        return report


# ─────────────────────────────────────────────────────────────────────────────
# 五、三条 0 成本路
# ─────────────────────────────────────────────────────────────────────────────

def run_recheck(ctx: RunContext, chapters: str | None = None) -> CheckReport:
    """`--recheck`：原字节重判，只刷新当前检查报告与 trim 状态。

    这条 0 成本路不再偷偷运行第二次正文归一：producer-owned ``segments``、冻结任务书与
    ``_provenance`` 原样保留。否则一次“检查”会被收敛账误认成写手又落笔了一轮。
    """
    store = ArtifactStore(ctx)
    require_canonical_layout(store)
    book = review.load_book(ctx, store)
    trim_notes: list[str] = []
    judged: dict[int, list] = {}
    ordinals = ([int(entry['output_ordinal'])
                 for entry in _chapters_of(store, book, chapters)]
                if chapters else sorted(book.chapters))
    for o in ordinals:
        with stage5_runs.commit_ownership(ctx.novel_dir, o):
            rel = _rel(o)
            if not store.exists(rel):
                continue
            art, _raw = store.read_own(Chapter, rel)
            entry = book.chapters[o]
            segments = [segment.model_dump() for segment in art.segments]
            _require_canonical_slots(segments, entry)
            text = art.text
            acc = _inspection_account(segments, book)
            mat = prompts.build_material(entry, book).given
            fs, errs, warns = _judge(
                text, entry, book, mat=mat, account=acc,
                segments=segments)
            judged[o] = fs
            updated = art  # v3 无派生字段可刷新；检查不得改写正文或历史来源。
            _where, notes = _commit_canonical_chapter(
                store,
                updated,
                expected_ordinal=o,
                entry=entry,
                commit=lambda canonical_rel, artifact: store.path(canonical_rel) if artifact == art else store.refresh_formal(canonical_rel, artifact),
            )
            trim_notes.extend(f'第 {o} 章：{note}' for note in notes)
    return _report(ctx, store, written=[], blocked=trim_notes, book=book, judged=judged)


def run_revalidate(
    ctx: RunContext, *, actor: str, reason: str, chapters: str | None = None,
) -> CheckReport:
    """`--revalidate`：按**当前**判据重判被闸门拦下的 `.INVALID`，合格即提升为正式产物。

    ⚠⚠ **来源账继承，一个字不改**——`store.promote_invalid` 的签名里**没有**
    `prompts` / `model` / `inputs`，**没有参数就没有可以传错的东西**。
    原型这条路**伪造**来源账：它盖上当前 SYS 指纹 + 当前 model + 当前 inputs、`round` 还 +1，
    而**没有任何一次新的落笔发生过**。三件事同时错：记了一个没产出它的 prompt、
    记了一个没写它的模型、把「重判提升」记成了「第 N 轮写作」。
    ⚠ **刻意不宣称 `origin=human`**：「判据改了」与「人手改过」在节点这一侧分不开。
    """
    store = ArtifactStore(ctx)
    require_canonical_layout(store)
    book = review.load_book(ctx, store)
    promoted: list[str] = []
    still: list[str] = []
    judged: dict[int, list] = {}
    ordinals = ([int(entry['output_ordinal'])
                 for entry in _chapters_of(store, book, chapters)]
                if chapters else sorted(book.chapters))
    for o in ordinals:
        with stage5_runs.commit_ownership(ctx.novel_dir, o):
            rel = _rel(o)
            inv = paths.invalid_of(rel)
            if not store.exists(inv):
                continue
            art, _raw = store.read_own(Chapter, inv)
            entry = book.chapters[o]
            baseline = store.revalidation_baseline(
                rel, Chapter, actor=actor, reason=reason)
            _require_revalidation_task_identity(art, baseline)
            mat = prompts.build_material(entry, book).given
            segments = [segment.model_dump() for segment in art.segments]
            # INVALID 可能被人在盘外编辑过。revalidate 承诺原 segments 提升，
            # 因此不能像模型响应/--apply-edit 那样偷偷重排；集合或顺序不闭合就停。
            _require_canonical_slots(segments, entry)
            text = art.text
            account = _inspection_account(segments, book)
            fs, errs, warns = _judge(
                text, entry, book, mat=mat,
                account=account, segments=segments)
            judged[o] = fs
            if errs:
                still.append(f'第 {o} 章仍有 {len(errs)} 条阻断级：{errs[0]}')
                continue
            rebuilt = art  # 提升只追加 revalidation audit，正文和创建身份原样继承。
            where, notes = _commit_canonical_chapter(
                store,
                rebuilt,
                expected_ordinal=o,
                entry=entry,
                commit=lambda canonical_rel, artifact: store.promote_invalid(canonical_rel, Chapter, actor=actor, reason=reason, artifact_override=artifact),
            )
            promoted.append(
                f'第 {o} 章 → {where}'
                + (f'　⚠ {"；".join(notes)}' if notes else ''))
    if not promoted and not still:
        raise TerminalState('盘上一份 `.INVALID` 都没有——没有可提升的稿')
    return _report(ctx, store, written=promoted, blocked=still, book=book,
                   judged=judged)


@dataclass(frozen=True)
class _EditEvaluation:
    """preview 与 apply 共用的完整纯评估结果；构造过程不写盘、不铸 provenance。"""

    ordinal: int
    entry: dict[str, Any] = field(repr=False)
    book: M.Book = field(repr=False)
    current: Chapter = field(repr=False)
    current_chapter_sha256: str
    segments: tuple[ChapterSegment, ...]
    findings: tuple[Finding, ...]
    errors: tuple[str, ...]
    warnings: tuple[str, ...]
    current_hard: tuple[Finding, ...]
    introduced_hard: tuple[Finding, ...]
    resolved_hard: tuple[Finding, ...]
    preview_subject_sha256: str

    @property
    def required_confirmation(self) -> str:
        return 'working_draft' if self.introduced_hard else 'formal'

    def preview(self) -> DraftEditPreview:
        return DraftEditPreview(
            output_ordinal=self.ordinal,
            current_chapter_sha256=self.current_chapter_sha256,
            preview_subject_sha256=self.preview_subject_sha256,
            normalized_segments=list(self.segments),
            findings=list(self.findings),
            current_hard_findings=list(self.current_hard),
            introduced_hard_findings=list(self.introduced_hard),
            resolved_hard_findings=list(self.resolved_hard),
            required_confirmation=self.required_confirmation,
        )


def _hard_findings(findings: Sequence[Finding]) -> tuple[Finding, ...]:
    return tuple(f for f in findings if REGISTRY.get(f.rule).sev == 'hard')


def _finding_subject_key(finding: Finding) -> tuple[str, str]:
    """同一 HARD 的收敛身份；detail 是展示，evidence_key 才是稳定主体。"""
    return finding.rule, finding.evidence_key


def _preview_finding_payload(finding: Finding) -> dict[str, Any]:
    """preview subject 绑定判据返回的完整 typed finding，不手挑第二份字段表。"""
    return finding.model_dump(by_alias=True, mode='json')


def prepare_edit_preview(
    ctx: RunContext,
    ordinal: int,
    edited: ChapterResponse,
    *,
    store: ArtifactStore | None = None,
) -> _EditEvaluation:
    """归一 + 当前全判据；preview/apply 必经的唯一纯评估入口。"""
    store = store or ArtifactStore(ctx)
    require_canonical_layout(store)
    book = review.load_book(ctx, store)
    entry = book.chapters.get(ordinal)
    if entry is None:
        raise TerminalState(f'人工稿说自己是第 {ordinal} 章，而计划里没有这一章')
    rel = _rel(ordinal)
    if not store.exists(rel):
        raise UpstreamMissing(
            f'第 {ordinal} 章没有 current canonical，无法建立 edit preview 乐观锁')
    current, current_sha256 = store.read_own_with_sha256(
        Chapter, rel, require_current=True)
    if current.output_ordinal != ordinal:
        raise ProvenanceRefused(
            f'{rel} 自报第 {current.output_ordinal} 章，不能作为第 {ordinal} 章 preview 基线')

    checked = ChapterResponse.model_validate(edited)
    problem = _response_problem(
        checked, ChatResponse(content='', finish_reason='stop'), entry)
    if problem:
        raise TerminalState(f'人工稿 segment 契约不成立：{problem}')
    segments, account = _normalize_segments(_ordered_segments(checked, entry), book)
    text = _segments_text(segments)
    mat = prompts.build_material(entry, book).given
    findings, errors, warnings = _judge(
        text, entry, book, mat=mat, account=account, segments=segments)

    current_segments = [segment.model_dump() for segment in current.segments]
    current_findings, _current_errors, _current_warnings = _judge(
        current.text,
        entry,
        book,
        mat=mat,
        account=_inspection_account(current_segments, book),
        segments=current_segments,
    )
    current_hard = _hard_findings(current_findings)
    candidate_hard = _hard_findings(findings)
    current_keys = {_finding_subject_key(f) for f in current_hard}
    candidate_keys = {_finding_subject_key(f) for f in candidate_hard}
    introduced = tuple(
        f for f in candidate_hard if _finding_subject_key(f) not in current_keys)
    resolved = tuple(
        f for f in current_hard if _finding_subject_key(f) not in candidate_keys)
    typed_segments = tuple(ChapterSegment.model_validate(item) for item in segments)
    subject = _canonical_sha256({
        'output_ordinal': ordinal,
        'current_chapter_sha256': current_sha256,
        'rules_fingerprint': prov.rules_fingerprint('stage5'),
        'normalized_segments': [
            segment.model_dump(mode='json') for segment in typed_segments],
        'findings': [_preview_finding_payload(f) for f in findings],
        'current_hard': [_preview_finding_payload(f) for f in current_hard],
        'introduced_hard': [_preview_finding_payload(f) for f in introduced],
        'resolved_hard': [_preview_finding_payload(f) for f in resolved],
    })
    return _EditEvaluation(
        ordinal=ordinal,
        entry=entry,
        book=book,
        current=current,
        current_chapter_sha256=current_sha256,
        segments=typed_segments,
        findings=tuple(findings),
        errors=tuple(errors),
        warnings=tuple(warnings),
        current_hard=current_hard,
        introduced_hard=introduced,
        resolved_hard=resolved,
        preview_subject_sha256=subject,
    )


def _edit_response_from_path(
    ctx: RunContext,
    edit_path: str,
    *,
    operation: str,
) -> tuple[str, int, ChapterResponse]:
    raw_path = os.fspath(edit_path)
    candidate = Path(raw_path)
    if candidate.is_absolute():
        # Stage5 HTTP 依 R-11.3 把一次性稿放在书外 `_service/edits/`，再把可信
        # 绝对路径交给 CLI；不能套 stage4/6/7 的“绝对路径也必须书内”边界。
        candidate = candidate.resolve()
    else:
        root = ctx.novel_dir.resolve()
        candidate = (root / candidate).resolve()
        try:
            candidate.relative_to(root)
        except ValueError:
            raise TerminalState(
                f'{operation} 的相对路径必须留在当前书目录 {root} 内：{edit_path!r}') from None
    p = os.fspath(candidate)
    if not p or not os.path.isfile(p):
        raise TerminalState(f'{operation} 要一份人工稿的路径（这一次是 {edit_path!r}）')
    raw_edit = Path(p).read_text(encoding='utf-8').strip()
    ordinal = _ordinal_of_edit(p, raw_edit)
    try:
        edited = ChapterResponse.model_validate_json(raw_edit)
    except Exception as exc:
        raise TerminalState(
            f'{operation} 的 schema v2 文件必须是 '
            '{"segments":[{"slot":"s01","text":"..."},...]}，'
            '不再接受无法恢复 segment 边界的整章纯文本'
        ) from exc
    return p, ordinal, edited


def run_edit_preview(ctx: RunContext, edit_path: str) -> DraftEditPreview:
    """``--preview-edit``：stdout-only 只读预览；一个产物字节都不改。"""
    _path, ordinal, edited = _edit_response_from_path(
        ctx, edit_path, operation='--preview-edit')
    return prepare_edit_preview(ctx, ordinal, edited).preview()


def _human_action_kwargs(
    ctx: RunContext,
    *,
    ordinal: int,
    actor: str,
    reason: str,
    confirmation: str,
    current_chapter_sha256: str,
    preview_subject_sha256: str,
    result_chapter_sha256: str,
    introduced_hard_rules: list[str],
) -> dict[str, Any]:
    return {
        'sid': ctx.sid,
        'output_ordinal': ordinal,
        'actor': actor,
        'reason': reason,
        'confirmation': confirmation,
        'current_chapter_sha256': current_chapter_sha256,
        'preview_subject_sha256': preview_subject_sha256,
        'result_chapter_sha256': result_chapter_sha256,
        'introduced_hard_rules': sorted(set(introduced_hard_rules)),
        'destination_rel': _rel(ordinal),
        'run_id': ctx.run_id,
        'code': ctx.code,
    }


def _reconcile_landed_human_action(
    ctx: RunContext,
    store: ArtifactStore,
    chapter: Chapter,
    prepared: human_actions.HumanEditAction,
) -> CheckReport:
    """canonical 已落但 committed 未必落：不造新轮，补 trim/report/审计相。"""
    ordinal = prepared.output_ordinal
    rel = _rel(ordinal)
    if prepared.destination_rel != rel or chapter.output_ordinal != ordinal:
        raise ProvenanceRefused(
            f'human action {prepared.action_id} 的落点/章号与 current canonical 不一致')
    book = review.load_book(ctx, store)
    entry = book.chapters.get(ordinal)
    if entry is None:
        raise TerminalState(
            f'human action {prepared.action_id} 已有 landed canonical，'
            f'但当前计划不再含第 {ordinal} 章；拒绝猜测补账')
    segments = [segment.model_dump() for segment in chapter.segments]
    mat = prompts.build_material(entry, book).given
    findings, errors, _warnings = _judge(
        chapter.text,
        entry,
        book,
        mat=mat,
        account=_inspection_account(segments, book),
        segments=segments,
    )
    audit_rel = artifact_rel('stage5.trim_audit', nn=ordinal)
    resolution_rel = artifact_rel('stage5.trim_resolution', nn=ordinal)
    with store.history_transaction(rel, audit_rel, resolution_rel):
        where, trim_notes = _commit_canonical_chapter(
            store,
            chapter,
            expected_ordinal=ordinal,
            entry=entry,
            commit=lambda canonical_rel, _artifact: store.path(canonical_rel),
        )
        note = ([
            f'⚠ 人工稿仍有 {len(errors)} 条阻断级判定，'
            f'以 {prepared.confirmation} 落盘供最终 Review 收口：{errors[0]}；'
            '正式下游会由 accepted Review / typed closure 闸阻断',
        ] if errors else [])
        note.extend(trim_notes)
        report = _report(
            ctx,
            store,
            written=[f'第 {ordinal} 章已落盘 → {where}；补齐 human action committed'],
            blocked=note,
            book=book,
            judged={ordinal: list(findings)},
        )
        human_actions.commit_edit(
            ctx.novel_dir.parent,
            **_human_action_kwargs(
                ctx,
                ordinal=ordinal,
                actor=prepared.actor,
                reason=prepared.reason,
                confirmation=prepared.confirmation,
                current_chapter_sha256=prepared.current_chapter_sha256,
                preview_subject_sha256=prepared.preview_subject_sha256,
                result_chapter_sha256=prepared.result_chapter_sha256,
                introduced_hard_rules=prepared.introduced_hard_rules,
            ),
        )
    return report


def run_apply_edit(
    ctx: RunContext,
    edit_path: str,
    *,
    actor: str,
    reason: str,
    current_chapter_sha256: str,
    preview_subject_sha256: str,
    confirmation: str,
) -> CheckReport:
    """把一次双 SHA 锁定的人工预览落成新 canonical 轮，并追加人工审计。

    preview 与 apply 共用 :func:`prepare_edit_preview`。任何 current chapter、归一结果、
    判据指纹或 finding 漂移都会改变其中一把锁并在归档前拒绝。新增一般 HARD 只能
    明示为 ``working_draft``；``paragraph_budget_missed`` 在两种确认下都拒绝。
    多次 edit 不自动产 Review，最后由 Commander 显式运行一次 ``stage5.review``。
    """
    if not actor.strip() or not reason.strip():
        raise TerminalState('--apply-edit 必须显式给非空 actor 与 reason')
    if confirmation not in STAGE5_EDIT_CONFIRMATIONS:
        raise TerminalState(
            f'--edit-confirmation 只接受 {list(STAGE5_EDIT_CONFIRMATIONS)}，实得 {confirmation!r}')
    p, ordinal, edited = _edit_response_from_path(
        ctx, edit_path, operation='--apply-edit')
    base = ctx.novel_dir.parent
    request_id = human_actions.request_id_of(
        sid=ctx.sid,
        output_ordinal=ordinal,
        actor=actor,
        reason=reason,
        confirmation=confirmation,
        current_chapter_sha256=current_chapter_sha256,
        preview_subject_sha256=preview_subject_sha256,
    )

    # 服务作业的进程内书锁不覆盖直 CLI / 多服务实例。跨进程章锁必须从
    # “重算 preview 双锁”一直握到 canonical+trim+committed 全部落稳。
    with stage5_runs.commit_ownership(ctx.novel_dir, ordinal):
        store = ArtifactStore(ctx)
        require_canonical_layout(store)
        rel = _rel(ordinal)

        # kill 可能发生在 canonical os.replace 之后、committed append 之前。
        # 先按 request intent + 当前完整文件 SHA 找 prepared；精确命中才补账，
        # 不重跑 write_round。API 重试会从同一 segments 重建一次性上传，解析完即可
        # 走本分支，不依赖上次请求已在 finally 删除的那条旧路径。
        if store.exists(rel):
            landed, landed_sha256 = store.read_own_with_sha256(Chapter, rel)
            prepared, _committed = human_actions.request_state(
                base,
                request_id,
                result_chapter_sha256=landed_sha256,
            )
            if prepared is not None:
                return _reconcile_landed_human_action(
                    ctx, store, landed, prepared)

        evaluated = prepare_edit_preview(
            ctx, ordinal, edited, store=store)
        if current_chapter_sha256 != evaluated.current_chapter_sha256:
            raise SourceConflict(
                f'第 {ordinal} 章已在 preview 后变化：提交锁 '
                f'{current_chapter_sha256[:12]}…，当前 '
                f'{evaluated.current_chapter_sha256[:12]}…；请重新 preview')
        if preview_subject_sha256 != evaluated.preview_subject_sha256:
            raise SourceConflict(
                f'第 {ordinal} 章 edit preview 主体已漂移：提交锁 '
                f'{preview_subject_sha256[:12]}…，当前 '
                f'{evaluated.preview_subject_sha256[:12]}…；'
                '请按当前归一与判据重新 preview')

        paragraph_hard = [
            f for f in evaluated.findings if f.rule == 'paragraph_budget_missed']
        if paragraph_hard:
            raise TerminalState(
                f'人工稿仍未满足段落数硬闸：{paragraph_hard[0].detail}。'
                f'文件未落盘，请继续编辑原文件 {p}')
        required = evaluated.required_confirmation
        if confirmation != required:
            introduced = sorted({f.rule for f in evaluated.introduced_hard})
            raise SourceConflict(
                f'第 {ordinal} 章当前预览要求 confirmation={required!r}，'
                f'提交却是 {confirmation!r}；introduced_hard={introduced}。'
                '不得把新增 HARD 伪装成 formal，也不得把无新增 HARD 的稿伪装成工作稿')

        prev_inputs = dict(evaluated.current.provenance.inputs)
        rnd = store.next_round(rel)
        extra: dict[str, Any] = {'round': rnd, 'origin': 'human'}
        if not prev_inputs:
            extra['input_basis'] = 'apply_edit_current_validation'
        provenance = ctx.free_provenance(RoundFreeProvenance, **extra)
        if prev_inputs:
            provenance = provenance.model_copy(update={
                'inputs': {
                    key: InputEdge.model_validate(value)
                    for key, value in prev_inputs.items()
                },
            })
        segments = [segment.model_dump() for segment in evaluated.segments]
        art = _chapter_record(
            evaluated.entry,
            segments,
            provenance=provenance,
        )
        expected_result_sha256 = store.prospective_sha256(rel, art)
        action_kwargs = _human_action_kwargs(
            ctx,
            ordinal=ordinal,
            actor=actor,
            reason=reason,
            confirmation=confirmation,
            current_chapter_sha256=evaluated.current_chapter_sha256,
            preview_subject_sha256=evaluated.preview_subject_sha256,
            result_chapter_sha256=expected_result_sha256,
            introduced_hard_rules=sorted({
                f.rule for f in evaluated.introduced_hard}),
        )
        # prepared 在任何产物变更之前落；进程被硬杀后，旧请求可用 expected result
        # 精确识别“章已落、账未提交”并只补 committed，不会再造一轮。
        human_actions.prepare_edit(base, **action_kwargs)

        audit_rel = artifact_rel('stage5.trim_audit', nn=ordinal)
        resolution_rel = artifact_rel('stage5.trim_resolution', nn=ordinal)
        with store.history_transaction(rel, audit_rel, resolution_rel):
            _locked_current, locked_sha256 = store.read_own_with_sha256(
                Chapter, rel, require_current=True)
            if locked_sha256 != evaluated.current_chapter_sha256:
                raise SourceConflict(
                    f'第 {ordinal} 章在 apply 评估与提交之间变化：'
                    f'{evaluated.current_chapter_sha256[:12]}… -> '
                    f'{locked_sha256[:12]}…；一个字节都没动，请重新 preview')
            where, trim_notes = _commit_canonical_chapter(
                store,
                art,
                expected_ordinal=ordinal,
                entry=evaluated.entry,
                commit=lambda canonical_rel, artifact: store.write_round(canonical_rel, artifact),
            )
            landed, result_sha256 = store.read_own_with_sha256(Chapter, rel)
            if landed.output_ordinal != ordinal:
                raise ProvenanceRefused(f'{rel} 写后读回章号漂移')
            if result_sha256 != expected_result_sha256:
                raise ProvenanceRefused(
                    f'{rel} 写后完整 SHA 与 prepared 预期不一致：'
                    f'{expected_result_sha256} != {result_sha256}')
            note = ([
                f'⚠ 人工稿仍有 {len(evaluated.errors)} 条阻断级判定，'
                f'以 {confirmation} 落盘供最终 Review 收口：{evaluated.errors[0]}；'
                '正式下游会由 accepted Review / typed closure 闸阻断',
            ] if evaluated.errors else [])
            note.extend(trim_notes)
            report = _report(
                ctx,
                store,
                written=[f'第 {ordinal} 章 → {where}'],
                blocked=note,
                book=evaluated.book,
                judged={ordinal: list(evaluated.findings)},
            )
            human_actions.commit_edit(base, **action_kwargs)
        return report


_EDIT_ORDINAL = re.compile(r'ch(\d+)')


def _ordinal_of_edit(path: str, text: str) -> int:
    """人工稿是第几章。**从文件名认**（`chNN...`），认不出**当场炸**。

    ⚠ 不从正文猜：猜错会把一章的人工修改**落到另一章头上**，而两边都不出声。
    """
    m = _EDIT_ORDINAL.search(os.path.basename(path))
    if m is None:
        raise TerminalState(
            f'从文件名认不出这是第几章：{os.path.basename(path)!r}。'
            f'⚠ 不从正文猜——猜错会把一章的人工修改落到另一章头上，而两边都不出声。'
            f'把结构化人工稿命名成 `chNN.json`')
    del text
    return int(m.group(1))


# ─────────────────────────────────────────────────────────────────────────────
# 六、报告
# ─────────────────────────────────────────────────────────────────────────────

def _report(ctx: RunContext, store: ArtifactStore, *,
            written: list[str], blocked: list[str], book: M.Book,
            judged: dict[int, list] | None = None) -> CheckReport:
    """写作相的报告：**这一次碰过的那几章**逐条报出判据。

    ⚠⚠ **书级/跨章那 14 条落 `unver` 而不是 `skipped`**（`checks.check(phase='write')`）。
    两者是两种颜色：`skipped` 的措辞是「实现未被调用」，用在这里是**一句假话**——
    它们的实现在，只是这一相判不了。而一份 50 行全 `skipped` 的报告徽章是 `fail`，
    读起来像「全查过全没过」，实际上什么都没说。**要看全书 50 行，跑 `stage5.review`。**

    ⚠ 这一次一章都没碰（比如 `--revalidate` 一份 `.INVALID` 都没有）⇒ 逐章那几条
    也落 `unver`：「这一次没有任何一章被判过」与「判过、干净」必须分得开。
    """
    for line in (*written, *blocked):
        print(f'    · {line}', flush=True)
    rel = 'stage5'
    by_rule: dict[str, list] = {}
    n = len(judged or {})
    for fs in (judged or {}).values():
        for f in fs:
            by_rule.setdefault(f.rule, []).append(f)
    empty = '这一次一章都没碰过——**没有任何一章被判过**，不是判过没问题'
    scanned = {r: f'{n} 章（这一次碰过的）' for r in checks._FROM_CHAPTER}
    outcomes = checks.check(
        chapter_findings_by_rule=by_rule,
        scanned=scanned, blind={},
        unver_empty={}, phase='write') if n else [
        unverifiable(r, empty, scanned='0 章')
        for r in (*checks._FROM_CHAPTER, *checks._FROM_REVIEW)]
    # ⚠ 这里曾追加 `stage5.semantic` 的两条 UNVER 占位（写作相判不了书级语义）。
    #   该节点于 2026-08-17 下线，两条判据一并注销 ⇒ 不再追加占位行。
    any_rel = next(iter(sorted(
        p for p in glob.glob(os.path.join(store.path(rel), 'ch*.json'))
        if re.fullmatch(r'ch\d+\.json', os.path.basename(p)))), None)
    fresh_rel = (f'{rel}/{os.path.basename(any_rel)}' if any_rel
                 else review.WRITER_CONTEXT)
    return build(sid=ctx.sid, node=ctx.node, freshness=store.freshness(fresh_rel),
                 outcomes=outcomes, accounting=ctx.accounting,
                  invalid=bool(glob.glob(os.path.join(store.path(rel), 'ch*.INVALID.json'))))


def run_check_only(ctx: RunContext) -> CheckReport:
    """写作节点没有 `--check-only`（判据表由 `stage5.review` 报）。**说出来，不静默。**"""
    raise UpstreamMissing(
        '`stage5.write` 不接 `--check-only`——判据表由 `stage5.review` 报（两相共用一张表）')
