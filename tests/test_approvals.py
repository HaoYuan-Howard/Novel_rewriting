#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""审批状态机已随人工闸整层退役；本文件守幸存面的不变式（F8c）。

原文件是旧审批状态机的全量用例：公开 v9 账（`approvals.get`）、写作授权
（`begin_writing`／`authorize_writing`）、gate 复位（`reset`）、旧盘面
backfill、存量 s7 typed 接受证明与 Stage5 legacy 拒读。2026-09-15 全自动
裁决后**新链没有审批面**——上传即授权开跑，`approvals` 模块只剩三件仍然
真实的事（`novel/orchestrator/approvals.py` 模块头）：

  1. **存量账只读解码**：`_read_raw`／`approval_head_bytes` 按原字节解回
     记录形状，版本不符显式拒绝（旧版本与未来版本均不迁移）；
  2. **invocation-scoped 只读投影**：`read_projection`／`projection_code`
     让 operator／inspection 在同一次快照里复用 code 坐标；
  3. **锁设施**：`approval_exclusion_lock`／`approval_file_lock` 串行化
     read-modify-write 与回收站移动（消费者见 work_unit_grants／
     work_unit_recovery／trash，其用例在各自文件）。

写入口、gate 语义、spawns 投影与公开账一律不复活（S-05）；「下一动作」的
权威归 operator／execution permit（`tests/test_operator_next.py`、
`tests/test_execution_permit.py`）。
"""
from __future__ import annotations

import json
from pathlib import Path

import pytest

from contracts.schema.api import StructureChapterDecision
from novel import prov
from novel.errors import ArtifactUnreadable, HumanEditProtected
from novel.orchestrator import approvals
from novel.work_units import initialize_workflow

#: 随人工闸整层退役的审批状态机入口（写面、公开账与旧盘面迁移）。
_RETIRED_STATE_MACHINE_ENTRIES = (
    'get', 'approve_gate', 'begin_writing', 'authorize_writing',
    'writing_is_current', 'reset', 'backfill_from_disk',
    'record_stage5_auto_closure', 'stage5_closure_view',
    'stage4_review_payload_landed', 'reviewer_approval_payload_landed',
    'freeze_design_constraint_history', 'cast_infeasible_replan_scope',
    '_write', '_read_current', '_effective_record', '_proof_for_gate',
    '_proof_for_writing', '_planned', '_spawns', '_has_stage5_work',
    '_require_artifacts', '_require_fresh_json', '_root_clean_sha1',
    '_store_for', '_GATE_ORDER', '_GATE_REQUIREMENTS', '_ApprovalRecordV8',
)


def _overview(sid: str = '123'):
    from contracts.schema.orchestration import (
        NovelOverview, OverviewSummary,
    )
    return NovelOverview(
        sid=sid, title='测试书', dirname=f'平台_测试书_{sid}', stages=[],
        summary=OverviewSummary(
            verdict='pending', text='待继续', detail='下一步待判',
            artifact_count=0, latest_mtime=None))


def _structure_decision(reason: str) -> approvals._StoredStructureDecision:
    return approvals._StoredStructureDecision(
        actor='reviewer', reason=reason, subject_sha256='3' * 64,
        chapters=[StructureChapterDecision(
            output_ordinal=1, decision='accept', reason='本章结构可接受')],
        decided_at='2026-09-01T00:00:00+00:00',
        proof=approvals._ApprovalProof(
            root_clean_sha1='0' * 40,
            artifacts={'stage4/plan_chapters.json': '1' * 64},
            decision_sha256='2' * 64))


def _record_with_history(sid: str = '123'):
    first = _structure_decision('当前结构结论')
    second = _structure_decision('第二次结构结论')
    return approvals._fresh_record(sid).model_copy(update={
        'structure_history': [first, second],
        'structure_decision': second,
    })


def _book(tmp_path, sid: str = '123') -> Path:
    """存量书的目录形状：只读解码要求书在盘上，否则是 UpstreamMissing。"""
    book = tmp_path / f'平台_测试书_{sid}'
    (book / 'stage0').mkdir(parents=True, exist_ok=True)
    return book


def _write_ledger(tmp_path, record) -> Path:
    target = approvals._path(tmp_path, record.sid)
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_bytes(record.model_dump_json().encode('utf-8'))
    return target


def test_approval_state_machine_entries_stay_deleted():
    """审批状态机写面不复活：任何旧入口重新出现都是退役回退（S-05）。"""
    survivors = [name for name in _RETIRED_STATE_MACHINE_ENTRIES
                 if hasattr(approvals, name)]
    assert survivors == [], f'已退役的审批状态机入口仍在模块面上：{survivors}'


def test_read_projection_is_invocation_scoped_and_resets(monkeypatch, tmp_path):
    """⭐ 只读投影是同一次调用的坐标，不是跨请求缓存；退出即复位。"""
    initialize_workflow(tmp_path)
    overview = _overview('123')
    monkeypatch.setattr(prov, 'code_version', lambda: 'fresh-fingerprint')
    assert approvals.projection_code() == 'fresh-fingerprint', (
        '投影外的普通调用仍现场取指纹')
    with approvals.read_projection(overview=overview, code='frozen-code'):
        assert approvals.projection_code() == 'frozen-code', (
            '同一次 operator 快照内的 code 坐标必须复用，不重跑 git 指纹')
        assert approvals._READ_OVERVIEW.get() is overview
    assert approvals.projection_code() == 'fresh-fingerprint', (
        '投影退出后必须复位，不能把坐标泄漏给下一次调用')
    assert approvals._READ_OVERVIEW.get() is None


def test_read_projection_refuses_a_foreign_book_snapshot():
    """别书的快照不能共享：投影坐标必须属于同一本书。

    `read_projection` 自身以 overview.sid 为坐标源；这条守卫直接钉住
    `_validate_read_projection` 的三个拒绝面（成对、归属、继承冲突），
    供任何未来调用点在复活写面前先撞上同一道闸。
    """
    with pytest.raises(ValueError, match='不是'):
        approvals._validate_read_projection(
            '123', overview=_overview('999'), code='c')
    with pytest.raises(ValueError, match='成对提供'):
        approvals._validate_read_projection('123', overview=None, code='c')
    with approvals.read_projection(overview=_overview('123'), code='c'):
        with pytest.raises(ValueError, match='属于另一书'):
            approvals._validate_read_projection('999', overview=None, code=None)


def test_approval_head_bytes_serializes_only_the_current_head(tmp_path):
    """历史增长不进 surface seal：head bytes 只序列化 current 决定。"""
    initialize_workflow(tmp_path)
    _book(tmp_path)
    target = _write_ledger(tmp_path, _record_with_history('123'))

    head = approvals.approval_head_bytes(tmp_path, '123')
    assert head is not None
    decoded = json.loads(head.decode('utf-8'))
    assert decoded['sid'] == '123' and decoded['v'] == 9
    assert 'structure_history' not in decoded and 'closure_history' not in decoded
    assert decoded['structure_decision']['reason'] == '第二次结构结论'
    # 同一份字节重复读取得到同一序列化（canonical，sort_keys + 紧凑分隔）。
    assert approvals.approval_head_bytes(tmp_path, '123') == head

    # 当前决定相同、历史长度不同的两份账 ⇒ head 字节相同：seal 不随历史增长漂移。
    current = _structure_decision('唯一当前结论')
    short = approvals._fresh_record('123').model_copy(update={
        'structure_history': [current], 'structure_decision': current})
    long = approvals._fresh_record('123').model_copy(update={
        'structure_history': [_structure_decision('第一次结论'), current],
        'structure_decision': current})
    target.write_bytes(short.model_dump_json().encode('utf-8'))
    short_head = approvals.approval_head_bytes(tmp_path, '123')
    target.write_bytes(long.model_dump_json().encode('utf-8'))
    long_head = approvals.approval_head_bytes(tmp_path, '123')
    assert short_head == long_head, (
        '历史追加改变了 current head 序列化——seal 会随历史漂移')

    # current 决定真的变化时 head 必须随之变化。
    changed_decision = _structure_decision('改过的当前结论')
    changed = long.model_copy(update={
        'structure_history': [*long.structure_history, changed_decision],
        'structure_decision': changed_decision})
    target.write_bytes(changed.model_dump_json().encode('utf-8'))
    assert approvals.approval_head_bytes(tmp_path, '123') != long_head


def test_approval_head_bytes_refuses_foreign_sid_and_returns_none_when_absent(
        tmp_path):
    initialize_workflow(tmp_path)
    _book(tmp_path)
    assert approvals.approval_head_bytes(tmp_path, '123') is None, (
        '盘上没有账是缺失，不是空账序列化')
    # 账面 sid 与请求 sid 不符：按原字节解不出「这本书的 head」。
    foreign = approvals._path(tmp_path, '123')
    foreign.parent.mkdir(parents=True, exist_ok=True)
    foreign.write_bytes(
        _record_with_history('999').model_dump_json().encode('utf-8'))
    with pytest.raises(ArtifactUnreadable, match='属于'):
        approvals.approval_head_bytes(tmp_path, '123')


def test_read_raw_decodes_current_v9_and_refuses_sid_drift(tmp_path):
    """存量账按原字节解回 v9；归属不符或损坏一律显式拒绝，不迁移。"""
    initialize_workflow(tmp_path)
    _book(tmp_path)
    target = _write_ledger(tmp_path, _record_with_history('123'))
    decoded = approvals._read_raw(tmp_path, '123')
    assert decoded.sid == '123' and decoded.structure_decision is not None
    assert len(decoded.structure_history) == 2

    target.write_bytes(
        _record_with_history('999').model_dump_json().encode('utf-8'))
    with pytest.raises(HumanEditProtected, match='belongs to'):
        approvals._read_raw(tmp_path, '123')

    target.write_bytes(b'{"v": 9, "sid": ')
    with pytest.raises(HumanEditProtected, match='损坏'):
        approvals._read_raw(tmp_path, '123')


def test_has_record_is_the_only_authorization_free_reading(tmp_path):
    """`has_record` 只回答「盘上有没有这份历史账」，不推断授权状态。"""
    initialize_workflow(tmp_path)
    _book(tmp_path)
    assert approvals.has_record(tmp_path, '123') is False
    _write_ledger(tmp_path, approvals._fresh_record('123'))
    assert approvals.has_record(tmp_path, '123') is True
    assert not any(approvals._read_raw(tmp_path, '123').gate.values()), (
        '有账文件不等于有授权：读到的仍是全关闭的原字节')
