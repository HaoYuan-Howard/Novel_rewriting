#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
test_llm.py —— 同档重采 / Flash→Pro 兜底 · 缓存 · 响应契约 · 成本账。

⚠⚠ **这一份的存在本身就是 B2 的主要产出。** 原型那条恢复链扛着多类实测故障，
而它**一行测试都没有**——因为跑一次要花钱。现役取消 effort/thinking 自动降级，
保留同配置重采与 Flash→Pro 同配置兜底。
把「发请求」抽成一条缝（`novel/llm/transport.py::Transport`）之后，
每一种配置都能零成本跑一遍，而且能断言它**真的走过**哪一个模型
（只看最终结果的话，「Flash 成功」与「Pro 兜底成功」长得一模一样）。

⚠ 本机 `%TEMP%\\pytest-of-yh255` 的 ACL 坏了，`tmp_path` 一用就炸——一律 `tempfile.mkdtemp()`。
"""
from __future__ import annotations

import json
import multiprocessing
import tempfile
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

import pytest
from pydantic import BaseModel, Field

from contracts.schema import Usage
from novel.config import Config, DEFAULT_MODEL, load_config, parse_dotenv
from novel.errors import ContextBudgetRefused, ProvenanceRefused, TerminalState
from novel.llm.cache import (
    CachePolicy, ResponseCache, cache_key, request_fingerprint,
)
from novel.llm.client import (
    DEFAULT_MAX_TOKENS, DEFAULT_RUNGS, FLASH_MODEL, JSON_OBJECT_MODE,
    MAX_SAMPLES_PER_RUNG,
    NOTHINK_RUNGS, REJECT_GATES, REJECT_REASON_MAX, RESOURCE_INTERRUPTED,
    SALVAGE_ANSWER_FROM_REASONING, PRO_FALLBACK_MODEL, LlmClient, LlmFailed,
    models_for,
)
from novel.llm.ledger import CostLedger, combine_usage
from novel.llm.transport import (
    ChatRequest, ChatResponse, FakeTransport, MissingApiKey, TransportError,
    _classify,
)
from novel.observability import AttemptHeartbeat, RequestProgress
from novel.run import RunContext

# 既有响应闸用例按“一个显式模型行为档位”取分母；名称留在本测试文件内，
# 不再表示生产有自动 effort 降级。
LADDER = DEFAULT_RUNGS


class Resp(BaseModel):
    """一个**响应**模型（不是产物模型）：`ask_json` 吃的是这种。"""
    voice: dict = Field(...)
    surface_habits: list[str] = Field(...)
    note: str | None = None          # ← 可选，不该进 require


def _book() -> Path:
    base = Path(tempfile.mkdtemp())
    (base / '书名_123' / 'stage1').mkdir(parents=True)
    return base


def _ctx(*, cache: CachePolicy | None = None, paid: bool = True) -> RunContext:
    return RunContext.open(_book(), '123', 'stage3.style', paid=paid,
                           cache=cache or CachePolicy.default(), write_ledger=False)


def _client(transport, *, ctx=None, cache: ResponseCache | None = None, **kw) -> LlmClient:
    # 响应闸/缓存/重试的既有单元测试默认固定 Pro，避免把模型兜底这一根轴混进来；
    # Flash→Pro 由下面的专门用例显式传 model=FLASH_MODEL 覆盖。
    kw.setdefault('model', PRO_FALLBACK_MODEL)
    return LlmClient(ctx or _ctx(), transport, cache=cache,
                     sleep=lambda _s: None,          # 测试不真的睡
                     **kw)


OK_TEXT = ChatResponse(content='一段正文', finish_reason='stop',
                       usage={'prompt_tokens': 10, 'completion_tokens': 5},
                       model='deepseek-v4-pro')
OK_JSON = ChatResponse(
    content=json.dumps({'voice': {'a': 1}, 'surface_habits': ['h']}, ensure_ascii=False),
    finish_reason='stop', usage={'prompt_tokens': 10, 'completion_tokens': 5},
    model='deepseek-v4-pro')


def test_usage_combination_is_strict_about_missing_history_and_token_parts():
    left = Usage(calls_made=1, calls_cached=2, prompt_tokens=10,
                 completion_tokens=None, reasoning_tokens=3)
    right = Usage(calls_made=4, calls_cached=5, prompt_tokens=20,
                  completion_tokens=7, reasoning_tokens=6)

    assert combine_usage(left, None) is None
    got = combine_usage(left, right)
    assert got is not None
    assert (got.calls_made, got.calls_cached) == (5, 7)
    assert got.prompt_tokens == 30 and got.reasoning_tokens == 9
    assert got.completion_tokens is None


def test_ask_json_can_refresh_a_valid_cached_response():
    """Structured revision resampling must bypass, then replace, a valid cache row."""
    cache = ResponseCache(Path(tempfile.mkdtemp()))
    first = ChatResponse(
        content=json.dumps({'voice': {'v': 1}, 'surface_habits': ['old']}),
        finish_reason='stop')
    second = ChatResponse(
        content=json.dumps({'voice': {'v': 2}, 'surface_habits': ['new']}),
        finish_reason='stop')
    _client(FakeTransport.always(first), cache=cache).ask_json(
        'sys', 'usr', model_cls=Resp, purpose='refresh-json')
    transport = FakeTransport.always(second)
    got = _client(transport, cache=cache).ask_json(
        'sys', 'usr', model_cls=Resp, purpose='refresh-json',
        cache_policy=CachePolicy.refresh())
    assert got.voice == {'v': 2}
    assert len(transport.calls) == 1


# ─────────────────────────────────────────────────────────────────────────────
# 一、⭐ 同档重采 + Flash→Pro 兜底
# ─────────────────────────────────────────────────────────────────────────────

def test_default_behavior_is_one_rung_and_flash_has_exactly_one_pro_fallback():
    """默认不再自动降 effort / 关 thinking；模型链只允许 Flash → Pro。"""
    assert DEFAULT_RUNGS == (('high', True),)
    assert MAX_SAMPLES_PER_RUNG == 2, '内容恢复上限必须是首发 + 一次同档重采'
    assert models_for(FLASH_MODEL) == (FLASH_MODEL, PRO_FALLBACK_MODEL)
    assert models_for(PRO_FALLBACK_MODEL) == (PRO_FALLBACK_MODEL,)
    assert models_for('experiment-model') == ('experiment-model',)
    import inspect
    from novel.llm import client
    src = inspect.getsource(client)
    assert src.count('DEFAULT_RUNGS: Final') == 1
    assert src.count('def models_for(') == 1
    text_body = inspect.getsource(client.LlmClient.ask_text)
    json_body = inspect.getsource(client.LlmClient.ask_json)
    assert 'self._active_rungs_for' in text_body
    assert 'self._active_rungs_for' in json_body
    assert _client(FakeTransport.always(OK_TEXT))._rungs is DEFAULT_RUNGS


def _empty() -> ChatResponse:
    """实测那个空正文形态：`finish_reason='stop'`、答案全写进了 reasoning。"""
    return ChatResponse(content='', reasoning='想了很多，但答案没流出来',
                        finish_reason='stop', usage=None)


def test_flash_text_keeps_the_same_rung_then_falls_back_to_pro():
    events: list[dict] = []
    ctx = _ctx()
    ctx.observer = events.append
    t = FakeTransport(responder=lambda req, _i: (
        _empty() if req.model == FLASH_MODEL else OK_TEXT))

    client = _client(t, ctx=ctx, model=FLASH_MODEL)
    got = client.ask_text(
        'sys', 'usr', purpose='p')

    assert got == '一段正文'
    assert [req.model for req in t.calls] == (
        [FLASH_MODEL] * MAX_SAMPLES_PER_RUNG + [PRO_FALLBACK_MODEL])
    assert t.rungs == [DEFAULT_RUNGS[0]] * (MAX_SAMPLES_PER_RUNG + 1)
    switched = _observed(events, 'llm_model_fallback')
    assert [(e['from_model'], e['to_model'], e['reason']) for e in switched] == [
        (FLASH_MODEL, PRO_FALLBACK_MODEL, 'content_rejected')]
    assert client.accepted_model('p') == PRO_FALLBACK_MODEL
    assert client.accepted_models() == (PRO_FALLBACK_MODEL,)


def test_flash_json_uses_the_same_contract_on_the_pro_fallback():
    bad = ChatResponse(content='{}', finish_reason='stop', model=FLASH_MODEL)
    t = FakeTransport(responder=lambda req, _i: (
        bad if req.model == FLASH_MODEL else OK_JSON))

    got = _client(t, model=FLASH_MODEL).ask_json(
        'sys', 'usr', model_cls=Resp, purpose='p')

    assert got.voice == {'a': 1}
    assert [req.model for req in t.calls] == (
        [FLASH_MODEL] * MAX_SAMPLES_PER_RUNG + [PRO_FALLBACK_MODEL])
    assert t.rungs == [DEFAULT_RUNGS[0]] * (MAX_SAMPLES_PER_RUNG + 1)


def test_pro_primary_neither_falls_back_nor_downgrades():
    t = FakeTransport.always(_empty())
    with pytest.raises(LlmFailed, match=PRO_FALLBACK_MODEL):
        _client(t, model=PRO_FALLBACK_MODEL).ask_text(
            'sys', 'usr', purpose='p')
    assert [req.model for req in t.calls] == [PRO_FALLBACK_MODEL] * MAX_SAMPLES_PER_RUNG
    assert t.rungs == [DEFAULT_RUNGS[0]] * MAX_SAMPLES_PER_RUNG


def test_both_flash_and_pro_content_failure_raise_llmfailed():
    t = FakeTransport.always(_empty())
    with pytest.raises(LlmFailed, match='主模型与兜底模型'):
        _client(t, model=FLASH_MODEL).ask_text('sys', 'usr', purpose='p')
    assert [req.model for req in t.calls] == (
        [FLASH_MODEL] * MAX_SAMPLES_PER_RUNG
        + [PRO_FALLBACK_MODEL] * MAX_SAMPLES_PER_RUNG)


def test_retryable_flash_transport_exhaustion_falls_back_after_all_retries():
    t = FakeTransport(responder=lambda req, _i: (
        TransportError('503', retryable=True, status=503)
        if req.model == FLASH_MODEL else OK_TEXT))

    got = _client(t, model=FLASH_MODEL, max_retries=2).ask_text(
        'sys', 'usr', purpose='p')

    assert got == '一段正文'
    assert [req.model for req in t.calls] == [
        FLASH_MODEL, FLASH_MODEL, FLASH_MODEL, PRO_FALLBACK_MODEL]


def test_nonretryable_flash_transport_failure_does_not_spend_a_pro_call():
    t = FakeTransport.always(
        TransportError('400', retryable=False, status=400))
    with pytest.raises(TransportError):
        _client(t, model=FLASH_MODEL).ask_text('sys', 'usr', purpose='p')
    assert [req.model for req in t.calls] == [FLASH_MODEL]


@pytest.mark.parametrize('entry', ['text', 'json'])
def test_nonretryable_error_from_the_pro_fallback_is_llmfailed(entry):
    def responder(req: ChatRequest, _i: int):
        if req.model == FLASH_MODEL:
            return _empty()
        return TransportError('400', retryable=False, status=400)

    t = FakeTransport(responder=responder)
    with pytest.raises(LlmFailed, match='主模型与兜底模型'):
        client = _client(t, model=FLASH_MODEL)
        if entry == 'text':
            client.ask_text('sys', 'usr', purpose='p')
        else:
            client.ask_json('sys', 'usr', model_cls=Resp, purpose='p')
    assert [req.model for req in t.calls] == (
        [FLASH_MODEL] * MAX_SAMPLES_PER_RUNG + [PRO_FALLBACK_MODEL])


def test_single_model_diagnostic_can_explicitly_disable_product_fallback():
    t = FakeTransport.always(_empty())
    with pytest.raises(LlmFailed):
        _client(t, model=FLASH_MODEL, enable_model_fallback=False).ask_text(
            'sys', 'usr', purpose='p')
    assert [req.model for req in t.calls] == [FLASH_MODEL] * MAX_SAMPLES_PER_RUNG


def test_flash_and_pro_are_both_budget_checked_before_any_wire_attempt():
    seen: list[str] = []

    def refuse(requests, **_kwargs):
        seen.extend(req.model for req in requests)
        raise ContextBudgetRefused('合成 Pro 预算拒绝', model=PRO_FALLBACK_MODEL)

    t = FakeTransport.always(OK_TEXT)
    with pytest.raises(ContextBudgetRefused):
        _client(t, model=FLASH_MODEL, budget_checker=refuse).ask_text(
            'sys', 'usr', purpose='p')
    assert seen == [FLASH_MODEL, PRO_FALLBACK_MODEL]
    assert t.calls == []


@pytest.mark.parametrize('rung', range(len(LADDER)))
def test_the_default_rung_can_recover_an_empty_answer(rung):
    """默认档首发为空时，同档第二采仍可拿回正文，不触发模型兜底。

    ⚠ 空正文那个形态是实测的：`finish_reason='stop'`、`reasoning` 很长、`content` 是空串
    ——**不是截断**，是推理流与答案流的边界丢了。照着「截断」去修（加 max_tokens）救不了它。
    ⇒ 保留同档重采，取消的是后续自动 effort 降级。
    """
    empties = (MAX_SAMPLES_PER_RUNG * rung
               + MAX_SAMPLES_PER_RUNG - 1)  # 本档首发为空，最后一采成功

    def responder(_req: ChatRequest, i: int) -> ChatResponse:
        return _empty() if i < empties else OK_TEXT

    t = FakeTransport(responder=responder)
    assert _client(t).ask_text('sys', 'usr', purpose='t') == '一段正文'
    expected = ([r for r in LADDER[:rung] for _ in range(MAX_SAMPLES_PER_RUNG)]
                + [LADDER[rung]] * MAX_SAMPLES_PER_RUNG)
    assert t.rungs == expected, '走过的采样与阶梯对不上'


def test_all_rungs_empty_raises_and_never_returns_an_empty_draft():
    """**全空则抛，绝不返回空稿。**

    ⚠ 一份空稿落下去，下游是**确定性代码**——它不会像写手那样将就着用，
    它会在几层之外炸，或者更坏：静默产出一份缺内容的东西。
    ⚠ 每个配置两采（首发 + 一次重采）全空才判失败——重采不是无限重试，烧完就退。
    """
    t = FakeTransport.always(_empty())
    with pytest.raises(LlmFailed, match='拿不到正文'):
        _client(t).ask_text('sys', 'usr', purpose='t')
    assert t.rungs == [r for r in LADDER for _ in range(MAX_SAMPLES_PER_RUNG)]


def test_thinking_off_walks_only_the_single_nothink_rung():
    """⭐ 思考开关 `off`：两个入口都只走**单档** `('low', False)`，不再有降级。

    阶梯扛的两类故障全由推理流引起（模块 docstring ①②）——关掉 thinking 后
    没有降级对象；复制同档当第二档等于把「重试」混进「阶梯」。
    """
    t = FakeTransport.always(OK_TEXT)
    c = _client(t, thinking_mode='off')
    assert c.ask_text('sys', 'usr', purpose='t') == '一段正文'
    assert t.rungs == list(NOTHINK_RUNGS) == [('low', False)]

    t2 = FakeTransport.always(OK_JSON)
    got = _client(t2, thinking_mode='off').ask_json(
        'sys', 'usr', model_cls=Resp, purpose='t')
    assert got.voice == {'a': 1}
    assert t2.rungs == [('low', False)]


def test_a_node_effort_override_selects_one_explicit_rung():
    """节点 effort 是显式配置，不是异常后的自动降档。"""
    low = _client(FakeTransport.always(OK_TEXT), effort='low')
    assert low._rungs == (('low', True),)
    assert _client(FakeTransport.always(OK_TEXT), effort='high')._rungs is LADDER
    assert _client(FakeTransport.always(OK_TEXT), effort=None)._rungs is LADDER
    with pytest.raises(ValueError, match='effort'):
        _client(FakeTransport.always(OK_TEXT), effort='ultra')


def test_efforts_the_server_remaps_to_high_are_refused_instead_of_silently_accepted():
    """⚠⚠ `medium` / `xhigh` **是合法参数**——服务端只是把它们静默映射回 `high`。

    所以这道闸拒的不是「API 不认的值」，而是「**看起来降了、实际没降**的配置」：
    放行它们，账本 `reasoning_effort` 那一列会如实写着 `medium`，而模型按 `high` 跑，
    于是事后没有任何东西说得出这一次到底按什么档跑的——**这正是 A/B 最怕的那种脏数据**。
    ⚠ `max` 是另一回事（升档、要官方 effort 前缀），它由 budget 那一侧拒绝。
    """
    for effort in ('medium', 'xhigh'):
        with pytest.raises(ValueError, match='映射回'):
            _client(FakeTransport.always(OK_TEXT), effort=effort)


def test_thinking_off_exhaustion_still_raises_and_never_returns_an_empty_draft():
    """off 模式全空同样抛 `LlmFailed`——「绝不返回空稿」与思考开关无关。

    单档也照走同档重采（首发 + 一次重采），重采完仍空才认输。
    """
    t = FakeTransport.always(_empty())
    with pytest.raises(LlmFailed, match='拿不到正文'):
        _client(t, thinking_mode='off').ask_text('sys', 'usr', purpose='t')
    assert t.rungs == [r for r in NOTHINK_RUNGS for _ in range(MAX_SAMPLES_PER_RUNG)]


def _observed(events: list, name: str = 'llm_rung_rejected') -> list[dict]:
    return [e for e in events if e['event'] == name]


@pytest.mark.parametrize('body, gate', [
    ('这不是 JSON', 'parse'),
    (json.dumps({'voice': {}, 'surface_habits': ['h']}), 'require'),
    (json.dumps({'voice': 'not-a-dict', 'surface_habits': ['h']}), 'schema'),
])
def test_a_rung_rejected_by_a_content_gate_says_which_gate_and_why(body, gate):
    """⭐ **响应拒绝必须出声，而且说得出是哪一道闸。**

    ⚠⚠ 这条守的是一个实测踩过的洞：内容闸拒掉一档时只 `evict` + 降档，而账本那一行
    记的是**传输层**的结论（`outcome=ok` / `finish_reason=stop` / `will_retry=false`）
    ⇒ 一次「high 被拒、退到 low 才成功」的调用，事后从账本上读起来像两次成功的调用，
    理由一个字都不在（被拒的那份响应随即被 `evict` 从盘上删掉）。
    ⇒ `gate` 是机器可数的那一半，`reason` 是人读的那一半，两个都要有。
    ⚠ 这条守的是**出声**，不是「退了哪一档」：2026-08-22 起内容闸拒的下一步改成
    同档重采（`_worth_resampling`），`next_*` 因此指回本档——事件仍须说真话。
    """
    events: list[dict] = []
    ctx = _ctx()
    ctx.observer = events.append
    bad = ChatResponse(content=body, reasoning='推敲过程', finish_reason='stop')
    t = FakeTransport(responder=lambda _req, i: bad if i == 0 else OK_JSON)

    got = _client(t, ctx=ctx).ask_json('sys', 'usr', model_cls=Resp, purpose='p')

    assert got.voice == {'a': 1}
    assert t.rungs == [LADDER[0], LADDER[0]], '被拒之后必须真的再采一次'
    rejected = _observed(events)
    assert len(rejected) == 1, '拒绝只出一次声：成功的第二采不出声'
    e = rejected[0]
    assert e['gate'] == gate and e['gate'] in REJECT_GATES
    # ⚠ 跟着 `JSON_OBJECT_MODE` 走，别写死：2026-08-22 起 `ask_json` 不再发
    #   `response_format`（用户裁决，见那一格的注释）。这条断言守的是「事件说的是
    #   **真的发出去的那一位**」，不是「ask_json 恒 True」。
    assert (e['source'], e['json_mode']) == ('wire', JSON_OBJECT_MODE)
    assert (e['reasoning_effort'], e['thinking']) == LADDER[0]
    # 2026-08-22 起内容闸拒的下一步是**同档重采**（`_worth_resampling`）⇒ next 指回本档。
    assert (e['next_effort'], e['next_thinking']) == LADDER[0]
    assert e['exhausted'] is False
    assert (e['purpose'], e['node'], e['sid']) == ('p', 'stage3.style', '123')
    assert e['finish_reason'] == 'stop'
    assert e['reason'] and not e['reason_truncated']
    assert (e['content_chars'], e['reasoning_chars']) == (len(body), len('推敲过程'))
    # ⚠ 事件里**不带响应正文**：被拒的那份可能是半截 JSON 或整段推敲过程。
    assert body not in json.dumps(e, ensure_ascii=False)
    assert '推敲过程' not in json.dumps(e, ensure_ascii=False)


def test_every_rung_rejection_is_observed_and_only_the_last_one_is_exhausted():
    """走完当前模型配置时**两采各出一声**，最后那一声标 `exhausted`。

    ⚠ `exhausted` 与 `LlmFailed` 不是一回事：缓存那一趟走完也会标它，
    而那一趟走完之后还有真跑那一趟。它说的是「这一趟的阶梯到头了」。
    ⚠ 当前每档第一声 `resample=True`，表示后面还有一次**同档重采**：该事件的
    `next_effort`/`next_thinking` 必须指回**同一档**——否则「重采」会被
    统计口径读成「降档」，而降档统计正是这条事件存在的目的。
    ⚠ 2026-08-22 起空正文那一格叫 `answer_lost`（`_empty()` 正是**非截断**的那种
    形态）；`empty_text` 只剩「空**且**截断」那一半——见下面那条参数化。
    """
    events: list[dict] = []
    ctx = _ctx()
    ctx.observer = events.append
    t = FakeTransport.always(_empty())

    with pytest.raises(LlmFailed, match='拿不到正文'):
        _client(t, ctx=ctx).ask_text('sys', 'usr', purpose='p')

    rejected = _observed(events)
    walked = [r for r in LADDER for _ in range(MAX_SAMPLES_PER_RUNG)]
    assert [(e['reasoning_effort'], e['thinking']) for e in rejected] == walked
    assert {e['gate'] for e in rejected} == {'answer_lost'}
    assert {e['json_mode'] for e in rejected} == {False}
    assert [e['resample'] for e in rejected] == (
        [True] * (MAX_SAMPLES_PER_RUNG - 1) + [False]) * len(LADDER)
    assert [e['exhausted'] for e in rejected] == [False] * (len(walked) - 1) + [True]
    assert rejected[-1]['next_effort'] is None and rejected[-1]['next_thinking'] is None
    # 第一声仍指回同模型同档；Pro 主模型第二声才耗尽，不存在低档。
    assert all(
        (event['next_model'], event['next_effort'], event['next_thinking'])
        == (PRO_FALLBACK_MODEL, *LADDER[0])
        for event in rejected[:-1])
    assert rejected[-1]['next_model'] is None


@pytest.mark.parametrize('finish, text_gate, json_gate', [
    ('stop', 'answer_lost', 'answer_lost'),
    ('length', 'empty_text', 'parse'),
])
def test_an_empty_body_is_answer_lost_only_when_it_is_not_a_truncation(
        finish, text_gate, json_gate):
    """⭐ **空正文按 `finish_reason` 劈成两格**，两个入口用同一把尺子。

    ⚠⚠ 它守的是一个账面上的洞：`ask_json` 里空正文照旧走 `json.loads('')` 必然抛
    ⇒「答案写进了推理通道」（`client.py` 模块 docstring ①）与「JSON 被截断」（②）
    在账上塌成同一个 `parse`。而这两类的处置**正好相反**——①换新样本、②腾预算。
    2026-08-22 复盘四份账本 752 次 wire 时正是这一格挡住了诊断：
    flash@high 418 次里 60 次是 ①、pro 255 次里 0 次，而账面上它与截断长得一样。

    ⚠ 劈的是**账**不是**判定**：走法仍由 `_worth_resampling` 一家管
    ⇒ 最后那条断言钉的就是「加了这一格之后，走过的档位与从前逐字相同」。
    """
    empty = ChatResponse(content='', reasoning='想了很多', finish_reason=finish)

    for gate, call in (
        (text_gate, lambda c: c.ask_text('sys', 'usr', purpose='p')),
        (json_gate, lambda c: c.ask_json('sys', 'usr', model_cls=Resp, purpose='p')),
    ):
        events: list[dict] = []
        ctx = _ctx()
        ctx.observer = events.append
        t = FakeTransport.always(empty)

        with pytest.raises(LlmFailed):
            call(_client(t, ctx=ctx))

        assert {e['gate'] for e in _observed(events)} == {gate}
        assert gate in REJECT_GATES
        # 截断不重采（每档一次），非截断每档最多两采。
        per_rung = 1 if finish == 'length' else MAX_SAMPLES_PER_RUNG
        assert t.rungs == [r for r in LADDER for _ in range(per_rung)]


# ── 服务端资源中断：走传输层重试，不碰采样预算 ──────────────────────────────

def _interrupted(usage: dict | None = None) -> ChatResponse:
    """`finish_reason='insufficient_system_resource'`：服务端自称这次被资源中断了。

    ⚠ HTTP 是 200、`usage` 是真的、**钱花了**——它与连接故障那种「没到达模型」
    不是一回事，所以这里给它一份 usage。
    """
    return ChatResponse(content='', reasoning='', finish_reason=RESOURCE_INTERRUPTED,
                        usage=usage or {'prompt_tokens': 10, 'completion_tokens': 0},
                        model='deepseek-v4-pro')


def test_a_resource_interruption_retries_on_the_wire_and_never_touches_the_sampling_budget():
    """⭐ 资源中断走**传输层重试**那条循环，内容闸根本看不到它。

    ⚠⚠ 这一条守的是「三条循环各管各的」：判成 `answer_lost` 的话，两次资源中断
    就能把一个付费节点推进 `low` 档（`stage4.plan` 在 low 上有已知劣化），
    而它一次**模型行为**问题都不是。⇒ 断言三样：档位没动 · 采样预算没消耗
    （`gate_decision` 的 `sample` 仍是 1）· 内容闸一声拒都没出。
    ⚠ 账上仍记一行 wire（`usage` 是真的、钱花了），靠 `finish_reason` 列分辨——
    **不新增账本字段**。
    """
    events: list[dict] = []
    ctx = _ctx()
    ctx.observer = events.append
    slept: list[float] = []

    def responder(_req: ChatRequest, i: int) -> ChatResponse:
        return _interrupted() if i == 0 else OK_JSON

    t = FakeTransport(responder=responder)
    client = LlmClient(ctx, t, cache=None, sleep=slept.append)
    got = client.ask_json('sys', 'usr', model_cls=Resp, purpose='p')

    assert got.voice == {'a': 1}
    # 档位一格没动：两次上线都在首档，**不是**降档也**不是**同档重采。
    assert t.rungs == [LADDER[0], LADDER[0]]
    assert not _observed(events), '内容闸不该看见资源中断'
    gates = [e for e in ctx.ledger.audit_events if e['kind'] == 'gate_decision']
    assert [(g['decision'], g['sample'], g['rung_index']) for g in gates] == [
        ('accepted', 1, 0)], '采样预算被资源中断吃掉了'
    assert client.accepted_attempt('p') == gates[0]['attempt_id']
    assert client.accepted_request_fingerprint('p') == request_fingerprint(t.calls[1])
    assert slept, '资源中断要退避一下再来，不是原地立刻重发'


def test_a_resource_interruption_that_never_recovers_is_terminal_and_says_it_was_billed():
    """⭐ 重试额度用尽仍然中断 ⇒ `LlmFailed`（`TerminalState` ⇒ exit 2，不打栈）。

    ⚠ 报错里必须说**已经计费**：它与连接故障那种「不花钱、同参数重跑即可」长得像，
    而两者的下一步判断不同——这一条上人会想知道自己付没付钱。
    """
    ctx = _ctx()
    t = FakeTransport.always(_interrupted())
    client = LlmClient(ctx, t, cache=None, model=PRO_FALLBACK_MODEL,
                       sleep=lambda _s: None, max_retries=2)

    with pytest.raises(LlmFailed, match='资源中断') as excinfo:
        client.ask_json('sys', 'usr', model_cls=Resp, purpose='p')

    assert '已经计费' in str(excinfo.value)
    assert isinstance(excinfo.value, TerminalState)
    # 3 次上线（首发 + 2 次重试）全在 Pro 同档，不存在自动低档。
    assert t.rungs == [LADDER[0]] * 3


# ── 答案写进推理通道时，过得了同一套闸就采纳 ────────────────────────────────

_SALVAGEABLE = json.dumps({'voice': {'a': 1}, 'surface_habits': ['h']}, ensure_ascii=False)


def _lost(reasoning: str, finish: str = 'stop') -> ChatResponse:
    """正文空、答案（或别的什么）在 `reasoning` 里。"""
    return ChatResponse(content='', reasoning=reasoning, finish_reason=finish,
                        usage={'prompt_tokens': 10, 'completion_tokens': 9,
                               'reasoning_tokens': 9},
                        model='deepseek-v4-pro')


def test_an_answer_written_into_the_reasoning_channel_is_salvaged_instead_of_resampled():
    """⭐ `content` 空而 `reasoning` 整体过得了同一套闸 ⇒ **采纳，不重采**。

    ⚠⚠ 推翻的是「救回来是半成品」那句老理由：留证 5/5 份横跨两个 `json_mode` 臂，
    推理通道里那段**整体就是一份合法 JSON**、一个字推敲过程都没混进去，
    其中 1 份完全合契约——那一次丢掉的是一份**可用的产物**，然后又付了一次钱重采。
    ⇒ 断言只发 **1 次** wire：这条路省的就是那一次重采。
    """
    assert SALVAGE_ANSWER_FROM_REASONING, '这条路关掉的话，本测试钉的是另一件事'
    ctx = _ctx()
    t = FakeTransport.always(_lost(_SALVAGEABLE))
    client = _client(t, ctx=ctx)
    got = client.ask_json('sys', 'usr', model_cls=Resp, purpose='p')

    assert got.voice == {'a': 1} and got.surface_habits == ['h']
    assert len(t.calls) == 1, '捞回来了就不该再付一次重采的钱'
    assert client.accepted_request_fingerprint('p') == request_fingerprint(t.calls[0])


def test_a_contract_can_prefer_one_normal_resample_without_discarding_valid_salvage():
    """首份合法救回稿作后备；第二采拿到正常正文时采用正常正文。"""
    normal = ChatResponse(
        content=json.dumps({'voice': {'normal': 1}, 'surface_habits': ['正文']}),
        reasoning='', finish_reason='stop',
        usage={'prompt_tokens': 10, 'completion_tokens': 5},
        model='deepseek-v4-flash')
    t = FakeTransport(lambda _req, i: _lost(_SALVAGEABLE) if i == 0 else normal)
    ctx = _ctx()

    client = _client(t, ctx=ctx)
    got = client.ask_json(
        'sys', 'usr', model_cls=Resp, purpose='p',
        prefer_content_over_reasoning_salvage=True)

    assert got.voice == {'normal': 1}
    assert len(t.calls) == 2 and t.rungs == [LADDER[0], LADDER[0]]
    gates = [e for e in ctx.ledger.audit_events if e['kind'] == 'gate_decision']
    assert [(g['decision'], g['gate'], g['resample'], g['salvaged'])
            for g in gates] == [
                ('rejected', 'answer_lost', True, False),
                ('accepted', None, False, False),
            ]
    assert client.accepted_attempt('p') == gates[-1]['attempt_id']
    assert client.accepted_request_fingerprint('p') == request_fingerprint(t.calls[1])


def test_a_contract_uses_valid_salvage_after_the_preferred_normal_sample_fails():
    """第二采仍不可用时采纳已过契约的后备，不得继续降到 low。"""
    invalid = ChatResponse(
        content='不是 JSON', reasoning='', finish_reason='stop',
        usage={'prompt_tokens': 10, 'completion_tokens': 5},
        model='deepseek-v4-flash')
    t = FakeTransport(lambda _req, i: _lost(_SALVAGEABLE) if i == 0 else invalid)
    ctx = _ctx()
    events: list[dict] = []
    ctx.observer = events.append

    client = _client(t, ctx=ctx)
    got = client.ask_json(
        'sys', 'usr', model_cls=Resp, purpose='p',
        prefer_content_over_reasoning_salvage=True)

    assert got.voice == {'a': 1}
    assert len(t.calls) == 2 and t.rungs == [LADDER[0], LADDER[0]]
    gates = [e for e in ctx.ledger.audit_events if e['kind'] == 'gate_decision']
    assert [(g['decision'], g['gate'], g['salvaged']) for g in gates] == [
        ('rejected', 'parse', False),
        ('accepted', None, True),
    ]
    lost, = [e for e in ctx.ledger.audit_events if e['kind'] == 'answer_lost']
    assert lost['salvaged'] is True
    rejected, = _observed(events)
    assert rejected['fallback_salvage'] is True
    assert rejected['next_effort'] is None and rejected['exhausted'] is False
    assert client.accepted_attempt('p') == gates[-1]['attempt_id']
    assert client.accepted_request_fingerprint('p') == request_fingerprint(t.calls[0])


def test_a_transport_failure_on_the_preferred_resample_does_not_discard_the_salvage():
    """⭐ **手里已有一份过完整契约的响应，不能因为下一次没打通就把它扔掉。**

    这一档的后备只在「再争取一次正常 content」时存在；而争取的那一次**死在传输层**
    （连接故障 / 资源中断退避耗尽）与「拿到了一份更好的」是两件完全不同的事。
    原来异常直接冒出 `ask_json`，后备随栈消失 ⇒ 重跑要**重新付一次**同样的钱，
    而那份合格响应连缓存都进不去（`ResponseCache.put` 拒写空正文）。
    """
    from novel.llm.transport import TransportError

    def responder(_req, index):
        if index == 0:
            return _lost(_SALVAGEABLE)
        raise TransportError('connection reset', retryable=False, kind='connection')

    ctx = _ctx()
    t = FakeTransport(responder)

    client = _client(t, ctx=ctx)
    got = client.ask_json(
        'sys', 'usr', model_cls=Resp, purpose='p',
        prefer_content_over_reasoning_salvage=True)

    assert got.voice == {'a': 1}, '合格后备被一次连接故障扔掉了'
    gates = [e for e in ctx.ledger.audit_events if e['kind'] == 'gate_decision']
    assert ('accepted', None, True) in [
        (g['decision'], g['gate'], g['salvaged']) for g in gates]
    lost, = [e for e in ctx.ledger.audit_events if e['kind'] == 'answer_lost']
    assert lost['salvaged'] is True
    accepted, = [event for event in gates if event['decision'] == 'accepted']
    assert client.accepted_attempt('p') == accepted['attempt_id']
    assert client.accepted_request_fingerprint('p') == request_fingerprint(t.calls[0])


def test_a_transport_failure_without_any_salvage_still_propagates():
    """没有后备时传输故障必须原样抛出——不许被这条新路悄悄吞掉。"""
    from novel.llm.transport import TransportError

    def responder(_req, _index):
        raise TransportError('connection reset', retryable=False, kind='connection')

    t = FakeTransport(responder)
    with pytest.raises(TransportError):
        _client(t, ctx=_ctx()).ask_json(
            'sys', 'usr', model_cls=Resp, purpose='p',
            prefer_content_over_reasoning_salvage=True)


def test_salvage_runs_the_full_contract_so_a_merely_parseable_reasoning_is_still_rejected():
    """⭐ 捞的是**同一套四道闸**，不是「解析得动就用」。

    ⚠⚠ 这一条守的是 `SALVAGE_ANSWER_FROM_REASONING` 边界②：采纳进来的东西必须与
    正常路径的产物**同质**，否则这条路就是在契约上开了个后门。
    ⇒ 合法 JSON 但不合 `Resp` 契约的那份**不采纳**，走法与 2026-08-22 之前逐字相同。
    """
    for reasoning in (
        '想了很多，但答案没流出来',                      # 压根不是 JSON
        '{"voice": {"a": 1}, "surface_hab',              # 半截
        json.dumps({'voice': 'not-a-dict', 'surface_habits': ['h']}),   # 合法 JSON，不合契约
        json.dumps({'voice': {}, 'surface_habits': ['h']}),             # 合法 JSON，空壳闸
    ):
        events: list[dict] = []
        ctx = _ctx()
        ctx.observer = events.append
        t = FakeTransport.always(_lost(reasoning))

        with pytest.raises(LlmFailed):
            _client(t, ctx=ctx).ask_json('sys', 'usr', model_cls=Resp, purpose='p')

        # 走法逐字相同：每档按生产上限两采，闸名仍是 `answer_lost`。
        assert t.rungs == [r for r in LADDER for _ in range(MAX_SAMPLES_PER_RUNG)]
        assert {e['gate'] for e in _observed(events)} == {'answer_lost'}


def test_salvage_applies_to_neither_a_truncation_nor_ask_text():
    """⭐ 两条边界，各有各的理由，都不许顺手放开。

    · **截断那一半不捞**（`finish='length'`）：那时 `reasoning` 自己也是半截的，
      而②的处置是腾预算，捞它只会把两类搅在一起。
    · **`ask_text` 不捞**：它一次都没量过，而且**没有等价的客观闸**——JSON 那边
      「解析得动 + 合契约」是可判的证据，自然语言那边「这段是答案还是推敲」判不出来。
      ⇒ 结构上就不给它这条路（捞的代码只在 `ask_json` 里）。
    """
    ctx = _ctx()
    t = FakeTransport.always(_lost(_SALVAGEABLE, finish='length'))
    with pytest.raises(LlmFailed):
        _client(t, ctx=ctx).ask_json('sys', 'usr', model_cls=Resp, purpose='p')
    assert t.rungs == list(LADDER), '截断不做同配置重采'

    t2 = FakeTransport.always(_lost(_SALVAGEABLE))
    with pytest.raises(LlmFailed, match='拿不到正文'):
        _client(t2, ctx=_ctx()).ask_text('sys', 'usr', purpose='p')
    assert t2.rungs == [r for r in LADDER for _ in range(MAX_SAMPLES_PER_RUNG)]


def test_a_salvaged_answer_is_marked_in_both_audit_rows_and_stays_out_of_the_cache():
    """⭐ 捞回来的那一份**在账上分得出来**，而且**进不了缓存**。

    ⚠⚠ 不落 `salvaged` 这一位，「这本书有几章的产物不是走正常通道产出的」事后就答不了
    ——那正是这条路全部的产品语义争议所在。两种审计行都要带：
    `gate_decision` 答「判定与来路」，`answer_lost` 答「证据在哪」。
    ⚠ 留证**照写不误**：捞成功之后它已经不是「丢失的证据」而是**被采纳产物的来源**。
    ⚠ 缓存那条是**认下来的代价**：`ResponseCache.put` 拒写空正文（那是对的），
    而把捞出来的文本塞回 `content` 再存等于改写供应商的响应 ⇒ 与 `response_sha256` 分家。
    ⇒ 这条路省的是**本次的重采**，不是下次重跑的钱。
    """
    cache = ResponseCache(Path(tempfile.mkdtemp()))
    ctx = _ctx()
    t = FakeTransport.always(_lost(_SALVAGEABLE))
    got = _client(t, ctx=ctx, cache=cache).ask_json(
        'sys', 'usr', model_cls=Resp, purpose='p')
    assert got.voice == {'a': 1}

    gate, = [e for e in ctx.ledger.audit_events if e['kind'] == 'gate_decision']
    assert (gate['decision'], gate['gate'], gate['salvaged']) == ('accepted', None, True)
    lost, = [e for e in ctx.ledger.audit_events if e['kind'] == 'answer_lost']
    assert lost['salvaged'] is True and lost['content_chars'] == 0
    assert lost['reasoning_chars'] == len(_SALVAGEABLE)

    # 空正文进不了缓存 ⇒ 同参数再来一次仍然要真发。
    t2 = FakeTransport.always(_lost(_SALVAGEABLE))
    _client(t2, ctx=_ctx(), cache=cache).ask_json(
        'sys', 'usr', model_cls=Resp, purpose='p')
    assert len(t2.calls) == 1, '捞回来的那份不该被当成正常响应存进缓存'


def test_a_nonempty_content_gate_rejection_resamples_once_before_stepping_down():
    """⭐ 非空正文被内容闸拒（schema 形状不对）**先同档重采一次**再走模型兜底。

    ⚠⚠ **这条测试 2026-08-22 反转过，反例证据记在这里，别按旧字面改回去。**
    它原来叫 `..._does_not_resample`，断言「内容闸拒绝必须每档一采、直接降档」，
    理由是「同一参数大概率拿回同一份形状」。推翻它的是盘上读数：
    `stage4.plan` 5 次带失败的跑里 **3 次走成 `high→low→low/nothink→high`**
    ——整条阶梯白走一遍再回到 `high` 才成功；另 2 次以 `high→low` 收尾，
    即**最终计划产自降档档位**。22 次实付里 47.7% 的 completion 花在没被采纳的档位上。
    而真发循环 `use_cache=False` ⇒ 同档重采拿的本来就是新样本。
    ⚠ 唯一仍然不做同档重采的是**截断**，那一条由
    `test_a_truncated_json_walks_the_ladder_because_lower_effort_frees_budget` 守着。
    """
    events: list[dict] = []
    ctx = _ctx()
    ctx.observer = events.append
    bad = ChatResponse(content=json.dumps({'voice': 'not-a-dict',
                                           'surface_habits': ['h']}),
                       finish_reason='stop')
    t = FakeTransport(responder=lambda _r, i: bad if i < 1 else OK_JSON)

    client = _client(t, ctx=ctx)
    got = client.ask_json('sys', 'usr', model_cls=Resp, purpose='p')

    assert got.voice == {'a': 1}
    assert t.rungs == [LADDER[0], LADDER[0]], '内容闸拒绝必须先同档重采一次'
    assert [e['resample'] for e in _observed(events)] == [True]
    accepted, = [event for event in ctx.ledger.audit_events
                 if event['kind'] == 'gate_decision' and event['decision'] == 'accepted']
    assert client.accepted_attempt('p') == accepted['attempt_id']
    assert client.accepted_request_fingerprint('p') == request_fingerprint(t.calls[1])


def test_a_long_rejection_reason_is_truncated_and_marked():
    """`ValidationError` 的文本能有几千字，而观测事件是**一行 JSON**。"""
    events: list[dict] = []
    ctx = _ctx()
    ctx.observer = events.append
    long_why = '很长的理由' * 500

    with pytest.raises(LlmFailed):
        _client(FakeTransport.always(OK_JSON), ctx=ctx).ask_json(
            'sys', 'usr', model_cls=Resp, purpose='p',
            validator=lambda _obj, _resp: long_why)

    e = _observed(events)[0]
    assert e['gate'] == 'validator'
    assert len(e['reason']) == REJECT_REASON_MAX and e['reason_truncated'] is True


def test_explicit_rungs_beat_the_thinking_mode():
    """显式 `rungs` 的优先级不因思考开关而变——收窄阶梯的调用照走自己的档。

    ⚠ 这里曾点名 `TRIM_RUNGS`，那个常量随 stage5 收缩臂整臂下线一起删了；
    现役的同款是 `novel/stages/stage6/hook.py::CANDIDATE_RETRY_RUNGS`。
    """
    t = FakeTransport.always(OK_TEXT)
    c = _client(t, thinking_mode='off')
    c.ask_text('sys', 'usr', purpose='t', rungs=(('high', True),))
    assert t.rungs == [('high', True)]


def test_a_malformed_thinking_mode_is_refused_at_client_construction():
    """取值闸在构造期（同 concurrency 那道闸）：手搭一个 client 也拦得住。"""
    with pytest.raises(ValueError, match='DEEPSEEK_THINKING'):
        _client(FakeTransport.always(OK_TEXT), thinking_mode='max')


def test_nonempty_but_length_capped_flash_text_falls_back_to_pro_without_downgrade():
    """非空不等于完整；截断的 Flash 不重采、不降档，直接交给同档 Pro。"""
    cut = ChatResponse(content='写到一半', reasoning='想了很久',
                       finish_reason='length', usage=None)
    t = FakeTransport(responder=lambda req, _i: (
        cut if req.model == FLASH_MODEL else OK_TEXT))

    def complete(_text: str, response: ChatResponse) -> str | None:
        return '答案被 token 上限截断' if response.finish_reason == 'length' else None

    assert _client(t, model=FLASH_MODEL).ask_text(
        'sys', 'usr', purpose='t', validator=complete) == '一段正文'
    assert [req.model for req in t.calls] == [FLASH_MODEL, PRO_FALLBACK_MODEL]
    assert t.rungs == [DEFAULT_RUNGS[0]] * 2


def test_a_text_helper_can_explicitly_use_one_cheaper_rung():
    """收缩类辅助调用不必先烧 high/medium，但默认创作路径仍走唯一 `LADDER`。"""
    t = FakeTransport.always(OK_TEXT)
    assert _client(t).ask_text(
        'sys', 'usr', purpose='trim', rungs=(('low', False),)) == '一段正文'
    assert t.rungs == [('low', False)]


def test_a_json_helper_can_explicitly_use_one_cheaper_rung():
    """Structured trim keeps its JSON contract without paying the prose ladder."""
    t = FakeTransport.always(OK_JSON)
    got = _client(t).ask_json(
        'sys', 'usr', model_cls=Resp, purpose='trim-json',
        rungs=(('low', False),))
    assert got.voice == {'a': 1}
    assert t.rungs == [('low', False)]


def test_empty_custom_json_ladder_fails_before_budget_cache_or_transport():
    t = FakeTransport.always(OK_JSON)
    budget_calls = []
    client = _client(t, budget_checker=lambda *args, **kwargs: budget_calls.append((args, kwargs)))
    with pytest.raises(ValueError, match='rungs 不能为空'):
        client.ask_json('sys', 'usr', model_cls=Resp, purpose='trim-json', rungs=())
    assert budget_calls == []
    assert t.calls == []


@pytest.mark.parametrize('entry', ['text', 'json'])
def test_multiple_rungs_cannot_restore_automatic_effort_degradation(entry):
    t = FakeTransport.always(OK_JSON if entry == 'json' else OK_TEXT)
    budget_calls = []
    client = _client(
        t, budget_checker=lambda *args, **kwargs: budget_calls.append((args, kwargs)))
    with pytest.raises(ValueError, match='只允许一个显式配置'):
        if entry == 'text':
            client.ask_text(
                'sys', 'usr', purpose='p',
                rungs=(('high', True), ('low', True)))
        else:
            client.ask_json(
                'sys', 'usr', model_cls=Resp, purpose='p',
                rungs=(('high', True), ('low', True)))
    assert budget_calls == [] and t.calls == []


def test_custom_json_rungs_reach_budget_preflight_and_keep_cache_identity():
    cache = ResponseCache(Path(tempfile.mkdtemp()))
    budget_requests = []

    def budget_checker(requests, **_kwargs):
        budget_requests.append(tuple(requests))

    first_transport = FakeTransport.always(OK_JSON)
    first = _client(first_transport, cache=cache, budget_checker=budget_checker)
    first.ask_json(
        'sys', 'usr', model_cls=Resp, purpose='trim-json',
        rungs=(('low', False),))
    assert [(row.reasoning_effort, row.thinking) for row in budget_requests[0]] == [
        ('low', False)]

    second_transport = FakeTransport.always(OK_JSON)
    second = _client(second_transport, cache=cache, budget_checker=budget_checker)
    second.ask_json(
        'sys', 'usr', model_cls=Resp, purpose='trim-json',
        rungs=(('low', False),))
    assert second_transport.calls == []
    assert len(budget_requests) == 2, '预算预检必须早于缓存命中'


def test_an_empty_custom_text_ladder_is_a_call_error():
    with pytest.raises(ValueError, match='rungs 不能为空'):
        _client(FakeTransport.always(OK_TEXT)).ask_text(
            'sys', 'usr', purpose='t', rungs=())


def test_a_truncated_flash_json_falls_back_to_pro_at_the_same_effort():
    """⚠ **截断表现为 JSON 解析失败**（`finish_reason='length'`），不是别的。

    截断**不触发同档重采**（`resample` 恒 False）；旧实现会降 effort，现实现保持
    high 并切 Pro，避免用低档响应静默收口。
    """
    events: list[dict] = []
    ctx = _ctx()
    ctx.observer = events.append
    truncated = ChatResponse(content='{"voice": {"a": 1}, "surface_hab',
                             finish_reason='length', usage=None)

    def responder(req, _i):
        return truncated if req.model == FLASH_MODEL else OK_JSON

    t = FakeTransport(responder=responder)
    out = _client(t, ctx=ctx, model=FLASH_MODEL).ask_json(
        'sys', 'usr', model_cls=Resp, purpose='t')
    assert out.surface_habits == ['h']
    assert [req.model for req in t.calls] == [FLASH_MODEL, PRO_FALLBACK_MODEL]
    assert t.rungs == [DEFAULT_RUNGS[0]] * 2
    assert [e['resample'] for e in _observed(events)] == [False]


def test_a_legal_but_structurally_empty_json_is_rejected_like_a_parse_failure():
    """⚠⚠ **「解析失败」与「合法 JSON 但结构上是空的」是同一类故障**（原型 F-125）。

    实测：某次规划调用拿回 `{"reconstruction_strategy": "…"}`——**0 个输出章**、
    正常结束、4477 token 里推理占 4317。它是合法 JSON，于是一路放行**并进了缓存**；
    旧下游把它摊成 **74 条**派生症状，还附一句「说明 prompt 漂了」，
    盖住了「规划结果为空」这一句真正的事实。
    """
    shell = ChatResponse(content='{"voice": {"a": 1}}', finish_reason='stop', usage=None)
    t = FakeTransport(responder=lambda _r, i: shell if i < 1 else OK_JSON)
    out = _client(t).ask_json('sys', 'usr', model_cls=Resp, purpose='t')
    assert out.surface_habits == ['h']
    # 与①同源（想得多、交得少）⇒ 先同档重采；它**不是**截断，不该靠降档腾预算。
    assert t.rungs == [LADDER[0], LADDER[0]]


def test_each_rung_is_its_own_cache_entry():
    """⚠ `reasoning_effort` **进缓存键**。

    不进的话，降级到第二档时会读回第一档那个空壳——「重试」变成把同一个结果读两遍，
    而那与一次真调用长得一模一样。
    """
    base = dict(model='m', system='s', user='u', json_mode=False, max_tokens=1)
    keys = {cache_key(ChatRequest(reasoning_effort=e, thinking=t, **base)) for e, t in LADDER}
    assert len(keys) == len(LADDER)


def test_cache_identity_tracks_the_request_not_the_worktree():
    """prompt 改动必须 miss；与请求无关的代码/文档身份不进入缓存键。"""
    base = dict(model='m', user='u', reasoning_effort='high', thinking=True,
                json_mode=False, max_tokens=1)
    original = ChatRequest(system='prompt-v1', **base)
    same_request_under_another_worktree = ChatRequest(system='prompt-v1', **base)
    changed_prompt = ChatRequest(system='prompt-v2', **base)
    assert cache_key(original) == cache_key(same_request_under_another_worktree)
    assert cache_key(original) != cache_key(changed_prompt)


def test_cache_key_remains_the_legacy_prefix_of_the_full_request_identity():
    """缓存文件名保持 32 字符兼容；来源账保存同一摘要的完整 64 字符。"""
    req = _req()
    full = request_fingerprint(req)
    assert len(full) == 64
    assert len(cache_key(req)) == 32
    assert cache_key(req) == full[:32]


def test_request_identity_golden_vector_pins_the_legacy_cache_preimage():
    """固定历史前像；规范化任一字节变化都必须显式改裁决。"""
    req = ChatRequest(
        model='deepseek-v4-flash',
        system='你是闭卷改写作家。',
        user='请重写第七章：雨夜重逢。',
        reasoning_effort='high',
        thinking=True,
        json_mode=True,
        max_tokens=96000,
        temperature=0.25,
    )
    assert request_fingerprint(req) == (
        'f6939aa1dc49de9d4e88de75dd62ddc8'
        '5f35f589613b8b3e8618fb5141d6d44b'
    )
    assert cache_key(req) == 'f6939aa1dc49de9d4e88de75dd62ddc8'


# ─────────────────────────────────────────────────────────────────────────────
# 二、缓存：两个开关独立 · 空 content 不写 · 空壳 evict
# ─────────────────────────────────────────────────────────────────────────────

def test_read_and_write_switches_are_independent():
    """⚠ 原型只有一个 `--no-cache`，于是「**重新采样一次，但仍然记账**」表达不出来。

    而那正是 `R-3.11` 要求的那一档：定向重生成的 prompt 与整跑那一批**逐字相同**，
    缓存必然命中 ⇒ **一个字都没重新生成，而它照样打印「已重生成」**。
    """
    d = Path(tempfile.mkdtemp())

    # ① 先用 default 跑一次，缓存里留下一份
    t1 = FakeTransport.always(OK_TEXT)
    _client(t1, cache=ResponseCache(d)).ask_text('s', 'u', purpose='t')
    assert len(t1.calls) == 1 and len(list(d.iterdir())) == 1

    # ② default 重跑：0 次真调用（「参数不变的重跑什么都没跑」）
    t2 = FakeTransport.always(OK_TEXT)
    ctx2 = _ctx()
    _client(t2, ctx=ctx2, cache=ResponseCache(d)).ask_text('s', 'u', purpose='t')
    assert t2.calls == [] and (ctx2.ledger.calls_made, ctx2.ledger.calls_cached) == (0, 1)

    # ③ refresh = **不读但写**：真的重新采样了，而且新的一份仍然落了盘
    t3 = FakeTransport.always(ChatResponse(content='新采样', finish_reason='stop'))
    ctx3 = _ctx(cache=CachePolicy.refresh())
    got = _client(t3, ctx=ctx3, cache=ResponseCache(d)).ask_text('s', 'u', purpose='t')
    assert got == '新采样' and len(t3.calls) == 1
    assert ctx3.ledger.calls_made == 1
    assert ResponseCache(d).get(cache_key(_req())).text == '新采样', 'refresh 没有写回去'

    # ④ disabled = 两个方向一起关：跑了，但盘上那份没被动过
    t4 = FakeTransport.always(ChatResponse(content='又一次', finish_reason='stop'))
    ctx4 = _ctx(cache=CachePolicy.disabled())
    _client(t4, ctx=ctx4, cache=ResponseCache(d)).ask_text('s', 'u', purpose='t')
    assert len(t4.calls) == 1
    assert ResponseCache(d).get(cache_key(_req())).text == '新采样', 'disabled 不该写缓存'


def test_one_call_can_refresh_without_mutating_the_shared_run_policy():
    """并发逐章共享 client；单章的退化重采样不能把整批上下文改成 refresh。"""
    d = Path(tempfile.mkdtemp())
    cache = ResponseCache(d)
    cache.put(cache_key(_req()), OK_TEXT)
    ctx = _ctx(cache=CachePolicy.default())
    transport = FakeTransport.always(
        ChatResponse(content='绕缓存的新采样', finish_reason='stop'))
    client = _client(transport, ctx=ctx, cache=ResponseCache(d))

    got = client.ask_text(
        's', 'u', purpose='单次重采样', cache_policy=CachePolicy.refresh())

    assert got == '绕缓存的新采样'
    assert len(transport.calls) == 1, 'per-call refresh 读回了旧缓存'
    assert ctx.cache.name == 'default', '单章覆盖污染了共享 RunContext'
    assert ResponseCache(d).get(cache_key(_req())).text == '绕缓存的新采样'


def _req(effort: str = 'high', thinking: bool = True) -> ChatRequest:
    # 缓存单元测试用 `_client` 固定 Pro，避免混入模型兜底这根轴。
    return ChatRequest(model=PRO_FALLBACK_MODEL, system='s', user='u',
                       reasoning_effort=effort, thinking=thinking,
                       json_mode=False, max_tokens=DEFAULT_MAX_TOKENS)


# ── 首发独占闸 ───────────────────────────────────────────────────────────────
# 它守的是**顺序**（供应商前缀缓存要等一次请求完成才写得进去），而 `_gate` 守的是
# **并发度**（也就是钱）。三条循环——阶梯 / 重试 / 预热——各测各的，不许合并。


def test_the_first_wire_call_runs_alone_so_the_rest_can_hit_the_provider_prefix_cache():
    """逐章扇出默认并发 32；N 份请求同时上线时共享的 system 前缀谁也命中不了谁。

    ⚠ 断言的是**顺序**不是并发度：首发的 `exit` 必须排在第二个 `enter` 之前，
    否则它根本没有独占过，而「没独占」与「独占了」在最终结果上长得一模一样。
    """
    marks: list[tuple[str, str]] = []
    lock = threading.Lock()

    def responder(req: ChatRequest, _i: int) -> ChatResponse:
        with lock:
            marks.append(('enter', req.user))
        time.sleep(0.02)
        with lock:
            marks.append(('exit', req.user))
        return OK_TEXT

    client = _client(FakeTransport(responder=responder))
    with ThreadPoolExecutor(max_workers=8) as pool:
        list(pool.map(
            lambda n: client.ask_text('s', f'u{n}', purpose='扇出'), range(8)))

    assert marks[0][0] == 'enter'
    assert marks[1] == ('exit', marks[0][1]), \
        f'首发没有独占，第二条是 {marks[1]}——共享前缀会全部 miss'
    assert len(marks) == 16, '有请求没被记到'


def test_a_failing_first_call_still_releases_the_warmup_gate():
    """⚠⚠ 首发失败也必须放行：不放行就是整批死等一个**已经死掉**的请求。

    那比多花一次钱坏得多——它没有任何超时，整跑就停在那里。
    """
    def responder(req: ChatRequest, _i: int):
        return (TransportError('炸了', retryable=False) if req.user == 'boom'
                else OK_TEXT)

    client = _client(FakeTransport(responder=responder))
    with pytest.raises(TransportError):
        client.ask_text('s', 'boom', purpose='首发')

    assert client._warm_done.is_set(), '首发抛异常后闸没放行 ⇒ 后来者会死等'
    with ThreadPoolExecutor(max_workers=4) as pool:
        got = list(pool.map(
            lambda n: client.ask_text('s', f'u{n}', purpose='其余'), range(4)))
    assert got == ['一段正文'] * 4


def test_a_cache_hit_does_not_consume_the_warmup_slot():
    """本地缓存命中走 `_read_cached`，根本进不到 `_send_with_retry`。

    ⇒ 一次全命中的重跑**不会**因为这道闸而串行（也不该消耗那唯一的首发名额）。
    """
    d = Path(tempfile.mkdtemp())
    ResponseCache(d).put(cache_key(_req()), OK_TEXT)
    transport = FakeTransport.always(OK_TEXT)
    client = _client(transport, cache=ResponseCache(d))

    assert client.ask_text('s', 'u', purpose='全命中') == '一段正文'

    assert not transport.calls, '缓存命中却上了线'
    assert client._warmed is False, '缓存命中消耗了首发名额'


def test_warmup_can_be_turned_off_and_then_nothing_is_serialised():
    """`warmup=False` 是给测试留的口，不是产品 flag——但它必须真的关得掉。

    ⚠ 一个关不掉的开关与一个恒真的 flag 同罪：它让「这次跑是怎么发的」说不清。
    """
    client = _client(FakeTransport.always(OK_TEXT), warmup=False)
    client.ask_text('s', 'u', purpose='t')
    assert client._warmed is False and not client._warm_done.is_set()


def test_an_empty_answer_is_never_cached():
    """⚠ 缓存下来只会让重跑**每次都命中同一个空壳**——恢复链会被坏缓存短路。"""
    d = Path(tempfile.mkdtemp())
    t = FakeTransport.empty_until(1, OK_TEXT)
    _client(t, cache=ResponseCache(d)).ask_text('s', 'u', purpose='t')
    assert len(list(d.iterdir())) == 1, '空回答被写进缓存了'


def test_a_structurally_empty_shell_is_evicted_from_the_cache():
    """⭐ **空壳踢出缓存。**

    留着它，下一次不带 `--refresh` 的重跑会原样读回来，而调用方看到的是一模一样的失败
    ——**那不是重试，是把同一个结果读了两遍**。
    ⚠ 这里**不适用**产物那条「修不了的原样留着」（判据 7）：产物留着是给人看的，
    一份坏缓存留着不会被人看见，只会被下一次重跑静默地当成新结果。

    ⚠⚠ **判据方式 2026-08-22 换过一次，不是放宽。** 同档重采上线后，被拒的那份与
    救回来的那份**共用同一个缓存键**（键含 `effort`/`thinking`，同档必然相同）
    ⇒ 「那个键还在不在」不再区分得开「空壳被踢了」与「什么都没写」。
    改成**直接读那个键**：留下的必须是好的那一份。这比数文件名更强——
    它直接断言缓存里不是空壳，而不是绕着推。
    """
    d = Path(tempfile.mkdtemp())
    cache = ResponseCache(d)
    shell = ChatResponse(content='{"voice": {"a": 1}}', finish_reason='stop')
    t = FakeTransport(responder=lambda _r, i: shell if i < 1 else OK_JSON)
    _client(t, cache=cache).ask_json('s', 'u', model_cls=Resp, purpose='t')

    kept = cache.get(cache_key(_json_req(*LADDER[0])))
    assert kept is not None, '救回来的那一份该留着'
    assert 'surface_habits' in kept.content, '空壳还留在缓存里'
    assert len(list(d.iterdir())) == 1, '不该产生第二个 effort 缓存键'


def _json_req(effort: str, thinking: bool, *,
              model: str = PRO_FALLBACK_MODEL) -> ChatRequest:
    """`ask_json` 在某一档上**真的会发出去**的那个请求（缓存键就按它算）。

    ⚠ `json_mode` 跟着 `JSON_OBJECT_MODE` 走，**别写死 `True`**：写死之后这几条
    缓存断言测的就不是「客户端存/取的是同一个键」，而是「客户端恒发 json_object」
    ——那一位是可裁决的（2026-08-22 已关），而缓存键的一致性不是。
    """
    return ChatRequest(model=model, system='s', user='u',
                       reasoning_effort=effort, thinking=thinking,
                       json_mode=JSON_OBJECT_MODE, max_tokens=DEFAULT_MAX_TOKENS)


def test_a_truncated_response_is_never_written_to_the_cache():
    """⭐⭐ **写缓存的时机原来在「判断它可不可用」之前。**

    `_call_keyed` 拿到响应就 `put`，而 `ask_json` 还没解析它。于是四档阶梯各自
    拿回一份截断 JSON 时，盘上留下的是**四份毒缓存**——解析失败那条路
    （唯一没 evict 的那条）走完，四份全留着。
    """
    d = Path(tempfile.mkdtemp())
    truncated = ChatResponse(content='{"voice": {"a": 1}, "surface_hab', finish_reason='length')
    t = FakeTransport(responder=lambda req, _i: (
        truncated if req.model == FLASH_MODEL else OK_JSON))
    _client(t, cache=ResponseCache(d), model=FLASH_MODEL).ask_json(
        's', 'u', model_cls=Resp, purpose='t')

    keys = {p.name for p in d.iterdir()}
    flash_key = cache_key(_json_req(*DEFAULT_RUNGS[0], model=FLASH_MODEL))
    pro_key = cache_key(_json_req(*DEFAULT_RUNGS[0], model=PRO_FALLBACK_MODEL))
    assert f'{flash_key}.json' not in keys, '截断的 Flash 响应进了缓存'
    assert f'{pro_key}.json' in keys, 'Pro 兜底的好响应该留着'


def test_a_poisoned_cache_entry_heals_within_the_very_same_run():
    """⭐⭐ **一份读回来之后才发现不可用的缓存，是一次发现得晚的 miss，不是模型的毛病。**

    实测形态：第一次四档全拿回截断 JSON ⇒ 四份毒缓存。第二次即使传输层保证返回
    正确 JSON，也是 **0 次真调用**并以一模一样的方式失败——而且不带 `--refresh`
    永远走不出来。「重试」退化成把同一个坏结果读第二遍，
    **而那与一次真调用在报告上长得一模一样**。

    ⚠ 光 evict 不重发只能治好**下一次**跑，而这一次仍然会报
    「模型连着四档都吐截断 JSON」——**而事实是缓存坏了、模型一次都没被问到**。
    「派生症状盖住一句话能说清的事实」正是本项目反复付学费的那个形状。
    ⇒ 先检查完所有档位；都毒才从 high 真发一次。
    """
    d = Path(tempfile.mkdtemp())
    poison = ChatResponse(content='{"voice": {"a": 1}, "surface_hab', finish_reason='length')
    cache = ResponseCache(d)
    for e, th in LADDER:
        cache.put(cache_key(_json_req(e, th)), poison)
    assert len(list(d.iterdir())) == len(LADDER)

    ctx = _ctx()
    t = FakeTransport.always(OK_JSON)
    out = _client(t, ctx=ctx, cache=ResponseCache(d)).ask_json(
        's', 'u', model_cls=Resp, purpose='t')
    assert out.surface_habits == ['h']
    assert t.rungs == [('high', True)], '每一档缓存都坏后，只该从第一档真发一次'
    assert (ctx.ledger.calls_made, ctx.ledger.calls_cached) == (1, len(LADDER))
    # 毒的那份走了，好的那份留下
    assert cache.get(cache_key(_json_req('high', True))).text == OK_JSON.text


def test_a_valid_pro_json_cache_prevents_any_flash_network_call():
    """已有合格 Pro 兜底缓存时，Flash miss 不能触发重复付费。"""
    d = Path(tempfile.mkdtemp())
    cache = ResponseCache(d)
    cache.put(cache_key(_json_req(*DEFAULT_RUNGS[0], model=PRO_FALLBACK_MODEL)), OK_JSON)
    ctx = _ctx()
    events: list[dict] = []
    ctx.observer = events.append
    transport = FakeTransport.always(OK_JSON)

    out = _client(transport, ctx=ctx, cache=ResponseCache(d),
                  model=FLASH_MODEL).ask_json(
        's', 'u', model_cls=Resp, purpose='t')

    assert out.surface_habits == ['h']
    assert transport.calls == []
    assert (ctx.ledger.calls_made, ctx.ledger.calls_cached) == (0, 1)
    switched, = _observed(events, 'llm_model_fallback')
    assert switched['reason'] == 'cached_fallback'


def test_a_valid_pro_text_cache_prevents_any_flash_network_call():
    d = Path(tempfile.mkdtemp())
    cache = ResponseCache(d)
    pro = ChatRequest(model=PRO_FALLBACK_MODEL, system='s', user='u',
                      reasoning_effort='high', thinking=True, json_mode=False,
                      max_tokens=DEFAULT_MAX_TOKENS)
    cache.put(cache_key(pro), OK_TEXT)
    ctx = _ctx()
    transport = FakeTransport.always(OK_TEXT)

    assert _client(transport, ctx=ctx, cache=ResponseCache(d),
                   model=FLASH_MODEL).ask_text(
        's', 'u', purpose='t') == '一段正文'
    assert transport.calls == []
    assert (ctx.ledger.calls_made, ctx.ledger.calls_cached) == (0, 1)


def test_a_truncated_flash_response_falls_back_without_double_sending():
    """截断不做同档重采；Flash 一次被拒后只发一次同档 Pro。"""
    truncated = ChatResponse(content='{"voice": {"a": 1}, "surface_hab', finish_reason='length')
    t = FakeTransport(responder=lambda req, _i: (
        truncated if req.model == FLASH_MODEL else OK_JSON))
    out = _client(t, model=FLASH_MODEL).ask_json(
        's', 'u', model_cls=Resp, purpose='t')
    assert out.surface_habits == ['h']
    assert [req.model for req in t.calls] == [FLASH_MODEL, PRO_FALLBACK_MODEL]
    assert t.rungs == [DEFAULT_RUNGS[0]] * 2


def test_a_half_written_cache_file_reads_as_a_miss_not_as_a_failure():
    """⚠ 半截缓存抛异常的话，那个异常会被当成「这次调用失败」——
    **把一次已经付过钱的结果变成一次重跑**。⇒ 读不出来一律当没有。"""
    d = Path(tempfile.mkdtemp())
    (d / f'{cache_key(_req())}.json').write_bytes('{"content": "半截'.encode('utf-8'))
    t = FakeTransport.always(OK_TEXT)
    assert _client(t, cache=ResponseCache(d)).ask_text('s', 'u', purpose='t') == '一段正文'
    assert len(t.calls) == 1


@pytest.mark.parametrize('stale', ['{}\n', '[]\n', '"abc"\n', '{"没这个字段": 1}\n'])
def test_a_structurally_stale_cache_payload_reads_as_a_miss_too(stale):
    """⭐ **合法 JSON 不等于读得回 `ChatResponse`。**

    `{}` 会让 `ChatResponse(**raw)` 抛 `TypeError`（少必填参数），而那个异常与半截文件
    抛的那个下场一样：被当成「这次调用失败」，把一次已经付过钱的结果变成一次重跑。
    缓存的形态只要改过一次，盘上就会留着一批这样的旧文件——
    它们该是 miss，不该是故障。
    """
    d = Path(tempfile.mkdtemp())
    (d / f'{cache_key(_req())}.json').write_bytes(stale.encode('utf-8'))
    t = FakeTransport.always(OK_TEXT)
    assert _client(t, cache=ResponseCache(d)).ask_text('s', 'u', purpose='t') == '一段正文'
    assert len(t.calls) == 1


def test_the_memory_only_cache_is_a_real_cache():
    """⭐⭐ `dir=None` 曾经是「一个字都不缓存」，而 docstring 写着「语义完全一样」。

    `_path` 返回 `None` ⇒ `get` 恒 `None` · `put` 恒 `False` · `evict` 什么也不做。
    而 **`LlmClient` 的默认缓存恰好就是这一档**：于是「四档各自成键」「空壳踢出缓存」
    「命中记进 `calls_cached`」这些行为在默认配置下**一条都不成立**，
    却没有任何测试会红——它们全都在断言一个从不发生的事。
    """
    ctx = _ctx()
    t = FakeTransport.always(OK_TEXT)
    c = _client(t, ctx=ctx)                    # ← cache=None ⇒ 默认那一档
    assert c.ask_text('s', 'u', purpose='t') == '一段正文'
    assert c.ask_text('s', 'u', purpose='t') == '一段正文'
    assert len(t.calls) == 1, '第二次又发了一遍——默认那一档根本没在缓存'
    assert (ctx.ledger.calls_made, ctx.ledger.calls_cached) == (1, 1)

    # 三个方法逐条对齐盘上那一份：空 content 不写 · evict 真的删得掉
    mem = ResponseCache()
    assert mem.put('k', ChatResponse(content='', finish_reason='stop')) is False
    assert mem.put('k', OK_TEXT) is True and mem.get('k') is OK_TEXT
    mem.evict('k')
    assert mem.get('k') is None


def test_the_memory_cache_does_not_leak_between_two_runs():
    """作用域是**实例**：一个 `LlmClient` 一份。两次跑 = 两份 = 零串扰。

    同 `RunContext` 那条纪律——进程级的共享状态正是原型被迫用子进程的理由。
    """
    t1, t2 = FakeTransport.always(OK_TEXT), FakeTransport.always(OK_TEXT)
    _client(t1).ask_text('s', 'u', purpose='t')
    _client(t2).ask_text('s', 'u', purpose='t')
    assert len(t1.calls) == 1 and len(t2.calls) == 1, '两次跑共用了同一份内存缓存'


# ─────────────────────────────────────────────────────────────────────────────
# 三、`require` 从模型派生 —— 判据 39 从纪律变成结构
# ─────────────────────────────────────────────────────────────────────────────

def test_require_is_derived_from_the_response_model():
    """⚠⚠ **这一个默认值就是判据 39 的全部实现。**

    原型把 `require=()` 设成默认值 ⇒ **漏传不报错，只是静默停掉一条 HARD 闸门**。
    派生之后，「记得声明全部必需顶层键」这件事**结构上不可能忘**。
    """
    from novel.llm.prompts import required_top_level
    assert set(required_top_level(Resp)) == {'voice', 'surface_habits'}
    assert 'note' not in required_top_level(Resp), '可选字段不该进 require'

    # 少一个必需键 ⇒ 走完阶梯仍然失败，而且报得出缺的是哪一个
    shell = ChatResponse(content='{"voice": {"a": 1}}', finish_reason='stop')
    with pytest.raises(LlmFailed, match='surface_habits'):
        _client(FakeTransport.always(shell)).ask_json('s', 'u', model_cls=Resp, purpose='t')


def test_ask_json_returns_a_validated_model_not_a_raw_dict():
    """⭐⭐ `model_cls` 原来**只**被拿去派生 `require`，一次 `model_validate` 都没跑过。

    `store.py` 那条主杠杆写着「只有 `store.read` 返回带类型的模型 ⇒ 绕过意味着
    同时放弃校验和类型」。`ask_json` 是不可信数据进运行时的**另一扇门**，得守同一条。
    校验完再退回 dict 等于让调用方读**未校验**的那一份（pydantic 会强制转换、
    填默认值、走别名）——同一份响应的两种表示，而两种表示必然分家。
    """
    out = _client(FakeTransport.always(OK_JSON)).ask_json(
        's', 'u', model_cls=Resp, purpose='t')
    assert isinstance(out, Resp)
    assert out.voice == {'a': 1} and out.surface_habits == ['h']
    assert out.note is None, '可选字段该由模型填默认值'


@pytest.mark.parametrize('bad', [
    {'voice': 'not-a-dict', 'surface_habits': ['h']},      # 类型错
    {'voice': {'a': 1}, 'surface_habits': [1]},            # 元素类型错
])
def test_a_wrongly_shaped_value_walks_the_ladder_instead_of_being_accepted(bad):
    """⭐ `require` 只查真值，于是 `Resp.model_validate` 明确拒绝的两种形状被一路放行。

    ⚠ 这也意味着 `extra='forbid'` 那类**载体守卫一次都没响过**——
    契约层为它们写的每一条理由，在这条路上都不成立。
    形状错与「结构上是空的」是同一类故障：走同一条阶梯，降一档再试。
    """
    t = FakeTransport(responder=lambda _r, i:
                      ChatResponse(content=json.dumps(bad), finish_reason='stop')
                      if i < 1 else OK_JSON)
    out = _client(t).ask_json('s', 'u', model_cls=Resp, purpose='t')
    assert out.surface_habits == ['h']
    assert t.rungs == [LADDER[0], LADDER[0]], '形状错的那一份被放行了'


def test_a_legitimate_false_or_zero_is_not_mistaken_for_a_missing_key():
    """⭐ **`False` 与 `0` 不叫空。**

    原来这里写的是 `not data.get(k)`，于是一个合法的 `False`（或 `0`、或一个刻意
    为空的集合语义）会被报成「模型没给这个键」——**白走四档阶梯、花四次钱，
    去救一个根本没坏的响应**。
    """
    class Flags(BaseModel):
        flag: bool
        count: int

    t = FakeTransport.always(ChatResponse(
        content='{"flag": false, "count": 0}', finish_reason='stop'))
    out = _client(t).ask_json('s', 'u', model_cls=Flags, purpose='t')
    assert (out.flag, out.count) == (False, 0)
    assert len(t.calls) == 1, '一档就该成功，而它走了阶梯'


def test_an_empty_collection_is_still_treated_as_the_f125_shell():
    """反过来这道闸**不能删**：F-125 那个形态是**合法 JSON**，`model_validate` 也放行。

    实测：`{"reconstruction_strategy": "…"}`——**0 个输出章**、正常结束、
    4477 token 里推理占 4317；下游把它摊成 74 条派生症状。
    要让 pydantic 自己拦住它，响应模型得声明 `Field(min_length=1)`——
    而「记得声明」正是判据 39 已经证明过靠不住的那种纪律。
    """
    shell = ChatResponse(content='{"voice": {}, "surface_habits": []}', finish_reason='stop')
    t = FakeTransport(responder=lambda _r, i: shell if i < 1 else OK_JSON)
    out = _client(t).ask_json('s', 'u', model_cls=Resp, purpose='t')
    assert out.surface_habits == ['h']
    assert t.rungs == [LADDER[0], LADDER[0]], '空集合被当成可用的响应放行了'


def test_an_explicit_require_may_only_narrow():
    """显式传只许**收窄**。放宽等于给这个调用点开一个「它特殊」的口子，
    而那种口子会长成一句注释，然后没人再回来看它。"""
    t = FakeTransport.always(OK_JSON)
    _client(t).ask_json('s', 'u', model_cls=Resp, purpose='t', require=('voice',))   # 收窄，OK
    with pytest.raises(ValueError, match='收窄'):
        _client(t).ask_json('s', 'u', model_cls=Resp, purpose='t', require=('voice', '编的'))


def test_every_ask_json_call_site_declares_its_model():
    """全仓每个 `ask_json(` 调用点都必须传 `model_cls=`。

    ⚠ 关键字必填已经在签名上拦住了漏传（下一条测试钉着），这条查的是另一件事：
    **有没有人把它包了一层**（`def my_ask(...): return client.ask_json(..., model_cls=X)`），
    从而让某个调用点绕开「声明必需顶层键」这条纪律。
    """
    import ast
    import os
    root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    sites = []
    for dirpath, dirnames, filenames in os.walk(os.path.join(root, 'novel')):
        dirnames[:] = [d for d in dirnames if d != '__pycache__']
        for fn in filenames:
            if not fn.endswith('.py'):
                continue
            p = os.path.join(dirpath, fn)
            tree = ast.parse(open(p, encoding='utf-8').read(), filename=p)
            for n in ast.walk(tree):
                if (isinstance(n, ast.Call) and isinstance(n.func, ast.Attribute)
                        and n.func.attr == 'ask_json'):
                    sites.append((os.path.relpath(p, root), n.lineno,
                                  {k.arg for k in n.keywords}))
    if not sites:
        pytest.skip('全仓还没有 `ask_json(` 调用点（第一个在 C 期 stage3）'
                    '——**这一条现在没在查**，不是「查过没事」')
    bad = [(f, ln) for f, ln, kws in sites if 'model_cls' not in kws]
    assert not bad, f'这些调用点没传 model_cls：{bad}'


def test_ask_json_cannot_be_called_without_a_model():
    """`model_cls` 是**关键字必填**：没有它就没有 `require`，而没有 `require`
    就等于把那条 HARD 闸门静默关掉。"""
    import inspect
    p = inspect.signature(LlmClient.ask_json).parameters['model_cls']
    assert p.default is inspect.Parameter.empty and p.kind is inspect.Parameter.KEYWORD_ONLY


# ─────────────────────────────────────────────────────────────────────────────
# 四、传输层重试 —— 与同档重采 / 模型兜底是**独立**循环
# ─────────────────────────────────────────────────────────────────────────────

def test_a_retryable_transport_error_does_not_consume_a_ladder_rung():
    """⚠⚠ 混成一条会让网络抖动消耗内容采样；模型兜底也救不了连接被重置。"""
    def responder(_req, i):
        return TransportError('429 Too Many Requests', retryable=True, status=429) if i < 2 \
            else OK_TEXT

    t = FakeTransport(responder=responder)
    ctx = _ctx()
    assert _client(t, ctx=ctx).ask_text('s', 'u', purpose='t') == '一段正文'
    assert t.rungs == [('high', True)] * 3, '重试把阶梯的档位吃掉了'
    assert ctx.ledger.calls_made == 3, '发了 3 次而账上不是 3——见「逐次记录发送事实」那条'


def test_the_transport_layer_owns_no_retries_of_its_own():
    """⭐⭐ **重试的唯一所有者是 `client.py`。**

    openai SDK 自带重试，默认 **2**（＝一次 `create()` 最多 3 次 HTTP）。
    叠上 `_send_with_retry` 的「首发 + 3 次重试」，**一次调用最多打出 3 × 4 = 12 次 HTTP**
    ——而账本上只会记 1 次。两层各退各的避、谁也不知道对方在退，限流时正好是最坏的形状。
    而两个模块的 docstring 都写着这一层「不管重试」。
    """
    import inspect
    from novel.llm.transport import DeepSeekTransport
    src = inspect.getsource(DeepSeekTransport._sdk)
    assert 'max_retries=0' in src, \
        'SDK 那一层的重试没关——它会与 `_send_with_retry` 相乘，而账本看不见'


def test_every_wire_attempt_gets_its_own_ledger_row():
    """⭐⭐ **失败的那几次也要记，而且计进 `calls_made`。**

    原来只在整轮重试**成功之后**记一行：传输层实际发了 3 次而账上只有 1 次；
    整轮失败则**一行都没有**。于是「限流限了半天」与「一次就过」在账本上同形，
    「彻底失败」与「什么都没干」也同形——而这几对的下一步动作完全不同。
    `calls_made` 的契约原文是「**真发出去**的调用数」：一次 429 确实上过线，
    一次超时对面很可能已经生成并计费了。
    """
    d = Path(tempfile.mkdtemp())
    ctx = RunContext.open(_book(), '123', 'stage3.style', paid=True,
                          ledger=CostLedger(path=d / 'ledger.jsonl'))
    events = []
    ctx.observer = events.append
    t = FakeTransport(responder=lambda _r, i:
                      TransportError('429', retryable=True, status=429) if i < 2 else OK_TEXT)
    progress = RequestProgress(chapter=7, index=2, total=5)
    _client(t, ctx=ctx, backoff_base=0.25).ask_text(
        's', 'u', purpose='t', progress=progress)

    rows = [json.loads(ln) for ln in (d / 'ledger.jsonl').read_text('utf-8').splitlines()]
    assert [r['outcome'] for r in rows] == ['error', 'error', 'ok']
    assert [r['status'] for r in rows] == [429, 429, None]
    assert rows[0]['prompt_tokens'] is None, '失败那次没有任何读数，不许编一个 0'
    assert rows[2]['prompt_tokens'] == 10
    assert rows[0]['prompt_cache_hit_tokens'] is None
    assert rows[2]['prompt_cache_hit_tokens'] is None, '供应商没给不能记成 0'
    assert rows[0]['reasoning_effort'] == 'high' and rows[0]['thinking'] is True
    assert rows[2]['json_mode'] is False
    assert rows[2]['max_tokens'] == DEFAULT_MAX_TOKENS
    assert rows[0]['finish_reason'] is None and rows[2]['finish_reason'] == 'stop'
    assert [r['attempt'] for r in rows] == [1, 2, 3]
    assert {r['max_attempts'] for r in rows} == {4}
    assert [r['error_kind'] for r in rows] == ['rate_limit', 'rate_limit', None]
    assert [r['retryable'] for r in rows] == [True, True, None]
    assert [r['will_retry'] for r in rows] == [True, True, False]
    assert [r['backoff_ms'] for r in rows] == [250, 500, None]
    assert all(isinstance(r['latency_ms'], int) and r['latency_ms'] >= 0 for r in rows)
    assert [(r['chapter'], r['chapter_index'], r['chapter_total']) for r in rows] == [
        (7, 2, 5), (7, 2, 5), (7, 2, 5),
    ]
    finished = [event for event in events if event['event'] == 'llm_attempt_finished']
    assert [(event['attempt'], event['state'], event['chapter']) for event in finished] == [
        (1, 'error', 7), (2, 'error', 7), (3, 'ok', 7),
    ]
    assert all(event['sid'] == '123' and event['node'] == 'stage3.style'
               for event in events)
    assert ctx.ledger.calls_made == 3


def test_transport_errors_have_stable_timeout_and_http_classifications():
    class ApiTimeoutError(Exception):
        pass

    class BadRequest(Exception):
        status_code = 400

    timeout = _classify(ApiTimeoutError('read timed out'))
    bad = _classify(BadRequest('bad payload'))
    assert (timeout.kind, timeout.retryable, timeout.status) == ('timeout', True, None)
    assert (bad.kind, bad.retryable, bad.status) == ('http_error', False, 400)


def test_an_inflight_attempt_emits_periodic_heartbeats_with_chapter_progress():
    events = []
    in_flight = threading.Event()

    def emit(event, **fields):
        events.append({'event': event, **fields})
        if fields.get('state') == 'in_flight':
            in_flight.set()

    heartbeat = AttemptHeartbeat(
        emit, purpose='stage5.write·ch3', attempt=1, max_attempts=4,
        progress=RequestProgress(chapter=3, index=1, total=2), interval=0.005)
    heartbeat.start()
    assert in_flight.wait(1), '在途请求没有按间隔发心跳'
    heartbeat.finish(outcome='ok')

    assert events[0]['state'] == 'started'
    assert any(event['state'] == 'in_flight' for event in events)
    assert events[-1]['event'] == 'llm_attempt_finished'
    assert (events[-1]['chapter'], events[-1]['chapter_index'],
            events[-1]['chapter_total']) == (3, 1, 2)


def test_a_run_that_fails_outright_still_says_it_spent_something():
    """整轮失败原来 `calls_made == 0`——与「什么都没干」一模一样，而钱可能已经花了。"""
    ctx = _ctx()
    t = FakeTransport(responder=lambda _r, _i: TransportError('503', retryable=True, status=503))
    with pytest.raises(LlmFailed):
        _client(t, ctx=ctx, max_retries=3).ask_text('s', 'u', purpose='t')
    assert len(t.calls) == 4 and ctx.ledger.calls_made == 4


def test_a_non_retryable_transport_error_is_not_retried_at_all():
    """4xx 里除了 429 一律**不重试**：那是我们自己发错了（key 不对、参数不合法），
    重试只会把同一个错误再犯三遍，而**每一遍都可能计费**。"""
    t = FakeTransport(responder=lambda _r, _i: TransportError('400 Bad Request',
                                                             retryable=False, status=400))
    with pytest.raises(TransportError):
        _client(t).ask_text('s', 'u', purpose='t')
    assert len(t.calls) == 1


def test_retries_are_bounded():
    t = FakeTransport(responder=lambda _r, _i: TransportError('503', retryable=True, status=503))
    with pytest.raises(LlmFailed):
        _client(t, max_retries=2).ask_text('s', 'u', purpose='t')
    assert len(t.calls) == 3, '重试次数 = max_retries + 1 次首发'


# ─────────────────────────────────────────────────────────────────────────────
# 五、成本账
# ─────────────────────────────────────────────────────────────────────────────

def test_the_ledger_separates_real_calls_from_cache_hits():
    """⚠⚠ 只数真调用，会让「参数不变的重跑什么都没跑」（`Q-11` / `R-12.4` 要求
    **显式说出来**的那条语义）与「什么都没干」在报告上**同形**。"""
    d = Path(tempfile.mkdtemp())
    ctx = _ctx()
    c = _client(FakeTransport.always(OK_TEXT), ctx=ctx, cache=ResponseCache(d))
    c.ask_text('s', 'u', purpose='t')
    c.ask_text('s', 'u', purpose='t')          # 第二次命中缓存
    assert (ctx.ledger.calls_made, ctx.ledger.calls_cached) == (1, 1)
    u = ctx.ledger.usage()
    assert u is not None and (u.calls_made, u.calls_cached) == (1, 1)


def test_provider_prompt_cache_counts_keep_null_distinct_from_zero():
    d = Path(tempfile.mkdtemp())
    ctx = RunContext.open(_book(), '123', 'stage3.style', paid=True,
                          ledger=CostLedger(path=d / 'ledger.jsonl'))
    response = ChatResponse(
        content='一段正文', finish_reason='stop', model='deepseek-v4-pro',
        usage={
            'prompt_tokens': 10,
            'prompt_cache_hit_tokens': 0,
            'prompt_cache_miss_tokens': 10,
            'completion_tokens': 5,
        },
    )
    _client(FakeTransport.always(response), ctx=ctx).ask_text('s', 'u', purpose='cache-accounting')
    row = json.loads((d / 'ledger.jsonl').read_text('utf-8').splitlines()[0])
    assert row['prompt_cache_hit_tokens'] == 0
    assert row['prompt_cache_miss_tokens'] == 10


def test_a_run_with_no_calls_has_no_usage_key_at_all():
    """0 成本节点是**键不在**，不是一份全 0 的 usage。"""
    assert CostLedger().usage() is None


def test_concurrent_chapter_usage_is_attributed_by_chapter_not_completion_order():
    ledger = CostLedger()
    common = dict(node='stage5.write', sid='book', model='m', code='c',
                  cache_policy='default')
    ledger.record(
        **common, cached=False, prompt_tokens=20, completion_tokens=200,
        progress=RequestProgress(chapter=2, index=2, total=2))
    ledger.record(
        **common, cached=False, prompt_tokens=10, completion_tokens=100,
        progress=RequestProgress(chapter=1, index=1, total=2))
    first = ledger.chapter_usage(1)
    second = ledger.chapter_usage(2)
    assert first and (first.calls_made, first.prompt_tokens, first.completion_tokens) == (1, 10, 100)
    assert second and (second.calls_made, second.prompt_tokens, second.completion_tokens) == (1, 20, 200)
    ledger.commit_chapter(1)
    with pytest.raises(ProvenanceRefused, match='already claimed'):
        ledger.commit_chapter(1)


def _append_ledger_rows(path: str, worker: int, count: int) -> None:
    ledger = CostLedger(path=Path(path))
    for index in range(count):
        ledger.record(
            node='stage5.write', sid=f'book-{worker}', model='m', code='c',
            cached=False, cache_policy='default', purpose=f'{worker}:{index}',
            prompt_tokens=1, completion_tokens=1,
        )


def test_different_novel_processes_append_whole_ledger_rows():
    directory = Path(tempfile.mkdtemp())
    path = directory / 'ledger.jsonl'
    mp = multiprocessing.get_context('spawn')
    processes = [
        mp.Process(target=_append_ledger_rows, args=(str(path), worker, 20))
        for worker in range(2)
    ]
    for process in processes:
        process.start()
    for process in processes:
        process.join(20)
        assert process.exitcode == 0
    rows = [json.loads(line) for line in path.read_text('utf-8').splitlines()]
    assert len(rows) == 40
    assert {row['sid'] for row in rows} == {'book-0', 'book-1'}


# ─────────────────────────────────────────────────────────────────────────────
# 六、配置：不在 import 期跑 · 结构上不会打印密钥
# ─────────────────────────────────────────────────────────────────────────────

def test_the_api_key_can_never_be_printed_by_accident():
    """⚠ 密钥泄漏从来不是「有人故意打印它」，是**一条异常消息里带上了整个 config 对象**。

    ⇒ 做成形状：`api_key` 的 `repr` 关掉，`Config.__repr__` 只说设了没设。
    """
    cfg = Config(api_key='sk-super-secret-value')
    assert 'sk-super-secret-value' not in repr(cfg)
    assert 'sk-super-secret-value' not in str(cfg)
    assert 'sk-super-secret-value' not in f'{cfg}'
    assert '<set>' in repr(cfg) and '<unset>' in repr(Config())


def test_proxy_credentials_can_never_be_printed_by_accident():
    cfg = Config(proxy_url='http://proxy-user:proxy-secret@127.0.0.1:7890')
    shown = repr(cfg)
    assert 'proxy-user' not in shown and 'proxy-secret' not in shown
    assert 'proxy_url=<set>' in shown and 'proxy_url=<unset>' in repr(Config())


def test_load_config_reads_the_explicit_deepseek_proxy(tmp_path):
    dotenv = tmp_path / '.env'
    dotenv.write_text('DEEPSEEK_PROXY=http://127.0.0.1:7890\n', encoding='utf-8')
    assert load_config(env={}, dotenv_path=dotenv).proxy_url == 'http://127.0.0.1:7890'


def test_malformed_proxy_is_a_secret_safe_hard_error():
    with pytest.raises(ValueError, match='DEEPSEEK_PROXY') as caught:
        Config(proxy_url='not-a-url-with-secret')
    assert 'not-a-url-with-secret' not in str(caught.value)


def test_transport_passes_the_explicit_proxy_to_the_sdk(monkeypatch):
    import openai
    from novel.llm.transport import DeepSeekTransport

    seen = {}

    def fake_http_client(**kwargs):
        seen['http'] = kwargs
        return object()

    def fake_openai(**kwargs):
        seen['sdk'] = kwargs
        return object()

    monkeypatch.setattr(openai, 'DefaultHttpxClient', fake_http_client)
    monkeypatch.setattr(openai, 'OpenAI', fake_openai)
    cfg = Config(api_key='synthetic', proxy_url='http://127.0.0.1:7890', timeout=123)
    DeepSeekTransport(cfg)._sdk()

    assert seen['http'] == {'proxy': cfg.proxy_url, 'timeout': 123}
    assert seen['sdk']['http_client'] is not None
    assert seen['sdk']['max_retries'] == 0


def test_transport_doctor_uses_the_same_proxy_and_redacts_its_credentials(monkeypatch):
    """网络诊断可以复现 Agent 的传输面，但错误输出不能泄露代理口令。"""
    import openai
    from novel.llm.transport import probe_transport

    secret_proxy = 'http://proxy-user:proxy-secret@127.0.0.1:7890'
    seen: dict[str, object] = {}

    class ConnectError(OSError):
        pass

    class BrokenClient:
        def get(self, url):
            seen['url'] = url
            raise ConnectError(f'TLS EOF while using {secret_proxy}')

        def close(self):
            seen['closed'] = True

    def build(**kwargs):
        seen['kwargs'] = kwargs
        return BrokenClient()

    monkeypatch.setattr(openai, 'DefaultHttpxClient', build)
    result = probe_transport(Config(proxy_url=secret_proxy, timeout=17))

    assert result.reached_http is False
    assert result.kind == 'connection'
    assert seen['kwargs'] == {'timeout': 17, 'proxy': secret_proxy}
    assert seen['url'] == 'https://api.deepseek.com/models'
    assert seen['closed'] is True
    assert secret_proxy not in result.detail
    assert 'proxy-user' not in result.detail and 'proxy-secret' not in result.detail


def test_load_config_does_not_touch_the_process_environment():
    """⚠ 原型用 `os.environ.setdefault` 把 `.env` 灌进进程全局，于是「这次跑用的是哪个 key」
    **取决于此前有没有别的模块先 import 过它**。这里读进来就用，全局一个字节不动。"""
    import os
    d = Path(tempfile.mkdtemp())
    (d / '.env').write_text('DEEPSEEK_API_KEY=from-dotenv\nDEEPSEEK_MODEL=m2\n', encoding='utf-8')
    before = dict(os.environ)
    cfg = load_config(env={}, dotenv_path=d / '.env')
    assert cfg.api_key == 'from-dotenv' and cfg.model == 'm2'
    assert dict(os.environ) == before, 'load_config 改了 os.environ'


def test_a_real_env_var_beats_the_dotenv_file():
    d = Path(tempfile.mkdtemp())
    (d / '.env').write_text('DEEPSEEK_MODEL=from-file\n', encoding='utf-8')
    assert load_config(env={'DEEPSEEK_MODEL': 'from-env'},
                       dotenv_path=d / '.env').model == 'from-env'


def test_a_malformed_number_is_a_hard_error_not_a_silent_default():
    """⚠ **不静默兜底。** `DEEPSEEK_TIMEOUT=6O` 里那是个字母 O，
    兜底成默认值会让它一路跑到超时重试——而写错一个数字比不写更危险。"""
    with pytest.raises(ValueError, match='DEEPSEEK_TIMEOUT'):
        load_config(env={'DEEPSEEK_TIMEOUT': '6O'}, dotenv_path=Path('/nonexistent'))


@pytest.mark.parametrize('env,var', [
    ({'DEEPSEEK_MAX_RETRIES': '-1'}, 'DEEPSEEK_MAX_RETRIES'),   # 一次都不发，而调用方以为发过了
    ({'NOVEL_CONCURRENCY': '0'}, 'NOVEL_CONCURRENCY'),          # 信号量取 0 ⇒ 整跑死锁
    ({'NOVEL_CONCURRENCY': '-4'}, 'NOVEL_CONCURRENCY'),
    ({'NOVEL_CONCURRENCY': '33'}, 'NOVEL_CONCURRENCY'),         # 用户裁决的逐章硬上限是 32
    ({'DEEPSEEK_BACKOFF_BASE': '-2'}, 'DEEPSEEK_BACKOFF_BASE'),
    ({'DEEPSEEK_TIMEOUT': '-5'}, 'DEEPSEEK_TIMEOUT'),
    ({'DEEPSEEK_TIMEOUT': '0'}, 'DEEPSEEK_TIMEOUT'),
    ({'DEEPSEEK_TIMEOUT': 'inf'}, 'DEEPSEEK_TIMEOUT'),          # float('inf') 转得过去
    ({'DEEPSEEK_TIMEOUT': 'nan'}, 'DEEPSEEK_TIMEOUT'),
])
def test_an_out_of_range_config_number_is_refused_by_the_name_people_typed(env, var):
    """⭐ `_num` 原来**只**查「能不能转成数字」，不查范围。

    于是负重试（一次都不发，而调用方以为发过了）· `concurrency=0`（信号量取 0，
    整跑当场死锁——今天靠 `client.py` 里的 `max(1, …)` 兜着，而那是在**另一个文件**里
    替配置擦屁股）· 负超时 / `nan` / `inf`，全都能一路走到发请求那一刻才发作，
    **而那时的报错说的是别的事**。

    ⚠ 报错必须点**人敲的那个名字**（环境变量），不是字段名——照着字段名去改，
    改半天不生效。
    """
    with pytest.raises(ValueError, match=var):
        load_config(env=env, dotenv_path=Path('/nonexistent'))


def test_a_hand_built_config_is_checked_too():
    """闸在 `__post_init__` 不在 `load_config`：C 期手搭一个 `Config` 也得拦得住。"""
    with pytest.raises(ValueError, match='NOVEL_CONCURRENCY'):
        Config(concurrency=0)
    with pytest.raises(ValueError, match='NOVEL_CONCURRENCY'):
        Config(concurrency=33)
    with pytest.raises(ValueError, match='DEEPSEEK_MODEL'):
        Config(model='')
    with pytest.raises(ValueError, match='DEEPSEEK_THINKING'):
        Config(thinking_mode='max')


def test_the_thinking_mode_env_var_reaches_config_and_defaults_to_on():
    """A/B 的思考轴走 `DEEPSEEK_THINKING`；未设 = `on` = 现行阶梯，零行为变化。"""
    assert load_config(env={}, dotenv_path=Path('/nonexistent')).thinking_mode == 'on'
    assert load_config(env={'DEEPSEEK_THINKING': 'off'},
                       dotenv_path=Path('/nonexistent')).thinking_mode == 'off'


@pytest.mark.parametrize('raw', ['Off', 'ON', 'max', '1', 'true'])
def test_a_malformed_thinking_mode_is_a_hard_error_named_by_the_env_var(raw):
    """⚠ 不静默兜底：`DEEPSEEK_THINKING=Off` 兜成默认值会把一次 A/B **跑成另一个臂**，
    而账本里看不出来是拼写错了——报错必须点人敲的那个名字。"""
    with pytest.raises(ValueError, match='DEEPSEEK_THINKING'):
        load_config(env={'DEEPSEEK_THINKING': raw}, dotenv_path=Path('/nonexistent'))


def test_the_client_defaults_come_from_config_not_from_a_second_copy():
    """⭐ `config.py` 抬头写着「默认值都在这里，别在调用点再写一份」，而调用点写了第二份。

    ⚠ 两份数分家时**不会报错**——它只是让「这次跑到底用的哪个并发度」
    有两个都说得通的答案，而报告只会显示其中一个。
    """
    import inspect

    from novel import config as cfg
    p = inspect.signature(LlmClient.__init__).parameters
    assert p['max_retries'].default == cfg.DEFAULT_MAX_RETRIES
    assert p['backoff_base'].default == cfg.DEFAULT_BACKOFF_BASE
    assert p['concurrency'].default == cfg.DEFAULT_CONCURRENCY
    # `model=''` 走的是「没显式传」那条，落到同一个常量上
    assert LlmClient(_ctx(), FakeTransport.always(OK_TEXT)).model == cfg.DEFAULT_MODEL

    # 只看**代码**，注释里可以复述那几个旧字面量（它们是解释，不是第二个落点）
    code = '\n'.join(ln.split('#')[0] for ln in
                     inspect.getsource(LlmClient.__init__).splitlines())
    for literal in ("'deepseek-v4-pro'", '= 3,', '= 2.0,', '= 2,'):
        assert literal not in code, f'调用点又写了一份默认值：{literal}'


def test_dotenv_parsing_handles_the_shapes_people_actually_write():
    got = parse_dotenv('# 注释\n\nA=1\nB = "two"\nC=\'three\'\nD=has=equals\n没有等号\n')
    assert got == {'A': '1', 'B': 'two', 'C': 'three', 'D': 'has=equals'}


def test_config_is_not_loaded_at_import_time():
    """⚠ 原型的 `_load_dotenv()` 是**模块级单例**，`import` 那一刻就跑了——
    那是它不得不把每个节点跑成子进程的五条理由之一（已登记退役）。"""
    import ast
    import inspect
    from novel import config
    tree = ast.parse(inspect.getsource(config))
    top_calls = [n for n in tree.body if isinstance(n, ast.Expr) and isinstance(n.value, ast.Call)]
    assert not top_calls, f'模块顶层有函数调用：{top_calls}'
    assigns = [t.id for n in tree.body if isinstance(n, ast.Assign)
               for t in n.targets if isinstance(t, ast.Name)
               if isinstance(n.value, ast.Call)]
    assert not assigns, f'模块顶层有靠调用算出来的常量：{assigns}（`_REPO_ROOT` 那种要用表达式）'


# ─────────────────────────────────────────────────────────────────────────────
# 七、没有 key 也要能跑完每一个 0 成本 flag
# ─────────────────────────────────────────────────────────────────────────────

def test_every_free_flag_runs_without_an_api_key():
    """⚠ 原型靠**逐个人工核对**保证这件事。

    这里把它变成一条会红的断言：0 成本路径连 `DeepSeekTransport` 都不该实例化到
    要 key 的那一步。缺 key 只在**真要发请求**的那一刻才是错误。
    """
    from contracts.invariants import FREE_ENTRIES
    from novel.llm.transport import DeepSeekTransport

    cfg = load_config(env={}, dotenv_path=Path('/nonexistent'))
    assert not cfg.has_key
    # 建客户端、走完整条 0 成本链路都不许炸
    for flag in FREE_ENTRIES:
        t = DeepSeekTransport(cfg)
        c = _client(t)
        assert c.model and flag.startswith('--')
    # 只有真发请求那一刻才报，而且报的是显式终态
    with pytest.raises(MissingApiKey) as e:
        DeepSeekTransport(cfg).send(_req())
    assert issubclass(MissingApiKey, TerminalState)
    assert 'DEEPSEEK_API_KEY' in str(e.value)


def test_cli_client_uses_the_configured_persistent_cache(monkeypatch):
    """两个 CLI 进程必须能共享缓存；内存缓存会让每次重跑都重新付费。"""
    from novel.cli import main as cli

    cache_dir = Path(tempfile.mkdtemp()) / 'responses'
    monkeypatch.setenv('NOVEL_CACHE_DIR', str(cache_dir))
    client = cli._llm_client(_ctx())
    assert client.cache.dir == cache_dir


def test_cli_client_wires_the_thinking_mode(monkeypatch):
    """接线断了的话，A/B 的两个 nothink 臂会**静默**跑成默认档——必须钉住这条线。"""
    from novel.cli import main as cli

    monkeypatch.setenv('DEEPSEEK_THINKING', 'off')
    assert cli._llm_client(_ctx())._rungs is NOTHINK_RUNGS
    monkeypatch.delenv('DEEPSEEK_THINKING', raising=False)
    assert cli._llm_client(_ctx())._rungs is LADDER
