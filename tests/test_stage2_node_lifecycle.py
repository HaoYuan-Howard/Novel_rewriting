"""Stage2 审阅接受与工作包已随语义审阅整体退役（Git 保留历史反例）。

原「两份 typed 接受、审批继承、跨章复核与 rework 派修」的 producer 生命周期
由统一机械准入（``novel/orchestrator/mechanical_admission.py``）替代；审阅接受面的用例按退役处置，
「蓝图册 typed 修订的重放与零模型恢复」现役反例由 ``tests/test_plot_edit.py``
承担（同一 producer、同一故障点、等价断言）。

F8c（人工闸整层退役）后本文件只剩两格：
  1. **写入口不复活守卫**：``approve_gate``／``backfill_from_disk``／``_write``
     一族审批写面已整层删除，任何一项重新出现都是退役回退（S-05）。
  2. **幸存写相关边界的旧 base 拒绝**：唯一还占审批文件锁的入口
     （``approval_file_lock``）仍要求在持锁前已有当前工作流 manifest——
     错误请求不得创建任务、建锁或重发预算。
"""
from __future__ import annotations

import pytest

#: 随人工闸整层退役的审批写入口（``novel/orchestrator/approvals.py`` 已删除）。
#: 名单与 ``contracts/schema/retirement.py`` 的 F8c 收口说明一一对应。
_RETIRED_WRITE_ENTRIES = (
    'get', 'approve_gate', 'backfill_from_disk', 'reset',
    'authorize_writing', 'begin_writing', 'writing_is_current',
    'record_stage5_auto_closure', 'stage5_closure_view',
    'stage4_review_payload_landed', 'reviewer_approval_payload_landed',
    'freeze_design_constraint_history', 'cast_infeasible_replan_scope',
    '_write', '_read_current', '_effective_record', '_proof_for_gate',
    '_proof_for_writing', '_planned', '_spawns', '_has_stage5_work',
    '_require_artifacts', '_require_fresh_json', '_root_clean_sha1',
    '_store_for', '_GATE_ORDER', '_GATE_REQUIREMENTS', '_ApprovalRecordV8',
)


def test_approval_write_entries_stay_deleted_after_retirement():
    """审批模块整层删除（2026-10-04 裁决）：写入口与只读解码都不复活。"""
    import importlib.util
    assert importlib.util.find_spec('novel.orchestrator.approvals') is None


@pytest.mark.parametrize('legacy', [False, True])
def test_approval_lock_still_requires_existing_manifest_before_lock(
        tmp_path, legacy):
    """（历史边界随模块删除）审批文件锁入口已不存在；旧 base 的连锁文件
    都不建立这一约束由「模块缺席」结构性闭合。"""
    import importlib.util
    assert importlib.util.find_spec('novel.orchestrator.approvals') is None
