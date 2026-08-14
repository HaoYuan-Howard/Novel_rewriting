#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""VAL-02 的守卫：stage4/6/7 的每条 HARD 都要有一个**真的会红**的最小样本。

一条「据说会拦住 X」的 HARD，如果没有一个反例证明它真的会拦，
它与「只写在注释里的约束」没有区别（LESSONS #1/#2）。

守卫从第一天就生效：**新增一条 HARD 而没在 `tests/hard_cases.py` 登记反例
（也没进 `ALLOWED_MISSING` 白名单）⇒ 当场红。**
"""
from __future__ import annotations

import pytest

from contracts.schema.common import Finding
from tests.hard_cases import ALLOWED_MISSING, CASES

_STAGES = ('stage4', 'stage6', 'stage7')


def _hard_rules() -> dict[str, str]:
    from contracts.rules import REGISTRY
    return {r.rule: r.stage for r in REGISTRY.all()
            if r.sev == 'hard' and r.stage in _STAGES}


def test_every_hard_rule_of_stage4_6_7_is_registered_with_a_counterexample_or_explicitly_deferred():
    """两个方向同时钉住：漏登记 ⇒ 红（守卫的本体）；多登记 ⇒ 红（表在点名死人）。"""
    hard = _hard_rules()
    assert hard, '三层一条 HARD 都没有——判据表多半没被 import'
    missing = sorted(set(hard) - set(CASES) - ALLOWED_MISSING)
    assert not missing, (
        f'这些 HARD 没有登记反例也没进白名单：{missing}。'
        f'⇒ 在 tests/hard_cases.py 里补一个最小会红样本（调真实判据入口），'
        f'确实要推迟就进 ALLOWED_MISSING 并写明批次')
    stray = sorted(set(CASES) - set(hard))
    assert not stray, f'CASES 点名了不存在/非 HARD 的判据：{stray}——判据改名或退役要跟着改这张表'
    wrong_stage = sorted(r for r, c in CASES.items() if c.stage != hard[r])
    assert not wrong_stage, f'这些样本登记的层与判据表不符：{wrong_stage}'


def test_allowed_missing_only_names_rules_that_still_exist():
    """白名单只准点名仍存在的 HARD，且不许与 CASES 重叠——重叠时没人知道哪边算数。"""
    hard = set(_hard_rules())
    assert ALLOWED_MISSING <= hard, f'白名单点名了死人：{sorted(ALLOWED_MISSING - hard)}'
    assert not (ALLOWED_MISSING & set(CASES)), \
        f'既登记了反例又在白名单里：{sorted(ALLOWED_MISSING & set(CASES))}'


@pytest.mark.parametrize('rule', sorted(CASES))
def test_each_counterexample_actually_reddens_exactly_its_target_rule(rule):
    """逐条验证：样本喂进**真实判据入口**后，目标判据真的产出了 `Finding`。

    ⚠ 只断言目标判据命中，不断言「不溅射」——部分判据共享前置，
    强断言不溅射会把构造成本翻倍；最小性靠 hard_cases 里的自律。
    产出的必须是真 `Finding`（走了 `finding()` 工厂 ⇒ sev/owner 与注册表一致由载体保证）。
    """
    findings = CASES[rule].build()
    assert findings, f'{rule} 的样本一条结论都没产出来'
    assert all(isinstance(f, Finding) for f in findings), \
        f'{rule} 的样本产出了非 Finding 对象——没走真实判据入口'
    got = {f.rule for f in findings}
    assert rule in got, f'{rule} 的样本没让目标判据变红（只命中了 {sorted(got)}）'
