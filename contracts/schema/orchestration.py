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

from pydantic import Field, StrictInt, model_validator

from contracts.invariants import (
    AGENT_DELIVERY_OUTCOME,
    AGENT_EXECUTOR_NODES, AGENT_RUN_EVENT_KINDS,
    ARTIFACT_CONDITION, ARTIFACT_DISPLAY, BEACON_STATES,
    AgentDriver, AgentExecutionMode, AgentReasoningEffort,
    FRESHNESS, FRESHNESS_REASON, JOB_EVENT_KINDS, JOB_STATE, NODES,
    OPERATOR_DURATION_SOURCES, OPERATOR_PHASE_EVENT_KINDS, OPERATOR_PHASES,
    OPERATOR_TELEMETRY_SOURCES, OPERATOR_TERMINAL_STATES,
    OVERVIEW_VERDICT, PROGRESS_PHASES, RETIRED_NODES, RETIRED_STAGES, STAGES,
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
AgentDeliveryState = Literal[AGENT_DELIVERY_OUTCOME]
OperatorPhaseEventKind = Literal[OPERATOR_PHASE_EVENT_KINDS]
OperatorPhase = Literal[OPERATOR_PHASES]
OperatorTelemetrySource = Literal[OPERATOR_TELEMETRY_SOURCES]
OperatorTerminalState = Literal[OPERATOR_TERMINAL_STATES]
OperatorDurationSource = Literal[OPERATOR_DURATION_SOURCES]


# ─────────────────────────────────────────────────────────────────────────────
# 零、agent 运行配置 —— 一次选择的精确快照
# ─────────────────────────────────────────────────────────────────────────────

class AgentProfile(FrozenModel):
    """一个 agent 进程的精确运行配置。

    ``driver`` 是本机交互式 CLI，不拿供应商名冒充可执行入口；``model`` 保存
    稳定 id，界面文案由 capability 的独立 ``label`` 给出。这里只守字段形状，
    driver / role / model / effort / mode 的组合由
    ``novel.agent_capabilities::validate_profile`` 对照唯一 registry 关闭式校验。
    """

    driver: AgentDriver
    model: str = Field(min_length=1)
    reasoning_effort: AgentReasoningEffort

    #: ``None`` = 让 registry 选择普通 ``standard``；显式字符串与 reasoning
    #: effort 正交，且仍须由具体 model capability 准入。当前不宣称支持 ``ultra``。
    execution_mode: AgentExecutionMode | None = None


class PipelineAgentProfiles(FrozenModel):
    """一次整书代跑的 commander 与 executor 选择。

    默认 executor 覆盖付费 agent 节点；节点覆盖只写差异。键必须属于
    ``AGENT_EXECUTOR_NODES``，不能拿 stage 名、代码节点或退役节点冒充。
    角色能力与每个 profile 的组合仍由 registry 校验。
    """

    commander: AgentProfile
    executor_default: AgentProfile
    executor_overrides: dict[str, AgentProfile] = Field(default_factory=dict)

    @model_validator(mode='after')
    def _overrides_are_current_nodes(self) -> PipelineAgentProfiles:
        unknown = sorted(set(self.executor_overrides) - set(AGENT_EXECUTOR_NODES))
        if unknown:
            raise ValueError(
                f'executor_overrides 含非 executor 节点 {unknown}；'
                f'只接受 {list(AGENT_EXECUTOR_NODES)}')
        return self


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

    #: 本步真正解析后的 executor 配置。旧 ``steps`` 没有这一格时为 ``None``，
    #: 不得拿当前默认值倒推历史；新执行会在命令 builder 边界补齐。
    executor_profile: AgentProfile | None = None

    #: 本步是否拿到独立的正文外发授权。旧记录没有这一格时为 ``None``；新 executor
    #: 步必须显式冻结 bool，未接 executor 的代码/legacy 步则不得伪造授权快照。
    egress_authorized: bool | None = None

    @model_validator(mode='after')
    def _executor_egress_shape(self) -> Step:
        argv_authorized = '--allow-egress' in self.argv
        if self.executor_profile is None:
            if self.egress_authorized is not None:
                raise ValueError('没有 executor_profile 的 Step 不能携带正文外发授权')
            if argv_authorized:
                raise ValueError('没有 executor_profile 的 Step.argv 不能夹带 --allow-egress')
            return self
        if not self.paid:
            raise ValueError('0 成本 step 不能携带 executor_profile')
        # ``None`` 既要读旧盘，也会由旧记录的公共 HTTP 投影显式发成 null；不能靠
        # fields_set 猜它来自哪一边。新执行的“必须显式 bool”由唯一 builder 边界守。
        if argv_authorized is not (self.egress_authorized is True):
            raise ValueError('Step.egress_authorized 与 argv 的 --allow-egress 不一致')
        return self


# ─────────────────────────────────────────────────────────────────────────────
# 二、作业（`_orchestration.md` §二 · §三）
# ─────────────────────────────────────────────────────────────────────────────

class JobStart(StrictModel):
    """`started` 事件的载荷 —— 一个作业的全部静态字段。"""

    #: ⚠ **必填但可为 `null`**：上传新书那条路不带 sid（那会儿目录还不存在）。
    #: 原型正是在这个 `None` 上把互斥键算成了两个（见 `active_key` 的注释）。
    sid: str | None

    node: str = Field(min_length=1)

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

    #: 本次直接节点作业的 executor 选择。``None`` 只承接旧记录或尚未指定的请求；
    #: 新执行须把解析后的 profile 同时快照到本格与 ``steps[]``。
    executor_profile: AgentProfile | None = None

    #: 与付费 executor Step 同源的正文外发授权快照。旧记录缺字段时为 ``None``；
    #: 新执行必须显式冻结 bool，且不得拿请求默认或今天的配置倒推历史。
    egress_authorized: bool | None = None

    #: 业务 run 身份（TODO-45 批次 3，`R-13.3`）：同一业务节点从首跑、恢复到
    #: 聚合**始终不变**，账本、session 审计、checkpoint 与 run 目录都以它为键。
    #: 旧盘记录没有这一列（可缺、不回填、不按时间猜）；新 writer 必填。
    run_id: str | None = Field(default=None, min_length=1)

    #: 本次 execution 是从哪个旧 job 恢复的。新 job 必有**新** ``job_id``；
    #: 恢复续跑必须同时携带原 ``run_id``，两个身份不得互相冒充。
    recovered_from_job_id: str | None = Field(default=None, min_length=1)

    @model_validator(mode='after')
    def _postconditions(self) -> JobStart:
        if self.recovered_from_job_id is not None and self.run_id is None:
            raise ValueError(
                'recovered_from_job_id 要求 run_id：恢复续跑必须携带原 run 身份')
        if self.node not in NODES and self.node not in RETIRED_NODES:
            raise ValueError(
                f'未知节点 {self.node!r}。当前取值面在 NODES，历史只读面在 RETIRED_NODES')
        # 整份的贵贱 / 写不写，是逐步折叠出来的：第二处算就必然分家（`R-11.7`）。
        if self.paid != any(s.paid for s in self.steps):
            raise ValueError(f'paid={self.paid} 与逐步折叠出来的对不上')
        if self.writes != any(bool(s.writes) for s in self.steps):
            raise ValueError(f'writes={self.writes} 与逐步折叠出来的对不上')
        step_profiles = [
            step.executor_profile for step in self.steps
            if step.executor_profile is not None
        ]
        if self.executor_profile is None:
            if step_profiles:
                raise ValueError('JobStart 缺 executor_profile，但 step 已带 profile')
        else:
            if not step_profiles:
                raise ValueError('JobStart 带 executor_profile，但没有执行 step 承接')
            if any(profile != self.executor_profile for profile in step_profiles):
                raise ValueError('JobStart.executor_profile 与 steps[] 快照不一致')
        if any(step.executor_profile is not None and not step.paid
               for step in self.steps):
            raise ValueError('0 成本 step 不能携带 executor_profile')
        profiled_steps = [
            step for step in self.steps if step.executor_profile is not None
        ]
        if self.executor_profile is None:
            if self.egress_authorized is not None:
                raise ValueError('没有 executor_profile 的 JobStart 不能携带正文外发授权')
        elif self.egress_authorized is None:
            if any(step.egress_authorized is not None for step in profiled_steps):
                raise ValueError('历史 JobStart 缺外发授权，但 step 已带授权快照')
        elif any(step.egress_authorized is not self.egress_authorized
                 for step in profiled_steps):
            raise ValueError('JobStart.egress_authorized 与付费 executor steps[] 不一致')
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

    #: 本条观测所属的跨进程 run。``None`` 只表示旧 JSONL 行没有这一列；
    #: 新 jobs / agent 行由运行时写入口自动补齐，读历史时不猜。
    run_id: str | None = Field(default=None, min_length=1)

    #: 写下这一行的**进程 attempt**（job/execution 身份，TODO-45 批次 3）。
    #: 同一业务 run 恢复后会有多个 execution；旧行可缺，新作业行必填。
    execution_id: str | None = Field(default=None, min_length=1)

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


class OperatorPhaseEvent(FrozenModel):
    """Commander/operator 本地阶段的一行持久 wire（v1）。

    它不带 node、model、usage、token、prompt、正文或自由理由；``action`` /
    ``error_kind`` 只接受短小 ASCII kind。这样人工审阅与终审动作既不会污染
    ``NODES``/成本账，也不能借 telemetry 成为正文外泄暗门。
    """

    wire_version: Literal[1] = 1
    session_id: str = Field(pattern=r'^[a-z0-9][a-z0-9_-]{0,127}$')
    sid: str = Field(min_length=1, max_length=128)
    source: OperatorTelemetrySource
    kind: OperatorPhaseEventKind
    ts_ms: StrictInt = Field(ge=0)
    parent_run_id: str | None = Field(
        default=None, pattern=r'^[a-z0-9][a-z0-9_-]{0,127}$')
    phase_execution_id: str | None = Field(
        default=None, pattern=r'^[a-z0-9][a-z0-9_-]{0,127}$')
    phase: OperatorPhase | None = None
    action: str | None = Field(
        default=None, pattern=r'^[a-z][a-z0-9_.-]{0,127}$')
    run_id: str | None = Field(
        default=None, pattern=r'^[a-z0-9][a-z0-9_-]{0,127}$')
    state: OperatorTerminalState | None = None
    exit_code: StrictInt | None = None
    elapsed_ms: StrictInt | None = Field(default=None, ge=0)
    duration_source: OperatorDurationSource | None = None
    evidence_sha256: str | None = Field(
        default=None, pattern=r'^[0-9a-f]{64}$')
    error_kind: str | None = Field(
        default=None, pattern=r'^[a-z][a-z0-9_.-]{0,127}$')
    start_code_version: str | None = Field(default=None, min_length=1, max_length=256)
    end_code_version: str | None = Field(default=None, min_length=1, max_length=256)

    @model_validator(mode='after')
    def _payload_matches_kind(self) -> OperatorPhaseEvent:
        if self.kind == 'session_started':
            carried = (
                self.phase_execution_id, self.phase, self.action, self.run_id,
                self.state, self.exit_code, self.elapsed_ms, self.duration_source,
                self.evidence_sha256, self.error_kind, self.end_code_version,
            )
            if self.start_code_version is None or any(v is not None for v in carried):
                raise ValueError(
                    'session_started 只带 session 静态身份与 start_code_version')
            return self
        if self.kind == 'phase_started':
            if self.phase_execution_id is None or self.phase is None:
                raise ValueError('phase_started 必须带 phase_execution_id 与 phase')
            carried = (
                self.parent_run_id, self.state, self.exit_code, self.elapsed_ms,
                self.duration_source, self.error_kind, self.start_code_version,
                self.end_code_version,
            )
            if any(v is not None for v in carried):
                raise ValueError('phase_started 不得提前带终态/耗时/代码终点')
            return self
        if self.kind == 'phase_ended':
            if (self.phase_execution_id is None or self.phase is None
                    or self.state is None):
                raise ValueError(
                    'phase_ended 必须带 phase_execution_id、phase 与 state')
            if self.elapsed_ms is None or self.duration_source is None:
                raise ValueError('phase_ended 必须带真实 elapsed_ms 与 duration_source')
            if any(v is not None for v in (
                    self.parent_run_id, self.start_code_version,
                    self.end_code_version)):
                raise ValueError('phase_ended 不带 session 级代码/父 run 身份')
            if self.state == 'failed' and self.error_kind is None:
                raise ValueError('failed phase 必须带 error_kind')
            return self
        carried = (
            self.parent_run_id, self.phase_execution_id, self.phase, self.action,
            self.run_id, self.exit_code, self.evidence_sha256, self.error_kind,
            self.start_code_version,
        )
        if any(v is not None for v in carried):
            raise ValueError(
                'session_ended 只带 session 终态、耗时与 end_code_version')
        if (self.state is None or self.elapsed_ms is None
                or self.duration_source is None or self.end_code_version is None):
            raise ValueError(
                'session_ended 必须带 state、elapsed_ms、duration_source 与代码终点')
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

    ⚠ `argv` 与 `prompt` **分栏全文留档**：Commander 与它调度的逐节点
    executor 都可能产生外部模型费用，审计与终审包要能看到
    「到底跑了哪条命令、授权声明原文是什么」。prompt 只作为
    Commander stdin 传入，绝不出现在 `argv` 或进程日志里。
    """

    sid: str = Field(min_length=1)

    #: 本次整书代跑所绑定的规范化绝对 base。旧事件没有这一格时为 ``None``；
    #: 新运行必须由 launcher 显式写入，child CLI 才能用 ``run_id + contract SHA``
    #: 从 append-only 事件流反查并核验同一份配置，不能只信一段可被改写的环境 JSON。
    base: str | None = Field(default=None, min_length=1)

    #: 解析后的真实 Commander 命令行（可安全回显，不含 prompt）。
    argv: list[str] = Field(min_length=1)

    #: 发给 agent 的提示词全文。两项授权声明都在里面；下面两格只是机读摘要。
    prompt: str = Field(min_length=1)

    #: 显式付费总授权（SKILL 接口契约）：True = 付费命令直接执行；False = 首个付费节点前停。
    authorized: bool

    #: 正文外发授权，与付费授权正交；每一条 started 事件都必须显式记录。
    egress_authorized: bool

    #: 启动时的仓库代码指纹。旧事件没有这一格时为 `None`，只能把最终交付判为
    #: unknown；新 run 在结束时必须与当前指纹一致，避免一次跑跨两版代码却冒充可复现。
    code_version: str | None = None

    #: commander + 默认 executor + 节点覆盖的解析后快照。旧事件没有这一格时为
    #: ``None``，只能显示 unknown，绝不按今天的默认配置回填昨天的运行身份。
    profiles: PipelineAgentProfiles | None = None


class AgentDeliveryOutcome(FrozenModel):
    """外层进程结束后，服务端从当前盘面重新得出的交付结论。"""

    state: AgentDeliveryState
    reason: str = Field(min_length=1)
    checked_at: int = Field(ge=0)


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

    #: 新版 `ended` 事件随退出码一起带；旧事件缺失时折叠为 outcome=unknown。
    outcome: AgentDeliveryOutcome | None = None

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
        if self.outcome is not None and self.kind != 'ended':
            raise ValueError('只有 ended 事件可以带 outcome')
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

    #: 与 `state` 正交：前者只讲进程，后者讲盘面是否真正可交付。
    outcome: AgentDeliveryOutcome | None = None

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
        if self.outcome is not None and self.exit_code is None:
            raise ValueError('没有 ended/exit_code 时不得声称已有交付终验')
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

    #: 入队时已解析并深拷贝的完整选择。出队只消费这份快照，不再读取默认值，
    #: 也不重新解释请求对象；因此等待期间前端对象或服务默认变化都不会漂移运行身份。
    profiles: PipelineAgentProfiles


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

    #: `<stage>/<文件名>`。**经 `contracts/schema/__init__.py::artifact_spec` 路由**，
    #: 动态章路径也要落回注册模板；不新建第二张清单。
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
    #: 实测就撞上了：stage5 的产物是**逐章**的（`stage5/chNN.json`），
    #: 「该有几章」要去读 stage4 计划里的输出章；计划读不回来时，
    #: 盘上有 4 章这件事**既不能证明跑完了、也不能证明没跑完**。
    #: 压成 `False` 会让队列一次次去重跑一个可能已经写完的付费层——
    #: 那是**真金白银**，不是一个显示问题。
    #:
    #: ⚠ **`.INVALID` 不计入已完成**，它进 inbox（`INBOX_KIND` 已有 `invalid` 这个值）。
    #: ⚠ 普通具名产物的 **`freshness == 'unknown'` 计入已完成**，这不是漏判：
    #: 当前七层仍可能读到缺少 `consumed` 投影的早期产物；把它当未完成会让整条链
    #: 永远跑不出第一步。
    #: 「不知道它新不新」与「它是旧的」是两件事——**后者才该挡路**。
    #: Stage5 是例外：它的 `done=True` 会直接让付费写作 SKIP，且退役 `draft.md`
    #: 后 canonical 章就是唯一证据，因此必须逐章达到 `fresh`；未知不能冒充当前。
    done: bool | None

    #: 逐章类的层（stage5）：canonical `stage5/chNN.json` 已落盘的章。
    #: **必须排除 `.INVALID.json`**——
    #: `chNN.json` 的存在本身就是「过了写作时的落盘闸门」这个语义，
    #: 把被拦下的那份数进去就把语义反过来了。
    chapters_done: list[int] = Field(default_factory=list)

    #: 旧 `stage5/deep|shallow` 盘面是一个明确终态，不是 missing。
    #: 只读历史路径可展示，但续跑/队列必须停下来要求显式重建。
    legacy_incompatible: bool = False
    legacy_paths: list[str] = Field(default_factory=list)

    #: stage5.review 的唯一 currentness 结论；其他 stage 三格恒为空。
    review_state: str | None = None
    review_reasons: list[str] = Field(default_factory=list)
    review_rel: str | None = None

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

        ⚠ Stage5 的 ``artifacts`` 只携带不可用或未知的 canonical 章；干净书不重复展示
        全部章稿。它的完整完成度证据仍在**计划 + 逐章成稿**
        （`state.stage_state` 的 stage5 分支），不在本模型携带的字段里——
        载体在这里能「重算」的东西是零，硬立一条
        「只能是 None」就是拿结构断言一件结构看不见的事。
        这条教训是实测买来的：第一本全链跑完的书（e2e_20260813）`done=True`
        被旧断言当场拒收，`/api/novels` 对它静默跳过，**语料库屏上它整本消失**。
        「计划读不回 ⇒ None、不许压成 False」那半句仍然成立，但它守在**产地**
        （`planned_chapters` 返回 None 的那条路），不在载体。
        """
        if self.legacy_incompatible != bool(self.legacy_paths):
            raise ValueError('legacy_incompatible 与 legacy_paths 必须同时出现或同时缺席')
        if self.stage != 'stage5' and (
            self.review_state is not None or self.review_reasons or self.review_rel is not None
        ):
            raise ValueError('review_state 只属于 stage5')
        if self.review_state == 'fresh' and self.review_reasons:
            raise ValueError('fresh Stage5 Review 不得携带失效原因')
        if self.review_state is None and (self.review_reasons or self.review_rel is not None):
            raise ValueError('review_state 缺席时不得携带 review 明细')
        if self.chapters_done != sorted(set(self.chapters_done)):
            raise ValueError('chapters_done 必须是升序去重章号')
        if self.stage == 'stage5':
            if self.done is True and (
                self.legacy_incompatible
                or not self.chapters_done
                or self.artifacts
            ):
                raise ValueError(
                    'stage5 done=True 必须有当前、可读的 canonical 章稿，'
                    '且不得携带章级问题或 legacy')
            return self
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
    #: 语料库列表复用本模型，因此不用对每本书再发一次 `/stage1`。
    chapters: int | None = Field(default=None, ge=1)

    stages: list[StageState] = Field(default_factory=list)

    #: 旧 v1 `stage5/deep|shallow` 盘面的只读证据路径；空列表 = 没有 legacy。
    stage5_legacy_paths: list[str] = Field(default_factory=list)

    summary: OverviewSummary


# ─────────────────────────────────────────────────────────────────────────────
# 四、队列（`_orchestration.md` §五 · `R-13`）
# ─────────────────────────────────────────────────────────────────────────────

class QueueItem(StrictModel):
    """队列里的一项 = 一本书的一个节点跑法。"""

    sid: str = Field(min_length=1)
    node: str = Field(min_length=1)
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
        if self.until_stage not in STAGES and self.until_stage not in RETIRED_STAGES:
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
