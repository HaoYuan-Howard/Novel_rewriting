# -*- coding: utf-8 -*-
"""Stage2 生产公开面：转发 knowledge + blueprint producer（无业务实现）。

``from …extract import _private`` 与 ``extract.fn`` 均经 ``__getattr__`` 解析，
因 producer 使用下划线私有名且未声明 ``__all__``，不能依赖 ``import *``。
"""
from __future__ import annotations

from novel.stages.stage2.blueprint import producer as _blueprint_producer
from novel.stages.stage2.knowledge import producer as _knowledge_producer

__all__ = []  # 显式经 __getattr__；避免 star-import 漏私有符号


def __getattr__(name: str):
    if hasattr(_knowledge_producer, name):
        return getattr(_knowledge_producer, name)
    if hasattr(_blueprint_producer, name):
        return getattr(_blueprint_producer, name)
    raise AttributeError(f'module {__name__!r} has no attribute {name!r}')


def __dir__():
    names = set(dir(_knowledge_producer)) | set(dir(_blueprint_producer))
    return sorted(names)
