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
    with pytest.raises(KeyError):
        _merge_patches({'beats': []}, [(('nope',), {})])


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
    wire = {'protocol_version': 'stage2-chapters-v2', 'beats': [
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


@pytest.mark.parametrize('wire_version', ['stage2-chapters-v2', 'stage2-chapters-v3'])
@pytest.mark.parametrize('wrong_segment', ['e01_01', 'e01_02'])
def test_quote_repair_can_update_parent_refs_in_one_round(tmp_path, wrong_segment, wire_version):
    """引用错位与拍级清单耦合，不能只让模型修改 know 而禁止修 src。"""
    from copy import deepcopy
    from novel.stages.stage2 import chapter_patch_locate, compile_chapter_observation
    evidence = {
        'e01_01': {'chapter': 1, 'text': '芸娘走进房内。'},
        'e01_02': {'chapter': 1, 'text': '夫君坦白自己不能生育。'},
    }
    wire = {'protocol_version': 'stage2-chapters-v3', 'beats': [{
        'ev': '夫君向芸娘坦白不能生育。', 'who': ['fu_jun', 'yun_niang'],
        'src': 'e01_01', 'new_scene': True,
        'know': [{'who': 'yun_niang', 'how': 'learns', 'what': '夫君不能生育',
                  'basis': [{'segment': wrong_segment, 'quote': '夫君坦白自己不能生育。'}]}],
    }, {'ev': '芸娘进房。', 'who': ['yun_niang'], 'src': 'e01_01'}]}
    if wire_version == 'stage2-chapters-v2':
        from novel.stages.stage2.slim import expand_chapter_wire
        wire, expansion_errors = expand_chapter_wire(wire, chapter=1)
        assert not expansion_errors
    refs_key = 'src' if wire_version.endswith('v3') else 'source_refs'
    cognition_key = 'know' if wire_version.endswith('v3') else 'cognition'
    def validate(value):
        return compile_chapter_observation(value, evidence_ids=evidence,
            chapter=1, character_slugs=('fu_jun', 'yun_niang'))[1]
    def locate(value, errors):
        return chapter_patch_locate(value, errors, evidence_ids=evidence,
            chapter=1, character_slugs=('fu_jun', 'yun_niang'))
    errors = validate(wire)
    assert errors and locate(wire, errors) == [(('beats', 0), errors)]
    fixed = deepcopy(wire['beats'][0])
    fixed[refs_key] = 'e01_01..e01_02' if wire_version.endswith('v3') else ['e01_01', 'e01_02']
    fixed[cognition_key][0]['basis'][0]['segment'] = 'e01_02'
    transport = _ScriptedTransport([json.dumps(wire, ensure_ascii=False),
        json.dumps({'patches': [{'path': 'beats[0]', 'value': fixed}]}, ensure_ascii=False)])
    _base, _repo, session = _json_session(tmp_path, transport)
    result, answer = ask_json(session, 'ch01', _request(), validate, patch=locate)
    assert result['beats'][0] == fixed and result['beats'][1] == wire['beats'][1]
    assert len(transport.calls) == 2 and len(_as_adoption_refs(answer)) == 2
    assert not validate(result)
    assert wire['beats'][0][refs_key] == ('e01_01' if wire_version.endswith('v3') else ['e01_01'])


def test_parent_patch_absorbs_child_errors_without_overlapping_paths():
    """拍整体已需返修时，不再同时请求其子条目，全部错误仍覆盖一次。"""
    from novel.stages.stage2 import chapter_patch_locate, compile_chapter_observation
    from tests.test_str06_chapter_slim import _slim_wire, EVIDENCE, SLUGS
    wire = _slim_wire()
    wire['beats'][0]['who'].append('ghost')
    wire['beats'][0]['know'][0]['basis'][0]['quote'] = '正文没有这句话'
    _, errors = compile_chapter_observation(wire, evidence_ids=EVIDENCE,
        chapter=1, character_slugs=SLUGS)
    assert len(errors) == 2
    targets = chapter_patch_locate(wire, errors, evidence_ids=EVIDENCE,
        chapter=1, character_slugs=SLUGS)
    assert targets == [(('beats', 0), errors)]



def test_quote_repair_stays_on_transfer_when_home_already_in_refs():
    """正确引文段已在该拍清单中时，只需改转移，继续保留窄返修。"""
    from novel.stages.stage2 import chapter_patch_locate, compile_chapter_observation
    from tests.test_str06_chapter_slim import _slim_wire, EVIDENCE, SLUGS
    wire = _slim_wire()
    wire['beats'][0]['know'][0]['basis'] = [
        {'segment': 'e01_01', 'quote': '夫君说求娶是为报恩。'}]
    _, errors = compile_chapter_observation(wire, evidence_ids=EVIDENCE,
        chapter=1, character_slugs=SLUGS)
    assert len(errors) == 1
    assert chapter_patch_locate(wire, errors, evidence_ids=EVIDENCE,
        chapter=1, character_slugs=SLUGS) == [(('beats', 0, 'know', 0), errors)]
