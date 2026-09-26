"""真实审阅反例的最小回归：消费身份、状态、因果、免费采用和请求成本。"""
from __future__ import annotations

from copy import deepcopy
from dataclasses import replace
import json
import os
import subprocess
import sys

import pytest
from pydantic import ValidationError

from contracts.schema.design import TargetDelta
from contracts.schema.grounding import BindingEntry, SemanticBinding
from contracts.schema.state import StateAddress, SurfaceIntroduction
from novel.stages.stage4.compile import compile_execution_plan
from novel.stages.common import project_context
from novel.stages.stage4.fold import fold_accepted_prefix, FoldedState
from novel.stages.common import canonical_sha256
from novel.stages.stage4.guard import run_mechanical_guard
from novel.stages.stage4.grounding import assemble_preserve, project_brief, assemble_binding
from novel.stages.stage2.source import ChapterCompiler, assemble_blueprint, chapter_input_snapshot
from tests.str06_facilities import make_context
from tests.test_str06_execution_ir import _graph, _binding, _cp, _ob
from tests.test_str06_target import _assemble, _preserve_wire, _source_pair
from tests.test_str06_source import _compile_pair, _chapter_wire


def contexts(graph, binding):
    plan, errors = compile_execution_plan(graph, binding)
    assert not errors, errors
    return {s.scene_id: project_context(
        scene_id=s.scene_id, graph=graph, plan=plan, binding=binding,
        folded_prefix=fold_accepted_prefix(plan.scenes[:i], initial=plan.initial_establishments))
        for i, s in enumerate(graph.scenes)}


def test_diamond_requires_is_not_cycle_and_brief_all_kinds_are_closed():
    chars, bp = _source_pair()
    wire = _preserve_wire()
    wire['obligations'][0]['requires'] = ['ob02', 'ob03']
    wire['obligations'][1]['requires'] = ['ob03']
    cp, errors = assemble_preserve(wire, policy_id='p', policy_version=1, characters=chars,
                                  blueprint=bp, provider='x', model='x', request_sha256='a'*64)
    assert not errors, errors
    brief, _ = project_brief(cp, chars)
    assert len(brief.requirements) == 3
    ids = {c.core_id for c in brief.requirements}
    assert all(set(c.requires_cores) <= ids for c in brief.requirements)
    assert [c.core_id for c in brief.requirements] == sorted(ids)
    assert {c.satisfaction for c in brief.requirements} == {o.satisfaction for o in cp.obligations}


def test_binding_accepts_full_wire_beat_id_and_rejects_omission():
    _, _, cp = _assemble()
    wire = {'entries': [{'obligation_id': o.obligation_id, 'scene_id': 's01', 'beat_ref': 's01#bt1'}
                        for o in cp.obligations], 'unbound': []}
    binding, errors = assemble_binding(wire, cp, {'s01': {'s01#bt1'}})
    assert not errors and binding.obligations == cp.obligations
    del wire['entries'][0]['beat_ref']
    assert assemble_binding(wire, cp, {'s01': {'bt1'}})[1]


def test_reversed_acyclic_dependency_is_rejected():
    cp = _cp(_ob('ob1', requires=('ob2',)), _ob('ob2'))
    binding = _binding(cp)
    binding.entries = (BindingEntry(obligation_id='ob1', scene_id='s01', beat_ref='s01#bt1'),
                       BindingEntry(obligation_id='ob2', scene_id='s02', beat_ref='s02#bt1'))
    assert any('逆序' in e for e in compile_execution_plan(_graph(), binding)[1])


@pytest.mark.parametrize('change', ['satisfaction', 'pov', 'avoid', 'hint', 'delta', 'brief'])
def test_actual_business_change_invalidates_only_own_context(change):
    cp = _cp(_ob('ob1'))
    graph, binding = _graph(), _binding(cp)
    before = contexts(graph, binding)
    if change == 'satisfaction':
        binding.obligations[0].satisfaction = '明确呈现生理欲望，不能写成爱上他'
    elif change == 'pov':
        graph.scenes[0].delivery.pov = 'narrator'
    elif change == 'avoid':
        graph.scenes[0].delivery.avoid = ('不得提前披露',)
    elif change == 'hint':
        graph.scenes[0].beats[0].delivery_hint = '先动作后解释'
    elif change == 'brief':
        graph.scenes[0].realization_brief = '重心改为暗中交锋。'
    else:
        graph.scenes[0].beats[0].target_delta[0].value = '冬'
    after = contexts(graph, binding)
    # slice 身份变化 = 该场消费内容变化（章级失效判定的材料）
    assert before['s01'][1] != after['s01'][1]
    assert before['s02'] == after['s02']  # s02 没有消费 s01 的该状态/义务
    # avoid 经投影进入 IR 的明确禁止事项（不进呈现视图）
    if change == 'avoid':
        assert after['s01'][0].verification_ir.prohibitions == ('不得提前披露',)
        assert '不得提前披露' not in '\n'.join(
            v for v in after['s01'][0].writer_brief.model_dump().values()
            if isinstance(v, str))


def test_state_read_change_invalidates_consumer_and_unknown_blocks():
    cp = _cp(_ob('ob1'))
    graph, binding = _graph(), _binding(cp)
    graph.scenes[1].state_reads = (StateAddress(domain='world', key='season'),)
    before = contexts(graph, binding)
    graph.scenes[0].beats[0].target_delta[0].value = '冬'
    assert before['s02'][1] != contexts(graph, binding)['s02'][1]
    graph.scenes[0].beats[0].target_delta = (TargetDelta(
        domain='world', key='season', op='unknown', value=None, scope='book'),)
    with pytest.raises(ValueError, match='unknown'):
        contexts(graph, binding)


def test_compound_obligation_uses_prior_beats_without_premature_whole_fact():
    cp = _cp(_ob('ob30'))
    graph = _graph()
    binding = _binding(cp)
    binding.entries = (BindingEntry(obligation_id='ob30', scene_id='s02', beat_ref='s02#bt1',
                                   supporting_beat_refs=('s01#bt1',)),)
    context = contexts(graph, binding)['s02'][0]
    assert context.verification_ir.obligations == cp.obligations
    assert context.verification_ir.continuity_fact_view == ('s01#bt1：事件s01',)
    # 义务清单属于事实约束视图（IR），不在呈现视图里重复
    assert '义务ob30' in context.verification_ir.obligations[0].statement
    assert not hasattr(context.writer_brief, 'required_content')
    binding.entries[0].supporting_beat_refs = ('s02#bt1',)
    assert compile_execution_plan(graph, binding)[1]


def test_scene_overlay_expires_to_book_and_unset_is_not_unknown():
    from contracts.schema.execution import Establishment
    base = Establishment(domain='world', key='weather', op='set', value='晴', scope='book', established_by='init')
    temporary = base.model_copy(update={'value': '雨', 'scope': 'scene', 'established_by': 's01#bt1'})
    state = FoldedState().apply('init', [base]).apply('s01', [temporary])
    assert state.entry_state_view('s01')[0].value == '雨'
    state.expire_scene_scope('s01')
    assert state.entry_state_view('s02')[0].value == '晴'
    state.apply('s02', [base.model_copy(update={'op': 'unset', 'value': None})])
    assert state.entry_state_view('s03')[0].status == 'unset'


def test_explicit_knowledge_holder_and_character_subjects_are_required():
    for delta in [dict(domain='knowledge', key='lu_secret', op='set', value='隐疾', scope='book'),
                  dict(domain='character', key='appearance', op='set', value='俊美', scope='book')]:
        with pytest.raises(ValidationError):
            TargetDelta(**delta)


def test_surface_release_is_explicit_and_guard_rechecks_it():
    cp = _cp(_ob('ob1'))
    graph, binding = _graph(), _binding(cp)
    graph.scenes[1].beats[0].surface_introductions = (
        SurfaceIntroduction(char_id='ch1', surfaces=('苏姑娘',)),)
    all_contexts = contexts(graph, binding)
    assert all_contexts['s01'][0].verification_ir.released_surfaces == ()
    assert all_contexts['s02'][0].verification_ir.released_surfaces[0].surfaces == ('苏姑娘',)
    context = all_contexts['s01'][0]
    context.verification_ir.released_surfaces = all_contexts['s02'][0].verification_ir.released_surfaces
    assert any(e.startswith('M5') for e in run_mechanical_guard(binding=binding, context=context, graph=graph))


def test_named_absent_character_and_introduction_beat_reach_writer():
    graph, binding = _graph(), _binding(_cp(_ob('ob1')))
    graph.scenes[1].beats[0].surface_introductions = (
        SurfaceIntroduction(char_id='ch2', surfaces=('裴公子',)),)
    context = contexts(graph, binding)['s02'][0]
    assert 'ch2' not in graph.scenes[1].beats[0].participants
    assert context.writer_brief.usable_surfaces[0].char_id == 'ch2'
    assert '第 bt1 拍起才可使用：裴公子' in context.writer_brief.delivery_hints
    assert run_mechanical_guard(binding=binding, context=context, graph=graph) == []


def test_source_prior_snapshot_is_latest_and_validation_has_no_side_effect():
    chars, wcs, compiler, ch1 = _compile_pair()
    before = deepcopy(compiler.__dict__)
    assert compiler.validate(_chapter_wire(), 2) == []
    assert compiler.__dict__ == before
    ch2, _ = compiler.compile(_chapter_wire(), 2)
    snapshot = chapter_input_snapshot(chars, assemble_blueprint(wcs, [ch1, ch2]), 3)
    assert any('wariness' in item for item in snapshot)
    with pytest.raises(ValueError, match='连续'):
        chapter_input_snapshot(chars, assemble_blueprint(wcs, [ch1]), 3)


@pytest.mark.parametrize('fault', ['evidence', 'effect_subject', 'holder', 'bad_edge'])
def test_source_reference_closure_and_no_silent_edge_drop(fault):
    _, _, compiler, _ = _compile_pair()
    wire = _chapter_wire()
    if fault == 'evidence': wire['beats'][0]['source_refs'] = ['ghost']
    elif fault == 'effect_subject': wire['beats'][0]['effects'][0]['target']['char_id'] = 'ghost'
    elif fault == 'holder': wire['beats'][0]['cognition'][0]['holder'] = 'ghost'
    else: wire['causal_edges'] = [{'from_beat': 'b01_01', 'to_beat': 'b02_01', 'kind': 'bogus'}]
    assert compiler.compile(wire, 2)[1]


def test_compiler_hash_stable_across_process_seeds():
    code = """from tests.test_str06_repairs import contexts
from tests.test_str06_execution_ir import _graph,_binding,_cp,_ob
from novel.stages.common import canonical_sha256
from novel.stages.stage4.compile import compile_execution_plan
cp=_cp(_ob('obZ'),_ob('obA'),_ob('obM'));g=_graph();b=_binding(cp)
p,e=compile_execution_plan(g,b)
print(canonical_sha256(p),canonical_sha256(contexts(g,b)['s01'][0]))
"""
    outputs = [subprocess.check_output([sys.executable, '-B', '-c', code], text=True,
                                      env={**os.environ, 'PYTHONHASHSEED': seed})
               for seed in ['1', '2', '3']]
    assert len(set(outputs)) == 1


def test_paid_response_durable_before_validation_and_reused(tmp_path):
    from novel.llm.paid_requests import PaidRequestSession
    from novel.llm.transport import ChatRequest, ChatResponse
    from novel.llm.ledger import CostLedger, ledger_path, read_cost_snapshot
    from novel.store import Str06Repository
    class Transport:
        calls = 0
        def send(self, request):
            self.calls += 1
            return ChatResponse(content='{"entries": []}', usage={'prompt_tokens': 10, 'completion_tokens': 20})
    transport = Transport()
    repo = Str06Repository(tmp_path/'book')
    ledger = CostLedger(path=ledger_path(tmp_path))
    repo.attach_context(make_context(tmp_path/'book', ledger=ledger))
    session = PaidRequestSession(repo, ledger, transport, yes=True, allow_source_export=True,
                                 provider='fake', sid='book')
    request = ChatRequest(model='fake', system='s', user='u', reasoning_effort='high',
                          thinking=True, json_mode=True, max_tokens=50)
    with pytest.raises(ValueError, match='免费修复'):
        session.ask('bind', request, lambda _: ['缺必保义务'])
    # 跨 session、同请求，免费重新校验已购原响应。
    session2 = PaidRequestSession(repo, CostLedger(path=ledger_path(tmp_path)), transport,
                                  provider='fake', sid='book')
    assert session2.ask('bind', request, lambda _: []) == {'entries': []}
    assert transport.calls == 1
    rows, _ = read_cost_snapshot(tmp_path)
    assert len([r for r in rows if not r['cached']]) == 1
    assert len([r for r in rows if r['cached']]) == 1
    assert rows[0]['completion_tokens'] == 20 and rows[0]['reasoning_tokens'] is None
    assert rows[1]['prompt_tokens'] == rows[1]['completion_tokens'] == 0
    assert sum(row['completion_tokens'] for row in rows) == 20
    with pytest.raises(ValueError, match='--yes'):
        session2.ask('bind', replace(request, user='changed'), lambda _: [])


def test_target_rc1_missing_semantics_is_rejected():
    from novel.stages.stage4.design import assemble_body_design
    from tests.test_str06_target import _design_wire
    wire = _design_wire()
    del wire['initial_state']
    assert assemble_body_design(wire, known_char_ids={'yun','fu_jun'})[1]


@pytest.mark.parametrize('failure', ['transport', 'duplicate_json'])
def test_failed_request_is_preserved_and_never_automatically_rebought(tmp_path, failure):
    from novel.llm.paid_requests import PaidRequestSession
    from novel.llm.transport import ChatRequest, ChatResponse
    from novel.llm.ledger import CostLedger, ledger_path, read_cost_snapshot
    from novel.store import Str06Repository
    class Transport:
        calls = 0
        def send(self, request):
            self.calls += 1
            if failure == 'transport':
                raise TimeoutError('模拟未确认请求')
            return ChatResponse(content='{"a":1,"a":2}', usage=None)
    transport = Transport()
    repo = Str06Repository(tmp_path/'book')
    ledger = CostLedger(path=ledger_path(tmp_path))
    repo.attach_context(make_context(tmp_path/'book', ledger=ledger))
    session = PaidRequestSession(repo, ledger, transport,
                                 provider='fake', yes=True, allow_source_export=True,
                                 sid='book')
    request = ChatRequest(model='fake', system='s', user='u', reasoning_effort='high',
                          thinking=True, json_mode=True, max_tokens=50)
    with pytest.raises((ValueError, TimeoutError)):
        session.ask('design', request, lambda _: [])
    with pytest.raises(ValueError):
        session.ask('design', request, lambda _: [])
    assert transport.calls == 1
    rows, partial = read_cost_snapshot(tmp_path)
    assert not partial and len([r for r in rows if not r['cached']]) == 1
    assert rows[0]['prompt_tokens'] is None and rows[0]['completion_tokens'] is None
