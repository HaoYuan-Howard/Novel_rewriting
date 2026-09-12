# -*- coding: utf-8 -*-
"""当前知识决定 → 显式场景端点 → 章级 WriterContext。

保留旧链的披露、关系有效期、叫法、缺失关联和去重反例；V9 使用完整节点、
目标及知识事件引用，不再通过 hinge/fact 分配推定自由文本的语义覆盖。
"""
from __future__ import annotations

import copy
import json

import pytest

from contracts.schema.knowledge_links import (
    KnowledgeAtEntry, KnowledgeAtBeats, KnowledgeLinkHistory, KnowledgeLinks,
    KnowledgeLinksProvenance,
)
from contracts.schema.stage4 import CtxChapter
from novel.errors import RefusedByStructure
from novel.stages.stage2 import grounding, knowledge_links
from novel.stages.stage4.context import build_writer_context
from novel.stages.stage4.measure import identity_plan
from novel.stages.stage5.prompts import build_material, revise_user
from tests.factories import stage5_book
from tests.stage2_fixtures import plot_blueprints as blueprints
from tests.test_stage2 import main_text
from tests.test_stage2_grounding import character_preparation, pair  # noqa: F401
from tests.test_stage2_knowledge_links import _scope, _submission


def _links(knowledge, bp):
    scope = _scope(knowledge, bp)
    submission = _submission(scope)
    updated = []
    for decision in submission.decisions:
        row = next(row for row in scope.obligations if row.obligation_id == decision.obligation_id)
        if row.kind == 'secret_reveal':
            # 多条支持证据，明确选择后场触发；不根据支持数组顺序猜时点。
            refs = [ref for ref in row.candidate_subject_refs if ref.kind == 'beat']
            resolution = KnowledgeAtBeats(kind='at_beats', subject_refs=list(reversed(refs)),
                                         trigger_beat_ref=refs[-1].ref)
        elif row.kind in {'setting', 'world_rule', 'relation'}:
            resolution = KnowledgeAtEntry(kind='at_entry', subject_refs=[row.entry_subject_refs[0]])
        else:
            resolution = decision.resolution
        updated.append(decision.model_copy(update={'resolution': resolution}))
    submission.decisions = updated
    history = [KnowledgeLinkHistory(decision_sha256='d' * 64, accepted=True, submission=submission)]
    return KnowledgeLinks(scope=scope, links=knowledge_links.require_complete(scope, history),
        _provenance=KnowledgeLinksProvenance(code='disclosure-fixture',
            root_clean_sha1=knowledge.provenance.root_clean_sha1, inputs={},
            decision_sha256='d' * 64)).model_dump(by_alias=True, mode='json')


def _inputs(pair, *, subjects=()):
    registry, knowledge = pair
    bp, main = copy.deepcopy(blueprints()), main_text()
    plan = identity_plan(bp, main)
    # 当前上下文只消费必要主体。显式提出保留未知的目标，测试后章/隐藏主体的投影。
    for chapter in plan['output_chapters']:
        chapter['chapter_goal']['subject_refs'] += [
            {'kind': kind, 'ref': ref} for kind, ref in subjects]
        chapter['chapter_goal']['intent'] = 'withhold'
    view = grounding.compose(registry, knowledge).model_dump(by_alias=True, mode='json')
    return plan, view, bp, main, _links(knowledge, bp)


def _build(inputs):
    plan, view, bp, main, links = inputs
    return build_writer_context(plan, view, bp, main, knowledge_links=links).ctx['chapters']


def _global(entry, kind, ref):
    return next((row for row in entry['global_subjects']
                 if row['subject_ref'] == {'kind': kind, 'ref': ref}), None)


def _event(entry, kind, ref):
    return next(row for row in entry['knowledge_events']
                if row['kind'] == kind and row['subject_ref']['ref'] == ref)


def test_setup_has_only_permitted_performance_and_never_contains_secret_answer(pair):
    inputs = _inputs(pair)
    first = _build(inputs)[0]
    answer = inputs[1]['canon']['secrets'][0]['fact']
    assert answer not in json.dumps(first, ensure_ascii=False)
    assert _global(first, 'secret', 'secret_01') is None
    assert _event(first, 'secret_setup', 'secret_01')['allowed_slots']
    assert answer not in build_material(first, stage5_book()).given
    CtxChapter.model_validate(first)


def test_scene_reorder_moves_disclosure_from_current_trigger_not_support_list_order(pair):
    inputs = _inputs(pair)
    before = _build(inputs)[1]
    assert _global(before, 'secret', 'secret_01')['allowed_slots'] == ['s02']
    assert _event(before, 'secret_reveal', 'secret_01')['slot'] == 's02'
    chapter = inputs[0]['output_chapters'][1]
    chapter['scenes'].reverse()
    chapter['scenes'][0]['transition'] = 'start'
    chapter['scenes'][1]['transition'] = 'continue'
    chapter['open_hook_goal'].update(anchor_scene_ref='s02', anchor_beat_ref='b02_02')
    chapter['end_hook_goal'].update(anchor_scene_ref='s01', anchor_beat_ref='b02_01')
    after = _build(inputs)[1]
    assert _global(after, 'secret', 'secret_01')['allowed_slots'] == ['s02', 's01']
    CtxChapter.model_validate(after)


def test_early_state_or_context_reference_cannot_bypass_scene_reveal(pair):
    inputs = _inputs(pair)
    inputs[0]['output_chapters'][1]['scenes'][0]['context_beat_refs'].append('b02_02')
    with pytest.raises(RefusedByStructure, match='完整节点作为已知背景'):
        _build(inputs)


@pytest.mark.parametrize('goal_name', ['chapter_goal', 'open_hook_goal', 'end_hook_goal'])
def test_goal_subject_reference_cannot_bypass_scene_reveal(pair, goal_name):
    inputs = _inputs(pair)
    chapter = inputs[0]['output_chapters'][1]
    # 结尾目标仍位于末场；把秘密触发点移到其后的阅读场景不合法。
    goal = chapter[goal_name]
    goal.update(subject_refs=[{'kind': 'secret', 'ref': 'secret_01'}],
                intent='advance', anchor_beat_ref='b02_01', anchor_scene_ref='s01')
    with pytest.raises(RefusedByStructure, match='披露许可|结尾目标必须落在末场'):
        _build(inputs)


def test_dialogue_only_party_remains_in_writer_character_projection(pair):
    inputs = _inputs(pair)
    chapter = inputs[2]['blueprints'][2]
    for beat in chapter['beats']:
        beat['participant_char_ids'] = ['char_001']
    # V9 对白含义在完整正文，收信人/被提及者也是参与者，但不是实际在场者。
    beat = chapter['beats'][0]
    beat['content'] = '李宝珠托管家向母亲传话，询问家中账目。'
    beat['participant_char_ids'].append('char_002')
    assert all(not b['source_contexts'] for b in chapter['beats'])
    result = _build(inputs)[2]
    assert 'char_002' in {row['char_id'] for row in result['characters']}
    assert result['beats'][0]['content'] == beat['content']


def test_relation_goal_keeps_explicit_character_dependencies(pair):
    inputs = _inputs(pair, subjects=[('relation', 'rel_01')])
    result = _build(inputs)[2]
    relation = inputs[1]['entities']['relations'][0]
    assert {relation['subject_id'], relation['object_id']} <= {
        row['char_id'] for row in result['characters']}


def test_association_does_not_bypass_the_rule_name_gate(pair):
    registry, knowledge = pair
    changed = knowledge.model_copy(deep=True)
    changed.canon.world_rules[0].versions[0].desc = '母亲不得夜行'
    inputs = _inputs((registry, changed), subjects=[('world_rule', 'wr_01')])
    chapters = _build(inputs)
    assert '母亲不得夜行' not in json.dumps(chapters[0], ensure_ascii=False)
    assert '母亲不得夜行' in json.dumps(chapters[1], ensure_ascii=False)


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
    chapters = _build(_inputs((registry, changed), subjects=[('relation', 'rel_01')]))
    assert relation.predicate not in json.dumps(chapters[0], ensure_ascii=False)
    assert _global(chapters[1], 'relation', 'rel_01')['allowed_slots'] == (
        ['s01', 's02'] if visibility == 'public' else ['s02'])
    assert _global(chapters[2], 'relation', 'rel_01') is None


def test_character_and_group_knowers_do_not_appear_before_their_current_scene(pair):
    registry, knowledge = pair
    changed = knowledge.model_copy(deep=True)
    secret = changed.canon.secrets[0]
    secret.first_reveal_chapter = 1
    for event in secret.knowledge_events:
        event.known_from_chapter = 2
    chapters = _build(_inputs((registry, changed), subjects=[('secret', 'secret_01')]))
    assert not any(e['kind'] == 'knowledge_event' for e in chapters[0]['knowledge_events'])
    second = chapters[1]
    assert _global(second, 'secret', 'secret_01')['allowed_slots'] == ['s01', 's02']
    events = [e for e in second['knowledge_events'] if e['kind'] == 'knowledge_event']
    assert {e['audience']['kind'] for e in events} == {'character', 'group'}
    assert all(e['timing'] == 'at_slot' and e['slot'] == 's01' for e in events)


def test_split_secret_members_do_not_release_later_answers_together(pair):
    registry, knowledge = pair
    changed = knowledge.model_copy(deep=True)
    later = changed.canon.secrets[0].model_copy(deep=True)
    later.secret_id = 'secret_02'
    later.fact = '父亲避开家人是为了追查失窃账银'
    later.first_reveal_chapter = 3
    changed.canon.secrets.append(later)
    chapters = _build(_inputs((registry, changed), subjects=[('secret', 'secret_02')]))
    assert later.fact not in json.dumps(chapters[0], ensure_ascii=False)
    assert later.fact not in json.dumps(chapters[1], ensure_ascii=False)
    assert later.fact in json.dumps(chapters[2], ensure_ascii=False)


def test_setting_is_gated_by_explicit_association_without_relying_on_matching_words(pair):
    registry, knowledge = pair
    changed = knowledge.model_copy(deep=True)
    changed.canon.setting.worldview = '一家人所寻之人其实一直藏在这座城里'
    changed.canon.setting.worldview_evidence.chapter_ordinal = 3
    chapters = _build(_inputs((registry, changed), subjects=[('setting', '世界观')]))
    assert _global(chapters[0], 'setting', '世界观') is None
    assert changed.canon.setting.worldview not in json.dumps(chapters[0], ensure_ascii=False)
    assert _global(chapters[2], 'setting', '世界观')['value'] == changed.canon.setting.worldview


def test_rule_replacement_does_not_keep_old_version_as_current_or_reveal_new_early(pair):
    registry, knowledge = pair
    changed = knowledge.model_copy(deep=True)
    rule = changed.canon.world_rules[0]
    later = rule.versions[0].model_copy(deep=True)
    later.effective_from_chapter = 2
    later.desc = '城中宵禁已经解除，夜间可以出门。'
    later.desc_evidence.chapter_ordinal = 3
    rule.versions.append(later)
    chapters = _build(_inputs((registry, changed), subjects=[('world_rule', 'wr_01')]))
    assert _global(chapters[0], 'world_rule', 'wr_01')['value']['versions'][0]['desc'] == rule.versions[0].desc
    assert _global(chapters[1], 'world_rule', 'wr_01') is None
    assert later.desc not in json.dumps(chapters[1], ensure_ascii=False)
    assert [v['desc'] for v in _global(chapters[2], 'world_rule', 'wr_01')['value']['versions']] == [later.desc]


def test_missing_or_incomplete_association_cannot_masquerade_as_no_secrets(pair):
    inputs = _inputs(pair)
    assert _build(inputs), '完整关联是当前消费链的绿对照'
    inputs[4]['links'].pop()
    # KL2 自身先拒绝缺事件；无需等 WriterContext 的后置结构断言才发现。
    from pydantic import ValidationError
    with pytest.raises(ValidationError, match='完整|覆盖'):
        KnowledgeLinks.model_validate(inputs[4])
    with pytest.raises(ValidationError, match='完整|覆盖'):
        _build(inputs)


def test_writer_renders_each_beat_body_once_and_preserves_permission_for_revision(pair):
    inputs = _inputs(pair)
    entry = _build(inputs)[1]
    before = copy.deepcopy(entry)
    material = build_material(entry, stage5_book())
    answer = inputs[1]['canon']['secrets'][0]['fact']
    trigger = inputs[2]['blueprints'][1]['beats'][-1]['content']
    assert material.given.count(answer) == 1
    assert material.given.count(trigger) == 1
    assert '逐场表达许可' in material.given and '同次请求可见后场材料' in material.given
    assert '逐场表达许可' in revise_user(material, '上一版正文。', ['修复披露时点'])
    assert entry == before
