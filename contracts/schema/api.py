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
from contracts.invariants import STAGE6_PREPARE_DECISIONS
from contracts.schema.analyze_draft import AnalyzeOwnerDraftEdit, DraftOwner, DraftSourceState
from contracts.schema.plot import PlotBlueprint
from contracts.schema.plot_review import (
    ReviewScope, BlueprintRunProgress,
)
from contracts.schema.orchestration import AgentDeliveryOutcome, RunContinuationSource, WorkUnitView, PipelineConcurrency

import json
import re
from collections.abc import Mapping
from typing import Annotated, Any, Literal, TypeAlias

from pydantic import Field, field_validator, model_serializer, model_validator

from contracts.invariants import (
    WORKPACK_CLOSED_REF_KINDS, WORKPACK_READING_KINDS,
    ADDRESS_CONTEXT,
    LLM_EXECUTOR_NODES,
    PIPELINE_PARALLEL_NODES, PIPELINE_CONCURRENCY_TRIALS, PIPELINE_DEFAULT_CONCURRENCY,
    PIPELINE_TOTAL_CONCURRENCY_MIN, PIPELINE_TOTAL_CONCURRENCY_MAX,
    AgentProfileSettingsState,
    ANCHOR_COVERAGE_STATE,
    ANCHOR_COVERAGE_WARN,
    APPROVAL_GATES,
    BLUEPRINT_REVIEW_SUBJECT_KINDS,
    CHAPTER_LABEL_ANOMALY,
    DELIVERY_MODES,
    DESIGN_CHAPTER_DECISIONS,
    DESIGN_CONSTRAINT_OPERATION_KINDS,
    APPROVAL_WORKPACK_ACTIONS,
    APPROVAL_WORKPACK_DISPOSITIONS,
    APPROVAL_WORKPACK_EFFECTS,
    APPROVAL_WORKPACK_GROUP_EFFECTS,
    APPROVAL_WORKPACK_GROUPS,
    APPROVAL_WORKPACK_KINDS,
    FINAL_APPROVAL_WORKPACK_KINDS,
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
    STAGE7_REVIEW_CONTRACT,
    STAGE7_APPROVAL_CHECK_BASIS,
    INBOX_ATTENTION,
    INBOX_DELIVERY_EFFECT,
    INBOX_KIND,
    NODES,
    EXECUTION_PERMIT_KINDS,
    OPERATOR_NEXT_STATES,
    RESUME_VERDICTS,
    KNOWLEDGE_SUBJECT_KINDS,
    NAME_DISTINCTIVENESS,
    NONBODY_KINDS,
    REMOVED_ORIGIN,
    RETENTION_CANDIDATE_KINDS,
    RESIDUAL_ADOPTION_CONFIDENCE,
    STATUS,
    SECRET_SETUP_KINDS,
    SEVERITY,
    STAGES,
    STAGE5_REVIEW_STATE,
    STRUCTURE_APPROVAL_DECISIONS,
    STRUCTURE_APPROVAL_STATES,
    STRUCTURE_CHANGE_KINDS,
    STRUCTURE_METRIC_CONTRACT,
    STRUCTURAL_ROLES,
    STAGE0_SEGMENT_KINDS,
    STAGE1_SEGMENT_KINDS,
    TIME_LAYERS,
    TEMPORAL_LINKS,
    UPLOAD_AUTOSTART_OUTCOMES,
    STAGE5_CLOSURE_GROUPS, STAGE5_CLOSURE_STATES,
    STAGE5_EDIT_CONFIRMATIONS,
    STAGE5_MUST_APPEAR_RULINGS,
    STAGE5_REVIEWER_REWRITE_DIRECTIVE_CODES,
)

from contracts.schema.base import FrozenModel, StrictModel
from contracts.schema.common import Finding, HashBoundHumanApproval, Sha256
from contracts.schema.characters import CharacterRegistry, CharactersEdit
from contracts.schema.orchestration import (
    AgentRunRecord, ArtifactCondition,
    ArtifactState, ExecutorBackend, Freshness, FreshnessReason, JobRecord,
    JobState, NovelOverview, PipelineAgentProfiles, QueueRun,
)
from contracts.schema.stage0 import Stage0RevisionOrigin, Stage0Warning
from contracts.schema.stage2 import (
    BlueprintProvenance, GlobalEdit, SourceBlurbGrounding, FactEvidence, HookFactEvidence,
)
from contracts.schema.core_evidence import CoreEvidenceAnchor, EvidenceId
from contracts.schema.knowledge import (
    GlobalArcSecret, GlobalProvenance, GlobalSetting, GlobalTimeWindow, GlobalWorldRule,
)
from contracts.schema.stage4 import (
    BindingConcern, BindingInfeasibilityWitness,
    BlindCandidateChapter, BlindChapterWeightResponse,
    CtxChapter, CtxProvenance, PlanGoal, PlanProvenance, SemanticContract,
    StructureDecision, PlanScene,
)
from contracts.schema.stage5 import (
    ChapterSegment, JudgedDraft, RoundFreeProvenance, RoundLlmProvenance,
)
# ⚠ F8a：`contracts.schema.stage6` 的 import 随 Stage6 整层退役删除
#   （HookApprovalResolution／HookProvenance／HookSources／Mech；F6d 登记）。
from contracts.schema.report import (
    AccountingLine, ProvenanceSection, RuleLine, StatusCounts,
)
from contracts.schema.stage7 import (
    Alias, ApprovedNameMap, BookTxt, ExtraSub, IdentityBaseline,
    NameMapProvenance, NameMapSources, Residual, StemBaseline,
)

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
AnchorCoverageState = Literal[ANCHOR_COVERAGE_STATE]
Stage5ReviewState = Literal[STAGE5_REVIEW_STATE]
StructureApprovalDecisionKind = Literal[STRUCTURE_APPROVAL_DECISIONS]
StructureApprovalState = Literal[STRUCTURE_APPROVAL_STATES]
DesignStrategyDecisionKind = Literal[DESIGN_STRATEGY_DECISIONS]
DesignChapterDecisionKind = Literal[DESIGN_CHAPTER_DECISIONS]
DesignReviewerKind = Literal[DESIGN_REVIEWER_KINDS]
ApprovalWorkpackAction = Literal[APPROVAL_WORKPACK_ACTIONS]
ApprovalWorkpackEffect = Literal[APPROVAL_WORKPACK_EFFECTS]
ApprovalWorkpackGroupKind = Literal[APPROVAL_WORKPACK_GROUPS]
ApprovalWorkpackKind = Literal[APPROVAL_WORKPACK_KINDS]
ApprovalWorkpackDisposition = Literal[APPROVAL_WORKPACK_DISPOSITIONS]
FinalApprovalWorkpackKind = Literal[FINAL_APPROVAL_WORKPACK_KINDS]
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
    #: （`novel/stages/stage0/eligibility.py::is_stage0_executable`）的结论原样投影
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


class Stage0EditRequest(StrictModel):
    '''人在 S-00 选择要剔除的分区；两格共同构成乐观锁。

    ⚠ 2026-08-22 阶段 C：乐观锁从 `expected_round` + `expected_clean_sha1` 两格换成
    一格 `expected_revision_id`——revision id 由内容与 parent 一起派生，
    「基于哪一版」与「那一版长什么样」它一格就钉完了，而原来那两格之间
    还可能互相矛盾。原文身份仍单独钉（它是整条链的前提，不是某一版的属性）。
    '''

    expected_revision_id: Annotated[str, Field(pattern=r'^[0-9a-f]{64}$')]
    removed_segment_ids: list[str] = Field(default_factory=list)
    actor: str = Field(min_length=1)
    reason: str = Field(min_length=1)

    @model_validator(mode='after')
    def _ids_are_unique(self) -> Stage0EditRequest:
        if len(self.removed_segment_ids) != len(set(self.removed_segment_ids)):
            raise ValueError('removed_segment_ids 不得重复')
        if any(not item for item in self.removed_segment_ids):
            raise ValueError('removed_segment_ids 不得含空 id')
        return self


class Stage0ApprovalRequest(StrictModel):
    expected_revision_id: Annotated[str, Field(pattern=r'^[0-9a-f]{64}$')]
    actor: str = Field(min_length=1)
    reason: str = Field(min_length=1)


class Stage0EditResult(StrictModel):
    """Stage0 编辑作业及该计划精确落成的 revision。"""

    job: JobRecord
    revision_id: Annotated[str, Field(pattern=r'^[0-9a-f]{64}$')] | None

    @model_validator(mode='after')
    def _revision_only_exists_for_a_landed_edit(self) -> Stage0EditResult:
        landed = self.job.state in ('ok', 'landed_red')
        if landed != (self.revision_id is not None):
            raise ValueError('Stage0 edit revision_id 只可随已落盘作业返回')
        return self


class Stage0CandidateDecisionRequest(StrictModel):
    """人对机器候选的三选一裁决（保留当前 = 不发这个请求 / 采纳 / 否掉）。

    ⚠ 2026-08-22 阶段 C 取代 `Stage0MergeDecisionRequest`：diff3 与 typed 冲突下线
    之后，页面上要决定的不再是「逐个冲突选 ours 还是 theirs」，而是
    **整版留哪一版**——`resolutions` 那一格连同它守的东西一起没有了。
    「采纳后再编辑」不是第三个动作，是 adopt 之后照常走 `POST /stage0/edit`。
    """

    candidate_revision_id: Annotated[str, Field(pattern=r'^[0-9a-f]{64}$')]
    expected_revision_id: Annotated[str, Field(pattern=r'^[0-9a-f]{64}$')]
    actor: str = Field(min_length=1)
    reason: str = Field(min_length=1)


class Stage0CandidateRevisionView(StrictModel):
    revision_id: Annotated[str, Field(pattern=r'^[0-9a-f]{64}$')]
    origin: Stage0RevisionOrigin
    clean_text: str
    body_code_points: int = Field(ge=0)
    removed_count: int = Field(ge=0)


class Stage0CandidateView(StrictModel):
    """`GET /api/novels/:sid/stage0/candidate` 的那一份：**当前稿与候选并排**。

    ⚠ 它把两版的**读数**一起给出来，前端不自己算：机器重跑到底改了什么，
    唯一诚实的答案是逐版对比正文长度与剔除条数，而那是服务端已经有的事实。
    """

    candidate: Stage0CandidateRevisionView
    current_revision_id: Annotated[str, Field(pattern=r'^[0-9a-f]{64}$')]
    current_body_code_points: int = Field(ge=0)
    current_removed_count: int = Field(ge=0)
    current_origin: str = Field(min_length=1)


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


class WorkpackClosedRefReading(FrozenModel):
    ref: str = Field(min_length=1)
    kind: Literal[WORKPACK_CLOSED_REF_KINDS]
    text: str | None = None


class WorkpackConcernReading(FrozenModel):
    """B concern 的 closed_refs 解成人话后的投影；unknown 表示蓝图找不到该引用。"""

    constraint_code: str = Field(min_length=1)
    chapter_ordinal: int | None = Field(default=None, ge=1)
    scenes: list[str] = Field(default_factory=list)
    reason: str = Field(min_length=1)
    closed_refs: list[WorkpackClosedRefReading] = Field(default_factory=list)


from contracts.schema.plot_materials import PlotChapterReviewMaterials
from contracts.schema.stage4 import PlanChapter


class StructureChapterReading(FrozenModel):
    """Stage4B 一章的裁决材料：由 plan + 当前蓝图确定性渲染（`R-5.25`）。"""

    kind: Literal[WORKPACK_READING_KINDS[2]] = WORKPACK_READING_KINDS[2]
    materials: PlotChapterReviewMaterials
    chapter: PlanChapter
    #: 与 stage4.plan 复检同一函数现场复算的本章读数，外加 alignment；
    #: 只含 JSON 标量/列表/字典。
    structure_metrics: dict[str, Any]
    binding_concerns: list[WorkpackConcernReading] = Field(default_factory=list)
    signature_conflict_codes: list[str] = Field(default_factory=list)


class DesignChapterReading(FrozenModel):
    """完整剧情正文只在 truth 中；设计仅通过 placement 与端点引用它。"""

    kind: Literal[WORKPACK_READING_KINDS[1]] = WORKPACK_READING_KINDS[1]
    truth: DesignBlindChapterTruth
    chapter: BlindCandidateChapter


WorkpackChapterReading = Annotated[
    DesignChapterReading | StructureChapterReading,
    Field(discriminator='kind'),
]


class WorkpackReading(FrozenModel):
    """工作包顶层只出现一次的字段说明与整书材料。"""

    kind: Literal[WORKPACK_READING_KINDS[0]] = WORKPACK_READING_KINDS[0]
    legend: dict[str, str]
    reconstruction_strategy: str | None = None
    chapter_weights: list[dict[str, Any]] | None = None
    global_binding_concerns: list[WorkpackConcernReading] = Field(
        default_factory=list)


class WorkpackItem(FrozenModel):
    """工作包的一条异常/披露/读数行；只呈现服务端判定，无自算字段。"""

    code: str = Field(min_length=1)
    rule: str | None = None
    sev: Literal[SEVERITY] | None = None
    status: Literal[STATUS] | None = None
    chapter_ordinal: int | None = Field(default=None, ge=1)
    scene_refs: list[str] = Field(default_factory=list)
    refs: list[str] = Field(default_factory=list)
    repair_owner: str | None = None
    detail: str = Field(min_length=1)
    effect: ApprovalWorkpackEffect
    #: 服务端允许的后续动作（typed 入口名）；INFO/metric/UNVER 恒为空。
    allowed_actions: list[ApprovalWorkpackAction] = Field(default_factory=list)
    #: coverage 行只标「当前主体是否还需重审」，不把历史 accept
    #: 填成当前决定。新主体与从未审过都是 changed=true。
    changed: bool = False
    historical_accept_reusable: bool = False
    source_decision_sha256: str | None = Field(
        default=None, pattern=r'^[0-9a-f]{64}$')
    #: Stage4A 完整章人工替换的单一外部 TOCTOU 票；内部多摘要不让
    #: Commander/skill/UI 手工搬运。
    review_token: str | None = Field(
        default=None, pattern=r'^s4er1_[0-9a-f]{64}$')
    #: v2：coverage 行携带服务端确定性渲染的本章裁决材料；不进 subject/token/proof。
    reading: WorkpackChapterReading | None = None
    inherited_structure_decision: StructureChapterDecision | None = None

    @model_validator(mode='after')
    def _actions_and_history_are_closed(self) -> WorkpackItem:
        if self.inherited_structure_decision is not None and (
                self.effect != 'coverage' or not self.historical_accept_reusable
                or self.inherited_structure_decision.output_ordinal != self.chapter_ordinal
                or self.inherited_structure_decision.source_decision_sha256 != self.source_decision_sha256):
            raise ValueError('结构继承载荷必须来自同一章覆盖义务的有效历史接受')
        if self.reading is not None and self.effect != 'coverage':
            raise ValueError('reading 只属于 coverage 行')
        if (self.sev is None) != (self.status is None):
            raise ValueError('workpack sev/status 必须同进同出')
        order = {value: index for index, value in enumerate(APPROVAL_WORKPACK_ACTIONS)}
        if self.allowed_actions != sorted(
            set(self.allowed_actions), key=order.__getitem__,
        ):
            raise ValueError('allowed_actions 必须去重并按契约顺序排列')
        action_forbidden = (
            self.sev in ('info', 'metric', 'unver')
            or self.status in ('unver', 'skipped')
            or self.effect not in ('block_b', 'block_context', 'coverage')
        )
        if action_forbidden and self.allowed_actions:
            raise ValueError(
                'INFO/metric/UNVER/skipped 与只读效果不得发布审批动作')
        if self.historical_accept_reusable != (
            self.source_decision_sha256 is not None
        ):
            raise ValueError(
                'historical_accept_reusable 必须有精确 source decision SHA')
        if self.historical_accept_reusable and self.changed:
            raise ValueError('精确可复用的历史 accept 不能同时标为 changed')
        if self.review_token is not None and self.chapter_ordinal is None:
            raise ValueError('Stage4 edit review_token 必须绑定明确目标章')
        return self


class WorkpackGroup(FrozenModel):
    """固定分组之一；空组也必须出现（「没有 blocking」是可读结论）。"""

    group: ApprovalWorkpackGroupKind
    scanned: str = Field(min_length=1)
    items: list[WorkpackItem] = Field(default_factory=list)

    @model_validator(mode='after')
    def _items_match_the_group(self) -> WorkpackGroup:
        effects = APPROVAL_WORKPACK_GROUP_EFFECTS[self.group]
        if any(item.effect not in effects for item in self.items):
            raise ValueError(f'{self.group} 组含有不属于该组的 effect')
        if self.group != 'coverage' and any(
            item.changed or item.historical_accept_reusable
            or item.source_decision_sha256 is not None
            for item in self.items
        ):
            raise ValueError('历史 accept / changed 标记只属于 coverage 行')
        return self


class ApprovalWorkpack(FrozenModel):
    """exception-first Commander 工作包 v2 的公共外壳（批次 5；v2 加 reading）。

    只读 façade：绑定当前审阅 subject，不预填任何决定、不启动作业。v2 在 coverage 行
    携带逐章 ``reading``、顶层携带一次 ``legend``；它们是阅读凭证版本的变化，不改
    subject/token/proof，也不是 artifact 升版。
    """

    v: Literal[2] = 2
    sid: str = Field(min_length=1)
    kind: ApprovalWorkpackKind
    candidate_sha256: str | None = Field(
        default=None, pattern=r'^[0-9a-f]{64}$')
    subject_sha256: str = Field(pattern=r'^[0-9a-f]{64}$')
    review_token: str = Field(pattern=r'^s4[ds]r1_[0-9a-f]{64}$')
    chapter_ordinals: list[int] = Field(min_length=1)
    groups: list[WorkpackGroup] = Field(min_length=len(APPROVAL_WORKPACK_GROUPS))
    disposition: ApprovalWorkpackDisposition = 'review_required'
    current_decision_sha256: str | None = Field(
        default=None, pattern=r'^[0-9a-f]{64}$')
    reading: WorkpackReading | None = None

    @model_validator(mode='after')
    def _shape_is_closed_and_covers_every_chapter(self) -> ApprovalWorkpack:
        has_current_decision = self.disposition == 'already_current'
        if has_current_decision != (self.current_decision_sha256 is not None):
            raise ValueError(
                'already_current 与 current_decision_sha256 '
                '必须同时出现')
        if [row.group for row in self.groups] != list(APPROVAL_WORKPACK_GROUPS):
            raise ValueError(
                'workpack 分组必须是固定顺序的全集，不许裁剪或重排')
        if self.chapter_ordinals != sorted(set(self.chapter_ordinals)):
            raise ValueError('workpack chapter_ordinals 必须升序去重')
        if (self.kind == 'design') != (self.candidate_sha256 is not None):
            raise ValueError('design 必须绑 candidate SHA，structure 不得冒充 A 候选')
        prefix = 's4dr1_' if self.kind == 'design' else 's4sr1_'
        if self.review_token != f'{prefix}{self.subject_sha256}':
            raise ValueError('Stage4 workpack review_token 必须封装当前 exact subject')
        expected_block = 'block_b' if self.kind == 'design' else 'block_context'
        blocking = self.groups[0].items
        if any(item.effect != expected_block for item in blocking):
            raise ValueError('blocking effect 必须与 A 挡 B / B 挡 Context 一致')
        if any(
            'request_repair' in item.allowed_actions
            for group in self.groups for item in group.items
        ) and self.kind != 'design':
            raise ValueError('request_repair 只属于 Stage4A 章级修订面')
        token_items = [
            item for group in self.groups for item in group.items
            if item.review_token is not None
        ]
        if token_items and self.kind != 'design':
            raise ValueError('s4er1 review_token 只属于 Stage4A workpack')
        if any(
            not item.code.startswith('design_repair_requested:ch')
            for item in token_items
        ):
            raise ValueError('s4er1 review_token 只发布在当前 repair 决定行')
        if any(
            'accept' in item.allowed_actions and item.effect != 'coverage'
            for group in self.groups for item in group.items
        ):
            raise ValueError('accept 只能出现在全章 coverage，不能被异常行预填')
        expected_reading = (
            DesignChapterReading if self.kind == 'design'
            else StructureChapterReading)
        if any(
            item.reading is not None
            and not isinstance(item.reading, expected_reading)
            for group in self.groups for item in group.items
        ):
            raise ValueError('coverage reading 的类型必须与 workpack kind 一致')
        chapter_set = set(self.chapter_ordinals)
        if any(
            item.chapter_ordinal is not None
            and item.chapter_ordinal not in chapter_set
            for group in self.groups for item in group.items
        ):
            raise ValueError('workpack 条目引用了当前主体之外的章号')
        coverage = self.groups[-1].items
        ordinals = [item.chapter_ordinal for item in coverage]
        if any(value is None for value in ordinals) or set(ordinals) != chapter_set \
                or len(ordinals) != len(chapter_set):
            raise ValueError('coverage 必须不重不漏覆盖当前全部章')
        if any(
            item.sev is not None or item.status is not None
            or item.changed == item.historical_accept_reusable
            for item in coverage
        ):
            raise ValueError(
                'coverage 不伪造判定，且 changed / 精确历史复用必须二选一')
        seen_unchanged = False
        changed_ordinals: list[int] = []
        unchanged_ordinals: list[int] = []
        allowed_for_kind = (
            {'accept', 'request_repair'}
            if self.kind == 'design'
            else {'accept', 'request_replan'}
        )
        for item in coverage:
            if not item.allowed_actions or not set(item.allowed_actions) <= allowed_for_kind:
                raise ValueError('coverage 动作与 A/B typed 决定面不匹配')
            if item.changed:
                if seen_unchanged:
                    raise ValueError('coverage 必须 changed 异常在前')
                changed_ordinals.append(int(item.chapter_ordinal))
            else:
                seen_unchanged = True
                unchanged_ordinals.append(int(item.chapter_ordinal))
        if changed_ordinals != sorted(changed_ordinals) \
                or unchanged_ordinals != sorted(unchanged_ordinals):
            raise ValueError('coverage 的 changed / reusable 各段内必须按章号升序')
        return self


class CompactApprovalCheck(FrozenModel):
    """终审工作包里的 exception-first 机械报告。

    非 ``ok`` 行逐条保留；正常 metric 仍保留读数。其余绿色机械行只由完整分母与
    四态计数证明已扫描，避免 Commander 再整读一份完整 CheckReport。
    """

    rules_scanned: int = Field(ge=0)
    counts: StatusCounts
    provenance: ProvenanceSection
    attention: list[RuleLine] = Field(default_factory=list)
    metrics: list[RuleLine] = Field(default_factory=list)

    @model_validator(mode='after')
    def _is_an_exact_exception_first_projection(self) -> 'CompactApprovalCheck':
        if self.rules_scanned != sum(self.counts.model_dump().values()):
            raise ValueError('rules_scanned 必须等于四态计数之和')
        if any(row.status == 'ok' for row in self.attention):
            raise ValueError('attention 只收非 ok 行')
        if any(row.sev != 'metric' or row.status != 'ok' for row in self.metrics):
            raise ValueError('metrics 只收正常产出的 metric 读数')
        return self


class Stage7ApprovalWorkpack(FrozenModel):
    """Stage7 当前人物决策面的紧凑终审包；身份分层同 Stage6。"""

    v: Literal[3] = 3
    kind: FinalApprovalWorkpackKind
    sid: str = Field(min_length=1)
    review_contract_version: Literal[STAGE7_REVIEW_CONTRACT] = STAGE7_REVIEW_CONTRACT
    review_identity_sha256: str = Field(pattern=r'^[0-9a-f]{64}$')
    validation_seal_sha256: str = Field(pattern=r'^[0-9a-f]{64}$')
    review_token: str = Field(pattern=r'^s7r3_[0-9a-f]{64}$')
    artifact_subject_sha256: str = Field(pattern=r'^[0-9a-f]{64}$')
    decision_subject_sha256: str = Field(pattern=r'^[0-9a-f]{64}$')
    #: 检查固定针对去审批、去交付账的人物表；交付状态由 delivery 单独判定。
    check_basis: Literal[STAGE7_APPROVAL_CHECK_BASIS] = STAGE7_APPROVAL_CHECK_BASIS
    check: CompactApprovalCheck
    surname_map: dict[str, str]
    characters: list['NameDecisionLine'] = Field(min_length=1)
    extra_subs: list[ExtraSub]
    subs_count: int = Field(ge=0)
    kept_count: int = Field(ge=0)
    kept: list[str]
    #: 实际单次扫描命中（逐章 + hook 合计），键是被替换的旧字面；不是 ``str.count``。
    replacement_hits: dict[str, int]
    residual: list[Residual]
    baselines: dict[str, StemBaseline]
    book: list[Finding]
    identity: IdentityBaseline | None
    workpack_sha256: str = Field(pattern=r'^[0-9a-f]{64}$')
    disposition: ApprovalWorkpackDisposition
    current_approval: ApprovedNameMap | None = None

    @model_validator(mode='after')
    def _currentness_is_hash_bound(self) -> 'Stage7ApprovalWorkpack':
        if self.kind != 'stage7':
            raise ValueError('Stage7ApprovalWorkpack.kind 必须是 stage7')
        if self.review_token != f's7r3_{self.workpack_sha256}':
            raise ValueError('Stage7 review_token 必须封装当前 exact workpack')
        if ((self.disposition == 'already_current')
                != (self.current_approval is not None)):
            raise ValueError('already_current 必须携带当前 approval')
        if self.current_approval is not None \
                and self.current_approval.review_identity_sha256 \
                != self.review_identity_sha256:
            raise ValueError('当前 approval 必须绑定这份长期 review identity')
        if self.kept_count != len(self.kept):
            raise ValueError('kept_count 必须与 kept 明细一致')
        if any(value < 0 for value in self.replacement_hits.values()):
            raise ValueError('replacement_hits 不得出现负数')
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


class BatchTrimResolutionItem(StrictModel):
    """批量销账的一章：绑定当章 audit 与 canonical 正文两个精确 SHA。"""

    output_ordinal: int = Field(ge=1)
    audit_sha256: str = Field(pattern=r'^[0-9a-f]{64}$')
    chapter_sha256: str = Field(pattern=r'^[0-9a-f]{64}$')
    #: 单项覆盖理由；空串 = 使用请求级共享 reason。
    reason: str = Field(default='')


class BatchTrimResolutionRequest(StrictModel):
    """Stage5 trim 决定的一次整批提交（P1-2）。

    写前先校验整批：任一项的 audit/chapter SHA 与当前盘面漂移 ⇒ **整批不开始**。
    按章序幂等写入，进程中断后重交同一 manifest 只会继续剩余项。
    actor 共享；reason 共享、单项可覆盖。
    """

    actor: str = Field(min_length=1)
    reason: str = Field(min_length=1)
    items: list[BatchTrimResolutionItem] = Field(min_length=1)

    @model_validator(mode='after')
    def _batch_has_unique_chapters(self) -> BatchTrimResolutionRequest:
        if not self.actor.strip() or not self.reason.strip():
            raise ValueError('批量销账 actor/reason 不能只含空白')
        ordinals = [item.output_ordinal for item in self.items]
        if ordinals != sorted(set(ordinals)):
            raise ValueError('批量销账章号必须升序、去重')
        return self


class Stage5TrimWorkItem(FrozenModel):
    """Commander 可直接提交的当前 trim 工作面；摘要全部由服务端精确读取。"""

    output_ordinal: int = Field(ge=1)
    audit_sha256: str = Field(pattern=r'^[0-9a-f]{64}$')
    chapter_sha256: str = Field(pattern=r'^[0-9a-f]{64}$')


class Stage5ReviewerRewriteDirectiveInput(StrictModel):
    """Commander 对单章 current 正文给出的一条关闭式改写定位。"""

    code: Literal[STAGE5_REVIEWER_REWRITE_DIRECTIVE_CODES]
    current_text_evidence: str = Field(min_length=1, max_length=240)

    @model_validator(mode='after')
    def _evidence_is_concrete(self) -> 'Stage5ReviewerRewriteDirectiveInput':
        if not self.current_text_evidence.strip():
            raise ValueError('reviewer rewrite evidence 不能只含空白')
        return self


class Stage5ReviewerRewriteTarget(StrictModel):
    target_ordinal: int = Field(ge=1)
    target_chapter_file_sha256: Sha256
    directives: list[Stage5ReviewerRewriteDirectiveInput] = Field(min_length=1)

    @model_validator(mode='after')
    def _unique_directives(self) -> 'Stage5ReviewerRewriteTarget':
        keys = [(row.code, row.current_text_evidence) for row in self.directives]
        if len(keys) != len(set(keys)):
            raise ValueError('reviewer rewrite directive 的 code/evidence 不得重复')
        return self


class Stage5ReviewerRewriteRequest(StrictModel):
    """完整 frontier 上由 Commander 选择的非空目标子集；每章有独立指令与 CAS。"""

    actor: str = Field(min_length=1)
    reason: str = Field(min_length=1)
    review_sha256: Sha256
    unresolved_trim_items: list[Stage5TrimWorkItem] = Field(min_length=1)
    targets: list[Stage5ReviewerRewriteTarget] = Field(min_length=1)

    @model_validator(mode='after')
    def _closed_rewrite_request(self) -> 'Stage5ReviewerRewriteRequest':
        if not self.actor.strip() or not self.reason.strip():
            raise ValueError('reviewer rewrite actor/reason 不能只含空白')
        ordinals = [row.output_ordinal for row in self.unresolved_trim_items]
        targets = [row.target_ordinal for row in self.targets]
        if ordinals != sorted(set(ordinals)):
            raise ValueError('reviewer rewrite frontier 必须按章升序、去重')
        if targets != sorted(set(targets)) or not set(targets) <= set(ordinals):
            raise ValueError('reviewer rewrite targets 必须是完整 frontier 的非空有序子集')
        return self


class Stage5TrimSubject(Stage5TrimWorkItem):
    """闭合工作包里一条 current trim audit 及其可选当前销账回执。"""

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


class AnalyzeDraftEvidenceCandidate(StrictModel):
    evidence_candidate_id: str = Field(pattern=r'^e\d{6}$')
    chapter_ordinal: int = Field(ge=1)
    excerpt: str = Field(min_length=1)


class AnalyzeDraftView(StrictModel):
    """原联合候选的只读工作包；语法可读不代表能编译或已获审阅接受。"""

    sid: str
    owner: DraftOwner
    source_state: DraftSourceState
    template: AnalyzeOwnerDraftEdit
    diagnostics: list[str]
    evidence_candidates: list[AnalyzeDraftEvidenceCandidate]

    @model_validator(mode='after')
    def _owned_template(self):
        if self.owner != self.template.owner:
            raise ValueError('草稿工作包与编辑模板必须属于同一 owner')
        return self


class AnalyzeDraftEditRequest(StrictModel):
    actor: str = Field(min_length=1, max_length=256)
    reason: str = Field(min_length=1, max_length=4096)
    edit: AnalyzeOwnerDraftEdit

    @model_validator(mode='after')
    def _visible_author(self):
        if not self.actor.strip() or not self.reason.strip():
            raise ValueError('联合草稿订正必须有明确 actor 与 reason')
        return self


class CharactersEditRequest(StrictModel):
    actor: str = Field(min_length=1, max_length=256)
    reason: str = Field(min_length=1, max_length=4096)
    edit: CharactersEdit

    @model_validator(mode='after')
    def _visible_author(self) -> CharactersEditRequest:
        if not self.actor.strip() or not self.reason.strip():
            raise ValueError('人物订正必须有明确 actor 与 reason')
        return self


class GlobalEditPreview(StrictModel):
    """Global typed 编辑的独立只读预检结果；不是预授权。

    ``result_subject_sha256`` 是编辑后业务候选的 canonical 摘要，与 apply
    现场重判使用同一判据；``changed`` 为实际改动的业务路径；
    ``hard_hits`` 非空表示当前候选会被落盘闸门拦下并写入问题稿。
    """
    result_subject_sha256: Sha256
    changed: list[str]
    hard_hits: list[str]


class CharacterRegistryView(StrictModel):
    sid: str
    source_sha256: Sha256
    artifact: ArtifactState
    registry: CharacterRegistry
    evidence_snippets: dict[EvidenceId, str | None]
    edit_template: CharactersEdit | None
    template_unavailable_reason: str | None

    @model_validator(mode='after')
    def _template_state(self) -> CharacterRegistryView:
        if (self.edit_template is None) != (self.template_unavailable_reason is not None):
            raise ValueError('人物编辑模板缺失必须保留明确原因')
        if self.edit_template is not None and self.edit_template.source_sha256 != self.source_sha256:
            raise ValueError('人物编辑模板必须来自当前实际展示的同一字节主体')
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


class ApprovalBackfillResult(StrictModel):
    """按现有产物补录 CLI/队列路径的验收链；`backfilled` 必须在 UI 明示。"""

    approvals: ApprovalDoc
    backfilled: list[GateKey] = Field(default_factory=list)


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
# 二d、S-05 全局设定（`GET /api/novels/:sid/stage2/global`）
# ─────────────────────────────────────────────────────────────────────────────

class NameRef(FrozenModel):
    """一个「谁」的引用，view 层已解引用。

    `raw` 是产物里的原始串（`char_052` / `narration` / `public` / `rel_05` / `reader`），
    `display` 是人能读的那个名字。**解析不动时 `resolved=False` 且 `display == raw`**
    ——view 不编名字，编出来的名字比一个看得出来的 id 更坏（它看不出来）。
    """

    raw: str = Field(min_length=1)
    display: str = Field(min_length=1)
    resolved: bool


class GlobalRelationLine(GlobalTimeWindow):
    """有向长期关系；显示名只用于审阅 UI，不授予 Writer 身份披露。"""

    relation_id: str = Field(min_length=1)
    subject: NameRef
    predicate: str = Field(min_length=1)
    object: NameRef
    evidence_refs: list[EvidenceId]


class GlobalSecretLine(GlobalArcSecret[EvidenceId]):
    """保持 fact/fact_ref 的真实分支，不在视图中复制或猜测秘密事实。"""


class GlobalView(StrictModel):
    """四类长期约束的只读视图；人物登记通过独立 endpoint 提供。"""

    sid: str = Field(min_length=1)
    title: str = Field(min_length=1)
    setting: GlobalSetting[EvidenceId]
    relations: list[GlobalRelationLine]
    world_rules: list[GlobalWorldRule[EvidenceId]]
    arc_secrets: list[GlobalSecretLine]
    evidence_anchors: dict[EvidenceId, CoreEvidenceAnchor]
    evidence_snippets: dict[EvidenceId, str | None]
    unresolved_refs: int = Field(ge=0)
    invalid_exists: bool
    freshness: Freshness
    reason: FreshnessReason
    ghosts: list[str] = Field(default_factory=list)
    provenance: GlobalProvenance = Field(alias='_provenance')


# ─────────────────────────────────────────────────────────────────────────────
# 二d、S-11 开篇文案（`GET /api/novels/:sid/stage6`）
# ─────────────────────────────────────────────────────────────────────────────

# ─────────────────────────────────────────────────────────────────────────────
# 二e、S-12 人名替换 + 成书（`GET /api/novels/:sid/stage7[/:o]`）
# ─────────────────────────────────────────────────────────────────────────────

class NameDecisionLine(FrozenModel):
    """一个角色的改名决定（`stage7.NameDecision` 的视图行 + 现算 `hits`）。

    `hits` = 这个角色的全部字面（canonical + aliases 的 old）在 `applied`
    记账里的替换总次数。**记账是产地写的，这里只是按人分组求和**——读数不是判定。
    """

    char_id: str = Field(min_length=1)
    old_canonical: str = Field(min_length=1)
    new_canonical: str = Field(min_length=1)
    renamed: bool
    not_renamed_reason: str | None
    reason: str
    aliases: list[Alias]
    hits: int = Field(ge=0)


class RenameRow(FrozenModel):
    """逐章对照的一行（一段）。**`changed` 是 bool 不是 kind 字符串**——
    api.py 不许手抄取值面，而两态本来就该是 bool。"""

    l: str
    r: str
    changed: bool


class HookRename(FrozenModel):
    """开篇文案的替换前后。`before` = stage6 定稿字节。"""

    before: str = Field(min_length=1)
    after: str = Field(min_length=1)


class Stage7View(StrictModel):
    """`GET /api/novels/:sid/stage7` 与 `/stage7/:o`。**一个模型两问**：
    书级（`ordinal=null, rows=null`）与逐章对照（两者同非空）。

    ⚠ `rows` 用的是 **stage7 同一个替换器**（`novel/stages/stage7/subs.py`）对当前
    成稿现算——不写第二个替换器，也不落盘。
    ⚠ 判据表不在这里——结论只能来自 `/check/stage7`（`R-11.4`）。
    """

    sid: str = Field(min_length=1)
    title: str = Field(min_length=1)

    surname_map: dict[str, str]
    characters: list[NameDecisionLine] = Field(min_length=1)
    extra_subs: list[ExtraSub]
    subs_count: int = Field(ge=0)
    kept_count: int = Field(ge=0)
    #: **顺序是承重的**（照产物：`confidence` 高在前、覆盖率降序）。
    residual: list[Residual]
    baselines: dict[str, StemBaseline]
    approved: ApprovedNameMap | None
    #: Canonical SHA-256 of surname_map/characters/extra_subs; approval optimistic lock.
    approval_subject_sha256: str = Field(pattern=r'^[0-9a-f]{64}$')
    book: list[Finding]
    identity: IdentityBaseline | None
    book_txt: BookTxt | None
    #: 成书此刻真的在不在盘上（`book_txt` 记的是组装时的账，两者可分家）。
    book_txt_on_disk: bool
    #: 共用确定性交付终验；文件存在不能代替可下载证明。
    delivery: AgentDeliveryOutcome
    #: 成书的文件名（`<书名>_rewriting.txt`）。**下载时另存成什么名字用它，
    #: 别在前端拿 `title` 再拼一次**——那就是同一条命名规则的第二个落点，
    #: 而它与 `novel/paths.py::book_txt_rel` 分家时盘上那份和下载那份会叫两个名字。
    #: （`R-11.4` / `docs/LESSONS.md` 第 13 条：前端只呈现判定，不补后端没给的结论。）
    book_filename: str = Field(min_length=1)
    sources: NameMapSources

    #: 可切换「逐章对照」的输出章号（= 本层判过的成稿章，升序）。
    chapters: list[int]
    ordinal: int | None
    rows: list[RenameRow] | None
    hook: HookRename | None

    invalid_exists: bool
    freshness: Freshness
    reason: FreshnessReason
    ghosts: list[str] = Field(default_factory=list)

    provenance: NameMapProvenance = Field(alias='_provenance')

    @model_validator(mode='after')
    def _chapter_block_is_all_or_nothing(self) -> Stage7View:
        """书级 / 逐章是**一问一答**：`ordinal` 与 `rows` 同进同出。"""
        if (self.ordinal is None) != (self.rows is None):
            raise ValueError(
                f'ordinal={self.ordinal!r} 而 rows={"有" if self.rows is not None else "null"}'
                f'——逐章对照要么整块在（问了 :o），要么整块不在（书级）')
        if self.ordinal is not None and self.ordinal not in self.chapters:
            raise ValueError(f'ordinal={self.ordinal} 不在 chapters={self.chapters} 里')
        return self


# ─────────────────────────────────────────────────────────────────────────────
# 二f、S-04 原文↔骨架（`GET /api/novels/:sid/reading/:o`）
# ─────────────────────────────────────────────────────────────────────────────



class ReadingView(StrictModel):
    """原文与完整剧情同快照对照；证据定位不冒充语义覆盖。"""

    sid: str = Field(min_length=1)
    title: str = Field(min_length=1)
    chapter_ordinal: int = Field(ge=1)
    text: str = Field(min_length=1)
    text_chars: int = Field(ge=1)
    blueprint: PlotBlueprint | None
    blueprint_state: ArtifactState
    provenance: BlueprintProvenance | None = Field(alias='_provenance')

    @model_validator(mode='after')
    def _source_chapter(self) -> ReadingView:
        if self.text_chars != len(self.text):
            raise ValueError('原文字数必须来自当前实际文本')
        if self.blueprint is not None and self.blueprint.chapter_ordinal != self.chapter_ordinal:
            raise ValueError('阅读原文和剧情蓝图必须同章')
        if self.blueprint is None and self.provenance is not None:
            raise ValueError('缺少蓝图时不能下发半份来源账')
        return self


# ─────────────────────────────────────────────────────────────────────────
# 二f、Stage2 Blueprint 只读阅读工作包（审阅已退役；查看与证据回源仍共用）
# ──────────────────────────────────────────────────────────────────────────



class BlueprintReviewBinding(FrozenModel):
    """工作包的精确输入身份；摘要均来自同一次受校验的字节读取。"""

    blueprint_rel: str = Field(min_length=1)
    subject_kind: Literal[BLUEPRINT_REVIEW_SUBJECT_KINDS]
    main_text_artifact_sha256: str = Field(pattern=r'^[0-9a-f]{64}$')
    blueprint_artifact_sha256: str = Field(pattern=r'^[0-9a-f]{64}$')
    check_report_sha256: str = Field(pattern=r'^[0-9a-f]{64}$')
    chapter_ordinals: list[int] = Field(min_length=1)

    @model_validator(mode='after')
    def _chapters_are_a_closed_ordered_set(self) -> 'BlueprintReviewBinding':
        if self.chapter_ordinals != sorted(set(self.chapter_ordinals)):
            raise ValueError('Blueprint review binding 章号必须升序且去重')
        return self


class BlueprintReadingWorkpack(FrozenModel):
    """Blueprint 范围阅读材料：原文与完整节点各一份；不携带审阅或接受语义。

    Stage2 语义审阅退役后，这份只读工作包仅服务原文↔蓝图对照阅读与证据回源；
    机械可用性由统一准入（``/admission``）与 ``/check/stage2`` 报告分别披露。
    """

    sid: str = Field(min_length=1)
    title: str = Field(min_length=1)
    read_only: Literal[True] = True
    scope: ReviewScope
    review_identity_sha256: Sha256
    subject_sha256: Sha256
    binding_sha256: Sha256
    binding: BlueprintReviewBinding
    material: dict[str, Any]
    progress: BlueprintRunProgress
    check: CompactApprovalCheck
    accounting: list[AccountingLine]

    @model_validator(mode='after')
    def _binding(self) -> 'BlueprintReadingWorkpack':
        if self.scope != self.material.get('scope'):
            raise ValueError('Blueprint 范围和实际材料不一致')
        return self


# ─────────────────────────────────────────────────────────────────────────────
# 二g、S-07 计划 ↔ 写作上下文（`GET /api/novels/:sid/stage4/:o`）
# ─────────────────────────────────────────────────────────────────────────────

class PlanSceneLine(PlanScene):
    """只读场景视图；节点列表由 placement 派生，不作为编辑接口。"""
    i: int = Field(ge=1)
    beat_refs: list[str] = Field(min_length=1)


class StructureDecisionView(StructureDecision):
    """持久化人工声明 + 当前算法从实际计划现场复算的只读向量。"""

    computed_change_kinds: list[Literal[STRUCTURE_CHANGE_KINDS]]


class PlanChapterLine(PlanChapter):
    """正式计划加同源阅读投影；映射只由 placement 拥有。"""
    structure_decision: StructureDecisionView
    semantic_contract: SemanticContract
    scenes: list[PlanSceneLine] = Field(min_length=1)

    @model_validator(mode='after')
    def _derived_membership(self) -> PlanChapterLine:
        for scene in self.scenes:
            expected = [p.beat_ref for p in self.beat_placements
                        if any(d.scene_ref == scene.slot for d in p.deliveries)]
            if scene.beat_refs != expected:
                raise ValueError('只读节点列表必须由 placement 派生')
        return self


class Stage4IndexRow(FrozenModel):
    """章索引一行。⚠ **没有 `in_band` / `style_hits`**——那是判据结论，
    唯一来源是 `/check/stage4`（`R-11.4`），前端要并排显示就去 join 那份报告。"""

    output_ordinal: int = Field(ge=1)
    #: 章级阅读/写作导航的主要目标。来自同一份 `plan_chapters.json`，随索引一次下发，
    #: 不让前端为了 N 章再发 N 次章级请求。
    chapter_goal: str = Field(min_length=1)
    structure_decision: StructureDecisionView
    budget_chars: int | None
    scenes: int = Field(ge=1)


class CtxChapterView(CtxChapter):
    """任务书的读取视图；展示读数现算，不能序列化为 WriterContext 产物。"""

    from_source_ordinals: list[int] = Field(min_length=1, max_length=1)
    budget_chars: int = Field(gt=0)
    #: Stage1 缺失或不可读时保持未知；不能从旧任务书抄一份数字兜底。
    source_chars: int | None = Field(ge=0)


class Stage4View(StrictModel):
    """`GET /api/novels/:sid/stage4/:o`。**S-07：计划决定了什么？写手实际拿到什么？**

    ⚠ **没跑过 stage4 时本屏也有内容**（`04_delivery.md:133`）：`identity_plan=true`，
    计划与上下文都是**本次现算的恒等基线**（`identity_plan` + `build_writer_context`
    同一对纯函数，不落盘）——「如果计划什么都不改，写手手里会有什么」。
    ⚠ 计划在而 ctx 不在 ⇒ 200 且 `ctx_chapter=null`（右半边缺席，`ctx_state` 说明处境）。
    """

    sid: str = Field(min_length=1)
    title: str = Field(min_length=1)

    identity_plan: bool
    #: 这一屏 `index` / `plan_chapter` 所来自的**同一份正式计划**的结构主体。
    #: 恒等基线不是盘上可审批对象，因此只能是 `null`；正式计划则必须给完整 SHA-256，
    #: 供前端与审批服务的 `structure.current_subject_sha256` 关闭式对齐。
    structure_subject_sha256: str | None = Field(pattern=r'^[0-9a-f]{64}$')
    structure_subject_unavailable_reason: str | None
    blueprint: PlotBlueprint
    chapters: list[int] = Field(min_length=1)
    ordinal: int = Field(ge=1)
    index: list[Stage4IndexRow] = Field(min_length=1)

    plan_chapter: PlanChapterLine
    ctx_chapter: CtxChapterView | None

    #: 写手被要求的篇幅区间，**与写手同一把尺子**（`prompts.target_band`）。
    #: `null` ⇔ `ctx_chapter=null`（没有任务书就没有目标）。
    #:
    #: ⚠ 此处**没有** `writer_target_estimated`：那个布尔曾用于区分「按 `budget_chars`
    #: 算的实数带」与「`budget_chars` 缺席时按 `source_chars` 估的带」，而
    #: `CtxScene.budget_chars` 是 `Field(gt=0)` 必填、总预算只由它求和，
    #: 估算那条路已随 `08240cf` 一并退役 ⇒ 它只会是 `false`。
    #: **一个永远不会为真的布尔比没有这个字段更坏**：它让下一个人以为这里真能区分两者。
    writer_target: tuple[int, int] | None

    plan_state: ArtifactState
    ctx_state: ArtifactState

    plan_provenance: PlanProvenance | None = Field(alias='_provenance')
    ctx_provenance: CtxProvenance | None = Field(alias='_ctx_provenance')

    @model_validator(mode='after')
    def _postconditions(self) -> Stage4View:
        if self.ordinal not in self.chapters:
            raise ValueError(f'ordinal={self.ordinal} 不在 chapters={self.chapters} 里')
        if self.identity_plan and self.plan_provenance is not None:
            raise ValueError('恒等基线是现算的，没有落盘来源账可发')
        if self.identity_plan and self.structure_subject_sha256 is not None:
            raise ValueError('参考基线不能拥有正式审阅身份')
        if self.structure_subject_sha256 is None and not self.structure_subject_unavailable_reason:
            raise ValueError('审阅主体不可用时必须说明原因')
        if self.structure_subject_sha256 is not None and self.structure_subject_unavailable_reason:
            raise ValueError('审阅主体可用时不能同时声称不可用')
        if (self.writer_target is None) != (self.ctx_chapter is None):
            raise ValueError('writer_target 与 ctx_chapter 必须同进同出（同一份任务书）')
        return self


# ─────────────────────────────────────────────────────────────────────────────
# 二h、S-08 成稿 ↔ 材料（`GET /api/novels/:sid/stage5/:o`）
# ─────────────────────────────────────────────────────────────────────────────

class MaterialView(StrictModel):
    """写手拿到的下发面。**与写手同一份**（`prompts.build_material(...).blocks`，
    `R-11.8` 在读屏上的对应物：显示的材料 = 真发出去的材料，不是重新组装的第二份）。

    按唯一闭卷写作契约组装；源章原文只在本屏的 `source_text` 有落点，
    不是发给写手的 material block。
    """

    blocks: list[str] = Field(min_length=1)
    chars: int = Field(ge=0)
    #: ⚠ 同 `Stage4View.writer_target`：不再有 `writer_target_estimated`，理由见那一格。
    writer_target: tuple[int, int]


class DraftAlignRow(FrozenModel):
    """原文↔成稿的一个对齐块。`anchored=false` = 本块并入了无锚的拍
    （分界线只由双侧都命中的 verbatim 定，无锚的拍并进相邻块，不编分界线）。"""

    beats: list[int] = Field(min_length=1)
    l: str
    r: str
    anchored: bool


class DraftAlignView(StrictModel):
    """对齐的账。**这是对应关系，不是判定**（`note` 常驻这句话）。
    锚点覆盖率必须常驻显示——没有它，「这一行盖了 3 拍」看起来和
    「这一行就是第 3 拍」一模一样。"""

    beats: int = Field(ge=0)
    anchors: int = Field(ge=0)
    rows: int = Field(ge=0)
    inversions: int = Field(ge=0)
    coverage: float = Field(ge=0, le=1)
    coverage_threshold: float = Field(gt=0, le=1)
    coverage_state: AnchorCoverageState
    note: str = Field(min_length=1)

    @model_validator(mode='after')
    def _coverage_state_is_from_the_server_threshold(self) -> DraftAlignView:
        if self.coverage_threshold != ANCHOR_COVERAGE_WARN:
            raise ValueError('coverage_threshold 必须来自 ANCHOR_COVERAGE_WARN')
        want = 'low' if self.coverage < self.coverage_threshold else 'adequate'
        if self.coverage_state != want:
            raise ValueError(
                f'coverage_state={self.coverage_state!r}，按 coverage/threshold 应为 {want!r}')
        return self


class DraftView(StrictModel):
    """一份成稿卡。正文只发 producer-owned 的 typed 槽位。"""

    chars: int = Field(ge=0)
    budget_chars: int | None
    overlap_excl_verbatim: float | None
    #: 盘上 schema v2 的唯一内容权威；整章正文由消费者确定性投影。
    segments: list[ChapterSegment] = Field(min_length=1)

    #: `null` = 对齐辅助尚未由服务端提供（本章无锚可对时 rows 为空、账仍在）。
    align: DraftAlignView | None
    align_rows: list[DraftAlignRow]

    freshness: Freshness
    reason: FreshnessReason

    provenance: RoundLlmProvenance | RoundFreeProvenance = Field(
        alias='_provenance', union_mode='left_to_right')


class Stage5IndexRow(FrozenModel):
    """章索引一行。`chars` / `overlap` / `freshness` 取 canonical 稿；
    没有成稿的章三格全 `null`。"""

    output_ordinal: int = Field(ge=1)
    budget_chars: int | None
    has_draft: bool
    invalid: bool
    chars: int | None
    overlap_excl_verbatim: float | None
    freshness: Freshness | None


class Stage5TrimAuditView(FrozenModel):
    """当前章仍待人工销账的超上限事实。

    这里只投影人做决定所需的事实与现算上下文；hash 绑定由专用
    ``retain-pretrim`` 路由在执行时重新从盘上读取，浏览器不代抄锁。
    """

    output_ordinal: int = Field(ge=1)
    reason: str = Field(min_length=1)
    context: str = Field(min_length=1)


class Stage5View(StrictModel):
    """`GET /api/novels/:sid/stage5/:o`。**S-08：这一章写出来了吗？守住材料了吗？**

    ⚠ `ctx_exists=false` ⇒ `materials=null`（**不显示假材料**，`04_delivery.md:134`）
    ——给「先跑 stage4」，左半边缺席、右半边照常列已有成稿。
    ⚠ 某章无 canonical 稿 ⇒ 200 且 `drafts=[]`（「还没写」不是错误）。
    """

    sid: str = Field(min_length=1)
    title: str = Field(min_length=1)

    ctx_exists: bool
    #: `null` = 计划读不回来，「该有几章」判不了（与 0 是两件事）。
    planned: int | None
    written: int = Field(ge=0)
    invalid: int = Field(ge=0)
    missing: list[int]
    legacy_incompatible: bool = False
    legacy_paths: list[str] = Field(default_factory=list)

    chapters: list[int] = Field(min_length=1)
    ordinal: int = Field(ge=1)
    index: list[Stage5IndexRow]

    source_text: str | None
    source_chars_raw: int | None
    materials: MaterialView | None
    drafts: list[DraftView]
    invalid_drafts: list[DraftView]
    #: ``null`` = 本章没有一份 current、未销账的 TrimAudit。
    trim_audit: Stage5TrimAuditView | None = None

    ctx_state: ArtifactState

    @model_validator(mode='after')
    def _postconditions(self) -> Stage5View:
        if self.ordinal not in self.chapters:
            raise ValueError(f'ordinal={self.ordinal} 不在 chapters={self.chapters} 里')
        if not self.ctx_exists and self.materials is not None:
            raise ValueError('ctx 不在盘上而 materials 有内容——假材料（`04_delivery.md:134`）')
        if self.legacy_incompatible != bool(self.legacy_paths):
            raise ValueError('legacy_incompatible 与 legacy_paths 必须同时出现或缺席')
        return self


# ─────────────────────────────────────────────────────────────────────────────
# 二i、S-09 验收 / 修订（`GET /api/novels/:sid/review/:o`）
# ─────────────────────────────────────────────────────────────────────────────

class ReviewIndexRow(FrozenModel):
    """章索引一行。`finding_counts` 四 sev 键**恒在**（0 也写）——
    产物侧 `Review.counts` 是稀疏的（登记过的病），出线侧修掉。"""

    output_ordinal: int = Field(ge=1)
    finding_counts: dict[str, int]
    #: 有收敛账（`unresolved_rounds > 0`）且未豁免的结论条数。
    unresolved: int = Field(ge=0)


class ReviewChapterView(StrictModel):
    """一章的验收结论；正文与编辑槽位由并行 Stage5 响应提供。"""

    output_ordinal: int = Field(ge=1)
    findings: list[Finding]


class ReviewView(StrictModel):
    """`GET /api/novels/:sid/review/:o`。**S-09：最新一份全书验收 + 该章明细。**

    一份验收都没有 ⇒ 404（`UpstreamMissing`，前端渲染「先跑 stage5.review」）。
    ⚠ 判据表本身仍归 `/check/stage5`；本屏发的是**验收产物**（历史事实），
    与「现在复检一遍」是两件事。
    """

    sid: str = Field(min_length=1)
    title: str = Field(min_length=1)

    review_seq: int = Field(ge=1)
    review_state: Stage5ReviewState
    review_reasons: list[str] = Field(default_factory=list)
    #: 全书（chapters+book 全部结论）按 sev 现数，四键恒在。
    #: ⚠ 不读产物的 `counts` 字段——那份是稀疏 Counter（`Review.counts` 的登记病）。
    finding_counts: dict[str, int]
    book: list[Finding]

    chapters: list[int] = Field(min_length=1)
    ordinal: int = Field(ge=1)
    index: list[ReviewIndexRow] = Field(min_length=1)
    chapter: ReviewChapterView

    @model_validator(mode='after')
    def _postconditions(self) -> ReviewView:
        if self.ordinal not in self.chapters:
            raise ValueError(f'ordinal={self.ordinal} 不在 chapters={self.chapters} 里')
        want = set(SEVERITY)
        if set(self.finding_counts) != want:
            raise ValueError(f'finding_counts 必须四 sev 键恒在（0 也写），实得 '
                             f'{sorted(self.finding_counts)}')
        for row in self.index:
            if set(row.finding_counts) != want:
                raise ValueError(f'ch{row.output_ordinal} 的 finding_counts 键不全')
        if self.review_state == 'missing':
            raise ValueError('missing Review 应由路由返回 404，不得伪装成 ReviewView')
        if (self.review_state == 'fresh') != (not self.review_reasons):
            raise ValueError('Review fresh 当且仅当 review_reasons 为空')
        return self


# ─────────────────────────────────────────────────────────────────────────────
# 二j、人工写回的请求体（三条 0 成本路：改稿 / 文案定稿 / 人名定稿）
# ─────────────────────────────────────────────────────────────────────────────

class DraftEditRequest(StrictModel):
    """`POST /api/novels/:sid/review/:o/edit` —— 章节改稿。

    服务进程把 typed `segments` 落成 `_service/edits/` 下的一次性人工稿（文件名带 `ch{o}`，
    `stage5.write --apply-edit` 从文件名认章），CLI 子进程走**与生成稿同一个判定落点**
    （`run_apply_edit`：归一 → 判据 → 归档 → 写回，`origin=human` 记账）。

    正式 apply 必须同时绑定 preview 时读到的 current chapter 完整文件 SHA-256，
    以及由归一候选、当前判据指纹与全部 finding 共同构成的 preview subject SHA-256。
    ``formal`` 只接受没有新增 HARD 的候选；新增一般 HARD 必须显式确认
    ``working_draft``，而段落预算 HARD 在两种确认下都拒绝。
    """

    segments: list[ChapterSegment] = Field(min_length=1)
    actor: str = Field(min_length=1)
    reason: str = Field(min_length=1)
    current_chapter_sha256: str = Field(pattern=r'^[0-9a-f]{64}$')
    preview_subject_sha256: str = Field(pattern=r'^[0-9a-f]{64}$')
    confirmation: Literal[STAGE5_EDIT_CONFIRMATIONS]

    @model_validator(mode='after')
    def _segment_slots_are_unique(self) -> DraftEditRequest:
        if not self.actor.strip() or not self.reason.strip():
            raise ValueError('人工改稿 actor/reason 不得只有空白')
        slots = [segment.slot for segment in self.segments]
        if len(slots) != len(set(slots)):
            raise ValueError('人工稿 segment slot 不得重复')
        return self


class DraftEditPreviewRequest(StrictModel):
    """只读 Stage5 人工改稿预览；请求只含完整 producer-owned segments。"""

    segments: list[ChapterSegment] = Field(min_length=1)

    @model_validator(mode='after')
    def _segment_slots_are_unique(self) -> 'DraftEditPreviewRequest':
        slots = [segment.slot for segment in self.segments]
        if len(slots) != len(set(slots)):
            raise ValueError('人工稿 segment slot 不得重复')
        return self


class DraftEditPreview(FrozenModel):
    """与正式 apply 共用纯评估结果的 hash-bound 只读投影。"""

    output_ordinal: int = Field(ge=1)
    current_chapter_sha256: str = Field(pattern=r'^[0-9a-f]{64}$')
    preview_subject_sha256: str = Field(pattern=r'^[0-9a-f]{64}$')
    normalized_segments: list[ChapterSegment] = Field(min_length=1)
    findings: list[Finding]
    current_hard_findings: list[Finding]
    introduced_hard_findings: list[Finding]
    resolved_hard_findings: list[Finding]
    required_confirmation: Literal[STAGE5_EDIT_CONFIRMATIONS]


class TrimResolutionRequest(StrictModel):
    """Retain the pretrim text for the current hash-bound trim audit.

    ``actor`` is self-reported and is not an authenticated identity.  The
    action is fixed by the endpoint and cannot be used to adopt a candidate.
    """

    actor: str = Field(min_length=1)
    reason: str = Field(min_length=1)

    @model_validator(mode='after')
    def _audit_text_is_not_blank(self) -> 'TrimResolutionRequest':
        if not self.actor.strip() or not self.reason.strip():
            raise ValueError('actor/reason 不得只有空白')
        return self


class NameMapApproveRequest(StrictModel):
    """Stage7 human approval bound to one exact immutable workpack."""

    actor: str = Field(min_length=1)
    reason: str = Field(min_length=1)
    review_token: str = Field(pattern=r'^s7r3_[0-9a-f]{64}$')

    @model_validator(mode='after')
    def _audit_text_is_not_blank(self) -> 'NameMapApproveRequest':
        if not self.actor.strip() or not self.reason.strip():
            raise ValueError('actor/reason 不得只有空白')
        return self

class ResidualAdoptionRequest(StrictModel):
    """Explicit, zero-cost adoption; low-confidence residuals are never accepted."""

    confidence: Literal[RESIDUAL_ADOPTION_CONFIDENCE]


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
