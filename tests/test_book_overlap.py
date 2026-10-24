#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""重合工具以真实当前产物与身份回执重放成书；不逆猜姓名，不把缺证据记作零。

rc26（命名前移批 C）：度量使用 Stage5 已接受的最终正文——命名前移后正文
直接以目标姓名成文，装配零改名。采用前逐章核对当前 ChapterReceipt 的章序、
状态、正文原始字节 hash、协议与机械检查项；只有已接受正文与成书该章逐字节
一致才度量。缺证据或存在表外改动时明确“量不了”。
"""
from __future__ import annotations

import hashlib
import json

import pytest

from contracts.artifacts import str06_body_receipt_rel, str06_body_rel
from contracts.invariants import BOOK_BLOCK_SEP
from contracts.schema import encode_artifact
from contracts.schema.naming import NameMapV6
from contracts.schema.receipt import (
    BODY_MECHANICAL_CHECKS, BODY_VALIDATOR_VERSION, ChapterReceipt,
    RENDERER_VERSION, WRITER_PROTOCOL_VERSION,
)
from contracts.schema.source import CharactersArtifactV7
from contracts.schema.stage1 import MainText
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
    """rc26 零改名度量夹具：已接受正文即最终成文（源章旧名、成书目标姓名）。"""
    novel_dir = tmp_path / '同形人物_123'
    novel_dir.mkdir()
    characters = CharactersArtifactV7.model_validate({'initial_knowledge': [],
        'characters': {char_id: {'identity_bindings': [], 'canonical': '白', 'surfaces': [{
            'surface': '白', 'from_chapter': 1, 'source_refs': ['e01_01']}]}
            for char_id in ('first', 'second')},
        'initial_state': [],
        'name_structure': {
            'families': {family: {'literal': '白', 'forms': []}
                         for family in ('fam_first', 'fam_second')},
            'people': {char_id: {'family_id': f'fam_{char_id}', 'given': None,
                                 'aliases': [], 'surface_forms': {'白': '{family}'}}
                       for char_id in ('first', 'second')}}})
    names = NameMapV6.model_validate({'entries': [
        {'char_ref': 'first', 'name': {'family': '沈'}},
        {'char_ref': 'second', 'name': {'family': '柳'}}]})
    # 源章（Stage1 原文，旧名）；成书该章＝Stage5 已接受正文（目标姓名，零改名）。
    originals = (
        '白走到院门前等待来客，白在花架后站定。'
        '我心里明白，白天的雨会让石阶湿滑，因此仍须慢慢行走。',
        '窗外的雨落在树梢，又沿着屋檐流向院中的青石，整夜没有停歇。')
    delivered = (
        '沈走到院门前等待来客，柳在花架后站定。'
        '我心里明白，白天的雨会让石阶湿滑，因此仍须慢慢行走。',
        originals[1])
    _write_artifact(novel_dir, 'stage2/characters/characters.json', characters)
    _write_artifact(novel_dir, 'stage4/naming/name_map.json', names)
    _write_artifact(novel_dir, 'stage1/main_text.json', MainText.model_validate([
        {'chapter_ordinal': ordinal, 'text': original}
        for ordinal, original in enumerate(originals, 1)]))
    for ordinal, body in enumerate(delivered, 1):
        body_path = artifact_path(novel_dir, str06_body_rel(ordinal))
        body_path.parent.mkdir(parents=True, exist_ok=True)
        body_path.write_bytes(body.encode('utf-8'))
        receipt = ChapterReceipt(
            chapter_ordinal=ordinal, writer_view_sha256='a' * 64,
            validation_sha256='b' * 64, machine_view_sha256='c' * 64,
            prose_sha256=hashlib.sha256(body.encode('utf-8')).hexdigest(),
            request_identity=f'test-chapter-{ordinal}',
            protocol_version=WRITER_PROTOCOL_VERSION, renderer_version=RENDERER_VERSION,
            validator_version=BODY_VALIDATOR_VERSION, mechanical_checks=BODY_MECHANICAL_CHECKS,
            status='accepted', length={
                'char_count': len(body), 'target_chars': len(body),
                'band_low': 1, 'band_high': len(body) * 2,
                'within_band': True, 'deviation_ratio': 0})
        _write_artifact(novel_dir, str06_body_receipt_rel(ordinal), receipt)
    book_path = artifact_path(novel_dir, book_txt_rel(novel_dir))
    book_path.parent.mkdir(parents=True, exist_ok=True)
    book_path.write_bytes(_book(*delivered).encode('utf-8'))
    return novel_dir, book_path, originals, delivered


def test_measure_uses_accepted_final_prose_byte_for_byte(tmp_path):
    """rc26 零改名度量：已接受正文与成书该章逐字节一致才度量（R-10.5 重放面）。"""
    novel_dir, book_path, originals, delivered = _fixture(tmp_path)
    result = measure(tmp_path, '123')
    # 度量对象是**已接受正文 vs 源章**：改名后重合下降但不为 None（不是量不了）
    assert all(row['overlap'] is not None for row in result['chapters'])
    assert result['chapters'][0]['chars'] == len(delivered[0])
    assert split_book(book_path.read_bytes().decode('utf-8'))[1] == delivered[0]
    # 源章是旧名、成书是目标姓名：度量照常给出（不按姓名豁免，也不逆猜姓名）
    assert '白走到院门前' in originals[0] and '沈走到院门前' in delivered[0]
    assert '明白，白天' in delivered[0], '普通同形字保持原字面，正文直接以目标姓名成文'
    assert result['novel_dir'] == str(novel_dir)


@pytest.mark.parametrize('damage', [
    'ordinal', 'ordinal_bool', 'status', 'hash', 'protocol', 'renderer', 'validator', 'checks',
    'body_bytes',
])
def test_bad_current_receipt_cannot_produce_a_false_overlap(tmp_path, damage):
    """坏回执/坏字节不能给出假重合；局部坏证据不污染另一章读数。"""
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
        raw['mechanical_checks'] = ('non_empty',)
    elif damage == 'body_bytes':
        artifact_path(novel_dir, str06_body_rel(1)).write_bytes((originals[0] + '\n').encode('utf-8'))
    path.write_text(json.dumps(raw, ensure_ascii=False), encoding='utf-8')
    rows = measure(tmp_path, '123')['chapters']
    assert rows[0]['overlap'] is None and rows[0]['why']
    assert rows[1]['overlap'] is not None, '局部坏证据不污染另一章读数'


@pytest.mark.parametrize('extra', ['正文表外改字', '\n'])
def test_final_text_changes_outside_bound_mentions_are_not_silently_measured(tmp_path, extra):
    """rc26：成书表外任何改动（不只姓名）都不能静默进入度量。"""
    _novel_dir, book_path, _originals, delivered = _fixture(tmp_path)
    book_path.write_bytes((_book(*delivered) + extra).encode('utf-8'))
    rows = measure(tmp_path, '123')['chapters']
    assert rows[0]['overlap'] is not None
    assert rows[1]['overlap'] is None and '表外改动' in rows[1]['why']


def test_missing_receipt_is_unmeasured_and_missing_delivery_stays_unknown(tmp_path):
    novel_dir, _book_path, _originals, _delivered = _fixture(tmp_path)
    artifact_path(novel_dir, str06_body_receipt_rel(1)).unlink()
    rows = measure(tmp_path, '123')['chapters']
    assert len(rows) == 2 and rows[0]['overlap'] is None and rows[0]['why']
    assert rows[1]['overlap'] is not None, '局部坏证据不污染另一章读数'


def test_missing_delivered_chapter_stays_in_the_measurement_denominator(tmp_path):
    _novel_dir, book_path, _originals, delivered = _fixture(tmp_path)
    book_path.write_bytes(_book(delivered[0]).encode('utf-8'))
    rows = measure(tmp_path, '123')['chapters']
    assert len(rows) == 2 and rows[0]['overlap'] is not None
    assert rows[1]['overlap'] is None and rows[1]['why'] == '成书缺少这一章'
