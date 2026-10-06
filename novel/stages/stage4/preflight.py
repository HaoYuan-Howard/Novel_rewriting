"""正文接受、恢复预览与最终编译共用的纯检查；正式输入完整时验证实际阅读材料。"""
from __future__ import annotations

from dataclasses import asdict, dataclass
from typing import Literal, Sequence

from pydantic import ValidationError

from contracts.schema.book_ir import WriterBookIR
from contracts.schema.design import BodyDesign, DesignWireV8, STAGE4_DESIGN_WIRE_PROTOCOL
from contracts.schema.grounding import OutputConstraints, SemanticBinding
from novel.core.material_trace import MaterialIssue
from novel.core.text import kp_statement
from novel.core.validation import (
    ValidationIssue, ValidationMessage, validation_identity,
)
from novel.stages.common import (
    material_identities, project_chapter_packet, writer_material_issues,
    writer_name_projections, validate_hook_materials,
)
from novel.stages.stage4.book_ir import compile_writer_book_ir
from novel.stages.stage4.compile import compile_execution_plan, compile_issue
from novel.stages.stage4.design import inspect_body_design
from novel.stages.stage4.design_paths import design_issue, wire_paths_of
from novel.stages.stage4.grounding import derive_semantic_binding
from novel.stages.stage4.guard import run_mechanical_guard
from novel.stages.stage4.hook import publish_graph
from novel.stages.stage4.hook_context import compile_hook_context
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
        """按根因聚合成任务并附受影响位置（CS-H H2，RC-57/59）。

        同一规则码（＋责任面）的多处命中是**一个任务**：csf4 根 321 行反馈实为
        一个根因，逐行反馈既放大返修输入又不聚焦。聚合后的任务 carrier 仍是
        typed 问题（规则码、稳定位置、可编辑 wire 路径），局部返修的定位与覆盖
        核对照常工作；``wire_paths`` 只在该根因**每一处**发射都可编辑时才给——
        混有不可定位发射面（如 compile 侧同码扫描）的任务不带路径，`locate`
        据此退整份返修，不用局部修复静默丢掉没有编辑位置的那一处。

        任务身份（规则码＋责任面＋受影响位置集合）即无进展判定的尺子：候选字节
        变化（换了内容仍在同一处失败）不算进展，问题集严格缩小是进展。
        未运行信息（checks 摘要）附在首个任务上，随实际问题展示。
        """
        if not errors:
            return []
        summary = '；'.join(f'{c.name}={c.status}' + (f'({c.reason})' if c.reason else '')
                           for c in self.checks)
        return _root_cause_tasks(errors, summary=summary)


def _root_cause_tasks(errors: Sequence[str], *, summary: str = '') -> list[str]:
    """错误列表 → 按根因聚合的任务 carrier（保序；同一根因的多处命中合成一个）。

    typed 问题（规则码＋责任面）分组；``wire_paths`` 只在该根因每一处发射都可
    编辑时才给（混有不可定位发射面的任务不带路径，locate 据此退整份返修）。
    存量字符串与材料归因消息按身份分组，身份与无进展判定同一把尺。``summary``
    （预检摘要）附在首个任务上——未运行信息随实际问题展示，不单独计数。
    """
    groups: dict[tuple, list[ValidationIssue]] = {}
    legacy: list[str] = []
    for error in errors:
        if isinstance(error, ValidationIssue):
            groups.setdefault((error.code, error.owner, error.scope), []).append(error)
        else:
            legacy.append(error)
    tasks: list[str] = []
    for (code, owner, scope), members in groups.items():
        locations = [tuple(m.location) for m in members]
        paths = [p for m in members for p in (wire_paths_of(m) or ())]
        editable = all(wire_paths_of(m) for m in members)
        first = members[0]
        where = '、'.join(_location_text(loc) for loc in locations)
        message = (f'任务 {code}（{owner}）{len(members)} 处：{first}；'
                   f'受影响位置：{where}')
        tasks.append(ValidationIssue(
            message, code=code, location=tuple(sorted(locations, key=repr)),
            owner=owner, scope=scope,
            context={'root_cause': code,
                     'affected_locations': [list(loc) for loc in locations],
                     'wire_paths': [list(p) for p in paths] if editable else []}))
    legacy_groups: dict[str, list[str]] = {}
    for error in legacy:
        legacy_groups.setdefault(validation_identity(error), []).append(error)
    for _identity, members in legacy_groups.items():
        head = members[0]
        text = f'{head}（同类 {len(members)} 处）' if len(members) > 1 else str(head)
        tasks.append(ValidationMessage(text, validation_identity(head)))
    if summary and tasks:
        head = tasks[0]
        if isinstance(head, ValidationIssue):
            tasks[0] = ValidationIssue(
                f'{head}\n本候选预检：{summary}', code=head.code,
                location=head.location, owner=head.owner, scope=head.scope,
                context=head.context)
        else:
            tasks[0] = ValidationMessage(
                f'{head}\n本候选预检：{summary}', validation_identity(head))
    return tasks


def _location_text(location: tuple) -> str:
    """稳定位置的人读形式（`('scene', 3)` → scene:3）。"""
    return ':'.join(str(part) for part in location) if location else '（无位置）'


def hook_combination_errors(hook, *, body, binding, chars, hook_constraints,
                            kp_statements, world_statements) -> list[str]:
    """候选 Hook 与目标正文的组合检查（CS-H H2，RC-57「同法接入」）。

    ``publish_graph`` → HookContext 编译 → 材料泄漏。**只读预览与生产接受循环
    共用本函数**（`novel/stages/review.py::_design_preview` 与
    `novel/stages/chain.py::run_hook_design`）：内部句柄闭集取
    `material_identities` 全集（scene/beat/thread/obligation/ex/kp/kf）——此前
    生产面只用 `chars.characters`，预览抓得到的内部 id 泄漏在生产里会漏网
    （同一行为两把尺；判据 25：判定与预览同一把尺）。
    """
    from novel.stages.common import material_identities
    graph = publish_graph(body, hook)
    context, errors = compile_hook_context(
        graph, chars.name_projection(), hook_constraints, chars.name_structure,
        kp_statements, world_statements)
    if context is None:
        return list(errors)
    return list(errors) + list(validate_hook_materials(
        context, internal_ids=material_identities(graph, binding, chars)))


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
    # compile 面『必保义务未承载』（compile_must_unbound，stop 态）是 design 覆盖闸
    # （design_carrier_missing）同一根因的下游回声：覆盖闸先于它对同一义务触发
    # （未标注的必保义务两面必同报），点名也更精确（缺谁）。回声不重复计入同一
    # 修订轮——否则一个不可定位任务挡住可选目标窄修，把一轮闭集返修打回整份
    # 重写（CS-H H2 判据③；覆盖闸没点名的义务上回声保留，仍按 stop 态整份退回）。
    coverage_missing = {str(part) for error in errors
                        if isinstance(error, ValidationIssue)
                        and error.code == 'design_carrier_missing'
                        for part in (error.location[1]
                                     if len(error.location) > 1
                                     and isinstance(error.location[1], tuple) else ())}
    if coverage_missing:
        errors = [error for error in errors
                  if not (isinstance(error, ValidationIssue)
                          and error.code == 'compile_must_unbound'
                          and len(error.location) > 1
                          and str(error.location[1]) in coverage_missing)]
    return DesignInspection(body, binding, tuple(dict.fromkeys(errors)), materials,
                            tuple(checks), projections)


def _design_wire_projections(wire) -> tuple[dict | None, tuple, list[str]]:
    """现役wire的机械投影：real承载位与事实锁标注；旧wire不补造任一标注。"""
    if wire.get('protocol_version') != STAGE4_DESIGN_WIRE_PROTOCOL:
        return None, (), [design_issue(
            f'design wire 必须是 {STAGE4_DESIGN_WIRE_PROTOCOL}'
            f'（实得 {wire.get("protocol_version")!r}）——旧wire不补造'
            'real承载标注与事实锁引用',
            code='design_protocol_literal', location=('protocol_version',))]
    try:
        model = DesignWireV8.model_validate(wire)
    except ValidationError as exc:
        return None, (), [design_issue(
            f'design 瘦 wire 协议校验失败：{exc}',
            code='design_wire_shape', location=('wire_shape',))]
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
            # compile 面自洽性违例（IR 输入引用不可解析）：没有该键的 wire 行
            # 坐标（键扫描跨拍汇总），按 PATCH_ROUTES 的 stop 态停机报码。
            input_errors.append(compile_issue(
                str(exc), 'design_kp_key_closed', ('kp_key', key)))
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
