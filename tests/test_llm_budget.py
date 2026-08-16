#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""DeepSeek V4 上下文预算：精确计数、边界语义与零副作用。"""
from __future__ import annotations

from functools import partial
import hashlib
import json
from importlib import resources
from pathlib import Path
import tempfile

import pytest
from pydantic import BaseModel

from novel.errors import ContextBudgetRefused, TerminalState
from novel.llm.budget import (
    MODEL_BUDGETS,
    TOKENIZER_REVISION,
    TOKENIZER_SHA256,
    BudgetEstimate,
    count_input_tokens,
    enforce_request_budget,
    validate_budget,
    _verified_tokenizer_bytes,
)
from novel.llm.cache import CachePolicy, ResponseCache, cache_key
from novel.llm.client import LlmClient
from novel.llm.ledger import CostLedger
from novel.llm.transport import ChatRequest, ChatResponse, FakeTransport
from novel.run import RunContext


class _JsonResponse(BaseModel):
    value: str


def _ctx() -> RunContext:
    base = Path(tempfile.mkdtemp(prefix='budget-test-'))
    book = base / '预算测试_1'
    book.mkdir()
    return RunContext(
        novel_dir=book,
        node='stage2.global',
        paid=True,
        code='budget-test',
        cache=CachePolicy.default(),
        ledger=CostLedger(path=None),
    )


def _req(*, system: str = 'sys', user: str = 'usr', thinking: bool = True,
         json_mode: bool = False, max_tokens: int = 96_000,
         model: str = 'deepseek-v4-pro') -> ChatRequest:
    return ChatRequest(
        model=model,
        system=system,
        user=user,
        reasoning_effort='high',
        thinking=thinking,
        json_mode=json_mode,
        max_tokens=max_tokens,
    )


def _estimate(input_tokens: int, max_tokens: int = 96_000) -> BudgetEstimate:
    spec = MODEL_BUDGETS['deepseek-v4-pro']
    return BudgetEstimate(
        model=spec.model,
        input_tokens=input_tokens,
        max_tokens=max_tokens,
        context_limit=spec.context_limit,
        max_output_tokens=spec.max_output_tokens,
        tokenizer_revision=spec.tokenizer_revision,
    )


def test_registry_pins_both_deepseek_v4_models_to_the_product_limits():
    assert set(MODEL_BUDGETS) == {'deepseek-v4-pro', 'deepseek-v4-flash'}
    assert {(s.context_limit, s.max_output_tokens) for s in MODEL_BUDGETS.values()} == {
        (1_000_000, 384_000),
    }
    assert len(TOKENIZER_REVISION) == 40


def test_official_tokenizer_and_two_message_framing_have_locked_counts():
    # 这些数由固定 tokenizer + BOS/System/User/Assistant/thinking framing 共同决定；
    # JSON mode 是顶层 API 控制参数，不凭空增加一段本地文字。
    assert count_input_tokens(_req()) == 6
    assert count_input_tokens(_req(
        system='你是编辑', user='请重写这一章', thinking=False, json_mode=True)) == 11
    assert count_input_tokens(_req(json_mode=True)) == count_input_tokens(_req(json_mode=False))


@pytest.mark.parametrize('input_tokens', [903_999, 904_000])
def test_inside_and_exact_context_boundary_pass(input_tokens):
    got = validate_budget(_estimate(input_tokens), node='stage2.global', purpose='global')
    assert got.total_tokens <= 1_000_000


def test_one_token_over_context_boundary_is_an_explanatory_terminal_state():
    with pytest.raises(ContextBudgetRefused) as caught:
        validate_budget(_estimate(904_001), node='stage2.global', purpose='global')
    err = caught.value
    assert issubclass(type(err), TerminalState)
    assert (err.input_tokens, err.max_tokens, err.context_limit) == (904_001, 96_000, 1_000_000)
    message = str(err)
    for fact in ('input_tokens=904001', 'declared_max_tokens=96000', 'total=1000001',
                 'context_limit=1000000', 'input_tokens <= 904000'):
        assert fact in message
    assert '不截断' in message and '不分块聚合' in message and '不自动换模型' in message


def test_declared_output_above_provider_limit_is_rejected():
    with pytest.raises(ContextBudgetRefused, match='单次输出上限') as caught:
        validate_budget(_estimate(1, 384_001), node='stage4.plan', purpose='stage4.plan')
    assert caught.value.max_output_tokens == 384_000


def test_unknown_model_fails_closed_before_any_tokenizer_guess():
    with pytest.raises(ContextBudgetRefused, match='没有已验证') as caught:
        enforce_request_budget((_req(model='future-model'),), node='stage2.global', purpose='global')
    assert caught.value.input_tokens is None
    assert '自动换模型' in str(caught.value)


def test_tokenizer_asset_is_packaged_and_matches_the_pinned_hash():
    data = resources.files('novel.llm').joinpath(
        'resources/deepseek_v4_tokenizer.json').read_bytes()
    assert hashlib.sha256(data).hexdigest() == TOKENIZER_SHA256
    assert resources.files('novel.llm').joinpath('resources/DEEPSEEK_V4_LICENSE.txt').is_file()
    assert resources.files('novel.llm').joinpath('resources/NOTICE.md').is_file()


def test_tokenizer_hash_mismatch_is_a_terminal_refusal():
    with pytest.raises(ContextBudgetRefused, match='资源校验失败'):
        _verified_tokenizer_bytes(b'not the pinned tokenizer')


class _SpyCache(ResponseCache):
    def __init__(self, directory: Path):
        super().__init__(directory)
        self.operations: list[str] = []

    def get(self, key: str):
        self.operations.append('get')
        return super().get(key)

    def put(self, key: str, response: ChatResponse):
        self.operations.append('put')
        return super().put(key, response)

    def evict(self, key: str):
        self.operations.append('evict')
        return super().evict(key)


@pytest.mark.parametrize('entry', ['text', 'json'])
def test_over_limit_touches_no_cache_transport_or_ledger_even_if_a_hit_exists(entry):
    cache_dir = Path(tempfile.mkdtemp(prefix='budget-cache-'))
    cache = _SpyCache(cache_dir)
    response = ChatResponse(
        content=('cached text' if entry == 'text' else json.dumps({'value': 'cached'})),
        finish_reason='stop',
        usage={'prompt_tokens': 1, 'completion_tokens': 1},
        model='deepseek-v4-pro',
    )
    request = _req(json_mode=entry == 'json', max_tokens=64_000)
    cache.put(cache_key(request), response)
    before = {path.name: path.read_bytes() for path in cache_dir.iterdir()}
    cache.operations.clear()

    transport = FakeTransport.always(response)
    ctx = _ctx()
    checker = partial(enforce_request_budget, counter=lambda _request: 936_001)
    client = LlmClient(ctx, transport, cache=cache, budget_checker=checker)

    with pytest.raises(ContextBudgetRefused):
        if entry == 'text':
            client.ask_text('sys', 'usr', purpose='over-limit')
        else:
            client.ask_json('sys', 'usr', model_cls=_JsonResponse, purpose='over-limit')

    after = {path.name: path.read_bytes() for path in cache_dir.iterdir()}
    assert cache.operations == []
    assert transport.calls == []
    assert ctx.ledger.usage() is None
    assert after == before


def test_preflight_checks_every_distinct_ladder_framing_before_side_effects():
    seen: list[tuple[str, bool]] = []

    def count(request: ChatRequest) -> int:
        seen.append((request.reasoning_effort, request.thinking))
        return 1

    enforce_request_budget(
        tuple(_req(thinking=thinking) for thinking in (True, True, False)),
        node='stage2.global', purpose='global', counter=count)
    assert seen == [('high', True), ('high', False)]
