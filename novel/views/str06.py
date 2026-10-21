"""当前三层故事流水线的只读视图；只公开当前契约，交付必须免费复验证明。"""
from __future__ import annotations

import hashlib
import os
import re
from pathlib import Path

from contracts.invariants import NODES
from contracts.story_paths import *
from contracts.schema.api import (
    StoryArtifactView, Str06CandidateList, Str06CandidateSummary,
    Str06DeliveryView, Str06DeliveryChapterView, Str06EpochView,
    Str06LedgerCostView, Str06LedgerNodeCostView, Str06NodeView, Str06RunView,
)
from contracts.schema.story_prose import ChapterBody, HookBody, FinalStory
from contracts.schema.workflow import candidate_node_owner, STR06_RC_VERSION
from novel.paths import derive_source_meta
from novel.errors import ArtifactUnreadable, ProvenanceRefused, UpstreamMissing, UpstreamStale, UnsealedArtifact
from novel.store import Str06Repository
from novel.stages.stage7 import story as assembly_engine
from novel.work_unit_usage import read_cost_snapshot

WORKFLOW_REL = 'runtime/candidate_workflow.json'
STATUS_REL = 'runtime/workflow_status.json'
_NAME_RE = re.compile(r'^[^\x00-\x1f/\\:*?"<>|]+$')
_READ_ERRORS = (OSError, ValueError, ArtifactUnreadable, ProvenanceRefused, UpstreamMissing, UpstreamStale, UnsealedArtifact)


def _run_dir(base, candidate):
    if (not _NAME_RE.fullmatch(candidate) or candidate in ('.', '..')
            or candidate.rstrip(' .') != candidate):
        return None
    root = (Path(base).resolve() / candidate).resolve()
    if not root.is_relative_to(Path(base).resolve()):
        return None
    if (root / WORKFLOW_REL).is_file():
        return root
    return None


def _revision(run):
    # 每次只读查询重新取元数据，覆盖中断补提交与同一工作流重设计。
    rows = []
    for family in ('runtime', 'stage2', 'stage4', 'stage5', 'stage7'):
        directory = run / family
        if directory.is_dir():
            for path in sorted(directory.rglob('*')):
                if path.is_file():
                    stat = path.stat()
                    rows.append((path.relative_to(run).as_posix(), stat.st_size, stat.st_mtime_ns))
    return hashlib.sha256(repr(rows).encode('utf8')).hexdigest()


def _inspect(run, rel):
    try:
        return Str06Repository(run).inspect(rel)
    except FileNotFoundError:
        return None


def _current(run):
    workflow = _inspect(run, WORKFLOW_REL)
    epoch = workflow.get('epoch', {}) if isinstance(workflow, dict) else {}
    return epoch.get('contract_version') == STR06_RC_VERSION


def list_candidates(base):
    root = Path(base)
    if not root.is_dir():
        return Str06CandidateList(candidates=())
    rows = []
    for entry in sorted(root.iterdir()):
        run = _run_dir(root, entry.name) if entry.is_dir() else None
        if run is None:
            continue
        state = _inspect(run, STATUS_REL) or {}
        rows.append(Str06CandidateSummary(candidate=entry.name, present=True,
            status=state.get('status') if _current(run) else 'contract_incompatible',
            updated_at=int((run / WORKFLOW_REL).stat().st_mtime)))
    return Str06CandidateList(candidates=tuple(rows))


def _artifact(base, candidate, build):
    run = _run_dir(base, candidate)
    if run is None:
        return StoryArtifactView(candidate_id=candidate, revision='', status='missing', data=None)
    revision = _revision(run)
    if not _current(run):
        return StoryArtifactView(candidate_id=candidate, revision=revision, status='invalid', data=None,
            errors=('旧根只读归档；当前契约须在新根重新生成',))
    try:
        data = build(Str06Repository(run))
    except FileNotFoundError:
        return StoryArtifactView(candidate_id=candidate, revision=revision, status='missing', data=None)
    except _READ_ERRORS as exc:
        return StoryArtifactView(candidate_id=candidate, revision=revision, status='invalid', data=None, errors=(str(exc),))
    return StoryArtifactView(candidate_id=candidate, revision=revision, status='ready', data=data)


def source_story_view(base, candidate):
    return _artifact(base, candidate, lambda repo: repo.check_model(SOURCE_STORY).model_dump(mode='json'))


def target_story_view(base, candidate):
    return _artifact(base, candidate, lambda repo: repo.check_model(TARGET_STORY).model_dump(mode='json'))


def _optional_model(repo, rel):
    try:
        return repo.inspect_sealed_model(rel).model_dump(mode='json')
    except FileNotFoundError:
        return None


def _optional_text(repo, rel):
    try:
        return repo.read_text(rel)
    except FileNotFoundError:
        return None


def chapter_view(base, candidate, ordinal):
    def build(repo):
        packet = repo.check_model(chapter_rel(CHAPTER_PACKET, ordinal))
        body = _optional_text(repo, chapter_rel(BODY, ordinal))
        receipt = _optional_model(repo, chapter_rel(BODY_RECEIPT, ordinal))
        return {'chapter_ordinal': ordinal, 'packet': packet.model_dump(mode='json'), 'body': body, 'receipt': receipt}
    return _artifact(base, candidate, build)


def hook_view(base, candidate):
    return _artifact(base, candidate, lambda repo: {
        'packet': repo.check_model(HOOK_PACKET).model_dump(mode='json'),
        'body': _optional_text(repo, HOOK), 'receipt': _optional_model(repo, HOOK_RECEIPT)})


def _verified_delivery(repo):
    manifest = repo.check_model(MANIFEST)
    count = manifest.source_chapter_count
    packets = tuple(repo.check_model(chapter_rel(CHAPTER_PACKET, i)) for i in range(1, count + 1))
    bodies = tuple(ChapterBody(contract_version=STR06_RC_VERSION, chapter_ordinal=i,
        text=repo.read_text(chapter_rel(BODY, i)), receipt=repo.inspect_sealed_model(chapter_rel(BODY_RECEIPT, i)))
        for i in range(1, count + 1))
    hook = HookBody(contract_version=STR06_RC_VERSION, text=repo.read_text(HOOK), receipt=repo.check_model(HOOK_RECEIPT))
    path = repo.path(FINAL.format(title=manifest.title))
    final = FinalStory(contract_version=STR06_RC_VERSION, text=repo.read_text(FINAL.format(title=manifest.title)), manifest=manifest)
    issues = assembly_engine.verify_final_story(final, title=manifest.title, source_chapter_count=count,
        packets=packets, bodies=bodies, hook=hook, hook_packet=repo.check_model(HOOK_PACKET))
    if issues:
        raise ArtifactUnreadable('；'.join(issue.detail for issue in issues))
    return final, path


def delivery_view(base, candidate):
    run = _run_dir(base, candidate)
    if run is None:
        return Str06DeliveryView(present=False)
    if not _current(run):
        return Str06DeliveryView(present=False, disclosure='旧根只读归档；请在新根重新生成')
    try:
        final, _path = _verified_delivery(Str06Repository(run))
    except FileNotFoundError:
        return Str06DeliveryView(present=False)
    except _READ_ERRORS as exc:
        return Str06DeliveryView(present=False, disclosure=str(exc))
    return Str06DeliveryView(present=True, delivery_identity=final.manifest.final_text_sha256,
        final_text_sha256=final.manifest.final_text_sha256,
        chapters=tuple(Str06DeliveryChapterView(chapter_ordinal=row.chapter_ordinal, prose_sha256=row.body_sha256)
            for row in final.manifest.chapters), disclosure='机械证明通过；小说质量未评（unver）')


def final_text_delivery(base, candidate):
    run = _run_dir(base, candidate)
    if run is None or not _current(run):
        return None
    try:
        final, path = _verified_delivery(Str06Repository(run))
    except FileNotFoundError:
        return None
    return path.name, final.text


def _ledger_cost(candidate_root: Path, sid: str) -> Str06LedgerCostView:
    """ledger 成本账的只读投影：实付与缓存分开，usage 缺失单列不并零。"""
    if not (candidate_root / '_service/ledger.jsonl').is_file():
        return Str06LedgerCostView(present=False)
    try:
        rows, partial = read_cost_snapshot(candidate_root)
    except (OSError, ValueError) as exc:
        raise ArtifactUnreadable(f'候选根成本账不可读：{exc}') from exc

    def known(keys: tuple[str, ...], subset: list[dict]) -> int | None:
        values = [row.get(k) for row in subset for k in keys]
        if any(v is None for v in values):
            return None
        return sum(v for v in values if isinstance(v, int))

    rows = [row for row in rows if row.get('sid') == sid]
    paid = [row for row in rows if not row.get('cached')]
    cached = [row for row in rows if row.get('cached')]
    unknown = sum(1 for row in paid
                  if row.get('prompt_tokens') is None
                  or row.get('completion_tokens') is None)
    per_node: dict[str, list[dict]] = {}
    for row in rows:
        per_node.setdefault(str(row.get('node') or 'unknown'), []).append(row)
    return Str06LedgerCostView(
        present=True, rows=len(rows), new_paid_rows=len(paid), cached_rows=len(cached),
        prompt_tokens=known(('prompt_tokens',), paid),
        completion_tokens=known(('completion_tokens',), paid),
        reasoning_tokens=known(('reasoning_tokens',), paid),
        unknown_cost_rows=unknown + (1 if partial else 0),
        per_node=tuple(
            Str06LedgerNodeCostView(
                node=node, rows=len(node_rows),
                new_paid_rows=sum(1 for r in node_rows if not r.get('cached')),
                cached_rows=sum(1 for r in node_rows if r.get('cached')),
                prompt_tokens=known(('prompt_tokens',),
                                    [r for r in node_rows if not r.get('cached')]),
                completion_tokens=known(('completion_tokens',),
                                        [r for r in node_rows if not r.get('cached')]),
                reasoning_tokens=known(('reasoning_tokens',),
                                       [r for r in node_rows if not r.get('cached')]))
            for node, node_rows in sorted(per_node.items())))



def run_view(base, candidate):
    run = _run_dir(base, candidate)
    if run is None:
        return Str06RunView(candidate=candidate, present=False)
    status = _inspect(run, STATUS_REL) or {}
    workflow = _inspect(run, WORKFLOW_REL) or {}
    epoch = workflow.get('epoch') or {}
    nodes = []
    for node, spec in NODES.items():
        if not node.startswith('str06.'):
            continue
        node_status = _inspect(run, 'runtime/node_status/' + node + '.json')
        nodes.append(Str06NodeView(node=node, stage=spec.stage, owner=candidate_node_owner(node),
            paid=spec.paid, produced=any(path.is_file() for rel in spec.produces
                for path in run.glob(rel.replace('{nn}', '*').replace('{title}', '*')))))
    return Str06RunView(candidate=candidate, present=True, revision=_revision(run),
        status=status.get('status') if _current(run) else 'contract_incompatible',
        failed_node=status.get('failed_node'), accepted_body=status.get('accepted_body'),
        failure_kind=status.get('failure_kind'), detail=status.get('detail'),
        epoch=Str06EpochView(epoch_id=epoch['epoch_id'], ordinal=epoch['ordinal'], reason=epoch['reason'],
            session_run_id=epoch.get('session_run_id')) if epoch else None,
        nodes=tuple(nodes), cost=_ledger_cost(Path(base), derive_source_meta(run.name)[0]),
        **{key: status.get(key) for key in ('error_code', 'error_stage', 'error_node',
            'error_item', 'error_phase', 'error_context')})
