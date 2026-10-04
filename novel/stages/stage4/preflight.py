"""正文接受、恢复预览与最终编译共用的纯检查；正式输入完整时验证实际阅读材料。"""
from __future__ import annotations

from dataclasses import dataclass

from contracts.schema.book_ir import WriterBookIR
from novel.core.material_trace import MaterialIssue
from novel.stages.common import material_identities, project_chapter_packet, writer_material_issues
from novel.stages.stage4.book_ir import compile_writer_book_ir
from novel.stages.stage4.compile import compile_execution_plan
from novel.stages.stage4.guard import run_mechanical_guard


@dataclass(frozen=True)
class BodyInspection:
    ir: WriterBookIR | None
    errors: tuple[str, ...]
    materials: tuple[MaterialIssue, ...]


def inspect_body_materials(body, binding, brief, chars, *, budget, length_band,
                           book_title='') -> BodyInspection:
    """从相同canonical重建章包；不写盘，不选择修复，不依赖Hook产物。"""
    plan, errors = compile_execution_plan(body, binding)
    if errors:
        return BodyInspection(None, tuple(errors), ())
    ir = compile_writer_book_ir(
        graph=body, plan=plan, binding=binding, total_budget=budget,
        length_band=length_band, book_title=book_title,
        projection=chars.name_projection(), structure=chars.name_structure,
        fact_locks=brief.fact_locks,
        kp_statements={p.kp_id: p.statement for p in brief.propositions},
        world_statements={wc.constraint_id: wc.statement for wc in brief.book_constraints})
    identities = material_identities(body, binding, chars)
    materials = []
    for chapter in ir.chapters:
        materials.extend(writer_material_issues(
            project_chapter_packet(ir, chapter.chapter_ordinal), internal_ids=identities))
        for context in chapter.scenes:
            errors.extend(run_mechanical_guard(binding=binding, context=context, graph=body))
    return BodyInspection(ir, tuple(errors), tuple(materials))
