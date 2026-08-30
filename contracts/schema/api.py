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

import re
from typing import Annotated, Literal, TypeAlias

from pydantic import Field, field_validator, model_validator

from contracts.invariants import (
    ADDRESS_CONTEXT,
    AGENT_EXECUTOR_NODES,
    AgentAvailability,
    AgentDriver,
    AgentExecutionMode,
    AgentReasoningEffort,
    AgentRole,
    ANCHOR_COVERAGE_STATE,
    ANCHOR_COVERAGE_WARN,
    APPROVAL_GATES,
    APPROVAL_SEEN,
    BLUEPRINT_REVIEW_SUBJECT_KINDS,
    CHAPTER_LABEL_ANOMALY,
    DELIVERY_MODES,
    DESIGN_CHAPTER_DECISIONS,
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
    DESIGN_REVIEWER_KINDS,
    DESIGN_REVIEW_STATES,
    DESIGN_STRATEGY_DECISIONS,
    FACT_ATOM_KINDS,
    FACT_ATOM_OBLIGATIONS,
    GLOBAL_REVIEW_EFFECTS,
    HINGE_KINDS,
    HOOK_APPROACHES,
    STAGE6_JUDGE_ROLES,
    HOOK_SLOTS,
    slot_of,
    HOOK_SEMANTIC_STATES,
    INBOX_ATTENTION,
    INBOX_DELIVERY_EFFECT,
    INBOX_KIND,
    NODES,
    EXECUTION_PERMIT_KINDS,
    OPERATOR_NEXT_STATES,
    RESUME_VERDICTS,
    KNOWLEDGE_SUBJECT_KINDS,
    NAME_DISTINCTIVENESS,
    NARRATIVE_PERSON,
    NONBODY_KINDS,
    REMOVED_ORIGIN,
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
    STAGE5_CLOSURE_GROUPS, STAGE5_CLOSURE_STATES,
    STAGE5_EDIT_CONFIRMATIONS,
    STAGE5_MUST_APPEAR_RULINGS,)
from contracts.schema.base import FrozenModel, StrictModel
from contracts.schema.common import Finding, HashBoundHumanApproval, Sha256
from contracts.schema.evaluation import GlobalReviewItem, SemanticDecision
from contracts.schema.orchestration import (
    AgentProfile, AgentRunRecord, ArtifactCondition, ArtifactState, Freshness,
    FreshnessReason, JobRecord, JobState, NovelOverview, PipelineAgentProfiles,
    QueueRun,
)
from contracts.schema.stage0 import Stage0RevisionOrigin, Stage0Warning
from contracts.schema.stage2 import (
    BlueprintProvenance, ConflictStage, FactEvidence, GlobalProvenance,
    RelationVisibility, Setting, WorldRule,
)
from contracts.schema.stage4 import (
    BindingConcern, BindingInfeasibilityWitness,
    BlindCandidateChapter, BlindChapterWeightResponse,
    CtxChapter, CtxProvenance, PlanGoal, PlanProvenance, SemanticContract,
    StructureDecision,
)
from contracts.schema.stage5 import (
    ChapterSegment, JudgedDraft, RoundFreeProvenance, RoundLlmProvenance,
)
from contracts.schema.stage6 import (
    HookApprovalResolution,
    HookCandidateReview,
    HookContradiction,
    HookProvenance,
    HookQualityScores,
    HookSources,
    Mech,
)
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
SeenKey = Literal[APPROVAL_SEEN]
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
HookSemanticState = Literal[HOOK_SEMANTIC_STATES]
# 退役码仍在 Literal 里：盘上历史决定要能解码；新决定在 approve 闸关闭式拒绝。
DesignRepairCode = Literal[(*DESIGN_REPAIR_CODES, *DESIGN_REPAIR_CODES_RETIRED)]
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
    sid: str = Field(min_length=1)
    job: JobRecord


class TrashNovelRequest(StrictModel):
    """删除确认绑定当前书名；sid 由路径参数单独绑定。"""

    expected_title: str = Field(min_length=1)


class TrashNovelResult(StrictModel):
    """项目已移出语料库，但仍可从 `_service/trash/` 恢复。"""

    sid: str = Field(min_length=1)
    title: str = Field(min_length=1)
    trash_id: str = Field(min_length=1)
    trash_rel: str = Field(min_length=1)


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

    #: 直接节点作业只需要 executor。``None`` 为旧客户端兼容面；真正启动前由
    #: builder 按服务端 registry 解析默认并把 resolved profile 留档。
    executor_profile: AgentProfile | None = None

    #: 正文外发授权与付费确认相互独立。请求层默认未授权；无 profile 的 legacy
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
    """`POST /api/agent/runs/preview` 的响应。与真跑同一构造函数（`R-11.8` 同构）。"""

    argv: list[str] = Field(min_length=1)
    prompt: str = Field(min_length=1)
    authorized: bool
    egress_authorized: bool

    #: 服务端关闭式校验并物化 execution mode 后的完整选择。当前默认仍为 ``null``，
    #: 因此成功 preview 必然有这份快照；不能拿 capability 数组第一项替用户猜。
    profiles: PipelineAgentProfiles


class AgentModelCapability(FrozenModel):
    """一个稳定 model id 的可选 effort / execution mode。

    ``id`` 进入 argv 与审计账；``label`` 只负责界面显示。二者分开，避免改文案
    破坏重放，也避免把可升级的供应商别名当成稳定模型身份。
    """

    id: str = Field(min_length=1)
    label: str = Field(min_length=1)
    reasoning_efforts: list[AgentReasoningEffort] = Field(min_length=1)
    execution_modes: list[AgentExecutionMode] = Field(default_factory=list)

    @model_validator(mode='after')
    def _options_are_unique(self) -> AgentModelCapability:
        if len(self.reasoning_efforts) != len(set(self.reasoning_efforts)):
            raise ValueError('reasoning_efforts 不得重复')
        if len(self.execution_modes) != len(set(self.execution_modes)):
            raise ValueError('execution_modes 不得重复')
        return self


class AgentDriverCapability(FrozenModel):
    """一个本机 CLI driver 的静态能力与当次只读探针结果。"""

    id: AgentDriver
    #: 只作能力元数据；真正选择仍以 ``AgentProfile.driver`` 为唯一键。
    provider: str = Field(min_length=1)
    label: str = Field(min_length=1)
    roles: list[AgentRole] = Field(min_length=1)
    availability: AgentAvailability

    #: 人读探针说明。``ready`` 也会明确写出「只验 CLI/版本，未发模型请求」。
    availability_reason: str = Field(min_length=1)

    #: ``--version`` 成功时的首个非空输出行；缺失或无法探测时为 ``None``。
    version: str | None = Field(default=None, min_length=1)
    models: list[AgentModelCapability] = Field(min_length=1)

    @model_validator(mode='after')
    def _domains_are_unique(self) -> AgentDriverCapability:
        if len(self.roles) != len(set(self.roles)):
            raise ValueError('roles 不得重复')
        ids = [model.id for model in self.models]
        if len(ids) != len(set(ids)):
            raise ValueError(f'driver={self.id!r} 的 model id 不得重复')
        if self.availability == 'missing' and self.version is not None:
            raise ValueError('CLI=missing 时不得声称探测到 version')
        return self


class AgentCapabilitiesResponse(StrictModel):
    """``GET /api/agent/capabilities``：前端 agent 选择器的唯一数据源。"""

    drivers: list[AgentDriverCapability] = Field(min_length=1)
    executor_nodes: list[str] = Field(min_length=1)

    #: 服务端已冻结的双角色默认；``None`` 明确表示迁移期尚无 provider-neutral
    #: 默认，调用方必须显式选择，不能拿 capability 数组第一项猜。Phase7 切换唯一
    #: agent 默认时才会变为非空。
    default_profiles: PipelineAgentProfiles | None = None

    @model_validator(mode='after')
    def _drivers_are_unique(self) -> AgentCapabilitiesResponse:
        ids = [driver.id for driver in self.drivers]
        if len(ids) != len(set(ids)):
            raise ValueError('driver id 不得重复')
        if self.executor_nodes != list(AGENT_EXECUTOR_NODES):
            raise ValueError(
                'executor_nodes 必须按 AGENT_EXECUTOR_NODES 的完整顺序发布，'
                '不能让前端再维护一份节点清单')
        return self


class AgentRunList(StrictModel):
    """`GET /api/agent/runs?sid=`。新的在前（与 `JobList` 同款约定）。"""

    runs: list[AgentRunRecord] = Field(default_factory=list)


class ApprovalAction(FrozenModel):
    kind: str = Field(min_length=1)
    label: str = Field(min_length=1)
    extra: list[str] | None = None


class DesignReviewBasis(FrozenModel):
    """Stage4A 人审的全局失效域；任一格变化都会令策略与全部章过期。"""

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


class DesignBlindFact(FrozenModel):
    """A 相真正可见的事实原子；只含 opaque alias 与可读语义。"""

    fact_alias: str = Field(pattern=r'^a_[0-9a-f]{16,64}$')
    claim: str = Field(min_length=1)
    kind: Literal[FACT_ATOM_KINDS]
    obligation: Literal[FACT_ATOM_OBLIGATIONS]
    participant_aliases: list[str]

    @field_validator('participant_aliases')
    @classmethod
    def _participants_are_canonical(cls, values: list[str]) -> list[str]:
        if values != sorted(set(values)):
            raise ValueError('blind truth participant_aliases 必须排序且不重复')
        if any(not re.fullmatch(r'a_[0-9a-f]{16,64}', value) for value in values):
            raise ValueError('blind truth participant_aliases 必须是 opaque alias')
        return values


class DesignBlindCausalEdge(FrozenModel):
    before_alias: str = Field(pattern=r'^a_[0-9a-f]{16,64}$')
    after_alias: str = Field(pattern=r'^a_[0-9a-f]{16,64}$')


class DesignBlindChapterTruth(FrozenModel):
    """由当前 Stage2 blueprint 现场重建的 A 可见去序真相章。"""

    chapter_ordinal: int = Field(ge=1)
    chapter_function: str = Field(min_length=1)
    facts: list[DesignBlindFact] = Field(min_length=1)
    causal_edges: list[DesignBlindCausalEdge]
    entry_state_aliases: list[str]
    ending_state_aliases: list[str]

    @model_validator(mode='after')
    def _blind_truth_is_closed(self) -> DesignBlindChapterTruth:
        facts = [row.fact_alias for row in self.facts]
        if len(facts) != len(set(facts)):
            raise ValueError('blind truth fact_alias 不得重复')
        fact_set = set(facts)
        edges = [(row.before_alias, row.after_alias) for row in self.causal_edges]
        if edges != sorted(set(edges)):
            raise ValueError('blind truth causal_edges 必须排序且不重复')
        referenced = {
            alias for edge in self.causal_edges
            for alias in (edge.before_alias, edge.after_alias)
        } | set(self.entry_state_aliases) | set(self.ending_state_aliases)
        if not referenced.issubset(fact_set):
            raise ValueError('blind truth 因果/状态 alias 必须属于本章 facts')
        for label, values in (
            ('entry_state_aliases', self.entry_state_aliases),
            ('ending_state_aliases', self.ending_state_aliases),
        ):
            if values != sorted(set(values)):
                raise ValueError(f'blind truth {label} 必须排序且不重复')
        return self


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

    strategy_subject_sha256: str = Field(pattern=r'^[0-9a-f]{64}$')
    blind_truth: DesignBlindChapterTruth
    chapter: BlindCandidateChapter
    structure_evidence: DesignStructureEvidence

    @model_validator(mode='after')
    def _ordinals_match(self) -> DesignChapterSubject:
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

    绑定 candidate / blueprint / review contract 三个 SHA；`failures` 只来自
    构造性绑定（每场 catalog 首项舞台、材料全落首场）跑同一联合 HARD——
    「B 选得不好」在这个预检里结构性不存在。
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
    request_repair_available: bool


class DesignReviewRepairCapability(FrozenModel):
    """当前候选的 split-checkpoint targeted repair 可执行面。"""

    repairable: bool
    reason_code: str = Field(min_length=1)
    chapters: list[DesignChapterRepairCapability] = Field(min_length=1)

    @model_validator(mode='after')
    def _chapter_availability_is_derived(self) -> DesignReviewRepairCapability:
        ordinals = [row.output_ordinal for row in self.chapters]
        if ordinals != sorted(set(ordinals)):
            raise ValueError('design repair capability 章号必须升序且不重复')
        for row in self.chapters:
            expected = self.repairable and row.committed_rounds < 2
            if row.request_repair_available != expected:
                raise ValueError(
                    'request_repair_available 必须由 lineage 与 committed_rounds 推导')
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
    source_decision_sha256: str | None = Field(
        default=None, pattern=r'^[0-9a-f]{64}$')

    @model_validator(mode='after')
    def _decision_has_a_canonical_scope(self) -> DesignChapterReview:
        if self.placeholder != (self.template_token is not None):
            raise ValueError('Stage4A 逐章 placeholder 与 template_token 必须同进同出')
        if not self.reason.strip():
            raise ValueError('Stage4A 逐章裁决理由不能只含空白')
        fingerprints = [
            (row.code, tuple(row.scene_refs), tuple(row.opaque_aliases))
            for row in self.constraints
        ]
        if len(fingerprints) != len(set(fingerprints)):
            raise ValueError('design chapter constraints 不得包含完全重复的约束')
        if self.decision == 'accept' and self.constraints:
            raise ValueError('accept 章不得夹带 repair scope')
        if self.decision == 'request_repair' and not self.constraints:
            raise ValueError('request_repair 章必须给出至少一条 constraint')
        if self.output_ordinal != self.subject.chapter.output_ordinal:
            raise ValueError('design chapter 章号必须与 subject.chapter 一致')
        return self


class DesignReviewRequest(StrictModel):
    """对当前完整 Stage4A 候选作出的原子全书盲审提交。"""

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
    def _submission_is_a_complete_book(self) -> DesignReviewRequest:
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
        if any(
            row.subject.strategy_subject_sha256 != self.strategy.subject_sha256
            for row in self.chapters
        ):
            raise ValueError('每章 subject 必须绑定本次策略 subject SHA-256')
        return self


class DesignReviewTemplate(DesignReviewRequest):
    """服务端按当前候选生成的关闭式模板；可直接编辑后作为 request 提交。"""


class DesignReviewDecision(DesignReviewRequest):
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
    current_decision: DesignReviewDecision | None = None
    history: list[DesignReviewDecision] = Field(default_factory=list)

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
            'accepted', 'repair_requested', 'strategy_replan_requested',
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
            if not self.history or self.current_decision != self.history[-1]:
                raise ValueError('design review current_decision 必须是 history 最后一项')
            wants_replan = self.current_decision.strategy.decision == 'request_replan'
            wants_repair = any(
                row.decision == 'request_repair'
                for row in self.current_decision.chapters)
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
        return self


class DesignRepairChapterScope(FrozenModel):
    output_ordinal: int = Field(ge=1)
    subject_sha256: str = Field(pattern=r'^[0-9a-f]{64}$')
    constraints: list[DesignRepairConstraint] = Field(min_length=1)


class DesignRepairScope(FrozenModel):
    """CLI/Core 只读消费的稳定恢复面；不暴露服务盘记录形状。"""

    candidate_sha256: str = Field(pattern=r'^[0-9a-f]{64}$')
    aggregate_subject_sha256: str = Field(pattern=r'^[0-9a-f]{64}$')
    decision_sha256: str = Field(pattern=r'^[0-9a-f]{64}$')
    binding_infeasibility: DesignBindingInfeasibility | None = None
    repair_capability: DesignReviewRepairCapability
    strategy_replan: bool
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
        available = {
            row.output_ordinal
            for row in self.repair_capability.chapters
            if row.request_repair_available
        }
        if not self.strategy_replan and not set(ordinals).issubset(available):
            raise ValueError('逐章 repair scope 包含当前不可执行 request_repair 的章')
        return self


class StructureChapterDecision(FrozenModel):
    """人对当前 Stage4 计划中一章作出的结构裁决。"""

    output_ordinal: int = Field(ge=1)
    decision: StructureApprovalDecisionKind
    reason: str = Field(min_length=1)

    @field_validator('reason')
    @classmethod
    def _reason_is_not_blank(cls, value: str) -> str:
        if not value.strip():
            raise ValueError('逐章结构裁决理由不能只含空白')
        return value


class StructureApprovalRequest(HashBoundHumanApproval):
    """一次原子的全书 Stage4 结构裁决；章集必须由服务端再与当前计划精确对账。"""

    chapters: list[StructureChapterDecision] = Field(min_length=1)

    @model_validator(mode='after')
    def _chapters_are_sorted_unique(self) -> StructureApprovalRequest:
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
        if self.state in ('accepted', 'replan_requested'):
            if self.current_decision is None:
                raise ValueError(f'{self.state} 结构态必须带当前 typed decision')
            if self.current_decision.subject_sha256 != self.current_subject_sha256:
                raise ValueError('当前结构裁决必须绑定当前 subject')
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

    @model_validator(mode='after')
    def _actions_and_history_are_closed(self) -> WorkpackItem:
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
    """exception-first Commander 工作包 v1 的公共外壳（批次 5）。

    只读 façade：绑定当前审阅 subject，不预填任何决定、不启动作业。
    """

    v: Literal[1] = 1
    sid: str = Field(min_length=1)
    kind: ApprovalWorkpackKind
    candidate_sha256: str | None = Field(
        default=None, pattern=r'^[0-9a-f]{64}$')
    subject_sha256: str = Field(pattern=r'^[0-9a-f]{64}$')
    chapter_ordinals: list[int] = Field(min_length=1)
    groups: list[WorkpackGroup] = Field(min_length=len(APPROVAL_WORKPACK_GROUPS))
    disposition: ApprovalWorkpackDisposition = 'review_required'
    current_decision_sha256: str | None = Field(
        default=None, pattern=r'^[0-9a-f]{64}$')

    @model_validator(mode='after')
    def _shape_is_closed_and_covers_every_chapter(self) -> ApprovalWorkpack:
        if ((self.disposition == 'already_current')
                != (self.current_decision_sha256 is not None)):
            raise ValueError(
                'already_current 与 current_decision_sha256 必须同时出现')
        if [row.group for row in self.groups] != list(APPROVAL_WORKPACK_GROUPS):
            raise ValueError(
                'workpack 分组必须是固定顺序的全集，不许裁剪或重排')
        if self.chapter_ordinals != sorted(set(self.chapter_ordinals)):
            raise ValueError('workpack chapter_ordinals 必须升序去重')
        if (self.kind == 'design') != (self.candidate_sha256 is not None):
            raise ValueError('design 必须绑 candidate SHA，structure 不得冒充 A 候选')
        expected_block = 'block_b' if self.kind == 'design' else 'block_context'
        blocking = self.groups[0].items
        if any(item.effect != expected_block for item in blocking):
            raise ValueError('blocking effect 必须与 A 挡 B / B 挡 Context 一致')
        if any(
            'request_repair' in item.allowed_actions
            for group in self.groups for item in group.items
        ) and self.kind != 'design':
            raise ValueError('request_repair 只属于 Stage4A 章级修订面')
        if any(
            'accept' in item.allowed_actions and item.effect != 'coverage'
            for group in self.groups for item in group.items
        ):
            raise ValueError('accept 只能出现在全章 coverage，不能被异常行预填')
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


class Stage6ApprovalCandidate(FrozenModel):
    """Stage6 终审的一条候选：文本、机械结论与 A/B 原始证据同包。"""

    index: int = Field(ge=1)
    slot: Literal[HOOK_SLOTS]
    approach: Literal[HOOK_APPROACHES]
    chars: int = Field(ge=0)
    text: str = Field(min_length=1)
    subject_sha256: str = Field(pattern=r'^[0-9a-f]{64}$')
    mech: Mech
    findings: list[Finding]
    semantic_state: HookSemanticState
    semantic_reason: str
    semantic_review_seq: int | None = Field(default=None, ge=1)
    semantic_review: HookCandidateReview | None = None
    semantic_decision: SemanticDecision | None = None
    quality_score: float | None = Field(default=None, ge=0, le=10)
    quality_rank: int | None = Field(default=None, ge=1)

    @model_validator(mode='after')
    def _slot_matches_the_visible_index(self) -> 'Stage6ApprovalCandidate':
        if self.slot != slot_of(self.index):
            raise ValueError('候选 slot 必须与 1 起 index 使用同一位置身份')
        if ((self.semantic_review is None)
                != (self.semantic_decision is None)):
            raise ValueError('semantic review 与合并 decision 必须同时出现')
        return self


class Stage6ApprovalWorkpack(FrozenModel):
    """Stage6 当前候选集的紧凑、代码与证据绑定终审包。"""

    v: Literal[1] = 1
    kind: FinalApprovalWorkpackKind
    sid: str = Field(min_length=1)
    code_version: str = Field(min_length=1)
    rules_fingerprint: str = Field(min_length=1)
    artifact_subject_sha256: str = Field(pattern=r'^[0-9a-f]{64}$')
    rationale: str = Field(min_length=1)
    check: CompactApprovalCheck
    #: 两名盲评各自的原始完整排序；逐候选 verdict/reason/evidence 在
    #: ``candidates[].semantic_decision.judges``，两者合起来才是未压扁的 A/B 证据。
    judge_rankings: dict[Literal[STAGE6_JUDGE_ROLES], list[str]] | None = None
    candidates: list[Stage6ApprovalCandidate] = Field(min_length=3, max_length=3)
    workpack_sha256: str = Field(pattern=r'^[0-9a-f]{64}$')
    disposition: ApprovalWorkpackDisposition
    current_slot: Literal[HOOK_SLOTS] | None = None
    current_approval: HookApprovalResolution | None = None

    @model_validator(mode='after')
    def _currentness_and_candidate_set_are_closed(self) -> 'Stage6ApprovalWorkpack':
        if self.kind != 'stage6':
            raise ValueError('Stage6ApprovalWorkpack.kind 必须是 stage6')
        if [row.index for row in self.candidates] != [1, 2, 3]:
            raise ValueError('Stage6 workpack 必须按产物顺序完整覆盖 1..3')
        has_semantic = any(row.semantic_decision is not None for row in self.candidates)
        if has_semantic != (self.judge_rankings is not None):
            raise ValueError('当前 A/B decision 与 judge_rankings 必须同进同出')
        if (self.judge_rankings is not None
                and set(self.judge_rankings) != set(STAGE6_JUDGE_ROLES)):
            raise ValueError('judge_rankings 必须恰含 A/B 两名判官')
        current = self.current_slot is not None and self.current_approval is not None
        if (self.disposition == 'already_current') != current:
            raise ValueError('already_current 必须携带当前 slot 与 approval')
        if self.current_approval is not None \
                and self.current_approval.workpack_sha256 != self.workpack_sha256:
            raise ValueError('当前 approval 必须绑定这份 workpack')
        return self


class Stage7ApprovalWorkpack(FrozenModel):
    """Stage7 当前人物决策面的紧凑、代码与机械证据绑定终审包。"""

    v: Literal[1] = 1
    kind: FinalApprovalWorkpackKind
    sid: str = Field(min_length=1)
    code_version: str = Field(min_length=1)
    rules_fingerprint: str = Field(min_length=1)
    artifact_subject_sha256: str = Field(pattern=r'^[0-9a-f]{64}$')
    decision_subject_sha256: str = Field(pattern=r'^[0-9a-f]{64}$')
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
        if ((self.disposition == 'already_current')
                != (self.current_approval is not None)):
            raise ValueError('already_current 必须携带当前 approval')
        if self.current_approval is not None \
                and self.current_approval.workpack_sha256 != self.workpack_sha256:
            raise ValueError('当前 approval 必须绑定这份 workpack')
        if self.kept_count != len(self.kept):
            raise ValueError('kept_count 必须与 kept 明细一致')
        if any(value < 0 for value in self.replacement_hits.values()):
            raise ValueError('replacement_hits 不得出现负数')
        return self


class WritingApprovalRequest(HashBoundHumanApproval):
    """对当前整本 Stage5 写作主体作出的、可归责且 hash-bound 的授权请求。"""


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
    current_decision: WritingApprovalDecision | None = None
    history: list[WritingApprovalDecision] = Field(default_factory=list)

    @model_validator(mode='after')
    def _subject_and_current_decision_agree(self) -> WritingApprovalView:
        if self.available != (self.current_subject_sha256 is not None):
            raise ValueError('writing available 与 current subject 必须同进同出')
        if self.current_decision is not None:
            if self.current_decision.subject_sha256 != self.current_subject_sha256:
                raise ValueError('当前写作授权必须绑定当前 subject')
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


class Stage5ClosureRequest(StrictModel):
    """对当前整书 Stage5 主体作出的原子闭合提交。"""

    actor: str = Field(min_length=1)
    reason: str = Field(min_length=1)
    workpack_sha256: str = Field(pattern=r'^[0-9a-f]{64}$')
    review_sha256: str = Field(pattern=r'^[0-9a-f]{64}$')
    must_appear: list[Stage5MustAppearDecision] = Field(default_factory=list)
    group_acks: list[Stage5FindingGroupAck] = Field(default_factory=list)

    @model_validator(mode='after')
    def _submission_has_no_duplicate_targets(self) -> Stage5ClosureRequest:
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


class Stage5ClosureTemplate(Stage5ClosureRequest):
    """服务端按当前盘面生成的关闭式模板；可直接编辑后作为 request 提交。

    模板的每条 must_appear / 整组确认都带 ``placeholder=True`` 与一次性
    ``template_token``；原样提交会被关闭式拒绝，必须由人逐项裁决。
    """


class Stage5ClosureDecision(Stage5ClosureRequest):
    """服务端已落盘的闭合回执；决定摘要与时间不接受客户端代填。"""

    decision_sha256: str = Field(pattern=r'^[0-9a-f]{64}$')
    decided_at: str = Field(min_length=1)

    @model_validator(mode='after')
    def _decision_contains_no_template_placeholders(self) -> Stage5ClosureDecision:
        if any(row.placeholder or row.template_token is not None
               for row in self.must_appear):
            raise ValueError('正式 closure decision 不得保留 must_appear placeholder')
        if any(ack.placeholder or ack.template_token is not None
               for ack in self.group_acks):
            raise ValueError('正式 closure decision 不得保留整组确认 placeholder')
        return self


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
    处置的条目不进任何待办，只在 ``resolved`` 里留计数。``disclosure`` 是
    INFO/metric 的只读披露计数（规则 → 条数），不需要裁决。
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
    must_appear: list[Stage5MustAppearRow]
    fact_group: Stage5FindingGroup | None = None
    unver_group: Stage5FindingGroup | None = None
    disclosure: dict[str, int] = Field(default_factory=dict)
    resolved: dict[str, int] = Field(default_factory=dict)
    workpack_sha256: str = Field(pattern=r'^[0-9a-f]{64}$')

    @model_validator(mode='after')
    def _subject_sets_are_closed(self) -> 'Stage5ClosureWorkpack':
        planned = set(self.planned_chapters)
        missing = set(self.missing_chapters)
        subjects = [row.output_ordinal for row in self.chapter_subjects]
        trims = [row.output_ordinal for row in self.unresolved_trim_items]
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
        """当前不可提交回执的原因；空列表 = 可以提交。"""
        blockers: list[str] = []
        if self.missing_chapters:
            blockers.append(f'缺正式章稿 {self.missing_chapters}')
        if self.invalid_chapters:
            blockers.append(f'仍有 .INVALID {self.invalid_chapters}')
        if self.hard_findings:
            blockers.append(f'阻断级判定 {sorted(set(self.hard_findings))}')
        if self.unresolved_trim_chapters:
            blockers.append(f'trim 待销账 {self.unresolved_trim_chapters}')
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
            if decision.workpack_sha256 != self.workpack.workpack_sha256:
                raise ValueError('closed 决定必须绑定当前 workpack')
            if not self.history or decision != self.history[-1]:
                raise ValueError('closed 当前决定必须是 history 最后一项')
        if self.state == 'expired' and self.current_decision is not None:
            raise ValueError('expired 态的旧决定只能留在 history')
        return self


class ApprovalDoc(StrictModel):
    """公开审批面 v6：新增 Stage5 闭合回执投影。"""

    v: Literal[6] = 6
    sid: str = Field(min_length=1)
    gate: dict[GateKey, bool]
    seen: dict[SeenKey, bool]
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
            raise ValueError('gate must contain exactly s0,s1,s2,s4,s5,s6,s7')
        if set(self.seen) != set(APPROVAL_SEEN):
            raise ValueError('seen must contain exactly read/global')
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


class OperatorNext(StrictModel):
    """同一次 operator 快照上的 overview、inbox 与唯一下一动作。"""

    base: str = Field(min_length=1)
    sid: str = Field(min_length=1)
    code_version: str = Field(min_length=1)
    surface_seal: Sha256
    state: OperatorNextState
    overview: NovelOverview
    inbox: InboxView
    action: OperatorAction | None = None

    @model_validator(mode='after')
    def _state_matches_the_action(self) -> OperatorNext:
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
            if self.action.resume_verdict != 'run':
                raise ValueError('ready action 的 resume_verdict 必须是 run')
        elif not self.action.waiting_on:
            raise ValueError('blocked action 必须明确 waiting_on')
        return self


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


class GlobalAddressLine(FrozenModel):
    """一条称谓（`stage2.Address` 的视图行：`by` 已解引用）。"""

    surface: str = Field(min_length=1)
    by: NameRef
    context: Literal[ADDRESS_CONTEXT]
    from_chapter: int = Field(ge=1)


class GlobalCharacterLine(FrozenModel):
    """一个人（`entities.characters` 的 dict 展平成带 `char_id` 的行）。

    ⚠ **`fate` 全量下发**：剧透遮蔽是前端的纯展示行为（`R-11.13`，四个字段名
    写在 `SPOILER_DISCLOSURE['hidden_paths']` 里），服务端不做第二把尺子。
    """

    char_id: str = Field(min_length=1)
    canonical: str = Field(min_length=1)
    name_distinctiveness: Literal[NAME_DISTINCTIVENESS]
    role: str = Field(min_length=1)
    role_evidence: FactEvidence
    fate: str | None
    fate_evidence: FactEvidence | None
    address: list[GlobalAddressLine] = Field(min_length=1)


class GlobalRelationLine(FrozenModel):
    """一条有向关系（两端已解引用）。"""

    relation_id: str = Field(min_length=1)
    subject: NameRef
    predicate: str = Field(min_length=1)
    predicate_evidence: FactEvidence
    object: NameRef
    visibility: RelationVisibility
    valid_from_chapter: int = Field(ge=1)
    valid_through_chapter: int | None


class GlobalKnowledgeEventLine(FrozenModel):
    """一条世界内知情事件；人物已解引用，无名群体原样显示。"""

    subject_kind: Literal[KNOWLEDGE_SUBJECT_KINDS]
    subject: NameRef
    known_from_chapter: int = Field(ge=1)
    evidence: FactEvidence


class GlobalSecretLine(FrozenModel):
    """一条秘密。⚠ **没有 `reader_knows_by_now`**——那是「相对当前章」的量，
    而本屏是全书视图，没有「当前章」；揭示章原样给，让人自己对。
    """

    secret_id: str = Field(min_length=1)
    fact: str = Field(min_length=1)
    fact_evidence: FactEvidence

    knowledge_events: list[GlobalKnowledgeEventLine] = Field(min_length=1)

    #: ⚠ **必须连 `setup_kind` 一起读。** `cold`（声明没有埋点）与
    #: `foreshadowed` 但章号被落盘前裁空，在 `setup_chapters` 上都是 `[]`；
    #: 界面拿空数组当「没有埋点」是对的，拿它当「模型声明了冷揭示」是错的。
    setup_kind: Literal[SECRET_SETUP_KINDS]
    setup_chapters: list[int]

    #: **逐章剧透闸门按它开**。
    first_reveal_chapter: int = Field(ge=1)


class ArcTrackLine(FrozenModel):
    """一条人物弧线（`subject` 已解引用；`char_id` / `reader` / `relation_id` 三类）。"""

    subject: NameRef
    trajectory: str = Field(min_length=1)
    trajectory_evidence: FactEvidence


class GlobalArcView(StrictModel):
    """解读层。`climax` / `ending` 全量下发（遮蔽归前端，同 `fate` 那一条）。"""

    conflict_stages: list[ConflictStage] = Field(min_length=1)
    climax: str = Field(min_length=1)
    ending: str = Field(min_length=1)
    arc_tracks: list[ArcTrackLine] = Field(min_length=1)


class GlobalView(StrictModel):
    """`GET /api/novels/:sid/stage2/global`。**S-05 那一屏的全部数据。**

    ⚠ 与 S-06 不同，本屏**没有「左半边」**：stage2 没跑就没有任何东西可看
    ⇒ 产物不在盘上是 **404**（`UpstreamMissing`），不是 `exists=False` 的 200。
    ⚠ `setting` 经 `by_alias` 出线，键是那两个固定中文 key（`时代背景` / `世界观`）。
    """

    sid: str = Field(min_length=1)
    title: str = Field(min_length=1)

    setting: Setting
    characters: list[GlobalCharacterLine] = Field(min_length=1)
    relations: list[GlobalRelationLine]
    world_rules: list[WorldRule]
    #: 空数组 = 这本书没抽到秘密。**那可能是漏抽了，不是「无」**（探针盲区）。
    secrets: list[GlobalSecretLine]
    arc: GlobalArcView

    #: 全屏解引用失败的总数（relations 两端 + secrets.knowledge_events + arc.subject）。
    #: **读数不是判定**——它是给人看「这份产物里有几个说不出名字的 id」。
    unresolved_refs: int = Field(ge=0)

    invalid_exists: bool
    freshness: Freshness
    reason: FreshnessReason
    ghosts: list[str] = Field(default_factory=list)

    provenance: GlobalProvenance = Field(alias='_provenance')


class GlobalReviewRow(GlobalReviewItem):
    """一条复核项 **加上服务端派生的两格**：交付效果与人审卡收敛键。

    ⚠ 两格都**只在服务端算**（`contracts/invariants.py::review_effect_of` 与
    `novel/quality/global_review.py::review_group_key`）。前端复制一份分类表，
    就出现第二个判官：某天新增一类高影响事实，界面仍按旧表把它画成「只披露」，
    而没有任何东西会出声（判据 13）。
    ⚠ 它们**不进产物**：`global_review.json` 的形状与版本一格没动，两格都可由
    kind/subject_path 零成本重算。
    """

    effect: Literal[GLOBAL_REVIEW_EFFECTS]
    group_key: str = Field(min_length=1)


class GlobalReviewView(StrictModel):
    """Current-subject status plus the auditable Stage2 review rows.

    ``approved`` is recomputed by the read model.  It is never trusted from the
    persisted review alone, because the current ``GlobalGrounding`` or required
    review surface may have changed since a person decided the rows.

    ⚠ ``approved`` 只回答 **blocking 面**是否逐项 PASS——那是 Stage4 那道闸的口径。
    `disclosure` 的未决项一条不少地留在 `items` 与 `effect_counts` 里；
    界面必须把它们画出来，只是不画成「Stage4 被它挡住」。
    """

    source_sha256: str = Field(pattern=r'^[0-9a-f]{64}$')
    current_subject_sha256: str = Field(pattern=r'^[0-9a-f]{64}$')
    current: bool
    approved: bool

    #: ``{effect: {四态: n}}``。两层键都**恒全出**（0 也出）：
    #: 「没有这个键」与「值是 0」不许混（同 `InboxView.counts`）。
    effect_counts: dict[str, dict[str, int]]
    items: list[GlobalReviewRow]


# ─────────────────────────────────────────────────────────────────────────────
# 二d、S-11 开篇文案（`GET /api/novels/:sid/stage6`）
# ─────────────────────────────────────────────────────────────────────────────

class HookCandidateLine(FrozenModel):
    """一条候选的视图行。`index` / `chars` / `approved` 是 view 现算的。

    ⚠ **`chars` 必须在 Python 侧走 `measure()` 那把尺子现算**（`stage6.Candidate`
    裁掉 `chars` 时明写的条件）：同一条候选 `measure()['chars']=253` 而
    `len(text)=277`——下沉到 JS 的后果是同一页上人会看到两个字数。
    """

    index: int = Field(ge=1)
    approach: Literal[HOOK_APPROACHES]
    chars: int = Field(ge=0)
    text: str = Field(min_length=1)
    subject_sha256: str = Field(pattern=r'^[0-9a-f]{64}$')
    mech: Mech
    findings: list[Finding]

    #: 最新质量审判按文本 sha1 映射出的状态。没有/过期审判是 unreviewed；
    #: 判过则恒 uncertain（本仓没有自动放行门）。⚠ 两者**不是同一件事**：
    #: 前者没有任何判官看过，后者有两名判官的带证据结论等着人读。
    semantic_state: Literal[HOOK_SEMANTIC_STATES]
    semantic_reason: str = Field(min_length=1)
    contradictions: list[HookContradiction]
    quality: HookQualityScores | None
    quality_score: float | None = Field(default=None, ge=0, le=10)
    quality_rank: int | None = Field(default=None, ge=1)
    #: ⚠ 这里曾有 `selectable`（「最新审判是校准双盲 PASS，可直接定稿」）与
    #: `human_resolvable`（「这条可由人工消解」）。前者在自动放行门下线后恒 `false`，
    #: 后者在 `semantic_state` 收成两态后恒 `true`——一条永远走不通的捷径与一道
    #: 永远开着的闸，误导的方向相反、形状相同。⇒ 两个都删掉；
    #: **每一条候选都要人显式核验**，要判还用不用人看就读 `semantic_state`。

    #: 这一条是不是当前定稿（按**位置身份**比：`hook.approved.slot == slot_of(index)`）。
    #: ⚠ 曾按 `approach` 比。同一种策略采两条样本时，那样写会把**两条**都标成已定稿。
    approved: bool


class HookApprovedLine(FrozenModel):
    """定稿的视图行。`origin` 的取值由 `stage6.ApprovedHook` 收口（恒 `human`），
    这里不抄第二份 Literal（api.py 纪律 ①）。"""

    #: 从**哪一条候选**来的；`None` = 没有前身（人手写）。
    slot: Literal[HOOK_SLOTS] | None
    #: 那条候选用的什么策略。**现算**（`candidates[slot].approach`），
    #: `slot is None` 时同为 `None`——没有前身就没有策略可报。
    approach: str | None
    chars: int = Field(ge=0)
    text: str = Field(min_length=1)
    origin: str = Field(min_length=1)
    mech: Mech
    findings: list[Finding]
    resolution: HookApprovalResolution


class Stage6View(StrictModel):
    """`GET /api/novels/:sid/stage6`。**S-11 那一屏的全部数据。**

    ⚠ 判据表不在这里——**结论只能来自 `/check/stage6`**（`R-11.4`）。
    ⚠ 产物不在 ⇒ 404（本屏没有产物就没有内容）；读不回 ⇒ 409。
    """

    sid: str = Field(min_length=1)
    title: str = Field(min_length=1)
    source_hook_text: str = Field(min_length=1)

    lead_upto: int = Field(ge=1)
    rationale: str = Field(min_length=1)
    candidates: list[HookCandidateLine] = Field(min_length=3, max_length=3)
    approved: HookApprovedLine | None
    judged: dict[str, JudgedDraft]
    sources: HookSources

    invalid_exists: bool
    freshness: Freshness
    reason: FreshnessReason
    ghosts: list[str] = Field(default_factory=list)

    provenance: HookProvenance = Field(alias='_provenance')

    @model_validator(mode='after')
    def _the_approved_flag_matches_the_approved_hook(self) -> Stage6View:
        """`candidates[].approved` 与 `approved` 说的必须是同一件事。"""
        flagged = [slot_of(c.index) for c in self.candidates if c.approved]
        if self.approved is None and flagged:
            raise ValueError(f'没有定稿而 {flagged} 标着已定——两格在说两件事')
        if self.approved is not None:
            want = [self.approved.slot] if self.approved.slot is not None else []
            if flagged != want:
                raise ValueError(
                    f'定稿来自 {self.approved.slot!r} 而候选标记是 {flagged}——'
                    f'无前身（人手写）时不许有候选自称定稿')
        if [c.index for c in self.candidates] != [1, 2, 3]:
            raise ValueError('候选 index 必须是 1..3（顺序即产物顺序）')
        return self


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

class ReadingSegment(FrozenModel):
    """该章原文上的一个连续分区。**`beat_id=null` ⇒ 空隙（hole）**——
    kind 不另立字符串（两态由可空性表达，api.py 不手抄取值面）。

    坐标恒是 Python 码点半开区间；**服务端已切好 `text`**，前端绝不按 start/end
    自己切字符串（Python 码点 ≠ JS 码元）。
    """

    start: int = Field(ge=0)
    end: int = Field(ge=1)
    chars: int = Field(ge=1)
    text: str = Field(min_length=1)
    beat_id: str | None
    #: 空隙且 `chars >= uncovered_min_chars` 才 `true`（beat 分区恒 `false`）。
    #: 阈值由响应自带（`uncovered_min_chars`），**是漏抓还是过渡交人工看摘录判断**。
    flagged: bool

    @model_validator(mode='after')
    def _interval_is_sound(self) -> ReadingSegment:
        if self.end <= self.start:
            raise ValueError(f'[{self.start}, {self.end}) 不是非空半开区间')
        if self.chars != self.end - self.start or self.chars != len(self.text):
            raise ValueError('chars 必须同时等于 end-start 与 text 的码点长度')
        if self.beat_id is not None and self.flagged:
            raise ValueError('beat 分区不许 flagged——「漏」只属于空隙')
        return self


class ReadingHinge(FrozenModel):
    """一条保真材料。`text=null` = 未定位（INVALID 状态的 hinge 没有原文可引）。"""

    kind: Literal[HINGE_KINDS]
    text: str | None


class ReadingFactLine(FrozenModel):
    """所属拍内的一条原子事实；证据字节只作阅读对照。"""

    fact_id: str = Field(min_length=1)
    claim: str = Field(min_length=1)
    kind: Literal[FACT_ATOM_KINDS]
    obligation: Literal[FACT_ATOM_OBLIGATIONS]
    evidence_text: str | None
    participant_char_ids: list[str]


class ReadingBeatLine(FrozenModel):
    """一拍。`located=false` 时坐标为 `null`（定位失败不编区间）。"""

    beat_id: str = Field(min_length=1)
    located: bool
    start: int | None
    end: int | None
    time_layer: Literal[TIME_LAYERS]
    place: str | None
    present_char_ids: list[str]
    fact_atoms: list[ReadingFactLine]
    hinges: list[ReadingHinge]

    @model_validator(mode='after')
    def _location_is_all_or_nothing(self) -> ReadingBeatLine:
        if self.located != (self.start is not None and self.end is not None):
            raise ValueError('located 与坐标必须说同一件事')
        return self


class ReadingNarrationLine(FrozenModel):
    grammatical_person: Literal[NARRATIVE_PERSON]
    narrator_char_id: str | None
    focalizer_char_id: str | None
    pronoun_surfaces: list[str]


class ReadingView(StrictModel):
    """`GET /api/novels/:sid/reading/:o`。**S-04 那一屏：原文 ↔ 骨架。**

    左半边（原文）来自 **stage1**；右半边（骨架）来自 stage2 逐章蓝图。
    ⚠ **stage2 没跑 ⇒ 200 且 `blueprint=false`**：整章一个 hole、beats 空
    ——左半边与 stage2 无关（同 S-06 左右半分治的道理）。
    stage1 不在 / `:o` 越界 ⇒ 404。
    """

    sid: str = Field(min_length=1)
    title: str = Field(min_length=1)

    chapter_ordinal: int = Field(ge=1)
    text_chars: int = Field(ge=1)
    blueprint: bool
    segments: list[ReadingSegment] = Field(min_length=1)
    beats: list[ReadingBeatLine]
    narration: list[ReadingNarrationLine]

    #: 没被任何 beat 覆盖的字数（全部空隙之和，不只 flagged 的）。
    loose: int = Field(ge=0)
    uncovered_min_chars: int = Field(ge=1)

    #: `stage2/chapter_blueprints.json` 的处境（六格一次拿全）。
    #: `blueprint=false` 的两种来路（没跑 / 读不回）靠它分：`exists` 与 `readable`。
    blueprint_state: ArtifactState

    provenance: BlueprintProvenance | None = Field(alias='_provenance')

    @model_validator(mode='after')
    def _postconditions(self) -> ReadingView:
        """① 分区连续覆盖整章；② `blueprint=false` 的形态整块成立；③ `loose` 对得上账。"""
        if self.segments[0].start != 0 or self.segments[-1].end != self.text_chars:
            raise ValueError('分区必须从 0 开始、到 text_chars 结束')
        for left, right in zip(self.segments, self.segments[1:]):
            if left.end != right.start:
                raise ValueError('分区必须按顺序首尾连续')
        if not self.blueprint:
            if self.beats or self.provenance is not None:
                raise ValueError('blueprint=false 时右半边必须整块缺席——那份内容是从哪儿来的？')
            if any(s.beat_id is not None for s in self.segments):
                raise ValueError('blueprint=false 时不该有 beat 分区')
        holes = sum(s.chars for s in self.segments if s.beat_id is None)
        if self.loose != holes:
            raise ValueError(f'loose={self.loose} 而空隙合计 {holes}——两个数必须是同一个')
        return self


# ─────────────────────────────────────────────────────────────────────────
# 二f、Stage2 Blueprint 只读审阅工作包（暂不是审批面）
# ──────────────────────────────────────────────────────────────────────────

class BlueprintReviewCoverageSegment(FrozenModel):
    """一章原文的一个连续分区。

    坐标和 ``beat_id`` 是机械面，工作包不重复展示；``beat_order``
    只是当前工作包内的 1 起顺序，让 coverage 能与右侧语义拍对齐。
    ``None`` 表示未被任何已定位 beat 覆盖的原文空隙。
    """

    beat_order: int | None = Field(default=None, ge=1)
    chars: int = Field(ge=1)
    text: str = Field(min_length=1)
    flagged: bool

    @model_validator(mode='after')
    def _chars_and_flag_are_sound(self) -> 'BlueprintReviewCoverageSegment':
        if self.chars != len(self.text):
            raise ValueError('coverage segment chars 必须等于 text 的码点长度')
        if self.beat_order is not None and self.flagged:
            raise ValueError('已覆盖的 beat 分区不许标成空隙异常')
        return self


class BlueprintReviewCoverage(FrozenModel):
    """一章正文的完整分区账。按 ``segments`` 顺序连接即是整章原文。"""

    text_chars: int = Field(ge=1)
    covered_chars: int = Field(ge=0)
    loose_chars: int = Field(ge=0)
    uncovered_min_chars: int = Field(ge=1)
    segments: list[BlueprintReviewCoverageSegment] = Field(min_length=1)

    @model_validator(mode='after')
    def _segments_close_the_complete_chapter(self) -> 'BlueprintReviewCoverage':
        covered = sum(
            row.chars for row in self.segments if row.beat_order is not None)
        loose = sum(
            row.chars for row in self.segments if row.beat_order is None)
        if covered != self.covered_chars or loose != self.loose_chars:
            raise ValueError('coverage 的 covered/loose 分账与 segments 对不上')
        if covered + loose != self.text_chars:
            raise ValueError('coverage segments 必须不重不漏覆盖整章原文')
        for row in self.segments:
            expected = (
                row.beat_order is None and row.chars >= self.uncovered_min_chars)
            if row.flagged != expected:
                raise ValueError('coverage flagged 必须只标记超阈值原文空隙')
        return self


class BlueprintReviewFact(FrozenModel):
    """人审真正需要看的事实面；稳定 id 与 locator 由机械报告守。"""

    claim: str = Field(min_length=1)
    kind: Literal[FACT_ATOM_KINDS]
    obligation: Literal[FACT_ATOM_OBLIGATIONS]
    evidence_text: str | None
    participant_char_ids: list[str]


class BlueprintReviewBeat(FrozenModel):
    """一拍紧凑语义面。``beat_order`` 是工作包内与 coverage 的连接键。"""

    beat_order: int = Field(ge=1)
    located: bool
    time_layer: Literal[TIME_LAYERS]
    place: str | None
    present_char_ids: list[str]
    facts: list[BlueprintReviewFact]
    hinges: list[ReadingHinge]


class BlueprintReviewCausalLink(FrozenModel):
    """因果边的人读投影：直接给 claim → claim，不再逼人回查 fact_id。"""

    before_claim: str = Field(min_length=1)
    after_claim: str = Field(min_length=1)


class BlueprintReviewChapter(FrozenModel):
    """一章的完整 coverage + 紧凑语义审阅面。"""

    chapter_ordinal: int = Field(ge=1)
    anomalous: bool
    attention_rules: list[str]
    chapter_function: str = Field(min_length=1)
    coverage: BlueprintReviewCoverage
    beats: list[BlueprintReviewBeat] = Field(min_length=1)
    causal_links: list[BlueprintReviewCausalLink]
    entry_state_claims: list[str]
    ending_state_claims: list[str]
    narration: list[ReadingNarrationLine] = Field(min_length=1)

    @model_validator(mode='after')
    def _chapter_surface_is_closed(self) -> 'BlueprintReviewChapter':
        if self.anomalous != bool(self.attention_rules):
            raise ValueError('anomalous 必须与本章 attention_rules 同进同出')
        orders = [row.beat_order for row in self.beats]
        if orders != list(range(1, len(orders) + 1)):
            raise ValueError('Blueprint review beat_order 必须从 1 起连续')
        known = set(orders)
        used = {
            row.beat_order for row in self.coverage.segments
            if row.beat_order is not None
        }
        if used - known:
            raise ValueError('coverage 引用了本章不存在的 beat_order')
        return self


class BlueprintReviewBinding(FrozenModel):
    """工作包的精确输入身份；摘要均来自同一次受校验的字节读取。"""

    blueprint_rel: str = Field(min_length=1)
    subject_kind: Literal[BLUEPRINT_REVIEW_SUBJECT_KINDS]
    main_text_artifact_sha256: str = Field(pattern=r'^[0-9a-f]{64}$')
    blueprint_artifact_sha256: str = Field(pattern=r'^[0-9a-f]{64}$')
    check_report_sha256: str = Field(pattern=r'^[0-9a-f]{64}$')
    code_version: str = Field(min_length=1)
    rules_fingerprint: str = Field(min_length=1)
    chapter_ordinals: list[int] = Field(min_length=1)

    @model_validator(mode='after')
    def _chapters_are_a_closed_ordered_set(self) -> 'BlueprintReviewBinding':
        if self.chapter_ordinals != sorted(set(self.chapter_ordinals)):
            raise ValueError('Blueprint review binding 章号必须升序且去重')
        return self


class BlueprintReviewWorkpack(FrozenModel):
    """Stage2 Blueprint 的只读、异常优先审阅包。

    它不是审批或产物：没有 actor/state/action，不开任何 gate。
    ``subject_sha256`` 是当前 Blueprint 完整版本化盘上字节；
    ``binding_sha256`` 则覆盖除自身外的整份可见工作包。
    """

    sid: str = Field(min_length=1)
    title: str = Field(min_length=1)
    read_only: bool = True
    auto_decisions: bool = False
    subject_sha256: str = Field(pattern=r'^[0-9a-f]{64}$')
    binding_sha256: str = Field(pattern=r'^[0-9a-f]{64}$')
    binding: BlueprintReviewBinding
    check: CompactApprovalCheck
    accounting: list[AccountingLine]
    mechanical_fields_hidden: list[str] = Field(min_length=1)
    chapters: list[BlueprintReviewChapter] = Field(min_length=1)

    @model_validator(mode='after')
    def _read_only_complete_exception_first_surface(self) -> 'BlueprintReviewWorkpack':
        if not self.read_only or self.auto_decisions:
            raise ValueError('Blueprint review workpack 只读且绝不自动做决定')
        if self.subject_sha256 != self.binding.blueprint_artifact_sha256:
            raise ValueError('subject_sha256 必须精确绑定 Blueprint 盘上字节')
        ordinals = [row.chapter_ordinal for row in self.chapters]
        if (len(ordinals) != len(set(ordinals))
                or set(ordinals) != set(self.binding.chapter_ordinals)):
            raise ValueError('Blueprint review chapters 必须不重不漏覆盖全部章')
        seen_normal = False
        anomalous: list[int] = []
        normal: list[int] = []
        for row in self.chapters:
            if row.anomalous:
                if seen_normal:
                    raise ValueError('Blueprint review 必须把异常/命中章放在前面')
                anomalous.append(row.chapter_ordinal)
            else:
                seen_normal = True
                normal.append(row.chapter_ordinal)
        if anomalous != sorted(anomalous) or normal != sorted(normal):
            raise ValueError('异常章与普通章各自必须按章号升序')
        if len(self.mechanical_fields_hidden) != len(set(self.mechanical_fields_hidden)):
            raise ValueError('mechanical_fields_hidden 不得重复')
        return self


class BlueprintSeenRequest(StrictModel):
    """把 ``seen.read`` 精确绑定到用户实际取得的 Blueprint 工作包输入。

    这不是新的审批形状；它只是写现役 seen proof 前的乐观并发条件。
    ``expected_binding_sha256`` 证明提交方拿到的是完整工作包，而不是从其它
    读面各抄几枚散装 hash。Blueprint、MainText、代码或规则任一漂移，都必须
    重新取工作包再确认。
    """

    expected_binding_sha256: str = Field(pattern=r'^[0-9a-f]{64}$')
    expected_subject_sha256: str = Field(pattern=r'^[0-9a-f]{64}$')
    expected_main_text_artifact_sha256: str = Field(pattern=r'^[0-9a-f]{64}$')
    expected_code_version: str = Field(min_length=1)
    expected_rules_fingerprint: str = Field(min_length=1)


# ─────────────────────────────────────────────────────────────────────────────
# 二g、S-07 计划 ↔ 写作上下文（`GET /api/novels/:sid/stage4/:o`）
# ─────────────────────────────────────────────────────────────────────────────

class PlanSceneLine(FrozenModel):
    """Stage4 编译出的一场：UI 展示决策与 typed 覆盖，不回退成源 beat 清单。"""

    i: int = Field(ge=1)
    slot: str = Field(pattern=r'^s\d{2,4}$')
    budget_chars: int = Field(gt=0)
    purpose: str = Field(min_length=1)
    role: Literal[STRUCTURAL_ROLES]
    delivery: Literal[DELIVERY_MODES]
    transition: Literal[TEMPORAL_LINKS]
    land_fact_refs: list[str] = Field(min_length=1)
    context_fact_refs: list[str]
    turn_fact_ref: str = Field(min_length=1)
    entry_state_refs: list[str]
    exit_state_refs: list[str]


class StructureDecisionView(StructureDecision):
    """持久化人工声明 + 当前算法从实际计划现场复算的只读向量。"""

    computed_change_kinds: list[Literal[STRUCTURE_CHANGE_KINDS]]


class PlanChapterLine(StrictModel):
    """计划侧的一章：当前 blueprint 派生语义 + 持久化的新场景设计。"""

    output_ordinal: int = Field(ge=1)
    from_source_ordinals: list[int] = Field(min_length=1, max_length=1)
    chapter_goal: PlanGoal
    open_hook_goal: PlanGoal
    end_hook_goal: PlanGoal
    structure_decision: StructureDecisionView
    semantic_contract: SemanticContract
    chapter_rewrite_brief: str = Field(min_length=1)
    scenes: list[PlanSceneLine] = Field(min_length=1)


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
    chapters: list[int] = Field(min_length=1)
    ordinal: int = Field(ge=1)
    index: list[Stage4IndexRow] = Field(min_length=1)

    plan_chapter: PlanChapterLine
    ctx_chapter: CtxChapter | None

    #: 写手被要求的篇幅区间，**与写手同一把尺子**（`prompts.target_band`）。
    #: `null` ⇔ `ctx_chapter=null`（没有任务书就没有目标）。
    #:
    #: ⚠ 此处**没有** `writer_target_estimated`：那个布尔曾用于区分「按 `budget_chars`
    #: 算的实数带」与「`budget_chars` 缺席时按 `source_chars` 估的带」，而
    #: `CtxScene.budget_chars` / `CtxChapter.budget_chars` 都是 `Field(gt=0)` 必填，
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
        if self.identity_plan != (self.structure_subject_sha256 is None):
            raise ValueError(
                '恒等基线不是可审批主体；正式计划必须带同份计划的完整结构 SHA-256')
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


class HookApproveRequest(StrictModel):
    """`POST /api/novels/:sid/stage6/approve` —— 文案定稿（`--pick`，1 起）。

    ⚠ **不收文本**（2026-08-17 用户裁决）：改字是另一个节点。除候选序号外，
    必须收 actor/reason 和候选完整 SHA-256，用于自报身份的人工审计与乐观锁。
    改字由 `stage6.hook --apply-edit` 承接，且契约把编辑文件钉死在书目录内（`_edit_rel`）、
    R-11.3 禁止服务进程写产物盘 ⇒ 浏览器文本没有合规通道落成 `hook_edit.json`。
    改字走 CLI；S-11 的产品语义本来就只有「定稿按钮」（人选那一下是唯一闸门）。
    """

    pick: int = Field(ge=1)
    actor: str = Field(min_length=1)
    reason: str = Field(min_length=1)
    subject_sha256: str = Field(pattern=r'^[0-9a-f]{64}$')
    workpack_sha256: str = Field(pattern=r'^[0-9a-f]{64}$')

    @model_validator(mode='after')
    def _audit_text_is_not_blank(self) -> 'HookApproveRequest':
        if not self.actor.strip() or not self.reason.strip():
            raise ValueError('actor/reason 不得只有空白')
        return self


class NameMapApproveRequest(HashBoundHumanApproval):
    """Stage7 human approval bound to the exact decision surface."""

    workpack_sha256: str = Field(pattern=r'^[0-9a-f]{64}$')

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
