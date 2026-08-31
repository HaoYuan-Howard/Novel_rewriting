#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
test_stage7_subs.py —— 替换器与残留探针。**不吃语料，纯函数。**

【为什么单独一个文件】`tests/test_stage7.py` 整份挂着「盘上有没有这本书」的 skipif
（形态照 `tests/test_stage6.py`），而本文件这一族**在任何机器上都该跑**——
替换器是本层唯一动正文字节的东西，它不该因为语料不在就没人查。

⚠ 本文件盯的是那**三处方向各不相同的 count**（`subs.py` 模块头那张表）。
每一处弄错都不会报错，只会让某个数字静默地变成另一个意思。
"""
from __future__ import annotations

import json
from contextlib import nullcontext
from pathlib import Path
from types import SimpleNamespace

import pytest

from pydantic import ValidationError

from contracts.schema import decode_artifact
from contracts.schema.stage7 import ExtraSub, NameDecision, NameMapEdit, Residual
from novel import prov
from novel.consumption import stage7_grounding
from novel.core.naming import family_groups, strip_distinguisher, surface_owners
from novel.errors import ApprovalRequired, BlockingFindingsUnresolved, TerminalState
from novel.stages.stage7 import assemble, checks, prompts, rename
from novel.stages.stage7.subs import (
    apply_subs, derived_alias_scan, mask_known, residual_scan, sub_table, surface_hits,
    unclaimed_residual_keys,
)

# 「阿禾」⊂「阿禾姊姊」· 「青禾」⊂「孟青禾」——嵌套是本层全部风险的来源。
CH1 = '阿禾姊姊回来了。孟青禾站在门口，青禾轻轻应了一声，阿禾没有回头。'


def test_derived_alias_probe_catches_name_tail_nickname_without_auto_replacing():
    characters = [{
        'char_id': 'char_001', 'old_canonical': '谢箬', 'new_canonical': '苏瑶',
        'renamed': True, 'aliases': [],
    }]

    rows = derived_alias_scan([('ch6', '箬姐姐替她掩上房门。')], characters)

    assert [(row['unit'], row['suggest']) for row in rows] == [('箬姐姐', '瑶姐姐')]
    findings = checks.rename_findings(
        name_map={'surname_map': {}, 'characters': characters,
                  'extra_subs': [], 'subs': {}, 'kept': []},
        subs={}, problems=[], applied={}, residual=[], baseline={},
        dispatched_characters={},
        pre_target='', source_text='', residual_keys=set(), stem_noise={},
        family_members={}, missing=[], invalids=[],
        post_units=[('ch6', '箬姐姐替她掩上房门。')], title_line='书名语义需人工确认',
        derived_aliases=rows)
    hit = [row for row in findings if row.rule == 'rn_derived_alias_candidate']
    assert len(hit) == 1 and '箬姐姐→瑶姐姐' in hit[0].detail


def test_derived_alias_probe_with_ambiguous_new_tail_refuses_to_guess():
    characters = [
        {'char_id': 'char_001', 'old_canonical': '谢箬', 'new_canonical': '苏瑶',
         'renamed': True, 'aliases': []},
        {'char_id': 'char_002', 'old_canonical': '陆箬', 'new_canonical': '顾宁',
         'renamed': True, 'aliases': []},
    ]
    rows = derived_alias_scan([('ch1', '箬姐姐来了。')], characters)
    assert len(rows) == 1 and rows[0]['suggest'] is None


def test_derived_alias_probe_masks_an_explicitly_kept_long_surface():
    characters = [{
        'char_id': 'char_001', 'old_canonical': '谢箬', 'new_canonical': '苏瑶',
        'renamed': True, 'aliases': [],
    }]
    assert derived_alias_scan(
        [('ch1', '箬姐姐来了。')], characters, kept={'箬姐姐'}) == []


def test_edit_template_is_a_registry_encoded_strict_v1_artifact():
    raw = json.loads(rename.edit_template_json())
    assert raw['_schema_version'] == 1
    assert set(raw) == {
        '_schema_version', 'surname_map', 'characters',
        'character_patches', 'extra_subs',
    }
    model = NameMapEdit.model_validate(
        decode_artifact(rename.EDIT, raw).payload)
    assert model == NameMapEdit()


def test_rename_prompt_receives_the_typed_era_instead_of_an_alias_mismatch():
    """时代背景必须真的进最终 user prompt，不能因 ``era``/中文 alias 错位变未知。"""
    material = prompts.build_material(
        title='测试书', registry={}, relations=[], era='架空古代江南',
        groups={}, sur={}, book_text='', targets=[],
    )

    user = prompts.build_user(material)

    assert '"架空古代江南"' in user
    era_block = next(block for block in material.blocks if '时代背景' in block)
    assert '（未知）' not in era_block


def test_stage7_material_keeps_pure_titles_but_does_not_dispatch_a_ghost_family():
    """最终模型材料里人物仍在，但族群表不能把两个纯位号交成「皇姓一家」。"""
    registry = {
        'char_001': {'canonical': '皇贵夫', 'name_distinctiveness': 'medium', 'address': []},
        'char_002': {'canonical': '皇太女', 'name_distinctiveness': 'high', 'address': []},
        'char_003': {'canonical': '王夫人', 'name_distinctiveness': 'medium', 'address': []},
        'char_004': {'canonical': '王姑娘', 'name_distinctiveness': 'high', 'address': []},
    }
    book = '皇贵夫拜见皇太女，王夫人携王姑娘入宫。'
    groups, surnames, _accounting = family_groups(
        registry, book, surface_owners(registry))

    material = prompts.build_material(
        title='测试书', registry=registry, relations=[], era='架空古代',
        groups=groups, sur=surnames, book_text=book, targets=[book],
    )
    user = prompts.build_user(material)

    assert '皇贵夫' in user and '皇太女' in user
    assert [row['原姓'] for row in material.groups] == [['王']]
    assert [member['char_id'] for member in material.groups[0]['成员']] == [
        'char_003', 'char_004',
    ]
    assert json.dumps(material.groups, ensure_ascii=False) in user


def test_generic_appellation_wrapper_may_keep_label_and_replace_embedded_name():
    """真实 E2E：`女主方衡→女主沈凌` 只改真名，不是把「女主」关系改掉。"""
    subs = {'女主方衡': '女主沈凌', '方衡': '沈凌'}
    assert not checks._generic_appellation_was_touched(
        '女主方衡', '女主沈凌', subs, {'方'}, set())
    assert checks._generic_appellation_was_touched(
        '姐姐', '妹妹', {'姐姐': '妹妹'}, set(), set())
    assert checks._generic_appellation_was_touched(
        '女主方衡', '女主沈凌', {'女主方衡': '女主沈凌'}, {'方'}, set()), \
        '没有内嵌真名映射作证时仍要保守拦截'


# ── ①b 「名」不是关系词：`rn_kinship_touched` 的第二条豁免 ─────────────────

#: 一真一诱饵。`周子白` 的名是 `子白`，而 `子` 在亲属字表里（儿子）；
#: `陈娘子` 去姓之后**正好**是 `娘子` —— 本条要守的东西。
_KINSHIP_CHARS = [
    {'char_id': 'char_001', 'old_canonical': '周子白', 'new_canonical': '顾清宴',
     'renamed': True, 'aliases': [{'old': '子白', 'new': '清宴'}]},
    {'char_id': 'char_002', 'old_canonical': '陈娘子', 'new_canonical': '林娘子',
     'renamed': True, 'aliases': []},
]
_KINSHIP_SURNAMES = {'周', '陈'}


def test_the_given_name_half_of_a_renamed_canonical_is_not_a_kinship_word():
    """⭐ `周子白→顾清宴` 时 `子白→清宴` 是**名**的那一半，不是关系词。

    姓氏豁免只看首字（`周` 在映射里、`子` 不在），够不着这一形。按本条去修数据，
    唯一走法是把 `子白` 登记成不改——**那会让原作的名活着进入「原创」成品**。
    """
    halves = checks.given_name_halves(_KINSHIP_CHARS, _KINSHIP_SURNAMES)
    subs = {'周子白': '顾清宴', '子白': '清宴', '陈娘子': '林娘子'}
    assert ('子白', '清宴') in halves
    assert not checks._generic_appellation_was_touched(
        '子白', '清宴', subs, _KINSHIP_SURNAMES, halves)


def test_the_exemption_is_a_pair_so_a_relationship_word_still_cannot_slip_through():
    """⭐⭐ **豁免按 `(旧, 新)` 配对，不按旧名单边。**

    只按旧名豁免的话，`陈娘子→林娘子` 会顺带把 `娘子` 放进白名单，
    于是任何一条 `娘子→X` 都不再被拦——而 `娘子` 正是本条要守的东西。
    """
    halves = checks.given_name_halves(_KINSHIP_CHARS, _KINSHIP_SURNAMES)
    subs = {'陈娘子': '林娘子', '娘子': '夫人'}
    assert ('娘子', '娘子') in halves, '配对里确实有它，但两边相同'
    assert checks._generic_appellation_was_touched(
        '娘子', '夫人', subs, _KINSHIP_SURNAMES, halves), \
        '把关系词换成另一个关系词仍然违规'
    assert checks._generic_appellation_was_touched(
        '大娘', '苏娘', subs, _KINSHIP_SURNAMES, halves), \
        '排行式称呼没有任何 canonical 供它派生 ⇒ 照旧被拦'


def test_a_character_that_is_not_renamed_contributes_no_exemption():
    """不改名的角色不该给任何映射发豁免——那正是幽灵映射的入口形状。"""
    chars = [{'char_id': 'char_001', 'old_canonical': '周子白', 'new_canonical': '周子白',
              'renamed': False, 'aliases': []}]
    assert checks.given_name_halves(chars, _KINSHIP_SURNAMES) == set()


# ── ①c 姓可以只由别名承载：`rn_surname_split` 的 orphan 那一路 ─────────────

def _mapping_findings(
    characters: list[dict], surname_map: dict, subs: dict,
    *, family_members: dict[str, tuple[str, ...]] | None = None,
    dispatched_characters: dict[str, dict] | None = None,
):
    """只跑映射表自身那几条判据；替换器侧入参全给空。"""
    if dispatched_characters is None:
        dispatched_characters = {
            c['char_id']: {
                'canonical': strip_distinguisher(c.get('old_canonical') or ''),
                'aliases': [
                    {'surface': (alias.get('old') or '').strip(), 'count': 0}
                    for alias in (c.get('aliases') or [])
                ],
            }
            for c in characters
        }
    return checks.rename_findings(
        name_map={'surname_map': surname_map, 'characters': characters,
                  'extra_subs': [], 'subs': subs, 'kept': []},
        subs=subs, problems=[], applied={'chapters': {}}, residual=[], baseline={},
        dispatched_characters=dispatched_characters,
        pre_target='', source_text='', residual_keys=set(), stem_noise={},
        family_members=family_members or {}, missing=[], invalids=[], post_units=[],
        title_line='书名语义需人工确认')


def _split_detail(characters: list[dict], surname_map: dict, subs: dict,
                  *, family_members: dict[str, tuple[str, ...]] | None = None
                  ) -> str | None:
    fs = _mapping_findings(
        characters, surname_map, subs, family_members=family_members)
    return next((f.detail for f in fs if f.rule == 'rn_surname_split'), None)


def test_a_surname_carried_only_by_an_alias_is_not_an_orphan_mapping():
    """⭐ `宝珠→林琬` 的 canonical 里没有姓，家姓只出现在别名 `陈娘子→林娘子` 上。

    只认 canonical 会把这条**真在用**的映射报成幽灵，而唯一的「修法」是去改一个
    正文里出现 0 次的登记标签（`陈母`/`陈父`）—— 那是判据错。
    """
    assert _split_detail(
        [{'char_id': 'char_001', 'old_canonical': '宝珠', 'new_canonical': '林琬',
          'renamed': True, 'aliases': [{'old': '陈娘子', 'new': '林娘子'},
                                       {'old': '小姐', 'new': '小姐'}]}],
        {'陈': '林'}, {'宝珠': '林琬', '陈娘子': '林娘子'}) is None


def test_a_mapping_no_one_uses_at_all_is_still_reported():
    """⭐⭐ 反例守卫：实测 `太后`/`太傅` 误组留下的 `太→宋`。

    两个角色都明确**不改名** ⇒ 既没有 canonical 也没有别名在用这个姓，
    这条幽灵映射此前能让全部 HARD 打绿。别名那一路不许把它放过去。
    """
    detail = _split_detail(
        [{'char_id': 'char_001', 'old_canonical': '太后', 'new_canonical': '太后',
          'renamed': False, 'aliases': [{'old': '太后', 'new': '太后'}]},
         {'char_id': 'char_002', 'old_canonical': '太傅', 'new_canonical': '太傅',
          'renamed': False, 'aliases': []}],
        {'太': '宋'}, {})
    assert detail is not None and '太→宋' in detail


def test_an_identity_alias_does_not_certify_a_surname_mapping():
    """别名两侧一字未改时不算「用过这个姓」——否则恒等条目会给任何映射背书。"""
    detail = _split_detail(
        [{'char_id': 'char_001', 'old_canonical': '宝珠', 'new_canonical': '林琬',
          'renamed': True, 'aliases': [{'old': '陈娘子', 'new': '陈娘子'}]}],
        {'陈': '陈'}, {'宝珠': '林琬'})
    assert detail is not None and '陈→陈' in detail


def test_a_real_family_cannot_opt_out_by_marking_every_member_unrenamed():
    """族群由确定性上游给出，模型不能靠把整组都写成 false 来撤掉 HARD。"""
    chars = [
        {'char_id': 'char_001', 'old_canonical': '沈知意', 'new_canonical': '沈知意',
         'renamed': False, 'aliases': [], 'not_renamed_reason': '误判为称谓', 'reason': ''},
        {'char_id': 'char_002', 'old_canonical': '沈知微', 'new_canonical': '沈知微',
         'renamed': False, 'aliases': [], 'not_renamed_reason': '误判为称谓', 'reason': ''},
    ]

    detail = _split_detail(
        chars, {}, {}, family_members={'沈': ('char_001', 'char_002')})

    assert detail is not None and "漏填 ['沈']" in detail


def test_renamed_true_requires_a_real_canonical_or_alias_change():
    """真实改动必须绑定这个角色**实际收到**的 canonical/alias。"""
    registry = {
        'char_001': {
            'canonical': '胖婶', 'address': [
                {'surface': '王婶'}, {'surface': '王婶'}, {'surface': '胖婶'},
            ],
        },
        'char_002': {'canonical': '皇贵夫', 'address': []},
    }
    mat = prompts.build_material(
        title='测试书', registry=registry, relations=[], era='架空古代',
        groups={}, sur={}, book_text='胖婶、王婶与皇贵夫。', targets=[],
    )

    alias_only = [{
        'char_id': 'char_001', 'old_canonical': '胖婶', 'new_canonical': '胖婶',
        'renamed': True, 'aliases': [{'old': '王婶', 'new': '林婶'}],
        'not_renamed_reason': None, 'reason': '描述词不动，只改带姓别名',
    }]
    canonical_change = [{
        'char_id': 'char_002', 'old_canonical': '皇贵夫', 'new_canonical': '沈知礼',
        'renamed': True, 'aliases': [], 'not_renamed_reason': None, 'reason': '改名',
    }]
    retained = [{
        'char_id': 'char_001', 'old_canonical': '胖婶', 'new_canonical': '胖婶',
        'renamed': False, 'aliases': [{'old': '王婶', 'new': '王婶'}],
        'not_renamed_reason': '只是描述称谓', 'reason': '',
    }]

    def rules(characters: list[dict]) -> set[str]:
        return {f.rule for f in _mapping_findings(
            characters, {}, {}, dispatched_characters=mat.chars)}

    assert 'rn_llm_shape' not in rules(alias_only + canonical_change)
    assert 'rn_llm_shape' not in rules(retained + canonical_change)

    invalids = {
        '全恒等 true': [alias_only[0], {
            **canonical_change[0], 'new_canonical': '皇贵夫',
        }],
        '只改虚构 alias': [alias_only[0], {
            **canonical_change[0], 'new_canonical': '皇贵夫',
            'aliases': [{'old': '虚构甲', 'new': '虚构乙'}],
        }],
        '挪用别人 alias': [alias_only[0], {
            **canonical_change[0], 'new_canonical': '皇贵夫',
            'aliases': [{'old': '王婶', 'new': '林婶'}],
        }],
        '漏 alias': [{**alias_only[0], 'aliases': []}, canonical_change[0]],
        '重复 alias': [{
            **alias_only[0], 'aliases': alias_only[0]['aliases'] * 2,
        }, canonical_change[0]],
        'canonical 重列进 alias': [{
            **alias_only[0],
            'aliases': alias_only[0]['aliases'] + [{'old': '胖婶', 'new': '林婶'}],
        }, canonical_change[0]],
        'false 却改 canonical': [retained[0], {
            **canonical_change[0], 'renamed': False, 'new_canonical': '沈知礼',
            'not_renamed_reason': '纯位号',
        }],
        'false 却改 alias': [{
            **retained[0], 'aliases': [{'old': '王婶', 'new': '林婶'}],
        }, canonical_change[0]],
        '重复 char_id': [alias_only[0], alias_only[0], canonical_change[0]],
    }
    for label, characters in invalids.items():
        assert 'rn_llm_shape' in rules(characters), label


def test_removing_only_a_registry_distinguisher_is_not_a_real_rename():
    registry = {
        'char_001': {'canonical': '皇贵夫(宫中消重标签)', 'address': []},
    }
    mat = prompts.build_material(
        title='测试书', registry=registry, relations=[], era='架空古代',
        groups={}, sur={}, book_text='皇贵夫入殿。', targets=[],
    )
    decision = [{
        'char_id': 'char_001', 'old_canonical': '皇贵夫(宫中消重标签)',
        'new_canonical': '皇贵夫', 'renamed': True, 'aliases': [],
        'not_renamed_reason': None, 'reason': '只去内部标签',
    }]

    rules = {f.rule for f in _mapping_findings(
        decision, {}, {}, dispatched_characters=mat.chars)}
    assert 'rn_llm_shape' in rules


def test_rename_semantic_marker_finding_catches_rank_change():
    kwargs = dict(
        name_map={'characters': [], 'surname_map': {}},
        problems=[], applied={}, residual=[], baseline={},
        dispatched_characters={},
        pre_target='', source_text='', residual_keys=set(), stem_noise={},
        family_members={}, missing=[], invalids=[],
        post_units=[], title_line='书名语义需人工确认',
    )
    changed = checks.rename_findings(subs={'七郎': '六郎'}, **kwargs)
    kept = checks.rename_findings(subs={'阿九': '玄九'}, **kwargs)
    assert 'rn_semantic_marker_changed' in {finding.rule for finding in changed}
    assert 'rn_semantic_marker_changed' not in {finding.rule for finding in kept}


# ⚠ `test_custom_edit_path_is_resolved_inside_the_novel_directory` 曾在这里，
# 直接测 `rename._edit_rel`（全仓唯一一条），只覆盖「书内绝对路径 / 目录外绝对路径」两类。
# 四份 `_edit_rel` 收敛成 `novel/paths.py::edit_rel` 之后，它的覆盖面并进
# `tests/test_paths.py` §五「四层 × 五类路径」——**只增不减**：
# 新增了相对路径按书目录解释（旧实现按 cwd，那正是 FN-016 记着的那条 bug）与 `..` 穿越两类，
# 且四层各跑一遍而不只是 stage7。


# ── ① 最长优先与恒等屏蔽 ───────────────────────────────────────────────────

def test_longest_first_is_by_sort_not_by_regex():
    """Python 的 alternation 是 **leftmost-first**，不是最长匹配。

    短的排前面会切出半截残串——`sub_table` 靠 `sorted(key=len, reverse=True)` 兜这件事。
    """
    pat, table = sub_table({'青禾': '晚棠', '孟青禾': '苏晚棠'})
    assert pat.findall('孟青禾') == ['孟青禾'], '短项抢走了长项的字节'
    assert table['孟青禾'] == '苏晚棠'


def test_kept_surface_shields_the_shorter_mapped_one():
    """⭐ **第一道防线是结构性的**：保留项进 alternation 当恒等条目。

    「阿禾」要换、「阿禾姊姊」要留 —— 不把后者编进正则，单次扫描会把它切成「阿舒姊姊」。
    """
    out, counts, _ = apply_subs(CH1, {'阿禾': '阿舒'}, kept=['阿禾姊姊'])
    assert '阿禾姊姊' in out, '保留项被更短的映射项切开了'
    assert out.count('阿舒') == 1 and counts['阿禾'] == 1


def test_no_chaining_a_to_b_to_c():
    """`A→B` 与 `B→C` 一起跑，A **不许**变成 C（`R-10.2`：级联在结构上不可表示）。"""
    out, _counts, _ = apply_subs('甲乙', {'甲': '乙', '乙': '丙'})
    assert out == '乙丙', '游标回头了 —— 这就是链式 replace 的形状'


def test_empty_subs_is_identity():
    assert apply_subs('原样', {}) == ('原样', {}, [])
    assert apply_subs('', {'甲': '乙'}) == ('', {}, [])


def test_generated_placeholder_is_not_mistaken_for_an_old_surface_residual():
    """门牌号 `001` 会合法地出现在 `char_001` 占位符里；只看裸字面会让整本假红。"""
    subs = {'001': '⟦char_001_00⟧'}
    after, _counts, claimed = apply_subs('门牌号001', subs)

    assert unclaimed_residual_keys(after, subs, claimed) == set()
    assert unclaimed_residual_keys(after + '，旧门牌001仍在', subs, claimed) == {'001'}


def test_claimed_covers_identity_entries_too():
    """⭐ `claimed` **必须含恒等条目认领的那一半**。

    原型最初只记「被替换的」那一半，**被测试当场抓住**：保留串里的子串
    会被残留判据算成残留，而那不是残留、是那条保留决定的直接后果。
    """
    out, _counts, claimed = apply_subs('阿禾姊姊', {'阿禾': '阿舒'}, kept=['阿禾姊姊'])
    assert out == '阿禾姊姊'
    assert claimed == [(0, 4)], '恒等条目没有认领它自己那一段'
    # 区间是**输出**坐标系的：换成更长的新名之后，后一段要相应右移
    _out2, _c2, claimed2 = apply_subs('阿禾和阿禾', {'阿禾': '苏阿舒'})
    assert claimed2 == [(0, 3), (4, 7)]


# ── ② 下发面的计数：绝不 `str.count` ──────────────────────────────────────

def test_surface_hits_are_scan_hits_not_str_count():
    """⭐⭐ **这个数下发给模型**，虚高会让它按错的轻重去分配注意力。

    实测某本「阿禾」`count`=4 而独立命中只有 1（另外 3 次在「阿禾姊姊」里）。
    """
    universe = {'阿禾姊姊', '阿禾', '孟青禾', '青禾'}
    hits = surface_hits(universe, [CH1])
    assert CH1.count('阿禾') == 2 and hits['阿禾'] == 1
    assert CH1.count('青禾') == 2 and hits['青禾'] == 1
    assert hits['阿禾姊姊'] == 1 and hits['孟青禾'] == 1
    assert surface_hits(set(), [CH1]) == {}


# ── ③ 残留探针：分母【故意】含嵌套 ────────────────────────────────────────

def test_residual_total_is_measured_before_and_keeps_nesting():
    """⭐ 分母量在 **pre**，而且**故意**用 `str.count`（单字姓字，嵌套本来就该算）。

    量在 post 上，改名之后旧姓字所剩无几，覆盖率会恒等于 0 —— 这个比率就不携带信息。
    """
    pre = [('ch1', '孟青禾和孟青兰在孟家。')]
    post = [('ch1', '苏晚棠和苏晚兰在孟家。')]
    residual, base = residual_scan(pre, post, {'孟'}, {'苏晚棠', '苏晚兰'})
    assert base['孟']['total'] == 3, '三处「孟」，含被替换掉的那两处'
    assert base['孟']['residual'] == 1 and base['孟']['covered'] == 2
    assert base['孟']['ratio'] == 0.67
    assert [r['hit'] for r in residual] == ['孟家。']
    assert residual[0]['confidence'] == 'high', '`<姓>家` 是结构信号'
    assert residual[0]['unit'] == '孟家' and residual[0]['suggest'] is None


def test_residual_high_uses_longest_appellation_suffix_and_split_canonical():
    residual, _base = residual_scan(
        [('ch1', '温兄温公子与汤临')],
        [('ch1', '温兄、温公子见到汤、临')],
        {'温', '汤'},
        set(),
        {'温': '顾', '汤': '陆'},
        {'汤临': '陆景'},
    )
    by_unit = {row['unit']: row for row in residual}
    assert by_unit['温兄']['confidence'] == 'high'
    assert by_unit['温兄']['suggest'] == '顾兄'
    assert by_unit['温公子']['confidence'] == 'high'
    assert by_unit['温公子']['suggest'] == '顾公子'
    assert by_unit['汤、临']['confidence'] == 'high'
    assert by_unit['汤、临']['suggest'] == '陆、景'


def test_residual_split_canonical_accepts_any_gap_in_a_three_character_name():
    residual, _base = residual_scan(
        [('ch1', '温夏白温夏白温夏白')],
        [('ch1', '温夏、白，温、夏白，温、夏 白')],
        {'温'},
        set(),
        {'温': '顾'},
        {'温夏白': '顾清禾'},
    )
    by_unit = {row['unit']: row for row in residual}
    assert by_unit['温夏、白']['suggest'] == '顾清、禾'
    assert by_unit['温、夏白']['suggest'] == '顾、清禾'
    assert by_unit['温、夏 白']['suggest'] == '顾、清 禾'
    assert all(row['confidence'] == 'high' for row in by_unit.values())


def test_residual_common_words_stay_low():
    residual, _base = residual_scan(
        [('ch1', '温柔温度常常周围')],
        [('ch1', '温柔温度常常周围')],
        {'温', '常', '周'},
        set(),
        {'温': '顾', '常': '林', '周': '陆'},
    )
    assert residual
    assert all(row['confidence'] == 'low' for row in residual)


# ── 被打断的姓：第三个结构信号，**正例与反例同书** ──────────────────────────
#
# 两条字面都取自 `无情道第一她来自合欢宗` 的成稿：ch18「【每日一问，迟……等等」
# 是原作真姓被叫到一半（人最后靠**读正文**才补上 `迟……→苏……`），
# ch8「高岭之花。」的「花」同样是本书旧姓、同样后接标点，**但那是一句话说完了**。
# ⇒ 这两条必须落在不同的 confidence 上，否则这个信号不携带信息。

def test_an_interrupted_surname_is_high_but_a_finished_sentence_is_not():
    residual, _base = residual_scan(
        [('ch18', '【每日一问，迟……等等】'), ('ch8', '真正的高岭之花。他醉心于剑')],
        [('ch18', '【每日一问，迟……等等】'), ('ch8', '真正的高岭之花。他醉心于剑')],
        {'迟', '花'}, set(), {'迟': '苏', '花': '柳'},
    )
    by_stem = {row['stem']: row for row in residual}
    assert by_stem['迟']['confidence'] == 'high', '姓 + 成串省略号 ＝ 被打断的称呼'
    assert by_stem['迟']['unit'] == '迟……' and by_stem['迟']['suggest'] == '苏……'
    assert by_stem['花']['confidence'] == 'low', \
        '终止句号不是中断号——放宽这一条，high 这个可采纳面立刻变噪声'
    assert by_stem['花']['suggest'] is None


def test_a_single_mark_is_not_a_run_and_stays_low():
    """`……` 与 `——` 在中文里是成对的全角形；孤立一个更可能是排版噪声。"""
    residual, _base = residual_scan(
        [('ch1', '迟…了')], [('ch1', '迟…了')], {'迟'}, set(), {'迟': '苏'})
    assert [row['confidence'] for row in residual] == ['low']


def test_low_rows_carry_no_suggestion_because_only_high_can_be_adopted():
    """⭐ `RESIDUAL_ADOPTION_CONFIDENCE` 只有 `high`，`--adopt-residual` 也只读 high
    ⇒ **low 的 `suggest` 结构上没有消费者**，留着只会造噪声。

    这三条字面全是盘上真出现过的假阳，旧实现会给出「柳糕」「柳眼」「苏钝」。
    """
    residual, _base = residual_scan(
        [('ch1', '桂花糕'), ('ch2', '桃花眼'), ('ch3', '有些迟钝地')],
        [('ch1', '桂花糕'), ('ch2', '桃花眼'), ('ch3', '有些迟钝地')],
        {'花', '迟'}, set(), {'花': '柳', '迟': '苏'},
    )
    assert residual and all(row['confidence'] == 'low' for row in residual)
    assert all(row['suggest'] is None for row in residual)
    assert all(row['unit'] for row in residual), \
        '`unit` 要留着——人仍要看旧姓后面跟的是什么字，被砍掉的只是那条建议'


def test_overlapping_low_windows_merge_without_moving_the_denominator():
    """⭐⭐ **合并的是呈现行，不是计数。**

    「我迟迟未登灵网」里 `迟` 命中两次（i 与 i+1），旧实现报成两行
    （`迟迟未` + `迟未登`）——同一处要人读两遍。
    ⚠ 而 `baseline['迟'].residual` 是覆盖率的**分子**：它必须仍然是 2。
      把计数挪到合并之后，`ratio` 会静默变大，而那是一张人按覆盖率从上往下读的表。
    """
    pre = [('ch18', '只是因为我迟迟未登灵网')]
    residual, base = residual_scan(pre, pre, {'迟'}, set(), {'迟': '苏'})

    assert len(residual) == 1, '两个重叠窗口是同一处，只报一行'
    assert residual[0]['hit'] == '迟迟未登', '`hit` 撑到覆盖合并后的整段'
    assert base['迟']['residual'] == 2, '**分子不动**——合并只影响呈现'
    assert base['迟']['total'] == 2 and base['迟']['covered'] == 0


def test_high_rows_are_never_merged_because_adoption_reads_their_pairs():
    """`high` 行带着 `unit → suggest` 这一对，`--adopt-residual` 直接拿它写 `extra_subs`
    ⇒ 合并两行 high 等于伪造一对没人验算过的替换。"""
    pre = [('ch1', '孙家孙府')]
    residual, base = residual_scan(pre, pre, {'孙'}, set(), {'孙': '韩'})
    assert [(r['unit'], r['suggest']) for r in residual] == [('孙家', '韩家'),
                                                             ('孙府', '韩府')]
    assert base['孙']['residual'] == 2


# ── 「为什么不改」这一格的两侧 ──────────────────────────────────────────────

def _decision(**over) -> dict:
    return {'char_id': 'char_001', 'old_canonical': '迟折月', 'new_canonical': '苏暮雪',
            'renamed': True, 'not_renamed_reason': None, 'aliases': [], 'reason': '清冷',
            **over}


@pytest.mark.parametrize('bad', ['', '  ', '纯头衔，没有本名'])
def test_a_renamed_character_may_not_carry_a_reason_for_not_renaming(bad):
    """⭐ 载体收这半边。**空串与实质内容一视同仁**——盘上两本 flash 书
    `renamed=true` 的角色 7/7 是空串，而契约注释一直写着「必须是 null」。
    「没有这个字段」「是 null」「是空串」曾在盘上同时存在，谁也没在看。
    """
    with pytest.raises(ValidationError, match='not_renamed_reason'):
        NameDecision(**_decision(not_renamed_reason=bad))


def test_the_other_side_stays_with_the_info_judge_not_the_carrier():
    """⚠ `renamed=false` 却没写理由**不进载体**：拒收 ⇒ 本层唯一那次付费调用整个作废。
    那一侧由 `rn_no_reason`（info）报，这里只钉住「载体放它过」。"""
    art = NameDecision(**_decision(renamed=False, new_canonical='迟折月',
                                   not_renamed_reason=None))
    assert art.not_renamed_reason is None


def test_dropping_a_substantive_reason_is_reported_but_dropping_a_blank_is_not():
    """归一掉一个空串没有信息（不报）；归一掉实质内容说明模型误读了契约（要报）。"""
    def _rules(dropped):
        fs = checks.rename_findings(
            name_map={'characters': [_decision()], 'surname_map': {}},
            subs={}, problems=[], applied={'chapters': {}, 'hook': {'counts': {}}},
            residual=[], baseline={},
            dispatched_characters={
                'char_001': {'canonical': '迟折月', 'aliases': []},
            },
            pre_target='', source_text='', residual_keys=set(), stem_noise={},
            family_members={}, missing=[], invalids=[], post_units=[],
            # ⚠ `title_line` 非空是**必需**的：`rn_title_unver` 直接拿它当 detail
            #   （`checks.py::rename_findings`），而 `Finding.detail` 有 min_length。
            title_line='书名「测试书」→「测试书」：恒等', dropped_reason=dropped)
        return {f.rule for f in fs}

    assert 'rn_no_reason' not in _rules([]), '空串被归一掉时不该出声'
    hit = _rules(['char_001：纯头衔，没有本名'])
    assert 'rn_no_reason' in hit


#: 假 store 也必须有 `ctx.novel_dir`：成书的 rel 带书名，`rename.book_txt_rel(store)`
#: 从这里现算。⚠ **别在测试里手写那个文件名**——那就是第二个落点
#: （`docs/LESSONS.md` 第 21 条：守卫要调用生产那一个落点）。
_FAKE_CTX = SimpleNamespace(novel_dir=Path('FQ_测试书_42'))


class _AdoptionStore:
    def __init__(self, art, *, fail_write=False):
        self.ctx = _FAKE_CTX
        self.art = art
        self.writes = []
        self.archives = []
        self.events = []
        self.fail_write = fail_write

    def exists(self, rel):
        return rel == rename.REL

    def read_own(self, _model, rel):
        assert rel == rename.REL
        return self.art, b'original-map'

    def history_transaction(self, *_rels):
        return nullcontext()

    def archive_round(self, rel, *, keep):
        self.events.append('archive')
        self.archives.append((rel, keep))

    def write(self, rel, art):
        self.events.append('write')
        if self.fail_write:
            raise RuntimeError('injected write failure')
        self.writes.append((rel, art))

    def write_round(self, rel, art):
        self.archive_round(rel, keep=True)
        self.write(rel, art)


class _RebuiltMap(SimpleNamespace):
    def model_copy(self, *, update):
        values = vars(self) | update
        return _RebuiltMap(**values)


def _residual(*, unit, suggest, confidence='high'):
    return Residual(
        stem='温', where='ch1', hit='温兄。', context='温兄站在门外',
        confidence=confidence, unit=unit, suggest=suggest,
    )


def _adoption_harness(monkeypatch, residual, *, fail_write=False):
    from novel.llm import stage7_runs

    art = SimpleNamespace(
        residual=residual,
        extra_subs=[ExtraSub(old='沈家', new='林家', origin='human')],
        subs={'周兄': '陆兄'}, surname_map={'温': '顾'}, characters=[],
        provenance='old-provenance', approved='old-approval',
        book_txt='old-delivery', identity='identity-baseline',
    )
    store = _AdoptionStore(art, fail_write=fail_write)
    captured = {}
    retired = []

    monkeypatch.setattr(rename, 'ArtifactStore', lambda _ctx: store)
    monkeypatch.setattr(
        stage7_runs, 'commit_ownership', lambda _novel_dir: nullcontext())
    monkeypatch.setattr(rename, '_Upstreams', lambda _ctx, _store: object())
    def retire(_store):
        store.events.append('retire')
        retired.append(True)

    monkeypatch.setattr(rename, '_retire_book_txt', retire)
    monkeypatch.setattr(rename, '_judge', lambda *_args: [])
    monkeypatch.setattr(rename, '_report', lambda *_args: 'report')

    def fake_build(_ctx, _up, model_out, extra, provenance):
        captured.update(model_out=model_out, extra=extra, provenance=provenance)
        return _RebuiltMap(
            approved='stale', book_txt='stale', identity=None,
        ), [], None

    monkeypatch.setattr(rename, '_build', fake_build)
    ctx = SimpleNamespace(
        novel_dir=Path('synthetic-book'),
        rewrite_provenance=lambda provenance, *, by: (provenance, by),
    )
    return ctx, store, captured, retired


def test_adopt_residual_only_commits_unambiguous_uncovered_high_suggestions(monkeypatch):
    ctx, store, captured, retired = _adoption_harness(monkeypatch, [
        _residual(unit='温兄', suggest='顾兄'),
        _residual(unit='温柔', suggest='顾柔', confidence='low'),
        _residual(unit=None, suggest=None),
        _residual(unit='沈家', suggest='赵家'),       # already in extra_subs
        _residual(unit='周兄', suggest='赵兄'),       # already in derived subs
        _residual(unit='汤兄', suggest='陆兄'),       # conflicting suggestions
        _residual(unit='汤兄', suggest='赵兄'),
    ])

    rebuilt, report = rename.run_adopt_residual(ctx, 'high')

    assert report == 'report'
    assert captured['provenance'] == ('old-provenance', '--adopt-residual')
    # ⚠ 两条的 `origin` **必须不同**：`沈家` 是本来就在 `extra_subs` 里的那条（人手写的），
    #   `温兄` 是本次由探针建议、被这条命令批量采纳的 ⇒ `adopted`。
    #   旧断言两条都写 `human`，那是把「产物说假话」钉成了期望值。
    assert captured['extra'] == [
        {'old': '沈家', 'new': '林家', 'origin': 'human'},
        {'old': '温兄', 'new': '顾兄', 'origin': 'adopted'},
    ]
    assert rebuilt.approved is None and rebuilt.book_txt is None
    assert rebuilt.identity == 'identity-baseline'
    assert retired == [True]
    assert store.events == ['archive', 'write', 'retire']
    assert store.archives == [(rename.REL, True)]
    assert store.writes == [(rename.REL, rebuilt)]


def test_adopt_residual_with_no_safe_candidate_is_a_no_write_terminal_state(monkeypatch):
    ctx, store, _captured, retired = _adoption_harness(monkeypatch, [
        _residual(unit='温柔', suggest='顾柔', confidence='low'),
        _residual(unit='沈家', suggest='赵家'),
        _residual(unit='汤兄', suggest='陆兄'),
        _residual(unit='汤兄', suggest='赵兄'),
    ])

    with pytest.raises(TerminalState, match='没有可安全采纳'):
        rename.run_adopt_residual(ctx, 'high')

    assert store.archives == [] and store.writes == [] and retired == []


def test_adopt_residual_blocked_by_hard_findings_is_a_terminal_state_not_a_crash(
        monkeypatch):
    """⭐ 采纳后判出 hard ⇒ **退出码 2 的预期内终态**，不是崩。

    这条路此前抛 `GateBlocked` **却漏了它必填的 `invalid_path`** ⇒ 真走到这里得到的是
    `TypeError`，退出码 70「崩了，结论未知」——编排层会据此把「这是我们的 bug」
    报给人，而实际发生的只是「这份采纳结果不合格，什么都没动」。
    ⚠ 分类也错：什么都没落盘，盘上根本没有 `.INVALID` 可指 ⇒ 不是 `GateBlocked`。
    """
    ctx, store, _captured, retired = _adoption_harness(
        monkeypatch, [_residual(unit='温兄', suggest='顾兄')])
    monkeypatch.setattr(
        rename, '_judge',
        lambda *_args: [SimpleNamespace(rule='rn_ambiguous', findings=['阿禾'])])

    with pytest.raises(BlockingFindingsUnresolved) as blocked:
        rename.run_adopt_residual(ctx, 'high')

    assert blocked.value.rules == ('rn_ambiguous',)
    assert store.archives == [] and store.writes == [] and retired == []
    assert store.art.book_txt == 'old-delivery'


def test_adopt_residual_write_failure_keeps_live_delivery(monkeypatch):
    ctx, store, _captured, retired = _adoption_harness(
        monkeypatch,
        [_residual(unit='温兄', suggest='顾兄')],
        fail_write=True,
    )

    with pytest.raises(RuntimeError, match='injected write failure'):
        rename.run_adopt_residual(ctx, 'high')

    assert store.events == ['archive', 'write']
    assert store.writes == [] and retired == []
    assert store.art.book_txt == 'old-delivery'


class _Dumpable(SimpleNamespace):
    def model_dump(self, **_kwargs):
        return dict(vars(self))

    def model_copy(self, *, update):
        return _Dumpable(**(vars(self) | update))


class _AtomicStore:
    def __init__(self, art, *, rels, edit=None, fail_write=False, fail_archive=False,
                 input_freshness=None):
        self.ctx = _FAKE_CTX
        self.art = art
        self.rels = set(rels)
        self.edit = edit or _Dumpable()
        self.fail_write = fail_write
        self.fail_archive = fail_archive
        self.events = []
        self.writes = []
        self.archives = []
        self.input_freshness = input_freshness or SimpleNamespace(
            state='fresh', reason='match')
        self.seen_recorded_inputs = []

    def exists(self, rel):
        return rel in self.rels

    def read_own(self, _model, rel):
        assert rel in (rename.REL, rename._INVALID)
        return self.art, b'old-map'

    def read(self, _model, rel):
        assert rel == rename.EDIT
        return self.edit

    def freshness_of_inputs(self, rel, recorded):
        assert rel == rename.REL
        self.seen_recorded_inputs.append(recorded)
        return self.input_freshness

    def history_transaction(self, *_rels):
        return nullcontext()

    def archive_round(self, rel, *, keep):
        self.events.append('archive')
        if self.fail_archive:
            raise RuntimeError('injected archive failure')
        self.archives.append((rel, keep))

    def write(self, rel, art):
        self.events.append('write')
        if self.fail_write:
            raise RuntimeError('injected write failure')
        self.writes.append((rel, art))

    def write_round(self, rel, art):
        self.archive_round(rel, keep=True)
        self.write(rel, art)


def _atomic_harness(
        monkeypatch, *, rels, fail_write=False, fail_archive=False,
        blocking=False, input_freshness=None, recorded_inputs=None):
    from novel.llm import stage7_runs

    subject = 'a' * 64
    recorded_inputs = recorded_inputs or {
        rename.GLOBAL_GROUNDING: {'file': 'old-file', 'consumed': 'old-consumed'},
    }
    art = _Dumpable(
        surname_map={'温': '顾'}, characters=[], extra_subs=[],
        provenance=_Dumpable(inputs=recorded_inputs),
        approved=SimpleNamespace(subject_sha256=subject),
        book_txt='old-delivery', identity='identity-baseline',
    )
    store = _AtomicStore(
        art, rels=rels, edit=_Dumpable(character_patches=[]),
        fail_write=fail_write, fail_archive=fail_archive,
        input_freshness=input_freshness,
    )
    retired = []
    monkeypatch.setattr(rename, 'ArtifactStore', lambda _ctx: store)
    monkeypatch.setattr(
        stage7_runs, 'commit_ownership', lambda _novel_dir: nullcontext())
    monkeypatch.setattr(rename, '_Upstreams', lambda _ctx, _store: object())
    monkeypatch.setattr(rename, 'approval_subject_sha256', lambda _art: subject)
    monkeypatch.setattr(rename.paths, 'edit_rel', lambda *_args: rename.EDIT)
    monkeypatch.setattr(rename, 'merge_mapping_edit', lambda *_args: ({}, []))
    monkeypatch.setattr(rename, '_judge', lambda *_args: ['hard'] if blocking else [])
    monkeypatch.setattr(rename, '_blocking', lambda outcomes: bool(outcomes))
    monkeypatch.setattr(rename, '_report', lambda *_args: 'report')

    def fake_build(_ctx, _up, _model_out, _extra, _provenance):
        return _RebuiltMap(
            approved='stale', book_txt='stale', identity=None,
        ), [], None

    monkeypatch.setattr(rename, '_build', fake_build)

    def retire(_store):
        store.events.append('retire')
        retired.append(True)

    monkeypatch.setattr(rename, '_retire_book_txt', retire)
    ctx = SimpleNamespace(
        novel_dir='novel-dir',
        rewrite_provenance=lambda provenance, *, by: (provenance, by),
    )
    return ctx, store, retired


def _stage7_grounding_with(*, role: str, fate: str | None = None) -> dict:
    return {
        'entities': {
            'characters': {
                'char_001': {
                    'canonical': '沈清禾', 'role': role, 'fate': fate,
                    'address': [{'surface': '沈娘子'}],
                },
            },
            'relations': [],
        },
        'canon': {'setting': {'时代背景': '架空古代江南'}},
    }


def _input_freshness_between(before: dict, after: dict):
    key = rename.GLOBAL_GROUNDING
    recorded = {
        key: {
            'file': 'before-file',
            'consumed': prov.projection_fingerprint(stage7_grounding(before)),
        },
    }
    report = prov.freshness(
        recorded,
        file_now=lambda _key: 'after-file',
        consumed_now=lambda _key: prov.projection_fingerprint(stage7_grounding(after)),
    )
    return recorded, report


def test_recheck_revokes_approval_after_role_drift_and_assemble_stays_closed(monkeypatch):
    before = _stage7_grounding_with(role='县令之女')
    after = _stage7_grounding_with(role='商户之女')
    recorded, freshness = _input_freshness_between(before, after)
    assert (freshness.state, freshness.reason) == ('stale', 'consumed_changed')
    ctx, store, _retired = _atomic_harness(
        monkeypatch, rels={rename.REL}, input_freshness=freshness,
        recorded_inputs=recorded,
    )

    rebuilt, _report = rename.run_recheck(ctx)

    assert store.seen_recorded_inputs == [recorded]
    assert rebuilt.approved is None, '旧 role 下的批准不能由 provenance 重写洗成当前批准'
    with pytest.raises(ApprovalRequired, match='还没定稿'):
        assemble._require_mapping_current(ctx, store, rebuilt, SimpleNamespace())


def test_recheck_preserves_approval_when_only_an_unconsumed_field_changed(monkeypatch):
    before = _stage7_grounding_with(role='县令之女', fate='最终留在故乡')
    after = _stage7_grounding_with(role='县令之女', fate='最终远走他乡')
    recorded, freshness = _input_freshness_between(before, after)
    assert (freshness.state, freshness.reason) == ('fresh', 'match')
    ctx, store, _retired = _atomic_harness(
        monkeypatch, rels={rename.REL}, input_freshness=freshness,
        recorded_inputs=recorded,
    )
    old_approval = store.art.approved

    rebuilt, _report = rename.run_recheck(ctx)

    assert rebuilt.approved is old_approval


def test_identity_revokes_approval_after_role_drift_and_assemble_stays_closed(monkeypatch):
    before = _stage7_grounding_with(role='县令之女')
    after = _stage7_grounding_with(role='商户之女')
    recorded, freshness = _input_freshness_between(before, after)
    assert (freshness.state, freshness.reason) == ('stale', 'consumed_changed')
    ctx, store, retired = _atomic_harness(
        monkeypatch, rels={rename.REL}, input_freshness=freshness,
        recorded_inputs=recorded,
    )
    baseline = SimpleNamespace(roundtrip_ok=True)
    monkeypatch.setattr(rename, '_identity_baseline', lambda _up: baseline)

    got, _report = rename.run_identity(ctx)
    rebuilt = store.writes[-1][1]

    assert got is baseline
    assert store.seen_recorded_inputs == [recorded]
    assert rebuilt.approved is None and rebuilt.book_txt is None
    assert retired == [True]
    assert store.events == ['archive', 'write', 'retire']
    with pytest.raises(ApprovalRequired, match='还没定稿'):
        assemble._require_mapping_current(ctx, store, rebuilt, SimpleNamespace())


def test_identity_revokes_approval_when_old_inputs_are_unprovable(monkeypatch):
    ctx, store, retired = _atomic_harness(
        monkeypatch, rels={rename.REL},
        input_freshness=SimpleNamespace(state='unknown', reason='no_inputs'),
    )
    monkeypatch.setattr(
        rename, '_identity_baseline',
        lambda _up: SimpleNamespace(roundtrip_ok=True),
    )

    rename.run_identity(ctx)
    rebuilt = store.writes[-1][1]

    assert rebuilt.approved is None and rebuilt.book_txt is None
    assert retired == [True]


def test_identity_preserves_approval_when_only_an_unconsumed_field_changed(monkeypatch):
    before = _stage7_grounding_with(role='县令之女', fate='最终留在故乡')
    after = _stage7_grounding_with(role='县令之女', fate='最终远走他乡')
    recorded, freshness = _input_freshness_between(before, after)
    assert (freshness.state, freshness.reason) == ('fresh', 'match')
    ctx, store, retired = _atomic_harness(
        monkeypatch, rels={rename.REL}, input_freshness=freshness,
        recorded_inputs=recorded,
    )
    old_approval = store.art.approved
    monkeypatch.setattr(
        rename, '_identity_baseline',
        lambda _up: SimpleNamespace(roundtrip_ok=True),
    )

    rename.run_identity(ctx)
    rebuilt = store.writes[-1][1]

    assert rebuilt.approved is old_approval
    assert rebuilt.book_txt is None and retired == [True]
    assert store.events == ['archive', 'write', 'retire']


def test_recheck_write_failure_keeps_live_delivery(monkeypatch):
    ctx, store, retired = _atomic_harness(
        monkeypatch, rels={rename.REL}, fail_write=True,
    )

    with pytest.raises(RuntimeError, match='injected write failure'):
        rename.run_recheck(ctx)

    assert store.events == ['archive', 'write']
    assert store.writes == [] and retired == []
    assert store.rels == {rename.REL} and store.art.book_txt == 'old-delivery'


def test_recheck_archive_failure_keeps_map_and_live_delivery(monkeypatch):
    ctx, store, retired = _atomic_harness(
        monkeypatch, rels={rename.REL}, fail_archive=True,
    )

    with pytest.raises(RuntimeError, match='injected archive failure'):
        rename.run_recheck(ctx)

    assert store.events == ['archive']
    assert store.writes == [] and store.archives == [] and retired == []
    assert store.rels == {rename.REL} and store.art.book_txt == 'old-delivery'


def test_apply_edit_write_failure_keeps_live_delivery(monkeypatch):
    ctx, store, retired = _atomic_harness(
        monkeypatch, rels={rename.REL, rename.EDIT}, fail_write=True,
    )

    with pytest.raises(RuntimeError, match='injected write failure'):
        rename.run_apply_edit(ctx)

    assert store.events == ['archive', 'write']
    assert store.writes == [] and retired == []
    assert store.rels == {rename.REL, rename.EDIT}
    assert store.art.book_txt == 'old-delivery'


def test_apply_edit_invalid_hard_keeps_live_delivery_and_invalid_map(monkeypatch):
    ctx, store, retired = _atomic_harness(
        monkeypatch, rels={rename._INVALID, rename.EDIT}, blocking=True,
    )

    rebuilt, report = rename.run_apply_edit(ctx)

    assert rebuilt.approved is None and rebuilt.book_txt is None
    assert report == 'report'
    assert store.events == []
    assert store.writes == [] and store.archives == [] and retired == []
    assert store.rels == {rename._INVALID, rename.EDIT}
    assert store.art.book_txt == 'old-delivery'


def test_residual_reports_its_baseline_even_with_nothing_left():
    """判据 31：**残留为 0 时也要输出这四个数。**

    没有分母，「residual 只有 4 条」读不出是探针管用还是探针瞎了。
    """
    residual, base = residual_scan([('ch1', '孟青禾')], [('ch1', '苏晚棠')],
                                   {'孟'}, {'苏晚棠'})
    assert residual == []
    assert base == {'孟': {'total': 1, 'covered': 1, 'residual': 0, 'ratio': 1.0}}


def test_a_stem_that_is_not_in_the_source_gets_a_null_ratio_not_zero():
    """⭐ `total == 0` ⇒ `ratio` 是 `null` **不是 `0.0`**（`Q-2` 的三态）。

    压成 0.0 会被读成「覆盖率 0%」——在这张**按覆盖率排序**的表上，
    一个原文里压根不存在的姓会被摆到「灾难性漏网」那一端。
    """
    _residual, base = residual_scan([('ch1', '苏晚棠')], [('ch1', '苏晚棠')],
                                    {'霍'}, {'苏晚棠'})
    assert base['霍'] == {'total': 0, 'covered': 0, 'residual': 0, 'ratio': None}


def test_residual_order_is_load_bearing():
    """顺序 = `(confidence != 'high', -ratio, stem, where)`，**是契约不是装饰**。

    设计口径是「人从上往下读到覆盖率掉下去为止」。
    """
    pre = [('ch1', '沈家沈家沈家沈佐'), ('ch2', '许久许久不允许')]
    post = [('ch1', '沈家沈家沈家林佐'), ('ch2', '许久许久不允许')]
    residual, base = residual_scan(pre, post, {'沈', '许'}, {'林佐'},
                                   surname_map={'沈': '林'})
    assert base['沈']['ratio'] == 0.25 and base['许']['ratio'] == 0.0
    # `沈` 覆盖率**更低**，而它排在最前——因为 `confidence` 是结构信号，压过比率
    assert [r['stem'] for r in residual][:3] == ['沈', '沈', '沈']
    assert residual[0]['confidence'] == 'high' and residual[0]['suggest'] == '林家'
    assert all(r['confidence'] == 'low' for r in residual if r['stem'] == '许')


def test_mask_known_is_length_preserving():
    """等长替换是为了让报出来的位置**还对得上原文**。"""
    masked = mask_known('孟青禾走了', {'孟青禾'})
    assert len(masked) == len('孟青禾走了') and '孟' not in masked
    assert mask_known('原样', set()) == '原样'
