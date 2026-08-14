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
    # ⚠⚠ **全链唯一一层的记账横跨两份产物**（`stage2.md` §3.3 列了 11 个出口）：
    #    前 6 个在 `global_grounding._provenance`、后 5 个在 `chapter_blueprints`。
    #    合成一节而不是两节，理由与「判据表只有一张」同源：两个节点是**同一层**，
    #    拆成两节，「这一层落盘前改写了多少」就要读两屏才拼得出来。
    # ⚠ 这一层的记账**不是可选的装饰**：本层有一整族判据**断言的就是这些计数而不是字段值**
    #    （`secret_setup_order` 断言 `setup_chapters_pruned` · `anchor_not_verbatim` 断言
    #    `verbatim_recovered` · `verbatim_failed` 断言 `verbatim_failures`）——
    #    因为那几件事都跑在**落盘前**，再去断言字段值就是一条**永远为真**的判定。
    # ⚠ `partial_regen_chapters` / `repairs` 即使为空也必须落盘并报告：
    #    「记了 0 次」与「压根没记」不是一回事（`Q-2` 的三态）。
    'stage2': (
        AccountingSpec('address_by_fallback', '`by` 兜底成 `public` 几次？（判据断言这个计数）'),
        AccountingSpec('address_surface_rewrites', '哪几条称呼被最长子串回取改写过？'),
        AccountingSpec('address_from_chapter_raised', '哪几条称呼的可用章被往后抬过？'),
        AccountingSpec('canonical_distinguishers_stripped', '哪几个显示名被剥掉了消歧区分词？'),
        AccountingSpec('setup_chapters_pruned', '哪几条秘密的埋点章被裁掉了？'
                                                '（「冷揭示」与「被裁空」只能靠它分开）'),
        AccountingSpec('verbatim_rewrites', '哪几个锚点被**精确回取**过？（一个字都没动）'),
        AccountingSpec('verbatim_recovered', '哪几个锚点是**按最长公共子串救回来的**？'
                                             '（它们本来就不是原文字节）'),
        AccountingSpec('verbatim_failures', '哪几个锚点**锚不到**？（非空即失败态，按三种根因分）'),
        AccountingSpec('partial_regen_chapters', '哪几章是定向重生成过的？'),
        AccountingSpec('repairs', '那几次定向重生成各修了什么？（`failure_items` + 前后三个计数）'),
    ),
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
    # ⚠⚠ `params` 是**原型记了、而全仓没有一个读者**的那个键（`stage4.md` §3.3）：
    #    `beat_seq` / `beat_adjacent` / `beat_layer` 三条 HARD 拿**当前**的分组函数去量，
    #    从不读它——**每一次都在拿新尺子量旧布**。本层给了它两个出口：
    #    这一行（常驻）与判据 `params_drift`（三条后置条件先比它再判）。
    # ⚠ **刻意不声明 `partial_rewrite`**：本层没有一条 0 成本路会把计划改成混版的
    #    （`--identity` 写的是另一份文件、`--apply-edit` 落的是新一轮）。
    #    声明一个结构上永远记不上的键，只会让报告上恒挂一行「未记录」。
    'stage4': (
        AccountingSpec('params', '这份计划的拍是照哪一套阈值切的？'),
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
    #    `_provenance` 而本层的报告按**这一次跑了什么**报（判据那 47 行由验收相报）；
    #    后者本层一条都不会跳（判据全是确定性的，没有「缺料所以没跑」那一支）。
    #    声明一个结构上永远记不上的键，只会让报告上恒挂一行「未记录」。
    'stage5': (
        AccountingSpec('punct_fixed', '这一次标点归一改了多少字符？'),
        AccountingSpec('quotes_fixed', '这一次引号体归一改了多少字符？'),
        AccountingSpec('paras_fixed', '这一次有几章的段落分隔被归一改过？'),
    ),
    # ⚠ `params` 是**原型完全没有**的键（`stage6.md` 事实④）：本层是付费节点，
    #    而换了模型、换了 `--max-tokens`，产物里没有任何一处说得出来。
    'stage6': (
        AccountingSpec('params', '这一次是用什么旋钮跑的？（`max_tokens` / `tag`）'),
        AccountingSpec('revalidated', '这份稿是跑出来的，还是被重判提升上来的？'),
        AccountingSpec('gates_skipped', '哪几条阻断级判据当时【没跑成】？'),
        AccountingSpec('partial_rewrite', '这份产物是混版的吗？（`--recheck` 改过其中一部分）'),
    ),
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
