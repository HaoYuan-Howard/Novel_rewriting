#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""只读 operator 快照内的独立对象快速克隆。

这里的 pickle bytes 只能由当前进程里的 ``factory`` 产生，随后放进既有的
invocation-scoped memo；它不落盘、不接收路径/请求字节，也不跨 surface CAS。
``loads`` 因而只反序列化本仓已验证模型的内部编码，不是通用 pickle 读取口。

这个口只给少数 heavyweight、深层纯数据对象使用。普通标量/小模型继续走
``readonly_snapshot_value``，避免为了统一形式反而增加序列化成本。
"""
from __future__ import annotations

import pickle
from typing import Callable, TypeVar, cast

from novel.run import RunContext
from novel.store import readonly_snapshot_value

T = TypeVar('T')
_PROTOCOL = 5


def readonly_snapshot_clone(
    ctx: RunContext, key: tuple[object, ...], factory: Callable[[], T],
) -> T:
    """缓存本调用自产 bytes，并为每个消费者 thaw 一份独立对象。"""
    blob = readonly_snapshot_value(
        ctx,
        ('trusted-pickle-clone-v1', *key),
        lambda: pickle.dumps(factory(), protocol=_PROTOCOL),
    )
    if not isinstance(blob, bytes):
        raise TypeError('readonly snapshot clone memo 必须保存 bytes')
    return cast(T, pickle.loads(blob))


__all__ = ['readonly_snapshot_clone']
