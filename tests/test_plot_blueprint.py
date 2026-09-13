"""完整剧情到两章呈现投影的反例。此模块不冒充正式流水线或模型质量验收。"""
from __future__ import annotations

import json

import pytest

from contracts.schema.plot import (
    PlotAnchorBinding, PlotBlueprint, PlotChapterArrangement, PlotChapterResponse,
    PlotGoalAnchor,
)
from novel.stages.stage2.plot_compile import (
    compile_chapter, validate_book, validate_evidence,
)
from novel.stages.stage4.plot_placements import (
    compile_placements,
)

CHARS = frozenset({'char_001', 'char_002'})
SOURCE = '我当初因对方富有且好看而救了他。\n\n今天他跪下挽留，我仍决定离开。'
CONTENT = '娘子回忆自己当初因对方富有且好看而施救。'


def chapter_response(ordinal: int = 1) -> dict:
    return {
        'chapter_ordinal': ordinal, 'chapter_function': '回顾施救动机并决定离开',
        'beats': [
            {'beat_key': 'b1', 'content': CONTENT,
             'participants': [{'char_id': 'char_001'}, {'char_id': 'char_002'}],
             'evidence': [{'start_para': 1, 'end_para': 1}],
             'source_contexts': [{
                 'evidence': [{'start_para': 1, 'end_para': 1}],
                 'time_layer': 'recall', 'place': None,
                 'present': [{'char_id': 'char_001'}, {'char_id': 'char_002'}],
             }]},
            {'beat_key': 'b2', 'content': '夫君恳切挽留，娘子仍决定离开。',
             'participants': [{'char_id': 'char_001'}, {'char_id': 'char_002'}],
             'evidence': [{'start_para': 2, 'end_para': 2}],
             'source_contexts': [{
                 'evidence': [{'start_para': 2, 'end_para': 2}],
                 'time_layer': 'present', 'place': None,
                 'present': [{'char_id': 'char_001'}, {'char_id': 'char_002'}],
             }]},
        ],
        'states': [], 'inherited_state_endings': [], 'constraints': [],
        'entry_state_refs': [], 'carry_out_refs': [],
        'narration': [{
            'grammatical_person': 'first', 'narrator': {'char_id': 'char_001'},
            'focalizer': {'char_id': 'char_001'}, 'pronoun_surfaces': ['我'],
            'beat_refs': [], 'evidence': [{'start_para': 1, 'end_para': 2}],
        }],
    }


def blueprint(ordinal: int = 1, response: dict | None = None) -> PlotBlueprint:
    return compile_chapter(
        PlotChapterResponse.model_validate(response or chapter_response(ordinal)), SOURCE,
        allowed_char_ids=CHARS)


def arrangement(ordinal: int = 1, *, distributed: bool = False) -> PlotChapterArrangement:
    prefix = f'{ordinal:02d}'
    scenes = [
        {'slot': 's01', 'purpose': '回顾施救', 'role': 'setup', 'delivery': 'scene',
         'transition': 'start', 'context_beat_refs': []},
        {'slot': 's02', 'purpose': '呈现挽留与离开', 'role': 'turn', 'delivery': 'scene',
         'transition': 'continue', 'context_beat_refs': []},
    ]
    second = [{'scene_ref': 's02', 'presentation_instruction': None,
               'evidence_refs': [f'e{prefix}_02']}]
    if distributed:
        second = [
            {'scene_ref': 's01', 'presentation_instruction': '以当下的恳切挽留承接回忆。',
             'evidence_refs': [f'e{prefix}_02']},
            {'scene_ref': 's02', 'presentation_instruction': '呈现娘子仍然决定离开。',
             'evidence_refs': [f'e{prefix}_02']},
        ]
    return PlotChapterArrangement.model_validate({
        'chapter_ordinal': ordinal, 'scenes': scenes,
        'beat_placements': [
            {'beat_ref': f'b{prefix}_01', 'deliveries': [
                {'scene_ref': 's01', 'presentation_instruction': None,
                 'evidence_refs': [f'e{prefix}_01']}]},
            {'beat_ref': f'b{prefix}_02', 'deliveries': second},
        ],
        'anchor_bindings': [],
    })


def formal_writer_view(chapter, arrangement, chapters=None):
    """让端点反例穿过正式 Plan/WriterContext，而非平行原型；知识材料已退役。"""
    from novel.stages.stage4.measure import identity_plan
    from novel.stages.stage4.plot_context import build_writer_context
    typed = list(chapters) if chapters is not None else [
        blueprint(n) for n in range(1, chapter.chapter_ordinal)]
    typed.append(chapter)
    bps = {'blueprints': [row.model_dump(mode='json') for row in typed]}
    main = [{'chapter_ordinal': n, 'text': SOURCE} for n in range(1, len(typed) + 1)]
    plan = identity_plan(bps, main)
    current = plan['output_chapters'][-1]
    contexts = {p['beat_ref']: p['deliveries'][0]['source_context_refs']
                for p in current['beat_placements']}
    current['beat_placements'] = [p.model_dump(mode='json') for p in arrangement.beat_placements]
    for p in current['beat_placements']:
        for delivery in p['deliveries']:
            delivery['source_context_refs'] = contexts[p['beat_ref']]
    current['anchor_bindings'] = [r.model_dump(mode='json') for r in arrangement.anchor_bindings]
    from tests.plot_fixtures import GLOBAL
    characters = {'characters': {
        cid: {'canonical': name, 'role': role, 'name_distinctiveness': 'high', 'first_chapter': 1,
              'aliases': [{'surface': name, 'identity_revealed_from': 1, 'evidence_refs': []}]}
        for cid, name, role in [('char_001', '我', '施救者'), ('char_002', '他', '被救者')]
    }, 'evidence_anchors': {}}
    return build_writer_context(plan, GLOBAL, bps, main,
                                characters=characters).ctx['chapters'][-1]


def test_two_chapter_compile_and_single_copy_writer_projection():
    chapters = [blueprint(1), blueprint(2)]
    validate_book(chapters, {1: SOURCE, 2: SOURCE})
    for chapter in chapters:
        view = formal_writer_view(chapter, arrangement(chapter.chapter_ordinal),
                                  chapters=chapters[:chapters.index(chapter)])
        wire = json.dumps(view, ensure_ascii=False)
        assert wire.count(view['beats'][0]['content']) == 1
        assert len(view['beats']) == 2
        assert 'fact_atoms' not in wire and 'required_facts' not in wire
        # 两个 Beat 各自一段；叙述覆盖两段，形成第三个合法范围。相同范围仍只编译一次。
        pairs = [(a.start_para, a.end_para) for a in chapter.evidence_anchors]
        assert len(pairs) == len(set(pairs)) == 3
        assert set(pairs) == {(1, 1), (2, 2), (1, 2)}
        assert chapter.narration[0].evidence_refs == [chapter.evidence_anchors[2].evidence_id]
        assert 'text' not in chapter.evidence_anchors[0].model_dump()
        assert chapter.beats[0].source_contexts[0].evidence_refs == chapter.beats[0].evidence_refs


def test_distributed_presentation_keeps_parent_and_allows_shared_paragraph():
    chapter = blueprint()
    before = chapter.model_dump_json()
    plan = arrangement(distributed=True)
    compiled = compile_placements(chapter, plan)
    assert chapter.model_dump_json() == before
    assert compiled.scene_beats['s01'] == ('b01_01', 'b01_02')
    assert compiled.completed_at['b01_02'] == 's02'
    assert compiled.shared_evidence == (('b01_02', 's01/s02', ('e01_02',)),)
    view = formal_writer_view(chapter, plan)
    assert sum(row['beat_ref'] == 'b01_02'
               for scene in view['scenes'] for row in scene['deliveries']) == 2


def test_partial_node_is_not_complete_background():
    plan = arrangement(distributed=True)
    plan.scenes[1].context_beat_refs.append('b01_02')
    with pytest.raises(ValueError, match='全部主要呈现'):
        compile_placements(blueprint(), plan)


def test_evidence_source_drift_and_coordinates_refused():
    chapter = blueprint()
    with pytest.raises(ValueError, match='原文已经变化'):
        validate_evidence(chapter, SOURCE + '额外内容')
    changed = chapter.model_dump()
    changed['evidence_anchors'][0]['start'] = 1
    with pytest.raises(ValueError, match='坐标'):
        validate_evidence(PlotBlueprint.model_validate(changed), SOURCE)


def test_unknown_character_and_unproven_entry_state_refused():
    response = chapter_response()
    response['beats'][0]['participants'].append({'char_id': 'char_999'})
    with pytest.raises(ValueError, match='未提供的人物'):
        blueprint(response=response)
    response = chapter_response()
    response['entry_state_refs'] = ['st99_01']
    with pytest.raises(ValueError, match='引用不存在'):
        blueprint(response=response)


def test_literal_is_evidenced_and_claim_is_not_world_state():
    response = chapter_response()
    response['constraints'] = [{
        'constraint_key': 'c1', 'kind': 'literal', 'target_beat_ref': 'b1',
        'value': '不曾说过的密码', 'evidence': [{'start_para': 1, 'end_para': 1}],
    }]
    with pytest.raises(ValueError, match='没有原文依据'):
        blueprint(response=response)
    response['constraints'] = []
    response['states'] = [{
        'state_key': 'st1', 'content': '夫君声称自己有钱。', 'global_ref': None,
        'modality': 'claim', 'holder': {'char_id': 'char_002'},
        'established_at': {'kind': 'at_beat', 'beat_ref': 'b2'}, 'ended_at': None,
        'supersedes_state_refs': [], 'evidence': [{'start_para': 2, 'end_para': 2}],
    }]
    response['carry_out_refs'] = ['st1']
    result = blueprint(response=response)
    assert result.states[0].modality == 'claim'
    response['states'][0]['holder'] = None
    with pytest.raises(ValueError, match='明确持有者'):
        blueprint(response=response)


def test_goals_require_correct_explicit_delivery():
    chapter, plan = blueprint(), arrangement(distributed=True)
    goal = PlotGoalAnchor(goal_ref='ending', kind='close', beat_ref='b01_02')
    with pytest.raises(ValueError, match='明确绑定'):
        compile_placements(chapter, plan, goals=[goal])
    plan.anchor_bindings.append(PlotAnchorBinding(
        kind='goal', ref='ending', endpoint='close', scene_ref='s01'))
    with pytest.raises(ValueError, match='结尾目标'):
        compile_placements(chapter, plan, goals=[goal])


def test_planner_view_does_not_include_source_order_or_coordinates():
    from novel.stages.stage4.plot_design import build_blind_projection
    from tests.test_plot_design import planner_characters
    truth, mapping = build_blind_projection({'blueprints': [blueprint().model_dump()]},
        root_clean_sha1='1' * 40, global_g=__import__('tests.plot_fixtures', fromlist=['GLOBAL']).GLOBAL,
        characters=planner_characters(), main_text=[{'chapter_ordinal': 1, 'text': SOURCE}])
    view = truth['chapters'][0]
    wire = json.dumps(view, ensure_ascii=False)
    assert 'source_contexts' not in wire
    assert 'evidence' not in wire
    assert 'b01_01' not in wire and 'char_001' not in wire
    assert next(k for k, v in mapping['subject_aliases'].items() if v['ref'] == 'b01_01').startswith('a_')
    assert [b['alias'] for b in view['beats']] == sorted(b['alias'] for b in view['beats'])


def test_formal_ids_do_not_reuse_retired_range():
    chapter = compile_chapter(PlotChapterResponse.model_validate(chapter_response()), SOURCE,
                              allowed_char_ids=CHARS, next_ids={'b': 8, 'st': 4, 'c': 3})
    assert [beat.beat_id for beat in chapter.beats] == ['b01_08', 'b01_09']

def test_inherited_state_can_end_without_rewriting_previous_chapter():
    first_response = chapter_response()
    first_response['states'] = [{
        'state_key': 'st1', 'content': '娘子仍决定离开。', 'global_ref': None,
        'modality': 'world', 'holder': None,
        'established_at': {'kind': 'at_beat', 'beat_ref': 'b2'}, 'ended_at': None,
        'supersedes_state_refs': [], 'evidence': [{'start_para': 2, 'end_para': 2}],
    }]
    first_response['carry_out_refs'] = ['st1']
    first = blueprint(response=first_response)
    original = first.model_dump_json()
    second_response = chapter_response(2)
    second_response['entry_state_refs'] = ['st01_01']
    second_response['inherited_state_endings'] = [{
        'state_ref': 'st01_01', 'at_beat_ref': 'b2',
        'evidence': [{'start_para': 2, 'end_para': 2}],
    }]
    second = compile_chapter(
        PlotChapterResponse.model_validate(second_response), SOURCE,
        allowed_char_ids=CHARS, allowed_entry_state_refs=frozenset(first.carry_out_refs))
    validate_book([first, second], {1: SOURCE, 2: SOURCE})
    assert first.model_dump_json() == original
    assert not second.carry_out_refs
    plan_raw = arrangement(distributed=True).model_dump(mode='json')
    plan_raw['chapter_ordinal'] = 2
    for row in plan_raw['beat_placements']:
        row['beat_ref'] = row['beat_ref'].replace('b01_', 'b02_')
        for delivery in row['deliveries']:
            delivery['evidence_refs'] = [ref.replace('e01_', 'e02_')
                                         for ref in delivery['evidence_refs']]
    plan = PlotChapterArrangement.model_validate(plan_raw)
    with pytest.raises(ValueError, match='明确绑定'):
        compile_placements(second, plan)
    plan.anchor_bindings.append(PlotAnchorBinding(
        kind='state', ref='st01_01', endpoint='ended', scene_ref='s02'))
    assert compile_placements(second, plan).bindings[0].scene_ref == 's02'
    from novel.stages.stage4.plot_design import build_blind_projection
    from tests.test_plot_design import planner_characters
    truth, mapping = build_blind_projection({'blueprints': [first.model_dump(), second.model_dump()]},
        root_clean_sha1='2' * 40, global_g=__import__('tests.plot_fixtures', fromlist=['GLOBAL']).GLOBAL,
        characters=planner_characters(), main_text=[{'chapter_ordinal': n, 'text': SOURCE} for n in (1, 2)])
    view = truth['chapters'][1]
    assert 'st01_01' not in json.dumps(view)
    assert next(k for k, v in mapping['subject_aliases'].items() if v['ref'] == 'st01_01').startswith('a_')


def test_plot_response_projection_keeps_semantics_and_shared_paragraph_evidence():
    from novel.stages.stage2.plot_compile import response_projection
    compiled = blueprint()
    wire = response_projection(compiled.model_dump(mode='json'), SOURCE)
    restored = compile_chapter(
        PlotChapterResponse.model_validate(wire), SOURCE, allowed_char_ids=CHARS)
    assert restored == compiled
    assert 'source' not in wire and 'evidence_anchors' not in wire


def test_response_projection_does_not_rewrite_prose_matching_a_formal_id():
    from novel.stages.stage2.plot_compile import response_projection
    chapter = blueprint().model_dump(mode='json')
    # 人物说出编号等内容时，正文和地点恰好等于标识也仍然是原文语义。
    chapter['beats'][0]['content'] = 'b01_01'
    chapter['beats'][0]['source_contexts'][0]['place'] = 'b01_02'
    chapter['chapter_function'] = 'b01_01'
    wire = response_projection(chapter, SOURCE)
    assert wire['beats'][0]['beat_key'] == 'b1'
    assert wire['beats'][0]['content'] == 'b01_01'
    assert wire['beats'][0]['source_contexts'][0]['place'] == 'b01_02'
    assert wire['chapter_function'] == 'b01_01'
