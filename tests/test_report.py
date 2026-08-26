#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
test_report.py —— 报告按**判据**编排而不是按**结论**编排，这件事得有守卫。

【这份测试盯的那一句话】`build()` 遍历判据注册表，**不遍历 findings**。
它推出来的两条性质各杀掉一种已发生的故障：
  · 注册了而没实现 ⇒ 一行 `skipped`（stage7 的 `rn_unregistered`：判据表里注册着、
    全仓 0 行实现、每本每次打绿灯）
  · 产出了而没注册 ⇒ 抛（镜像疾病：那条结论的 `sev`/`owner` 是谁定的？）

⚠ 全程用**合成的 `Registry` 实例**，不碰全局 `REGISTRY`——
往全局注册会撞 `test_rules_registry.py::test_registry_starts_empty`，
而那条测试守着「判据随各 stage 移植过来时才注册」。
"""
from __future__ import annotations

from types import SimpleNamespace

import pytest

from contracts.invariants import STATUS
from contracts.rules import Registry, Rule
from contracts.schema.report import (
    CheckReport, ProvenanceSection, RuleLine, StatusCounts, badge_of, finding,
)
from novel.errors import AccountingKeyUnregistered, RefusedByStructure
from novel.prov import EdgeVerdict, FreshnessReport
from novel.report import accounting as acc
from novel.report import builder as report_builder
from novel.report import build, render
from novel.report.findings import ran, unverifiable

NODE = 'stage2.blueprint'
STAGE = 'stage2'


# ───────────────────────── 脚手架 ─────────────────────────

def _rule(**kw) -> Rule:
    base = dict(rule='r_demo', stage=STAGE, item='示例判据', sev='hard',
                who_checks='HARD', requirement='R-4.5', owner='stage2')
    base.update(kw)
    return Rule(**base)


def _registry(*rules: Rule) -> Registry:
    reg = Registry()
    for r in rules or (_rule(),):
        reg.register(r)
    return reg


def _fresh(*edges: EdgeVerdict) -> FreshnessReport:
    if not edges:
        return FreshnessReport('unknown', 'no_inputs')
    worst = max(edges, key=lambda e: {'fresh': 0, 'unknown': 1, 'stale': 2}[e.state])
    return FreshnessReport(worst.state, worst.reason, tuple(edges))


def _ghost(key: str) -> EdgeVerdict:
    return EdgeVerdict(key=key, state='unknown', reason='ghost',
                       recorded_file='0123456789', now_file=None,
                       recorded_consumed=None, now_consumed=None)


def _build(reg: Registry, outcomes=(), *, accounting=None, spec=(), monkeypatch=None, **kw):
    if monkeypatch is not None:
        monkeypatch.setitem(acc.ACCOUNTING, STAGE, tuple(spec))
    return build(sid='7615069877019300633', node=NODE, freshness=_fresh(),
                 outcomes=outcomes, accounting=accounting, registry=reg, **kw)


# ───────────────── 恒等基线两格的出口（2026-08-23 补） ─────────────────
#
# ⚠⚠ 补这三条之前，`identity_hit` / `identity_dead` 是**只写不读**的：
# `novel/stages/stage5/review.py` 的两道闸给它们赋值、`contracts/schema/common.py`
# 定义、`web/src/types/common.ts` 也有类型，而**全仓没有任何渲染面、view 或前端组件
# 读过它们**。于是「这一条被本章源文的同一现象混淆了」与「写手真的漂了」
# 在终端上完全同形——而两者的处置相反（前者要修上游或不修，后者才该付钱重写）。
# 形状同 `wontfix` 那个记号，理由也同（`docs/LESSONS.md` 第 4 条）。

def _finding(**kw):
    # ⚠ 用一条**真注册**的判据 id：`contracts/schema/report.py::finding` 会去
    #   `contracts.rules.REGISTRY` 取那一行（`sev` / `owner` / `actionable` 全从它派生）。
    #   本文件脚手架里的 `r_demo` 只活在临时 `Registry()` 里，进不了全局表。
    from contracts.schema.report import finding
    base = dict(rule='band_drift', detail='示例结论', evidence=['e1'])
    base.update(kw)
    return finding(**base)


def test_a_clean_finding_carries_no_identity_mark():
    """缺省不出声——没命中恒等基线的那些不该被这句话污染。"""
    from novel.report.render import _identity_mark
    assert _identity_mark(_finding()) == ''


def test_the_two_identity_questions_get_two_different_sentences():
    """⚠ 两格问的**不是同一个问题**，措辞必须分得开。

    `identity_hit` 逐章问「这一条被本章源文的同一现象混淆了吗」；
    `identity_dead` 书级问「这条判据在这本书上比它自己的基础发生率强吗」。
    合成一句「恒等基线也命中」会把两种完全不同的处置读成同一种。
    """
    from novel.report.render import _identity_mark
    hit = _identity_mark(_finding(identity_hit=True))
    dead = _identity_mark(_finding(identity_hit=True, identity_dead=True))
    assert hit and dead and hit != dead
    assert '本章源文' in hit and '判别力' in dead


def test_identity_dead_none_means_not_computed_not_false():
    """⚠ `identity_dead: bool | None`，`None` = **没算过**，不是「否」。

    只有 stage5 的书级闸会给它赋值，别的层的 finding 这一格恒 `None`
    ——写成真值判断就会把「没算过」渲染成「算过、是否」（`docs/LESSONS.md` 第 4 条）。
    """
    from novel.report.render import _identity_mark
    assert _identity_mark(_finding(identity_dead=None)) == ''
    assert _identity_mark(_finding(identity_dead=False)) == ''


def test_a_wontfix_and_an_identity_mark_both_survive_on_the_same_line():
    """两个记号是**正交**的，不许互相吃掉：豁免说的是「有人决定不修」，
    恒等说的是「这条结论不携带信息」，一条 finding 可以同时是这两件事。"""
    from contracts.schema.common import WontFixNote
    from novel.report.render import _rules
    from contracts.schema.report import RuleLine
    f = _finding(identity_hit=True,
                 wontfix=WontFixNote(why='这条我不修', at='review_1'))
    line = RuleLine(rule='band_drift', item='示例判据', sev='info', check_kind='mechanical',
                    requirement='R-7.21', remediation='human', status='hit', findings=[f])
    text = ' | '.join(_rules([line]))   # 分隔符无关，本条只查两个记号都在
    assert '已豁免' in text and '本章源文' in text


def test_a_valued_fix_route_gets_its_chapters_from_machine_evidence():
    rule = Rule(
        rule='demo_chapters', stage='stage2', item='示例', sev='hard',
        who_checks='HARD', requirement='R-3.5', owner='stage2',
        fix=('stage2.blueprint', '--chapters'),
        fix_args_from_finding=('--chapters',),
    )
    route = report_builder._fix_route(
        rule,
        [SimpleNamespace(fix_args={'--chapters': '11'}),
         SimpleNamespace(fix_args={'--chapters': '3'})],
    )
    assert route is not None and route.available
    assert route.flags == ['--chapters', '3,11']


def test_a_valued_fix_route_without_an_argument_says_it_is_unavailable():
    rule = Rule(
        rule='demo_chapters', stage='stage2', item='示例', sev='hard',
        who_checks='HARD', requirement='R-3.5', owner='stage2',
        fix=('stage2.blueprint', '--chapters'),
        fix_args_from_finding=('--chapters',),
    )
    route = report_builder._fix_route(
        rule,
        [SimpleNamespace(fix_args={})],
    )
    assert route is not None and not route.available
    assert route.unavailable_reason and '没有提供' in route.unavailable_reason


def test_hinge_in_hole_carries_the_parsed_chapter_into_the_report(monkeypatch):
    from novel.stages.stage2.checks import finding as stage2_finding

    hit = stage2_finding(
        'hinge_in_hole', detail='锚点落在漏抓区', evidence=['b11_02:「证据」'])
    assert hit.fix_args == {'--chapters': '11'}
    monkeypatch.setitem(acc.ACCOUNTING, 'stage2', ())
    rep = build(
        sid='7615069877019300633', node='stage2.blueprint', freshness=_fresh(),
        outcomes=[ran('hinge_in_hole', [hit])],
    )
    line = next(row for row in rep.rules if row.rule == 'hinge_in_hole')
    assert line.fix is not None and line.fix.available
    assert line.fix.flags == ['--chapters', '11']
    from novel.views.inbox import _report_items
    inbox = _report_items(rep, 'stage2')
    item = next(row for row in inbox if row.rule == 'hinge_in_hole')
    assert item.kind == 'repairable' and '--chapters 11' in item.how


# ───────── ⭐ 报告由注册表生成，不由产出生成 ─────────

def test_a_registered_rule_with_no_implementation_becomes_skipped_not_absent(monkeypatch):
    """⭐ **本层最值钱的一条。**

    stage7 的 `rn_unregistered` 在判据表里注册着、全仓 0 行实现，而它是 `info`
    ⇒ 每本每次都打绿灯。报告由**产出**生成时，这种判据会从报告里**消失**，
    而消失与「跑过没事」在界面上长得一模一样。
    """
    rep = _build(_registry(_rule(rule='never_implemented')), (), monkeypatch=monkeypatch)
    assert [r.rule for r in rep.rules] == ['never_implemented']
    line = rep.rules[0]
    assert line.status == 'skipped'
    assert line.why and '未被调用' in line.why
    assert line.findings == []


def test_the_row_count_equals_the_registry_not_the_outcomes(monkeypatch):
    """三条注册、一条有产出 ⇒ **仍然三行**。"""
    reg = _registry(_rule(rule='a'), _rule(rule='b', sev='info'), _rule(rule='c', sev='info'))
    rep = _build(reg, [ran('b', [finding('b', detail='命中', evidence=['x'], registry=reg)])],
                 monkeypatch=monkeypatch)
    assert len(rep.rules) == 3
    assert {r.rule: r.status for r in rep.rules} == {'a': 'skipped', 'b': 'hit', 'c': 'skipped'}


def test_an_outcome_for_an_unregistered_rule_raises(monkeypatch):
    """镜像疾病：一条没有判据表撑腰的结论，它的 `sev` / `owner` 是谁定的？"""
    reg = _registry(_rule(rule='a'))
    orphan = ran('a')
    object.__setattr__(orphan, 'rule', 'ghost_rule')      # 绕过 Outcome 自己的校验
    with pytest.raises(RefusedByStructure, match='判据表里没有'):
        _build(reg, [orphan], monkeypatch=monkeypatch)


def test_two_outcomes_for_the_same_rule_raise(monkeypatch):
    """并表时会静默丢掉其中一份——而「一条注册判据恰好一行」在这里就已经破了。"""
    reg = _registry(_rule(rule='a', sev='info'))
    with pytest.raises(RefusedByStructure, match='两份结果'):
        _build(reg, [ran('a'), ran('a')], monkeypatch=monkeypatch)


def test_a_stage_with_no_registered_rules_raises(monkeypatch):
    """守卫的守卫：一份零判据的报告**看起来是绿的**，而它什么也没查。"""
    with pytest.raises(RefusedByStructure, match='一条注册判据都没有'):
        _build(Registry(), (), monkeypatch=monkeypatch)


# ───────── 徽章 ─────────

def test_a_skipped_hard_rule_fails_the_badge(monkeypatch):
    """一条阻断级判据这次没被执行，与它执行了并且命中，对调用方是同一件事：
    **你不能拿这份产物往下走。** 降成 amber 等于说「大概没事」，而我们恰恰不知道。"""
    rep = _build(_registry(_rule(rule='hard_one', sev='hard')), (), monkeypatch=monkeypatch)
    assert rep.badge == 'fail' and rep.passed is False


def test_a_skipped_info_rule_only_ambers_the_badge(monkeypatch):
    rep = _build(_registry(_rule(rule='info_one', sev='info')), (), monkeypatch=monkeypatch)
    assert rep.badge == 'amber' and rep.passed is True


def test_an_unverifiable_rule_ambers_but_never_passes_green(monkeypatch):
    """`amber` 不是绿色 ⇒ **「pass + N 项未验证」在类型上不可能**。"""
    reg = _registry(_rule(rule='name_leak'))
    rep = _build(reg, [unverifiable('name_leak', '这本书没有名表')], monkeypatch=monkeypatch)
    assert rep.rules[0].status == 'unver'
    assert rep.badge == 'amber' and rep.counts.unver == 1


def test_everything_ran_and_nothing_hit_is_the_only_way_to_pass(monkeypatch):
    reg = _registry(_rule(rule='a'), _rule(rule='b', sev='info'))
    rep = _build(reg, [ran('a'), ran('b')], monkeypatch=monkeypatch)
    assert rep.badge == 'pass' and rep.counts == StatusCounts(ok=2, hit=0, unver=0, skipped=0)


# ───────── 度量：无条件产出，**永不命中** ─────────

def _metric_rule(rule='m'):
    return _rule(rule=rule, sev='metric', who_checks='METRIC', owner=None)


def test_a_metric_with_readings_is_ok_not_hit(monkeypatch):
    """度量有产出是**常态**。写成 `hit` 就是把「现状读数」重新并回「告警」那一支。"""
    reg = _registry(_metric_rule())
    rep = _build(reg, [ran('m', [finding('m', detail='中位 138 字', evidence=['138'],
                                         registry=reg)])], monkeypatch=monkeypatch)
    assert rep.rules[0].status == 'ok'
    assert len(rep.rules[0].findings) == 1, '读数不许因为「不算命中」就被丢掉'


def test_a_metric_never_affects_the_badge(monkeypatch):
    reg = _registry(_metric_rule(), _rule(rule='a', sev='info'))
    rep = _build(reg, [ran('m', [finding('m', detail='读数', evidence=['1'], registry=reg)]),
                       ran('a')], monkeypatch=monkeypatch)
    assert rep.badge == 'pass'


def test_a_ruleline_that_claims_a_metric_hit_raises():
    with pytest.raises(ValueError, match='度量'):
        RuleLine(rule='m', sev='metric', status='hit', item='x', requirement='R-4.5')


# ───────── 「够不着」必须说出为什么 ─────────

def test_a_permanently_unverifiable_rule_must_say_why(monkeypatch):
    """`sev='unver'` 的判据（stage6 有 3/9 条）必须说出边界在哪，而只有它自己知道。"""
    reg = _registry(_rule(rule='u', sev='unver', owner=None))
    with pytest.raises(RefusedByStructure, match='为什么够不着'):
        _build(reg, [ran('u')], monkeypatch=monkeypatch)
    rep = _build(reg, [unverifiable('u', '连续文本上的字面探针结构性失效')],
                 monkeypatch=monkeypatch)
    assert rep.rules[0].status == 'unver'


def test_an_unverifiable_outcome_without_a_reason_raises():
    with pytest.raises(ValueError, match='没写出为什么'):
        unverifiable('u', '   ')


@pytest.mark.parametrize('status', ['ok', 'hit'])
def test_a_rule_that_ran_must_not_carry_a_why(status):
    """跑过的两态带 `why` 是噪声，而噪声会让人不看报告。"""
    kw = dict(rule='a', sev='info', item='x', requirement='R-4.5', why='多余的话')
    if status == 'hit':
        kw['findings'] = [_finding_for('a')]
    with pytest.raises(ValueError, match='不该带 why'):
        RuleLine(status=status, **kw)


def _finding_for(rule: str):
    reg = _registry(_rule(rule=rule, sev='info'))
    return finding(rule, detail='d', evidence=['e'], registry=reg)


def test_a_skipped_row_cannot_carry_findings():
    with pytest.raises(ValueError, match='skipped'):
        RuleLine(rule='a', sev='info', status='skipped', item='x', requirement='R-4.5',
                 why='没跑', findings=[_finding_for('a')])


# ───────── `finding()` 工厂：一条结论不可能与判据表说的不一致 ─────────

def test_the_factory_takes_sev_item_owner_from_the_registry():
    reg = _registry(_rule(rule='a', sev='info', item='它在守这个', owner='stage2'))
    f = finding('a', detail='正文', evidence=['x', 'y'], registry=reg)
    assert (f.sev, f.item, f.owner) == ('info', '它在守这个', 'stage2')
    assert f.evidence_key == 'x|y', 'evidence_key 是 evidence 的函数，载体的后置条件'


def test_report_human_text_is_plain_and_rule_note_has_its_own_field(monkeypatch):
    reg = _registry(_rule(
        rule='a', sev='info', note='**这是稳定的`判据`理由**'))
    hit = finding('a', detail='**hinge 落在`漏抓区`**', evidence=['ch11'], registry=reg)
    rep = _build(reg, [ran('a', [hit], scanned='`3` 条')], monkeypatch=monkeypatch)
    line = rep.rules[0]
    assert line.findings[0].detail == 'hinge 落在漏抓区'
    assert line.note == '这是稳定的判据理由'
    assert line.scanned == '3 条'
    assert '**' not in line.model_dump_json() and '`' not in line.model_dump_json()
    assert '说明：这是稳定的判据理由' in render(rep)


def test_the_factory_refuses_a_rule_that_is_not_registered():
    with pytest.raises(KeyError, match='不在注册表里'):
        finding('no_such_rule', detail='d', evidence=[], registry=_registry())


def test_actionable_is_derived_from_repair_not_stored_twice():
    """`Rule` 上**没有** `actionable` 字段，它从 `repair != 'NONE'` 派生。
    加第二个字段等于给同一件事开第二个落点。"""
    plain = _registry(_rule(rule='a', sev='info', owner='stage5'))
    assert finding('a', detail='d', evidence=[], registry=plain).actionable is False
    fed = _registry(_rule(rule='a', sev='info', owner='stage5',
                          repair='UNIQUE', directive='把它改回去。'))
    assert finding('a', detail='d', evidence=[], registry=fed).actionable is True


def test_report_exposes_check_kind_and_remediation_independently_of_severity(monkeypatch):
    from novel.report.findings import ran
    reg = _registry(
        _rule(rule='semantic', sev='hard', check_kind='semantic', repair='UNIQUE', owner='stage5',
              directive='修掉它。'),
        _rule(rule='human', sev='hard'),
        _rule(rule='metric', sev='metric', who_checks='METRIC', owner=None),
    )
    rep = _build(reg, [ran('semantic', scanned='1'), ran('human', scanned='1'),
                       ran('metric', scanned='1')], monkeypatch=monkeypatch)
    rows = {row.rule: row for row in rep.rules}
    assert (rows['semantic'].check_kind, rows['semantic'].remediation) == ('semantic', 'llm')
    assert (rows['human'].check_kind, rows['human'].remediation) == ('mechanical', 'human')
    assert rows['metric'].remediation == 'none'



# ───────── 记账键的出口 ─────────

def _spec(*pairs):
    return tuple(acc.AccountingSpec(k, i) for k, i in pairs)


def test_an_accounting_key_with_no_spec_raises(monkeypatch):
    """**记了一笔谁也读不到的账**：数已经写进 `_provenance`，人却永远看不见。
    原型 13 个记账键里 6 个 0 出口。"""
    with pytest.raises(AccountingKeyUnregistered, match='attempts'):
        _build(_registry(), (), accounting={'attempts': [1]},
               spec=(), monkeypatch=monkeypatch)


def test_a_declared_key_the_producer_never_recorded_is_flagged(monkeypatch):
    """「记了 0 次」与「压根没记」必须分开。"""
    reg = _registry(_rule(rule='a', sev='info'))
    rep = _build(reg, [ran('a')], accounting={},
                 spec=_spec(('attempts', '第几次才产对的')), monkeypatch=monkeypatch)
    line = rep.accounting[0]
    assert line.recorded is False and line.count == 0
    assert '[????] attempts：未记录' in render(rep)


def test_an_empty_value_still_prints_its_line(monkeypatch):
    """空也要报，报成 `0 处`——「没改过」与「没在报」必须分得开。"""
    reg = _registry(_rule(rule='a', sev='info'))
    rep = _build(reg, [ran('a')], accounting={'gates_skipped': []},
                 spec=_spec(('gates_skipped', '哪几条闸门没跑成')), monkeypatch=monkeypatch)
    assert rep.accounting[0].recorded is True and rep.accounting[0].count == 0
    assert 'gates_skipped：0 处' in render(rep)


def test_a_stage_missing_from_the_table_raises(monkeypatch):
    """当前七层都要有一行：「没有记账键」与「没被声明过」不是一回事。"""
    monkeypatch.delitem(acc.ACCOUNTING, STAGE)
    with pytest.raises(AccountingKeyUnregistered, match='没有一行'):
        build(sid='s', node=NODE, freshness=_fresh(),
              outcomes=[ran('a')], registry=_registry(_rule(rule='a', sev='info')))


def test_accounting_covers_exactly_the_current_stages():
    """退役层不得靠空行继续伪装成现役；声明面与 STAGES 必须逐值闭合。"""
    from contracts.invariants import STAGES
    assert set(acc.ACCOUNTING) == set(STAGES)


def test_a_recorded_zero_is_not_the_same_as_unrecorded(monkeypatch):
    reg = _registry(_rule(rule='a', sev='info'))
    spec = _spec(('n', '数一个数'))
    rep = _build(reg, [ran('a')], accounting={'n': 0}, spec=spec, monkeypatch=monkeypatch)
    assert rep.accounting[0].recorded is True and rep.accounting[0].count == 0


def test_an_unrecorded_line_cannot_carry_a_count():
    from contracts.schema.report import AccountingLine
    with pytest.raises(ValueError, match='没记'):
        AccountingLine(key='k', item='i', recorded=False, count=3)


# ───────── 来源在前，判定在后 ─────────

def test_provenance_comes_before_the_rules_in_the_rendered_report(monkeypatch):
    """`Q-4`：对不上时报出来的一堆违规是「拿新尺子量旧布」，不说破就会被当成一次回归。"""
    reg = _registry(_rule(rule='a', sev='info'))
    text = render(_build(reg, [ran('a')], monkeypatch=monkeypatch))
    assert text.index('── 来源 ──') < text.index('── 判据 ──')


def test_ghost_edges_are_named_not_just_counted(monkeypatch):
    """`Q-4` 亲自点过的 bug：原型对「取不到指纹」的条目**静默跳过**。"""
    monkeypatch.setitem(acc.ACCOUNTING, STAGE, ())
    reg = _registry(_rule(rule='a', sev='info'))
    rep = build(sid='s', node=NODE, freshness=_fresh(_ghost('stage4/plan_chapters.INVALID.json')),
                outcomes=[ran('a')], registry=reg)
    assert rep.provenance.ghosts == ['stage4/plan_chapters.INVALID.json']
    assert 'stage4/plan_chapters.INVALID.json' in render(rep)
    assert rep.provenance.freshness == 'unknown', '幽灵边**永不 fresh**'


def test_the_ghost_list_cannot_be_hand_written():
    """点名清单只能从逐条边派生——手抄一份必然与判定分家。"""
    with pytest.raises(ValueError, match='ghosts'):
        ProvenanceSection(freshness='unknown', reason='ghost', ghosts=['我编的'],
                          edges=[{'key': 'a/b.json', 'state': 'fresh', 'reason': 'match',
                                  'recorded_file': None, 'now_file': None,
                                  'recorded_consumed': None, 'now_consumed': None}])


def test_the_three_state_keys_survive_as_three_states(monkeypatch):
    """`None` = 键不在（这份产物太老），与 `False` 不是一回事。"""
    reg = _registry(_rule(rule='a', sev='info'))
    rep = _build(reg, [ran('a')], monkeypatch=monkeypatch, revalidated=None, origin=None)
    assert '键不在' in render(rep)
    rep2 = _build(reg, [ran('a')], monkeypatch=monkeypatch, revalidated=False)
    assert rep2.provenance.revalidated is False
    assert 'revalidated：False' in render(rep2)


# ───────── 服务端算的那几个数不许与 rules 分家（`R-11.7`） ─────────

def _line(**kw) -> RuleLine:
    base = dict(rule='a', sev='info', status='ok', item='x', requirement='R-4.5')
    base.update(kw)
    return RuleLine(**base)


def _report(rules, **kw) -> CheckReport:
    base = dict(sid='s', node=NODE, stage=STAGE,
                provenance=ProvenanceSection(freshness='unknown', reason='no_inputs'),
                rules=rules, badge=badge_of(rules), passed=badge_of(rules) != 'fail',
                counts=StatusCounts.of(rules))
    base.update(kw)
    return CheckReport(**base)


def test_a_badge_that_disagrees_with_the_rules_raises():
    with pytest.raises(ValueError, match='徽章只有一处算'):
        _report([_line()], badge='fail', passed=False)


def test_counts_that_disagree_with_the_rules_raise():
    with pytest.raises(ValueError, match='counts'):
        _report([_line()], counts=StatusCounts(ok=9, hit=0, unver=0, skipped=0))


def test_the_same_rule_cannot_appear_twice():
    with pytest.raises(ValueError, match='不止一行'):
        _report([_line(), _line()])


# ───────── `Q-9`：词表判据永远打印基线 ─────────

def test_a_wordlist_rule_always_carries_its_baseline_into_the_report(monkeypatch):
    """两段合起来的链：注册期拦住「没填基线」，构造期原样带出来。"""
    reg = _registry(_rule(rule='w', sev='info', wordlist=True,
                          baseline='头牌 0/17，三岁 2/40'))
    rep = _build(reg, [ran('w')], monkeypatch=monkeypatch)
    assert rep.rules[0].baseline == '头牌 0/17，三岁 2/40'
    assert '基线：头牌 0/17，三岁 2/40' in render(rep)


# ───────── 守卫的守卫 ─────────

def test_every_status_is_reachable_and_rendered(monkeypatch):
    """四态都要有人走过一遍——`STATUS` 加了第五个值而没人处理时，这条会红。"""
    reg = _registry(_rule(rule='h', sev='hard'), _rule(rule='o', sev='info'),
                    _rule(rule='u', sev='info'), _rule(rule='s', sev='info'))
    reg_hit = finding('h', detail='命中了', evidence=['x'], registry=reg)
    rep = _build(reg, [ran('h', [reg_hit]), ran('o'), unverifiable('u', '没有样本')],
                 monkeypatch=monkeypatch)
    seen = {r.status for r in rep.rules}
    assert seen == set(STATUS), f'只走到 {sorted(seen)}——四态没跑全'
    text = render(rep)
    for mark in ('[OK]', '[!!]', '[??]', '[--]'):
        assert mark in text, f'{mark} 没出现在渲染结果里'
