# -*- coding: utf-8 -*-
"""Knowledge owner（产品节点 ``stage2.global``）：compile / edit / renormalize / checks。

文件系统包名用 ``knowledge``，因 ``global`` 是 Python 关键字不可作标识符。
正式 grounding API 从 ``grounding`` 子模块导出；兼容 shim 见顶层 ``grounding.py``。
"""
from novel.stages.stage2.knowledge.grounding import *  # noqa: F403
