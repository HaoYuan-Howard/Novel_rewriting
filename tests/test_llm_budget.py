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
from novel.llm.client import DEFAULT_RUNGS, LlmClient
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
        node='str06.stage2.source',
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
    'effort', ['medium', 'xhigh', 'ultra', '', 'HIGH'])
@pytest.mark.parametrize('thinking', [True, False])
def test_an_unregistered_effort_is_refused_instead_of_guessing_a_framing(
        effort: str, thinking: bool):
    """⭐ **未登记的档位关闭式拒绝**，不猜一个 framing 了事。

    ⚠⚠ 取值面里那两个**不是**「API 不认的值」：`medium` / `xhigh` 完全合法，
    只是被服务端静默映射回 `high`。它们被拒的理由与 `ultra` 不同——不是「猜不出 framing」，
    是「放行等于允许一个看起来降了档、实际一档没降的配置」，而账本分辨不出来。
    真实档位只有 low / high / max 三个，`max` 于 2026-08-21 启用（见下一条）。

    ⚠ prompt 是**发出去的字节**：猜错 framing 等于按另一个 prompt 估预算，
    而预算拒绝是花钱前的最后一道闸。报错必须给出行动。
    """
    with pytest.raises(TerminalState) as caught:
        count_input_tokens(_req(reasoning_effort=effort, thinking=thinking))
    message = str(caught.value)
    assert repr(effort) in message, f'报错没说是哪个档位：{message}'
    assert '登记' in message, f'报错没给出行动：{message}'


def test_max_costs_exactly_the_official_effort_prefix_and_only_when_thinking():
    """`max` 是**唯一**加文字前缀的档位：79 token，且只在带思考时加。

    ⚠ 79 这个数不是抄注释：它是 `4744479^` 那版删掉时实测的字节数，这里当场重量。
    对不上说明前缀字节被人改过——**前缀是发出去的字节**，改一个字符就是换了 prompt，
    此前按它估的预算与账全部作废。
    ⚠ `thinking=False` 时前缀无处落地（那段文字要求写出完整推敲过程）⇒ 与 high 等长。
    """
    from novel.llm.budget import _MAX_EFFORT_PREFIX

    high = count_input_tokens(_req(reasoning_effort='high', thinking=True))
    mx = count_input_tokens(_req(reasoning_effort='max', thinking=True))
    assert mx - high == 79, f'官方 effort 前缀不再是 79 token：{mx - high}'
    assert len(_MAX_EFFORT_PREFIX) == 476, '前缀字节数变了，不是当年那一段'

    high_off = count_input_tokens(_req(reasoning_effort='high', thinking=False))
    max_off = count_input_tokens(_req(reasoning_effort='max', thinking=False))
    assert max_off == high_off, '关思考时不该有前缀'


def test_every_explicit_effort_selects_exactly_one_rung():
    """`max` / `high` 都是显式单档；异常不再自动改变 effort。"""
    from novel.llm.client import rungs_from_effort

    assert rungs_from_effort('max') == (('max', True),)
    assert rungs_from_effort('low') == (('low', True),)
    assert rungs_from_effort(None) == DEFAULT_RUNGS == rungs_from_effort('high')


def test_every_ladder_rung_shares_the_same_framing():
    """现役档位**逐个**都走官方 `high` framing（不加文字前缀）⇒ token 数逐字相等。

    ⚠ 这是上面那条拒绝的**对照组**：只查「非法值被拒」而不查「合法值都通得过」，
    一次把取值面收窄过头的改动照样全绿。
    ⚠ 档位从 `novel/llm/client.py::DEFAULT_RUNGS` **现算**，不在这里手抄第二份：
    默认配置面**加宽**时（比如哪天把 `max` 加回去）这条要当场把新档位一并量，否则一个
    framing 没人核过的档位会带着错的预算估算上真跑——而预算是花钱前最后一道闸。
    """
    counts = {
        rung: count_input_tokens(_req(reasoning_effort=rung, thinking=True))
        for rung in sorted({effort for effort, _thinking in DEFAULT_RUNGS})
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
    assert set(MODEL_BUDGETS) == {'deepseek-v4-pro', 'deepseek-flash'}
    assert {(s.context_limit, s.max_output_tokens) for s in MODEL_BUDGETS.values()} == {
        (1_000_000, 384_000),
    }
    assert len(TOKENIZER_REVISION) == 40


def test_every_model_the_pipeline_can_name_is_registered_in_the_budget():
    """⚠ 全局默认 + `NODES` 里钉死的每一个模型 id，预算闸都必须认识。

    `model_budget` 对未知模型**关闭式拒绝**（加载资源之前）——一个拼错的模型名
    本该在预检就拦下；这道守卫保证注册表与 `MODEL_BUDGETS` 不分家
    （`contracts` 不能 import 运行时 ⇒ 这道交叉核对只能放在这一侧）。
    """
    from contracts.invariants import NODES
    from novel.config import DEFAULT_MODEL

    named = {DEFAULT_MODEL} | {spec.model for spec in NODES.values() if spec.model}
    assert named <= set(MODEL_BUDGETS), f'有模型 id 没进预算表：{named - set(MODEL_BUDGETS)}'


def test_official_tokenizer_and_two_message_framing_have_locked_counts():
    # 这些数由固定 tokenizer + BOS/System/User/Assistant/thinking framing 共同决定；
    # JSON mode 是顶层 API 控制参数，不凭空增加一段本地文字。
    assert count_input_tokens(_req()) == 6
    assert count_input_tokens(_req(
        system='你是编辑', user='请重写这一章', thinking=False, json_mode=True)) == 11
    assert count_input_tokens(_req(json_mode=True)) == count_input_tokens(_req(json_mode=False))


@pytest.mark.parametrize('input_tokens', [903_999, 904_000])
def test_inside_and_exact_context_boundary_pass(input_tokens):
    got = validate_budget(_estimate(input_tokens), node='str06.stage2.source', purpose='global')
    assert got.total_tokens <= 1_000_000


def test_one_token_over_context_boundary_is_an_explanatory_terminal_state():
    with pytest.raises(ContextBudgetRefused) as caught:
        validate_budget(_estimate(904_001), node='str06.stage2.source', purpose='global')
    err = caught.value
    assert issubclass(type(err), TerminalState)
    assert (err.input_tokens, err.max_tokens, err.context_limit) == (904_001, 96_000, 1_000_000)
    message = str(err)
    for fact in ('input_tokens=904001', 'declared_max_tokens=96000', 'total=1000001',
                 'context_limit=1000000', 'input_tokens <= 904000'):
        assert fact in message
    assert ('不截断' in message and '不分块聚合' in message
            and '预算预检拒绝不触发模型兜底' in message)


def test_declared_output_above_provider_limit_is_rejected():
    with pytest.raises(ContextBudgetRefused, match='单次输出上限') as caught:
        validate_budget(_estimate(1, 384_001), node='str06.stage4.bind', purpose='stage4.bind')
    assert caught.value.max_output_tokens == 384_000


def test_unknown_model_fails_closed_before_any_tokenizer_guess():
    with pytest.raises(ContextBudgetRefused, match='没有已验证') as caught:
        enforce_request_budget((_req(model='future-model'),), node='str06.stage2.source', purpose='global')
    assert caught.value.input_tokens is None
    assert '预算预检拒绝' in str(caught.value) and '模型兜底' in str(caught.value)


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
        # ⚠ `str06.stage2.source` 的整书单元按 purpose 显式登记（`executor_scope`），
        #   不是旧链那种节点名自带 scope；`book` 是它整书联合抽取的现役 purpose。
        if entry == 'text':
            client.ask_text('sys', 'usr', purpose='book')
        else:
            client.ask_json('sys', 'usr', model_cls=_JsonResponse, purpose='book')

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
        node='str06.stage2.source', purpose='global', counter=count)
    assert seen == [('high', True), ('high', False)]
