"""Stage2 身份、命题与认知边界：显式当前协议的红绿例及真实发布链。

模型替身只给定观察和窄关联答案；人物闭集、原文证据、答案引用、读者派生、
双真源发布与 fold 均由生产代码执行。语义误绑是否属同一人物仍保持 unver，
本文件不把引用合法冒充成语义正确性证明。
"""
from __future__ import annotations

from copy import deepcopy
import json

import pytest

from contracts.schema.observation import (
    AssociateObservation, STAGE2_ASSOCIATE_PROTOCOL, STAGE2_BOOK_WIRE_PROTOCOL,
    STAGE2_CHAPTERS_WIRE_PROTOCOL, STAGE2_CROSS_CAUSE_WIRE_PROTOCOL,
    STAGE2_NAMES_WIRE_PROTOCOL,
)
from novel.core.state_proposition import state_statement
from novel.llm.ledger import CostLedger
from novel.stages.chain import build_evidence, run_workflow
from novel.stages.stage2 import (
    book_patch_locate, chapter_observation_prompt, chapter_patch_locate,
    character_core_view, compile_book_observation, compile_chapter_observation,
    compile_source_truth, slim_chapter_wire,
)
from novel.stages.stage2.chapters import chapter_facts_block
from novel.stages.stage2.associate import project_association_space
from novel.stages.stage2.names import compile_names_observation
from novel.stages.stage2.slim import loop_answer_resolution
from novel.store import Str06Repository
from tests.str06_expression_inputs import sample_book_expression
from tests.str06_facilities import (
    complete_associate_fixture, make_context, synthetic_ref,
)


STAGE2_NODES = [
    'str06.stage2.book', 'str06.stage2.chapters', 'str06.stage2.names',
    'str06.stage2.associate', 'str06.stage2.compile',
]


def _know(ref: str, evidence_ref: str, *, mode: str | None = 'believes',
          polarity: str | None = 'positive', support: str = 'inference',
          visible: bool = True, op: str = 'set') -> dict:
    return {'who': 'yun_niang', 'op': op, 'how': mode, 'polarity': polarity,
            'support': support, 'reader_visible': visible, 'fact': ref,
            'evidence_refs': [evidence_ref]}


def _case() -> tuple[list[dict], dict[str, dict], dict]:
    """17 章合成原文：7／8 章婴儿，10 章命名，11 章误信，17 章纠正。"""
    texts = {number: '云娘静待天明。' for number in range(1, 18)}
    texts.update({
        1: '云娘姓云，望着阿姐。',
        7: '云娘抱起孩子，看见孩子背上的印记。',
        8: '云娘怀中的孩子背上有印记，邻居抱着的另一个孩子没有印记。',
        10: '云娘给怀中这个孩子取名晟儿。',
        11: '云娘以为那个男子是阿姐送来的，因此决定照看他。',
        12: '邻居给另一个孩子取名宁儿。',
        17: '阿姐说：“那男子不是我送来的。”\n云娘收回先前的猜测。\n'
            '门牌和来路都对上了，云娘终于确认自己走错了巷子，那男子并非阿姐送来。',
    })
    chapters = [{'chapter_ordinal': number, 'text': texts[number]}
                for number in range(1, 18)]
    evidence = build_evidence(chapters, '')
    characters = {
        'yun_niang': {'canonical': '云娘', 'surfaces': [
            {'surface': '云娘', 'source_refs': ['e01_01']}], 'identity_bindings': []},
        'a_jie': {'canonical': '阿姐', 'surfaces': [
            {'surface': '阿姐', 'source_refs': ['e01_01']}], 'identity_bindings': []},
        'sheng_er': {'canonical': '晟儿', 'surfaces': [
            {'surface': '晟儿', 'source_refs': ['e10_01']}],
            'identity_bindings': [{'description': '云娘怀中被称为孩子的婴儿',
                                   'appearance_refs': ['e07_01', 'e08_01'],
                                   'identity_refs': ['e10_01']}]},
        'ning_er': {'canonical': '宁儿', 'surfaces': [
            {'surface': '宁儿', 'source_refs': ['e12_01']}],
            'identity_bindings': [{'description': '邻居抱着的另一个孩子',
                                   'appearance_refs': ['e08_01'],
                                   'identity_refs': ['e12_01']}]},
    }
    book = {**sample_book_expression(characters),
            'protocol_version': STAGE2_BOOK_WIRE_PROTOCOL,
            'characters': characters, 'initial_state': [], 'world_constraints': [],
            'facts': [
                {'statement': '男子由阿姐送来', 'known_at_start': [],
                 'evidence': [
                     {'kind': 'inference', 'polarity': 'positive', 'holder': 'yun_niang',
                      'evidence_refs': ['e11_01']},
                     {'kind': 'claim', 'polarity': 'negative', 'holder': 'a_jie',
                      'evidence_refs': ['e17_01']},
                     {'kind': 'confirmed', 'polarity': 'negative', 'holder': None,
                      'evidence_refs': ['e17_03']}]},
                {'statement': '云娘走错巷子', 'known_at_start': [],
                 'evidence': [{'kind': 'confirmed', 'polarity': 'positive',
                               'holder': None, 'evidence_refs': ['e17_03']}]},
            ]}
    wires = {'book': book}
    for number in range(1, 18):
        beat = {'event': texts[number], 'on_stage': ['yun_niang'],
                'start': f'e{number:02d}_01', 'location_reader_visible': False,
                'change': [], 'know': [], 'loops': []}
        if number == 1:
            beat['new_scene'] = True
        wires[f'ch{number:02d}'] = {
            'protocol_version': STAGE2_CHAPTERS_WIRE_PROTOCOL, 'beats': [beat]}
    for number in (7, 8):
        beat = wires[f'ch{number:02d}']['beats'][0]
        beat['on_stage'].append('sheng_er')
        beat['change'] = [{'at': 'sheng_er', 'key': 'body_state',
                           'val': '背上有印记', 'reader_visible': True,
                           'evidence_refs': [f'e{number:02d}_01']}]
    wires['ch08']['beats'][0]['on_stage'].append('ning_er')
    wires['ch10']['beats'][0]['on_stage'].append('sheng_er')
    wires['ch12']['beats'][0]['on_stage'].append('ning_er')
    wires['ch11']['beats'][0]['know'] = [_know('f01', 'e11_01')]
    wires['ch11']['beats'][0]['loops'] = [
        {'ref': 'q_arrival', 'op': 'open', 'question': '男子是阿姐送来的吗？',
         'answer_refs': []}]
    wires['ch17']['beats'] = [
        {'event': '阿姐否认送来男子，云娘听到她的说法。',
         'on_stage': ['yun_niang', 'a_jie'], 'start': 'e17_01', 'change': [],
         'location_reader_visible': False,
         'know': [_know('f01', 'e17_01', mode='hears_claim', polarity='negative',
                        support='claim')], 'loops': []},
        {'event': '云娘收回原先的误信。', 'on_stage': ['yun_niang'],
         'start': 'e17_02', 'change': [],
         'location_reader_visible': False,
         'know': [_know('f01', 'e17_02', op='unset', mode=None, polarity=None,
                        support='confirmed')], 'loops': []},
        {'event': '云娘核对来路，确认走错巷子且男子不是阿姐送来。',
         'on_stage': ['yun_niang'], 'start': 'e17_03', 'change': [],
         'location_reader_visible': False,
         'know': [_know('f01', 'e17_03', mode='learns', polarity='negative',
                        support='confirmed'),
                  _know('f02', 'e17_03', mode='learns', support='confirmed')],
         'loops': [{'ref': 'q_arrival_answer', 'op': 'close', 'question': None,
                    'answer_refs': [{'kind': 'know', 'index': 1}]}]},
    ]
    wires['names'] = {'protocol_version': STAGE2_NAMES_WIRE_PROTOCOL,
                      'families': {'f_yun': {'literal': '云', 'forms': []}},
                      'people': {slug: {'family_id': 'f_yun' if slug == 'yun_niang' else None,
                                        'given': None, 'aliases': [],
                                        'additional_surfaces': []}
                                 for slug in characters}}
    return chapters, wires, evidence


def _typed_case():
    chapters, wires, evidence = _case()
    book, errors = compile_book_observation(wires['book'], evidence=evidence)
    assert book is not None and not errors, errors
    observations = {}
    for number in range(1, 18):
        observation, errors = compile_chapter_observation(
            wires[f'ch{number:02d}'], evidence_ids=evidence, chapter=number,
            character_slugs=book.characters, fact_ids=[fact.fact_id for fact in book.facts])
        assert observation is not None and not errors, errors
        observations[number] = observation
    core, errors = character_core_view(book, evidence=evidence)
    assert core is not None and not errors, errors
    names, _extended, errors = compile_names_observation(
        wires['names'], core, '\n'.join(chapter['text'] for chapter in chapters),
        evidence=evidence)
    assert names is not None and not errors, errors
    associate = complete_associate_fixture(
        book, observations, AssociateObservation(
            protocol_version=STAGE2_ASSOCIATE_PROTOCOL,
            loop_pairing=[{'open': 'ch11.q_arrival', 'close': 'ch17.q_arrival_answer',
                           'verdict': 'pair'}]))
    return chapters, wires, evidence, book, observations, names, associate


def _compiled_case():
    chapters, wires, evidence, book, observations, names, associate = _typed_case()
    outcome = compile_source_truth(book=book, chapters=observations, names=names,
                                   associate=associate, evidence=evidence,
                                   source_texts=tuple(chapter['text'] for chapter in chapters))
    assert outcome.publishable, (outcome.errors, outcome.undecided)
    return outcome


def test_late_name_early_identity_and_same_child_word_keep_distinct_people():
    outcome = _compiled_case()
    baby = outcome.characters.characters['sheng_er']
    other = outcome.characters.characters['ning_er']
    assert baby.surfaces[0].from_chapter == 10
    assert other.surfaces[0].from_chapter == 12
    assert baby.identity_bindings[0].appearance_refs == ('e07_01', 'e08_01')
    assert other.identity_bindings[0].appearance_refs == ('e08_01',)
    early = outcome.blueprint.chapters[6:8]
    assert all('sheng_er' in chapter.beats[0].participants for chapter in early)
    assert {'sheng_er', 'ning_er'} <= set(early[1].beats[0].participants)
    assert all(effect.target.char_id == 'sheng_er'
               for chapter in early for effect in chapter.beats[0].effects)
    assert outcome.fold['characters']['sheng_er']['appearance_chapters'] == [7, 8, 10]


@pytest.mark.parametrize('field', ['appearance_refs', 'identity_refs'])
def test_identity_binding_bad_evidence_is_rejected_and_locatable(field):
    _chapters, wires, evidence = _case()
    good, errors = compile_book_observation(wires['book'], evidence=evidence)
    assert good is not None and not errors
    bad = deepcopy(wires['book'])
    bad['characters']['sheng_er']['identity_bindings'][0][field] = ['e99_01']
    observation, errors = compile_book_observation(bad, evidence=evidence)
    assert observation is None and any('e99_01' in str(error) for error in errors)
    locations = book_patch_locate(bad, errors, evidence=evidence)
    assert locations and locations[0][0][:2] == ('characters', 'sheng_er')


def test_unknown_baby_participant_rejects_without_deleting_real_baby():
    _chapters, wires, evidence = _case()
    wire = deepcopy(wires['ch07'])
    wire['beats'][0]['on_stage'].append('ghost_baby')
    options = dict(evidence_ids=evidence, chapter=7,
                   character_slugs=wires['book']['characters'], fact_ids=['f01', 'f02'])
    observation, errors = compile_chapter_observation(wire, **options)
    assert observation is None and any('ghost_baby' in str(error) for error in errors)
    assert chapter_patch_locate(wire, errors, **options)
    wire['beats'][0]['on_stage'].remove('ghost_baby')
    accepted, errors = compile_chapter_observation(wire, **options)
    assert accepted is not None and not errors
    assert 'sheng_er' in accepted.beats[0].participants


def test_valid_identity_refs_do_not_pretend_to_prove_semantic_coreference():
    """反例边界：描述与原文指代冲突归语义判断，compiler 不按字面猜人物。"""
    _chapters, wires, evidence = _case()
    wire = deepcopy(wires['book'])
    wire['characters']['ning_er']['identity_bindings'][0]['description'] = '云娘怀中的孩子'
    observation, errors = compile_book_observation(wire, evidence=evidence)
    assert observation is not None and not errors
    assert observation.characters['ning_er'].identity_bindings[0].description == '云娘怀中的孩子'


@pytest.mark.parametrize('support,accepted_mode', [
    ('inference', 'believes'), ('claim', 'hears_claim'),
])
def test_unconfirmed_learns_rejects_with_locatable_green_mode(support, accepted_mode):
    _chapters, wires, evidence = _case()
    wire = deepcopy(wires['ch11'])
    wire['beats'][0]['know'][0].update(how='learns', support=support)
    options = dict(evidence_ids=evidence, chapter=11,
                   character_slugs=wires['book']['characters'], fact_ids=['f01', 'f02'])
    observation, errors = compile_chapter_observation(wire, **options)
    assert observation is None and any('confirmed' in str(error) for error in errors)
    assert chapter_patch_locate(wire, errors, **options)
    wire['beats'][0]['know'][0]['how'] = accepted_mode
    accepted, errors = compile_chapter_observation(wire, **options)
    assert accepted is not None and not errors, errors


@pytest.mark.parametrize('mode,support,visible', [
    ('believes', 'inference', True), ('hears_claim', 'claim', True),
    ('learns', 'confirmed', False),
])
def test_loop_answer_requires_visible_confirmation_with_green_control(mode, support, visible):
    _chapters, wires, evidence = _case()
    wire = deepcopy(wires['ch17'])
    wire['beats'][-1]['know'][0].update(how=mode, support=support, reader_visible=visible)
    options = dict(evidence_ids=evidence, chapter=17,
                   character_slugs=wires['book']['characters'], fact_ids=['f01', 'f02'])
    observation, errors = compile_chapter_observation(wire, **options)
    assert observation is None and any('答案' in str(error) for error in errors)
    assert chapter_patch_locate(wire, errors, **options)
    wire['beats'][-1]['know'][0].update(how='learns', support='confirmed', reader_visible=True)
    accepted, errors = compile_chapter_observation(wire, **options)
    assert accepted is not None and not errors, errors


def test_book_projection_keeps_future_confirmation_and_identity_evidence_out_of_chapter():
    chapters, _wires, evidence, book, _observations, _names, _associate = _typed_case()
    block = chapter_facts_block(book, chapter=11)
    assert 'e11_01' in block and 'e17_01' not in block and 'e17_03' not in block
    first = json.loads(next(line.split('] ', 1)[1] for line in block.splitlines()
                            if line.startswith('[f01]')))
    assert [item['kind'] for item in first['evidence_in_this_chapter']] == ['inference']
    prompt = chapter_observation_prompt(
        chapter=7, book=book, chapters=chapters,
        evidence={ref: segment for ref, segment in evidence.items() if segment['chapter'] == 7})
    assert '云娘怀中被称为孩子的婴儿' in prompt and 'e07_01' in prompt
    assert 'e10_01' not in prompt and 'e12_01' not in prompt
    assert book.characters['sheng_er'].surfaces[0].surface == '晟儿'


def test_cognition_negative_reader_and_unset_preserve_separate_history():
    outcome = _compiled_case()
    early = outcome.blueprint.chapters[10].beats[0]
    assert not any(cog.holder == 'reader' and cog.proposition.ref == 'f01'
                   for cog in early.cognition)
    denial, withdrawal, confirmation = outcome.blueprint.chapters[16].beats
    assert [(cog.mode, cog.polarity) for cog in denial.cognition if cog.holder == 'reader'] == [
        ('hears_claim', 'negative')]
    assert not any(cog.holder == 'reader' for cog in withdrawal.cognition)
    assert any(cog.holder == 'reader' and cog.mode == 'learns'
               and cog.polarity == 'negative' and cog.proposition.ref == 'f01'
               for cog in confirmation.cognition)
    closed = confirmation.loops[0]
    assert closed.resolution == '非（男子由阿姐送来）'
    assert closed.answer_refs == ('b17_03.c1',)
    events = [entry for entry in outcome.fold['knowledge']['f01']['events']
              if entry['holder'] == 'yun_niang']
    assert [(entry['op'], entry['mode'], entry['polarity']) for entry in events] == [
        ('set', 'believes', 'positive'), ('set', 'hears_claim', 'negative'),
        ('unset', None, None), ('set', 'learns', 'negative')]
    final = '\n'.join(outcome.fold['snapshots'][17])
    assert 'yun_niang=learns[negative]' in final
    assert '说法历史: yun_niang=hears_claim[negative]' in final


def test_current_wire_inverse_keeps_explicit_cognition_and_scene_answer_offsets():
    _chapters, wires, evidence = _case()
    wire = deepcopy(wires['ch17'])
    last = wire['beats'][-1]
    last['scene_location'] = '巷口'
    last['change'] = [{'at': 'yun_niang', 'key': 'emotional_state', 'val': '释然',
                       'reader_visible': True, 'evidence_refs': ['e17_03']}]
    last['loops'][0]['answer_refs'].append({'kind': 'change', 'index': 1})
    observation, errors = compile_chapter_observation(
        wire, evidence_ids=evidence, chapter=17,
        character_slugs=wires['book']['characters'], fact_ids=['f01', 'f02'])
    assert observation is not None and not errors, errors
    assert observation.beats[-1].loops[0].answer_refs == ('c1', 'e2')
    inverse = slim_chapter_wire(observation.model_dump(mode='json'))
    # 瘦面省略空容器与机械默认；认知边界及答案选择仍必须显式、可逆。
    for beat in inverse['beats']:
        for cognition in beat.get('know', ()):
            assert {'op', 'how', 'polarity', 'support', 'reader_visible'} <= cognition.keys()
    assert inverse['beats'][1]['know'][0]['op'] == 'unset'
    assert inverse['beats'][1]['know'][0]['how'] is None
    assert inverse['beats'][1]['know'][0]['polarity'] is None
    assert inverse['beats'][-1]['loops'][0]['answer_refs'] == [
        {'kind': 'know', 'index': 1}, {'kind': 'change', 'index': 1}]
    assert inverse['beats'][-1]['change'][0]['reader_visible'] is True
    assert observation.beats[-1].effects[0].reader_visible is False
    assert inverse['beats'][-1]['location_reader_visible'] is False
    restored, errors = compile_chapter_observation(
        inverse, evidence_ids=evidence, chapter=17,
        character_slugs=wires['book']['characters'], fact_ids=['f01', 'f02'])
    assert restored is not None and not errors, errors
    assert restored.model_dump(mode='json') == observation.model_dump(mode='json')


@pytest.mark.parametrize('visible', [False, True])
def test_scene_location_requires_explicit_reader_disclosure_through_final_compile(visible):
    chapters, wires, evidence, book, observations, names, associate = _typed_case()
    wire = deepcopy(wires['ch01'])
    wire['beats'][0].update(scene_location='隐蔽的密室', location_reader_visible=visible)
    observation, errors = compile_chapter_observation(
        wire, evidence_ids=evidence, chapter=1, character_slugs=book.characters,
        fact_ids=[fact.fact_id for fact in book.facts])
    assert observation is not None and not errors, errors
    assert observation.beats[0].effects[0].reader_visible is visible
    inverse = slim_chapter_wire(observation.model_dump(mode='json'))
    assert inverse['beats'][0]['location_reader_visible'] is visible
    observations[1] = observation
    outcome = compile_source_truth(book=book, chapters=observations, names=names,
                                   associate=associate, evidence=evidence,
                                   source_texts=tuple(chapter['text'] for chapter in chapters))
    assert outcome.publishable, (outcome.errors, outcome.undecided)
    reader = [entry for entry in outcome.blueprint.chapters[0].beats[0].cognition
              if entry.holder == 'reader']
    assert len(reader) == int(visible)
    if visible:
        assert reader[0].proposition.statement == 'world.scene_location = "隐蔽的密室"'


@pytest.mark.parametrize('invalid', ['missing', 'visible_without_location'])
def test_scene_location_disclosure_flag_is_required_and_locatable(invalid):
    _chapters, wires, evidence = _case()
    wire = deepcopy(wires['ch01'])
    if invalid == 'missing':
        del wire['beats'][0]['location_reader_visible']
    else:
        wire['beats'][0]['location_reader_visible'] = True
    options = dict(evidence_ids=evidence, chapter=1,
                   character_slugs=wires['book']['characters'], fact_ids=['f01', 'f02'])
    observation, errors = compile_chapter_observation(wire, **options)
    assert observation is None and any('location_reader_visible' in str(error) for error in errors)
    assert chapter_patch_locate(wire, errors, **options)
    wire['beats'][0]['location_reader_visible'] = False
    accepted, errors = compile_chapter_observation(wire, **options)
    assert accepted is not None and not errors, errors


def test_effect_answer_text_distinguishes_subject_and_key_for_the_same_value():
    atoms = [
        {'target': {'kind': 'character', 'char_id': 'sheng_er'},
         'key': 'body_state', 'value': '有印记'},
        {'target': {'kind': 'character', 'char_id': 'ning_er'},
         'key': 'body_state', 'value': '有印记'},
        {'target': {'kind': 'relation', 'subject': 'yun_niang', 'object': 'sheng_er'},
         'key': 'relationship_status', 'value': '有印记'},
        {'target': {'kind': 'world'}, 'key': 'scene_location', 'value': '有印记'},
    ]
    expected = [
        'char:sheng_er.body_state = "有印记"',
        'char:ning_er.body_state = "有印记"',
        'rel:yun_niang,sheng_er.relationship_status = "有印记"',
        'world.scene_location = "有印记"',
    ]
    assert [state_statement(atom) for atom in atoms] == expected
    assert loop_answer_resolution(atoms) == '；'.join(expected)


@pytest.mark.parametrize('first,second', [
    ('口令是A-B', '口令是AB'),
    ('年龄是10.5岁', '年龄是105岁'),
    ('口令是A B', '口令是AB'),
])
def test_local_propositions_preserve_punctuation_and_internal_whitespace(first, second):
    evidence = {'e01_01': {'chapter': 1, 'text': f'云娘先猜{first}，又猜{second}。'}}
    know = [{'who': 'yun_niang', 'op': 'set', 'how': 'believes',
             'polarity': 'positive', 'support': 'inference', 'reader_visible': True,
             'what': statement, 'evidence_refs': ['e01_01']}
            for statement in (first, second, first)]
    wire = {'protocol_version': STAGE2_CHAPTERS_WIRE_PROTOCOL, 'beats': [{
        'event': '云娘比较两种不同的猜测，又重申第一种。', 'on_stage': ['yun_niang'],
        'start': 'e01_01', 'new_scene': True, 'location_reader_visible': False,
        'know': know}]}
    observation, errors = compile_chapter_observation(
        wire, evidence_ids=evidence, chapter=1, character_slugs=['yun_niang'], fact_ids=[])
    assert observation is not None and not errors, errors
    a, b, repeated = observation.beats[0].cognition
    assert a.proposition.ref != b.proposition.ref
    assert (a.proposition.statement, b.proposition.statement) == (first, second)
    assert repeated.proposition.ref == a.proposition.ref
    assert repeated.proposition.statement is None, '逐字相同文本仍复用首次登记'


def test_fact_reference_preserves_one_proposition_identity_across_polarities_and_withdrawal():
    evidence = {'e01_01': {'chapter': 1, 'text': '云娘先猜甲，撤回猜测后证实非甲。'}}
    wire = {'protocol_version': STAGE2_CHAPTERS_WIRE_PROTOCOL, 'beats': [{
        'event': '云娘纠正误信。', 'on_stage': ['yun_niang'], 'start': 'e01_01',
        'new_scene': True, 'location_reader_visible': False,
        'know': [_know('f01', 'e01_01'),
                 _know('f01', 'e01_01', op='unset', mode=None, polarity=None,
                       support='confirmed'),
                 _know('f01', 'e01_01', mode='learns', polarity='negative',
                       support='confirmed')]}]}
    observation, errors = compile_chapter_observation(
        wire, evidence_ids=evidence, chapter=1, character_slugs=['yun_niang'], fact_ids=['f01'])
    assert observation is not None and not errors, errors
    assert [c.proposition.ref for c in observation.beats[0].cognition] == ['f01'] * 3
    assert [c.polarity for c in observation.beats[0].cognition] == ['positive', None, 'negative']
    assert all(c.proposition.statement is None for c in observation.beats[0].cognition)


@pytest.mark.parametrize('value', [0, 3, False, [], ['银簪', '钥匙'], ''])
def test_visible_effect_reader_projection_preserves_subject_key_and_json_value(value):
    chapters, _wires, evidence, book, observations, names, associate = _typed_case()
    wire = {'protocol_version': STAGE2_CHAPTERS_WIRE_PROTOCOL,
            'beats': [{'event': '云娘核对持有物。', 'on_stage': ['yun_niang'],
                       'start': 'e01_01', 'new_scene': True,
                       'location_reader_visible': False,
                       'change': [{'at': 'yun_niang', 'key': 'possession', 'val': value,
                                   'reader_visible': True, 'evidence_refs': ['e01_01']}],
                       'know': [], 'loops': [
                           {'ref': 'q_possession', 'op': 'open',
                            'question': '云娘现持有哪些东西？', 'answer_refs': []},
                           {'ref': 'q_possession', 'op': 'close', 'question': None,
                            'answer_refs': [{'kind': 'change', 'index': 1}]}]}]}
    observation, errors = compile_chapter_observation(
        wire, evidence_ids=evidence, chapter=1, character_slugs=book.characters,
        fact_ids=[fact.fact_id for fact in book.facts])
    assert observation is not None and not errors, errors
    observations[1] = observation
    expected = json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(',', ':'))
    expected_statement = f'char:yun_niang.possession = {expected}'
    projected = project_association_space(book=book, chapters=observations)
    assert projected.loop_closes['ch01.q_possession'].resolution == expected_statement
    outcome = compile_source_truth(book=book, chapters=observations, names=names,
                                   associate=associate, evidence=evidence,
                                   source_texts=tuple(chapter['text'] for chapter in chapters))
    assert outcome.publishable, (outcome.errors, outcome.undecided)
    reader = [entry for entry in outcome.blueprint.chapters[0].beats[0].cognition
              if entry.holder == 'reader']
    assert len(reader) == 1 and reader[0].mode == 'learns'
    assert reader[0].proposition.statement == expected_statement
    assert outcome.blueprint.chapters[0].beats[0].loops[-1].resolution == expected_statement
    assert reader[0].basis == outcome.blueprint.chapters[0].beats[0].effects[0].basis


def test_real_stage2_producers_publish_identity_and_corrected_cognition(tmp_path):
    chapters, wires, _evidence, _book, _observations, _names, associate = _typed_case()
    identity = associate.model_dump(mode='json')
    causes = identity.pop('cross_chapter_cause')
    identity['cross_chapter_cause'] = []
    wires['associate'] = identity
    wires['cross_cause'] = {'protocol_version': STAGE2_CROSS_CAUSE_WIRE_PROTOCOL,
                           'causes': causes}
    calls: list[str] = []

    def ask(owner, prompt, validate, patch=None, downstream_preview=None):
        candidate = deepcopy(wires[owner])
        errors = validate(candidate)
        assert not errors, (owner, errors)
        calls.append(owner)
        return candidate, synthetic_ref(owner, prompt)

    root = tmp_path / 'identity_cognition'
    repository = Str06Repository(root)
    repository.attach_context(make_context(root, ledger=CostLedger(path=None)))
    report = run_workflow(repository=repository, chapters=chapters, hook_copy='',
                          ask=ask, ask_text=None, ask_body=None, budget=800,
                          through='stage2', node_filter=STAGE2_NODES)
    assert report['undecided'] == 0
    assert {'book', 'ch07', 'ch08', 'ch11', 'ch17', 'names', 'associate', 'cross_cause'} <= set(calls)
    characters = repository.read_model('stage2/characters/characters.json')
    blueprint = repository.read_model('stage2/blueprints/chapter_blueprints.json')
    fold = repository.read('stage2/acceptance/fold.json')
    assert characters.characters['sheng_er'].surfaces[0].from_chapter == 10
    assert 'sheng_er' in blueprint.chapters[6].beats[0].participants
    assert blueprint.chapters[6].beats[0].effects[0].target.char_id == 'sheng_er'
    assert blueprint.chapters[16].beats[-1].loops[0].resolution == '非（男子由阿姐送来）'
    assert any(entry['op'] == 'unset' for entry in fold['knowledge']['f01']['events'])
    assert 'yun_niang=learns[negative]' in '\n'.join(fold['snapshots']['17'])


@pytest.mark.parametrize('out_of_scope', [False, True])
def test_free_preview_and_production_share_chapter_revision_scope_without_writes(tmp_path, out_of_scope):
    from novel.stages.chain import _backfill_source_evidence_conflicts, prepare_chapter_request
    from novel.stages.owner_revision import OwnerRevisionRequired
    from novel.stages.review import dry_run_patch

    chapters, wires, _evidence, _book, _observations, _names, associate = _typed_case()
    chapters[0]['text'] += '\n阿姐坐在门边，云娘仍在等候。'
    wires['ch01']['beats'].append({
        'event': '阿姐坐在门边，云娘仍在等候。', 'on_stage': ['yun_niang', 'a_jie'],
        'start': 'e01_02', 'location_reader_visible': False})
    evidence = build_evidence(chapters, '')
    identity = associate.model_dump(mode='json')
    causes = identity.pop('cross_chapter_cause')
    identity['cross_chapter_cause'] = []
    wires['associate'] = identity
    wires['cross_cause'] = {'protocol_version': STAGE2_CROSS_CAUSE_WIRE_PROTOCOL, 'causes': causes}
    calls = []

    def ask(owner, prompt, validate, patch=None, downstream_preview=None):
        candidate = deepcopy(wires[owner])
        assert not validate(candidate), owner
        calls.append(owner)
        return candidate, synthetic_ref(owner, prompt)

    root = tmp_path / 'scope_preview'
    ledger = CostLedger(path=None)
    repository = Str06Repository(root)
    repository.attach_context(make_context(root, ledger=ledger))
    run_workflow(repository=repository, chapters=chapters, hook_copy='', ask=ask,
                 ask_text=None, ask_body=None, budget=800, through='stage2', node_filter=STAGE2_NODES)
    with pytest.raises(OwnerRevisionRequired):
        _backfill_source_evidence_conflicts(repository, [{
            'anchor': 'b01_01', 'segment': 'e01_01', 'note': '修正首拍事件的指代'}])
    book = repository.read_model('stage2/observations/book.json')
    previous_calls = tuple(calls)
    previous_usage = ledger.snapshot()
    before = {str(path.relative_to(root)): path.read_bytes()
              for path in root.rglob('*') if path.is_file()}
    chapter_evidence = {ref: segment for ref, segment in evidence.items() if segment['chapter'] == 1}
    _prompt, validate = prepare_chapter_request(
        repository, no=1, book=book, chapters=chapters, chapter_evidence=chapter_evidence,
        fact_ids=frozenset(fact.fact_id for fact in book.facts))
    candidate = deepcopy(wires['ch01'])
    candidate['beats'][0]['event'] = '云娘望着阿姐，静待天明。'
    if out_of_scope:
        candidate['beats'][1]['event'] = '云娘离开房间，阿姐独自等候。'
    _observation, production_errors = validate(candidate)
    preview = dry_run_patch(repository, 'ch01', candidate, evidence=evidence,
        budget=800, chapter_count=len(chapters), source_texts=tuple(c['text'] for c in chapters))
    assert preview['status'] == 'ran'
    assert preview['errors'] == production_errors
    if out_of_scope:
        assert any('窄修越界' in str(error) for error in production_errors)
    else:
        assert not production_errors and not preview['undecided']
    assert tuple(calls) == previous_calls, '免费预览不能触发模型请求'
    assert ledger.snapshot() == previous_usage and ledger.calls_made == 0
    after = {str(path.relative_to(root)): path.read_bytes()
             for path in root.rglob('*') if path.is_file()}
    assert after == before, '免费预览与生产验证器只能读取，不得改变正式产物或回派账'



def _location_answer_wire(selector, *, with_other_atoms=False):
    beat = {'event': '旁白向读者公开地点，云娘和阿姐仍不知道身处何地。',
            'on_stage': [], 'start': 'e01_01', 'new_scene': True, 'access': 'narrated',
            'scene_location': '京郊废寺', 'location_reader_visible': True,
            'change': [], 'know': [],
            'loops': [{'ref': 'q_location', 'op': 'open', 'question': '被困地点在哪里？',
                       'answer_refs': []},
                      {'ref': 'q_location', 'op': 'close', 'question': None,
                       'answer_refs': [selector]}]}
    text = '云娘望着阿姐，她们仍不知身处何处。旁白揭示所在地是京郊废寺。'
    if with_other_atoms:
        beat['on_stage'] = ['yun_niang']
        beat['change'] = [{'at': 'yun_niang', 'key': 'possession', 'val': '铜钥匙',
                           'reader_visible': True, 'evidence_refs': ['e01_01']}]
        beat['know'] = [{'who': 'yun_niang', 'op': 'set', 'how': 'learns',
                        'polarity': 'positive', 'support': 'confirmed', 'reader_visible': True,
                        'what': '门已锁上', 'evidence_refs': ['e01_01']}]
        beat['loops'][0]['question'] = {
            'scene_location': '被困地点在哪里？', 'change': '云娘拿着什么？',
            'know': '门是否锁上？'}[selector['kind']]
        text = '云娘望着阿姐，拿着铜钥匙并确认门已锁上，却仍不知地点。旁白揭示京郊废寺。'
    return {'protocol_version': STAGE2_CHAPTERS_WIRE_PROTOCOL, 'beats': [beat]}, {
        'e01_01': {'chapter': 1, 'text': text}}


@pytest.mark.parametrize('selector,valid', [
    ({'kind': 'scene_location', 'index': 1}, True),
    ({'kind': 'scene_location', 'index': 0}, False),
    ({'kind': 'scene_location', 'index': 2}, False),
    ({'kind': 'scene_location', 'index': -1}, False),
    ({'kind': 'scene_location', 'index': True}, False),
    ({'kind': 'scene_location', 'index': '1'}, False),
    ({'kind': 'scene_location', 'index': 1.0}, True),
    ({'kind': 'scene_location', 'index': 1.5}, False),
    ({'kind': 'change', 'index': 1}, True),
    ({'kind': 'change', 'index': 2}, True),
    ({'kind': 'change', 'index': True}, False),
    ({'kind': 'change', 'index': '1'}, False),
    ({'kind': 'know', 'index': 1}, True),
    ({'kind': 'know', 'index': 2}, True),
    ({'kind': 'location', 'index': 1}, False),
])
def test_location_answer_selector_has_the_same_shape_in_schema_and_pydantic(selector, valid):
    from jsonschema import Draft202012Validator
    from pydantic import ValidationError
    from contracts.schema.observation import SlimAnswerRef

    assert Draft202012Validator(SlimAnswerRef.model_json_schema()).is_valid(selector) is valid
    if valid:
        assert SlimAnswerRef.model_validate(selector).model_dump(mode='json') == selector
    else:
        with pytest.raises(ValidationError):
            SlimAnswerRef.model_validate(selector)


@pytest.mark.parametrize('kind,canonical_ref', [
    ('scene_location', 'e1'), ('change', 'e2'), ('know', 'c1'),
])
def test_all_location_change_and_know_answers_roundtrip_without_array_index_drift(kind, canonical_ref):
    selector = {'kind': kind, 'index': 1}
    wire, evidence = _location_answer_wire(selector, with_other_atoms=True)
    options = dict(evidence_ids=evidence, chapter=1,
                   character_slugs=['yun_niang', 'a_jie'], fact_ids=[])
    observation, errors = compile_chapter_observation(wire, **options)
    assert observation is not None and not errors, errors
    assert observation.beats[0].loops[-1].answer_refs == (canonical_ref,)
    inverse = slim_chapter_wire(observation.model_dump(mode='json'))
    assert inverse['beats'][0]['loops'][-1]['answer_refs'] == [selector]
    restored, errors = compile_chapter_observation(inverse, **options)
    assert restored is not None and not errors, errors
    assert restored.model_dump(mode='json') == observation.model_dump(mode='json')


@pytest.mark.parametrize('missing', [True, False])
def test_location_answer_cannot_capture_an_ordinary_e1_without_a_location(missing):
    from jsonschema import Draft202012Validator
    from pydantic import ValidationError
    from contracts.schema.observation import ChapterObservationWire

    wire, evidence = _location_answer_wire({'kind': 'scene_location', 'index': 1},
                                           with_other_atoms=True)
    if missing:
        del wire['beats'][0]['scene_location']
    else:
        wire['beats'][0]['scene_location'] = None
    wire['beats'][0]['location_reader_visible'] = False
    assert not Draft202012Validator(ChapterObservationWire.model_json_schema()).is_valid(wire)
    with pytest.raises(ValidationError, match='scene_location'):
        ChapterObservationWire.model_validate(wire)
    options = dict(evidence_ids=evidence, chapter=1,
                   character_slugs=['yun_niang', 'a_jie'], fact_ids=[])
    observation, errors = compile_chapter_observation(wire, **options)
    assert observation is None and any('scene_location' in str(error) for error in errors)
    assert chapter_patch_locate(wire, errors, **options)
    # 普通数组的 e1 保持原义；改为 change/1 后，同一无位置输入免费预验通过。
    wire['beats'][0]['loops'][-1]['answer_refs'] = [{'kind': 'change', 'index': 1}]
    accepted, errors = compile_chapter_observation(wire, **options)
    assert accepted is not None and not errors, errors
    assert accepted.beats[0].loops[-1].answer_refs == ('e1',)
    inverse = slim_chapter_wire(accepted.model_dump(mode='json'))
    assert inverse['beats'][0]['loops'][-1]['answer_refs'] == [{'kind': 'change', 'index': 1}]


def test_hidden_location_answer_is_rejected_by_shared_rule_and_free_revalidation():
    wire, evidence = _location_answer_wire({'kind': 'scene_location', 'index': 1})
    wire['beats'][0]['location_reader_visible'] = False
    options = dict(evidence_ids=evidence, chapter=1, character_slugs=[], fact_ids=[])
    observation, errors = compile_chapter_observation(wire, **options)
    assert observation is None and any(
        getattr(error, 'code', None) == 'chapter_loop_answer_invalid' for error in errors)
    assert chapter_patch_locate(wire, errors, **options)
    wire['beats'][0]['location_reader_visible'] = True
    accepted, errors = compile_chapter_observation(wire, **options)
    assert accepted is not None and not errors, errors
    assert not accepted.beats[0].cognition and accepted.beats[0].loops[-1].answer_refs == ('e1',)


@pytest.mark.parametrize('invalid_index', [0, 2, -1])
def test_location_answer_wrong_index_is_locatable_and_has_a_free_green_control(invalid_index):
    wire, evidence = _location_answer_wire({'kind': 'scene_location', 'index': invalid_index})
    options = dict(evidence_ids=evidence, chapter=1, character_slugs=[], fact_ids=[])
    observation, errors = compile_chapter_observation(wire, **options)
    assert observation is None and errors
    assert chapter_patch_locate(wire, errors, **options)
    wire['beats'][0]['loops'][-1]['answer_refs'][0]['index'] = 1
    accepted, errors = compile_chapter_observation(wire, **options)
    assert accepted is not None and not errors, errors


def test_location_only_answer_finalizes_blueprint_without_inventing_character_knowledge():
    chapters, _wires, evidence, book, observations, names, associate = _typed_case()
    wire, local_evidence = _location_answer_wire({'kind': 'scene_location', 'index': 1})
    evidence.update(local_evidence)
    chapters[0]['text'] = local_evidence['e01_01']['text']
    observation, errors = compile_chapter_observation(
        wire, evidence_ids=evidence, chapter=1, character_slugs=book.characters,
        fact_ids=[fact.fact_id for fact in book.facts])
    assert observation is not None and not errors, errors
    assert not observation.beats[0].cognition
    inverse = slim_chapter_wire(observation.model_dump(mode='json'))
    restored, errors = compile_chapter_observation(
        inverse, evidence_ids=evidence, chapter=1, character_slugs=book.characters,
        fact_ids=[fact.fact_id for fact in book.facts])
    assert restored is not None and not errors and restored == observation, errors
    observations[1] = restored
    outcome = compile_source_truth(book=book, chapters=observations, names=names,
                                   associate=associate, evidence=evidence,
                                   source_texts=tuple(chapter['text'] for chapter in chapters))
    assert outcome.publishable, (outcome.errors, outcome.undecided)
    beat = outcome.blueprint.chapters[0].beats[0]
    assert len(beat.effects) == 1 and beat.effects[0].key == 'scene_location'
    assert beat.loops[-1].answer_refs == ('b01_01.e1',)
    assert beat.loops[-1].resolution == 'world.scene_location = "京郊废寺"'
    assert len(beat.cognition) == 1
    reader = beat.cognition[0]
    assert (reader.holder, reader.op, reader.mode, reader.support) == (
        'reader', 'set', 'learns', 'confirmed')
    assert reader.proposition.statement == 'world.scene_location = "京郊废寺"'
    assert reader.basis == beat.effects[0].basis
