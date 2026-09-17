"""章节抽取的轻量输入身份；producer 与进度使用同一消费投影，不构建模型工作包。"""
from __future__ import annotations

from typing import Any

from novel.consumption import consumed_digest, plot_global_projection
from novel.stages.stage2.blueprint.compile import source_digest


def input_digests(title: str, main_text: list[dict[str, Any]], *,
                  character_inventory: dict, global_projections: dict) -> dict[str, str]:
    visible = character_inventory['visible_character_refs']
    return {str(row['chapter_ordinal']): consumed_digest({
        'contract': 'plot-extraction-input-v2',
        'title': title, 'chapter_ordinal': row['chapter_ordinal'],
        'source_sha256': source_digest(row['text']),
        'character_refs': visible.get(row['chapter_ordinal'], {}),
        'character_bindings': character_inventory['character_bindings'].get(row['chapter_ordinal'], {}),
        'global_subjects': global_projections[str(row['chapter_ordinal'])],
    }) for row in main_text}


def read_input_digests(store) -> dict[str, str]:
    from contracts.schema.knowledge import KnowledgeGrounding
    from contracts.schema.stage1 import MainText
    from novel.errors import HumanEditProtected
    from novel.stages.stage2 import extract
    from novel.stages.stage2.shared.character_refs import blueprint_inventory_from_projection

    rels = (extract.MAIN_TEXT, extract.BLUEPRINT_CHARACTER_REL, extract.GLOBAL_REL)
    hashes = {rel: store.full_sha256_or_none(rel) for rel in rels}
    main = store.read(MainText, extract.MAIN_TEXT).model_dump(mode='json')
    projection, _ = extract._require_blueprint_character_projection(store)
    knowledge = store.read(KnowledgeGrounding, extract.GLOBAL_REL).model_dump(
        mode='json', by_alias=True)
    result = input_digests(store.ctx.title, main,
        character_inventory=blueprint_inventory_from_projection(projection),
        global_projections={str(row['chapter_ordinal']): plot_global_projection(
            knowledge, row['chapter_ordinal']) for row in main})
    if hashes != {rel: store.full_sha256_or_none(rel) for rel in rels}:
        raise HumanEditProtected('读取章节生成进度时输入发生变化')
    return result
