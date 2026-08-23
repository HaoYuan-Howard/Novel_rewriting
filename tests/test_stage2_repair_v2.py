#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
test_stage2_repair_v2.py —— `stage2.blueprint --patch` 的字段级修复（5A 最小集）。

覆盖：路由规划（可修/整章级/cascade 三分）· 消歧扩展的确定性 ·
端到端（ir1 INVALID → 候选选择 + 短语重给 → 全量重编译 → 正式落盘，
`Repair.fields` 记具体路径不再恒 `['*']`）· v3 基线与整章级根因的显式拒绝。
"""
from __future__ import annotations

import re
import tempfile
from pathlib import Path

import pytest

from contracts.schema import MainText
from contracts.schema.stage2 import ChapterBlueprints
from novel.errors import GateBlocked
from novel.stages.stage2 import extract
from novel.stages.stage2.contract import CONTRACTS
from novel.stages.stage2.paragraphs import paragraph_spans, span_of
from novel.stages.stage2.repair import (
    disambiguate_phrase, plan_repairs, run_field_patches,
)
from novel.store import ArtifactStore
from tests.test_stage2 import main_text
from tests.test_stage2_contract_dispatch import (
    _ENV_KEY, _ir1_responses, _ir2_responses, _scaffolded_responses,
    _summary_for,
)
from tests.test_stage2_generation import _client, _ctx, _ok, _response_global

#: 适用面**从注册表派生**，不在测试里另写一份名单——那份名单漂了不会有东西出声，
#: 而 `--patch` 曾经就是被本文件外的一句写死架构名挡住了新臂。
_PATCHABLE = sorted(a for a, s in CONTRACTS.items() if s.supports_field_patch)
_UNPATCHABLE = sorted(a for a, s in CONTRACTS.items() if not s.supports_field_patch)


# ─────────────────────────────────────────────────────────────────────────────
# 一、纯函数：路由规划与消歧扩展
# ─────────────────────────────────────────────────────────────────────────────

def _artifact_raw(*, failures, pending=(), beats) -> dict:
    return {
        'blueprints': [{'chapter_ordinal': 1, 'beats': list(beats)}],
        '_provenance': {'verbatim_failures': [list(r) for r in failures],
                        'hinge_ambiguous_pending': [list(r) for r in pending]},
    }


def test_plan_repairs_partitions_fieldable_chapterlevel_and_cascade():
    beats = [
        {'beat_id': 'b01_01', 'span': {'start': 0, 'end': 9},
         'hinges': [{'kind': '物件', 'source': None},        # 歧义（可修）
                    {'kind': '硬数字', 'source': {'text': 'x'}},
                    {'kind': '因果前提', 'source': None}]},   # 编的（可修）
        {'beat_id': 'b01_02', 'span': {'start': 10, 'end': 20},
         'hinges': [{'kind': '要害台词', 'source': None}]},   # drift（整章级）
        {'beat_id': 'b01_03', 'span': None,
         'hinges': [{'kind': '物件', 'source': None}]},       # cascade（不逐条记）
    ]
    actions, leftovers = plan_repairs(_artifact_raw(
        failures=[('b01_01', 'hinge', '短语在本拍区间内多处命中'),
                  ('b01_01', 'hinge', '短语不在本章原文里'),
                  ('b01_02', 'hinge', '短语不在它所属拍的区间里，而出现在别处')],
        pending=[('b01_01', '物件', '纸鸢')],
        beats=beats))
    assert [(a.route, a.beat_id, a.hinge_index) for a in actions] == [
        ('candidate_select', 'b01_01', 0), ('phrase_patch', 'b01_01', 2)]
    assert actions[0].phrase == '纸鸢'
    assert leftovers == ['b01_02: 短语不在它所属拍的区间里，而出现在别处']


def test_plan_repairs_reports_span_causes_as_chapter_level():
    _actions, leftovers = plan_repairs(_artifact_raw(
        failures=[('b01_01', 'span', '末拍没有到达本章最后一段')],
        beats=[{'beat_id': 'b01_01', 'span': {'start': 0, 'end': 9},
                'hinges': []}]))
    assert leftovers == ['b01_01: 末拍没有到达本章最后一段']


def test_disambiguate_phrase_expands_to_the_chosen_occurrence_only():
    seg = '她把纸鸢递过去。他接过纸鸢看了半天。'
    first = seg.find('纸鸢')
    second = seg.find('纸鸢', first + 1)
    expanded = disambiguate_phrase(seg, second, second + 2)
    assert expanded and '纸鸢' in expanded
    assert seg.count(expanded) == 1
    assert seg.find(expanded) <= second < seg.find(expanded) + len(expanded), \
        '扩展串必须覆盖选中的那一处命中'


# ─────────────────────────────────────────────────────────────────────────────
# 二、端到端：ir1 INVALID → --patch → 正式落盘
# ─────────────────────────────────────────────────────────────────────────────

def _pick_ambiguous_site(text: str, beats: list[dict]) -> tuple[int, str, str]:
    """第 1 章里第一处「拍内有重复内容字」的 (拍下标, 拍原文, 重复字)。

    动态选点：合成底座的拍不保证哪一拍有重复字，扫出来的样本才稳。
    """
    cursor = 0
    for index, beat in enumerate(beats):
        start, end = span_of(text, cursor + 1, beat['end_para'])
        cursor = beat['end_para']
        seg = text[start:end]
        for ch in seg:
            if ch.strip() and ch not in '。，「」？！\n' and seg.count(ch) >= 2:
                return index, seg, ch
    raise AssertionError('全章找不到拍内重复字——换一本合成书造样本')


def _unique_snippet(seg: str, avoid: str) -> str:
    """从这一拍里切一段唯一、且不含 avoid 的原样字节（给短语重给臂当答案）。"""
    for size in range(4, len(seg)):
        for at in range(0, len(seg) - size):
            piece = seg[at:at + size]
            if avoid not in piece and piece.strip() and seg.count(piece) == 1:
                return piece
    raise AssertionError('这一拍切不出唯一片段')


def _land_broken_ir_book(monkeypatch, architecture: str) -> tuple[Path, dict]:
    """跑指定 IR 臂的全链但给第 1 章第 1 拍塞一条歧义 hinge 和一条编的 hinge ⇒ .INVALID。

    v1/v1.1 共用开放字面响应，v2 用关闭式 char_id 响应并多一段边界预传，
    v3/v3.1 则在单次响应里带召回脚手架；
    测试从 spec 的能力与拓扑派生，不把「IR 都是一相、都同响应模型」当永久事实。
    """
    monkeypatch.setenv(_ENV_KEY, architecture)
    book = Path(tempfile.mkdtemp()) / '修复_123'
    book.mkdir(parents=True)
    global_ctx = _ctx(book, 'stage2.global')
    ArtifactStore(global_ctx).write(
        extract.MAIN_TEXT, MainText.model_validate(main_text()))
    global_client, _t = _client(
        global_ctx, lambda _req, _index: _ok(_response_global()))
    extract.run_global(global_ctx, global_client)

    spec = CONTRACTS[architecture]
    if spec.topology == 'boundary_then_detail':
        by_ordinal = _ir2_responses()
    elif spec.topology == 'scaffolded_single':
        by_ordinal = _scaffolded_responses(
            closed=spec.uses_narration_refs)
    else:
        by_ordinal = _ir1_responses()
    ordinals = sorted(by_ordinal)
    texts = {s['chapter_ordinal']: s['text'] for s in main_text()}
    beat_index, seg, dup = _pick_ambiguous_site(
        texts[1], by_ordinal[1]['beats'])
    target = by_ordinal[1]['beats'][beat_index]
    base = len(target['hinges'])
    target['hinges'] += [
        {'kind': '物件', 'phrase': dup},
        {'kind': '因果前提', 'phrase': '这句话不存在于原文里也不该存在'},
    ]

    def respond(req, _index):
        if '逐章事件清单' in req.user:
            return _ok({'chapters': [
                {'chapter_ordinal': o, 'summary': _summary_for(o)}
                for o in ordinals]})
        found = re.findall(r'阅读序第(\d+)章', req.user)
        ordinal = int(found[-1])
        if '第一张表 `micro_events`' in req.system:
            boundary_beats = [
                {'end_para': beat['end_para'], 'event': beat['event']}
                for beat in by_ordinal[ordinal]['beats']]
            return _ok({'blueprints': [{
                'chapter_ordinal': ordinal,
                'micro_events': boundary_beats,
                'beats': boundary_beats,
            }]})
        return _ok({'blueprints': [by_ordinal[ordinal]]})

    ctx = _ctx(book, 'stage2.blueprint')
    client, _transport = _client(ctx, respond)
    with pytest.raises(GateBlocked):
        extract.run_blueprint(ctx, client)
    store = ArtifactStore(ctx)
    assert store.exists(extract._INVALID)
    invalid_raw = store.read_own(ChapterBlueprints, extract._INVALID)[1]
    return book, {'dup': dup, 'seg': seg, 'invalid': invalid_raw,
                  'beat_id': f'b01_{beat_index + 1:02d}', 'beat_index': beat_index,
                  'base': base}


@pytest.mark.parametrize('architecture', _PATCHABLE)
def test_patch_repairs_ambiguous_and_void_then_lands_formally(monkeypatch, architecture):
    """⭐ 端到端：**每一条声明可字段级修的契约**都真的修得动。

    ⚠ 参数化到 `supports_field_patch` 全表，不写死架构名——`extraction-ir-v1.1`
    上线时正是被 `repair.py` 里一句写死的 `!= 'extraction-ir-v1'` 挡在门外，
    而它与 ir1 的编译器/投影/响应模型逐格共用、结构上完全修得动
    （2026-08-21 跑《四人成行》时撞出来）。新臂进注册表就自动进这条测试。
    """
    book, info = _land_broken_ir_book(monkeypatch, architecture)
    bid, base = info['beat_id'], info['base']
    prov_raw = info['invalid']['_provenance']
    assert [row[2] for row in prov_raw['verbatim_failures']] == [
        '短语在本拍区间内多处命中', '短语不在本章原文里']
    assert prov_raw['hinge_ambiguous_pending'] == [[bid, '物件', info['dup']]]

    replacement = _unique_snippet(info['seg'], avoid=info['dup'])

    def respond(req, _index):
        if '候选位置' in req.user:
            return _ok({'candidate_id': 'c1'})           # 选第二处命中
        assert '找不到' in req.user
        return _ok({'phrase': replacement})

    ctx = _ctx(book, 'stage2.blueprint')
    client, transport = _client(ctx, respond)
    report = run_field_patches(ctx, client)

    assert report.passed
    assert len(transport.calls) == 2, '一条歧义 + 一条编的 = 恰好两次字段级调用'
    store = ArtifactStore(ctx)
    stored, raw = store.read_own(ChapterBlueprints, extract.BLUEPRINT_REL)
    assert not store.exists(extract._INVALID), '修好之后问题稿应随正式稿落盘归档'
    assert raw['_provenance']['context']['architecture'] == architecture, \
        '--patch 必须与基线同构：修完的产地不能变成另一条臂的名字'

    beat = raw['blueprints'][0]['beats'][info['beat_index']]
    fixed = beat['hinges'][base:base + 2]
    assert all(h['source'] is not None for h in fixed)
    seg_start = beat['span']['start']
    # 候选选择：新 source 必须覆盖**第二处**命中（模型挑的 c1）。
    second_at = seg_start + info['seg'].find(
        info['dup'], info['seg'].find(info['dup']) + 1)
    assert fixed[0]['source']['start'] <= second_at < fixed[0]['source']['end']
    assert replacement.startswith(fixed[1]['source']['text'][:2]) or \
        fixed[1]['source']['text'].startswith(replacement[:2])

    new_prov = raw['_provenance']
    assert new_prov['verbatim_failures'] == []
    assert new_prov['hinge_ambiguous_pending'] == []
    fields = [row['fields'] for row in new_prov['repairs']]
    assert fields == [[f'beats[{bid}].hinges[{base}].phrase'],
                      [f'beats[{bid}].hinges[{base + 1}].phrase']], \
        'Repair.fields 必须记具体路径，不再恒 ["*"]'
    assert {'HINGE_SELECT_SYS', 'HINGE_PHRASE_SYS'} <= set(new_prov['prompts'])
    adoption = [row for row in ctx.ledger.audit_events
                if row['kind'] == 'artifact_adoption']
    assert {row['purpose'] for row in adoption} == {
        f'stage2.blueprint:patch:{bid}.h{base}',
        f'stage2.blueprint:patch:{bid}.h{base + 1}'}
    assert stored.provenance.usage is not None


def test_all_retained_contracts_support_field_patch():
    assert _UNPATCHABLE == []
    assert _PATCHABLE == [
        'extraction-ir-v1.1', 'extraction-ir-v2', 'extraction-ir-v3.1']
