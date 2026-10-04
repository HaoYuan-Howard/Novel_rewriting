#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
api.py —— 对应 `docs/schema/_api.md`。**一次 HTTP 响应长什么样。**

【它与另外三份非产物契约的分界线】
`common.py` 管每份产物里都有的那几个键；`report.py` 管一次校验的输出；
`orchestration.py` 管一次跑 / 一批跑 / 这本书跑到哪了；
**本模块管的是「界面收到的那一份」**——它是前三者的**视图**，一个新事实都不产生。

【本模块的两条自有纪律，各配一条测试】

  ① **只组合，不手抄取值面。** 这里不许出现 `Literal['fresh', 'stale', …]` 这种
     字面量取值面——取值面的唯一落点是 `contracts/invariants.py`。
     一份响应模型抄一份取值面，就是那份取值面的第 N 个落点，而它们必然分家。
  ② **一个字段都不许自己算判定**（`R-11.4`）。`badge` / `passed` / `counts`
     只出现在 `CheckReport` 里。视图给的是「两份数字并排放着让人自己看」
     （`R-11` 节首语的原话），判定给的是「合不合格」——**混在一格里就是第二个判官**。

⚠ 本模块**不 import fastapi**。它住在 `contracts/` 正是为了这件事：
离线产前端 TS 类型的脚本要 import 它，而那个脚本一行 HTTP 都不发，
不该为此背上整个 web 栈（`tests/test_layering.py::test_contracts_never_imports_the_web_stack`）。

⚠ `/api/enums` **刻意没有响应模型**，理由见 `_api.md` §二：它的键集就是它的内容，
写成一个五十来个字段的模型等于把「自动发布」变回一份手写清单。**别来补齐它。**
"""
from __future__ import annotations

from contracts.schema.workflow import BudgetGrant
from contracts.schema.orchestration import RunContinuationSource, WorkUnitView, PipelineConcurrency

import json
import re
from collections.abc import Mapping
from typing import Annotated, Any, Literal, TypeAlias
from urllib.parse import urlsplit

from pydantic import Field, field_validator, model_serializer, model_validator

from contracts.invariants import (
    LLM_EXECUTOR_NODES,
    PIPELINE_PARALLEL_NODES, PIPELINE_DEFAULT_CONCURRENCY,
    PIPELINE_TOTAL_CONCURRENCY_MIN, PIPELINE_TOTAL_CONCURRENCY_MAX,
    AgentProfileSettingsState,
    NetworkEgressMode,
    CHAPTER_LABEL_ANOMALY,
    INBOX_ATTENTION,
    INBOX_DELIVERY_EFFECT,
    INBOX_KIND,
    NODES,
    EXECUTION_PERMIT_KINDS,
    OPERATOR_NEXT_STATES,
    RESUME_VERDICTS,
    NONBODY_KINDS,
    REMOVED_ORIGIN,
    RETENTION_CANDIDATE_KINDS,
    STAGE0_SEGMENT_KINDS,
    STAGE1_SEGMENT_KINDS,
    UPLOAD_AUTOSTART_OUTCOMES,
)

from contracts.schema.base import FrozenModel, StrictModel
from contracts.schema.common import Sha256
from contracts.schema.orchestration import (
    AgentRunRecord, ArtifactCondition,
    ArtifactState, ExecutorBackend, Freshness, FreshnessReason, JobRecord,
    JobState, NovelOverview, PipelineAgentProfiles, QueueRun,
)
from contracts.schema.stage0 import Stage0RevisionOrigin, Stage0Warning
# ⚠ F8a：`contracts.schema.stage6` 的 import 随 Stage6 整层退役删除
#   （HookApprovalResolution／HookProvenance／HookSources／Mech；F6d 登记）。
# ⚠ 2026-10-04 清理批（用户裁决：不保留旧产物读兼容）：旧链读屏视图、写回
#   请求体、审批工作包/终审包载体，以及存量审批账解码所需的 Design/Structure/
#   Writing/Closure 决定族全部删除；旧根一律 legacy_incompatible 拒识。


Stage0SegmentKind = Literal[STAGE0_SEGMENT_KINDS]
Stage1SegmentKind = Literal[STAGE1_SEGMENT_KINDS]
ChapterLabelAnomaly = Literal[CHAPTER_LABEL_ANOMALY]
NonbodyKind = Literal[NONBODY_KINDS]
RemovedOrigin = Literal[REMOVED_ORIGIN]
InboxKind = Literal[INBOX_KIND]
InboxAttention = Literal[INBOX_ATTENTION]
InboxDeliveryEffect = Literal[INBOX_DELIVERY_EFFECT]
OperatorNextState = Literal[OPERATOR_NEXT_STATES]
ResumeVerdict = Literal[RESUME_VERDICTS]
OperatorNode = Literal[tuple(NODES)]
ExecutionPermitKind = Literal[EXECUTION_PERMIT_KINDS]


# ─────────────────────────────────────────────────────────────────────────────
# 零、S-00 / S-03 连续文本视图与零成本动作
# ─────────────────────────────────────────────────────────────────────────────

class Stage0WarningView(StrictModel):
    """S-00 收到的一条可疑项 = **产物里的证据 + 现渲染的那句话**。

    【为什么响应里要多这一格】产物 2026-08-22 起只记证据不记措辞
    （`contracts/schema/stage0.py` 「可疑项」那一段）。措辞总得有人生成，而前端
    自己拼就是同一句话的第二个落点——它必然与 `novel/core/warnings.py` 分家，
    而分家之后前端说的那一句谁都不知道是按哪版阈值算的。
    ⇒ 服务端现渲染、随响应发下去，前端只负责画。
    ⚠ `message` **只活在这一层**：它不进产物，也不许被前端拿去做分支
    （认告警仍然一律用 `kind`）。
    """

    warning: Stage0Warning
    message: str = Field(min_length=1)


class Stage0Report(StrictModel):
    """S-00 真正消费的窄视图；不暴露 revision 持久化形状。"""

    original_filename: str = Field(min_length=1)
    total_code_points: int = Field(ge=1)
    warnings: list[Stage0WarningView]

    #: 这份报告**此刻能不能放行 stage1 与付费链**。
    #:
    #: 【这不是本模块纪律 ② 说的「第二个判官」】那条禁的是视图**自己算**一个判定。
    #: 这一格恰恰相反：它是把**唯一那个判官**
    #: （`novel/core/eligibility.py::is_stage0_executable`）的结论原样投影
    #: 下来。此前前端自己拼 `approved === 'approved' && origin === 'human'`
    #: ——那才是第二个落点，而且是外部评审点名要收口的第五处（前四处是 s0_approval
    #: 判据、stage1 启动前置、orchestrator 队列、付费防线，2026-08-21 已收口）。
    #: ⚠ 前端**不许**再从 `approval` 里自己推这件事：谓词收紧时改一处就够，
    #: 两处各写一遍迟早漏一处，而漏掉的那处就是绕过闸门的复活口。
    #:
    #: ⚠ 这里曾有配套的第二格 `block_reason`（稳定理由码 `pending` /
    #: `legacy_auto_approval_requires_review`），2026-08-22 随 `auto_zero_hit` 死值
    #: 一并删除：理由码只剩一个取值之后，它与本格表达的是同一个 bit
    #: ——当年那条 `_verdict_and_reason_are_one_state` 校验器校的正是这件事，
    #: 而**一个 bit 只该有一个投影**。要再有第二种「批了却跑不得」的理由时整格加回来。
    executable: bool
    revision_id: Annotated[str, Field(pattern=r'^[0-9a-f]{64}$')]
    revision_origin: Stage0RevisionOrigin


class Stage0Segment(FrozenModel):
    '''S-00 原始文本上的一个连续分区；坐标恒是 Python 码点半开区间。'''

    id: str = Field(min_length=1)
    kind: Stage0SegmentKind
    start: int = Field(ge=0)
    end: int = Field(ge=1)
    chars: int = Field(ge=1)
    flagged: bool
    text: str = Field(min_length=1)
    origin: RemovedOrigin | None = None
    removed_kind: NonbodyKind | None = None
    marker_line: str | None = None

    @model_validator(mode='after')
    def _interval_and_remove_metadata_agree(self) -> Stage0Segment:
        if self.end <= self.start:
            raise ValueError(f'[{self.start}, {self.end}) 不是非空半开区间')
        if self.chars != self.end - self.start or self.chars != len(self.text):
            raise ValueError('chars 必须同时等于 end-start 与 text 的码点长度')
        is_remove = self.kind == 'remove'
        # keep 分区带 `removed_kind` = **机器此刻新认出的非正文候选**（不在当前
        # removed[] 里）。2026-08-21 起有人工分叉的轮次不再自动三方合并，
        # 「重选段」是采纳机器新结果的唯一出路——候选必须看得见。它只是提示：
        # 预选集合只认 kind='remove'，不替人勾选。
        is_candidate = self.kind == 'keep' and self.removed_kind is not None
        if is_remove and not self.flagged:
            raise ValueError('remove 分区必须 flagged=true')
        if self.flagged and not (is_remove or is_candidate):
            raise ValueError(
                '非 remove 分区只有机器新候选（带 removed_kind）才可 flagged=true')
        metadata = (self.origin, self.removed_kind, self.marker_line)
        if is_remove != all(value is not None for value in metadata):
            raise ValueError('remove 必须带完整剔除元数据，非 remove 不得冒充剔除记录')
        return self


class Stage0Segments(StrictModel):
    '''S-00 连续原文。原文件移走时返回显式 unavailable，而不是假装没有剔除项。'''

    available: bool
    reason: Annotated[str, Field(min_length=1)] | None
    segments: list[Stage0Segment] = Field(default_factory=list)

    @model_validator(mode='after')
    def _availability_is_explicit(self) -> Stage0Segments:
        if self.available != (self.reason is None):
            raise ValueError('available 与 reason 必须恰好表达同一状态')
        if not self.available and self.segments:
            raise ValueError('原文不可用时不得发一份看似完整的分区')
        if self.available:
            if not self.segments or self.segments[0].start != 0:
                raise ValueError('可用的 stage0 分区必须从坐标 0 开始且非空')
            for left, right in zip(self.segments, self.segments[1:]):
                if left.end != right.start:
                    raise ValueError('stage0 分区必须按顺序首尾连续')
        ids = [segment.id for segment in self.segments]
        if len(ids) != len(set(ids)):
            raise ValueError('stage0 分区 id 不得重复')
        return self


class Stage1Segment(FrozenModel):
    '''S-03 批准后 clean_text 上的一个连续只读分区。'''

    id: str = Field(min_length=1)
    kind: Stage1SegmentKind
    start: int = Field(ge=0)
    end: int = Field(ge=1)
    chars: int = Field(ge=1)
    text: str = Field(min_length=1)
    chapter_ordinal: int | None = Field(default=None, ge=1)
    source_chapter_no: int | None = Field(default=None, ge=0)
    label_delta: int | None = None
    anomaly: ChapterLabelAnomaly | None = None

    @model_validator(mode='after')
    def _interval_and_chapter_metadata_agree(self) -> Stage1Segment:
        if self.end <= self.start:
            raise ValueError(f'[{self.start}, {self.end}) 不是非空半开区间')
        if self.chars != self.end - self.start or self.chars != len(self.text):
            raise ValueError('chars 必须同时等于 end-start 与 text 的码点长度')
        is_chapter = self.kind == 'chapter'
        if is_chapter != (self.chapter_ordinal is not None):
            raise ValueError('只有 chapter 分区带 chapter_ordinal')
        if is_chapter != (self.source_chapter_no is not None):
            raise ValueError('只有 chapter 分区带 source_chapter_no')
        if not is_chapter and (self.label_delta is not None or self.anomaly is not None):
            raise ValueError('只有 chapter 分区带章号诊断')
        if is_chapter and (self.chapter_ordinal == 1) != (self.label_delta is None):
            raise ValueError('有且只有第一章没有 label_delta')
        return self


class Stage1View(StrictModel):
    """Stage1 页面的一次完整读取；连续原文与章号诊断不再分两个 API。"""

    segments: list[Stage1Segment] = Field(min_length=1)
    anomaly_count: int = Field(default=0, ge=0)

    @model_validator(mode='after')
    def _segments_are_one_continuous_document(self) -> Stage1View:
        if self.segments[0].start != 0:
            raise ValueError('stage1 分区必须从 clean_text 坐标 0 开始')
        for left, right in zip(self.segments, self.segments[1:]):
            if left.end != right.start:
                raise ValueError('stage1 分区必须按顺序首尾连续')
        ids = [segment.id for segment in self.segments]
        if len(ids) != len(set(ids)):
            raise ValueError('stage1 分区 id 不得重复')
        chapters = [segment for segment in self.segments if segment.kind == 'chapter']
        ordinals = [chapter.chapter_ordinal for chapter in chapters]
        if ordinals != list(range(1, len(ordinals) + 1)):
            raise ValueError('stage1 章节索引必须从 1 起连续有序')
        hit = sum(1 for chapter in chapters if chapter.anomaly is not None)
        if hit != self.anomaly_count:
            raise ValueError(f'anomaly_count={self.anomaly_count} 与逐章实际命中 {hit} 不符')
        return self


class UploadResult(StrictModel):
    """上传响应；自动开跑路径可附带幂等键、run 与 autostart 结局（R-11.28）。"""

    sid: str = Field(min_length=1)
    job: JobRecord
    run: AgentRunRecord | None = None
    idempotency_key: str | None = Field(
        default=None, pattern=r'^[a-zA-Z0-9_-]{8,128}$')
    autostart: Literal[UPLOAD_AUTOSTART_OUTCOMES] | None = None

    @model_serializer(mode='wrap')
    def _omit_absent_autostart(self, handler: Any) -> dict[str, Any]:
        result = handler(self)
        if self.run is None:
            result.pop('run', None)
        if self.idempotency_key is None:
            result.pop('idempotency_key', None)
        if self.autostart is None:
            result.pop('autostart', None)
        return result


class TrashNovelRequest(StrictModel):
    """删除确认绑定当前书名；sid 由路径参数单独绑定。"""

    expected_title: str = Field(min_length=1)


class TrashNovelResult(StrictModel):
    """项目已移出语料库，但仍可从 `_service/trash/` 恢复。"""

    sid: str = Field(min_length=1)
    title: str = Field(min_length=1)
    trash_id: str = Field(min_length=1)
    trash_rel: str = Field(min_length=1)


class RetentionPolicyRequest(StrictModel):
    """服务空间维护策略；``None`` 明确表示该类 TTL 关闭。"""

    log_ttl_seconds: int | None = Field(default=None, gt=0)
    telemetry_ttl_seconds: int | None = Field(default=None, gt=0)
    trash_ttl_seconds: int | None = Field(default=None, gt=0)
    checkpoint_ttl_seconds: int | None = Field(default=None, gt=0)
    max_entries: int = Field(default=512, ge=1)


class RetentionCandidateView(FrozenModel):
    kind: Literal[RETENTION_CANDIDATE_KINDS]
    sid: str = Field(min_length=1)
    rel: str = Field(min_length=1)
    observed_mtime_ns: int = Field(ge=0)
    observed_size: int = Field(ge=0)
    observed_sha256: str = Field(pattern=r'^[0-9a-f]{64}$')
    evidence: dict[str, object]


class RetentionPlanView(StrictModel):
    """preview 返回的完整冻结计划；apply 原样交回，不拆 token 内部身份。"""

    wire_version: Literal[1]
    generated_at: int = Field(ge=0)
    policy: RetentionPolicyRequest
    examined: int = Field(ge=0)
    candidates: list[RetentionCandidateView]
    plan_token: str = Field(pattern=r'^[0-9a-f]{64}$')


class RetentionApplyRequest(StrictModel):
    plan: RetentionPlanView
    confirmed: bool = False


class RetentionApplyResultView(StrictModel):
    plan_token: str = Field(pattern=r'^[0-9a-f]{64}$')
    removed: list[str]
    receipts: list[str]


class TrashRestorePreviewRequest(StrictModel):
    trash_id: str = Field(pattern=r'^[0-9]{10}-[0-9a-f]{8}$')
    expected_title: str = Field(min_length=1)


class TrashRestorePlanView(StrictModel):
    """book/approval/service run 三面恢复的不可变 preview CAS。"""

    wire_version: Literal[1]
    generated_at: int = Field(ge=0)
    trash_id: str = Field(pattern=r'^[0-9]{10}-[0-9a-f]{8}$')
    sid: str = Field(min_length=1)
    title: str = Field(min_length=1)
    original_dirname: str = Field(min_length=1)
    metadata_sha256: str = Field(pattern=r'^[0-9a-f]{64}$')
    manifest_file_sha256: str = Field(pattern=r'^[0-9a-f]{64}$')
    manifest_sha256: str = Field(pattern=r'^[0-9a-f]{64}$')
    plan_token: str = Field(pattern=r'^[0-9a-f]{64}$')


class TrashRestoreApplyRequest(StrictModel):
    plan: TrashRestorePlanView
    confirmed: bool = False


class TrashRestoreResultView(StrictModel):
    sid: str = Field(min_length=1)
    title: str = Field(min_length=1)
    trash_id: str = Field(min_length=1)
    book_rel: str = Field(min_length=1)


# ─────────────────────────────────────────────────────────────────────────────
# 二、列表与作业（`_api.md` §五）
# ─────────────────────────────────────────────────────────────────────────────

class NovelList(StrictModel):
    """`GET /api/novels`。抓包 `novels.json` 的顶层就是 `{base, novels}`。

    ⚠ **复用 `NovelOverview`，不另造一张轻量清单。** 原型 `Web/novels.py` 里那张
    第二清单的代价是具体的：stage5 的产物是逐章的、存在性不能靠一次 `is_file()`，
    于是它被**整个排除在外**——「有没有陈旧产物」那个徽章从此不覆盖成稿。
    ⚠ 已知代价：每本书都要跑一遍完成度。盘上 3 本无压力，全语料 163 本会明显慢。
    要优化就给同一个函数加浅层分支，**别造第二张表**。
    """

    #: 语料根目录的绝对路径。界面上要显示「你现在看的是哪一批书」。
    base: str = Field(min_length=1)
    novels: list[NovelOverview] = Field(default_factory=list)


class JobList(StrictModel):
    """`GET /api/jobs?sid=`。

    ⚠ 抓包 `jobs.json` 是 `{"jobs":[]}`——**一个样本都没有**，
    所以元素形状只能从 `_orchestration.md` 来（那份文档自己写着这条证据缺口）。
    """

    jobs: list[JobRecord] = Field(default_factory=list)


class LogChunk(StrictModel):
    """`GET /api/jobs/:id/log?offset=N`。按**字节**续读。

    ⚠ 中文一字跨 3 字节，而日志是按字节追加的 ⇒ **只在读的一侧解码**，
    且一次读到的末尾可能切在一个字的中间。`next_byte` 原样回传给下一次 `?offset=`，
    **不许拿 `len(text)` 去推**——那是字符数不是字节数，推出来的偏移会越滑越远。
    """

    text: str
    next_byte: int = Field(ge=0)


class LogStreamEvent(StrictModel):
    """`GET /api/jobs/:id/stream` 每个 SSE event 的 JSON data。

    `next_byte` 同轮询端点，是盘上 UTF-8 **字节**偏移；浏览器把它同时保存为 SSE
    `id`，断线重连时通过 `Last-Event-ID` 续读。终态 event 只出现一次并关闭连接。
    """

    text: str
    next_byte: int = Field(ge=0)
    done: bool
    state: JobState | None = None

    @model_validator(mode='after')
    def _terminal_state_matches_done(self) -> LogStreamEvent:
        if self.done != (self.state is not None):
            raise ValueError('done 与 state 必须同时出现或同时缺席')
        if self.state == 'running':
            raise ValueError('SSE 终态不能是 running')
        return self


class PreviewRequest(StrictModel):
    """`POST /api/jobs/preview` 的请求体。响应是 `JobStart`（`_orchestration.md`）。

    ⚠ **响应直接用 `JobStart` 是有意的**：那个模型自带
    `paid == 任一步 paid` / `writes == 任一步 writes` 的后置条件，于是
    「整份贵贱是逐步折叠出来的」这条 `R-11.7` 白拿；而且**真跑时
    `jobs.run()` 构造的就是这个类**——「预览与真跑是同一个对象」
    从一句话变成字面事实（`R-11.8`）。

    ⚠ `flags` 里的**带值 flag 要带上值**（`--apply-edit <路径>`）。
    `/api/enums` 的 `actions[].needs_value` 就是用来告诉前端「这个按钮要先要一个文件」的。
    """

    sid: Annotated[str, Field(min_length=1)] | None
    node: str = Field(min_length=1)
    flags: list[str] = Field(default_factory=list)

    #: 直接节点作业只需要 legacy executor。C3 起不再接受旧 ``executor_profile`` /
    #: ``kind=agent`` shim；退役格式由 ``retired_formats`` 关闭式拒绝。
    executor_backend: ExecutorBackend | None = None

    #: 正文外发授权与付费确认相互独立。请求层默认未授权；无 backend 的兼容
    #: 路径会在 builder 边界归一成 ``None``，不得把这个默认值伪造成历史快照。
    egress_authorized: bool = False


class QueueCreateRequest(StrictModel):
    """``POST /api/queues`` and ``/preview`` request body."""

    sids: list[str] = Field(min_length=1)
    until_stage: str = Field(min_length=1)
    confirmed: bool = False

    @model_validator(mode='after')
    def _valid_queue_target(self) -> QueueCreateRequest:
        if self.sids != list(dict.fromkeys(self.sids)):
            raise ValueError('queue sids must be unique in requested order')
        if self.until_stage not in STAGES:
            raise ValueError(f'unknown queue target stage {self.until_stage!r}')
        return self


class QueueResumeRequest(StrictModel):
    """``POST /api/queues/{queue_id}/resume`` request body.

    A persisted queue's creation-time confirmation is not reusable for a new
    resume turn.  The API therefore carries the new authorization explicitly
    instead of treating the resume endpoint itself as consent.
    """

    confirmed: bool = False


class QueueList(StrictModel):
    queues: list[QueueRun] = Field(default_factory=list)


class WorkUnitGrantRequest(StrictModel):
    """人工追加的明确意图；token 由预览提供，重复提交不重复增额。"""

    preview_token: str = Field(pattern=r'^[0-9a-f]{64}$')
    intent_id: str = Field(pattern=r'^[a-zA-Z0-9_-]{16,80}$')
    additional: int = Field(default=1, ge=1, le=4, strict=True)
    reason: str = Field(min_length=1, max_length=2000)

    @model_validator(mode='after')
    def _explicit_reason(self) -> WorkUnitGrantRequest:
        if not self.reason.strip():
            raise ValueError('追加额度必须填写理由')
        return self


class WorkUnitCostView(StrictModel):
    """只统计 ledger 的网络尝试与 token 成本；缺用量保持未知。"""

    calls: int = Field(ge=0)
    failed_calls: int = Field(ge=0)
    cached_calls: int = Field(ge=0)
    prompt_tokens: int | None = Field(default=None, ge=0)
    completion_tokens: int | None = Field(default=None, ge=0)
    total_tokens: int | None = Field(default=None, ge=0)
    missing_usage_calls: int = Field(ge=0)


class WorkUnitGrantPreview(StrictModel):
    sid: str
    unit_id: str
    available: bool
    reason: str
    additional: int = Field(ge=1, le=4)
    allowed_additions: list[int]
    preview_token: str | None = None
    unit: WorkUnitView | None = None
    costs: WorkUnitCostView | None = None
    history: list[BudgetGrant] | None = None


class WorkUnitGrantResult(StrictModel):
    grant: BudgetGrant
    current: WorkUnitGrantPreview


class AgentRunRequest(StrictModel):
    """`POST /api/agent/runs`（与 preview）的请求体。

    ⚠ 两项授权彼此独立，不能从其中一项推定另一项。`authorized` 只表示
    **付费授权**，默认 False；`egress_authorized` 只表示小说正文及派生上下文的
    **外发授权**，按当前产品偏好默认 True。前端仍显式提交两项，二者的原文都会
    写进发给 agent 的提示词，并随 `AgentRunStart` 全文留档。
    """

    sid: str = Field(min_length=1)
    authorized: bool = False
    egress_authorized: bool = True

    #: ``None`` 只为旧客户端保留请求兼容形状；当前服务端默认也是
    #: ``null``，preview/start 均会关闭式拒绝，绝不拿 capability 首项代选。
    profiles: PipelineAgentProfiles | None = None

    continuation: RunContinuationSource | None = None
    #: 省略时续跑继承原范围；显式 null 表示解除本次范围上界。
    pause_before_node: OperatorNode | None = None


class AgentRunContinuationView(StrictModel):
    run_id: str
    sid: str | None = None
    available: bool
    reason: str
    profiles: PipelineAgentProfiles | None = None
    source: RunContinuationSource | None = None
    pause_before_node: OperatorNode | None = None


class AgentBatchRequest(StrictModel):
    """`POST /api/agent/runs/batch` 的请求体：**一次交 N 本**，超出并发闸的排队。

    ⚠ 两项授权对这一批**整体**生效，不是逐本勾——逐本勾会让「这一批到底授权了什么」
    没有一个能写进终审包的答案。要给不同书不同授权，就分两批提交。
    ⚠ 没有「全选当前 base 下所有书」这种服务端快捷方式：那会让一次误点
    把一整个语料目录送上付费路径。要跑哪几本，前端逐个 sid 列出来。
    """

    sids: list[str] = Field(min_length=1)
    authorized: bool = False
    egress_authorized: bool = True
    profiles: PipelineAgentProfiles | None = None


class AgentRunPreview(StrictModel):
    """`POST /api/agent/runs/preview` 的响应。与真跑同一构造函数（`R-11.8` 同构）。

    Commander 退役（2026-09-15）后 preview 不再摊开顶层进程命令行：
    ``argv`` / ``prompt`` 保持 schema 兼容并恒为 ``None``（序列化省略），
    摊开的冻结事实只剩 profiles 与两项授权。
    """

    argv: list[str] | None = Field(default=None, min_length=1)
    prompt: str | None = Field(default=None, min_length=1)
    authorized: bool
    egress_authorized: bool

    #: 服务端关闭式校验并物化 execution mode 后的完整选择。当前默认仍为 ``null``，
    #: 因此成功 preview 必然有这份快照；不能拿 capability 数组第一项替用户猜。
    profiles: PipelineAgentProfiles

    continuation: RunContinuationSource | None = None
    pause_before_node: OperatorNode | None = None


class LegacyThinkingCapability(FrozenModel):
    """一种 legacy thinking wire 及其非笛卡尔积 effort 取值。"""

    id: str = Field(min_length=1)
    label: str = Field(min_length=1)
    reasoning_efforts: list[str] = Field(min_length=1)

    @model_validator(mode='after')
    def _efforts_are_unique(self) -> LegacyThinkingCapability:
        if len(self.reasoning_efforts) != len(set(self.reasoning_efforts)):
            raise ValueError('legacy reasoning_efforts 不得重复')
        return self


class LegacyModelCapability(FrozenModel):
    id: str = Field(min_length=1)
    label: str = Field(min_length=1)
    thinking_modes: list[LegacyThinkingCapability] = Field(min_length=1)
    executor_nodes: list[str] = Field(min_length=1)

    @model_validator(mode='after')
    def _domains_are_unique(self) -> LegacyModelCapability:
        mode_ids = [item.id for item in self.thinking_modes]
        if len(mode_ids) != len(set(mode_ids)):
            raise ValueError(f'legacy model={self.id!r} 的 thinking mode 不得重复')
        if len(self.executor_nodes) != len(set(self.executor_nodes)):
            raise ValueError(f'legacy model={self.id!r} 的 executor_nodes 不得重复')
        unknown = sorted(set(self.executor_nodes) - set(LLM_EXECUTOR_NODES))
        if unknown:
            raise ValueError(f'legacy model={self.id!r} 含未知 executor 节点 {unknown}')
        return self


class LegacyProviderCapability(FrozenModel):
    id: str = Field(min_length=1)
    label: str = Field(min_length=1)
    models: list[LegacyModelCapability] = Field(min_length=1)

    @model_validator(mode='after')
    def _models_are_unique(self) -> LegacyProviderCapability:
        ids = [item.id for item in self.models]
        if len(ids) != len(set(ids)):
            raise ValueError(f'legacy provider={self.id!r} 的 model id 不得重复')
        return self


class PipelineConcurrencyCapability(FrozenModel):
    """运行配置的并发选择面；前端不复制节点或总槽范围。

    可并发节点、整书总槽和供应商总槽都取 ``total_min``～``total_max`` 的整数。
    ``node_choices`` 只保留给旧客户端，不再是允许档位。
    """

    nodes: list[str] = Field(default_factory=lambda: list(PIPELINE_PARALLEL_NODES))
    node_choices: list[int] = Field(default_factory=list)
    default_node: int = PIPELINE_DEFAULT_CONCURRENCY
    defaults: PipelineConcurrency = Field(default_factory=PipelineConcurrency)
    total_min: int = PIPELINE_TOTAL_CONCURRENCY_MIN
    total_max: int = PIPELINE_TOTAL_CONCURRENCY_MAX
    scope: str = 'output_root'


class AgentCapabilitiesResponse(StrictModel):
    """``GET /api/agent/capabilities``：executor 选择面的唯一数据源（C3：仅 legacy）。

    路径保留；不再发布 Agent CLI ``drivers[]``。前端 C4 再收口消费面。
    """

    legacy_providers: list[LegacyProviderCapability] = Field(min_length=1)
    executor_nodes: list[str] = Field(min_length=1)

    #: 节点 → 产品契约钉死的 legacy 模型 id（``NODES[node].model``）。
    #: 被钉节点在 legacy 下必须经逐节点覆盖选该模型。没有钉死的节点不出现。
    legacy_model_pins: dict[str, str] = Field(default_factory=dict)

    concurrency: PipelineConcurrencyCapability = Field(default_factory=PipelineConcurrencyCapability)

    #: 服务端经真实整书验收后才能发布的产品预置；``None`` 表示尚无合格预置。
    default_profiles: PipelineAgentProfiles | None = None

    @model_validator(mode='after')
    def _providers_are_unique(self) -> AgentCapabilitiesResponse:
        providers = [provider.id for provider in self.legacy_providers]
        if len(providers) != len(set(providers)):
            raise ValueError('legacy provider id 不得重复')
        if self.executor_nodes != list(LLM_EXECUTOR_NODES):
            raise ValueError(
                'executor_nodes 必须按 LLM_EXECUTOR_NODES 的完整顺序发布，'
                '不能让前端再维护一份节点清单')
        published_models = {
            model.id
            for provider in self.legacy_providers
            for model in provider.models
        }
        unknown_pins = sorted(set(self.legacy_model_pins) - set(LLM_EXECUTOR_NODES))
        if unknown_pins:
            raise ValueError(f'legacy_model_pins 含未知 executor 节点 {unknown_pins}')
        unpublishable = sorted(
            node for node, model in self.legacy_model_pins.items()
            if model not in published_models
        )
        if unpublishable:
            raise ValueError(
                f'legacy_model_pins 指向未发布的 legacy 模型：{unpublishable}；'
                '钉死模型必须随能力矩阵一起发布，前端才能把它预选为覆盖')
        return self


class AgentProfileSettingsView(StrictModel):
    """当前 output base 的用户常用 Agent 配置。

    它与 capability 的 ``default_profiles`` 不是同一个概念：后者是服务端经整书
    验收后才能发布的产品默认；本对象只是用户主动保存的偏好。历史 run 仍只认
    自己冻结的 profile 快照，绝不拿这里的当前值倒推。
    """

    state: AgentProfileSettingsState
    profiles: PipelineAgentProfiles | None
    revision: Sha256
    updated_at: float | None = Field(default=None, ge=0)
    issue: str | None = Field(default=None, min_length=1)

    @model_validator(mode='after')
    def _state_matches_payload(self) -> AgentProfileSettingsView:
        if self.state == 'configured':
            if self.profiles is None or self.updated_at is None or self.issue is not None:
                raise ValueError('configured 必须带 profiles/updated_at 且不得带 issue')
        elif self.state == 'unset':
            if self.profiles is not None or self.issue is not None:
                raise ValueError('unset 不得带 profiles 或 issue')
        elif self.state == 'unreadable':
            if self.profiles is not None or self.issue is None:
                raise ValueError('unreadable 必须只带 issue，不得带 profiles')
            if self.updated_at is not None:
                raise ValueError('unreadable 的更新时间无法可信读取，必须为 null')
        return self


class AgentProfileSettingsUpdate(StrictModel):
    """保存或清空常用配置；revision 是防多标签页静默覆盖的 CAS。"""

    profiles: PipelineAgentProfiles | None
    expected_revision: Sha256


class NetworkPreference(StrictModel):
    """付费外发走不走代理（`novel/config.py` 的 `DEEPSEEK_PROXY` 那一格）。

    `direct` 是产品默认：长请求（high effort 章观察）经本地代理会在约 60 秒被
    切断，那一类失败在 ledger 里记成 connection 类 attempt 并重试到上限——
    2026-09-29 B6-A 首次实跑 31 次 attempt 就是这么烧掉的。代理地址本身是
    运行环境配置，不进任何语义身份；这里只保存用户偏好，与 API key 同级的
    「本机网络」一格，不写进小说产物、不进 run 身份。
    """

    mode: NetworkEgressMode
    proxy_url: str | None = Field(default=None, min_length=1)

    @model_validator(mode='after')
    def _mode_matches_url(self) -> NetworkPreference:
        if self.mode == 'direct':
            if self.proxy_url is not None:
                raise ValueError('直连模式不得带代理地址')
        else:
            if self.proxy_url is None:
                raise ValueError('走代理模式必须给代理地址')
            parsed = urlsplit(self.proxy_url)
            if parsed.scheme not in ('http', 'https') or not parsed.netloc:
                raise ValueError('代理地址必须是合法的 http(s) URL')
        return self


class NetworkSettingsView(StrictModel):
    """当前 output base 的网络偏好；三态与 CAS 语义同常用 Agent 配置。

    `unset`（未配置）按直连处置——产品默认不开代理；只有在设置里显式选
    「走代理」并在本 run 起跑前保存，付费子进程才会带上代理地址。
    """

    state: AgentProfileSettingsState
    network: NetworkPreference | None
    revision: Sha256
    updated_at: float | None = Field(default=None, ge=0)
    issue: str | None = Field(default=None, min_length=1)

    @model_validator(mode='after')
    def _state_matches_payload(self) -> NetworkSettingsView:
        if self.state == 'configured':
            if self.network is None or self.updated_at is None or self.issue is not None:
                raise ValueError('configured 必须带 network/updated_at 且不得带 issue')
        elif self.state == 'unset':
            if self.network is not None or self.issue is not None:
                raise ValueError('unset 不得带 network 或 issue')
        elif self.state == 'unreadable':
            if self.network is not None or self.issue is None:
                raise ValueError('unreadable 必须只带 issue，不得带 network')
            if self.updated_at is not None:
                raise ValueError('unreadable 的更新时间无法可信读取，必须为 null')
        return self


class NetworkSettingsUpdate(StrictModel):
    """保存或清空网络偏好；revision 是防多标签页静默覆盖的 CAS。"""

    network: NetworkPreference | None
    expected_revision: Sha256


class AgentRunList(StrictModel):
    """`GET /api/agent/runs?sid=`。新的在前（与 `JobList` 同款约定）。"""

    runs: list[AgentRunRecord] = Field(default_factory=list)


# ─────────────────────────────────────────────────────────────────────────────
# 二b、待人工清单（修复路由的 `human` 落面；BE-04 第一条的生产者形状）
# ─────────────────────────────────────────────────────────────────────────────

class InboxDecision(FrozenModel):
    """能从 typed 产物直接举证的一次人工裁决；没有就必须为 None。"""

    actor: str = Field(min_length=1)
    reason: str = Field(min_length=1)
    subject_sha256: str = Field(pattern=r'^[0-9a-f]{64}$')
    state: str = Field(min_length=1)
    decided_at: str = Field(min_length=1)

    @model_validator(mode='after')
    def _audit_fields(self) -> 'InboxDecision':
        if not self.actor.strip() or not self.state.strip() or not self.decided_at.strip():
            raise ValueError('inbox decision 的 actor/state/decided_at 不得为空')
        return self


class InboxItem(FrozenModel):
    """「待人工」清单的一条。通用 check 的 `unver` 不入列；
    TODO-24 中能通过人工证据消解的 `pending` / `UNVER` 以 `verification` 入列。

    `rule`：`repairable` / `human_only` / `verification` / `upstream` / `stalled`
    填**判据或复核项 id**；
    `invalid` / `rejected` / `approval` 没有单条判据可指，填**节点 id**（如 `stage6.hook`）。
    `chapter`：**输出章**口径（现契约一源章=一输出章，数值恰好相同；未来规划映射若改，
    本字段跟 stage5 结论键走输出章，别按源章跳屏）。书级 / 整份产物 = `None`。
    ⚠ 前端现役类型把 `chapter` 写成非空 number（mock 时代的形状）——第二阶段接线时
    前端要跟着放宽，不是后端把书级条目编一个假章号。
    """

    kind: InboxKind
    stage: str
    chapter: int | None
    rule: str = Field(min_length=1)
    what: str = Field(min_length=1)
    how: str = Field(min_length=1)
    attention: InboxAttention = 'pending'
    delivery_effect: InboxDeliveryEffect = 'blocking'
    decision: InboxDecision | None = None

    @model_validator(mode='after')
    def _retained_requires_a_typed_decision(self) -> InboxItem:
        if (self.attention == 'reviewed_retained') != (self.decision is not None):
            raise ValueError('reviewed_retained 与 typed decision 必须同进同出')
        return self


class InboxView(StrictModel):
    """`GET /api/novels/:sid/inbox` 的响应体（路由属第二阶段；本模块只定形状）。

    `counts` **八键全出**（0 也要出）：「没有这个键」与「值是 0」不许混——
    stage5 的 `counts` 字段就是这么烂掉的（稀疏 Counter，0 键消失）。
    `wontfix`：被人豁免的条数——豁免不删结论、不进清单，单独计数。
    """

    items: list[InboxItem]
    counts: dict[str, int]
    attention_counts: dict[str, int] = Field(default_factory=dict)
    delivery_counts: dict[str, int] = Field(default_factory=dict)
    total: int = Field(ge=0)
    wontfix: int = Field(ge=0)

    @model_validator(mode='after')
    def _counts_partition_is_consistent(self) -> InboxView:
        if set(self.counts) != set(INBOX_KIND):
            raise ValueError('counts 必须八键全出（0 也要出）——稀疏计数会把「没有」与「0」混掉')
        if self.total != len(self.items) or sum(self.counts.values()) != self.total:
            raise ValueError('counts / total / items 三者必须相互一致')
        got: dict[str, int] = {}
        for item in self.items:
            got[item.kind] = got.get(item.kind, 0) + 1
        if any(self.counts[k] != got.get(k, 0) for k in self.counts):
            raise ValueError('counts 与 items 的分组计数不一致')
        attention = {key: 0 for key in INBOX_ATTENTION}
        delivery = {key: 0 for key in INBOX_DELIVERY_EFFECT}
        for item in self.items:
            attention[item.attention] += 1
            delivery[item.delivery_effect] += 1
        if not self.attention_counts:
            self.attention_counts = attention
        if not self.delivery_counts:
            self.delivery_counts = delivery
        if self.attention_counts != attention:
            raise ValueError('attention_counts 与 items 不一致')
        if self.delivery_counts != delivery:
            raise ValueError('delivery_counts 与 items 不一致')
        return self


# ─────────────────────────────────────────────────────────────────────────────
# 二c、确定性 operator 下一动作（CLI 与只读 HTTP 共用）
# ─────────────────────────────────────────────────────────────────────────────

class OperatorAction(FrozenModel):
    """当前盘面上最早的一项 lane 决策；不携带可被误执行的 legacy Step。"""

    node: OperatorNode
    flags: list[str] = Field(default_factory=list)
    resume_verdict: ResumeVerdict
    why: str = Field(min_length=1)
    waiting_on: str | None = None
    paid: bool
    writes: str
    action_sha256: Sha256


class ExecutionPermitInput(FrozenModel):
    """一份会被节点消费的外部文件；摘要覆盖未经归一的原始字节。"""

    flag: str = Field(pattern=r'^--[a-z][a-z0-9-]*$')
    argument: str = Field(min_length=1)
    resolved_path: str = Field(min_length=1)
    bytes: int = Field(ge=0)
    sha256: Sha256


class ExecutionPermit(FrozenModel):
    """绑定一次 exact node/flags 与当前 operator 读面的执行许可。

    ``operator_action_sha256`` 只属于 ready：恢复许可必须重新按恢复策略铸
    ``permit_sha256``，绝不能复用那枚仅描述 blocker 的 action hash。
    """

    base: str = Field(min_length=1)
    sid: str = Field(min_length=1)
    code_version: str = Field(min_length=1)
    surface_seal: Sha256
    next_state: OperatorNextState
    waiting_on: str | None = None
    kind: ExecutionPermitKind
    node: OperatorNode
    flags: list[str] = Field(default_factory=list)
    paid: bool
    writes: str
    operator_action_sha256: Sha256 | None = None
    external_inputs: list[ExecutionPermitInput] = Field(default_factory=list)
    permit_sha256: Sha256

    @model_validator(mode='after')
    def _source_matches_current_next(self) -> ExecutionPermit:
        if self.kind == 'ready':
            if self.next_state != 'ready' or self.waiting_on is not None:
                raise ValueError('ready permit 必须来自无 waiting_on 的 ready next')
            if self.operator_action_sha256 is None:
                raise ValueError('ready permit 必须绑定当前 operator action SHA-256')
        else:
            if self.next_state != 'blocked' or not self.waiting_on:
                raise ValueError('recovery permit 必须来自明确 waiting_on 的 blocked next')
            if self.operator_action_sha256 is not None:
                raise ValueError('recovery permit 不得复用 blocker action SHA-256')
        input_flags = [item.flag for item in self.external_inputs]
        if len(input_flags) != len(set(input_flags)):
            raise ValueError('同一个外部输入 flag 只能绑定一份字节摘要')
        for flag in input_flags:
            if self.flags.count(flag) != 1:
                raise ValueError(f'外部输入 {flag} 必须在 exact flags 中恰出现一次')
        return self


class OperatorNextDecision(StrictModel):
    """一份冻结快照上的 lane 判断；许可层消费它，不向调用方单独发放。"""

    base: str = Field(min_length=1)
    sid: str = Field(min_length=1)
    code_version: str = Field(min_length=1)
    surface_seal: Sha256
    state: OperatorNextState
    overview: NovelOverview
    inbox: InboxView
    action: OperatorAction | None = None

    @model_validator(mode='after')
    def _state_matches_the_action(self) -> OperatorNextDecision:
        if self.overview.sid != self.sid:
            raise ValueError('OperatorNext.sid 必须与 overview.sid 一致')
        if self.state == 'pipeline_complete':
            if self.action is not None:
                raise ValueError('pipeline_complete 不得携带 action')
            return self
        if self.action is None:
            raise ValueError(f'{self.state} 必须携带 action')
        if self.state == 'ready':
            if self.action.waiting_on is not None:
                raise ValueError('ready action 不得携带 waiting_on')
            # ``redo`` 不改名成 ``run``：operator planner 已在同一冻结快照上
            # 证明 NodeSpec 策略与 exact flags，schema 这里只守 ready 的闭集。
            # skip/unknown/legacy 仍绝不能携执行许可。
            if self.action.resume_verdict not in ('run', 'redo'):
                raise ValueError(
                    'ready action 的 resume_verdict 必须是 run 或已准入的 redo')
        elif not self.action.waiting_on:
            raise ValueError('blocked action 必须明确 waiting_on')
        return self


class OperatorNext(OperatorNextDecision):
    """同一次 operator 快照上的判断与 ready execution token。

    token 对调用方是不拆解的 opaque envelope：ready 时一次随 next 返回，
    blocked 恢复因 exact flags 尚未确定而继续由显式 ``operator permit`` 铸造。
    """

    execution_token: ExecutionPermit | None = None

    @model_validator(mode='after')
    def _execution_token_matches_the_decision(self) -> OperatorNext:
        if self.state != 'ready':
            if self.execution_token is not None:
                raise ValueError(f'{self.state} 不得携带 execution_token')
            return self
        if self.execution_token is None:
            raise ValueError('ready next 必须在同一快照内携带 execution_token')
        assert self.action is not None  # 父类 validator 已守
        token = self.execution_token
        if (
            token.base != self.base
            or token.sid != self.sid
            or token.code_version != self.code_version
            or token.surface_seal != self.surface_seal
            or token.next_state != self.state
            or token.kind != 'ready'
            or token.waiting_on is not None
            or token.node != self.action.node
            or token.flags != self.action.flags
            or token.paid != self.action.paid
            or token.writes != self.action.writes
            or token.operator_action_sha256 != self.action.action_sha256
        ):
            raise ValueError('execution_token 必须逐项绑定同一份 ready decision')
        return self
# ─────────────────────────────────────────────────────────────────────────────
# 三、错误体（`_api.md` §六）
# ─────────────────────────────────────────────────────────────────────────────

class ErrorBody(StrictModel):
    """一次失败的响应体。状态码表在 `novel/api/errors.py::STATUS`。"""

    #: **异常类名**，不是措辞（`Q-6`）。取值面 = `novel/errors.py` 的类名集合，由测试闭合。
    #: 前端的「每屏四态」要求区分「后端没起来 / 这本书没有 / 这一步还没跑」
    #: （`04_delivery.md`），靠句子前缀去认，一次措辞改动就静默断掉。
    kind: str = Field(min_length=1)

    message: str = Field(min_length=1)

    #: 缺的是**哪一个**（`'novel'` / `'stage4/plan_chapters.json'`）。
    #: `None` = 这次失败与某一份具体的东西无关。
    missing: str | None = None

    #: 下一步该做什么。`None` = 说不出来——**说不出来就别编一句**。
    #: 产地是异常自己的 `NovelError.hint`（`novel/api/app.py` 一处 `getattr` 转过来），
    #: **不在这一层从 `message` 里抠**：措辞不是契约（`Q-6`）。
    #: ⚠ 它与 `message` 分成两格正是为了让「这条失败有没有出路」**可判定**——
    #: 拼成一句之后，界面只能把整段话糊进正文，而人得自己从中文里找那条命令。
    hint: str | None = None

# ═════════════════════════════════════════════════════════════════════════════
# 附：STR-06 候选链只读监控面的响应模型（F7d 起落；F8c 分流自迁移包
# `contracts/schema/str06/api.py`，原样合入本模块——模型名与字段一字未改）。
#
# 【与上面现役 API 契约的分界】上面管现役链；这一段管候选链（`/api/str06/
# candidates/...`）的只读监控面。两条纪律与现役一致：
#   ① 只组合，不手抄取值面（字面量取值面的唯一落点是 `contracts/invariants.py`）；
#   ② 一个字段都不自己算判定（`R-11.4`）：没有 `badge` / `passed` / `counts`——
#      候选链的判定只有现役 check 报告与机械回执的 `mechanical_checks` 名单，
#      这里给的是「盘上有什么、回执写了什么」并排呈现。
#
# 【缺席与未知分开（I-42）】候选根可能只跑到某一腿（`--through stage4` 就没有
# stage5/stage7 产物）：每个面用 `present` 显式表达「盘上有没有」，没有就用
# `None` 字段缺席——不拿空数组冒充合法空值，不拿旧轮产物冒充当前结论。
# `semantic` 恒 `unverified`：章级机械回执不升级为语义已验，正文 Judge 已取消。
#
# 【provenance 不进监控面】盘上 `_provenance` 是来源账事实，由 views 剥掉；
# 监控面要回答「这本书写到哪了」，不是「这份产物怎么来的」——后者在盘上与
# `--check-only` 复检里核。
# ═════════════════════════════════════════════════════════════════════════════

class Str06SurfaceWindowView(StrictModel):
    """一个称谓的时间窗（源事实）：从 from_chapter 起可解析到该人物。"""

    surface: str = Field(min_length=1)
    from_chapter: int = Field(ge=1)
    until_chapter: int | None = None


class Str06CharacterView(StrictModel):
    char_id: str = Field(min_length=1)
    canonical: str = Field(min_length=1)
    surfaces: tuple[Str06SurfaceWindowView, ...] = Field(min_length=1)


class Str06NameStructureView(StrictModel):
    """源名称结构（Stage2 names 窄调用产出）：家族、名、化名与称谓构词。"""

    families: dict[str, str] = Field(default_factory=dict,
                                     description='family_id → 姓字面（可复姓）')
    people: dict[str, dict[str, Any]] = Field(default_factory=dict,
                                              description='char_id → 结构条目原样投影')


class Str06CharactersView(StrictModel):
    present: bool
    contract_version: str | None = None
    characters: tuple[Str06CharacterView, ...] = ()
    name_structure: Str06NameStructureView | None = None


class Str06BlueprintBeatView(StrictModel):
    beat_id: str = Field(min_length=1)
    event: str = Field(min_length=1)
    participants: tuple[str, ...] = ()
    effects: tuple[dict[str, Any], ...] = ()
    #: v13（质量改进批次二）：cognition 取代 reveals；loops 独立成数组。
    cognition: tuple[dict[str, Any], ...] = ()
    loops: tuple[dict[str, Any], ...] = ()
    scene_boundary: str | None = None
    temporal_mode: str | None = None


class Str06BlueprintChapterView(StrictModel):
    chapter: int = Field(ge=1)
    beats: tuple[Str06BlueprintBeatView, ...] = Field(min_length=1)
    #: v13：compiler 生成的场景分段（跨章场景在续章同样出现）。
    scenes: tuple[dict[str, Any], ...] = ()
    causal_edges: tuple[dict[str, Any], ...] = ()


class Str06BlueprintView(StrictModel):
    present: bool
    world_constraints: tuple[dict[str, Any], ...] = ()
    chapters: tuple[Str06BlueprintChapterView, ...] = ()


class Str06DesignSceneView(StrictModel):
    scene_id: str = Field(min_length=1)
    driver: dict[str, str]
    realization_brief: str = Field(min_length=1, description='导演说明原样投影')
    pov: str = Field(min_length=1)
    voice: str = Field(min_length=1)
    avoid: tuple[str, ...] = ()
    beats: tuple[dict[str, Any], ...] = Field(min_length=1)


class Str06DesignChapterView(StrictModel):
    chapter_ordinal: int = Field(ge=1)
    purpose: str = Field(min_length=1)
    scene_ids: tuple[str, ...] = Field(min_length=1)


class Str06DesignHookView(StrictModel):
    """Hook 的正式选择（PresentationGraph.hook）：只投影选择本身，不重算。

    `person`／`materials` 的取值面属于产物契约（`HookPov`／`HookMaterial`）——
    视图层不抄第二份字面量，原样投影已由产物模型校验过的值。
    """

    person: str = Field(min_length=1)
    focalizers: tuple[str, ...] = Field(min_length=1)
    materials: tuple[str, ...] = Field(min_length=1,
                                       description='按设计序的 beat_ref（乱序冷开合法）')
    concealed: tuple[str, ...] = ()
    forbidden_reveals: int = Field(ge=0, description='禁止揭示条数（逐条在盘面）')
    design_brief: str = Field(min_length=1)


class Str06DesignBindingView(StrictModel):
    obligations: int = Field(ge=0)
    entries: tuple[dict[str, Any], ...] = ()
    unbound: tuple[str, ...] = ()


class Str06DesignView(StrictModel):
    present: bool
    graph_id: str | None = None
    scenes: tuple[Str06DesignSceneView, ...] = ()
    chapters: tuple[Str06DesignChapterView, ...] = ()
    hook: Str06DesignHookView | None = None
    binding: Str06DesignBindingView | None = None


class Str06IrChapterView(StrictModel):
    chapter_ordinal: int = Field(ge=1)
    purpose: str = Field(min_length=1)
    length_budget: int = Field(ge=1)
    scene_ids: tuple[str, ...] = Field(min_length=1)
    scene_lines: tuple[str, ...] = Field(min_length=1)


class Str06IrNavView(StrictModel):
    """WriterBookIR 的导航面：章预算、章目的、逐场摘要行与称谓释放位置表。"""

    present: bool
    book_title: str = ''
    target_chapter_count: int | None = Field(default=None, ge=1)
    total_length_budget: int | None = Field(default=None, ge=1)
    length_band_low: float | None = Field(default=None, gt=0)
    length_band_high: float | None = Field(default=None, gt=0)
    chapters: tuple[Str06IrChapterView, ...] = ()
    release_schedule: tuple[dict[str, Any], ...] = ()


class Str06ChapterReceiptView(StrictModel):
    """章级回执的监控投影：机械范围与语义未评分开，篇幅披露显式。

    `status`／`semantic` 原样投影产物回执（取值面属于 `ChapterReceipt`；
    语义恒 `unverified`——机械通过不升级为语义已验）。
    """

    status: str = Field(min_length=1)
    mechanical_checks: tuple[str, ...] = Field(min_length=1)
    semantic: str = Field(min_length=1, default='unverified')
    length: dict[str, Any]
    length_disclosed: bool = False
    request_identity: str = Field(min_length=1)
    protocol_version: str = Field(min_length=1)
    renderer_version: str = Field(min_length=1)
    validator_version: str = Field(min_length=1)


class Str06ChapterView(StrictModel):
    ordinal: int = Field(ge=1)
    present: bool
    prose: str | None = None
    receipt: Str06ChapterReceiptView | None = None


class Str06HookView(StrictModel):
    present: bool
    prose: str | None = None
    receipt: dict[str, Any] | None = None


class Str06NamingEntryView(StrictModel):
    char_ref: str = Field(min_length=1)
    family: str = Field(min_length=1)
    given: str | None = None
    full: str = Field(min_length=1)
    aliases: dict[str, str] = Field(default_factory=dict)
    keep_reason: str | None = None


class Str06NamingView(StrictModel):
    present: bool
    entries: tuple[Str06NamingEntryView, ...] = ()


class Str06DeliveryChapterView(StrictModel):
    chapter_ordinal: int = Field(ge=1)
    prose_sha256: str | None = None
    length: dict[str, Any] | None = None
    length_disclosed: bool = False


class Str06DeliveryView(StrictModel):
    """成书交付的监控投影：manifest 是就绪核对记录，字节 hash 逐字转出。"""

    present: bool
    delivery_identity: str | None = None
    final_text_sha256: str | None = None
    chapters: tuple[Str06DeliveryChapterView, ...] = ()
    length_reading: dict[str, Any] | None = None
    disclosure: str | None = None


class Str06NodeView(StrictModel):
    """候选节点表的盘面事实：产物在不在是 I/O 事实，不是业务状态（I-32）。"""

    node: str = Field(min_length=1)
    stage: str = Field(min_length=1)
    owner: str = Field(min_length=1)
    paid: bool
    produced: bool


class Str06LedgerNodeCostView(StrictModel):
    node: str = Field(min_length=1)
    rows: int = Field(ge=0)
    new_paid_rows: int = Field(ge=0)
    cached_rows: int = Field(ge=0)
    prompt_tokens: int | None = None
    completion_tokens: int | None = None
    reasoning_tokens: int | None = None


class Str06LedgerCostView(StrictModel):
    """ledger 成本账的只读投影：缓存采用与实付分开，未知不作零（Q-11／I-43）。"""

    present: bool
    rows: int = Field(default=0, ge=0)
    new_paid_rows: int = Field(default=0, ge=0)
    cached_rows: int = Field(default=0, ge=0)
    prompt_tokens: int | None = None
    completion_tokens: int | None = None
    reasoning_tokens: int | None = None
    unknown_cost_rows: int = Field(default=0, ge=0,
                                   description='usage 缺失或部分提交的行数；不并入合计')
    per_node: tuple[Str06LedgerNodeCostView, ...] = ()


class Str06EpochView(StrictModel):
    epoch_id: str = Field(min_length=1)
    ordinal: int = Field(ge=1)
    reason: str = Field(min_length=1)
    session_run_id: str | None = None


class Str06BodyFailureChapterView(StrictModel):
    """章级局部失败留证（B4）：item 是章序；类别与详情直投影，不并零。"""

    chapter_ordinal: int = Field(ge=1)
    failure_kind: str = Field(min_length=1)
    detail: str


class Str06ChapterFailuresView(StrictModel):
    """逐章失败留证的只读投影（D3/W2）：章观察与正文共用一个形状（I-11）。

    「最近一次完成执行」语义：`requested` 是本次请求的章范围（章观察＝全部
    源章；正文＝调用层选章 `body_chapters` 或全书）；`attempted` 是本次实际
    进入逐章执行器的章（正文不含已提交章）；`chapters` 是 `attempted` 的失败
    子集（合法空清单＝完成执行无失败）；`recorded_at` 是记录时间。视图缺席
    （None）＝该节点还没有一次完成执行——缺席与合法空清单是两件事（I-42）。
    """

    present: bool
    requested: tuple[int, ...] = ()
    attempted: tuple[int, ...] = ()
    chapters: tuple[Str06BodyFailureChapterView, ...] = ()
    recorded_at: int | None = Field(default=None, ge=0)


class Str06BodyAccessView(StrictModel):
    """章就绪五态（I-42）：accepted／missing／rejected／unknown／stale 按章序分开。"""

    accepted: tuple[int, ...] = ()
    missing: tuple[int, ...] = ()
    rejected: tuple[int, ...] = ()
    unknown: tuple[int, ...] = ()
    stale: tuple[int, ...] = ()


class Str06AssemblyOutcomeView(StrictModel):
    """组装结论：not_ready 显式给 reason，不落新交付。"""

    status: str | None = None
    reason: str | None = None
    delivery_identity: str | None = None


class Str06AssemblyReportView(StrictModel):
    """`runtime/delivery/assembly_report.json` 的只读投影（Stage7 集合核对）。"""

    present: bool
    accepted_body: bool | None = None
    body_access: Str06BodyAccessView | None = None
    chain_problems: tuple[str, ...] = ()
    assembly: Str06AssemblyOutcomeView | None = None


class Str06OwnerIssueItemView(StrictModel):
    """一条 typed 机械问题的监控投影（R3）：code／item／message 直投影。"""

    code: str = Field(min_length=1)
    item: str = ''
    message: str = Field(min_length=1)
    context: dict[str, Any] = Field(default_factory=dict)


class Str06OwnerIssueRecordView(StrictModel):
    """一个 owner 的跨节点回派账投影（`runtime/issues/<owner>.json`）。

    ``rounds_total`` 是该 owner 终身已发起的修订轮数（resolve 不复位）；
    ``status`` 取值面归 `OwnerIssueFileV3`，视图层不抄第二份字面量。
    ``against_sha256``（R8a）是发现节点判定时的 owner 产物摘要——监控面据此
    对照「open 记录 + owner 已改稿（盘面摘要已变）＝等下游复验」的中间态。
    """

    owner: str = Field(min_length=1)
    node: str = Field(min_length=1)
    raised_by: str = Field(min_length=1)
    status: str = Field(min_length=1)
    rounds_total: int = Field(ge=0)
    fingerprint: str = Field(min_length=1)
    against_sha256: str = Field(min_length=64, max_length=64)
    issues: tuple[Str06OwnerIssueItemView, ...] = Field(min_length=1)
    updated_at: int = Field(ge=0)


class Str06OwnerIssuesView(StrictModel):
    """修订轮的只读投影（R3）：`runtime/issues/` 全部回派账，open 与 resolved
    都在（resolved 是历史留账，轮数供监控面读取）。

    ``rounds_cap`` 是每 owner 终身修订轮上限（契约常量
    `contracts/invariants.py::OWNER_REVISION_MAX_ROUNDS`）的投影——上限属后端
    契约，前端不抄字面量。视图缺席（None）＝ 从未起过修订轮。
    """

    present: bool
    rounds_cap: int = Field(ge=1)
    records: tuple[Str06OwnerIssueRecordView, ...] = ()


class Str06RunView(StrictModel):
    """候选根的一次运行总览：workflow 状态、失败形状、epoch、节点盘面事实与成本账。

    失败形状是 `runtime/workflow_status.json` 的直投影：`failure_kind`／`detail`
    对任何失败；`error_*` 六字段是 ExecutionError 的显式错误形状（I-36）——
    非执行设施失败（如机械 ValueError）只有前三者，缺失保持 None，不补空串。
    修订轮（R3）由 `owner_issues` 携带；停止原因在失败形状里（含按 item 的
    typed 问题清单），两者并排呈现、不互相推导。逐章失败留证（D3/W2）按
    「最近一次完成执行」语义分节点呈现：`body_failures`（正文）与
    `chapter_observation_failures`（章观察）同一形状。
    """

    candidate: str = Field(min_length=1)
    present: bool
    status: str | None = None
    failed_node: str | None = None
    accepted_body: bool | None = None
    failure_kind: str | None = None
    detail: str | None = None
    error_code: str | None = None
    error_stage: str | None = None
    error_node: str | None = None
    error_item: str | None = None
    error_phase: str | None = None
    error_context: dict[str, Any] | None = None
    body_failures: Str06ChapterFailuresView | None = None
    chapter_observation_failures: Str06ChapterFailuresView | None = None
    assembly_report: Str06AssemblyReportView | None = None
    owner_issues: Str06OwnerIssuesView | None = None
    epoch: Str06EpochView | None = None
    nodes: tuple[Str06NodeView, ...] = ()
    cost: Str06LedgerCostView | None = None


class Str06CandidateSummary(StrictModel):
    candidate: str = Field(min_length=1)
    present: bool
    status: str | None = None
    updated_at: int | None = None


class Str06CandidateList(StrictModel):
    candidates: tuple[Str06CandidateSummary, ...] = ()
