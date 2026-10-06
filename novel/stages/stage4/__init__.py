#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""novel.stages.stage4 —— STR-06 新链 Stage4：目标故事设计与确定性编译。

【本地块】
  · `design.py`    BodyDesign 组装与派生面生成（`str06.stage4.body_design`；
                   B3 前半：场景/拍 ID、归章、权重归一与锚定引用由 compiler 生成）。
  · `grounding.py` Preserve／ReconstructionBrief 投影／SemanticBinding 派生
                   （`str06.stage4.preserve`；CS-3 bind 退役后承载位由 design
                   wire 的 real 标注派生，`derive_semantic_binding`）。
  · `hook.py`      Hook Design 的确定性校验臂与唯一 graph publisher
                   （`str06.stage4.hook_design`；B3 后半自 stage5 包归位）。
  · `hook_validation.py` Hook选择的纯结构判据；wire接受与canonical复验共用，
                   validator不依赖producer、compiler或LLM。
  · `hook_context.py` HookContext 确定性投影（`str06.stage4.compile` 的一段；
                   B3 后半自 stage5 包归位）。
  · `compile.py`   ExecutionPlan 确定性编译＋章闭包证明（`str06.stage4.compile`）。
  · `guard.py`     写前 M1–M8 机械闸（M7 声明读取已解析进 IR、M8 线程投影；
                   只读验证、只写报告出口）；a5 全书级锁位置 Guard
                   （`lock_order_violations`，知情升级/读者确认不早于授权
                   承载位——`epistemic_lock_order` 回派面）。
  · `fold.py`      单一目标 Fold：accepted prefix 的 planned_delta 重放；
                   ThreadFold（R2.4）独立折叠拍上的线程操作（状态走叠加折叠，
                   线程走线程折叠，共用 guard.scene_order 的阅读序）。
  · `design_paths.py` Design wire 的局部修订定位（CS-F1 RC-47）：错误在判定处带 wire
                   路径，按最小完整父项／耦合字段合并／替换不重叠发起条目返修。
  · `book_ir.py`   WriterBookIR 确定性编译（章预算 largest-remainder 分配；权重
                   变化的整数预算传导规则 `chapters_with_budget_change`；章级
                   验证材料——称谓许可／禁用字面——compile 时算好，D2）。

Hook 的设计、publisher 与 Context 编译按节点所属 Stage 住本包（B3 后半）；
Hook 成文（`hook_writer.py`）仍在 stage5——语义边界不变（`R-5.6`／`R-6.3`）。

【包级公开面】跨 stage 只许 `from novel.stages.stage4 import X`；深路径 import
本包内部模块一律禁止（守卫见
`tests/test_layering.py::test_cross_stage_imports_stay_on_the_package_surface`）。
"""
from __future__ import annotations

from novel.stages.stage4.book_ir import (
    allocate_chapter_budgets, chapter_scene_weights, chapter_surface_releases,
    chapter_withheld_literals, chapters_with_budget_change, compile_writer_book_ir,
)
from novel.stages.stage4.compile import compile_execution_plan, init_establishments
from novel.stages.stage4.design import assemble_body_design
from novel.stages.stage4.slim import (
    design_patch_legal_values, design_patch_slice, knowledge_change_index,
    project_design_input, project_hook_semantics,
)
from novel.stages.stage4.fold import (
    FoldedState, ThreadFold, fold_accepted_prefix, fold_thread_faces,
)
from novel.stages.stage4.grounding import (
    assemble_preserve, design_obligation_index, derive_semantic_binding,
    fact_lock_subjects, obligation_core_id, preserve_name_issues,
    preserve_patch_slice, project_brief, project_preserve_view,
    preserve_revision_wire,
)
from novel.stages.stage4.preflight import (
    hook_combination_errors, inspect_body_materials, inspect_design_candidate,
)
from novel.stages.stage4.repair import plan_material_repairs
from novel.stages.stage4.guard import (
    appeared_chars, check_binding_beat_refs, check_cast_legality,
    check_entry_timing, check_epistemic_lock_order, check_obligation_coverage,
    check_read_projection, check_thread_lifecycle, check_thread_projection,
    expected_thread_faces, lock_order_violations, run_mechanical_guard,
    scene_order,
)
from novel.stages.stage4.hook import (
    assemble_hook_design, hook_patch_legal_values, hook_patch_slice, publish_graph,
)
from novel.stages.stage4.hook_validation import validate_hook_design
from novel.stages.stage4.hook_context import compile_hook_context
from novel.stages.stage4.slim import design_fact_marks, design_placements
from novel.stages.stage4.design_paths import locate_design_patches, wire_paths_of

__all__ = [
    'inspect_body_materials', 'inspect_design_candidate', 'plan_material_repairs',
    'FoldedState', 'ThreadFold', 'allocate_chapter_budgets', 'appeared_chars',
    'assemble_body_design', 'assemble_hook_design',
    'assemble_preserve', 'chapter_scene_weights', 'chapter_surface_releases',
    'chapter_withheld_literals', 'chapters_with_budget_change',
    'check_binding_beat_refs',
    'check_cast_legality', 'check_entry_timing', 'check_epistemic_lock_order',
    'check_obligation_coverage',
    'check_read_projection', 'check_thread_lifecycle', 'check_thread_projection',
    'compile_execution_plan', 'compile_hook_context', 'compile_writer_book_ir',
    'design_fact_marks', 'design_obligation_index', 'design_patch_legal_values',
    'design_patch_slice', 'design_placements',
    'derive_semantic_binding', 'locate_design_patches', 'wire_paths_of',
    'expected_thread_faces', 'fold_accepted_prefix', 'fold_thread_faces',
    'init_establishments', 'lock_order_violations', 'obligation_core_id',
    'project_brief', 'project_design_input', 'project_hook_semantics', 'project_preserve_view',
    'preserve_name_issues', 'preserve_patch_slice', 'preserve_revision_wire', 'fact_lock_subjects',
    'hook_combination_errors', 'hook_patch_legal_values', 'hook_patch_slice',
    'publish_graph', 'run_mechanical_guard', 'scene_order', 'validate_hook_design',
]
