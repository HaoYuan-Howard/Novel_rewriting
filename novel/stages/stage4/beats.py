"""完整剧情节点及证据索引；场景映射只由 BeatPlacement 派生。"""
from __future__ import annotations

from typing import Any


def beat_index(blueprints: dict[str, Any],
               main_text: list[dict[str, Any]]) -> dict[str, dict[str, Any]]:
    """索引完整剧情及共享证据；源章篇幅取原文一次，不累加重叠证据。"""
    from contracts.schema.plot import PlotBlueprint
    from novel.stages.stage2.blueprint.compile import validate_evidence
    from novel.stages.stage2.blueprint.evidence import slice_evidence_map
    from novel.stages.stage2.blueprint.source_contexts import expand_beat_contexts
    texts = {m['chapter_ordinal']: m['text'] for m in main_text}
    if len(texts) != len(main_text):
        raise ValueError('原文章号重复')
    result = {}
    for raw in blueprints['blueprints']:
        bp = PlotBlueprint.model_validate(raw)
        source = texts[bp.chapter_ordinal]
        validate_evidence(bp, source)
        for beat in bp.beats:
            if beat.beat_id in result:
                raise ValueError('剧情节点身份重复')
            result[beat.beat_id] = {
                'ord': bp.chapter_ordinal, 'content': beat.content,
                'source_chapter_chars': len(source),
                'evidence': slice_evidence_map(bp, beat.evidence_refs),
                'source_contexts': expand_beat_contexts(beat),
            }
    return result
