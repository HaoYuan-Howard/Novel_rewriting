#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
contracts.schema —— 产物 schema 的机器形态。

**`docs/schema/*.md` 是源，这里的模型照它写。方向别搞反。**
文件名一一对应：`docs/schema/stage3.md` ↔ `contracts/schema/stage3.py`，
`docs/schema/_common.md` ↔ `contracts/schema/common.py`。

除便利 re-export 外，本包只在产物 IO 边界做路径路由与当前 schema version 编解码；
当前 Pydantic 模型与盘上载体都只保持一种形状。
`contracts/invariants.py` 仍是契约叶子（只 import 标准库与更底层的路径目录），
它不知道 pydantic 的存在。

【`ARTIFACTS`：相对路径 → 模型，唯一落点】
盘上语料体检、`/api/novels/:sid/...` 的响应类型、将来 `tools/emit_json_schema.py`
产前端 TS 类型，三个消费方读的是**同一张表**。手抄第二份的下场见 `Q-6`。
"""
from __future__ import annotations

import re
from copy import deepcopy
from dataclasses import dataclass, field
from typing import Any, Callable, Mapping

from pydantic import BaseModel

from contracts.artifacts import (
    STAGE0_AUDIT_ENTRY, STAGE0_MACHINE_SNAPSHOT, STAGE0_MERGE_CANDIDATE,
    STAGE0_REMOVAL_REPORT, STAGE1_HOOK_COPY, STAGE1_MAIN_TEXT,
    STAGE1_PARTITION_REPORT, STAGE2_CHAPTER_BLUEPRINTS, STAGE2_GLOBAL_EDIT,
    STAGE2_GLOBAL_GROUNDING, STAGE2_GLOBAL_REVIEW, STAGE2_GLOBAL_REVIEW_DECISIONS,
    STAGE3_STYLE_EDIT,
    STAGE3_STYLE_GROUNDING,
    STAGE4_PLAN_CHAPTERS, STAGE4_PLAN_EDIT, STAGE4_WRITER_CONTEXT,
    STAGE4_WRITER_CONTEXT_IDENTITY, STAGE5_CHAPTER, STAGE5_REJECTED_REVISION,
    STAGE5_REVIEW, STAGE5_SEMANTIC, STAGE5_TRIM_AUDIT, STAGE5_TRIM_RESOLUTION,
    STAGE5_WONTFIX, STAGE6_HOOK,
    STAGE6_HOOK_EDIT, STAGE6_SEMANTIC, STAGE6_WONTFIX, STAGE7_NAME_MAP,
    STAGE7_NAME_MAP_EDIT,
)

from contracts.schema.api import (
    ApprovalAction,
    ApprovalBackfillResult,
    ApprovalDoc,
    DeepSelectionRequest,
    DriftItem,
    ErrorBody,
    HookCopyView,
    HookStyleLine,
    InboxItem,
    InboxView,
    JobList,
    LogChunk,
    LogStreamEvent,
    NameMapApproveRequest,
    NovelList,
    PreviewRequest,
    Stage0EditRequest,
    Stage0ApprovalRequest,
    Stage0MergeDecisionRequest,
    Stage0Segment,
    Stage0Segments,
    Stage1ChapterRow,
    Stage1Chapters,
    Stage1Segment,
    Stage1Segments,
    StyleView,
    ResidualAdoptionRequest,
    TrimResolutionRequest,
    UploadResult,
    VoiceLine,
)
from contracts.schema.base import Fp10, FrozenModel, Sha1, StrictModel
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
    CapabilityMetrics, Claim, EvaluatorFingerprint, EvaluatorProfile, EvidenceRef,
    GlobalReview, GlobalReviewDecision, GlobalReviewDecisionSet, GlobalReviewItem,
    GroundTruthCase, GroundTruthManifest,
    JudgeDecision, SemanticDecision, TrimAudit, TrimEvidenceSpan, TrimResolution,
    TrimJudgeResponse,
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
    MachineRemovalSnapshot, RemovalReport, RemovedSegment, Stage0AuditEntry,
    Stage0ConflictResolution, Stage0EditPlan, Stage0MergeCandidate,
    Stage0MergeConflict, Stage0MergeDecisionPlan, Stage0Warning,
)
from contracts.schema.stage1 import (
    HookCopy, MainText, MainTextSection, PartitionReport, Stage1Warning,
)
from contracts.schema.stage2 import (
    Address, AddressContextPatch, Arc, ArcTrack, Beat, Blueprint, Canon,
    ChapterBlueprints, ChapterBlueprintsResponse, Character, CharacterEnumPatch,
    CharacterAddressReplacement, ConflictStage, Entities, GlobalEdit, GlobalGrounding,
    GlobalGroundingResponse, Hinge, Narration, ReaderRevealEvent, Relation,
    RoleSplitResponse, Secret, SecretPatch, Setting, SourceSpan, WorldRule,
    WorldRulePatch, WorldRuleVersion,
)
from contracts.schema.stage3 import (
    ChapterStyle, ChapterStyleResponse, HabitRepairResponse, HookStyle, Measurable,
    StyleEdit, StyleGrounding, StyleResponse, Voice,
)
from contracts.schema.stage4 import (
    BookCharacter, CtxBeat, CtxChapter, CtxFacts, CtxGoal, FactRef, PlanBeat, PlanChapter,
    PlanChapters, PlanEdit, PlanGoal, WriterContext,
)
from contracts.schema.stage5 import (
    Chapter, ChapterResponse, ChapterSegment, DraftConsumed, GeneratedSegment,
    JudgedDraft, RejectedAttempt, RejectedRevision, Review,
    ReviewChapter, SemanticAlignmentResponse, SemanticChapterClaims, SemanticClaim,
    SemanticClaimsResponse, SemanticEvidenceRef, SemanticReview,
    SemanticSecretAdjudication, SemanticSecretDecision,
)
from contracts.schema.stage6 import (
    ApprovedHook, Candidate, Hook, HookApprovalResolution, HookEdit,
    HookSemanticResponse, HookSemanticReview, Mech,
)
from contracts.schema.stage7 import (
    BookTxt, CharacterPatch, ExtraSub, IdentityBaseline, NameDecision, NameMap,
    NameMapEdit, NameMapResponse, Residual, StemBaseline,
)

class ArtifactVersionError(ValueError):
    """一份产物不是当前注册版本。错误恒带文件与版本。"""


@dataclass(frozen=True)
class ArtifactSpec:
    """一类盘上 JSON 的当前版本与模型。"""

    model: type[BaseModel]
    current_version: int = 1
    migrations: Mapping[int, Callable[[Any], Any]] = field(default_factory=dict)

    @property
    def root(self) -> bool:
        return bool(getattr(self.model, '__pydantic_root_model__', False))


@dataclass(frozen=True)
class DecodedArtifact:
    payload: Any
    version: int


def _v2_to_v3(payload: Any) -> Any:
    """Neutral v2→v3 migration for provenance-only contract additions."""
    return deepcopy(payload)


def _finding_defaults(rows: object) -> None:
    if not isinstance(rows, list):
        return
    for row in rows:
        if isinstance(row, dict):
            row.setdefault('output_ordinal', None)


def _stage4_v2_to_v3(payload: Any) -> Any:
    out = deepcopy(payload)
    if isinstance(out, dict) and isinstance(out.get('_provenance'), dict):
        source = out['_provenance']
        source.setdefault('partial_regen_chapters', [])
        source.setdefault('repairs', [])
    return out


def _stage5_review_v2_to_v3(payload: Any) -> Any:
    out = deepcopy(payload)
    if not isinstance(out, dict):
        return out
    chapters = out.get('chapters')
    if isinstance(chapters, list):
        for chapter in chapters:
            if isinstance(chapter, dict):
                # Migration may only add a neutral unknown.  Newly generated
                # reviews stamp the actual chapter ordinal at production time.
                _finding_defaults(chapter.get('findings'))
    _finding_defaults(out.get('book'))
    return out


def _stage6_v2_to_v3(payload: Any) -> Any:
    out = deepcopy(payload)
    if not isinstance(out, dict):
        return out
    source = out.get('_provenance')
    if isinstance(source, dict):
        source.setdefault('candidate_retries', [])
        params = source.get('params')
        if isinstance(params, dict):
            params.setdefault('candidate_retry_max_tokens', 24000)
            # v2 predates targeted candidate retrying.  A migration may make
            # that absence explicit, but must not rewrite history as if one
            # retry had been configured at generation time.
            params.setdefault('candidate_retry_limit', 0)
            params.setdefault('candidate_retry_effort', 'low')
            params.setdefault('candidate_retry_thinking', False)
    candidates = out.get('candidates')
    if isinstance(candidates, list):
        for candidate in candidates:
            if isinstance(candidate, dict):
                _finding_defaults(candidate.get('findings'))
    approved = out.get('approved')
    if isinstance(approved, dict):
        _finding_defaults(approved.get('findings'))
    return out


def _stage7_v2_to_v3(payload: Any) -> Any:
    out = deepcopy(payload)
    if not isinstance(out, dict):
        return out
    # v2 approvals carried only {origin: human}.  An actor/reason/hash cannot
    # be inferred, so migration deliberately invalidates the old decision.
    out['approved'] = None
    _finding_defaults(out.get('book'))
    return out


MIGRATE_V2: Mapping[int, Callable[[Any], Any]] = {2: _v2_to_v3}
MIGRATE_STAGE4_V2: Mapping[int, Callable[[Any], Any]] = {2: _stage4_v2_to_v3}
MIGRATE_STAGE5_REVIEW_V2: Mapping[int, Callable[[Any], Any]] = {
    2: _stage5_review_v2_to_v3,
}
MIGRATE_STAGE6_V2: Mapping[int, Callable[[Any], Any]] = {2: _stage6_v2_to_v3}
MIGRATE_STAGE7_V2: Mapping[int, Callable[[Any], Any]] = {2: _stage7_v2_to_v3}


#: **产物相对路径 → 当前契约。** 路径里的 `{n}` / `{mode}` 是占位符。
#: 这是路径、模型与 schema version 的唯一权威表；`ARTIFACTS` 是模型投影。
#:
#: ⚠ 这里**不含** `stage0/clean_text.txt` / `stage5/**/draft.md` / `stage7/book.txt`
#: ——它们不是 JSON，schema 是**字节形态**，守卫在写盘口那一层。
ARTIFACT_SPECS: dict[str, ArtifactSpec] = {
    STAGE0_REMOVAL_REPORT: ArtifactSpec(RemovalReport, current_version=2),
    STAGE0_MACHINE_SNAPSHOT: ArtifactSpec(MachineRemovalSnapshot),
    STAGE0_MERGE_CANDIDATE: ArtifactSpec(Stage0MergeCandidate),
    STAGE0_AUDIT_ENTRY: ArtifactSpec(Stage0AuditEntry),
    STAGE1_MAIN_TEXT: ArtifactSpec(MainText),
    STAGE1_HOOK_COPY: ArtifactSpec(HookCopy),
    STAGE1_PARTITION_REPORT: ArtifactSpec(PartitionReport),
    STAGE2_GLOBAL_GROUNDING: ArtifactSpec(
        GlobalGrounding, current_version=3, migrations=MIGRATE_V2),
    STAGE2_GLOBAL_REVIEW: ArtifactSpec(GlobalReview),
    STAGE2_GLOBAL_REVIEW_DECISIONS: ArtifactSpec(GlobalReviewDecisionSet),
    # ⚠ 人产的 Stage2 typed 订正决策集（`stage2.global --apply-edit` 吃它）——
    #   与下面三份 `*_edit.json` 同一类：不是节点产的，但落在产物目录、
    #   被 `store.read` 当上游边记账 ⇒ 形状同样是契约。
    STAGE2_GLOBAL_EDIT: ArtifactSpec(GlobalEdit),
    STAGE2_CHAPTER_BLUEPRINTS: ArtifactSpec(
        ChapterBlueprints, current_version=3, migrations=MIGRATE_V2),
    STAGE3_STYLE_GROUNDING: ArtifactSpec(
        StyleGrounding, current_version=3, migrations=MIGRATE_V2),
    # ⚠ 三份 `*_edit.json` 不是节点产的，是**人产的输入**——但它们落在产物目录、
    #   被 `--apply-edit` 当决策集吃掉，所以形状同样是契约（stage7 先例，§三）。
    STAGE3_STYLE_EDIT: ArtifactSpec(StyleEdit),
    STAGE4_PLAN_CHAPTERS: ArtifactSpec(
        PlanChapters, current_version=3, migrations=MIGRATE_STAGE4_V2),
    STAGE4_PLAN_EDIT: ArtifactSpec(PlanEdit),
    STAGE4_WRITER_CONTEXT: ArtifactSpec(WriterContext, current_version=2),
    # ⚠ **恒等基线是一份【产物】不是一次 print**（2026-08-12，F3-4）。
    #   `stage4.plan --identity` 必须让「跑过且写手手里有这些」与
    #   「从没跑过」在盘上长得一模一样（`Q-2`）。stage6/stage7 在同一道工序上都做对了。
    #   ⚠ 它与正式那份**同一个模型**：形状完全一样，差别只在三个 goal 是显式标记
    #   ——**换一个模型就等于说它是另一种东西**，而它恰恰要回答
    #   「计划什么都不改时，写手手里到底有什么」。
    STAGE4_WRITER_CONTEXT_IDENTITY: ArtifactSpec(WriterContext, current_version=2),
    STAGE5_CHAPTER: ArtifactSpec(Chapter, current_version=3, migrations=MIGRATE_V2),
    STAGE5_REJECTED_REVISION: ArtifactSpec(RejectedRevision),
    STAGE5_REVIEW: ArtifactSpec(
        Review, current_version=3, migrations=MIGRATE_STAGE5_REVIEW_V2),
    STAGE5_SEMANTIC: ArtifactSpec(SemanticReview, current_version=2),
    STAGE5_TRIM_AUDIT: ArtifactSpec(TrimAudit),
    STAGE5_TRIM_RESOLUTION: ArtifactSpec(TrimResolution),
    # ⚠ 逐层一份账（2026-08-15 裁决）：**层由文件路径承载，不进收敛键**。
    #   两份共用一个模型——同一件事的第 N 个落点必然分家。
    STAGE5_WONTFIX: ArtifactSpec(WontFix),
    STAGE6_HOOK: ArtifactSpec(Hook, current_version=3, migrations=MIGRATE_STAGE6_V2),
    STAGE6_HOOK_EDIT: ArtifactSpec(HookEdit),
    STAGE6_SEMANTIC: ArtifactSpec(HookSemanticReview, current_version=2),
    STAGE6_WONTFIX: ArtifactSpec(WontFix),
    STAGE7_NAME_MAP: ArtifactSpec(NameMap, current_version=3, migrations=MIGRATE_STAGE7_V2),
    STAGE7_NAME_MAP_EDIT: ArtifactSpec(NameMapEdit),
}
ARTIFACTS: dict[str, type[BaseModel]] = {
    key: spec.model for key, spec in ARTIFACT_SPECS.items()
}


def artifact_regex(template: str) -> re.Pattern[str]:
    """把注册表模板编译成语料、运行时共用的相对路径路由。"""
    escaped = re.escape(template)
    escaped = escaped.replace(r'\{mode\}', r'(?:deep|shallow)')
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


def decode_artifact(rel: str, data: Any) -> DecodedArtifact:
    """拆出当前版本的逻辑 payload；未注册的测试数据原样返回。"""
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
    if version > spec.current_version:
        raise ArtifactVersionError(
            f'{rel}: schema v{version} 不是当前 v{spec.current_version}；'
            '高版本产物不猜读')
    while version < spec.current_version:
        migrate = spec.migrations.get(version)
        if migrate is None:
            raise ArtifactVersionError(
                f'{rel}: schema v{version} 不是当前 v{spec.current_version}；'
                f'没有登记 v{version}→v{version + 1} migration，拒绝猜读')
        try:
            payload = migrate(payload)
        except (TypeError, ValueError, KeyError) as exc:
            raise ArtifactVersionError(
                f'{rel}: schema v{version}→v{version + 1} migration 失败：{exc}') from exc
        version += 1
    return DecodedArtifact(payload, version)


def encode_artifact(rel: str, payload: Any) -> Any:
    """给当前逻辑 payload 加版本；RootModel 用 envelope，普通对象加顶层标记。"""
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
    'EvidenceRef', 'GroundTruthCase', 'GroundTruthManifest',
    'EvaluatorFingerprint', 'CapabilityMetrics', 'EvaluatorProfile',
    'JudgeDecision', 'SemanticDecision', 'GlobalReviewItem', 'GlobalReview',
    'GlobalReviewDecision', 'GlobalReviewDecisionSet',
    'Claim', 'TrimEvidenceSpan', 'TrimJudgeResponse', 'TrimAudit', 'TrimResolution',
    # 校验报告（`_report.md`）——**不是产物**，所以不在 `ARTIFACTS` 里
    'CheckReport', 'RuleLine', 'ProvenanceSection', 'EdgeLine',
    'CheckKind', 'Remediation', 'FixRoute',
    'AccountingLine', 'StatusCounts', 'Status', 'Badge', 'finding', 'badge_of',
    # HTTP 响应（`_api.md`）——同样**不是产物**。它是前三份的视图，不产生新事实
    'StyleView', 'VoiceLine', 'HookStyleLine', 'DriftItem',
    'Stage0Segment', 'Stage0Segments', 'Stage0EditRequest',
    'Stage0ApprovalRequest', 'Stage0MergeDecisionRequest',
    'Stage1Segment', 'Stage1Segments', 'Stage1ChapterRow', 'Stage1Chapters',
    'HookCopyView', 'UploadResult',
    'NovelList', 'JobList', 'LogChunk', 'LogStreamEvent', 'PreviewRequest', 'ErrorBody',
    'ApprovalAction', 'ApprovalBackfillResult', 'ApprovalDoc', 'DeepSelectionRequest',
    'InboxItem', 'InboxView', 'TrimResolutionRequest', 'NameMapApproveRequest',
    'ResidualAdoptionRequest',
    # 八层
    'RemovalReport', 'RemovedSegment', 'Stage0EditPlan', 'Stage0Warning',
    'MachineRemovalSnapshot', 'Stage0MergeCandidate', 'Stage0MergeConflict',
    'Stage0ConflictResolution', 'Stage0MergeDecisionPlan', 'Stage0AuditEntry',
    'MainText', 'MainTextSection', 'HookCopy', 'PartitionReport', 'Stage1Warning',
    'GlobalGrounding', 'Entities', 'Character', 'Address', 'Relation', 'Canon',
    'Setting', 'WorldRule', 'WorldRuleVersion', 'Secret', 'ReaderRevealEvent',
    'GlobalEdit', 'SecretPatch', 'WorldRulePatch', 'CharacterEnumPatch',
    'AddressContextPatch', 'CharacterAddressReplacement', 'Arc', 'ConflictStage',
    'ArcTrack', 'ChapterBlueprints', 'Blueprint', 'Beat', 'SourceSpan', 'Hinge', 'Narration',
    'GlobalGroundingResponse', 'ChapterBlueprintsResponse', 'RoleSplitResponse',
    'StyleGrounding', 'StyleResponse', 'ChapterStyle', 'ChapterStyleResponse',
    'HabitRepairResponse', 'Voice', 'HookStyle', 'Measurable', 'StyleEdit',
    'PlanChapters', 'PlanChapter', 'PlanBeat', 'FactRef', 'PlanGoal', 'PlanEdit',
    'WriterContext', 'BookCharacter',
    'CtxChapter', 'CtxBeat', 'CtxFacts', 'CtxGoal',
    'Chapter', 'GeneratedSegment', 'ChapterSegment', 'ChapterResponse',
    'RejectedAttempt', 'RejectedRevision',
    'Review', 'ReviewChapter', 'JudgedDraft', 'DraftConsumed',
    'SemanticClaim', 'SemanticChapterClaims', 'SemanticEvidenceRef',
    'SemanticClaimsResponse', 'SemanticAlignmentResponse',
    'SemanticSecretDecision', 'SemanticSecretAdjudication', 'SemanticReview',
    'Hook', 'Candidate', 'ApprovedHook', 'HookApprovalResolution', 'Mech',
    'HookSemanticResponse', 'HookSemanticReview', 'HookEdit',
    'NameMap', 'NameDecision', 'ExtraSub', 'Residual', 'StemBaseline', 'BookTxt',
    'IdentityBaseline', 'NameMapEdit', 'CharacterPatch', 'NameMapResponse',
    # 路由表
    'ArtifactSpec', 'ArtifactVersionError', 'DecodedArtifact',
    'ARTIFACT_SPECS', 'ARTIFACTS', 'ARTIFACT_ROUTES',
    'artifact_regex', 'formal_artifact_rel', 'artifact_key', 'artifact_spec',
    'decode_artifact', 'encode_artifact',
]
