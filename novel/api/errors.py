#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
errors.py —— 异常 → HTTP 状态码的**唯一落点**。纯函数，**不 import fastapi**。

【为什么它单独一份，理由与 `novel/cli/exit_codes.py` 逐字相同】
「哪个数字表示什么」一旦在两处各写一遍，两处就会在某个分支上给出不同的数
——那时候界面上显示的成败与真实发生的事就分家了，**且没有任何东西会出声**。
本模块是那份 docstring 在 HTTP 这一侧的同一条纪律。

【它与退出码是**两张**表，不是一张的两种写法】同一个异常在两侧的归属可以不同，
因为两侧问的是不同的问题：退出码问「编排层该怎么办」，状态码问「**是谁的错**」。
`PathRefused` 就是那个实例，见下面它那一格。

【没有默认格 —— 这是本模块最要紧的一条】
`novel/errors.py` 开头写着「**不设默认异常**」：每一处终态都要选一个具体的类，
选不出来说明那个失败还没想清楚。本表是它的孪生条：
**落不进这张表的异常一律抛出去**，不 fallback 到 500。
一个默认的 500 会把「这个失败我们还没想清楚」藏成「服务器内部错误」，
而那两件事对下一个人是完全不同的指示。
"""
from __future__ import annotations

from novel.errors import (
    ApprovalFrozen,
    AccountingKeyUnregistered,
    ApprovalRequired,
    ArtifactUnreadable,
    BlockingFindingsUnresolved,
    BookTrashFailed,
    ByteFormRefused,
    DeliveryIncomplete,
    GateBlocked,
    HookCopyMissing,
    HumanEditProtected,
    NovelError,
    PathRefused,
    PartitionChapterMismatch,
    PartitionInputMismatch,
    ProvenanceRefused,
    QueueConflict,
    ResidualNonbody,
    SourceConflict,
    SourceDecodeFailed,
    SourcePathRejected,
    Stage0BodyRejected,
    TooFewChapters,
    UnsupportedRunMode,
    UpstreamMissing,
    UpstreamStale,
    UpstreamUnproven,
    ValueDomainRefused,
)
from novel.orchestrator.agent import AgentBusy
from novel.orchestrator.jobs import JobConflict

#: CSRF 闸门没过（`R-11.2`）。**不是一个异常** —— 它在请求进到业务代码**之前**就被拦下。
#: 登记在这里是为了让「本服务会发哪些状态码」只有一张表。
CSRF_REFUSED = 403

#: 路径参数本身不合法（`:kind` 不在八个里 / `:sid` 过不了 `paths.check_sid`）。
BAD_REQUEST = 400

#: 异常 → 状态码。**按类精确匹配，再沿 MRO 上溯；没有默认格。**
STATUS: dict[type[NovelError], int] = {
    # ── 数据/流程说走不下去了（`TerminalState`）。**不是 bug，不打栈** ──
    UpstreamMissing: 404,
    #: ⚠ **与 404 必须分开**（`novel/errors.py` 的原话）：「上游还没跑」是流程问题
    #: （去跑它），「上游读不出来」是产物坏了（去看它）。合成一条，
    #: 界面会把「先跑 stage2」说成「stage2 的产物损坏了」。
    #: 409 而不是 500：产物是**数据**，不是我们的代码写错了。
    ArtifactUnreadable: 409,
    #: ⚠ **不是 404**：那一份在盘上，只是不再是当前根上那一版 ⇒ 与「还没跑」是两条恢复路。
    #: 409 与 `ArtifactUnreadable` 同码同因：产物是**数据**，不是我们的代码写错了。
    UpstreamStale: 409,
    #: ⚠ **与 `UpstreamStale` 同码不同因**：那一条断言「它变了」，这一条只说
    #: 「证明不了它是当前那一份」。同为 409（产物是**数据**），但界面上给的下一步不同：
    #: 前者指得出重建哪一条边，后者只能整份从 producer 重建。
    UpstreamUnproven: 409,
    GateBlocked: 409,
    #: 正式稿在 live 路径上、但带着没消的 hard ⇒ 下一步被拦住。**与 `GateBlocked` 同码
    #: 不同因**：那条指得出被拦下的稿在哪（`invalid_path`），这条盘上没有 `.INVALID`，
    #: 给的是 `rules`——逐条修判据命中，不是提升一份废稿。
    BlockingFindingsUnresolved: 409,
    ApprovalRequired: 409,
    #: 上游都在，但内容还没齐（缺章 / 文案未定稿）⇒ 是**冲突**不是 404：
    #: 那本书在，只是这一刻不可交付。`R-10.14` 明写 txt 里没有地方写风险抬头横幅。
    DeliveryIncomplete: 409,
    #: 原始内容与当前状态冲突：覆盖会抹掉另一份书或人工编辑，或批准后输入已变化。
    SourceConflict: 409,
    HumanEditProtected: 409,
    PartitionInputMismatch: 409,
    #: 请求本身合法，但小说字节/结构不满足摄入与分章契约。
    SourceDecodeFailed: 422,
    SourcePathRejected: 422,
    Stage0BodyRejected: 422,
    PartitionChapterMismatch: 422,
    ResidualNonbody: 422,
    HookCopyMissing: 422,
    TooFewChapters: 422,
    #: `R-11.10`：一本书同时只跑一个作业。`jobs.py` 的 docstring 明写「E 期翻成 409」。
    #: 行为半边由通用 `POST /api/jobs` 与专用 Stage0 端点共同测试：同书第二次启动为 409，
    #: 不同书可并行；同一本书的作业始终互斥。Stage5 v2 已无书内双臂例外。
    JobConflict: 409,
    #: 删除是维护动作；目标忙、确认面变化或移动失败都不得半删后伪装成功。
    BookTrashFailed: 409,
    #: 删除进行中，这本书的审批写入不落盘。与 `JobConflict` 同码同因：
    #: 两个人同时点了「删除」与「批准」，是**流程**说这一步现在不该发生。
    ApprovalFrozen: 409,
    #: agent 并发闸满了（`agent.MAX_CONCURRENT`）。**与 `JobConflict` 同码不同因**：
    #: 那条是「这本书现在不该开始」，这条是「同时烧的钱到上限了，换个时间来」。
    #: ⚠ 单本入口才会看到它；批量入口 `POST /api/agent/runs/batch` 把它接住改成排队。
    AgentBusy: 409,
    QueueConflict: 409,
    #: 人敲了一条这个节点没有的跑法 ⇒ 产地是**客户端**。
    UnsupportedRunMode: 400,
    # ── 代码自相矛盾（`RefusedByStructure`）。**是 bug，栈要留全** ──
    #: ⚠ 500 的响应体里**只出类名与一句人话，不出栈**——那份正文是不外流的版权内容，
    #: 而栈里可能带着它。栈留在服务端日志里（`R-11.1` 第四条理由）。
    ProvenanceRefused: 500,
    AccountingKeyUnregistered: 500,
    ByteFormRefused: 500,
    ValueDomainRefused: 500,
    #: ⚠⚠ **全表唯一一条两侧归属不同的。** CLI 侧它是退出码 3（代码自相矛盾），
    #: 而 HTTP 侧「书 id 不合法」的产地是**客户端** —— 那是 400 不是 500。
    #: 解法不是给异常分子类（那会让 `novel/errors.py` 为了 HTTP 长出一个分叉），
    #: 是**在边界上先验**：路由第一件事调 `paths.check_sid(sid)`，就地翻 400。
    #: 验过之后再从深处冒出来的 `PathRefused`（例如「一个 id 匹配到两个目录」，F-088）
    #: 就**真的是我们的 bug** —— 那时候 500 是对的。
    #: 「边界验过之后再抛就是 bug」是一条说得清的线，而给异常分子类不是。
    PathRefused: 500,
}

#: **越不过 HTTP 边界的那些异常**，逐条写出为什么。它们不在 `STATUS` 里不是漏登记。
#:
#: ⚠⚠ 理由只有一条，而且是结构性的：`R-11.3` 规定**一切花钱的操作走子进程**。
#: LLM 那一族只在真要发请求的那一刻抛，而那一刻发生在**子进程里**——
#: 父进程看到的是一个退出码，不是一个异常对象。
#: 于是给它们配一个状态码，等于**声称一件不会发生的事**，
#: 而下一个人会以为「服务端处理过 LLM 失败」。
#:
#: ⚠⚠ **键是类名字符串，不是类对象**，而这不是图省事：
#: 写成类对象就得 `from novel.llm... import ...`，于是**服务层在结构上够得到
#: 发请求的代码**——正是 `test_the_service_process_cannot_spend_money` 在钉的那一条
#: （它连函数体里的懒 import 都查，因为「够得到」与「这次没调」是两件事）。
#: 用字符串的代价是拼错不会被解释器发现，而那个代价**已经被补上**：
#: `test_every_error_class_lands_in_exactly_one_table` 会把拼错的那个类报成「无家可归」，
#: `test_the_unreachable_table_names_real_classes` 会把留下来的幽灵名字报出来。
#:
#: ⚠ 那条边界哪天松了（比如有人把付费路直接接进 HTTP 层），这张表要跟着改——
#: 而上面那条测试会逼他先来读这段话。
UNREACHABLE_OVER_HTTP: dict[str, str] = {
    'ContextBudgetRefused': '供应商上下文预算预检在缓存/网络之前拒绝请求。它只由付费节点的'
                            'LLM 子进程抛出，父进程只看到终态退出码',
    'ResponseRepairRequired': '结构化响应在下一采之前暂停，等待受托 agent 修复或放弃。'
                              '它只由付费节点的 LLM 子进程抛出；父进程只看到终态退出码，'
                              '零成本 response-repair sidecar 只封存决定、不抛这类暂停',
    'LlmFailed': '主模型与允许的兜底模型均没拿到可用结果。它在**子进程**里抛，父进程只看到退出码',
    'TransportError': '一次请求没发成功（429/5xx/超时）。同上——服务进程结构上发不出请求',
    'MissingApiKey': '要发真请求了而没有 key。⚠ 它恰恰**证明**了那条边界：'
                     '0 成本路径不需要 key（`novel/llm/transport.py` 明写「缺 key 只在真要'
                     '发请求的那一刻才是错误」），所以它只可能出现在付费的那条路上，'
                     '而那条路走子进程',
}


def status_of(exc: BaseException) -> int:
    """这个异常该配哪个状态码。**认不出就原样抛出去，绝不 fallback 到 500。**

    ⚠⚠ **精确匹配类型，不沿 MRO 上溯，这是有意的。** 上溯会让
    「新加一个 `RefusedByStructure` 的子类」自动继承 500 —— 听起来省事，
    实际是给新异常发了一张**静默通行证**：那个类从此有了一个没人决定过的状态码。
    而本表存在的全部理由，就是逼每一个新的失败当场做一次决定
    （同 `novel/errors.py` 的「不设默认异常」、`novel/api/enums.py` 的三个桶）。
    ⇒ 加一个异常类而不给它一格，`test_every_error_class_has_a_status` 当场红。

    ⚠ 两个抽象基类（`TerminalState` / `RefusedByStructure`）**刻意不在表里**：
    它们不该被直接抛（`novel/errors.py` 的原话）。它们要是有了一格，
    上面那条「加一个类就得决定一次」立刻就漏了。
    """
    got = STATUS.get(type(exc))
    if got is None:
        raise exc
    return got
