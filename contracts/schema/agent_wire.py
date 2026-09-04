#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""agent 严格 JSON wire 的动态映射适配器。

严格结构化输出只能生成 schema 预先声明的 object 键；业务上的动态键映射因此必须
在线上表达成带显式键字段的行数组。Pydantic 收到数组后在这一层恢复成既有 ``dict``，
让 provider wire 的限制不渗进正式 artifact、判据、checkpoint payload 或人工编辑契约。

这里刻意不自动猜任意 ``dict`` 的键名/值形状。每个调用点必须声明 row model、键字段
以及可选的值字段；这样新增动态映射若没有设计明确的 wire 行形状，会在公共 schema
预检处关闭式失败，而不是把未知语义静默改写成某种数组。
"""
from __future__ import annotations

from typing import Any, TypeVar

from pydantic import BaseModel


R = TypeVar('R', bound=BaseModel)


def keyed_rows_to_mapping(
        value: object, *, row_model: type[R], key_field: str,
        value_field: str | None = None,
) -> object:
    """把 provider 的唯一键行数组还原为内部映射；既有映射用于可信重放。

    ``value_field=None`` 表示除键字段外的整行都是映射值；指定 ``value_field``
    则只取该字段。重复键没有可证明的覆盖顺序，必须拒绝而不能 last-write-wins。

    已是 ``dict`` 的值原样返回，供当前内部对象的 ``model_dump``、验后 checkpoint
    payload 与 0-wire typed 重放继续使用；真正下发给 agent 的 JSON Schema 只发布
    行数组形状。durable checkpoint 仍绑定当时 schema SHA，不借此跨 schema 漂移恢复。
    """
    if not isinstance(value, list):
        return value

    out: dict[str, Any] = {}
    for index, raw in enumerate(value):
        row = row_model.model_validate(raw)
        payload = row.model_dump(by_alias=True, mode='python')
        key = payload.pop(key_field)
        if not isinstance(key, str) or not key:
            raise ValueError(
                f'{row_model.__name__}[{index}].{key_field} 必须是非空字符串')
        if key in out:
            raise ValueError(
                f'{row_model.__name__}.{key_field} 不能重复：{key!r}')
        if value_field is None:
            out[key] = payload
        else:
            if set(payload) != {value_field}:
                raise ValueError(
                    f'{row_model.__name__} 除 {key_field!r} 外必须且只能有 '
                    f'{value_field!r}')
            out[key] = payload[value_field]
    return out


__all__ = ['keyed_rows_to_mapping']
