#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""test_repair_rounds —— `tools/repair_rounds.py` 的只读聚合冒烟（CS-patch）。

合成最小 `_service` 布局（ledger 两行＋一个工作单元的两轮接受分项），断言
三张表的机械读数：fresh/completion、首过率、失败码×轮次。工具是读数器不是
判官（R-13.6）：不判定缺陷，只把逐轮失败集摆成可读矩阵。
"""
from __future__ import annotations

import json

from tools.repair_rounds import main


def _make_base(tmp_path):
    base = tmp_path / 'out'
    (base / '_service').mkdir(parents=True)
    (base / '_service' / 'ledger.jsonl').write_text('\n'.join(json.dumps(r) for r in (
        {'node': 'str06.stage2.chapters', 'cached': False, 'completion_tokens': 100},
        {'node': 'str06.stage2.chapters', 'cached': False, 'completion_tokens': 50},
        {'node': 'str06.stage2.chapters', 'cached': True, 'completion_tokens': 0},
    )), encoding='utf-8')
    unit = base / '_service' / 'runs' / 'sid1' / 'work_units' / 'unit1'
    unit.mkdir(parents=True)
    for name, accepted, errors, at in (
            ('accept_aaaa', False,
             ['第1章拍1 participants 引用未知人物 ghost'], 1),
            ('accept_bbbb', True, [], 2)):
        (unit / f'{name}.json').write_text(json.dumps({
            'payload': {'node': 'str06.stage2.chapters', 'scope': 'chapter:1',
                        'owner': 'ch01', 'turn_id': 't1', 'attempt_id': name,
                        'mode': 'json', 'parse': 'ok' if accepted else 'failed',
                        'salvage': 'none', 'errors': errors, 'accepted': accepted,
                        'created_at': at}}, ensure_ascii=False), encoding='utf-8')
    return base


def test_repair_rounds_tables(tmp_path, capsys):
    base = _make_base(tmp_path)
    assert main([str(base), '--sid', 'sid1']) == 0
    out = capsys.readouterr().out
    assert 'str06.stage2.chapters      fresh=   2  completion=     150' in out, \
        '缓存重放不计 fresh（ledger 为权威账）'
    assert '首过率：0/1' in out, '第 1 轮失败即非首过'
    assert '返修×1' in out
    assert '第1轮×1' in out and '第1章拍1 participants' in out, '失败码矩阵携带定位段'


def test_repair_rounds_missing_layout_is_quiet(tmp_path, capsys):
    base = tmp_path / 'empty'
    base.mkdir()
    assert main([str(base)]) == 0
    assert '无接受记录' in capsys.readouterr().out
