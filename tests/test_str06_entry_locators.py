#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""test_str06_entry_locators —— book／names／naming 条目定位器（CS-H H4，RC-57
覆盖：全部 JSON 节点都有条目定位器）。

【验证边界】三个 owner 的 `patch` 协议实现（`book_patch_locate`／
`names_patch_locate`／`naming_patch_locate`）：错误映射到 wire 条目路径、
返回的 error 是传入的**同一批对象**（ask_json 以集合相等核对覆盖）、wire 级
问题与 `errors` 不完全一致时返回 None 退整份——不静默漏项、不新增判据
（与各自 compile 同一条管道，判据由既有测试守卫）。
"""
from __future__ import annotations

from novel.stages.stage2 import (
    book_patch_locate, compile_book_observation, compile_name_structure,
    names_patch_locate,
)
from novel.stages.stage7 import compile_name_map, naming_patch_locate
from tests.test_str06_stage2_owners import EVIDENCE, _book_wire


# ───────────────────────── book ──────────────────────────────────────────────

def test_book_patch_locate_maps_entry_errors_to_wire_paths():
    wire = _book_wire()
    wire['characters']['fu_jun']['canonical'] = '夫'
    wire['initial_state'][0]['knowledge'] = {
        'known_by': ['reader'], 'reader_knows': False}
    _obs, errors = compile_book_observation(wire, evidence=EVIDENCE)
    assert len(errors) == 2, errors
    targets = book_patch_locate(wire, errors, evidence=EVIDENCE)
    assert targets == [(('characters', 'fu_jun'), [errors[0]]),
                       (('initial_state', 0), [errors[1]])], targets
    assert targets[0][1][0] is errors[0], '返回传入的同一批错误对象'


def test_book_patch_locate_returns_none_for_wire_level_problems():
    wire = _book_wire()
    wire['characters']['fu_jun']['surfaces'][0]['source_refs'] = ['e09_09']
    _obs, errors = compile_book_observation(wire, evidence=EVIDENCE)
    assert errors
    # 同一条管道重跑定位一致时不退整份：surface 条目路径可定位。
    targets = book_patch_locate(wire, errors, evidence=EVIDENCE)
    assert targets and targets[0][0] == ('characters', 'fu_jun', 'surfaces', 0)
    # 协议字面量（wire 级）混入即退整份。
    stale = _book_wire()
    stale['protocol_version'] = 'stage2-book-v1'
    _obs, errors = compile_book_observation(stale, evidence=EVIDENCE)
    assert book_patch_locate(stale, errors, evidence=EVIDENCE) is None


# ───────────────────────── names ─────────────────────────────────────────────

def _names_inputs():
    """names 夹具：core（两人物）＋含家族构词的合法 wire 底座。"""
    from contracts.schema.source import CharactersCore, CharacterEntry
    core = CharactersCore(characters={
        'fu_jun': CharacterEntry(canonical='夫君', surfaces=[
            {'surface': '夫君', 'from_chapter': 1, 'until_chapter': None,
             'source_refs': ['e01_01']}]),
        'yun_niang': CharacterEntry(canonical='云娘', surfaces=[
            {'surface': '云娘', 'from_chapter': 1, 'until_chapter': None,
             'source_refs': ['e01_02']}]),
    }, initial_state=())
    source = '夫君走进门来。云娘坐在窗边。陆家宅子安静。'
    return core, source


def _names_wire() -> dict:
    return {'protocol_version': 'stage2-names-v3', 'resolutions': [],
            'families': {'f_lu': {'literal': '陆', 'forms': []}},
            'people': {
                'fu_jun': {'family_id': 'f_lu', 'given': None, 'aliases': [],
                           'surface_forms': {'夫君': None}},
                'yun_niang': {'family_id': None, 'given': None, 'aliases': [],
                              'surface_forms': {'云娘': None}}}}


def test_names_patch_locate_maps_family_and_person_entries():
    core, source = _names_inputs()
    wire = _names_wire()
    wire['families']['f_lu']['literal'] = '鲁'      # 未在源文出现
    wire['people']['yun_niang']['given'] = '娘'     # 名字面未在源文出现
    _structure, errors = compile_name_structure(wire, core, source)
    assert len(errors) == 2, errors
    targets = names_patch_locate(wire, errors, core, source)
    assert targets == [(('families', 'f_lu'), [errors[0]]),
                       (('people', 'yun_niang'), [errors[1]])], targets


def test_names_patch_locate_returns_none_for_coverage_problems():
    """覆盖增删（people 漏项／多出）不是替换既有条目——退整份返修。"""
    core, source = _names_inputs()
    wire = _names_wire()
    del wire['people']['yun_niang']
    _structure, errors = compile_name_structure(wire, core, source)
    assert any('缺少人物条目' in e for e in errors)
    assert names_patch_locate(wire, errors, core, source) is None


# ───────────────────────── naming ────────────────────────────────────────────

def _naming_wire(chars) -> dict:
    """对 `_chars` 夹具的合法命名 wire（全部应改名人物各一条）。"""
    from novel.stages.stage7 import naming_projection
    rename_set = naming_projection(chars)['rename_set']
    entries = []
    for ref in rename_set:
        person = chars.name_structure.people[ref]
        family = (chars.name_structure.families[person.family_id].literal
                  if person.family_id else None)
        entries.append({'char_ref': ref, 'name': {'family': family or None,
                                                  'given': person.given},
                        'aliases': {}} if family or person.given else
                       {'char_ref': ref, 'name': None,
                        'keep_reason': '纯称谓保留'})
    return {'entries': entries}


def test_naming_patch_locate_maps_entry_errors_to_entry_paths():
    from tests.test_str06_naming import _chars
    chars = _chars()
    wire = _naming_wire(chars)
    # zhao_a 源结构登记了姓（赵A），目标名称缺姓氏位——条目级问题（条目仍进
    # 分母，不产生 wire 级漏项）。
    for entry in wire['entries']:
        if entry['char_ref'] == 'zhao_a':
            entry['name'] = {'family': None, 'given': '大'}
    _map, errors = compile_name_map(wire, chars)
    assert [e for e in errors if '缺少姓氏位' in e], errors
    targets = naming_patch_locate(wire, errors, chars)
    assert targets is not None, errors
    zhao_index = next(i for i, e in enumerate(wire['entries'])
                      if e['char_ref'] == 'zhao_a')
    assert ('entries', zhao_index) in {t[0] for t in targets}
    covered = {error for target in targets for error in target[1]}
    assert covered == set(errors), '覆盖全部问题（集合相等）'


def test_naming_patch_locate_attaches_conflicts_to_both_sides():
    """冲突类错误挂到牵连的**每个**条目（改任一侧都能解：换掉其中一个新姓）。"""
    from tests.test_str06_naming import _chars
    chars = _chars()
    wire = _naming_wire(chars)
    # 皇甫珩（源姓 皇甫）改姓「陈」——与陈家（源姓 陈）合并到同一新姓，冲突。
    for entry in wire['entries']:
        if entry['char_ref'] == 'huang_fu_heng':
            entry['name'] = {'family': '陈', 'given': '珩'}
    _map, errors = compile_name_map(wire, chars)
    assert any('不得合并到同一新姓' in e for e in errors), errors
    targets = naming_patch_locate(wire, errors, chars)
    assert targets is not None, errors
    paths = {target[0] for target in targets}
    index_of = {e['char_ref']: i for i, e in enumerate(wire['entries'])}
    assert ('entries', index_of['huang_fu_heng']) in paths
    assert ('entries', index_of['chen_yan']) in paths, '冲突两侧条目都开放替换'


def test_naming_patch_locate_returns_none_for_missing_entries():
    from tests.test_str06_naming import _chars
    chars = _chars()
    wire = _naming_wire(chars)
    wire['entries'] = wire['entries'][:-1]      # 漏项＝需新增条目
    _map, errors = compile_name_map(wire, chars)
    assert any('漏项' in e for e in errors)
    assert naming_patch_locate(wire, errors, chars) is None
