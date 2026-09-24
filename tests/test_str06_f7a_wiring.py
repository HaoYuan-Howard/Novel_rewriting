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
from contracts.schema.str06 import open_candidate_epoch
from contracts.schema.str06.hook import HookConstraints
from novel.errors import ProvenanceRefused, UpstreamStale
from novel.llm.ledger import CostLedger, ledger_path, read_cost_snapshot
from novel.llm.str06_requests import PaidRequestSession, PurchaseRequired
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
    repo.context.note_adoption(owner='naming', model='deepseek-chat',
                               prompts={'system': '系统', 'user': '用户'})
    repo.write('stage7/naming/name_map.json', {'entries': []})
    provenance = repo.inspect('stage7/naming/name_map.json')['_provenance']
    assert provenance['model'] == 'fake' and provenance['adopted_models'] == ['deepseek-chat']
    assert any(k.startswith('naming.') for k in provenance['prompts'])
    LlmProvenance.model_validate(provenance)


def test_paid_write_without_adoption_evidence_is_refused(tmp_path):
    repo = _repo(tmp_path, node='str06.stage7.naming')
    with pytest.raises(ProvenanceRefused, match='没有已采用响应'):
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
    from contracts.schema.str06.naming import NameAssignment, NameMapV5, TargetName
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
    data = session.ask('bind', _request(), lambda _: [])
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
    # 产物采纳后轮次转 adopted，单元可继续取轮。
    repo.write('stage4/grounding/semantic_binding.json', {'x': 1}, owners=('bind',))
    assert unit._read().turns[0].state == 'adopted'


def test_cached_replay_costs_nothing_and_keeps_ledger_row(tmp_path):
    transport = _Transport()
    base, repo, ledger, session = _session(tmp_path, transport)
    session.ask('bind', _request(), lambda _: [])
    session2 = PaidRequestSession(repo, CostLedger(path=ledger_path(base)), transport,
                                  provider='fake', sid='book')
    assert session2.ask('bind', _request(), lambda _: []) == {'entries': []}
    assert transport.calls == 1, '同请求身份必须免费采用'
    rows, _ = read_cost_snapshot(base)
    assert len(rows) == 2 and rows[1]['cached'] is True
    assert rows[1]['node'] == 'str06.stage4.bind'


def test_unauthorized_new_call_stops_at_owner_without_touching_budget(tmp_path):
    transport = _Transport()
    base, repo, ledger, session = _session(tmp_path, transport, yes=False)
    with pytest.raises(PurchaseRequired, match='--yes'):
        session.ask('bind', _request(), lambda _: [])
    assert transport.calls == 0
    assert read_book_budget(base, 'book') is None, '未授权不得建账'


def test_unit_content_limit_and_unadopted_turn_close(tmp_path):
    transport = _Transport()
    base, repo, ledger, session = _session(tmp_path, transport)
    for index in range(4):
        session.ask('bind', _request(user=f'第{index}次'), lambda _: [])
    unit = WorkUnitBudget(base, 'book', 'str06.stage4.bind', 'executor', 'book')
    # 前三次新购都没有产物采纳：每次 reserve 前未采纳轮被 mark_rejected 关闭
    # （保留原回答、不返还轮次，对齐正式 continued_content_work 语义）。
    assert [t.state for t in unit._read().turns] == ['failed', 'failed', 'failed', 'delivered']
    with pytest.raises(WorkUnitBudgetExhausted, match='内容预算已耗尽'):
        session.ask('bind', _request(user='第5次'), lambda _: [])
    # 第 5 次未取到轮：reserve 前未采纳轮已被关闭，四轮终态全 closed；
    # 生成事实不丢——产物的 provenance 完整记录窗口内四次采用的模型与 prompt。
    repo.write('stage4/grounding/semantic_binding.json', {'x': 1}, owners=('bind',))
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
    with pytest.raises(TimeoutError):
        session.ask('bind', _request(), lambda _: [])
    with pytest.raises(ValueError, match='先恢复'):
        session.ask('bind', _request(), lambda _: [])
    unit = WorkUnitBudget(base, 'book', 'str06.stage4.bind', 'executor', 'book')
    turn = unit._read().turns[0]
    assert turn.state == 'unknown' and turn.reason_code == 'TimeoutError'


def test_book_budget_gate_persists_across_sessions(tmp_path):
    transport = _Transport()
    base, repo, ledger, session = _session(tmp_path, transport)
    session.ask('bind', _request(), lambda _: [])
    budget = read_book_budget(base, 'book')
    assert budget is not None and budget.used == 1
    session.ask('bind', _request(user='再来'), lambda _: [])
    assert read_book_budget(base, 'book').used == 2
    # 新 session 读同一份持久账：重启不重置已消耗额度（Q-10）。
    session2 = PaidRequestSession(repo, CostLedger(path=ledger_path(base)), transport,
                                  yes=True, allow_source_export=True, provider='fake',
                                  sid='book')
    session2.ask('bind', _request(user='第三次'), lambda _: [])
    assert read_book_budget(base, 'book').used == 3


def test_free_node_cannot_open_paid_unit(tmp_path):
    base = tmp_path / 'out'
    base.mkdir(exist_ok=True)
    with pytest.raises(WorkUnitRefused, match='登记'):
        WorkUnitBudget(base, 'book', 'str06.stage4.compile', 'executor', 'book')


# ─── workflow／epoch 身份 ────────────────────────────────────────────────────

def _init_workflow(repo, *, source_identity, code='code-a'):
    from novel.llm.str06_run_context import init_or_resume_candidate_workflow
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
        from contracts.schema.str06 import roll_candidate_epoch
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

