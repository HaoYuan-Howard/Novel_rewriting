#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""VAL-02 的守卫：stage4/6/7 的每条 HARD 都要有一个**真的会红**的最小样本。

一条「据说会拦住 X」的 HARD，如果没有一个反例证明它真的会拦，
它与「只写在注释里的约束」没有区别（LESSONS #1/#2）。

新增一条 HARD 而没在 `tests/hard_cases.py` 登记反例会当场红。
"""
from __future__ import annotations

import pytest

from contracts.schema.common import Finding
from tests.hard_cases import CASES

_STAGES = ('stage4', 'stage6', 'stage7')


def _hard_rules() -> dict[str, str]:
    from contracts.rules import REGISTRY
    return {r.rule: r.stage for r in REGISTRY.all()
            if r.sev == 'hard' and r.stage in _STAGES}


def test_every_hard_rule_of_stage4_6_7_has_a_counterexample():
    """两个方向同时钉住：漏登记 ⇒ 红（守卫的本体）；多登记 ⇒ 红（表在点名死人）。"""
    hard = _hard_rules()
    assert hard, '三层一条 HARD 都没有——判据表多半没被 import'
    missing = sorted(set(hard) - set(CASES))
    assert not missing, (
        f'这些 HARD 没有登记反例：{missing}。'
        f'⇒ 在 tests/hard_cases.py 里补一个调用真实判据入口的最小会红样本')
    stray = sorted(set(CASES) - set(hard))
    assert not stray, f'CASES 点名了不存在/非 HARD 的判据：{stray}——判据改名或退役要跟着改这张表'
    wrong_stage = sorted(r for r, c in CASES.items() if c.stage != hard[r])
    assert not wrong_stage, f'这些样本登记的层与判据表不符：{wrong_stage}'


@pytest.mark.parametrize('rule', sorted(CASES))
def test_each_counterexample_actually_reddens_exactly_its_target_rule(rule):
    """逐条验证：样本喂进**真实判据入口**后，目标判据真的产出了 `Finding`。

    ⚠ 只断言目标判据命中，不断言「不溅射」——部分判据共享前置，
    强断言不溅射会把构造成本翻倍；最小性靠 hard_cases 里的自律。
    产出的必须是真 `Finding`（走了 `finding()` 工厂 ⇒ sev/owner 与注册表一致由载体保证）。
    """
    findings = CASES[rule].build()
    assert findings, f'{rule} 的样本一条结论都没产出来'
    assert all(isinstance(f, Finding) for f in findings), \
        f'{rule} 的样本产出了非 Finding 对象——没走真实判据入口'
    got = {f.rule for f in findings}
    assert rule in got, f'{rule} 的样本没让目标判据变红（只命中了 {sorted(got)}）'


# ─────────── 一条 HARD 的多个判定分支：只红过一支不算见过红灯 ───────────
#
# 上面那张表是 **判据 → 一个样本** 的 1:1 表，它证明的是「这条 HARD 会红」。
# 但 `structure_units` 的 `if` 有**四支**（`novel/stages/stage4/checks.py:187-195`）：
#   ①重复引用 ②组内有 beat 没出现 ③一个源单元被拆到两个写作单元 ④组内换序
# 表里的样本只走 ①，② 与 `beat_coverage` 重叠，而 **③④ 从未见过红灯**——
# 它们与「恒不红」在报告上长得一模一样（LESSONS 第 2 条）。
#
# ⚠ 这两支同时是 `R-5.6` 里**唯一没被别处兜住**的那半：`R-5.5` 把「输出写作单元的
# 分组与顺序」交给了 LLM，所以「源内容单元整体进入同一个写作单元、内部不被二次切碎」
# 只剩这条 HARD 一个守卫。

def _unit_plan(*segments_from_beats: tuple[str, ...]) -> set[str]:
    """按给定摆法走**真实判据入口**，返回命中的判据名集合。"""
    from tests.hard_cases import _MAIN4, _BPS4_UNIT, _b4, _bidx4_unit, _ch4, _plan4

    findings = _plan4([_ch4(1, [_b4(*g) for g in segments_from_beats])],
                      blueprints=_BPS4_UNIT, main_text=[_MAIN4[0]], bidx=_bidx4_unit())
    return {f.rule for f in findings}


def test_the_unit_fixture_really_produces_a_multi_beat_source_unit():
    """⭐ **守卫的守卫：合并没发生的话，下面两条打的是空气，而它们会静静地绿。**

    ③④ 都要求「一个源内容单元里有不止一个 beat」。`BEAT_MIN_CHARS` 一改、
    或吸收规则一动，`_bidx4_unit()` 造的靶子就散成三个单元素组——那时候
    ③④ 恒不命中，**看起来和「这两支查过、没问题」完全一样**。
    ⇒ 把靶子本身钉死在这里。
    """
    from novel.stages.stage4.beats import group_beats
    from tests.hard_cases import _MAIN4, _BPS4_UNIT, _bidx4_unit

    assert group_beats(_BPS4_UNIT, [_MAIN4[0]], _bidx4_unit()) == {
        1: [['b1_01', 'b1_02'], ['b1_03']]}, '靶子散了：下面两条分支测试将不再打得中任何东西'


def test_a_source_unit_split_across_two_writing_units_reddens_structure_units():
    """③ **一个源内容单元被拆到两个写作单元里** —— 事实守恒边界被切开。

    `b1_01`/`b1_02` 是代码判定的同一个不可再拆单元；把它们摆进两个 segment
    就是让写手在两处分别落笔，而它们之间的因果由代码保证过「不能拆」。
    """
    assert 'structure_units' in _unit_plan(('b1_01',), ('b1_02', 'b1_03'))


def test_reordering_inside_a_source_unit_reddens_structure_units():
    """④ **组内换序** —— 单元之间怎么排是 LLM 的自由（`R-5.5`），单元**之内**不是。

    ⚠ 这一条与 `structure_changed` 方向相反而不冲突：那条要求**单元之间**必须重排，
    这条要求**单元之内**一个字不许动。两者管的是不同的粒度。
    """
    assert 'structure_units' in _unit_plan(('b1_02', 'b1_01'), ('b1_03',))


def test_the_same_units_kept_whole_and_in_order_do_not_redden_it():
    """⭐ **绿对照**：同一份材料、合法摆法 ⇒ 这条判据不响。

    没有它，上面两条的红可能来自材料本身坏掉（比如 beat 编号写错、蓝图不合法），
    而那种红与「分支真的被触发了」在断言上同形。
    """
    assert 'structure_units' not in _unit_plan(('b1_01', 'b1_02'), ('b1_03',))


# ─────────── 红了还不够：**病因也要说对**（TODO-10b） ───────────
#
# 一条 HARD 亮起时附的那句 detail 就是人拿到的**全部**线索。说错了病因，
# 人会去查一段根本没问题的代码——那不是「少了点信息」，是**制造了第二个 bug**（判据 32）。

def _para_sep_detail(body: str, src_hook: str) -> str:
    from tests.hard_cases import _hook

    fs = [f for f in _hook(body, src_hook=src_hook) if f.rule == 'hook_para_sep']
    assert len(fs) == 1, f'期望恰好一条 hook_para_sep，拿到 {len(fs)}'
    return fs[0].detail


def test_a_single_paragraph_hook_is_not_blamed_on_normalization():
    """⭐ **单段文案：归一化修不掉，所以不能说「归一没跑或坏了」。**

    `normalize_paragraphs` 是 `sep.join(按行切开的非空行)` ——
    它换得了分隔符，**造不出段落边界**。模型回一整段密排正文（一个 `\n` 都没有）时
    `lines` 长度为 1，join 出来还是它自己，`changed=False`；
    于是这条 HARD 亮起，而此前它附的诊断是「归一化应该已经修掉它了，
    这条亮了说明归一没跑或坏了」——**把人支去查一个根本没问题的归一化**。

    这条 bug 在本仓被记过三次（`tests/test_stage6.py::_sample` 的注释、
    skill 现场记录 FN-008、两份真实跑书的 final_review），一直没修。
    """
    detail = _para_sep_detail(
        '她站在门口手里攥着那张纸不知道该不该推门进去风把她的头发吹乱了。',
        '第一段。\n\n第二段。')
    assert '一整段' in detail and '归一化修不掉' in detail, \
        f'单段文案拿到的仍是旧诊断：{detail}'
    assert '归一没跑或坏了' not in detail, '单段文案不该被归咎于归一化'


def test_a_multi_paragraph_hook_with_the_wrong_separator_still_blames_normalization():
    """⚠ 反面：多段但分隔符不对，**归一化确实修得掉** ⇒ 旧措辞对这一类是对的。

    只按「档位不同」一刀切成新措辞，就把另一半真病因也说错了——
    两类病因处方相反（重出/人工分段 vs 查归一化），必须分开。
    """
    detail = _para_sep_detail(
        '第一段落的文字要足够长一点。\n第二段落的文字也足够长一点。',
        '第一段。\n\n第二段。')
    assert '归一没跑或坏了' in detail, f'多段样本的诊断被改错了：{detail}'
    assert '一整段' not in detail
