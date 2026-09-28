#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""test_str06_semantic_delivery —— R0：语义送达与送达方式守卫＋故障注入底座。

【验什么】PLAN.md「R0 语义送达与送达方式守卫」的四条检查落在
`contracts/schema/consumption.py::STR06_FIELD_DELIVERY`（字段级送达登记）上：

  1. canonical 字段未登记即红——送达面的每个 canonical 叶子字段都必须有登记；
  2. 变异一个字段后，只有登记范围内的章，其写手视图摘要（`packet_sha256`）变化；
  3. 变异值只出现在其 `delivery_mode` 对应的送达面——R0 在现有投影面上断言
     「audit_only 材料进章包但不进阅读面；送达字段进阅读面且只进登记章」；
  4. 写手视图不出现 audit_only 内容——R0 锁定当前面已成立的部分（禁用字面、
     lineage、provenance、导航、称谓总表）；`sc01#b02` 式 ID／`established_by`／
     字段名清扫由 R2 WriterPacket 收口（`r2_face_pending` 登记待收口集合）。

【送达面（本文件的 canonical 面）】字段登记只覆盖送达链的正式产物：
`WriterBookIR`（`stage4/ir/writer_book_ir.json`）＋派生章包的
`RealizationContract`＋`HookContext`（`stage4/context/hook/hook.json`）＋
`NameMapV5`（`stage7/naming/name_map.json`）。回执是输出不是送达，不在面内。

【与 R2 的边界】当前 renderer（v4）仍把 VerificationIR 以 JSON 倾倒进 prompt，
因此 `established_by`、`beat_ref`、字段名等 audit 材料今天确实可见——这些条目在
登记表里标 `r2_face_pending=True`，R2 的 WriterPacket 分区渲染落地后必须清零
（R2 完成项含「R0 守卫全绿」）。本文件对 pending 条目只验章范围（检查 2），
不验阅读面（检查 3/4），pending 集合本身被钉成闭集，防静默漂移。

【故障注入底座（R3 复用）】`tests/str06_faults.py` 提供五类脚本化模型式错误
（证据 id 越界、未知 slug、漏配对、必保义务未承载、wire 多键），沿用
`tests/test_str06_product_entry.py` 的 sitecustomize 注入路径；本批只验证
「注入确实在真实链入口产生 typed 机械失败」，不接修订逻辑（R3 的事）。
"""
from __future__ import annotations

import subprocess
import sys
import types
from dataclasses import dataclass
from pathlib import Path
from typing import Callable, get_args, get_origin, Union

import pytest
from pydantic import BaseModel

from contracts.invariants import NODES, RETIRED_NODES
from contracts.schema.book_ir import RealizationContract, WriterBookIR
from contracts.schema.consumption import (
    STR06_CONSUMPTION_PROJECTIONS, STR06_FIELD_DELIVERY, DeliveryMode,
    DeliveryScope, FieldDeliverySpec,
)
from contracts.schema.context import KnowledgeEntry, PendingConfirmation, StateEntry
from contracts.schema.hook import HookContext, HookOpeningFact
from contracts.schema.naming import NameMapV5
from contracts.schema.state import EffectDomain, StateAddress
from novel.stages.common import (
    packet_sha256, project_chapter_packet, render_chapter_packet,
)
from tests.str06_faults import (
    FAULT_ENV, FAULTS, REPO_ENV, fault_sitecustomize_source,
)
from tests.test_str06_body import _green_book_ir
from tests.test_str06_hook import _green_pair


# ═══════════════════════════ 一、检查 1：canonical 字段未登记即红 ═══════════════════════════

def _model_under(annotation: object) -> tuple[type[BaseModel] | None, bool]:
    """注解下的唯一模型与是否为序列；非模型或混合并集都按叶子处理。"""
    origin = get_origin(annotation)
    if origin in (Union, types.UnionType):
        models = [a for a in get_args(annotation)
                  if isinstance(a, type) and issubclass(a, BaseModel)]
        rest = [a for a in get_args(annotation)
                if not (isinstance(a, type) and issubclass(a, BaseModel))
                and a is not type(None)]
        if len(models) == 1 and not rest:
            return _model_under(models[0])[0], False
        return None, False
    if origin in (tuple, list, frozenset, set, dict):
        args = [a for a in get_args(annotation) if a is not Ellipsis]
        if len(args) == 1:
            return _model_under(args[0])[0], True
        return None, False
    if isinstance(annotation, type) and issubclass(annotation, BaseModel):
        return annotation, False
    return None, False


def _face_paths(prefix: str, model: type[BaseModel], out: set[str]) -> None:
    """送达面的叶子路径全集：模型递归、序列加 ``[]``、exclude 字段按叶子收。"""
    for name, field in model.model_fields.items():
        path = f'{prefix}.{name}'
        if field.exclude:               # `_provenance` 传输身份，不进业务载荷
            out.add(path)
            continue
        sub, repeated = _model_under(field.annotation)
        if sub is None:
            out.add(path)
        else:
            _face_paths(f'{path}[]' if repeated else path, sub, out)


def _delivery_face() -> set[str]:
    face: set[str] = set()
    _face_paths('writer_book_ir', WriterBookIR, face)
    _face_paths('chapter_packet.realization_contract', RealizationContract, face)
    _face_paths('hook_context', HookContext, face)
    _face_paths('name_map', NameMapV5, face)
    # packet_metrics.json 是 dict 落盘的观测面（I-38：不进领域 schema，无模型可
    # 走）；以文件级路径进面——登记为 audit_only／not_delivered。
    face.add('stage4/acceptance/packet_metrics.json')
    return face


def test_every_canonical_field_of_the_delivery_face_is_registered():
    face = _delivery_face()
    unregistered = sorted(face - set(STR06_FIELD_DELIVERY))
    assert not unregistered, f'送达面存在未登记字段：{unregistered}'
    stale = sorted(set(STR06_FIELD_DELIVERY) - face)
    assert not stale, f'登记表存在送达面之外的路径：{stale}'


def test_registry_entries_are_well_formed():
    for path, spec in STR06_FIELD_DELIVERY.items():
        assert isinstance(spec, FieldDeliverySpec)
        assert spec.field_path == path, f'{path} 的 field_path 与键不一致'
        assert spec.consumers
        # 仅审计的条目不得声明写手消费者；送达写手的条目必须有身份
        if DeliveryMode.AUDIT_ONLY is spec.delivery_mode:
            assert not ({'writer_packet', 'hook_context'} & set(spec.consumers)), path
        if spec.r2_face_pending:
            assert spec.mode_note, f'{path} 标记 r2_face_pending 必须写明待收口原因'


def test_r2_pending_face_gaps_form_a_closed_documented_set():
    """R2 收口清单是闭集：WriterPacket 分区渲染落地后必须**清零**（R2 完成项
    「R0 守卫全绿」）；后续任何新 pending 都必须显式登记待收口原因。"""
    pending = {path for path, spec in STR06_FIELD_DELIVERY.items()
               if spec.r2_face_pending}
    assert pending == set(), (
        f'R2 引擎批后不得残留待收口条目（每条须写明原因并登记收口批次）：'
        f'{sorted(pending)}')


def test_all_seven_delivery_modes_are_declared():
    declared = {spec.delivery_mode for spec in STR06_FIELD_DELIVERY.values()}
    # R2 引擎批起七档齐全：background 落在 background_state／background_knowledge
    # （编译器按参与者补齐的入场基线，R2.3）。
    assert declared == set(DeliveryMode)
    background_fields = [path for path, spec in STR06_FIELD_DELIVERY.items()
                         if spec.delivery_mode is DeliveryMode.BACKGROUND]
    assert background_fields and all(
        '.background_state[' in p or '.background_knowledge[' in p
        for p in background_fields), background_fields


# ═══════════════ 二、检查 2＋3：变异只动登记章；变异值只进对应送达面 ═══════════════

def _ordinals(ir: WriterBookIR) -> list[int]:
    return [c.chapter_ordinal for c in ir.chapters]


def _digests(ir: WriterBookIR) -> dict[int, str]:
    return {n: packet_sha256(project_chapter_packet(ir, n)) for n in _ordinals(ir)}


def _renders(ir: WriterBookIR) -> dict[int, str]:
    return {n: render_chapter_packet(project_chapter_packet(ir, n))
            for n in _ordinals(ir)}


def _expected_chapters(spec: FieldDeliverySpec, located: int | None,
                       total: int) -> set[int]:
    if spec.scope is DeliveryScope.EVERY_CHAPTER:
        return set(range(1, total + 1))
    if spec.scope in (DeliveryScope.CHAPTER_FIELD, DeliveryScope.SCENE_OWNER):
        assert located is not None, '章内字段的变异用例必须给出定位章'
        return {located}
    return set()          # hook_only／naming_only／not_delivered：不进正文章包


def _patch_chapter(ir: WriterBookIR, ordinal: int, **updates) -> WriterBookIR:
    chapters = tuple(ch.model_copy(update=updates) if ch.chapter_ordinal == ordinal
                     else ch for ch in ir.chapters)
    return ir.model_copy(update={'chapters': chapters})


def _patch_scene_ir(ir: WriterBookIR, ordinal: int, scene_id: str,
                    **vir_updates) -> WriterBookIR:
    """只改某场 VerificationIR 的字段（其余章/场原对象复用）。"""
    def patch(ch):
        scenes = tuple(
            sc.model_copy(update={'verification_ir':
                                  sc.verification_ir.model_copy(update=vir_updates)})
            if sc.scene_id == scene_id else sc for sc in ch.scenes)
        return ch.model_copy(update={'scenes': scenes})
    chapters = tuple(patch(ch) if ch.chapter_ordinal == ordinal else ch
                     for ch in ir.chapters)
    return ir.model_copy(update={'chapters': chapters})


def _patch_scene_brief(ir: WriterBookIR, ordinal: int, scene_id: str,
                       **brief_updates) -> WriterBookIR:
    def patch(ch):
        scenes = tuple(
            sc.model_copy(update={'writer_brief':
                                  sc.writer_brief.model_copy(update=brief_updates)})
            if sc.scene_id == scene_id else sc for sc in ch.scenes)
        return ch.model_copy(update={'scenes': scenes})
    chapters = tuple(patch(ch) if ch.chapter_ordinal == ordinal else ch
                     for ch in ir.chapters)
    return ir.model_copy(update={'chapters': chapters})


def _patch_beat(ir: WriterBookIR, ordinal: int, scene_id: str, ref: str,
                **beat_updates):
    def patch_beats(vir):
        beats = tuple(b.model_copy(update=beat_updates) if b.beat_ref == ref
                      else b for b in vir.beats)
        return vir.model_copy(update={'beats': beats})
    return _patch_scene_ir(ir, ordinal, scene_id,
                           **{'beats': patch_beats(
                               _scene(ir, ordinal, scene_id).verification_ir).beats})


def _patch_first_delta(ir: WriterBookIR, ordinal: int, scene_id: str, ref: str,
                       **delta_updates):
    scene = _scene(ir, ordinal, scene_id)
    beat = next(b for b in scene.verification_ir.beats if b.beat_ref == ref)
    deltas = list(beat.deltas)
    deltas[0] = deltas[0].model_copy(update=delta_updates)

    def patch_beats(vir):
        return tuple(b.model_copy(update={'deltas': tuple(deltas)})
                     if b.beat_ref == ref else b for b in vir.beats)
    return _patch_scene_ir(ir, ordinal, scene_id, beats=patch_beats(
        scene.verification_ir))


def _patch_obligation(ir: WriterBookIR, ordinal: int, scene_id: str,
                      oid: str, **updates):
    scene = _scene(ir, ordinal, scene_id)
    obligations = tuple(o.model_copy(update=updates)
                        if o.obligation_id == oid else o
                        for o in scene.verification_ir.obligations)
    return _patch_scene_ir(ir, ordinal, scene_id, obligations=obligations)


def _scene(ir: WriterBookIR, ordinal: int, scene_id: str):
    chapter = next(c for c in ir.chapters if c.chapter_ordinal == ordinal)
    return next(s for s in chapter.scenes if s.scene_id == scene_id)


_INJECTED_ENTRY = StateEntry(domain=EffectDomain.CHARACTER, key='marital_status',
                             subjects=('yun_niang',), holder=None,
                             value='变异入场九九', status='set',
                             established_by='sc01#b02')
_INJECTED_KNOWLEDGE = KnowledgeEntry(
    holder='yun_niang', proposition='变异知情九九', established_by='sc01#b02',
    subjects=('fu_jun',), key='debt_agreement', status='set', mode='known')
_INJECTED_PENDING = PendingConfirmation(
    fact='变异悬念九九', key='jade_secret', subjects=(),
    address=StateAddress(domain=EffectDomain.KNOWLEDGE, key='jade_secret',
                         subjects=('fu_jun',), holder='yun_niang'),
    position='sc02#b01', in_scene=False)


@dataclass(frozen=True)
class _BodyMutation:
    path: str
    located: int | None
    sentinel: str | None
    apply: Callable[[WriterBookIR], WriterBookIR]


def _book_brief(ir: WriterBookIR, **updates) -> WriterBookIR:
    return ir.model_copy(update={'book_brief':
                                 ir.book_brief.model_copy(update=updates)})


def _body_mutations() -> list[_BodyMutation]:
    def nav_line(ir):
        nav = ir.book_brief.chapters[0].model_copy(
            update={'scene_lines': ('变异导航九九',)})
        return _book_brief(ir, chapters=(nav,) + ir.book_brief.chapters[1:])

    def schedule(ir):
        entry = ir.book_brief.release_schedule[0].model_copy(
            update={'from_chapter': 2})
        return _book_brief(ir, release_schedule=(entry,))

    def withheld(ir):
        chapter = next(c for c in ir.chapters if c.chapter_ordinal == 2)
        return _patch_chapter(ir, 2, withheld_literals=chapter.withheld_literals +
                              ('禁露字面九九',))

    def surfaces(ir):
        chapter = next(c for c in ir.chapters if c.chapter_ordinal == 2)
        return _patch_chapter(ir, 2, released_surfaces=('变异面九九',))

    def scene_release(ir):
        scene = _scene(ir, 1, 'sc01')
        releases = list(scene.verification_ir.released_surfaces)
        if releases:
            releases[0] = releases[0].model_copy(
                update={'surfaces': releases[0].surfaces + ('变异称谓九九',)})
        return _patch_scene_ir(ir, 1, 'sc01', released_surfaces=tuple(releases))

    def usable(ir):
        scene = _scene(ir, 1, 'sc01')
        releases = list(scene.writer_brief.usable_surfaces)
        assert releases, '绿基座的可用称谓面不应为空'
        releases[0] = releases[0].model_copy(
            update={'surfaces': releases[0].surfaces + ('变异可用九九',)})
        return _patch_scene_brief(ir, 1, 'sc01', usable_surfaces=tuple(releases))

    def entry_state(ir):
        return _patch_scene_ir(ir, 1, 'sc01', entry_state=(_INJECTED_ENTRY,))

    def entry_established(ir):
        # 只改既有条目的来源锚（audit 位）：交付值保持不变，阅读面必须不动
        scene = _scene(ir, 1, 'sc01')
        entries = list(scene.verification_ir.entry_state)
        assert entries, '绿基座的入场视图不应为空'
        entries[0] = entries[0].model_copy(update={'established_by': 'sc01#b01'})
        return _patch_scene_ir(ir, 1, 'sc01', entry_state=tuple(entries))

    def entry_knowledge(ir):
        return _patch_scene_ir(ir, 2, 'sc02', entry_knowledge=(_INJECTED_KNOWLEDGE,))

    def knowledge_mode(ir):
        mutated = _INJECTED_KNOWLEDGE.model_copy(update={'mode': 'suspected'})
        return _patch_scene_ir(ir, 2, 'sc02', entry_knowledge=(mutated,))

    def pending(ir):
        return _patch_scene_ir(ir, 1, 'sc01',
                               pending_reader_confirmations=(_INJECTED_PENDING,))

    def pending_fact(ir):
        mutated = _INJECTED_PENDING.model_copy(update={'fact': '再变异悬念八八'})
        return _patch_scene_ir(ir, 1, 'sc01', pending_reader_confirmations=(mutated,))

    def beats(ir):
        return _patch_beat(ir, 1, 'sc01', 'sc01#b01', event='变异事件九九')

    def beat_ref(ir):
        return _patch_beat(ir, 1, 'sc01', 'sc01#b01', beat_ref='sc01#b09')

    def delta_value(ir):
        return _patch_first_delta(ir, 1, 'sc01', 'sc01#b01', value='变异效应九九')

    def planned_delta(ir):
        scene = _scene(ir, 1, 'sc01')
        deltas = list(scene.verification_ir.planned_delta)
        deltas[0] = deltas[0].model_copy(update={'value': '变异计划九九'})
        return _patch_scene_ir(ir, 1, 'sc01', planned_delta=tuple(deltas))

    def slice_sha(ir):
        scene = _scene(ir, 1, 'sc01')
        scene2 = scene.model_copy(update={'execution_slice_sha256': 'b' * 64})
        chapter = next(c for c in ir.chapters if c.chapter_ordinal == 1)
        scenes = tuple(s if s.scene_id != 'sc01' else scene2 for s in chapter.scenes)
        return _patch_chapter(ir, 1, scenes=scenes)

    def obligation(ir):
        return _patch_obligation(ir, 1, 'sc01', 'ob01', statement='变异义务九九')

    def obligation_id(ir):
        return _patch_obligation(ir, 1, 'sc01', 'ob01', obligation_id='ob99')

    def lineage(ir):
        scene = _scene(ir, 1, 'sc01')
        obligations = tuple(
            o.model_copy(update={'lineage': o.lineage.model_copy(
                update={'blueprint_refs': ('b99_99',)})})
            for o in scene.verification_ir.obligations)
        return _patch_scene_ir(ir, 1, 'sc01', obligations=obligations)

    def realization(ir):
        return _patch_scene_brief(ir, 1, 'sc01', realization_brief='变异导演九九')

    def hints(ir):
        scene = _scene(ir, 1, 'sc01')
        return _patch_scene_brief(ir, 1, 'sc01',
                                  delivery_hints=scene.writer_brief.delivery_hints +
                                  ('变异提示九九',))

    def chapter_purpose(ir):
        chapter = next(c for c in ir.chapters if c.chapter_ordinal == 2)
        return _patch_chapter(ir, 2, purpose='变异章目九九')

    def chapter_budget(ir):
        chapter = next(c for c in ir.chapters if c.chapter_ordinal == 2)
        return _patch_chapter(ir, 2, length_budget=chapter.length_budget + 200)

    def world_rules(ir):
        return _book_brief(ir, world_rules=ir.book_brief.world_rules
                           + ('变异边界九九',))

    def entry_transition(ir):
        from contracts.schema.book_ir import ChapterContinuity
        from contracts.schema.design import ChapterEntry
        chapter = next(c for c in ir.chapters if c.chapter_ordinal == 2)
        continuity = ChapterContinuity(
            entry=ChapterEntry(transition='pov_shift', recap='do_not_recap'),
            previous_ending=chapter.continuity.previous_ending)
        return _patch_chapter(ir, 2, continuity=continuity)

    def thread_ops(ir):
        from contracts.schema.context import ThreadOpIR
        return _patch_scene_ir(ir, 2, 'sc02', thread_ops=(ThreadOpIR(
            ref='th01', op='advance', kind='question', subject='yun_niang',
            text='玉佩藏着什么', progress='变异线程九九'),))

    def open_threads(ir):
        from contracts.schema.context import OpenThreadIR
        return _patch_scene_ir(ir, 2, 'sc02', open_threads=(OpenThreadIR(
            ref='th02', kind='goal', subject='fu_jun', text='变异开放线九九',
            opened_at='sc01#b01'),))

    return [
        # ── 书级（every_chapter）──
        _BodyMutation('writer_book_ir.book_brief.book_style', None, '变异风格九九',
                      lambda ir: _book_brief(ir, book_style='变异风格九九')),
        _BodyMutation('writer_book_ir.book_brief.world_rules', None, '变异边界九九',
                      world_rules),
        _BodyMutation('writer_book_ir.book_brief.length_band_low', None, None,
                      lambda ir: _book_brief(ir, length_band_low=0.61)),
        _BodyMutation('writer_book_ir.book_brief.book_title', None, '变异书名九九',
                      lambda ir: _book_brief(ir, book_title='变异书名九九')),
        # chapter_packet.realization_contract.* 是派生投影（protocol_version 与
        # 篇幅带由协议版本/book band/章预算决定），其登记由检查 1 守，变异面经
        # 上两行源头字段（book_style 档与 length_band/length_budget 档）覆盖。
        # ── 审计面（not_delivered：不进任何章包）──
        _BodyMutation('writer_book_ir.book_brief.chapters[].scene_lines', None,
                      '变异导航九九', nav_line),
        _BodyMutation('writer_book_ir.book_brief.release_schedule[].from_chapter',
                      None, None, schedule),
        # ── 章级 ──
        _BodyMutation('writer_book_ir.chapters[].purpose', 2, '变异章目九九',
                      chapter_purpose),
        _BodyMutation('writer_book_ir.chapters[].length_budget', 2, None,
                      chapter_budget),
        _BodyMutation('writer_book_ir.chapters[].withheld_literals', 2,
                      '禁露字面九九', withheld),
        _BodyMutation('writer_book_ir.chapters[].released_surfaces', 2,
                      '变异面九九', surfaces),
        # ── R2 契约面：承接与线程（Machine 层已进章包，阅读面归分区渲染）──
        _BodyMutation('writer_book_ir.chapters[].continuity.entry.transition', 2,
                      None, entry_transition),
        _BodyMutation('writer_book_ir.chapters[].scenes[].verification_ir'
                      '.thread_ops[].progress', 2, '变异线程九九', thread_ops),
        _BodyMutation('writer_book_ir.chapters[].scenes[].verification_ir'
                      '.open_threads[].text', 2, '变异开放线九九', open_threads),
        # ── 场景/条目级 ──
        _BodyMutation('writer_book_ir.chapters[].scenes[].writer_brief'
                      '.realization_brief', 1, '变异导演九九', realization),
        _BodyMutation('writer_book_ir.chapters[].scenes[].writer_brief'
                      '.delivery_hints', 1, '变异提示九九', hints),
        _BodyMutation('writer_book_ir.chapters[].scenes[].writer_brief'
                      '.usable_surfaces[].surfaces', 1, '变异可用九九', usable),
        _BodyMutation('writer_book_ir.chapters[].scenes[].execution_slice_sha256',
                      1, None, slice_sha),
        _BodyMutation('writer_book_ir.chapters[].scenes[].verification_ir'
                      '.beats[].event', 1, '变异事件九九', beats),
        _BodyMutation('writer_book_ir.chapters[].scenes[].verification_ir'
                      '.beats[].beat_ref', 1, None, beat_ref),
        _BodyMutation('writer_book_ir.chapters[].scenes[].verification_ir'
                      '.beats[].deltas[].value', 1, '变异效应九九', delta_value),
        _BodyMutation('writer_book_ir.chapters[].scenes[].verification_ir'
                      '.planned_delta[].value', 1, '变异计划九九', planned_delta),
        _BodyMutation('writer_book_ir.chapters[].scenes[].verification_ir'
                      '.obligations[].statement', 1, '变异义务九九', obligation),
        _BodyMutation('writer_book_ir.chapters[].scenes[].verification_ir'
                      '.obligations[].obligation_id', 1, None, obligation_id),
        _BodyMutation('writer_book_ir.chapters[].scenes[].verification_ir'
                      '.obligations[].lineage.blueprint_refs', 1, 'b99_99', lineage),
        _BodyMutation('writer_book_ir.chapters[].scenes[].verification_ir'
                      '.entry_state[].value', 1, '变异入场九九', entry_state),
        _BodyMutation('writer_book_ir.chapters[].scenes[].verification_ir'
                      '.entry_state[].established_by', 1, None, entry_established),
        _BodyMutation('writer_book_ir.chapters[].scenes[].verification_ir'
                      '.entry_knowledge[].proposition', 2, '变异知情九九',
                      entry_knowledge),
        _BodyMutation('writer_book_ir.chapters[].scenes[].verification_ir'
                      '.entry_knowledge[].mode', 2, None, knowledge_mode),
        _BodyMutation('writer_book_ir.chapters[].scenes[].verification_ir'
                      '.released_surfaces[].surfaces', 1, '变异称谓九九',
                      scene_release),
        _BodyMutation('writer_book_ir.chapters[].scenes[].verification_ir'
                      '.pending_reader_confirmations[].fact', 1, '变异悬念九九',
                      pending),
        _BodyMutation('writer_book_ir.chapters[].scenes[].verification_ir'
                      '.pending_reader_confirmations[].fact', 1, '再变异悬念八八',
                      pending_fact),
        _BodyMutation('writer_book_ir.chapters[].scenes[].verification_ir'
                      '.prohibitions', 2, '变异禁区九九',
                      lambda ir: _patch_scene_ir(ir, 2, 'sc02',
                                                 prohibitions=('变异禁区九九',))),
        _BodyMutation('writer_book_ir.chapters[].scenes[].verification_ir'
                      '.continuity_fact_view', 2, '变异承接九九',
                      lambda ir: _patch_scene_ir(
                          ir, 2, 'sc02',
                          continuity_fact_view=('sc01#b01：变异承接九九',))),
    ]


@pytest.fixture(scope='module')
def green_ir() -> WriterBookIR:
    return _green_book_ir()[3]


@pytest.mark.parametrize('case', _body_mutations(),
                         ids=lambda case: case.path.replace('writer_book_ir.', ''))
def test_mutation_reaches_only_registered_chapters_and_face(green_ir, case):
    """检查 2＋3：变异只动登记章的章包摘要；送达值只出现在对应送达面。"""
    spec = STR06_FIELD_DELIVERY[case.path]
    total = len(green_ir.chapters)
    expected = _expected_chapters(spec, case.located, total)
    before_d, before_r = _digests(green_ir), _renders(green_ir)
    after = case.apply(green_ir)
    after_d, after_r = _digests(after), _renders(after)
    changed = {n for n in after_d if after_d[n] != before_d[n]}
    assert changed == expected, (
        f'{case.path} 的章包摘要变化面 {sorted(changed)} 与登记范围 '
        f'{sorted(expected)}（scope={spec.scope.value}）不一致')
    if spec.r2_face_pending:
        return                      # 阅读面清扫归 R2；这里只锁章范围
    if spec.delivery_mode is DeliveryMode.AUDIT_ONLY:
        assert after_r == before_r, (
            f'{case.path} 是 audit_only：验证器材料不得改变写手阅读面')
        if case.sentinel:
            assert all(case.sentinel not in text for text in after_r.values()), (
                f'{case.path} 的变异值泄漏进了写手阅读面')
        return
    face_changed = {n for n in after_r if after_r[n] != before_r[n]}
    assert face_changed == expected, (
        f'{case.path} 的阅读面变化面 {sorted(face_changed)} 与登记范围不一致')
    if case.sentinel:
        for ordinal, text in after_r.items():
            assert (case.sentinel in text) == (ordinal in expected), (
                f'{case.path} 的变异值出现在了登记范围之外的章 {ordinal}')


def test_name_map_mutation_never_reaches_body_packets(green_ir):
    """naming_only 面：NameMap 的任何变异都不触碰正文章包（Apply 才是消费点）。"""
    name_map = NameMapV5.model_validate({
        'entries': [{'char_ref': 'fu_jun', 'name': {'family': '顾'},
                     'aliases': {}, 'keep_reason': None}]})
    before = _digests(green_ir)
    mutated = name_map.model_copy(update={'entries': (
        name_map.entries[0].model_copy(
            update={'name': name_map.entries[0].name.model_copy(
                update={'family': '变异姓九九'})}),)})
    assert STR06_FIELD_DELIVERY['name_map.entries[].name.family'].scope \
        is DeliveryScope.NAMING_ONLY
    assert _digests(green_ir) == before and mutated.entries[0].name.family \
        == '变异姓九九'


# ═══════════════ 三、检查 4：写手视图不出现 audit_only 内容 ═══════════════

def test_writer_view_contains_no_audit_only_material(green_ir):
    renders = _renders(green_ir)
    all_text = '\n'.join(renders.values())
    # ① 禁用字面清单本身不进阅读面：整列清零后阅读面逐字节不变（清单是
    #    validator 检查面）。语义文本（义务陈述等）中出现的源侧字面属 R2
    #    renderer 的目标名解析问题，不是本检查的对象。
    stripped = green_ir.model_copy(update={'chapters': tuple(
        c.model_copy(update={'withheld_literals': ()}) for c in green_ir.chapters)})
    assert _renders(stripped) == renders
    # ② 来源账、导航与称谓总表（审计面）不进阅读面。书名不用裸子串断言——
    #    短书名可能撞上语义文本的正常用词（如「报恩之约」），它的阅读面隔离
    #    由检查 3 的变异用例（变异书名九九）钉住。
    for marker in ('lineage', 'blueprint_refs', 'character_ids', '_provenance',
                   'release_schedule', 'scene_lines'):
        assert marker not in all_text, f'审计材料 {marker!r} 泄进阅读面'


# ═══════════════ 四、族登记节点名必须是现役节点 ═══════════════

def test_projection_families_name_live_nodes_only():
    live, retired = set(NODES), set(RETIRED_NODES)
    for family, spec in STR06_CONSUMPTION_PROJECTIONS.items():
        for owner in spec.source_owners:
            assert owner in live, (
                f'投影族 {family} 引用了未登记节点 {owner}（对照 NODES 现役名）')
            assert owner not in retired, f'投影族 {family} 引用了退役节点 {owner}'


# ═══════════════ 五、Hook 送达面（HookContext 的变异守卫） ═══════════════

@pytest.fixture(scope='module')
def green_hook() -> HookContext:
    return _green_pair()[2]


def _hook_prompt(context: HookContext) -> str:
    from novel.stages.stage5.hook_writer import build_hook_user_prompt
    return build_hook_user_prompt(context)


@pytest.mark.parametrize('path,update,sentinel,expect_prompt_change', [
    ('hook_context.design_brief', {'design_brief': '变异钩子导演九九'},
     '变异钩子导演九九', True),
    ('hook_context.delivery_brief', {'delivery_brief': '变异钩子口吻九九'},
     '变异钩子口吻九九', True),
    ('hook_context.target_chars', {'target_chars': 320}, None, True),
    ('hook_context.forbidden_reveals',
     {'forbidden_reveals': ('变异禁揭九九',)}, '变异禁揭九九', True),
    ('hook_context.materials[].presentation', {}, '变异材料九九', True),
    ('hook_context.opening_facts[].text', {}, '变异起笔九九', True),
])
def test_hook_mutations_reach_only_hook_face(green_hook, green_ir, path, update,
                                             sentinel, expect_prompt_change):
    """hook_only 面：Hook 材料变异只动 Hook 阅读面，正文章包摘要一律不动。"""
    context = green_hook
    if path == 'hook_context.materials[].presentation':
        materials = list(context.materials)
        materials[0] = materials[0].model_copy(
            update={'presentation': '变异材料九九'})
        context = context.model_copy(update={'materials': tuple(materials)})
    elif path == 'hook_context.opening_facts[].text':
        context = context.model_copy(update={'opening_facts': (
            HookOpeningFact(audience='reader', established_by='init',
                            text='变异起笔九九'),)})
    else:
        context = context.model_copy(update=update)
    spec = STR06_FIELD_DELIVERY[path]
    assert spec.scope is DeliveryScope.HOOK_ONLY, 'Hook 材料不得声明进正文章包'
    body_digests = _digests(green_ir)
    before, after = _hook_prompt(green_hook), _hook_prompt(context)
    assert (after != before) == expect_prompt_change
    if sentinel:
        assert (sentinel in after) == expect_prompt_change
    assert _digests(green_ir) == body_digests   # 正文摘要与 Hook 面互不相扰


def test_hook_audit_only_material_stays_out_of_body_face(green_hook, green_ir):
    """Hook 的 audit_only 面：provenance 与禁用字面都不进 Hook 阅读面。

    R2 分层渲染落地：withheld_literals 是 validator 检查材料，整列清零后
    Hook prompt 逐字节不变（此前随 JSON 倾倒可见，已收口）。"""
    prompt = _hook_prompt(green_hook)
    assert '_provenance' not in prompt
    spec = STR06_FIELD_DELIVERY['hook_context.withheld_literals']
    assert spec.delivery_mode is DeliveryMode.AUDIT_ONLY
    assert _hook_prompt(green_hook.model_copy(
        update={'withheld_literals': ('钩禁字面九九',)})) == prompt, (
        '禁用字面是 validator 材料，不得进入 Hook 阅读面')


# ═══════════════ 六、故障注入 fake 底座（R3 复用；本批只验证注入有效） ═══════════════

def _fault_run(tmp_path, fault, name):
    """用（可选故障化的）wires 走真实链入口：PaidRequestSession＋fake transport。

    返回 ``(runner, repo)``：runner 触发一次 `run_workflow`（body_chapter_limit=0，
    正文不在故障注入面内）；repo 供盘面证据断言。
    """
    from contracts.schema.hook import HookConstraints
    from novel.stages.chain import run_workflow
    from tests.str06_faults import FaultTransport, looped_full_chain_wires
    from tests.str06_facilities import json_ask, text_ask
    from tests.test_str06_body import _chapters, _make_session

    wires = looped_full_chain_wires()
    repo, session, _transport = _make_session(
        tmp_path, wires, name=name, transport=FaultTransport(wires, fault))

    def ask_body(owner, prompt):
        raise AssertionError('故障用例不得走到正文 owner')

    def runner():
        return run_workflow(
            repository=repo, chapters=_chapters(), ask=json_ask(session),
            ask_text=text_ask(session), ask_body=ask_body, budget=2000,
            through='stage7', book_title='报恩',
            hook_constraints=HookConstraints(target_chars=10, hard_max_chars=360),
            body_chapter_limit=0)
    return runner, repo


def test_fault_base_green_baseline_completes_the_chain(tmp_path):
    """基线必须是绿的：带跨章悬念的 wires（无故障）能走通 stage2→stage4 编译。"""
    runner, repo = _fault_run(tmp_path, None, 'green')
    report = runner()
    assert report['assembly']['status'] == 'not_ready'   # body_chapter_limit=0
    assert 'body_not_implemented' in report['assembly']['reason']
    # 编译真跑到 Stage4：双真源与 WriterBookIR 都已发布
    assert repo.path('stage2/characters/characters.json').is_file()
    assert repo.path('stage4/ir/writer_book_ir.json').is_file()


@pytest.mark.parametrize('fault,signature,disk_gate', [
    ('evidence_out_of_range', ('book 校验失败', 'e99_99'),
     lambda repo: not repo.path('stage2/observations/book.json').exists()),
    ('unknown_slug', ('compile 机械缺陷', 'participants 引用未知人物 ghost'),
     lambda repo: (repo.path('stage2/observations/associate.json').is_file()
                   and not repo.path('stage2/characters/characters.json')
                   .is_file())),
    ('missing_pairing', ('未决', 'q_yu'),
     lambda repo: (repo.path('stage2/observations/associate.json').is_file()
                   and not repo.path('stage2/characters/characters.json')
                   .is_file())),
    ('obligation_unbound', ('必保义务 ob01 尚未承载',),
     # bind 对「必保义务进 unbound」的稿是合法返回（owner 路由在案），拦下它的
     # 是 stage4 compile 的义务覆盖 Guard——R3 的回派规则正是把这类问题按
     # unbound.owner 回 body_design／preserve。
     lambda repo: (repo.path('stage4/grounding/semantic_binding.json').is_file()
                   and repo.path('stage4/acceptance/guard/compile.json').is_file()
                   and not repo.path('stage4/compiled/execution_plan.json')
                   .is_file())),
    ('duplicate_json_key', ('book 校验失败', '重复键'),
     lambda repo: not repo.path('stage2/observations/book.json').exists()),
])
def test_each_injected_fault_fails_fast_at_its_mechanical_gate(
        tmp_path, fault, signature, disk_gate):
    """五类脚本化模型式错误各自在真实链入口被 typed 机械闸拦下（R3 的修复对象）。"""
    runner, repo = _fault_run(tmp_path, fault, fault)
    with pytest.raises(ValueError) as excinfo:
        runner()
    message = str(excinfo.value)
    for needle in signature:
        assert needle in message, f'{fault} 的失败信息缺 {needle!r}：{message}'
    assert disk_gate(repo), f'{fault} 的盘面证据与预期拦截位置不符'


def test_sitecustomize_injection_is_env_gated(tmp_path):
    """sitecustomize 注入路径可用且受环境门控制（R3/R5 子进程复用）。"""
    fake_site = tmp_path / 'fault_site'
    fake_site.mkdir()
    (fake_site / 'sitecustomize.py').write_text(fault_sitecustomize_source(),
                                                encoding='utf-8')
    repo_root = str(Path(__file__).resolve().parents[1])
    probe = ('import novel.stages.chain as c; '
             'print(c.DeepSeekTransport.__module__)')
    common = {**__import__('os').environ, 'PYTHONPATH': str(fake_site)}

    import os
    ungated = subprocess.run([sys.executable, '-B', '-c', probe], cwd=repo_root,
                             capture_output=True, text=True, env=common)
    assert ungated.returncode == 0, ungated.stderr
    assert 'novel.llm.transport' in ungated.stdout      # 无环境门：不注入

    gated = dict(common)
    gated[FAULT_ENV] = 'missing_pairing'
    gated[REPO_ENV] = repo_root
    injected = subprocess.run([sys.executable, '-B', '-c', probe], cwd=repo_root,
                              capture_output=True, text=True, env=gated)
    assert injected.returncode == 0, injected.stderr
    assert 'tests.str06_faults' in injected.stdout      # 有环境门：注入 FaultTransport


def test_fault_kinds_are_the_documented_five():
    assert FAULTS == ('evidence_out_of_range', 'unknown_slug', 'missing_pairing',
                      'obligation_unbound', 'duplicate_json_key')
    source = fault_sitecustomize_source()
    assert FAULT_ENV in source and REPO_ENV in source
    assert 'install_fault_transport' in source
