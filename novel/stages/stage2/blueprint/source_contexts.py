"""source_contexts 稀疏编码与规范化展开；压缩值、不压缩列表表达能力。

语义（缺省 ≠ 猜测）：
- 列表整体省略 / None → 恰好一条默认 context（present≡participants、
  time_layer=present、evidence≡beat.evidence_refs）
- 有任意显式条目 → 只用显式，不再合成默认
- 条目内省略 present → 已确定与 participants 一致
- present: null → 未知；[] → 明确空；非空列表 → 例外在场集
- 省略 time_layer → 已确定当下；null → 未知

不变量：展开(压缩(完整)) == 完整（ActorRef 含 presentation_ref）。
``scx_*`` 摘要必须基于本模块的规范化展开视图。
"""
from __future__ import annotations

from typing import Any

from contracts.schema.plot import ActorRef, PlotBeat, SourceContext


def _actor_dump(actor: ActorRef | dict[str, Any]) -> dict[str, Any]:
    if isinstance(actor, ActorRef):
        return actor.model_dump(mode='json')
    return {'char_id': actor['char_id'],
            'presentation_ref': actor.get('presentation_ref')}


def _actors_equal(left: list[Any] | None, right: list[Any] | None) -> bool:
    if left is None or right is None:
        return left is right
    if len(left) != len(right):
        return False
    return [_actor_dump(a) for a in left] == [_actor_dump(a) for a in right]


def _participant_dumps(beat: PlotBeat) -> list[dict[str, Any]]:
    return [_actor_dump(a) for a in beat.participants]


def expand_source_context(
    context: SourceContext | dict[str, Any],
    beat: PlotBeat,
) -> dict[str, Any]:
    """单条稀疏 context → 规范化展开 dict（供 catalog / 等价比较）。"""
    if isinstance(context, SourceContext):
        fields = context.model_fields_set
        evidence = (
            list(context.evidence_refs)
            if 'evidence_refs' in fields and context.evidence_refs is not None
            else list(beat.evidence_refs))
        if 'time_layer' in fields:
            time_layer = context.time_layer
        else:
            time_layer = 'present'
        place = context.place if 'place' in fields else None
        if 'present' in fields:
            present = (
                None if context.present is None
                else [_actor_dump(a) for a in context.present])
        else:
            present = _participant_dumps(beat)
    else:
        # dict：缺键 = 省略；键在且值为 null = 未知
        if 'evidence_refs' in context and context['evidence_refs'] is not None:
            evidence = list(context['evidence_refs'])
        else:
            evidence = list(beat.evidence_refs)
        if 'time_layer' in context:
            time_layer = context['time_layer']
        else:
            time_layer = 'present'
        place = context['place'] if 'place' in context else None
        if 'present' in context:
            raw = context['present']
            present = None if raw is None else [_actor_dump(a) for a in raw]
        else:
            present = _participant_dumps(beat)
    if not evidence:
        raise ValueError('展开后的舞台证据不能为空')
    if not set(evidence) <= set(beat.evidence_refs):
        raise ValueError('舞台证据必须属于父节点')
    return {
        'evidence_refs': evidence,
        'time_layer': time_layer,
        'place': place,
        'present': present,
    }


def expand_beat_contexts(beat: PlotBeat) -> list[dict[str, Any]]:
    """整拍 source_contexts 展开列表。"""
    raw = beat.source_contexts
    # None / 未设置：一条默认。显式 []：零条。非空：只用显式。
    if raw is None:
        return [expand_source_context(SourceContext(), beat)]
    if len(raw) == 0:
        return []
    return [expand_source_context(row, beat) for row in raw]


def compress_source_context(
    expanded: dict[str, Any],
    beat: PlotBeat,
) -> dict[str, Any]:
    """完整展开 → 稀疏 dict（只含非默认键；显式 null/[] 保留）。"""
    sparse: dict[str, Any] = {}
    evidence = list(expanded['evidence_refs'])
    if evidence != list(beat.evidence_refs):
        sparse['evidence_refs'] = evidence
    time_layer = expanded.get('time_layer')
    if time_layer is None:
        sparse['time_layer'] = None
    elif time_layer != 'present':
        sparse['time_layer'] = time_layer
    place = expanded.get('place')
    if place is not None:
        sparse['place'] = place
    present = expanded.get('present')
    if present is None:
        sparse['present'] = None
    elif present == []:
        sparse['present'] = []
    elif not _actors_equal(present, beat.participants):
        sparse['present'] = present
    return sparse


def compress_beat_contexts(
    expanded_rows: list[dict[str, Any]],
    beat: PlotBeat,
) -> list[dict[str, Any]] | None:
    """展开列表 → 稀疏列表；若恰为单条全默认则返回 None（省略）。"""
    if not expanded_rows:
        return []
    if len(expanded_rows) == 1:
        sparse = compress_source_context(expanded_rows[0], beat)
        if not sparse:
            return None
    return [compress_source_context(row, beat) for row in expanded_rows]


def contexts_equal_expanded(left: list[dict[str, Any]], right: list[dict[str, Any]]) -> bool:
    return left == right
