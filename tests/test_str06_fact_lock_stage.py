"""CS-F2 stage：事实锁在已选状态上标注引用，经真实 Stage2→Preserve→body_design→
compile 链发布映射并被下游消费（fake transport，零付费）。

验证面（单元反例在 `test_str06_a4_design_locks.py` 的 CS-F2 一节）：
· Preserve 义务锚定「预计」效应 → Brief 事实锁及必保兑现分母；
· body_design 用 v8 `lock` 标注已选状态 → BodyDesign 与 SemanticBinding 同组发布，
  Binding 只存锁→拍→地址、不复制 value，BodyDesign 里没有 kf 状态键；
· 下游 compile 真实消费：WriterBookIR 的写手材料带「预计，尚未发生」措辞、不含 kf 句柄，
  packet_metrics 计入锁承载；
· 漏标注的坏稿：同节点修订轮收到带规则码与 wire 路径的问题，粘性坏稿停止且不发布半份产物。
· 事实锁与义务位置闭合：已购错位回答拒绝，免费 owner 补丁可补齐显式支撑位；
  重启后的正式 compile 同样拒绝错位盘面，修复后零新购恢复。
"""
from copy import deepcopy

import pytest

from contracts.schema.grounding import OutputConstraints, _lock_id
from contracts.schema.hook import HookConstraints
from novel.llm.paid_requests import ValidationStop, digest
from novel.stages.chain import ChainContext, run_body_design, run_preserve, run_workflow
from tests.str06_facilities import json_ask, stub_body_runner, text_ask
from tests.test_str06_body import _chapters, _make_session
from tests.test_str06_hook import _full_chain_wires

KF_PLAN = _lock_id('kf_', 'b01_01.e2')


def _wires(*, marked: bool = True, unknown: bool = False) -> dict:
    """全链基座加一条预计／值未知效应、必保义务及其标注（或漏标）状态。"""
    wires = deepcopy(_full_chain_wires())
    wires['ch01']['beats'][0]['effects'] = (
        *wires['ch01']['beats'][0]['effects'],
        {'target': {'kind': 'world'}, 'key': 'return_time',
         'op': 'unknown' if unknown else 'set', 'value': None if unknown else '约半月',
         'scope': 'book', 'modality': 'actual' if unknown else 'expected',
         'proposition': {'ref': 'p_return', 'statement': '夫君约半月后归家'},
         'disclosure': {'visibility': 'on_page'},
         'basis': ({'segment': 'e01_01', 'quote': '苏芸娘嫁入陆家'},)})
    wires['preserve']['obligations'].append({
        'kind': 'event_core', 'statement': '夫君归期未知' if unknown else '夫君约定归期（预计，尚未发生）',
        'anchors': ['b01_01.e2'], 'must_preserve': True,
        'satisfaction': '不得补出归期' if unknown else '归期作为预计呈现，不得写成已经归来',
        'requires': []})
    beat = wires['design']['scenes'][0]['beats'][2]         # sc01#b03 '芸娘立誓查清'
    beat['real'] = ['ob02']
    beat['state'].append({'at': 'world', 'key': 'return_time',
                          **({'op': 'unknown'} if unknown else
                             {'val': '约半月后归家', 'modality': 'expected'}),
                          **({'lock': KF_PLAN} if marked else {})})
    return wires


def _prepare(tmp_path, wires):
    repo, session, transport = _make_session(tmp_path, wires)

    def no_body(*args):
        raise AssertionError('不调用正文写手')

    ctx = ChainContext(repository=repo, ask=json_ask(session), ask_text=text_ask(session),
                       ask_body=no_body, chapters=_chapters(), budget=2000, digest=digest)
    run_workflow(repository=repo, chapters=ctx.chapters, ask=ctx.ask, ask_text=ctx.ask_text,
                 ask_body=ctx.ask_body, budget=ctx.budget, through='stage2')
    run_preserve(ctx)
    return ctx, transport


def test_preserve_obligation_on_an_expected_effect_becomes_a_realize_lock(tmp_path):
    ctx, _ = _prepare(tmp_path, _wires())
    brief = ctx.repository.read_model('stage4/grounding/reconstruction_brief.json')
    lock = next(f for f in brief.fact_locks if f.kind == 'expected')
    assert lock.kf_id == KF_PLAN
    assert any(lock.kf_id in r.realize_locks for r in brief.requirements)


def test_body_design_publishes_binding_with_lock_mapping_and_no_kf_state_key(tmp_path):
    ctx, _ = _prepare(tmp_path, _wires())
    result = run_body_design(ctx)
    assert result['candidate_preflight']['passed'] is True
    repo = ctx.repository
    body = repo.read_model('stage4/design/body_design.json')
    binding = repo.read_model('stage4/grounding/semantic_binding.json')
    assert binding.contract_version == 'str06-rc17'
    [mapping] = binding.fact_bindings
    assert (mapping.kf_id, mapping.beat_ref) == (KF_PLAN, 'sc01#b03')
    assert (mapping.address.domain.value, mapping.address.key) == ('world', 'return_time')
    carrier = body.beat('sc01', 'b03').target_delta[-1]
    assert (carrier.key, carrier.value, carrier.modality) == ('return_time', '约半月后归家',
                                                              'expected')
    assert not any(d.key.startswith('kf_') for s in body.scenes for b in s.beats
                   for d in b.target_delta), 'BodyDesign 只持有一份目标事实'
    # 映射只存引用：落盘字节里没有承载状态的 value。
    raw = repo.path('stage4/grounding/semantic_binding.json').read_text(encoding='utf-8')
    fact_part = raw.split('"fact_bindings"', 1)[1].split(']', 1)[0]
    assert '约半月后归家' not in fact_part


def test_downstream_compile_consumes_the_binding_and_writer_material_hides_the_handle(
        tmp_path, monkeypatch):
    from novel.stages.common import project_chapter_packet, render_chapter_packet
    wires = _wires()
    repo, session, transport = _make_session(tmp_path, wires)
    stub_body_runner(monkeypatch)
    report = run_workflow(repository=repo, chapters=_chapters(), ask=json_ask(session),
                          ask_text=text_ask(session),
                          ask_body=lambda *a: (_ for _ in ()).throw(AssertionError('stub')),
                          budget=2000, through='stage4',
                          hook_constraints=HookConstraints(target_chars=10, hard_max_chars=360))
    assert report is not None
    ir = repo.read_model('stage4/ir/writer_book_ir.json')
    text = render_chapter_packet(project_chapter_packet(ir, 1))
    assert '预计，尚未发生' in text, '预计状态以尚未发生措辞送达写手'
    assert KF_PLAN not in text and 'kf_' not in text
    metrics = repo.inspect('stage4/acceptance/packet_metrics.json')
    assert metrics['chapters'][0]['carried_locks'] >= 1, '承载的事实锁计入读数'


def test_missing_lock_mark_is_fed_back_with_rule_code_and_path_and_stops_without_publishing(
        tmp_path):
    ctx, transport = _prepare(tmp_path, _wires(marked=False))
    with pytest.raises(ValidationStop) as stopped:
        run_body_design(ctx)
    issues = [str(i) for i in stopped.value.context['issues']]
    assert any('事实锁未兑现' in i and KF_PLAN in i and 'ob02' in i for i in issues), issues
    assert 'req_' not in ' '.join(issues), '用 Design 看得见的 obNN'
    assert transport.calls.count('design') == 2, '首答＋一次同节点修订轮后无进展即停'
    repo = ctx.repository
    assert not repo.path('stage4/design/body_design.json').exists()
    assert not repo.path('stage4/grounding/semantic_binding.json').exists()


@pytest.mark.parametrize('unknown', [False, True], ids=['expected', 'unknown_value'])
def test_ambiguous_fact_lock_is_rejected_by_full_candidate_paid_entry_and_free_preview(
        tmp_path, unknown):
    """实际候选入口拒绝未标锁的同地址覆盖；免费预览同报告且不发布半份产物。"""
    from novel.stages.review import dry_run_patch
    from novel.stages.stage4 import design_obligation_index, inspect_design_candidate, project_brief
    wires = _wires(unknown=unknown)
    wires['design']['scenes'][0]['beats'][2]['state'].append(
        {'at': 'world', 'key': 'return_time', 'val': '已经归家', 'modality': 'actual'})
    before = deepcopy(wires['design'])
    ctx, transport = _prepare(tmp_path, wires)
    repo = ctx.repository
    chars = repo.read_model('stage2/characters/characters.json')
    checkpoint = repo.read_model('runtime/preserve_checkpoint.json')
    brief, _ = project_brief(checkpoint, chars.core_view(),
                             repo.read_model('stage2/blueprints/chapter_blueprints.json'))
    view = inspect_design_candidate(
        wires['design'], checkpoint=checkpoint, brief=brief,
        obligations=design_obligation_index(checkpoint, brief), chars=chars,
        constraints=OutputConstraints(target_chapter_count=2, total_length_budget=2000),
        length_band=ctx.length_band)
    assert not view.passed
    issue = next(e for e in view.errors if getattr(e, 'code', '') == 'fact_lock_ambiguous')
    assert issue.context['wire_paths'] == [
        ['scenes', 0, 'beats', 2, 'state', 1],
        ['scenes', 0, 'beats', 2, 'state', 2]]
    assert {c.name: c.status for c in view.checks} == {
        'design': 'failed', 'placements': 'passed', 'binding': 'failed',
        'execution_plan': 'not_run', 'writer_inputs': 'not_run',
        'writer_materials': 'not_run', 'mechanical_guard': 'not_run',
        'hook_materials': 'not_run'}
    preview = dry_run_patch(
        repo, 'design', wires['design'], evidence={}, budget=2000, chapter_count=2,
        length_band=ctx.length_band, hook_constraints=ctx.hook_constraints)
    assert preview['status'] == 'ran' and preview['candidate_preflight'] == view.report()
    with pytest.raises(ValidationStop) as stopped:
        run_body_design(ctx)
    assert any(getattr(e, 'code', '') == 'fact_lock_ambiguous'
               for e in stopped.value.context['issues'])
    assert transport.calls.count('design') == 2, '结构坏稿无进展即停，无新下游购买'
    assert 'hook_design' not in transport.calls
    assert wires['design'] == before
    assert not repo.path('stage4/design/body_design.json').exists()
    assert not repo.path('stage4/grounding/semantic_binding.json').exists()


def _move_lock_before_obligation(wire):
    """只移动有锁状态，不移动 ob02 的 real：早拍确有事实，但未声明承载义务。"""
    state = wire['scenes'][0]['beats'][2]['state'].pop()
    assert state['lock'] == KF_PLAN
    wire['scenes'][0]['beats'][0]['state'].append(state)


def _candidate_args(ctx):
    from novel.stages.stage4 import design_obligation_index, project_brief
    repo = ctx.repository
    chars = repo.read_model('stage2/characters/characters.json')
    checkpoint = repo.read_model('runtime/preserve_checkpoint.json')
    brief, _ = project_brief(checkpoint, chars.core_view(),
                             repo.read_model('stage2/blueprints/chapter_blueprints.json'))
    return dict(checkpoint=checkpoint, brief=brief,
                obligations=design_obligation_index(checkpoint, brief), chars=chars,
                constraints=OutputConstraints(target_chapter_count=2,
                                              total_length_budget=ctx.budget),
                length_band=ctx.length_band)


@pytest.mark.parametrize('unknown', [False, True], ids=['expected', 'unknown_value'])
def test_saved_misplaced_lock_is_rejected_then_owner_patch_recovers_free(tmp_path, unknown):
    """Stage2→Preserve→付费接受拒绝；已购原字节经 owner 补丁恢复真实双产物。"""
    import json
    from dataclasses import replace

    from novel.llm.ledger import CostLedger, ledger_path, read_cost_snapshot
    from novel.llm.paid_requests import purchased_baseline
    from novel.stages.review import dry_run_patch
    from novel.stages.revisions import (
        activate_node_revisions, adopted_patch, preview_owner_patch, stage_revision,
    )
    from novel.stages.stage4 import inspect_design_candidate
    from novel.store import Str06Repository
    from tests.str06_facilities import make_context, synthetic_ref

    wires = _wires(unknown=unknown)
    _move_lock_before_obligation(wires['design'])
    before_wire = deepcopy(wires['design'])
    ctx, transport = _prepare(tmp_path, wires)
    args = _candidate_args(ctx)
    rejected = inspect_design_candidate(wires['design'], **args)
    assert not rejected.passed
    issue = next(e for e in rejected.errors
                 if getattr(e, 'code', '') == 'fact_lock_outside_obligation')
    # CS-H H3（RC-63）：共享集合（realize_locks 引用同一锁的必保义务）取代单义务。
    assert {key: issue.context[key] for key in ('obligation_ids', 'kf_id', 'beat_ref')} == {
        'obligation_ids': ['ob02'], 'kf_id': KF_PLAN, 'beat_ref': 'sc01#b01'}
    with pytest.raises(ValidationStop) as stopped:
        run_body_design(ctx)
    assert any(getattr(e, 'code', '') == 'fact_lock_outside_obligation'
               for e in stopped.value.context['issues'])
    assert transport.calls.count('design') == 2, '首答和粘性返修后无进展即停'
    repo = ctx.repository
    assert not repo.path('stage4/design/body_design.json').exists()
    assert not repo.path('stage4/grounding/semantic_binding.json').exists()

    rows, _ = read_cost_snapshot(repo.root.parent)
    paid = [r for r in rows if not r['cached']]
    initial = next(r for r in paid if r['purpose'] == 'design')
    identity = initial['request_fingerprint']
    baseline = purchased_baseline(repo.root, 'design', identity)
    assert json.loads(baseline['content']) == before_wire, '采用基线必须是已购耐久原答'
    calls = len(transport.calls)

    def validate(wire):
        return list(inspect_design_candidate(wire, **args).errors)

    def preview(wire):
        return preview_owner_patch(
            owner='design', identity=identity, candidate=wire, baseline=baseline,
            validate=validate, dry_run=lambda w: dry_run_patch(
                repo, 'design', w, evidence={}, budget=ctx.budget,
                chapter_count=2, length_band=ctx.length_band,
                hook_constraints=ctx.hook_constraints))

    bad_preview = preview(before_wire)
    assert any(getattr(e, 'code', '') == 'fact_lock_outside_obligation'
               for e in bad_preview['owner_errors'])
    assert not bad_preview['downstream']['candidate_preflight']['passed']
    repaired = deepcopy(before_wire)
    repaired['scenes'][0]['beats'][0]['real'].append('ob02')
    green_preview = preview(repaired)
    assert not green_preview['owner_errors']
    assert green_preview['downstream']['candidate_preflight']['passed']
    record = stage_revision(
        repo, owner='design', identity=identity, baseline=baseline,
        raw_bytes=json.dumps(repaired, ensure_ascii=False).encode('utf-8'),
        preview=green_preview, reason='把事实锁建立拍登记为 ob02 的显式支撑拍')

    reopened = Str06Repository(repo.root)
    reopened.attach_context(make_context(
        reopened.root, ledger=CostLedger(path=ledger_path(repo.root.parent)),
        node='str06.stage4.body_design'))

    def replay_registered_patch(owner, prompt, current_validate, patch=None, **kwargs):
        # 与 execute 的 replay_patch 同形：核验登记证据后直接交真实 producer，
        # 不经 transport；用当前完整接受判据复验，不用预检结论冒充通过。
        assert owner == 'design'
        replay = adopted_patch(reopened, owner, identity, current_validate)
        assert replay is not None
        wire, current, _baseline = replay
        ref = synthetic_ref(owner)._replace(
            request_sha256=identity,
            patch={'candidate_sha256': current.candidate_sha256})
        return wire, ref

    result = run_body_design(replace(ctx, repository=reopened, ask=replay_registered_patch))
    activate_node_revisions(reopened, 'str06.stage4.body_design')
    assert result['candidate_preflight']['passed']
    binding = reopened.read_model('stage4/grounding/semantic_binding.json')
    [entry] = [e for e in binding.entries if e.obligation_id == 'ob02']
    assert entry.beat_ref == 'sc01#b03'
    assert entry.supporting_beat_refs == ('sc01#b01',)
    assert binding.fact_bindings[0].beat_ref == 'sc01#b01'
    assert reopened.read_model('stage4/design/body_design.json').beat('sc01', 'b01')
    assert reopened.path(record.evidence_rel).read_bytes() == json.dumps(
        repaired, ensure_ascii=False).encode('utf-8')
    assert wires['design'] == before_wire, '预检和恢复不改已购原答'
    assert len(transport.calls) == calls, 'owner 补丁采用不购买模型'
    assert [r for r in read_cost_snapshot(repo.root.parent)[0] if not r['cached']] == paid


@pytest.mark.parametrize('unknown', [False, True], ids=['expected', 'unknown_value'])
def test_restarted_free_compile_rejects_misplaced_canonical_lock_then_recovers(tmp_path, unknown):
    """正式 schema/seal 合法的存量错位盘面仍须拒绝，不覆盖已验输出；补支持后免费恢复。"""
    from dataclasses import replace

    from novel.llm.ledger import CostLedger, ledger_path
    from novel.stages.chain import run_compile_stage4, run_hook_design
    from novel.stages.owner_revision import MechanicalDefectStop
    from novel.stages.stage4 import publish_graph
    from novel.store import PublishItem, Str06Repository
    from tests.str06_facilities import make_context, synthetic_ref

    ctx, transport = _prepare(tmp_path, _wires(unknown=unknown))
    run_body_design(ctx)
    run_hook_design(ctx)
    run_compile_stage4(ctx)
    repo = ctx.repository
    outputs = ('stage4/compiled/execution_plan.json', 'stage4/context/hook/hook.json',
               'stage4/ir/writer_book_ir.json', 'stage4/acceptance/packet_metrics.json')
    before_outputs = {rel: repo.path(rel).read_bytes() for rel in outputs}
    body = repo.read_model('stage4/design/body_design.json')
    binding = repo.read_model('stage4/grounding/semantic_binding.json')
    scenes = list(body.scenes)
    beats = list(scenes[0].beats)
    [carrier] = [d for d in beats[2].target_delta if d.key == 'return_time']
    beats[2] = beats[2].model_copy(update={
        'target_delta': tuple(d for d in beats[2].target_delta if d.key != 'return_time')})
    beats[0] = beats[0].model_copy(update={'target_delta': (*beats[0].target_delta, carrier)})
    scenes[0] = scenes[0].model_copy(update={'beats': tuple(beats)})
    moved_body = body.model_copy(update={'scenes': tuple(scenes)})
    moved_binding = binding.model_copy(update={'fact_bindings': tuple(
        f.model_copy(update={'beat_ref': 'sc01#b01'}) for f in binding.fact_bindings)})
    hook = repo.read_graph_hook()
    # repository 正式写入并封存，模拟旧接受判据发布的现役 schema 产物；
    # 拒绝原因必须是纯位置约束，不能靠字节损坏、schema 或 provenance 守卫代替。
    def publish_recovery_inputs(repository, current_binding, reason):
        ledger = CostLedger(path=ledger_path(repository.root.parent))
        # 每个 writer 用独立输入窗口，并真实读取其上游。否则沿用 compile 的
        # last_inputs 会把旧 Binding 指纹带进重写 Graph，先被 stale 守卫挡住。
        repository.attach_context(make_context(
            repository.root, ledger=ledger, node='str06.stage4.body_design'))
        for rel in ('runtime/preserve_checkpoint.json',
                    'stage2/characters/characters.json',
                    'stage2/blueprints/chapter_blueprints.json'):
            repository.read_model(rel)
        ref = synthetic_ref('design', reason)
        repository.publish((
            PublishItem(rel='stage4/design/body_design.json', value=moved_body,
                        refs=(ref,)),
            PublishItem(rel='stage4/grounding/semantic_binding.json', value=current_binding,
                        refs=(ref,), claim_usage=False),
        ))
        repository.attach_context(make_context(
            repository.root, ledger=ledger, node='str06.stage4.hook_design'))
        current_body = repository.read_model('stage4/design/body_design.json')
        repository.read_model('stage4/grounding/semantic_binding.json')
        repository.read_model('stage4/grounding/reconstruction_brief.json')
        graph = publish_graph(current_body, hook)
        repository.write('stage4/design/presentation_graph.json', graph,
                         refs=(synthetic_ref('hook_design', reason),))

    publish_recovery_inputs(repo, moved_binding, '存量错位盘面')
    calls = len(transport.calls)
    costs = ledger_path(repo.root.parent).read_bytes()
    reopened = Str06Repository(repo.root)
    reopened.attach_context(make_context(
        reopened.root, ledger=CostLedger(path=ledger_path(repo.root.parent)),
        node='str06.stage4.compile'))
    resumed = replace(ctx, repository=reopened)
    with pytest.raises(MechanicalDefectStop) as stopped:
        run_compile_stage4(resumed)
    assert stopped.value.node == 'str06.stage4.compile'
    message = str(stopped.value)
    assert 'ob02' in message and KF_PLAN in message and 'sc01#b01' in message
    assert before_outputs == {rel: reopened.path(rel).read_bytes() for rel in outputs}

    repaired_binding = moved_binding.model_copy(update={'entries': tuple(
        e.model_copy(update={'supporting_beat_refs': ('sc01#b01',)})
        if e.obligation_id == 'ob02' else e for e in moved_binding.entries)})
    publish_recovery_inputs(reopened, repaired_binding, '恢复显式支撑位')
    reopened.attach_context(make_context(
        reopened.root, ledger=CostLedger(path=ledger_path(repo.root.parent)),
        node='str06.stage4.compile'))
    report = run_compile_stage4(resumed)
    assert not report['guard_errors']
    assert all(reopened.read_model(rel) is not None for rel in outputs[:3])
    repaired = reopened.read_model('stage4/grounding/semantic_binding.json')
    [entry] = [e for e in repaired.entries if e.obligation_id == 'ob02']
    assert entry.supporting_beat_refs == ('sc01#b01',)
    assert len(transport.calls) == calls
    assert ledger_path(repo.root.parent).read_bytes() == costs, '拒绝和恢复都只走免费 compiler'
