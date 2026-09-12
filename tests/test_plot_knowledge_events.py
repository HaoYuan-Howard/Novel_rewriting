"""必要剧情知识的声明、审计和跨章依赖；纯契约反例不冒充模型语义验收。"""
from __future__ import annotations

import pytest

from contracts.artifacts import STAGE1_MAIN_TEXT, STAGE2_CHAPTER_BLUEPRINTS, STAGE2_GLOBAL_GROUNDING, STAGE2_CHARACTERS
from contracts.schema.knowledge_links import KnowledgeLinkSubmission
from contracts.schema.plot import PlotState
from contracts.schema.stage1 import MainText
from contracts.schema.stage2 import ChapterBlueprints
from novel.errors import HumanEditProtected, ProvenanceRefused
from novel.stages.stage2 import knowledge_links, plot_review
from novel.views.plot_review import PlotReviewSnapshot
from tests.stage2_fixtures import formal_subjects, plot_blueprints
from tests.test_stage2 import global_grounding, main_text
from tests.test_plot_producer import plot_contract  # noqa: F401


def snapshot():
    _registry, knowledge = formal_subjects(global_grounding())
    raw = plot_blueprints()
    raw['_provenance'].update({'code': 'fixture', 'root_clean_sha1': '0' * 40,
        'inputs': {}, 'prompts': {'FIXTURE_ONLY': '0' * 10}, 'model': 'fixture'})
    first = raw['blueprints'][0]
    first['states'] = [PlotState(state_id='st01_01', content='李宝珠知道父亲已经回城。',
        global_ref=None, modality='belief', holder={'char_id': 'char_001'},
        established_at={'kind': 'at_beat', 'beat_ref': first['beats'][-1]['beat_id']},
        ended_at=None, supersedes_state_refs=[], evidence_refs=first['beats'][-1]['evidence_refs']).model_dump()]
    first['carry_out_refs'] = ['st01_01']
    raw['blueprints'][1]['entry_state_refs'] = ['st01_01']
    return PlotReviewSnapshot(None, MainText.model_validate(main_text()),
        ChapterBlueprints.model_validate(raw), knowledge, STAGE2_CHAPTER_BLUEPRINTS,
        {STAGE1_MAIN_TEXT: 'a' * 64, STAGE2_CHAPTER_BLUEPRINTS: 'b' * 64,
         STAGE2_GLOBAL_GROUNDING: 'c' * 64, STAGE2_CHARACTERS: 'd' * 64}, (), _registry)


def pack(current, scope='chapter:2'):
    return plot_review.build_workpack(scope, current.inputs,
        additional_selections=current.additional_selections(scope),
        extra_identity=current.knowledge_scope(scope).scope_sha256)


def put(*, kind='character_knowledge', source_chapter=2, evidence_ref='e02_01'):
    return {'operation': 'put', 'event_id': 'ke_belief_shared', 'expected_event_sha256': None,
        'event': {'kind': kind, 'subject_ref': {'kind': 'state', 'ref': 'st01_01'},
                  'audience': {'kind': 'character', 'actor': {'char_id': 'char_002'}} if kind == 'character_knowledge' else None,
                  'chapter_ordinal': 2, 'evidence_refs': [
                      {'chapter_ordinal': source_chapter, 'evidence_ref': evidence_ref}]},
        'resolution': {'kind': 'at_beats', 'trigger_beat_ref': 'b02_01', 'subject_refs': [
            {'kind': 'state', 'ref': 'st01_01'}, {'kind': 'beat', 'ref': 'b02_01'}]}}


def submission(current, operations, *, scope='chapter:2'):
    return KnowledgeLinkSubmission(scope_sha256=current.knowledge_scope(scope).scope_sha256,
        actor='test:commander', reason='只验证权限与审计契约', decisions=[], event_operations=operations)


def test_state_does_not_create_automatic_reader_permission():
    current = snapshot()
    assert current.blueprints.blueprints[0].states
    assert not any(row.kind == 'state_reveal' for row in current.all_knowledge.obligations)
    assert current.all_knowledge.registered_events == []


def test_same_review_registers_later_character_knowledge_without_copying_state_text():
    current = snapshot()
    application, after = current.propose_knowledge(pack(current), submission(current, [put()]), accepted=True)
    event = next(row for row in after.all_knowledge.obligations if row.kind == 'character_knowledge')
    assert event.chapter_ordinal == 2 and event.subject_ref.ref == 'st01_01'
    assert event.audience.actor.char_id == 'char_002'
    assert application.decisions[0].resolution.trigger_beat_ref == 'b02_01'
    assert '李宝珠知道' not in application.model_dump_json()
    assert application.event_operations[0].event.evidence_refs[0].evidence_ref.startswith('p:')
    assert knowledge_links.review(after.knowledge_scope('chapter:2'), after.knowledge_history).inherited[0].obligation_id == event.obligation_id
    assert after.identity('chapter:1')[0] == current.identity('chapter:1')[0]
    assert after.identity('chapter:3')[0] == current.identity('chapter:3')[0]


def test_cross_chapter_source_evidence_is_not_forced_to_target_chapter():
    current = snapshot()
    # 第一章的被引用 State 证据已作为第二章必要继承状态材料下发。
    evidence = current.blueprints.blueprints[0].states[0].evidence_refs[0]
    operation = put(source_chapter=1, evidence_ref=evidence)
    application, after = current.propose_knowledge(pack(current), submission(current, [operation]), accepted=True)
    proof = application.event_operations[0].event.evidence_refs[0]
    assert proof.chapter_ordinal == 1
    assert after.all_knowledge.registered_events[0].event.chapter_ordinal == 2


def test_registration_cas_retirement_and_recreation_keep_identity_history():
    current = snapshot()
    _application, after = current.propose_knowledge(pack(current), submission(current, [put()]), accepted=True)
    with pytest.raises(ProvenanceRefused, match='合法后继'):
        after.propose_knowledge(pack(after), submission(after, [put()]), accepted=True)
    registered = after.all_knowledge.registered_events[0]
    retire = {'operation': 'retire', 'event_id': registered.event_id,
              'expected_event_sha256': registered.event_sha256, 'reason': '本许可重复了已明确表达的剧情，不需独立管理'}
    _application, retired = after.propose_knowledge(pack(after), submission(after, [retire]), accepted=True)
    assert retired.all_knowledge.registered_events[0].retired
    assert not any(row.kind == 'character_knowledge' for row in retired.all_knowledge.obligations)
    assert len(retired.knowledge_history) == 2
    with pytest.raises(ProvenanceRefused, match='合法后继'):
        retired.propose_knowledge(pack(retired), submission(retired, [put()]), accepted=True)


def test_removed_subject_keeps_event_pending_and_can_be_explicitly_retired():
    from dataclasses import replace
    current = snapshot()
    _, after = current.propose_knowledge(pack(current), submission(current, [put()]), accepted=True)
    raw = after.blueprints.model_dump(mode='json', by_alias=True)
    raw['blueprints'][0]['states'] = []
    raw['blueprints'][0]['carry_out_refs'] = []
    raw['blueprints'][1]['entry_state_refs'] = []
    changed = replace(after, blueprints=ChapterBlueprints.model_validate(raw))
    changed.__dict__['knowledge_history'] = after.knowledge_history
    pending = knowledge_links.review(changed.knowledge_scope('chapter:2'), changed.knowledge_history)
    assert any(ref.startswith('character_knowledge:ke_belief_shared:') for ref in pending.pending_obligation_ids)
    old = changed.all_knowledge.registered_events[0]
    operation = {'operation': 'retire', 'event_id': old.event_id,
        'expected_event_sha256': old.event_sha256, 'reason': '命题已由上游有据修订移除，明确撤销原许可'}
    _, retired = changed.propose_knowledge(pack(changed), submission(changed, [operation]), accepted=True)
    assert retired.all_knowledge.registered_events[0].retired
    assert len(retired.knowledge_history) == 2


def test_unprovided_subject_or_evidence_cannot_register_new_permission():
    current = snapshot()
    operation = put()
    operation['event']['subject_ref'] = {'kind': 'beat', 'ref': 'b03_01'}
    with pytest.raises(HumanEditProtected, match='完整提供'):
        current.propose_knowledge(pack(current), submission(current, [operation]), accepted=True)
    operation = put(source_chapter=1, evidence_ref='p:999:999')
    with pytest.raises(ValueError):
        current.propose_knowledge(pack(current), submission(current, [operation]), accepted=True)


@pytest.mark.parametrize('kind', ['character_knowledge', 'reader_reveal'])
def test_same_round_registration_is_current_durable_and_replay_is_idempotent(tmp_path, kind):
    from tests.test_plot_producer import prepare_book, generate_plot, responses
    from tests.test_plot_review_runtime import request
    from novel.views.stage2 import blueprint_review_workpack
    from novel.orchestrator import approvals
    from novel.store import ArtifactStore
    by = responses()
    by[1]['states'] = [{
        'state_key': 'st1', 'content': '李宝珠知道父亲已经回城。', 'global_ref': None,
        'modality': 'belief', 'holder': {'char_id': 'char_001'},
        'established_at': {'kind': 'at_beat', 'beat_ref': 'b2'}, 'ended_at': None,
        'supersedes_state_refs': [], 'evidence': [{'start_para': 3, 'end_para': 4}]}]
    by[1]['carry_out_refs'] = ['st1']
    by[2]['entry_state_refs'] = ['st01_01']
    book = prepare_book(tmp_path)
    ctx, transport = generate_plot(book, by)
    base, sid = book.parent, ctx.sid
    for ordinal in (1, 3):
        workpack = blueprint_review_workpack(base, sid, scope=f'chapter:{ordinal}')
        approvals.approve_stage2_review(base, sid, request(workpack))
    first_before = blueprint_review_workpack(base, sid, scope='chapter:1')
    first_decision = next(row for row in approvals.stage2_review_history(base, sid)
                          if row.node == 'stage2.blueprint' and row.scope == 'chapter:1')
    workpack = blueprint_review_workpack(base, sid, scope='chapter:2')
    outgoing = request(workpack)
    outgoing.knowledge_links.event_operations = KnowledgeLinkSubmission.model_validate({
        **outgoing.knowledge_links.model_dump(mode='json'), 'event_operations': [put(kind=kind)]}).event_operations
    before_calls = len(transport.calls)
    approvals.approve_stage2_review(base, sid, outgoing)
    approvals.approve_stage2_review(base, sid, outgoing)
    current = PlotReviewSnapshot.read(ArtifactStore(ctx), approvals.stage2_review_history(base, sid))
    assert {r.scope: r.state for r in current.progress().scopes} == {
        'chapter:1': 'accepted' if kind == 'character_knowledge' else 'expired',
        'chapter:2': 'accepted', 'chapter:3': 'accepted', 'cross_chapter': 'missing'}
    history = [r for r in current.history if r.node == 'stage2.blueprint']
    assert len(history) == 3
    assert current.records['chapter:1'] == first_decision
    first_after = current.workpack('chapter:1')
    if kind == 'character_knowledge':
        assert first_after.subject_sha256 == first_before.subject_sha256
        assert first_after.dependency_manifest == first_before.dependency_manifest
        assert first_after.material == first_before.material
        assert first_after.knowledge_links == first_before.knowledge_links
    else:
        assert first_after.subject_sha256 != first_before.subject_sha256
        assert any(row.event.kind == 'reader_reveal'
                   for row in first_after.knowledge_links.scope.registered_events)
    assert current.records['chapter:2'].knowledge_links == outgoing.knowledge_links
    assert current.records['chapter:2'].knowledge_application.event_operations[0].event.evidence_refs[0].evidence_ref.startswith('p:')
    assert len(transport.calls) == before_calls


def formal_links(current):
    from contracts.schema.knowledge_links import KnowledgeLinks, KnowledgeLinkRecord
    records = []
    for event in current.all_knowledge.obligations:
        trigger = next(ref for ref in event.candidate_subject_refs if ref.kind == 'beat')
        support = [trigger]
        if event.kind in ('reader_reveal', 'character_knowledge') and event.subject_ref not in support:
            support.append(event.subject_ref)
        records.append(KnowledgeLinkRecord(obligation_id=event.obligation_id,
            expected_subject_sha256=event.subject_sha256,
            resolution={'kind': 'at_beats', 'trigger_beat_ref': trigger.ref, 'subject_refs': support},
            actor='test:显式许可', reason='只验证编译，不替代语义审阅', source_decision_sha256='d' * 64))
    return KnowledgeLinks(scope=current.all_knowledge, links=records,
        _provenance={'code': 'fixture', 'root_clean_sha1': '0' * 40, 'inputs': {},
                     'decision_sha256': 'e' * 64}).model_dump(mode='json', by_alias=True)


def test_future_state_disclosure_is_planner_truth_but_not_prior_writer_body():
    from novel.stages.stage4.plot_design import build_blind_projection
    from novel.stages.stage4.plot_context import build_writer_context
    from novel.stages.stage4.plot_materials import chapter_materials
    from novel.stages.stage4.measure import identity_plan
    current = snapshot()
    _, after = current.propose_knowledge(pack(current), submission(current, [put(kind='reader_reveal')]), accepted=True)
    links = formal_links(after)
    bp = after.blueprints.model_dump(mode='json', by_alias=True)
    source = after.main.model_dump(mode='json')
    grounding = after.knowledge.model_dump(mode='json', by_alias=True)
    registry = after.characters.model_dump(mode='json', by_alias=True)
    truth, _ = build_blind_projection(bp, root_clean_sha1='1' * 40,
                                     global_g=grounding, knowledge_links=links, characters=registry,
                                     main_text=main_text())
    assert any(k['kind'] == 'reader_reveal' and k['timing'] == 'future_chapter'
               for k in truth['chapters'][0]['knowledge'])
    material = chapter_materials(1, bp, grounding, links, source, [], characters=registry)
    assert any(e['event']['kind'] == 'reader_reveal' and e['event']['chapter_ordinal'] == 2
               for e in material.knowledge_events)
    plan = identity_plan(bp, source)
    ctx = build_writer_context(plan, grounding, bp, source, knowledge_links=links, characters=registry).ctx
    first, second = ctx['chapters'][:2]
    assert {'kind': 'state', 'ref': 'st01_01'} in first['restricted_subject_refs']
    assert not any(s['state_ref'] == 'st01_01' for s in first['states'])
    assert any(s['state_ref'] == 'st01_01' and s['modality'] == 'belief' for s in second['states'])
    assert any(e['kind'] == 'reader_reveal' and e['subject_ref']['ref'] == 'st01_01'
               for e in second['knowledge_events'])


def test_character_can_learn_prior_beat_without_new_fact_or_cross_chapter_delivery():
    from novel.stages.stage4.plot_design import build_blind_projection
    from novel.stages.stage4.plot_context import build_writer_context
    from novel.stages.stage4.measure import identity_plan
    from contracts.schema.knowledge_links import RegisteredPlotKnowledgeEvent
    current = snapshot()
    event = put()['event']
    event['subject_ref'] = {'kind': 'beat', 'ref': 'b01_01'}
    # 全书 C 能读完整原文并登记此事件；本例直接构造同一正式载体以测试下游消费。
    registered = RegisteredPlotKnowledgeEvent(event_id='ke_prior_beat', event_sha256='f' * 64,
        event=event, retired=False, reason=None)
    current.__dict__['all_knowledge'] = knowledge_links.build_scope(current.main, current.knowledge,
        current.blueprints, main_sha256='a' * 64, knowledge_sha256='b' * 64,
        blueprint_sha256='c' * 64, registered_events=[registered])
    bp = current.blueprints.model_dump(mode='json', by_alias=True)
    links = formal_links(current)
    grounding = current.knowledge.model_dump(mode='json', by_alias=True)
    registry = current.characters.model_dump(mode='json', by_alias=True)
    truth, _ = build_blind_projection(bp, root_clean_sha1='1' * 40,
        global_g=grounding, knowledge_links=links, characters=registry, main_text=main_text())
    background = truth['chapters'][1]['background_beats']
    assert len(background) == 1 and background[0]['participant_aliases']
    ctx = build_writer_context(identity_plan(bp, main_text()), grounding, bp,
                               main_text(), knowledge_links=links, characters=registry).ctx['chapters'][1]
    assert [b['beat_ref'] for b in ctx['background_beats']] == ['b01_01']
    assert all(d['beat_ref'] != 'b01_01' for scene in ctx['scenes'] for d in scene['deliveries'])
    mother = next(row['actor_ref'] for row in ctx['characters'] if '母亲' in row['surfaces'])
    assert any(e['kind'] == 'character_knowledge' and e['audience'] == {'kind': 'character', 'actor_ref': mother, 'label': None}
               for e in ctx['knowledge_events'])



@pytest.mark.parametrize('kind', ['character_knowledge', 'reader_reveal'])
def test_sparse_event_chapter_closure_preserves_current_and_carried_history(kind):
    from dataclasses import replace

    current = snapshot()
    raw = current.blueprints.model_dump(mode='json', by_alias=True)
    raw['blueprints'][1]['carry_out_refs'] = ['st01_01']
    raw['blueprints'][2]['entry_state_refs'] = ['st01_01']
    current = replace(current, blueprints=ChapterBlueprints.model_validate(raw))
    before = {n: pack(current, f'chapter:{n}') for n in (1, 2, 3)}
    _application, after = current.propose_knowledge(
        pack(current), submission(current, [put(kind=kind)]), accepted=True)
    for ordinal in (1, 2, 3):
        scope = after.knowledge_scope(f'chapter:{ordinal}')
        direct = knowledge_links.build_scope(after.main, after.knowledge, after.blueprints,
            main_sha256=after.hashes[STAGE1_MAIN_TEXT],
            knowledge_sha256=after.hashes[STAGE2_GLOBAL_GROUNDING],
            blueprint_sha256=after.hashes[STAGE2_CHAPTER_BLUEPRINTS],
            chapter_ordinal=ordinal, registered_events=after.all_knowledge.registered_events)
        expected = ordinal >= 2 or kind == 'reader_reveal'
        assert bool(scope.registered_events) == expected
        assert direct.registered_events == scope.registered_events
        assert direct.scope_sha256 == scope.scope_sha256
        assert (after.identity(f'chapter:{ordinal}')[0] != before[ordinal].subject_sha256) == expected
        if ordinal == 1 and kind == 'character_knowledge':
            assert pack(after, 'chapter:1').material == before[1].material
            assert after.additional_selections('chapter:1') == current.additional_selections('chapter:1')
    # 同章接受有实际关联；后章 carry 仅带历史事件，不新造第三章的知情义务。
    own = knowledge_links.review(after.knowledge_scope('chapter:2'), after.knowledge_history)
    assert any(row.obligation_id.startswith(kind + ':ke_belief_shared:2') for row in own.inherited)
    assert not any(row.kind == kind for row in after.knowledge_scope('chapter:3').obligations)
    # 历史事件实际证据仍进入后章的精确来源闭包。
    assert any(row.input_rel == STAGE1_MAIN_TEXT and row.chapter_ordinal == 2
               and row.projection in ('source_ranges', 'source_chapter')
               for row in after.additional_selections('chapter:3'))
