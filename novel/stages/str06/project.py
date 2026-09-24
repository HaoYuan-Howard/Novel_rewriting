"""同一局部输入投影 IR/WriterBrief 与消费身份；不累计复制所有历史义务。

F5 职责重划后：VerificationIR 承载全部事实约束（含逐拍定位 BeatIR 与禁止项），
WriterBrief 只承载呈现（realization_brief 原样投影）；章预算归 ChapterIR，
不再进入场景 slice 身份。
"""
from __future__ import annotations

import hashlib
import json

from contracts.schema.str06.context import (
    SurfaceRelease, VerificationIR, WriterBrief, WriterContext, beats_ir,
)
from contracts.schema.str06.design import BodyDesign
from contracts.schema.str06.execution import ExecutionPlan, ExecutionSlice
from contracts.schema.str06.grounding import SemanticBinding
from contracts.schema.str06 import STR06_RC_VERSION
from novel.stages.str06.fold import FoldedState


def _sha(value: object) -> str:
    return hashlib.sha256(json.dumps(value, ensure_ascii=False, sort_keys=True,
                                     separators=(',', ':')).encode('utf-8')).hexdigest()


def surface_engine_release(graph: BodyDesign, at_scene: str) -> tuple[SurfaceRelease, ...]:
    """只释放 Design 显式声明的称谓；participants 出现不等于真名已揭示。"""
    faces: dict[str, set[str]] = {}
    for intro in graph.initial_surfaces:
        faces.setdefault(intro.char_id, set()).update(intro.surfaces)
    for scene in graph.scenes:
        for beat in scene.beats:
            for intro in beat.surface_introductions:
                faces.setdefault(intro.char_id, set()).update(intro.surfaces)
        if scene.scene_id == at_scene:
            relevant = {p for b in scene.beats for p in b.participants}
            relevant.update(intro.char_id for b in scene.beats for intro in b.surface_introductions)
            relevant.update(p for r in scene.state_reads for p in r.subjects)
            relevant.update(r.holder for r in scene.state_reads if r.holder)
            return tuple(SurfaceRelease(char_id=cid, surfaces=tuple(sorted(faces[cid])))
                         for cid in sorted(relevant & set(faces)))
    raise ValueError(f'未知场景 {at_scene}')


def project_context(*, scene_id: str, graph: BodyDesign,
                    plan: ExecutionPlan, binding: SemanticBinding,
                    folded_prefix: FoldedState) -> tuple[WriterContext, ExecutionSlice]:
    """场景双视图与消费身份的确定性投影（纯函数，不落盘、不调 LLM）。"""
    scene = next(s for s in graph.scenes if s.scene_id == scene_id)
    scene_plan = next(s for s in plan.scenes if s.scene_id == scene_id)
    entries = sorted((e for e in binding.entries if e.scene_id == scene_id),
                     key=lambda e: e.obligation_id)
    ids = tuple(e.obligation_id for e in entries)
    obmap = {o.obligation_id: o for o in binding.obligations}
    owned = tuple(obmap[oid] for oid in ids)
    reads = {r.address() for r in scene.state_reads}
    state = tuple(e for e in folded_prefix.entry_state_view(f'{scene_id}#start')
                  if e.address() in reads)
    knowledge = tuple(e for e in folded_prefix.entry_knowledge_view()
                      if ('knowledge', e.subjects, e.holder, e.key) in reads)
    available = {e.address() for e in state} | {
        ('knowledge', e.subjects, e.holder, e.key) for e in knowledge}
    missing = reads - available
    if missing:
        raise ValueError(f'{scene_id} 消费了未建立状态：{sorted(missing)}；返回 Design 修复')
    if any(e.status == 'unknown' for e in (*state, *knowledge)):
        raise ValueError(f'{scene_id} 消费状态为 unknown；不得当作通过')
    # 跨场义务只携带明确的已完成支撑拍；绝不把整条复合义务当作先前真值。
    positions = {f'{s.scene_id}#{b.beat_id}': (i, j, b)
                 for i, s in enumerate(graph.scenes) for j, b in enumerate(s.beats)}
    refs = sorted({ref for e in entries for ref in e.supporting_beat_refs},
                  key=lambda r: positions[r][:2])
    current = next(i for i, s in enumerate(graph.scenes) if s.scene_id == scene_id)
    prior_refs = [r for r in refs if positions[r][0] < current]
    continuity = tuple(f'{r}：{positions[r][2].event}' for r in prior_refs)
    releases = surface_engine_release(graph, scene_id)
    delta = tuple(type(d)(**d.model_dump()) for b in scene.beats for d in b.target_delta)
    ir = VerificationIR(scene_id=scene_id, obligations=owned, beats=beats_ir(scene_id, scene.beats),
                        entry_state=state, entry_knowledge=knowledge,
                        released_surfaces=releases, planned_delta=delta,
                        prohibitions=tuple(scene.delivery.avoid),
                        continuity_fact_view=continuity)
    facts = tuple(json.dumps(e.model_dump(mode='json'), ensure_ascii=False, sort_keys=True)
                  for e in (*state, *knowledge))
    brief = WriterBrief(
        scene_id=scene_id,
        realization_brief=scene.realization_brief,
        scene_driver_summary=(f'{scene.driver.focal_actor} 想「{scene.driver.immediate_want}」，'
                              f'受阻于「{scene.driver.opposition}」，转折：{scene.driver.turn}'),
        pov=scene.delivery.pov, voice=scene.delivery.voice,
        delivery_hints=tuple(b.delivery_hint for b in scene.beats if b.delivery_hint) + tuple(
            f'第 {b.beat_id} 拍起才可使用：' + '、'.join(intro.surfaces)
            for b in scene.beats for intro in b.surface_introductions),
        usable_surfaces=releases)
    # 内容身份不含全书 hash、请求 attempt、源坐标和观测元数据。
    business = {
        'contract': STR06_RC_VERSION, 'scene': scene.model_dump(mode='json'),
        'plan': scene_plan.model_dump(mode='json'),
        'ir': ir.model_dump(mode='json'), 'brief': brief.model_dump(mode='json'),
        'bindings': [e.model_dump(mode='json') for e in entries],
        'supporting_beats': [positions[r][2].model_dump(mode='json') for r in prior_refs],
    }
    for o in business['ir']['obligations']:
        o.pop('lineage')
    digest = _sha(business)
    context = WriterContext(scene_id=scene_id, execution_slice_sha256=digest,
                            verification_ir=ir, writer_brief=brief)
    slice_ = ExecutionSlice(scene_id=scene_id, consumed_obligation_ids=ids,
                            entry_state_sha256=_sha([e.model_dump(mode='json') for e in state]),
                            entry_knowledge_sha256=_sha([e.model_dump(mode='json') for e in knowledge]),
                            execution_slice_sha256=digest)
    return context, slice_
