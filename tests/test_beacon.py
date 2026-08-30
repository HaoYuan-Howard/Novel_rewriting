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

import hashlib
import json

import pytest

from contracts.invariants import BEACON_STATES, NODES, PIPELINE_STEPS
from contracts.schema.orchestration import ProgressEvent
from novel.cli import exit_codes as X
from novel.cli.main import main
from novel.errors import TerminalState
from novel.orchestrator import admission, beacon
from novel.orchestrator import nodes as N


@pytest.fixture
def base(tmp_path):
    return str(tmp_path)


def _ev(ts, node, phase, exit_code=None, sid='42', note=None, *,
        run_id=None, execution_id=None):
    return ProgressEvent(ts=ts, sid=sid, node=node, phase=phase,
                         exit_code=exit_code, note=note, run_id=run_id,
                         execution_id=execution_id)


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


def test_complete_json_without_lf_is_not_a_committed_beacon(base):
    """对象字节完整也不算提交；LF 才是 append-only commit marker。"""
    path = beacon.beacon_path(base)
    path.parent.mkdir(parents=True, exist_ok=True)
    event = _ev(
        1, 'stage2.global', 'start', run_id='parent-1',
        execution_id='exec-uncommitted')
    path.write_bytes(json.dumps(
        event.model_dump(mode='json'), ensure_ascii=False).encode('utf-8'))

    assert beacon.read_events(base) == []
    assert beacon.open_executions(base, 'parent-1') == ()


def test_lf_committed_bad_last_line_is_not_silently_tolerated(base):
    path = beacon.beacon_path(base)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(b'{"not":"a ProgressEvent"}\n')
    with pytest.raises(Exception):
        beacon.read_events(base)


def test_truncated_end_never_closes_the_committed_start(base):
    start = _ev(
        1, 'stage2.global', 'start', run_id='parent-1',
        execution_id='exec-open')
    end = _ev(
        2, 'stage2.global', 'end', exit_code=0, run_id='parent-1',
        execution_id='exec-open')
    beacon.append(base, start)
    path = beacon.beacon_path(base)
    with open(path, 'ab') as handle:
        # 完整 JSON 但没有 LF，仍然是未提交 end。
        handle.write(json.dumps(
            end.model_dump(mode='json'), ensure_ascii=False).encode('utf-8'))

    assert beacon.read_events(base) == [start]
    assert beacon.open_executions(base, 'parent-1') == (start,)


def test_append_refuses_to_join_a_non_lf_tail(base):
    path = beacon.beacon_path(base)
    path.parent.mkdir(parents=True, exist_ok=True)
    original = b'{"truncated":'
    path.write_bytes(original)

    with pytest.raises(ValueError, match='未以 LF 提交'):
        beacon.append(base, _ev(2, 'stage2.global', 'start'))
    assert path.read_bytes() == original


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


def test_open_executions_pairs_exact_attempts_within_one_parent_run(base):
    events = [
        _ev(1, 'stage2.global', 'start', run_id='parent-1', execution_id='exec-a'),
        _ev(2, 'stage2.global', 'start', run_id='parent-1', execution_id='exec-b'),
        _ev(3, 'stage2.global', 'end', exit_code=0,
            run_id='parent-1', execution_id='exec-a'),
        _ev(4, 'stage2.global', 'start', run_id='other-run', execution_id='exec-c'),
    ]
    for event in events:
        beacon.append(base, event)

    assert beacon.open_executions(base, 'parent-1') == (events[1],)


def test_open_executions_refuses_unpaired_or_unidentified_current_events(base):
    beacon.append(base, _ev(
        1, 'stage2.global', 'start', run_id='parent-1'))
    with pytest.raises(ValueError, match='缺 execution_id'):
        beacon.open_executions(base, 'parent-1')


def test_typed_tail_repair_quarantines_then_replays_open_execution(base):
    start = _ev(
        1, 'stage2.global', 'start', run_id='parent-1',
        execution_id='exec-repair')
    end = _ev(
        2, 'stage2.global', 'end', exit_code=0, run_id='parent-1',
        execution_id='exec-repair')
    beacon.append(base, start)
    path = beacon.beacon_path(base)
    committed = path.read_bytes()
    tail = json.dumps(
        end.model_dump(mode='json'), ensure_ascii=False).encode('utf-8')
    with open(path, 'ab') as handle:
        handle.write(tail)

    subject = beacon.tail_repair_subject(base)
    assert subject.committed_size == len(committed)
    assert subject.committed_sha256 == hashlib.sha256(committed).hexdigest()
    assert subject.tail_size == len(tail)
    assert subject.tail_sha256 == hashlib.sha256(tail).hexdigest()
    done = beacon.repair_tail(
        base, actor='恢复值守员', reason='隔离 kill 留下的未提交 end',
        subject_sha256=subject.subject_sha256)

    assert done == subject
    assert path.read_bytes() == committed
    assert subject.quarantine_path.read_bytes() == tail
    records = beacon.read_tail_repair_records(base)
    assert [record.kind for record in records] == ['authorized', 'applied']
    assert all(record.actor == '恢复值守员' for record in records)
    assert beacon.open_executions(base, 'parent-1') == (start,)

    # 恢复不猜 end；由真实写手重新追加完整事件后才能闭合。
    beacon.append(base, end)
    assert beacon.open_executions(base, 'parent-1') == ()


def test_tail_repair_subject_cas_drift_does_not_mutate_beacon(base):
    path = beacon.beacon_path(base)
    path.parent.mkdir(parents=True, exist_ok=True)
    original = b'{"truncated":'
    path.write_bytes(original)
    stale = beacon.tail_repair_subject(base)
    changed = original + b'x'
    path.write_bytes(changed)

    with pytest.raises(TerminalState, match='subject SHA 已变化'):
        beacon.repair_tail(
            base, actor='恢复值守员', reason='旧主体不得截新尾',
            subject_sha256=stale.subject_sha256)
    assert path.read_bytes() == changed
    assert not stale.quarantine_path.exists()


def test_mark_drained_and_reconcile_refuse_non_lf_beacon_under_joint_lock(base):
    admission.open_gate(base, 'parent-tail-gate', '42')
    admission.close_gate(base, 'parent-tail-gate')
    subject = admission.reconcile_subject(base, 'parent-tail-gate')
    path = beacon.beacon_path(base)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(b'{"possible_start":')

    with pytest.raises(ValueError, match='未以 LF 提交'):
        admission.mark_drained(base, 'parent-tail-gate')
    with pytest.raises(ValueError, match='未以 LF 提交'):
        admission.reconcile_subject(base, 'parent-tail-gate')
    with pytest.raises(ValueError, match='未以 LF 提交'):
        admission.reconcile_gate(
            base, 'parent-tail-gate', actor='恢复值守员',
            reason='non-LF beacon 不能参与收口', subject_sha256=subject)
    current = admission.snapshot(base, 'parent-tail-gate')
    assert current.terminal_kind is None


def test_mark_command_roundtrip(base):
    assert main(['mark', '42', '--node', 'stage4.plan', '--phase', 'start',
                 '--execution-id', 'attempt-1', '--base', base]) == X.OK
    assert main(['mark', '42', '--node', 'stage4.plan', '--phase', 'end',
                 '--execution-id', 'attempt-1', '--exit', '0',
                 '--note', '计划过闸', '--base', base]) == X.OK
    events = beacon.read_events(base)
    assert [e.phase for e in events] == ['start', 'end']
    assert events[-1].exit_code == 0 and events[-1].note == '计划过闸'
    assert {event.execution_id for event in events} == {'attempt-1'}


def test_mark_end_requires_exit(base):
    """被拒绝的打点不许落盘。"""
    assert main(['mark', '42', '--node', 'stage4.plan', '--phase', 'end',
                 '--base', base]) == X.TERMINAL_STATE
    assert main(['mark', '42', '--node', 'stage4.plan', '--phase', 'start',
                 '--exit', '0', '--base', base]) == X.TERMINAL_STATE
    assert beacon.read_events(base) == []


def test_reconcile_cli_exposes_zero_cost_typed_beacon_tail_repair(
        base, capsys):
    path = beacon.beacon_path(base)
    path.parent.mkdir(parents=True, exist_ok=True)
    committed = (
        json.dumps(_ev(
            1, 'stage4.plan', 'start', run_id='parent-1',
            execution_id='attempt-1').model_dump(mode='json'),
            ensure_ascii=False) + '\n').encode('utf-8')
    tail = b'{"truncated":'
    path.write_bytes(committed + tail)

    assert main([
        'reconcile', '--base', base, '--beacon-repair-tail']) == X.OK
    shown = json.loads(capsys.readouterr().out)
    assert shown['committed_size'] == len(committed)
    assert shown['tail_size'] == len(tail)
    assert shown['apply_requires'] == [
        '--actor', '--reason', '--subject-sha256']

    before_apply = path.read_bytes()
    assert main([
        'reconcile', '--base', base, '--beacon-repair-tail',
        '--actor', '恢复值守员']) == X.TERMINAL_STATE
    assert main([
        'reconcile', '--base', base, '--beacon-repair-tail',
        '--actor', '恢复值守员', '--reason', '不得强制',
        '--subject-sha256', shown['subject_sha256'],
        '--force']) == X.TERMINAL_STATE
    assert path.read_bytes() == before_apply

    assert main([
        'reconcile', '--base', base, '--beacon-repair-tail',
        '--actor', '恢复值守员', '--reason', '隔离 non-LF beacon 尾',
        '--subject-sha256', shown['subject_sha256']]) == X.OK
    assert path.read_bytes() == committed
    assert beacon.tail_repair_dir(base).joinpath(
        f"{shown['subject_sha256']}.tail.bin").read_bytes() == tail
    beacon.append(base, _ev(
        2, 'stage4.plan', 'end', exit_code=0, run_id='parent-1',
        execution_id='attempt-1'))
    assert beacon.open_executions(base, 'parent-1') == ()


def test_commander_control_policy_allows_beacon_tail_inspect_but_not_apply(
        base, monkeypatch, capsys):
    import novel.cli.main as cli

    path = beacon.beacon_path(base)
    path.parent.mkdir(parents=True, exist_ok=True)
    damaged = b'{"truncated":'
    path.write_bytes(damaged)
    monkeypatch.setenv(cli.commander_context.COMMANDER_RUN_ID_ENV, 'parent-1')

    assert cli.main([
        'reconcile', '--base', base, '--beacon-repair-tail']) == X.OK
    shown = json.loads(capsys.readouterr().out)
    assert cli.main([
        'reconcile', '--base', base, '--beacon-repair-tail',
        '--actor', '恢复值守员', '--reason', 'Commander 内不得 apply',
        '--subject-sha256', shown['subject_sha256']]) == X.TERMINAL_STATE
    assert path.read_bytes() == damaged
