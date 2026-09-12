"""完整剧情节点及证据索引；场景映射只由 BeatPlacement 派生。"""
from __future__ import annotations

from typing import Any


def beat_index(blueprints: dict[str, Any],
               main_text: list[dict[str, Any]]) -> dict[str, dict[str, Any]]:
    """索引完整剧情及共享证据；源章篇幅取原文一次，不累加重叠证据。"""
    from contracts.schema.plot import PlotBlueprint
    from novel.stages.stage2.plot_compile import validate_evidence
    texts = {m['chapter_ordinal']: m['text'] for m in main_text}
    if len(texts) != len(main_text):
        raise ValueError('原文章号重复')
    result = {}
    for raw in blueprints['blueprints']:
        bp = PlotBlueprint.model_validate(raw)
        source = texts[bp.chapter_ordinal]
        validate_evidence(bp, source)
        anchors = {a.evidence_id: a.model_dump(mode='json') for a in bp.evidence_anchors}
        for beat in bp.beats:
            if beat.beat_id in result:
                raise ValueError('剧情节点身份重复')
            result[beat.beat_id] = {
                'ord': bp.chapter_ordinal, 'content': beat.content,
                'source_chapter_chars': len(source),
                'evidence': {ref: anchors[ref] for ref in beat.evidence_refs},
                'source_contexts': [c.model_dump(mode='json') for c in beat.source_contexts],
            }
    return result
