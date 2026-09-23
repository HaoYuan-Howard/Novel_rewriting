#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""str06 —— STR-06 迁移期工作包（F1 起存在，F8 分流后删除）。

【为什么是一个迁移期包而不是直接进 stageN】
F0–F7 在隔离分支推进、主仓不出现两套现役定义：新链的判定器与确定性渲染先落在这
里，只依赖 `contracts.schema.str06`（RC 契约）与标准库；F8 切换时按 owner 分流到
`novel/stages/stage4|stage5|stage7` 的正式位置并删除本包。包内纪律：
  • 只准 import contracts.* / novel.errors / 本包 / 标准库 / pydantic；
  • 零 LLM、零盘上 IO（纯函数，落盘归 repository）；
  • validator 不修改输入对象（I-24）。

模块：checks（跨产物机械判定）、delivery（接受正文→交付正文的确定性组装）。
"""
from __future__ import annotations
