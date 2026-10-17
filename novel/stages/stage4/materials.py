"""Design 作者文本与正式写作材料的验证；不改写、不做语义代猜。

v11 作者用身份引用/普通义片段声明每次候选出现；原始 wire 的独立检查不依赖
canonical 构造成功。候选未声明只报 name_text_unclassified，绝不据裸姓名部件
宣布发生泄漏。canonical 保留原标记，材料检查按明确人物投影显示面，普通义
区间不参与姓名守卫；内部句柄仍必须独立拒收。旧未标记材料只查完整登记称谓。

CS-F1（RC-46/47）：每条泄漏问题在判定处带上**站点**（Design 哪个字段产生了这段文本），
调用方（`design.py`）按自己手里的 v8 wire 把站点换成可编辑的 JSON 路径，局部修订据此
只重写真正出问题的最小完整父项；本模块不读 wire、不猜路径、不解析错误文案。

CS-H H3（RC-60「投影」「残余归属」）：**先投影再查**——每个分区文本按本章显示面
把已绑定称谓逐次确定性投影（同位语折叠与正文／Hook 同一把尺），再对投影
后的文本查残余。

**已释放按章判（2026-10-07 用户裁定，取代 H3 的按位置口径）**：写手拿到整章章包，
正文最终闸（`stage5/body_checks.py`）也只按章判已释放称谓，章内谁先谁后不检查——
Design 的中间文本不该比它要指导的正文更严。章内后释放的人名（如拍 1 的事件提到
拍 2 才介绍的人物）不再算泄漏；本章及之前都没释放过的名字仍是泄漏（真正的提前
披露）。已释放集与绑定表共用 `chapter_surface_releases`（与正文章包同一函数），
闸与绑定表由构造一致。必保揭名（`design_requires_released`，按拍位）是独立的情节
保真闸，不受影响。残余两态归因（规格「投影后的残余归属」表）：

· `design_unreleased_reference`——已绑定称谓，但该人物在此处没有显示面
  （本章末仍无释放且无回落纯称谓）：补一行释放或挪承载位／改写该字段指称；
· `positive_material_leak`——未释放的完整登记称谓残余：改该字段或其合法释放。

两类都带路径（locate）；义务文本里的同类残余归 Preserve（自闸随 H3d 前置），
不在本模块的 Design 分区出现。
"""
from __future__ import annotations

from typing import Any, Callable, Iterable, Mapping

from contracts.schema.design import BodyDesign
from contracts.schema.source import CharactersCore, NameProjection, NameStructure
from novel.core.material_trace import MaterialPart
from novel.core.name_materials import (
    appellation_fallbacks, display_surface, expand_surface_releases,
    material_name_permissions, name_candidate_literals, withheld_name_bindings,
)
from novel.core.name_text import resolve_name_text
from novel.stages.common import (
    ReleasePrefixes, positive_material_issues, project_withheld_names,
    withheld_name_literals,
)
from novel.stages.stage4.book_ir import chapter_name_releases, chapter_surface_releases
from novel.stages.stage4.design_paths import design_issue

#: 站点：Design 字段的稳定坐标（不含 wire 结构）。第一位是种类，其余是该种类的坐标。
Site = tuple

#: 站点 → wire 路径的解析器；返回 None 表示该字段不是 Design 可编辑的（派生自上游）。
SiteResolver = Callable[[Site], 'tuple | None']


def _text_fields(value: Any, fields: Iterable[str], path: tuple):
    if isinstance(value, dict):
        for field in fields:
            text = value.get(field)
            if isinstance(text, str):
                yield (*path, field), text


def _rows(value: Any) -> tuple | list:
    return value if isinstance(value, (list, tuple)) else ()


def design_wire_text_fields(wire: Mapping[str, Any]):
    """明确列出 Design 作者的自然语言；不遍历 ID、状态键、引用或登记称谓。"""
    yield from _text_fields(wire, ('book_style',), ())
    for field, texts in (('character_voices', ('voice',)),
                         ('expression_overrides', ('reason', 'target_statement')),
                         ('initial_state', ('value',))):
        for i, row in enumerate(_rows(wire.get(field))):
            yield from _text_fields(row, texts, (field, i))
    for si, scene in enumerate(_rows(wire.get('scenes'))):
        if not isinstance(scene, dict):
            continue
        path = ('scenes', si)
        yield from _text_fields(scene, ('brief',), path)
        yield from _text_fields(scene.get('driver'), ('immediate_want', 'opposition', 'turn'),
                                (*path, 'driver'))
        for ai, text in enumerate(_rows(scene.get('avoid'))):
            if isinstance(text, str):
                yield (*path, 'avoid', ai), text
        for bi, beat in enumerate(_rows(scene.get('beats'))):
            if not isinstance(beat, dict):
                continue
            beat_path = (*path, 'beats', bi)
            yield from _text_fields(beat, ('ev',), beat_path)
            for ti, row in enumerate(_rows(beat.get('state'))):
                yield from _text_fields(row, ('val',), (*beat_path, 'state', ti))
            for ti, row in enumerate(_rows(beat.get('threads'))):
                yield from _text_fields(row, ('text', 'progress', 'resolution'),
                                        (*beat_path, 'threads', ti))
    for ci, chapter in enumerate(_rows(wire.get('chapters'))):
        yield from _text_fields(chapter, ('purpose',), ('chapters', ci))
        if isinstance(chapter, dict):
            yield from _text_fields(chapter.get('entry'), ('carryover',),
                                    ('chapters', ci, 'entry'))


def name_text_field_issues(text: str, *, path: tuple,
                           surfaces: Mapping[str, Iterable[str]] | None,
                           candidates: Iterable[str] = ()) -> list:
    """语法/身份引用/词法候选未决分别报码，全部保留当前作者的可编辑路径。"""
    resolved = resolve_name_text(text, surfaces, candidate_literals=candidates)
    return [design_issue(
        str(error), code=error.code, location=(*path, error.start, error.literal),
        paths=(path,), context={'name_text_start': error.start,
                                'name_text_end': error.end,
                                'literal': error.literal}) for error in resolved.errors]


def design_wire_name_text_issues(wire: Mapping[str, Any], *,
                                 projection: NameProjection | None,
                                 structure: NameStructure | None,
                                 registered_surfaces: Mapping[str, Iterable[str]] | None,
                                 require_classification: bool) -> list:
    """自有文本先独立校验；know/real 或 canonical 构造失败不遮住本轮可修问题。"""
    candidates = (name_candidate_literals(projection, structure)
                  if require_classification and projection is not None else ())
    surfaces = (expand_surface_releases(registered_surfaces, structure)
                if registered_surfaces is not None else None)
    return [issue for path, text in design_wire_text_fields(wire)
            for issue in name_text_field_issues(
                text, path=path, surfaces=surfaces, candidates=candidates)]


def design_internal_ids(body: BodyDesign) -> set[str]:
    return {s.scene_id for s in body.scenes} | {
        b.beat_id for s in body.scenes for b in s.beats} | {
        t.ref for s in body.scenes for b in s.beats for t in b.threads}


def validate_design_materials(
    body: BodyDesign, *, projection: NameProjection, known_char_ids: set[str],
    structure: NameStructure | None = None, resolve: SiteResolver | None = None,
    characters: CharactersCore | None = None,
) -> list:
    """按章显示面投影后检查将送达的自然语言；avoid 等负向条目不纳入。

    返回逐条问题（身份与措辞、字符偏移无关）。`resolve` 给出时，问题带可编辑
    wire 路径（局部修订用）；字段不可由 Design 编辑（世界规则、由 pov 派生的
    voice 等）或没有解析器时不带路径，退整份返修。`characters` 只用于显示面
    回落表（RC-60 第 3 步）与聚焦人物角色词；缺席时无回落（更严，不更松）。
    """
    issues: list = []
    scenes = {s.scene_id: s for s in body.scenes}
    internal = known_char_ids | design_internal_ids(body)
    prefixes = ReleasePrefixes(body)
    fallbacks = appellation_fallbacks(characters, structure) if characters is not None else {}
    first_person = body.narration.person == 'first'
    focalizers = set(body.narration.focalizers)

    def display_at(faces: dict[str, tuple[str, ...]]) -> dict[str, str]:
        chars = set(faces) | {cid for cid, face in fallbacks.items() if face}
        if first_person:
            chars |= focalizers
        return {
            char_id: display_surface(
                char_id, faces.get(char_id, ()), structure=structure,
                entry=(characters.characters.get(char_id)
                       if characters is not None else None),
                first_person_focalizer=(first_person and char_id in focalizers))
            for char_id in chars}

    for chapter in body.chapters:
        # 分区文本按**章**判已释放（见模块头）：一章一份显示面、一份字面检查面。
        parts: dict[str, str] = {}
        sites: dict[str, Site] = {}

        def add(key: str, text: str, site: Site) -> None:
            parts[key] = text
            sites[key] = site

        add('book_style', body.book_style, ('book_style',))
        add('purpose', chapter.purpose, ('chapter', chapter.chapter_ordinal, 'purpose'))
        for item in body.expression_decisions:
            add(f'expression/{item.ex_id}', item.statement, ('expression', item.ex_id))
        for i, rule in enumerate(body.world_rules):
            add(f'world_rules/{i}', rule, ('world_rule', i))
        released = chapter_surface_releases(body, chapter.chapter_ordinal, structure)
        released_names = chapter_name_releases(body, chapter.chapter_ordinal, structure)
        available = {r.char_id for r in body.initial_surfaces}
        for prior in body.chapters:
            if prior.chapter_ordinal <= chapter.chapter_ordinal:
                available.update(r.char_id for sid in prior.scene_ids for b in scenes[sid].beats
                                 for r in b.surface_introductions)
        for voice in body.character_voices:
            if voice.char_id in available:
                add(f'voice/{voice.char_id}', voice.voice, ('voice', voice.char_id))
        if chapter.entry.carryover:
            add('entry/carryover', chapter.entry.carryover, ('entry', chapter.chapter_ordinal))
        for sid in chapter.scene_ids:
            scene = scenes[sid]
            add(f'{sid}/realization_brief', scene.realization_brief, ('scene', sid, 'brief'))
            add(f'{sid}/delivery/voice', scene.delivery.voice, ('scene', sid, 'voice'))
            for field in ('immediate_want', 'opposition', 'turn'):
                add(f'{sid}/driver/{field}', getattr(scene.driver, field),
                    ('scene', sid, 'driver'))
            for beat in scene.beats:
                ref = f'{sid}/{beat.beat_id}'
                for field in ('event', 'delivery_hint'):
                    add(f'{ref}/{field}', getattr(beat, field),
                        ('beat', sid, beat.beat_id, field))
                for i, delta in enumerate(beat.target_delta):
                    if isinstance(delta.value, str):
                        add(f'{ref}/delta/{i}', delta.value, ('delta', sid, beat.beat_id, i))
                for i, thread in enumerate(beat.threads):
                    for field in ('text', 'progress', 'resolution'):
                        if getattr(thread, field):
                            add(f'{ref}/threads/{i}/{field}', getattr(thread, field),
                                ('thread', sid, beat.beat_id, i))
        # 字面检查面与绑定表同源：`released`（含登记家族构词，陆家＝{family}家）是
        # 正文章包的章级称谓许可，绑定表只收本章仍被扣留的完整登记称谓。
        withheld = withheld_name_literals(projection, released, structure)
        bindings = withheld_name_bindings(structure, released_names)
        binding_of = {b.literal: b for b in bindings if b.unmarked_unique}
        prefix = f'ch{chapter.chapter_ordinal:02d}/'
        # 显示面取章末（RC-60 第 2～4 条的解析不变，只是取位按章）。
        display = display_at(prefixes.chapter_end_faces(chapter.chapter_ordinal))
        # 先投影再查：同一人物的各次提及分别投影，残余才进字面检查。
        projected: dict[str, MaterialPart] = {}
        surfaces = (expand_surface_releases(
                    {cid: tuple(person.surface_forms) for cid, person in structure.people.items()}, structure)
                    if structure is not None else None)
        for key, text in parts.items():
            if '[[' in text or ']]' in text:
                for error in resolve_name_text(text, surfaces).errors:
                    issues.append(_site_issue(
                        error.message, code=error.code, literal=error.literal,
                        chapter=chapter, where=prefix + key, prefix=prefix,
                        sites=sites, resolve=resolve, inherited=set()))
            new_text, _hits = project_withheld_names(
                text, bindings=bindings, display=display)
            # 保留原来的独立 name/text 片段。提前解码会把相邻两人“白”“若”拼成
            # 第三人的“白若”，也会把某一人的已释放面错误授予同形的另一人。
            projected[prefix + key] = MaterialPart(new_text)
        # keep 的文本由 Brief 回填，直接重写场景不会改变它；诊断指出 wire
        # 中显式改写表达的入口，不替模型改语义，不扩大/缩小原判据。
        inherited = {item.ex_id for item in body.expression_decisions
                     if item.action == 'keep'}
        material_issues = positive_material_issues(
            projected, released=released, withheld=withheld, internal_ids=internal,
            chapter_ordinal=chapter.chapter_ordinal,
            name_permissions=material_name_permissions(released_names, display))
        for issue in material_issues:
            if issue.code != 'positive_material_leak':
                issues.append(_site_issue(
                    issue.message, code=issue.code, literal=issue.literal,
                    chapter=chapter, where=issue.where, prefix=prefix,
                    sites=sites, resolve=resolve, inherited=inherited))
                continue
            binding = binding_of.get(issue.literal)
            if binding is not None and not display.get(binding.char_id):
                message = (f'{issue.message}——已绑定人物 {binding.char_id} 在本章'
                           '没有显示面（本章末仍无释放且无回落纯称谓）；补一行释放、'
                           '挪承载位，或改写该字段指称')
                code = 'design_unreleased_reference'
            else:
                # positive_material_leak / internal_id_leak：裸姓、未登记别名
                # 等不可投影字面，改该字段。
                code = issue.code
                message = issue.message
            issues.append(_site_issue(
                message, code=code, literal=issue.literal, chapter=chapter,
                where=issue.where, prefix=prefix, sites=sites, resolve=resolve,
                inherited=inherited))
    return issues


def _site_issue(message: str, *, code: str, literal: str, chapter, where: str,
                prefix: str, sites: dict, resolve, inherited: set[str]) -> object:
    """分区问题 → 带站点／可编辑路径的 ValidationIssue（稳定位置：站点＋字面）。"""
    for ex_id in sorted(inherited):
        if where.startswith(f'{prefix}expression/{ex_id}'):
            message += (f'（{ex_id} 由默认 keep 从 Brief 回填；须用 expression_overrides'
                        ' 显式改写这项表达并说明理由，保留表达机制与披露时点，'
                        '不得提前使用未释放名称）')
            break
    site = sites.get(where.removeprefix(prefix))
    path = resolve(site) if resolve is not None and site is not None else None
    return design_issue(
        message, code=code,
        location=(chapter.chapter_ordinal, *(site or (('where', where),)), literal),
        paths=(path,) if path is not None else ())
