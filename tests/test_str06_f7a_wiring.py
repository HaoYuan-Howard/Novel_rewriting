"""F7a-2 候选链接线测试：provenance／freshness／INVALID／工作单元／书级总闸／epoch。

覆盖计划 F7a-2 的三类验收面：
① repository 接正式 Store 机制——已登记产物带来源账、未登记不带、无上下文拒绝、
   stale 拒绝、inspect 不记账、INVALID 归档与提升；
② 付费边界接正式设施——新购取内容轮、ledger 行带候选节点归属、单元上限、
   未采纳轮关闭、unknown 轮阻断重购、整书总闸持久跨 session；
③ workflow／epoch 身份——首开、复用、代码漂移滚动、源输入变化与契约漂移拒绝。
"""
from __future__ import annotations

import json

import pytest

from contracts.schema.common import FreeProvenance, LlmProvenance
from novel.llm.paid_requests import open_candidate_epoch
from contracts.schema.hook import HookConstraints
from novel.errors import ProvenanceRefused, UpstreamStale
from novel.llm.ledger import CostLedger, ledger_path, read_cost_snapshot
from novel.llm.paid_requests import (
    AdoptionRef, ExecutionError, PaidRequestSession, PurchaseRequired, ask_json,
)
from novel.llm.transport import ChatRequest, ChatResponse
from novel.store import Str06Repository
from novel.work_units import (
    WorkUnitBudget, WorkUnitBudgetExhausted, WorkUnitRefused, read_book_budget,
)
from tests.str06_facilities import candidate_epoch, make_context


def _repo(tmp_path, *, ledger=None, node='str06.stage2.names'):
    repo = Str06Repository(tmp_path / 'run')
    repo.attach_context(make_context(tmp_path / 'run', ledger=ledger or CostLedger(path=None),
                                     node=node))
    return repo


def _request(**kw):
    base = dict(model='fake', system='系统', user='用户', reasoning_effort='high',
                thinking=True, json_mode=True, max_tokens=100)
    base.update(kw)
    return ChatRequest(**base)


class _Transport:
    def __init__(self, content='{"entries": []}', usage=None):
        self.content, self.usage, self.calls = content, usage, 0

    def send(self, request):
        self.calls += 1
        return ChatResponse(content=self.content,
                            usage=self.usage or {'prompt_tokens': 7, 'completion_tokens': 20})


# ─── repository 接正式 Store 机制 ────────────────────────────────────────────

def test_registered_write_carries_provenance_by_node_cost(tmp_path):
    repo = _repo(tmp_path, node='str06.stage4.compile')
    repo.write('stage4/compiled/execution_plan.json', {'scenes': []})
    stored = repo.inspect('stage4/compiled/execution_plan.json')
    provenance = stored['_provenance']
    assert 'model' not in provenance, '免费节点不得记 model（假账防线）'
    assert provenance['inputs'] == {}
    FreeProvenance.model_validate(provenance)


def test_llm_provenance_records_adopted_model_and_prompts(tmp_path):
    repo = _repo(tmp_path, node='str06.stage7.naming')
    ref = AdoptionRef(owner='naming', model='deepseek-chat',
                      prompts={'system': '系统', 'user': '用户'})
    repo.write('stage7/naming/name_map.json', {'entries': []}, refs=(ref,))
    provenance = repo.inspect('stage7/naming/name_map.json')['_provenance']
    assert provenance['model'] == 'fake' and provenance['adopted_models'] == ['deepseek-chat']
    assert any(k.startswith('naming.') for k in provenance['prompts'])
    LlmProvenance.model_validate(provenance)


def test_paid_write_without_adoption_evidence_is_refused(tmp_path):
    repo = _repo(tmp_path, node='str06.stage7.naming')
    with pytest.raises(ProvenanceRefused, match='显式采用引用'):
        repo.write('stage7/naming/name_map.json', {'entries': []})


def test_unregistered_runtime_write_has_no_provenance(tmp_path):
    repo = _repo(tmp_path)
    repo.write('runtime/workflow_status.json', {'status': 'running'})
    assert '_provenance' not in repo.inspect('runtime/workflow_status.json')


def test_repository_refuses_registered_write_without_context(tmp_path):
    repo = Str06Repository(tmp_path / 'run')
    with pytest.raises(ProvenanceRefused, match='未附加上下文'):
        repo.write('stage7/naming/name_map.json', {'entries': []})
    # 未登记路径仍按路径准入先行拒绝，不依赖上下文。
    from novel.errors import PathRefused
    with pytest.raises(PathRefused):
        repo.write('unregistered/nested/file.json', {'x': 1})


def test_provenance_field_never_enters_model_dump(tmp_path):
    """exclude=True：prompt／请求字节与 F5e 已购身份逐字不变。"""
    from contracts.schema.naming import NameAssignment, NameMapV5, TargetName
    model = NameMapV5(entries=[NameAssignment(char_ref='c1',
                                              name=TargetName(family='萧', given='知言',
                                                              full='萧知言'))])
    assert 'provenance' not in model.model_dump(mode='json')
    round_trip = NameMapV5.model_validate(
        {**model.model_dump(mode='json'), '_provenance': {
            'code': 'c' * 40, 'root_clean_sha1': 'a' * 40, 'inputs': {}}})
    assert round_trip.provenance is not None
    assert 'provenance' not in round_trip.model_dump(mode='json')


def test_stale_upstream_read_is_refused_and_inspect_records_nothing(tmp_path):
    repo = _repo(tmp_path, node='str06.stage4.compile')
    repo.write('stage4/compiled/execution_plan.json', {'scenes': []})
    upstream = repo.inspect('stage4/compiled/execution_plan.json')
    # 伪造一份记录了上游旧指纹的产物：上游被改写后读它必须拒绝。
    repo.context.record_edge('stage4/compiled/execution_plan.json', '0' * 10)
    repo.write('stage4/ir/writer_book_ir.json', {'book_brief': {}, 'chapters': []})
    repo.write('stage4/compiled/execution_plan.json', {'scenes': [{'scene_id': 's01'}]})
    with pytest.raises(UpstreamStale, match='已过期'):
        repo.read('stage4/ir/writer_book_ir.json')
    # inspect 不记边、不判过期：断言与视图不会毒化生产来源账。
    before = len(repo.context.edges)
    assert repo.inspect('stage4/compiled/execution_plan.json')['scenes']
    assert len(repo.context.edges) == before


def test_write_invalid_archives_rounds_and_promote_keeps_bytes(tmp_path):
    repo = _repo(tmp_path, node='str06.stage4.compile')
    formal = 'stage4/compiled/execution_plan.json'
    repo.write(formal, {'scenes': []})
    repo.write_invalid(formal, {'scenes': [{'bad': True}]}, problems=('guard_gate',))
    invalid = repo.invalid_rel(formal)
    stored = repo.inspect(invalid)
    assert stored['_provenance']['gates_skipped'] == ['guard_gate']
    assert stored['scenes'] == [{'bad': True}]
    assert repo.path(formal).exists(), 'INVALID 不覆盖正式位'
    # 第二次拦下：先归档旧孪生再写新孪生，不静默覆盖。
    repo.write_invalid(formal, {'scenes': [{'bad': False}]}, problems=('guard_gate',))
    assert repo.archived_rounds(invalid) == {1}
    assert repo.inspect(invalid)['scenes'] == [{'bad': False}]
    archived = repo.inspect(repo.round_rel(invalid, 1))
    assert archived['scenes'] == [{'bad': True}]
    repo.promote_invalid(formal)
    assert repo.inspect(formal)['scenes'] == [{'bad': False}]
    assert not repo.path(invalid).exists()


# ─── 付费边界接正式设施 ──────────────────────────────────────────────────────

def _session(tmp_path, transport, *, yes=True, sid='book'):
    base = tmp_path / 'out'
    base.mkdir(exist_ok=True)
    repo = Str06Repository(base / 'run')
    ledger = CostLedger(path=ledger_path(base))
    repo.attach_context(make_context(base / 'run', ledger=ledger))
    session = PaidRequestSession(repo, ledger, transport, yes=yes,
                                 allow_source_export=yes, provider='fake', sid=sid)
    return base, repo, ledger, session


def test_new_call_takes_work_unit_turn_and_ledger_attribution(tmp_path):
    transport = _Transport()
    base, repo, ledger, session = _session(tmp_path, transport)
    data, _answer = ask_json(session, 'bind', _request(), lambda _: [])
    assert data == {'entries': []}
    assert transport.calls == 1
    unit = WorkUnitBudget(base, 'book', 'str06.stage4.bind', 'executor', 'book')
    turns = unit._read().turns
    assert len(turns) == 1 and turns[0].state == 'delivered'
    assert turns[0].response_sha256, 'delivered 轮必须有响应证据'
    rows, partial = read_cost_snapshot(base)
    assert not partial and len(rows) == 1
    row = rows[0]
    assert row['node'] == 'str06.stage4.bind' and row['sid'] == 'book'
    assert row['cached'] is False and row['purpose'] == 'bind'
    assert row['prompt_tokens'] == 7 and row['completion_tokens'] == 20
    # 产物采纳后单元可继续取轮；采用状态由下一次取轮时从封存证据对账。
    ask_json(session, 'bind', _request(user='第二个请求'), lambda _: [])
    assert unit._read().turns[0].state == 'failed', '未封存轮在下一次 reserve 前关闭'
    assert unit._read().turns[1].state == 'delivered'


def test_cached_replay_costs_nothing_and_keeps_ledger_row(tmp_path):
    transport = _Transport()
    base, repo, ledger, session = _session(tmp_path, transport)
    ask_json(session, 'bind', _request(), lambda _: [])
    session2 = PaidRequestSession(repo, CostLedger(path=ledger_path(base)), transport,
                                  provider='fake', sid='book')
    assert ask_json(session2, 'bind', _request(), lambda _: [])[0] == {'entries': []}
    assert transport.calls == 1, '同请求身份必须免费采用'
    rows, _ = read_cost_snapshot(base)
    assert len(rows) == 2 and rows[1]['cached'] is True
    assert rows[1]['node'] == 'str06.stage4.bind'


def test_unauthorized_new_call_stops_at_owner_without_touching_budget(tmp_path):
    transport = _Transport()
    base, repo, ledger, session = _session(tmp_path, transport, yes=False)
    with pytest.raises(PurchaseRequired, match='--yes'):
        ask_json(session, 'bind', _request(), lambda _: [])
    assert transport.calls == 0
    assert read_book_budget(base, 'book') is None, '未授权不得建账'


def test_unit_content_limit_and_unadopted_turn_close(tmp_path):
    transport = _Transport()
    base, repo, ledger, session = _session(tmp_path, transport)
    answers = [ask_json(session, 'bind', _request(user=f'第{index}次'), lambda _: [])[1]
               for index in range(4)]
    unit = WorkUnitBudget(base, 'book', 'str06.stage4.bind', 'executor', 'book')
    # 前三次新购都没有产物采纳：每次 reserve 前未采纳轮被 mark_rejected 关闭
    # （保留原回答、不返还轮次，对齐正式 continued_content_work 语义）。
    assert [t.state for t in unit._read().turns] == ['failed', 'failed', 'failed', 'delivered']
    with pytest.raises(WorkUnitBudgetExhausted, match='内容预算已耗尽'):
        ask_json(session, 'bind', _request(user='第5次'), lambda _: [])
    # 第 5 次未取到轮：reserve 前未采纳轮已被关闭，四轮终态全 closed；
    # 生成事实不丢——产物的 provenance 完整记录窗口内四次采用的模型与 prompt。
    repo.write('stage4/grounding/semantic_binding.json', {'x': 1}, owners=('bind',),
               refs=(answers[-1].ref,))
    assert [t.state for t in unit._read().turns] == ['failed'] * 4
    provenance = repo.inspect('stage4/grounding/semantic_binding.json')['_provenance']
    assert provenance['adopted_models'] == ['fake']


def test_unknown_turn_blocks_repurchase_until_reconciled(tmp_path):
    class Failing(_Transport):
        def send(self, request):
            self.calls += 1
            raise TimeoutError('模拟未确认请求')

    transport = Failing()
    base, repo, ledger, session = _session(tmp_path, transport)
    with pytest.raises(ExecutionError, match='结果未知'):
        ask_json(session, 'bind', _request(), lambda _: [])
    with pytest.raises(ExecutionError, match='先恢复'):
        ask_json(session, 'bind', _request(), lambda _: [])
    unit = WorkUnitBudget(base, 'book', 'str06.stage4.bind', 'executor', 'book')
    turn = unit._read().turns[0]
    assert turn.state == 'unknown' and turn.reason_code == 'TimeoutError'


def test_book_budget_gate_persists_across_sessions(tmp_path):
    transport = _Transport()
    base, repo, ledger, session = _session(tmp_path, transport)
    ask_json(session, 'bind', _request(), lambda _: [])
    budget = read_book_budget(base, 'book')
    assert budget is not None and budget.used == 1
    ask_json(session, 'bind', _request(user='再来'), lambda _: [])
    assert read_book_budget(base, 'book').used == 2
    # 新 session 读同一份持久账：重启不重置已消耗额度（Q-10）。
    session2 = PaidRequestSession(repo, CostLedger(path=ledger_path(base)), transport,
                                  yes=True, allow_source_export=True, provider='fake',
                                  sid='book')
    ask_json(session2, 'bind', _request(user='第三次'), lambda _: [])
    assert read_book_budget(base, 'book').used == 3


def test_free_node_cannot_open_paid_unit(tmp_path):
    base = tmp_path / 'out'
    base.mkdir(exist_ok=True)
    with pytest.raises(WorkUnitRefused, match='登记'):
        WorkUnitBudget(base, 'book', 'str06.stage4.compile', 'executor', 'book')


# ─── workflow／epoch 身份 ────────────────────────────────────────────────────

def _init_workflow(repo, *, source_identity, code='code-a'):
    from novel.llm.paid_requests import init_or_resume_candidate_workflow
    return init_or_resume_candidate_workflow(
        repository=repo, base=repo.root.parent, code=code, root_sha1='a' * 40,
        source_identity=source_identity, task_id='task-1', configured_model='fake',
        created_at=1759000000)


def test_workflow_record_init_resume_and_code_drift_roll(tmp_path):
    repo = _repo(tmp_path)
    source = 'b' * 64
    workflow, context = _init_workflow(repo, source_identity=source)
    assert workflow.epoch.ordinal == 1 and workflow.epoch.reason == 'initial'
    assert context.ledger.parent_run_id == workflow.epoch.session_run_id
    stored = repo.inspect('runtime/candidate_workflow.json')
    assert stored['contract_identity'] == workflow.contract_identity
    # 同代码复用：epoch 原样不动，累计额度连续。
    again, _ = _init_workflow(repo, source_identity=source)
    assert again.epoch.epoch_id == workflow.epoch.epoch_id
    # 代码漂移：滚动 epoch，ordinal +1，链上前 epoch_id。
    drifted, _ = _init_workflow(repo, source_identity=source, code='code-b')
    assert drifted.epoch.ordinal == 2 and drifted.epoch.reason == 'code_drift'
    assert drifted.epoch.epoch_id != workflow.epoch.epoch_id
    with pytest.raises(ValueError, match='拒绝假漂移'):
        from novel.llm.paid_requests import roll_candidate_epoch
        roll_candidate_epoch(drifted.epoch, reason='code_drift', code_version='code-b',
                             task_id='task-1', session_run_id='r', created_at=1759000001)


def test_workflow_refuses_source_identity_change(tmp_path):
    repo = _repo(tmp_path)
    _init_workflow(repo, source_identity='b' * 64)
    from novel.errors import ProvenanceRefused as Refused
    with pytest.raises(Refused, match='源输入身份'):
        _init_workflow(repo, source_identity='c' * 64)


def test_workflow_refuses_contract_table_drift(tmp_path):
    repo = _repo(tmp_path)
    workflow, _ = _init_workflow(repo, source_identity='b' * 64)
    tampered = repo.inspect('runtime/candidate_workflow.json')
    tampered['contract_identity'] = '0' * 64
    repo.path('runtime/candidate_workflow.json').write_text(
        json.dumps(tampered, ensure_ascii=False), encoding='utf-8')
    with pytest.raises(ProvenanceRefused, match='契约表已漂移'):
        _init_workflow(repo, source_identity='b' * 64)



# ─── 登记的显式历史采用（F7a-3） ──────────────────────────────────────────────

def _source_root(tmp_path, *, content='{"entries": []}', attempt='orig-attempt',
                 usage=None, tamper=None):
    """造一个注册源根：请求行 + ledger 真实付费行。"""
    from novel.llm.paid_requests import digest
    root = tmp_path / 'source' / 'run'
    (root / 'runtime').mkdir(parents=True)
    request = _request()
    identity = digest({'owner': 'bind', 'provider': 'fake',
                       'request': request.cache_payload()})
    row = {'identity': identity, 'status': 'answered', 'attempt_id': attempt,
           'provider': 'fake', 'request': request.cache_payload(),
           'content': content, 'content_sha256': digest(content),
           'model': 'deepseek-flash',
           'usage': usage or {'prompt_tokens': 11, 'completion_tokens': 22}}
    if tamper == 'content':
        row['content'] = content + '被改'
    if tamper == 'identity':
        row['identity'] = 'f' * 64
    (root / 'runtime' / f'request_{identity}.json').write_text(
        json.dumps(row, ensure_ascii=False), encoding='utf-8')
    ledger = CostLedger(path=ledger_path(tmp_path / 'source'))
    ledger.record(node='str06.stage4.bind', sid='book', model='deepseek-flash',
                  request_model='fake', code='str06-rc4', attempt=1, max_attempts=1,
                  will_retry=False, cached=False, cache_policy='request-identity',
                  purpose='bind', attempt_id=attempt, request_fingerprint=identity,
                  outcome='ok', prompt_tokens=11, completion_tokens=22)
    return root, identity, request


def _candidate(tmp_path, adopt_root):
    base = tmp_path / 'cand'
    base.mkdir(exist_ok=True)
    repo = Str06Repository(base / 'run')
    ledger = CostLedger(path=ledger_path(base))
    repo.attach_context(make_context(base / 'run', ledger=ledger))
    session = PaidRequestSession(repo, ledger, _Transport(), yes=False,
                                 allow_source_export=False, provider='fake',
                                 sid='book', adopt_roots=(adopt_root,))
    return base, repo, ledger, session


def test_historical_adoption_zero_new_purchase_and_no_double_count(tmp_path):
    root, identity, request = _source_root(tmp_path)
    base, repo, ledger, session = _candidate(tmp_path, root)
    data, _answer = ask_json(session, 'bind', request, lambda _: [])
    assert data == {'entries': []}
    record = repo.inspect('runtime/revisions/bind.json')
    assert record['mode'] == 'historical-adoption' and record['new_paid_calls'] == 0
    assert record['cost_link'] == 'ledger-row'
    assert record['historical_usage'] == {'prompt_tokens': 11, 'completion_tokens': 22,
                                          'reasoning_tokens': None}
    assert record['model'] == 'deepseek-flash'
    # 不复制响应字节进候选根（不冒充 cache hit）。
    assert not list((base / 'run' / 'runtime').glob('request_*.json'))
    rows, _ = read_cost_snapshot(base)
    assert len(rows) == 1 and rows[0]['cached'] is True
    assert rows[0]['completion_tokens'] == 0, '历史实付不得在候选根重复计费'
    assert rows[0]['cache_policy'] == 'historical-adoption'
    # provenance 的生成事实来自源行真实模型（显式引用）。
    repo.write('stage4/grounding/semantic_binding.json', {'x': 1}, owners=('bind',),
               refs=(AdoptionRef(owner='bind', model='deepseek-flash',
                                 prompts={'system': '系统', 'user': '用户'},
                                 request_sha256=identity,
                                 historical={'cost_link': 'ledger-row'}),))
    provenance = repo.inspect('stage4/grounding/semantic_binding.json')['_provenance']
    assert provenance['adopted_models'] == ['deepseek-flash']


def test_historical_adoption_blocks_corrupted_evidence(tmp_path):
    root, identity, request = _source_root(tmp_path, tamper='content')
    base, repo, ledger, session = _candidate(tmp_path, root)
    with pytest.raises(ExecutionError, match='内容损坏'):
        ask_json(session, 'bind', request, lambda _: [])


def test_historical_adoption_blocks_identity_mismatch(tmp_path):
    root, identity, request = _source_root(tmp_path, tamper='identity')
    base, repo, ledger, session = _candidate(tmp_path, root)
    with pytest.raises(ExecutionError, match='身份损坏'):
        ask_json(session, 'bind', request, lambda _: [])


def test_historical_adoption_blocks_unknown_paid_status(tmp_path):
    root, identity, request = _source_root(tmp_path)
    # 抹掉源根成本账里的付费行：付费状态未知 → 拒绝采用。
    (tmp_path / 'source' / '_service' / 'ledger.jsonl').write_text('', encoding='utf-8')
    base, repo, ledger, session = _candidate(tmp_path, root)
    with pytest.raises(ExecutionError, match='原成本关联'):
        ask_json(session, 'bind', request, lambda _: [])


def test_unregistered_owner_falls_through_to_authorization(tmp_path):
    root, identity, request = _source_root(tmp_path)
    base, repo, ledger, session = _candidate(tmp_path, root)
    with pytest.raises(PurchaseRequired, match='--yes'):
        ask_json(session, 'naming', request, lambda _: [])


# ─── F7c 恢复职责：INVALID 留痕／owner 停止／同指纹熔断／崩溃补尾 ─────────────

def _full_chain(tmp_path, wires, *, ceiling=None):
    from tests.test_str06_body import (
        _RoutingTransport, _fake_ask, _fake_text, _fake_body, _chapters, _make_session,
    )
    repo, session, transport = _make_session(tmp_path, wires, ceiling=ceiling)
    return repo, session, transport, (_fake_ask, _fake_text, _fake_body, _chapters())


def test_binding_invalid_witness_and_owner_stop(tmp_path):
    from tests.test_str06_body import _full_chain_wires
    from novel.stages.chain import run_workflow
    wires = _full_chain_wires()
    # 悬空 beat_ref：bind 机械闸必须拦下并留 typed witness。
    wires['bind'] = {'entries': [{'obligation_id': 'ob01', 'scene_id': 's01',
                                  'beat_ref': 's01#bt99'}], 'unbound': []}
    repo, session, transport, (ask, ask_text, ask_body, chapters) = _full_chain(tmp_path, wires)
    with pytest.raises(ValueError, match='INVALID 孪生已留证'):
        run_workflow(repository=repo, chapters=chapters, ask=ask(session),
                     ask_text=ask_text(session), ask_body=ask_body(session),
                     budget=2000, through='stage7', book_title='报恩',
                     hook_constraints=HookConstraints(target_chars=10, hard_max_chars=360),
                     length_band=(0.01, 0.05))
    invalid = repo.inspect('stage4/grounding/semantic_binding.INVALID.json')
    assert invalid['_provenance']['gates_skipped'], 'INVALID 必须带 typed witness'
    assert invalid['wire']['entries'][0]['beat_ref'] == 's01#bt1'.replace('bt1', 'bt99')
    status = repo.inspect('runtime/workflow_status.json')
    assert status['status'] == 'failed'
    assert status['failed_node'] == 'str06.stage4.bind', status
    # owner 停止：bind 之后没有任何后续 owner 的调用（失败即停，不继续问模型）。
    asked = set(transport.calls)
    assert 'hook_design' not in asked and not any(
        o.startswith('body_') for o in asked), asked


def test_same_fingerprint_failure_never_rebuys(tmp_path):
    transport = _Transport(content='{"entries": "坏形状"}')
    base = tmp_path / 'out'
    base.mkdir(exist_ok=True)
    repo = Str06Repository(base / 'run')
    ledger = CostLedger(path=ledger_path(base))
    repo.attach_context(make_context(base / 'run', ledger=ledger))
    session = PaidRequestSession(repo, ledger, transport, yes=True,
                                 allow_source_export=True, provider='fake', sid='book')
    request = _request()
    for _ in range(3):
        with pytest.raises(ValueError, match='同一问题集重复即停'):
            ask_json(session, 'bind', request, lambda _: ['形状不合约定'])
    # R3（2026-09-28 裁决 1）：非空但不过闸先经**一次**有界修订轮（原轮＋修订轮
    # ＝2 次外发），修订后问题集原样重现即停；此后每次重问都按请求身份免费
    # 重放——不再重复计费。修订轮本身如实入账（I-43）。
    assert transport.calls == 2, '原轮＋一次修订轮后同指纹停，不无限付费'
    rows, _ = read_cost_snapshot(base)
    # 缓存命中按 Q-11 每次记一行：2 行实付（原轮＋修订轮）＋4 行零 token 缓存
    # （两次重问各重放两轮），不重复计费。
    assert len([r for r in rows if not r['cached']]) == 2
    assert all(r['prompt_tokens'] == 0 and r['completion_tokens'] == 0
               for r in rows if r['cached'])


def test_crash_tail_replays_exact_without_new_purchase(tmp_path):
    from tests.test_str06_body import _full_chain_wires
    from novel.stages.chain import run_workflow
    wires = _full_chain_wires()
    repo, session, transport, (ask, ask_text, ask_body, chapters) = _full_chain(tmp_path, wires)
    kwargs = dict(budget=2000, through='stage7', book_title='报恩',
                  hook_constraints=HookConstraints(target_chars=10, hard_max_chars=360),
                  length_band=(0.01, 0.05))
    run_workflow(repository=repo, chapters=chapters, ask=ask(session),
                 ask_text=ask_text(session), ask_body=ask_body(session), **kwargs)
    final_before = repo.path('stage7/final/报恩_rewriting.txt').read_bytes()
    calls_before = transport.calls
    # 崩溃窗口：编译产物丢失 → 重跑必须精确补尾，零新购、交付不变。
    repo.path('stage4/compiled/execution_plan.json').unlink()
    run_workflow(repository=repo, chapters=chapters, ask=ask(session),
                 ask_text=ask_text(session), ask_body=ask_body(session), **kwargs)
    assert transport.calls == calls_before, '崩溃补尾不得新购'
    assert repo.path('stage7/final/报恩_rewriting.txt').read_bytes() == final_before
    assert repo.inspect('runtime/workflow_status.json')['status'] == 'delivered'
