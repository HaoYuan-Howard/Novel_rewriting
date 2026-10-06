"""Design 自有文本与正式写作材料的正向分区验证；不改写、不做语义代猜。

CS-F1（RC-46/47）：每条泄漏问题在判定处带上**站点**（Design 哪个字段产生了这段文本），
调用方（`design.py`）按自己手里的 v8 wire 把站点换成可编辑的 JSON 路径，局部修订据此
只重写真正出问题的最小完整父项；本模块不读 wire、不猜路径、不解析错误文案。

CS-H H3（RC-60「投影」「残余归属」）：**先投影再查**——每个分区文本按其位置
（书级行＝章首、场级说明行＝场末、拍行＝拍前缀含本拍释放）的显示面把已绑定
称谓确定性投影（同位语折叠与无损性守卫与正文／Hook 同一把尺），再对投影后的
文本查残余。残余三态归因（规格「投影后的残余归属」表）：

· `design_unreleased_reference`——已绑定称谓，但该人物在此处没有显示面
  （尚无释放且无回落纯称谓）：补一行释放或挪承载位／改写该字段指称；
· `design_projection_lossy`——无损性守卫触发（同一句同一人物两个不相邻的
  不同称谓将压成一个）：挪 know 拍位或改写指称；
· `positive_material_leak`——裸姓、未登记别名等不可投影字面：改该字段。

三类都带路径（locate）；义务文本里的同类残余归 Preserve（自闸随 H3d 前置），
不在本模块的 Design 分区出现。
"""
from __future__ import annotations

from typing import Callable

from contracts.schema.design import BodyDesign
from contracts.schema.source import CharactersCore, NameProjection, NameStructure
from contracts.schema.state import WithheldNameBinding
from novel.core.material_trace import MaterialPart
from novel.core.name_materials import (
    appellation_fallbacks, display_surface, withheld_name_bindings,
)
from novel.stages.common import (
    ReleasePrefixes, expand_surface_releases, positive_material_issues,
    project_withheld_names, withheld_name_literals,
)
from novel.stages.stage4.book_ir import chapter_surface_releases
from novel.stages.stage4.design_paths import design_issue

#: 站点：Design 字段的稳定坐标（不含 wire 结构）。第一位是种类，其余是该种类的坐标。
Site = tuple

#: 站点 → wire 路径的解析器；返回 None 表示该字段不是 Design 可编辑的（派生自上游）。
SiteResolver = Callable[[Site], 'tuple | None']


def design_internal_ids(body: BodyDesign) -> set[str]:
    return {s.scene_id for s in body.scenes} | {
        b.beat_id for s in body.scenes for b in s.beats} | {
        t.ref for s in body.scenes for b in s.beats for t in b.threads}


def validate_design_materials(
    body: BodyDesign, *, projection: NameProjection, known_char_ids: set[str],
    structure: NameStructure | None = None, resolve: SiteResolver | None = None,
    characters: CharactersCore | None = None,
) -> list:
    """按位置显示面投影后检查将送达的自然语言；avoid 等负向条目不纳入。

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
        return {
            char_id: display_surface(
                char_id, faces.get(char_id, ()), structure=structure,
                entry=(characters.characters.get(char_id)
                       if characters is not None else None),
                first_person_focalizer=(first_person and char_id in focalizers))
            for char_id in chars}

    for chapter in body.chapters:
        # 分区文本按位置分组：同位置的文本共用同一份显示面与字面检查面。
        groups: dict[tuple, dict[str, str]] = {}
        sites: dict[str, Site] = {}

        def add(key: str, text: str, site: Site, position: tuple) -> None:
            groups.setdefault(position, {})[key] = text
            sites[key] = site

        chapter_pos = ('chapter', chapter.chapter_ordinal)
        add('book_style', body.book_style, ('book_style',), chapter_pos)
        add('purpose', chapter.purpose, ('chapter', chapter.chapter_ordinal, 'purpose'),
            chapter_pos)
        for item in body.expression_decisions:
            add(f'expression/{item.ex_id}', item.statement, ('expression', item.ex_id),
                chapter_pos)
        for i, rule in enumerate(body.world_rules):
            add(f'world_rules/{i}', rule, ('world_rule', i), chapter_pos)
        released_flat = chapter_surface_releases(body, chapter.chapter_ordinal, structure)
        available = {r.char_id for r in body.initial_surfaces}
        for prior in body.chapters:
            if prior.chapter_ordinal <= chapter.chapter_ordinal:
                available.update(r.char_id for sid in prior.scene_ids for b in scenes[sid].beats
                                 for r in b.surface_introductions)
        for voice in body.character_voices:
            if voice.char_id in available:
                add(f'voice/{voice.char_id}', voice.voice, ('voice', voice.char_id),
                    chapter_pos)
        if chapter.entry.carryover:
            add('entry/carryover', chapter.entry.carryover,
                ('entry', chapter.chapter_ordinal), chapter_pos)
        for sid in chapter.scene_ids:
            scene = scenes[sid]
            scene_pos = ('scene', sid)
            add(f'{sid}/realization_brief', scene.realization_brief,
                ('scene', sid, 'brief'), scene_pos)
            add(f'{sid}/delivery/voice', scene.delivery.voice, ('scene', sid, 'voice'),
                scene_pos)
            for field in ('immediate_want', 'opposition', 'turn'):
                add(f'{sid}/driver/{field}', getattr(scene.driver, field),
                    ('scene', sid, 'driver'), scene_pos)
            for beat in scene.beats:
                ref = f'{sid}/{beat.beat_id}'
                beat_pos = ('beat', sid, beat.beat_id)
                for field in ('event', 'delivery_hint'):
                    add(f'{ref}/{field}', getattr(beat, field),
                        ('beat', sid, beat.beat_id, field), beat_pos)
                for i, delta in enumerate(beat.target_delta):
                    if isinstance(delta.value, str):
                        add(f'{ref}/delta/{i}', delta.value,
                            ('delta', sid, beat.beat_id, i), beat_pos)
                for i, thread in enumerate(beat.threads):
                    for field in ('text', 'progress', 'resolution'):
                        if getattr(thread, field):
                            add(f'{ref}/threads/{i}/{field}', getattr(thread, field),
                                ('thread', sid, beat.beat_id, i), beat_pos)
        # 本章绑定表（归属证明与位置无关；显示面随位置变）。
        bindings = tuple(
            WithheldNameBinding(literal=literal, char_id=char_id, enclosing=enclosing)
            for literal, char_id, enclosing in withheld_name_bindings(
                withheld_name_literals(projection, released_flat, structure),
                structure, released_flat))
        binding_of = {b.literal: b for b in bindings}
        prefix = f'ch{chapter.chapter_ordinal:02d}/'
        for position, parts in groups.items():
            if position[0] == 'chapter':
                faces = prefixes.chapter_start_faces(position[1])
            elif position[0] == 'scene':
                faces = prefixes.scene_end_faces(position[1])
            else:
                faces = prefixes.beat_faces(position[1], position[2])
            display = display_at(faces)
            # 字面检查面与正文／章 IR 同一把尺：已释放称谓并入本人家族的登记构词
            # （陆家＝{family}家）。显示面解析用原始释放面（构词不是指称某人的
            # 显示面），两者分开——此前只用原始面，「陆家上下」这类集体指称在
            # 同家族人物已释放后仍被判泄漏，比正文闸更严。
            released_here = {s for values in expand_surface_releases(faces, structure).values()
                             for s in values}
            withheld_here = withheld_name_literals(projection, released_here, structure)
            # 先投影再查：同位语折叠与无损性守卫之后，残余才进字面检查。
            projected: dict[str, MaterialPart] = {}
            blocked: set[str] = set()
            for key, text in parts.items():
                new_text, _hits, blocks = project_withheld_names(
                    text, bindings=bindings, display=display)
                projected[prefix + key] = MaterialPart(new_text)
                blocked.update(block.literal for block in blocks)
            # keep 的文本由 Brief 回填，直接重写场景不会改变它；诊断指出 wire
            # 中显式改写表达的入口，不替模型改语义，不扩大/缩小原判据。
            inherited = {item.ex_id for item in body.expression_decisions
                         if item.action == 'keep'}
            for issue in positive_material_issues(
                    projected, released=released_here, withheld=withheld_here,
                    internal_ids=internal, chapter_ordinal=chapter.chapter_ordinal):
                if issue.code != 'positive_material_leak':
                    issues.append(_site_issue(
                        issue.message, code=issue.code, literal=issue.literal,
                        chapter=chapter, where=issue.where, prefix=prefix,
                        sites=sites, resolve=resolve, inherited=inherited))
                    continue
                binding = binding_of.get(issue.literal)
                if binding is not None and not display.get(binding.char_id):
                    message = (f'{issue.message}——已绑定人物 {binding.char_id} 在此位置'
                               '没有显示面（尚无释放且无回落纯称谓）；补一行释放、'
                               '挪承载位，或改写该字段指称')
                    code = 'design_unreleased_reference'
                elif issue.literal in blocked:
                    message = (f'{issue.message}——无损性守卫：同一句同一人物两个'
                               '不相邻的不同称谓投影后将压成一个；挪拍位、'
                               '改写指称或统一称谓')
                    code = 'design_projection_lossy'
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
