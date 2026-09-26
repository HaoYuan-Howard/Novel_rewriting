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
    """审批写入口整层退役：任何旧写面重新出现都是回退，不是「恢复兼容」。"""
    from novel.orchestrator import approvals

    survivors = [name for name in _RETIRED_WRITE_ENTRIES
                 if hasattr(approvals, name)]
    assert survivors == [], f'已退役的审批写入口仍在模块面上：{survivors}'
    # 只读解码与锁设施是 F8c 后明确保留的两件事，反向钉住模块没有整个消失。
    for name in ('read_projection', 'projection_code', 'approval_head_bytes',
                 'approval_exclusion_lock', 'approval_file_lock',
                 'frozen_for_trash', 'has_record', '_read_raw', '_path'):
        assert hasattr(approvals, name), f'幸存设施被误删：{name}'


@pytest.mark.parametrize('legacy', [False, True])
def test_approval_lock_still_requires_existing_manifest_before_lock(
        tmp_path, legacy):
    """幸存的文件锁入口仍先要求已有当前 manifest，再谈互斥副作用。

    原用例以三个写入口（gate／backfill／raw）各问一遍；写入口退役后同一
    边界只剩锁设施这一个真实占用点，旧 base（无 manifest）必须连锁文件
    都不建立。
    """
    if legacy:
        old = tmp_path / '书_123' / 'stage2'
        old.mkdir(parents=True)
        (old / 'global_grounding.json').write_text('{}', encoding='utf-8')
    before = sorted(str(path.relative_to(tmp_path)) for path in tmp_path.rglob('*'))
    from novel.orchestrator import approvals
    from novel.work_units import WorkUnitRefused

    with pytest.raises(WorkUnitRefused, match='已有当前工作流 manifest'):
        with approvals.approval_file_lock(tmp_path, '123'):
            pytest.fail('旧 base 不应进入审批文件锁临界区')
    assert sorted(str(path.relative_to(tmp_path)) for path in tmp_path.rglob('*')) == before
