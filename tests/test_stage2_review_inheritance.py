"""Stage2 义务继承穿过 typed 决定、真实 producer/API；合成材料不代表质量标定。"""
from __future__ import annotations

import pytest

from contracts.schema.api import Stage2NodeReviewDecision
from contracts.schema.stage2_review import Stage2ReviewObligation, Stage2ReviewSubmission
from novel.errors import ArtifactUnreadable, HumanEditProtected
from novel.stages.stage2 import review_obligations as review
from tests.test_stage2_knowledge_links import produced  # noqa: F401


def _obligation(key, content, *, refs=()):
    return Stage2ReviewObligation(
        obligation_id=key, subject_sha256=review.sha(content), owner='stage2.global',
        chapter_ordinals=[1], subject_path='canon.world_rules.' + key,
        kind='global_item', context_refs=list(refs))


def _decide(coverage, history=(), *, decision='accept', exceptions=()):
    submission = Stage2ReviewSubmission(
        scope_sha256=coverage.scope.scope_sha256, default_decision='accept',
        exceptions=list(exceptions))
    receipt = review.materialize(coverage, history, decision=decision, actor='test:原审阅者',
                                 reason='合成原决定', submission=submission)
    return Stage2NodeReviewDecision(
        node='stage2.global', review_token='test-token', decision=decision,
        actor='test:原审阅者', reason='合成原决定',
        owner='stage2.global' if decision == 'rework' else None,
        review=submission, obligation_receipt=receipt,
        review_subject_sha256=review.sha(coverage.scope.model_dump(mode='json')),
        review_contract_version='stage2-global-acceptance-v1', decided_at=str(len(history)))


def test_partial_acceptance_preserves_unrelated_original_decisions():
    rows = [_obligation('one', '甲'), _obligation('two', '乙')]
    first = _decide(review.coverage('stage2.global', rows, []), decision='rework', exceptions=[
        {'obligation_id': 'two', 'decision': 'rework', 'reason': '乙的原证据不足'}])
    changed = [rows[0], _obligation('two', '乙已补足证据')]
    current = review.coverage('stage2.global', changed, [first])
    assert current.pending_obligation_ids == ['two']
    assert current.inherited[0].actor == first.actor
    assert current.inherited[0].source_decision_sha256 == review.sha(first.model_dump(mode='json'))
    second = _decide(current, [first])
    final = review.coverage('stage2.global', changed, [first, second])
    assert not final.pending_obligation_ids
    assert {row.obligation.obligation_id for row in final.inherited} == {'one', 'two'}


def test_removed_problem_stays_pending_and_is_stable_after_explicit_review():
    rows = [_obligation('collection', ['bad']), _obligation('bad', '错误事实', refs=['collection'])]
    first = _decide(review.coverage('stage2.global', rows, []), decision='rework', exceptions=[
        {'obligation_id': 'bad', 'decision': 'rework', 'reason': '源文没有这条事实'}])
    current_rows = [_obligation('collection', [])]
    current = review.coverage('stage2.global', current_rows, [first])
    removed = next(row for row in current.scope.obligations if row.obligation_id == 'bad')
    assert removed.kind == 'removed_problem'
    assert 'bad' in current.pending_obligation_ids
    assert current.prior_problems[0].reason == '源文没有这条事实'
    second = _decide(current, [first])
    inherited = review.coverage('stage2.global', current_rows, [first, second])
    assert inherited.scope == current.scope and not inherited.pending_obligation_ids


def test_latest_negative_blocks_old_positive_even_after_bytes_return():
    old = [_obligation('one', '原内容')]
    first = _decide(review.coverage('stage2.global', old, []))
    changed = [_obligation('one', '错误修订')]
    second = _decide(review.coverage('stage2.global', changed, [first]), [first],
                     decision='rework', exceptions=[
                         {'obligation_id': 'one', 'decision': 'rework', 'reason': '不符合源文'}])
    assert review.coverage('stage2.global', old, [first, second]).pending_obligation_ids == ['one']


@pytest.mark.parametrize('damage', ['original_reason', 'scope_hash', 'source_reference'])
def test_damaged_receipt_is_unknown_not_a_license_to_fall_back(damage):
    rows = [_obligation('one', '原内容')]
    first = _decide(review.coverage('stage2.global', rows, []))
    second = _decide(review.coverage('stage2.global', rows, [first]), [first])
    if damage == 'original_reason':
        first = first.model_copy(update={'reason': '破坏被引用的原审阅回执'})
    elif damage == 'scope_hash':
        first = first.model_copy(update={'obligation_receipt': first.obligation_receipt.model_copy(update={
            'scope': first.obligation_receipt.scope.model_copy(update={'scope_sha256': 'f' * 64})})})
    else:
        second = second.model_copy(update={'obligation_receipt': second.obligation_receipt.model_copy(update={
            'units': [second.obligation_receipt.units[0].model_copy(update={'source_decision_sha256': 'f' * 64})]})})
    with pytest.raises(ArtifactUnreadable):
        review.coverage('stage2.global', rows, [first, second])


def test_incomplete_or_unknown_submission_cannot_become_accept():
    coverage = review.coverage('stage2.global', [_obligation('one', '事实')], [])
    for submission in (
        Stage2ReviewSubmission(scope_sha256='f' * 64, default_decision='accept', exceptions=[]),
        Stage2ReviewSubmission(scope_sha256=coverage.scope.scope_sha256,
                               default_decision='uncertain', exceptions=[]),
        Stage2ReviewSubmission(scope_sha256=coverage.scope.scope_sha256,
            default_decision='accept', exceptions=[
                {'obligation_id': 'missing', 'decision': 'uncertain', 'reason': '不存在的项'}]),
    ):
        with pytest.raises(HumanEditProtected):
            review.materialize(coverage, [], decision='accept', actor='test', reason='明确审阅', submission=submission)


def test_current_head_cannot_be_detached_from_immutable_history():
    from novel.orchestrator import approvals
    decision = _decide(review.coverage('stage2.global', [_obligation('one', '事实')], []))
    raw = approvals._fresh_record('1234567890123456789')
    with pytest.raises(ValueError, match='不可变历史'):
        type(raw).model_validate({**raw.model_dump(), 'stage2_reviews': {'stage2.global': decision}})


def test_source_damage_is_explicit_unknown_and_free_closure_is_unavailable(produced, monkeypatch):
    from novel.orchestrator import approvals
    from tests.test_stage2_knowledge_links import _accept_current

    ctx, _transport = produced
    base, sid = ctx.novel_dir.parent, ctx.sid
    _accept_current(ctx)
    original = approvals.stage2_review_coverage
    def broken(*args, **kwargs):
        if args[2] == 'stage2.global':
            raise ArtifactUnreadable('原义务回执的 scope 摘要损坏')
        return original(*args, **kwargs)
    monkeypatch.setattr(approvals, 'stage2_review_coverage', broken)
    result = approvals._stage2_review_view(ctx.novel_dir, approvals._read_raw(base, sid), 'stage2.global')
    assert result.state == 'unknown' and '损坏' in result.unavailable_reason
    assert result.current_decision is None
    assert not approvals.stage2_review_closure_available(base, sid, node='stage2.global')


def test_blueprint_http_acceptance_covers_real_catalog_and_projects_no_repeated_material(produced):
    from fastapi.testclient import TestClient
    from contracts.schema.api import BlueprintReviewWorkpack, Stage2NodeReviewRequest
    from novel.api.app import UI_HEADER, create_app
    from novel.orchestrator import approvals, reviewer_tasks, queue
    from tests.test_stage2_knowledge_links import _submission

    ctx, transport = produced
    base, sid = ctx.novel_dir.parent, ctx.sid
    endpoint = f'/api/novels/{sid}/stage2/blueprint-review-workpack'
    with TestClient(create_app(str(base))) as client:
        client.headers[UI_HEADER] = '1'
        response = client.get(endpoint)
        assert response.status_code == 200, response.text
        first = BlueprintReviewWorkpack.model_validate(response.json())
        assert first.review_coverage.pending_obligation_ids
        first_identity = reviewer_tasks._stage2_progress_identity(
            {'blueprint': first.model_dump(mode='json')}, queue.WAITING_ON_STAGE2_BLUEPRINT_REVIEW)
        assert first_identity['blocker_count'] == len(first.review_coverage.pending_obligation_ids)
        assert first_identity['source_sha256'] == first.subject_sha256
        assert first_identity['workpack_sha256'] == first.binding_sha256
        assert first_identity['main_text_sha256'] == first.binding.main_text_artifact_sha256
        links = _submission(first.knowledge_links.scope)
        body = Stage2NodeReviewRequest(
            node='stage2.blueprint', review_token=first.review_token, decision='accept',
            actor=links.actor, reason=links.reason, knowledge_links=links,
            review=Stage2ReviewSubmission(scope_sha256=first.review_coverage.scope.scope_sha256,
                                          default_decision='accept', exceptions=[]))
        accepted = client.post(f'/api/novels/{sid}/approvals/stage2-review', json=body.model_dump(mode='json'))
        assert accepted.status_code == 200, accepted.text
        record = accepted.json()['stage2_reviews']['stage2.blueprint']['current_decision']
        assert record['obligation_receipt']['units']
    with TestClient(create_app(str(base))) as client:
        response = client.get(endpoint)
        assert response.status_code == 200, response.text
        current = BlueprintReviewWorkpack.model_validate(response.json())
        assert current.review_token == first.review_token
        assert not current.review_coverage.pending_obligation_ids
        current_identity = reviewer_tasks._stage2_progress_identity(
            {'blueprint': current.model_dump(mode='json'), 'global': {'items': '不能消费的重复材料'}},
            queue.WAITING_ON_STAGE2_BLUEPRINT_REVIEW)
        assert current_identity['blocker_count'] == 0
        assert current_identity['progress_sha256'] != first_identity['progress_sha256']
        visible = reviewer_tasks._review_materials(
            {'blueprint': current.model_dump(mode='json')}, queue.WAITING_ON_STAGE2_BLUEPRINT_REVIEW)['blueprint']
        assert visible['chapters'] == []
        assert visible['global_review']['items'] == []
        assert visible['knowledge_links']['pending'] == []
        assert visible['review_coverage']['inherited_count'] == len(first.review_coverage.scope.obligations)
        before = approvals.stage2_review_history(base, sid)
        approvals.close_stage2_inherited(base, sid, node='stage2.blueprint')
        assert approvals.stage2_review_history(base, sid) == before


def test_typed_fact_edit_only_reopens_its_chapter_and_keeps_original_reviewers(produced):
    from contracts.schema.api import Stage2NodeReviewRequest
    from contracts.schema.stage2 import BlueprintRelationEdit
    from novel.orchestrator import approvals
    from novel.stages.stage2 import relation_edit
    from novel.store import ArtifactStore
    from novel.views.stage2 import blueprint_review_workpack
    from tests.test_stage2_global_agent import _free_ctx
    from tests.test_stage2_knowledge_links import _accept_current, _submission

    ctx, transport = produced
    base, sid = ctx.novel_dir.parent, ctx.sid
    _accepted, first = _accept_current(ctx)
    calls = len(transport.calls)
    free = _free_ctx(ctx)
    raw = relation_edit.edit_template(free)
    raw.pop('_schema_version')
    raw.update(actor='test:editor', reason='只订正第一章事实措辞')
    raw['chapters'] = raw['chapters'][:1]
    fact = next(row for row in first.chapters if row.chapter_ordinal == 1).beats[0].facts[0]
    raw['chapters'][0]['fact_patches'] = [{'fact_id': fact.fact_id, 'claim': fact.claim + '。'}]
    ArtifactStore(free).write_sparse_input(relation_edit.REQUEST_REL, BlueprintRelationEdit.model_validate(raw))
    _changed, report = relation_edit.apply_edit(free)
    assert report.passed
    current = blueprint_review_workpack(base, sid)
    coverage = current.review_coverage
    pending = set(coverage.pending_obligation_ids)
    assert {ordinal for row in coverage.scope.obligations if row.obligation_id in pending
            for ordinal in row.chapter_ordinals} == {1}
    assert {ordinal for row in coverage.inherited for ordinal in row.obligation.chapter_ordinals} >= {2, 3}
    links = _submission(current.knowledge_links.scope)
    request = Stage2NodeReviewRequest(
        node='stage2.blueprint', review_token=current.review_token, decision='accept',
        actor=links.actor, reason=links.reason, knowledge_links=links,
        review=Stage2ReviewSubmission(scope_sha256=coverage.scope.scope_sha256,
                                      default_decision='accept', exceptions=[]))
    result = approvals.approve_stage2_review(base, sid, request)
    receipt = result.stage2_reviews['stage2.blueprint'].current_decision.obligation_receipt
    assert all(row.source_decision_sha256 for row in receipt.units if 1 not in row.obligation.chapter_ordinals)
    assert len(transport.calls) == calls
    with pytest.raises(HumanEditProtected):
        approvals.approve_stage2_review(base, sid, request.model_copy(update={'review_token': first.review_token}))


def test_audit_only_change_uses_real_cli_free_closure_and_reuses_knowledge_decisions(produced, monkeypatch):
    from contracts.artifacts import STAGE2_GLOBAL_REVIEW, STAGE2_KNOWLEDGE_LINKS
    from contracts.schema.evaluation import GlobalReview
    from novel.cli.main import _run_stage2
    from novel.orchestrator import approvals
    from novel.store import ArtifactStore
    from novel.views.stage2 import blueprint_review_workpack
    from tests.test_stage2_global_agent import _free_ctx
    from tests.test_stage2_knowledge_links import _accept_current

    ctx, _transport = produced
    base, sid = ctx.novel_dir.parent, ctx.sid
    _accept_current(ctx)
    before = ctx.ledger.path.read_bytes()
    free = _free_ctx(ctx)
    free.node = 'stage2.global'
    store = ArtifactStore(free)
    report = store.read_own(GlobalReview, STAGE2_GLOBAL_REVIEW)[0]
    report.items[0] = report.items[0].model_copy(update={'reason': '补充审计说明，事实、证据和决定不变'})
    store.write_round(STAGE2_GLOBAL_REVIEW, report)
    assert approvals.stage2_review_closure_available(base, sid, node='stage2.global')
    assert _run_stage2(free, ['--close-review']) == 0
    approvals.require_stage2_node_accepted(base, sid, 'stage2.global')
    assert approvals.stage2_review_closure_available(base, sid, node='stage2.blueprint')
    # 本例到关联 producer 为止；S0/S1 完整人工审批使用独立链路验收。
    monkeypatch.setattr(approvals, 'close_stage2_accepted', lambda *_args: None)
    free = _free_ctx(ctx)
    assert _run_stage2(free, ['--close-review']) == 0
    assert ArtifactStore(free).exists(STAGE2_KNOWLEDGE_LINKS)
    assert not blueprint_review_workpack(base, sid).review_coverage.pending_obligation_ids
    assert ctx.ledger.path.read_bytes() == before
    history = approvals.stage2_review_history(base, sid)
    assert history[-1].actor == 'system:review-inheritance'
    assert all(row.source_decision_sha256 and row.actor != history[-1].actor
               for row in history[-1].obligation_receipt.units)
