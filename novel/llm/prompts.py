#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""响应顶层键推导与空壳闸。

旧链的 `$NAME` 枚举块渲染（``fill``／``ENUM_BLOCKS``）与结构提示拼接
（``with_json_shape``）随旧链 prompt 模板删除：现役语义单元的 prompt 由
各 owner 自带的模板渲染，不再走本模块。这里只保留**从响应模型派生
``require``** 的那一半——它是判据 39（每个结构化调用点声明全部必需
顶层键）的全部实现。
"""
from __future__ import annotations

from typing import Sequence

from pydantic import BaseModel


def required_top_level(model_cls: type[BaseModel]) -> tuple[str, ...]:
    """这个响应模型的**必需顶层键**。`ask_json` 的 `require` 默认从它派生。

    ⚠⚠ **这一个默认值就是判据 39 的全部实现。**
    判据 39 要求「每个结构化调用点声明**全部**必需顶层键」。从响应模型派生后，
    调用点无法漏写其中一部分，也不会让合法 JSON 中的结构缺失静默穿过。

    ⚠ 用**别名**（盘上键名）不是字段名：响应是 JSON，`require` 要拿去查 JSON 的顶层键。
    """
    return tuple(f.alias or name for name, f in model_cls.model_fields.items()
                 if f.is_required())


def missing_required_fields(
    payload: object, require: Sequence[str], model_cls: type[BaseModel],
) -> list[str]:
    """所有模型入口共用的顶层空壳闸；稀疏集合须显式声明 min_length=0。

    False 和 0 是值。合法空集合、缺失与 null 不互换；错误容器类型仍由
    随后的 schema 校验拒绝。按 wire 别名检查，避免不同请求路不同口径。
    """
    if not isinstance(payload, dict):
        return list(require)
    explicit_empty = {
        field.alias or name for name, field in model_cls.model_fields.items()
        if any(getattr(meta, 'min_length', None) == 0 for meta in field.metadata)
    }
    missing = []
    for key in require:
        if key not in payload:
            missing.append(key)
            continue
        value = payload[key]
        empty = value is None or (
            isinstance(value, (str, bytes, list, tuple, dict, set, frozenset))
            and len(value) == 0)
        if empty and not (
            key in explicit_empty and isinstance(value, (list, dict))
        ):
            missing.append(key)
    return missing
