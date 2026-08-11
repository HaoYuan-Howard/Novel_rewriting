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

#: 账本在 `<Output>/` 下的位置。跨书，所以不在任何一本书的目录里。
LEDGER_REL = ('_service', 'ledger.jsonl')


def ledger_path(base: str | Path) -> Path:
    """`<Output>` → 账本路径。**唯一落点**——账本只有一个，别按书分账。"""
    return Path(base).joinpath(*LEDGER_REL)


@dataclass
class CostLedger:
    """一次 run 的成本账：往盘上追加行，同时在内存里攒出这一份产物的 `usage`。

    ⚠⚠ **`calls_made` 与 `calls_cached` 是两个计数器，不许合并。**
    只数真调用，会让「参数不变的重跑什么都没跑」（`Q-11` / `R-12.4` 要求**显式说出来**
    的那条语义）与「什么都没干」在报告上**同形**。
    """

    path: Path | None = None            # `None` = 不落盘（测试 / `--dry-run`），仍然计数

    calls_made: int = 0                 # **真发出去**的调用数
    calls_cached: int = 0               # 命中缓存的次数（＝ 0 元）
    prompt_tokens: int = 0
    completion_tokens: int = 0
    reasoning_tokens: int = 0

    #: 有没有见过一次「响应没给 usage」。见 `usage()` 里那条三态。
    _saw_token_counts: bool = field(default=False, repr=False)

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
        for name, val in (('prompt_tokens', prompt_tokens),
                          ('completion_tokens', completion_tokens),
                          ('reasoning_tokens', reasoning_tokens)):
            if val is not None:
                setattr(self, name, getattr(self, name) + val)
                self._saw_token_counts = True

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

    def usage(self) -> Usage | None:
        """进 `_provenance.usage` 的那一份。**一次调用都没有 ⇒ `None`（键不在）。**

        ⚠ `None` 与「全 0 的 usage」不是一回事：前者是「这份产物没有任何模型参与」
        （0 成本节点该是这个），后者是「跑过，而它一次都没命中也没发出去」——
        后者不该发生，发生了要看得见。

        ⚠ token 三项是 `int | null`，**`null` = 这次响应没给 usage，不是 0**
        （`_common.md` §八）。DeepSeek 实测会有不带 usage 的响应，
        而把它记成 0 会让「没给」与「真的没花」混掉。
        """
        if not self.calls_made and not self.calls_cached:
            return None
        known = self._saw_token_counts
        return Usage(
            calls_made=self.calls_made,
            calls_cached=self.calls_cached,
            prompt_tokens=self.prompt_tokens if known else None,
            completion_tokens=self.completion_tokens if known else None,
            reasoning_tokens=self.reasoning_tokens if known else None,
        )
