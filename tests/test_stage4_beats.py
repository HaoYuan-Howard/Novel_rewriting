#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Stage4 拍的确定性切分、精确 span 与篇幅量纲。"""
from __future__ import annotations

from functools import wraps

import pytest

from contracts.invariants import BEAT_MIN_CHARS, GROUPING_VERSION, PLAN_BUDGET_BAND
from novel.core.spans import content_only, locate_spans
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
    ([{'plan_beats': ['p09_09']}], '编号不存在'),
    ([{'plan_beats': ['p01_01']}, {'plan_beats': ['p01_01']}], '重复引用'),
    ([], '这一拍没有给预算'),
])
def test_every_plan_beat_violation_is_a_terminal_state(bad, why):
    """三种违约各自说出**是哪一种**——兜底会给违约数据发一张静默通行证。"""
    plan = {'output_chapters': [{'output_ordinal': 1, 'segments': bad}]}
    with pytest.raises(RefusedByStructure, match=why):
        expand_plan_beats(plan, {1: [['b01_01']]}, _fake_bidx('b01_01'))


def test_expand_plan_beats_writes_from_beats_and_drops_nothing():
    plan = {'output_chapters': [{'output_ordinal': 1,
                                 'segments': [{'plan_beats': ['p01_01'], 'budget_chars': 120}]}]}
    assert expand_plan_beats(
        plan, {1: [['b01_01', 'b01_02']]}, _fake_bidx('b01_01', 'b01_02')) == 1
    assert plan['output_chapters'][0]['segments'][0]['from_beats'] == ['b01_01', 'b01_02']
    assert plan['output_chapters'][0]['segments'][0]['slot'] == 's01'


def test_expand_plan_beats_preserves_planner_grouping_and_reordered_sequence():
    plan = {'output_chapters': [{'output_ordinal': 1, 'segments': [
        {'plan_beats': ['p01_03'], 'budget_chars': 80},
        {'plan_beats': ['p01_01', 'p01_02'], 'budget_chars': 160},
    ]}]}
    assert expand_plan_beats(
        plan, {1: [['b01_01'], ['b01_02'], ['b01_03']]},
        _fake_bidx('b01_01', 'b01_02', 'b01_03')) == 3
    assert [row['from_beats'] for row in plan['output_chapters'][0]['segments']] == [
        ['b01_03'], ['b01_01', 'b01_02']]
    assert [row['slot'] for row in plan['output_chapters'][0]['segments']] == ['s01', 's02']


# ── 六·2、`spine`：拍内主次（2026-08-22，批 3）────────────────────────────

def _spine_plan(spine, *, plan_beats=('p01_01',)) -> dict:
    return {'output_chapters': [{'output_ordinal': 1, 'segments': [
        {'plan_beats': list(plan_beats), 'budget_chars': 120, 'spine': spine}]}]}


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
    引的那几个源内容单元里」要先把 `plan_beats[]` 过一遍 `gmap` 才知道。
    ⚠ 它与三种编号违约走**同一条出口**：分成两处 raise，就会有一份计划先报完编号错、
    重跑一次再报一遍 spine 错，而两次都要花钱。
    ⚠ `b01_03` 是「本章有、但不在本单元」，`b07_07` 是「压根不存在」——
    两种都只是**指错人**，判词一样。
    """
    plan = _spine_plan(spine)
    with pytest.raises(RefusedByStructure, match='不是本单元里的事'):
        expand_plan_beats(plan, {1: [['b01_01', 'b01_02'], ['b01_03']]},
                          _fake_bidx('b01_01', 'b01_02', 'b01_03'))


def test_a_unit_that_already_broke_the_numbering_does_not_also_get_a_spine_complaint():
    """⭐ 已经有编号违约的单元**不再判它的 `spine`**——那时 `cited` 本来就是残的。

    ⚠ 报出来的归属错是**派生症状**，会盖住真根因。这是「74 条症状盖住一句话」
    （`test_an_empty_plan_says_so_in_one_sentence`）的同一个教训，
    只不过这一次症状与根因在同一个写作单元里。
    """
    plan = _spine_plan('b01_01', plan_beats=('p09_09',))   # 编号不存在 ⇒ cited 为空
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
        {'plan_beats': ['p01_01'], 'budget_chars': 120}]}]}
    assert expand_plan_beats(plan, {1: [['b01_01']]}, _fake_bidx('b01_01')) == 1
    assert 'spine' not in plan['output_chapters'][0]['segments'][0]


def test_the_identity_baseline_never_claims_to_have_picked_a_spine():
    """⭐⭐ 恒等基线的 `spine` 恒为 `None`，**不是 1**。

    基线的定义就是「一个决策都没做过」，而 `spine` 是拍这一层的第二个决策
    （第一个是 `budget_chars`）。给它 1 会让基线看起来做过一次主次判断——
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
