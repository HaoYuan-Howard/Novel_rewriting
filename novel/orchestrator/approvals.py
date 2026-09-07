#!/usr/bin/env python3
from __future__ import annotations

from contracts.artifacts import (
    STAGE0_CLEAN_TEXT, STAGE0_REVISIONS, STAGE1_HOOK_COPY, STAGE1_MAIN_TEXT,
    STAGE2_CHAPTER_BLUEPRINTS,
    RETIRED_STAGE3_STYLE_GROUNDING, STAGE2_GLOBAL_GROUNDING, STAGE2_GLOBAL_REVIEW,
    STAGE4_BLIND_CANDIDATE, STAGE4_EVIDENCE_BINDING,
    STAGE4_PLAN_CHAPTERS, STAGE4_WRITER_CONTEXT,
    STAGE6_HOOK, STAGE7_NAME_MAP, artifact_rel,
)

import hashlib
import json
import os
import threading
from collections.abc import Iterator
from contextlib import contextmanager
from contextvars import ContextVar
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Literal

from pydantic import Field, ValidationError, model_validator

from contracts.invariants import (
    DESIGN_REPAIR_CODES_RETIRED,
    APPROVAL_GATES,
    APPROVAL_SEEN,
    DESIGN_REPAIR_CODES,
    DESIGN_REVIEW_CONTRACT,
    FROZEN_V6_DESIGN_REVIEW_CONTRACT,
    STRUCTURE_CHANGE_KINDS,
    STRUCTURE_METRIC_CONTRACT,
    STRUCTURE_REVIEW_CONTRACT,
    STYLE_MEASUREMENT_CONTRACT,
)
from contracts.rules import REGISTRY
from contracts.schema import artifact_spec
from contracts.schema.api import (
    ApprovalAction,
    ApprovalBackfillResult,
    ApprovalDoc,
    ApprovalWorkpack,
    BlueprintReviewWorkpack,
    BlueprintSeenRequest,
    Stage2FinalReviewRequest, Stage2GlobalSeenRequest,
    DesignBindingInfeasibility,
    DesignAcceptedChapterConstraint,
    DesignBlindChapterTruth,
    DesignChapterReview,
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
from novel.errors import (
    ApprovalFrozen, ApprovalRequired, ArtifactUnreadable, GateBlocked, HumanEditProtected,
    NovelError, UpstreamMissing,
)
from novel.llm import stage5_runs
from novel.orchestrator import state
from novel.quality.global_review import (
    review_status as global_review_status,
    subject_sha256 as global_subject_sha256,
)
from novel.stages.stage4.phase_state import current_infeasible_binding
from novel.store import ArtifactStore

APPROVALS_DIRNAME = 'approvals'
_LOCK = threading.RLock()
_READ_CODE: ContextVar[str | None] = ContextVar(
    'approval_read_code', default=None)
_READ_OVERVIEW: ContextVar[NovelOverview | None] = ContextVar(
    'approval_read_overview', default=None)
_LEGACY_APPROVAL_GATES = ('s0', 's1', 's2', 's3', 's4', 's5', 's6', 's7')

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


class _ApprovalRecordV2Legacy(StrictModel):
    """只为显式 backfill 读取的 v2 记录；普通 GET/启动绝不迁移它。"""

    v: Literal[2]
    sid: str = Field(min_length=1)
    gate: dict[str, bool]
    seen: dict[str, bool]
    deep: list[int] = Field(default_factory=list)
    writing: bool = False
    gate_proofs: dict[str, _ApprovalProof | None]
    seen_proofs: dict[str, _ApprovalProof | None]
    deep_proof: _ApprovalProof | None = None
    writing_proof: _ApprovalProof | None = None

    @model_validator(mode='after')
    def _legacy_record_is_complete(self) -> _ApprovalRecordV2Legacy:
        if (set(self.gate) != set(_LEGACY_APPROVAL_GATES)
                or set(self.gate_proofs) != set(_LEGACY_APPROVAL_GATES)):
            raise ValueError('legacy gate/proofs must contain exactly s0..s7')
        if set(self.seen) != set(APPROVAL_SEEN) or set(self.seen_proofs) != set(APPROVAL_SEEN):
            raise ValueError('legacy seen/proofs must contain exactly read/global')
        if self.deep != sorted(set(self.deep)) or any(n < 1 for n in self.deep):
            raise ValueError('legacy deep must be sorted unique positive ordinals')
        return self


class _ApprovalRecordV3Legacy(StrictModel):
    """只为显式 backfill 读取的旧 v3；它没有 typed Stage4 结构裁决。"""

    v: Literal[3]
    sid: str = Field(min_length=1)
    gate: dict[str, bool]
    seen: dict[str, bool]
    writing: bool = False
    gate_proofs: dict[str, _ApprovalProof | None]
    seen_proofs: dict[str, _ApprovalProof | None]
    writing_proof: _ApprovalProof | None = None

    @model_validator(mode='after')
    def _record_is_complete_and_proved(self) -> _ApprovalRecordV3Legacy:
        if set(self.gate) != set(_LEGACY_APPROVAL_GATES):
            raise ValueError('gate must contain exactly s0..s7')
        if set(self.seen) != set(APPROVAL_SEEN):
            raise ValueError('seen must contain exactly read/global')
        if set(self.gate_proofs) != set(_LEGACY_APPROVAL_GATES):
            raise ValueError('gate_proofs must contain exactly s0..s7')
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


class _ApprovalRecordV4Legacy(StrictModel):
    """只为显式 backfill 读取的旧 v4；它仍含退役的 s3 闸门。"""

    v: Literal[4] = 4
    sid: str = Field(min_length=1)
    gate: dict[str, bool]
    seen: dict[str, bool]
    writing: bool = False
    gate_proofs: dict[str, _ApprovalProof | None]
    seen_proofs: dict[str, _ApprovalProof | None]
    writing_proof: _ApprovalProof | None = None
    structure_history: list[_StoredStructureDecision] = Field(default_factory=list)
    structure_decision: _StoredStructureDecision | None = None

    @model_validator(mode='after')
    def _record_is_complete_and_proved(self) -> _ApprovalRecordV4Legacy:
        if set(self.gate) != set(_LEGACY_APPROVAL_GATES):
            raise ValueError('gate must contain exactly s0..s7')
        if set(self.seen) != set(APPROVAL_SEEN):
            raise ValueError('seen must contain exactly read/global')
        if set(self.gate_proofs) != set(_LEGACY_APPROVAL_GATES):
            raise ValueError('gate_proofs must contain exactly s0..s7')
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
        if self.gate['s4']:
            decision = self.structure_decision
            if decision is None or any(
                row.decision != 'accept' for row in decision.chapters
            ):
                raise ValueError('s4 gate 只能由逐章全 accept 的 typed 结构裁决打开')
            if self.gate_proofs['s4'] != decision.proof:
                raise ValueError('s4 gate proof 必须等于当前 typed 结构裁决 proof')
        return self


class _ApprovalRecordV5Legacy(StrictModel):
    """只为显式 backfill 读取的旧 v5；它还没有 Stage4A typed 盲审。"""

    v: Literal[5] = 5
    sid: str = Field(min_length=1)
    gate: dict[str, bool]
    seen: dict[str, bool]
    writing: bool = False
    gate_proofs: dict[str, _ApprovalProof | None]
    seen_proofs: dict[str, _ApprovalProof | None]
    writing_proof: _ApprovalProof | None = None
    structure_history: list[_StoredStructureDecision] = Field(default_factory=list)
    structure_decision: _StoredStructureDecision | None = None
    # v5 初版只有 writing/writing_proof。以下两项是同一 v5 服务记录上的
    # additive 审计面；默认值保证旧记录仍可读，但新的正式入口绝不匿名补签。
    writing_history: list[_StoredWritingDecision] = Field(default_factory=list)
    writing_decision: _StoredWritingDecision | None = None

    @model_validator(mode='after')
    def _record_is_complete_and_proved(self) -> _ApprovalRecordV5Legacy:
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


class _ApprovalRecordV6Legacy(_ApprovalRecordV5Legacy):
    """只为显式 backfill 读取的旧 v6；它还没有 Stage5 typed 闭合回执。"""

    v: Literal[6] = 6
    design_history: list[_StoredDesignDecision] = Field(default_factory=list)
    design_decision: _StoredDesignDecision | None = None

    @model_validator(mode='after')
    def _design_history_has_one_current_tail(self) -> _ApprovalRecordV6Legacy:
        if (self.design_decision is None) != (not self.design_history):
            raise ValueError('design_decision 与非空 design_history 必须同进同出')
        if (
            self.design_decision is not None
            and self.design_decision != self.design_history[-1]
        ):
            raise ValueError('design_decision 必须是 design_history 最后一项')
        return self


class _ApprovalRecordV7(_ApprovalRecordV6Legacy):
    """盘上审批记录 v7；``gate.s5`` 只能由当前 typed closure 回执打开。"""

    v: Literal[7] = 7
    #: 每个 seen 视图最近一次确认的明文主体（token / actor）；proof 仍是判当前
    #: 的唯一依据，这里只留审计：谁在哪份 review 上签的字。旧 v7 字节缺此格时
    #: 按空读取，不伪造历史 actor。
    seen_subjects: dict[str, dict[str, str]] = Field(default_factory=dict)
    # v7 旧字节仍按完整决定读取；新决定使用 slim_v1，不再复制整份 subject。
    design_history: list[_StoredDesignHistory] = Field(default_factory=list)
    design_decision: _StoredDesignHistory | None = None
    closure_history: list[_StoredClosureDecision] = Field(default_factory=list)
    closure_decision: _StoredClosureDecision | None = None

    @model_validator(mode='after')
    def _closure_history_has_one_current_tail(self) -> _ApprovalRecordV7:
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
def approval_file_lock(
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
def _read_projection(
    *, overview: NovelOverview | None, code: str | None,
) -> Iterator[None]:
    """把同一次 operator 快照限制在当前只读投影调用栈内。"""
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
    # Stage2 两份付费生成各自确认、顺序落盘，但 global 的人工阅读不再挡住
    # blueprint。global 已当前有效时，已完成的 s1 直接下发唯一 continuation；
    # 浏览器只渲染服务端动作，不从页面是否看过/复核是否 PASS 反推产物状态。
    global_action = ApprovalAction(
        kind='stage2.global', label='生成 stage2 全局底座；完成后逐项人工复核')
    blueprint_action = ApprovalAction(
        kind='stage2.blueprint', label='生成 stage2 逐章骨架；可与全局人工复核并行')
    if state.resume_verdict(overview, global_action.kind)[0] != state.RESUME_SKIP:
        spawns['s1'] = [global_action]
    elif state.resume_verdict(overview, blueprint_action.kind)[0] != state.RESUME_SKIP:
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


def _fresh_record(sid: str, planned: list[int] | None = None) -> _ApprovalRecordV7:
    del planned  # 兼容旧调用缝；结构章集只从当前正式计划读取
    return _ApprovalRecordV7(
        sid=sid,
        gate={key: False for key in APPROVAL_GATES},
        seen={key: False for key in APPROVAL_SEEN},
        gate_proofs={key: None for key in APPROVAL_GATES},
        seen_proofs={key: None for key in APPROVAL_SEEN},
    )


def _view(
    base: str | os.PathLike[str], record: _ApprovalRecordV7, *,
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
) -> _ApprovalRecordV7:
    paths.resolve_novel_dir(base, sid)
    p = _path(base, sid)
    if not p.is_file():
        if require_existing:
            raise HumanEditProtected('正式审批在已提交回执复验期间消失，不能推断未提交')
        return _fresh_record(sid, _planned(base, sid))
    try:
        record = _ApprovalRecordV7.model_validate_json(p.read_text(encoding='utf-8'))
    except (OSError, UnicodeDecodeError, ValidationError) as exc:
        raise HumanEditProtected(
            f'approval record is corrupt or is a pre-v7 version (v1..v6 需要'
            f' 显式 backfill，普通读取不得静默迁移)；'
            f'reset/backfill explicitly: {exc}') from exc
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
        record = _ApprovalRecordV7.model_validate_json(raw)
    except ValidationError as exc:
        raise ArtifactUnreadable(
            f'审批当前 head 不是合法 v7 记录：{p}: {exc}') from exc
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


def _read_current(base: str | os.PathLike[str], sid: str) -> _ApprovalRecordV7:
    return _read_state(base, sid)[1]


def _read_state(
    base: str | os.PathLike[str], sid: str,
) -> tuple[_ApprovalRecordV7, _ApprovalRecordV7]:
    """同时给出 durable raw fact 与只读 effective 投影。

    freshness / prerequisite 漂移只能让 ``effective`` 暂停，绝不能把派生出的
    ``False`` 再写回盘。所有 mutation 都必须用 effective 作前置校验、在 raw
    上作定点更新；这是审批可以在 exact subject 回退后自动复活的根本边界。
    """
    raw = _read_raw(base, sid)
    if not _path(base, sid).is_file():
        return raw, raw
    novel_dir = paths.resolve_novel_dir(base, sid)
    return raw, _effective_record(novel_dir, raw)


def _write(
    base: str | os.PathLike[str], record: _ApprovalRecordV7,
) -> ApprovalDoc:
    """审批 raw fact 的唯一落点；返回值始终是落盘后的 effective 投影。"""
    record = _ApprovalRecordV7.model_validate(record.model_dump())
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
    新增一份机械 check 或自行解释 PASS/UNVER。``stage7.assemble`` 还复用其产地
    的唯一 frozen-v6 delivery 判据，让 exact 历史书可零 wire 组装，但不把该例外
    泛化给其他下游节点。
    """
    novel_dir = paths.resolve_novel_dir(base, sid)
    ctx = state.readonly_ctx(novel_dir, node, _projection_code())
    if node == 'stage7.assemble':
        from novel.stages.stage7.assemble import require_stage2_delivery_approved
        require_stage2_delivery_approved(ctx)
        return
    from novel.stages.stage2 import global_review
    global_review.require_approved(ctx)


_GATE_ARTIFACTS = {
    's0': (STAGE0_REVISIONS,),
    's1': (STAGE1_MAIN_TEXT, STAGE1_HOOK_COPY),
    's2': (STAGE2_GLOBAL_GROUNDING, STAGE2_CHAPTER_BLUEPRINTS, STAGE2_GLOBAL_REVIEW),
    's4': (STAGE4_PLAN_CHAPTERS,),
    's6': (STAGE6_HOOK,),
    's7': (STAGE7_NAME_MAP,),
}

_GATE_ORDER = APPROVAL_GATES
_PRE_WRITING_GATES = ('s0', 's1', 's2', 's4')
_POST_WRITING_GATES = ('s5', 's6', 's7')
_LEGACY_GATE_ARTIFACTS = {
    **_GATE_ARTIFACTS,
    's3': (RETIRED_STAGE3_STYLE_GROUNDING,),
}

_GATE_REQUIREMENTS = {
    's1': ('s0',), 's2': ('s1',), 's4': ('s2',),
    's5': ('s4',), 's6': ('s5',), 's7': ('s6',),
}

_SEEN_ARTIFACTS = {
    'read': (STAGE2_CHAPTER_BLUEPRINTS,),
    # ``seen.global`` 绑定 Global 主体**与 review 完整字节**：review 任何一格
    # 状态/理由变化都让旧阅读事实过期，独立终审必须重读。
    'global': (STAGE2_GLOBAL_GROUNDING, STAGE2_GLOBAL_REVIEW),
}


def _decision_sha256(value: Any) -> str:
    raw = json.dumps(
        value, ensure_ascii=False, sort_keys=True, separators=(',', ':'),
    ).encode('utf-8')
    return hashlib.sha256(raw).hexdigest()


class _DesignSubjects(StrictModel):
    """当前 Stage4A 候选一次读取所得的完整人审 subject 集。"""

    #: 只表示这份人审工作包来自“原始 v6 字节 ↔ 现存 Stage4A v1 truth”
    #: 的关闭式证明。它不是 current v7，也不能进入任何付费修订/重规划。
    legacy_frozen_v6: bool = False
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
    plan_payload_sha256: str = Field(pattern=r'^[0-9a-f]{64}$')
    binding_payload_sha256: str = Field(pattern=r'^[0-9a-f]{64}$')
    blueprint_payload_sha256: str = Field(pattern=r'^[0-9a-f]{64}$')


def _formal_design_chapter(
    chapter: Any,
    fact_aliases: dict[str, str],
) -> dict[str, Any]:
    """只在内存中把 A 的 opaque alias 还原给唯一结构量尺。"""

    def fact(alias: str, where: str) -> str:
        formal = fact_aliases.get(alias)
        if formal is None:
            raise ArtifactUnreadable(f'{where} 引用了未知 opaque alias {alias}')
        return formal

    def goal(value: Any, where: str) -> dict[str, Any]:
        return {
            'intent': value.intent,
            'fact_refs': [
                {'kind': 'fact_atom', 'ref': fact(alias, where)}
                for alias in value.fact_aliases
            ],
        }

    scenes = []
    for scene in chapter.scenes:
        scenes.append({
            'slot': scene.candidate_scene_id,
            'land_fact_refs': [
                fact(alias, f'{scene.candidate_scene_id}.land_fact_aliases')
                for alias in scene.land_fact_aliases
            ],
            'context_fact_refs': [
                fact(alias, f'{scene.candidate_scene_id}.context_fact_aliases')
                for alias in scene.context_fact_aliases
            ],
            'entry_state_refs': [
                fact(alias, f'{scene.candidate_scene_id}.entry_state_aliases')
                for alias in scene.entry_state_aliases
            ],
            'exit_state_refs': [
                fact(alias, f'{scene.candidate_scene_id}.exit_state_aliases')
                for alias in scene.exit_state_aliases
            ],
        })
    return {
        'output_ordinal': chapter.output_ordinal,
        'structure_decision': chapter.structure_decision.model_dump(mode='json'),
        'chapter_goal': goal(chapter.chapter_goal, 'chapter_goal.fact_aliases'),
        'open_hook_goal': goal(chapter.open_hook_goal, 'open_hook_goal.fact_aliases'),
        'end_hook_goal': goal(chapter.end_hook_goal, 'end_hook_goal.fact_aliases'),
        'scenes': scenes,
    }


def _safe_structure_evidence(
    blueprint: dict[str, Any], chapter: Any, fact_aliases: dict[str, str],
) -> DesignStructureEvidence:
    """复用正式量尺，仅发布无 formal id 的标量与关闭式变化集合。"""
    from novel.stages.stage4.measure import (
        computed_structure_change_kinds, structure_metrics_of,
    )

    metrics = structure_metrics_of(
        blueprint, _formal_design_chapter(chapter, fact_aliases))
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
        'land_facts': scalar('mapping', 'land_facts'),
        'mapped_facts': scalar('mapping', 'mapped_facts'),
        'plan_scenes': scalar('mapping', 'plan_scenes'),
        'partition_state': scalar('partition', 'state'),
        'all_fact_pairs': scalar('partition', 'all_fact_pairs'),
        'changed_fact_pairs': scalar('partition', 'changed_pairs'),
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

    from novel.stages.stage4.blind import (
        BlindPlanError, build_blind_projection, candidate_subject_sha256,
    )
    from novel.stages.stage4.design_evolution import (
        chapter_strategy_sha256, strategy_epoch_sha256,
    )
    root_clean_sha1 = _root_clean_sha1(novel_dir)
    legacy_frozen_v6 = store.schema_version(STAGE2_CHAPTER_BLUEPRINTS) == 6
    if legacy_frozen_v6:
        from novel.stages.stage2.frozen_v6 import (
            LEGACY_FROZEN_REVIEW_CONTRACT,
            require_frozen_v6_stage4_subject,
        )
        frozen = require_frozen_v6_stage4_subject(store)
        if (
            frozen.candidate_sha256 != candidate_sha256
            or frozen.candidate != candidate
        ):
            raise ArtifactUnreadable(
                '冻结 v6 证明绑定的 Stage4A candidate 与审批现场读取不一致')
        blueprint_payload = frozen.blueprint_payload
        blind_truth = frozen.truth
        current_mapping = frozen.mapping
        blueprint_by_ordinal = {
            row.chapter_ordinal: row.model_dump(mode='json')
            for row in frozen.blueprint.blueprints
        }
        review_contract = LEGACY_FROZEN_REVIEW_CONTRACT
        repair_capability = _current_design_repair_capability(
            store, candidate=candidate,
            candidate_file_sha256=candidate_sha256, ordinals=ordinals,
            agent_disabled_reason_code='legacy_frozen_v6_human_edit_only')
        # 普通 v3 preflight 会把 v6 填成 current ChapterBlueprints；冻结兼容
        # 明确不做这件事。None 令所有 accept/B 授权继续关闭，只开放 typed edit。
        preflight = None
    else:
        _require_fresh_json(store, STAGE2_CHAPTER_BLUEPRINTS)
        blueprints, _blueprints_raw = store.read_own(
            ChapterBlueprints, STAGE2_CHAPTER_BLUEPRINTS)
        blueprint_payload = blueprints.model_dump(mode='json', by_alias=True)
        try:
            blind_truth, current_mapping = build_blind_projection(
                blueprint_payload, root_clean_sha1=root_clean_sha1)
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
        'fact_aliases': dict(candidate.fact_aliases),
        'participant_aliases': dict(candidate.participant_aliases),
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
    strategy_sha256 = _decision_sha256({
        'kind': 'stage4_design_strategy_subject',
        'subject': strategy.model_dump(mode='json'),
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
                chapter, candidate.fact_aliases),
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
        legacy_frozen_v6=legacy_frozen_v6,
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
        grounding, raw = store.read_own(GlobalGrounding, STAGE2_GLOBAL_GROUNDING)
        del grounding
        source = {
            key: value for key, value in raw.items()
            if key not in {'_provenance', '_schema_version'}
        }
        if review.source_sha256 != global_subject_sha256(source):
            raise ApprovalRequired('stage2 global review is bound to an older grounding subject')
    else:
        report = store.freshness(rel)
        if report.state != 'fresh':
            frozen_compatible = False
            if (
                rel in {
                    STAGE4_EVIDENCE_BINDING,
                    STAGE4_PLAN_CHAPTERS,
                    STAGE4_WRITER_CONTEXT,
                }
                and store.schema_version(STAGE2_CHAPTER_BLUEPRINTS) == 6
            ):
                from novel.stages.stage2.frozen_v6 import (
                    require_frozen_v6_downstream_subject,
                )
                require_frozen_v6_downstream_subject(store, rel)
                frozen_compatible = True
            if not frozen_compatible:
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
        root_clean_sha1=_root_clean_sha1(novel_dir),
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
            source_decision_sha256=None,
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
            source_decision_sha256=None,
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
    """读取当前正式计划+B 担忧并计算人真正审阅的结构决策面 hash。"""
    store = _store_for(novel_dir)
    _require_fresh_json(store, STAGE4_PLAN_CHAPTERS)
    plan, _raw = store.read_own(PlanChapters, STAGE4_PLAN_CHAPTERS)
    binding = _current_structure_binding(novel_dir, store=store)
    concerns = _binding_concerns(binding)
    binding_input_contract = _binding_input_contract_identity(binding)
    try:
        from novel.stages.stage4.measure import structure_review_subject_sha256
    except ImportError as exc:
        raise ArtifactUnreadable(
            'stage4 structure subject helper is unavailable') from exc
    subject = structure_review_subject_sha256(
        plan, concerns, binding_input_contract=binding_input_contract)
    if (
        not isinstance(subject, str) or len(subject) != 64
        or any(char not in '0123456789abcdef' for char in subject)
    ):
        raise ArtifactUnreadable(
            'stage4 structure subject helper did not return a full lowercase SHA-256')
    chapters = [row.output_ordinal for row in plan.output_chapters]
    if chapters != sorted(set(chapters)) or any(value < 1 for value in chapters):
        raise ArtifactUnreadable(
            'stage4 structure subject has a non-canonical output chapter set')
    return subject, chapters


def _structure_chapter_subject_sha256(
    plan: PlanChapters, concerns: list[BindingConcern], *,
    binding_input_contract: str | None = None,
) -> dict[int, str]:
    """结构人审的逐章复用主体；业务 Plan 投影复用生产唯一尺子。

    全局 concern（``chapter_ordinal=None``）影响每一章；章级 concern
    只影响它所属章。因此改一章不会让其他章的 exact accept
    凭空过期，但全局担忧变化会让全章都重审。
    """
    from novel.stages.stage4.measure import structure_subject

    projected = structure_subject(plan)
    projected_by = {
        int(row.get('output_ordinal') or 0): row
        for row in projected.get('output_chapters') or []
    }
    hashes: dict[int, str] = {}
    for chapter in plan.output_chapters:
        ordinal = chapter.output_ordinal
        applicable = [
            concern.model_dump(mode='json')
            for concern in concerns
            if concern.chapter_ordinal in (None, ordinal)
        ]
        hashes[ordinal] = _decision_sha256({
            'kind': 'stage4_structure_chapter_review_subject',
            'review_contract': STRUCTURE_REVIEW_CONTRACT,
            'chapter': projected_by[ordinal],
            'binding_concerns': applicable,
            **({'binding_input_contract': binding_input_contract}
               if binding_input_contract is not None else {}),
        })
    return hashes


def _current_structure_chapter_subject_sha256(
    novel_dir: Path,
) -> dict[int, str]:
    """从当前 fresh Plan+B 一次读取铸造逐章审阅快照。"""
    store = _store_for(novel_dir)
    _require_fresh_json(store, STAGE4_PLAN_CHAPTERS)
    plan, _raw = store.read_own(PlanChapters, STAGE4_PLAN_CHAPTERS)
    binding = _current_structure_binding(novel_dir, store=store)
    return _structure_chapter_subject_sha256(
        plan, _binding_concerns(binding),
        binding_input_contract=_binding_input_contract_identity(binding))


def _structure_workpack_snapshot(
    novel_dir: Path,
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
    concerns = _binding_concerns(binding)
    binding_input_contract = _binding_input_contract_identity(binding)
    try:
        from novel.stages.stage4.measure import structure_review_subject_sha256
    except ImportError as exc:
        raise ArtifactUnreadable(
            'stage4 structure subject helper is unavailable') from exc
    subject = structure_review_subject_sha256(
        plan, concerns, binding_input_contract=binding_input_contract)
    ordinals = [row.output_ordinal for row in plan.output_chapters]
    if ordinals != sorted(set(ordinals)) or any(value < 1 for value in ordinals):
        raise ArtifactUnreadable(
            'stage4 structure workpack has a non-canonical output chapter set')
    return _StructureWorkpackSnapshot(
        subject_sha256=subject,
        chapter_ordinals=ordinals,
        chapter_subject_sha256=_structure_chapter_subject_sha256(
            plan, concerns, binding_input_contract=binding_input_contract),
        plan=plan,
        concerns=concerns,
        binding_input_contract=binding_input_contract,
        blueprints=blueprints,
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
    """绑定正文根、完整计划/B 字节与逐章 typed 决定。"""
    return _make_proof(
        novel_dir, (STAGE4_PLAN_CHAPTERS, STAGE4_EVIDENCE_BINDING),
        _structure_decision_payload(decision),
    )


def _proof_for_legacy_structure(
    novel_dir: Path,
    decision: StructureApprovalRequest | StructureApprovalDecision,
) -> _ApprovalProof:
    """显式迁移时只用于验证旧 plan-only structure proof。"""
    return _make_proof(
        novel_dir, (STAGE4_PLAN_CHAPTERS,),
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
    novel_dir: Path, record: _ApprovalRecordV7,
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
    if state == 'accepted' and current.legacy_frozen_v6:
        # raw typed 语义结论与下游执行权分离：frozen v6 的当前
        # 主体可以被人如实审完，但它永远缺 v7 relation/preflight
        # validation authority。保留 current_decision 让 raw 事实可见，只把
        # effective 状态暂停；因此不会被 design_review_is_current/authorize_plan
        # 读成 B 授权，也不会签发任何 repair/edit action token。
        state = 'suspended'
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
                not current.legacy_frozen_v6
                and state in ('repair_requested', 'strategy_replan_requested')
            )
            else None),
        current_decision=public,
        history=history,
    )


def _structure_view(
    novel_dir: Path, record: _ApprovalRecordV7,
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
    novel_dir: Path, record: _ApprovalRecordV7,
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


def _closure_view(novel_dir: Path, record: _ApprovalRecordV7) -> Stage5ClosureView:
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
    if workpack.binding.subject_kind != 'formal':
        raise ApprovalRequired(
            'seen.read 只接受 current formal Blueprint 工作包；'
            'INVALID 工作包只能用于恢复，不能生成已读证明')
    # exact token 只守本次提交窗口；耐久 proof 只绑定可见内容与显式审阅
    # 契约。代码/rules/provenance 改变会得到新 validation seal，但只要工作包
    # 的内容/finding identity 未变，旧人工阅读事实就能在免费复检后自动复活。
    proof = _ApprovalProof(
        root_clean_sha1=_root_clean_sha1(novel_dir),
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
    novel_dir: Path,
) -> tuple[_ApprovalProof, dict[str, str]]:
    """用当前只读投影重建完整工作包，再读出 proof 与明文身份。"""
    from novel.views.stage2 import blueprint_review_workpack

    sid, _title, _platform = paths.derive_source_meta(novel_dir.name)
    workpack = blueprint_review_workpack(
        novel_dir.parent, sid, code=_projection_code())
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


def _frozen_v6_seen_read_is_current(
    novel_dir: Path, proof: _ApprovalProof | None,
) -> bool:
    """只复活盘上已有的 v6 阅读事实；绝不铸一份新的 legacy seen proof。

    旧工作包没有显式 review-contract 字段，耐久 proof 绑定的是当时真正可见的
    Blueprint 与 Stage1 正文字节。当前 v7 workpack 当然不能重建它；这里仅在
    exact v6 已被现存 Stage4A v1 truth 反向证明、且两份可见字节仍逐项相等时
    保留 raw 人工事实。任一 SHA/来源漂移都回到普通 suspended 投影。
    """
    if proof is None or set(proof.artifacts) != {
        STAGE2_CHAPTER_BLUEPRINTS, STAGE1_MAIN_TEXT,
    }:
        return False
    try:
        store = _store_for(novel_dir)
        from novel.stages.stage2.frozen_v6 import (
            require_frozen_v6_stage4_subject,
        )
        frozen = require_frozen_v6_stage4_subject(store)
        main_sha256 = _require_fresh_json(store, STAGE1_MAIN_TEXT)
        return (
            proof.root_clean_sha1 == _root_clean_sha1(novel_dir)
            and proof.artifacts[STAGE2_CHAPTER_BLUEPRINTS]
            == frozen.blueprint_sha256
            and proof.artifacts[STAGE1_MAIN_TEXT] == main_sha256
        )
    except (NovelError, OSError, UnicodeDecodeError, ValidationError, ValueError):
        return False


def _frozen_v6_s2_gate_proof(
    novel_dir: Path, record: _ApprovalRecordV7,
) -> _ApprovalProof:
    """重算一份已有 v6 s2 章的 exact content proof，不开放新盖章。

    v6 不运行 current-v7 producer/check，也不取得关系权威；它只在 raw 盘本来
    就有 s2 决定时，证明原三份 subject 字节、根与 Stage4A 历史绑定均未变化。
    """
    stored = record.gate_proofs.get('s2')
    if not record.gate.get('s2') or stored is None:
        raise ApprovalRequired(
            '冻结 v6 只能恢复已有 s2 人工事实，不能在当前契约下新盖章')
    expected_rels = {
        STAGE2_GLOBAL_GROUNDING,
        STAGE2_CHAPTER_BLUEPRINTS,
        STAGE2_GLOBAL_REVIEW,
    }
    if set(stored.artifacts) != expected_rels:
        raise ApprovalRequired('冻结 v6 的历史 s2 proof 不是可逐项对证的三主体形状')
    store = _store_for(novel_dir)
    from novel.stages.stage2.frozen_v6 import require_frozen_v6_stage4_subject
    frozen = require_frozen_v6_stage4_subject(store)
    subjects = {
        STAGE2_GLOBAL_GROUNDING: _require_fresh_json(
            store, STAGE2_GLOBAL_GROUNDING),
        STAGE2_CHAPTER_BLUEPRINTS: frozen.blueprint_sha256,
        STAGE2_GLOBAL_REVIEW: _require_fresh_json(store, STAGE2_GLOBAL_REVIEW),
    }
    return _ApprovalProof(
        root_clean_sha1=_root_clean_sha1(novel_dir),
        artifacts=subjects,
        decision_sha256=_decision_sha256({'kind': 'gate', 'gate': 's2'}),
    )


def _proof_for_legacy_depth(novel_dir: Path, deep: list[int]) -> _ApprovalProof:
    return _make_proof(
        novel_dir, (STAGE4_PLAN_CHAPTERS,),
        {'kind': 'depth_selection', 'deep': deep},
    )


def _require_legacy_stage3_fresh(novel_dir: Path) -> None:
    """冻结 v2 只读解码 + 旧 consumed 复核；不把退役文件重新注册为当前产物。"""
    store = _store_for(novel_dir)
    rel = RETIRED_STAGE3_STYLE_GROUNDING
    invalid = store.path(paths.invalid_of(rel))
    if invalid.is_file():
        raise GateBlocked(
            f'{rel} 有未裁决的历史 INVALID 孪生，旧审批不能迁移',
            invalid_path=str(invalid),
            hint='保留历史证据；按当前流程重新审 Stage4 并授权写作',
        )
    try:
        legacy = store.read_legacy_stage3_v2(rel)
    except UpstreamMissing:
        raise UpstreamMissing(f'{rel} 已不在盘上，旧 v4 写作授权不能对证迁移') from None
    report = store.freshness_of_inputs(rel, legacy.provenance.inputs)
    if report.state != 'fresh':
        details = ', '.join(f'{edge.key}:{edge.reason}' for edge in report.edges)
        raise ApprovalRequired(
            f'{rel} 的旧来源已不是 fresh（{report.reason}；{details}），不能迁移写作授权')


def _proof_for_legacy_writing(novel_dir: Path, deep: list[int]) -> _ApprovalProof:
    _require_legacy_stage3_fresh(novel_dir)
    return _make_proof(
        novel_dir, (RETIRED_STAGE3_STYLE_GROUNDING, STAGE4_PLAN_CHAPTERS),
        {'kind': 'writing', 'deep': deep},
    )


def _proof_for_writing(novel_dir: Path) -> _ApprovalProof:
    return _make_proof(
        novel_dir, (
            STAGE1_MAIN_TEXT, STAGE4_PLAN_CHAPTERS, STAGE4_WRITER_CONTEXT,
        ),
        {
            'kind': 'writing',
            'scope': 'all_planned_chapters',
            'contract': 'stage5-v3',
            'measurement_contract': STYLE_MEASUREMENT_CONTRACT,
        },
    )


def _stage5_subject_rels(
    base: str | os.PathLike[str], sid: str, record: _ApprovalRecordV7,
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
    novel_dir: Path, gate: str, record: _ApprovalRecordV7,
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
    if (
        gate == 's2'
        and store.schema_version(STAGE2_CHAPTER_BLUEPRINTS) == 6
    ):
        return _frozen_v6_s2_gate_proof(novel_dir, record)
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


def _effective_record(novel_dir: Path, record: _ApprovalRecordV7) -> _ApprovalRecordV7:
    """Return the effective current projection without rewriting audit history."""
    seen = dict(record.seen)
    seen_proofs = dict(record.seen_proofs)
    for key in APPROVAL_SEEN:
        if not seen[key]:
            continue
        if (
            key == 'read'
            and _frozen_v6_seen_read_is_current(
                novel_dir, seen_proofs.get(key))
        ):
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
        if current is None or not _same_proof(gate_proofs[gate], current):
            gates[gate] = False
            gate_proofs[gate] = None

    for gate in _PRE_WRITING_GATES:
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
        if not gates[gate]:
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
            current = _proof_for_gate(novel_dir, gate, effective)
        except NovelError:
            current = None
        if current is None or not _same_proof(gate_proofs[gate], current):
            gates[gate] = False
            gate_proofs[gate] = None
        effective = effective.model_copy(update={
            'gate': gates, 'gate_proofs': gate_proofs,
        })
    return effective


def _require_artifacts(
    base: str | os.PathLike[str], sid: str, gate: str,
    record: _ApprovalRecordV7,
) -> _ApprovalProof:
    novel_dir = paths.resolve_novel_dir(base, sid)
    return _proof_for_gate(novel_dir, gate, record)


def _require_stage5_complete(
    base: str | os.PathLike[str], sid: str, record: _ApprovalRecordV7,
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


def _accepted_design_strategy_source(
    record: _ApprovalRecordV7, current: _DesignSubjects,
) -> _StoredDesignHistory | None:
    for stored in reversed(record.design_history):
        if isinstance(stored, _StoredDesignDecisionSlim):
            accepted = (
                stored.request.strategy.decision == 'accept'
                and stored.strategy_subject_sha256 == current.strategy_subject_sha256
                and stored.strategy_epoch_sha256 == current.strategy_epoch_sha256
            )
        else:
            public = _public_design_decision(stored)
            accepted = (
                public.strategy.decision == 'accept'
                and public.strategy.subject == current.strategy
                and public.strategy.subject_sha256 == current.strategy_subject_sha256
            )
        if (
            _stored_design_is_self_consistent(stored)
            and accepted
        ):
            return stored
    return None


def _accepted_design_chapter_source(
    record: _ApprovalRecordV7,
    subject: DesignChapterSubject,
    subject_sha256: str,
) -> tuple[_StoredDesignHistory, DesignChapterReview | Any] | None:
    ordinal = subject.chapter.output_ordinal
    for stored in reversed(record.design_history):
        if not _stored_design_is_self_consistent(stored):
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
        if (
            row is not None
            and row.decision == 'accept'
            and same_subject
        ):
            return stored, row
    return None


def _design_chapter_business_subject(
    subject: DesignChapterSubject,
) -> dict[str, Any]:
    """去掉随 review contract 变化的策略身份，只保留一章可见业务主体。"""
    return subject.model_dump(mode='json', exclude={
        'strategy_subject_sha256',
        'strategy_epoch_sha256',
        'chapter_strategy_sha256',
    })


def _frozen_v6_design_suggestion(
    record: _ApprovalRecordV7, current: _DesignSubjects, novel_dir: Path,
) -> DesignReviewDecision | None:
    """把 exact 旧决定仅作为新 legacy-frozen 工作包的 typed 建议。

    review contract 已变化，所以这里绝不复用 accept、绝不铸 edit token；所有行
    仍带 placeholder，必须由人一次性显式确认。它只避免 ch4/ch6/ch8 这类既有
    generic constraint 在恢复时丢失，且 relation effect 永不跨进无权威的 v6。
    """
    if not current.legacy_frozen_v6:
        return None

    current_strategy = current.strategy.model_dump(mode='json')
    current_strategy['basis'].pop('review_contract', None)
    current_by = {
        row.chapter.output_ordinal: row for row in current.chapters
    }
    for stored in reversed(record.design_history):
        if (
            isinstance(stored, _StoredDesignDecisionSlim)
            or stored.decision_sha256 != stored.proof.decision_sha256
        ):
            continue
        public = _public_design_decision(stored)
        if (
            stored.proof.root_clean_sha1 != _root_clean_sha1(novel_dir)
            or stored.proof.artifacts.get(STAGE4_BLIND_CANDIDATE)
            != public.candidate_sha256
        ):
            continue
        proposed_strategy = public.strategy.subject.model_dump(mode='json')
        proposed_strategy['basis'].pop('review_contract', None)
        if proposed_strategy != current_strategy:
            continue
        if {row.output_ordinal for row in public.chapters} != set(current_by):
            continue
        compatible = True
        for row in public.chapters:
            subject = current_by[row.output_ordinal]
            if row.subject.blind_truth != subject.blind_truth:
                compatible = False
                break
            scenes = {
                scene.candidate_scene_id for scene in subject.chapter.scenes}
            aliases = {
                fact.fact_alias for fact in subject.blind_truth.facts
            } | {
                alias
                for fact in subject.blind_truth.facts
                for alias in fact.participant_aliases
            }
            if any(
                constraint.effect is not None
                or constraint.code == 'narrative_relation'
                or not set(constraint.scene_refs).issubset(scenes)
                or not set(constraint.opaque_aliases).issubset(aliases)
                for constraint in row.constraints
            ):
                compatible = False
                break
        if not compatible:
            continue
        return public
    return None


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
    record: _ApprovalRecordV7, current: _DesignSubjects, novel_dir: Path,
) -> list[WorkpackGroup]:
    blocking: list[WorkpackItem] = []
    attention: list[WorkpackItem] = []
    disclosure: list[WorkpackItem] = []
    metrics: list[WorkpackItem] = []

    if current.legacy_frozen_v6:
        disclosure.append(WorkpackItem(
            code='legacy_frozen_v6_no_relation_authority',
            rule=None,
            sev='info',
            status='hit',
            repair_owner=None,
            detail=(
                '本工作包来自 exact v6 Blueprint 与现存 Stage4A v1 truth 的'
                '冻结绑定；narrative_relations 不在其审阅权威面。可如实记录 '
                'raw all-accept，但 effective 投影保持 suspended，不授权 '
                'Stage4B。只有真实 request_repair 才签发零 wire 完整章'
                '替换票；不得为获票伪造问题，也不得手补关系、付费 '
                'repair/replan。'),
            effect='disclose',
            allowed_actions=[],
        ))

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
                *truth.ending_state_aliases,
                *(edge.before_alias for edge in truth.causal_edges),
                *(edge.after_alias for edge in truth.causal_edges),
            ],
            detail=_compact({
                'entry_state_aliases': truth.entry_state_aliases,
                'ending_state_aliases': truth.ending_state_aliases,
                'fact_count': len(truth.facts),
                'causal_edge_count': len(truth.causal_edges),
                'narrative_relation_count': len(truth.narrative_relations),
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
                record, subject, current.chapter_subject_sha256[ordinal])
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
                view.state in ('accepted', 'suspended')
                and view.current_decision is not None
            ):
                workpack = workpack.model_copy(update={
                    'disposition': (
                        'already_current' if view.state == 'accepted'
                        else 'suspended'
                    ),
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
    record: _ApprovalRecordV7, novel_dir: Path, subject_sha256: str,
    chapter_subject_sha256: dict[int, str],
) -> dict[int, str]:
    """逐章找仍精确绑定当前业务面的历史 accept，不复用旧散文。

    v6 起可带内部逐章 subject 快照并逐项对证；当前 v7 继续承载该快照。
    缺少快照的旧记录只能在整书 subject 与完整当前 proof 都仍相同的
    情况下保守复用。
    """
    accepted: dict[int, str] = {}
    for stored in reversed(record.structure_history):
        public = _public_structure_decision(stored)
        if stored.proof.decision_sha256 != _decision_sha256(
            _structure_decision_payload(public)
        ):
            continue

        if stored.chapter_subject_sha256:
            for row in public.chapters:
                ordinal = row.output_ordinal
                if (
                    row.decision == 'accept'
                    and ordinal not in accepted
                    and stored.chapter_subject_sha256.get(ordinal)
                    == chapter_subject_sha256.get(ordinal)
                ):
                    accepted[ordinal] = stored.proof.decision_sha256
            continue

        if stored.subject_sha256 != subject_sha256:
            continue
        try:
            current_proof = _proof_for_structure(novel_dir, public)
        except NovelError:
            continue
        if not _same_proof(stored.proof, current_proof):
            continue
        for row in public.chapters:
            if row.decision == 'accept' and row.output_ordinal not in accepted:
                accepted[row.output_ordinal] = stored.proof.decision_sha256
    return accepted


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
    record: _ApprovalRecordV7, novel_dir: Path, *, subject_sha256: str,
    chapter_subject_sha256: dict[int, str],
    plan: PlanChapters, concerns: list[BindingConcern], report: Any,
    blueprint_by_ordinal: Mapping[int, Mapping[str, Any]],
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
            if line.rule == 'state_closure':
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
    coverage: list[WorkpackItem] = []
    for chapter in plan.output_chapters:
        ordinal = chapter.output_ordinal
        source_sha = accepted_sources.get(ordinal)
        reading = structure_chapter_reading(
            chapter,
            blueprint=blueprint_by_ordinal.get(ordinal) or {},
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
            snapshot = _structure_workpack_snapshot(novel_dir)
            if check_report is None:
                from novel.stages.stage4.plan import run_check_only
                report = run_check_only(state.readonly_ctx(
                    novel_dir, 'stage4.plan', _projection_code()))
            else:
                report = check_report
            # view 的读取也必须落在首尾快照之间；放到末读之后会留下一个
            # 未经 rebound 校验的第三次整读，返回的处置态可能与 groups 混版。
            view = _structure_view(novel_dir, record)
            after = _structure_workpack_snapshot(novel_dir)
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
                ),
                reading=WorkpackReading(
                    legend={
                        **workpack_legend('structure'),
                        **({'binding_input_contract': (
                            snapshot.binding_input_contract
                            + '：本次章级 B 收到场景 alias 与事实/材料的完整映射；'
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
        (fact.fact_alias for fact in subject.blind_truth.facts), None)
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
        strategy_source = (
            None if current.binding_infeasibility is not None
            else _accepted_design_strategy_source(record, current)
        )
        frozen_suggestion = (
            None if current.binding_infeasibility is not None
            else _frozen_v6_design_suggestion(record, current, novel_dir)
        )
        suggested_strategy_accept = (
            strategy_source is None
            and frozen_suggestion is not None
            and frozen_suggestion.strategy.decision == 'accept'
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
                if strategy_source is not None or suggested_strategy_accept
                else 'request_replan'
            ),
            reason=(
                '沿用仍绑定当前策略主体的历史 accept'
                if strategy_source is not None
                else '冻结 v6 历史策略仅作建议；须在本次显式确认后才生效'
                if suggested_strategy_accept
                else _DESIGN_STRATEGY_TEMPLATE_REASON
            ),
            source_decision_sha256=(
                strategy_source.decision_sha256 if strategy_source else None),
            replan_constraints=(
                [] if strategy_source is not None or suggested_strategy_accept else [
                    DesignStrategyReplanConstraint(
                        kind='replace_reconstruction_strategy',
                        subject_sha256=current.strategy_subject_sha256,
                    )
                ]
            ),
        )
        chapters: list[DesignChapterReview] = []
        suggested_chapters = {
            row.output_ordinal: row
            for row in (
                frozen_suggestion.chapters if frozen_suggestion is not None else []
            )
        }
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
                    record, subject, subject_sha)
            )
            suggested = (
                suggested_chapters.get(ordinal) if source is None else None)
            if (
                suggested is not None
                and suggested.decision == 'accept'
                and _design_chapter_business_subject(suggested.subject)
                != _design_chapter_business_subject(subject)
            ):
                # 跨 contract 的 accept 只有可见章主体逐字相同时才可作建议；
                # 不相同仍回到关闭式未审模板，不把旧 accept 偷换成新决定。
                suggested = None
            can_repair = repairable_by_ordinal[ordinal]
            suggested_constraints = (
                sorted(
                    suggested.constraints,
                    key=lambda item: json.dumps(
                        item.model_dump(mode='json'), ensure_ascii=False,
                        sort_keys=True, separators=(',', ':'),
                    ),
                )
                if suggested is not None
                and suggested.decision == 'request_repair'
                else []
            )
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
                    else suggested.decision if suggested is not None
                    else 'accept' if not can_repair
                    else 'request_repair'
                ),
                reason=(
                    '沿用仍绑定当前章主体的历史 accept'
                    if source is not None
                    else (
                        '冻结 v6 历史逐章决定仅作建议；须在本次显式确认：'
                        f'{suggested.reason}'
                    )
                    if suggested is not None
                    else _DESIGN_CHAPTER_TEMPLATE_REASON
                    if can_repair
                    else _DESIGN_CHAPTER_UNREPAIRABLE_TEMPLATE_REASON
                ),
                constraints=(
                    [] if source is not None
                    else suggested_constraints if suggested is not None
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


def _require_design_codes_active(request: DesignReviewMaterialized) -> None:
    """新决定只接受 active 修复码；退役码历史可解码，但不再可提交（批次 2）。"""
    retired = [row.output_ordinal for row in request.chapters for constraint in row.constraints
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
    if current.legacy_frozen_v6:
        # 冻结 v6 没有 current-v7 relation authority，因而永远不能得到普通
        # v3 preflight pass。这里只决定 raw 人审事实能否落账：真实
        # request_repair 继续铸造零 wire 完整章 edit token；all-accept 也可
        # 如实保存，但 _design_review_view 只投影 suspended，绝不授权 B。
        # 唯一仍被禁止的决定是 request_replan：它对 frozen v6 只有付费
        # 执行语义，而这条 paid lane 在冻结契约下必须保持关闭。
        if request.strategy.decision == 'request_replan':
            raise HumanEditProtected(
                'legacy_frozen v6 不开放付费 strategy replan；'
                '可如实提交 raw all-accept（effective=suspended），'
                '或对真实问题提交 request_repair 走零 wire 完整章替换')
        return
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
) -> None:
    for row in request.chapters:
        scenes = {
            scene.candidate_scene_id for scene in row.subject.chapter.scenes
        }
        aliases = {
            fact.fact_alias for fact in row.subject.blind_truth.facts
        } | {
            alias
            for fact in row.subject.blind_truth.facts
            for alias in fact.participant_aliases
        }
        for constraint in row.constraints:
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
        relation_effects = [
            constraint.effect.model_dump(mode='json')
            for constraint in row.constraints
            if constraint.effect is not None
        ]
        if relation_effects:
            # 新的关系约束只能把本章 blind truth 中明确标成 review 的关系
            # 升级为本 strategy epoch 的 HARD 修复要求；不得从自由文本发明关系。
            from novel.stages.stage4.design_evolution import (
                DesignEvolutionError, accumulate_relation_constraints,
            )
            try:
                accumulate_relation_constraints(
                    parent_epoch_sha256=(
                        row.subject.strategy_epoch_sha256 or '0' * 64),
                    current_epoch_sha256=(
                        row.subject.strategy_epoch_sha256 or '0' * 64),
                    inherited=(),
                    requested=relation_effects,
                    truth_relations=[
                        relation.model_dump(mode='json')
                        for relation in row.subject.blind_truth.narrative_relations
                    ],
                )
            except DesignEvolutionError as exc:
                raise HumanEditProtected(
                    f'Stage4A 第 {row.output_ordinal} 章 typed relation '
                    f'不属于当前 review truth：{exc}') from exc

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
                for constraint in row.constraints
                if constraint.code == 'binding_constraint_unsat'
            ]
            if offenders:
                raise HumanEditProtected(
                    f'Stage4A 第 {offenders} 章 binding_constraint_unsat 缺机械 '
                    'witness：预检构造性绑定可行且无 fresh B infeasible 终态；'
                    '不能把 B 失败升级为约束不可满足')


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
        _require_design_constraint_refs(materialized, current)
        _require_design_codes_active(materialized)
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


def _chapter_constraint_scope(
    record: _ApprovalRecordV7,
    decision: DesignReviewDecision,
    row: DesignChapterReview,
) -> DesignRepairChapterScope:
    """收集同 strategy epoch 自最近 accept 后的全部尚未关闭约束。"""
    epoch = row.subject.strategy_epoch_sha256
    if epoch is None:
        return DesignRepairChapterScope(
            output_ordinal=row.output_ordinal,
            subject_sha256=row.subject_sha256,
            constraints=row.constraints,
        )
    accumulated: list[DesignRepairConstraint] = []
    found = False
    for stored in record.design_history:
        if isinstance(stored, _StoredDesignDecisionSlim):
            candidate = next((
                item for item in stored.request.chapters
                if item.output_ordinal == row.output_ordinal
                and stored.strategy_epoch_sha256 == epoch
            ), None)
            stored_sha = stored.decision_sha256
        else:
            public = _public_design_decision(stored)
            candidate = next((
                item for item in public.chapters
                if item.output_ordinal == row.output_ordinal
                and item.subject.strategy_epoch_sha256 == epoch
            ), None)
            stored_sha = public.decision_sha256
        if candidate is not None:
            if candidate.decision == 'accept':
                accumulated = []
            else:
                accumulated.extend(candidate.constraints)
        if stored_sha == decision.decision_sha256:
            found = True
            break
    if not found:
        raise HumanEditProtected('Stage4A 当前 decision 不在 raw history 中')
    canonical = _canonical_design_constraints(accumulated)
    incoming = _canonical_design_constraints(row.constraints)
    if not canonical or not set(
        json.dumps(item.model_dump(mode='json'), ensure_ascii=False,
                   sort_keys=True, separators=(',', ':'))
        for item in incoming
    ).issubset({
        json.dumps(item.model_dump(mode='json'), ensure_ascii=False,
                   sort_keys=True, separators=(',', ':'))
        for item in canonical
    }):
        raise HumanEditProtected(
            f'Stage4A 第 {row.output_ordinal} 章累计约束断链')
    return DesignRepairChapterScope(
        output_ordinal=row.output_ordinal,
        subject_sha256=row.subject_sha256,
        new_constraints=incoming,
        accumulated_constraints=canonical,
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
    record: _ApprovalRecordV7,
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
        if current.legacy_frozen_v6:
            raise HumanEditProtected(
                'legacy_frozen v6 只开放零 wire 完整章替换；'
                '不得启动付费 Stage4 repair/replan')
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


def _legacy_gate_proof(novel_dir: Path, gate: str) -> _ApprovalProof:
    """用 v2 当年的 decision digest 重算 s0..s4，只给显式 backfill 对证。"""
    store = _store_for(novel_dir)
    if gate == 's3':
        _require_legacy_stage3_fresh(novel_dir)
    _require_typed_gate_decision(store, gate)
    return _make_proof(
        novel_dir, list(_LEGACY_GATE_ARTIFACTS[gate]),
        {'kind': 'gate', 'gate': gate, 'deep': None},
    )


def _migrate_legacy_v2(
    base: str | os.PathLike[str], sid: str, legacy: _ApprovalRecordV2Legacy,
) -> tuple[_ApprovalRecordV7, list[str]]:
    """保守重签 v2。

    只有能对上当前原文/产物的 s0～s2 proof 才保留。旧记录没有逐章 typed
    结构裁决，绝不据一个 s4 bool 推造 actor/reason/决定；s4、writing 与下游全清。
    """
    if legacy.sid != sid:
        raise HumanEditProtected(
            f'approval state belongs to {legacy.sid!r}, not requested novel {sid!r}')
    novel_dir = paths.resolve_novel_dir(base, sid)
    record = _fresh_record(sid)
    seen = dict(record.seen)
    seen_proofs = dict(record.seen_proofs)
    for key in APPROVAL_SEEN:
        if not legacy.seen.get(key):
            continue
        try:
            current = _proof_for_seen(novel_dir, key)
        except NovelError:
            continue
        if _same_proof(legacy.seen_proofs.get(key), current):
            seen[key] = True
            seen_proofs[key] = current

    gates = dict(record.gate)
    gate_proofs = dict(record.gate_proofs)
    backfilled: list[str] = []
    for gate in ('s0', 's1', 's2'):
        if not legacy.gate.get(gate):
            continue
        if any(not gates[key] for key in _GATE_REQUIREMENTS.get(gate, ())):
            continue
        if gate == 's2' and not all(seen.values()):
            continue
        try:
            current = _legacy_gate_proof(novel_dir, gate)
        except NovelError:
            continue
        if not _same_proof(legacy.gate_proofs.get(gate), current):
            continue
        # 落盘的是 v4 proof：旧 digest 只用来证明那次决定确实绑定当前主体。
        working = record.model_copy(update={
            'gate': gates, 'gate_proofs': gate_proofs,
            'seen': seen, 'seen_proofs': seen_proofs,
        })
        try:
            current_v4 = _proof_for_gate(novel_dir, gate, working)
        except NovelError:
            continue
        gates[gate] = True
        gate_proofs[gate] = current_v4
        backfilled.append(gate)

    return record.model_copy(update={
        'gate': gates, 'gate_proofs': gate_proofs,
        'seen': seen, 'seen_proofs': seen_proofs,
    }), backfilled


def _migrate_legacy_v3(
    base: str | os.PathLike[str], sid: str, legacy: _ApprovalRecordV3Legacy,
) -> tuple[_ApprovalRecordV7, list[str]]:
    """显式把旧 v3 收成 v6；只能保留能对证的 s0～s2。"""
    if legacy.sid != sid:
        raise HumanEditProtected(
            f'approval state belongs to {legacy.sid!r}, not requested novel {sid!r}')
    novel_dir = paths.resolve_novel_dir(base, sid)
    record = _fresh_record(sid)
    seen = dict(record.seen)
    seen_proofs = dict(record.seen_proofs)
    for key in APPROVAL_SEEN:
        if not legacy.seen.get(key):
            continue
        try:
            current = _proof_for_seen(novel_dir, key)
        except NovelError:
            continue
        if _same_proof(legacy.seen_proofs.get(key), current):
            seen[key] = True
            seen_proofs[key] = current

    gates = dict(record.gate)
    gate_proofs = dict(record.gate_proofs)
    backfilled: list[str] = []
    for gate in ('s0', 's1', 's2'):
        if not legacy.gate.get(gate):
            continue
        if any(not gates[key] for key in _GATE_REQUIREMENTS.get(gate, ())):
            continue
        if gate == 's2' and not all(seen.values()):
            continue
        working = record.model_copy(update={
            'gate': gates, 'gate_proofs': gate_proofs,
            'seen': seen, 'seen_proofs': seen_proofs,
        })
        try:
            current = _proof_for_gate(novel_dir, gate, working)
        except NovelError:
            continue
        if not _same_proof(legacy.gate_proofs.get(gate), current):
            continue
        gates[gate] = True
        gate_proofs[gate] = current
        backfilled.append(gate)

    return record.model_copy(update={
        'gate': gates, 'gate_proofs': gate_proofs,
        'seen': seen, 'seen_proofs': seen_proofs,
        # 旧 v3 的 s4/writing/s5～s7 没有 typed 结构决定，刻意不迁。
        'writing': False, 'writing_proof': None,
    }), backfilled


def _migrate_legacy_v4(
    base: str | os.PathLike[str], sid: str, legacy: _ApprovalRecordV4Legacy,
) -> tuple[_ApprovalRecordV7, list[str]]:
    """显式把 v4 收成 v6；丢弃 s3，但保守重签仍绑定当前主体的决定。"""
    if legacy.sid != sid:
        raise HumanEditProtected(
            f'approval state belongs to {legacy.sid!r}, not requested novel {sid!r}')
    novel_dir = paths.resolve_novel_dir(base, sid)
    record = _fresh_record(sid).model_copy(update={
        'structure_history': legacy.structure_history,
        'structure_decision': legacy.structure_decision,
    })

    seen = dict(record.seen)
    seen_proofs = dict(record.seen_proofs)
    for key in APPROVAL_SEEN:
        if not legacy.seen.get(key):
            continue
        try:
            current = _proof_for_seen(novel_dir, key)
        except NovelError:
            continue
        if _same_proof(legacy.seen_proofs.get(key), current):
            seen[key] = True
            seen_proofs[key] = current

    gates = dict(record.gate)
    gate_proofs = dict(record.gate_proofs)
    backfilled: list[str] = []
    for gate in ('s0', 's1', 's2'):
        if not legacy.gate.get(gate):
            continue
        if any(not gates[key] for key in _GATE_REQUIREMENTS.get(gate, ())):
            continue
        if gate == 's2' and not all(seen.values()):
            continue
        working = record.model_copy(update={
            'gate': gates, 'gate_proofs': gate_proofs,
            'seen': seen, 'seen_proofs': seen_proofs,
        })
        try:
            current = _proof_for_gate(novel_dir, gate, working)
        except NovelError:
            continue
        if _same_proof(legacy.gate_proofs.get(gate), current):
            gates[gate] = True
            gate_proofs[gate] = current
            backfilled.append(gate)

    structure_history = list(record.structure_history)
    structure_decision = record.structure_decision
    # Plan-only 旧票没有 STRUCTURE_REVIEW_CONTRACT 证据权威。即使
    # plan/proof 字节仍可对证，显式 backfill 也只保留历史，
    # 不得以当前 v2 subject 代人重签并打开 s4。

    writing = False
    writing_proof = None
    # 新 writing subject 包含最终结构批准后独立生成的 WriterContext；旧 v4
    # 从未签过这份字节，显式迁移也不能替人扩张原决定。

    working = record.model_copy(update={
        'gate': gates, 'gate_proofs': gate_proofs,
        'seen': seen, 'seen_proofs': seen_proofs,
        'writing': writing, 'writing_proof': writing_proof,
        'structure_history': structure_history,
        'structure_decision': structure_decision,
    })
    for gate in _POST_WRITING_GATES:
        if not legacy.gate.get(gate):
            continue
        if any(not gates[key] for key in _GATE_REQUIREMENTS.get(gate, ())):
            continue
        if gate == 's5' and not writing:
            continue
        try:
            current = _proof_for_gate(novel_dir, gate, working)
        except NovelError:
            continue
        if not _same_proof(legacy.gate_proofs.get(gate), current):
            continue
        gates[gate] = True
        gate_proofs[gate] = current
        backfilled.append(gate)
        working = working.model_copy(update={
            'gate': gates, 'gate_proofs': gate_proofs,
        })

    return record.model_copy(update={
        'gate': gates, 'gate_proofs': gate_proofs,
        'seen': seen, 'seen_proofs': seen_proofs,
        'writing': writing, 'writing_proof': writing_proof,
        'structure_history': structure_history,
        'structure_decision': structure_decision,
    }), backfilled


def _migrate_legacy_v5(
    base: str | os.PathLike[str], sid: str, legacy: _ApprovalRecordV5Legacy,
) -> tuple[_ApprovalRecordV7, list[str]]:
    """显式把 v5 收成 v6；A 历史恒空，绝不从 Plan 或旧 bool 伪造盲审。"""
    if legacy.sid != sid:
        raise HumanEditProtected(
            f'approval state belongs to {legacy.sid!r}, not requested novel {sid!r}')
    novel_dir = paths.resolve_novel_dir(base, sid)
    record = _fresh_record(sid).model_copy(update={
        'structure_history': legacy.structure_history,
        'structure_decision': legacy.structure_decision,
        # 保留旧 typed 决定作为审计历史；它的旧 proof 不含 writer_context，
        # 因而不会被投影成当前授权，人须在当前 Context 上重新签一次。
        'writing_history': legacy.writing_history,
        'writing_decision': legacy.writing_decision,
        'design_history': [],
        'design_decision': None,
    })
    seen = dict(record.seen)
    seen_proofs = dict(record.seen_proofs)
    for key in APPROVAL_SEEN:
        if not legacy.seen.get(key):
            continue
        try:
            current = _proof_for_seen(novel_dir, key)
        except NovelError:
            continue
        if _same_proof(legacy.seen_proofs.get(key), current):
            seen[key] = True
            seen_proofs[key] = current

    gates = dict(record.gate)
    gate_proofs = dict(record.gate_proofs)
    structure_history = list(record.structure_history)
    structure_decision = record.structure_decision
    backfilled: list[str] = []
    for gate in ('s0', 's1', 's2'):
        if not legacy.gate.get(gate):
            continue
        if any(not gates[key] for key in _GATE_REQUIREMENTS.get(gate, ())):
            continue
        if gate == 's2' and not all(seen.values()):
            continue
        working = record.model_copy(update={
            'gate': gates, 'gate_proofs': gate_proofs,
            'seen': seen, 'seen_proofs': seen_proofs,
        })
        try:
            current = _proof_for_gate(novel_dir, gate, working)
        except NovelError:
            continue
        if _same_proof(legacy.gate_proofs.get(gate), current):
            gates[gate] = True
            gate_proofs[gate] = current
            backfilled.append(gate)

    # 与 v4 迁移相同：旧 Plan-only 结构票只留历史，不以
    # 当前 STRUCTURE_REVIEW_CONTRACT 静默重签。

    return record.model_copy(update={
        'gate': gates,
        'gate_proofs': gate_proofs,
        'seen': seen,
        'seen_proofs': seen_proofs,
        'writing': False,
        'writing_proof': None,
        'structure_history': structure_history,
        'structure_decision': structure_decision,
    }), backfilled


def _migrate_legacy_v6(
    base: str | os.PathLike[str], sid: str, legacy: _ApprovalRecordV6Legacy,
) -> tuple[_ApprovalRecordV7, list[str]]:
    """显式把 v6 收成 v7：仅保留现役 proof 可对证的旧决定与历史。

    s5/s6/s7 的 gate 与 proof 一律清除——v7 的 s5 只能由当前 typed closure
    回执打开，backfill 不伪造 Commander 回执。写作授权的 typed 历史保留，
    但与 v5 迁移一样按当前盘面重新对证后才能投影成现行授权。旧
    ``seen.read`` 没有完整工作包 binding，不能迁成现役阅读事实。
    """
    if legacy.sid != sid:
        raise HumanEditProtected(
            f'approval state belongs to {legacy.sid!r}, not requested novel {sid!r}')
    novel_dir = paths.resolve_novel_dir(base, sid)
    record = _fresh_record(sid).model_copy(update={
        'structure_history': legacy.structure_history,
        'structure_decision': legacy.structure_decision,
        'writing_history': legacy.writing_history,
        'writing_decision': legacy.writing_decision,
        'design_history': legacy.design_history,
        'design_decision': legacy.design_decision,
    })
    seen = dict(record.seen)
    seen_proofs = dict(record.seen_proofs)
    for key in APPROVAL_SEEN:
        if not legacy.seen.get(key):
            continue
        try:
            current = _proof_for_seen(novel_dir, key)
        except NovelError:
            continue
        if _same_proof(legacy.seen_proofs.get(key), current):
            seen[key] = True
            seen_proofs[key] = current

    gates = dict(record.gate)
    gate_proofs = dict(record.gate_proofs)
    backfilled: list[str] = []
    for gate in ('s0', 's1', 's2', 's4'):
        if not legacy.gate.get(gate):
            continue
        if any(not gates[key] for key in _GATE_REQUIREMENTS.get(gate, ())):
            continue
        if gate == 's2' and not all(seen.values()):
            continue
        if gate == 's4' and legacy.gate_proofs.get('s4') is None:
            continue
        try:
            current = _proof_for_gate(
                novel_dir, gate,
                record.model_copy(update={
                    'gate': gates, 'gate_proofs': gate_proofs,
                    'seen': seen, 'seen_proofs': seen_proofs}))
        except NovelError:
            continue
        if _same_proof(legacy.gate_proofs.get(gate), current):
            gates[gate] = True
            gate_proofs[gate] = current
            backfilled.append(gate)

    return record.model_copy(update={
        'gate': gates,
        'gate_proofs': gate_proofs,
        'seen': seen,
        'seen_proofs': seen_proofs,
        'writing': False,
        'writing_proof': None,
    }), backfilled


def backfill_from_disk(
    base: str | os.PathLike[str], sid: str,
) -> ApprovalBackfillResult:
    """补录 CLI/队列已经落成的链，并把补了哪些章结构化返回给界面。

    这是显式的人操作，不在 GET 时静默发生。普通逐屏验收仍走 `approve_gate`；
    本入口只在对应正式产物齐备且没有 `.INVALID` 时补章。Stage5 只认
    全部 canonical `stage5/chNN.json` + fresh Review。
    """
    with approval_file_lock(base, sid):
        migrated = False
        migrated_gates: list[str] = []
        try:
            raw_record, record = _read_state(base, sid)
        except HumanEditProtected:
            path = _path(base, sid)
            try:
                raw = path.read_text(encoding='utf-8')
            except (OSError, UnicodeDecodeError, ValidationError) as exc:
                raise HumanEditProtected(
                    f'approval record cannot be read for explicit backfill: {exc}') from exc
            # legacy 梯子按新到旧尝试；命中第一个可解析版本做**显式**迁移。
            # v6→v7 只保留 s0–s4/seen/typed 历史并清除 s5–s7，不伪造回执。
            parsed = False
            for legacy_model, migrate in (
                (_ApprovalRecordV6Legacy, _migrate_legacy_v6),
                (_ApprovalRecordV5Legacy, _migrate_legacy_v5),
                (_ApprovalRecordV4Legacy, _migrate_legacy_v4),
                (_ApprovalRecordV3Legacy, _migrate_legacy_v3),
                (_ApprovalRecordV2Legacy, _migrate_legacy_v2),
            ):
                try:
                    legacy = legacy_model.model_validate_json(raw)
                except ValidationError:
                    continue
                raw_record, migrated_gates = migrate(base, sid, legacy)
                record = raw_record
                migrated = True
                parsed = True
                break
            if not parsed:
                raise HumanEditProtected(
                    'approval record cannot be explicitly backfilled as '
                    'v2/v3/v4/v5/v6: no legacy reader accepted the bytes')
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
        backfilled: list[str] = list(migrated_gates)

        for gate in _GATE_ORDER:
            if current_gates[gate]:
                continue
            # Stage4 不能从「盘上有一份计划」推成人已经逐章审过；只有
            # ``approve_structure`` 能生成 actor/reason/subject-bound typed 决定。
            if gate == 's4':
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
            if (backfilled or migrated)
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
    并记录 actor。headless 路由下两枚 token 都来自第四席只读终审的回执，
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
            after_proof, after_identity = _blueprint_seen_snapshot(novel_dir)
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
    """按公开审批服务的固定顺序收口一次 headless Stage2 独立终审。

    第四席只读终审提交它实际读过的两枚 opaque token（Global 审阅面 + Blueprint
    工作包）与 actor/reason；本函数不生成 review 决定，也不碰小说 artifact。
    五步各自仍执行原有 currentness、typed review 与 TOCTOU 校验。若中途失败，
    已经落下的 raw proof 也是可审计、可幂等恢复的事实，最后的 ``s2`` 不会在
    前置项失效时被误开。
    """
    approve_gate(base, sid, 's0')
    approve_gate(base, sid, 's1')
    mark_seen(base, sid, 'read', blueprint_subject=request.blueprint_subject)
    mark_seen(base, sid, 'global', global_subject=request.global_subject)
    return approve_gate(base, sid, 's2')


def _has_stage5_work(novel_dir: Path) -> bool:
    stage5 = novel_dir / 'stage5'
    return bool(
        state.stage5_legacy_paths(novel_dir)
        or state.chapters_done(novel_dir)
        or any(stage5.glob('ch*.INVALID.json'))
        or any(stage5.glob('ch*.rejected.json'))
    )


def _writing_prerequisites(
    base: str | os.PathLike[str], sid: str, record: _ApprovalRecordV7,
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
        return StructureApprovalRequest(
            actor=actor,
            reason=reason,
            review_token=f's4sr1_{subject}',
            chapters=[{
                'output_ordinal': ordinal,
                'decision': 'request_replan',
                'reason': '关闭式模板：尚未逐章核实，保持 request_replan',
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
