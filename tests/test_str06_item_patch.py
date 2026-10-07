#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""test_str06_item_patch —— 条目级返修（CS-patch，2026-10-03）。

【验证边界】返修成本从「整份×轮数」降到「失败条目×轮数」的机制面：
  • 路径机械：`format_json_path`／`_merge_patches`（唯一命中替换、坏路径拒绝）；
  • patch 接受闸：全覆盖/无多余/无重复，合并版过**同一套 owner 校验**才采纳；
  • `ask_json`：可定位错误走 patch 轮（原请求作固定前缀），采纳返回
    `PatchedAnswer`（基底＋patch 多引用展开进 provenance）；patch 修不动 →
    无进展即停或退整份修订；不可定位错误直接整份修订（R3 原路径不变）；
  • 章观察定位协议 `chapter_patch_locate`：与验收闸同源（判定与定位同一把尺），
    其它闸在拦时退整份。
"""
from __future__ import annotations

import json

import pytest

from novel.llm.paid_requests import (
    PatchedAnswer, accept_patch_answer, ask_json, format_json_path,
    patch_user_prompt, _as_adoption_refs, _merge_patches,
)
from novel.llm.transport import ChatResponse
from tests.test_str06_r3_selfheal import _ScriptedTransport, _json_session, _request


# ───────────────────────── 路径机械（unit） ─────────────────────────

def test_format_json_path_round_trip():
    assert format_json_path(('beats', 1, 'effects', 0)) == 'beats[1].effects[0]'
    assert format_json_path(('beats', 0)) == 'beats[0]'
    assert format_json_path(('protocol_version',)) == 'protocol_version'


def test_merge_patches_replaces_only_target_items():
    wire = {'protocol_version': 'v', 'beats': [
        {'event': 'a', 'participants': ['ghost']},
        {'event': 'b', 'participants': ['fu_jun']}]}
    merged = _merge_patches(
        wire, [(('beats', 0), {'event': 'a', 'participants': ['fu_jun']})])
    assert merged['beats'][0]['participants'] == ['fu_jun']
    assert merged['beats'][1]['participants'] == ['fu_jun'], '未列出条目原样'
    assert wire['beats'][0]['participants'] == ['ghost'], '原 wire 不被修改（I-24）'


def test_merge_patches_refuses_unknown_path():
    with pytest.raises(KeyError):
        _merge_patches({'beats': []}, [(('beats', 5), {})])
    # 顶层可选键缺失允许补建（CS-H H3：追加式修复如 expression_overrides，
    # 值＝完整替换数组）；非顶层／中途缺键仍是坏路径。
    assert _merge_patches({'beats': []}, [(('nope',), [{'ex_id': 'ex_01'}])]) == {
        'beats': [], 'nope': [{'ex_id': 'ex_01'}]}
    with pytest.raises(KeyError):
        _merge_patches({'beats': []}, [(('beats', 'nope', 0), {})])


# ───────────────────────── patch 提示词与接受闸 ─────────────────────────

def _faulty_wire():
    return {'protocol_version': 'v', 'beats': [
        {'event': 'a', 'participants': ['ghost'], 'source_refs': ['e01_01'],
         'scene_boundary': 'new', 'temporal_mode': 'single',
         'effects': [], 'cognition': [], 'loops': []}]}


def test_patch_prompt_keeps_base_prefix_and_lists_paths():
    from novel.llm.paid_requests import PatchTarget
    prompt = patch_user_prompt(
        '【原始请求】源文与协议…',
        [PatchTarget(path=('beats', 0), issues=('第1章拍1 participants 引用未知人物 ghost',))],
        _faulty_wire())
    assert prompt.startswith('【原始请求】源文与协议…'), '原请求作固定前缀（缓存友好）'
    assert '【条目返修】' in prompt and '路径 beats[0]' in prompt
    assert 'ghost' in prompt and 'participants 引用未知人物' in prompt


def _patch_answer(patches_json: str, request_seq=0):
    class _A:
        content = patches_json
        reasoning = None
    return _A()


def test_accept_patch_answer_requires_exact_coverage():
    wire = _faulty_wire()
    targets_path = (('beats', 0),)
    validate = lambda w: ['第1章拍1 participants 引用未知人物 ghost'] \
        if w['beats'][0]['participants'] == ['ghost'] else []  # noqa: E731
    ok = json.dumps({'patches': [
        {'path': 'beats[0]',
         'value': {'event': 'a', 'participants': ['fu_jun'], 'source_refs': ['e01_01'],
                   'scene_boundary': 'new', 'temporal_mode': 'single',
                   'effects': [], 'cognition': [], 'loops': []}}]}, ensure_ascii=False)
    from novel.llm.paid_requests import PatchTarget
    targets = [PatchTarget(path=targets_path[0], issues=('participants 未知人物',))]
    merged, passed, fields = accept_patch_answer(
        _patch_answer(ok), targets, wire, validate)
    assert passed and merged['beats'][0]['participants'] == ['fu_jun']
    assert fields['parse'] == 'ok'
    # 多余路径：拒绝
    extra = json.dumps({'patches': [
        {'path': 'beats[0]', 'value': {}}, {'path': 'beats[1]', 'value': {}}]})
    merged, passed, fields = accept_patch_answer(
        _patch_answer(extra), targets, wire, validate)
    assert not passed and merged is None
    assert any('未请求的路径' in e for e in fields['errors'])
    # 缺少路径：拒绝
    merged, passed, fields = accept_patch_answer(
        _patch_answer('{"patches": []}'), targets, wire, validate)
    assert not passed and any('缺少路径' in e for e in fields['errors'])


def test_optional_targets_accept_subset_refuse_empty_and_outside_the_closed_set():
    """RC-58 可选目标闭集：子集合法、一个不选拒绝、闭集外路径拒绝。"""
    from novel.llm.paid_requests import NarrowPatchRequest, PatchTarget

    wire = {'protocol_version': 'v', 'scenes': [
        {'beats': [{'ev': 'a', 'real': []}, {'ev': 'b', 'real': []}]}]}
    closed = [PatchTarget(path=('scenes', 0, 'beats', i),
                          issues=('必保义务漏标：可承载本义务',), optional=True)
              for i in range(2)]

    def validate(w):
        return [] if any(b.get('real') for s in w['scenes'] for b in s['beats']) \
            else ['必保义务未被任何拍标注承载']

    def carrier(**over):
        return json.dumps({'patches': over.get('patches', [])}, ensure_ascii=False)

    def _beat(i, real):
        return {'ev': 'ab'[i], 'real': real}

    # ① 只覆盖选中子集（一个）即合法：合并后过同一套校验，未选拍原样。
    answer = carrier(patches=[{'path': 'scenes[0].beats[0]',
                               'value': _beat(0, ['ob01'])}])
    merged, passed, fields = accept_patch_answer(_patch_answer(answer), closed, wire, validate)
    assert passed and merged['scenes'][0]['beats'][0]['real'] == ['ob01']
    assert merged['scenes'][0]['beats'][1] == wire['scenes'][0]['beats'][1], '未选拍原样'
    # ② 一个都不给＝没有选定承载位：拒绝（不静默采纳零承载）。
    merged, passed, fields = accept_patch_answer(
        _patch_answer('{"patches": []}'), closed, wire, validate)
    assert not passed and merged is None
    assert any('一个都没有选定承载位' in e for e in fields['errors'])
    # ③ 闭集外路径：拒绝（回答路径必须落在闭集内）。
    outside = carrier(patches=[{'path': 'scenes[0].beats[0]', 'value': _beat(0, ['ob01'])},
                               {'path': 'chapters[0]', 'value': {}}])
    merged, passed, fields = accept_patch_answer(
        _patch_answer(outside), closed, wire, validate)
    assert not passed and merged is None
    assert any('未请求的路径' in e for e in fields['errors'])

    # 窄形状的可选目标行不带「当前值」（当前值随依赖切片送达，M3 返修宽度）；
    # 宽形状保持原样（无切片，模型需要当前值才能给完整替换）。
    narrow = NarrowPatchRequest(rules='协议', dependency='切片', legal_values='闭集')
    prompt = patch_user_prompt('【原始请求】', closed, wire, narrow=narrow)
    assert '当前值见依赖切片' in prompt and '【依赖切片】' in prompt
    assert '- 路径 scenes[0].beats[0]：当前值' not in prompt, \
        '可选目标闭集不逐拍内联当前值（整份 wire 会写两遍）'
    wide = patch_user_prompt('【原始请求】', closed, wire)
    assert '- 路径 scenes[0].beats[0]：当前值' in wide


# ───────────────────────── ask_json 的 patch 轮（unit） ─────────────────────────

def _locate(wire, errors):
    if len(errors) == 1 and 'ghost' in errors[0]:
        return [(('beats', 0), list(errors))]
    return None


def test_ask_json_patch_round_adopts_merged_with_multi_refs(tmp_path):
    """可定位错误 → 一原轮＋一 patch 轮即采纳；refs 展开基底＋patch 两引用。"""
    fixed_beat = json.dumps(
        {'event': 'a', 'participants': ['fu_jun'], 'source_refs': ['e01_01'],
         'scene_boundary': 'new', 'temporal_mode': 'single',
         'effects': [], 'cognition': [], 'loops': []}, ensure_ascii=False)
    transport = _ScriptedTransport([
        json.dumps(_faulty_wire(), ensure_ascii=False),
        json.dumps({'patches': [{'path': 'beats[0]', 'value': json.loads(fixed_beat)}]},
                   ensure_ascii=False)])
    base, _repo, session = _json_session(tmp_path, transport)
    data, answer = ask_json(session, 'ch01', _request(),
                            lambda w: (['第1章拍1 participants 引用未知人物 ghost']
                                       if w['beats'][0]['participants'] == ['ghost'] else []),
                            patch=_locate)
    assert data['beats'][0]['participants'] == ['fu_jun'], '采纳的是合并版'
    assert isinstance(answer, PatchedAnswer) and len(answer.patches) == 1
    assert len(transport.calls) == 2, '原轮＋patch 轮，无整份重写'
    assert '【条目返修】' in transport.calls[1].user
    assert transport.calls[1].user.startswith(transport.calls[0].user), \
        '原请求逐字前缀（供应商缓存命中最大化）'
    refs = _as_adoption_refs(answer)
    assert len(refs) == 2, 'provenance 展开基底＋patch 双引用'


def test_ask_json_unfixable_patch_stops_as_no_progress(tmp_path):
    """patch 原样重抄故障条目 → 合并版问题集与上轮相同 → 无进展即停。"""
    transport = _ScriptedTransport([
        json.dumps(_faulty_wire(), ensure_ascii=False),
        json.dumps({'patches': [{'path': 'beats[0]', 'value': _faulty_wire()['beats'][0]}]},
                   ensure_ascii=False)])
    base, _repo, session = _json_session(tmp_path, transport)
    with pytest.raises(ValueError, match='重问后无进展'):
        ask_json(session, 'ch01', _request(),
                 lambda w: (['第1章拍1 participants 引用未知人物 ghost']
                            if w['beats'][0]['participants'] == ['ghost'] else []),
                 patch=_locate)
    assert len(transport.calls) == 2, '不无限付费'


def test_ask_json_baseline_start_merges_baseline_errors_with_review_targets_in_one_round(tmp_path):
    """带基底起步：基底自身的验收错误与复核目标合并进同一轮条目返修——只发一轮，没有
    整份首轮请求；没有复核目标就没有发起理由，拒绝（零调用）。

    ⚠ 「基底自身预检未过、没有复核目标」不得从基底起步：那样每次重启重入都会重新买
    一轮、重置修订额度；该情形由编排走普通接受循环（同请求身份免费重放）。"""
    from novel.llm.paid_requests import PatchBaseline, PatchTarget, digest

    faulty = _faulty_wire()
    faulty['beats'].append({**faulty['beats'][0], 'event': 'b', 'participants': ['fu_jun']})
    baseline = PatchBaseline(wire=faulty, digest=digest(faulty))
    fixed_first = {**faulty['beats'][0], 'participants': ['fu_jun']}
    reviewed_second = {**faulty['beats'][1]}
    transport = _ScriptedTransport([json.dumps({'patches': [
        {'path': 'beats[0]', 'value': fixed_first},
        {'path': 'beats[1]', 'value': reviewed_second}]}, ensure_ascii=False)])
    base, _repo, session = _json_session(tmp_path, transport)

    def validate(w):
        return (['第1章拍1 participants 引用未知人物 ghost']
                if w['beats'][0]['participants'] == ['ghost'] else [])

    review = [PatchTarget(path=('beats', 1), issues=('上游条目变化：复核本拍',))]
    data, answer = ask_json(session, 'ch01', _request(), validate, patch=_locate,
                            baseline=baseline, review_targets=review)
    assert len(transport.calls) == 1, '基底错误＋复核目标合并成一轮，没有整份首轮请求'
    request = transport.calls[0].user
    assert '路径 beats[0]' in request and '路径 beats[1]' in request
    assert data['beats'][0]['participants'] == ['fu_jun'] and data['beats'][1] == reviewed_second
    assert isinstance(answer, PatchedAnswer) and answer.base is None and len(answer.patches) == 1
    assert len(_as_adoption_refs(answer)) == 1

    (tmp_path / 'idle').mkdir()
    idle = _ScriptedTransport([])
    _base, _repo, refused = _json_session(tmp_path / 'idle', idle)
    with pytest.raises(ValueError, match='必须给出窄复核目标'):
        ask_json(refused, 'ch01', _request(), validate, patch=_locate, baseline=baseline)
    assert not idle.calls, '拒绝发生在任何付费请求之前'


def test_no_progress_judges_root_cause_tasks_not_candidate_bytes(tmp_path):
    """判据④（CS-H H2）：无进展按根因任务判定——候选字节变化不算进展。

    两轮 patch 换了 participants 的字面（ghost→幽灵），同一条目仍引用闭集外
    人物：规则码与位置不变 ⇒ 同一任务 ⇒ 第二轮即停，不为「改了但没改对」付费。"""
    from novel.core.validation import ValidationIssue

    def validate(w):
        bad = w['beats'][0]['participants'][0] if w['beats'][0]['participants'] else ''
        return [] if bad == 'fu_jun' else [ValidationIssue(
            f'拍1 participants 引用未知人物 {bad}', code='unknown_slug',
            location=('ch01', 0), owner='chapters',
            context={'wire_paths': [['beats', 0]]})]

    def _patch(wire, errors):
        return [(('beats', 0), list(errors))]

    transport = _ScriptedTransport([
        json.dumps(_faulty_wire(), ensure_ascii=False),
        json.dumps({'patches': [{'path': 'beats[0]', 'value': {
            **_faulty_wire()['beats'][0], 'participants': ['幽灵']}}]},
            ensure_ascii=False)])
    base, _repo, session = _json_session(tmp_path, transport)
    with pytest.raises(ValueError, match='重问后无进展'):
        ask_json(session, 'ch01', _request(), validate, patch=_patch)
    assert len(transport.calls) == 2, '字节变化不算进展，第二轮即停'


def test_ask_json_non_localizable_error_falls_back_to_full_revision(tmp_path):
    """定位协议返回 None（存在不可定位错误）→ R3 整份修订原路径不变。"""
    transport = _ScriptedTransport(['{"ok": false}', '{"ok": true}'])
    base, _repo, session = _json_session(tmp_path, transport)
    data, answer = ask_json(session, 'preserve', _request(),
                            lambda w: [] if w.get('ok') else ['形状不合约定'],
                            patch=lambda w, e: None)
    assert data == {'ok': True} and not isinstance(answer, PatchedAnswer)
    assert '【修订要求】' in transport.calls[1].user, '整份修订轮'


def test_ask_json_bad_patch_shape_uses_full_revision_next(tmp_path):
    """patch 形状失败（返回整份而非 patches）→ 退整份修订轮，不丢额度。"""
    transport = _ScriptedTransport([
        json.dumps(_faulty_wire(), ensure_ascii=False),
        '{"patches": "不是列表"}',
        json.dumps({'beats': [{'event': 'a', 'participants': ['fu_jun'],
                               'source_refs': ['e01_01'], 'scene_boundary': 'new',
                               'temporal_mode': 'single', 'effects': [],
                               'cognition': [], 'loops': []}]}, ensure_ascii=False)])
    base, _repo, session = _json_session(tmp_path, transport)
    data, answer = ask_json(session, 'ch01', _request(),
                            lambda w: (['第1章拍1 participants 引用未知人物 ghost']
                                       if w['beats'][0]['participants'] == ['ghost'] else []),
                            patch=_locate)
    assert data['beats'][0]['participants'] == ['fu_jun']
    assert not isinstance(answer, PatchedAnswer), '整份轮采纳（非合并）'
    assert '【修订要求】' in transport.calls[2].user


# ───────────────────────── 章观察定位协议（unit） ─────────────────────────

def test_revisions_allow_strictly_decreasing_errors(tmp_path):
    transport = _ScriptedTransport(['{"a": false, "b": false}',
                                    '{"a": true, "b": false}',
                                    '{"a": true, "b": true}'])
    _, _, session = _json_session(tmp_path, transport)
    data, _ = ask_json(session, 'ch01', _request(),
                       lambda w: [f'{k}：缺失' for k in ('a', 'b') if not w[k]])
    assert data == {'a': True, 'b': True}
    assert len(transport.calls) == 3


def test_patch_and_full_requests_share_revision_ceiling(tmp_path):
    transport = _ScriptedTransport([
        json.dumps(_faulty_wire()), '{"patches": "坏形状"}',
        json.dumps(_faulty_wire()), '{"patches": []}', '{"ok": true}'])
    _, _, session = _json_session(tmp_path, transport)
    from novel.llm.paid_requests import ValidationStop
    with pytest.raises(ValidationStop) as caught:
        ask_json(session, 'ch01', _request(),
                 lambda w: ['拍1：ghost 仍未修复'],
                 patch=lambda w, e: [(('beats', 0), e)])
    assert len(transport.calls) == 3
    assert caught.value.context['rounds'] == 2


def test_two_partial_patches_keep_complete_adoption_refs(tmp_path):
    transport = _ScriptedTransport([
        '{"items": [false, false]}',
        '{"patches": [{"path": "items[0]", "value": true},'
        ' {"path": "items[1]", "value": false}]}',
        '{"patches": [{"path": "items[1]", "value": true}]}'])
    _, _, session = _json_session(tmp_path, transport)
    validate = lambda w: [f'条目{i}：未完成' for i, v in enumerate(w['items']) if not v]
    locate = lambda w, e: [(('items', i), [f'条目{i}：未完成'])
                           for i, v in enumerate(w['items']) if not v]
    data, answer = ask_json(session, 'ch01', _request(), validate, patch=locate)
    assert data == {'items': [True, True]}
    assert len(_as_adoption_refs(answer)) == 3
    assert len(transport.calls) == 3
    # 同身份重放不得再次购买；合并仍引用原轮及两次 patch。
    data2, answer2 = ask_json(session, 'ch01', _request(), validate, patch=locate)
    assert data2 == data and len(_as_adoption_refs(answer2)) == 3
    assert len(transport.calls) == 3

SEGMENTS = {'e01_01': {'chapter': 1, 'text': '夫君走进门来。云娘没有回头。'}}


def test_chapter_patch_locate_maps_and_guards():
    from novel.stages.stage2 import chapter_patch_locate, compile_chapter_observation
    wire = {'protocol_version': 'stage2-chapters-v4', 'beats': [
        {'event': 'a', 'participants': ['ghost'], 'source_refs': ['e01_01'],
         'scene_boundary': 'new', 'temporal_mode': 'single',
         'effects': [], 'cognition': [], 'loops': []}]}
    _observation, errors = compile_chapter_observation(
        wire, evidence_ids=SEGMENTS, chapter=1, character_slugs=('fu_jun', 'yun_niang'))
    assert len(errors) == 1 and 'ghost' in errors[0]
    targets = chapter_patch_locate(wire, errors, evidence_ids=SEGMENTS, chapter=1,
                                   character_slugs=('fu_jun', 'yun_niang'))
    assert targets == [(('beats', 0), [errors[0]])], '与验收闸同源的定位'
    # errors 与可定位集不一致（其它闸在拦）→ None：退整份，不静默漏项
    assert chapter_patch_locate(wire, errors + ['别的闸拦的'], evidence_ids=SEGMENTS,
                                chapter=1,
                                character_slugs=('fu_jun', 'yun_niang')) is None
    assert chapter_patch_locate(wire, [], evidence_ids=SEGMENTS, chapter=1,
                                character_slugs=('fu_jun', 'yun_niang')) is None


def test_quote_repair_can_update_parent_refs_in_one_round(tmp_path):
    """basis 段在本拍 src 之外（basis_segment_outside_refs）：修正可能要扩 src，
    返修定位到整拍，模型一轮内同时改 src／basis，不被锁在转移条目上。"""
    from copy import deepcopy
    from contracts.schema.observation import STAGE2_CHAPTERS_WIRE_PROTOCOL
    from novel.stages.stage2 import chapter_patch_locate, compile_chapter_observation
    evidence = {
        'e01_01': {'chapter': 1, 'text': '芸娘走进房内。'},
        'e01_02': {'chapter': 1, 'text': '夫君坦白自己不能生育。'},
    }
    wire = {'protocol_version': STAGE2_CHAPTERS_WIRE_PROTOCOL, 'beats': [{
        'ev': '夫君向芸娘坦白不能生育。', 'who': ['fu_jun', 'yun_niang'],
        'src': 'e01_01', 'new_scene': True,
        'know': [{'who': 'yun_niang', 'how': 'learns', 'what': '夫君不能生育',
                  'basis': ['e01_02']}],
    }, {'ev': '芸娘进房。', 'who': ['yun_niang'], 'src': 'e01_01'}]}

    def validate(value):
        return compile_chapter_observation(value, evidence_ids=evidence,
            chapter=1, character_slugs=('fu_jun', 'yun_niang'))[1]

    def locate(value, errors):
        return chapter_patch_locate(value, errors, evidence_ids=evidence,
            chapter=1, character_slugs=('fu_jun', 'yun_niang'))

    errors = validate(wire)
    assert errors and 'e01_02' in errors[0]
    assert locate(wire, errors) == [(('beats', 0), errors)]
    fixed = deepcopy(wire['beats'][0])
    fixed['src'] = 'e01_01..e01_02'
    transport = _ScriptedTransport([json.dumps(wire, ensure_ascii=False),
        json.dumps({'patches': [{'path': 'beats[0]', 'value': fixed}]}, ensure_ascii=False)])
    _base, _repo, session = _json_session(tmp_path, transport)
    result, answer = ask_json(session, 'ch01', _request(), validate, patch=locate)
    assert result['beats'][0] == fixed and result['beats'][1] == wire['beats'][1]
    assert len(transport.calls) == 2 and len(_as_adoption_refs(answer)) == 2
    assert not validate(result)
    assert wire['beats'][0]['src'] == 'e01_01'


def test_parent_patch_absorbs_child_errors_without_overlapping_paths():
    """拍整体已需返修时，不再同时请求其子条目，全部错误仍覆盖一次。"""
    from novel.stages.stage2 import chapter_patch_locate, compile_chapter_observation
    from tests.test_str06_chapter_slim import _slim_wire, EVIDENCE, SLUGS
    wire = _slim_wire()
    wire['beats'][0]['who'].append('ghost')
    wire['beats'][0]['know'][0]['basis'] = ['e01_99']
    _, errors = compile_chapter_observation(wire, evidence_ids=EVIDENCE,
        chapter=1, character_slugs=SLUGS)
    assert len(errors) == 2
    targets = chapter_patch_locate(wire, errors, evidence_ids=EVIDENCE,
        chapter=1, character_slugs=SLUGS)
    assert targets == [(('beats', 0), errors)]



def test_basis_pointer_inside_src_needs_no_quote_copy():
    """basis 只给段 id（2026-10-07 裁决）：模型不抄原文，「引文不在原文中」
    这一类返修不再存在；段不在闭集才回派该转移（窄返修）。"""
    from novel.stages.stage2 import chapter_patch_locate, compile_chapter_observation
    from tests.test_str06_chapter_slim import _slim_wire, EVIDENCE, SLUGS
    wire = _slim_wire()
    wire['beats'][0]['know'][0]['basis'] = ['e01_02']
    observation, errors = compile_chapter_observation(wire, evidence_ids=EVIDENCE,
        chapter=1, character_slugs=SLUGS)
    assert errors == []
    stored = observation.beats[0].cognition[0].basis[0]
    assert stored.segment == 'e01_02' and stored.quote == EVIDENCE['e01_02']['text']
    wire['beats'][0]['know'][0]['basis'] = ['e01_99']
    _, errors = compile_chapter_observation(wire, evidence_ids=EVIDENCE,
        chapter=1, character_slugs=SLUGS)
    assert len(errors) == 1
    assert chapter_patch_locate(wire, errors, evidence_ids=EVIDENCE,
        chapter=1, character_slugs=SLUGS) == [(('beats', 0, 'know', 0), errors)]
