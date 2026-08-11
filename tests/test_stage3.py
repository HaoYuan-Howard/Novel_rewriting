#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
test_stage3.py —— 第一条纵切的对账。**盘上三本，零成本，一分钱不花。**

【这份测试的判准】结论集必须与**原型**一致：
`--check-only` 三本 **PASS / PASS / PASS + 2 条 INFO `micro_quote`**（三岁那本），
`--remeasure` 的 `measurable` **23 项逐值相同**。
⚠ **不一致就是骨架抄错了，不是数据变了**——盘上那三份产物一个字节都没动过。

⚠ 语料按 `tests/corpus.py::load_cut` **现算**成新契约形态，**不落任何盘**：
写一份迁移后的语料出来就是第二个落点，而它会与 `CUT` 分家。
"""
from __future__ import annotations

import pytest

from contracts.rules import REGISTRY
from contracts.schema.stage1 import HookCopy, MainText
from contracts.schema.stage2 import GlobalGrounding
from contracts.schema.stage3 import Measurable, StyleGrounding
from novel.core.measure import drift, measure, measure_body
from novel.core.naming import names_of, spoiler_names_of
from novel.report import build, render
from novel.prov import FreshnessReport
from novel.stages.stage3 import checks
from tests import corpus

pytestmark = pytest.mark.skipif(corpus.corpus_root() is None,
                                reason='盘上没有语料——这几条**没在查**，不是「查过没事」')

#: 盘上三本里 `micro_quote` 唯一命中的那本，以及它命中了**几处**（`stage3.md` §六：「三岁 2 处」）。
#:
#: ⚠ **「2 处」是两条微引用，不是两条 finding。** 原型把一次命中汇总成**一条**结论、
#: 逐处放进凭据（`别说话别说话——` / `结果没过三天`），本层照搬——
#: 改动结论的**粒度**会连带改掉收敛键（`_common.md` §七：收敛键 = (章, rule, evidence_key)），
#: 而那是一次语义裁决，不该在移植途中顺手做掉。
_EXPECT_MICRO = {'我，三岁嘴强月老，拆姻缘一拆一个准': 2}


def _books() -> list[str]:
    return sorted({a.book_dir for a in corpus.iter_artifacts()
                   if a.rel == 'stage3/style_grounding.json'})


def _cut(book: str, rel: str):
    """盘上那一份 → 新契约形态。找不到返回 `None`（上游缺失是合法状态）。"""
    a = next((x for x in corpus.iter_artifacts() if x.book_dir == book and x.rel == rel), None)
    return corpus.load_cut(a) if a is not None else None


def _inputs(book: str):
    """本层的三份上游 + 被判的那一份产物，全部现算成新契约形态。"""
    sg = _cut(book, 'stage3/style_grounding.json')
    main = MainText.model_validate(_cut(book, 'stage1/main_text.json'))
    hook = HookCopy.model_validate(_cut(book, 'stage1/hook_copy.json'))
    raw_gg = _cut(book, 'stage2/global_grounding.json')
    gg = GlobalGrounding.model_validate(raw_gg) if raw_gg is not None else None
    return sg, main, hook, gg


def _outcomes(book: str, *, with_drift: bool = True):
    sg, main, hook, gg = _inputs(book)
    from novel.core.text import norm
    corpus_text = norm(''.join(s.text for s in main.root) + (hook.text or '').strip())
    names = names_of(gg) if gg is not None else None
    now = Measurable(body=measure_body(main), hook=measure((hook.text or '').strip()))
    return sg, checks.check(
        sg, corpus=corpus_text, names=names,
        spoiler_names=spoiler_names_of(gg, names) if gg is not None and names else frozenset(),
        measured_now=now if with_drift else None)


def _report(book: str):
    sg, outs = _outcomes(book)
    prov = sg.get('_provenance') or {}
    return build(
        sid=book.rsplit('_', 1)[-1], node='stage3.style',
        freshness=FreshnessReport('unknown', 'no_inputs'), outcomes=outs,
        accounting={k: prov[k] for k in ('attempts', 'revalidated', 'gates_skipped')
                    if k in prov})


# ───────────────── ⭐ 与原型逐条对账 ─────────────────

def test_the_corpus_actually_has_three_books():
    """守卫的守卫：语料没找到时上面的 skip 会说出来，但**找到了却只有一本**不会。"""
    assert len(_books()) == 3, f'盘上只找到 {len(_books())} 本 stage3 产物'


@pytest.mark.parametrize('book', _books())
def test_check_only_matches_the_prototype_verdict(book):
    """三本全 PASS，唯一的非空结论是三岁那本的 2 条 `micro_quote`。"""
    rep = _report(book)
    title = book.rsplit('_', 1)[0]
    hit_rules = sorted(r.rule for r in rep.rules if r.status == 'hit')
    assert hit_rules == (['micro_quote'] if title in _EXPECT_MICRO else []), \
        f'{title} 的命中集与原型不一致：{hit_rules}\n{render(rep)}'
    if title in _EXPECT_MICRO:
        line = next(r for r in rep.rules if r.rule == 'micro_quote')
        assert len(line.findings) == 1, '原型把一次命中汇总成一条结论，逐处放进凭据'
        assert len(line.findings[0].evidence) == _EXPECT_MICRO[title], \
            f'微引用处数与 stage3.md 记的不一致：{line.findings[0].evidence}'
    # 一条 HARD 都不许命中 ⇒ 徽章不是 fail。三本原型都是 PASS。
    assert not [r for r in rep.rules if r.sev == 'hard' and r.status == 'hit']


@pytest.mark.parametrize('book', _books())
def test_every_registered_rule_produced_a_row(book):
    """九条注册判据**一行不少**，且没有一条是 `skipped`（`--check-only` 恒排全）。"""
    rep = _report(book)
    assert {r.rule for r in rep.rules} == {r.rule for r in REGISTRY.of_stage('stage3')}
    assert [r.rule for r in rep.rules if r.status == 'skipped'] == []


@pytest.mark.parametrize('book', _books())
def test_remeasure_reproduces_the_stored_numbers_value_by_value(book):
    """**23 项逐值相同** —— 3 本 × 23 项的免费等价测试。

    ⚠ 不一致就是 `measure()` 抄错了：盘上那份 `measurable` 是原型算的，
    而两边走同一个 `round()`，所以**直接判等，不需要容差**。
    """
    sg, main, hook, _gg = _inputs(book)
    stored = Measurable.model_validate(sg['measurable'])
    now = Measurable(body=measure_body(main), hook=measure((hook.text or '').strip()))
    from novel.core.measure import flatten
    assert len(flatten(stored.body)) + len(flatten(stored.hook)) == 23
    assert drift(stored.body, now.body) == []
    assert drift(stored.hook, now.hook) == []


@pytest.mark.parametrize('book', _books())
def test_the_artifact_still_loads_as_a_model(book):
    """判据跑在 dict 上，但那份 dict 仍然必须是一份合法产物——否则判据在判一个幻觉。"""
    sg, *_ = _inputs(book)
    StyleGrounding.model_validate(sg)


# ───────────────── `measurable_drift` 只在复检时跑 ─────────────────

@pytest.mark.parametrize('book', _books()[:1])
def test_drift_is_not_scheduled_at_write_time_and_that_shows(book):
    """落盘那一刻 `measurable` 刚由代码算出来，与重算必然相等
    ——**一条永远不会红的检查不该打 `[OK]`**（判据 31）。

    ⚠ 它随后在报告里是一行 `skipped`，而 `measurable_drift` 是 HARD ⇒ 徽章 `fail`。
    **那是对的**：你确实没跑那道闸，就不该拿到一个绿灯。
    """
    sg, outs = _outcomes(book, with_drift=False)
    assert 'measurable_drift' not in {o.rule for o in outs}
    rep = build(sid='s', node='stage3.style',
                freshness=FreshnessReport('unknown', 'no_inputs'), outcomes=outs)
    line = next(r for r in rep.rules if r.rule == 'measurable_drift')
    assert line.status == 'skipped' and rep.badge == 'fail'


# ───────────────── 每条 HARD 都要有一份会让它红的样本 ─────────────────
#
# 「见过它通过」不是证据，「见过它红一次」才是。盘上三本全 PASS，
# 所以反例只能合成——而合成反例正是 `checks.check` 吃 dict 不吃模型的理由：
# 新契约把八维做成了 `Field(min_length=1)`，模型化之后 `required` **构造不出反例**。

def _clean(book: str) -> dict:
    return _inputs(book)[0]


def _fire(data: dict, rule: str, **kw) -> list:
    outs = checks.check(data, corpus=kw.pop('corpus', ''), **kw)
    o = next(o for o in outs if o.rule == rule)
    return list(o.findings)


def test_required_goes_red_when_a_voice_dimension_is_empty():
    data = _clean(_books()[0])
    data['voice']['interiority'] = '   '
    assert _fire(data, 'required'), '八维缺一必须红'


def test_required_goes_red_on_a_field_outside_the_contract():
    data = _clean(_books()[0])
    data['voice']['made_up'] = 'x'
    assert _fire(data, 'required')


def test_verbatim_goes_red_on_a_twenty_char_lift():
    book = _books()[0]
    data = _clean(book)
    main = MainText.model_validate(_cut(book, 'stage1/main_text.json'))
    from novel.core.text import norm
    corpus_text = norm(''.join(s.text for s in main.root))
    data['voice']['register_tone'] = corpus_text[:40]
    assert _fire(data, 'verbatim', corpus=corpus_text)


def test_habit_quote_goes_red_on_a_concrete_word_example():
    data = _clean(_books()[0])
    data['surface_habits'] = ['高频使用「我气笑了」这类口头禅']
    assert _fire(data, 'habit_quote')


def test_habit_quote_spares_a_pure_structural_template():
    """⚠ 「引号内一律禁」**过宽**：`说话者+说：`、`一……便……` 是结构模板，
    删掉会让产物更差（判据 20）。"""
    data = _clean(_books()[0])
    data['surface_habits'] = ['固定用「一……便……」这类条件复句推进']
    assert not _fire(data, 'habit_quote')


def test_number_dup_goes_red_on_a_measured_number():
    data = _clean(_books()[0])
    data['voice']['sentence_rhythm'] = '平均句长 19 字，偏短'
    assert _fire(data, 'number_dup')


def test_struct_ref_goes_red_on_a_beat_id():
    data = _clean(_books()[0])
    data['voice']['chapter_open_close'] = '章首常照 b03_02 那样起'
    assert _fire(data, 'struct_ref')


def test_node_leak_goes_red_on_an_internal_workflow_name():
    data = _clean(_books()[0])
    data['voice']['mode_variation'] = '按 blueprint 的分段切换声音'
    assert _fire(data, 'node_leak')


def test_name_leak_goes_red_and_flags_the_spoiler_subject():
    book = _books()[0]
    data = _clean(book)
    _sg, _main, _hook, gg = _inputs(book)
    names = names_of(gg)
    victim = sorted(names)[0]
    data['voice']['register_tone'] = f'常借{victim}的口吻自嘲'
    fs = _fire(data, 'name_leak', names=names)
    assert fs and victim in fs[0].evidence


# ───────────────── `name_leak` 的三态 ─────────────────

def test_name_leak_without_a_name_table_is_unver_not_ok():
    """**查不了 ≠ 查过没问题。** 没有名表时这条规则根本没跑。"""
    data = _clean(_books()[0])
    o = next(o for o in checks.check(data, corpus='', names=None) if o.rule == 'name_leak')
    assert o.unver_why and '没有跑' in o.unver_why


def test_name_leak_with_an_empty_name_table_is_also_unver():
    """⚠ **拿一张空表去扫，0 命中是必然的**——那是「保证会亮的绿灯」（判据 31）。"""
    data = _clean(_books()[0])
    o = next(o for o in checks.check(data, corpus='', names=frozenset())
             if o.rule == 'name_leak')
    assert o.unver_why and '空' in o.unver_why


def test_an_unver_name_leak_ambers_the_badge():
    data = _clean(_books()[0])
    outs = checks.check(data, corpus='', names=None)
    rep = build(sid='s', node='stage3.style',
                freshness=FreshnessReport('unknown', 'no_inputs'), outcomes=outs)
    assert next(r for r in rep.rules if r.rule == 'name_leak').status == 'unver'
    assert rep.badge in ('amber', 'fail')


# ───────────────── 绿灯必须报出自己的分母（判据 31） ─────────────────

@pytest.mark.parametrize('book', _books())
def test_every_green_light_reports_what_it_scanned(book):
    """一个不知道自己扫了多少的绿灯没有判别力。"""
    rep = _report(book)
    naked = [r.rule for r in rep.rules if r.status == 'ok' and not r.scanned]
    assert not naked, f'这些绿灯没说自己扫了多少：{naked}'


# ───────────────── 记账键终于有了出口（`stage3.md` §五 那笔账） ─────────────────

@pytest.mark.parametrize('book', _books())
def test_the_three_accounting_keys_finally_have_an_exit(book):
    """⚠ 实跑三本原型 `--check-only`，报告里这三个键**一行都没有**。这一条把账还上。"""
    rep = _report(book)
    assert {a.key for a in rep.accounting} == {'attempts', 'revalidated', 'gates_skipped'}
    text = render(rep)
    for key in ('attempts', 'revalidated', 'gates_skipped'):
        assert key in text, f'{key} 没有出现在报告里——记账不是终点，报出来才是'


@pytest.mark.parametrize('book', _books())
def test_gates_skipped_is_reported_even_when_the_producer_never_recorded_it(book):
    """盘上三本都是**串行跑的**，`gates_skipped` 零样本 ⇒ 报告要打 `[????] 未记录`。
    「记了 0 次」与「压根没记」必须分开。"""
    rep = _report(book)
    line = next(a for a in rep.accounting if a.key == 'gates_skipped')
    assert line.recorded is False
    assert '[????] gates_skipped：未记录' in render(rep)


# ───────────────── prompt 逐字移植，不许转述 ─────────────────

_PROTOTYPE = (corpus.corpus_root().parent / 'Rapid_Prototyping' / 'Stage3' / 'n3_style.py'
              if corpus.corpus_root() else None)


def _prototype_const(name: str) -> str:
    """从原型源码里把那个字符串常量**原样求值**出来（不 import 它——那要拉起整条依赖链）。"""
    src = _PROTOTYPE.read_text(encoding='utf-8').splitlines(keepends=True)
    i = next(k for k, ln in enumerate(src) if ln.startswith(name + ' = """'))
    j = next(k for k in range(i + 1, len(src)) if src[k].rstrip().endswith('"""'))
    ns: dict = {}
    exec(''.join(src[i:j + 1]), ns)      # noqa: S102 —— 只 exec 一段字符串字面量赋值
    return ns[name]


@pytest.mark.skipif(_PROTOTYPE is None or not _PROTOTYPE.exists(),
                    reason='原型仓不在盘上——这一条**没在查**，不是「查过没事」')
@pytest.mark.parametrize('name', ['STYLE_SYS', 'HABIT_REPAIR_SYS'])
def test_the_prompt_is_a_byte_for_byte_port(name):
    """⚠⚠ **逐字移植，不许转述。**

    这段文字压着 120+ 条真机 findings 的学费：每一条禁令下面都躺着一次实测的损失
    （F-013 的自相冲突指令、F-104/F-118 的剧透泄漏…）。改写它等于把学费重交一遍，
    而**改写不会报错**——它只会让模型的产出慢慢变差，而没有任何一层会出声。
    ⚠ 真要改，改的是**本仓这一份**，然后这条测试会红——那正是它该红的时候，
    删掉它之前先想清楚谁来守「prompt 没被人顺手改坏」。
    """
    from novel.stages.stage3 import prompts
    assert getattr(prompts, name) == _prototype_const(name)


def test_the_rendered_system_prompt_survives_fill():
    """`fill()` 的残留检查：渲染完不许剩下任何 `$`。
    本层没有占位符，但「今天没有」不等于「明天加一个不会忘」。"""
    from novel.stages.stage3.prompts import STYLE_SYS, style_system
    assert style_system() == STYLE_SYS


def test_require_is_derived_and_covers_hook_style():
    """⭐ **原型这个调用点漏了 `hook_style`**，而漏传不报错——它只是静默停掉一条
    HARD 闸门（判据 39）。从 `model_cls` 派生之后，这件事结构上不可能再发生。"""
    from contracts.schema.stage3 import StyleResponse
    from novel.llm.prompts import required_top_level
    assert set(required_top_level(StyleResponse)) == {'voice', 'surface_habits', 'hook_style'}


# ───────────────── 退役而不删 ─────────────────

@pytest.mark.parametrize('book', _books()[:1])
def test_the_two_retired_rules_are_still_listed(book):
    """让「量过、量不准，所以撤了」与「忘了」读起来不一样。"""
    rep = _report(book)
    assert {r.rule for r in rep.retired} == {'num_vs_desc', 'cliche'}
    assert '已退役' in render(rep)
