#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
transport.py —— 「把一次请求发出去」这一件事，**只有这一层知道网络存在**。

【为什么要这条缝】同档重采与 Flash→Pro 兜底扛的是实测发生过的模型响应故障，
却不能靠真实付费调用才能测试。把「发请求」抽成协议之后，每次采样、模型切换与
传输重试都能零成本逐项断言。旧实现曾自动走 `high→low→low/nothink`；2026-08-24
已取消异常驱动的 effort 降级，历史理由保留在 `novel/llm/client.py`。

⚠ `FakeTransport` **住在运行时包里，不在 `tests/`**，而这是有意的：
它同时是 `--dry-run` 的实现（`FREE_ENTRIES` 里那条「只打印将要发出去的东西，不调用、不写盘」）。
放进 `tests/` 就会变成第二份——一份给测试、一份给 `--dry-run`，然后它们分家。

【本层不管重试，也不管模型兜底】
  · **同档重采与 Flash→Pro 兜底**在 `client.py`：它们扛的是**模型响应**；
  · **传输层重试**（429 / 5xx / 超时）也在 `client.py`，但那是另一条循环。
  ⚠ **它们混成一条，一次网络抖动就会消耗掉内容采样**——而模型链是用来救「响应是空的」的，
    不是用来救「连接被重置」的。
"""
from __future__ import annotations

import threading
from dataclasses import dataclass, field
from typing import Any, Callable, Protocol
from urllib.parse import unquote, urlsplit

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
    if 'read' in name and 'timeout' in name:
        # 读超时＝请求已送达、响应未回：结果未知，不得当网络抖动重试（B1）。
        return 'read_timeout'
    if 'timeout' in name:
        return 'timeout'
    if 'connect' in name:
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
    模型 / effort / thinking 任一项变化都必须是独立缓存——不然 Pro 会读回 Flash 的空壳。
    """

    model: str
    system: str
    user: str
    reasoning_effort: str
    thinking: bool
    json_mode: bool
    max_tokens: int
    temperature: float | None = None
    history: tuple[tuple[str, str], ...] = ()

    def messages(self) -> list[dict[str, str]]:
        return [{'role': 'system', 'content': self.system},
                *({'role': role, 'content': content} for role, content in self.history),
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

    ⚠⚠ **`reasoning` 现在会用来救场，但只在 `ask_json` 那一侧**
    （`novel/llm/client.py::SALVAGE_ANSWER_FROM_REASONING`，2026-08-22 用户裁决）：
    实测 DeepSeek 会把整段答案写进 `reasoning_content` 而 `content` 返空，
    而那一段**整体过得了同一套闸**时就采纳它，不再多付一次重采。
    ⚠ `ask_text` 那一侧仍然不抠（自然语言没有等价的客观闸），所以这个字段的另外两个
    用途照旧：报错时说清「模型不是没干活，是答案流丢了」，以及落一份留证
    （`novel/llm/keepsake.py`）——捞成功时留证也照写，那时它是**被采纳产物的来源**。
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
                    from openai import DefaultHttpxClient, OpenAI
                    http_client = None
                    if self.cfg.proxy_url:
                        # 显式代理是传输配置，不依赖启动者有没有把 HTTP(S)_PROXY
                        # 灌进进程环境。前端 Agent、交互式 CLI 因而走同一条网络口。
                        http_client = DefaultHttpxClient(
                            proxy=self.cfg.proxy_url, timeout=self.cfg.timeout)
                    # ⚠⚠ SDK 重试必须关闭；唯一重试所有者是 client.py。
                    self._client = OpenAI(
                        api_key=self.cfg.api_key, base_url=self.cfg.base_url,
                        timeout=self.cfg.timeout, max_retries=0,
                        http_client=http_client)
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


@dataclass(frozen=True)
class TransportProbe:
    """不带正文、不带 API key 的 TLS/HTTP 通路探针结果。"""

    reached_http: bool
    status: int | None
    kind: str
    detail: str


def _redact_proxy_detail(detail: str, proxy_url: str | None) -> str:
    """清除代理 URL 及其中可能被异常单独展开的认证字段。"""
    if not proxy_url:
        return detail
    redacted = detail.replace(proxy_url, '<proxy>')
    parsed = urlsplit(proxy_url)
    for secret in (parsed.username, parsed.password):
        if not secret:
            continue
        for form in {secret, unquote(secret)}:
            if form:
                redacted = redacted.replace(form, '<redacted>')
    return redacted


def probe_transport(cfg: Any) -> TransportProbe:
    """用与正式请求相同的代理/超时构造客户端，只探测到 ``/models``。

    401/403 也算传输成功：它证明 DNS/TCP/TLS/HTTP 已到服务端；本命令不验证密钥，
    更不发送正文。代理地址与密钥绝不进入结果文本。
    """
    from openai import DefaultHttpxClient

    kwargs: dict[str, Any] = {'timeout': cfg.timeout}
    if cfg.proxy_url:
        kwargs['proxy'] = cfg.proxy_url
    client = DefaultHttpxClient(**kwargs)
    try:
        response = client.get(cfg.base_url.rstrip('/') + '/models')
        return TransportProbe(
            reached_http=True, status=int(response.status_code), kind='http',
            detail=f'已收到 HTTP {response.status_code}')
    except Exception as exc:  # noqa: BLE001 —— httpx/openai 异常族会随版本变化
        classified = _classify(exc)
        detail = _redact_proxy_detail(
            f'{type(exc).__name__}: {exc}', cfg.proxy_url)
        return TransportProbe(
            reached_http=False, status=classified.status, kind=classified.kind,
            detail=detail)
    finally:
        client.close()


def _retryable_http_status(status: int) -> bool:
    """该不该做传输层有界退避。

    408 是「超时」这一类的 HTTP 表达，与无状态码的 timeout 异常同样可重试；
    其余 4xx 只有 429 可重试。409 等配置/请求冲突必须立即终态。
    所有 5xx 由供应端负责，不在这里挑选少数状态码，避免新的 5xx
    被误分类为不可重试。
    """
    return status in (408, 429) or 500 <= status <= 599


def _classify(e: Exception) -> TransportError:
    """SDK 异常 → 该不该重试。**判定只有这一处**，调用方不许自己认措辞。

    B1（按是否已送达分类）：连接/DNS/TLS 失败与连接超时是**未送达**，可重试；
    读超时是**已送达后结果未知**（``kind='read_timeout'``），重试会重复付费，
    必须交由上层记 unknown。SDK 会把 httpx 超时包进 ``APIConnectionError``，
    因此沿 ``__cause__`` 链认原始异常名。
    """
    status = getattr(e, 'status_code', None) or getattr(e, 'status', None)
    name = type(e).__name__
    if isinstance(status, int):
        return TransportError(f'{type(e).__name__}: {e}',
                              retryable=_retryable_http_status(status),
                              status=status,
                              kind=_error_kind(status=status, exception_name=name))
    cause_names = []
    cause = getattr(e, '__cause__', None)
    while cause is not None and len(cause_names) < 5:
        cause_names.append(type(cause).__name__)
        cause = getattr(cause, '__cause__', None)
    chain = ' '.join([name, *cause_names])
    # 没有状态码时只重试可识别的超时/连接异常；任意本地异常不能被当成网络抖动。
    # 沿异常链认原始名（SDK 把 httpx 超时包进 APIConnectionError）。
    kind = _error_kind(exception_name=chain)
    retryable = kind in ('timeout', 'connection')
    return TransportError(f'{type(e).__name__}: {e}', retryable=retryable, kind=kind)


# ─────────────────────────────────────────────────────────────────────────────
# 零成本那个
# ─────────────────────────────────────────────────────────────────────────────

@dataclass
class FakeTransport:
    """**不发任何请求**。测试与 `--dry-run` 共用这一份。

    `responder(req, attempt_index) -> ChatResponse | Exception`：
    返回异常对象就当成抛出去——于是「第 1 次 429、第 2 次成功」这种剧本可以直接写出来，
    而不必去 mock SDK 的内部。

    ⚠ `calls` 记下**每一次**收到的请求，包括同档重采、模型兜底与网络重试。
    这是「恢复链真的跑过哪些配置」的直接证据——只看最终结果分不出
    「Flash 首发成功」与「Flash 两采失败后由 Pro 成功」。
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
        """收到的请求走过哪些显式 effort/thinking 配置，按顺序。"""
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
