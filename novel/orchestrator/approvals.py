#!/usr/bin/env python3
from __future__ import annotations

from contracts.artifacts import (
    STAGE0_CLEAN_TEXT, STAGE0_REVISIONS, STAGE1_HOOK_COPY, STAGE1_MAIN_TEXT,
    STAGE2_CHAPTER_BLUEPRINTS,
    STAGE4_BLIND_CANDIDATE, STAGE4_EVIDENCE_BINDING,
    STAGE4_PLAN_CHAPTERS, STAGE4_WRITER_CONTEXT,  # plan_chapters 仅历史 proof 复验
    STAGE6_HOOK, STAGE7_NAME_MAP, artifact_rel,
)

import hashlib
import json
import os
import threading
from collections.abc import Iterator
from contextlib import contextmanager
from contextvars import ContextVar
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Literal

from pydantic import Field, ValidationError, model_validator
from contracts.schema.plot_materials import PlotChapterReviewMaterials

from contracts.invariants import (
    APPROVAL_GATES,
    DESIGN_REVIEW_CONTRACT,
    STRUCTURE_CHANGE_KINDS,
    STRUCTURE_METRIC_CONTRACT,
    STRUCTURE_REVIEW_CONTRACT,
    STYLE_MEASUREMENT_CONTRACT,
)
from contracts.schema import artifact_key, artifact_spec
from contracts.schema.api import (
    ApprovalAction,
    ApprovalBackfillResult,
    ApprovalDoc,
    DesignBindingInfeasibility,
    DesignBlindChapterTruth,
    DesignChapterReview,
    DesignActiveConstraint,
    DesignConstraintCatalog,
    DesignConstraintOperations,
    DesignChapterSubject,
    DesignPreflightSummary,
    DesignRepairConstraint,
    DesignRepairScope,
    DesignReviewBasis,
    DesignReviewDecision,
    DesignReviewHistoryEntry,
    DesignReviewMaterialized,
    DesignReviewRepairCapability,
    DesignReviewRequest,
    DesignReviewView,
    DesignStrategyReview,
    DesignStrategyReplanConstraint,
    DesignStrategyReplanFeedback,
    DesignStrategySubject,
    DesignStructureEvidence,
    Stage5ClosureDecision,
    Stage5ClosureRequest,
    Stage5ClosureView,
    Stage5FindingGroupAck,
    Stage5FindingGroupRuling,
    Stage5MustAppearDecision,
    Stage5MustAppearRulingInput,
    StructureChapterDecision,
    StructureApprovalDecision,
    StructureApprovalRequest,
    StructureApprovalView,
    WritingApprovalDecision,
    WritingApprovalRequest,
    WritingApprovalView,
)
from contracts.schema.orchestration import NovelOverview
from contracts.schema.stage0 import Stage0Transform
from contracts.schema.stage1 import HookCopy, MainText
from contracts.schema.stage2 import ChapterBlueprints
from contracts.schema.stage4 import (
    BindingConcern, BlindCandidate,
    EvidenceBinding, PlanChapters,
)
# ⚠ F8a：`contracts.schema.stage6` 的 import 随 Stage6 整层退役删除（F6d 登记）。
from contracts.schema.stage7 import NameMap
from contracts.schema.base import StrictModel
from novel import paths, prov
from novel.errors import (
    ApprovalFrozen, ApprovalRequired, ArtifactUnreadable, GateBlocked, HumanEditProtected,
    NovelError, UpstreamMissing, UpstreamUnproven,
)
from novel.llm import stage5_runs
from novel.orchestrator import review_inheritance, state
from novel.stages.stage4.phase_state import current_infeasible_binding
from novel.store import ArtifactStore

APPROVALS_DIRNAME = 'approvals'
_LOCK = threading.RLock()
_READ_CODE: ContextVar[str | None] = ContextVar(
    'approval_read_code', default=None)
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


class _ApprovalRecordV8(StrictModel):
    """唯一现役盘上审批；当前主体与不可变历史仍完整校验，不继承旧版本读取器。"""

    v: Literal[9] = 9
    sid: str = Field(min_length=1)
    gate: dict[str, bool]
    writing: bool = False
    gate_proofs: dict[str, _ApprovalProof | None]
    writing_proof: _ApprovalProof | None = None
    structure_history: list[_StoredStructureDecision] = Field(default_factory=list)
    structure_decision: _StoredStructureDecision | None = None
    writing_history: list[_StoredWritingDecision] = Field(default_factory=list)
    writing_decision: _StoredWritingDecision | None = None
    #: 完整与 slim 历史仍保留原证据；历史能否生效由当前审阅契约另行核验。
    design_history: list[_StoredDesignHistory] = Field(default_factory=list)
    design_decision: _StoredDesignHistory | None = None
    closure_history: list[_StoredClosureDecision] = Field(default_factory=list)
    closure_decision: _StoredClosureDecision | None = None

    @model_validator(mode='after')
    def _record_is_complete_and_proved(self) -> _ApprovalRecordV8:
        if set(self.gate) != set(APPROVAL_GATES):
            raise ValueError('gate must contain exactly ' + ','.join(APPROVAL_GATES))
        if set(self.gate_proofs) != set(APPROVAL_GATES):
            raise ValueError('gate_proofs must contain exactly ' + ','.join(APPROVAL_GATES))
        for key, approved in self.gate.items():
            if approved != (self.gate_proofs[key] is not None):
                raise ValueError(f'gate {key} and its current-subject proof must agree')
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
    # ⚠ F8a：旧链 s5→stage6.hook→s6→stage7.rename 的 spawn 链随 Stage6 与旧
    #   Stage2/4/5/7 退役整条删除；新链无审批 spawn 面（全自动单模式）。
    candidates: dict[str, list[ApprovalAction]] = {}
    overview = _READ_OVERVIEW.get()
    if overview is None:
        overview = state.overview(base, sid)
    spawns = {
        gate: [action for action in actions
               if state.resume_verdict(overview, action.kind)[0] != state.RESUME_SKIP]
        for gate, actions in candidates.items()
    }
    # ⚠ F8a：stage6.prepare 的 spawn 分支随 Stage6 整层退役删除（F6d 登记）。
    # Stage2 两份付费生成各自确认；机械准入通过后才发布下一步动作。
    # 浏览器只渲染服务端动作，不自行计算准入或语义结论。
    from novel.orchestrator import mechanical_admission as admission
    global_action = ApprovalAction(
        kind='stage2.global', label='生成 stage2 全局底座')
    blueprint_action = ApprovalAction(
        kind='stage2.blueprint', label='机械准入通过后生成 stage2 逐章骨架')
    if state.resume_verdict(overview, global_action.kind)[0] != state.RESUME_SKIP:
        spawns['s1'] = [global_action]
    elif state.resume_verdict(overview, blueprint_action.kind)[0] != state.RESUME_SKIP:
        check = admission.blueprint_generation_ready(base, sid)
        spawns['s1'] = [blueprint_action] if check.ok else []
    else:
        spawns['s1'] = []
    # Stage4 人工闸已退役（2026-09-15 全自动裁决）：spawn 只按机械准入给出
    # 下一条可执行动作。A 未落盘时给 A；A 的 typed infeasible 终态在盘时给
    # 预算内自动重采；否则 A 已当前时给 B。
    design = ApprovalAction(kind='stage4.design', label='Stage4A 盲结构设计')
    binding = ApprovalAction(kind='stage4.plan', label='Stage4B 证据绑定与计划编译')
    design_run = state.resume_verdict(overview, design.kind)[0] != state.RESUME_SKIP
    if design_run:
        spawns['s4'] = [design] if admission.blueprint_generation_ready(base, sid).ok else []
    elif current_infeasible_binding(
            _store_for(paths.resolve_novel_dir(base, sid))) is not None:
        spawns['s4'] = [ApprovalAction(
            kind='stage4.design', extra=['--replan'],
            label='Stage4B 已落盘 typed infeasible；预算内自动重采 Stage4A')]
    elif state.resume_verdict(overview, binding.kind)[0] != state.RESUME_SKIP:
        spawns['s4'] = [binding] if admission.stage4_plan_ready(base, sid).ok else []
    else:
        spawns['s4'] = []
    return spawns


def _fresh_record(sid: str, planned: list[int] | None = None) -> _ApprovalRecordV8:
    del planned  # 兼容旧调用缝；结构章集只从当前正式计划读取
    return _ApprovalRecordV8(
        sid=sid,
        gate={key: False for key in APPROVAL_GATES},
        gate_proofs={key: None for key in APPROVAL_GATES},
    )


def _view(
    base: str | os.PathLike[str], record: _ApprovalRecordV8, *,
    include_spawns: bool = True,
    operator_projection: bool = False,
) -> ApprovalDoc:
    """服务记录的唯一公开投影；public v6 明示闭合回执与 typed 裁决。

    Stage4 四道人工闸退役（2026-09-15 全自动裁决）后，design_review /
    structure 视图与 ``gate['s4']`` 只作为**存量书只读投影**继续输出；
    新书不再产生 typed 决定，s4 闸由机械准入替代（见 `_spawns` 的 s4 行）。
    """
    novel_dir = paths.resolve_novel_dir(base, record.sid)
    # DesignReviewView 的 missing/expired 也必须带当前 candidate、aggregate 与
    # repair capability；这些是 queue/inbox 的业务输入，不能为提速造一个
    # 缺字段的“轻量 missing”。operator_projection 只保留为调用意图标记；
    # 它不改变 public view 语义。
    design_review = _design_review_view(novel_dir, record)
    structure = _structure_view(novel_dir, record)
    writing_approval = _writing_view(novel_dir, record)
    closure = _closure_view(novel_dir, record)
    spawns = _spawns(base, record.sid) if include_spawns else {}
    # ⚠ F8b-3：`prepare` 字段随 Stage6 prepare 链退役从 ApprovalDoc 删除
    # （F6d 登记；F8a 先删投影产出，本批补齐模型与消费面）。
    return ApprovalDoc(
        sid=record.sid, gate=record.gate,
        writing=record.writing,
        design_review=design_review,
        structure=structure,
        writing_approval=writing_approval,
        closure=closure,
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
                or payload['v'] != 9):
            raise ValueError('盘上审批必须显式声明当前整数版本 v=9')
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
            f'审批当前 head 不是合法 v9 记录：{p}: {exc}') from exc
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


def has_record(base: str | os.PathLike[str], sid: str) -> bool:
    """是否有 durable 审批记录文件（含存量书历史账）。

    独立 writing 授权已随批次 B3 退役；本谓词只剩「盘上有没有审批账」
    这一个诚实读数，不再推断任何当前授权状态。
    """
    with _LOCK:
        try:
            return _path(base, sid).is_file()
        except (NovelError, OSError):
            return False


_GATE_ARTIFACTS = {
    's0': (STAGE0_REVISIONS,),
    's1': (STAGE1_MAIN_TEXT, STAGE1_HOOK_COPY),
    's4': (STAGE4_BLIND_CANDIDATE, STAGE4_EVIDENCE_BINDING),
    # ⚠ F8a：'s6' 项随 Stage6 退役删除（F6d 登记）；键位在 APPROVAL_GATES 保留为存量只读。
    's7': (STAGE7_NAME_MAP,),
}

_GATE_ORDER = APPROVAL_GATES
# gate.s2 已随 Stage2 语义审阅退役；Stage2 与下游之间只保留统一机械准入。
# gate.s4 已随 Stage4 四道人工闸退役（2026-09-15 全自动裁决）：不再有可写面，
# 键位只为存量书的读投影保留；Stage5 写作授权的前置改为机械事实（fresh 计划
# 与 writer context），不再依赖这张票。
_PRE_WRITING_GATES = ('s0', 's1', 's4')
_POST_WRITING_GATES = ('s5', 's6', 's7')

# s4 已随 Stage4 人工闸退役：s5 不再以任何审批票为前置（写作授权与闭合
# 回执自身的 typed 校验、机械 freshness 即准入），s6/s7 仍按定稿链闭合。
_GATE_REQUIREMENTS = {
    's1': ('s0',),
    's6': ('s5',), 's7': ('s6',),
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
            global_g=upstream.global_g,
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
    # 审批投影的只读仓没有单一「产它的节点」（跨 stage0～stage7 读数）；
    # node 只进错误前缀。曾用 'stage0.approve'，该节点 2026-09-15 退役后
    # 不能再作为 RunContext 的 node（取值面唯一落点是 NODES）。
    ctx = state.readonly_ctx(novel_dir, 'stage0.remove', _projection_code())
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
            hint=f'<producing node> --revalidate --actor system:invalid-revalidate --reason <audit>, or '
                 f'--apply-edit to fix {rel} first (both cost nothing)')
    if not path.is_file():
        raise UpstreamMissing(f'{rel} is required before approval')
    spec = artifact_spec(rel)
    if spec is None:
        raise ArtifactUnreadable(f'{rel} has no registered artifact contract')
    store.read_own(spec.model, rel)

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
            hint=f'<producing node> --revalidate --actor system:invalid-revalidate --reason <audit>, or '
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
        if rel == STAGE1_MAIN_TEXT:
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
    _require_fresh_json(store, STAGE4_BLIND_CANDIDATE)
    _require_fresh_json(store, STAGE4_EVIDENCE_BINDING)
    binding, binding_raw = store.read_own(
        EvidenceBinding, STAGE4_EVIDENCE_BINDING)
    if binding.status != 'bound':
        raise ApprovalRequired(
            '最终结构审批只接受当前 status=bound 的 EvidenceBinding')
    _require_fresh_json(store, STAGE2_CHAPTER_BLUEPRINTS)
    blueprints, blueprints_raw = store.read_own(
        ChapterBlueprints, STAGE2_CHAPTER_BLUEPRINTS)
    from novel.stages.stage4.plan import compile_plan_view
    plan, plan_raw = compile_plan_view(store)
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
    from novel.stages.stage4.plot_materials import (
        chapter_materials, chapter_materials_identity, material_identity,
    )
    from novel.stages.stage2.knowledge.grounding import read_own as read_grounding, read_registry
    _grounding, _raw = read_grounding(store)
    global_g = _grounding.model_dump(by_alias=True, mode='json')
    characters = read_registry(store).model_dump(by_alias=True, mode='json')
    main, _ = store.read_own(MainText, STAGE1_MAIN_TEXT)
    blueprint_payload = blueprints.model_dump(by_alias=True, mode='json')
    main_payload = main.model_dump(by_alias=True, mode='json')
    material_by, material_hashes = {}, {}
    for row in plan.output_chapters:
        args = (row.output_ordinal, blueprint_payload, global_g, main_payload,
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
        novel_dir, (STAGE4_BLIND_CANDIDATE, STAGE4_EVIDENCE_BINDING,
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
            STAGE1_MAIN_TEXT, STAGE4_BLIND_CANDIDATE, STAGE4_WRITER_CONTEXT,
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
    if not record.writing:
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


# ⚠ `stage5_closure_template` / `approve_writing` / `begin_writing` /
#    `writing_template` 已随批次 B3（2026-09-15 全自动裁决）退役删除：
#    闭合由 `record_stage5_auto_closure` 机械落账，写作授权并入 run 启动
#    授权；历史回执与授权只经 `_writing_view` / `_closure_view` 只读投影。


#: Stage5 写作授权退役（2026-09-15 全自动裁决，批次 B3）后的机械自报身份。
#: 写作授权并入 run 启动授权（上传即授权本书全部付费节点）；本身份只出现在
#: 机械闭合与机械 trim 处置的 typed 账里，绝不伪造任何人的决定。
STAGE5_AUTO_ACTOR = 'system:stage5-auto'


def record_stage5_auto_closure(
    base: str | os.PathLike[str], sid: str,
) -> ApprovalDoc:
    """批次 B3 机械闭合：fresh Review 工作包无阻断项时自动落 typed 回执。

    落点选 **producer 内机械闭合**（`stage5.review` 落 fresh 报告后调用）：
    单一落点、可随时幂等重放，queue/UI/gate 消费的仍是同一份 `gate.s5`
    闭合凭证。actor 用 `system:stage5-auto` 自报身份；must_appear 缺项按
    预定义规则落 `accepted_exception`（披露交付），fact/unver 整组按披露
    确认——确认不改写任何机械结论，报告与盘面照常携带全部事实。
    """
    from novel.stages.stage5.closure import closure_workpack
    novel_dir = paths.resolve_novel_dir(base, sid)
    store = _store_for(novel_dir)
    # 先做一次只读预检给出精确理由；真正的阻断复验与原子提交都发生在
    # `_commit_stage5_closure` 的书锁 + 审批文件锁内（锁不可重入，这里不占）。
    pack = closure_workpack(store)
    if blockers := pack.blockers():
        raise GateBlocked(
            'Stage5 机械闭合被当前工作包阻断：' + '；'.join(blockers)
            + '。缺章/.INVALID/阻断级判定先按各恢复面收口，再零成本重跑 '
            'stage5.review',
            invalid_path=str(store.path('stage5')))
    request = Stage5ClosureRequest(
        actor=STAGE5_AUTO_ACTOR,
        reason=(
            '机械闭合（2026-09-15 全自动裁决）：fresh Review 无缺章/'
            '.INVALID/阻断级判定，trim 面已按 degradable 预定义规则处置'
            f'（workpack {pack.workpack_sha256[:12]}）；可降级目标偏差与'
            '语义盲区随交付披露'),
        review_token=pack.review_token,
        must_appear=[
            Stage5MustAppearRulingInput(
                chapter_ordinal=row.chapter_ordinal,
                evidence_key=row.evidence_key,
                ruling='accepted_exception',
                reason='机械闭合披露：缺项按 Q-13 随交付披露，不再逐项人工裁决',
            )
            for row in sorted(
                pack.must_appear,
                key=lambda row: (row.chapter_ordinal, row.evidence_key))],
        group_acks=[
            Stage5FindingGroupRuling(group=group.group, acknowledged=True)
            for group in (pack.fact_group, pack.unver_group)
            if group is not None],
    )
    return _commit_stage5_closure(base, sid, request)


def _commit_stage5_closure(
    base: str | os.PathLike[str], sid: str, request: Stage5ClosureRequest,
) -> ApprovalDoc:
    """闭合回执的唯一原子提交；人工写面已退役，现役调用方只有机械闭合。"""
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


def _require_typed_gate_decision(store: ArtifactStore, gate: str) -> str | None:
    if gate == 's0':
        from contracts.schema.stage0 import Stage0Transform
        from novel.stages.stage0 import revisions as stage0_revisions
        transform, _ = store.read_own(Stage0Transform, STAGE0_REVISIONS)
        stage0_revisions.verify_transform(transform)
        report = stage0_revisions.snapshot_of(transform)
        # 付费防线与 stage1 启动闸用同一个谓词（eligibility 是唯一落点），
        # 不在这里复述条件——stage2 是第一个花钱的层，漏一处就是绕过闸门花钱。
        from novel.stages.stage0.eligibility import is_stage0_executable
        if not is_stage0_executable(report):
            raise ApprovalRequired(
                'stage0 mechanical admission has not passed')
    elif gate == 's1':
        from contracts.schema.stage0 import Stage0Transform
        from novel.stages.stage0 import revisions as stage0_revisions
        from novel.stages.stage1.audit import audit_surface

        transform, _ = store.read_own(Stage0Transform, STAGE0_REVISIONS)
        stage0_revisions.verify_transform(transform)
        stage0 = stage0_revisions.snapshot_of(transform)
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
    # ⚠ F8a：s6 分支随 Stage6 整层退役删除（三候选定稿链不存在；F6d 登记）。
    #   存量书的 s6 票恒 False，只读投影不再复验它。
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
            STAGE1_MAIN_TEXT, STAGE4_BLIND_CANDIDATE, STAGE4_WRITER_CONTEXT,
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
    gates = dict(record.gate)
    gate_proofs = dict(record.gate_proofs)

    def validate_gate(gate: str) -> None:
        if not gates[gate]:
            return
        if any(not gates[key] for key in _GATE_REQUIREMENTS.get(gate, ())):
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
        ):
            writing = False
            writing_proof = None

    # s5's proof builder needs the effective writing state, not a stale boolean
    # from disk. Later gates then cascade from the resulting s5 state.
    effective = record.model_copy(update={
        'gate': gates,
        'gate_proofs': gate_proofs,
        'writing': writing, 'writing_proof': writing_proof,
    })
    for gate in _POST_WRITING_GATES:
        # s6/s7 的正式事实在 Hook/NameMap 的 typed 决定中。HTTP 旧附加章只是
        # 检查单，不能让同一票因来自 CLI/Agent 而变成未审；s5 由机械闭合回执
        # 打开（批次 B3），不再依赖任何写作授权。
        final_gate = gate in ('s6', 's7')
        if not final_gate and not gates[gate]:
            continue
        if any(not gates[key] for key in _GATE_REQUIREMENTS.get(gate, ())):
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
        raise UpstreamMissing(
            f'{STAGE4_BLIND_CANDIDATE} (Design) is required before stage5 approval')
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
            hint='stage5.write --revalidate --actor system:invalid-revalidate --reason <audit>, '
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
                    {STAGE4_PLAN_CHAPTERS, STAGE4_EVIDENCE_BINDING},
                    {STAGE4_BLIND_CANDIDATE, STAGE4_EVIDENCE_BINDING})
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
    models = {
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
                if (set(stored.proof.artifacts) not in (
                        {STAGE1_MAIN_TEXT, STAGE4_PLAN_CHAPTERS, STAGE4_WRITER_CONTEXT},
                        {STAGE1_MAIN_TEXT, STAGE4_BLIND_CANDIDATE, STAGE4_WRITER_CONTEXT})
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


#: metric 行的 finding 超过这个长度就只保留摘要：整书逐章 JSON 已按章并入
#: coverage[].reading.structure_metrics，再在 detail 与 refs 各抄一份只多花 token。
_METRIC_INLINE_CHARS = 300


def _structure_inheritance_history(record: _ApprovalRecordV8) -> list[review_inheritance.DecisionReceipt]:
    return [review_inheritance.DecisionReceipt(
        stored.proof.decision_sha256,
        {str(row.output_ordinal): _review_decision_unit(
            row, stored.chapter_subject_sha256.get(row.output_ordinal, f'whole:{stored.subject_sha256}'))
            for row in stored.chapters},
        stored.proof.decision_sha256 == _decision_sha256(_structure_decision_payload(stored)))
        for stored in record.structure_history]


_DESIGN_STRATEGY_TEMPLATE_REASON = \
    '关闭式模板：尚未确认当前整书策略，保持 request_replan'
_DESIGN_CHAPTER_TEMPLATE_REASON = \
    '关闭式模板：尚未确认当前章设计，保持 request_repair'
_DESIGN_CHAPTER_UNREPAIRABLE_TEMPLATE_REASON = \
    '关闭式模板：当前章不能执行 targeted repair，须确认接受或改走策略 replan'


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


def cast_infeasible_replan_scope(
    base: str | os.PathLike[str], sid: str,
) -> DesignRepairScope:
    """把当前 fresh typed infeasible 终态铸成预算内自动 replan 的唯一 scope。

    2026-09-15 全自动裁决：B 的 infeasible 结论不再等人提交 design
    resolution——代码直接把不可行证明翻译成关闭式 typed 策略约束
    （``replace_reconstruction_strategy``，机器无权伪造逐章语义反馈），
    生产者按与人工 replan 完全相同的 typed 链与机械复验执行重采。
    预算消耗与耗尽行为同普通付费 design 工作单元；本函数只读、不锁写。
    """
    with _LOCK:
        novel_dir = paths.resolve_novel_dir(base, sid)
        store = _store_for(novel_dir)
        binding = current_infeasible_binding(store)
        if binding is None:
            raise ApprovalRequired(
                '当前没有 fresh typed infeasible 终态；不能铸造自动 replan scope')
        current = _current_design_subjects(novel_dir)
        if not current.repair_capability.is_v3 or current.strategy_epoch_sha256 is None:
            raise ApprovalRequired(
                '当前 Stage4A 候选不是 v3+ 世代；自动 replan 只支持现役契约候选')
        constraint = DesignStrategyReplanConstraint(
            kind='replace_reconstruction_strategy',
            subject_sha256=current.strategy_subject_sha256,
            chapter_ordinals=[],
        )
        constraint_set_sha256 = _decision_sha256({
            'kind': 'stage4-design-strategy-feedback-set-v1',
            'strategy_epoch_sha256': current.strategy_epoch_sha256,
            'constraints': [constraint.model_dump(mode='json')],
        })
        feedback = DesignStrategyReplanFeedback(
            new_constraints=[constraint],
            accumulated_constraints=[constraint],
            constraint_set_sha256=constraint_set_sha256,
            accepted_chapters=[],
            chapter_failures=[],
        )
        decision_sha256 = _decision_sha256({
            'kind': 'stage4-infeasible-auto-replan-v1',
            'candidate_sha256': current.candidate_sha256,
            'aggregate_subject_sha256': current.aggregate_subject_sha256,
            'binding_infeasibility': binding.model_dump(mode='json'),
            'constraint_set_sha256': constraint_set_sha256,
        })
        return DesignRepairScope(
            candidate_sha256=current.candidate_sha256,
            aggregate_subject_sha256=current.aggregate_subject_sha256,
            decision_sha256=decision_sha256,
            binding_infeasibility=binding,
            repair_capability=current.repair_capability,
            strategy_epoch_sha256=current.strategy_epoch_sha256,
            strategy_replan=True,
            replan_feedback=feedback,
            target_chapters=[],
            chapters=[],
        )


def approve_gate(base: str | os.PathLike[str], sid: str, gate: str) -> ApprovalDoc:
    if gate not in APPROVAL_GATES:
        raise ApprovalRequired(f'unknown approval gate: {gate}')
    if gate == 's4':
        # s4 可写面已随 Stage4 四道人工闸退役（2026-09-15 全自动裁决）；
        # 键位只为存量书读投影保留，任何新写都是关闭式拒绝。
        raise ApprovalRequired(
            'gate.s4 已退役：Stage4 的唯一打开条件是统一机械准入'
            '（novel/orchestrator/mechanical_admission.py），不再接受任何审批写面')
    if gate == 's5':
        # 人工回执写面已随批次 B3 退役（2026-09-15 全自动裁决）；s5 唯一的
        # 打开方式是 `stage5.review` producer 的机械闭合（system:stage5-auto）。
        raise ApprovalRequired(
            'gate.s5 不接受人工盖章：Stage5 闭合由 Review 机械判定自动落账'
            '（approvals.record_stage5_auto_closure）；缺章/.INVALID/阻断级'
            '判定先走各自恢复面，再零成本重跑 stage5.review')
    with approval_file_lock(base, sid):
        raw, record = _read_state(base, sid)
        if missing := [
            key for key in _GATE_REQUIREMENTS.get(gate, ()) if not record.gate[key]
        ]:
            raise ApprovalRequired(f'{gate} requires prior approvals: {missing}')
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
        # backfill 只能补“产物已落成”的链。先把盘上历史布尔投影成 current。
        record = _effective_record(novel_dir, raw_record)
        # durable maps 从 raw 起步；另保留一套 effective maps 只用于本次前置判断。
        # 否则显式 backfill 一个上游 gate 也会顺手擦掉暂时 suspended 的下游事实。
        gates = dict(raw_record.gate)
        gate_proofs = dict(raw_record.gate_proofs)
        writing = raw_record.writing
        writing_proof = raw_record.writing_proof
        current_gates = dict(record.gate)
        current_gate_proofs = dict(record.gate_proofs)
        current_writing = record.writing
        current_writing_proof = record.writing_proof
        backfilled: list[str] = []

        for gate in _GATE_ORDER:
            if current_gates[gate]:
                continue
            # Stage4 人工闸已整体退役（2026-09-15 全自动裁决）：s4 永远由
            # 机械准入替代，不接受补录；s6/s7 已直接投影 current typed 定稿，
            # 无需补一个前端点击章；真缺票也不能靠补录代审。
            # 其它检查单补录仍须显式操作，旧审批版本不迁移。
            if gate in ('s4', 's6', 's7'):
                continue
            if any(
                not current_gates[key]
                for key in _GATE_REQUIREMENTS.get(gate, ())
            ):
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
    """写作授权主体的 current 判据；人工写面已退役，仅剩历史 proof 复验消费。"""
    novel_dir = paths.resolve_novel_dir(base, sid)
    _require_stage5_contract_compatible(novel_dir, action='授权新作业')
    # stage4 人工结构票已退役（2026-09-15 全自动裁决）：写作前置只认机械事实——
    # context 必须是当前最终 Plan 的免费重建产物，其 fresh 完整字节由下面的
    # `_require_fresh_json` 关闭式冻结进写作 proof。
    planned = _planned(base, sid)
    if planned is None:
        raise UpstreamMissing(
            f'{STAGE4_BLIND_CANDIDATE} (Design) is required before writing')
    if not planned:
        raise ApprovalRequired('stage4 plan contains no output chapters')
    _require_fresh_json(_store_for(novel_dir), STAGE4_WRITER_CONTEXT)
    subject, proof = _current_writing_subject(novel_dir)
    return novel_dir, subject, proof


def authorize_writing(
    base: str | os.PathLike[str],
    sid: str,
    chapters: list[int] | None,
    *,
    requires_fresh_review: bool = False,
    reviewer_rewrite_request_rel: str | None = None,
) -> None:
    """启动付费 Stage5 作业前的机械授权校验；不再替人签字，也不再读任何审批票。

    独立 writing 授权已随批次 B3（2026-09-15 全自动裁决）并入 run 启动授权
    （上传即授权本书全部付费节点）。这里保留的是**机械事实**部分：writer
    context 必须由当前正式计划免费重建且字节未漂移，章集必须在计划内，
    revise 必须有 fresh Review，reviewer rewrite 只能整批精确授权。
    """
    with _LOCK:
        novel_dir = paths.resolve_novel_dir(base, sid)
        store, current_review = _require_stage5_contract_compatible(
            novel_dir, action='触发新付费作业')
        _require_fresh_json(_store_for(novel_dir), STAGE4_WRITER_CONTEXT)
        planned = _planned(base, sid)
        if planned is None:
            raise UpstreamMissing(
            f'{STAGE4_BLIND_CANDIDATE} (Design) is required before writing')
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

    独立 writing 授权已随批次 B3 退役；机械事实即前置——writer context
    必须由当前正式计划免费重建且字节未漂移，章集必须在计划内。与新付费
    写作唯一不同的是不要求既有 Stage5 Review fresh：canonical 尾窗未收口
    时，旧 Review 正常就会 stale，反向拿它堵恢复会让已经付费的 checkpoint
    永久悬空。
    """
    with _LOCK:
        novel_dir = paths.resolve_novel_dir(base, sid)
        _require_stage5_contract_compatible(
            novel_dir, action='收口 prepared checkpoint')
        _require_fresh_json(_store_for(novel_dir), STAGE4_WRITER_CONTEXT)
        planned = _planned(base, sid)
        if planned is None:
            raise UpstreamMissing(
                f'{STAGE4_BLIND_CANDIDATE} (Design) is required before prepared recovery')
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
