"""Stage2 义务继承穿过 typed 决定、真实 producer/API；合成材料不代表质量标定。"""
from __future__ import annotations

import pytest

from contracts.schema.api import Stage2NodeReviewDecision
from contracts.schema.stage2_review import Stage2ReviewObligation, Stage2ReviewSubmission
from novel.errors import ArtifactUnreadable, HumanEditProtected
from novel.stages.stage2 import review_obligations as review
from tests.test_plot_producer import plot_contract, prepare_book, generate_plot


@pytest.fixture
def produced(tmp_path):
    return generate_plot(prepare_book(tmp_path))


def _accept_current(ctx):
    from tests.stage2_fixtures import accept_and_materialize_links
    from novel.orchestrator import approvals
    from novel.views.stage2 import blueprint_review_workpack
    accept_and_materialize_links(ctx)
    return approvals.get(ctx.novel_dir.parent, ctx.sid, include_spawns=False), blueprint_review_workpack(ctx.novel_dir.parent, ctx.sid, scope='cross_chapter')


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


@pytest.mark.parametrize('context_refs', [[], ['two', 'new']])
def test_navigation_changes_keep_original_acceptance_and_exact_current_scope(context_refs):
    rows = [_obligation('one', '甲', refs=['two']), _obligation('two', '乙')]
    first = _decide(review.coverage('stage2.global', rows, []))
    original = first.model_dump(mode='json')
    current_rows = [rows[0].model_copy(update={'context_refs': context_refs}), rows[1]]
    if 'new' in context_refs:
        current_rows.append(_obligation('new', '新增的阅读上下文'))
    current = review.coverage('stage2.global', current_rows, [first])
    inherited = next(row for row in current.inherited if row.obligation.obligation_id == 'one')
    assert inherited.obligation == current_rows[0]
    assert inherited.source_decision_sha256 == review.sha(original)
    assert inherited.actor == first.actor and inherited.reason == first.reason
    assert current.pending_obligation_ids == (['new'] if 'new' in context_refs else [])
    assert len(current.inherited) + len(current.pending_obligation_ids) == len(current_rows)
    second = _decide(current, [first])
    final = review.coverage('stage2.global', current_rows, [first, second])
    assert not final.pending_obligation_ids and final.scope == current.scope
    assert first.model_dump(mode='json') == original


def test_navigation_rebinding_does_not_restore_an_acceptance_after_a_newer_negative():
    rows = [_obligation('one', '甲', refs=['two']), _obligation('two', '乙')]
    first = _decide(review.coverage('stage2.global', rows, []))
    second = _decide(review.coverage('stage2.global', rows, [first]), [first],
        decision='rework', exceptions=[dict(obligation_id='one', decision='rework', reason='较新反证')])
    changed = [rows[0].model_copy(update={'context_refs': []}), rows[1]]
    current = review.coverage('stage2.global', changed, [first, second])
    assert current.pending_obligation_ids == ['one']
    assert current.prior_problems[0].reason == '较新反证'
    assert {row.obligation.obligation_id for row in current.inherited} == {'two'}


@pytest.mark.parametrize('changed', [
    {'owner': 'stage2.characters'}, {'chapter_ordinals': [2]}, {'subject_path': 'global.relations.other'},
])
def test_navigation_rebinding_never_ignores_changed_business_identity(changed):
    row = _obligation('one', '原主张', refs=['context'])
    first = _decide(review.coverage('stage2.global', [row], []))
    current = row.model_copy(update={**changed, 'context_refs': []})
    with pytest.raises(ArtifactUnreadable, match='业务身份不一致'):
        review.coverage('stage2.global', [current], [first])


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


@pytest.mark.parametrize('damage', ['original_reason', 'scope_hash', 'source_reference', 'context_refs'])
def test_damaged_receipt_is_unknown_not_a_license_to_fall_back(damage):
    rows = [_obligation('one', '原内容')]
    first = _decide(review.coverage('stage2.global', rows, []))
    second = _decide(review.coverage('stage2.global', rows, [first]), [first])
    if damage == 'original_reason':
        first = first.model_copy(update={'reason': '破坏被引用的原审阅回执'})
    elif damage == 'scope_hash':
        first = first.model_copy(update={'obligation_receipt': first.obligation_receipt.model_copy(update={
            'scope': first.obligation_receipt.scope.model_copy(update={'scope_sha256': 'f' * 64})})})
    elif damage == 'context_refs':
        # 导航不参与语义继承比较，也不能回写到历史 scope 冒充原始阅读材料。
        receipt = first.obligation_receipt
        altered = receipt.scope.obligations[0].model_copy(update={'context_refs': ['forged']})
        first = first.model_copy(update={'obligation_receipt': receipt.model_copy(update={
            'scope': receipt.scope.model_copy(update={'obligations': [altered]}),
            'units': [receipt.units[0].model_copy(update={'obligation': altered})]})})
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


def test_blueprint_http_acceptance_covers_real_scope_and_survives_restart(produced):
    from fastapi.testclient import TestClient
    from contracts.schema.api import BlueprintReviewWorkpack
    from novel.api.app import UI_HEADER, create_app
    from novel.orchestrator import approvals
    from tests.test_plot_review_runtime import request
    ctx, transport = produced
    base, sid = ctx.novel_dir.parent, ctx.sid
    endpoint = f'/api/novels/{sid}/stage2/blueprint-review-workpack?scope=chapter:1'
    with TestClient(create_app(str(base))) as client:
        client.headers[UI_HEADER] = '1'
        result = client.get(endpoint)
        assert result.status_code == 200, result.text
        first = BlueprintReviewWorkpack.model_validate(result.json())
        assert first.scope == 'chapter:1' and len(first.material['blueprints']) == 1
        accepted = client.post(f'/api/novels/{sid}/approvals/stage2-review', json=request(first).model_dump(mode='json'))
        assert accepted.status_code == 200, accepted.text
    with TestClient(create_app(str(base))) as client:
        result = client.get(endpoint)
        assert result.status_code == 200, result.text
        current = BlueprintReviewWorkpack.model_validate(result.json())
        assert current.subject_sha256 == first.subject_sha256
        assert {row.scope: row.state for row in current.progress.scopes}['chapter:1'] == 'accepted'
        assert len(current.material['sources']) == 1
        assert current.material['chapter_acceptances'] == []
    record = approvals.stage2_review_history(base, sid)[-1]
    assert record.plot_record.scope == 'chapter:1' and record.obligation_receipt is None
    assert len(transport.calls) == 3


def test_typed_plot_edit_only_reopens_its_chapter_and_keeps_original_reviewers(produced):
    from novel.orchestrator import approvals
    from novel.stages.stage2 import plot_edit
    from novel.views.stage2 import blueprint_review_workpack
    from tests.test_stage2_generation import _ctx
    from tests.test_plot_edit import request_for, save_request
    from tests.test_plot_review_runtime import request
    ctx, transport = produced
    base, sid = ctx.novel_dir.parent, ctx.sid
    _accept_current(ctx)
    prior = {r.scope: r for r in approvals.stage2_review_history(base, sid) if r.node == 'stage2.blueprint'}
    first = blueprint_review_workpack(base, sid, scope='chapter:1')
    free = _ctx(ctx.novel_dir, 'stage2.blueprint', paid=False)
    raw = request_for(free)
    raw['chapters'][0]['chapter']['beats'][0]['content'] += '（据昨夜来信）'
    assert plot_edit.apply_edit(free, save_request(free, raw))[1].passed
    current = blueprint_review_workpack(base, sid, scope='chapter:1')
    states = {row.scope: row.state for row in current.progress.scopes}
    assert states == {'chapter:1': 'expired', 'chapter:2': 'accepted', 'chapter:3': 'accepted', 'cross_chapter': 'expired'}
    approvals.approve_stage2_review(base, sid, request(current))
    after = {r.scope: r for r in approvals.stage2_review_history(base, sid) if r.node == 'stage2.blueprint'}
    assert after['chapter:2'] == prior['chapter:2'] and after['chapter:3'] == prior['chapter:3']
    assert len(transport.calls) == 3
    with pytest.raises(HumanEditProtected):
        approvals.approve_stage2_review(base, sid, request(first))


def test_audit_only_change_uses_real_cli_free_closure_and_reuses_knowledge_decisions(produced, monkeypatch):
    from contracts.artifacts import STAGE2_GLOBAL_REVIEW, STAGE2_KNOWLEDGE_LINKS
    from contracts.schema.evaluation import GlobalReview
    from novel.cli.main import _run_stage2
    from novel.orchestrator import approvals
    from novel.store import ArtifactStore
    from novel.views.stage2 import blueprint_review_workpack
    from tests.test_stage2_global_agent import _free_ctx

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
    assert blueprint_review_workpack(base, sid, scope='cross_chapter').progress.accepted
    assert ctx.ledger.path.read_bytes() == before
    history = approvals.stage2_review_history(base, sid)
    assert history[-1].actor == 'system:review-inheritance'
    assert all(row.source_decision_sha256 and row.actor != history[-1].actor
               for row in history[-1].obligation_receipt.units)
