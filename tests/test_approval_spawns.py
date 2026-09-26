#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""审批派生子进程面已随人工闸整层退役（F8c）。

原文件测 `approvals.get` 的 `spawns` 投影：gate 通过后按
`state.resume_verdict` 过滤出下一批可派生子进程（FC-05「fresh 下游产物不得
被提议付费覆盖」）、Stage4 typed infeasible 终态的预算内自动重采，以及
「fresh 空账不得被读成某人已存的决定」。人工闸退役后：

  * 整个 spawn 投影面（`approvals.get`／`_planned`／`_spawns` 与
    `state.resume_verdict` 的消费点）随审批写入口一并删除；「下一动作」的
    唯一权威是 operator（`novel/orchestrator/operator.py`）与 execution
    permit，其恢复判定反例由 ``tests/test_operator_next.py`` 与
    ``tests/test_execution_permit.py`` 承接；
  * 仍然真实的是**存量账的诚实读数**：`_fresh_record` 的空账形状与
    `has_record` 的「盘上有没有这份历史账」语义——它不推断任何当前授权。

本文件改写为退役守卫 + 幸存读数的不变式。
"""
from __future__ import annotations

import pytest

from novel.orchestrator import approvals

#: 随人工闸退役的派生子进程面（`approvals.get` 的 spawns 投影与其依赖）。
_RETIRED_SPAWN_ENTRIES = (
    'get', '_planned', '_spawns', 'current_infeasible_binding',
    'approve_gate', 'backfill_from_disk', 'reset', 'authorize_writing',
)


def test_spawn_projection_surface_stays_deleted():
    """派生子进程面不复活：任何旧入口重新出现都是退役回退（S-05）。"""
    survivors = [name for name in _RETIRED_SPAWN_ENTRIES
                 if hasattr(approvals, name)]
    assert survivors == [], f'已退役的派生子进程入口仍在模块面上：{survivors}'
    # approvals 退役后不得再反向 import 恢复判定/机械准入这些编排面。
    assert not hasattr(approvals, 'state'), 'approvals 不得再持有 state 坐标'
    assert not hasattr(approvals, 'mechanical_admission'), (
        'approvals 不得再持有 mechanical_admission 坐标')


def test_fresh_record_is_all_closed_and_never_a_saved_human_choice(tmp_path):
    """⭐ 空账≠已存决定：新链不写这份账，空账只诚实回答「从没有过票」。"""
    record = approvals._fresh_record('123')
    assert record.v == 9
    assert not any(record.gate.values()), '空账必须全 gate 关闭'
    assert all(proof is None for proof in record.gate_proofs.values())
    assert not record.writing and record.writing_proof is None
    assert not approvals.has_record(tmp_path, '123'), (
        '盘上没有审批文件时不得推断出任何授权状态')


def test_ledger_file_presence_is_the_only_honest_reading(tmp_path):
    """存量书的账文件在盘上这一事实可读；它本身不构成任何授权结论。"""
    book = tmp_path / '书名_123'
    (book / 'stage0').mkdir(parents=True)
    target = approvals._path(tmp_path, '123')
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_bytes(
        approvals._fresh_record('123').model_dump_json().encode('utf-8'))
    assert approvals.has_record(tmp_path, '123')
    record = approvals._read_raw(tmp_path, '123')
    assert record.sid == '123'
    assert not any(record.gate.values()), '读到的是原字节，不是推断的授权'
    assert approvals.approval_head_bytes(tmp_path, '123') is not None


def test_frozen_for_trash_serializes_against_repeated_freeze(tmp_path):
    """锁设施幸存：同一本书的删除冻结必须显式 409，不静默重叠。"""
    from novel.errors import HumanEditProtected

    with approvals.frozen_for_trash('123'):
        with pytest.raises(HumanEditProtected, match='已经有一个删除在进行中'):
            with approvals.frozen_for_trash('123'):
                pytest.fail('同一本书的冻结不得静默重叠')
    # 冻结释放后另一本书不受影响（冻结只按 sid，不持全局锁）。
    with approvals.frozen_for_trash('124'):
        pass
