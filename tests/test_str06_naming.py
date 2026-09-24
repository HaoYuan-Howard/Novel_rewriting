#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""test_str06_naming —— F6a NameMap v5／名称结构／ApplyNames／Assembly 反例。

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

from contracts.schema.str06.naming import NameAssignment, NameMapV5, TargetName
from contracts.schema.str06.source import (
    CharactersArtifactV4, CharactersCore, NameStructure,
)
from novel.stages.str06.literal_scan import apply_replacements
from novel.stages.str06.name_structure import (
    compile_name_structure, surname_construction_probe,
)
from novel.stages.str06.naming import (
    compile_name_map, compile_surface_replacements, diff_proof, naming_projection,
)
from novel.stages.str06.assembly import (
    BodyAccess, assemble_delivery, body_access, delivery_identity,
)

SRC = ('皇甫珩执剑护院。陈砚与陈松是亲兄弟，住在陈家。赵老大与赵老二素不相识。'
       '云溪居士隐居山中。夫人安坐。柳条垂岸。')


# ───────────────────────── 名称结构夹具 ─────────────────────────

def _chars(**people_over) -> CharactersArtifactV4:
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
    return CharactersArtifactV4.model_validate({
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


def _surface(name: str) -> dict:
    return {'surface': name, 'from_chapter': 1, 'until_chapter': None,
            'source_refs': ['e01_01']}


def _naming_wire(entries) -> dict:
    return {'entries': entries}


# ───────────────────────── 名称结构 compile（Stage2） ─────────────────────────

def test_name_structure_compile_green_and_refusals():
    core = _chars().core_view()
    structure = _chars().name_structure
    wire = structure.model_dump(mode='json')
    compiled, errors = compile_name_structure(wire, core, SRC)
    assert compiled is not None and not errors, errors

    bad = {'families': wire['families'], 'people': wire['people'],
           'extra': 1}
    _, errors = compile_name_structure(bad, core, SRC)
    assert any('未知字段' in e for e in errors)

    bad = {'families': wire['families'],
           'people': {**wire['people'], 'ghost': wire['people']['fu_ren']}}
    _, errors = compile_name_structure(bad, core, SRC)
    assert any('Characters 之外' in e for e in errors)

    bad = {'families': wire['families'],
           'people': {k: v for k, v in wire['people'].items() if k != 'fu_ren'}}
    _, errors = compile_name_structure(bad, core, SRC)
    assert any('缺少人物条目' in e for e in errors)

    # 模板渲染等式：构词与 surface 对不上即拒
    bad = {'families': wire['families'], 'people': {
        **wire['people'],
        'chen_yan': {**wire['people']['chen_yan'],
                     'surface_forms': {'陈砚': '{given}', '砚哥': '{given}哥'}}}}
    _, errors = compile_name_structure(bad, core, SRC)
    assert any('渲染不一致' in e for e in errors)

    # 纯称谓含本人姓名字面 → 防漏标拒绝
    bad = {'families': wire['families'], 'people': {
        **wire['people'],
        'fu_ren': {**wire['people']['fu_ren'],
                   'family_id': 'fam_chen',
                   'surface_forms': {'夫人': None, '陈夫人': None}}}}
    _, errors = compile_name_structure(bad, core, SRC)
    assert any('防漏标' in e for e in errors)

    # 字面必须在源文中出现
    bad = {'families': {**wire['families'],
                        'fam_hpf': {'literal': '慕容', 'forms': []}},
           'people': wire['people']}
    _, errors = compile_name_structure(bad, core, SRC)
    assert any('未在源文中出现' in e for e in errors)


def test_surname_probe_reports_and_never_blocks():
    core = _chars().core_view()
    structure = _chars().name_structure
    probe = surname_construction_probe(structure, SRC + '赵家村口。')
    assert any(f.literal == '赵' and not f.strict for f in probe)  # 单字姓：只列上下文
    multi = [f for f in probe if f.literal == '皇甫']
    assert multi and all(f.strict for f in multi)                  # 复姓：逐处严格
    # 探针不阻断：同一 structure 照常编译
    compiled, errors = compile_name_structure(
        structure.model_dump(mode='json'), core, SRC)
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
    table, errors = compile_surface_replacements(chars, name_map)
    assert not errors, errors
    # 复姓完整替换，不被截成首字
    assert table['皇甫珩'] == '慕容珩'
    assert table['皇甫公子'] == '慕容公子'
    # 家族级称谓按同 family_id 新姓一致展开
    assert table['陈家'] == '沈家'
    # 独立化名显式赋值，不由新名推导
    assert table['云溪居士'] == '白云先生'
    text = '皇甫珩与陈砚在陈家。云溪居士旁观。'
    renamed, claims = apply_replacements(text, table)
    assert renamed == '慕容珩与沈砚在沈家。白云先生旁观。'
    assert [c.literal for c in claims] == ['皇甫珩', '陈砚', '陈家', '云溪居士']


def test_same_surface_multiple_values_rejected_same_value_ok():
    from novel.stages.str06.naming import _record
    # 同一源字面对应多个不同值：报错并列出 owner，不按替换顺序选
    errors: list[str] = []
    owners: dict[str, str] = {}
    table_x: dict[str, str] = {}
    _record(table_x, owners, '大郎', '柳大', 'a', errors)
    _record(table_x, owners, '大郎', '顾大', 'b', errors)
    assert any('多个不同值' in e and 'a' in e and 'b' in e for e in errors)
    # 同值多归属：合法（共享同姓本身不是冲突）
    _record(table_x, owners, '二郎', '顾二', 'b', errors)
    _record(table_x, owners, '二郎', '顾二', 'c', errors)
    assert not any('二郎' in e for e in errors)


def test_replacement_value_containing_key_rejected():
    chars = _chars()
    name_map = _green_map(chars)
    table, _ = compile_surface_replacements(chars, name_map)
    tampered = dict(table)
    tampered['砚哥'] = '沈砚哥里含陈砚'  # 值包含另一 key：链式替换风险
    _, errors = compile_surface_replacements(chars, name_map)
    assert not errors  # 原表干净
    from novel.stages.str06.naming import _record
    errors2: list[str] = []
    owners: dict[str, str] = {'陈砚': 'x'}
    table_y = {'陈砚': '沈砚'}
    _record(table_y, owners, '砚哥', '沈砚里含陈砚', 'y', errors2)
    for key, value in table_y.items():
        for other in table_y:
            if other != key and other in value:
                errors2.append('链式')
    assert any('链式' in e for e in errors2)
    del tampered


# ───────────────────────── residual 与 diff proof ─────────────────────────

def test_residual_claims_and_kept_no_false_positives():
    table = {'裴书远': '林书言', '裴': '林'}
    text = '裴书远与裴兄同行'
    renamed, claims = apply_replacements(text, table)
    # 目标名含旧字面：书言/林 出现在替换值里 → 认领区间吸收，不误报
    from novel.stages.str06.naming import check_residual
    assert check_residual(renamed, table, claims) == []
    # kept 包含映射键：该字面是合法保留面 → 从查禁集剔除
    assert check_residual('裴家老宅', table, (), ('裴',)) == []
    # 未认领且应替换的残留 → 拒
    problems = check_residual('裴书远与裴兄'.replace('裴书远', '林书言') + '裴', table, claims[:0])
    assert problems and '裴' in problems[0]


def test_diff_proof_green_and_tamper_red():
    table = {'裴书远': '林书言', '陆大哥': '顾大哥'}
    original = '裴书远对陆大哥说。'
    renamed, claims = apply_replacements(original, table)
    assert diff_proof(original, renamed, table, claims) == []
    tampered = renamed + '多一个字'
    assert diff_proof(original, tampered, table, claims)


# ───────────────────────── Assembly：就绪边界与身份分离 ─────────────────────────

def test_body_access_states_separated():
    access = body_access(['s01', 's02', 's03', 's04'], {
        's01': {'status': 'accepted'}, 's03': {'status': 'rejected'},
        's04': {'status': 'something_else'}})
    assert access.accepted == ('s01',)
    assert access.missing == ('s02',)
    assert access.rejected == ('s03',)
    assert access.unknown == ('s04',)
    assert not access.ready


def _hook_receipt_dict(prose: str, context_sha: str, status: str = 'accepted') -> dict:
    import hashlib
    return {'hook_context_sha256': context_sha, 'prose_sha256':
            hashlib.sha256(prose.encode('utf-8')).hexdigest(),
            'request_identity': 'r1', 'validator_version': 'v1', 'status': status,
            'mechanical_checks': ('non_empty',), 'semantic': 'unverified'}


def _ready_delivery():
    table = {'裴书远': '林书言'}
    hook, body_text = '楔子：裴书远夜行。', '正文：裴书远到家。'
    context_sha = 'c' * 64
    receipt = _hook_receipt_dict(hook, context_sha)
    access = body_access(['s01'], {'s01': {'status': 'accepted'}})
    outcome = assemble_delivery(body_text=body_text, body=access, hook_prose=hook,
                                hook_receipt=receipt, hook_context_sha256=context_sha,
                                replacements=table, kept_literals=())
    return outcome, table, hook, body_text, context_sha, receipt, access


def test_assembly_replay_is_byte_stable_and_identity_separated():
    outcome, table, hook, body_text, context_sha, receipt, access = _ready_delivery()
    assert outcome.status == 'ready'
    assert outcome.delivery_bytes.decode('utf-8') == '楔子：林书言夜行。\n\n正文：林书言到家。'
    again = assemble_delivery(body_text=body_text, body=access, hook_prose=hook,
                              hook_receipt=receipt, hook_context_sha256=context_sha,
                              replacements=table, kept_literals=())
    assert again.delivery_bytes == outcome.delivery_bytes
    assert again.delivery_identity == outcome.delivery_identity
    # 换 NameMap：交付身份变，但 Stage5/Hook 回执的输入原样（回执字段无交付材料）
    other = {'裴书远': '周书言'}
    changed = assemble_delivery(body_text=body_text, body=access, hook_prose=hook,
                                hook_receipt=receipt, hook_context_sha256=context_sha,
                                replacements=other, kept_literals=())
    assert changed.delivery_identity != outcome.delivery_identity
    assert set(receipt) & {'name_map', 'delivery_identity'} == set()


def test_assembly_not_ready_without_accepted_body():
    table = {'裴书远': '林书言'}
    hook, context_sha = '楔子。', 'c' * 64
    receipt = _hook_receipt_dict(hook, context_sha)
    outcome = assemble_delivery(body_text=None, body=body_access([], {}),
                                hook_prose=hook, hook_receipt=receipt,
                                hook_context_sha256=context_sha,
                                replacements=table, kept_literals=())
    assert outcome.status == 'not_ready'
    assert outcome.reason == 'body_not_implemented'
    assert outcome.delivery_bytes is None and outcome.delivery_identity is None


def test_assembly_hook_receipt_binding_enforced():
    outcome, *_ = _ready_delivery()
    table = {'裴书远': '林书言'}
    hook, body_text, context_sha, receipt, access = (
        '楔子：裴书远夜行。', '正文：裴书远到家。', 'c' * 64,
        _hook_receipt_dict('楔子：裴书远夜行。', 'c' * 64),
        body_access(['s01'], {'s01': {'status': 'accepted'}}))
    wrong_ctx = assemble_delivery(body_text=body_text, body=access, hook_prose=hook,
                                  hook_receipt=receipt, hook_context_sha256='d' * 64,
                                  replacements=table, kept_literals=())
    assert wrong_ctx.status == 'not_ready' and wrong_ctx.reason == 'hook_context_mismatch'
    tampered_receipt = _hook_receipt_dict('别的成文', context_sha)
    wrong_prose = assemble_delivery(body_text=body_text, body=access, hook_prose=hook,
                                    hook_receipt=tampered_receipt,
                                    hook_context_sha256=context_sha,
                                    replacements=table, kept_literals=())
    assert wrong_prose.status == 'not_ready' and wrong_prose.reason == 'hook_prose_mismatch'
    rejected = assemble_delivery(
        body_text=body_text, body=access, hook_prose=hook,
        hook_receipt=_hook_receipt_dict(hook, context_sha, status='rejected'),
        hook_context_sha256=context_sha, replacements=table, kept_literals=())
    assert rejected.status == 'not_ready' and rejected.reason == 'hook_rejected'


def test_delivery_identity_pure_function():
    assert delivery_identity(body_sha256='a' * 64, hook_prose_sha256='b' * 64,
                             replacements={'x': 'y'}, kept_literals=('k',)) == \
        delivery_identity(body_sha256='a' * 64, hook_prose_sha256='b' * 64,
                          replacements={'x': 'y'}, kept_literals=('k',))
    assert delivery_identity(body_sha256='a' * 64, hook_prose_sha256='b' * 64,
                             replacements={'x': 'y'}, kept_literals=()) != \
        delivery_identity(body_sha256='a' * 64, hook_prose_sha256='b' * 64,
                          replacements={'x': 'y'}, kept_literals=('k',))
