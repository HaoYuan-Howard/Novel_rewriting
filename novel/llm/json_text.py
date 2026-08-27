#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""JSON 响应文本的唯一窄归一化。

模型解析与 Stage2 checkpoint 恢复必须调用同一份纯函数；否则两边各自“剥围栏”
很容易在空白或异常围栏上分叉，让盘上原通道文本与真正验证过的 payload 失去来源关系。
"""
from __future__ import annotations


def normalize_json_text(text: str | None) -> str:
    """防御性地剥掉 `````json … ``` ``；除此之外只裁首尾空白。"""
    normalized = (text or '').strip()
    if normalized.startswith('```'):
        normalized = (normalized.split('\n', 1)[1]
                      if '\n' in normalized else normalized[3:])
        if normalized.rstrip().endswith('```'):
            normalized = normalized.rstrip()[:-3]
    return normalized.strip()


__all__ = ['normalize_json_text']
