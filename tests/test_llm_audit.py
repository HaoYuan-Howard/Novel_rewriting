#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
test_llm_audit.py —— 采纳审计三事件：wire 行的联结键 · `gate_decision` · `artifact_adoption`。

⚠⚠ 它守的是一个实测踩过的观测洞：成本账那一行是**传输层**结论
（内容闸拒了它照样 `outcome=ok`），「最终采纳档位」原来只能靠
「同 purpose 最后一条 ok 行」猜——并发、缓存重放、中断恢复下都不可靠。
⇒ 判定与采纳各自显式落账（`llm_audit.jsonl`），靠 `attempt_id` 反查。

⚠ 本机 `%TEMP%\\pytest-of-yh255` 的 ACL 坏了，`tmp_path` 一用就炸——一律 `tempfile.mkdtemp()`。
"""
from __future__ import annotations

import hashlib
import json
import tempfile
from pathlib import Path

from pydantic import BaseModel, Field

from novel.llm.cache import CachePolicy, ResponseCache, cache_key
from novel.llm.client import LlmClient
from novel.llm.ledger import AUDIT_REL, CostLedger, audit_path
from novel.llm.transport import ChatRequest, ChatResponse, FakeTransport
from novel.run import RunContext


class Resp(BaseModel):
    voice: dict = Field(...)
    surface_habits: list[str] = Field(...)


OK_JSON = ChatResponse(
    content=json.dumps({'voice': {'a': 1}, 'surface_habits': ['h']},
                       ensure_ascii=False),
    finish_reason='stop', usage={'prompt_tokens': 10, 'completion_tokens': 5},
    model='deepseek-v4-pro')


def _book() -> Path:
    base = Path(tempfile.mkdtemp())
    (base / '书名_123' / 'stage1').mkdir(parents=True)
    return base


def _ledgered_ctx() -> tuple[RunContext, Path]:
    d = Path(tempfile.mkdtemp())
    ctx = RunContext.open(_book(), '123', 'stage3.style', paid=True,
                          ledger=CostLedger(path=d / 'ledger.jsonl'))
    return ctx, d


def _client(transport, ctx, **kw) -> LlmClient:
    return LlmClient(ctx, transport, sleep=lambda _s: None, **kw)


def _rows(path: Path) -> list[dict]:
    if not path.is_file():
        return []
    return [json.loads(line)
            for line in path.read_text('utf-8').splitlines() if line.strip()]


def test_wire_rows_carry_the_attempt_identity_triplet():
    """成本账 wire 行要能被审计行**指回来**：attempt_id + 请求指纹 + 响应内容身份。"""
    ctx, d = _ledgered_ctx()
    client = _client(FakeTransport.always(OK_JSON), ctx)
    client.ask_json('sys', 'usr', model_cls=Resp, purpose='p')

    rows = _rows(d / 'ledger.jsonl')
    assert len(rows) == 1
    row = rows[0]
    assert isinstance(row['attempt_id'], str) and len(row['attempt_id']) == 16
    expected_req = ChatRequest(
        model=client.model, system='sys', user='usr',
        reasoning_effort='high', thinking=True, json_mode=True,
        max_tokens=row['max_tokens'])
    assert row['request_fingerprint'] == cache_key(expected_req)
    assert row['response_sha256'] == hashlib.sha256(
        OK_JSON.content.encode('utf-8')).hexdigest()


def test_gate_decisions_and_adoption_answer_which_rung_was_finally_used():
    """⭐ 反查全链：首档被 parse 闸拒 → 次档采纳 → adoption 行 → attempt_id → 档位。

    这正是「最终采纳档位」的读法：**不看**「最后一条 ok 行」（首档那行也是 ok），
    看 `artifact_adoption → attempt_id → 成本账行` 这条联结。
    """
    ctx, d = _ledgered_ctx()
    bad = ChatResponse(content='这不是 JSON', finish_reason='stop')
    t = FakeTransport(responder=lambda _r, i: bad if i == 0 else OK_JSON)
    client = _client(t, ctx)
    client.ask_json('sys', 'usr', model_cls=Resp, purpose='p')

    audit = _rows(d / AUDIT_REL[-1])
    assert [row['kind'] for row in audit] == ['gate_decision', 'gate_decision']
    rejected, accepted = audit
    assert (rejected['decision'], rejected['gate']) == ('rejected', 'parse')
    assert (rejected['rung_index'], rejected['sample'],
            rejected['resample']) == (0, 1, False)
    assert rejected['source'] == 'wire'
    assert (accepted['decision'], accepted['gate']) == ('accepted', None)
    assert accepted['rung_index'] == 1

    adopted = client.accepted_attempt('p')
    assert adopted == accepted['attempt_id']

    ctx.ledger.note_adoption(
        sid=ctx.sid, node=ctx.node, purpose='p', attempt_id=adopted,
        artifact='stage2/chapter_blueprints.json', artifact_fingerprint='fp10',
        chapter=3)
    adoption = _rows(d / AUDIT_REL[-1])[-1]
    assert adoption['kind'] == 'artifact_adoption'
    assert adoption['chapter'] == 3

    wire = {row['attempt_id']: row for row in _rows(d / 'ledger.jsonl')}
    # 两行都是传输层 ok —— 单看成本账分不出谁被采纳，这正是审计账存在的理由。
    assert all(row['outcome'] == 'ok' for row in wire.values())
    assert wire[adoption['attempt_id']]['reasoning_effort'] == 'low'
    assert wire[rejected['attempt_id']]['reasoning_effort'] == 'high'


def test_cache_hits_settle_gates_and_are_adoptable_too():
    """缓存来的响应同样过闸、同样可被采纳——联结键不因来源缺席。"""
    cache_dir = Path(tempfile.mkdtemp())
    ctx1, _d1 = _ledgered_ctx()
    _client(FakeTransport.always(OK_JSON), ctx1,
            cache=ResponseCache(cache_dir)).ask_json(
        'sys', 'usr', model_cls=Resp, purpose='p')

    ctx2, d2 = _ledgered_ctx()
    t = FakeTransport.always(OK_JSON)
    client = _client(t, ctx2, cache=ResponseCache(cache_dir))
    client.ask_json('sys', 'usr', model_cls=Resp, purpose='p')
    assert not t.calls, '有合格缓存时必须 0 次真请求'

    audit = _rows(d2 / AUDIT_REL[-1])
    assert [(row['decision'], row['source']) for row in audit] == [
        ('accepted', 'cache')]
    ledger_rows = _rows(d2 / 'ledger.jsonl')
    assert len(ledger_rows) == 1 and ledger_rows[0]['cached'] is True
    assert ledger_rows[0]['attempt_id'] == audit[0]['attempt_id']
    assert client.accepted_attempt('p') == audit[0]['attempt_id']


def test_audit_events_survive_in_memory_when_there_is_no_ledger_file():
    """`--dry-run` / 测试（`write_ledger=False`）没有盘上账本，判定仍要能被断言。"""
    ctx = RunContext.open(_book(), '123', 'stage3.style', paid=True,
                          write_ledger=False)
    client = LlmClient(ctx, FakeTransport.always(OK_JSON), sleep=lambda _s: None)
    client.ask_json('sys', 'usr', model_cls=Resp, purpose='p',
                    cache_policy=CachePolicy.default())
    kinds = [row['kind'] for row in ctx.ledger.audit_events]
    assert kinds == ['gate_decision']
    assert ctx.ledger.audit_events[0]['decision'] == 'accepted'


def test_audit_path_is_next_to_the_ledger():
    assert audit_path('X').as_posix() == 'X/_service/llm_audit.jsonl'
