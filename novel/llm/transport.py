#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
transport.py —— 「把一次请求发出去」这一件事，**只有这一层知道网络存在**。

【为什么要这条缝】降级阶梯（现行 `(high,T)→(low,T)→(low,F)`）是全链
最值钱的一段逻辑之一——它扛的是两类**实测发生过**的故障。而它此前**一行测试都没有**，
因为跑一次要花钱。把「发请求」抽成一个协议之后，阶梯的**每一档**都能零成本跑一遍。
⚠ 原型那一份是四档（`high→medium→low→low/nothink`），`medium` 已删：它是合法参数但被
服务端静默映射回 `high` ⇒ 与首档**两个缓存键、同一种行为**，白花一次钱且腾不出任何预算
（`novel/llm/client.py::LADDER` 记着全部理由）。

⚠ `FakeTransport` **住在运行时包里，不在 `tests/`**，而这是有意的：
它同时是 `--dry-run` 的实现（`FREE_ENTRIES` 里那条「只打印将要发出去的东西，不调用、不写盘」）。
放进 `tests/` 就会变成第二份——一份给测试、一份给 `--dry-run`，然后它们分家。

【本层不管重试，也不管降级】
  · **降级阶梯**（换 effort / 关 thinking）在 `client.py`：它扛的是**模型行为**；
  · **传输层重试**（429 / 5xx / 超时）也在 `client.py`，但那是另一条循环。
  ⚠ **两者混成一条，一次网络抖动就会消耗掉整条阶梯**——而阶梯是用来救「响应是空的」的，
    不是用来救「连接被重置」的。
"""
from __future__ import annotations

import threading
from dataclasses import dataclass, field
from typing import Any, Callable, Protocol

from novel.errors import NovelError, TerminalState


def _error_kind(*, status: int | None = None, exception_name: str = '') -> str:
    """把供应商异常压成稳定、可聚合的分类；不依赖 SDK 的易变异常继承树。"""
    if status in (408, 504):
        return 'timeout'
    if status == 409:
        return 'conflict'
    if status == 429:
        return 'rate_limit'
    if isinstance(status, int):
        return 'server_error' if status >= 500 else 'http_error'
    name = exception_name.lower()
    if 'timeout' in name:
        return 'timeout'
    if 'connection' in name or 'apiconnection' in name:
        return 'connection'
    return 'transport'


class TransportError(NovelError):
    """一次请求没发成功。重试判定与稳定分类都由这里给出，不让调用方猜措辞。"""

    def __init__(self, message: str, *, retryable: bool, status: int | None = None,
                 kind: str | None = None) -> None:
        super().__init__(message)
        self.retryable = retryable
        self.status = status
        self.kind = kind or _error_kind(status=status)


class MissingApiKey(TerminalState):
    """要发真请求了，而没有 key。

    ⚠ **报在这里，不报在 `load_config()`**：0 成本路径本来就不需要 key，
    在配置那一层早报会让 `--check-only` 这类动作**在没有 key 的机器上跑不起来**，
    而它们一分钱都不花。⇒ 缺 key 只在真要发请求的那一刻才是错误。
    """


@dataclass(frozen=True)
class ChatRequest:
    """一次调用的**全部**参数。缓存键就是它的规范化形态。

    ⚠ 每一个字段都进缓存键：`reasoning_effort` 换一档就是**另一次调用**，
    而降级阶梯正是靠这一点让四档各自独立缓存——不然第二档会读回第一档那个空壳。
    """

    model: str
    system: str
    user: str
    reasoning_effort: str
    thinking: bool
    json_mode: bool
    max_tokens: int
    temperature: float | None = None

    def messages(self) -> list[dict[str, str]]:
        return [{'role': 'system', 'content': self.system},
                {'role': 'user', 'content': self.user}]

    def cache_payload(self) -> dict[str, Any]:
        """算缓存键用的规范化形态。**改这个函数会让整个缓存失效**——那通常是对的，
        但要知道自己在做什么（旧缓存不会被删，只是再也命中不了）。"""
        return {
            'model': self.model, 'messages': self.messages(),
            'reasoning_effort': self.reasoning_effort, 'thinking': self.thinking,
            'json_mode': self.json_mode, 'max_tokens': self.max_tokens,
            'temperature': self.temperature,
        }


@dataclass(frozen=True)
class ChatResponse:
    """一次调用的结果。

    ⚠ `reasoning` 存下来但**永不用来救场**：实测 DeepSeek 会把整段答案写进
    `reasoning_content` 而 `content` 返空，而从那里面把答案抠出来救回的是**半成品**
    （里面混着推敲过程）。它只用来在报错时说清「模型不是没干活，是答案流丢了」。
    """

    content: str | None
    reasoning: str | None = None
    finish_reason: str | None = None
    usage: dict[str, Any] | None = None
    model: str = ''

    @property
    def text(self) -> str:
        return (self.content or '').strip()


class Transport(Protocol):
    """把一次请求发出去。**实现只有两个**：真的那个，和零成本那个。"""

    def send(self, req: ChatRequest) -> ChatResponse: ...


# ─────────────────────────────────────────────────────────────────────────────
# 真的那个
# ─────────────────────────────────────────────────────────────────────────────

class DeepSeekTransport:
    """OpenAI SDK → DeepSeek。**除了这个类，全仓没有第二处 import `openai`。**"""

    def __init__(self, cfg: Any) -> None:
        self.cfg = cfg
        self._client: Any = None
        self._client_lock = threading.Lock()

    def _sdk(self) -> Any:
        """**懒建**：import 本模块不该要求装了 `openai`，也不该在没有 key 的机器上炸。

        ⚠ 契约层与 0 成本路径都不该背上 HTTP 栈这个依赖——
        `pyproject.toml` 把它放在 optional 的 `llm` 分组里，正是为了这条。
        """
        if self._client is None:
            # Stage 2 会让多个章节线程同时发起第一笔请求。懒初始化也必须只有一个赢家；
            # 否则会建立多个 SDK 连接池，后写者覆盖前写者，而前者仍被在途线程使用。
            with self._client_lock:
                if self._client is None:
                    if not self.cfg.api_key:
                        raise MissingApiKey(
                            '要发真请求了，但 `DEEPSEEK_API_KEY` 没设。'
                            '把 `.env.example` 抄成 `.env` 填进去，或者导出这个环境变量。'
                            '⚠ 只想跑 0 成本动作的话不需要它——`FREE_ENTRIES` 里那些 flag 一分钱不花')
                    from openai import OpenAI          # 懒 import：见本方法 docstring
                    # ⚠⚠ SDK 重试必须关闭；唯一重试所有者是 client.py。
                    self._client = OpenAI(
                        api_key=self.cfg.api_key, base_url=self.cfg.base_url,
                        timeout=self.cfg.timeout, max_retries=0)
        return self._client

    def send(self, req: ChatRequest) -> ChatResponse:
        # ⚠⚠ **建客户端必须在 `try` 外面。** 放进去的话，`MissingApiKey`（一条显式终态）
        #   会被下面那个 `except Exception` 抓走、经 `_classify` 变成一条 `TransportError`
        #   ——于是「你没设 key」被当成网络问题**重试三次**（还带指数退避），
        #   而人看到的报错是 `TransportError` 不是「去填 .env」。
        #   这一处是 B2 的测试当场抓出来的，不是设想。
        sdk = self._sdk()
        params: dict[str, Any] = {
            'model': req.model,
            'messages': req.messages(),
            'stream': False,
            'reasoning_effort': req.reasoning_effort,
            # DeepSeek 的推理开关走 extra_body（照抄原型，别改成别的写法）
            'extra_body': {'thinking': {'type': 'enabled' if req.thinking else 'disabled'}},
            'max_tokens': req.max_tokens,
        }
        if req.json_mode:
            params['response_format'] = {'type': 'json_object'}
        if req.temperature is not None:
            params['temperature'] = req.temperature

        try:
            resp = sdk.chat.completions.create(**params)
        except Exception as e:                      # noqa: BLE001 —— SDK 的异常族不稳定
            raise _classify(e) from e

        choice = resp.choices[0]
        msg = choice.message
        usage = None
        if getattr(resp, 'usage', None) is not None:
            try:
                usage = resp.usage.model_dump()
            except Exception:                       # noqa: BLE001
                usage = {k: getattr(resp.usage, k, None)
                         for k in ('prompt_tokens', 'prompt_cache_hit_tokens',
                                   'prompt_cache_miss_tokens', 'completion_tokens',
                                   'total_tokens')}
        return ChatResponse(
            content=msg.content,
            reasoning=getattr(msg, 'reasoning_content', None),
            finish_reason=getattr(choice, 'finish_reason', None),
            usage=usage,
            model=getattr(resp, 'model', req.model),
        )


#: 该重试的 HTTP 状态。**429 与 5xx**：前者是限流（等一下就好），后者是对面的事。
#: ⚠ 4xx 里除了 429 一律**不重试**——那是我们自己发错了（key 不对、参数不合法），
#: 重试只会把同一个错误再犯三遍，而每一遍都可能计费。
_RETRYABLE_STATUS = frozenset({408, 409, 429, 500, 502, 503, 504})


def _classify(e: Exception) -> TransportError:
    """SDK 异常 → 该不该重试。**判定只有这一处**，调用方不许自己认措辞。"""
    status = getattr(e, 'status_code', None) or getattr(e, 'status', None)
    name = type(e).__name__
    if isinstance(status, int):
        return TransportError(f'{type(e).__name__}: {e}', retryable=status in _RETRYABLE_STATUS,
                              status=status,
                              kind=_error_kind(status=status, exception_name=name))
    # 没有状态码时只重试可识别的超时/连接异常；任意本地异常不能被当成网络抖动。
    kind = _error_kind(exception_name=name)
    return TransportError(f'{type(e).__name__}: {e}',
                          retryable=kind in ('timeout', 'connection'), kind=kind)


# ─────────────────────────────────────────────────────────────────────────────
# 零成本那个
# ─────────────────────────────────────────────────────────────────────────────

@dataclass
class FakeTransport:
    """**不发任何请求**。测试与 `--dry-run` 共用这一份。

    `responder(req, attempt_index) -> ChatResponse | Exception`：
    返回异常对象就当成抛出去——于是「第 1 次 429、第 2 次成功」这种剧本可以直接写出来，
    而不必去 mock SDK 的内部。

    ⚠ `calls` 记下**每一次**收到的请求，包括被降级阶梯重发的那几次。
    这是「阶梯每一档都真的跑过」唯一的证据——只看最终结果的话，
    「第一档就成功」与「降到第四档才成功」长得一模一样。
    """

    responder: Callable[[ChatRequest, int], ChatResponse | Exception]
    calls: list[ChatRequest] = field(default_factory=list)

    def send(self, req: ChatRequest) -> ChatResponse:
        idx = len(self.calls)
        self.calls.append(req)
        out = self.responder(req, idx)
        if isinstance(out, Exception):
            raise out
        return out

    @property
    def rungs(self) -> list[tuple[str, bool]]:
        """收到的请求走过哪几档阶梯，按顺序。断言写起来就是一行。"""
        return [(r.reasoning_effort, r.thinking) for r in self.calls]

    # ── 几个常用剧本。**写在这里而不是每个测试各写一遍** ──────────────────

    @classmethod
    def always(cls, response: ChatResponse) -> FakeTransport:
        return cls(responder=lambda _req, _i: response)

    @classmethod
    def empty_until(cls, n: int, then: ChatResponse) -> FakeTransport:
        """前 `n` 次返回**空 content**（实测那个形态：答案全写进了 reasoning），第 `n+1` 次成功。

        ⚠ `finish_reason='stop'` 是**故意**的：那不是截断，是推理流与答案流的边界丢了。
        照着「截断」去修（加 max_tokens）救不了它。
        """
        def _r(_req: ChatRequest, i: int) -> ChatResponse:
            if i < n:
                return ChatResponse(content='', reasoning='想了很多，但答案没流出来',
                                    finish_reason='stop', usage=None)
            return then
        return cls(responder=_r)
