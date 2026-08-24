#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""把 Stage1 两块文本测成 Stage3 v2 的 ``measurable``。

【为什么是两块而不是一块】`R-4.2`：**正文与开篇文案段分开测、互不污染**。
文案段的情绪浓度通常远高于正文，拿它的调子去描述正文就错了。
那一条在需求表里标的是 `结构`——`measure()` 对正文与文案段**各调一次、不共享任何中间量**，
`measurable` 分 `body` / `hook` 两块**同形分开落盘**。要违反它必须先改代码。
"""
from __future__ import annotations

from contracts.invariants import STAGE3_MEASUREMENT_CONTRACT
from contracts.schema.stage1 import HookCopy, MainText
from contracts.schema.stage3 import Measurable
from novel.core.measure import measure, measure_body
from novel.errors import ArtifactUnreadable

# 对外保留一个短名，生产与测试都不得手写版本字面。
MEASUREMENT_CONTRACT = STAGE3_MEASUREMENT_CONTRACT


def measure_all(main_text: MainText, hook: HookCopy) -> Measurable:
    """两份上游 → 本层的 `measurable{}`。**确定性，0 成本，可重复。**

    ⚠ `hook` 是**必填**的：`R-1.12` 已把「没有开篇文案段」的书挡在 stage1，
    走不到这一层；v2 的 ``measurable.hook`` 因而保持必填。
    ⇒ **别在本层留兜底分支**——留了就是一条永不执行、也永不被验证的路（`R-4.6`）。
    """
    hook_block = measure(hook_text_of(hook))
    if hook_block is None:
        raise ArtifactUnreadable(
            'stage1/hook_copy.json 的文案段是空的，量不出形态。'
            '⇒ `R-1.12` 本应把无文案段的书挡在 stage1，走到这里说明那道闸没生效')
    return Measurable(body=measure_body(main_text), hook=hook_block)


def hook_text_of(hook: HookCopy) -> str:
    """开篇文案段的正文。**唯一取法**——两处各写一遍 `.text.strip()` 就会有两个答案。"""
    return (hook.text or '').strip()
