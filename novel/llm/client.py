#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
client.py —— `ask_text` / `ask_json`。**降级阶梯只有一份，两个入口共用。**

【降级阶梯扛的是两类实测发生过的故障，它们看起来完全不像同一件事】

  ① **答案流丢了。** DeepSeek 会把整段答案写进 `reasoning_content` 而 `content` 返回**空串**，
     `finish_reason='stop'`——**不是截断**。实测：模型把整章正文写进了推理里。
     ⚠ 不从 `reasoning` 里把答案抠出来——那里面混着推敲过程，救回来的是**半成品**。

  ② **JSON 被截断。** `max_tokens` 在推理模型上**同时约束 reasoning + completion**。
     `reasoning_effort='high'` 时模型可能把大半预算花在思考上，正文写到一半就到顶了，
     于是**截断表现为 JSON 解析失败**（`finish_reason='length'`）。
     ⇒ 降 effort 就是**给正文腾预算**，这才是阶梯管用的原因。

  ③ **合法 JSON 但结构上是空的。** 这一类与①②**必须走同一条阶梯**（原型 F-125）。
     实测：某次规划调用拿回 `{"reconstruction_strategy": "…"}`——0 个输出章、正常结束、
     4477 token 里推理占 4317。它是合法 JSON，于是一路放行**并进了缓存**；
     下游把它摊成 **74 条**「这一拍没有给预算」。**74 条派生症状盖住了一句话能说清的事实。**

⚠⚠ **原型把这条阶梯在两个入口各写了一遍。** 两份逐行相同的东西必然分家——
本文件只有一份 `LADDER`，两个入口都从它走。

【阶梯与传输层重试是两条独立的循环，不许合并】
阶梯换的是**模型行为**（effort / thinking），重试扛的是**网络**（429 / 5xx / 超时）。
混成一条，一次网络抖动就会消耗掉整条阶梯——而阶梯救不了连接被重置。
"""
from __future__ import annotations

import json
import threading
import time
from typing import Any, Callable, Final, TypeVar

from pydantic import BaseModel, ValidationError

from novel import config
from novel.errors import NovelError
from novel.llm.cache import CachePolicy, ResponseCache, cache_key
from novel.llm.prompts import required_top_level
from novel.llm.transport import ChatRequest, ChatResponse, Transport, TransportError
from novel.observability import (
    AttemptHeartbeat, DEFAULT_HEARTBEAT_SECONDS, RequestProgress,
)
from novel.run import RunContext

#: **降级阶梯，全仓唯一一份。** `(reasoning_effort, thinking)`，按顺序走。
#: ⚠ 逐字照抄原型：`high→medium→low` 是**给正文腾预算**（见模块 docstring ②），
#: 最后那档关掉 thinking 是「推理流本身就是问题源」时的最后一手。
#: ⚠ **全空则抛**，绝不返回空稿——一份空稿落下去，下游是确定性代码，
#: 它不会像写手那样将就着用，它会在几层之外炸，或者更坏：静默产出一份缺内容的东西。
LADDER: Final[tuple[tuple[str, bool], ...]] = (
    ('high', True), ('medium', True), ('low', True), ('low', False),
)

#: 默认 token 上限。**统一 64k**：`max_tokens` 在推理模型上同时约束 reasoning + completion，
#: 高预算防止长正文/推理被 API 默认上限**悄悄**截断。确需更小上限时显式传。
DEFAULT_MAX_TOKENS: Final = 64_000


M = TypeVar('M', bound=BaseModel)
TextValidator = Callable[[str, ChatResponse], str | None]


class LlmFailed(NovelError):
    """整条阶梯走完仍然没拿到可用的东西。**显式终态，绝不返回空稿。**"""


def _strip_fences(s: str | None) -> str:
    """防御性地剥掉 ```json … ``` ——有些模型在 JSON 模式下照样吐围栏。"""
    s = (s or '').strip()
    if s.startswith('```'):
        s = s.split('\n', 1)[1] if '\n' in s else s[3:]
        if s.rstrip().endswith('```'):
            s = s.rstrip()[:-3]
    return s.strip()


def _is_empty(v: Any) -> bool:
    """这个顶层键的值算不算「结构上是空的」。**不是真值判断。**

    `None` / `''` / `[]` / `{}` / 空集合叫空；**`False` 与 `0` 不叫空**。

    ⚠ 原来这里写的是 `not data.get(k)`，于是一个**合法的 `False`**（或 `0`、
    或一个刻意为空的集合语义）会被报成「模型没给这个键」——
    白走四档阶梯、花四次钱，去救一个根本没坏的响应。
    ⚠ 反过来这道闸**不能删**：F-125 那个形态（`{"reconstruction_strategy": "…"}`，
    **0 个输出章**、正常结束、4477 token 里推理占 4317）是**合法 JSON**，
    `model_validate` 对一个 `list` 字段收到 `[]` 也照样放行。
    要让 pydantic 自己拦住它，响应模型得声明 `Field(min_length=1)`——
    而「记得声明」正是判据 39 已经证明过靠不住的那种纪律。
    """
    if v is None:
        return True
    return isinstance(v, (str, bytes, list, tuple, dict, set, frozenset)) and len(v) == 0


class LlmClient:
    """一次跑的 LLM 入口。**缓存口径与成本账都从 `RunContext` 来**，不各自持有一份。"""

    def __init__(self, ctx: RunContext, transport: Transport, *,
                 cache: ResponseCache | None = None, model: str = '',
                 max_retries: int = config.DEFAULT_MAX_RETRIES,
                 backoff_base: float = config.DEFAULT_BACKOFF_BASE,
                 concurrency: int = config.DEFAULT_CONCURRENCY,
                 sleep: Callable[[float], None] = time.sleep,
                 heartbeat_interval: float = DEFAULT_HEARTBEAT_SECONDS,
                 clock: Callable[[], float] = time.perf_counter) -> None:
        # ⚠ 四个默认值**引用** `novel/config.py`，不在这里再写一份字面量。
        #   那个文件抬头就写着「默认值都在这里，别在调用点再写一份」，而这里原来写了
        #   第二份（`3` / `2.0` / `2` / `'deepseek-v4-pro'`）。两份数分家时不会报错——
        #   它只是让「这次跑到底用的哪个并发度」有两个都说得通的答案。
        self.ctx = ctx
        self.transport = transport
        self.cache = cache if cache is not None else ResponseCache()
        self.model = model or config.DEFAULT_MODEL
        self.max_retries = max_retries
        self.backoff_base = backoff_base
        self._sleep = sleep
        if heartbeat_interval < 0:
            raise ValueError(f'heartbeat_interval 不能为负数，实得 {heartbeat_interval}')
        self.heartbeat_interval = heartbeat_interval
        self._clock = clock
        #: 并发闸。⚠ 它守的不是速率，是**钱**：一次配置错误（prompt 漂了）在被人看见之前
        #: 能烧掉多少，与并发度成正比。见 `novel/config.py::DEFAULT_CONCURRENCY`。
        self.concurrency = max(1, concurrency)
        self._gate = threading.Semaphore(self.concurrency)

    # ── 两个入口 ──────────────────────────────────────────────────────────

    def ask_text(self, system: str, user: str, *, purpose: str,
                 max_tokens: int = DEFAULT_MAX_TOKENS,
                 validator: TextValidator | None = None,
                 rungs: tuple[tuple[str, bool], ...] | None = None,
                 progress: RequestProgress | None = None) -> str:
        """纯文本调用。默认走 `LADDER`，**全空或全被调用点拒绝则抛**。

        `validator` 是纯文本响应声明「可用」含义的边界：返回 `None` 表示可用，
        返回人话理由表示这一档不可用、应驱逐缓存并降档。它只该判断响应是否完整，
        不能拿文风偏好触发付费重采样。

        `rungs` 只给确有不同成本语义的辅助调用收窄阶梯；默认路径仍走全仓唯一的
        `LADDER`。空阶梯是调用错误，不能伪装成「模型失败」。
        """
        active_rungs = LADDER if rungs is None else tuple(rungs)
        if not active_rungs:
            raise ValueError(f'{self.ctx.node}/{purpose}: rungs 不能为空')
        tried: list[str] = []
        for effort, thinking in active_rungs:
            for use_cache in (True, False):    # ← 缓存那份不可用 ⇒ 同一档真发一次，见 `_call_keyed`
                resp, key, cached = self._call_keyed(
                    system, user, effort=effort, thinking=thinking, json_mode=False,
                    max_tokens=max_tokens, purpose=purpose, use_cache=use_cache,
                    progress=progress)
                text = resp.text
                why = None if text else '正文为空'
                if why is None and validator is not None:
                    why = validator(text, resp)
                if why is None:
                    if not cached:
                        self._keep(key, resp)      # ← 判定可用之后才写
                    return text
                self.cache.evict(key)
                tried.append(
                    f'effort={effort} think={thinking}{" [缓存]" if cached else ""} '
                    f'finish={resp.finish_reason} reasoning_len={len(resp.reasoning or "")} '
                    f'拒绝={why}')
                if not cached:
                    break                      # 真发的也空 ⇒ 这一档确实不行，降一档
        raise LlmFailed(
            f'{self.ctx.node}/{purpose}: 走完 {len(active_rungs)} 档阶梯仍然拿不到正文'
            '（没有可用响应）。\n  '
            + '\n  '.join(tried)
            + '\n⚠ `reasoning_len` 大而正文为空 = 模型不是没干活，是答案流丢了；'
              '**不从 reasoning 里抠答案**，那里面混着推敲过程，救回来的是半成品')

    def ask_json(self, system: str, user: str, *, model_cls: type[M], purpose: str,
                 require: tuple[str, ...] | None = None,
                 max_tokens: int = DEFAULT_MAX_TOKENS,
                 progress: RequestProgress | None = None) -> M:
        """结构化调用。走**同一条** `LADDER`，解析失败与「结构上是空的」同等对待。

        ⚠⚠ **`model_cls` 是必填的，而 `require` 默认从它派生。**
        原型的 `require=()` 默认值正是那三个漏声明的调用点得以存在的原因——
        **漏传不报错，只是静默停掉一条 HARD 闸门**（判据 39）。派生之后它**结构上不可能忘**。

        显式传 `require` **只许收窄**（子集）：放宽等于绕开上面那条，
        而绕开会长成「这个调用点特殊」的注释，然后没人再回来看它。

        ⚠⚠ **返回的是校验过的模型实例，不是原始 dict。** 原来 `model_cls` 只被拿去
        派生 `require`，**一次 `model_validate` 都没跑过**：于是 `voice='not-a-dict'`、
        `surface_habits=[1]` 这种一路放行，而 `extra='forbid'` 那类载体守卫
        **一次都没响过**。`store.py` 那条主杠杆写着「只有 `store.read` 返回带类型的模型
        ⇒ 绕过意味着同时放弃校验和类型」——`ask_json` 是不可信数据进运行时的**另一扇门**，
        它得守同一条。校验完再退回 dict 等于让调用方读**未校验**的那一份
        （pydantic 会强制转换、填默认值、走别名），那就是同一份响应的两种表示。

        【一档之内三道闸，顺序是挑过的】
          ① `json.loads`      —— 截断就长这样（`finish_reason='length'`）；
          ② **空壳闸**（`_is_empty`）—— F-125，合法 JSON 但结构上是空的；
          ③ `model_validate`  —— 形状/类型/`extra='forbid'`。
        ②在③之前：`require` 是按**盘上键名**（alias）定义的，而且这条路要报得出
        「缺的是哪一个顶层键」——那句话归②，不归 pydantic 的错误文本。
        三道闸失败的处置**完全一样**：驱逐这一档的缓存 + 降一档。
        """
        derived = required_top_level(model_cls)
        if require is None:
            require = derived
        elif extra := sorted(set(require) - set(derived)):
            raise ValueError(
                f'{purpose}: require={sorted(require)} 里的 {extra} 不在 '
                f'{model_cls.__name__} 的必需顶层键 {sorted(derived)} 里。'
                f'显式传只许**收窄**——放宽等于给这个调用点开一个「它特殊」的口子')

        errors: list[str] = []
        for effort, thinking in LADDER:
            for use_cache in (True, False):    # ← 缓存那份不可用 ⇒ 同一档真发一次，见 `_call_keyed`
                resp, key, cached = self._call_keyed(
                    system, user, effort=effort, thinking=thinking, json_mode=True,
                    max_tokens=max_tokens, purpose=purpose, use_cache=use_cache,
                    progress=progress)
                content = _strip_fences(resp.content)
                head = (f'effort={effort} think={thinking}{" [缓存]" if cached else ""} '
                        f'finish={resp.finish_reason} clen={len(content)}')

                why = None
                obj = None
                try:
                    data = json.loads(content)
                except json.JSONDecodeError as e:
                    # 截断的表现就是这个（见模块 docstring ②）。降一档 = 给正文腾预算。
                    why = f'解析失败: {e}'
                else:
                    missing = [k for k in require
                               if not isinstance(data, dict) or k not in data
                               or _is_empty(data[k])]
                    if missing:
                        why = f'顶层缺必需键（或值为空）: {missing}'
                    else:
                        try:
                            obj = model_cls.model_validate(data)
                        except ValidationError as e:
                            why = f'不合 {model_cls.__name__} 的契约: {e}'

                if why is None:
                    # ⚠⚠ **判定可用之后才写缓存。** 见 `_call_keyed`。
                    if not cached:
                        self._keep(key, resp)
                    return obj

                # ⚠ **每一条不可用的路径都 evict**（包括解析失败这条——它原来没有）。
                #   留着它，下一次不带 `--no-cache` 的重跑会原样读回来，
                #   而调用方看到的是一模一样的失败：**那不是重试，是把同一个结果读了两遍**。
                self.cache.evict(key)
                errors.append(f'{head} {why}')
                if not cached:
                    break                      # 真发的也坏 ⇒ 这一档确实不行，降一档
        raise LlmFailed(
            f'{self.ctx.node}/{purpose}: 走完 {len(LADDER)} 档阶梯仍然没有可用的 JSON。\n  '
            + '\n  '.join(errors)
            + f'\n⚠ 必需顶层键来自 {model_cls.__name__}（判据 39：每个结构化调用点'
              f'声明【全部】必需顶层键，而这一条现在由派生保证）')

    # ── 一次调用：缓存 → 传输层重试 → 记账 ────────────────────────────────

    def _call_keyed(self, system: str, user: str, *, effort: str, thinking: bool,
                    json_mode: bool, max_tokens: int, purpose: str,
                    use_cache: bool = True,
                    progress: RequestProgress | None = None
                    ) -> tuple[ChatResponse, str, bool]:
        """发一次（或读一次缓存）。返回 `(响应, 缓存键, 这一份是不是缓存来的)`。

        ⚠⚠ **它不再写缓存。** 原来这里拿到响应就 `put`，而调用方**还没判断它可不可用**：
        于是四档阶梯各自拿回一份截断 JSON 时，盘上留下的是**四份毒缓存**——
        此后一次完全健康的重跑会 0 次真调用、以一模一样的方式失败，
        不带 `--no-cache` 永远走不出来。
        ⇒ 写缓存的时机挪到「判定可用之后」，由两个入口各自调 `_keep`。
        `cached` 这一格是给它们用的：读回来的那一份不必再写一遍。

        `use_cache=False` 是**同一档的第二次**（见两个入口里的 `for use_cache in (True, False)`）：
        ⚠ 一份**读回来之后才发现不可用**的缓存，是一次**发现得晚的 miss**，不是模型的毛病。
        光 evict 不重发的话，四档会各自读回自己那份毒缓存、各自 evict、然后整体失败——
        报出来的症状是「模型连着四档都吐截断 JSON」，而**事实是缓存坏了、模型一次都没被问到**。
        「派生症状盖住一句话能说清的事实」正是本项目反复付学费的那个形状。
        ⇒ 缓存那份不可用 ⇒ 同一档真发一次；真发的那份也不可用 ⇒ 这一档确实不行，降一档。
        """
        req = ChatRequest(model=self.model, system=system, user=user,
                          reasoning_effort=effort, thinking=thinking,
                          json_mode=json_mode, max_tokens=max_tokens)
        key = cache_key(req)
        policy: CachePolicy = self.ctx.cache

        if use_cache and policy.read:
            hit = self.cache.get(key)
            if hit is not None:
                # ⚠ **缓存命中也要记一行。** 不记的话，「这次跑读了 31 份缓存」与
                #   「这次跑什么都没做」在账本上长得一模一样，而这两件事的下一步完全不同。
                self._record_cache_hit(req, hit, purpose=purpose, progress=progress)
                return hit, key, True

        # ⚠ 记账在 `_send_with_retry` 里逐次发生（那里才知道自己发了几次）。
        return self._send_with_retry(
            req, purpose=purpose, progress=progress), key, False

    def _keep(self, key: str, resp: ChatResponse) -> None:
        """把一份**已经判定可用**的响应写进缓存。全类唯一的写口。"""
        if self.ctx.cache.write:
            self.cache.put(key, resp)      # 空 content 由 `put` 自己再拒一道

    def _send_with_retry(self, req: ChatRequest, *, purpose: str,
                         progress: RequestProgress | None = None) -> ChatResponse:
        """**传输层**重试。与降级阶梯是两条独立的循环（见模块 docstring 末尾）。

        ⚠ 只重试 `retryable` 的（429 / 5xx / 超时）。4xx 里其余的是**我们自己发错了**，
        重试只会把同一个错误再犯三遍，而每一遍都可能计费。

        ⚠⚠ **每一次上线都记一行**，成功与失败都记。原来只在整轮成功之后记一次：
        传输层实际发了 3 次而账上只有 1 次，整轮失败则一行都没有——
        于是「限流限了半天」与「一次就过」在账本上同形，
        而「彻底失败」与「什么都没干」也同形。这一层是**唯一**知道自己发了几次的地方。

        ⚠ 本方法是重试的**唯一所有者**：SDK 那一层已经 `max_retries=0`
        （见 `transport.py::_sdk`），不然两层各退各的避，一次调用能打出 12 次 HTTP。
        """
        last: TransportError | None = None
        max_attempts = self.max_retries + 1
        for attempt_index in range(max_attempts):
            attempt = attempt_index + 1
            backoff_seconds: float | None = None
            with self._gate:                       # 并发闸：守的是钱不是速率
                heartbeat = AttemptHeartbeat(
                    self.ctx.observe, purpose=purpose, attempt=attempt,
                    max_attempts=max_attempts, progress=progress,
                    interval=self.heartbeat_interval, clock=self._clock)
                heartbeat.start()
                started = self._clock()
                try:
                    resp = self.transport.send(req)
                except TransportError as e:
                    last = e
                    latency_ms = max(0, round((self._clock() - started) * 1000))
                    will_retry = e.retryable and attempt < max_attempts
                    if will_retry:
                        backoff_seconds = self.backoff_base * (2 ** attempt_index)
                    try:
                        self._record_wire(
                            req, None, error=e, purpose=purpose, attempt=attempt,
                            max_attempts=max_attempts, latency_ms=latency_ms,
                            will_retry=will_retry,
                            backoff_ms=(round(backoff_seconds * 1000)
                                        if backoff_seconds is not None else None),
                            progress=progress)
                    finally:
                        heartbeat.finish(outcome='error', error_kind=e.kind)
                    if not will_retry:
                        raise
                except BaseException:
                    heartbeat.finish(outcome='error', error_kind='unexpected')
                    raise
                else:
                    latency_ms = max(0, round((self._clock() - started) * 1000))
                    try:
                        self._record_wire(
                            req, resp, error=None, purpose=purpose, attempt=attempt,
                            max_attempts=max_attempts, latency_ms=latency_ms,
                            will_retry=False, backoff_ms=None, progress=progress)
                    finally:
                        heartbeat.finish(outcome='ok')
                    return resp
            # ⚠ 睡在信号量**外面**：在闸里睡会把整跑串行化（并发闸守的是钱，不是速率）。
            assert backoff_seconds is not None
            self._sleep(backoff_seconds)
        raise last if last else LlmFailed('unreachable')

    def _record_wire(self, req: ChatRequest, resp: ChatResponse | None, *,
                     error: TransportError | None, purpose: str,
                     attempt: int, max_attempts: int, latency_ms: int,
                     will_retry: bool, backoff_ms: int | None,
                     progress: RequestProgress | None) -> None:
        """一次**真上线**记一行。失败那次 token 三项全 `None`——它没拿回任何读数。"""
        u = (resp.usage if resp else None) or {}
        self.ctx.ledger.record(
            node=self.ctx.node, sid=self.ctx.sid,
            model=(resp.model if resp else '') or self.model,
            code=self.ctx.code, cached=False, cache_policy=self.ctx.cache.name,
            prompt_tokens=u.get('prompt_tokens'),
            completion_tokens=u.get('completion_tokens'),
            reasoning_tokens=(u.get('reasoning_tokens')
                              or (u.get('completion_tokens_details') or {}).get('reasoning_tokens')),
            purpose=purpose,
            outcome='ok' if error is None else 'error',
            error=None if error is None else str(error),
            status=None if error is None else error.status,
            reasoning_effort=req.reasoning_effort,
            thinking=req.thinking,
            json_mode=req.json_mode,
            max_tokens=req.max_tokens,
            finish_reason=None if resp is None else resp.finish_reason,
            attempt=attempt,
            max_attempts=max_attempts,
            latency_ms=latency_ms,
            error_kind=None if error is None else error.kind,
            retryable=None if error is None else error.retryable,
            will_retry=will_retry,
            backoff_ms=backoff_ms,
            progress=progress,
        )

    def _record_cache_hit(self, req: ChatRequest, resp: ChatResponse, *,
                          purpose: str, progress: RequestProgress | None) -> None:
        """一次缓存命中记一行（0 元）。**不记的话，「读了 31 份缓存」与「什么都没做」同形。**"""
        u = resp.usage or {}
        self.ctx.ledger.record(
            node=self.ctx.node, sid=self.ctx.sid, model=resp.model or self.model,
            code=self.ctx.code, cached=True, cache_policy=self.ctx.cache.name,
            prompt_tokens=u.get('prompt_tokens'),
            completion_tokens=u.get('completion_tokens'),
            reasoning_tokens=(u.get('reasoning_tokens')
                              or (u.get('completion_tokens_details') or {}).get('reasoning_tokens')),
            purpose=purpose,
            reasoning_effort=req.reasoning_effort,
            thinking=req.thinking,
            json_mode=req.json_mode,
            max_tokens=req.max_tokens,
            finish_reason=resp.finish_reason,
            progress=progress,
        )
