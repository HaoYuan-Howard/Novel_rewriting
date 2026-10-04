"""正文接受、恢复预览与最终编译共用的纯检查；正式输入完整时验证实际阅读材料。"""
from __future__ import annotations

from dataclasses import asdict, dataclass
from typing import Literal

from pydantic import ValidationError

from contracts.schema.book_ir import WriterBookIR
from contracts.schema.design import BodyDesign, DesignWireV8, STAGE4_DESIGN_WIRE_PROTOCOL
from contracts.schema.grounding import OutputConstraints, SemanticBinding
from novel.core.material_trace import MaterialIssue
from novel.core.text import kp_statement
from novel.core.validation import ValidationIssue, ValidationMessage, validation_identity
from novel.stages.common import (
    material_identities, project_chapter_packet, writer_material_issues,
    writer_name_projections,
)
from novel.stages.stage4.book_ir import compile_writer_book_ir
from novel.stages.stage4.compile import compile_execution_plan
from novel.stages.stage4.design import inspect_body_design
from novel.stages.stage4.grounding import derive_semantic_binding
from novel.stages.stage4.guard import run_mechanical_guard
from novel.stages.stage4.slim import design_fact_marks, design_placements


@dataclass(frozen=True)
class PreflightCheck:
    """派生诊断，不进入领域产物；未运行与通过严格分开。"""

    name: str
    status: Literal['passed', 'failed', 'not_run']
    reason: str = ''


@dataclass(frozen=True)
class BodyInspection:
    ir: WriterBookIR | None
    errors: tuple[str, ...]
    materials: tuple[MaterialIssue, ...]
    checks: tuple[PreflightCheck, ...]
    #: 写手材料里实际发生的称谓投影（逐章留痕；投影是显式步骤，不静默改写）。
    name_projections: tuple[dict, ...] = ()


@dataclass(frozen=True)
class DesignInspection:
    """候选只供诊断；body/binding存在不代表准许发布。"""

    body: BodyDesign | None
    binding: SemanticBinding | None
    errors: tuple[str, ...]
    materials: tuple[MaterialIssue, ...]
    checks: tuple[PreflightCheck, ...]
    name_projections: tuple[dict, ...] = ()

    @property
    def passed(self) -> bool:
        required = tuple(c for c in self.checks if c.name != 'hook_materials')
        return (self.body is not None and self.binding is not None
                and not self.errors and not self.materials
                and all(c.status == 'passed' for c in required))

    def report(self) -> dict:
        """供现有停止上下文和节点结果留证，不另设验收账。"""
        return {'passed': self.passed, 'checks': [asdict(c) for c in self.checks],
                'errors': list(self.errors),
                'material_errors': [m.message for m in self.materials],
                'name_projections': list(self.name_projections)}

    def feedback(self, errors: list[str]) -> list[str]:
        """未运行信息跟随实际问题展示，不作为新错误计数或修订进展。"""
        if not errors:
            return []
        summary = '；'.join(f'{c.name}={c.status}' + (f'({c.reason})' if c.reason else '')
                           for c in self.checks)
        text = f'{errors[0]}\n本候选预检：{summary}'
        if isinstance(errors[0], ValidationIssue):
            # 结构化问题（规则码、稳定位置、wire 路径）整体保留，只有展示文案追加摘要；
            # 局部修订按这些结构化字段定位，不能因拼接展示文案而丢失。
            lead = errors[0]
            first = ValidationIssue(text, code=lead.code, location=lead.location,
                                    owner=lead.owner, scope=lead.scope, context=lead.context)
        else:
            first = ValidationMessage(text, ())
            first.identity = validation_identity(errors[0])
        return [first, *errors[1:]]


def inspect_design_candidate(wire, *, checkpoint, brief, obligations, chars,
                             constraints: OutputConstraints, length_band,
                             book_title='') -> DesignInspection:
    """同一候选完成全部可执行检查；不因自有文字错误遮住只读上游材料错误。

    Shape/引用无法构造时仅跳过依赖检查，保留原因；Hook尚未生成，不冒充已验。
    复用现役compiler，不读盘、不回派、不发布，也不判断事实语义是否正确。
    """
    body, errors = inspect_body_design(
        wire, known_char_ids=set(chars.characters), constraints=constraints,
        name_projection=chars.name_projection(), name_structure=chars.name_structure,
        expression_requirements=brief.expression, source_narration=brief.source_narration,
        registered_surfaces={cid: {s.surface for s in c.surfaces}
                             for cid, c in chars.characters.items()},
        brief=brief, obligations=obligations)
    checks = [PreflightCheck('design', 'failed' if errors else 'passed')]
    binding = None
    # placements与事实锁标注只接受现役wire；即使Design构造失败也独立检查标注形状。
    placements, marks, placement_errors = _design_wire_projections(wire)
    # 现役wire同一份Pydantic形状错误已经由Design完整报告，不再复制一遍。
    shared_shape_failure = (body is None and placement_errors
                            and wire.get('protocol_version') == STAGE4_DESIGN_WIRE_PROTOCOL)
    if not shared_shape_failure:
        errors.extend(placement_errors)
    checks.append(PreflightCheck('placements', 'failed' if placement_errors else 'passed',
                                 '同一wire形状错误见Design报告' if shared_shape_failure else ''))
    if body is not None and placements is not None:
        binding, binding_errors = derive_semantic_binding(placements, body, checkpoint, marks)
        errors.extend(binding_errors)
        checks.append(PreflightCheck('binding', 'failed' if binding_errors else 'passed'))
    else:
        checks.append(PreflightCheck('binding', 'not_run', 'Design或承载标注无法构造'))
    materials = ()
    projections: tuple[dict, ...] = ()
    if body is not None and binding is not None:
        view = inspect_body_materials(body, binding, brief, chars,
                                      budget=constraints.total_length_budget,
                                      length_band=length_band, book_title=book_title)
        errors.extend(view.errors)
        materials = view.materials
        projections = view.name_projections
        checks.extend(view.checks)
    else:
        checks.extend(PreflightCheck(name, 'not_run', '缺少可构造的Design或Binding')
                      for name in ('execution_plan', 'writer_inputs', 'writer_materials', 'mechanical_guard'))
    checks.append(PreflightCheck('hook_materials', 'not_run', 'Hook尚未生成，由Hook节点验收'))
    return DesignInspection(body, binding, tuple(dict.fromkeys(errors)), materials,
                            tuple(checks), projections)


def _design_wire_projections(wire) -> tuple[dict | None, tuple, list[str]]:
    """现役wire的机械投影：real承载位与事实锁标注；旧wire不补造任一标注。"""
    if wire.get('protocol_version') != STAGE4_DESIGN_WIRE_PROTOCOL:
        return None, (), [f'design wire 必须是 {STAGE4_DESIGN_WIRE_PROTOCOL}'
                          f'（实得 {wire.get("protocol_version")!r}）——旧wire不补造'
                          'real承载标注与事实锁引用']
    try:
        model = DesignWireV8.model_validate(wire)
    except ValidationError as exc:
        return None, (), [f'design 瘦 wire 协议校验失败：{exc}']
    return design_placements(model), design_fact_marks(model), []


def inspect_body_materials(body, binding, brief, chars, *, budget, length_band,
                           book_title='') -> BodyInspection:
    """从相同canonical重建章包；不写盘，不选择修复，不依赖Hook产物。"""
    plan, errors = compile_execution_plan(body, binding)
    # 可构造的Design可能仍有悬空命题。复用渲染器同一引用判据，先证明IR输入
    # 可解析；不把局部Design错误变成project_context的裸异常或派生连锁噪声。
    propositions = {p.kp_id: p.statement for p in brief.propositions}
    keys = {d.key for d in body.initial_state}
    for scene in body.scenes:
        keys.update(d.key for d in scene.state_reads)
        keys.update(d.key for beat in scene.beats for d in beat.target_delta)
    input_errors = []
    for key in sorted(keys):
        try:
            kp_statement(propositions, key)
        except ValueError as exc:
            input_errors.append(str(exc))
    checks = (PreflightCheck('execution_plan', 'failed' if errors else 'passed'),
              PreflightCheck('writer_inputs', 'failed' if input_errors else 'passed'))
    if errors or input_errors:
        reason = '；'.join(name for name, failed in (
            ('ExecutionPlan编译失败', bool(errors)), ('Writer命题引用未闭合', bool(input_errors))) if failed)
        errors.extend(input_errors)
        return BodyInspection(None, tuple(errors), (), (
            *checks, PreflightCheck('writer_materials', 'not_run', reason),
            PreflightCheck('mechanical_guard', 'not_run', reason)))
    ir = compile_writer_book_ir(
        graph=body, plan=plan, binding=binding, total_budget=budget,
        length_band=length_band, book_title=book_title,
        projection=chars.name_projection(), structure=chars.name_structure,
        fact_locks=brief.fact_locks,
        kp_statements=propositions,
        world_statements={wc.constraint_id: wc.statement for wc in brief.book_constraints})
    identities = material_identities(body, binding, chars)
    materials = []
    projections: list[dict] = []
    for chapter in ir.chapters:
        packet = project_chapter_packet(ir, chapter.chapter_ordinal)
        materials.extend(writer_material_issues(packet, internal_ids=identities))
        projections.extend({'chapter_ordinal': chapter.chapter_ordinal, **row}
                           for row in writer_name_projections(packet))
        for context in chapter.scenes:
            errors.extend(run_mechanical_guard(binding=binding, context=context, graph=body))
    return BodyInspection(ir, tuple(errors), tuple(materials), (
        *checks,
        PreflightCheck('writer_materials', 'failed' if materials else 'passed'),
        PreflightCheck('mechanical_guard', 'failed' if errors else 'passed')),
        tuple(projections))
