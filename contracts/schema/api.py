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
    PIPELINE_PARALLEL_NODES, PIPELINE_CONCURRENCY_TRIALS, PIPELINE_DEFAULT_CONCURRENCY,
    PIPELINE_TOTAL_CONCURRENCY_MIN, PIPELINE_TOTAL_CONCURRENCY_MAX,
    AgentProfileSettingsState,
    NetworkEgressMode,
    APPROVAL_GATES,
    CHAPTER_LABEL_ANOMALY,
    DESIGN_CHAPTER_DECISIONS,
    DESIGN_CONSTRAINT_OPERATION_KINDS,
    DESIGN_PREFLIGHT_STATUSES,
    DESIGN_REPAIR_CODES_RETIRED,
    DESIGN_REPAIR_CONSTRAINT_ARITY,
    DESIGN_REPAIR_CODES,
    DESIGN_REVIEW_CONTRACT,
    FROZEN_V6_DESIGN_REVIEW_CONTRACT,
    LEGACY_V3_DESIGN_REVIEW_CONTRACT,
    LEGACY_V4_DESIGN_REVIEW_CONTRACT,
    DESIGN_REVIEWER_KINDS,
    DESIGN_REVIEW_STATES,
    DESIGN_STRATEGY_REPLAN_CONSTRAINTS,
    DESIGN_STRATEGY_DECISIONS,
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
    STATUS,
    SECRET_SETUP_KINDS,
    SEVERITY,
    STAGES,
    STRUCTURE_APPROVAL_DECISIONS,
    STRUCTURE_APPROVAL_STATES,
    STRUCTURE_CHANGE_KINDS,
    STRUCTURE_METRIC_CONTRACT,
    STAGE0_SEGMENT_KINDS,
    STAGE1_SEGMENT_KINDS,
    UPLOAD_AUTOSTART_OUTCOMES,
    STAGE5_CLOSURE_GROUPS, STAGE5_CLOSURE_STATES,
    STAGE5_MUST_APPEAR_RULINGS,
)

from contracts.schema.base import FrozenModel, StrictModel
from contracts.schema.common import Sha256
from contracts.schema.orchestration import (
    AgentRunRecord, ArtifactCondition,
    ArtifactState, ExecutorBackend, Freshness, FreshnessReason, JobRecord,
    JobState, NovelOverview, PipelineAgentProfiles, QueueRun,
)
from contracts.schema.stage0 import Stage0RevisionOrigin, Stage0Warning
from contracts.schema.stage4 import (
    BindingConcern, BindingInfeasibilityWitness,
    BlindCandidateChapter, BlindChapterWeightResponse,
)
# ⚠ F8a：`contracts.schema.stage6` 的 import 随 Stage6 整层退役删除
#   （HookApprovalResolution／HookProvenance／HookSources／Mech；F6d 登记）。
# ⚠ 2026-10-04 清理批：旧链读屏视图（Stage2 全局/AnalyzeDraft、Stage4 计划屏、
#   Stage5 成稿/材料/Review 屏、Stage7 改名对照屏、Draft/Trim/NameMap/Residual
#   写回请求体、审批工作包与终审包载体）随各自路由删除后已是零消费者载体，
#   本模块不再 import 对应的 plot/plot_review/analyze_draft/knowledge/stage2/
#   stage5/stage7 旧链模型；存量审批账解码仍需的 Design/Structure/Stage5 闭合
#   决定族全部保留。


Stage0SegmentKind = Literal[STAGE0_SEGMENT_KINDS]
Stage1SegmentKind = Literal[STAGE1_SEGMENT_KINDS]
ChapterLabelAnomaly = Literal[CHAPTER_LABEL_ANOMALY]
NonbodyKind = Literal[NONBODY_KINDS]
RemovedOrigin = Literal[REMOVED_ORIGIN]
GateKey = Literal[APPROVAL_GATES]
InboxKind = Literal[INBOX_KIND]
InboxAttention = Literal[INBOX_ATTENTION]
InboxDeliveryEffect = Literal[INBOX_DELIVERY_EFFECT]
OperatorNextState = Literal[OPERATOR_NEXT_STATES]
ResumeVerdict = Literal[RESUME_VERDICTS]
OperatorNode = Literal[tuple(NODES)]
ExecutionPermitKind = Literal[EXECUTION_PERMIT_KINDS]
StructureApprovalDecisionKind = Literal[STRUCTURE_APPROVAL_DECISIONS]
StructureApprovalState = Literal[STRUCTURE_APPROVAL_STATES]
DesignStrategyDecisionKind = Literal[DESIGN_STRATEGY_DECISIONS]
DesignChapterDecisionKind = Literal[DESIGN_CHAPTER_DECISIONS]
DesignReviewerKind = Literal[DESIGN_REVIEWER_KINDS]
# 退役码仍在 Literal 里：盘上历史决定要能解码；新决定在 approve 闸关闭式拒绝。
DesignRepairCode = Literal[(*DESIGN_REPAIR_CODES, *DESIGN_REPAIR_CODES_RETIRED)]
DesignConstraintOperationKind = Literal[DESIGN_CONSTRAINT_OPERATION_KINDS]
DesignReviewState = Literal[DESIGN_REVIEW_STATES]


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
    """运行配置的并发选择面；前端不复制节点、档位或总槽范围。"""

    nodes: list[str] = Field(default_factory=lambda: list(PIPELINE_PARALLEL_NODES))
    node_choices: list[int] = Field(default_factory=lambda: list(PIPELINE_CONCURRENCY_TRIALS))
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


class ApprovalAction(FrozenModel):
    kind: str = Field(min_length=1)
    label: str = Field(min_length=1)
    extra: list[str] | None = None


class DesignReviewBasis(FrozenModel):
    """Stage4A 人审的整书证据快照；v5 的局部失效按实际策略/章投影判断。"""

    review_contract: str = Field(min_length=1)
    root_clean_sha1: str = Field(pattern=r'^[0-9a-f]{40}$')
    truth_sha256: str = Field(pattern=r'^[0-9a-f]{64}$')
    alias_algorithm: str = Field(min_length=1)


class DesignStrategySubject(FrozenModel):
    """人实际审阅的整书策略面；章权重属于策略，不属于可独立复用的章稿。"""

    basis: DesignReviewBasis
    reconstruction_strategy: str = Field(min_length=1)
    chapter_weights: list[BlindChapterWeightResponse] = Field(min_length=1)

    @model_validator(mode='after')
    def _chapter_weights_are_canonical(self) -> DesignStrategySubject:
        ordinals = [row.chapter_ordinal for row in self.chapter_weights]
        if ordinals != sorted(set(ordinals)):
            raise ValueError('design strategy 章权重必须按章号升序且不重复')
        return self




from contracts.schema.plot_planning import PlannerChapter


class DesignBlindChapterTruth(PlannerChapter):
    """设计审阅与 A 实际消费同一完整剧情投影。"""



DesignStructureReadingValue = str | int | float | bool | None


class DesignStructureEvidence(FrozenModel):
    """从当前 blueprint+A 场景现场复算的安全结构读数，不含正式引用。"""

    metric_contract: str = Field(default=STRUCTURE_METRIC_CONTRACT, min_length=1)
    state: str = Field(min_length=1)
    declared_change_kinds: list[Literal[STRUCTURE_CHANGE_KINDS]]
    computed_change_kinds: list[Literal[STRUCTURE_CHANGE_KINDS]]
    realized_declared: list[Literal[STRUCTURE_CHANGE_KINDS]]
    unrealized_declared: list[Literal[STRUCTURE_CHANGE_KINDS]]
    undeclared_actual: list[Literal[STRUCTURE_CHANGE_KINDS]]
    readings: dict[str, DesignStructureReadingValue]

    @model_validator(mode='after')
    def _change_sets_are_derived(self) -> DesignStructureEvidence:
        order = {name: index for index, name in enumerate(STRUCTURE_CHANGE_KINDS)}
        for label in (
            'declared_change_kinds', 'computed_change_kinds',
            'realized_declared', 'unrealized_declared', 'undeclared_actual',
        ):
            values = getattr(self, label)
            if values != sorted(set(values), key=order.__getitem__):
                raise ValueError(f'design structure {label} 必须规范排序且不重复')
        declared = set(self.declared_change_kinds)
        computed = set(self.computed_change_kinds)
        if set(self.realized_declared) != declared & computed:
            raise ValueError('realized_declared 必须等于 declared∩computed')
        if set(self.unrealized_declared) != declared - computed:
            raise ValueError('unrealized_declared 必须等于 declared-computed')
        if set(self.undeclared_actual) != computed - declared:
            raise ValueError('undeclared_actual 必须等于 computed-declared')
        if not self.readings:
            raise ValueError('design structure readings 不能为空')
        return self


class DesignChapterSubject(FrozenModel):
    """一章可独立复用的人审业务面；不暴露任何 formal id。"""

    # v1/v2 历史决定把整份策略 subject SHA 复制到每一章。它仍
    # 可解码，但 v3 新模板不再产生：其他章的 weight 变化不应让
    # 本章已接受 checkpoint 过期。
    strategy_subject_sha256: str | None = Field(
        default=None, pattern=r'^[0-9a-f]{64}$')
    strategy_epoch_sha256: str | None = Field(
        default=None, pattern=r'^[0-9a-f]{64}$')
    chapter_strategy_sha256: str | None = Field(
        default=None, pattern=r'^[0-9a-f]{64}$')
    blind_truth: DesignBlindChapterTruth
    chapter: BlindCandidateChapter
    structure_evidence: DesignStructureEvidence

    @model_validator(mode='after')
    def _ordinals_match(self) -> DesignChapterSubject:
        legacy = self.strategy_subject_sha256 is not None
        current = (
            self.strategy_epoch_sha256 is not None
            and self.chapter_strategy_sha256 is not None
        )
        if (self.strategy_epoch_sha256 is None) != (
                self.chapter_strategy_sha256 is None):
            raise ValueError('strategy_epoch/chapter_strategy SHA-256 必须同进同出')
        if legacy == current:
            raise ValueError(
                'design chapter subject 必须且只能使用 legacy strategy subject '
                '或 v3 strategy epoch/chapter strategy 身份')
        if self.chapter.output_ordinal != self.blind_truth.chapter_ordinal:
            raise ValueError('design chapter 与 blind truth 章号必须一致')
        if (
            self.chapter.structure_decision.declared_change_kinds
            != self.structure_evidence.declared_change_kinds
        ):
            raise ValueError('structure evidence 必须复述候选声明的结构变化')
        return self


class DesignPreflightFailure(FrozenModel):
    """A→formal 零调用预检的一条 typed 失败投影（无模型正文）。

    ``repair_owner`` 是服务端路由读数（A 修订 / 上游修复 / 代码），界面只呈现
    它，不拿它二次判官（`R-11.4`）。
    """

    code: str = Field(min_length=1)
    rule: str = Field(min_length=1)
    chapter_ordinal: int | None = Field(default=None, ge=1)
    scene_refs: list[str] = Field(default_factory=list)
    refs: list[str] = Field(default_factory=list)
    repair_owner: str = Field(min_length=1)
    detail: str = ''


class DesignPreflightSummary(FrozenModel):
    """`preflight_binding_candidate` 的内存结论投影；不是产物、不落盘。

    绑定 candidate / blueprint / review contract 三个 SHA；对冻结 placement
    构造证据存在性见证并运行联合 HARD。见证不作为正式 B 选择，也不证明语义保真。
    """

    status: Literal[DESIGN_PREFLIGHT_STATUSES]
    candidate_sha256: str = Field(pattern=r'^[0-9a-f]{64}$')
    blueprint_sha256: str = Field(pattern=r'^[0-9a-f]{64}$')
    review_contract: str = Field(min_length=1)
    code_version: str = Field(min_length=1)
    blocked_owners: list[str] = Field(default_factory=list)
    failures: list[DesignPreflightFailure] = Field(default_factory=list)
    infeasibility: list[BindingInfeasibilityWitness] = Field(default_factory=list)


class DesignBindingInfeasibility(FrozenModel):
    """当前 A 候选对应的 fresh B 关闭式不可行证据。"""

    candidate_sha256: str = Field(pattern=r'^[0-9a-f]{64}$')
    binding_concerns: list[BindingConcern] = Field(min_length=1)
    infeasibility_proof: list[BindingInfeasibilityWitness] = Field(min_length=1)


class DesignChapterRepairCapability(FrozenModel):
    output_ordinal: int = Field(ge=1)
    committed_rounds: int = Field(ge=0)
    #: v1/v2 历史字段；当时人工完整章替换尚未建模。
    request_repair_available: bool | None = None
    #: v3 将会发 wire 的 agent repair 与零 wire 的完整章替换分开。
    agent_repair_available: bool | None = None
    full_chapter_edit_available: bool | None = None

    @model_validator(mode='after')
    def _capability_family_is_unambiguous(self) -> DesignChapterRepairCapability:
        legacy = self.request_repair_available is not None
        current = (
            self.agent_repair_available is not None
            and self.full_chapter_edit_available is not None
        )
        if (self.agent_repair_available is None) != (
                self.full_chapter_edit_available is None):
            raise ValueError('agent/full-chapter-edit capability 必须同进同出')
        if legacy == current:
            raise ValueError(
                'design chapter repair capability 必须且只能是 legacy 或 v3 形状')
        return self


class DesignReviewRepairCapability(FrozenModel):
    """当前候选的两条恢复路；旧 v2 历史仍可读。"""

    #: v1/v2 历史形状。
    repairable: bool | None = None
    reason_code: str | None = Field(default=None, min_length=1)
    #: v3 形状：agent 依赖 split lineage/轮数；完整章替换只依赖
    #: current candidate + typed decision，两者不得再压成一个布尔值。
    agent_repairable: bool | None = None
    agent_reason_code: str | None = Field(default=None, min_length=1)
    full_chapter_editable: bool | None = None
    full_chapter_edit_reason_code: str | None = Field(default=None, min_length=1)
    chapters: list[DesignChapterRepairCapability] = Field(min_length=1)

    @model_validator(mode='after')
    def _chapter_availability_is_derived(self) -> DesignReviewRepairCapability:
        ordinals = [row.output_ordinal for row in self.chapters]
        if ordinals != sorted(set(ordinals)):
            raise ValueError('design repair capability 章号必须升序且不重复')
        legacy = self.repairable is not None or self.reason_code is not None
        current_fields = (
            self.agent_repairable, self.agent_reason_code,
            self.full_chapter_editable, self.full_chapter_edit_reason_code,
        )
        current = any(value is not None for value in current_fields)
        if legacy and (
                self.repairable is None or self.reason_code is None):
            raise ValueError('legacy repairable/reason_code 必须同进同出')
        if current and any(value is None for value in current_fields):
            raise ValueError('v3 agent/full-chapter-edit capability 与两个 reason 必须齐全')
        if legacy == current:
            raise ValueError('design repair capability 必须且只能是 legacy 或 v3 形状')
        for row in self.chapters:
            if legacy:
                expected = bool(self.repairable) and row.committed_rounds < 2
                if row.request_repair_available != expected:
                    raise ValueError(
                        'request_repair_available 必须由 lineage 与 committed_rounds 推导')
                if row.agent_repair_available is not None \
                        or row.full_chapter_edit_available is not None:
                    raise ValueError('legacy capability 不得夹带 v3 章行')
            else:
                if row.request_repair_available is not None:
                    raise ValueError('v3 capability 不得夹带 legacy 章行')
                if row.agent_repair_available and not self.agent_repairable:
                    raise ValueError(
                        '章级 agent repair 不得超出全书 agent capability')
                if row.full_chapter_edit_available != self.full_chapter_editable:
                    raise ValueError(
                        'full_chapter_edit_available 必须与全书 edit capability 一致')
        if current and bool(self.agent_repairable) != any(
                bool(row.agent_repair_available) for row in self.chapters):
            raise ValueError('agent_repairable 必须等于章级 agent capability 的并集')
        return self

    @property
    def is_v3(self) -> bool:
        return self.agent_repairable is not None


class DesignStrategyReplanConstraint(FrozenModel):
    """策略 replan 真正进 Executor prompt 的 typed 反馈。

    它只能指向策略 wire 真正拥有的两个变量。人写的 ``reason`` 仍只入
    审计；不得从自由文本反推章号或修改面。
    """

    kind: Literal[DESIGN_STRATEGY_REPLAN_CONSTRAINTS]
    subject_sha256: str = Field(pattern=r'^[0-9a-f]{64}$')
    chapter_ordinals: list[int] = Field(default_factory=list)

    @model_validator(mode='after')
    def _scope_matches_kind(self) -> DesignStrategyReplanConstraint:
        if self.chapter_ordinals != sorted(set(self.chapter_ordinals)) or any(
                value < 1 for value in self.chapter_ordinals):
            raise ValueError('strategy replan chapter_ordinals 必须是升序不重复正整数')
        if self.kind == 'replace_reconstruction_strategy' and self.chapter_ordinals:
            raise ValueError('全局策略替换不得伪装成局部章集')
        if self.kind == 'change_chapter_weights' and not self.chapter_ordinals:
            raise ValueError('权重修订必须指明受影响章')
        return self


class DesignStrategyReview(FrozenModel):
    subject: DesignStrategySubject
    subject_sha256: str = Field(pattern=r'^[0-9a-f]{64}$')
    #: GET 模板上的关闭式占位，不是人的决定。正式 decision 永远为 false。
    placeholder: bool
    #: 只标识服务端生成的未审行；显式确认时必须与 placeholder 一起清空。
    template_token: str | None = Field(
        default=None, pattern=r'^[0-9a-f]{64}$')
    decision: DesignStrategyDecisionKind
    reason: str = Field(min_length=1)
    #: v3 起 request_replan 必须带非空 typed 约束；v1/v2 历史缺字段
    #: 仍可解码，但不能直接变成新的 replan scope。v4 沿用该载体。
    replan_constraints: list[DesignStrategyReplanConstraint] = Field(
        default_factory=list)
    source_decision_sha256: str | None = Field(
        default=None, pattern=r'^[0-9a-f]{64}$')

    @field_validator('reason')
    @classmethod
    def _reason_is_not_blank(cls, value: str) -> str:
        if not value.strip():
            raise ValueError('Stage4A 策略裁决理由不能只含空白')
        return value

    @model_validator(mode='after')
    def _template_marker_matches_placeholder(self) -> DesignStrategyReview:
        if self.placeholder != (self.template_token is not None):
            raise ValueError('Stage4A 策略 placeholder 与 template_token 必须同进同出')
        keys = [json.dumps(
            row.model_dump(mode='json'), ensure_ascii=False,
            sort_keys=True, separators=(',', ':'),
        ) for row in self.replan_constraints]
        if keys != sorted(set(keys)):
            raise ValueError('strategy replan constraints 必须规范排序且不重复')
        split_capability = self.subject.basis.review_contract in {
            DESIGN_REVIEW_CONTRACT, LEGACY_V3_DESIGN_REVIEW_CONTRACT, LEGACY_V4_DESIGN_REVIEW_CONTRACT,
            FROZEN_V6_DESIGN_REVIEW_CONTRACT,
        }
        if self.decision == 'accept' and self.replan_constraints:
            raise ValueError('accept 策略不得夹带 replan constraints')
        if split_capability and self.decision == 'request_replan' \
                and not self.replan_constraints:
            raise ValueError(
                'split-capability request_replan 必须给出 typed strategy constraint')
        return self


class DesignRepairConstraint(FrozenModel):
    """一条可执行的关闭式修订约束；数组顺序由具体 code 的模板解释。"""

    code: DesignRepairCode
    scene_refs: list[str] = Field(default_factory=list)
    opaque_aliases: list[str] = Field(default_factory=list)
    @model_validator(mode='after')
    def _refs_are_unique_without_destroying_order(self) -> DesignRepairConstraint:
        if len(self.scene_refs) != len(set(self.scene_refs)):
            raise ValueError('design constraint scene_refs 不得重复')
        if len(self.opaque_aliases) != len(set(self.opaque_aliases)):
            raise ValueError('design constraint opaque_aliases 不得重复')
        min_scenes, min_aliases = DESIGN_REPAIR_CONSTRAINT_ARITY[self.code]
        if len(self.scene_refs) < min_scenes or len(self.opaque_aliases) < min_aliases:
            raise ValueError(
                f'{self.code} 至少需要 {min_scenes} 个 scene_ref / '
                f'{min_aliases} 个 opaque_alias')
        return self


class DesignConstraintOperation(FrozenModel):
    """对一条真实历史义务的显式处置；理由只作审计，不进入执行指令。"""

    kind: DesignConstraintOperationKind
    constraint_sha256: Sha256
    reason: str = Field(min_length=1)
    replacements: list[DesignRepairConstraint] = Field(default_factory=list)

    @model_validator(mode='after')
    def _operation_is_closed(self) -> DesignConstraintOperation:
        if not self.reason.strip():
            raise ValueError('约束处置理由不能只含空白')
        if (self.kind == 'supersede') != bool(self.replacements):
            raise ValueError('只有 supersede 必须且只能携带非空 replacements')
        keys = [json.dumps(row.model_dump(mode='json'), ensure_ascii=False,
                           sort_keys=True, separators=(',', ':'))
                for row in self.replacements]
        if keys != sorted(set(keys)):
            raise ValueError('replacements 必须规范排序且不重复')
        return self


class DesignConstraintOperations(FrozenModel):
    expected_constraint_set_sha256: Sha256
    items: list[DesignConstraintOperation] = Field(default_factory=list)

    @model_validator(mode='after')
    def _targets_are_canonical(self) -> DesignConstraintOperations:
        targets = [row.constraint_sha256 for row in self.items]
        if targets != sorted(set(targets)):
            raise ValueError('约束处置目标必须按 SHA 升序且不重复')
        return self


class DesignActiveConstraint(FrozenModel):
    constraint_sha256: Sha256
    constraint: DesignRepairConstraint
    source_decision_sha256s: list[Sha256] = Field(min_length=1)


class DesignConstraintCatalog(FrozenModel):
    expected_constraint_set_sha256: Sha256
    items: list[DesignActiveConstraint] = Field(default_factory=list)


class DesignChapterReview(FrozenModel):
    output_ordinal: int = Field(ge=1)
    subject: DesignChapterSubject
    subject_sha256: str = Field(pattern=r'^[0-9a-f]{64}$')
    #: GET 模板上的关闭式占位，不是人的决定。正式 decision 永远为 false。
    placeholder: bool
    template_token: str | None = Field(
        default=None, pattern=r'^[0-9a-f]{64}$')
    decision: DesignChapterDecisionKind
    reason: str = Field(min_length=1)
    constraints: list[DesignRepairConstraint] = Field(default_factory=list)
    constraint_operations: DesignConstraintOperations | None = None
    source_decision_sha256: str | None = Field(
        default=None, pattern=r'^[0-9a-f]{64}$')

    @model_serializer(mode='wrap')
    def _omit_absent_operations(self, handler: Any) -> dict[str, Any]:
        # 新字段缺席必须逐字保留已发行外部 payload/历史/proof 投影；
        # 不能全局 exclude_none，旧字段中的 null 本来就属于身份。
        data = handler(self)
        if self.constraint_operations is None:
            data.pop('constraint_operations', None)
        return data

    @model_validator(mode='after')
    def _decision_has_a_canonical_scope(self) -> DesignChapterReview:
        if self.placeholder != (self.template_token is not None):
            raise ValueError('Stage4A 逐章 placeholder 与 template_token 必须同进同出')
        if not self.reason.strip():
            raise ValueError('Stage4A 逐章裁决理由不能只含空白')
        fingerprints = [
            json.dumps(
                row.model_dump(mode='json'), ensure_ascii=False,
                sort_keys=True, separators=(',', ':'),
            )
            for row in self.constraints
        ]
        if len(fingerprints) != len(set(fingerprints)):
            raise ValueError('design chapter constraints 不得包含完全重复的约束')
        if self.decision == 'accept' and self.constraint_operations is not None \
                and any(item.kind == 'supersede' for item in self.constraint_operations.items):
            raise ValueError('accept 章不得新增替代修复要求')
        if self.decision == 'accept' and self.constraints:
            raise ValueError('accept 章不得夹带 repair scope')
        if self.decision == 'request_repair' and not self.constraints \
                and self.constraint_operations is None:
            raise ValueError('request_repair 章必须给出至少一条 constraint')
        if self.output_ordinal != self.subject.chapter.output_ordinal:
            raise ValueError('design chapter 章号必须与 subject.chapter 一致')
        return self


class DesignReviewMaterialized(StrictModel):
    """服务端重建的完整 Stage4A 审阅材料；不是外部写入载荷。"""

    actor: str = Field(min_length=1)
    reviewer_kind: DesignReviewerKind
    review_run_id: str = Field(pattern=r'^[a-z0-9][a-z0-9_-]{0,127}$')
    general_reason: str = Field(min_length=1)
    candidate_sha256: str = Field(pattern=r'^[0-9a-f]{64}$')
    aggregate_subject_sha256: str = Field(pattern=r'^[0-9a-f]{64}$')
    binding_infeasibility: DesignBindingInfeasibility | None = None
    preflight: DesignPreflightSummary | None = None
    repair_capability: DesignReviewRepairCapability
    strategy: DesignStrategyReview
    chapters: list[DesignChapterReview] = Field(min_length=1)

    @model_validator(mode='after')
    def _submission_is_a_complete_book(self) -> DesignReviewMaterialized:
        if not self.actor.strip() or not self.review_run_id.strip() \
                or not self.general_reason.strip():
            raise ValueError('design review actor/run/general reason 不能只含空白')
        ordinals = [row.output_ordinal for row in self.chapters]
        if ordinals != sorted(set(ordinals)):
            raise ValueError('design review 章号必须升序、去重')
        expected = [row.chapter_ordinal for row in self.strategy.subject.chapter_weights]
        if ordinals != expected:
            raise ValueError('design review 章集必须与策略章权重表精确一致')
        if [row.output_ordinal for row in self.repair_capability.chapters] != ordinals:
            raise ValueError('design repair capability 必须精确覆盖本次审阅章集')
        split_capability = self.strategy.subject.basis.review_contract in {
            DESIGN_REVIEW_CONTRACT, LEGACY_V3_DESIGN_REVIEW_CONTRACT, LEGACY_V4_DESIGN_REVIEW_CONTRACT,
            FROZEN_V6_DESIGN_REVIEW_CONTRACT,
        }
        if split_capability:
            if not self.repair_capability.is_v3:
                raise ValueError('split-capability design review 必须发布分离的 repair/edit capability')
            epochs = {
                row.subject.strategy_epoch_sha256 for row in self.chapters
            }
            if None in epochs or len(epochs) != 1:
                raise ValueError('split-capability 每章 subject 必须绑定同一 strategy epoch')
            if any(
                row.subject.chapter_strategy_sha256 is None
                or row.subject.strategy_subject_sha256 is not None
                for row in self.chapters
            ):
                raise ValueError('split-capability 每章 subject 必须只使用 chapter strategy 身份')
            if any(
                self.strategy.subject_sha256 != constraint.subject_sha256
                for constraint in self.strategy.replan_constraints
            ):
                raise ValueError('strategy replan constraint 必须绑定本次策略 subject')
        else:
            if self.repair_capability.is_v3:
                raise ValueError('legacy design review 不得夹带 v3 repair capability')
            if any(
                row.subject.strategy_subject_sha256 != self.strategy.subject_sha256
                or row.subject.strategy_epoch_sha256 is not None
                or row.subject.chapter_strategy_sha256 is not None
                for row in self.chapters
            ):
                raise ValueError('历史每章 subject 必须绑定本次策略 subject SHA-256')
        return self


class DesignStrategyReplanRuling(StrictModel):
    kind: Literal[DESIGN_STRATEGY_REPLAN_CONSTRAINTS]
    chapter_ordinals: list[int] = Field(default_factory=list)

    @model_validator(mode='after')
    def _scope_matches_kind(self) -> DesignStrategyReplanRuling:
        if self.chapter_ordinals != sorted(set(self.chapter_ordinals)) or any(
                value < 1 for value in self.chapter_ordinals):
            raise ValueError('strategy replan chapter_ordinals 必须升序不重复')
        if self.kind == 'replace_reconstruction_strategy' and self.chapter_ordinals:
            raise ValueError('全局策略替换不得夹带章集')
        if self.kind == 'change_chapter_weights' and not self.chapter_ordinals:
            raise ValueError('权重修订必须指明受影响章')
        return self


class DesignStrategyRuling(StrictModel):
    """Stage4A 策略的外部裁决；主体由 review token 在服务端展开。"""

    decision: DesignStrategyDecisionKind
    reason: str = Field(min_length=1)
    replan_constraints: list[DesignStrategyReplanRuling] = Field(default_factory=list)
    source_decision_sha256: Sha256 | None = None

    @model_serializer(mode='wrap')
    def _omit_absent_source(self, handler: Any) -> dict[str, Any]:
        data = handler(self)
        if self.source_decision_sha256 is None:
            data.pop('source_decision_sha256', None)
        return data

    @model_validator(mode='after')
    def _ruling_is_closed(self) -> DesignStrategyRuling:
        if not self.reason.strip():
            raise ValueError('Stage4A 策略裁决理由不能只含空白')
        if self.source_decision_sha256 is not None and self.decision != 'accept':
            raise ValueError('只允许继承有效接受；新否决必须明确裁决')
        keys = [json.dumps(
            row.model_dump(mode='json'), ensure_ascii=False,
            sort_keys=True, separators=(',', ':'),
        ) for row in self.replan_constraints]
        if keys != sorted(set(keys)):
            raise ValueError('Stage4A 策略约束必须规范排序且不重复')
        if self.decision == 'accept' and self.replan_constraints:
            raise ValueError('accept 策略不得夹带 replan constraints')
        if self.decision == 'request_replan' and not self.replan_constraints:
            raise ValueError('request_replan 必须给出 typed strategy constraint')
        return self


class DesignChapterRuling(StrictModel):
    """Stage4A 一章的外部裁决；不复制章 subject。"""

    output_ordinal: int = Field(ge=1)
    decision: DesignChapterDecisionKind
    reason: str = Field(min_length=1)
    constraints: list[DesignRepairConstraint] = Field(default_factory=list)
    constraint_operations: DesignConstraintOperations | None = None
    source_decision_sha256: Sha256 | None = None

    @model_serializer(mode='wrap')
    def _omit_absent_operations(self, handler: Any) -> dict[str, Any]:
        # 新字段缺席必须逐字保留已发行外部 payload/历史/proof 投影；
        # 不能全局 exclude_none，旧字段中的 null 本来就属于身份。
        data = handler(self)
        if self.constraint_operations is None:
            data.pop('constraint_operations', None)
        if self.source_decision_sha256 is None:
            data.pop('source_decision_sha256', None)
        return data

    @model_validator(mode='after')
    def _ruling_is_closed(self) -> DesignChapterRuling:
        if not self.reason.strip():
            raise ValueError('Stage4A 逐章裁决理由不能只含空白')
        if self.source_decision_sha256 is not None and (
                self.decision != 'accept' or self.constraint_operations is not None):
            raise ValueError('历史接受不得夹带新决定或约束处置')
        keys = [json.dumps(
            row.model_dump(mode='json'), ensure_ascii=False,
            sort_keys=True, separators=(',', ':'),
        ) for row in self.constraints]
        if keys != sorted(set(keys)):
            raise ValueError('Stage4A 逐章 constraints 必须规范排序且不重复')
        if self.decision == 'accept' and self.constraint_operations is not None \
                and any(item.kind == 'supersede' for item in self.constraint_operations.items):
            raise ValueError('accept 章不得新增替代修复要求')
        if self.decision == 'accept' and self.constraints:
            raise ValueError('accept 章不得夹带 repair constraint')
        if self.decision == 'request_repair' and not self.constraints \
                and self.constraint_operations is None:
            raise ValueError('request_repair 章必须给出至少一条 constraint')
        return self


class DesignReviewRequest(StrictModel):
    """Stage4A 唯一写入载荷：一枚 opaque token 加 typed 裁决。"""

    actor: str = Field(min_length=1)
    reviewer_kind: DesignReviewerKind
    review_run_id: str = Field(pattern=r'^[a-z0-9][a-z0-9_-]{0,127}$')
    general_reason: str = Field(min_length=1)
    review_token: str = Field(pattern=r'^s4dr1_[0-9a-f]{64}$')
    strategy: DesignStrategyRuling
    chapters: list[DesignChapterRuling] = Field(min_length=1)

    @model_validator(mode='after')
    def _request_is_canonical(self) -> DesignReviewRequest:
        if not self.actor.strip() or not self.general_reason.strip():
            raise ValueError('design review actor/general reason 不能只含空白')
        ordinals = [row.output_ordinal for row in self.chapters]
        if ordinals != sorted(set(ordinals)):
            raise ValueError('design review 章号必须升序且不重复')
        if self.reviewer_kind == 'inherited' and (
                self.strategy.source_decision_sha256 is None
                or any(row.source_decision_sha256 is None for row in self.chapters)):
            raise ValueError('免费继承收口不得包含新的语义裁决')
        return self


class DesignReviewTemplate(DesignReviewMaterialized):
    """服务端只读关闭式材料；POST 时只投影 token 与 typed rulings。"""

    constraint_catalog: dict[int, DesignConstraintCatalog] = Field(default_factory=dict)
    review_token: str = Field(pattern=r'^s4dr1_[0-9a-f]{64}$')


class DesignReviewDecision(DesignReviewMaterialized):
    """服务端已落盘的人审决定；摘要与决定时间均不接受客户端代填。"""

    decision_sha256: str = Field(pattern=r'^[0-9a-f]{64}$')
    decided_at: str = Field(min_length=1)

    @model_validator(mode='after')
    def _decision_contains_no_template_placeholders(self) -> DesignReviewDecision:
        if self.strategy.placeholder or any(
            row.placeholder for row in self.chapters
        ):
            raise ValueError('正式 design review decision 不得保留模板 placeholder')
        if self.strategy.template_token is not None or any(
            row.template_token is not None for row in self.chapters
        ):
            raise ValueError('正式 design review decision 不得保留 template_token')
        return self


class DesignReviewView(FrozenModel):
    """Stage4A 当前盲审态与不可擦除历史；它不是 ``gate.s4``。"""

    state: DesignReviewState
    candidate_sha256: str | None = Field(
        default=None, pattern=r'^[0-9a-f]{64}$')
    aggregate_subject_sha256: str | None = Field(
        default=None, pattern=r'^[0-9a-f]{64}$')
    binding_infeasibility: DesignBindingInfeasibility | None = None
    preflight: DesignPreflightSummary | None = None
    repair_capability: DesignReviewRepairCapability | None = None
    action_review_token: str | None = Field(
        default=None, pattern=r'^s4rr1_[0-9a-f]{64}$')
    current_decision: DesignReviewDecision | None = None
    history: list[DesignReviewHistoryEntry] = Field(default_factory=list)

    @model_validator(mode='after')
    def _state_matches_current_subject(self) -> DesignReviewView:
        available = self.state != 'unavailable'
        if available != (
            self.candidate_sha256 is not None
            and self.aggregate_subject_sha256 is not None
        ):
            raise ValueError('design review 可用态与两个 current hash 必须同进同出')
        if available != (self.repair_capability is not None):
            raise ValueError('design review 可用态与 repair capability 必须同进同出')
        decided_states = {
            'accepted',
            'repair_requested', 'strategy_replan_requested',
        }
        if (self.state in decided_states) != (self.current_decision is not None):
            raise ValueError('design review 当前决定必须与 decided state 同进同出')
        if self.current_decision is not None:
            if (
                self.current_decision.candidate_sha256 != self.candidate_sha256
                or self.current_decision.aggregate_subject_sha256
                != self.aggregate_subject_sha256
                or self.current_decision.binding_infeasibility
                != self.binding_infeasibility
                or self.current_decision.repair_capability
                != self.repair_capability
            ):
                raise ValueError(
                    'design review 当前决定必须绑定 current candidate/aggregate/B evidence')
            if not self.history or (
                self.current_decision.decision_sha256
                != self.history[-1].decision_sha256
            ):
                raise ValueError('design review current_decision 必须对应 history 最后一项')
            wants_replan = self.current_decision.strategy.decision == 'request_replan'
            wants_repair = any(
                row.decision == 'request_repair'
                for row in self.current_decision.chapters)
            if self.current_decision.strategy.subject.basis.review_contract != DESIGN_REVIEW_CONTRACT:
                raise ValueError('旧审阅契约只能作为 history，不能取得 current authority')
            expected = (
                'strategy_replan_requested' if wants_replan else
                'repair_requested' if wants_repair else 'accepted'
            )
            if self.state != expected:
                raise ValueError('design review state 与策略/逐章决定不一致')
        if self.state == 'missing' and self.history:
            raise ValueError('已有旧 design review 时当前无决定应为 expired')
        if self.state == 'expired' and not self.history:
            raise ValueError('没有 design review 历史时不能声称 expired')
        actionable = (
            self.state in ('repair_requested', 'strategy_replan_requested')
        )
        if actionable != (self.action_review_token is not None):
            raise ValueError('Stage4A 修订态必须且只能发布一枚 action review token')
        return self


class DesignRepairChapterScope(FrozenModel):
    output_ordinal: int = Field(ge=1)
    subject_sha256: str = Field(pattern=r'^[0-9a-f]{64}$')
    #: v1/v2 临时恢复面只保存当前决定，没有历史累计语义。
    constraints: list[DesignRepairConstraint] = Field(default_factory=list)
    #: v3 的两条正交轴：本轮新决定与本 strategy epoch 全部未关闭
    #: 累计约束。Executor 永远消费 accumulated，不得只看 new。
    new_constraints: list[DesignRepairConstraint] = Field(default_factory=list)
    accumulated_constraints: list[DesignRepairConstraint] = Field(default_factory=list)
    constraint_set_sha256: str | None = Field(
        default=None, pattern=r'^[0-9a-f]{64}$')

    constraint_operations: DesignConstraintOperations | None = None

    @model_serializer(mode='wrap')
    def _omit_absent_operations(self, handler: Any) -> dict[str, Any]:
        # 新字段缺席必须逐字保留已发行外部 payload/历史/proof 投影；
        # 不能全局 exclude_none，旧字段中的 null 本来就属于身份。
        data = handler(self)
        if self.constraint_operations is None:
            data.pop('constraint_operations', None)
        return data

    @property
    def is_v3(self) -> bool:
        """这行是否使用累计约束族；供外围 scope 的世代闭合校验复用。"""
        return self.constraint_set_sha256 is not None

    @model_validator(mode='after')
    def _constraint_family_is_closed(self) -> DesignRepairChapterScope:
        current = bool(
            self.constraint_operations is not None
            or self.new_constraints or self.accumulated_constraints
            or self.constraint_set_sha256 is not None
        )
        if self.constraints and current:
            raise ValueError('repair chapter scope 不得混用 legacy/v3 constraints')
        if not self.constraints and not current:
            raise ValueError('repair chapter scope 必须携带一种约束形状')
        if current:
            if (not self.new_constraints and self.constraint_operations is None) \
                    or not self.accumulated_constraints \
                    or self.constraint_set_sha256 is None:
                raise ValueError(
                    'v3 repair chapter scope 必须同时携带 new/accumulated/constraint_set')
            new_rows = [
                json.dumps(row.model_dump(mode='json'), ensure_ascii=False,
                           sort_keys=True, separators=(',', ':'))
                for row in self.new_constraints
            ]
            accumulated = [
                json.dumps(row.model_dump(mode='json'), ensure_ascii=False,
                           sort_keys=True, separators=(',', ':'))
                for row in self.accumulated_constraints
            ]
            if new_rows != sorted(set(new_rows)) \
                    or accumulated != sorted(set(accumulated)):
                raise ValueError('v3 repair constraints 必须规范排序且不重复')
            if not set(new_rows).issubset(set(accumulated)):
                raise ValueError('new_constraints 必须完整包含在 accumulated_constraints')
        else:
            keys = [json.dumps(
                row.model_dump(mode='json'), ensure_ascii=False,
                sort_keys=True, separators=(',', ':'),
            ) for row in self.constraints]
            if len(keys) != len(set(keys)):
                raise ValueError('legacy repair constraints 不得重复')
        return self


class DesignReviewHistoryEntry(FrozenModel):
    """公开历史摘要；大 subject 永远只存在于当次 immutable workpack。"""

    actor: str = Field(min_length=1)
    reviewer_kind: DesignReviewerKind
    review_run_id: str = Field(pattern=r'^[a-z0-9][a-z0-9_-]{0,127}$')
    general_reason: str = Field(min_length=1)
    review_token: str = Field(pattern=r'^s4dr1_[0-9a-f]{64}$')
    strategy_decision: DesignStrategyDecisionKind
    chapter_decisions: dict[int, DesignChapterDecisionKind]
    decision_sha256: str = Field(pattern=r'^[0-9a-f]{64}$')
    decided_at: str = Field(min_length=1)


class DesignAcceptedChapterConstraint(FrozenModel):
    """replan 时必须保留的旧 accepted 章身份。"""

    output_ordinal: int = Field(ge=1)
    subject_sha256: str = Field(pattern=r'^[0-9a-f]{64}$')
    chapter_strategy_sha256: str = Field(pattern=r'^[0-9a-f]{64}$')


class DesignStrategyReplanFeedback(FrozenModel):
    """策略 replan 的累计 typed 反馈；不携带人写的 reason。"""

    new_constraints: list[DesignStrategyReplanConstraint] = Field(min_length=1)
    accumulated_constraints: list[DesignStrategyReplanConstraint] = Field(
        min_length=1)
    constraint_set_sha256: str = Field(pattern=r'^[0-9a-f]{64}$')
    accepted_chapters: list[DesignAcceptedChapterConstraint] = Field(
        default_factory=list)
    chapter_failures: list[DesignRepairChapterScope] = Field(default_factory=list)

    @model_validator(mode='after')
    def _feedback_is_canonical(self) -> DesignStrategyReplanFeedback:
        def keys(rows: list[object]) -> list[str]:
            return [json.dumps(
                row.model_dump(mode='json'), ensure_ascii=False,
                sort_keys=True, separators=(',', ':'),
            ) for row in rows]

        new = keys(self.new_constraints)
        accumulated = keys(self.accumulated_constraints)
        if new != sorted(set(new)) or accumulated != sorted(set(accumulated)):
            raise ValueError('strategy replan constraints 必须规范排序且不重复')
        if not set(new).issubset(set(accumulated)):
            raise ValueError('strategy new constraints 必须包含在 accumulated 中')
        accepted = [row.output_ordinal for row in self.accepted_chapters]
        failed = [row.output_ordinal for row in self.chapter_failures]
        if accepted != sorted(set(accepted)) or failed != sorted(set(failed)):
            raise ValueError('replan accepted/failure 章必须分别升序且不重复')
        if set(accepted) & set(failed):
            raise ValueError('同一章不得同时标为 accepted 与 failed')
        if any(not row.is_v3 for row in self.chapter_failures):
            raise ValueError('v3 replan 只能携带累计后的 v3 章失败约束')
        return self


class DesignRepairScope(FrozenModel):
    """CLI/Core 只读消费的稳定恢复面；不暴露服务盘记录形状。"""

    candidate_sha256: str = Field(pattern=r'^[0-9a-f]{64}$')
    aggregate_subject_sha256: str = Field(pattern=r'^[0-9a-f]{64}$')
    decision_sha256: str = Field(pattern=r'^[0-9a-f]{64}$')
    binding_infeasibility: DesignBindingInfeasibility | None = None
    repair_capability: DesignReviewRepairCapability
    strategy_epoch_sha256: str | None = Field(
        default=None, pattern=r'^[0-9a-f]{64}$')
    strategy_replan: bool
    replan_feedback: DesignStrategyReplanFeedback | None = None
    target_chapters: list[int] = Field(default_factory=list)
    chapters: list[DesignRepairChapterScope] = Field(default_factory=list)

    @model_validator(mode='after')
    def _target_chapters_match_rows(self) -> DesignRepairScope:
        ordinals = [row.output_ordinal for row in self.chapters]
        if ordinals != sorted(set(ordinals)) or self.target_chapters != ordinals:
            raise ValueError('design repair target_chapters 必须精确等于规范章行')
        if self.strategy_replan and ordinals:
            raise ValueError('策略级 replan 不得夹带逐章 repair scope')
        if not self.strategy_replan and not ordinals:
            raise ValueError('逐章 repair 必须带非空 target_chapters')
        current = self.strategy_epoch_sha256 is not None
        if current != self.repair_capability.is_v3:
            raise ValueError('repair scope 的 strategy epoch 必须与 capability 世代一致')
        if current:
            if self.strategy_replan != (self.replan_feedback is not None):
                raise ValueError('v3 strategy replan 与 typed feedback 必须同进同出')
            if not self.strategy_replan and any(not row.is_v3 for row in self.chapters):
                raise ValueError('v3 targeted repair 必须携带累计约束 scope')
            available = {
                row.output_ordinal
                for row in self.repair_capability.chapters
                if row.agent_repair_available
            }
        else:
            if self.replan_feedback is not None or any(
                    row.is_v3 for row in self.chapters):
                raise ValueError('legacy repair scope 不得夹带 v3 feedback')
            available = {
                row.output_ordinal
                for row in self.repair_capability.chapters
                if row.request_repair_available
            }
        if not self.strategy_replan and not set(ordinals).issubset(available):
            raise ValueError('逐章 repair scope 包含当前不可执行 agent repair 的章')
        return self


class StructureChapterDecision(FrozenModel):
    """人对当前 Stage4 计划中一章作出的结构裁决。"""

    output_ordinal: int = Field(ge=1)
    decision: StructureApprovalDecisionKind
    reason: str = Field(min_length=1)
    source_decision_sha256: Sha256 | None = None

    @model_serializer(mode='wrap')
    def _omit_absent_source(self, handler: Any) -> dict[str, Any]:
        data = handler(self)
        if self.source_decision_sha256 is None:
            data.pop('source_decision_sha256', None)
        return data

    @model_validator(mode='after')
    def _inherited_acceptance(self) -> StructureChapterDecision:
        if self.source_decision_sha256 is not None and self.decision != 'accept':
            raise ValueError('只允许继承有效结构接受；新否决必须明确裁决')
        return self

    @field_validator('reason')
    @classmethod
    def _reason_is_not_blank(cls, value: str) -> str:
        if not value.strip():
            raise ValueError('逐章结构裁决理由不能只含空白')
        return value


class StructureApprovalRequest(StrictModel):
    """一次原子的全书 Stage4 结构裁决；主体只由 opaque token 携带。"""

    actor: str = Field(min_length=1)
    reason: str = Field(min_length=1)
    review_token: str = Field(pattern=r'^s4sr1_[0-9a-f]{64}$')
    chapters: list[StructureChapterDecision] = Field(min_length=1)

    @model_validator(mode='after')
    def _chapters_are_sorted_unique(self) -> StructureApprovalRequest:
        if not self.actor.strip() or not self.reason.strip():
            raise ValueError('结构裁决 actor/reason 不能只含空白')
        ordinals = [row.output_ordinal for row in self.chapters]
        if ordinals != sorted(set(ordinals)):
            raise ValueError('结构裁决章号必须升序、去重')
        return self


class StructureApprovalDecision(StructureApprovalRequest):
    """服务端已落盘的一次结构裁决；时间由服务端生成，不接受客户端代填。"""

    decided_at: str = Field(min_length=1)


class StructureApprovalView(FrozenModel):
    """当前结构裁决及其历史；失效不删除旧的人类决定。"""

    state: StructureApprovalState
    current_subject_sha256: str | None = Field(
        default=None, pattern=r'^[0-9a-f]{64}$')
    review_token: str | None = Field(
        default=None, pattern=r'^s4sr1_[0-9a-f]{64}$')
    binding_concerns: list[BindingConcern] = Field(default_factory=list)
    current_decision: StructureApprovalDecision | None = None
    history: list[StructureApprovalDecision] = Field(default_factory=list)

    @model_validator(mode='after')
    def _state_matches_the_decision(self) -> StructureApprovalView:
        if self.state == 'unavailable' and self.current_subject_sha256 is not None:
            raise ValueError('unavailable 结构态不能声称存在当前 subject')
        if self.state in ('missing', 'accepted', 'replan_requested') \
                and self.current_subject_sha256 is None:
            raise ValueError(f'{self.state} 结构态必须带当前 subject')
        available = self.current_subject_sha256 is not None
        if available != (self.review_token is not None):
            raise ValueError('结构 current subject 与 review_token 必须同进同出')
        if available and self.review_token != f's4sr1_{self.current_subject_sha256}':
            raise ValueError('结构 review_token 必须封装当前 subject')
        if self.state in ('accepted', 'replan_requested'):
            if self.current_decision is None:
                raise ValueError(f'{self.state} 结构态必须带当前 typed decision')
            if self.current_decision.review_token != self.review_token:
                raise ValueError('当前结构裁决必须绑定当前 review token')
            wants_replan = any(
                row.decision == 'request_replan'
                for row in self.current_decision.chapters)
            if (self.state == 'replan_requested') != wants_replan:
                raise ValueError('结构态必须与逐章 request_replan 裁决一致')
        elif self.current_decision is not None:
            raise ValueError(f'{self.state} 结构态不得把旧裁决冒充当前裁决')
        if self.current_decision is not None:
            if not self.history or self.current_decision != self.history[-1]:
                raise ValueError('current_decision 必须是 history 最后一项')
        if self.state == 'missing' and self.history:
            raise ValueError('已有旧裁决但不再绑定当前 subject 时应为 expired，不是 missing')
        if self.state == 'expired' and not self.history:
            raise ValueError('没有历史裁决时不能声称 expired')
        return self


class WritingApprovalRequest(StrictModel):
    """对当前整本 Stage5 写作主体作出的单 token 授权请求。"""

    actor: str = Field(min_length=1)
    reason: str = Field(min_length=1)
    review_token: str = Field(pattern=r'^s5wr1_[0-9a-f]{64}$')

    @model_validator(mode='after')
    def _identity_is_not_blank(self) -> WritingApprovalRequest:
        if not self.actor.strip() or not self.reason.strip():
            raise ValueError('写作授权 actor/reason 不能只含空白')
        return self


class WritingApprovalDecision(WritingApprovalRequest):
    """服务端已经落盘的一次写作授权；决定时间只由服务端填写。"""

    decided_at: str = Field(min_length=1)


class WritingApprovalView(FrozenModel):
    """当前写作主体与不可擦除的 typed 授权历史。

    ``available`` 回答当前主体能否在已通过 Stage4 结构闸的前提下完整计算；
    真正能否启动写作仍由 ``ApprovalDoc.writing`` 回答。旧 v6 盘上记录可能
    只有 ``writing=true`` 而没有 typed 历史，因此这里允许空历史，不能伪造
    actor/reason 替旧记录补签。
    """

    available: bool = False
    current_subject_sha256: str | None = Field(
        default=None, pattern=r'^[0-9a-f]{64}$')
    review_token: str | None = Field(
        default=None, pattern=r'^s5wr1_[0-9a-f]{64}$')
    current_decision: WritingApprovalDecision | None = None
    history: list[WritingApprovalDecision] = Field(default_factory=list)

    @model_validator(mode='after')
    def _subject_and_current_decision_agree(self) -> WritingApprovalView:
        if self.available != (self.current_subject_sha256 is not None):
            raise ValueError('writing available 与 current subject 必须同进同出')
        if self.available != (self.review_token is not None):
            raise ValueError('writing available 与 review_token 必须同进同出')
        if self.available and self.review_token != f's5wr1_{self.current_subject_sha256}':
            raise ValueError('writing review_token 必须封装当前 subject')
        if self.current_decision is not None:
            if self.current_decision.review_token != self.review_token:
                raise ValueError('当前写作授权必须绑定当前 review token')
            if not self.history or self.current_decision != self.history[-1]:
                raise ValueError('current_decision 必须是 writing history 最后一项')
        return self


# ── Stage5 Commander 闭合回执（P0-2）──────────────────────────────────────────
#
# 一份唯一、强制、hash-bound 的整书回执：由当前 canonical 章节、fresh Review、
# TrimResolution、wontfix 与规则指纹确定性生成模板，Commander 逐项裁决后原子提交。
# ``gate.s5`` 从此只能由这份 typed 回执打开；匿名盖章是关闭式拒绝。

Stage5MustAppearRuling: TypeAlias = Literal[STAGE5_MUST_APPEAR_RULINGS]
Stage5ClosureGroup: TypeAlias = Literal[STAGE5_CLOSURE_GROUPS]


class Stage5MustAppearDecision(StrictModel):
    """一条 ``must_appear_missing`` 的逐项终态裁决。

    只有两种终态：正文已用改写或语义等价方式承载（``confirmed_present``）、
    明确接受缺项（``accepted_exception``，理由必填）。「需要修改」不是终态，
    它只跳转到逐章改稿，改完重算 Review 后再裁决。模板给出 finding 与本章
    canonical 正文的精确 SHA；提交时二者漂移即整份回执过期。
    """

    chapter_ordinal: int = Field(ge=1)
    evidence_key: str = Field(min_length=1)
    finding_sha256: str = Field(pattern=r'^[0-9a-f]{64}$')
    chapter_sha256: str = Field(pattern=r'^[0-9a-f]{64}$')
    ruling: Stage5MustAppearRuling
    reason: str = Field(default='')
    placeholder: bool = False
    template_token: str | None = None

    @model_validator(mode='after')
    def _exception_needs_a_reason(self) -> Stage5MustAppearDecision:
        if self.ruling == 'accepted_exception' and not self.reason.strip():
            raise ValueError('accepted_exception 必须写明接受缺项的理由')
        return self


class Stage5FindingGroupAck(StrictModel):
    """fact landing / 其余 UNVER 的整组披露确认。

    不逐条写理由：一次确认绑定**该组全部 finding 的完整 digest** 与条数。
    确认不会把 UNVER 改成 PASS，也不会从报告中删除任何条目。
    """

    group: Stage5ClosureGroup
    findings_sha256: str = Field(pattern=r'^[0-9a-f]{64}$')
    items: int = Field(ge=1)
    placeholder: bool = False
    template_token: str | None = None


class Stage5TrimWorkItem(FrozenModel):
    """存量闭合工作包里的 trim 待办行；摘要全部由服务端精确读取。"""

    output_ordinal: int = Field(ge=1)
    audit_sha256: str = Field(pattern=r'^[0-9a-f]{64}$')
    chapter_sha256: str = Field(pattern=r'^[0-9a-f]{64}$')


class Stage5TrimSubject(Stage5TrimWorkItem):
    """存量闭合工作包里一条 current trim audit 及其可选当前销账回执。"""

    resolution_sha256: str | None = Field(pattern=r'^[0-9a-f]{64}$')


class Stage5ClosureMaterialized(StrictModel):
    """只读模板使用的完整 Stage5 闭合材料；不是外部提交载荷。"""

    actor: str = Field(min_length=1)
    reason: str = Field(min_length=1)
    workpack_sha256: str = Field(pattern=r'^[0-9a-f]{64}$')
    review_sha256: str = Field(pattern=r'^[0-9a-f]{64}$')
    must_appear: list[Stage5MustAppearDecision] = Field(default_factory=list)
    group_acks: list[Stage5FindingGroupAck] = Field(default_factory=list)

    @model_validator(mode='after')
    def _submission_has_no_duplicate_targets(self) -> Stage5ClosureMaterialized:
        if not self.actor.strip() or not self.reason.strip():
            raise ValueError('closure actor/reason 不能只含空白')
        seen_rows = {
            (row.chapter_ordinal, row.evidence_key) for row in self.must_appear}
        if len(seen_rows) != len(self.must_appear):
            raise ValueError('must_appear 裁决必须逐章逐证据唯一')
        seen_groups = [ack.group for ack in self.group_acks]
        if len(set(seen_groups)) != len(seen_groups):
            raise ValueError('每组 finding 只允许一次整组确认')
        return self


class Stage5MustAppearRulingInput(StrictModel):
    """外部 must-appear 裁决；finding/正文摘要由 token 在服务端展开。"""

    chapter_ordinal: int = Field(ge=1)
    evidence_key: str = Field(min_length=1)
    ruling: Stage5MustAppearRuling
    reason: str = ''

    @model_validator(mode='after')
    def _exception_needs_reason(self) -> Stage5MustAppearRulingInput:
        if self.ruling == 'accepted_exception' and not self.reason.strip():
            raise ValueError('accepted_exception 必须写明接受缺项的理由')
        return self


class Stage5FindingGroupRuling(StrictModel):
    group: Stage5ClosureGroup
    acknowledged: Literal[True]


class Stage5ClosureRequest(StrictModel):
    """Stage5 闭合唯一写入载荷：opaque token 与 typed rulings。"""

    actor: str = Field(min_length=1)
    reason: str = Field(min_length=1)
    review_token: str = Field(pattern=r'^s5cr1_[0-9a-f]{64}$')
    must_appear: list[Stage5MustAppearRulingInput] = Field(default_factory=list)
    group_acks: list[Stage5FindingGroupRuling] = Field(default_factory=list)

    @model_validator(mode='after')
    def _submission_is_canonical(self) -> Stage5ClosureRequest:
        if not self.actor.strip() or not self.reason.strip():
            raise ValueError('closure actor/reason 不能只含空白')
        targets = [(row.chapter_ordinal, row.evidence_key) for row in self.must_appear]
        if targets != sorted(set(targets)):
            raise ValueError('must_appear 裁决必须按章/证据排序且唯一')
        groups = [row.group for row in self.group_acks]
        if groups != sorted(set(groups)):
            raise ValueError('finding group acknowledgement 必须排序且唯一')
        return self


class Stage5ClosureTemplate(Stage5ClosureMaterialized):
    """服务端按当前盘面生成的只读材料；POST 只投影 token 与 typed rulings。

    模板的每条 must_appear / 整组确认都带 ``placeholder=True`` 与一次性
    ``template_token``；原样提交会被关闭式拒绝，必须由人逐项裁决。
    """

    review_token: str = Field(pattern=r'^s5cr1_[0-9a-f]{64}$')


class Stage5ClosureDecision(Stage5ClosureRequest):
    """服务端已落盘的闭合回执；决定摘要与时间不接受客户端代填。"""

    decision_sha256: str = Field(pattern=r'^[0-9a-f]{64}$')
    decided_at: str = Field(min_length=1)

class Stage5MustAppearRow(StrictModel):
    """workpack 里一条待裁决的 ``must_appear_missing``；只读证据面。"""

    chapter_ordinal: int = Field(ge=1)
    evidence_key: str = Field(min_length=1)
    detail: str = Field(min_length=1)
    finding_sha256: str = Field(pattern=r'^[0-9a-f]{64}$')
    chapter_sha256: str = Field(pattern=r'^[0-9a-f]{64}$')


class Stage5FindingGroup(StrictModel):
    """workpack 里一组待整组确认的 finding 摘要；只读证据面。"""

    group: Stage5ClosureGroup
    findings_sha256: str = Field(pattern=r'^[0-9a-f]{64}$')
    items: int = Field(ge=1)
    rules: list[str] = Field(min_length=1)


class Stage5ChapterSubject(FrozenModel):
    """闭合工作包绑定的一章 canonical 完整文件身份。"""

    output_ordinal: int = Field(ge=1)
    chapter_sha256: str = Field(pattern=r'^[0-9a-f]{64}$')


class Stage5ClosureWorkpack(FrozenModel):
    """只读闭合工作包：由当前盘面确定性重算，是模板与过期判定的唯一依据。

    ``blocking`` 回答「现在能不能提交回执」；``must_appear`` / ``fact_group`` /
    ``unver_group`` 回答「Commander 还要看什么」；已由 wontfix / typed resolution
    处置的条目不进任何待办，但完整文件身份分别留在 ``wontfix_sha256`` 与
    ``trim_subjects``，``resolved`` 只作计数。``disclosure`` 是 INFO/metric 的
    只读披露计数（规则 → 条数），不需要裁决。
    """

    review_seq: int
    review_rel: str
    review_sha256: str
    planned_chapters: list[int]
    missing_chapters: list[int]
    chapter_subjects: list[Stage5ChapterSubject]
    invalid_chapters: list[int]
    hard_findings: list[str]
    unresolved_trim_chapters: list[int]
    unresolved_trim_items: list[Stage5TrimWorkItem]
    trim_subjects: list[Stage5TrimSubject]
    wontfix_sha256: str | None = Field(pattern=r'^[0-9a-f]{64}$')
    must_appear: list[Stage5MustAppearRow]
    fact_group: Stage5FindingGroup | None = None
    unver_group: Stage5FindingGroup | None = None
    disclosure: dict[str, int] = Field(default_factory=dict)
    resolved: dict[str, int] = Field(default_factory=dict)
    workpack_sha256: str = Field(pattern=r'^[0-9a-f]{64}$')
    review_token: str = Field(pattern=r'^s5cr1_[0-9a-f]{64}$')

    @model_validator(mode='after')
    def _subject_sets_are_closed(self) -> 'Stage5ClosureWorkpack':
        if self.review_token != f's5cr1_{self.workpack_sha256}':
            raise ValueError('Stage5 closure review_token 必须封装当前 workpack')
        planned = set(self.planned_chapters)
        missing = set(self.missing_chapters)
        subjects = [row.output_ordinal for row in self.chapter_subjects]
        trims = [row.output_ordinal for row in self.unresolved_trim_items]
        trim_subject_ordinals = [
            row.output_ordinal for row in self.trim_subjects]
        if self.planned_chapters != sorted(planned):
            raise ValueError('planned_chapters 必须升序、去重')
        if not missing <= planned:
            raise ValueError('missing_chapters 必须属于 planned_chapters')
        if subjects != sorted(planned - missing):
            raise ValueError('chapter_subjects 必须精确覆盖全部当前 canonical 计划章')
        if trims != sorted(set(trims)):
            raise ValueError('unresolved_trim_items 必须按章升序、去重')
        if self.unresolved_trim_chapters != trims:
            raise ValueError('unresolved_trim_chapters 必须是 trim 工作面的精确章号投影')
        if trim_subject_ordinals != sorted(set(trim_subject_ordinals)):
            raise ValueError('trim_subjects 必须按章升序、去重')
        trim_subject_by_ordinal = {
            row.output_ordinal: row for row in self.trim_subjects}
        unresolved_subjects = [
            row.output_ordinal for row in self.trim_subjects
            if row.resolution_sha256 is None]
        if unresolved_subjects != trims:
            raise ValueError(
                'trim_subjects 中缺当前 resolution 的章必须精确等于 unresolved trim')
        mismatched_trim_items = [
            row.output_ordinal for row in self.unresolved_trim_items
            if (
                (subject := trim_subject_by_ordinal.get(row.output_ordinal)) is None
                or subject.audit_sha256 != row.audit_sha256
                or subject.chapter_sha256 != row.chapter_sha256
            )
        ]
        if mismatched_trim_items:
            raise ValueError(
                'unresolved_trim_items 必须与完整 trim_subjects 双 SHA 投影一致：'
                f'{sorted(set(mismatched_trim_items))}')
        subject_sha = {
            row.output_ordinal: row.chapter_sha256 for row in self.chapter_subjects}
        mismatched_findings = [
            row.chapter_ordinal for row in self.must_appear
            if subject_sha.get(row.chapter_ordinal) != row.chapter_sha256
        ]
        if mismatched_findings:
            raise ValueError(
                'must_appear 的章 SHA 必须与完整 canonical 章主体表一致：'
                f'{sorted(set(mismatched_findings))}')
        return self

    def blockers(self) -> list[str]:
        """当前不可提交回执的原因；空列表 = 可以提交。

        ⚠ trim 不再是 blocker（批次 B3，2026-09-15 全自动裁决）：
        ``paragraph_budget_missed`` 是登记的可降级目标偏差（`Rule.degradable`，
        契约 §七），超限未消的章按预定义规则由机械保留并披露交付；
        ``unresolved_trim_*`` 仍作为事实与披露面保留在本工作包上。
        """
        blockers: list[str] = []
        if self.missing_chapters:
            blockers.append(f'缺正式章稿 {self.missing_chapters}')
        if self.invalid_chapters:
            blockers.append(f'仍有 .INVALID {self.invalid_chapters}')
        if self.hard_findings:
            blockers.append(f'阻断级判定 {sorted(set(self.hard_findings))}')
        return blockers


Stage5ClosureState = Literal[STAGE5_CLOSURE_STATES]


class Stage5ClosureView(FrozenModel):
    """Stage5 当前闭合态与不可擦除历史；它不是 ``gate.s5`` 本身。"""

    state: Stage5ClosureState
    workpack: Stage5ClosureWorkpack | None = None
    next_step: str | None = None
    current_decision: Stage5ClosureDecision | None = None
    history: list[Stage5ClosureDecision] = Field(default_factory=list)

    @model_validator(mode='after')
    def _state_matches_workpack_and_decision(self) -> Stage5ClosureView:
        if self.state == 'missing':
            if self.workpack is not None:
                raise ValueError('missing 态不能携带 workpack')
            if self.current_decision is not None:
                raise ValueError('missing 态不能有当前决定')
        if self.state == 'review_required':
            if self.workpack is not None or self.current_decision is not None:
                raise ValueError('review_required 态不能携带 current workpack/decision')
            if not (self.next_step or '').strip():
                raise ValueError('review_required 态必须给出重跑最终 Review 的下一步')
        if self.state in ('blocked', 'ready', 'closed', 'expired'):
            if self.workpack is None:
                raise ValueError(f'{self.state} 态必须携带当前 workpack')
        if self.state == 'closed':
            decision = self.current_decision
            if decision is None:
                raise ValueError('closed 态必须有当前决定')
            if decision.review_token != self.workpack.review_token:
                raise ValueError('closed 决定必须绑定当前 workpack token')
            if not self.history or (
                decision.decision_sha256 != self.history[-1].decision_sha256
            ):
                raise ValueError('closed 当前决定必须对应 history 最后一项')
        if self.state == 'expired' and self.current_decision is not None:
            raise ValueError('expired 态的旧决定只能留在 history')
        return self


class ApprovalDoc(StrictModel):
    """公开审批面 v9：Stage2 语义审阅退役后不再携带已读/接受记录。"""

    v: Literal[9] = 9
    sid: str = Field(min_length=1)
    gate: dict[GateKey, bool]
    writing: bool = False
    design_review: DesignReviewView = Field(
        default_factory=lambda: DesignReviewView(state='unavailable'))
    structure: StructureApprovalView
    writing_approval: WritingApprovalView = Field(default_factory=WritingApprovalView)
    closure: Stage5ClosureView = Field(
        default_factory=lambda: Stage5ClosureView(state='missing'))
    spawns: dict[GateKey, list[ApprovalAction]] = Field(default_factory=dict)

    @model_validator(mode='after')
    def _approval_state_is_complete(self) -> ApprovalDoc:
        if set(self.gate) != set(APPROVAL_GATES):
            raise ValueError('gate must contain exactly s0,s1,s4,s5,s6,s7')
        if self.gate['s4'] and self.structure.state != 'accepted':
            raise ValueError('s4 gate 只能由当前 accepted 的 typed 结构裁决打开')
        if self.gate['s5'] and self.closure.state != 'closed':
            raise ValueError('s5 gate 只能由当前 typed closure 回执打开')
        return self


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
