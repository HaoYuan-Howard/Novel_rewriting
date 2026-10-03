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
from contracts.schema.observation import (
    STAGE2_CHAPTERS_PROTOCOL as CPV, STAGE2_OBSERVATION_PROTOCOL as PV,
)
from contracts.schema.state import StateRead, SurfaceIntroduction
from novel.stages.stage4.compile import compile_execution_plan
from novel.stages.common import project_context
from novel.stages.stage4.fold import fold_accepted_prefix, FoldedState
from novel.stages.common import canonical_sha256
from novel.stages.stage4.guard import run_mechanical_guard
from novel.stages.stage4.grounding import assemble_preserve, derive_semantic_binding, project_brief
from novel.stages.stage2 import compile_source_truth
from tests.str06_facilities import make_context
from tests.test_str06_execution_ir import _graph, _binding, _cp, _ob
from tests.test_str06_target import _assemble, _preserve_wire, _source_pair
from tests.str06_expression_inputs import (
    sample_narration, sample_requirements,
)


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
    wire['obligations'][0]['requires'] = [2, 3]
    wire['obligations'][1]['requires'] = [3]
    cp, errors = assemble_preserve(wire, policy_id='p', policy_version=1, characters=chars,
                                  blueprint=bp, provider='x', model='x', request_sha256='a'*64)
    assert not errors, errors
    brief, _ = project_brief(cp, chars, bp)
    assert len(brief.requirements) == 3
    ids = {c.core_id for c in brief.requirements}
    assert all(set(c.requires_cores) <= ids for c in brief.requirements)
    assert [c.core_id for c in brief.requirements] == sorted(ids)
    assert {c.satisfaction for c in brief.requirements} == {o.satisfaction for o in cp.obligations}


def test_binding_derivation_closes_denominator_and_rejects_out_of_range():
    """CS-3 bind 退役：binding 由 real 标注投影派生——全分母闭合、无标注
    义务进 unbound；标注拍越界（Design 拍闭集外）精确拒。"""
    _, _, cp = _assemble()
    graph = _graph()
    placements = {o.obligation_id: [(0, 0)] for o in cp.obligations}
    binding, errors = derive_semantic_binding(placements, graph, cp)
    assert not errors and binding.obligations == cp.obligations
    assert binding.entries[0].beat_ref == 's01#bt1'
    assert not binding.unbound
    # 无标注义务不删：进 unbound（owner=design）
    partial = {o.obligation_id: [(0, 0)] for o in cp.obligations[:-1]}
    binding2, errors2 = derive_semantic_binding(partial, graph, cp)
    assert not errors2
    assert [u.obligation_id for u in binding2.unbound] == [
        cp.obligations[-1].obligation_id]
    # 标注拍越界（闭集外）即拒
    bad = {o.obligation_id: [(9, 9)] for o in cp.obligations}
    assert derive_semantic_binding(bad, graph, cp)[1]


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
    graph.scenes[1].state_reads = (StateRead(domain='world', key='season'),)
    before = contexts(graph, binding)
    graph.scenes[0].beats[0].target_delta[0].value = '冬'
    assert before['s02'][1] != contexts(graph, binding)['s02'][1]
    graph.scenes[0].beats[0].target_delta = (TargetDelta(
        domain='world', key='season', op='unknown', value=None, scope='book'),)
    # unknown 消费在 compile 的章闭包证明即拦下（fail fast，免费重验），
    # 不留给写手投影（I-34；B3 后半起证明与投影共用同一解析实现）。
    _, errors = compile_execution_plan(graph, binding)
    assert any('unknown' in e for e in errors)


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
    assert '第 1 拍起才可使用：裴公子' in context.writer_brief.delivery_hints
    assert run_mechanical_guard(binding=binding, context=context, graph=graph) == []


# ⚠ B5（S-05）：`test_source_prior_snapshot_is_latest_and_validation_has_no_side_effect`
# 已随 D3 退役面（逐章输入快照 chapter_input_snapshot）删除——章观察互不依赖后
# 「前章快照严格前缀」没有对象；validator 不改输入由
# tests/test_str06_stage2_owners.py::test_book_observation_never_modifies_the_wire 承接。


def _stage2_outcome_with_ch02(ch02: dict):
    """ch02 观察替换成红例、其余走绿底座 → compile_source_truth。"""
    from tests.test_str06_stage2_publish import (
        EVIDENCE, _associate_wire, _book_wire, _chapter_wires, _names_wire,
    )
    from novel.stages.stage2 import (
        AssociateObservation, BookObservation, ChapterObservation, NamesObservation,
    )
    chapters = _chapter_wires()
    chapters['ch02'] = ch02
    return compile_source_truth(
        book=BookObservation.model_validate(_book_wire()),
        chapters={no: ChapterObservation.model_validate(chapters[f'ch{no:02d}'])
                  for no in range(1, len(chapters) + 1)},
        names=NamesObservation.model_validate(
            {'protocol_version': PV, 'structure': _names_wire()}),
        associate=AssociateObservation.model_validate(_associate_wire()),
        evidence=EVIDENCE)


@pytest.mark.parametrize('fault', ['evidence', 'effect_subject', 'holder'])
def test_source_reference_closure_and_no_silent_edge_drop(fault):
    """章观察的引用闭合（D3 章内证据；人物闭集归 compile）。旧链因果边形状随
    D3「章观察无因果边」退役——跨章因果改经 associate 配对（见
    tests/test_str06_stage2_publish.py::test_cross_chapter_cause_lands_in_the_to_chapter）。"""
    ch02 = {'protocol_version': CPV, 'beats': [{
        'event': '云娘向夫君试探', 'participants': ['yun_niang', 'fu_jun'],
        'source_refs': ['e02_01'], 'scene_boundary': 'new',
        'temporal_mode': 'single',
        'effects': [{'target': {'kind': 'character', 'char_id': 'yun_niang'},
                     'key': 'wariness', 'op': 'set', 'value': '高',
                     'scope': 'book', 'modality': 'actual',
                     'disclosure': {'visibility': 'on_page'},
                     'basis': [{'segment': 'e02_01', 'quote': '云娘质问床底的人'}]}],
        'cognition': [{'proposition': {'ref': 'p_secret',
                                       'statement': '借种之约是夫妻共同秘密'},
                       'holder': 'yun_niang', 'mode': 'learns',
                       'basis': [{'segment': 'e02_01',
                                  'quote': '云娘质问床底的人'}]}],
        'loops': []}]}
    beat = ch02['beats'][0]
    if fault == 'effect_subject':
        beat['effects'][0]['target']['char_id'] = 'ghost'
        outcome = _stage2_outcome_with_ch02(ch02)
        findings = outcome.errors + outcome.undecided
    elif fault == 'holder':
        beat['cognition'][0]['holder'] = 'ghost'
        outcome = _stage2_outcome_with_ch02(ch02)
        findings = outcome.errors + outcome.undecided
    else:
        # 本章证据句段闭集归章观察 owner（引用闭集之外的段 id 即精确 issue）。
        from tests.test_str06_stage2_publish import EVIDENCE
        from novel.stages.stage2 import compile_chapter_observation
        beat['source_refs'] = ['ghost']
        ch02['beats'][0]['source_refs'] = ['ghost']
        _observation, findings = compile_chapter_observation(
            ch02, evidence_ids=frozenset(EVIDENCE))
    assert findings, fault


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
    from novel.llm.paid_requests import ExecutionError, PaidRequestSession, ask_json
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
    with pytest.raises(ValueError, match='重问后无进展'):
        ask_json(session, 'preserve', request, lambda _: ['缺必保义务'])
    # R3：原轮＋一次修订轮后同问题集停（有界，不无限付费）；跨 session、同
    # 请求，免费重新校验已购原响应——恢复零外发。
    session2 = PaidRequestSession(repo, CostLedger(path=ledger_path(tmp_path)), transport,
                                  provider='fake', sid='book')
    assert ask_json(session2, 'preserve', request, lambda _: [])[0] == {'entries': []}
    assert transport.calls == 2
    rows, _ = read_cost_snapshot(tmp_path)
    assert len([r for r in rows if not r['cached']]) == 2
    assert len([r for r in rows if r['cached']]) == 1
    assert rows[0]['completion_tokens'] == 20 and rows[0]['reasoning_tokens'] is None
    assert rows[2]['prompt_tokens'] == rows[2]['completion_tokens'] == 0
    assert sum(row['completion_tokens'] for row in rows) == 40
    with pytest.raises(ValueError, match='--yes'):
        ask_json(session2, 'preserve', replace(request, user='changed'), lambda _: [])


def test_same_root_owner_patch_bootstraps_and_version_mismatch_refuses(tmp_path):
    """F-4：同根 owner patch 首次采用可引导（adopt 记录缺失即本次建档），
    非补丁通路的改写 wire 仍拒；补丁版本不符（已建档的另一 sha）也拒。

    真实验证轮实证：ch04 补丁 wire 编译干净、patch note 基线身份相符，但
    adopt_ch04.json 尚未存在（旧逻辑要求它预存在）而被拒——同根自补丁永远
    死循环。安全边界不变：已购行身份／成本仍逐项核验，编译仍先过闸。
    """
    from novel.llm.paid_requests import PaidRequestSession, digest
    from novel.llm.ledger import CostLedger, ledger_path
    from novel.store import Str06Repository

    book = tmp_path / 'book'
    repo = Str06Repository(book)
    ledger = CostLedger(path=ledger_path(tmp_path))
    repo.attach_context(make_context(book, ledger=ledger))
    identity, attempt = 'a' * 64, 'b' * 32
    content = json.dumps({'beats': [{'beat_id': 'b04_01'}]}, ensure_ascii=False)
    repo.write(f'runtime/request_{identity}.json', {
        'identity': identity, 'status': 'answered', 'attempt_id': attempt,
        'content': content, 'content_sha256': digest(content)})
    ledger.record(node='str06.stage2.source', sid='book', model='fake',
                  request_model='fake', code='str06-rc4', cached=False,
                  cache_policy='request-identity', purpose='ch04',
                  attempt_id=attempt, request_fingerprint=identity,
                  outcome='ok', prompt_tokens=10, completion_tokens=20)

    class Transport:
        def send(self, request):
            raise AssertionError('本测试不得触达 transport')

    session = PaidRequestSession(repo, ledger, Transport(), yes=True,
                                 allow_source_export=True, provider='fake',
                                 sid='book', adopt_roots=(book,))
    wire = tmp_path / 'ch04_wire.json'
    wire.write_text(json.dumps({'beats': [{'beat_id': 'b04_01', 'patched': True}]},
                               ensure_ascii=False), encoding='utf-8')
    # 绿：同根首次补丁引导——记录缺失，身份／成本／编译三道闸已过。
    bound = session.bind_wire_adoption('ch04', identity, wire, allow_patch=True)
    assert bound['mode'] == 'historical-adoption' and bound['owner_patch'] is True
    assert bound['attempt_id'] == attempt and bound['cost_link'] == 'ledger-row'
    # 红 1：非补丁通路的改写 wire 仍拒。
    with pytest.raises(ValueError, match='无匹配的'):
        session.bind_wire_adoption('ch04', identity, wire, allow_patch=False)
    # 红 2：补丁版本不符（已建档的另一 sha）仍拒。
    repo.write('runtime/adopt_ch04.json', {'owner': 'ch04', 'sha256': 'f' * 64})
    with pytest.raises(ValueError, match='无匹配的'):
        session.bind_wire_adoption('ch04', identity, wire, allow_patch=True)


def test_target_rc1_missing_semantics_is_rejected():
    from novel.stages.stage4.design import assemble_body_design
    from tests.test_str06_target import _design_wire
    wire = _design_wire()
    del wire['initial_state']
    assert assemble_body_design(wire, known_char_ids={'yun','fu_jun'}, expression_requirements=sample_requirements(), source_narration=sample_narration())[1]


@pytest.mark.parametrize('failure', ['transport', 'duplicate_json'])
def test_failed_request_is_preserved_and_never_automatically_rebought(tmp_path, failure):
    from novel.llm.paid_requests import ExecutionError, PaidRequestSession, ask_json
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
    with pytest.raises(Exception, match='结果未知|形状失败'):
        ask_json(session, 'design', request, lambda _: [])
    with pytest.raises((ValueError, ExecutionError)):
        ask_json(session, 'design', request, lambda _: [])
    # R3：wire 形状失败先经一次有界修订轮（原轮＋修订轮＝2 次外发，同问题集
    # 重复即停）；unknown 侧不自动重试，保持 1 次。第二次调用全部按请求身份
    # 免费重放，外发数不再增长。
    expected_calls = 1 if failure == 'transport' else 2
    assert transport.calls == expected_calls
    rows, partial = read_cost_snapshot(tmp_path)
    assert not partial and len([r for r in rows if not r['cached']]) == expected_calls
    assert rows[0]['prompt_tokens'] is None and rows[0]['completion_tokens'] is None
