#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""test_str06_stage4_protocol —— Stage4 四协议独立版化与 B3 前半契约反例。

【验证边界】四个付费语义节点的 wire 协议版本逐字对账（漏带/错带即拒，I-13）；
Design wire 瘦身的 compiler 派生面（场景/拍 ID、章切片、权重归一、跨章锚定，
I-21）；TargetDesign 结构化跨章字段与人物闭集；读者确认的事实地址（D4：
漏填即拒、悬空即拒、确认不早于建立、按地址投影）；章预算的最大余数传导
（精确失效表：权重变化按整数预算变化波及多章）；Bind 输入投影的最小闭集。
真实付费链路语义质量归 B6，不在本文件伪造。
"""
from __future__ import annotations

import pytest
from pydantic import ValidationError

from contracts.schema.design import (
    BodyDesign, ChapterAssignment, ChapterEntry, SceneDelivery,
    SceneDriver, TargetBeat, TargetDelta, TargetScene, ThreadAction,
)
from contracts.schema.grounding import PreserveCheckpoint, SourceLineage, SourceObligation
from contracts.schema.policy import PreserveKind
from contracts.schema.state import StateAddress
from contracts.schema.workflow import (
    STAGE4_DESIGN_PROTOCOL, STAGE4_HOOK_DESIGN_PROTOCOL,
    STAGE4_PRESERVE_PROTOCOL, STR06_RC_VERSION,
)
from tests.test_str06_hook import _characters
from novel.stages.stage4.book_ir import (
    allocate_chapter_budgets, chapters_with_budget_change, compile_writer_book_ir,
)
from novel.stages.stage4.compile import compile_execution_plan
from novel.stages.stage4.design import assemble_body_design
from novel.stages.stage4.grounding import (
    assemble_preserve, derive_semantic_binding,
)
from novel.stages.stage4.hook import assemble_hook_design
from tests.str06_expression_inputs import (
    sample_narration, sample_requirements, sample_design_expression, sample_canonical_expression,
)
from tests.str06_facilities import anchor_stub

H64 = 'a' * 64


# ───────────────────────── 构造器 ─────────────────────────

def _wire() -> dict:
    """两场三拍的 design wire v2：章切片、相对权重、线程、world_rules、entry
    与 reader 确认（四个静态跨章列表已退役，AUD-04）。"""
    return {**sample_design_expression(('yun',)),
        'protocol_version': STAGE4_DESIGN_PROTOCOL,
        'book_style': '冷峻克制的第三人称叙事，重日常细节里的异样。',
        'character_voices': [{'char_id': 'yun', 'voice': '短句、少感叹'}],
        'world_rules': ['术法须以寿命为代价'],
        'initial_state': [
            {'domain': 'character', 'subjects': ['yun'], 'holder': None,
             'key': 'marital_status', 'op': 'set', 'modality': 'actual', 'value': '新婚'}],
        'initial_surfaces': [{'char_id': 'yun', 'surfaces': ['云娘']}],
        'scenes': [
            {'state_reads': [],
             'driver': {'focal_actor': 'yun', 'immediate_want': '安度新婚',
                        'opposition': '夫君的怪异', 'turn': '立约'},
             'realization_brief': '重心在婚礼到立约的落差；展开洞房对坐，在旧约处收住。',
             'beats': [
                 {'event': '拜堂', 'participants': ['yun', 'fu_jun'],
                  'delivery_hint': '', 'target_delta': [
                      {'domain': 'relation', 'subjects': ['yun', 'fu_jun'],
                       'op': 'set', 'modality': 'actual', 'scope': 'book', 'key': 'marital_status',
                       'value': '夫妻'}], 'delivery_hint': ''},
                 {'event': '夫君立约', 'participants': ['yun', 'fu_jun'],
                  'delivery_hint': '', 'target_delta': [],
                  'threads': [{'op': 'open', 'tag': 't1', 'kind': 'question',
                               'subject': 'yun', 'text': '玉佩上的字是谁刻的'},
                              {'op': 'open', 'tag': 't2', 'kind': 'promise',
                               'subject': 'fu_jun', 'text': '再不隐瞒'}]},
                 {'event': '云娘起疑', 'participants': ['yun'],
                  'delivery_hint': '', 'target_delta': [
                      {'domain': 'knowledge', 'subjects': [], 'holder': 'reader',
                       'op': 'set', 'scope': 'scene', 'key': 'debt',
                       'value': 'true', 'knowledge_mode': 'known',
                       'confirms': {'domain': 'relation', 'subjects': ['yun', 'fu_jun'], 'key': 'marital_status'}}],
                  'delivery_hint': ''}],
             'delivery': {'pov': 'yun', 'voice': '冷峻', 'avoid': []}},
            {'state_reads': [{'domain': 'relation', 'subjects': ['yun', 'fu_jun'],
                              'key': 'marital_status'}],
             'driver': {'focal_actor': 'yun', 'immediate_want': '求证',
                        'opposition': '无凭据', 'turn': '发现玉佩'},
             'realization_brief': '重心在玉佩的发现；压缩等待，在字面露出时收束。',
             'beats': [
                 {'event': '翻检妆匣', 'participants': ['yun'],
                  'delivery_hint': '', 'target_delta': []},
                 {'event': '发现玉佩刻字', 'participants': ['yun'],
                  'delivery_hint': '', 'target_delta': [],
                  'threads': [{'op': 'close', 'ref': 't1', 'ending': 'answered',
                               'resolution': '刻着沈字，指向旧案'}]}],
             'delivery': {'pov': 'yun', 'voice': '冷峻', 'avoid': []}}],
        'chapters': [{'purpose': '新婚之夜埋下旧约之疑', 'scenes': 1},
                     {'purpose': '玉佩之谜浮出水面', 'scenes': 1,
                      'entry': {'transition': 'time_skip',
                                'carryover': '云娘的疑心未消',
                                'recap': 'brief_orientation'}}],
        'scene_weights': [2, 1],
    }


def _green() -> BodyDesign:
    body, errors = assemble_body_design(_wire(), known_char_ids={'yun', 'fu_jun'}, expression_requirements=sample_requirements(), source_narration=sample_narration())
    assert body is not None and not errors, errors
    return body


def _checkpoint(*obligation_ids: str) -> PreserveCheckpoint:
    return PreserveCheckpoint(
        checkpoint_id='cp1', policy_id='p', policy_version=1,
        characters_sha256=H64, blueprints_sha256=H64, provider='x', model='y',
        request_sha256='r' * 64,
        obligations=tuple(SourceObligation(
            obligation_id=oid, kind=PreserveKind.EVENT_CORE, statement=f'义务{oid}',
            anchors=anchor_stub(),
            lineage=SourceLineage(blueprint_refs=('b01_01',), character_ids=('yun',)),
            must_preserve=True, satisfaction='正文呈现')
            for oid in obligation_ids),
        checkpoint_sha256=H64)


# ───────────────────────── 四协议独立版化 ─────────────────────────

def test_protocols_are_independent_literals():
    """每个付费语义节点一个独立协议字面量：演进互不牵连（B3 前半；
    CS-3 bind 退役——承载位并入 design wire v6 的 real 标注）。"""
    assert STAGE4_PRESERVE_PROTOCOL == 'stage4-preserve-v5'
    assert STAGE4_DESIGN_PROTOCOL == 'stage4-design-v5'
    assert STAGE4_HOOK_DESIGN_PROTOCOL == 'stage4-hook-design-v3'
    assert len({STAGE4_PRESERVE_PROTOCOL, STAGE4_DESIGN_PROTOCOL,
                STAGE4_HOOK_DESIGN_PROTOCOL}) == 3
    # RC 版式头随 R2 语义送达升 rc10（R1 关联无损曾升 rc9）
    assert STR06_RC_VERSION == 'str06-rc23'  # Hook 独立升版；正文协议不变。


def test_design_wire_without_protocol_is_refused():
    bad = _wire()
    del bad['protocol_version']
    _, errors = assemble_body_design(bad, known_char_ids={'yun', 'fu_jun'}, expression_requirements=sample_requirements(), source_narration=sample_narration())
    assert any('protocol_version' in e for e in errors)


def test_hook_design_wire_without_protocol_is_refused():
    body = _green()
    from tests.test_str06_hook import _characters
    projection = _characters().name_projection()
    _, errors = assemble_hook_design(
        {'pov': {'person': 'first', 'focalizers': ['yun']},
         'materials': [{'beat_ref': {'scene': 'sc01', 'beat': 'b02'}, 'withheld': []}],
         'forbidden_reveals': [], 'design_brief': '组织。', 'delivery_brief': '一句话。'},
        body=body, known_char_ids={'yun', 'fu_jun'}, name_projection=projection)
    assert any('protocol_version' in e for e in errors)


# ───────────────────────── compiler 生成 ID／归章／权重（I-21） ─────────────

def test_compiler_dispatches_ids_slices_chapters_and_normalizes_weights():
    body = _green()
    # 场景/拍 ID 按列表序派发
    assert [s.scene_id for s in body.scenes] == ['sc01', 'sc02']
    assert [b.beat_id for b in body.scenes[0].beats] == ['b01', 'b02', 'b03']
    assert [b.beat_id for b in body.scenes[1].beats] == ['b01', 'b02']
    # 章切片：章号连续、场景按阅读序切分
    assert [(c.chapter_ordinal, c.scene_ids) for c in body.chapters] == [
        (1, ('sc01',)), (2, ('sc02',))]
    # 权重归一：相对重点 [2,1] → 和为 1
    assert body.chapter_weights == {'sc01': 2 / 3, 'sc02': 1 / 3}
    # 整数预算总和恰等于书级目标（largest remainder）
    budgets = allocate_chapter_budgets(body, 1000)
    assert sum(budgets.values()) == 1000
    assert budgets == {1: 667, 2: 333}


def test_thread_tags_dispatch_to_canonical_refs():
    """线程本地标签由 compiler 按 open 先后派发 `th{nn}`（I-21）。"""
    body = _green()
    threads = [t for s in body.scenes for b in s.beats for t in b.threads]
    assert [(t.ref, t.op) for t in threads] == [
        ('th01', 'open'), ('th02', 'open'), ('th01', 'close')]
    assert threads[0].kind == 'question' and threads[0].subject == 'yun'
    assert threads[2].ending == 'answered'
    # world_rules 与 entry 原样落 canonical（第 1 章固定开篇）
    assert body.world_rules == ('术法须以寿命为代价',)
    assert body.chapters[0].entry == ChapterEntry.opening()
    assert body.chapters[1].entry.carryover == '云娘的疑心未消'


def test_thread_wire_bad_references_are_refused():
    bad = _wire()
    bad['scenes'][1]['beats'][1]['threads'][0]['ref'] = 't_unknown'
    _, errors = assemble_body_design(bad, known_char_ids={'yun', 'fu_jun'}, expression_requirements=sample_requirements(), source_narration=sample_narration())
    assert any('未开启' in e for e in errors)
    bad = _wire()
    bad['scenes'][0]['beats'][1]['threads'][1]['tag'] = 't1'   # 重复开标签
    _, errors = assemble_body_design(bad, known_char_ids={'yun', 'fu_jun'}, expression_requirements=sample_requirements(), source_narration=sample_narration())
    assert any('重复开启' in e for e in errors)
    bad = _wire()
    bad['scenes'][1]['beats'][1]['threads'][0]['ending'] = 'fulfilled'
    _, errors = assemble_body_design(bad, known_char_ids={'yun', 'fu_jun'}, expression_requirements=sample_requirements(), source_narration=sample_narration())
    assert any('闭集' in e or '构造失败' in e for e in errors), errors


def test_retired_static_lists_are_refused_on_wire():
    """AUD-04：四个静态跨章列表退役——wire 旧键关闭式拒读（I-13 无迁移）。"""
    bad = _wire()
    bad['standing_goals'] = [{'char_id': 'yun', 'goal': '查清夫君的底细'}]
    _, errors = assemble_body_design(bad, known_char_ids={'yun', 'fu_jun'}, expression_requirements=sample_requirements(), source_narration=sample_narration())
    assert any('standing_goals' in e and 'Extra inputs' in e for e in errors)


def test_cross_chapter_char_ids_must_be_known():
    bad = _wire()
    bad['character_voices'] = [{'char_id': 'ghost', 'voice': 'v'}]
    _, errors = assemble_body_design(bad, known_char_ids={'yun', 'fu_jun'}, expression_requirements=sample_requirements(), source_narration=sample_narration())
    assert any('CharacterVoice 引用未知 char_id' in e for e in errors)
    bad = _wire()
    bad['scenes'][0]['beats'][1]['threads'][0]['subject'] = 'ghost'
    _, errors = assemble_body_design(bad, known_char_ids={'yun', 'fu_jun'}, expression_requirements=sample_requirements(), source_narration=sample_narration())
    assert any('ThreadAction 引用未知 char_id ghost' in e for e in errors)


def test_book_style_is_required_on_canonical():
    """整书风格是 TargetDesign 的必填字段：缺省即拒，不用空串兜底。"""
    with pytest.raises(ValidationError):
        BodyDesign(**sample_canonical_expression(('ch1',)),
            graph_id='g', book_style='', initial_state=(),
            scenes=(TargetScene(
                scene_id='s01', state_reads=(),
                driver=SceneDriver(focal_actor='ch1', immediate_want='w',
                                   opposition='o', turn='t'),
                realization_brief='重心；收束。',
                beats=(TargetBeat(beat_id='b01', event='e', participants=('ch1',),
                                  delivery_hint=''),),
                delivery=SceneDelivery(pov='ch1', voice='v')),),
            chapters=(ChapterAssignment(chapter_ordinal=1, purpose='p',
                                        scene_ids=('s01',)),))


def test_book_style_and_voices_project_into_book_brief_and_packet():
    """书级条目随 BookBrief 进每章请求（不再只写在导演说明里）。"""
    body = _green()
    checkpoint = _checkpoint('ob01')
    from contracts.schema.grounding import BindingEntry, SemanticBinding
    binding = SemanticBinding(
        binding_id='b1', preserve_checkpoint_sha256=H64,
        obligations=checkpoint.obligations,
        entries=(BindingEntry(obligation_id='ob01', scene_id='sc01',
                              beat_ref='sc01#b02'),),
        unbound=())
    plan, errors = compile_execution_plan(body, binding)
    assert not errors, errors
    ir = compile_writer_book_ir(graph=body, plan=plan, binding=binding,
                                total_budget=1000, length_band=(0.6, 1.3),
                                book_title='t',
                                projection=_characters().name_projection(),
                                structure=None)
    assert ir.book_brief.book_style.startswith('冷峻克制')
    assert ir.book_brief.character_voices[0].char_id == 'yun'
    from novel.stages.common import project_chapter_packet, render_chapter_packet
    packet = render_chapter_packet(project_chapter_packet(ir, 1))
    # v5 分区渲染：书级风格与声线进「导演说明」分区
    assert '【导演说明】' in packet
    assert '冷峻克制的第三人称叙事' in packet
    assert '声线：短句、少感叹' in packet


# ──────────────── 读者认知统一 knowledge(holder=reader)（D5，rc13） ────────────────

def test_legacy_reader_domain_and_confirms_are_refused():
    """D5 统一：旧 reader 域整域关闭（枚举闭集）、`confirms` 键 extra 即拒。

    读者揭示统一为 knowledge(holder=reader)（模式取知情锁）；确认时序管控
    归 pending 投影（读者知情地址）与锁位置 Guard——confirms 地址面不再存在。
    """
    with pytest.raises(ValidationError, match='domain'):
        TargetDelta(domain='reader', subjects=(), key='hiding', op='set',
                    scope='scene', value='床底有人')
    with pytest.raises(ValidationError, match='confirms'):
        TargetDelta(modality='actual', domain='world', subjects=(), key='hiding',
                    op='set', scope='book', value='床底有人',
                    confirms=StateAddress(domain='world', key='hiding'))


def test_reader_reveal_is_knowledge_with_reader_holder():
    """统一后的读者揭示形状：knowledge 域、holder=reader、模式取锁（known）。"""
    body = _green()
    row = body.scenes[0].beats[2].target_delta[0]
    assert (row.domain.value, row.holder, row.knowledge_mode, row.key) == (
        'knowledge', 'reader', 'known', 'debt')


def test_reader_confirmation_is_projected_by_touched_proposition():
    """D5 投影维度：确认只投影进实际动该命题（kp）的场景（读取或写入）。"""
    from novel.stages.common import pending_reader_confirmations
    body = _green()
    # sc01 写下 reader 确认行（kp=debt）⇒ 本场在清单（确认拍在本场，in_scene）
    first = pending_reader_confirmations(body, 0)
    assert [(p.key, p.position, p.in_scene) for p in first] == [
        ('debt', 'sc01#b03', True)]
    assert first[0].address.address() == ('relation', ('yun', 'fu_jun'), '', 'marital_status')
    # 确认已发生在过去（sc01） ⇒ sc02 起不再列入（时序维度，D15）
    assert pending_reader_confirmations(body, 1) == ()
    # 不动该命题的场景 ⇒ 清单为空（按命题的精确投影）
    sliced = body.model_copy(update={'scenes': (body.scenes[1],),
                                     'chapters': (ChapterAssignment(
                                         chapter_ordinal=1, purpose='p',
                                         scene_ids=('sc02',),
                                         entry=ChapterEntry.opening()),)})
    sliced.scenes[0].state_reads = ()
    assert pending_reader_confirmations(sliced, 0) == ()


# ───────────────────────── 章预算的最大余数传导 ─────────────────────────

def _canonical_with_weights(weights: dict[str, float]) -> BodyDesign:
    return _green().model_copy(update={'chapter_weights': weights})


def test_budget_change_propagates_by_integer_allocation():
    """精确失效表：场景权重变化按整数预算变化传导，可能波及多章。"""
    old = _canonical_with_weights({'sc01': 0.5, 'sc02': 0.5})
    new = _canonical_with_weights({'sc01': 0.8, 'sc02': 0.2})
    # 1000：等权 (500, 500) → 加权 (800, 200)：两章预算都变
    assert allocate_chapter_budgets(old, 1000) == {1: 500, 2: 500}
    assert allocate_chapter_budgets(new, 1000) == {1: 800, 2: 200}
    assert chapters_with_budget_change(old, new, 1000) == {1, 2}
    # 余数重分配：等权 1001 时低章号拿余数；把 sc01 权重微调到 0.6 后余数归 sc02
    near = _canonical_with_weights({'sc01': 0.6, 'sc02': 0.4})
    assert allocate_chapter_budgets(old, 1001) == {1: 501, 2: 500}
    assert allocate_chapter_budgets(near, 1001) == {1: 601, 2: 400}
    assert chapters_with_budget_change(old, near, 1001) == {1, 2}
    # 权重不变 ⇒ 无章受影响
    assert chapters_with_budget_change(old, old.model_copy(), 1001) == set()
    # 只有部分章变化：大幅倾斜时小权重章预算可能保持
    assert chapters_with_budget_change(old, new, 10) <= {1, 2}


# ───────────────────────── Bind 输入投影（最小承载选择面） ────────────────

def test_binding_supporting_refs_compose_and_stay_in_the_denominator():
    """支撑拍（非末次标注拍）→ 复合引用进 entries；标注拍越界精确拒。"""
    from tests.test_str06_execution_ir import _graph
    checkpoint = _checkpoint('ob01')
    body = _graph()          # s01#bt1、s02#bt1 两拍
    placements = {'ob01': [(1, 0), (0, 0)]}   # 支撑 s02#bt1＋接受 s01#bt1
    binding, errors = derive_semantic_binding(placements, body, checkpoint)
    assert not errors, errors
    entry = binding.entries[0]
    assert entry.beat_ref == 's01#bt1'
    assert entry.supporting_beat_refs == ('s02#bt1',)

    _, errors = derive_semantic_binding({'ob01': [(0, 9)]}, body, checkpoint)
    assert any('越界' in e for e in errors)
