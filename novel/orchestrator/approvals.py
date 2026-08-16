#!/usr/bin/env python3
from __future__ import annotations

from contracts.artifacts import (
    STAGE0_REMOVAL_REPORT, STAGE1_MAIN_TEXT, STAGE2_CHAPTER_BLUEPRINTS,
    STAGE2_GLOBAL_GROUNDING, STAGE3_STYLE_GROUNDING, STAGE4_PLAN_CHAPTERS,
    STAGE5_BOOK_DRAFT, STAGE6_HOOK, STAGE7_NAME_MAP,
)

import os
import threading
from pathlib import Path

from contracts.invariants import APPROVAL_GATES, APPROVAL_SEEN
from contracts.schema.api import ApprovalAction, ApprovalDoc
from novel import paths, prov
from novel.errors import ApprovalRequired, HumanEditProtected, UpstreamMissing
from novel.orchestrator import state
from novel.store import ArtifactStore

APPROVALS_DIRNAME = 'approvals'
DEFAULT_DEEP = (1, 2, 3)
_LOCK = threading.RLock()


def _path(base: str | os.PathLike[str], sid: str) -> Path:
    return Path(os.fspath(base)) / '_service' / APPROVALS_DIRNAME / f'{sid}.json'


def _spawns() -> dict[str, list[ApprovalAction]]:
    return {
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


def _fresh(sid: str, planned: list[int] | None = None) -> ApprovalDoc:
    allowed = set(planned) if planned is not None else None
    deep = [n for n in DEFAULT_DEEP if allowed is None or n in allowed]
    return ApprovalDoc(
        sid=sid,
        gate={key: False for key in APPROVAL_GATES},
        seen={key: False for key in APPROVAL_SEEN},
        deep=deep,
        spawns=_spawns(),
    )


def _planned(base: str | os.PathLike[str], sid: str) -> list[int] | None:
    novel_dir = paths.resolve_novel_dir(base, sid)
    ctx = state.readonly_ctx(novel_dir, 'stage5.write', prov.code_version())
    return state.planned_chapters(ArtifactStore(ctx))


def _read(base: str | os.PathLike[str], sid: str) -> ApprovalDoc:
    paths.resolve_novel_dir(base, sid)
    p = _path(base, sid)
    if not p.is_file():
        return _fresh(sid, _planned(base, sid))
    doc = ApprovalDoc.model_validate_json(p.read_text(encoding='utf-8'))
    if doc.sid != sid:
        raise HumanEditProtected(
            f'approval state belongs to {doc.sid!r}, not requested novel {sid!r}')
    return doc.model_copy(update={'spawns': _spawns()})


def _write(base: str | os.PathLike[str], doc: ApprovalDoc) -> ApprovalDoc:
    p = _path(base, doc.sid)
    p.parent.mkdir(parents=True, exist_ok=True)
    tmp = p.with_suffix(f'.{threading.get_ident()}.tmp')
    tmp.write_text(doc.model_dump_json(indent=2), encoding='utf-8')
    os.replace(tmp, p)
    return doc


def get(base: str | os.PathLike[str], sid: str) -> ApprovalDoc:
    with _LOCK:
        return _read(base, sid)


_GATE_ARTIFACTS = {
    's0': (STAGE0_REMOVAL_REPORT,),
    's1': (STAGE1_MAIN_TEXT,),
    's2': (STAGE2_GLOBAL_GROUNDING, STAGE2_CHAPTER_BLUEPRINTS),
    's3': (STAGE3_STYLE_GROUNDING,),
    's4': (STAGE4_PLAN_CHAPTERS,),
    's5': (STAGE5_BOOK_DRAFT,),
    's6': (STAGE6_HOOK,),
    's7': (STAGE7_NAME_MAP,),
}


def _require_artifacts(base: str | os.PathLike[str], sid: str, gate: str) -> None:
    novel_dir = paths.resolve_novel_dir(base, sid)
    for rel in _GATE_ARTIFACTS.get(gate, ()):
        if not (novel_dir / rel).is_file() or (novel_dir / paths.invalid_of(rel)).is_file():
            raise UpstreamMissing(f'{gate} cannot be approved before {rel} is valid')


def _require_stage5_complete(
    base: str | os.PathLike[str], sid: str, doc: ApprovalDoc,
) -> None:
    planned = _planned(base, sid)
    if planned is None:
        raise UpstreamMissing(f'{STAGE4_PLAN_CHAPTERS} is required before stage5 approval')
    novel_dir = paths.resolve_novel_dir(base, sid)
    deep_done = set(state.chapters_done(novel_dir, 'deep'))
    shallow_done = set(state.chapters_done(novel_dir, 'shallow'))
    deep = set(doc.deep)
    shallow = set(planned) - deep
    missing = sorted((deep - deep_done) | (shallow - shallow_done))
    if missing:
        raise ApprovalRequired(f'stage5 still lacks assigned chapters: {missing}')
    wrong = sorted((deep & shallow_done) | (shallow & deep_done))
    if wrong:
        raise HumanEditProtected(f'stage5 has drafts in the wrong arm: {wrong}')
    if any((novel_dir / 'stage5').glob('*/ch*.INVALID.json')):
        raise ApprovalRequired('stage5 still has blocked INVALID chapter drafts')


def approve_gate(base: str | os.PathLike[str], sid: str, gate: str) -> ApprovalDoc:
    if gate not in APPROVAL_GATES:
        raise ApprovalRequired(f'unknown approval gate: {gate}')
    with _LOCK:
        doc = _read(base, sid)
        requirements = {
            's1': ('s0',), 's2': ('s1',), 's3': ('s1',), 's4': ('s2',),
            's5': ('s3', 's4'), 's6': ('s5',), 's7': ('s6',),
        }
        if missing := [key for key in requirements.get(gate, ()) if not doc.gate[key]]:
            raise ApprovalRequired(f'{gate} requires prior approvals: {missing}')
        if gate == 's2' and not all(doc.seen.values()):
            raise ApprovalRequired('stage2 global and chapter views must both be seen')
        if gate == 's5':
            if not doc.writing:
                raise ApprovalRequired('select deep chapters and start writing first')
            _require_stage5_complete(base, sid, doc)
        _require_artifacts(base, sid, gate)
        gates = dict(doc.gate)
        gates[gate] = True
        return _write(base, doc.model_copy(update={'gate': gates}))


def mark_seen(base: str | os.PathLike[str], sid: str, key: str) -> ApprovalDoc:
    rels = {
        'read': (STAGE2_CHAPTER_BLUEPRINTS,),
        'global': (STAGE2_GLOBAL_GROUNDING,),
    }
    if key not in rels:
        raise ApprovalRequired(f'unknown stage2 view: {key}')
    with _LOCK:
        doc = _read(base, sid)
        novel_dir = paths.resolve_novel_dir(base, sid)
        for rel in rels[key]:
            if not (novel_dir / rel).is_file() or (novel_dir / paths.invalid_of(rel)).is_file():
                raise UpstreamMissing(f'cannot mark unseen artifact as seen: {rel}')
        seen = dict(doc.seen)
        seen[key] = True
        return _write(base, doc.model_copy(update={'seen': seen}))


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
        doc = _read(base, sid)
        novel_dir = paths.resolve_novel_dir(base, sid)
        if doc.writing or _has_stage5_work(novel_dir):
            raise HumanEditProtected('deep selection is locked after stage5 writing starts')
        planned = _planned(base, sid)
        if planned is None:
            raise UpstreamMissing(f'{STAGE4_PLAN_CHAPTERS} is required before depth selection')
        if outside := sorted(set(canonical) - set(planned)):
            raise ApprovalRequired(f'deep selection contains unplanned chapters: {outside}')
        return _write(base, doc.model_copy(update={'deep': canonical}))


def begin_writing(
    base: str | os.PathLike[str], sid: str, expected: list[int] | None = None,
) -> ApprovalDoc:
    with _LOCK:
        doc = _read(base, sid)
        if not doc.gate['s3'] or not doc.gate['s4']:
            raise ApprovalRequired('stage3 style and stage4 plan must be approved first')
        planned = _planned(base, sid)
        if planned is None:
            raise UpstreamMissing(f'{STAGE4_PLAN_CHAPTERS} is required before writing')
        if outside := sorted(set(doc.deep) - set(planned)):
            raise ApprovalRequired(f'saved deep selection is outside the plan: {outside}')
        if expected is not None and list(expected) != doc.deep:
            raise HumanEditProtected(
                f'writing request depth {list(expected)} differs from approved {doc.deep}')
        if doc.writing:
            return doc
        return _write(base, doc.model_copy(update={'writing': True}))


def authorize_arm(
    base: str | os.PathLike[str],
    sid: str,
    mode: str,
    chapters: list[int] | None,
) -> None:
    if mode not in ('deep', 'shallow'):
        raise ApprovalRequired(f'unknown stage5 writing arm: {mode!r}')
    with _LOCK:
        doc = _read(base, sid)
        if not doc.writing:
            raise ApprovalRequired('select deep chapters and start composite writing first')
        planned = _planned(base, sid)
        if planned is None:
            raise UpstreamMissing(f'{STAGE4_PLAN_CHAPTERS} is required before writing')
        requested = set(planned if chapters is None else chapters)
        allowed = set(doc.deep) if mode == 'deep' else set(planned) - set(doc.deep)
        if outside := sorted(requested - allowed):
            raise HumanEditProtected(
                f'{mode} arm request contains chapters assigned to the other arm: {outside}')


def reset(base: str | os.PathLike[str], sid: str) -> ApprovalDoc:
    with _LOCK:
        novel_dir = paths.resolve_novel_dir(base, sid)
        if _has_stage5_work(novel_dir):
            raise HumanEditProtected('cannot reset approvals after stage5 drafts exist')
        return _write(base, _fresh(sid, _planned(base, sid)))
