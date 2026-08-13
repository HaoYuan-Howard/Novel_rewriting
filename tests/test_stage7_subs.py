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

import shutil
import tempfile
from pathlib import Path
from types import SimpleNamespace

import pytest

from novel.errors import TerminalState
from novel.stages.stage7 import checks, rename
from novel.stages.stage7.subs import (
    apply_subs, mask_known, residual_scan, sub_table, surface_hits,
)

# 「阿禾」⊂「阿禾姊姊」· 「青禾」⊂「孟青禾」——嵌套是本层全部风险的来源。
CH1 = '阿禾姊姊回来了。孟青禾站在门口，青禾轻轻应了一声，阿禾没有回头。'


def test_generic_appellation_wrapper_may_keep_label_and_replace_embedded_name():
    """真实 E2E：`女主方衡→女主沈凌` 只改真名，不是把「女主」关系改掉。"""
    subs = {'女主方衡': '女主沈凌', '方衡': '沈凌'}
    assert not checks._generic_appellation_was_touched(
        '女主方衡', '女主沈凌', subs, {'方'})
    assert checks._generic_appellation_was_touched(
        '姐姐', '妹妹', {'姐姐': '妹妹'}, set())
    assert checks._generic_appellation_was_touched(
        '女主方衡', '女主沈凌', {'女主方衡': '女主沈凌'}, {'方'}), \
        '没有内嵌真名映射作证时仍要保守拦截'


def test_rename_semantic_marker_finding_catches_rank_change():
    kwargs = dict(
        name_map={'characters': [], 'surname_map': {}},
        problems=[], applied={}, residual=[], baseline={}, registry={},
        pre_target='', source_text='', residual_keys=set(), stem_noise={},
        family_members={}, dup=[], missing=[], mixed=[], invalids=[],
        post_units=[], title_line='书名语义需人工确认',
    )
    changed = checks.rename_findings(subs={'七郎': '六郎'}, **kwargs)
    kept = checks.rename_findings(subs={'阿九': '玄九'}, **kwargs)
    assert 'rn_semantic_marker_changed' in {finding.rule for finding in changed}
    assert 'rn_semantic_marker_changed' not in {finding.rule for finding in kept}


def test_custom_edit_path_is_resolved_inside_the_novel_directory():
    """`--apply-edit` 的自定义路径不能靠 `store.path('')` 取得根目录。

    路径安全层明确拒绝空相对路径；那种写法会让每一次自定义编辑都在读文件前结构性失败。
    """
    root = Path(tempfile.mkdtemp())
    try:
        novel_dir = root / '书名_123'
        edit = novel_dir / 'stage7' / 'name_map_edit.json'
        store = SimpleNamespace(ctx=SimpleNamespace(novel_dir=novel_dir))
        assert rename._edit_rel(store, str(edit)) == 'stage7/name_map_edit.json'
        with pytest.raises(TerminalState, match='产物目录之外'):
            rename._edit_rel(store, str(root / 'outside.json'))
    finally:
        shutil.rmtree(root, ignore_errors=True)


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
