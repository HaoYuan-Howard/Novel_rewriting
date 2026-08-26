#!/usr/bin/env python3
from __future__ import annotations

from contracts.artifacts import (
    STAGE0_CLEAN_TEXT, STAGE0_REVISIONS, STAGE1_HOOK_COPY, STAGE1_MAIN_TEXT,
    STAGE2_CHAPTER_BLUEPRINTS,
    STAGE2_GLOBAL_GROUNDING, STAGE2_GLOBAL_REVIEW, STAGE3_STYLE_GROUNDING,
    STAGE4_PLAN_CHAPTERS,
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

from contracts.invariants import APPROVAL_GATES, APPROVAL_SEEN
from contracts.schema import artifact_spec
from contracts.schema.api import (
    ApprovalAction, ApprovalBackfillResult, ApprovalDoc,
    StructureApprovalDecision, StructureApprovalRequest, StructureApprovalView,
)
from contracts.schema.evaluation import GlobalReview
from contracts.schema.stage0 import Stage0Revisions
from contracts.schema.stage1 import HookCopy, MainText
from contracts.schema.stage2 import GlobalGrounding
from contracts.schema.stage4 import PlanChapters
from contracts.schema.stage6 import Hook
from contracts.schema.stage7 import NameMap
from contracts.schema.base import StrictModel
from novel import paths, prov
from novel.errors import (
    ApprovalFrozen, ApprovalRequired, ArtifactUnreadable, GateBlocked, HumanEditProtected,
    NovelError, UpstreamMissing,
)
from novel.orchestrator import state
from novel.quality.global_review import (
    review_status as global_review_status,
    subject_sha256 as global_subject_sha256,
)
from novel.stages.stage4.phase_state import current_infeasible_binding
from novel.store import ArtifactStore

APPROVALS_DIRNAME = 'approvals'
_LOCK = threading.RLock()

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
        if set(self.gate) != set(APPROVAL_GATES) or set(self.gate_proofs) != set(APPROVAL_GATES):
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
        if set(self.gate) != set(APPROVAL_GATES):
            raise ValueError('gate must contain exactly s0..s7')
        if set(self.seen) != set(APPROVAL_SEEN):
            raise ValueError('seen must contain exactly read/global')
        if set(self.gate_proofs) != set(APPROVAL_GATES):
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


class _ApprovalRecordV4(StrictModel):
    """盘上审批记录 v4；Stage4 只认 hash-bound typed 结构裁决。"""

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
    def _record_is_complete_and_proved(self) -> _ApprovalRecordV4:
        if set(self.gate) != set(APPROVAL_GATES):
            raise ValueError('gate must contain exactly s0..s7')
        if set(self.seen) != set(APPROVAL_SEEN):
            raise ValueError('seen must contain exactly read/global')
        if set(self.gate_proofs) != set(APPROVAL_GATES):
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


def _path(base: str | os.PathLike[str], sid: str) -> Path:
    return Path(os.fspath(base)) / '_service' / APPROVALS_DIRNAME / f'{sid}.json'


def _spawns(base: str | os.PathLike[str], sid: str) -> dict[str, list[ApprovalAction]]:
    """Gate follow-ups that still need to run on the current artifact surface.

    These actions used to be an unconditional constant.  That made approving a
    fresh stage2 artifact offer (and then execute) another paid ``stage4.plan``
    run.  The queue and the gate now ask the same resume predicate; the browser
    remains a renderer and never has to infer freshness or cost itself.
    """
    candidates = {
        's1': [ApprovalAction(
            kind='stage2.global', label='生成 stage2 全局底座；完成后逐项人工复核')],
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
    # Stage4 是两次互相独立的付费确认，不是一个按钮里塞两项作业。A 尚未完成时
    # 只能展示 A；A 已落盘后才展示 B。这样服务端给 GateBar 的永远是一条可预览
    # 动作，浏览器不猜阶段，也不会在一次确认里自动串行两次调用。
    design = ApprovalAction(kind='stage4.design', label='Stage4A 盲结构设计')
    binding = ApprovalAction(kind='stage4.plan', label='Stage4B 证据绑定与计划编译')
    if state.resume_verdict(overview, design.kind)[0] != state.RESUME_SKIP:
        spawns['s2'] = [design]
    elif state.resume_verdict(overview, binding.kind)[0] != state.RESUME_SKIP:
        spawns['s2'] = [binding]
    else:
        spawns['s2'] = []
    return spawns


def _fresh_record(sid: str, planned: list[int] | None = None) -> _ApprovalRecordV4:
    del planned  # 兼容旧调用缝；结构章集只从当前正式计划读取
    return _ApprovalRecordV4(
        sid=sid,
        gate={key: False for key in APPROVAL_GATES},
        seen={key: False for key in APPROVAL_SEEN},
        gate_proofs={key: None for key in APPROVAL_GATES},
        seen_proofs={key: None for key in APPROVAL_SEEN},
    )


def _view(
    base: str | os.PathLike[str], record: _ApprovalRecordV4,
) -> ApprovalDoc:
    """服务记录的唯一公开投影；public v3 明示结构裁决的五态。"""
    novel_dir = paths.resolve_novel_dir(base, record.sid)
    structure = _structure_view(novel_dir, record)
    spawns = _spawns(base, record.sid)
    replan_cause = _stage4_replan_cause(novel_dir, structure)
    if replan_cause is not None:
        # 保存 typed request_replan 或 B 落下 fresh infeasible 都不自动发作业；
        # 付费重规划必须由人另点一次，走正常 preview/confirm。显式 --replan
        # 绕过当前 fresh A 的 skip，且只重跑 A。此时不得再把会复用同一份
        # infeasible 的 B 暴露在 s2 下，避免给人一条必然原地返回的假恢复路。
        label = (
            'Stage4B 证据不可行，重新生成 Stage4A 盲候选'
            if replan_cause == 'evidence_infeasible'
            else '按结构裁决重新生成 Stage4A 盲候选'
        )
        spawns = {
            **spawns,
            's2': [],
            's4': [ApprovalAction(
                kind='stage4.design',
                label=label,
                extra=['--replan'],
            )],
        }
    return ApprovalDoc(
        sid=record.sid, gate=record.gate, seen=record.seen,
        writing=record.writing,
        structure=structure,
        spawns=spawns,
    )


def _planned(base: str | os.PathLike[str], sid: str) -> list[int] | None:
    novel_dir = paths.resolve_novel_dir(base, sid)
    ctx = state.readonly_ctx(novel_dir, 'stage5.write', prov.code_version())
    return state.planned_chapters(ArtifactStore(ctx))


def _read_raw(base: str | os.PathLike[str], sid: str) -> _ApprovalRecordV4:
    paths.resolve_novel_dir(base, sid)
    p = _path(base, sid)
    if not p.is_file():
        return _fresh_record(sid, _planned(base, sid))
    try:
        record = _ApprovalRecordV4.model_validate_json(p.read_text(encoding='utf-8'))
    except (OSError, UnicodeDecodeError, ValidationError) as exc:
        raise HumanEditProtected(
            f'approval record is corrupt or is unsupported v1/v2/v3; '
            f'reset/backfill explicitly: {exc}') from exc
    if record.sid != sid:
        raise HumanEditProtected(
            f'approval state belongs to {record.sid!r}, not requested novel {sid!r}')
    return record


def _read_current(base: str | os.PathLike[str], sid: str) -> _ApprovalRecordV4:
    record = _read_raw(base, sid)
    if not _path(base, sid).is_file():
        return record
    novel_dir = paths.resolve_novel_dir(base, sid)
    return _effective_record(novel_dir, record)


def _write(
    base: str | os.PathLike[str], record: _ApprovalRecordV4,
) -> ApprovalDoc:
    """审批落盘的**唯一落点**；冻结检查因此也只装在这里。"""
    record = _ApprovalRecordV4.model_validate(record.model_dump())
    _refuse_if_frozen(record.sid)
    p = _path(base, record.sid)
    p.parent.mkdir(parents=True, exist_ok=True)
    tmp = p.with_suffix(f'.{threading.get_ident()}.tmp')
    tmp.write_text(record.model_dump_json(indent=2), encoding='utf-8')
    os.replace(tmp, p)
    return _view(base, record)


def get(base: str | os.PathLike[str], sid: str) -> ApprovalDoc:
    with _LOCK:
        return _view(base, _read_current(base, sid))


def writing_is_current(base: str | os.PathLike[str], sid: str) -> bool:
    """是否有一份绑定当前 Stage3 机械基线与 Stage4 计划的 Stage5 写作授权。"""
    with _LOCK:
        try:
            if not _path(base, sid).is_file():
                paths.resolve_novel_dir(base, sid)
                return False
            record = _read_current(base, sid)
        except (NovelError, OSError, UnicodeDecodeError, ValidationError):
            return False
        return bool(record.writing and record.gate['s3'] and record.gate['s4'])


def has_record(base: str | os.PathLike[str], sid: str) -> bool:
    return writing_is_current(base, sid)


_GATE_ARTIFACTS = {
    's0': (STAGE0_REVISIONS,),
    's1': (STAGE1_MAIN_TEXT, STAGE1_HOOK_COPY),
    's2': (STAGE2_GLOBAL_GROUNDING, STAGE2_CHAPTER_BLUEPRINTS, STAGE2_GLOBAL_REVIEW),
    's3': (STAGE3_STYLE_GROUNDING,),
    's4': (STAGE4_PLAN_CHAPTERS,),
    's6': (STAGE6_HOOK,),
    's7': (STAGE7_NAME_MAP,),
}

_GATE_ORDER = ('s0', 's1', 's2', 's3', 's4', 's5', 's6', 's7')

_GATE_REQUIREMENTS = {
    's1': ('s0',), 's2': ('s1',), 's3': ('s1',), 's4': ('s2',),
    's5': ('s3', 's4'), 's6': ('s5',), 's7': ('s6',),
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


def _current_structure_subject_and_chapters(
    novel_dir: Path,
) -> tuple[str, list[int]]:
    """读取当前正式计划并计算人真正审阅的结构决策面 hash。"""
    store = _store_for(novel_dir)
    _require_fresh_json(store, STAGE4_PLAN_CHAPTERS)
    plan, _raw = store.read_own(PlanChapters, STAGE4_PLAN_CHAPTERS)
    try:
        from novel.stages.stage4.measure import structure_subject_sha256
    except ImportError as exc:
        raise ArtifactUnreadable(
            'stage4 structure subject helper is unavailable') from exc
    subject = structure_subject_sha256(plan)
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


def _proof_for_structure(
    novel_dir: Path,
    decision: StructureApprovalRequest | StructureApprovalDecision,
) -> _ApprovalProof:
    """同时绑定正文根、完整计划字节与逐章 typed 决定。"""
    return _make_proof(
        novel_dir, (STAGE4_PLAN_CHAPTERS,),
        _structure_decision_payload(decision),
    )


def _structure_view(
    novel_dir: Path, record: _ApprovalRecordV4,
) -> StructureApprovalView:
    """即时投影当前结构态；失效只影响有效性，不擦掉历史决定。"""
    history = [
        _public_structure_decision(decision)
        for decision in record.structure_history
    ]
    latest = record.structure_decision
    try:
        current_subject, _chapters = _current_structure_subject_and_chapters(novel_dir)
    except NovelError:
        return StructureApprovalView(
            state='expired' if history else 'unavailable',
            current_subject_sha256=None,
            current_decision=None,
            history=history,
        )
    if latest is None:
        return StructureApprovalView(
            state='missing', current_subject_sha256=current_subject,
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
            current_decision=None, history=history,
        )
    state = (
        'replan_requested'
        if any(row.decision == 'request_replan' for row in public.chapters)
        else 'accepted'
    )
    return StructureApprovalView(
        state=state, current_subject_sha256=current_subject,
        current_decision=public, history=history,
    )


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
        review, _ = store.read_own(GlobalReview, STAGE2_GLOBAL_REVIEW)
        grounding, _ = store.read_own(GlobalGrounding, STAGE2_GLOBAL_GROUNDING)
        if not global_review_status(
            grounding.model_dump(by_alias=True), review,
        )[2]:
            raise ApprovalRequired(
                'stage2 global review is stale, incomplete, or not all PASS')
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


def _proof_for_legacy_writing(novel_dir: Path, deep: list[int]) -> _ApprovalProof:
    return _make_proof(
        novel_dir, (STAGE3_STYLE_GROUNDING, STAGE4_PLAN_CHAPTERS),
        {'kind': 'writing', 'deep': deep},
    )


def _proof_for_writing(novel_dir: Path) -> _ApprovalProof:
    return _make_proof(
        novel_dir, (STAGE3_STYLE_GROUNDING, STAGE4_PLAN_CHAPTERS),
        {'kind': 'writing', 'scope': 'all_planned_chapters', 'contract': 'stage5-v2'},
    )


def _stage5_subject_rels(
    base: str | os.PathLike[str], sid: str, record: _ApprovalRecordV4,
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
    novel_dir: Path, gate: str, record: _ApprovalRecordV4,
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
        rels = _stage5_subject_rels(novel_dir.parent, record.sid, record)
    else:
        rels = list(_GATE_ARTIFACTS[gate])
    return _make_proof(
        novel_dir, rels, {'kind': 'gate', 'gate': gate})


def _same_proof(expected: _ApprovalProof | None, current: _ApprovalProof) -> bool:
    return expected is not None and expected == current


def _effective_record(novel_dir: Path, record: _ApprovalRecordV4) -> _ApprovalRecordV4:
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

    for gate in _GATE_ORDER[:5]:
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
            or not gates['s3']
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
    for gate in _GATE_ORDER[5:]:
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
    record: _ApprovalRecordV4,
) -> _ApprovalProof:
    novel_dir = paths.resolve_novel_dir(base, sid)
    return _proof_for_gate(novel_dir, gate, record)


def _require_stage5_complete(
    base: str | os.PathLike[str], sid: str, record: _ApprovalRecordV4,
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


def approve_structure(
    base: str | os.PathLike[str], sid: str, request: StructureApprovalRequest,
) -> ApprovalDoc:
    """保存一次原子的全书结构裁决；本函数不启动任何作业。"""
    with _LOCK:
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
            for gate in _GATE_ORDER[5:]:
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
    with _LOCK:
        record = _read_current(base, sid)
        if missing := [
            key for key in _GATE_REQUIREMENTS.get(gate, ()) if not record.gate[key]
        ]:
            raise ApprovalRequired(f'{gate} requires prior approvals: {missing}')
        if gate == 's2' and not all(record.seen.values()):
            raise ApprovalRequired('stage2 global and chapter views must both be seen')
        if gate == 's5':
            if not record.writing:
                raise ApprovalRequired('authorize Stage5 whole-book writing first')
            _require_stage5_complete(base, sid, record)
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
    _require_typed_gate_decision(store, gate)
    return _make_proof(
        novel_dir, list(_GATE_ARTIFACTS[gate]),
        {'kind': 'gate', 'gate': gate, 'deep': None},
    )


def _migrate_legacy_v2(
    base: str | os.PathLike[str], sid: str, legacy: _ApprovalRecordV2Legacy,
) -> tuple[_ApprovalRecordV4, list[str]]:
    """保守重签 v2。

    只有能对上当前原文/产物的 s0～s3 proof 才保留。旧记录没有逐章 typed
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
    for gate in _GATE_ORDER[:4]:
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
) -> tuple[_ApprovalRecordV4, list[str]]:
    """显式把旧 v3 收成 v4；只能保留能对证的 s0～s3。"""
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
    for gate in _GATE_ORDER[:4]:
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


def backfill_from_disk(
    base: str | os.PathLike[str], sid: str,
) -> ApprovalBackfillResult:
    """补录 CLI/队列已经落成的链，并把补了哪些章结构化返回给界面。

    这是显式的人操作，不在 GET 时静默发生。普通逐屏验收仍走 `approve_gate`；
    本入口只在对应正式产物齐备且没有 `.INVALID` 时补章。Stage5 只认
    全部 canonical `stage5/chNN.json` + fresh Review。
    """
    with _LOCK:
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
            try:
                legacy_v3 = _ApprovalRecordV3Legacy.model_validate_json(raw)
            except ValidationError:
                try:
                    legacy_v2 = _ApprovalRecordV2Legacy.model_validate_json(raw)
                except ValidationError as exc:
                    raise HumanEditProtected(
                        'approval record cannot be explicitly backfilled as v2/v3: '
                        f'{exc}') from exc
                record, migrated_gates = _migrate_legacy_v2(base, sid, legacy_v2)
            else:
                record, migrated_gates = _migrate_legacy_v3(base, sid, legacy_v3)
            migrated = True
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
    with _LOCK:
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


def begin_writing(
    base: str | os.PathLike[str], sid: str,
) -> ApprovalDoc:
    with _LOCK:
        record = _read_current(base, sid)
        novel_dir = paths.resolve_novel_dir(base, sid)
        _require_stage5_contract_compatible(novel_dir, action='授权新作业')
        if not record.gate['s3'] or not record.gate['s4']:
            raise ApprovalRequired(
                'stage3 mechanical baseline and stage4 plan must be approved first')
        planned = _planned(base, sid)
        if planned is None:
            raise UpstreamMissing(f'{STAGE4_PLAN_CHAPTERS} is required before writing')
        if not planned:
            raise ApprovalRequired('stage4 plan contains no output chapters')
        if record.writing:
            return _view(base, record)
        proof = _proof_for_writing(novel_dir)
        return _write(base, record.model_copy(update={
            'writing': True, 'writing_proof': proof,
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


def reset(base: str | os.PathLike[str], sid: str) -> ApprovalDoc:
    with _LOCK:
        novel_dir = paths.resolve_novel_dir(base, sid)
        if _has_stage5_work(novel_dir):
            raise HumanEditProtected('cannot reset approvals after stage5 drafts exist')
        return _write(base, _fresh_record(sid, _planned(base, sid)))
