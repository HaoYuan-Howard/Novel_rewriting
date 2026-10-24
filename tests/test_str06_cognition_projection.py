"""认知操作和极性经真实 Preserve、Design、IR 及写手渲染保持闭合。

只验证结构化语义的确定性传递；样例中的真假、同一性均为显式输入，
不宣称机械测试证明了小说语义抽取正确。
"""
from __future__ import annotations

from copy import deepcopy

import pytest
from pydantic import ValidationError

from contracts.schema.grounding import ObligationAnchor
from contracts.schema.naming import NameAssignment, NameMapV6
from contracts.schema.source import BlueprintsArtifactV18, CharactersCore, NameProjection, NameStructure
from contracts.schema.workflow import STAGE4_PRESERVE_PROTOCOL
from contracts.schema.design import STAGE4_DESIGN_WIRE_PROTOCOL
from novel.stages.common import project_chapter_packet, render_chapter_packet
from novel.stages.stage4.book_ir import compile_writer_book_ir
from novel.stages.stage4.compile import compile_execution_plan
from novel.stages.stage4.design import assemble_body_design
from novel.stages.stage4.fold import fold_accepted_prefix
from novel.stages.stage4.grounding import (
    assemble_preserve, derive_semantic_binding, design_obligation_index, project_brief,
)
from novel.stages.stage4.hook_context import render_hook_fact_text
from novel.stages.stage4.hook_facts import established_fact, fact_is_current, hook_facts
from novel.stages.stage4.slim import design_placements, design_wire_model, knowledge_change_index
from tests.str06_expression_inputs import sample_blueprint_expression
from tests.str06_facilities import v13_beat


def _inputs(rows, *, named=False):
    surfaces = [{'surface': '主角', 'from_chapter': 1, 'source_refs': ['e01_01']}]
    forms = {'主角': None}
    if named:
        surfaces.append({'surface': '晟儿', 'from_chapter': 1, 'source_refs': ['e01_03']})
        forms['晟儿'] = '{given}'
    chars = CharactersCore.model_validate({'initial_knowledge': [], 'characters': {'hero': {
        'canonical': '主角', 'surfaces': surfaces, 'identity_bindings': []}}, 'initial_state': []})
    structure = NameStructure.model_validate({'families': {
        'fam_shen': {'literal': '沈', 'forms': []}}, 'people': {'hero': {
        'family_id': 'fam_shen', 'given': '晟儿' if named else None,
        'aliases': [], 'surface_forms': forms}}})
    name_map = NameMapV6(entries=(NameAssignment(
        char_ref='hero', name=None, aliases={}, keep_reason='测试样例保留称谓'),))
    beats = []
    evidence = {}
    handles = []
    for n, row in enumerate(rows, 1):
        segment = f'e01_{n:02d}'
        evidence[segment] = row['quote']
        cognitions = []
        for cognition in row['cognition']:
            cognitions.append({**cognition, 'basis': [{'segment': segment, 'quote': row['quote']}]})
            handles.append(f'b01_{n:02d}.c{len(cognitions)}')
        beats.append(v13_beat(f'b01_{n:02d}', row['quote'], participants=('hero',),
                              source_refs=(segment,), cognition=tuple(cognitions),
                              boundary='new' if n == 1 else 'continue'))
    blueprint = BlueprintsArtifactV18.model_validate({
        'source_expression': sample_blueprint_expression(), 'world_constraints': [],
        'chapters': [{'chapter': 1, 'beats': beats,
                      'scenes': [{'scene_id': 'sc01', 'beat_ids': [b['beat_id'] for b in beats]}],
                      'causal_edges': []}], 'evidence': evidence})
    preserve = {'protocol_version': STAGE4_PRESERVE_PROTOCOL, 'obligations': [
        {'kind': 'identity' if named else 'reveal', 'statement': f'保留第{n}次认知',
         'anchors': [handle], 'must_preserve': True,
         'satisfaction': '保留该次认知的操作、极性及依据', 'requires': []}
        for n, handle in enumerate(handles, 1)], 'series': []}
    checkpoint, errors = assemble_preserve(
        preserve, policy_id='test', policy_version=13, characters=chars, blueprint=blueprint,
        name_map=name_map, name_map_sha256='n' * 64, provider='fake', model='fake',
        request_sha256='r' * 64)
    assert checkpoint is not None and errors == [], errors
    brief, _ = project_brief(checkpoint, chars, blueprint, structure=structure, name_map=name_map)
    return chars, structure, name_map, checkpoint, brief


def _cognition(mode, *, op='set', polarity='positive', holder='hero', statement='阿姐送来此人'):
    return {'op': op, 'holder': holder, 'mode': mode,
            'polarity': polarity, 'support': 'inference' if mode == 'believes' else 'confirmed',
            'reader_visible': True, 'proposition': {'ref': 'p_arrival', 'statement': statement}}


def test_correction_reaches_design_ir_body_and_hook_without_losing_negative_or_withdrawal():
    chars, structure, name_map, checkpoint, brief = _inputs([
        {'quote': '我猜是阿姐把他送来的。', 'cognition': [_cognition('believes')]},
        {'quote': '我不再坚持原先的猜测。', 'cognition': [
            _cognition(None, op='unset', polarity=None)]},
        {'quote': '原来不是阿姐送来，是我走错了巷子。', 'cognition': [
            _cognition('learns', polarity='negative'),
            _cognition('learns', polarity='negative', holder='reader')]},
    ])
    obligations = design_obligation_index(checkpoint, brief)
    refs = {ob: ref for ref, (ob, _change) in knowledge_change_index(obligations).items()}
    wire = {'protocol_version': STAGE4_DESIGN_WIRE_PROTOCOL, 'book_style': '保留喜剧误会及纠正。',
            'initial_surfaces': [{'char_id': 'hero', 'surfaces': ['主角']}],
            'scenes': [{'driver': {'focal_actor': 'hero', 'immediate_want': '查明来由',
                                   'opposition': '误会', 'turn': '得知走错巷子'},
                        'brief': '依次保留猜测、撤回和反命题确认。', 'beats': [
                            {'ev': '主角猜测来由', 'who': ['hero'], 'real': ['ob01'],
                             'know': [{'ref': refs['ob01']}]},
                            {'ev': '主角撤回猜测', 'who': ['hero'], 'real': ['ob02'],
                             'know': [{'ref': refs['ob02']}]},
                            {'ev': '揭示走错巷子', 'who': ['hero'], 'real': ['ob03', 'ob04'],
                             'state': [{'at': 'world', 'key': 'arrival_cause', 'val': '主角走错巷子'}],
                             'know': [{'ref': refs['ob03']}, {'ref': refs['ob04'],
                                       'confirms': {'domain': 'world', 'key': 'arrival_cause'}}]},
                        ]}], 'chapters': [{'purpose': '误会产生并获得纠正', 'scenes': 1}]}
    kwargs = {'known_char_ids': set(chars.characters), 'brief': brief, 'obligations': obligations,
              'source_narration': brief.source_narration, 'expression_requirements': brief.expression}
    body, errors = assemble_body_design(wire, **kwargs)
    assert body is not None and errors == [], errors
    binding, errors = derive_semantic_binding(
        design_placements(design_wire_model(wire, brief)), body, checkpoint)
    assert binding is not None and errors == [], errors
    plan, errors = compile_execution_plan(body, binding)
    assert plan is not None and errors == [], errors
    current = fold_accepted_prefix(plan.scenes).entry_knowledge_view()
    assert {(e.holder, e.polarity, e.mode) for e in current} == {
        ('hero', 'negative', 'known'), ('reader', 'negative', 'known')}
    projection = NameProjection.model_validate({'proper_literals': [], 'family_literals': [],
        'by_person': [{'char_id': 'hero', 'display': '主角', 'family_literal': None,
                       'given': None, 'proper_literals': []}]})
    statements = {p.kp_id: p.statement for p in brief.propositions}
    ir = compile_writer_book_ir(
        graph=body, plan=plan, binding=binding, total_budget=1200, length_band=(0.8, 1.2),
        projection=projection, structure=structure, name_map=name_map, kp_statements=statements)
    deltas = [d for beat in ir.chapters[0].scenes[0].verification_ir.beats for d in beat.deltas
              if d.domain.value == 'knowledge']
    assert [d.value for d in deltas] == ['true', None, 'false', 'false']
    assert all(d.proposition_statement == '阿姐送来此人' for d in deltas)
    text = render_chapter_packet(project_chapter_packet(ir, 1))
    assert '主角以为为真（不代表世界为真）「阿姐送来此人」' in text
    assert '主角撤回对「阿姐送来此人」的接受' in text
    assert '主角确知「并非：阿姐送来此人」' in text
    assert '读者确知「并非：阿姐送来此人」' in text
    negative = body.scenes[0].beats[2].target_delta[1]
    hook_text = render_hook_fact_text(projection, negative.address(), negative, statements)
    assert '并非：阿姐送来此人' in hook_text
    missing = deepcopy(wire)
    missing['scenes'][0]['beats'][2]['know'][1].pop('confirms')
    rejected, errors = assemble_body_design(missing, **kwargs)
    assert rejected is None and any('confirms' in error for error in errors)


def test_later_name_evidence_does_not_force_earlier_same_proposition_to_reveal_name():
    statement = '这个孩子就是晟儿'
    _, _, _, checkpoint, brief = _inputs([
        {'quote': '这个孩子一直陪在主角身边。', 'cognition': [
            _cognition('believes', statement=statement)]},
        {'quote': '主角仍然在猜孩子的来历。', 'cognition': [
            _cognition('believes', statement=statement)]},
        {'quote': '孩子的名字叫晟儿。', 'cognition': [
            _cognition('learns', statement=statement)]},
    ], named=True)
    obligations = design_obligation_index(checkpoint, brief)
    assert obligations['ob01'].knowledge_changes[0].requires_released == ()
    assert obligations['ob02'].knowledge_changes[0].requires_released == ()
    assert obligations['ob03'].knowledge_changes[0].requires_released == ('晟儿',)
    assert checkpoint.obligations[0].anchors[0].basis_segments == ('e01_01',)
    assert checkpoint.obligations[2].anchors[0].basis_segments == ('e01_03',)


def test_cognition_anchor_quotes_require_their_own_evidence_location():
    data = {'handle': 'b01_01.c1', 'source_kind': 'cognition', 'holder': 'hero',
            'op': 'set', 'mode': 'learns', 'polarity': 'positive',
            'support': 'confirmed', 'reader_visible': True,
            'proposition_ref': 'p_identity', 'proposition_statement': '孩子名叫晟儿',
            'quotes': ['孩子名叫晟儿']}
    with pytest.raises(ValidationError, match='原子自身的证据定位'):
        ObligationAnchor.model_validate(data)
    assert ObligationAnchor.model_validate({**data, 'basis_segments': ['e01_03']})


@pytest.mark.parametrize('withdraw_last', [False, True])
def test_same_beat_correction_keeps_each_change_and_hook_uses_final_acceptance(withdraw_last):
    cognitions = [_cognition('believes'), _cognition(None, op='unset', polarity=None),
                  _cognition('learns', polarity='negative')]
    if withdraw_last:
        cognitions.append(_cognition(None, op='unset', polarity=None))
    chars, _, _, checkpoint, brief = _inputs([
        {'quote': f'第{n}次明确认知变化', 'cognition': [cognition]}
        for n, cognition in enumerate(cognitions, 1)])
    obligations = design_obligation_index(checkpoint, brief)
    changes = knowledge_change_index(obligations)
    wire = {'protocol_version': STAGE4_DESIGN_WIRE_PROTOCOL, 'book_style': '保留误信及纠正。',
            'scenes': [{'driver': {'focal_actor': 'hero', 'immediate_want': '核实',
                                   'opposition': '先前误会', 'turn': '修正认知'},
                        'brief': '同拍依序写出各次认知变化。', 'beats': [
                            {'ev': '主角经历完整的认知变化', 'who': ['hero'],
                             'real': list(obligations),
                             'know': [{'ref': ref} for ref in changes]}]}],
            'chapters': [{'purpose': '保留认知顺序', 'scenes': 1}]}
    body, errors = assemble_body_design(
        wire, known_char_ids=set(chars.characters), brief=brief, obligations=obligations,
        source_narration=brief.source_narration, expression_requirements=brief.expression)
    assert body is not None and errors == [], errors
    deltas = body.scenes[0].beats[0].target_delta
    assert [d.op for d in deltas] == ['set', 'unset', 'set'] + (['unset'] if withdraw_last else [])
    assert [d.value for d in deltas] == ['true', None, 'false'] + ([None] if withdraw_last else [])
    facts = hook_facts(body)
    last_set = next(f for f in reversed(facts) if f.delta.op == 'set')
    fact = established_fact(facts, last_set.ref, last_set.delta.address(),
                            fact_ref=last_set.fact_ref)
    assert fact is not None
    if withdraw_last:
        assert not fact_is_current(fact, facts, (0, 1)), '最后一次撤回后不再有当前接受'
    else:
        assert fact.delta.value == 'false' and fact_is_current(fact, facts, (0, 1))
