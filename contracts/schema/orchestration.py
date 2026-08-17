#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
orchestration.py —— 对应 `docs/schema/_orchestration.md`。作业 · 完成度 · 队列。

【它与另外两份的分界线】`common.py` 管**每份产物里都有的那几个键**；
`report.py` 管**一次校验的输出**；本模块管**一次跑、一批跑、以及「这本书跑到哪了」**。
三者的消费方、生命周期、改版节奏都不一样——合成一份会让**产物契约**与**服务契约**
共用一个版本号，而前者改一次要重跑一层书，后者改一次只影响界面。

【本模块的证据面比另外两份差，说清楚差在哪】
`docs/BACKEND_CONTRACT.md` §八冻结的 `jobs.json` 是 `{"jobs":[]}`——**一个样本都没有**。
所以 `JobRecord` 的形状**只能从代码来**（原型 `Web/jobs.py` 的 `meta` 字典），
而那正是 `_README.md` 立过的纪律：盘上零样本的取值面要去代码或全语料再确认一次
（`platform` 就是这么被推错的）。`NovelOverview` 那一半好些——
统一契约 §八冻结的 `novel_overview.json` 是一本真实的书。`QueuePlan` 整个是新增能力，全是设计。

⚠ 本模块 import `contracts.invariants` 与 `contracts.schema.base`，**不 import `rules`**：
队列与作业不产生任何判定（`R-13.6`），连类型上都够不到判据注册表。
"""
from __future__ import annotations

from typing import Literal

from pydantic import Field, model_validator

from contracts.invariants import (
    AGENT_RUN_EVENT_KINDS, ARTIFACT_CONDITION, ARTIFACT_DISPLAY, BEACON_STATES,
    FRESHNESS, FRESHNESS_REASON, JOB_EVENT_KINDS, JOB_STATE, NODES,
    OVERVIEW_VERDICT, PROGRESS_PHASES, STAGES,
)
from contracts.schema.base import FrozenModel, StrictModel

#: 枚举一律从 `contracts.invariants` 取，不在本文件重抄（同 `common.py` / `report.py`）。
JobState = Literal[JOB_STATE]
JobEventKind = Literal[JOB_EVENT_KINDS]
Freshness = Literal[FRESHNESS]
FreshnessReason = Literal[FRESHNESS_REASON]
ArtifactDisplay = Literal[ARTIFACT_DISPLAY]
ArtifactCondition = Literal[ARTIFACT_CONDITION]
OverviewVerdict = Literal[OVERVIEW_VERDICT]
ProgressPhase = Literal[PROGRESS_PHASES]
BeaconState = Literal[BEACON_STATES]


# ─────────────────────────────────────────────────────────────────────────────
# 一、`Step` —— 一条将要执行的命令（`_orchestration.md` §一）
# ─────────────────────────────────────────────────────────────────────────────

class Step(FrozenModel):
    """**`R-11.8` 的全部内容是一句话：展示与执行共用同一个构造函数。**

    所以「界面上摊开给人看的那条命令」与「真的会被 `Popen` 的那条」是**同一个对象**，
    不是两份。原型这一条做对了，且有测试钉着
    （`test_preview_argv_is_identical_to_what_would_run`）。
    """

    #: 完整命令行。**`argv[0]` 恒是 `sys.executable`**，绝不硬编码解释器路径。
    #:
    #: ⚠ 原型有一处裂缝**别抄**：前端用 `argv.slice(4)` 硬编码切掉解释器前缀来渲染
    #: （`Web/static/app.js:291`）——`build_argv` 的前缀长度一变它就**静默错位**。
    #: 渲染也只许有一个落点。
    argv: list[str] = Field(min_length=1)

    #: 绝对路径。`--base` 已经是绝对的（好让 argv 与 cwd 无关），但 `-m novel.cli`
    #: 这一段**本身**要求 cwd 在仓根 ⇒ 粘命令的人要知道在哪儿粘。
    cwd: str = Field(min_length=1)

    #: 人读的一句话。**从 `NODES` 派生，不许手写**——手写的 label 会说谎：
    #: 原型 `n1_partition` 的 label 写着「stage1 摄入+分章」，而「摄入」早已搬到 stage0。
    label: str = Field(min_length=1)

    #: 这一步会不会花钱。**唯一落点 `novel/orchestrator/nodes.py`**（`R-11.7`）。
    paid: bool

    #: ⭐ 这一步会往盘上写什么。**空串 = 不写盘。**
    #:
    #: 原型**根本没有这个概念**：它只活在 8 条一字不差的注释里
    #: （「`FREE_FLAGS` 只管钱不管写」），于是界面上一个写着「0 成本」的按钮
    #: 可能归档旧轮、覆盖正式产物、重拼成书 txt。通用取值落在
    #: `invariants.WRITE_EFFECTS`，跨节点特例落在 `NODE_WRITE_EFFECTS`。
    writes: str

    #: 这一步的特别提醒（例：追跑的那条 `--check-only` 为什么在）。`None` = 键不在。
    note: str | None = None


# ─────────────────────────────────────────────────────────────────────────────
# 二、作业（`_orchestration.md` §二 · §三）
# ─────────────────────────────────────────────────────────────────────────────

class JobStart(StrictModel):
    """`started` 事件的载荷 —— 一个作业的全部静态字段。"""

    #: ⚠ **必填但可为 `null`**：上传新书那条路不带 sid（那会儿目录还不存在）。
    #: 原型正是在这个 `None` 上把互斥键算成了两个（见 `active_key` 的注释）。
    sid: str | None

    node: str = Field(min_length=1)

    #: 只有 `stage5.write` 有（`DEPTH_MODES`）。`None` = 键不在。
    mode: str | None = None

    #: 这一次带的 flag。**空列表也要写**——「没带 flag」与「没记」不是一回事。
    flags: list[str] = Field(default_factory=list)

    steps: list[Step] = Field(min_length=1)

    #: `sys.version`。哪版解释器跑的。
    python: str = Field(min_length=1)

    #: 完整提交 + 可选脏树内容摘要。哪版代码跑的（同 `_provenance.code`，全链一个实现）。
    code: str = Field(min_length=1)

    #: 整个作业花不花钱 / 写不写盘 = 任一步花钱 / 写盘。
    paid: bool
    writes: bool

    @model_validator(mode='after')
    def _postconditions(self) -> JobStart:
        if self.node not in NODES:
            raise ValueError(
                f'未知节点 {self.node!r}。取值面的唯一落点是 `contracts.invariants.NODES`')
        want = NODES[self.node].modes
        tokens = {value for value in self.flags if value.startswith('--')}
        composite_write = (
            self.node == 'stage5.write'
            and {'--deep', '--shallow-rest'} <= tokens
        )
        if composite_write and self.mode is not None:
            raise ValueError('stage5 composite write must not carry a single-arm mode')
        if want and not composite_write and self.mode not in want:
            raise ValueError(f'{self.node} 的 mode 必须是 {want} 之一，实得 {self.mode!r}')
        if not want and self.mode is not None:
            raise ValueError(
                f'{self.node} 没有臂，不该带 mode={self.mode!r}。'
                f'⚠ 全链只有 `stage5.write` 有臂——给别的节点加 mode 就是把两件事藏进一个 id 里')
        # 整份的贵贱 / 写不写，是逐步折叠出来的：第二处算就必然分家（`R-11.7`）。
        if self.paid != any(s.paid for s in self.steps):
            raise ValueError(f'paid={self.paid} 与逐步折叠出来的对不上')
        if self.writes != any(bool(s.writes) for s in self.steps):
            raise ValueError(f'writes={self.writes} 与逐步折叠出来的对不上')
        return self


class JobEvent(FrozenModel):
    """作业记录的**一行**。append-only JSONL，永不读-改-写。

    ⚠ **记录是事件折叠出来的，不是一份被反复改写的 JSON。**
    原型每次状态变化都 `os.replace` 整份 `<job_id>.json`，并为此写了一段重试——
    `PermissionError: [WinError 5]`，因为服务一边写一边被 SSE 轮询读，**实测必现**。
    """

    job_id: str = Field(min_length=1)
    kind: JobEventKind

    #: epoch 秒。⚠ 事件流的顺序由**写入顺序**保证，`ts` 只是给人看的
    #: ——同一秒内的两条事件靠它排不出先后，别拿它当排序键。
    ts: int = Field(ge=0)

    #: `kind == 'started'` 时**必填**，其余三态必须为 `None`。
    start: JobStart | None = None

    #: `kind == 'step'`：第几步开始了（0 起）。
    step_index: int | None = None

    #: `kind == 'ended'`：子进程的退出码。
    #: ⚠ **`exit_code == 0` 不等于成功**——原型实测 `n2_blueprint` 打印
    #: 「blueprint 缺章（勿静默）」之后**没有 `sys.exit`**，残缺产物退出码 0（F-087）。
    exit_code: int | None = None

    #: `kind == 'reconciled'`：为什么改判。
    note: str | None = None

    @model_validator(mode='after')
    def _payload_matches_the_kind(self) -> JobEvent:
        """一种事件恰好带一种载荷。

        **松一格的代价是具体的**：一条 `ended` 事件如果允许不带 `exit_code`，
        折叠的时候就分不出「跑完了而我们没记退出码」与「还没跑完」——
        而那两件事一个是 bug、一个是正常状态。
        """
        want = {'started': 'start', 'step': 'step_index',
                'ended': 'exit_code', 'reconciled': 'note'}[self.kind]
        carried = {k for k in ('start', 'step_index', 'exit_code', 'note')
                   if getattr(self, k) is not None}
        if carried != {want}:
            raise ValueError(
                f'{self.kind} 事件该带且只该带 {want!r}，实得 {sorted(carried)}')
        return self


class JobRecord(StrictModel):
    """把同一个 `job_id` 的事件按顺序折叠出来的**视图**。盘上不存在这样一份文件。"""

    job_id: str = Field(min_length=1)
    start: JobStart

    started: int = Field(ge=0)

    #: 必填但可为 `null` —— **`null` = 还没结束、或不知道**，不是 0。
    ended: int | None
    exit_code: int | None

    #: 五态；`landed_red` 明确区分「产物已落盘」与「命令没跑成」。
    #: **`unknown` 是真状态，不是缺省值**（`R-11.11`）。
    state: JobState

    #: 已经开始过的步数（由 `step` 事件数出来）。
    steps_started: int = Field(ge=0)

    #: `reconciled` 那一条的原因。`None` = 键不在。
    note: str | None = None

    @model_validator(mode='after')
    def _state_matches_the_events(self) -> JobRecord:
        """折叠规则只有一处算。**第二处必然分家**（同 `CheckReport` 的 badge）。"""
        if self.exit_code is None:
            if self.state in ('ok', 'landed_red', 'failed'):
                raise ValueError(
                    f'state={self.state!r} 而没有 exit_code——'
                    f'「跑完了」这个结论只能从退出码来')
        else:
            want = ('ok' if self.exit_code == 0 else
                    'landed_red' if self.exit_code == 1 else 'failed')
            if self.state != want:
                raise ValueError(f'exit_code={self.exit_code} 应折叠成 {want!r}，'
                                 f'实得 {self.state!r}')
        if self.state == 'unknown' and not self.note:
            raise ValueError(
                'state=unknown 必须说出原因。'
                '⚠ 「重启前它是死是活我们不知道」与「这个作业出了别的问题」'
                '在界面上长得一样近，分不开就没法判断该不该重跑')
        return self


# ─────────────────────────────────────────────────────────────────────────────
# 二之二、进度信标 —— agent 代跑 CLI 时的**观察轨迹**
# ─────────────────────────────────────────────────────────────────────────────

class ProgressEvent(FrozenModel):
    """信标的**一行**。append-only JSONL，与 `JobEvent` 同形同理（永不读-改-写）。

    ⚠ **它是观察轨迹，不是判定**：`phase='end', exit_code=0` 只说「那条命令
    exit 0」——exit 0 ≠ 合格（`F-087`），「这层过没过」的唯一判官仍是 check
    报告的 badge（`R-13.6`）。消费方（路由/界面）不许把信标渲染成判定。
    ⚠ **为什么不并进 `JobEvent`**：作业是**服务进程拉起子进程**的记录
    （有 job_id、有日志文件、有 `reconciled` 改判）；信标是 agent **直跑** CLI
    时的人为打点，没有进程可追、没有日志可续。捏在一起会让 `jobs.fold` 的
    「重启后 running → unknown」规则（`R-11.11`）对一堆没有 job_id 的行开火。
    """

    #: epoch 秒。与 `JobEvent.ts` 同理：顺序由写入顺序保证，`ts` 只给人看。
    ts: int = Field(ge=0)
    sid: str = Field(min_length=1)
    node: str = Field(min_length=1)
    phase: ProgressPhase

    #: `phase == 'end'` 必填（那条命令的退出码）；`start` 必须为 `None`。
    exit_code: int | None = None

    #: 人读的一句附注（如「走 §11.2 修复路」）。可空。
    note: str | None = None

    @model_validator(mode='after')
    def _payload_matches_the_phase(self) -> ProgressEvent:
        """一种相位恰好带一种载荷（与 `JobEvent` 那条同理）。"""
        if self.phase == 'start' and self.exit_code is not None:
            raise ValueError('start 不带 exit_code——还没跑完哪来的退出码')
        if self.phase == 'end' and self.exit_code is None:
            raise ValueError(
                'end 必须带 exit_code——否则折叠分不出「跑完了」与「还在跑」')
        return self


class RunStepView(StrictModel):
    """`PIPELINE_STEPS` 里一步此刻的样子。**盘上不存在这样一份文件**——路由现算。"""

    seq: int = Field(ge=1)
    node: str = Field(min_length=1)
    label: str = Field(min_length=1)
    paid: bool
    beacon: BeaconState
    last_exit: int | None
    last_ts: int | None
    #: 仅 `running` 时有值：从 start 打点到现在的秒数。
    elapsed: int | None


class RunProgressView(StrictModel):
    """`GET /api/novels/{sid}/run` 的响应：信标折叠 + 产物盘的完成度，两栏并排。"""

    sid: str = Field(min_length=1)
    steps: list[RunStepView]
    #: `STAGES` → done 三态。书目录还不存在时全 `None`
    #: （stage0.remove 打点先于书目录落地，进度屏那时也得开得开）。
    stage_done: dict[str, bool | None]
    #: 最近若干条信标事件（人读时间线，文件序——新的在后）。
    events_tail: list[ProgressEvent]


# ─────────────────────────────────────────────────────────────────────────────
# 二之三、agent 代跑 —— 前端一键唤起 headless agent 的记录
# ─────────────────────────────────────────────────────────────────────────────

class AgentRunStart(FrozenModel):
    """`started` 事件的载荷——一次 agent 代跑的全部静态字段。

    ⚠ `argv` 与 `prompt` **全文留档**：这是一次会花两笔钱的进程
    （pipeline 的 DeepSeek + agent 自身的模型），审计与终审包要能看到
    「到底跑了哪条命令、授权声明原文是什么」——不是一份摘要。
    """

    sid: str = Field(min_length=1)

    #: 解析后的真实命令行（含 claude 的绝对路径与全部旗标）。
    argv: list[str] = Field(min_length=1)

    #: 发给 agent 的提示词全文。两项授权声明都在里面；下面两格只是机读摘要。
    prompt: str = Field(min_length=1)

    #: 显式付费总授权（SKILL 接口契约）：True = 付费命令直接执行；False = 首个付费节点前停。
    authorized: bool

    #: 正文外发授权，与付费授权正交；每一条 started 事件都必须显式记录。
    egress_authorized: bool


AgentRunEventKind = Literal[AGENT_RUN_EVENT_KINDS]


class AgentRunEvent(FrozenModel):
    """agent 代跑记录的**一行**。append-only JSONL，与 `JobEvent` 同形同理。

    ⚠ **为什么不并进 `jobs.jsonl`**：`JobStart` 的后置条件钉死 `node ∈ NODES`，
    而 agent 代跑不是流水线节点——它是「拉起一个会自己跑节点的进程」。
    捏进去就要往 `NODES` 里塞一个假节点，而那张表是完成度/贵贱/写盘效应的
    全部派生源头，塞假节点的代价是全链说假话。
    """

    run_id: str = Field(min_length=1)
    kind: AgentRunEventKind
    ts: int = Field(ge=0)

    #: `kind == 'started'` 时必填，其余两态必须为 `None`。
    start: AgentRunStart | None = None

    #: `kind == 'ended'`：agent 进程的退出码。
    exit_code: int | None = None

    #: `kind == 'reconciled'`：为什么改判（服务重启那一族）。
    note: str | None = None

    @model_validator(mode='after')
    def _payload_matches_the_kind(self) -> AgentRunEvent:
        """一种事件恰好带一种载荷（与 `JobEvent` 那条逐字同理）。"""
        want = {'started': 'start', 'ended': 'exit_code',
                'reconciled': 'note'}[self.kind]
        carried = {k for k in ('start', 'exit_code', 'note')
                   if getattr(self, k) is not None}
        if carried != {want}:
            raise ValueError(
                f'{self.kind} 事件该带且只该带 {want!r}，实得 {sorted(carried)}')
        return self


class AgentRunRecord(StrictModel):
    """把同一个 `run_id` 的事件按顺序折叠出来的**视图**。盘上不存在这样一份文件。"""

    run_id: str = Field(min_length=1)
    start: AgentRunStart

    started: int = Field(ge=0)

    #: 必填但可为 `null`——`null` = 还没结束、或不知道，不是 0。
    ended: int | None
    exit_code: int | None

    #: `JobState` 那五态。**`unknown` 是真状态，不是缺省值**（`R-11.11` 同构）：
    #: 服务重启后，子进程是死是活我们真的不知道。
    state: JobState

    #: `reconciled` 那一条的原因。`None` = 键不在。
    note: str | None = None

    @model_validator(mode='after')
    def _state_matches_the_events(self) -> AgentRunRecord:
        """折叠规则只有一处算（同 `JobRecord` 那条）。"""
        if self.exit_code is None:
            if self.state in ('ok', 'landed_red', 'failed'):
                raise ValueError(
                    f'state={self.state!r} 而没有 exit_code——'
                    f'「跑完了」这个结论只能从退出码来')
        else:
            want = ('ok' if self.exit_code == 0 else
                    'landed_red' if self.exit_code == 1 else 'failed')
            if self.state != want:
                raise ValueError(f'exit_code={self.exit_code} 应折叠成 {want!r}，'
                                 f'实得 {self.state!r}')
        if self.state == 'unknown' and not self.note:
            raise ValueError('state=unknown 必须说出原因（R-11.11）')
        return self


class AgentPending(FrozenModel):
    """排在队里、**还没有进程**的一本。

    ⚠ 它**不是** `AgentRunRecord` 的一种状态：那份记录的每一格都从事件流折叠而来，
    而排队项在盘上一个字节都没有（队列只在内存里，`R-14.6` 那条的同族）。
    捏进 `JobState` 会让「重启后 running → unknown」那条规则对一堆
    根本没有 run_id 的行开火。
    """

    sid: str = Field(min_length=1)

    #: 排在第几，**1 起算**。给人看的序号，不是索引。
    position: int = Field(ge=1)


class AgentRejected(FrozenModel):
    """批量受理时**没被收下**的一本，连同原因。

    ⚠ 它存在的理由是「部分失败不回滚」：八本起来了、两本没起来，
    只报一句「批量失败」会把已经在跑的那八本藏掉——人看到报错，盘上却真有进程。
    """

    sid: str = Field(min_length=1)
    reason: str = Field(min_length=1)


class AgentQueueView(StrictModel):
    """agent 并发的全景：谁在跑、谁在排、闸开多大。

    `running + pending + rejected` 三者之和 = 这一批提交的全部（`enqueue` 的后置条件）。
    """

    running: list[AgentRunRecord] = Field(default_factory=list)
    pending: list[AgentPending] = Field(default_factory=list)
    rejected: list[AgentRejected] = Field(default_factory=list)

    #: 同时在跑的上限（`novel/orchestrator/agent.py::MAX_CONCURRENT`）。
    #: **前端不自己写死这个数**——它要显示「3 个都在跑」就得从这儿拿。
    max_concurrent: int = Field(ge=1)


# ─────────────────────────────────────────────────────────────────────────────
# 三、完成度（`_orchestration.md` §四 · `R-14.1`）
# ─────────────────────────────────────────────────────────────────────────────

class ArtifactState(StrictModel):
    """一份产物此刻的处境。

    ⚠ **`readable=False` 不等于 `exists=False`。** 盘上真有一份加载不了的产物
    （`成年人不做选择` 那本的 `stage4/plan_chapters.json` 根本没有 `_provenance`）。
    把它折成「不存在」会让界面说「还没跑」——而它跑过，只是那次把来源账丢了。
    """

    #: `<stage>/<文件名>`。**取自 `contracts/schema/__init__.py` 的 `ARTIFACTS`**，
    #: 不新建第二张清单（原型那张在 `Web/novels.py` 里，是第二份，且 stage5 被迫排除在外）。
    rel: str = Field(min_length=1)

    exists: bool

    #: 必填可空：不存在时是 `null`，**不是 0**。
    bytes: int | None
    #: ⚠ **mtime 不参与新鲜度判定**，它只是给人看的。判定走指纹（`novel/prov.py`）。
    mtime: int | None

    freshness: Freshness
    #: 前端直接显示的四态；`missing` 在这里合并，不由 `!exists` 另算一遍。
    display_freshness: ArtifactDisplay
    #: **用 id 不用措辞**（同 `STAGE0_WARNINGS` 的理由）：报告不在逐字节验收范围内，
    #: 拿字符串前缀认原因会随一次措辞改动静默断掉。
    reason: FreshnessReason

    #: 指向已经不在盘上的那些边，**按名字点名**（`Q-4` 亲自点过的 bug：
    #: 原型对「取不到指纹」的条目静默跳过，而盘上真有一条）。**空列表也要写。**
    ghosts: list[str] = Field(default_factory=list)

    #: 盘上有一份 `.INVALID` 在等人处理。
    invalid: bool = False

    #: 能不能按**当前** schema 加载。
    readable: bool = True

    #: 缺席/可用/隔离/读不回。界面只按这个 id 选文案，不再组合 exists/invalid/readable。
    condition: ArtifactCondition

    @model_validator(mode='after')
    def _absent_artifacts_carry_nothing(self) -> ArtifactState:
        if not self.exists and (self.bytes is not None or self.mtime is not None):
            raise ValueError(f'{self.rel}: exists=False 却带着 bytes/mtime')
        if not self.exists and self.freshness != 'unknown':
            raise ValueError(
                f'{self.rel}: 不在盘上的产物只能是 unknown，实得 {self.freshness!r}。'
                f'「没有它」不是「它是新的」')
        want_display = 'missing' if not self.exists else self.freshness
        if self.display_freshness != want_display:
            raise ValueError(
                f'{self.rel}: display_freshness={self.display_freshness!r}，'
                f'按文件与来源状态应为 {want_display!r}')
        want_condition = ('invalid' if self.invalid else
                          'missing' if not self.exists else
                          'unreadable' if not self.readable else 'ready')
        if self.condition != want_condition:
            raise ValueError(
                f'{self.rel}: condition={self.condition!r}，按载体状态应为 {want_condition!r}')
        return self


class StageState(StrictModel):
    """一层的处境。`done` 是折叠出来的，不是另存的一格。"""

    stage: str = Field(min_length=1)
    artifacts: list[ArtifactState] = Field(default_factory=list)

    #: 这一层算不算跑完了。**三态，不是 `bool`。**
    #:
    #: ⚠⚠ **`None` = 判不了**，与 `False`「没跑完」是两件事——
    #: 这是 `Q-2` 那个三态在完成度这一侧的样子，也是本项目一路在防的那个病
    #: （`FRESHNESS` / `JOB_STATE` 的 `unknown` 防的都是它）。
    #: 实测就撞上了：stage5 的产物是**逐章**的（`stage5/<mode>/chNN.json`），
    #: 「该有几章」要去读 stage4 计划里的输出章；计划读不回来时，
    #: 盘上有 4 章这件事**既不能证明跑完了、也不能证明没跑完**。
    #: 压成 `False` 会让队列一次次去重跑一个可能已经写完的付费层——
    #: 那是**真金白银**，不是一个显示问题。
    #:
    #: ⚠ **`.INVALID` 不计入已完成**，它进 inbox（`INBOX_KIND` 已有 `invalid` 这个值）。
    #: ⚠ **`freshness == 'unknown'` 计入已完成**，这不是漏判：八层今天几乎全是 `unknown`
    #: （`consumed` 投影只有 stage3 声明过三条），把它当未完成会让整条链永远跑不出第一步。
    #: 「不知道它新不新」与「它是旧的」是两件事——**后者才该挡路**。
    done: bool | None

    #: 逐章类的层（stage5）：已落盘的章。**必须排除 `.INVALID.json`**——
    #: `chNN.json` 的存在本身就是「过了写作时的落盘闸门」这个语义，
    #: 把被拦下的那份数进去就把语义反过来了。
    chapters_done: dict[str, list[int]] = Field(default_factory=dict)

    #: 轮次编号产物（`stage5/review_{n}.json` / `stage5/semantic_{n}.json` /
    #: `stage6/semantic_{n}.json`）盘上已有的编号，键是**产它的节点**。
    #:
    #: ⚠⚠ **它与 `chapters_done` 不是同一件事，合并会把 bug 装回去。**
    #: 这两类产物的路径都带 `{}`，于是 `required_artifacts` 一并丢掉、
    #: `artifacts[]` 里没有它们的行；此前续跑判定只剩 stage 级 `done` 可问，
    #: 而 stage5 的 `done` 说的是**章稿写全了没有**——拿它答「全书验收跑过没有」，
    #: 答案恒为「跑过了」，**而盘上一份 `review_*.json` 都没有**。
    #: 实测：批量队列把 `stage5.review`（0 成本）与当时的 `stage5.semantic`（付费，
    #: 已于 2026-08-17 下线）双双记成 `done` 却一次都没执行。
    #: ⇒ 逐章有逐章的账，轮次有轮次的账。
    rounds_done: dict[str, list[int]] = Field(default_factory=dict)

    #: 只读导航能否进入。它不是运行闸门；运行/定稿仍看 approvals。
    reviewable: bool

    @model_validator(mode='after')
    def _done_matches_the_artifacts(self) -> StageState:
        """折叠规则只有一处算——**第二处必然分家**。

        ⚠ 清单为空（粒度在章上的层，现役只有 stage5）时**本载体不设约束**：
        它的完成度证据在**计划 + 逐章成稿**（`state.stage_state` 的 stage5 分支），
        不在本模型携带的字段里——载体在这里能「重算」的东西是零，硬立一条
        「只能是 None」就是拿结构断言一件结构看不见的事。
        这条教训是实测买来的：第一本全链跑完的书（e2e_20260813）`done=True`
        被旧断言当场拒收，`/api/novels` 对它静默跳过，**语料库屏上它整本消失**。
        「计划读不回 ⇒ None、不许压成 False」那半句仍然成立，但它守在**产地**
        （`planned_chapters` 返回 None 的那条路），不在载体。
        """
        if not self.artifacts:
            return self
        if self.done is None:
            return self          # 逐章类的层：判据在别处（见 `chapters_done`）
        want = all(a.exists and a.readable and a.freshness != 'stale' and not a.invalid
                   for a in self.artifacts)
        if self.done != want:
            raise ValueError(
                f'{self.stage}: done={self.done} 与逐份算出来的 {want} 对不上。'
                f'完成度只有一处算——第二处必然分家')
        return self


class OverviewSummary(StrictModel):
    """语料库卡片顶部那一句。业务优先级与计数都由服务端折叠。"""

    verdict: OverviewVerdict
    text: str = Field(min_length=1)
    detail: str = Field(min_length=1)
    artifact_count: int = Field(ge=0)
    latest_mtime: int | None


class NovelOverview(StrictModel):
    """一本书跑到哪了。**由产物存在性 + 来源新鲜度判定，不靠进度文件**（`R-14.1`）。"""

    sid: str = Field(min_length=1)
    title: str = Field(min_length=1)
    dirname: str = Field(min_length=1)

    #: stage1 主正文一落盘即可确定的总章数；还没分章 / 读不回时为 `None`。
    #: 语料库列表复用本模型，因此不用对每本书再发一次 `/stage1/chapters`。
    chapters: int | None = Field(default=None, ge=1)

    stages: list[StageState] = Field(default_factory=list)

    #: glob `stage5/*` 得到的目录名。stage5 的粒度是**章**（`R-14.2`）。
    stage5_modes: list[str] = Field(default_factory=list)

    summary: OverviewSummary


# ─────────────────────────────────────────────────────────────────────────────
# 四、队列（`_orchestration.md` §五 · `R-13`）
# ─────────────────────────────────────────────────────────────────────────────

class QueueItem(StrictModel):
    """队列里的一项 = 一本书的一个节点跑法。"""

    sid: str = Field(min_length=1)
    node: str = Field(min_length=1)
    mode: str | None = None
    flags: list[str] = Field(default_factory=list)

    #: 与真跑**同一个 `build_steps`**（`R-11.8`）。需要人工选择才能拼出命令的
    #: blocked 项可以暂时为空；一旦可运行，必须带精确命令快照。
    steps: list[Step] = Field(default_factory=list)

    #: 为什么排它（`产物不存在` / `上游变过 ⇒ 重来` / `.INVALID 待处理`…）。
    #: ⚠ **必填**：一份说不出理由的计划，人没法判断该不该批准它花钱。
    why: str = Field(min_length=1)

    #: ⭐ 在等谁（`R-13.5` 与 `R-0.3b` 用的是同一句话：「明确说出在等谁」）。
    #: `None` = 没在等谁，可以跑。
    waiting_on: str | None = None

    @model_validator(mode='after')
    def _runnable_items_have_a_command(self) -> QueueItem:
        if not self.steps and not self.waiting_on:
            raise ValueError(
                'a runnable queue item must carry its exact command snapshot; '
                'only an item blocked on a human choice may have no steps')
        return self


class QueueCounts(StrictModel):
    """⭐ **三个数不许合并**（`R-13.4`）。

    合并成「N 成功 / M+1 失败」是一次真实的信息损失：`not_run` 那些
    **既没有失败也没有成功，它们根本没被执行**——而人要决定的恰恰是
    「修完这一项之后从哪儿续」。

    ⚠ 批量的风险与单本不同：单本跑错人当场看得见；
    **批量跑错是一批产物同时坏掉且没人在看**。所以「停在那里」不是保守，
    是这条能力成立的前提。
    """

    done: int = Field(ge=0)
    failed: int = Field(ge=0)
    not_run: int = Field(ge=0)

    @model_validator(mode='after')
    def _at_most_one_failure_stops_the_queue(self) -> QueueCounts:
        if self.failed > 1:
            raise ValueError(
                f'failed={self.failed}：队列在第一项失败时就该停下（`R-13.4`）。'
                f'出现第二个失败项，说明「停在那里」没有生效')
        if self.failed == 0 and self.not_run:
            raise ValueError(
                f'没有失败项却有 {self.not_run} 项没跑——那它们为什么没跑？'
                f'说不出来就是队列在静默跳过')
        return self


class QueuePlan(StrictModel):
    """`plan()` 的产出。**纯函数算出来的，不跑、不写、不发请求。**

    于是「排这一批会花多少次调用、会写哪些文件」可以 0 成本地摊开给人看
    （`R-13.3`：花钱的项必须一次性预先确认，确认时摊开的仍是**将要执行的那些命令**）。
    """

    #: 顺序即执行顺序（`NODES` 的声明顺序就是处理链顺序）。
    items: list[QueueItem] = Field(default_factory=list)

    #: 被人工闸门挡住的那些，各带 `waiting_on`。
    blocked: list[QueueItem] = Field(default_factory=list)

    #: 花钱的项数。⚠ **不显示预估花费**（`Q-10`：猜一个数写在界面上比不显示更坏）。
    paid_items: int = Field(ge=0)

    @model_validator(mode='after')
    def _postconditions(self) -> QueuePlan:
        # A persistent queue may resume after a human gate.  Those currently
        # blocked items are still part of the queue's declared target, so any
        # paid command behind the gate must be included in the one-time
        # confirmation as well.  Counting only ``items`` would turn clearing a
        # gate into an implicit spending authorization.
        want = sum(1 for it in self.items + self.blocked
                   if any(s.paid for s in it.steps))
        if self.paid_items != want:
            raise ValueError(f'paid_items={self.paid_items} 与逐项数出来的 {want} 对不上')
        if any(it.waiting_on for it in self.items):
            raise ValueError(
                '`items` 里混进了在等人的项——那些该在 `blocked` 里。'
                '⚠ 混在一起，队列跑起来的第一件事就是撞上一道人工闸门，'
                '而「排进去了」会被读成「会自动跑完」')
        if any(not it.waiting_on for it in self.blocked):
            raise ValueError('`blocked` 里有说不出在等谁的项（`R-13.5`）')
        return self


# ─────────────────────────────────────────────────────────────────────────────
# 四之二、持久队列实例（TODO-01）
# ─────────────────────────────────────────────────────────────────────────────

QueueEntryState = Literal[
    'pending', 'running', 'unknown', 'done', 'landed_red', 'failed', 'not_run',
    'cancelled', 'blocked',
]
QueueRunState = Literal[
    'pending', 'running', 'unknown', 'ok', 'landed_red', 'failed', 'blocked', 'cancelled',
]
QueueEventKind = Literal['created', 'item_state']


class QueueStart(FrozenModel):
    """持久队列的创建快照。

    ``plan`` is frozen here rather than recomputed at execution time: the
    commands a runner may launch must be exactly the commands the user
    confirmed.  Freshness and human gates are rechecked before each item, but
    that check may only skip/block a command, never invent a new one.
    """

    sids: list[str] = Field(min_length=1)
    until_stage: str = Field(min_length=1)
    plan: QueuePlan
    paid_confirmed: bool

    @model_validator(mode='after')
    def _valid_target_and_confirmation(self) -> QueueStart:
        if self.sids != list(dict.fromkeys(self.sids)):
            raise ValueError('queue sids must be non-empty and unique in requested order')
        if self.until_stage not in STAGES:
            raise ValueError(f'unknown queue target stage {self.until_stage!r}')
        planned = {item.sid for item in self.plan.items + self.plan.blocked}
        if not planned <= set(self.sids):
            raise ValueError(f'queue plan contains undeclared novels: {sorted(planned - set(self.sids))}')
        if self.plan.paid_items and not self.paid_confirmed:
            raise ValueError('a persistent queue with paid items must record explicit confirmation')
        return self


class QueueEvent(FrozenModel):
    """持久队列事件流的一行；与 ``JobEvent`` 一样只追加、不回写。"""

    queue_id: str = Field(min_length=1)
    kind: QueueEventKind
    ts: int = Field(ge=0)
    start: QueueStart | None = None
    item_index: int | None = Field(default=None, ge=0)
    state: QueueEntryState | None = None
    job_id: str | None = None
    exit_code: int | None = None
    waiting_on: str | None = None
    note: str | None = None

    @model_validator(mode='after')
    def _payload_matches_kind_and_state(self) -> QueueEvent:
        if self.kind == 'created':
            if self.start is None or any(value is not None for value in (
                    self.item_index, self.state, self.job_id, self.exit_code,
                    self.waiting_on, self.note)):
                raise ValueError('created queue event must carry only start')
            return self
        if self.start is not None or self.item_index is None or self.state is None:
            raise ValueError('item_state event must carry item_index and state, not start')
        if self.state == 'running' and not self.job_id:
            raise ValueError('running queue item must name its job_id')
        if self.state == 'unknown' and (not self.job_id or not self.note):
            raise ValueError('unknown queue item must name its job and explain the uncertainty')
        if self.state == 'landed_red' and (not self.job_id or self.exit_code != 1):
            raise ValueError('landed_red queue item must name a job with exit_code=1')
        if self.state == 'failed' and (
                not self.job_id or self.exit_code in (None, 0, 1)):
            raise ValueError('failed queue item must name a job with an exit_code other than 0/1')
        if self.state == 'done' and self.exit_code not in (None, 0):
            raise ValueError('done queue item cannot carry a failing exit_code')
        if self.state == 'blocked' and not self.waiting_on:
            raise ValueError('blocked queue item must say what it is waiting on')
        if self.state in ('pending', 'not_run', 'cancelled') and (
                self.job_id is not None or self.exit_code is not None):
            raise ValueError(f'{self.state} queue item cannot claim a job result')
        if self.state in ('not_run', 'cancelled') and not self.note:
            raise ValueError(f'{self.state} queue item must say why it did not run')
        if self.state != 'blocked' and self.waiting_on is not None:
            raise ValueError(f'{self.state} queue item cannot carry waiting_on')
        return self


class QueueEntry(StrictModel):
    """一项在持久队列里的当前折叠视图。"""

    index: int = Field(ge=0)
    item: QueueItem
    state: QueueEntryState
    job_id: str | None = None
    exit_code: int | None = None
    waiting_on: str | None = None
    note: str | None = None


class QueueLaneCounts(StrictModel):
    """一本书在一条批量队列里的九类计数。

    ``done / landed_red / failed / not_run`` remain separate.  Cancellation
    and blocking are separate too, because neither is a failed command.
    """

    done: int = Field(ge=0)
    landed_red: int = Field(ge=0)
    failed: int = Field(ge=0)
    not_run: int = Field(ge=0)
    pending: int = Field(ge=0)
    running: int = Field(ge=0)
    unknown: int = Field(ge=0)
    cancelled: int = Field(ge=0)
    blocked: int = Field(ge=0)

    @model_validator(mode='after')
    def _one_lane_has_one_stop(self) -> QueueLaneCounts:
        if self.failed + self.landed_red > 1:
            raise ValueError('one novel lane must stop at its first failed/landed_red item')
        if self.cancelled > 1:
            raise ValueError('one novel lane can have at most one cancellation stop')
        return self


class QueueRun(StrictModel):
    """一个持久队列实例的当前视图；盘上由 ``QueueEvent`` 折叠得出。"""

    queue_id: str = Field(min_length=1)
    created: int = Field(ge=0)
    updated: int = Field(ge=0)
    start: QueueStart
    state: QueueRunState
    entries: list[QueueEntry]
    counts_by_sid: dict[str, QueueLaneCounts]

    @model_validator(mode='after')
    def _counts_match_entries(self) -> QueueRun:
        fields = ('done', 'landed_red', 'failed', 'not_run', 'pending', 'running', 'unknown',
                  'cancelled', 'blocked')
        for sid in self.start.sids:
            entries = [entry for entry in self.entries if entry.item.sid == sid]
            want = {field: sum(entry.state == field for entry in entries) for field in fields}
            got = self.counts_by_sid.get(sid)
            if got is None or any(getattr(got, field) != want[field] for field in fields):
                raise ValueError(f'queue counts for {sid!r} do not match its entries')
        if set(self.counts_by_sid) != set(self.start.sids):
            raise ValueError('queue counts must contain exactly the declared novels')
        return self
