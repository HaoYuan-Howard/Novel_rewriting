"""legacy 完整回答与成本 append 之间的零模型恢复纵切。"""
from __future__ import annotations

import json
from pathlib import Path

import pytest
from pydantic import BaseModel

from novel.errors import ProvenanceRefused
from novel.llm.cache import CachePolicy, ResponseCache
from novel.llm.client import LlmClient
from novel.llm.ledger import CostLedger, ledger_path
from novel.llm.transport import ChatResponse, FakeTransport
from novel.run import RunContext
from novel.work_units import budget_for, require_workflow


class _Answer(BaseModel):
    value: int


def _ctx(base: Path) -> RunContext:
    require_workflow(base)
    book = base / '成本回执_855'
    book.mkdir(exist_ok=True)
    return RunContext(
        novel_dir=book, node='str06.stage5.hook', paid=True, code='receipt-test',
        cache=CachePolicy.default(), ledger=CostLedger(path=ledger_path(base)),
        observer=lambda _event: None)


def _client(ctx: RunContext, transport: FakeTransport) -> LlmClient:
    return LlmClient(
        ctx, transport, cache=ResponseCache(ctx.novel_dir.parent / 'cache'),
        model='test-model', max_retries=0,
        budget_checker=lambda *_a, **_kw: None)


def _ask(client: LlmClient) -> _Answer:
    return client.ask_json('封闭的测试系统', '同一完整工作包',
                           model_cls=_Answer, purpose='str06.stage5.hook')


def _response() -> ChatResponse:
    return ChatResponse(
        content='{"value":7}', finish_reason='stop', model='test-model',
        usage={'prompt_tokens': 13, 'completion_tokens': 5, 'reasoning_tokens': 2})


@pytest.mark.parametrize('after_cost', [False, True], ids=['成本首写前', '成本首写后'])
def test_prepared_response_reconciles_original_cost_once_across_restarts(
        tmp_path, monkeypatch, after_cost):
    original = _ctx(tmp_path)
    driver = FakeTransport.always(_response())
    record = original.ledger.record

    def crash(**kwargs):
        if after_cost:
            record(**kwargs)
        raise OSError('模型已回答，成本提交窗硬杀')

    monkeypatch.setattr(original.ledger, 'record', crash)
    with pytest.raises(OSError, match='成本提交窗'):
        _ask(_client(original, driver))
    assert len(driver.calls) == 1
    budget = budget_for(original, scope='book')
    assert (budget.used, budget.remaining) == (1, 3)

    for _repeat in range(2):
        resumed = _ctx(tmp_path)
        blocked_wire = FakeTransport.always(_response())
        value = _ask(_client(resumed, blocked_wire))
        assert value.value == 7 and blocked_wire.calls == []
        assert resumed.ledger.calls_made == 1
        rows = [json.loads(line) for line in ledger_path(tmp_path).read_text(
            encoding='utf-8').splitlines() if line.strip()]
        assert len(rows) == 1
        assert rows[0]['run_id'] == original.run_id
        assert rows[0]['prompt_tokens'] == 13 and rows[0]['completion_tokens'] == 5
    assert (budget.used, budget.remaining) == (1, 3)


def test_prepared_response_refuses_changed_original_cost_without_new_wire(
        tmp_path, monkeypatch):
    original = _ctx(tmp_path)
    record = original.ledger.record

    def crash(**kwargs):
        record(**kwargs)
        raise OSError('原成本已经写完')

    monkeypatch.setattr(original.ledger, 'record', crash)
    with pytest.raises(OSError, match='已经写完'):
        _ask(_client(original, FakeTransport.always(_response())))
    path = ledger_path(tmp_path)
    row = json.loads(path.read_text(encoding='utf-8'))
    row['completion_tokens'] = 999
    path.write_text(json.dumps(row, ensure_ascii=False) + '\n', encoding='utf-8')
    tampered = path.read_bytes()

    resumed = _ctx(tmp_path)
    blocked_wire = FakeTransport.always(_response())
    with pytest.raises(ProvenanceRefused, match='原尝试成本行与冻结回执不一致'):
        _ask(_client(resumed, blocked_wire))
    assert blocked_wire.calls == [] and path.read_bytes() == tampered
    assert budget_for(resumed, scope='book').used == 1


def test_same_purpose_parallel_requests_keep_wire_history_and_budgets(tmp_path, monkeypatch):
    """仅核 wire 组装隔离；现役 producer 采纳仍使用含章号的唯一 purpose。"""
    from concurrent.futures import ThreadPoolExecutor
    from threading import Barrier
    from novel.observability import RequestProgress
    from novel.run import RunContext
    seed = _ctx(tmp_path)
    ctx = RunContext(novel_dir=seed.novel_dir, node='str06.stage5.body', paid=True,
                     code=seed.code, cache=CachePolicy.default(),
                     ledger=CostLedger(path=ledger_path(tmp_path)), observer=lambda _event: None)
    wire = FakeTransport.always(_response())
    client = _client(ctx, wire)
    purpose = 'str06.stage5.body'
    for chapter in (1, 2):
        client.ask_json('SYS', f'首稿章{chapter}', model_cls=_Answer, purpose=purpose,
                        progress=RequestProgress(chapter=chapter, index=chapter, total=2))
        client.record_candidate_feedback(purpose, {'reason': f'第{chapter}章独有错误'})
    barrier = Barrier(2)
    preflight = client._preflight

    def synchronize(*args, **kwargs):
        barrier.wait(timeout=5)
        return preflight(*args, **kwargs)

    monkeypatch.setattr(client, '_preflight', synchronize)

    def revise(chapter):
        return client.ask_json('SYS', f'修订章{chapter}', model_cls=_Answer, purpose=purpose,
                               progress=RequestProgress(chapter=chapter, index=chapter, total=2)).value

    with ThreadPoolExecutor(max_workers=2) as executor:
        assert list(executor.map(revise, (1, 2))) == [7, 7]
    # 并发段不调用 purpose-only 的 accepted 接口，不声称该接口有采纳隔离。
    assert len(wire.calls) == 4
    for request in wire.calls[2:]:
        chapter = int(request.user[-1])
        history = json.dumps(request.history, ensure_ascii=False)
        assert f'首稿章{chapter}' in history
        assert f'首稿章{3 - chapter}' not in history
        assert budget_for(ctx, scope=f'chapter:{chapter}').latest_feedback() == {
            'reason': f'第{chapter}章独有错误'}
    assert [budget_for(ctx, scope=f'chapter:{chapter}').used for chapter in (1, 2)] == [2, 2]
