#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Stage3 v2 机械基线的判据注册表。"""
from __future__ import annotations

from contracts.rules import REGISTRY, Rule

STAGE = 'stage3'


def _hard(rule: str, item: str, requirement: str, **kw) -> Rule:
    return REGISTRY.register(Rule(
        rule=rule,
        stage=STAGE,
        item=item,
        sev='hard',
        who_checks='HARD',
        requirement=requirement,
        owner=STAGE,
        **kw,
    ))


_hard(
    'measurable_drift',
    '机械基线与当前 Stage1 一致',
    'R-4.1',
    fix=('stage3.style',),
    note='两块摊平后取叶子并集逐项比较；测量算法版本也进入 Stage3 consumed 投影。',
)
