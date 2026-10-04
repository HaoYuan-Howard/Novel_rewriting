#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
test_prompts.py —— 响应顶层键派生与空壳闸。

⚠ **历史**：本文件曾守「prompt 枚举块整份进渲染」
（写法词表 prompt 版漂成「象征/强化感官」而真词表是「意象/感官」、另 8 词一个没提；
篇幅带漏一整档——模型按 A 算、校验按 B 判，谁也不会出声）。
`fill()`／`ENUM_BLOCKS` 随旧链 prompt 模板删除（2026-10-04 清理批）后，
那段守卫的对象不复存在；现役语义单元的枚举下发由各 owner 模板自带的
插值纪律承担。本文件只留 `required_top_level`／`missing_required_fields`
的用例（判据 39：结构化调用点声明全部必需顶层键）。
"""
from __future__ import annotations

from pydantic import BaseModel, Field

from novel.llm.prompts import required_top_level


# ─────────────────────────────────────────────────────────────────────────────
# 一、`required_top_level`
# ─────────────────────────────────────────────────────────────────────────────

def test_required_top_level_uses_the_alias_not_the_field_name():
    """响应是 JSON，`require` 要拿去查 **JSON 的顶层键**。"""
    class M(BaseModel):
        provenance: dict = Field(alias='_provenance')
        opt: str | None = None

    assert required_top_level(M) == ('_provenance',)


def test_required_top_level_skips_anything_with_a_default():
    class M(BaseModel):
        a: int
        b: int = 1
        c: str | None = None

    assert required_top_level(M) == ('a',)
