"""BP10 表面身份的真实编译、编辑与身份闭集；不以结构通过声称语义接受。"""
from __future__ import annotations

import pytest

from contracts.schema.plot import PlotChapterResponse, PlotSubjectRef
from contracts.schema.plot_edit import PlotChapterEdit
from novel.stages.stage2.blueprint import edit as plot_edit
from novel.stages.stage2.shared.character_refs import blueprint_character_prompt_inventory
from novel.stages.stage2.blueprint.compile import (
    available_actor_bindings, compile_chapter, response_projection, state_materials,
    validate_actors, validate_book,
)
from novel.stages.stage2.blueprint.sequence import request_compiler, with_entry_states
from novel.stages.stage2.blueprint.prompts import build_plot_user

SOURCE = '新邻居向娘子点头。\n\n门开了，新邻居竟是裴书远。'
INVENTORY = {'char_001': [
    {'surface': '新邻居', 'identity_revealed_from': 1},
    {'surface': '裴书远', 'identity_revealed_from': 1}],
    'char_002': [{'surface': '娘子', 'identity_revealed_from': 1}]}
CHARS = frozenset(INVENTORY)


def response(ordinal=1):
    def evidence(n):
        return [{'start_para': n, 'end_para': n}]
    return {
        'chapter_ordinal': ordinal, 'chapter_function': '拜访后揭示邻居的身份',
        'actor_bindings': [
            {'presentation_key': 'a1', 'char_id': 'char_001', 'surfaces': ['新邻居'],
             'evidence': evidence(1), 'public_state_ref': None},
            {'presentation_key': 'a2', 'char_id': 'char_001', 'surfaces': ['裴书远'],
             'evidence': evidence(2), 'public_state_ref': None}],
        'beats': [
            {'beat_key': 'b1', 'content': '新邻居向娘子点头。',
             'participants': [{'char_id': 'char_001', 'presentation_ref': 'a1'}, {'char_id': 'char_002'}],
             'evidence': evidence(1), 'source_contexts': []},
            {'beat_key': 'b2', 'content': '门开后，娘子认出新邻居就是裴书远。',
             'participants': [{'char_id': 'char_001', 'presentation_ref': 'a1'},
                              {'char_id': 'char_001', 'presentation_ref': 'a2'}],
             'evidence': evidence(2), 'source_contexts': []}],
        'states': [], 'entry_state_refs': [], 'carry_out_refs': [],
        'inherited_state_endings': [], 'constraints': [],
        'narration': [{'grammatical_person': 'third', 'narrator': None,
            'focalizer': {'char_id': 'char_002'}, 'pronoun_surfaces': [],
            'beat_refs': [], 'evidence': evidence(1)}]}


def compiled(raw=None, *, inherited=None, available=frozenset()):
    return compile_chapter(PlotChapterResponse.model_validate(raw or response()), SOURCE,
        allowed_char_ids=CHARS, character_bindings=INVENTORY,
        allowed_actor_bindings=inherited, allowed_entry_state_refs=available)


def test_same_beat_keeps_two_presentations_and_roundtrip_keeps_handles():
    chapter = compiled()
    assert [actor.key() for actor in chapter.beats[1].participants] == [
        ('char_001', 'ap01_01'), ('char_001', 'ap01_02')]
    projection = response_projection(chapter.model_dump(mode='json'), SOURCE)
    assert [row['presentation_ref'] for row in projection['beats'][1]['participants']] == ['a1', 'a2']
    assert compiled(projection) == chapter
    assert not any('role' in type(row).model_fields for row in chapter.actor_bindings)


@pytest.mark.parametrize('failure', ['ambiguous_default', 'missing', 'wrong_owner', 'invented_surface', 'unattested_surface'])
def test_compiler_rejects_unprovided_or_ambiguous_binding(failure):
    raw = response()
    if failure == 'ambiguous_default':
        raw['beats'][0]['participants'][0]['presentation_ref'] = None
    elif failure == 'missing':
        raw['beats'][0]['participants'][0]['presentation_ref'] = 'ap01_99'
    elif failure == 'wrong_owner':
        raw['beats'][0]['participants'][0]['char_id'] = 'char_002'
    elif failure == 'invented_surface':
        raw['actor_bindings'][0]['surfaces'] = ['裴知远']
    else:
        raw['actor_bindings'][1]['evidence'] = [{'start_para': 1, 'end_para': 1}]
    with pytest.raises(ValueError):
        compiled(raw)


def test_subject_use_key_keeps_presentations_while_raw_key_identifies_fact():
    first = PlotSubjectRef(kind='character', ref='char_001',
        actor={'char_id': 'char_001', 'presentation_ref': 'ap01_01'})
    second = first.model_copy(update={'actor': first.actor.model_copy(update={'presentation_ref': 'ap01_02'})})
    assert first.raw_subject_key() == second.raw_subject_key()
    assert first.key() != second.key()


def test_relation_uses_bind_actual_presentations_without_inventing_a_state():
    raw = response()
    raw['relation_uses'] = [dict(kind='relation', ref='rel_01', relation_actors={
        'subject': {'char_id': 'char_001', 'presentation_ref': ref},
        'object': {'char_id': 'char_002', 'presentation_ref': None}}) for ref in ('a1', 'a2')]
    chapter = compile_chapter(PlotChapterResponse.model_validate(raw), SOURCE,
        allowed_char_ids=CHARS, character_bindings=INVENTORY,
        allowed_global_refs=frozenset({('relation', 'rel_01')}),
        global_subjects={('relation', 'rel_01'): {'subject_id': 'char_001', 'object_id': 'char_002'}})
    assert chapter.states == []
    assert len({row.key() for row in chapter.relation_uses}) == 2
    assert [row.relation_actors.subject.presentation_ref for row in chapter.relation_uses] == ['ap01_01', 'ap01_02']
    projected = response_projection(chapter.model_dump(mode='json'), SOURCE)
    assert [row['relation_actors']['subject']['presentation_ref'] for row in projected['relation_uses']] == ['a1', 'a2']
    raw['relation_uses'][0]['relation_actors']['subject']['char_id'] = 'char_002'
    with pytest.raises(ValueError, match='有向端点'):
        compile_chapter(PlotChapterResponse.model_validate(raw), SOURCE,
            allowed_char_ids=CHARS, character_bindings=INVENTORY,
            allowed_global_refs=frozenset({('relation', 'rel_01')}),
            global_subjects={('relation', 'rel_01'): {'subject_id': 'char_001', 'object_id': 'char_002'}})


def test_canonical_first_appearance_does_not_hide_earlier_registered_persona():
    people = {'char_001': {
        'canonical_surface': '芸娘',
        'first_mention_chapter': 1,
        'surfaces': [
            {'surface': '娘子', 'kind': 'social_address', 'identity_revealed_from': 4},
            {'surface': '芸娘', 'kind': 'proper_name', 'identity_revealed_from': 4},
        ],
    }}
    inventory = blueprint_character_prompt_inventory(people, [
        {'chapter_ordinal': 1, 'text': '娘子，我等你。'}, {'chapter_ordinal': 4, 'text': '她名叫芸娘。'}])
    assert inventory['visible_character_refs'][1] == {'char_001': ['娘子']}
    assert inventory['character_bindings'][1]['char_001'] == [
        {'surface': '娘子', 'identity_revealed_from': 4}]


def test_required_mention_actors_cover_bound_relational_not_pronoun():
    from novel.stages.stage2.blueprint.compile import (
        actor_refs, missing_mention_actors, required_char_ids_from_mentions,
    )
    from contracts.schema.plot import ActorRef

    mentions = {
        'm_aaaaaaaaaaaaaaaa': {
            'mention_id': 'm_aaaaaaaaaaaaaaaa', 'chapter_ordinal': 1,
            'surface': '女儿', 'kind': 'relational_address', 'disposition': 'bound',
            'char_id': 'char_099',
        },
        'm_bbbbbbbbbbbbbbbb': {
            'mention_id': 'm_bbbbbbbbbbbbbbbb', 'chapter_ordinal': 1,
            'surface': '她', 'kind': 'pronoun', 'disposition': 'bound',
            'char_id': 'char_098',
        },
        'm_cccccccccccccccc': {
            'mention_id': 'm_cccccccccccccccc', 'chapter_ordinal': 1,
            'surface': '路人', 'kind': 'proper_name', 'disposition': 'excluded',
            'char_id': None,
        },
    }
    characters = {
        'char_099': {'status': 'confirmed', 'canonical_surface': '女儿'},
        'char_098': {'status': 'confirmed', 'canonical_surface': '她'},
    }
    required = required_char_ids_from_mentions(mentions, 1, characters=characters)
    assert required == frozenset({'char_099'})

    chapter = compiled()
    missing = missing_mention_actors(chapter, mentions, characters=characters)
    assert missing == frozenset({'char_099'})

    pronoun_only = {k: v for k, v in mentions.items() if k != 'm_aaaaaaaaaaaaaaaa'}
    assert required_char_ids_from_mentions(
        pronoun_only, 1, characters=characters) == frozenset()
    assert missing_mention_actors(
        chapter, pronoun_only, characters=characters) == frozenset()

    chapter.beats[0].participants.append(
        ActorRef(char_id='char_099', presentation_ref=None))
    assert missing_mention_actors(
        chapter, mentions, characters=characters) == frozenset()
    assert 'char_098' not in {a.char_id for a in actor_refs(chapter)}


def test_plain_public_nicknames_can_share_one_binding():
    raw = response()
    raw['actor_bindings'] = [{**raw['actor_bindings'][0], 'surfaces': ['新邻居', '裴书远'],
        'evidence': [{'start_para': 2, 'end_para': 2}]}]
    raw['beats'][1]['participants'] = [{'char_id': 'char_001', 'presentation_ref': 'a1'}]
    assert len(compiled(raw).actor_bindings) == 1


def test_unique_public_binding_is_used_when_all_positions_omit_presentation():
    raw = response(2)
    raw['actor_bindings'] = [{**raw['actor_bindings'][0], 'surfaces': ['新邻居', '裴书远'],
        'evidence': [{'start_para': 2, 'end_para': 2}]}]
    for beat in raw['beats']:
        beat['participants'] = [{'char_id': 'char_001'}, {'char_id': 'char_002'}]
    chapter = compiled(raw)
    assert len(chapter.actor_bindings) == 1
    assert all(actor.presentation_ref is None for beat in chapter.beats for actor in beat.participants)


@pytest.mark.parametrize('revealed_from', [2, 3])
@pytest.mark.parametrize('binding_surface', ['新邻居', '裴书远'])
def test_old_default_cannot_be_absorbed_by_new_binding_and_free_edit_resolves_it(revealed_from, binding_surface):
    from copy import deepcopy
    raw = response(2)
    raw['actor_bindings'] = raw['actor_bindings'][:1]
    raw['actor_bindings'][0].update(surfaces=[binding_surface],
        evidence=[{'start_para': 2, 'end_para': 2}])
    raw['beats'][1]['participants'] = [{'char_id': 'char_001'}, {'char_id': 'char_002'}]
    old_names = deepcopy(INVENTORY)
    old_names['char_001'][0]['identity_revealed_from'] = 1
    # 构造此前机械检查曾放过的问题稿；它不是语义接受证据。
    previous = compile_chapter(PlotChapterResponse.model_validate(raw), SOURCE,
        allowed_char_ids=CHARS, character_bindings=old_names)
    current_names = deepcopy(old_names)
    current_names['char_001'][0]['identity_revealed_from'] = revealed_from
    with pytest.raises(ValueError, match='不能吸入'):
        compile_chapter(PlotChapterResponse.model_validate(raw), SOURCE,
            allowed_char_ids=CHARS, character_bindings=current_names)
    with pytest.raises(ValueError, match='不能吸入'):
        validate_actors(previous, SOURCE, allowed_char_ids=CHARS, character_bindings=current_names)

    projected = response_projection(previous.model_dump(mode='json'), SOURCE)
    identities = [dict(collection=collection, kind='preserve', old_refs=[getattr(old, field)], new_keys=[new[key]])
        for collection, field, key, _ in plot_edit.COLLECTIONS
        for old, new in zip(getattr(previous, collection), projected[collection], strict=True)]
    other_surface = '裴书远' if binding_surface == '新邻居' else '新邻居'
    projected['actor_bindings'].append({'presentation_key': 'a2', 'char_id': 'char_001',
        'surfaces': [other_surface], 'evidence': [{'start_para': 2, 'end_para': 2}], 'public_state_ref': None})
    identities.append(dict(collection='actor_bindings', kind='add', old_refs=[], new_keys=['a2']))
    projected['beats'][1]['participants'][0]['presentation_ref'] = 'a2'
    repaired, _, _ = plot_edit._compile_edit(PlotChapterEdit(chapter=projected, identities=identities),
        previous, SOURCE, available=frozenset(), chars=CHARS, globals_=frozenset(),
        retired=set(), character_bindings=current_names)
    assert repaired.beats[0].participants[0].presentation_ref == 'ap02_01'
    assert repaired.beats[1].participants[0].presentation_ref == 'ap02_03'
    assert repaired.beats[1].content == previous.beats[1].content


def test_inherited_state_free_request_compiler_receives_exact_binding_closure():
    raw = response()
    raw['states'] = [{'state_key': 'st1', 'content': '新邻居声称来拜访。',
        'global_ref': None, 'modality': 'claim',
        'holder': {'char_id': 'char_001', 'presentation_ref': 'a1'},
        'established_at': {'kind': 'at_beat', 'beat_ref': 'b1'}, 'ended_at': None,
        'supersedes_state_refs': [], 'evidence': [{'start_para': 1, 'end_para': 1}]}]
    raw['carry_out_refs'] = ['st1']
    first = compiled(raw)
    materials = state_materials([first], ['st01_01'])
    assert [row['binding']['presentation_id'] for row in materials[0]['actor_bindings']] == ['ap01_01']
    later = response(2)
    later['actor_bindings'] = []
    later['entry_state_refs'] = ['st01_01']
    later['carry_out_refs'] = ['st01_01']
    later['beats'] = [later['beats'][0]]
    # 抄写历史正式句柄：编译器压成 null（chapter-local），不保留 ap01_01。
    later['beats'][0]['participants'][0]['presentation_ref'] = 'ap01_01'
    user = build_plot_user('合成', SOURCE, 2, character_refs={cid: [row['surface'] for row in names]
        for cid, names in INVENTORY.items()}, character_bindings=INVENTORY, global_projection=[])
    compiler = request_compiler(with_entry_states(user, materials), SOURCE)
    second = compiler(PlotChapterResponse.model_validate(later))
    assert second.beats[0].participants[0].presentation_ref is None
    validate_book([first, second], {1: SOURCE, 2: SOURCE})
    with pytest.raises(ValueError):
        request_compiler(with_entry_states(user, []), SOURCE)(PlotChapterResponse.model_validate(later))


def test_ch7_style_foreign_presentation_refs_canonicalize_to_null():
    """fujun ch7 形态：本章只声明本地 binding，却引用他章 ap0N_* —— 编译压成 null。"""
    first = compiled(response(1))
    inherited = {row.presentation_id: row for row in first.actor_bindings}
    later = response(2)
    later['actor_bindings'] = [{
        'presentation_key': 'a1', 'char_id': 'char_002', 'surfaces': ['娘子'],
        'evidence': [{'start_para': 1, 'end_para': 1}], 'public_state_ref': None,
    }]
    later['beats'] = [{
        'beat_key': 'b1', 'content': '娘子与新邻居同席。',
        'participants': [
            {'char_id': 'char_001', 'presentation_ref': 'ap01_01'},
            {'char_id': 'char_002', 'presentation_ref': 'a1'},
        ],
        'evidence': [{'start_para': 1, 'end_para': 1}], 'source_contexts': [{
            'evidence': [{'start_para': 1, 'end_para': 1}],
            'time_layer': 'present', 'place': None,
            'present': [
                {'char_id': 'char_001', 'presentation_ref': 'ap01_02'},
                {'char_id': 'char_002', 'presentation_ref': 'a1'},
            ],
        }],
    }]
    later['states'] = []
    later['constraints'] = []
    later['entry_state_refs'] = []
    later['carry_out_refs'] = []
    later['narration'] = [{
        'grammatical_person': 'third',
        'narrator': None,
        'focalizer': {'char_id': 'char_002', 'presentation_ref': None},
        'pronoun_surfaces': [], 'beat_refs': [],
        'evidence': [{'start_para': 1, 'end_para': 1}],
    }]
    second = compile_chapter(
        PlotChapterResponse.model_validate(later), SOURCE,
        allowed_char_ids=CHARS, character_bindings=INVENTORY,
        allowed_actor_bindings=inherited)
    assert second.actor_bindings[0].presentation_id == 'ap02_01'
    assert second.beats[0].participants[0].presentation_ref is None
    assert second.beats[0].participants[1].presentation_ref == 'ap02_01'
    from novel.stages.stage2.blueprint.source_contexts import expand_beat_contexts
    present = expand_beat_contexts(second.beats[0])[0]['present']
    assert present[0]['presentation_ref'] is None
    assert present[1]['presentation_ref'] == 'ap02_01'
    validate_actors(second, SOURCE, allowed_char_ids=CHARS, character_bindings=INVENTORY,
                    inherited=inherited)
    # 历史句柄 + 错误 char_id：拒绝，不能碰巧解析。
    later['beats'][0]['participants'][0] = {
        'char_id': 'char_002', 'presentation_ref': 'ap01_01'}
    with pytest.raises(ValueError, match='与实体不符'):
        compile_chapter(
            PlotChapterResponse.model_validate(later), SOURCE,
            allowed_char_ids=CHARS, character_bindings=INVENTORY,
            allowed_actor_bindings=inherited)


def test_formal_blueprint_renormalize_canonicalizes_foreign_presentation_refs():
    """存量正式稿上的跨章句柄：免费 rebuild 压 null，不付费重抽。"""
    from novel.stages.stage2.blueprint.recovery import canonicalize_presentation_refs

    first = compiled(response(1))
    later = response(2)
    later['actor_bindings'] = [{
        'presentation_key': 'a1', 'char_id': 'char_002', 'surfaces': ['娘子'],
        'evidence': [{'start_para': 1, 'end_para': 1}], 'public_state_ref': None,
    }]
    later['beats'] = [{
        'beat_key': 'b1', 'content': '娘子与新邻居同席。',
        'participants': [
            {'char_id': 'char_001', 'presentation_ref': None},
            {'char_id': 'char_002', 'presentation_ref': 'a1'},
        ],
        'evidence': [{'start_para': 1, 'end_para': 1}], 'source_contexts': [{
            'evidence': [{'start_para': 1, 'end_para': 1}],
            'time_layer': 'present', 'place': None,
            'present': [{'char_id': 'char_002', 'presentation_ref': 'a1'}],
        }],
    }]
    later['states'] = []
    later['constraints'] = []
    later['entry_state_refs'] = []
    later['carry_out_refs'] = []
    later['narration'] = [{
        'grammatical_person': 'third', 'narrator': None,
        'focalizer': {'char_id': 'char_002', 'presentation_ref': 'a1'},
        'pronoun_surfaces': [], 'beat_refs': [],
        'evidence': [{'start_para': 1, 'end_para': 1}],
    }]
    second = compile_chapter(
        PlotChapterResponse.model_validate(later), SOURCE,
        allowed_char_ids=CHARS, character_bindings=INVENTORY)
    # 模拟旧正式稿：手写塞入跨章句柄
    dumped = second.model_dump(mode='json')
    dumped['beats'][0]['participants'][0]['presentation_ref'] = 'ap01_01'
    from contracts.schema.plot import PlotBlueprint
    stale = PlotBlueprint.model_validate(dumped)
    fixed = canonicalize_presentation_refs(stale)
    assert fixed.beats[0].participants[0].presentation_ref is None
    assert fixed.beats[0].participants[1].presentation_ref == second.beats[0].participants[1].presentation_ref
    validate_actors(fixed, SOURCE, allowed_char_ids=CHARS, character_bindings=INVENTORY)


@pytest.mark.parametrize('declare_current', [False, True])
def test_unconsumed_historical_bindings_do_not_enter_current_executor_candidates(declare_current):
    import json
    first = compiled()
    later = response(2)
    later['actor_bindings'] = ([{**later['actor_bindings'][0], 'surfaces': ['新邻居', '裴书远'],
        'evidence': [{'start_para': 2, 'end_para': 2}]}] if declare_current else [])
    for beat in later['beats']:
        beat['participants'] = [{'char_id': 'char_001'}, {'char_id': 'char_002'}]
    user = build_plot_user('历史候选边界', SOURCE, 2,
        character_refs={cid: [row['surface'] for row in names] for cid, names in INVENTORY.items()},
        character_bindings=INVENTORY, global_projection=[])
    request = with_entry_states(user, state_materials([first], first.carry_out_refs))
    assert json.loads(request.split('\n', 1)[0])['entry_states'] == []
    compiler = request_compiler(request, SOURCE)
    second = compiler(PlotChapterResponse.model_validate(later))
    assert set(available_actor_bindings([first, second], 2)) == ({'ap02_01'} if declare_current else set())
    validate_book([first, second], {1: SOURCE, 2: SOURCE})
    later['beats'][0]['participants'][0]['presentation_ref'] = 'ap01_01'
    with pytest.raises(ValueError, match='引用不存在'):
        compiler(PlotChapterResponse.model_validate(later))


def test_carried_default_state_includes_its_original_unique_binding_without_rewriting_identity():
    raw = response()
    raw['actor_bindings'] = [{**raw['actor_bindings'][0], 'surfaces': ['新邻居', '裴书远'],
        'evidence': [{'start_para': 2, 'end_para': 2}]}]
    for beat in raw['beats']:
        beat['participants'] = [{'char_id': 'char_001'}, {'char_id': 'char_002'}]
    raw['states'] = [{'state_key': 'st1', 'content': '新邻居声称来拜访。',
        'global_ref': None, 'modality': 'claim', 'holder': {'char_id': 'char_001'},
        'established_at': {'kind': 'at_beat', 'beat_ref': 'b1'}, 'ended_at': None,
        'supersedes_state_refs': [], 'evidence': [{'start_para': 1, 'end_para': 1}]}]
    raw['carry_out_refs'] = ['st1']
    first = compiled(raw)
    carried = state_materials([first], first.carry_out_refs)
    assert carried[0]['state']['holder'] == {'char_id': 'char_001', 'presentation_ref': None}
    assert [row['binding']['presentation_id'] for row in carried[0]['actor_bindings']] == ['ap01_01']
    assert carried[0]['actor_bindings'][0]['source_sha256'] == first.source_sha256
    later = response(2)
    later['actor_bindings'] = later['actor_bindings'][:1]
    later['beats'] = later['beats'][:1]
    # 章内句柄规则：无本章多表面绑定时，只用 char_id（null）合法；不要求复用 ap01_01。
    later['beats'][0]['participants'][0]['presentation_ref'] = None
    later['entry_state_refs'] = ['st01_01']
    later['carry_out_refs'] = []
    user = build_plot_user('携入身份边界', SOURCE, 2,
        character_refs={cid: [row['surface'] for row in names] for cid, names in INVENTORY.items()},
        character_bindings=INVENTORY, global_projection=[])
    compiler = request_compiler(with_entry_states(user, carried), SOURCE)
    second = compiler(PlotChapterResponse.model_validate(later))
    assert second.beats[0].participants[0].presentation_ref is None
    later['beats'][0]['participants'][0]['presentation_ref'] = 'a1'
    second = compiler(PlotChapterResponse.model_validate(later))
    assert set(available_actor_bindings([first, second], 2)) == {'ap01_01', 'ap02_01'}
    assert state_materials([first, second], ['st01_01']) == carried
    validate_book([first, second], {1: SOURCE, 2: SOURCE})
    with pytest.raises(ValueError, match='原始使用章'):
        available_actor_bindings([first, second], 3)


def test_typed_presentation_replace_retires_old_id_and_reserves_next_id():
    previous = compiled()
    raw = response_projection(previous.model_dump(mode='json'), SOURCE)
    identities = [dict(collection=collection, kind='preserve', old_refs=[getattr(old, field)], new_keys=[new[key]])
        for collection, field, key, _ in plot_edit.COLLECTIONS
        for old, new in zip(getattr(previous, collection), raw[collection], strict=True)]
    next(row for row in identities if row['old_refs'] == ['ap01_01'])['kind'] = 'replace'
    result, changes, tombstones = plot_edit._compile_edit(PlotChapterEdit(chapter=raw, identities=identities),
        previous, SOURCE, available=frozenset(), chars=CHARS, globals_=frozenset(),
        retired={'ap01_03'}, character_bindings=INVENTORY)
    assert result.actor_bindings[0].presentation_id == 'ap01_04'
    assert result.beats[0].participants[0].presentation_ref == 'ap01_04'
    assert tombstones[0].node_id == 'ap01_01'
    assert tombstones[0].superseded_by == ['ap01_04']
