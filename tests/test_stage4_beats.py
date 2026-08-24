#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Stage4 拍的确定性切分、精确 span 与篇幅量纲。"""
from __future__ import annotations

from functools import wraps

import pytest

from contracts.invariants import (
    BEAT_MIN_CHARS, GROUPING_VERSION, PLAN_BUDGET_BAND, VERBATIM_BUDGET_FACTOR,
)
from novel.core.spans import content_only, locate_spans
from novel.core.text import norm
from novel.errors import RefusedByStructure
from novel.stages.stage4.beats import (
    beat_index, group_beats, group_chars, group_map, plan_beat_id, verbatim_pieces,
)
from novel.stages.stage4.measure import (
    IDENTITY_GOAL, allocate_budgets, budget_basis, chapter_source_chars,
    expand_plan_beats, identity_plan, source_chars_table, verbatim_chars,
    verbatim_share_stats,
)
from tests import corpus


def _fake_bidx(*beat_ids: str) -> dict[str, dict[str, object]]:
    """`expand_plan_beats` 从 bidx 只读一格：`event`。

    它读那一格是为了守住 `spine` 的下标口径——下发给规划者的骨架**过滤**空 event，
    写手那一侧**不过滤**，两边计数一分家 `spine` 就指错人。所以这里给的每条都非空：
    合成一份「stage2 契约成立」的 bidx，让下面那批测试判的是编号违约本身，
    而不是顺带撞上那道断言。
    """
    return {b: {'event': f'{b} 发生了一件事'} for b in beat_ids}


def _by_rel(rel: str) -> dict[str, object]:
    return {a.source_id: corpus.load_current(a) for a in corpus.iter_artifacts() if a.rel == rel}


MAIN: dict[str, object] = {}
BPS: dict[str, object] = {}
PLANS: dict[str, object] = {}
BOOKS: list[str] = []
_CORPUS_LOADED = False


def _load_corpus() -> None:
    global MAIN, BPS, PLANS, BOOKS, _CORPUS_LOADED
    if _CORPUS_LOADED:
        return
    MAIN = _by_rel('stage1/main_text.json')
    BPS = _by_rel('stage2/chapter_blueprints.json')
    PLANS = _by_rel('stage4/plan_chapters.json')
    BOOKS = sorted(set(MAIN) & set(BPS) & set(PLANS))
    _CORPUS_LOADED = True


def needs_corpus(test):
    @pytest.mark.corpus
    @wraps(test)
    def run(*args, **kwargs):
        _load_corpus()
        if not BOOKS:
            pytest.skip('盘上没有语料——本族对账无从谈起')
        return test(*args, **kwargs)
    return run


# ── 一、源 span 定位与预算基准 ────────────────────────────────────────────
#
# ⚠⚠ **这里曾经有一条「重算分组必须逐组等于盘上 `from_beats`」的语料对账，已删。**
# 它断言的是一道**产品明说不存在的闸**：`R-5.5` 把「输出写作单元的分组与顺序」交给了
# LLM（`group_beats` 的输出从不落盘，只是喂给规划器的候选，再由 `expand_plan_beats`
# 按模型给的顺序展开），而 `R-5.6` 的 `structure_changed`（HARD）更**要求**源单元 ≥4
# 的章不得仍按源序展开 ⇒ 那条断言与一条 HARD 方向相反，在产品**正常工作时**恒红
# （实测盘上三本 55/55 章全部命中）。属 `docs/LESSONS.md` 第 21 条第二形状。
# 它想守的合法那半已被判据侧完整接住：`structure_units`（HARD，守「每个源 beat 恰好
# 一次 + 整组落在同一写作单元 + 组内连续不换序」）、`beat_coverage`（HARD，守一个不丢）、
# `beat_ref` + `one_to_one`（守跨章）；那两条从未见过红灯的分支已在
# `tests/test_hard_counterexamples.py` 补了定向反例。
# 它唯一守得住而别处没有的那半——「算法漂了而 `GROUPING_VERSION` 没跟着动」——
# 由下面第三节的 `test_the_grouping_version_string_moves_whenever_the_grouping_moves` 接住。

@needs_corpus
def test_every_current_source_span_locates():
    """正式蓝图的精确区间全部与正文一致。"""
    located = 0
    for sid in BOOKS:
        bps = BPS[sid]['blueprints']
        text = {m['chapter_ordinal']: (m.get('text') or '') for m in MAIN[sid]}
        spans, problems = locate_spans(bps, text)
        assert not problems, f'{sid} 有锚点定位不到：{problems[:3]}'
        located += sum(len(v) for v in spans.values())
    assert located > 0


@needs_corpus
def test_the_span_baseline_is_not_the_whole_chapter():
    """预算基准来自可定位 span，且不得超出正文。"""
    for sid in BOOKS:
        bidx = beat_index(BPS[sid], MAIN[sid])
        chars, cov = chapter_source_chars(MAIN[sid], bidx)
        assert all(g == t for g, t in cov.values()), f'{sid} 有拍算不出 span'
        span_total = sum(chars.values())
        body = sum(len(m.get('text') or '') for m in MAIN[sid])
        assert 0 < span_total <= body


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


def test_the_grouping_version_string_moves_whenever_the_grouping_moves():
    """⭐ **接住被删掉那条语料对账唯一守得住、而别处没有的那半。**

    `params_drift`（`novel/stages/stage4/checks.py:223-243`）判的是「这份计划的拍是照
    哪一套切的」，而它的**全部**依据就是 `GROUPING_VERSION` 这一个字符串。
    算法改了而这个串没跟着改 ⇒ 盘上一批旧计划会被当成同一把尺子量过的，
    而它们不是；那条判据会报「比过」，**而它其实没比**——一个假的匹配比没有记录更坏。
    ⇒ 把「算法的行为」与「版本串」钉进同一条断言：改 `group_beats` 的行为必须同时改这里
    与 `contracts/invariants.py::GROUPING_VERSION`，否则本条当场红。

    ⚠ **这不是把实现抄一遍**：只有一组固定输入 → 一组固定输出，约束的是
    「行为变了要有人改版本串」，不是「行为该怎么算」——后者由本节上面那四条性质测试守。
    这一组输入刻意走满三件事：吸收碎拍、**并进较小的那一侧**（b01_02 两侧 500 vs 100
    取右）、以及**没有右邻居时并左**（b01_04），且需要连续两轮才收敛。
    """
    assert GROUPING_VERSION == 'absorb-small-v1'
    ids = ['b01_01', 'b01_02', 'b01_03', 'b01_04']
    bidx = _bidx({'b01_01': 500, 'b01_02': 10, 'b01_03': 100, 'b01_04': 10})
    assert group_beats(_bps(ids), [], bidx)[1] == [
        ['b01_01'], ['b01_02', 'b01_03', 'b01_04']], (
        '分组行为变了。**这不是把断言改成新值就完事**——`GROUPING_VERSION` 要同时 bump，'
        '否则盘上按旧算法切的计划会被 `params_drift` 当成同一套尺子量过的')


def test_plan_beat_ids_use_p_not_b():
    """`p` 前缀与 stage2 的 `b` 分开——**同名不同义**是最好的漂移温床。"""
    assert plan_beat_id(1, 2) == 'p01_02'
    assert group_map({3: [['b03_01', 'b03_02']]}) == {'p03_01': ['b03_01', 'b03_02']}


# ── 四、hinge 拆点 ────────────────────────────────────────────────────────

def _hinge(kind: str, text: str) -> dict:
    return {
        'kind': kind,
        'source': {
            'text': text,
            'start': 0,
            'end': len(text),
            'normalized': content_only(text),
            'recovery': 'exact',
        },
    }

def test_a_cross_paragraph_hinge_becomes_several_points():
    """跨段落的一条 hinge **就是一个「段」而不是一个「点」**。

    实测某章 4 条 verbatim 全部跨段落，成稿把它们**一条不剩地丢光**——
    写手要么连原文的段落结构一起复现，要么整组放弃，而它选了后者。
    """
    out, unknown = verbatim_pieces([_hinge('要害台词', '第一句。\n\n第二句。')])
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
    out, unknown = verbatim_pieces([_hinge('行动描述', '她走进了屋子。')])
    assert out == {} and unknown == [('行动描述', '她走进了屋子。')]


def test_verbatim_chars_counts_only_the_verbatim_group():
    """另外三组允许换措辞甚至不出现在文面 —— **它们压得动，不算不可压缩量**。"""
    hs = [_hinge('要害台词', '你别走。'),
          _hinge('硬数字', '二十两银子'),
          _hinge('因果前提', '她已经知道了')]
    assert verbatim_chars(hs) == len('你别走。')


def test_beat_index_hands_verbatim_pieces_a_form_it_can_actually_read():
    """⭐⭐ **这条缝断过一次，整整一批书的闭卷写手手里一个原文字节都没有。**

    上面那三条测的是 `verbatim_pieces` **自己**的逻辑，而它们各自现造一份 hinge 喂进去
    ——于是「`beat_index` 产出的形态，`verbatim_pieces` 到底读不读得懂」这件事
    **一条测试都没查**。真实经过：
      · `beat_index` 曾把 hinge 归一成 `{'kind','text'}`（那一版 stage2 的 hinge 就长这样，
        当时是恒等的）；
      · stage2 换成 `{'kind','source'}` 后，`_hinge_text` 加了双分支同时读两种形态；
      · 一次「清理遗留兼容」把读 `text` 的分支删了——**而那个键名恰好与已废弃的旧契约同名**
        ⇒ 归一形态从此读不出字节，`verbatim_pieces` 恒返回空。
    盘上五本书 898 条 hinge 一条没下发，stage2 的判据看产物（齐的）、
    stage4 的 `结构` 级守卫看函数（还在），stage5 三条 HARD 分母归零报绿，全链无人出声。

    ⇒ 本条**必须走 `beat_index` 这个生产落点**，不许自己拼一份 hinge：
      守卫重拼一份，守的就是它自己那一份，不是生产真正走的那扇门。
    """
    bps = {'blueprints': [{'chapter_ordinal': 1, 'beats': [{
        'beat_id': 'b01_01', 'event': '事件一', 'time_layer': 'present',
        'hinges': [_hinge('要害台词', '你别走。'), _hinge('硬数字', '二十两银子')]}]}]}
    bidx = beat_index(bps, [{'chapter_ordinal': 1, 'text': '你别走。二十两银子。'}])
    hinges = [h for v in bidx.values() for h in (v['hinges'] or [])]
    assert hinges, '`beat_index` 把 hinge 整个丢了'

    out, unknown = verbatim_pieces(hinges)
    assert not unknown
    assert out.get('verbatim') == ['你别走。'], (
        f'`beat_index` 产出的形态 `verbatim_pieces` 读不出来（拿到 {out!r}）——'
        f'两边的 hinge 形态又分家了。**别在 `_hinge_text` 里加第二条读法把它兼容过去**，'
        f'那正是上一次被当成「遗留兼容」删掉的东西；改成两边都用 stage2 产物形态')
    assert out.get('keep_value') == ['二十两银子']
    assert verbatim_chars(hinges) == len('你别走。'), '不可压缩量算成 0 ⇒ 规划器看不到原话占位'


# ── 五、量纲与分布 ────────────────────────────────────────────────────────

@needs_corpus
def test_every_chapter_budget_falls_inside_the_strict_band():
    """每章预算严格落在当前唯一预算带内。"""
    lo, hi = PLAN_BUDGET_BAND
    for sid in BOOKS:
        bidx = beat_index(BPS[sid], MAIN[sid])
        chars, _cov = chapter_source_chars(MAIN[sid], bidx)
        for o in PLANS[sid]['output_chapters']:
            src = sum(chars.get(s, 0) for s in o['from_source_ordinals'])
            tot = sum(b['budget_chars'] for b in o['segments']
                      if isinstance(b.get('budget_chars'), int))
            assert lo <= tot / src <= hi, f'{sid} 第 {o["output_ordinal"]} 章 {tot / src:.3f} 出带'


@needs_corpus
def test_the_verbatim_share_distribution_uses_the_current_threshold():
    """度量无条件可算；越线列表必须与分布阈值同口径。"""
    for sid in BOOKS:
        bidx = beat_index(BPS[sid], MAIN[sid])
        stats, over = verbatim_share_stats(PLANS[sid], bidx)
        if stats is not None:
            assert stats['n'] > 0
            assert 0 <= stats['median'] <= stats['p90'] <= stats['max']
            assert stats['threshold'] == 0.5
        assert all(budget < 2 * verbatim for _chapter, _beat, verbatim, budget in over)


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
        expand_plan_beats({'output_chapters': []}, {1: [['b01_01']]}, _fake_bidx('b01_01'))


@pytest.mark.parametrize('bad, why', [
    ([{'end_beat': 'p09_09'}], '编号不存在'),
    ([{'end_beat': 'p01_01'}, {'end_beat': 'p01_01'}], '被两个写作单元指到'),
    ([{'end_beat': 'p01_01'}], '末单元没有到底'),
    ([], '一个 end_beat 都没有'),
])
def test_every_end_beat_violation_is_a_terminal_state(bad, why):
    """⭐⭐ 模型只剩这四种违约，**各自说出是哪一种**——兜底会给违约数据发一张静默通行证。

    ⚠ 批 4 之前这里守的是三种「逐条点名」的违约（编号不存在 / 重复引用 / 少给一拍）。
    换成 `end_beat` 之后「少给一拍」这一类**不再表达得出来**：切分由排序推出，
    覆盖是恒等式。取而代之的是**「末单元没有到底」**——它是新契约下唯一还能漏掉事实的口子。
    """
    plan = {'output_chapters': [{'output_ordinal': 1, 'segments': bad}]}
    with pytest.raises(RefusedByStructure, match=why):
        expand_plan_beats(plan, {1: [['b01_01'], ['b01_02']]},
                          _fake_bidx('b01_01', 'b01_02'))


def test_a_source_chapter_nobody_cut_is_a_terminal_state():
    """⭐ 覆盖是章内的恒等式，**章本身漏掉不是**——那一格仍要有人守。"""
    plan = {'output_chapters': [{'output_ordinal': 1,
                                 'segments': [{'end_beat': 'p01_01'}]}]}
    with pytest.raises(RefusedByStructure, match='没有任何输出章切分它'):
        expand_plan_beats(plan, {1: [['b01_01']], 2: [['b02_01']]},
                          _fake_bidx('b01_01', 'b02_01'))


def test_expand_plan_beats_writes_from_beats_and_drops_nothing():
    plan = {'output_chapters': [{'output_ordinal': 1,
                                 'segments': [{'end_beat': 'p01_01'}]}]}
    assert expand_plan_beats(
        plan, {1: [['b01_01', 'b01_02']]}, _fake_bidx('b01_01', 'b01_02')) == 1
    assert plan['output_chapters'][0]['segments'][0]['from_beats'] == ['b01_01', 'b01_02']
    assert plan['output_chapters'][0]['segments'][0]['slot'] == 's01'
    assert 'end_beat' not in plan['output_chapters'][0]['segments'][0], \
        '`end_beat` 是响应字段，展开之后不该留在产物上'


def test_expand_plan_beats_preserves_planner_grouping_and_reordered_sequence():
    """⭐⭐ **切分由排序推出，顺序由数组序决定——这两件事互不干扰。**

    这一条就是批 4 那次编码换代的验收断言：模型把第三个源单元提到最前面写，
    同时把前两个并成一个写作单元。它给的只有两个 `end_beat`
    （`p01_03` 与 `p01_02`，数组序是成稿序），而代码要还原出
    「(3) 先写、(1,2) 后写」这两个范围。⇒ **重排一格没丢。**
    """
    plan = {'output_chapters': [{'output_ordinal': 1, 'segments': [
        {'end_beat': 'p01_03'},
        {'end_beat': 'p01_02'},
    ]}]}
    assert expand_plan_beats(
        plan, {1: [['b01_01'], ['b01_02'], ['b01_03']]},
        _fake_bidx('b01_01', 'b01_02', 'b01_03')) == 3
    assert [row['from_beats'] for row in plan['output_chapters'][0]['segments']] == [
        ['b01_03'], ['b01_01', 'b01_02']]
    assert [row['slot'] for row in plan['output_chapters'][0]['segments']] == ['s01', 's02']


# ── 六·2、`spine`：拍内主次（2026-08-22，批 3）────────────────────────────

def _spine_plan(spine, *, end_beat='p01_01') -> dict:
    return {'output_chapters': [{'output_ordinal': 1, 'segments': [
        {'end_beat': end_beat, 'spine': spine}]}]}


@pytest.mark.parametrize('spine, expected', [('b01_01', 1), ('b01_02', 2)])
def test_a_spine_naming_an_event_of_this_unit_becomes_its_index(spine, expected):
    """⭐⭐ 模型给的是**哪一件事**（`beat_id`），落盘的是**第几件**（下标）。

    ⚠ 换算就是「它在 `from_beats` 里排第几」——与 `plan_beats → from_beats` 同一次动作。
    模型一次都不必数：那个数代码本来就精确知道（2026-08-22 的教训，见
    `PlanBeatResponse.spine`）。
    """
    plan = _spine_plan(spine)
    assert expand_plan_beats(
        plan, {1: [['b01_01', 'b01_02']]}, _fake_bidx('b01_01', 'b01_02')) == 1
    assert plan['output_chapters'][0]['segments'][0]['spine'] == expected


@pytest.mark.parametrize('spine', ['b01_03', 'b07_07'])
def test_a_spine_naming_an_event_outside_this_unit_is_a_terminal_state(spine):
    """⭐⭐ **归属只有这里知道，所以它也在这里判。**

    载体侧（`PlanBeatResponse.spine`）只判得了形状（`bNN_NN`）——「这个编号在不在本单元
    切到的那几个源内容单元里」要先把切分展开出来才知道。
    ⚠ 它与三种编号违约走**同一条出口**：分成两处 raise，就会有一份计划先报完编号错、
    重跑一次再报一遍 spine 错，而两次都要花钱。
    ⚠ `b01_03` 是「本章有、但不在本单元」，`b07_07` 是「压根不存在」——
    两种都只是**指错人**，判词一样。
    """
    plan = {'output_chapters': [{'output_ordinal': 1, 'segments': [
        {'end_beat': 'p01_01', 'spine': spine},
        {'end_beat': 'p01_02'},
    ]}]}
    with pytest.raises(RefusedByStructure, match='不是本单元里的事'):
        expand_plan_beats(plan, {1: [['b01_01', 'b01_02'], ['b01_03']]},
                          _fake_bidx('b01_01', 'b01_02', 'b01_03'))


def test_a_unit_that_already_broke_the_numbering_does_not_also_get_a_spine_complaint():
    """⭐ 已经有编号违约的单元**不再判它的 `spine`**——那时 `cited` 本来就是残的。

    ⚠ 报出来的归属错是**派生症状**，会盖住真根因。这是「74 条症状盖住一句话」
    （`test_an_empty_plan_says_so_in_one_sentence`）的同一个教训，
    只不过这一次症状与根因在同一个写作单元里。
    """
    plan = _spine_plan('b01_01', end_beat='p09_09')        # 编号不存在 ⇒ 本章整个不展开
    with pytest.raises(RefusedByStructure) as excinfo:
        expand_plan_beats(plan, {1: [['b01_01']]}, _fake_bidx('b01_01'))
    assert '编号不存在' in str(excinfo.value)
    assert '不是本单元里的事' not in str(excinfo.value), 'spine 的派生症状盖过了真根因'


def test_a_spine_is_never_a_number_on_the_response_side():
    """⭐⭐ **响应侧收编号、不收数字**，这条守卫钉的是那次实付买回来的形状。

    2026-08-22：一份 schema 合法的计划里 60 个写作单元错 10 个，**9 个是「本单元只有
    1 件事、却填了 2 或 3」**——模型要填对下标就得把几个源单元的 events 摊平累加，
    而那个数代码本来就精确知道。⇒ 数字这条路**不许再回来**：真回来了，
    `expand_plan_beats` 会安静地把它当成「没做过主次决策」放过去。
    """
    plan = _spine_plan(2)                                   # 旧形状：下标
    with pytest.raises(RefusedByStructure, match='不是本单元里的事'):
        expand_plan_beats(plan, {1: [['b01_01', 'b01_02']]},
                          _fake_bidx('b01_01', 'b01_02'))


def test_an_empty_source_event_is_a_terminal_state_because_it_shifts_the_spine():
    """⭐⭐ **那道计数断言**：下发面过滤空 event，写手那一侧不过滤，差一格 `spine` 就指错人。

    ⚠ stage2 契约（`event: str = Field(min_length=1)`）保证两者恒等 ⇒ 这条在正常盘上
    **永远不响**。它响的那一天，恒等式已经破了——而破了之后往下走的代价是
    「主干静默指向另一件事」，一条判据都看不见。
    ⚠ 文案必须说清这不是模型的错：根因在上游那份产物，重跑规划一次都修不好。
    """
    bad = _fake_bidx('b01_01', 'b01_02')
    bad['b01_02']['event'] = '   '
    with pytest.raises(RefusedByStructure, match='stage2 契约') as excinfo:
        expand_plan_beats(_spine_plan('b01_01'), {1: [['b01_01', 'b01_02']]}, bad)
    assert 'b01_02' in str(excinfo.value), '没点名是哪一个 beat'


def test_a_plan_without_a_spine_expands_unchanged():
    """`spine` 缺席是合法态（`--revalidate` 的存量计划、恒等基线）——展开路不为它出声。"""
    plan = {'output_chapters': [{'output_ordinal': 1, 'segments': [
        {'end_beat': 'p01_01'}]}]}
    assert expand_plan_beats(plan, {1: [['b01_01']]}, _fake_bidx('b01_01')) == 1
    assert 'spine' not in plan['output_chapters'][0]['segments'][0]



# ── 六·3、篇幅：两级档位 → 整数（2026-08-22，批 4）────────────────────────
#
# ⚠⚠ **这一节守的是「三条算术 HARD 已经换了责任方」这件事本身。**
# `budget_int` / `budget_band` / `plan_book_band` 现在是 `allocate_budgets` 的后置条件；
# 判据那三条一条没撤（人工稿与存量产物仍然走它们），但**生成路上它们恒绿**——
# 而「恒绿」只有在这里被证明成恒等式时才是好事，否则它就只是没人再看的绿灯。

def _weighted_plan(chapter_weights, unit_weight='mid'):
    """每章一个源内容单元、每个单元一个写作单元的最小盘面 + 指定的档位。"""
    groups = {o: [[f'b{o:02d}_01']] for o in chapter_weights}
    bidx = {f'b{o:02d}_01': {'event': '一件事', 'source_chars': 100 * o, 'hinges': [],
                             'ord': o}
            for o in chapter_weights}
    plan = {'output_chapters': [
        {'output_ordinal': o, 'from_source_ordinals': [o], 'weight': w,
         'segments': [{'slot': 's01', 'from_beats': [f'b{o:02d}_01'], 'weight': unit_weight}]}
        for o, w in chapter_weights.items()]}
    return plan, groups, bidx


@pytest.mark.parametrize('weights', [
    {1: 'major', 2: 'minor', 3: 'minor', 4: 'minor'},      # 一章独重
    {1: 'major', 2: 'major', 3: 'major', 4: 'minor'},      # 章章都想重
    {1: 'major', 2: 'above', 3: 'below', 4: 'minor'},      # 摊开
    {1: 'even', 2: 'even', 3: 'even', 4: 'even'},          # 一格都没动
])
def test_no_weighting_the_model_can_pick_pushes_a_chapter_out_of_band(weights):
    """⭐⭐ **ch18 那次 0.442× 在新契约下结构上不可能。**

    批 2 run1 花了一次全书实付才知道：模型把「压缩」理解成把那个数字调小，
    ch18 冲出 `PLAN_BUDGET_BAND` 的下界，整份计划退回。那时唯一的防线是 prompt 里
    一句「两端都是硬的」。现在数字不归它管了 ⇒ **无论它怎么填档位**，
    逐章倍率都被 `_water_fill` 钳在带内。
    ⚠ 参数化里那四行是**四种填法**，包括「章章都标 major」这种最容易顶破总量的填法。
    """
    plan, groups, bidx = _weighted_plan(weights)
    allocate_budgets(plan, groups, bidx)
    lo, hi = PLAN_BUDGET_BAND
    for chapter in plan['output_chapters']:
        src = 100 * chapter['output_ordinal']
        got = sum(b['budget_chars'] for b in chapter['segments'])
        assert lo <= got / src <= hi, \
            f'第 {chapter["output_ordinal"]} 章 {got / src:.3f} 出带（档位 {chapter["weight"]}）'
        assert all(isinstance(b['budget_chars'], int) and b['budget_chars'] > 0
                   for b in chapter['segments']), 'budget_int 的后置条件破了'


def test_the_book_total_is_exactly_the_source_total_not_merely_inside_the_band():
    """⭐⭐ 全书合计**逐字等于**源字数，不是「落在 `BOOK_BAND` 里就算」。

    ⚠ 「恰好相等」是取整那一步买的（最大余数法）：逐格 `round()` 之后那个和会漂几十个字，
    而它正好压在带的边界上时，一份合格的计划会因为**取整**被判红——那种红最难查，
    因为每一格看上去都对。
    """
    plan, groups, bidx = _weighted_plan({1: 'major', 2: 'minor', 3: 'above', 4: 'below'})
    allocate_budgets(plan, groups, bidx)
    total = sum(b['budget_chars'] for c in plan['output_chapters'] for b in c['segments'])
    assert total == 100 + 200 + 300 + 400, f'全书合计 {total} ≠ 源字数 1000'


def test_marking_a_chapter_major_actually_makes_it_thicker_than_marking_it_minor():
    """⭐⭐ **档位不是一个装饰字段**——它必须真的动那个数。

    ⚠ 这条与上一条是**一对**：只钳带不看效果的话，一个「把所有章都算成 1.0×」的实现
    会让上面每一条断言都绿，而线 A 那三轮实付买来的章间轻重会被**静默清零**。
    """
    heavy, groups, bidx = _weighted_plan({1: 'major', 2: 'minor'})
    light, _g, _b = _weighted_plan({1: 'minor', 2: 'major'})
    allocate_budgets(heavy, groups, bidx)
    allocate_budgets(light, groups, bidx)
    first_heavy = sum(b['budget_chars'] for b in heavy['output_chapters'][0]['segments'])
    first_light = sum(b['budget_chars'] for b in light['output_chapters'][0]['segments'])
    assert first_heavy > first_light, \
        f'第 1 章标 major 与标 minor 拿到同样多的字（{first_heavy} vs {first_light}）'


def test_marking_a_unit_peak_actually_makes_it_thicker_than_the_rest_of_the_chapter():
    """⭐ 章内那一层同理：`W_PEAK` 必须真的比 `W_BRUSH` 厚，否则「本章哪里重要」没有落点。"""
    groups = {1: [['b01_01'], ['b01_02']]}
    bidx = {'b01_01': {'event': 'a', 'source_chars': 200, 'hinges': [], 'ord': 1},
            'b01_02': {'event': 'b', 'source_chars': 200, 'hinges': [], 'ord': 1}}
    plan = {'output_chapters': [{
        'output_ordinal': 1, 'from_source_ordinals': [1], 'weight': 'even', 'segments': [
            {'slot': 's01', 'from_beats': ['b01_01'], 'weight': 'peak'},
            {'slot': 's02', 'from_beats': ['b01_02'], 'weight': 'brush'}]}]}
    allocate_budgets(plan, groups, bidx)
    peak, brush = (b['budget_chars'] for b in plan['output_chapters'][0]['segments'])
    assert peak > brush * 3, f'重头拍 {peak} 与一带而过 {brush} 差得不够——档位没进到分配式里'


def test_the_allocator_keeps_the_verbatim_floor_it_promised_the_writer():
    """⭐⭐ 逐字下限（`R-5.13`）现在也由分配器守：原话密集的那一拍不会被压到只剩引用。

    ⚠ 它从前是 prompt 里一句「办法是匀」——而「匀」这件事要模型在两拍之间做加减法。
    """
    groups = {1: [['b01_01'], ['b01_02']]}
    hinge = [{'kind': '要害台词', 'source': {'text': '你' * 60}}]   # ⇒ verbatim 组
    bidx = {'b01_01': {'event': 'a', 'source_chars': 200, 'hinges': hinge, 'ord': 1},
            'b01_02': {'event': 'b', 'source_chars': 200, 'hinges': [], 'ord': 1}}
    plan = {'output_chapters': [{
        'output_ordinal': 1, 'from_source_ordinals': [1], 'weight': 'even', 'segments': [
            # ⚠ 故意把**原话最多**的那一拍标成一带而过——这正是模型会犯的那个错。
            {'slot': 's01', 'from_beats': ['b01_01'], 'weight': 'brush'},
            {'slot': 's02', 'from_beats': ['b01_02'], 'weight': 'peak'}]}]}
    allocate_budgets(plan, groups, bidx)
    got = plan['output_chapters'][0]['segments'][0]['budget_chars']
    assert got >= VERBATIM_BUDGET_FACTOR * 60, \
        f'带 60 字原话的那一拍只拿到 {got} 字 ⇒ 写手除了原话再没有别的字可写'


def test_targeted_repair_cannot_move_the_chapter_total():
    """⭐⭐ 定向修复那一路**顶不出 `plan_book_band`**——总量钉死，档位只决定怎么分。

    ⚠ 从前这是 `PLAN_REPAIR_OVERRIDE` 里对模型的一句叮嘱。一句叮嘱的守卫强度是
    「它照做了我们才没事」，而别章的预算它根本看不见 ⇒ 它没有任何办法验证自己。
    """
    groups = {2: [['b02_01'], ['b02_02']]}
    bidx = {'b02_01': {'event': 'a', 'source_chars': 300, 'hinges': [], 'ord': 2},
            'b02_02': {'event': 'b', 'source_chars': 300, 'hinges': [], 'ord': 2}}
    plan = {'output_chapters': [{
        'output_ordinal': 2, 'from_source_ordinals': [2], 'weight': 'major', 'segments': [
            {'slot': 's01', 'from_beats': ['b02_01'], 'weight': 'peak'},
            {'slot': 's02', 'from_beats': ['b02_02'], 'weight': 'brush'}]}]}
    allocate_budgets(plan, groups, bidx, chapter_totals={2: 555})
    got = [b['budget_chars'] for b in plan['output_chapters'][0]['segments']]
    assert sum(got) == 555, f'本章总量被改动了：{sum(got)} ≠ 555'
    assert got[0] > got[1], '总量钉死之后档位就该只管分配——它没有生效'


def test_a_chapter_whose_spans_all_failed_is_named_before_any_call_is_made():
    """⭐⭐ 没有基准就**不许开跑**：那是 stage2 的锚点坏了，重采多少次都修不好。

    ⚠ 不拦在调用之前的话，它会在 `ask_json(validator=)` 里变成一次「响应不合格」，
    于是整条降级阶梯走到底、每一次都必然失败——**一次纯粹的付费空转**。
    """
    groups = {1: [['b01_01']], 2: [['b02_01']]}
    bidx = {'b01_01': {'event': 'a', 'source_chars': 100, 'hinges': [], 'ord': 1},
            'b02_01': {'event': 'b', 'source_chars': None, 'hinges': [], 'ord': 2}}
    per_chapter, _per_unit, gaps = budget_basis(groups, bidx)
    assert gaps == [2] and 1 in per_chapter, f'没点名是哪一章：{gaps}'


def test_a_partly_unknown_chapter_falls_back_to_its_own_median_not_to_zero():
    """⭐ 部分未知的拍用**本章已知单元的中位数**顶替，不当成 0。

    ⚠ 当成 0 的后果是方向反的：那一拍拿到 1 个字的预算，而它在原文里可能有两百字。
    ⚠ 这一章的 `budget_band` 本来就走 `unver`（覆盖率残缺），所以顶替不会让任何一条
    判据拿着一把缩水的尺子打出结论。盘上实测 5 本书 379 个单元里算不出的是 **0 个**。
    """
    groups = {1: [['b01_01'], ['b01_02'], ['b01_03']]}
    bidx = {'b01_01': {'event': 'a', 'source_chars': 100, 'hinges': [], 'ord': 1},
            'b01_02': {'event': 'b', 'source_chars': None, 'hinges': [], 'ord': 1},
            'b01_03': {'event': 'c', 'source_chars': 300, 'hinges': [], 'ord': 1}}
    per_chapter, per_unit, gaps = budget_basis(groups, bidx)
    assert not gaps
    assert per_unit['p01_02'] == 300, f'顶替值不是本章已知单元的中位数：{per_unit}'
    assert per_chapter[1] == 700


def test_the_response_side_pattern_matches_exactly_what_the_producer_writes():
    """⭐⭐ `PLAN_BEAT_ID_PATTERN` 与 `plan_beat_id` 的恒等——**两个落点，一条断言**。

    载体在 `contracts/` 里（那一层不许 import `novel/`），生产函数在 `novel/` 里
    ⇒ 这条恒等式只有测试能钉。⚠ 三位数的章号必须仍然合法：`plan_beat_id` 用 `:02d`
    格式化，宽度是**下限不是上限**，写死 `d{2}` 会让一本 100 章以上的书在
    **一次已经付过钱的响应**上被载体拒收。
    """
    import re

    from contracts.schema.stage4 import PLAN_BEAT_ID_PATTERN

    for ordinal, seq in ((1, 1), (9, 12), (23, 7), (100, 3), (137, 99)):
        assert re.match(PLAN_BEAT_ID_PATTERN, plan_beat_id(ordinal, seq)), \
            f'生产函数写得出、而载体收不下：{plan_beat_id(ordinal, seq)}'
    assert not re.match(PLAN_BEAT_ID_PATTERN, 'b01_01'), '把 stage2 的源 beat 编号也收了'


def test_the_identity_baseline_never_claims_to_have_picked_a_spine():
    """⭐⭐ 恒等基线的 `spine` 恒为 `None`，**不是 1**。

    基线的定义就是「一个决策都没做过」，而 `spine` 是拍这一层的决策之一
    （另一个是分量档 `weight`）。给它 1 会让基线看起来做过一次主次判断——
    而这份基线正是 `structure_changed` 的反例来源，**反例上多一个决策，那条 HARD 的靶子
    就软了一格**。
    """
    from tests.hard_cases import _BPS4_PRESENT, _MAIN4

    plan = identity_plan(_BPS4_PRESENT, [_MAIN4[0]])
    spines = [be['spine'] for o in plan['output_chapters'] for be in o['segments']]
    assert spines and set(spines) == {None}, f'恒等基线自称做过主次决策：{spines}'


def test_the_baseline_block_omits_a_null_spine_instead_of_printing_it():
    """⭐⭐ 反编码：`spine=None` 那一格**整个删掉**，不印 `null`。

    ⚠ 与那三张前缀表是同一件事：基线块必须是一份「照抄就合法」的响应。
    `PlanBeatResponse.spine` 是必填的，把 `null` 印进基线块，就是在同一份 user 里让
    system 与基线互相打脸——模型照基线抄，schema 当场拒，**而那一拒要花一次钱才看得见**。
    """
    from novel.stages.stage4.measure import encode_plan_baseline

    kept = encode_plan_baseline({'segments': [
        {'delivery': 'scene', 'spine': 2, 'from_beats': ['b01_01', 'b01_02']},
        {'delivery': 'summary', 'spine': None, 'from_beats': ['b01_03']}]})
    # ⚠⚠ 顺带钉住**类型也要翻回去**：产物侧存下标、响应侧收 `beat_id`，
    #   基线块里印一个数字，模型照抄就是一次 schema 拒（同一格的错，只是错在类型）。
    assert kept['segments'][0]['spine'] == 'b01_02'
    assert 'spine' not in kept['segments'][1]


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
            assert ch['chapter_goal']['rationale'] == IDENTITY_GOAL
            for be in ch['segments']:
                assert be['budget_chars'] == group_chars(be['from_beats'], bidx)
