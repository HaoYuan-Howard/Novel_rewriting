#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
accounting.py —— 记账键的出口。**一张会炸的表。**

【它存在的那一句话】`Q-1`：**记账不是终点，报出来才是。**
历史原型 13 个记账键里曾有 6 个 0 出口；本表由那次事故建立。Stage3 已整层退役，
旧机械 provenance 只由 legacy 读取面解释，因此不在当前声明表占一行。

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
#: 后者会让 `build_accounting` 把该层的任何一笔账都判成未登记。当前七层都要有一行。
ACCOUNTING: dict[str, tuple[AccountingSpec, ...]] = {
    'stage0': (),
    'stage1': (),
    # Global 只记录不可静默丢弃的定位失败；Blueprint 失败保存在已购响应恢复账。
    'stage2': (
        AccountingSpec('setup_chapters_pruned', '哪几条秘密的埋点章被裁掉了？'
                                                '（「冷揭示」与「被裁空」只能靠它分开）'),
        AccountingSpec('evidence_locator_failures',
                       '哪些事实 locator 因错章、零命中、多命中或正式 span 漂移而失败？'
                       '（非空即证据定位失败态）'),
    ),
    # ⚠ **刻意不声明 `partial_rewrite`**：本层没有一条 0 成本路会把计划改成混版的
    #    （`--identity` 只作内存诊断、`--apply-edit` 落的是新一轮）。
    #    声明一个结构上永远记不上的键，只会让报告上恒挂一行「未记录」。
    'stage4': (
        AccountingSpec('revalidated', '这份稿是跑出来的，还是被重判提升上来的？'),
        AccountingSpec('gates_skipped', '哪几条阻断级判据当时【没跑成】？'),
    ),
    # ⚠⚠ **这三个键就是被裁掉的 `mech` 块换的落点**（`stage5.md` §1.1）：原型把归一链的
    #    账落进**每一份成稿**，而它全仓 **0 个读者、0 个报告出口**，盘上 31/31 四项全 0。
    #    它记的是「这一次归一改了多少」——那是**一次运行的读数**，不是产物的属性；
    #    记在产物里而没有出口，正是它 0 读者的根因。
    # ⚠ 而 `mech.quote_problems` **不在这三格里**：它根本不是记账，**是一条结论**
    #    （「引号配对异常、未归一、留着交人工」），已升成判据 `quote_unbalanced`。
    #    把一条结论塞进记账块，就是它既没有出口、也没有守卫的原因。
    # ⚠ **刻意不声明 `revalidated` / `gates_skipped`**：前者由 `promoted()` 写进
    #    `_provenance` 而本层的报告按**这一次跑了什么**报（判据那 46 行由验收相报）；
    #    后者本层一条都不会跳（判据全是确定性的，没有「缺料所以没跑」那一支）。
    #    声明一个结构上永远记不上的键，只会让报告上恒挂一行「未记录」。
    'stage5': (
        AccountingSpec('punct_fixed', '这一次标点归一改了多少字符？'),
        AccountingSpec('quotes_fixed', '这一次引号体归一改了多少字符？'),
        AccountingSpec('paras_fixed', '这一次有几章的段落分隔被归一改过？'),
    ),
    # ⚠ F8a：Stage6 整层退役，ACCOUNTING 行随层删除（STAGES 不再有 stage6）。
    # ⚠ `params` 与 stage6 同形（两个付费节点犯同一个错：换了模型、换了 `--max-tokens`，
    #    产物里没有任何一处说得出来）。另外三个的理由也同 stage6。
    'stage7': (
        AccountingSpec('params', '这一次是用什么旋钮跑的？（`max_tokens` / `tag`）'),
        AccountingSpec('revalidated', '这份映射表是跑出来的，还是被重判提升上来的？'),
        AccountingSpec('gates_skipped', '哪几条阻断级判据当时【没跑成】？'),
        AccountingSpec('partial_rewrite', '这份产物是混版的吗？（`--recheck` / `--apply-edit` '
                                          '改过其中一部分）'),
    ),
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
            f'{stage} 在 ACCOUNTING 里没有一行。当前七层都要有一行——'
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
