#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
test_rules_registry.py —— 注册期那五条校验，逐条要有一个会红的反例。

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


def test_repairable_rule_must_be_owned_by_stage5():
    """根因不在写手身上的条目进反哺 = 花钱让它去修一个它修不了的东西。"""
    with pytest.raises(ValueError, match='反哺|repair'):
        Registry().register(_ok(owner='stage2', repair='UNIQUE'))


def test_unver_rule_cannot_be_repairable():
    with pytest.raises(ValueError, match='unver'):
        Registry().register(_ok(sev='unver', repair='UNIQUE'))


def test_wordlist_rule_must_declare_its_baseline():
    """没有基线数字的词表判据，等于不知道自己在说什么。"""
    with pytest.raises(ValueError, match='baseline'):
        Registry().register(_ok(wordlist=True))
    # 填了就过
    Registry().register(_ok(wordlist=True, baseline='头牌 0/17，三岁 2/40；人读真违规 6/17'))


def test_registry_starts_empty():
    """判据随各 stage 移植过来时才注册。现在是空的，这是对的，不是漏了。"""
    from contracts.rules import PORTED_STAGES, REGISTRY
    assert len(REGISTRY) == 0
    assert PORTED_STAGES == frozenset()
