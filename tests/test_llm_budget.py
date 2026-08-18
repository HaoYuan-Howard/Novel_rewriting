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
from novel.llm.client import LADDER, LlmClient
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
         model: str = 'deepseek-v4-pro',
         reasoning_effort: str = 'high') -> ChatRequest:
    return ChatRequest(
        model=model,
        system=system,
        user=user,
        reasoning_effort=reasoning_effort,
        thinking=thinking,
        json_mode=json_mode,
        max_tokens=max_tokens,
    )


@pytest.mark.parametrize(
    'effort', ['max', 'medium', 'xhigh', 'ultra', '', 'HIGH'])
@pytest.mark.parametrize('thinking', [True, False])
def test_an_unregistered_effort_is_refused_instead_of_guessing_a_framing(
        effort: str, thinking: bool):
    """⭐ **未登记的档位关闭式拒绝**，不猜一个 framing 了事。

    ⚠ 这条是补给一段刚删掉的死分支的：`_render_prompt` 里曾有 `effort == 'max'` 的
    官方前缀分支，而 `max` 进不了 `client.py::LADDER`、`rungs_from_effort` 也拒绝它
    ⇒ 那条分支不可达，**且全仓没有一条测试扫过它**。
    删掉分支之后如果不补这一条，剩下的关闭式拒绝同样没人查——
    「删掉一段没人查的代码」不该顺手把它那道闸也变成没人查的。

    ⚠⚠ 取值面里那两个**不是**「API 不认的值」：`medium` / `xhigh` 完全合法，
    只是被服务端静默映射回 `high`。它们被拒的理由与 `ultra` 不同——不是「猜不出 framing」，
    是「放行等于允许一个看起来降了档、实际一档没降的配置」，而账本分辨不出来。
    真实档位只有 low / high / max 三个。

    ⚠ prompt 是**发出去的字节**：猜错 framing 等于按另一个 prompt 估预算，
    而预算拒绝是花钱前的最后一道闸。报错必须给出行动。
    """
    with pytest.raises(TerminalState) as caught:
        count_input_tokens(_req(reasoning_effort=effort, thinking=thinking))
    message = str(caught.value)
    assert repr(effort) in message, f'报错没说是哪个档位：{message}'
    assert '登记' in message, f'报错没给出行动：{message}'


def test_every_ladder_rung_shares_the_same_framing():
    """现役档位**逐个**都走官方 `high` framing（不加文字前缀）⇒ token 数逐字相等。

    ⚠ 这是上面那条拒绝的**对照组**：只查「非法值被拒」而不查「合法值都通得过」，
    一次把取值面收窄过头的改动照样全绿。
    ⚠ 档位从 `novel/llm/client.py::LADDER` **现算**，不在这里手抄第二份：阶梯
    **加宽**时（比如哪天把 `max` 加回去）这条要当场把新档位一并量，否则一个
    framing 没人核过的档位会带着错的预算估算上真跑——而预算是花钱前最后一道闸。
    """
    counts = {
        rung: count_input_tokens(_req(reasoning_effort=rung, thinking=True))
        for rung in sorted({effort for effort, _thinking in LADDER})
    }
    assert len(set(counts.values())) == 1, f'某个档位悄悄改了 framing：{counts}'


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


def test_git_never_rewrites_the_byte_verified_tokenizer_resource():
    attributes = Path('.gitattributes').read_text(encoding='utf-8').splitlines()
    assert 'novel/llm/resources/deepseek_v4_tokenizer.json -text' in attributes


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
