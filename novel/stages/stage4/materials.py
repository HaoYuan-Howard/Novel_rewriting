"""Design 自有文本与正式写作材料的正向分区验证；不改写、不做语义代猜。"""
from __future__ import annotations

from contracts.schema.design import BodyDesign
from contracts.schema.source import NameProjection, NameStructure
from novel.stages.common import validate_positive_materials, withheld_name_literals
from novel.stages.stage4.book_ir import chapter_surface_releases


def design_internal_ids(body: BodyDesign) -> set[str]:
    return {s.scene_id for s in body.scenes} | {
        b.beat_id for s in body.scenes for b in s.beats} | {
        t.ref for s in body.scenes for b in s.beats for t in b.threads}


def validate_design_materials(
    body: BodyDesign, *, projection: NameProjection, known_char_ids: set[str],
    structure: NameStructure | None = None,
) -> list[str]:
    """按章自身释放表检查将送达的自然语言；avoid 等负向条目不纳入。"""
    errors: list[str] = []
    scenes = {s.scene_id: s for s in body.scenes}
    internal = known_char_ids | design_internal_ids(body)
    for chapter in body.chapters:
        parts = {'book_style': body.book_style, 'purpose': chapter.purpose}
        parts.update({f'expression/{item.ex_id}': item.statement for item in body.expression_decisions})
        parts.update({f'world_rules/{i}': r for i, r in enumerate(body.world_rules)})
        released = chapter_surface_releases(body, chapter.chapter_ordinal, structure)
        available = {r.char_id for r in body.initial_surfaces}
        for prior in body.chapters:
            if prior.chapter_ordinal <= chapter.chapter_ordinal:
                available.update(r.char_id for sid in prior.scene_ids for b in scenes[sid].beats
                                 for r in b.surface_introductions)
        for voice in body.character_voices:
            if voice.char_id in available:
                parts[f'voice/{voice.char_id}'] = voice.voice
        if chapter.entry.carryover:
            parts['entry/carryover'] = chapter.entry.carryover
        for sid in chapter.scene_ids:
            scene = scenes[sid]
            parts[f'{sid}/realization_brief'] = scene.realization_brief
            parts[f'{sid}/delivery/voice'] = scene.delivery.voice
            for field in ('immediate_want', 'opposition', 'turn'):
                parts[f'{sid}/driver/{field}'] = getattr(scene.driver, field)
            for beat in scene.beats:
                ref = f'{sid}/{beat.beat_id}'
                for field in ('event', 'delivery_hint'):
                    parts[f'{ref}/{field}'] = getattr(beat, field)
                for i, delta in enumerate(beat.target_delta):
                    if isinstance(delta.value, str):
                        parts[f'{ref}/delta/{i}'] = delta.value
                for i, thread in enumerate(beat.threads):
                    for field in ('text', 'progress', 'resolution'):
                        if getattr(thread, field):
                            parts[f'{ref}/threads/{i}/{field}'] = getattr(thread, field)
        # 初始态只按实际投影终检；未送达本章的隐藏事实不能冒充正向阅读材料。
        chapter_errors = validate_positive_materials(
            {f'ch{chapter.chapter_ordinal:02d}/{key}': text for key, text in parts.items()},
            released=released, withheld=withheld_name_literals(projection, released, structure),
            internal_ids=internal)
        # keep 的文本由 Brief 回填，直接重写场景不会改变它；诊断指出 wire
        # 中显式改写表达的入口，不替模型改语义，不扩大/缩小原判据。
        inherited = {item.ex_id for item in body.expression_decisions if item.action == 'keep'}
        for error in chapter_errors:
            for ex_id in inherited:
                if error.startswith(f'positive_material_leak[ch{chapter.chapter_ordinal:02d}/expression/{ex_id}@'):
                    error += (f'（{ex_id} 由默认 keep 从 Brief 回填；须用 expression_overrides'
                              ' 显式改写这项表达并说明理由，保留表达机制与披露时点，'
                              '不得提前使用未释放名称）')
                    break
            errors.append(error)
    return errors
