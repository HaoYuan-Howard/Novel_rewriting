# -*- coding: utf-8 -*-
"""人物 → 必要知识 → 审阅/下游视图的真实 producer 消费链；不发付费请求。"""
from __future__ import annotations

import json
import copy

import pytest

from contracts.artifacts import (
    STAGE2_BLUEPRINT_CHARACTER_PROJECTION, STAGE2_CHARACTERS, STAGE2_GLOBAL_GROUNDING,
)
from contracts.schema.characters import CharactersEdit
from contracts.schema.knowledge import KnowledgeGrounding
from contracts.schema.stage2 import GlobalEdit
from novel.errors import ProvenanceRefused, UpstreamMissing
from novel.node_agents import Stage2GlobalAgent
from novel.stages.stage2 import characters, extract, grounding
from novel.store import ArtifactStore
from tests.test_stage2_characters import _ctx, _generate
from tests.test_stage2_global_agent import _Driver, _free_ctx, _resume_ctx
from tests.test_stage2_grounding import _knowledge_response


def _prepared(tmp_path):
    ctx = _ctx(tmp_path)
    registry, _ = _generate(ctx)
    ctx.node = 'stage2.global'
    return ctx, registry


def _run(ctx):
    wire = json.dumps({'payload': {'kind': 'full_candidate',
                                  'candidate': _knowledge_response().model_dump(mode='json')}},
                      ensure_ascii=False)
    driver = _Driver([wire], ctx.novel_dir, session_id='knowledge-flow')
    result = Stage2GlobalAgent(ctx, driver=driver).run()
    return result, driver


def test_global_wire_and_disk_have_no_static_character_copy(tmp_path):
    ctx, registry = _prepared(tmp_path)
    store = ArtifactStore(ctx)
    character_sha = store.full_sha256_or_none(STAGE2_CHARACTERS)
    prepared = extract.prepare_global_candidate(ctx, agent_envelope=True)
    assert 'role_evidence' not in prepared.user
    assert 'from_chapter' not in prepared.user
    assert 'role_fate_patch' not in prepared.system
    result, driver = _run(ctx)
    assert result.passed
    assert [kind for kind, _request in driver.calls] == ['start']
    artifact = store.read_own(KnowledgeGrounding, STAGE2_GLOBAL_GROUNDING)[0]
    assert artifact.characters_reference_sha256 == grounding.knowledge_reference_sha256(registry)
    assert 'characters' not in artifact.model_dump(mode='json')
    assert 'entities' not in artifact.model_dump(mode='json')
    assert store.full_sha256_or_none(STAGE2_CHARACTERS) == character_sha
    assert store.freshness(STAGE2_GLOBAL_GROUNDING).state == 'fresh'
    assert grounding.read_own(store)[0] == artifact
    assert grounding.read_registry(store, own=True) == registry


def test_global_refuses_missing_static_registry_before_driver(tmp_path):
    from tests.test_stage2_global_agent import _ctx as global_ctx
    ctx = global_ctx(tmp_path, with_characters=False)
    driver = _Driver([], ctx.novel_dir, session_id='missing-registry')
    with pytest.raises(UpstreamMissing) as caught:
        Stage2GlobalAgent(ctx, driver=driver).run()
    assert 'characters' in str(caught.value)
    assert not driver.calls


def test_knowledge_commit_does_not_own_missing_character_projection(tmp_path):
    ctx, _ = _prepared(tmp_path)
    store = ArtifactStore(ctx)
    store.archive_round(STAGE2_BLUEPRINT_CHARACTER_PROJECTION)
    result, driver = _run(ctx)
    assert result.passed and len(driver.calls) == 1
    assert not store.exists(STAGE2_BLUEPRINT_CHARACTER_PROJECTION)
    from novel.llm import stage2_global_runs
    journal = stage2_global_runs.committed(ctx)
    assert STAGE2_BLUEPRINT_CHARACTER_PROJECTION not in json.dumps(journal)


def test_saved_knowledge_cannot_overwrite_character_identity(tmp_path):
    ctx, registry = _prepared(tmp_path)
    result, _ = _run(ctx)
    store = ArtifactStore(ctx)
    original = {rel: store.path(rel).read_bytes() for rel in (
        STAGE2_CHARACTERS, STAGE2_GLOBAL_GROUNDING, STAGE2_BLUEPRINT_CHARACTER_PROJECTION)}
    mixed = result.artifact.model_dump(mode='json', by_alias=True)
    mixed['characters'] = {'char_001': {'role': '越权修改'}}
    with pytest.raises(ValueError, match='Extra inputs'):
        KnowledgeGrounding.model_validate(mixed)
    from novel.views.stage2 import global_view
    from contracts.schema.api import GlobalView
    from contracts.schema import ARTIFACT_SPECS
    view = global_view(ctx.novel_dir.parent, ctx.sid)
    assert type(view) is GlobalView
    assert all(spec.model is not GlobalView for spec in ARTIFACT_SPECS.values())
    displayed = view.model_dump(mode='json', by_alias=True)
    assert 'characters' not in displayed
    displayed['relations'][0]['subject']['display'] = '只改展示副本'
    assert global_view(ctx.novel_dir.parent, ctx.sid).relations[0].subject.display == registry.characters['char_001'].canonical
    assert {rel: store.path(rel).read_bytes() for rel in original} == original
    assert grounding.read_registry(store, own=True) == registry


@pytest.mark.parametrize('window', ['answer', 'checkpoint', 'formal', 'review'])
def test_knowledge_recovery_reuses_saved_result_at_every_commit_window(tmp_path, monkeypatch, window):
    from novel.llm import stage2_global_runs
    ctx, _ = _prepared(tmp_path)
    owner, name = {
        'answer': (extract, 'write_global_agent_checkpoint'),
        'checkpoint': (extract, '_finalize_agent_global_formal'),
        'formal': (stage2_global_runs, 'mark_committed'),
        'review': (extract, '_refresh_current_global_review'),
    }[window]
    original = getattr(owner, name)
    def stop(*args, **kwargs):
        raise RuntimeError('模拟提交窗口中断')
    monkeypatch.setattr(owner, name, stop)
    with pytest.raises(RuntimeError, match='模拟提交窗口'):
        _run(ctx)
    monkeypatch.setattr(owner, name, original)
    resumed = _resume_ctx(ctx)
    driver = _Driver([], resumed.novel_dir, session_id='no-second-purchase')
    result = Stage2GlobalAgent(resumed, driver=driver).run()
    assert result.passed and not driver.calls
    assert ArtifactStore(resumed).read_own(KnowledgeGrounding, STAGE2_GLOBAL_GROUNDING)[0]
    assert stage2_global_runs.committed(resumed)['state'] == 'committed'


def _edit_unused_character_evidence(free):
    """补入父亲翻账的真实旁证段，不改 canonical/surfaces/role 的知识依赖。"""
    from contracts.schema.stage1 import MainText
    from novel.stages.stage2.core_evidence import evidence_snippet
    store = ArtifactStore(free)
    registry = grounding.read_registry(store, own=True)
    main = store.read_own(MainText, extract.MAIN_TEXT)[0].model_dump(mode='json')
    edit = characters.edit_template(free)
    edit['operations'] = edit['operations'][:1]
    operation = edit['operations'][0]
    previous = registry.characters[operation['char_id']]
    anchor = registry.evidence_anchors[previous.role_evidence_refs[0]]
    extra_locator = '她忽然想起父亲从前也这样翻账。'
    assert previous.canonical == '李宝珠' and '替父亲掌管家中账目' in previous.role
    chapter = next(row for row in main if row['chapter_ordinal'] == 3)
    assert extra_locator in chapter['text']
    assert extra_locator not in evidence_snippet(anchor, main)
    operation['character']['role_evidence_refs'] = [*previous.role_evidence_refs, {
        'chapter_ordinal': 3, 'locator': extra_locator}]
    updated = characters.apply_edit(free, CharactersEdit.model_validate(edit),
        actor='test:editor', reason='补入父亲翻账旁证段，保留既有角色事实')
    assert grounding.knowledge_reference_sha256(updated) == grounding.knowledge_reference_sha256(registry)
    assert updated.evidence_anchors != registry.evidence_anchors
    return updated


def test_unused_character_evidence_change_keeps_knowledge_current(tmp_path, monkeypatch):
    ctx, _ = _prepared(tmp_path)
    _run(ctx)
    before_store = ArtifactStore(ctx)
    knowledge_sha = before_store.full_sha256_or_none(STAGE2_GLOBAL_GROUNDING)
    free = _free_ctx(ctx)
    free.node = characters.NODE
    # 人物正式证据已落、审阅后继硬杀：知识仍fresh，但审阅必须明确待刷新。
    # 正常人物写口已经会自动刷新义务，不能把这项能力误测为永久过期。
    original_refresh = characters._refresh_review
    def interrupted(*args, **kwargs):
        raise RuntimeError('模拟人物证据落盘后的审阅尾窗')
    monkeypatch.setattr(characters, '_refresh_review', interrupted)
    with pytest.raises(RuntimeError, match='审阅尾窗'):
        _edit_unused_character_evidence(free)
    monkeypatch.setattr(characters, '_refresh_review', original_refresh)
    updated = grounding.read_registry(ArtifactStore(free), own=True)
    assert before_store.full_sha256_or_none(STAGE2_GLOBAL_GROUNDING) == knowledge_sha
    assert before_store.freshness(STAGE2_GLOBAL_GROUNDING).state == 'fresh'
    from contracts.artifacts import STAGE2_GLOBAL_REVIEW
    from novel.quality.global_review import require_current
    from novel.orchestrator import state
    ov = state.overview(ctx.novel_dir.parent, ctx.sid)
    assert state.stage2_global_review_refresh_needed(ov)
    free.node = 'stage2.global'
    review = extract.run_refresh_review(free)
    require_current(grounding.read_own(before_store)[1], review,
        characters=updated.model_dump(mode='json', by_alias=True))
    assert before_store.full_sha256_or_none(STAGE2_GLOBAL_GROUNDING) == knowledge_sha
    assert before_store.freshness(STAGE2_GLOBAL_REVIEW).state == 'fresh'
    assert not state.stage2_global_review_refresh_needed(state.overview(ctx.novel_dir.parent, ctx.sid))


@pytest.mark.parametrize('window', ['answer', 'checkpoint', 'formal', 'review'])
def test_unused_character_change_during_recovery_requires_no_second_purchase(tmp_path, monkeypatch, window):
    from novel.llm import stage2_global_runs
    ctx, _ = _prepared(tmp_path)
    owner, name = {
        'answer': (extract, 'write_global_agent_checkpoint'),
        'checkpoint': (extract, '_finalize_agent_global_formal'),
        'formal': (stage2_global_runs, 'mark_committed'),
        'review': (extract, '_refresh_current_global_review'),
    }[window]
    original = getattr(owner, name)
    def stop(*args, **kwargs):
        raise RuntimeError('人物订正前模拟知识提交中断')
    monkeypatch.setattr(owner, name, stop)
    with pytest.raises(RuntimeError, match='人物订正前'):
        _run(ctx)
    monkeypatch.setattr(owner, name, original)
    free = _free_ctx(ctx)
    free.node = characters.NODE
    updated = _edit_unused_character_evidence(free)
    resumed = _resume_ctx(ctx)
    driver = _Driver([], resumed.novel_dir, session_id='unused-character-recovery')
    result = Stage2GlobalAgent(resumed, driver=driver).run()
    assert result.passed and not driver.calls
    assert 'characters' not in result.artifact.model_dump(mode='json')
    assert grounding.read_registry(ArtifactStore(resumed), own=True) == updated
    assert ArtifactStore(resumed).freshness(STAGE2_GLOBAL_GROUNDING).state == 'fresh'


def test_free_knowledge_edit_changes_no_static_or_blueprint_projection(tmp_path):
    ctx, _ = _prepared(tmp_path)
    result, _ = _run(ctx)
    store = ArtifactStore(ctx)
    hashes = {rel: store.full_sha256_or_none(rel) for rel in (
        STAGE2_CHARACTERS, STAGE2_BLUEPRINT_CHARACTER_PROJECTION)}
    setting = copy.deepcopy(result.artifact.setting.model_dump(mode='json'))
    setting['era'] = '中国古代架空时代'
    # 新主张显式同交当前有据 refs；验证回写不会改人物或称名投影。
    edit = GlobalEdit.model_validate({'setting': setting})
    free = _free_ctx(ctx)
    ArtifactStore(free).write_sparse_input(extract.GLOBAL_EDIT, edit)
    changed, _report = extract.run_apply_edit(free, extract.GLOBAL_EDIT)
    assert changed.setting.era == '中国古代架空时代'
    assert {rel: store.full_sha256_or_none(rel) for rel in hashes} == hashes
    assert store.freshness(STAGE2_GLOBAL_GROUNDING).state == 'fresh'
    assert 'characters' not in store.read_own(KnowledgeGrounding, STAGE2_GLOBAL_GROUNDING)[0].model_dump(mode='json')


@pytest.mark.parametrize('node', [
    'stage4.plan', 'stage4.context', 'stage5.write', 'stage5.review',
    'stage6.hook', 'stage6.semantic', 'stage7.rename',
])
def test_real_generation_consumer_records_both_registered_input_edges(tmp_path, node):
    from contracts.artifacts import STAGE2_GLOBAL_REVIEW
    from novel.stages.stage2.global_review import generation_grounding
    from tests.factories import approved_global_review
    ctx, registry = _prepared(tmp_path)
    result, _ = _run(ctx)
    ArtifactStore(ctx).write_round(STAGE2_GLOBAL_REVIEW, approved_global_review(result.artifact))
    consumer = _free_ctx(ctx)
    consumer.node = node
    view = generation_grounding(ArtifactStore(consumer))
    assert view.entities.characters.keys() == registry.characters.keys()
    assert {STAGE2_CHARACTERS, STAGE2_GLOBAL_GROUNDING, STAGE2_GLOBAL_REVIEW} <= consumer.edges.keys()
    assert all(consumer.edges[rel].consumed is not None for rel in (
        STAGE2_CHARACTERS, STAGE2_GLOBAL_GROUNDING, STAGE2_GLOBAL_REVIEW))


def test_static_mutations_are_rejected_by_knowledge_edit_schema():
    from pydantic import ValidationError
    for edit in (
        {'characters': [{'char_id': 'char_001', 'role': '越权身份'}]},
        {'addresses': [{'char_id': 'char_001', 'address_index': 0, 'context': 'reference'}]},
        {'characters_replace': {}},
        {'collection_edits': [{'collection': 'characters', 'expected_collection_sha256': '0' * 64}]},
    ):
        with pytest.raises(ValidationError):
            GlobalEdit.model_validate(edit)


def test_three_repair_owners_keep_exactly_two_commander_seats():
    from contracts.schema.api import Stage2NodeReviewRequest
    from contracts.invariants import PLOT_REVIEW_DIMENSIONS
    from pydantic import ValidationError
    for reviewer, owners in (
        ('stage2.global', ('stage2.characters', 'stage2.global')),
        ('stage2.blueprint', ('stage2.characters', 'stage2.global', 'stage2.blueprint')),
    ):
        for owner in owners:
            reason = '原文有据的定向返工'
            scoped = {} if reviewer == 'stage2.global' else {
                'scope': 'chapter:1', 'plot_review': {
                    'decision': 'revise', 'reason': reason,
                    'coverage': {name: {'state': 'issue', 'issue_refs': ['new:1']}
                                 if name == 'attribution' else {'state': 'pass', 'issue_refs': []}
                                 for name in PLOT_REVIEW_DIMENSIONS},
                    'new_issues': [{'issue_key': 'new:1', 'dimension': 'attribution',
                        'problem': reason, 'subjects': [{'kind': 'beat', 'ref': 'b01_01'}],
                        'evidence_refs': [{'chapter_ordinal': 1, 'evidence_ref': 'e01_01'}]}],
                    'dispositions': []}}
            request = Stage2NodeReviewRequest(node=reviewer, review_token='current-token',
                decision='rework', actor='test:reviewer', reason=reason,
                owner=owner, chapter_ordinals=[1] if owner == 'stage2.blueprint' else [], **scoped)
            assert request.node == reviewer and request.owner == owner
            if reviewer == 'stage2.blueprint':
                assert request.scope == 'chapter:1'
                assert set(type(request.plot_review.coverage).model_fields) == set(PLOT_REVIEW_DIMENSIONS)
    with pytest.raises(ValidationError):
        Stage2NodeReviewRequest(node='stage2.characters', review_token='current-token',
                                decision='accept', actor='test:reviewer', reason='不能新增第三席')


@pytest.mark.parametrize('window', ['none', 'checkpoint', 'outcome'])
def test_knowledge_validator_custody_replays_sealed_replacement_without_wire(tmp_path, monkeypatch, window):
    from novel.llm import response_repair, stage2_global_runs
    from novel.llm.client import LlmFailed
    ctx, _ = _prepared(tmp_path)
    prepared = extract.prepare_global_candidate(ctx, agent_envelope=True)
    candidate = _knowledge_response().model_dump(mode='json', by_alias=True)
    wrong = copy.deepcopy(candidate)
    wrong['setting']['era_evidence_refs'] = ['e999999']
    def wire(value):
        return json.dumps({'payload': {'kind': 'full_candidate', 'candidate': value}}, ensure_ascii=False)
    driver = _Driver([wire(wrong)], ctx.novel_dir, session_id='knowledge-rejected')
    with pytest.raises(LlmFailed):
        Stage2GlobalAgent(ctx, driver=driver, max_turns=1).run()
    assert len(driver.calls) == 1
    assert not ArtifactStore(ctx).exists(STAGE2_GLOBAL_GROUNDING)
    records = response_repair.records_for_chain(
        ctx.novel_dir, extract._global_validator_custody_chain(ctx, prepared))
    [record] = records
    response_repair.candidate_path(record).write_text(wire(candidate), encoding='utf-8')
    response_repair.submit(ctx.novel_dir, record.attempt_id, actor='test:editor', reason='按原文修正知识证据候选')
    resumed = _resume_ctx(ctx)
    no_wire = _Driver([], resumed.novel_dir, session_id='knowledge-custody-resume')
    if window != 'none':
        owner, name = ((stage2_global_runs, 'write_checkpoint') if window == 'checkpoint'
                       else (extract, '_note_global_custody_terminal_outcome'))
        original = getattr(owner, name)
        def stop(*args, **kwargs):
            raise RuntimeError('模拟 sealed 知识恢复中断')
        monkeypatch.setattr(owner, name, stop)
        with pytest.raises(RuntimeError, match='sealed 知识'):
            Stage2GlobalAgent(resumed, driver=no_wire, max_turns=1).run()
        monkeypatch.setattr(owner, name, original)
    result = Stage2GlobalAgent(_resume_ctx(ctx), driver=no_wire, max_turns=1).run()
    assert result.passed and not no_wire.calls
    assert response_repair.find_attempt(ctx.novel_dir, record.attempt_id).outcome['decision'] == 'accepted'
