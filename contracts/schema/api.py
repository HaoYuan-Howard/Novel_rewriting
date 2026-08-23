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

from typing import Annotated, Literal

from pydantic import Field, model_validator

from contracts.invariants import (
    ADDRESS_CONTEXT,
    ANCHOR_COVERAGE_STATE,
    ANCHOR_COVERAGE_WARN,
    APPROVAL_GATES,
    APPROVAL_SEEN,
    CHAPTER_LABEL_ANOMALY,
    DELIVERY_MODES,
    DEPTH_MODES,
    HINGE_KINDS,
    HOOK_APPROACHES,
    HOOK_SLOTS,
    slot_of,
    HOOK_SEMANTIC_STATES,
    HOOK_KEYS,
    INBOX_ATTENTION,
    INBOX_DELIVERY_EFFECT,
    INBOX_KIND,
    NAME_DISTINCTIVENESS,
    NARRATIVE_PERSON,
    NONBODY_KINDS,
    REMOVED_ORIGIN,
    RESIDUAL_ADOPTION_CONFIDENCE,
    SEVERITY,
    STAGES,
    STRUCTURAL_ROLES,
    STAGE0_SEGMENT_KINDS,
    STAGE1_SEGMENT_KINDS,
    TIME_LAYERS,
    TEMPORAL_LINKS,
    VOICE_KEYS,
)
from contracts.schema.base import FrozenModel, Sha1, StrictModel
from contracts.schema.common import Finding, HashBoundHumanApproval
from contracts.schema.evaluation import GlobalReviewItem
from contracts.schema.orchestration import (
    AgentRunRecord, ArtifactCondition, ArtifactState, Freshness, FreshnessReason,
    JobRecord, JobState, NovelOverview, QueueRun,
)
from contracts.schema.stage1 import HookCopy
from contracts.schema.stage0 import RemovalReport, Stage0Revision, Stage0Warning
from contracts.schema.stage2 import (
    BlueprintProvenance, ConflictStage, GlobalProvenance, Setting, WorldRule,
)
from contracts.schema.stage3 import Measurable, StyleProvenance
from contracts.schema.stage4 import CtxChapter, CtxProvenance, PlanGoal, PlanProvenance
from contracts.schema.stage5 import (
    ChapterSegment, JudgedDraft, ReviewProvenance, ReviewSources,
    RoundFreeProvenance, RoundLlmProvenance,
)
from contracts.schema.stage6 import (
    HookApprovalResolution,
    HookContradiction,
    HookProvenance,
    HookQualityScores,
    HookSources,
    Mech,
)
from contracts.schema.stage7 import (
    Alias, ApprovedNameMap, BookTxt, ExtraSub, IdentityBaseline,
    NameMapProvenance, NameMapSources, Residual, StemBaseline,
)

VoiceKey = Literal[VOICE_KEYS]
HookKey = Literal[HOOK_KEYS]
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
AnchorCoverageState = Literal[ANCHOR_COVERAGE_STATE]


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


class Stage0Report(RemovalReport):
    """`GET /api/novels/:sid/stage0/report` 的那一份 = 产物 + 渲染好的告警 + 执行资格。

    ⚠ 它**继承**产物模型而不是抄一份：22 个字段抄第二遍就是 22 个会分家的字段。
    只覆盖 `warnings` 一格、外加两格视图专有的投影。
    """

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

    normalized_source_sha1: Sha1
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
    expected_normalized_source_sha1: Sha1
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
    actor: str = Field(min_length=1)
    reason: str = Field(min_length=1)


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


class Stage0CandidateView(StrictModel):
    """`GET /api/novels/:sid/stage0/candidate` 的那一份：**当前稿与候选并排**。

    ⚠ 它把两版的**读数**一起给出来，前端不自己算：机器重跑到底改了什么，
    唯一诚实的答案是逐版对比正文长度与剔除条数，而那是服务端已经有的事实。
    """

    candidate: Stage0Revision
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
    flagged: bool
    text: str = Field(min_length=1)
    chapter_ordinal: int | None = Field(default=None, ge=1)
    source_chapter_no: int | None = Field(default=None, ge=0)

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
        if self.flagged != (self.kind == 'hole'):
            raise ValueError('stage1 只有 hole 分区 flagged=true')
        return self


class Stage1Segments(StrictModel):
    input_sha1: Sha1
    segments: list[Stage1Segment] = Field(min_length=1)

    @model_validator(mode='after')
    def _segments_are_one_continuous_document(self) -> Stage1Segments:
        if self.segments[0].start != 0:
            raise ValueError('stage1 分区必须从 clean_text 坐标 0 开始')
        for left, right in zip(self.segments, self.segments[1:]):
            if left.end != right.start:
                raise ValueError('stage1 分区必须按顺序首尾连续')
        ids = [segment.id for segment in self.segments]
        if len(ids) != len(set(ids)):
            raise ValueError('stage1 分区 id 不得重复')
        return self


class Stage1ChapterRow(FrozenModel):
    chapter_ordinal: int = Field(ge=1)
    source_chapter_no: int = Field(ge=0)
    chars: int = Field(ge=0)
    preview: str
    #: 正式 Stage1 章表也携带章号诊断；否则重号/缺号/倒退只藏在校验行的
    #: ``scanned`` 字符串里，界面会把那条规则折进「查过、没问题」。
    label_delta: int | None = None
    anomaly: ChapterLabelAnomaly | None = None

    @model_validator(mode='after')
    def _delta_exists_exactly_when_it_is_computable(self) -> Stage1ChapterRow:
        if (self.chapter_ordinal == 1) != (self.label_delta is None):
            raise ValueError('有且只有第一章没有 label_delta')
        return self


class Stage1Chapters(StrictModel):
    chapters: list[Stage1ChapterRow] = Field(default_factory=list)
    anomaly_count: int = Field(default=0, ge=0)

    @model_validator(mode='after')
    def _ordinals_are_contiguous(self) -> Stage1Chapters:
        got = [chapter.chapter_ordinal for chapter in self.chapters]
        if got != list(range(1, len(got) + 1)):
            raise ValueError('stage1 章节索引必须从 1 起连续有序')
        hit = sum(1 for chapter in self.chapters if chapter.anomaly is not None)
        if hit != self.anomaly_count:
            raise ValueError(f'anomaly_count={self.anomaly_count} 与逐章实际命中 {hit} 不符')
        return self


class Stage0PreviewChapter(FrozenModel):
    """S-00 批准前分章预览里的一章。**诊断用，不承重。**

    与 `Stage1ChapterRow` 刻意不共用一个模型：那一份是**已批准之后**的正式章表，
    这一份是**批准之前**的一次性推演。两份都显示同源的章号诊断，但不能因此
    合成一个模型——那会让「已验证事实」与「尚未批准的推演」在类型上分不开
    （同 `TODO-36` 要拆 command DTO 的那条理由）。
    """

    chapter_ordinal: int = Field(ge=1)
    source_chapter_no: int = Field(ge=0)
    chars: int = Field(ge=0)
    preview: str
    #: 本章原始章号 − 上一章原始章号。**只有第一章为 `None`**（没有上一章）。
    #: ⚠ 曾经还有第二种缺席理由「两章任一不是数字章号」。`source_chapter_no` 改型成
    #: `int` 之后那种情形不存在了，`non_numeric` 一档也随之出局。
    label_delta: int | None = None
    anomaly: ChapterLabelAnomaly | None = None

    @model_validator(mode='after')
    def _delta_exists_exactly_when_it_is_computable(self) -> Stage0PreviewChapter:
        if (self.chapter_ordinal == 1) != (self.label_delta is None):
            raise ValueError(
                '有且只有第一章没有 label_delta——第一章没有上一章，'
                '而其余每一章的落差都算得出来（两个章号都是 int）')
        return self


class Stage0PartitionPreview(StrictModel):
    """S-00 批准前的**只读**分章预览：不写 `stage1/`，不改变任何产物。

    【为什么闸门形状没变】正式 stage1 产物仍然只来自已批准的 stage0（`R-0.3b`），
    变的只是**人批之前能看见什么**：审批页多一屏 hook / 逐章 outline / tail，
    而分章本身是零成本纯函数，重算一次不欠任何账。

    ⚠ `available=False` 不是错误页：它说的是「按当前剔除结果根本分不出章」——
    那正是应当在批准**之前**知道的事，不是等 stage1 跑起来才炸。
    """

    #: 预览基于哪一份 clean_text。人看完预览再批准，批的是同一份字节。
    input_sha1: Sha1
    available: bool
    reason: Annotated[str, Field(min_length=1)] | None
    hook_chars: int = Field(default=0, ge=0)
    hook_preview: str = ''
    chapters: list[Stage0PreviewChapter] = Field(default_factory=list)
    tail_chars: int = Field(default=0, ge=0)
    anomaly_count: int = Field(default=0, ge=0)
    note: str = Field(min_length=1)

    @model_validator(mode='after')
    def _availability_is_explicit(self) -> Stage0PartitionPreview:
        if self.available != (self.reason is None):
            raise ValueError('available 与 reason 必须恰好表达同一状态')
        if not self.available:
            if self.chapters or self.hook_chars or self.tail_chars or self.anomaly_count:
                raise ValueError('分不出章时不得发一份看似完整的 outline')
            return self
        ordinals = [chapter.chapter_ordinal for chapter in self.chapters]
        if ordinals != list(range(1, len(ordinals) + 1)):
            raise ValueError('预览章节索引必须从 1 起连续有序')
        hit = sum(1 for chapter in self.chapters if chapter.anomaly is not None)
        if hit != self.anomaly_count:
            raise ValueError(f'anomaly_count={self.anomaly_count} 与逐章实际命中 {hit} 不符')
        return self


class HookCopyView(StrictModel):
    hook_copy: HookCopy
    note: str = Field(min_length=1)


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
# 一、S-06 文风那一屏（`_api.md` §四）
# ─────────────────────────────────────────────────────────────────────────────

class VoiceLine(FrozenModel):
    """声音内核的一维。**数组的一项，不是对象的一个字段。**

    ⚠ 发成对象（`{narrative_distance: "...", ...}`）的话，八维的**顺序**就落在
    字段声明顺序上——那是第二个落点，而顺序在界面上是有意义的
    （`narrative_distance` 打头，`mode_variation` 收尾且明显长于其余七维）。
    数组 + 一条「顺序恒等于 `VOICE_KEYS`」的后置条件，顺序就只有一处算。
    """

    key: VoiceKey
    text: str = Field(min_length=1)

    #: 这一维写了多少字。**产地是代码，不是模型**——它是给人快速看「哪一维写得敷衍」的。
    chars: int = Field(ge=0)


class HookStyleLine(FrozenModel):
    """开篇文案段写法的一项。理由同 `VoiceLine`。

    ⚠ 它**没有** `chars`：抓包里就没有。别顺手补齐——补一个没人要的字段，
    下一个人会以为它有消费方（判据 6：加字段不是免费的）。
    """

    key: HookKey
    text: str = Field(min_length=1)


class DriftItem(FrozenModel):
    """一项实测漂移：`(键, 产物里存的, 照现在的 stage1 重算的)`。

    ⚠ **具名对象，不是三元组。** `novel/core/measure.py::drift` 内部返回的是元组，
    而元组一旦出线就是一个**位置数组**——前端拿到 `["body.chars", 9516, 9520]`，
    三个格子靠下标认。全仓对位置数组的态度只有一条：不发（同 `/api/enums`
    拒收 `NamedTuple` 的那一条）。

    ⚠ `stored` / `now` 有一个特殊值 `'（缺）'`：**两边摊平后取并集比**，
    少一个键也算漂移（F-054：老产物缺 `paragraph_sep_chars`，
    而下游拿 budget 直接除以 `avg_paragraph_chars`，段落目标系统性高 14%）。
    """

    key: str = Field(min_length=1)
    stored: int | float | str | list[str] | None
    now: int | float | str | list[str] | None


class StyleView(StrictModel):
    """`GET /api/novels/:sid/stage3/style`。**S-06 那一屏的全部数据。**

    ⚠⚠ **它是视图不是产物。** 右半边来自盘上那份 `stage3/style_grounding.json`；
    **左半边（实测数字与档位词）是代码现算的，产物在不在都算得出来**
    ——`04_delivery.md:123` 明写「`exists: false` 时左半边（实测）仍有内容」。
    这不是花哨：左半边量的是 **stage1**，它与 stage3 有没有跑过无关。
    """

    #: **由 `novel/paths.py` 从目录名派生**——新契约已把 `title`/`source_id` 从产物里
    #: 裁掉了（`stage3.md`：它们在本层唯一的作用是被扫描面排除掉，
    #: 「一个字段存在的理由是记得把它从检查里排除」本身就说明它不该存在）。
    sid: str = Field(min_length=1)
    title: str = Field(min_length=1)

    exists: bool

    #: 产物整体处境的服务端结论。界面用它选提示文案，不再用
    #: ``exists`` / ``invalid_exists`` / ``readable`` 自己重建优先级。
    condition: ArtifactCondition

    #: 盘上有一份 `.INVALID` 在等人处理。**留着不说等于静默**：
    #: 正式稿可能是**更早**的一版，而人会以为它就是最近这次跑出来的。
    invalid_exists: bool

    #: 能不能按**当前** schema 读回来。**`None` = 产物不在盘上，无从谈起。**
    #: ⚠ 三态而不是 `bool`：`readable=False` 与 `exists=False` 是两件事
    #: （`ArtifactState` 已经踩过——盘上真有一份没有 `_provenance` 的产物，
    #: 折成「不存在」会让界面说「还没跑」，而它跑过）。
    readable: bool | None

    # ── 右半边：产物（`exists and readable` 时才有） ──────────────────────────

    voice: list[VoiceLine] = Field(default_factory=list)
    surface_habits: list[str] = Field(default_factory=list)
    hook_style: list[HookStyleLine] = Field(default_factory=list)
    measurable_stored: Measurable | None
    bands_stored: dict[str, str] | None
    provenance: StyleProvenance | None = Field(alias='_provenance')

    # ── 左半边：现算（与产物无关） ────────────────────────────────────────────

    #: 开篇文案段原文。来自 **stage1**，与本层产物无关。
    hook_copy_text: str | None

    measured_now: Measurable | None
    bands_now: dict[str, str] | None

    #: 量不出来时的人话，与 `measured_now` **恰好一个非空**（后置条件钉死）。
    measure_error: str | None
    #: **用 id 不用措辞**（`Q-6`）：异常类名。
    #: 「stage1 还没跑」（`UpstreamMissing`）与「stage1 的产物坏了」（`ArtifactUnreadable`）
    #: 对人的下一步是**相反**的指示，靠句子前缀去认会随一次措辞改动静默断掉。
    measure_error_kind: str | None

    #: 存/现两份实测的逐项差异。
    #: ⚠⚠ **`[]` 与 `null` 是两件事**：`[]` = 「比过了，没有漂移」，
    #: `null` = 「有一边算不出来，**无从比**」。压成一个 `[]` 就回到
    #: `Q-2` 那个病（「不知道」与「没问题」必须能区分）。
    drift: list[DriftItem] | None

    # ── 来源账 ────────────────────────────────────────────────────────────────

    freshness: Freshness
    reason: FreshnessReason
    #: 指向已经不在盘上的那些边，**按名字点名**（`Q-4` 亲自点过的 bug：
    #: 原型对取不到指纹的条目静默跳过，而盘上真有一条）。**空列表也要写。**
    ghosts: list[str] = Field(default_factory=list)

    @model_validator(mode='after')
    def _postconditions(self) -> StyleView:
        """五条。每一条都在挡一种「两个状态在界面上长得一样」。"""
        want_condition = (
            'invalid' if self.invalid_exists else
            'missing' if not self.exists else
            'unreadable' if not self.readable else
            'ready'
        )
        if self.condition != want_condition:
            raise ValueError(
                f'condition={self.condition!r} 与服务端原始事实推导出的 '
                f'{want_condition!r} 不一致')

        # ① 不在盘上 ⇒ 「读不读得回来」是个没有意义的问题。
        if (self.readable is None) != (not self.exists):
            raise ValueError(
                f'exists={self.exists} 而 readable={self.readable!r}：'
                f'`readable=None` 必须恰好对应「产物不在盘上」')

        # ② 右半边要么整块在、要么整块不在。半块在的形态没有任何消费方，
        #    而它会让界面画出一屏「有 voice 没有 measurable」的假产物。
        loaded = bool(self.exists and self.readable)
        right = (self.voice, self.surface_habits, self.hook_style,
                 self.measurable_stored, self.bands_stored, self.provenance)
        if loaded and not all(x is not None and x != [] for x in right):
            raise ValueError('产物读回来了，而右半边有空的格子——那不是一份合法的产物')
        if not loaded and any(x is not None and x != [] for x in right):
            raise ValueError(
                f'exists={self.exists} readable={self.readable} 而右半边有内容——'
                f'那份内容是从哪儿来的？')

        # ③ 顺序只有一处算：`VOICE_KEYS` / `HOOK_KEYS`。
        if loaded:
            if [v.key for v in self.voice] != list(VOICE_KEYS):
                raise ValueError(f'voice 的顺序与 VOICE_KEYS 分家了：{[v.key for v in self.voice]}')
            if [h.key for h in self.hook_style] != list(HOOK_KEYS):
                raise ValueError(
                    f'hook_style 的顺序与 HOOK_KEYS 分家了：{[h.key for h in self.hook_style]}')

        # ④ 「量到了」与「量不了，原因是 X」恰好一个成立。
        #    同空 = 什么都没说；同非空 = 说了两件互相矛盾的事。
        if (self.measured_now is None) == (self.measure_error is None):
            raise ValueError(
                'measured_now 与 measure_error 必须**恰好一个**非空：'
                '同空是「什么都没说」，同非空是「量到了但也没量到」')
        if (self.measure_error is None) != (self.measure_error_kind is None):
            raise ValueError('measure_error 与 measure_error_kind 必须同进同出')
        # 档位词是 `bands()` 从实测数字现算的纯函数结果 ⇒ 它有没有，完全由实测有没有决定。
        # ⚠ 抓包里另有一个 `bands_error`，**本模型刻意不要它**：`bands()` 在一个合法的
        #    `Measurable` 上是全函数，那个格子只可能与 `measure_error` 说同一件事
        #    ——同一条信息的第二个落点。登记在 `_api.md` §四。
        if (self.bands_now is None) != (self.measured_now is None):
            raise ValueError('bands_now 与 measured_now 必须同进同出（档位词是现算的）')
        if (self.bands_stored is None) != (self.measurable_stored is None):
            raise ValueError('bands_stored 与 measurable_stored 必须同进同出')

        # ⑤ 两边都在才比得了。`drift=[]` 是「比过了没漂移」，`None` 是「无从比」。
        comparable = self.measurable_stored is not None and self.measured_now is not None
        if (self.drift is not None) != comparable:
            raise ValueError(
                f'drift={"有" if self.drift is not None else "null"} 而两份实测'
                f'{"都在" if comparable else "不齐"}——`[]` 是「没漂移」，`null` 是「无从比」')
        return self


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
    mode: str | None = None
    flags: list[str] = Field(default_factory=list)


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


class AgentRunPreview(StrictModel):
    """`POST /api/agent/runs/preview` 的响应。与真跑同一构造函数（`R-11.8` 同构）。"""

    argv: list[str] = Field(min_length=1)
    prompt: str = Field(min_length=1)
    authorized: bool
    egress_authorized: bool


class AgentRunList(StrictModel):
    """`GET /api/agent/runs?sid=`。新的在前（与 `JobList` 同款约定）。"""

    runs: list[AgentRunRecord] = Field(default_factory=list)


class ApprovalAction(FrozenModel):
    kind: str = Field(min_length=1)
    label: str = Field(min_length=1)
    extra: list[str] | None = None
    mode: str | None = None


class ApprovalDoc(StrictModel):
    v: Literal[1] = 1
    sid: str = Field(min_length=1)
    gate: dict[GateKey, bool]
    seen: dict[SeenKey, bool]
    deep: list[int] = Field(default_factory=list)
    writing: bool = False
    spawns: dict[GateKey, list[ApprovalAction]] = Field(default_factory=dict)

    @model_validator(mode='after')
    def _approval_state_is_complete(self) -> ApprovalDoc:
        if set(self.gate) != set(APPROVAL_GATES):
            raise ValueError('gate must contain exactly s0..s7')
        if set(self.seen) != set(APPROVAL_SEEN):
            raise ValueError('seen must contain exactly read/global')
        if self.deep != sorted(set(self.deep)) or any(n < 1 for n in self.deep):
            raise ValueError('deep must be sorted, unique positive chapter ordinals')
        return self


class ApprovalBackfillResult(StrictModel):
    """按现有产物补录 CLI/队列路径的验收链；`backfilled` 必须在 UI 明示。"""

    approvals: ApprovalDoc
    backfilled: list[GateKey] = Field(default_factory=list)


class DeepSelectionRequest(StrictModel):
    deep: list[int] = Field(default_factory=list)

    @model_validator(mode='after')
    def _selection_is_canonical(self) -> DeepSelectionRequest:
        if self.deep != sorted(set(self.deep)) or any(n < 1 for n in self.deep):
            raise ValueError('deep must be sorted, unique positive chapter ordinals')
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
# 二c、S-05 全局设定（`GET /api/novels/:sid/stage2/global`）
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
    写在 `SPOILER_DISCLOSURE['fields']` 里），服务端不做第二把尺子。
    """

    char_id: str = Field(min_length=1)
    canonical: str = Field(min_length=1)
    name_distinctiveness: Literal[NAME_DISTINCTIVENESS]
    role: str = Field(min_length=1)
    fate: str | None
    address: list[GlobalAddressLine] = Field(min_length=1)


class GlobalRelationLine(FrozenModel):
    """一条有向关系（两端已解引用）。"""

    relation_id: str = Field(min_length=1)
    subject: NameRef
    predicate: str = Field(min_length=1)
    object: NameRef
    valid_from_chapter: int = Field(ge=1)
    valid_to_chapter: int | None


class GlobalSecretLine(FrozenModel):
    """一条秘密。⚠ **没有 `reader_knows_by_now`**——那是「相对当前章」的量，
    而本屏是全书视图，没有「当前章」；揭示章原样给，让人自己对。
    """

    secret_id: str = Field(min_length=1)
    fact: str = Field(min_length=1)
    known_by: list[NameRef] = Field(min_length=1)
    setup_chapters: list[int]
    reader_reveal_chapters: list[int] = Field(min_length=1)


class ArcTrackLine(FrozenModel):
    """一条人物弧线（`subject` 已解引用；`char_id` / `reader` / `relation_id` 三类）。"""

    subject: NameRef
    dimension: str = Field(min_length=1)
    change_type: str = Field(min_length=1)
    trajectory: str = Field(min_length=1)


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

    #: 全屏解引用失败的总数（relations 两端 + secrets.known_by + arc.subject）。
    #: **读数不是判定**——它是给人看「这份产物里有几个说不出名字的 id」。
    unresolved_refs: int = Field(ge=0)

    invalid_exists: bool
    freshness: Freshness
    reason: FreshnessReason
    ghosts: list[str] = Field(default_factory=list)

    provenance: GlobalProvenance = Field(alias='_provenance')


class GlobalReviewView(StrictModel):
    """Current-subject status plus the auditable Stage2 review rows.

    ``approved`` is recomputed by the read model.  It is never trusted from the
    persisted review alone, because the current ``GlobalGrounding`` or required
    review surface may have changed since a person decided the rows.
    """

    source_sha256: str = Field(pattern=r'^[0-9a-f]{64}$')
    current_subject_sha256: str = Field(pattern=r'^[0-9a-f]{64}$')
    current: bool
    approved: bool
    items: list[GlobalReviewItem]


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


class ReadingBeatLine(FrozenModel):
    """一拍。`located=false` 时坐标为 `null`（定位失败不编区间）。"""

    beat_id: str = Field(min_length=1)
    located: bool
    start: int | None
    end: int | None
    time_layer: Literal[TIME_LAYERS]
    place: str | None
    present: list[str]
    event: str = Field(min_length=1)
    hinges: list[ReadingHinge]

    @model_validator(mode='after')
    def _location_is_all_or_nothing(self) -> ReadingBeatLine:
        if self.located != (self.start is not None and self.end is not None):
            raise ValueError('located 与坐标必须说同一件事')
        return self


class ReadingNarrationLine(FrozenModel):
    grammatical_person: Literal[NARRATIVE_PERSON]
    narrator: str | None
    focalizer: str | None
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
    #: `blueprint=false` ⇒ `null`（没有蓝图就没有摘要，不编一句）。
    summary: str | None

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
            if self.beats or self.summary is not None or self.provenance is not None:
                raise ValueError('blueprint=false 时右半边必须整块缺席——那份内容是从哪儿来的？')
            if any(s.beat_id is not None for s in self.segments):
                raise ValueError('blueprint=false 时不该有 beat 分区')
        holes = sum(s.chars for s in self.segments if s.beat_id is None)
        if self.loose != holes:
            raise ValueError(f'loose={self.loose} 而空隙合计 {holes}——两个数必须是同一个')
        return self


# ─────────────────────────────────────────────────────────────────────────────
# 二g、S-07 计划 ↔ 写作上下文（`GET /api/novels/:sid/stage4/:o`）
# ─────────────────────────────────────────────────────────────────────────────

class PlanBeatLine(FrozenModel):
    """计划的一拍。`budget_chars=null` 只出现在恒等基线上（算不出源字数的拍，
    **不估算、不拿邻居补**）。"""

    i: int = Field(ge=1)
    slot: str = Field(pattern=r'^s\d{2,4}$')
    from_beats: list[str] = Field(min_length=1)
    budget_chars: int | None
    narrative_role: Literal[STRUCTURAL_ROLES]
    delivery: Literal[DELIVERY_MODES]
    transition: Literal[TEMPORAL_LINKS]

    #: 本拍的主干是 `events[]` 里的第几条。`null` = 这份计划没做过主次决策
    #: （恒等基线就是它，与 `budget_chars=null` 同一种「基线什么都没决定」）。
    #: ⚠ 它与上面三个枚举是**同一批东西**——模型在拍这一层做的决策。
    #: 那三个在这里而它不在，等于让审阅面与决策面分家。
    spine: int | None = Field(default=None, ge=1)


class PlanChapterLine(StrictModel):
    """计划侧的一章。恒等基线时三个 goal 是显式标记（`IDENTITY_GOAL`），不是真决策。"""

    output_ordinal: int = Field(ge=1)
    from_source_ordinals: list[int] = Field(min_length=1, max_length=1)
    chapter_goal: PlanGoal
    open_hook_goal: PlanGoal
    end_hook_goal: PlanGoal
    beats: list[PlanBeatLine] = Field(min_length=1)


class Stage4IndexRow(FrozenModel):
    """章索引一行。⚠ **没有 `in_band` / `style_hits`**——那是判据结论，
    唯一来源是 `/check/stage4`（`R-11.4`），前端要并排显示就去 join 那份报告。"""

    output_ordinal: int = Field(ge=1)
    #: 深度臂选择的首要决策依据。来自同一份 `plan_chapters.json`，随索引一次下发，
    #: 不让前端为了 N 章再发 N 次章级请求。
    chapter_goal: str = Field(min_length=1)
    budget_chars: int | None
    span_chars: int | None
    beats: int = Field(ge=1)
    #: 本章不许泄的 secret 数。`null` = ctx 不在盘上，**判不了**（不编 0）。
    hidden: int | None


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
    reconstruction_strategy: str = Field(min_length=1)
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
    #: `CtxBeat.budget_chars` / `CtxChapter.budget_chars` 现在都是 `Field(gt=0)` 必填，
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
        if (self.writer_target is None) != (self.ctx_chapter is None):
            raise ValueError('writer_target 与 ctx_chapter 必须同进同出（同一份任务书）')
        return self


# ─────────────────────────────────────────────────────────────────────────────
# 二h、S-08 成稿 ↔ 材料（`GET /api/novels/:sid/stage5/:o`）
# ─────────────────────────────────────────────────────────────────────────────

class MaterialView(StrictModel):
    """写手拿到的下发面。**与写手同一份**（`prompts.build_material(...).blocks`，
    `R-11.8` 在读屏上的对应物：显示的材料 = 真发出去的材料，不是重新组装的第二份）。

    ⚠ 按**闭卷**臂组装（`with_source_text=False`）：开卷臂多出的那块就是源章原文，
    而原文在本屏单独有落点（`source_text`），不重复下发。
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
    """一份成稿卡（正式或 `.INVALID`，由 `invalid` 区分）。"""

    mode: Literal[DEPTH_MODES]
    relpath: str = Field(min_length=1)
    invalid: bool
    round: int = Field(ge=1)
    #: `human`（`--apply-edit` 的人工稿）或 `llm`。由 `_provenance` 的载体类派生。
    origin: str = Field(min_length=1)
    chars: int = Field(ge=0)
    budget_chars: int | None
    source_chars: int = Field(ge=0)
    overlap_excl_verbatim: float | None
    #: 盘上 schema v2 的唯一内容权威；`text` 只是下面的确定性整章投影。
    segments: list[ChapterSegment] = Field(min_length=1)
    text: str = Field(min_length=1)

    #: `null` = 对齐辅助尚未由服务端提供（本章无锚可对时 rows 为空、账仍在）。
    align: DraftAlignView | None
    align_rows: list[DraftAlignRow]

    freshness: Freshness
    reason: FreshnessReason

    provenance: RoundLlmProvenance | RoundFreeProvenance = Field(
        alias='_provenance', union_mode='left_to_right')

    @model_validator(mode='after')
    def _text_is_the_segment_projection(self) -> DraftView:
        projected = '\n\n'.join(segment.text.strip() for segment in self.segments).strip()
        if self.text != projected:
            raise ValueError('draft text 必须是 segments 的确定性投影')
        return self


class Stage5IndexRow(FrozenModel):
    """章索引一行。`chars` / `overlap` / `freshness` 取**主稿**（deep 优先）；
    没有成稿的章三格全 `null`。"""

    output_ordinal: int = Field(ge=1)
    budget_chars: int | None
    modes: list[Literal[DEPTH_MODES]]
    invalid_modes: list[Literal[DEPTH_MODES]]
    dup: bool
    chars: int | None
    overlap_excl_verbatim: float | None
    freshness: Freshness | None


class Stage5View(StrictModel):
    """`GET /api/novels/:sid/stage5/:o`。**S-08：这一章写出来了吗？守住材料了吗？**

    ⚠ `ctx_exists=false` ⇒ `materials=null`（**不显示假材料**，`04_delivery.md:134`）
    ——给「先跑 stage4」，左半边缺席、右半边照常列已有成稿。
    ⚠ 某章两臂皆无稿 ⇒ 200 且 `drafts=[]`（「还没写」不是错误）。
    """

    sid: str = Field(min_length=1)
    title: str = Field(min_length=1)

    ctx_exists: bool
    #: `null` = 计划读不回来，「该有几章」判不了（与 0 是两件事）。
    planned: int | None
    written: int = Field(ge=0)
    invalid: int = Field(ge=0)
    missing: list[int]
    duplicates: list[int]

    chapters: list[int] = Field(min_length=1)
    ordinal: int = Field(ge=1)
    index: list[Stage5IndexRow]

    source_text: str | None
    source_chars_raw: int | None
    materials: MaterialView | None
    drafts: list[DraftView]
    invalid_drafts: list[DraftView]

    ctx_state: ArtifactState

    @model_validator(mode='after')
    def _postconditions(self) -> Stage5View:
        if self.ordinal not in self.chapters:
            raise ValueError(f'ordinal={self.ordinal} 不在 chapters={self.chapters} 里')
        if not self.ctx_exists and self.materials is not None:
            raise ValueError('ctx 不在盘上而 materials 有内容——假材料（`04_delivery.md:134`）')
        return self


# ─────────────────────────────────────────────────────────────────────────────
# 二i、S-09 验收 / 修订（`GET /api/novels/:sid/review/:o`）
# ─────────────────────────────────────────────────────────────────────────────

class ReviewIndexRow(FrozenModel):
    """章索引一行。`finding_counts` 四 sev 键**恒在**（0 也写）——
    产物侧 `Review.counts` 是稀疏的（登记过的病），出线侧修掉。"""

    output_ordinal: int = Field(ge=1)
    mode: Literal[DEPTH_MODES] | None
    round: int | None
    chars: int | None
    budget_chars: int | None
    finding_counts: dict[str, int]
    #: 有收敛账（`unresolved_rounds > 0`）且未豁免的结论条数。
    unresolved: int = Field(ge=0)
    #: 这一章：验收判的那份与盘上当前那份还是不是同一份（整文件指纹）。
    #: `null` = 判不了（验收后成稿被移走 / 本章当时就无稿）。
    stale: bool | None


class ReviewChapterView(StrictModel):
    """一章的验收视图 + 当前正式稿正文（改稿入口要在原文旁边）。"""

    output_ordinal: int = Field(ge=1)
    findings: list[Finding]
    mode: Literal[DEPTH_MODES] | None
    chars: int | None
    budget_chars: int | None
    round: int | None
    origin: str | None
    segments: list[ChapterSegment] | None
    text: str | None
    #: 盘上已归档的轮次号（`_rounds/`），升序。改稿历史从这里回看。
    rounds: list[int]
    stale: bool | None

    @model_validator(mode='after')
    def _text_is_the_nullable_segment_projection(self) -> ReviewChapterView:
        if self.segments is None:
            if self.text is not None:
                raise ValueError('review chapter 无 segments 时 text 必须为 null')
            return self
        projected = '\n\n'.join(segment.text.strip() for segment in self.segments).strip()
        if self.text != projected:
            raise ValueError('review chapter text 必须是 segments 的确定性投影')
        return self


class ReviewView(StrictModel):
    """`GET /api/novels/:sid/review/:o`。**S-09：最新一份全书验收 + 该章明细。**

    一份验收都没有 ⇒ 404（`UpstreamMissing`，前端渲染「先跑 stage5.review」）。
    ⚠ 判据表本身仍归 `/check/stage5`；本屏发的是**验收产物**（历史事实），
    与「现在复检一遍」是两件事。
    """

    sid: str = Field(min_length=1)
    title: str = Field(min_length=1)

    review_seq: int = Field(ge=1)
    sources: ReviewSources
    #: 全书（chapters+book 全部结论）按 sev 现数，四键恒在。
    #: ⚠ 不读产物的 `counts` 字段——那份是稀疏 Counter（`Review.counts` 的登记病）。
    finding_counts: dict[str, int]
    baselines: dict[str, str]
    baselines_identity: dict[str, str]
    book: list[Finding]
    wontfix: int = Field(ge=0)

    chapters: list[int] = Field(min_length=1)
    ordinal: int = Field(ge=1)
    index: list[ReviewIndexRow] = Field(min_length=1)
    chapter: ReviewChapterView

    provenance: ReviewProvenance = Field(alias='_provenance')

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
        return self


# ─────────────────────────────────────────────────────────────────────────────
# 二j、人工写回的请求体（三条 0 成本路：改稿 / 文案定稿 / 人名定稿）
# ─────────────────────────────────────────────────────────────────────────────

class DraftEditRequest(StrictModel):
    """`POST /api/novels/:sid/review/:o/edit` —— 章节改稿。

    服务进程把 typed `segments` 落成 `_service/edits/` 下的一次性人工稿（文件名带 `ch{o}`，
    `stage5.write --apply-edit` 从文件名认章），CLI 子进程走**与生成稿同一个判定落点**
    （`run_apply_edit`：归一 → 判据 → 归档 → 写回，`origin=human` 记账）。

    ⚠ **没有乐观锁**（`stage5.write --apply-edit` 无对应机制）：并发改稿以
    `archive_round` 的整份归档兜底——覆盖的代价是多花一轮归档，不是丢稿。
    登记为已知限制，不在 API 层造第二套锁。
    """

    mode: Literal[DEPTH_MODES]
    segments: list[ChapterSegment] = Field(min_length=1)

    @model_validator(mode='after')
    def _segment_slots_are_unique(self) -> DraftEditRequest:
        slots = [segment.slot for segment in self.segments]
        if len(slots) != len(set(slots)):
            raise ValueError('人工稿 segment slot 不得重复')
        return self


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

    @model_validator(mode='after')
    def _audit_text_is_not_blank(self) -> 'HookApproveRequest':
        if not self.actor.strip() or not self.reason.strip():
            raise ValueError('actor/reason 不得只有空白')
        return self


class NameMapApproveRequest(HashBoundHumanApproval):
    """Stage7 human approval bound to the exact decision surface."""

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

    #: 缺的是**哪一个**（`'novel'` / `'stage3/style_grounding.json'`）。
    #: `None` = 这次失败与某一份具体的东西无关。
    missing: str | None = None

    #: 下一步该做什么。`None` = 说不出来——**说不出来就别编一句**。
    #: 产地是异常自己的 `NovelError.hint`（`novel/api/app.py` 一处 `getattr` 转过来），
    #: **不在这一层从 `message` 里抠**：措辞不是契约（`Q-6`）。
    #: ⚠ 它与 `message` 分成两格正是为了让「这条失败有没有出路」**可判定**——
    #: 拼成一句之后，界面只能把整段话糊进正文，而人得自己从中文里找那条命令。
    hint: str | None = None
