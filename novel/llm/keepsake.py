#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
keepsake.py —— 「答案流丢失」那一份的**留证落点**。整个模块只做一件事：落字节。

【为什么它是单独一个模块，而不是 `client.py` 里的一个函数】
`tests/test_layering.py::test_only_the_store_writes_to_disk` 按**文件**豁免写盘口。
把 `client.py` 整个放进白名单，等于给全仓最长、改动最频繁的那个模块开永久豁免
——它下一次真的绕开 store 写产物时，那条守卫就不会出声了。
⇒ 留证这一处写盘收进这里：三十行、没有第二个职责、豁免范围一眼看得完。
（同一个理由让 `novel/appendlog.py` 成为五份 `_service/*.jsonl` 的唯一追加口。）

【留证不是缓存，也不是产物】
 · **不是缓存**：缓存会被**读回来当答案用**，所以 `ResponseCache.put` 拒写空正文是对的
   （缓存一份空壳，四档都会各自读回自己那份空的）。留证只给人看。
 · **不是产物**：不进 `ARTIFACTS`、不进来源账、没有 `_provenance`，
   完成度与阶段视图都不看它（`_` 前缀，与 `_rounds` / `_revalidation` 同类）。
 · **救场仍然禁止从 `reasoning` 里抠答案**——那里面混着推敲过程，抠回来的是半成品
   （`novel/llm/transport.py::ChatResponse.reasoning`）。留证只回答一个问题：
   那条通道里到底装了什么。在此之前这个问题只能拿 token 体积去猜。
"""
from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from novel import paths


def write_answer_lost(novel_dir: Path, attempt_id: str,
                      payload: dict[str, Any]) -> tuple[str | None, str | None]:
    """把「答案流丢失」那一份**整份**写进书目录下的留证文件。

    返回 `(相对书目录的路径, 失败原因)`——成功时后者是 `None`，反之亦然。

    ⚠⚠ **它永远不许把一次跑弄死。** 这是诊断不是产物：盘满、路径超长
    （Windows `MAX_PATH`，本仓踩过）、权限不对——任何一种都只该让**证据缺席**，
    而不该让一次已经付过钱、且马上就要正常重采/降档的调用抛在这里。
    ⇒ 吞 `OSError`，把原因原样交回去记进审计行（`Ledger.note_answer_lost`）。

    ⚠ 吞的**只有** `OSError`：`TypeError` 之类是我们自己把不可序列化的东西塞进了
    `payload`，那是 bug，不该伪装成「盘上写不下」。
    """
    try:
        target = paths.answer_lost_path(novel_dir, attempt_id)
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(
            json.dumps(payload, ensure_ascii=False, indent=2) + '\n',
            encoding='utf-8')
    except OSError as exc:
        return None, f'{type(exc).__name__}: {exc}'
    return paths.answer_lost_rel(attempt_id), None
