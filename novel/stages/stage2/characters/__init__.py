# -*- coding: utf-8 -*-
"""Characters owner：compile / edit / checks / projection 发布。

正式 API 从本包导出（兼容 ``from novel.stages.stage2 import characters``）。
**Characters v3 为权威 path**：``registry``（Extract→Resolve→compile）/
``checks`` / ``rebind`` / ``candidates`` / ``compile_v3`` / ``prompts_v3``。
正式产物唯一 writer 是 ``stage2.characters``；下游不得消费 analyze 草稿 wire。
"""
from novel.stages.stage2.characters.registry import *  # noqa: F403
