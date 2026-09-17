#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""测试兼容 re-export：真身在 ``novel.llm.run_journal``。

生产代码必须 ``from novel.llm import run_journal`` / ``import novel.llm.run_journal``，
不得再依赖本模块。
"""
from __future__ import annotations

from novel.llm import run_journal as _impl

# 含未列入 ``__all__`` 的 ``VERSION`` 等测试仍直接读的模块属性。
for _name in dir(_impl):
    if not _name.startswith('_'):
        globals()[_name] = getattr(_impl, _name)

__all__ = list(getattr(_impl, '__all__', []))
del _name, _impl
