#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""VAL-02：新链每条 HARD 都有真实会红的最小反例（F8a 重写）。

【F8a 重写】旧文件盯的是旧 Stage4/6/7 判据；那些判据随旧链退役。现在盯新链：
`tests/hard_cases.py` 登记跨判据的 consolidated 抽样（每条新链 HARD 至少一个最小
会红样本），`tests/test_str06_*.py` 是各能力的完整反例套件。两条合起来覆盖全部
新链 HARD——`test_every_new_chain_hard_rule_is_covered_somewhere` 钉住这个并集，
防止「哪一边都以为另一边在管」。
"""
from __future__ import annotations

import re
from pathlib import Path

import pytest

from tests.hard_cases import CASES

_ROOT = Path(__file__).resolve().parent
_STAGES = ('stage4', 'stage5', 'stage7')


def _hard_rules() -> dict[str, str]:
    from contracts.rules import REGISTRY
    return {row.rule: row.stage for row in REGISTRY.all()
            if row.sev == 'hard' and row.stage in _STAGES}


def _str06_suite_text() -> str:
    """新链反例套件的全文（覆盖检查的扫描面）。"""
    return '\n'.join(
        path.read_text(encoding='utf-8')
        for path in sorted(_ROOT.glob('test_str06_*.py')))


def test_every_new_chain_hard_rule_is_covered_somewhere():
    """并集覆盖：每条新链 HARD 至少在一个地方有真实会红的反例。

    反例（注册表空着 / 套件没跑）：`hard` 为空 ⇒ 判据表没被 import；
    stray（CASES 点名不存在的判据）与 wrong（层登记不符）同样当场红。
    """
    hard = _hard_rules()
    assert hard, '新链一条 HARD 都没有——判据表多半没被 import'
    assert not (stray := sorted(set(CASES) - set(hard))), (
        f'CASES 点名了不存在或非 HARD 的判据：{stray}')
    assert not (wrong := sorted(rule for rule, case in CASES.items()
                                if case.stage != hard[rule])), (
        f'这些样本登记的层与判据表不符：{wrong}')
    suites = _str06_suite_text()
    uncovered = sorted(
        rule for rule in hard
        if rule not in CASES
        and not re.search(rf'\b{re.escape(rule)}\b', suites))
    assert not uncovered, (
        f'这些 HARD 在 hard_cases 与 str06 套件里都找不到反例：{uncovered}')


def test_the_coverage_scan_actually_scans_something():
    """守卫的守卫：扫描面塌成空文件时，上面那条会全绿。"""
    text = _str06_suite_text()
    assert len(text) > 10_000, 'str06 反例套件扫描面异常小——glob 没匹配到文件？'


@pytest.mark.parametrize('rule', sorted(CASES))
def test_each_counterexample_actually_reddens_its_target_rule(rule):
    """逐条真跑：样本必须产出结论，且命中它登记的那一条。

    `expect` 是真实判据入口的输出子串（检查项名或 M 系前缀）——不是替身断言，
    换一段与生产无关的文本当场红。
    """
    case = CASES[rule]
    findings = case.build()
    assert findings, f'{rule} 的样本一条结论都没产出来'
    assert all(isinstance(finding, str) and finding.strip() for finding in findings), (
        f'{rule} 的样本结论不是非空字符串：{findings!r}')
    assert any(case.expect in finding for finding in findings), (
        f'{rule} 的样本没有命中 {case.expect!r}，只产出了 {findings}')
