#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
test_rules_registry.py —— 注册期那七条校验，逐条要有一个会红的反例。

一条「据说会拦住 X」的校验，如果没有一个反例证明它真的会拦，
它与「只写在注释里的约束」没有区别。
"""
from __future__ import annotations

import pytest

from contracts.rules import Registry, Rule


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


def test_wordlist_rule_must_declare_its_baseline():
    """没有基线数字的词表判据，等于不知道自己在说什么。"""
    with pytest.raises(ValueError, match='baseline'):
        Registry().register(_ok(wordlist=True))
    # 填了就过
    Registry().register(_ok(wordlist=True, baseline='头牌 0/17，三岁 2/40；人读真违规 6/17'))


def test_registry_covers_exactly_the_ported_stages():
    """**两个方向同时钉住。**（它换掉了 `test_registry_starts_empty`——那条在
    stage3 进 `PORTED_STAGES` 那一刻必然红，而它守的东西这一条全都守着，还多守一个方向。）

    · 注册了却没进 `PORTED_STAGES` ⇒ 正向覆盖率**不会查它**，判据白写；
    · 进了 `PORTED_STAGES` 却没注册 ⇒ 那张表在**说谎**，正向覆盖率成了摆设。
    """
    from contracts.rules import PORTED_STAGES, REGISTRY
    assert {r.stage for r in REGISTRY.all()} == set(PORTED_STAGES)
    assert len(REGISTRY) > 0, '注册表空了——`contracts/judges/__init__.py` 多半没被 import'


def test_the_judges_package_declares_what_it_loaded():
    """`LOADED` 与 `PORTED_STAGES` 分家 ⇒ 有一层的判据行写了却没被 import，
    而**没被 import 的判据行与不存在没有区别**。"""
    from contracts.judges import LOADED
    from contracts.rules import PORTED_STAGES
    assert set(LOADED) == set(PORTED_STAGES)


def test_registering_a_stage_outside_the_ported_set_is_visible():
    """守卫的守卫：上面那条靠的是集合相等，而集合相等在**两边都空**时也成立。"""
    from contracts.rules import PORTED_STAGES, REGISTRY
    assert REGISTRY.of_stage('stage3'), 'stage3 的判据行一条都没注册上'
    assert 'stage3' in PORTED_STAGES
