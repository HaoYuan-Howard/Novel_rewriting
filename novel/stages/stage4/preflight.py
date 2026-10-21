"""正文接受、恢复预览与最终编译共用的纯检查；正式输入完整时验证实际阅读材料。"""
from __future__ import annotations

from dataclasses import asdict, dataclass
from typing import Any, Literal, Mapping, Sequence

from pydantic import ValidationError

from contracts.schema.book_ir import WriterBookIR
from contracts.schema.design import BodyDesign, DesignWireV9, STAGE4_DESIGN_WIRE_PROTOCOL
from contracts.schema.grounding import OutputConstraints, SemanticBinding
from contracts.schema.hook import HookDesignWire
from novel.core.material_trace import MaterialIssue
from novel.core.name_materials import appellation_fallbacks, target_face_table
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
from novel.stages.stage4.design import _wire_shape_issues, inspect_body_design
from novel.stages.stage4.design_paths import design_issue, wire_paths_of
from novel.stages.stage4.grounding import (
    derive_semantic_binding, preserve_name_issues, required_releases,
)
from novel.stages.stage4.guard import run_mechanical_guard
from novel.stages.stage4.hook import publish_graph
from novel.stages.stage4.hook_context import compile_hook_context
from novel.stages.stage4.slim import (
    design_fact_marks, design_placements, design_wire_model,
    DESIGN_READ_PROTOCOLS, DesignReferenceError,
)


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
        据此拒绝局部计划并按正式路由停止或走登记出口，不静默漏掉那一处。

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
    编辑时才给（混有不可定位发射面时不签发局部计划，由正式路由处置）。
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
        context = {'root_cause': code,
                   'affected_locations': [list(loc) for loc in locations],
                   'wire_paths': [list(p) for p in paths] if editable else []}
        # 展示任务可以聚合；规划仍需要每处错误的类型、原位置和必要替换能力。
        # 不能只留下第一处文案，否则同码的 extra/missing/union 再次被混成一条路径。
        shapes = [dict(entry) for member in members
                  for entry in member.context.get('shape_errors', ())]
        if shapes:
            context['shape_errors'] = shapes
        member_contexts = [{key: value for key, value in member.context.items()
                            if key not in ('wire_paths', 'shape_errors')}
                           for member in members]
        if any(member_contexts):
            context['member_contexts'] = member_contexts
        tasks.append(ValidationIssue(
            message, code=code, location=tuple(sorted(locations, key=repr)),
            owner=owner, scope=scope, context=context))
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


def hook_combination_errors(hook, *, body, binding, chars, name_map,
                            kp_statements, world_statements,
                            total_budget) -> list[str]:
    """候选 Hook 与目标正文的组合检查（CS-H H2，RC-57「同法接入」）。

    ``publish_graph`` → HookContext 编译 → 材料泄漏。**只读预览与生产接受循环
    共用本函数**（`novel/stages/review.py::_design_preview` 与
    `novel/stages/chain.py::run_hook_design`）：内部句柄闭集取
    `material_identities` 全集（scene/beat/thread/obligation/ex/kp/kf）——此前
    生产面只用 `chars.characters`，预览抓得到的内部 id 泄漏在生产里会漏网
    （同一行为两把尺；判据 25：判定与预览同一把尺）。`total_budget`（rc24）
    只进 Context 的篇幅包络派生，两侧同一预算源。
    """
    from novel.stages.common import material_identities
    graph = publish_graph(body, hook)
    context, errors = compile_hook_context(
        graph, chars.name_projection(), chars.name_structure,
        kp_statements, world_statements, characters=chars.core_view(),
        total_budget=total_budget, name_map=name_map)
    if context is None:
        return list(errors)
    return list(errors) + list(validate_hook_materials(
        context, internal_ids=material_identities(graph, binding, chars)))

def hook_candidate_material_errors(wire: dict[str, Any], *, body: BodyDesign, chars,
                                   name_map, binding,
                                   kp_statements: Mapping[str, str] = (),
                                   world_statements: Mapping[str, str] = ()) -> list:
    """结构判据失败时的尽力而为材料预检（2026-10-10 裁决，方向二：一次性聚合）。

    正式组合检查（`hook_combination_errors`）只在结构全绿后运行；同一份首答里
    互不依赖的浅层（结构）与深层（材料泄漏）机械缺陷会被拆到多轮付费里逐层
    暴露——rc23 升版重跑 6/6 耗尽的实证：第 5 轮报两条 stale，补丁修复后第 6
    轮才第一次看到首答里就存在的未释放专名泄漏。本函数对结构失败的候选**只读**
    聚合不依赖失败条目的材料层错误：按候选自选的 surface_releases 推演写手视图
    会看到的起笔事实与材料文本，跑与正式面同一把专名/句柄泄漏尺
    （`positive_material_issues`），错误带 wire 路径供窄返修定位。

    只增反馈信息量，不放宽任何验收判据（I-34 不变：候选照样被拒）；
    不修改输入、不落盘、不调用模型。形状无法解析或条目引用不可解析时跳过
    该条——对应错误已由结构判据报告。
    """
    from novel.core.name_materials import (
        FIRST_PERSON_ROLE_WORD, expand_surface_releases, map_target_faces,
        name_watch_rows, target_face_table, target_old_literals,
        withheld_target_bindings,
    )
    from novel.core.name_residue import watch_literals
    from novel.stages.common import (
        MaterialPart, MaterialSource, material_identities, positive_material_issues,
        project_withheld_names,
    )
    from novel.stages.stage4.hook_facts import hook_facts
    from novel.stages.stage4.hook_context import (
        hook_display_faces, render_hook_fact_text,
    )
    try:
        parsed = HookDesignWire.model_validate(wire)
    except ValidationError:
        return []
    projection = chars.name_projection()
    structure = chars.name_structure
    releases = {r.char_id: list(r.surfaces) for r in parsed.surface_releases}
    # rc26：目标字面表在場時全部目標面（与 compile_hook_context 同一把尺）。
    faces, _face_errors = (target_face_table(structure, name_map)
                           if structure is not None else ({}, []))
    expanded = map_target_faces(faces, expand_surface_releases(releases, structure))
    released = {surface for row in expanded.values() for surface in row}
    concealed = {c for c in parsed.concealed if isinstance(c, str)}
    _names, display_faces = hook_display_faces(
        releases, structure, chars.core_view(), concealed, faces=faces)
    faces_of = {row.char_id: row.surfaces[0] for row in display_faces}
    focalizers = set(parsed.pov.focalizers)
    first_person = parsed.pov.person == 'first'

    def display(char_id: str) -> str:
        if first_person and char_id in focalizers:
            return FIRST_PERSON_ROLE_WORD
        return faces_of.get(char_id, '')

    display_projection = projection.model_copy(update={'by_person': tuple(
        entry.model_copy(update={'display': display(entry.char_id)})
        for entry in projection.by_person)})
    withheld = watch_literals(name_watch_rows(
        structure=structure, faces=faces, released_names=expanded,
        old_literals=(target_old_literals(structure, name_map)
                      if structure is not None else {})))
    bindings = withheld_target_bindings(structure, faces, expanded)

    def project(text: str) -> str:
        projected, _hits = project_withheld_names(
            text, bindings=bindings,
            display={b.char_id: display(b.char_id) for b in bindings})
        return projected

    index = {fact.fact_ref: fact for fact in hook_facts(body)}
    parts: dict[str, MaterialPart] = {}
    where_path: dict[str, tuple] = {}
    for k, item in enumerate(parsed.opening_facts):
        fact = index.get(item.fact_ref)
        if fact is None:
            continue
        try:
            text = render_hook_fact_text(display_projection, fact.delta.address(),
                                         fact.delta, kp_statements)
        except ValueError:
            continue
        where = f'hook/opening_facts/{k}'
        parts[where] = MaterialPart(project(text), (
            MaterialSource('hook', fact.ref, f'opening_facts/{k}', text),))
        where_path[where] = ('opening_facts', k)
    for k, item in enumerate(parsed.materials):
        ref = item.beat_ref
        beat = body.beat(ref.scene, ref.beat)
        if beat is None:
            continue
        hidden = {index[h].delta.address() for h in item.withheld if h in index}
        if item.presentation:
            lines = [item.presentation]
        else:
            lines = [beat.event] if beat.event else []
            for delta in beat.target_delta:
                if delta.address() in hidden or delta.op != 'set':
                    continue
                try:
                    lines.append(render_hook_fact_text(display_projection, delta.address(),
                                                       delta, kp_statements))
                except ValueError:
                    continue
        where = f'hook/materials/{k}'
        parts[where] = MaterialPart(project('\n'.join(lines)), (
            MaterialSource('hook', f'{ref.scene}#{ref.beat}',
                           f'materials/{k}', '\n'.join(lines)),))
        where_path[where] = ('materials', k)
    if not parts:
        return []
    issues = positive_material_issues(
        parts, released=released, withheld=withheld,
        internal_ids=material_identities(body, binding, chars))
    errors = []
    for issue in issues:
        path = where_path.get(issue.where)
        if path is None:
            continue
        code = ('hook_design_material_exposure' if issue.code in
                {'positive_material_leak', 'name_text_reference'}
                else 'hook_design_internal_exposure')
        errors.append(design_issue(
            f'（材料预检）{issue.message}', code=code, location=path, paths=(path,)))
    return errors


def inspect_design_candidate(wire, *, checkpoint, brief, obligations, chars,
                             name_map, constraints: OutputConstraints, length_band,
                             book_title='') -> DesignInspection:
    """同一候选完成全部可执行检查；不因自有文字错误遮住只读上游材料错误。

    Shape/引用无法构造时仅跳过依赖检查，保留原因；Hook尚未生成，不冒充已验。
    复用现役compiler，不读盘、不回派、不发布，也不判断事实语义是否正确。
    """
    # rc26（命名前移批 C）：目标字面表——Design 材料闸与 IR/Hook 编译同源。
    faces, _face_errors = (target_face_table(chars.name_structure, name_map)
                           if chars.name_structure is not None else ({}, []))
    body, errors = inspect_body_design(
        wire, known_char_ids=set(chars.characters), constraints=constraints,
        name_projection=chars.name_projection(), name_structure=chars.name_structure,
        expression_requirements=brief.expression, source_narration=brief.source_narration,
        registered_surfaces={cid: {s.surface for s in c.surfaces}
                             for cid, c in chars.characters.items()},
        brief=brief, obligations=obligations, characters=chars.core_view(),
        faces=faces, name_map=name_map)
    checks = [PreflightCheck('design', 'failed' if errors else 'passed')]
    preserve_errors = preserve_name_issues(checkpoint, chars, name_map)
    errors = list(preserve_errors) + errors
    checks.append(PreflightCheck('preserve_input', 'failed' if preserve_errors else 'passed'))
    binding = None
    # placements与事实锁标注只接受现役wire；即使Design构造失败也独立检查标注形状。
    placements, marks, placement_errors = _design_wire_projections(wire, brief)
    # 现役wire同一份Pydantic形状错误已经由Design完整报告，不再复制一遍。
    shared_shape_failure = (body is None and placement_errors
                            and wire.get('protocol_version') in DESIGN_READ_PROTOCOLS)
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
                                      length_band=length_band, book_title=book_title,
                                      name_map=name_map)
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
    # 相同正式引用/目标时序在 Design 与免费 compile 同验；候选入口保留 Design
    # 的真实编辑位置，去掉同码同位置的 compile 回声。独立正式重编译仍完整报错。
    echoed_codes = {
        'design_detail_ref', 'series_summary_early', 'series_summary_unbound',
        'design_confirm_unestablished', 'design_read_anchor',
        'design_read_unestablished', 'design_read_unknown',
    }
    design_roots = {(error.code, tuple(error.location)) for error in errors
                    if isinstance(error, ValidationIssue) and error.owner == 'design'
                    and error.code in echoed_codes}
    errors = [error for error in errors
              if not (isinstance(error, ValidationIssue) and error.owner == 'compile'
                      and (error.code, tuple(error.location)) in design_roots)]
    # ValidationIssue 是 str carrier；相同措辞可能位于不同字段。按结构身份去重，
    # 否则同一候选的第二个错误被遮住，局部返修只能逐轮发现另一个位置。
    distinct_errors: dict[str, str] = {}
    for error in errors:
        distinct_errors.setdefault(validation_identity(error), error)
    return DesignInspection(body, binding, tuple(distinct_errors.values()), materials,
                            tuple(checks), projections)


def _design_wire_projections(wire, brief) -> tuple[dict | None, tuple, list[str]]:
    """现役wire的机械投影：real承载位与事实锁标注；旧wire不补造任一标注。"""
    if wire.get('protocol_version') not in DESIGN_READ_PROTOCOLS:
        return None, (), [design_issue(
            f'design wire 必须是 {STAGE4_DESIGN_WIRE_PROTOCOL}'
            f'（实得 {wire.get("protocol_version")!r}）——旧wire不补造'
            'real承载标注与事实锁引用',
            code='design_protocol_literal', location=('protocol_version',))]
    try:
        model = design_wire_model(wire, brief)
    except DesignReferenceError as exc:
        return None, (), [design_issue(str(exc), code='design_wire_expand',
                                      location=exc.path, paths=(exc.path,))]
    except ValidationError as exc:
        return None, (), _wire_shape_issues(exc, 'design 瘦 wire 协议校验失败', wire=wire)
    return design_placements(model), design_fact_marks(model), []


def inspect_body_materials(body, binding, brief, chars, *, budget, length_band,
                           book_title='', name_map) -> BodyInspection:
    """从相同canonical重建章包；不写盘，不选择修复，不依赖Hook产物。

    rc26（命名前移批 C）：`name_map`（NameMapV6）是章包称谓材料的目标字面权威，
    必填——缺它 compile 只能给出源侧旧口径，静默降级比显式失败更糟。
    """
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
        display_fallback=appellation_fallbacks(chars.core_view(), chars.name_structure),
        required_releases=required_releases(brief),
        kp_statements=propositions,
        world_statements={wc.constraint_id: wc.statement for wc in brief.book_constraints},
        name_map=name_map)
    identities = material_identities(body, binding, chars)
    materials = []
    projections: list[dict] = []
    # rc26：M5 的释放期望与章包同一面——目标字面表在場时 guard 也按目标面比对。
    faces, _face_errors = (target_face_table(chars.name_structure, name_map)
                           if chars.name_structure is not None else ({}, []))
    for chapter in ir.chapters:
        packet = project_chapter_packet(ir, chapter.chapter_ordinal)
        materials.extend(writer_material_issues(packet, internal_ids=identities))
        projections.extend({'chapter_ordinal': chapter.chapter_ordinal, **row}
                           for row in writer_name_projections(packet))
        for context in chapter.scenes:
            errors.extend(run_mechanical_guard(binding=binding, context=context,
                                              graph=body, faces=faces))
    return BodyInspection(ir, tuple(errors), tuple(materials), (
        *checks,
        PreflightCheck('writer_materials', 'failed' if materials else 'passed'),
        PreflightCheck('mechanical_guard', 'failed' if errors else 'passed')),
        tuple(projections))
