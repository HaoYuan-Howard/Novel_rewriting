#!/usr/bin/env python3
from __future__ import annotations

from contracts.artifacts import (
    STAGE0_CLEAN_TEXT, STAGE0_REMOVAL_REPORT, STAGE1_MAIN_TEXT, STAGE2_CHAPTER_BLUEPRINTS,
    STAGE2_GLOBAL_GROUNDING, STAGE2_GLOBAL_REVIEW, STAGE3_STYLE_GROUNDING, STAGE4_PLAN_CHAPTERS,
    STAGE5_BOOK_DRAFT, STAGE6_HOOK, STAGE7_NAME_MAP, artifact_rel,
)

import hashlib
import json
import os
import threading
from pathlib import Path
from typing import Any, Literal

from pydantic import Field, ValidationError, model_validator

from contracts.invariants import APPROVAL_GATES, APPROVAL_SEEN
from contracts.schema import artifact_spec
from contracts.schema.api import ApprovalAction, ApprovalBackfillResult, ApprovalDoc
from contracts.schema.evaluation import GlobalReview
from contracts.schema.stage0 import RemovalReport
from contracts.schema.stage2 import GlobalGrounding
from contracts.schema.stage6 import Hook
from contracts.schema.stage7 import NameMap
from contracts.schema.base import StrictModel
from novel import paths, prov
from novel.errors import (
    ApprovalRequired, ArtifactUnreadable, GateBlocked, HumanEditProtected, NovelError,
    UpstreamMissing,
)
from novel.orchestrator import state
from novel.quality.global_review import (
    review_status as global_review_status,
    subject_sha256 as global_subject_sha256,
)
from novel.store import ArtifactStore

APPROVALS_DIRNAME = 'approvals'
_LOCK = threading.RLock()


class _ApprovalProof(StrictModel):
    """Exact current subject of one persisted human/service decision.

    The service record is deliberately not an artifact and therefore does not
    participate in pipeline provenance.  It still needs the same two anchors:
    the current clean-text root and the exact bytes a person accepted.  The
    decision digest additionally binds choices such as the deep chapter list.
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


class _ApprovalRecordV2(StrictModel):
    """On-disk approval record; old v1 booleans are intentionally not migrated.

    A bare boolean cannot prove which artifact was approved.  Reading a v1
    record as if it referred to today's files would invent that fact, so v2 is
    a hard boundary: reset/backfill is explicit and both paths create proofs.
    """

    v: Literal[2] = 2
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
    def _record_is_complete_and_proved(self) -> _ApprovalRecordV2:
        if set(self.gate) != set(APPROVAL_GATES):
            raise ValueError('gate must contain exactly s0..s7')
        if set(self.seen) != set(APPROVAL_SEEN):
            raise ValueError('seen must contain exactly read/global')
        if set(self.gate_proofs) != set(APPROVAL_GATES):
            raise ValueError('gate_proofs must contain exactly s0..s7')
        if set(self.seen_proofs) != set(APPROVAL_SEEN):
            raise ValueError('seen_proofs must contain exactly read/global')
        if self.deep != sorted(set(self.deep)) or any(n < 1 for n in self.deep):
            raise ValueError('deep must be sorted, unique positive chapter ordinals')
        for key, approved in self.gate.items():
            if approved != (self.gate_proofs[key] is not None):
                raise ValueError(f'gate {key} and its current-subject proof must agree')
        for key, seen in self.seen.items():
            if seen != (self.seen_proofs[key] is not None):
                raise ValueError(f'seen {key} and its current-subject proof must agree')
        if self.writing != (self.writing_proof is not None):
            raise ValueError('writing and writing_proof must agree')
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
            kind='stage2.global', label='\u5e76\u53d1\u62bd\u53d6 stage2 + stage3',
            extra=['--full-extract'])],
        's2': [ApprovalAction(
            kind='stage4.plan', label='\u751f\u6210\u91cd\u6784\u8ba1\u5212')],
        's5': [ApprovalAction(
            kind='stage6.hook', label='\u751f\u6210\u5f00\u7bc7\u6587\u6848')],
        's6': [ApprovalAction(
            kind='stage7.rename', label='\u6267\u884c\u4eba\u7269\u6539\u540d')],
        's7': [ApprovalAction(
            kind='stage7.assemble', label='\u62fc\u88c5\u6700\u7ec8\u6210\u4e66')],
    }
    overview = state.overview(base, sid)
    return {
        gate: [action for action in actions
               if state.resume_verdict(overview, action.kind)[0] != state.RESUME_SKIP]
        for gate, actions in candidates.items()
    }


def default_deep(planned: list[int] | None) -> list[int]:
    """默认深度选择 = **这本书的全部计划章**（2026-08-22 起；此前是常量 `(1, 2, 3)`）。

    旧默认的理由是「开篇三章决定读者留存，值得更高的单章成本」。推翻它的是一次
    **成书级**实测（无情道 23 章，`tools/book_overlap.py`）：按 `stage7/name_map.json`
    把人名回映射之后逐章量 20 字窗重合——

    | 臂 | n | 中位 | 整句照搬率 |
    |---|--:|--:|--:|
    | deep（闭卷） | 3 | **0.017** | **18.2%** |
    | shallow（开卷） | 20 | **0.226** | **55.4%** |

    **13 倍差距**；而全书 290 条照搬句里只有 60 条是 `verbatim` 制度要求的，其余
    230 条（79%）是写手自发照抄。⇒ 用户那句「差别只是改变了措辞」几乎全部产自开卷臂，
    而开卷当时是 20/23 章的默认路径。

    ⚠ **这不是把开卷判死**：单臂维护入口 `stage5.write --mode shallow` 一个字没动，
    `B-03` 那个洞仍在代码里。变的只是**默认路径** ⇒ 走开卷从此是例外，
    由 `arm_split_report` 常驻报出（只报不判，不为它发明阈值——2026-08-10 已否决过一次，
    理由「0.9%~96.8% 的跨度不是一个可以一刀切的量」今天仍然成立）。

    ⚠ 返回值仍然只是**建议，不是花钱授权**：`_fresh_record` 不带 `deep_proof`，而
    `current_depth_selection` / `begin_writing` 都要求 proof 在场
    ⇒ 没有人签过字之前，队列里不会出现一条付费的 `stage5.write`。

    ⚠ `planned is None`（stage4 还没产计划）⇒ **`[]` 而不是 `[1, 2, 3]`**：
    「还不知道有哪些章」与「选了三章」必须分得开。这不会把默认卡在空集上——
    `_read_raw` 在没有盘上记录时每次都现算一份，计划一落盘它自动就是全书。
    """
    return sorted(set(planned)) if planned else []


def _fresh_record(sid: str, planned: list[int] | None = None) -> _ApprovalRecordV2:
    deep = default_deep(planned)
    return _ApprovalRecordV2(
        sid=sid,
        gate={key: False for key in APPROVAL_GATES},
        seen={key: False for key in APPROVAL_SEEN},
        deep=deep,
        gate_proofs={key: None for key in APPROVAL_GATES},
        seen_proofs={key: None for key in APPROVAL_SEEN},
    )


def _view(
    base: str | os.PathLike[str], record: _ApprovalRecordV2,
) -> ApprovalDoc:
    return ApprovalDoc(
        sid=record.sid, gate=record.gate, seen=record.seen,
        deep=record.deep, writing=record.writing,
        spawns=_spawns(base, record.sid),
    )


def _planned(base: str | os.PathLike[str], sid: str) -> list[int] | None:
    novel_dir = paths.resolve_novel_dir(base, sid)
    ctx = state.readonly_ctx(novel_dir, 'stage5.write', prov.code_version())
    return state.planned_chapters(ArtifactStore(ctx))


def _read_raw(base: str | os.PathLike[str], sid: str) -> _ApprovalRecordV2:
    paths.resolve_novel_dir(base, sid)
    p = _path(base, sid)
    if not p.is_file():
        return _fresh_record(sid, _planned(base, sid))
    try:
        record = _ApprovalRecordV2.model_validate_json(p.read_text(encoding='utf-8'))
    except (OSError, UnicodeDecodeError, ValidationError) as exc:
        raise HumanEditProtected(
            f'approval record is corrupt or is unsupported v1; reset/backfill explicitly: {exc}') from exc
    if record.sid != sid:
        raise HumanEditProtected(
            f'approval state belongs to {record.sid!r}, not requested novel {sid!r}')
    return record


def _read_current(base: str | os.PathLike[str], sid: str) -> _ApprovalRecordV2:
    record = _read_raw(base, sid)
    if not _path(base, sid).is_file():
        return record
    novel_dir = paths.resolve_novel_dir(base, sid)
    return _effective_record(novel_dir, record)


def _write(
    base: str | os.PathLike[str], record: _ApprovalRecordV2,
) -> ApprovalDoc:
    record = _ApprovalRecordV2.model_validate(record.model_dump())
    p = _path(base, record.sid)
    p.parent.mkdir(parents=True, exist_ok=True)
    tmp = p.with_suffix(f'.{threading.get_ident()}.tmp')
    tmp.write_text(record.model_dump_json(indent=2), encoding='utf-8')
    os.replace(tmp, p)
    return _view(base, record)


def get(base: str | os.PathLike[str], sid: str) -> ApprovalDoc:
    with _LOCK:
        return _view(base, _read_current(base, sid))


def has_record(base: str | os.PathLike[str], sid: str) -> bool:
    """Whether Stage5 has a current, approved, persisted spending choice.

    ``get()`` deliberately returns a read-only default for a new book, so its
    value cannot answer this question.  A file alone is also insufficient: an
    old/corrupt record or a choice bound to stale style/plan artifacts must not
    make the queue emit a paid Stage5 command.
    """
    return current_depth_selection(base, sid) is not None


def current_depth_selection(
    base: str | os.PathLike[str], sid: str,
) -> list[int] | None:
    """Atomically return the exact current Stage5 spending decision.

    ``[]`` is a valid all-shallow choice, hence ``None`` is the only blocked
    result.  Queue planning consumes this once instead of a racy ``exists`` +
    ``get`` pair.
    """
    with _LOCK:
        try:
            if not _path(base, sid).is_file():
                paths.resolve_novel_dir(base, sid)
                return None
            record = _read_current(base, sid)
        except (NovelError, OSError, UnicodeDecodeError, ValidationError):
            return None
        if (
            record.deep_proof is None
            or not record.gate['s3']
            or not record.gate['s4']
        ):
            return None
        return list(record.deep)


_GATE_ARTIFACTS = {
    's0': (STAGE0_REMOVAL_REPORT,),
    's1': (STAGE1_MAIN_TEXT,),
    's2': (STAGE2_GLOBAL_GROUNDING, STAGE2_CHAPTER_BLUEPRINTS, STAGE2_GLOBAL_REVIEW),
    's3': (STAGE3_STYLE_GROUNDING,),
    's4': (STAGE4_PLAN_CHAPTERS,),
    's5': (STAGE5_BOOK_DRAFT,),
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


def _require_typed_gate_decision(store: ArtifactStore, gate: str) -> str | None:
    if gate == 's0':
        report, _ = store.read_own(RemovalReport, STAGE0_REMOVAL_REPORT)
        # 付费防线与 stage1 启动闸用同一个谓词（eligibility 是唯一落点），
        # 不在这里复述批准条件——stage2 是第一个花钱的层，漏一处就是绕过闸门花钱。
        from novel.stages.stage0.eligibility import is_stage0_executable
        if not is_stage0_executable(report):
            raise ApprovalRequired(
                'stage0 removal report has not actually been approved')
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


def _proof_for_depth(novel_dir: Path, deep: list[int]) -> _ApprovalProof:
    return _make_proof(
        novel_dir, (STAGE4_PLAN_CHAPTERS,),
        {'kind': 'depth_selection', 'deep': deep},
    )


def _proof_for_writing(novel_dir: Path, deep: list[int]) -> _ApprovalProof:
    return _make_proof(
        novel_dir, (STAGE3_STYLE_GROUNDING, STAGE4_PLAN_CHAPTERS),
        {'kind': 'writing', 'deep': deep},
    )


def _stage5_subject_rels(
    base: str | os.PathLike[str], sid: str, record: _ApprovalRecordV2,
) -> list[str]:
    _require_stage5_complete(base, sid, record)
    planned = _planned(base, sid)
    assert planned is not None
    deep = set(record.deep)
    return [
        artifact_rel(
            'stage5.chapter', mode='deep' if ordinal in deep else 'shallow',
            nn=ordinal,
        )
        for ordinal in planned
    ] + [STAGE5_BOOK_DRAFT]


def _proof_for_gate(
    novel_dir: Path, gate: str, record: _ApprovalRecordV2,
) -> _ApprovalProof:
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
                'kind': 'gate', 'gate': gate, 'deep': None,
            }),
        )
    if gate == 's5':
        rels = _stage5_subject_rels(novel_dir.parent, record.sid, record)
    else:
        rels = list(_GATE_ARTIFACTS[gate])
    return _make_proof(
        novel_dir, rels, {'kind': 'gate', 'gate': gate, 'deep': record.deep if gate == 's5' else None})


def _same_proof(expected: _ApprovalProof | None, current: _ApprovalProof) -> bool:
    return expected is not None and expected == current


def _effective_record(novel_dir: Path, record: _ApprovalRecordV2) -> _ApprovalRecordV2:
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

    deep_proof = record.deep_proof
    if deep_proof is not None:
        try:
            current_depth = _proof_for_depth(novel_dir, record.deep)
        except NovelError:
            current_depth = None
        if current_depth is None or not _same_proof(deep_proof, current_depth):
            deep_proof = None

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
            current_writing = _proof_for_writing(novel_dir, record.deep)
        except NovelError:
            current_writing = None
        if (
            current_writing is None
            or not _same_proof(writing_proof, current_writing)
            or deep_proof is None
            or not gates['s3']
            or not gates['s4']
        ):
            writing = False
            writing_proof = None

    # s5's proof builder needs the effective depth/writing state, not the stale
    # booleans from disk.  Later gates then cascade from the resulting s5 state.
    effective = record.model_copy(update={
        'gate': gates, 'seen': seen,
        'gate_proofs': gate_proofs, 'seen_proofs': seen_proofs,
        'deep_proof': deep_proof, 'writing': writing,
        'writing_proof': writing_proof,
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
    record: _ApprovalRecordV2,
) -> _ApprovalProof:
    novel_dir = paths.resolve_novel_dir(base, sid)
    return _proof_for_gate(novel_dir, gate, record)


def _require_stage5_complete(
    base: str | os.PathLike[str], sid: str, record: _ApprovalRecordV2,
) -> None:
    planned = _planned(base, sid)
    if planned is None:
        raise UpstreamMissing(f'{STAGE4_PLAN_CHAPTERS} is required before stage5 approval')
    novel_dir = paths.resolve_novel_dir(base, sid)
    deep_done = set(state.chapters_done(novel_dir, 'deep'))
    shallow_done = set(state.chapters_done(novel_dir, 'shallow'))
    deep = set(record.deep)
    shallow = set(planned) - deep
    missing = sorted((deep - deep_done) | (shallow - shallow_done))
    if missing:
        raise ApprovalRequired(f'stage5 still lacks assigned chapters: {missing}')
    wrong = sorted((deep & shallow_done) | (shallow & deep_done))
    if wrong:
        raise HumanEditProtected(f'stage5 has drafts in the wrong arm: {wrong}')
    unexpected = sorted((deep_done | shallow_done) - set(planned))
    if unexpected:
        raise HumanEditProtected(f'stage5 has drafts outside the current plan: {unexpected}')
    # Same classification as `_require_fresh_json`: a blocked draft is a gate
    # state, not a missing signature.  Stage5 blocks per chapter, so the pointer
    # is the directory that holds them — `GateBlocked.invalid_path` is documented
    # as "where the blocked draft(s) are", not "one file".
    blocked = sorted(p.relative_to(novel_dir).as_posix()
                     for p in (novel_dir / 'stage5').glob('*/ch*.INVALID.json'))
    if blocked:
        raise GateBlocked(
            f'stage5 still has blocked INVALID chapter drafts: {blocked}; an '
            f'approval cannot bind to them',
            invalid_path=str(novel_dir / 'stage5'),
            hint='stage5.write --mode <arm> --revalidate --actor <id> --reason <why>, '
                 'or --apply-edit to fix those chapters first (both cost nothing)')


def approve_gate(base: str | os.PathLike[str], sid: str, gate: str) -> ApprovalDoc:
    if gate not in APPROVAL_GATES:
        raise ApprovalRequired(f'unknown approval gate: {gate}')
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
                raise ApprovalRequired('select deep chapters and start writing first')
            _require_stage5_complete(base, sid, record)
        proof = _require_artifacts(base, sid, gate, record)
        gates = dict(record.gate)
        proofs = dict(record.gate_proofs)
        gates[gate] = True
        proofs[gate] = proof
        return _write(base, record.model_copy(update={
            'gate': gates, 'gate_proofs': proofs,
        }))


def _artifacts_ready(
    novel_dir: Path, gate: str, record: _ApprovalRecordV2,
) -> bool:
    try:
        _proof_for_gate(novel_dir, gate, record)
    except NovelError:
        return False
    return True


def backfill_from_disk(
    base: str | os.PathLike[str], sid: str,
) -> ApprovalBackfillResult:
    """补录 CLI/队列已经落成的链，并把补了哪些章结构化返回给界面。

    这是显式的人操作，不在 GET 时静默发生。普通逐屏验收仍走 `approve_gate`；
    本入口只在对应正式产物齐备且没有 `.INVALID` 时补章。stage5 还要求计划章
    在两条臂中恰好各有一份成稿，不能拿半本稿冒充已经验收。
    """
    with _LOCK:
        try:
            record = _read_current(base, sid)
        except HumanEditProtected:
            # Backfill is itself the explicit recovery turn.  It does not copy
            # a single legacy boolean; it starts blank and recomputes every v2
            # proof from today's typed/current disk.  A record naming another
            # sid is different: never overwrite someone else's service state.
            path = _path(base, sid)
            try:
                legacy = ApprovalDoc.model_validate_json(
                    path.read_text(encoding='utf-8'))
            except (OSError, UnicodeDecodeError, ValidationError):
                legacy = None
            if legacy is not None and legacy.sid != sid:
                raise
            record = _fresh_record(sid, _planned(base, sid))
        novel_dir = paths.resolve_novel_dir(base, sid)
        gates = dict(record.gate)
        gate_proofs = dict(record.gate_proofs)
        seen = dict(record.seen)
        seen_proofs = dict(record.seen_proofs)
        deep = list(record.deep)
        deep_proof = record.deep_proof
        writing = record.writing
        writing_proof = record.writing_proof
        backfilled: list[str] = []

        for gate in _GATE_ORDER:
            if gates[gate]:
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
                planned = _planned(base, sid)
                if planned is None:
                    continue
                planned_set = set(planned)
                deep_done = set(state.chapters_done(novel_dir, 'deep'))
                shallow_done = set(state.chapters_done(novel_dir, 'shallow'))
                if ((deep_done | shallow_done) & planned_set) != planned_set:
                    continue
                if deep_done & shallow_done & planned_set:
                    continue
                if (deep_done | shallow_done) - planned_set:
                    continue
                if any((novel_dir / 'stage5').glob('*/ch*.INVALID.json')):
                    continue
                deep = sorted(deep_done & planned_set)
                try:
                    deep_proof = _proof_for_depth(novel_dir, deep)
                    writing_proof = _proof_for_writing(novel_dir, deep)
                except NovelError:
                    continue
                writing = True

            working = record.model_copy(update={
                'gate': gates, 'gate_proofs': gate_proofs,
                'seen': seen, 'seen_proofs': seen_proofs,
                'deep': deep, 'deep_proof': deep_proof,
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
            'deep': deep, 'deep_proof': deep_proof,
            'writing': writing, 'writing_proof': writing_proof,
        })
        approvals_view = _write(base, updated) if backfilled else _view(base, updated)
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
    for mode in ('deep', 'shallow'):
        folder = novel_dir / 'stage5' / mode
        if folder.is_dir() and any(folder.glob('ch*.json')):
            return True
    return False


def set_deep(base: str | os.PathLike[str], sid: str, deep: list[int]) -> ApprovalDoc:
    canonical = sorted(set(int(value) for value in deep))
    if canonical != deep or any(value < 1 for value in canonical):
        raise ApprovalRequired('deep selection must be sorted unique positive ordinals')
    with _LOCK:
        record = _read_current(base, sid)
        novel_dir = paths.resolve_novel_dir(base, sid)
        if record.writing or _has_stage5_work(novel_dir):
            raise HumanEditProtected('deep selection is locked after stage5 writing starts')
        planned = _planned(base, sid)
        if planned is None:
            raise UpstreamMissing(f'{STAGE4_PLAN_CHAPTERS} is required before depth selection')
        if outside := sorted(set(canonical) - set(planned)):
            raise ApprovalRequired(f'deep selection contains unplanned chapters: {outside}')
        proof = _proof_for_depth(novel_dir, canonical)
        return _write(base, record.model_copy(update={
            'deep': canonical, 'deep_proof': proof,
        }))


def begin_writing(
    base: str | os.PathLike[str], sid: str, expected: list[int] | None = None,
) -> ApprovalDoc:
    with _LOCK:
        record = _read_current(base, sid)
        if not record.gate['s3'] or not record.gate['s4']:
            raise ApprovalRequired('stage3 style and stage4 plan must be approved first')
        if record.deep_proof is None:
            raise ApprovalRequired('save a current depth selection before writing')
        planned = _planned(base, sid)
        if planned is None:
            raise UpstreamMissing(f'{STAGE4_PLAN_CHAPTERS} is required before writing')
        if outside := sorted(set(record.deep) - set(planned)):
            raise ApprovalRequired(f'saved deep selection is outside the plan: {outside}')
        if expected is not None and list(expected) != record.deep:
            raise HumanEditProtected(
                f'writing request depth {list(expected)} differs from approved {record.deep}')
        if record.writing:
            return _view(base, record)
        proof = _proof_for_writing(
            paths.resolve_novel_dir(base, sid), record.deep)
        return _write(base, record.model_copy(update={
            'writing': True, 'writing_proof': proof,
        }))


def authorize_arm(
    base: str | os.PathLike[str],
    sid: str,
    mode: str,
    chapters: list[int] | None,
) -> None:
    if mode not in ('deep', 'shallow'):
        raise ApprovalRequired(f'unknown stage5 writing arm: {mode!r}')
    with _LOCK:
        record = _read_current(base, sid)
        if not record.writing:
            raise ApprovalRequired('select deep chapters and start composite writing first')
        planned = _planned(base, sid)
        if planned is None:
            raise UpstreamMissing(f'{STAGE4_PLAN_CHAPTERS} is required before writing')
        requested = set(planned if chapters is None else chapters)
        allowed = (
            set(record.deep) if mode == 'deep'
            else set(planned) - set(record.deep)
        )
        if outside := sorted(requested - allowed):
            raise HumanEditProtected(
                f'{mode} arm request contains chapters assigned to the other arm: {outside}')


def reset(base: str | os.PathLike[str], sid: str) -> ApprovalDoc:
    with _LOCK:
        novel_dir = paths.resolve_novel_dir(base, sid)
        if _has_stage5_work(novel_dir):
            raise HumanEditProtected('cannot reset approvals after stage5 drafts exist')
        return _write(base, _fresh_record(sid, _planned(base, sid)))
