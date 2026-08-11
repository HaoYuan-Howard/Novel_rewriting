#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
test_llm.py —— 降级阶梯 · 缓存两个开关 · `require` 从模型派生 · 成本账。

⚠⚠ **这一份的存在本身就是 B2 的主要产出。** 原型那条阶梯是全链最值钱的逻辑之一
（它扛着两类实测发生过的故障），而它**一行测试都没有**——因为跑一次要花钱。
把「发请求」抽成一条缝（`novel/llm/transport.py::Transport`）之后，
**每一档**都能零成本跑一遍，而且能断言它**真的走过**那一档
（只看最终结果的话，「第一档就成功」与「降到第四档才成功」长得一模一样）。

⚠ 本机 `%TEMP%\\pytest-of-yh255` 的 ACL 坏了，`tmp_path` 一用就炸——一律 `tempfile.mkdtemp()`。
"""
from __future__ import annotations

import json
import tempfile
from pathlib import Path

import pytest
from pydantic import BaseModel, Field

from novel.config import Config, load_config, parse_dotenv
from novel.errors import TerminalState
from novel.llm.cache import CachePolicy, ResponseCache, cache_key
from novel.llm.client import DEFAULT_MAX_TOKENS, LADDER, LlmClient, LlmFailed
from novel.llm.ledger import CostLedger
from novel.llm.transport import (
    ChatRequest, ChatResponse, FakeTransport, MissingApiKey, TransportError,
)
from novel.run import RunContext


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


# ─────────────────────────────────────────────────────────────────────────────
# 一、⭐ 降级阶梯 —— 每一档都零成本跑一遍
# ─────────────────────────────────────────────────────────────────────────────

def test_the_ladder_is_exactly_the_four_rungs_and_there_is_only_one_of_it():
    """⚠ **原型把这条阶梯在两个入口各写了一遍。** 两份逐行相同的东西必然分家。

    档位逐字照抄：`high→medium→low` 是**给正文腾预算**（`max_tokens` 在推理模型上
    同时约束 reasoning + completion），最后那档关掉 thinking 是最后一手。
    """
    assert LADDER == (('high', True), ('medium', True), ('low', True), ('low', False))
    import inspect
    from novel.llm import client
    src = inspect.getsource(client)
    assert src.count('LADDER: Final') == 1
    # 两个入口都必须**走**它，不许各自展开一份
    for fn in ('ask_text', 'ask_json'):
        body = inspect.getsource(getattr(client.LlmClient, fn))
        assert 'in LADDER' in body, f'{fn} 没有走那条唯一的阶梯'


@pytest.mark.parametrize('rung', range(len(LADDER)))
def test_every_rung_can_recover_an_empty_answer(rung):
    """⭐ **四档各测一遍**：前 `rung` 档返回空正文，第 `rung+1` 档拿到正文。

    ⚠ 空正文那个形态是实测的：`finish_reason='stop'`、`reasoning` 很长、`content` 是空串
    ——**不是截断**，是推理流与答案流的边界丢了。照着「截断」去修（加 max_tokens）救不了它。
    """
    t = FakeTransport.empty_until(rung, OK_TEXT)
    assert _client(t).ask_text('sys', 'usr', purpose='t') == '一段正文'
    assert t.rungs == list(LADDER[:rung + 1]), '走过的档位与阶梯对不上'


def test_all_rungs_empty_raises_and_never_returns_an_empty_draft():
    """**全空则抛，绝不返回空稿。**

    ⚠ 一份空稿落下去，下游是**确定性代码**——它不会像写手那样将就着用，
    它会在几层之外炸，或者更坏：静默产出一份缺内容的东西。
    """
    t = FakeTransport.empty_until(len(LADDER), OK_TEXT)
    with pytest.raises(LlmFailed, match='拿不到正文'):
        _client(t).ask_text('sys', 'usr', purpose='t')
    assert t.rungs == list(LADDER)


def test_a_truncated_json_walks_the_ladder_because_lower_effort_frees_budget():
    """⚠ **截断表现为 JSON 解析失败**（`finish_reason='length'`），不是别的。

    `max_tokens` 在推理模型上**同时约束 reasoning + completion**：`effort='high'` 时
    模型可能把大半预算花在思考上，正文写到一半就到顶了。⇒ 降 effort = 给正文腾预算。
    """
    truncated = ChatResponse(content='{"voice": {"a": 1}, "surface_hab',
                             finish_reason='length', usage=None)

    def responder(_req, i):
        return truncated if i < 2 else OK_JSON

    t = FakeTransport(responder=responder)
    out = _client(t).ask_json('sys', 'usr', model_cls=Resp, purpose='t')
    assert out['surface_habits'] == ['h']
    assert t.rungs == [('high', True), ('medium', True), ('low', True)]


def test_a_legal_but_structurally_empty_json_goes_down_the_same_ladder():
    """⚠⚠ **「解析失败」与「合法 JSON 但结构上是空的」是同一类故障**（原型 F-125）。

    实测：某次规划调用拿回 `{"reconstruction_strategy": "…"}`——**0 个输出章**、
    正常结束、4477 token 里推理占 4317。它是合法 JSON，于是一路放行**并进了缓存**；
    下游把它摊成 **74 条**「这一拍没有给预算」，还附一句「说明 prompt 漂了」。
    **74 条派生症状盖住了一句话能说清的事实。**
    """
    shell = ChatResponse(content='{"voice": {"a": 1}}', finish_reason='stop', usage=None)
    t = FakeTransport(responder=lambda _r, i: shell if i < 1 else OK_JSON)
    out = _client(t).ask_json('sys', 'usr', model_cls=Resp, purpose='t')
    assert out['surface_habits'] == ['h']
    assert t.rungs == [('high', True), ('medium', True)]


def test_each_rung_is_its_own_cache_entry():
    """⚠ `reasoning_effort` **进缓存键**。

    不进的话，降级到第二档时会读回第一档那个空壳——「重试」变成把同一个结果读两遍，
    而那与一次真调用长得一模一样。
    """
    base = dict(model='m', system='s', user='u', json_mode=False, max_tokens=1)
    keys = {cache_key(ChatRequest(reasoning_effort=e, thinking=t, **base)) for e, t in LADDER}
    assert len(keys) == len(LADDER)


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


def _req(effort: str = 'high', thinking: bool = True) -> ChatRequest:
    return ChatRequest(model='deepseek-v4-pro', system='s', user='u',
                       reasoning_effort=effort, thinking=thinking,
                       json_mode=False, max_tokens=DEFAULT_MAX_TOKENS)


def test_an_empty_answer_is_never_cached():
    """⚠ 缓存下来只会让重试**每次都命中同一个空壳**——那时连降级阶梯都救不了它，
    因为四档会各自读回自己那份空的。"""
    d = Path(tempfile.mkdtemp())
    t = FakeTransport.empty_until(2, OK_TEXT)
    _client(t, cache=ResponseCache(d)).ask_text('s', 'u', purpose='t')
    assert len(list(d.iterdir())) == 1, '空回答被写进缓存了'


def test_a_structurally_empty_shell_is_evicted_from_the_cache():
    """⭐ **空壳踢出缓存。**

    留着它，下一次不带 `--no-cache` 的重跑会原样读回来，而调用方看到的是一模一样的失败
    ——**那不是重试，是把同一个结果读了两遍**。
    ⚠ 这里**不适用**产物那条「修不了的原样留着」（判据 7）：产物留着是给人看的，
    一份坏缓存留着不会被人看见，只会被下一次重跑静默地当成新结果。
    """
    d = Path(tempfile.mkdtemp())
    shell = ChatResponse(content='{"voice": {"a": 1}}', finish_reason='stop')
    t = FakeTransport(responder=lambda _r, i: shell if i < 1 else OK_JSON)
    _client(t, cache=ResponseCache(d)).ask_json('s', 'u', model_cls=Resp, purpose='t')

    keys = {p.name for p in d.iterdir()}
    assert f'{cache_key(_json_req("high", True))}.json' not in keys, '空壳还留在缓存里'
    assert f'{cache_key(_json_req("medium", True))}.json' in keys, '好的那一份该留着'


def _json_req(effort: str, thinking: bool) -> ChatRequest:
    return ChatRequest(model='deepseek-v4-pro', system='s', user='u',
                       reasoning_effort=effort, thinking=thinking,
                       json_mode=True, max_tokens=DEFAULT_MAX_TOKENS)


def test_a_half_written_cache_file_reads_as_a_miss_not_as_a_failure():
    """⚠ 半截缓存抛异常的话，那个异常会被当成「这次调用失败」——
    **把一次已经付过钱的结果变成一次重跑**。⇒ 读不出来一律当没有。"""
    d = Path(tempfile.mkdtemp())
    (d / f'{cache_key(_req())}.json').write_bytes('{"content": "半截'.encode('utf-8'))
    t = FakeTransport.always(OK_TEXT)
    assert _client(t, cache=ResponseCache(d)).ask_text('s', 'u', purpose='t') == '一段正文'
    assert len(t.calls) == 1


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
# 四、传输层重试 —— 与降级阶梯是**两条**循环
# ─────────────────────────────────────────────────────────────────────────────

def test_a_retryable_transport_error_does_not_consume_a_ladder_rung():
    """⚠⚠ **混成一条，一次网络抖动就会消耗掉整条阶梯**——而阶梯救不了连接被重置。"""
    def responder(_req, i):
        return TransportError('429 Too Many Requests', retryable=True, status=429) if i < 2 \
            else OK_TEXT

    t = FakeTransport(responder=responder)
    assert _client(t).ask_text('s', 'u', purpose='t') == '一段正文'
    assert t.rungs == [('high', True)] * 3, '重试把阶梯的档位吃掉了'


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
    with pytest.raises(TransportError):
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


def test_a_run_with_no_calls_has_no_usage_key_at_all():
    """0 成本节点是**键不在**，不是一份全 0 的 usage。"""
    assert CostLedger().usage() is None


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
