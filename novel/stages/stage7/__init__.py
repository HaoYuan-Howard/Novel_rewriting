#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""novel.stages.stage7 —— STR-06 新链 Stage7：命名与成书装配。

【本地块】
  · `naming.py`   NameMap v5 赋值与确定性替换／残留硬闸（`str06.stage7.naming`）。
  · `assembly.py` 改名替换与成书装配：TXT 先落盘、manifest 最后提交
                  （`str06.stage7.assemble`）。

【包级公开面】跨 stage 只许 `from novel.stages.stage7 import X`；深路径 import
本包内部模块一律禁止（守卫见
`tests/test_layering.py::test_cross_stage_imports_stay_on_the_package_surface`）。
本包经 `novel.stages.stage5` 的公开面消费篇幅口径与前文拼接。
"""
from __future__ import annotations

from novel.stages.stage7.assembly import (
    BodyAccess, DeliveryOutcome, assemble_delivery, build_delivery_manifest,
    chapter_access, delivery_identity, verify_chapter_chain,
)
from novel.stages.stage7.naming import (
    NAMING_VALIDATOR_VERSION, apply_names, check_residual, compile_name_map,
    compile_surface_replacements, diff_proof, naming_projection,
)

__all__ = [
    'BodyAccess', 'DeliveryOutcome', 'NAMING_VALIDATOR_VERSION', 'apply_names',
    'assemble_delivery', 'build_delivery_manifest', 'chapter_access',
    'check_residual', 'compile_name_map', 'compile_surface_replacements',
    'delivery_identity', 'diff_proof', 'naming_projection', 'verify_chapter_chain',
]
