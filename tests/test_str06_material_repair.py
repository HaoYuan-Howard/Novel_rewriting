"""CS-R：正式材料来源到目标owner；源目标同号章二分法已废弃，保留实质反例。"""
from dataclasses import replace

import pytest

from novel.core.material_trace import (
    MaterialFragment, MaterialIssue, MaterialLines, MaterialSource, sources_for_span,
)
from novel.stages.stage4 import inspect_body_materials, plan_material_repairs
from tests.test_str06_hook import _characters
from tests.test_str06_r10b_writer_packet import _design, _binding, _requirements


def _conflict(*, ch1_has_allowed_surface: bool = False):
    """源第1章已合法出现的称谓，目标第二章才释放陆大哥。

    CS-H H3 起显示面有回落（RC-60 第 3 步）：夫君的 canonical『夫君』是纯称谓，
    无释放也能投影——「无法投影」的构造改为该人物**没有任何纯称谓**（全部登记
    称谓都含姓名部件）且未释放：`ch1_has_allowed_surface=False` 时夫君初态未
    释放且名称结构去掉纯称谓，完整登记称谓『陆大哥』没有显示面，泄漏如实保留
    并按 H3 残余归属（全书无显示面归 Design）回派。`True` 时夫君初态已释放
    『夫君』，同一字面被确定性投影（RC-40/41）——见
    `test_registered_full_surface_is_projected_not_leaked`。
    """
    body, chars, brief = _design(), _characters(), _requirements()
    scenes = list(body.scenes)
    beats = list(scenes[1].beats)
    from contracts.schema.context import SurfaceRelease
    beats[0] = beats[0].model_copy(update={'surface_introductions': (
        SurfaceRelease(char_id='fu_jun', surfaces=('陆大哥',)),)})
    scenes[1] = scenes[1].model_copy(update={'beats': tuple(beats)})
    updates = {'scenes': tuple(scenes)}
    if not ch1_has_allowed_surface:
        updates['initial_surfaces'] = tuple(
            s for s in body.initial_surfaces if s.char_id != 'fu_jun')
        chars = _characters().model_copy(update={'characters': {
            **_characters().characters,
            'fu_jun': _characters().characters['fu_jun'].model_copy(update={
                'canonical': '陆大哥', 'surfaces': tuple(
                    s for s in _characters().characters['fu_jun'].surfaces
                    if s.surface != '夫君')})}})
        structure = _characters().name_structure.model_copy(update={'people': {
            **_characters().name_structure.people,
            'fu_jun': _characters().name_structure.people['fu_jun'].model_copy(
                update={'surface_forms': {'陆大哥': '{family}大哥', '陆兄': '{family}兄'}})}})
        chars = chars.model_copy(update={'name_structure': structure})
    body = body.model_copy(update=updates)
    binding = _binding(body)
    brief = brief.model_copy(update={'propositions': (
        brief.propositions[0].model_copy(update={'statement': '陆大哥在暗中查访旧案'}),)})
    return body, binding, brief, chars


def test_registered_full_surface_is_projected_not_leaked():
    """RC-40/41：已证明唯一绑定人物的完整登记称谓，按本场已释放称谓确定性投影；
    留痕可观测，原字段（Brief 命题）与正式产物保持原样，不反写。"""
    body, binding, brief, chars = _conflict(ch1_has_allowed_surface=True)
    before = [x.model_dump_json() for x in (body, binding, brief, chars)]
    view = inspect_body_materials(body, binding, brief, chars, budget=4000,
                                  length_band=(0.6, 1.3))
    assert not view.errors and not view.materials, '可证明的完整称谓不再是泄漏'
    assert view.name_projections and {
        (p['literal'], p['char_id'], p['replacement'], p['chapter_ordinal'])
        for p in view.name_projections} == {('陆大哥', 'fu_jun', '夫君', 1)}
    from novel.stages.common import (
        build_writer_packet, project_chapter_packet, render_chapter_packet)
    packet = project_chapter_packet(view.ir, 1)
    assert '陆大哥' not in render_chapter_packet(packet)
    assert any('夫君在暗中查访旧案' in line for line in build_writer_packet(packet).must_realize)
    assert [x.model_dump_json() for x in (body, binding, brief, chars)] == before
    assert brief.propositions[0].statement == '陆大哥在暗中查访旧案', '原字段不被改写'


def test_knowledge_placement_routes_design_from_actual_writer_material():
    """RC-01/02/06：真正compiler与renderer给出来源，不读取源目标同号章。"""
    body, binding, brief, chars = _conflict()
    before = [x.model_dump_json() for x in (body, binding, brief, chars)]
    view = inspect_body_materials(body, binding, brief, chars, budget=4000,
                                  length_band=(0.6, 1.3))
    assert not view.errors
    assert view.materials
    repairs = plan_material_repairs(view.materials, body=body, binding=binding,
                                    brief=brief, structure=chars.name_structure)
    assert {r.owner for r in repairs} == {'design'}
    assert {r.issue.item for r in repairs} == {'chapter:1'}
    assert all(r.issue.context['target_release_positions'] for r in repairs)
    assert all(s.kind == 'knowledge' for i in view.materials for s in i.sources)
    assert [x.model_dump_json() for x in (body, binding, brief, chars)] == before


@pytest.mark.parametrize('kind,expected', [('obligation', 'preserve'),
    ('expression', 'design'), ('hook', ''), ('projection', ''), ('world', '')])
def test_origin_ownership_and_code_defect(kind, expected):
    """RC-03/04：自有字段与只读投影分别判定。"""
    body, binding, brief, chars = _conflict()
    if kind == 'obligation':
        ref, text = binding.obligations[0].obligation_id, binding.obligations[0].statement
    elif kind == 'expression':
        ref, text = body.expression_decisions[0].ex_id, body.expression_decisions[0].statement
    else:
        ref, text = 'sc01', '正式材料'
    source = MaterialSource(kind, ref, 'statement', text)
    issue = MaterialIssue('positive_material_leak', 'positive', 0, text, (source,), 1)
    repair, = plan_material_repairs([issue], body=body, binding=binding, brief=brief)
    assert repair.owner == expected
    assert (repair.action == 'stop') == (not expected)


def test_missing_stale_and_ambiguous_origins_do_not_route():
    """RC-05：未登记、缺来源、正式值不一致、多owner皆停。"""
    body, binding, brief, _ = _conflict()
    ob = binding.obligations[0]
    good = MaterialSource('obligation', ob.obligation_id, 'statement', ob.statement)
    bad = MaterialSource('knowledge', brief.propositions[0].kp_id, 'statement', '过期命题')
    design = MaterialSource('design', 'sc01', 'event', '候选文本')
    for origins in ((), (bad,), (good, design)):
        issue = MaterialIssue('positive_material_leak', 'positive', 0, '字面', origins, 1)
        repair, = plan_material_repairs([issue], body=body, binding=binding, brief=brief)
        assert repair.action == 'stop'
    issue = MaterialIssue('unknown_code', 'positive', 0, '字面', (good,), 1)
    assert plan_material_repairs([issue], body=body, binding=binding, brief=brief)[0].action == 'stop'


def test_missing_display_cannot_assign_an_owner_without_formal_material_proof():
    """无显示面不是来源证明；模板和过期投影不得借该分支消耗 Design 修订轮。"""
    body, binding, brief, chars = _conflict()
    proposition = brief.propositions[0]
    for origins in ((), (MaterialSource('projection', 'sc01', 'readonly', '陆大哥'),),
                    (MaterialSource('knowledge', proposition.kp_id, 'statement', '陆大哥过期命题'),)):
        issue = MaterialIssue('positive_material_leak', 'rendered_template', 0, '陆大哥', origins, 1)
        repair, = plan_material_repairs([issue], body=body, binding=binding, brief=brief,
                                        structure=chars.name_structure)
        assert repair.action == 'stop' and repair.owner == ''


def test_identity_excludes_display_location_and_wording():
    from novel.stages.owner_revision import _fingerprint
    body, binding, brief, chars = _conflict()
    issue = inspect_body_materials(body, binding, brief, chars, budget=4000,
                                   length_band=(0.6, 1.3)).materials[0]
    a, = plan_material_repairs([issue], body=body, binding=binding, brief=brief)
    b, = plan_material_repairs([replace(issue, where='另一展示行', start=999)],
                               body=body, binding=binding, brief=brief)
    assert _fingerprint([a.issue]) == _fingerprint([b.issue.model_copy(update={'message': '改文案'})])
    assert _fingerprint([a.issue, a.issue]) == _fingerprint([a.issue])


def test_projection_wrong_beat_and_unregistered_source_name_stop():
    body, binding, brief, chars = _conflict()
    issue = inspect_body_materials(body, binding, brief, chars, budget=4000,
                                   length_band=(0.6, 1.3)).materials[0]
    origin = issue.sources[0]
    broken = replace(issue, sources=(replace(origin, position='sc03#b99'),))
    assert plan_material_repairs([broken], body=body, binding=binding, brief=brief)[0].action == 'stop'
    # 源命题使用名称表没有登记的组合，即使放开所有合法称谓仍无法被认领。
    proposition = brief.propositions[0].model_copy(update={'statement': '陆假名在暗中查访旧案'})
    brief = brief.model_copy(update={'propositions': (proposition,)})
    issue = replace(issue, literal='陆', sources=(replace(origin, text=proposition.statement),))
    result, = plan_material_repairs([issue], body=body, binding=binding, brief=brief,
                                    structure=chars.name_structure)
    assert result.action == 'stop' and '全部登记称谓' in result.issue.message


def test_correction_preserves_history_and_refuses_stale_preview(tmp_path):
    """RC-15：真实正式产物免费重验、CAS更正、重启后不再驱动错误owner。"""
    from contracts.schema.workflow import OwnerIssueFileV2, OwnerIssueV1
    from novel.llm.ledger import CostLedger
    from novel.store import Str06Repository
    from novel.stages.issue_recovery import preview_issue_correction, apply_issue_correction
    from novel.stages.stage4 import assemble_hook_design, publish_graph
    from novel.stages.owner_revision import read_owner_issue_file, open_issue_record
    from tests.str06_facilities import make_context, synthetic_ref
    from tests.factories import hook_wire_v3
    from tests.test_str06_hook import _hook_wire
    body, binding, brief, chars = _conflict()
    hook, errors = assemble_hook_design(
        hook_wire_v3(_hook_wire(
            opening_facts=[], forbidden_reveals=[],
            reader_expectation={
                'kind': 'fact', 'focus': '所谓赴文会的真实目的是什么？',
                'answer': {'ref': {'scene': 'sc02', 'beat': 'b02'},
                           'address': {'domain': 'knowledge', 'subjects': ['yun_niang'],
                                       'holder': 'yun_niang', 'key': brief.propositions[0].kp_id}}})),
        body=body, known_char_ids=set(chars.characters),
        name_projection=chars.name_projection(), name_structure=chars.name_structure)
    assert not errors
    repo = Str06Repository(tmp_path / 'run')
    repo.attach_context(make_context(repo.root, ledger=CostLedger(path=None)))
    for rel, model, owner in [('stage4/design/body_design.json', body, 'design'),
        ('stage4/design/presentation_graph.json', publish_graph(body, hook), 'hook_design'),
        ('stage4/grounding/semantic_binding.json', binding, 'design'),
        ('stage4/grounding/reconstruction_brief.json', brief, 'preserve'),
        ('stage2/characters/characters.json', chars, 'compile')]:
        repo.write(rel, model, refs=(synthetic_ref(owner, '更正夹具'),))
    original = OwnerIssueFileV2(owner='preserve', node='str06.stage4.preserve',
        raised_by='str06.stage4.compile', status='open', rounds_total=2, fingerprint='old',
        against_sha256='a'*64, updated_at=1, issues=(OwnerIssueV1(code='material_name_leak',
            item='chapter:1', message='旧二分法错误推断', context={'literal':'陆大哥','chapter':1}),))
    repo.write('runtime/issues/preserve.json', original)
    preview = preview_issue_correction(repo, 'preserve', budget=4000)
    assert preview['target_owners'] == ['design']
    assert read_owner_issue_file(repo, 'preserve').status == 'open'
    with pytest.raises(ValueError, match='预览已过期'):
        apply_issue_correction(repo, 'preserve', budget=4000, expected_sha256='0'*64, reason='修正归因')
    apply_issue_correction(repo, 'preserve', budget=4000,
                           expected_sha256=preview['evidence_sha256'], reason='修正归因')
    reopened = Str06Repository(repo.root)
    record = read_owner_issue_file(reopened, 'preserve')
    assert record.status == 'superseded' and record.rounds_total == 2
    assert record.corrections[0].original == original
    assert open_issue_record(reopened, 'preserve') is None
    assert apply_issue_correction(reopened, 'preserve', budget=4000,
        expected_sha256=preview['evidence_sha256'], reason='幂等重放')['idempotent']


def test_material_repairs_group_owners_and_keep_unknown_unwritten(tmp_path):
    """RC-03/05：同owner一次落账；多owner按执行序回跳；未知不跳过。"""
    from novel.stages.chain import _backfill_material_repairs
    from novel.stages.owner_revision import MechanicalDefectStop, OwnerRevisionRequired, read_owner_issue_file
    from novel.llm.ledger import CostLedger
    from novel.store import Str06Repository
    from tests.str06_facilities import make_context, synthetic_ref
    body, binding, brief, chars = _conflict()
    repo = Str06Repository(tmp_path / 'run')
    repo.attach_context(make_context(repo.root, ledger=CostLedger(path=None)))
    for owner, rel in [('design', 'stage4/design/body_design.json'),
                       ('preserve', 'runtime/preserve_checkpoint.json')]:
        repo.write(rel, {'owner': owner}, owners=(owner,), refs=(synthetic_ref(owner, '归因夹具'),))
    issues = list(inspect_body_materials(body, binding, brief, chars, budget=4000,
                                        length_band=(0.6, 1.3)).materials)
    ob = binding.obligations[0]
    issues.append(MaterialIssue('positive_material_leak', 'positive', 0, ob.statement,
        (MaterialSource('obligation', ob.obligation_id, 'statement', ob.statement),), 1))
    repairs = plan_material_repairs(issues, body=body, binding=binding, brief=brief)
    with pytest.raises(OwnerRevisionRequired) as exc:
        _backfill_material_repairs(repo, repairs)
    assert exc.value.owner == 'preserve'
    assert read_owner_issue_file(repo, 'design').rounds_total == 1
    assert read_owner_issue_file(repo, 'preserve').rounds_total == 1
    assert read_owner_issue_file(repo, 'book') is None
    unknown, = plan_material_repairs([MaterialIssue('positive_material_leak', 'positive', 0, '字面')],
                                     body=body, binding=binding, brief=brief)
    with pytest.raises(MechanicalDefectStop):
        _backfill_material_repairs(repo, [unknown])
    assert read_owner_issue_file(repo, 'design').rounds_total == 1


def test_design_own_field_is_verified_before_routing():
    body, binding, brief, _ = _conflict()
    for value, expected in [(body.book_style, 'design'), ('投影凭空改出的名称', '')]:
        problem = MaterialIssue('positive_material_leak', 'positive', 0, value,
            (MaterialSource('design', 'book', 'book_style', value),), 1)
        repair, = plan_material_repairs([problem], body=body, binding=binding, brief=brief)
        assert repair.owner == expected


def test_internal_id_issue_carries_only_sources_that_contain_the_handle():
    """句柄来源只认逐字含它的正式字段；来源文本里没有它＝渲染期引入，保持无来源。"""
    from novel.core.material_trace import MaterialPart
    from novel.stages.common import positive_material_issues
    own = MaterialSource('design', 'sc01#b01', 'target_delta/0', 'fu_jun')
    other = MaterialSource('design', 'sc01#b01', 'event', '夫君出门')
    lines = MaterialLines()
    lines.append_fragments(MaterialFragment('夫君出门', (other,)),
                           '——拍末应成立：', MaterialFragment('fu_jun', (own,)))
    issue, = positive_material_issues(
        {'must_realize/0': lines.parts()[0]},
        released=(), withheld=(), internal_ids=['fu_jun'], chapter_ordinal=7)
    assert issue.code == 'internal_id_leak' and issue.sources == (own,)
    assert issue.chapter_ordinal == 7
    rendered, = positive_material_issues(
        {'must_realize/0': MaterialPart('fu_jun：出门', (other,))},
        released=(), withheld=(), internal_ids=['fu_jun'], chapter_ordinal=7)
    assert rendered.sources == ()


def test_material_span_tracks_each_occurrence_and_keeps_templates_unowned():
    """同一行相同字面两次出现：模板中的那次不能借正式字段中的那次获得 owner。"""
    source = MaterialSource('design', 'book', 'book_style', 'fu_jun')
    lines = MaterialLines()
    lines.append_fragments('模板 fu_jun：', MaterialFragment('fu_jun', (source,)))
    part, = lines.parts()
    first, second = part.text.index('fu_jun'), part.text.rindex('fu_jun')
    assert sources_for_span(part, first, first + len('fu_jun')) == ()
    assert sources_for_span(part, second, second + len('fu_jun')) == (source,)
    assert sources_for_span(part, second - 1, second + 1) == (), '跨过模板边界保持 unknown'
    from novel.core.material_trace import MaterialPart
    assert sources_for_span(MaterialPart(part.text, (source,)), second,
                            second + len('fu_jun')) == (), '旧的行级来源只能诊断'


def test_material_span_survives_projection_and_dedup_only_with_proof():
    """字段内投影坐标可证；模板和其它字段不提供同位语折叠证据。"""
    source = MaterialSource('design', 'sc01#b01', 'event', '原称谓去查案')
    other = MaterialSource('obligation', 'ob01', 'statement', '原称谓去查案')
    lines = MaterialLines(lambda text: (text.replace('原称谓', '他'), ()))
    for origin in (source, other):
        lines.append_fragments('承接：', MaterialFragment('原称谓去查案', (origin,)))
    part, = lines.parts(deduplicate=True)
    assert part.text == '承接：他去查案'
    assert sources_for_span(part, 4, 7) == (source, other)
    lines.append('承接：他去查案')
    unproven, = lines.parts(deduplicate=True)
    assert unproven.sources == (source, other)
    assert sources_for_span(unproven, 4, 7) == (), '任一重复产出路径没有来源就不能自动回派'
    folding = MaterialLines(lambda text: (text.replace('他原称谓', '他'), ()))
    folding.append_fragments('他', MaterialFragment('原称谓', (source,)))
    folded, = folding.parts()
    assert folded.text == '他原称谓' and folded.sources == (source,)
    assert folded.ordinary_spans == ((0, 1),)
    assert sources_for_span(folded, 1, 4) == (source,), '模板不参与语义字段的同位语折叠'


def test_identity_candidates_do_not_join_across_declared_or_template_boundaries():
    from novel.stages.common import positive_material_issues
    text = '[[name:a|白]][[text|若非如此]]'
    origin = MaterialSource('design', 'book', 'book_style', text)
    lines = MaterialLines()
    lines.append_fragments('旁白：', MaterialFragment(text, (origin,)))
    assert positive_material_issues({'row': lines.parts()[0]}, released=('白',),
        withheld=('白若', '旁白'), internal_ids=()) == ()
    blocked = MaterialLines()
    blocked_origin = replace(origin, text='[[name:a|白若]]')
    blocked.append_fragments(MaterialFragment('[[name:a|白若]]', (blocked_origin,)))
    issue, = positive_material_issues({'row': blocked.parts()[0]}, released=(),
        withheld=('白若',), internal_ids=())
    assert issue.code == 'positive_material_leak' and issue.literal == '白若'
    assert issue.sources == (blocked_origin,)


@pytest.mark.parametrize('code', ['name_text_syntax', 'name_text_reference', 'name_text_unclassified'])
def test_identity_protocol_failures_keep_actual_code_and_only_route_mutable_fields(code):
    body, binding, brief, _ = _conflict()
    text = {'name_text_syntax': '[[text|字面',
            'name_text_reference': '[[name:unknown|字面]]',
            'name_text_unclassified': '字面'}[code]
    body = body.model_copy(update={'book_style': text})
    field = MaterialSource('design', 'book', 'book_style', body.book_style)
    issue = MaterialIssue(code, 'row', 0, '字面', (field,), 1, reason='明确的身份协议诊断')
    own, = plan_material_repairs([issue], body=body, binding=binding, brief=brief)
    assert own.owner == 'design' and own.issue.context['validation_code'] == code
    assert '内部句柄' not in issue.message and '未释放专名字面' not in issue.message
    proposition = brief.propositions[0]
    readonly = MaterialSource('knowledge', proposition.kp_id, 'statement', proposition.statement)
    for origins in ((), (readonly,), (MaterialSource('projection', 'sc01', 'readonly', '字面'),)):
        stopped, = plan_material_repairs([replace(issue, sources=origins)], body=body,
                                        binding=binding, brief=brief)
        assert stopped.action == 'stop' and stopped.owner == ''
    unknown, = plan_material_repairs([replace(issue, code='future_unknown_code')], body=body,
                                    binding=binding, brief=brief)
    assert unknown.action == 'stop' and '未识别' in replace(issue, code='future_unknown_code').message


def test_cross_scene_support_event_keeps_exact_design_origin_through_writer():
    """真实 compiler→IR→章包→renderer→回派链：前场事实不再冒充无owner的projection。"""
    from novel.stages.common import _build_writer_packet, project_chapter_packet
    body = _design()
    scenes, beats = list(body.scenes), list(body.scenes[0].beats)
    beats[0] = beats[0].model_copy(update={'event': beats[0].event + '，fu_jun 留下字条'})
    scenes[0] = scenes[0].model_copy(update={'beats': tuple(beats)})
    body = body.model_copy(update={'scenes': tuple(scenes)})
    binding = _binding(body)
    binding = binding.model_copy(update={'entries': tuple(
        entry.model_copy(update={'supporting_beat_refs': ('sc01#b01', 'sc02#b01')})
        if entry.obligation_id == 'ob02' else entry for entry in binding.entries)})
    brief, chars = _requirements(), _characters()
    before = [value.model_dump_json() for value in (body, binding, brief, chars)]
    view = inspect_body_materials(body, binding, brief, chars, budget=4000,
                                  length_band=(0.6, 1.3))
    assert not view.errors and view.ir is not None
    packet = project_chapter_packet(view.ir, 2)
    supporting, = packet.chapter.scenes[0].verification_ir.continuity_fact_view
    assert supporting.beat_ref == 'sc01#b01' and supporting.event == beats[0].event
    _, parts = _build_writer_packet(packet)
    part, = [part for part in parts['continuity'] if part.text.startswith('前场已成立：')]
    start = part.text.index('fu_jun')
    origins = sources_for_span(part, start, start + len('fu_jun'))
    assert len(origins) == 1
    origin, = origins
    assert (origin.kind, origin.ref, origin.field, origin.text) == (
        'design', 'sc01#b01', 'event', beats[0].event)
    issue = MaterialIssue('internal_id_leak', 'continuity', start, 'fu_jun', origins, 2)
    repair, = plan_material_repairs([issue], body=body, binding=binding, brief=brief)
    assert (repair.action, repair.owner, repair.node) == (
        'revise_upstream', 'design', 'str06.stage4.body_design')
    stale, = plan_material_repairs([replace(issue, sources=(replace(origin,
        text='fu_jun 由投影臆造的事件'),))], body=body, binding=binding, brief=brief)
    assert stale.action == 'stop' and stale.owner == ''
    assert [value.model_dump_json() for value in (body, binding, brief, chars)] == before


def test_internal_id_in_design_own_value_routes_design_not_code_defect():
    """实证反例（2026-10-09《夫君的报恩》rc21 新根）：Design 把人物代号写成状态值，
    建立章与后续读取章的写手材料都带出该代号。来源是 Design 自有字段，归 Design
    改写；无来源或出自源命题的同一代号仍停止，不购买修不了它的 Design 修订。"""
    body = _design()
    scenes, beats = list(body.scenes), list(body.scenes[0].beats)
    deltas = list(beats[0].target_delta)
    deltas[0] = deltas[0].model_copy(update={'value': 'fu_jun'})
    beats[0] = beats[0].model_copy(update={'target_delta': tuple(deltas)})
    scenes[0] = scenes[0].model_copy(update={'beats': tuple(beats)})
    body = body.model_copy(update={'scenes': tuple(scenes)})
    binding, brief, chars = _binding(body), _requirements(), _characters()
    before = [x.model_dump_json() for x in (body, binding, brief, chars)]
    view = inspect_body_materials(body, binding, brief, chars, budget=4000,
                                  length_band=(0.6, 1.3))
    leaks = [m for m in view.materials if m.code == 'internal_id_leak']
    assert {m.chapter_ordinal for m in leaks} == {1, 2}
    assert all(m.sources for m in leaks)
    assert all(s.kind == 'design' and s.ref == 'sc01#b01' and s.text == 'fu_jun'
               for m in leaks for s in m.sources)
    repairs = plan_material_repairs(leaks, body=body, binding=binding, brief=brief,
                                    structure=chars.name_structure)
    assert {(r.action, r.owner, r.node) for r in repairs} == {
        ('revise_upstream', 'design', 'str06.stage4.body_design')}
    assert {r.issue.item for r in repairs} == {'chapter:1', 'chapter:2'}
    assert all('内部句柄' in r.issue.message and '未释放字面' not in r.issue.message
               for r in repairs)
    proposition = brief.propositions[0]
    for origins in ((), (MaterialSource('knowledge', proposition.kp_id, 'statement',
                                        proposition.statement),)):
        stopped, = plan_material_repairs([replace(leaks[0], sources=origins)],
                                         body=body, binding=binding, brief=brief)
        assert stopped.action == 'stop' and stopped.owner == ''
    assert [x.model_dump_json() for x in (body, binding, brief, chars)] == before


def test_selected_detail_routes_to_preserve_only_with_exact_formal_source():
    """软材料的错误也有窄回派入口；错误来源身份或改写后的文本不得冒充正式证据。"""
    from contracts.schema.grounding import SourceDetail
    body, binding, brief, _ = _conflict()
    obligation = binding.obligations[0]
    detail = SourceDetail(detail_id='dt_1234567890abcdef',
                          anchor=obligation.anchors[0], text='陆大哥停在灯下')
    changed = obligation.model_copy(update={'details': (detail,)})
    binding = binding.model_copy(update={'obligations': (
        changed, *binding.obligations[1:])})
    for detail_id, text, expected in (
            (detail.detail_id, detail.text, 'preserve'),
            ('dt_ffffffffffffffff', detail.text, ''),
            (detail.detail_id, '陆大哥停在窗边', '')):
        source = MaterialSource('obligation', obligation.obligation_id,
                                f'details/{detail_id}', text)
        problem = MaterialIssue('positive_material_leak', 'creative_guidance', 0,
                                '陆大哥', (source,), 1)
        repair, = plan_material_repairs([problem], body=body, binding=binding, brief=brief)
        assert repair.owner == expected


def test_hook_owned_text_routes_only_with_formal_hook_proof():
    from contracts.schema.design import PresentationGraph
    from contracts.schema.hook import HookDesign, HookMaterial
    body, binding, brief, _ = _conflict()
    hook = HookDesign(pov=body.narration,
        materials=(HookMaterial(beat_ref=sorted(body.beat_refs())[0],
                                emphasis='develop', transition='start'),),
        reader_expectation={'kind': 'action', 'focus': '人物怎样查证线索',
                            'followup_refs': [sorted(body.beat_refs())[-1]]},
        local_focus='以姓名线索组织即时反应')
    from novel.stages.stage4 import publish_graph
    graph = publish_graph(body, hook)
    for value, expected in [(hook.local_focus, 'hook_design'), ('凭空改写的提示', '')]:
        problem = MaterialIssue('positive_material_leak', 'hook/writer_view', 0, value,
            (MaterialSource('hook', 'hook', 'local_focus', value),))
        repair, = plan_material_repairs([problem], body=graph, binding=binding, brief=brief)
        assert repair.owner == expected


def test_hook_renderer_emits_scoped_sources_and_keeps_readonly_facts_unknown():
    from tests.test_str06_hook import _green_pair
    from novel.stages.common import _hook_render_parts, hook_material_issues
    _, graph, context = _green_pair()
    _, binding, brief, _ = _conflict()
    text = 'fu_jun 是错误句柄'
    hook = graph.hook.model_copy(update={'local_focus': text})
    graph = graph.model_copy(update={'hook': hook})
    context = context.model_copy(update={'local_focus': text})
    issues = hook_material_issues(context, internal_ids=('fu_jun',))
    own, = [issue for issue in issues if issue.code == 'internal_id_leak']
    assert len(own.sources) == 1
    assert (own.sources[0].kind, own.sources[0].ref, own.sources[0].field) == ('hook', 'hook', 'local_focus')
    repaired, = plan_material_repairs([own], body=graph, binding=binding, brief=brief)
    assert repaired.owner == 'hook_design'
    facts = list(context.opening_facts)
    facts[0] = facts[0].model_copy(update={'text': text})
    changed = context.model_copy(update={'local_focus': '眼前动作', 'opening_facts': tuple(facts)})
    readonly, = [issue for issue in hook_material_issues(changed, internal_ids=('fu_jun',))
                 if issue.code == 'internal_id_leak']
    assert readonly.sources and {source.kind for source in readonly.sources} == {'projection'}
    stopped, = plan_material_repairs([readonly], body=graph, binding=binding, brief=brief)
    assert stopped.action == 'stop'
    parts, _ = _hook_render_parts(context)
    field, = [part for part in parts if part.text.startswith('- 局部表现重点：')]
    assert sources_for_span(field, 0, 2) == (), '固定模板不能借同一行的Hook字段获取owner'


def test_hook_selected_event_and_body_event_have_distinct_proven_permissions():
    from contracts.schema.hook import HookDesign, HookMaterial
    from novel.stages.stage4 import publish_graph
    body, binding, brief, _ = _conflict()
    ref = sorted(body.beat_refs())[0]
    scenes, beats = list(body.scenes), list(body.scenes[0].beats)
    beats[0] = beats[0].model_copy(update={'event': beats[0].event + '，fu_jun 留下字条'})
    scenes[0] = scenes[0].model_copy(update={'beats': tuple(beats)})
    body = body.model_copy(update={'scenes': tuple(scenes)})
    hook = HookDesign(pov=body.narration,
        materials=(HookMaterial(beat_ref=ref, emphasis='develop', transition='start'),),
        reader_expectation={'kind': 'action', 'focus': '眼前行动如何发展',
                            'followup_refs': [sorted(body.beat_refs())[-1]]}, local_focus='眼前行动')
    graph = publish_graph(body, hook)
    event = graph.beat(*ref.split('#')).event
    for source, expected in (
            (MaterialSource('hook', ref, 'materials/0/event', event), 'hook_design'),
            (MaterialSource('design', ref, 'event', event), 'design'),
            (MaterialSource('hook', ref, 'materials/0/event', '投影臆造的事件'), '')):
        issue = MaterialIssue('internal_id_leak', 'hook/writer_view/1', 0, 'fu_jun', (source,))
        repair, = plan_material_repairs([issue], body=graph, binding=binding, brief=brief)
        assert repair.owner == expected
        if expected == 'hook_design':
            assert '不得改写正文事件' in repair.issue.message
