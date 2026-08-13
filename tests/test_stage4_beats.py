#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
test_stage4_beats.py —— 拍的确定性切分与篇幅量纲。

⭐ **本文件最值钱的一条是 `test_the_grouping_reproduces_every_plan_on_disk`**：
拿 stage2 骨架 + stage1 正文重跑 `group_beats`，产出的分组与盘上三份计划的
`from_beats` **逐组相同**。它一次钉住 span 定位（`novel/core/spans.py`）
与吸收算法（`beats.group_beats`）——**对位 stage7 那条「重拼的 book.txt 逐字节相同」**。
不一致就是骨架抄错了，不是数据变了：那三份产物一个字节都没动过。

⚠ 盘上那本 `成年人不做选择` 的计划**载体拒收**（缺 `_provenance`，
`tests/corpus.py::MISSING_PROVENANCE` 登记在案），所以本文件一律走 `load_raw`：
**确定性核心吃 dict 不吃模型**，那本才进得了对账
（理由见 `novel/stages/stage4/__init__.py` ③）。
"""
from __future__ import annotations

import pytest

from contracts.invariants import BEAT_MIN_CHARS, LOCKED_OP, OP_BAND
from novel.core.spans import content_only, has_spans, locate_spans
from novel.core.text import norm
from novel.errors import RefusedByStructure
from novel.stages.stage4.beats import (
    beat_index, group_beats, group_chars, group_map, plan_beat_id, verbatim_pieces,
)
from novel.stages.stage4.measure import (
    IDENTITY_GOAL, chapter_source_chars, expand_plan_beats, identity_plan,
    source_chars_table, verbatim_chars, verbatim_share_stats,
)
from tests import corpus


def _by_rel(rel: str) -> dict[str, object]:
    return {a.source_id: corpus.load_raw(a) for a in corpus.iter_artifacts() if a.rel == rel}


MAIN = _by_rel('stage1/main_text.json')
BPS = _by_rel('stage2/chapter_blueprints.json')
PLANS = _by_rel('stage4/plan_chapters.json')
BOOKS = sorted(set(MAIN) & set(BPS) & set(PLANS))

needs_corpus = pytest.mark.skipif(not BOOKS, reason='盘上没有语料——本族对账无从谈起')


# ── ⭐ 一、拍分组 == 存盘 ──────────────────────────────────────────────────

@needs_corpus
def test_the_grouping_reproduces_every_plan_on_disk():
    """⭐⭐ **本层最值钱的一条免费等价测试。**

    盘上那三份计划的 `from_beats` 是**原型**的 `group_beats` 切出来的。
    本仓重切一遍，逐组比对：**197 拍 / 251 条引用，一条不差。**

    它抓的是「逐字移植时少一个前瞻、错一个字符类」那种错——
    那种错不会报错，只会让某几拍并得不一样，而 `beat_seq` / `beat_adjacent` /
    `beat_layer` 三条 HARD 是拿**当前**这个函数去量的 ⇒ 它们会跟着一起错，
    **两边同时错成一样，谁也不会出声**。
    """
    total_beats = total_refs = 0
    for sid in BOOKS:
        groups = group_beats(BPS[sid], MAIN[sid])
        for o in (PLANS[sid].get('output_chapters') or []):
            oo = o.get('output_ordinal')
            stored = [list(be.get('from_beats') or []) for be in (o.get('beats') or [])]
            assert [list(g) for g in groups.get(oo, [])] == stored, (
                f'{sid} 第 {oo} 章的拍与盘上不一致\n  算: {groups.get(oo)}\n  盘: {stored}')
            total_beats += len(stored)
            total_refs += sum(len(x) for x in stored)
    assert (total_beats, total_refs) == (197, 251), (
        f'盘上是 197 拍 / 251 条引用，这次数到 {total_beats} / {total_refs}——'
        f'语料变了还是扫描面变窄了？')


@needs_corpus
def test_every_span_locates_and_nothing_needed_normalizing():
    """span 定位 **251/251**，`problems` 空，且 `loose` 恒 0。

    `loose` 不是统计口味：stage2 落盘时会把锚点回取成原文字节，
    跑过那一步之后它**应当恒为 0**——非 0 即「锚点被改过而未回取」。
    """
    located = 0
    for sid in BOOKS:
        bps = BPS[sid]['blueprints']
        assert has_spans(bps)
        text = {m['chapter_ordinal']: (m.get('text') or '') for m in MAIN[sid]}
        spans, problems, loose = locate_spans(bps, text)
        assert not problems, f'{sid} 有锚点定位不到：{problems[:3]}'
        assert loose == 0, f'{sid} 有 {loose} 个锚点归一化之后才命中——它们没被回取'
        located += sum(len(v) for v in spans.values())
    assert located == 251


@needs_corpus
def test_the_span_baseline_is_not_the_whole_chapter():
    """⚠ **span 合计 ≠ 整章字数**，两者是本层三个「源字数」口径里的两个。

    实测 span 合计占正文 **97.2%~98.2%**。拿整章字数当预算带的基准，
    产地已经判死过一份合格的计划一次。
    """
    for sid in BOOKS:
        bidx = beat_index(BPS[sid], MAIN[sid])
        chars, cov = chapter_source_chars(MAIN[sid], bidx)
        assert all(g == t for g, t in cov.values()), f'{sid} 有拍算不出 span'
        span_total = sum(chars.values())
        body = sum(len(m.get('text') or '') for m in MAIN[sid])
        assert span_total < body, 'span 合计不该 ≥ 整章字数'
        assert 0.95 < span_total / body < 1.0, (
            f'{sid} 的 span 占比 {span_total / body:.1%} 落到实测区间之外了')


# ── 二、`content_only` 与 `norm` 不是同一个东西 ───────────────────────────

def test_content_only_is_not_norm_and_must_not_be_merged():
    """⚠⚠ **两个内容过滤器长得几乎一样，而合并会静默改动 span 定位。**

    盘上三本实测**逐字节相同**（31/31 章 · 502/502 个锚点），
    所以「顺手合并成一个」在这份语料上**一条测试都不会红**——
    本条就是为此存在的：它测的是**取值面的分歧**，不是这三本的巧合。

    `content_index` 的 `cmap` 是**下标映射**：少认两个内容字，
    后面每一条锚点的原文偏移全错，而**它不会报错**，
    只会让某几拍的 `source_chars` 变成另一个数。
    """
    assert content_only('第Ａ章０１') == '第Ａ章０１', '全角字母数字是内容字'
    assert norm('第Ａ章０１') == '第章', '而 norm 把它们剥掉'
    for s in ('ｉＰｈｏｎｅ１３', '喵_喵', 'ですます', 'café'):
        assert content_only(s) != norm(s), f'{s!r} 上两者必须分歧'
    # 三本语料上它们恰好一致——**那是这三本的性质，不是这两个正则的性质**
    for sid in BOOKS:
        for m in MAIN[sid]:
            t = m.get('text') or ''
            assert content_only(t) == norm(t), (
                '这份语料上两者本该一致；不一致说明语料里出现了新字类，'
                '而那正是「别把它们合并」的理由变成现实的时刻')


# ── 三、吸收算法的三条不变量 ─────────────────────────────────────────────

def _bidx(sizes: dict[str, int | None], layers: dict[str, str] | None = None):
    return {b: {'ord': 1, 'source_chars': n, 'hinges': [], 'event': '',
                'time_layer': (layers or {}).get(b, 'present'), 'place': None,
                'present_char_ids': []}
            for b, n in sizes.items()}


def _bps(ids: list[str], layers: dict[str, str] | None = None):
    return {'blueprints': [{'chapter_ordinal': 1,
                            'beats': [{'beat_id': b, 'time_layer': (layers or {}).get(b, 'present')}
                                      for b in ids]}]}


def test_a_beat_whose_size_is_unknown_stays_alone():
    """⭐ **`source_chars` 未知的拍独立成组，绝不被吸收。**

    span 锚不到时它的字数是**「不知道」，不是 0**。并进邻居会让组预算看起来正常，
    于是「stage2 那一拍锚点坏了」这个真信号**被一次合并悄悄掩盖掉**。
    ⚠ 它**也不参与「哪一侧更小」的比较**——未知不能拿去比大小。
    """
    ids = ['b01_01', 'b01_02', 'b01_03']
    bidx = _bidx({'b01_01': 300, 'b01_02': None, 'b01_03': 10})
    got = group_beats(_bps(ids), [], bidx)[1]
    assert ['b01_02'] in got, '未知的那一拍被吸收了——真信号就此消失'
    assert group_chars(['b01_02'], bidx) is None
    assert group_chars(['b01_01', 'b01_02'], bidx) is None, '任一条未知 ⇒ 整组未知'


def test_absorption_never_crosses_a_narrative_layer():
    """跨叙述层并拍 ⇒ 写手在一拍之内分不清哪句是现场、哪句是回忆。"""
    ids = ['b01_01', 'b01_02', 'b01_03']
    layers = {'b01_01': 'present', 'b01_02': 'recall', 'b01_03': 'present'}
    bidx = _bidx({'b01_01': 300, 'b01_02': 10, 'b01_03': 300}, layers)
    got = group_beats(_bps(ids, layers), [], bidx)[1]
    assert ['b01_02'] in got, '碎拍两侧都不同层 ⇒ 它只能保持独立，哪怕它很碎'


def test_absorption_merges_into_the_smaller_side_and_keeps_source_order():
    """同层两侧都可并时取**较小**的那一边（同大取左，保证确定），且**永不换位**。"""
    ids = ['b01_01', 'b01_02', 'b01_03']
    bidx = _bidx({'b01_01': 500, 'b01_02': 10, 'b01_03': 100})
    got = group_beats(_bps(ids), [], bidx)[1]
    assert got == [['b01_01'], ['b01_02', 'b01_03']], '并进了大的那一侧，或者顺序动了'
    assert [b for g in got for b in g] == ids, '展平之后必须恒等于源序'


def test_a_big_enough_beat_is_never_touched():
    """**够大的拍一条不动**——这样才保住原文自己的轻重，不把一章削成一排等长的拍。"""
    ids = ['b01_01', 'b01_02']
    bidx = _bidx({b: BEAT_MIN_CHARS for b in ids})
    assert group_beats(_bps(ids), [], bidx)[1] == [['b01_01'], ['b01_02']]


def test_plan_beat_ids_use_p_not_b():
    """`p` 前缀与 stage2 的 `b` 分开——**同名不同义**是最好的漂移温床。"""
    assert plan_beat_id(1, 2) == 'p01_02'
    assert group_map({3: [['b03_01', 'b03_02']]}) == {'p03_01': ['b03_01', 'b03_02']}


# ── 四、hinge 拆点 ────────────────────────────────────────────────────────

def test_a_cross_paragraph_hinge_becomes_several_points():
    """跨段落的一条 hinge **就是一个「段」而不是一个「点」**。

    实测某章 4 条 verbatim 全部跨段落，成稿把它们**一条不剩地丢光**——
    写手要么连原文的段落结构一起复现，要么整组放弃，而它选了后者。
    """
    out, unknown = verbatim_pieces([{'kind': '要害台词', 'text': '第一句。\n\n第二句。'}])
    assert out['verbatim'] == ['第一句。', '第二句。']
    assert not unknown


def test_formal_hinge_source_is_the_material_stage4_consumes():
    hinge = {
        'kind': '要害台词',
        'source': {
            'text': '原样材料。', 'start': 4, 'end': 9,
            'normalized': '原样材料', 'recovery': 'exact',
        },
    }
    out, unknown = verbatim_pieces([hinge])
    assert out == {'verbatim': ['原样材料。']}
    assert unknown == []


def test_an_unknown_hinge_kind_is_dropped_not_upgraded():
    """⚠ **未知 kind 丢弃 + 告警，不许兜底。**

    曾经是 `HINGE_GROUPS.get(kind, 'verbatim')`，于是模型自造的 kind（实测「行动描述」）
    被**升级**成「原样写入，一个字都不能改」——而那是一句叙述文。
    """
    out, unknown = verbatim_pieces([{'kind': '行动描述', 'text': '她走进了屋子。'}])
    assert out == {} and unknown == [('行动描述', '她走进了屋子。')]


def test_verbatim_chars_counts_only_the_verbatim_group():
    """另外三组允许换措辞甚至不出现在文面 —— **它们压得动，不算不可压缩量**。"""
    hs = [{'kind': '要害台词', 'text': '你别走。'},
          {'kind': '硬数字', 'text': '二十两银子'},
          {'kind': '因果前提', 'text': '她已经知道了'}]
    assert verbatim_chars(hs) == len('你别走。')


# ── 五、量纲与分布 ────────────────────────────────────────────────────────

@needs_corpus
def test_every_chapter_budget_falls_inside_the_strict_band():
    """⚠ **严格带（0.90~1.10），没有第二次宽限。**

    原型在带之外又乘了一次 ±10%，实际执行 0.81~1.21×。实测三本逐章比
    0.901~1.058 / 0.910~0.969 / 0.994~1.049 —— **宽限那一段 0 样本**。
    ⚠ 但去掉它不是零风险：最窄那一章 **0.901**，离下界只差 0.1%。
    """
    lo, hi = OP_BAND[LOCKED_OP]
    tightest = 9.9
    for sid in BOOKS:
        bidx = beat_index(BPS[sid], MAIN[sid])
        chars, _cov = chapter_source_chars(MAIN[sid], bidx)
        for o in PLANS[sid]['output_chapters']:
            src = sum(chars.get(s, 0) for s in o['from_source_ordinals'])
            tot = sum(b['budget_chars'] for b in o['beats']
                      if isinstance(b.get('budget_chars'), int))
            assert lo <= tot / src <= hi, f'{sid} 第 {o["output_ordinal"]} 章 {tot / src:.3f} 出带'
            tightest = min(tightest, tot / src - lo)
    assert tightest < 0.01, (
        '最窄那一章离下界应当只差 0.1% —— 这个余量变大了说明语料或口径动过')


@needs_corpus
def test_the_verbatim_share_distribution_matches_the_measured_one():
    """度量**无条件报出**，而它的值逐本可复算（`R-5.14`）。

    ⚠ 这里钉的是**实测原值**，不是文档里那个四舍五入过的百分数：
    `stage4.md` 写着「max 44% / 28% / 92%」，而真值是 `0.438 / 0.28 / 0.923`。
    拿显示用的圆整值当断言，本条第一次跑就会红——**而红的是测试不是实现**。
    """
    # 章数 → (带原话的拍数, 中位, p90, 最高)
    want = {14: (51, 0.079, 0.188, 0.438),
            8: (41, 0.106, 0.227, 0.28),
            9: (45, 0.14, 0.35, 0.923)}
    for sid in BOOKS:
        bidx = beat_index(BPS[sid], MAIN[sid])
        stats, over = verbatim_share_stats(PLANS[sid], bidx)
        n_ch = len(PLANS[sid]['output_chapters'])
        assert stats is not None
        assert stats['threshold'] == 0.5, '阈值是 1/2.0，与 `VERBATIM_BUDGET_FACTOR` 同源'
        got = (stats['n'], stats['median'], stats['p90'], stats['max'])
        assert got == want[n_ch], f'{sid} 的分布变了：{got}'
        # `verbatim_starved`：只有一本命中一处（60 字原话 / 65 字预算）
        assert over == ([(8, 10, 60, 65)] if n_ch == 9 else [])


@needs_corpus
def test_the_table_fed_to_the_planner_is_per_beat_not_per_source_beat():
    """规划者手里的单位是**拍**，那张表必须与它对齐。"""
    sid = BOOKS[0]
    bidx = beat_index(BPS[sid], MAIN[sid])
    table = source_chars_table(group_beats(BPS[sid], MAIN[sid], bidx), bidx)
    assert table and 'p01_01=' in table and '共' in table
    assert 'b01_01=' not in table, '按源 beat 报了——那样「本章源字数」这个加法就落回模型手里'


# ── 六、落盘前的确定性改写：三种违约一律显式终态 ─────────────────────────

def test_an_empty_plan_says_so_in_one_sentence():
    """⚠ **先把「一章都没产」单独说出来**，不摊成 N 条派生症状。

    实测某次模型只返回了 `reconstruction_strategy`，而报出来的是
    「**74 处 `plan_beat` 对不上**」——74 条症状盖住了一句话就能说清的事实，
    而症状的文案指向的是另一个根因。
    """
    with pytest.raises(RefusedByStructure, match='一个输出章都没有'):
        expand_plan_beats({'output_chapters': []}, {1: [['b01_01']]})


@pytest.mark.parametrize('bad, why', [
    ([{'plan_beat': 'p09_09'}], '编号不存在'),
    ([{'plan_beat': 'p01_01'}, {'plan_beat': 'p01_01'}], '重复引用'),
    ([], '这一拍没有给预算'),
])
def test_every_plan_beat_violation_is_a_terminal_state(bad, why):
    """三种违约各自说出**是哪一种**——兜底会给违约数据发一张静默通行证。"""
    plan = {'output_chapters': [{'output_ordinal': 1, 'beats': bad}]}
    with pytest.raises(RefusedByStructure, match=why):
        expand_plan_beats(plan, {1: [['b01_01']]})


def test_expand_plan_beats_writes_from_beats_and_drops_nothing():
    plan = {'output_chapters': [{'output_ordinal': 1,
                                 'beats': [{'plan_beat': 'p01_01', 'budget_chars': 120}]}]}
    assert expand_plan_beats(plan, {1: [['b01_01', 'b01_02']]}) == 1
    assert plan['output_chapters'][0]['beats'][0]['from_beats'] == ['b01_01', 'b01_02']


# ── 七、恒等基线 ──────────────────────────────────────────────────────────

@needs_corpus
def test_the_identity_plan_gives_每拍照源字数():
    """恒等基线 = 「计划什么都不改时，写手手里到底有什么」。

    ⚠ 三个 goal 填的是**显式标记**而不是 `None`/空串：新契约那三个字段必填 `str`，
    而**空串与「没做决策」在人眼里同形**。
    """
    for sid in BOOKS:
        bidx = beat_index(BPS[sid], MAIN[sid])
        groups = group_beats(BPS[sid], MAIN[sid], bidx)
        ip = identity_plan(BPS[sid], MAIN[sid], bidx, groups)
        assert len(ip['output_chapters']) == len(BPS[sid]['blueprints'])
        for ch in ip['output_chapters']:
            assert ch['output_ordinal'] == ch['from_source_ordinals'][0], '恒等式必须成立'
            assert ch['chapter_goal'] == IDENTITY_GOAL
            for be in ch['beats']:
                assert be['budget_chars'] == group_chars(be['from_beats'], bidx)
