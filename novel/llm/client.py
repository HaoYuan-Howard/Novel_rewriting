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
from contextlib import contextmanager
from typing import Any, Callable, Final, Iterator, TypeVar

from pydantic import BaseModel, ValidationError

from novel import config
from novel.errors import TerminalState
from novel.llm.budget import BudgetChecker, enforce_request_budget
from novel.llm.cache import CachePolicy, ResponseCache, cache_key
from novel.llm.prompts import required_top_level
from novel.llm.transport import ChatRequest, ChatResponse, Transport, TransportError
from novel.observability import (
    AttemptHeartbeat, DEFAULT_HEARTBEAT_SECONDS, RequestProgress,
)
from novel.run import RunContext

#: **降级阶梯，全仓唯一一份。** `(reasoning_effort, thinking)`，按顺序走。
#: `high→low` 是**给正文腾预算**（见模块 docstring ②），
#: 最后那档关掉 thinking 是「推理流本身就是问题源」时的最后一手。
#: ⚠ **全空则抛**，绝不返回空稿——一份空稿落下去，下游是确定性代码，
#: 它不会像写手那样将就着用，它会在几层之外炸，或者更坏：静默产出一份缺内容的东西。
#:
#: ⚠⚠ **这里曾有第二档 `('medium', True)`，逐字照抄自原型，而它是一次白花的钱。**
#: 本模型的真实档位是 `low` / `high` / `max` 三档；`medium`（还有 `xhigh`）**是合法参数、
#: 但服务端静默映射回 `high`** ⇒ 它不报错、不重试、看起来一切正常，
#: 而 `cache_key` 含 `reasoning_effort`（`novel/llm/transport.py::ChatRequest.cache_payload`）
#: ⇒ 它与首档是**两个缓存键、同一种模型行为**。后果：首档拿回空响应/截断之后，
#: 第二档换个键把**同样的行为**再跑一遍，大概率同样失败，**一分预算都没腾出来**——
#: 而「降 effort 就是给正文腾预算」正是这条阶梯管用的全部理由。
#: 四档里只有三种行为，却付四次钱。⇒ 删掉那一档。
#:
#: ⚠ **`max` 刻意不在梯上，那不是遗漏。** 它是**升档**：带 79 token 的官方 effort 前缀
#: （`novel/llm/budget.py` 记着它被删的位置与恢复方式），且那段前缀明令模型写出完整推敲过程
#: ⇒ reasoning 只会更多。而这条阶梯扛的两类故障里有一类**就是**「推理吃光了 `max_tokens`
#: 预算、正文写到一半截断」——拿 `max` 去救它是火上浇油。
#: 要用 `max` 是**质量实验**那条路（`docs/REQUIREMENTS.md` FC-13），不是救场路。
LADDER: Final[tuple[tuple[str, bool], ...]] = (
    ('high', True), ('low', True), ('low', False),
)

#: 关思考阶梯：**单档**。`('low', False)` 是仓内唯一实测验证过的关思考档
#: （`novel/stages/stage6/hook.py::CANDIDATE_RETRY_RUNGS` 同款；这里曾指向
#: `stage5/write.py::TRIM_RUNGS`，那个常量随收缩臂整臂下线一起删了）；
#: 阶梯扛的两类故障全由推理流引起
#: （见模块 docstring ①②），关掉 thinking 后没有降级对象——复制同档当第二档
#: 等于把「重试」混进「阶梯」，而那两条循环不许合并（见上）。
#: 选用哪条由 `DEEPSEEK_THINKING` 决定（`novel/config.py::THINKING_MODES`）。
NOTHINK_RUNGS: Final[tuple[tuple[str, bool], ...]] = (('low', False),)


def rungs_from_effort(effort: str | None) -> tuple[tuple[str, bool], ...]:
    """节点的首档覆盖 → 仍可向下退让的阶梯。

    ``None`` 与 ``high`` 都保留全局默认。覆盖只裁掉更高档，不另造第二份阶梯；
    因而截断/空响应时仍能走现有降级路径。

    ⚠ **能降的只有一档**（`low`）——本模型带 thinking 的档位就 `low`/`high` 两个，
    `max` 是升档且不在梯上（见 `LADDER`）。取值面窄不是漏写：
    `medium`/`xhigh` 虽然 API 收，但服务端把它们映射回 `high`
    ⇒ 允许它们等于允许一个**看起来降了、实际没降**的配置，而账本上分辨不出来。
    """
    if effort in (None, 'high'):
        return LADDER
    if effort != 'low':
        raise ValueError(
            f'节点 effort={effort!r}；可选 low / high / None。'
            f'⚠ `medium` 与 `xhigh` 会被服务端静默映射回 `high`——这里关闭式拒绝它们，'
            f'免得一个「降了档」的节点配置其实一档没降；`max` 是升档，'
            f'要用它先按 `novel/llm/budget.py` 的记录补回官方 effort 前缀')
    start = next(i for i, (name, thinking) in enumerate(LADDER)
                 if name == effort and thinking)
    return LADDER[start:]

#: 默认 token 上限。**统一 64k**：`max_tokens` 在推理模型上同时约束 reasoning + completion，
#: 高预算防止长正文/推理被 API 默认上限**悄悄**截断。确需更小上限时显式传。
DEFAULT_MAX_TOKENS: Final = 64_000

#: 内容闸的机器可数名。**取值面只有这一份**，`llm_rung_rejected` 事件的 `gate` 取自它。
#: 与 `ask_json` docstring 里那三道一一对应，加上 `validator`（可选的第四道）与
#: `ask_text` 独有的 `empty_text`。
REJECT_GATES: Final[tuple[str, ...]] = (
    'parse',        # ① json.loads 失败——截断的典型形态（finish_reason='length'）
    'require',      # ② 空壳闸：顶层缺必需键，或值结构上是空的
    'schema',       # ③ model_validate：形状 / 类型 / extra='forbid'
    'validator',    # ④ 调用点自带的那一道（只有部分调用点传）
    'empty_text',   # ask_text 专有：正文为空
)

#: 降档理由进事件时的截断长度。`ValidationError` 的文本可以有几千字，而观测事件是
#: **一行 JSON**、要能被作业日志与 `grep` 消化 ⇒ 截断并标记，不整段灌进去。
REJECT_REASON_MAX: Final = 400


M = TypeVar('M', bound=BaseModel)
TextValidator = Callable[[str, ChatResponse], str | None]
JsonValidator = Callable[[M, ChatResponse], str | None]


class LlmFailed(TerminalState):
    """整条阶梯走完仍然没拿到可用的东西。**显式终态，绝不返回空稿。**

    ⚠⚠ 基类是 `TerminalState` 而不是 `NovelError`，而这一位承重：
    `novel/cli/main.py` 只捕 `TerminalState` / `RefusedByStructure` / `Exception`，
    挂在 `NovelError` 上就落最后那条兜底 ⇒ **exit 70 + 一段栈**，
    编排层与人都会读成「进程崩了，结论未知」。
    而它说的其实是「按设计这一步走不下去了」——`exit_codes.py` 的 `2`，不打栈。
    ⚠ 这不是把 bug 说成正常：真正未捕获的异常仍走 70 且栈留全，两条各有守卫。
    ⚠ `MissingApiKey`（`novel/llm/transport.py`）早就是同样的归属，本条与它对齐。
    ⚠ 归族改了**不影响 HTTP 那一侧**：`novel/api/errors.py` 是
    `STATUS.get(type(exc))` 精确类型查表、不走 MRO，`LlmFailed` 仍只登记在
    `UNREACHABLE_OVER_HTTP`（它只在子进程里抛，父进程只看得到退出码）。
    """


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
                 thinking_mode: str = config.DEFAULT_THINKING_MODE,
                 effort: str | None = None,
                 max_retries: int = config.DEFAULT_MAX_RETRIES,
                 backoff_base: float = config.DEFAULT_BACKOFF_BASE,
                 concurrency: int = config.DEFAULT_CONCURRENCY,
                 warmup: bool = True,
                 sleep: Callable[[float], None] = time.sleep,
                 heartbeat_interval: float = DEFAULT_HEARTBEAT_SECONDS,
                 clock: Callable[[], float] = time.perf_counter,
                 budget_checker: BudgetChecker = enforce_request_budget) -> None:
        # ⚠ 四个默认值**引用** `novel/config.py`，不在这里再写一份字面量。
        #   那个文件抬头就写着「默认值都在这里，别在调用点再写一份」，而这里原来写了
        #   第二份（`3` / `2.0` / `2` / `'deepseek-v4-pro'`）。两份数分家时不会报错——
        #   它只是让「这次跑到底用的哪个并发度」有两个都说得通的答案。
        self.ctx = ctx
        self.transport = transport
        self.cache = cache if cache is not None else ResponseCache()
        self.model = model or config.DEFAULT_MODEL
        # 取值闸放构造期（同下面的 concurrency 闸）：手搭一个 client 也拦得住。
        if thinking_mode not in config.THINKING_MODES:
            raise ValueError(
                f'DEEPSEEK_THINKING（thinking_mode）={thinking_mode!r}：'
                f'思考模式只有 {config.THINKING_MODES}')
        self.thinking_mode = thinking_mode
        #: 默认阶梯。思考开关拧的是**默认起点集**；显式 `rungs` 的优先级不受它影响。
        self._rungs = (NOTHINK_RUNGS if thinking_mode == 'off'
                       else rungs_from_effort(effort))
        self.max_retries = max_retries
        self.backoff_base = backoff_base
        self._sleep = sleep
        if heartbeat_interval < 0:
            raise ValueError(f'heartbeat_interval 不能为负数，实得 {heartbeat_interval}')
        self.heartbeat_interval = heartbeat_interval
        self._clock = clock
        # 预算闸在两个公开入口、任何缓存读之前运行。测试可注入一把合成的精确计数尺，
        # 但生产默认只有 ``enforce_request_budget`` 这一处供应商规格权威。
        self._budget_checker = budget_checker
        #: 并发闸。⚠ 它守的不是速率，是**钱**：一次配置错误（prompt 漂了）在被人看见之前
        #: 能烧掉多少，与并发度成正比。见 `novel/config.py::DEFAULT_CONCURRENCY`。
        if not 1 <= concurrency <= config.MAX_CONCURRENCY:
            raise ValueError(
                f'concurrency 必须在 1..{config.MAX_CONCURRENCY}，实得 {concurrency!r}')
        self.concurrency = concurrency
        self._gate = threading.Semaphore(self.concurrency)
        #: 首发独占闸。⚠ **与 `_gate` 是两件事**：`_gate` 守的是**并发度**（也就是钱），
        #: 这一闸守的是**顺序**。见 `_warmup_gate`。默认开；关掉只该是测试的事。
        self.warmup = warmup
        self._warmup_lock = threading.Lock()
        self._warmed = False
        self._warm_done = threading.Event()

    # ── 两个入口 ──────────────────────────────────────────────────────────

    def ask_text(self, system: str, user: str, *, purpose: str,
                 max_tokens: int = DEFAULT_MAX_TOKENS,
                 validator: TextValidator | None = None,
                 rungs: tuple[tuple[str, bool], ...] | None = None,
                 progress: RequestProgress | None = None,
                 cache_policy: CachePolicy | None = None) -> str:
        """纯文本调用。默认走 `self._rungs`（思考开关在 `LADDER` 与 `NOTHINK_RUNGS`
        之间选定），**全空或全被调用点拒绝则抛**。

        `validator` 是纯文本响应声明「可用」含义的边界：返回 `None` 表示可用，
        返回人话理由表示这一档不可用、应驱逐缓存并降档。它只该判断响应是否完整，
        不能拿文风偏好触发付费重采样。

        `rungs` 只给确有不同成本语义的辅助调用收窄阶梯；默认路径仍走同一份
        `self._rungs`。空阶梯是调用错误，不能伪装成「模型失败」。
        """
        active_rungs = self._rungs if rungs is None else tuple(rungs)
        if not active_rungs:
            raise ValueError(f'{self.ctx.node}/{purpose}: rungs 不能为空')
        self._preflight(system, user, rungs=active_rungs, json_mode=False,
                        max_tokens=max_tokens, purpose=purpose)
        policy = cache_policy or self.ctx.cache
        tried: list[str] = []

        # 先横扫所有档位的已验证缓存，再发任何新请求。真实语义审判暴露过：一章在
        # medium/low 档已有合格缓存，但旧顺序会因为 high miss 立刻重发 high，既重复
        # 付费又污染 A/B 成本。缓存档位表示“哪种模型行为产出”，不是质量优先级。
        for i, (effort, thinking) in enumerate(active_rungs):
            got = self._read_cached(
                system, user, effort=effort, thinking=thinking, json_mode=False,
                max_tokens=max_tokens, purpose=purpose, progress=progress,
                cache_policy=policy)
            if got is None:
                continue
            resp, key = got
            text = resp.text
            why = None if text else '正文为空'
            gate = 'empty_text'
            if why is None and validator is not None:
                why = validator(text, resp)
                gate = 'validator'
            if why is None:
                return text
            self.cache.evict(key)
            self._observe_rung_rejected(
                purpose=purpose, rungs=active_rungs, index=i, source='cache',
                gate=gate, why=why, resp=resp, json_mode=False)
            tried.append(
                f'effort={effort} think={thinking} [缓存] '
                f'finish={resp.finish_reason} reasoning_len={len(resp.reasoning or "")} '
                f'拒绝={why}')

        for i, (effort, thinking) in enumerate(active_rungs):
            resp, key, _cached = self._call_keyed(
                system, user, effort=effort, thinking=thinking, json_mode=False,
                max_tokens=max_tokens, purpose=purpose, use_cache=False,
                progress=progress, cache_policy=policy)
            text = resp.text
            why = None if text else '正文为空'
            gate = 'empty_text'
            if why is None and validator is not None:
                why = validator(text, resp)
                gate = 'validator'
            if why is None:
                self._keep(key, resp, cache_policy=policy)  # ← 判定可用之后才写
                return text
            self.cache.evict(key)
            self._observe_rung_rejected(
                purpose=purpose, rungs=active_rungs, index=i, source='wire',
                gate=gate, why=why, resp=resp, json_mode=False)
            tried.append(
                f'effort={effort} think={thinking} '
                f'finish={resp.finish_reason} reasoning_len={len(resp.reasoning or "")} '
                f'拒绝={why}')
        raise LlmFailed(
            f'{self.ctx.node}/{purpose}: 走完 {len(active_rungs)} 档阶梯仍然拿不到正文'
            '（没有可用响应）。\n  '
            + '\n  '.join(tried)
            + '\n⚠ `reasoning_len` 大而正文为空 = 模型不是没干活，是答案流丢了；'
              '**不从 reasoning 里抠答案**，那里面混着推敲过程，救回来的是半成品')

    def ask_json(self, system: str, user: str, *, model_cls: type[M], purpose: str,
                 require: tuple[str, ...] | None = None,
                 max_tokens: int = DEFAULT_MAX_TOKENS,
                 validator: JsonValidator | None = None,
                 rungs: tuple[tuple[str, bool], ...] | None = None,
                 progress: RequestProgress | None = None,
                 cache_policy: CachePolicy | None = None) -> M:
        """结构化调用。默认走与 `ask_text` **同一条**阶梯（`self._rungs`）；
        只有收缩/裁决这类成本语义明确不同的辅助调用可显式收窄 `rungs`。
        解析失败与「结构上是空的」同等对待。

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
        active_rungs = self._rungs if rungs is None else tuple(rungs)
        if not active_rungs:
            raise ValueError(f'{self.ctx.node}/{purpose}: rungs 不能为空')
        derived = required_top_level(model_cls)
        if require is None:
            require = derived
        elif extra := sorted(set(require) - set(derived)):
            raise ValueError(
                f'{purpose}: require={sorted(require)} 里的 {extra} 不在 '
                f'{model_cls.__name__} 的必需顶层键 {sorted(derived)} 里。'
                f'显式传只许**收窄**——放宽等于给这个调用点开一个「它特殊」的口子')

        policy = cache_policy or self.ctx.cache
        self._preflight(system, user, rungs=active_rungs, json_mode=True,
                        max_tokens=max_tokens, purpose=purpose)

        def inspect_response(
                resp: ChatResponse) -> tuple[M | None, str | None, str, str]:
            """⚠ 第四个返回值是**闸名**（`REJECT_GATES` 之一），不是给人读的。
            `why` 那句话事后要靠人读才能归类，而降档统计要能机器数。"""
            content = _strip_fences(resp.content)
            why = None
            obj = None
            gate = 'parse'
            try:
                data = json.loads(content)
            except json.JSONDecodeError as e:
                why = f'解析失败: {e}'
            else:
                missing = [k for k in require
                           if not isinstance(data, dict) or k not in data
                           or _is_empty(data[k])]
                if missing:
                    why, gate = f'顶层缺必需键（或值为空）: {missing}', 'require'
                else:
                    try:
                        obj = model_cls.model_validate(data)
                    except ValidationError as e:
                        why, gate = f'不合 {model_cls.__name__} 的契约: {e}', 'schema'
                    else:
                        if validator is not None:
                            why, gate = validator(obj, resp), 'validator'
            return obj, why, content, gate

        errors: list[str] = []

        # 同 ask_text：任一档已有合格缓存时，本次必须是 0 次真请求。
        for i, (effort, thinking) in enumerate(active_rungs):
            got = self._read_cached(
                system, user, effort=effort, thinking=thinking, json_mode=True,
                max_tokens=max_tokens, purpose=purpose, progress=progress,
                cache_policy=policy)
            if got is None:
                continue
            resp, key = got
            obj, why, content, gate = inspect_response(resp)
            head = (f'effort={effort} think={thinking} [缓存] '
                    f'finish={resp.finish_reason} clen={len(content)}')
            if why is None:
                return obj
            self.cache.evict(key)
            self._observe_rung_rejected(
                purpose=purpose, rungs=active_rungs, index=i, source='cache',
                gate=gate, why=why, resp=resp, json_mode=True)
            errors.append(f'{head} {why}')

        for i, (effort, thinking) in enumerate(active_rungs):
            resp, key, _cached = self._call_keyed(
                system, user, effort=effort, thinking=thinking, json_mode=True,
                max_tokens=max_tokens, purpose=purpose, use_cache=False,
                progress=progress, cache_policy=policy)
            obj, why, content, gate = inspect_response(resp)
            head = (f'effort={effort} think={thinking} '
                    f'finish={resp.finish_reason} clen={len(content)}')
            if why is None:
                self._keep(key, resp, cache_policy=policy)
                return obj
            # ⚠ **每一条不可用的路径都 evict**（包括解析失败这条——它原来没有）。
            self.cache.evict(key)
            self._observe_rung_rejected(
                purpose=purpose, rungs=active_rungs, index=i, source='wire',
                gate=gate, why=why, resp=resp, json_mode=True)
            errors.append(f'{head} {why}')
        raise LlmFailed(
            f'{self.ctx.node}/{purpose}: 走完 {len(active_rungs)} 档阶梯仍然没有可用的 JSON。\n  '
            + '\n  '.join(errors)
            + f'\n⚠ 必需顶层键来自 {model_cls.__name__}（判据 39：每个结构化调用点'
              f'声明【全部】必需顶层键，而这一条现在由派生保证）')

    def _observe_rung_rejected(self, *, purpose: str,
                               rungs: tuple[tuple[str, bool], ...], index: int,
                               source: str, gate: str, why: str,
                               resp: ChatResponse, json_mode: bool) -> None:
        """降一档时留下【为什么降】。**这条事件是降档理由的唯一出口。**

        ⚠⚠ 它补的是一个实测踩过的观测洞：内容闸判定不可用时，这里只做
        `cache.evict` + 降一档，**既不产事件也不写日志**；而账本那一行记的是
        **传输层**的结论（`outcome=ok` / `finish_reason=stop` / `will_retry=false`），
        读起来像「这一次很成功」。⇒ 2026-08-19 复盘 `stage4.plan` 那次降档时，
        理由在三处同时不在：账本只有传输层、降档路径不出声、被拒的那一档响应
        随即被 `ResponseCache.evict` 从盘上删掉；`errors` / `tried` 只有在
        **所有档全灭**时才随 `LlmFailed` 浮出来，而降档成功那一路根本走不到。
        ⇒ 降档是**模型行为层**的判定，它得有自己的出口，不能借账本那一行。

        ⚠ 事件里**不带响应正文**：被拒的那份可能是半截 JSON 或整段推敲过程，
        灌进作业日志既没用又可能把正文写进不该写的地方。只带闸名、截断后的理由
        与几个长度读数——够回答「哪一道闸、拒的是什么形状」。
        """
        effort, thinking = rungs[index]
        nxt = rungs[index + 1] if index + 1 < len(rungs) else None
        self.ctx.observe(
            'llm_rung_rejected',
            purpose=purpose,
            reasoning_effort=effort,
            thinking=thinking,
            json_mode=json_mode,
            source=source,                    # cache | wire —— 缓存那一趟也会拒
            gate=gate,                        # REJECT_GATES 之一，机器可数
            reason=why[:REJECT_REASON_MAX],
            reason_truncated=len(why) > REJECT_REASON_MAX,
            finish_reason=resp.finish_reason,
            content_chars=len(resp.content or ''),
            reasoning_chars=len(resp.reasoning or ''),
            next_effort=None if nxt is None else nxt[0],
            next_thinking=None if nxt is None else nxt[1],
            # 最后一档也被拒 ⇒ 这一趟走完了，调用方要么进真跑那一趟、要么 LlmFailed。
            exhausted=nxt is None,
        )

    # ── 一次调用：缓存 → 传输层重试 → 记账 ────────────────────────────────

    def _preflight(self, system: str, user: str, *,
                   rungs: tuple[tuple[str, bool], ...], json_mode: bool,
                   max_tokens: int, purpose: str) -> None:
        """把整条降级阶梯一次验完；返回前没有缓存、账本、网络或文件副作用。"""
        self.ctx.require_observed_roots_current()
        requests = tuple(
            ChatRequest(
                model=self.model,
                system=system,
                user=user,
                reasoning_effort=effort,
                thinking=thinking,
                json_mode=json_mode,
                max_tokens=max_tokens,
            )
            for effort, thinking in rungs
        )
        self._budget_checker(requests, node=self.ctx.node, purpose=purpose)

    def _read_cached(self, system: str, user: str, *, effort: str, thinking: bool,
                     json_mode: bool, max_tokens: int, purpose: str,
                     progress: RequestProgress | None,
                     cache_policy: CachePolicy | None = None,
                     ) -> tuple[ChatResponse, str] | None:
        """只读一个档位的缓存；miss 绝不顺手上线。"""
        policy = cache_policy or self.ctx.cache
        if not policy.read:
            return None
        req = ChatRequest(model=self.model, system=system, user=user,
                          reasoning_effort=effort, thinking=thinking,
                          json_mode=json_mode, max_tokens=max_tokens)
        key = cache_key(req)
        hit = self.cache.get(key)
        if hit is None:
            return None
        self._record_cache_hit(
            req, hit, purpose=purpose, progress=progress, cache_policy=policy)
        return hit, key

    def _call_keyed(self, system: str, user: str, *, effort: str, thinking: bool,
                    json_mode: bool, max_tokens: int, purpose: str,
                    use_cache: bool = True,
                    progress: RequestProgress | None = None,
                    cache_policy: CachePolicy | None = None,
                    ) -> tuple[ChatResponse, str, bool]:
        """发一次（或读一次缓存）。返回 `(响应, 缓存键, 这一份是不是缓存来的)`。

        ⚠⚠ **它不再写缓存。** 原来这里拿到响应就 `put`，而调用方**还没判断它可不可用**：
        于是四档阶梯各自拿回一份截断 JSON 时，盘上留下的是**四份毒缓存**——
        此后一次完全健康的重跑会 0 次真调用、以一模一样的方式失败，
        不带 `--refresh` 永远走不出来。
        ⇒ 写缓存的时机挪到「判定可用之后」，由两个入口各自调 `_keep`。
        `cached` 这一格是给它们用的：读回来的那一份不必再写一遍。

        两个公开入口会先用 `_read_cached` 横扫全部档位；只有没有合格缓存时才以
        `use_cache=False` 调这里。保留 `use_cache=True` 仅维持本私有帮助函数的完整语义，
        不能拿它恢复“某档 miss 就立刻上线”的旧顺序。
        """
        req = ChatRequest(model=self.model, system=system, user=user,
                          reasoning_effort=effort, thinking=thinking,
                          json_mode=json_mode, max_tokens=max_tokens)
        key = cache_key(req)
        policy = cache_policy or self.ctx.cache

        if use_cache and policy.read:
            hit = self.cache.get(key)
            if hit is not None:
                # ⚠ **缓存命中也要记一行。** 不记的话，「这次跑读了 31 份缓存」与
                #   「这次跑什么都没做」在账本上长得一模一样，而这两件事的下一步完全不同。
                self._record_cache_hit(
                    req, hit, purpose=purpose, progress=progress,
                    cache_policy=policy)
                return hit, key, True

        # ⚠ 记账在 `_send_with_retry` 里逐次发生（那里才知道自己发了几次）。
        return self._send_with_retry(
            req, purpose=purpose, progress=progress,
            cache_policy=policy), key, False

    def _keep(self, key: str, resp: ChatResponse, *,
              cache_policy: CachePolicy | None = None) -> None:
        """把一份**已经判定可用**的响应写进缓存。全类唯一的写口。"""
        policy = cache_policy or self.ctx.cache
        if policy.write:
            self.cache.put(key, resp)      # 空 content 由 `put` 自己再拒一道

    def _send_with_retry(self, req: ChatRequest, *, purpose: str,
                         progress: RequestProgress | None = None,
                         cache_policy: CachePolicy | None = None) -> ChatResponse:
        """**传输层**重试。与降级阶梯是两条独立的循环（见模块 docstring 末尾）。

        ⚠ 只重试 `retryable` 的（429 / 5xx / 超时）。4xx 里其余的是**我们自己发错了**，
        重试只会把同一个错误再犯三遍，而每一遍都可能计费。

        ⚠⚠ **每一次上线都记一行**，成功与失败都记。原来只在整轮成功之后记一次：
        传输层实际发了 3 次而账上只有 1 次，整轮失败则一行都没有——
        于是「限流限了半天」与「一次就过」在账本上同形，
        而「彻底失败」与「什么都没干」也同形。这一层是**唯一**知道自己发了几次的地方。

        ⚠ 本方法是重试的**唯一所有者**：SDK 那一层已经 `max_retries=0`
        （见 `transport.py::_sdk`），不然两层各退各的避，一次调用能打出 12 次 HTTP。

        ⚠ 首发独占闸包在最外层（`_warmup_gate`）：它守的是**顺序**，而重试守的是**网络**。
        与降级阶梯同理，**三条不许合并**——合并之后一次限流退避会把整批章都堵在闸里。
        """
        with self._warmup_gate():
            return self._send_attempts(
                req, purpose=purpose, progress=progress, cache_policy=cache_policy)

    @contextmanager
    def _warmup_gate(self) -> Iterator[None]:
        """**首发独占**：第一次真上线跑完，其余线程才放行。一个 client 只发生一次。

        【它省的是什么】供应商的上下文缓存按**字节前缀**命中，而那份前缀要等一次请求
        **完成**之后才写得进去。逐章扇出（`stage2.blueprint` / `stage5.write`，默认并发 32）
        把 N 章**同时**发出去时，N 份请求共享的那一大段 system 前缀**谁也命中不了谁**——
        实测 `stage2/prompts.py::BLUEPRINT_SYS` 4073 token、`stage5` 的臂 SYS 约 1900 token，
        一本 20 章的书因此白付 19 份。⇒ 拿第一发的串行，换后面 N−1 发的前缀命中。

        ⚠⚠ **`finally` 里无条件放行。** 首发走完整条降级阶梯仍然失败（或抛任何异常）时，
        其余线程必须继续跑：不放行就是整批死等一个已经死掉的请求，而那比多花一次钱坏得多。
        ⚠ **只有真上线才消耗名额**：本地缓存命中走 `_read_cached`，根本进不到这里——
        一次全命中的重跑不会因为这道闸而串行。
        ⚠ 单次调用的节点（`stage2.global` / `stage4.plan` / `stage6.hook` / `stage7.rename`）
        本来就只发一次，这道闸对它们是恒等的。
        ⚠ **不给它 CLI flag**：用户已裁决默认接受这一次串行的墙钟，而一个恒真的 flag 是误导
        （同 `cache.py::CachePolicy.disabled` 那条）。构造参数留着只为让测试关掉它。
        """
        if not self.warmup:
            yield
            return
        with self._warmup_lock:
            first = not self._warmed
            self._warmed = True             # 认领首发；后来者一律等
        if not first:
            self._warm_done.wait()
            yield
            return
        try:
            yield
        finally:
            self._warm_done.set()

    def _send_attempts(self, req: ChatRequest, *, purpose: str,
                       progress: RequestProgress | None = None,
                       cache_policy: CachePolicy | None = None) -> ChatResponse:
        """重试循环本体。**语义全部写在 `_send_with_retry` 的 docstring 里**，
        拆出来只是为了让首发闸不必把整个循环缩进一层。"""
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
                            progress=progress, cache_policy=cache_policy)
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
                            will_retry=False, backoff_ms=None, progress=progress,
                            cache_policy=cache_policy)
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
                     progress: RequestProgress | None,
                     cache_policy: CachePolicy | None = None) -> None:
        """一次**真上线**记一行。失败那次 token 三项全 `None`——它没拿回任何读数。"""
        u = (resp.usage if resp else None) or {}
        policy = cache_policy or self.ctx.cache
        self.ctx.ledger.record(
            node=self.ctx.node, sid=self.ctx.sid,
            model=(resp.model if resp else '') or self.model,
            code=self.ctx.code, cached=False, cache_policy=policy.name,
            prompt_tokens=u.get('prompt_tokens'),
            prompt_cache_hit_tokens=u.get('prompt_cache_hit_tokens'),
            prompt_cache_miss_tokens=u.get('prompt_cache_miss_tokens'),
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
                          purpose: str, progress: RequestProgress | None,
                          cache_policy: CachePolicy | None = None) -> None:
        """一次缓存命中记一行（0 元）。**不记的话，「读了 31 份缓存」与「什么都没做」同形。**"""
        u = resp.usage or {}
        policy = cache_policy or self.ctx.cache
        self.ctx.ledger.record(
            node=self.ctx.node, sid=self.ctx.sid, model=resp.model or self.model,
            code=self.ctx.code, cached=True, cache_policy=policy.name,
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
