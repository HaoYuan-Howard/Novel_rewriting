#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""grounding —— Preserve／Brief 的确定性组件与 bind 派生（F3；B3 前半换协议；
CS-3 bind LLM 退役）。

【分工】语义判断（哪些必保）是 Preserve 一次 book-level LLM 调用的职责
（harness 组装付费请求）；本模块是确定性臂：
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
  • `derive_semantic_binding`：design 的 real 承载标注 → SemanticBinding
    （CS-3，2026-10-03）：bind LLM 退役——义务落在哪一拍由 design wire 的
    `real:["obNN"]` 标注唯一声明（末次标注拍＝接受拍，其余＝支撑拍；无标注
    的非必保义务进 unbound，义务不删）；series 成员接受拍互斥（C3/C5 承载
    面）随 wire 判据迁入。承载位不再有第二个声明处，SemanticBinding 的
    唯一 writer 是 `str06.stage4.body_design`（双产物同组发布）。

不修改输入；不落盘（落盘归 harness／repository）。
"""
from __future__ import annotations

import hashlib
from typing import Any, Final, Iterable

from contracts.schema.design import BodyDesign
from contracts.schema.grounding import (
    BriefFactLock, BriefKnowledgeChange, BriefKnowledgeLock, BriefProposition, BriefRequirement,
    FactLockBinding, IdentityProjection, ObligationAnchor, ObligationSeries, PreserveCheckpoint,
    ReconstructionBrief, SemanticBinding, SourceLineage, SourceObligation,
    BookConstraint, _lock_id,
)
from contracts.schema.source import (
    BlueprintsArtifactV17, CharactersCore,
)
from contracts.schema.workflow import STAGE4_PRESERVE_PROTOCOL
from novel.core.name_materials import (
    family_form_literals, withheld_name_bindings,
)
from novel.core.validation import ValidationIssue
from novel.stages.common import (
    check_binding_covers_checkpoint, claim_spans, uncovered_occurrences,
)
from novel.stages.stage4.design_paths import design_issue
from novel.stages.stage4.fact_bindings import (
    fact_binding_errors, fact_lock_carrier_errors, fact_lock_subject_errors,
)
from pydantic import ValidationError


def _sha256_payload(obj: Any) -> str:
    import json
    return hashlib.sha256(
        json.dumps(obj, ensure_ascii=False, sort_keys=True).encode('utf-8')).hexdigest()


#: wire 键白名单（Q-1：多余键在 compile 入口报错，不静默丢弃）。
#: B3 前半：Preserve 不收 obligation_id（compiler 派发），requires 是 1-based
#: 序号数组。R10a a2：义务以 anchors 引用投影句柄，lineage 退出 wire
#: （compiler 派生）；顶层增 series／evidence_conflicts。
_OBLIGATION_KEYS = frozenset({'kind', 'statement', 'anchors',
                              'must_preserve', 'satisfaction', 'requires'})
_SERIES_KEYS = frozenset({'members', 'mechanism'})
_CONFLICT_KEYS = frozenset({'anchor', 'segment', 'note'})


def _reject_unknown_keys(errors: list[str], obj: dict[str, Any], allowed: frozenset[str],
                         where: str) -> None:
    extra = set(obj) - allowed
    if extra:
        errors.append(f'{where} 携带未知字段 {sorted(extra)}（wire 键白名单外）')


# ── R10a a2：Preserve 输入投影（project_preserve_view）与句柄解析 ──────────


def _render_target(target: Any) -> str:
    """typed 作用对象 → `char:x`／`rel:a×b`／`world`（本模块自带渲染；I-04
    禁止反向消费 Stage2 内部函数）。"""
    if target.kind == 'character':
        return f'char:{target.char_id}'
    if target.kind == 'relation':
        return f'rel:{target.subject}×{target.object}'
    return 'world'


def _anchor_index(
    characters: CharactersCore, blueprint: BlueprintsArtifactV17,
) -> dict[str, ObligationAnchor]:
    """投影句柄 → 类型化拷贝（compiler 拷贝，模型不复述；I-15/I-21）。

    句柄闭集＝`s0_*`（初态）、`wc_*`（世界约束）、`b拍.eN/.cN/.lN`（拍内
    效应/认知/悬念）。命题带正式 ID（k*，无本地句柄）；初态/约束的地址
    含 state_id／constraint_id 本体。
    """
    index: dict[str, ObligationAnchor] = {}
    for atom in characters.initial_state:
        index[atom.state_id] = ObligationAnchor(
            handle=atom.state_id, source_kind='initial_state',
            address=f'{_render_target(atom.target)}.{atom.key}', value=atom.value,
            quotes=tuple(b.quote for b in atom.basis))
    for rule in blueprint.world_constraints:
        index[rule.constraint_id] = ObligationAnchor(
            handle=rule.constraint_id, source_kind='world_constraint',
            address=f'{rule.kind}:{rule.constraint_id}', value=rule.statement,
            quotes=tuple(b.quote for b in rule.basis))
    for chapter_bp in sorted(blueprint.chapters, key=lambda c: c.chapter):
        for beat in chapter_bp.beats:
            for n, effect in enumerate(beat.effects, 1):
                handle = f'{beat.beat_id}.e{n}'
                # CS-S 1b：effect 删 disclosure／proposition——锚点不再拷贝
                # 可见性与命题（命题锚只在 cognition 上）。
                index[handle] = ObligationAnchor(
                    handle=handle, source_kind='effect',
                    address=f'{_render_target(effect.target)}.{effect.key}',
                    value=effect.value, modality=effect.modality,
                    op=effect.op,
                    quotes=tuple(b.quote for b in effect.basis))
            for n, cognition in enumerate(beat.cognition, 1):
                handle = f'{beat.beat_id}.c{n}'
                index[handle] = ObligationAnchor(
                    handle=handle, source_kind='cognition',
                    holder=cognition.holder, mode=cognition.mode,
                    proposition_ref=cognition.proposition.ref,
                    proposition_statement=cognition.proposition.statement,
                    quotes=tuple(b.quote for b in cognition.basis))
            for n, loop in enumerate(beat.loops, 1):
                handle = f'{beat.beat_id}.l{n}'
                index[handle] = ObligationAnchor(
                    handle=handle, source_kind='loop',
                    question=loop.question or loop.resolution or '')
    return index


def _derive_lineage(
    handles: tuple[str, ...], index: dict[str, ObligationAnchor],
    characters: CharactersCore, blueprint: BlueprintsArtifactV17,
) -> SourceLineage:
    """义务 anchors → SourceLineage（I-15/I-21：compiler 派生，wire 不再输出）。

    blueprint_refs＝锚点主体（拍内转移归其 beat_id；初态/约束归自身 id）；
    character_ids＝初态 target 人物＋效应作用对象人物＋人物认知 holder——同一
    既有语义（可综合多个源主体），只是来源从模型声明改为锚点解析。
    """
    blueprint_refs: list[str] = []
    character_ids: list[str] = []
    initial_by_handle = {atom.state_id: atom for atom in characters.initial_state}
    transfers: dict[str, Any] = {}
    for chapter_bp in blueprint.chapters:
        for beat in chapter_bp.beats:
            for n, effect in enumerate(beat.effects, 1):
                transfers[f'{beat.beat_id}.e{n}'] = effect
            for n, cognition in enumerate(beat.cognition, 1):
                transfers[f'{beat.beat_id}.c{n}'] = cognition
            for n, loop in enumerate(beat.loops, 1):
                transfers[f'{beat.beat_id}.l{n}'] = loop
    for handle in handles:
        anchor = index[handle]
        if '.' in handle:
            blueprint_refs.append(handle.split('.', 1)[0])
            transfer = transfers.get(handle)
        else:
            blueprint_refs.append(handle)
            transfer = initial_by_handle.get(handle)
        if transfer is None:
            continue
        if anchor.source_kind == 'cognition':
            # reader 是知情持有者，人物表没有它；其锁仍由 cognition anchor
            # 派生，不能经 lineage 变成 Brief 的参演人物。
            if transfer.holder != 'reader' and transfer.holder in characters.characters:
                character_ids.append(transfer.holder)
        elif anchor.source_kind == 'initial_state':
            target = transfer.target
            if target.kind == 'character':
                character_ids.append(target.char_id)
            elif target.kind == 'relation':
                character_ids.extend((target.subject, target.object))
        elif anchor.source_kind == 'effect':
            target = transfer.target
            if target.kind == 'character':
                character_ids.append(target.char_id)
            elif target.kind == 'relation':
                character_ids.extend((target.subject, target.object))
    deduped = []
    for ref in blueprint_refs:
        if ref not in deduped:
            deduped.append(ref)
    people = []
    for person in character_ids:
        if person not in people:
            people.append(person)
    return SourceLineage(blueprint_refs=tuple(deduped), character_ids=tuple(people))


def project_preserve_view(
    characters: CharactersCore, blueprint: BlueprintsArtifactV17,
) -> str:
    """R10a a2：Preserve 的源证据投影（确定性渲染，取代 Blueprint JSON 倾倒）。

    按书级初态 → 世界约束 → 逐章逐拍渲染：每原子一行带投影句柄＋类型化内容
    ＋逐字引文；拍头带 event 与拍 source_refs 的证据窗口（Blueprint.evidence
    的窗口文本＝被引段全文＋同章前后各一段）。不摘要、不改写（I-24 不改输入；
    I-20/I-21 机械渲染归 compiler）。R10-0.3 实证旧形态整份 Blueprint JSON
    倾倒 29.9k token/轮——本投影只给语义判断实际需要的原子与证据。
    """
    lines: list[str] = ['输入版本：stage4-preserve-input-v2']
    cited: set[str] = set()
    if characters.initial_state:
        lines.append('【书级初始事实（故事开始前成立）】')
        for atom in characters.initial_state:
            cited.update(atom.source_refs)
            # CS-S 1b：初始已知后缀删（initial_state.knowledge 已删）。语义缺口
            # 如实登记：Phase 2 的 facts `known_at_start` 只落在 fold 知情时间线读数
            # （T-5：只读读数，不进下游 API），Stage4 的 Preserve／Brief 仍看不到
            # 「开场前已知」——是否把它渲染进 Preserve 视图待产品裁决（REQUIREMENTS）。
            lines.append(f'[{atom.state_id}] {_render_target(atom.target)}.'
                         f'{atom.key} = {atom.value!r}（{atom.persistence}）')
            for basis in atom.basis:
                lines.append(f'  依据 {basis.segment}：「{basis.quote}」')
    if blueprint.world_constraints:
        lines.append('【世界约束】')
        for rule in blueprint.world_constraints:
            cited.update(rule.source_refs)
            lines.append(f'[{rule.constraint_id}] {rule.kind}：{rule.statement}')
            for basis in rule.basis:
                lines.append(f'  依据 {basis.segment}：「{basis.quote}」')
    if blueprint.source_expression.items:
        lines.append('【源表达机制（series 的 mechanism 闭集）】')
        for item in blueprint.source_expression.items:
            lines.append(f'{item.ex_id}（{item.kind}）：{item.statement}')
    for chapter_bp in sorted(blueprint.chapters, key=lambda c: c.chapter):
        lines.append(f'【第 {chapter_bp.chapter} 章】')
        scene_of = {bid: span.scene_id
                    for span in chapter_bp.scenes for bid in span.beat_ids}
        for beat in chapter_bp.beats:
            scene_note = scene_of.get(beat.beat_id, '?')
            lines.append(f'拍 {beat.beat_id}（场景 {scene_note}；'
                         f'{beat.scene_boundary}；{beat.temporal_mode}；'
                         f'{"；".join(beat.participants) or "无人物"}；'
                         f'来源 {",".join(beat.source_refs)}）：{beat.event}')
            bases: dict[tuple[str, str], list[str]] = {}
            for letter, transfers in (('e', beat.effects), ('c', beat.cognition),
                                      ('l', beat.loops)):
                for n, transfer in enumerate(transfers, 1):
                    handle = f'{beat.beat_id}.{letter}{n}'
                    lines.append(f'  [{handle}] {_transfer_line(letter, transfer)}')
                    for basis in getattr(transfer, 'basis', ()):
                        bases.setdefault((basis.segment, basis.quote), []).append(handle)
            for (segment, quote), handles in bases.items():
                cited.add(segment)
                lines.append(f'  依据 [{",".join(handles)}] {segment}：「{quote}」')
            for ref in beat.source_refs:
                cited.add(ref)
        for edge in chapter_bp.causal_edges:
            lines.append(f'因果 {edge.from_beat} —{edge.kind}→ {edge.to_beat}')
    lines.append('【证据窗口（被引句段全文，含同章前后各一句）】')
    for ref in sorted(cited):
        window = blueprint.evidence.get(ref)
        if window:
            lines.append(f'{ref}：{window}')
    return '\n'.join(lines)


def _transfer_line(kind: str, transfer: Any) -> str:
    """直接投影正式转移；锚点摘要不含完整作用域，不能充当输入真相。"""
    if kind == 'e':
        return (f'事实 {_render_target(transfer.target)}.{transfer.key}'
                f' {transfer.op} {transfer.value!r}（{transfer.modality}）')
    if kind == 'c':
        p = transfer.proposition
        return (f'认知 {transfer.holder} {transfer.mode} 命题 {p.ref}'
                + (f'「{p.statement}」' if p.statement is not None else '（引用）'))
    return (f'悬念 {transfer.ref} {transfer.op}：'
            f'{transfer.question if transfer.op == "open" else transfer.resolution}')



def preserve_revision_wire(checkpoint: PreserveCheckpoint) -> dict[str, Any]:
    """正式 checkpoint → 同协议的修订对照；只撤销 ID/锚点/lineage 派生。

    保留所有语义字段与列表顺序，不信任模型回写的派生元数据；本投影不是
    输入迁移或 validator repair。调用方在显式 owner 返修步骤使用（I-15/I-35）。
    """
    ordinals = {o.obligation_id: n for n, o in enumerate(checkpoint.obligations, 1)}
    if len(ordinals) != len(checkpoint.obligations):
        raise ValueError('Preserve 修订投影拒绝重复 obligation_id')
    refs = [ref for o in checkpoint.obligations for ref in o.requires]
    refs.extend(ref for group in checkpoint.series for ref in group.members)
    if any(ref not in ordinals for ref in refs):
        raise ValueError('Preserve 修订投影拒绝闭集外义务引用')
    return {
        'protocol_version': STAGE4_PRESERVE_PROTOCOL,
        'obligations': [
            {'kind': o.kind.value, 'statement': o.statement,
             'anchors': [a.handle for a in o.anchors],
             'must_preserve': o.must_preserve, 'satisfaction': o.satisfaction,
             'requires': [ordinals[ref] for ref in o.requires]}
            for o in checkpoint.obligations],
        'series': [{'members': [ordinals[ref] for ref in group.members],
                    'mechanism': group.mechanism} for group in checkpoint.series],
        'evidence_conflicts': [],
    }


def _preserve_shape_errors(wire: Any) -> list[str]:
    """在 get/迭代/hash 前验证不可信 JSON 容器；不改输入，不吞校验错误。"""
    if not isinstance(wire, dict):
        return ['Preserve 必须是 JSON 对象']
    errors: list[str] = []
    for key in ('obligations', 'series', 'evidence_conflicts'):
        items = wire.get(key)
        if items is None:
            continue
        if not isinstance(items, list):
            errors.append(f'{key} 必须是数组')
            continue
        for n, item in enumerate(items, 1):
            path = f'{key}[{n}]'
            if not isinstance(item, dict):
                errors.append(f'{path} 必须是对象')
                continue
            if key == 'obligations':
                anchors = item.get('anchors')
                if anchors is not None and (
                    not isinstance(anchors, list)
                    or any(not isinstance(a, str) for a in anchors)
                ):
                    errors.append(f'{path}.anchors 必须是句柄字符串数组（不是锚点对象）')
                requires = item.get('requires')
                if requires is not None and not isinstance(requires, list):
                    errors.append(f'{path}.requires 必须是序号数组')
            elif key == 'series':
                members = item.get('members')
                if members is not None and not isinstance(members, list):
                    errors.append(f'{path}.members 必须是序号数组')
                mechanism = item.get('mechanism')
                if mechanism is not None and not isinstance(mechanism, str):
                    errors.append(f'{path}.mechanism 必须是 ex_id 字符串或 null')
            else:
                for field in ('anchor', 'segment', 'note'):
                    if field in item and not isinstance(item[field], str):
                        errors.append(f'{path}.{field} 必须是字符串')
    return errors


def assemble_preserve(
    wire: Any, *,
    policy_id: str, policy_version: int,
    characters: CharactersCore, blueprint: BlueprintsArtifactV17,
    provider: str, model: str, request_sha256: str,
) -> tuple[PreserveCheckpoint | None, list[str]]:
    """Preserve wire（stage4-preserve-v2）→ checkpoint。

    确定性校验：协议版本、义务 anchors 在投影句柄闭集内（每条义务至少锚定
    一个原子）、requires 序号闭合无环、series 成员闭集互斥且 mechanism 属源
    表达、evidence_conflicts 的锚点与句段在闭集且句段属该拍证据。义务编号
    由 compiler 按列表序派发（I-21）；lineage 由 anchors 派生（I-15）。
    `characters_sha256` 绑定 core view（F6 裁决）。冲突非空时本函数照常返回
    校验结果——「冲突不发布 checkpoint、回派 Stage2」是 a3 `run_preserve` 的
    编排决定，不混入 validator（I-36）。"""
    errors = _preserve_shape_errors(wire)
    if errors:
        return None, errors
    if wire.get('protocol_version') != STAGE4_PRESERVE_PROTOCOL:
        errors.append(f'protocol_version 必须是 {STAGE4_PRESERVE_PROTOCOL}，'
                      f'实得 {wire.get("protocol_version")!r}（I-13 无隐式迁移）')
    _reject_unknown_keys(errors, wire,
                         frozenset({'protocol_version', 'obligations', 'series',
                                    'evidence_conflicts'}),
                         'Preserve')
    raw = wire.get('obligations') or []
    if not raw:
        return None, ['obligations 缺失或为空'] + errors
    index = _anchor_index(characters, blueprint)
    beat_refs: dict[str, tuple[str, ...]] = {}
    for chapter_bp in blueprint.chapters:
        for beat in chapter_bp.beats:
            beat_refs[beat.beat_id] = tuple(beat.source_refs)
    all_segments: set[str] = set(blueprint.evidence)
    for ordinal, item in enumerate(raw, start=1):
        _reject_unknown_keys(errors, item, _OBLIGATION_KEYS, f'义务 {ordinal}')
        anchors = item.get('anchors')
        if not anchors:
            errors.append(f'义务 {ordinal} 缺 anchors——每条义务必须锚定其依赖的'
                          '源原子（至少 1 个投影句柄）')
            continue
        for handle in anchors:
            if handle not in index:
                errors.append(f'义务 {ordinal} anchors 引用未知句柄：{handle!r}'
                              '（只能取投影视图给出的 s0_*/wc_*/b拍.e|c|l 句柄）')
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
                lineage=_derive_lineage(tuple(o.get('anchors') or ()), index,
                                        characters, blueprint),
                anchors=tuple(index[h] for h in o.get('anchors') or ()),
                must_preserve=o['must_preserve'],
                satisfaction=o.get('satisfaction') or '',
                requires=tuple(graph[oid]))
            for oid, o in zip(dispatched, raw))
    except (ValidationError, KeyError) as exc:
        return None, [f'义务构造失败：{exc}']
    # series：成员序号（1-based）→ 正式 ID；闭集、互斥；mechanism 属源表达。
    ex_ids = {item.ex_id for item in blueprint.source_expression.items}
    series_list: list[ObligationSeries] = []
    for n, group in enumerate(wire.get('series') or (), 1):
        _reject_unknown_keys(errors, group, _SERIES_KEYS, f'series {n}')
        members_raw = group.get('members') or []
        resolved_members: list[str] = []
        for ref in members_raw:
            if not isinstance(ref, int) or isinstance(ref, bool) or not 1 <= ref <= len(raw):
                errors.append(f'series {n} 成员序号越界：{ref!r}（1-{len(raw)}）')
                continue
            resolved_members.append(dispatched[ref - 1])
        if len(resolved_members) < 2:
            errors.append(f'series {n} 至少 2 个成员（离散兑现时点分别登记，'
                          '不为满足阶段规则合并）')
        mechanism = group.get('mechanism')
        if mechanism is not None and mechanism not in ex_ids:
            errors.append(f'series {n} mechanism 不在源表达 ex_id 闭集：'
                          f'{mechanism!r}')
        if not errors:
            try:
                series_list.append(ObligationSeries(
                    members=tuple(resolved_members), mechanism=mechanism))
            except ValidationError as exc:
                errors.append(f'series {n} 模型校验失败：{exc}')
    if errors:
        return None, errors
    # evidence_conflicts：只验形状与闭集（a3 消费——冲突不发布、回派 Stage2）。
    for n, conflict in enumerate(wire.get('evidence_conflicts') or (), 1):
        _reject_unknown_keys(errors, conflict, _CONFLICT_KEYS, f'evidence_conflicts {n}')
        anchor = conflict.get('anchor')
        if anchor not in index:
            errors.append(f'evidence_conflicts {n} 引用未知锚点：{anchor!r}')
            continue
        segment = conflict.get('segment')
        if segment not in all_segments:
            errors.append(f'evidence_conflicts {n} 引用未知句段：{segment!r}')
            continue
        beat_id = anchor.split('.', 1)[0] if '.' in anchor else None
        refs = beat_refs.get(beat_id) if beat_id else None
        if refs is not None and segment not in refs:
            errors.append(f'evidence_conflicts {n} 的句段 {segment!r} 不属于锚点'
                          f' {anchor!r} 所在拍的证据 {list(refs)}')
        if not str(conflict.get('note') or '').strip():
            errors.append(f'evidence_conflicts {n} 缺 note（分歧描述）')
    if errors:
        return None, errors
    try:
        checkpoint = PreserveCheckpoint(
            checkpoint_id=f'cp_{policy_id}_v{policy_version}',
            policy_id=policy_id, policy_version=policy_version,
            characters_sha256=_sha256_payload(characters.model_dump()),
            blueprints_sha256=_sha256_payload(blueprint.model_dump()),
            provider=provider, model=model, request_sha256=request_sha256,
            obligations=obligations, series=tuple(series_list),
            checkpoint_sha256='0' * 64)  # 占位：先构造再回填内容 hash
    except ValidationError as exc:
        # R10c 硬化（2026-10-02）：构造期模型校验（如 series 互斥）此前裸穿成
        # exit 70「结论未知」，修订环拿不到反馈只能崩。归入 errors 让 preserve
        # 的同节点修订轮带精确问题重问（R10c1b 实证：ob44 进了两个 series）。
        return None, [f'preserve wire 模型校验失败：{exc}']
    content_hash = _sha256_payload({
        'checkpoint_id': checkpoint.checkpoint_id,
        'policy': [policy_id, policy_version],
        'truth': [checkpoint.characters_sha256, checkpoint.blueprints_sha256],
        'obligations': [o.model_dump() for o in obligations],
        'series': [s.model_dump() for s in series_list]})
    return checkpoint.model_copy(update={'checkpoint_sha256': content_hash}), []


def obligation_core_id(obligation_id: str) -> str:
    """源义务 id（obNN，近似源文顺序）→ Design 可见的源盲 core id。

    唯一铸造口：Brief 投影（`project_brief`）与跨节点回派（compile 的
    `must_keep_unbound` 定位）共用同一映射——Design 源盲，它的修订提示里
    不出现源序号（R8a-3）。确定性纯函数（同一 id 恒同一 core id）。
    """
    return 'req_' + hashlib.sha256(
        obligation_id.encode('utf-8')).hexdigest()[:16]


#: 源认知模式 → 知情锁目标模式的唯一映射（a4，PLAN §二.1）：learns/confirms→
#: known、suspects→suspected、hears_claim→heard、believes→believes。asserts
#: 不在表内——公开声称是「某人声称」的世界事实（命题登记带
#: publicly_claimed_by），不铸任何持有者的知情锁；public 持有者（公众知情）
#: 同理不铸锁（目标侧经世界事实表达）。a5 起锁位置 Guard（guard.py）复用
#: 本表解析义务 anchors 的授权三元组——同一映射，不复制第二份。
KNOWLEDGE_LOCK_MODE: Final[dict[str, str]] = {
    'learns': 'known', 'confirms': 'known',
    'suspects': 'suspected', 'hears_claim': 'heard', 'believes': 'believes',
}


def _fact_lock_statement(anchor: ObligationAnchor) -> str:
    """事实锁的自然语言面（确定性渲染；不摘要、不改写命题）。"""
    if anchor.proposition_statement:
        return anchor.proposition_statement
    if anchor.value is not None:
        return f'{anchor.address} = {anchor.value!r}'
    return anchor.address or anchor.handle


def _address_subjects(address: str | None) -> tuple[str, ...]:
    """compiler 渲染的地址串 → 主体集（`_render_target` 的同一语法，机械回读）。

    char:x.key → (x,)；rel:a×b.key → (a, b)；world / 未知前缀 → ()。锚点只带
    渲染串（typed target 在 assemble 时已折进地址），回读不猜语义（I-21）。
    """
    if not address:
        return ()
    head = address.split('.', 1)[0]
    if head.startswith('char:'):
        return (head[5:],)
    if head.startswith('rel:'):
        left, _, right = head[4:].partition('×')
        return (left, right) if right else (left,)
    return ()


def _anchor_fact_lock(anchor: ObligationAnchor) -> BriefFactLock | None:
    """同一结构化源原子派生同一事实锁；Brief 与正式消费复用这份 compiler 规则。"""
    if anchor.source_kind != 'effect':
        return None
    if anchor.op == 'unknown':
        kind, modality = 'unknown_value', None
    elif anchor.op != 'unset' and anchor.modality not in (None, 'actual'):
        kind, modality = anchor.modality, anchor.modality
    else:
        return None
    return BriefFactLock(kf_id=_lock_id('kf_', anchor.handle), kind=kind,
                         modality=modality, statement=_fact_lock_statement(anchor),
                         subjects=_address_subjects(anchor.address))


def required_fact_locks(obligations: Iterable[SourceObligation]) -> dict[str, tuple[str, ...]]:
    """仅从正式义务 anchors 重建必保锁分母；不依赖 Brief 或历史接受结论。"""
    result: dict[str, tuple[str, ...]] = {}
    for obligation in obligations:
        if obligation.must_preserve:
            result[obligation.obligation_id] = tuple(dict.fromkeys(
                lock.kf_id for anchor in obligation.anchors
                if (lock := _anchor_fact_lock(anchor)) is not None))
    return result


def fact_lock_subjects(obligations: Iterable[SourceObligation]) -> dict[str, frozenset[str]]:
    """kf → 锁主体集合（CS-H H3，RC-63）：effect 锚点作用对象的机械回读。

    与 `required_fact_locks` 同一把尺（同一 `_anchor_fact_lock` 派生），供主体
    包含检查（锁主体 ⊆ 承载状态主体）在 bind 派生、Design 接受与 Guard M4
    三面共用（I-16：不复制第二份锁定义）。
    """
    result: dict[str, frozenset[str]] = {}
    for obligation in obligations:
        for anchor in obligation.anchors:
            lock = _anchor_fact_lock(anchor)
            if lock is not None:
                result[lock.kf_id] = frozenset(lock.subjects)
    return result


def _unanswered_loops(
    blueprint: BlueprintsArtifactV17,
) -> list[tuple[str, str]]:
    """canonical 里 open 而从未 close 的悬念（原文未回答锁的输入，§二.1）。

    按 loop 正式 ref 扫全书：open 记问题、close 记收束；ref 是 compiler 归一
    后的身份（跨章配对同一 ref），所以「全书范围」只是对 canonical 的机械
    扫描，不做语义合并。留线（书末仍开放）与未回答同形——都不许目标作答。
    """
    questions: dict[str, str] = {}
    closed: set[str] = set()
    order: list[str] = []
    for chapter_bp in sorted(blueprint.chapters, key=lambda c: c.chapter):
        for beat in chapter_bp.beats:
            for loop in beat.loops:
                if loop.op == 'open' and loop.ref not in questions:
                    questions[loop.ref] = loop.question or loop.ref
                    order.append(loop.ref)
                elif loop.op == 'close':
                    closed.add(loop.ref)
    return [(ref, questions[ref]) for ref in order if ref not in closed]


def _requires_released_surfaces(
    obligation: SourceObligation, anchor: ObligationAnchor, *,
    characters: CharactersCore | None, structure,
    first_appearance: dict[str, str], prop_segments: dict[str, set[str]],
) -> tuple[str, ...]:
    """必保揭名识别（CS-H H3，RC-61）——三项类型化信号**合取**：

    ① 所属义务是 identity／reveal 类**必保**义务（类别来自义务 kind，不用文本）；
    ② 该命题的源证据句段（Blueprint 里该命题 basis 的句段）正是某**带名**称谓
      在源文首次出现的句段（Characters 的 SurfaceWindow.source_refs[0]；
      带名＝名称结构构词非 null）；
    ③ 命题文本含该称谓字面。

    任一不满足即无揭名要求（漏检退化为普通投影，不比现状差）；不用文本共现
    作判定。返回按字面排序去重的称谓清单（不暴露句段号，保持源盲）。
    """
    if not obligation.must_preserve or obligation.kind.value not in ('identity', 'reveal'):
        return ()
    if characters is None or structure is None or not anchor.proposition_ref:
        return ()
    segments = prop_segments.get(anchor.proposition_ref)
    statement = anchor.proposition_statement or ''
    if not segments:
        return ()
    matched: set[str] = set()
    for char_id, entry in characters.characters.items():
        person = structure.people.get(char_id)
        if person is None:
            continue
        for window in entry.surfaces:
            surface = window.surface
            bearing = person.surface_forms.get(surface) is not None
            if (bearing and first_appearance.get(surface) in segments
                    and surface in statement):
                matched.add(surface)
    return tuple(sorted(matched))


def _derive_brief_locks(
    checkpoint: PreserveCheckpoint, blueprint: BlueprintsArtifactV17,
    characters: CharactersCore | None = None, structure=None,
) -> tuple[tuple[BriefProposition, ...], tuple[BriefKnowledgeLock, ...],
           tuple[BriefFactLock, ...], dict[str, dict[str, tuple]]]:
    """义务 anchors＋canonical loops → Brief 锁定项（a4 唯一派生点）。

    · kp 命题登记：anchors 引用到的全部命题（CS-S 1b 起 effect 不带命题锚，
      命题面只在 cognition 上；asserts 认知转 `publicly_claimed_by`，不铸知情锁）。
    · 知情锁：cognition anchors 按唯一映射铸 {holder, kp, mode}；
      holder=public 跳过。
    · 事实锁：effect anchors——op=unknown → unknown_value（值未知，目标
      不得补值）；op≠unset 且 modality≠actual → 时态锁（目标只能按同
      modality 承载）。canonical 悬念 open 未 close → unanswered。
    · 认知变化：每个义务独立保留各 anchor 的 holder/kp/mode 与盲句柄；
      必保义务及已实现的可综合义务都按 knowledge_changes 检查完整承载。
      事实兑现分母由必保义务的 kf 句柄 realize_locks 派生。
    · 必保揭名（CS-H H3）：`requires_released` 由三项类型化信号合取派生
      （`_requires_released_surfaces`），随认知变化条目携带。
    """
    propositions: dict[str, BriefProposition] = {}
    knowledge: dict[tuple[str, str, str], BriefKnowledgeLock] = {}
    facts: dict[str, BriefFactLock] = {}
    per_obligation: dict[str, dict[str, tuple]] = {}
    # 必保揭名的两项索引：带名称谓的首次出现句段；命题 → 源证据句段集
    # （Blueprint 里该命题全部 basis 的句段——anchors 的 quotes 只有引文字面，
    # 句段身份在 blueprint 的 basis 上）。
    first_appearance: dict[str, str] = {}
    if characters is not None:
        for entry in characters.characters.values():
            for window in entry.surfaces:
                if window.source_refs:
                    first_appearance.setdefault(window.surface, window.source_refs[0])
    # CS-S 1b：prop_segments 只收 cognition（effect 不再带命题锚）。
    prop_segments: dict[str, set[str]] = {}
    for chapter_bp in blueprint.chapters:
        for beat in chapter_bp.beats:
            for cognition in beat.cognition:
                if cognition.proposition.ref:
                    prop_segments.setdefault(cognition.proposition.ref, set()).update(
                        basis.segment for basis in cognition.basis)

    def _register_proposition(anchor: ObligationAnchor) -> str | None:
        if not anchor.proposition_ref:
            return None
        kp = _lock_id('kp_', anchor.proposition_ref)
        if kp not in propositions:
            propositions[kp] = BriefProposition(
                kp_id=kp,
                statement=anchor.proposition_statement or anchor.proposition_ref)
        return kp

    for obligation in checkpoint.obligations:
        derived: list[str] = []
        knowledge_changes: dict[str, BriefKnowledgeChange] = {}
        realize_locks: list[str] = []
        for anchor in obligation.anchors:
            if anchor.source_kind == 'cognition':
                kp = _register_proposition(anchor)
                if kp is None:
                    continue
                derived.append(kp)
                mode = KNOWLEDGE_LOCK_MODE.get(anchor.mode or '')
                if mode is not None and anchor.holder not in (None, 'public'):
                    knowledge[(anchor.holder, kp, mode)] = \
                        BriefKnowledgeLock(holder=anchor.holder, kp=kp, mode=mode)
                    change_id = _lock_id('kc_', anchor.handle)
                    knowledge_changes[change_id] = BriefKnowledgeChange(
                        change_id=change_id, holder=anchor.holder, kp=kp, mode=mode,
                        requires_released=_requires_released_surfaces(
                            obligation, anchor, characters=characters,
                            structure=structure, first_appearance=first_appearance,
                            prop_segments=prop_segments))
                elif anchor.mode == 'asserts' and anchor.holder \
                        and anchor.holder != 'public':
                    entry = propositions[kp]
                    if anchor.holder not in entry.publicly_claimed_by:
                        propositions[kp] = entry.model_copy(update={
                            'publicly_claimed_by': (*entry.publicly_claimed_by,
                                                    anchor.holder)})
            elif anchor.source_kind == 'effect':
                lock = _anchor_fact_lock(anchor)
                if lock is not None:
                    facts[lock.kf_id] = lock
                    derived.append(lock.kf_id)
                    if obligation.must_preserve:
                        realize_locks.append(lock.kf_id)
        deduped = []
        for item in derived:
            if item not in deduped:
                deduped.append(item)
        per_obligation[obligation.obligation_id] = {
            'locks': tuple(deduped),
            'knowledge_changes': tuple(knowledge_changes[k] for k in sorted(knowledge_changes)),
            'realize_locks': tuple(realize_locks)}
    for ref, question in _unanswered_loops(blueprint):
        kf = _lock_id('kf_', ref)
        facts[kf] = BriefFactLock(kf_id=kf, kind='unanswered', statement=question)
    return (
        tuple(propositions[k] for k in sorted(propositions)),
        tuple(knowledge[key] for key in sorted(knowledge)),
        tuple(facts[k] for k in sorted(facts)),
        per_obligation,
    )


def project_brief(
    checkpoint: PreserveCheckpoint, characters: CharactersCore,
    blueprint: BlueprintsArtifactV17, structure=None,
) -> tuple[ReconstructionBrief, tuple[IdentityProjection, ...]]:
    """checkpoint → (Brief, IdentityProjection)。Design 的全部语义输入。

    所有义务类型、must 与 satisfaction 无损进入 source-blind Brief；因果闭合。
    `book_constraints`（R2.5 世界约束通道）：Blueprint 的 world_constraints
    的 statement 原样机械填入（AUD 反例「世界约束没有通道」——恒空即红）；
    目标侧是否沿用或改写成 world_rules 由 Design 决定（wire v2 的 world_rules），
    compiler 不做语义推断。R9 起 blueprint 为必需输入，表达语义无历史默认值。
    a4：锁定项（kp 命题/知情锁/kf 事实锁）由义务 anchors 与 canonical loops
    确定性派生（`_derive_brief_locks`）；series 以盲组 token 标记到成员义务
    （不泄源序号；mechanism＝源表达 ex_id）。CS-H H3：`structure`（名称结构）
    供必保揭名识别的「带名」判定；缺席时不派生 `requires_released`（漏检
    退化为普通投影，不比现状差）。
    """
    # 不以源义务序号暴露叙述顺序；所有类型与满足条件等量投影，依赖闭合。
    core_ids = {o.obligation_id: obligation_core_id(o.obligation_id)
                for o in checkpoint.obligations}
    propositions, knowledge_locks, fact_locks, derived_locks = \
        _derive_brief_locks(checkpoint, blueprint, characters, structure)
    series_group: dict[str, str] = {}
    series_mechanism: dict[str, str | None] = {}
    for group in checkpoint.series:
        token = _lock_id('sr_', '|'.join(sorted(group.members)))
        for member in group.members:
            series_group[member] = token
            series_mechanism[member] = group.mechanism
    cores = tuple(sorted((
        BriefRequirement(
            core_id=core_ids[o.obligation_id], summary=o.statement,
            kind=o.kind, must_preserve=o.must_preserve, satisfaction=o.satisfaction,
            participants=tuple(sorted(o.lineage.character_ids)),
            requires_cores=tuple(sorted(core_ids[r] for r in o.requires)),
            locks=derived_locks[o.obligation_id]['locks'],
            knowledge_changes=derived_locks[o.obligation_id]['knowledge_changes'],
            realize_locks=derived_locks[o.obligation_id]['realize_locks'],
            series_group=series_group.get(o.obligation_id),
            series_mechanism=series_mechanism.get(o.obligation_id))
        for o in checkpoint.obligations), key=lambda c: c.core_id))
    brief = ReconstructionBrief(
        brief_id=f'brief_{checkpoint.checkpoint_id}',
        preserve_checkpoint_sha256=checkpoint.checkpoint_sha256,
        requirements=cores,
        propositions=propositions,
        knowledge_locks=knowledge_locks,
        fact_locks=fact_locks,
        source_narration=blueprint.source_expression.narration,
        expression=tuple({'ex_id': item.ex_id, 'kind': item.kind, 'statement': item.statement}
                         for item in blueprint.source_expression.items),
        expression_readings=blueprint.source_expression.readings,
        book_constraints=tuple(
            BookConstraint(constraint_id=wc.constraint_id, statement=wc.statement)
            for wc in blueprint.world_constraints))
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


def design_obligation_index(
    checkpoint: PreserveCheckpoint, brief: ReconstructionBrief,
) -> dict[str, Any]:
    """ob 编号（＝obligation_id）→ Brief 需求的唯一映射（design 面共享）。

    chain 的 prompt 渲染与 wire 校验（`assemble_body_design` 的 obligations）
    和 review 的免费预览共用同一映射：需求经 summary 与义务 statement 连接
    （同一文本，Preserve 投影的恒等式）。
    """
    by_core = {r.core_id: r for r in brief.requirements}
    return {o.obligation_id: by_core[obligation_core_id(o.obligation_id)]
            for o in checkpoint.obligations}


def _preserve_issue(message: str, code: str, location: tuple,
                   paths=()) -> ValidationIssue:
    """Preserve 判据问题：规则码＋稳定位置＋`obligations[i]` 条目路径。"""
    return ValidationIssue(message, code=code, location=tuple(location),
                           owner='preserve',
                           context={'wire_paths': [list(p) for p in paths]})


def preserve_name_issues(
    checkpoint: PreserveCheckpoint | None, chars,
) -> list[ValidationIssue]:
    """Preserve 自闸（CS-H H3，RC-64）：义务 statement／satisfaction 里的
    **裸姓与未登记别名**在 Preserve 自己的接受循环里被逐条定位。

    同一把尺＝下游材料归因的判据面（`positive_material_issues`／
    `withheld_name_bindings`）：能被显示面投影的字面（唯一归属的带名称谓）
    合法——它们在写手材料里确定性投影；**永不投影**的字面（裸姓；多义带名面——
    同一登记称谓属于两个人物，其中的裸姓同被查出；未登记别名因含裸姓成分同被
    查出）在义务文本里就是下游缺口，这里带 `obligations[i]` 条目路径提前拦下，
    不再等跨节点回派。家族构词（「陆家」一类集体指称）**不拒**：同家族人物释放后
    它是正文的合法字面，Preserve 阶段不知释放时点，拒收只会误报。
    quotes／anchors 是源文逐字证据，不在扫描面。纯函数、只读。
    """
    structure = chars.name_structure
    if checkpoint is None or structure is None:
        return []
    projection = chars.name_projection()
    family_forms = family_form_literals(structure)
    registered = sorted(set(projection.proper_literals)
                        | {s for entry in projection.by_person
                           for s in entry.name_bearing_surfaces}
                        | family_forms)
    bindable = {literal for literal, _cid, _enc in
                withheld_name_bindings(registered, structure)}
    # 闸面＝裸姓（家族字面本身，含复姓整字）。认领面只有两类合法字面：家族构词
    # （「陆家」一类集体指称）与**唯一归属、可投影**的带名面——「陆」在「陆家」
    # 「陆兄」里被更长的合法字面认领；在「陆郎」「陆某」这类未登记别名里未被覆盖、
    # 如实报出。多义带名面（同一登记称谓属于两个人物）永不投影，**不**作认领面：
    # 它里面的裸姓同样如实报出（与下游材料归因同一把尺，RC-64）。
    gate = tuple(sorted({f.literal for f in structure.families.values()}))
    if not gate:
        return []
    claim_face = tuple(sorted((family_forms | bindable) - set(gate)))
    issues: list[ValidationIssue] = []
    for index, obligation in enumerate(checkpoint.obligations):
        for field in ('statement', 'satisfaction'):
            text = getattr(obligation, field) or ''
            if not text:
                continue
            claimed = claim_spans(text, claim_face)
            for claim in uncovered_occurrences(text, gate, claimed):
                issue = _preserve_issue(
                    f'义务 {obligation.obligation_id} 的 {field} 含不可投影字面 '
                    f'{claim.literal!r}（裸姓／多义带名面中的裸姓成分——写手材料无法'
                    '确定性投影，下游必成泄漏）：改用可用称呼或不含姓名字面的表述，'
                    '保留 anchors 与原义务语义',
                    'preserve_name_literal',
                    ('preserve_name_literal', obligation.obligation_id, field, claim.literal),
                    (('obligations', index),))
                issue.context.update({'obligation_id': obligation.obligation_id,
                                      'field': field, 'literal': claim.literal})
                issues.append(issue)
    return issues


def _anchor_line(anchor: ObligationAnchor) -> str:
    """锚点类型化拷贝 → 切片里的人读原子行（与投影视图同一语法，不新造语义）。"""
    if anchor.source_kind == 'cognition':
        statement = anchor.proposition_statement or ''
        return (f'认知 {anchor.holder} {anchor.mode or ""} 命题 '
                f'{anchor.proposition_ref or ""}'
                + (f'「{statement}」' if statement else ''))
    if anchor.source_kind == 'effect':
        return (f'事实 {anchor.address or anchor.handle} '
                f'{anchor.op or "set"} {anchor.value!r}'
                + (f'（{anchor.modality}）' if anchor.modality else ''))
    if anchor.source_kind == 'loop':
        return f'悬念 {anchor.handle}：{anchor.question or ""}'
    if anchor.source_kind == 'world_constraint':
        return f'世界约束 {anchor.handle}：{anchor.value or ""}'
    if anchor.source_kind == 'initial_state':
        return f'初始事实 {anchor.address or anchor.handle} = {anchor.value!r}'
    return f'{anchor.handle}（{anchor.source_kind}）'


def preserve_patch_slice(
    wire: dict[str, Any], characters: CharactersCore,
    blueprint: BlueprintsArtifactV17, indices: Iterable[int],
) -> dict[str, Any]:
    """Preserve 条目返修的依赖切片（RC-64）：目标义务的源原子与证据窗口。

    只带失败条目必需的材料——该义务 anchors 的类型化原子行与逐字引文；不带
    整份投影视图，也不带其他义务的内容（M3 返修宽度；对照本根 54,390 输入的
    整份重写）。纯函数。
    """
    index = _anchor_index(characters, blueprint)
    raw = wire.get('obligations') or []
    slices = []
    for i in sorted(set(indices)):
        if not 0 <= i < len(raw) or not isinstance(raw[i], dict):
            continue
        atoms = []
        for handle in raw[i].get('anchors') or ():
            anchor = index.get(handle)
            if anchor is None:
                continue
            atoms.append({'handle': handle, 'line': _anchor_line(anchor),
                          'quotes': list(anchor.quotes)})
        slices.append({'index': i, 'obligation': raw[i], 'atoms': atoms})
    return {'input_version': 'stage4-preserve-slice-v1', 'targets': slices}


def required_releases(brief: ReconstructionBrief) -> dict[tuple[str, str, str], tuple[str, ...]]:
    """必保揭名 → IR compile 的映射面：(holder, kp, mode) → 必现字面。

    认知增量在 canonical BodyDesign 里按 (holder, kp, mode) 展开（change_id
    不进目标侧）；同三元组若对应多条变化，取并集（重复三元组罕见，取并集
    只会多担不会漏担）。承载章正文必现该字面（RC-61 正文面）。
    """
    result: dict[tuple[str, str, str], tuple[str, ...]] = {}
    for requirement in brief.requirements:
        for change in requirement.knowledge_changes:
            if not change.requires_released:
                continue
            key = (change.holder, change.kp, change.mode)
            merged = set(result.get(key, ())) | set(change.requires_released)
            result[key] = tuple(sorted(merged))
    return result


def _binding_issue(message: str, code: str, location: tuple) -> ValidationIssue:
    """bind 派生面问题：规则码＋稳定位置（无 wire 路径；owner=design 的定位由
    wire 侧判据负责，本面是 compile 侧的承载结构自洽性）。"""
    return ValidationIssue(message, code=code, location=tuple(location), owner='design')


def derive_semantic_binding(
    placements: dict[str, list[tuple[int, int]]],
    body: BodyDesign, checkpoint: PreserveCheckpoint,
    fact_marks: tuple = (),
) -> tuple[SemanticBinding | None, list[str]]:
    """CS-3（bind LLM 退役）：design 的 real 承载标注 → SemanticBinding。

    `placements` 是 ob 编号 → 标注拍 `(scene 序, beat 序)` 的阅读序投影
    （`novel/stages/stage4/slim.py::design_placements`——ob 编号与
    checkpoint 派发的 obligation_id 同源同值）。派生规则与 wire 语义一致：
    末次标注拍＝接受拍（单一最终验收位），其余标注拍＝支撑拍；无标注义务
    进 unbound（owner=design——必保义务漏标已在 `assemble_body_design` 的
    real 覆盖闸拦下，能到这里的 unbound 必为非必保义务）。series 成员接受拍
    互斥（`series_collapsed` 判据随 bind 迁入：离散兑现时点分别承载，C3/C5
    的承载面）。纯函数、不落盘；错误并入 body_design 的同节点修订轮报告。

    CS-F2：`fact_marks`（`slim.py::design_fact_marks`）是 wire 在已选状态上的
    事实锁标注；本函数把每条标注落成 `FactLockBinding`（锁、承载拍复合引用、
    状态地址——不复制 value），并核对该拍的 BodyDesign 里确有同地址的状态：
    映射必须指向 BodyDesign 实际持有的事实，悬空即拒（I-16）；必保锁逐义务
    完整覆盖，承载位须属于该义务的 real 接受／支撑拍，不另造义务承载位。
    """
    errors: list[str] = []
    # (scene 序, beat 序) → (scene_id, beat_id, 复合引用)；越界当场精确拒。
    site_refs: dict[tuple[int, int], tuple[str, str, str]] = {}
    for scene_i, scene in enumerate(body.scenes):
        for beat_i, beat in enumerate(scene.beats):
            site_refs[(scene_i, beat_i)] = (
                scene.scene_id, beat.beat_id, f'{scene.scene_id}#{beat.beat_id}')
    denominator = {o.obligation_id for o in checkpoint.obligations}
    outside = sorted(set(placements) - denominator)
    if outside:
        # CS-H H2（RC-59）：bind 派生面的自洽性违例带规则码与位置，无 wire
        # 路径（compile 面）——`PATCH_ROUTES` 的 stop 态。
        errors.append(_binding_issue(
            f'real 标注引用 checkpoint 外义务：{outside}',
            'design_real_closed', ('real_outside', tuple(outside))))
    entries: list[dict[str, Any]] = []
    unbound: list[dict[str, Any]] = []
    for obligation in checkpoint.obligations:
        sites = placements.get(obligation.obligation_id) or ()
        resolved = [site_refs.get(site) for site in sites]
        if any(ref is None for ref in resolved):
            errors.append(_binding_issue(
                f'义务 {obligation.obligation_id} 的 real 标注拍越界：{list(sites)}'
                f'（Design 共 {len(site_refs)} 拍）',
                'design_real_anchor', ('real_anchor', obligation.obligation_id)))
            continue
        if not resolved:
            unbound.append({
                'obligation_id': obligation.obligation_id, 'owner': 'design',
                'reason': 'design 未给该义务标注承载位（real 缺失；非必保义务'
                          '无承载时按 unbound 保留，义务不删）'})
            continue
        accept = resolved[-1]
        entries.append({
            'obligation_id': obligation.obligation_id,
            'scene_id': accept[0], 'beat_ref': accept[2],
            'supporting_beat_refs': tuple(ref[2] for ref in resolved[:-1])})
    fact_bindings: list[FactLockBinding] = []
    for mark in fact_marks:
        ref = site_refs.get((mark.scene, mark.beat))
        beat = body.beat(ref[0], ref[1]) if ref is not None else None
        if beat is None or not any(
                d.address() == mark.address.address() and d.op == mark.op
                and (mark.op != 'set' or d.modality == mark.modality)
                for d in beat.target_delta):
            errors.append(design_issue(
                f'事实锁 {mark.kf_id} 的承载状态在 BodyDesign 中不存在：第 {mark.scene + 1} '
                f'场第 {mark.beat + 1} 拍 state[{mark.state}]（映射不得悬空）',
                code='fact_lock_missing',
                location=('fact_lock_missing', mark.kf_id, mark.beat),
                paths=(mark.path,)))
            continue
        fact_bindings.append(FactLockBinding(
            kf_id=mark.kf_id, beat_ref=ref[2], address=mark.address))
    errors.extend(fact_binding_errors(body, fact_bindings, wire_paths=True))
    errors.extend(fact_lock_subject_errors(
        body, fact_bindings, fact_lock_subjects(checkpoint.obligations),
        wire_paths=True))
    placed_refs = {ob: tuple(site_refs[site][2] for site in sites if site in site_refs)
                   for ob, sites in placements.items()}
    errors.extend(fact_lock_carrier_errors(
        body, fact_bindings, required_locks=required_fact_locks(checkpoint.obligations),
        carrier_refs=placed_refs, wire_paths=True,
        accept_refs={ob: refs[-1] for ob, refs in placed_refs.items() if refs}))
    if errors:
        return None, errors
    # 共享锁派生支撑引用（CS-H H3，RC-63）：一项事实定位一次——承载拍属于任一
    # 共享义务的 real 集合即成立；其余共享义务由 compiler 把承载拍记入
    # supporting_beat_refs，对它们只校验先后（承载拍早于其接受拍：Design 面在上面的
    # `fact_lock_carrier_errors(accept_refs=…)` 带路径判掉，compile 面
    # compile_support_early 兜底），不再要求逐义务自担承载。
    shared_required = required_fact_locks(checkpoint.obligations)
    carrier_by_ob = placed_refs
    for bound in fact_bindings:
        obligations = [ob for ob, locks in shared_required.items()
                       if bound.kf_id in locks]
        if len(obligations) < 2:
            continue
        for row in entries:
            if row['obligation_id'] in obligations \
                    and bound.beat_ref not in carrier_by_ob.get(row['obligation_id'], ()):
                refs = list(row.get('supporting_beat_refs') or ())
                if bound.beat_ref not in refs:
                    refs.append(bound.beat_ref)
                row['supporting_beat_refs'] = tuple(refs)
    try:
        binding = SemanticBinding(
            binding_id=f'bind_{checkpoint.checkpoint_id}',
            preserve_checkpoint_sha256=checkpoint.checkpoint_sha256,
            obligations=checkpoint.obligations,
            entries=tuple(_binding_entry(e) for e in entries),
            unbound=tuple(_unbound_entry(u) for u in unbound),
            fact_bindings=tuple(fact_bindings))
    except (ValidationError, KeyError) as exc:
        return None, [_binding_issue(
            f'binding 派生构造失败：{exc}', 'book_design_construct',
            ('binding_construct',))]
    problems = [_binding_issue(str(p), 'compile_binding_denominator',
                               ('binding_covers',))
                for p in check_binding_covers_checkpoint(checkpoint, binding)]
    # series 接受拍互斥（判据自 assemble_binding 迁入，消息保持同一把尺；
    # CS-H H2：候选拍闭集里改选承载位——可选目标，路径由 wire 侧拍坐标给出）。
    entry_of = {e.obligation_id: e for e in binding.entries}
    for group in checkpoint.series:
        carried = [(member, entry_of[member].beat_ref)
                   for member in group.members if member in entry_of]
        by_ref: dict[str, list[str]] = {}
        for member, ref in carried:
            by_ref.setdefault(ref, []).append(member)
        for ref, members in sorted(by_ref.items()):
            if len(members) > 1:
                collapsed = sorted(members)
                problem = ValidationIssue(
                    f'series_collapsed：series（mechanism={group.mechanism or "无"}）'
                    f'的成员 {collapsed} 的接受拍同为 {ref}——离散兑现时点'
                    '必须分别承载（不同拍），复合义务用支撑拍表达前半，不合并成员',
                    code='series_collapsed',
                    location=('series_collapsed', ref, tuple(collapsed)),
                    owner='design',
                    context={'beat_ref': ref, 'members': collapsed,
                             'mechanism': group.mechanism})
                problems.append(problem)
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
