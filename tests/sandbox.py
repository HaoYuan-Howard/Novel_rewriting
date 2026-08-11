#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
sandbox.py —— 一本书的**可写拷贝**，给要真的落盘的那些测试用。

【为什么需要它】盘上那批语料是**原型契约**产的，新 schema 裁了 70 条路径 ⇒
原样加载必然失败（`tests/corpus.py::CUT` 就是那张表）。
要端到端跑一遍节点函数 / CLI，就得先有一份**合当前契约**的书。

【纪律两条】
  ⚠ **原语料一个字节都不许动。** `../Novel_refactoring/Output` 是只读证据，
    改坏了就再也对不了账（`--check-only` 三本 PASS/PASS/PASS 那笔对账靠的就是它没变过）。
  ⚠ **迁移后的语料不许写进仓里。** 那是第二个落点，且会与 `CUT` 分家
    （`tests/corpus.py` 开头逐字写着这条）。所以它只活在 `mkdtemp()` 里，跑完就删。

⚠ 本机 `%TEMP%\\pytest-of-yh255` 的 ACL 坏了，pytest 的 `tmp_path` 一用就炸
  ⇒ 一律 `tempfile.mkdtemp()`。
"""
from __future__ import annotations

import json
import shutil
import tempfile
from pathlib import Path

from tests import corpus

#: 一条 stage3 纵切跑起来要的四份。
STAGE3_INPUTS = ('stage1/main_text.json', 'stage1/hook_copy.json',
                 'stage2/global_grounding.json', 'stage3/style_grounding.json')


def books_with_stage3() -> list[str]:
    return sorted({a.book_dir for a in corpus.iter_artifacts()
                   if a.rel == 'stage3/style_grounding.json'})


def cut(book: str, rel: str):
    """盘上那一份 → 新契约形态。**现算，不落盘。** 找不到返回 `None`。"""
    a = next((x for x in corpus.iter_artifacts()
              if x.book_dir == book and x.rel == rel), None)
    return corpus.load_cut(a) if a is not None else None


class Sandbox:
    """`<mkdtemp>/<书目录名>/…`，装着迁移到新契约的那几份产物。"""

    def __init__(self, book: str, rels: tuple[str, ...] = STAGE3_INPUTS) -> None:
        self.root = Path(tempfile.mkdtemp())
        self.dir = self.root / book
        self.book = book
        self.sid = book.rsplit('_', 1)[-1]
        for rel in rels:
            data = cut(book, rel)
            assert data is not None, f'{book} 少了 {rel}——沙箱建不起来'
            p = self.dir / rel
            p.parent.mkdir(parents=True, exist_ok=True)
            p.write_text(json.dumps(data, ensure_ascii=False), encoding='utf-8')

    def read(self, rel: str) -> dict:
        return json.loads((self.dir / rel).read_text(encoding='utf-8'))

    def write(self, rel: str, data: dict) -> None:
        (self.dir / rel).write_text(json.dumps(data, ensure_ascii=False),
                                    encoding='utf-8')

    def close(self) -> None:
        shutil.rmtree(self.root, ignore_errors=True)
