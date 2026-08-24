#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""结构化拒绝响应的受托修复状态机：每份先修一次，失败才消费下一采。"""
from __future__ import annotations

import json
import tempfile
from pathlib import Path

import pytest
from pydantic import BaseModel, Field

from novel.errors import ResponseRepairRequired, TerminalState
from novel.llm import response_repair
from novel.llm.client import (
    MAX_SAMPLES_PER_RUNG, PRO_FALLBACK_MODEL, LlmClient, LlmFailed,
)
from novel.llm.ledger import AUDIT_REL, ledger_path
from novel.llm.transport import ChatResponse, FakeTransport
from novel.run import RunContext


class RepairResponse(BaseModel):
    voice: dict = Field(...)
    surface_habits: list[str] = Field(...)


GOOD = {'voice': {'person': 'third'}, 'surface_habits': ['短句']}
BAD = {'voice': {'person': 'third'}}


def _base() -> Path:
    base = Path(tempfile.mkdtemp())
    (base / '书名_123' / 'stage1').mkdir(parents=True)
    return base


def _ctx(base: Path) -> RunContext:
    return RunContext.open(
        base, '123', 'stage2.global', paid=True,
        repair_rejected_responses=True)


def _client(ctx: RunContext, transport: FakeTransport) -> LlmClient:
    return LlmClient(ctx, transport, sleep=lambda _seconds: None)


def _bad(model: str = 'deepseek-v4-flash') -> ChatResponse:
    return ChatResponse(
        content=json.dumps(BAD, ensure_ascii=False), finish_reason='stop',
        usage={'prompt_tokens': 11, 'completion_tokens': 7}, model=model)


def _good(model: str = 'deepseek-v4-flash') -> ChatResponse:
    return ChatResponse(
        content=json.dumps(GOOD, ensure_ascii=False), finish_reason='stop',
        usage={'prompt_tokens': 11, 'completion_tokens': 9}, model=model)


def _ask(client: LlmClient) -> RepairResponse:
    return client.ask_json('system', 'user', model_cls=RepairResponse, purpose='repair.p')


def _rows(path: Path) -> list[dict]:
    if not path.is_file():
        return []
    return [json.loads(line) for line in path.read_text('utf-8').splitlines()
            if line.strip()]


def test_an_agent_candidate_is_revalidated_and_adopted_without_a_second_wire_call():
    base = _base()
    first_transport = FakeTransport.always(_bad())
    with pytest.raises(ResponseRepairRequired) as stopped:
        _ask(_client(_ctx(base), first_transport))
    assert len(first_transport.calls) == 1
    exc = stopped.value
    root = Path(exc.candidate_path).parent
    rejection = json.loads((root / response_repair.REJECTION_FILE).read_text('utf-8'))
    assert (rejection['gate'], rejection['repair_eligible'],
            rejection['sample']) == ('require', True, 1)
    assert json.loads((root / response_repair.ORIGINAL_FILE).read_text('utf-8')) == BAD

    Path(exc.candidate_path).write_text(
        json.dumps(GOOD, ensure_ascii=False), encoding='utf-8')
    response_repair.submit(
        _ctx(base).novel_dir, exc.attempt_id,
        actor='agent:novel-pipeline-operator', reason='只补齐响应已明确要求的外层字段')

    second_transport = FakeTransport.always(_bad())
    second_ctx = _ctx(base)
    client = _client(second_ctx, second_transport)
    got = _ask(client)
    assert got.surface_habits == ['短句']
    assert second_transport.calls == [], '修复候选过原闸后不能再发 Flash/Pro'
    assert client.accepted_model('repair.p') == 'deepseek-v4-flash'
    usage = second_ctx.ledger.usage()
    assert usage is not None and (usage.calls_made, usage.completion_tokens) == (1, 7)

    audit = _rows(base.joinpath(*AUDIT_REL))
    repaired = [row for row in audit if row['kind'] == 'response_repair']
    assert [(row['action'], row['decision']) for row in repaired] == [
        ('offered', 'pending'), ('agent', 'accepted')]
    # 原响应仍然是 rejected；不能因候选通过就篡改供应商响应的历史判定。
    gate = [row for row in audit if row['kind'] == 'gate_decision']
    assert [(row['decision'], row['gate']) for row in gate] == [('rejected', 'require')]


def test_a_trailing_comma_is_repaired_mechanically_before_agent_or_resampling():
    base = _base()
    malformed = ChatResponse(
        content='{"voice":{"person":"third"},"surface_habits":["短句"],}',
        finish_reason='stop', usage={'completion_tokens': 8},
        model='deepseek-v4-flash')
    transport = FakeTransport.always(malformed)
    got = _ask(_client(_ctx(base), transport))
    assert got.surface_habits == ['短句']
    assert len(transport.calls) == 1
    repairs = [row for row in _rows(base.joinpath(*AUDIT_REL))
               if row['kind'] == 'response_repair']
    assert [(row['action'], row['decision']) for row in repairs] == [
        ('offered', 'pending'), ('deterministic', 'accepted')]
    resolution, = list((_ctx(base).novel_dir / '_llm_rejected').glob(
        '*/*/resolution.json'))
    assert json.loads(resolution.read_text('utf-8'))['action'] == 'deterministic'


def test_a_repaired_candidate_that_still_fails_uses_flash_sample_two_before_pro():
    base = _base()
    with pytest.raises(ResponseRepairRequired) as stopped:
        _ask(_client(_ctx(base), FakeTransport.always(_bad())))
    exc = stopped.value
    Path(exc.candidate_path).write_text('{}\n', encoding='utf-8')
    response_repair.submit(
        _ctx(base).novel_dir, exc.attempt_id,
        actor='agent:novel-pipeline-operator', reason='测试：提交后仍缺字段')

    transport = FakeTransport.always(_good())
    ctx = _ctx(base)
    got = _ask(_client(ctx, transport))
    assert got.voice == GOOD['voice']
    assert [request.model for request in transport.calls] == ['deepseek-v4-flash']

    audit = _rows(base.joinpath(*AUDIT_REL))
    repairs = [row for row in audit if row['kind'] == 'response_repair']
    assert repairs[-1]['decision'] == 'rejected'
    accepted, = [row for row in audit
                 if row['kind'] == 'gate_decision' and row['decision'] == 'accepted']
    assert accepted['sample'] == 2
    usage = ctx.ledger.usage()
    assert usage is not None and usage.calls_made == 2


def test_every_flash_and_pro_sample_gets_one_agent_chance_before_llmfailed():
    """完整顺序：Flash#1→修→Flash#2→修→Pro#1→修→Pro#2→修→LlmFailed。"""
    base = _base()
    emitted_models: list[str] = []

    for expected_model in (
            ['deepseek-v4-flash'] * MAX_SAMPLES_PER_RUNG
            + [PRO_FALLBACK_MODEL] * MAX_SAMPLES_PER_RUNG):
        transport = FakeTransport(responder=lambda req, _index: _bad(req.model))
        with pytest.raises(ResponseRepairRequired) as stopped:
            _ask(_client(_ctx(base), transport))
        emitted_models.extend(request.model for request in transport.calls)
        assert emitted_models[-1] == expected_model
        response_repair.submit(
            _ctx(base).novel_dir, stopped.value.attempt_id,
            actor='agent:novel-pipeline-operator', reason='没有足够事实可安全补齐',
            decline=True)

    final_transport = FakeTransport.always(_good())
    with pytest.raises(LlmFailed, match='主模型与兜底模型'):
        _ask(_client(_ctx(base), final_transport))
    assert final_transport.calls == [], '四个样本均已消费后不能偷偷发第五采'
    assert emitted_models == (
        ['deepseek-v4-flash'] * MAX_SAMPLES_PER_RUNG
        + [PRO_FALLBACK_MODEL] * MAX_SAMPLES_PER_RUNG)

    ledger = _rows(ledger_path(base))
    assert [row['model'] for row in ledger] == emitted_models
    repairs = [row for row in _rows(base.joinpath(*AUDIT_REL))
               if row['kind'] == 'response_repair']
    assert [row['decision'] for row in repairs].count('pending') == 4
    assert [row['decision'] for row in repairs].count('declined') == 4


def test_truncation_is_kept_but_never_pauses_for_agent_guesswork():
    base = _base()
    cut = ChatResponse(content='{"voice": {', finish_reason='length',
                       model='deepseek-v4-flash')
    transport = FakeTransport(responder=lambda req, index: cut if index == 0 else _good(req.model))
    got = _ask(_client(_ctx(base), transport))
    assert got.surface_habits == ['短句']
    assert [request.model for request in transport.calls] == [
        'deepseek-v4-flash', PRO_FALLBACK_MODEL]
    roots = list((_ctx(base).novel_dir / '_llm_rejected').glob('*/*/rejection.json'))
    rejection, = [json.loads(path.read_text('utf-8')) for path in roots]
    assert rejection['repair_eligible'] is False
    assert '截断' in rejection['eligibility_reason']


def test_one_response_cannot_receive_a_second_agent_resolution():
    base = _base()
    with pytest.raises(ResponseRepairRequired) as stopped:
        _ask(_client(_ctx(base), FakeTransport.always(_bad())))
    exc = stopped.value
    Path(exc.candidate_path).write_text(
        json.dumps(GOOD, ensure_ascii=False), encoding='utf-8')
    response_repair.submit(
        _ctx(base).novel_dir, exc.attempt_id,
        actor='agent:a', reason='first')
    with pytest.raises(TerminalState, match='最多修复一次'):
        response_repair.submit(
            _ctx(base).novel_dir, exc.attempt_id,
            actor='agent:b', reason='second')
