"""只读确认 Reviewer 的精确节点批准已由正式 producer 落盘（已退役模式留档）。

此口只补 canonical 批准落盘到工作单元 mark_adopted 之间的尾窗。它不授予审批、
不把当前 gate 当作原动作证明，也不重建旧工作包。原 token 对照批准时保存的
workpack SHA，当前适用性仍由 producer 的主体与 stable review identity 判据复验。

⚠ Stage6 三候选定稿（`stage6.approve`／`s6r4_` token／slot 槽位）随 Stage6
整层退役（F8b）：新链 Hook 单输出、无候选槽位，本模块不再解码该节点的历史
认领；Stage4 四道人工闸随 2026-09-15 全自动裁决退役。

⚠ F8c：stage7 人名表定稿的落账解码段（旧 Stage7 整包内 `rename` 模块的
NameMap 逐字段比对）随旧 Stage7 整包删除。曾可认领的两个批准节点
（`stage0.approve`／`stage7.approve`）均已不在 `NODES`，本模块作为已退役模式的
历史反例保留（测试桶登记），只剩「退役节点 fail closed」与「已落查询零写盘」
两条可达语义——真实 landed 精度反例随旧 producer 一并退役，登记见
`contracts/schema/retirement.py`（F6d reviewer_node_landed rewrite_consumer
＋ `@"s6r4_"` remove_entry）。
"""
from __future__ import annotations

from pathlib import Path


def approval_action_landed(
    base: str | Path, sid: str, *, node: str, flags: tuple[str, ...], code: str,
) -> bool:
    """原动作与正式批准逐字段一致才返回 True；未知/损坏不转成已落盘。

    ⚠ 认领面已随旧链清空：两个曾可认领的批准节点（`stage0.approve`／
    `stage7.approve`）退役后不在 `NODES`，一律 **fail closed** 返回 False，由原
    恢复流程继续——既不让 `readonly_ctx` 的 RunContext 校验把一次只读探测炸成
    ValueError，也不把已退役节点的历史决定认领成现役工作单元的落盘证据。本
    函数零写盘：不读审批、不落产物、不动工作单元与费用账。
    """
    return False
