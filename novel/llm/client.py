#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
client.py —— `ask_text` / `ask_json`。**降级阶梯只有一份，两个入口共用。**

【降级阶梯扛的是两类实测发生过的故障，它们看起来完全不像同一件事】

  ① **答案流丢了。** DeepSeek 会把整段答案写进 `reasoning_content` 而 `content` 返回**空串**，
     `finish_reason='stop'`——**不是截断**。实测：模型把整章正文写进了推理里。
     ⚠ 不从 `reasoning` 里把答案抠出来。**但那句老理由已经不成立了，别再照抄**：
       「里面混着推敲过程、救回来是半成品」——2026-08-22 的留证把它证伪了。实测
       （`stage4.plan` / flash / `high`，3 份留证，`docs/REQUIREMENTS.md` TODO-40）：
       推理通道里那段**整体就是一份合法 JSON**，23 章齐全、括号配平、**一个字推敲过程
       都没混进去**，其中 1 份**完全合 `PlanResponse` 契约**——那一次丢掉的是一份**可用的计划**。
       旧说法的射程其实只到「从没测过」：空响应根本不落盘，没有任何人看得见那段。
       ⇒ **行为一格未改**，规矩仍然是不抠。改不改是**产品语义裁决**，不是一句注释能改的：
       n=3、一本书、一个节点、一个档位，且只量过 `ask_json` 这一侧——
       `ask_text` 的自然语言响应是不是同样形状，一次都没量过。
     ⇒ 2026-08-22 起它在账上**有自己的名字**（闸名 `answer_lost`，见 `REJECT_GATES`），
       且整份响应会落一份留证（`novel/paths.py::answer_lost_path`）。在此之前它与
       ②「JSON 被截断」共用 `parse` 一格，而这两类的处置正好相反。
     ⇒ 2026-08-20 起这一档先**同档重采一次**（`MAX_SAMPLES_PER_RUNG`）再决定降档；
     2026-08-22 起**除截断外的每一种拒绝**都走这条重采（见 `_worth_resampling`）。
     实测（flash 迁移，三本书）：`stage4.plan` 的 `high` 档空正文而 `reasoning_chars`
     上万、`finish_reason=stop`，旧阶梯把它当「这一档不行」直接降档 ⇒ 3 本里 2 本的
     计划**从未在 `high` 档被评估过**，一上来就掉进取证能力受损的 `low`。
     温度采样下同参数重发拿到的是新样本；降档换的是模型行为——两件事。

  ② **JSON 被截断。** `max_tokens` 在推理模型上**同时约束 reasoning + completion**。
     `reasoning_effort='high'` 时模型可能把大半预算花在思考上，正文写到一半就到顶了，
     于是**截断表现为 JSON 解析失败**（`finish_reason='length'`）。
     ⇒ 降 effort 就是**给正文腾预算**，这才是阶梯管用的原因。
     ⚠ 它是**唯一不做同档重采**的一类：同参数重采只会把推理再写满一遍。

  ③ **合法 JSON 但结构上是空的。** 这一类与①②**必须走同一条阶梯**（原型 F-125）。
     实测：某次规划调用拿回 `{"reconstruction_strategy": "…"}`——0 个输出章、正常结束、
     4477 token 里推理占 4317。它是合法 JSON，于是一路放行**并进了缓存**；
     下游把它摊成 **74 条**「这一拍没有给预算」。**74 条派生症状盖住了一句话能说清的事实。**
     ⚠ 它与①同源（想得多、交得少），因此**也先同档重采**再降档——
     `stage4.plan` 实测 `high` 档被拒后直接降档，白花掉整条阶梯再回 `high` 才成功。

⚠⚠ **原型把这条阶梯在两个入口各写了一遍。** 两份逐行相同的东西必然分家——
本文件只有一份 `LADDER`，两个入口都从它走。

【阶梯与传输层重试是两条独立的循环，不许合并】
阶梯换的是**模型行为**（effort / thinking），重试扛的是**网络**（429 / 5xx / 超时）。
混成一条，一次网络抖动就会消耗掉整条阶梯——而阶梯救不了连接被重置。
"""
from __future__ import annotations

import hashlib
import json
import threading
import time
from contextlib import contextmanager
from datetime import datetime, timezone
from typing import Any, Callable, Final, Iterator, TypeVar
from uuid import uuid4

from pydantic import BaseModel, ValidationError

from novel import config
from novel.errors import TerminalState
from novel.llm.budget import BudgetChecker, enforce_request_budget
from novel.llm.cache import CachePolicy, ResponseCache, cache_key
from novel.llm.keepsake import write_answer_lost
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

#: 答案流丢失（模块 docstring ①）时，同一档最多采几个样本。**2 = 首发 + 一次重采。**
#:
#: ⚠ 这是「重采」不是「重试」：重试扛**网络**（`_send_attempts`，429/5xx/超时），
#: 重采换的是**同参数下的新样本**，降档换的是**模型行为**——三条循环各管各的，不许合并
#: （合并一次网络抖动就会烧掉两份采样预算）。`tools/current_corpus_paid_preview.py`
#: 按这一格放大 wire 硬上限——改它必须同批改那边的口径。
MAX_SAMPLES_PER_RUNG: Final = 2


def _worth_resampling(resp: ChatResponse) -> bool:
    """这一档被拒之后，值不值得**同档再采一个样本**（而不是直接降档）。

    规则只有一条例外：**截断（`finish_reason='length'`）不重采。**
    那是模块 docstring ②——要腾的是 `max_tokens` 预算，同参数重采大概率把推理
    再写满一遍；降 effort 才是给正文腾预算。其余一律重采一次：①的答案流丢失、
    ③的空壳，以及 `parse` / `schema` / `validator` / `empty_text` 这些形状拒绝。

    ⚠⚠ **这里把 ① 已经确立的结论推广到了 ③ 与内容闸拒，别按旧字面撤回。**
    2026-08-20 版只让 ①（`content` 空 + `stop`）重采，理由写着「非空正文被内容闸拒
    也不重采——那是形状/契约问题，同一参数大概率拿回同一份形状」。三条实测推翻了它：

    · **真发循环 `use_cache=False`** ⇒ 同档重采拿的是新样本，不是缓存里那一份。
      模块 docstring ① 自己就写着「温度采样下同参数重发拿到的是新样本」——
      那句话对 ③ 与内容闸拒同样成立，当时只是没推广过去。同档两次真实调用实测
      completion 29202 vs 33231（`stage4.plan`，2026-08-20），样本确实不同。
    · `stage4.plan` 盘上 5 次带失败的跑里 **3 次是 `high→low→low/nothink→high`**：
      整条阶梯走完**再重来**，而最后成功的那次仍是 `high`。既然第 4 次 high 能过，
      第 2 次 high 就能过——中间两档是白花。
    · 另 2 次以 `high→low` 收尾 ⇒ **最终计划产自降档档位**。那不是省钱是降质，
      同 `docs/REQUIREMENTS.md` TODO-32「最终稿产自哪一档」那条读数。

    盘上 22 次实付合计 completion 378 368，其中 **180 334（47.7%）** 花在没被采纳的档位上。

    ⚠ `MAX_SAMPLES_PER_RUNG` 的值**不动** ⇒ 每档 wire 硬上限仍是 2，
    `tools/current_corpus_paid_preview.py` 的上限口径不受影响；变的只是**触发面**。
    """
    return resp.finish_reason != 'length'


def _answer_lost(text: str, resp: ChatResponse) -> bool:
    """这一份是不是模块 docstring ① 的形态：**正文空，而且不是截断。**

    ⚠ 它只决定**闸名**（`answer_lost` 而不是 `parse` / `empty_text`），
    **不决定走哪条路**——重采与降档仍由 `_worth_resampling` 一家判。两个谓词都看
    `finish_reason` 却各自独立：这里问「这一份是什么」，那里问「下一步做什么」。
    ⇒ 2026-08-22 加这一格时判定式一行没动，走法与从前逐字相同。

    ⚠ 判的是 `!= 'length'` 而不是 `== 'stop'`：要分出来的对立面**只有截断**一个
    （②要腾预算，这一类要换样本）。`stop` 以外那些少见的收尾理由归到哪一边都是猜，
    而猜错的代价不对称——把一次非截断误记成截断，就又把 ① 塞回 `parse` 那一格了。

    ⚠ 传的是**已归一的正文**（`ask_text` 的 `resp.text` / `ask_json` 去掉围栏之后），
    不是 `resp.content`：两个入口对「空」的定义必须与各自闸判的那一份是同一个。
    """
    return not text and resp.finish_reason != 'length'


def rungs_from_effort(effort: str | None) -> tuple[tuple[str, bool], ...]:
    """节点的首档覆盖 → 仍可向下退让的阶梯。

    ``None`` 与 ``high`` 都保留全局默认。覆盖只裁掉更高档，不另造第二份阶梯；
    因而截断/空响应时仍能走现有降级路径。

    ⚠ **能降的只有一档**（`low`）——本模型带 thinking 的档位就 `low`/`high` 两个。
    取值面窄不是漏写：`medium`/`xhigh` 虽然 API 收，但服务端把它们映射回 `high`
    ⇒ 允许它们等于允许一个**看起来降了、实际没降**的配置，而账本上分辨不出来。

    ⚠ `max` 是**升档**：它在这里长成「`max` 起步、再降进 `LADDER`」的覆盖梯，
    而 **`LADDER` 本身一格不动**——那条梯扛的两类故障里有一类就是「推理吃光
    `max_tokens`」，把升档放进降级路是火上浇油。它带 79 token 的官方 effort 前缀
    （`novel/llm/budget.py::_MAX_EFFORT_PREFIX`，2026-08-21 为 FC-13 质量实验取回），
    因此它与其余档位**不同 framing**——预算那一侧要跟着算，别只改这里。
    """
    if effort in (None, 'high'):
        return LADDER
    if effort == 'max':
        return (('max', True),) + LADDER
    if effort != 'low':
        raise ValueError(
            f'节点 effort={effort!r}；可选 low / high / max / None。'
            f'⚠ `medium` 与 `xhigh` 会被服务端静默映射回 `high`——这里关闭式拒绝它们，'
            f'免得一个「降了档」的节点配置其实一档没降')
    start = next(i for i, (name, thinking) in enumerate(LADDER)
                 if name == effort and thinking)
    return LADDER[start:]

#: 默认 token 上限。**统一 64k**：`max_tokens` 在推理模型上同时约束 reasoning + completion，
#: 高预算防止长正文/推理被 API 默认上限**悄悄**截断。确需更小上限时显式传。
DEFAULT_MAX_TOKENS: Final = 64_000

#: 内容闸的机器可数名。**取值面只有这一份**，`llm_rung_rejected` 事件的 `gate` 取自它。
#: 与 `ask_json` docstring 里那三道一一对应，加上 `validator`（可选的第四道），
#: 以及正文为空时那两格——`answer_lost` 与 `empty_text`。
#:
#: ⚠⚠ **`answer_lost` 与 `parse` 曾经是同一格，而它们是两件事。**
#: 空正文照旧进 `json.loads` 必然抛 `JSONDecodeError` ⇒ 模块 docstring ①（答案写进了
#: 推理通道、非截断）与 ②（JSON 被截断）在账上塌成同一个 `parse`。而这两类的处置
#: **本来就相反**：①换新样本（同档重采），②腾预算（降档）——账上却分不出谁是谁，
#: 于是「这个节点到底在坏哪一种」只能靠人去翻 `finish_reason` 手数。
#: 实测代价（2026-08-22 复盘，四份账本 752 次 wire）：flash@high 418 次里 60 次是 ①、
#: pro 255 次里 0 次——一个只在**一个模型的一个档位**上出现的失败模式，
#: 在账面上和「JSON 被截断」长得一模一样。⇒ 按 `finish_reason` 劈开，两边各有名字。
#: ⚠ 劈的是**账**不是**判定**：`_worth_resampling` 一行没动（见 `_answer_lost`）。
REJECT_GATES: Final[tuple[str, ...]] = (
    'answer_lost',  # ⓪ 正文空而**不是截断**：答案写进了 reasoning（模块 docstring ①）
    'parse',        # ① json.loads 失败——截断的典型形态（finish_reason='length'）
    'require',      # ② 空壳闸：顶层缺必需键，或值结构上是空的
    'schema',       # ③ model_validate：形状 / 类型 / extra='forbid'
    'validator',    # ④ 调用点自带的那一道（只有部分调用点传）
    'empty_text',   # ⑤ ask_text 专有：正文空**且是截断**（非截断那半归 ⓪）
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


def _attempt_id() -> str:
    """一次响应获取事件（一次真上线 attempt 或一次缓存命中）的联结键。

    只求进程内外唯一，不求可复算——它联结的是**账本行与审计行**，
    不是缓存键（那份身份归 `cache_key`，两者不许混）。
    """
    return uuid4().hex[:16]


def _response_sha256(resp: ChatResponse | None) -> str | None:
    """响应正文的内容身份。`None` = 这次 attempt 没拿回响应（传输层失败）。

    ⚠ 空 content 也算：空串的哈希把「拿回了空正文」与「没拿回」分开。
    """
    if resp is None:
        return None
    return hashlib.sha256((resp.content or '').encode('utf-8')).hexdigest()


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
        #: 每个 purpose 最终被采纳的那次响应（`attempt_id`）。落盘方
        #: （`extract.py` 等）写 `artifact_adoption` 行时从这里反查——
        #: 一个 run 里一个 purpose 只问一次，后采纳者覆盖前者即是正确语义。
        self._adopt_lock = threading.Lock()
        self._accepted: dict[str, str] = {}

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
            resp, key, attempt_id = got
            text = resp.text
            why = None if text else '正文为空'
            gate = 'answer_lost' if _answer_lost(text, resp) else 'empty_text'
            if why is None and validator is not None:
                why = validator(text, resp)
                gate = 'validator'
            if why is None:
                self._settle_gate(
                    purpose=purpose, attempt_id=attempt_id, source='cache',
                    accepted=True, gate=None, rungs=active_rungs, index=i,
                    progress=progress)
                return text
            self.cache.evict(key)
            self._settle_gate(
                purpose=purpose, attempt_id=attempt_id, source='cache',
                accepted=False, gate=gate, rungs=active_rungs, index=i,
                progress=progress)
            self._observe_rung_rejected(
                purpose=purpose, attempt_id=attempt_id, rungs=active_rungs,
                index=i, source='cache',
                gate=gate, why=why, resp=resp, json_mode=False, progress=progress)
            tried.append(
                f'effort={effort} think={thinking} [缓存] '
                f'finish={resp.finish_reason} reasoning_len={len(resp.reasoning or "")} '
                f'拒绝={why}')

        for i, (effort, thinking) in enumerate(active_rungs):
            # 被拒后先同档重采一次再决定降档（_worth_resampling；截断除外）,
            # 见 MAX_SAMPLES_PER_RUNG。
            for sample in range(1, MAX_SAMPLES_PER_RUNG + 1):
                resp, key, _cached, attempt_id = self._call_keyed(
                    system, user, effort=effort, thinking=thinking, json_mode=False,
                    max_tokens=max_tokens, purpose=purpose, use_cache=False,
                    progress=progress, cache_policy=policy)
                text = resp.text
                why = None if text else '正文为空'
                gate = 'answer_lost' if _answer_lost(text, resp) else 'empty_text'
                if why is None and validator is not None:
                    why = validator(text, resp)
                    gate = 'validator'
                if why is None:
                    self._keep(key, resp, cache_policy=policy)  # ← 判定可用之后才写
                    self._settle_gate(
                        purpose=purpose, attempt_id=attempt_id, source='wire',
                        accepted=True, gate=None, rungs=active_rungs, index=i,
                        sample=sample, progress=progress)
                    return text
                self.cache.evict(key)
                resample = sample < MAX_SAMPLES_PER_RUNG and _worth_resampling(resp)
                self._settle_gate(
                    purpose=purpose, attempt_id=attempt_id, source='wire',
                    accepted=False, gate=gate, rungs=active_rungs, index=i,
                    sample=sample, resample=resample, progress=progress)
                self._observe_rung_rejected(
                    purpose=purpose, attempt_id=attempt_id, rungs=active_rungs,
                    index=i, source='wire',
                    gate=gate, why=why, resp=resp, json_mode=False,
                    resample=resample, sample=sample, progress=progress)
                tried.append(
                    f'effort={effort} think={thinking}{"（重采）" if sample > 1 else ""} '
                    f'finish={resp.finish_reason} reasoning_len={len(resp.reasoning or "")} '
                    f'拒绝={why}')
                if not resample:
                    break
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
            if not content:
                # ⓪ 空正文若照旧往下走，`json.loads('')` 必然抛 ⇒ 落进 `parse`，
                # 把「答案流丢了」与「JSON 被截断」记成同一格（见 `REJECT_GATES`）。
                # ⚠ 先劈开再解析，而**不是**事后按 `finish_reason` 改写闸名：
                # 那样一来 `parse` 这一格的含义会取决于读的人记不记得改写规则。
                return None, '正文为空', content, (
                    'answer_lost' if _answer_lost(content, resp) else 'parse')
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
            resp, key, attempt_id = got
            obj, why, content, gate = inspect_response(resp)
            head = (f'effort={effort} think={thinking} [缓存] '
                    f'finish={resp.finish_reason} clen={len(content)}')
            if why is None:
                self._settle_gate(
                    purpose=purpose, attempt_id=attempt_id, source='cache',
                    accepted=True, gate=None, rungs=active_rungs, index=i,
                    progress=progress)
                return obj
            self.cache.evict(key)
            self._settle_gate(
                purpose=purpose, attempt_id=attempt_id, source='cache',
                accepted=False, gate=gate, rungs=active_rungs, index=i,
                progress=progress)
            self._observe_rung_rejected(
                purpose=purpose, attempt_id=attempt_id, rungs=active_rungs,
                index=i, source='cache',
                gate=gate, why=why, resp=resp, json_mode=True, progress=progress)
            errors.append(f'{head} {why}')

        for i, (effort, thinking) in enumerate(active_rungs):
            # 被拒后先同档重采一次再决定降档（_worth_resampling；截断除外）,
            # 见 MAX_SAMPLES_PER_RUNG。
            for sample in range(1, MAX_SAMPLES_PER_RUNG + 1):
                resp, key, _cached, attempt_id = self._call_keyed(
                    system, user, effort=effort, thinking=thinking, json_mode=True,
                    max_tokens=max_tokens, purpose=purpose, use_cache=False,
                    progress=progress, cache_policy=policy)
                obj, why, content, gate = inspect_response(resp)
                head = (f'effort={effort} think={thinking}'
                        f'{"（重采）" if sample > 1 else ""} '
                        f'finish={resp.finish_reason} clen={len(content)}')
                if why is None:
                    self._keep(key, resp, cache_policy=policy)
                    self._settle_gate(
                        purpose=purpose, attempt_id=attempt_id, source='wire',
                        accepted=True, gate=None, rungs=active_rungs, index=i,
                        sample=sample, progress=progress)
                    return obj
                # ⚠ **每一条不可用的路径都 evict**（包括解析失败这条——它原来没有）。
                self.cache.evict(key)
                resample = sample < MAX_SAMPLES_PER_RUNG and _worth_resampling(resp)
                self._settle_gate(
                    purpose=purpose, attempt_id=attempt_id, source='wire',
                    accepted=False, gate=gate, rungs=active_rungs, index=i,
                    sample=sample, resample=resample, progress=progress)
                self._observe_rung_rejected(
                    purpose=purpose, attempt_id=attempt_id, rungs=active_rungs,
                    index=i, source='wire',
                    gate=gate, why=why, resp=resp, json_mode=True,
                    resample=resample, sample=sample, progress=progress)
                errors.append(f'{head} {why}')
                if not resample:
                    break
        raise LlmFailed(
            f'{self.ctx.node}/{purpose}: 走完 {len(active_rungs)} 档阶梯仍然没有可用的 JSON。\n  '
            + '\n  '.join(errors)
            + f'\n⚠ 必需顶层键来自 {model_cls.__name__}（判据 39：每个结构化调用点'
              f'声明【全部】必需顶层键，而这一条现在由派生保证）')

    def _observe_rung_rejected(self, *, purpose: str, attempt_id: str,
                               rungs: tuple[tuple[str, bool], ...], index: int,
                               source: str, gate: str, why: str,
                               resp: ChatResponse, json_mode: bool,
                               resample: bool = False,
                               sample: int | None = None,
                               progress: RequestProgress | None = None) -> None:
        """降一档（或同档重采）时留下【为什么】。**这条事件是降档理由的唯一出口。**

        ⚠⚠ 它补的是一个实测踩过的观测洞：内容闸判定不可用时，这里只做
        `cache.evict` + 降一档，**既不产事件也不写日志**；而账本那一行记的是
        **传输层**的结论（`outcome=ok` / `finish_reason=stop` / `will_retry=false`），
        读起来像「这一次很成功」。⇒ 2026-08-19 复盘 `stage4.plan` 那次降档时，
        理由在三处同时不在：账本只有传输层、降档路径不出声、被拒的那一档响应
        随即被 `ResponseCache.evict` 从盘上删掉；`errors` / `tried` 只有在
        **所有档全灭**时才随 `LlmFailed` 浮出来，而降档成功那一路根本走不到。
        ⇒ 降档是**模型行为层**的判定，它得有自己的出口，不能借账本那一行。

        ⚠ `resample=True` 表示这一拒的下一步是**同档重采**（`MAX_SAMPLES_PER_RUNG`），
        此时 `next_effort` / `next_thinking` 指回**同一档**——事件必须说真话，
        否则「重采」会被统计口径读成「降档」。

        ⚠ 事件里**不带响应正文**：被拒的那份可能是半截 JSON 或整段推敲过程，
        灌进作业日志既没用又可能把正文写进不该写的地方。只带闸名、截断后的理由
        与几个长度读数——够回答「哪一道闸、拒的是什么形状」。

        ⚠⚠ **`answer_lost` 那一格另留一份证据，而事件本身仍然一个字正文都不带。**
        「推理通道里到底装了什么」这个问题事件答不了，而它正是这一类的全部争议：
        模型是没干活，还是答案写好了走错了通道。两者在事件里长得一样
        （`content_chars=0` + 一个大 `reasoning_chars`）。⇒ 整份响应落一份留证文件
        （`_keep_answer_lost`），指针进审计账，正文只在那份文件里、且跟着书走。
        在此之前这份证据**从来没落过盘**：`ResponseCache.put` 拒写空正文
        （那是对的——缓存下来只会让重试每次命中同一个空壳），于是空响应一被 `evict`
        就彻底没了，只剩 token 体积可以拿来猜。
        """
        effort, thinking = rungs[index]
        if resample:
            nxt = (effort, thinking)
        else:
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
            resample=resample,                # True = 下一步同档重采，不是降档
            next_effort=None if nxt is None else nxt[0],
            next_thinking=None if nxt is None else nxt[1],
            # 最后一档也被拒 ⇒ 这一趟走完了，调用方要么进真跑那一趟、要么 LlmFailed。
            exhausted=nxt is None,
        )
        if gate == 'answer_lost':
            self._keep_answer_lost(
                purpose=purpose, attempt_id=attempt_id, source=source,
                effort=effort, thinking=thinking, json_mode=json_mode,
                index=index, sample=sample, resample=resample,
                resp=resp, progress=progress)

    def _keep_answer_lost(self, *, purpose: str, attempt_id: str, source: str,
                          effort: str, thinking: bool, json_mode: bool,
                          index: int, sample: int | None, resample: bool,
                          resp: ChatResponse,
                          progress: RequestProgress | None) -> None:
        """留证 + 记指针。**正文只进留证文件，账上只有指针与长度读数。**

        留证落在**书目录**下（`novel/paths.py::answer_lost_path`）而不是跨书的
        `_service/`：那份 `reasoning` 里装的是这本书的正文——答案本来就该写进
        `content`，只是走错了通道。⇒ 跟书走，删书即删证据。

        ⚠ 留一份供应商原样的 `usage`：`reasoning_tokens == completion_tokens`
        是这一类的**服务端自证**（答案整份记在了推理侧），而它与 `reasoning` 的
        字数一起才说得清「模型是没干活还是走错通道」。
        ⚠ token 数不抄进审计账（那是成本账那一行的，见 `note_answer_lost`）。
        """
        payload: dict[str, Any] = {
            'at': datetime.now(timezone.utc).isoformat(timespec='seconds'),
            'sid': self.ctx.sid, 'node': self.ctx.node, 'purpose': purpose,
            'attempt_id': attempt_id, 'source': source,
            'reasoning_effort': effort, 'thinking': thinking, 'json_mode': json_mode,
            'rung_index': index, 'sample': sample, 'resample': resample,
            'chapter': progress.chapter if progress else None,
            'finish_reason': resp.finish_reason,
            'model': resp.model,
            'usage': resp.usage,
            'content': resp.content,
            'reasoning': resp.reasoning,
        }
        dump, dump_error = write_answer_lost(
            self.ctx.novel_dir, attempt_id, payload)
        self.ctx.ledger.note_answer_lost(
            sid=self.ctx.sid, node=self.ctx.node, purpose=purpose,
            attempt_id=attempt_id, source=source,
            reasoning_effort=effort, thinking=thinking, json_mode=json_mode,
            rung_index=index, sample=sample, resample=resample,
            finish_reason=resp.finish_reason,
            content_chars=len(resp.content or ''),
            reasoning_chars=len(resp.reasoning or ''),
            dump=dump, dump_error=dump_error,
            chapter=progress.chapter if progress else None)

    def _settle_gate(self, *, purpose: str, attempt_id: str, source: str,
                     accepted: bool, gate: str | None,
                     rungs: tuple[tuple[str, bool], ...], index: int,
                     sample: int | None = None, resample: bool = False,
                     progress: RequestProgress | None) -> None:
        """内容闸的判定落**审计账**（`gate_decision` 行），采纳时同时登记
        `purpose → attempt_id`，供落盘方写 `artifact_adoption` 行时反查。

        与 `_observe_rung_rejected` 的分工：那条是给**看现场的人**的 stdout 事件
        （带被拒理由的截断文本）；这条是给**离线归因**的账（机器可联结，
        不带理由文本——理由归事件，采纳归账）。
        """
        effort, thinking = rungs[index]
        self.ctx.ledger.note_gate(
            sid=self.ctx.sid, node=self.ctx.node, purpose=purpose,
            attempt_id=attempt_id, source=source,
            decision='accepted' if accepted else 'rejected',
            gate=None if accepted else gate,
            reasoning_effort=effort, thinking=thinking, rung_index=index,
            sample=sample, resample=resample,
            chapter=progress.chapter if progress else None)
        if accepted:
            with self._adopt_lock:
                self._accepted[purpose] = attempt_id

    def accepted_attempt(self, purpose: str) -> str | None:
        """这个 purpose 最终被采纳的响应的 `attempt_id`；本 run 没采纳过则 `None`。"""
        with self._adopt_lock:
            return self._accepted.get(purpose)

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
                     ) -> tuple[ChatResponse, str, str] | None:
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
        attempt_id = self._record_cache_hit(
            req, hit, purpose=purpose, progress=progress, cache_policy=policy)
        return hit, key, attempt_id

    def _call_keyed(self, system: str, user: str, *, effort: str, thinking: bool,
                    json_mode: bool, max_tokens: int, purpose: str,
                    use_cache: bool = True,
                    progress: RequestProgress | None = None,
                    cache_policy: CachePolicy | None = None,
                    ) -> tuple[ChatResponse, str, bool, str]:
        """发一次（或读一次缓存）。返回 `(响应, 缓存键, 是不是缓存来的, attempt_id)`。

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
                attempt_id = self._record_cache_hit(
                    req, hit, purpose=purpose, progress=progress,
                    cache_policy=policy)
                return hit, key, True, attempt_id

        # ⚠ 记账在 `_send_with_retry` 里逐次发生（那里才知道自己发了几次）。
        resp, attempt_id = self._send_with_retry(
            req, purpose=purpose, progress=progress, cache_policy=policy)
        return resp, key, False, attempt_id

    def _keep(self, key: str, resp: ChatResponse, *,
              cache_policy: CachePolicy | None = None) -> None:
        """把一份**已经判定可用**的响应写进缓存。全类唯一的写口。"""
        policy = cache_policy or self.ctx.cache
        if policy.write:
            self.cache.put(key, resp)      # 空 content 由 `put` 自己再拒一道

    def _send_with_retry(self, req: ChatRequest, *, purpose: str,
                         progress: RequestProgress | None = None,
                         cache_policy: CachePolicy | None = None,
                         ) -> tuple[ChatResponse, str]:
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
                       cache_policy: CachePolicy | None = None,
                       ) -> tuple[ChatResponse, str]:
        """重试循环本体。**语义全部写在 `_send_with_retry` 的 docstring 里**，
        拆出来只是为了让首发闸不必把整个循环缩进一层。
        返回 `(响应, 成功那次 attempt 的 attempt_id)`——每次上线各有自己的 id，
        失败行也带（账本行与审计行靠它相联）。"""
        last: TransportError | None = None
        max_attempts = self.max_retries + 1
        for attempt_index in range(max_attempts):
            attempt = attempt_index + 1
            attempt_id = _attempt_id()
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
                            attempt_id=attempt_id,
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
                            will_retry=False, backoff_ms=None,
                            attempt_id=attempt_id, progress=progress,
                            cache_policy=cache_policy)
                    finally:
                        heartbeat.finish(outcome='ok')
                    return resp, attempt_id
            # ⚠ 睡在信号量**外面**：在闸里睡会把整跑串行化（并发闸守的是钱，不是速率）。
            assert backoff_seconds is not None
            self._sleep(backoff_seconds)
        raise last if last else LlmFailed('unreachable')

    def _record_wire(self, req: ChatRequest, resp: ChatResponse | None, *,
                     error: TransportError | None, purpose: str,
                     attempt: int, max_attempts: int, latency_ms: int,
                     will_retry: bool, backoff_ms: int | None,
                     attempt_id: str,
                     progress: RequestProgress | None,
                     cache_policy: CachePolicy | None = None) -> None:
        """一次**真上线**记一行。失败那次 token 三项全 `None`——它没拿回任何读数。"""
        u = (resp.usage if resp else None) or {}
        policy = cache_policy or self.ctx.cache
        self.ctx.ledger.record(
            attempt_id=attempt_id,
            request_fingerprint=cache_key(req),
            response_sha256=_response_sha256(resp),
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
                          cache_policy: CachePolicy | None = None) -> str:
        """一次缓存命中记一行（0 元）。**不记的话，「读了 31 份缓存」与「什么都没做」同形。**

        返回本次命中事件的 `attempt_id`——缓存来的响应同样要过内容闸、
        同样可能被采纳，审计账的联结键不因来源而缺席。
        """
        attempt_id = _attempt_id()
        u = resp.usage or {}
        policy = cache_policy or self.ctx.cache
        self.ctx.ledger.record(
            attempt_id=attempt_id,
            request_fingerprint=cache_key(req),
            response_sha256=_response_sha256(resp),
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
        return attempt_id
