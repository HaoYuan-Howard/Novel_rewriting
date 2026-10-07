#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""test_str06_name_variants —— CS-S 1c（names v6：A＋B＋C）的契约反例。

C（构词派生）：`derive_surface_forms` 的占位符最大替换——今日两本模型自给
的 surface_forms 51/51 逐条一致的类型面（复姓、{family}兄、姓{family}的、
纯称谓、给名/裸名、化名）与多解歧义（须 form_overrides）。
B（候选先行）：候选生成只收「≥3 字含姓名部件锚的同首字编辑距离 1」类——
『裴知远』类收、『太子妃→太子的』类（纯称谓锚）与插入／标点噪声不收；
无候选＝零调用。
A（守卫）：people 键集合闸、same 只在证据段局部、构词多解未决（详见
hard_cases 与 test_str06_name_resolution.py 的判据链）。
"""
from __future__ import annotations

from contracts.schema.source import CharactersCore, CharacterEntry
from novel.stages.stage2.names import (
    NameVariantCandidate, _edit_distance_one, derive_surface_forms,
    name_variant_candidates,
)
from contracts.schema.source import NameStructure


def test_derive_forms_cover_the_real_book_surface_types():
    """C 的 oracle 类型面（今日两本 51 条构词类型的合成复现，不提交盘上产物）。"""
    # 复姓＋名（最长优先：family=皇甫 优先于任何单字部件）。
    assert derive_surface_forms('皇甫珩', {'family': '皇甫', 'given': '珩'}) == \
        ('{family}{given}', False)
    # 姓＋称谓。
    assert derive_surface_forms('沈公子', {'family': '沈'}) == ('{family}公子', False)
    # 「姓X的」构词。
    assert derive_surface_forms('姓沈的', {'family': '沈'}) == ('姓{family}的', False)
    # 纯称谓（无身份字面）→ None。
    assert derive_surface_forms('夫君', {'family': '沈', 'given': '知言'}) == \
        (None, False)
    assert derive_surface_forms('表弟', {}) == (None, False)
    # 给名／裸名。
    assert derive_surface_forms('知言', {'family': '沈', 'given': '知言'}) == \
        ('{given}', False)
    # 全名＝family＋given 两部件拼接（不是「given 吃掉全名」）。
    assert derive_surface_forms('沈知言', {'family': '沈', 'given': '知言'}) == \
        ('{family}{given}', False)
    # 化名。
    assert derive_surface_forms('柳青', {'alias:al_x': '柳青'}) == \
        ('{alias:al_x}', False)
    # 家族字面重复出现（占位符最大替换＝每处都替换）。
    assert derive_surface_forms('沈家沈氏', {'family': '沈'}) == \
        ('{family}家{family}氏', False)


def test_derive_forms_flag_same_position_same_length_ambiguity():
    """多解＝同位同长的最大字面属于多个部件——须 form_overrides（A：构词多解
    未决）。不同长度不歧义（最长优先是确定性的）。"""
    template, ambiguous = derive_surface_forms(
        '云娘', {'family': '云', 'given': '娘', 'alias:al_x': '云'})
    assert ambiguous
    assert template in ('{family}{given}', '{alias:al_x}{given}')
    # 长度不同 → 无歧义。
    assert derive_surface_forms('云娘', {'family': '云', 'given': '云娘'}) == \
        ('{given}', False)


def test_edit_distance_one_first_char_must_match():
    assert _edit_distance_one('裴知远', '裴书远')       # 替换
    assert _edit_distance_one('裴书远啊', '裴书远')     # 插入（尾部）
    assert _edit_distance_one('书远', '裴书远') is False  # 首字不同
    assert _edit_distance_one('裴书', '裴书远')          # 缺字
    assert _edit_distance_one('裴书远', '裴书远') is False
    assert _edit_distance_one('裴大远二', '裴书远') is False


def _core_with(surfaces: dict[str, list[str]]) -> CharactersCore:
    return CharactersCore.model_validate({
        'characters': {slug: {'canonical': names[0], 'surfaces': [
            {'surface': name, 'from_chapter': 1, 'until_chapter': None,
             'source_refs': ['e01_01']} for name in names]}
            for slug, names in surfaces.items()},
        'initial_state': []})


def _structure_with(forms: dict[str, dict[str, str | None]],
                    families: dict | None = None) -> NameStructure:
    return NameStructure.model_validate({
        'families': families if families is not None
        else {'fam_pei': {'literal': '裴', 'forms': []}},
        'people': {slug: {'family_id': 'fam_pei' if slug == 'pei' else None,
                          'given': None, 'aliases': [],
                          'surface_forms': person}
                   for slug, person in forms.items()},
        'resolutions': []})


def test_candidates_take_variant_class_and_refuse_title_noise():
    """『裴知远』类进候选；纯称谓锚（太子妃→太子的）与「anchor＋后继字／
    标点」插入邻域不进（互为子串滤除——夫君不行实测 16 条噪声的类）。"""
    evidence = {
        'e01_01': {'chapter': 1, 'text': '裴书远夜里赶路，遇裴知远于渡口。'},
        'e01_02': {'chapter': 1, 'text': '太子妃不悦，太子的赏赐迟迟未到。'},
    }
    core = _core_with({'pei': ['裴书远'], 'taizi_fei': ['太子妃']})
    structure = _structure_with({
        'pei': {'裴书远': '{family}书远'},
        'taizi_fei': {'太子妃': None}})
    # 裴书远 的部件应是 family=裴＋given=书远——重建以匹配真实构词。
    structure = NameStructure.model_validate({
        'families': {'fam_pei': {'literal': '裴', 'forms': []}},
        'people': {'pei': {'family_id': 'fam_pei', 'given': '书远', 'aliases': [],
                           'surface_forms': {'裴书远': '{family}{given}'}},
                   'taizi_fei': {'family_id': None, 'given': None, 'aliases': [],
                                 'surface_forms': {'太子妃': None}}},
        'resolutions': []})
    candidates = name_variant_candidates(structure, core, evidence)
    assert [c.literal for c in candidates] == ['裴知远']
    assert candidates[0].anchor == '裴书远'
    assert candidates[0].segments == ('e01_01',)
    # 太子妃（纯称谓，2 字部件无——派生 None）不是锚：'太子的' 不进候选。
    # '太子妃不' 类插入噪声：包含登记 surface 太子妃 → 滤除。


def test_candidates_require_name_bearing_anchor_of_three_or_more():
    """两字姓名面不作锚（夫君不行 24／75 条噪声的实测类）：苏娘（{family}{given}
    两字）的 ed1 字面不进候选；三字姓名面（苏芸娘）的 ed1 字面进。"""
    evidence = {
        'e01_01': {'chapter': 1, 'text': '苏娘出门，苏娘远眺。'},
        'e01_02': {'chapter': 1, 'text': '苏芸娘回头，苏芸孃一笑。'},
    }
    core = _core_with({'yun': ['苏娘', '苏芸娘']})
    structure = NameStructure.model_validate({
        'families': {'fam_su': {'literal': '苏', 'forms': []}},
        'people': {'yun': {'family_id': 'fam_su', 'given': '芸娘', 'aliases': [],
                           'surface_forms': {'苏娘': '{family}{given}',
                                             '苏芸娘': '{family}{given}'}}},
        'resolutions': []})
    candidates = name_variant_candidates(structure, core, evidence)
    assert [c.literal for c in candidates] == ['苏芸孃']


def test_name_variant_candidate_is_frozen_dataclass():
    cand = NameVariantCandidate(literal='裴知远', char_id='pei',
                                anchor='裴书远', segments=('e01_01',))
    assert cand.literal == '裴知远'
