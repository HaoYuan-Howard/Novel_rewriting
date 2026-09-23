#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""test_str06_contracts —— STR-06 F0 RC 契约的定向 contract 测试。

【验证边界】只验证 F0 落盘的契约形状与构造期不变量：必保满足、source-blind、
完整 Binding、双视图同源、回执最小面、路径家族准入、RC 隔离（不进现役
ARTIFACT_SPECS / NODES）。跨产物不变量（分母一致、DAG 无环、established_by
时序）与行为语义归 F1 反例与 F4 Guard，不在本文件伪造覆盖。
"""
from __future__ import annotations

import ast
from pathlib import Path

import pytest
from pydantic import ValidationError

from contracts.schema import ARTIFACT_SPECS
from contracts.schema.str06 import (
    STR06_CONSUMPTION_PROJECTIONS,
    STR06_DEPENDENCY_RULES,
    STR06_PATH_FAMILIES,
    STR06_TWO_LEVEL_PATHS,
    BindingEntry,
    ChapterAssignment,
    DependencyEdge,
    Establishment,
    ExecutionSlice,
    FailureOrigin,
    FidelityVerdict,
    IdentityProjection,
    OutputConstraints,
    PrepareMaterials,
    PreserveCheckpoint,
    PreserveKind,
    PreservationPolicy,
    ReconstructionBrief,
    SceneReceipt,
    SemanticBinding,
    SourceHookEvidence,
    SourceLineage,
    SourceObligation,
    TargetOpening,
    UnboundEntry,
    WriterContext,
    WriterBrief,
    VerificationIR,
)
from contracts.schema.str06.grounding import EventCoreNote
from contracts.schema.str06.design import (
    PresentationGraph, SceneDelivery, SceneDriver, TargetBeat, TargetScene,
)
from contracts.schema.str06.execution import Reinforcement
from contracts.schema.str06.receipt import FidelityFinding
from novel.errors import PathRefused
from novel.paths import check_rel, check_rel_family, family_input_key

H64 = 'a' * 64


def _obligation(**over) -> dict:
    base = dict(
        obligation_id='ob1', kind=PreserveKind.EVENT_CORE, statement='借种之约成立',
        lineage=SourceLineage(blueprint_refs=('b01_01',), character_ids=()),
        must_preserve=True, satisfaction='正文呈现该约定被提出且被接受',
    )
    base.update(over)
    return base


def _checkpoint(**over) -> dict:
    base = dict(
        checkpoint_id='cp1', policy_id='preserve-default', policy_version=1,
        characters_sha256=H64, blueprints_sha256=H64, provider='deepseek',
        model='flash', request_sha256=H64,
        obligations=(SourceObligation(**_obligation()),), checkpoint_sha256=H64,
    )
    base.update(over)
    return base


def _brief(**over) -> dict:
    base = dict(
        brief_id='br1', preserve_checkpoint_sha256=H64,
        event_cores=(EventCoreNote(core_id='c1', summary='约定成立', participants=('ch1',)),),
    )
    base.update(over)
    return base


def _binding(**over) -> dict:
    base = dict(
        binding_id='bd1', preserve_checkpoint_sha256=H64,
        entries=(BindingEntry(obligation_id='ob1', scene_id='s01'),),
        unbound=(),
    )
    base.update(over)
    return base


# ───────────────────────── Policy ─────────────────────────

def test_policy_needs_explicit_version_and_kinds():
    assert PreservationPolicy(
        policy_id='preserve-default', version=1,
        preserve_kinds=(PreserveKind.EVENT_CORE,)).version == 1
    with pytest.raises(ValidationError):
        PreservationPolicy(policy_id='p', version=0, preserve_kinds=(PreserveKind.REVEAL,))
    with pytest.raises(ValidationError):
        PreservationPolicy(policy_id='p', version=1, preserve_kinds=())


# ───────────────────────── Preserve ─────────────────────────

def test_must_preserve_requires_satisfaction():
    with pytest.raises(ValidationError, match='实质满足条件'):
        SourceObligation(**_obligation(satisfaction=''))
    # 可综合类允许无 satisfaction，但 lineage 仍必填
    SourceObligation(**_obligation(must_preserve=False, satisfaction=''))
    with pytest.raises(ValidationError):
        SourceObligation(**_obligation(lineage=SourceLineage(blueprint_refs=(), character_ids=())))


def test_preserve_checkpoint_isolated_from_design_and_bind():
    PreserveCheckpoint(**_checkpoint())
    with pytest.raises(ValidationError):
        # Preserve 不接收 Design 或 Bind 的失败/结果信息：混入即拒（S-04 的一面）
        PreserveCheckpoint(**_checkpoint(design_feedback='retry'))
    with pytest.raises(ValidationError):
        PreserveCheckpoint(**_checkpoint(bind_failures=['x']))
    with pytest.raises(ValidationError):
        # obligation_id 重复 = 稳定 ID 被破坏（I-31）
        PreserveCheckpoint(**_checkpoint(obligations=(
            SourceObligation(**_obligation()),
            SourceObligation(**_obligation()),
        )))


def test_reconstruction_brief_is_source_blind():
    ReconstructionBrief(**_brief())
    for smuggled in ('source_chapter', 'source_beat_ref', 'evidence_ref',
                     'source_intent_ref', 'narration_order'):
        with pytest.raises(ValidationError):
            ReconstructionBrief(**_brief(**{smuggled: 'ch07'}))
    with pytest.raises(ValidationError):
        ReconstructionBrief(**_brief(event_cores=(
            EventCoreNote(core_id='c1', summary='a'),
            EventCoreNote(core_id='c1', summary='b'),
        )))


def test_identity_projection_is_id_only():
    IdentityProjection(char_id='ch1', identity_statement='裴书远，主角', stable_constraints=())
    with pytest.raises(ValidationError):
        # 身份投影不带源坐标：first_mention_chapter 属于 Characters 源事实
        IdentityProjection(char_id='ch1', identity_statement='x',
                           first_mention_chapter=3)


# ───────────────────────── Bind ─────────────────────────

def test_semantic_binding_is_complete_snapshot():
    SemanticBinding(**_binding())
    # 半份/草稿状态不存在：partial 混入即拒
    with pytest.raises(ValidationError):
        SemanticBinding(**_binding(partial=True))
    # unbound 保留义务并返回 owner——Bind 不得删义务迁就 Design（删掉 = 分母缺失，
    # 由 F4 Guard 对照 checkpoint 查；这里锁「同一条不得两边都出现」）
    with pytest.raises(ValidationError):
        SemanticBinding(**_binding(unbound=(UnboundEntry(
            obligation_id='ob1', owner='design', reason='x'),)))
    with pytest.raises(ValidationError):
        SemanticBinding(**_binding(entries=(
            BindingEntry(obligation_id='ob1', scene_id='s01'),
            BindingEntry(obligation_id='ob1', scene_id='s02'),
        )))


def test_binding_beat_ref_must_stay_inside_scene():
    BindingEntry(obligation_id='ob1', scene_id='s01', beat_ref='s01#bt1')
    with pytest.raises(ValidationError):
        BindingEntry(obligation_id='ob1', scene_id='s01', beat_ref='s02#bt1')


# ───────────────────────── Design（source-blind + 归章自洽） ─────────────────────────

def _scene(scene_id: str) -> TargetScene:
    return TargetScene(
        scene_id=scene_id,
        driver=SceneDriver(focal_actor='ch1', immediate_want='退婚', opposition='家族', turn='翻盘'),
        beats=(TargetBeat(beat_id='bt1', event='提亲', participants=('ch1',),
                          dramatic_intent='立誓', target_delta=()),),
        delivery=SceneDelivery(pov='ch1', voice='冷峻'),
    )


def _graph(**over) -> dict:
    base = dict(
        graph_id='g1',
        scenes=(_scene('s01'), _scene('s02')),
        chapters=(ChapterAssignment(chapter_ordinal=1, scene_ids=('s01',)),
                  ChapterAssignment(chapter_ordinal=2, scene_ids=('s02',))),
    )
    base.update(over)
    return base


def test_presentation_graph_source_blind():
    PresentationGraph(**_graph())
    for smuggled in ('source_ref', 'source_beat_ref', 'evidence_ref', 'source_intent_ref'):
        with pytest.raises(ValidationError):
            PresentationGraph(**{**_graph(), smuggled: 'b07_01'})


def test_presentation_graph_chapter_invariants():
    PresentationGraph(**_graph())
    # 章号必须从 1 连续
    with pytest.raises(ValidationError):
        PresentationGraph(**_graph(chapters=(
            ChapterAssignment(chapter_ordinal=2, scene_ids=('s01',)),
            ChapterAssignment(chapter_ordinal=3, scene_ids=('s02',)))))
    # 场景必须全部归章
    with pytest.raises(ValidationError, match='未归章'):
        PresentationGraph(**_graph(chapters=(ChapterAssignment(chapter_ordinal=1, scene_ids=('s01',)),)))
    # 归章引用不存在的场景
    with pytest.raises(ValidationError, match='不存在'):
        PresentationGraph(**_graph(chapters=(
            ChapterAssignment(chapter_ordinal=1, scene_ids=('s01',)),
            ChapterAssignment(chapter_ordinal=2, scene_ids=('s02', 'sX')),)))
    # 同一场景归两章
    with pytest.raises(ValidationError, match='多章'):
        PresentationGraph(**_graph(chapters=(
            ChapterAssignment(chapter_ordinal=1, scene_ids=('s01',)),
            ChapterAssignment(chapter_ordinal=2, scene_ids=('s01',)))))
    # 权重引用不存在的场景
    with pytest.raises(ValidationError, match='chapter_weights'):
        PresentationGraph(**_graph(chapter_weights={'sX': 1.0}))


# ───────────────────────── Execution ─────────────────────────

def test_reinforcement_cannot_carry_obligations():
    # 0..N 强化不稀释必保：类型上没有 obligation 通道
    assert 'obligation_id' not in Reinforcement.model_fields
    assert 'must_preserve' not in Reinforcement.model_fields
    with pytest.raises(ValidationError):
        Reinforcement(target_key='k', obligation_id='ob1')


def test_dependency_edge_rejects_self_loop():
    with pytest.raises(ValidationError):
        DependencyEdge(before='s01', after='s01', kind='reveal_after')


def test_establishment_scope_and_domain_closed():
    Establishment(domain='character', key='whereabouts', value='陆家', scope='scene')
    with pytest.raises(ValidationError):
        Establishment(domain='character', key='k', value='v', scope='act')
    from contracts.schema.str06.execution import EffectDomain
    assert {d.value for d in EffectDomain} == {
        'character', 'relation', 'knowledge', 'world', 'reader'}


# ───────────────────────── WriterContext 双视图 ─────────────────────────

def _ir(scene_id: str) -> VerificationIR:
    return VerificationIR(scene_id=scene_id, obligations=('ob1',))


def test_writer_context_views_must_agree():
    WriterContext(
        scene_id='s01', attempt_id='a1', execution_slice_sha256=H64,
        verification_ir=_ir('s01'),
        writer_brief=WriterBrief(scene_id='s01', task='写', scene_driver_summary='驱动',
                                 beat_outlines=('拍1',), length_budget=800, voice='冷峻'))
    with pytest.raises(ValidationError, match='同源'):
        WriterContext(
            scene_id='s01', attempt_id='a1', execution_slice_sha256=H64,
            verification_ir=_ir('s02'),
            writer_brief=WriterBrief(scene_id='s01', task='写', scene_driver_summary='驱动',
                                     beat_outlines=('拍1',), length_budget=800, voice='冷峻'))


def test_execution_slice_fields():
    ExecutionSlice(scene_id='s01', consumed_obligation_ids=('ob1',),
                   entry_state_sha256=H64, entry_knowledge_sha256=H64,
                   execution_slice_sha256=H64)
    with pytest.raises(ValidationError):
        ExecutionSlice(scene_id='s01', consumed_obligation_ids=(),
                       entry_state_sha256='short', entry_knowledge_sha256=H64,
                       execution_slice_sha256=H64)


# ───────────────────────── Receipt ─────────────────────────

def test_scene_receipt_is_minimal_seven_fields():
    assert set(SceneReceipt.model_fields) == {
        'scene_id', 'attempt_id', 'prose_sha256', 'writer_context_sha256',
        'execution_slice_sha256', 'judge_contract_version', 'status'}
    receipt = SceneReceipt(
        scene_id='s01', attempt_id='a1', prose_sha256=H64, writer_context_sha256=H64,
        execution_slice_sha256=H64, judge_contract_version='str06-rc1', status='accepted')
    assert receipt.status.value == 'accepted'
    # 无关 metadata 不作硬匹配条件：类型上就不存在
    with pytest.raises(ValidationError):
        SceneReceipt(**{**receipt.model_dump(), 'book_plan_sha256': H64})
    with pytest.raises(ValidationError):
        SceneReceipt(**{**receipt.model_dump(), 'audited_at': '2026-09-23'})
    # 大写/非十六进制 hash 是「看似绑定、对不上账」的形态
    with pytest.raises(ValidationError):
        SceneReceipt(**{**receipt.model_dump(), 'prose_sha256': 'A' * 64})


def test_fidelity_verdict_only_diagnoses():
    assert {o.value for o in FailureOrigin} == {'writer', 'upstream_ir', 'unverifiable'}
    receipt = SceneReceipt(
        scene_id='s01', attempt_id='a1', prose_sha256=H64, writer_context_sha256=H64,
        execution_slice_sha256=H64, judge_contract_version='str06-rc1', status='rejected')
    FidelityVerdict(receipt=receipt, findings=(
        FidelityFinding(code='time_precision', scene_id='s01', detail='当天→次日',
                        origin='writer'),))
    with pytest.raises(ValidationError):
        FidelityVerdict(receipt=receipt, findings=(
            FidelityFinding(code='x', scene_id='s02', detail='y', origin='writer'),))
    with pytest.raises(ValidationError):
        # Judge 不编辑 truth：判定上没有修复通道
        FidelityVerdict(**{**FidelityVerdict(receipt=receipt).model_dump(), 'applied_fix': 'z'})


# ───────────────────────── 消费投影与 Prepare 坐标分离 ─────────────────────────

def test_consumption_projection_registry_complete():
    assert set(STR06_CONSUMPTION_PROJECTIONS) == {
        'world_view', 'character_exposure', 'chapter_map', 'body_access', 'rename_materials'}
    for spec in STR06_CONSUMPTION_PROJECTIONS.values():
        assert spec.source_owners
        assert spec.coordinate.value in ('source', 'target', 'dual')


def test_prepare_materials_two_coordinates():
    # 源坐标材料与目标坐标材料分别表达；target 未就绪是显式 None，不是缺省成功
    PrepareMaterials(
        source_evidence=SourceHookEvidence(
            through_chapter=3, hook_facts=('f',), source_evidence_refs=('s://ch1',)),
        target_opening=None)
    PrepareMaterials(
        source_evidence=SourceHookEvidence(
            through_chapter=3, hook_facts=('f',), source_evidence_refs=('s://ch1',)),
        target_opening=TargetOpening(opening_scene_ids=('s01',), opening_note='首场'))


# ───────────────────────── 路径家族 ─────────────────────────

def test_path_families_unique_prefixes():
    prefixes = [f.prefix for f in STR06_PATH_FAMILIES.values()]
    assert len(prefixes) == len(set(prefixes))
    assert all(p.endswith('/') for p in prefixes)
    # 两级特例不在三级家族前缀内（走现役两级准入）
    assert not any(t.startswith(p) for p in prefixes for t in STR06_TWO_LEVEL_PATHS)


def test_check_rel_family_admits_registered_and_refuses_rest():
    assert check_rel_family('stage4/grounding/semantic_binding.json')
    assert check_rel_family('stage2/characters/characters.json')
    assert check_rel_family('runtime/continuity.json')
    with pytest.raises(PathRefused):
        check_rel_family('stage9/other/file.json')
    with pytest.raises(PathRefused):
        check_rel_family('stage4/other/file.json')
    with pytest.raises(PathRefused):
        check_rel_family('stage4/design/../../escape.json')
    with pytest.raises(PathRefused):
        check_rel_family('E:/abs/path.json')


def test_legacy_two_level_rule_unchanged():
    # 两级一般规则一寸不放：未走家族入口的三级路径仍然拒绝
    with pytest.raises(PathRefused):
        check_rel('stage4/design/presentation_graph.json')
    assert check_rel('stage4/writer_context.json') == 'stage4/writer_context.json'


def test_family_input_key_is_full_path():
    assert family_input_key('stage4/grounding/semantic_binding.json') \
        == 'stage4/grounding/semantic_binding.json'
    with pytest.raises(PathRefused):
        family_input_key('stage4/not_a_family/x.json')


# ───────────────────────── 依赖登记与 RC 隔离 ─────────────────────────

def test_dependency_rules_self_consistent():
    modules = [r.module for r in STR06_DEPENDENCY_RULES]
    assert len(modules) == len(set(modules)), '模块规则必须唯一'
    for rule in STR06_DEPENDENCY_RULES:
        assert rule.forbids, f'{rule.module} 缺少禁止面'
        for banned in rule.forbids:
            base = banned[:-2] if banned.endswith('.*') else banned
            assert rule.module != base and not rule.module.startswith(base + '.'), (
                f'{rule.module} 的禁止面包含了自己（{banned}）——自禁规则没有信息量')


def test_str06_stays_out_of_live_registry():
    # 「主仓不出现两套现役定义」的反向守卫：F8 之前，str06 家族不得进
    # ARTIFACT_SPECS 现役路径面（键即产物相对路径，可含 {n} 占位）。
    live_paths = {key.replace('\\', '/') for key in ARTIFACT_SPECS}
    for family in STR06_PATH_FAMILIES.values():
        for rel in live_paths:
            assert not rel.startswith(family.prefix), (
                f'{rel} 已进入现役 ARTIFACT_SPECS，但 {family.prefix} 属 STR-06 RC 家族；'
                f'正式挂载只能发生在 F8 切换提交')


def test_str06_subpackage_never_imports_novel():
    from contracts.schema import str06 as str06_pkg
    pkg_dir = Path(str06_pkg.__file__).parent
    for py in pkg_dir.glob('*.py'):
        tree = ast.parse(py.read_text(encoding='utf-8'))
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                names = [a.name for a in node.names]
            elif isinstance(node, ast.ImportFrom):
                names = [node.module or '']
            else:
                continue
            for name in names:
                assert not name.startswith('novel'), (
                    f'{py.name} import 了 {name}：contracts 层禁止依赖 novel（I-03）')
