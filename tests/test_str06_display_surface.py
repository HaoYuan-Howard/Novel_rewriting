#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""test_str06_display_surface —— CS-H H3 判据①：显示面、投影与残余归属。

规格（`docs/schema/repair_convergence.md` CS-H B 节，RC-60）的反例与绿对照：
  · 显示面解析函数四条规则（聚焦角色词／释放顺序首个／canonical-纯称谓回落／
    最早登记纯称谓回落）与 concealed 不回落；
  · 释放前缀保序、覆盖全体人物（被提及未出场人物投影——丈夫不再被叫作「哥」）；
  · 同一人物自然混称逐次投影与同位语折叠；裸姓不投影；
  · 无显示面残余归 Design（`design_unreleased_reference`，带路径）。
零付费：纯函数与真实 compile 入口。
"""
from __future__ import annotations

from contracts.schema.state import WithheldNameBinding
from novel.core.name_materials import (
    FIRST_PERSON_ROLE_WORD, appellation_fallback, appellation_fallbacks,
    display_surface,
)
from novel.stages.common import (
    ReleasePrefixes, project_withheld_names, render_chapter_packet,
)
from tests.str06_facilities import name_map_of
from tests.test_str06_hook import _characters


def _faces(chars) -> dict:
    """给定 Characters 的目标字面表（全部保留替身：源字面与目标字面一致）。"""
    from novel.core.name_materials import target_face_table
    faces, _errors = target_face_table(chars.name_structure, name_map_of(chars))
    return faces


# ───────────────────────── 显示面解析函数（RC-60 四条规则） ─────────────────────────

def test_display_surface_rules_in_order():
    """①聚焦角色词 → ②释放顺序首个 → ③canonical 纯称谓 → 最早登记纯称谓 → 无。"""
    chars = _characters().core_view()
    structure = _characters().name_structure
    # ②释放顺序首个：陆大哥先释放、陆兄后释放 ⇒ 显示面是陆大哥（不是字典序）。
    assert display_surface('fu_jun', ['陆大哥', '陆兄'],
                           structure=structure, entry=chars.characters['fu_jun']) == '陆大哥'
    # ③canonical 为纯称谓（yun_niang 无纯称谓、fu_jun 的 canonical 夫君是纯称谓）。
    assert display_surface('fu_jun', (), structure=structure,
                           entry=chars.characters['fu_jun']) == '夫君'
    # ④canonical 含姓名部件 ⇒ 取源文最早登记的纯称谓；本夹具 yun_niang 全部登记
    # 称谓都含姓名部件 ⇒ 无回落（无显示面）。
    assert display_surface('yun_niang', (), structure=structure,
                           entry=chars.characters['yun_niang']) == ''
    # ①第一人称聚焦人物用角色词，优先于已释放称谓。
    assert display_surface('fu_jun', ['陆大哥'], structure=structure,
                           entry=chars.characters['fu_jun'],
                           first_person_focalizer=True) == FIRST_PERSON_ROLE_WORD
    # concealed 不回落（allow_fallback=False）：尚无释放即无显示面。
    assert display_surface('fu_jun', (), structure=structure,
                           entry=chars.characters['fu_jun'],
                           allow_fallback=False) == ''


def test_appellation_fallback_prefers_earliest_registered_pure_surface():
    """canonical 不是纯称谓时，回落取源文**最早登记**的纯称谓（登记序）。"""
    chars = _characters().core_view()
    structure = _characters().name_structure
    # 夹具里 yun_niang 无纯称谓；给她在最前面补一个纯称谓，回落应取它。
    data = _characters().model_dump(mode='json')
    data['characters']['yun_niang']['surfaces'].insert(0, {
        'surface': '娘子', 'from_chapter': 1, 'until_chapter': None,
        'source_refs': ['e01_01']})
    data['name_structure']['people']['yun_niang']['surface_forms']['娘子'] = None
    from contracts.schema.source import CharactersArtifactV6
    amended = CharactersArtifactV6.model_validate(data)
    assert appellation_fallback('yun_niang', amended.core_view().characters['yun_niang'],
                                amended.name_structure) == '娘子'
    assert appellation_fallbacks(amended.core_view(), amended.name_structure) == {
        'fu_jun': '夫君', 'yun_niang': '娘子'}


# ───────────────────────── 释放前缀：保序、覆盖全体人物 ─────────────────────────

def _released_body():
    """两章三场的绿基座：陆大哥第 1 章释放（夫君之后），第 2 章场提到未出场。"""
    from tests.test_str06_r10b_writer_packet import _binding, _design
    from contracts.schema.context import SurfaceRelease
    body = _design()
    scenes = list(body.scenes)
    beats = list(scenes[0].beats)
    beats[0] = beats[0].model_copy(update={'surface_introductions': (
        SurfaceRelease(char_id='fu_jun', surfaces=('陆大哥',)),)})
    scenes[0] = scenes[0].model_copy(update={'beats': tuple(beats)})
    body = body.model_copy(update={'scenes': tuple(scenes)})
    return body, _binding(body)


def test_release_prefixes_cover_all_characters_in_release_order():
    """前缀对全体人物有效并保留释放顺序：拍前缀含本拍释放、场末含本场、章首不含本章。"""
    body, _ = _released_body()
    prefixes = ReleasePrefixes(body)
    beat0 = prefixes.beat_faces('sc01', 'b01')
    # 含本拍释放：b01 自己引入的陆大哥在本拍可用（本拍正式介绍之后同拍可用）。
    assert beat0['fu_jun'][:2] == ('夫君', '陆大哥'), '释放顺序＝阅读序，不是字典序'
    # 拍前不可用：b01 之前没有任何拍，陆大哥在 b01 引入 ⇒ 章首前缀没有它。
    assert '陆大哥' not in prefixes.chapter_start_faces(1)['fu_jun']
    # 第 2 章章首＝第 1 章场末：第 1 章的全部释放到达第 2 章（被提及未出场也有面）。
    assert prefixes.chapter_start_faces(2)['fu_jun'][:2] == ('夫君', '陆大哥')
    assert prefixes.scene_end_faces('sc03')['fu_jun'][:2] == ('夫君', '陆大哥')
    # 未释放人物也保留在映射里（尚无面 ⇒ 不在；全体人物不因「本场参与者」被裁剪）。
    assert 'yun_niang' in prefixes.chapter_start_faces(2)


def test_mentioned_but_absent_character_projects_in_the_chapter_packet():
    """判据①：被提及未出场的人物按书级前缀投影——第 2 章某拍只有芸娘在场，
    事件文本提到未释放的『陆大哥』（绑定人物 fu_jun），fu_jun 本场不出场、
    也不在该场任何读取里；显示面取其已释放的『夫君』，送达行投影、不再泄漏。
    旧口径只收本场参与者：fu_jun 不在场面里 ⇒ 无显示面 ⇒ 泄漏回派。"""
    from tests.test_str06_r10b_writer_packet import _binding, _design, _requirements
    from novel.stages.stage4.book_ir import compile_writer_book_ir
    from novel.stages.stage4.compile import compile_execution_plan
    from novel.stages.common import project_chapter_packet
    body = _design()
    # sc03（第 2 章）：只有芸娘在场，事件文本提到未释放的陆大哥。
    scenes = list(body.scenes)
    beats = list(scenes[2].beats)
    beats[0] = beats[0].model_copy(
        update={'event': '芸娘翻出陆大哥的行囊', 'participants': ('yun_niang',)})
    scenes[2] = scenes[2].model_copy(update={'beats': tuple(beats)})
    body = body.model_copy(update={'scenes': tuple(scenes)})
    binding = _binding(body)
    plan, errors = compile_execution_plan(body, binding)
    assert not errors, errors
    chars = _characters()
    brief = _requirements()
    ir = compile_writer_book_ir(
        graph=body, plan=plan, binding=binding, total_budget=4000,
        length_band=(0.6, 1.3), projection=chars.name_projection(),
        structure=chars.name_structure, fact_locks=brief.fact_locks,
        kp_statements={p.kp_id: p.statement for p in brief.propositions},
        display_fallback=appellation_fallbacks(chars.core_view(), chars.name_structure),
        name_map=name_map_of(chars))
    packet = project_chapter_packet(ir, 2)
    view = render_chapter_packet(packet)
    assert '陆大哥' not in view, '被提及未出场人物的完整称谓按显示面投影'
    assert '芸娘翻出夫君的行囊' in view


def test_first_person_focalizer_uses_the_role_word_in_the_packet():
    """判据①：第一人称叙述的聚焦人物在材料里用角色词『我（叙述者）』。"""
    from contracts.schema.hook import HookPov
    from novel.stages.stage4.design import assemble_body_design
    from tests.test_str06_r10b_writer_packet import (
        _binding, _requirements, _wire,
    )
    from novel.stages.stage4.book_ir import compile_writer_book_ir
    from novel.stages.stage4.compile import compile_execution_plan
    from novel.stages.common import project_chapter_packet
    brief = _requirements()
    # 与 _design 同尺：事实锁兑现不进本用例（写手面测试），换第一人称叙述。
    no_realize = brief.model_copy(update={'requirements': tuple(
        r.model_copy(update={'realize_locks': ()}) for r in brief.requirements)})
    first = HookPov(person='first', focalizers=('yun_niang',))
    wire = _wire()
    wire['narration'] = {'person': 'first', 'focalizers': ['yun_niang']}
    body, errors = assemble_body_design(
        wire, known_char_ids={'yun_niang', 'fu_jun'},
        expression_requirements=brief.expression,
        source_narration=first,
        brief=no_realize)
    assert body is not None and not errors, errors
    binding = _binding(body)
    plan, errors = compile_execution_plan(body, binding)
    assert not errors, errors
    chars = _characters()
    ir = compile_writer_book_ir(
        graph=body, plan=plan, binding=binding, total_budget=4000,
        length_band=(0.6, 1.3), projection=chars.name_projection(),
        structure=chars.name_structure, fact_locks=brief.fact_locks,
        kp_statements={p.kp_id: p.statement for p in brief.propositions},
        display_fallback=appellation_fallbacks(chars.core_view(), chars.name_structure),
        name_map=name_map_of(chars))
    view = render_chapter_packet(project_chapter_packet(ir, 2))
    assert f'{FIRST_PERSON_ROLE_WORD}确知' in view, '知情条目的持有者按角色词呈现'
    assert '苏芸娘确知' not in view and '芸娘确知' not in view


# ───────────────────────── 投影：折叠、自然混称、裸姓（判据①） ─────────────────────────

def test_apposition_folds_instead_of_doubling():
    """同位语折叠：「表弟裴书远」投影为「表弟」，不输出「表弟表弟」。"""
    bindings = (WithheldNameBinding(literal='裴书远', char_id='cousin', unmarked_unique=True),)
    text, hits = project_withheld_names(
        '表弟裴书远进门', bindings=bindings, display={'cousin': '表弟'})
    assert text == '表弟进门'
    assert [(h.literal, h.replacement) for h in hits] == [('裴书远', '表弟')]
    # 姓名在前、称谓在后同样折叠。
    text, _hits = project_withheld_names(
        '裴书远表弟进门', bindings=bindings, display={'cousin': '表弟'})
    assert text == '表弟进门'
    # 原文同位语之外还有一次姓名呼唤，不能因折叠后已输出「表弟」再吞一次。
    for source in ('表弟裴书远裴书远，快来！', '裴书远裴书远表弟，快来！'):
        text, hits = project_withheld_names(source, bindings=bindings, display={'cousin': '表弟'})
        assert text == '表弟表弟，快来！'
        assert [hit.literal for hit in hits] == ['裴书远', '裴书远']


def test_bare_surname_is_never_projected():
    """裸姓不投影：短姓／家族构词不在绑定表内，保持原样留给字面检查。"""
    bindings = (WithheldNameBinding(literal='陆大哥', char_id='fu_jun', unmarked_unique=True),)
    text, hits = project_withheld_names(
        '陆与陆家同在，陆大哥在门口。', bindings=bindings, display={'fu_jun': '夫君'})
    assert text == '陆与陆家同在，夫君在门口。'
    assert [h.literal for h in hits] == ['陆大哥']
    assert '陆' in text and '陆家' in text


def test_display_projection_preserves_mixed_forms_and_quotation_context():
    """姓名叙述与带名称呼同句出现时，只投影称谓，不删除行动或对白框架。"""
    text, hits = project_withheld_names(
        '陆大哥在外看房，娘子听见人说「陆兄不是外人」。',
        bindings=(WithheldNameBinding(literal='陆大哥', char_id='fu_jun', unmarked_unique=True),
                  WithheldNameBinding(literal='陆兄', char_id='fu_jun', unmarked_unique=True)),
        display={'fu_jun': '夫君'})
    assert text == '夫君在外看房，娘子听见人说「夫君不是外人」。'
    assert [(h.literal, h.char_id, h.replacement) for h in hits] == [
        ('陆大哥', 'fu_jun', '夫君'), ('陆兄', 'fu_jun', '夫君')]


# ───────────────────────── Hook：concealed 不回落 ─────────────────────────

def test_hook_concealed_characters_do_not_fall_back_but_others_do():
    """concealed 人物不走第 3 步回落：未释放即无显示面；非 concealed 未释放者
    在 compile 期附带回落纯称谓（夫君）——两条边界都在 display_faces 里。"""
    from novel.stages.stage4.design import assemble_body_design
    from novel.stages.stage4.hook import assemble_hook_design, publish_graph
    from novel.stages.stage4.hook_context import compile_hook_context
    from tests.factories import hook_wire_v3
    from tests.str06_expression_inputs import sample_narration, sample_requirements
    from tests.test_str06_hook import _body_wire, _hook_wire
    chars = _characters()
    known = set(chars.characters)
    body, errors = assemble_body_design(
        _body_wire(), known_char_ids=known,
        expression_requirements=sample_requirements(), source_narration=sample_narration())
    assert body is not None and not errors, errors
    faces = {}

    def build(concealed):
        hook, errors = assemble_hook_design(
            hook_wire_v3(_hook_wire(concealed=concealed, surface_releases=[
                {'char_id': 'yun_niang', 'surfaces': ['苏芸娘']}])),
            body=body, known_char_ids=known, name_projection=chars.name_projection(),
            name_structure=chars.name_structure, faces=_faces(chars))
        assert hook is not None and not errors, errors
        context, errors = compile_hook_context(
            publish_graph(body, hook), chars.name_projection(),
            chars.name_structure, {}, {}, characters=chars.core_view(),
            total_budget=2000, name_map=name_map_of(chars))
        assert context is not None and not errors, errors
        return context

    open_context = build(concealed=[])
    faces['open'] = {r.char_id: r.surfaces for r in open_context.display_faces}
    hidden_context = build(concealed=['fu_jun'])
    faces['hidden'] = {r.char_id: r.surfaces for r in hidden_context.display_faces}
    # 非 concealed：fu_jun 未在 Hook 释放，但回落纯称谓『夫君』随上下文携带。
    assert faces['open']['fu_jun'] == ('夫君',)
    # concealed：同一人物无显示面（不回落），投影保持原文、字面检查如实报告。
    assert 'fu_jun' not in faces['hidden']
    from novel.stages.common import hook_material_issues
    assert any(i.literal == '陆大哥' for i in hook_material_issues(
        hidden_context.model_copy(update={'local_focus': '陆大哥的身份不露'}),
        internal_ids=set()))


# ───────────────────────── 无显示面归 Design（残余归属表第一行） ─────────────────────────

def _no_fallback_characters():
    """fu_jun 去掉纯称谓（canonical 改陆大哥、去掉夫君登记）：无回落可证明。"""
    base = _characters()
    characters = {
        **base.characters,
        'fu_jun': base.characters['fu_jun'].model_copy(update={
            'canonical': '陆大哥',
            'surfaces': tuple(s for s in base.characters['fu_jun'].surfaces
                              if s.surface != '夫君')})}
    structure = base.name_structure.model_copy(update={'people': {
        **base.name_structure.people,
        'fu_jun': base.name_structure.people['fu_jun'].model_copy(
            update={'surface_forms': {'陆大哥': '{family}大哥',
                                      '陆兄': '{family}兄'}})}})
    return base.model_copy(update={'characters': characters,
                                   'name_structure': structure})


def test_bound_name_without_display_routes_design_with_a_wire_path():
    """判据①：已绑定称谓但该人物全书无显示面 ⇒ `design_unreleased_reference`
    （补释放／挪承载位／改指称），带站点 wire 路径；同一字面有回落时不报。"""
    from novel.stages.stage4.materials import validate_design_materials
    from tests.test_str06_r10b_writer_packet import _design
    body = _design()
    # fu_jun 不释放任何面（去掉 initial 的夫君），事件文本用未释放的陆大哥。
    scenes = list(body.scenes)
    beats = list(scenes[0].beats)
    beats[0] = beats[0].model_copy(update={'event': '陆大哥临行留赠玉簪'})
    scenes[0] = scenes[0].model_copy(update={'beats': tuple(beats)})
    body = body.model_copy(update={
        'scenes': tuple(scenes),
        'initial_surfaces': tuple(s for s in body.initial_surfaces
                                  if s.char_id != 'fu_jun')})

    def resolve(site):
        return ('scenes', 0, 'beats', 0)

    # 无回落人物：陆大哥未释放且无纯称谓。
    chars = _no_fallback_characters()
    issues = validate_design_materials(
        body, projection=chars.name_projection(), known_char_ids=set(chars.characters),
        structure=chars.name_structure, resolve=resolve,
        characters=chars.core_view(), faces=_faces(chars),
        name_map=name_map_of(chars))
    matches = [i for i in issues if i.code == 'design_unreleased_reference']
    assert matches, [str(i) for i in issues]
    issue = matches[0]
    assert '陆大哥' in str(issue) and 'fu_jun' in str(issue)
    assert issue.context['wire_paths'], '残余带可编辑 wire 路径（locate）'
    # 有回落（夫君是纯称谓）时同一文本不报——投影已把送达字节换成夫君。
    plain = _characters()
    plain_issues = validate_design_materials(
        body, projection=plain.name_projection(), known_char_ids=set(plain.characters),
        structure=plain.name_structure, resolve=resolve,
        characters=plain.core_view(), faces=_faces(plain),
        name_map=name_map_of(plain))
    assert not [i for i in plain_issues
                if i.code in ('design_unreleased_reference', 'positive_material_leak')
                and '陆大哥' in str(i)], '回落显示面把残余消掉，不再报泄漏'


def _chapter_boundary_body(*, release_scene: int, release_beat: int):
    """第 1 章章目的与 sc01 b01 的事件都提到『陆大哥』；夫君全书初始无释放，
    『陆大哥』在 (release_scene, release_beat) 才释放（0 起的下标）。"""
    from contracts.schema.context import SurfaceRelease
    from tests.test_str06_r10b_writer_packet import _design
    body = _design()
    scenes = list(body.scenes)
    beats = list(scenes[0].beats)
    beats[0] = beats[0].model_copy(update={'event': '陆大哥说约定半月便回'})
    scenes[0] = scenes[0].model_copy(update={'beats': tuple(beats)})
    target = list(scenes[release_scene].beats)
    target[release_beat] = target[release_beat].model_copy(update={
        'surface_introductions': (SurfaceRelease(char_id='fu_jun', surfaces=('陆大哥',)),)})
    scenes[release_scene] = scenes[release_scene].model_copy(update={'beats': tuple(target)})
    chapters = list(body.chapters)
    chapters[0] = chapters[0].model_copy(update={'purpose': '陆大哥临行约定埋下疑心'})
    return body.model_copy(update={
        'scenes': tuple(scenes), 'chapters': tuple(chapters),
        'initial_surfaces': tuple(s for s in body.initial_surfaces if s.char_id != 'fu_jun')})


def _design_material_issues(body):
    from novel.stages.stage4.materials import validate_design_materials
    chars = _no_fallback_characters()      # 无回落纯称谓：投影不会把问题掩掉
    return validate_design_materials(
        body, projection=chars.name_projection(), known_char_ids=set(chars.characters),
        structure=chars.name_structure, characters=chars.core_view(),
        faces=_faces(chars), name_map=name_map_of(chars))


def test_design_gate_judges_released_names_per_chapter_not_per_beat():
    """Design 原文闸的「已释放」按**章**判（2026-10-07 用户裁定，取代 H3 的按位置）：
    正文最终闸与写手章包本来就只按章判，章内谁先谁后不检查，Design 的中间文本不该
    比它指导的正文更严。反例来自 csf4 第 3 章：章目的与拍 1 事件提到拍 2 才介绍的人名，
    被按位置闸拒收，已购的 Design 第 2 轮因此不能零调用沿用。

    对照：同一人名只在**下一章**才释放时仍是泄漏（本章及之前都没释放过＝真正的
    提前披露），带路径、可窄修。"""
    # 章内后释放（第 1 章 sc01 b03）：章目的与 b01 事件都不算泄漏。
    later_in_chapter = _design_material_issues(
        _chapter_boundary_body(release_scene=0, release_beat=2))
    assert [str(i) for i in later_in_chapter if '陆大哥' in str(i)] == [], \
        [str(i) for i in later_in_chapter]
    # 下一章才释放（第 2 章 sc02 b01）：第 1 章的两处提及仍被拦下，且归 Design。
    next_chapter = _design_material_issues(
        _chapter_boundary_body(release_scene=1, release_beat=0))
    hits = [i for i in next_chapter if '陆大哥' in str(i)]
    assert hits and {i.code for i in hits} == {'design_unreleased_reference'}, \
        [str(i) for i in next_chapter]
    assert {i.location[0] for i in hits} == {1}, '都在第 1 章（第 2 章释放之后不再报）'
    assert len(hits) == 2, '章目的与 b01 事件各一处'


def test_first_release_inside_the_chapter_beats_the_fallback_face():
    """RC-60 第 2 条：该位置之前已释放称谓里按释放顺序的第一个——章内才释放的人物，
    释放之后显示面是**首个已释放称谓**，不再停在章首附带的回落纯称谓上。

    夹具：夫君全书初始无释放（回落＝canonical『夫君』），第 1 章 b01 才释放『陆大哥』；
    b03 的事件提到未释放的完整称谓『陆兄』（绑定夫君）。b01 之后写手章包的投影应取
    『陆大哥』，而不是章首回落面『夫君』（这是写手章包按拍取显示面，与 Design 原文闸
    的按章判已释放无关）。"""
    from contracts.schema.context import SurfaceRelease
    from tests.test_str06_r10b_writer_packet import _binding, _design, _requirements
    from novel.stages.stage4.book_ir import compile_writer_book_ir
    from novel.stages.stage4.compile import compile_execution_plan
    from novel.stages.common import project_chapter_packet
    body = _design()
    body = body.model_copy(update={'initial_surfaces': tuple(
        r for r in body.initial_surfaces if r.char_id != 'fu_jun')})
    scenes = list(body.scenes)
    beats = list(scenes[0].beats)
    beats[0] = beats[0].model_copy(update={'surface_introductions': (
        SurfaceRelease(char_id='fu_jun', surfaces=('陆大哥',)),)})
    beats[2] = beats[2].model_copy(update={'event': '陆兄在巷口回头看了一眼'})
    scenes[0] = scenes[0].model_copy(update={'beats': tuple(beats)})
    body = body.model_copy(update={'scenes': tuple(scenes)})
    binding = _binding(body)
    plan, errors = compile_execution_plan(body, binding)
    assert not errors, errors
    chars = _characters()
    brief = _requirements()
    ir = compile_writer_book_ir(
        graph=body, plan=plan, binding=binding, total_budget=4000,
        length_band=(0.6, 1.3), projection=chars.name_projection(),
        structure=chars.name_structure, fact_locks=brief.fact_locks,
        kp_statements={p.kp_id: p.statement for p in brief.propositions},
        display_fallback=appellation_fallbacks(chars.core_view(), chars.name_structure),
        name_map=name_map_of(chars))
    view = render_chapter_packet(project_chapter_packet(ir, 1))
    line = next(row for row in view.splitlines() if '在巷口回头看了一眼' in row)
    assert '陆大哥在巷口回头看了一眼' in line, f'章内首个已释放称谓是显示面：{line}'


def test_family_collective_forms_follow_the_family_members_release_in_design_gate():
    """Design 原文闸的「已释放」与正文／章 IR 同一把尺：同家族有人物已释放后，登记的
    家族构词（陆家＝{family}家）不再算未释放字面。显示面解析用原始释放面（构词不是
    指称某人的显示面），但字面检查面必须并入家族构词——否则 Design 对「陆家上下」
    这类集体指称比正文闸更严，凭空多出首答失败。"""
    from novel.stages.stage4.materials import validate_design_materials
    from tests.test_str06_r10b_writer_packet import _design
    body = _design()      # 夫君（陆家人）初始即已释放
    scenes = list(body.scenes)
    beats = list(scenes[0].beats)
    beats[2] = beats[2].model_copy(update={'event': '陆家上下都在等消息'})
    scenes[0] = scenes[0].model_copy(update={'beats': tuple(beats)})
    body = body.model_copy(update={'scenes': tuple(scenes)})
    chars = _characters()
    issues = validate_design_materials(
        body, projection=chars.name_projection(), known_char_ids={'yun_niang', 'fu_jun'},
        structure=chars.name_structure, characters=chars.core_view(),
        faces=_faces(chars), name_map=name_map_of(chars))
    assert [i for i in issues if '陆家' in i.location] == [], [str(i) for i in issues]
