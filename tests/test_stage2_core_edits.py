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
联合首抽（stage2.analyze）的历史 agent 半边提交职责已随生产臂退役；
这里只保留人物修复后从直连回答侧车免费物化 Global 的现役路由。
"""
from __future__ import annotations

from novel.orchestrator import queue
from tests.test_stage2_analyze import _awaiting_global, _ctx


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
