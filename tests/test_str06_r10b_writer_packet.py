#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""R10b · WriterIR 投影与 renderer v7 的完整章包夹具（夹具先行，免费）。

【冻结的夹具是什么】一个含 kp/kf 锁、expression_refs、义务承载拍、
无消费者状态、unset 状态与原文未回答悬念的**合成绿基座**（两章三场），
经 `compile_writer_book_ir` 到 `render_chapter_packet` 的完整投影。断言钉住
实际发给 Writer 的完整输入（非单分区）必须同时具备（PLAN R10b.1）：

1. 因果与公开说辞/真实判断并存保留（义务 statement 两面都写；kp 知情按
   锁模式措辞，句柄不进阅读面）；
2. 既成事实作为约束送达（仍被消费的状态进背景）、不变成本章重复介绍
   任务（无消费者的拍状态不向后投影——C7；unset 不作背景——C10）；
3. 言行反差保留为表达目标（expression_refs 展开成拍级表达任务，措辞是
   呈现目标、不是解释性总结句）；
4. 本章入口状态、知情边界、必要承接、退出位置（末拍结果）与统一表达
   约束（书级风格/人称/声线）齐全。

【判据入口】C7（背景回声）、C9（原文未知不补答）、C10（生命周期残留）、
P4（对白自称）按 `tests/str06_semantic_cases.py::CASES_BY_BATCH['R10b']`
登记的期望在此转绿；红面（kp/kf 进阅读面）由 WriterPacket 构造期守卫
与 `material_identities` 锁键闭集钉住。
"""
from __future__ import annotations

import hashlib

import pytest

H64 = 'a' * 64

INQUIRY = '夫君在暗中查访旧案'
PROMISE = '约定半月便回'
QUESTION = '当年他去了哪里'


def _lock(prefix: str, seed: str) -> str:
    return prefix + hashlib.sha256(seed.encode('utf-8')).hexdigest()[:16]


KP_INQUIRY = _lock('kp_', 'p_inquiry')
KF_PROMISE = _lock('kf_', 'b03_01.e1')
KF_QUESTION = _lock('kf_', 'loop_missing')


def _requirements():
    from contracts.schema.grounding import (
        BriefFactLock, BriefKnowledgeLock, BriefProposition, BriefRequirement,
        ReconstructionBrief,
    )
    from contracts.schema.policy import PreserveKind
    from tests.str06_expression_inputs import sample_narration
    from contracts.schema.expression import ExpressionRequirement

    propositions = (BriefProposition(kp_id=KP_INQUIRY, statement=INQUIRY),)
    knowledge_locks = (
        BriefKnowledgeLock(holder='fu_jun', kp=KP_INQUIRY, mode='known'),
        BriefKnowledgeLock(holder='yun_niang', kp=KP_INQUIRY, mode='suspected'),
        BriefKnowledgeLock(holder='yun_niang', kp=KP_INQUIRY, mode='known'),
        BriefKnowledgeLock(holder='reader', kp=KP_INQUIRY, mode='known'),
    )
    fact_locks = (
        BriefFactLock(kf_id=KF_PROMISE, statement=PROMISE,
                      kind='expected', modality='expected'),
        BriefFactLock(kf_id=KF_QUESTION, statement=QUESTION,
                      kind='unanswered', modality=None),
    )
    requirements = (
        # 公开说辞与真实判断并存（夹具性质 1）。
        BriefRequirement(
            core_id='req_claim_truth', kind=PreserveKind.EVENT_CORE,
            summary='夫君假称赴文会，实为暗中查访旧案',
            must_preserve=True,
            satisfaction='对外说辞与真实意图都呈现；不得把说辞当成真实动机',
            participants=('fu_jun',),
            locks=(KP_INQUIRY,), knowledge_changes=({'change_id': 'kc_1111111111111111', 'holder': 'fu_jun', 'kp': KP_INQUIRY, 'mode': 'known'},)),
        # 先怀疑、后新证据确认（P2 的授权升级链）。
        BriefRequirement(
            core_id='req_suspect_confirm', kind=PreserveKind.CHARACTER_ARC,
            summary='芸娘由起疑到确认', must_preserve=True,
            satisfaction='怀疑与确认分两个时点呈现，确认由新证据支持',
            participants=('yun_niang',),
            locks=(KP_INQUIRY,), knowledge_changes=tuple({'change_id': 'kc_' + digit * 16, 'holder': 'yun_niang', 'kp': KP_INQUIRY, 'mode': mode} for digit, mode in [('2', 'suspected'), ('3', 'known')])),
        # 预计时长不得写成已发生（C2：kf 时态锁）。
        BriefRequirement(
            core_id='req_promise', kind=PreserveKind.EVENT_CORE,
            summary=PROMISE, must_preserve=True,
            satisfaction='约定作为预计呈现，不得写成已经归来',
            participants=('fu_jun', 'yun_niang'),
            locks=(KF_PROMISE,), realize_locks=(KF_PROMISE,)),
        # 原文未回答的悬念（C9：unanswered 锁，锚定 loop）。
        BriefRequirement(
            core_id='req_unanswered', kind=PreserveKind.REVEAL,
            summary='夫君当年去向成谜', must_preserve=True,
            satisfaction='悬念保留为悬念，正文不得作答',
            participants=('fu_jun',),
            locks=(KF_QUESTION,)),
    )
    expression = (
        ExpressionRequirement(ex_id='ex_01', kind='narration',
                              statement='第三人称旁白叙述。'),
        ExpressionRequirement(ex_id='ex_02', kind='contrast',
                              statement='对外守礼与内心直白的反差。'),
    )
    brief = ReconstructionBrief(
        brief_id='br', preserve_checkpoint_sha256=H64,
        requirements=requirements, propositions=propositions,
        knowledge_locks=knowledge_locks, fact_locks=fact_locks,
        book_constraints=(), source_narration=sample_narration(),
        expression=expression,
        expression_readings=_readings())
    return brief


def _readings():
    from contracts.schema.expression import ExpressionReadings
    return ExpressionReadings(status='unavailable', paragraph_mean_chars=None,
                              sentence_mean_chars=None, dialogue_ratio=None)


def _wire() -> dict:
    """stage4-design-v5 中间形状：两章三场，携带 kp 锁承载、被事实锁承载的预计状态与 C7/C10 材料。"""
    return {
        'protocol_version': 'stage4-design-v5',
        'narration': {'person': 'third', 'focalizers': ['narrator', 'yun_niang']},
        'expression_decisions': [
            {'ex_id': 'ex_01', 'action': 'change',
             'reason': '合成样例改限知：以芸娘视角承接限知叙事。',
             'target_statement': '第三人称限知叙述，允许多视角。'},
            {'ex_id': 'ex_02', 'action': 'keep'}],
        'book_style': '冷峻克制的限知叙事，重日常细节里的异样。',
        'world_rules': ['术法须以寿命为代价'],
        'character_voices': [
            {'char_id': 'yun_niang', 'voice': '短句、少感叹，怀疑压在动作里'}],
        'initial_state': [
            {'domain': 'character', 'subjects': ['yun_niang'], 'holder': None,
             'key': 'residence', 'op': 'set', 'modality': 'actual',
             'value': '城南旧宅', 'scope': 'book'},
            # C10 材料：初态成立、拍上显式解除——unset 不作背景。
            {'domain': 'character', 'subjects': ['yun_niang'], 'holder': None,
             'key': 'chill', 'op': 'set', 'modality': 'actual',
             'value': '新婚夜里受了寒', 'scope': 'book'}],
        'initial_surfaces': [
            {'char_id': 'yun_niang', 'surfaces': ['苏芸娘', '芸娘']},
            {'char_id': 'fu_jun', 'surfaces': ['夫君']}],
        'scenes': [
            {  # sc01（第 1 章）
             'state_reads': [],
             'driver': {'focal_actor': 'yun_niang', 'immediate_want': '弄清约定',
                        'opposition': '夫君的搪塞', 'turn': '送别'},
             'realization_brief': '重心在临行约定与说辞的落差；在巷口收住。',
             'beats': [
                 # 义务 req_promise 的验收拍：statement 与事件逐字相同（去重面）。
                 {'event': PROMISE, 'participants': ['yun_niang', 'fu_jun'],
                  'delivery_hint': '', 'target_delta': [
                      {'domain': 'character', 'subjects': ['fu_jun'],
                       'holder': None, 'key': 'return_promise', 'op': 'set',
                       'modality': 'expected', 'value': PROMISE, 'scope': 'book'},
                      # C10：显式解除初态的受寒。
                      {'domain': 'character', 'subjects': ['yun_niang'],
                       'holder': None, 'key': 'chill', 'op': 'unset',
                       'value': None, 'scope': 'book'}],
                  'expression_refs': [], 'surface_introductions': []},
                 {'event': '夫君假称赴文会，实为暗中查访旧案',
                  'participants': ['yun_niang', 'fu_jun'],
                  'delivery_hint': '说辞与内心两个声部', 'target_delta': [
                      {'domain': 'character', 'subjects': ['fu_jun'], 'holder': None,
                       'key': 'actual_intent', 'op': 'set', 'value': INQUIRY,
                       'scope': 'book', 'modality': 'actual'},
                      {'domain': 'knowledge', 'subjects': ['fu_jun'],
                       'holder': 'fu_jun', 'key': KP_INQUIRY, 'op': 'set',
                       'value': INQUIRY, 'scope': 'book',
                       'knowledge_mode': 'known'},
                      {'domain': 'knowledge', 'subjects': ['yun_niang'],
                       'holder': 'yun_niang', 'key': KP_INQUIRY, 'op': 'set',
                       'value': INQUIRY, 'scope': 'book',
                       'knowledge_mode': 'suspected'}],
                  # 言行反差的表达任务落具体拍（夹具性质 3）。
                  'expression_refs': ['ex_02'], 'surface_introductions': [],
                  'threads': [
                      {'op': 'open', 'tag': 'q1', 'kind': 'question',
                       'subject': 'yun_niang', 'text': '文会之说有几分真'}]},
                 # C7 材料：只写一次、后文无消费者的拍状态。
                 {'event': '芸娘当夜织补到三更', 'participants': ['yun_niang'],
                  'delivery_hint': '', 'target_delta': [
                      {'domain': 'character', 'subjects': ['yun_niang'],
                       'holder': None, 'key': 'night_sewing', 'op': 'set',
                       'modality': 'actual', 'value': '织补到三更',
                       'scope': 'book'}],
                  'expression_refs': [], 'surface_introductions': []}],
             'delivery': {'pov': 'yun_niang', 'voice': '冷峻', 'avoid': ['倒叙']}},
            {  # sc02（第 2 章）：读取被锁承载的预计状态（仍被消费→背景保留）＋确认升级。
             'state_reads': [
                 {'domain': 'character', 'subjects': ['fu_jun'], 'holder': None,
                  'key': 'return_promise'}],
             'driver': {'focal_actor': 'yun_niang', 'immediate_want': '证实疑心',
                        'opposition': '无凭据', 'turn': '亲见查访文书'},
             'realization_brief': '重心在疑心落地的瞬间；不点破。',
             'beats': [
                 {'event': '芸娘翻检夫君行囊', 'participants': ['yun_niang'],
                  'delivery_hint': '', 'target_delta': [], 'expression_refs': [],
                  'surface_introductions': []},
                 {'event': '芸娘亲见查访文书，疑心落地',
                  'participants': ['yun_niang'],
                  'delivery_hint': '', 'target_delta': [
                      {'domain': 'knowledge', 'subjects': ['yun_niang'],
                       'holder': 'yun_niang', 'key': KP_INQUIRY, 'op': 'set',
                       'value': INQUIRY, 'scope': 'book',
                       'knowledge_mode': 'known'},
                      {'domain': 'knowledge', 'subjects': [], 'holder': 'reader',
                       'key': KP_INQUIRY, 'op': 'set',
                       'value': INQUIRY, 'scope': 'book',
                       'knowledge_mode': 'known', 'confirms': {
                           'domain': 'character', 'subjects': ['fu_jun'],
                           'key': 'actual_intent'}}],
                  'expression_refs': [], 'surface_introductions': []}],
             'delivery': {'pov': 'yun_niang', 'voice': '冷峻', 'avoid': []}},
            {  # sc03（第 2 章）：无线程操作——开放线程快照章内去重的材料。
             'state_reads': [],
             'driver': {'focal_actor': 'yun_niang', 'immediate_want': '试探夫君',
                        'opposition': '夫君搪塞', 'turn': '话到嘴边收回'},
             'realization_brief': '重心在同处一室的沉默张力；不点破。',
             'beats': [
                 {'event': '同处一室无言', 'participants': ['yun_niang', 'fu_jun'],
                  'delivery_hint': '', 'target_delta': [], 'expression_refs': [],
                  'surface_introductions': []}],
             'delivery': {'pov': 'yun_niang', 'voice': '冷峻',
                          'avoid': ['倒叙']}}],
        'chapters': [
            {'purpose': '临行约定埋下疑心', 'scenes': 1},
            {'purpose': '疑心落地，说辞与真相对撞', 'scenes': 2,
             'entry': {'transition': 'time_skip', 'carryover': '芸娘的疑心未消',
                       'recap': 'do_not_recap'}}],
        'scene_weights': [1, 1, 1],
    }


def _design():
    from novel.stages.stage4.design import assemble_body_design
    brief = _requirements()
    # v5 中间形状没有事实锁标注通道；事实锁的接受与映射由 v8 真实入口验证
    # （test_str06_a4_design_locks.py 的 CS-F2 一节），这里只验写手面。
    no_realize = brief.model_copy(update={'requirements': tuple(
        r.model_copy(update={'realize_locks': ()}) for r in brief.requirements)})
    body, errors = assemble_body_design(
        _wire(), known_char_ids={'yun_niang', 'fu_jun'},
        expression_requirements=brief.expression,
        source_narration=brief.source_narration, brief=no_realize)
    assert body is not None and not errors, errors
    return body


def _binding(body):
    from contracts.schema.grounding import (
        BindingEntry, ObligationAnchor, PreserveCheckpoint, SemanticBinding,
        SourceLineage, SourceObligation,
    )
    from contracts.schema.policy import PreserveKind

    def _cognition(holder: str, mode: str):
        return ObligationAnchor(
            handle=f'c_{holder}', source_kind='cognition', holder=holder,
            mode=mode, proposition_ref='p_inquiry', proposition_statement=INQUIRY,
            quotes=(INQUIRY,))

    obligations = (
        SourceObligation(
            obligation_id='ob01', kind=PreserveKind.EVENT_CORE,
            statement='夫君假称赴文会，实为暗中查访旧案',
            anchors=(_cognition('fu_jun', 'learns'),),
            lineage=SourceLineage(blueprint_refs=('b03_02',),
                                  character_ids=('fu_jun',)),
            must_preserve=True,
            satisfaction='对外说辞与真实意图都呈现；不得把说辞当成真实动机'),
        SourceObligation(
            obligation_id='ob02', kind=PreserveKind.CHARACTER_ARC,
            statement='芸娘由起疑到确认',
            anchors=(_cognition('yun_niang', 'suspects'),
                     _cognition('yun_niang', 'confirms')),
            lineage=SourceLineage(blueprint_refs=('b03_02',),
                                  character_ids=('yun_niang',)),
            must_preserve=True,
            satisfaction='怀疑与确认分两个时点呈现，确认由新证据支持'),
        SourceObligation(
            obligation_id='ob03', kind=PreserveKind.EVENT_CORE,
            statement=PROMISE,
            anchors=(ObligationAnchor(
                handle='b03_01.e1', source_kind='effect',
                address='char:fu_jun.return_promise', value=PROMISE,
                modality='expected', quotes=(PROMISE,)),),
            lineage=SourceLineage(blueprint_refs=('b03_01',),
                                  character_ids=('fu_jun', 'yun_niang')),
            must_preserve=True, satisfaction='约定作为预计呈现，不得写成已经归来'),
        SourceObligation(
            obligation_id='ob04', kind=PreserveKind.REVEAL,
            statement='夫君当年去向成谜的悬念保留为悬念',
            anchors=(ObligationAnchor(
                handle='b03_02.l1', source_kind='loop', question=QUESTION,
                quotes=(QUESTION,)),),
            lineage=SourceLineage(blueprint_refs=('b03_02',),
                                  character_ids=('fu_jun',)),
            must_preserve=True, satisfaction='悬念保留为悬念，正文不得作答'),
    )
    cp = PreserveCheckpoint(name_map_sha256='n' * 64,
        checkpoint_id='cp', policy_id='p', policy_version=5, characters_sha256=H64,
        blueprints_sha256=H64, provider='x', model='y', request_sha256='r' * 64,
        obligations=obligations, checkpoint_sha256=H64)
    # CS-3 bind 退役：承载位＝design wire 的 real 标注投影（ob → 标注拍序号）。
    # ob02 复合承载：sc02#b01（支撑）→ sc02#b02（接受）。
    placements = {
        'ob01': [(0, 1)],
        'ob02': [(1, 0), (1, 1)],
        'ob03': [(0, 0)],
        'ob04': [(0, 1)],
    }
    from contracts.schema.state import EffectDomain, StateAddress
    from novel.stages.stage4.grounding import derive_semantic_binding
    from novel.stages.stage4.slim import FactLockMark
    # CS-F2：预计状态由事实锁在已选状态上标注承载；映射随 Binding 发布。
    promise = FactLockMark(
        kf_id=KF_PROMISE, scene=0, beat=0, state=0, op='set', modality='expected',
        address=StateAddress(domain=EffectDomain.CHARACTER, key='return_promise',
                             subjects=('fu_jun',)))
    binding, errors = derive_semantic_binding(placements, body, cp, (promise,))
    assert binding is not None and not errors, errors
    return binding


def _ir():
    from novel.stages.stage4.compile import compile_execution_plan
    from novel.stages.stage4.book_ir import compile_writer_book_ir
    from tests.test_str06_hook import _characters
    from tests.str06_facilities import name_map_of
    body = _design()
    binding = _binding(body)
    plan, errors = compile_execution_plan(body, binding)
    assert not errors, errors
    chars = _characters()
    brief = _requirements()
    ir = compile_writer_book_ir(
        graph=body, plan=plan, binding=binding, total_budget=4000,
        length_band=(0.6, 1.3), book_title='合成样例',
        projection=chars.name_projection(), structure=chars.name_structure,
        fact_locks=brief.fact_locks,
        kp_statements={p.kp_id: p.statement for p in brief.propositions},
        name_map=name_map_of(chars))
    return body, binding, plan, ir


# ═══════════════ 一、完整章包夹具（性质 1–4 的送达断言） ═════════════════════


def test_freeze_full_packet_carries_all_four_required_faces():
    """实际发给 Writer 的完整输入（render_chapter_packet 全字节）四类性质齐全。

    夹具性质：①公开说辞与真实判断并存；②既成事实作约束、不重复成任务；
    ③反差作为表达目标；④入口状态、知情边界、承接、退出位置与统一表达
    约束（书级风格／人称／声线）。
    """
    from novel.stages.common import (
        WRITER_PROTOCOL, build_writer_packet, project_chapter_packet,
        render_chapter_packet,
    )
    _body, _binding, _plan, ir = _ir()
    packet = project_chapter_packet(ir, 2)
    wp = build_writer_packet(packet)
    text = render_chapter_packet(packet)

    # ④ 统一表达约束与章包结构：协议、六分区节标题、交付要求齐全。
    assert WRITER_PROTOCOL in text
    for section in ('【必须写出】', '【不可违背】', '【承接】', '【导演说明】',
                    '【背景】', '【禁区】', '【本章交付要求】'):
        assert section in text
    assert '全书风格：冷峻克制的限知叙事' in '\n'.join(wp.creative_guidance)
    assert '叙述人称：第三人称' in '\n'.join(wp.must_respect)
    # ④ 承接与退出位置：章承接行＋末拍结果行（sc02 是本章第一场）。
    assert any('上一章收在「芸娘当夜织补到三更」' in line
               for line in wp.continuity)
    assert any(line.startswith('第一场·第2拍：芸娘亲见查访文书')
               for line in wp.must_realize)
    # ④ 入口状态与知情边界（第 2 章：读取的 kf 旧值进 must_respect）。
    assert any('第一场·入场已成立' in line and PROMISE in line
               for line in wp.must_respect)
    # ① kp 知情按锁模式措辞（P2 升级链的拍末效应行），句柄不进阅读面。
    assert any(f'芸娘确知「{INQUIRY}」' in line for line in wp.must_realize)
    assert not any(KP_INQUIRY in line for line in (
        wp.must_realize + wp.must_respect + wp.continuity
        + wp.creative_guidance + wp.background + wp.guard_only))


def test_obligations_organize_under_carrying_beats_with_dedup():
    """义务挂承载拍（v7：场→拍组织）；statement 与本拍事件逐字相同时去重。"""
    from novel.stages.common import build_writer_packet, project_chapter_packet
    _body, _binding, _plan, ir = _ir()
    wp = build_writer_packet(project_chapter_packet(ir, 1))
    # ob03（验收拍 sc01#b01）的 statement 与事件逐字相同 →「即本拍事件」形态。
    assert any(
        line.startswith(f'第一场·第1拍·义务（即本拍事件）——本拍验收时须兑现：'
                        '约定作为预计呈现')
        for line in wp.must_realize), wp.must_realize
    # ob01（验收拍 sc01#b02）statement 也与事件逐字相同 → 同样去重；
    # ob04（同拍承载）statement 不同 → 完整义务行（不按相似度删）。
    assert any(
        line.startswith('第一场·第2拍·义务（即本拍事件）——本拍验收时须兑现：'
                        '对外说辞与真实意图都呈现')
        for line in wp.must_realize)
    assert any(
        line.startswith('第一场·第2拍·义务：夫君当年去向成谜的悬念保留为悬念'
                        '（本拍验收时须兑现：悬念保留为悬念，正文不得作答）')
        for line in wp.must_realize)
    # 场级孤立义务行（v6 形态）不再出现。
    assert not any(line.startswith('第一场·义务：') for line in wp.must_realize)
    wp2 = build_writer_packet(project_chapter_packet(ir, 2))
    # ob02 验收拍 sc02#b02、支撑拍 sc02#b01 →「第1拍起铺垫」。
    assert any('第2拍·义务（第1拍起铺垫）：芸娘由起疑到确认' in line
               for line in wp2.must_realize)


def test_modality_wording_reaches_writer_and_expected_not_actual():
    """时态锁措辞（C2 写手面）：expected 的拍末效应显式「尚未发生」。"""
    from novel.stages.common import build_writer_packet, project_chapter_packet
    _body, _binding, _plan, ir = _ir()
    wp = build_writer_packet(project_chapter_packet(ir, 1))
    assert any(f'夫君：{PROMISE}（预计，尚未发生）' in line
               for line in wp.must_realize), wp.must_realize


def test_expression_refs_expand_to_beat_level_presentation_goals():
    """expression_refs 展开成拍级表达任务：呈现目标措辞，非解释性总结句。"""
    from novel.stages.common import build_writer_packet, project_chapter_packet
    _body, _binding, _plan, ir = _ir()
    wp = build_writer_packet(project_chapter_packet(ir, 1))
    assert any(
        line.startswith('第一场·第2拍·表达任务：对外守礼与内心直白的反差。'
                        '（呈现目标')
        for line in wp.must_realize), wp.must_realize
    assert not any('ex_02' in line for line in wp.must_realize)


# ═══════════════ 二、背景投影收窄（C7/C10 判据） ═══════════════════════════


def test_c7_background_only_keeps_init_foothold_and_consumed_state():
    """C7：无消费者的拍状态不向后投影；书级初态立足点与仍被消费的状态保留。"""
    from novel.stages.common import build_writer_packet, project_chapter_packet
    _body, _binding, _plan, ir = _ir()
    wp = build_writer_packet(project_chapter_packet(ir, 2))
    text = '\n'.join(wp.background)
    # 书级初态立足点 → 保留。
    assert '城南旧宅' in text
    # 仍被消费的当前状态：sc02 承载 kp 升级（锁键消费者）→ 入场旧值
    #（sc01 建立的「起疑」）进背景——怀疑→确认链的写手面。
    assert f'芸娘起疑「{INQUIRY}」' in text, wp.background
    # C7 反例：只写一次、无任何消费者的拍状态（night_sewing）→ 不进背景。
    assert '织补到三更' not in text
    # C10 反例：已解除的初态（chill unset）→ 不作背景。
    assert '受了寒' not in text
    # kf 旧值经**声明读取**以 must_respect 送达（entry 视图），不双区重复。
    assert not any(PROMISE in line for line in wp.background)


def test_background_deduped_within_chapter_and_names_on_change_only():
    """背景章首一次、场内只补新条目；称谓许可只在变化处列。"""
    from novel.stages.common import build_writer_packet, project_chapter_packet
    _body, _binding, _plan, ir = _ir()
    wp = build_writer_packet(project_chapter_packet(ir, 2))
    # 背景行章内无逐字重复。
    assert len(wp.background) == len(set(wp.background))
    # 第 2 章第二场（sc03）无新增面 → 不再重复「本场可用称谓」基线行。
    permits = [line for line in wp.must_respect if '可用称谓' in line]
    assert permits and permits[0].startswith('第一场·本场可用称谓')
    assert not any(line.startswith('第二场·本场可用称谓') for line in permits)


def test_narrow_background_keeps_lock_key_consumers():
    """锁键是消费者：后文承载 kp 升级的知情旧值仍进背景（怀疑→确认链）。"""
    from novel.stages.common import consumption_closure
    body = _design()
    closure = consumption_closure(body)
    knowledge_addr = ('knowledge', ('yun_niang',), 'yun_niang', KP_INQUIRY)
    # sc01（场序 0）建立 suspected＋sc02（场序 1）升级 known → 集合两位置；
    # 收窄按「建立之后」判定 → sc02 的入场旧值仍进背景。
    assert closure.get(knowledge_addr) == (0, 1)


def test_fact_lock_carrier_is_a_consumer_via_binding_not_via_a_kf_key():
    """CS-F2：事实锁承载状态由 Binding 的 fact_bindings 登记为消费者（原先靠
    kf 状态键）；承载拍所在场计入消费位置，句柄本身不进任何阅读材料。"""
    from novel.stages.common import consumption_closure
    body, binding, _plan, ir = _ir()
    address = ('character', ('fu_jun',), '', 'return_promise')
    assert consumption_closure(body).get(address) == (1,), '只有 sc02 的声明读取'
    assert consumption_closure(body, binding.fact_bindings).get(address) == (0, 1)
    assert [f.kf_id for f in binding.fact_bindings] == [KF_PROMISE]
    from novel.stages.common import render_chapter_packet, project_chapter_packet
    for chapter in (1, 2):
        text = render_chapter_packet(project_chapter_packet(ir, chapter))
        assert KF_PROMISE not in text and 'kf_' not in text


def test_consumption_closure_ignores_hook_selection():
    """正文背景消费只来自正文，Hook 取材与禁区不改变章包。"""
    from contracts.schema.design import PresentationGraph
    from contracts.schema.hook import HookDesign
    from novel.stages.common import consumption_closure
    body = _design()
    hook = HookDesign(
        pov={'person': 'third', 'focalizers': ['yun_niang']},
        opening_facts=[{'ref': 'init', 'audience': 'reader',
                        'address': {'domain': 'character', 'key': 'residence',
                                    'subjects': ['yun_niang'], 'holder': None}}],
        materials=[{'beat_ref': 'sc01#b01', 'emphasis': 'develop', 'transition': 'start'}],
        concealed=[], surface_releases=[{'char_id': 'yun_niang',
                                         'surfaces': ['芸娘']}],
        forbidden_reveals=[
            {'ref': 'sc02#b02',
             'address': {'domain': 'knowledge', 'key': KP_INQUIRY,
                         'subjects': ['yun_niang'], 'holder': 'yun_niang'}}],
        reader_expectation={'kind': 'action', 'focus': '她如何查明真相',
                            'followup_refs': ['sc02#b02']},
        local_focus='通过观察与即时反应呈现疑心。')
    graph = PresentationGraph.model_validate({
        **body.model_dump(mode='json'), 'contract_version': 'str06-rc26',
        'hook': hook.model_dump(mode='json')})
    closure = consumption_closure(graph)
    assert closure == consumption_closure(body)
    assert ('character', ('yun_niang',), '', 'residence') not in closure
    assert closure.get(
        ('knowledge', ('yun_niang',), 'yun_niang', KP_INQUIRY)) == (0, 1)


# ═══════════════ 三、原文未回答锁与禁区（C9 判据） ═════════════════════════


def test_c9_unanswered_guard_reaches_packets_from_carrier_position():
    """C9：原文未回答锁从承载位置（sc01#b02，第 1 章）起进章包禁区。"""
    from novel.stages.common import build_writer_packet, project_chapter_packet
    _body, _binding, _plan, ir = _ir()
    guard_line = f'原文未回答的悬念「{QUESTION}」：正文不得给出答案'
    for ordinal in (1, 2):
        wp = build_writer_packet(project_chapter_packet(ir, ordinal))
        assert any(line.startswith(guard_line) for line in wp.guard_only), ordinal
    # IR 数据面：承载位置与问题文本都在，且承载场之前不投。
    sc01 = ir.chapter(1).scenes[0]
    assert any(g.question == QUESTION and g.carrier_ref == 'sc01#b02'
               for g in sc01.verification_ir.unanswered_guards)


# ═══════════════ 四、锁句柄不进阅读面（P4／构造期守卫） ═════════════════════


def test_writer_packet_rejects_kp_kf_handles_in_any_section():
    """WriterPacket 构造期守卫：kp_/kf_ 句柄出现在任何分区即拒。"""
    from contracts.schema.context import WriterPacket
    with pytest.raises(ValueError, match='kp_'):
        WriterPacket(
            chapter_ordinal=1,
            must_realize=(f'芸娘确知「{INQUIRY}」（{KP_INQUIRY}）',),
            must_respect=(), continuity=(), creative_guidance=(),
            background=(), guard_only=())


def test_material_identities_include_lock_keys_and_catch_leak():
    """material_identities 增锁键闭集：渲染行泄漏 kp/kf 句柄即拦（P4 面）。"""
    from novel.stages.common import (
        material_identities, project_chapter_packet, validate_writer_materials,
    )
    from tests.test_str06_hook import _characters
    body, binding, _plan, ir = _ir()
    identities = material_identities(body, binding, _characters())
    assert KP_INQUIRY in identities and KF_PROMISE in identities
    problems = validate_writer_materials(
        project_chapter_packet(ir, 1), internal_ids=identities)
    assert problems == []


def test_p4_dialogue_self_reference_not_an_identity_leak():
    """P4：对白自称「我」不是 delta key，不在身份闭集——人称约束限定叙述层。"""
    from novel.stages.common import (
        WRITER_PROTOCOL, build_writer_packet, project_chapter_packet,
    )
    _body, _binding, _plan, ir = _ir()
    wp = build_writer_packet(project_chapter_packet(ir, 1))
    # 人称约束限定叙述层：渲染的人称行明说「对白中的自称不改变正文叙述人称」。
    assert any('叙述人称：第三人称' in line
               and '对白中的自称不改变正文叙述人称' in line
               for line in wp.must_respect)
    # 协议 v7 的知情/时态/留白条款（写手面总则）。
    assert '怀疑不写成确知、听说不写成相信、计划与预计不写成已发生' \
        in WRITER_PROTOCOL
    assert '场景是控制单元' in WRITER_PROTOCOL
    assert '不写成解释性总结句' in WRITER_PROTOCOL


# ═══════════════ 五、读数与形状对照（v6 基线 → v7 收窄） ═══════════════════


def test_packet_metrics_reports_new_shape_readings():
    """packet_metrics 新读数：重复行比例、被剔除历史回声数、锁数、离线 token。"""
    from novel.stages.common import packet_metrics
    _body, _binding, _plan, ir = _ir()
    metrics = packet_metrics(ir, token_counter=lambda text: len(text))
    entry = metrics['chapters'][1]
    assert entry['carried_locks'] >= 3       # kp×2（sc02）＋unanswered×1
    assert entry['echoes_dropped'] >= 0
    # token_counter 注入即按其口径计数（此处 len＝含空白的字符数 ≥ 非空白字数）。
    assert entry['writer_view_tokens'] >= entry['writer_view_chars']
    # 收窄读数：第 2 章背景条目数 ≤ 未收窄参与者基线（C7 生效的形状证据）。
    assert entry['section_entries']['background'] <= entry['baseline_facts']
