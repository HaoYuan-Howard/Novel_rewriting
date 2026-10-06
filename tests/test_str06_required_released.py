#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""test_str06_required_released —— CS-H H3 判据②：必保揭名（RC-61）。

注册判据：`design_requires_released_gate`（stage4，know 拍位闸）与
`body_required_literals`（stage5，承载章正文必现字面＋段落级修句）——
本文件即它们的反例套件。

识别（三项类型化合取：identity／reveal 必保义务引用＋命题证据句段＝带名称谓
首次出现句段＋命题文本含该称谓）→ compiler 写进 Brief 认知变化条目
（`requires_released`，不暴露句段号）；Design 闸（know 拍位必须已释放所列
称谓，同拍释放合法，违例带路径可窄修）；章包必保揭名行与机械 validator
（承载章正文缺字面即拒）；段落级修句（不重发整份章包，按行窄修）。
零付费：纯函数与真实入口（fake answers）。
"""
from __future__ import annotations

from contracts.schema.grounding import (
    ObligationAnchor, PreserveCheckpoint, SourceLineage, SourceObligation,
)
from contracts.schema.policy import PreserveKind
from contracts.schema.source import (
    CharactersCore, NameStructure,
)
from novel.stages.stage4.grounding import project_brief, required_releases
from tests.test_str06_a4_design_locks import _blueprint

H64 = 'a' * 64


def _ident_setup(*, kind=PreserveKind.IDENTITY, must=True, first_segment='e01_01',
                 statement='表弟的名字是裴书远', structure=True):
    """cousin：带名面「裴书远」首次出现于 first_segment，纯称谓「表弟」在别处。

    命题 p_x 的源证据句段＝a4 蓝图 b01_01 认知 basis 的 e01_01（带名面首次
    出现句段与之重合即命中识别②）。"""
    characters = CharactersCore.model_validate({
        'characters': {'cousin': {'canonical': '裴书远', 'surfaces': [
            {'surface': '裴书远', 'from_chapter': 1, 'source_refs': [first_segment]},
            {'surface': '表弟', 'from_chapter': 1, 'source_refs': ['e01_02']}]}},
        'initial_state': []})
    name_structure = NameStructure.model_validate({
        'families': {'fam_pei': {'literal': '裴', 'forms': []}},
        'people': {'cousin': {'family_id': 'fam_pei', 'given': '书远', 'aliases': [],
                              'surface_forms': {'裴书远': '{family}{given}',
                                                '表弟': None}}}})
    anchor = ObligationAnchor(
        handle='b01_01.c1', source_kind='cognition', holder='hero', mode='learns',
        proposition_ref='p_x', proposition_statement=statement,
        quotes=('原来是裴家书远',))
    obligation = SourceObligation(
        obligation_id='ob01', kind=kind, statement=statement, anchors=(anchor,),
        lineage=SourceLineage(blueprint_refs=('b01_01',), character_ids=('cousin',)),
        must_preserve=must, satisfaction='首次点名自然落地')
    checkpoint = PreserveCheckpoint(
        checkpoint_id='cp', policy_id='p', policy_version=1, characters_sha256=H64,
        blueprints_sha256=H64, provider='x', model='y', request_sha256='r' * 64,
        obligations=(obligation,), checkpoint_sha256=H64)
    brief, _ = project_brief(checkpoint, characters, _blueprint(),
                             structure=name_structure if structure else None)
    return brief


def test_requires_released_needs_all_three_typed_signals():
    """三项合取：类别（identity／reveal＋必保）、证据句段＝带名面首次出现、
    命题文本含该称谓——任一不满足即无揭名要求（漏检退化为普通投影）。"""
    change = _ident_setup().requirements[0].knowledge_changes[0]
    assert change.requires_released == ('裴书远',), '三项齐备 ⇒ 必保揭名'
    # 反例：非 identity／reveal 类。
    assert _ident_setup(kind=PreserveKind.EVENT_CORE)\
        .requirements[0].knowledge_changes[0].requires_released == ()
    # 反例：非必保。
    assert _ident_setup(must=False)\
        .requirements[0].knowledge_changes[0].requires_released == ()
    # 反例：带名面首次出现句段不在命题的证据句段集里（命题证据在 e01_01，
    # 首次出现改在 e02_02）。
    assert _ident_setup(first_segment='e02_02')\
        .requirements[0].knowledge_changes[0].requires_released == ()
    # 反例：命题文本不含该称谓。
    assert _ident_setup(statement='表弟的名字是个谜')\
        .requirements[0].knowledge_changes[0].requires_released == ()
    # 无名称结构不判定（带名判定缺输入，不猜）。
    assert _ident_setup(structure=False)\
        .requirements[0].knowledge_changes[0].requires_released == ()
    # IR 映射面：(holder, kp, mode) → 必现字面。
    assert required_releases(_ident_setup()) == {
        ('hero', change.kp, 'known'): ('裴书远',)}


# ───────────────────────── Design 闸：know 拍位必须已释放所列称谓 ─────────────────────────

def _gate_wires():
    from copy import deepcopy
    from tests.test_str06_design_slim import _brief, _obligations, _v8_wire
    brief = _brief()
    change = brief.requirements[0].knowledge_changes[0]
    brief = brief.model_copy(update={'requirements': (
        brief.requirements[0].model_copy(update={'knowledge_changes': (
            change.model_copy(update={'requires_released': ('陆大哥',)}),)}),
        *brief.requirements[1:],)})
    return brief, _obligations(brief), _v8_wire(), deepcopy(_v8_wire())


def _gate_errors(brief, obligations, wire):
    from novel.stages.stage4.design import assemble_body_design
    _body, errors = assemble_body_design(
        wire, known_char_ids={'lu_fujun', 'yun_niang', 'pei_shuyuan'},
        expression_requirements=brief.expression,
        source_narration=brief.source_narration, brief=brief, obligations=obligations)
    return errors


def test_know_before_release_is_rejected_with_a_patchable_path():
    """判据②-①：know 早于释放被拒（design_requires_released），带 know 行
    wire 路径——定位器给出条目返修目标（挪 know 拍位或提前释放）。"""
    from novel.stages.stage4.design_paths import locate_design_patches, wire_paths_of
    brief, obligations, wire, later = _gate_wires()
    # 陆大哥在下一场才释放：本拍 know 早于释放。
    later['scenes'][1]['beats'][0]['release'] = [{'char_id': 'lu_fujun',
                                                  'surfaces': ['陆大哥']}]
    errors = [e for e in _gate_errors(brief, obligations, later)
              if getattr(e, 'code', None) == 'design_requires_released']
    assert errors, [str(e) for e in _gate_errors(brief, obligations, later)]
    assert wire_paths_of(errors[0]) == (('scenes', 0, 'beats', 0, 'know', 0),)
    located = locate_design_patches(later, errors)
    assert located, '可窄修：know 行是最小可替换单元'
    assert located[0][0] == ('scenes', 0, 'beats', 0, 'know', 0)


def test_same_beat_release_passes_and_book_wide_absence_is_rejected():
    """判据②-②③：同拍释放合法（本拍 release 引入即可通过）；全书未释放仍拒。"""
    brief, obligations, same_beat, nowhere = _gate_wires()
    same_beat['scenes'][0]['beats'][0]['release'] = [{'char_id': 'lu_fujun',
                                                      'surfaces': ['陆大哥']}]
    codes = [getattr(e, 'code', None) for e in _gate_errors(brief, obligations, same_beat)]
    assert 'design_requires_released' not in codes, codes
    codes = [getattr(e, 'code', None) for e in _gate_errors(brief, obligations, nowhere)]
    assert 'design_requires_released' in codes, '全书未释放该称谓 ⇒ 拒'


def test_chapter_ir_carries_requires_released_for_the_carrier_chapter():
    """章包与正文检查面：认知增量落章 ⇒ 承载章 requires_released 必现字面随
    ChapterIR 携带并进「必保揭名」行；validator 缺字面即拒。"""
    from novel.stages.stage4.book_ir import compile_writer_book_ir
    from novel.stages.stage4.compile import compile_execution_plan
    from novel.stages.stage5.body_checks import validate_chapter_prose
    from novel.stages.common import project_chapter_packet, render_chapter_packet
    from tests.test_str06_body import _green_book_ir
    body, binding, plan, plain_ir = _green_book_ir()
    from tests.test_str06_hook import _characters
    chars = _characters()
    ir = compile_writer_book_ir(
        graph=body, plan=plan, binding=binding,
        total_budget=plain_ir.book_brief.total_length_budget,
        length_band=(plain_ir.book_brief.length_band_low,
                     plain_ir.book_brief.length_band_high),
        projection=chars.name_projection(), structure=chars.name_structure,
        required_releases={('yun_niang', 'vow', 'known'): ('苏芸娘',)})
    assert ir.chapters[0].requires_released == ('苏芸娘',)
    packet = project_chapter_packet(ir, 1)
    assert '必保揭名：本章正文必须出现称谓字面「苏芸娘」' in render_chapter_packet(packet)
    assert '[required_literals]' in '；'.join(validate_chapter_prose(
        '夫君立下旧约便走了。', withheld_literals=packet.chapter.withheld_literals,
        released_surfaces=packet.chapter.released_surfaces, target_chars=100,
        band_low=1, band_high=1000, required_literals=('苏芸娘',)))
    assert '[required_literals]' not in '；'.join(validate_chapter_prose(
        '苏芸娘听夫君立下旧约。', withheld_literals=packet.chapter.withheld_literals,
        released_surfaces=packet.chapter.released_surfaces, target_chars=100,
        band_low=1, band_high=1000, required_literals=('苏芸娘',)))


def test_missing_literal_gets_a_paragraph_level_repair_round(tmp_path):
    """判据②-④：承载章正文缺该字面被拒并走**段落级修句**——不重发整份章包，
    按行编号窄修；合并稿重跑同一套机械检查后通过。"""
    from novel.stages.stage5.body import write_chapter
    from novel.stages.common import project_chapter_packet
    from tests.test_str06_body import _FakeAnswers, _green_book_ir, _answered
    _body, _binding, _plan, ir = _green_book_ir()
    packet = project_chapter_packet(ir, 1).model_copy(update={'chapter': (
        project_chapter_packet(ir, 1).chapter.model_copy(
            update={'requires_released': ('苏芸娘',)}))})
    # 章预算 1000、篇幅带 [600,1300]：初稿与修句稿都须在带内。
    draft = '洞房花烛，夫君立下旧约，句句克制。' * 36
    assert '苏芸娘' not in draft
    repaired = '洞房花烛，苏芸娘听夫君立下旧约，句句克制。' * 33
    patch = ('{"paragraphs": [{"index": 0, "text": "' + repaired + '"}]}')
    fake = _FakeAnswers([_answered(draft), _answered(patch, identity='id-2')])
    outcome = write_chapter(packet=packet, ask=fake)
    assert outcome.prose == repaired and outcome.receipt.status == 'accepted'
    assert '[required_literals]' in '；'.join(outcome.attempts[0].errors)
    assert outcome.attempts[1].errors == ()
    # 窄输入：修句请求不重发整份章包（不含写手协议），只带失败要求与按行当前稿。
    assert '【段落修句】' in fake.prompts[1] and '〔当前稿〕' in fake.prompts[1]
    assert not fake.prompts[1].startswith(fake.prompts[0].rstrip('\n'))
    assert '【必须写出】' not in fake.prompts[1]
    # 绿对照：首答已含字面即通过，无修句轮。
    fake = _FakeAnswers([_answered(repaired)])
    outcome = write_chapter(packet=packet, ask=fake)
    assert outcome.receipt.status == 'accepted' and len(outcome.attempts) == 1


def test_unparseable_paragraph_patch_is_a_failed_attempt_not_a_candidate():
    """段落修句回答不可解析 ⇒ 记一次失败留证；下一轮退整份重写，缺字面稿与
    解析失败稿都不进候选、不放行。"""
    from novel.stages.stage5.body import write_chapter
    from novel.stages.common import project_chapter_packet
    from tests.test_str06_body import _FakeAnswers, _green_book_ir, _answered
    _body, _binding, _plan, ir = _green_book_ir()
    packet = project_chapter_packet(ir, 1).model_copy(update={'chapter': (
        project_chapter_packet(ir, 1).chapter.model_copy(
            update={'requires_released': ('苏芸娘',)}))})
    draft = '洞房花烛，夫君立下旧约，句句克制。' * 36
    fixed = '洞房花烛，苏芸娘听夫君立下旧约，句句克制。' * 33
    fake = _FakeAnswers([_answered(draft), _answered('不是 JSON', identity='id-2'),
                         _answered(fixed, identity='id-3')])
    outcome = write_chapter(packet=packet, ask=fake)
    assert outcome.prose == fixed and outcome.receipt.status == 'accepted'
    assert '[paragraph_patch]' in '；'.join(outcome.attempts[1].errors), '解析失败如实留证'
    assert '【段落修句】' in fake.prompts[1] and '【重写要求】' in fake.prompts[2], \
        '修句轮后回退整份重写路径'
