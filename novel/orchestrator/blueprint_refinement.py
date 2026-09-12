"""Stage4 疑点转交 Stage2 原文核查；沿用范围问题账、接受链和累计预算。

转交不声称疑点已被证实，更不授权 Executor。A 只持盲 alias；这里只在服务内
把引用还原并定位证据。确认真实缺陷、撤销误报、定向修订均由既有章级 Commander
负责。疑点与原请求同一次原子审批写入，提交后崩溃可按原字节证明免费补尾。
"""
from __future__ import annotations

from contracts.artifacts import STAGE4_BLIND_CANDIDATE
from contracts.invariants import PLOT_REVIEW_DIMENSIONS
from contracts.schema.api import (
    BlueprintRefinementCatalog, BlueprintRefinementRequest, Stage2NodeReviewDecision,
    Stage2NodeReviewRequest,
)
from contracts.schema.plot import PlotSubjectRef
from contracts.schema.plot_review import PlotReviewResponse
from contracts.schema.stage4 import BlindCandidate
from novel import paths
from novel.errors import ApprovalRequired, HumanEditProtected
from novel.orchestrator import approvals
from novel.stages.stage2 import plot_review
from novel.stages.stage2.paragraphs import paragraph_spans


def design_catalog(template) -> BlueprintRefinementCatalog:
    """复用已下发的 A truth，不为普通规划另构 Blueprint 工作包。"""
    return BlueprintRefinementCatalog(
        origin='design', review_token=template.review_token,
        subjects_by_chapter={
            row.output_ordinal: ['chapter', *sorted(
                item.alias for rows in (row.subject.blind_truth.beats,
                                        row.subject.blind_truth.states,
                                        row.subject.blind_truth.constraints) for item in rows)]
            for row in template.chapters})


def _current(novel_dir, origin):
    if origin == 'design':
        current = approvals._current_design_subjects(novel_dir)
        candidate = approvals._store_for(novel_dir).read_own(
            BlindCandidate, STAGE4_BLIND_CANDIDATE)[0]
        mappings = {}
        for row in current.chapters:
            truth = row.blind_truth
            mappings[row.chapter.output_ordinal] = {
                item.alias: candidate.subject_aliases[item.alias]
                for rows in (truth.beats, truth.states, truth.constraints) for item in rows}
        token = approvals._design_review_token(current.aggregate_subject_sha256)
    else:
        current = approvals._structure_workpack_snapshot(novel_dir)
        mappings = {
            bp.chapter_ordinal: {
                getattr(row, field): PlotSubjectRef(kind=kind, ref=getattr(row, field))
                for kind, rows, field in (('beat', bp.beats, 'beat_id'),
                                         ('state', bp.states, 'state_id'),
                                         ('constraint', bp.constraints, 'constraint_id'))
                for row in rows}
            for bp in current.blueprints.blueprints}
        token = f's4sr1_{current.subject_sha256}'
    for ordinal, refs in mappings.items():
        refs['chapter'] = PlotSubjectRef(kind='chapter_boundary', ref=f'chapter:{ordinal}')
    return BlueprintRefinementCatalog(
        origin=origin, review_token=token,
        subjects_by_chapter={ordinal: sorted(refs) for ordinal, refs in mappings.items()}), mappings


def catalog(base, sid, *, origin: str) -> BlueprintRefinementCatalog:
    if origin not in ('design', 'structure'):
        raise ApprovalRequired('疑点只能来自当前设计或结构审阅')
    with approvals._LOCK:
        novel_dir = paths.resolve_novel_dir(base, sid)
        approvals.require_stage2_node_accepted(base, sid, 'stage2.blueprint')
        before, _ = _current(novel_dir, origin)
        after, _ = _current(novel_dir, origin)
        if before != after:
            raise HumanEditProtected('读取转交目录期间 Stage4 材料变化')
        return before


def _matched(record, request):
    matched = [row for row in record.stage2_review_history
               if row.refinement_request == request]
    for row in matched:
        receipt = row.plot_record
        expected = approvals._decision_sha256({
            'contract': plot_review.CONTRACT, 'scope': receipt.scope, 'actor': receipt.actor,
            'subject_sha256': receipt.subject_sha256,
            'request_sha256': receipt.request_sha256,
            'response': receipt.response.model_dump(mode='json')})
        if receipt.decision_sha256 != expected:
            raise HumanEditProtected('疑点转交记录的范围决定摘要损坏')
    return bool(matched)


def payload_landed(base, sid, payload) -> bool:
    request = BlueprintRefinementRequest.model_validate(payload, strict=True)
    if request.model_dump(mode='json') != payload:
        raise HumanEditProtected('转交请求不是原始完整载荷')
    with approvals._LOCK:
        target = approvals._path(base, paths.check_sid(sid))
        if target.is_symlink():
            raise HumanEditProtected('转交审计来源不得是符号链接')
        if not target.exists():
            return False
        return _matched(approvals._read_raw(base, sid, require_existing=True), request)


def submit(base, sid, request: BlueprintRefinementRequest):
    with approvals.approval_file_lock(base, sid):
        raw = approvals._read_raw(base, sid)
        novel_dir = paths.resolve_novel_dir(base, sid)
        if _matched(raw, request):
            return approvals._view(base, approvals._effective_record(novel_dir, raw))
        approvals.require_stage2_node_accepted(base, sid, 'stage2.blueprint')
        current, mappings = _current(novel_dir, request.origin)
        if current.review_token != request.review_token:
            raise HumanEditProtected('Stage4 主体已变化，不能用旧 token 转交疑点')
        mapping = mappings.get(request.chapter_ordinal)
        if mapping is None or not set(request.subject_refs) <= set(mapping):
            raise HumanEditProtected('疑点引用不在本章已下发主体目录')
        snapshot = approvals._plot_snapshot(novel_dir, raw)
        scope = f'chapter:{request.chapter_ordinal}'
        pack = snapshot.workpack(scope)
        # 下游只能在 Blueprint 当前放行时转交；有未核查疑点时先由既有章审处理。
        if any(issue.state == 'open' for issue in snapshot.relevant_issues(scope)):
            raise ApprovalRequired('本章已有待核查问题，请先完成章级裁决')
        bp = next(row for row in snapshot.blueprints.blueprints
                  if row.chapter_ordinal == request.chapter_ordinal)
        source = next(row.text for row in snapshot.main.root
                      if row.chapter_ordinal == request.chapter_ordinal)
        parents = {getattr(row, field): row
                   for rows, field in ((bp.beats, 'beat_id'), (bp.states, 'state_id'),
                                       (bp.constraints, 'constraint_id')) for row in rows}
        subjects = [mapping[ref] for ref in request.subject_refs]
        evidence = set()
        for subject in subjects:
            if subject.kind == 'chapter_boundary':
                evidence.add(f'p:1:{len(paragraph_spans(source))}')
            else:
                evidence.update(parents[subject.ref].evidence_refs)
        reason = '下游疑点待原文核查：' + request.reason
        response = PlotReviewResponse.model_validate({
            'decision': 'unver', 'reason': reason,
            'coverage': {dimension: (
                {'state': 'issue', 'issue_refs': ['new:1'], 'reason': reason}
                if dimension == request.dimension else
                {'state': 'unver', 'issue_refs': [],
                 'reason': '本次仅转交疑点，未重新进行原文语义审阅'})
                for dimension in PLOT_REVIEW_DIMENSIONS},
            'new_issues': [{
                'issue_key': 'new:1', 'dimension': request.dimension,
                'problem': reason + (
                    '。提出方仅见规划盲视图；所附证据由编译器定位，尚未核实。'
                    if request.origin == 'design' else
                    '。请对照章级原文确认；本次转交不授权修改剧情。'),
                'subjects': [subject.model_dump(mode='json') for subject in subjects],
                'evidence_refs': [{'chapter_ordinal': request.chapter_ordinal,
                                   'evidence_ref': ref} for ref in sorted(evidence)],
            }], 'dispositions': []})
        node_request = Stage2NodeReviewRequest(
            node='stage2.blueprint', actor=request.actor, reason=reason, decision='uncertain',
            review_token=pack.review_token, scope=scope, plot_review=response)
        receipt = snapshot.apply(pack, response, request.actor)
        snapshot.verify()
        if (_current(novel_dir, request.origin)[0] != current
                or snapshot.store.ctx.code != approvals._projection_code()):
            raise HumanEditProtected('转交期间 Stage4 材料或代码身份变化')
        decision = Stage2NodeReviewDecision(
            **node_request.model_dump(), refinement_request=request, plot_record=receipt,
            refinement_proof_sha256=approvals._decision_sha256({
                'request': request.model_dump(mode='json'), 'decision_sha256': receipt.decision_sha256}),
            review_subject_sha256=pack.subject_sha256,
            review_contract_version=plot_review.CONTRACT, decided_at=approvals._now())
        return approvals._write(base, raw.model_copy(update={
            'stage2_reviews': {**raw.stage2_reviews, 'stage2.blueprint': decision},
            'stage2_review_history': [*raw.stage2_review_history, decision]}))
