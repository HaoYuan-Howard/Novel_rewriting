#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
context.py —— `writer_context.json` 的**确定性组装**。

【契约】本模块按显式引用组装写手任务，与 Stage5 从正文计算的机械基线分工。
事实、舞台和材料由 Blueprint 提供；人物只沿本章事实、舞台、叙述或目标引用下发，
叫法仍受源文时点约束。必要知识从独立 KnowledgeLinks 的 typed 决定编译到当前场景。
世界内成立、人物知情与读者披露分别计时；埋点只下发表现，不附秘密答案。

【不能退回的旧做法】逐章比较首次揭示章不足以支持场景重排；setting 的字面子串
过滤曾漏过改写式剧透，不能当作语义通过。本层只核验明确关联及引用，不替 Commander
判断证据是否支持知识。缺失、过期或未决关联由入口拒绝，不能猜一个揭示场景。

【本模块吃 `dict` 不吃模型】理由见 `novel/stages/stage4/__init__.py` ③。
【本模块不 `print`、不 `sys.exit`】可观察信息进入返回账，由节点写入校验报告；
显式终态一律抛 `RefusedByStructure`。
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any


@dataclass
class Assembly:
    """组装的**副产物**：那些「必须让人看见、但不该静默改变产物」的账。

    例如「这一章剔了 3 个在场者」与「这一章本来就没人」不能在落盘后同形，
    因此全部收进本结构，由节点交给校验报告。
    """

    ctx: dict[str, Any] = field(default_factory=dict)
    #: `canonical` 是自造标签、已被换成有据叫法的那些（`{原标签: 换成的}`）
    subs: dict[str, str] = field(default_factory=dict)
    #: 书级表上就撞名的（`[(名字, [char_id…], [canonical…])]`）
    collisions: list[tuple[str, list[str], list[Any]]] = field(default_factory=list)
    #: **章级表新增**的撞名（书级表上不存在）
    chapter_collisions: list[tuple[Any, str, list[str]]] = field(default_factory=list)
    #: 本章还叫不出名字、被剔出在场者的（`[(输出章, char_id)]`）
    withheld: list[tuple[Any, str]] = field(default_factory=list)
    #: 规则暂不下发（`{(输出章, rule_id, 命中的叫法)}`）
    held_rules: set[tuple[Any, Any, tuple[str, ...]]] = field(default_factory=set)


# 公开身份解析在 actors.py，任务书编译在 plot_context.py；不保留旧 address 或
# 按 char_id 判等删除 narration 的第二条兼容路径。
from novel.stages.stage4.plot_context import build_writer_context
