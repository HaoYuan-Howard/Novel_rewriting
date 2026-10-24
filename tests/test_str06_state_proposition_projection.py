"""机器状态命题到目标名字的确定性投影；不购买语义、不改源审计真源。"""
from __future__ import annotations

from copy import deepcopy
import json

import pytest

from contracts.schema.design import STAGE4_DESIGN_WIRE_PROTOCOL
from contracts.schema.naming import NameAssignment, NameMapV6
from contracts.schema.source import STATE_KEY_LABELS, REGISTERED_STATE_KEYS
from contracts.schema.workflow import STAGE4_HOOK_DESIGN_PROTOCOL, STAGE4_PRESERVE_PROTOCOL
from novel.core.name_materials import appellation_fallbacks, target_face_table
from novel.core.state_proposition import state_proposition_text, state_statement
from novel.stages.common import (
    hook_material_issues, material_identities, project_chapter_packet,
    render_chapter_packet, render_hook_context, writer_material_issues,
)
from novel.stages.stage2 import compile_chapter_observation, compile_source_truth
from novel.stages.stage4.book_ir import compile_writer_book_ir
from novel.stages.stage4.compile import compile_execution_plan
from novel.stages.stage4.design import assemble_body_design
from novel.stages.stage4.grounding import (
    assemble_preserve, derive_semantic_binding, design_obligation_index, project_brief,
)
from novel.stages.stage4.hook import assemble_hook_design, publish_graph
from novel.stages.stage4.hook_context import compile_hook_context
from novel.stages.stage4.repair import plan_material_repairs
from novel.stages.stage4.slim import design_placements, design_wire_model, knowledge_change_index
from tests.str06_facilities import complete_associate_fixture
from tests.test_str06_identity_cognition import _typed_case


def _source(value='背上有印记', *, at='sheng_er', key='body_state'):
    chapters, wires, evidence, book, observations, names, associate = _typed_case()
    wire = deepcopy(wires['ch07'])
    wire['beats'][0]['change'][0].update(at=at, key=key, val=value)
    observation, errors = compile_chapter_observation(
        wire, evidence_ids=evidence, chapter=7, character_slugs=book.characters,
        fact_ids=[fact.fact_id for fact in book.facts])
    assert observation is not None and errors == [], errors
    observations = {**observations, 7: observation}
    outcome = compile_source_truth(
        book=book, chapters=observations, names=names, associate=associate, evidence=evidence,
        source_texts=tuple(chapter['text'] for chapter in chapters))
    assert outcome.publishable, (outcome.errors, outcome.undecided)
    return outcome.characters, outcome.blueprint


def _characters(chars, *, fallback=False):
    """显式 fixture 登记姓名构词；两个孩子仍是各自身份，不造全书唯一孩子别名。"""
    data = chars.model_dump(mode='json')
    person = data['name_structure']['people']['sheng_er']
    person.update(given='晟儿', surface_forms={'晟儿': '{given}'})
    if fallback:
        # 语义同一性由 fixture 明示；投影代码不从描述猜出该绑定。
        data['characters']['sheng_er']['surfaces'].append(
            {'surface': '孩子', 'from_chapter': 7, 'source_refs': ['e07_01']})
        person['surface_forms']['孩子'] = None
    return type(chars).model_validate(data)


def _brief(chars, blueprint, *, close_only=False):
    name_map = NameMapV6(entries=tuple(
        NameAssignment(char_ref=slug, name={'given': '小满', 'full': '小满'})
        if slug == 'sheng_er' else
        NameAssignment(char_ref=slug, name=None, keep_reason='保留当前称谓')
        for slug in chars.characters))
    beat = blueprint.chapters[6].beats[0]
    if close_only:
        index = next(n for n, action in enumerate(beat.loops, 1) if action.op == 'close')
        anchor = f'{beat.beat_id}.l{index}'
    else:
        index = next(n for n, c in enumerate(beat.cognition, 1) if c.holder == 'reader')
        anchor = f'{beat.beat_id}.c{index}'
    checkpoint, errors = assemble_preserve({
        'protocol_version': STAGE4_PRESERVE_PROTOCOL, 'obligations': [{
            'kind': 'reveal', 'statement': '读者得知眼前的状态',
            'anchors': [anchor], 'must_preserve': True,
            'satisfaction': '保住状态的主体和值及其披露', 'requires': []}], 'series': []},
        policy_id='test', policy_version=13, characters=chars.core_view(), blueprint=blueprint,
        name_map=name_map, name_map_sha256='n' * 64, provider='fake', model='fake',
        request_sha256='r' * 64)
    assert checkpoint is not None and errors == [], errors
    brief, _ = project_brief(checkpoint, chars.core_view(), blueprint,
                             structure=chars.name_structure, name_map=name_map)
    return checkpoint, brief, name_map


def _consumers(chars, blueprint, *, reveal_name=True, close_resolution=None):
    checkpoint, brief, name_map = _brief(chars, blueprint, close_only=close_resolution is not None)
    obligations = design_obligation_index(checkpoint, brief)
    ref = next(iter(knowledge_change_index(obligations)), None)
    effect = blueprint.chapters[6].beats[0].effects[0]
    target = effect.target
    subjects = ([target.char_id] if target.kind == 'character' else
                [target.subject, target.object] if target.kind == 'relation' else [])
    domain = 'character' if target.kind == 'character' else target.kind
    releases = [{'char_id': 'yun_niang', 'surfaces': ['云娘']}]
    if reveal_name:
        releases.append({'char_id': 'sheng_er', 'surfaces': ['晟儿']})
    elif '孩子' in chars.name_structure.people['sheng_er'].surface_forms:
        releases.append({'char_id': 'sheng_er', 'surfaces': ['孩子']})

    def state_row(atom):
        target = atom.target
        at = (target.char_id if target.kind == 'character' else
              f'{target.subject}+{target.object}' if target.kind == 'relation' else 'world')
        return {'at': at, 'key': atom.key,
                'val': json.dumps(atom.value, ensure_ascii=False, sort_keys=True)}

    wire = {'protocol_version': STAGE4_DESIGN_WIRE_PROTOCOL, 'book_style': '平实呈现状态及来由。',
            'initial_surfaces': releases,
            'scenes': [{'driver': {'focal_actor': 'yun_niang', 'immediate_want': '核实所见',
                                   'opposition': '需要观察', 'turn': '看清状态'},
                        'brief': '状态由眼前行动自然呈现。', 'beats': [
                            {'ev': close_resolution or '眼前状态得到确认', 'who': ['yun_niang', 'sheng_er'],
                             'real': ['ob01'], 'state': [state_row(atom) for atom in
                                                       blueprint.chapters[6].beats[0].effects],
                             'know': [{'ref': ref, 'confirms': {'domain': domain,
                                        'subjects': subjects, 'key': effect.key}}] if ref else [],
                             'threads': ([{'op': 'open', 'tag': 'answer', 'kind': 'question',
                                           'subject': 'yun_niang', 'text': '眼前的状态是什么？'},
                                          {'op': 'close', 'ref': 'answer', 'ending': 'answered',
                                           'resolution': close_resolution}]
                                         if close_resolution is not None else [])},
                            {'ev': '照看之事继续展开', 'who': ['yun_niang', 'sheng_er']},
                        ]}], 'chapters': [{'purpose': '观察后继续行动', 'scenes': 1}]}
    body, errors = assemble_body_design(
        wire, known_char_ids=set(chars.characters), brief=brief, obligations=obligations,
        source_narration=brief.source_narration, expression_requirements=brief.expression)
    assert body is not None and errors == [], errors
    binding, errors = derive_semantic_binding(
        design_placements(design_wire_model(wire, brief)), body, checkpoint)
    assert binding is not None and errors == [], errors
    plan, errors = compile_execution_plan(body, binding)
    assert plan is not None and errors == [], errors
    statements = {p.kp_id: p.statement for p in brief.propositions}
    ir = compile_writer_book_ir(
        graph=body, plan=plan, binding=binding, total_budget=1200, length_band=(0.8, 1.2),
        projection=chars.name_projection(), structure=chars.name_structure, name_map=name_map,
        display_fallback=appellation_fallbacks(chars.core_view(), chars.name_structure),
        kp_statements=statements)
    packet = project_chapter_packet(ir, 1)
    identities = material_identities(body, binding, chars.core_view())
    faces, errors = target_face_table(chars.name_structure, name_map)
    assert errors == [], errors
    hook, errors = assemble_hook_design({
        'protocol_version': STAGE4_HOOK_DESIGN_PROTOCOL, 'pov': {'person': 'third', 'focalizers': ['narrator']},
        'materials': [{'beat_ref': {'scene': 'sc01', 'beat': 'b01'}, 'emphasis': 'develop', 'transition': 'start'}],
        'surface_releases': releases,
        'reader_expectation': {'kind': 'action', 'focus': '接下来如何照看？',
                               'followup_refs': [{'scene': 'sc01', 'beat': 'b02'}]},
        'local_focus': '以观察推动后续照看，不额外解释。'},
        body=body, known_char_ids=set(chars.characters), name_projection=chars.name_projection(),
        name_structure=chars.name_structure, faces=faces)
    assert hook is not None and errors == [], errors
    hook_context, errors = compile_hook_context(
        publish_graph(body, hook), chars.name_projection(), chars.name_structure,
        kp_statements=statements, characters=chars.core_view(), total_budget=1200, name_map=name_map)
    assert hook_context is not None and errors == [], errors
    return (checkpoint, brief, packet, writer_material_issues(packet, internal_ids=identities),
            hook_context, hook_material_issues(hook_context, internal_ids=identities), body, binding)


def test_state_key_display_labels_cover_exact_current_registry_and_unknowns_fail():
    assert set(STATE_KEY_LABELS) == set(REGISTERED_STATE_KEYS)
    with pytest.raises(ValueError, match='显示标签未登记'):
        state_proposition_text({'target': {'kind': 'world'}, 'key': 'invented', 'value': 0}, names={})
    with pytest.raises(ValueError, match='目标姓名缺失'):
        state_proposition_text({'target': {'kind': 'character', 'char_id': 'absent'},
                                'key': 'health', 'value': False}, names={})


@pytest.mark.parametrize('value', [0, False, '', [], {}])
@pytest.mark.parametrize('at,key,subject', [
    ('sheng_er', 'body_state', '小满'),
    ('yun_niang+sheng_er', 'relationship', '云娘对小满'),
    ('world', 'health', '世界'),
])
def test_stage2_to_body_and_hook_material_gates_keep_structural_subject_and_json_value(value, at, key, subject):
    chars, blueprint = _source(value, at=at, key=key)
    chars = _characters(chars)
    audit = blueprint.chapters[6].beats[0].cognition[-1].proposition.statement
    checkpoint, brief, packet, body_issues, hook_context, hook_issues, _body, _binding = _consumers(chars, blueprint)
    value_json = json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(',', ':'))
    expected = f'{subject}的{STATE_KEY_LABELS[key]}：{value_json}'
    assert brief.propositions[0].statement == expected
    assert checkpoint.obligations[0].anchors[0].proposition_statement == audit
    assert blueprint.chapters[6].beats[0].cognition[-1].proposition.statement == audit
    assert expected in render_chapter_packet(packet)
    assert expected in render_hook_context(hook_context)
    assert body_issues == () and hook_issues == ()


def test_unreleased_target_name_projects_to_registered_context_appellation_without_early_name():
    chars, blueprint = _source()
    chars = _characters(chars, fallback=True)
    _checkpoint, brief, packet, body_issues, hook_context, hook_issues, _body, _binding = _consumers(
        chars, blueprint, reveal_name=False)
    assert '小满' in brief.propositions[0].statement
    for rendered in (render_chapter_packet(packet), render_hook_context(hook_context)):
        assert '孩子的身体与外观："背上有印记"' in rendered
        assert '小满' not in rendered and 'sheng_er' not in rendered
    assert body_issues == () and hook_issues == ()


def test_ordinary_natural_proposition_is_unchanged_and_cross_beat_effect_is_not_borrowed():
    chars, blueprint = _source()
    chars = _characters(chars)
    raw = blueprint.model_dump(mode='json')
    raw['chapters'][6]['beats'][0]['cognition'][-1]['proposition']['statement'] = '孩子背上有印记'
    altered = type(blueprint).model_validate(raw)
    _checkpoint, brief, _names = _brief(chars, altered)
    assert brief.propositions[0].statement == '孩子背上有印记'
    raw['chapters'][6]['beats'][0]['effects'][0]['reader_visible'] = False
    raw['chapters'][6]['beats'][0]['cognition'][-1]['proposition']['statement'] = state_statement(
        raw['chapters'][7]['beats'][0]['effects'][0])
    altered = type(blueprint).model_validate(raw)
    _checkpoint, brief, _names = _brief(chars, altered)
    assert brief.propositions[0].statement.startswith('char:sheng_er.'), '不能借后拍事实认领本拍机器命题'


def test_no_available_appellation_blocks_without_early_name_or_paid_source_repair():
    chars, blueprint = _source()
    chars = _characters(chars)
    _checkpoint, brief, packet, body_issues, hook_context, hook_issues, body, binding = _consumers(
        chars, blueprint, reveal_name=False)
    assert body_issues and hook_issues
    assert all(issue.literal == '小满' and issue.code == 'positive_material_leak'
               for issue in (*body_issues, *hook_issues))
    assert 'sheng_er' not in render_chapter_packet(packet)
    assert 'sheng_er' not in render_hook_context(hook_context)
    repairs = plan_material_repairs(body_issues, body=body, binding=binding,
                                    brief=brief, structure=chars.name_structure)
    assert all(repair.action == 'stop' for repair in repairs), '没有可用显示面不能删除主体或购买源观察来迁就'


def test_json_zero_and_false_do_not_join_as_the_same_fact():
    chars, blueprint = _source(0)
    chars = _characters(chars)
    raw = blueprint.model_dump(mode='json')
    false_atom = {**raw['chapters'][6]['beats'][0]['effects'][0], 'value': False}
    false_statement = state_statement(false_atom)
    raw['chapters'][6]['beats'][0]['cognition'][-1]['proposition']['statement'] = false_statement
    _checkpoint, brief, _names = _brief(chars, type(blueprint).model_validate(raw))
    assert brief.propositions[0].statement == false_statement, 'Python False==0 不构成机器命题全等'


def test_equal_values_on_different_people_keep_different_readable_subjects():
    names = {'sheng_er': '小满', 'ning_er': '小宁'}
    atoms = [{'target': {'kind': 'character', 'char_id': subject}, 'key': 'body_state', 'value': False}
             for subject in names]
    assert [state_proposition_text(atom, names=names) for atom in atoms] == [
        '小满的身体与外观：false', '小宁的身体与外观：false']


@pytest.mark.parametrize('answers', ['effect', 'mixed', 'two_subjects'])
def test_closed_loop_machine_answer_stays_audit_and_semantic_target_materials_do_not_leak(answers):
    chapters, wires, evidence, book, observations, names, associate = _typed_case()
    wire = deepcopy(wires['ch07'])
    beat = wire['beats'][0]
    answer_refs = [{'kind': 'change', 'index': 1}]
    expected = '小满的身体与外观："背上有印记"'
    if answers == 'mixed':
        beat['know'] = [{'who': 'yun_niang', 'op': 'set', 'how': 'learns',
                         'polarity': 'negative', 'support': 'confirmed', 'reader_visible': True,
                         'what': '云娘没有看见印记', 'evidence_refs': ['e07_01']}]
        answer_refs.append({'kind': 'know', 'index': 1})
        expected += '；非（云娘没有看见印记）'
    elif answers == 'two_subjects':
        # 明示两个人物的同值状态输入；这里只验证结构和消费，不标定抽取质量。
        beat['change'].append({**beat['change'][0], 'at': 'yun_niang'})
        answer_refs.append({'kind': 'change', 'index': 2})
        expected += '；云娘的身体与外观："背上有印记"'
    beat['loops'] = [
        {'ref': 'q_marks', 'op': 'open', 'question': '眼前的状态是什么？', 'answer_refs': []},
        {'ref': 'q_marks', 'op': 'close', 'question': None, 'answer_refs': answer_refs}]
    observation, errors = compile_chapter_observation(
        wire, evidence_ids=evidence, chapter=7, character_slugs=book.characters,
        fact_ids=[fact.fact_id for fact in book.facts])
    assert observation is not None and errors == [], errors
    observations = {**observations, 7: observation}
    associate = complete_associate_fixture(book, observations, associate)
    outcome = compile_source_truth(
        book=book, chapters=observations, names=names, associate=associate, evidence=evidence,
        source_texts=tuple(chapter['text'] for chapter in chapters))
    assert outcome.publishable, (outcome.errors, outcome.undecided)
    chars, blueprint = _characters(outcome.characters), outcome.blueprint
    close = blueprint.chapters[6].beats[0].loops[-1]
    audit = close.resolution
    assert audit is not None and 'char:sheng_er.body_state' in audit
    if answers == 'mixed':
        assert '非（云娘没有看见印记）' in audit
    elif answers == 'two_subjects':
        assert 'char:yun_niang.body_state' in audit
    checkpoint, brief, packet, body_issues, hook_context, hook_issues, _body, _binding = _consumers(
        chars, blueprint, close_resolution=expected)
    assert checkpoint.obligations[0].anchors[0].question == audit
    assert brief.propositions == () and brief.knowledge_locks == () and brief.fact_locks == ()
    assert brief.requirements[0].knowledge_changes == (), 'close 审计串不能自行生成认知'
    for rendered in (render_chapter_packet(packet), render_hook_context(hook_context)):
        assert expected in rendered
        assert 'char:sheng_er' not in rendered and 'char:yun_niang' not in rendered
    assert body_issues == () and hook_issues == ()
    assert close.resolution == audit, '目标转述和材料投影不得改写源审计答案'
