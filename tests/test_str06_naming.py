#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""test_str06_naming —— F6a NameMap v6／名称结构／ApplyNames／Assembly 反例。

【验证边界】复姓不被截成首字；同姓亲属同 family_id（新姓一致）；同姓不同人
（各用各的 family_id）；独立化名显式赋值；纯称谓不进应改名集；漏项／合法
保留／名称位一致；单遍替换无链式；residual 对目标名含旧字面与 kept 映射键
不误报；同一源字面多归属多值报错；名称结构投影接管 name_distinctiveness 的
有效职责；交付重放稳定、换 NameMap 不使回执失效、无 accepted body 显式
not_ready。
"""
from __future__ import annotations

import pytest
from pydantic import ValidationError

from contracts.schema.naming import NameAssignment, NameMapV6, TargetName
from contracts.schema.name_text import NameMention
from contracts.schema.source import (
    CharactersArtifactV6, CharactersCore, NameStructure,
)
from novel.core.name_text import resolve_name_text
from novel.stages.stage2.names import (
    compile_name_structure, surname_construction_probe,
)
from novel.stages.stage4.naming import (
    apply_bound_names, compile_bound_replacements, compile_name_map,
    diff_proof, naming_projection,
)
from novel.stages.stage7.assembly import (
    BodyAccess, assemble_delivery, chapter_access, delivery_identity,
)
from tests.str06_expression_inputs import (
    sample_narration, sample_requirements, sample_design_expression,
)

SRC = ('皇甫珩执剑护院。陈砚与陈松是亲兄弟，住在陈家。赵老大与赵老二素不相识。'
       '云溪居士隐居山中。夫人安坐。柳条垂岸。')


# ───────────────────────── 名称结构夹具 ─────────────────────────

def _chars(**people_over) -> CharactersArtifactV6:
    people = {
        'huang_fu_heng': {'family_id': 'fam_hpf', 'given': '珩', 'aliases': [],
                          'surface_forms': {'皇甫珩': '{family}{given}',
                                            '皇甫公子': '{family}公子'}},
        'chen_yan': {'family_id': 'fam_chen', 'given': '砚', 'aliases': [],
                     'surface_forms': {'陈砚': '{family}{given}', '砚哥': '{given}哥'}},
        'chen_song': {'family_id': 'fam_chen', 'given': '松', 'aliases': [],
                      'surface_forms': {'陈松': '{family}{given}'}},
        'zhao_a': {'family_id': 'fam_zhao_a', 'given': '大', 'aliases': [],
                   'surface_forms': {'赵老大': '{family}老{given}'}},
        'zhao_b': {'family_id': 'fam_zhao_b', 'given': '二', 'aliases': [],
                   'surface_forms': {'赵老二': '{family}老{given}'}},
        'ju_shi': {'family_id': None, 'given': None,
                   'aliases': [{'alias_id': 'al_yx', 'literal': '云溪居士'}],
                   'surface_forms': {'云溪居士': '{alias:al_yx}'}},
        'fu_ren': {'family_id': None, 'given': None, 'aliases': [],
                   'surface_forms': {'夫人': None}},
    }
    people.update(people_over)
    return CharactersArtifactV6.model_validate({
        'characters': {
            'huang_fu_heng': {'canonical': '皇甫珩', 'surfaces': [
                _surface('皇甫珩'), _surface('皇甫公子')]},
            'chen_yan': {'canonical': '陈砚', 'surfaces': [
                _surface('陈砚'), _surface('砚哥')]},
            'chen_song': {'canonical': '陈松', 'surfaces': [_surface('陈松')]},
            'zhao_a': {'canonical': '赵老大', 'surfaces': [_surface('赵老大')]},
            'zhao_b': {'canonical': '赵老二', 'surfaces': [_surface('赵老二')]},
            'ju_shi': {'canonical': '云溪居士', 'surfaces': [_surface('云溪居士')]},
            'fu_ren': {'canonical': '夫人', 'surfaces': [_surface('夫人')]},
        },
        'initial_state': [],
        'name_structure': {
            'families': {
                'fam_hpf': {'literal': '皇甫', 'forms': []},
                'fam_chen': {'literal': '陈', 'forms': [
                    {'literal': '陈家', 'form': '{family}家', 'source_refs': ['e01_01']}]},
                'fam_zhao_a': {'literal': '赵', 'forms': []},
                'fam_zhao_b': {'literal': '赵', 'forms': []},
            },
            'people': people}})


from tests.str06_facilities import names_v6_wire


def _surface(name: str) -> dict:
    return {'surface': name, 'from_chapter': 1, 'until_chapter': None,
            'source_refs': ['e01_01']}


def _naming_wire(entries) -> dict:
    return {'entries': entries}


# ───────────────────────── 名称结构 compile（Stage2） ─────────────────────────

def test_name_structure_compile_green_and_refusals():
    core = _chars().core_view()
    structure = _chars().name_structure
    wire = names_v6_wire(structure.model_dump(mode='json'))
    compiled, errors = compile_name_structure(wire, core, SRC)
    assert compiled is not None and not errors, errors

    bad = {**wire, 'families': wire['families'], 'people': wire['people'],
           'extra': 1}
    _, errors = compile_name_structure(bad, core, SRC)
    assert any('未知字段' in e for e in errors)

    bad = {**wire, 'families': wire['families'],
           'people': {**wire['people'], 'ghost': wire['people']['fu_ren']}}
    _, errors = compile_name_structure(bad, core, SRC)
    assert any('Characters 之外' in e for e in errors)

    bad = {**wire, 'families': wire['families'],
           'people': {k: v for k, v in wire['people'].items() if k != 'fu_ren'}}
    _, errors = compile_name_structure(bad, core, SRC)
    assert any('缺少人物条目' in e for e in errors)

    # 模板渲染等式：form_overrides 的构词与 surface 对不上即拒（CS-S 1c：
    # 派生模板按构造满足等式，例外 override 的等式在构造期复检）。
    bad = {**wire, 'families': wire['families'], 'people': {
        **wire['people'],
        'chen_yan': {**wire['people']['chen_yan'],
                     'form_overrides': {'陈砚': '{given}'}}}}
    _, errors = compile_name_structure(bad, core, SRC)
    assert any('渲染不一致' in e for e in errors)

    # 纯称谓含本人姓名字面 → 防漏标拒绝（override 写 null 掩盖不了部件包含）
    bad = {**wire, 'families': wire['families'], 'people': {
        **wire['people'],
        'fu_ren': {'family_id': 'fam_chen', 'given': None, 'aliases': [],
                   'form_overrides': {'陈夫人': None}}}}
    from contracts.schema.source import CharactersCore
    core_with_surface = CharactersCore.model_validate({
        'characters': {**core.model_dump()['characters'],
                       'fu_ren': {'canonical': '夫人', 'surfaces': [
                           {'surface': '夫人', 'from_chapter': 1,
                            'until_chapter': None, 'source_refs': ['e01_01']},
                           {'surface': '陈夫人', 'from_chapter': 1,
                            'until_chapter': None, 'source_refs': ['e01_01']}]}},
        'initial_state': []})
    _, errors = compile_name_structure(bad, core_with_surface, SRC)
    assert any('防漏标' in e for e in errors), errors

    # form_overrides 指向未登记 surface → 拒（A：不得私自加称谓面）
    bad = {**wire, 'families': wire['families'], 'people': {
        **wire['people'],
        'fu_ren': {'family_id': None, 'given': None, 'aliases': [],
                   'form_overrides': {'陈夫人': None}}}}
    _, errors = compile_name_structure(bad, core, SRC)
    assert any('form_overrides 指向未登记 surface' in e for e in errors), errors

    # 字面必须在源文中出现
    bad = {**wire, 'families': {**wire['families'],
                        'fam_hpf': {'literal': '慕容', 'forms': []}},
           'people': wire['people']}
    _, errors = compile_name_structure(bad, core, SRC)
    assert any('未在源文中出现' in e for e in errors)


def test_family_redundant_id_normalized_or_mismatch_rejected():
    """F6c 真书证据：LLM 会在家族对象内冗余自报 family_id；与键一致时归一，
    不一致时报错（wire 白名单不静默）。"""
    core = _chars().core_view()
    structure = _chars().name_structure
    wire = names_v6_wire(structure.model_dump(mode='json'))
    wire['families']['fam_chen']['family_id'] = 'fam_chen'  # 冗余但一致
    compiled, errors = compile_name_structure(wire, core, SRC)
    assert compiled is not None and not errors, errors
    assert 'family_id' not in compiled.families['fam_chen'].model_dump()
    wire['families']['fam_chen']['family_id'] = 'fam_other'
    _, errors = compile_name_structure(wire, core, SRC)
    assert any('不一致' in e for e in errors)


def test_surname_probe_reports_and_never_blocks():
    core = _chars().core_view()
    structure = _chars().name_structure
    probe = surname_construction_probe(structure, SRC + '赵家村口。')
    assert any(f.literal == '赵' and not f.strict for f in probe)  # 单字姓：只列上下文
    multi = [f for f in probe if f.literal == '皇甫']
    assert multi and all(f.strict for f in multi)                  # 复姓：逐处严格
    # 探针不阻断：同一 structure 照常编译
    compiled, errors = compile_name_structure(
        names_v6_wire(structure.model_dump(mode='json')), core, SRC)
    assert compiled is not None and not errors


# ───────────────────────── 名称投影：接管 name_distinctiveness ─────────────────────────

def test_projection_takes_over_name_distinctiveness():
    chars = _chars()
    projection = chars.name_projection()
    # 复姓不被截成首字：家族字面集含「皇甫」，绝无「皇」
    assert '皇甫' in projection.family_literals
    assert '皇' not in projection.family_literals
    assert '皇' not in projection.proper_literals
    # 纯称谓不成姓：夫人不在专名面
    entry = projection.person('fu_ren')
    assert entry is not None and entry.proper_literals == ()
    assert '夫人' not in projection.proper_literals
    # display 取 core canonical
    assert projection.person('yun_niang') is None  # 本夹具无此人物
    assert projection.person('huang_fu_heng').display == '皇甫珩'


# ───────────────────────── naming_projection 与 NameMap compile ─────────────────────────

def test_projection_rename_set_excludes_pure_appellations():
    projection = naming_projection(_chars())
    assert 'fu_ren' not in projection['rename_set']
    assert 'ju_shi' in projection['rename_set']       # 有化名位：需要决策
    assert '夫人' in projection['avoided_old_literals']


def test_compile_name_map_green_with_full_computed():
    chars = _chars()
    wire = _naming_wire([
        {'char_ref': 'huang_fu_heng', 'name': {'family': '慕容', 'given': '珩'},
         'aliases': {}, 'keep_reason': None},
        {'char_ref': 'chen_yan', 'name': {'family': '沈', 'given': '砚'}},
        {'char_ref': 'chen_song', 'name': {'family': '沈', 'given': '松'}},
        {'char_ref': 'zhao_a', 'name': {'family': '柳', 'given': '大'}},
        {'char_ref': 'zhao_b', 'name': {'family': '顾', 'given': '二'}},
        {'char_ref': 'ju_shi', 'name': {'family': None, 'given': None},
         'aliases': {'al_yx': '白云先生'}, 'keep_reason': None},
    ])
    name_map, errors = compile_name_map(wire, chars)
    assert name_map is not None and not errors, errors
    full_of = {e.char_ref: e.name.full for e in name_map.entries if e.name}
    assert full_of['huang_fu_heng'] == '慕容珩'   # full 由 compiler 拼接
    assert full_of['zhao_a'] == '柳大'


def test_same_family_same_surname_but_conflict_rejected():
    chars = _chars()
    base = [
        {'char_ref': 'huang_fu_heng', 'name': {'family': '慕容', 'given': '珩'}},
        {'char_ref': 'zhao_a', 'name': {'family': '柳', 'given': '大'}},
        {'char_ref': 'zhao_b', 'name': {'family': '顾', 'given': '二'}},
        {'char_ref': 'ju_shi', 'name': None, 'keep_reason': '化名即身份'},
    ]
    green = base + [
        {'char_ref': 'chen_yan', 'name': {'family': '沈', 'given': '砚'}},
        {'char_ref': 'chen_song', 'name': {'family': '沈', 'given': '松'}}]
    name_map, errors = compile_name_map(_naming_wire(green), chars)
    assert name_map is not None and not errors, errors  # 同姓亲属：新姓一致

    red = base + [
        {'char_ref': 'chen_yan', 'name': {'family': '沈', 'given': '砚'}},
        {'char_ref': 'chen_song', 'name': {'family': '周', 'given': '松'}}]
    _, errors = compile_name_map(_naming_wire(red), chars)
    assert any('新姓不一致' in e for e in errors)

    # 不同源姓不得合并到同一新姓：陈家（源姓 陈）也改姓 柳
    red = base + [
        {'char_ref': 'chen_yan', 'name': {'family': '柳', 'given': '砚'}},
        {'char_ref': 'chen_song', 'name': {'family': '柳', 'given': '松'}}]
    _, errors = compile_name_map(_naming_wire(red), chars)
    assert any('不得合并到同一新姓' in e for e in errors)

    # 新姓与本人旧姓相同：换名就要真换
    red = base + [
        {'char_ref': 'chen_yan', 'name': {'family': '陈', 'given': '砚'}},
        {'char_ref': 'chen_song', 'name': {'family': '陈', 'given': '松'}}]
    _, errors = compile_name_map(_naming_wire(red), chars)
    assert any('与本人旧姓相同' in e for e in errors)


def test_name_positions_must_match_source_structure():
    chars = _chars()
    missing_given = _naming_wire([
        {'char_ref': 'chen_yan', 'name': {'family': '沈'}},
        {'char_ref': 'huang_fu_heng', 'name': {'family': '慕容', 'given': '珩'}},
        {'char_ref': 'chen_song', 'name': {'family': '沈', 'given': '松'}},
        {'char_ref': 'zhao_a', 'name': {'family': '柳', 'given': '大'}},
        {'char_ref': 'zhao_b', 'name': {'family': '顾', 'given': '二'}},
        {'char_ref': 'ju_shi', 'name': None, 'keep_reason': '隐士'}])
    _, errors = compile_name_map(missing_given, chars)
    assert any('缺少名字位' in e for e in errors)

    family_for_none = _naming_wire([
        {'char_ref': 'chen_yan', 'name': {'family': '沈', 'given': '砚'}},
        {'char_ref': 'huang_fu_heng', 'name': {'family': '慕容', 'given': '珩'}},
        {'char_ref': 'chen_song', 'name': {'family': '沈', 'given': '松'}},
        {'char_ref': 'zhao_a', 'name': {'family': '柳', 'given': '大'}},
        {'char_ref': 'zhao_b', 'name': {'family': '顾', 'given': '二'}},
        {'char_ref': 'ju_shi', 'name': {'family': '沈', 'given': None},
         'aliases': {'al_yx': '白云先生'}, 'keep_reason': None}])
    _, errors = compile_name_map(family_for_none, chars)
    assert any('源结构无姓氏位' in e for e in errors)


def test_missing_and_extra_entries_rejected():
    chars = _chars()
    complete = [
        {'char_ref': 'huang_fu_heng', 'name': {'family': '慕容', 'given': '珩'}},
        {'char_ref': 'chen_yan', 'name': {'family': '沈', 'given': '砚'}},
        {'char_ref': 'chen_song', 'name': {'family': '沈', 'given': '松'}},
        {'char_ref': 'zhao_a', 'name': {'family': '柳', 'given': '大'}},
        {'char_ref': 'zhao_b', 'name': {'family': '顾', 'given': '二'}},
        {'char_ref': 'ju_shi', 'name': None, 'keep_reason': '化名即身份'}]
    _, errors = compile_name_map(
        _naming_wire(complete[:-1]), chars)
    assert any('漏项' in e for e in errors)
    _, errors = compile_name_map(
        _naming_wire(complete + [{'char_ref': 'fu_ren', 'name': {'family': '钱'}}]), chars)
    assert any('不在应改名集合' in e for e in errors)
    _, errors = compile_name_map(
        _naming_wire(complete + [{'char_ref': 'ghost', 'name': {'family': '钱'}}]), chars)
    assert any('未知 char_ref' in e for e in errors)


def test_keep_requires_reason_and_alias_positions_registered():
    with pytest.raises(ValidationError, match='恰好给出一个'):
        NameAssignment(char_ref='p', name=None, keep_reason=None)
    with pytest.raises(ValidationError, match='恰好给出一个'):
        NameAssignment(char_ref='p', name=TargetName(family='沈'),
                       keep_reason='理由')
    chars = _chars()
    base = [
        {'char_ref': 'huang_fu_heng', 'name': {'family': '慕容', 'given': '珩'}},
        {'char_ref': 'chen_yan', 'name': {'family': '沈', 'given': '砚'}},
        {'char_ref': 'chen_song', 'name': {'family': '沈', 'given': '松'}},
        {'char_ref': 'zhao_a', 'name': {'family': '柳', 'given': '大'}},
        {'char_ref': 'zhao_b', 'name': {'family': '顾', 'given': '二'}}]
    # 保留必须带理由（空白理由同拒）
    _, errors = compile_name_map(
        _naming_wire(base + [{'char_ref': 'ju_shi', 'name': None, 'keep_reason': '   '}]),
        chars)
    assert any('保留必须带理由' in e for e in errors)
    # 未登记的化名位拒绝
    _, errors = compile_name_map(
        _naming_wire(base + [{'char_ref': 'ju_shi',
                              'name': {'family': None, 'given': None},
                              'aliases': {'al_bad': 'x'}, 'keep_reason': None}]), chars)
    assert any('化名位未登记' in e for e in errors)
    # 空名称位且无化名位：不是合法改名
    _, errors = compile_name_map(
        _naming_wire(base + [{'char_ref': 'ju_shi',
                              'name': {'family': None, 'given': None},
                              'aliases': {}, 'keep_reason': None}]), chars)
    assert any('目标名称全空' in e for e in errors)


# ───────────────────────── 替换表与单遍应用 ─────────────────────────

def _green_map(chars):
    wire = _naming_wire([
        {'char_ref': 'huang_fu_heng', 'name': {'family': '慕容', 'given': '珩'}},
        {'char_ref': 'chen_yan', 'name': {'family': '沈', 'given': '砚'}},
        {'char_ref': 'chen_song', 'name': {'family': '沈', 'given': '松'}},
        {'char_ref': 'zhao_a', 'name': {'family': '柳', 'given': '大'}},
        {'char_ref': 'zhao_b', 'name': {'family': '顾', 'given': '二'}},
        {'char_ref': 'ju_shi', 'name': {'family': None, 'given': None},
         'aliases': {'al_yx': '白云先生'}, 'keep_reason': None}])
    name_map, errors = compile_name_map(wire, chars)
    assert name_map is not None and not errors, errors
    return name_map


def test_surface_replacements_long_name_and_family_forms():
    chars = _chars()
    name_map = _green_map(chars)
    table, errors = compile_bound_replacements(chars, name_map)
    assert not errors, errors
    # 复姓完整替换，不被截成首字
    assert table['huang_fu_heng']['皇甫珩'] == '慕容珩'
    assert table['huang_fu_heng']['皇甫公子'] == '慕容公子'
    # 家族级称谓按同 family_id 新姓一致展开
    assert table['chen_yan']['陈家'] == '沈家'
    # 独立化名显式赋值，不由新名推导
    assert table['ju_shi']['云溪居士'] == '白云先生'
    resolved = resolve_name_text('[[name:huang_fu_heng|皇甫珩]]与[[name:chen_yan|陈砚]]在'
                                 '[[name:chen_yan|陈家]]。[[name:ju_shi|云溪居士]]旁观。')
    renamed, errors = apply_bound_names(resolved.text, resolved.mentions, table)
    assert renamed == '慕容珩与沈砚在沈家。白云先生旁观。'
    assert not errors
    assert [c.surface for c in resolved.mentions] == ['皇甫珩', '陈砚', '陈家', '云溪居士']


def test_released_surfaces_close_over_the_replacement_table():
    """贯穿（A3）：Design 放行面闭合在登记面内 ⇒ 每个获准输出称谓都换成新名。

    名称结构 → Design 放行面 → NameMap → 替换表：把放行面写进一段正文后，
    带名称谓零残留、逐一对上新名，且不出现旧姓配新名（陈砚 不得残存）。
    """
    from novel.stages.stage4.design import assemble_body_design

    chars = _chars()
    name_map = _green_map(chars)
    table, errors = compile_bound_replacements(chars, name_map)
    assert not errors, errors
    surface_owner = {s.surface: cid for cid, entry in chars.characters.items()
                     for s in entry.surfaces}
    released = ['皇甫珩', '皇甫公子', '陈砚', '陈松', '赵老大', '云溪居士', '夫人']
    assert all(surface in surface_owner for surface in released), '放行面必须已登记'
    # 生产端同一道闸：放行面取自登记面才编译得过（registered_surfaces）
    registered = {cid: {s.surface for s in entry.surfaces}
                  for cid, entry in chars.characters.items()}
    design_wire = {**sample_design_expression(('huang_fu_heng',)),
        'protocol_version': 'stage4-design-v5',
        'book_style': '平实克制的第三人称叙事。',
        'initial_state': [],
        'scenes': [{
            'state_reads': [],
            'driver': {'focal_actor': 'huang_fu_heng', 'immediate_want': '见面',
                       'opposition': '门第', 'turn': '通名'},
            'realization_brief': '重心在称谓初现；从登门展开，在互通姓名处收束。',
            'beats': [{'event': '诸人相见', 'participants': ['huang_fu_heng'],
                       'delivery_hint': '', 'target_delta': [],
                       'surface_introductions': [
                           {'char_id': surface_owner[s], 'surfaces': [s]} for s in released]}],
            'delivery': {'pov': 'huang_fu_heng', 'voice': '平实', 'avoid': []}}],
        'chapters': [{'purpose': '人物逐一登场', 'scenes': 1}],
        'scene_weights': [1],
    }
    body, errors = assemble_body_design(
        design_wire, known_char_ids=set(chars.characters),
        registered_surfaces=registered, expression_requirements=sample_requirements(), source_narration=sample_narration())
    assert body is not None and not errors, errors

    resolved = resolve_name_text('、'.join(f'[[name:{surface_owner[s]}|{s}]]' for s in released)
                                 + '同席。', surfaces=registered)
    assert not resolved.errors
    renamed, errors = apply_bound_names(resolved.text, resolved.mentions, table)
    assert not errors, '获准输出的称谓必须全部换成新名'
    for old, new in (('皇甫珩', '慕容珩'), ('皇甫公子', '慕容公子'),
                     ('陈砚', '沈砚'), ('陈松', '沈松'), ('赵老大', '柳老大'),
                     ('云溪居士', '白云先生')):
        assert table[surface_owner[old]][old] == new and new in renamed
        assert old not in renamed, f'{old} 未换成本名'
    assert '夫人' in renamed, '纯称谓不进替换表，原样保留'
    # 旧姓配新名不得出现（家族新姓一致展开的并集之外）
    for mixed in ('陈婉', '柳砚', '沈大'):
        assert mixed not in renamed


def test_same_surface_without_person_decision_is_refused():
    mentions = (NameMention(start=0, end=2, char_id='a', surface='大郎'),)
    assert apply_bound_names('大郎回家。', mentions, {'b': {'大郎': '顾大'}})[1]
    assert apply_bound_names('大郎回家。', mentions, {'a': {'大郎': '柳大'}}) == ('柳大回家。', [])


def test_replacement_value_containing_another_source_name_is_applied_once():
    resolved = resolve_name_text('[[name:a|砚哥]]与[[name:b|陈砚]]同行。')
    renamed, errors = apply_bound_names(resolved.text, resolved.mentions,
                                      {'a': {'砚哥': '新陈砚'}, 'b': {'陈砚': '沈砚'}})
    assert not errors and renamed == '新陈砚与沈砚同行。'


def test_kept_mention_and_target_containing_old_literal_do_not_affect_ordinary_words():
    resolved = resolve_name_text('[[name:a|白]]与[[name:b|白家公子]]说[[text|对白]]。')
    table = {'a': {'白': '赵白'}, 'b': {'白家公子': '白家公子'}}
    renamed, errors = apply_bound_names(resolved.text, resolved.mentions, table)
    assert not errors and renamed == '赵白与白家公子说对白。'
    wrong = resolved.mentions[0].model_copy(update={'surface': '赵'})
    assert apply_bound_names(resolved.text, (wrong,), table)[1]


def test_diff_proof_green_and_tamper_red():
    resolved = resolve_name_text('[[name:p|裴书远]]对[[name:l|陆大哥]]说。')
    table = {'p': {'裴书远': '林书言'}, 'l': {'陆大哥': '顾大哥'}}
    renamed = '林书言对顾大哥说。'
    assert diff_proof(resolved.text, renamed, table, resolved.mentions) == []
    assert diff_proof(resolved.text, renamed + '多一个字', table, resolved.mentions)


def test_body_access_states_separated():
    access = chapter_access(['s01', 's02', 's03', 's04'], {
        's01': {'status': 'accepted'}, 's03': {'status': 'rejected'},
        's04': {'status': 'something_else'}})
    assert access.accepted == ('s01',)
    assert access.missing == ('s02',)
    assert access.rejected == ('s03',)
    assert access.unknown == ('s04',)
    assert not access.ready


def test_chapter_access_separates_stale_from_missing_and_rejected():
    """I-42 五态：有效／缺失／拒绝／未知／失效分开（R-10.3 绑定分类）。

    失效（stale）＝状态 accepted 但与当前章包摘要或正文 hash 绑定不成立——
    回执在盘上、须重做，不是缺失。分类与 `verify_chapter_receipts` 共用同一
    逐章绑定实现；不给核对材料时只按 status 分类（stale 恒空）。
    """
    import hashlib

    def sha(text: str) -> str:
        return hashlib.sha256(text.encode('utf-8')).hexdigest()

    from novel.stages.common import ChapterIdentities
    digests = {1: '1' * 64, 2: '2' * 64, 3: '3' * 64, 4: '4' * 64}
    identities = {n: ChapterIdentities(writer_view_sha256=d, validation_sha256='9' * 64,
                                       machine_view_sha256='e' * 64)
                  for n, d in digests.items()}
    prose = {1: '第一章。', 2: '第二章。', 3: '第三章。', 4: '第四章。'}

    def receipt(writer_view, ordinal):
        from contracts.schema.receipt import (
            BODY_VALIDATOR_VERSION, BODY_MECHANICAL_CHECKS, RENDERER_VERSION, WRITER_PROTOCOL_VERSION,
        )
        return {'status': 'accepted', 'chapter_ordinal': ordinal, 'writer_view_sha256': writer_view,
                'validator_version': BODY_VALIDATOR_VERSION, 'mechanical_checks': BODY_MECHANICAL_CHECKS,
                'protocol_version': WRITER_PROTOCOL_VERSION, 'renderer_version': RENDERER_VERSION,
                'validation_sha256': '9' * 64, 'machine_view_sha256': 'e' * 64,
                'name_mentions': (), 'non_name_spans': (),
                'prose_sha256': sha(prose[ordinal])}

    receipts = {
        1: receipt(digests[1], 1),
        # 写手视图换版：失效，不是缺失
        2: receipt('f' * 64, 2),
        # 正文脱钩：同样失效
        3: receipt(digests[3], 3) | {'prose_sha256': sha('被改过的第三章')},
        4: receipt(digests[4], 4),
        5: {'status': 'rejected', 'writer_view_sha256': digests[1],
            'validation_sha256': '9' * 64, 'machine_view_sha256': 'e' * 64,
            'prose_sha256': sha(prose[1])},
        6: {'notes': '回执存在但状态不可解'},
    }
    access = chapter_access(range(1, 8), receipts,
                            identities_by_ordinal=identities, chapter_prose=prose)
    assert access.accepted == (1, 4)
    assert access.stale == (2, 3)
    assert access.rejected == (5,)
    assert access.unknown == (6,)
    assert access.missing == (7,)
    assert not access.ready
    # 缺省（不给核对材料）只按 status 分类：接受态不核对绑定，stale 恒空
    status_only = chapter_access(range(1, 4), receipts)
    assert status_only.accepted == (1, 2, 3)
    assert status_only.stale == ()


def _hook_receipt_dict(prose: str, context_sha: str, status: str = 'accepted') -> dict:
    import hashlib
    from contracts.schema.receipt import HOOK_VALIDATOR_VERSION, HOOK_MECHANICAL_CHECKS
    return {'hook_context_sha256': context_sha, 'prose_sha256':
            hashlib.sha256(prose.encode('utf-8')).hexdigest(),
            'request_identity': 'r1', 'validator_version': HOOK_VALIDATOR_VERSION, 'status': status,
            'mechanical_checks': HOOK_MECHANICAL_CHECKS, 'semantic': 'unverified',
            'name_mentions': _delivery_mentions(prose), 'non_name_spans': ()}


def _delivery_mentions(prose):
    import re
    return [NameMention(start=match.start(), end=match.end(), char_id='pei',
                        surface='裴书远').model_dump()
            for match in re.finditer('裴书远', prose)]


def _body_receipt_dict(prose):
    import hashlib
    from contracts.schema.receipt import (
        BODY_VALIDATOR_VERSION, BODY_MECHANICAL_CHECKS, RENDERER_VERSION, WRITER_PROTOCOL_VERSION,
    )
    return {'status': 'accepted', 'chapter_ordinal': 1, 'prose_sha256': hashlib.sha256(prose.encode()).hexdigest(),
            'writer_view_sha256': 'a' * 64, 'validation_sha256': 'b' * 64,
            'validator_version': BODY_VALIDATOR_VERSION, 'mechanical_checks': BODY_MECHANICAL_CHECKS,
            'protocol_version': WRITER_PROTOCOL_VERSION, 'renderer_version': RENDERER_VERSION,
            'name_mentions': _delivery_mentions(prose), 'non_name_spans': ()}


def _delivery_identities():
    from novel.stages.common import ChapterIdentities
    return {1: ChapterIdentities(writer_view_sha256='a' * 64, validation_sha256='b' * 64,
                                 machine_view_sha256='c' * 64)}


def _ready_delivery():
    table = {'pei': {'裴书远': '林书言'}}
    hook, chapter1 = '楔子：裴书远夜行。', '正文：裴书远到家。'
    context_sha = 'c' * 64
    receipt = _hook_receipt_dict(hook, context_sha)
    access = chapter_access([1], {1: {'status': 'accepted'}})
    outcome = assemble_delivery(chapter_prose={1: chapter1}, body=access, hook_prose=hook,
                                hook_receipt=receipt, hook_context_sha256=context_sha,
                                replacements=table, kept_literals=(),
                                chapter_receipts={1: _body_receipt_dict(chapter1)}, identities_by_ordinal=_delivery_identities())
    return outcome, table, hook, chapter1, context_sha, receipt, access


def test_assembly_replay_is_byte_stable_and_identity_separated():
    outcome, table, hook, chapter1, context_sha, receipt, access = _ready_delivery()
    assert outcome.status == 'ready'
    # 章标记由系统添加：引子在前不加标题，每章一行「第N章」加正文
    assert outcome.delivery_bytes.decode('utf-8') == (
        '楔子：林书言夜行。\n\n第1章\n正文：林书言到家。')
    again = assemble_delivery(chapter_prose={1: chapter1}, body=access, hook_prose=hook,
                              hook_receipt=receipt, hook_context_sha256=context_sha,
                              replacements=table, kept_literals=(),
                              chapter_receipts={1: _body_receipt_dict(chapter1)}, identities_by_ordinal=_delivery_identities())
    assert again.delivery_bytes == outcome.delivery_bytes
    assert again.delivery_identity == outcome.delivery_identity
    # 换 NameMap：交付身份变，但 Stage5/Hook 回执的输入原样（回执字段无交付材料）
    other = {'pei': {'裴书远': '周书言'}}
    changed = assemble_delivery(chapter_prose={1: chapter1}, body=access, hook_prose=hook,
                                hook_receipt=receipt, hook_context_sha256=context_sha,
                                replacements=other, kept_literals=(),
                                chapter_receipts={1: _body_receipt_dict(chapter1)}, identities_by_ordinal=_delivery_identities())
    assert changed.delivery_identity != outcome.delivery_identity
    assert set(receipt) & {'name_map', 'delivery_identity'} == set()


def test_delivery_identity_is_sensitive_to_chapter_split():
    """分章进身份：拼接相同、分章不同 ⇒ 交付身份不同（A4）。"""
    import hashlib

    def digests(chapters):
        return [f'{o}:{hashlib.sha256(t.encode("utf-8")).hexdigest()}'
                for o, t in sorted(chapters.items())]

    joined = '上半。下半。'
    one = {1: joined}
    two = {1: '上半。', 2: '下半。'}
    hook_sha = hashlib.sha256(b'hook').hexdigest()
    assert delivery_identity(
        chapter_digests=digests(one), hook_prose_sha256=hook_sha,
        replacements={}, kept_literals=()) != delivery_identity(
        chapter_digests=digests(two), hook_prose_sha256=hook_sha,
        replacements={}, kept_literals=())
    # 章序也进身份：同两章文本、调换次序即不同交付
    swapped = {1: '下半。', 2: '上半。'}
    assert delivery_identity(
        chapter_digests=digests(two), hook_prose_sha256=hook_sha,
        replacements={}, kept_literals=()) != delivery_identity(
        chapter_digests=digests(swapped), hook_prose_sha256=hook_sha,
        replacements={}, kept_literals=())


def test_assembly_not_ready_without_accepted_body():
    table = {'裴书远': '林书言'}
    hook, context_sha = '楔子。', 'c' * 64
    receipt = _hook_receipt_dict(hook, context_sha)
    outcome = assemble_delivery(chapter_prose=None, body=chapter_access([], {}),
                                hook_prose=hook, hook_receipt=receipt,
                                hook_context_sha256=context_sha,
                                replacements=table, kept_literals=())
    assert outcome.status == 'not_ready'
    assert outcome.reason == 'body_not_implemented'
    assert outcome.delivery_bytes is None and outcome.delivery_identity is None
    # 有章文本但回执不齐：同样 not_ready，且不制造成功状态
    partial = assemble_delivery(chapter_prose={1: '正文。'}, body=chapter_access([], {}),
                                hook_prose=hook, hook_receipt=receipt,
                                hook_context_sha256=context_sha,
                                replacements=table, kept_literals=())
    assert partial.status == 'not_ready' and partial.reason.startswith('missing=')
    # 章文本与已接受章不一致：显式不匹配，不静默按手头的字节组装
    mismatch = assemble_delivery(chapter_prose={1: '正文。', 2: '第二章。'},
                                 body=chapter_access([1], {1: {'status': 'accepted'}}),
                                 hook_prose=hook, hook_receipt=receipt,
                                 hook_context_sha256=context_sha,
                                 replacements=table, kept_literals=())
    assert mismatch.status == 'not_ready'
    assert mismatch.reason.startswith('body_chapter_mismatch')


def test_assembly_hook_receipt_binding_enforced():
    outcome, *_ = _ready_delivery()
    table = {'裴书远': '林书言'}
    hook, chapter1, context_sha, receipt, access = (
        '楔子：裴书远夜行。', '正文：裴书远到家。', 'c' * 64,
        _hook_receipt_dict('楔子：裴书远夜行。', 'c' * 64),
        chapter_access([1], {1: {'status': 'accepted'}}))
    wrong_ctx = assemble_delivery(chapter_prose={1: chapter1}, body=access, hook_prose=hook,
                                  hook_receipt=receipt, hook_context_sha256='d' * 64,
                                  replacements=table, kept_literals=())
    assert wrong_ctx.status == 'not_ready' and wrong_ctx.reason == 'hook_context_mismatch'
    tampered_receipt = _hook_receipt_dict('别的成文', context_sha)
    wrong_prose = assemble_delivery(chapter_prose={1: chapter1}, body=access,
                                    hook_prose=hook,
                                    hook_receipt=tampered_receipt,
                                    hook_context_sha256=context_sha,
                                    replacements=table, kept_literals=())
    assert wrong_prose.status == 'not_ready' and wrong_prose.reason == 'hook_prose_mismatch'
    rejected = assemble_delivery(
        chapter_prose={1: chapter1}, body=access, hook_prose=hook,
        hook_receipt=_hook_receipt_dict(hook, context_sha, status='rejected'),
        hook_context_sha256=context_sha, replacements=table, kept_literals=())
    assert rejected.status == 'not_ready' and rejected.reason == 'hook_rejected'


def test_bound_names_distinguish_shared_surfaces_and_leave_ordinary_words_untouched():
    """相同短名可属于不同人；普通词命中旧名部件不参与改名。"""
    prose = '对白说完，白向白点头，若有所思。'
    first, second = prose.index('白向'), prose.index('白点')
    mentions = (
        NameMention(start=first, end=first + 1, char_id='a', surface='白'),
        NameMention(start=second, end=second + 1, char_id='b', surface='白'),
    )
    renamed, errors = apply_bound_names(prose, mentions, {
        'a': {'白': '赵'}, 'b': {'白': '钱'},
    })
    assert errors == [] and renamed == '对白说完，赵向钱点头，若有所思。'
    assert apply_bound_names(prose, mentions[::-1], {'a': {'白': '赵'}, 'b': {'白': '钱'}})[1]
    assert apply_bound_names(prose, mentions, {'a': {'白': '赵'}})[1]


def test_bound_replacements_keep_person_identity_instead_of_rejecting_shared_surface():
    chars = _chars()
    first = chars.name_structure.people['zhao_a']
    second = chars.name_structure.people['zhao_b']
    structure = chars.name_structure.model_copy(update={'people': {
        **chars.name_structure.people,
        'zhao_a': first.model_copy(update={'surface_forms': {**first.surface_forms, '赵先生': '{family}先生'}}),
        'zhao_b': second.model_copy(update={'surface_forms': {**second.surface_forms, '赵先生': '{family}先生'}}),
    }})
    chars = chars.model_copy(update={'name_structure': structure})
    mapping = NameMapV6(entries=(
        NameAssignment(char_ref='zhao_a', name=TargetName(family='林', given='青'), aliases={}),
        NameAssignment(char_ref='zhao_b', name=TargetName(family='周', given='竹'), aliases={}),
        *(NameAssignment(char_ref=char_id, keep_reason='本例明确保留')
          for char_id in ('huang_fu_heng', 'chen_yan', 'chen_song', 'ju_shi')),
    ))
    replacements, errors = compile_bound_replacements(chars, mapping)
    assert errors == []
    assert replacements['zhao_a']['赵先生'] == '林先生'
    assert replacements['zhao_b']['赵先生'] == '周先生'


def test_assembly_requires_current_name_receipt_and_binds_mention_identity():
    _, table, hook, chapter, context_sha, receipt, access = _ready_delivery()
    old = {key: value for key, value in receipt.items()
           if key not in {'name_mentions', 'non_name_spans'}}
    refused = assemble_delivery(
        chapter_prose={1: chapter}, body=access, hook_prose=hook,
        hook_receipt=old, hook_context_sha256=context_sha, replacements=table,
        chapter_receipts={1: _body_receipt_dict(chapter)}, identities_by_ordinal=_delivery_identities())
    assert refused.status == 'not_ready' and '缺人物提及' in refused.reason
    refused = assemble_delivery(
        chapter_prose={1: chapter}, body=access, hook_prose=hook,
        hook_receipt=receipt, hook_context_sha256=context_sha, replacements=table)
    assert refused.status == 'not_ready' and '缺章级人物提及回执' in refused.reason

    same_target = {**table, 'other': {'裴书远': '林书言'}}
    first = assemble_delivery(
        chapter_prose={1: chapter}, body=access, hook_prose=hook,
        hook_receipt=receipt, hook_context_sha256=context_sha, replacements=same_target,
        chapter_receipts={1: _body_receipt_dict(chapter)}, identities_by_ordinal=_delivery_identities())
    changed = {**receipt, 'name_mentions': [dict(row, char_id='other')
                                          for row in receipt['name_mentions']]}
    second = assemble_delivery(
        chapter_prose={1: chapter}, body=access, hook_prose=hook,
        hook_receipt=changed, hook_context_sha256=context_sha, replacements=same_target,
        chapter_receipts={1: _body_receipt_dict(chapter)}, identities_by_ordinal=_delivery_identities())
    assert first.delivery_bytes == second.delivery_bytes
    assert first.delivery_identity != second.delivery_identity


@pytest.mark.parametrize('field,value', [
    ('validator_version', 'old-validator'), ('mechanical_checks', None),
    ('mechanical_checks', ('non_empty',)), ('name_mentions', ''),
    ('non_name_spans', {}), ('name_mentions', None),
])
def test_assembly_rejects_old_hook_checks_or_non_array_identity_records(field, value):
    _, table, hook, chapter, context_sha, receipt, access = _ready_delivery()
    receipt = {**receipt, field: value}
    outcome = assemble_delivery(
        chapter_prose={1: chapter}, body=access, hook_prose=hook,
        hook_receipt=receipt, hook_context_sha256=context_sha, replacements=table,
        chapter_receipts={1: _body_receipt_dict(chapter)}, identities_by_ordinal=_delivery_identities())
    assert outcome.status == 'not_ready'
    assert outcome.reason.startswith(('hook_validator_mismatch', 'hook_name_bindings:'))


def test_assembly_does_not_accept_status_only_body_access_as_binding_proof():
    _, table, hook, chapter, context_sha, receipt, access = _ready_delivery()
    assert access.ready, '状态只读分类可以就绪，但不能替代当前输入证据'
    outcome = assemble_delivery(
        chapter_prose={1: chapter}, body=access, hook_prose=hook,
        hook_receipt=receipt, hook_context_sha256=context_sha, replacements=table,
        chapter_receipts={1: _body_receipt_dict(chapter)})
    assert outcome.status == 'not_ready' and '缺当前章包身份' in outcome.reason
    for inputs in ({'identities_by_ordinal': _delivery_identities()}, {'chapter_prose': {1: chapter}}):
        with pytest.raises(ValueError, match='同时提供'):
            chapter_access([1], {1: _body_receipt_dict(chapter)}, **inputs)


@pytest.mark.parametrize('field,value', [
    ('validator_version', 'old-validator'), ('mechanical_checks', None),
    ('protocol_version', 'old-protocol'), ('renderer_version', 'old-renderer'),
    ('writer_view_sha256', 'f' * 64), ('validation_sha256', 'f' * 64),
    ('name_mentions', ''), ('non_name_spans', None),
    ('chapter_ordinal', 2), ('chapter_ordinal', True), ('chapter_ordinal', None),
])
def test_assembly_rechecks_current_body_identity_even_if_caller_claims_ready(field, value):
    _, table, hook, chapter, context_sha, receipt, access = _ready_delivery()
    body_receipt = {**_body_receipt_dict(chapter), field: value}
    outcome = assemble_delivery(
        chapter_prose={1: chapter}, body=access, hook_prose=hook,
        hook_receipt=receipt, hook_context_sha256=context_sha, replacements=table,
        chapter_receipts={1: body_receipt}, identities_by_ordinal=_delivery_identities())
    assert outcome.status == 'not_ready' and outcome.reason.startswith('body_name_bindings:')


@pytest.mark.parametrize('side', ['hook', 'body'])
def test_assembly_refuses_protocol_wire_in_formal_prose(side):
    _, table, hook, chapter, context_sha, receipt, access = _ready_delivery()
    if side == 'hook':
        hook = '[[text|楔子]]'
        receipt = _hook_receipt_dict(hook, context_sha)
    else:
        chapter = '[[text|正文]]'
    outcome = assemble_delivery(
        chapter_prose={1: chapter}, body=access, hook_prose=hook,
        hook_receipt=receipt, hook_context_sha256=context_sha, replacements=table,
        chapter_receipts={1: _body_receipt_dict(chapter)}, identities_by_ordinal=_delivery_identities())
    assert outcome.status == 'not_ready' and '仍含身份协议定界符' in outcome.reason


def test_delivery_identity_binds_non_person_declarations_even_with_identical_prose():
    first, table, hook, chapter, context_sha, receipt, access = _ready_delivery()
    changed = {**receipt, 'non_name_spans': [{'start': 0, 'end': 2}]}
    second = assemble_delivery(
        chapter_prose={1: chapter}, body=access, hook_prose=hook,
        hook_receipt=changed, hook_context_sha256=context_sha, replacements=table,
        chapter_receipts={1: _body_receipt_dict(chapter)}, identities_by_ordinal=_delivery_identities())
    assert first.delivery_bytes == second.delivery_bytes
    assert first.delivery_identity != second.delivery_identity


def test_delivery_identity_pure_function():
    digests = ['1:' + 'a' * 64]
    assert delivery_identity(chapter_digests=digests, hook_prose_sha256='b' * 64,
                             replacements={'x': 'y'}, kept_literals=('k',)) == \
        delivery_identity(chapter_digests=digests, hook_prose_sha256='b' * 64,
                          replacements={'x': 'y'}, kept_literals=('k',))
    assert delivery_identity(chapter_digests=digests, hook_prose_sha256='b' * 64,
                             replacements={'x': 'y'}, kept_literals=()) != \
        delivery_identity(chapter_digests=digests, hook_prose_sha256='b' * 64,
                          replacements={'x': 'y'}, kept_literals=('k',))
    # 组装格式版本进身份：换版即换身份（旧交付不会与新交付混为一份）
    assert delivery_identity(chapter_digests=digests, hook_prose_sha256='b' * 64,
                             replacements={}, kept_literals=()) != \
        delivery_identity(chapter_digests=['2:' + 'a' * 64],
                          hook_prose_sha256='b' * 64,
                          replacements={}, kept_literals=())
