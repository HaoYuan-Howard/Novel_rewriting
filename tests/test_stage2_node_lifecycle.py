"""Stage2 审阅接受与工作包已随语义审阅整体退役（Git 保留历史反例）。

原「两份 typed 接受、审批继承、跨章复核与 rework 派修」的 producer 生命周期
先由统一机械准入接管，2026-10-04 清理批再随人工闸整层退役一并删除
（``novel/orchestrator/mechanical_admission.py`` 亦已删除）；蓝图册 typed 修订
的历史反例见 Git 历史（``tests/test_plot_edit.py`` 已随旧链 plot schema 删除）。

F8c（人工闸整层退役）后本文件只剩一格：**审批模块不复活守卫**——
``novel/orchestrator/approvals.py`` 写面与只读解码面均已整层删除，模块重新
出现即是退役回退（S-05）；旧 base「持审批文件锁前须有当前工作流 manifest」
的边界随模块缺席结构性闭合。
"""
from __future__ import annotations


def test_approval_module_stays_deleted_after_retirement():
    """审批模块整层删除（2026-10-04 裁决）：写入口与只读解码都不复活。"""
    import importlib.util
    assert importlib.util.find_spec('novel.orchestrator.approvals') is None
