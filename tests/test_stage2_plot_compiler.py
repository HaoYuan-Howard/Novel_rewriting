"""V10 段级证据编译与可见人物闭集，保留旧定位/越界反例的当前契约版本。

旧候选表、自动挪动拍边界、逐短语修复已没有模型调用口，因此不保留它们的
成功操作测试。原有失败风险改由显式段范围、人物闭集、源文摘要及原样字面检验。
"""
from __future__ import annotations
import copy
import json
import pytest
from pydantic import ValidationError
from contracts.schema.plot import PlotChapterResponse, PlotBlueprint
from novel.stages.stage2.character_refs import visible_character_refs
from novel.stages.stage2.plot_compile import compile_chapter, validate_evidence, recompile_evidence, evidence_texts
from tests.test_plot_blueprint import chapter_response, CHARS, SOURCE


CH1 = ('李宝珠推开院门，井边的水汽漫上来。\n'
       '「今天的第一课，」母亲说，「是挑水。」\n'
       '她挑了两担水，肩膀磨出了红印。\n'
       '母亲看着她，又说了一句话。\n'
       '傍晚她回屋，把三十文钱放在桌上。')



def _main_text(text: str = CH1, ordinal: int = 1) -> list[dict]:
    return [{'chapter_ordinal': ordinal, 'text': text}]



CHARACTERS = {
    'char_001': {
        'canonical': '李宝珠', 'name_distinctiveness': 'distinct',
        'aliases': [{'surface': '李宝珠', 'by': 'narration',
                     'context': 'reference', 'identity_revealed_from': 1}],
        'role': '略', 'fate': None,
    },
    'char_002': {
        'canonical': '母亲', 'name_distinctiveness': 'generic',
        'aliases': [{'surface': '母亲', 'by': 'narration',
                     'context': 'reference', 'identity_revealed_from': 1}],
        'role': '略', 'fate': None,
    },
}



def test_visible_character_refs_never_leak_a_future_surface():
    mt = [
        {'chapter_ordinal': 1, 'text': '李宝珠独自推开门。'},
        {'chapter_ordinal': 2, 'text': '母亲这才从门后走出来。'},
    ]
    visible = visible_character_refs(CHARACTERS, mt)
    assert visible[1] == {'char_001': ['李宝珠']}
    assert visible[2] == {
        'char_001': ['李宝珠'], 'char_002': ['母亲']}



def test_visible_refs_do_not_grant_a_future_owner_of_an_already_seen_surface():
    """字面出现过不等于挂这个字面的所有人都已经可见。"""
    characters = copy.deepcopy(CHARACTERS)
    characters['char_003'] = {
        'canonical': '后来者', 'name_distinctiveness': 'distinct', 'first_chapter': 2,
        'aliases': [{'surface': '后来者', 'identity_revealed_from': 2},
                    {'surface': '母亲', 'identity_revealed_from': 2}],
        'role': '略', 'fate': None,
    }
    mt = [
        {'chapter_ordinal': 1, 'text': '母亲先在门边等着。'},
        {'chapter_ordinal': 2, 'text': '后来者也被人叫作母亲。'},
    ]
    visible = visible_character_refs(characters, mt)
    assert 'char_003' not in visible[1]
    assert visible[2]['char_003'] == ['后来者', '母亲']



def test_canonical_surface_shadows_the_same_address_clock():
    """身份映射晚揭示不禁止已在原文出现的称谓字面。"""
    characters = copy.deepcopy(CHARACTERS)
    characters['char_002']['aliases'][0]['identity_revealed_from'] = 9

    visible = visible_character_refs(
        characters, [{'chapter_ordinal': 1, 'text': '母亲先在门边等着。'}])

    assert visible[1]['char_002'] == ['母亲']



def _two_maids_one_name():
    """两个角色在 ch1 只剩同一个可见字面；第三个人可区分。

    `canonical_unique` 只保证**完整** canonical 唯一，而消歧靠的正是尾部区分词，
    `strip_distinguisher` 会把它剥掉 —— 这就是碰撞的真实来路。
    """
    characters = {
        'C001': {'canonical': '小宫女(打翻茶盏)',
                 'aliases': [{'surface': '小宫女', 'identity_revealed_from': 1}]},
        'C002': {'canonical': '小宫女(捧灯)',
                 'aliases': [{'surface': '小宫女', 'identity_revealed_from': 1}]},
        'C003': {'canonical': '黎玄',
                 'aliases': [{'surface': '黎玄', 'identity_revealed_from': 1}]},
    }
    main = [{'chapter_ordinal': 1, 'text': '小宫女端着茶盏，黎玄看了一眼。'}]
    return characters, main



def test_indistinguishable_char_ids_remain_candidates_for_semantic_review():
    """同名字面保留两实体，不以丢失人物代替归属审阅。"""
    from novel.stages.stage2.character_refs import visible_character_refs_with_drops

    characters, main = _two_maids_one_name()
    refs, dropped = visible_character_refs_with_drops(characters, main)

    assert sorted(refs[1]) == ['C001', 'C002', 'C003']
    assert dropped[1] == [['C001', 'C002']], dropped



def test_a_distinguishable_pair_is_still_offered():
    """只要还有一个字面能分开，就不该剔——剔多了是另一个方向的错。"""
    from novel.stages.stage2.character_refs import visible_character_refs_with_drops

    characters, main = _two_maids_one_name()
    characters['C002']['aliases'].append(
        {'surface': '捧灯宫女', 'identity_revealed_from': 1})
    main = [{'chapter_ordinal': 1,
             'text': '小宫女端着茶盏，捧灯宫女跟在后面，黎玄看了一眼。'}]
    refs, dropped = visible_character_refs_with_drops(characters, main)

    assert sorted(refs[1]) == ['C001', 'C002', 'C003']
    assert dropped == {}



def test_the_plain_helper_and_the_accounted_one_agree():
    """三个调用点必须看到逐字相同的一份，否则模型的合法答案会在编译期炸。"""
    from novel.stages.stage2.character_refs import (
        visible_character_refs, visible_character_refs_with_drops,
    )

    characters, main = _two_maids_one_name()
    assert visible_character_refs(characters, main) == \
        visible_character_refs_with_drops(characters, main)[0]


def _compile(raw=None, source=SOURCE):
    return compile_chapter(PlotChapterResponse.model_validate(raw or chapter_response()),
                           source, allowed_char_ids=CHARS)


def test_compiler_keeps_complete_content_once_and_deduplicates_shared_ranges():
    raw = chapter_response()
    raw['beats'][1]['evidence'] = copy.deepcopy(raw['beats'][0]['evidence'])
    raw['beats'][1]['source_contexts'] = []
    raw['narration'][0]['evidence'] = copy.deepcopy(raw['beats'][0]['evidence'])
    result = _compile(raw)
    assert [row.content for row in result.beats] == [row['content'] for row in raw['beats']]
    assert len(result.evidence_anchors) == 1
    assert result.beats[0].evidence_refs == result.beats[1].evidence_refs
    assert '"text":' not in json.dumps(result.model_dump(mode='json'), ensure_ascii=False)


@pytest.mark.parametrize('field', ['participants', 'narrator', 'focalizer', 'present'])
def test_unprovided_character_cannot_enter_any_semantic_reference(field):
    raw = chapter_response()
    if field == 'participants':
        raw['beats'][0][field] = [{'char_id': 'char_999999'}]
    elif field == 'present':
        raw['beats'][0]['source_contexts'][0][field] = [{'char_id': 'char_999999'}]
    else:
        raw['narration'][0][field] = {'char_id': 'char_999999'}
    with pytest.raises(ValueError, match='未提供的人物'):
        _compile(raw)


@pytest.mark.parametrize('span', [
    {'start_para': 0, 'end_para': 1}, {'start_para': 2, 'end_para': 1},
    {'start_para': 1, 'end_para': 3}, {'start_para': True, 'end_para': 1},
    {'start_para': '1', 'end_para': 1}, {'start_para': 1.2, 'end_para': 2},
])
def test_bad_paragraph_ranges_are_rejected_without_boundary_nudging(span):
    raw = chapter_response()
    raw['beats'][0]['evidence'] = [span]
    before = copy.deepcopy(raw)
    with pytest.raises(ValueError):
        _compile(raw)
    assert raw == before


@pytest.mark.parametrize('extra', [
    {'phrase': '原文没有的短语'}, {'text': '伪造证据'}, {'start': 0, 'end': 4},
])
def test_model_cannot_supply_fake_source_text_or_coordinates(extra):
    raw = chapter_response()
    raw['beats'][0]['evidence'][0].update(extra)
    with pytest.raises(ValidationError):
        _compile(raw)


@pytest.mark.parametrize(('source', 'paragraph', 'expected'), [
    ('冰冷链子。\n\n他说：「别动！」', 1, '冰冷链子。'),
    ('温夏白脸上的笑意僵住。\n\n温夏白:「......」', 2, '温夏白:「......」'),
    ('我母妃心系百姓。\n\n「母......」', 2, '「母......」'),
])
def test_evidence_uses_exact_selected_paragraph_not_another_same_surface(source, paragraph, expected):
    raw = chapter_response()
    raw['beats'] = raw['beats'][:1]
    raw['beats'][0]['evidence'] = [{'start_para': paragraph, 'end_para': paragraph}]
    raw['beats'][0]['source_contexts'] = []
    result = _compile(raw, source)
    assert evidence_texts(result, source, result.beats[0].evidence_refs) == [expected]


def test_valid_evidence_coverage_does_not_certify_plot_semantics():
    raw = chapter_response()
    raw['beats'][0]['content'] = '人物做了一件事。'
    result = _compile(raw)
    validate_evidence(result, SOURCE)
    # 编译器保存待审完整声明，不根据证据覆盖补全因果或悄悄发语义通过。
    assert result.beats[0].content == '人物做了一件事。'
    assert set(type(result).model_fields).isdisjoint({'accepted', 'semantic_pass', 'receipt'})


def test_free_revalidation_repairs_only_coordinates_and_preserves_identity():
    result = _compile()
    raw = result.model_dump(mode='json')
    raw['evidence_anchors'][0]['start'] += 1
    drifted = PlotBlueprint.model_validate(raw)
    with pytest.raises(ValueError, match='坐标'):
        validate_evidence(drifted, SOURCE)
    assert recompile_evidence(drifted, SOURCE) == result
    with pytest.raises(ValueError, match='源文身份'):
        recompile_evidence(result, SOURCE + '变更')


@pytest.mark.parametrize('wrong', ['A17', 'a-17', 'A—17', 'A-71'])
def test_literal_cannot_be_normalized_into_a_different_source_value(wrong):
    source = '他要求进门报 A-17。\n\n她答应。'
    raw = chapter_response()
    raw['constraints'] = [{'constraint_key': 'c1', 'kind': 'literal',
                          'target_beat_ref': 'b1', 'value': wrong,
                          'evidence': [{'start_para': 1, 'end_para': 1}]}]
    with pytest.raises(ValueError, match='没有原文依据'):
        _compile(raw, source)
    raw['constraints'][0]['value'] = 'A-17'
    assert _compile(raw, source).constraints[0].value == 'A-17'


def test_empty_source_or_dangling_evidence_is_not_recovered_from_content():
    with pytest.raises(ValueError):
        _compile(source='')
    result = _compile().model_dump(mode='json')
    result['beats'][0]['evidence_refs'] = ['e01_99']
    with pytest.raises(ValidationError):
        PlotBlueprint.model_validate(result)
