"""CS-F2 stage：事实锁在已选状态上标注引用，经真实 Stage2→Preserve→body_design→
compile 链发布映射并被下游消费（fake transport，零付费）。

验证面（单元反例在 `test_str06_a4_design_locks.py` 的 CS-F2 一节）：
· Preserve 义务锚定「预计」效应 → Brief 事实锁及必保兑现分母；
· body_design 用 v8 `lock` 标注已选状态 → BodyDesign 与 SemanticBinding 同组发布，
  Binding 只存锁→拍→地址、不复制 value，BodyDesign 里没有 kf 状态键；
· 下游 compile 真实消费：WriterBookIR 的写手材料带「预计，尚未发生」措辞、不含 kf 句柄，
  packet_metrics 计入锁承载；
· 漏标注的坏稿：同节点修订轮收到带规则码与 wire 路径的问题，粘性坏稿停止且不发布半份产物。
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


def _wires(*, marked: bool = True) -> dict:
    """在全链基座上加一条「预计」效应、锚定它的必保义务，以及标注（或漏标）该锁的状态。"""
    wires = deepcopy(_full_chain_wires())
    wires['ch01']['beats'][0]['effects'] = (
        *wires['ch01']['beats'][0]['effects'],
        {'target': {'kind': 'world'}, 'key': 'return_time', 'op': 'set', 'value': '约半月',
         'scope': 'book', 'modality': 'expected',
         'proposition': {'ref': 'p_return', 'statement': '夫君约半月后归家'},
         'disclosure': {'visibility': 'on_page'},
         'basis': ({'segment': 'e01_01', 'quote': '苏芸娘嫁入陆家'},)})
    wires['preserve']['obligations'].append({
        'kind': 'event_core', 'statement': '夫君约定归期（预计，尚未发生）',
        'anchors': ['b01_01.e2'], 'must_preserve': True,
        'satisfaction': '归期作为预计呈现，不得写成已经归来', 'requires': []})
    beat = wires['design']['scenes'][0]['beats'][2]         # sc01#b03 '芸娘立誓查清'
    beat['real'] = ['ob02']
    beat['state'].append({'at': 'world', 'key': 'return_time', 'val': '约半月后归家',
                          'modality': 'expected', **({'lock': KF_PLAN} if marked else {})})
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
    assert binding.contract_version == 'str06-rc16'
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
