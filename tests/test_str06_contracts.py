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

from contracts.artifacts import STR06_ARTIFACT_RC_VERSIONS
from contracts.schema import ARTIFACT_SPECS
from contracts.schema.book_ir import (
    ChapterIR,
    ChapterNavEntry,
    WriterBookIR,
)
from contracts.schema.consumption import STR06_CONSUMPTION_PROJECTIONS
from contracts.schema.context import (
    VerificationIR,
    WriterBrief,
    WriterContext,
)
from contracts.schema.dependency import STR06_DEPENDENCY_RULES
from contracts.schema.design import ChapterAssignment
from contracts.schema.execution import (
    DependencyEdge,
    Establishment,
    ExecutionSlice,
)
from contracts.schema.families import STR06_PATH_FAMILIES
from contracts.schema.grounding import (
    BindingEntry,
    IdentityProjection,
    OutputConstraints,
    PreserveCheckpoint,
    ReconstructionBrief,
    SemanticBinding,
    SourceLineage,
    SourceObligation,
    UnboundEntry,
)
from contracts.schema.policy import (
    PreservationPolicy,
    PreserveKind,
)
from contracts.schema.receipt import (
    ChapterLengthDisclosure,
    ChapterReceipt,
)
from contracts.schema.grounding import BriefRequirement
from tests.str06_expression_inputs import sample_brief_expression, sample_canonical_expression
from contracts.schema.design import (
    ChapterEntry,
    BodyDesign, SceneDelivery, SceneDriver, TargetBeat, TargetScene,
)
from contracts.schema.execution import Reinforcement
from novel.errors import PathRefused
from novel.paths import check_rel, check_rel_family, family_input_key

H64 = 'a' * 64


def _obligation(**over) -> dict:
    from tests.str06_facilities import anchor_stub
    base = dict(
        obligation_id='ob1', kind=PreserveKind.EVENT_CORE, statement='借种之约成立',
        anchors=anchor_stub(),
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
        **sample_brief_expression(),
        brief_id='br1', preserve_checkpoint_sha256=H64,
        requirements=(BriefRequirement(kind='event_core', must_preserve=False, satisfaction='', core_id='c1', summary='约定成立', participants=('ch1',)),),
    )
    base.update(over)
    return base


def _binding(**over) -> dict:
    base = dict(
        binding_id='bd1', preserve_checkpoint_sha256=H64,
        obligations=(SourceObligation(**_obligation()),),
        entries=(BindingEntry(beat_ref=('s01' + '#bt1'), obligation_id='ob1', scene_id='s01'),),
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
    PreserveCheckpoint(name_map_sha256='n' * 64,**_checkpoint())
    with pytest.raises(ValidationError):
        # Preserve 不接收 Design 或 Bind 的失败/结果信息：混入即拒（S-04 的一面）
        PreserveCheckpoint(name_map_sha256='n' * 64,**_checkpoint(design_feedback='retry'))
    with pytest.raises(ValidationError):
        PreserveCheckpoint(name_map_sha256='n' * 64,**_checkpoint(bind_failures=['x']))
    with pytest.raises(ValidationError):
        # obligation_id 重复 = 稳定 ID 被破坏（I-31）
        PreserveCheckpoint(name_map_sha256='n' * 64,**_checkpoint(obligations=(
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
        ReconstructionBrief(**_brief(requirements=(
            BriefRequirement(kind='event_core', must_preserve=False, satisfaction='', core_id='c1', summary='a'),
            BriefRequirement(kind='event_core', must_preserve=False, satisfaction='', core_id='c1', summary='b'),
        )))


def test_identity_projection_is_id_only():
    IdentityProjection(char_id='ch1', identity_statement='裴书远，主角', target_name='裴书远', stable_constraints=())
    with pytest.raises(ValidationError):
        # 身份投影不带源坐标：first_mention_chapter 属于 Characters 源事实
        IdentityProjection(char_id='ch1', identity_statement='x', target_name='x',
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
            BindingEntry(beat_ref=('s01' + '#bt1'), obligation_id='ob1', scene_id='s01'),
            BindingEntry(beat_ref=('s02' + '#bt1'), obligation_id='ob1', scene_id='s02'),
        )))


def test_binding_beat_ref_must_stay_inside_scene():
    BindingEntry(obligation_id='ob1', scene_id='s01', beat_ref='s01#bt1')
    with pytest.raises(ValidationError):
        BindingEntry(obligation_id='ob1', scene_id='s01', beat_ref='s02#bt1')


def test_binding_fact_bindings_reference_states_and_never_copy_values():
    """CS-F2（rc16）：事实锁→已有目标状态的映射只存锁句柄、承载拍、状态地址；
    一个锁至多一处承载；旧 rc12 产物没有该映射，按版本字面量拒读而不是补空表。"""
    from contracts.schema.grounding import FactLockBinding
    from contracts.schema.state import EffectDomain, StateAddress
    kf = 'kf_' + '0' * 16
    address = StateAddress(domain=EffectDomain.WORLD, key='agreement')
    bound = FactLockBinding(kf_id=kf, beat_ref='sc01#b06', address=address)
    assert set(bound.model_dump()) == {'kf_id', 'beat_ref', 'address'}, '不复制目标 value'
    binding = SemanticBinding(**_binding(fact_bindings=(bound,)))
    assert binding.contract_version == 'str06-rc26'
    assert SemanticBinding(**_binding()).fact_bindings == (), '空表＝没有锁被兑现'
    with pytest.raises(ValidationError):
        SemanticBinding(**_binding(fact_bindings=(bound, bound)))   # 一锁一承载
    for bad in ({'kf_id': 'kf_short', 'beat_ref': 'sc01#b06'},
                {'kf_id': kf, 'beat_ref': 'sc01'},                  # 非复合拍引用
                {'kf_id': kf, 'beat_ref': 'sc01#b06', 'value': '复制的目标值'}):
        with pytest.raises(ValidationError):
            FactLockBinding(address=address, **bad)
    stored = binding.model_dump(mode='json')
    stored['contract_version'] = 'str06-rc12'
    with pytest.raises(ValidationError):
        SemanticBinding.model_validate(stored)


# ───────────────────────── Design（source-blind + 归章自洽） ─────────────────────────

def _scene(scene_id: str) -> TargetScene:
    return TargetScene(state_reads=(),
        scene_id=scene_id,
        driver=SceneDriver(focal_actor='ch1', immediate_want='退婚', opposition='家族', turn='翻盘'),
        beats=(TargetBeat(beat_id='bt1', event='提亲', participants=('ch1',),
                          delivery_hint='', target_delta=()),),
        delivery=SceneDelivery(pov='ch1', voice='冷峻'),
        realization_brief='重心在提亲被拒的瞬间；从门槛外的雨写起，压缩路途，'
                          '在退婚言辞落地处收束，衔接下一场的家族反击。')


def _graph(**over) -> dict:
    base = dict(
        **sample_canonical_expression(('ch1',)),
        graph_id='g1', initial_state=(), book_style='冷峻克制的第三人称叙事。',
        scenes=(_scene('s01'), _scene('s02')),
        chapters=(ChapterAssignment(chapter_ordinal=1, purpose='婚约破裂，主角立誓',
                                    scene_ids=('s01',), entry=ChapterEntry.opening()),
                  ChapterAssignment(chapter_ordinal=2, purpose='家族反扑，退路收窄',
                                    scene_ids=('s02',),
                                    entry=ChapterEntry(transition='time_skip',
                                                       recap='do_not_recap'))),
    )
    base.update(over)
    return base


def test_presentation_graph_source_blind():
    BodyDesign(**_graph())
    for smuggled in ('source_ref', 'source_beat_ref', 'evidence_ref', 'source_intent_ref'):
        with pytest.raises(ValidationError):
            BodyDesign(**{**_graph(), smuggled: 'b07_01'})


def test_rc4_requires_director_brief_and_chapter_purpose():
    """RC4 新必填：realization_brief / purpose 缺失即拒读——不用空串兜底，
    不由 compiler 补写（旧 RC3 design 明确拒读，归 F5e 重跑 Design）。"""
    import copy
    no_brief = copy.deepcopy(_graph())
    scene = no_brief['scenes'][1].model_dump()
    del scene['realization_brief']
    no_brief['scenes'] = (no_brief['scenes'][0], scene)
    with pytest.raises(ValidationError):
        BodyDesign(**no_brief)
    empty_brief = copy.deepcopy(_graph())
    scene0 = empty_brief['scenes'][0].model_dump()
    scene0['realization_brief'] = ''
    empty_brief['scenes'] = (scene0, empty_brief['scenes'][1])
    with pytest.raises(ValidationError):
        BodyDesign(**empty_brief)
    no_purpose = copy.deepcopy(_graph())
    with pytest.raises(ValidationError):
        no_purpose['chapters'] = tuple(
            ChapterAssignment(chapter_ordinal=c.chapter_ordinal, scene_ids=c.scene_ids)
            for c in no_purpose['chapters'])


def test_presentation_graph_chapter_invariants():
    BodyDesign(**_graph())
    # 章号必须从 1 连续
    with pytest.raises(ValidationError):
        BodyDesign(**_graph(chapters=(
            ChapterAssignment(chapter_ordinal=2, purpose='起', scene_ids=('s01',),
                             entry=ChapterEntry(transition='time_skip',
                                                recap='do_not_recap')),
            ChapterAssignment(chapter_ordinal=3, purpose='承', scene_ids=('s02',),
                             entry=ChapterEntry(transition='new_scene',
                                                recap='brief_orientation')))))
    # 场景必须全部归章
    with pytest.raises(ValidationError, match='未归章'):
        BodyDesign(**_graph(chapters=(ChapterAssignment(
            chapter_ordinal=1, purpose='起', scene_ids=('s01',),
            entry=ChapterEntry.opening()),)))
    # 归章引用不存在的场景
    with pytest.raises(ValidationError, match='不存在'):
        BodyDesign(**_graph(chapters=(
            ChapterAssignment(chapter_ordinal=1, purpose='起', scene_ids=('s01',),
                             entry=ChapterEntry.opening()),
            ChapterAssignment(chapter_ordinal=2, purpose='承', scene_ids=('s02', 'sX'),
                             entry=ChapterEntry(transition='time_skip',
                                                recap='do_not_recap')),)))
    # 同一场景归两章
    with pytest.raises(ValidationError, match='多章'):
        BodyDesign(**_graph(chapters=(
            ChapterAssignment(chapter_ordinal=1, purpose='起', scene_ids=('s01',),
                             entry=ChapterEntry.opening()),
            ChapterAssignment(chapter_ordinal=2, purpose='承', scene_ids=('s01',),
                             entry=ChapterEntry(transition='immediate_continuation',
                                                recap='do_not_recap', anchor='s01#b01')))))
    # 权重引用不存在的场景
    with pytest.raises(ValidationError, match='chapter_weights'):
        BodyDesign(**_graph(chapter_weights={'sX': 1.0}))


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
    Establishment(op='set', established_by='init', subjects=('ch1',), domain='character', key='whereabouts', value='陆家', scope='scene')
    with pytest.raises(ValidationError):
        Establishment(op='set', established_by='init', subjects=('ch1',), domain='character', key='k', value='v', scope='act')
    from contracts.schema.execution import EffectDomain
    # D5 统一（rc13）：reader 域删除——读者认知走 knowledge(holder=reader)。
    assert {d.value for d in EffectDomain} == {
        'character', 'relation', 'knowledge', 'world'}


# ───────────────────────── WriterContext 双视图 ─────────────────────────

def _ir(scene_id: str) -> VerificationIR:
    from contracts.schema.context import BeatIR
    return VerificationIR(scene_id=scene_id,
                          obligations=(SourceObligation(**_obligation()),),
                          beats=(BeatIR(beat_ref=f'{scene_id}#bt1', event='提亲'),))


def test_writer_context_views_must_agree():
    brief = WriterBrief(scene_id='s01', realization_brief='重心在提亲被拒。',
                        scene_driver_summary='驱动', pov='ch1', voice='冷峻')
    WriterContext(scene_id='s01', execution_slice_sha256=H64,
                  verification_ir=_ir('s01'), writer_brief=brief)
    with pytest.raises(ValidationError, match='同源'):
        WriterContext(
            scene_id='s01', execution_slice_sha256=H64,
            verification_ir=_ir('s02'),
            writer_brief=brief)
    # attempt/审计字段不进静态 IR（I-38）
    with pytest.raises(ValidationError):
        WriterContext(**{**WriterContext(
            scene_id='s01', execution_slice_sha256=H64,
            verification_ir=_ir('s01'), writer_brief=brief).model_dump(),
            'attempt_id': 'a1'})


def test_verification_ir_requires_beat_positioning():
    """F5：IR 必须携带逐拍定位；跨场景 beat_ref 挂靠被构造期拒绝。"""
    with pytest.raises(ValidationError):
        VerificationIR(scene_id='s01', obligations=(), beats=())
    from contracts.schema.context import BeatIR
    with pytest.raises(ValidationError, match='别的场景'):
        VerificationIR(scene_id='s01', obligations=(), beats=(
            BeatIR(beat_ref='s02#bt1', event='x'),))
    with pytest.raises(ValidationError):
        BeatIR(beat_ref='bt1', event='x')


def test_execution_slice_fields():
    ExecutionSlice(scene_id='s01', consumed_obligation_ids=('ob1',),
                   entry_state_sha256=H64, entry_knowledge_sha256=H64,
                   execution_slice_sha256=H64)
    with pytest.raises(ValidationError):
        ExecutionSlice(scene_id='s01', consumed_obligation_ids=(),
                       entry_state_sha256='short', entry_knowledge_sha256=H64,
                       execution_slice_sha256=H64)


# ───────────────────────── 章级回执（F5） ─────────────────────────

def _length(**over) -> dict:
    base = dict(char_count=900, target_chars=1000, band_low=600, band_high=1300,
                within_band=True, deviation_ratio=-0.1)
    base.update(over)
    return base


def _chapter_receipt(**over) -> dict:
    base = dict(
        name_mentions=(), non_name_spans=(), chapter_ordinal=1, writer_view_sha256=H64, validation_sha256='b' * 64,
        machine_view_sha256='c' * 64,
        prose_sha256=H64, request_identity='req-1',
        protocol_version='str06-body-protocol-v5', renderer_version='str06-render-v5',
        validator_version='str06-body-mechanical-v1', status='accepted',
        mechanical_checks=('non_empty', 'length_band'), length=_length())
    base.update(over)
    return base


def test_chapter_receipt_binds_inputs_and_rejects_metadata():
    receipt = ChapterReceipt(**_chapter_receipt())
    assert receipt.semantic == 'unverified'
    # 无关 metadata 不作硬匹配条件：类型上就不存在
    with pytest.raises(ValidationError):
        ChapterReceipt(**{**_chapter_receipt(), 'attempt_id': 'a1'})
    with pytest.raises(ValidationError):
        ChapterReceipt(**{**_chapter_receipt(), 'audited_at': '2026-09-24'})
    # 换 NameMap 不影响接受回执：回执字段里没有任何交付材料
    assert 'name_map' not in ChapterReceipt.model_fields
    # hash 字符集：大写/非十六进制在构造期即拒
    with pytest.raises(ValidationError):
        ChapterReceipt(**_chapter_receipt(prose_sha256='A' * 64))
    with pytest.raises(ValidationError):
        ChapterReceipt(**_chapter_receipt(writer_view_sha256='A' * 64))
    # 篇幅披露窗口自洽
    with pytest.raises(ValidationError):
        ChapterReceipt(**_chapter_receipt(length=_length(band_low=2000, band_high=1)))


def test_chapter_receipt_disclosure_shape():
    disclosed = ChapterReceipt(**_chapter_receipt(
        length=_length(char_count=400, band_low=600, within_band=False,
                       deviation_ratio=-0.6), length_disclosed=True))
    assert disclosed.length_disclosed and not disclosed.length.within_band
    in_band = ChapterReceipt(**_chapter_receipt())
    assert not in_band.length_disclosed and in_band.length.within_band


# ───────────────────────── 消费投影与 Prepare 坐标分离 ─────────────────────────

def test_consumption_projection_registry_complete():
    assert set(STR06_CONSUMPTION_PROJECTIONS) == {
        'world_view', 'character_exposure', 'chapter_map', 'body_access',
        'hook_material', 'rename_materials'}
    for spec in STR06_CONSUMPTION_PROJECTIONS.values():
        assert spec.source_owners
        assert spec.coordinate.value in ('source', 'target', 'dual')


# ───────────────────────── 路径家族 ─────────────────────────

def test_path_families_unique_prefixes():
    prefixes = [f.prefix for f in STR06_PATH_FAMILIES.values()]
    assert len(prefixes) == len(set(prefixes))
    assert all(p.endswith('/') for p in prefixes)
    # F5 章级正文家族已登记；场景级旧家族不得残留（正文按章成文）
    assert 'str06.stage5.scenes' not in STR06_PATH_FAMILIES
    assert 'str06.stage5.acceptance.fidelity' not in STR06_PATH_FAMILIES
    assert 'str06.stage5.body' in STR06_PATH_FAMILIES
    assert 'str06.stage5.acceptance.body' in STR06_PATH_FAMILIES
    assert 'str06.stage4.ir' in STR06_PATH_FAMILIES


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


def test_str06_live_mounts_come_from_the_candidate_table():
    # 「主仓不出现两套现役定义」的正向守卫：F8a 激活后 str06 家族就是现役
    # 注册面（键即产物相对路径，可含 {nn} 占位），现役 ARTIFACT_SPECS 内
    # 属于 str06 家族前缀的每个 rel 都必须来自激活后的产物表
    # （`contracts/artifacts.py::STR06_ARTIFACT_RC_VERSIONS`），不得出现表外
    # 的第二套定义。
    live_paths = {key.replace('\\', '/') for key in ARTIFACT_SPECS}
    for family in STR06_PATH_FAMILIES.values():
        for rel in live_paths:
            if not rel.startswith(family.prefix):
                continue
            assert rel in STR06_ARTIFACT_RC_VERSIONS, (
                f'{rel} 挂在现役 ARTIFACT_SPECS 的 {family.prefix} 家族下，'
                f'但不在激活产物表里：str06 家族的现役挂载只能来自激活表')


def test_str06_contracts_never_import_novel():
    """contracts 层禁止 import novel（I-03）。

    F8c 后新链契约模型是 `contracts/schema/` 顶层模块（迁移包子包已删），本条
    随之扫**整个 contracts 目录**而不是单个子包：一条反向边都不许有。
    """
    contracts_dir = Path(__file__).resolve().parents[1] / 'contracts'
    scanned = 0
    for py in sorted(contracts_dir.rglob('*.py')):
        if '__pycache__' in py.parts:
            continue
        scanned += 1
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
    # 2026-10-04 清理批：旧链 schema 模块删除后现役 38 个。
    assert scanned >= 38, f'只扫到 {scanned} 个契约层模块——扫描面可能失效'
