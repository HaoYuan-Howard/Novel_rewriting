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
    from novel.work_units import initialize_workflow
    initialize_workflow(base)
    (base / '_service' / 'ledger.jsonl').write_text('\n'.join(json.dumps(dict(r, sid='sid1')) for r in (
        {'node': 'str06.stage2.chapters', 'cached': False, 'completion_tokens': 100,
         'prompt_tokens': 120, 'completion_tokens_details': {'reasoning_tokens': 40}},
        {'node': 'str06.stage2.chapters', 'cached': False, 'completion_tokens': 50,
         'prompt_tokens': 80, 'completion_tokens_details': {'reasoning_tokens': 20}},
        {'node': 'str06.stage2.chapters', 'cached': True, 'completion_tokens': 0},
    )) + '\n', encoding='utf-8')
    import hashlib
    from novel.work_units import WorkUnitBudget
    unit = WorkUnitBudget(base, 'sid1', 'str06.stage2.chapters', scope='chapter:1')
    for name, accepted, errors, at in (
            ('zzzz', False,
             ['第1章拍1 participants 引用未知人物 ghost'], 2),
            ('aaaa', True, [], 1)):
        request = hashlib.sha256(name.encode()).hexdigest()
        turn = unit.reserve(request, run_id='metrics-fixture')
        unit.record_attempt(turn.turn_id, attempt_id=name, outcome='ok')
        unit.save_response(turn.turn_id, {'response': name})
        unit.save_acceptance(turn.turn_id, name, {
                        'node': 'str06.stage2.chapters', 'scope': 'chapter:1',
                        'request_sha256': request,
                        'owner': 'ch01', 'turn_id': turn.turn_id, 'attempt_id': name,
                        'mode': 'json', 'response_kind': 'result',
                        'parse': 'ok' if accepted else 'failed',
                        'salvage': 'none', 'errors': errors, 'accepted': accepted,
                        'created_at': at})
        if accepted:
            unit.mark_adopted(turn.turn_id)
        else:
            unit.mark_rejected(turn.turn_id)
    return base


def test_repair_rounds_tables(tmp_path, capsys):
    base = _make_base(tmp_path)
    assert main([str(base), '--sid', 'sid1']) == 0
    out = capsys.readouterr().out
    # CS-H H4（D 表计量口径）：输入／输出／推理三分列（推理不重复计入输出），
    # 缓存重放不计 fresh。
    assert 'str06.stage2.chapters      fresh=   2  输入=     200  输出=     150  推理=      60' in out, \
        '缓存重放不计 fresh（ledger 为权威账）'
    assert '当前首内容轮接受：0/1' in out, '按正式轮次排序，不按反序时间戳或随机ID'
    assert '修订内容轮×1' in out and '非历史首过率' in out
    assert '第1轮×1' in out and '第1章拍1 participants' in out, '失败码矩阵携带定位段'


def test_repair_rounds_missing_layout_is_quiet(tmp_path, capsys):
    base = tmp_path / 'empty'
    base.mkdir()
    assert main([str(base)]) == 0
    assert '无接受记录' in capsys.readouterr().out


def test_usage_unknown_failed_calls_and_sid_filter(tmp_path, capsys):
    base = _make_base(tmp_path)
    with (base / '_service/ledger.jsonl').open('a', encoding='utf-8') as stream:
        for row in ({'sid':'sid1','node':'str06.stage2.chapters','outcome':'failed','completion_tokens':None},
                    {'sid':'other','node':'other','completion_tokens':999999}):
            stream.write(json.dumps(row)+'\n')
    assert main([str(base), '--sid', 'sid1']) == 0
    output = capsys.readouterr().out
    assert '未知（已知小计150，缺失1次）' in output
    assert '999999' not in output and 'fresh=   3' in output


def test_malformed_complete_ledger_is_not_silently_skipped(tmp_path):
    import pytest
    base = _make_base(tmp_path)
    with (base / '_service/ledger.jsonl').open('a', encoding='utf-8') as stream:
        stream.write('not-json\n')
    with pytest.raises(ValueError):
        main([str(base), '--sid', 'sid1'])


def _unit_dir_of(base, sid='sid1', unit_id='f' * 64):
    from novel import paths
    return paths.work_unit_dir(base, sid, unit_id)


def test_lock_only_unit_directories_are_not_units(tmp_path, capsys):
    """采用等路径取事务锁会留下只有 ledger.json.lock 的空壳目录（csf4 根上 4 个）。

    分母是 work_units.json 登记的单元，不是目录扫描：空壳不是单元，既不崩溃也不进分母。
    """
    base = _make_base(tmp_path)
    shell = _unit_dir_of(base)
    shell.mkdir(parents=True)
    (shell / 'ledger.json.lock').write_bytes(b' ')
    assert main([str(base), '--sid', 'sid1']) == 0
    out = capsys.readouterr().out
    assert '当前首内容轮接受：0/1' in out, '空壳目录不改变分母'


def test_unregistered_unit_directory_with_content_is_refused(tmp_path):
    """账不闭合不能被悄悄移出分母：未登记却有锁文件之外内容的目录显式拒绝。"""
    import pytest
    base = _make_base(tmp_path)
    stray = _unit_dir_of(base)
    stray.mkdir(parents=True)
    (stray / 'ledger.json.lock').write_bytes(b' ')
    (stray / ('a' * 64 + '.json')).write_text('{}', encoding='utf-8')
    with pytest.raises(ValueError, match='未登记'):
        main([str(base), '--sid', 'sid1'])


def test_registered_unit_without_ledger_is_still_refused(tmp_path):
    """登记了却缺预算账是丢账，不是空壳：沿用关闭式拒绝，不当作没有这个单元。"""
    import pytest
    from novel.work_units import WorkUnitBudget, WorkUnitRefused
    base = _make_base(tmp_path)
    unit = WorkUnitBudget(base, 'sid1', 'str06.stage2.chapters', scope='chapter:1')
    unit.path.unlink()
    with pytest.raises(WorkUnitRefused):
        main([str(base), '--sid', 'sid1'])


def test_revalidation_is_not_a_new_content_round(tmp_path, capsys):
    from novel.work_units import WorkUnitBudget
    base = _make_base(tmp_path)
    unit = WorkUnitBudget(base, 'sid1', 'str06.stage2.chapters', scope='chapter:1')
    attempt, record = unit.acceptance_records()[0]
    unit.save_acceptance(record['turn_id'], attempt, {**record, 'created_at': 100})
    assert main([str(base), '--sid', 'sid1']) == 0
    out = capsys.readouterr().out
    assert '内容轮 2  attempt 2' in out and '修订内容轮×1' in out
