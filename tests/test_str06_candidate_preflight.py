"""RC-44/45：候选完整预检、责任回派与免费恢复；真实compiler和章包消费者。

CS-H H1b（RC-50～53）另覆盖**已购候选的免费沿用**：请求身份变而条目未变、变化
条目称谓规范形相等 → 零调用采用（成本链指向原 attempt）；较早的合格候选不被较晚
的失败候选挡住；四档动作与整份新购闭集原因。全部走真实入口（真实 producer、
fake transport、零付费）。
"""
from copy import deepcopy

import pytest

from contracts.schema.grounding import OutputConstraints
from contracts.schema.hook import HookConstraints
from novel.llm.paid_requests import digest
from novel.llm.transport import ChatResponse
from novel.stages.chain import ChainContext, run_body_design, run_preserve, run_workflow
from novel.stages.stage4 import design_obligation_index, inspect_design_candidate, project_brief
from tests.str06_facilities import (
    carry_forward_ask, json_ask, republish_checkpoint, text_ask,
)
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


def test_owner_patch_preview_rechecks_existing_hook_surface_release_rules(tmp_path):
    """Design修订移除称谓声明后，旧Hook必须与正式接受入口同样拒绝。"""
    import json

    from novel.stages.chain import run_hook_design
    from novel.stages.review import dry_run_patch
    from novel.stages.revisions import preview_owner_patch
    from novel.stages.stage4 import assemble_hook_design

    ctx, wires, transport, candidate_args = _prepare(tmp_path)
    run_body_design(ctx)
    run_hook_design(ctx)
    baseline = deepcopy(wires['design'])
    candidate = deepcopy(baseline)
    candidate['initial_surfaces'] = [item for item in candidate['initial_surfaces']
                                     if item['char_id'] != 'yun_niang']
    inspection = inspect_design_candidate(candidate, **candidate_args)
    assert inspection.passed, '该变化只破坏旧Hook的称谓释放授权，正文候选仍合法'
    chars = ctx.repository.read_model('stage2/characters/characters.json')
    hook, expected_errors = assemble_hook_design(
        wires['hook_design'], body=inspection.body, known_char_ids=set(chars.characters),
        name_projection=chars.name_projection(), name_structure=chars.name_structure)
    assert hook is None and any('不是 yun_niang 已声明的面' in e for e in expected_errors)
    before = {p.relative_to(ctx.repository.root): p.read_bytes()
              for p in ctx.repository.root.rglob('*') if p.is_file()}
    calls = len(transport.calls)
    baseline_json = json.dumps(baseline, ensure_ascii=False)
    identity = digest({'preview': 'design'})
    preview = preview_owner_patch(
        owner='design', identity=identity, candidate=candidate,
        baseline={'identity': identity, 'content': baseline_json,
                  'response_sha256': digest(baseline_json)},
        validate=lambda w: list(inspect_design_candidate(w, **candidate_args).errors),
        dry_run=lambda w: dry_run_patch(
            ctx.repository, 'design', w, evidence={}, budget=ctx.budget,
            chapter_count=len(ctx.chapters), length_band=ctx.length_band,
            hook_constraints=ctx.hook_constraints))
    assert not preview['owner_errors'], '错误来自候选与旧Hook组合，不是Design自身'
    downstream = preview['downstream']
    report = downstream['candidate_preflight']
    assert downstream['status'] == 'ran' and not report['passed']
    assert report['checks'][-1]['name'] == 'hook_materials'
    assert report['checks'][-1]['status'] == 'failed'
    assert all(error in downstream['errors'] for error in expected_errors)
    assert all(error in report['errors'] for error in expected_errors)
    assert candidate['initial_surfaces'] == [item for item in baseline['initial_surfaces']
                                              if item['char_id'] != 'yun_niang']
    assert before == {p.relative_to(ctx.repository.root): p.read_bytes()
                      for p in ctx.repository.root.rglob('*') if p.is_file()}
    assert len(transport.calls) == calls, '恢复预览不购买或重写旧Hook'


def test_restarted_compile_rechecks_canonical_hook_before_republishing_outputs(tmp_path):
    """读回正式图的免费编译不能绕过Hook接受判据或覆盖已验输出组。"""
    from dataclasses import replace

    from novel.llm.ledger import CostLedger, ledger_path
    from novel.stages.chain import run_compile_stage4, run_hook_design
    from novel.stages.owner_revision import MechanicalDefectStop
    from novel.stages.stage4 import publish_graph
    from novel.store import Str06Repository
    from tests.str06_facilities import make_context, synthetic_ref

    ctx, wires, transport, candidate_args = _prepare(tmp_path)
    run_body_design(ctx)
    run_hook_design(ctx)
    run_compile_stage4(ctx)
    outputs = ('stage4/compiled/execution_plan.json', 'stage4/context/hook/hook.json',
               'stage4/ir/writer_book_ir.json', 'stage4/acceptance/packet_metrics.json')
    before_outputs = {rel: ctx.repository.path(rel).read_bytes() for rel in outputs}
    candidate = deepcopy(wires['design'])
    candidate['initial_surfaces'] = [item for item in candidate['initial_surfaces']
                                     if item['char_id'] != 'yun_niang']
    inspection = inspect_design_candidate(candidate, **candidate_args)
    assert inspection.passed and inspection.body is not None
    graph = publish_graph(inspection.body, ctx.repository.read_graph_hook())
    # schema能证明拍与人物存在，不能证明旧Hook获准释放候选已删除的称谓。
    ctx.repository.write('stage4/design/body_design.json', inspection.body,
                         refs=(synthetic_ref('design', '恢复输入'),))
    ctx.repository.write('stage4/design/presentation_graph.json', graph,
                         refs=(synthetic_ref('hook_design', '恢复输入'),))
    source_bytes = ctx.repository.path('stage4/design/presentation_graph.json').read_bytes()
    costs = ledger_path(ctx.repository.root.parent).read_bytes()
    calls = len(transport.calls)
    reopened = Str06Repository(ctx.repository.root)
    reopened.attach_context(make_context(
        reopened.root, ledger=CostLedger(path=ledger_path(reopened.root.parent))))
    with pytest.raises(MechanicalDefectStop, match='不是 yun_niang 已声明的面') as stopped:
        run_compile_stage4(replace(ctx, repository=reopened))
    assert stopped.value.node == 'str06.stage4.compile'
    assert before_outputs == {rel: reopened.path(rel).read_bytes() for rel in outputs}
    assert reopened.path('stage4/design/presentation_graph.json').read_bytes() == source_bytes
    assert ledger_path(reopened.root.parent).read_bytes() == costs
    assert len(transport.calls) == calls, 'canonical复验只走免费确定性消费者'


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


# ── CS-H H1b：已购候选的免费沿用（RC-50～53） ────────────────────────────────

def _design_ctx(ctx, session):
    """同一节点上下文换到新付费边界（重启语义：判定在 run_body_design，闭包落采用凭证）。

    ask／ask_text 闭包随会话一起换——否则「重启后零新购」断言查的是旧会话的
    transport，测不到真正的恢复路径。
    """
    from dataclasses import replace
    return replace(ctx, ask=json_ask(session), ask_text=text_ask(session),
                   carry_forward=carry_forward_ask(session))


def _reopened_session(repo, transport):
    """同一根上新开一个付费边界（重启语义：同一账、同一 transport、会话级状态清空）。"""
    from novel.llm.paid_requests import PaidRequestSession
    return PaidRequestSession(repo, repo.context.ledger, transport, yes=True,
                              allow_source_export=True, provider='fake', sid='run')


def _ledger_rows(repo, purpose='design'):
    import json as _json
    path = repo.root.parent / '_service' / 'ledger.jsonl'
    return [_json.loads(line) for line in path.read_text(encoding='utf-8').splitlines()
            if line.strip() and _json.loads(line).get('purpose') == purpose]


def _lineage_only_mutation(checkpoint):
    """只改 lineage.character_ids（去掉 fu_jun）：设计 prompt 的 participants 变
    （请求身份变），修订对照 wire 的条目（kind/statement/anchors/must/satisfaction/
    requires）不变——lineage 是 compiler 从 anchors 的派生，不进修订对照 wire。"""
    obligations = tuple(
        o.model_copy(update={'lineage': o.lineage.model_copy(update={
            'character_ids': ('yun_niang',)})})
        for o in checkpoint.obligations)
    return checkpoint.model_copy(update={'obligations': obligations})


def _appellation_only_rewrite(checkpoint):
    """换登记称谓不改语义：苏芸娘→芸娘、夫君→陆大哥（同一人物的其它登记面）。"""
    obligations = tuple(
        o.model_copy(update={'statement': o.statement.replace('苏芸娘', '芸娘')
                             .replace('夫君', '陆大哥')})
        for o in checkpoint.obligations)
    return checkpoint.model_copy(update={'obligations': obligations})


def test_identity_change_without_entry_change_carries_forward_with_zero_calls(tmp_path):
    """① 请求身份变而候选依赖条目未变 → 零新调用采用旧候选，成本链指向原 attempt。"""
    ctx, wires, transport, _ = _prepare(tmp_path)
    repo = ctx.repository
    session = _reopened_session(repo, transport)
    ctx = _design_ctx(ctx, session)
    run_body_design(ctx)
    design_calls_first = transport.calls.count('design')
    assert design_calls_first == 1
    registry = repo.inspect('runtime/candidates/design.json')
    assert registry and registry['candidates'][0]['upstream_version']['wire']['obligations']
    first_design = repo.read_model('stage4/design/body_design.json')
    paid = [row for row in _ledger_rows(repo) if not row.get('cached')]
    original_attempt = paid[0]['attempt_id']
    original_identity = paid[0]['request_fingerprint']
    assert registry['candidates'][0]['request_sha256'] == original_identity

    # 上游新版本：请求身份变（participants 进 prompt），修订对照 wire 条目不变。
    republish_checkpoint(repo, _lineage_only_mutation)
    ctx = _design_ctx(ctx, session)
    result = run_body_design(ctx)
    assert transport.calls.count('design') == design_calls_first, '零新调用采用旧候选'
    assert repo.read_model('stage4/design/body_design.json') == first_design, \
        '采用同一候选，产物内容不变（来源账如实记新上游指纹）'
    assert result['reuse']['action'] == 'carried_forward'
    assert result['reuse']['reason'] == '上游无差异'
    carried = [row for row in _ledger_rows(repo)
               if row.get('cache_policy') == 'carried-forward']
    assert len(carried) == 1 and carried[0]['prompt_tokens'] == 0
    assert carried[0]['request_fingerprint'] != original_identity, '本行是新的请求身份'
    assert carried[0]['attempt_id'] != original_attempt, '沿用事件是新行，成本仍归原 attempt'
    # 成本链指向原 attempt：发布的 provenance 聚合到原付费行，不是零也不是双计。
    provenance = repo.inspect('stage4/design/body_design.json')['_provenance']
    assert provenance['usage'] is not None
    assert provenance['usage']['prompt_tokens'] == 10
    assert provenance['usage']['completion_tokens'] == 10
    record = repo.inspect('runtime/revisions/carried/design/'
                          + carried[0]['request_fingerprint'] + '.json')
    assert record['kind'] == 'carried_forward' and record['sequence'] == 0
    assert record['applies_to_request_sha256'] == carried[0]['request_fingerprint']
    assert record['baseline_request_sha256'] == registry['candidates'][0]['request_sha256']


def test_changed_entry_with_equal_canonical_form_carries_forward_with_zero_calls(tmp_path):
    """② 变化条目的称谓规范形相等 → 同样零调用采用（换登记称谓／同位语不算语义变化）。"""
    ctx, wires, transport, _ = _prepare(tmp_path)
    repo = ctx.repository
    session = _reopened_session(repo, transport)
    ctx = _design_ctx(ctx, session)
    run_body_design(ctx)
    design_calls = transport.calls.count('design')
    assert design_calls == 1
    first_design = repo.read_model('stage4/design/body_design.json')

    republish_checkpoint(repo, _appellation_only_rewrite)
    ctx = _design_ctx(ctx, session)
    result = run_body_design(ctx)
    assert transport.calls.count('design') == design_calls, '规范形相等：零调用'
    assert repo.read_model('stage4/design/body_design.json') == first_design
    assert result['reuse']['action'] == 'carried_forward'
    assert result['reuse']['reason'] == '变化条目称谓规范形相等'
    carried = [row for row in _ledger_rows(repo)
               if row.get('cache_policy') == 'carried-forward']
    assert len(carried) == 1 and carried[0]['prompt_tokens'] == 0


def test_changed_entry_without_provable_equivalence_gets_one_narrow_review_round(tmp_path):
    """变化条目不能证明等价 → 只复核引用它的承载拍（一轮条目返修，允许原值不变）。"""
    ctx, wires, transport, _ = _prepare(tmp_path)
    repo = ctx.repository
    session = _reopened_session(repo, transport)
    ctx = _design_ctx(ctx, session)
    run_body_design(ctx)
    calls = transport.calls.count('design')
    seen: dict = {}

    from dataclasses import replace

    def recording_ask(owner, prompt, validate, patch=None, **kwargs):
        seen['review_targets'] = kwargs.get('review_targets')
        return json_ask(session)(owner, prompt, validate, patch=patch)

    ctx = replace(ctx, ask=recording_ask, carry_forward=carry_forward_ask(session))
    first_model = repo.read_model('stage4/design/body_design.json')
    before_beats = [b.model_dump(mode='json') for b in first_model.scenes[1].beats]

    def semantic_rewrite(checkpoint):
        obligations = tuple(
            o.model_copy(update={'statement': o.statement + '，且夫君许诺必归'})
            for o in checkpoint.obligations)
        return checkpoint.model_copy(update={'obligations': obligations})

    republish_checkpoint(repo, semantic_rewrite)
    run_body_design(ctx)
    targets = seen['review_targets']
    assert targets, '不能证明等价的变化必须产生窄复核目标'
    paths = [t.path for t in targets]
    assert ('scenes', 0, 'beats', 0) in paths and ('scenes', 0, 'beats', 1) in paths, \
        '承载拍＝binding 里引用 ob01 的拍（beat_ref 与支撑拍）'
    assert ('scenes', 1, 'beats', 0) not in paths, '不引用该条目的拍不进复核目标'
    # 一轮条目返修（允许原值不变）：付费轮数恰好 +1，非相关拍字节不变。
    assert transport.calls.count('design') == calls + 1, '一轮窄复核，不整份重写'
    after_model = repo.read_model('stage4/design/body_design.json')
    assert [b.model_dump(mode='json') for b in after_model.scenes[1].beats] == before_beats, \
        '无关拍字节不变'


class _FirstDesignBadTransport:
    """包一层假 transport：**第一份** design 回答是坏候选，其后给好候选。

    只在自己出坏稿的那一次计数；委派给内层的 design 请求由内层计数——一次
    外发记一行，别让包装器把调用数翻倍。
    """

    def __init__(self, inner):
        self.inner = inner
        self.calls = inner.calls
        self.wires = inner.wires
        self._spoiled = False

    def send(self, request):
        if self.inner._owner(request) != 'design':
            return self.inner.send(request)
        import json as _json
        if not self._spoiled:
            self._spoiled = True
            self.calls.append('design')
            bad = deepcopy(self.inner.wires['design'])
            _bad_design(bad)
            return ChatResponse(content=_json.dumps(bad, ensure_ascii=False),
                                finish_reason='stop',
                                usage={'prompt_tokens': 10, 'completion_tokens': 10},
                                model='fake')
        return self.inner.send(request)


def test_earlier_green_candidate_is_not_blocked_by_a_later_failed_one(tmp_path):
    """③ 较早的合格候选不被较晚的失败候选挡住（择优：全绿优先，同分取最近）。"""
    ctx, wires, transport, _ = _prepare(tmp_path)
    repo = ctx.repository
    session = _reopened_session(repo, transport)
    ctx = _design_ctx(ctx, session)
    run_body_design(ctx)
    assert transport.calls.count('design') == 1
    first_design = repo.read_model('stage4/design/body_design.json')
    original_checkpoint = repo.read_model('runtime/preserve_checkpoint.json')

    def semantic_rewrite(checkpoint):
        """上游改一条义务的语义：请求身份变、修订对照 wire 该条目变化且不等价。"""
        return checkpoint.model_copy(update={'obligations': tuple(
            o.model_copy(update={'statement': o.statement + '，且夫君许诺必归'})
            for o in checkpoint.obligations)})

    # 上游语义变化 → 新身份：此轮第一份候选是坏稿（被拒），修订轮才拿到合格稿。
    republish_checkpoint(repo, semantic_rewrite)
    flaky = _FirstDesignBadTransport(transport)
    session_flaky = _reopened_session(repo, flaky)
    run_body_design(_design_ctx(ctx, session_flaky))
    assert transport.calls.count('design') == 3, '首份被拒＋一轮修订，不无限重买'

    # 上游回到与候选 1 生成时等价的新版本（修订对照 wire 逐条相等，lineage 派生
    # 只换请求身份）：较早的全绿候选仍可沿用——更晚的候选（含被拒那份）不挡它。
    republish_checkpoint(repo, lambda _ck: _lineage_only_mutation(original_checkpoint))
    ctx = _design_ctx(ctx, session)
    result = run_body_design(ctx)
    assert transport.calls.count('design') == 3, '零新调用：沿用较早的全绿候选'
    assert repo.read_model('stage4/design/body_design.json') == first_design
    assert result['reuse']['action'] == 'carried_forward'
    registry = repo.inspect('runtime/candidates/design.json')
    adopted = [e for e in registry['candidates'] if e.get('adoptions')]
    assert len(adopted) == 1 and adopted[0]['ordinal'] == 1, '采用引用链落在第 1 轮候选上'
    rejected = [e for e in registry['candidates'] if e['accepted'] is False]
    assert rejected, '被拒的那份也在登记里（RC-52：全部已购候选），且如实记未接受'
    assert all(e['upstream_version'] for e in registry['candidates']), \
        '本次验收过的候选都带谱系'


def test_added_entry_review_uses_the_beat_closed_set_as_optional_targets(tmp_path):
    """新增条目 → 可选目标的条目返修：模型只在给定拍闭集里选承载位。"""
    ctx, wires, transport, _ = _prepare(tmp_path)
    repo = ctx.repository
    session = _reopened_session(repo, transport)
    ctx = _design_ctx(ctx, session)
    run_body_design(ctx)
    calls = transport.calls.count('design')
    seen: dict = {}

    from dataclasses import replace

    def recording_ask(owner, prompt, validate, patch=None, **kwargs):
        seen['review_targets'] = kwargs.get('review_targets')
        return json_ask(session)(owner, prompt, validate, patch=patch)

    ctx = replace(ctx, ask=recording_ask, carry_forward=carry_forward_ask(session))

    def add_obligation(checkpoint):
        first = checkpoint.obligations[0]
        added = first.model_copy(update={
            'obligation_id': 'ob02', 'kind': first.kind.__class__('causal'),
            'statement': '旧约之因果不可断', 'must_preserve': False,
            'satisfaction': '', 'requires': ()})
        return checkpoint.model_copy(update={'obligations': (*checkpoint.obligations, added)})

    republish_checkpoint(repo, add_obligation)
    run_body_design(ctx)
    targets = seen['review_targets']
    assert targets and all(t.optional for t in targets), '新增条目只给可选目标'
    paths = {t.path for t in targets}
    assert paths == {('scenes', 0, 'beats', 0), ('scenes', 0, 'beats', 1),
                     ('scenes', 0, 'beats', 2), ('scenes', 1, 'beats', 0),
                     ('scenes', 1, 'beats', 1)}, '闭集＝候选全部拍'
    assert transport.calls.count('design') == calls + 1, '一轮可选目标返修'
    assert repo.read_model('stage4/design/body_design.json').scenes


def test_full_purchase_is_confined_to_the_closed_set_of_reasons_and_leaves_a_trace(tmp_path):
    """整份新购只限闭集原因并留痕（RC-53）：登记丢失＋上游语义变化 ⇒ 谱系不可证明。"""
    from novel.stages import candidates
    from tests.str06_facilities import republish_checkpoint

    ctx, wires, transport, _ = _prepare(tmp_path)
    repo = ctx.repository
    session = _reopened_session(repo, transport)
    run_body_design(_design_ctx(ctx, session))
    # 崩溃窗口＋上游语义变化：候选的上游版本摘要缺失，无法证明谱系（闭集原因）。
    repo.path(candidates.candidates_rel('design')).unlink()

    def semantic_rewrite(checkpoint):
        return checkpoint.model_copy(update={'obligations': tuple(
            o.model_copy(update={'statement': o.statement + '，且夫君许诺必归'})
            for o in checkpoint.obligations)})

    republish_checkpoint(repo, semantic_rewrite)
    result = run_body_design(_design_ctx(ctx, session))
    assert result['reuse']['action'] == 'full_purchase'
    assert result['reuse']['reason'].startswith('上游是全新回答无修订谱系')
    assert transport.calls.count('design') == 2, '谱系不可证明才整份新购（一次）'
    registry = candidates.load_registry(repo, 'design')
    assert registry is not None
    assert registry.full_purchase_reasons[-1].startswith('上游是全新回答无修订谱系')
    assert len(registry.candidates) == 2, '新购轮也进登记'


def test_routed_candidate_keeps_its_lineage_so_a_repaired_upstream_can_reuse_it(tmp_path):
    """被上游回派（未发布）的候选也登记谱系：上游修好后据此沿用，不整份重购。

    这正是 CS-H 的起因：Design 候选自身无错、只因 Preserve 的义务措辞不合格被回派；
    Preserve 修订后同一份回答往往已经全绿。若谱系（候选生成时的上游版本）只在发布成功
    后才登记，这类候选永远「没有修订谱系」，免费沿用对它不可达。
    """
    from novel.stages.owner_revision import OwnerRevisionRequired
    ctx, wires, transport, _ = _prepare(tmp_path, bad_preserve=True)
    repo = ctx.repository
    session = _reopened_session(repo, transport)
    ctx = _design_ctx(ctx, session)
    with pytest.raises(OwnerRevisionRequired):
        run_body_design(ctx)
    assert transport.calls.count('design') == 1
    good = _full_chain_wires()['preserve']['obligations'][0]['statement']

    def repaired(checkpoint):
        first = checkpoint.obligations[0].model_copy(update={'statement': good})
        return checkpoint.model_copy(update={'obligations': (first, *checkpoint.obligations[1:])})

    registry = repo.inspect('runtime/candidates/design.json')
    assert len(registry['candidates']) == 1, '被回派（未发布）的候选已登记'
    routed = registry['candidates'][0]
    assert routed['accepted'] is False and routed['upstream_version']['wire']['obligations'], \
        '如实记未接受，并带它生成时的上游版本'
    republish_checkpoint(repo, repaired)
    result = run_body_design(_design_ctx(ctx, session))
    assert result['reuse']['action'] in ('carried_forward', 'narrow_review'), result['reuse']
    assert transport.calls.count('design') <= 2, '至多一轮窄复核，不整份重购'


def test_narrow_review_starts_from_the_purchased_candidate_with_a_narrow_request(tmp_path):
    """窄复核走真实 `ask_json(baseline=…)`：从已购候选整份 wire 起步，只发窄输入。

    此前的窄复核用例用会吞掉 `baseline`／`narrow` 的记录闭包，实际走的是普通整份购买。
    本例让生产闭包原样收到它们：不重发整份原请求（无输入投影），请求带基底摘要与依赖
    切片；回答只替换复核目标，其余拍字节不变，provenance 同时引用基底与返修轮。
    """
    from tests.test_str06_design_patch import PatchingTransport
    ctx, wires, transport, _ = _prepare(tmp_path)
    repo = ctx.repository
    run_body_design(_design_ctx(ctx, _reopened_session(repo, transport)))
    assert transport.calls.count('design') == 1
    first = repo.read_model('stage4/design/body_design.json')
    first_attempts = [row['attempt_id'] for row in _ledger_rows(repo)
                      if not row.get('cached')]

    def semantic_rewrite(checkpoint):
        return checkpoint.model_copy(update={'obligations': tuple(
            o.model_copy(update={'statement': o.statement + '，且夫君许诺必归'})
            for o in checkpoint.obligations)})

    republish_checkpoint(repo, semantic_rewrite)
    patching = PatchingTransport(wires, wires['design'])
    result = run_body_design(_design_ctx(ctx, _reopened_session(repo, patching)))
    assert result['reuse']['action'] == 'narrow_review'
    assert len(patching.owner_requests) == 1, '只有一轮条目返修，没有整份首轮请求'
    request = patching.owner_requests[0]
    assert '【窄返修】' in request and '【基底】' in request and '【依赖切片】' in request
    assert 'stage4-design-input-v1' not in request, '不带整份原请求的输入投影'
    assert repo.read_model('stage4/design/body_design.json') == first, \
        '复核目标取回原值：产物内容不变'
    provenance = repo.inspect('stage4/design/body_design.json')['_provenance']
    assert provenance['usage'] is not None
    assert provenance['usage']['prompt_tokens'] == 10 + 100, \
        '基底 attempt（原付费行）与返修轮一并聚合，既不丢也不双计'
    assert first_attempts and all(
        any(row['attempt_id'] == a for row in _ledger_rows(repo)) for a in first_attempts)


def test_same_identity_reentry_is_a_plain_cached_replay_not_a_carry_forward(tmp_path):
    """同请求身份重入：沿用闭包主动放弃，节点走普通接受循环（缓存重放）。

    沿用是跨请求身份的快路径；同身份那是缓存重放。否则每次重启都被记成一次「采用」
    （修订记录＋沿用行），并要求原 attempt 有唯一真实付费行——历史采用的根上会误停。
    """
    ctx, wires, transport, _ = _prepare(tmp_path)
    repo = ctx.repository
    session = _reopened_session(repo, transport)
    run_body_design(_design_ctx(ctx, session))
    first = repo.read_model('stage4/design/body_design.json')
    result = run_body_design(_design_ctx(ctx, session))
    assert transport.calls.count('design') == 1, '零新购'
    assert 'reuse' not in result, '没有发生沿用'
    assert not [row for row in _ledger_rows(repo) if row.get('cache_policy') == 'carried-forward']
    assert not repo.path('runtime/revisions/carried').exists(), '不落沿用凭证'
    assert repo.read_model('stage4/design/body_design.json') == first


def test_free_carry_forward_yields_to_registered_patch_resample_and_same_identity(tmp_path):
    """免费沿用不得越过更高优先级的采用：同身份缓存重放、已登记 owner patch（`adopted_patch`
    的职责是防止缓存原稿反向覆盖修订）、显式重采与已登记的采样选择。"""
    import json
    from dataclasses import replace
    from types import SimpleNamespace

    from novel.llm.paid_requests import purchased_baseline
    from novel.llm.transport import ChatRequest
    from novel.stages import candidates
    from novel.stages.chain import carry_forward_candidate
    from novel.stages.common import SYS
    from novel.stages.revisions import preview_owner_patch, stage_revision

    ctx, wires, transport, _ = _prepare(tmp_path)
    repo = ctx.repository
    session = _reopened_session(repo, transport)
    run_body_design(_design_ctx(ctx, session))                       # X1：购买并发布
    republish_checkpoint(repo, _lineage_only_mutation)               # 请求身份变（差异为空）
    prompts = {}
    plain = json_ask(session)

    def spy(owner, prompt, validate, **kwargs):
        prompts[owner] = prompt
        return plain(owner, prompt, validate, **kwargs)

    run_body_design(replace(ctx, ask=spy, carry_forward=None))       # X2：普通购买
    assert transport.calls.count('design') == 2
    _unit, rounds = candidates.purchased_rounds(repo.root.parent, repo.root.name, 'design')
    first, second = rounds[0], rounds[1]
    assert first.request_sha256 != second.request_sha256
    request = ChatRequest(model='fake', system=SYS, user=prompts['design'],
                          reasoning_effort='high', thinking=True, json_mode=True,
                          max_tokens=1000)

    def decision(rnd):
        return SimpleNamespace(
            reason='测试', diff=None,
            candidate={'turn_id': rnd.turn_id, 'attempt_id': rnd.attempt_id,
                       'response_sha256': rnd.response_sha256,
                       'request_sha256': rnd.request_sha256, 'wire': rnd.wire})

    def carry(rnd=first, **kwargs):
        return carry_forward_candidate(session, repo, 'design', request, decision(rnd), **kwargs)

    def carried_rows():
        return [r for r in _ledger_rows(repo) if r.get('cache_policy') == 'carried-forward']

    # 放弃：候选身份＝当前身份（那是缓存重放）、显式重采、已登记的采样选择
    assert carry(second) is None, '同身份：缓存重放，不是沿用'
    assert carry(resample_owner='design') is None, '显式重采要新样本'
    assert carry(selection=object()) is None, '已登记的采样选择要按选择取样'
    assert not carried_rows()

    # 已登记 owner patch（基线＝当前身份 X2）：沿用旧候选会反向覆盖人工修订
    patched = json.loads(json.dumps(wires['design']))
    patched['scenes'][0]['brief'] = patched['scenes'][0]['brief'] + '（人工补丁）'
    baseline = purchased_baseline(repo.root, 'design', second.request_sha256)
    preview = preview_owner_patch(
        owner='design', identity=second.request_sha256, candidate=patched, baseline=baseline,
        validate=lambda w: [], dry_run=lambda w: {'status': 'ran', 'errors': []})
    stage_revision(repo, owner='design', identity=second.request_sha256, baseline=baseline,
                   raw_bytes=json.dumps(patched, ensure_ascii=False).encode('utf-8'),
                   preview=preview, reason='人工补丁')
    assert carry() is None, '已登记 owner patch 优先：交给 ask 重放补丁'
    assert not carried_rows() and not repo.path('runtime/revisions/carried').exists()
