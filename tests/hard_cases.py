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
from tests.str06_expression_inputs import (
    sample_narration, sample_requirements, sample_blueprint_expression, sample_design_expression,
)
from tests.str06_facilities import anchor_stub


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


def _design_bad_knowledge_mode() -> list:
    """R-5.12：knowledge 域的知情模式落在闭集外（believes 已入集，其余拒收）。"""
    from novel.stages.stage4.design import assemble_body_design
    wire = _design_wire_for_mode()
    wire['scenes'][0]['beats'][0]['target_delta'].append(
        {'domain': 'knowledge', 'subjects': [], 'holder': 'ch1', 'op': 'set',
         'scope': 'book', 'key': 'guess', 'value': '以为夫君已离开',
         'knowledge_mode': 'imagines'})
    _body, errors = assemble_body_design(wire, known_char_ids={'ch1'}, expression_requirements=sample_requirements(), source_narration=sample_narration())
    return errors


def _design_wire_for_mode() -> dict:
    from tests.test_str06_target import _design_wire
    wire = _design_wire()
    wire['scenes'][0]['beats'][0]['participants'] = ['ch1']
    return wire


def _guard_m6() -> list:
    """M6：释放面人物尚未由 Design 声明参与或引入。"""
    from contracts.schema.context import SurfaceRelease
    from tests.test_str06_fold_guard import _context, _graph
    from novel.stages.stage4.guard import check_cast_legality

    context = _context('s01', released=(SurfaceRelease(char_id='ghost', surfaces=('阿 ghosts',)),))
    return check_cast_legality(context, _graph(), appeared={'ch1'})


def _guard_m7() -> list:
    """M7：声明的读取没有进入 IR（入场值不在 entry 视图、拍级值未挂拍）。

    R2/AUD-03 判据面：s02 声明了入场读取与拍级读取各一条，IR 两条都没带
    （拍级视图也没有挂值）——写手拿不到本场依赖的事实，在此红。
    """
    from contracts.schema.context import (
        BeatIR, VerificationIR, WriterBrief, WriterContext,
    )
    from tests.test_str06_r2_contracts import _read_graph
    from novel.stages.stage4.guard import check_read_projection

    scene = _read_graph().scenes[1]                       # s02：声明了两条读取
    ir = VerificationIR(
        scene_id='s02', obligations=(),
        beats=(BeatIR(beat_ref='s02#b01', event='点灯'),
               BeatIR(beat_ref='s02#b02', event='灯下翻检')))
    context = WriterContext(
        scene_id='s02', execution_slice_sha256='a' * 64, verification_ir=ir,
        writer_brief=WriterBrief(scene_id='s02', realization_brief='重心；收束。',
                                 scene_driver_summary='ch1 想查', pov='ch1',
                                 voice='冷峻'))
    return check_read_projection(scene, context)


def _guard_m8() -> list:
    """M8：本场线程操作未按 Design 投影进 IR（must_realize 身份漏包）。"""
    from tests.test_str06_r2_contracts import _project, _thread_graph
    from novel.stages.stage4.guard import check_thread_projection

    graph = _thread_graph()
    return check_thread_projection(graph, _project('s02', graph))


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
    """章回执与当前编译脱钩：集合核对报出，装配未就绪（D2；rc6 身份拆分）。"""
    from novel.stages.stage7.assembly import verify_chapter_receipts
    from contracts.schema.receipt import BODY_VALIDATOR_VERSION, BODY_MECHANICAL_CHECKS
    receipts = {1: {'status': 'accepted', 'writer_view_sha256': 'c' * 64,
                    'validation_sha256': 'b' * 64, 'machine_view_sha256': 'd' * 64,
                    'validator_version': BODY_VALIDATOR_VERSION,
                    'mechanical_checks': BODY_MECHANICAL_CHECKS,
                    'prose_sha256': 'b' * 64}}
    from novel.stages.common import ChapterIdentities
    return verify_chapter_receipts(
        receipts, {1: '推门进屋。'},
        identities_by_ordinal={1: ChapterIdentities(
            writer_view_sha256='c' * 64, validation_sha256='b' * 64,
            machine_view_sha256='d' * 64)})


CASES: dict[str, Case] = {
    'guard_m1_obligation_coverage': Case('stage4', _guard_m1, 'M1 义务覆盖'),
    'guard_m2_early_reveal': Case('stage4', _guard_m2, 'M2 提前揭示'),
    'guard_m3_early_effect': Case('stage4', _guard_m3, 'M3 提前效应'),
    'guard_m4_carrier_ref': Case('stage4', _guard_m4, 'M4 承载位置'),
    'guard_m5_surface_release': Case('stage4', _guard_m5, 'M5 称谓释放'),
    'guard_m6_cast_legality': Case('stage4', _guard_m6, 'M6 人物合法性'),
    'guard_m7_read_projection': Case('stage4', _guard_m7, 'M7 声明读取未进入 IR'),
    'guard_m8_thread_lifecycle': Case('stage4', _guard_m8,
                                     'M8 本场线程操作未按 Design 投影进 IR'),
    'design_knowledge_mode_closure': Case('stage4', _design_bad_knowledge_mode, 'knowledge_mode'),
    'body_chapter_complete': Case('stage5', _body_empty, '[non_empty]'),
    'body_withheld_literals': Case('stage5', _body_withheld, '[withheld_literals]'),
    'hook_mechanical': Case('stage5', _hook_withheld, '[withheld_literals]'),
    'name_map_compile': Case('stage7', _name_map_conflict, 'naming 条目形状非法'),
    'naming_residual': Case('stage7', _naming_residual, '在交付文本中残留'),
    'assembly_chapter_chain': Case('stage7', _chain_broken, 'prose_sha256 脱钩'),
}


# ─── 其余 HARD 的最小会红样本（编译闭合一族） ────────────────────────────────

def _body_design_bad_wire() -> list:
    """BodyDesign 编译闭合：非法 POV（未知人物）当场拒（stage4-design-v5 形状）。"""
    from novel.stages.stage4.design import assemble_body_design
    _body, errors = assemble_body_design(
        {'protocol_version': 'stage4-design-v5', 'book_style': '冷峻。',
         'chapters': [{'purpose': 'e', 'scenes': 1}],
         'scenes': [{'beats': [{'event': 'e', 'participants': ['ch1'],
                                'delivery_hint': ''}],
                     'delivery': {'pov': 'ghost', 'voice': 'v'},
                     'driver': {'focal_actor': 'ch1', 'immediate_want': 'w',
                                'opposition': 'o', 'turn': 't'},
                     'realization_brief': '重心在e；展开后收束。'}]},
        known_char_ids={'ch1'}, expression_requirements=sample_requirements(), source_narration=sample_narration())
    return errors


def _chapter_count_mismatch() -> list:
    """章唯一键：目标章数必须等于实际章任务数（约束声明 99、实到 N 即拒）。"""
    from contracts.schema.grounding import OutputConstraints
    from tests.test_str06_hook import _body_wire
    from novel.stages.stage4.design import assemble_body_design

    _body, errors = assemble_body_design(
        _body_wire(), known_char_ids={'fu_jun', 'yun_niang'},
        constraints=OutputConstraints(target_chapter_count=99,
                                      total_length_budget=10000), expression_requirements=sample_requirements(), source_narration=sample_narration())
    return errors


def _preserve_bad_wire() -> list:
    """Preserve 编译闭合：模型带 obligation_id 即拒——编号由 compiler 派发（I-21）。"""
    from novel.stages.stage4.grounding import assemble_preserve

    from contracts.schema.source import (
        BlueprintsArtifactV17, ChapterBlueprint, CharacterEntry,
        CharactersCore, SurfaceWindow,
    )
    from tests.str06_facilities import anchor_stub, v13_beat, v13_effect
    entry = {'obligation_id': 'o1', 'kind': 'event_core', 'statement': 's',
             'must_preserve': True, 'satisfaction': '明确呈现',
             'anchors': ['b01_01.e1']}
    _checkpoint, errors = assemble_preserve(
        {'protocol_version': 'stage4-preserve-v5', 'obligations': [entry]},
        policy_id='preserve-default', policy_version=1,
        characters=CharactersCore(characters={'ch1': CharacterEntry(
            canonical='苏姑娘',
            surfaces=(SurfaceWindow(surface='苏姑娘', from_chapter=1,
                                    source_refs=('e01_01',)),))}),
        blueprint=BlueprintsArtifactV17(source_expression=sample_blueprint_expression(), chapters=(
            ChapterBlueprint(chapter=1,
                             beats=(v13_beat('b01_01', 'e', effects=(
                                 v13_effect({'kind': 'world'}, 'social_standing', '多舛'),)),),
                             scenes=({'scene_id': 'sc01', 'beat_ids': ('b01_01',)},),
                             causal_edges=()),),
            evidence={'e01_01': '苏姑娘初到陆家。'}),
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
            anchors=anchor_stub(),
            lineage=SourceLineage(blueprint_refs=('b01_01',), character_ids=()),
            must_preserve=True, satisfaction='明确呈现')
            for oid in obligation_ids))


def _binding_unclosed() -> list:
    """Binding 派生（CS-3）：real 标注引用 checkpoint 分母外义务。"""
    from tests.test_str06_execution_ir import _graph
    from novel.stages.stage4.grounding import derive_semantic_binding

    _model, errors = derive_semantic_binding(
        {'ghost': [(0, 0)]}, _graph(), _checkpoint_with('o1'))
    return errors


def _hook_design_bad_wire() -> list:
    """Hook 设计机械校验：材料引用不存在的拍。"""
    from tests.test_str06_hook import _green_pair
    from novel.stages.stage4.hook import assemble_hook_design

    graph, projection, _context = _green_pair()
    _hook, errors = assemble_hook_design(
        {'protocol_version': 'stage4-hook-design-v3',
         'pov': {'person': 'third', 'focalizers': ['narrator']},
         'local_focus': '用即时反应形成反差',
         'reader_expectation': {'kind': 'action', 'focus': '人物怎样应对旧约',
                                'followup_refs': [{'scene': 'sc02', 'beat': 'b02'}]},
         'materials': [{'beat_ref': {'scene': 's99', 'beat': 'bt1'},
                        'emphasis': 'develop', 'transition': 'start',
                        'presentation': '倒叙材料'}],
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
            anchors=anchor_stub(),
            lineage=SourceLineage(blueprint_refs=('b01_01',), character_ids=()),
            must_preserve=True, satisfaction='明确呈现'),),
        entries=(BindingEntry(obligation_id='ghost', scene_id='s01',
                              beat_ref='s01#bt1', supporting_beat_refs=()),),
        unbound=())
    _plan, errors = compile_execution_plan(_graph(), binding)
    return errors


def _chapter_closure_unclosed() -> list:
    """章闭包（R-6.13）：已声明状态读取在其定位点未建立（没有任何建立点）。"""
    from contracts.schema.grounding import BindingEntry
    from contracts.schema.state import StateRead
    from tests.test_str06_fold_guard import _binding, _graph
    from novel.stages.stage4.compile import compile_execution_plan

    graph = _graph()
    mutated = graph.model_copy(update={
        'scenes': (graph.scenes[0],
                   graph.scenes[1].model_copy(update={
                       'state_reads': (StateRead(domain='world', key='season'),)}),
                   graph.scenes[2])})
    binding = _binding(BindingEntry(obligation_id='ob1', scene_id='s01',
                                    beat_ref='s01#bt1'))
    _plan, errors = compile_execution_plan(mutated, binding)
    return errors


def _hook_context_unresolvable() -> list:
    """HookContext 仅凭正式输入可重建：起笔事实在引用处未建立即拒。"""
    from tests.test_str06_hook import _green_pair
    from novel.stages.stage4.hook_context import compile_hook_context

    _body, graph, _context = _green_pair()
    from tests.test_str06_hook import _projection
    broken = graph.model_copy(update={'hook': graph.hook.model_copy(update={
        'opening_facts': tuple(
            fact.model_copy(update={'ref': 's99#bt1'})
            for fact in graph.hook.opening_facts)})})
    _rebuilt, errors = compile_hook_context(
        broken, _projection(), total_budget=2000)
    return errors


def _body_out_of_band() -> list:
    from novel.stages.stage5.body_checks import validate_chapter_prose
    return validate_chapter_prose(
        '短。', withheld_literals=(), released_surfaces=(), target_chars=1000,
        band_low=600, band_high=1300)


def _receipt_stale() -> list:
    """章回执绑定：旧回执配新章包必须拒绝（rc6 身份拆分，逐一核对）。"""
    from contracts.schema.receipt import ChapterReceipt
    from novel.stages.common import ChapterIdentities, check_chapter_receipt
    from contracts.schema.receipt import BODY_VALIDATOR_VERSION, BODY_MECHANICAL_CHECKS

    from contracts.schema.receipt import ChapterLengthDisclosure
    receipt = ChapterReceipt(
        contract_version='str06-rc17', chapter_ordinal=1, status='accepted',
        writer_view_sha256='a' * 64, validation_sha256='b' * 64,
        machine_view_sha256='e' * 64,
        prose_sha256='c' * 64, request_identity='r1',
        protocol_version='p1', renderer_version='r1', validator_version=BODY_VALIDATOR_VERSION,
        mechanical_checks=BODY_MECHANICAL_CHECKS, length=ChapterLengthDisclosure(
            char_count=100, target_chars=100, band_low=60, band_high=130,
            within_band=True, deviation_ratio=0.0))
    return check_chapter_receipt(
        receipt,
        identities=ChapterIdentities(writer_view_sha256='d' * 64,
                                     validation_sha256='b' * 64,
                                     machine_view_sha256='e' * 64),
        prose_sha256='c' * 64)


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
    'chapter_receipt_binding': Case('stage5', _receipt_stale, 'writer_view_sha256'),
    'hook_receipt_binding': Case('stage5', _hook_rejected_no_formal,
                                 '[withheld_literals]'),
    'body_design_compile': Case('stage4', _body_design_bad_wire, '必须是本书 char_id'),
    'chapter_plan_shape': Case('stage4', _chapter_count_mismatch, '章'),
    'length_budget_conserved': Case('stage4', _chapter_count_mismatch, '章'),
    'preserve_compile': Case('stage4', _preserve_bad_wire, 'obligation'),
    'binding_mechanical': Case('stage4', _binding_unclosed, 'checkpoint 外'),
    'hook_design_mechanical': Case('stage4', _hook_design_bad_wire, '材料引用不存在的拍'),
    'execution_plan_compile': Case('stage4', _plan_unclosed, 'Binding'),
    'chapter_closure_read_resolution': Case('stage4', _chapter_closure_unclosed,
                                            '未建立'),
    'writer_ir_dual_view': Case('stage4', _hook_context_unresolvable, '起笔事实'),
    'hook_context_rebuildable': Case('stage4', _hook_context_unresolvable, '起笔事实'),
    'delivery_replayable': Case('stage7', _delivery_not_ready, 'not ready'),
})


# ─── 质量改进批次（2026-09-26）新增 HARD 的最小会红样本 ───────────────────────

def _canonical_not_a_surface() -> list:
    """称谓闭合：canonical 不在本人 surfaces 里（book 观察闸，不过就走 owner patch）。"""
    from tests.test_str06_stage2_publish import EVIDENCE, _book_wire
    from novel.stages.stage2 import compile_book_observation
    wire = _book_wire(extra_characters={
        'ghost_yun': {'canonical': '陆夫人', 'surfaces': [
            {'surface': '云姑娘', 'source_refs': ['e01_02']}]}})
    _observation, errors = compile_book_observation(wire, evidence=EVIDENCE)
    return errors


def _hook_copy_source_identity() -> list:
    """hook_copy 进源身份：只改章前文字，旧候选根复校关闭式拒绝。"""
    import json
    import shutil
    import tempfile
    from pathlib import Path

    from contracts.artifacts import STAGE1_HOOK_COPY, STAGE1_MAIN_TEXT
    from contracts.schema import encode_artifact
    from novel.llm.ledger import CostLedger
    from novel.llm.paid_requests import (
        ProvenanceRefused, init_or_resume_candidate_workflow,
    )
    from novel.store import Str06Repository
    from tests.str06_facilities import make_context

    base = Path(tempfile.mkdtemp(prefix='hard-hook-'))
    try:
        book = base / '9001' / 'stage1'
        book.mkdir(parents=True)
        chapters = [{'chapter_ordinal': 1, 'text': '云娘提出约定。'},
                    {'chapter_ordinal': 2, 'text': '第二夜窗外有响动。'}]

        def write_stage1(hook: str) -> None:
            (book / 'main_text.json').write_bytes(
                json.dumps(encode_artifact(STAGE1_MAIN_TEXT, chapters),
                           ensure_ascii=False).encode('utf-8'))
            (book / 'hook_copy.json').write_bytes(
                json.dumps(encode_artifact(STAGE1_HOOK_COPY, {'text': hook}),
                           ensure_ascii=False).encode('utf-8'))

        from novel.stages.chain import load_stage1_chapters
        write_stage1('新婚夜，夫君坦白借种之约。')
        first = load_stage1_chapters(base, '9001')
        repo = Str06Repository(base / 'run')
        repo.attach_context(make_context(base / 'run', ledger=CostLedger(path=None)))
        init_or_resume_candidate_workflow(
            repository=repo, base=base, code='code-x',
            root_sha1=first.root_sha1, source_identity=first.source_identity,
            task_id='task-1', configured_model='fake', created_at=1759000000)
        # 只改引子：源身份换 ⇒ 旧根不得沿用已购结论
        write_stage1('改过的引子。')
        second = load_stage1_chapters(base, '9001')
        if second.source_identity == first.source_identity:
            return ['只改 hook_copy 但源身份未变：引子不在源身份内']
        try:
            init_or_resume_candidate_workflow(
                repository=repo, base=base, code='code-x',
                root_sha1=second.root_sha1, source_identity=second.source_identity,
                task_id='task-1', configured_model='fake', created_at=1759000001)
        except ProvenanceRefused as exc:
            return [str(exc)]
        return ['源身份变化后旧候选根仍被复用']
    finally:
        shutil.rmtree(base, ignore_errors=True)


def _design_surface_not_registered() -> list:
    """称谓闭合：Design 放行 Characters 未登记的字面。"""
    from novel.stages.stage4.design import assemble_body_design
    _body, errors = assemble_body_design(
        {**sample_design_expression(('yun',)), 'protocol_version': 'stage4-design-v5', 'book_style': '冷峻。',
         'initial_state': [], 'scenes': [{
            'state_reads': [],
            'driver': {'focal_actor': 'yun', 'immediate_want': 'w',
                       'opposition': 'o', 'turn': 't'},
            'realization_brief': '重心在约定成立；展开对峙，在立约处收束。',
            'beats': [{'event': '约定成立', 'participants': ['yun'],
                       'delivery_hint': '', 'target_delta': [],
                       'surface_introductions': [
                           {'char_id': 'yun', 'surfaces': ['云娘子']}]}],
            'delivery': {'pov': 'yun', 'voice': 'v', 'avoid': []}}],
         'chapters': [{'purpose': '立约', 'scenes': 1}], 'scene_weights': [1]},
        known_char_ids={'yun'}, registered_surfaces={'yun': {'云娘', '云姑娘'}}, expression_requirements=sample_requirements(), source_narration=sample_narration())
    return errors


def _delivery_chapter_split_mismatch() -> list:
    """章标记与分章身份：章文本与有效接受章集合不一致 ⇒ 显式未就绪。"""
    import hashlib

    from novel.stages.stage7.assembly import assemble_delivery, chapter_access
    hook, context_sha = '楔子。', 'c' * 64
    receipt = {'hook_context_sha256': context_sha,
               'prose_sha256': hashlib.sha256(hook.encode('utf-8')).hexdigest(),
               'request_identity': 'r1', 'validator_version': 'v1',
               'status': 'accepted', 'mechanical_checks': ('non_empty',),
               'semantic': 'unverified'}
    outcome = assemble_delivery(
        chapter_prose={1: '第一章。', 2: '第二章。'},
        body=chapter_access([1], {1: {'status': 'accepted'}}),
        hook_prose=hook, hook_receipt=receipt, hook_context_sha256=context_sha,
        replacements={}, kept_literals=())
    if outcome.status == 'ready':
        return ['章文本与接受章不一致仍组装就绪']
    return [outcome.reason]


CASES.update({
    'source_canonical_surface': Case(
        'stage2', _canonical_not_a_surface, '不是其 surface 之一'),
    'source_identity_covers_hook_copy': Case(
        'stage2', _hook_copy_source_identity, '源输入身份'),
    'design_surface_release_closure': Case(
        'stage4', _design_surface_not_registered, '未登记面'),
    'delivery_chapter_markers': Case(
        'stage7', _delivery_chapter_split_mismatch, 'body_chapter_mismatch'),
})


# ─── stage2 既有 HARD 的同批红例（扫描面纳入 stage2 时补齐） ──────────────────
#
# 【B5（S-05）】红例全部换轨五 owner 引擎（旧联合抽取 compile_book／ChapterCompiler
# 已删）；共用底座取 `tests/test_str06_stage2_publish.py` 的绿观察 wires。

def _stage2_outcome(*, book=None, chapters=None, names=None, associate=None):
    """四份观察 → `compile_source_truth`（未决项在 `outcome.undecided`）。"""
    from tests.test_str06_stage2_publish import (
        EVIDENCE, _associate_wire, _book_wire, _chapter_wires, _names_structure,
    )
    from novel.stages.stage2 import (
        AssociateObservation, BookObservation, ChapterObservation,
        NamesObservation, compile_source_truth,
    )
    ch_wires = chapters if chapters is not None else _chapter_wires()
    return compile_source_truth(
        book=BookObservation.model_validate(
            book if book is not None else _book_wire()),
        chapters={no: ChapterObservation.model_validate(ch_wires[f'ch{no:02d}'])
                  for no in range(1, len(ch_wires) + 1)},
        names=NamesObservation.model_validate({
            'protocol_version': 'stage2-names-v4',
            'structure': names if names is not None else _names_structure()}),
        associate=AssociateObservation.model_validate(
            associate if associate is not None else _associate_wire()),
        evidence=EVIDENCE)


def _book_extraction_dangling() -> list:
    """book 观察编译闭合：surface 的 source_ref 悬空（句段闭集之外的 id）。"""
    from tests.test_str06_stage2_publish import EVIDENCE, _book_wire
    from novel.stages.stage2 import compile_book_observation
    wire = _book_wire(extra_characters={
        'fu_jun': {'canonical': '夫君', 'surfaces': [
            {'surface': '夫君', 'source_refs': ['e99_99']}]}})
    _observation, errors = compile_book_observation(wire, evidence=EVIDENCE)
    return errors


def _chapter_ordinals_not_dense() -> list:
    """源章数组阅读序 1..N 密集：断号由 Stage1 MainText 模型自身拒绝。"""
    from pydantic import ValidationError
    from contracts.schema.stage1 import MainText
    try:
        MainText.model_validate([
            {'chapter_ordinal': 1, 'text': '一。'},
            {'chapter_ordinal': 3, 'text': '三。'}])
    except ValidationError as exc:
        return [str(exc)]
    return ['断号章序被接受']


def _chapter_blueprint_bad_wire() -> list:
    """章观察编译闭合：participants 引用书级人物账之外的人物（闭集归 compile，R-2.3）。"""
    from tests.test_str06_stage2_publish import _chapter_wires
    outcome = _stage2_outcome(chapters={**_chapter_wires(), 'ch01': {
        'protocol_version': 'stage2-chapters-v7', 'beats': [
            {'event': '开篇', 'participants': ['ghost'],
             'source_refs': ['e01_01'], 'scene_boundary': 'new',
             'temporal_mode': 'single', 'effects': [], 'cognition': [],
             'loops': []}]}})
    return outcome.errors + outcome.undecided


def _fold_loop_close_without_open() -> list:
    """悬念 close 没有本章 open 也没有开合配对：保持未决（fold 不会见到不一致 canonical）。"""
    from tests.test_str06_stage2_publish import _associate_wire
    outcome = _stage2_outcome(
        associate={**_associate_wire(), 'loop_pairing': []})
    return outcome.errors + outcome.undecided


def _loop_reopened() -> list:
    """R-2.11：同一悬念句柄本章内重复 open（章观察 owner 的自含纪律）。"""
    from tests.test_str06_stage2_publish import EVIDENCE
    from novel.stages.stage2 import compile_chapter_observation
    wire = {'protocol_version': 'stage2-chapters-v7', 'beats': [
        {'event': '开悬念', 'participants': [], 'source_refs': ['e01_01'],
         'scene_boundary': 'new', 'temporal_mode': 'single', 'effects': [],
         'cognition': [],
         'loops': [{'ref': 'q_x', 'op': 'open', 'question': '谁在暗中窥伺'}]},
        {'event': '再开同一悬念', 'participants': [], 'source_refs': ['e01_02'],
         'scene_boundary': 'continue', 'temporal_mode': 'single', 'effects': [],
         'cognition': [],
         'loops': [{'ref': 'q_x', 'op': 'open', 'question': '改写后的问题'}]},
    ]}
    _observation, errors = compile_chapter_observation(
        wire, evidence_ids=frozenset(EVIDENCE))
    return errors


def _proposition_dangling() -> list:
    """R-2.12：跨章命题引用没有配对——保持未决，不猜（D8）。"""
    from tests.test_str06_stage2_publish import _associate_wire
    outcome = _stage2_outcome(
        associate={**_associate_wire(), 'same_proposition': []})
    return outcome.errors + outcome.undecided


def _registered_key_wrong_scope() -> list:
    """R-2.14／CS-S Phase 3：表外变化键（含退役键 whereabouts 一类）即拒。

    键入 `CHANGE_KEYS` 闭集——退役键拒识并入闭集拒识（语义细节由 val 承载，
    不自造新键）；这一条红例守的是闭集与指针。
    """
    from tests.test_str06_stage2_publish import EVIDENCE
    from novel.stages.stage2 import compile_chapter_observation
    wire = {'protocol_version': 'stage2-chapters-v7', 'beats': [
        {'event': '旧位置键借道', 'participants': [], 'source_refs': ['e01_01'],
         'scene_boundary': 'new', 'temporal_mode': 'single',
         'effects': [{'target': {'kind': 'character', 'char_id': 'fu_jun'},
                      'key': 'whereabouts', 'op': 'set',
                      'value': '陆家卧房内，藏于床底',
                      'modality': 'actual',
                      'basis': [{'segment': 'e01_01', 'quote': '夫君藏进床底'}]}],
         'cognition': [], 'loops': []},
    ]}
    _observation, errors = compile_chapter_observation(
        wire, evidence_ids=EVIDENCE)
    return errors


def _surface_anchor_without_literal() -> list:
    """R-2.10：称谓的证据锚点段不含该字面（from_chapter 的派生失去依据）。"""
    from tests.test_str06_stage2_publish import _book_wire
    from novel.stages.stage2 import compile_book_observation
    wire = _book_wire(extra_characters={
        'fu_jun': {'canonical': '陆大哥', 'surfaces': [
            {'surface': '陆大哥', 'source_refs': ['e01_01']}]}})
    _observation, errors = compile_book_observation(wire, evidence={
        'e01_01': {'chapter': 1, 'text': '本章根本没出现这个称呼。'}})
    return errors


def _names_structure_uncovered() -> list:
    """名称结构编译闭合：form_overrides 指向未登记 surface（CS-S 1c 的 v6 面
    ——构词模板由程序派生并恰好覆盖，私自加称谓面即拒，A/S2-10）。"""
    from contracts.schema.source import (
        CharacterEntry, CharactersCore, SurfaceWindow,
    )
    from novel.stages.stage2.names import compile_name_structure
    core = CharactersCore(characters={'ch1': CharacterEntry(
        canonical='夫君',
        surfaces=(SurfaceWindow(surface='夫君', from_chapter=1,
                                source_refs=('e01_01',)),))})
    _structure, errors = compile_name_structure(
        {'protocol_version': 'stage2-names-v6',
         'families': {'fam_lu': {'literal': '陆', 'forms': []}},
         'people': {'ch1': {'family_id': 'fam_lu', 'given': None, 'aliases': [],
                            'form_overrides': {'陆大哥': None}}}},
        core, '夫君与陆家大哥夜话。')
    return errors


def _names_coverage_missing_entry() -> list:
    """应改名集合覆盖源名称结构：NameMap 漏项即拒。"""
    from tests.test_str06_naming import _chars
    from novel.stages.stage7.naming import compile_name_map
    _map, errors = compile_name_map(
        {'entries': [{'char_ref': 'chen_yan', 'name': {'family': '沈', 'given': '砚'}}]},
        _chars())
    return errors


CASES.update({
    'source_book_observation': Case('stage2', _book_extraction_dangling, '闭集之外'),
    'source_chapter_dense_ordinals': Case(
        'stage2', _chapter_ordinals_not_dense, 'chapter_ordinal'),
    'source_chapter_blueprint': Case(
        'stage2', _chapter_blueprint_bad_wire, '未知人物'),
    'source_fold_consistent': Case(
        'stage2', _fold_loop_close_without_open, '没有开合配对'),
    'source_loop_lifecycle': Case('stage2', _loop_reopened, '重复 open'),
    'source_proposition_closure': Case(
        'stage2', _proposition_dangling, '悬空的跨章引用'),
    'source_registered_key_scope': Case(
        'stage2', _registered_key_wrong_scope, '变化键闭集'),
    'source_surface_anchor_literal': Case(
        'stage2', _surface_anchor_without_literal, '锚点不含该字面'),
    'names_structure_compile': Case(
        'stage2', _names_structure_uncovered, 'form_overrides 指向未登记 surface'),
    'names_coverage': Case('stage2', _names_coverage_missing_entry, '漏项'),
})


# ── R10a 关键语义交接（收口批登记）：每条 HARD 的最小会红样本 ─────────────────
# 绿对照在反例套件：`tests/test_str06_a1_semantics.py`（引文/时态正例）、
# `test_str06_a2_preserve.py`（锚定/series 正例）、`test_str06_a3_conflicts.py`
# （三分支恢复）、`test_str06_a4_design_locks.py`（锁规则绿基座）、
# `test_str06_a5_positions.py`（series 分别承载、P2/P5 位置正例）。

def _source_quote_off_literal() -> list:
    """R-2.18：basis 引文与句段原文不符（C4 引文错位；逐字判定，不归一化）。"""
    from novel.stages.stage2.resolver import EvidenceResolver

    resolver = EvidenceResolver(
        {'e01_01': {'chapter': 1, 'text': '她攥紧了帕子，指节发白。'}})
    issues = resolver.verify_quote('e01_01', '他攥紧了帕子', ('e01_01',))
    return [issue.message for issue in issues]


def _source_effect_modality_off() -> list:
    """R-2.19：Effect.modality 闭集外（'大概'）在观察构造期即拒。"""
    from contracts.schema.source import ChapterBlueprint
    from tests.str06_facilities import v13_beat, v13_effect

    try:
        ChapterBlueprint(chapter=1, beats=(
            v13_beat('b01_01', 'e', effects=(
                v13_effect({'kind': 'world'}, 'social_standing', '多舛',
                           modality='大概'),)),),
            scenes=({'scene_id': 'sc01', 'beat_ids': ('b01_01',)},),
            causal_edges=())
    except Exception as exc:
        return [f'effect modality 闭集外被拒：{exc}']
    return []


def _preserve_unknown_anchor() -> list:
    """R-5.15：义务 anchors 引用投影句柄闭集之外（每义务须锚定源原子）。"""
    from novel.stages.stage4.grounding import assemble_preserve

    from contracts.schema.source import (
        BlueprintsArtifactV17, ChapterBlueprint, CharacterEntry,
        CharactersCore, SurfaceWindow,
    )
    from tests.str06_facilities import v13_beat, v13_effect
    _checkpoint, errors = assemble_preserve(
        {'protocol_version': 'stage4-preserve-v5', 'obligations': [
            {'kind': 'event_core', 'statement': 's', 'anchors': ['b09_99.e1'],
             'must_preserve': True, 'satisfaction': '明确呈现', 'requires': []}]},
        policy_id='preserve-default', policy_version=1,
        characters=CharactersCore(characters={'ch1': CharacterEntry(
            canonical='苏姑娘',
            surfaces=(SurfaceWindow(surface='苏姑娘', from_chapter=1,
                                    source_refs=('e01_01',)),))}),
        blueprint=BlueprintsArtifactV17(source_expression=sample_blueprint_expression(), chapters=(
            ChapterBlueprint(chapter=1,
                             beats=(v13_beat('b01_01', 'e', effects=(
                                 v13_effect({'kind': 'world'}, 'social_standing', '多舛'),)),),
                             scenes=({'scene_id': 'sc01', 'beat_ids': ('b01_01',)},),
                             causal_edges=()),),
            evidence={'e01_01': '苏姑娘初到陆家。'}),
        provider='p', model='m', request_sha256='a' * 64)
    return errors


def _conflict_anchor_unroutable() -> list:
    """R-5.16：证据冲突锚点不可解析 → 机械路由显式停（不猜回派目标）。"""
    from contracts.schema.workflow import route_owner_issue

    action, why = route_owner_issue('source_evidence_conflict',
                                    conflict_anchor='zzz')
    if action != 'stop':
        return []
    return [f'{action}：{why}']


def _design_lock_mode_escalation() -> list:
    """R-5.17：仅 suspects 锁的命题被写成 known（C1 模式越权，known 只能
    来自 known 锁）。"""
    from tests.test_str06_a4_design_locks import _assemble, _brief, _design_wire

    brief = _brief()
    kp_y = next(p.kp_id for p in brief.propositions
                if p.statement == '婆母似乎知情')
    wire = _design_wire(brief)
    wire['scenes'][0]['beats'][2]['target_delta'].append(
        {'domain': 'knowledge', 'subjects': [], 'holder': 'yun_niang',
         'key': kp_y, 'op': 'set', 'value': '她确知婆母知情',
         'scope': 'book', 'knowledge_mode': 'known'})
    _body, errors = _assemble(wire, brief)
    return errors


def _binding_series_same_beat() -> list:
    """R-5.18：series 成员接受拍相同（C3/C5 的承载面，离散兑现被压成一次）。"""
    from tests.test_str06_a5_positions import (
        _placements, _checkpoint, _fact_marks, _green_graph_and_binding,
    )
    from novel.stages.stage4.grounding import derive_semantic_binding

    body, _binding, brief = _green_graph_and_binding()
    _model, errors = derive_semantic_binding(
        _placements(ob04=[(0, 4)]), body, _checkpoint(), _fact_marks(brief))
    return errors


def _binding_series_summary_early_or_unbound() -> list:
    """R-5.23：显式概括早回顾／遗漏可选被覆实例；沿真实Design与compile入口。"""
    from tests.test_str06_a5_positions import _assemble_summary_fixture
    from tests.test_str06_body import _green_design, _series_binding
    from novel.stages.stage4.compile import compile_execution_plan
    _wire, _body, design_errors = _assemble_summary_fixture({
        'ob01': [(0, 0)], 'ob02': [(1, 0)], 'ob03': [(0, 1)]})
    body = _green_design()
    binding = _series_binding(body, {
        'ob01': [(0, 0)], 'ob03': [(1, 0)]}, optional_instance=True)
    _plan, compile_errors = compile_execution_plan(body, binding)
    return [*design_errors, *compile_errors]


def _design_detail_ref_outside() -> list:
    """R-5.24：内部人工恢复输入引用Brief未登记细节，真实Design组装必须拒。"""
    from novel.stages.stage4.design import assemble_body_design
    from tests.test_str06_a5_positions import _summary_design_fixture
    from tests.test_str06_hook import _body_wire
    _wire, brief, _index = _summary_design_fixture({})
    wire = _body_wire()
    wire['scenes'][0]['beats'][0]['detail_refs'] = ['dt_' + '2' * 16]
    _body, errors = assemble_body_design(
        wire, known_char_ids={'fu_jun', 'yun_niang'},
        expression_requirements=brief.expression, source_narration=brief.source_narration,
        brief=brief)
    return errors


def _epistemic_lock_early_upgrade() -> list:
    """R-5.19：读者确认早于授权锁承载位（C6 位置面，披露顺序前移）。"""
    from tests.test_str06_a5_positions import (
        _placements, _checkpoint, _green_graph_and_binding,
    )
    from novel.stages.stage4.grounding import derive_semantic_binding
    from novel.stages.stage4.guard import check_epistemic_lock_order

    body, binding, _brief = _green_graph_and_binding(
        placements=_placements(ob02=[(0, 4)]))
    assert binding is not None
    return check_epistemic_lock_order(body, binding)


def _writer_packet_background_narrowed() -> list:
    """R-6.16：手工 IR 的背景含无消费者拍状态与 unset → 判据报出（C7/C10）；
    init 立足点与仍被消费的状态保留（绿对照在 r10b 套件）。"""
    from contracts.schema.state import EffectDomain
    from contracts.schema.context import StateEntry
    from novel.stages.common import background_narrowing_violations

    def _entry(key: str, status: str = 'set', anchor: str = 'sc01#b03') -> StateEntry:
        return StateEntry(domain=EffectDomain.CHARACTER, key=key,
                          subjects=('yun_niang',), value='值', status=status,
                          established_by=anchor)

    return background_narrowing_violations(
        (_entry('night_sewing'), _entry('chill', status='unset'),
         _entry('whereabouts'), _entry('residence', anchor='init')),
        (), scene_id='sc03', scene_index=2,
        consumers={('character', ('yun_niang',), '', 'whereabouts'): (3,)},
        scene_of={'sc01': 0, 'sc02': 1, 'sc03': 2})


def _answer_reuse_identity_follows_material() -> list:
    """R-5.20 的机械面：请求身份随实际消费材料变化——材料不同身份必不同
    （否则同身份缓存会命中重放上一轮带冲突处理的旧回答）；纯审计字段
    （时间戳等）不进 prompt、不换身份。

    CS-H H1b（第三条件改写）另测免费沿用的判定面：身份已变但上游条目未变且
    当前输入下预检全绿 → 零调用采用；没有可用候选或协议不兼容 → 只按闭集原因
    整份新购并留痕。判定入口是 `novel.stages.candidates.plan_reuse`（纯函数）。
    """
    from novel.llm.paid_requests import digest
    from novel.stages.candidates import (
        CandidateAssessment, UpstreamDiff, plan_reuse,
    )

    first, second = digest('材料 A（含冲突处理结果）'), digest('材料 B')
    findings = []
    if first == second:
        findings.append('材料不同而请求身份相同：会错误复用旧回答')
    else:
        findings.append('请求身份随实际消费材料变化：材料 A/B 身份不同')
    empty_diff = UpstreamDiff((), (), '0' * 64)
    green = CandidateAssessment(entry='green', ordinal=1, compatible=True,
                                preflight_passed=True, root_causes=0, diff=empty_diff)
    failing = CandidateAssessment(entry='failing', ordinal=2, compatible=True,
                                  preflight_passed=False, root_causes=3, diff=empty_diff)
    adopted = plan_reuse([failing, green])
    if adopted.action != 'carry_forward' or adopted.candidate != 'green':
        findings.append(f'可零调用沿用的候选被判成 {adopted.action}')
    else:
        findings.append('上游无差异且预检全绿：零调用采用已购候选（carried_forward）')
    blocked = plan_reuse([CandidateAssessment(entry='x', ordinal=1, compatible=False,
                                              incompatibility_reason='协议字面量不兼容')])
    if blocked.action != 'full_purchase' or blocked.reason != '协议字面量不兼容':
        findings.append('不兼容候选没有按闭集原因整份新购')
    else:
        findings.append('协议不兼容：按闭集原因整份新购并留痕')
    return findings


CASES.update({
    'source_basis_quote_literal': Case(
        'stage2', _source_quote_off_literal, '不逐字存在'),
    'source_effect_modality': Case(
        'stage2', _source_effect_modality_off, 'modality 闭集外被拒'),
    'preserve_anchor_closure': Case(
        'stage4', _preserve_unknown_anchor, '未知句柄'),
    'source_evidence_conflict_routing': Case(
        'stage4', _conflict_anchor_unroutable, '不可解析'),
    'design_lock_acceptance': Case(
        'stage4', _design_lock_mode_escalation, '无锁或模式越权'),
    'binding_series_collapse': Case(
        'stage4', _binding_series_same_beat, 'series_collapsed'),
    'binding_series_summary': Case(
        'stage4', _binding_series_summary_early_or_unbound, '系列概括'),
    'design_detail_ref_closure': Case(
        'stage4', _design_detail_ref_outside, '不在正式 Brief 细节闭集内'),
    'epistemic_lock_order_guard': Case(
        'stage4', _epistemic_lock_early_upgrade, 'epistemic_lock_order'),
    'answer_reuse_four_conditions': Case(
        'stage4', _answer_reuse_identity_follows_material, '身份不同'),
    'writer_packet_background_narrowing': Case(
        'stage4', _writer_packet_background_narrowed, '违反收窄规则'),
})
