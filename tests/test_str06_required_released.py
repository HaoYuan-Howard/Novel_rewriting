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
from novel.stages.common import BODY_PARAGRAPH_REPAIR_SYSTEM
from novel.stages.stage4.grounding import project_brief, required_releases
from tests.str06_facilities import name_map_of
from tests.test_str06_a4_design_locks import _blueprint

H64 = 'a' * 64


class _FakeAnswers:
    """rc26：正文回答是普通目标文本（identity-text 协议退役）——不做标记包装。

    与 `tests/test_str06_body.py::_FakeAnswers` 同形；那里仍经 `_declared_prose`
    注入身份文本标记，普通正文主链下会踩 `no_wrapper`，故本文件自持一份。
    """

    def __init__(self, answers):
        self.answers = list(answers)
        self.prompts = []
        self.validators = []
        self.systems = []

    def __call__(self, prompt, validate, system=None):
        self.prompts.append(prompt)
        self.validators.append(validate)
        self.systems.append(system)
        return self.answers.pop(0)


def _answered(text, finish_reason=None, identity='id'):
    from novel.stages.stage5.body import AnsweredText
    return AnsweredText(text=text, finish_reason=finish_reason, identity=identity,
                        cached=False)


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
    checkpoint = PreserveCheckpoint(name_map_sha256='n' * 64,
        checkpoint_id='cp', policy_id='p', policy_version=1, characters_sha256=H64,
        blueprints_sha256=H64, provider='x', model='y', request_sha256='r' * 64,
        obligations=(obligation,), checkpoint_sha256=H64)
    brief, _ = project_brief(checkpoint, characters, _blueprint(),
                             structure=name_structure if structure else None,
name_map=name_map_of(characters))
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
    from tests.test_str06_design_slim import _brief, _obligations, _v9_wire
    brief = _brief()
    change = brief.requirements[0].knowledge_changes[0]
    brief = brief.model_copy(update={'requirements': (
        brief.requirements[0].model_copy(update={'knowledge_changes': (
            change.model_copy(update={'requires_released': ('陆大哥',)}),)}),
        *brief.requirements[1:],)})
    return brief, _obligations(brief), _v9_wire(), deepcopy(_v9_wire())


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
        required_releases={('yun_niang', 'intent', 'known'): ('苏芸娘',)},
        name_map=name_map_of(chars))
    assert ir.chapters[0].requires_released == ('苏芸娘',)
    packet = project_chapter_packet(ir, 1)
    assert '必保揭名：本章正文必须出现称谓字面「苏芸娘」' in render_chapter_packet(packet)
    # rc26：正文是普通目标文本——机械验只按纯文本判定必现字面，无 withheld/
    # released/name_surfaces 参（R-7.8）。
    assert '[required_literals]' in '；'.join(validate_chapter_prose(
        '夫君立下旧约便走了。', target_chars=100,
        band_low=1, band_high=1000, required_literals=('苏芸娘',),
        scene_markers=(packet.chapter.scenes[0].scene_id,)))
    assert '[required_literals]' not in '；'.join(validate_chapter_prose(
        '苏芸娘听夫君立下旧约。', target_chars=100,
        band_low=1, band_high=1000, required_literals=('苏芸娘',),
        scene_markers=(packet.chapter.scenes[0].scene_id,)))


def test_missing_literal_gets_a_paragraph_level_repair_round(tmp_path):
    """判据②-④：承载章正文缺该字面被拒并走**段落级修句**——不重发整份章包，
    按行编号窄修；合并稿重跑同一套机械检查后通过。"""
    from novel.stages.stage5.body import write_chapter
    from novel.stages.common import project_chapter_packet
    from tests.test_str06_body import _green_book_ir
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
    # 修句轮用专用 system 提示（写手 system 要求「只输出完整正文」，与 JSON 输出冲突）；
    # 首轮不带 system，沿用写手默认。
    assert fake.systems == [None, BODY_PARAGRAPH_REPAIR_SYSTEM]
    # 付费边界收到的修句轮校验器验的是**合并后**的稿，不是行替换 JSON 本身。
    assert fake.validators[1](patch) == []
    assert '[required_literals]' in '；'.join(fake.validators[1](
        '{"paragraphs": [{"index": 0, "text": "' + draft[:5] + '"}]}'))
    assert '[paragraph_patch]' in '；'.join(fake.validators[1]('不是 JSON'))
    # 绿对照：首答已含字面即通过，无修句轮。
    fake = _FakeAnswers([_answered(repaired)])
    outcome = write_chapter(packet=packet, ask=fake)
    assert outcome.receipt.status == 'accepted' and len(outcome.attempts) == 1


def test_unparseable_paragraph_patch_is_a_failed_attempt_not_a_candidate():
    """段落修句回答不可解析 ⇒ 记一次失败留证；下一轮退整份重写，缺字面稿与
    解析失败稿都不进候选、不放行。"""
    from novel.stages.stage5.body import write_chapter
    from novel.stages.common import project_chapter_packet
    from tests.test_str06_body import _green_book_ir
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
    # 整份重写的原因同时带上解析失败与原有的缺字面问题（不能只剩解析失败：重写者
    # 要知道为什么要重写）。
    assert '[paragraph_patch]' in fake.prompts[2] and '[required_literals]' in fake.prompts[2]
    assert fake.systems == [None, BODY_PARAGRAPH_REPAIR_SYSTEM, None]


def test_paragraph_repair_carries_the_released_name_directory_and_packet_identity():
    """rc26：段落修句窄请求带本章已释放的目标姓名字面目录与章包身份——
    首次补揭名不依赖上一稿的标记片段；未释放候选与整份章包都不进窄请求。"""
    import hashlib
    import json

    from novel.stages.common import chapter_identities, project_chapter_packet
    from novel.stages.stage5.body import write_chapter
    from tests.test_str06_body import _green_book_ir

    _body, _binding, _plan, ir = _green_book_ir()
    packet = project_chapter_packet(ir, 1)
    packet = packet.model_copy(update={'chapter': packet.chapter.model_copy(update={
        'requires_released': ('苏芸娘',)})})
    expected_directory = {row.char_id: list(row.surfaces) for row in packet.chapter.released_names}
    draft_plain = '她在窗前守着旧约，烛火摇了三摇。' * 40
    draft = draft_plain
    assert '苏芸娘' not in draft and '[[' not in draft
    prompts = []

    def ask(prompt, validate, *, system=None):
        prompts.append(prompt)
        if len(prompts) == 1:
            assert system is None
            errors = validate(draft)
            assert errors and all(error.startswith('[required_literals]') for error in errors)
            return _answered(draft, identity='draft')
        assert len(prompts) == 2 and system == BODY_PARAGRAPH_REPAIR_SYSTEM
        assert '【必须写出】' not in prompt
        directory_json = prompt.split('【可用称谓】\n', 1)[1].split('\n〔当前稿〕\n', 1)[0]
        assert directory_json == '、'.join(sorted(
            {s for row in packet.chapter.released_names for s in row.surfaces}))
        directory = json.loads(json.dumps(expected_directory, ensure_ascii=False))
        identities = chapter_identities(packet)
        repair_identity = json.loads(prompt.split('【段落修句身份】', 1)[1].split('\n【段落修句】', 1)[0])
        assert repair_identity == {
            'chapter_ordinal': packet.chapter.chapter_ordinal,
            'base_text_sha256': hashlib.sha256(draft.encode('utf-8')).hexdigest(),
            'writer_view_sha256': identities.writer_view_sha256,
            'validation_sha256': identities.validation_sha256}
        owners = [cid for cid, faces in directory.items() if '苏芸娘' in faces]
        assert len(owners) == 1
        fixed = '苏芸娘' + draft_plain[1:]
        patch = json.dumps({'paragraphs': [{'index': 0, 'text': fixed}]}, ensure_ascii=False)
        assert validate(patch) == [], '付费边界仍按合并稿校验'
        return _answered(patch, identity='repair')

    outcome = write_chapter(packet=packet, ask=ask)
    assert outcome.prose == '苏芸娘' + draft_plain[1:] and '[[' not in outcome.prose
    assert outcome.receipt.status == 'accepted' and len(outcome.attempts) == 2
    assert outcome.attempts[1].errors == ()


def test_one_chapter_validator_serves_every_consumer_of_the_mechanical_gate():
    """采用／补丁预检／复核捕获与写章循环必须是同一把尺：缺必保揭名字面的 wire
    不能先被采用面放行、再在写章循环里被拒（此前三处各自拼参数且漏带
    `required_literals` 与 `scene_markers`）。"""
    import pathlib
    from novel.stages.common import project_chapter_packet
    from novel.stages.stage5 import chapter_prose_validator
    from tests.test_str06_body import _green_book_ir
    _body, _binding, _plan, ir = _green_book_ir()
    packet = project_chapter_packet(ir, 1)
    packet = packet.model_copy(update={'chapter': packet.chapter.model_copy(
        update={'requires_released': ('苏芸娘',)})})
    validate = chapter_prose_validator(packet)
    body = '洞房花烛，夫君立下旧约，句句克制。' * 36
    assert '[required_literals]' in '；'.join(validate(body)), '缺必保揭名字面被拒'
    # rc26：普通目标正文无协议标记；`[[` 残片由 no_wrapper 拦（R-7.2）。
    assert '[no_wrapper]' in '；'.join(validate('[[name:yun_niang|苏芸娘]]' + body)), \
        '身份文本标记残片被拒'
    assert all('[required_literals]' not in e for e in validate('苏芸娘' + body)), \
        '必现字面已在正文即过该项'
    # 静态守卫：stage5 之外不再直接拼 `validate_chapter_prose` 参数。
    root = pathlib.Path(__file__).resolve().parent.parent
    offenders = [str(path.relative_to(root)) for base in ('novel', 'tools')
                 for path in (root / base).rglob('*.py')
                 if 'stages/stage5' not in path.as_posix()
                 and 'validate_chapter_prose(' in path.read_text(encoding='utf-8')]
    assert not offenders, f'章机械校验须经 chapter_prose_validator 构造：{offenders}'
