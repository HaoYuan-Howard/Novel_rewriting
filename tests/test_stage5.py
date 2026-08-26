#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
test_stage5.py —— 逐章写作 + 全书验收。

⭐ **这一份里最值钱的是那 7 条 HARD 的反例样本。**
盘上历史成稿对这些 HARD 是 **0 命中**，而「见过它通过」不是证据，「见过它红一次」才是
（LESSONS 第 3 条）。stage4 / stage6 / stage7 合计 36 条 HARD 至今 0 反例样本——
本层不再往那个数上加。

⚠ 反例配得出来，是因为**判据吃 `dict` 不吃模型**：一旦判据跑在已经过了 Pydantic 的
对象上，「必填齐全」这类判据在结构上就构造不出反例，它必然全绿。
"""
from __future__ import annotations

import json
import shutil
import tempfile
from pathlib import Path

import pytest

from contracts.schema import Chapter, decode_artifact, encode_artifact
from contracts.rules import REGISTRY
from novel.stages.stage5 import checks, measure as M, prompts
from tests import corpus, factories, sandbox

_REQUIRED = {
    'stage1/main_text.json', 'stage2/global_grounding.json',
    'stage3/style_grounding.json', 'stage4/writer_context.json',
}


def _eligible_books() -> list[str]:
    rels: dict[str, set[str]] = {}
    drafts: set[str] = set()
    for artifact in corpus.iter_artifacts():
        rels.setdefault(artifact.book_dir, set()).add(artifact.rel)
        if artifact.pattern == 'stage5/ch{nn}.json' and '_rounds/' not in artifact.rel:
            drafts.add(artifact.book_dir)
    return sorted(book for book, seen in rels.items() if _REQUIRED <= seen and book in drafts)


_BOOKS = _eligible_books()

#: ⚠⚠ **优先取「成稿链条相对上游是当前」的那一本**，不是排序第一的那一本。
#: 本文件五条端到端用例真跑 CLI 写盘，而写盘口的后置条件要求写完自证 `fresh`；
#: 盘上成稿一旦相对重生成过的 `stage4/writer_context.json` 过期，
#: `stage5.write --recheck` 会 `ProvenanceRefused` ⇒ **退出码 3**，五条全红。
#: 实测三本里两本过期、一本干净，换到干净那本上五条全绿。
#: 一本都不当前时留 `_BOOKS[0]`，由 `box` 里的 `requires_current` 给出**带下一步的跳过**。
def _draft_rels(book_dir: str) -> tuple[str, ...]:
    return tuple(sorted(
        artifact.rel for artifact in corpus.iter_artifacts()
        if artifact.book_dir == book_dir
        and artifact.pattern == 'stage5/ch{nn}.json'
        and '_rounds/' not in artifact.rel
    ))


_BOOK = (next((candidate for candidate in _BOOKS
               if (rels := _draft_rels(candidate))
               and corpus.is_current(candidate, *rels)), None)
         or (_BOOKS[0] if _BOOKS else ''))


# ─────────────────────────────────────────────────────────────────────────────
# 零、一本**真书**的 Book，与一份合成章
# ─────────────────────────────────────────────────────────────────────────────

@pytest.fixture(scope='module')
def book() -> M.Book:
    """A deterministic current-shape book; no local novel output required."""
    return factories.stage5_book()


@pytest.fixture(scope='module')
def entry(book: M.Book) -> dict:
    """一章的 ctx 条目（第 1 章）。"""
    return book.chapters[min(book.chapters)]


def _fire(text: str, entry: dict, book: M.Book) -> set[str]:
    """这一份成稿触发了哪些判据。"""
    return {f.rule for f in checks.chapter_findings(text, entry, book, given='')}


#: 一段**通得过大多数判据**的底稿：完整句、够长、带本书的人名。
def _clean_body(book: M.Book, entry: dict) -> str:
    name = sorted(book.names)[0] if book.names else '她'
    line = f'{name}把杯子放下，没有说话。窗外的雨敲在玻璃上，一下，又一下。'
    lo, _hi = M.target_paragraphs(entry, book)
    return '\n\n'.join(line for _ in range(lo))


def _with_source_material(book: M.Book, entry: dict, *materials: str) -> M.Book:
    """把合成材料放回对应源章；下游保真 HARD 只检查有源文证据的锚。"""
    source = dict(book.source_text)
    ordinal = int((entry.get('from_source_ordinals') or [1])[0])
    source[ordinal] = source.get(ordinal, '') + '\n' + '\n'.join(materials)
    return M.Book(title=book.title, ctx=book.ctx, measurable=book.measurable,
                  source_text=source, secrets=book.secrets)


# ─────────────────────────────────────────────────────────────────────────────
# 一、⭐⭐ 7 条 HARD，**每条一份会让它红的样本**
# ─────────────────────────────────────────────────────────────────────────────

def test_the_seven_hard_judges_are_exactly_the_ones_we_have_samples_for():
    """守卫的守卫：**本层的 HARD 判据集合改了，下面样本就要跟着改。**

    多一条 HARD 而这里没有对应的反例，就又回到了「36 条 HARD 盘上 0 反例样本」那个欠账。
    """
    hard = {r.rule for r in REGISTRY.of_stage('stage5') if r.sev == 'hard'}
    assert hard == {'draft_too_short', 'draft_truncated', 'meta_leak', 'verbatim_missing',
                    'keep_value_missing', 'secret_leak', 'paragraph_budget_missed'}, (
        f'stage5 的 HARD 集合变成了 {sorted(hard)}——'
        f'每新加一条 HARD 就要有一份会让它红的样本（LESSONS 第 3 条）')


def test_draft_too_short_goes_red_and_swallows_everything_else(book, entry):
    """⭐ **< 50 内容字 ⇒ 早退，其余判据一条都不跑。**

    早退是**有意的**：后面每一条判据都建立在「这是一篇文章」之上，对 12 个字跑它们
    只会产出一堆没有意义的数。而「这不是『其余都干净』」由这条 HARD 自己承担。
    """
    fs = checks.chapter_findings('太短了。', entry, book, given='')
    assert [f.rule for f in fs] == ['draft_too_short'], (
        '早退没生效——短稿上跑出了别的判据，那些数全是噪声')


def test_paragraph_budget_has_an_exact_shared_boundary_and_a_red_counterexample(
        book, entry):
    """⭐ prompt 与判据共用同一目标；区间内不拦，少一段/多一段都明确变红。"""
    assert prompts.target_paragraphs(entry, book) == M.target_paragraphs(entry, book)
    lo, hi = M.target_paragraphs(entry, book)
    line = '沈青合上账册，确认门外无人，才把最后一页重新摊开检查。'

    for count in (lo, hi):
        fs = checks._paragraph_budget('\n\n'.join([line] * count), entry, book)
        assert [f.rule for f in fs] == ['paragraph_count_report']
    for count in (lo - 1, hi + 1):
        fs = checks._paragraph_budget('\n\n'.join([line] * count), entry, book)
        assert [f.rule for f in fs] == [
            'paragraph_count_report', 'paragraph_budget_missed']


# ────────── 事实原子：「提不出」与「提错了」是两个层的账 ──────────

def test_a_bad_upstream_atom_is_reported_as_stage2s_bill_not_as_ours():
    """⭐ **反例样本**：原子在**源章原文**里都找不到 ⇒ 抽取那一步错了。

    ⚠ 它此前与「提不出原子」混成一条下游结论、`owner` 记 stage5
    ⇒ 一个 stage2 的 bug 被报成「stage5 这一维够不着」，
    `novel/report/builder.py::remediation_of` 的修复路由因此指向错的层。
    ⚠ 两桶的**措辞也必须分开**：一个说「事件是概括句，提不出可回查的原子」，
    另一个说「原子提错了」——合成一句就没人知道该去修谁。
    """
    entry = {'output_ordinal': 1,
             'characters': [{'char_id': 'c1', 'display_name': '顾堇', 'role': '主角'}],
             'narration': [{'segment': '全章', 'grammatical_person': 'third'}],
             'scenes': [{'slot': 's01', 'required_facts': ['顾堇打翻了茶盏']}]}
    book = M.Book(title='t', ctx={'chapters': [entry]}, measurable={},
                  source_text={1: '这一章原文里根本没有那个人。'}, secrets=[])
    entry['from_source_ordinals'] = [1]
    fs, land = checks.review_chapter_findings('成稿正文。', entry, book)
    assert [f.rule for f in fs] == ['fact_atom_unattested']
    assert land['ident'] == 1 and land['ok'] == 0 and land['missed'] == 0
    assert REGISTRY.get('fact_atom_unattested').owner == 'stage2',         '这条账记的是上游；owner 一改回 stage5，修复路由就又指错层了'


# ───────────── band_drift：翻档与真漂移必须在报告上分得开 ─────────────

def test_cut_distance_separates_a_baseline_hugging_the_edge_from_one_in_the_middle():
    """⭐ 这是 `band_drift` 那条 detail 存在的全部理由。

    档位粗（句子切 16/22/30），基线 16.4 时成稿只降几个百分点就翻档，
    而报告上「短句 → 极短句」与一次真正的形态崩塌**长得一模一样**。
    ⚠ 本条只断言**读数分得开**，不断言「多近算贴边」——那个数没有标定过。
    """
    from novel.core.bands import PARA_BANDS, SENT_BANDS, band, cut_distance
    assert band(16.4, SENT_BANDS) == band(19.0, SENT_BANDS)      # 同一档
    assert cut_distance(16.4, SENT_BANDS) < cut_distance(19.0, SENT_BANDS)
    assert cut_distance(55.0, PARA_BANDS) == 15.0
    assert cut_distance(5.0, [(None, '兜底')]) is None, '一条档边都没有时不许编一个数'


def test_band_drift_says_the_numbers_not_just_the_band_words(book, entry):
    """`detail` 要带上本书基线、本章实测、比值与离档边的距离。"""
    body = chr(10).join(['短。', '', '很短。', '', '还是短。'])
    got = list(checks._band_drift(body, book))
    for f in got:
        assert '字' in f.detail and '×' in f.detail and '离最近档边' in f.detail, f.detail
    # ⚠ 凭据仍按「哪一维」区分两个实例——`_converge` 的收敛差集靠它，别破坏。
    assert all(f.evidence[0] in ('句子形态', '段落形态') for f in got)


# ─────────────────────────────────────────────────────────────────────────────
# 章尾交付 —— `R-7.9` 的第二个守卫。⭐ 反例在这里，盘上零样本
# ─────────────────────────────────────────────────────────────────────────────

def _hook_book(facts, *, slots=('s01', 's02')):
    """造一本只含「结尾目标 + 分段」的书。判据吃 dict ⇒ 反例配得出来。"""
    entry = {
        'output_ordinal': 1,
        'narration': [{'segment': '全章', 'grammatical_person': 'third'}],
        'characters': [{'char_id': 'c1', 'display_name': '顾堇', 'role': '主角'}],
        'scenes': [{'slot': sl} for sl in slots],
        'end_hook_goal': {'intent': 'open', 'facts': list(facts)},
    }
    book = M.Book(title='t', ctx={'chapters': [entry]}, measurable={},
                  source_text={}, secrets=[])
    return book, entry


def _segs(*pairs):
    return [{'slot': sl, 'text': tx} for sl, tx in pairs]


def test_an_end_hook_surface_hit_in_the_last_slot_still_needs_semantic_review():
    book, entry = _hook_book(['顾堇被系统提示'])
    fs = checks._end_hook_landing(
        entry, book, _segs(('s01', '她醒了。'), ('s02', '顾堇开口。')))
    assert [f.rule for f in fs] == ['end_hook_surface_report', 'end_hook_unver']
    assert 'tail:顾堇' in fs[0].evidence
    assert '不能证明' in fs[1].detail


def test_end_hook_surface_report_distinguishes_other_slot_and_whole_chapter_absence():
    """字面探针只报告分布：别处命中、整章缺失都不能自动定罪。"""
    book, entry = _hook_book(['顾堇被系统提示'])
    fs = checks._end_hook_landing(
        entry, book, _segs(('s01', '顾堇开口。'), ('s02', '她愣住。')))
    assert [f.rule for f in fs] == ['end_hook_surface_report', 'end_hook_unver']
    assert 'other:顾堇' in fs[0].evidence
    gone = checks._end_hook_landing(
        entry, book, _segs(('s01', '她醒了。'), ('s02', '她愣住。')))
    assert [f.rule for f in gone] == ['end_hook_surface_report', 'end_hook_unver']
    assert 'absent:顾堇' in gone[0].evidence


def test_end_hook_pronoun_delivery_and_same_name_collision_are_both_unver():
    """代词可能完成交付，同名也可能假命中；两边都只能给字面读数。"""
    book, entry = _hook_book(['顾堇收到密令'])
    pronoun = checks._end_hook_landing(
        entry, book, _segs(('s01', '门外有脚步。'), ('s02', '她收到了密令。')))
    collision = checks._end_hook_landing(
        entry, book, _segs(('s01', '门外有脚步。'), ('s02', '顾堇只是写在旧名册上。')))
    assert all(fs[-1].rule == 'end_hook_unver' for fs in (pronoun, collision))
    assert 'absent:顾堇' in pronoun[0].evidence
    assert 'tail:顾堇' in collision[0].evidence


def test_the_three_ways_of_not_being_able_to_judge_say_which_one_it_was():
    """⚠⚠ 三种「够不着」必须分得开，而且**都不是通过**（`docs/LESSONS.md` 第 4 条）。

    `noatom` 那一桶预计不小：`CtxGoal.facts` 是人读事实，很可能既无数字也无专名。
    把它当成通过，就是 `R-7.22` 那两桶「统计出来了但没判过」的翻版。
    """
    book, entry = _hook_book(['顾堇被系统提示'])
    no_seg = checks._end_hook_landing(entry, book, None)
    lost = checks._end_hook_landing(entry, book, _segs(('s01', '顾堇开口。')))
    book2, entry2 = _hook_book(['她心里松了一口气'])
    noatom = checks._end_hook_landing(
        entry2, book2, _segs(('s01', 'x'), ('s02', 'y')))
    whys = [f[1].detail for f in (no_seg, lost, noatom)]
    assert all([f.rule for f in fs] == ['end_hook_surface_report', 'end_hook_unver']
               for fs in (no_seg, lost, noatom))
    assert len(set(whys)) == 3, f'三种够不着说了同一句话：{whys}'


def test_no_end_hook_goal_or_no_plan_segments_is_explicitly_unver():
    """上游没给目标时明确留下零分母原因，不能渲染成通过。"""
    book, entry = _hook_book([])
    fs = checks._end_hook_landing(entry, book, _segs(('s01', 'x')))
    assert [f.rule for f in fs] == ['end_hook_surface_report', 'end_hook_unver']
    assert 'no_goal_or_plan' in fs[0].evidence


def test_end_hook_surface_and_semantic_boundary_are_identity_na():
    """⚠ 恒等基线上**源章没有计划切出来的 slot**，「最后一段」这个概念不存在
    ⇒ 不登记进 `IDENTITY_NA` 的话它在那一路恒 `unver`，
    也就是 `measure.py::Book` docstring 警告的「一盏保证会亮的绿灯」。"""
    assert {'end_hook_surface_report', 'end_hook_unver'} <= checks.IDENTITY_NA


# ─────────────────────────────────────────────────────────────────────────────
# 叙述人称 —— `R-7.9` 的第一个守卫，反例在这里
# ─────────────────────────────────────────────────────────────────────────────

def _person_book(*persons_and_surfaces) -> tuple[M.Book, list[dict]]:
    """按 `(人称, 自称字面)` 造一本只含 narration 的书。**判据吃 dict，所以反例配得出来。**"""
    chapters = [
        {'output_ordinal': i,
         'narration': [{'segment': '全章', 'grammatical_person': person,
                        'pronoun_surfaces': surfaces}]}
        for i, (person, surfaces) in enumerate(persons_and_surfaces, 1)]
    book = M.Book(title='t', ctx={'chapters': chapters}, measurable={},
                  source_text={}, secrets=[])
    return book, chapters


def test_the_book_supplies_its_own_first_person_surfaces_and_never_a_hardcoded_list():
    """⭐ 右锚是**上游给的**，不是代码里写死的一张「我/咱/本宫/朕」。

    ⚠ 并集取**书级**：声明 `third` 的那一段自己的 `pronoun_surfaces` 恒为 `null`
    （第三人称交空数组合法），拿同段那一份当锚 ⇒ **被测的那一类永远没有锚、判据恒不执行**。
    """
    book, _ = _person_book(('first', ['咱']), ('third', None), ('first', ['奴家']))
    assert book.first_person_surfaces == frozenset({'咱', '奴家'})
    assert M.Book(title='t', ctx={'chapters': []}, measurable={}, source_text={},
                  secrets=[]).first_person_surfaces == frozenset()


def test_narration_person_drift_catches_a_third_person_chapter_that_slips_into_first():
    """⭐ **反例样本**：这正是盘上真出过的那一次（上游误判 `third`，写手忠实执行）。"""
    book, chapters = _person_book(('first', ['我']), ('third', None))
    fs = checks._narration_person('她确实知道。我不信他。她也得确保。', chapters[1], book)
    assert [f.rule for f in fs] == ['narration_person_drift']
    assert '「我」×1' in fs[0].detail


def test_narration_person_drift_catches_a_first_person_chapter_without_any_self_reference():
    """⭐ **反例样本**：声明第一人称，叙述层里一个自称都没有。"""
    book, chapters = _person_book(('first', ['我']))
    assert [f.rule for f in checks._narration_person('她走进屋。他没抬头。', chapters[0], book)]         == ['narration_person_drift']
    assert checks._narration_person('我走进屋。他没抬头。', chapters[0], book) == []


def test_narration_person_only_ever_looks_at_the_narration_layer():
    """⚠⚠ **台词里的人称是说话人的，不是叙述者的**——两个方向的假阳都要挡住。

    不剔除直接引语的话：声明 `first` 那一支会被角色台词里的「我」**假阴**（判据静默失效），
    声明 `third` 那一支会被同一个「我」**假阳**（每一章都红）。
    """
    book, chapters = _person_book(('first', ['我']), ('third', None))
    # 「我」只在台词里 ⇒ 第一人称那一支照样要出声（叙述层确实没有自称）
    assert [f.rule for f in checks._narration_person(
        '她说：「我不去。」他没抬头。', chapters[0], book)] == ['narration_person_drift']
    # 同一句话，第三人称那一支必须**沉默**
    assert checks._narration_person('她说：「我不去。」他没抬头。', chapters[1], book) == []


def test_narration_person_report_says_out_loud_when_it_could_not_judge():
    """⚠ 「0 命中」的三种来源必须分得开：对得上 / 没给 narration / 没有锚。"""
    book, chapters = _person_book(('third', None))          # 全书没有第一人称 ⇒ 池空
    only = checks._narration_person_report('她走了。', chapters[0], book)[0]
    assert only.rule == 'narration_person_report' and '未判' in only.detail
    none = checks._narration_person_report('她走了。', {'output_ordinal': 9}, book)[0]
    assert '上游没有 narration' in none.detail


def test_second_person_and_multiple_narration_are_explicitly_unver():
    second, second_rows = _person_book(('second', ['你']))
    multi_entry = {
        'output_ordinal': 1,
        'narration': [
            {'segment': '前半', 'grammatical_person': 'first', 'pronoun_surfaces': ['我']},
            {'segment': '后半', 'grammatical_person': 'third', 'pronoun_surfaces': None},
        ],
    }
    multi = M.Book(title='t', ctx={'chapters': [multi_entry]}, measurable={},
                   source_text={}, secrets=[])
    second_report = checks._narration_person_report('你走进屋。', second_rows[0], second)[0]
    multi_report = checks._narration_person_report('我走进屋。她没回头。', multi_entry, multi)[0]
    assert '第二人称尚未标定' in second_report.detail
    assert '多 narration 但正文没有对应段界' in multi_report.detail
    assert checks._narration_person('你走进屋。', second_rows[0], second) == []
    assert checks._narration_person('我走进屋。', multi_entry, multi) == []


def test_every_chapter_finding_carries_its_output_ordinal(book, entry):
    """逐章 finding 自己携带章号，不再靠 ReviewChapter 外层猜它属于谁。"""
    ordinal = int(entry['output_ordinal'])
    short = checks.chapter_findings('太短了。', entry, book, given='')
    full = checks.chapter_findings(
        _clean_body(book, entry), entry, book, given='')
    assert short and full
    assert {hit.output_ordinal for hit in [*short, *full]} == {ordinal}


def test_draft_truncated_goes_red_on_a_missing_final_punctuation(book, entry):
    """⭐ 结尾没有终止标点 ⇒ 疑似截断。"""
    body = _clean_body(book, entry)
    assert 'draft_truncated' not in _fire(body, entry, book)
    assert 'draft_truncated' in _fire(body.rstrip('。') + '他忽然', entry, book)


@pytest.mark.parametrize('message', [
    '【好感度已降低。】',
    '[好感度已降低。]',
    '［好感度已降低。］',
])
def test_draft_truncated_accepts_a_closed_interface_message(book, entry, message):
    """三种界面方括号的闭合符都表示消息已经完整结束。"""
    body = _clean_body(book, entry).rstrip('。') + message
    assert 'draft_truncated' not in _fire(body, entry, book)


@pytest.mark.parametrize('shell', ['第三章 雨夜', '本章共 8 拍，先写第一拍。',
                                   '## 小标题', '正文：她把杯子放下。'])
def test_meta_leak_goes_red_on_narrative_shell(book, entry, shell):
    """⭐ 叙述外壳（章节标题 / 编号 / 任务术语）**不该进正文**。

    ⚠ 四种形态各测一次：这条判据是**词表类**（`wordlist=True`），
    而一张只被单一形态验过的词表，与一张没被验过的词表没有区别。
    """
    body = shell + '\n' + _clean_body(book, entry)
    assert 'meta_leak' in _fire(body, entry, book)


def test_verbatim_missing_goes_red_when_the_mandated_line_is_gone(book, entry):
    """⭐ 【原样写入】那一组**要求逐字**，改一个字就废。"""
    line = '这句话一个字都不能改，写手必须原样放回去。'
    e = dict(entry, scenes=[{**(entry['scenes'][0]), 'verbatim': [line]}])
    anchored = _with_source_material(book, e, line)
    body = _clean_body(book, entry)
    assert 'verbatim_missing' in _fire(body, e, anchored)
    assert 'verbatim_missing' not in _fire(body + line, e, anchored)


@pytest.mark.parametrize('rewritten', [
    '这位工子的伤口已经溃烂了整整三日无人问津。',
    '这位公子的伤口已经溃烂了整整三日无人问津。',
])
def test_verbatim_typo_is_a_separate_conclusion_not_a_miss(
        book, entry, rewritten):
    """⚠ **一两个字之差按错别字放行**，但**单独留痕**。

    原文里本来就有错字（实测「这**为**公子伤口溃烂…」），写手写成「这**位**公子…」
    ——那是**纠正**不是改写。要求它复现原文的错别字是荒谬的。
    """
    line = '这为公子的伤口已经溃烂了整整三日无人问津。'
    e = dict(entry, scenes=[{**(entry['scenes'][0]), 'verbatim': [line]}])
    anchored = _with_source_material(book, e, line)
    fired = _fire(_clean_body(book, entry) + rewritten, e, anchored)
    assert 'verbatim_typo' in fired and 'verbatim_missing' not in fired


def test_verbatim_three_character_rewrite_is_still_missing(book, entry):
    """三字及以上已经不是局部错别字修正，仍须走 missing。"""
    line = '这为工子的伤口已经溃烂了整整三日无人问津。'
    e = dict(entry, scenes=[{**(entry['scenes'][0]), 'verbatim': [line]}])
    rewritten = '这位年轻人的伤口已经溃烂了整整三日无人问津。'
    anchored = _with_source_material(book, e, line)
    fired = _fire(_clean_body(book, entry) + rewritten, e, anchored)
    assert 'verbatim_missing' in fired and 'verbatim_typo' not in fired


def test_verbatim_split_by_a_speech_tag_is_allowed_but_still_recorded(book, entry):
    """⚠ `「…，」他说，「…」` 是中文小说的**标准写法** ⇒ 放行；

    但「一个字都没动」与「切成两截后逐字出现」不是同一件事，**不能都走 `[OK]`**。
    """
    line = '「你要是真的想走，那就走吧，我不拦着你，一次都不会。」'
    e = dict(entry, scenes=[{**(entry['scenes'][0]), 'verbatim': [line]}])
    split = '「你要是真的想走，」他说，「那就走吧，我不拦着你，一次都不会。」'
    anchored = _with_source_material(book, e, line)
    fired = _fire(_clean_body(book, entry) + split, e, anchored)
    assert 'verbatim_split' in fired and 'verbatim_missing' not in fired


def test_real_stage5_report_keeps_verbatim_caveat_in_note_only(book, entry):
    """FC-14：不用手搓 finding，从真实保真检查一路组装到报告行。"""
    from collections import defaultdict

    from novel.prov import FreshnessReport
    from novel.report import build, render

    line = '「你要是真的想走，那就走吧，我不拦着你，一次都不会。」'
    e = dict(entry, scenes=[{**entry['scenes'][0], 'verbatim': [line]}])
    split = '「你要是真的想走，」他说，「那就走吧，我不拦着你，一次都不会。」'
    anchored = _with_source_material(book, e, line)
    findings = checks.chapter_findings(
        _clean_body(book, entry) + split, e, anchored, given='')
    by_rule = defaultdict(list)
    for hit in findings:
        by_rule[hit.rule].append(hit)
    outcomes = checks.check(
        chapter_findings_by_rule=dict(by_rule), scanned={}, unver_empty={}, phase='write')
    report = build(
        sid='7615069877019300633', node='stage5.write',
        freshness=FreshnessReport('unknown', 'no_inputs'), outcomes=outcomes,
    )
    row = next(item for item in report.rules if item.rule == 'verbatim_split')
    assert row.findings
    assert row.findings[0].output_ordinal == int(entry['output_ordinal'])
    assert '本体裁的标准写法' not in row.findings[0].detail
    assert '已放行' not in row.findings[0].detail
    assert row.note and '中文小说的标准写法' in row.note
    assert f'说明：{row.note}' in render(report)


def test_stage5_check_report_aggregates_band_drift_without_losing_chapter_state():
    """Review 保留逐章事实；CheckReport/CLI/API 只外发一条无损书级结论。"""
    from types import SimpleNamespace

    from contracts.schema.common import WontFixNote
    from contracts.schema.report import finding
    from novel.prov import FreshnessReport
    from novel.report import build
    from novel.stages.stage5 import review

    first = checks.with_output_ordinal([finding(
        'band_drift', detail='句子形态：短句→中句',
        evidence=['句子形态', '短句', '中句'],
        converge='same_draft', unresolved_rounds=1,
        wontfix=WontFixNote(why='本章故意放慢', at='review_2'),
    )], 2)[0]
    first = review._downgrade(first, actionable=False)
    second = checks.with_output_ordinal([finding(
        'band_drift', detail='段落形态：短段→极短段',
        evidence=['段落形态', '短段', '极短段'],
        converge='unresolved_2', unresolved_rounds=2,
    )], 7)[0]
    review_artifact = SimpleNamespace(
        chapters=[
            SimpleNamespace(output_ordinal=2, relpath='stage5/ch02.json', findings=[first]),
            SimpleNamespace(output_ordinal=7, relpath='stage5/ch07.json', findings=[second]),
        ],
        book=[],
        baselines={'band_drift': '2/2', '_fact_landing': '0/0 事实有落点'},
        baselines_identity={'band_drift': '0/2'},
    )
    report = build(
        sid='7615069877019300633', node='stage5.review',
        freshness=FreshnessReport('unknown', 'no_inputs'),
        outcomes=review._outcomes(review_artifact, None),
    )

    row = next(item for item in report.rules if item.rule == 'band_drift')
    assert len(row.findings) == 1
    aggregate = row.findings[0]
    assert aggregate.output_ordinal is None
    assert '2/2 章命中' in aggregate.detail and '[2, 7]' in aggregate.detail
    assert aggregate.unresolved_rounds == 2 and aggregate.converge.startswith('mixed(')
    details = [json.loads(value) for value in aggregate.evidence]
    assert [value['output_ordinal'] for value in details] == [2, 7]
    assert details[0]['wontfix'] == {'why': '本章故意放慢', 'at': 'review_2'}
    assert details[0]['converge'] == 'same_draft'
    assert details[1]['converge'] == 'unresolved_2'


def test_check_only_overlays_a_wontfix_written_after_the_latest_review(monkeypatch):
    """旁路决定账落下后立刻生效，不要求额外生成一份 Review 才能从 inbox 销账。"""
    from types import SimpleNamespace

    from contracts.schema.common import WontFixEntry
    from contracts.schema.report import finding
    from contracts.schema.stage5 import Review, ReviewChapter
    from novel.prov import FreshnessReport
    from novel.stages.stage5 import review

    hit = checks.with_output_ordinal([finding(
        'fact_landing', detail='一个必落事实没有落点', evidence=['事实摘要'],
        converge='same_draft', unresolved_rounds=0,
    )], 6)[0]
    row = ReviewChapter(
        output_ordinal=6, from_source_ordinals=[6], budget_chars=100,
        source_chars=100, relpath='stage5/ch06.json', draft_round=1,
        draft_sha1='a' * 10, draft_text_sha1='b' * 10, chars=100,
        findings=[hit],
    )
    artifact = Review.model_construct(
        sources=None, judged={}, chapters=[row], book=[],
        baselines={'_fact_landing': '0/1 事实有落点'},
        baselines_identity={}, counts={}, provenance=None,
    )
    entry = WontFixEntry(
        output_ordinal=6, rule=hit.rule, evidence_key=hit.evidence_key,
        why='故意保留留白', detail=hit.detail, at='review_1',
    )
    monkeypatch.setattr(review, 'load_wontfix', lambda _store: {
        review.wontfix_key(6, hit.rule, hit.evidence_key): entry,
    })
    monkeypatch.setattr(review, 'review_seqs', lambda _store: [])
    store = SimpleNamespace(
        freshness=lambda _rel: FreshnessReport('unknown', 'no_inputs'))
    ctx = SimpleNamespace(sid='book-1', node='stage5.review')

    report = review._report_of_review(ctx, store, artifact)

    finding_row = next(item for item in report.rules if item.rule == 'fact_landing')
    assert finding_row.findings[0].wontfix is not None
    assert finding_row.findings[0].wontfix.why == '故意保留留白'
    assert finding_row.findings[0].actionable is False


def test_review_material_denominators_follow_current_writer_context_scenes():
    """全书汇总必须读 Stage4 v2 的 ``scenes``，不能退回已下线的 ``segments``。"""
    from types import SimpleNamespace

    from contracts.schema.stage5 import Review
    from novel.stages.stage5 import review

    artifact = Review.model_construct(
        sources=None, judged={}, chapters=[], book=[],
        baselines={
            'verbatim_missing': '0/0',
            'keep_value_missing': '0/0',
            'must_appear_missing': '0/0',
            '_fact_landing': '0/0 事实有落点',
        },
        baselines_identity={
            'verbatim_missing': '0/0',
            'keep_value_missing': '0/0',
            'must_appear_missing': '0/0',
        },
    )
    current_ctx = {
        'chapters': [{'scenes': [{
            'verbatim': ['逐字保留'],
            'keep_value': ['保留 30 岁'],
            'must_appear': ['旧钥匙'],
        }]}],
    }

    outcomes = review._outcomes(
        artifact, SimpleNamespace(ctx=current_ctx, secrets=[]))

    by_rule = {row.rule: row for row in outcomes}
    assert by_rule['verbatim_missing'].unver_why is None
    assert by_rule['keep_value_missing'].unver_why is None
    assert by_rule['must_appear_missing'].unver_why is None
    assert not by_rule['verbatim_missing'].findings
    assert not by_rule['keep_value_missing'].findings
    assert not by_rule['must_appear_missing'].findings
    assert by_rule['verbatim_missing'].scanned.startswith('1 句【原样写入】')
    assert by_rule['keep_value_missing'].scanned.startswith('1 处【数值不得改动】')
    assert by_rule['must_appear_missing'].scanned.startswith('1 个【必须在场】')


def test_keep_value_missing_compares_the_value_not_the_literal(book, entry):
    """⭐⭐ **比值不比字面**：下发面「30岁」、成稿「三十岁」＝**满足**。

    ⚠ 这条是被一次**全量误报**逼出来的：原型只搜阿拉伯字面，某章因此被**整章拦下**
    ——而契约发给写手的原话是「数字必须保住，**措辞随你**」，写手做的正是它让做的事。
    """
    material = '她今年30岁了。'
    e = dict(entry, scenes=[{**(entry['scenes'][0]), 'keep_value': [material]}])
    anchored = _with_source_material(book, e, material)
    body = _clean_body(book, entry)
    assert 'keep_value_missing' in _fire(body, e, anchored)
    assert 'keep_value_missing' not in _fire(body + '她今年三十岁了。', e, anchored), (
        '「三十岁」没被认成 30——判据退回了字面比对，那会把一次正确的改写判成丢数字')
    assert 'keep_value_missing' not in _fire(body + '她今年30岁了。', e, anchored)


def test_unattested_material_is_stage2s_bill_and_never_a_stage5_missing_target(book, entry):
    """源文不存在的锚不能沿 UNIQUE 路由付费塞进正文。"""
    fake = '源章从未出现过的伪造原话。'
    e = dict(entry, scenes=[{**entry['scenes'][0], 'verbatim': [fake]}])
    fired = _fire(_clean_body(book, entry), e, book)
    assert 'material_anchor_unattested' in fired
    assert 'verbatim_missing' not in fired
    assert REGISTRY.get('material_anchor_unattested').owner == 'stage2'


def test_secret_leak_goes_red_and_the_evidence_is_the_drafts_own_bytes(book, entry):
    """⭐ 提前点破本章不该揭示的事。

    ⚠⚠ **凭据必须是成稿自己的字节，不是 secret 的字节**：命中的片段来自 `secret.fact`，
    把它当凭据发回给写手就是**把秘密告诉它**。
    """
    # 旧实现在 fact 侧剥「一 / 三」、成稿侧不剥，这份原样转抄
    # 会系统性假阴。反例要直接保留这两类数字词，不许退回不含数字的 happy path。
    fact = '她其实一直都知道那三笔钱是从公司账上挪出来的'
    b = M.Book(title=book.title, ctx=book.ctx, measurable=book.measurable,
               source_text=book.source_text,
               secrets=[{'secret_id': 'secret_x', 'fact': fact,
                         'knowledge_events': [], 'setup': {'kind': 'cold'},
                         'first_reveal_chapter': 9}])
    body = _clean_body(book, entry) + fact + '。'
    fs = checks.chapter_findings(body, entry, b, given='')
    leak = [f for f in fs if f.rule == 'secret_leak']
    assert leak, 'secret_leak 没红——转抄式泄底是它唯一挡得住的那一类'
    assert all(ev in body for ev in leak[0].evidence), (
        '凭据不是成稿自己的字节——把 secret 的原文发回写手就是把秘密告诉它')


def test_secret_literal_leak_uses_the_authoritative_reveal_timeline(book, entry):
    """只靠权威 reveal timeline 拦截揭示章之前的字面泄密。"""
    fact = '她其实一直都知道那三笔钱是从公司账上挪出来的'
    synthetic = M.Book(
        title=book.title, ctx=book.ctx, measurable=book.measurable,
        source_text=book.source_text,
        secrets=[{
            'secret_id': 'secret_x', 'fact': fact,
            'knowledge_events': [], 'setup': {'kind': 'cold'},
            'first_reveal_chapter': 9,
        }],
    )
    before = dict(entry, output_ordinal=1)
    at_reveal = dict(entry, output_ordinal=9)
    body = _clean_body(book, entry) + fact + '。'
    assert 'secret_leak' in _fire(body, before, synthetic)
    assert 'secret_leak' not in _fire(body, at_reveal, synthetic)


def test_secret_timeline_uses_the_same_number_ruler(book):
    """揭示章原样写出带数字的 fact 时，不得误报「连揭示章都没命中」。"""
    fact = '她其实一直都知道他是三皇子'
    secret = {'secret_id': 'secret_x', 'fact': fact, 'knowledge_events': [],
              'setup': {'kind': 'cold'}, 'first_reveal_chapter': 9}
    synthetic = M.Book(title=book.title, ctx=book.ctx, measurable=book.measurable,
                       source_text=book.source_text, secrets=[secret])
    fs = checks.book_findings(synthetic, rows=[], bodies={9: fact},
                              inv_numbers=[], inv_names=[])
    timeline = next(f for f in fs if f.rule == 'secret_timeline')
    assert '出现于 [9]' in timeline.detail
    assert '没有判别力' not in timeline.detail


def test_dialogue_loss_is_single_arm_information_not_a_hard_gate(book, entry):
    """闭卷写手不能据源文对白数补写内容；这里只留逐章读数与人工线索。"""
    src_ord = (entry.get('from_source_ordinals') or [1])[0]
    src = '「一。」「二。」「三。」「四。」「五。」「六。」「七。」「八。」' * 3
    b = M.Book(title=book.title, ctx=book.ctx, measurable=book.measurable,
               source_text={**book.source_text, src_ord: src}, secrets=book.secrets)
    e = dict(entry, source_chars=100, budget_chars=100)
    body = _clean_body(book, entry)          # 一句直接引语都没有
    fired = _fire(body, e, b)
    assert {'dialogue_report', 'dialogue_fewer'} <= fired
    assert 'dialogue_halved' not in fired
    assert 'dialogue_halved' not in {r.rule for r in REGISTRY.of_stage('stage5')}


# ─────────────────────────────────────────────────────────────────────────────
# 二、⭐ 一张表，两相共用
# ─────────────────────────────────────────────────────────────────────────────

def test_the_two_phases_read_one_and_the_same_rule_table():
    """⭐ **落盘闸门与全书验收相调同一个 `chapter_findings`。**

    原型把书级那 8 条住在另一个模块的 `RULES6` 里，靠一个 `_merged_rules()` 合起来，
    而共享层得**反向 lazy-import** 才拿得到——一张表拆成两半的全部成本都在那个环上。
    """
    import inspect
    from novel.stages.stage5 import review, write
    assert 'chapter_findings' in inspect.getsource(write._judge)
    assert 'chapter_findings' in inspect.getsource(review.build_review)


def test_every_registered_rule_has_exactly_one_producer():
    """⭐ 50 条判据**一条不多一条不少**地被实现覆盖。

    ⚠ 少列一条，那一条在报告上就变 `skipped`——而 `skipped`（实现未被调用）与
    「跑了没命中」是**两种颜色**；多列一条，`builder` 会抛（那是「注册了没实现」的镜像病）。
    """
    reg = {r.rule for r in REGISTRY.of_stage('stage5')}
    mine = [*checks._FROM_CHAPTER, *checks._FROM_REVIEW]
    assert len(mine) == len(set(mine)), '同一条判据被列了两次'
    assert set(mine) == reg, (f'注册了没实现：{sorted(reg - set(mine))}；'
                              f'实现了没注册：{sorted(set(mine) - reg)}')
    # secret_unknown 随重复 must_not_reveal 清单退役，现役共 50 条。
    assert len(reg) == 50


def test_every_unver_rule_must_say_why_it_cannot_check():
    """`sev='unver'` 的那几条**必须**走 `unverifiable(rule, why)`。

    `builder._status_of` 会拒收一条 `sev=unver` 却没说出「这次为什么够不着」的判据——
    **`unver` 是判据的边界，而边界要说得出在哪。**
    """
    unver = {r.rule for r in REGISTRY.of_stage('stage5') if r.sev == 'unver'}
    assert unver == set(checks._UNVER) and len(unver) == 5


def test_the_badge_can_never_be_pass_on_this_layer():
    """⭐⭐ **本层徽章结构上永远到不了 `pass`**，而那是对的。

    5 条 `unver`，其中 `must_hold_unver` **每本都产**（全书那一组按定义查不了文面）
    ⇒ 最好情况是 `amber` + 退出码 0。**与 stage4 相反**（那一层没有一条 `unver`）。
    """
    from contracts.schema.report import RuleLine, badge_of
    lines = [RuleLine(rule=r.rule, sev=r.sev, status=('unver' if r.sev == 'unver' else 'ok'),
                      item=r.item, requirement=r.requirement,
                      why='查不了' if r.sev == 'unver' else None)
             for r in REGISTRY.of_stage('stage5')]
    assert badge_of(lines) == 'amber', '一份「全部通过」的 stage5 报告也只能是 amber'


# ─────────────────────────────────────────────────────────────────────────────
# 三、唯一闭卷写作面
# ─────────────────────────────────────────────────────────────────────────────

def test_stage5_has_one_closed_book_material_surface(book, entry):
    """没有 selector / source-text 开关，闭卷由接口形状保证。"""
    import inspect
    from novel.stages.stage5 import write
    assert list(inspect.signature(prompts.build_material).parameters) == ['entry', 'book']
    assert not hasattr(write, '_Arm') and not hasattr(write, '_ARMS')
    material = prompts.build_material(entry, book)
    assert not hasattr(material, 'source_text')
    assert all(not block.startswith('── 源章原文') for block in material.blocks)


def test_the_closed_book_arm_never_touches_the_source_text(book, entry):
    """⚠⚠ **「闭卷」这件事由 `build_material` 保证，不是由 system prompt 里那句话保证。**"""
    material = prompts.build_material(entry, book)
    src = M.chapter_source(entry, book.source_text)
    assert len(src) > 200, '这一章源文太短，这条断言失去判别力'
    assert src[:120] not in material.given


def test_stage5_book_has_no_qualitative_style_surface(book):
    """Book 结构上只拿正文机械投影，不能再把 Stage3 整包偷偷递给 prompt。"""
    import dataclasses

    assert 'style' not in {field.name for field in dataclasses.fields(book)}
    assert set(book.measurable) == {
        'avg_sentence_chars', 'avg_paragraph_chars', 'paragraph_sep_chars',
        'dialogue_ratio_applicable', 'dialogue_ratio', 'quote_styles',
        'chapter_chars', 'per_chapter_avg_sentence',
    }
    assert set(book.measurable['chapter_chars']) == {'avg'}
    assert set(book.measurable['per_chapter_avg_sentence']) == {'min', 'max'}


def test_source_overlap_high_reports_excess_surface_overlap(book, entry):
    """canonical 闭卷稿逐章越过源文重合上限时必须出声。"""
    src = M.chapter_source(entry, book.source_text)
    assert 'source_overlap_high' in _fire(src, entry, book)


# ─────────────────────────────────────────────────────────────────────────────
# 四、下发面的三条纪律
# ─────────────────────────────────────────────────────────────────────────────

def test_the_downsend_band_is_the_very_one_the_judges_use(book, entry):
    """⭐ 发给写手的区间 == 判据用的区间。

    ⚠ LESSONS 第 9 条那个形状：**模型按 A 写、校验按 B 判，谁也不出声。**
    原型在 `DRAFT_BAND` 之外又乘了一次 ±10%，实际执行 0.81~1.21×——而两端极值
    恰好卡在旧口径内侧、各差不到 1%，**两条判据一次都没响**。
    """
    lo, hi = prompts.target_band(entry)
    assert M.target_range(entry) == (lo, hi)
    mat = prompts.build_material(entry, book)
    assert f'**{lo}~{hi} 字**' in mat.given


def test_the_secret_ids_never_reach_the_writer(book, entry):
    """秘密边界由 Stage2 时间线执行；写手面只接收已开闸的人话事实。"""
    given = prompts.build_material(entry, book).given
    assert 'must_not_reveal' not in given
    assert all(str(secret['secret_id']) not in given for secret in book.secrets)


def test_paid_writer_prompt_excludes_global_source_spans(monkeypatch, tmp_path):
    """Stage5 付费入口必须经共用 ``load_book`` 剥掉正式取证字节。"""
    import copy
    from types import SimpleNamespace

    from novel.core.evidence import iter_global_evidence_slots
    from novel.stages.stage5 import review, write
    from tests.test_stage2 import global_grounding

    seed = factories.stage5_book()
    grounding = global_grounding()
    markers: list[str] = []
    coordinates: list[int] = []
    for index, (_claim_path, owner, field) in enumerate(
        iter_global_evidence_slots(grounding), 1,
    ):
        marker = f'STAGE5_EVIDENCE_ONLY_{index}'
        start = 934_567_800 + index * 100
        owner[field] = {
            'chapter_ordinal': 1,
            'source': {
                'text': marker,
                'start': start,
                'end': start + len(marker),
            },
        }
        markers.append(marker)
        coordinates.extend((start, start + len(marker)))

    class Dumped:
        def __init__(self, value):
            self.value = value

        def model_dump(self, **_kwargs):
            return copy.deepcopy(self.value)

    attested = ' '.join(
        str(row.get('canonical') or '')
        for row in ((grounding.get('entities') or {}).get('characters') or {}).values())
    stored = {
        review.MAIN_TEXT: SimpleNamespace(root=[
            SimpleNamespace(
                chapter_ordinal=ordinal,
                text=text + ('\n' + attested if ordinal == 1 else ''),
            )
            for ordinal, text in seed.source_text.items()
        ]),
        review.GLOBAL_GROUNDING: Dumped(grounding),
        review.STYLE_GROUNDING: Dumped({'measurable': {'body': seed.measurable}}),
        review.WRITER_CONTEXT: Dumped(seed.ctx),
    }

    class Store:
        @staticmethod
        def path(rel):
            return tmp_path / rel

        @staticmethod
        def exists(rel):
            return rel in stored

        @staticmethod
        def read(_model, rel):
            return stored[rel]

    monkeypatch.setattr(write, 'ArtifactStore', lambda _ctx: Store())
    _system, user, material = write.build_prompt(
        SimpleNamespace(title=seed.title), '1')

    paid_material = user + '\n' + material.given
    assert markers and all(marker not in paid_material for marker in markers)
    assert all(str(coordinate) not in paid_material for coordinate in coordinates), \
        'SourceSpan 的 start/end 坐标进了 Stage5 付费 prompt'


def test_current_trim_audit_absorbs_the_duplicate_length_over_action():
    """同一份当前稿已有 hash-bound trim audit 时，判据侧不再产第二条 length_over。"""
    from contracts.schema.report import finding
    from contracts.schema.stage5 import ReviewChapter
    from novel.stages.stage5 import review

    hit = checks.with_output_ordinal([finding(
        'length_over', detail='当前 1800 字，预算 1200 字', evidence=['1800', '1200'],
    )], 3)[0]
    row = ReviewChapter(
        output_ordinal=3, from_source_ordinals=[7], budget_chars=1200,
        source_chars=1500, findings=[hit],
    )

    untouched = review._merge_length_over_into_trim([row], set())[0]
    merged = review._merge_length_over_into_trim([row], {3})[0]
    assert untouched.findings == [hit] and untouched.findings[0].actionable is True
    assert merged.output_ordinal == 3 and merged.findings == []


# ─────────────────────────────────────────────────────────────────────────────
# 五、`--wontfix` 的三条约束
# ─────────────────────────────────────────────────────────────────────────────

@pytest.fixture
def box():
    """一份能跑 `stage5.review` 的当前契约沙箱（跑完即删）。

    ⚠⚠ **必须经 `tests/sandbox.py::Sandbox`，不许在这里自己 `mkdtemp` 铺一遍。**
    这里曾经是手抄的一份，而它按 `corpus.iter_artifacts()` 铺——那个迭代器**只产
    `.json`**，于是 `stage0/clean_text.txt`（全链根）**从来没有被拷进来过**：
    任何一次带来源账的写盘都在 `_refuse_bad_provenance` 当场 `ProvenanceRefused`，
    CLI 把它渲染成**退出码 3「结构性拒绝（这是 bug）」**——一条纯粹的环境缺料
    被报成代码自相矛盾，本文件五条端到端用例全挂在这上面。
    `Sandbox` 无条件搬全链根，它的 docstring 记着的正是同一次事故
    （「两处手抄、其余七个文件漏掉，17 条红在同一句话上」）。这是那批手抄里最后一处。
    """
    if not _BOOKS:
        pytest.skip('当前语料没有完整 Stage5 上游与正式成稿')
    # ⚠ 前提是**语料的版本**不是被测的性质：本文件测的是验收/wontfix/归一幂等，
    #   而它们都得先写得下去。成稿过期时 `--recheck` 恒 `ProvenanceRefused`
    #   ⇒ 用例根本跑不到自己的断言（实测：过期两本退出码 3，干净那本退出码 0）。
    corpus.requires_current(_BOOK, *_draft_rels(_BOOK),
                            produced_by='stage5.write', cost='付费')
    rels = tuple(
        a.rel for a in corpus.iter_artifacts()
        if a.book_dir == _BOOK and '_rounds' not in a.rel
        and not a.rel.startswith(('stage0/', 'stage6/', 'stage7/'))
        and 'review' not in a.rel)
    s = sandbox.Sandbox(_BOOK, rels)
    yield s.root, s.sid
    s.close()


def _run(*argv: str) -> int:
    from novel.cli.main import main
    return main(list(argv))


@pytest.mark.corpus
def test_the_review_runs_on_a_current_book_and_reports_all_baselines(box):
    """端到端重算当前正式成稿，并核对退出码与 HARD 结论。"""
    from novel.cli import exit_codes as X
    root, sid = box
    rc = _run('run', 'stage5.review', sid, '--base', str(root))
    rev = json.loads((root / _BOOK / 'stage5' / 'review_1.json').read_text(encoding='utf-8'))
    assert rc in (X.OK, X.CHECK_FAILED)
    assert (rc == X.CHECK_FAILED) is (rev['counts'].get('hard', 0) > 0)
    mechanical = [r for r in REGISTRY.of_stage('stage5')
                  if r.sev in ('hard', 'info') and r.check_kind == 'mechanical']
    assert len(rev['baselines']) == len(mechanical) + 1
    nch = len([c for c in rev['chapters'] if c.get('relpath')])
    for rule, baseline in rev['baselines'].items():
        if rule == '_fact_landing':
            continue
        hits, total = map(int, baseline.split('/'))
        assert total == nch and hits <= total, \
            f'{rule}: baseline 的单位应是命中章节数，不是 finding 条数（{baseline}）'
    # ⚠ **`name_surface_survey` 无条件产出**：原型只在命中时才产，于是
    #   「全书 0 个自造称谓」这个**读数**永远说不出来。
    assert any(f['rule'] == 'name_surface_survey' for f in rev['book'])


@pytest.mark.corpus
def test_wontfix_refuses_a_conclusion_that_does_not_exist(box):
    """⚠ **不给不存在的结论发豁免** ⇒ 显式终态（缺省是「不知道」，不许猜）。"""
    from novel.cli import exit_codes as X
    root, sid = box
    _run('run', 'stage5.review', sid, '--base', str(root))
    assert _run('run', 'stage5.review', sid, '--wontfix',
                '1|draft_too_short=这条我不修', '--base', str(root)) == X.TERMINAL_STATE


@pytest.mark.corpus
def test_wontfix_needs_a_reason_and_a_third_segment_when_ambiguous(box):
    """⚠ 两条约束一起验：

      · **`why` 必填**——写不出理由的豁免，与「没看过」在盘上没有区别；
      · 同一 (章, 判据) 有多条结论时**要人写出第三段**——原型那条路把它们**一并豁免**了，
        而「换了个位置的同类问题」不是人做的决定。
    """
    from novel.cli import exit_codes as X
    root, sid = box
    _run('run', 'stage5.review', sid, '--base', str(root))
    assert _run('run', 'stage5.review', sid, '--wontfix', '1|band_drift',
                '--base', str(root)) == X.TERMINAL_STATE
    rev = json.loads((root / _BOOK / 'stage5' / 'review_1.json').read_text(encoding='utf-8'))
    multi = next((c for c in rev['chapters']
                  if len([f for f in c['findings'] if f['rule'] == 'band_drift']) > 1), None)
    if multi is None:
        pytest.skip('这本书没有一章报了两条 `band_drift`——本条**没在查**')
    assert _run('run', 'stage5.review', sid, '--wontfix',
                f"{multi['output_ordinal']}|band_drift=一并豁免", '--base', str(root)) \
        == X.TERMINAL_STATE


def test_the_wontfix_key_survives_an_evidence_key_that_contains_a_pipe():
    """⭐⭐ **`evidence_key` 自己就带 `|`**，而 `--wontfix` 的键也用 `|` 分段。

    `evidence_key = '|'.join(evidence)` ⇒ 一条 `length_report` 的证据键长成 `887|880|875`。
    裸 `split('|')` 会切出 5 段而不是 3 段，于是**每一条多凭据的结论都豁免不了**，
    报错还说「键读不出来」——**那句话是假的**：键是对的，是解析器错了。
    ⇒ 限定切两刀，第三段吃掉剩下的全部（证据键在最后，这样恒正确）。
    """
    # ⚠ 解析器已收成全链一份（`novel/wontfix.py`）：stage5 与 stage6 共用它。
    from novel.wontfix import parse_spec
    got = parse_spec('1|length_report|887|880|875=这本书就这么写')
    assert got == ('1', 'length_report', '887|880|875', '这本书就这么写')
    assert parse_spec('book|book_length=预算就是这么定的')[2] is None


@pytest.mark.corpus
def test_wontfix_lands_and_never_deletes_the_conclusion(box):
    """⭐ **豁免不删结论**：它照常列在报告与界面上，只是不再进反哺、不再占「待人工」。

    「有人看过并决定不修」与「没人看过」必须分得开——那正是加这个机制的全部理由。
    """
    from novel.cli import exit_codes as X
    root, sid = box
    _run('run', 'stage5.review', sid, '--base', str(root))
    rev = json.loads((root / _BOOK / 'stage5' / 'review_1.json').read_text(encoding='utf-8'))
    row = next((c for c in rev['chapters'] if c['findings']), None)
    if row is None:
        pytest.skip('当前成稿没有可豁免结论')
    f = row['findings'][0]
    spec = f"{row['output_ordinal']}|{f['rule']}|{f['evidence_key']}=这本书就这么写"
    assert _run('run', 'stage5.review', sid, '--wontfix', spec, '--base', str(root)) == X.OK
    rel = 'stage5/wontfix.json'
    raw_wf = json.loads((root / _BOOK / rel).read_text(encoding='utf-8'))
    wf = decode_artifact(rel, raw_wf).payload
    assert list(wf) == [f"{row['output_ordinal']}|{f['rule']}|{f['evidence_key']}"]
    assert wf[list(wf)[0]]['why'] == '这本书就这么写'
    # 再跑一次验收：那条结论**照样在**，只是 `actionable=False` 且带着 `wontfix`
    _run('run', 'stage5.review', sid, '--base', str(root))
    rev2 = json.loads((root / _BOOK / 'stage5' / 'review_2.json').read_text(encoding='utf-8'))
    row2 = next(c for c in rev2['chapters'] if c['output_ordinal'] == row['output_ordinal'])
    hit = next(x for x in row2['findings'] if x['evidence_key'] == f['evidence_key']
               and x['rule'] == f['rule'])
    assert hit['wontfix'] and hit['actionable'] is False


@pytest.mark.corpus
def test_rerunning_the_review_on_one_and_the_same_draft_never_advances_the_counter(box):
    """⭐⭐ `R-8.4`：**成稿指纹没变时不许前进未消轮次计数。**

    拿同一份成稿重跑一次验收（改判据、补上游、或者手滑跑了两遍）**不是「写手又试了一次」**
    ——那时把计数 +1，等于让一次 **0 成本重算**把「停止重写」的闸门推到跟前。
    """
    root, sid = box
    _run('run', 'stage5.review', sid, '--base', str(root))
    _run('run', 'stage5.review', sid, '--base', str(root))
    rev2 = json.loads((root / _BOOK / 'stage5' / 'review_2.json').read_text(encoding='utf-8'))
    fs = [f for c in rev2['chapters'] for f in c['findings']]
    if not fs:
        pytest.skip('当前成稿没有可用于收敛计数的结论')
    assert all(f['converge'] == 'same_draft' for f in fs), (
        '同一份稿重跑了一次验收，而收敛状态前进了——0 成本重算不许有副作用')
    assert all(f['unresolved_rounds'] == 0 for f in fs)


@pytest.mark.corpus
def test_recheck_is_idempotent_on_stored_drafts(box):
    """⭐ 产物里存的是**归一之后**的字节 ⇒ 再归一一遍必须**一个字节都不动**。

    ⚠ 搬错一个字符类不会报错，只会让某类输入**静默地不再归一**——而落盘闸门与验收相
    看的是同一份字节，两边会一起错成一样，**谁也不会出声**。
    """
    from novel.cli import exit_codes as X
    root, sid = box
    before = {p: p.read_bytes() for p in (root / _BOOK / 'stage5').glob('ch[0-9]*.json')
              if '.INVALID.' not in p.name and '.rejected.' not in p.name}
    assert before, '这本书没有成稿——本条**没在查**'
    rc = _run('run', 'stage5.write', sid, '--recheck', '--base', str(root))
    assert rc in (X.OK, X.CHECK_FAILED)
    for p, raw in before.items():
        rel = p.relative_to(root / _BOOK).as_posix()
        now = decode_artifact(rel, json.loads(p.read_text(encoding='utf-8'))).payload
        old = decode_artifact(rel, json.loads(raw.decode('utf-8'))).payload
        assert Chapter.model_validate(now).text == Chapter.model_validate(old).text, (
            f'{p.name}：`--recheck` 改写了正文字节——归一不幂等了')


@pytest.mark.corpus
def test_dry_run_builds_the_same_prompt_the_paid_path_would_send(box):
    """⭐ `R-11.8`：预览与真跑**共用同一个构造入口**。

    ⚠ 否则「预览里看到的」与「真发出去的」是两件事，而那种分家没有任何东西会报错。
    """
    from novel.run import RunContext
    from novel.stages.stage5 import write
    root, sid = box
    ctx = RunContext.open(root, sid, 'stage5.write', paid=False, write_ledger=False)
    system, user, mat = write.build_prompt(ctx, '1')
    assert system == prompts.write_system()
    assert user == prompts.build_user(mat)
    assert '── 本章任务 ──' in user and '篇幅方式' not in user, (
        '「篇幅方式」那一行回来了——`op` 已整族裁撤，它只会渲染出一个带窟窿的指令')


# ─────────────────────────────────────────────────────────────────────────────
# 六、退役接口不再复活
# ─────────────────────────────────────────────────────────────────────────────

def test_retired_arm_rules_and_selectors_stay_absent():
    from novel.orchestrator import approvals
    from novel.stages.stage5 import write

    retired = {'dialogue_halved', 'arm_split_report', 'draft_duplicated'}
    assert retired.isdisjoint({r.rule for r in REGISTRY.of_stage('stage5')})
    assert not hasattr(write, '_ARMS') and not hasattr(write, '_Arm')
    assert not hasattr(approvals, 'default_deep')
    assert not hasattr(approvals, 'current_depth_selection')
