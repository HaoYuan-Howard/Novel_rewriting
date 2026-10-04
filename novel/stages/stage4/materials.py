"""Design 自有文本与正式写作材料的正向分区验证；不改写、不做语义代猜。

CS-F1（RC-46/47）：每条泄漏问题在判定处带上**站点**（Design 哪个字段产生了这段文本），
调用方（`design.py`）按自己手里的 v8 wire 把站点换成可编辑的 JSON 路径，局部修订据此
只重写真正出问题的最小完整父项；本模块不读 wire、不猜路径、不解析错误文案。
"""
from __future__ import annotations

from typing import Callable

from contracts.schema.design import BodyDesign
from contracts.schema.source import NameProjection, NameStructure
from novel.core.material_trace import MaterialPart
from novel.stages.common import positive_material_issues, withheld_name_literals
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
) -> list:
    """按章自身释放表检查将送达的自然语言；avoid 等负向条目不纳入。

    返回逐条问题（文案沿用 `MaterialIssue.message`，身份与措辞、字符偏移无关）。
    `resolve` 给出时，问题带可编辑 wire 路径（局部修订用）；字段不可由 Design 编辑
    （世界规则、由 pov 派生的 voice 等）或没有解析器时不带路径，退整份返修。
    """
    issues: list = []
    scenes = {s.scene_id: s for s in body.scenes}
    internal = known_char_ids | design_internal_ids(body)
    for chapter in body.chapters:
        parts: dict[str, str] = {'book_style': body.book_style, 'purpose': chapter.purpose}
        sites: dict[str, Site] = {'book_style': ('book_style',),
                                  'purpose': ('chapter', chapter.chapter_ordinal, 'purpose')}
        for item in body.expression_decisions:
            key = f'expression/{item.ex_id}'
            parts[key], sites[key] = item.statement, ('expression', item.ex_id)
        for i, rule in enumerate(body.world_rules):
            key = f'world_rules/{i}'
            parts[key], sites[key] = rule, ('world_rule', i)
        released = chapter_surface_releases(body, chapter.chapter_ordinal, structure)
        available = {r.char_id for r in body.initial_surfaces}
        for prior in body.chapters:
            if prior.chapter_ordinal <= chapter.chapter_ordinal:
                available.update(r.char_id for sid in prior.scene_ids for b in scenes[sid].beats
                                 for r in b.surface_introductions)
        for voice in body.character_voices:
            if voice.char_id in available:
                key = f'voice/{voice.char_id}'
                parts[key], sites[key] = voice.voice, ('voice', voice.char_id)
        if chapter.entry.carryover:
            parts['entry/carryover'] = chapter.entry.carryover
            sites['entry/carryover'] = ('entry', chapter.chapter_ordinal)
        for sid in chapter.scene_ids:
            scene = scenes[sid]
            key = f'{sid}/realization_brief'
            parts[key], sites[key] = scene.realization_brief, ('scene', sid, 'brief')
            key = f'{sid}/delivery/voice'
            parts[key], sites[key] = scene.delivery.voice, ('scene', sid, 'voice')
            for field in ('immediate_want', 'opposition', 'turn'):
                key = f'{sid}/driver/{field}'
                parts[key], sites[key] = getattr(scene.driver, field), ('scene', sid, 'driver')
            for beat in scene.beats:
                ref = f'{sid}/{beat.beat_id}'
                for field in ('event', 'delivery_hint'):
                    key = f'{ref}/{field}'
                    parts[key], sites[key] = getattr(beat, field), (
                        'beat', sid, beat.beat_id, field)
                for i, delta in enumerate(beat.target_delta):
                    if isinstance(delta.value, str):
                        key = f'{ref}/delta/{i}'
                        parts[key], sites[key] = delta.value, ('delta', sid, beat.beat_id, i)
                for i, thread in enumerate(beat.threads):
                    for field in ('text', 'progress', 'resolution'):
                        if getattr(thread, field):
                            key = f'{ref}/threads/{i}/{field}'
                            parts[key], sites[key] = getattr(thread, field), (
                                'thread', sid, beat.beat_id, i)
        # 初始态只按实际投影终检；未送达本章的隐藏事实不能冒充正向阅读材料。
        prefix = f'ch{chapter.chapter_ordinal:02d}/'
        chapter_issues = positive_material_issues(
            {prefix + key: MaterialPart(text) for key, text in parts.items()},
            released=released, withheld=withheld_name_literals(projection, released, structure),
            internal_ids=internal)
        # keep 的文本由 Brief 回填，直接重写场景不会改变它；诊断指出 wire
        # 中显式改写表达的入口，不替模型改语义，不扩大/缩小原判据。
        inherited = {item.ex_id for item in body.expression_decisions if item.action == 'keep'}
        for issue in chapter_issues:
            message = issue.message
            for ex_id in inherited:
                if issue.where.startswith(f'ch{chapter.chapter_ordinal:02d}/expression/{ex_id}'):
                    message += (f'（{ex_id} 由默认 keep 从 Brief 回填；须用 expression_overrides'
                                ' 显式改写这项表达并说明理由，保留表达机制与披露时点，'
                                '不得提前使用未释放名称）')
                    break
            site = sites.get(issue.where.removeprefix(prefix))
            path = resolve(site) if resolve is not None and site is not None else None
            # 稳定位置：字段站点＋泄漏字面；与措辞、章内字符偏移、展示行号无关。
            issues.append(design_issue(
                message, code=issue.code,
                location=(chapter.chapter_ordinal, *(site or (issue.where,)), issue.literal),
                paths=(path,) if path is not None else ()))
    return issues
