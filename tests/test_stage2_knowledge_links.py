# -*- coding: utf-8 -*-
"""知识关联的当前性、覆盖与真实免费 producer；合成决定不作为小说质量实验。"""
from __future__ import annotations

import copy

import pytest

from contracts.artifacts import (
    STAGE1_MAIN_TEXT, STAGE2_CHAPTER_BLUEPRINTS, STAGE2_GLOBAL_GROUNDING,
    STAGE2_KNOWLEDGE_LINKS,
)
from contracts.schema.api import Stage2NodeReviewRequest
from contracts.schema.knowledge import KnowledgeGrounding
from contracts.schema.knowledge_links import (
    KnowledgeAtBeats, KnowledgeLinkDecision, KnowledgeLinkHistory, KnowledgeLinkSubmission,
)
from contracts.schema.stage1 import MainText
from contracts.schema.stage2 import ChapterBlueprints
from novel.errors import HumanEditProtected, ProvenanceRefused
from novel.orchestrator import approvals, state
from novel.stages.stage2 import knowledge_links as links
from novel.store import ArtifactStore
from tests.test_stage2 import main_text
from tests.stage2_fixtures import plot_blueprints as blueprints
from tests.test_stage2_grounding import character_preparation, pair  # noqa: F401


def _scope(knowledge, bp=None, *, digest='b' * 64, source=None):
    candidate = copy.deepcopy(bp or blueprints())
    candidate['_provenance'].update({key: value for key, value in
        knowledge.provenance.model_dump(mode='json').items()
        if key in {'code', 'root_clean_sha1', 'inputs', 'prompts', 'model', 'usage'}})
    return links.build_scope(
        MainText.model_validate(main_text() if source is None else source), knowledge,
        ChapterBlueprints.model_validate(candidate),
        main_sha256='a' * 64, knowledge_sha256=digest, blueprint_sha256='c' * 64)


def _submission(scope, decisions=None):
    # 只用于关闭式载体测试的显式合成决定；真实 C 的证据质量仍单独验收。
    return KnowledgeLinkSubmission(
        scope_sha256=scope.scope_sha256, actor='test:knowledge-commander',
        reason='合成关联边界测试', decisions=decisions if decisions is not None else [
            KnowledgeLinkDecision(obligation_id=row.obligation_id,
                expected_subject_sha256=row.subject_sha256,
                resolution=KnowledgeAtBeats(kind='at_beats', subject_refs=[row.candidate_subject_refs[0]],
                                            trigger_beat_ref=row.candidate_subject_refs[0].ref))
            for row in scope.obligations])


def _history(scope):
    return [KnowledgeLinkHistory(decision_sha256='d' * 64, accepted=True,
                                 submission=_submission(scope))]


def test_scope_separates_world_knowledge_reader_reveal_and_setup(pair):
    _registry, knowledge = pair
    scope = _scope(knowledge)
    kinds = {row.kind for row in scope.obligations}
    assert {'secret_reveal', 'secret_setup',
            'world_rule', 'world_rule_reveal', 'relation', 'relation_visibility'} <= kinds
    for row in scope.obligations:
        if row.kind in {'secret_reveal', 'secret_setup', 'world_rule_reveal', 'relation_visibility'}:
            assert not row.entry_subject_refs
    assert any(row.entry_subject_refs for row in scope.obligations if row.kind == 'relation')
    assert not {'knowledge_event', 'character_state'} & kinds


@pytest.mark.parametrize('change', ['audit', 'evidence_id'])
def test_unrelated_knowledge_change_inherits_exact_obligations(pair, change):
    _registry, knowledge = pair
    old = _scope(knowledge)
    changed = knowledge.model_copy(deep=True)
    if change == 'audit':
        changed.provenance.evidence_locator_failures = [('audit', 1, 'old', '已订正')]
    else:
        # 未被本轮任何主体消费的锚点不扩大义务失效（正式冗余锚点另由Core lint管理）。
        changed.evidence_anchors['ev_' + 'f' * 24] = next(iter(changed.evidence_anchors.values())).model_copy()
    current = _scope(changed, digest='e' * 64)
    assert current.scope_sha256 == old.scope_sha256
    assert current.obligations == old.obligations
    assert links.review(current, _history(old)).pending_obligation_ids == []


@pytest.mark.parametrize('change', ['claim', 'evidence', 'candidate_set'])
def test_current_candidate_bodies_evidence_and_ambiguity_invalidate_links(pair, change):
    _registry, knowledge = pair
    old = _scope(knowledge)
    candidate = copy.deepcopy(blueprints())
    fact = candidate['blueprints'][1]['beats'][0]
    if change == 'claim':
        fact['content'] += '候选业务改变'
    elif change == 'evidence':
        fact['evidence_refs'] += candidate['blueprints'][1]['beats'][1]['evidence_refs']
    else:
        duplicate = copy.deepcopy(fact)
        duplicate['beat_id'] = 'b02_999'
        candidate['blueprints'][1]['beats'].append(duplicate)
    current = _scope(knowledge, candidate)
    pending = links.review(current, _history(old)).pending_obligation_ids
    assert pending
    for row in current.obligations:
        if row.chapter_ordinal == 2:
            assert row.obligation_id in pending
    unaffected = [row for row in old.obligations
                  if row.kind == 'world_rule_reveal' and row.chapter_ordinal == 1]
    assert unaffected and all(row.obligation_id not in pending for row in unaffected)


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


def test_reveal_only_foreign_proof_does_not_consume_entire_foreign_chapter(pair):
    from novel.stages.stage2.plot_compile import source_digest
    _registry, knowledge = pair
    bp, source = blueprints(), main_text()
    before = _scope(knowledge, bp, source=source)
    source[0]['text'] += '\n\n院外微风吹过，无人经过。'
    bp['blueprints'][0]['source_sha256'] = source_digest(source[0]['text'])
    after = _scope(knowledge, bp, source=source)
    reveal = next(r for r in before.obligations if r.kind == 'secret_reveal')
    assert reveal.chapter_ordinal == 2 and knowledge.evidence_anchors[knowledge.arc_secrets[0].evidence_refs[0]].chapter_ordinal == 1
    updated = next(r for r in after.obligations if r.obligation_id == reveal.obligation_id)
    assert updated == reveal
    assert reveal.obligation_id not in links.review(after, _history(before)).pending_obligation_ids
    # 对外章开放了完整候选的事件仍应失效，不能把材料选择和单独证据混为一谈。
    assert links.review(after, _history(before)).pending_obligation_ids


def test_reveal_cannot_be_silently_promoted_to_chapter_entry(pair):
    _registry, knowledge = pair
    scope = _scope(knowledge)
    row = next(row for row in scope.obligations if row.kind == 'secret_reveal')
    submission = _submission(scope, [KnowledgeLinkDecision(
        obligation_id=row.obligation_id, expected_subject_sha256=row.subject_sha256,
        resolution={'kind': 'at_entry', 'subject_refs': [row.candidate_subject_refs[0]]})])
    with pytest.raises(HumanEditProtected, match='时点'):
        links.validate_submission(scope, submission)


@pytest.fixture
def produced(tmp_path, monkeypatch):
    from tests.test_plot_review_runtime import prepare_complete_book
    from tests.test_plot_producer import generate_plot
    monkeypatch.setenv('NOVEL_STAGE2_BLUEPRINT_CONTRACT', 'plot-blueprint-v1')
    book = prepare_complete_book(tmp_path)
    return generate_plot(book)


def _accept_current(ctx):
    from novel.views.stage2 import blueprint_review_workpack
    from tests.test_plot_review_runtime import request
    for scope in ('chapter:1', 'chapter:2', 'chapter:3', 'cross_chapter'):
        pack = blueprint_review_workpack(ctx.novel_dir.parent, ctx.sid, scope=scope)
        result = approvals.approve_stage2_review(ctx.novel_dir.parent, ctx.sid, request(pack))
    return result, pack


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
    from contracts.schema.api import BlueprintReviewWorkpack
    from tests.test_plot_review_runtime import request
    ctx, transport = produced
    base, sid = ctx.novel_dir.parent, ctx.sid
    endpoint = f'/api/novels/{sid}/stage2/blueprint-review-workpack'
    with TestClient(create_app(str(base))) as client:
        client.headers[UI_HEADER] = '1'
        for scope in ('chapter:1', 'chapter:2', 'chapter:3', 'cross_chapter'):
            response = client.get(endpoint, params={'scope': scope})
            assert response.status_code == 200, response.text
            pack = BlueprintReviewWorkpack.model_validate(response.json())
            submitted = request(pack)
            result = client.post(f'/api/novels/{sid}/approvals/stage2-review',
                                 json=submitted.model_dump(mode='json'))
            assert result.status_code == 200, result.text
        assert result.json()['stage2_reviews']['stage2.blueprint']['state'] == 'accepted'
        assert not result.json()['gate']['s2']
        assert not ArtifactStore(ctx).exists(STAGE2_KNOWLEDGE_LINKS)
    with TestClient(create_app(str(base))) as restarted:
        restarted.headers[UI_HEADER] = '1'
        current = restarted.get(endpoint, params={'scope': 'cross_chapter'})
        assert current.status_code == 200, current.text
        after = BlueprintReviewWorkpack.model_validate(current.json())
        assert after.review_token == pack.review_token
        assert after.knowledge_links.pending_obligation_ids == []
        assert len(after.knowledge_links.inherited) == len(pack.knowledge_links.scope.obligations)
        history, _digest = approvals.current_knowledge_link_decisions(base, sid)
        assert history[-1].submission == submitted.knowledge_links


def test_supporting_beats_do_not_implicitly_choose_the_disclosure_trigger():
    refs = [{'kind': 'beat', 'ref': 'b01_01'}, {'kind': 'beat', 'ref': 'b01_02'}]
    with pytest.raises(ValueError, match='trigger_beat_ref'):
        KnowledgeAtBeats.model_validate({'kind': 'at_beats', 'subject_refs': refs})
    with pytest.raises(ValueError, match='支持主体'):
        KnowledgeAtBeats(kind='at_beats', subject_refs=refs[:1], trigger_beat_ref='b01_02')
