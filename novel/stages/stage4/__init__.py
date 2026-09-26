#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""novel.stages.stage4 —— STR-06 新链 Stage4：目标故事设计与确定性编译。

【本地块】
  · `design.py`    BodyDesign 组装（`str06.stage4.body_design`）。
  · `grounding.py` Preserve／ReconstructionBrief 投影／Bind（`str06.stage4.preserve`、
                   `str06.stage4.bind`）。
  · `compile.py`   ExecutionPlan 确定性编译（`str06.stage4.compile`）。
  · `guard.py`     写前 M1–M6 机械闸（只读验证、只写报告出口）。
  · `fold.py`      单一目标 Fold：accepted prefix 的 planned_delta 重放。
  · `book_ir.py`   WriterBookIR 确定性编译（章预算 largest-remainder 分配）。

⚠ Hook 面不住本包：Hook 设计（`hook_design`）与 HookContext 编译在 F8c 分流时
落进 `novel/stages/stage5/`（`hook.py`／`hook_context.py`），与 Hook 成文
（`hook_writer.py`）同包——「Hook 是一个面」按模块聚拢，不按节点所属 Stage 拆开。

【包级公开面】跨 stage 只许 `from novel.stages.stage4 import X`；深路径 import
本包内部模块一律禁止（守卫见
`tests/test_layering.py::test_cross_stage_imports_stay_on_the_package_surface`）。
"""
from __future__ import annotations

from novel.stages.stage4.book_ir import (
    allocate_chapter_budgets, chapter_scene_weights, compile_writer_book_ir,
)
from novel.stages.stage4.compile import compile_execution_plan, init_establishments
from novel.stages.stage4.design import assemble_body_design
from novel.stages.stage4.fold import FoldedState, fold_accepted_prefix
from novel.stages.stage4.grounding import (
    assemble_binding, assemble_preserve, project_brief,
)
from novel.stages.stage4.guard import (
    appeared_chars, check_binding_beat_refs, check_cast_legality,
    check_entry_timing, check_obligation_coverage, run_mechanical_guard,
    scene_order,
)

__all__ = [
    'FoldedState', 'allocate_chapter_budgets', 'appeared_chars',
    'assemble_binding', 'assemble_body_design',
    'assemble_preserve', 'chapter_scene_weights', 'check_binding_beat_refs',
    'check_cast_legality', 'check_entry_timing', 'check_obligation_coverage',
    'compile_execution_plan', 'compile_writer_book_ir', 'fold_accepted_prefix',
    'init_establishments', 'project_brief',
    'run_mechanical_guard', 'scene_order',
]
