#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""grounding —— Preserve／Bind 的确定性组件（F3；B3 前半换协议）。

【分工】语义判断（哪些必保、如何承载）是 Preserve／Bind 两次 book-level LLM
调用的职责（harness 组装付费请求）；本模块是确定性臂：
  • `assemble_preserve`：Preserve wire → PreserveCheckpoint。B3 前半起模型
    不输出义务编号（I-21）：obligation_id 由 compiler 按列表序派发
    （`ob{序:02d}`），`requires` 用 1-based 列表序号表达、compiler 重写为
    正式 ID。校验：requires 引用闭合且无环、lineage 引用真实 beat/char、
    必保必有 satisfaction——后者由 schema 构造期保证；checkpoint hash 由
    规范化字节计算。
  • `project_brief`：checkpoint →（ReconstructionBrief, IdentityProjection）。
    Brief 投影全部类别及满足条件（去源坐标，引用改为不带源顺序的需求 ID）；
    Blueprint 的 world_constraints 机械填入 `book_constraints`（R2.5 世界
    约束通道——Design 侧决定沿用或改写成目标 world_rules）。
    IdentityProjection 从 Characters v4 公共投影。
    Design 拿到的是这两份 + OutputConstraints，**读不到 checkpoint 内部**。
  • `project_binding_space`：Bind 的输入投影（B3 前半）——义务清单（正式
    obligation_id 闭集）与场景/拍闭集（scene_id、每拍 beat_id＋event＋
    participants），不再向模型回显整个 Design。
  • `assemble_binding`：Bind wire → SemanticBinding。B3 前半起 wire 不写
    复合引用（I-21）：承载位置给 {obligation_id, scene, beat} 与支撑拍
    [{scene, beat}]——三个分量都只能取投影给出的闭集内单 ID，`场景#拍`
    复合引用由 compiler 组成。跨产物校验复用 F1 的
    check_binding_covers_checkpoint；完整发布，unbound 保留义务。

不修改输入；不落盘（落盘归 harness／repository）。
"""
from __future__ import annotations

import hashlib
from typing import Any

from contracts.schema.design import BodyDesign
from contracts.schema.grounding import (
    IdentityProjection, PreserveCheckpoint, ReconstructionBrief,
    SemanticBinding, SourceLineage, SourceObligation,
)
from contracts.schema.source import (
    BlueprintsArtifactV13, CharactersCore,
)
from contracts.schema.grounding import BriefRequirement
from contracts.schema.workflow import (
    STAGE4_BIND_PROTOCOL, STAGE4_PRESERVE_PROTOCOL,
)
from novel.stages.common import check_binding_covers_checkpoint
from pydantic import ValidationError


def _sha256_payload(obj: Any) -> str:
    import json
    return hashlib.sha256(
        json.dumps(obj, ensure_ascii=False, sort_keys=True).encode('utf-8')).hexdigest()


#: wire 键白名单（Q-1：多余键在 compile 入口报错，不静默丢弃）。
#: B3 前半：Preserve 不收 obligation_id（compiler 派发），requires 是 1-based
#: 序号数组；Bind 的承载位置是 {scene, beat} 单 ID 分量（复合引用 compiler 组）。
_OBLIGATION_KEYS = frozenset({'kind', 'statement', 'lineage',
                              'must_preserve', 'satisfaction', 'requires'})
_LINEAGE_KEYS = frozenset({'blueprint_refs', 'character_ids'})
_ENTRY_KEYS = frozenset({'obligation_id', 'scene', 'beat', 'supporting'})
_SUPPORTING_KEYS = frozenset({'scene', 'beat'})
_UNBOUND_KEYS = frozenset({'obligation_id', 'owner', 'reason'})


def _reject_unknown_keys(errors: list[str], obj: dict[str, Any], allowed: frozenset[str],
                         where: str) -> None:
    extra = set(obj) - allowed
    if extra:
        errors.append(f'{where} 携带未知字段 {sorted(extra)}（wire 键白名单外）')


def assemble_preserve(
    wire: dict[str, Any], *,
    policy_id: str, policy_version: int,
    characters: CharactersCore, blueprint: BlueprintsArtifactV13,
    provider: str, model: str, request_sha256: str,
) -> tuple[PreserveCheckpoint | None, list[str]]:
    """Preserve wire（stage4-preserve-v1：{'obligations': [...]}）→ checkpoint。

    确定性校验：协议版本、requires 序号闭合无环、lineage 的 beat/char 引用
    真实；义务编号由 compiler 按列表序派发（I-21），模型不输出 ID。
    `characters_sha256` 绑定 core view（characters + initial_state）——名称结构
    修订不使已购 checkpoint 失效（F6 裁决）。"""
    errors: list[str] = []
    if wire.get('protocol_version') != STAGE4_PRESERVE_PROTOCOL:
        errors.append(f'protocol_version 必须是 {STAGE4_PRESERVE_PROTOCOL}，'
                      f'实得 {wire.get("protocol_version")!r}（I-13 无隐式迁移）')
    _reject_unknown_keys(errors, wire, frozenset({'protocol_version', 'obligations'}),
                         'Preserve')
    raw = wire.get('obligations') or []
    if not raw:
        return None, ['obligations 缺失或为空'] + errors
    beat_ids = {b.beat_id for c in blueprint.chapters for b in c.beats}
    char_ids = set(characters.characters)
    for ordinal, item in enumerate(raw, start=1):
        _reject_unknown_keys(errors, item, _OBLIGATION_KEYS, f'义务 {ordinal}')
        _reject_unknown_keys(errors, item.get('lineage') or {}, _LINEAGE_KEYS,
                             f'义务 {ordinal}.lineage')
        for ref in (item.get('lineage') or {}).get('blueprint_refs') or []:
            if ref not in beat_ids:
                errors.append(f'义务 {ordinal} lineage 引用未知 beat：{ref}')
        for ref in (item.get('lineage') or {}).get('character_ids') or []:
            if ref not in char_ids:
                errors.append(f'义务 {ordinal} lineage 引用未知人物：{ref}')
    if errors:
        return None, errors
    # ID 派发（I-21）：obligation_id 按列表序生成；requires 是 1-based 列表
    # 序号，compiler 重写为正式 ID——自指与越界在此精确拒。
    dispatched = [f'ob{i:02d}' for i in range(1, len(raw) + 1)]
    graph: dict[str, list[str]] = {}
    for ordinal, (item, oid) in enumerate(zip(raw, dispatched), start=1):
        requires = item.get('requires') or []
        resolved: list[str] = []
        for ref in requires:
            if not isinstance(ref, int) or isinstance(ref, bool) or not 1 <= ref <= len(raw):
                errors.append(f'义务 {ordinal} 的 requires 序号越界：{ref!r}'
                              f'（1-{len(raw)}；不得自指）')
                continue
            if ref == ordinal:
                errors.append(f'义务 {ordinal} 的 requires 不得自指')
                continue
            resolved.append(dispatched[ref - 1])
        graph[oid] = resolved
    if errors:
        return None, errors
    # requires DAG 无环（存在即报，不给拓扑）
    for start in graph:
        seen: set[str] = set()
        stack: list[str] = list(graph[start])
        while stack:
            node = stack.pop()
            if node == start:
                errors.append(f'义务因果成环：{start} -> ... -> {node}')
                break
            if node in seen:
                continue
            seen.add(node)
            stack.extend(graph.get(node, ()))
    if errors:
        return None, errors
    try:
        obligations = tuple(
            SourceObligation(
                obligation_id=oid, kind=o['kind'],
                statement=o['statement'],
                lineage=SourceLineage(
                    blueprint_refs=tuple((o.get('lineage') or {}).get('blueprint_refs') or []),
                    character_ids=tuple((o.get('lineage') or {}).get('character_ids') or [])),
                must_preserve=o['must_preserve'],
                satisfaction=o.get('satisfaction') or '',
                requires=tuple(graph[oid]))
            for oid, o in zip(dispatched, raw))
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
    checkpoint: PreserveCheckpoint, characters: CharactersCore,
    blueprint: BlueprintsArtifactV13 | None = None,
) -> tuple[ReconstructionBrief, tuple[IdentityProjection, ...]]:
    """checkpoint → (Brief, IdentityProjection)。Design 的全部语义输入。

    所有义务类型、must 与 satisfaction 无损进入 source-blind Brief；因果闭合。
    `book_constraints`（R2.5 世界约束通道）：Blueprint 的 world_constraints
    的 statement 原样机械填入（AUD 反例「世界约束没有通道」——恒空即红）；
    目标侧是否沿用或改写由 Design 决定（wire v2 的 world_rules），compiler
    不做语义推断。`blueprint` 缺省 None 仅供不触世界约束的存量调用，正式
    链路（run_preserve／run_body_design）必须传入。
    """
    # 不以源义务序号暴露叙述顺序；所有类型与满足条件等量投影，依赖闭合。
    core_ids = {o.obligation_id: 'req_' + hashlib.sha256(
        o.obligation_id.encode('utf-8')).hexdigest()[:16] for o in checkpoint.obligations}
    cores = tuple(sorted((
        BriefRequirement(
            core_id=core_ids[o.obligation_id], summary=o.statement,
            kind=o.kind, must_preserve=o.must_preserve, satisfaction=o.satisfaction,
            participants=tuple(sorted(o.lineage.character_ids)),
            requires_cores=tuple(sorted(core_ids[r] for r in o.requires)))
        for o in checkpoint.obligations), key=lambda c: c.core_id))
    brief = ReconstructionBrief(
        brief_id=f'brief_{checkpoint.checkpoint_id}',
        preserve_checkpoint_sha256=checkpoint.checkpoint_sha256,
        requirements=cores,
        book_constraints=tuple(wc.statement for wc in blueprint.world_constraints)
        if blueprint is not None else ())
    identity = tuple(
        IdentityProjection(
            char_id=slug,
            identity_statement=(
                f"{entry.canonical}（可用称呼："
                + '、'.join(sorted({s.surface for s in entry.surfaces})) + '）'),
            stable_constraints=tuple(
                f'{a.key}={a.value}' for a in characters.initial_state
                if a.persistence == 'stable' and a.target.kind == 'character'
                and a.target.char_id == slug))
        for slug, entry in characters.characters.items())
    return brief, identity


def project_binding_space(
    checkpoint: PreserveCheckpoint, body: BodyDesign,
) -> dict[str, Any]:
    """Bind 的输入投影（B3 前半）：承载选择的最小闭集，不回显整个 Design。

    模型需要的是「每条义务要保什么」与「每个拍发生了什么」——driver、导演
    说明、delta 全量与称谓释放都不进承载选择的输入。义务 id 与场景/拍 id 是
    compiler 派发的正式 ID（闭集选择，不是模型生成）。纯函数。"""
    return {
        'protocol_version': STAGE4_BIND_PROTOCOL,
        'obligations': [
            {'obligation_id': o.obligation_id, 'kind': o.kind.value
             if hasattr(o.kind, 'value') else str(o.kind),
             'statement': o.statement, 'must_preserve': o.must_preserve,
             'satisfaction': o.satisfaction, 'requires': list(o.requires)}
            for o in checkpoint.obligations],
        'scenes': [
            {'scene_id': scene.scene_id,
             'beats': [{'beat_id': beat.beat_id, 'event': beat.event,
                        'participants': list(beat.participants)}
                       for beat in scene.beats]}
            for scene in body.scenes],
    }


def assemble_binding(
    wire: dict[str, Any], checkpoint: PreserveCheckpoint,
    known_scene_beats: dict[str, set[str]],
) -> tuple[SemanticBinding | None, list[str]]:
    """Bind wire（stage4-bind-v1）→ 完整 SemanticBinding。

    wire 的承载位置是 {obligation_id, scene, beat}（支撑拍同）——三个分量都
    只能取投影给出的闭集内单 ID；`场景#拍` 复合引用由 compiler 组成（I-21，
    模型不写复合引用）。校验：分量落真实拍（M4 面）、分母一致（复用 F1
    checks）。known_scene_beats: scene_id → 该场景真实 beat_id 集合（来自
    Design canonical）。
    """
    errors: list[str] = []
    if wire.get('protocol_version') != STAGE4_BIND_PROTOCOL:
        errors.append(f'protocol_version 必须是 {STAGE4_BIND_PROTOCOL}，'
                      f'实得 {wire.get("protocol_version")!r}（I-13 无隐式迁移）')
    _reject_unknown_keys(errors, wire, frozenset({'protocol_version', 'entries', 'unbound'}),
                         'Bind')
    denominator = {o.obligation_id for o in checkpoint.obligations}

    def _ref(item: dict[str, Any], where: str) -> str | None:
        """{scene, beat} 分量 → `场景#拍` 复合引用；闭集外精确拒。"""
        scene, beat = item.get('scene'), item.get('beat')
        if scene not in known_scene_beats:
            errors.append(f'{where} 引用未知场景：{scene}')
            return None
        if beat not in known_scene_beats[scene]:
            errors.append(f'{where} 的拍不是 {scene!r} 的真实拍：{beat}')
            return None
        return f'{scene}#{beat}'

    canonical_entries: list[dict[str, Any]] = []
    for entry in wire.get('entries') or []:
        _reject_unknown_keys(errors, entry, _ENTRY_KEYS, 'entries')
        if entry.get('obligation_id') not in denominator:
            errors.append(f"entries 混入 checkpoint 外义务：{entry.get('obligation_id')}")
        ref = _ref(entry, f"承载 {entry.get('obligation_id')}")
        if ref is None:
            continue
        supporting: list[str] = []
        for raw in entry.get('supporting') or []:
            if not isinstance(raw, dict):
                errors.append(f"承载 {entry.get('obligation_id')} 的支撑拍形状非法：{raw!r}")
                continue
            _reject_unknown_keys(errors, raw, _SUPPORTING_KEYS, 'supporting')
            supported = _ref(raw, f"支撑 {entry.get('obligation_id')}")
            if supported is not None:
                supporting.append(supported)
        canonical_entries.append({'obligation_id': entry.get('obligation_id'),
                                  'scene_id': entry.get('scene'), 'beat_ref': ref,
                                  'supporting_beat_refs': supporting})
    for unbound in wire.get('unbound') or []:
        _reject_unknown_keys(errors, unbound, _UNBOUND_KEYS, 'unbound')
        if unbound.get('obligation_id') not in denominator:
            errors.append(f"unbound 混入 checkpoint 外义务：{unbound.get('obligation_id')}")
    if errors:
        return None, errors
    try:
        entries = tuple(_binding_entry(e) for e in canonical_entries)
        unbound = tuple(_unbound_entry(u) for u in wire.get('unbound') or [])
        binding = SemanticBinding(
            binding_id=f'bind_{checkpoint.checkpoint_id}',
            preserve_checkpoint_sha256=checkpoint.checkpoint_sha256,
            obligations=checkpoint.obligations, entries=entries, unbound=unbound)
    except (ValidationError, KeyError) as exc:
        return None, [f'binding 构造失败：{exc}']
    problems = check_binding_covers_checkpoint(checkpoint, binding)
    if problems:
        return None, problems
    return binding, []


def _binding_entry(raw: dict[str, Any]):
    from contracts.schema.grounding import BindingEntry
    return BindingEntry(
        obligation_id=raw['obligation_id'], scene_id=raw['scene_id'],
        beat_ref=raw['beat_ref'],
        supporting_beat_refs=tuple(raw.get('supporting_beat_refs') or ()))

def _unbound_entry(raw: dict[str, Any]):
    from contracts.schema.grounding import UnboundEntry
    return UnboundEntry(
        obligation_id=raw['obligation_id'], owner=raw['owner'], reason=raw['reason'])
