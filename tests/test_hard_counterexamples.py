#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""VAL-02 的守卫：stage4/6/7 的每条 HARD 都要有一个**真的会红**的最小样本。

一条「据说会拦住 X」的 HARD，如果没有一个反例证明它真的会拦，
它与「只写在注释里的约束」没有区别（LESSONS #1/#2）。

新增一条 HARD 而没在 `tests/hard_cases.py` 登记反例会当场红。
"""
from __future__ import annotations

import pytest

from contracts.schema.common import Finding
from tests.hard_cases import CASES

_STAGES = ('stage4', 'stage6', 'stage7')


def _hard_rules() -> dict[str, str]:
    from contracts.rules import REGISTRY
    return {r.rule: r.stage for r in REGISTRY.all()
            if r.sev == 'hard' and r.stage in _STAGES}


def test_every_hard_rule_of_stage4_6_7_has_a_counterexample():
    """两个方向同时钉住：漏登记 ⇒ 红（守卫的本体）；多登记 ⇒ 红（表在点名死人）。"""
    hard = _hard_rules()
    assert hard, '三层一条 HARD 都没有——判据表多半没被 import'
    missing = sorted(set(hard) - set(CASES))
    assert not missing, (
        f'这些 HARD 没有登记反例：{missing}。'
        f'⇒ 在 tests/hard_cases.py 里补一个调用真实判据入口的最小会红样本')
    stray = sorted(set(CASES) - set(hard))
    assert not stray, f'CASES 点名了不存在/非 HARD 的判据：{stray}——判据改名或退役要跟着改这张表'
    wrong_stage = sorted(r for r, c in CASES.items() if c.stage != hard[r])
    assert not wrong_stage, f'这些样本登记的层与判据表不符：{wrong_stage}'


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
