#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
test_beacon.py —— 进度信标的写读、容错、折叠，以及 `mark` 子命令。

信标是**观察轨迹不是判定**（`novel/orchestrator/beacon.py` 模块头那条）。
这里钉四件事：append-only 的写读往返、半截行容错（只容忍最后一行）、
五态折叠规则（`fold` 唯一落点）、`PIPELINE_STEPS` 与 `NODES` / SKILL 工作流表的对账。

临时目录由 pytest 提供，测试结束后自动清理。
"""
from __future__ import annotations

import json

import pytest

from contracts.invariants import BEACON_STATES, NODES, PIPELINE_STEPS
from contracts.schema.orchestration import ProgressEvent
from novel.cli import exit_codes as X
from novel.cli.main import main
from novel.orchestrator import beacon
from novel.orchestrator import nodes as N


@pytest.fixture
def base(tmp_path):
    return str(tmp_path)


def _ev(ts, node, phase, exit_code=None, sid='42', note=None):
    return ProgressEvent(ts=ts, sid=sid, node=node, phase=phase,
                         exit_code=exit_code, note=note)


def test_pipeline_steps_match_the_skill_table():
    """进度节点逐项来自 NODES；贵贱仍只有 `is_paid` 一处算。"""
    assert PIPELINE_STEPS == tuple(NODES)
    assert PIPELINE_STEPS[-3:] == (
        'stage7.rename', 'stage7.approve', 'stage7.assemble')
    assert {n for n in PIPELINE_STEPS if N.is_paid(n, ())} == {
        n for n in NODES if N.is_paid(n, ())}


def test_progress_event_payload_matches_phase():
    """一种相位恰好带一种载荷：start 不带退出码，end 必带。"""
    with pytest.raises(Exception):
        _ev(1, 'stage4.plan', 'start', exit_code=0)
    with pytest.raises(Exception):
        _ev(1, 'stage4.plan', 'end')


def test_append_then_read_roundtrip(base):
    beacon.append(base, _ev(1, 'stage0.remove', 'start'))
    beacon.append(base, _ev(2, 'stage0.remove', 'end', exit_code=0, note='剔除过闸'))
    assert beacon.read_events(base) == [
        _ev(1, 'stage0.remove', 'start'),
        _ev(2, 'stage0.remove', 'end', exit_code=0, note='剔除过闸'),
    ]


def test_legacy_progress_without_run_id_is_read_without_backfill(base):
    """旧信标缺 run_id 仍可读为 legacy/ungrouped，且读取不迁移历史 JSONL。"""
    path = beacon.beacon_path(base)
    path.parent.mkdir(parents=True, exist_ok=True)
    payload = _ev(1, 'stage0.remove', 'start').model_dump(
        mode='json', exclude={'run_id'})
    before = (json.dumps(payload, ensure_ascii=False) + '\n').encode('utf-8')
    path.write_bytes(before)

    events = beacon.read_events(base)

    assert len(events) == 1 and events[0].run_id is None
    assert path.read_bytes() == before
    assert 'run_id' not in json.loads(path.read_text('utf-8'))


def test_read_tolerates_a_truncated_last_line(base):
    """被 kill 截在半路的最后一行当没有——与 `jobs.read_events` 同一条容错。"""
    beacon.append(base, _ev(1, 'stage0.remove', 'start'))
    with open(beacon.beacon_path(base), 'ab') as f:
        f.write(b'{"ts": 2, "sid": "4')
    assert beacon.read_events(base) == [_ev(1, 'stage0.remove', 'start')]


def test_a_bad_line_in_the_middle_is_not_silent(base):
    """中间有坏行 = 这份文件被别的东西写过，不许静默吞掉。"""
    p = beacon.beacon_path(base)
    p.parent.mkdir(parents=True, exist_ok=True)
    good = json.dumps(_ev(1, 'stage0.remove', 'start').model_dump(mode='json'),
                      ensure_ascii=False).encode('utf-8')
    p.write_bytes(b'{"ts": 1}\n' + good + b'\n')
    with pytest.raises(Exception):
        beacon.read_events(base)


def test_fold_five_states():
    events = [
        _ev(10, 'stage0.remove', 'start'),
        _ev(20, 'stage0.remove', 'end', exit_code=0),
        _ev(30, 'stage0.approve', 'start'),                       # 还在跑
        _ev(35, 'stage0.edit', 'start'),
        _ev(36, 'stage0.edit', 'end', exit_code=1),               # 落盘但红
        _ev(40, 'stage1.partition', 'start'),
        _ev(50, 'stage1.partition', 'end', exit_code=2),          # 显式终态
    ]
    steps = beacon.fold(events, now=100)
    by_node = {s.node: s for s in steps}
    assert [s.seq for s in steps] == list(range(1, len(NODES) + 1))
    assert by_node['stage0.remove'].beacon == 'ok'
    assert by_node['stage0.remove'].last_exit == 0
    assert by_node['stage0.approve'].beacon == 'running'
    assert by_node['stage0.approve'].elapsed == 70
    assert by_node['stage0.edit'].beacon == 'landed_red'
    assert by_node['stage0.edit'].last_exit == 1
    assert by_node['stage1.partition'].beacon == 'failed'
    assert by_node['stage1.partition'].last_exit == 2
    assert by_node['stage4.plan'].beacon == 'pending'
    assert by_node['stage4.plan'].label == NODES['stage4.plan'].label
    assert all(s.beacon in BEACON_STATES for s in steps)


def test_fold_takes_the_latest_event_per_node():
    """修复回路里同一节点会跑多次——进度只关心最近一次，历史在 events_tail。"""
    events = [
        _ev(10, 'stage7.rename', 'start'),
        _ev(20, 'stage7.rename', 'end', exit_code=2),
        _ev(30, 'stage7.rename', 'start'),
        _ev(40, 'stage7.rename', 'end', exit_code=0),
    ]
    [step] = [s for s in beacon.fold(events, now=50) if s.node == 'stage7.rename']
    assert step.beacon == 'ok' and step.last_ts == 40


def test_mark_command_roundtrip(base):
    assert main(['mark', '42', '--node', 'stage4.plan', '--phase', 'start',
                 '--base', base]) == X.OK
    assert main(['mark', '42', '--node', 'stage4.plan', '--phase', 'end',
                 '--exit', '0', '--note', '计划过闸', '--base', base]) == X.OK
    events = beacon.read_events(base)
    assert [e.phase for e in events] == ['start', 'end']
    assert events[-1].exit_code == 0 and events[-1].note == '计划过闸'


def test_mark_end_requires_exit(base):
    """被拒绝的打点不许落盘。"""
    assert main(['mark', '42', '--node', 'stage4.plan', '--phase', 'end',
                 '--base', base]) == X.TERMINAL_STATE
    assert main(['mark', '42', '--node', 'stage4.plan', '--phase', 'start',
                 '--exit', '0', '--base', base]) == X.TERMINAL_STATE
    assert beacon.read_events(base) == []
