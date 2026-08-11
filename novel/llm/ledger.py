#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
ledger.py —— 成本账。**原型完全没有这一层。**

【两份账，答的是两个问题】（`_common.md` §八）
  · **账本**（本模块）：`<Output>/_service/ledger.jsonl`，跨书跨时间，答「这周 / 这本书花了多少」；
  · **产物里那一份**（`_provenance.usage`）：答「**这一份**花了多少」——
    那是看返工回路边际成本时会问的，而账本按时间排，答不了。
  ⚠ token 数**不可再生**（`Q-3` 的判准）：响应一丢就永远算不回来了，所以两份都必须落盘。

【为什么是 append-only JSONL】它**永不需要读-改-写**：
并发安全（两个节点同时跑不会互相截断）· 被 kill 不损坏（最多留半行，前面的行照样可读）·
可以直接 `grep`。一个「统计文件」如果需要先读进来改再写回去，
那它在被 kill 的那一刻就是全损——而这层账正是为了记录**已经花掉的钱**。

⚠⚠ **只存事实，不存价格**（`Q-10`：不显示预估花费——「猜一个数写在界面上比不显示更坏」）。
本模块存 token 数与调用数，**不存钱**。要出价钱是另一件事，要配一张带日期的费率表。
"""
from __future__ import annotations

import json
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path

from contracts.schema import Usage
from novel.errors import ProvenanceRefused

#: 账本在 `<Output>/` 下的位置。跨书，所以不在任何一本书的目录里。
LEDGER_REL = ('_service', 'ledger.jsonl')

#: token 三项的名字。**一处落点**——三个计数器、三个「见过几次」、`Usage` 的三个键
#: 都从它派生，免得哪天加一项时漏掉其中一处。
TOKEN_KINDS = ('prompt_tokens', 'completion_tokens', 'reasoning_tokens')


def ledger_path(base: str | Path) -> Path:
    """`<Output>` → 账本路径。**唯一落点**——账本只有一个，别按书分账。"""
    return Path(base).joinpath(*LEDGER_REL)


@dataclass(frozen=True)
class UsageMark:
    """账本的一把**刻度**：结算「上一份产物之后又花了多少」时的起点。

    ⚠ `seq` 让它**一次性**：同一把刻度结算两次 = 两份产物认领同一批 token，
    而那正是本次要修掉的那个病的镜像。见 `CostLedger.commit`。
    """

    seq: int
    calls_made: int
    calls_cached: int
    tokens: tuple[int, ...]
    #: 每一项**被响应报过几次**。差值为 0 ⇒ 这段窗口里没人报过它 ⇒ `null`，不是 `0`。
    seen: tuple[int, ...]


@dataclass
class CostLedger:
    """一次 run 的成本账：往盘上追加行，同时在内存里攒出**每一份产物**的 `usage`。

    ⚠⚠ **`calls_made` 与 `calls_cached` 是两个计数器，不许合并。**
    只数真调用，会让「参数不变的重跑什么都没跑」（`Q-11` / `R-12.4` 要求**显式说出来**
    的那条语义）与「什么都没干」在报告上**同形**。

    ⚠⚠ **本类的计数器是 run 累计的，而 `_provenance.usage` 问的是「这一份花了多少」。**
    两者原来是同一个数：`RunContext.llm_provenance` 直接写 `ledger.usage()`。
    于是一次跑产 N 份产物时，第 2 份带着第 1 份的账，第 3 份带着前两份的，
    一路累加——而返工回路的边际成本正是要从这一格里读的。
    ⇒ 结算走 `snapshot()` / `usage_since(mark)` 的**差值**；`usage()`（无参）仍是 run 总量。
    """

    path: Path | None = None            # `None` = 不落盘（测试 / `--dry-run`），仍然计数

    calls_made: int = 0                 # **真发出去**的调用数
    calls_cached: int = 0               # 命中缓存的次数（＝ 0 元）
    prompt_tokens: int = 0
    completion_tokens: int = 0
    reasoning_tokens: int = 0

    #: 每一项**被响应报过几次**。
    #:
    #: ⚠⚠ 原来这里是**一个** `_saw_token_counts: bool`，三项共用。于是一次只给了
    #: `prompt_tokens` 的响应（DeepSeek 实测有）会把另外两项写成 `0`——
    #: 而契约明写「`null` = 这次响应**没给** usage，**不是 0**」（`_common.md` §八）。
    #: 一个全局开关同时管三件事，必然把其中两件搞错。⇒ 一项一个计数。
    _seen: dict[str, int] = field(default_factory=lambda: {k: 0 for k in TOKEN_KINDS},
                                  repr=False)

    #: 刻度的世代号。每 `commit` 一次 +1，于是旧刻度立刻作废。
    _seq: int = field(default=0, repr=False)

    def record(self, *, node: str, sid: str, model: str, code: str,
               cached: bool, cache_policy: str,
               prompt_tokens: int | None = None,
               completion_tokens: int | None = None,
               reasoning_tokens: int | None = None,
               purpose: str = '') -> None:
        """记一次调用。`cached=True` 表示这一次是缓存命中（0 元）。

        ⚠ **缓存命中也要记一行。** 不记的话，「这次跑读了 31 份缓存」与
        「这次跑什么都没做」在账本上长得一模一样，而这两件事的下一步动作完全不同。
        """
        if cached:
            self.calls_cached += 1
        else:
            self.calls_made += 1
        for name, val in zip(TOKEN_KINDS, (prompt_tokens, completion_tokens, reasoning_tokens)):
            if val is not None:
                setattr(self, name, getattr(self, name) + val)
                self._seen[name] += 1

        if self.path is None:
            return
        row = {
            'at': datetime.now(timezone.utc).isoformat(timespec='seconds'),
            'sid': sid, 'node': node, 'model': model, 'code': code,
            'cached': cached, 'cache_policy': cache_policy,
            'prompt_tokens': prompt_tokens,
            'completion_tokens': completion_tokens,
            'reasoning_tokens': reasoning_tokens,
            'purpose': purpose,
        }
        self._append(row)

    def _append(self, row: dict) -> None:
        """一行 JSON + `\\n`，**按字节追加**。

        ⚠ 二进制模式不是洁癖：Windows 上文本模式会把 `\\n` 写成 `\\r\\n`，
        于是同一份账本在两台机器上是两种字节，`grep`/`wc -l` 之外的任何逐字节比对都会打架。
        """
        assert self.path is not None
        self.path.parent.mkdir(parents=True, exist_ok=True)
        line = json.dumps(row, ensure_ascii=False, sort_keys=True) + '\n'
        with open(self.path, 'ab') as fh:
            fh.write(line.encode('utf-8'))

    # ── 结算：run 总量 vs **这一份** ──────────────────────────────────────

    def snapshot(self) -> UsageMark:
        """当前刻度。**一份产物的账 = 两把刻度之间的差值。**"""
        return UsageMark(
            seq=self._seq, calls_made=self.calls_made, calls_cached=self.calls_cached,
            tokens=tuple(getattr(self, k) for k in TOKEN_KINDS),
            seen=tuple(self._seen[k] for k in TOKEN_KINDS),
        )

    def usage_since(self, mark: UsageMark) -> Usage | None:
        """从 `mark` 到现在花了多少。**纯算，一个字节都不动**（包括刻度本身）。

        ⚠ 与 `commit` 分成两步是有原因的：铸造来源账时校验可能失败
        （`StyleProvenance.attempts` 之类的后置条件），而**校验失败不许把刻度推走**——
        推走了那段窗口的账就再也没有产物认领它，Σ 不变量当场破。
        ⇒ 先纯算、再构造（可能抛）、构造成功了才 `commit`。
        """
        return self._usage_between(mark)

    def commit(self, mark: UsageMark) -> UsageMark:
        """把刻度推到现在，返回**下一把**。

        ⚠⚠ **同一把刻度不许提交两次。** 提交两次 = 两份产物各自认领同一批 token，
        而那正是本次要修掉的那个病（run 累计写进按份计的字段）的镜像形态：
        一个数被两份产物同时声称是自己的。它是显式终态，不是一笔悄悄重复的账。
        """
        if mark.seq != self._seq:
            raise ProvenanceRefused(
                f'这把刻度是第 {mark.seq} 代，账本已经走到第 {self._seq} 代——'
                f'同一把刻度结算了两次，等于两份产物各自认领同一批 token。'
                f'⇒ 一份产物一次结算（`RunContext` 的两个来源账铸造口各调一次）')
        self._seq += 1
        return self.snapshot()

    def usage(self) -> Usage | None:
        """**整个 run** 花了多少。⚠ 进 `_provenance.usage` 的**不是**这一份，是 `usage_since` 的差值。

        一次调用都没有 ⇒ `None`（键不在）。
        """
        return self._usage_between(None)

    def _usage_between(self, mark: UsageMark | None) -> Usage | None:
        """`mark`（`None` = run 起点）到现在的用量。**三态在这里兑现。**

        ⚠ `None` 与「全 0 的 usage」不是一回事：前者是「这份产物没有任何模型参与」
        （0 成本节点该是这个），后者是「跑过，而它一次都没命中也没发出去」——
        后者不该发生，发生了要看得见。

        ⚠ token 三项是 `int | null`，**`null` = 这次响应没给 usage，不是 0**
        （`_common.md` §八）。DeepSeek 实测会有不带 usage 的响应，
        而把它记成 0 会让「没给」与「真的没花」混掉。
        ⇒ 判据是「**这段窗口里**这一项被报过几次」，一项一判：
          只给了 `prompt_tokens` 的那次响应，不许让另外两项从 `null` 变成 `0`。
        """
        made = self.calls_made - (mark.calls_made if mark else 0)
        cached = self.calls_cached - (mark.calls_cached if mark else 0)
        if not made and not cached:
            return None
        vals = {}
        for i, k in enumerate(TOKEN_KINDS):
            seen = self._seen[k] - (mark.seen[i] if mark else 0)
            vals[k] = (getattr(self, k) - (mark.tokens[i] if mark else 0)) if seen else None
        return Usage(calls_made=made, calls_cached=cached, **vals)
