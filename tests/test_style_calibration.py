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
    """旧链风格判据（含修复类别 UNIQUE/FREE/NONE）已随正文 Judge 整族退役。

    新链 Stage5 的语义判定只剩「语义未评如实保留」的披露位；章级机械验
    （body_length_band 等）不再驱动风格修复分类。标定工具保留为参考件，
    只做确定性分析（见上条），不再有注册判据面。
    """
    retired_style_rules = (
        'dialogue_halved', 'layout_drift', 'dialogue_fewer', 'dialogue_broken',
        'dialogue_ratio_drift', 'band_drift', 'para_len_drift',
        'short_run_report',
    )
    registered = {row.rule for row in REGISTRY.all()}
    assert not registered & set(retired_style_rules), \
        '旧风格判据不得在现役注册表复活'
    # 新链 stage5 现役判据只有章级机械验、Hook 机械验、回执绑定与披露位。
    stage5_rules = {row.rule for row in REGISTRY.of_stage('stage5')}
    assert stage5_rules == {
        'body_chapter_complete', 'body_withheld_literals', 'body_required_literals',
        'body_length_band',
        'body_semantics_unver', 'body_length_reading',
        'hook_mechanical', 'chapter_receipt_binding', 'hook_receipt_binding',
    }
