"""统一 Commander 双 owner 编辑批次已整体退役（见 docs/schema/stage2.md）。

本文件原验证「Commander 原决定先落盘、独立 owner 免费提交及恢复」的
``novel.stages.stage2.core_edits`` 机制；该模块连同其 queue/inspection 消费点
已随 Stage2 语义审阅退役删除，全部成功路径用例按退役处置（Git 保留历史）。
现役等价能力由独立入口各自的测试承担：
- 人物独立 typed 编辑：``tests/test_stage2_secret_edit.py``、``tests/test_stage2_characters*.py``；
- Global 独立 typed 编辑（含集合 CAS、候选预检、WAL 恢复）：
  ``tests/test_stage2_typed_edit.py``、``tests/test_stage2_secret_edit.py``、
  ``tests/test_stage2_global_core_edits.py``；
- 「同一决定新增人物并被关系引用」的组合由两次独立编辑表达，不再有跨 owner 批次。

这里只保留两例与双 owner 无关、但原本只在本文件覆盖的现役职责：
联合首抽（stage2.analyze）的队列冷启动与半边提交恢复仍必须经真实
queue/operator/execution_permit 面零模型闭合。
"""
from __future__ import annotations

import pytest

from novel.node_agents import Stage2AnalyzeAgent
from novel.orchestrator import execution_permit, operator, queue
from novel.store import ArtifactStore
from tests.test_stage2_analyze import _awaiting_global, _ctx, _driver


def test_queue_cold_start_uses_initial_recovery_and_partial_submit_keeps_exact_run(tmp_path, monkeypatch):
    """analyze 冷启动中断后，队列/operator/许可只指向免费 --recover-analyze-run。"""
    from novel.stages.stage2 import analyze, analyze_recovery
    from tests.test_stage2_global_agent import _free_ctx
    ctx = _ctx(tmp_path)
    todo, blocked = queue.plan_book(tmp_path, ctx.sid, until_stage='stage2')
    nodes = [row.node for row in [*todo, *blocked]]
    assert 'stage2.analyze' in nodes
    assert 'stage2.characters' not in nodes and 'stage2.global' not in nodes
    original_write = ArtifactStore.write_round
    def stop_global(store, rel, value):
        if rel == analyze.GLOBAL:
            raise KeyboardInterrupt('首次联合提交人物后硬杀')
        return original_write(store, rel, value)
    monkeypatch.setattr(ArtifactStore, 'write_round', stop_global)
    with pytest.raises(KeyboardInterrupt):
        Stage2AnalyzeAgent(ctx, driver=_driver(ctx)).run()
    ledger = ctx.ledger.path.read_bytes()
    recovery = queue.stage2_core_recovery_item(tmp_path, ctx.sid)
    assert recovery.node == analyze.NODE
    assert recovery.flags == ['--recover-analyze-run', ctx.run_id]
    current = operator.next_action(tmp_path, ctx.sid)
    assert current.state == 'ready' and current.action.node == analyze.NODE
    assert current.action.flags == recovery.flags and current.action.paid is False
    permit = operator.permit(tmp_path, ctx.sid, analyze.NODE, recovery.flags)
    assert permit.paid is False
    execution_permit.revalidate_active_execution_surface(permit, flags=recovery.flags)
    monkeypatch.setattr(ArtifactStore, 'write_round', original_write)
    free = _free_ctx(ctx)
    analyze_recovery.recover_initial(free, ctx.run_id)
    assert queue.stage2_core_recovery_item(tmp_path, ctx.sid) is None
    assert ctx.ledger.path.read_bytes() == ledger
    assert analyze.cold_start_reason(ctx.novel_dir) is not None


def test_fixed_characters_route_saved_global_draft_to_free_recovery(tmp_path):
    """人物侧已修复时，队列把 saved Global 草稿指向免费 --recover-analyze。"""
    from novel.stages.stage2 import analyze_recovery
    ctx = _ctx(tmp_path)
    free = _awaiting_global(ctx)
    ledger = ctx.ledger.path.read_bytes()
    recovery = queue.stage2_core_recovery_item(tmp_path, ctx.sid)
    assert recovery.node == 'stage2.global' and recovery.flags == ['--recover-analyze']
    todo, blocked = queue.plan_book(tmp_path, ctx.sid, until_stage='stage2')
    assert [row.flags for row in todo] == [['--recover-analyze']] and blocked == []
    analyze_recovery.recover(free)
    assert queue.stage2_core_recovery_item(tmp_path, ctx.sid) is None
    assert ctx.ledger.path.read_bytes() == ledger
