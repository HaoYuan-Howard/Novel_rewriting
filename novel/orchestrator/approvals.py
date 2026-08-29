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
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Literal

from pydantic import Field, ValidationError, model_validator

from contracts.invariants import (
    APPROVAL_GATES, APPROVAL_SEEN, DESIGN_REPAIR_CODES, DESIGN_REVIEW_CONTRACT,
    STRUCTURE_CHANGE_KINDS, STRUCTURE_METRIC_CONTRACT, STYLE_MEASUREMENT_CONTRACT,
)
from contracts.schema import artifact_spec
from contracts.schema.api import (
    ApprovalAction, ApprovalBackfillResult, ApprovalDoc,
    DesignBindingInfeasibility, DesignBlindChapterTruth,
    DesignChapterReview, DesignChapterSubject, DesignRepairConstraint,
    DesignRepairChapterScope, DesignRepairScope,
    DesignReviewBasis, DesignReviewDecision, DesignReviewRequest,
    DesignReviewRepairCapability, DesignReviewTemplate, DesignReviewView,
    DesignStrategyReview,
    DesignStrategySubject, DesignStructureEvidence,
    StructureApprovalDecision, StructureApprovalRequest, StructureApprovalView,
    WritingApprovalDecision, WritingApprovalRequest, WritingApprovalView,
    Stage5ClosureDecision, Stage5ClosureRequest, Stage5ClosureTemplate,
    Stage5ClosureView,
)
from contracts.schema.evaluation import GlobalReview
from contracts.schema.stage0 import Stage0Revisions
from contracts.schema.stage1 import HookCopy, MainText
from contracts.schema.stage2 import ChapterBlueprints, GlobalGrounding
from contracts.schema.stage4 import BindingConcern, BlindCandidate, EvidenceBinding, PlanChapters
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


class _StoredStructureDecision(StructureApprovalDecision):
    """一项服务审批历史：公开 typed 决定 + 当前主体完整字节 proof。"""

    proof: _ApprovalProof


class _StoredWritingDecision(WritingApprovalDecision):
    """一项 Stage5 写作授权历史：typed 决定 + 当时完整主体 proof。"""

    proof: _ApprovalProof


class _StoredDesignDecision(DesignReviewDecision):
    """一次 Stage4A 盲审历史：完整 typed 决定 + 当时候选的精确字节 proof。"""

    proof: _ApprovalProof


class _StoredClosureDecision(Stage5ClosureDecision):
    """一次 Stage5 闭合回执：完整 typed 决定 + 提交时盘面的精确字节 proof。"""

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
    base: str | os.PathLike[str], record: _ApprovalRecordV7,
) -> ApprovalDoc:
    """服务记录的唯一公开投影；public v6 明示闭合回执与 typed 裁决。"""
    novel_dir = paths.resolve_novel_dir(base, record.sid)
    design_review = _design_review_view(novel_dir, record)
    structure = _structure_view(novel_dir, record)
    writing_approval = _writing_view(novel_dir, record)
    closure = _closure_view(novel_dir, record)
    spawns = _spawns(base, record.sid)
    decision = design_review.current_decision
    if decision is not None and design_review.state in (
        'repair_requested', 'strategy_replan_requested',
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
                    ['--repair-decision', decision.decision_sha256]
                    if repairing else ['--replan']
                ),
            )],
        }
    elif current_infeasible_binding(_store_for(novel_dir)) is not None:
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
    ctx = state.readonly_ctx(novel_dir, 'stage5.write', prov.code_version())
    return state.planned_chapters(ArtifactStore(ctx))


def _read_raw(base: str | os.PathLike[str], sid: str) -> _ApprovalRecordV7:
    paths.resolve_novel_dir(base, sid)
    p = _path(base, sid)
    if not p.is_file():
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


def _read_current(base: str | os.PathLike[str], sid: str) -> _ApprovalRecordV7:
    record = _read_raw(base, sid)
    if not _path(base, sid).is_file():
        return record
    novel_dir = paths.resolve_novel_dir(base, sid)
    return _effective_record(novel_dir, record)


def _write(
    base: str | os.PathLike[str], record: _ApprovalRecordV7,
) -> ApprovalDoc:
    """审批落盘的**唯一落点**；冻结检查因此也只装在这里。"""
    record = _ApprovalRecordV7.model_validate(record.model_dump())
    _refuse_if_frozen(record.sid)
    p = _path(base, record.sid)
    p.parent.mkdir(parents=True, exist_ok=True)
    tmp = p.with_suffix(f'.{os.getpid()}.{threading.get_ident()}.tmp')
    tmp.write_text(record.model_dump_json(indent=2), encoding='utf-8')
    os.replace(tmp, p)
    return _view(base, record)


def get(base: str | os.PathLike[str], sid: str) -> ApprovalDoc:
    with _LOCK:
        return _view(base, _read_current(base, sid))


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
    ``global_review.require_approved`` 同时读取当前 grounding/review，并复用
    ``stage2.blueprint --check-only`` 的联合判定与 freshness；不在编排层新增
    一份机械 check 或自行解释 PASS/UNVER。
    """
    novel_dir = paths.resolve_novel_dir(base, sid)
    ctx = state.readonly_ctx(novel_dir, node, prov.code_version())
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
    'global': (STAGE2_GLOBAL_GROUNDING,),
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
    repair_capability: DesignReviewRepairCapability
    strategy: DesignStrategySubject
    strategy_subject_sha256: str = Field(pattern=r'^[0-9a-f]{64}$')
    chapters: list[DesignChapterSubject] = Field(min_length=1)
    chapter_subject_sha256: dict[int, str]


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
    _require_fresh_json(store, STAGE2_CHAPTER_BLUEPRINTS)
    blueprints, _blueprints_raw = store.read_own(
        ChapterBlueprints, STAGE2_CHAPTER_BLUEPRINTS)
    ordinals = [row.output_ordinal for row in candidate.output_chapters]
    if ordinals != sorted(set(ordinals)):
        raise ArtifactUnreadable('Stage4A 候选章号必须升序、去重')
    repair_capability = _current_design_repair_capability(
        store, candidate=candidate, candidate_file_sha256=candidate_sha256,
        ordinals=ordinals)

    from novel.stages.stage4.blind import (
        BlindPlanError, build_blind_projection, candidate_subject_sha256,
    )
    root_clean_sha1 = _root_clean_sha1(novel_dir)
    blueprint_payload = blueprints.model_dump(mode='json', by_alias=True)
    try:
        blind_truth, current_mapping = build_blind_projection(
            blueprint_payload, root_clean_sha1=root_clean_sha1)
    except BlindPlanError as exc:
        raise ArtifactUnreadable(
            f'当前 Stage2 不能重建 Stage4A blind truth：{exc}') from exc
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
    blueprint_by_ordinal = {
        row.chapter_ordinal: row.model_dump(mode='json')
        for row in blueprints.blueprints
    }
    if set(truth_by_ordinal) != set(ordinals) or set(blueprint_by_ordinal) != set(ordinals):
        raise ArtifactUnreadable(
            'Stage4A candidate、blind truth 与 Stage2 blueprint 章集不闭合')

    basis = DesignReviewBasis(
        review_contract=DESIGN_REVIEW_CONTRACT,
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

    chapter_subjects: list[DesignChapterSubject] = []
    chapter_hashes: dict[int, str] = {}
    for chapter in candidate.output_chapters:
        subject = DesignChapterSubject(
            strategy_subject_sha256=strategy_sha256,
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
    aggregate_sha256 = _decision_sha256({
        'kind': 'stage4_design_review_aggregate',
        'contract': DESIGN_REVIEW_CONTRACT,
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
        repair_capability=repair_capability,
        strategy=strategy,
        strategy_subject_sha256=strategy_sha256,
        chapters=chapter_subjects,
        chapter_subject_sha256=chapter_hashes,
    )


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
    return DesignReviewRepairCapability(
        repairable=capability.repairable,
        reason_code=capability.reason_code,
        chapters=[{
            'output_ordinal': ordinal,
            'committed_rounds': rounds.get(ordinal, 0),
            'request_repair_available': (
                capability.repairable and rounds.get(ordinal, 0) < 2
            ),
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
    ctx = state.readonly_ctx(novel_dir, 'stage0.approve', prov.code_version())
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
            raise ApprovalRequired(
                f'{rel} is not definitively current: {report.state}/{report.reason}')
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
    decision: DesignReviewRequest | DesignReviewDecision,
) -> dict[str, Any]:
    """Stage4A 人审的唯一摘要面；服务端决定时间与摘要自身不参与递归。"""
    return {
        'kind': 'stage4_design_review',
        **decision.model_dump(
            mode='json', exclude={'decision_sha256', 'decided_at'}),
    }


def _public_design_decision(
    decision: _StoredDesignDecision,
) -> DesignReviewDecision:
    return DesignReviewDecision.model_validate(
        decision.model_dump(mode='json', exclude={'proof'}))


def _proof_for_design(
    novel_dir: Path,
    decision: DesignReviewRequest | DesignReviewDecision,
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
    request: DesignReviewRequest | DesignReviewDecision,
    current: _DesignSubjects,
) -> bool:
    if (
        request.candidate_sha256 != current.candidate_sha256
        or request.aggregate_subject_sha256 != current.aggregate_subject_sha256
        or request.binding_infeasibility != current.binding_infeasibility
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


def _structure_decision_payload(
    decision: StructureApprovalRequest | StructureApprovalDecision,
) -> dict[str, Any]:
    """Typed 结构裁决的唯一 proof 投影；服务时间不改写人作出的决定。"""
    return {
        'kind': 'stage4_structure',
        'subject_sha256': decision.subject_sha256,
        'actor': decision.actor,
        'reason': decision.reason,
        'chapters': [row.model_dump(mode='json') for row in decision.chapters],
    }


def _public_structure_decision(
    decision: _StoredStructureDecision,
) -> StructureApprovalDecision:
    return StructureApprovalDecision.model_validate(
        decision.model_dump(mode='json', exclude={'proof'}))


def _writing_decision_payload(
    decision: WritingApprovalRequest | WritingApprovalDecision,
) -> dict[str, Any]:
    """写作授权的唯一 proof 投影；服务端时间不参与人的决定摘要。"""
    return {
        'kind': 'stage5_writing_approval',
        'subject_sha256': decision.subject_sha256,
        'actor': decision.actor,
        'reason': decision.reason,
    }


def _public_writing_decision(
    decision: _StoredWritingDecision,
) -> WritingApprovalDecision:
    return WritingApprovalDecision.model_validate(
        decision.model_dump(mode='json', exclude={'proof'}))


def _current_structure_subject_and_chapters(
    novel_dir: Path,
) -> tuple[str, list[int]]:
    """读取当前正式计划+B 担忧并计算人真正审阅的结构决策面 hash。"""
    store = _store_for(novel_dir)
    _require_fresh_json(store, STAGE4_PLAN_CHAPTERS)
    plan, _raw = store.read_own(PlanChapters, STAGE4_PLAN_CHAPTERS)
    concerns = _current_binding_concerns(novel_dir, store=store)
    try:
        from novel.stages.stage4.measure import structure_review_subject_sha256
    except ImportError as exc:
        raise ArtifactUnreadable(
            'stage4 structure subject helper is unavailable') from exc
    subject = structure_review_subject_sha256(plan, concerns)
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


def _current_binding_concerns(
    novel_dir: Path, *, store: ArtifactStore | None = None,
) -> list[BindingConcern]:
    """发布当前 bound B 的非阻断担忧；不把它们重新判成机械失败。"""
    current_store = store or _store_for(novel_dir)
    _require_fresh_json(current_store, STAGE4_EVIDENCE_BINDING)
    binding, _raw = current_store.read_own(
        EvidenceBinding, STAGE4_EVIDENCE_BINDING)
    if binding.status != 'bound':
        raise ApprovalRequired(
            '最终结构审批只接受当前 status=bound 的 EvidenceBinding')
    return list(binding.binding_concerns)


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


def _migrate_current_legacy_structure(
    novel_dir: Path,
    legacy_decision: _StoredStructureDecision,
    legacy_gate_proof: _ApprovalProof | None,
) -> _StoredStructureDecision | None:
    """把显式 backfill 的旧 Plan-only 决定重铸到当前 Plan+B v3 主体。

    旧决定确实审过的 actor/reason/逐章决定/时间一字不改；只在旧 Plan
    subject、旧 proof、当前 Plan 章集及当前 bound EvidenceBinding 全部可逐项
    对证时，重算 v6 的 subject/proof。任一格不闭合就返回 ``None``，调用方
    保持 s4 关闭，绝不拿一张旧 proof 冒充新主体。
    """
    public = _public_structure_decision(legacy_decision)
    try:
        store = _store_for(novel_dir)
        _require_fresh_json(store, STAGE4_PLAN_CHAPTERS)
        plan, _raw = store.read_own(PlanChapters, STAGE4_PLAN_CHAPTERS)
        from novel.stages.stage4.measure import structure_subject_sha256
        legacy_subject = structure_subject_sha256(plan)
        current_subject, current_chapters = \
            _current_structure_subject_and_chapters(novel_dir)
        legacy_proof = _proof_for_legacy_structure(novel_dir, public)
    except NovelError:
        return None
    decided = [row.output_ordinal for row in public.chapters]
    if (
        public.subject_sha256 != legacy_subject
        or decided != current_chapters
        or any(row.decision != 'accept' for row in public.chapters)
        or not _same_proof(legacy_decision.proof, legacy_proof)
        or not _same_proof(legacy_gate_proof, legacy_proof)
    ):
        return None

    migrated_public = StructureApprovalDecision(
        actor=public.actor,
        reason=public.reason,
        subject_sha256=current_subject,
        chapters=public.chapters,
        decided_at=public.decided_at,
    )
    try:
        migrated_proof = _proof_for_structure(novel_dir, migrated_public)
        after_subject, after_chapters = \
            _current_structure_subject_and_chapters(novel_dir)
        after_legacy_proof = _proof_for_legacy_structure(novel_dir, public)
        after_migrated_proof = _proof_for_structure(
            novel_dir, migrated_public)
    except NovelError:
        return None
    if (
        (after_subject, after_chapters) != (current_subject, current_chapters)
        or after_legacy_proof != legacy_proof
        or after_migrated_proof != migrated_proof
    ):
        return None
    return _StoredStructureDecision(
        **migrated_public.model_dump(mode='json'), proof=migrated_proof)


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
) -> DesignReviewView:
    """即时投影当前 Stage4A 人审态；旧决定只留历史，不自动拼成新批准。"""
    history = [
        _public_design_decision(decision)
        for decision in record.design_history
    ]
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
            repair_capability=current.repair_capability,
            current_decision=None,
            history=[],
        )
    public = _public_design_decision(latest)
    try:
        current_proof = _proof_for_design(novel_dir, public)
    except NovelError:
        current_proof = None
    digest = _decision_sha256(_design_decision_payload(public))
    if (
        latest.decision_sha256 != digest
        or not _design_request_matches_subjects(public, current)
        or current_proof is None
        or not _same_proof(latest.proof, current_proof)
    ):
        return DesignReviewView(
            state='expired',
            candidate_sha256=current.candidate_sha256,
            aggregate_subject_sha256=current.aggregate_subject_sha256,
            binding_infeasibility=current.binding_infeasibility,
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
        repair_capability=current.repair_capability,
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
        current_decision=current,
        history=history,
    )


def _public_closure_decision(
    decision: _StoredClosureDecision,
) -> Stage5ClosureDecision:
    return Stage5ClosureDecision.model_validate(
        decision.model_dump(exclude={'proof'}, mode='json'))


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
            store, record_history=history, record_current=current)
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
        closure_workpack, validate_closure_request,
    )
    novel_dir = paths.resolve_novel_dir(base, sid)
    # 锁序固定为 Stage5 书锁 → 审批文件锁：盘面快照、proof 与 gate.s5
    # 必须共同消费同一个正文/trim 主体，不能在回执尾窗让写者穿过。
    with (stage5_runs.book_commit_ownership(novel_dir),
          approval_file_lock(base, sid)):
        record = _read_current(base, sid)
        store = _store_for(novel_dir)
        pack = validate_closure_request(store, request)
        # 双读防提交期间盘面漂移（与 design review 的提交窗口一致）。
        after = closure_workpack(store)
        if after.workpack_sha256 != pack.workpack_sha256:
            raise HumanEditProtected(
                'Stage5 盘面在回执提交期间发生变化；请刷新模板后重新裁决')
        proof = _make_proof(
            novel_dir, _stage5_subject_rels(base, sid, record),
            {'kind': 'closure', 'closure_sha256': request.workpack_sha256})
        payload = request.model_dump(mode='json')
        digest = _decision_sha256(payload)
        latest = record.closure_decision
        if (
            latest is not None
            and latest.decision_sha256 == digest
            and _same_proof(latest.proof, proof)
        ):
            return _view(base, record)
        public = Stage5ClosureDecision(
            **payload, decision_sha256=digest, decided_at=_now())
        stored = _StoredClosureDecision(**public.model_dump(mode='json'), proof=proof)
        gates = dict(record.gate)
        gate_proofs = dict(record.gate_proofs)
        gates['s5'] = True
        gate_proofs['s5'] = proof
        return _write(base, record.model_copy(update={
            'closure_history': [*record.closure_history, stored],
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
        proof = approval_proof_sha256(names)
        if proof is None:
            raise ApprovalRequired(
                'stage7 typed approval is not bound to the current name decision surface')
        return proof
    return None


def _proof_for_seen(novel_dir: Path, key: str) -> _ApprovalProof:
    return _make_proof(
        novel_dir, _SEEN_ARTIFACTS[key], {'kind': 'seen', 'key': key})


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
        decision = record.closure_decision
        if decision is None:
            raise ApprovalRequired(
                's5 gate 只能由当前 typed closure 回执打开；缺闭合决定')
        pack = closure_workpack(_store_for(novel_dir))
        if decision.workpack_sha256 != pack.workpack_sha256:
            raise ApprovalRequired(
                's5 closure 回执绑定的是过期工作包；请重新生成模板并重新裁决')
        rels = _stage5_subject_rels(novel_dir.parent, record.sid, record)
        return _make_proof(
            novel_dir, rels,
            {'kind': 'gate', 'gate': gate, 'closure': decision.decision_sha256})
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


def _stored_design_is_self_consistent(decision: _StoredDesignDecision) -> bool:
    public = _public_design_decision(decision)
    digest = _decision_sha256(_design_decision_payload(public))
    return (
        decision.decision_sha256 == digest
        and decision.proof.decision_sha256 == digest
    )


def _accepted_design_strategy_source(
    record: _ApprovalRecordV7, current: _DesignSubjects,
) -> _StoredDesignDecision | None:
    for stored in reversed(record.design_history):
        public = _public_design_decision(stored)
        if (
            _stored_design_is_self_consistent(stored)
            and public.strategy.decision == 'accept'
            and public.strategy.subject == current.strategy
            and public.strategy.subject_sha256 == current.strategy_subject_sha256
        ):
            return stored
    return None


def _accepted_design_chapter_source(
    record: _ApprovalRecordV7,
    subject: DesignChapterSubject,
    subject_sha256: str,
) -> tuple[_StoredDesignDecision, DesignChapterReview] | None:
    ordinal = subject.chapter.output_ordinal
    for stored in reversed(record.design_history):
        if not _stored_design_is_self_consistent(stored):
            continue
        row = next((
            item for item in stored.chapters
            if item.output_ordinal == ordinal
        ), None)
        if (
            row is not None
            and row.decision == 'accept'
            and row.subject == subject
            and row.subject_sha256 == subject_sha256
        ):
            return stored, row
    return None


_DESIGN_STRATEGY_TEMPLATE_REASON = \
    '关闭式模板：尚未确认当前整书策略，保持 request_replan'
_DESIGN_CHAPTER_TEMPLATE_REASON = \
    '关闭式模板：尚未确认当前章设计，保持 request_repair'
_DESIGN_CHAPTER_UNREPAIRABLE_TEMPLATE_REASON = \
    '关闭式模板：当前章不能执行 targeted repair，须确认接受或改走策略 replan'
_DESIGN_CHAPTER_TEMPLATE_CONSTRAINTS = [DesignRepairConstraint(
    code='structure_delta', scene_refs=[], opaque_aliases=[])]


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
) -> DesignReviewTemplate:
    """生成当前全书 A 审阅模板；仅复用逐项仍完全相同的历史 accept。"""
    with _LOCK:
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
            decision='accept' if strategy_source is not None else 'request_replan',
            reason=(
                '沿用仍绑定当前策略主体的历史 accept'
                if strategy_source is not None
                else _DESIGN_STRATEGY_TEMPLATE_REASON
            ),
            source_decision_sha256=(
                strategy_source.decision_sha256 if strategy_source else None),
        )
        chapters: list[DesignChapterReview] = []
        repairable_by_ordinal = {
            row.output_ordinal: row.request_repair_available
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
                    'accept' if source is not None or not can_repair
                    else 'request_repair'
                ),
                reason=(
                    '沿用仍绑定当前章主体的历史 accept'
                    if source is not None
                    else _DESIGN_CHAPTER_TEMPLATE_REASON
                    if can_repair
                    else _DESIGN_CHAPTER_UNREPAIRABLE_TEMPLATE_REASON
                ),
                constraints=(
                    [] if source is not None or not can_repair
                    else _DESIGN_CHAPTER_TEMPLATE_CONSTRAINTS
                ),
                source_decision_sha256=(
                    source[0].decision_sha256 if source else None),
            ))
        return DesignReviewTemplate(
            actor=actor,
            reviewer_kind=reviewer_kind,
            review_run_id=review_run_id,
            general_reason=reason,
            candidate_sha256=current.candidate_sha256,
            aggregate_subject_sha256=current.aggregate_subject_sha256,
            binding_infeasibility=current.binding_infeasibility,
            repair_capability=current.repair_capability,
            strategy=strategy,
            chapters=chapters,
        )


def _require_design_source_decisions(
    record: _ApprovalRecordV7, request: DesignReviewRequest,
) -> None:
    if request.binding_infeasibility is not None and (
        request.strategy.source_decision_sha256 is not None
        or any(row.source_decision_sha256 is not None for row in request.chapters)
    ):
        raise HumanEditProtected(
            'fresh B infeasible 证据必须重新逐项审阅，不得复用此前 accept')
    by_sha = {
        row.decision_sha256: row for row in record.design_history
        if _stored_design_is_self_consistent(row)
    }
    strategy_source = request.strategy.source_decision_sha256
    if strategy_source is not None:
        stored = by_sha.get(strategy_source)
        if stored is None:
            raise HumanEditProtected('Stage4A strategy source decision 不存在或摘要损坏')
        source = stored.strategy
        if (
            source.decision != 'accept'
            or source.subject != request.strategy.subject
            or source.subject_sha256 != request.strategy.subject_sha256
            or request.strategy.decision != 'accept'
        ):
            raise HumanEditProtected(
                'Stage4A strategy source decision 不是同主体的历史 accept')
    for row in request.chapters:
        source_sha = row.source_decision_sha256
        if source_sha is None:
            continue
        stored = by_sha.get(source_sha)
        source = next((
            item for item in stored.chapters
            if item.output_ordinal == row.output_ordinal
        ), None) if stored is not None else None
        if (
            source is None
            or source.decision != 'accept'
            or source.subject != row.subject
            or source.subject_sha256 != row.subject_sha256
            or row.decision != 'accept'
        ):
            raise HumanEditProtected(
                f'Stage4A 第 {row.output_ordinal} 章 source decision '
                '不是同主体的历史 accept')


def _require_design_template_materialized(request: DesignReviewRequest) -> None:
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
    sentinel_constraints = [
        row.model_dump(mode='json')
        for row in _DESIGN_CHAPTER_TEMPLATE_CONSTRAINTS
    ]
    for row in request.chapters:
        default_repair = (
            row.source_decision_sha256 is None
            and row.decision == 'request_repair'
            and row.reason.strip() == _DESIGN_CHAPTER_TEMPLATE_REASON
            and [item.model_dump(mode='json') for item in row.constraints]
            == sentinel_constraints
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


def _require_design_repair_capability(request: DesignReviewRequest) -> None:
    available = {
        row.output_ordinal
        for row in request.repair_capability.chapters
        if row.request_repair_available
    }
    unavailable = [
        row.output_ordinal for row in request.chapters
        if row.decision == 'request_repair' and row.output_ordinal not in available
    ]
    if unavailable:
        raise HumanEditProtected(
            f'Stage4A request_repair 章 {unavailable} 当前无可执行 split lineage '
            '或已耗尽 2 轮；只能接受该章或改走策略 request_replan')


def _require_design_constraint_refs(request: DesignReviewRequest) -> None:
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


def approve_design(
    base: str | os.PathLike[str], sid: str, request: DesignReviewRequest,
) -> ApprovalDoc:
    """原子保存当前完整 A 盲审；不启动修订、B 或任何付费作业。"""
    with approval_file_lock(base, sid):
        record = _read_current(base, sid)
        if not record.gate['s2']:
            raise ApprovalRequired('Stage4A design review requires current gate.s2')
        novel_dir = paths.resolve_novel_dir(base, sid)
        current = _current_design_subjects(novel_dir)
        if not _design_request_matches_subjects(request, current):
            raise HumanEditProtected(
                'Stage4A 审阅主体已变化；请刷新完整模板后重新原子提交')
        if request.strategy.placeholder or any(
            row.placeholder for row in request.chapters
        ):
            raise HumanEditProtected(
                'Stage4A 完整提交仍含未逐项确认的 template placeholder')
        if request.strategy.template_token is not None or any(
            row.template_token is not None for row in request.chapters
        ):
            raise HumanEditProtected(
                'Stage4A 完整提交仍含 template_token；须逐项显式确认')
        _require_design_template_materialized(request)
        _require_design_repair_capability(request)
        _require_design_source_decisions(record, request)
        _require_design_constraint_refs(request)
        proof = _proof_for_design(novel_dir, request)
        digest = _decision_sha256(_design_decision_payload(request))

        after = _current_design_subjects(novel_dir)
        after_proof = _proof_for_design(novel_dir, request)
        if current != after or proof != after_proof:
            raise HumanEditProtected(
                'Stage4A 候选在盲审提交期间发生变化；请刷新后重审')

        latest = record.design_decision
        if (
            latest is not None
            and latest.decision_sha256 == digest
            and _same_proof(latest.proof, proof)
        ):
            return _view(base, record)
        public = DesignReviewDecision(
            **request.model_dump(mode='json'),
            decision_sha256=digest,
            decided_at=_now(),
        )
        stored = _StoredDesignDecision(
            **public.model_dump(mode='json'), proof=proof)
        return _write(base, record.model_copy(update={
            'design_history': [*record.design_history, stored],
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
        view = _design_review_view(novel_dir, record)
        if view.state != 'accepted' or view.current_decision is None:
            raise ApprovalRequired(
                f'Stage4B requires current all-accept design review; state={view.state}')
        if current_infeasible_binding(_store_for(novel_dir)) is not None:
            raise ApprovalRequired(
                '当前 A 已有 fresh B infeasible 终态；必须先按 typed design 决定'
                '修订/重规划 A，不能重复同一 B')
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


def resolve_design_action(
    base: str | os.PathLike[str], sid: str,
    decision_sha256: str | None, *, expected: Literal['repair', 'replan'],
) -> DesignRepairScope:
    """把当前 typed A 决定解析成 core 唯一可执行的关闭式修订范围。"""
    with _LOCK:
        record = _read_current(base, sid)
        novel_dir = paths.resolve_novel_dir(base, sid)
        view = _design_review_view(novel_dir, record)
        decision = view.current_decision
        if decision is None:
            raise ApprovalRequired(
                f'没有当前 Stage4A typed 决定；state={view.state}')
        if decision_sha256 is not None and decision_sha256 != decision.decision_sha256:
            raise HumanEditProtected(
                'repair decision SHA-256 不等于当前 design decision')
        if expected == 'replan':
            if decision.strategy.decision != 'request_replan':
                raise ApprovalRequired('当前策略决定不是 request_replan')
            return DesignRepairScope(
                candidate_sha256=decision.candidate_sha256,
                aggregate_subject_sha256=decision.aggregate_subject_sha256,
                decision_sha256=decision.decision_sha256,
                binding_infeasibility=decision.binding_infeasibility,
                repair_capability=decision.repair_capability,
                strategy_replan=True,
                target_chapters=[],
                chapters=[],
            )
        if expected != 'repair':
            raise ValueError(f'unknown design action {expected!r}')
        if decision_sha256 is None:
            raise HumanEditProtected('--repair-decision 必须给出当前完整 decision SHA-256')
        if decision.strategy.decision != 'accept':
            raise ApprovalRequired('策略 request_replan 时不得执行逐章 repair')
        rows = [
            DesignRepairChapterScope(
                output_ordinal=row.output_ordinal,
                subject_sha256=row.subject_sha256,
                constraints=row.constraints,
            )
            for row in decision.chapters
            if row.decision == 'request_repair'
        ]
        if not rows:
            raise ApprovalRequired('当前 design 决定没有 request_repair 章节')
        _require_design_repair_capability(decision)
        return DesignRepairScope(
            candidate_sha256=decision.candidate_sha256,
            aggregate_subject_sha256=decision.aggregate_subject_sha256,
            decision_sha256=decision.decision_sha256,
            binding_infeasibility=decision.binding_infeasibility,
            repair_capability=decision.repair_capability,
            strategy_replan=False,
            target_chapters=[row.output_ordinal for row in rows],
            chapters=rows,
        )


def approve_structure(
    base: str | os.PathLike[str], sid: str, request: StructureApprovalRequest,
) -> ApprovalDoc:
    """保存一次原子的全书结构裁决；本函数不启动任何作业。"""
    with approval_file_lock(base, sid):
        record = _read_current(base, sid)
        if missing := [
            key for key in _GATE_REQUIREMENTS['s4'] if not record.gate[key]
        ]:
            raise ApprovalRequired(
                f's4 structure decision requires prior approvals: {missing}')
        novel_dir = paths.resolve_novel_dir(base, sid)
        current_subject, planned = _current_structure_subject_and_chapters(novel_dir)
        if request.subject_sha256 != current_subject:
            raise HumanEditProtected(
                'Stage4 结构审批对象已变化：'
                f'提交 {request.subject_sha256}，当前 {current_subject}；请刷新后重审')
        decided = [row.output_ordinal for row in request.chapters]
        if decided != planned:
            raise HumanEditProtected(
                f'结构裁决章集必须精确等于当前计划：提交 {decided}，当前 {planned}')

        proof = _proof_for_structure(novel_dir, request)
        # proof 会重新读取完整计划字节；再看一遍语义 subject 与章集，避免计划在两次读取
        # 之间变化却把 A 的 subject 与 B 的字节 proof 拼成一份立即过期的假批准。
        after_subject, after_planned = _current_structure_subject_and_chapters(novel_dir)
        after_proof = _proof_for_structure(novel_dir, request)
        if (
            (after_subject, after_planned) != (current_subject, planned)
            or after_proof != proof
        ):
            raise HumanEditProtected('Stage4 计划在结构裁决提交期间发生变化；请刷新后重审')

        latest = record.structure_decision
        if (
            latest is not None
            and _structure_decision_payload(
                _public_structure_decision(latest)) == _structure_decision_payload(request)
            and _same_proof(latest.proof, proof)
        ):
            return _view(base, record)

        public = StructureApprovalDecision(
            actor=request.actor,
            reason=request.reason,
            subject_sha256=request.subject_sha256,
            chapters=request.chapters,
            decided_at=_now(),
        )
        stored = _StoredStructureDecision(
            **public.model_dump(mode='json'), proof=proof)
        history = [*record.structure_history, stored]
        gates = dict(record.gate)
        gate_proofs = dict(record.gate_proofs)
        all_accepted = all(
            row.decision == 'accept' for row in request.chapters)
        gates['s4'] = all_accepted
        gate_proofs['s4'] = proof if all_accepted else None
        writing = record.writing
        writing_proof = record.writing_proof
        if not all_accepted:
            writing = False
            writing_proof = None
            for gate in _POST_WRITING_GATES:
                gates[gate] = False
                gate_proofs[gate] = None
        return _write(base, record.model_copy(update={
            'gate': gates,
            'gate_proofs': gate_proofs,
            'writing': writing,
            'writing_proof': writing_proof,
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
        record = _read_current(base, sid)
        if missing := [
            key for key in _GATE_REQUIREMENTS.get(gate, ()) if not record.gate[key]
        ]:
            raise ApprovalRequired(f'{gate} requires prior approvals: {missing}')
        if gate == 's2' and not all(record.seen.values()):
            raise ApprovalRequired('stage2 global and chapter views must both be seen')
        proof = _require_artifacts(base, sid, gate, record)
        gates = dict(record.gate)
        proofs = dict(record.gate_proofs)
        gates[gate] = True
        proofs[gate] = proof
        return _write(base, record.model_copy(update={
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
    if legacy.gate.get('s4') and gates['s2'] and legacy.structure_decision is not None:
        migrated_structure = _migrate_current_legacy_structure(
            novel_dir, legacy.structure_decision,
            legacy.gate_proofs.get('s4'))
        if migrated_structure is not None:
            structure_history.append(migrated_structure)
            structure_decision = migrated_structure
            gates['s4'] = True
            gate_proofs['s4'] = migrated_structure.proof
            backfilled.append('s4')

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

    if legacy.gate.get('s4') and gates['s2'] and legacy.structure_decision is not None:
        migrated_structure = _migrate_current_legacy_structure(
            novel_dir, legacy.structure_decision,
            legacy.gate_proofs.get('s4'))
        if migrated_structure is not None:
            structure_history.append(migrated_structure)
            structure_decision = migrated_structure
            gates['s4'] = True
            gate_proofs['s4'] = migrated_structure.proof
            backfilled.append('s4')

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
    """显式把 v6 收成 v7：保留 s0–s4、seen 与 design/structure/writing 历史。

    s5/s6/s7 的 gate 与 proof 一律清除——v7 的 s5 只能由当前 typed closure
    回执打开，backfill 不伪造 Commander 回执。写作授权的 typed 历史保留，
    但与 v5 迁移一样按当前盘面重新对证后才能投影成现行授权。
    """
    if legacy.sid != sid:
        raise HumanEditProtected(
            f'approval state belongs to {legacy.sid!r}, not requested novel {sid!r}')
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
            record = _read_current(base, sid)
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
                record, migrated_gates = migrate(base, sid, legacy)
                migrated = True
                parsed = True
                break
            if not parsed:
                raise HumanEditProtected(
                    'approval record cannot be explicitly backfilled as '
                    'v2/v3/v4/v5/v6: no legacy reader accepted the bytes')
        novel_dir = paths.resolve_novel_dir(base, sid)
        gates = dict(record.gate)
        gate_proofs = dict(record.gate_proofs)
        seen = dict(record.seen)
        seen_proofs = dict(record.seen_proofs)
        writing = record.writing
        writing_proof = record.writing_proof
        backfilled: list[str] = list(migrated_gates)

        for gate in _GATE_ORDER:
            if gates[gate]:
                continue
            # Stage4 不能从「盘上有一份计划」推成人已经逐章审过；只有
            # ``approve_structure`` 能生成 actor/reason/subject-bound typed 决定。
            if gate == 's4':
                continue
            if any(not gates[key] for key in _GATE_REQUIREMENTS.get(gate, ())):
                continue

            if gate == 's2':
                try:
                    seen_proofs = {
                        key: _proof_for_seen(novel_dir, key)
                        for key in APPROVAL_SEEN
                    }
                except NovelError:
                    continue
                seen = {key: True for key in APPROVAL_SEEN}

            if gate == 's5':
                if state.stage5_legacy_paths(novel_dir):
                    continue
                try:
                    _require_stage5_complete(base, sid, record)
                    writing_proof = _proof_for_writing(novel_dir)
                except NovelError:
                    continue
                writing = True

            working = record.model_copy(update={
                'gate': gates, 'gate_proofs': gate_proofs,
                'seen': seen, 'seen_proofs': seen_proofs,
                'writing': writing, 'writing_proof': writing_proof,
            })
            try:
                proof = _proof_for_gate(novel_dir, gate, working)
            except NovelError:
                continue
            gates[gate] = True
            gate_proofs[gate] = proof
            backfilled.append(gate)

        updated = record.model_copy(update={
            'gate': gates, 'gate_proofs': gate_proofs,
            'seen': seen, 'seen_proofs': seen_proofs,
            'writing': writing, 'writing_proof': writing_proof,
        })
        approvals_view = _write(base, updated) if (backfilled or migrated) else _view(base, updated)
        return ApprovalBackfillResult(
            approvals=approvals_view, backfilled=backfilled)


def mark_seen(base: str | os.PathLike[str], sid: str, key: str) -> ApprovalDoc:
    if key not in _SEEN_ARTIFACTS:
        raise ApprovalRequired(f'unknown stage2 view: {key}')
    with approval_file_lock(base, sid):
        record = _read_current(base, sid)
        novel_dir = paths.resolve_novel_dir(base, sid)
        proof = _proof_for_seen(novel_dir, key)
        seen = dict(record.seen)
        proofs = dict(record.seen_proofs)
        seen[key] = True
        proofs[key] = proof
        return _write(base, record.model_copy(update={
            'seen': seen, 'seen_proofs': proofs,
        }))


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
            subject_sha256=subject,
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
            actor=actor, reason=reason, subject_sha256=subject)


def approve_writing(
    base: str | os.PathLike[str], sid: str, request: WritingApprovalRequest,
) -> ApprovalDoc:
    """保存 Stage5 typed 写作授权；不启动模型、作业或任何 continuation。"""
    with approval_file_lock(base, sid):
        record = _read_current(base, sid)
        novel_dir, current_subject, current_proof = _writing_prerequisites(
            base, sid, record)
        if request.subject_sha256 != current_subject:
            raise HumanEditProtected(
                'Stage5 写作审批对象已变化：'
                f'提交 {request.subject_sha256}，当前 {current_subject}；请刷新模板后重审')
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

        latest = record.writing_decision
        if (
            latest is not None
            and _writing_decision_payload(
                _public_writing_decision(latest)) == _writing_decision_payload(request)
            and _same_proof(latest.proof, decision_proof)
        ):
            if record.writing and _same_proof(record.writing_proof, current_proof):
                return _view(base, record)
            return _write(base, record.model_copy(update={
                'writing': True,
                'writing_proof': current_proof,
            }))

        public = WritingApprovalDecision(
            actor=request.actor,
            reason=request.reason,
            subject_sha256=request.subject_sha256,
            decided_at=_now(),
        )
        stored = _StoredWritingDecision(
            **public.model_dump(mode='json'), proof=decision_proof)
        return _write(base, record.model_copy(update={
            'writing': True,
            'writing_proof': current_proof,
            'writing_history': [*record.writing_history, stored],
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
        record = _read_current(base, sid)
        _novel_dir, _subject, proof = _writing_prerequisites(base, sid, record)
        if record.writing:
            return _view(base, record)
        return _write(base, record.model_copy(update={
            'writing': True,
            'writing_proof': proof,
        }))


def authorize_writing(
    base: str | os.PathLike[str],
    sid: str,
    chapters: list[int] | None,
    *,
    requires_fresh_review: bool = False,
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
        if requires_fresh_review or current_review.state != 'missing':
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
