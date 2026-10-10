#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""test_str06_body —— D2 章包写手链的免费反例与 stage 级验证（B4）。

【验证边界】章预算确定性分配；双视图 IR 与 canonical 同源（brief 原样投影、
事实不遗漏）；**Writer/Validator 只收当前章包**（D2：请求不含前文正文、其他
章 IR、导航与称谓总表；验证材料 compile 时算好）；章包失效的精确性（导演
说明变化只换该章章包）；机械检查全分支；有限重写、截断即停（D6）与确定性
候选选择；回执集合核对；经真实 repository + 请求边界（fake transport）跑到
最终成书、同根恢复零新调用、**集合恢复只补缺章**、交付字节一致；并发度不进
语义身份。成文质量归 B6 阅读，不在本文件伪造。
"""
from __future__ import annotations

import re
import time
from copy import deepcopy

import pytest

from novel.llm.ledger import CostLedger, ledger_path
from novel.llm.paid_requests import PaidRequestSession
from novel.llm.transport import ChatRequest, ChatResponse
from novel.store import Str06Repository
from novel.stages.stage4.book_ir import allocate_chapter_budgets, chapter_surface_releases, compile_writer_book_ir
from novel.stages.stage4.book_ir import chapter_withheld_literals
from novel.stages.stage5.body_checks import validate_chapter_prose
from novel.stages.stage5.body import (
    AnsweredText, sha256_text, write_chapter,
)
from novel.stages.common import (
    chapter_identities, packet_sha256, project_chapter_packet,
    render_chapter_packet,
)
from novel.stages.stage7.assembly import verify_chapter_receipts
from novel.stages.chain import derive_total_length_budget, run_workflow
from tests.str06_facilities import anchor_stub, make_context
from tests.test_str06_hook import _body_wire, _characters, _full_chain_wires
from novel.stages.stage4.design import assemble_body_design
from novel.stages.stage4.compile import compile_execution_plan
from novel.stages.stage4.grounding import derive_semantic_binding
from tests.str06_expression_inputs import (
    sample_narration, sample_requirements,
)

H64 = 'a' * 64


def test_source_budget_ignores_whitespace_and_rejects_empty_body():
    """空白不得抬高正文预算；空白源章仍是无正文，不生成付费设计预算。"""
    assert derive_total_length_budget([{'text': '甲' * 900 + '\n' * 150}]) == 1000
    with pytest.raises(ValueError):
        derive_total_length_budget([{'text': ' \n\t\u3000'}])


# ───────────────────────── fixture：小型两章设计 ─────────────────────────

def _green_design():
    body, errors = assemble_body_design(_body_wire(),
                                        known_char_ids={'fu_jun', 'yun_niang'}, expression_requirements=sample_requirements(), source_narration=sample_narration())
    assert body is not None and not errors, errors
    return body


def _site_index(body):
    """`场景#拍` → (scene 序, beat 序)——binding 派生夹具的序号投影。"""
    return {f'{s.scene_id}#{b.beat_id}': (i, j)
            for i, s in enumerate(body.scenes) for j, b in enumerate(s.beats)}


def _green_binding(body):
    from contracts.schema.grounding import (
        PreserveCheckpoint, SourceLineage, SourceObligation,
    )
    from contracts.schema.policy import PreserveKind
    ob = SourceObligation(
        obligation_id='ob01', kind=PreserveKind.EVENT_CORE,
        statement='苏芸娘嫁入陆家且夫君立下旧约',
        anchors=anchor_stub(),
        lineage=SourceLineage(blueprint_refs=('b01_01',), character_ids=('yun_niang', 'fu_jun')),
        must_preserve=True, satisfaction='正文呈现婚约成立且夫君立下旧约')
    cp = PreserveCheckpoint(
        checkpoint_id='cp', policy_id='p', policy_version=1, characters_sha256=H64,
        blueprints_sha256=H64, provider='x', model='y', request_sha256='r' * 64,
        obligations=(ob,), checkpoint_sha256=H64)
    sites = _site_index(body)
    placements = {'ob01': [sites['sc01#b01'], sites['sc01#b02']]}
    binding, errors = derive_semantic_binding(placements, body, cp)
    assert binding is not None and not errors, errors
    return binding


def _green_book_ir(band=(0.6, 1.3), total=2000):
    body = _green_design()
    binding = _green_binding(body)
    plan, errors = compile_execution_plan(body, binding)
    assert not errors, errors
    chars = _characters()
    writer_ir = compile_writer_book_ir(graph=body, plan=plan, binding=binding,
                                       total_budget=total, length_band=band,
                                       book_title='报恩',
                                       projection=chars.name_projection(),
                                       structure=chars.name_structure)
    return body, binding, plan, writer_ir


def _two_obligation_binding(body, ob1_beat, ob2_beat):
    """ob01 尝试（验收拍可晚）＋ob02 揭示确认 requires ob01 的绑定夹具。"""
    from contracts.schema.grounding import (
        PreserveCheckpoint, SourceLineage, SourceObligation,
    )
    from contracts.schema.policy import PreserveKind
    anchor = anchor_stub()
    ob1 = SourceObligation(
        obligation_id='ob01', kind=PreserveKind.EVENT_CORE,
        statement='夫君假意走开实则藏身床底；尝试以头套始、以推门出终',
        anchors=anchor, lineage=SourceLineage(blueprint_refs=('b01_01',),
                                              character_ids=('fu_jun',)),
        must_preserve=True, satisfaction='正文完整呈现该次尝试')
    ob2 = SourceObligation(
        obligation_id='ob02', kind=PreserveKind.EVENT_CORE,
        statement='芸娘（与读者）确认：夫君并未走开，一直藏在床底',
        anchors=anchor, lineage=SourceLineage(blueprint_refs=('b01_01',),
                                              character_ids=('yun_niang',)),
        must_preserve=True, satisfaction='正文在该揭示点呈现确认', requires=('ob01',))
    cp = PreserveCheckpoint(
        checkpoint_id='cp', policy_id='p', policy_version=1, characters_sha256=H64,
        blueprints_sha256=H64, provider='x', model='y', request_sha256='r' * 64,
        obligations=(ob1, ob2), checkpoint_sha256=H64)
    sites = _site_index(body)
    placements = {'ob01': [sites[f'{ob1_beat[0]}#{ob1_beat[1]}']],
                  'ob02': [sites[f'{ob2_beat[0]}#{ob2_beat[1]}']]}
    binding, errors = derive_semantic_binding(placements, body, cp)
    assert binding is not None and not errors, errors
    return binding


def test_same_scene_requires_interleave_is_legal_cross_scene_inversion_is_not():
    """顺序三分（PLAN §二.3，R10c c1 实证）：requires 是因果前提语义，不机械
    映射到场内阅读序——揭示确认拍可早于复合尝试义务的场末验收拍（床底/衣柜/
    房梁三对揭示曾因此被误拦）；跨场逆序（确认场先于尝试场）仍是结构剧透硬闸。
    """
    body = _green_design()
    beats = {s.scene_id: [b.beat_id for b in s.beats] for s in body.scenes}
    sc1_first, sc1_last = beats['sc01'][0], beats['sc01'][-1]
    sc2_first = beats['sc02'][0]
    # 场内交错：ob01 验收在场末拍、ob02 揭示在早拍 → 合法（修复前此处误报）。
    binding = _two_obligation_binding(body, ('sc01', sc1_last), ('sc01', sc1_first))
    plan, errors = compile_execution_plan(body, binding)
    assert not errors, errors
    assert plan is not None
    # 跨场顺承：尝试场早、确认场晚 → 合法。
    binding_ok = _two_obligation_binding(body, ('sc01', sc1_last), ('sc02', sc2_first))
    plan_ok, errors_ok = compile_execution_plan(body, binding_ok)
    assert not errors_ok, errors_ok
    assert plan_ok is not None
    # 跨场逆序：确认场早于尝试场 → 硬闸拒绝。
    binding_bad = _two_obligation_binding(body, ('sc02', beats['sc02'][-1]),
                                          ('sc01', sc1_first))
    plan_bad, errors_bad = compile_execution_plan(body, binding_bad)
    assert plan_bad is None
    assert any('必须不晚于' in e and '跨场' in e for e in errors_bad), errors_bad


def _series_binding(body, placements, *, optional_instance=False):
    """两个离散实例与一次显式 covers；沿真实 Binding compiler 派生接受拍。"""
    from contracts.schema.grounding import (
        ObligationSeries, PreserveCheckpoint, SeriesSummary, SourceLineage,
        SourceObligation,
    )
    from contracts.schema.policy import PreserveKind
    obligations = tuple(SourceObligation(
        obligation_id=f'ob{index:02d}', kind=PreserveKind.EVENT_CORE,
        statement=statement, anchors=anchor_stub(),
        lineage=SourceLineage(blueprint_refs=('b01_01',), character_ids=('yun_niang',)),
        must_preserve=not (optional_instance and index == 2), satisfaction=statement)
        for index, statement in enumerate(('婚礼实例', '旧约实例', '回顾两次经历'), 1))
    checkpoint = PreserveCheckpoint(
        checkpoint_id='cp', policy_id='p', policy_version=1, characters_sha256=H64,
        blueprints_sha256=H64, provider='x', model='y', request_sha256='r' * 64,
        obligations=obligations, checkpoint_sha256=H64,
        series=(ObligationSeries(
            members=('ob01', 'ob02', 'ob03'),
            summaries=(SeriesSummary(member='ob03', covers=('ob01', 'ob02')),)),))
    binding, errors = derive_semantic_binding(placements, body, checkpoint)
    assert binding is not None and not errors, errors
    return binding


def _series_ir(body, binding):
    plan, errors = compile_execution_plan(body, binding)
    assert plan is not None and not errors, errors
    chars = _characters()
    return compile_writer_book_ir(
        graph=body, plan=plan, binding=binding, total_budget=2000,
        length_band=(0.6, 1.3), projection=chars.name_projection(),
        structure=chars.name_structure)


def test_summary_packet_carries_target_instances_and_only_summary_gets_the_recall():
    """章包闭环：普通实例不新增任务，概括章得到目标已写实例，禁止编补前史。"""
    from novel.stages.common import build_writer_packet
    body = _green_design()
    binding = _series_binding(body, {
        'ob01': [(0, 0)], 'ob02': [(0, 1)], 'ob03': [(1, 0)]})
    ir = _series_ir(body, binding)
    assert ir.chapter(1).scenes[0].verification_ir.prior_series_events == ()
    events = ir.chapter(2).scenes[0].verification_ir.prior_series_events
    assert [(e.summary_beat_ref, e.beat_ref, e.chapter_ordinal, e.event) for e in events] == [
        ('sc02#b01', 'sc01#b01', 1, body.scenes[0].beats[0].event),
        ('sc02#b01', 'sc01#b02', 1, body.scenes[0].beats[1].event)]
    first = build_writer_packet(project_chapter_packet(ir, 1))
    summary = build_writer_packet(project_chapter_packet(ir, 2))
    assert not any('概括系列' in line for line in first.must_respect)
    recall = [line for line in summary.must_respect if '概括系列' in line]
    assert len(recall) == 2
    assert all('第1章' in line and '不另编人物或前史' in line for line in recall)
    assert body.scenes[0].beats[0].event in recall[0]
    assert body.scenes[0].beats[1].event in recall[1]
    prompt = render_chapter_packet(project_chapter_packet(ir, 2))
    assert 'sc01#b01' not in prompt and 'sc02#b01' not in prompt


def test_series_compile_rejects_early_summary_but_accepts_same_beat_and_reordered_instances():
    """只查显式 covers：概括不能早回顾；同拍概括与实例间自由重排均合法。"""
    body = _green_design()
    early = _series_binding(body, {
        'ob01': [(0, 0)], 'ob02': [(1, 0)], 'ob03': [(0, 1)]})
    plan, errors = compile_execution_plan(body, early)
    assert plan is None
    assert [(e.code, e.context['instance']) for e in errors
            if getattr(e, 'code', '') == 'series_summary_early'] == [
                ('series_summary_early', 'ob02')]
    # covers 列表不是目标阅读序：两个实例在目标故事中交换顺序仍合法。
    reordered = _series_binding(body, {
        'ob01': [(0, 1)], 'ob02': [(0, 0)], 'ob03': [(1, 0)]})
    ir = _series_ir(body, reordered)
    assert [e.beat_ref for e in ir.chapter(2).scenes[0].verification_ir.prior_series_events] == [
        'sc01#b01', 'sc01#b02']
    same = _series_binding(body, {
        'ob01': [(0, 0)], 'ob02': [(0, 1)], 'ob03': [(0, 1)]})
    same_ir = _series_ir(body, same)
    events = same_ir.chapter(1).scenes[0].verification_ir.prior_series_events
    assert any(e.beat_ref == e.summary_beat_ref for e in events)
    assert '与本拍一起完成' in render_chapter_packet(project_chapter_packet(same_ir, 1))
    assert same_ir.chapter(2).scenes[0].verification_ir.prior_series_events == ()


def test_series_compile_rejects_selected_summary_without_covered_optional_instance():
    """compile 免费重验闭合显式 covers，而非只看 must_preserve 分母。"""
    body = _green_design()
    binding = _series_binding(body, {
        'ob01': [(0, 0)], 'ob03': [(1, 0)]}, optional_instance=True)
    plan, errors = compile_execution_plan(body, binding)
    assert plan is None
    assert [(e.code, e.context['instance']) for e in errors] == [
        ('series_summary_unbound', 'ob02')]


def test_free_compile_rejects_repeated_canonical_detail_refs_without_deduplicating():
    """正式当前schema可构造坏稿，免费compile须typed拒绝，不能重复送导演提示。"""
    from contracts.schema.grounding import SourceDetail
    body = _green_design()
    binding = _series_binding(body, {
        'ob01': [(0, 0)], 'ob02': [(0, 1)], 'ob03': [(1, 0)]})
    first = binding.obligations[0]
    detail = SourceDetail(detail_id='dt_' + '1' * 16, anchor=first.anchors[0],
                          text='临窗摆着一盏茶')
    binding = type(binding).model_validate(binding.model_copy(update={
        'obligations': (first.model_copy(update={'details': (detail,)}),
                        *binding.obligations[1:])}).model_dump(mode='json'))
    scene = body.scenes[0]
    beat = scene.beats[0].model_copy(update={'detail_refs': (detail.detail_id, detail.detail_id)})
    repeated = type(body).model_validate(body.model_copy(update={
        'scenes': (scene.model_copy(update={'beats': (beat, *scene.beats[1:])}),
                   *body.scenes[1:])}).model_dump(mode='json'))
    plan, errors = compile_execution_plan(repeated, binding)
    assert plan is None
    assert [(e.code, e.owner, e.location) for e in errors] == [
        ('design_detail_ref', 'compile', ('detail_ref', 'sc01', 'b01', detail.detail_id))]
    assert '重复引用' in errors[0]
    assert repeated.scenes[0].beats[0].detail_refs == (detail.detail_id, detail.detail_id)


def test_series_recall_changes_summary_packet_and_leaves_unrelated_future_packet_unchanged():
    """前情事件是显式消费：改变实例换实例与概括章身份，不波及其它章。"""
    body = _green_design()
    binding = _series_binding(body, {
        'ob01': [(0, 0)], 'ob02': [(0, 1)], 'ob03': [(0, 2)]})
    original = _series_ir(body, binding)
    scene = body.scenes[0]
    changed = scene.beats[0].model_copy(update={'event': '婚礼临窗举行'})
    updated_scene = scene.model_copy(update={'beats': (changed, *scene.beats[1:])})
    updated_body = body.model_copy(update={'scenes': (updated_scene, *body.scenes[1:])})
    updated = _series_ir(updated_body, binding)
    assert chapter_identities(project_chapter_packet(original, 1)).writer_view_sha256 != \
        chapter_identities(project_chapter_packet(updated, 1)).writer_view_sha256
    assert chapter_identities(project_chapter_packet(original, 2)).writer_view_sha256 == \
        chapter_identities(project_chapter_packet(updated, 2)).writer_view_sha256


# ───────────────────────── 章预算确定性分配 ─────────────────────────

def test_chapter_budgets_sum_to_total_and_are_deterministic():
    body = _green_design()
    budgets = allocate_chapter_budgets(body, 1001)
    assert sum(budgets.values()) == 1001
    assert budgets == {1: 501, 2: 500}  # 0.5/0.5 权重 + 最大余数给低章号
    assert allocate_chapter_budgets(body, 1001) == budgets
    # 权重变化 → 预算变化（预算是 IR 业务内容）
    weighted = body.model_copy(update={'chapter_weights': {'sc01': 0.8, 'sc02': 0.2}})
    assert allocate_chapter_budgets(weighted, 1000)[2] == 200


# ───────────────────────── 双视图 IR 与 canonical 同源 ─────────────────────────

def test_writer_book_ir_projects_canonically():
    _, _, _, writer_ir = _green_book_ir()
    body = _green_design()
    chars = _characters()
    assert [c.chapter_ordinal for c in writer_ir.chapters] == [1, 2]
    assert writer_ir.book_brief.target_chapter_count == 2
    for chapter in writer_ir.chapters:
        assignment = next(a for a in body.chapters if a.chapter_ordinal == chapter.chapter_ordinal)
        assert chapter.purpose == assignment.purpose  # 章目的原样投影
        for context in chapter.scenes:
            scene = next(s for s in body.scenes if s.scene_id == context.scene_id)
            assert context.writer_brief.realization_brief == scene.realization_brief
            assert [b.event for b in context.verification_ir.beats] == \
                [b.event for b in scene.beats]  # 逐拍事实定位
            assert [d.value for d in context.verification_ir.planned_delta] == \
                [d.value for b in scene.beats for d in b.target_delta]
        # 章级验证材料 compile 时算好（D2）：称谓许可＋禁用字面
        ordinal = chapter.chapter_ordinal
        assert chapter.released_surfaces == tuple(sorted(
            chapter_surface_releases(body, ordinal, chars.name_structure)))
        assert chapter.withheld_literals == chapter_withheld_literals(
            chars.name_projection(), body, ordinal, chars.name_structure)
    # 称谓释放导航（审计面）：initial 即第 1 章；芸娘在 sc01#b02（第 1 章）释放
    schedule = {e.char_id: e for e in writer_ir.book_brief.release_schedule}
    assert schedule['yun_niang'].from_chapter == 1
    assert '芸娘' in schedule['yun_niang'].surfaces


def test_packet_materials_must_be_compiled_not_derived_late():
    """验证材料缺失（compile 未带名称数据）关闭式失败——不静默放行专名泄漏。"""
    body, binding, plan, writer_ir = _green_book_ir()
    chars = _characters()
    naked = compile_writer_book_ir(graph=body, plan=plan, binding=binding,
                                   total_budget=2000, length_band=(0.6, 1.3),
                                   projection=chars.name_projection(),
                                   structure=None)
    assert naked.chapter(1).withheld_literals  # 名称投影已够推导（结构仅补家族构词）
    stripped = writer_ir.model_copy(update={'chapters': tuple(
        c.model_copy(update={'withheld_literals': (), 'released_surfaces': ()})
        for c in writer_ir.chapters)})
    with pytest.raises(ValueError, match='验证材料缺失'):
        project_chapter_packet(stripped, 1)


# ───────────────────────── 章包 renderer：D2 反例、可追溯、身份 ─────────────────────────

def test_renderer_only_contains_the_current_packet():
    """D2 反例：Writer 请求不含前文正文、其他章 IR、导航与称谓总表。"""
    _, _, _, writer_ir = _green_book_ir()
    prior_ch1 = '第一章的正文。'
    packet2 = project_chapter_packet(writer_ir, 2)
    body2 = render_chapter_packet(packet2)
    # 章包层结构（v5 分区渲染）：协议→六分区→交付要求
    for section in ('【整书写作约定与自由度】', '【必须写出】', '【不可违背】',
                    '【承接】', '【导演说明】', '【背景】', '【禁区】',
                    '【本章交付要求】'):
        assert section in body2
    # 前文正文与其他章 IR 不进请求
    assert prior_ch1 not in body2
    assert '【完整已提交前文】' not in body2 and '〔第1章〕' not in body2
    assert '【全书规划参考' not in body2 and '规划参考' not in body2
    assert '洞房夜夫君立约' not in body2, '第 1 章的场景 IR 不得进第 2 章请求'
    # 称谓总表（释放位置导航）不进请求
    assert '称谓释放位置' not in body2 and '释放位置（未到位置不得使用对应字面）' not in body2
    # 本章材料可追溯：本章场景事件、知情措辞与逐拍结果都在（自然语言条目）
    assert '玉佩之谜浮出水面' in body2 and '夫君夜半离家' in body2
    assert '确知' in body2 and '玉佩刻着沈字' in body2
    # source lineage（b01_01）与内部 ID 不进 Writer 阅读面
    assert 'b01_01' not in body2 and 'blueprint_refs' not in body2
    assert 'sc01' not in body2 and 'sc02' not in body2
    assert 'yun_niang' not in body2 and 'fu_jun' not in body2
    # 同章包 ⇒ 同字节
    assert body2 == render_chapter_packet(project_chapter_packet(writer_ir, 2))
    # 第 1 章的章包不含第 2 章材料；场内称谓释放位置以自然拍序表述
    body1 = render_chapter_packet(project_chapter_packet(writer_ir, 1))
    assert '夫君夜半离家' not in body1 and prior_ch1 not in body1
    assert '第 2 拍起才可使用：芸娘' in body1


def test_packet_changes_only_when_its_own_inputs_change():
    """精确失效表：某章导演说明变化只换该章章包；无关章的章包与请求不变。"""
    _, _, _, writer_ir = _green_book_ir()
    chars = _characters()

    def compile_with(changed_scene: int, brief: str):
        body = _green_design()
        scene = body.scenes[changed_scene].model_copy(update={'realization_brief': brief})
        scenes = list(body.scenes)
        scenes[changed_scene] = scene
        body = body.model_copy(update={'scenes': tuple(scenes)})
        binding = _green_binding(body)
        plan, errors = compile_execution_plan(body, binding)
        assert not errors, errors
        return compile_writer_book_ir(graph=body, plan=plan, binding=binding,
                                      total_budget=2000, length_band=(0.6, 1.3),
                                      book_title='报恩',
                                      projection=chars.name_projection(),
                                      structure=chars.name_structure)

    sha_old = {c: packet_sha256(project_chapter_packet(writer_ir, c)) for c in (1, 2)}
    ir_ch1_changed = compile_with(0, '重心改为婚礼内外的注视。')
    sha_new = {c: packet_sha256(project_chapter_packet(ir_ch1_changed, c)) for c in (1, 2)}
    assert sha_new[1] != sha_old[1], '第 1 章导演说明变化 ⇒ 第 1 章章包变化'
    assert sha_new[2] == sha_old[2], '无关章（第 2 章）的章包与请求不变'
    ir_ch2_changed = compile_with(1, '重心改为追踪线。')
    sha_new = {c: packet_sha256(project_chapter_packet(ir_ch2_changed, c)) for c in (1, 2)}
    assert sha_new[2] != sha_old[2], '第 2 章导演说明变化 ⇒ 第 2 章章包变化'
    assert sha_new[1] == sha_old[1], '无关章（第 1 章）的章包与请求不变'


# ───────────────────────── 机械检查：全分支 ─────────────────────────

def _withheld(chapter=1):
    chars = _characters()
    body = _green_design()
    return chapter_withheld_literals(
        chars.name_projection(), body, chapter, chars.name_structure)


def test_withheld_literals_follow_release_schedule():
    chars = _characters()
    body = _green_design()
    released1 = chapter_surface_releases(body, 1)
    assert {'苏芸娘', '夫君', '芸娘'} <= released1
    withheld1 = _withheld(1)
    # 未释放：陆家姓字面与构词、苏姑娘；已释放的 芸娘/苏芸娘/夫君 不在禁用面
    assert '陆大哥' in withheld1 and '陆兄' in withheld1 and '陆' in withheld1
    assert '苏姑娘' in withheld1
    assert '芸娘' not in withheld1 and '夫君' not in withheld1
    # R9：成员已合法释放，其登记家族构词随之可用；其它个人称谓继续禁用。
    assert '陆家' not in withheld1


def test_validate_chapter_prose_all_branches():
    withheld = _withheld(1)
    released = tuple(sorted(chapter_surface_releases(_green_design(), 1)))
    kw = dict(withheld_literals=withheld, released_surfaces=released,
              target_chars=100, band_low=10, band_high=130,
              scene_markers=('s01', 's02'))
    assert validate_chapter_prose('夫君夜半离家，芸娘追至门前，心中起疑。', **kw) == []
    assert validate_chapter_prose('   ', **kw) == ['[non_empty] 章正文为空']
    assert any(e.startswith('[encoding]') for e in validate_chapter_prose(
        '正文\ufffd带坏字符。', **kw))
    assert any('no_wrapper' in e for e in validate_chapter_prose(
        '```正文```', **kw))
    assert any('no_wrapper' in e for e in validate_chapter_prose(
        '第一章 开端\n正文', **kw))
    assert any('no_wrapper' in e for e in validate_chapter_prose(
        '苏芸娘走了。s01 场景结束。', **kw))
    assert any('withheld_literals' in e for e in validate_chapter_prose(
        '夫君走了，芸娘追到陆家大门。', **kw))
    # 已释放长名认领后不误报其构成字面
    assert validate_chapter_prose('苏芸娘与夫君对坐无言，烛火摇了三摇。', **kw) == []
    assert any('length_band' in e for e in validate_chapter_prose(
        '字' * 200, **kw))


# ───────────────────────── 章包 Writer：重写与候选选择 ─────────────────────────

class _FakeAnswers:
    def __init__(self, answers):
        self.answers = list(answers)
        self.prompts = []
        self.validators = []
        self.systems = []

    def __call__(self, prompt, validate, system=None):
        # 付费边界据 validate 把接受结果如实记入账（RC-54）；它必须是本章真实的机械校验
        # （段落修句轮例外：回答是行替换 JSON，校验器先合并再验）。
        self.prompts.append(prompt)
        self.validators.append(validate)
        self.systems.append(system)
        return self.answers.pop(0)


def _packet_kw(band=(0.6, 1.3), total=200, ordinal=1):
    _, _, _, writer_ir = _green_book_ir(band=band, total=total)
    return dict(packet=project_chapter_packet(writer_ir, ordinal))


def _answered(text, finish_reason=None, identity='id'):
    return AnsweredText(text=text, finish_reason=finish_reason, identity=identity,
                        cached=False)


def test_write_chapter_first_pass_and_rewrite_identity():
    kw = _packet_kw()
    # 章 1 预算 100、带 [60,130]：稿长需落在带内
    good = ('洞房之夜，苏芸娘望着夫君，把旧约问了个明白。' * 3)
    fake = _FakeAnswers([_answered(good)])
    outcome = write_chapter(**kw, ask=fake)
    assert outcome.prose == good and outcome.receipt.status == 'accepted'
    assert not outcome.receipt.length_disclosed
    assert outcome.receipt.writer_view_sha256 == chapter_identities(
        kw['packet']).writer_view_sha256
    assert len(outcome.attempts) == 1
    # 重写：同章包 + 上一稿 + typed 错误进 prompt
    bad = '字' * 500
    fake = _FakeAnswers([_answered(bad), _answered(good, identity='id-2')])
    outcome = write_chapter(**kw, ask=fake)
    assert outcome.prose == good and outcome.request_identity == 'id-2'
    assert len(outcome.attempts) == 2
    assert '[length_band]' in '；'.join(outcome.attempts[0].errors)
    assert '上一稿' in fake.prompts[1] and '原因' in fake.prompts[1]
    # 交给付费边界的就是控制流用的那把机械校验：同一稿得同一结论（RC-54）
    assert fake.validators[0](bad) == list(outcome.attempts[0].errors)
    assert fake.validators[1](good) == []
    # 重写请求仍是同一章包正文 + 重写要求（不含前文/他章材料）
    assert fake.prompts[1].startswith(fake.prompts[0].rstrip('\n'))


def _withheld_patch_packet():
    """只含本测试专名许可的章包；不改变篇幅判据来接受实际违规稿。"""
    packet = _packet_kw()['packet']
    return packet.model_copy(update={'chapter': packet.chapter.model_copy(update={
        'withheld_literals': ('未名',), 'released_surfaces': ('夫君',),
        'requires_released': (),
    })})


def _withheld_patch_draft():
    return '\n'.join((
        '远处没有进请求的开场。', '近旁烛火烧得很稳。', '她把旧约问得清楚。',
        '未名把回答低低说完。未名又掩上门。', '她没有追问。',
        '屋里只剩风声。', '远处没有进请求的收束。'))


def test_withheld_diagnostic_binds_original_offsets_and_locates_each_affected_line():
    from novel.core.validation import ValidationIssue
    from novel.stages.stage5.body import _withheld_repair_lines
    prose = ' \n\n未名低声答。\n夫君不多说。\n未名关上门。\n '
    errors = validate_chapter_prose(
        prose, withheld_literals=('未名',), released_surfaces=('夫君',),
        target_chars=20, band_low=0, band_high=100)
    assert len(errors) == 2 and all(isinstance(e, ValidationIssue) for e in errors)
    assert [e.context for e in errors] == [
        {'literal': '未名', 'start': prose.index('未名'), 'end': prose.index('未名') + 2},
        {'literal': '未名', 'start': prose.rindex('未名'), 'end': prose.rindex('未名') + 2},
    ]
    assert _withheld_repair_lines(prose, errors) == (2, 4)


def test_pure_withheld_literal_repairs_only_the_affected_paragraph_and_uses_full_base_identity():
    import json
    from novel.stages.common import BODY_PARAGRAPH_REPAIR_SYSTEM
    packet, draft = _withheld_patch_packet(), _withheld_patch_draft()
    fixed = draft.split('\n')[3].replace('未名', '夫君')
    patch = json.dumps({'paragraphs': [{'index': 3, 'text': fixed}]}, ensure_ascii=False)
    fake = _FakeAnswers([_answered(draft), _answered(patch, identity='patch-id')])
    outcome = write_chapter(packet=packet, ask=fake)
    assert outcome.prose == draft.replace('未名', '夫君')
    assert outcome.request_identity == 'patch-id' and len(outcome.attempts) == 2
    prompt = fake.prompts[1]
    assert fake.systems[1] == BODY_PARAGRAPH_REPAIR_SYSTEM
    assert '允许替换的行号：[3]' in prompt and '3（可编辑）' in prompt
    assert '2（只读邻段）' in prompt and '4（只读邻段）' in prompt
    assert '远处没有进请求' not in prompt and '本章交付要求' not in prompt
    assert '本章合法已释放称谓：["夫君"]' in prompt
    assert sha256_text(draft) in prompt
    assert fake.validators[1](patch) == [], '付费接受器检查合并后的完整稿'

    # 切片外只改一句，窄输入正文仍相同，但完整基底变化必须换请求身份。
    other = draft.replace('远处没有进请求的开场。', '另一处没有进请求的开场。')
    second = _FakeAnswers([_answered(other), _answered(patch)])
    write_chapter(packet=packet, ask=second)
    assert other.split('\n')[0] not in second.prompts[1]
    assert sha256_text(other) in second.prompts[1] and second.prompts[1] != prompt


@pytest.mark.parametrize('bad_patch', [
    '{"paragraphs":[{"index":2,"text":"改动只读邻段。"}]}',
    '{"paragraphs":[{"index":99,"text":"越界。"}]}',
    '{"paragraphs":[{"index":3,"text":"甲"},{"index":3,"text":"乙"}]}',
    '{"paragraphs":[{"index":true,"text":"甲"}]}',
    '{"paragraphs":[{"index":3,"text":"甲\\n新增段落"}]}',
    '{"paragraphs":[{"index":3,"text":"甲","extra":1}]}',
    '{"paragraphs":[{"index":3,"index":2,"text":"甲"}]}',
    '正文整稿，不能当作段落补丁。',
])
def test_bad_withheld_patch_keeps_the_same_narrow_arm_and_does_not_edit_other_lines(bad_patch):
    import json
    from novel.stages.common import BODY_PARAGRAPH_REPAIR_SYSTEM
    draft = _withheld_patch_draft()
    fixed = draft.split('\n')[3].replace('未名', '夫君')
    good_patch = json.dumps({'paragraphs': [{'index': 3, 'text': fixed}]}, ensure_ascii=False)
    fake = _FakeAnswers([_answered(draft), _answered(bad_patch), _answered(good_patch)])
    outcome = write_chapter(packet=_withheld_patch_packet(), ask=fake)
    assert outcome.prose == draft.replace('未名', '夫君')
    assert len(outcome.attempts) == 3 and outcome.attempts[1].errors[0].startswith('[paragraph_patch]')
    assert fake.validators[1](bad_patch)[0].startswith('[paragraph_patch]')
    assert fake.systems[1:] == [BODY_PARAGRAPH_REPAIR_SYSTEM] * 2
    assert all('允许替换的行号：[3]' in prompt and sha256_text(draft) in prompt
               and '远处没有进请求' not in prompt for prompt in fake.prompts[1:])
    assert fake.prompts[1] != fake.prompts[2], '补丁格式失败进入下一轮请求身份'


def test_mixed_withheld_and_length_failure_keeps_the_existing_whole_chapter_rewrite():
    draft = _withheld_patch_draft() * 3
    good = _withheld_patch_draft().replace('未名', '夫君')
    fake = _FakeAnswers([_answered(draft), _answered(good)])
    outcome = write_chapter(packet=_withheld_patch_packet(), ask=fake)
    assert outcome.prose == good
    assert any(e.startswith('[length_band]') for e in outcome.attempts[0].errors)
    assert any(e.startswith('[withheld_literals]') for e in outcome.attempts[0].errors)
    assert fake.systems[1] is None and '【重写要求】' in fake.prompts[1]
    assert draft in fake.prompts[1]


def test_write_chapter_selects_min_deviation_late_tie_and_blocks():
    # 硬失败不能成为候选：全部 attempts 带专名泄漏 → 无合法候选
    kw = _packet_kw()
    leak = '夫君与芸娘说到陆大哥的旧约，字字清楚。'
    fake = _FakeAnswers([_answered(leak)] * 3)
    outcome = write_chapter(**kw, ask=fake)
    assert outcome.prose is None and outcome.receipt is None
    # 额度耗尽：偏差最小者当选；同偏差取较晚（带 [3,4]，三稿都带外但硬检查全过）
    shorter = '苏芸娘问。'            # 5 字
    mid = '苏芸娘问旧约，夫君答。'     # 11 字（相对目标 100 偏差最小）
    kw = _packet_kw(band=(0.03, 0.04))
    fake = _FakeAnswers([_answered(shorter, identity='a'), _answered(mid, identity='b'),
                         _answered(mid, identity='c')])
    outcome = write_chapter(**kw, ask=fake)
    assert outcome.prose == mid and outcome.length_disclosed
    assert outcome.request_identity == 'c'  # 平局取较晚
    assert outcome.receipt.length_disclosed and not outcome.receipt.length.within_band
    # 平局取较晚：三稿同字数（都带外，额度耗尽后选择）
    same1 = '苏芸娘问约！'
    same2 = '苏芸娘问约？'
    same3 = '苏芸娘问约～'
    fake = _FakeAnswers([_answered(same1, identity='a'), _answered(same2, identity='b'),
                         _answered(same3, identity='c')])
    outcome = write_chapter(**kw, ask=fake)
    assert outcome.prose == same3
    # 截断即停（D6）：不重写、不进候选，该章无合法候选即停止
    fake = _FakeAnswers([_answered('苏芸娘问旧约。', finish_reason='length'),
                         _answered('备用稿不应被问到', identity='never')])
    outcome = write_chapter(**kw, ask=fake)
    assert outcome.prose is None
    assert len(outcome.attempts) == 1, '截断即停在该 item，不消耗重写额度（D6）'
    assert outcome.attempts[0].errors[-1].startswith('[truncated]')
    assert len(fake.prompts) == 1, '截断后不得再发起重写请求'


# ───────────────────────── 回执集合核对 ─────────────────────────

def test_verify_chapter_receipts_checks_each_chapter_against_its_packet():
    import hashlib
    def sha(t):
        return hashlib.sha256(t.encode('utf-8')).hexdigest()
    sha1, sha2, vsha = '1' * 64, '2' * 64, '9' * 64
    prose1, prose2 = '第一章', '第二章'

    def identities(writer_view, validation=vsha):
        from novel.stages.common import ChapterIdentities
        return ChapterIdentities(writer_view_sha256=writer_view,
                                 validation_sha256=validation,
                                 machine_view_sha256='e' * 64)

    r1 = {'status': 'accepted', 'writer_view_sha256': sha1,
          'validation_sha256': vsha, 'machine_view_sha256': 'e' * 64,
          'prose_sha256': sha(prose1)}
    r2 = {'status': 'accepted', 'writer_view_sha256': sha2,
          'validation_sha256': vsha, 'machine_view_sha256': 'e' * 64,
          'prose_sha256': sha(prose2)}
    from contracts.schema.receipt import BODY_VALIDATOR_VERSION, BODY_MECHANICAL_CHECKS
    for receipt in (r1, r2):
        receipt.update(validator_version=BODY_VALIDATOR_VERSION, mechanical_checks=BODY_MECHANICAL_CHECKS)
    ids = {1: identities(sha1), 2: identities(sha2)}
    assert verify_chapter_receipts({1: r1, 2: r2}, {1: prose1, 2: prose2},
                                  identities_by_ordinal=ids) == []
    # 某章写手视图重编译 → 仅该章失效，其他章独立有效（不再有前缀断裂）
    r2_stale = dict(r2, writer_view_sha256='f' * 64)
    problems = verify_chapter_receipts({1: r1, 2: r2_stale}, {1: prose1, 2: prose2},
                                       identities_by_ordinal=ids)
    assert len(problems) == 1 and '第2章' in problems[0]
    # 验证输入变化（禁用字面/篇幅带）→ 同样失效（rc6 身份拆分：分别绑定）
    r2_validation = dict(r2, validation_sha256='7' * 64)
    problems = verify_chapter_receipts({1: r1, 2: r2_validation}, {1: prose1, 2: prose2},
                                       identities_by_ordinal=ids)
    assert len(problems) == 1 and '验证输入' in problems[0]
    # Machine 层摘要不参与核对（仅审计）
    r2_machine = dict(r2, machine_view_sha256='6' * 64)
    assert verify_chapter_receipts({1: r1, 2: r2_machine}, {1: prose1, 2: prose2},
                                   identities_by_ordinal=ids) == []
    # 文本被改 → 该章脱钩
    problems = verify_chapter_receipts({1: r1}, {1: '被改过的第一章'},
                                       identities_by_ordinal=ids)
    assert problems and '第1章' in problems[0]
    # 拒绝回执显式报出
    problems = verify_chapter_receipts({1: dict(r1, status='rejected')}, {1: prose1},
                                       identities_by_ordinal=ids)
    assert problems and 'rejected' in problems[0]


# ───────────────────────── 全链集成：fake transport → 交付 → 恢复 ─────────────────────────

GOOD_CH1 = '拜堂之夜，苏芸娘望着夫君，旧约出口，烛火到天亮才熄，她记住了每一个字。'
GOOD_CH2 = '夫君夜半离家，芸娘在他枕下发现玉佩，字迹陌生，她把玉佩藏回原处，坐到天明。'


class _RoutingTransport:
    """按 owner 路由的假 transport：JSON owner 走 wires，文本 owner 出正文。

    `truncate` 指定的章 owner 返回 finish_reason=length（D6 截断即停反例）；
    `hook_text` 可替换 Hook 成文（Hook 失败反例用，默认原文）。
    """

    def __init__(self, wires, truncate=(), hook_text='苏芸娘望着空房。'):
        self.wires = wires
        self.truncate = tuple(truncate)
        self.hook_text = hook_text
        self.calls: list[str] = []

    def send(self, request: ChatRequest) -> ChatResponse:
        owner = self._owner(request)
        self.calls.append(owner)
        if owner == 'hook_writer':
            return ChatResponse(content=self.hook_text, finish_reason='stop',
                                usage={'prompt_tokens': 10, 'completion_tokens': 5},
                                model='fake')
        if owner.startswith('body_ch'):
            if owner in self.truncate:
                return ChatResponse(content='截断的开头。', finish_reason='length',
                                    usage={'prompt_tokens': 100, 'completion_tokens': 40000},
                                    model='fake')
            text = GOOD_CH1 if owner.endswith('ch01') else GOOD_CH2
            return ChatResponse(content=text, finish_reason='stop',
                                usage={'prompt_tokens': 100, 'completion_tokens': 40},
                                model='fake')
        import json as _json
        return ChatResponse(content=_json.dumps(self.wires[owner], ensure_ascii=False),
                            finish_reason='stop',
                            usage={'prompt_tokens': 10, 'completion_tokens': 10},
                            model='fake')

    @staticmethod
    def _owner(request: ChatRequest) -> str:
        # owner 由工具层放进 system？不——PaidRequestSession 以请求身份缓存，
        # owner 只影响路由。这里按内容嗅探（测试专用）。
        user = request.user
        if '楔子' in request.system:
            return 'hook_writer'
        if '【本章交付要求】' in user:
            # v5 分区渲染：章目的在「导演说明」分区（「本章目的（第N章）：…」）
            return 'body_ch01' if '本章目的（第1章）' in user else 'body_ch02'
        if '人物账、初始状态、世界约束与书级命题词表' in user:
            return 'book'
        if '条目级关联判定' in user:
            return 'associate'
        if '跨章因果判定' in user:
            return 'cross_cause'
        if '拆成有序 beats' in user:
            # 章正文标记（【第 N 章正文】）的最后一次出现即当前章：前文原文块用
            # 〔第N章〕方头括号，不会与章正文标记混淆。
            marks = re.findall(r'【第 (\d+) 章正文】', user)
            return f'ch{int(marks[-1]):02d}' if marks else 'ch01'
        for owner, marker in (
                ('names', '登记全书源名称结构'), ('preserve', '最小语义义务'),
                ('design', '设计目标书'),
                ('hook_design', 'stage4-hook-design-v3'), ('naming', '表层姓名')):
            if marker in user:
                return owner
        raise AssertionError(f'无法路由的请求：{user[:80]}')


class _ReorderedTransport(_RoutingTransport):
    """第 2 章提交先落盘、第 1 章才拿到响应的假 transport（乱序完成场景）。

    等待发生在 transport 内（付费会话不跨 transport.send 持锁），第 2 章的
    请求、提交不依赖第 1 章（D3/D2），因此不会死锁；60 秒保底把「乱序前提
    未成立」变成显式失败而不是挂起。
    """

    def __init__(self, wires, wait_for):
        super().__init__(wires)
        self._wait_for = wait_for

    def send(self, request: ChatRequest) -> ChatResponse:
        if self._owner(request) == 'body_ch01':
            deadline = time.monotonic() + 60.0
            while not self._wait_for.exists():
                if time.monotonic() > deadline:
                    raise AssertionError('乱序前提不成立：第 2 章未先于第 1 章提交')
                time.sleep(0.01)
        return super().send(request)


def _make_session(tmp_path, wires, ceiling=None, truncate=(), name='out', transport=None):
    if transport is None:
        transport = _RoutingTransport(wires, truncate=truncate)
    base = tmp_path / name
    base.mkdir(exist_ok=True)
    repo = Str06Repository(base / 'run')
    ledger = CostLedger(path=ledger_path(base))
    repo.attach_context(make_context(base / 'run', ledger=ledger))
    session = PaidRequestSession(repo, ledger, transport, yes=True,
                                 allow_source_export=True, provider='fake',
                                 book_call_ceiling=ceiling, sid='run')
    return repo, session, transport


def _candidates_base(repo):
    return repo.root.parent


def _fake_ask(session):
    from tests.str06_facilities import json_ask
    return json_ask(session)


def _fake_text(session):
    from tests.str06_facilities import text_ask
    return text_ask(session)


def _fake_body(session):
    from tests.str06_facilities import body_ask
    return body_ask(session)


def _chapters():
    return [
        {'chapter_ordinal': 1, 'text': '苏芸娘嫁入陆家，唤夫君为陆大哥，苏姑娘陪嫁，陆兄迎娶。'},
        {'chapter_ordinal': 2, 'text': '夫君夜半翻墙而出，苏芸娘发现玉佩刻着沈字。'}]


def _ledger_purpose_counts(repo, purpose: str) -> int:
    """ledger 里该 owner（purpose）的行数（缓存重放也留一行零 token 账）。"""
    import json as _json
    path = repo.root.parent / '_service' / 'ledger.jsonl'
    return sum(1 for line in path.read_text(encoding='utf-8').splitlines()
               if line.strip()
               and _json.loads(line).get('purpose') == purpose)


def _ledger_rows(repo, purpose: str) -> list[dict]:
    """ledger 里该 owner（purpose）的全部行（按写入序；身份断言用）。"""
    import json as _json
    path = repo.root.parent / '_service' / 'ledger.jsonl'
    return [_json.loads(line) for line in path.read_text(encoding='utf-8').splitlines()
            if line.strip() and _json.loads(line).get('purpose') == purpose]


def _run(repo, session, *, budget=2000, concurrency=4, node_concurrency=None,
         node_filter=None, body_chapters=None, through='stage7'):
    return run_workflow(repository=repo, chapters=_chapters(),
                        ask=_fake_ask(session), ask_text=_fake_text(session),
                        ask_body=_fake_body(session),
                        budget=budget, through=through, book_title='报恩',
                        length_band=(0.01, 0.05), concurrency=concurrency,
                        node_concurrency=node_concurrency,
                        node_filter=node_filter, body_chapters=body_chapters)


def test_full_pipeline_to_delivery_and_free_recovery(tmp_path):
    wires = _full_chain_wires()
    repo, session, transport = _make_session(tmp_path, wires)
    report = _run(repo, session)
    # 交付就绪：章级回执集合 + 最终 TXT + manifest（文件先落盘、manifest 最后提交）
    assert report['assembly']['status'] == 'ready', report['assembly']
    assert repo.read('runtime/workflow_status.json')['status'] == 'delivered'
    final_rel = 'stage7/final/报恩_rewriting.txt'
    final_text = repo.read_text(final_rel)
    assert '楔子' not in final_text  # 系统不添加标题
    assert '柳婉' in final_text and '苏芸娘' not in final_text  # 改名后交付
    # 章标记由系统按阅读序添加：引子在前不加标题，其后每章一行「第N章」（A4）——
    # 乱序完成也得到相同有序交付。
    markers = [line for line in final_text.splitlines()
               if line.startswith('第') and line.endswith('章')]
    assert markers == ['第1章', '第2章'], markers
    assert final_text.index('第1章') < final_text.index('拜堂之夜')
    assert final_text.index('第2章') < final_text.index('夫君夜半离家')
    manifest = repo.inspect('stage7/final/delivery_manifest.json')
    assert manifest['final_text_sha256'] == repo.file_sha256(repo.path(final_rel))
    assert [c['chapter_ordinal'] for c in manifest['chapters']] == [1, 2]
    assert manifest['length_reading']['whole_book_deviation_ratio'] is not None
    # 章级回执绑定章包摘要与改名前正文（D2/rc5）：不再绑定全书 IR 与前文链
    receipt1 = repo.inspect('stage5/acceptance/body/ch01.json')
    receipt2 = repo.inspect('stage5/acceptance/body/ch02.json')
    assert receipt2['status'] == 'accepted'
    assert receipt2['length_disclosed'] is False
    assert len(receipt2['writer_view_sha256']) == 64
    assert 'book_ir_sha256' not in receipt2 and 'prior_text_sha256' not in receipt2
    # 章包摘要互不相同（各章章包独立），正文摘要绑定改名前文本
    assert receipt1['writer_view_sha256'] != receipt2['writer_view_sha256']
    assert receipt1['prose_sha256'] == sha256_text(GOOD_CH1)
    # 新购账：五节点 9 个 JSON owner（bind 退役，CS-3）+ hook_writer + 2 章 body = 12 次外发
    assert len(transport.calls) == 12
    from novel.work_units import read_book_budget
    assert read_book_budget(_candidates_base(repo), 'run').used == 12, 'admission 平行计数已删，整书总闸是唯一准入账'

    # ── 同根恢复：零新调用、交付字节一致 ──
    calls_before = len(transport.calls)
    final_before = repo.path(final_rel).read_bytes()
    report2 = _run(repo, session)
    assert len(transport.calls) == calls_before
    assert repo.path(final_rel).read_bytes() == final_before
    assert report2['assembly']['status'] == 'ready'

    # ── 崩溃窗口：回执丢失 → 本章免费重做（缓存命中），交付不变 ──
    repo.path('stage5/acceptance/body/ch02.json').unlink()
    report3 = _run(repo, session)
    assert len(transport.calls) == calls_before  # 同请求身份免费重放
    assert repo.path(final_rel).read_bytes() == final_before
    assert report3['assembly']['status'] == 'ready'

    # ── 集合恢复（D2）：第 1 章回执丢失 → 只补第 1 章，第 2 章合法提交保留 ──
    repo.path('stage5/acceptance/body/ch01.json').unlink()
    ch01_rows = _ledger_purpose_counts(repo, 'body_ch01')
    ch02_rows = _ledger_purpose_counts(repo, 'body_ch02')
    report4 = _run(repo, session)
    # 只补缺章：body_ch01 免费重放各多一行缓存账；body_ch02 未被触碰
    assert _ledger_purpose_counts(repo, 'body_ch01') == ch01_rows + 1
    assert _ledger_purpose_counts(repo, 'body_ch02') == ch02_rows
    assert len(transport.calls) == calls_before  # 重放走缓存，零新外发
    assert repo.path(final_rel).read_bytes() == final_before
    assert report4['assembly']['status'] == 'ready'
    assert report4['body_complete'] is True

    # ── IR 变化（导演说明变化经重新设计）→ 章包变化的章重新核验（重购）：
    # 提高预算改变 design 请求与全书 IR（CS-3 起 binding 随 design 派生）；
    # rc24 起 Hook 篇幅包络也随章预算改变，hook_writer 须用新身份重写。
    wires_changed = deepcopy(wires)
    wires_changed['design']['scenes'][1]['brief'] = '重心改为追踪与藏匿。'
    transport.wires = wires_changed
    calls_before = len(transport.calls)
    hook_before = repo.read_model('stage4/context/hook/hook.json')
    _run(repo, session, budget=2100)
    assert sorted(transport.calls[calls_before:]) == [
        'body_ch01', 'body_ch02', 'design', 'hook_design', 'hook_writer']
    hook_after = repo.read_model('stage4/context/hook/hook.json')
    assert (hook_after.length_low, hook_after.length_high, hook_after.length_cap) != (
        hook_before.length_low, hook_before.length_high, hook_before.length_cap)
    assert repo.read('runtime/workflow_status.json')['status'] == 'delivered'


# ───────────────────────── W1/D2：调用层选章（canary）与身份隔离 ─────────────

def test_body_chapters_selection_writes_only_selected_chapters(tmp_path):
    """canary 选章（W1/D2）：`body_chapters=(2,)` 只写第 2 章，第 1 章不被触碰。"""
    wires = _full_chain_wires()
    repo, session, transport = _make_session(tmp_path, wires)
    _run(repo, session, through='stage4')            # 链跑到正文前（不买正文）
    calls_before = list(transport.calls)
    report = _run(repo, session, node_filter=('str06.stage5.body',),
                  body_chapters=(2,))
    assert report['body_chapters'] == [2]
    assert report['body_complete'] is False, '部分完成不是全书就绪'
    assert transport.calls[len(calls_before):] == ['body_ch02']
    assert repo.read_text('stage5/body/ch02.txt') == GOOD_CH2
    assert repo.inspect('stage5/acceptance/body/ch02.json')['status'] == 'accepted'
    assert not repo.path('stage5/body/ch01.txt').exists()
    # 逐章留证（D3/W2）：requested=attempted=(2,)，本次无失败
    evidence = repo.inspect('runtime/body_failures.json')
    assert evidence['requested'] == [2] and evidence['attempted'] == [2]
    assert evidence['chapters'] == {}


def test_body_chapters_full_run_after_canary_only_fills_missing(tmp_path):
    """canary 后不带参数整跑：只补缺章（0 或 1 次新调用），无重写 wire。"""
    wires = _full_chain_wires()
    repo, session, transport = _make_session(tmp_path, wires)
    _run(repo, session, through='stage4')
    _run(repo, session, node_filter=('str06.stage5.body',), body_chapters=(2,))
    calls_before = len(transport.calls)
    receipt_before = repo.path('stage5/acceptance/body/ch02.json').read_bytes()
    seal_dir = repo.root / 'runtime' / 'commit' / 'str06.stage5.body'
    seals_before = {p.name: p.read_bytes() for p in seal_dir.glob('*_ch02.json')}
    assert seals_before, 'canary 章的正式提交必须已封存'
    report = _run(repo, session)                    # 整书续跑（不含选章参数）
    assert report['assembly']['status'] == 'ready'
    assert report['body_complete'] is True
    assert transport.calls[calls_before:].count('body_ch01') == 1
    assert transport.calls[calls_before:].count('body_ch02') == 0, 'canary 章不重买'
    # canary 章的回执字节与 seal 在整跑后不变（已提交章身份稳定）
    assert repo.path('stage5/acceptance/body/ch02.json').read_bytes() == receipt_before
    assert {p.name: p.read_bytes() for p in seal_dir.glob('*_ch02.json')} == seals_before


def test_body_chapters_rejects_invalid_selection_before_any_call(tmp_path):
    """`()`／乱序／重复／越界在任何正文调用之前拒绝（I-34），不静默截断。"""
    wires = _full_chain_wires()
    repo, session, transport = _make_session(tmp_path, wires)
    _run(repo, session, through='stage4')
    calls_before = len(transport.calls)
    for bad in ((), (2, 1), (1, 1), (0,), (3,)):
        with pytest.raises(ValueError, match='body_chapters'):
            _run(repo, session, node_filter=('str06.stage5.body',),
                 body_chapters=bad)
    assert transport.calls[calls_before:] == [], '拒绝发生在付费调用之前'


def test_body_chapters_does_not_enter_request_or_receipt_identity(tmp_path):
    """身份约束（W1）：选章只作调用元数据——canary 章与整书跑出的同一章，
    请求身份与回执三把摘要逐字相等。"""
    wires = _full_chain_wires()
    canary_repo, canary_session, _ = _make_session(tmp_path, wires, name='canary')
    _run(canary_repo, canary_session, through='stage4')
    _run(canary_repo, canary_session, node_filter=('str06.stage5.body',),
         body_chapters=(2,))

    whole_repo, whole_session, _ = _make_session(tmp_path, wires, name='whole')
    _run(whole_repo, whole_session)

    canary_row = next(r for r in _ledger_rows(canary_repo, 'body_ch02')
                      if not r['cached'])
    whole_row = next(r for r in _ledger_rows(whole_repo, 'body_ch02')
                     if not r['cached'])
    assert canary_row['request_fingerprint'] == whole_row['request_fingerprint']
    canary_receipt = canary_repo.inspect('stage5/acceptance/body/ch02.json')
    whole_receipt = whole_repo.inspect('stage5/acceptance/body/ch02.json')
    for field in ('writer_view_sha256', 'validation_sha256', 'machine_view_sha256'):
        assert canary_receipt[field] == whole_receipt[field], field
    assert canary_receipt['prose_sha256'] == whole_receipt['prose_sha256']
    # 选章也不进 run 级身份摘要：只作审计字段记在 workflow_status.json
    canary_status = canary_repo.read('runtime/workflow_status.json')
    assert canary_status['body_chapters'] == [2]
    whole_status = whole_repo.read('runtime/workflow_status.json')
    assert whole_status['body_chapters'] is None
    assert canary_status['input_sha256'] == whole_status['input_sha256']


def test_body_pool_uses_its_own_concurrency(tmp_path, monkeypatch):
    """正文线程数跟 body 覆盖走；章观察仍用回落值。"""
    from concurrent.futures import ThreadPoolExecutor

    sizes: list[int | None] = []

    class Recording(ThreadPoolExecutor):
        def __init__(self, max_workers=None, *args, **kwargs):
            sizes.append(max_workers)
            super().__init__(max_workers=max_workers, *args, **kwargs)

    monkeypatch.setattr('novel.stages.chain.ThreadPoolExecutor', Recording)
    wires = _full_chain_wires()
    repo, session, _transport = _make_session(tmp_path, wires)
    _run(repo, session, concurrency=1,
         node_concurrency={'str06.stage5.body': 2})
    assert sizes == [1, 2]


def test_concurrency_level_does_not_enter_semantic_identity(tmp_path):
    """并发度不进语义身份：并发 1 与并发 4 得到相同请求身份集合与相同交付。"""
    wires = _full_chain_wires()
    repo_a, session_a, transport_a = _make_session(tmp_path, wires, name='out_a')
    report_a = _run(repo_a, session_a, concurrency=1)
    wires_b = deepcopy(wires)
    repo_b, session_b, transport_b = _make_session(tmp_path, wires_b, name='out_b')
    report_b = _run(repo_b, session_b, concurrency=4)
    # 相同章包 ⇒ 相同回执（章包摘要逐章一致），交付字节一致
    for ordinal in (1, 2):
        ra = repo_a.inspect(f'stage5/acceptance/body/ch{ordinal:02d}.json')
        rb = repo_b.inspect(f'stage5/acceptance/body/ch{ordinal:02d}.json')
        assert ra['writer_view_sha256'] == rb['writer_view_sha256']
        assert ra['prose_sha256'] == rb['prose_sha256']
    assert repo_a.path('stage7/final/报恩_rewriting.txt').read_bytes() == \
        repo_b.path('stage7/final/报恩_rewriting.txt').read_bytes()
    assert report_a['assembly']['delivery_identity'] == report_b['assembly']['delivery_identity']
    # 两次跑的外发多重集一致（并发不串输入、来源或费用；并发完成顺序不定）
    assert sorted(transport_a.calls) == sorted(transport_b.calls)


def test_body_hard_rejection_is_typed_and_keeps_other_chapter(tmp_path):
    """机械耗尽定位到章，返回已知负结论，其余章合法提交仍保留。"""
    from novel.stages.owner_revision import MechanicalDefectStop
    class HardRejectTransport(_RoutingTransport):
        def send(self, request):
            answer = super().send(request)
            if self._owner(request) == 'body_ch01':
                return ChatResponse(content='```正文```', finish_reason='stop',
                                    usage=answer.usage, model='fake')
            return answer
    wires = _full_chain_wires()
    repo, session, _transport = _make_session(tmp_path, wires, transport=HardRejectTransport(wires))
    with pytest.raises(MechanicalDefectStop) as stopped:
        _run(repo, session)
    assert stopped.value.node == 'str06.stage5.body' and stopped.value.item == 'chapter:1'
    status = repo.read('runtime/workflow_status.json')
    assert status['error_code'] == 'mechanical_defect' and status['error_item'] == 'chapter:1'
    assert any('[no_wrapper]' in e for e in status['error_context']['issues'])
    assert not repo.path('stage5/body/ch01.txt').exists()
    assert repo.read_text('stage5/body/ch02.txt') == GOOD_CH2


def test_chapter_failure_keeps_other_legal_commits_and_stops_truncated(tmp_path):
    """局部失败保留其他合法结果（I-31）；截断即停（D6）不重写、不进候选。"""
    wires = _full_chain_wires()
    repo, session, transport = _make_session(tmp_path, wires, truncate=('body_ch02',))
    with pytest.raises(Exception) as excinfo:
        _run(repo, session)
    # typed 错误保持原类型上抛（截断 → ExecutionError），run 级状态留痕
    from novel.llm.paid_requests import ExecutionError
    assert isinstance(excinfo.value, ExecutionError)
    assert excinfo.value.code == 'truncated'
    status = repo.read('runtime/workflow_status.json')
    assert status['status'] == 'failed' and status['failed_node'] == 'str06.stage5.body'
    assert status['error_code'] == 'truncated'
    # 第 1 章合法提交保留；第 2 章无提交
    assert repo.read_text('stage5/body/ch01.txt') == GOOD_CH1
    assert repo.inspect('stage5/acceptance/body/ch01.json')['status'] == 'accepted'
    assert not repo.path('stage5/body/ch02.txt').exists()
    failures = repo.inspect('runtime/body_failures.json')['chapters']
    assert set(failures) == {'2'}
    # 截断章只发一次请求（不重写）：body_ch02 恰 1 次外发
    assert transport.calls.count('body_ch02') == 1
    # 重跑：截断响应已按 attempt 耐久留证——重放免费命中同一截断，不再付费
    calls_before = len(transport.calls)
    with pytest.raises(Exception):
        _run(repo, session)
    assert len(transport.calls) == calls_before
    assert repo.read_text('stage5/body/ch01.txt') == GOOD_CH1
    assert set(repo.inspect('runtime/body_failures.json')['chapters']) == {'2'}

    # 恢复（D3/W2）：截断是节点设计缺陷——换请求身份（预算变化）且模型不再
    # 截断时正文完成执行 ⇒ 同一留证文件改写为合法空清单（不是删文件）。
    transport.truncate = ()
    report = _run(repo, session, budget=2100)
    assert report['assembly']['status'] == 'ready'
    evidence = repo.inspect('runtime/body_failures.json')
    assert evidence['chapters'] == {}
    assert evidence['requested'] == [1, 2] and evidence['attempted'] == [1, 2]
    assert evidence['recorded_at'] > 0


def test_body_system_change_invalidates_commits_without_changing_packet_or_validation(
        tmp_path, monkeypatch):
    """仅正文 system 改变：真实提交不能沿用，重写请求与回执绑定同一提示。"""
    from novel.stages import chain, common
    from novel.stages.stage5.body import recheck_chapter
    from tests.str06_facilities import body_ask

    class RecordingTransport(_RoutingTransport):
        def __init__(self, wires):
            super().__init__(wires)
            self.body_requests = []

        def send(self, request):
            if self._owner(request).startswith('body_ch'):
                self.body_requests.append(request)
            return super().send(request)

    def canonical_body_ask(session):
        # 通用测试替身默认用占位 system；本例送正式 system，保护实际外发与
        # 回执摘要同源，不把只改变摘要、却继续发送旧提示的路径当成功。
        ask = body_ask(session)

        def ask_body(owner, prompt, validate=None, system=None):
            return ask(owner, prompt, validate,
                       system=system or chain.BODY_WRITER_SYSTEM)

        return ask_body

    monkeypatch.setitem(globals(), '_fake_body', canonical_body_ask)
    wires = _full_chain_wires()
    transport = RecordingTransport(wires)
    repo, session, _ = _make_session(tmp_path, wires, transport=transport)
    assert _run(repo, session)['assembly']['status'] == 'ready'
    ir = repo.read_model('stage4/ir/writer_book_ir.json')
    packets = {n: project_chapter_packet(ir, n) for n in (1, 2)}
    identities_before = {n: chapter_identities(packet) for n, packet in packets.items()}
    receipts_before = {n: repo.inspect_sealed_model(
        f'stage5/acceptance/body/ch{n:02d}.json') for n in (1, 2)}
    original_system = common.BODY_WRITER_SYSTEM
    assert chain.BODY_WRITER_SYSTEM == original_system, '实际调用边界使用 common 的同源提示'
    assert len(transport.body_requests) == 2
    assert all(request.system == original_system for request in transport.body_requests)

    changed_system = original_system + '\n本轮呈现时保留人物反应的停顿。'
    # 模拟新代码进程重新 import：调用边界的 from-import 与身份产地一起读取
    # 新提示，协议版本与章包保持不变，不能靠测试替身的旧占位 system 命中缓存。
    monkeypatch.setattr(common, 'BODY_WRITER_SYSTEM', changed_system)
    monkeypatch.setattr(chain, 'BODY_WRITER_SYSTEM', changed_system)
    identities_after = {n: chapter_identities(packet) for n, packet in packets.items()}
    for n, packet in packets.items():
        old, new = identities_before[n], identities_after[n]
        assert new.writer_view_sha256 != old.writer_view_sha256
        assert new.validation_sha256 == old.validation_sha256
        assert new.machine_view_sha256 == old.machine_view_sha256
        assert recheck_chapter(packet, repo.read_text(f'stage5/body/ch{n:02d}.txt'),
                               receipts_before[n]) is None, '机械重验不能消除写手提示漂移'

    calls_before = len(transport.calls)
    report = _run(repo, session, node_filter=('str06.stage7.assemble',))
    assert report['assembly']['status'] == 'not_ready'
    assert report['body_access']['stale'] == [1, 2]
    assert report['body_access']['accepted'] == []
    assert len(transport.calls) == calls_before, '交付检查只拒识旧稿，不发请求'

    report = _run(repo, session,
                  node_filter=('str06.stage5.body', 'str06.stage7.assemble'))
    assert report['assembly']['status'] == 'ready'
    assert len(transport.calls) == calls_before + 2, '旧提交不得继续采用或只免费重验'
    assert all(request.system == changed_system for request in transport.body_requests[2:])
    for n in (1, 2):
        receipt = repo.inspect_sealed_model(f'stage5/acceptance/body/ch{n:02d}.json')
        assert receipt.writer_view_sha256 == identities_after[n].writer_view_sha256
        assert receipt.request_identity != receipts_before[n].request_identity
        assert receipt.protocol_version == receipts_before[n].protocol_version
        assert receipt.renderer_version == receipts_before[n].renderer_version
        assert receipt.validation_sha256 == receipts_before[n].validation_sha256
        assert receipt.machine_view_sha256 == receipts_before[n].machine_view_sha256


def test_book_call_ceiling_stops_generation(tmp_path):
    wires = _full_chain_wires()
    repo, session, transport = _make_session(tmp_path, wires, ceiling=3)
    with pytest.raises(ValueError, match='book-call-ceiling'):
        _run(repo, session)
    assert repo.read('runtime/workflow_status.json')['status'] == 'failed'


# ───────────────────────── B4 后半：Stage7 集合核对、乱序交付与 Hook 失败 ──────

def test_assemble_reports_stale_and_missing_per_item(tmp_path):
    """Stage7 集合核对（R-10.3/I-42）：失效与缺失按 item 分开报告、不静默跳过。

    某章章包摘要失效（回执在盘上、绑定旧章包）报 stale 而不是 missing；缺口
    报 missing。not_ready 不落新交付（旧 manifest 原样保留，消费者仍以它为
    准），他章合法提交不受影响；修复经同请求身份免费重放闭合，交付字节不变。
    """
    wires = _full_chain_wires()
    repo, session, transport = _make_session(tmp_path, wires)
    report = _run(repo, session)
    assert report['assembly']['status'] == 'ready'
    final_rel = 'stage7/final/报恩_rewriting.txt'
    delivered = repo.path(final_rel).read_bytes()
    manifest_before = repo.inspect('stage7/final/delivery_manifest.json')

    # ── 某章章包摘要失效：回执在盘上但绑定旧章包 → stale（不是 missing）──
    import json as _json
    tampered = repo.inspect('stage5/acceptance/body/ch02.json')
    tampered['writer_view_sha256'] = 'f' * 64
    repo.path('stage5/acceptance/body/ch02.json').write_text(
        _json.dumps(tampered, ensure_ascii=False), encoding='utf-8')
    report2 = _run(repo, session, node_filter=('str06.stage7.assemble',))
    assert report2['assembly']['status'] == 'not_ready'
    assert 'stale=[2]' in report2['assembly']['reason'], report2['assembly']['reason']
    assert report2['body_access']['stale'] == [2]
    assert report2['body_access']['accepted'] == [1]
    assert report2['accepted_body'] is False
    assert report2['chain_problems'] == [
        '第2章回执绑定的是另一版写手视图（章包已重编译，须重购）']
    # 盘面留证同按 item 报告；不落新交付，旧 manifest 原样（文件先落盘、manifest
    # 最后提交的既存交付不被未就绪重跑触碰）
    assert repo.inspect('runtime/delivery/assembly_report.json')['body_access']['stale'] == [2]
    assert repo.path(final_rel).read_bytes() == delivered
    assert repo.inspect('stage7/final/delivery_manifest.json') == manifest_before
    # 他章的合法提交不受该章失效影响
    assert repo.inspect('stage5/acceptance/body/ch01.json')['status'] == 'accepted'
    # 修复：该章按有效提交集合重做，同请求身份免费重放，交付字节不变
    calls_before = len(transport.calls)
    report3 = _run(repo, session)
    assert len(transport.calls) == calls_before
    assert report3['assembly']['status'] == 'ready'
    assert repo.path(final_rel).read_bytes() == delivered
    assert repo.inspect('stage5/acceptance/body/ch02.json')['status'] == 'accepted'

    # ── 缺口（无回执）：missing 按 item 报告；正文仍在盘上（合法提交未被破坏）──
    repo.path('stage5/acceptance/body/ch01.json').unlink()
    report4 = _run(repo, session, node_filter=('str06.stage7.assemble',))
    assert report4['assembly']['status'] == 'not_ready'
    assert 'missing=[1]' in report4['assembly']['reason'], report4['assembly']['reason']
    assert report4['body_access']['missing'] == [1]
    assert report4['body_access']['accepted'] == [2]
    assert repo.read_text('stage5/body/ch01.txt') == GOOD_CH1
    assert report4['chain_problems'] == []
    # 补缺：同请求身份免费重放，集合重新齐备，交付不变
    report5 = _run(repo, session)
    assert len(transport.calls) == calls_before
    assert report5['assembly']['status'] == 'ready'
    assert repo.path(final_rel).read_bytes() == delivered


def test_out_of_order_completion_yields_identical_ordered_delivery(tmp_path):
    """乱序完成得到相同有序交付（I-40/R-10.10）。

    第 2 章先提交落盘、第 1 章后提交：组装按阅读序加「第N章」标记、身份按
    有序 (chapter_ordinal, prose_sha256) 派生——与按序完成的交付字节、身份、
    manifest 完全一致（并发完成顺序不影响交付）。
    """
    wires = _full_chain_wires()
    base = tmp_path / 'reordered'
    base.mkdir()
    transport_a = _ReorderedTransport(
        deepcopy(wires),
        wait_for=base / 'run' / 'stage5' / 'acceptance' / 'body' / 'ch02.json')
    repo_a, session_a, _ = _make_session(tmp_path, wires, name='reordered',
                                         transport=transport_a)
    report_a = _run(repo_a, session_a)
    assert report_a['assembly']['status'] == 'ready'
    # 乱序前提成立：第 2 章回执先于第 1 章落盘
    mt1 = repo_a.path('stage5/acceptance/body/ch01.json').stat().st_mtime
    mt2 = repo_a.path('stage5/acceptance/body/ch02.json').stat().st_mtime
    assert mt2 < mt1, (mt1, mt2)

    repo_b, session_b, _ = _make_session(tmp_path, deepcopy(wires), name='ordered')
    report_b = _run(repo_b, session_b)
    assert report_b['assembly']['status'] == 'ready'
    # 乱序完成与按序完成：相同有序交付（字节、身份、manifest 完全一致）
    assert repo_a.path('stage7/final/报恩_rewriting.txt').read_bytes() == \
        repo_b.path('stage7/final/报恩_rewriting.txt').read_bytes()
    assert report_a['assembly']['delivery_identity'] == report_b['assembly']['delivery_identity']
    manifest_a = repo_a.inspect('stage7/final/delivery_manifest.json')
    manifest_b = repo_b.inspect('stage7/final/delivery_manifest.json')
    assert manifest_a == manifest_b
    assert [c['chapter_ordinal'] for c in manifest_a['chapters']] == [1, 2]


def test_hook_failure_keeps_other_legal_commits_and_recovers_free(tmp_path, monkeypatch):
    """Hook 失败不破坏其他合法提交（I-31）：章提交与已交付字节原样保留。

    Hook Design 输入变化 → 新 HookContext（章包摘要不变，章提交仍有效）；
    Hook 写手在重写额度内机械不过 → 停在 `str06.stage5.hook`，正文/命名/
    组装不被触碰；恢复原始输入后全链零新购，交付字节复原。
    """
    wires = _full_chain_wires()
    repo, session, transport = _make_session(tmp_path, wires)
    report = _run(repo, session)
    assert report['assembly']['status'] == 'ready'
    final_rel = 'stage7/final/报恩_rewriting.txt'
    delivered = repo.path(final_rel).read_bytes()
    receipts_before = {ordinal: repo.path(
        f'stage5/acceptance/body/ch{ordinal:02d}.json').read_bytes() for ordinal in (1, 2)}
    body1_before = repo.path('stage5/body/ch01.txt').read_bytes()

    # ── Hook 输入变化（新 HookContext，章包不变）＋ Hook 写手机械不过 ──
    wires_hook_changed = deepcopy(wires)
    wires_hook_changed['hook_design']['local_focus'] = '以瞬时反应突出局面反差。'
    transport.wires = wires_hook_changed
    transport.hook_text = '陆大哥的旧约。'  # 未释放姓名，每次都失败
    calls_before = len(transport.calls)
    from novel.stages.owner_revision import MechanicalDefectStop
    from novel.stages import chain
    # Hook 提示词修订只改变其请求身份；正文设计、章包与历史正文仍同一输入。
    with monkeypatch.context() as changed_hook:
        changed_hook.setattr(chain, 'HOOK_DESIGN_RULES',
                             chain.HOOK_DESIGN_RULES + '\n局部表现重点采用修订后的反差策略。')
        with pytest.raises(MechanicalDefectStop, match='Hook 写作') as stopped:
            _run(repo, session)
    assert stopped.value.node == 'str06.stage5.hook' and stopped.value.item == 'book'
    status = repo.read('runtime/workflow_status.json')
    assert status['status'] == 'failed' and status['failed_node'] == 'str06.stage5.hook'
    assert status['error_code'] == 'mechanical_defect' and status['error_context']['issues']
    # 新 HookContext 是新请求身份：hook_design 1 次＋hook_writer 2 次外发
    # （第 3 轮重写提示与第 2 轮逐字相同 → 同请求身份命中缓存，不再外发）；
    # 正文、命名、组装不被触碰
    assert transport.calls[calls_before:] == ['hook_design'] + ['hook_writer'] * 2
    for ordinal in (1, 2):
        assert repo.path(f'stage5/acceptance/body/ch{ordinal:02d}.json').read_bytes() == \
            receipts_before[ordinal]
    assert repo.path('stage5/body/ch01.txt').read_bytes() == body1_before
    assert repo.path(final_rel).read_bytes() == delivered

    # ── 恢复原始输入：全链缓存命中零新购，交付复原 ──
    transport.wires = wires
    transport.hook_text = '苏芸娘望着空房。'
    calls_after_run2 = len(transport.calls)
    report2 = _run(repo, session)
    assert len(transport.calls) == calls_after_run2
    assert report2['assembly']['status'] == 'ready'
    assert repo.path(final_rel).read_bytes() == delivered
    for ordinal in (1, 2):
        assert repo.path(f'stage5/acceptance/body/ch{ordinal:02d}.json').read_bytes() == \
            receipts_before[ordinal]
    assert repo.read('runtime/workflow_status.json')['status'] == 'delivered'


def test_clean_chain_keeps_one_purchase_and_registers_candidates_without_carry_forward(tmp_path):
    """干净链每节点只买一次：候选登记随 H1b 落盘，但同身份不走沿用凭证（缓存重放）。"""
    from novel.stages import candidates

    wires = _full_chain_wires()
    repo, session, transport = _make_session(tmp_path, wires)
    _run(repo, session)
    from novel.work_units import read_book_budget
    base = _candidates_base(repo)
    assert read_book_budget(base, 'run').used == 12, '沿用机制不改变干净链的购买次数'
    design_rows = _ledger_rows(repo, 'design')
    assert len([row for row in design_rows if not row.get('cached')]) == 1
    assert not [row for row in design_rows if row.get('cache_policy') == 'carried_forward'], \
        '同身份免费重放是缓存命中，不记沿用凭证'
    registry = candidates.load_registry(repo, 'design')
    assert registry is not None and len(registry.candidates) == 1
    entry = registry.candidates[0]
    assert entry['wire_sha256'] and entry['request_sha256'] and entry['upstream_version']['wire']
    # 免费重放：零新购、登记不退化
    _run(repo, session)
    assert len(transport.calls) == 12
    assert candidates.load_registry(repo, 'design').candidates
    assert not (repo.root / 'runtime/revisions/carried').exists() or not list(
        (repo.root / 'runtime/revisions/carried/design').glob('*.json')), \
        '免费重放不产生沿用凭证'
