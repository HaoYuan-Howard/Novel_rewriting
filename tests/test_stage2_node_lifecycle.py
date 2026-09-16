"""Stage2 审阅接受与工作包已随语义审阅整体退役（Git 保留历史反例）。

原「两份 typed 接受、审批继承、跨章复核与 rework 派修」的 producer 生命周期
由统一机械准入（``novel/orchestrator/mechanical_admission.py``）替代；审阅接受面的用例按退役处置，
「蓝图册 typed 修订的重放与零模型恢复」现役反例由 ``tests/test_plot_edit.py``
承担（同一 producer、同一故障点、等价断言）。这里保留所有审批写入口的
旧 base 拒绝边界：任何入口都不得在缺失当前 manifest 时建立锁或写副作用。
"""
from __future__ import annotations

import pytest


@pytest.mark.parametrize('legacy', [False, True])
@pytest.mark.parametrize('entry', ['gate', 'backfill', 'raw'])
def test_all_approval_write_entries_require_existing_manifest_before_lock(tmp_path, entry, legacy):
    if legacy:
        old = tmp_path / '书_123' / 'stage2'
        old.mkdir(parents=True)
        (old / 'global_grounding.json').write_text('{}', encoding='utf-8')
    before = sorted(str(path.relative_to(tmp_path)) for path in tmp_path.rglob('*'))
    from novel.orchestrator import approvals
    from novel.work_units import WorkUnitRefused
    calls = {
        'gate': lambda: approvals.approve_gate(tmp_path, '123', 's0'),
        # Stage4 design/structure typed apply 已随人工闸退役；Stage5
        # writing/closure 写面亦已随批次 B3 机械闭合退役。
        'backfill': lambda: approvals.backfill_from_disk(tmp_path, '123'),
        'raw': lambda: approvals._write(tmp_path, None),
    }
    with pytest.raises(WorkUnitRefused, match='已有当前工作流 manifest'):
        calls[entry]()
    assert sorted(str(path.relative_to(tmp_path)) for path in tmp_path.rglob('*')) == before
