# -*- coding: utf-8 -*-
"""当前知识决定 → 场景重排 → 真实写手材料；合成关联不冒充语义质量验收。"""
from __future__ import annotations

import copy
import json

import pytest

from contracts.schema.knowledge_links import (
    KnowledgeAtEntry, KnowledgeAtFacts, KnowledgeLinkHistory, KnowledgeLinks,
    KnowledgeLinksProvenance,
)
from contracts.schema.stage4 import CtxChapter
from novel.errors import RefusedByStructure
from novel.stages.stage2 import grounding, knowledge_links
from novel.stages.stage4.context import build_writer_context
from novel.stages.stage4.measure import identity_plan
from novel.stages.stage5.prompts import build_material, revise_user
from tests.factories import stage5_book
from tests.test_stage2 import blueprints, main_text
from tests.test_stage2_grounding import character_preparation, pair  # noqa: F401
from tests.test_stage2_knowledge_links import _scope, _submission


def _links(knowledge, bp):
    scope = _scope(knowledge, bp)
    submission = _submission(scope)
    kinds = {row.obligation_id: row.kind for row in scope.obligations}
    updated = []
    for decision in submission.decisions:
        row = next(row for row in scope.obligations if row.obligation_id == decision.obligation_id)
        if kinds[row.obligation_id] == 'secret_reveal':
            # 支持证据多条，明确选择后场触发；不能根据数组顺序猜测时点。
            resolution = KnowledgeAtFacts(kind='at_facts', fact_refs=row.candidate_fact_refs,
                                          trigger_fact_ref=row.candidate_fact_refs[-1])
        elif row.kind in {'setting', 'world_rule', 'relation'}:
            resolution = KnowledgeAtEntry(kind='at_entry', fact_refs=[row.entry_fact_refs[0]])
        else:
            resolution = decision.resolution
        updated.append(decision.model_copy(update={'resolution': resolution}))
    submission.decisions = updated
    history = [KnowledgeLinkHistory(decision_sha256='d' * 64, accepted=True, submission=submission)]
    return KnowledgeLinks(scope=scope, links=knowledge_links.require_complete(scope, history),
        _provenance=KnowledgeLinksProvenance(code='disclosure-fixture',
            root_clean_sha1=knowledge.provenance.root_clean_sha1, inputs={},
            decision_sha256='d' * 64)).model_dump(by_alias=True, mode='json')


def _inputs(pair):
    registry, knowledge = pair
    bp, main = copy.deepcopy(blueprints()), main_text()
    plan = identity_plan(bp, main)
    view = grounding.compose(registry, knowledge).model_dump(by_alias=True, mode='json')
    return plan, view, bp, main, _links(knowledge, bp)


def _build(inputs):
    plan, view, bp, main, links = inputs
    return build_writer_context(plan, view, bp, main, knowledge_links=links).ctx['chapters']


def test_setup_has_only_permitted_performance_and_never_contains_secret_answer(pair):
    inputs = _inputs(pair)
    first = _build(inputs)[0]
    answer = inputs[1]['canon']['secrets'][0]['fact']
    assert answer not in json.dumps(first, ensure_ascii=False)
    assert first['facts']['secrets'] == []
    setup = [item for scene in first['scenes'] for item in scene['knowledge'] if item['kind'] == 'setup']
    assert setup and all(item['usage'] == 'setup' for item in setup)
    assert answer not in build_material(first, stage5_book()).given
    CtxChapter.model_validate(first)


def test_scene_reorder_moves_disclosure_from_current_trigger_not_support_list_order(pair):
    inputs = _inputs(pair)
    before = _build(inputs)[1]
    answer = inputs[1]['canon']['secrets'][0]['fact']
    assert before['facts']['secrets'] == []
    assert not any(item['kind'] == 'secret' for item in before['scenes'][0]['knowledge'])
    assert any(item['content'] == answer for item in before['scenes'][1]['knowledge'])
    window = next(row for row in before['disclosure_windows'] if row['subject_ref'] == 'secret_01')
    assert window['allowed_slots'] == [before['scenes'][1]['slot']]
    inputs[0]['output_chapters'][1]['scenes'].reverse()
    after = _build(inputs)[1]
    window = next(row for row in after['disclosure_windows'] if row['subject_ref'] == 'secret_01')
    assert window['allowed_slots'] == [scene['slot'] for scene in after['scenes']]
    assert any(item['content'] == answer for item in after['scenes'][0]['knowledge'])
    CtxChapter.model_validate(after)


def test_early_state_or_context_reference_cannot_bypass_scene_reveal(pair):
    inputs = _inputs(pair)
    second = inputs[0]['output_chapters'][1]
    second['scenes'][0]['context_fact_refs'].append('f02_002')
    with pytest.raises(RefusedByStructure, match='在触发场景'):
        _build(inputs)


@pytest.mark.parametrize('kind', ['因果前提', '要害台词', '字面例外'])
def test_material_fact_reference_cannot_bypass_scene_reveal(pair, kind):
    inputs = _inputs(pair)
    second = inputs[0]['output_chapters'][1]
    hinge = inputs[2]['blueprints'][1]['beats'][1]['hinges'][-1]
    hinge['kind'] = kind
    if kind == '要害台词':
        hinge['dialogue'] = {'speaker': None, 'addressees': None,
                             'meaning': '合成揭示任务', 'speech_act': '告知'}
    elif kind == '字面例外':
        hinge['literal'] = {'kind': 'riddle', 'reason': '合成谜面；此例只检查引用时点'}
    second['scenes'][1]['material_refs'].remove(hinge['hinge_id'])
    second['scenes'][0]['material_refs'].append(hinge['hinge_id'])
    with pytest.raises(RefusedByStructure, match='在触发场景'):
        _build(inputs)


def test_dialogue_only_party_remains_in_writer_character_projection(pair):
    inputs = _inputs(pair)
    chapter = inputs[2]['blueprints'][2]
    for fact in chapter['fact_atoms']:
        fact['participant_char_ids'] = ['char_001']
    for beat in chapter['beats']:
        beat['present_char_ids'] = ['char_001']
    hinge = chapter['beats'][1]['hinges'][0]
    hinge.update(kind='要害台词', dialogue={
        'speaker': {'kind': 'character', 'char_id': 'char_001'},
        'addressees': [{'kind': 'character', 'char_id': 'char_002'}],
        'meaning': '合成通知材料，用于验证不在场的明确对象', 'speech_act': '告知'})
    result = _build(inputs)[2]
    assert 'char_002' in {row['char_id'] for row in result['characters']}
    task = next(item for scene in result['scenes'] for item in scene.get('dialogue', []))
    assert task['addressees'] == ['母亲']


def test_relation_goal_keeps_explicit_character_dependencies(pair):
    inputs = _inputs(pair)
    chapter = inputs[0]['output_chapters'][2]
    relation = inputs[1]['entities']['relations'][0]
    chapter['chapter_goal']['fact_refs'] = [{'kind': 'relation', 'ref': relation['relation_id']}]
    result = _build(inputs)[2]
    ids = {row['char_id'] for row in result['characters']}
    assert {relation['subject_id'], relation['object_id']} <= ids


def test_association_does_not_bypass_the_rule_name_gate(pair):
    registry, knowledge = pair
    changed = knowledge.model_copy(deep=True)
    changed.canon.world_rules[0].versions[0].desc = '母亲不得夜行'
    inputs = _inputs((registry, changed))
    first = _build(inputs)[0]
    assert '母亲不得夜行' not in json.dumps(first, ensure_ascii=False)


@pytest.mark.parametrize('visibility', ['public', 'via_secret'])
def test_relation_world_reader_and_valid_through_boundaries_are_independent(pair, visibility):
    registry, knowledge = pair
    changed = knowledge.model_copy(deep=True)
    relation = changed.entities.relations[0]
    relation.predicate = '共同受雇掌账'
    relation.valid_from_chapter = 1
    relation.valid_through_chapter = 2
    from contracts.schema.stage2 import Relation
    data = relation.model_dump()
    data['visibility'] = ({'kind': 'public', 'from_chapter': 2} if visibility == 'public'
                          else {'kind': 'via_secret', 'secret_id': 'secret_01'})
    changed.entities.relations[0] = Relation.model_validate(data)
    chapters = _build(_inputs((registry, changed)))
    assert relation.predicate not in json.dumps(chapters[0], ensure_ascii=False)
    second = chapters[1]
    assert second['facts']['relations'] == []
    items = [[row for row in scene['knowledge'] if row['kind'] == 'relation']
             for scene in second['scenes']]
    assert bool(items[0]) is (visibility == 'public')
    assert bool(items[1]) is (visibility == 'via_secret')
    assert relation.predicate not in json.dumps(chapters[2], ensure_ascii=False)


def test_character_and_group_knowers_do_not_appear_before_their_current_scene(pair):
    registry, knowledge = pair
    changed = knowledge.model_copy(deep=True)
    secret = changed.canon.secrets[0]
    secret.first_reveal_chapter = 1
    for event in secret.knowledge_events:
        event.known_from_chapter = 2
    chapters = _build(_inputs((registry, changed)))
    second = chapters[1]
    assert second['facts']['secrets'][0]['reader_knows_by_now'] is True
    assert second['facts']['secrets'][0]['known_by'] == []
    event = next(row for row in second['scenes'][0]['knowledge'] if row['kind'] == 'secret')
    assert event['usage'] == 'known'
    assert event['known_by'] == ['李宝珠', '知情的沈家人']


def test_split_secret_members_do_not_release_later_answers_together(pair):
    registry, knowledge = pair
    changed = knowledge.model_copy(deep=True)
    later = changed.canon.secrets[0].model_copy(deep=True)
    later.secret_id = 'secret_02'
    later.fact = '父亲避开家人是为了追查失窃账银'
    later.first_reveal_chapter = 3
    changed.canon.secrets.append(later)
    chapters = _build(_inputs((registry, changed)))
    assert later.fact not in json.dumps(chapters[0], ensure_ascii=False)
    assert later.fact not in json.dumps(chapters[1], ensure_ascii=False)
    assert later.fact in json.dumps(chapters[2], ensure_ascii=False)


def test_setting_is_gated_by_explicit_association_without_relying_on_matching_words(pair):
    registry, knowledge = pair
    changed = knowledge.model_copy(deep=True)
    changed.canon.setting.worldview = '一家人所寻之人其实一直藏在这座城里'
    changed.canon.setting.worldview_evidence.chapter_ordinal = 3
    chapters = _build(_inputs((registry, changed)))
    assert chapters[0]['facts']['setting']['世界观'] is None
    assert changed.canon.setting.worldview not in json.dumps(chapters[0], ensure_ascii=False)
    assert chapters[2]['facts']['setting']['世界观'] == changed.canon.setting.worldview


def test_missing_or_incomplete_association_cannot_masquerade_as_no_secrets(pair):
    inputs = _inputs(pair)
    inputs[4]['links'].pop()
    with pytest.raises(ValueError, match='完整|覆盖'):
        _build(inputs)


def test_writer_renders_each_fact_body_once_and_preserves_window_for_revision(pair):
    inputs = _inputs(pair)
    entry = _build(inputs)[1]
    before = copy.deepcopy(entry)
    material = build_material(entry, stage5_book())
    answer = inputs[1]['canon']['secrets'][0]['fact']
    trigger = inputs[2]['blueprints'][1]['fact_atoms'][-1]['claim']
    assert material.given.count(answer) == 1
    assert material.given.count(trigger) == 1
    assert '场景披露许可' in material.given and '正文仍整章输出' in material.given
    assert 'secret_01' not in material.given and 'f02_002' not in material.given
    assert '场景披露许可' in revise_user(material, '上一版正文。', ['修复披露时点'])
    assert entry == before
