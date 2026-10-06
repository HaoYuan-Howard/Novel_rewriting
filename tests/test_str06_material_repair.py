"""CS-R：正式材料来源到目标owner；源目标同号章二分法已废弃，保留实质反例。"""
from dataclasses import replace

import pytest

from novel.core.material_trace import MaterialIssue, MaterialSource
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
    from tests.test_str06_hook import _hook_wire
    body, binding, brief, chars = _conflict()
    hook, errors = assemble_hook_design(_hook_wire(opening_facts=[], forbidden_reveals=[]), body=body, known_char_ids=set(chars.characters),
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


def test_hook_owned_text_routes_only_with_formal_hook_proof():
    from contracts.schema.design import PresentationGraph
    from contracts.schema.hook import HookDesign, HookMaterial
    body, binding, brief, _ = _conflict()
    hook = HookDesign(pov=body.narration,
        materials=(HookMaterial(beat_ref=sorted(body.beat_refs())[0]),),
        design_brief='以姓名线索开场', delivery_brief='保留疑问')
    graph = PresentationGraph(**body.model_dump(), hook=hook)
    for value, expected in [(hook.design_brief, 'hook_design'), ('凭空改写的提示', '')]:
        problem = MaterialIssue('positive_material_leak', 'hook/writer_view', 0, value,
            (MaterialSource('hook', 'hook', 'design_brief', value),))
        repair, = plan_material_repairs([problem], body=graph, binding=binding, brief=brief)
        assert repair.owner == expected
