#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
accounting.py —— 记账键的出口。**一张会炸的表。**

【它存在的那一句话】`Q-1`：**记账不是终点，报出来才是。**
原型 13 个记账键里 6 个 0 出口——stage2 的 8 个有 5 个没有出口、
stage3 的 3 个（`attempts` / `revalidated` / `gates_skipped`）**一个都没有**，
而它们记的恰好是最该被人看见的三件事（这份稿第几次才产对的 / 是不是被重判提升上来的 /
哪几条阻断级判据当时没跑成）。

【三条判定，各消灭一种已发生的故障】见 `docs/schema/_report.md` §六：
  · 产地记了而表里没有 ⇒ **`AccountingKeyUnregistered`**（记了一笔谁也读不到的账）
  · 表里有而产地没记   ⇒ 打 `[????] 未记录`（「记了 0 次」与「压根没记」必须分开）
  · 值为空             ⇒ 打 `… 0 处`（「没改过」与「没在报」必须分开）

⇒ **加一个记账键的成本必须是「同时给它一个出口」，否则加不进去。**
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from contracts.schema.report import AccountingLine
from novel.errors import AccountingKeyUnregistered


@dataclass(frozen=True)
class AccountingSpec:
    """一个记账键的声明。`item` 是人读的一句话「这个数在数什么」。"""

    key: str
    item: str


#: **每层声明自己有哪些记账键。** 唯一落点。
#:
#: ⚠ 空元组与「这一层不在表里」**不是一回事**：前者是「这层声明过，它没有记账键」，
#: 后者会让 `build_accounting` 把该层的任何一笔账都判成未登记。八层都要有一行。
ACCOUNTING: dict[str, tuple[AccountingSpec, ...]] = {
    'stage0': (),
    'stage1': (),
    'stage2': (),
    # ⚠⚠ **这三个键就是 `stage3.md` §五 记的那笔账**：实跑三本 `--check-only`，
    #    报告里 `attempts` / `revalidated` / `gates_skipped` **一行都没有**，
    #    而它们记的恰好是最该被人看见的三件事。这一格把账还上。
    'stage3': (
        AccountingSpec('attempts', '这份稿第几次才产对的？返工改了什么？'),
        AccountingSpec('revalidated', '这份稿是跑出来的，还是被重判提升上来的？'),
        # 第三个尤其要紧：它是判据 36 那整套设计的**唯一痕迹**，而「闸门没跑成」
        # 与「闸门跑过没事」正是 `Q-2` 要求必须能区分的两件事。
        AccountingSpec('gates_skipped', '哪几条阻断级判据当时【没跑成】？'),
    ),
    'stage4': (),
    'stage5': (),
    'stage6': (),
    'stage7': (),
}


def _count_and_detail(value: Any) -> tuple[int, list[str]]:
    """一笔账的值 → `(几处, 明细)`。

    ⚠ **`None` 与 `0` 都算「记了，是 0」**，不是「没记」——「没记」是**键不在**。
    这是 `Q-2` 那个三态在记账这一侧的样子。
    """
    if value is None:
        return 0, []
    if isinstance(value, bool):
        # 放在 int 之前：`bool` 是 `int` 的子类，落到下一支会变成「1 处」。
        return (1 if value else 0), []
    if isinstance(value, int):
        return value, []
    if isinstance(value, dict):
        return len(value), [f'{k}: {v}' for k, v in value.items()]
    if isinstance(value, (list, tuple, set, frozenset)):
        seq = list(value)
        return len(seq), [str(x) for x in seq]
    if isinstance(value, str):
        return (1 if value else 0), ([value] if value else [])
    return 1, [str(value)]


def build_accounting(stage: str, recorded: dict[str, Any]) -> list[AccountingLine]:
    """`RunContext.accounting` → 报告里的那一节。**空也要报。**

    `recorded` 是 `ctx.accounting`（一份拷贝，`novel/run.py`）。本函数**只读**。
    """
    if stage not in ACCOUNTING:
        raise AccountingKeyUnregistered(
            f'{stage} 在 ACCOUNTING 里没有一行。八层都要有一行——'
            f'「这层没有记账键」要写成空元组，那与「这层没被声明过」不是一回事'
        )
    spec = ACCOUNTING[stage]
    declared = {s.key: s for s in spec}

    unknown = sorted(set(recorded) - set(declared))
    if unknown:
        raise AccountingKeyUnregistered(
            f'{stage} 记了 {unknown} 这几笔账，而 ACCOUNTING 里没有它们。\n'
            f'→ 那是**记了一笔谁也读不到的账**：数已经写进 `_provenance`，人却永远看不见。\n'
            f'→ 要么给它一行声明（`novel/report/accounting.py`），要么别记这笔账。\n'
            f'⚠ 先问一句：那个位置本来有东西吗？没有的话那叫【初始化】不叫改写，'
            f'记一份「本来是空的」只会制造恒定的噪声（stage4 的 `op_forced` 就是这个下场）'
        )

    out: list[AccountingLine] = []
    for s in spec:
        if s.key not in recorded:
            out.append(AccountingLine(key=s.key, item=s.item, recorded=False, count=0, detail=[]))
            continue
        count, detail = _count_and_detail(recorded[s.key])
        out.append(AccountingLine(key=s.key, item=s.item, recorded=True,
                                  count=count, detail=detail))
    return out
