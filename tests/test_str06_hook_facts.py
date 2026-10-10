"""Hook v3：事实有效区间、组织声明、真实后续依据与上下文边界的反例。"""
from copy import deepcopy
import json

import pytest

from contracts.schema.design import BodyDesign, TargetDelta
from novel.stages.stage4.hook import assemble_hook_design, hook_patch_slice, hook_patch_legal_values, publish_graph
from novel.stages.stage4.hook_context import compile_hook_context
from novel.stages.stage4.design_paths import locate_design_patches
from novel.stages.stage4.hook_facts import established_fact, fact_ref_of, hook_facts
from novel.stages.stage4.hook_wire import (
    expand_hook_wire, hook_catalog, hook_wire_schema, project_hook_input,
)
from tests.factories import hook_wire_v3
from tests.test_str06_hook import _characters, _green_pair, _hook_wire, _projection


ADDRESS = ('character', ('yun_niang',), '', 'marital_status')


def _accept(body, wire):
    return assemble_hook_design(wire, body=body, known_char_ids={'yun_niang', 'fu_jun'},
                                name_projection=_projection())


def _append_delta(body, si, bi, delta):
    raw = body.model_dump(mode='json')
    raw['scenes'][si]['beats'][bi]['target_delta'].append(delta)
    return BodyDesign.model_validate(raw)


def _state(**over):
    return {'domain': 'character', 'subjects': ['yun_niang'], 'holder': None,
            'key': 'marital_status', 'op': 'set', 'value': '已婚',
            'modality': 'actual', 'scope': 'book', **over}


def test_later_same_address_material_does_not_reject_established_initial_fact():
    """本书首答反例：init 已成立，同地址在材料拍再次更新，不等于引用该拍增量。"""
    body, _, _ = _green_pair()
    body = _append_delta(body, 1, 0, _state())
    wire = hook_wire_v3(_hook_wire())
    before = deepcopy(wire)
    hook, errors = _accept(body, wire)
    assert hook is not None and not errors, errors
    assert hook.opening_facts[0].ref == 'init' and wire == before
    # 实际引用材料拍这一次建立，仍然拒绝；不能靠地址相同把它当旧事实。
    wire['opening_facts'][0]['fact_ref'] = fact_ref_of('sc02#b01', ADDRESS)
    hook, errors = _accept(body, wire)
    assert hook is None and any('材料拍' in e for e in errors)


@pytest.mark.parametrize('change', [_state(), _state(op='unset', value=None, modality=None),
                                    _state(op='unknown', value=None, modality=None)])
def test_invalidated_initial_fact_is_still_rejected(change):
    body, _, _ = _green_pair()
    body = _append_delta(body, 0, 0, change)
    hook, errors = _accept(body, hook_wire_v3(_hook_wire()))
    assert hook is None and any('失效' in e for e in errors)


@pytest.mark.parametrize('op', ['unknown', 'unset'])
def test_unknown_and_unset_are_not_established_opening_facts(op):
    body, _, _ = _green_pair()
    delta = body.initial_state[0].model_copy(update={'op': op, 'value': None, 'modality': None})
    body = body.model_copy(update={'initial_state': (delta,)})
    hook, errors = _accept(body, hook_wire_v3(_hook_wire()))
    assert hook is None and any('未建立' in e for e in errors)
    assert fact_ref_of('init', ADDRESS) not in hook_wire_schema(body)[
        '$defs']['HookOpeningFactWire']['properties']['fact_ref']['enum']


def test_scene_overlay_expires_and_does_not_destroy_book_initial_value():
    body, _, _ = _green_pair()
    body = _append_delta(body, 0, 0, _state(scope='scene'))
    wire = hook_wire_v3(_hook_wire())
    assert _accept(body, wire)[1] == []
    wire['opening_facts'][0]['fact_ref'] = fact_ref_of('sc01#b01', ADDRESS)
    assert any('失效' in e for e in _accept(body, wire)[1])


def test_expired_scene_establishment_is_rejected_even_if_book_value_is_identical():
    body, _, _ = _green_pair()
    body = _append_delta(body, 0, 0, _state(scope='scene', value=body.initial_state[0].value))
    wire = hook_wire_v3(_hook_wire())
    wire['opening_facts'][0]['fact_ref'] = fact_ref_of('sc01#b01', ADDRESS)
    assert any('失效' in e for e in _accept(body, wire)[1])


def test_coldopen_anchor_is_first_presented_material_and_reader_uses_max_horizon():
    body, _, _ = _green_pair()
    wire = hook_wire_v3(_hook_wire())
    # 第一材料 sc02#b01，随后回到 sc01#b02；婚礼事实 sc01#b01 在冷开前已成立。
    wire['opening_facts'] = [{'fact_ref': fact_ref_of('sc01#b01',
        ('relation', ('yun_niang', 'fu_jun'), '', 'marital_status')), 'audience': 'world'}]
    assert _accept(body, wire)[1] == []
    wire['opening_facts'] = [{'fact_ref': fact_ref_of('sc02#b02',
        ('knowledge', ('fu_jun',), 'yun_niang', 'secret')), 'audience': 'reader'}]
    assert any('视界' in e for e in _accept(body, wire)[1])


@pytest.mark.parametrize('modality', ['planned', 'expected', 'conditional'])
def test_plans_are_not_world_facts_and_writer_keeps_their_modality(modality):
    body, _, _ = _green_pair()
    delta = body.initial_state[0].model_copy(update={'modality': modality})
    body = body.model_copy(update={'initial_state': (delta,)})
    wire = hook_wire_v3(_hook_wire())
    assert any('不能把' in e for e in _accept(body, wire)[1])
    wire['opening_facts'][0]['audience'] = 'pov'
    hook, errors = _accept(body, wire)
    assert hook is not None and not errors, errors
    context, errors = compile_hook_context(publish_graph(body, hook), _projection())
    assert context is not None and not errors, errors
    text = json.dumps(context.model_dump(mode='json'), ensure_ascii=False)
    assert ('尚未发生' if modality != 'conditional' else '条件成立时才发生') in text


def test_pov_knowledge_requires_correct_holder_and_keeps_knowledge_mode():
    body, _, _ = _green_pair()
    delta = TargetDelta.model_validate({'domain': 'knowledge', 'subjects': ['fu_jun'],
        'holder': 'fu_jun', 'key': 'rumor', 'op': 'set', 'value': '玉佩另有主人',
        'knowledge_mode': 'suspected', 'scope': 'book'})
    body = body.model_copy(update={'initial_state': (*body.initial_state, delta)})
    wire = hook_wire_v3(_hook_wire())
    wire['opening_facts'] = [{'fact_ref': fact_ref_of('init', delta.address()), 'audience': 'pov'}]
    assert any('持有人' in e for e in _accept(body, wire)[1])
    wire['pov']['focalizers'] = ['fu_jun']
    hook, errors = _accept(body, wire)
    assert hook is not None and not errors, errors
    context, errors = compile_hook_context(publish_graph(body, hook), _projection())
    assert context is not None and not errors, errors
    assert '怀疑' in json.dumps(context.model_dump(mode='json'), ensure_ascii=False)


def test_forbidden_fact_is_one_occurrence_not_every_update_at_same_address():
    body, _, _ = _green_pair()
    body = _append_delta(body, 1, 1, _state())
    wire = hook_wire_v3(_hook_wire())
    wire['forbidden_reveals'] = [{'kind': 'fact', 'fact_ref': fact_ref_of('sc02#b02', ADDRESS)}]
    assert _accept(body, wire)[1] == []
    wire['forbidden_reveals'][0]['fact_ref'] = fact_ref_of('init', ADDRESS)
    assert any('禁止揭示集' in e for e in _accept(body, wire)[1])


def test_catalog_reference_is_stable_and_ambiguous_establishment_is_not_accepted():
    body, _, _ = _green_pair()
    initial_ref = fact_ref_of('init', ADDRESS)
    extra = TargetDelta.model_validate(_state(key='residence', value='陆家'))
    changed = body.model_copy(update={'initial_state': (extra, *body.initial_state)})
    assert hook_catalog(body)[initial_ref] == hook_catalog(changed)[initial_ref]
    duplicate = body.model_copy(update={'initial_state': (*body.initial_state, body.initial_state[0])})
    assert established_fact(hook_facts(duplicate), 'init', ADDRESS) is None


def test_v3_closed_schema_rejects_made_up_ref_and_cross_beat_withholding():
    body, _, _ = _green_pair()
    wire = hook_wire_v3(_hook_wire())
    wire['opening_facts'][0]['fact_ref'] = 'f不存在'
    assert any('未知事实引用' in e for e in _accept(body, wire)[1])
    wire = hook_wire_v3(_hook_wire())
    wire['materials'][0]['withheld'] = [fact_ref_of('init', ADDRESS)]
    assert any('不属于该拍' in e for e in _accept(body, wire)[1])
    wire = hook_wire_v3(_hook_wire())
    wire['forbidden_reveals'][0]['address'] = {'key': '乱加字段'}
    assert any('形状非法' in e for e in _accept(body, wire)[1])
    assert any('protocol_version' in e for e in _accept(body, _hook_wire())[1])


def test_narrow_patch_has_initial_facts_boundaries_and_complete_branch_schema():
    body, _, _ = _green_pair()
    wire = hook_wire_v3(_hook_wire())
    sliced = hook_patch_slice(body, wire, [('opening_facts', 0)])
    assert sliced['facts'][fact_ref_of('init', ADDRESS)]['ref'] == 'init'
    assert sliced['materials'] == wire['materials'] and sliced['pov'] == wire['pov']
    legal = hook_patch_legal_values(body=body)
    assert set(legal['fact_refs']) == set(sliced['facts'])
    assert legal['wire_schema'] == hook_wire_schema(body)
    projected = project_hook_input(body)
    assert projected['facts'] == sliced['facts']
    assert projected['world_rules'] == list(body.world_rules)
    assert projected['character_voices'] == [v.model_dump(mode='json') for v in body.character_voices]
    assert projected['expression_decisions'] == [d.model_dump(mode='json') for d in body.expression_decisions]
    assert sliced['expression_decisions'] == projected['expression_decisions']
    assert all('fact_refs' in b and 'target_delta' not in b
               for s in projected['scenes'] for b in s['beats'])
    for scene, projected_scene in zip(body.scenes, projected['scenes'], strict=True):
        for beat, projected_beat in zip(scene.beats, projected_scene['beats'], strict=True):
            assert {k: v for k, v in projected_beat.items() if k != 'fact_refs'} == beat.model_dump(
                mode='json', exclude={'target_delta'})
    assert all('length_budget' not in c for c in projected['chapters'])
    assert projected['opening_chapter'] == {
        'purpose': body.chapters[0].purpose,
        'scene_refs': list(body.chapters[0].scene_ids),
        'beat_refs': [{'scene': 'sc01', 'beat': beat.beat_id}
                      for beat in body.scenes[0].beats]}


@pytest.mark.parametrize('protocol', ['stage4-hook-design-v1', 'stage4-hook-design-v2'])
def test_old_hook_protocols_cannot_be_upgraded_without_new_semantic_choices(protocol):
    body, _, _ = _green_pair()
    wire = hook_wire_v3(_hook_wire())
    wire['protocol_version'] = protocol
    before = deepcopy(wire)
    hook, errors = _accept(body, wire)
    assert hook is None and any('hook_protocol_literal' == e.code for e in errors)
    assert wire == before


def test_fact_expectation_json_schema_keeps_full_branch_and_requires_address():
    """JSON Schema 与 typed canonical 同尺，不用额外约束覆盖原字段定义。"""
    from jsonschema import Draft202012Validator
    from pydantic import ValidationError
    from contracts.schema.hook import HookDesign
    body, _, _ = _green_pair()
    hook, errors = _accept(body, hook_wire_v3(_hook_wire()))
    assert hook is not None and not errors, errors
    schema = HookDesign.model_json_schema()
    Draft202012Validator.check_schema(schema)
    assert {'kind', 'focus', 'answer'} <= schema['$defs']['HookReaderFact']['properties'].keys()
    validator = Draft202012Validator(schema)
    payload = hook.model_dump(mode='json')
    assert not list(validator.iter_errors(payload))
    for change in ('missing_kind', 'missing_focus', 'missing_address', 'null_address'):
        bad = deepcopy(payload)
        expectation = bad['reader_expectation']
        if change == 'missing_kind':
            expectation.pop('kind')
        elif change == 'missing_focus':
            expectation.pop('focus')
        elif change == 'missing_address':
            expectation['answer'].pop('address')
        else:
            expectation['answer']['address'] = None
        assert list(validator.iter_errors(bad)), change
        with pytest.raises(ValidationError):
            HookDesign.model_validate(bad)


def _material(scene, beat, transition):
    return {'beat_ref': {'scene': scene, 'beat': beat}, 'withheld': [],
            'presentation': '只呈现当拍异样，不交代人物身份。',
            'emphasis': 'develop', 'transition': transition}


@pytest.mark.parametrize('materials,valid', [
    ([_material('sc01', 'b01', 'start'), _material('sc01', 'b02', 'continue')], True),
    ([_material('sc01', 'b01', 'start'), _material('sc01', 'b03', 'time_skip')], True),
    ([_material('sc02', 'b01', 'start'), _material('sc01', 'b02', 'flashback')], True),
    ([_material('sc02', 'b01', 'start'), _material('sc01', 'b02', 'scene_cut')], True),
    ([_material('sc01', 'b01', 'start'), _material('sc01', 'b03', 'continue')], False),
    ([_material('sc02', 'b01', 'start'), _material('sc01', 'b02', 'time_skip')], False),
    ([_material('sc01', 'b02', 'start'), _material('sc02', 'b01', 'flashback')], False),
    ([_material('sc01', 'b01', 'start'), _material('sc01', 'b02', 'scene_cut')], False),
    ([_material('sc01', 'b02', 'start'), _material('sc02', 'b01', 'return')], False),
    ([_material('sc01', 'b01', 'continue')], False),
    ([_material('sc01', 'b01', 'start'), _material('sc01', 'b02', 'start')], False),
])
def test_transition_declarations_match_positions_without_banning_cross_scene(materials, valid):
    body, _, _ = _green_pair()
    wire = hook_wire_v3(_hook_wire(materials=materials, opening_facts=[]))
    before = deepcopy(wire)
    hook, errors = _accept(body, wire)
    transition_errors = [error for error in errors if error.code == 'hook_transition']
    assert bool(transition_errors) is not valid
    assert bool(hook) is valid, errors
    assert wire == before
    for error in transition_errors:
        assert error.context['wire_paths']


def test_return_after_flashback_and_reader_development_use_real_later_beats():
    body, _, _ = _green_pair()
    raw = body.model_dump(mode='json')
    raw['scenes'][1]['beats'].append({
        'beat_id': 'b03', 'event': '芸娘追问玉佩的旧主。', 'participants': ['yun_niang'],
        'target_delta': []})
    body = BodyDesign.model_validate(raw)
    wire = hook_wire_v3(_hook_wire(
        materials=[_material('sc02', 'b01', 'start'),
                   _material('sc01', 'b02', 'flashback'),
                   _material('sc02', 'b02', 'return')],
        opening_facts=[], forbidden_reveals=[],
        reader_expectation={'kind': 'action', 'focus': '她接下来会怎样追查？',
                            'followup_refs': [{'scene': 'sc02', 'beat': 'b03'}]}))
    hook, errors = _accept(body, wire)
    assert hook is not None and not errors, errors
    assert hook.reader_expectation.followup_refs == ('sc02#b03',)
    context, errors = compile_hook_context(publish_graph(body, hook), _projection())
    assert context is not None and not errors, errors
    assert context.reader_expectation.model_dump() == {
        'kind': 'action', 'focus': '她接下来会怎样追查？'}
    assert '芸娘追问玉佩的旧主' not in json.dumps(context.model_dump(), ensure_ascii=False)


@pytest.mark.parametrize('field', ['emphasis', 'transition'])
def test_material_organization_is_required_and_not_guessed(field):
    body, _, _ = _green_pair()
    wire = hook_wire_v3(_hook_wire())
    wire['materials'][0].pop(field)
    hook, errors = _accept(body, wire)
    assert hook is None and any(error.code == 'hook_wire_shape' for error in errors)


@pytest.mark.parametrize('mode', ['suspected', 'heard', 'believes'])
def test_fact_expectation_cannot_promote_uncertain_knowledge_to_an_answer(mode):
    body, _, _ = _green_pair()
    raw = body.model_dump(mode='json')
    raw['scenes'][1]['beats'][1]['target_delta'][0]['knowledge_mode'] = mode
    body = BodyDesign.model_validate(raw)
    hook, errors = _accept(body, hook_wire_v3(_hook_wire()))
    assert hook is None and any(error.code == 'hook_expectation_knowledge' for error in errors)


@pytest.mark.parametrize('op', ['unset', 'unknown'])
def test_fact_expectation_requires_a_real_set_establishment(op):
    body, _, _ = _green_pair()
    raw = body.model_dump(mode='json')
    raw['scenes'][1]['beats'][1]['target_delta'][0].update(
        op=op, value=None, knowledge_mode=None)
    body = BodyDesign.model_validate(raw)
    hook, errors = _accept(body, hook_wire_v3(_hook_wire()))
    assert hook is None and any(error.code == 'hook_expectation_fact' for error in errors)


def test_reader_expectation_anchor_is_after_max_horizon_and_has_narrow_patch_evidence():
    body, _, _ = _green_pair()
    wire = hook_wire_v3(_hook_wire())
    wire['reader_expectation']['fact_ref'] = fact_ref_of('init', ADDRESS)
    hook, errors = _accept(body, wire)
    assert hook is None and any(error.code == 'hook_expectation_ref' for error in errors)
    wire['reader_expectation'] = {
        'kind': 'relationship', 'focus': '他们将怎样相处？',
        'followup_refs': [{'scene': 'sc01', 'beat': 'b03'}]}
    hook, errors = _accept(body, wire)
    assert hook is None and any(error.code == 'hook_expectation_horizon' for error in errors)
    sliced = hook_patch_slice(body, wire, [('reader_expectation',)])
    assert sliced['reader_expectation'] == wire['reader_expectation']
    assert any(row['beat_ref'] == {'scene': 'sc01', 'beat': 'b03'} for row in sliced['beats'])


@pytest.mark.parametrize('kind', ['fact', 'action', 'relationship'])
def test_expectation_patch_slice_preserves_each_kind_and_its_real_source(kind):
    body, _, _ = _green_pair()
    wire = hook_wire_v3(_hook_wire())
    if kind != 'fact':
        wire['reader_expectation'] = {
            'kind': kind, 'focus': '后续发展怎样承接？',
            'followup_refs': [{'scene': 'sc02', 'beat': 'b02'}]}
    before = deepcopy(wire)
    sliced = hook_patch_slice(body, wire, [('reader_expectation',)])
    assert sliced['reader_expectation'] == before['reader_expectation']
    assert any(row['beat_ref'] == {'scene': 'sc02', 'beat': 'b02'} for row in sliced['beats'])
    assert wire == before


@pytest.mark.parametrize('expectation', [
    {'kind': 'action', 'focus': '后续行动', 'followup_refs': None},
    {'kind': 'relationship', 'focus': '后续关系', 'followup_refs': 'sc02#b02'},
    {'kind': 'action', 'focus': '后续行动', 'followup_refs': {'scene': 'sc02', 'beat': 'b02'}},
    {'kind': 'relationship', 'focus': '后续关系', 'followup_refs': [None, []]},
    {'kind': 'fact', 'focus': '后续答案', 'fact_ref': {}},
    {'kind': 'fact', 'focus': '后续答案', 'fact_ref': []},
])
def test_invalid_expectation_shape_remains_narrowly_repairable_without_inventing_sources(expectation):
    body, _, _ = _green_pair()
    wire = hook_wire_v3(_hook_wire())
    wire['reader_expectation'] = deepcopy(expectation)
    before = deepcopy(wire)
    hook, errors = _accept(body, wire)
    assert hook is None and all(error.code == 'hook_wire_shape' for error in errors)
    targets = locate_design_patches(wire, errors)
    assert targets is not None and [target[0] for target in targets] == [('reader_expectation',)]
    sliced = hook_patch_slice(body, wire, [target[0] for target in targets])
    assert sliced['reader_expectation'] == expectation
    assert {tuple(row['beat_ref'].values()) for row in sliced['beats']} == {('sc02', 'b01'), ('sc01', 'b02')}
    assert wire == before


@pytest.mark.parametrize('followups', [[], [{'scene': 'sc09', 'beat': 'b01'}]])
def test_expectation_patch_exposes_real_later_action_without_delta_or_prior_reference(followups):
    body, _, _ = _green_pair()
    raw = body.model_dump(mode='json')
    raw['scenes'][1]['beats'].append({
        'beat_id': 'b03', 'event': '芸娘向玉佩的旧主递出问讯。',
        'participants': ['yun_niang'], 'target_delta': []})
    body = BodyDesign.model_validate(raw)
    wire = hook_wire_v3(_hook_wire())
    wire['reader_expectation'] = {
        'kind': 'action', 'focus': '她接下来怎样查证？', 'followup_refs': followups}
    before = deepcopy(wire)
    hook, errors = _accept(body, wire)
    assert hook is None and errors
    targets = locate_design_patches(wire, errors)
    assert targets is not None and [target[0] for target in targets] == [('reader_expectation',)]
    sliced = hook_patch_slice(body, wire, [target[0] for target in targets])
    assert sliced['followup_candidates'] == {
        'scope': 'after_material_horizon',
        'material_horizon': {'scene': 'sc02', 'beat': 'b01'},
        'beats': [
            {'beat_ref': {'scene': 'sc02', 'beat': 'b02'},
             'event': body.scenes[1].beats[1].event, 'participants': ['yun_niang']},
            {'beat_ref': {'scene': 'sc02', 'beat': 'b03'},
             'event': '芸娘向玉佩的旧主递出问讯。', 'participants': ['yun_niang']}]}
    assert not any(row['beat_ref'] == {'scene': 'sc02', 'beat': 'b03'} for row in sliced['beats'])
    material_slice = hook_patch_slice(body, wire, [('materials', 0)])
    assert 'followup_candidates' not in material_slice
    assert wire == before


def test_expectation_patch_does_not_guess_horizon_from_invalid_material_positions():
    body, _, _ = _green_pair()
    wire = hook_wire_v3(_hook_wire())
    wire['materials'][0]['beat_ref'] = {'scene': 'sc09', 'beat': 'b01'}
    before = deepcopy(wire)
    sliced = hook_patch_slice(body, wire, [('reader_expectation',)])
    directory = sliced['followup_candidates']
    assert directory['scope'] == 'all_beats_horizon_unresolved'
    assert directory['material_horizon'] is None
    assert [row['beat_ref'] for row in directory['beats']] == [
        {'scene': scene.scene_id, 'beat': beat.beat_id}
        for scene in body.scenes for beat in scene.beats]
    assert [row['event'] for row in directory['beats']] == [
        beat.event for scene in body.scenes for beat in scene.beats]
    assert wire == before


def test_context_projects_authoritative_voice_and_never_copies_future_answer():
    body, _, _ = _green_pair()
    raw = body.model_dump(mode='json')
    raw['expression_decisions'][0]['statement'] = '叙述克制，情感藏在动作和停顿里。'
    raw['scenes'][0]['delivery']['voice'] = '对白里的试探，话说一半便收住。'
    raw['scenes'][1]['delivery']['voice'] = '冷面反应，异常藏在日常动作中。'
    body = BodyDesign.model_validate(raw)
    wire = hook_wire_v3(_hook_wire(forbidden_reveals=[]))
    hook, errors = _accept(body, wire)
    assert hook is not None and not errors, errors
    graph = publish_graph(body, hook)
    assert body.contract_version == 'str06-rc22' and graph.contract_version == 'str06-rc23'
    context, errors = compile_hook_context(graph, _projection())
    assert context is not None and not errors, errors
    assert context.book_style == body.book_style
    assert [m.model_dump() for m in context.expression_mechanisms] == [
        {'ex_id': decision.ex_id, 'statement': decision.statement}
        for decision in body.expression_decisions]
    assert all(v.char_id not in hook.concealed for v in context.character_voices)
    assert [(v.scene_ref, v.voice) for v in context.scene_voices] == [
        ('sc02', body.scenes[1].delivery.voice), ('sc01', body.scenes[0].delivery.voice)]
    assert context.reader_expectation.model_dump() == {
        'kind': 'fact', 'focus': hook.reader_expectation.focus}
    assert [m.emphasis for m in context.materials] == ['develop', 'brief']
    assert [m.transition for m in context.materials] == ['start', 'flashback']
    assert '玉佩刻着沈字' not in json.dumps(context.model_dump(), ensure_ascii=False)
    assert not {'target_chars', 'hard_max_chars', 'design_brief', 'delivery_brief'} & context.model_dump().keys()
    from novel.stages.stage5.hook_writer import build_hook_user_prompt
    prompt = build_hook_user_prompt(context)
    for decision in body.expression_decisions:
        assert decision.statement in prompt
        assert decision.ex_id not in prompt and decision.reason not in prompt
    assert f'材料1所在场的表达机制（按本楔子人称实现）：{body.scenes[1].delivery.voice}' in prompt
    assert f'材料2所在场的表达机制（按本楔子人称实现）：{body.scenes[0].delivery.voice}' in prompt
