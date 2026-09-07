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

import hashlib
import json
from collections.abc import Mapping
from typing import Annotated, Any, Literal

from pydantic import Field, StrictInt, model_validator

from contracts.invariants import (
    AGENT_DELIVERY_OUTCOME,
    AGENT_EXECUTOR_NODES, AGENT_RUN_CONTROLS, AGENT_RUN_EVENT_KINDS,
    AGENT_RUN_EVENT_V2_KINDS, AGENT_RUN_STATE,
    ARTIFACT_CONDITION, ARTIFACT_DISPLAY, BEACON_STATES,
    COMMANDER_ADMISSION_EVENT_KINDS, COMMANDER_ADMISSION_TAIL_REPAIR_KINDS,
    AgentDriver, AgentExecutionMode, AgentReasoningEffort,
    LegacyLlmProvider,
    FRESHNESS, FRESHNESS_REASON, JOB_EVENT_KINDS, JOB_STATE, NODES,
    OPERATOR_DURATION_SOURCES, OPERATOR_PHASE_EVENT_KINDS, OPERATOR_PHASES,
    OPERATOR_TELEMETRY_SOURCES, OPERATOR_TERMINAL_STATES,
    OVERVIEW_VERDICT, PROGRESS_PHASES, RESUME_VERDICTS,
    RETIRED_NODES, RETIRED_STAGES, REVIEWER_APPROVAL_SURFACES,
    RUN_NEXT_HANDLERS,
    RUN_RESUME_FRONTIER_STATES, STAGES,
    EXECUTION_EPOCH_DRAIN_STATES, EXECUTION_EPOCH_REASONS,
    COMMANDER_ATTEMPT_KINDS, COMMANDER_REVIEWER_MAX_CORRECTIONS,
    STAGE2_COMMANDER_MAX_EDIT_SEAT,
    STAGE2_COMMANDER_MAX_SEATS, STAGE2_COMMANDER_SEAT_PURPOSES,
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
AgentRunState = Literal[AGENT_RUN_STATE]
AgentRunControl = Literal[AGENT_RUN_CONTROLS]
AgentRunEventV2Kind = Literal[AGENT_RUN_EVENT_V2_KINDS]
ExecutionEpochReason = Literal[EXECUTION_EPOCH_REASONS]
ExecutionEpochDrainState = Literal[EXECUTION_EPOCH_DRAIN_STATES]
OperatorPhaseEventKind = Literal[OPERATOR_PHASE_EVENT_KINDS]
OperatorPhase = Literal[OPERATOR_PHASES]
OperatorTelemetrySource = Literal[OPERATOR_TELEMETRY_SOURCES]
OperatorTerminalState = Literal[OPERATOR_TERMINAL_STATES]
OperatorDurationSource = Literal[OPERATOR_DURATION_SOURCES]
RunResumeFrontierState = Literal[RUN_RESUME_FRONTIER_STATES]
RunNextHandler = Literal[RUN_NEXT_HANDLERS]
RunResumeVerdict = Literal[RESUME_VERDICTS]


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


class LegacyLlmProfile(FrozenModel):
    """一个直接 LLM API executor 的精确、无密钥运行配置。

    ``provider`` 是稳定 adapter id，不是 base URL；密钥、代理、超时与重试仍由
    provider 自己的安全运行配置管理，不能写进设置或历史事件。model / effort /
    thinking 的组合由 ``novel.llm.providers`` 关闭式校验，使未来 GLM driver 不会
    意外继承 DeepSeek 的模型兜底、tokenizer 或请求参数。
    """

    provider: LegacyLlmProvider
    model: str = Field(min_length=1)
    reasoning_effort: str = Field(min_length=1)
    thinking_mode: str = Field(min_length=1)


class AgentExecutorBackend(FrozenModel):
    """由本地 Agent CLI 承接 executor。"""

    kind: Literal['agent'] = 'agent'
    profile: AgentProfile


class LegacyExecutorBackend(FrozenModel):
    """由直接 LLM API adapter 承接 executor。"""

    kind: Literal['legacy'] = 'legacy'
    profile: LegacyLlmProfile


ExecutorBackend = Annotated[
    AgentExecutorBackend | LegacyExecutorBackend,
    Field(discriminator='kind'),
]


def _backend_from_legacy_profile(value: Any) -> Any:
    """旧 ``AgentProfile`` wire → 显式 agent backend；只做无损形状迁移。"""
    if isinstance(value, AgentProfile):
        return AgentExecutorBackend(profile=value)
    if isinstance(value, Mapping) and 'kind' not in value and 'driver' in value:
        return {'kind': 'agent', 'profile': dict(value)}
    return value


class PipelineAgentProfiles(FrozenModel):
    """一次整书代跑的 commander 与显式 executor backend 选择。

    ``schema_version=2`` 起 executor 不再用 ``AgentProfile | None`` 暗示模式：
    ``agent`` 携 ``AgentProfile``，``legacy`` 携 ``LegacyLlmProfile``。旧 v1
    profile 只会被无损包成 ``kind=agent``，新序列化永远写显式分辨字段。

    默认 backend 覆盖付费 executor 节点；节点覆盖只写差异。键必须属于
    ``AGENT_EXECUTOR_NODES``，不能拿 stage 名、代码节点或退役节点冒充。
    角色能力与每个 backend/profile 的组合仍由 registry 校验。
    """

    schema_version: Literal[2] = 2
    commander: AgentProfile
    executor_default: ExecutorBackend
    executor_overrides: dict[str, ExecutorBackend] = Field(default_factory=dict)

    @model_validator(mode='before')
    @classmethod
    def _migrate_v1_agent_profiles(cls, value: Any) -> Any:
        if not isinstance(value, Mapping):
            return value
        data = dict(value)
        if data.get('schema_version') in (None, 1):
            data['schema_version'] = 2
        if 'executor_default' in data:
            data['executor_default'] = _backend_from_legacy_profile(
                data['executor_default'])
        overrides = data.get('executor_overrides')
        if isinstance(overrides, Mapping):
            data['executor_overrides'] = {
                str(node): _backend_from_legacy_profile(profile)
                for node, profile in overrides.items()
            }
        return data

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

    #: 本步真正解析后的 executor backend。旧 ``steps.executor_profile`` 会在读取
    #: 边界无损包成 ``kind=agent``；旧 legacy 记录两格都缺，只能保持 unknown，
    #: 不得拿当前默认值倒推历史。新 AgentRun 执行在 builder 边界显式补齐。
    executor_backend: ExecutorBackend | None = None

    #: 本步是否拿到独立的正文外发授权。旧记录没有这一格时为 ``None``；新 executor
    #: 步必须显式冻结 bool，未接 executor 的代码/兼容步则不得伪造授权快照。
    egress_authorized: bool | None = None

    @model_validator(mode='before')
    @classmethod
    def _migrate_executor_profile(cls, value: Any) -> Any:
        if not isinstance(value, Mapping) or 'executor_profile' not in value:
            return value
        data = dict(value)
        old = data.pop('executor_profile')
        if old is not None:
            if data.get('executor_backend') is not None:
                raise ValueError('Step 不能同时携带 executor_backend 与旧 executor_profile')
            data['executor_backend'] = _backend_from_legacy_profile(old)
        return data

    @property
    def executor_profile(self) -> AgentProfile | None:
        """旧 Python 消费方的只读投影；分流必须改看 ``executor_backend.kind``。"""
        backend = self.executor_backend
        return backend.profile if isinstance(backend, AgentExecutorBackend) else None

    @model_validator(mode='after')
    def _executor_egress_shape(self) -> Step:
        argv_authorized = '--allow-egress' in self.argv
        if self.executor_backend is None:
            if self.egress_authorized is not None:
                raise ValueError('没有 executor_backend 的 Step 不能携带正文外发授权')
            if argv_authorized:
                raise ValueError('没有 executor_backend 的 Step.argv 不能夹带 --allow-egress')
            return self
        if not self.paid:
            raise ValueError('0 成本 step 不能携带 executor_backend')
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

    #: 本次直接节点作业的 executor 选择。旧 ``executor_profile`` 只读迁移成
    #: agent backend；新执行须把解析后的 backend 同时快照到本格与 ``steps[]``。
    executor_backend: ExecutorBackend | None = None

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

    @model_validator(mode='before')
    @classmethod
    def _migrate_executor_profile(cls, value: Any) -> Any:
        if not isinstance(value, Mapping) or 'executor_profile' not in value:
            return value
        data = dict(value)
        old = data.pop('executor_profile')
        if old is not None:
            if data.get('executor_backend') is not None:
                raise ValueError('JobStart 不能同时携带 executor_backend 与旧 executor_profile')
            data['executor_backend'] = _backend_from_legacy_profile(old)
        return data

    @property
    def executor_profile(self) -> AgentProfile | None:
        """旧 Python 消费方的只读 agent 投影。"""
        backend = self.executor_backend
        return backend.profile if isinstance(backend, AgentExecutorBackend) else None

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
        step_backends = [
            step.executor_backend for step in self.steps
            if step.executor_backend is not None
        ]
        if self.executor_backend is None:
            if step_backends:
                raise ValueError('JobStart 缺 executor_backend，但 step 已带 backend')
        else:
            if not step_backends:
                raise ValueError('JobStart 带 executor_backend，但没有执行 step 承接')
            if any(backend != self.executor_backend for backend in step_backends):
                raise ValueError('JobStart.executor_backend 与 steps[] 快照不一致')
        if any(step.executor_backend is not None and not step.paid
               for step in self.steps):
            raise ValueError('0 成本 step 不能携带 executor_backend')
        profiled_steps = [
            step for step in self.steps if step.executor_backend is not None
        ]
        if self.executor_backend is None:
            if self.egress_authorized is not None:
                raise ValueError('没有 executor_backend 的 JobStart 不能携带正文外发授权')
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


class RunEventView(ProgressEvent):
    """运行页时间线的一行；在原始信标外携服务端折叠的观察态。

    ``ProgressEvent`` 是 append-only 原始证据，不能为界面回写派生字段；本 view
    只用于 HTTP 响应。这样 exit 2 的 ``stopped`` 与真正失败仍由后端唯一判定，
    浏览器不再从裸退出码另造第二套状态机。
    """

    beacon: BeaconState


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


class RunResumeFrontierView(StrictModel):
    """运行页的权威续跑前沿；只投影 operator，不从信标历史猜。"""

    state: RunResumeFrontierState
    node: str | None = None
    seq: int | None = Field(default=None, ge=1)
    label: str | None = None
    resume_verdict: RunResumeVerdict | None = None
    why: str = Field(min_length=1)
    waiting_on: str | None = None
    paid: bool | None = None
    writes: str | None = None
    next_handler: RunNextHandler
    #: 当前同一 planner 快照明确判为无需再次执行、且位于续跑点之前的节点。
    verified_skipped_nodes: list[str] = Field(default_factory=list)
    verified_skipped_count: int = Field(ge=0)

    @model_validator(mode='after')
    def _frontier_is_closed(self) -> RunResumeFrontierView:
        if self.verified_skipped_count != len(self.verified_skipped_nodes):
            raise ValueError('verified_skipped_count 与节点列表不一致')
        rank = {name: index for index, name in enumerate(NODES)}
        if (
            len(self.verified_skipped_nodes)
            != len(set(self.verified_skipped_nodes))
            or any(node not in rank for node in self.verified_skipped_nodes)
            or self.verified_skipped_nodes != sorted(
                self.verified_skipped_nodes, key=rank.__getitem__)
        ):
            raise ValueError('verified skipped 节点必须来自 NODES、按序且不重复')
        action_values = (
            self.node, self.seq, self.label, self.resume_verdict,
            self.paid, self.writes,
        )
        if self.state in ('unknown', 'pipeline_complete'):
            if any(value is not None for value in action_values) \
                    or self.waiting_on is not None:
                raise ValueError(f'{self.state} 不得携带伪造的下一动作')
            expected = 'unknown' if self.state == 'unknown' else 'none'
            if self.next_handler != expected:
                raise ValueError(f'{self.state} 的 next_handler 必须是 {expected}')
            expected_skipped = [] if self.state == 'unknown' else list(NODES)
            if self.verified_skipped_nodes != expected_skipped:
                raise ValueError(
                    f'{self.state} 的 verified skipped 前缀不完整')
            return self
        if any(value is None for value in action_values):
            raise ValueError(f'{self.state} 必须携带完整下一动作')
        assert self.node is not None and self.seq is not None
        if self.node not in rank or self.seq != rank[self.node] + 1:
            raise ValueError('续跑点 node/seq 必须与 NODES 的唯一顺序一致')
        if self.label != NODES[self.node].label:
            raise ValueError('续跑点 label 必须来自 NODES，不能由调用方另造')
        if any(rank[node] >= rank[self.node]
               for node in self.verified_skipped_nodes):
            raise ValueError('verified skipped 节点必须位于续跑点之前')
        if self.verified_skipped_nodes != list(NODES)[:rank[self.node]]:
            raise ValueError('verified skipped 必须是续跑点之前的完整 NODES 前缀')
        if self.state == 'ready' and self.waiting_on is not None:
            raise ValueError('ready 续跑点不得携带 waiting_on')
        if self.state == 'ready':
            expected = 'executor' if self.paid else 'code'
            if self.next_handler != expected:
                raise ValueError(f'ready 的 next_handler 必须是 {expected}')
        if self.state == 'blocked' and not self.waiting_on:
            raise ValueError('blocked 续跑点必须明确 waiting_on')
        if self.state == 'blocked' and self.next_handler not in {
                'commander', 'executor', 'human'}:
            raise ValueError('blocked 的 next_handler 必须是 commander/executor/human')
        return self


class RunProgressView(StrictModel):
    """`GET /api/novels/{sid}/run` 的响应：信标折叠 + 产物盘的完成度，两栏并排。"""

    sid: str = Field(min_length=1)
    resume_frontier: RunResumeFrontierView
    steps: list[RunStepView]
    #: `STAGES` → done 三态。书目录还不存在时全 `None`
    #: （stage0.remove 打点先于书目录落地，进度屏那时也得开得开）。
    stage_done: dict[str, bool | None]
    #: 最近若干条信标事件（人读时间线，文件序——新的在后）。
    events_tail: list[RunEventView]


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

    #: Commander 启动时 canonical operator skill 整树（SKILL + references +
    #: scripts + assets）的原始字节契约。旧事件没有这一格时为
    #: ``None``，只能读为未记录，不得拿今天的 skill 回填。
    skill_sha256: str | None = Field(
        default=None, pattern=r'^[0-9a-f]{64}$')

    #: commander + 默认 executor + 节点覆盖的解析后快照。旧事件没有这一格时为
    #: ``None``，只能显示 unknown，绝不按今天的默认配置回填昨天的运行身份。
    profiles: PipelineAgentProfiles | None = None


class RunContractV2(FrozenModel):
    """整本书 parent run 真正稳定的不可变契约（wire v2）。

    parent 只绑定书、base、两项授权与解析后的 profiles。命令、prompt、代码和
    skill 都是可滚动 execution epoch 的事实，不能再混进 parent 内容地址。

    ``legacy_*`` 四格只负责解码本功能落地期间已经写出的早期 v2 started 行；
    它们在序列化和 contract hash 面上均被排除。新 writer 把首次启动审计快照
    放在同一 started 事件的 ``start`` 兼容投影中，不再污染 parent identity。
    """

    wire_version: Literal[2] = 2
    sid: str = Field(min_length=1)
    base: str = Field(min_length=1)
    authorized: bool
    egress_authorized: bool
    profiles: PipelineAgentProfiles

    legacy_argv: list[str] | None = Field(
        default=None, alias='argv', exclude=True)
    legacy_prompt: str | None = Field(
        default=None, alias='prompt', exclude=True)
    legacy_code_version: str | None = Field(
        default=None, alias='code_version', exclude=True)
    legacy_skill_sha256: str | None = Field(
        default=None, alias='skill_sha256', exclude=True,
        pattern=r'^[0-9a-f]{64}$')

    def legacy_start(self) -> AgentRunStart | None:
        """把早期 v2 内嵌快照投影回旧 UI；新 contract 永远返回 ``None``。"""
        if self.legacy_argv is None or self.legacy_prompt is None:
            return None
        return AgentRunStart(
            sid=self.sid, base=self.base,
            argv=self.legacy_argv, prompt=self.legacy_prompt,
            authorized=self.authorized,
            egress_authorized=self.egress_authorized,
            code_version=self.legacy_code_version,
            skill_sha256=self.legacy_skill_sha256,
            profiles=self.profiles)


class ExecutionEpoch(FrozenModel):
    """同一 parent run 内一段身份稳定的 Commander 执行窗口。

    一个 epoch 可以包含多个 provider turn；只要代码、skill 与 Commander
    profile 身份没有变化，Supervisor 就在同一 epoch 内精确续会。发生代码或
    skill 漂移时关闭旧 epoch、另开新 epoch，但仍保留同一个 parent run。
    Stage4 typed repair/replan 还是显式的 producer 边界：即使身份未漂移，也要在
    执行动作前以 ``reason=continue`` 打开 fresh epoch，不能让修前与修后 aggregate
    共用同一 ``session_run_id``。
    已结束 producer 留下的 prepared aggregate **提交补尾**不是新的模型 repair/replan：
    它在当前 recovery epoch 内执行 FREE/0-wire deterministic action，不续 source
    provider，也不得把 source run 填进 ``resume_provider_session_id``。
    """

    epoch_id: str = Field(pattern=r'^[a-z0-9][a-z0-9_-]{0,127}$')
    ordinal: int = Field(ge=1)
    reason: ExecutionEpochReason
    argv: list[str] = Field(min_length=1)
    prompt_sha256: str = Field(pattern=r'^[0-9a-f]{64}$')
    protocol_version: str = Field(min_length=1)
    cli_version: str | None = Field(default=None, min_length=1)
    code_version: str = Field(min_length=1)
    skill_sha256: str = Field(pattern=r'^[0-9a-f]{64}$')
    session_run_id: str = Field(pattern=r'^[a-z0-9][a-z0-9_-]{0,127}$')

    #: 新 epoch 若沿用既有供应商会话，必须把精确 id 先写进 opened 事件；
    #: ``None`` 只表示首次创建 provider session，不能解释成“可任选一个会话”。
    resume_provider_session_id: str | None = Field(default=None, min_length=1)


CommanderAdmissionEventKind = Literal[COMMANDER_ADMISSION_EVENT_KINDS]


class CommanderAdmissionEvent(FrozenModel):
    """Commander execution admission 的一行持久 wire（v1 / v2）。

    ``opened/closed`` 管父 run 是否还能接纳新执行；``claimed/released`` 精确
    标识一个 ``run_step --execute`` 的完整 Step 序列；``drained/reconciled``
    分别记录正常双面闭合与显式人工恢复。六态共用同一 append-only 文件与同一
    事务锁，close 和 claim 因而有唯一、可重放的先后关系。
    """

    wire_version: Literal[1, 2] = 1
    parent_run_id: str = Field(pattern=r'^[a-z0-9][a-z0-9_-]{0,127}$')
    #: gate 自身身份与持有 OS owner lease、写 progress beacon 的外层生命周期
    #: 可以不同。v2 把这条联结逐行固化；v1 缺失时只能由恢复层依据唯一
    #: AgentRun ``epoch_opened`` 事实显式解析，绝不能按时间或字符串前缀猜。
    lifecycle_run_id: str | None = Field(
        default=None, pattern=r'^[a-z0-9][a-z0-9_-]{0,127}$')
    sid: str = Field(min_length=1, max_length=128)
    kind: CommanderAdmissionEventKind
    ts: int = Field(ge=0)
    claim_id: str | None = Field(
        default=None, pattern=r'^[a-z0-9][a-z0-9_-]{0,127}$')
    node: str | None = Field(default=None, min_length=1)
    claim_sha256: str | None = Field(
        default=None, pattern=r'^[0-9a-f]{64}$')
    actor: str | None = Field(default=None, min_length=1, max_length=256)
    reason: str | None = Field(default=None, min_length=1, max_length=2000)
    subject_sha256: str | None = Field(
        default=None, pattern=r'^[0-9a-f]{64}$')
    force: bool | None = None

    @model_validator(mode='after')
    def _payload_matches_kind(self) -> CommanderAdmissionEvent:
        if self.wire_version == 1:
            if self.lifecycle_run_id is not None:
                raise ValueError('wire v1 不得携带 lifecycle_run_id')
        elif self.lifecycle_run_id is None:
            raise ValueError('wire v2 必须携带 lifecycle_run_id')
        claim_payload = (self.claim_id, self.node, self.claim_sha256)
        audit_payload = (self.actor, self.reason, self.subject_sha256, self.force)
        if self.kind in ('opened', 'closed', 'drained'):
            if any(value is not None for value in claim_payload + audit_payload):
                raise ValueError(f'{self.kind} 不得携带 claim/人工 reconcile 载荷')
            return self
        if self.kind in ('claimed', 'released'):
            if any(value is None for value in claim_payload):
                raise ValueError(f'{self.kind} 必须携带完整 claim_id/node/claim_sha256')
            if any(value is not None for value in audit_payload):
                raise ValueError(f'{self.kind} 不得携带人工 reconcile 载荷')
            return self
        if any(value is not None for value in claim_payload):
            raise ValueError('reconciled 不得冒充 claim/release')
        if any(value is None for value in audit_payload):
            raise ValueError(
                'reconciled 必须携带完整 actor/reason/subject_sha256/force')
        return self


CommanderAdmissionTailRepairKind = Literal[
    COMMANDER_ADMISSION_TAIL_REPAIR_KINDS]
BeaconTailRepairKind = CommanderAdmissionTailRepairKind


class AppendTailRepairRecord(FrozenModel):
    """non-LF append 尾的 immutable、hash-bound 两相恢复公共形状。"""

    wire_version: Literal[1] = 1
    kind: Literal['authorized', 'applied']
    ts: int = Field(ge=0)
    actor: str = Field(min_length=1, max_length=256)
    reason: str = Field(min_length=1, max_length=2000)
    subject_sha256: str = Field(pattern=r'^[0-9a-f]{64}$')
    committed_size: int = Field(ge=0)
    committed_sha256: str = Field(pattern=r'^[0-9a-f]{64}$')
    tail_size: int = Field(gt=0)
    tail_sha256: str = Field(pattern=r'^[0-9a-f]{64}$')
    quarantine_name: str = Field(
        pattern=r'^[0-9a-f]{64}\.tail\.bin$')


class CommanderAdmissionTailRepairRecord(AppendTailRepairRecord):
    """Commander admission 账坏尾的两相恢复记录（v1）。"""

    kind: CommanderAdmissionTailRepairKind


class BeaconTailRepairRecord(AppendTailRepairRecord):
    """进度 beacon 账坏尾的两相恢复记录（v1）。"""

    kind: BeaconTailRepairKind


REVIEWER_ACTION_RECORD_KIND = 'novel-reviewer-action-record-v1'


class CommanderSessionPointer(FrozenModel):
    """Commander 子进程首个协议事件到达时在线落盘的 provider session 指针。

    它不是审计账也不是预算：审计仍在 ``agent_sessions.jsonl``，席位仍在
    Stage2 席位账。它只回答“这一席当前 attempt 对应供应商哪一个 session”，
    并 hash-bound 到本次 prompt/schema/profile；宿主崩溃后运维可据此判断
    该 session 能否续接，而不必等进程退出才知道 id。
    """

    kind: Literal['novel-commander-session-pointer-v1'] = (
        'novel-commander-session-pointer-v1')
    sid: str = Field(min_length=1)
    parent_run_id: str = Field(min_length=1)
    epoch_id: str = Field(min_length=1)
    session_run_id: str = Field(min_length=1)
    attempt_id: str = Field(min_length=1)
    attempt_kind: Literal[COMMANDER_ATTEMPT_KINDS]
    provider_session_id: str = Field(min_length=1)
    driver: str = Field(min_length=1)
    model: str = Field(min_length=1)
    prompt_sha256: str = Field(pattern=r'^[0-9a-f]{64}$')
    schema_sha256: str = Field(pattern=r'^[0-9a-f]{64}$')
    observed_at: int = Field(ge=0)


class Stage2ReviewAttempt(FrozenModel):
    """同一席位内的一次真实 CLI 进程尝试。

    ``launch`` 是新进程首发；``resume`` 用供应商 exact session id 续接同一
    provider 会话。两者都记在同一行席位下，不新开席位、不重复预留预算。
    ``wire_evidence`` 只在收到供应商首个协议事件（session/thread id）或 usage
    后才为真：没有它的失败是 0-wire 本地失败，恢复层可据此判断是否只需 relaunch。
    """

    attempt_id: str = Field(min_length=1)
    kind: Literal[COMMANDER_ATTEMPT_KINDS]
    started_at: int = Field(ge=0)
    finished_at: int | None = Field(default=None, ge=0)
    exit_code: int | None = None
    error_kind: str | None = None
    provider_session_id: str | None = None
    wire_evidence: bool = False

    @model_validator(mode='after')
    def _finish_fields_agree(self) -> Stage2ReviewAttempt:
        if (self.finished_at is None) != (self.exit_code is None):
            raise ValueError('attempt 的 finished_at 与 exit_code 必须同进同出')
        if self.kind == 'resume' and not self.provider_session_id:
            raise ValueError('resume attempt 必须携带被续接的 provider session id')
        return self


class Stage2ReviewRejection(FrozenModel):
    """Commander 已成功交付回答、但 typed/canonical 边界拒绝的事实。"""

    kind: Literal['typed_loader', 'canonical_action']
    request_sha256: str = Field(pattern=r'^[0-9a-f]{64}$')
    reason_sha256: str = Field(pattern=r'^[0-9a-f]{64}$')
    reason: str = Field(min_length=1, max_length=8192)
    recorded_at: int = Field(ge=0)


class Stage2ReviewLedgerTurn(FrozenModel):
    """一席已经预留的 Stage2 Commander 席位；started 也消耗席位。

    ``purpose`` 是席位语义而不是标签：``final_review`` 只能是只读终审，
    ``review`` / ``correction`` / ``prereview`` 只能落在前
    ``STAGE2_COMMANDER_MAX_EDIT_SEAT`` 席。``attempts`` 记录同席内的真实进程
    尝试（首发与 resume），它们共享同一 logical session 与同一格预算。
    """

    ordinal: int = Field(ge=1, le=STAGE2_COMMANDER_MAX_SEATS)
    purpose: Literal[STAGE2_COMMANDER_SEAT_PURPOSES]
    parent_run_id: str = Field(min_length=1)
    epoch_id: str = Field(min_length=1)
    session_run_id: str = Field(min_length=1)
    business_sha256: str = Field(pattern=r'^[0-9a-f]{64}$')
    workpack_sha256: str = Field(pattern=r'^[0-9a-f]{64}$')
    progress_sha256: str = Field(pattern=r'^[0-9a-f]{64}$')
    blocker_count: int = Field(ge=0)
    started_at: int = Field(ge=0)
    finished_at: int | None = Field(default=None, ge=0)
    exit_code: int | None = None
    outcome: Literal['started', 'ok', 'failed'] = 'started'
    attempts: list[Stage2ReviewAttempt] = Field(default_factory=list)
    rejections: list[Stage2ReviewRejection] = Field(default_factory=list)

    @model_validator(mode='after')
    def _finish_fields_agree(self) -> Stage2ReviewLedgerTurn:
        if self.outcome == 'started':
            if self.finished_at is not None or self.exit_code is not None:
                raise ValueError('started review turn 不得携带 finished/exit')
        elif self.finished_at is None or self.exit_code is None:
            raise ValueError('已结束 review turn 必须携带 finished_at/exit_code')
        if (self.purpose != 'final_review'
                and self.ordinal > STAGE2_COMMANDER_MAX_EDIT_SEAT):
            raise ValueError(
                f'第 {self.ordinal} 席只保留给 final_review，'
                f'不能承担 {self.purpose}')
        if self.purpose == 'final_review' and self.blocker_count != 0:
            raise ValueError('final_review 席位只能在未闭合项为 0 时预留')
        ids = [row.attempt_id for row in self.attempts]
        if len(ids) != len(set(ids)):
            raise ValueError('同一席位内 attempt_id 不得重复')
        if self.attempts and self.attempts[0].kind != 'launch':
            raise ValueError('席位的首个 attempt 必须是 launch')
        if self.rejections and (self.outcome != 'ok' or self.exit_code != 0):
            raise ValueError('Stage2 typed/canonical 拒因只能绑定成功结束的 Commander 席位')
        rejected_requests = [row.request_sha256 for row in self.rejections]
        if len(rejected_requests) != len(set(rejected_requests)):
            raise ValueError('同一席位的 Stage2 被拒请求 identity 不得重复')
        return self


class Stage2ReviewLedgerReconciliation(FrozenModel):
    """一次经人工 CAS 确认的 0-wire 假预留隔离记录。

    ``removed_turn`` 保留被移出有效预算面的原始行；这样恢复不会把历史异常
    擦掉，也不会靠直接删 JSON 冒充它从未发生。当前只接受已知的旧版只读预审
    admission/epoch 校验事故，其他失败仍按“started 也消耗预算”关闭式处理。
    """

    kind: Literal['stage2-review-no-wire-reconciliation-v1'] = (
        'stage2-review-no-wire-reconciliation-v1')
    removed_turn: Stage2ReviewLedgerTurn
    actor: str = Field(min_length=1, max_length=256)
    reason: str = Field(min_length=1, max_length=2048)
    subject_sha256: str = Field(pattern=r'^[0-9a-f]{64}$')
    original_ledger_sha256: str = Field(pattern=r'^[0-9a-f]{64}$')
    run_log_sha256: str = Field(pattern=r'^[0-9a-f]{64}$')
    agent_sessions_sha256: str = Field(pattern=r'^[0-9a-f]{64}$')
    cost_ledger_sha256: str = Field(pattern=r'^[0-9a-f]{64}$')
    parent_ended_at: int = Field(ge=0)
    parent_exit_code: int
    reconciled_at: int = Field(ge=0)

    @model_validator(mode='after')
    def _only_the_known_no_wire_failure(self) -> Stage2ReviewLedgerReconciliation:
        turn = self.removed_turn
        if (turn.purpose != 'prereview' or turn.outcome != 'failed'
                or turn.exit_code != 70):
            raise ValueError('只允许隔离 exit 70 的失败 Stage2 prereview 假预留')
        return self


class ReviewerSeatTurn(FrozenModel):
    """非 Stage2 reviewer 席位内的一轮 Commander turn（首次审阅或纠错）。"""

    turn_id: str = Field(min_length=1)
    ordinal: int = Field(ge=1)
    purpose: Literal['review', 'correction']
    session_run_id: str = Field(min_length=1)
    parent_run_id: str = Field(min_length=1)
    epoch_id: str = Field(min_length=1)
    started_at: int = Field(ge=0)
    finished_at: int | None = Field(default=None, ge=0)
    exit_code: int | None = None
    outcome: Literal['started', 'ok', 'failed'] = 'started'
    # structured Reviewer 的 provider 回答在 full action 或 typed 拒因
    # durable 前先把 canonical JSON 摘要绑到原 seat turn。它关闭
    # ``record 自己携 digest 又自己证明 digest`` 的循环，也让
    # record-save -> seat-finish 硬崩可以由独立席位事实恢复。
    provider_response_sha256: str | None = Field(
        default=None, pattern=r'^[0-9a-f]{64}$')
    attempts: list[Stage2ReviewAttempt] = Field(default_factory=list)

    @model_validator(mode='after')
    def _finish_fields_agree(self) -> ReviewerSeatTurn:
        if self.outcome == 'started':
            if self.finished_at is not None or self.exit_code is not None:
                raise ValueError('started reviewer seat turn 不得携带 finished/exit')
        elif self.finished_at is None or self.exit_code is None:
            raise ValueError('已结束 reviewer seat turn 必须携带 finished_at/exit_code')
        ids = [row.attempt_id for row in self.attempts]
        if len(ids) != len(set(ids)):
            raise ValueError('同一轮内 attempt_id 不得重复')
        return self


class ReviewerSeatPendingCorrection(FrozenModel):
    """一席已付费审阅留下、但尚未闭合的 typed 纠错事实。"""

    source_turn_id: str = Field(min_length=1)
    session_run_id: str = Field(min_length=1)
    provider_session_id: str = Field(min_length=1)
    request_sha256: str = Field(pattern=r'^[0-9a-f]{64}$')
    reason: str = Field(min_length=1, max_length=2048)
    recorded_at: int = Field(ge=0)


class ReviewerSeatLedger(FrozenModel):
    """按 ``(sid, blocker, business_sha256)`` 持久化的非 Stage2 reviewer 席位账。

    同一业务主体上：首轮必须是 ``review``，其后最多 ``max_corrections`` 轮
    ``correction``；所有轮共用同一席（同一 logical/provider session），账跨
    重启持久、纠错不返还。业务主体（surface）变化即新账新席。
    """

    schema_version: Literal[1] = 1
    kind: Literal['novel-reviewer-seat-ledger'] = 'novel-reviewer-seat-ledger'
    sid: str = Field(min_length=1)
    waiting_on: str = Field(min_length=1)
    business_sha256: str = Field(pattern=r'^[0-9a-f]{64}$')
    max_corrections: int = Field(
        default=COMMANDER_REVIEWER_MAX_CORRECTIONS, ge=0)
    turns: list[ReviewerSeatTurn] = Field(default_factory=list)
    pending_correction: ReviewerSeatPendingCorrection | None = None

    @model_validator(mode='after')
    def _turns_are_consistent(self) -> ReviewerSeatLedger:
        if [row.ordinal for row in self.turns] != list(
                range(1, len(self.turns) + 1)):
            raise ValueError('reviewer seat ledger ordinal 必须从 1 连续递增')
        ids = [row.turn_id for row in self.turns]
        if len(ids) != len(set(ids)):
            raise ValueError('reviewer seat ledger turn_id 不得重复')
        reviews = [row for row in self.turns if row.purpose == 'review']
        if self.turns and (
                self.turns[0].purpose != 'review' or len(reviews) != 1
                or any(row.purpose != 'correction'
                       for row in self.turns[1:])):
            raise ValueError('reviewer seat ledger 只能有一轮首审，其后只能纠错')
        sessions = {row.session_run_id for row in self.turns}
        if len(sessions) > 1:
            raise ValueError('同一 reviewer 席位的所有轮必须共用 logical session')
        corrections = sum(1 for row in self.turns if row.purpose == 'correction')
        if corrections > self.max_corrections:
            raise ValueError('reviewer seat ledger 纠错轮数超出 max_corrections')
        pending = self.pending_correction
        if pending is not None:
            matches = [
                row for row in self.turns
                if row.turn_id == pending.source_turn_id]
            if len(matches) != 1 or matches[0].outcome != 'ok':
                raise ValueError('pending correction 必须绑定唯一的成功 reviewer turn')
            if matches[0].session_run_id != pending.session_run_id:
                raise ValueError('pending correction 与 reviewer 席位 session 不一致')
        return self


class Stage2ReviewLedgerArchive(FrozenModel):
    """一份经人工 typed 决定整体归档的旧席位账（含 v1 三席账）。

    归档不是删除：旧 turns / reconciliations 逐字保留在这里，新账从空席位重新
    计数。只有 parent AgentRun 已终止、席位账无 ``started`` 行时才允许归档；
    它是两本被旧三席契约卡死的书唯一合规的续跑路，也是未来席位契约再升版时
    的固定入口。
    """

    kind: Literal['stage2-review-ledger-archive-v1'] = (
        'stage2-review-ledger-archive-v1')
    actor: str = Field(min_length=1, max_length=256)
    reason: str = Field(min_length=1, max_length=2048)
    archived_at: int = Field(ge=0)
    original_schema_version: int = Field(ge=1)
    original_max_calls: int = Field(ge=1)
    original_state: Literal['active', 'exhausted', 'complete']
    original_sha256: str = Field(pattern=r'^[0-9a-f]{64}$')
    turns: list[Stage2ReviewLedgerTurn] = Field(default_factory=list)
    reconciliations: list[Stage2ReviewLedgerReconciliation] = Field(
        default_factory=list)


class Stage2ReviewLedger(FrozenModel):
    """按 Global + MainText identity 持久化的 Commander 席位与进展账（v2）。

    v1 是三席平权账；v2 把席位分成 review/correction 与只读 ``final_review``，
    第四席只能是终审，并在每席下记录同 session 的进程 attempts。本仓没有
    通用迁移机制：v1 账只能经带 actor/reason 的显式归档进入 ``archived_ledgers``。
    """

    schema_version: Literal[2] = 2
    kind: Literal['stage2-global-review-ledger'] = 'stage2-global-review-ledger'
    sid: str = Field(min_length=1)
    source_sha256: str = Field(pattern=r'^[0-9a-f]{64}$')
    main_text_sha256: str = Field(pattern=r'^[0-9a-f]{64}$')
    max_calls: Literal[STAGE2_COMMANDER_MAX_SEATS] = STAGE2_COMMANDER_MAX_SEATS
    state: Literal['active', 'exhausted', 'complete'] = 'active'
    turns: list[Stage2ReviewLedgerTurn] = Field(default_factory=list)
    reconciliations: list[Stage2ReviewLedgerReconciliation] = Field(
        default_factory=list)
    archived_ledgers: list[Stage2ReviewLedgerArchive] = Field(
        default_factory=list)

    @model_validator(mode='after')
    def _turns_are_monotonic(self) -> Stage2ReviewLedger:
        if [row.ordinal for row in self.turns] != list(
                range(1, len(self.turns) + 1)):
            raise ValueError('Stage2 review ledger ordinal 必须从 1 连续递增')
        if len(self.turns) > self.max_calls:
            raise ValueError('Stage2 review ledger 超出 max_calls')
        sessions = [row.session_run_id for row in self.turns]
        if len(sessions) != len(set(sessions)):
            raise ValueError('每席 Stage2 Commander 必须使用新的 logical session')
        progress = [row.progress_sha256 for row in self.turns]
        if len(progress) != len(set(progress)):
            raise ValueError('Stage2 review ledger 不得重复无进展 fingerprint')
        reconciled_sessions = [
            row.removed_turn.session_run_id for row in self.reconciliations]
        if len(reconciled_sessions) != len(set(reconciled_sessions)):
            raise ValueError('Stage2 review ledger 不得重复隔离同一 session')
        if set(sessions) & set(reconciled_sessions):
            raise ValueError('有效 turn 与已隔离 turn 的 session 不得重叠')
        if self.state == 'exhausted' and len(self.turns) != self.max_calls:
            raise ValueError('未用满席位不得标 exhausted')
        if self.state == 'active' and len(self.turns) >= self.max_calls:
            raise ValueError('已用满席位必须标 exhausted 或 complete')
        if (self.state == 'complete'
                and (not self.turns or any(
                    row.outcome != 'ok' or row.exit_code != 0
                    for row in self.turns))):
            raise ValueError(
                'Stage2 review ledger 只有全部席位 ok/exit0 才能标 complete')
        return self


class Stage2PrereviewRecord(FrozenModel):
    """已通过预审 typed loader、尚未绑定 live surface 的 Stage2 决定。

    这不是 ``ReviewerActionRecord``：Blueprint 尚未完成时没有 live submission
    option、execution permit 或 canonical action。记录只保存重新绑定所需的完整
    业务身份、原 logical session 与 provider 原始回答摘要；provider session 不属于
    恢复身份，也绝不据此续会。
    """

    kind: Literal['novel-stage2-prereview-record-v1'] = (
        'novel-stage2-prereview-record-v1')
    sid: str = Field(min_length=1)
    run_id: str = Field(min_length=1)
    epoch_id: str = Field(min_length=1)
    business_sha256: str = Field(pattern=r'^[0-9a-f]{64}$')
    workpack_sha256: str = Field(pattern=r'^[0-9a-f]{64}$')
    source_sha256: str = Field(pattern=r'^[0-9a-f]{64}$')
    main_text_sha256: str = Field(pattern=r'^[0-9a-f]{64}$')
    progress_sha256: str = Field(pattern=r'^[0-9a-f]{64}$')
    blocker_count: int = Field(ge=1)
    reviewer_session_run_id: str = Field(min_length=1)
    provider_response_sha256: str = Field(pattern=r'^[0-9a-f]{64}$')
    input_payload: dict[str, Any]
    recorded_at: int = Field(ge=0)


class ReviewerProtocolRecoveryProof(FrozenModel):
    """已结束 Commander 的已知协议误拒经零模型复验后留下的来源证明。"""

    policy: Literal['codex-recovered-transport-notice-v1'] = (
        'codex-recovered-transport-notice-v1')
    session_run_id: str = Field(min_length=1)
    turn_id: str = Field(min_length=1)
    attempt_id: str = Field(min_length=1)
    provider_session_id: str = Field(min_length=1)
    parent_sha256: str = Field(pattern=r'^[0-9a-f]{64}$')
    seat_sha256: str = Field(pattern=r'^[0-9a-f]{64}$')
    session_sha256: str = Field(pattern=r'^[0-9a-f]{64}$')
    cost_sha256: str = Field(pattern=r'^[0-9a-f]{64}$')
    log_sha256: str = Field(pattern=r'^[0-9a-f]{64}$')
    stdout_sha256: str = Field(pattern=r'^[0-9a-f]{64}$')
    raw_response_sha256: str = Field(pattern=r'^[0-9a-f]{64}$')
    response_sha256: str = Field(pattern=r'^[0-9a-f]{64}$')
    validation_code_version: str = Field(pattern=r'^[0-9a-f]{40}$')


class ReviewerActionRecord(FrozenModel):
    """Commander 已通过 typed loader 的可重放决定，按内容寻址落盘。

    它不是审批账，也不是执行授权：审批仍只由 ``novel.cli approval apply`` 子进程
    在 admission 内写入。这里只保证一次已经完成的模型审阅，在 epoch 因代码/skill
    漂移关闭、进程崩溃或服务重启后，能在同一 blocker 上零模型成本重放，而不是再
    买一轮 Commander。普通决定的适用性由 ``waiting_on`` 加三条判据之一决定：
    ``surface_seal``、``review_token`` 或稳定 business identity 相同；Stage2 则必须
    ``waiting_on + business_sha256 + workpack_sha256`` 同时命中。Stage0/6/7 的
    approve 与 Stage2 Global
    reviewed edit/review-decisions 虽编码为 node 动作，也都先记在这里；
    Stage2 的书内 typed 产物只有 canonical action landed 后才存在，不能覆盖
    typed-valid 回答到 action 启动之间的暂停、终止或进程丢失窗口。
    """

    kind: Literal['novel-reviewer-action-record-v1'] = (
        REVIEWER_ACTION_RECORD_KIND)
    sid: str = Field(min_length=1)
    run_id: str = Field(min_length=1)
    epoch_id: str = Field(min_length=1)
    waiting_on: str = Field(min_length=1)
    surface_seal: str = Field(min_length=1)
    business_sha256: str = Field(pattern=r'^[0-9a-f]{64}$')
    # 历史 approval façade 直接保存 surface/payload；Stage0/6/7 的
    # approve 在 reviewer contract 中编码为 node action，所以统一以
    # node/flags/input_payload 保存。Stage2 另带产出席位、workpack 与 provider
    # 原始回答 digest：full action 可以安全重放，但 compact wire 的 no-progress /
    # canonical 拒因仍必须绑定 provider 原始回答，不能改绑 assembled digest。
    surface: Literal[REVIEWER_APPROVAL_SURFACES] | None = None
    payload: dict[str, Any] | None = None
    node: str | None = Field(default=None, min_length=1)
    flags: tuple[str, ...] | None = None
    input_payload: dict[str, Any] | None = None
    review_token: str | None = None
    protocol_recovery: ReviewerProtocolRecoveryProof | None = None
    # Stage5 trim disposition 额外保存原 Review 与 reviewer contract 摘要；再结合
    # payload 的完整有序 trim 双 SHA，可先自证 durable business 未损坏，
    # 再区分“current 主体已换可安全退休”和“同主体 partial
    # frontier 冲突须保留停止”。closure token 不是 trim 人审主体。
    stage5_review_sha256: str | None = Field(
        default=None, pattern=r'^[0-9a-f]{64}$')
    stage5_reviewer_contract_sha256: str | None = Field(
        default=None, pattern=r'^[0-9a-f]{64}$')
    workpack_sha256: str | None = Field(
        default=None, pattern=r'^[0-9a-f]{64}$')
    source_sha256: str | None = Field(
        default=None, pattern=r'^[0-9a-f]{64}$')
    main_text_sha256: str | None = Field(
        default=None, pattern=r'^[0-9a-f]{64}$')
    # correction 预留会把确定性拒因并入 progress；它不一定等于当前 task 的
    # base progress。记录必须保存实际 reserve 使用的 effective progress。
    progress_sha256: str | None = Field(
        default=None, pattern=r'^[0-9a-f]{64}$')
    blocker_count: int | None = Field(default=None, ge=0)
    reviewer_session_run_id: str | None = Field(default=None, min_length=1)
    # 非 Stage2 durable-first 回放用它定位同一 logical session
    # 内的原 reviewer turn；correction 共用 session，只靠 session id
    # 无法证明是哪一轮。
    reviewer_seat_turn_id: str | None = Field(default=None, min_length=1)
    provider_response_sha256: str | None = Field(
        default=None, pattern=r'^[0-9a-f]{64}$')
    recorded_at: int = Field(ge=0)

    @model_validator(mode='after')
    def _one_replayable_action_shape(self) -> ReviewerActionRecord:
        if self.protocol_recovery is not None and (
                self.node is not None or self.surface is None
                or self.surface == 'stage2_final_review' or not self.review_token):
            raise ValueError('协议误拒恢复只接受绑定 review token 的普通 approval')
        approval = self.surface is not None
        node = self.node is not None
        if approval == node:
            raise ValueError('reviewer action record 必须恰含 approval 或 node 形状')
        if approval:
            if (self.payload is None or self.flags is not None
                    or self.input_payload is not None):
                raise ValueError('approval reviewer record 字段组合不成立')
        else:
            if self.payload is not None or self.flags is None:
                raise ValueError('node reviewer record 字段组合不成立')
            if any(not isinstance(flag, str) or not flag for flag in self.flags):
                raise ValueError('node reviewer record flags 必须是非空字符串')
        is_stage2_node = node and self.node == 'stage2.global'
        is_stage2_final = approval and self.surface == 'stage2_final_review'
        is_stage2 = bool(is_stage2_node or is_stage2_final)
        is_stage5_disposition = bool(
            node
            and self.node == 'stage5.review'
            and self.flags in {
                ('--retain-pretrim-batch', '@reviewer-input'),
                ('--request-reviewer-rewrite', '@reviewer-input'),
            })
        stage2_legacy_identity = (
            self.workpack_sha256, self.reviewer_session_run_id,
            self.provider_response_sha256)
        stage2_extended_identity = (
            self.source_sha256,
            self.main_text_sha256,
            self.progress_sha256,
            self.blocker_count,
        )
        if (any(value is not None for value in (
                self.workpack_sha256, *stage2_extended_identity))
                or is_stage2 and any(value is not None for value in (
                    self.reviewer_session_run_id,
                    self.provider_response_sha256))):
            if not all(value is not None for value in stage2_legacy_identity):
                raise ValueError('Stage2 reviewer record 恢复身份必须成组出现')
            # v1 早期记录只有 legacy 三元组；新增 effective progress 后仍须可读，
            # 但新 writer 一律写完整扩展组。扩展字段不允许半组出现。
            if (any(value is not None for value in stage2_extended_identity)
                    and not all(
                        value is not None for value in stage2_extended_identity)):
                raise ValueError('Stage2 reviewer record 扩展恢复身份必须成组出现')
            if not is_stage2:
                raise ValueError(
                    'Stage2 reviewer record 恢复身份只属于 stage2.global node '
                    '或 stage2_final_review approval')
            if self.reviewer_seat_turn_id is not None:
                raise ValueError('Stage2 reviewer record 不使用通用 seat turn id')
        elif is_stage5_disposition:
            stage5_identity = (
                self.stage5_review_sha256,
                self.stage5_reviewer_contract_sha256,
                self.reviewer_session_run_id,
                self.reviewer_seat_turn_id,
                self.provider_response_sha256,
            )
            if not all(value is not None for value in stage5_identity):
                raise ValueError(
                    'Stage5 trim disposition durable record 的 Review/contract/session/'
                    'turn/provider 身份必须成组出现')
            if self.review_token is not None:
                raise ValueError('Stage5 trim durable record 不得绑定 closure token')
        elif any(value is not None for value in (
                self.stage5_review_sha256,
                self.stage5_reviewer_contract_sha256,
                self.reviewer_session_run_id,
                self.reviewer_seat_turn_id,
                self.provider_response_sha256,
        )):
            raise ValueError('ReviewerActionRecord 持久恢复身份与动作形状不一致')
        return self


def reviewer_rewrite_authority_receipt_sha256(
        payload: Mapping[str, object],
) -> str:
    """Stage5 Reviewer rewrite 永久 authority receipt 的唯一自摘要。"""
    canonical = {
        key: value for key, value in dict(payload).items()
        if key != 'receipt_sha256'
    }
    try:
        raw = json.dumps(
            canonical, ensure_ascii=False, sort_keys=True,
            separators=(',', ':'), allow_nan=False,
        ).encode('utf-8')
    except (TypeError, ValueError) as exc:
        raise ValueError('reviewer rewrite authority receipt 不能 canonicalize') from exc
    return hashlib.sha256(raw).hexdigest()


class ReviewerRewriteAuthorityReceipt(FrozenModel):
    """canonical rewrite request 已消费一份 Commander action 的永久封印。

    active ``ReviewerActionRecord`` 仍是崩溃恢复凭据；只有 request typed
    readback、原席位/session/attempt/cost/response 全闭合后，Agent 才能把原字节
    复制到 authority archive 并写本 receipt。后续 queue/permit/producer 只读这份
    seal，不借 live outer owner 身份，也不补写任何账。
    """

    kind: Literal['novel-stage5-reviewer-rewrite-authority-v1'] = (
        'novel-stage5-reviewer-rewrite-authority-v1')
    sid: str = Field(min_length=1)
    reviewer_action_record_sha256: str = Field(pattern=r'^[0-9a-f]{64}$')
    request_rel: str = Field(min_length=1)
    request_file_sha256: str = Field(pattern=r'^[0-9a-f]{64}$')
    request_sha256: str = Field(pattern=r'^[0-9a-f]{64}$')
    business_sha256: str = Field(pattern=r'^[0-9a-f]{64}$')
    reviewer_contract_sha256: str = Field(pattern=r'^[0-9a-f]{64}$')
    reviewer_session_run_id: str = Field(min_length=1)
    reviewer_seat_turn_id: str = Field(min_length=1)
    provider_response_sha256: str = Field(pattern=r'^[0-9a-f]{64}$')
    seat_evidence_sha256: str = Field(pattern=r'^[0-9a-f]{64}$')
    session_evidence_sha256: str = Field(pattern=r'^[0-9a-f]{64}$')
    cost_evidence_sha256: str = Field(pattern=r'^[0-9a-f]{64}$')
    sealed_at: int = Field(ge=0)
    receipt_sha256: str = Field(pattern=r'^[0-9a-f]{64}$')

    @model_validator(mode='after')
    def _self_hash_is_closed(self) -> 'ReviewerRewriteAuthorityReceipt':
        if self.receipt_sha256 != reviewer_rewrite_authority_receipt_sha256(
                self.model_dump(mode='json')):
            raise ValueError('reviewer rewrite authority receipt SHA 不闭合')
        return self


class AgentDeliveryOutcome(FrozenModel):
    """外层进程结束后，服务端从当前盘面重新得出的交付结论。"""

    state: AgentDeliveryState
    reason: str = Field(min_length=1)
    checked_at: int = Field(ge=0)


AgentRunEventKind = Literal[AGENT_RUN_EVENT_KINDS]


class AgentRunEvent(FrozenModel):
    """agent 代跑记录的一行；同一文件兼容读取 v1 / v2。

    ⚠ **为什么不并进 `jobs.jsonl`**：`JobStart` 的后置条件钉死 `node ∈ NODES`，
    而 agent 代跑不是流水线节点——它是「拉起一个会自己跑节点的进程」。
    捏进去就要往 `NODES` 里塞一个假节点，而那张表是完成度/贵贱/写盘效应的
    全部派生源头，塞假节点的代价是全链说假话。
    """

    wire_version: Literal[1, 2] = 1
    run_id: str = Field(min_length=1)
    kind: AgentRunEventKind | AgentRunEventV2Kind
    ts: int = Field(ge=0)

    #: v1 ``started`` 的权威载荷；v2 ``started`` 也携一份不参加 parent hash 的
    #: 首 epoch 审计/旧 UI 投影，其余事件必须为 ``None``。
    start: AgentRunStart | None = None

    #: v2 ``started`` 唯一载荷；整书不变事实不再和每个 epoch 的代码身份混写。
    contract: RunContractV2 | None = None

    #: v2 ``epoch_opened`` 唯一身份载荷。
    epoch: ExecutionEpoch | None = None

    #: v2 ``epoch_closed`` 指向被关闭的 epoch。
    epoch_id: str | None = Field(
        default=None, pattern=r'^[a-z0-9][a-z0-9_-]{0,127}$')

    #: epoch 关闭后的 execution admission 结论；unknown 绝不冒充已 drain。
    drain_state: ExecutionEpochDrainState | None = None

    #: 该 epoch 最后一次成功观测到的供应商 session id，供下一 turn 精确续会。
    provider_session_id: str | None = Field(default=None, min_length=1)

    #: `kind == 'ended'`：agent 进程的退出码。
    exit_code: int | None = None

    #: `kind == 'reconciled'`：为什么改判（服务重启那一族）。
    note: str | None = None

    #: 新版 `ended` 事件随退出码一起带；旧事件缺失时折叠为 outcome=unknown。
    outcome: AgentDeliveryOutcome | None = None

    @model_validator(mode='after')
    def _payload_matches_the_kind(self) -> AgentRunEvent:
        """一种事件只携带自己的载荷；v1 旧行仍逐字兼容。"""
        if self.wire_version == 1:
            if self.kind not in AGENT_RUN_EVENT_KINDS:
                raise ValueError(f'wire v1 不认识 kind={self.kind!r}')
            want = {'started': 'start', 'ended': 'exit_code',
                    'reconciled': 'note'}[self.kind]
            carried = {k for k in ('start', 'exit_code', 'note')
                       if getattr(self, k) is not None}
            if carried != {want}:
                raise ValueError(
                    f'{self.kind} 事件该带且只该带 {want!r}，实得 {sorted(carried)}')
            if any(value is not None for value in (
                    self.contract, self.epoch, self.epoch_id,
                    self.drain_state, self.provider_session_id)):
                raise ValueError('wire v1 不得携带 v2 contract/epoch 载荷')
            if self.outcome is not None and self.kind != 'ended':
                raise ValueError('只有 ended 事件可以带 outcome')
            return self

        if self.kind not in AGENT_RUN_EVENT_V2_KINDS:
            raise ValueError(f'wire v2 不认识 kind={self.kind!r}')
        if self.kind == 'started':
            if self.contract is None:
                raise ValueError('wire v2 started 必须携带 RunContractV2')
        elif self.contract is not None or self.start is not None:
            raise ValueError('只有 wire v2 started 可以携带 contract/start 审计投影')
        if self.kind == 'epoch_opened':
            if self.epoch is None:
                raise ValueError('epoch_opened 必须携带 ExecutionEpoch')
        elif self.epoch is not None:
            raise ValueError('只有 epoch_opened 可以携带 epoch')
        if self.kind == 'epoch_closed':
            if self.epoch_id is None or self.exit_code is None \
                    or self.drain_state is None:
                raise ValueError(
                    'epoch_closed 必须携带 epoch_id/exit_code/drain_state')
        elif any(value is not None for value in (
                self.epoch_id, self.drain_state, self.provider_session_id)):
            raise ValueError('只有 epoch_closed 可以携带 epoch 关闭载荷')
        if self.kind in ('paused', 'resumed', 'reconciled'):
            if self.note is None:
                raise ValueError(f'{self.kind} 必须携带原因 note')
        elif self.note is not None:
            raise ValueError(f'{self.kind} 不得携带 note')
        if self.kind == 'ended':
            if self.exit_code is None or self.outcome is None:
                raise ValueError('wire v2 ended 必须携带 exit_code 与 outcome')
        elif self.kind != 'epoch_closed' and self.exit_code is not None:
            raise ValueError(f'{self.kind} 不得携带 exit_code')
        if self.kind != 'ended' and self.outcome is not None:
            raise ValueError('只有 ended 事件可以带 outcome')
        return self


class AgentRunRecord(StrictModel):
    """把同一个 `run_id` 的事件按顺序折叠出来的**视图**。盘上不存在这样一份文件。"""

    run_id: str = Field(min_length=1)
    start: AgentRunStart

    #: 新运行携带 v2 immutable parent contract；旧账为 ``None``。
    contract: RunContractV2 | None = None

    #: 已打开过的 execution epoch，按 ordinal 排列；这里只存身份，不伪造关闭结论。
    epochs: list[ExecutionEpoch] = Field(default_factory=list)

    #: 当前仍开放的 epoch；没有时显式为 ``None``。
    active_epoch_id: str | None = Field(
        default=None, pattern=r'^[a-z0-9][a-z0-9_-]{0,127}$')

    started: int = Field(ge=0)

    #: 必填但可为 `null`——`null` = 还没结束、或不知道，不是 0。
    ended: int | None
    exit_code: int | None

    #: 整书外层进程状态。**`unknown` 是真状态，不是缺省值**（`R-11.11` 同构）：
    #: 服务重启后，子进程是死是活我们真的不知道。
    state: AgentRunState

    #: `reconciled` 那一条的原因。`None` = 键不在。
    note: str | None = None

    #: 与 `state` 正交：前者只讲进程，后者讲盘面是否真正可交付。
    outcome: AgentDeliveryOutcome | None = None

    #: 控制能力由服务端按 wire/liveness 投影；前端不得从相似的状态名猜。
    allowed_controls: list[AgentRunControl] = Field(default_factory=list)

    @model_validator(mode='after')
    def _state_matches_the_events(self) -> AgentRunRecord:
        """折叠规则只认 ended 事实与服务端终验，不让前端补推。"""
        if self.exit_code is None:
            if self.state in ('ok', 'landed_red', 'stopped', 'failed'):
                raise ValueError(
                    f'state={self.state!r} 而没有 exit_code——'
                    f'「跑完了」这个结论只能从退出码来')
        else:
            want = ('ok' if self.exit_code == 0 else
                    'landed_red' if self.exit_code == 1 else
                    'stopped' if self.exit_code == 2
                    and self.outcome is not None
                    and self.outcome.state == 'stopped' else 'failed')
            if self.state != want:
                raise ValueError(
                    f'exit_code={self.exit_code}, outcome='
                    f'{self.outcome.state if self.outcome else None!r} '
                    f'应折叠成 {want!r}，实得 {self.state!r}')
        if self.state == 'unknown' and not self.note:
            raise ValueError('state=unknown 必须说出原因（R-11.11）')
        if self.state == 'running' and self.contract is not None \
                and self.active_epoch_id is None:
            raise ValueError('wire v2 running 必须指向 active_epoch_id')
        if self.state == 'paused' and self.exit_code is not None:
            raise ValueError('paused 是非终态，不得携带 exit_code')
        if self.exit_code is not None and self.active_epoch_id is not None:
            raise ValueError('终态 AgentRun 不得仍指向 active execution epoch')
        if self.active_epoch_id is not None and not any(
                epoch.epoch_id == self.active_epoch_id for epoch in self.epochs):
            raise ValueError('active_epoch_id 必须来自 epochs')
        if self.outcome is not None and self.exit_code is None:
            raise ValueError('没有 ended/exit_code 时不得声称已有交付终验')
        if self.contract is None:
            expected_controls = ['stop'] if self.state == 'running' else []
        elif self.state in ('pending', 'running'):
            expected_controls = ['pause', 'stop']
        elif self.state == 'paused':
            expected_controls = ['resume', 'stop']
        else:
            expected_controls = []
        if self.allowed_controls != expected_controls:
            raise ValueError(
                'allowed_controls 必须由 wire 与当前状态唯一投影：'
                f'expected={expected_controls!r}, got={self.allowed_controls!r}')
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

    #: 活动 `.INVALID` 自己的来源状态；与 formal 的 ``freshness/reason`` 分开，
    #: 避免 formal+INVALID 共存时把两份载体压成一行混主体事实。
    #: 两格在公共响应中始终存在；``invalid=False`` 时必须同时为 ``null``，
    #: ``invalid=True`` 时必须同时给出，不能让恢复分类悄悄丢 marker。
    invalid_freshness: Freshness | None
    invalid_reason: FreshnessReason | None

    #: 能不能按**当前** schema 加载。
    readable: bool = True

    #: 当前 schema 明确拒读、但由专用只读适配器证明仍精确绑定既有人审主体。
    #: 这不是迁移或 ``readable=True`` 的别名；正常 producer 仍必须拒绝该载体。
    legacy_frozen: bool = False
    legacy_contract: str | None = Field(default=None, min_length=1)

    #: 缺席/可用/隔离/读不回。界面只按这个 id 选文案，不再组合 exists/invalid/readable。
    condition: ArtifactCondition

    @model_validator(mode='after')
    def _absent_artifacts_carry_nothing(self) -> ArtifactState:
        if self.legacy_frozen != (self.legacy_contract is not None):
            raise ValueError(
                f'{self.rel}: legacy_frozen/legacy_contract 必须同时出现或同时缺席')
        if self.legacy_frozen and (not self.exists or self.readable):
            raise ValueError(
                f'{self.rel}: frozen legacy 必须在盘且仍不可按 current schema 读取')
        if ((self.invalid_freshness is None)
                != (self.invalid_reason is None)):
            raise ValueError(
                f'{self.rel}: invalid_freshness/invalid_reason 必须成对出现')
        if self.invalid and self.invalid_freshness is None:
            raise ValueError(
                f'{self.rel}: invalid=True 必须携带 INVALID 来源状态')
        if not self.invalid and self.invalid_freshness is not None:
            raise ValueError(
                f'{self.rel}: invalid=False 却携带 INVALID 来源状态')
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
        want = all(
            a.exists and (a.readable or a.legacy_frozen)
            and a.freshness != 'stale' and not a.invalid
            for a in self.artifacts
        )
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
