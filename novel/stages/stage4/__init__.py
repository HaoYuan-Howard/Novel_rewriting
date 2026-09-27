#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""novel.stages.stage4 —— STR-06 新链 Stage4：目标故事设计与确定性编译。

【本地块】
  · `design.py`    BodyDesign 组装与派生面生成（`str06.stage4.body_design`；
                   B3 前半：场景/拍 ID、归章、权重归一与锚定引用由 compiler 生成）。
  · `grounding.py` Preserve／ReconstructionBrief 投影／Bind 输入投影／Bind
                   （`str06.stage4.preserve`、`str06.stage4.bind`）。
  · `hook.py`      Hook Design 的确定性校验臂与唯一 graph publisher
                   （`str06.stage4.hook_design`；B3 后半自 stage5 包归位）。
  · `hook_context.py` HookContext 确定性投影（`str06.stage4.compile` 的一段；
                   B3 后半自 stage5 包归位）。
  · `compile.py`   ExecutionPlan 确定性编译＋章闭包证明（`str06.stage4.compile`）。
  · `guard.py`     写前 M1–M6 机械闸（只读验证、只写报告出口）。
  · `fold.py`      单一目标 Fold：accepted prefix 的 planned_delta 重放。
  · `book_ir.py`   WriterBookIR 确定性编译（章预算 largest-remainder 分配；权重
                   变化的整数预算传导规则 `chapters_with_budget_change`）。

Hook 的设计、publisher 与 Context 编译按节点所属 Stage 住本包（B3 后半）；
Hook 成文（`hook_writer.py`）仍在 stage5——语义边界不变（`R-5.6`／`R-6.3`）。

【包级公开面】跨 stage 只许 `from novel.stages.stage4 import X`；深路径 import
本包内部模块一律禁止（守卫见
`tests/test_layering.py::test_cross_stage_imports_stay_on_the_package_surface`）。
"""
from __future__ import annotations

from novel.stages.stage4.book_ir import (
    allocate_chapter_budgets, chapter_scene_weights, chapters_with_budget_change,
    compile_writer_book_ir,
)
from novel.stages.stage4.compile import compile_execution_plan, init_establishments
from novel.stages.stage4.design import assemble_body_design
from novel.stages.stage4.fold import FoldedState, fold_accepted_prefix
from novel.stages.stage4.grounding import (
    assemble_binding, assemble_preserve, project_binding_space, project_brief,
)
from novel.stages.stage4.guard import (
    appeared_chars, check_binding_beat_refs, check_cast_legality,
    check_entry_timing, check_obligation_coverage, run_mechanical_guard,
    scene_order,
)
from novel.stages.stage4.hook import assemble_hook_design, publish_graph
from novel.stages.stage4.hook_context import compile_hook_context

__all__ = [
    'FoldedState', 'allocate_chapter_budgets', 'appeared_chars',
    'assemble_binding', 'assemble_body_design', 'assemble_hook_design',
    'assemble_preserve', 'chapter_scene_weights', 'chapters_with_budget_change',
    'check_binding_beat_refs',
    'check_cast_legality', 'check_entry_timing', 'check_obligation_coverage',
    'compile_execution_plan', 'compile_hook_context', 'compile_writer_book_ir',
    'fold_accepted_prefix',
    'init_establishments', 'project_binding_space', 'project_brief',
    'publish_graph', 'run_mechanical_guard', 'scene_order',
]
