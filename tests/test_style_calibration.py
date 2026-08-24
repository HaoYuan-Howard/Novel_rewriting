#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""单臂 Stage5 风格标定工具可复跑，自动反哺边界由规则表锁定。"""
from __future__ import annotations

import tempfile
from pathlib import Path

from contracts.rules import REGISTRY
from tools.calibrate_style_corpus import _percentile, _split, analyze


def test_style_calibration_is_deterministic_and_keeps_a_held_out_split():
    names = ['合成甲_1', '合成乙_2', '合成丙_3']
    assert _split(names) == _split(names)
    assert set(_split(names).values()) == {'calibration', 'validation'}
    assert _percentile([0, 1, 2], .95) == 1.9

    empty = Path(tempfile.mkdtemp(prefix='style-calibration-'))
    first = analyze(empty)
    second = analyze(empty)
    assert first == second
    assert first['summaries']['calibration']['books'] == 0


def test_only_unique_style_repairs_may_enter_paid_revision():
    assert 'dialogue_halved' not in {row.rule for row in REGISTRY.of_stage('stage5')}
    assert REGISTRY.get('layout_drift').repair == 'UNIQUE'
    for rule in ('dialogue_fewer', 'dialogue_broken', 'dialogue_ratio_drift',
                 'band_drift', 'para_len_drift'):
        assert REGISTRY.get(rule).repair == 'FREE', rule
    assert REGISTRY.get('short_run_report').sev == 'metric'
    assert REGISTRY.get('short_run_report').repair == 'NONE'
