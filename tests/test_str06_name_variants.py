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
    assert cand.anchors == (('pei', '裴书远'),)


def _pei_structure(*extra_people: str) -> NameStructure:
    people = {'pei': {'family_id': 'fam_pei', 'given': '书远', 'aliases': [],
                      'surface_forms': {'裴书远': '{family}{given}'}}}
    for slug in extra_people:
        people[slug] = {'family_id': None, 'given': None, 'aliases': [],
                        'surface_forms': {name: None for name in
                                          _EXTRA_SURFACES[slug]}}
    return NameStructure.model_validate({
        'families': {'fam_pei': {'literal': '裴', 'forms': []}},
        'people': people, 'resolutions': []})


_EXTRA_SURFACES = {'yuan': ['远'], 'niang': ['娘']}


def test_candidate_substring_filter_ignores_short_registered_titles():
    """1～2 字的登记称谓（「远」「娘」）不得参与子串滤除：它们会把含该字的真变体
    （「裴知远」）整条吞掉，使异写无候选而漏过。≥3 字的登记面照常滤除。"""
    evidence = {'e01_01': {'chapter': 1, 'text': '遇裴知远于渡口。'}}
    core = _core_with({'pei': ['裴书远'], 'yuan': ['远'], 'niang': ['娘']})
    candidates = name_variant_candidates(
        _pei_structure('yuan', 'niang'), core, evidence)
    assert [c.literal for c in candidates] == ['裴知远']
    # 对照：≥3 字登记面仍滤除含它的邻域（裴书远了）。
    evidence = {'e01_01': {'chapter': 1, 'text': '裴书远了，裴书远。'}}
    assert name_variant_candidates(
        _pei_structure('yuan', 'niang'), core, evidence) == ()


def test_candidates_exclude_slices_carrying_punctuation_or_space():
    """邻域切片带标点／空白（「裴书，」「裴书 」）不是名字，不进请求；否则噪声
    进提示词，且判 same 后归一会吃掉标点。"""
    evidence = {'e01_01': {'chapter': 1, 'text': '裴书，来了。裴书。去了。裴书！裴书 在。'}}
    core = _core_with({'pei': ['裴书远']})
    assert name_variant_candidates(_pei_structure(), core, evidence) == ()


def _two_anchor_case():
    """裴书远（pei）与裴书达（pei2）同为 ≥3 字锚；『裴书运』与两者都差一字。"""
    evidence = {'e01_01': {'chapter': 1, 'text': '裴书运拦住了去路。'}}
    core = _core_with({'pei': ['裴书远'], 'pei2': ['裴书达'], 'stranger': ['陌生人']})
    structure = NameStructure.model_validate({
        'families': {'fam_pei': {'literal': '裴', 'forms': []}},
        'people': {
            'pei': {'family_id': 'fam_pei', 'given': '书远', 'aliases': [],
                    'surface_forms': {'裴书远': '{family}{given}'}},
            'pei2': {'family_id': 'fam_pei', 'given': '书达', 'aliases': [],
                     'surface_forms': {'裴书达': '{family}{given}'}},
            'stranger': {'family_id': None, 'given': None, 'aliases': [],
                         'surface_forms': {'陌生人': None}}},
        'resolutions': []})
    return core, structure, evidence


def test_candidate_keeps_every_anchor_person_not_just_the_first():
    """同一字面落在两个人物锚的邻域：全部保留并进请求，不静默归给先到的人物。"""
    from novel.stages.stage2.names import name_variants_prompt
    core, structure, evidence = _two_anchor_case()
    candidates = name_variant_candidates(structure, core, evidence)
    assert [c.literal for c in candidates] == ['裴书运']
    assert candidates[0].anchors == (('pei', '裴书远'), ('pei2', '裴书达'))
    prompt = name_variants_prompt(candidates, evidence)
    assert '裴书远，人物 pei' in prompt and '裴书达，人物 pei2' in prompt


def test_same_verdict_must_point_at_an_anchor_person():
    """提示词说 same 只能指向近似锚人物：compile 同口径执行——指向锚之外的
    已登记人物＝凭空新增同一性，条目级拒收；指向任一锚人物则接受。"""
    from novel.stages.stage2.names import compile_name_variants
    core, structure, evidence = _two_anchor_case()
    candidates = name_variant_candidates(structure, core, evidence)

    def wire(char_id: str, surface: str) -> dict:
        return {'protocol_version': 'stage2-name-variants-v1', 'variants': [{
            'literal': '裴书运', 'verdict': 'same', 'char_id': char_id,
            'surface': surface, 'evidence_refs': ['e01_01'], 'reason': '同一人物笔误'}]}

    value, located = compile_name_variants(
        wire('stranger', '陌生人'), candidates, evidence=evidence, characters=core)
    assert value is None and any('只能指向近似锚' in m for _p, m in located), located
    for char_id, surface in (('pei', '裴书远'), ('pei2', '裴书达')):
        value, located = compile_name_variants(
            wire(char_id, surface), candidates, evidence=evidence, characters=core)
        assert not located and value[0]['char_id'] == char_id


def test_variants_prompt_carries_neighbor_context_and_mechanical_facts():
    """判据（2026-10-08 修复「裴知远」死锁）：判定材料须含**同章邻段上下文**
    （正名复指证据常在邻段——只给出现段会让单次笔误在材料面上不可判定）与
    代码派生的机械事实（字面/锚称谓出现次数、姓名结构、同姓登记人物清单）；
    邻段明确标注非证据段（evidence_refs 仍限出现段，compile 同口径核对）。"""
    from novel.stages.stage2.names import name_variants_prompt
    evidence = {
        'e01_01': {'chapter': 1, 'text': '夫君连日在外吃酒。'},
        'e01_02': {'chapter': 1, 'text': '家里就剩下了我和裴知远。'},
        'e01_03': {'chapter': 1, 'text': '我和裴书远在家里偶遇的次数变多了。'},
        'e02_01': {'chapter': 2, 'text': '裴书远别扭地打招呼。裴书远又来了。'},
    }
    core = _core_with({'pei': ['裴书远']})
    structure = NameStructure.model_validate({
        'families': {'fam_pei': {'literal': '裴', 'forms': []}},
        'people': {'pei': {'family_id': 'fam_pei', 'given': '书远', 'aliases': [],
                           'surface_forms': {'裴书远': '{family}{given}'}}},
        'resolutions': []})
    candidates = name_variant_candidates(structure, core, evidence)
    assert [c.literal for c in candidates] == ['裴知远']
    prompt = name_variants_prompt(candidates, evidence, structure)
    assert '[e01_02] 家里就剩下了我和裴知远。' in prompt
    # 邻段上下文：出现段前后各一段（e01_01／e01_03），且明确非证据段。
    assert '邻段上下文，非证据段' in prompt
    assert '[e01_03] 我和裴书远在家里偶遇的次数变多了。' in prompt, \
        '正名复指证据必须在材料里'
    # 机械事实：候选出现 1 次、锚称谓出现次数、姓名结构、同姓唯一清单。
    assert '字面「裴知远」全书出现 1 次' in prompt
    assert '锚称谓「裴书远」出现' in prompt
    assert '人物 pei 姓「裴」，名「书远」' in prompt
    assert '书内姓「裴」的登记人物：仅 pei' in prompt


def test_variants_prompt_marks_multi_anchor_same_family_as_needing_context():
    """守门反例：同一字面落在两个同姓锚（裴书远／裴书达）邻域——事实行必须
    如实写「不止一人，须凭上下文区分」，不得借唯一性事实替模型判定。"""
    from novel.stages.stage2.names import name_variants_prompt
    core, structure, evidence = _two_anchor_case()
    candidates = name_variant_candidates(structure, core, evidence)
    prompt = name_variants_prompt(candidates, evidence, structure)
    assert '不止一人，须凭上下文区分' in prompt
    assert '仅 pei、' not in prompt


def test_variants_rules_pin_two_axis_evidence_standard():
    """判据锚定（2026-10-08）：same＝字面相似＋上下文证据之一（两轴缺一不可，
    证据形态 a/b/c 显式列出）；「仅凭全书只出现一次」不构成 unresolved——
    单次笔误正是本任务最常见的形态；邻段不进 evidence_refs。"""
    from novel.stages.common import NAME_VARIANTS_RULES

    for phrase in ('两轴缺一不可', '正名复指', '上下文唯一匹配', '称呼链延续',
                   '不构成 unresolved 的理由', '邻段只是上下文，不进 evidence_refs'):
        assert phrase in NAME_VARIANTS_RULES, phrase
