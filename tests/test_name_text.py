"""身份引用的机械闭包：同形普通词、具体人物、坐标与复验分开。"""
import pytest

from contracts.schema.name_text import NameMention, NameTextSpan
from novel.core.name_text import resolve_name_text, validate_resolved_name_text


@pytest.mark.parametrize('word', ['明白', '对白', '若非', '谢恩', '顾着脸面', '礼数周到', '平安'])
def test_ordinary_reading_is_not_a_person_reference(word):
    result = resolve_name_text(f'[[text|{word}]]', {'bai': ('白姑娘',)},
                               candidate_literals=('白', '若', '谢', '顾', '周', '平安'))
    assert result.text == word and not result.mentions and not result.errors
    assert not validate_resolved_name_text(
        result.text, name_mentions=result.mentions, non_name_spans=result.non_name_spans,
        surfaces={'bai': ('白姑娘',)}, candidate_literals=('白', '若', '谢', '顾', '周', '平安'))


def test_reference_and_nonperson_homograph_keep_compiler_positions():
    result = resolve_name_text('[[name:bai|白姑娘]]说：[[text|我明白]]。',
                               {'bai': ('白姑娘',)}, candidate_literals=('白',))
    assert result.text == '白姑娘说：我明白。'
    assert result.mentions == (NameMention(start=0, end=3, char_id='bai', surface='白姑娘'),)
    assert result.non_name_spans == (NameTextSpan(start=5, end=8),)
    assert not result.errors


def test_unmarked_candidate_is_unknown_instead_of_proved_leak():
    result = resolve_name_text('我明白', {}, candidate_literals=('白',))
    assert {e.code for e in result.errors} == {'name_text_unclassified'}


@pytest.mark.parametrize('wire', ['[[name:absent|白姑娘]]', '[[name:bai|白某]]'])
def test_unregistered_identity_cannot_be_guessed(wire):
    assert any(e.code == 'name_text_reference' for e in
               resolve_name_text(wire, {'bai': ('白姑娘',)}).errors)


@pytest.mark.parametrize('wire', ['[[text|', '[[wat|白]]', '[[text|]]', '[[text|a|b]]',
                                  '[[text|[[name:bai|白姑娘]]]]', '多余]]'])
def test_malformed_identity_text_is_rejected(wire):
    assert any(e.code == 'name_text_syntax' for e in resolve_name_text(wire, {}).errors)


def test_reading_tables_cannot_cover_each_other_or_change_surface():
    errors = validate_resolved_name_text(
        '白姑娘', surfaces={'bai': ('白姑娘',)}, candidate_literals=('白',),
        name_mentions=(NameMention(start=0, end=2, char_id='bai', surface='白姑娘'),),
        non_name_spans=(NameTextSpan(start=1, end=3),))
    assert {'name_text_surface', 'name_text_span'} <= {e.code for e in errors}


def test_multiple_people_with_same_surface_remain_distinct():
    result = resolve_name_text('[[name:a|平安]]见到[[name:b|平安]]。[[text|一路平安]]',
                               {'a': ('平安',), 'b': ('平安',)}, candidate_literals=('平安',))
    assert not result.errors
    assert tuple(m.char_id for m in result.mentions) == ('a', 'b')


def test_decoded_occurrence_retains_exact_marker_source_interval():
    wire = '前[[name:a|白姑娘]]后'
    result = resolve_name_text(wire, {'a': ('白姑娘',)})
    assert len(result.text) == len(result.origin_spans)
    assert {span for span in result.origin_spans[1:4]} == {(1, len(wire) - 1)}


def test_identity_reference_uses_existing_unicode_identity_contract():
    result = resolve_name_text('[[name:人物.甲|平安]]', {'人物.甲': ('平安',)})
    assert not result.errors and result.mentions[0].char_id == '人物.甲'


@pytest.mark.parametrize('wire,candidate', [('[[text|明]][[text|白]]', '明白'),
                                          ('[[name:a|白]][[text|若非如此]]', '白若')])
def test_candidate_crossing_declared_fragments_is_fully_explained(wire, candidate):
    result = resolve_name_text(wire, {'a': ('白',)}, candidate_literals=(candidate,))
    assert not result.errors


def test_wire_diagnostic_position_is_distinct_from_plain_prose_position():
    wire = '[[text|前]]明白'
    result = resolve_name_text(wire, {}, candidate_literals=('白',))
    issue = result.errors[0]
    assert issue.coordinate_space == 'wire' and wire[issue.start:issue.end] == '白'
