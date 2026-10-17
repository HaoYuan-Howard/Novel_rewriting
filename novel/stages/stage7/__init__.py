#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""novel.stages.stage7 —— STR-06 新链 Stage7：成书装配。

【本地块】
  · `assembly.py` 成书装配：核对章回执链后直接装配已接受正文，TXT 先落盘、
                  manifest 最后提交（`str06.stage7.assemble`）。
  · 命名前移批（2026-10-18，rc26）：`naming.py` 随 `str06.stage4.naming`
    迁往 `novel/stages/stage4/naming.py`，本包不再承载命名。

【包级公开面】跨 stage 只许 `from novel.stages.stage7 import X`；深路径 import
本包内部模块一律禁止（守卫见
`tests/test_layering.py::test_cross_stage_imports_stay_on_the_package_surface`）。
本包经 `novel.stages.stage5` 的公开面消费篇幅口径与文本 SHA。
"""
from __future__ import annotations

from novel.stages.stage7.assembly import (
    BodyAccess, DeliveryOutcome, assemble_delivery, build_delivery_manifest,
    chapter_access, delivery_identity, verify_chapter_receipts,
)

__all__ = [
    'BodyAccess', 'DeliveryOutcome',
    'assemble_delivery', 'build_delivery_manifest', 'chapter_access',
    'delivery_identity', 'verify_chapter_receipts',
]
