#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""enums.py —— `GET /api/enums`。整份取值面。"""
from __future__ import annotations

from typing import Any

from fastapi import APIRouter

from novel.api.enums import payload

router = APIRouter()


@router.get('/enums')
def get_enums() -> dict[str, Any]:
    """整份取值面。**刻意没有 `response_model`。**

    它的键集就是它的内容（全部由 `contracts/invariants.py` 的公开常量派生）。
    写一个逐字段的 `StrictModel` 去描述它，等于把「自动发布」
    重新变回一份**手写清单**——而那正是 `novel/api/enums.py` 开头论证过要消灭的东西。
    ⇒ 它的守卫是**超集测试**（`tests/test_api_enums.py`），不是类型。
    这一行也写在 `docs/schema/_api.md` §二 里，免得下一个人来「补齐」它。

    ⚠ 每次现算（成本可忽略），**不缓存**：
    缓存一份取值面，就要回答「什么时候失效」——而它的输入是**代码**，
    改了代码本来就要重启服务。加一层缓存只是把一个不存在的问题换成一个真问题。
    """
    return payload()
