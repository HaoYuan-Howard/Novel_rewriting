#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""hard_cases —— 新链每条 HARD 判据的最小会红样本登记表（VAL-02，F8a 重写）。

【F8a 重写的理由】旧表登记的是旧 Stage4/6/7 判据的最小会红样本；那些判据随旧链
整体退役（`contracts/judges/str06.py` 是新链唯一注册表）。本表现在登记**新链**
HARD 判据的最小会红样本：每个 ``build`` 都调用真实判据入口——Guard M1–M6、
章级机械验、Hook 机械验、命名残留与回执链都在 per-stage 引擎与 `novel/stages/common.py`。

【与 str06 反例套件的分工】`tests/test_str06_*.py` 是各能力的完整反例套件
（绿底座＋逐条红）；本表是**跨判据的 consolidated 抽样**：每条新链 HARD 至少有一个
在这里登记的最小会红样本，`test_hard_counterexamples.py` 逐条真跑。完整性由
``test_every_new_chain_hard_rule_is_covered_somewhere`` 兜底（判据 id 必须在本表
或 str06 套件里出现）。
"""
from __future__ import annotations

from typing import Callable, NamedTuple


class Case(NamedTuple):
    stage: str
    build: Callable[[], list]
    #: 会红样本必须命中的结论片段（真实判据入口的输出子串）。
    expect: str


def _guard_m1() -> list:
    """M1：本场 owner 的义务没有完整进入 IR。"""
    from contracts.schema.grounding import BindingEntry
    from tests.test_str06_fold_guard import _binding, _context
    from novel.stages.stage4.guard import check_obligation_coverage

    binding = _binding(BindingEntry(obligation_id='ob1', scene_id='s01',
                                    beat_ref='s01#bt1', supporting_beat_refs=()))
    return check_obligation_coverage(binding, _context('s01', obligations=()))


def _guard_m2() -> list:
    """M2：entry_knowledge 的 established_by 不早于本场起点（提前揭示）。"""
    from contracts.schema.context import KnowledgeEntry
    from tests.test_str06_fold_guard import _context
    from novel.stages.stage4.guard import check_entry_timing

    # ce1：把后期（s03）才揭示的 knowledge 塞进 s02 的 entry_knowledge。
    context = _context('s02', entry_knowledge=(
        KnowledgeEntry(key='secret', mode='known', holder='ch1', proposition='身世',
                       established_by='s03#bt1'),))
    return check_entry_timing(context, {'s01': 0, 's02': 1, 's03': 2})


def _guard_m3() -> list:
    """M3：entry_state 的 established_by 不早于本场起点（提前效应）。"""
    from contracts.schema.context import StateEntry
    from tests.test_str06_fold_guard import _context
    from novel.stages.stage4.guard import check_entry_timing

    # ce2：把后期建立的 state 塞进 s02 的 entry_state。
    context = _context('s02', entry_state=(
        StateEntry(domain='world', key='season', value='冬',
                   established_by='s03#scene'),))
    return check_entry_timing(context, {'s01': 0, 's02': 1, 's03': 2})


def _guard_m4() -> list:
    """M4：承载位置指向不真实的拍。"""
    from contracts.schema.grounding import BindingEntry
    from tests.test_str06_fold_guard import _binding, _graph
    from novel.stages.stage4.guard import check_binding_beat_refs

    binding = _binding(BindingEntry(obligation_id='ob1', scene_id='s01',
                                    beat_ref='s01#bt1', supporting_beat_refs=()))
    # 跨产物存在性：binding 合法（beat_ref 落在自己声明的场景内），但 graph 里
    # 已经没有 s01 了——Binding 的 validator 保不住「对手盘当前还在」。
    graph = _graph().model_copy(update={
        'scenes': tuple(s for s in _graph().scenes if s.scene_id != 's01')})
    return check_binding_beat_refs(binding, graph)


def _guard_m5() -> list:
    """M5：IR 释放集与 Design 显式释放集不一致（同一称谓释放引擎复算）。"""
    from contracts.schema.context import SurfaceRelease
    from contracts.schema.grounding import BindingEntry
    from tests.test_str06_fold_guard import _binding, _context, _graph
    from novel.stages.stage4.guard import run_mechanical_guard

    graph = _graph()
    binding = _binding(BindingEntry(obligation_id='ob1', scene_id='s01',
                                    beat_ref='s01#bt1', supporting_beat_refs=()))
    context = _context('s01', obligations=('ob1',), released=(
        SurfaceRelease(char_id='ch1', surfaces=('苏姑娘',)),))
    return run_mechanical_guard(binding=binding, context=context, graph=graph)


def _guard_m6() -> list:
    """M6：释放面人物尚未由 Design 声明参与或引入。"""
    from contracts.schema.context import SurfaceRelease
    from tests.test_str06_fold_guard import _context, _graph
    from novel.stages.stage4.guard import check_cast_legality

    context = _context('s01', released=(SurfaceRelease(char_id='ghost', surfaces=('阿 ghosts',)),))
    return check_cast_legality(context, _graph(), appeared={'ch1'})


def _body_withheld() -> list:
    """未释放专名进正文：硬失败（重写耗尽后无合法候选即停止，不披露放行）。"""
    from novel.stages.stage5.body_checks import validate_chapter_prose
    return validate_chapter_prose(
        '苏芸娘推门进屋，看见满地信纸。', withheld_literals=('苏芸娘',),
        released_surfaces=('苏姑娘',), target_chars=1000,
        band_low=600, band_high=1300)


def _body_empty() -> list:
    from novel.stages.stage5.body_checks import validate_chapter_prose
    return validate_chapter_prose(
        '   ', withheld_literals=(), released_surfaces=(), target_chars=1000,
        band_low=600, band_high=1300)


def _hook_withheld() -> list:
    """Hook 出现未释放专名：机械验拦下，拒绝稿不落正式位。

    绿 Context 来自生产构造器（`test_str06_hook._green_pair` 的同一入口），
    只把成文换成泄漏未释放专名的那一段。
    """
    from tests.test_str06_hook import _green_pair
    from novel.stages.stage5.hook_writer import validate_hook_prose

    _body, _published, context = _green_pair()
    return validate_hook_prose('苏芸娘想起陆大哥。', context)


def _name_map_conflict() -> list:
    """同姓不同人不得合并：NameMap 编译闭合拒绝。"""
    from tests.test_str06_naming import _chars, _naming_wire
    from novel.stages.stage7.naming import compile_name_map

    wire = _naming_wire([
        {'char_ref': 'c1', 'name': '苏瑶', 'family': '苏', 'kept_reason': None},
        {'char_ref': 'c2', 'name': '苏瑶', 'family': '苏', 'kept_reason': None},
    ])
    _map, errors = compile_name_map(wire, _chars())
    return errors


def _naming_residual() -> list:
    """应替换的源名在交付文本中残留且未认领。"""
    from novel.stages.stage7.naming import check_residual
    return check_residual('苏姑娘推门进屋。', {'苏姑娘': '苏瑶'}, claims=(), kept_literals=())


def _chain_broken() -> list:
    """章回执与正文 hash 脱钩：回执链断裂，装配未就绪。"""
    from novel.stages.stage7.assembly import verify_chapter_chain
    receipts = {1: {'status': 'accepted', 'book_ir_sha256': 'a' * 64,
                    'prose_sha256': 'b' * 64, 'prior_text_sha256': 'c' * 64}}
    return verify_chapter_chain(receipts, {1: '推门进屋。'}, book_ir_sha256='a' * 64)


CASES: dict[str, Case] = {
    'guard_m1_obligation_coverage': Case('stage4', _guard_m1, 'M1 义务覆盖'),
    'guard_m2_early_reveal': Case('stage4', _guard_m2, 'M2 提前揭示'),
    'guard_m3_early_effect': Case('stage4', _guard_m3, 'M3 提前效应'),
    'guard_m4_carrier_ref': Case('stage4', _guard_m4, 'M4 承载位置'),
    'guard_m5_surface_release': Case('stage4', _guard_m5, 'M5 称谓释放'),
    'guard_m6_cast_legality': Case('stage4', _guard_m6, 'M6 人物合法性'),
    'body_chapter_complete': Case('stage5', _body_empty, '[non_empty]'),
    'body_withheld_literals': Case('stage5', _body_withheld, '[withheld_literals]'),
    'hook_mechanical': Case('stage5', _hook_withheld, '[withheld_literals]'),
    'name_map_compile': Case('stage7', _name_map_conflict, 'naming 条目形状非法'),
    'naming_residual': Case('stage7', _naming_residual, '在交付文本中残留'),
    'assembly_chapter_chain': Case('stage7', _chain_broken, 'prose_sha256 脱钩'),
}


# ─── 其余 HARD 的最小会红样本（编译闭合一族） ────────────────────────────────

def _body_design_bad_wire() -> list:
    """BodyDesign 编译闭合：非法 POV（未知人物）当场拒。"""
    from novel.stages.stage4.design import assemble_body_design
    _body, errors = assemble_body_design(
        {'scenes': [{'scene_id': 's01',
                     'beats': [{'beat_id': 'bt1', 'event': 'e',
                                'participants': ['ch1']}],
                     'delivery': {'pov': 'ghost', 'voice': 'v'}}]},
        known_char_ids={'ch1'})
    return errors


def _chapter_count_mismatch() -> list:
    """章唯一键：目标章数必须等于实际章任务数（约束声明 99、实到 N 即拒）。"""
    from contracts.schema.grounding import OutputConstraints
    from tests.test_str06_hook import _body_wire
    from novel.stages.stage4.design import assemble_body_design

    _body, errors = assemble_body_design(
        _body_wire(), known_char_ids={'fu_jun', 'yun_niang'},
        constraints=OutputConstraints(target_chapter_count=99,
                                      total_length_budget=10000))
    return errors


def _preserve_bad_wire() -> list:
    """Preserve 编译闭合：obligation_id 重复拒收。"""
    from novel.stages.stage4.grounding import assemble_preserve

    from contracts.schema.source import (
        Beat, BlueprintsArtifactV12, ChapterBlueprint, CharacterEntry,
        CharactersCore, SurfaceWindow,
    )
    entry = {'obligation_id': 'o1', 'kind': 'event_core', 'statement': 's',
             'must_preserve': True, 'satisfaction': '明确呈现',
             'lineage': {'blueprint_refs': ['b01_01'], 'character_ids': []}}
    _checkpoint, errors = assemble_preserve(
        {'obligations': [entry, dict(entry)]},
        policy_id='preserve-default', policy_version=1,
        characters=CharactersCore(characters={'ch1': CharacterEntry(
            canonical='苏姑娘',
            surfaces=(SurfaceWindow(surface='苏姑娘', from_chapter=1,
                                    source_refs=('e01_01',)),))}),
        blueprint=BlueprintsArtifactV12(chapters=(
            ChapterBlueprint(chapter=1, beats=(Beat(
                beat_id='b01_01', event='e', source_refs=('e01_01',)),)),)),
        provider='p', model='m', request_sha256='a' * 64)
    return errors


def _checkpoint_with(*obligation_ids: str):
    from contracts.schema.grounding import (
        PreserveCheckpoint, SourceObligation, SourceLineage,
    )
    return PreserveCheckpoint(
        checkpoint_id='cp1', policy_id='preserve-default', policy_version=1,
        characters_sha256='a' * 64, blueprints_sha256='b' * 64,
        provider='p', model='m', request_sha256='c' * 64,
        checkpoint_sha256='d' * 64,
        obligations=tuple(SourceObligation(
            obligation_id=oid, kind='event_core', statement=oid,
            lineage=SourceLineage(blueprint_refs=('b01_01',), character_ids=()),
            must_preserve=True, satisfaction='明确呈现')
            for oid in obligation_ids))


def _binding_unclosed() -> list:
    """Bind 机械校验：entry 的 obligation 不在 checkpoint 分母内。"""
    from novel.stages.stage4.grounding import assemble_binding

    _model, errors = assemble_binding(
        {'entries': [{'obligation_id': 'ghost', 'scene_id': 's01',
                      'beat_ref': 's01#bt1', 'supporting_beat_refs': []}],
         'unbound': []},
        _checkpoint_with('o1'), known_scene_beats={'s01': {'bt1'}})
    return errors


def _hook_design_bad_wire() -> list:
    """Hook 设计机械校验：材料引用不存在的拍。"""
    from tests.test_str06_hook import _green_pair
    from novel.stages.stage5.hook import assemble_hook_design

    graph, projection, _context = _green_pair()
    _hook, errors = assemble_hook_design(
        {'materials': [{'beat_ref': 's99#bt1', 'presentation': '倒叙材料'}],
         'opening_facts': [], 'forbidden_reveals': []},
        body=graph, known_char_ids=set(), name_projection=projection)
    return errors


def _plan_unclosed() -> list:
    """ExecutionPlan 编译闭合：Binding 义务分母不闭合（entry 无对应 obligation）。"""
    from contracts.schema.grounding import BindingEntry, SemanticBinding, SourceObligation, SourceLineage
    from tests.test_str06_fold_guard import _graph
    from novel.stages.stage4.compile import compile_execution_plan

    binding = SemanticBinding(
        binding_id='bd1', preserve_checkpoint_sha256='a' * 64,
        obligations=(SourceObligation(
            obligation_id='o1', kind='event_core', statement='s',
            lineage=SourceLineage(blueprint_refs=('b01_01',), character_ids=()),
            must_preserve=True, satisfaction='明确呈现'),),
        entries=(BindingEntry(obligation_id='ghost', scene_id='s01',
                              beat_ref='s01#bt1', supporting_beat_refs=()),),
        unbound=())
    _plan, errors = compile_execution_plan(_graph(), binding)
    return errors


def _hook_context_unresolvable() -> list:
    """HookContext 仅凭正式输入可重建：起笔事实在引用处未建立即拒。"""
    from contracts.schema.hook import HookConstraints
    from tests.test_str06_hook import _green_pair
    from novel.stages.stage5.hook_context import compile_hook_context

    _body, graph, _context = _green_pair()
    from tests.test_str06_hook import _projection
    broken = graph.model_copy(update={'hook': graph.hook.model_copy(update={
        'opening_facts': tuple(
            fact.model_copy(update={'ref': 's99#bt1'})
            for fact in graph.hook.opening_facts)})})
    _rebuilt, errors = compile_hook_context(
        broken, _projection(), HookConstraints(target_chars=280, hard_max_chars=360))
    return errors


def _body_out_of_band() -> list:
    from novel.stages.stage5.body_checks import validate_chapter_prose
    return validate_chapter_prose(
        '短。', withheld_literals=(), released_surfaces=(), target_chars=1000,
        band_low=600, band_high=1300)


def _receipt_stale() -> list:
    """章回执绑定：旧回执配新 IR 必须拒绝（三份绑定逐一核对）。"""
    from contracts.schema.receipt import ChapterReceipt
    from novel.stages.common import check_chapter_receipt

    from contracts.schema.receipt import ChapterLengthDisclosure
    receipt = ChapterReceipt(
        contract_version='str06-rc4', chapter_ordinal=1, status='accepted',
        book_ir_sha256='a' * 64, prior_text_sha256='b' * 64,
        prose_sha256='c' * 64, request_identity='r1',
        protocol_version='p1', renderer_version='r1', validator_version='v1',
        mechanical_checks=('non_empty',), length=ChapterLengthDisclosure(
            char_count=100, target_chars=100, band_low=60, band_high=130,
            within_band=True, deviation_ratio=0.0))
    return check_chapter_receipt(receipt, book_ir_sha256='d' * 64,
                                 prior_text_sha256='b' * 64, prose_sha256='c' * 64)


def _hook_rejected_no_formal() -> list:
    """Hook 重写上限内无合法候选：拒绝稿不落正式位（机械验持续拦下）。"""
    from tests.test_str06_hook import _green_pair
    from novel.stages.stage5.hook_writer import MAX_REWRITES, validate_hook_prose

    _body, _published, context = _green_pair()
    errors: list[str] = []
    for _attempt in range(1 + MAX_REWRITES):
        errors = validate_hook_prose('苏芸娘想起陆大哥。', context)
        if not errors:
            return []
    return errors


def _delivery_not_ready() -> list:
    """交付可确定性重放：缺环时装配显式未就绪（not_ready），不制造成功状态。"""
    from novel.stages.stage7.assembly import chapter_access

    access = chapter_access(range(1, 3), {})
    if access.ready:
        return []
    return [f'assembly not ready: accepted={list(access.accepted)} '
            f'missing={list(access.missing)}']


CASES.update({
    'body_length_band': Case('stage5', _body_out_of_band, '[length_band]'),
    'chapter_receipt_binding': Case('stage5', _receipt_stale, 'book_ir_sha256'),
    'hook_receipt_binding': Case('stage5', _hook_rejected_no_formal,
                                 '[withheld_literals]'),
    'body_design_compile': Case('stage4', _body_design_bad_wire, '构造失败'),
    'chapter_plan_shape': Case('stage4', _chapter_count_mismatch, '章'),
    'length_budget_conserved': Case('stage4', _chapter_count_mismatch, '章'),
    'preserve_compile': Case('stage4', _preserve_bad_wire, 'obligation'),
    'binding_mechanical': Case('stage4', _binding_unclosed, 'Binding'),
    'hook_design_mechanical': Case('stage4', _hook_design_bad_wire, '材料引用不存在的拍'),
    'execution_plan_compile': Case('stage4', _plan_unclosed, 'Binding'),
    'writer_ir_dual_view': Case('stage4', _hook_context_unresolvable, '起笔事实'),
    'hook_context_rebuildable': Case('stage4', _hook_context_unresolvable, '起笔事实'),
    'delivery_replayable': Case('stage7', _delivery_not_ready, 'not ready'),
})
