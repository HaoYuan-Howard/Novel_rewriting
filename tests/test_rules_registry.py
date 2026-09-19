#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
test_rules_registry.py —— 注册期那**八**条校验，逐条要有一个会红的反例。

一条「据说会拦住 X」的校验，如果没有一个反例证明它真的会拦，
它与「只写在注释里的约束」没有区别。
"""
from __future__ import annotations

import pytest

from contracts.rules import PromptSurface, Registry, Rule


def _ok(**kw) -> Rule:
    base = dict(rule='r_demo', stage='stage5', item='示例', sev='info',
                who_checks='HARD', requirement='R-7.3', owner='stage5')
    base.update(kw)
    return Rule(**base)


def test_registers_a_wellformed_rule():
    reg = Registry()
    reg.register(_ok())
    assert len(reg) == 1 and 'r_demo' in reg


def test_duplicate_rule_id_raises():
    reg = Registry()
    reg.register(_ok())
    with pytest.raises(ValueError, match='重复'):
        reg.register(_ok())


def test_reference_rule_requires_a_registered_prompt_surface_that_provides_every_id_space():
    with pytest.raises(ValueError, match='没有 prompt_surface'):
        Registry().register(_ok(needs_id_spaces=('events',)))

    missing = Registry()
    missing.register_prompt_surface(PromptSurface('demo', ('events',)))
    with pytest.raises(ValueError, match='缺少 ID spaces'):
        missing.register(_ok(
            needs_id_spaces=('event_units', 'events'), prompt_surface='demo'))

    complete = Registry()
    complete.register_prompt_surface(PromptSurface(
        'demo', ('event_units', 'events')))
    complete.register(_ok(
        needs_id_spaces=('event_units', 'events'), prompt_surface='demo'))
    assert len(complete) == 1


def test_every_registered_rule_id_need_is_satisfied_by_its_prompt_surface():
    from contracts.rules import REGISTRY

    for rule in REGISTRY.all():
        if not rule.needs_id_spaces:
            continue
        surface = REGISTRY.prompt_surface(rule.prompt_surface or '')
        assert set(rule.needs_id_spaces) <= set(surface.provides_id_spaces)


def test_stage4_registered_ids_match_the_current_plot_and_evidence_catalog():
    from contracts.rules import REGISTRY
    from novel.stages.stage4.plot_binding import build_evidence_catalog
    from tests.plot_fixtures import blueprint

    catalog = build_evidence_catalog(blueprint())[1]
    surface = REGISTRY.prompt_surface('stage4.plan')
    assert {'beats', 'states', 'constraints', 'evidence_anchors'} <= set(surface.provides_id_spaces)
    assert not {'fact_atoms', 'source_stages'} & set(surface.provides_id_spaces)
    assert catalog['blueprint']['beats'][0]['beat_id'] == 'b01_01'
    assert catalog['blueprint']['beats'][0]['evidence_refs'] == ['e01_01']
    assert 'e01_01' in catalog['evidence_refs']


def test_bad_severity_raises():
    with pytest.raises(ValueError, match='sev'):
        Registry().register(_ok(sev='warning'))


def test_bad_owner_raises():
    with pytest.raises(ValueError, match='owner'):
        Registry().register(_ok(owner='stage9'))


@pytest.mark.parametrize('bad', ['', 'R-7', 'Q-1', 'B-01', '见 R-7.3', 'R7.3'])
def test_requirement_must_be_a_wellformed_r_id(bad):
    with pytest.raises(ValueError, match='requirement'):
        Registry().register(_ok(requirement=bad))


#: 进反哺的判据必须同时写出「这一条要怎么改」（见 `test_a_directive_is_required_...`）。
#: 下面几条查的是别的校验，所以一律带着它，免得被第 ⑦ 条先拦下来。
_FIX = dict(repair='UNIQUE', directive='把它改回去。')


def test_repairable_rule_must_be_owned_by_stage5():
    """根因不在写手身上的条目进反哺 = 花钱让它去修一个它修不了的东西。"""
    with pytest.raises(ValueError, match='反哺|repair'):
        Registry().register(_ok(owner='stage2', **_FIX))


def test_unver_rule_cannot_be_repairable():
    with pytest.raises(ValueError, match='unver'):
        Registry().register(_ok(sev='unver', **_FIX))


def test_disclosable_unver_requires_unver_severity():
    """Q-13 披露分层：hard/info/metric 的命中是**有结论的**，走修复或阻断，
    不存在「降为披露」这回事；置位只留给内容质量层面的 unver。"""
    with pytest.raises(ValueError, match='disclosable_unver'):
        Registry().register(_ok(sev='hard', disclosable_unver=True))


def test_degradable_requires_hard_with_repair_path():
    """可降级目标 = 生成期按 HARD 修复、预算耗尽后降披露 ⇒ 必须既有修复路。
    没有修复路的 HARD 是交付硬约束，不存在「耗尽后降级」。"""
    with pytest.raises(ValueError, match='degradable'):
        Registry().register(_ok(sev='hard', degradable=True))   # 无 repair
    Registry().register(_ok(sev='hard', degradable=True, **_FIX))  # 有修复路则过


def test_disclosure_and_degradable_sets_are_pinned():
    """披露集合与可降级集合是逐条人工裁决（Q-13 / §七），双向钉扎：

    · 反向：新置位判据未同步更新本钉扎 ⇒ 集合悄悄漂，披露面/降级面失控；
    · 正向：集合空了 ⇒ 分层机制被整体绕开，上面那条反向断言照样绿。
    """
    from contracts.rules import REGISTRY
    disclosed = {r.rule for r in REGISTRY.all() if r.disclosable_unver}
    assert disclosed == {
        'character_semantics_unverifiable', 'secrets_absent', 'plot_semantics',
        's0_correctness'}, \
        '披露集合变化须同步 Q-13 理由与本钉扎'
    degradable = {r.rule for r in REGISTRY.all() if r.degradable}
    assert degradable == {'paragraph_budget_missed'}, \
        '可降级集合变化须同步契约 §七 与本钉扎'
    # 披露集合同时是机械准入的唯一披露面（单一落点）
    from novel.orchestrator.mechanical_admission import SEMANTIC_UNREVIEWED_RULES
    assert SEMANTIC_UNREVIEWED_RULES == frozenset(disclosed)


@pytest.mark.parametrize('bad', ['unique', 'free', 'none', '', 'YES'])
def test_repair_bucket_must_be_one_of_the_three(bad):
    """⚠ `Literal` 在运行时不拦任何东西，而这一格写歪的后果是**会花钱**：
    `repair='unique'`（小写）原样存进去，然后 `!= 'NONE'` 判真。"""
    with pytest.raises(ValueError, match='repair'):
        Registry().register(_ok(repair=bad, directive='把它改回去。'))


def test_a_directive_is_required_exactly_when_the_rule_feeds_back():
    """**双向**：进反哺却没有指令 ⇒ 那一轮会把一条空指令发给写手（花了钱、没说改什么）；
    不进反哺却写着指令 ⇒ 一句永远发不出去的话，下一个人会以为反哺在修这一条。"""
    with pytest.raises(ValueError, match='directive'):
        Registry().register(_ok(repair='UNIQUE'))
    with pytest.raises(ValueError, match='directive'):
        Registry().register(_ok(directive='改回去。'))
    # 两侧对齐就过（两种对齐各一个）
    Registry().register(_ok(**_FIX))
    Registry().register(_ok())


# ── ⑧ `fix` 那条修复路由必须真跑得起来（F5-1）────────────────────────────────
#
# ⚠ 这一格存在的**全部理由**就是「有东西在查它」：原型那句「修法：…」是写在文案里的
#   散文，而**拼错一个 flag 名不会有任何东西出声**（LESSONS 第 11 条）。
#   下面四条各消灭一种「表里看着像能修、点下去修不了」。

def _s2(**kw) -> Rule:
    """一条 stage2 判据。⚠ `owner` 与 `requirement` 都要落在本层，否则先被 ②④ 拦下。"""
    return _ok(stage='stage2', owner='stage2', requirement='R-3.2', repair='NONE', **kw)


def test_a_fix_route_is_accepted_when_the_node_and_flag_both_exist():
    """正例：`stage2.md` §5.1 里真实存在的那两条修法，注册得进去。

    ⚠ **这一条是上面四条反例的「守卫的守卫」**：`_validate` ⑧ 要是写成恒抛，
    下面每条 `pytest.raises` 都会绿，而真表一条也注册不进来。
    """
    reg = Registry()
    reg.register(_s2(rule='r_fix_free', fix=('stage2.blueprint', '--renormalize')))
    reg.register(_s2(rule='r_fix_paid', fix=('stage2.characters', '--resample')))
    assert len(reg) == 2


def test_a_fix_route_may_not_name_a_node_that_does_not_exist():
    """节点名拼错 ⇒ 界面上那个「去修」按钮指到一个跑不起来的地址。"""
    with pytest.raises(ValueError, match='不在 `NODES` 里'):
        Registry().register(_s2(fix=('stage2.bluepirnt', '--renormalize')))


def test_a_fix_route_may_not_name_a_flag_the_node_does_not_accept():
    """⭐ **这一条是本格最值钱的那个反例。**

    flag 不在该节点的 `flags` 里 ⇒ `build_argv` 会当场拒收，
    于是「这一条能 0 成本修」是一句**永远兑现不了**的承诺，而表里只看得见
    一句「修法：…」。实例：`--revise` 是 stage5 的，`stage2.blueprint` 接不了它。
    """
    with pytest.raises(ValueError, match='不在 `NODES'):
        Registry().register(_s2(fix=('stage2.blueprint', '--revise')))


def test_a_fix_route_may_not_point_at_another_layer():
    """指到别的层 ⇒ 报告让人去改一个**不产这份产物**的节点。"""
    with pytest.raises(ValueError, match='属 stage5'):
        Registry().register(_s2(fix=('stage5.write', '--dry-run')))


def test_a_fix_route_needs_at_least_one_flag():
    """只写节点名 = 「去跑一次默认路径」，而 stage2 的默认路径**要花钱**
    ——那对一条声称 0 成本的修复路是假话。"""
    with pytest.raises(ValueError, match='至少要'):
        Registry().register(_s2(fix=('stage2.blueprint',)))


def test_a_valued_fix_route_needs_a_value_or_a_finding_source():
    with pytest.raises(ValueError, match='没有值.*finding'):
        Registry().register(_s2(fix=('stage2.blueprint', '--chapters')))

    reg = Registry()
    reg.register(_s2(
        fix=('stage2.blueprint', '--chapters'),
        fix_args_from_finding=('--chapters',),
    ))
    assert len(reg) == 1


def test_a_fix_argument_cannot_have_two_sources_or_a_stray_declaration():
    with pytest.raises(ValueError, match='两个来源'):
        Registry().register(_s2(
            fix=('stage2.blueprint', '--chapters', '11'),
            fix_args_from_finding=('--chapters',),
        ))
    with pytest.raises(ValueError, match='没有对应'):
        Registry().register(_s2(
            fix=('stage2.blueprint', '--renormalize'),
            fix_args_from_finding=('--chapters',),
        ))


def test_a_static_valued_fix_argument_cannot_be_only_whitespace():
    with pytest.raises(ValueError, match='空白值'):
        Registry().register(_s2(
            fix=('stage2.blueprint', '--chapters', '   '),
        ))


def test_the_fix_route_is_what_makes_the_cost_colour_derivable():
    """⭐⭐ **`fix` 的收益就是它能过 `is_paid`**，而散文过不了。

    界面据此把「0 成本修」与「要再花一次钱」画成两个颜色。这一条钉住那两条
    真实修法**确实**落在贵贱的两侧——它们要是一起变成付费（或一起免费），
    §5.1 那一列就不再携带信息了。
    """
    from novel.orchestrator.nodes import is_paid
    free = _s2(rule='r_c_free', fix=('stage2.blueprint', '--renormalize'))
    paid = _s2(rule='r_c_paid', fix=('stage2.global', '--purge-role'))
    assert is_paid(free.fix[0], free.fix[1:]) is False
    assert is_paid(paid.fix[0], paid.fix[1:]) is True


def test_wordlist_rule_must_declare_its_baseline():
    """没有基线数字的词表判据，等于不知道自己在说什么。"""
    with pytest.raises(ValueError, match='baseline'):
        Registry().register(_ok(wordlist=True))
    # 填了就过
    Registry().register(_ok(wordlist=True, baseline='头牌 0/17，三岁 2/40；人读真违规 6/17'))


def test_registry_covers_exactly_the_current_stages():
    """七个现役层必须各有判据，且不得注册不存在的阶段。"""
    from contracts.invariants import STAGES
    from contracts.rules import REGISTRY
    assert {r.stage for r in REGISTRY.all()} == set(STAGES)
    assert len(REGISTRY) > 0, '注册表空了——`contracts/judges/__init__.py` 多半没被 import'


def test_registry_is_not_vacuously_empty():
    from contracts.rules import REGISTRY
    assert all(REGISTRY.of_stage(stage) for stage in ('stage0', 'stage1', 'stage2',
                                                      'stage4', 'stage5', 'stage6',
                                                      'stage7'))
    assert not REGISTRY.of_stage('stage3'), '退役 Stage3 不得残留现役判据'


def test_semantic_check_kind_is_confined_to_stage6():
    """「语义校验只许出现在 stage6，其余层全机械」——2026-08-14 产品裁决的当前形态。

    · 反向：语义判据跑出这一层 ⇒ 裁决被静默突破——语义判据要花钱、结论要靠
      审判模型，放错层就是把「确定性可查」的层悄悄换成「花钱问模型」；
    · 正向（守卫的守卫）：语义判据的层集合空着，上面那条反向断言照样绿。

    ⚠ 原裁决写的是 `{stage5, stage6}`。stage5 那两条（`semantic_secret_leak` /
    `semantic_secret_coverage`）随 `stage5.semantic` 节点于 2026-08-17 一并下线
    （用户裁决），于是层集合收敛成 `{stage6}`。**这一条仍是双向断言**：
    语义判据既不许跑到别的层，也不许在 stage6 上一条不剩。
    """
    from contracts.rules import REGISTRY
    strays = [(r.rule, r.stage) for r in REGISTRY.all()
              if r.check_kind == 'semantic' and r.stage != 'stage6']
    assert strays == []
    semantic_stages = {r.stage for r in REGISTRY.all() if r.check_kind == 'semantic'}
    assert semantic_stages == {'stage6'}, \
        '语义判据的层集合变了——stage6 的语义判据一条都不能少，也不许扩到别的层'
