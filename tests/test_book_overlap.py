#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""重合工具以真实当前产物与身份回执重放成书；不逆猜姓名，不把缺证据记作零。"""
from __future__ import annotations

import hashlib
import json

import pytest

from contracts.artifacts import str06_body_receipt_rel, str06_body_rel
from contracts.invariants import BOOK_BLOCK_SEP
from contracts.schema import encode_artifact
from contracts.schema.naming import NameMapV5
from contracts.schema.receipt import (
    BODY_MECHANICAL_CHECKS, BODY_VALIDATOR_VERSION, ChapterReceipt,
    RENDERER_VERSION, WRITER_PROTOCOL_VERSION,
)
from contracts.schema.source import CharactersArtifactV6
from contracts.schema.stage1 import MainText
from novel.core.name_text import resolve_name_text
from novel.paths import artifact_path, book_txt_rel
from tools.book_overlap import measure, split_book


def _book(*chapters: str) -> str:
    return BOOK_BLOCK_SEP.join(['文案一句。', *(
        f'第{ordinal}章\n{body}' for ordinal, body in enumerate(chapters, 1))])


def test_a_chapter_body_that_has_blank_lines_is_not_cut_at_its_first_paragraph():
    body = BOOK_BLOCK_SEP.join(['第一段。', '第二段。', '第三段。'])
    assert split_book(_book(body, '另一章。')) == {1: body, 2: '另一章。'}


def test_a_bare_number_inside_a_body_is_not_mistaken_for_a_chapter_head():
    body = BOOK_BLOCK_SEP.join(['他点开帖子。', '2', '楼上说得对。'])
    assert split_book(_book(body)) == {1: body}


def test_the_hook_block_has_no_chapter_number_and_never_enters_the_table():
    assert split_book(_book('正文。')) == {1: '正文。'}


def _write_artifact(novel_dir, rel, model):
    path = artifact_path(novel_dir, rel)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(encode_artifact(
        rel, model.model_dump(mode='json', by_alias=True)), ensure_ascii=False), encoding='utf-8')


def _fixture(tmp_path):
    novel_dir = tmp_path / '同形人物_123'
    novel_dir.mkdir()
    characters = CharactersArtifactV6.model_validate({
        'characters': {char_id: {'canonical': '白', 'surfaces': [{
            'surface': '白', 'from_chapter': 1, 'source_refs': ['e01_01']}]}
            for char_id in ('first', 'second')},
        'initial_state': [],
        'name_structure': {
            'families': {family: {'literal': '白', 'forms': []}
                         for family in ('fam_first', 'fam_second')},
            'people': {char_id: {'family_id': f'fam_{char_id}', 'given': None,
                                 'aliases': [], 'surface_forms': {'白': '{family}'}}
                       for char_id in ('first', 'second')}}})
    names = NameMapV5.model_validate({'entries': [
        {'char_ref': 'first', 'name': {'family': '沈'}},
        {'char_ref': 'second', 'name': {'family': '柳'}}]})
    declared = (
        '[[name:first|白]]走到院门前等待来客，[[name:second|白]]在花架后站定。'
        '[[text|我心里明白，白天的雨会让石阶湿滑，因此仍须慢慢行走。]]')
    resolved = resolve_name_text(declared, {'first': ('白',), 'second': ('白',)})
    assert not resolved.errors
    second = resolve_name_text('[[text|窗外的雨落在树梢，又沿着屋檐流向院中的青石，整夜没有停歇。]]')
    originals = (resolved, second)
    delivered = (
        '沈走到院门前等待来客，柳在花架后站定。'
        '我心里明白，白天的雨会让石阶湿滑，因此仍须慢慢行走。',
        second.text)
    _write_artifact(novel_dir, 'stage2/characters/characters.json', characters)
    _write_artifact(novel_dir, 'stage7/naming/name_map.json', names)
    _write_artifact(novel_dir, 'stage1/main_text.json', MainText.model_validate([
        {'chapter_ordinal': ordinal, 'text': original.text}
        for ordinal, original in enumerate(originals, 1)]))
    for ordinal, original in enumerate(originals, 1):
        body_path = artifact_path(novel_dir, str06_body_rel(ordinal))
        body_path.parent.mkdir(parents=True, exist_ok=True)
        body_path.write_bytes(original.text.encode('utf-8'))
        receipt = ChapterReceipt(
            chapter_ordinal=ordinal, writer_view_sha256='a' * 64,
            validation_sha256='b' * 64, machine_view_sha256='c' * 64,
            prose_sha256=hashlib.sha256(original.text.encode('utf-8')).hexdigest(),
            name_mentions=original.mentions, non_name_spans=original.non_name_spans,
            request_identity=f'test-chapter-{ordinal}',
            protocol_version=WRITER_PROTOCOL_VERSION, renderer_version=RENDERER_VERSION,
            validator_version=BODY_VALIDATOR_VERSION, mechanical_checks=BODY_MECHANICAL_CHECKS,
            status='accepted', length={
                'char_count': len(original.text), 'target_chars': len(original.text),
                'band_low': 1, 'band_high': len(original.text) * 2,
                'within_band': True, 'deviation_ratio': 0})
        _write_artifact(novel_dir, str06_body_receipt_rel(ordinal), receipt)
    book_path = artifact_path(novel_dir, book_txt_rel(novel_dir))
    book_path.parent.mkdir(parents=True, exist_ok=True)
    book_path.write_bytes(_book(*delivered).encode('utf-8'))
    return novel_dir, book_path, originals, delivered


def test_measure_replays_same_surface_by_identity_and_preserves_ordinary_homographs(tmp_path):
    novel_dir, book_path, originals, delivered = _fixture(tmp_path)
    result = measure(tmp_path, '123')
    assert [row['overlap'] for row in result['chapters']] == [1.0, 1.0]
    assert result['chapters'][0]['name_changes_verified'] == 2
    assert result['chapters'][0]['chars'] == len(originals[0].text)
    assert split_book(book_path.read_bytes().decode('utf-8'))[1] == delivered[0]
    assert '明白，白天' in delivered[0], '普通同形字保持原字面，只有绑定提及改名'
    assert result['novel_dir'] == str(novel_dir)


@pytest.mark.parametrize('damage', [
    'ordinal', 'ordinal_bool', 'status', 'hash', 'protocol', 'renderer', 'validator', 'checks',
    'missing_mentions', 'missing_ordinary', 'unknown_person', 'wrong_surface_span',
    'overlapping_tables', 'unclassified', 'body_bytes',
])
def test_bad_current_receipt_cannot_produce_a_false_overlap(tmp_path, damage):
    novel_dir, _book_path, originals, _delivered = _fixture(tmp_path)
    path = artifact_path(novel_dir, str06_body_receipt_rel(1))
    raw = json.loads(path.read_text(encoding='utf-8'))
    if damage == 'ordinal':
        raw['chapter_ordinal'] = 2
    elif damage == 'ordinal_bool':
        raw['chapter_ordinal'] = True
    elif damage == 'status':
        raw['status'] = 'rejected'
    elif damage == 'hash':
        raw['prose_sha256'] = '0' * 64
    elif damage in ('protocol', 'renderer', 'validator'):
        raw[damage + '_version'] = 'obsolete'
    elif damage == 'checks':
        raw['mechanical_checks'].remove('name_text')
    elif damage == 'missing_mentions':
        del raw['name_mentions']
    elif damage == 'missing_ordinary':
        del raw['non_name_spans']
    elif damage == 'unknown_person':
        raw['name_mentions'][0]['char_id'] = 'unknown'
    elif damage == 'wrong_surface_span':
        raw['name_mentions'][0].update(start=1, end=2)
    elif damage == 'overlapping_tables':
        raw['non_name_spans'] = [{'start': 0, 'end': len(originals[0].text)}]
    elif damage == 'unclassified':
        raw['name_mentions'], raw['non_name_spans'] = [], []
    elif damage == 'body_bytes':
        artifact_path(novel_dir, str06_body_rel(1)).write_bytes((originals[0].text + '\n').encode('utf-8'))
    path.write_text(json.dumps(raw, ensure_ascii=False), encoding='utf-8')
    rows = measure(tmp_path, '123')['chapters']
    assert rows[0]['overlap'] is None and rows[0]['why']
    assert rows[1]['overlap'] == 1.0, '局部坏证据不污染另一章读数'


@pytest.mark.parametrize('extra', ['正文表外改字', '\n'])
def test_final_text_changes_outside_bound_mentions_are_not_silently_measured(tmp_path, extra):
    _novel_dir, book_path, _originals, delivered = _fixture(tmp_path)
    book_path.write_bytes((_book(*delivered) + extra).encode('utf-8'))
    rows = measure(tmp_path, '123')['chapters']
    assert rows[0]['overlap'] == 1.0
    assert rows[1]['overlap'] is None and '表外改动' in rows[1]['why']


def test_missing_receipt_is_unmeasured_and_missing_name_map_is_not_an_empty_mapping(tmp_path):
    novel_dir, _book_path, _originals, _delivered = _fixture(tmp_path)
    artifact_path(novel_dir, str06_body_receipt_rel(1)).unlink()
    rows = measure(tmp_path, '123')['chapters']
    assert len(rows) == 2 and rows[0]['overlap'] is None and rows[0]['why']
    assert rows[1]['overlap'] == 1.0
    artifact_path(novel_dir, 'stage7/naming/name_map.json').unlink()
    with pytest.raises(SystemExit, match='无法度量'):
        measure(tmp_path, '123')


def test_missing_delivered_chapter_stays_in_the_measurement_denominator(tmp_path):
    _novel_dir, book_path, _originals, delivered = _fixture(tmp_path)
    book_path.write_bytes(_book(delivered[0]).encode('utf-8'))
    rows = measure(tmp_path, '123')['chapters']
    assert len(rows) == 2 and rows[0]['overlap'] == 1.0
    assert rows[1]['overlap'] is None and rows[1]['why'] == '成书缺少这一章'
