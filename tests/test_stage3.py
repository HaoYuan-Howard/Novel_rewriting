#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Stage3 当前契约、确定性复测与判据反例。"""
from __future__ import annotations

import pytest

from contracts.rules import REGISTRY
from contracts.schema.stage1 import HookCopy, MainText
from contracts.schema.stage2 import GlobalGrounding
from contracts.schema.stage3 import Measurable, StyleGrounding
from novel.core.measure import drift, measure, measure_body
from novel.report import build, render
from novel.prov import FreshnessReport
from novel.stages.stage3 import checks
from tests import corpus, factories, sandbox

def _books() -> list[str]:
    return sorted({a.book_dir for a in corpus.iter_artifacts()
                   if a.rel == 'stage3/style_grounding.json'})


def _cut(book: str, rel: str):
    """读取当前严格契约；找不到时返回 `None`。"""
    a = next((x for x in corpus.iter_artifacts() if x.book_dir == book and x.rel == rel), None)
    return corpus.load_current(a) if a is not None else None


def _inputs(book: str):
    """本层的三份上游 + 被判的那一份产物，全部现算成新契约形态。"""
    sg = _cut(book, 'stage3/style_grounding.json')
    main = MainText.model_validate(_cut(book, 'stage1/main_text.json'))
    hook = HookCopy.model_validate(_cut(book, 'stage1/hook_copy.json'))
    raw_gg = _cut(book, 'stage2/global_grounding.json')
    gg = GlobalGrounding.model_validate(raw_gg) if raw_gg is not None else None
    return sg, main, hook, gg


def _outcomes(book: str, *, with_drift: bool = True):
    """⚠ **走生产那一个调度点（`style.judge_with`），不在这里第二次拼实参。**

    原来这里自己拼 `corpus` / `names` / `spoiler_names`，于是
    `expected_chapters` 加进 `checks.check` 时**只有这一份没跟上** ⇒
    三本书的 `required` 恒红，报的是「校验期拿不到预期源章节集合」——
    而真跑的 `stage3.style --check-only` 三本全 PASS。
    **一条说产物有问题的红，实际说的是测试台漂了。**
    """
    from novel.stages.stage3 import style
    sg, main, hook, gg = _inputs(book)
    assert gg is not None, f'{book} 少了 stage2/global_grounding.json——本层判据没有专名表可用'
    now = Measurable(body=measure_body(main), hook=measure((hook.text or '').strip()))
    return sg, style.judge_with(sg, main, hook, gg, measured_now=now if with_drift else None)


def _report(book: str):
    sg, outs = _outcomes(book)
    prov = sg.get('_provenance') or {}
    return build(
        sid=book.rsplit('_', 1)[-1], node='stage3.style',
        freshness=FreshnessReport('unknown', 'no_inputs'), outcomes=outs,
        accounting={k: prov[k] for k in ('attempts', 'revalidated', 'gates_skipped')
                    if k in prov})


# ───────────────── 当前正式产物 ─────────────────

@pytest.mark.corpus
def test_the_current_corpus_has_stage3_artifacts():
    if corpus.corpus_root() is None:
        pytest.skip('盘上没有语料——本条**没在查**，不是「查过没事」')
    assert _books()


@pytest.mark.corpus
@pytest.mark.parametrize('book', _books())
def test_check_only_accepts_every_current_formal_artifact(book):
    """正式画像已经过写入闸门，复检不得命中 HARD。INFO 命中随小说内容变化。"""
    rep = _report(book)
    hard_hits = [r.rule for r in rep.rules if r.sev == 'hard' and r.status == 'hit']
    assert not hard_hits, f'{book} 的当前正式画像命中 HARD：{hard_hits}\n{render(rep)}'


@pytest.mark.corpus
@pytest.mark.parametrize('book', _books())
def test_every_registered_rule_produced_a_row(book):
    """九条注册判据**一行不少**，且没有一条是 `skipped`（`--check-only` 恒排全）。"""
    rep = _report(book)
    assert {r.rule for r in rep.rules} == {r.rule for r in REGISTRY.of_stage('stage3')}
    assert [r.rule for r in rep.rules if r.status == 'skipped'] == []


@pytest.mark.corpus
@pytest.mark.parametrize('book', _books())
def test_remeasure_reproduces_the_stored_numbers_value_by_value(book):
    """当前产物的 23 项机械度量可逐值复算。"""
    sg, main, hook, _gg = _inputs(book)
    stored = Measurable.model_validate(sg['measurable'])
    now = Measurable(body=measure_body(main), hook=measure((hook.text or '').strip()))
    from novel.core.measure import flatten
    assert len(flatten(stored.body)) + len(flatten(stored.hook)) == 23
    assert drift(stored.body, now.body) == []
    assert drift(stored.hook, now.hook) == []


@pytest.mark.corpus
@pytest.mark.parametrize('book', _books())
def test_the_artifact_still_loads_as_a_model(book):
    """判据跑在 dict 上，但那份 dict 仍然必须是一份合法产物——否则判据在判一个幻觉。"""
    sg, *_ = _inputs(book)
    StyleGrounding.model_validate(sg)


# ───────────────── `measurable_drift` 只在复检时跑 ─────────────────

@pytest.mark.corpus
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
# 「见过它通过」不是证据，「见过它红一次」才是。反例使用合成 dict：
# 新契约把八维做成了 `Field(min_length=1)`，模型化之后 `required` **构造不出反例**。

def _clean(book: str | None = None) -> dict:
    return _inputs(book)[0] if book is not None else factories.stage3_artifact()


def _fire(data: dict, rule: str, **kw) -> list:
    outs = checks.check(data, corpus=kw.pop('corpus', ''), **kw)
    o = next(o for o in outs if o.rule == rule)
    return list(o.findings)


def test_required_goes_red_when_a_voice_dimension_is_empty():
    data = _clean()
    data['voice']['interiority'] = '   '
    assert _fire(data, 'required'), '八维缺一必须红'


def test_required_goes_red_on_a_field_outside_the_contract():
    data = _clean()
    data['voice']['made_up'] = 'x'
    assert _fire(data, 'required')


def test_verbatim_goes_red_on_a_twenty_char_lift():
    source = factories.stage5_book()
    data = _clean()
    from novel.core.text import norm
    corpus_text = norm(''.join(source.source_text.values()))
    data['voice']['register_tone'] = corpus_text[:40]
    assert _fire(data, 'verbatim', corpus=corpus_text)


def test_habit_quote_goes_red_on_a_concrete_word_example():
    data = _clean()
    data['surface_habits'] = ['高频使用「我气笑了」这类口头禅']
    assert _fire(data, 'habit_quote')


def test_habit_quote_spares_a_pure_structural_template():
    """⚠ 「引号内一律禁」**过宽**：`说话者+说：`、`一……便……` 是结构模板，
    删掉会让产物更差（判据 20）。"""
    data = _clean()
    data['surface_habits'] = ['固定用「一……便……」这类条件复句推进']
    assert not _fire(data, 'habit_quote')


def test_habit_quote_scans_chapter_local_style_lists_too():
    data = _clean()
    data['chapter_styles'] = [{
        'source_ordinal': 1,
        'voice': data['voice'],
        'surface_habits': ['在转折处插入「我早就知道」式具体口头禅'],
    }]
    findings = _fire(data, 'habit_quote')
    assert findings and findings[0].evidence == [
        'chapter_styles[0].surface_habits[0]', '我早就知道']
    assert '我早就知道' not in '\n'.join(data['surface_habits']), \
        '命中应只能来自 chapter_styles，否则没有证明递归扫描真的生效'


def test_targeted_habit_replacement_preserves_everything_outside_the_quote():
    from novel.stages.stage3 import style
    data = {'surface_habits': ['转折处用「我气笑了」制造突兀停顿，随后恢复长句。']}
    got = style._replace_habit_literals(data, {'我气笑了': '短促的情绪反应句'})
    assert got == {'surface_habits': ['转折处用短促的情绪反应句制造突兀停顿，随后恢复长句。']}
    assert data['surface_habits'][0] == '转折处用「我气笑了」制造突兀停顿，随后恢复长句。'


def test_targeted_habit_repair_contract_accepts_category_and_rejects_normalised_echo():
    from novel.stages.stage3 import style

    expected = {'我气笑了'}
    assert style._replacement_contract_error(
        {'我气笑了': '短促的情绪反应句'}, expected) is None
    assert '复述原字面' in (style._replacement_contract_error(
        {'我气笑了': '我 气笑了式反应'}, expected) or '')


def test_chapter_prompt_contains_only_current_chapter_and_global_bands():
    from novel.stages.stage3 import prompts
    measurable = Measurable.model_validate(_clean()['measurable'])
    one = prompts.build_chapter_user(
        measurable, chapter_ordinal=1, chapter_text='第一章独有字节甲乙丙')
    two = prompts.build_chapter_user(
        measurable, chapter_ordinal=2, chapter_text='第二章独有字节丁戊己')
    assert '第一章独有字节甲乙丙' in one and '第二章独有字节丁戊己' not in one
    assert '第二章独有字节丁戊己' in two and '第一章独有字节甲乙丙' not in two
    assert one.partition('── 源章 1')[0] == two.partition('── 源章 2')[0]


def test_chapter_and_aggregate_prompts_publish_the_exact_response_shape():
    from contracts.schema.stage3 import HookStyle, Voice
    from novel.stages.stage3 import prompts

    chapter = prompts.chapter_style_system()
    aggregate = prompts.chapter_aggregate_system()
    for key in Voice.model_fields:
        assert f'"{key}"' in chapter
        assert f'"{key}"' in aggregate
    for key in HookStyle.model_fields:
        assert f'"{key}"' not in chapter
        assert f'"{key}"' in aggregate
    assert '"surface_habits": [' in chapter
    assert '"hook_style": {' in aggregate


def test_anti_leak_response_abstraction_removes_inferred_mechanism_and_names():
    from contracts.schema.stage3 import ChapterStyleResponse
    from novel.stages.stage3 import style

    raw = ChapterStyleResponse.model_validate({
        'voice': {
            'narrative_distance': '方衡以第一人称贴身叙述',
            'register_tone': '系统提示与弹幕形成冷暖反差',
            'interiority': '内心短句直接出现',
            'sentence_rhythm': '短句快速推进',
            'dialogue_presentation': '系统数值反馈独立成段',
            'description_density': '环境描写稀疏',
            'chapter_open_close': '直接开场并戛然而止',
            'mode_variation': '任务面板出现时转为机械语气',
        },
        'surface_habits': ['方衡的系统弹窗独立成段，穿越身份不立即解释'],
    })
    cleaned, count = style._abstract_style_content(
        raw, frozenset({'方衡', '系统'}), title='攻略女主系统')
    blob = cleaned.model_dump_json()
    assert count == 8
    assert all(word not in blob for word in
               ('方衡', '系统', '弹幕', '任务面板', '穿越身份'))
    assert '量化反馈' in blob and '群体屏幕化评论' in blob


def test_required_rejects_incomplete_or_reordered_chapter_style_coverage():
    data = {**_clean()}
    local = {
        'source_ordinal': 1,
        'voice': data['voice'],
        'surface_habits': ['短句在转折处收束'],
    }
    data['chapter_styles'] = [local]
    outcomes = checks.check(data, corpus='', expected_chapters=(1, 2))
    required = next(outcome for outcome in outcomes if outcome.rule == 'required')
    assert required.findings and '期望 [1, 2]' in required.findings[0].detail

    data['chapter_styles'] = [
        {**local, 'source_ordinal': 2},
        local,
    ]
    outcomes = checks.check(data, corpus='', expected_chapters=(1, 2))
    required = next(outcome for outcome in outcomes if outcome.rule == 'required')
    assert required.findings and '实得 [2, 1]' in required.findings[0].detail


@pytest.mark.corpus
def test_chapter_style_generation_refuses_to_run_without_stage2_name_table():
    """缺 stage2 专名表 ⇒ **显式终态**，不许把「没有检查材料」当成防泄露。

    ⚠⚠ **守卫在 `_upstreams` 里，不在 `build_chapter_prompts` 里。**
    本条原来 `monkeypatch` 掉 `_upstreams`（让它返回 `(object(), object(), None)`）
    再要求 `build_chapter_prompts` 自己抛——那道 `if gg is None` 的兜底已经随
    `_upstreams` 收紧而删掉了（`store.read` 读不到就 `UpstreamMissing`，
    `gg` 在生产里**不可能是 `None`**）。删得对，测试没跟上：
    它打的是一个够不着的分支，于是先炸在 `measure_all(object(), object())` 的
    裸 `AttributeError` 上——而 `errors.py` 明写「每一处终态都要选一个具体的类」，
    一条裸 `AttributeError` 连终态都不算。
    ⇒ 改成走**真的沙箱**：把 `stage2/global_grounding.json` 拿掉，让真入口去拒。
    """
    from novel.errors import UpstreamMissing
    from novel.run import RunContext
    from novel.stages.stage3 import style

    if not _books():
        pytest.skip('盘上没有语料——本条**没在查**，不是「查过没事」')
    book = _books()[0]
    box = sandbox.Sandbox(book, ('stage1/main_text.json', 'stage1/hook_copy.json'))
    try:
        ctx = RunContext.open(str(box.root), box.sid, 'stage3.style', paid=True,
                              write_ledger=False)
        with pytest.raises(UpstreamMissing, match='global_grounding'):
            style.build_chapter_prompts(ctx)
    finally:
        box.close()


def test_stage3_has_one_production_path_without_variant_parameter():
    import inspect
    from novel.stages.stage3 import style

    assert list(inspect.signature(style.run_style).parameters) == ['ctx', 'client']



def test_number_dup_goes_red_on_a_measured_number():
    data = _clean()
    data['voice']['sentence_rhythm'] = '平均句长 19 字，偏短'
    assert _fire(data, 'number_dup')


def test_struct_ref_goes_red_on_a_beat_id():
    data = _clean()
    data['voice']['chapter_open_close'] = '章首常照 b03_02 那样起'
    assert _fire(data, 'struct_ref')


def test_node_leak_goes_red_on_an_internal_workflow_name():
    data = _clean()
    data['voice']['mode_variation'] = '按 blueprint 的分段切换声音'
    assert _fire(data, 'node_leak')


def test_name_leak_goes_red_and_flags_the_spoiler_subject():
    data = _clean()
    names = frozenset({'沈青'})
    victim = sorted(names)[0]
    data['voice']['register_tone'] = f'常借{victim}的口吻自嘲'
    fs = _fire(data, 'name_leak', names=names)
    assert fs and victim in fs[0].evidence


# ───────────────── `name_leak` 的三态 ─────────────────

def test_name_leak_without_a_name_table_is_unver_not_ok():
    """**查不了 ≠ 查过没问题。** 没有名表时这条规则根本没跑。"""
    data = _clean()
    o = next(o for o in checks.check(data, corpus='', names=None) if o.rule == 'name_leak')
    assert o.unver_why and '没有跑' in o.unver_why


def test_name_leak_with_an_empty_name_table_is_also_unver():
    """⚠ **拿一张空表去扫，0 命中是必然的**——那是「保证会亮的绿灯」（判据 31）。"""
    data = _clean()
    o = next(o for o in checks.check(data, corpus='', names=frozenset())
             if o.rule == 'name_leak')
    assert o.unver_why and '空' in o.unver_why


def test_an_unver_name_leak_ambers_the_badge():
    data = _clean()
    outs = checks.check(data, corpus='', names=None)
    rep = build(sid='s', node='stage3.style',
                freshness=FreshnessReport('unknown', 'no_inputs'), outcomes=outs)
    assert next(r for r in rep.rules if r.rule == 'name_leak').status == 'unver'
    assert rep.badge in ('amber', 'fail')


# ───────────────── 绿灯必须报出自己的分母（判据 31） ─────────────────

@pytest.mark.corpus
@pytest.mark.parametrize('book', _books())
def test_every_green_light_reports_what_it_scanned(book):
    """一个不知道自己扫了多少的绿灯没有判别力。"""
    rep = _report(book)
    naked = [r.rule for r in rep.rules if r.status == 'ok' and not r.scanned]
    assert not naked, f'这些绿灯没说自己扫了多少：{naked}'


# ───────────────── 记账键终于有了出口（`stage3.md` §五 那笔账） ─────────────────

@pytest.mark.corpus
@pytest.mark.parametrize('book', _books())
def test_the_three_accounting_keys_finally_have_an_exit(book):
    """三个当前记账键都在报告中有常驻出口。"""
    rep = _report(book)
    assert {a.key for a in rep.accounting} == {'attempts', 'revalidated', 'gates_skipped'}
    text = render(rep)
    for key in ('attempts', 'revalidated', 'gates_skipped'):
        assert key in text, f'{key} 没有出现在报告里——记账不是终点，报出来才是'


@pytest.mark.corpus
@pytest.mark.parametrize('book', _books())
def test_gates_skipped_is_reported_even_when_the_producer_never_recorded_it(book):
    """`recorded` 必须与当前产物是否真的保存该键一致。"""
    sg, *_ = _inputs(book)
    recorded = 'gates_skipped' in (sg.get('_provenance') or {})
    rep = _report(book)
    line = next(a for a in rep.accounting if a.key == 'gates_skipped')
    assert line.recorded is recorded
    assert ('[????] gates_skipped：未记录' in render(rep)) is (not recorded)


# ───────────────── 响应契约 ─────────────────

def test_require_is_derived_and_covers_hook_style():
    """响应必填键从当前模型派生，并覆盖 `hook_style`。"""
    from contracts.schema.stage3 import StyleResponse
    from novel.llm.prompts import required_top_level
    assert set(required_top_level(StyleResponse)) == {'voice', 'surface_habits', 'hook_style'}
