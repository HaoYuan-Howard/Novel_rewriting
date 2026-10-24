"""当前接受与说法历史经执行计划、入场读取和 Hook 原子选择分别保留。

例中命题、说法性质及人物都是显式输入；测试证明确定性消费不升级推测或
听到的否认，不把机械通过宣称为小说抽取质量已经验收。
"""
from __future__ import annotations

from copy import deepcopy

import pytest
from jsonschema import Draft202012Validator
from pydantic import ValidationError

from contracts.schema.design import BodyDesign, TargetDelta
from contracts.schema.execution import Establishment
from contracts.schema.grounding import BindingEntry
from contracts.schema.hook import ForbiddenReveal, HookDesign
from contracts.schema.naming import NameAssignment, NameMapV6
from contracts.schema.source import NameProjection, NameStructure
from contracts.schema.state import EffectDomain, StateRead, SurfaceIntroduction
from novel.stages.common import (
    participant_background, project_chapter_packet, render_chapter_packet,
    render_hook_context, scene_read_views,
)
from novel.stages.stage4.book_ir import compile_writer_book_ir
from novel.stages.stage4.compile import compile_execution_plan
from novel.stages.stage4.fold import FoldedState, fold_accepted_prefix
from novel.stages.stage4.hook import assemble_hook_design, publish_graph
from novel.stages.stage4.hook_context import compile_hook_context
from novel.stages.stage4.hook_facts import (
    established_fact, fact_is_current, hook_facts,
)
from novel.stages.stage4.hook_validation import validate_hook_design
from novel.stages.stage4.hook_wire import annotated_hook_catalog, expand_hook_wire
from tests.test_str06_fold_guard import _binding, _graph


STATEMENT = '阿姐送来此人'
KEY = 'kp_0123456789abcdef'


def _delta(mode='believes', value='true', *, op='set', scope='book') -> TargetDelta:
    return TargetDelta(domain='knowledge', holder='ch1', key=KEY, subjects=(),
                       op=op, value=value, scope=scope, knowledge_mode=mode)


def _est(delta: TargetDelta, ref: str) -> Establishment:
    return Establishment(**delta.model_dump(exclude={'confirms'}), established_by=ref)


def _body(*, first=(), second=(), read_second=False) -> BodyDesign:
    raw = _graph().model_dump(mode='json')
    raw['initial_surfaces'] = [
        SurfaceIntroduction(char_id='ch1', surfaces=('主角',)).model_dump(),
        SurfaceIntroduction(char_id='ch2', surfaces=('阿姐',)).model_dump()]
    for scene, deltas in zip(raw['scenes'], (first, second)):
        scene['beats'][0]['target_delta'] = [d.model_dump(mode='json') for d in deltas]
    if read_second:
        raw['scenes'][1]['state_reads'] = [{
            'domain': 'knowledge', 'holder': 'ch1', 'key': KEY, 'subjects': []}]
    return BodyDesign.model_validate(raw)


def _names():
    projection = NameProjection.model_validate({
        'proper_literals': [], 'family_literals': [], 'by_person': [
            {'char_id': char_id, 'display': face, 'family_literal': None,
             'given': None, 'proper_literals': []}
            for char_id, face in (('ch1', '主角'), ('ch2', '阿姐'))]})
    structure = NameStructure.model_validate({
        'families': {'fam_chen': {'literal': '陈', 'forms': []}},
        'people': {char_id: {'family_id': 'fam_chen', 'given': None, 'aliases': [],
                             'surface_forms': {face: None}}
                   for char_id, face in (('ch1', '主角'), ('ch2', '阿姐'))}})
    name_map = NameMapV6(entries=tuple(NameAssignment(
        char_ref=char_id, name=None, aliases={}, keep_reason='测试沿用纯称谓')
        for char_id in ('ch1', 'ch2')))
    return projection, structure, name_map


def _hook_wire(body, *, material='s02#bt1', opening=(), withheld=(), forbidden=()):
    scene, beat = material.split('#')
    return {
        'protocol_version': 'stage4-hook-design-v5',
        'pov': {'person': 'first', 'focalizers': ['ch1']},
        'opening_facts': [{'fact_ref': fact.fact_ref, 'audience': 'world'} for fact in opening],
        'materials': [{'beat_ref': {'scene': scene, 'beat': beat},
                       'emphasis': 'develop', 'transition': 'start',
                       'withheld': [fact.fact_ref for fact in withheld]}],
        'concealed': [], 'surface_releases': [
            {'char_id': 'ch1', 'surfaces': ['主角']},
            {'char_id': 'ch2', 'surfaces': ['阿姐']}],
        'forbidden_reveals': [{'kind': 'fact', 'fact_ref': fact.fact_ref} for fact in forbidden],
        'reader_expectation': {'kind': 'action', 'focus': '主角会怎样继续核实来由',
                               'followup_refs': [{'scene': 's03', 'beat': 'bt1'}]},
        'local_focus': '保留眼前的误会；听到否认不等于已经纠正误信。',
    }


def test_fold_keeps_belief_and_both_claim_polarities_with_their_origins():
    fold = FoldedState().apply('s01', [_est(_delta(), 's01#bt1')])
    fold.apply('s02', [_est(_delta('heard'), 's02#bt1'),
                       _est(_delta('heard', 'false'), 's02#bt2')])
    entries = fold.entry_knowledge_view()
    assert {(e.mode, e.polarity, e.established_by) for e in entries} == {
        ('believes', 'positive', 's01#bt1'), ('heard', 'positive', 's02#bt1'),
        ('heard', 'negative', 's02#bt2')}
    assert fold.value_of(EffectDomain.KNOWLEDGE, KEY, holder='ch1') == 'true'


@pytest.mark.parametrize('op', ['unset', 'unknown'])
def test_withdrawal_or_unknown_clears_acceptance_without_erasing_heard_history(op):
    fold = FoldedState().apply('s01', [
        _est(_delta(), 's01#bt1'), _est(_delta('heard'), 's01#bt2'),
        _est(_delta('heard', 'false'), 's01#bt3')])
    fold.apply('s02', [_est(_delta(None, None, op=op), 's02#bt1')])
    entries = fold.entry_knowledge_view()
    assert {(e.mode, e.polarity) for e in entries if e.status == 'set'} == {
        ('heard', 'positive'), ('heard', 'negative')}
    assert [(e.status, e.mode, e.polarity) for e in entries if e.status != 'set'] == [
        (op, None, None)]
    assert fold.value_of(EffectDomain.KNOWLEDGE, KEY, holder='ch1') is None


def test_claim_history_obeys_scope_and_derived_scene_views_do_not_mutate_prefix():
    base = FoldedState().apply('s01', [_est(_delta(), 's01#bt1')])
    view = base.with_scene_changes('s02', [
        _est(_delta('heard', 'false', scope='scene'), 's02#bt1')])
    assert len(view.entry_knowledge_view()) == 2
    assert len(base.entry_knowledge_view()) == 1
    view.expire_scene_scope('s02')
    assert view.entry_knowledge_view() == base.entry_knowledge_view()


def test_entry_read_uses_acceptance_and_keeps_same_address_claim_as_background():
    body = _body(read_second=True)
    fold = FoldedState().apply('s01', [
        _est(_delta(), 's01#bt1'), _est(_delta('heard', 'false'), 's01#bt2')])
    scene = body.scenes[1]
    views = scene_read_views(scene=scene, establishments=(), folded_prefix=fold)
    assert views.errors == []
    assert [(e.mode, e.polarity) for e in views.entry_knowledge] == [('believes', 'positive')]
    assert views.read_anchors == ('s01#bt1',)
    _, background = participant_background(scene=scene, folded_prefix=fold)
    assert [(e.mode, e.polarity) for e in background] == [('heard', 'negative')]


def test_same_scene_read_after_hearing_denial_still_reads_prior_belief():
    body = _body()
    first = body.scenes[0]
    prior = first.beats[0]
    second = prior.model_copy(update={'beat_id': 'bt2'})
    scene = first.model_copy(update={
        'beats': (prior, second), 'state_reads': (StateRead(
            domain='knowledge', holder='ch1', key=KEY, at='bt2'),)})
    fold = FoldedState().apply('init', [_est(_delta(), 'init')])
    views = scene_read_views(scene=scene, folded_prefix=fold, establishments=(
        _est(_delta('heard', 'false'), 's01#bt1'),))
    assert views.errors == []
    assert [(e.mode, e.polarity) for e in views.beat_reads['bt2']] == [('believes', 'positive')]
    assert len(fold.entry_knowledge_view()) == 1
    only_claim = FoldedState().apply('init', [_est(_delta('heard', 'false'), 'init')])
    denied = scene_read_views(scene=body.scenes[1].model_copy(update={
        'state_reads': (StateRead(domain='knowledge', holder='ch1', key=KEY),)}),
        establishments=(), folded_prefix=only_claim)
    assert [e.code for e in denied.errors] == ['design_read_unestablished']


def test_real_compile_ir_and_writer_keep_belief_and_denial_in_separate_roles():
    body = _body(first=(_delta(), _delta('heard', 'false')), read_second=True)
    binding = _binding(BindingEntry(
        obligation_id='ob01', scene_id='s01', beat_ref='s01#bt1'))
    plan, errors = compile_execution_plan(body, binding)
    assert plan is not None and errors == [], errors
    assert [e.knowledge_mode for e in plan.scenes[0].planned_delta] == ['believes', 'heard']
    current = fold_accepted_prefix(plan.scenes[:1]).entry_knowledge_view()
    assert {(e.mode, e.polarity) for e in current} == {
        ('believes', 'positive'), ('heard', 'negative')}
    projection, structure, name_map = _names()
    ir = compile_writer_book_ir(
        graph=body, plan=plan, binding=binding, projection=projection, structure=structure,
        name_map=name_map, kp_statements={KEY: STATEMENT}, total_budget=1200,
        length_band=(0.8, 1.2))
    second = ir.chapters[1].scenes[0].verification_ir
    assert [(e.mode, e.polarity) for e in second.entry_knowledge] == [('believes', 'positive')]
    assert [(e.mode, e.polarity) for e in second.background_knowledge] == [('heard', 'negative')]
    text = render_chapter_packet(project_chapter_packet(ir, 2))
    assert '主角以为为真（不代表世界为真）「阿姐送来此人」' in text
    assert '主角听说「并非：阿姐送来此人」（说法历史' in text
    assert '主角确知「并非：阿姐送来此人」' not in text


def test_hook_currentness_keeps_claim_history_and_does_not_revive_withdrawn_atom():
    body = _body(first=(
        _delta(), _delta(None, None, op='unset'), _delta(), _delta('heard', 'false')))
    facts = hook_facts(body)
    assert len({f.fact_ref for f in facts}) == 4
    assert [f.atom_ordinal for f in facts] == [0, 1, 2, 3]
    assert not fact_is_current(facts[0], facts, (1, 0))
    assert fact_is_current(facts[2], facts, (1, 0))
    assert fact_is_current(facts[3], facts, (1, 0))
    exact = established_fact(facts, facts[2].ref, facts[2].delta.address(),
                             fact_ref=facts[2].fact_ref)
    assert exact == facts[2]
    catalog = annotated_hook_catalog(body)
    assert 'later_writes' not in catalog[facts[2].fact_ref]
    assert 'later_writes' not in catalog[facts[3].fact_ref]


def test_hook_wire_canonical_and_renderer_preserve_belief_and_claim_without_truth_upgrade():
    body = _body(first=(_delta(), _delta('heard', 'false')))
    facts = hook_facts(body)
    projection, structure, name_map = _names()
    wire = _hook_wire(body, opening=facts)
    expanded, errors = expand_hook_wire(wire, body)
    assert expanded is not None and errors == [], errors
    assert [row['fact_ref'] for row in expanded['opening_facts']] == [f.fact_ref for f in facts]
    hook, errors = assemble_hook_design(
        wire, body=body, known_char_ids={'ch1', 'ch2'}, name_projection=projection)
    assert hook is not None and errors == [], errors
    restored = HookDesign.model_validate_json(hook.model_dump_json())
    assert restored == hook
    context, errors = compile_hook_context(
        publish_graph(body, restored), projection, structure, {KEY: STATEMENT},
        total_budget=1200, name_map=name_map)
    assert context is not None and errors == [], errors
    text = render_hook_context(context)
    assert '【起笔背景基线】' in text
    assert '世界已成立' not in text
    assert '主角以为为真（不代表世界为真）：阿姐送来此人' in text
    assert '主角听说：并非：阿姐送来此人（说法历史' in text
    assert '主角确知' not in text
    invalid = restored.model_dump(mode='json')
    invalid['opening_facts'][0]['fact_ref'] = 'missing_atom'
    bad = HookDesign.model_validate(invalid)
    assert any(error.code == 'hook_opening_unestablished' for error in validate_hook_design(
        bad, body=body, known_char_ids={'ch1', 'ch2'}, name_projection=projection))


def test_hook_withheld_and_forbidden_select_only_one_same_address_atom():
    body = _body(first=(_delta(), _delta('heard', 'false')))
    belief, denial = hook_facts(body)
    projection, structure, name_map = _names()
    wire = _hook_wire(body, material='s01#bt1', withheld=(denial,), forbidden=(denial,))
    hook, errors = assemble_hook_design(
        wire, body=body, known_char_ids={'ch1', 'ch2'}, name_projection=projection)
    assert hook is not None and errors == [], errors
    assert hook.materials[0].withheld[0].fact_ref == denial.fact_ref
    context, errors = compile_hook_context(
        publish_graph(body, hook), projection, structure, {KEY: STATEMENT},
        total_budget=1200, name_map=name_map)
    assert context is not None and errors == [], errors
    assert len(context.materials[0].deltas) == 1
    assert '以为为真' in context.materials[0].deltas[0]
    assert '听说' not in context.materials[0].deltas[0]
    assert any('听说：并非' in text for text in context.forbidden_reveals)
    bad = deepcopy(wire)
    bad['materials'][0]['withheld'] = [belief.fact_ref]
    rejected, errors = assemble_hook_design(
        bad, body=body, known_char_ids={'ch1', 'ch2'}, name_projection=projection)
    assert rejected is None and any(error.code == 'hook_forbidden_leak' for error in errors)


@pytest.mark.parametrize('raw,valid', [
    ({'ref': 's03#bt1', 'address': None, 'fact_ref': None}, True),
    ({'ref': 's03#bt1', 'fact_ref': None}, False),
    ({'ref': 's03#bt1', 'address': None}, False),
    ({'ref': 's03#bt1', 'address': None, 'fact_ref': 'f_atom'}, False),
    ({'ref': 's03#bt1', 'address': {
        'domain': 'world', 'key': 'cause'}, 'fact_ref': None}, False),
    ({'ref': 's03#bt1', 'address': {
        'domain': 'world', 'key': 'cause'}, 'fact_ref': 'f_atom'}, True),
])
def test_forbidden_atom_address_and_reference_have_the_same_explicit_shape_in_both_validators(
    raw, valid,
):
    schema_valid = Draft202012Validator(ForbiddenReveal.model_json_schema()).is_valid(raw)
    assert schema_valid is valid
    if valid:
        item = ForbiddenReveal.model_validate(raw)
        dumped = item.model_dump(mode='json')
        assert 'address' in dumped and dumped['fact_ref'] == raw['fact_ref']
        assert ForbiddenReveal.model_validate_json(item.model_dump_json()) == item
    else:
        with pytest.raises(ValidationError):
            ForbiddenReveal.model_validate(raw)
