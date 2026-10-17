"""材料失败的纯归因：正式来源与目标位置决定责任，不读盘、不改产物、不解析错误文案。"""
from __future__ import annotations

from dataclasses import asdict, dataclass
from typing import Iterable

from contracts.schema.design import BodyDesign
from contracts.schema.grounding import ReconstructionBrief, SemanticBinding
from contracts.schema.source import NameStructure
from contracts.schema.workflow import OwnerIssueV1, route_owner_issue
from novel.core.material_trace import MaterialIssue, MaterialSource
from novel.core.name_materials import expand_surface_releases
from novel.stages.common import claim_spans, uncovered_occurrences


_NAME_TEXT_CODES = frozenset({'name_text_syntax', 'name_text_reference', 'name_text_unclassified'})


@dataclass(frozen=True)
class MaterialRepair:
    action: str
    owner: str
    node: str
    issue: OwnerIssueV1


def _release_positions(body: BodyDesign, literal: str,
                       structure: NameStructure | None) -> tuple[str, ...]:
    positions: list[str] = []

    def released(rows) -> bool:
        releases: dict[str, set[str]] = {}
        for row in rows:
            releases.setdefault(row.char_id, set()).update(row.surfaces)
        return any(literal in faces for faces in expand_surface_releases(releases, structure).values())

    if released(body.initial_surfaces):
        positions.append('init')
    for scene in body.scenes:
        for beat in scene.beats:
            if released(beat.surface_introductions):
                positions.append(f'{scene.scene_id}#{beat.beat_id}')
    return tuple(positions)


def _design_source_matches(source: MaterialSource, body: BodyDesign, literal: str) -> bool:
    """仅按已登记字段取值核对来源；复合投影不能用任意相似文本冒充正式选择。"""
    if source.ref == 'book':
        if source.field == 'book_style':
            return source.text == body.book_style
        if source.field.startswith('world_rules/'):
            index = source.field.removeprefix('world_rules/')
            return index.isdigit() and int(index) < len(body.world_rules) and source.text == body.world_rules[int(index)]
    if source.field == 'voice':
        return any(v.char_id == source.ref and v.voice == source.text for v in body.character_voices)
    for chapter in body.chapters:
        if source.ref == f'chapter:{chapter.chapter_ordinal}':
            value = chapter.purpose if source.field == 'purpose' else (
                chapter.entry.carryover if source.field == 'entry.carryover' else None)
            return source.text == value
    for scene in body.scenes:
        if scene.scene_id == source.ref:
            direct = {'realization_brief': scene.realization_brief, 'delivery.voice': scene.delivery.voice,
                      'driver.turn': scene.driver.turn, 'final_beat_event': scene.beats[-1].event}
            if source.field in direct:
                return source.text == direct[source.field]
            if source.field == 'driver':
                d = scene.driver
                suffix = f' 想「{d.immediate_want}」，受阻于「{d.opposition}」，转折：{d.turn}'
                return source.text.endswith(suffix) and any(literal in s for s in (d.immediate_want, d.opposition, d.turn))
            if source.field.startswith('delivery_hints/'):
                return any(source.text == b.delivery_hint for b in scene.beats)
        for beat in scene.beats:
            if source.ref == f'{scene.scene_id}#{beat.beat_id}':
                if source.field in {'event', 'entry.anchor'}:
                    return source.text == beat.event
                if source.field.startswith('target_delta/'):
                    index = source.field.removeprefix('target_delta/')
                    return index.isdigit() and int(index) < len(beat.target_delta) and source.text == beat.target_delta[int(index)].value
                # 读取／背景指向正式建立拍；只验证值，不决定读取时点。
                if source.field.startswith(('reads/', 'entry_state/', 'background_state/')):
                    return any(source.text == d.value for d in beat.target_delta)
            for thread in beat.threads:
                if source.ref == thread.ref and source.field in {'text', 'progress', 'resolution'}:
                    if source.text == getattr(thread, source.field):
                        return True
    if source.ref == 'init' and source.field.startswith(('reads/', 'entry_state/', 'background_state/')):
        return any(source.text == d.value for d in body.initial_state)
    return False


def _hook_source_matches(source: MaterialSource, body: BodyDesign) -> bool:
    hook = getattr(body, 'hook', None)
    if hook is None:
        return False
    if source.ref == 'hook' and source.field == 'local_focus':
        return source.text == getattr(hook, source.field)
    if source.ref == 'hook' and source.field == 'reader_expectation.focus':
        return source.text == hook.reader_expectation.focus
    if source.field.startswith('materials/'):
        parts = source.field.split('/')
        if len(parts) != 3 or not parts[1].isdigit() or int(parts[1]) >= len(hook.materials):
            return False
        item = hook.materials[int(parts[1])]
        if item.beat_ref != source.ref:
            return False
        if parts[2] == 'presentation':
            return item.presentation == source.text
        if parts[2] == 'event' and item.presentation is None:
            position = source.ref.split('#', 1)
            beat = body.beat(*position) if len(position) == 2 else None
            return beat is not None and beat.event == source.text
    # 起笔事实及delta为复合投影；没有原字段证明时停，不向Hook派未知问题。
    return False


def _source_owner(source: MaterialSource, *, brief: ReconstructionBrief, literal: str,
                  body: BodyDesign, binding: SemanticBinding) -> tuple[str, str]:
    if source.kind == 'knowledge':
        proposition = next((p for p in brief.propositions if p.kp_id == source.ref), None)
        if proposition is None or proposition.statement != source.text:
            return '', '命题来源缺失或投影与正式Brief不一致'
        if source.field.startswith('target_delta/'):
            parts = source.position.split('#', 1)
            beat = body.beat(*parts) if len(parts) == 2 else None
            if beat is None or not any(d.key == source.ref for d in beat.target_delta):
                return '', '实际材料的认知拍位与正式Design不一致，须修复确定性投影'
        return 'design', '认知know拍位／读取位置与称谓释放须一致；源命题保持不变'
    if source.kind == 'obligation':
        obligation = next((o for o in binding.obligations if o.obligation_id == source.ref), None)
        if obligation is not None and source.field.startswith('details/'):
            detail_id = source.field.removeprefix('details/')
            if any(d.detail_id == detail_id and d.text == source.text for d in obligation.details):
                return 'preserve', '仅修订可改写细节的指称，保留其源锚点和必要事实条件'
            return '', '细节来源缺失或投影与正式binding不一致'
        if obligation is None or source.field not in ('statement', 'satisfaction') \
                or getattr(obligation, source.field) != source.text:
            return '', '义务来源缺失或投影与正式binding不一致'
        return 'preserve', '仅修订义务statement／satisfaction中的可用指称，保留anchors和原义务语义'
    if source.kind == 'expression':
        decision = next((item for item in body.expression_decisions if item.ex_id == source.ref), None)
        if decision is None or decision.statement != source.text:
            return '', '表达来源缺失或投影与正式Design不一致'
        return 'design', '通过expression_overrides及目标释放选择闭合；默认keep不会随场景改写改变'
    if source.kind == 'design':
        if not _design_source_matches(source, body, literal):
            return '', '材料来源与正式Design字段未闭合，须核验确定性投影'
        return 'design', '修订Design自有字段或其目标消费／释放选择，保留必保及认知约束'
    if source.kind == 'hook':
        if not _hook_source_matches(source, body):
            return '', '材料来源与正式Hook字段未闭合，须核验确定性投影'
        if source.field.endswith('/event'):
            return 'hook_design', '所选正文事件只读；仅修订Hook选材、presentation或释放，不得改写正文事件'
        return 'hook_design', '修订Hook自有呈现／材料选择与Hook称谓释放，正文设计不可被暗改'
    if source.kind == 'world':
        return '', '继承世界约束与全书正向投影冲突，不能购买Design去改只读约束'
    return '', '确定性投影来源尚未证明，须定位代码而非重购语义输出'


def _released_char_faces(body: BodyDesign, structure: NameStructure | None,
                         ) -> dict[str, tuple[str, ...]]:
    """全书（含 initial）按人物累积的已释放称谓——「该人物是否曾有显示面」的判定面。"""
    releases: dict[str, set[str]] = {}
    for row in body.initial_surfaces:
        releases.setdefault(row.char_id, set()).update(row.surfaces)
    for scene in body.scenes:
        for beat in scene.beats:
            for row in beat.surface_introductions:
                releases.setdefault(row.char_id, set()).update(row.surfaces)
    return expand_surface_releases(releases, structure)


def _no_display_anywhere(literal: str, body: BodyDesign,
                         structure: NameStructure | None) -> bool:
    """已绑定称谓的人物在**全书任何位置**都没有显示面（RC-60 残余归属表第一行）。

    无释放且无回落：该人物全部登记称谓都含姓名部件（无纯称谓可回落）——
    唯一修复面是 Design 的释放声明（补一行释放或挪承载位），与文本来源无关。
    归属证明与 `withheld_name_bindings` 同一把尺：字面唯一绑定某一人物。
    """
    if structure is None:
        return False
    owners: dict[str, set[str]] = {}
    for char_id, person in structure.people.items():
        for surface in person.surface_forms:
            owners.setdefault(surface, set()).add(char_id)
    who = owners.get(literal, set())
    if len(who) != 1:
        return False
    char_id = next(iter(who))
    person = structure.people[char_id]
    if any(form is None for form in person.surface_forms.values()):
        return False    # 有纯称谓：显示面回落存在（RC-60 第 3 步）
    released = _released_char_faces(body, structure)
    return char_id not in released


def plan_material_repairs(
    issues: Iterable[MaterialIssue], *, body: BodyDesign, binding: SemanticBinding,
    brief: ReconstructionBrief, structure: NameStructure | None = None,
) -> tuple[MaterialRepair, ...]:
    """一条结果保留全部命中来源；缺失／不同owner多义／未登记均停止。"""
    result: list[MaterialRepair] = []
    for problem in issues:
        explanations = [_source_owner(source, brief=brief, body=body, binding=binding, literal=problem.literal)
                        for source in problem.sources]
        identity_error = problem.code in _NAME_TEXT_CODES
        if identity_error:
            explanations = [('', '只读源命题或派生投影的身份声明不闭合，目标owner无权改写；须核验原来源')
                            if source.kind in {'knowledge', 'world', 'projection'} else explanation
                            for source, explanation in zip(problem.sources, explanations)]
        # CS-H H3（RC-60 残余归属表第一行）：已绑定称谓但该人物全书无显示面
        # ——补释放／挪承载位归 Design，先于按文本来源的归因（唯一修复面是
        # Design 的释放声明，改义务或 Hook 文本都修不了「没有可指称的面」）。
        # 无显示面只能解释已证明来自正式字段的提及；模板／无来源／过期投影
        # 即使碰巧含同一字面，也不能借全书名称结构获得一个付费修订 owner。
        if problem.code == 'positive_material_leak' and explanations \
                and all(owner for owner, _reason in explanations) \
                and _no_display_anywhere(problem.literal, body, structure):
            positions = _release_positions(body, problem.literal, structure)
            fields = sorted({f'{s.ref}/{s.field}' for s in problem.sources})
            issue = OwnerIssueV1(
                code='material_name_leak',
                item=f'chapter:{problem.chapter_ordinal}' if problem.chapter_ordinal else 'hook',
                message=(f'{problem.literal!r} 已绑定人物全书没有任何显示面（无释放且无回落'
                         f'纯称谓）；字段={fields or "未决"}；须由 Design 补一行释放或挪'
                         f'承载位，目标释放位置={positions or "未声明"}'),
                context={'literal': problem.literal, 'where': problem.where,
                         'chapter_ordinal': problem.chapter_ordinal,
                         'no_display_surface': True,
                         'target_release_positions': list(positions),
                         'material_identity': problem.identity})
            action, target = route_owner_issue('material_name_leak', material_owner='design')
            result.append(MaterialRepair(action, 'design', target, issue))
            continue
        owners = {owner for owner, _ in explanations}
        owner = next(iter(owners)) if len(owners) == 1 and '' not in owners else ''
        internal_id = problem.code == 'internal_id_leak'
        if owner == 'design' and structure is not None and not internal_id:
            all_released = expand_surface_releases(
                {cid: set(person.surface_forms) for cid, person in structure.people.items()}, structure)
            faces = {face for values in all_released.values() for face in values}
            if any(source.kind == 'knowledge' and uncovered_occurrences(
                    source.text, [problem.literal], claim_spans(source.text, faces))
                   for source in problem.sources):
                owner = ''
                explanations.append(('', '源命题即使允许全部登记称谓仍有未认领字面；'
                                          '不能靠目标释放修复，须核验源证据与名称观察'))
        # 内部句柄同样只按正式来源归因：owner 把代号写进自有字段（来源已逐字核对）
        # 即归该 owner 改写；出自源命题的代号目标设计无权改，没有来源证明的是渲染期
        # 引入——这两种都停，不购买修不了它的语义输出。
        if internal_id and any(source.kind == 'knowledge' for source in problem.sources):
            owner = ''
            explanations.append(('', '内部句柄出自源命题文本，目标设计无权改写；须核验上游观察'))
        if problem.code not in {'positive_material_leak', 'internal_id_leak', *_NAME_TEXT_CODES}:
            owner = ''
        action, target = route_owner_issue('material_name_leak', material_owner=owner)
        positions = _release_positions(body, problem.literal, structure)
        fields = sorted({f'{s.ref}/{s.field}' for s in problem.sources})
        reasons = '；'.join(dict.fromkeys(reason for _, reason in explanations)) \
            or '正式字段来源缺失，不能从报错位置或源目标同号章推断owner'
        if len(owners) > 1:
            reasons = '命中字面有不同责任方的多义来源；' + reasons
        context = {
            'literal': problem.literal, 'where': problem.where,
            'chapter_ordinal': problem.chapter_ordinal,
            'sources': [asdict(source) for source in problem.sources],
            'target_release_positions': list(positions),
            'material_identity': problem.identity,
            'validation_code': problem.code,
        }
        # 反馈只含目标坐标／源盲句柄；来源诊断不把源拍证据送进Design prompt。
        label = f'目标第{problem.chapter_ordinal}章' if problem.chapter_ordinal else 'Hook'
        if identity_error:
            message = (f'{label}材料身份文本声明未闭合（{problem.code}）；'
                       f'字段={fields or "未决"}。{problem.reason or problem.message}；{reasons}')
        elif internal_id:
            message = (f'{label}材料含内部句柄 {problem.literal!r}（代号不是可读文字，'
                       f'须改写为自然语言指称）；字段={fields or "未决"}。{reasons}')
        else:
            message = (f'{label}材料含未释放字面 {problem.literal!r}；'
                       f'字段={fields or "未决"}；目标释放位置={positions or "未声明"}。{reasons}')
        issue = OwnerIssueV1(code='material_name_leak',
                             item=f'chapter:{problem.chapter_ordinal}' if problem.chapter_ordinal else 'hook',
                             message=message, context=context)
        result.append(MaterialRepair(action, owner, target if action != 'stop' else '', issue))
    return tuple(result)
