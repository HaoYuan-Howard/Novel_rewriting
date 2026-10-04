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
    DropRatioWarning, EarlyCutWarning, ExternalFileRef,
    LongMarkerWarning, ManagedUploadRef, SourceRef,
    RemovedSegment, RequestedRemoval, Stage0Admission, Stage0Snapshot,
    Stage0Source, Stage0Transform, Stage0Warning,
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
# ⚠ F8a：`contracts.schema.stage6` / `stage6_prepare` 两个模块的 import 与
#   ARTIFACT_SPECS 挂载（八项）随 Stage6 整层退役删除（F6d 登记，F8c 删文件）。
#   Hook 的契约形状由 `contracts/schema/hook.py`（HookContext/HookReceipt）承接。
from contracts.schema.stage7 import (
    BookTxt, CharacterPatch, ExtraSub, IdentityBaseline, NameDecision, NameMap,
    NameMapEdit, NameMapResponse, Residual, StemBaseline,
)

# ── STR-06 新链契约模型（F8a 激活；F8c 分流至正式 stageN 模块后改 import）──
from contracts.schema.source import (
    BlueprintsArtifactV15, CharactersArtifactV4, CharactersCore,
)
from contracts.schema.observation import (
    STAGE2_ASSOCIATE_PROTOCOL, STAGE2_OBSERVATION_PROTOCOL, AssociateObservation,
    BookObservation, ChapterObservation, NamesObservation,
)
from contracts.schema.grounding import (
    PreserveCheckpoint, ReconstructionBrief, SemanticBinding,
)
from contracts.schema.execution import ExecutionPlan
from contracts.schema.design import (
    THREAD_ENDINGS, BodyDesign, ChapterAssignment, ChapterEntry,
    PresentationGraph, ThreadAction, ThreadKind,
)
from contracts.schema.hook import HookContext, HookReceipt
from contracts.schema.book_ir import WriterBookIR
from contracts.schema.receipt import ChapterReceipt
from contracts.schema.naming import NameMapV5

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
    STAGE0_REVISIONS: ArtifactSpec(Stage0Transform, 3),
    # v3（2026-08-25）：删除下游不应信任的 ``source_chapter_no``；原文章号由当前
    # clean_text 复扫，只在对照页显示。存量由 ``stage1.partition`` 免费重建；本次
    # Stage0～7 减法完成后，两本现役书统一从头重建，不做迁移或手补字段。
    STAGE1_MAIN_TEXT: ArtifactSpec(MainText, 3),
    STAGE1_HOOK_COPY: ArtifactSpec(HookCopy, 2),
    # ── STR-06 新链（F8a 激活；键即相对路径，见 contracts/artifacts.py）────────
    # 【版本口径】新链产物全部 current_version=1：三级路径是切换时新建的命名
    #   空间，盘上没有旧信封字节可读（旧路径已整体退役，见 RETIRED_ARTIFACT_PATHS）。
    #   形状破坏史不在信封上累加，而由两条线承载：① 模型自带 `contract_version`
    #   字面量（Characters v4／Brief／Binding／Plan／Graph／BookIR／HookContext／
    #   ChapterReceipt／NameMap v5，与候选产物表逐字对账）；② 候选产物表的
    #   `schema_version` RC 标签（rc2→rc3→rc4 的批次定格）。未声明
    #   `contract_version` 的模型（Blueprints v13）按 F8
    #   义务在本注册表登记形状（即本表条目自身），不进模型——往模型加非 exclude
    #   字段会改 `model_dump` 字节，从而改 prompt 与已购请求身份，F7a 的历史
    #   采用会当场失配（`_provenance` 用 exclude=True 正是为守住这条）。
    # Stage2：源语义（str06.stage2.source／names）
    # v13（2026-09-26 质量改进批次二）：拍模型改三类转移（effects／cognition／
    #   loops）＋场景边界与时间模式＋章蓝图场景分段；存量 v12 盘面明确拒读
    #   （SchemaVersionRefused），由新独立根重新抽取，不做迁移。
    # B2 五节点观察与关联协议（2026-09-27，D3/D8/D9）：协议版本由模型自带的
    # `protocol_version` 字面量承载（I-13 显式版本化，无运行时转换层）；联合
    # 抽取派生输入 `stage2/compiled/book_extraction.json` 已退役（D9）。
    'stage2/observations/book.json': ArtifactSpec(BookObservation, 1),
    'stage2/observations/ch{nn}.json': ArtifactSpec(ChapterObservation, 1),
    # CS-R：名称同一性必须由 names 留证声明；旧观察不能补空声明冒充已核验。
    # names 按新协议重建后，compile 免费重建 Characters，旧根保留证据不原地猜迁。
    'stage2/observations/names.json': ArtifactSpec(NamesObservation, 2),
    'stage2/observations/associate.json': ArtifactSpec(AssociateObservation, 1),
    'stage2/blueprints/chapter_blueprints.json': ArtifactSpec(BlueprintsArtifactV15, 1),
    'stage2/characters/characters.json': ArtifactSpec(CharactersArtifactV4, 2),
    # Stage4：目标故事与确定性编译（preserve／body_design／hook_design／bind／compile）
    'runtime/preserve_checkpoint.json': ArtifactSpec(PreserveCheckpoint, 1),
    'stage4/grounding/reconstruction_brief.json': ArtifactSpec(ReconstructionBrief, 1),
    'stage4/grounding/semantic_binding.json': ArtifactSpec(SemanticBinding, 1),
    'stage4/compiled/execution_plan.json': ArtifactSpec(ExecutionPlan, 1),
    'stage4/design/body_design.json': ArtifactSpec(BodyDesign, 1),
    'stage4/design/presentation_graph.json': ArtifactSpec(PresentationGraph, 1),
    'stage4/context/hook/hook.json': ArtifactSpec(HookContext, 1),
    'stage4/ir/writer_book_ir.json': ArtifactSpec(WriterBookIR, 1),
    # Stage5：按章正文与 Hook 回执（body／hook）
    'stage5/hook/receipt.json': ArtifactSpec(HookReceipt, 1),
    'stage5/acceptance/body/ch{nn}.json': ArtifactSpec(ChapterReceipt, 1),
    # Stage7：命名与交付（naming／assemble）
    'stage7/naming/name_map.json': ArtifactSpec(NameMapV5, 1),
    # ⚠ 自由形态派生记录（input_snapshots／fold／guard 两份／delivery_manifest）
    #   与三份正文（hook.txt／ch{nn}.txt／{title}_rewriting.txt）只登记路径
    #   （`contracts/artifacts.py::ARTIFACT_PATHS`），不进本表：它们的形状由唯一
    #   writer 的契约治理（同 `stage0/clean_text.txt` 的先例，见模块头），
    #   信封版本对「没有第二 Shape」的记录不携带信息。
}
ARTIFACTS: dict[str, type[BaseModel]] = {
    key: spec.model for key, spec in ARTIFACT_SPECS.items()
}


def artifact_regex(template: str) -> re.Pattern[str]:
    """把注册表模板编译成语料、运行时共用的相对路径路由。

    `{nn}`／`{n}` = 数字占位（章序、轮次）；`{title}` = 书级占位（F8a 新链成书
    `stage7/final/{title}_rewriting.txt`，非数字、按「这本书是谁」解析）。
    """
    escaped = re.escape(template)
    escaped = escaped.replace(r'\{nn\}', r'\d+')
    escaped = escaped.replace(r'\{n\}', r'\d+')
    escaped = escaped.replace(r'\{title\}', r'[^/]+')
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
    'Stage0Snapshot', 'Stage0Source', 'Stage0Transform', 'Stage0Admission',
    'RemovedSegment', 'RequestedRemoval', 'Stage0Warning',
    'EarlyCutWarning', 'DropRatioWarning', 'LongMarkerWarning',
    'SourceRef', 'ExternalFileRef', 'ManagedUploadRef',
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
    # B2 观察与关联协议（contracts/schema/observation.py）
    'AssociateObservation', 'BookObservation', 'ChapterObservation',
    'NamesObservation', 'STAGE2_ASSOCIATE_PROTOCOL', 'STAGE2_OBSERVATION_PROTOCOL',
]
