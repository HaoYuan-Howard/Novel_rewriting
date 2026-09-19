"""Blueprint 证据统一解析与按引用切片；不另存可编辑真相源。

章内 ``evidence_anchors`` 仍由 compile 物化；本模块是唯一推荐读取入口。
禁止全书按 (start_para, end_para) 去重；禁止把整表塞进 Stage4 A 工作包。
"""
from __future__ import annotations

from collections.abc import Iterable, Mapping, Sequence
from typing import Any

from contracts.schema.plot import EvidenceAnchor, PlotBlueprint


class BlueprintEvidenceError(ValueError):
    """证据解析或切片失败（缺锚、越章、空切片等）。"""


def chapter_evidence_table(chapter: PlotBlueprint) -> dict[str, EvidenceAnchor]:
    """本章 evidence_id → 锚点；同章内 ID 必须唯一。"""
    table: dict[str, EvidenceAnchor] = {}
    for row in chapter.evidence_anchors:
        if row.evidence_id in table:
            raise BlueprintEvidenceError(f'章内证据 ID 重复：{row.evidence_id}')
        table[row.evidence_id] = row
    return table


def evidence_ids_of(chapter: PlotBlueprint) -> frozenset[str]:
    return frozenset(row.evidence_id for row in chapter.evidence_anchors)


def resolve_evidence(chapter: PlotBlueprint, evidence_id: str) -> EvidenceAnchor:
    """解析单条证据；校验章前缀归属。"""
    prefix = f'e{chapter.chapter_ordinal:02d}_'
    if not evidence_id.startswith(prefix):
        raise BlueprintEvidenceError(
            f'证据 {evidence_id} 不属于第 {chapter.chapter_ordinal} 章')
    table = chapter_evidence_table(chapter)
    row = table.get(evidence_id)
    if row is None:
        raise BlueprintEvidenceError(f'证据不存在：{evidence_id}')
    return row


def slice_evidence(
    chapter: PlotBlueprint,
    evidence_refs: Sequence[str],
) -> list[dict[str, Any]]:
    """只返回实际引用的锚点 JSON（按引用序、去重保序）；不下发整章表。"""
    seen: set[str] = set()
    result: list[dict[str, Any]] = []
    for ref in evidence_refs:
        if ref in seen:
            continue
        seen.add(ref)
        result.append(resolve_evidence(chapter, ref).model_dump(mode='json'))
    return result


def slice_evidence_map(
    chapter: PlotBlueprint,
    evidence_refs: Iterable[str],
) -> dict[str, dict[str, Any]]:
    """引用 → 锚点 JSON；仅含请求的键。"""
    return {row['evidence_id']: row for row in slice_evidence(chapter, list(evidence_refs))}


def index_book_evidence(
    chapters: Sequence[PlotBlueprint],
) -> dict[str, tuple[int, EvidenceAnchor]]:
    """全书索引：evidence_id → (章号, 锚点)。不跨章按段号合并。"""
    result: dict[str, tuple[int, EvidenceAnchor]] = {}
    for chapter in chapters:
        for row in chapter.evidence_anchors:
            if row.evidence_id in result:
                raise BlueprintEvidenceError(f'跨章证据 ID 重复：{row.evidence_id}')
            result[row.evidence_id] = (chapter.chapter_ordinal, row)
    return result


def resolve_from_book(
    chapters: Sequence[PlotBlueprint] | Mapping[int, PlotBlueprint],
    evidence_id: str,
) -> tuple[int, EvidenceAnchor]:
    if isinstance(chapters, Mapping):
        index = index_book_evidence(list(chapters.values()))
    else:
        index = index_book_evidence(chapters)
    hit = index.get(evidence_id)
    if hit is None:
        raise BlueprintEvidenceError(f'证据不存在：{evidence_id}')
    return hit


def validate_chapter_evidence_closure(chapter: PlotBlueprint) -> None:
    """锚点必须全被引用；相同段范围必须复用同一锚点（章内）。"""
    anchors = chapter_evidence_table(chapter)
    used: set[str] = set()
    for row in (*chapter.beats, *chapter.states, *chapter.constraints,
                *chapter.inherited_state_endings, *chapter.actor_bindings, *chapter.narration):
        refs = getattr(row, 'evidence_refs', None)
        if refs is None:
            continue
        if not set(refs) <= set(anchors):
            raise BlueprintEvidenceError('证据引用不存在或越章')
        used.update(refs)
    # beat source_contexts 的 refs 在 expand 后校验；此处扫已物化列表
    from novel.stages.stage2.blueprint.source_contexts import expand_beat_contexts
    for beat in chapter.beats:
        for context in expand_beat_contexts(beat):
            if not set(context['evidence_refs']) <= set(anchors):
                raise BlueprintEvidenceError('舞台证据引用不存在或越章')
            used.update(context['evidence_refs'])
    if used != set(anchors):
        raise BlueprintEvidenceError('证据表只能包含实际引用的锚点')
    spans = [(r.start_para, r.end_para) for r in chapter.evidence_anchors]
    if len(spans) != len(set(spans)):
        raise BlueprintEvidenceError('相同段范围必须复用一个证据锚点')
