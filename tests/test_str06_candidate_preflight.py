"""RC-44/45：候选完整预检、责任回派与免费恢复；真实compiler和章包消费者。"""
from copy import deepcopy

import pytest

from contracts.schema.grounding import OutputConstraints
from contracts.schema.hook import HookConstraints
from novel.llm.paid_requests import digest
from novel.stages.chain import ChainContext, run_body_design, run_preserve, run_workflow
from novel.stages.stage4 import design_obligation_index, inspect_design_candidate, project_brief
from tests.str06_facilities import json_ask, text_ask
from tests.test_str06_body import _chapters, _make_session
from tests.test_str06_hook import _full_chain_wires


def _prepare(tmp_path, *, bad_preserve=False):
    wires = _full_chain_wires()
    if bad_preserve:
        wires['preserve']['obligations'][0]['statement'] = '陆郎与芸娘成亲且立下旧约'
    repo, session, transport = _make_session(tmp_path, wires)

    def no_body(*args):
        raise AssertionError('候选预检不调用正文写手')

    ctx = ChainContext(repository=repo, ask=json_ask(session), ask_text=text_ask(session),
                       ask_body=no_body, chapters=_chapters(), budget=2000, digest=digest)
    run_workflow(repository=repo, chapters=ctx.chapters, ask=ctx.ask,
                 ask_text=ctx.ask_text, ask_body=ctx.ask_body, budget=ctx.budget,
                 through='stage2')
    run_preserve(ctx)
    chars = repo.read_model('stage2/characters/characters.json')
    checkpoint = repo.read_model('runtime/preserve_checkpoint.json')
    brief, _ = project_brief(checkpoint, chars.core_view(),
                             repo.read_model('stage2/blueprints/chapter_blueprints.json'))
    args = dict(checkpoint=checkpoint, brief=brief,
                obligations=design_obligation_index(checkpoint, brief), chars=chars,
                constraints=OutputConstraints(target_chapter_count=2, total_length_budget=2000),
                length_band=ctx.length_band)
    return ctx, wires, transport, args


def _bad_design(wire):
    wire['scenes'][0]['beats'][0]['ev'] = '陆大哥拜堂成亲'
    wire['scenes'][0]['beats'][1]['ev'] = '洞房夜陆大哥立约'
    wire['scenes'][1]['beats'][0]['release'] = [
        {'char_id': 'fu_jun', 'surfaces': ['陆大哥']}]


def test_candidate_aggregates_direct_and_inherited_errors_without_mutation(tmp_path):
    """自身两处称谓错误不能遮住Preserve投影进入真实写手章包的另一处错误。"""
    ctx, wires, _, args = _prepare(tmp_path, bad_preserve=True)
    wire = wires['design']
    _bad_design(wire)
    before = deepcopy(wire)
    view = inspect_design_candidate(wire, **args)
    assert view.body is not None and view.binding is not None and not view.passed
    assert sum('positive_material_leak' in e for e in view.errors) >= 2
    assert any(s.kind == 'obligation' and '陆郎' in s.text
               for m in view.materials for s in m.sources)
    checks = {c.name: c.status for c in view.checks}
    assert checks['design'] == checks['writer_materials'] == 'failed'
    assert checks['binding'] == checks['execution_plan'] == 'passed'
    assert checks['hook_materials'] == 'not_run'
    assert wire == before
    assert not ctx.repository.path('stage4/design/body_design.json').exists()
    assert not ctx.repository.path('stage4/grounding/semantic_binding.json').exists()


def test_shape_failure_marks_dependencies_not_run_and_still_checks_placements(tmp_path):
    _, wires, _, args = _prepare(tmp_path)
    del wires['design']['scenes'][0]['beats'][0]['ev']
    view = inspect_design_candidate(wires['design'], **args)
    assert not view.passed and view.body is None and not view.materials
    checks = {c.name: c for c in view.checks}
    assert checks['design'].status == checks['placements'].status == 'failed'
    for name in ('binding', 'execution_plan', 'writer_inputs', 'writer_materials', 'mechanical_guard'):
        assert checks[name].status == 'not_run' and checks[name].reason
    assert not any('M1' in e or '尚未承载' in e for e in view.errors)


def test_real_acceptance_routes_upstream_first_and_preserves_all_diagnostics(tmp_path):
    from novel.stages.owner_revision import OwnerRevisionRequired, read_owner_issue_file
    ctx, wires, transport, _ = _prepare(tmp_path, bad_preserve=True)
    _bad_design(wires['design'])
    with pytest.raises(OwnerRevisionRequired) as stopped:
        run_workflow(repository=ctx.repository, chapters=ctx.chapters, ask=ctx.ask,
                     ask_text=ctx.ask_text, ask_body=ctx.ask_body, budget=ctx.budget,
                     node_filter=('str06.stage4.body_design',))
    assert stopped.value.node == 'str06.stage4.preserve'
    report = stopped.value.context['candidate_preflight']
    assert not report['passed'] and report['errors'] and report['material_errors']
    status = ctx.repository.read('runtime/workflow_status.json')
    assert status['error_context']['candidate_preflight'] == report
    assert status['failed_node'] == 'str06.stage4.body_design'
    record = read_owner_issue_file(ctx.repository, 'preserve')
    assert record and record.issues
    assert all('candidate_preflight' not in i.context for i in record.issues), '整份报告不按条目复制进返修请求'
    assert read_owner_issue_file(ctx.repository, 'design') is None, '不为过期输入提前消耗Design回派轮'
    assert transport.calls.count('design') == 1, '已知上游错误时不能再购买Design自修'
    assert not ctx.repository.path('stage4/design/body_design.json').exists()
    assert not ctx.repository.path('stage4/grounding/semantic_binding.json').exists()


def test_self_revision_stop_keeps_check_status_and_free_replay(tmp_path):
    from novel.llm.paid_requests import ValidationStop
    ctx, wires, transport, _ = _prepare(tmp_path)
    _bad_design(wires['design'])
    with pytest.raises(ValidationStop) as stopped:
        run_body_design(ctx)
    assert stopped.value.context['candidate_preflight']['checks']
    # Design自有文本的名称问题仍由Design接受判据报告；同一候选送达写手的材料里，已证明
    # 唯一绑定人物的完整登记称谓（陆大哥）被确定性投影，不再算写手材料泄漏（CS-F2）。
    assert 'design=failed' in str(stopped.value) and 'writer_materials=passed' in str(stopped.value)
    calls = len(transport.calls)
    with pytest.raises(ValidationStop):
        run_body_design(ctx)
    assert len(transport.calls) == calls, '相同候选与修订轮免费重放'
    assert not ctx.repository.path('stage4/design/body_design.json').exists()


def test_free_preview_before_any_design_publish_uses_same_candidate_checks(tmp_path):
    from novel.stages.review import dry_run_patch
    ctx, wires, _, args = _prepare(tmp_path, bad_preserve=True)
    _bad_design(wires['design'])
    direct = inspect_design_candidate(wires['design'], **args).report()
    preview = dry_run_patch(ctx.repository, 'design', wires['design'], evidence={},
                            budget=2000, chapter_count=2, length_band=ctx.length_band,
                            hook_constraints=HookConstraints(target_chars=100, hard_max_chars=360))
    assert preview['status'] == 'ran', '没有正式Binding或Hook不妨碍候选预检'
    assert preview['candidate_preflight'] == direct
    assert preview['errors'] == direct['errors'] + direct['material_errors']
    assert not ctx.repository.path('stage4/design/body_design.json').exists()


def test_green_candidate_publishes_both_outputs_and_discloses_hook_not_run(tmp_path):
    ctx, _, transport, _ = _prepare(tmp_path)
    result = run_body_design(ctx)
    assert result['candidate_preflight']['passed']
    assert result['candidate_preflight']['checks'][-1]['status'] == 'not_run'
    assert ctx.repository.read_model('stage4/design/body_design.json').scenes
    assert ctx.repository.read_model('stage4/grounding/semantic_binding.json').entries
    assert transport.calls.count('design') == 1


def test_free_preview_rechecks_existing_hook_against_changed_candidate(tmp_path):
    from novel.stages.chain import run_hook_design
    from novel.stages.review import dry_run_patch
    ctx, wires, _, _ = _prepare(tmp_path)
    run_body_design(ctx)
    run_hook_design(ctx)
    before = ctx.repository.path('stage4/design/presentation_graph.json').read_bytes()
    args = dict(evidence={}, budget=2000, chapter_count=2, length_band=ctx.length_band,
                hook_constraints=ctx.hook_constraints)
    good = dry_run_patch(ctx.repository, 'design', wires['design'], **args)
    assert good['candidate_preflight']['checks'][-1]['status'] == 'passed'
    assert good['candidate_preflight']['passed'] and not good['errors']
    # Hook起笔引用sc01#b03的vow；候选删除这个事实，正文自身仍可构造。
    del wires['design']['scenes'][0]['beats'][2]['state']
    bad = dry_run_patch(ctx.repository, 'design', wires['design'], **args)
    assert bad['candidate_preflight']['checks'][-1]['status'] == 'failed'
    assert not bad['candidate_preflight']['passed']
    assert any('起笔事实' in e for e in bad['errors'])
    assert ctx.repository.path('stage4/design/presentation_graph.json').read_bytes() == before


def test_execution_plan_failure_does_not_claim_writer_checks_passed():
    from novel.stages.stage4 import inspect_body_materials
    from tests.test_str06_material_repair import _conflict
    body, binding, brief, chars = _conflict()
    binding = binding.model_copy(update={'entries': ()})
    view = inspect_body_materials(body, binding, brief, chars,
                                  budget=4000, length_band=(0.6, 1.3))
    assert view.ir is None and view.errors and not view.materials
    assert {c.name: c.status for c in view.checks} == {
        'execution_plan': 'failed', 'writer_inputs': 'passed',
        'writer_materials': 'not_run', 'mechanical_guard': 'not_run'}


def test_dangling_proposition_stops_projection_without_an_uncaught_exception():
    from novel.stages.stage4 import inspect_body_materials
    from tests.test_str06_material_repair import _conflict
    body, binding, brief, chars = _conflict()
    knowledge = next(d for s in body.scenes for b in s.beats for d in b.target_delta
                     if d.domain.value == 'knowledge')
    body = body.model_copy(update={'initial_state': (*body.initial_state,
        knowledge.model_copy(update={'key': 'kp_aaaaaaaaaaaaaaaa'}))})
    view = inspect_body_materials(body, binding, brief, chars,
                                  budget=4000, length_band=(0.6, 1.3))
    assert view.ir is None and any('悬空 kp' in e for e in view.errors)
    checks = {c.name: c.status for c in view.checks}
    assert checks['writer_inputs'] == 'failed'
    assert checks['writer_materials'] == checks['mechanical_guard'] == 'not_run'
