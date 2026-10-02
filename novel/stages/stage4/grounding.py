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
    participants），不再向模型回显整个 Design。R10a a5 起带出 series 与
    锁摘要（`brief` 输入；锁的自然语言面来自 Brief，本投影只渲染）。
  • `assemble_binding`：Bind wire → SemanticBinding。B3 前半起 wire 不写
    复合引用（I-21）：承载位置给 {obligation_id, scene, beat} 与支撑拍
    [{scene, beat}]——三个分量都只能取投影给出的闭集内单 ID，`场景#拍`
    复合引用由 compiler 组成。跨产物校验复用 F1 的
    check_binding_covers_checkpoint；R10a a5 起 series 成员绑定同一拍即
    `series_collapsed` 拒（离散兑现时点分别承载）；完整发布，unbound 保留
    义务。

不修改输入；不落盘（落盘归 harness／repository）。
"""
from __future__ import annotations

import hashlib
from typing import Any, Final

from contracts.schema.design import BodyDesign
from contracts.schema.grounding import (
    BriefFactLock, BriefKnowledgeLock, BriefProposition, BriefRequirement,
    IdentityProjection, ObligationAnchor, ObligationSeries, PreserveCheckpoint,
    ReconstructionBrief, SemanticBinding, SourceLineage, SourceObligation,
    _lock_id,
)
from contracts.schema.source import (
    BlueprintsArtifactV15, CharactersCore,
)
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
#: R10a a2：义务以 anchors 引用投影句柄，lineage 退出 wire（compiler 派生）；
#: 顶层增 series／evidence_conflicts。
_OBLIGATION_KEYS = frozenset({'kind', 'statement', 'anchors',
                              'must_preserve', 'satisfaction', 'requires'})
_SERIES_KEYS = frozenset({'members', 'mechanism'})
_CONFLICT_KEYS = frozenset({'anchor', 'segment', 'note'})
_ENTRY_KEYS = frozenset({'obligation_id', 'scene', 'beat', 'supporting'})
_SUPPORTING_KEYS = frozenset({'scene', 'beat'})
_UNBOUND_KEYS = frozenset({'obligation_id', 'owner', 'reason'})


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
    characters: CharactersCore, blueprint: BlueprintsArtifactV15,
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
                prop = effect.proposition
                index[handle] = ObligationAnchor(
                    handle=handle, source_kind='effect',
                    address=f'{_render_target(effect.target)}.{effect.key}',
                    value=effect.value, modality=effect.modality,
                    op=effect.op,
                    visibility=effect.disclosure.visibility,
                    proposition_ref=prop.ref if prop else None,
                    proposition_statement=prop.statement if prop else None,
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
    characters: CharactersCore, blueprint: BlueprintsArtifactV15,
) -> SourceLineage:
    """义务 anchors → SourceLineage（I-15/I-21：compiler 派生，wire 不再输出）。

    blueprint_refs＝锚点主体（拍内转移归其 beat_id；初态/约束归自身 id）；
    character_ids＝初态 target 人物＋效应作用对象人物＋认知 holder——同一
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
    characters: CharactersCore, blueprint: BlueprintsArtifactV15,
) -> str:
    """R10a a2：Preserve 的源证据投影（确定性渲染，取代 Blueprint JSON 倾倒）。

    按书级初态 → 世界约束 → 逐章逐拍渲染：每原子一行带投影句柄＋类型化内容
    ＋逐字引文；拍头带 event 与拍 source_refs 的证据窗口（Blueprint.evidence
    的窗口文本＝被引段全文＋同章前后各一段）。不摘要、不改写（I-24 不改输入；
    I-20/I-21 机械渲染归 compiler）。R10-0.3 实证旧形态整份 Blueprint JSON
    倾倒 29.9k token/轮——本投影只给语义判断实际需要的原子与证据。
    """
    lines: list[str] = []
    index = _anchor_index(characters, blueprint)
    if characters.initial_state:
        lines.append('【书级初始事实（故事开始前成立）】')
        for atom in characters.initial_state:
            knowledge = ''
            if atom.knowledge is not None:
                known = list(atom.knowledge.known_by)
                if atom.knowledge.reader_knows:
                    known.append('reader')
                if known:
                    knowledge = f'；初始已知: {",".join(known)}'
            lines.append(f'[{atom.state_id}] {_render_target(atom.target)}.'
                         f'{atom.key} = {atom.value!r}（{atom.persistence}{knowledge}）')
            for basis in atom.basis:
                lines.append(f'  依据 {basis.segment}：「{basis.quote}」')
    if blueprint.world_constraints:
        lines.append('【世界约束】')
        for rule in blueprint.world_constraints:
            lines.append(f'[{rule.constraint_id}] {rule.kind}：{rule.statement}')
            for basis in rule.basis:
                lines.append(f'  依据 {basis.segment}：「{basis.quote}」')
    if blueprint.source_expression.items:
        lines.append('【源表达机制（series 的 mechanism 闭集）】')
        for item in blueprint.source_expression.items:
            lines.append(f'{item.ex_id}（{item.kind}）：{item.statement}')
    cited: set[str] = set()
    for chapter_bp in sorted(blueprint.chapters, key=lambda c: c.chapter):
        lines.append(f'【第 {chapter_bp.chapter} 章】')
        scene_of = {bid: span.scene_id
                    for span in chapter_bp.scenes for bid in span.beat_ids}
        for beat in chapter_bp.beats:
            scene_note = scene_of.get(beat.beat_id, '?')
            lines.append(f'拍 {beat.beat_id}（场景 {scene_note}；'
                         f'{"；".join(beat.participants) or "无人物"}）：{beat.event}')
            for letter, transfers in (('e', beat.effects), ('c', beat.cognition),
                                      ('l', beat.loops)):
                for n in range(1, len(transfers) + 1):
                    handle = f'{beat.beat_id}.{letter}{n}'
                    lines.append(f'  [{handle}] {_anchor_line(index[handle])}')
            for ref in beat.source_refs:
                cited.add(ref)
    lines.append('【证据窗口（被引句段全文，含同章前后各一句）】')
    for ref in sorted(cited):
        window = blueprint.evidence.get(ref)
        if window:
            lines.append(f'{ref}：{window}')
    return '\n'.join(lines)


def _anchor_line(anchor: ObligationAnchor) -> str:
    """单原子的人读渲染（与 _anchor_index 同一事实源，不二次解释）。"""
    parts: list[str] = []
    if anchor.source_kind == 'effect':
        parts.append(f'事实 {anchor.address} = {anchor.value!r}（{anchor.modality}'
                     f'；{anchor.visibility}')
        if anchor.proposition_ref:
            parts.append(f'；命题 {anchor.proposition_ref}「{anchor.proposition_statement}」')
        parts.append('）')
    elif anchor.source_kind == 'cognition':
        parts.append(f'认知 {anchor.holder} {anchor.mode}「{anchor.proposition_statement}」')
    elif anchor.source_kind == 'loop':
        parts.append(f'悬念 {anchor.question}')
    elif anchor.source_kind == 'initial_state':
        parts.append(f'初态 {anchor.address} = {anchor.value!r}')
    else:
        parts.append(f'约束 {anchor.address}：{anchor.value}')
    line = ''.join(parts)
    quotes = '／'.join(f'「{q}」' for q in anchor.quotes)
    return f'{line}　依据：{quotes}' if quotes else line


def assemble_preserve(
    wire: dict[str, Any], *,
    policy_id: str, policy_version: int,
    characters: CharactersCore, blueprint: BlueprintsArtifactV15,
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
    errors: list[str] = []
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
            if not isinstance(ref, int) or not 1 <= ref <= len(raw):
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
            series_list.append(ObligationSeries(
                members=tuple(resolved_members), mechanism=mechanism))
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


def _unanswered_loops(
    blueprint: BlueprintsArtifactV15,
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


def _derive_brief_locks(
    checkpoint: PreserveCheckpoint, blueprint: BlueprintsArtifactV15,
) -> tuple[tuple[BriefProposition, ...], tuple[BriefKnowledgeLock, ...],
           tuple[BriefFactLock, ...], dict[str, dict[str, tuple[str, ...]]]]:
    """义务 anchors＋canonical loops → Brief 锁定项（a4 唯一派生点）。

    · kp 命题登记：anchors 引用到的全部命题（effect 的 concealed 命题与
      cognition 命题同注册——揭示链经同一命题 ref 闭合，D3/D5）；
      asserts 认知转 `publicly_claimed_by`，不铸知情锁。
    · 知情锁：cognition anchors 按唯一映射铸 {holder, kp, mode}；
      holder=public 跳过。
    · 事实锁：effect anchors——op=unknown → unknown_value（值未知，目标
      不得补值）；op≠unset 且 modality≠actual → 时态锁（目标只能按同
      modality 承载）。canonical 悬念 open 未 close → unanswered。
    · 兑现分母：必保义务派生的 (holder|kp) 与 kf 句柄（`realize_holders`/
      `realize_locks`；非必保义务为空——可综合义务不强制兑现）。
    """
    propositions: dict[str, BriefProposition] = {}
    knowledge: dict[tuple[str, str, str], BriefKnowledgeLock] = {}
    facts: dict[str, BriefFactLock] = {}
    per_obligation: dict[str, dict[str, tuple[str, ...]]] = {}

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
        realize_holders: list[str] = []
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
                    if obligation.must_preserve:
                        pair = f'{anchor.holder}|{kp}'
                        if pair not in realize_holders:
                            realize_holders.append(pair)
                elif anchor.mode == 'asserts' and anchor.holder \
                        and anchor.holder != 'public':
                    entry = propositions[kp]
                    if anchor.holder not in entry.publicly_claimed_by:
                        propositions[kp] = entry.model_copy(update={
                            'publicly_claimed_by': (*entry.publicly_claimed_by,
                                                    anchor.holder)})
            elif anchor.source_kind == 'effect':
                kp = _register_proposition(anchor)
                if anchor.op == 'unknown':
                    kf = _lock_id('kf_', anchor.handle)
                    facts[kf] = BriefFactLock(
                        kf_id=kf, kind='unknown_value',
                        statement=_fact_lock_statement(anchor))
                    derived.append(kf)
                    if obligation.must_preserve:
                        realize_locks.append(kf)
                elif anchor.op != 'unset' and anchor.modality not in (
                        None, 'actual'):
                    kf = _lock_id('kf_', anchor.handle)
                    facts[kf] = BriefFactLock(
                        kf_id=kf, kind=anchor.modality, modality=anchor.modality,
                        statement=_fact_lock_statement(anchor))
                    derived.append(kf)
                    if obligation.must_preserve:
                        realize_locks.append(kf)
                if kp is not None:
                    derived.append(kp)
        deduped = []
        for item in derived:
            if item not in deduped:
                deduped.append(item)
        per_obligation[obligation.obligation_id] = {
            'locks': tuple(deduped),
            'realize_holders': tuple(realize_holders),
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
    blueprint: BlueprintsArtifactV15,
) -> tuple[ReconstructionBrief, tuple[IdentityProjection, ...]]:
    """checkpoint → (Brief, IdentityProjection)。Design 的全部语义输入。

    所有义务类型、must 与 satisfaction 无损进入 source-blind Brief；因果闭合。
    `book_constraints`（R2.5 世界约束通道）：Blueprint 的 world_constraints
    的 statement 原样机械填入（AUD 反例「世界约束没有通道」——恒空即红）；
    目标侧是否沿用或改写成 world_rules 由 Design 决定（wire v2 的 world_rules），
    compiler 不做语义推断。R9 起 blueprint 为必需输入，表达语义无历史默认值。
    a4：锁定项（kp 命题/知情锁/kf 事实锁）由义务 anchors 与 canonical loops
    确定性派生（`_derive_brief_locks`）；series 以盲组 token 标记到成员义务
    （不泄源序号；mechanism＝源表达 ex_id）。
    """
    # 不以源义务序号暴露叙述顺序；所有类型与满足条件等量投影，依赖闭合。
    core_ids = {o.obligation_id: obligation_core_id(o.obligation_id)
                for o in checkpoint.obligations}
    propositions, knowledge_locks, fact_locks, derived_locks = \
        _derive_brief_locks(checkpoint, blueprint)
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
            realize_holders=derived_locks[o.obligation_id]['realize_holders'],
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
        book_constraints=tuple(wc.statement for wc in blueprint.world_constraints))
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
    brief: ReconstructionBrief | None = None,
) -> dict[str, Any]:
    """Bind 的输入投影（B3 前半）：承载选择的最小闭集，不回显整个 Design。

    模型需要的是「每条义务要保什么」与「每个拍发生了什么」——driver、导演
    说明、delta 全量与称谓释放都不进承载选择的输入。义务 id 与场景/拍 id 是
    compiler 派发的正式 ID（闭集选择，不是模型生成）。纯函数。

    R10a a5：带出 series（同一机制的离散兑现成员——各成员必须绑不同拍）与
    锁摘要（`brief` 给出时按义务渲染 kp/kf 锁的自然语言面：命题、持有者、
    模式、modality——承载位置必须能完成这些锁的兑现；锁注册表的唯一权威是
    a4 的 Brief，本投影只渲染不派生）。
    """
    obligations = []
    if brief is not None:
        req_by_core = {r.core_id: r for r in brief.requirements}
        prop_by_kp = {p.kp_id: p for p in brief.propositions}
        fact_by_kf = {f.kf_id: f for f in brief.fact_locks}
        lock_modes: dict[tuple[str, str], set[str]] = {}
        for lock in brief.knowledge_locks:
            lock_modes.setdefault((lock.holder, lock.kp), set()).add(lock.mode)

        def _holder_line(pair: str) -> str:
            holder, _, kp = pair.partition('|')
            modes = sorted(lock_modes.get((holder, kp), ()))
            statement = prop_by_kp[kp].statement if kp in prop_by_kp else ''
            return (f'{holder} 对「{statement}」({kp}) 授权模式：'
                    + ('/'.join(modes) if modes else '无'))

        def _fact_line(kf: str) -> str:
            lock = fact_by_kf.get(kf)
            if lock is None:
                return kf
            note = (f'{lock.statement}（{kf}，只能按 {lock.modality} 承载，'
                    '不得写成已发生）' if lock.modality
                    else f'{lock.statement}（{kf}，{lock.kind}）')
            return note
    for o in checkpoint.obligations:
        item = {'obligation_id': o.obligation_id,
                'kind': o.kind.value if hasattr(o.kind, 'value') else str(o.kind),
                'statement': o.statement, 'must_preserve': o.must_preserve,
                'satisfaction': o.satisfaction, 'requires': list(o.requires)}
        if brief is not None:
            req = req_by_core.get(obligation_core_id(o.obligation_id))
            if req is not None:
                if req.series_group:
                    item['series_group'] = req.series_group
                if req.series_mechanism:
                    item['series_mechanism'] = req.series_mechanism
                if req.realize_holders:
                    item['knowledge_locks'] = [_holder_line(p)
                                               for p in req.realize_holders]
                if req.realize_locks:
                    item['fact_locks'] = [_fact_line(k) for k in req.realize_locks]
        obligations.append(item)
    return {
        'protocol_version': STAGE4_BIND_PROTOCOL,
        'obligations': obligations,
        'series': [
            {'members': list(group.members),
             'mechanism': group.mechanism,
             'note': '同一机制的离散兑现时点——各成员必须绑定不同拍，'
                     '不得合并承载'}
            for group in checkpoint.series],
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
    # R10a a5：series 成员同拍 → series_collapsed（同节点修订）。同一机制的
    # 离散兑现时点各自承载——两成员落在同一拍＝「多次发生」被压成一次（C3/C5
    # 的承载面）；unbound 成员无承载位，不参与比较（必保未承载另有
    # must_keep_unbound 回派）。
    entry_of = {e.obligation_id: e for e in binding.entries}
    for group in checkpoint.series:
        carried = [(member, entry_of[member].beat_ref)
                   for member in group.members if member in entry_of]
        by_ref: dict[str, list[str]] = {}
        for member, ref in carried:
            by_ref.setdefault(ref, []).append(member)
        for ref, members in sorted(by_ref.items()):
            if len(members) > 1:
                errors.append(
                    f'series_collapsed：series（mechanism='
                    f'{group.mechanism or "无"}）的成员 {sorted(members)} 绑定到'
                    f'同一拍 {ref}——离散兑现时点必须分别承载（不同拍），'
                    '复合义务用 supporting 表达前半，不合并成员')
    if errors:
        return None, errors
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
