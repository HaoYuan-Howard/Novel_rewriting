#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""JSON 响应文本的唯一窄归一化与 CLI wire 解码。

模型解析与 Stage2 checkpoint 恢复必须调用同一份纯函数；否则两边各自“剥围栏”
很容易在空白或异常围栏上分叉，让盘上原通道文本与真正验证过的 payload 失去来源关系。

``decode_wire_json`` 解析子进程 stdout 的 JSON 行，与 ``ArtifactStore`` 的盘上产物
读取明确分界：分层守卫把 ``json.load(s)`` 保留给 store。
"""
from __future__ import annotations

import json
from typing import Any


def normalize_json_text(text: str | None) -> str:
    """防御性地剥掉 markdown JSON 围栏；除此之外只裁首尾空白。"""
    normalized = (text or '').strip()
    if normalized.startswith('```'):
        normalized = (normalized.split('\n', 1)[1]
                      if '\n' in normalized else normalized[3:])
        if normalized.rstrip().endswith('```'):
            normalized = normalized.rstrip()[:-3]
    return normalized.strip()


def decode_wire_json(text: str) -> Any:
    """解析 CLI wire JSON，与 ``ArtifactStore`` 的盘上产物读取明确分界。

    分层守卫把 ``json.load(s)`` 保留给 store；这里处理的是子进程 stdout，不是上游
    artifact，因此用 decoder 的协议入口，避免两种证据源在代码形状上被混为一谈。
    """
    return json.JSONDecoder().decode(text)


__all__ = ['normalize_json_text', 'decode_wire_json']
