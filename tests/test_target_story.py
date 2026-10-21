"""新版目标模型：保真、人物因果、章边界、源盲投影与真实写手材料。"""
from __future__ import annotations

from copy import deepcopy
from tests.story_facilities import target_wires, compiled_case
import json
from types import SimpleNamespace as NS

import pytest
from pydantic import ValidationError

from contracts.schema.target_story import (
    ChapterPacket, ChapterPlan, ChapterPlanWire, HookDesignWire, PreserveWire,
    RequirementWire, StoryState, TargetEventWire, TargetModelWire, TargetStory,
)
from novel.stages.stage4.story import (
    build_target_model_prompt, compile_chapter, compile_hook, compile_naming,
    compile_preserve, compile_target_model, compile_target_story,
    preservation_brief, project_chapter_packet, project_hook_packet,
    TargetStoryIssue,
    TargetCompileError,
)






def test_target_story_reaches_writer_and_hook_with_target_rules():
    _, _, _, model, story, adaptation, hook_wire = compiled_case()
    assert sum(c.length_budget for c in model.chapters) == 2001
    assert [e.status for e in adaptation.entries] == ['realized', 'realized']
    packet = project_chapter_packet(story, 2)
    assert packet.chapter_ordinal == 2 and packet.source_chapter_count == 2
    assert packet.names == {'person_a': '林舟', 'person_b': '陆梨'}
    assert any('使者当面交付' in s for s in packet.must_respect)
    assert all('信件必须亲手交接' not in s for s in packet.must_respect)
    assert any('陆梨撤回条件' in s for s in packet.must_realize)
    assert any('入场：陆梨带新条件追上林舟' in s for s in packet.continuity)
    assert len([s for s in packet.entry_state if '意图' in s]) == 2
    hook = compile_hook(hook_wire, story, length_budget=300)
    hook_packet = project_hook_packet(story, hook)
    assert hook_packet.length_budget == 300
    assert any('使者当面交付' in s for s in hook_packet.must_respect)


def test_target_model_input_is_source_blind_and_preserves_decisions():
    _, names, preservation, _, _, adaptation, _ = compiled_case()
    brief = preservation_brief(preservation)
    assert 'source_bindings' not in brief and 'world_rule_decisions' not in brief
    prompt = build_target_model_prompt(preservation, names, source_chapter_count=2,
                                       total_length_budget=2001)
    assert 'source_event_1' not in prompt and 'source_rule_a' not in prompt
    assert adaptation.world_rule_decisions[0].action == 'adapt'
    assert adaptation.world_rule_decisions[0].reason


def test_initial_knowledge_survives_as_entry_permission():
    *_, story, _adaptation, _hook = compiled_case()
    packet = project_chapter_packet(story, 1)
    assert any('陆梨已确认：密信已经交付' in s for s in packet.must_respect)
    assert not any('林舟已确认' in s for s in packet.must_respect)


def test_false_belief_is_legal_but_known_refuted_fact_is_rejected():
    source, naming, preserve, model, _, _ = target_wires()
    names = compile_naming(naming, source)
    contract = compile_preserve(preserve, source, names)
    model['propositions'][0]['status'] = 'refuted'
    model['initial_knowledge'][0]['mode'] = 'believed'
    model['events'][0]['knowledge_reads'][0]['mode'] = 'believed'
    model['events'][1]['knowledge_changes'][0]['mode'] = 'refuted'
    compile_target_model(model, contract, source_chapter_count=2, total_length_budget=2001,
                         name_map=names)
    model['events'][1]['knowledge_changes'][0]['mode'] = 'known'
    with pytest.raises(ValueError, match='确知'):
        compile_target_model(model, contract, source_chapter_count=2, total_length_budget=2001,
                             name_map=names)


def test_local_expansion_cannot_create_cross_chapter_facts_or_reorder_events():
    _, _, _, model, *_ = compiled_case()
    _, _, _, _, plans, _ = target_wires()
    plans[0]['cross_boundary_changes'] = [
        {'subject_ids': ['person_a'], 'dimension': '秘密', 'value': '另有身份', 'op': 'set', 'modality': 'actual'}]
    with pytest.raises(ValueError, match='跨章事实'):
        compile_chapter(plans[0], model, chapter_ordinal=1)
    plans[0]['cross_boundary_changes'] = []
    plans[0]['scenes'][0]['actions'][0]['event_no'] = 2
    with pytest.raises(ValueError, match='越界'):
        compile_chapter(plans[0], model, chapter_ordinal=1)


def test_joint_cause_time_cycle_is_rejected():
    source, naming, preserve, model, _, _ = target_wires()
    names = compile_naming(naming, source)
    contract = compile_preserve(preserve, source, names)
    model['events'][0]['after'] = [2]
    model['events'][1]['after'] = []
    with pytest.raises(ValueError, match='联合图.*环'):
        compile_target_model(model, contract, source_chapter_count=2, total_length_budget=2001,
                             name_map=names)


def test_flashback_does_not_overwrite_present_state_but_reader_can_learn():
    source, naming, preserve, model, _, _ = target_wires()
    names = compile_naming(naming, source)
    contract = compile_preserve(preserve, source, names)
    model['events'][1]['temporal_frame'] = 'past'
    model['events'][1]['state_changes'] = [
        {'subject_ids': ['person_a'], 'dimension': '意图', 'qualifier': '身份', 'value': '公开',
         'op': 'set', 'modality': 'actual'}]
    model['events'][1]['knowledge_changes'] = [
        {'holder': 'reader', 'proposition_no': 1, 'mode': 'known'}]
    result = compile_target_model(model, contract, source_chapter_count=2,
                                  total_length_budget=2001, name_map=names)
    assert next(s.value for s in result.chapters[1].exit_state if s.qualifier == '身份') == '保密'
    assert any(k.holder == 'reader' for k in result.chapters[1].exit_knowledge)


def test_required_coverage_and_dependency_checked_at_final_publish():
    _, names, preservation, model, story, _, _ = compiled_case()
    changed = model.model_copy(deep=True)
    changed.events[1].realizes = ()
    with pytest.raises(ValueError, match='rq0002.*没有目标承载'):
        compile_target_story(changed, story.chapter_plans, preservation, names)
    with pytest.raises(ValueError, match='全部章节'):
        compile_target_story(model, story.chapter_plans[:1], preservation, names)


def test_world_rule_omit_cannot_delete_required_mechanism():
    source, naming, preserve, *_ = target_wires()
    names = compile_naming(naming, source)
    preserve['requirements'][0]['source_refs'].append('source_rule_a')
    preserve['world_rules'][0] = {'rule_ref': 'source_rule_a', 'action': 'omit', 'reason': '删除背景'}
    with pytest.raises(ValueError, match='必保规则.*omit'):
        compile_preserve(preserve, source, names)


def test_kept_rule_is_reexpressed_by_preserve_with_target_names():
    source, naming, preserve, model_wire, plans, _ = target_wires()
    source.world_rules['source_rule_a'].statement = '沈旧交接的信件必须由本人签收'
    names = compile_naming(naming, source)
    preserve['world_rules'][0] = {'rule_ref': 'source_rule_a', 'action': 'keep'}
    with pytest.raises(ValidationError, match='target_statement'):
        compile_preserve(preserve, source, names)
    preserve['world_rules'][0]['target_statement'] = '林舟交接的信件必须由本人签收'
    for plan in plans:
        plan['writer_context']['world_rules'][0]['text'] = '林舟交接的信件必须由本人签收'
    contract = compile_preserve(preserve, source, names)
    assert contract.world_rules[0].statement == '林舟交接的信件必须由本人签收'
    assert '沈旧' not in json.dumps(preservation_brief(contract), ensure_ascii=False)
    model = compile_target_model(model_wire, contract, source_chapter_count=2,
                                 total_length_budget=2001, name_map=names)
    chapter_plans = [compile_chapter(wire, model, chapter_ordinal=i) for i, wire in enumerate(plans, 1)]
    story, _ = compile_target_story(model, chapter_plans, contract, names)
    packet = project_chapter_packet(story, 1)
    assert '沈旧' not in json.dumps(packet.model_dump(mode='json'), ensure_ascii=False)
    assert any('林舟交接的信件' in row for row in packet.must_respect)


def test_local_consistency_and_ambiguity_are_explicit_and_audited():
    source, naming, preserve, *_ = target_wires()
    source.conflicts = (NS(statement='时间说法矛盾'),)
    names = compile_naming(naming, source)
    with pytest.raises(ValueError, match='每条源冲突'):
        compile_preserve(preserve, source, names)
    preserve['consistency_decisions'] = [{'conflict_no': 1, 'action': 'local_consistency',
        'explanation': '只统一往返所需时间', 'interpretation': '同日傍晚返回',
        'protected_outcomes': ['不改变拒绝与再接受的顺序']}]
    result = compile_preserve(preserve, source, names)
    assert result.consistency_decisions[0].interpretation == '同日傍晚返回'
    assert 'conflict_no' not in preservation_brief(result)['consistency_decisions'][0]


def test_future_obligation_satisfaction_is_not_sent_to_supporting_chapter():
    _, names, preservation, model, story, _, _ = compiled_case()
    model.events[0].realizes = ('rq0001', 'rq0002')
    story, _ = compile_target_story(model, story.chapter_plans, preservation, names)
    first = project_chapter_packet(story, 1)
    assert not any('林舟改为接受有限合作' in text for text in first.must_realize)


def test_hook_cannot_claim_unselected_knowledge_and_inputs_are_immutable():
    _, _, _, _, story, _, wire = compiled_case()
    before = story.model_dump(mode='json')
    wire['allowed_knowledge'] = [{'holder': 'person_a', 'proposition_no': 1, 'mode': 'known'}]
    with pytest.raises(ValueError, match='知情授权'):
        compile_hook(wire, story, length_budget=300)
    project_chapter_packet(story, 1)
    assert story.model_dump(mode='json') == before


@pytest.mark.parametrize('model', [PreserveWire, TargetModelWire, ChapterPlanWire, HookDesignWire])
def test_new_wire_rejects_unknown_and_old_protocol_fields(model):
    with pytest.raises(ValidationError):
        model.model_validate({'protocol_version': 'stage4-design-v12', 'source_refs': []})


def test_reference_positions_reject_boolean_coercion():
    *_, wire = target_wires()
    wire['beats'][0]['event_no'] = True
    with pytest.raises(ValidationError):
        HookDesignWire.model_validate(wire)


def test_late_name_release_and_hook_permission_do_not_reveal_identity_early():
    source, naming, preserve, model, plans, hook = target_wires()
    names = compile_naming(naming, source)
    contract = compile_preserve(preserve, source, names)
    model['initial_displays']['person_a'] = '黑衣人'
    model['events'][1]['name_releases'] = {'person_a': '林舟'}
    # 当章语义owner使用本章许可称谓表达；compiler不能从裸文本猜指称。
    plans[0]['scenes'][0]['actions'][0]['choice'] = '黑衣人拒绝受控'
    plans[0]['scenes'][0]['actions'][0]['action'] = '陆梨提出要求，黑衣人拒绝'
    plans[0]['writer_context']['exit'] = '黑衣人离席，陆梨考虑退让'
    plans[0]['writer_context']['requirements'][0]['text'] = '黑衣人听清条件后明确拒绝，不写成误听或巧合'
    plans[1]['writer_context']['entry'] = '陆梨带新条件追上黑衣人'
    hook['beats'][0]['choice'] = '黑衣人拒绝受控'
    hook['beats'][0]['action'] = '陆梨提出要求，黑衣人拒绝'
    hook['ending'] = '停在黑衣人离席'
    draft = compile_target_model(model, contract, source_chapter_count=2,
                                  total_length_budget=2001, name_map=names)
    compiled_plans = [compile_chapter(wire, draft, chapter_ordinal=i) for i, wire in enumerate(plans, 1)]
    story, _ = compile_target_story(draft, compiled_plans, contract, names)
    first, second = project_chapter_packet(story, 1), project_chapter_packet(story, 2)
    assert first.names['person_a'] == '黑衣人'
    assert '林舟' not in json.dumps(first.model_dump(mode='json'), ensure_ascii=False)
    assert second.names['person_a'] == '林舟'
    assert any('在此处正式揭示为：林舟' in text for text in second.must_realize)
    with pytest.raises(ValueError, match='显示称谓未.*授权'):
        compile_hook(hook, story, length_budget=300)
    hook['allowed_displays']['person_a'] = '黑衣人'
    packet = project_hook_packet(story, compile_hook(hook, story, length_budget=300))
    assert '林舟' not in json.dumps(packet.model_dump(mode='json'), ensure_ascii=False)


def test_short_name_does_not_rewrite_ordinary_words_in_real_writer_prompts():
    from novel.core.story_prose import build_body_request, build_hook_request
    source, naming, preserve, model, plans, hook = target_wires()
    source.entities['person_a'].name.aliases['al01'] = '阿旧'
    source.entities['person_a'].name.surface_forms['阿旧'] = '{alias:al01}'
    source.entities['person_a'].surfaces += (NS(literal='阿旧'),)
    naming['entries'][0]['aliases'] = {'al01': '安'}
    names = compile_naming(naming, source)
    model['initial_displays']['person_a'] = '黑衣人'
    model['events'][1]['name_releases'] = {'person_a': '安'}
    action = plans[0]['scenes'][0]['actions'][0]
    action['choice'] = '黑衣人拒绝受控'
    action['action'] = '黑衣人说起家中平安，再明确拒绝陆梨的要求'
    plans[0]['writer_context']['exit'] = '黑衣人离席，陆梨考虑退让'
    plans[0]['writer_context']['requirements'][0]['text'] = '黑衣人听清条件后明确拒绝'
    hook['beats'][0]['choice'] = '黑衣人拒绝受控'
    hook['beats'][0]['action'] = '黑衣人说起家中平安，再拒绝陆梨的要求'
    hook['ending'] = '停在黑衣人离席'
    hook['allowed_displays']['person_a'] = '黑衣人'
    contract = compile_preserve(preserve, source, names)
    draft = compile_target_model(model, contract, source_chapter_count=2,
                                  total_length_budget=2001, name_map=names)
    compiled = [compile_chapter(row, draft, chapter_ordinal=i) for i, row in enumerate(plans, 1)]
    story, _ = compile_target_story(draft, compiled, contract, names)
    body_prompt = build_body_request(project_chapter_packet(story, 1)).user
    hook_prompt = build_hook_request(project_hook_packet(story, compile_hook(hook, story, length_budget=300))).user
    for prompt in (body_prompt, hook_prompt):
        assert '家中平安' in prompt and '家中平黑衣人' not in prompt


def test_future_secret_in_global_requirement_never_enters_early_writer_material():
    from novel.core.story_prose import build_body_request, build_hook_request
    source, naming, preserve, model, plans, hook = target_wires()
    secret = '陆梨其实是北海暗卫统领，真名赵玄'
    preserve['requirements'][1]['constraints'] = [secret]
    model['events'][0]['realizes'].append('rq0002')
    names = compile_naming(naming, source)
    contract = compile_preserve(preserve, source, names)
    draft = compile_target_model(model, contract, source_chapter_count=2,
                                  total_length_budget=2001, name_map=names)
    compiled = [compile_chapter(row, draft, chapter_ordinal=i) for i, row in enumerate(plans, 1)]
    story, _ = compile_target_story(draft, compiled, contract, names)
    assert secret in story.preservation[1].constraints
    first = project_chapter_packet(story, 1)
    prompts = (build_body_request(first).user,
        build_hook_request(project_hook_packet(story, compile_hook(hook, story, length_budget=300))).user)
    assert all(secret not in prompt and '赵玄' not in prompt for prompt in prompts)


def test_source_extraction_issue_routes_to_precise_owner_instead_of_preserve_rewrite():
    from contracts.schema.source_story import StoryEvent, StoryProposition, StoryTemporal
    source, naming, preserve, *_ = target_wires()
    source.events['source_event_1'] = StoryEvent(chapter_ordinal=1, order=1, summary='林舟拒绝合作',
        participants=('person_a',), evidence_refs=('e1',), temporal=StoryTemporal(frame='current', anchor_event=None),
        scene_boundary='new')
    source.propositions['source_prop_1'] = StoryProposition(statement='合作条件没有改变',
        entity_refs=('person_b',), world_status='established', evidence_refs=('e1',))
    names = compile_naming(naming, source)
    preserve['source_issues'] = [{'source_ref': 'source_event_1', 'kind': 'missing',
        'path': ['participants'], 'operation': 'append',
        'statement': '漏记目睹者', 'evidence_refs': ['e1']}]
    with pytest.raises(TargetStoryIssue) as caught:
        compile_preserve(preserve, source, names)
    assert (caught.value.owner, caught.value.node, caught.value.item) == (
        'ch01', 'str06.stage2.chapters', 'source_event_1')
    preserve['source_issues'][0]['evidence_refs'].append('e2')
    with pytest.raises(TargetStoryIssue) as caught:
        compile_preserve(preserve, source, names)
    assert caught.value.owner == 'ch01'  # 证据跨章不改变正式字段writer。
    preserve['source_issues'][0].update(source_ref='source_prop_1', path=['world_status'],
        operation='replace', evidence_refs=['e1'])
    with pytest.raises(TargetStoryIssue) as caught:
        compile_preserve(preserve, source, names)
    assert caught.value.owner == 'book'  # 单章证据的全书命题仍归book。
    preserve['source_issues'][0]['path'] = ['不存在的自然字段']
    with pytest.raises(TargetCompileError) as caught:
        compile_preserve(preserve, source, names)
    assert caught.value.issues[0].path == ('source_issues', 0, 'path')


def test_cross_boundary_issue_has_canonical_target_owner_and_item():
    _, _, _, model, *_ = compiled_case()
    _, _, _, _, plans, _ = target_wires()
    plans[0]['cross_boundary_changes'] = [
        {'subject_ids': ['person_a'], 'dimension': '关系', 'value': '另有亲属', 'op': 'set', 'modality': 'actual'}]
    with pytest.raises(TargetStoryIssue) as caught:
        compile_chapter(plans[0], model, chapter_ordinal=1)
    assert caught.value.as_issue()['node'] == 'str06.stage4.body_design'
    assert caught.value.item == 'chapter:1'


def test_canonical_reload_rejects_missing_version_incomplete_plan_and_forged_fold():
    *_, story, _adaptation, _hook = compiled_case()
    payload = story.model_dump(mode='json')
    del payload['contract_version']
    with pytest.raises(ValidationError):
        TargetStory.model_validate(payload)
    payload = story.model_dump(mode='json')
    payload['chapter_plans'].pop()
    with pytest.raises(ValidationError, match='全部有序ChapterPlan'):
        TargetStory.model_validate(payload)
    payload = story.model_dump(mode='json')
    payload['chapters'][1]['entry_state'][0]['value'] = '未经事件建立的状态'
    with pytest.raises(ValidationError, match='入场边界'):
        TargetStory.model_validate(payload)
    packet = project_chapter_packet(story, 1).model_dump(mode='json')
    packet['min_chars'] = packet['max_chars'] + 1
    with pytest.raises(ValidationError, match='篇幅'):
        ChapterPacket.model_validate(packet)


def test_target_state_unset_and_unknown_are_distinct_from_missing_or_past():
    source, naming, preserve, model, _plans, _hook = target_wires()
    names = compile_naming(naming, source)
    contract = compile_preserve(preserve, source, names)
    model['events'][1]['state_changes'] = [
        {'subject_ids': ['person_a'], 'dimension': '意图', 'qualifier': '家计',
         'op': 'unset', 'value': None, 'modality': 'actual'},
        {'subject_ids': ['person_a'], 'dimension': '意图', 'qualifier': '身份',
         'op': 'unknown', 'value': None, 'modality': 'actual'}]
    draft = compile_target_model(model, contract, source_chapter_count=2,
                                  total_length_budget=2001, name_map=names)
    assert not any(s.qualifier == '家计' for s in draft.chapters[1].exit_state)
    unknown = next(s for s in draft.chapters[1].exit_state if s.qualifier == '身份')
    assert unknown.op == 'unknown' and unknown.value is None


def test_planned_state_cannot_overwrite_actual_or_unset_another_modality():
    from contracts.schema.target_story import TargetModelDraft
    source, naming, preserve, model, _plans, _hook = target_wires()
    names = compile_naming(naming, source)
    contract = compile_preserve(preserve, source, names)
    model['initial_state'].append({'subject_ids': ['person_a'], 'dimension': '位置',
                                    'value': '屋内', 'modality': 'actual', 'op': 'set'})
    model['events'][0].setdefault('state_changes', []).append({'subject_ids': ['person_a'], 'dimension': '位置',
                                                             'value': '集市', 'modality': 'planned', 'op': 'set'})
    model['events'][1]['state_changes'].append({'subject_ids': ['person_a'], 'dimension': '位置',
        'value': None, 'modality': 'planned', 'op': 'unset'})
    draft = compile_target_model(model, contract, source_chapter_count=2,
                                  total_length_budget=2001, name_map=names)
    first = {s.modality: s.value for s in draft.chapters[0].exit_state if s.dimension == '位置'}
    final = {s.modality: s.value for s in draft.chapters[1].exit_state if s.dimension == '位置'}
    assert first == {'actual': '屋内', 'planned': '集市'}
    assert final == {'actual': '屋内'}
    assert TargetModelDraft.model_validate(draft.model_dump(mode='json')) == draft


def test_semantic_omissions_do_not_default_to_current_actual_or_legal_empty():
    _, _, preserve, model, plans, _ = target_wires()
    cases = (
        (TargetEventWire, model['events'][0], ('temporal_frame', 'requires', 'after', 'realizes',
            'state_changes', 'knowledge_reads', 'knowledge_changes', 'name_releases')),
        (StoryState, model['initial_state'][0], ('op', 'modality')),
        (RequirementWire, preserve['requirements'][0], ('participants', 'requires', 'constraints', 'details')),
        (PreserveWire, preserve, ('source_issues',)),
        (ChapterPlanWire, plans[0], ('cross_boundary_changes', 'model_issues', 'writer_context')),
    )
    for schema, complete, fields in cases:
        schema.model_validate(complete)
        for field in fields:
            missing = deepcopy(complete)
            del missing[field]
            with pytest.raises(ValidationError) as caught:
                schema.model_validate(missing)
            assert any(row['loc'] == (field,) and row['type'] == 'missing' for row in caught.value.errors())


def test_semantic_reference_error_locates_narrow_editable_event():
    source, naming, preserve, model, _plans, _hook = target_wires()
    names = compile_naming(naming, source)
    contract = compile_preserve(preserve, source, names)
    model['events'][1]['requires'] = [99]
    with pytest.raises(TargetCompileError) as caught:
        compile_target_model(model, contract, source_chapter_count=2,
                             total_length_budget=2001, name_map=names)
    issue, = caught.value.issues
    assert issue.path == ('events', 1, 'requires')
    assert issue.code == 'reference_out_of_range'


def test_hook_cannot_omit_explicit_reader_release_from_selected_event():
    _, _, _, _, story, _, wire = compiled_case()
    payload = story.model_dump(mode='json')
    # 正式模型加入一项已明确授权的读者确认，再由真实compiler重建章边界。
    source, naming, preserve, model_wire, plans, _ = target_wires()
    model_wire['events'][0]['knowledge_changes'].append(
        {'holder': 'reader', 'proposition_no': 1, 'mode': 'known'})
    names = compile_naming(naming, source)
    contract = compile_preserve(preserve, source, names)
    model = compile_target_model(model_wire, contract, source_chapter_count=2,
                                 total_length_budget=2001, name_map=names)
    story, _ = compile_target_story(model, [compile_chapter(row, model, chapter_ordinal=i)
                                            for i, row in enumerate(plans, 1)], contract, names)
    with pytest.raises(TargetCompileError, match='读者披露'):
        compile_hook(wire, story, length_budget=300)


def test_chapter_missing_cause_returns_model_owner_without_creating_state():
    _, _, _, model, *_ = compiled_case()
    _, _, _, _, plans, _ = target_wires()
    plans[0]['model_issues'] = [{'event_no': 1, 'kind': 'missing_cause', 'component': 'trigger',
        'operation': 'replace', 'item_no': None, 'statement': '缺少拒绝合作的必要触发'}]
    with pytest.raises(TargetStoryIssue) as caught:
        compile_chapter(plans[0], model, chapter_ordinal=1)
    assert caught.value.node == 'str06.stage4.body_design'
    assert caught.value.item == model.chapters[0].event_refs[0]


@pytest.mark.parametrize('changes', [
    {'event_no': None}, {'operation': 'replace'}, {'item_no': True},
    {'item_no': 1}, {'component': 'not_a_component'},
])
def test_model_issue_requires_explicit_valid_component_operation_and_position(changes):
    from contracts.schema.target_story import TargetModelIssue
    data = {'event_no': 1, 'kind': 'missing_knowledge', 'component': 'knowledge_reads',
            'operation': 'append', 'item_no': None, 'statement': '需要明确知情前提'}
    TargetModelIssue.model_validate(data)
    with pytest.raises(ValidationError):
        TargetModelIssue.model_validate({**data, **changes})
