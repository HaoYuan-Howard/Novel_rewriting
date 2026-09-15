#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
contracts.schema —— 产物 schema 的机器形态。

**`docs/schema/*.md` 是源，这里的模型照它写。方向别搞反。**
`docs/schema/_common.md` ↔ `contracts/schema/common.py`。

除便利 re-export 外，本包只在产物 IO 边界做路径路由与当前 schema version 编解码；
当前 Pydantic 模型与盘上载体都只保持一种形状。
`contracts/invariants.py` 仍是契约叶子（只 import 标准库与更底层的路径目录），
它不知道 pydantic 的存在。

【`ARTIFACTS`：相对路径 → 模型，唯一落点】
盘上语料体检、`/api/novels/:sid/...` 的响应类型、将来 `tools/emit_json_schema.py`
产前端 TS 类型，三个消费方读的是**同一张表**。手抄第二份的下场见 `Q-6`。

【版本：没有迁移机制；除显式登记的破坏外全表恒 v1】
⚠⚠ 这里曾有一梯 v2→v3 migration（5 张 `MIGRATE_*` 表 + 6 个迁移函数），删除那天的实测：
**登记了 migration 的 8 个家族在盘上一份低版本文件都没有**，而盘上唯二的 v2 文件
（两本书的 `stage6/semantic_1.json`）恰好属于**刻意不登记 migration** 的那一族
⇒ 整条梯子零消费者，却让「只接受当前版本」这句写在入口文档里的话假了一整段时间。
判准是**谁来重建**：答得出「从 producer 重跑」就不该有迁移；答不出，那才是真兼容层，
而它必须同批写下重建的成本与授权。
⇒ 要破坏某个产物的形状，做法是把该路径的 `current_version` +1，**并在同一批里回答
「存量产物由谁重建」**，不是加一条迁移把旧字节读成新形状。
"""
from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Any

from pydantic import BaseModel

from contracts.artifacts import (
    RETIRED_ARTIFACT_PATHS,
    STAGE0_REVISIONS, STAGE1_HOOK_COPY, STAGE1_MAIN_TEXT,
    STAGE2_ANALYZE_RECEIPT, STAGE2_CHARACTERS, STAGE2_CHARACTERS_EDIT,
    STAGE2_BLUEPRINT_CHARACTER_PROJECTION, STAGE2_BLUEPRINT_EDIT,
    STAGE2_CHAPTER_BLUEPRINTS, STAGE2_KNOWLEDGE_LINKS, STAGE2_GLOBAL_EDIT, STAGE2_GLOBAL_EDIT_COMMIT,
    STAGE2_GLOBAL_EDIT_INPUT_COMMIT,
    STAGE2_GLOBAL_EDIT_FAILURE,
    STAGE2_GLOBAL_GROUNDING, STAGE2_GLOBAL_REVIEW, STAGE2_GLOBAL_REVIEW_DECISIONS,
    STAGE4_BLIND_CANDIDATE, STAGE4_BLIND_CHAPTER_EDIT, STAGE4_EVIDENCE_BINDING,
    STAGE4_PLAN_CHAPTERS, STAGE4_PLAN_EDIT, STAGE4_WRITER_CONTEXT,
    STAGE5_CHAPTER, STAGE5_REJECTED_REVISION,
    STAGE5_REVIEWER_REWRITE_REQUEST,
    STAGE5_REVIEW, STAGE5_TRIM_AUDIT, STAGE5_TRIM_RESOLUTION,
    STAGE5_WONTFIX, STAGE6_HOOK,
    STAGE6_HOOK_EDIT, STAGE6_WONTFIX, STAGE7_NAME_MAP,
    STAGE7_NAME_MAP_EDIT,
)

from contracts.schema.api import (
    ApprovalAction,
    ApprovalBackfillResult,
    ApprovalDoc,
    BatchTrimResolutionItem,
    BatchTrimResolutionRequest,
    Stage5ReviewerRewriteDirectiveInput,
    Stage5ReviewerRewriteTarget,
    Stage5ReviewerRewriteRequest,
    DraftEditPreview,
    DraftEditPreviewRequest,
    DraftEditRequest,
    ErrorBody,
    InboxItem,
    InboxView,
    JobList,
    LogChunk,
    LogStreamEvent,
    NameMapApproveRequest,
    NovelList,
    PreviewRequest,
    Stage0EditRequest,
    Stage0EditResult,
    Stage0ApprovalRequest,
    Stage0Report,
    Stage0WarningView,
    Stage0CandidateDecisionRequest,
    Stage0CandidateRevisionView,
    Stage0CandidateView,
    Stage0Segment,
    Stage0Segments,
    Stage1Segment,
    Stage1View,
    Stage5ChapterSubject,
    Stage5TrimWorkItem,
    ResidualAdoptionRequest,
    TrimResolutionRequest,
    UploadResult,
)
from contracts.schema.base import Fp10, FrozenModel, Sha1, StrictModel
from contracts.schema.characters import CharacterRegistry, CharactersEdit
from contracts.schema.analyze import AnalyzeReceipt
from contracts.schema.analyze_recovery import AnalyzeRecoveryJournal
from contracts.schema.analyze_draft import AnalyzeOwnerDraftEdit, AnalyzeDraftRepairJournal
from contracts.artifacts import (
    STAGE2_ANALYZE_OWNER_DRAFT_EDIT, STAGE2_ANALYZE_CHARACTERS_DRAFT_REPAIR,
    STAGE2_ANALYZE_GLOBAL_DRAFT_REPAIR,
)
from contracts.artifacts import STAGE2_ANALYZE_RECOVERY
from contracts.schema.knowledge import KnowledgeGrounding
from contracts.schema.plot_edit import PlotEdit
from contracts.schema.common import (
    Finding,
    FreeProvenance,
    HashBoundHumanApproval,
    HumanApproval,
    InputEdge,
    LlmProvenance,
    Owner,
    Provenance,
    RevalidationAudit,
    RoundFreeProvenance,
    RoundLlmProvenance,
    Severity,
    Usage,
    WontFix,
    WontFixEntry,
    WontFixNote,
)
from contracts.schema.corpus import (
    CorpusArtifactEntry, CorpusBookEntry, CorpusLedgerSummary, CorpusManifest,
    CorpusSkipExpectation, LedgerTokenSummary,
)
from contracts.schema.evaluation import (
    EvidenceRef,
    TrimAudit, TrimResolution,
)
from contracts.schema.report import (
    AccountingLine,
    Badge,
    CheckKind,
    CheckReport,
    EdgeLine,
    FixRoute,
    ProvenanceSection,
    Remediation,
    RuleLine,
    Status,
    StatusCounts,
    badge_of,
    finding,
)
from contracts.schema.stage0 import (
    ApprovedApproval, DropRatioWarning, EarlyCutWarning, ExternalFileRef,
    LongMarkerWarning, ManagedUploadRef, PendingApproval, SourceRef,
    Stage0Approval,
    RemovedSegment, RequestedRemoval, Stage0Snapshot, Stage0Source,
    Stage0CandidatePlan, Stage0Current, Stage0DismissedCandidate,
    Stage0EditPlan, Stage0Revision, Stage0RevisionContent, Stage0Revisions,
    Stage0Warning,
)
from contracts.schema.stage1 import HookCopy, MainText, MainTextSection
from contracts.schema.stage2 import (
    Address, Canon,
    BlueprintCharacterProjection, BlueprintCharacterProjectionChapter,
    ChapterBlueprints, Character,
    CharacterRef, CharacterResponse,
    Entities, EvidenceLocator,
    FactEvidence, GlobalEdit,
    CharacterCollectionItem, GlobalCollectionAdd, GlobalCollectionDelete,
    GlobalCollectionEdit, GlobalCollectionSplit,
    GlobalEditCommit, GlobalEditCommitBaseline, GlobalEditCommitTarget,
    GlobalEditInputCommit,
    GlobalEditFailure, GlobalEditQuarantineGroup, GlobalEditReviewAttestation,
    GlobalEditReviewTarget,
    GlobalGrounding,
    GroupRef, KnowledgeEvent, KnowledgeEventResponse,
    Relation, RelationPatch, RelationVisibility,
    Secret, SecretPatch, Setting, SettingPatch, SourceSpan, WorldRule,
    WorldRulePatch, WorldRuleVersion,
)
from contracts.schema.stage4 import (
    BlindCandidate, BlindChapterEditRequest, CtxChapter, CtxGoal, CtxScene,
    EvidenceBinding, PlanChapter, PlanChapters, PlanEdit, PlanGoal,
    PlanScene, WriterContext,
)
from contracts.schema.stage5 import (
    Chapter, ChapterResponse, ChapterSegment, DraftConsumed,
    JudgedDraft, PreparedRecoveryAction, PreparedRecoveryChapter,
    PreparedRecoveryJournal, PreparedRecoveryManifest, PreparedRecoveryWorkpack,
    RejectedAttempt, RejectedRevision, Review, ReviewChapter,
    ReviewerRewriteDirective, ReviewerRewriteDisposition,
    ReviewerRewriteExecutionJournal, ReviewerRewriteRecoveryJournal,
    ReviewerRewriteSuccessorJournal,
    ReviewerRewriteExecutionState, ReviewerRewriteProbe,
    ReviewerRewriteRequest, ReviewerRewriteSubject,
    ReviewerRewriteBatchRequest, ReviewerRewriteBatchTrustedInput, ReviewerRewriteTarget,
    ReviewerRewriteTrustedInput, reviewer_rewrite_execution_journal_sha256,
    reviewer_rewrite_disposition_sha256,
    reviewer_rewrite_recovery_journal_sha256,
    reviewer_rewrite_successor_journal_sha256,
    reviewer_rewrite_request_sha256,
)
from contracts.schema.stage6 import (
    ApprovedHook, Candidate, Hook, HookApprovalResolution, HookEdit,
    Mech,
)
from contracts.artifacts import (STAGE6_PREPARE, STAGE6_PREPARE_EDIT, STAGE6_PREPARE_REVIEW,
    STAGE6_PREPARE_REVIEW_DECISIONS, STAGE6_PREPARE_COMMIT)
from contracts.schema.stage6_prepare import (Stage6Prepare, PrepareEdit, Stage6PrepareReview,
    PrepareDecision, PrepareMutationJournal)
from contracts.schema.stage7 import (
    BookTxt, CharacterPatch, ExtraSub, IdentityBaseline, NameDecision, NameMap,
    NameMapEdit, NameMapResponse, Residual, StemBaseline,
)

class ArtifactVersionError(ValueError):
    """一份产物不是当前注册版本。错误恒带文件与版本。

    ⚠ **六个抛出点不是同一件事**，而消费方分得出它们才敢做不同处置：
    「盘上是 v2、当前是 v3」（旧产物，producer 重建即可）与「非法 marker」
    「envelope 形状坏了」「版本比当前还高」是**完全不同的失败**。
    ⇒ 版本不匹配那一处填 `found` / `expected`，其余保持 `None`。
    消费方只许按 `is_older_than_current` 放宽，**不许按类型放宽**——
    宽捕获整族会让一份 `_schema_version: 999` 的坏产物被当成旧 v2 静默跳过。
    """

    def __init__(self, message: str, *, rel: str | None = None,
                 found: int | None = None, expected: int | None = None) -> None:
        super().__init__(message)
        self.rel = rel
        self.found = found
        self.expected = expected

    @property
    def is_older_than_current(self) -> bool:
        """盘上那份**确实是一个更早的注册版本**（不是坏信封、不是未来版本）。"""
        return (self.found is not None and self.expected is not None
                and self.found < self.expected)


@dataclass(frozen=True)
class ArtifactSpec:
    """一类盘上 JSON 的当前版本与模型。

    ⚠ 这里曾有第三格 `migrations`（低版本 → 当前版本的迁移链）。删除的理由见模块头
    【版本】那一段：它保护的不是存量产物，是**没人重建过的**存量产物。
    `current_version` 保留为可写，是因为它仍要承担「这个形状破坏过几次」这件事——
    `stage2/chapter_blueprints.json` 已多次破坏形状（见版本账）。
    """

    model: type[BaseModel]
    current_version: int = 1

    @property
    def root(self) -> bool:
        return bool(getattr(self.model, '__pydantic_root_model__', False))


@dataclass(frozen=True)
class DecodedArtifact:
    payload: Any
    version: int


#: **产物相对路径 → 当前契约。** 路径里的 `{n}` / `{nn}` 是占位符。
#: 这是路径、模型与 schema version 的唯一权威表；`ARTIFACTS` 是模型投影。
#:
#: ⚠ 这里**不含** `stage0/clean_text.txt` / 成书 `stage7/{title}_rewriting.txt`
#: ——它们不是 JSON，schema 是**字节形态**，守卫在写盘口那一层。
#:
#: ⚠ **一条 `migrations=` 都没有**，将来也不许有。这不是「还没来得及写」，
#: 是模块头【版本】那一段的裁决：破坏形状要 `current_version` +1 并同批回答
#: 「存量产物由谁重建」。反向守卫在 `tests/test_artifact_versions.py`。
#:
#: ⚠ **例外逐条在下面自己那一行写着理由与重建人**：
#: `STAGE2_CHAPTER_BLUEPRINTS`（2026-08-19、2026-08-23）与 2026-08-22 阶段 B 那一批。
#: **升版的正确形态就是这样**——在表里显式登记，而不是加一条迁移把旧字节读成新形状。
ARTIFACT_SPECS: dict[str, ArtifactSpec] = {
    # v2（2026-08-25）：原文身份提到根部；revision 只留正文与最小剔除账。
    # 存量由 `stage0.remove` 免费重建并人工重批，不做迁移。
    STAGE0_REVISIONS: ArtifactSpec(Stage0Revisions, 2),
    # v3（2026-08-25）：删除下游不应信任的 ``source_chapter_no``；原文章号由当前
    # clean_text 复扫，只在对照页显示。存量由 ``stage1.partition`` 免费重建；本次
    # Stage0～7 减法完成后，两本现役书统一从头重建，不做迁移或手补字段。
    STAGE1_MAIN_TEXT: ArtifactSpec(MainText, 3),
    STAGE1_HOOK_COPY: ArtifactSpec(HookCopy, 2),
    # 联合首抽只新增来源回执；人物与知识分别编译/存储，不继承旧版本审批。
    STAGE2_ANALYZE_RECEIPT: ArtifactSpec(AnalyzeReceipt),
    STAGE2_ANALYZE_RECOVERY: ArtifactSpec(AnalyzeRecoveryJournal),
    STAGE2_ANALYZE_OWNER_DRAFT_EDIT: ArtifactSpec(AnalyzeOwnerDraftEdit),
    STAGE2_ANALYZE_CHARACTERS_DRAFT_REPAIR: ArtifactSpec(AnalyzeDraftRepairJournal),
    STAGE2_ANALYZE_GLOBAL_DRAFT_REPAIR: ArtifactSpec(AnalyzeDraftRepairJournal),
    STAGE2_CHARACTERS: ArtifactSpec(CharacterRegistry, 2),
    STAGE2_CHARACTERS_EDIT: ArtifactSpec(CharactersEdit, 2),
    STAGE2_GLOBAL_GROUNDING: ArtifactSpec(KnowledgeGrounding, 7),
    # ⚠ 人产的 Stage2 typed 订正决策集（`stage2.global --apply-edit` 吃它）——
    #   与下面三份 `*_edit.json` 同一类：不是节点产的，但落在产物目录、
    #   被 `store.read` 当上游边记账 ⇒ 形状同样是契约。
    # v6 编辑四类 Core；旧输入免费重发当前模板，不迁移已退役场景事实与文案字段。
    STAGE2_GLOBAL_EDIT: ArtifactSpec(GlobalEdit, 6),
    # v1：canonical reviewed edit 被拒后由 ``stage2.global --apply-edit`` 免费
    # 重建；它是 transient correction fact，不进入 NodeSpec.produces。
    STAGE2_GLOBAL_EDIT_FAILURE: ArtifactSpec(GlobalEditFailure),
    # v1：canonical sparse input + pending/superseded failure 的 transient WAL。
    STAGE2_GLOBAL_EDIT_INPUT_COMMIT: ArtifactSpec(GlobalEditInputCommit, 2),
    # v1：Stage2 Global 正式三件套的 transient prepared/committed journal；
    # reviewed 额外绑定 failure/workpack，generic INVALID 提升可显式表达没有
    # action-owned edit identity / formal baseline；不进入 NodeSpec.produces。
    STAGE2_GLOBAL_EDIT_COMMIT: ArtifactSpec(GlobalEditCommit, 3),
    # v2：由独立人物登记免费物化；缺/坏时由人物 owner 免费重验重建。
    STAGE2_BLUEPRINT_CHARACTER_PROJECTION: ArtifactSpec(
        BlueprintCharacterProjection, 2),
    # ⚠⚠ 2026-08-19 首次破坏：beat 区间改由**段号**算出，升 v2。
    #   2026-08-23 第二次破坏：`BlueprintContext.architecture` 从七值收窄为
    #   v1.1/v2/v3.1 三值，升 v3；2026-08-24 再收成 v2/v3.1 两值，升 v4。
    #   三次都会让旧信封**读不进来**。
    #   **存量产物由谁重建：`stage2.blueprint` 整跑（付费）**；`.INVALID` 与 `_rounds/`
    #   一律作废，不手补字段冒充当前产地。取值与升版记录见
    #   `contracts/invariants.py::BLUEPRINT_ARCHITECTURE`。
    # v6 删除摘要、人物显示名、重复父键与无消费者 provenance 账；由
    # `stage2.blueprint` 整跑（付费）重建，不迁移或手补。称名投影
    # 只改通用 `inputs` 的运行时键，没改 BlueprintProvenance 载体形状，不升版。
    STAGE2_CHAPTER_BLUEPRINTS: ArtifactSpec(ChapterBlueprints, 10),
    # v1：外部 hash-bound typed request；只被 CLI/API 读取，不是 producer 产物。
    STAGE2_BLUEPRINT_EDIT: ArtifactSpec(PlotEdit, 2),
    # v2 删除 goal rationale；A 仍保留重构策略与预算权重，供 B/编译器消费。
    # v3 退役整书拒稿采用及无架构来源；由新根内当前策略/单章 producer 重建。
    # v6 随 KnowledgeLinks 退役删除 PlannerKnowledge 与 knowledge 端点；
    # 存量由 stage4.design 重跑（付费）重建，不迁移。
    STAGE4_BLIND_CANDIDATE: ArtifactSpec(BlindCandidate, 6),
    # v1：外部 hash-bound 完整章替换请求；只被 Stage4A CLI/API 读取，
    # 不是 Executor 产物，也不进入正式产物展示面。
    STAGE4_BLIND_CHAPTER_EDIT: ArtifactSpec(BlindChapterEditRequest, 3),
    # v2 删除可由绑定舞台 present 唯一推出的 participant_refs。
    # v6 随 KnowledgeLinks 退役删除章级证据闭包中的 knowledge_events。
    STAGE4_EVIDENCE_BINDING: ArtifactSpec(EvidenceBinding, 6),
    # v4 删除 A 相过程字段、Stage2 重复契约、派生结构向量与编译参数快照。
    STAGE4_PLAN_CHAPTERS: ArtifactSpec(PlanChapters, 7),
    STAGE4_PLAN_EDIT: ArtifactSpec(PlanEdit, 7),
    # v5 删除文学 arc；由免费 context 节点从当前上游重建。
    # v6：场景知识与披露许可由独立知识关联导出；0 成本 stage4.context 重建。
    # v7 删除同号源章、总预算与源字数副本；仍由免费 context 节点重建。
    # v10 随 KnowledgeLinks 退役删除 WriterKnowledgeEvent 与逐场 knowledge_event_refs。
    STAGE4_WRITER_CONTEXT: ArtifactSpec(WriterContext, 10),
    # ⚠ 2026-08-24 单臂折平：删除 `mode`，路径改为
    #   `stage5/chNN.json`。存量双臂稿是只读历史证据，不做迁移；
    #   current 产物由 `stage5.write` 重建。
    STAGE5_CHAPTER: ArtifactSpec(Chapter, 3),
    STAGE5_REJECTED_REVISION: ArtifactSpec(RejectedRevision, 2),
    STAGE5_REVIEWER_REWRITE_REQUEST: ArtifactSpec(ReviewerRewriteBatchRequest, 2),
    # ⚠ Review 的章行同步删除 `mode/mode_dir`；由 `stage5.review`
    #   零成本重建，旧报告不伪装成当前判据产地。
    STAGE5_REVIEW: ArtifactSpec(Review, 3),
    # ⚠ trim 两份 hash-bound 产物删除目录臂字段，改绑
    #   canonical 章路径；新的超长章由写作/重判路径重建。
    STAGE5_TRIM_AUDIT: ArtifactSpec(TrimAudit, 3),
    STAGE5_TRIM_RESOLUTION: ArtifactSpec(TrimResolution, 3),
    # ⚠ 逐层一份账（2026-08-15 裁决）：**层由文件路径承载，不进收敛键**。
    #   两份共用一个模型——同一件事的第 N 个落点必然分家。
    STAGE5_WONTFIX: ArtifactSpec(WontFix),
    # v3 同槽可持续修订，attempt 按槽连续唯一；总内容预算仍属整书工作单元。
    # 旧 v2 只在冻结代码读取，新 clean base 由 Hook producer 重建，不迁移。
    STAGE6_PREPARE: ArtifactSpec(Stage6Prepare),
    STAGE6_PREPARE_EDIT: ArtifactSpec(PrepareEdit),
    STAGE6_PREPARE_REVIEW: ArtifactSpec(Stage6PrepareReview),
    STAGE6_PREPARE_REVIEW_DECISIONS: ArtifactSpec(PrepareDecision),
    STAGE6_PREPARE_COMMIT: ArtifactSpec(PrepareMutationJournal),
    STAGE6_HOOK: ArtifactSpec(Hook, 4),
    STAGE6_HOOK_EDIT: ArtifactSpec(HookEdit, 2),
    # v2 新增判官实际看见的 Stage5 开篇成稿 `judged`绑定；
    # 旧 v1 不能证明正文改字后语义结论仍然 current。
    STAGE6_WONTFIX: ArtifactSpec(WontFix),
    STAGE7_NAME_MAP: ArtifactSpec(NameMap, 3),
    STAGE7_NAME_MAP_EDIT: ArtifactSpec(NameMapEdit),
}
ARTIFACTS: dict[str, type[BaseModel]] = {
    key: spec.model for key, spec in ARTIFACT_SPECS.items()
}


def artifact_regex(template: str) -> re.Pattern[str]:
    """把注册表模板编译成语料、运行时共用的相对路径路由。"""
    escaped = re.escape(template)
    escaped = escaped.replace(r'\{nn\}', r'\d+')
    escaped = escaped.replace(r'\{n\}', r'\d+')
    return re.compile(f'^{escaped}$')


ARTIFACT_ROUTES: tuple[tuple[re.Pattern[str], str], ...] = tuple(
    (artifact_regex(key), key) for key in ARTIFACT_SPECS
)


def formal_artifact_rel(rel: str) -> str:
    """归档 / INVALID 路径映回其正式契约路径。"""
    normalized = str(rel).replace('\\', '/')
    normalized = normalized.replace('/_rounds/', '/')
    normalized = re.sub(r'\.r\d+(?=\.json$)', '', normalized)
    return normalized.replace('.INVALID.json', '.json')


def artifact_key(rel: str) -> str | None:
    formal = formal_artifact_rel(rel)
    return next((key for pattern, key in ARTIFACT_ROUTES if pattern.fullmatch(formal)), None)


def artifact_spec(rel: str) -> ArtifactSpec | None:
    key = artifact_key(rel)
    return ARTIFACT_SPECS.get(key) if key is not None else None


def _refuse_unfilled_template(rel: str) -> None:
    """带未填占位符的**模板**不是路径，一律当场拒绝。

    ⚠⚠ 这是 `docs/LESSONS.md` 第 23 条那个形状的**根治点**：`artifact_regex` 把 `{n}`
    编译成 `\\d+` ⇒ 模板自己匹配不上任何路由 ⇒ `artifact_spec()` 返回 `None`
    ⇒ 下面那句「未注册原样返回」把它**静默放过，一个异常都不抛**。
    于是任何一条拿模板写的「拒读」守卫都会全程绿着，却一次闸都没查到。
    ⇒ 闸装在这里，而不是要求每个调用点各自记得先把占位符替掉：
    真实文件路径不可能含 `{`，这一条不会误伤任何合法调用。
    """
    if '{' in str(rel):
        raise ArtifactVersionError(
            f'{rel}: 这是路径**模板**不是路径（占位符没填）。'
            f'模板匹配不上任何路由 ⇒ 会被当成「未注册」静默放行，'
            f'用它写的守卫会绿着却什么都没查。'
            f'→ 先用 `contracts/artifacts.py::artifact_rel` 填出具体路径')


def _refuse_retired_artifact(rel: str) -> None:
    """已登记退役的路径不能借未注册测试数据通道获得读写许可。"""
    if formal_artifact_rel(rel) in RETIRED_ARTIFACT_PATHS.values():
        raise ArtifactVersionError(
            f'{rel}: 产物路径已退役，不接受任何版本；旧字节只随旧代码与旧根配对读取')


def decode_artifact(rel: str, data: Any) -> DecodedArtifact:
    """拆出当前版本的逻辑 payload；未注册的测试数据原样返回。"""
    _refuse_unfilled_template(rel)
    _refuse_retired_artifact(rel)
    spec = artifact_spec(rel)
    if spec is None:
        return DecodedArtifact(data, 0)

    marker: Any = None
    payload = data
    if spec.root:
        if isinstance(data, dict) and '_schema_version' in data:
            marker = data.get('_schema_version')
            if set(data) != {'_schema_version', 'payload'}:
                raise ArtifactVersionError(
                    f'{rel}: root 产物 v{marker!r} 的 envelope 只能有 '
                    '`_schema_version` 与 `payload`')
            payload = data.get('payload')
    elif isinstance(data, dict) and '_schema_version' in data:
        marker = data.get('_schema_version')
        payload = {key: value for key, value in data.items() if key != '_schema_version'}

    if marker is None:
        raise ArtifactVersionError(
            f'{rel}: 缺少当前 schema v{spec.current_version} 标记；'
            '无版本旧产物不再兼容，请清理后从产地重新生成')
    version = marker
    if isinstance(version, bool) or not isinstance(version, int) or version < 0:
        raise ArtifactVersionError(f'{rel}: 非法 schema version {version!r}')
    # ⚠ **只有当前版本读得进来**，高低一视同仁：本仓没有迁移机制（模块头【版本】）。
    #   错误必须同时说出文件、盘上版本、当前版本与**下一步做什么**——
    #   「读不了」而不说从哪儿重建，等于把人推回去手补字段冒充当前产地。
    if version != spec.current_version:
        raise ArtifactVersionError(
            f'{rel}: schema v{version} 不是当前 v{spec.current_version}；'
            '本仓不迁移旧产物，请从 producer 重新生成'
            '（**不要手补字段冒充当前产地**）',
            rel=rel, found=version, expected=spec.current_version)
    return DecodedArtifact(payload, version)


def encode_artifact(rel: str, payload: Any) -> Any:
    """给当前逻辑 payload 加版本；RootModel 用 envelope，普通对象加顶层标记。"""
    _refuse_unfilled_template(rel)
    _refuse_retired_artifact(rel)
    spec = artifact_spec(rel)
    if spec is None:
        return payload
    if spec.root:
        return {'_schema_version': spec.current_version, 'payload': payload}
    if not isinstance(payload, dict):
        raise ArtifactVersionError(
            f'{rel}: {spec.model.__name__} 不是 RootModel，序列化结果却不是 object')
    return {'_schema_version': spec.current_version, **payload}

__all__ = [
    # 底座与共有部分
    'Fp10', 'Sha1', 'StrictModel', 'FrozenModel',
    'InputEdge', 'Usage', 'HumanApproval', 'HashBoundHumanApproval',
    'RevalidationAudit', 'Provenance', 'FreeProvenance', 'LlmProvenance',
    'RoundLlmProvenance', 'RoundFreeProvenance',
    'Finding', 'WontFixNote', 'WontFix', 'WontFixEntry', 'Severity', 'Owner',
    'CorpusArtifactEntry', 'CorpusBookEntry', 'CorpusLedgerSummary',
    'CorpusManifest', 'CorpusSkipExpectation', 'LedgerTokenSummary',
    'EvidenceRef',
    'TrimAudit', 'TrimResolution',
    # 校验报告（`_report.md`）——**不是产物**，所以不在 `ARTIFACTS` 里
    'CheckReport', 'RuleLine', 'ProvenanceSection', 'EdgeLine',
    'CheckKind', 'Remediation', 'FixRoute',
    'AccountingLine', 'StatusCounts', 'Status', 'Badge', 'finding', 'badge_of',
    # HTTP 响应（`_api.md`）——同样**不是产物**。它是前三份的视图，不产生新事实
    'Stage0Segment', 'Stage0Segments', 'Stage0EditRequest', 'Stage0EditResult',
    'Stage0ApprovalRequest', 'Stage0CandidateDecisionRequest', 'Stage0CandidateView',
    'Stage0CandidateRevisionView',
    'Stage1Segment', 'Stage1View', 'UploadResult',
    'NovelList', 'JobList', 'LogChunk', 'LogStreamEvent', 'PreviewRequest', 'ErrorBody',
    'ApprovalAction', 'ApprovalBackfillResult', 'ApprovalDoc',
    'InboxItem', 'InboxView', 'TrimResolutionRequest', 'NameMapApproveRequest',
    'BatchTrimResolutionItem', 'BatchTrimResolutionRequest',
    'Stage5ReviewerRewriteDirectiveInput', 'Stage5ReviewerRewriteTarget', 'Stage5ReviewerRewriteRequest',
    'DraftEditRequest', 'DraftEditPreviewRequest', 'DraftEditPreview',
    'Stage5ChapterSubject', 'Stage5TrimWorkItem',
    'ResidualAdoptionRequest',
    # 七个现役层（Stage3 已退役）
    'Stage0Snapshot', 'Stage0Source', 'RemovedSegment', 'RequestedRemoval', 'Stage0EditPlan',
    'Stage0CandidatePlan', 'Stage0Warning',
    'EarlyCutWarning', 'DropRatioWarning', 'LongMarkerWarning',
    'Stage0Approval', 'PendingApproval', 'ApprovedApproval',
    'SourceRef', 'ExternalFileRef', 'ManagedUploadRef',
    'Stage0Revisions', 'Stage0Revision', 'Stage0RevisionContent',
    'Stage0Current', 'Stage0DismissedCandidate',
    'MainText', 'MainTextSection', 'HookCopy',
    'CharacterRegistry', 'CharactersEdit', 'KnowledgeGrounding',
    'GlobalGrounding', 'Entities', 'Character', 'Address', 'Relation', 'Canon',
    'BlueprintCharacterProjection', 'BlueprintCharacterProjectionChapter',
    'Setting', 'WorldRule', 'WorldRuleVersion', 'Secret',
    'EvidenceLocator', 'FactEvidence', 'CharacterRef', 'GroupRef', 'KnowledgeEvent',
    'KnowledgeEventResponse', 'RelationVisibility',
    'GlobalEdit', 'GlobalEditCommit', 'GlobalEditCommitBaseline',
    'GlobalEditCommitTarget', 'GlobalEditFailure', 'GlobalEditInputCommit',
    'GlobalEditQuarantineGroup',
    'GlobalEditReviewAttestation',
    'GlobalEditReviewTarget', 'GlobalCollectionEdit', 'GlobalCollectionAdd',
    'GlobalCollectionDelete', 'GlobalCollectionSplit', 'CharacterCollectionItem',
    'SecretPatch', 'WorldRulePatch', 'RelationPatch', 'SettingPatch',
    'CharacterResponse',
    'ChapterBlueprints',
    'SourceSpan',
    'BlindCandidate', 'BlindChapterEditRequest', 'EvidenceBinding',
    'PlanChapters', 'PlanChapter', 'PlanScene', 'PlanGoal', 'PlanEdit',
    'WriterContext',
    'CtxChapter', 'CtxScene', 'CtxGoal',
    'Chapter', 'ChapterSegment', 'ChapterResponse',
    'PreparedRecoveryAction', 'PreparedRecoveryChapter',
    'PreparedRecoveryJournal', 'PreparedRecoveryManifest',
    'PreparedRecoveryWorkpack',
    'RejectedAttempt', 'RejectedRevision',
    'ReviewerRewriteDirective', 'ReviewerRewriteSubject',
    'ReviewerRewriteTrustedInput', 'ReviewerRewriteRequest',
    'ReviewerRewriteBatchRequest', 'ReviewerRewriteBatchTrustedInput', 'ReviewerRewriteTarget',
    'ReviewerRewriteExecutionJournal', 'ReviewerRewriteRecoveryJournal',
    'ReviewerRewriteSuccessorJournal',
    'ReviewerRewriteDisposition',
    'ReviewerRewriteExecutionState',
    'ReviewerRewriteProbe', 'reviewer_rewrite_request_sha256',
    'reviewer_rewrite_execution_journal_sha256',
    'reviewer_rewrite_disposition_sha256',
    'reviewer_rewrite_recovery_journal_sha256',
    'reviewer_rewrite_successor_journal_sha256',
    'Review', 'ReviewChapter', 'JudgedDraft', 'DraftConsumed',
    'Hook', 'Candidate', 'ApprovedHook', 'HookApprovalResolution', 'Mech',
    'HookEdit',
    'NameMap', 'NameDecision', 'ExtraSub', 'Residual', 'StemBaseline', 'BookTxt',
    'IdentityBaseline', 'NameMapEdit', 'CharacterPatch', 'NameMapResponse',
    # 路由表
    'ArtifactSpec', 'ArtifactVersionError', 'DecodedArtifact',
    'ARTIFACT_SPECS', 'ARTIFACTS', 'ARTIFACT_ROUTES',
    'artifact_regex', 'formal_artifact_rel', 'artifact_key', 'artifact_spec',
    'decode_artifact', 'encode_artifact',
]
