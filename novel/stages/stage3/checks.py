#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Stage3 v2 唯一机械判据：存量测量与当前 Stage1 一致。"""
from __future__ import annotations

from contracts.schema.report import finding
from contracts.schema.stage3 import Measurable
from novel.core.measure import drift, flatten
from novel.report.findings import Outcome, ran


def check(data: dict, *, measured_now: Measurable) -> list[Outcome]:
    """严格载体已经保证机械块完整；这里只比较可能真实漂移的叶子。"""
    stored = Measurable.model_validate(data['measurable'])
    return [_measurable_drift(stored, measured_now)]


def _measurable_drift(stored: Measurable, measured_now: Measurable) -> Outcome:
    """存量值与当前重算值按两块、取叶子并集逐项比较。"""
    findings = []
    scanned = []
    for scope in ('body', 'hook'):
        was = getattr(stored, scope)
        now = getattr(measured_now, scope)
        delta = drift(was, now)
        scanned.append(f'{scope} {len(flatten(now))} 项')
        if delta:
            findings.append(finding(
                'measurable_drift',
                detail=(
                    f'measurable.{scope} 有 {len(delta)} 项漂移（'
                    + '；'.join(f'{key} {before}→{after}'
                               for key, before, after in delta[:6])
                    + ('…' if len(delta) > 6 else '')
                    + '）；默认免费重跑 stage3.style 写回当前机械基线'
                ),
                evidence=[f'measurable.{scope}', *(key for key, _a, _b in delta)],
            ))
    return ran('measurable_drift', findings, scanned=' · '.join(scanned))
