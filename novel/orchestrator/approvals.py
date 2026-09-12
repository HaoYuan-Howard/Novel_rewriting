#!/usr/bin/env python3
from __future__ import annotations

from novel.stages.stage2 import grounding as grounding_io

from contracts.artifacts import (
    STAGE0_CLEAN_TEXT, STAGE0_REVISIONS, STAGE1_HOOK_COPY, STAGE1_MAIN_TEXT,
    STAGE2_CHAPTER_BLUEPRINTS, STAGE2_KNOWLEDGE_LINKS,
    STAGE2_GLOBAL_GROUNDING, STAGE2_GLOBAL_REVIEW,
    STAGE4_BLIND_CANDIDATE, STAGE4_EVIDENCE_BINDING,
    STAGE4_PLAN_CHAPTERS, STAGE4_WRITER_CONTEXT,
    STAGE6_HOOK, STAGE7_NAME_MAP, artifact_rel,
)

import hashlib
import json
import os
import threading
from collections.abc import Iterator, Mapping
from contextlib import contextmanager
from contextvars import ContextVar
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Literal

from pydantic import Field, ValidationError, model_validator
from contracts.schema.knowledge_links import KnowledgeLinkHistory
from contracts.schema.plot_materials import PlotChapterReviewMaterials

from contracts.invariants import (
    DESIGN_REPAIR_CODES_RETIRED,
    APPROVAL_GATES,
    APPROVAL_SEEN,
    DESIGN_REPAIR_CODES,
    DESIGN_REVIEW_CONTRACT,
    STRUCTURE_CHANGE_KINDS,
    STRUCTURE_METRIC_CONTRACT,
    STRUCTURE_REVIEW_CONTRACT,
    STYLE_MEASUREMENT_CONTRACT,
)
from contracts.rules import REGISTRY
from contracts.schema import artifact_key, artifact_spec
from contracts.schema.api import (
    ApprovalAction,
    ApprovalBackfillResult,
    ApprovalDoc,
    ApprovalWorkpack,
    BlueprintReviewWorkpack,
    BlueprintSeenRequest,
    Stage2FinalReviewRequest, Stage2GlobalSeenRequest,
    Stage2NodeReviewRequest, Stage2NodeReviewDecision, Stage2NodeReviewView,
    DesignBindingInfeasibility,
    DesignAcceptedChapterConstraint,
    DesignBlindChapterTruth,
    DesignChapterReview,
    DesignActiveConstraint,
    DesignConstraintCatalog,
    DesignConstraintOperations,
    DesignChapterSubject,
    DesignPreflightFailure,
    DesignPreflightSummary,
    DesignRepairChapterScope,
    DesignRepairConstraint,
    DesignRepairScope,
    DesignReviewBasis,
    DesignReviewDecision,
    DesignReviewHistoryEntry,
    DesignReviewMaterialized,
    DesignReviewRepairCapability,
    DesignReviewRequest,
    DesignReviewTemplate,
    DesignReviewView,
    DesignStrategyReview,
    DesignStrategyReplanConstraint,
    DesignStrategyReplanFeedback,
    DesignStrategySubject,
    DesignStructureEvidence,
    Stage5ClosureDecision,
    Stage5ClosureMaterialized,
    Stage5ClosureRequest,
    Stage5ClosureTemplate,
    Stage5ClosureView,
    Stage5FindingGroupAck,
    Stage5MustAppearDecision,
    StructureChapterDecision,
    StructureApprovalDecision,
    StructureApprovalRequest,
    StructureApprovalView,
    WorkpackGroup,
    WorkpackItem,
    WorkpackReading,
    WritingApprovalDecision,
    WritingApprovalRequest,
    WritingApprovalView,
)
from contracts.schema.evaluation import GlobalReview
from contracts.schema.orchestration import NovelOverview
from contracts.schema.report import CheckReport
from contracts.schema.stage0 import Stage0Revisions
from contracts.schema.stage1 import HookCopy, MainText
from contracts.schema.stage2 import ChapterBlueprints, GlobalGrounding
from contracts.schema.stage4 import (
    BindingConcern, BlindCandidate, BlindChapterEditRequest,
    EvidenceBinding, PlanChapters,
)
from contracts.schema.stage6 import Hook
from contracts.schema.stage7 import NameMap
from contracts.schema.base import StrictModel
from novel import paths, prov
from contracts.schema.stage2_review import Stage2ReviewCoverage
from novel.errors import (
    ApprovalFrozen, ApprovalRequired, ArtifactUnreadable, GateBlocked, HumanEditProtected,
    NovelError, ProvenanceRefused, UpstreamMissing, UpstreamUnproven,
)
from novel.llm import stage5_runs
from novel.orchestrator import review_inheritance, state
from novel.quality.global_review import (
    review_status as global_review_status,
    require_current as require_current_global_review,
)
from novel.stages.stage4.phase_state import current_infeasible_binding
from novel.store import ArtifactStore

APPROVALS_DIRNAME = 'approvals'
_LOCK = threading.RLock()
_READ_CODE: ContextVar[str | None] = ContextVar(
    'approval_read_code', default=None)
_STAGE2_CLOSURE_CODE: ContextVar[str | None] = ContextVar(
    'stage2_closure_code', default=None)
_READ_OVERVIEW: ContextVar[NovelOverview | None] = ContextVar(
    'approval_read_overview', default=None)

#: 正在被移入回收站的书。**`_LOCK` 保护它**，与审批写入同一把锁——
#: 这样「冻结」与「写一份审批」之间没有缝，两者要么先后、要么其中一个明确 409。
#: ⚠ 只在内存，与 `jobs.JobRegistry` 同一条理由（`R-14.6`）：写下来就要有陈旧清理
#: 策略，而**一份会说谎的冻结名单比没有名单更坏**。删除是短操作（拉一个 CLI 子进程
#: 移目录），进程死了这一格跟着消失，恢复由 `trash.sweep_incomplete` 从盘上重建。
_FROZEN_FOR_TRASH: set[str] = set()


@contextmanager
def frozen_for_trash(sid: str) -> Iterator[None]:
    """删除这本书的整段时间里冻结它的审批写入。**回收站的进程内那一半。**

    ⚠⚠ 这一格补的是一条真实的两半竞态：书目录与审批文件由 CLI 子进程移动，
    而**审批写入发生在服务父进程**。父进程只占了书级作业锁（`jobs.active_key`），
    审批那条路一把都不占 ⇒ 子进程刚把 `<sid>.json` 移进回收站，父进程的
    `approve_gate` 就能把它**重新写出来**，留下一本「书已删除、审批还活着」的孤儿；
    反过来若子进程随后失败回滚，回滚条件 `not approval.exists()` 已被那次重写破坏，
    归档的那一份就永远留在半成品回收站里。
    ⇒ 冻结**只按 sid**，不持有 `_LOCK` 本身：删一本书不该把别的书的审批全挡住。
    """
    with _LOCK:
        if sid in _FROZEN_FOR_TRASH:
            raise ApprovalFrozen(f'{sid} 已经有一个删除在进行中')
        _FROZEN_FOR_TRASH.add(sid)
    try:
        yield
    finally:
        with _LOCK:
            _FROZEN_FOR_TRASH.discard(sid)


def _refuse_if_frozen(sid: str) -> None:
    with _LOCK:
        if sid in _FROZEN_FOR_TRASH:
            raise ApprovalFrozen(
                f'{sid} 正在被移入回收站，这次审批写入不落盘——'
                '删除完成后这本书不再存在；若删除失败，重新打开这本书再批')


class _ApprovalProof(StrictModel):
    """Exact current subject of one persisted human/service decision.

    The service record is deliberately not an artifact and therefore does not
    participate in pipeline provenance.  It still needs the same two anchors:
    the current clean-text root and the exact bytes a person accepted.  The
    decision digest additionally binds the exact human decision scope.
    """

    root_clean_sha1: str = Field(pattern=r'^[0-9a-f]{40}$')
    artifacts: dict[str, str] = Field(min_length=1)
    decision_sha256: str = Field(pattern=r'^[0-9a-f]{64}$')

    @model_validator(mode='after')
    def _artifact_digests_are_full_sha256(self) -> _ApprovalProof:
        if any(
            not rel or len(digest) != 64
            or any(char not in '0123456789abcdef' for char in digest)
            for rel, digest in self.artifacts.items()
        ):
            raise ValueError('approval artifact subjects must be non-empty full SHA-256 values')
        return self


class _StoredStructureDecision(StrictModel):
    """一项服务审批历史：公开 typed 决定 + 当时主体的内部 proof。

    ``chapter_subject_sha256`` 是从 v6 起加入的私有 additive 快照：它让
    工作包只复用业务面仍精确相同的历史章 accept；当前 v7 记录继续承载。
    缺少快照的旧记录只能走整书 subject + 完整 proof 仍精确相同的保守
    fallback。该字段不进入公开 ``ApprovalDoc`` v6。
    """

    actor: str = Field(min_length=1)
    reason: str = Field(min_length=1)
    subject_sha256: str = Field(pattern=r'^[0-9a-f]{64}$')
    chapters: list[StructureChapterDecision] = Field(min_length=1)
    decided_at: str = Field(min_length=1)
    proof: _ApprovalProof
    chapter_subject_sha256: dict[int, str] = Field(default_factory=dict)

    @model_validator(mode='after')
    def _chapter_subject_snapshot_is_complete_when_present(
        self,
    ) -> _StoredStructureDecision:
        if not self.chapter_subject_sha256:
            return self
        expected = {row.output_ordinal for row in self.chapters}
        if set(self.chapter_subject_sha256) != expected:
            raise ValueError('structure 逐章 subject 快照必须精确覆盖决定章集')
        if any(
            len(value) != 64
            or any(char not in '0123456789abcdef' for char in value)
            for value in self.chapter_subject_sha256.values()
        ):
            raise ValueError('structure 逐章 subject 快照必须是完整小写 SHA-256')
        return self


class _StoredWritingDecision(StrictModel):
    """一项 Stage5 写作授权历史：typed 决定 + 当时完整主体 proof。"""

    actor: str = Field(min_length=1)
    reason: str = Field(min_length=1)
    subject_sha256: str = Field(pattern=r'^[0-9a-f]{64}$')
    decided_at: str = Field(min_length=1)
    proof: _ApprovalProof


class _StoredDesignDecision(DesignReviewDecision):
    """一次 Stage4A 盲审历史：完整 typed 决定 + 当时候选的精确字节 proof。"""

    proof: _ApprovalProof


class _StoredDesignDecisionSlim(StrictModel):
    """新写入的 Stage4A 决定：只存 typed facts 与内部身份，不复制 workpack。"""

    record_kind: Literal['slim_v1'] = 'slim_v1'
    request: DesignReviewRequest
    candidate_sha256: str = Field(pattern=r'^[0-9a-f]{64}$')
    aggregate_subject_sha256: str = Field(pattern=r'^[0-9a-f]{64}$')
    strategy_subject_sha256: str = Field(pattern=r'^[0-9a-f]{64}$')
    strategy_epoch_sha256: str = Field(pattern=r'^[0-9a-f]{64}$')
    chapter_subject_sha256: dict[int, str]
    repair_capability: DesignReviewRepairCapability
    binding_infeasibility: DesignBindingInfeasibility | None = None
    decision_sha256: str = Field(pattern=r'^[0-9a-f]{64}$')
    decided_at: str = Field(min_length=1)
    proof: _ApprovalProof

    @model_validator(mode='after')
    def _chapter_subjects_cover_request(self) -> _StoredDesignDecisionSlim:
        expected = {row.output_ordinal for row in self.request.chapters}
        if set(self.chapter_subject_sha256) != expected:
            raise ValueError('slim design 章 subject 必须精确覆盖 typed 决定')
        return self


_StoredDesignHistory = _StoredDesignDecision | _StoredDesignDecisionSlim


class _StoredClosureDecision(StrictModel):
    """一次 Stage5 闭合回执：完整 typed 决定 + 提交时盘面的精确字节 proof。"""

    actor: str = Field(min_length=1)
    reason: str = Field(min_length=1)
    workpack_sha256: str = Field(pattern=r'^[0-9a-f]{64}$')
    review_sha256: str = Field(pattern=r'^[0-9a-f]{64}$')
    must_appear: list[Stage5MustAppearDecision] = Field(default_factory=list)
    group_acks: list[Stage5FindingGroupAck] = Field(default_factory=list)
    decision_sha256: str = Field(pattern=r'^[0-9a-f]{64}$')
    decided_at: str = Field(min_length=1)
    proof: _ApprovalProof


class _Stage2PatchReceipt(StrictModel):
    """局部补丁提交前冻结的身份；正式产物仍只有 producer 的书内落点。"""

    request_sha256: str = Field(pattern=r'^[0-9a-f]{64}$')
    before_token: str = Field(min_length=1)
    result_subject_sha256: str = Field(pattern=r'^[0-9a-f]{64}$')
    global_sha256: str = Field(pattern=r'^[0-9a-f]{64}$')
    decision_timestamp: str | None = None


class _ApprovalRecordV8(StrictModel):
    """唯一现役盘上审批；当前主体与不可变历史仍完整校验，不继承旧版本读取器。"""

    v: Literal[8] = 8
    sid: str = Field(min_length=1)
    gate: dict[str, bool]
    seen: dict[str, bool]
    writing: bool = False
    gate_proofs: dict[str, _ApprovalProof | None]
    seen_proofs: dict[str, _ApprovalProof | None]
    writing_proof: _ApprovalProof | None = None
    structure_history: list[_StoredStructureDecision] = Field(default_factory=list)
    structure_decision: _StoredStructureDecision | None = None
    writing_history: list[_StoredWritingDecision] = Field(default_factory=list)
    writing_decision: _StoredWritingDecision | None = None
    #: 完整与 slim 历史仍保留原证据；历史能否生效由当前审阅契约另行核验。
    design_history: list[_StoredDesignHistory] = Field(default_factory=list)
    design_decision: _StoredDesignHistory | None = None
    #: 只保留谁在何主体上的审计；proof 仍是当前性的唯一依据。
    #: 缺席时不伪造 actor，也不能用这份明文摘要恢复审批权限。
    seen_subjects: dict[str, dict[str, str]] = Field(default_factory=dict)
    closure_history: list[_StoredClosureDecision] = Field(default_factory=list)
    closure_decision: _StoredClosureDecision | None = None
    stage2_review_history: list[Stage2NodeReviewDecision] = Field(default_factory=list)
    stage2_reviews: dict[str, Stage2NodeReviewDecision] = Field(default_factory=dict)
    stage2_rework_completed: list[str] = Field(default_factory=list)
    stage2_patch_receipts: dict[str, _Stage2PatchReceipt] = Field(default_factory=dict)

    @model_validator(mode='after')
    def _record_is_complete_and_proved(self) -> _ApprovalRecordV8:
        if set(self.gate) != set(APPROVAL_GATES):
            raise ValueError('gate must contain exactly s0,s1,s2,s4,s5,s6,s7')
        if set(self.seen) != set(APPROVAL_SEEN):
            raise ValueError('seen must contain exactly read/global')
        if set(self.gate_proofs) != set(APPROVAL_GATES):
            raise ValueError('gate_proofs must contain exactly s0,s1,s2,s4,s5,s6,s7')
        if set(self.seen_proofs) != set(APPROVAL_SEEN):
            raise ValueError('seen_proofs must contain exactly read/global')
        for key, approved in self.gate.items():
            if approved != (self.gate_proofs[key] is not None):
                raise ValueError(f'gate {key} and its current-subject proof must agree')
        for key, seen in self.seen.items():
            if seen != (self.seen_proofs[key] is not None):
                raise ValueError(f'seen {key} and its current-subject proof must agree')
        if self.writing != (self.writing_proof is not None):
            raise ValueError('writing and writing_proof must agree')
        if (self.structure_decision is None) != (not self.structure_history):
            raise ValueError('structure_decision 与非空 structure_history 必须同进同出')
        if (self.structure_decision is not None
                and self.structure_decision != self.structure_history[-1]):
            raise ValueError('structure_decision 必须是 structure_history 最后一项')
        if (self.writing_decision is None) != (not self.writing_history):
            raise ValueError('writing_decision 与非空 writing_history 必须同进同出')
        if (self.writing_decision is not None
                and self.writing_decision != self.writing_history[-1]):
            raise ValueError('writing_decision 必须是 writing_history 最后一项')
        if self.gate['s4']:
            decision = self.structure_decision
            if decision is None or any(
                row.decision != 'accept' for row in decision.chapters
            ):
                raise ValueError('s4 gate 只能由逐章全 accept 的 typed 结构裁决打开')
            if self.gate_proofs['s4'] != decision.proof:
                raise ValueError('s4 gate proof 必须等于当前 typed 结构裁决 proof')
        return self

    @model_validator(mode='after')
    def _design_history_has_one_current_tail(self) -> _ApprovalRecordV8:
        if (self.design_decision is None) != (not self.design_history):
            raise ValueError('design_decision 与非空 design_history 必须同进同出')
        if (
            self.design_decision is not None
            and self.design_decision != self.design_history[-1]
        ):
            raise ValueError('design_decision 必须是 design_history 最后一项')
        return self

    @model_validator(mode='after')
    def _stage2_history_has_current_heads(self) -> _ApprovalRecordV8:
        latest = {row.node: row for row in self.stage2_review_history}
        if self.stage2_reviews != latest:
            raise ValueError('Stage2 当前决定必须逐节点等于不可变历史的最后一项')
        return self

    @model_validator(mode='after')
    def _closure_history_has_one_current_tail(self) -> _ApprovalRecordV8:
        if (self.closure_decision is None) != (not self.closure_history):
            raise ValueError('closure_decision 与非空 closure_history 必须同进同出')
        if (
            self.closure_decision is not None
            and self.closure_decision != self.closure_history[-1]
        ):
            raise ValueError('closure_decision 必须是 closure_history 最后一项')
        if self.gate['s5'] and self.closure_decision is None:
            raise ValueError('s5 gate 只能由当前 typed closure 回执打开')
        return self


def _path(base: str | os.PathLike[str], sid: str) -> Path:
    return Path(os.fspath(base)) / '_service' / APPROVALS_DIRNAME / f'{sid}.json'


def _lock_path(base: str | os.PathLike[str], sid: str) -> Path:
    """每本书审批 RMW 的跨进程锁；锁文件不是审批事实，长期保留无害。"""
    checked = paths.check_sid(sid)
    return (
        Path(os.fspath(base)).resolve()
        / '_service' / APPROVALS_DIRNAME / '.locks' / f'{checked}.lock'
    )


@contextmanager
def approval_exclusion_lock(
    base: str | os.PathLike[str], sid: str,
) -> Iterator[None]:
    """串行化一份审批文件的完整 read-modify-write 与回收站移动。

    ``threading.RLock`` 只管当前服务进程；Commander 从 CLI apply，HTTP 从服务
    路由 apply，两者可能是两个进程。这里把进程内锁和 OS 文件锁叠在同一个公开
    边界上，trash 也占同一把锁，避免审批更新丢失或书删完后审批复活。
    """
    lock_path = _lock_path(base, sid)
    lock_path.parent.mkdir(parents=True, exist_ok=True)
    with _LOCK:
        with lock_path.open('a+b') as handle:
            handle.seek(0, os.SEEK_END)
            if handle.tell() == 0:
                handle.write(b'\0')
                handle.flush()
            handle.seek(0)
            if os.name == 'nt':
                import msvcrt

                msvcrt.locking(handle.fileno(), msvcrt.LK_LOCK, 1)
                try:
                    yield
                finally:
                    handle.seek(0)
                    msvcrt.locking(handle.fileno(), msvcrt.LK_UNLCK, 1)
            else:
                import fcntl

                fcntl.flock(handle.fileno(), fcntl.LOCK_EX)
                try:
                    yield
                finally:
                    fcntl.flock(handle.fileno(), fcntl.LOCK_UN)


@contextmanager
def approval_file_lock(
    base: str | os.PathLike[str], sid: str,
) -> Iterator[None]:
    """新审批的写准入先于互斥副作用；删除只使用 exclusion lock。"""
    _require_approval_workflow(base)
    with approval_exclusion_lock(base, sid):
        yield


def _require_approval_workflow(base: str | os.PathLike[str]) -> None:
    """审批只消费已初始化工作流身份；错误请求不能创建任务或重发预算。"""
    from novel.work_units import WorkUnitRefused, workflow_manifest
    if workflow_manifest(base) is None:
        raise WorkUnitRefused(
            '新审批要求已有当前工作流 manifest；旧 base 请用冻结版本恢复，'
            '新任务先经 Stage0 入口创建 clean 工作流')


@contextmanager
def _read_projection(
    *, overview: NovelOverview | None, code: str | None,
) -> Iterator[None]:
    """把同一次 operator 快照限制在当前只读投影调用栈内。"""
    if overview is None and code is None:
        overview, code = _READ_OVERVIEW.get(), _READ_CODE.get()
    overview_token = _READ_OVERVIEW.set(overview)
    code_token = _READ_CODE.set(code)
    try:
        yield
    finally:
        _READ_CODE.reset(code_token)
        _READ_OVERVIEW.reset(overview_token)


@contextmanager
def read_projection(
    *, overview: NovelOverview, code: str,
) -> Iterator[None]:
    """让同一次只读编排复用已经冻结的 overview / code 坐标。

    这是 invocation-scoped 的投影，不是跨请求缓存。operator 已经完成一次
    code/surface/七层 snapshot 后，queue、inbox 与审批 currentness 都必须在
    这一个上下文里派生；否则每个 gate 会各自重跑整仓 ``code_version``，既慢，
    也可能把同一次 next 拼成多个代码时点。
    """
    _validate_read_projection(overview.sid, overview=overview, code=code)
    with _read_projection(overview=overview, code=code):
        yield


def _projection_code() -> str:
    """有 invocation-scoped 坐标就复用；普通 API 调用仍现场取指纹。"""
    return _READ_CODE.get() or prov.code_version()


def _assert_stage2_closure_code() -> None:
    code = _STAGE2_CLOSURE_CODE.get()
    if code is not None and prov.code_version() != code:
        raise ProvenanceRefused('Stage2 免费汇合期间代码改变；保留已存决定与产物，重新读取恢复入口')


@contextmanager
def stage2_closure_projection(code: str) -> Iterator[None]:
    """一次免费汇合共用代码坐标，产物及审批仍逐次读盘和核验。

    不跨请求缓存 code_version，也不冻结写入前的 overview。写审批前以及
    返回前重新读取真实 Git；变化即拒绝继续，已存决定由原恢复入口补尾。
    """
    active = _STAGE2_CLOSURE_CODE.get()
    if active is not None:
        if active != code:
            raise ProvenanceRefused('嵌套 Stage2 汇合的代码坐标不一致')
        yield
        return
    token = _STAGE2_CLOSURE_CODE.set(code)
    try:
        _assert_stage2_closure_code()
        with _read_projection(overview=None, code=code):
            yield
            _assert_stage2_closure_code()
    finally:
        _STAGE2_CLOSURE_CODE.reset(token)


def projection_code() -> str:
    """返回当前只读 invocation 的代码坐标；普通调用仍现场读取。"""
    return _projection_code()


def _validate_read_projection(
    sid: str, *, overview: NovelOverview | None, code: str | None,
) -> None:
    """只接受完整且属于本书的 invocation-scoped 只读坐标。"""
    if overview is None and code is None:
        current = _READ_OVERVIEW.get()
        if current is not None and current.sid != sid:
            raise ValueError('审批只读投影属于另一书，不能共享快照')
    if (overview is None) != (code is None):
        raise ValueError('审批只读投影的 overview/code 必须成对提供')
    if overview is not None and overview.sid != sid:
        raise ValueError(
            f'审批只读投影属于 sid={overview.sid!r}，不是 {sid!r}')


def _spawns(base: str | os.PathLike[str], sid: str) -> dict[str, list[ApprovalAction]]:
    """Gate follow-ups that still need to run on the current artifact surface.

    These actions used to be an unconditional constant.  That made approving a
    fresh stage2 artifact offer (and then execute) another paid ``stage4.plan``
    run.  The queue and the gate now ask the same resume predicate; the browser
    remains a renderer and never has to infer freshness or cost itself.
    """
    candidates = {
        's5': [ApprovalAction(
            kind='stage6.hook', label='\u751f\u6210\u5f00\u7bc7\u6587\u6848')],
        's6': [ApprovalAction(
            kind='stage7.rename', label='\u6267\u884c\u4eba\u7269\u6539\u540d')],
        's7': [ApprovalAction(
            kind='stage7.assemble', label='\u62fc\u88c5\u6700\u7ec8\u6210\u4e66')],
    }
    overview = _READ_OVERVIEW.get()
    if overview is None:
        overview = state.overview(base, sid)
    spawns = {
        gate: [action for action in actions
               if state.resume_verdict(overview, action.kind)[0] != state.RESUME_SKIP]
        for gate, actions in candidates.items()
    }
    from novel.stages.stage6 import prepare as prepare_stage6
    prepare_state = prepare_stage6.view_state(_store_for(paths.resolve_novel_dir(base, sid)))
    if prepare_state.pending_commit:
        spawns['s5'] = [ApprovalAction(kind='stage6.prepare', label='免费补齐原准备决定', extra=['--recover-edit'])]
    elif prepare_state.state == 'missing':
        spawns['s5'] = [ApprovalAction(kind='stage6.prepare', label='准备原钩子事实与开篇范围')]
    elif prepare_state.state != 'accepted':
        spawns['s5'] = []
    # Stage2 两份付费生成各自确认；只有 Global 当前接受才可发布 Blueprint。
    # 浏览器只渲染服务端动作，不从已读或逐项 PASS 反推节点接受。
    global_action = ApprovalAction(
        kind='stage2.global', label='生成 stage2 全局底座；完成后逐项人工复核')
    blueprint_action = ApprovalAction(
        kind='stage2.blueprint', label='Global 接受后生成 stage2 逐章骨架')
    if state.resume_verdict(overview, global_action.kind)[0] != state.RESUME_SKIP:
        spawns['s1'] = [global_action]
    elif state.resume_verdict(overview, blueprint_action.kind)[0] != state.RESUME_SKIP:
        try:
            require_stage2_review(base, sid, 'stage2.blueprint')
        except (NovelError, OSError, ValueError):
            spawns['s1'] = []
        else:
            spawns['s1'] = [blueprint_action]
    else:
        spawns['s1'] = []
    # Stage4 是两次互相独立的付费确认，不是一个按钮里塞两项作业。A 尚未完成时
    # 只能展示 A；A 已落盘后才展示 B。这样服务端给 GateBar 的永远是一条可预览
    # 动作，浏览器不猜阶段，也不会在一次确认里自动串行两次调用。
    design = ApprovalAction(kind='stage4.design', label='Stage4A 盲结构设计')
    binding = ApprovalAction(kind='stage4.plan', label='Stage4B 证据绑定与计划编译')
    if state.resume_verdict(overview, design.kind)[0] != state.RESUME_SKIP:
        spawns['s2'] = [design]
    elif (
        state.resume_verdict(overview, binding.kind)[0] != state.RESUME_SKIP
        and design_review_is_current(base, sid)
    ):
        spawns['s2'] = [binding]
    else:
        spawns['s2'] = []
    return spawns


def _fresh_record(sid: str, planned: list[int] | None = None) -> _ApprovalRecordV8:
    del planned  # 兼容旧调用缝；结构章集只从当前正式计划读取
    return _ApprovalRecordV8(
        sid=sid,
        gate={key: False for key in APPROVAL_GATES},
        seen={key: False for key in APPROVAL_SEEN},
        gate_proofs={key: None for key in APPROVAL_GATES},
        seen_proofs={key: None for key in APPROVAL_SEEN},
    )


def _view(
    base: str | os.PathLike[str], record: _ApprovalRecordV8, *,
    include_spawns: bool = True,
    operator_projection: bool = False,
) -> ApprovalDoc:
    """服务记录的唯一公开投影；public v6 明示闭合回执与 typed 裁决。"""
    novel_dir = paths.resolve_novel_dir(base, record.sid)
    # DesignReviewView 的 missing/expired 也必须带当前 candidate、aggregate 与
    # repair capability；这些是 queue/inbox 的业务输入，不能为提速造一个
    # 缺字段的“轻量 missing”。operator_projection 只保留为调用意图标记；
    # 它不改变 public view 语义。
    design_review = _design_review_view(novel_dir, record)
    structure = _structure_view(novel_dir, record)
    writing_approval = _writing_view(novel_dir, record)
    closure = _closure_view(novel_dir, record)
    from novel.stages.stage6 import prepare as prepare_stage6
    prepare_state = prepare_stage6.view_state(_store_for(novel_dir))
    spawns = _spawns(base, record.sid) if include_spawns else {}
    decision = design_review.current_decision
    if (
        include_spawns
        and decision is not None
        and design_review.action_review_token is not None
        and design_review.state in (
            'repair_requested', 'strategy_replan_requested',
        )
    ):
        repairing = design_review.state == 'repair_requested'
        spawns = {
            **spawns,
            's2': [],
            's4': [ApprovalAction(
                kind='stage4.design',
                label=(
                    '按当前 Stage4A typed 决定定向返修章节'
                    if repairing else '按当前 Stage4A 策略裁决重新设计'
                ),
                extra=(
                    ['--review-token', design_review.action_review_token]
                    if repairing else [
                        '--replan', '--review-token',
                        design_review.action_review_token,
                    ]
                ),
            )],
        }
    elif (
        include_spawns
        and current_infeasible_binding(_store_for(novel_dir)) is not None
    ):
        # B 的负结论不能替人伪造一份 A repair/replan 决定；只隐藏同一 B，
        # 等待另行提交 hash-bound design review 或人工计划恢复。
        spawns = {**spawns, 's2': [], 's4': []}
    return ApprovalDoc(
        sid=record.sid, gate=record.gate, seen=record.seen,
        stage2_reviews={node: _stage2_review_view(novel_dir, record, node)
                        for node in ('stage2.global', 'stage2.blueprint')},
        writing=record.writing,
        design_review=design_review,
        structure=structure,
        writing_approval=writing_approval,
        closure=closure,
        prepare=prepare_state,
        spawns=spawns,
    )


def _planned(base: str | os.PathLike[str], sid: str) -> list[int] | None:
    novel_dir = paths.resolve_novel_dir(base, sid)
    ctx = state.readonly_ctx(novel_dir, 'stage5.write', _projection_code())
    return state.planned_chapters(ArtifactStore(ctx))


def _read_raw(
    base: str | os.PathLike[str], sid: str, *, require_existing: bool = False,
) -> _ApprovalRecordV8:
    paths.resolve_novel_dir(base, sid)
    p = _path(base, sid)
    if not p.is_file():
        if require_existing:
            raise HumanEditProtected('正式审批在已提交回执复验期间消失，不能推断未提交')
        return _fresh_record(sid, _planned(base, sid))
    try:
        payload = json.loads(p.read_text(encoding='utf-8'))
        if (not isinstance(payload, dict) or type(payload.get('v')) is not int
                or payload['v'] != 8):
            raise ValueError('盘上审批必须显式声明当前整数版本 v=8')
        record = _ApprovalRecordV8.model_validate(payload)
    except (OSError, UnicodeDecodeError, ValueError) as exc:
        raise HumanEditProtected(
            f'审批记录损坏或版本不是当前 v8（旧版本与未来版本均不迁移）；'
            f'旧代码与旧输出根配对读取：{exc}') from exc
    if record.sid != sid:
        raise HumanEditProtected(
            f'approval state belongs to {record.sid!r}, not requested novel {sid!r}')
    return record


def approval_head_bytes(
    base: str | os.PathLike[str], sid: str,
) -> bytes | None:
    """稳定解析审批盘并只序列化 current head；历史增长不进入 surface seal。

    JSON 形状、旧 v7 full/slim union 与 SID 归属都只在 approvals 边界解释；
    inspection 只消费这份 canonical bytes，不再拥有第二个裸 JSON 解析器。
    """
    paths.resolve_novel_dir(base, sid)
    p = _path(base, sid)
    try:
        before = p.stat()
    except FileNotFoundError:
        return None
    except OSError as exc:
        raise ArtifactUnreadable(f'审批当前 head 不可读：{p}: {exc!r}') from exc
    try:
        raw = p.read_bytes()
        after = p.stat()
    except OSError as exc:
        raise ArtifactUnreadable(f'审批当前 head 读取期间变化：{p}: {exc!r}') from exc
    coordinate = lambda stat: (
        stat.st_dev, stat.st_ino, stat.st_size, stat.st_mtime_ns)
    if coordinate(before) != coordinate(after) or len(raw) != after.st_size:
        raise ArtifactUnreadable(f'审批当前 head 读取期间发生变化：{p}')
    try:
        record = _ApprovalRecordV8.model_validate_json(raw)
    except ValidationError as exc:
        raise ArtifactUnreadable(
            f'审批当前 head 不是合法 v8 记录：{p}: {exc}') from exc
    if record.sid != sid:
        raise ArtifactUnreadable(
            f'审批当前 head 属于 {record.sid!r}，不是 {sid!r}')
    head = record.model_dump(
        mode='json',
        exclude={
            'design_history', 'structure_history',
            'writing_history', 'closure_history',
        },
    )
    return json.dumps(
        head, ensure_ascii=False, sort_keys=True, separators=(',', ':'),
        allow_nan=False,
    ).encode('utf-8')


def _read_current(base: str | os.PathLike[str], sid: str) -> _ApprovalRecordV8:
    return _read_state(base, sid)[1]


def _read_state(
    base: str | os.PathLike[str], sid: str,
) -> tuple[_ApprovalRecordV8, _ApprovalRecordV8]:
    """同时给出 durable raw fact 与只读 effective 投影。

    freshness / prerequisite 漂移只能让 ``effective`` 暂停，绝不能把派生出的
    ``False`` 再写回盘。所有 mutation 都必须用 effective 作前置校验、在 raw
    上作定点更新；这是审批可以在 exact subject 回退后自动复活的根本边界。
    """
    raw = _read_raw(base, sid)
    # CLI 首次批准可能尚无服务记录；typed 产物依然是当前事实，不能提前返回全 false。
    novel_dir = paths.resolve_novel_dir(base, sid)
    return raw, _effective_record(novel_dir, raw)


def _write(
    base: str | os.PathLike[str], record: _ApprovalRecordV8,
) -> ApprovalDoc:
    """审批 raw fact 的唯一落点；返回值始终是落盘后的 effective 投影。"""
    _require_approval_workflow(base)
    record = _ApprovalRecordV8.model_validate(record.model_dump())
    _refuse_if_frozen(record.sid)
    p = _path(base, record.sid)
    p.parent.mkdir(parents=True, exist_ok=True)
    tmp = p.with_suffix(f'.{os.getpid()}.{threading.get_ident()}.tmp')
    _assert_stage2_closure_code()
    tmp.write_text(record.model_dump_json(indent=2), encoding='utf-8')
    os.replace(tmp, p)
    novel_dir = paths.resolve_novel_dir(base, record.sid)
    return _view(base, _effective_record(novel_dir, record))


def get(
    base: str | os.PathLike[str], sid: str, *,
    overview: NovelOverview | None = None, code: str | None = None,
    include_spawns: bool = True,
    operator_projection: bool = False,
) -> ApprovalDoc:
    """公开审批投影；可安全复用同一次 operator 的只读坐标。

    两项必须成对提供，并且只在当前调用的 ``ContextVar`` 作用域内生效；
    普通 API/CLI 调用保持原来的现场读取，不形成跨请求缓存。
    """
    _validate_read_projection(sid, overview=overview, code=code)
    with _read_projection(overview=overview, code=code):
        with _LOCK:
            return _view(
                base, _read_current(base, sid),
                include_spawns=include_spawns,
                operator_projection=operator_projection)


def writing_is_current(base: str | os.PathLike[str], sid: str) -> bool:
    """是否有一份绑定当前 Stage1 正文、Stage4 计划与测量契约的写作授权。"""
    with _LOCK:
        try:
            if not _path(base, sid).is_file():
                paths.resolve_novel_dir(base, sid)
                return False
            record = _read_current(base, sid)
        except (NovelError, OSError, UnicodeDecodeError, ValidationError):
            return False
        return bool(record.writing and record.gate['s4'])


def has_record(base: str | os.PathLike[str], sid: str) -> bool:
    return writing_is_current(base, sid)


def require_stage2_review(
    base: str | os.PathLike[str], sid: str, node: str,
) -> None:
    """在服务/队列入口复用 Stage2 producer 的正式人审判据。

    这里只提供早反馈；CLI 真实执行边界仍会在构造下游请求前再验一次。同一个
    普通节点由 ``global_review.require_approved`` 同时读取当前 grounding/review，
    并复用 ``stage2.blueprint --check-only`` 的联合判定与 freshness；不在编排层
    新增一份机械 check 或自行解释 PASS/UNVER。Stage7 组装与下载也走同一当前契约；
    旧产物不迁移、不兼容消费，回滚须使用旧代码与旧归档根。
    """
    novel_dir = paths.resolve_novel_dir(base, sid)
    ctx = state.readonly_ctx(novel_dir, node, _projection_code())
    if node == 'stage7.assemble':
        from novel.stages.stage7.assemble import require_stage2_delivery_approved
        require_stage2_delivery_approved(ctx)
        return
    from novel.stages.stage2 import global_review
    if node == 'stage2.blueprint':
        global_review.require_global_accepted(ctx)
    else:
        global_review.require_approved(ctx)


def _stage2_review_identity(novel_dir: Path, node: str) -> tuple[str, str, str]:
    """token 守提交竞态，subject/contract 守长期接受；不读取审批 effective 投影。"""
    if node == 'stage2.global':
        token = _stage2_global_review_token(novel_dir)
        return token, token.removeprefix('s2gr2_'), 'stage2-global-acceptance-v2'
    if node != 'stage2.blueprint':
        raise ApprovalRequired(f'未登记 Stage2 审阅节点：{node}')
    from novel.views.stage2 import blueprint_review_workpack
    sid, _title, _platform = paths.derive_source_meta(novel_dir.name)
    pack = blueprint_review_workpack(novel_dir.parent, sid, code=_projection_code())
    return pack.review_token, pack.review_identity_sha256, pack.review_contract_version


def _stage2_review_view(
    novel_dir: Path, record: _ApprovalRecordV8, node: str,
) -> Stage2NodeReviewView:
    if node == 'stage2.blueprint':
        return _plot_review_view(novel_dir, record)
    history = [row for row in record.stage2_review_history if row.node == node]
    decision = record.stage2_reviews.get(node)
    if decision is None:
        return Stage2NodeReviewView(history=history)
    coverage = None
    try:
        _token, subject, contract = _stage2_review_identity(novel_dir, node)
        coverage = stage2_review_coverage(novel_dir.parent, record.sid, node)
        current = (subject == decision.review_subject_sha256
                   and contract == decision.review_contract_version
                   and decision.obligation_receipt is not None
                   and decision.obligation_receipt.scope == coverage.scope)
    except (ArtifactUnreadable, OSError, ValueError) as exc:
        return Stage2NodeReviewView(state='unknown', history=history, unavailable_reason=str(exc))
    except NovelError:
        current = False
    if not current:
        return Stage2NodeReviewView(state='expired', history=history, coverage=coverage)
    status = {'accept': 'accepted', 'stop': 'stopped'}.get(
        decision.decision, decision.decision)
    return Stage2NodeReviewView(
        state=status, current_decision=decision, history=history, coverage=coverage)


def _plot_snapshot(novel_dir: Path, record: _ApprovalRecordV8):
    from novel.views.plot_review import PlotReviewSnapshot
    return PlotReviewSnapshot.read(
        ArtifactStore(state.readonly_ctx(novel_dir, 'stage2.blueprint', _projection_code())),
        record.stage2_review_history)


def _plot_review_view(novel_dir: Path, record: _ApprovalRecordV8) -> Stage2NodeReviewView:
    history = [row for row in record.stage2_review_history if row.node == 'stage2.blueprint']
    if not ArtifactStore(state.readonly_ctx(
            novel_dir, 'stage2.blueprint', _projection_code())).exists(STAGE2_CHAPTER_BLUEPRINTS):
        return Stage2NodeReviewView(history=history)
    try:
        snapshot = _plot_snapshot(novel_dir, record)
        progress = snapshot.progress()
        scope = progress.next_scope or 'cross_chapter'
        current = snapshot.records.get(scope)
        status = next(row.state for row in progress.scopes if row.scope == scope)
        if _stage2_review_view(novel_dir, record, 'stage2.global').state != 'accepted':
            status = 'expired' if history else 'missing'
        return Stage2NodeReviewView(state=status, current_decision=current,
                                    history=history, plot_progress=progress)
    except (NovelError, OSError, ValueError) as exc:
        return Stage2NodeReviewView(state='unknown', history=history,
                                    unavailable_reason=str(exc))


def _approve_plot_review(base, sid: str, request: Stage2NodeReviewRequest) -> ApprovalDoc:
    """章级决定先耐久保存；全部章审和跨章明确接受才具备免费汇合资格。"""
    from novel.stages.stage2 import global_review, knowledge_links
    from novel.stages.stage2.plot_review import CONTRACT

    with approval_file_lock(base, sid):
        raw = _read_raw(base, sid)
        novel_dir = paths.resolve_novel_dir(base, sid)
        # 精确已购回答重放是幂等提交；不能因提交后问题历史变化要求再买一轮。
        expected = request.model_dump(mode='json')
        if any({key: value for key, value in row.model_dump(mode='json').items()
                if key in Stage2NodeReviewRequest.model_fields} == expected
               for row in raw.stage2_review_history):
            return _view(base, _effective_record(novel_dir, raw))
        snapshot = _plot_snapshot(novel_dir, raw)
        pack = snapshot.workpack(request.scope)
        if pack.review_token != request.review_token:
            raise HumanEditProtected('Blueprint 范围、材料或验证身份已变化；旧 token 不落盘')
        if not set(request.chapter_ordinals) <= set(snapshot.ordinals):
            raise HumanEditProtected('Blueprint 返工包含当前原文不存在的章节')
        if request.decision == 'accept':
            global_review.require_global_accepted(snapshot.store.ctx)
            global_review.require_approved(snapshot.store.ctx, require_acceptance=False)
        application, resulting = None, snapshot
        if request.knowledge_links is not None:
            application, resulting = snapshot.propose_knowledge(
                pack, request.knowledge_links, accepted=request.decision == 'accept')
            scope = resulting.knowledge_scope(request.scope)
            if request.decision == 'accept':
                knowledge_links.require_complete(scope, resulting.knowledge_history)
        plot_record = snapshot.apply(pack, request.plot_review, request.actor, resulting=resulting)
        snapshot.verify()
        if snapshot.store.ctx.code != _projection_code():
            raise HumanEditProtected('提交期间代码身份变化，须免费复验')
        decision = Stage2NodeReviewDecision(
            **request.model_dump(), plot_record=plot_record,
            knowledge_application=application,
            review_subject_sha256=plot_record.subject_sha256,
            review_contract_version=CONTRACT, decided_at=_now())
        return _write(base, raw.model_copy(update={
            'stage2_reviews': {**raw.stage2_reviews, request.node: decision},
            'stage2_review_history': [*raw.stage2_review_history, decision],
        }))


def require_stage2_node_accepted(
    base: str | os.PathLike[str], sid: str, node: str,
) -> None:
    """只从 raw typed 决定计算当前性，避免完整 gate 的递归依赖。"""
    novel_dir = paths.resolve_novel_dir(base, sid)
    view = _stage2_review_view(novel_dir, _read_raw(base, sid), node)
    if view.state != 'accepted':
        raise ApprovalRequired(f'{node} 尚无当前独立 Commander 接受：{view.state}')


def stage2_review_history(base: str | os.PathLike[str], sid: str) -> list[Stage2NodeReviewDecision]:
    """只读 durable 历史；不读取 effective gate，构包不会递归审批。"""
    return _read_raw(base, sid).stage2_review_history


def stage2_review_coverage(
    base: str | os.PathLike[str], sid: str, node: str,
) -> Stage2ReviewCoverage:
    from novel.stages.stage2 import global_review

    if node == 'stage2.global':
        ctx = state.readonly_ctx(paths.resolve_novel_dir(base, sid), node, _projection_code())
        return Stage2ReviewCoverage.model_validate(global_review.review_workpack(ctx)['review_coverage'])
    raise ArtifactUnreadable(f'{node} 缺当前完整审阅义务覆盖面')


def stage2_review_closure_available(base: str | os.PathLike[str], sid: str, *, node: str) -> bool:
    """只证明全部义务可继承；失败保持原等待，不把未知解释成可收口。"""
    try:
        if node == 'stage2.blueprint':
            return _plot_review_view(paths.resolve_novel_dir(base, sid),
                                     _read_raw(base, sid)).state == 'accepted'
        coverage = stage2_review_coverage(base, sid, node)
        if coverage.pending_obligation_ids or not coverage.inherited:
            return False
        return True
    except (NovelError, OSError, ValueError):
        return False


def close_stage2_inherited(base: str | os.PathLike[str], sid: str, *, node: str) -> ApprovalDoc:
    """在一次代码坐标内核验当前义务并免费重放原决定。"""
    with stage2_closure_projection(_projection_code()):
        return _close_stage2_inherited(base, sid, node=node)


def _close_stage2_inherited(base: str | os.PathLike[str], sid: str, *, node: str) -> ApprovalDoc:
    """只重放逐项原决定；锁内再验真实主体、来源链和完整覆盖。"""
    from contracts.schema.stage2_review import Stage2ReviewSubmission
    novel_dir = paths.resolve_novel_dir(base, sid)
    raw = _read_raw(base, sid)
    if _stage2_review_view(novel_dir, raw, node).state == 'accepted':
        return _view(base, _effective_record(novel_dir, raw))
    if node == 'stage2.blueprint':
        raise ApprovalRequired('章级接受不能推导跨章接受；仍须明确的当前范围决定')
    coverage = stage2_review_coverage(base, sid, node)
    if coverage.pending_obligation_ids or not coverage.inherited:
        raise ApprovalRequired('Stage2 仍有待审义务，免费收口不能产生新决定')
    actor = 'system:review-inheritance'
    reason = '逐项重放原审阅决定并核验完整覆盖；未运行模型、未产生新语义结论'
    return approve_stage2_review(base, sid, Stage2NodeReviewRequest(
        node=node, review_token=_stage2_review_identity(novel_dir, node)[0],
        decision='accept', actor=actor, reason=reason,
        review=Stage2ReviewSubmission(scope_sha256=coverage.scope.scope_sha256,
                                      default_decision='accept', exceptions=[])))


def _knowledge_link_history(record: _ApprovalRecordV8) -> list[KnowledgeLinkHistory]:
    from contracts.invariants import BLUEPRINT_REVIEW_CONTRACT

    return [KnowledgeLinkHistory(
        decision_sha256=_decision_sha256(row.model_dump(mode='json')),
        accepted=row.decision == 'accept', submission=row.knowledge_application or row.knowledge_links)
        for row in record.stage2_review_history
        if row.node == 'stage2.blueprint' and row.knowledge_links is not None
        and row.review_contract_version == BLUEPRINT_REVIEW_CONTRACT]


def knowledge_link_history(base: str | os.PathLike[str], sid: str) -> list[KnowledgeLinkHistory]:
    """只读 durable 决定，不读取 effective gate，供关联构包避免审批递归。"""
    return _knowledge_link_history(_read_raw(base, sid))


def current_knowledge_link_decisions(
    base: str | os.PathLike[str], sid: str,
) -> tuple[list[KnowledgeLinkHistory], str]:
    """原已接受决定的精确来源；免费物化不能发明新的 actor/reason。"""
    require_stage2_node_accepted(base, sid, 'stage2.blueprint')
    raw = _read_raw(base, sid)
    decision = next((row for row in reversed(raw.stage2_review_history)
                     if row.node == 'stage2.blueprint' and row.scope == 'cross_chapter'), None)
    if decision is None or decision.decision != 'accept' or decision.knowledge_links is None:
        raise ApprovalRequired('缺少已保存的 Blueprint 知识关联接受决定')
    return _knowledge_link_history(raw), _decision_sha256(decision.model_dump(mode='json'))


def approve_stage2_review(
    base: str | os.PathLike[str], sid: str, request: Stage2NodeReviewRequest,
) -> ApprovalDoc:
    """Stage2 两个节点共用的 typed 提交边界；接受绝不来自 seen 布尔值。"""
    from novel.stages.stage2 import global_review
    _require_approval_workflow(base)
    if request.node == 'stage2.blueprint':
        return _approve_plot_review(base, sid, request)
    with approval_file_lock(base, sid):
        raw = _read_raw(base, sid)
        if request.core_edits is not None:
            expected_request = request.model_dump(mode='json')
            if any({key: value for key, value in row.model_dump(mode='json').items()
                    if key in Stage2NodeReviewRequest.model_fields} == expected_request
                   for row in raw.stage2_review_history):
                # 原决定先于产物提交；部分提交后 token 已变，重放不能购买新 Commander。
                return _view(base, _effective_record(paths.resolve_novel_dir(base, sid), raw))

        novel_dir = paths.resolve_novel_dir(base, sid)
        ctx = state.readonly_ctx(novel_dir, request.node, _projection_code())
        if request.chapter_ordinals:
            from contracts.schema.stage1 import MainText
            main = ArtifactStore(ctx).read(MainText, STAGE1_MAIN_TEXT)
            known = {chapter.chapter_ordinal for chapter in main.root}
            if not set(request.chapter_ordinals) <= known:
                raise HumanEditProtected('Stage2 返工范围包含当前正文不存在的章')
        if request.decision == 'accept':
            global_review.require_global_valid(ctx)
        before = _stage2_review_identity(novel_dir, request.node)
        if request.review_token != before[0]:
            raise HumanEditProtected('Stage2 审阅包已经变化；旧 token 不落盘')
        if before != _stage2_review_identity(novel_dir, request.node):
            raise HumanEditProtected('Stage2 提交期间主体变化；拒绝混合快照')
        from novel.stages.stage2 import review_obligations
        material_pack = global_review.review_workpack(ctx)
        coverage = Stage2ReviewCoverage.model_validate(material_pack['review_coverage'])
        receipt = review_obligations.materialize(
            coverage, raw.stage2_review_history, decision=request.decision,
            actor=request.actor, reason=request.reason, submission=request.review)
        unavailable = {item['obligation_id'] for item in material_pack.get('removed_problem_materials', [])
                       if not item['available']}
        if any(unit.obligation.obligation_id in unavailable and unit.decision == 'accept'
               for unit in receipt.units):
            raise HumanEditProtected('被删问题的原主张/证据来源尚不可验证；恢复原归档并免费刷新后再审，当前保持不确定')
        if before != _stage2_review_identity(novel_dir, request.node):
            raise HumanEditProtected('Stage2 义务覆盖核验期间主体变化；拒绝混合回执')
        core_prepared_sha = None
        if request.core_edits is not None:
            from novel.stages.stage2 import core_edits
            core_prepared_sha = core_edits.prepare(base, sid, request, code=_projection_code())
            if before != _stage2_review_identity(novel_dir, request.node):
                raise HumanEditProtected('Core 整体预检期间主体变化；原决定未提交')
        prior = raw.stage2_reviews.get(request.node)
        if prior is not None and request.model_dump() == {
                key: value for key, value in prior.model_dump().items()
                if key in type(request).model_fields}:
            result = _view(base, _effective_record(novel_dir, raw))
        else:
            decision = Stage2NodeReviewDecision(
                **request.model_dump(), review_subject_sha256=before[1],
                obligation_receipt=receipt, core_edit_prepared_sha256=core_prepared_sha,
                material_source=material_pack['material_source'],
                review_contract_version=before[2], decided_at=_now())
            result = _write(base, raw.model_copy(update={
                'stage2_reviews': {**raw.stage2_reviews, request.node: decision},
                'stage2_review_history': [*raw.stage2_review_history, decision],
            }))
    # 接受先独立耐久；各审批服务重新核验当前身份并各取自己的 OS 文件锁。
    # Windows 文件锁不可重入，不能持本锁递归调用 approve_gate/mark_seen。
    # Blueprint 的关联决定在此已独立耐久。随后由 producer --close-review
    # 免费物化知识关联，再补审批；服务不能直接写 stage2/，也不能重新购买 C。
    return result


def pending_stage2_rework(
    base: str | os.PathLike[str], sid: str, node: str,
    *, review_token: str | None = None,
) -> Stage2NodeReviewDecision | None:
    if paths.find_novel_dir(base, sid) is None:
        return None
    raw = _read_raw(base, sid)
    if not raw.stage2_review_history:
        return None
    novel_dir = paths.resolve_novel_dir(base, sid)
    for decision in reversed(raw.stage2_review_history):
        if (decision.decision != 'rework' or decision.owner != node
                or decision.review_token in raw.stage2_rework_completed
                or review_token is not None and decision.review_token != review_token):
            continue
        if decision.node == 'stage2.blueprint':
            snapshot = _plot_snapshot(novel_dir, raw)
            if snapshot.records.get(decision.scope) != decision:
                continue
            progress = snapshot.progress()
            selected = next((row for row in progress.scopes if row.scope == decision.scope), None)
            if (selected is not None and selected.state == 'rework'
                    and _stage2_review_view(novel_dir, raw, 'stage2.global').state == 'accepted'):
                return decision
            continue
        if raw.stage2_reviews.get(decision.node) != decision:
            continue
        view = _stage2_review_view(novel_dir, raw, decision.node)
        if view.state == 'rework' and view.current_decision == decision:
            return decision
    return None


def stage2_rework_request(
    base: str | os.PathLike[str], sid: str, node: str, review_token: str,
) -> Stage2NodeReviewDecision:
    """执行者只能消费状态机已登记的当前返工，不能从自由理由推导动作。"""
    request = pending_stage2_rework(base, sid, node, review_token=review_token)
    if request is None or request.review_token != review_token:
        raise HumanEditProtected('Stage2 返工请求缺失、已消费或主体过期')
    return request


def mark_stage2_rework_landed(
    base: str | os.PathLike[str], sid: str, node: str, review_token: str,
) -> None:
    """producer 完成当前返工后的零模型回执；原请求与决定永久保留。"""
    with approval_file_lock(base, sid):
        raw = _read_raw(base, sid)
        matches = [row for row in raw.stage2_review_history
                   if row.review_token == review_token and row.owner == node
                   and row.decision == 'rework']
        if not matches:
            raise ApprovalRequired('Stage2 返工完成回执没有对应 typed 请求')
        if review_token not in raw.stage2_rework_completed:
            _write(base, raw.model_copy(update={
                'stage2_rework_completed': [*raw.stage2_rework_completed, review_token]}))


def stage2_patch_receipt(
    base: str | os.PathLike[str], sid: str, request_sha256: str,
) -> _Stage2PatchReceipt | None:
    return _read_raw(base, sid).stage2_patch_receipts.get(request_sha256)


def stage2_review_payload_landed(
    base: str | os.PathLike[str], sid: str, payload: dict[str, Any],
) -> bool:
    """已发布的精确 typed 决定就是提交事实；后续换主体不撤销历史提交。"""
    request = Stage2NodeReviewRequest.model_validate(payload)
    expected = request.model_dump(mode='json')
    return any({key: value for key, value in row.model_dump(mode='json').items()
                if key in Stage2NodeReviewRequest.model_fields} == expected
               for row in _read_raw(base, sid).stage2_review_history)


def stage2_review_halt(
    base: str | os.PathLike[str], sid: str,
) -> tuple[str, str, str] | None:
    """当前不确定/停止决定是持久人工出口，不能被下一次调度当作新审阅。"""
    if paths.find_novel_dir(base, sid) is None:
        return None
    raw = _read_raw(base, sid)
    if not raw.stage2_review_history:
        return None
    for node in ('stage2.global', 'stage2.blueprint'):
        view = _stage2_review_view(paths.resolve_novel_dir(base, sid), raw, node)
        if view.state in ('uncertain', 'stopped') and view.current_decision is not None:
            if view.current_decision.refinement_request is not None:
                # Stage4 只发起一次原文核查；它没有替章级 Commander 作停止决定。
                # 真正章审返回 uncertain/stop 后仍沿下面的持久人工出口停机。
                continue
            return node, view.state, view.current_decision.reason
    return None


def prepare_stage2_patch_receipt(
    base: str | os.PathLike[str], sid: str, *, request_sha256: str,
    before_token: str, result_subject_sha256: str, global_sha256: str,
    decision_timestamp: str | None = None,
) -> None:
    """首个正式写副作用前保存可重放补丁身份，不能拿事后内容推断曾审阅。"""
    _require_approval_workflow(base)
    receipt = _Stage2PatchReceipt(
        request_sha256=request_sha256, before_token=before_token,
        result_subject_sha256=result_subject_sha256, global_sha256=global_sha256,
        decision_timestamp=decision_timestamp)
    with approval_file_lock(base, sid):
        raw = _read_raw(base, sid)
        existing = raw.stage2_patch_receipts.get(request_sha256)
        if existing is not None:
            if existing != receipt:
                raise HumanEditProtected('同一 Stage2 补丁请求出现不一致的恢复身份')
            return
        _write(base, raw.model_copy(update={'stage2_patch_receipts': {
            **raw.stage2_patch_receipts, request_sha256: receipt}}))


_GATE_ARTIFACTS = {
    's0': (STAGE0_REVISIONS,),
    's1': (STAGE1_MAIN_TEXT, STAGE1_HOOK_COPY),
    's2': (STAGE2_GLOBAL_GROUNDING, STAGE2_CHAPTER_BLUEPRINTS, STAGE2_GLOBAL_REVIEW,
           STAGE2_KNOWLEDGE_LINKS),
    's4': (STAGE4_PLAN_CHAPTERS,),
    's6': (STAGE6_HOOK,),
    's7': (STAGE7_NAME_MAP,),
}

_GATE_ORDER = APPROVAL_GATES
_PRE_WRITING_GATES = ('s0', 's1', 's2', 's4')
_POST_WRITING_GATES = ('s5', 's6', 's7')

_GATE_REQUIREMENTS = {
    's1': ('s0',), 's2': ('s1',), 's4': ('s2',),
    's5': ('s4',), 's6': ('s5',), 's7': ('s6',),
}

_SEEN_ARTIFACTS = {
    'read': (STAGE2_CHAPTER_BLUEPRINTS,),
    # ``seen.global`` 绑定 Global 主体**与 review 完整字节**：review 任何一格
    # 状态/理由变化都让旧阅读事实过期；节点接受仍由独立的 typed 决定绑定。
    'global': (STAGE2_GLOBAL_GROUNDING, STAGE2_GLOBAL_REVIEW),
}


def _decision_sha256(value: Any) -> str:
    raw = json.dumps(
        value, ensure_ascii=False, sort_keys=True, separators=(',', ':'),
    ).encode('utf-8')
    return hashlib.sha256(raw).hexdigest()


class _DesignSubjects(StrictModel):
    """当前 Stage4A 候选一次读取所得的完整人审 subject 集。"""

    candidate_sha256: str = Field(pattern=r'^[0-9a-f]{64}$')
    aggregate_subject_sha256: str = Field(pattern=r'^[0-9a-f]{64}$')
    binding_infeasibility: DesignBindingInfeasibility | None = None
    #: TODO-45 批次 2：A→formal 零调用预检投影。None = 预检自身无法产出
    #: （上游缺失等）；审批 accept 闸对 None fail-closed。
    preflight: DesignPreflightSummary | None = None
    repair_capability: DesignReviewRepairCapability
    strategy: DesignStrategySubject
    strategy_subject_sha256: str = Field(pattern=r'^[0-9a-f]{64}$')
    strategy_epoch_sha256: str = Field(pattern=r'^[0-9a-f]{64}$')
    chapters: list[DesignChapterSubject] = Field(min_length=1)
    chapter_subject_sha256: dict[int, str]


@dataclass(frozen=True)
class _DesignEditAuthorization:
    """opaque edit token 现场展开后的内部身份；不进入公共请求。"""

    review_token: str
    scope: DesignRepairChapterScope
    candidate_sha256: str
    decision_sha256: str
    chapter_subject_sha256: str
    strategy_epoch_sha256: str
    chapter_strategy_sha256: str


class _StructureWorkpackSnapshot(StrictModel):
    """B 工作包一次乐观只读快照；前后完全一致才允许返回。"""

    subject_sha256: str = Field(pattern=r'^[0-9a-f]{64}$')
    chapter_ordinals: list[int]
    chapter_subject_sha256: dict[int, str]
    plan: PlanChapters
    concerns: list[BindingConcern]
    binding_input_contract: str | None = None
    #: v2 逐章 reading 的 claim/舞台/材料来源；与 plan/binding 同一次快照读出。
    blueprints: ChapterBlueprints
    chapter_materials: dict[int, PlotChapterReviewMaterials]
    plan_payload_sha256: str = Field(pattern=r'^[0-9a-f]{64}$')
    binding_payload_sha256: str = Field(pattern=r'^[0-9a-f]{64}$')
    blueprint_payload_sha256: str = Field(pattern=r'^[0-9a-f]{64}$')


def _formal_design_chapter(chapter: Any, subject_aliases: dict) -> dict[str, Any]:
    """仅在服务内还原引用给结构量尺；不产生另一份可编辑节点到场景映射。"""
    def subject(token):
        value = subject_aliases.get(token)
        if value is None:
            raise ArtifactUnreadable('设计引用了当前目录之外的主体')
        return value.model_dump(mode='json') if hasattr(value, 'model_dump') else value
    def beat(token):
        value = subject(token)
        if value['kind'] != 'beat':
            raise ArtifactUnreadable('呈现父主体必须是剧情节点')
        return value['ref']
    def goal(value):
        return {'intent': value.intent,
                'subject_refs': [subject(t) for t in value.subject_aliases],
                'anchor_beat_ref': beat(value.anchor_beat_alias),
                'anchor_scene_ref': value.anchor_scene_ref}
    return {
        'output_ordinal': chapter.output_ordinal,
        'structure_decision': chapter.structure_decision.model_dump(mode='json'),
        'chapter_goal': goal(chapter.chapter_goal),
        'open_hook_goal': goal(chapter.open_hook_goal),
        'end_hook_goal': goal(chapter.end_hook_goal),
        'scenes': [{'slot': s.scene_ref, 'context_beat_refs': [
                       beat(t) for t in s.context_beat_aliases],
                    'turn_beat_ref': beat(s.turn_beat_alias)}
                   for s in chapter.scenes],
        'beat_placements': [{'beat_ref': beat(p.beat_alias),
                             'deliveries': [d.model_dump(mode='json') for d in p.deliveries]}
                            for p in chapter.beat_placements],
    }


def _safe_structure_evidence(
    blueprint: dict[str, Any], chapter: Any, subject_aliases: dict,
) -> DesignStructureEvidence:
    """复用正式量尺，仅发布无 formal id 的标量与关闭式变化集合。"""
    from novel.stages.stage4.measure import (
        computed_structure_change_kinds, structure_metrics_of,
    )

    metrics = structure_metrics_of(
        blueprint, _formal_design_chapter(chapter, subject_aliases))
    declared = list(chapter.structure_decision.declared_change_kinds)
    computed = computed_structure_change_kinds(metrics)
    declared_set, computed_set = set(declared), set(computed)
    order = {name: index for index, name in enumerate(STRUCTURE_CHANGE_KINDS)}

    def ordered(values: set[str]) -> list[str]:
        return sorted(values, key=order.__getitem__)

    def scalar(section: str, key: str) -> str | int | float | bool | None:
        value = (metrics.get(section) or {}).get(key)
        if value is None or isinstance(value, (str, int, float, bool)):
            return value
        raise ArtifactUnreadable(
            f'Stage4 structure metric {section}.{key} 不是安全标量')

    readings = {
        'mapping_state': scalar('mapping', 'state'),
        'beats': scalar('mapping', 'beats'),
        'mapped_beats': scalar('mapping', 'mapped_beats'),
        'deliveries': scalar('mapping', 'deliveries'),
        'distributed_beats': scalar('mapping', 'distributed_beats'),
        'plan_scenes': scalar('mapping', 'plan_scenes'),
        'partition_state': scalar('partition', 'state'),
        'all_beat_pairs': scalar('partition', 'all_beat_pairs'),
        'changed_beat_pairs': scalar('partition', 'changed_pairs'),
        'partition_distance': scalar('partition', 'distance'),
        'order_state': scalar('order', 'state'),
        'source_comparable_pairs': scalar('order', 'source_comparable_pairs'),
        'inverted_pairs': scalar('order', 'inverted_pairs'),
        'cross_scene_inversion_rate': scalar(
            'order', 'cross_scene_inversion_rate'),
        'boundary_state': scalar('boundary', 'state'),
        'source_groups': scalar('boundary', 'source_groups'),
        'plan_groups': scalar('boundary', 'plan_groups'),
        'group_count_changed': scalar('boundary', 'group_count_changed'),
        'noncontiguous_state': scalar('noncontiguous', 'state'),
        'testable_scenes': scalar('noncontiguous', 'testable_scenes'),
        'noncontiguous_scenes': scalar(
            'noncontiguous', 'noncontiguous_scenes'),
        'noncontiguous_rate': scalar('noncontiguous', 'noncontiguous_rate'),
        'opening_anchor_state': (
            ((metrics.get('anchors') or {}).get('opening') or {})
            .get('scene_payload') or {}).get('state'),
        'opening_excludes_source_edge': (
            ((metrics.get('anchors') or {}).get('opening') or {})
            .get('scene_payload') or {}).get('excludes_source_edge'),
        'ending_anchor_state': (
            ((metrics.get('anchors') or {}).get('ending') or {})
            .get('scene_payload') or {}).get('state'),
        'ending_excludes_source_edge': (
            ((metrics.get('anchors') or {}).get('ending') or {})
            .get('scene_payload') or {}).get('excludes_source_edge'),
        'eligibility_state': scalar('eligibility', 'state'),
        'cross_group_comparable_pairs': scalar(
            'eligibility', 'cross_group_comparable_pairs'),
        'causally_constrained_pairs': scalar(
            'eligibility', 'causally_constrained_pairs'),
        'causally_incomparable_pairs': scalar(
            'eligibility', 'causally_incomparable_pairs'),
        'reorderable': scalar('eligibility', 'reorderable'),
        'noncontiguous_opportunity_pairs': scalar(
            'eligibility', 'noncontiguous_opportunity_pairs'),
    }
    if any(
        value is not None and not isinstance(value, (str, int, float, bool))
        for value in readings.values()
    ):
        raise ArtifactUnreadable('Stage4 structure readings 泄漏了非标量内部对象')
    return DesignStructureEvidence(
        metric_contract=STRUCTURE_METRIC_CONTRACT,
        state=str(metrics.get('state') or 'unver'),
        declared_change_kinds=declared,
        computed_change_kinds=computed,
        realized_declared=ordered(declared_set & computed_set),
        unrealized_declared=ordered(declared_set - computed_set),
        undeclared_actual=ordered(computed_set - declared_set),
        readings=readings,
    )


def _current_design_subjects(novel_dir: Path) -> _DesignSubjects:
    """当前 A 候选的唯一人审投影；机械 freshness 与业务 subject 同时关闭式校验。"""
    store = _store_for(novel_dir)
    candidate_sha256 = _require_fresh_json(store, STAGE4_BLIND_CANDIDATE)
    candidate, _raw = store.read_own(BlindCandidate, STAGE4_BLIND_CANDIDATE)
    binding_infeasibility = _current_design_binding_infeasibility(store)
    ordinals = [row.output_ordinal for row in candidate.output_chapters]
    if ordinals != sorted(set(ordinals)):
        raise ArtifactUnreadable('Stage4A 候选章号必须升序、去重')

    from novel.stages.stage4.plot_design import (
        BlindPlanError, build_blind_projection, candidate_subject_sha256,
    )
    from novel.stages.stage4.design_evolution import (
        chapter_strategy_sha256, strategy_epoch_sha256,
    )
    from novel.source_identity import artifact_body_root
    root_clean_sha1 = artifact_body_root(store, STAGE2_CHAPTER_BLUEPRINTS)
    _require_fresh_json(store, STAGE2_CHAPTER_BLUEPRINTS)
    blueprints, _blueprints_raw = store.read_own(
        ChapterBlueprints, STAGE2_CHAPTER_BLUEPRINTS)
    blueprint_payload = blueprints.model_dump(mode='json', by_alias=True)
    try:
        from novel.stages.stage4.plan import _DesignUpstream
        design_ctx = state.readonly_ctx(novel_dir, 'stage4.design', _projection_code())
        upstream = _DesignUpstream(design_ctx, ArtifactStore(design_ctx))
        blind_truth, current_mapping = build_blind_projection(
            blueprint_payload, root_clean_sha1=root_clean_sha1,
            knowledge_links=upstream.knowledge_links, global_g=upstream.global_g,
            characters=upstream.characters, main_text=upstream.main_text)
    except BlindPlanError as exc:
        raise ArtifactUnreadable(
            f'当前 Stage2 不能重建 Stage4A blind truth：{exc}') from exc
    blueprint_by_ordinal = {
        row.chapter_ordinal: row.model_dump(mode='json')
        for row in blueprints.blueprints
    }
    review_contract = DESIGN_REVIEW_CONTRACT
    repair_capability = _current_design_repair_capability(
        store, candidate=candidate,
        candidate_file_sha256=candidate_sha256, ordinals=ordinals)
    preflight = _design_preflight_summary(novel_dir)
    candidate_mapping = {
        'alias_algorithm': candidate.alias_algorithm,
        'truth_sha256': candidate.truth_sha256,
        'subject_aliases': {k: v.model_dump(mode='json') for k, v in candidate.subject_aliases.items()},
        'endpoint_aliases': {k: v.model_dump(mode='json') for k, v in candidate.endpoint_aliases.items()},
        'participant_aliases': {key: value.model_dump(mode='json')
                                for key, value in candidate.participant_aliases.items()},
    }
    if current_mapping != candidate_mapping:
        raise ApprovalRequired(
            'Stage4A 候选的 truth/alias mapping 不再等于当前 Stage2+root 投影')
    if (
        candidate.provenance.alias_algorithm != candidate.alias_algorithm
        or candidate.provenance.truth_sha256 != candidate.truth_sha256
    ):
        raise ArtifactUnreadable(
            'Stage4A 候选根部与 provenance 的 blind mapping 身份不一致')
    truth_by_ordinal = {
        int(row['chapter_ordinal']): DesignBlindChapterTruth.model_validate(row)
        for row in blind_truth['chapters']
    }
    if set(truth_by_ordinal) != set(ordinals) or set(blueprint_by_ordinal) != set(ordinals):
        raise ArtifactUnreadable(
            'Stage4A candidate、blind truth 与 Stage2 blueprint 章集不闭合')

    basis = DesignReviewBasis(
        review_contract=review_contract,
        root_clean_sha1=root_clean_sha1,
        truth_sha256=candidate.truth_sha256,
        alias_algorithm=candidate.alias_algorithm,
    )
    strategy = DesignStrategySubject(
        basis=basis,
        reconstruction_strategy=candidate.reconstruction_strategy,
        chapter_weights=[{
            'chapter_ordinal': row.output_ordinal,
            'weight': row.weight,
        } for row in candidate.output_chapters],
    )
    from novel.stages.stage4.plot_design import build_blind_strategy_projection
    strategy_sha256 = _decision_sha256({
        'kind': 'stage4_design_strategy_dependencies_v2',
        'basis': basis.model_dump(mode='json', exclude={'truth_sha256'}),
        'source_projection': build_blind_strategy_projection(blind_truth),
        'reconstruction_strategy': strategy.reconstruction_strategy,
        'chapter_weights': [row.model_dump(mode='json') for row in strategy.chapter_weights],
    })
    strategy_epoch = strategy_epoch_sha256(
        basis.model_dump(mode='json'), candidate.reconstruction_strategy)

    chapter_subjects: list[DesignChapterSubject] = []
    chapter_hashes: dict[int, str] = {}
    for chapter in candidate.output_chapters:
        subject = DesignChapterSubject(
            strategy_epoch_sha256=strategy_epoch,
            chapter_strategy_sha256=chapter_strategy_sha256(
                strategy_epoch, chapter.output_ordinal, chapter.weight),
            blind_truth=truth_by_ordinal[chapter.output_ordinal],
            chapter=chapter,
            structure_evidence=_safe_structure_evidence(
                blueprint_by_ordinal[chapter.output_ordinal],
                chapter, candidate.subject_aliases),
        )
        subject_sha256 = _decision_sha256({
            'kind': 'stage4_design_chapter_subject',
            'subject': subject.model_dump(mode='json'),
        })
        chapter_subjects.append(subject)
        chapter_hashes[chapter.output_ordinal] = subject_sha256

    business = candidate.model_dump(
        mode='json', by_alias=True, exclude={'provenance'})
    # 预检进聚合主体（code_version 是信息性读数，不参与 hash）：
    # 候选/上游/判据结论变化 ⇒ 旧 accept 自然过期，重审 0 成本。
    preflight_subject = (
        None if preflight is None else {
            key: value for key, value in preflight.model_dump(mode='json').items()
            if key != 'code_version'
        }
    )
    aggregate_sha256 = _decision_sha256({
        'kind': 'stage4_design_review_aggregate',
        'contract': review_contract,
        'preflight': preflight_subject,
        'candidate_subject_sha256': candidate_subject_sha256(business),
        'binding_infeasibility': (
            binding_infeasibility.model_dump(mode='json')
            if binding_infeasibility is not None else None
        ),
        'repair_capability': repair_capability.model_dump(mode='json'),
        'strategy_subject_sha256': strategy_sha256,
        'chapter_subject_sha256': [
            [ordinal, chapter_hashes[ordinal]] for ordinal in ordinals
        ],
    })
    return _DesignSubjects(
        candidate_sha256=candidate_sha256,
        aggregate_subject_sha256=aggregate_sha256,
        binding_infeasibility=binding_infeasibility,
        preflight=preflight,
        repair_capability=repair_capability,
        strategy=strategy,
        strategy_subject_sha256=strategy_sha256,
        strategy_epoch_sha256=strategy_epoch,
        chapters=chapter_subjects,
        chapter_subject_sha256=chapter_hashes,
    )


def _design_preflight_summary(
    novel_dir: Path,
) -> DesignPreflightSummary | None:
    """批次 2：A→formal 零调用预检的唯一投影缝。

    复用 `plan.preflight_binding_candidate`（构造性绑定 + 同一联合 HARD），
    不重拼判据。预检自身无法产出（上游缺失等）时返回 None——accept 闸对
    None fail-closed，审阅面仍可渲染以走修订路。
    """
    from novel.stages.stage4.plan import preflight_binding_candidate

    try:
        ctx = state.readonly_ctx(
            novel_dir, 'stage4.plan', _projection_code())
        report = preflight_binding_candidate(ctx)
    except NovelError:
        return None
    return DesignPreflightSummary.model_validate(report.summary())


def _current_design_binding_infeasibility(
    store: ArtifactStore,
) -> DesignBindingInfeasibility | None:
    """把唯一 phase-state capability 投影成 A 审阅可读的关闭式证据面。"""
    binding = current_infeasible_binding(store)
    if binding is None:
        return None
    if not binding.infeasibility_proof:
        # 现役 EvidenceBinding schema 已守住这一点；这里仍关闭式防未来模型漂移。
        raise ArtifactUnreadable('fresh infeasible EvidenceBinding 缺少代码 proof')
    return DesignBindingInfeasibility(
        candidate_sha256=binding.candidate_sha256,
        binding_concerns=binding.binding_concerns,
        infeasibility_proof=binding.infeasibility_proof,
    )


def _current_design_repair_capability(
    store: ArtifactStore, *, candidate: BlindCandidate,
    candidate_file_sha256: str, ordinals: list[int],
    agent_disabled_reason_code: str | None = None,
) -> DesignReviewRepairCapability:
    """复用 agent journal 唯一判官，投影当前候选逐章可执行修订面。"""
    from novel.llm import stage4_runs
    capability = stage4_runs.inspect_design_repair_capability(
        store.ctx,
        candidate_payload=candidate.model_dump(by_alias=True, mode='json'),
        candidate_file_sha256=candidate_file_sha256,
    )
    rounds = dict(capability.chapter_repair_rounds)
    if any(ordinal not in ordinals or count < 0 for ordinal, count in rounds.items()):
        raise ArtifactUnreadable(
            'Stage4 design repair capability 返回了候选章集之外的轮次')
    # v3 把「再发一次 agent wire」与「零 wire 完整章替换」拆开。后者只要
    # 当前候选能形成 typed review subject 就始终可用；前者依赖 split lineage，
    # 但不再把历史 committed run 数误当成永久封禁。每一个新的 typed decision
    # 都会打开一个同 strategy epoch 的 repair epoch，而单个 epoch 的 content
    # turns 仍由 Stage4DesignAgent 硬封顶为 2。
    agent_repairable = (
        capability.repairable if agent_disabled_reason_code is None else False)
    return DesignReviewRepairCapability(
        agent_repairable=agent_repairable,
        agent_reason_code=(
            agent_disabled_reason_code
            if agent_disabled_reason_code is not None else
            'same_strategy_epoch_ready' if capability.repairable
            else capability.reason_code),
        full_chapter_editable=True,
        full_chapter_edit_reason_code='current_typed_decision',
        chapters=[{
            'output_ordinal': ordinal,
            'committed_rounds': rounds.get(ordinal, 0),
            'agent_repair_available': agent_repairable,
            'full_chapter_edit_available': True,
        } for ordinal in ordinals],
    )


def _root_clean_sha1(novel_dir: Path) -> str:
    path = novel_dir / STAGE0_CLEAN_TEXT
    try:
        raw = path.read_bytes()
    except FileNotFoundError:
        raise UpstreamMissing(
            f'{STAGE0_CLEAN_TEXT} is required before an approval can be current') from None
    except OSError as exc:
        raise ArtifactUnreadable(f'cannot read approval root {STAGE0_CLEAN_TEXT}: {exc!r}') from exc
    return prov.content_sha1(raw)


def _store_for(novel_dir: Path) -> ArtifactStore:
    ctx = state.readonly_ctx(novel_dir, 'stage0.approve', _projection_code())
    return ArtifactStore(ctx)


def _stage5_contract_status(novel_dir: Path):
    """审批面复用 Stage5 唯一状态判官；不另拼旧目录/旧 schema 判据。"""
    from novel.stages.stage5.review import review_status

    store = _store_for(novel_dir)
    return store, review_status(store.ctx, store)


def _require_stage5_contract_compatible(novel_dir: Path, *, action: str):
    """旧 Stage5 主体在任何审批/授权 bool 之前终止，其他状态原样交调用方。"""
    store, current = _stage5_contract_status(novel_dir)
    if current.state == 'legacy_incompatible':
        details = '；'.join(current.reasons[:8])
        raise HumanEditProtected(
            f'legacy_incompatible: 旧 Stage5 契约只读保留，不能{action}：{details}')
    return store, current


def _require_fresh_json(store: ArtifactStore, rel: str) -> str:
    path = store.path(rel)
    invalid = store.path(paths.invalid_of(rel))
    # ⚠ **`GateBlocked`, not `ApprovalRequired`**: an unadjudicated INVALID sibling
    # is not "nobody has approved this yet" — approval cannot bind to it at all,
    # and the way out is the gate's own zero-cost promotion/edit path.  Telling a
    # person to approve something they are structurally unable to approve sends
    # them down the wrong recovery path, and the blocked draft's location — the
    # whole point of `invalid_path` — never reaches the report.
    if invalid.is_file():
        raise GateBlocked(
            f'{rel} has an unadjudicated INVALID sibling; an approval cannot bind to it',
            invalid_path=str(invalid),
            hint=f'<producing node> --revalidate --actor <id> --reason <why>, or '
                 f'--apply-edit to fix {rel} first (both cost nothing)')
    if not path.is_file():
        raise UpstreamMissing(f'{rel} is required before approval')
    spec = artifact_spec(rel)
    if spec is None:
        raise ArtifactUnreadable(f'{rel} has no registered artifact contract')
    store.read_own(spec.model, rel)

    # GlobalReview is a human/evaluator decision surface rather than a normal
    # provenance-bearing producer.  Its currentness is its exact binding to the
    # current typed GlobalGrounding subject; all other JSON subjects must have
    # a definitive fresh provenance result.
    if rel == STAGE2_GLOBAL_REVIEW:
        review, _ = store.read_own(GlobalReview, STAGE2_GLOBAL_REVIEW)
        _grounding, raw = grounding_io.read_own(store, STAGE2_GLOBAL_GROUNDING)
        registry = grounding_io.read_registry(store, own=True)
        require_current_global_review(raw, review,
            characters=registry.model_dump(mode='json', by_alias=True))
    else:
        report = store.freshness(rel)
        if report.state != 'fresh':
            raise ApprovalRequired(
                f'{rel} is not definitively current: '
                f'{report.state}/{report.reason}')
    try:
        return hashlib.sha256(path.read_bytes()).hexdigest()
    except OSError as exc:
        raise ArtifactUnreadable(f'cannot hash approval subject {rel}: {exc!r}') from exc


def _require_current_text(novel_dir: Path, rel: str) -> str:
    path = novel_dir / rel
    invalid = novel_dir / paths.invalid_of(rel)
    # Same classification as `_require_fresh_json`: a blocked draft is a gate
    # state, not a missing signature.  See that function for the reasoning.
    if invalid.is_file():
        raise GateBlocked(
            f'{rel} has an unadjudicated INVALID sibling; an approval cannot bind to it',
            invalid_path=str(invalid),
            hint=f'<producing node> --revalidate --actor <id> --reason <why>, or '
                 f'--apply-edit to fix {rel} first (both cost nothing)')
    try:
        raw = path.read_bytes()
    except FileNotFoundError:
        raise UpstreamMissing(f'{rel} is required before approval') from None
    except OSError as exc:
        raise ArtifactUnreadable(f'cannot read approval subject {rel}: {exc!r}') from exc
    try:
        raw.decode('utf-8')
    except UnicodeDecodeError as exc:
        raise ArtifactUnreadable(f'{rel} is not UTF-8: {exc!r}') from exc
    return hashlib.sha256(raw).hexdigest()


def _proof_source_root(store: ArtifactStore, rels: tuple[str, ...] | list[str]) -> str:
    """正文证明保留原主体的来源根；钩子、未知输入仍绑定当前完整 clean 根。

    每份主体仍先通过原 freshness 和字节核验。仅来源可证等价时保持已签内容的
    原根，不写新票、不修改原产物；任一真实材料变化仍由 artifacts 摘要使票过期。
    """
    from novel.consumption import producer_node
    from novel.source_identity import BODY_OWNERS, artifact_body_root
    body_rels: list[str] = []
    for rel in rels:
        if rel in (STAGE1_MAIN_TEXT, STAGE2_GLOBAL_REVIEW):
            continue  # 无 producer provenance；其当前性和字节仍属于 proof。
        if producer_node(rel) not in BODY_OWNERS:
            return store.ctx.root_clean_sha1
        body_rels.append(rel)
    if not body_rels:
        return store.ctx.root_clean_sha1
    roots = [artifact_body_root(store, rel) for rel in body_rels]
    return roots[0]


def _make_proof(
    novel_dir: Path,
    rels: tuple[str, ...] | list[str],
    decision: Any,
) -> _ApprovalProof:
    store = _store_for(novel_dir)
    subjects: dict[str, str] = {}
    for rel in rels:
        if rel in subjects:
            continue
        subjects[rel] = (
            _require_current_text(novel_dir, rel)
            if artifact_spec(rel) is None
            else _require_fresh_json(store, rel)
        )
    return _ApprovalProof(
        root_clean_sha1=_proof_source_root(store, rels),
        artifacts=subjects,
        decision_sha256=_decision_sha256(decision),
    )


def _design_decision_payload(
    decision: DesignReviewMaterialized | DesignReviewDecision,
) -> dict[str, Any]:
    """Stage4A 人审的唯一摘要面；服务端决定时间与摘要自身不参与递归。"""
    return {
        'kind': 'stage4_design_review',
        **decision.model_dump(
            mode='json', exclude={'decision_sha256', 'decided_at'}),
    }


def _design_review_token(aggregate_subject_sha256: str) -> str:
    return f's4dr1_{aggregate_subject_sha256}'


def _design_action_review_token(decision_sha256: str) -> str:
    return f's4rr1_{decision_sha256}'


def _materialize_design_request(
    request: DesignReviewRequest, current: _DesignSubjects,
) -> DesignReviewMaterialized:
    """在服务端 current immutable subject 上展开紧凑外部裁决。"""
    if request.review_token != _design_review_token(
            current.aggregate_subject_sha256):
        raise HumanEditProtected(
            'Stage4A review_token 已过期；请刷新 immutable workpack 后重审')
    ordinals = [row.chapter.output_ordinal for row in current.chapters]
    submitted = [row.output_ordinal for row in request.chapters]
    if submitted != ordinals:
        raise HumanEditProtected(
            f'Stage4A typed 章集必须精确等于当前主体：提交 {submitted}，当前 {ordinals}')
    chapter_by = {
        row.chapter.output_ordinal: row for row in current.chapters
    }
    strategy_constraints = [DesignStrategyReplanConstraint(
        kind=row.kind,
        subject_sha256=current.strategy_subject_sha256,
        chapter_ordinals=row.chapter_ordinals,
    ) for row in request.strategy.replan_constraints]
    return DesignReviewMaterialized(
        actor=request.actor,
        reviewer_kind=request.reviewer_kind,
        review_run_id=request.review_run_id,
        general_reason=request.general_reason,
        candidate_sha256=current.candidate_sha256,
        aggregate_subject_sha256=current.aggregate_subject_sha256,
        binding_infeasibility=current.binding_infeasibility,
        preflight=current.preflight,
        repair_capability=current.repair_capability,
        strategy=DesignStrategyReview(
            subject=current.strategy,
            subject_sha256=current.strategy_subject_sha256,
            placeholder=False,
            template_token=None,
            decision=request.strategy.decision,
            reason=request.strategy.reason,
            replan_constraints=strategy_constraints,
            source_decision_sha256=request.strategy.source_decision_sha256,
        ),
        chapters=[DesignChapterReview(
            output_ordinal=row.output_ordinal,
            subject=chapter_by[row.output_ordinal],
            subject_sha256=current.chapter_subject_sha256[row.output_ordinal],
            placeholder=False,
            template_token=None,
            decision=row.decision,
            reason=row.reason,
            constraints=row.constraints,
            constraint_operations=row.constraint_operations,
            source_decision_sha256=row.source_decision_sha256,
        ) for row in request.chapters],
    )


def _slim_design_payload(
    request: DesignReviewRequest, current: _DesignSubjects,
) -> dict[str, Any]:
    """新历史的唯一 digest：typed facts + 内部 subject 摘要，无大载荷。"""
    return {
        'kind': 'stage4_design_review_slim_v1',
        'request': request.model_dump(mode='json'),
        'candidate_sha256': current.candidate_sha256,
        'aggregate_subject_sha256': current.aggregate_subject_sha256,
        'strategy_subject_sha256': current.strategy_subject_sha256,
        'strategy_epoch_sha256': current.strategy_epoch_sha256,
        'chapter_subject_sha256': current.chapter_subject_sha256,
        'repair_capability': current.repair_capability.model_dump(mode='json'),
        'binding_infeasibility': (
            None if current.binding_infeasibility is None
            else current.binding_infeasibility.model_dump(mode='json')),
    }


def _public_design_decision(
    decision: _StoredDesignHistory, current: _DesignSubjects | None = None,
) -> DesignReviewDecision:
    if isinstance(decision, _StoredDesignDecisionSlim):
        if current is None or (
            decision.candidate_sha256 != current.candidate_sha256
            or decision.aggregate_subject_sha256 != current.aggregate_subject_sha256
            or decision.strategy_subject_sha256 != current.strategy_subject_sha256
            or decision.strategy_epoch_sha256 != current.strategy_epoch_sha256
            or decision.chapter_subject_sha256 != current.chapter_subject_sha256
            or decision.repair_capability != current.repair_capability
            or decision.binding_infeasibility != current.binding_infeasibility
        ):
            raise HumanEditProtected(
                'slim Stage4A 决定不能在不同 immutable subject 上展开')
        materialized = _materialize_design_request(decision.request, current)
        return DesignReviewDecision(
            **materialized.model_dump(mode='json'),
            decision_sha256=decision.decision_sha256,
            decided_at=decision.decided_at,
        )
    return DesignReviewDecision.model_validate(
        decision.model_dump(mode='json', exclude={'proof'}))


def _design_history_entry(
    decision: _StoredDesignHistory,
) -> DesignReviewHistoryEntry:
    if isinstance(decision, _StoredDesignDecisionSlim):
        request = decision.request
        return DesignReviewHistoryEntry(
            actor=request.actor,
            reviewer_kind=request.reviewer_kind,
            review_run_id=request.review_run_id,
            general_reason=request.general_reason,
            review_token=request.review_token,
            strategy_decision=request.strategy.decision,
            chapter_decisions={
                row.output_ordinal: row.decision for row in request.chapters},
            decision_sha256=decision.decision_sha256,
            decided_at=decision.decided_at,
        )
    return DesignReviewHistoryEntry(
        actor=decision.actor,
        reviewer_kind=decision.reviewer_kind,
        review_run_id=decision.review_run_id,
        general_reason=decision.general_reason,
        review_token=_design_review_token(decision.aggregate_subject_sha256),
        strategy_decision=decision.strategy.decision,
        chapter_decisions={
            row.output_ordinal: row.decision for row in decision.chapters},
        decision_sha256=decision.decision_sha256,
        decided_at=decision.decided_at,
    )


def _proof_for_design(
    novel_dir: Path,
    decision: DesignReviewMaterialized | DesignReviewDecision,
) -> _ApprovalProof:
    """绑定根、候选、可选 fresh B 负结论及完整 typed 盲审决定。"""
    artifacts = [STAGE4_BLIND_CANDIDATE]
    if decision.binding_infeasibility is not None:
        artifacts.append(STAGE4_EVIDENCE_BINDING)
    return _make_proof(
        novel_dir, artifacts,
        _design_decision_payload(decision),
    )


def _design_request_matches_subjects(
    request: DesignReviewMaterialized | DesignReviewDecision,
    current: _DesignSubjects,
) -> bool:
    return (
        request.candidate_sha256 == current.candidate_sha256
        and _design_request_matches_business_subjects(request, current)
    )


def _design_request_matches_business_subjects(
    request: DesignReviewMaterialized | DesignReviewDecision,
    current: _DesignSubjects,
) -> bool:
    """比较人审业务主体，故意不把候选 provenance 文件 SHA 当成新内容。

    完整文件 SHA 仍由 ``proof`` 绑定。确定性重写若只改变 provenance，旧决定先
    投影为 expired，随后只允许同一外部 typed payload 重绑新 proof；不能把旧
    token 用来改票。把这两类身份拆开，才不会在 subject 未变时形成永久复批死锁。
    """
    if (
        request.aggregate_subject_sha256 != current.aggregate_subject_sha256
        or request.binding_infeasibility != current.binding_infeasibility
        or request.preflight != current.preflight
        or request.repair_capability != current.repair_capability
        or request.strategy.subject != current.strategy
        or request.strategy.subject_sha256 != current.strategy_subject_sha256
        or len(request.chapters) != len(current.chapters)
    ):
        return False
    return all(
        row.output_ordinal == subject.chapter.output_ordinal
        and row.subject == subject
        and row.subject_sha256
        == current.chapter_subject_sha256[row.output_ordinal]
        for row, subject in zip(request.chapters, current.chapters, strict=True)
    )


def _stored_design_matches_business_subject(
    decision: _StoredDesignHistory,
    current: _DesignSubjects,
) -> bool:
    """判断 raw 最新 A 决定是否仍属于当前人真正看过的同一业务主体。"""
    if isinstance(decision, _StoredDesignDecisionSlim):
        return (
            decision.aggregate_subject_sha256 == current.aggregate_subject_sha256
            and decision.strategy_subject_sha256 == current.strategy_subject_sha256
            and decision.strategy_epoch_sha256 == current.strategy_epoch_sha256
            and decision.chapter_subject_sha256 == current.chapter_subject_sha256
            and decision.repair_capability == current.repair_capability
            and decision.binding_infeasibility == current.binding_infeasibility
        )
    return _design_request_matches_business_subjects(
        _public_design_decision(decision), current)


def _stored_design_external_payload(
    decision: _StoredDesignHistory,
) -> dict[str, Any]:
    """还原唯一外部 typed payload；不把服务端展开的 subject 混入改票比较。"""
    if isinstance(decision, _StoredDesignDecisionSlim):
        return decision.request.model_dump(mode='json')
    return {
        'actor': decision.actor,
        'reviewer_kind': decision.reviewer_kind,
        'review_run_id': decision.review_run_id,
        'general_reason': decision.general_reason,
        'review_token': _design_review_token(decision.aggregate_subject_sha256),
        'strategy': {
            'decision': decision.strategy.decision,
            'reason': decision.strategy.reason,
            'replan_constraints': [{
                'kind': row.kind,
                'chapter_ordinals': row.chapter_ordinals,
            } for row in decision.strategy.replan_constraints],
        },
        'chapters': [{
            'output_ordinal': row.output_ordinal,
            'decision': row.decision,
            'reason': row.reason,
            'constraints': [
                constraint.model_dump(mode='json')
                for constraint in row.constraints
            ],
            **({'constraint_operations': row.constraint_operations.model_dump(mode='json')}
               if row.constraint_operations is not None else {}),
        } for row in decision.chapters],
    }


def _structure_decision_payload(
    decision: StructureApprovalRequest | StructureApprovalDecision
    | _StoredStructureDecision,
) -> dict[str, Any]:
    """Typed 结构裁决的唯一 proof 投影；服务时间不改写人作出的决定。"""
    subject = (
        decision.subject_sha256
        if isinstance(decision, _StoredStructureDecision)
        else decision.review_token.removeprefix('s4sr1_')
    )
    return {
        'kind': 'stage4_structure',
        'subject_sha256': subject,
        'actor': decision.actor,
        'reason': decision.reason,
        'chapters': [row.model_dump(mode='json') for row in decision.chapters],
    }


def _public_structure_decision(
    decision: _StoredStructureDecision,
) -> StructureApprovalDecision:
    return StructureApprovalDecision(
        actor=decision.actor,
        reason=decision.reason,
        review_token=f's4sr1_{decision.subject_sha256}',
        chapters=decision.chapters,
        decided_at=decision.decided_at,
    )


def _writing_decision_payload(
    decision: WritingApprovalRequest | WritingApprovalDecision
    | _StoredWritingDecision,
) -> dict[str, Any]:
    """写作授权的唯一 proof 投影；服务端时间不参与人的决定摘要。"""
    subject = (
        decision.subject_sha256
        if isinstance(decision, _StoredWritingDecision)
        else decision.review_token.removeprefix('s5wr1_')
    )
    return {
        'kind': 'stage5_writing_approval',
        'subject_sha256': subject,
        'actor': decision.actor,
        'reason': decision.reason,
    }


def _public_writing_decision(
    decision: _StoredWritingDecision,
) -> WritingApprovalDecision:
    return WritingApprovalDecision(
        actor=decision.actor,
        reason=decision.reason,
        review_token=f's5wr1_{decision.subject_sha256}',
        decided_at=decision.decided_at,
    )


def _current_structure_subject_and_chapters(
    novel_dir: Path,
) -> tuple[str, list[int]]:
    """与付费工作包共用同一 typed 快照，缺蓝图时不发布审阅身份。"""
    snapshot = _structure_workpack_snapshot(novel_dir)
    return snapshot.subject_sha256, snapshot.chapter_ordinals


def _structure_chapter_subject_sha256(
    plan: PlanChapters, concerns: list[BindingConcern], *,
    blueprints: ChapterBlueprints,
    binding_input_contract: str | None = None,
) -> dict[int, str]:
    """结构人审的逐章复用主体；业务 Plan 投影复用生产唯一尺子。

    全局 concern（``chapter_ordinal=None``）影响每一章；章级 concern
    只影响它所属章。因此改一章不会让其他章的 exact accept
    凭空过期，但全局担忧变化会让全章都重审。
    """
    from novel.stages.stage4.measure import structure_blueprint_subject, structure_subject

    projected = structure_subject(plan)
    projected_by = {
        int(row.get('output_ordinal') or 0): row
        for row in projected.get('output_chapters') or []
    }
    blueprint_evidence = structure_blueprint_subject(blueprints)
    blueprint_by: dict[int, list[dict[str, Any]]] = {}
    for row in blueprint_evidence['blueprints']:
        blueprint_by.setdefault(row['chapter_ordinal'], []).append(row)
    has_global_concern = any(row.chapter_ordinal is None for row in concerns)
    hashes: dict[int, str] = {}
    for chapter in plan.output_chapters:
        ordinal = chapter.output_ordinal
        if not has_global_concern and len(blueprint_by.get(ordinal, [])) != 1:
            raise ValueError(f'结构审阅需要第 {ordinal} 章唯一 Blueprint')
        applicable = [
            concern.model_dump(mode='json')
            for concern in concerns
            if concern.chapter_ordinal in (None, ordinal)
        ]
        hashes[ordinal] = _decision_sha256({
            'kind': 'stage4_structure_chapter_review_subject',
            'review_contract': STRUCTURE_REVIEW_CONTRACT,
            'chapter': projected_by[ordinal],
            'blueprint_evidence': (
                blueprint_evidence if has_global_concern else
                {'blueprints': blueprint_by[ordinal]}),
            'binding_concerns': applicable,
            **({'binding_input_contract': binding_input_contract}
               if binding_input_contract is not None else {}),
        })
    return hashes


def _current_structure_chapter_subject_sha256(
    novel_dir: Path,
) -> dict[int, str]:
    """与整书主体及实际下发材料共用蓝图依赖。"""
    return _structure_workpack_snapshot(novel_dir).chapter_subject_sha256


def _structure_workpack_snapshot(
    novel_dir: Path,
    *, with_materials: bool = False,
) -> _StructureWorkpackSnapshot:
    """从同一组 typed 对象铸造 B subject/coverage，并保留精确载荷摘要。"""
    store = _store_for(novel_dir)
    _require_fresh_json(store, STAGE4_PLAN_CHAPTERS)
    plan, plan_raw = store.read_own(PlanChapters, STAGE4_PLAN_CHAPTERS)
    _require_fresh_json(store, STAGE4_EVIDENCE_BINDING)
    binding, binding_raw = store.read_own(
        EvidenceBinding, STAGE4_EVIDENCE_BINDING)
    _require_fresh_json(store, STAGE2_CHAPTER_BLUEPRINTS)
    blueprints, blueprints_raw = store.read_own(
        ChapterBlueprints, STAGE2_CHAPTER_BLUEPRINTS)
    return _structure_snapshot_from_artifacts(
        store, (plan, plan_raw), (binding, binding_raw), (blueprints, blueprints_raw),
        with_materials=with_materials)


def _structure_snapshot_from_artifacts(
    store: ArtifactStore,
    plan_snapshot: tuple[PlanChapters, Any],
    binding_snapshot: tuple[EvidenceBinding, Any],
    blueprint_snapshot: tuple[ChapterBlueprints, Any],
    *, with_materials: bool = False,
) -> _StructureWorkpackSnapshot:
    """从同一批已读取对象编译审阅主体；页面与审批共享完整材料依赖。

    本函数不授予审批权限；正式工作包入口先核 freshness。页面可以展示历史
    计划，但不能把缺少 Global/KL/原文依赖的简化 hash 冒充正式审阅主体。
    """
    plan, plan_raw = plan_snapshot
    binding, binding_raw = binding_snapshot
    blueprints, blueprints_raw = blueprint_snapshot
    concerns = _binding_concerns(binding)
    binding_input_contract = _binding_input_contract_identity(binding)
    try:
        from novel.stages.stage4.measure import structure_review_subject_sha256
    except ImportError as exc:
        raise ArtifactUnreadable(
            'stage4 structure subject helper is unavailable') from exc
    subject = structure_review_subject_sha256(
        plan, concerns, blueprints=blueprints,
        binding_input_contract=binding_input_contract)
    from novel.stages.stage2.global_review import generation_grounding
    from novel.stages.stage2.knowledge_links import require_current
    from novel.stages.stage4.plot_materials import (
        chapter_materials, chapter_materials_identity, material_identity,
    )
    global_g = generation_grounding(store).model_dump(by_alias=True, mode='json')
    from novel.stages.stage2.grounding import read_registry
    characters = read_registry(store).model_dump(by_alias=True, mode='json')
    knowledge = require_current(store.ctx).model_dump(by_alias=True, mode='json')
    main, _ = store.read_own(MainText, STAGE1_MAIN_TEXT)
    blueprint_payload = blueprints.model_dump(by_alias=True, mode='json')
    main_payload = main.model_dump(by_alias=True, mode='json')
    material_by, material_hashes = {}, {}
    for row in plan.output_chapters:
        args = (row.output_ordinal, blueprint_payload, global_g, knowledge, main_payload,
                [ref.model_dump(mode='json') for goal in
                 (row.chapter_goal, row.open_hook_goal, row.end_hook_goal) for ref in goal.subject_refs])
        material_hashes[row.output_ordinal] = chapter_materials_identity(*args, characters=characters)
        if with_materials:
            material = chapter_materials(*args, characters=characters)
            if material_identity(material) != material_hashes[row.output_ordinal]:
                raise ArtifactUnreadable('结构工作包与轻量依赖摘要不一致')
            material_by[row.output_ordinal] = material
    subject = _decision_sha256({'plan_subject': subject, 'chapter_materials': material_hashes})
    chapter_hashes = _structure_chapter_subject_sha256(
        plan, concerns, blueprints=blueprints, binding_input_contract=binding_input_contract)
    chapter_hashes = {n: _decision_sha256({'plan_subject': sha, 'materials': material_hashes[n]})
                      for n, sha in chapter_hashes.items()}
    ordinals = [row.output_ordinal for row in plan.output_chapters]
    if ordinals != sorted(set(ordinals)) or any(value < 1 for value in ordinals):
        raise ArtifactUnreadable(
            'stage4 structure workpack has a non-canonical output chapter set')
    return _StructureWorkpackSnapshot(
        subject_sha256=subject,
        chapter_ordinals=ordinals,
        chapter_subject_sha256=chapter_hashes,
        plan=plan,
        concerns=concerns,
        binding_input_contract=binding_input_contract,
        blueprints=blueprints,
        chapter_materials=material_by,
        plan_payload_sha256=_decision_sha256(plan_raw),
        binding_payload_sha256=_decision_sha256(binding_raw),
        blueprint_payload_sha256=_decision_sha256(blueprints_raw),
    )


def _binding_concerns(binding: EvidenceBinding) -> list[BindingConcern]:
    """bound B 的非阻断担忧唯一状态判定，供普通审批与快照共用。"""
    if binding.status != 'bound':
        raise ApprovalRequired(
            '最终结构审批只接受当前 status=bound 的 EvidenceBinding')
    return list(binding.binding_concerns)


def _current_structure_binding(
    novel_dir: Path, *, store: ArtifactStore | None = None,
) -> EvidenceBinding:
    current_store = store or _store_for(novel_dir)
    _require_fresh_json(current_store, STAGE4_EVIDENCE_BINDING)
    binding, _raw = current_store.read_own(
        EvidenceBinding, STAGE4_EVIDENCE_BINDING)
    _binding_concerns(binding)
    return binding


def _binding_input_contract_identity(binding: EvidenceBinding) -> str | None:
    from novel.stages.stage4.binding_input_contract import inspect_binding_input_contract

    verdict = inspect_binding_input_contract(binding)
    if verdict.state not in {'legacy', 'current'}:
        raise UpstreamUnproven(
            f'Stage4B 输入契约没有当前审阅权威：{verdict.state}')
    return verdict.identity


def _current_binding_concerns(
    novel_dir: Path, *, store: ArtifactStore | None = None,
) -> list[BindingConcern]:
    """发布当前 bound B 的非阻断担忧；不把它们重新判成机械失败。"""
    return _binding_concerns(_current_structure_binding(novel_dir, store=store))


def _proof_for_structure(
    novel_dir: Path,
    decision: StructureApprovalRequest | StructureApprovalDecision,
) -> _ApprovalProof:
    """绑定正文根、完整计划/B/蓝图字节与逐章 typed 决定。"""
    return _make_proof(
        novel_dir, (STAGE4_PLAN_CHAPTERS, STAGE4_EVIDENCE_BINDING,
                    STAGE2_CHAPTER_BLUEPRINTS),
        _structure_decision_payload(decision),
    )


def _writing_subject_sha256(proof: _ApprovalProof) -> str:
    """把根、完整输入字节与 Stage5 契约折成 Commander 必须回传的 current SHA。"""
    return _decision_sha256({
        'kind': 'stage5_writing_subject',
        'proof': proof.model_dump(mode='json'),
    })


def _current_writing_subject(novel_dir: Path) -> tuple[str, _ApprovalProof]:
    proof = _proof_for_writing(novel_dir)
    return _writing_subject_sha256(proof), proof


def _proof_for_writing_decision(
    novel_dir: Path,
    decision: WritingApprovalRequest | WritingApprovalDecision,
) -> _ApprovalProof:
    """绑定正文根、Stage1/Plan/Context 字节与 actor/reason/current SHA。"""
    return _make_proof(
        novel_dir, (
            STAGE1_MAIN_TEXT, STAGE4_PLAN_CHAPTERS, STAGE4_WRITER_CONTEXT,
        ),
        _writing_decision_payload(decision),
    )


def _design_review_view(
    novel_dir: Path, record: _ApprovalRecordV8,
    *, current: _DesignSubjects | None = None,
) -> DesignReviewView:
    """即时投影当前 Stage4A 人审态；旧决定只留历史，不自动拼成新批准。"""
    history = [
        _design_history_entry(decision)
        for decision in record.design_history
    ]
    if current is None:
        try:
            current = _current_design_subjects(novel_dir)
        except NovelError:
            return DesignReviewView(
                state='unavailable', current_decision=None, history=history)
    latest = record.design_decision
    if latest is None:
        return DesignReviewView(
            state='missing',
            candidate_sha256=current.candidate_sha256,
            aggregate_subject_sha256=current.aggregate_subject_sha256,
            binding_infeasibility=current.binding_infeasibility,
            preflight=current.preflight,
            repair_capability=current.repair_capability,
            current_decision=None,
            history=[],
        )
    review_inheritance.validate_history(_design_inheritance_history(record))
    # 新处置的当前权利依赖整条历史解释链。中央 view 在发布 current 之前
    # 复验同 epoch 含 operations 的链，让 GET/operator/B 授权使用同一边界。
    # 失败保留 typed unknown 异常，不能伪装为可用批准或普通没有候选。
    if any(
        (stored.strategy_epoch_sha256 == current.strategy_epoch_sha256
         and any(row.constraint_operations is not None for row in stored.request.chapters))
        if isinstance(stored, _StoredDesignDecisionSlim) else any(
            row.subject.strategy_epoch_sha256 == current.strategy_epoch_sha256
            and row.constraint_operations is not None for row in stored.chapters)
        for stored in record.design_history
    ):
        _design_constraint_catalog(record, current)
    try:
        public = _public_design_decision(latest, current)
    except HumanEditProtected:
        public = None
    try:
        if isinstance(latest, _StoredDesignDecisionSlim):
            payload = _slim_design_payload(latest.request, current)
            artifacts = [STAGE4_BLIND_CANDIDATE]
            if current.binding_infeasibility is not None:
                artifacts.append(STAGE4_EVIDENCE_BINDING)
            current_proof = _make_proof(novel_dir, artifacts, payload)
        elif public is not None:
            current_proof = _proof_for_design(novel_dir, public)
        else:
            current_proof = None
    except NovelError:
        current_proof = None
    digest = (
        latest.decision_sha256 if isinstance(latest, _StoredDesignDecisionSlim)
        else _decision_sha256(_design_decision_payload(public))
        if public is not None else None
    )
    if (
        public is None
        or latest.decision_sha256 != digest
        or not _design_request_matches_subjects(public, current)
        or current_proof is None
        or not _same_proof(latest.proof, current_proof)
    ):
        return DesignReviewView(
            state='expired',
            candidate_sha256=current.candidate_sha256,
            aggregate_subject_sha256=current.aggregate_subject_sha256,
            binding_infeasibility=current.binding_infeasibility,
            preflight=current.preflight,
            repair_capability=current.repair_capability,
            current_decision=None,
            history=history,
        )
    state = (
        'strategy_replan_requested'
        if public.strategy.decision == 'request_replan'
        else 'repair_requested'
        if any(row.decision == 'request_repair' for row in public.chapters)
        else 'accepted'
    )
    return DesignReviewView(
        state=state,
        candidate_sha256=current.candidate_sha256,
        aggregate_subject_sha256=current.aggregate_subject_sha256,
        binding_infeasibility=current.binding_infeasibility,
        preflight=current.preflight,
        repair_capability=current.repair_capability,
        action_review_token=(
            _design_action_review_token(public.decision_sha256)
            if (
                state in ('repair_requested', 'strategy_replan_requested')
            )
            else None),
        current_decision=public,
        history=history,
    )


def _structure_view(
    novel_dir: Path, record: _ApprovalRecordV8,
) -> StructureApprovalView:
    """即时投影当前结构态；失效只影响有效性，不擦掉历史决定。"""
    history = [
        _public_structure_decision(decision)
        for decision in record.structure_history
    ]
    latest = record.structure_decision
    try:
        current_subject, _chapters = _current_structure_subject_and_chapters(novel_dir)
        concerns = _current_binding_concerns(novel_dir)
    except NovelError:
        return StructureApprovalView(
            state='expired' if history else 'unavailable',
            current_subject_sha256=None,
            binding_concerns=[],
            current_decision=None,
            history=history,
        )
    if latest is None:
        return StructureApprovalView(
            state='missing', current_subject_sha256=current_subject,
            review_token=f's4sr1_{current_subject}',
            binding_concerns=concerns,
            current_decision=None, history=[],
        )
    public = _public_structure_decision(latest)
    review_inheritance.validate_history(_structure_inheritance_history(record))
    try:
        current_proof = _proof_for_structure(novel_dir, public)
    except NovelError:
        current_proof = None
    if (
        latest.subject_sha256 != current_subject
        or current_proof is None
        or not _same_proof(latest.proof, current_proof)
    ):
        return StructureApprovalView(
            state='expired', current_subject_sha256=current_subject,
            review_token=f's4sr1_{current_subject}',
            binding_concerns=concerns,
            current_decision=None, history=history,
        )
    state = (
        'replan_requested'
        if any(row.decision == 'request_replan' for row in public.chapters)
        else 'accepted'
    )
    return StructureApprovalView(
        state=state, current_subject_sha256=current_subject,
        review_token=f's4sr1_{current_subject}',
        binding_concerns=concerns,
        current_decision=public, history=history,
    )


def _writing_view(
    novel_dir: Path, record: _ApprovalRecordV8,
) -> WritingApprovalView:
    """即时投影写作授权；旧/stale 决定留在 history，但绝不冒充当前。"""
    history = [
        _public_writing_decision(decision)
        for decision in record.writing_history
    ]
    if not record.gate['s4']:
        return WritingApprovalView(
            available=False,
            current_subject_sha256=None,
            current_decision=None,
            history=history,
        )
    try:
        current_subject, _current_proof = _current_writing_subject(novel_dir)
    except NovelError:
        return WritingApprovalView(
            available=False,
            current_subject_sha256=None,
            current_decision=None,
            history=history,
        )
    latest = record.writing_decision
    if latest is None:
        return WritingApprovalView(
            available=True,
            current_subject_sha256=current_subject,
            review_token=f's5wr1_{current_subject}',
            current_decision=None,
            history=history,
        )
    public = _public_writing_decision(latest)
    try:
        current_decision_proof = _proof_for_writing_decision(novel_dir, public)
    except NovelError:
        current_decision_proof = None
    current = (
        public
        if (
            latest.subject_sha256 == current_subject
            and current_decision_proof is not None
            and _same_proof(latest.proof, current_decision_proof)
        )
        else None
    )
    return WritingApprovalView(
        available=True,
        current_subject_sha256=current_subject,
        review_token=f's5wr1_{current_subject}',
        current_decision=current,
        history=history,
    )


def _public_closure_decision(
    decision: _StoredClosureDecision,
) -> Stage5ClosureDecision:
    return Stage5ClosureDecision(
        actor=decision.actor,
        reason=decision.reason,
        review_token=f's5cr1_{decision.workpack_sha256}',
        must_appear=[{
            'chapter_ordinal': row.chapter_ordinal,
            'evidence_key': row.evidence_key,
            'ruling': row.ruling,
            'reason': row.reason,
        } for row in decision.must_appear],
        group_acks=[{
            'group': row.group,
            'acknowledged': True,
        } for row in decision.group_acks],
        decision_sha256=decision.decision_sha256,
        decided_at=decision.decided_at,
    )


def _closure_proof_payload(workpack_sha256: str) -> dict[str, str]:
    """Stage5 typed closure 与 ``gate.s5`` 共用的 proof 决策主体。"""
    return {
        'kind': 'closure',
        'closure_sha256': workpack_sha256,
    }


def _closure_view(novel_dir: Path, record: _ApprovalRecordV8) -> Stage5ClosureView:
    """把当前盘面 + 盘上回执历史折成公开闭合视图；workpack 恒按当前盘面重算。

    书还没有进入 Stage5（无 stage5 目录）时 nothing to close ⇒ missing，
    不去构造 workpack（那会把「没有 Review」读成一次注定失败的判定）。
    """
    from novel.stages.stage5.closure import closure_view as _workpack_view
    history = [_public_closure_decision(row) for row in record.closure_history]
    current = (
        None if record.closure_decision is None
        else _public_closure_decision(record.closure_decision))
    if not (novel_dir / 'stage5').is_dir():
        if history or current is not None:
            return Stage5ClosureView(
                state='review_required',
                next_step=(
                    'Stage5 产物目录缺失，但盘上仍有历史闭合回执。'
                    '先恢复 canonical 章稿，再零成本运行一次 stage5.review'),
                history=history,
            )
        return Stage5ClosureView(state='missing')
    store = _store_for(novel_dir)
    try:
        return _workpack_view(
            store, record_history=history, record_current=current,
            record_current_workpack_sha256=(
                None if record.closure_decision is None
                else record.closure_decision.workpack_sha256))
    except NovelError as exc:
        # Stage5 目录已经存在却读不出 current Review，不是「没进入 Stage5」。
        # 保留历史并给出唯一恢复动作，绝不能把 stale/unreadable 画成 missing/ready。
        return Stage5ClosureView(
            state='review_required',
            next_step=(
                f'当前最终 Review 无法构造闭合工作包：{exc}。'
                '多次改稿完成后只需零成本运行一次 stage5.review'),
            history=history,
        )


def stage5_closure_view(base: str | os.PathLike[str], sid: str) -> Stage5ClosureView:
    """/approvals 投影之外的独立读口：require_stage5_closed 与 UI 消费同一份。"""
    with _LOCK:
        record = _read_current(base, sid)
        novel_dir = paths.resolve_novel_dir(base, sid)
        return _closure_view(novel_dir, record)


def stage5_closure_template(
    base: str | os.PathLike[str], sid: str,
) -> Stage5ClosureTemplate:
    """按当前盘面生成闭合工作包模板；只读、0 成本、不代签。"""
    with _LOCK:
        paths.resolve_novel_dir(base, sid)
        record = _read_current(base, sid)
        if not record.gate['s4']:
            raise ApprovalRequired('Stage5 closure requires current gate.s4')
        novel_dir = paths.resolve_novel_dir(base, sid)
        from novel.stages.stage5.closure import closure_template as _build
        return _build(_store_for(novel_dir))


def approve_stage5_closure(
    base: str | os.PathLike[str], sid: str, request: Stage5ClosureRequest,
) -> ApprovalDoc:
    """原子提交 Stage5 闭合回执；通过即打开 ``gate.s5``，不启动任何付费作业。"""
    from novel.stages.stage5.closure import (
        closure_workpack, materialize_closure_request,
        validate_closure_request,
    )
    novel_dir = paths.resolve_novel_dir(base, sid)
    # 锁序固定为 Stage5 书锁 → 审批文件锁：盘面快照、proof 与 gate.s5
    # 必须共同消费同一个正文/trim 主体，不能在回执尾窗让写者穿过。
    with (stage5_runs.book_commit_ownership(novel_dir),
          approval_file_lock(base, sid)):
        raw, record = _read_state(base, sid)
        store = _store_for(novel_dir)
        pack = validate_closure_request(store, request)
        materialized = materialize_closure_request(request, pack)
        # 双读防提交期间盘面漂移（与 design review 的提交窗口一致）。
        after = closure_workpack(store)
        if after.workpack_sha256 != pack.workpack_sha256:
            raise HumanEditProtected(
                'Stage5 盘面在回执提交期间发生变化；请刷新模板后重新裁决')
        proof = _make_proof(
            novel_dir, _stage5_subject_rels(base, sid, record),
            _closure_proof_payload(pack.workpack_sha256))
        payload = request.model_dump(mode='json')
        digest = _decision_sha256(payload)
        latest = raw.closure_decision
        if (
            latest is not None
            and latest.decision_sha256 == digest
            and _same_proof(latest.proof, proof)
        ):
            if record.gate['s5'] and _same_proof(
                record.gate_proofs['s5'], proof,
            ):
                return _view(base, record)
            # 同一 typed 回执已存在，但旧/并行实现可能留下不同的 gate proof。
            # 复用本次已验证的统一 proof 原子补正，不追加第二条人工决定。
            gates = dict(raw.gate)
            gate_proofs = dict(raw.gate_proofs)
            gates['s5'] = True
            gate_proofs['s5'] = proof
            return _write(base, raw.model_copy(update={
                'gate': gates, 'gate_proofs': gate_proofs,
            }))
        decided_at = _now()
        stored = _StoredClosureDecision(
            **materialized.model_dump(mode='json'),
            decision_sha256=digest, decided_at=decided_at, proof=proof)
        gates = dict(raw.gate)
        gate_proofs = dict(raw.gate_proofs)
        gates['s5'] = True
        gate_proofs['s5'] = proof
        return _write(base, raw.model_copy(update={
            'closure_history': [*raw.closure_history, stored],
            'closure_decision': stored,
            'gate': gates, 'gate_proofs': gate_proofs,
        }))


def _stage4_replan_cause(
    novel_dir: Path, structure: StructureApprovalView,
) -> Literal['structure_decision', 'evidence_infeasible'] | None:
    """Stage4 付费重规划入口的唯一生产判定。

    人工逐章裁决与 Stage4B typed 负结论来自两张不同的决策面，但它们的下一步
    相同：另行确认重跑 A，绝不自动串 B。B 只有在完整产物可读且 provenance
    明确 fresh 时才有权给出这条恢复路；旧/坏/陈旧 binding 不能借 status 冒充
    当前结论。
    """
    if structure.state == 'replan_requested':
        return 'structure_decision'
    return (
        'evidence_infeasible'
        if current_infeasible_binding(_store_for(novel_dir)) is not None
        else None
    )


def _require_typed_gate_decision(store: ArtifactStore, gate: str) -> str | None:
    if gate == 's0':
        from novel.stages.stage0 import revisions as stage0_revisions
        revisions, _ = store.read_own(Stage0Revisions, STAGE0_REVISIONS)
        stage0_revisions.verify_chain(revisions)
        report = stage0_revisions.snapshot_of(revisions)
        # 付费防线与 stage1 启动闸用同一个谓词（eligibility 是唯一落点），
        # 不在这里复述批准条件——stage2 是第一个花钱的层，漏一处就是绕过闸门花钱。
        from novel.stages.stage0.eligibility import is_stage0_executable
        if not is_stage0_executable(report):
            raise ApprovalRequired(
                'stage0 removal report has not actually been approved')
    elif gate == 's1':
        from novel.stages.stage0 import revisions as stage0_revisions
        from novel.stages.stage1.audit import audit_surface

        revisions, _ = store.read_own(Stage0Revisions, STAGE0_REVISIONS)
        stage0_revisions.verify_chain(revisions)
        stage0 = stage0_revisions.snapshot_of(revisions)
        clean_text = store.read_own_text(STAGE0_CLEAN_TEXT)
        main_text, _ = store.read_own(MainText, STAGE1_MAIN_TEXT)
        hook_copy, _ = store.read_own(HookCopy, STAGE1_HOOK_COPY)
        audit = audit_surface(
            clean_text=clean_text,
            stage0=stage0,
            main_text=main_text,
            hook_copy=hook_copy,
        )
        if audit.hard_findings:
            details = '；'.join(item.detail for item in audit.hard_findings[:6])
            raise ApprovalRequired(f'stage1 当前材料未通过 HARD 审计：{details}')
    elif gate == 's2':
        # s2 不是“review 那张表全 PASS”的别名。公开盖章与 CLI/API/queue 的
        # Stage4 前置必须共用同一座完整收口闸：current global + current
        # blueprint + 联合 HARD + current all-PASS review。否则 UI 会先显示 s2
        # 已打开，真实 Stage4 子进程却在 dispatch 前拒绝，形成一份会说谎的 proof。
        from novel.stages.stage2 import global_review as stage2_global_review
        stage2_global_review.require_approved(store.ctx)
    elif gate == 's6':
        from novel.stages.stage6 import prepare as prepare_stage6
        prepare_stage6.require_accepted(store)
        hook, _ = store.read_own(Hook, STAGE6_HOOK)
        from novel.stages.stage6 import semantic
        semantic.require_approved_clear(store, hook)
    elif gate == 's7':
        names, _ = store.read_own(NameMap, STAGE7_NAME_MAP)
        if names.approved is None:
            raise ApprovalRequired('stage7 name map has no typed approved decision')
        from novel.stages.stage7.rename import approval_proof_sha256
        proof = approval_proof_sha256(store, names)
        if proof is None:
            raise ApprovalRequired(
                'stage7 typed approval is not bound to the current name decision surface')
        return proof
    return None


def _blueprint_seen_proof(
    novel_dir: Path, workpack: BlueprintReviewWorkpack,
) -> tuple[_ApprovalProof, dict[str, str]]:
    """把一份已构造工作包铸成可重算 proof，并拒绝构造后的主体竞态。"""
    if workpack.binding.subject_kind != 'formal' or workpack.scope != 'cross_chapter':
        raise ApprovalRequired(
            'seen.read 只接受 current formal Blueprint 工作包；'
            'INVALID 工作包只能用于恢复，不能生成已读证明')
    # exact token 只守本次提交窗口；耐久 proof 只绑定可见内容与显式审阅
    # 契约。代码/rules/provenance 改变会得到新 validation seal，但只要工作包
    # 的内容/finding identity 未变，旧人工阅读事实就能在免费复检后自动复活。
    proof = _ApprovalProof(
        root_clean_sha1=_proof_source_root(_store_for(novel_dir), [STAGE2_CHAPTER_BLUEPRINTS]),
        artifacts={
            STAGE2_CHAPTER_BLUEPRINTS: workpack.review_identity_sha256,
        },
        decision_sha256=_decision_sha256({
            'kind': 'seen',
            'key': 'read',
            'contract': workpack.review_contract_version,
            'review_identity_sha256': workpack.review_identity_sha256,
        }),
    )
    identity = {
        'review_token': workpack.review_token,
        'review_identity': workpack.review_identity_sha256,
        'validation_seal': workpack.validation_seal_sha256,
    }
    return proof, identity


def _blueprint_seen_snapshot(
    novel_dir: Path, *, exact: bool = False,
) -> tuple[_ApprovalProof, dict[str, str]]:
    """用当前只读投影重建完整工作包，再读出 proof 与明文身份。"""
    from novel.views.stage2 import blueprint_review_workpack

    sid, _title, _platform = paths.derive_source_meta(novel_dir.name)
    if not exact:
        from novel.stages.stage2.plot_review import CONTRACT
        snapshot = _plot_snapshot(novel_dir, _read_raw(novel_dir.parent, sid))
        progress = snapshot.progress()
        if not progress.accepted or snapshot.blueprint_rel != STAGE2_CHAPTER_BLUEPRINTS:
            raise ApprovalRequired('Blueprint 全书阅读证明缺当前章审与跨章接受')
        subject = next(row.subject_sha256 for row in progress.scopes if row.scope == 'cross_chapter')
        proof = _ApprovalProof(
            root_clean_sha1=_proof_source_root(_store_for(novel_dir), [STAGE2_CHAPTER_BLUEPRINTS]),
            artifacts={STAGE2_CHAPTER_BLUEPRINTS: subject},
            decision_sha256=_decision_sha256({
                'kind': 'seen', 'key': 'read', 'contract': CONTRACT,
                'review_identity_sha256': subject}))
        return proof, {'review_identity': subject}
    workpack = blueprint_review_workpack(
        novel_dir.parent, sid, code=_projection_code(), scope='cross_chapter')
    return _blueprint_seen_proof(novel_dir, workpack)


def _stage2_global_review_token(novel_dir: Path) -> str:
    """现算 ``seen.global`` 的 opaque 阅读凭证；主体不 current 时按现役异常拒绝。"""
    from novel.stages.stage2.global_review import review_token

    ctx = state.readonly_ctx(novel_dir, 'stage2.global', _projection_code())
    return review_token(ctx)


def _global_seen_proof(novel_dir: Path) -> tuple[_ApprovalProof, str]:
    """``seen.global`` 的耐久 proof 与明文 token；两者都只从盘上当前主体重算。"""
    token = _stage2_global_review_token(novel_dir)
    proof = _make_proof(
        novel_dir, _SEEN_ARTIFACTS['global'],
        {'kind': 'seen', 'key': 'global', 'review_token': token})
    return proof, token


def _proof_for_seen(novel_dir: Path, key: str) -> _ApprovalProof:
    if key == 'read':
        return _blueprint_seen_snapshot(novel_dir)[0]
    return _global_seen_proof(novel_dir)[0]


def _writing_proof_payload() -> dict[str, str]:
    """写作授权主体的固定契约投影；历史证明与新提交共用同一登记。"""
    return {
        'kind': 'writing',
        'scope': 'all_planned_chapters',
        'contract': 'stage5-v3',
        'measurement_contract': STYLE_MEASUREMENT_CONTRACT,
    }


def _proof_for_writing(novel_dir: Path) -> _ApprovalProof:
    return _make_proof(
        novel_dir, (
            STAGE1_MAIN_TEXT, STAGE4_PLAN_CHAPTERS, STAGE4_WRITER_CONTEXT,
        ),
        _writing_proof_payload(),
    )


def _stage5_subject_rels(
    base: str | os.PathLike[str], sid: str, record: _ApprovalRecordV8,
) -> list[str]:
    _require_stage5_complete(base, sid, record)
    planned = _planned(base, sid)
    assert planned is not None
    store = _store_for(paths.resolve_novel_dir(base, sid))
    from novel.stages.stage5.review import require_accepted_review
    review = require_accepted_review(store.ctx, store)
    assert review.review_rel is not None
    return [
        artifact_rel('stage5.chapter', nn=ordinal)
        for ordinal in planned
    ] + [review.review_rel]


def _proof_for_gate(
    novel_dir: Path, gate: str, record: _ApprovalRecordV8,
) -> _ApprovalProof:
    if gate == 's4':
        decision = record.structure_decision
        if decision is None or any(
            row.decision != 'accept' for row in decision.chapters
        ):
            raise ApprovalRequired(
                'stage4 requires a current typed structure decision with every chapter accepted')
        current_subject, chapters = _current_structure_subject_and_chapters(novel_dir)
        decided_chapters = [row.output_ordinal for row in decision.chapters]
        if decision.subject_sha256 != current_subject or decided_chapters != chapters:
            raise ApprovalRequired(
                'stage4 typed structure decision is bound to an older plan subject')
        return _proof_for_structure(
            novel_dir, _public_structure_decision(decision))
    store = _store_for(novel_dir)
    typed_subject = _require_typed_gate_decision(store, gate)
    if gate == 's7':
        # Validate schema, INVALID absence and provenance freshness exactly as
        # every other gate does, but bind the service approval to the typed
        # human decision instead of the whole mutable artifact byte string.
        # ``stage7.assemble`` writes only the derived ``book_txt`` ledger back
        # into this file; that authorized consequence must not revoke s7.
        _require_fresh_json(store, STAGE7_NAME_MAP)
        assert typed_subject is not None
        return _ApprovalProof(
            root_clean_sha1=_root_clean_sha1(novel_dir),
            artifacts={STAGE7_NAME_MAP: typed_subject},
            decision_sha256=_decision_sha256({
                'kind': 'gate', 'gate': gate,
            }),
        )
    if gate == 's5':
        # closure.py 在 require_stage5_closed() 的末端延迟 import approvals；
        # 这里也必须留在函数内，不能把循环依赖搬到模块 import 时。
        from novel.stages.stage5.closure import closure_workpack
        decision = record.closure_decision
        if decision is None:
            raise ApprovalRequired(
                's5 gate 只能由当前 typed closure 回执打开；缺闭合决定')
        pack = closure_workpack(store)
        if decision.workpack_sha256 != pack.workpack_sha256:
            raise ApprovalRequired(
                's5 closure 回执绑定的是过期工作包；请重新生成模板并重新裁决')
        rels = _stage5_subject_rels(novel_dir.parent, record.sid, record)
        return _make_proof(
            novel_dir, rels, _closure_proof_payload(decision.workpack_sha256))
    rels = list(_GATE_ARTIFACTS[gate])
    return _make_proof(
        novel_dir, rels, {'kind': 'gate', 'gate': gate})


def _same_proof(expected: _ApprovalProof | None, current: _ApprovalProof) -> bool:
    return expected is not None and expected == current


def _same_partition_confirmation(
    novel_dir: Path, expected: _ApprovalProof | None, current: _ApprovalProof,
) -> bool:
    """原切分确认在只改钩子后仍有效；不把 Stage0 接受变成新的 Stage1 票。

正文与切分必须逐字不变，且旧票的两份 SHA 都由旧 revision 原样重建。
当前两份材料已经过普通 proof 的 freshness；此处只证明旧确认仍适用。
"""
    from novel.source_identity import revision_proof
    subjects = {STAGE1_MAIN_TEXT, STAGE1_HOOK_COPY}
    if (expected is None or set(expected.artifacts) != subjects
            or set(current.artifacts) != subjects
            or expected.root_clean_sha1 == current.root_clean_sha1
            or expected.decision_sha256 != current.decision_sha256):
        return False
    try:
        proof = revision_proof(_store_for(novel_dir), expected.root_clean_sha1)
    except (NovelError, ValueError, OSError):
        return False
    return (proof.body_unchanged and proof.target_root == current.root_clean_sha1
            and expected.artifacts[STAGE1_MAIN_TEXT] == proof.main_text_sha256
            and current.artifacts[STAGE1_MAIN_TEXT] == proof.main_text_sha256
            and expected.artifacts[STAGE1_HOOK_COPY] == proof.source_hook_copy_sha256)


def _current_typed_gate_proof(
    novel_dir: Path, gate: str, record: _ApprovalRecordV8,
) -> _ApprovalProof:
    """只读复验正式 typed 决定；服务检查单缺章不构成另一道人工裁决。

    typed 验证与 proof 的产物读取可能跨过一次正式写回。必须再次复用完整
    producer/current/freshness 检查并核同一 proof，不能把 A 的批准拼到 B 的字节。
    返回值仅用于 effective 投影，不产生 raw 点击或补录事实。
    """
    if gate not in ('s0', 's6', 's7'):
        raise ValueError(f'不是正式 typed 决定闸门：{gate}')
    first = _proof_for_gate(novel_dir, gate, record)
    second = _proof_for_gate(novel_dir, gate, record)
    if not _same_proof(first, second):
        raise ArtifactUnreadable(f'{gate} 定稿证明在读取期间变化，不能投影为已确认')
    return second


def _effective_record(novel_dir: Path, record: _ApprovalRecordV8) -> _ApprovalRecordV8:
    """Return the effective current projection without rewriting audit history."""
    seen = dict(record.seen)
    seen_proofs = dict(record.seen_proofs)
    for key in APPROVAL_SEEN:
        if not seen[key]:
            continue
        try:
            current = _proof_for_seen(novel_dir, key)
        except NovelError:
            current = None
        if current is None or not _same_proof(seen_proofs[key], current):
            seen[key] = False
            seen_proofs[key] = None

    gates = dict(record.gate)
    gate_proofs = dict(record.gate_proofs)

    def validate_gate(gate: str) -> None:
        if not gates[gate]:
            return
        if any(not gates[key] for key in _GATE_REQUIREMENTS.get(gate, ())):
            gates[gate] = False
            gate_proofs[gate] = None
            return
        if gate == 's2' and not all(seen.values()):
            gates[gate] = False
            gate_proofs[gate] = None
            return
        try:
            current = _proof_for_gate(novel_dir, gate, record)
        except NovelError:
            current = None
        if current is None or not (_same_proof(gate_proofs[gate], current)
                or gate == 's1' and _same_partition_confirmation(novel_dir, gate_proofs[gate], current)):
            gates[gate] = False
            gate_proofs[gate] = None

    for gate in _PRE_WRITING_GATES:
        if gate == 's0':
            # CLI/Agent 与 HTTP 的批准都落同一 revision 链；只读面不能再等附加点击。
            try:
                current = _current_typed_gate_proof(novel_dir, gate, record)
            except NovelError:
                current = None
            gates[gate] = current is not None
            gate_proofs[gate] = current
        else:
            validate_gate(gate)

    writing = record.writing
    writing_proof = record.writing_proof
    if writing:
        try:
            current_writing = _proof_for_writing(novel_dir)
        except NovelError:
            current_writing = None
        if (
            current_writing is None
            or not _same_proof(writing_proof, current_writing)
            or not gates['s4']
        ):
            writing = False
            writing_proof = None

    # s5's proof builder needs the effective writing state, not a stale boolean
    # from disk. Later gates then cascade from the resulting s5 state.
    effective = record.model_copy(update={
        'gate': gates, 'seen': seen,
        'gate_proofs': gate_proofs, 'seen_proofs': seen_proofs,
        'writing': writing, 'writing_proof': writing_proof,
    })
    for gate in _POST_WRITING_GATES:
        # s6/s7 的正式事实在 Hook/NameMap 的 typed 决定中。HTTP 旧附加章只是
        # 检查单，不能让同一票因来自 CLI/Agent 而变成未审；s5 仍必须有原回执。
        final_gate = gate in ('s6', 's7')
        if not final_gate and not gates[gate]:
            continue
        if any(not gates[key] for key in _GATE_REQUIREMENTS.get(gate, ())):
            gates[gate] = False
            gate_proofs[gate] = None
            continue
        if gate == 's5' and not writing:
            gates[gate] = False
            gate_proofs[gate] = None
            continue
        try:
            current = (
                _current_typed_gate_proof(novel_dir, gate, effective)
                if final_gate else _proof_for_gate(novel_dir, gate, effective)
            )
        except NovelError:
            current = None
        gates[gate] = current is not None and (
            final_gate or _same_proof(gate_proofs[gate], current)
        )
        gate_proofs[gate] = current if gates[gate] else None
        effective = effective.model_copy(update={
            'gate': gates, 'gate_proofs': gate_proofs,
        })
    return effective


def _require_artifacts(
    base: str | os.PathLike[str], sid: str, gate: str,
    record: _ApprovalRecordV8,
) -> _ApprovalProof:
    novel_dir = paths.resolve_novel_dir(base, sid)
    return _proof_for_gate(novel_dir, gate, record)


def _require_stage5_complete(
    base: str | os.PathLike[str], sid: str, record: _ApprovalRecordV8,
) -> None:
    planned = _planned(base, sid)
    if planned is None:
        raise UpstreamMissing(f'{STAGE4_PLAN_CHAPTERS} is required before stage5 approval')
    novel_dir = paths.resolve_novel_dir(base, sid)
    _require_stage5_contract_compatible(novel_dir, action='盖 s5 审批章')
    done = set(state.chapters_done(novel_dir))
    missing = sorted(set(planned) - done)
    if missing:
        raise ApprovalRequired(f'stage5 still lacks canonical chapters: {missing}')
    unexpected = sorted(done - set(planned))
    if unexpected:
        raise HumanEditProtected(f'stage5 has drafts outside the current plan: {unexpected}')
    # Same classification as `_require_fresh_json`: a blocked draft is a gate
    # state, not a missing signature.  Stage5 blocks per chapter, so the pointer
    # is the directory that holds them — `GateBlocked.invalid_path` is documented
    # as "where the blocked draft(s) are", not "one file".
    blocked = sorted(p.relative_to(novel_dir).as_posix()
                     for p in (novel_dir / 'stage5').glob('ch*.INVALID.json'))
    if blocked:
        raise GateBlocked(
            f'stage5 still has blocked INVALID chapter drafts: {blocked}; an '
            f'approval cannot bind to them',
            invalid_path=str(novel_dir / 'stage5'),
            hint='stage5.write --revalidate --actor <id> --reason <why>, '
                 'or --apply-edit to fix those chapters first (both cost nothing)')


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec='seconds')


def _stored_design_is_self_consistent(decision: _StoredDesignHistory) -> bool:
    if isinstance(decision, _StoredDesignDecisionSlim):
        payload = {
            'kind': 'stage4_design_review_slim_v1',
            'request': decision.request.model_dump(mode='json'),
            'candidate_sha256': decision.candidate_sha256,
            'aggregate_subject_sha256': decision.aggregate_subject_sha256,
            'strategy_subject_sha256': decision.strategy_subject_sha256,
            'strategy_epoch_sha256': decision.strategy_epoch_sha256,
            'chapter_subject_sha256': decision.chapter_subject_sha256,
            'repair_capability': decision.repair_capability.model_dump(mode='json'),
            'binding_infeasibility': (
                None if decision.binding_infeasibility is None else
                decision.binding_infeasibility.model_dump(mode='json')),
        }
        digest = _decision_sha256(payload)
    else:
        public = _public_design_decision(decision)
        digest = _decision_sha256(_design_decision_payload(public))
    return (
        decision.decision_sha256 == digest
        and decision.proof.decision_sha256 == digest
    )


def stage4_review_payload_landed(
    base: str | os.PathLike[str], sid: str, *,
    surface: Literal['design', 'structure'], payload: dict[str, Any],
) -> bool:
    """只读证明完整外部 Stage4 回执曾正式落盘，供运输记录确定性退休。

    这是历史提交事实，不是当前批准：修后候选、token 或 code 改变都不会抹去
    已提交回执。逐值比较原 token、actor、理由、完整章集与全部 typed 约束，
    并重验正式记录自己的 decision/proof 联结；不能只按摘要展示行或新主体推测。
    缺文件、没有 exact 回执返回 False；盘面或匹配证据损坏仍抛出异常，不能把
    unknown 当成未提交。此入口不读当前小说产物、不补审批、不创建锁文件。
    """
    model = {'design': DesignReviewRequest, 'structure': StructureApprovalRequest}.get(surface)
    if model is None:
        raise HumanEditProtected('只接受 Stage4 design/structure 已落盘回执查询')
    try:
        request = model.model_validate(payload, strict=True)
    except ValidationError as exc:
        raise HumanEditProtected('待核 Stage4 外部回执不是现役 typed payload') from exc
    expected = request.model_dump(mode='json')
    if _decision_sha256(expected) != _decision_sha256(payload):
        raise HumanEditProtected('待核 Stage4 回执需要补字段或归一，不能冒充 exact 提交')
    with _LOCK:
        target = _path(base, paths.check_sid(sid))
        if target.is_symlink():
            raise HumanEditProtected('Stage4 正式审批来源不得是符号链接')
        if not target.exists():
            return False
        if not target.is_file():
            raise HumanEditProtected('Stage4 正式审批来源不是普通文件')
        record = _read_raw(base, sid, require_existing=True)
        if surface == 'design':
            matched = [stored for stored in record.design_history
                       if _stored_design_external_payload(stored) == expected]
            for stored in matched:
                expected_artifacts = {STAGE4_BLIND_CANDIDATE}
                if stored.binding_infeasibility is not None:
                    expected_artifacts.add(STAGE4_EVIDENCE_BINDING)
                if (not _stored_design_is_self_consistent(stored)
                        or expected['review_token'] != _design_review_token(
                            stored.aggregate_subject_sha256)
                        or set(stored.proof.artifacts) != expected_artifacts
                        or stored.proof.artifacts.get(STAGE4_BLIND_CANDIDATE)
                        != stored.candidate_sha256):
                    raise HumanEditProtected('匹配的 Stage4A 已落盘回执与原主体/proof 不闭合')
            return bool(matched)
        matched_structure = [stored for stored in record.structure_history
                             if _structure_decision_payload(stored)
                             == _structure_decision_payload(request)]
        for stored in matched_structure:
            # 已显式迁入 v7 的旧 plan-only proof 仍能证明历史提交，绝不因此
            # 获得当前 B 放行权；当前批准仍由 _structure_view 的 freshness 决定。
            if (set(stored.proof.artifacts) not in (
                    {STAGE4_PLAN_CHAPTERS},
                    {STAGE4_PLAN_CHAPTERS, STAGE4_EVIDENCE_BINDING})
                    or stored.proof.decision_sha256 != _decision_sha256(
                        _structure_decision_payload(stored))):
                raise HumanEditProtected('匹配的 Stage4B 已落盘回执与原主体/proof 不闭合')
        return bool(matched_structure)


def reviewer_approval_payload_landed(
    base: str | os.PathLike[str], sid: str, *, surface: str, payload: dict[str, Any],
) -> bool:
    """只读证明原 Commander 的完整 typed 审批已提交，供内容账补尾。

    历史中的 exact 回执与原主体 proof 才是提交事实。当前 gate、当前产物或
    新 token 均不参与；损坏证据必须暴露，不能被解释成尚未提交再购买模型。
    此查询不创建审批锁，也不授予当前主体任何新接受。
    """
    if surface == 'blueprint_refinement':
        from novel.orchestrator import blueprint_refinement
        return blueprint_refinement.payload_landed(base, sid, payload)
    models = {
        'stage2_global_review': Stage2NodeReviewRequest,
        'stage2_blueprint_review': Stage2NodeReviewRequest,
        'design': DesignReviewRequest,
        'structure': StructureApprovalRequest,
        'writing': WritingApprovalRequest,
        'stage5': Stage5ClosureRequest,
    }
    model = models.get(surface)
    if model is None:
        raise HumanEditProtected('未登记的 Commander 审批提交证明面')
    try:
        request = model.model_validate(payload, strict=True)
    except ValidationError as exc:
        raise HumanEditProtected('待核 Commander 回执不是完整的现役 typed payload') from exc
    expected = request.model_dump(mode='json')
    if _decision_sha256(expected) != _decision_sha256(payload):
        raise HumanEditProtected('待核 Commander 回执需要补字段或归一，不能冒充 exact 提交')
    if surface.startswith('stage2_'):
        expected_node = ('stage2.global' if surface == 'stage2_global_review'
                         else 'stage2.blueprint')
        if request.node != expected_node:
            raise HumanEditProtected('Stage2 回执的节点与审批面不一致')

    with _LOCK:
        target = _path(base, paths.check_sid(sid))
        try:
            if target.is_symlink():
                raise HumanEditProtected('正式审批来源不得是符号链接')
            target.stat()
            if not target.is_file():
                raise HumanEditProtected('正式审批来源不是普通文件')
        except FileNotFoundError:
            return False
        except OSError as exc:
            raise HumanEditProtected('正式审批来源不可读，不能推断未提交') from exc
        raw = _read_raw(base, sid, require_existing=True)

        if surface in ('stage2_global_review', 'stage2_blueprint_review'):
            return stage2_review_payload_landed(base, sid, expected)
        if surface in ('design', 'structure'):
            return stage4_review_payload_landed(base, sid, surface=surface, payload=expected)
        if surface == 'writing':
            matched = [stored for stored in raw.writing_history
                       if _public_writing_decision(stored).model_dump(
                           mode='json', exclude={'decided_at'}) == expected]
            for stored in matched:
                subject_proof = stored.proof.model_copy(update={
                    'decision_sha256': _decision_sha256(_writing_proof_payload()),
                })
                if (set(stored.proof.artifacts) != {
                        STAGE1_MAIN_TEXT, STAGE4_PLAN_CHAPTERS, STAGE4_WRITER_CONTEXT}
                        or stored.proof.decision_sha256 != _decision_sha256(
                            _writing_decision_payload(stored))
                        or _writing_subject_sha256(subject_proof) != stored.subject_sha256):
                    raise HumanEditProtected('匹配的写作授权回执与原主体/proof 不闭合')
            return bool(matched)

        matched = [stored for stored in raw.closure_history
                   if _public_closure_decision(stored).model_dump(
                       mode='json', exclude={'decision_sha256', 'decided_at'}) == expected]
        for stored in matched:
            # 对外载荷不包含模板标记，必须另核 materialize 后的正式终态；
            # 否则带相同裁决内容的未完成模板可能被误当成原请求已提交。
            if any(row.placeholder or row.template_token is not None
                   for row in (*stored.must_appear, *stored.group_acks)):
                raise HumanEditProtected('匹配的 Stage5 闭合历史含未完成模板，不能证明正式提交')
            subjects = stored.proof.artifacts
            reviews = [rel for rel in subjects
                       if artifact_key(rel) == artifact_rel('stage5.review')]
            chapters = [rel for rel in subjects
                        if artifact_key(rel) == artifact_rel('stage5.chapter')]
            if (stored.decision_sha256 != _decision_sha256(expected)
                    or stored.proof.decision_sha256 != _decision_sha256(
                        _closure_proof_payload(stored.workpack_sha256))
                    or len(reviews) != 1 or not chapters
                    or len(subjects) != len(reviews) + len(chapters)
                    or subjects[reviews[0]] != stored.review_sha256
                    or any(subjects.get(artifact_rel('stage5.chapter', nn=row.chapter_ordinal))
                           != row.chapter_sha256 for row in stored.must_appear)):
                raise HumanEditProtected('匹配的 Stage5 闭合回执与原主体/proof 不闭合')
        return bool(matched)


def _accepted_design_strategy_source(
    record: _ApprovalRecordV8, current: _DesignSubjects,
    *, sources: dict[str, review_inheritance.DecisionReceipt] | None = None,
) -> _StoredDesignHistory | None:
    chosen = (sources if sources is not None else _design_inheritance_sources(record, current)).get('strategy')
    return next((item for item in reversed(record.design_history)
                 if chosen is not None and item.decision_sha256 == chosen.decision_sha256), None)


def _accepted_design_chapter_source(
    record: _ApprovalRecordV8,
    subject: DesignChapterSubject,
    subject_sha256: str,
    *, sources: dict[str, review_inheritance.DecisionReceipt] | None = None,
) -> tuple[_StoredDesignHistory, DesignChapterReview | Any] | None:
    ordinal = subject.chapter.output_ordinal
    chosen = (sources if sources is not None else review_inheritance.accepted_sources(
        _design_inheritance_history(record), {str(ordinal): subject_sha256})).get(str(ordinal))
    if chosen is None:
        return None
    for stored in reversed(record.design_history):
        if stored.decision_sha256 != chosen.decision_sha256:
            continue
        rows = (
            stored.request.chapters
            if isinstance(stored, _StoredDesignDecisionSlim)
            else stored.chapters
        )
        row = next((item for item in rows if item.output_ordinal == ordinal), None)
        same_subject = (
            stored.chapter_subject_sha256.get(ordinal) == subject_sha256
            if isinstance(stored, _StoredDesignDecisionSlim)
            else row is not None and row.subject == subject
            and row.subject_sha256 == subject_sha256
        )
        if row is not None and same_subject:
            # 同主体最新拒绝遮住更早接受；B 请求重新规划后 A 的 resolution 同样适用。
            return (stored, row) if row.decision == 'accept' else None
    return None


def _review_decision_unit(row: Any, subject: str) -> review_inheritance.DecisionUnit:
    payload = {'decision': row.decision, 'reason': row.reason}
    for field in ('constraints', 'replan_constraints'):
        values = getattr(row, field, None)
        if values is not None:
            payload[field] = [value.model_dump(mode='json') for value in values]
    return review_inheritance.DecisionUnit(
        subject, payload, getattr(row, 'source_decision_sha256', None))


def _design_inheritance_history(record: _ApprovalRecordV8) -> list[review_inheritance.DecisionReceipt]:
    receipts = []
    for stored in record.design_history:
        slim = isinstance(stored, _StoredDesignDecisionSlim)
        request = stored.request if slim else stored
        units = {'strategy': _review_decision_unit(
            request.strategy, stored.strategy_subject_sha256 if slim else stored.strategy.subject_sha256)}
        units.update({str(row.output_ordinal): _review_decision_unit(
            row, stored.chapter_subject_sha256[row.output_ordinal] if slim else row.subject_sha256)
            for row in request.chapters})
        receipts.append(review_inheritance.DecisionReceipt(
            stored.decision_sha256, units, _stored_design_is_self_consistent(stored)))
    return receipts


def _design_obligations(current: _DesignSubjects) -> dict[str, str]:
    return {'strategy': current.strategy_subject_sha256,
            **{str(key): value for key, value in current.chapter_subject_sha256.items()}}


def _design_inheritance_sources(
    record: _ApprovalRecordV8, current: _DesignSubjects,
) -> dict[str, review_inheritance.DecisionReceipt]:
    if current.binding_infeasibility is not None:
        return {}
    return review_inheritance.accepted_sources(_design_inheritance_history(record), _design_obligations(current))


def _require_design_inheritance(
    record: _ApprovalRecordV8, current: _DesignSubjects, request: DesignReviewRequest,
) -> None:
    units = {'strategy': _review_decision_unit(request.strategy, current.strategy_subject_sha256)}
    units.update({str(row.output_ordinal): _review_decision_unit(
        row, current.chapter_subject_sha256.get(row.output_ordinal, '')) for row in request.chapters})
    if not any(unit.source_decision_sha256 for unit in units.values()):
        return
    if current.binding_infeasibility is not None:
        raise HumanEditProtected('当前不可行审阅义务不得继承接受')
    review_inheritance.validate_submission(
        _design_inheritance_history(record), _design_obligations(current), units)


#: metric 行的 finding 超过这个长度就只保留摘要：整书逐章 JSON 已按章并入
#: coverage[].reading.structure_metrics，再在 detail 与 refs 各抄一份只多花 token。
_METRIC_INLINE_CHARS = 300


def _compact(value: Any) -> str:
    """工作包的机器可比细节；不用自由文字二次解释判定。"""
    return json.dumps(
        value, ensure_ascii=False, sort_keys=True, separators=(',', ':'))


def _phase_deferred_rule_rows() -> list[Any]:
    """Context 相延后的规则只从生产 R-6 注册表派生。"""
    return [
        rule for rule in REGISTRY.of_stage('stage4')
        if rule.requirement.startswith('R-6.')
    ]


def _phase_deferred_items(
    *, report_rules: dict[str, Any] | None = None,
) -> list[WorkpackItem]:
    rows: list[WorkpackItem] = []
    for rule in _phase_deferred_rule_rows():
        line = (report_rules or {}).get(rule.rule)
        status = line.status if line is not None else 'unver'
        detail = (
            line.why or line.item
            if line is not None
            else f'{rule.item}：只在 stage4.context 产物在场后判定'
        )
        rows.append(WorkpackItem(
            code=rule.rule,
            rule=rule.rule,
            sev=rule.sev,
            status=status,
            repair_owner=rule.owner,
            detail=detail,
            effect='phase_deferred',
            allowed_actions=[],
        ))
    return rows


def _design_preflight_actions(
    current: _DesignSubjects, *, owner: str, ordinal: int | None,
) -> list[str]:
    """只发布 typed A 决定真能表达的路；Stage2/code owner 不伪造快捷修复。"""
    if owner != 'stage4.design':
        return []
    available = {
        row.output_ordinal
        for row in current.repair_capability.chapters
        if (
            bool(row.agent_repair_available)
            or bool(row.full_chapter_edit_available)
            or bool(row.request_repair_available)
        )
    }
    if ordinal is None:
        return ['request_repair', 'request_replan'] if available \
            else ['request_replan']
    return ['request_repair', 'request_replan'] \
        if ordinal in available else ['request_replan']


def _design_workpack_groups(
    record: _ApprovalRecordV8, current: _DesignSubjects, novel_dir: Path,
) -> list[WorkpackGroup]:
    inherited = _design_inheritance_sources(record, current)
    blocking: list[WorkpackItem] = []
    attention: list[WorkpackItem] = []
    disclosure: list[WorkpackItem] = []
    metrics: list[WorkpackItem] = []


    preflight = current.preflight
    if preflight is None:
        blocking.append(WorkpackItem(
            code='design_preflight_unavailable',
            rule=None,
            sev='hard',
            status='unver',
            repair_owner='stage4.design',
            detail='A→formal 生产预检无法产出；不得把未检冒充为通过',
            effect='block_b',
            allowed_actions=[],
        ))
    elif preflight.status == 'blocked':
        for index, failure in enumerate(preflight.failures, start=1):
            blocking.append(WorkpackItem(
                code=f'design_preflight:{failure.code}:{index}',
                rule=failure.rule,
                sev='hard',
                status='hit',
                chapter_ordinal=failure.chapter_ordinal,
                scene_refs=list(failure.scene_refs),
                refs=list(failure.refs),
                repair_owner=failure.repair_owner,
                detail=failure.detail or (
                    f'{failure.code}：owner={failure.repair_owner}'),
                effect='block_b',
                allowed_actions=_design_preflight_actions(
                    current, owner=failure.repair_owner,
                    ordinal=failure.chapter_ordinal),
            ))
        # 不可行 proof 是代码在关闭目录上铸出的第二种阻断面；
        # 它可以没有 TypedFailure，所以必须单独全量投影。
        for index, witness in enumerate(preflight.infeasibility, start=1):
            blocking.append(WorkpackItem(
                code=f'design_infeasible:{witness.constraint_code}:{index}',
                rule=None,
                sev='hard',
                status='hit',
                chapter_ordinal=witness.chapter_ordinal,
                scene_refs=list(witness.scenes),
                refs=list(witness.closed_refs),
                repair_owner='stage4.design',
                detail=_compact(witness.model_dump(mode='json')),
                effect='block_b',
                allowed_actions=_design_preflight_actions(
                    current, owner='stage4.design',
                    ordinal=witness.chapter_ordinal),
            ))

    if current.binding_infeasibility is not None:
        for index, witness in enumerate(
            current.binding_infeasibility.infeasibility_proof, start=1,
        ):
            blocking.append(WorkpackItem(
                code=f'current_binding_infeasible:{witness.constraint_code}:{index}',
                rule=None,
                sev='hard',
                status='hit',
                chapter_ordinal=witness.chapter_ordinal,
                scene_refs=list(witness.scenes),
                refs=list(witness.closed_refs),
                repair_owner='stage4.design',
                detail=_compact(witness.model_dump(mode='json')),
                effect='block_b',
                allowed_actions=_design_preflight_actions(
                    current, owner='stage4.design',
                    ordinal=witness.chapter_ordinal),
            ))

    # 一份当前 repair 决定里的「哪几章、哪些 scene/alias」是 A 面
    # 最应前置的人工上下文。它只做当前态投影，不执行 repair。
    design_view = _design_review_view(novel_dir, record, current=current)
    if (
        design_view.state == 'repair_requested'
        and design_view.current_decision is not None
    ):
        decision = design_view.current_decision
        for row in decision.chapters:
            if row.decision != 'request_repair':
                continue
            scope = _chapter_constraint_scope(record, decision, row)
            capability = next(
                item for item in decision.repair_capability.chapters
                if item.output_ordinal == row.output_ordinal)
            review_token = (
                _design_chapter_edit_review_token(decision, row, scope)
                if decision.repair_capability.is_v3
                and capability.full_chapter_edit_available
                else None
            )
            current_constraints = (
                scope.accumulated_constraints if scope.is_v3
                else scope.constraints
            )
            blocking.append(WorkpackItem(
                code=f'design_repair_requested:ch{row.output_ordinal}',
                rule=None,
                chapter_ordinal=row.output_ordinal,
                scene_refs=sorted({
                    scene for constraint in current_constraints
                    for scene in constraint.scene_refs
                }),
                refs=[
                    alias for constraint in current_constraints
                    for alias in constraint.opaque_aliases
                ],
                repair_owner='stage4.design',
                detail=_compact({
                    'new_constraints': [
                        value.model_dump(mode='json')
                        for value in row.constraints
                    ],
                    'unclosed_constraints': [
                        value.model_dump(mode='json')
                        for value in current_constraints
                    ],
                }),
                effect='block_b',
                allowed_actions=[],
                review_token=review_token,
            ))
    elif (
        design_view.state == 'strategy_replan_requested'
        and design_view.current_decision is not None
        and design_view.current_decision.repair_capability.is_v3
    ):
        decision = design_view.current_decision
        feedback = _strategy_replan_feedback(record, decision)
        blocking.append(WorkpackItem(
            code='design_strategy_replan_requested',
            rule=None,
            repair_owner='stage4.design',
            detail=_compact({
                'decision_sha256': decision.decision_sha256,
                'new_constraints': [
                    row.model_dump(mode='json')
                    for row in feedback.new_constraints
                ],
                'accumulated_constraints': [
                    row.model_dump(mode='json')
                    for row in feedback.accumulated_constraints
                ],
                'constraint_set_sha256': feedback.constraint_set_sha256,
                'accepted_chapters': [
                    row.model_dump(mode='json')
                    for row in feedback.accepted_chapters
                ],
                'chapter_failures': [
                    row.model_dump(mode='json')
                    for row in feedback.chapter_failures
                ],
            }),
            effect='block_b',
            allowed_actions=[],
        ))

    for subject in current.chapters:
        chapter = subject.chapter
        truth = subject.blind_truth
        ordinal = chapter.output_ordinal
        # 完整材料（facts 表、goal/边/关系/状态的 claim、逐场 alias）只在
        # coverage 行的 reading 出现一次；这里只留定位摘要，不再重复整章 JSON。
        attention.append(WorkpackItem(
            code=f'design_semantic_focus:ch{ordinal}',
            rule=None,
            chapter_ordinal=ordinal,
            scene_refs=[scene.candidate_scene_id for scene in chapter.scenes],
            refs=[
                *truth.entry_state_aliases,
                *truth.carry_out_aliases,
                *(endpoint.endpoint_alias for endpoint in truth.endpoints),
            ],
            detail=_compact({
                'entry_state_aliases': truth.entry_state_aliases,
                'carry_out_aliases': truth.carry_out_aliases,
                'beat_count': len(truth.beats),
                'constraint_count': len(truth.constraints),
                'endpoint_count': len(truth.endpoints),
                'scene_count': len(chapter.scenes),
                'reading': f'design_chapter_coverage:ch{ordinal}',
            }),
            effect='attention',
            allowed_actions=[],
        ))
        metrics.append(WorkpackItem(
            code=f'design_structure_metrics:ch{ordinal}',
            rule=None,
            sev='metric',
            status='ok',
            chapter_ordinal=ordinal,
            refs=[],
            detail=_compact(subject.structure_evidence.model_dump(mode='json')),
            effect='read_only',
            allowed_actions=[],
        ))

    capability = {
        row.output_ordinal: (
            bool(row.agent_repair_available)
            or bool(row.full_chapter_edit_available)
            or bool(row.request_repair_available)
        )
        for row in current.repair_capability.chapters
    }
    from novel.stages.stage4.reading import design_chapter_reading
    coverage: list[WorkpackItem] = []
    for subject in current.chapters:
        ordinal = subject.chapter.output_ordinal
        source = (
            None if current.binding_infeasibility is not None
            else _accepted_design_chapter_source(
                record, subject, current.chapter_subject_sha256[ordinal], sources=inherited)
        )
        reusable = source is not None
        actions = ['accept']
        if capability[ordinal]:
            actions.append('request_repair')
        coverage.append(WorkpackItem(
            code=f'design_chapter_coverage:ch{ordinal}',
            rule=None,
            chapter_ordinal=ordinal,
            scene_refs=[
                scene.candidate_scene_id for scene in subject.chapter.scenes
            ],
            refs=[],
            detail=_compact({
                'weight': subject.chapter.weight,
                'structure_decision': subject.chapter.structure_decision.model_dump(
                    mode='json'),
                'chapter_goal': subject.chapter.chapter_goal.model_dump(mode='json'),
                'scene_count': len(subject.chapter.scenes),
                'computed_change_kinds': (
                    subject.structure_evidence.computed_change_kinds),
            }),
            effect='coverage',
            allowed_actions=actions,
            changed=not reusable,
            historical_accept_reusable=reusable,
            source_decision_sha256=(
                source[0].decision_sha256 if source is not None else None),
            reading=design_chapter_reading(subject),
        ))
    coverage.sort(key=lambda item: (not item.changed, item.chapter_ordinal or 0))

    return [
        WorkpackGroup(
            group='blocking',
            scanned=(
                'A→formal 生产预检、当前 B 不可行 proof '
                f'与 {len(current.chapters)} 章 typed repair 状态'),
            items=blocking,
        ),
        WorkpackGroup(
            group='semantic_attention',
            scanned=f'{len(current.chapters)} 章 entry/end/因果/时态面',
            items=attention,
        ),
        WorkpackGroup(
            group='disclosure',
            scanned=(
                'Stage4A blind 主体不披露 formal id；'
                f'冻结兼容披露 {len(disclosure)} 条'),
            items=disclosure,
        ),
        WorkpackGroup(
            group='metric_only',
            scanned=f'{len(current.chapters)} 章生产结构量尺',
            items=metrics,
        ),
        WorkpackGroup(
            group='phase_deferred',
            scanned='contracts.rules 中 requirement=R-6.* 的 Stage4 规则',
            items=_phase_deferred_items(),
        ),
        WorkpackGroup(
            group='coverage',
            scanned=f'{len(current.chapters)}/{len(current.chapters)} 章',
            items=coverage,
        ),
    ]


def _design_workpack_reading(current: _DesignSubjects) -> WorkpackReading:
    """顶层一次：字段说明 + 本次整书策略主体（章权重属于策略，不属于章稿）。"""
    from novel.stages.stage4.reading import workpack_legend
    return WorkpackReading(
        legend=workpack_legend('design'),
        reconstruction_strategy=current.strategy.reconstruction_strategy,
        chapter_weights=[
            row.model_dump(mode='json') for row in current.strategy.chapter_weights
        ],
    )


def design_workpack(
    base: str | os.PathLike[str], sid: str,
    *, overview: NovelOverview | None = None, code: str | None = None,
) -> ApprovalWorkpack:
    """只读生成 A 审阅工作包；可复用 operator 的冻结坐标。"""
    _validate_read_projection(sid, overview=overview, code=code)
    with _read_projection(overview=overview, code=code):
        with _LOCK:
            record = _read_current(base, sid)
            novel_dir = paths.resolve_novel_dir(base, sid)
            current = _current_design_subjects(novel_dir)
            ordinals = [row.chapter.output_ordinal for row in current.chapters]
            workpack = ApprovalWorkpack(
                sid=sid,
                kind='design',
                candidate_sha256=current.candidate_sha256,
                subject_sha256=current.aggregate_subject_sha256,
                review_token=_design_review_token(current.aggregate_subject_sha256),
                chapter_ordinals=ordinals,
                groups=_design_workpack_groups(record, current, novel_dir),
                disposition='review_required',
                reading=_design_workpack_reading(current),
            )
            # 复用首读 subject；末尾第二读仍是唯一竞态守卫。若在这里再读一次，
            # 会把「前后两次一致才返回」悄悄扩成无守卫的第三份投影。
            view = _design_review_view(novel_dir, record, current=current)
            if (
                view.state == 'accepted'
                and view.current_decision is not None
            ):
                workpack = workpack.model_copy(update={
                    'disposition': 'already_current',
                    'current_decision_sha256': view.current_decision.decision_sha256,
                })
            if _current_design_subjects(novel_dir) != current:
                raise ArtifactUnreadable(
                    'Stage4A 审阅主体在工作包构造期间变化；请刷新后重试')
            return workpack


def _report_item(
    line: Any, *, effect: str, index: int = 1,
    detail: str | None = None, refs: list[str] | None = None,
    chapter_ordinal: int | None = None,
    allowed_actions: list[str] | None = None,
) -> WorkpackItem:
    finding_row = line.findings[index - 1] if len(line.findings) >= index else None
    return WorkpackItem(
        code=f'{line.rule}:{index}',
        rule=line.rule,
        sev=line.sev,
        status=line.status,
        chapter_ordinal=(
            chapter_ordinal if chapter_ordinal is not None
            else finding_row.output_ordinal if finding_row is not None else None
        ),
        refs=(
            list(refs) if refs is not None
            else list(finding_row.evidence) if finding_row is not None else []
        ),
        repair_owner=(
            str(finding_row.owner) if finding_row is not None
            and finding_row.owner is not None
            else REGISTRY.get(line.rule).owner
        ),
        detail=(
            detail or (
                finding_row.detail if finding_row is not None
                else line.why or line.item
            )
        ),
        effect=effect,
        allowed_actions=allowed_actions or [],
    )


def _accepted_structure_chapter_sources(
    record: _ApprovalRecordV8, novel_dir: Path, subject_sha256: str,
    chapter_subject_sha256: dict[int, str],
) -> dict[int, str]:
    """按实际审阅依赖找有效来源；没有逐章快照的旧决定不能用于局部继承。"""
    return {int(key): source.decision_sha256 for key, source in
            review_inheritance.accepted_sources(
                _structure_inheritance_history(record),
                {str(key): value for key, value in chapter_subject_sha256.items()}).items()}


def _structure_inheritance_history(record: _ApprovalRecordV8) -> list[review_inheritance.DecisionReceipt]:
    return [review_inheritance.DecisionReceipt(
        stored.proof.decision_sha256,
        {str(row.output_ordinal): _review_decision_unit(
            row, stored.chapter_subject_sha256.get(row.output_ordinal, f'whole:{stored.subject_sha256}'))
            for row in stored.chapters},
        stored.proof.decision_sha256 == _decision_sha256(_structure_decision_payload(stored)))
        for stored in record.structure_history]


def _require_structure_inheritance(
    record: _ApprovalRecordV8, chapter_subjects: dict[int, str], request: StructureApprovalRequest,
) -> None:
    if not any(row.source_decision_sha256 for row in request.chapters):
        return
    review_inheritance.validate_submission(
        _structure_inheritance_history(record),
        {str(key): value for key, value in chapter_subjects.items()},
        {str(row.output_ordinal): _review_decision_unit(
            row, chapter_subjects.get(row.output_ordinal, '')) for row in request.chapters})


def _baseline_signature_items(
    *, report_rules: dict[str, Any],
) -> list[WorkpackItem]:
    """直接投影生产 INFO finding；不在工作包里重算第二份判据。"""
    line = report_rules.get('stage_signature_conflict')
    if line is None:
        raise ArtifactUnreadable(
            'stage_signature_conflict 已注册但生产复检未返回该行')
    items: list[WorkpackItem] = []
    for index, finding_row in enumerate(line.findings, start=1):
        refs = list(finding_row.evidence)
        digest = _decision_sha256({
            'output_ordinal': finding_row.output_ordinal,
            'detail': finding_row.detail,
            'evidence': refs,
        })[:16]
        items.append(WorkpackItem(
            code=f'stage_signature_conflict:{index}:{digest}',
            rule='stage_signature_conflict',
            sev=line.sev,
            status=line.status,
            chapter_ordinal=finding_row.output_ordinal,
            scene_refs=refs[:1],
            refs=refs,
            repair_owner=(
                str(finding_row.owner)
                if finding_row.owner is not None else REGISTRY.get(line.rule).owner
            ),
            detail=finding_row.detail,
            effect='disclose',
            allowed_actions=[],
        ))
    return items


def _structure_workpack_groups(
    record: _ApprovalRecordV8, novel_dir: Path, *, subject_sha256: str,
    chapter_subject_sha256: dict[int, str],
    plan: PlanChapters, concerns: list[BindingConcern], report: Any,
    blueprint_by_ordinal: Mapping[int, Mapping[str, Any]],
    chapter_materials: Mapping[int, PlotChapterReviewMaterials],
) -> list[WorkpackGroup]:
    from novel.stages.stage4.reading import structure_chapter_reading

    by_rule = {line.rule: line for line in report.rules}
    deferred_ids = {rule.rule for rule in _phase_deferred_rule_rows()}
    blocking: list[WorkpackItem] = []
    attention: list[WorkpackItem] = []
    disclosure: list[WorkpackItem] = []
    metrics: list[WorkpackItem] = []

    semantic_line = by_rule.get('semantic_closure_reading')
    semantic_finding = (
        semantic_line.findings[0]
        if semantic_line is not None and semantic_line.findings else None
    )

    for line in report.rules:
        if line.rule in deferred_ids or line.rule == 'stage_signature_conflict':
            continue
        if line.sev == 'metric':
            if line.findings:
                for index in range(1, len(line.findings) + 1):
                    finding_row = line.findings[index - 1]
                    if len(str(finding_row.detail)) <= _METRIC_INLINE_CHARS:
                        metrics.append(
                            _report_item(line, effect='read_only', index=index))
                        continue
                    # 整书逐章 JSON 不再在 detail 与 refs 各抄一份：完整读数已按章
                    # 并入 coverage[].reading.structure_metrics（`R-5.25`）。
                    metrics.append(_report_item(
                        line, effect='read_only', index=index,
                        detail=(
                            f'{line.rule}：整书逐章读数共 '
                            f'{len(str(finding_row.detail))} 字，已按章并入 '
                            'coverage[].reading.structure_metrics；本行只作摘要'),
                        refs=[]))
            else:
                metrics.append(_report_item(line, effect='read_only'))
            continue
        if line.status == 'hit' and line.sev == 'hard':
            blocking.extend(
                _report_item(
                    line, effect='block_context', index=index,
                    allowed_actions=['request_replan'],
                )
                for index in range(1, len(line.findings) + 1)
            )
            continue
        if line.status in ('unver', 'skipped'):
            if line.rule == 'presentation_semantics':
                detail = _compact({
                    'why': line.why,
                    # 直接保留生产 metric finding，不在 façade 另解码、重组第二份判据。
                    'semantic_closure_reading': (
                        semantic_finding.detail
                        if semantic_finding is not None else None
                    ),
                })
                attention.append(_report_item(
                    line, effect='attention', detail=detail,
                    refs=(
                        [str(value) for value in semantic_finding.evidence]
                        if semantic_finding is not None else []
                    ),
                ))
            else:
                attention.append(_report_item(line, effect='attention'))
            continue
        if line.status == 'hit' and line.sev == 'info':
            disclosure.extend(
                _report_item(line, effect='disclose', index=index)
                for index in range(1, len(line.findings) + 1)
            )

    signature_items = _baseline_signature_items(report_rules=by_rule)
    disclosure.extend(signature_items)
    for index, concern in enumerate(concerns, start=1):
        disclosure.append(WorkpackItem(
            code=f'binding_concern:{concern.constraint_code}:{index}',
            rule=None,
            sev='info',
            status='hit',
            chapter_ordinal=concern.chapter_ordinal,
            scene_refs=list(concern.scenes),
            refs=list(concern.closed_refs),
            repair_owner=None,
            detail=concern.reason,
            effect='disclose',
            allowed_actions=[],
        ))

    accepted_sources = _accepted_structure_chapter_sources(
        record, novel_dir, subject_sha256, chapter_subject_sha256)
    source_rulings = {(stored.proof.decision_sha256, row.output_ordinal): row
                     for stored in record.structure_history for row in stored.chapters}
    coverage: list[WorkpackItem] = []
    for chapter in plan.output_chapters:
        ordinal = chapter.output_ordinal
        source_sha = accepted_sources.get(ordinal)
        reading = structure_chapter_reading(
            chapter,
            materials=chapter_materials[ordinal],
            concerns=concerns,
            signature_conflict_codes=[
                item.code for item in signature_items
                if item.chapter_ordinal == ordinal
            ])
        coverage.append(WorkpackItem(
            code=f'structure_chapter_coverage:ch{ordinal}',
            rule=None,
            chapter_ordinal=ordinal,
            scene_refs=[scene.slot for scene in chapter.scenes],
            refs=[],
            detail=_compact({
                'structure_decision': chapter.structure_decision.model_dump(
                    mode='json'),
                'chapter_goal': chapter.chapter_goal.model_dump(mode='json'),
                'open_hook_goal': chapter.open_hook_goal.model_dump(mode='json'),
                'end_hook_goal': chapter.end_hook_goal.model_dump(mode='json'),
                'scene_count': len(chapter.scenes),
                'budget_chars': sum(scene.budget_chars for scene in chapter.scenes),
            }),
            effect='coverage',
            allowed_actions=['accept', 'request_replan'],
            changed=source_sha is None,
            historical_accept_reusable=source_sha is not None,
            source_decision_sha256=source_sha,
            inherited_structure_decision=(
                source_rulings[(source_sha, ordinal)].model_copy(update={
                    'source_decision_sha256': source_sha}) if source_sha is not None else None),
            reading=reading,
        ))
    coverage.sort(key=lambda item: (not item.changed, item.chapter_ordinal or 0))

    return [
        WorkpackGroup(
            group='blocking',
            scanned=f'{len(report.rules)} 条 stage4.plan 生产复检规则',
            items=blocking,
        ),
        WorkpackGroup(
            group='semantic_attention',
            scanned=(
                '生产复检 UNVER/skipped；非线性状态未知取 '
                'semantic_closure_reading 全量证据'),
            items=attention,
        ),
        WorkpackGroup(
            group='disclosure',
            scanned=(
                f'{len(concerns)} 条 bound B concern，'
                f'{len(signature_items)} 条机械舞台签名冲突，'
                '以及全部 INFO 生产结论'),
            items=disclosure,
        ),
        WorkpackGroup(
            group='metric_only',
            scanned='全部 Stage4 生产 metric 读数',
            items=metrics,
        ),
        WorkpackGroup(
            group='phase_deferred',
            scanned='contracts.rules 中 requirement=R-6.* 的 Stage4 规则',
            items=_phase_deferred_items(report_rules=by_rule),
        ),
        WorkpackGroup(
            group='coverage',
            scanned=f'{len(plan.output_chapters)}/{len(plan.output_chapters)} 章',
            items=coverage,
        ),
    ]


def structure_workpack(
    base: str | os.PathLike[str], sid: str, *,
    overview: NovelOverview | None = None, code: str | None = None,
    check_report: CheckReport | None = None,
) -> ApprovalWorkpack:
    """只读生成 B 结构审批工作包；subject 不含 Context 相延后规则。

    operator 的组合快照可以同时传入其 overview/code/Stage4 报告，避免
    Inbox 再跑一遍同一机械判据。预计算报告必须与坐标成套且明确属于本书、
    本层、本节点；普通 API/CLI 不传时仍现场 fresh 复检，公共旧调用不变。
    """
    _validate_read_projection(sid, overview=overview, code=code)
    if check_report is not None:
        if overview is None:
            raise ValueError('预计算 Stage4 报告必须随 overview/code 坐标提供')
        if (
            check_report.sid != sid
            or check_report.stage != 'stage4'
            or check_report.node != 'stage4.plan'
        ):
            raise ValueError(
                '预计算报告必须属于当前 sid 的 stage4.plan 复检')
    with _read_projection(overview=overview, code=code):
        with _LOCK:
            record = _read_current(base, sid)
            novel_dir = paths.resolve_novel_dir(base, sid)
            snapshot = _structure_workpack_snapshot(novel_dir, with_materials=True)
            if check_report is None:
                from novel.stages.stage4.plan import run_check_only
                report = run_check_only(state.readonly_ctx(
                    novel_dir, 'stage4.plan', _projection_code()))
            else:
                report = check_report
            # view 的读取也必须落在首尾快照之间；放到末读之后会留下一个
            # 未经 rebound 校验的第三次整读，返回的处置态可能与 groups 混版。
            view = _structure_view(novel_dir, record)
            after = _structure_workpack_snapshot(novel_dir, with_materials=True)
            if after != snapshot:
                raise ArtifactUnreadable(
                    'Stage4B 审阅主体在工作包复检期间变化；请刷新后重试')
            from novel.stages.stage4.reading import (
                concern_reading, workpack_legend,
            )
            blueprint_by_ordinal = {
                row.chapter_ordinal: row.model_dump(mode='json', by_alias=True)
                for row in snapshot.blueprints.blueprints
            }
            workpack = ApprovalWorkpack(
                sid=sid,
                kind='structure',
                candidate_sha256=None,
                subject_sha256=snapshot.subject_sha256,
                review_token=f's4sr1_{snapshot.subject_sha256}',
                chapter_ordinals=snapshot.chapter_ordinals,
                groups=_structure_workpack_groups(
                    record, novel_dir,
                    subject_sha256=snapshot.subject_sha256,
                    chapter_subject_sha256=snapshot.chapter_subject_sha256,
                    plan=snapshot.plan,
                    concerns=snapshot.concerns,
                    report=report,
                    blueprint_by_ordinal=blueprint_by_ordinal,
                    chapter_materials=snapshot.chapter_materials,
                ),
                reading=WorkpackReading(
                    legend={
                        **workpack_legend('structure'),
                        **({'binding_input_contract': (
                            snapshot.binding_input_contract
                            + '：本次章级 B 收到完整设计及剧情、证据与舞台的必要映射；'
                            '该真实输入契约参与本次结构审阅主体。')}
                           if snapshot.binding_input_contract is not None else {}),
                    },
                    global_binding_concerns=[
                        concern_reading(concern, blueprints=blueprint_by_ordinal)
                        for concern in snapshot.concerns
                        if concern.chapter_ordinal is None
                    ],
                ),
            )
            if view.state == 'accepted' and view.current_decision is not None:
                workpack = workpack.model_copy(update={
                    'disposition': 'already_current',
                    'current_decision_sha256': _decision_sha256(
                        _structure_decision_payload(view.current_decision)),
                })
            return workpack


_DESIGN_STRATEGY_TEMPLATE_REASON = \
    '关闭式模板：尚未确认当前整书策略，保持 request_replan'
_DESIGN_CHAPTER_TEMPLATE_REASON = \
    '关闭式模板：尚未确认当前章设计，保持 request_repair'
_DESIGN_CHAPTER_UNREPAIRABLE_TEMPLATE_REASON = \
    '关闭式模板：当前章不能执行 targeted repair，须确认接受或改走策略 replan'
def _design_chapter_template_constraints(
    subject: DesignChapterSubject,
) -> list[DesignRepairConstraint]:
    """未审章的默认修复脚手架（批次 2）。

    `structure_delta` 退役后不再作 sentinel：泛化重排指令可以不带任何
    scene/alias 证据提交，等于允许「重排一下」式的不可执行返修。默认脚手架
    改为带真实 scene/alias 的 `scene_goal`，操作者必须逐项改写或显式清除。
    """
    scene = next(
        (scene.candidate_scene_id for scene in subject.chapter.scenes), None)
    alias = next(
        (beat.alias for beat in subject.blind_truth.beats), None)
    if scene is None or alias is None:
        return []
    return [DesignRepairConstraint(
        code='scene_goal', scene_refs=[scene], opaque_aliases=[alias])]


def _design_template_token(
    current: _DesignSubjects, *, kind: Literal['strategy', 'chapter'],
    subject_sha256: str, output_ordinal: int | None = None,
) -> str:
    """未审模板行的 typed marker；不是权限票，也不进入正式决定。"""
    return _decision_sha256({
        'kind': 'stage4_design_template_placeholder',
        'aggregate_subject_sha256': current.aggregate_subject_sha256,
        'row_kind': kind,
        'output_ordinal': output_ordinal,
        'subject_sha256': subject_sha256,
    })


def design_template(
    base: str | os.PathLike[str], sid: str, *,
    actor: str, reviewer_kind: str, review_run_id: str, reason: str,
    overview: NovelOverview | None = None, code: str | None = None,
) -> DesignReviewTemplate:
    """生成当前全书 A 审阅模板；仅复用逐项仍完全相同的历史 accept。"""
    _validate_read_projection(sid, overview=overview, code=code)
    with _read_projection(overview=overview, code=code), _LOCK:
        record = _read_current(base, sid)
        if not record.gate['s2']:
            raise ApprovalRequired('Stage4A design review requires current gate.s2')
        novel_dir = paths.resolve_novel_dir(base, sid)
        current = _current_design_subjects(novel_dir)
        # fresh B 不可行证明改变了本次 A 人审主体；旧 accept 不能替人读过它。
        inherited = _design_inheritance_sources(record, current)
        strategy_source = (
            None if current.binding_infeasibility is not None
            else _accepted_design_strategy_source(record, current, sources=inherited)
        )
        strategy = DesignStrategyReview(
            subject=current.strategy,
            subject_sha256=current.strategy_subject_sha256,
            placeholder=strategy_source is None,
            template_token=(
                _design_template_token(
                    current, kind='strategy',
                    subject_sha256=current.strategy_subject_sha256)
                if strategy_source is None else None
            ),
            decision=(
                'accept'
                if strategy_source is not None
                else 'request_replan'
            ),
            reason=(
                inherited['strategy'].units['strategy'].payload['reason']
                if strategy_source is not None
                else _DESIGN_STRATEGY_TEMPLATE_REASON
            ),
            source_decision_sha256=(
                strategy_source.decision_sha256 if strategy_source else None),
            replan_constraints=(
                [] if strategy_source is not None else [
                    DesignStrategyReplanConstraint(
                        kind='replace_reconstruction_strategy',
                        subject_sha256=current.strategy_subject_sha256,
                    )
                ]
            ),
        )
        chapters: list[DesignChapterReview] = []
        repairable_by_ordinal = {
            row.output_ordinal: (
                bool(row.agent_repair_available)
                or bool(row.full_chapter_edit_available)
            )
            for row in current.repair_capability.chapters
        }
        for subject in current.chapters:
            ordinal = subject.chapter.output_ordinal
            subject_sha = current.chapter_subject_sha256[ordinal]
            source = (
                None if current.binding_infeasibility is not None
                else _accepted_design_chapter_source(
                    record, subject, subject_sha, sources=inherited)
            )
            can_repair = repairable_by_ordinal[ordinal]
            chapters.append(DesignChapterReview(
                output_ordinal=ordinal,
                subject=subject,
                subject_sha256=subject_sha,
                placeholder=source is None,
                template_token=(
                    _design_template_token(
                        current, kind='chapter', subject_sha256=subject_sha,
                        output_ordinal=ordinal)
                    if source is None else None
                ),
                decision=(
                    'accept' if source is not None
                    else 'accept' if not can_repair
                    else 'request_repair'
                ),
                reason=(
                    source[1].reason
                    if source is not None
                    else _DESIGN_CHAPTER_TEMPLATE_REASON
                    if can_repair
                    else _DESIGN_CHAPTER_UNREPAIRABLE_TEMPLATE_REASON
                ),
                constraints=(
                    [] if source is not None
                    else [] if not can_repair
                    else _design_chapter_template_constraints(subject)
                ),
                source_decision_sha256=(
                    source[0].decision_sha256 if source else None),
            ))
        return DesignReviewTemplate(
            review_token=_design_review_token(current.aggregate_subject_sha256),
            actor=actor,
            reviewer_kind=reviewer_kind,
            review_run_id=review_run_id,
            general_reason=reason,
            candidate_sha256=current.candidate_sha256,
            aggregate_subject_sha256=current.aggregate_subject_sha256,
            binding_infeasibility=current.binding_infeasibility,
            preflight=current.preflight,
            repair_capability=current.repair_capability,
            strategy=strategy,
            chapters=chapters,
            constraint_catalog=_design_constraint_catalog(record, current),
        )


def _require_design_template_materialized(request: DesignReviewMaterialized) -> None:
    """拒绝仅清 marker 后原样提交服务端的默认关闭式 payload。"""
    strategy = request.strategy
    if (
        strategy.source_decision_sha256 is None
        and strategy.decision == 'request_replan'
        and strategy.reason.strip() == _DESIGN_STRATEGY_TEMPLATE_REASON
    ):
        raise HumanEditProtected(
            'Stage4A 策略仍是未填写的默认 template sentinel；'
            '须给出真实决定或审阅理由')
    for row in request.chapters:
        default_repair = (
            row.source_decision_sha256 is None
            and row.decision == 'request_repair'
            and row.reason.strip() == _DESIGN_CHAPTER_TEMPLATE_REASON
            and [item.model_dump(mode='json') for item in row.constraints]
            == [
                item.model_dump(mode='json')
                for item in _design_chapter_template_constraints(row.subject)
            ]
        )
        default_unrepairable = (
            row.source_decision_sha256 is None
            and row.decision == 'accept'
            and row.reason.strip() == _DESIGN_CHAPTER_UNREPAIRABLE_TEMPLATE_REASON
            and not row.constraints
        )
        if default_repair or default_unrepairable:
            raise HumanEditProtected(
                f'Stage4A 第 {row.output_ordinal} 章仍是未填写的默认 '
                'template sentinel；须给出真实决定、理由或约束')


def _require_design_codes_active(
    request: DesignReviewMaterialized,
    effective: dict[int, list[DesignRepairConstraint]] | None = None,
) -> None:
    """新决定只接受 active 修复码；退役码历史可解码，但不再可提交（批次 2）。"""
    retired = [row.output_ordinal for row in request.chapters
               for constraint in (row.constraints if effective is None else effective[row.output_ordinal])
               if constraint.code in DESIGN_REPAIR_CODES_RETIRED]
    if retired:
        raise HumanEditProtected(
            f'Stage4A 第 {retired} 章使用了退役修复码 '
            f'{list(DESIGN_REPAIR_CODES_RETIRED)}；泛化重排指令已退场，'
            '请改用带 scene/alias 的具体修复码')


def _require_design_preflight_clear(
    request: DesignReviewMaterialized, current: _DesignSubjects,
) -> None:
    """批次 2：A-owned/上游/代码 witness 在场时不允许 accept 授权 B。

    `request_repair` / `request_replan` 是 remedy，不受此闸；被拦的是「带着
    已知 A 域失败把 A 盖章通过」。preflight 为 None（自身无法产出）同样
    fail-closed——B 会跑同一把尺子，预检跑不动的书 B 也跑不动。
    """
    accepts = [row.output_ordinal for row in request.chapters
               if row.decision == 'accept']
    if request.strategy.decision == 'accept':
        accepts.append(0)
    if not accepts:
        return
    preflight = current.preflight
    if preflight is None:
        raise HumanEditProtected(
            'A→formal 预检未能产出（上游缺失或读不回来）；'
            'accept 需要 pass 的预检，先收口上游再刷新模板')
    if preflight.status != 'blocked':
        return
    raise HumanEditProtected(
        f'A→formal 预检 blocked（owners={preflight.blocked_owners}, '
        f'codes={sorted({row.code for row in preflight.failures})}）；'
        '这些失败不是 B 能修的，accept 不能授权 B——'
        '按 owner 走 typed 修订/上游修复后刷新模板重审')


def _require_design_repair_capability(request: DesignReviewMaterialized) -> None:
    available = {
        row.output_ordinal
        for row in request.repair_capability.chapters
        if (
            bool(row.agent_repair_available)
            or bool(row.full_chapter_edit_available)
        )
    }
    unavailable = [
        row.output_ordinal for row in request.chapters
        if row.decision == 'request_repair' and row.output_ordinal not in available
    ]
    if unavailable:
        raise HumanEditProtected(
            f'Stage4A request_repair 章 {unavailable} 既无可执行 agent lineage，'
            '也不能走零 wire 完整章替换')


def _require_design_constraint_refs(
    request: DesignReviewMaterialized, current: _DesignSubjects | None = None,
    effective: dict[int, list[DesignRepairConstraint]] | None = None,
) -> None:
    for row in request.chapters:
        scenes = {
            scene.candidate_scene_id for scene in row.subject.chapter.scenes
        }
        truth = row.subject.blind_truth
        beat_aliases = {beat.alias for beat in truth.beats}
        semantic_aliases = {c.alias for c in truth.constraints} | {
            endpoint.endpoint_alias for endpoint in truth.endpoints}
        aliases = beat_aliases | semantic_aliases | {
            person.alias for person in truth.characters
        } | {state.alias for state in (*truth.states, *truth.inherited_states)} | {
            subject.alias for subject in truth.global_subjects}
        for constraint in (row.constraints if effective is None else effective[row.output_ordinal]):
            unknown_scenes = [
                ref for ref in constraint.scene_refs if ref not in scenes
            ]
            unknown_aliases = [
                ref for ref in constraint.opaque_aliases if ref not in aliases
            ]
            if unknown_scenes or unknown_aliases:
                raise HumanEditProtected(
                    f'Stage4A 第 {row.output_ordinal} 章 constraint 越界：'
                    f'scenes={unknown_scenes}, aliases={unknown_aliases}')
            if constraint.code == 'semantic_constraint' and not set(
                    constraint.opaque_aliases) <= semantic_aliases:
                raise HumanEditProtected('语义修复只能引用本章已登记约束或端点')
            if constraint.code in {'entry_anchor', 'ending_anchor', 'presentation_grouping',
                                   'disclosure_order', 'noncontiguous_regroup'} and not set(
                    constraint.opaque_aliases) <= beat_aliases:
                raise HumanEditProtected('呈现修复只能引用本章完整剧情节点')

    # 批次 2：`binding_constraint_unsat` 是「B 失败升级为约束不可满足」的唯一
    # 合法通道，必须由机械 witness 支撑——代码铸造的预检不可行证明，或 fresh B
    # infeasible 正式终态。没有 witness 时 B 失败不得升级。
    if current is not None:
        witness_present = bool(
            (current.binding_infeasibility is not None
             and current.binding_infeasibility.infeasibility_proof)
            or (current.preflight.infeasibility if current.preflight else [])
        )
        if not witness_present:
            offenders = [
                row.output_ordinal for row in request.chapters
                for constraint in (row.constraints if effective is None else effective[row.output_ordinal])
                if constraint.code == 'binding_constraint_unsat'
            ]
            if offenders:
                raise HumanEditProtected(
                    f'Stage4A 第 {offenders} 章 binding_constraint_unsat 缺机械 '
                    'witness：预检构造性绑定可行且无 fresh B infeasible 终态；'
                    '不能把 B 失败升级为约束不可满足')


def _stage4_close_review_request(
    base: str | os.PathLike[str], sid: str, *, node: str,
) -> DesignReviewRequest | StructureApprovalRequest:
    """构造仅含有效历史决定的提交；typed 校验与锁内复验仍走正式 approve。"""
    if node == 'stage4.design':
        template = design_template(
            base, sid, actor='system:stage4-close-review', reviewer_kind='inherited',
            review_run_id='inherited-review', reason='当前全部义务继承有效历史接受')
        if template.strategy.source_decision_sha256 is None or any(
                row.source_decision_sha256 is None for row in template.chapters):
            raise ApprovalRequired('Stage4A 仍有待审义务，不能免费闭合')
        if template.preflight is None or template.preflight.status == 'blocked':
            raise ApprovalRequired('Stage4A 当前机械前置未通过，不能免费闭合')
        record = _read_current(base, sid)
        latest = record.design_decision
        if latest is not None and _stored_design_external_payload(latest)['review_token'] == template.review_token:
            return DesignReviewRequest.model_validate(_stored_design_external_payload(latest))
        request = DesignReviewRequest.model_validate(template, from_attributes=True)
        request.review_run_id = f'inherited-{template.review_token[-24:]}'
        return request
    if node == 'stage4.plan':
        request = structure_template(
            base, sid, actor='system:stage4-close-review', reason='当前全部结构义务继承有效历史接受')
        if any(row.source_decision_sha256 is None for row in request.chapters):
            raise ApprovalRequired('Stage4B 仍有待审义务，不能免费闭合')
        record = _read_current(base, sid)
        latest = record.structure_decision
        if latest is not None and request.review_token == f's4sr1_{latest.subject_sha256}':
            return StructureApprovalRequest.model_validate(_public_structure_decision(latest), from_attributes=True)
        return request
    raise ApprovalRequired('免费 Stage4 审阅收口只属于 design/plan')


def stage4_review_closure_available(
    base: str | os.PathLike[str], sid: str, *, node: str,
) -> bool:
    """只读决定是否只剩历史覆盖汇合；unknown 不得降为未命中。"""
    with _LOCK:
        try:
            record = _read_current(base, sid)
            book = paths.resolve_novel_dir(base, sid)
            view = (_design_review_view(book, record) if node == 'stage4.design'
                    else _structure_view(book, record))
            if view.state == 'accepted':
                return False
            _stage4_close_review_request(base, sid, node=node)
        except (ApprovalRequired, UpstreamMissing):
            return False
        return True


def close_stage4_review(
    base: str | os.PathLike[str], sid: str, *, node: str,
) -> ApprovalDoc:
    request = _stage4_close_review_request(base, sid, node=node)
    if isinstance(request, DesignReviewRequest):
        return approve_design(base, sid, request)
    return approve_structure(base, sid, request)


def approve_design(
    base: str | os.PathLike[str], sid: str, request: DesignReviewRequest,
) -> ApprovalDoc:
    """原子保存当前完整 A 盲审；不启动修订、B 或任何付费作业。"""
    with approval_file_lock(base, sid):
        raw, record = _read_state(base, sid)
        if not record.gate['s2']:
            raise ApprovalRequired('Stage4A design review requires current gate.s2')
        novel_dir = paths.resolve_novel_dir(base, sid)
        current = _current_design_subjects(novel_dir)
        prior_design = _design_review_view(
            novel_dir, record, current=current)
        prior_structure_state = _structure_view(novel_dir, record).state
        materialized = _materialize_design_request(request, current)
        _require_design_template_materialized(materialized)
        _require_design_repair_capability(materialized)
        _require_design_preflight_clear(materialized, current)
        payload = _slim_design_payload(request, current)
        digest = _decision_sha256(payload)
        artifacts = [STAGE4_BLIND_CANDIDATE]
        if current.binding_infeasibility is not None:
            artifacts.append(STAGE4_EVIDENCE_BINDING)
        proof = _make_proof(novel_dir, artifacts, payload)

        after = _current_design_subjects(novel_dir)
        after_payload = _slim_design_payload(request, after)
        after_artifacts = [STAGE4_BLIND_CANDIDATE]
        if after.binding_infeasibility is not None:
            after_artifacts.append(STAGE4_EVIDENCE_BINDING)
        after_proof = _make_proof(novel_dir, after_artifacts, after_payload)
        after_structure_state = _structure_view(novel_dir, record).state
        if (
            current != after
            or proof != after_proof
            or prior_structure_state != after_structure_state
        ):
            raise HumanEditProtected(
                'Stage4A 候选或最终结构裁决在盲审提交期间发生变化；'
                '请刷新后重审')

        latest = raw.design_decision
        same_business_subject = bool(
            latest is not None
            and _stored_design_matches_business_subject(latest, current)
        )
        same_external_payload = bool(
            latest is not None
            and _stored_design_external_payload(latest)
            == request.model_dump(mode='json')
        )
        exact_replay = bool(
            prior_design.current_decision is not None
            and _design_decision_payload(prior_design.current_decision)
            == _design_decision_payload(materialized)
        )
        if prior_design.current_decision is not None:
            # 同一 typed 决定的逐字重放始终是幂等读，不形成第二张票，
            # 也不会改写随后出现的 B replan。只有不同 payload 才进入
            # resolution 例外或同主体改票拒绝。
            if exact_replay:
                _design_constraint_catalog(record, current)
                return _view(base, record)
            resolving_structure_replan = (
                prior_design.state == 'accepted'
                and prior_structure_state == 'replan_requested'
            )
            all_accept = (
                materialized.strategy.decision == 'accept'
                and all(
                    row.decision == 'accept'
                    for row in materialized.chapters
                )
            )
            if resolving_structure_replan:
                if all_accept:
                    raise HumanEditProtected(
                        '当前 Stage4A 已接受但最终结构要求 replan；'
                        '同主体 direct all-accept 不能关闭 resolution blocker')
                # 这是同一 A 主体唯一允许的新决定：把当前 B 的正式负结论
                # 解析成非 all-accept 的 typed A resolution。落盘后该决定成为
                # current，后续同主体又恢复为 exact-replay-only。
            else:
                raise HumanEditProtected(
                    '当前 Stage4A 主体已有 typed 决定；同主体只接受 exact replay')
        elif same_business_subject:
            if not same_external_payload:
                raise HumanEditProtected(
                    '当前 Stage4A 业务主体已有 typed 决定；proof 漂移时只接受'
                    '同一外部 payload 重绑，不能用旧 token 改票')
            # 人真正审阅的业务主体和完整 typed 回执均未变；只有服务端 proof
            # （通常是 provenance 文件 SHA）变化。保留旧历史，下面追加一条绑定
            # 当前完整字节的 slim 记录。A 重绑绝不改写现存 B 决定或 s4 gate。
        proof_only_rebind = bool(
            prior_design.current_decision is None
            and same_business_subject and same_external_payload)
        if proof_only_rebind:
            # 重绑不重新裁决义务，却仍须证明原票和整条来源链真实自洽。
            _design_constraint_catalog(record, current)
        else:
            _require_design_inheritance(record, current, request)
        effective = (
            None if proof_only_rebind
            else _effective_design_submission_constraints(record, materialized))
        _require_design_constraint_refs(materialized, current, effective)
        _require_design_codes_active(materialized, effective)
        stored = _StoredDesignDecisionSlim(
            request=request,
            candidate_sha256=current.candidate_sha256,
            aggregate_subject_sha256=current.aggregate_subject_sha256,
            strategy_subject_sha256=current.strategy_subject_sha256,
            strategy_epoch_sha256=current.strategy_epoch_sha256,
            chapter_subject_sha256=current.chapter_subject_sha256,
            repair_capability=current.repair_capability,
            binding_infeasibility=current.binding_infeasibility,
            decision_sha256=digest,
            decided_at=_now(),
            proof=proof,
        )
        return _write(base, raw.model_copy(update={
            'design_history': [*raw.design_history, stored],
            'design_decision': stored,
        }))


def design_review_is_current(
    base: str | os.PathLike[str], sid: str,
) -> bool:
    """B 最低边界的只读 bool；唯一真值是公开 view 的 ``accepted``。"""
    with _LOCK:
        record = _read_current(base, sid)
        novel_dir = paths.resolve_novel_dir(base, sid)
        return _design_review_view(novel_dir, record).state == 'accepted'


def authorize_design_initial(
    base: str | os.PathLike[str], sid: str,
) -> None:
    """授权默认 Stage4A 首次生成；候选一旦存在就只能走 typed 恢复动作。"""
    with _LOCK:
        novel_dir = paths.resolve_novel_dir(base, sid)
        # 候选/INVALID/freshness 的完整状态机属于 producer；服务入口只委托这一把
        # 尺子，不能另拼一个 ``exists()`` 近似谓词让 API、queue 与 CLI 分家。
        from novel.stages.stage4.plan import require_initial_design_slot
        require_initial_design_slot(_store_for(novel_dir))


def authorize_plan(
    base: str | os.PathLike[str], sid: str,
) -> DesignReviewDecision:
    """授权一次会产新 B/Plan 的入口；check/revalidate/edit 不调用这里。"""
    with _LOCK:
        record = _read_current(base, sid)
        novel_dir = paths.resolve_novel_dir(base, sid)
        current = _current_design_subjects(novel_dir)
        view = _design_review_view(novel_dir, record, current=current)
        if view.state != 'accepted' or view.current_decision is None:
            raise ApprovalRequired(
                f'Stage4B requires current all-accept design review; state={view.state}')
        if current_infeasible_binding(_store_for(novel_dir)) is not None:
            raise ApprovalRequired(
                '当前 A 已有 fresh B infeasible 终态；必须先按 typed design 决定'
                '修订/重规划 A，不能重复同一 B')
        # 批次 2：批准与 B 真跑之间候选/上游仍可能漂移；授权口按**本次现场
        # 重建**的 subjects 读预检（_design_review_view 每次都从盘面现算），
        # 关闭 TOCTOU。B 的 prepare_plan_candidate 在任何 wire 前还会再跑一次。
        preflight = view.preflight
        if preflight is None or preflight.status == 'blocked':
            owners = None if preflight is None else preflight.blocked_owners
            raise ApprovalRequired(
                'A→formal 预检未通过（status='
                f"{'missing' if preflight is None else 'blocked'}, "
                f'owners={owners}）；先按 owner 收口，不能授权 B')
        return view.current_decision


def authorize_context(
    base: str | os.PathLike[str], sid: str,
) -> StructureApprovalDecision:
    """授权免费 context 组装；只认当前最终 Plan 已逐章全 accept。"""
    with _LOCK:
        record = _read_current(base, sid)
        novel_dir = paths.resolve_novel_dir(base, sid)
        view = _structure_view(novel_dir, record)
        if (
            not record.gate['s4']
            or view.state != 'accepted'
            or view.current_decision is None
        ):
            raise ApprovalRequired(
                'stage4.context requires current final structure all-accept approval')
        return view.current_decision


def _canonical_design_constraints(
    rows: list[DesignRepairConstraint],
) -> list[DesignRepairConstraint]:
    """按 typed payload 规范排序去重；自由文本 reason 永不进入执行面。"""
    by_key = {
        json.dumps(
            row.model_dump(mode='json'), ensure_ascii=False,
            sort_keys=True, separators=(',', ':'),
        ): row
        for row in rows
    }
    return [by_key[key] for key in sorted(by_key)]


def _design_constraint_sha256(
    epoch: str, ordinal: int, constraint: DesignRepairConstraint,
) -> str:
    return _decision_sha256({
        'kind': 'stage4-design-active-constraint-v1',
        'strategy_epoch_sha256': epoch,
        'output_ordinal': ordinal,
        'constraint': constraint.model_dump(mode='json'),
    })


def _constraint_catalog_from_active(
    epoch: str, ordinal: int, active: dict[str, DesignActiveConstraint],
) -> DesignConstraintCatalog:
    items = [active[key] for key in sorted(active)]
    return DesignConstraintCatalog(
        expected_constraint_set_sha256=_decision_sha256({
            'kind': 'stage4-design-constraint-catalog-v1',
            'strategy_epoch_sha256': epoch,
            'output_ordinal': ordinal,
            'items': [item.model_dump(mode='json') for item in items],
        }),
        items=items,
    )


def _apply_design_constraint_operations(
    active: dict[str, DesignActiveConstraint],
    operations: DesignConstraintOperations | None,
    *, epoch: str, ordinal: int,
) -> tuple[dict[str, DesignActiveConstraint], list[DesignRepairConstraint]]:
    """唯一处置解释器：只认精确历史目录，不解析 reason，也不隐式撤销。"""
    if operations is None:
        return dict(active), []
    catalog = _constraint_catalog_from_active(epoch, ordinal, active)
    if operations.expected_constraint_set_sha256 != catalog.expected_constraint_set_sha256:
        raise HumanEditProtected(f'Stage4A 第 {ordinal} 章历史约束集合已漂移')
    if [item.constraint_sha256 for item in operations.items] != sorted(active):
        raise HumanEditProtected(
            f'Stage4A 第 {ordinal} 章 constraint_operations 必须精确覆盖每条 active SHA')
    retained = dict(active)
    replacements: list[DesignRepairConstraint] = []
    for operation in operations.items:
        if operation.kind != 'keep':
            del retained[operation.constraint_sha256]
        if operation.kind == 'supersede':
            replacements.extend(operation.replacements)
    return retained, _canonical_design_constraints(replacements)


def _design_constraint_history_business(
    stored: _StoredDesignHistory,
) -> dict[str, Any]:
    """冻结在人实际看过的主体与完整决定上，识别紧邻的 proof-only 重绑。"""
    slim = isinstance(stored, _StoredDesignDecisionSlim)
    return {
        'request': _stored_design_external_payload(stored),
        'aggregate_subject_sha256': stored.aggregate_subject_sha256,
        'strategy_subject_sha256': (stored.strategy_subject_sha256 if slim
                                    else stored.strategy.subject_sha256),
        'strategy_epoch_sha256': (stored.strategy_epoch_sha256 if slim
                                 else stored.chapters[0].subject.strategy_epoch_sha256),
        'chapter_subject_sha256': (stored.chapter_subject_sha256 if slim else {
            row.output_ordinal: row.subject_sha256 for row in stored.chapters}),
        'repair_capability': stored.repair_capability.model_dump(mode='json'),
        'binding_infeasibility': (stored.binding_infeasibility.model_dump(mode='json')
                                 if stored.binding_infeasibility is not None else None),
    }


def _fold_design_constraints(
    history: list[_StoredDesignHistory], *, epoch: str, ordinal: int,
    through_decision_sha256: str | None = None,
) -> DesignConstraintCatalog:
    """纯历史 fold；expired 候选仍读到同 epoch 的真实未关闭义务。

    无 operations 的发行历史保持原有“最近 accept 后并集”语义。proof-only
    重绑不再次执行同一处置；相同决定 SHA 却出现不同事实必须关闭式拒绝。
    """
    active: dict[str, DesignActiveConstraint] = {}
    seen: dict[str, str] = {}
    previous_business: dict[str, Any] | None = None
    found = through_decision_sha256 is None
    for stored in history:
        if not _stored_design_is_self_consistent(stored):
            raise HumanEditProtected('Stage4A 历史决定/proof 摘要不闭合，不能铸造约束目录')
        stored_sha = stored.decision_sha256
        identity = json.dumps(stored.model_dump(
            mode='json', exclude={'proof', 'decided_at'}), ensure_ascii=False,
            sort_keys=True, separators=(',', ':'))
        business = _design_constraint_history_business(stored)
        if stored_sha in seen and seen[stored_sha] != identity:
            raise HumanEditProtected('Stage4A 历史决定 SHA 对应的事实发生漂移')
        seen[stored_sha] = identity
        if business != previous_business:
            if isinstance(stored, _StoredDesignDecisionSlim):
                candidate = next((item for item in stored.request.chapters
                                  if item.output_ordinal == ordinal
                                  and stored.strategy_epoch_sha256 == epoch), None)
            else:
                candidate = next((item for item in stored.chapters
                                  if item.output_ordinal == ordinal
                                  and item.subject.strategy_epoch_sha256 == epoch), None)
            if candidate is not None:
                active, replacements = _apply_design_constraint_operations(
                    active, candidate.constraint_operations, epoch=epoch, ordinal=ordinal)
                if candidate.decision == 'accept':
                    active = {}
                else:
                    for constraint in _canonical_design_constraints(
                            [*candidate.constraints, *replacements]):
                        key = _design_constraint_sha256(epoch, ordinal, constraint)
                        prior = active.get(key)
                        sources = [] if prior is None else prior.source_decision_sha256s
                        active[key] = DesignActiveConstraint(
                            constraint_sha256=key, constraint=constraint,
                            source_decision_sha256s=sorted({*sources, stored_sha}),
                        )
                    if not active:
                        raise HumanEditProtected(
                            f'Stage4A 第 {ordinal} 章 request_repair 处置后没有有效约束')
        previous_business = business
        if stored_sha == through_decision_sha256:
            found = True
            break
    if not found:
        raise HumanEditProtected('Stage4A 当前 decision 不在 raw history 中')
    return _constraint_catalog_from_active(epoch, ordinal, active)


def _design_constraint_catalog(
    record: _ApprovalRecordV8, current: _DesignSubjects,
) -> dict[int, DesignConstraintCatalog]:
    return {
        row.chapter.output_ordinal: _fold_design_constraints(
            record.design_history, epoch=current.strategy_epoch_sha256,
            ordinal=row.chapter.output_ordinal)
        for row in current.chapters
    }


def design_constraint_catalog(
    base: str | os.PathLike[str], sid: str,
) -> dict[str, dict[str, Any]]:
    """当前候选的完整历史义务目录；与 GET template 共用一把纯历史尺子。"""
    with _LOCK:
        record = _read_current(base, sid)
        current = _current_design_subjects(paths.resolve_novel_dir(base, sid))
        return {str(key): value.model_dump(mode='json') for key, value in
                _design_constraint_catalog(record, current).items()}


def freeze_design_constraint_history(
    base: str | os.PathLike[str], sid: str, *, epoch: str, ordinals: list[int],
    history_prefix: list[str] | None = None,
) -> dict[str, Any]:
    """为上游重建冻结原约束前缀；恢复只核验该前缀，不消费后来产生的决定。

    正式候选此时可以 stale，所以不能借 current review 的 freshness 取得目录。
    约束仍由同一历史 fold 解释，删除或损坏原决定保持未知。
    """
    with _LOCK:
        history = _read_current(base, sid).design_history
        if history_prefix is not None:
            if (not isinstance(history_prefix, list)
                    or [row.decision_sha256 for row in history[:len(history_prefix)]] != history_prefix):
                raise HumanEditProtected('上游重建冻结的原审阅历史前缀已损坏或缺失')
            history = history[:len(history_prefix)]
        return {'strategy_epoch_sha256': epoch,
                'history_prefix': [row.decision_sha256 for row in history],
                'catalogs': {str(ordinal): _fold_design_constraints(
                    history, epoch=epoch, ordinal=ordinal).model_dump(mode='json') for ordinal in ordinals}}


def _require_design_constraint_directions(
    constraints: list[DesignRepairConstraint], *, ordinal: int,
) -> None:
    """只关闭机械可证的方向矛盾，不把多场景集合猜成逐场全称要求。"""
    excluded: dict[str, set[str]] = {}
    for constraint in constraints:
        if constraint.code == 'scene_goal_exclude':
            for alias in constraint.opaque_aliases:
                excluded.setdefault(alias, set()).update(constraint.scene_refs)
    for constraint in constraints:
        if constraint.code != 'scene_goal':
            continue
        impossible = [alias for alias in constraint.opaque_aliases
                      if set(constraint.scene_refs).issubset(excluded.get(alias, set()))]
        if impossible:
            raise HumanEditProtected(
                f'Stage4A 第 {ordinal} 章 scene_goal 的所有指定场景同时排除 {impossible}；'
                '须显式 withdraw/supersede 原义务，不能交给 Executor 猜方向')


def _effective_design_submission_constraints(
    record: _ApprovalRecordV8, request: DesignReviewMaterialized,
) -> dict[int, list[DesignRepairConstraint]]:
    """新票必须显式处置完整 active 目录；旧票重放在调用本函数前处理。"""
    effective: dict[int, list[DesignRepairConstraint]] = {}
    for row in request.chapters:
        epoch = row.subject.strategy_epoch_sha256
        if epoch is None:
            if row.constraint_operations is not None:
                raise HumanEditProtected('legacy Stage4A 约束没有 strategy epoch，不能处置历史义务')
            effective[row.output_ordinal] = list(row.constraints)
            continue
        catalog = _fold_design_constraints(
            record.design_history, epoch=epoch, ordinal=row.output_ordinal)
        active = {item.constraint_sha256: item for item in catalog.items}
        if active and row.constraint_operations is None:
            raise HumanEditProtected(
                f'Stage4A 第 {row.output_ordinal} 章必须显式提交 constraint_operations')
        retained, replacements = _apply_design_constraint_operations(
            active, row.constraint_operations, epoch=epoch, ordinal=row.output_ordinal)
        constraints = _canonical_design_constraints([
            *(item.constraint for item in retained.values()),
            *row.constraints, *replacements,
        ])
        _require_design_constraint_directions(constraints, ordinal=row.output_ordinal)
        if row.decision == 'request_repair' and not constraints:
            raise HumanEditProtected(
                f'Stage4A 第 {row.output_ordinal} 章 request_repair 处置后没有有效约束')
        effective[row.output_ordinal] = constraints
    return effective


def _chapter_constraint_scope(
    record: _ApprovalRecordV8,
    decision: DesignReviewDecision,
    row: DesignChapterReview,
) -> DesignRepairChapterScope:
    """Executor 只消费同一纯历史 fold 产出的有效累计范围。"""
    epoch = row.subject.strategy_epoch_sha256
    if epoch is None:
        return DesignRepairChapterScope(
            output_ordinal=row.output_ordinal,
            subject_sha256=row.subject_sha256,
            constraints=row.constraints,
        )
    catalog = _fold_design_constraints(
        record.design_history, epoch=epoch, ordinal=row.output_ordinal,
        through_decision_sha256=decision.decision_sha256)
    canonical = _canonical_design_constraints([item.constraint for item in catalog.items])
    incoming = _canonical_design_constraints([
        *row.constraints,
        *(replacement for operation in (
            row.constraint_operations.items if row.constraint_operations else [])
          for replacement in operation.replacements),
    ])
    if not canonical or any(item not in canonical for item in incoming):
        raise HumanEditProtected(f'Stage4A 第 {row.output_ordinal} 章累计约束断链')
    return DesignRepairChapterScope(
        output_ordinal=row.output_ordinal,
        subject_sha256=row.subject_sha256,
        new_constraints=incoming,
        accumulated_constraints=canonical,
        constraint_operations=row.constraint_operations,
        constraint_set_sha256=_decision_sha256({
            'kind': 'stage4-design-repair-constraint-set-v1',
            'strategy_epoch_sha256': epoch,
            'output_ordinal': row.output_ordinal,
            'constraints': [item.model_dump(mode='json') for item in canonical],
        }),
    )


def _design_chapter_edit_review_token(
    decision: DesignReviewDecision,
    row: DesignChapterReview,
    scope: DesignRepairChapterScope,
) -> str:
    """把完整 edit 授权的内部多摘要封成一枚不可拆的外部票。"""
    epoch = row.subject.strategy_epoch_sha256
    chapter_strategy = row.subject.chapter_strategy_sha256
    constraint_set = scope.constraint_set_sha256
    if (
        not scope.is_v3
        or epoch is None
        or chapter_strategy is None
        or constraint_set is None
        or row.output_ordinal != scope.output_ordinal
        or row.subject_sha256 != scope.subject_sha256
    ):
        raise HumanEditProtected(
            'Stage4A 完整章 edit token 只能由当前 v3 章级 repair scope 铸造')
    digest = _decision_sha256({
        'kind': 'stage4-design-chapter-edit-review-token-v1',
        'review_contract': DESIGN_REVIEW_CONTRACT,
        'candidate_sha256': decision.candidate_sha256,
        'aggregate_subject_sha256': decision.aggregate_subject_sha256,
        'decision_sha256': decision.decision_sha256,
        'output_ordinal': row.output_ordinal,
        'chapter_subject_sha256': row.subject_sha256,
        'strategy_epoch_sha256': epoch,
        'chapter_strategy_sha256': chapter_strategy,
        'constraint_set_sha256': constraint_set,
    })
    return f's4er1_{digest}'


def _strategy_replan_feedback(
    record: _ApprovalRecordV8,
    decision: DesignReviewDecision,
) -> DesignStrategyReplanFeedback:
    """把策略反馈、已接受章及章失败历史收成一个无自由文本的 typed 面。"""
    epoch = next(
        row.subject.strategy_epoch_sha256 for row in decision.chapters
        if row.subject.strategy_epoch_sha256 is not None)
    accumulated: list[Any] = []
    found = False
    for stored in record.design_history:
        if isinstance(stored, _StoredDesignDecisionSlim):
            strategy_decision = stored.request.strategy.decision
            constraints = [DesignStrategyReplanConstraint(
                kind=row.kind,
                subject_sha256=stored.strategy_subject_sha256,
                chapter_ordinals=row.chapter_ordinals,
            ) for row in stored.request.strategy.replan_constraints]
            public_epochs = {stored.strategy_epoch_sha256}
            stored_sha = stored.decision_sha256
        else:
            public = _public_design_decision(stored)
            strategy_decision = public.strategy.decision
            constraints = public.strategy.replan_constraints
            public_epochs = {
                row.subject.strategy_epoch_sha256 for row in public.chapters
            }
            stored_sha = public.decision_sha256
        if epoch in public_epochs:
            if strategy_decision == 'accept':
                accumulated = []
            else:
                accumulated.extend(constraints)
        if stored_sha == decision.decision_sha256:
            found = True
            break
    if not found:
        raise HumanEditProtected('Stage4A 策略 decision 不在 raw history 中')

    def canonical_strategy(rows: list[Any]) -> list[Any]:
        by_key = {
            json.dumps(row.model_dump(mode='json'), ensure_ascii=False,
                       sort_keys=True, separators=(',', ':')): row
            for row in rows
        }
        return [by_key[key] for key in sorted(by_key)]

    new_rows = canonical_strategy(decision.strategy.replan_constraints)
    all_rows = canonical_strategy(accumulated)
    accepted = [
        DesignAcceptedChapterConstraint(
            output_ordinal=row.output_ordinal,
            subject_sha256=row.subject_sha256,
            chapter_strategy_sha256=row.subject.chapter_strategy_sha256 or '',
        )
        for row in decision.chapters if row.decision == 'accept'
    ]
    failures = [
        _chapter_constraint_scope(record, decision, row)
        for row in decision.chapters if row.decision == 'request_repair'
    ]
    return DesignStrategyReplanFeedback(
        new_constraints=new_rows,
        accumulated_constraints=all_rows,
        constraint_set_sha256=_decision_sha256({
            'kind': 'stage4-design-strategy-feedback-set-v1',
            'strategy_epoch_sha256': epoch,
            'constraints': [item.model_dump(mode='json') for item in all_rows],
        }),
        accepted_chapters=accepted,
        chapter_failures=failures,
    )


def resolve_design_action(
    base: str | os.PathLike[str], sid: str,
    review_token: str | None, *, expected: Literal['repair', 'replan'],
) -> DesignRepairScope:
    """把当前 typed A 决定解析成 core 唯一可执行的关闭式修订范围。"""
    with _LOCK:
        record = _read_current(base, sid)
        novel_dir = paths.resolve_novel_dir(base, sid)
        current = _current_design_subjects(novel_dir)
        view = _design_review_view(novel_dir, record, current=current)
        decision = view.current_decision
        if decision is None:
            raise ApprovalRequired(
                f'没有当前 Stage4A typed 决定；state={view.state}')
        expected_token = _design_action_review_token(decision.decision_sha256)
        if review_token is None:
            raise HumanEditProtected(
                '--review-token 必须给出当前 design review 的 opaque action token')
        if review_token != expected_token:
            raise HumanEditProtected(
                'review token 不等于当前 design action token')
        if expected == 'replan':
            if decision.strategy.decision != 'request_replan':
                raise ApprovalRequired('当前策略决定不是 request_replan')
            return DesignRepairScope(
                candidate_sha256=decision.candidate_sha256,
                aggregate_subject_sha256=decision.aggregate_subject_sha256,
                decision_sha256=decision.decision_sha256,
                binding_infeasibility=decision.binding_infeasibility,
                repair_capability=decision.repair_capability,
                strategy_epoch_sha256=current.strategy_epoch_sha256,
                strategy_replan=True,
                replan_feedback=_strategy_replan_feedback(
                    record, decision),
                target_chapters=[],
                chapters=[],
            )
        if expected != 'repair':
            raise ValueError(f'unknown design action {expected!r}')
        if decision.strategy.decision != 'accept':
            raise ApprovalRequired('策略 request_replan 时不得执行逐章 repair')
        agent_available = {
            row.output_ordinal
            for row in decision.repair_capability.chapters
            if (
                bool(row.agent_repair_available)
                if decision.repair_capability.is_v3
                else bool(row.request_repair_available)
            )
        }
        rows = [
            _chapter_constraint_scope(record, decision, row)
            for row in decision.chapters
            if row.decision == 'request_repair'
            and row.output_ordinal in agent_available
        ]
        if not rows:
            raise ApprovalRequired(
                '当前 design 决定没有可发 agent wire 的 request_repair 章节；'
                '请走零 wire 完整章替换')
        _require_design_repair_capability(decision)
        return DesignRepairScope(
            candidate_sha256=decision.candidate_sha256,
            aggregate_subject_sha256=decision.aggregate_subject_sha256,
            decision_sha256=decision.decision_sha256,
            binding_infeasibility=decision.binding_infeasibility,
            repair_capability=decision.repair_capability,
            strategy_epoch_sha256=current.strategy_epoch_sha256,
            strategy_replan=False,
            target_chapters=[row.output_ordinal for row in rows],
            chapters=rows,
        )


def resolve_design_edit_action(
    base: str | os.PathLike[str], sid: str,
    request: BlindChapterEditRequest,
) -> _DesignEditAuthorization:
    """现场展开 opaque token；不要求该章仍有 agent repair 额度。"""
    with _LOCK:
        record = _read_current(base, sid)
        novel_dir = paths.resolve_novel_dir(base, sid)
        current = _current_design_subjects(novel_dir)
        view = _design_review_view(novel_dir, record, current=current)
        decision = view.current_decision
        if decision is None or view.state != 'repair_requested':
            raise ApprovalRequired(
                f'Stage4A 完整章替换需要当前 request_repair 决定；'
                f'state={view.state}')
        row = next((
            item for item in decision.chapters
            if item.output_ordinal == request.output_ordinal
        ), None)
        if row is None or row.decision != 'request_repair':
            raise ApprovalRequired(
                f'Stage4A 第 {request.output_ordinal} 章当前不是 request_repair')
        capability = next((
            item for item in decision.repair_capability.chapters
            if item.output_ordinal == request.output_ordinal
        ), None)
        if capability is None or not capability.full_chapter_edit_available:
            raise ApprovalRequired(
                f'Stage4A 第 {request.output_ordinal} 章当前没有完整章替换能力')
        scope = _chapter_constraint_scope(record, decision, row)
        expected_token = _design_chapter_edit_review_token(
            decision, row, scope)
        epoch = row.subject.strategy_epoch_sha256
        chapter_strategy = row.subject.chapter_strategy_sha256
        if (
            request.review_token != expected_token
            or epoch is None
            or chapter_strategy is None
            or epoch != current.strategy_epoch_sha256
        ):
            raise HumanEditProtected(
                'Stage4A 完整章 edit 的 opaque review_token '
                '与当前 immutable workpack 不符')
        return _DesignEditAuthorization(
            review_token=expected_token,
            scope=scope,
            candidate_sha256=decision.candidate_sha256,
            decision_sha256=decision.decision_sha256,
            chapter_subject_sha256=row.subject_sha256,
            strategy_epoch_sha256=epoch,
            chapter_strategy_sha256=chapter_strategy,
        )


def approve_structure(
    base: str | os.PathLike[str], sid: str, request: StructureApprovalRequest,
) -> ApprovalDoc:
    """保存一次原子的全书结构裁决；本函数不启动任何作业。"""
    with approval_file_lock(base, sid):
        raw, record = _read_state(base, sid)
        if missing := [
            key for key in _GATE_REQUIREMENTS['s4'] if not record.gate[key]
        ]:
            raise ApprovalRequired(
                f's4 structure decision requires prior approvals: {missing}')
        novel_dir = paths.resolve_novel_dir(base, sid)
        current_subject, planned = _current_structure_subject_and_chapters(novel_dir)
        current_chapter_subjects = \
            _current_structure_chapter_subject_sha256(novel_dir)
        if request.review_token != f's4sr1_{current_subject}':
            raise HumanEditProtected(
                'Stage4 结构审批 opaque review_token 与当前 immutable workpack '
                '不符；请刷新后重审')
        decided = [row.output_ordinal for row in request.chapters]
        if decided != planned:
            raise HumanEditProtected(
                f'结构裁决章集必须精确等于当前计划：提交 {decided}，当前 {planned}')

        proof = _proof_for_structure(novel_dir, request)
        # proof 会重新读取完整计划字节；再看一遍语义 subject 与章集，避免计划在两次读取
        # 之间变化却把 A 的 subject 与 B 的字节 proof 拼成一份立即过期的假批准。
        after_subject, after_planned = _current_structure_subject_and_chapters(novel_dir)
        after_chapter_subjects = \
            _current_structure_chapter_subject_sha256(novel_dir)
        after_proof = _proof_for_structure(novel_dir, request)
        if (
            (after_subject, after_planned) != (current_subject, planned)
            or after_chapter_subjects != current_chapter_subjects
            or after_proof != proof
        ):
            raise HumanEditProtected('Stage4 计划在结构裁决提交期间发生变化；请刷新后重审')

        latest = raw.structure_decision
        same_payload = bool(
            latest is not None
            and _structure_decision_payload(
                _public_structure_decision(latest)) == _structure_decision_payload(request)
        )
        if latest is not None and latest.subject_sha256 == current_subject:
            if not same_payload:
                raise HumanEditProtected(
                    '当前 Stage4 最终结构主体已有 typed 决定；'
                    '同主体只接受 exact replay')
            if _same_proof(latest.proof, proof):
                return _view(base, record)
            # subject 与外部 typed payload 均逐字未变，仅完整计划/B provenance
            # proof 漂移。旧历史不可改写；下面追加同语义决定并只重绑当前 proof。
            review_inheritance.validate_history(_structure_inheritance_history(record))
        else:
            _require_structure_inheritance(record, current_chapter_subjects, request)

        public = StructureApprovalDecision(
            actor=request.actor,
            reason=request.reason,
            review_token=request.review_token,
            chapters=request.chapters,
            decided_at=_now(),
        )
        stored = _StoredStructureDecision(
            actor=request.actor, reason=request.reason,
            subject_sha256=current_subject, chapters=request.chapters,
            decided_at=public.decided_at, proof=proof,
            chapter_subject_sha256=current_chapter_subjects)
        history = [*raw.structure_history, stored]
        gates = dict(raw.gate)
        gate_proofs = dict(raw.gate_proofs)
        all_accepted = all(
            row.decision == 'accept' for row in request.chapters)
        gates['s4'] = all_accepted
        gate_proofs['s4'] = proof if all_accepted else None
        return _write(base, raw.model_copy(update={
            'gate': gates,
            'gate_proofs': gate_proofs,
            'structure_history': history,
            'structure_decision': stored,
        }))


def approve_gate(base: str | os.PathLike[str], sid: str, gate: str) -> ApprovalDoc:
    if gate not in APPROVAL_GATES:
        raise ApprovalRequired(f'unknown approval gate: {gate}')
    if gate == 's4':
        raise ApprovalRequired(
            's4 不接受匿名通用盖章；请提交带 subject hash、actor/reason 与逐章决定的 '
            'Stage4 structure approval')
    if gate == 's5':
        raise ApprovalRequired(
            's5 不接受匿名通用盖章；请经 `approval template stage5` / API 生成闭合'
            '工作包模板，由 Commander 逐项裁决后提交 typed 回执')
    with approval_file_lock(base, sid):
        raw, record = _read_state(base, sid)
        if missing := [
            key for key in _GATE_REQUIREMENTS.get(gate, ()) if not record.gate[key]
        ]:
            raise ApprovalRequired(f'{gate} requires prior approvals: {missing}')
        if gate == 's2' and not all(record.seen.values()):
            raise ApprovalRequired('stage2 global and chapter views must both be seen')
        proof = _require_artifacts(base, sid, gate, record)
        gates = dict(raw.gate)
        proofs = dict(raw.gate_proofs)
        gates[gate] = True
        proofs[gate] = proof
        return _write(base, raw.model_copy(update={
            'gate': gates, 'gate_proofs': proofs,
        }))


def backfill_from_disk(
    base: str | os.PathLike[str], sid: str,
) -> ApprovalBackfillResult:
    """补录 CLI/队列已经落成的链，并把补了哪些章结构化返回给界面。

    这是显式的人操作，不在 GET 时静默发生。普通逐屏验收仍走 `approve_gate`；
    本入口只在对应正式产物齐备且没有 `.INVALID` 时补章。Stage5 只认
    全部 canonical `stage5/chNN.json` + fresh Review。
    """
    with approval_file_lock(base, sid):
        raw_record, record = _read_state(base, sid)
        novel_dir = paths.resolve_novel_dir(base, sid)
        # backfill 只能补“产物已落成”的链，不能替人制造阅读事实。先把盘上
        # 历史布尔投影成 current；旧 seen.read proof 因未绑定工作包的
        # MainText/code/rules 会在这里自然失效。
        record = _effective_record(novel_dir, raw_record)
        # durable maps 从 raw 起步；另保留一套 effective maps 只用于本次前置判断。
        # 否则显式 backfill 一个上游 gate 也会顺手擦掉暂时 suspended 的下游事实。
        gates = dict(raw_record.gate)
        gate_proofs = dict(raw_record.gate_proofs)
        seen = dict(raw_record.seen)
        seen_proofs = dict(raw_record.seen_proofs)
        writing = raw_record.writing
        writing_proof = raw_record.writing_proof
        current_gates = dict(record.gate)
        current_gate_proofs = dict(record.gate_proofs)
        current_seen = dict(record.seen)
        current_seen_proofs = dict(record.seen_proofs)
        current_writing = record.writing
        current_writing_proof = record.writing_proof
        backfilled: list[str] = []

        for gate in _GATE_ORDER:
            if current_gates[gate]:
                continue
            # Stage4 不能从「盘上有一份计划」推成人已经逐章审过；只有
            # ``approve_structure`` 能生成 actor/reason/subject-bound typed 决定。
            # s6/s7 已直接投影 current typed 定稿，无需补一个前端点击章；
            # 真缺票也不能靠补录代审。其它检查单补录仍须显式操作，旧审批版本不迁移。
            if gate in ('s4', 's6', 's7'):
                continue
            if any(
                not current_gates[key]
                for key in _GATE_REQUIREMENTS.get(gate, ())
            ):
                continue

            if gate == 's2' and not all(current_seen.values()):
                # Stage2 产物齐备只证明“可读”，不证明 Commander 已经读过。
                # ``seen.read`` 只能由 hash-bound 工作包确认入口写入；
                # ``seen.global`` 也仍由自己的显式阅读动作写入。
                continue

            if gate == 's5':
                if state.stage5_legacy_paths(novel_dir):
                    continue
                try:
                    _require_stage5_complete(base, sid, record)
                    current_writing_proof = _proof_for_writing(novel_dir)
                except NovelError:
                    continue
                current_writing = True
                writing = True
                writing_proof = current_writing_proof

            working = record.model_copy(update={
                'gate': current_gates, 'gate_proofs': current_gate_proofs,
                'seen': current_seen, 'seen_proofs': current_seen_proofs,
                'writing': current_writing,
                'writing_proof': current_writing_proof,
            })
            try:
                proof = _proof_for_gate(novel_dir, gate, working)
            except NovelError:
                continue
            gates[gate] = True
            gate_proofs[gate] = proof
            current_gates[gate] = True
            current_gate_proofs[gate] = proof
            backfilled.append(gate)

        updated = raw_record.model_copy(update={
            'gate': gates, 'gate_proofs': gate_proofs,
            'seen': seen, 'seen_proofs': seen_proofs,
            'writing': writing, 'writing_proof': writing_proof,
        })
        # 无新增事实时也必须公开 effective 投影；``updated`` 仍是 durable raw，
        # 其中被 freshness 暂停的下游 true/proof 要留盘以便 exact 回退后复活，
        # 但不能直接塞进要求当前自洽的 public ApprovalDoc。
        approvals_view = (
            _write(base, updated)
            if backfilled
            else _view(base, _effective_record(novel_dir, updated))
        )
        return ApprovalBackfillResult(
            approvals=approvals_view, backfilled=backfilled)


def mark_seen(
    base: str | os.PathLike[str], sid: str, key: str,
    *, blueprint_subject: BlueprintSeenRequest | None = None,
    global_subject: Stage2GlobalSeenRequest | None = None,
) -> ApprovalDoc:
    """写一条 hash-bound 的 Stage2 阅读事实；两个视图都不再有无主体分支。

    ``seen.read`` 绑定 Blueprint 工作包 token；``seen.global`` 绑定 Global 审阅
    面 token（Global 主体 + review 完整字节 + MainText 主体 + blocking scope）
    并记录 actor。headless 路由下两枚 token 来自两个已接受的节点当前工作包，
    交互界面则由人读完当前 review 后显式提交，不再“打开页面即已读”。
    """
    if key not in _SEEN_ARTIFACTS:
        raise ApprovalRequired(f'unknown stage2 view: {key}')
    if key == 'read' and blueprint_subject is None:
        raise ApprovalRequired(
            'seen.read 必须携带实际读过的 Blueprint 工作包身份；'
            '请先取得完整工作包再确认')
    if key == 'global' and global_subject is None:
        raise ApprovalRequired(
            'seen.global 必须携带实际读过的 Global 审阅面身份（review_token + actor）；'
            '请先读完当前 review 再确认')
    if blueprint_subject is not None and key != 'read':
        raise ApprovalRequired('Blueprint 工作包身份只能用于 seen.read')
    if global_subject is not None and key != 'global':
        raise ApprovalRequired('Global 审阅面身份只能用于 seen.global')
    with approval_file_lock(base, sid):
        raw, record = _read_state(base, sid)
        novel_dir = paths.resolve_novel_dir(base, sid)
        if blueprint_subject is not None:
            from novel.views.stage2 import blueprint_review_workpack

            workpack = blueprint_review_workpack(base, sid)
            if workpack.binding.subject_kind != 'formal':
                raise ApprovalRequired(
                    'seen.read 只接受 current formal Blueprint 工作包；'
                    'INVALID 工作包只能用于恢复，不能生成已读证明')
            if workpack.review_token != blueprint_subject.review_token:
                raise ApprovalRequired(
                    'Blueprint 审阅工作包已过期；本次 seen.read 不落盘，'
                    '请刷新 immutable workpack 后重新确认')
        if key == 'read':
            proof, proof_identity = _blueprint_seen_proof(novel_dir, workpack)
            # 不能只做“两次 proof 相等”：若盘面恰在 workpack 比对后、第一次
            # proof 前从 B1 变到 B2，两次都可能稳定得到 P2。第一份 proof 的
            # 明文身份还必须重新等于用户提交的 B1 工作包身份。
            if proof_identity['review_token'] != blueprint_subject.review_token:
                raise ApprovalRequired(
                    'Blueprint 审阅主体在工作包校验后发生变化；'
                    '本次 seen.read 不落盘，请刷新工作包后重新确认')
            after_proof, after_identity = _blueprint_seen_snapshot(novel_dir, exact=True)
            if after_proof != proof or after_identity != proof_identity:
                raise ApprovalRequired(
                    'Blueprint 审阅主体在确认提交期间发生变化；'
                    '本次 seen.read 不落盘，请刷新工作包后重新确认')
        else:
            assert global_subject is not None
            proof, token = _global_seen_proof(novel_dir)
            if token != global_subject.review_token:
                raise ApprovalRequired(
                    'Global 审阅面已变化（review/主体/正文任一格不同）；'
                    '本次 seen.global 不落盘，请重新读取当前 review 后再确认')
            after_proof, after_token = _global_seen_proof(novel_dir)
            if after_proof != proof or after_token != token:
                raise ApprovalRequired(
                    'Global 审阅面在确认提交期间发生变化；'
                    '本次 seen.global 不落盘，请重新读取后再确认')
        seen = dict(raw.seen)
        proofs = dict(raw.seen_proofs)
        subjects = dict(raw.seen_subjects)
        seen[key] = True
        proofs[key] = proof
        if global_subject is not None:
            subjects[key] = {
                'actor': global_subject.actor,
                'review_token': global_subject.review_token,
            }
        elif blueprint_subject is not None:
            subjects[key] = {'review_token': blueprint_subject.review_token}
        return _write(base, raw.model_copy(update={
            'seen': seen, 'seen_proofs': proofs, 'seen_subjects': subjects,
        }))


def close_stage2_review(
    base: str | os.PathLike[str], sid: str,
    request: Stage2FinalReviewRequest,
) -> ApprovalDoc:
    """在本次代码坐标内验证两个决定并完成免费审批链，不复用盘面快照。"""
    with stage2_closure_projection(_projection_code()):
        return _close_stage2_review(base, sid, request)


def _close_stage2_review(
    base: str | os.PathLike[str], sid: str,
    request: Stage2FinalReviewRequest,
) -> ApprovalDoc:
    """只核验两份已经落盘的节点接受，并顺序补齐审批链。

    请求两枚 token 只绑定本次恢复快照；本函数不产生审阅决定、不调用模型。
    五步各自仍执行原有 currentness、typed review 与 TOCTOU 校验。若中途失败，
    已经落下的 raw proof 也是可审计、可幂等恢复的事实，最后的 ``s2`` 不会在
    前置项失效时被误开。
    """
    require_stage2_node_accepted(base, sid, 'stage2.global')
    require_stage2_node_accepted(base, sid, 'stage2.blueprint')
    from novel.stages.stage2.knowledge_links import require_current
    novel_dir = paths.resolve_novel_dir(base, sid)
    require_current(state.readonly_ctx(novel_dir, 'stage2.blueprint', _projection_code()))
    raw = _read_raw(base, sid)
    global_decision = raw.stage2_reviews['stage2.global']
    novel_dir = paths.resolve_novel_dir(base, sid)
    global_token = _stage2_review_identity(novel_dir, 'stage2.global')[0]
    blueprint_token = _stage2_review_identity(novel_dir, 'stage2.blueprint')[0]
    if (request.global_review_token != global_token
            or request.blueprint_review_token != blueprint_token):
        raise HumanEditProtected('Stage2 收口 token 已过期；零模型刷新收口请求')
    approve_gate(base, sid, 's0')
    approve_gate(base, sid, 's1')
    mark_seen(base, sid, 'read', blueprint_subject=BlueprintSeenRequest(
        review_token=blueprint_token))
    mark_seen(base, sid, 'global', global_subject=Stage2GlobalSeenRequest(
        review_token=global_token, actor=global_decision.actor))
    return approve_gate(base, sid, 's2')


def close_stage2_accepted(base: str | os.PathLike[str], sid: str) -> ApprovalDoc:
    """两接受已经落盘时的可重复、零模型收口入口。"""
    require_stage2_node_accepted(base, sid, 'stage2.global')
    require_stage2_node_accepted(base, sid, 'stage2.blueprint')
    raw = _read_raw(base, sid)
    novel_dir = paths.resolve_novel_dir(base, sid)
    decision = raw.stage2_reviews['stage2.blueprint']
    return close_stage2_review(base, sid, Stage2FinalReviewRequest(
        actor=decision.actor, reason=decision.reason,
        global_review_token=_stage2_review_identity(novel_dir, 'stage2.global')[0],
        blueprint_review_token=_stage2_review_identity(novel_dir, 'stage2.blueprint')[0]))


def _has_stage5_work(novel_dir: Path) -> bool:
    stage5 = novel_dir / 'stage5'
    return bool(
        state.stage5_legacy_paths(novel_dir)
        or state.chapters_done(novel_dir)
        or any(stage5.glob('ch*.INVALID.json'))
        or any(stage5.glob('ch*.rejected.json'))
    )


def _writing_prerequisites(
    base: str | os.PathLike[str], sid: str, record: _ApprovalRecordV8,
) -> tuple[Path, str, _ApprovalProof]:
    """写作 template/apply 共用的 current subject 判据；不写盘、不启动作业。"""
    novel_dir = paths.resolve_novel_dir(base, sid)
    _require_stage5_contract_compatible(novel_dir, action='授权新作业')
    # 这不是“盘上曾经开过 s4”即可：context 必须在当前最终 Plan 已逐章接受后
    # 由独立免费节点产出，写作 proof 随后把它的完整 fresh 字节一并冻结。
    authorize_context(base, sid)
    planned = _planned(base, sid)
    if planned is None:
        raise UpstreamMissing(f'{STAGE4_PLAN_CHAPTERS} is required before writing')
    if not planned:
        raise ApprovalRequired('stage4 plan contains no output chapters')
    _require_fresh_json(_store_for(novel_dir), STAGE4_WRITER_CONTEXT)
    subject, proof = _current_writing_subject(novel_dir)
    return novel_dir, subject, proof


def structure_template(
    base: str | os.PathLike[str], sid: str, *, actor: str, reason: str,
) -> StructureApprovalRequest:
    """生成关闭式结构审批模板；每章默认 request_replan，且全程只读。"""
    with _LOCK:
        record = _read_current(base, sid)
        if missing := [
            key for key in _GATE_REQUIREMENTS['s4'] if not record.gate[key]
        ]:
            raise ApprovalRequired(
                f's4 structure decision requires prior approvals: {missing}')
        novel_dir = paths.resolve_novel_dir(base, sid)
        subject, planned = _current_structure_subject_and_chapters(novel_dir)
        if not planned:
            raise ApprovalRequired('stage4 plan contains no output chapters')
        current_chapters = _current_structure_chapter_subject_sha256(novel_dir)
        inherited = review_inheritance.accepted_sources(
            _structure_inheritance_history(record),
            {str(key): value for key, value in current_chapters.items()})
        return StructureApprovalRequest(
            actor=actor,
            reason=reason,
            review_token=f's4sr1_{subject}',
            chapters=[{
                'output_ordinal': ordinal,
                **({'decision': 'accept',
                    'reason': inherited[str(ordinal)].units[str(ordinal)].payload['reason'],
                    'source_decision_sha256': inherited[str(ordinal)].decision_sha256}
                   if str(ordinal) in inherited else {
                       'decision': 'request_replan',
                       'reason': '关闭式模板：尚未逐章核实，保持 request_replan'}),
            } for ordinal in planned],
        )


def writing_template(
    base: str | os.PathLike[str], sid: str, *, actor: str, reason: str,
) -> WritingApprovalRequest:
    """生成绑定当前 Stage1/Stage4/契约的 typed 写作授权模板；全程只读。"""
    with _LOCK:
        record = _read_current(base, sid)
        _novel_dir, subject, _proof = _writing_prerequisites(base, sid, record)
        return WritingApprovalRequest(
            actor=actor, reason=reason, review_token=f's5wr1_{subject}')


def approve_writing(
    base: str | os.PathLike[str], sid: str, request: WritingApprovalRequest,
) -> ApprovalDoc:
    """保存 Stage5 typed 写作授权；不启动模型、作业或任何 continuation。"""
    with approval_file_lock(base, sid):
        raw, record = _read_state(base, sid)
        novel_dir, current_subject, current_proof = _writing_prerequisites(
            base, sid, record)
        if request.review_token != f's5wr1_{current_subject}':
            raise HumanEditProtected(
                'Stage5 写作审批 opaque review_token 与当前 immutable workpack '
                '不符；请刷新模板后重审')
        decision_proof = _proof_for_writing_decision(novel_dir, request)

        # template 与 apply 之间允许停很久，但 apply 自己的多次读取也不能把两个
        # 时刻的字节拼成一张立即过期的授权。第二次完整计算与第一次必须一致。
        after_subject, after_proof = _current_writing_subject(novel_dir)
        after_decision_proof = _proof_for_writing_decision(novel_dir, request)
        if (
            after_subject != current_subject
            or after_proof != current_proof
            or after_decision_proof != decision_proof
        ):
            raise HumanEditProtected('Stage5 写作主体在授权提交期间发生变化；请刷新模板后重审')

        latest = raw.writing_decision
        if (
            latest is not None
            and _writing_decision_payload(
                _public_writing_decision(latest)) == _writing_decision_payload(request)
            and _same_proof(latest.proof, decision_proof)
        ):
            if record.writing and _same_proof(record.writing_proof, current_proof):
                return _view(base, record)
            return _write(base, raw.model_copy(update={
                'writing': True,
                'writing_proof': current_proof,
            }))

        public = WritingApprovalDecision(
            actor=request.actor,
            reason=request.reason,
            review_token=request.review_token,
            decided_at=_now(),
        )
        stored = _StoredWritingDecision(
            actor=request.actor, reason=request.reason,
            subject_sha256=current_subject, decided_at=public.decided_at,
            proof=decision_proof)
        return _write(base, raw.model_copy(update={
            'writing': True,
            'writing_proof': current_proof,
            'writing_history': [*raw.writing_history, stored],
            'writing_decision': stored,
        }))


def begin_writing(
    base: str | os.PathLike[str], sid: str,
) -> ApprovalDoc:
    """旧 UI 的空-body 兼容缝；Commander/CLI 不得调用这一匿名入口。

    它保留既有业务语义与旧 v5 盘面，不伪造 actor/reason，也不把匿名旧决定
    塞进 typed history。正式 agent 路径只暴露 ``writing_template`` +
    ``approve_writing``。
    """
    with approval_file_lock(base, sid):
        raw, record = _read_state(base, sid)
        _novel_dir, _subject, proof = _writing_prerequisites(base, sid, record)
        if record.writing:
            return _view(base, record)
        return _write(base, raw.model_copy(update={
            'writing': True,
            'writing_proof': proof,
        }))


def authorize_writing(
    base: str | os.PathLike[str],
    sid: str,
    chapters: list[int] | None,
    *,
    requires_fresh_review: bool = False,
    reviewer_rewrite_request_rel: str | None = None,
) -> None:
    """启动付费 Stage5 作业前的只读授权校验；不替人自动签字。"""
    with _LOCK:
        record = _read_current(base, sid)
        novel_dir = paths.resolve_novel_dir(base, sid)
        store, current_review = _require_stage5_contract_compatible(
            novel_dir, action='触发新付费作业')
        # ``_read_current`` 已把旧 subject / 旧计划 / 旧代码下的 proof
        # 折成无效态；这里仍将两张独立通行证逐张点名。
        # 只查 ``writing`` 会把“结构尚未逐章接受”误报成普通
        # 写作授权缺失，也会让未来一条不经 effective projection 的
        # 调用缝只靠隐式约定防绕过。
        if not record.gate['s4'] or record.structure_decision is None:
            raise ApprovalRequired(
                '请先逐章接受当前且新鲜的 typed Stage4 结构决定')
        if not record.writing or record.writing_proof is None:
            raise ApprovalRequired('authorize current Stage5 whole-book writing first')
        planned = _planned(base, sid)
        if planned is None:
            raise UpstreamMissing(f'{STAGE4_PLAN_CHAPTERS} is required before writing')
        requested_list = planned if chapters is None else list(chapters)
        if (requested_list != sorted(set(requested_list))
                or any(value < 1 for value in requested_list)):
            raise HumanEditProtected('--chapters must be sorted, unique positive ordinals')
        if outside := sorted(set(requested_list) - set(planned)):
            raise HumanEditProtected(
                f'Stage5 writing request contains chapters outside the current plan: {outside}')
        from novel.stages.stage5.review import require_fresh_review
        # 首轮写作时 Review 本来尚未产生，missing 可通行；一旦盘上有报告，
        # 它必须 fresh。revise 则连 missing 也关闭式拒绝。
        if reviewer_rewrite_request_rel is not None:
            from novel.stages.stage5 import reviewer_rewrite
            ready = reviewer_rewrite.ready_reviewer_rewrite_targets(
                store.ctx, reviewer_rewrite_request_rel)
            if list(ready) != requested_list:
                raise HumanEditProtected('reviewer rewrite 只能授权整批尚未执行的精确章节')
            batch, _digest = reviewer_rewrite.load_reviewer_rewrite_batch(
                store.ctx, reviewer_rewrite_request_rel)
            for unit in batch.requests:
                if unit.target_ordinal in ready:
                    reviewer_rewrite.require_current_reviewer_rewrite_request(
                        store.ctx, reviewer_rewrite_request_rel,
                        request_sha256=unit.request_sha256)
        elif requires_fresh_review or current_review.state != 'missing':
            require_fresh_review(store.ctx, store)


def authorize_prepared_recovery(
    base: str | os.PathLike[str],
    sid: str,
    chapters: list[int],
) -> None:
    """授权 Stage5 initial-write prepared 的 0-wire 尾窗恢复。

    它复用 ``_read_current`` 对 s4 与 writing proof 的即时投影，因此 Stage1、
    Plan、Context、measurement 或写作契约任一漂移都会关闭式失败。与新付费写作
    唯一不同的是不要求既有 Stage5 Review fresh：canonical 尾窗未收口时，旧 Review
    正常就会 stale，反向拿它堵恢复会让已经付费的 checkpoint 永久悬空。
    """
    with _LOCK:
        record = _read_current(base, sid)
        novel_dir = paths.resolve_novel_dir(base, sid)
        _require_stage5_contract_compatible(
            novel_dir, action='收口 prepared checkpoint')
        if not record.gate['s4'] or record.structure_decision is None:
            raise ApprovalRequired(
                '请先逐章接受当前且新鲜的 typed Stage4 结构决定')
        if not record.writing or record.writing_proof is None:
            raise ApprovalRequired(
                'prepared recovery 仍需当前 Stage5 whole-book writing 授权')
        planned = _planned(base, sid)
        if planned is None:
            raise UpstreamMissing(
                f'{STAGE4_PLAN_CHAPTERS} is required before prepared recovery')
        requested = list(chapters)
        if (not requested
                or requested != sorted(set(requested))
                or any(value < 1 for value in requested)):
            raise HumanEditProtected(
                'prepared recovery chapters must be non-empty, sorted, unique positive ordinals')
        if outside := sorted(set(requested) - set(planned)):
            raise HumanEditProtected(
                'Stage5 prepared recovery contains chapters outside the current plan: '
                f'{outside}')


def reset(base: str | os.PathLike[str], sid: str) -> ApprovalDoc:
    with approval_file_lock(base, sid):
        novel_dir = paths.resolve_novel_dir(base, sid)
        if _has_stage5_work(novel_dir):
            raise HumanEditProtected('cannot reset approvals after stage5 drafts exist')
        return _write(base, _fresh_record(sid, _planned(base, sid)))
