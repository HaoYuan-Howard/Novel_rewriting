#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
contracts.schema —— 产物 schema 的机器形态。

**`docs/schema/*.md` 是源，这里的模型照它写。方向别搞反。**
文件名一一对应：`docs/schema/stage3.md` ↔ `contracts/schema/stage3.py`，
`docs/schema/_common.md` ↔ `contracts/schema/common.py`。

⚠ 本包只做便利 re-export，**不做任何逻辑**。
`contracts/invariants.py` 仍是叶子（只 import `typing`），它不知道 pydantic 的存在。

【`ARTIFACTS`：相对路径 → 模型，唯一落点】
盘上语料体检、`/api/novels/:sid/...` 的响应类型、将来 `tools/emit_json_schema.py`
产前端 TS 类型，三个消费方读的是**同一张表**。手抄第二份的下场见 `Q-6`。
"""
from __future__ import annotations

from contracts.schema.api import (
    ApprovalAction,
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
    NovelList,
    PreviewRequest,
    Stage0EditRequest,
    Stage0Segment,
    Stage0Segments,
    Stage1ChapterRow,
    Stage1Chapters,
    Stage1Segment,
    Stage1Segments,
    StyleView,
    UploadResult,
    VoiceLine,
)
from contracts.schema.base import Fp10, FrozenModel, Sha1, StrictModel
from contracts.schema.common import (
    Finding,
    FreeProvenance,
    InputEdge,
    LlmProvenance,
    Owner,
    Provenance,
    RoundFreeProvenance,
    RoundLlmProvenance,
    Severity,
    Usage,
    WontFix,
    WontFixEntry,
    WontFixNote,
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
    RemovalReport, RemovedSegment, Stage0EditPlan, Stage0Warning,
)
from contracts.schema.stage1 import (
    HookCopy, MainText, MainTextSection, PartitionReport, Stage1Warning,
)
from contracts.schema.stage2 import (
    Address, Arc, ArcTrack, Beat, Blueprint, Canon, ChapterBlueprints,
    ChapterBlueprintsResponse, Character, ConflictStage, Entities, GlobalEdit,
    GlobalGrounding, GlobalGroundingResponse, Hinge, Narration, ReaderRevealEvent,
    Relation, RoleSplitResponse, Secret, SecretPatch, Setting, SourceSpan,
    WorldRule, WorldRuleVersion,
)
from contracts.schema.stage3 import (
    ChapterStyle, ChapterStyleResponse, HabitRepairResponse, HookStyle, Measurable,
    StyleEdit, StyleGrounding, StyleResponse, Voice,
)
from contracts.schema.stage4 import (
    BookCharacter, CtxBeat, CtxChapter, CtxFacts, PlanBeat, PlanChapter, PlanChapters,
    PlanEdit, WriterContext,
)
from contracts.schema.stage5 import (
    Chapter, DraftConsumed, JudgedDraft, Review, ReviewChapter, SemanticAlignmentResponse,
    SemanticChapterClaims, SemanticClaim, SemanticClaimsResponse, SemanticEvidenceRef,
    SemanticReview, SemanticSecretAdjudication, SemanticSecretDecision,
)
from contracts.schema.stage6 import (
    ApprovedHook, Candidate, Hook, HookClaimDecision, HookEdit, HookSemanticResponse,
    HookSemanticReview, HookTextClaims, HookTextClaimsResponse, Mech,
)
from contracts.schema.stage7 import (
    BookTxt, CharacterPatch, ExtraSub, IdentityBaseline, NameDecision, NameMap,
    NameMapEdit, NameMapResponse, Residual, StemBaseline,
)

#: **产物相对路径 → 模型。** 路径里的 `{n}` / `{mode}` 是占位符，
#: `tests/corpus.py` 按它把盘上的真实路径映射回模型。
#:
#: ⚠ 这里**不含** `stage0/clean_text.txt` / `stage5/**/draft.md` / `stage7/book.txt`
#: ——它们不是 JSON，schema 是**字节形态**，守卫在写盘口那一层。
ARTIFACTS: dict[str, type] = {
    'stage0/removal_report.json': RemovalReport,
    'stage1/main_text.json': MainText,
    'stage1/hook_copy.json': HookCopy,
    'stage1/partition_report.json': PartitionReport,
    'stage2/global_grounding.json': GlobalGrounding,
    # ⚠ 人产的 secret 订正决策集（`stage2.global --apply-edit` 吃它）——
    #   与下面三份 `*_edit.json` 同一类：不是节点产的，但落在产物目录、
    #   被 `store.read` 当上游边记账 ⇒ 形状同样是契约。
    'stage2/global_edit.json': GlobalEdit,
    'stage2/chapter_blueprints.json': ChapterBlueprints,
    'stage3/style_grounding.json': StyleGrounding,
    # ⚠ 三份 `*_edit.json` 不是节点产的，是**人产的输入**——但它们落在产物目录、
    #   被 `--apply-edit` 当决策集吃掉，所以形状同样是契约（stage7 先例，§三）。
    'stage3/style_edit.json': StyleEdit,
    'stage4/plan_chapters.json': PlanChapters,
    'stage4/plan_edit.json': PlanEdit,
    'stage4/writer_context.json': WriterContext,
    # ⚠ **恒等基线是一份【产物】不是一次 print**（2026-08-12，F3-4）。原型的
    #   `--identity-plan` 整个输出是 `print` + 返回码 ⇒「跑过且写手手里有这些」与
    #   「从没跑过」在盘上长得一模一样（`Q-2`）。stage6/stage7 在同一道工序上都做对了。
    #   ⚠ 它与正式那份**同一个模型**：形状完全一样，差别只在三个 goal 是显式标记
    #   ——**换一个模型就等于说它是另一种东西**，而它恰恰要回答
    #   「计划什么都不改时，写手手里到底有什么」。
    'stage4/writer_context.identity.json': WriterContext,
    'stage5/{mode}/ch{nn}.json': Chapter,
    'stage5/review_{n}.json': Review,
    'stage5/semantic_{n}.json': SemanticReview,
    # ⚠ 逐层一份账（2026-08-15 裁决）：**层由文件路径承载，不进收敛键**。
    #   两份共用一个模型——同一件事的第 N 个落点必然分家。
    'stage5/wontfix.json': WontFix,
    'stage6/hook.json': Hook,
    'stage6/hook_edit.json': HookEdit,
    'stage6/semantic_{n}.json': HookSemanticReview,
    'stage6/wontfix.json': WontFix,
    'stage7/name_map.json': NameMap,
    'stage7/name_map_edit.json': NameMapEdit,
}

__all__ = [
    # 底座与共有部分
    'Fp10', 'Sha1', 'StrictModel', 'FrozenModel',
    'InputEdge', 'Usage', 'Provenance', 'FreeProvenance', 'LlmProvenance',
    'RoundLlmProvenance', 'RoundFreeProvenance',
    'Finding', 'WontFixNote', 'WontFix', 'WontFixEntry', 'Severity', 'Owner',
    # 校验报告（`_report.md`）——**不是产物**，所以不在 `ARTIFACTS` 里
    'CheckReport', 'RuleLine', 'ProvenanceSection', 'EdgeLine',
    'CheckKind', 'Remediation', 'FixRoute',
    'AccountingLine', 'StatusCounts', 'Status', 'Badge', 'finding', 'badge_of',
    # HTTP 响应（`_api.md`）——同样**不是产物**。它是前三份的视图，不产生新事实
    'StyleView', 'VoiceLine', 'HookStyleLine', 'DriftItem',
    'Stage0Segment', 'Stage0Segments', 'Stage0EditRequest',
    'Stage1Segment', 'Stage1Segments', 'Stage1ChapterRow', 'Stage1Chapters',
    'HookCopyView', 'UploadResult',
    'NovelList', 'JobList', 'LogChunk', 'PreviewRequest', 'ErrorBody',
    'ApprovalAction', 'ApprovalDoc', 'DeepSelectionRequest',
    'InboxItem', 'InboxView',
    # 八层
    'RemovalReport', 'RemovedSegment', 'Stage0EditPlan', 'Stage0Warning',
    'MainText', 'MainTextSection', 'HookCopy', 'PartitionReport', 'Stage1Warning',
    'GlobalGrounding', 'Entities', 'Character', 'Address', 'Relation', 'Canon',
    'Setting', 'WorldRule', 'WorldRuleVersion', 'Secret', 'ReaderRevealEvent',
    'GlobalEdit', 'SecretPatch', 'Arc', 'ConflictStage',
    'ArcTrack', 'ChapterBlueprints', 'Blueprint', 'Beat', 'SourceSpan', 'Hinge', 'Narration',
    'GlobalGroundingResponse', 'ChapterBlueprintsResponse', 'RoleSplitResponse',
    'StyleGrounding', 'StyleResponse', 'ChapterStyle', 'ChapterStyleResponse',
    'HabitRepairResponse', 'Voice', 'HookStyle', 'Measurable', 'StyleEdit',
    'PlanChapters', 'PlanChapter', 'PlanBeat', 'PlanEdit', 'WriterContext', 'BookCharacter',
    'CtxChapter', 'CtxBeat', 'CtxFacts',
    'Chapter', 'Review', 'ReviewChapter', 'JudgedDraft', 'DraftConsumed',
    'SemanticClaim', 'SemanticChapterClaims', 'SemanticEvidenceRef',
    'SemanticClaimsResponse', 'SemanticAlignmentResponse',
    'SemanticSecretDecision', 'SemanticSecretAdjudication', 'SemanticReview',
    'Hook', 'Candidate', 'ApprovedHook', 'Mech',
    'HookTextClaimsResponse', 'HookTextClaims', 'HookClaimDecision',
    'HookSemanticResponse', 'HookSemanticReview', 'HookEdit',
    'NameMap', 'NameDecision', 'ExtraSub', 'Residual', 'StemBaseline', 'BookTxt',
    'IdentityBaseline', 'NameMapEdit', 'CharacterPatch', 'NameMapResponse',
    # 路由表
    'ARTIFACTS',
]
