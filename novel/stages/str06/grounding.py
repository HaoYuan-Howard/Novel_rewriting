#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""grounding —— Preserve／Bind 的确定性组件（F3）。

【分工】语义判断（哪些必保、如何承载）是 Preserve／Bind 两次 book-level LLM
调用的职责（harness 组装付费请求）；本模块是确定性臂：
  • `assemble_preserve`：Preserve wire → PreserveCheckpoint（校验：义务 id
    唯一、requires 引用闭合且无环、lineage 引用真实 beat/char、必保必有
    satisfaction——后者由 schema 构造期保证；checkpoint hash 由规范化字节
    计算）。
  • `project_brief`：checkpoint →（ReconstructionBrief, IdentityProjection）。
    Brief 只从义务投影 event_core（去源坐标：participants 用 char_id，
    requires 用义务 id）；IdentityProjection 从 Characters v4 公共投影。
    Design 拿到的是这两份 + OutputConstraints，**读不到 checkpoint 内部**。
  • `assemble_binding`：Bind wire → SemanticBinding（跨产物校验复用 F1 的
    check_binding_covers_checkpoint；完整发布，unbound 保留义务）。

不修改输入；不落盘（落盘归 harness／repository）。
"""
from __future__ import annotations

import hashlib
from typing import Any

from contracts.schema.str06.grounding import (
    IdentityProjection, PreserveCheckpoint, ReconstructionBrief,
    SemanticBinding, SourceLineage, SourceObligation,
)
from contracts.schema.str06.policy import PreserveKind
from contracts.schema.str06.source import (
    BlueprintsArtifactV12, CharactersArtifactV4,
)
from contracts.schema.str06.grounding import EventCoreNote
from novel.stages.str06.checks import check_binding_covers_checkpoint
from pydantic import ValidationError


def _sha256_payload(obj: Any) -> str:
    import json
    return hashlib.sha256(
        json.dumps(obj, ensure_ascii=False, sort_keys=True).encode('utf-8')).hexdigest()


#: wire 键白名单（Q-1：多余键在 compile 入口报错，不静默丢弃）
_OBLIGATION_KEYS = frozenset({'obligation_id', 'kind', 'statement', 'lineage',
                              'must_preserve', 'satisfaction', 'requires'})
_LINEAGE_KEYS = frozenset({'blueprint_refs', 'character_ids'})
_ENTRY_KEYS = frozenset({'obligation_id', 'scene_id', 'beat_ref'})
_UNBOUND_KEYS = frozenset({'obligation_id', 'owner', 'reason'})


def _reject_unknown_keys(errors: list[str], obj: dict[str, Any], allowed: frozenset[str],
                         where: str) -> None:
    extra = set(obj) - allowed
    if extra:
        errors.append(f'{where} 携带未知字段 {sorted(extra)}（wire 键白名单外）')


def assemble_preserve(
    wire: dict[str, Any], *,
    policy_id: str, policy_version: int,
    characters: CharactersArtifactV4, blueprint: BlueprintsArtifactV12,
    provider: str, model: str, request_sha256: str,
) -> tuple[PreserveCheckpoint | None, list[str]]:
    """Preserve wire（{'obligations': [...]}）→ checkpoint。确定性校验：
    id 唯一、requires 闭合无环、lineage 的 beat/char 引用真实。"""
    errors: list[str] = []
    raw = wire.get('obligations') or []
    if not raw:
        return None, ['obligations 缺失或为空']
    beat_ids = {b.beat_id for c in blueprint.chapters for b in c.beats}
    char_ids = set(characters.characters)
    ids: list[str] = []
    for item in raw:
        oid = item.get('obligation_id')
        ids.append(oid)
        _reject_unknown_keys(errors, item, _OBLIGATION_KEYS, f'义务 {oid}')
        _reject_unknown_keys(errors, item.get('lineage') or {}, _LINEAGE_KEYS,
                             f'义务 {oid}.lineage')
        for ref in (item.get('lineage') or {}).get('blueprint_refs') or []:
            if ref not in beat_ids:
                errors.append(f'义务 {oid} lineage 引用未知 beat：{ref}')
        for ref in (item.get('lineage') or {}).get('character_ids') or []:
            if ref not in char_ids:
                errors.append(f'义务 {oid} lineage 引用未知人物：{ref}')
    if len(set(ids)) != len(ids):
        errors.append('obligation_id 重复')
    known = set(ids)
    for item in raw:
        for req in item.get('requires') or []:
            if req not in known:
                errors.append(f"义务 {item.get('obligation_id')} requires 引用未知义务：{req}")
    if errors:
        return None, errors
    # requires DAG 无环（存在即报，不给拓扑）
    graph: dict[str, list[str]] = {
        item['obligation_id']: list(item.get('requires') or []) for item in raw}
    for start in graph:
        seen: set[str] = set()
        stack = list(graph[start])
        while stack:
            node = stack.pop()
            if node == start or node in seen:
                errors.append(f'义务因果成环：{start} -> ... -> {node}')
                break
            seen.add(node)
            stack.extend(graph.get(node, []))
    if errors:
        return None, errors
    try:
        obligations = tuple(
            SourceObligation(
                obligation_id=o['obligation_id'], kind=o['kind'],
                statement=o['statement'],
                lineage=SourceLineage(
                    blueprint_refs=tuple((o.get('lineage') or {}).get('blueprint_refs') or []),
                    character_ids=tuple((o.get('lineage') or {}).get('character_ids') or [])),
                must_preserve=o['must_preserve'],
                satisfaction=o.get('satisfaction') or '',
                requires=tuple(o.get('requires') or []))
            for o in raw)
    except (ValidationError, KeyError) as exc:
        return None, [f'义务构造失败：{exc}']
    checkpoint = PreserveCheckpoint(
        checkpoint_id=f'cp_{policy_id}_v{policy_version}',
        policy_id=policy_id, policy_version=policy_version,
        characters_sha256=_sha256_payload(characters.model_dump()),
        blueprints_sha256=_sha256_payload(blueprint.model_dump()),
        provider=provider, model=model, request_sha256=request_sha256,
        obligations=obligations,
        checkpoint_sha256='0' * 64)  # 占位：先构造再回填内容 hash
    content_hash = _sha256_payload({
        'checkpoint_id': checkpoint.checkpoint_id,
        'policy': [policy_id, policy_version],
        'truth': [checkpoint.characters_sha256, checkpoint.blueprints_sha256],
        'obligations': [o.model_dump() for o in obligations]})
    return checkpoint.model_copy(update={'checkpoint_sha256': content_hash}), []


def project_brief(
    checkpoint: PreserveCheckpoint, characters: CharactersArtifactV4,
) -> tuple[ReconstructionBrief, tuple[IdentityProjection, ...]]:
    """checkpoint → (Brief, IdentityProjection)。Design 的全部语义输入。

    Brief 的 cores 只来自 kind=EVENT_CORE 的义务；非事件义务（身份/揭示/
    世界/因果约束类）由 Bind 直接对位到目标位置，不进事件骨架。
    """
    cores = tuple(
        EventCoreNote(
            core_id=o.obligation_id, summary=o.statement,
            participants=tuple(o.lineage.character_ids),
            requires_cores=tuple(o.requires))
        for o in checkpoint.obligations if o.kind is PreserveKind.EVENT_CORE)
    brief = ReconstructionBrief(
        brief_id=f'brief_{checkpoint.checkpoint_id}',
        preserve_checkpoint_sha256=checkpoint.checkpoint_sha256,
        event_cores=cores)
    identity = tuple(
        IdentityProjection(
            char_id=slug,
            identity_statement=(
                f"{entry.canonical}（可用称呼："
                + '、'.join(sorted({s.surface for s in entry.surfaces})) + '）'),
            stable_constraints=())
        for slug, entry in characters.characters.items())
    return brief, identity


def assemble_binding(
    wire: dict[str, Any], checkpoint: PreserveCheckpoint,
    known_scene_beats: dict[str, set[str]],
) -> tuple[SemanticBinding | None, list[str]]:
    """Bind wire（{'entries': [...], 'unbound': [...]}）→ 完整 SemanticBinding。

    校验：beat_ref 落真实拍（M4 面）、分母一致（复用 F1 checks）。
    known_scene_beats: scene_id → 该场景真实 beat_id 集合（来自 Design graph）。
    """
    errors: list[str] = []
    denominator = {o.obligation_id for o in checkpoint.obligations}
    for entry in wire.get('entries') or []:
        _reject_unknown_keys(errors, entry, _ENTRY_KEYS, 'entries')
        scene = entry.get('scene_id')
        if scene not in known_scene_beats:
            errors.append(f"entries 引用未知场景：{scene}")
            continue
        beat_ref = entry.get('beat_ref')
        if beat_ref is not None:
            beat = beat_ref.split('#', 1)[1] if '#' in beat_ref else ''
            if beat not in known_scene_beats[scene]:
                errors.append(f'承载 beat_ref 不是 {scene!r} 的真实拍：{beat_ref}')
    for unbound in wire.get('unbound') or []:
        _reject_unknown_keys(errors, unbound, _UNBOUND_KEYS, 'unbound')
        if unbound.get('obligation_id') not in denominator:
            errors.append(f"unbound 混入 checkpoint 外义务：{unbound.get('obligation_id')}")
    if errors:
        return None, errors
    try:
        entries = tuple(_binding_entry(e) for e in wire.get('entries') or [])
        unbound = tuple(_unbound_entry(u) for u in wire.get('unbound') or [])
        binding = SemanticBinding(
            binding_id=f'bind_{checkpoint.checkpoint_id}',
            preserve_checkpoint_sha256=checkpoint.checkpoint_sha256,
            entries=entries, unbound=unbound)
    except (ValidationError, KeyError) as exc:
        return None, [f'binding 构造失败：{exc}']
    problems = check_binding_covers_checkpoint(checkpoint, binding)
    if problems:
        return None, problems
    return binding, []


def _binding_entry(raw: dict[str, Any]):
    from contracts.schema.str06.grounding import BindingEntry
    return BindingEntry(
        obligation_id=raw['obligation_id'], scene_id=raw['scene_id'],
        beat_ref=raw.get('beat_ref'))


def _unbound_entry(raw: dict[str, Any]):
    from contracts.schema.str06.grounding import UnboundEntry
    return UnboundEntry(
        obligation_id=raw['obligation_id'], owner=raw['owner'], reason=raw['reason'])
