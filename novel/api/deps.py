#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
deps.py —— 路由共用的那几件小事。**不 import fastapi 以外的 web 东西，也不定义路由。**

【它为什么存在】`app.py` 要 import 路由、路由要拿到 `base` 与那两道边界校验——
两边直接互相 import 会成环。把共用的这几件放这里，依赖就是单向的：
`app.py → routes/* → deps.py`。
"""
from __future__ import annotations

from fastapi import HTTPException, Request

from contracts.invariants import STAGES
from novel import paths
from novel.api.errors import BAD_REQUEST


def get_base(request: Request) -> str:
    """这个服务在看哪一批书。**由 `create_app(base)` 注入，不读环境变量。**

    ⚠ 不读环境的理由与 `novel/__init__.py` 那条一样：import 期不做任何事。
    副产品是每个测试可以有自己的 `base`，于是 HTTP 测试跑在 `mkdtemp()` 的沙箱里
    而不是真语料上。
    """
    return request.app.state.base


def checked_sid(sid: str) -> str:
    """书 id 的边界校验。**不合法 ⇒ 400，不是 500。**

    ⚠⚠ 这就是 `novel/api/errors.py` 里 `PathRefused` 那一格说的「在边界上先验」。
    `paths.check_sid` 挡的四种走法（空 / `..` / 带分隔符 / 带盘符）各自都是一个
    **任意目录读写入口**，而它们的产地是**客户端**——那是 400。
    验过之后再从深处冒出来的 `PathRefused`（例如「一个 id 匹配到两个目录」，F-088）
    就真的是我们的 bug，那时候 500 是对的。
    ⇒ 「边界验过之后再抛就是 bug」是一条说得清的线，而给异常分子类不是。
    """
    try:
        return paths.check_sid(sid)
    except Exception as e:                       # noqa: BLE001 —— 就地翻，理由见上
        raise HTTPException(status_code=BAD_REQUEST, detail=str(e)) from e


def checked_kind(kind: str) -> str:
    """校验报告的 `:kind`。**取值面 = `invariants.STAGES`，不手写第二份清单。**

    ⚠ 退役 Stage3 前的八个 kind 里，第六个原来叫 `stage5_review`（原型脚本名泄漏进契约），
    E 期已改名为 `stage5`——于是这一列与 `STAGES` 逐值相等、可以直接派生。
    改名的账记在 `docs/schema/_api.md` §七；README 只保留现役入口，不保存改名历史。

    ⚠ **不在七个现役值里 ⇒ 400 并把七个列出来**，而不是 404：
    404 是「这本书没有」，400 是「你给的这个层名不存在」，两件事。
    """
    if kind not in STAGES:
        raise HTTPException(
            status_code=BAD_REQUEST,
            detail=f'未知的校验层 {kind!r}。七个取值：{list(STAGES)}')
    return kind
