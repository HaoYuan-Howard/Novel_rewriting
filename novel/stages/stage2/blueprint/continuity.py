"""Blueprint Continuity Pass：0-LLM 跨章状态缝合。

章抽取可并行（空 entry_states）；正式 identity / entry / carry 在本模块
按阅读序确定性落地。精确主张匹配用于去重续写，不做语义模糊合并，
不另起 Continuity LLM。
"""
from __future__ import annotations

from typing import Any, Mapping, Sequence

from contracts.schema.plot import PlotChapterResponse, PlotState


def formal_claim_key(state: Mapping[str, Any] | PlotState) -> tuple[Any, ...]:
    """正式 PlotState 的可匹配主张键（content XOR global_ref + modality + holder）。"""
    if isinstance(state, PlotState):
        state = state.model_dump(mode='json')
    modality = state['modality']
    holder = state.get('holder')
    holder_cid = holder.get('char_id') if isinstance(holder, Mapping) else None
    global_ref = state.get('global_ref')
    if global_ref is not None:
        return ('ref', modality, holder_cid, global_ref['kind'], global_ref['ref'])
    return ('content', modality, holder_cid, state.get('content'))


def wire_claim_key(state: Mapping[str, Any]) -> tuple[Any, ...]:
    """PlotStateResponse Extract 面的主张键（semantic / canonical_ref_hint）。"""
    modality = state['modality']
    holder = state.get('holder')
    holder_cid = holder.get('char_id') if isinstance(holder, Mapping) else None
    hint = state.get('canonical_ref_hint')
    if hint is not None:
        return ('ref', modality, holder_cid, hint['kind'], hint['ref'])
    # legacy dual-write 已在 schema before 规范化；仍容忍 content 形夹具
    if 'semantic' in state:
        return ('content', modality, holder_cid, state.get('semantic'))
    global_ref = state.get('global_ref')
    if global_ref is not None:
        return ('ref', modality, holder_cid, global_ref['kind'], global_ref['ref'])
    return ('content', modality, holder_cid, state.get('content'))


def prepare_chapter_for_continuity(
    chapter: PlotChapterResponse,
    carried_materials: Sequence[Mapping[str, Any]],
) -> PlotChapterResponse:
    """在 compile_chapter 前注入 entry/carry，并按精确主张折叠与携入态重复的本地 state。"""
    payload = chapter.model_dump(mode='json')
    carried_ids = [row['state']['state_id'] for row in carried_materials]
    claim_to_prior = {
        formal_claim_key(row['state']): row['state']['state_id']
        for row in carried_materials
    }
    if not carried_ids:
        return PlotChapterResponse.model_validate(payload)

    kept_states: list[dict[str, Any]] = []
    local_key_to_prior: dict[str, str] = {}
    extra_endings: list[dict[str, Any]] = []
    for state in payload['states']:
        local_key = state['state_key']
        prior = claim_to_prior.get(wire_claim_key(state))
        if prior is None:
            kept_states.append(state)
            continue
        local_key_to_prior[local_key] = prior
        ended = state.get('ended_at')
        if ended is not None and ended.get('kind') == 'at_beat' and ended.get('beat_ref'):
            extra_endings.append({
                'state_ref': prior,
                'at_beat_ref': ended['beat_ref'],
                'evidence': list(state.get('evidence') or []),
            })

    payload['states'] = kept_states

    endings = list(payload.get('inherited_state_endings') or [])
    seen_end = {row['state_ref'] for row in endings}
    for row in extra_endings:
        if row['state_ref'] in seen_end:
            continue
        if not row['evidence']:
            # 无证据不能伪造 inherited ending；仅折叠重复 state
            continue
        endings.append(row)
        seen_end.add(row['state_ref'])
    payload['inherited_state_endings'] = endings

    # entry：上游 carry ∪ 模型已写且仍属于 carry 的正式 ID；去掉已去重本地键
    entry = {
        ref for ref in payload.get('entry_state_refs') or []
        if ref in carried_ids or ref in local_key_to_prior.values()
    }
    entry.update(carried_ids)
    # 本章 at_entry 本地态仍可自引用
    local_keys = {row['state_key'] for row in kept_states}
    for ref in payload.get('entry_state_refs') or []:
        if ref in local_keys:
            entry.add(ref)
    payload['entry_state_refs'] = sorted(entry)

    ended_refs = {row['state_ref'] for row in payload['inherited_state_endings']}
    for state in kept_states:
        for ref in state.get('supersedes_state_refs') or []:
            mapped = local_key_to_prior.get(ref, ref)
            if mapped in carried_ids:
                ended_refs.add(mapped)

    carry = set(carried_ids) - ended_refs
    for ref in payload.get('carry_out_refs') or []:
        if ref in local_key_to_prior:
            prior = local_key_to_prior[ref]
            if prior not in ended_refs:
                carry.add(prior)
        elif ref in local_keys:
            carry.add(ref)
        elif ref in carried_ids and ref not in ended_refs:
            carry.add(ref)
    # 新本地态默认：模型列入 carry_out 的保留；未列入且未结束的不自动永久携带
    payload['carry_out_refs'] = sorted(carry)

    # supersedes 里若写了被折叠的本地键，改成 prior 正式 ID
    for state in payload['states']:
        state['supersedes_state_refs'] = [
            local_key_to_prior.get(ref, ref)
            for ref in state.get('supersedes_state_refs') or []
        ]

    return PlotChapterResponse.model_validate(payload)
