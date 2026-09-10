# -*- coding: utf-8 -*-
"""知识关联的当前性、覆盖与真实免费 producer；合成决定不作为小说质量实验。"""
from __future__ import annotations

import copy
import re

import pytest

from contracts.artifacts import (
    STAGE1_MAIN_TEXT, STAGE2_CHAPTER_BLUEPRINTS, STAGE2_GLOBAL_GROUNDING,
    STAGE2_KNOWLEDGE_LINKS,
)
from contracts.schema.api import Stage2NodeReviewRequest
from contracts.schema.knowledge import KnowledgeGrounding
from contracts.schema.knowledge_links import (
    KnowledgeAtFacts, KnowledgeLinkDecision, KnowledgeLinkHistory, KnowledgeLinkSubmission,
)
from contracts.schema.stage1 import MainText
from contracts.schema.stage2 import ChapterBlueprints
from novel.errors import HumanEditProtected, ProvenanceRefused
from novel.orchestrator import approvals, state
from novel.stages.stage2 import knowledge_links as links
from novel.store import ArtifactStore
from tests.test_stage2 import blueprints, main_text
from tests.test_stage2_grounding import character_preparation, pair  # noqa: F401


def _scope(knowledge, bp=None, *, digest='b' * 64):
    candidate = copy.deepcopy(bp or blueprints())
    candidate['_provenance'].update({key: value for key, value in
        knowledge.provenance.model_dump(mode='json').items()
        if key in {'code', 'root_clean_sha1', 'inputs', 'prompts', 'model', 'usage'}})
    return links.build_scope(
        MainText.model_validate(main_text()), knowledge,
        ChapterBlueprints.model_validate(candidate),
        main_sha256='a' * 64, knowledge_sha256=digest, blueprint_sha256='c' * 64)


def _submission(scope, decisions=None):
    # 只用于关闭式载体测试的显式合成决定；真实 C 的证据质量仍单独验收。
    return KnowledgeLinkSubmission(
        scope_sha256=scope.scope_sha256, actor='test:knowledge-commander',
        reason='合成关联边界测试', decisions=decisions if decisions is not None else [
            KnowledgeLinkDecision(obligation_id=row.obligation_id,
                expected_subject_sha256=row.subject_sha256,
                resolution=KnowledgeAtFacts(kind='at_facts', fact_refs=[row.candidate_fact_refs[0]],
                                            trigger_fact_ref=row.candidate_fact_refs[0]))
            for row in scope.obligations])


def _history(scope):
    return [KnowledgeLinkHistory(decision_sha256='d' * 64, accepted=True,
                                 submission=_submission(scope))]


def test_scope_separates_world_knowledge_reader_reveal_and_setup(pair):
    _registry, knowledge = pair
    scope = _scope(knowledge)
    kinds = {row.kind for row in scope.obligations}
    assert {'secret_reveal', 'secret_setup', 'knowledge_event',
            'world_rule', 'world_rule_reveal', 'relation', 'relation_visibility'} <= kinds
    for row in scope.obligations:
        if row.kind in {'secret_reveal', 'secret_setup', 'world_rule_reveal', 'relation_visibility'}:
            assert not row.entry_fact_refs
    assert any(row.entry_fact_refs for row in scope.obligations if row.kind == 'knowledge_event')


def test_unrelated_knowledge_change_inherits_exact_obligations(pair):
    _registry, knowledge = pair
    old = _scope(knowledge)
    changed = knowledge.model_copy(deep=True)
    changed.source_blurb.facts[0].claim += '订正'
    current = _scope(changed, digest='e' * 64)
    assert current.scope_sha256 != old.scope_sha256
    assert current.obligations == old.obligations
    assert links.review(current, _history(old)).pending_obligation_ids == []


@pytest.mark.parametrize('change', ['claim', 'evidence', 'candidate_set'])
def test_current_candidate_bodies_evidence_and_ambiguity_invalidate_links(pair, change):
    _registry, knowledge = pair
    old = _scope(knowledge)
    candidate = copy.deepcopy(blueprints())
    fact = candidate['blueprints'][1]['fact_atoms'][0]
    if change == 'claim':
        fact['claim'] += '候选业务改变'
    elif change == 'evidence':
        fact['source']['text'] += '证据改变'
    else:
        duplicate = copy.deepcopy(fact)
        duplicate['fact_id'] = re.sub(r'\d+$', '999', duplicate['fact_id'])
        candidate['blueprints'][1]['fact_atoms'].append(duplicate)
    current = _scope(knowledge, candidate)
    pending = links.review(current, _history(old)).pending_obligation_ids
    assert pending
    for row in current.obligations:
        if row.chapter_ordinal == 2:
            assert row.obligation_id in pending
    unaffected = [row for row in old.obligations
                  if row.chapter_ordinal == 3 and all(e.chapter_ordinal == 3 for e in row.evidence)]
    assert all(row.obligation_id not in pending for row in unaffected)


def test_latest_unresolved_cannot_resurrect_prior_accept(pair):
    _registry, knowledge = pair
    scope = _scope(knowledge)
    row = scope.obligations[0]
    negative = KnowledgeLinkDecision(
        obligation_id=row.obligation_id, expected_subject_sha256=row.subject_sha256,
        resolution={'kind': 'unresolved', 'reason': '原事实不能支持该时点'})
    history = [*_history(scope), KnowledgeLinkHistory(
        decision_sha256='e' * 64, accepted=False, submission=_submission(scope, [negative]))]
    assert links.review(scope, history).pending_obligation_ids == [row.obligation_id]
    with pytest.raises(HumanEditProtected, match='待审义务'):
        links.require_complete(scope, history)


def test_reveal_cannot_be_silently_promoted_to_chapter_entry(pair):
    _registry, knowledge = pair
    scope = _scope(knowledge)
    row = next(row for row in scope.obligations if row.kind == 'secret_reveal')
    submission = _submission(scope, [KnowledgeLinkDecision(
        obligation_id=row.obligation_id, expected_subject_sha256=row.subject_sha256,
        resolution={'kind': 'at_entry', 'fact_refs': [row.candidate_fact_refs[0]]})])
    with pytest.raises(HumanEditProtected, match='时点'):
        links.validate_submission(scope, submission)


@pytest.fixture
def produced(tmp_path, monkeypatch):
    from tests.test_stage2_knowledge_flow import _prepared, _run
    from tests.test_stage2_generation import (
        _accept_fixture_global, _client, _ctx, _respond_blueprints, _response_blueprints,
    )
    from novel.stages.stage2 import extract

    monkeypatch.setenv('NOVEL_STAGE2_BLUEPRINT_CONTRACT', 'extraction-ir-v3.1')
    ctx, _registry = _prepared(tmp_path)
    _run(ctx)
    _accept_fixture_global(ctx)
    bp_ctx = _ctx(ctx.novel_dir, 'stage2.blueprint')
    client, transport = _client(bp_ctx, lambda req, _index: _respond_blueprints(req, _response_blueprints()))
    extract.run_blueprint(bp_ctx, client)
    return bp_ctx, transport


def _accept_current(ctx):
    from novel.views.stage2 import blueprint_review_workpack
    pack = blueprint_review_workpack(ctx.novel_dir.parent, ctx.sid)
    submission = _submission(pack.knowledge_links.scope)
    request = Stage2NodeReviewRequest(
        node='stage2.blueprint', review_token=pack.review_token, decision='accept',
        actor=submission.actor, reason=submission.reason, knowledge_links=submission)
    return approvals.approve_stage2_review(ctx.novel_dir.parent, ctx.sid, request), pack


def test_real_accept_is_durable_before_independent_free_materialization(produced, monkeypatch):
    from tests.test_stage2_global_agent import _free_ctx
    from novel.views.stage2 import blueprint_review_workpack
    ctx, transport = produced
    before = ArtifactStore(ctx).full_sha256_or_none(STAGE2_CHAPTER_BLUEPRINTS)
    result, original_pack = _accept_current(ctx)
    assert result.stage2_reviews['stage2.blueprint'].state == 'accepted'
    assert not result.gate['s2']
    assert not ArtifactStore(ctx).exists(STAGE2_KNOWLEDGE_LINKS)
    assert blueprint_review_workpack(ctx.novel_dir.parent, ctx.sid).review_token == original_pack.review_token
    # 这里验证关联 producer，既有 S0/S1 全链审批另由 HTTP/E2E 测试覆盖。
    monkeypatch.setattr(approvals, 'close_stage2_accepted', lambda *_args: None)
    artifact = links.run_close_review(_free_ctx(ctx))
    assert artifact.links and artifact.provenance.usage is None
    assert set(artifact.provenance.inputs) == {
        STAGE1_MAIN_TEXT, STAGE2_GLOBAL_GROUNDING, STAGE2_CHAPTER_BLUEPRINTS}
    store = ArtifactStore(ctx)
    assert store.full_sha256_or_none(STAGE2_CHAPTER_BLUEPRINTS) == before
    assert store.freshness(STAGE2_KNOWLEDGE_LINKS).state == 'fresh'
    sha = store.full_sha256_or_none(STAGE2_KNOWLEDGE_LINKS)
    links.run_close_review(_free_ctx(ctx))
    assert store.full_sha256_or_none(STAGE2_KNOWLEDGE_LINKS) == sha
    assert links.require_current(_free_ctx(ctx)).links == artifact.links


def test_pending_links_do_not_schedule_new_blueprint_extraction(produced):
    ctx, _transport = produced
    overview = state.overview_at(ctx.novel_dir, ctx.code)
    assert state.stage2_blueprint_extraction_current(overview)
    verdict, _reason = state.resume_verdict(overview, 'stage2.blueprint')
    assert verdict == state.RESUME_SKIP


def test_formal_link_interruption_resumes_without_rebuying_or_replacing_bytes(produced, monkeypatch):
    from tests.test_stage2_global_agent import _free_ctx
    ctx, _transport = produced
    _accept_current(ctx)
    def interrupted(*_args):
        raise RuntimeError('关联落盘后审批中断')
    monkeypatch.setattr(approvals, 'close_stage2_accepted', interrupted)
    with pytest.raises(RuntimeError, match='审批中断'):
        links.run_close_review(_free_ctx(ctx))
    before = ArtifactStore(ctx).full_sha256_or_none(STAGE2_KNOWLEDGE_LINKS)
    monkeypatch.setattr(approvals, 'close_stage2_accepted', lambda *_args: None)
    links.run_close_review(_free_ctx(ctx))
    assert ArtifactStore(ctx).full_sha256_or_none(STAGE2_KNOWLEDGE_LINKS) == before


def test_missing_typed_association_source_is_unknown_even_with_formal_file(produced, monkeypatch):
    from tests.test_stage2_global_agent import _free_ctx
    ctx, _transport = produced
    _accept_current(ctx)
    monkeypatch.setattr(approvals, 'close_stage2_accepted', lambda *_args: None)
    links.run_close_review(_free_ctx(ctx))
    history, digest = approvals.current_knowledge_link_decisions(ctx.novel_dir.parent, ctx.sid)
    monkeypatch.setattr(approvals, 'current_knowledge_link_decisions', lambda *_args: ([], digest))
    with pytest.raises(HumanEditProtected, match='待审义务'):
        links.require_current(_free_ctx(ctx))


def test_blueprint_http_persists_typed_association_before_free_commit_and_survives_restart(produced):
    from fastapi.testclient import TestClient
    from novel.api.app import UI_HEADER, create_app
    ctx, transport = produced
    base, sid = ctx.novel_dir.parent, ctx.sid
    endpoint = f'/api/novels/{sid}/stage2/blueprint-review-workpack'
    with TestClient(create_app(str(base))) as client:
        client.headers[UI_HEADER] = '1'
        response = client.get(endpoint)
        assert response.status_code == 200, response.text
        from contracts.schema.api import BlueprintReviewWorkpack
        pack = BlueprintReviewWorkpack.model_validate(response.json())
        submission = _submission(pack.knowledge_links.scope)
        request = Stage2NodeReviewRequest(node='stage2.blueprint', review_token=pack.review_token,
            actor=submission.actor, reason=submission.reason, decision='accept', knowledge_links=submission)
        result = client.post(f'/api/novels/{sid}/approvals/stage2-review',
                             json=request.model_dump(mode='json'))
        assert result.status_code == 200, result.text
        assert result.json()['stage2_reviews']['stage2.blueprint']['state'] == 'accepted'
        assert not result.json()['gate']['s2']
        assert not ArtifactStore(ctx).exists(STAGE2_KNOWLEDGE_LINKS)
    with TestClient(create_app(str(base))) as restarted:
        restarted.headers[UI_HEADER] = '1'
        current = restarted.get(endpoint)
        assert current.status_code == 200, current.text
        after = BlueprintReviewWorkpack.model_validate(current.json())
        assert after.review_token == pack.review_token
        assert after.knowledge_links.pending_obligation_ids == []
        assert len(after.knowledge_links.inherited) == len(pack.knowledge_links.scope.obligations)
        history, _digest = approvals.current_knowledge_link_decisions(base, sid)
        assert history[-1].submission == submission


def test_supporting_facts_do_not_implicitly_choose_the_disclosure_trigger():
    with pytest.raises(ValueError, match='trigger_fact_ref'):
        KnowledgeAtFacts.model_validate({'kind': 'at_facts', 'fact_refs': ['f01_001', 'f01_002']})
    with pytest.raises(ValueError, match='支持事实'):
        KnowledgeAtFacts(kind='at_facts', fact_refs=['f01_001'], trigger_fact_ref='f01_002')
