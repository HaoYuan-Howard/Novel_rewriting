#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
contracts.schema —— 产物 schema 的机器形态。

**`docs/schema/*.md` 是源，这里的模型照它写。方向别搞反。**
文件名一一对应：`docs/schema/stage3.md` ↔ `contracts/schema/stage3.py`，
`docs/schema/_common.md` ↔ `contracts/schema/common.py`。

除便利 re-export 外，本包只在产物 IO 边界做路径路由、schema version 编解码与迁移；
当前 Pydantic 模型本身始终保持单一形状。
`contracts/invariants.py` 仍是契约叶子（只 import 标准库与更底层的路径目录），
它不知道 pydantic 的存在。

【`ARTIFACTS`：相对路径 → 模型，唯一落点】
盘上语料体检、`/api/novels/:sid/...` 的响应类型、将来 `tools/emit_json_schema.py`
产前端 TS 类型，三个消费方读的是**同一张表**。手抄第二份的下场见 `Q-6`。
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Any, Callable, Mapping

from pydantic import BaseModel

from contracts.artifacts import (
    STAGE0_REMOVAL_REPORT, STAGE1_HOOK_COPY, STAGE1_MAIN_TEXT,
    STAGE1_PARTITION_REPORT, STAGE2_CHAPTER_BLUEPRINTS, STAGE2_GLOBAL_EDIT,
    STAGE2_GLOBAL_GROUNDING, STAGE3_STYLE_EDIT, STAGE3_STYLE_GROUNDING,
    STAGE4_PLAN_CHAPTERS, STAGE4_PLAN_EDIT, STAGE4_WRITER_CONTEXT,
    STAGE4_WRITER_CONTEXT_IDENTITY, STAGE5_CHAPTER, STAGE5_REJECTED_REVISION,
    STAGE5_REVIEW, STAGE5_SEMANTIC, STAGE5_WONTFIX, STAGE6_HOOK,
    STAGE6_HOOK_EDIT, STAGE6_SEMANTIC, STAGE6_WONTFIX, STAGE7_NAME_MAP,
    STAGE7_NAME_MAP_EDIT,
)

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
    LogStreamEvent,
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
    Chapter, DraftConsumed, JudgedDraft, RejectedAttempt, RejectedRevision, Review,
    ReviewChapter, SemanticAlignmentResponse, SemanticChapterClaims, SemanticClaim,
    SemanticClaimsResponse, SemanticEvidenceRef, SemanticReview,
    SemanticSecretAdjudication, SemanticSecretDecision,
)
from contracts.schema.stage6 import (
    ApprovedHook, Candidate, Hook, HookEdit, HookSemanticResponse, HookSemanticReview, Mech,
)
from contracts.schema.stage7 import (
    BookTxt, CharacterPatch, ExtraSub, IdentityBaseline, NameDecision, NameMap,
    NameMapEdit, NameMapResponse, Residual, StemBaseline,
)

#: v0 → v1 的单步迁移。迁移只改载体形状，不补写无法从旧字节证明的新事实。
Migration = Callable[[Any], Any]


class ArtifactVersionError(ValueError):
    """一份产物的版本无法按当前注册表解释。错误恒带文件与版本。"""


@dataclass(frozen=True)
class ArtifactSpec:
    """一类盘上 JSON 的版本、模型与逐版本迁移。"""

    model: type[BaseModel]
    current_version: int = 1
    migrations: Mapping[int, Migration] = field(default_factory=dict)
    identity_v0: bool = True

    @property
    def root(self) -> bool:
        return bool(getattr(self.model, '__pydantic_root_model__', False))


@dataclass(frozen=True)
class DecodedArtifact:
    payload: Any
    source_version: int
    current_version: int


_OLD_HOOK_APPROACHES = {
    'conflict_first': 'source_architecture',
    'result_flashback': 'payoff_preview',
    'identity_contrast': 'scene_punchline',
}


def _identity_v0(payload: Any) -> Any:
    return payload


def _hook_v0(payload: Any) -> Any:
    """迁移 2026-08-15 前的 hook；映射是当次三策略改名的一一对应。"""
    if not isinstance(payload, dict):
        return payload
    out = dict(payload)
    for key in ('book', 'baselines_identity', 'baselines', 'counts', 'title', 'source_id'):
        out.pop(key, None)
    candidates = []
    for candidate in out.get('candidates', []):
        item = dict(candidate)
        item['approach'] = _OLD_HOOK_APPROACHES.get(item.get('approach'), item.get('approach'))
        candidates.append(item)
    if 'candidates' in out:
        out['candidates'] = candidates
    if isinstance(out.get('approved'), dict):
        approved = dict(out['approved'])
        approved['approach'] = _OLD_HOOK_APPROACHES.get(
            approved.get('approach'), approved.get('approach'))
        out['approved'] = approved
    provenance = out.get('_provenance')
    if isinstance(provenance, dict) and isinstance(provenance.get('params'), dict):
        provenance = dict(provenance)
        params = dict(provenance['params'])
        params.pop('tag', None)
        provenance['params'] = params
        out['_provenance'] = provenance
    return out


def _hook_edit_v0(payload: Any) -> Any:
    if not isinstance(payload, dict):
        return payload
    out = dict(payload)
    out['candidate'] = _OLD_HOOK_APPROACHES.get(out.get('candidate'), out.get('candidate'))
    return out


def _name_map_v0(payload: Any) -> Any:
    if not isinstance(payload, dict):
        return payload
    out = dict(payload)
    provenance = out.get('_provenance')
    if isinstance(provenance, dict) and isinstance(provenance.get('params'), dict):
        provenance = dict(provenance)
        params = dict(provenance['params'])
        params.pop('tag', None)
        provenance['params'] = params
        out['_provenance'] = provenance
    return out


#: **产物相对路径 → 版本化契约。** 路径里的 `{n}` / `{mode}` 是占位符。
#: 这是路径、模型、schema version 与迁移的唯一权威表；`ARTIFACTS` 只为旧消费方派生。
#:
#: ⚠ 这里**不含** `stage0/clean_text.txt` / `stage5/**/draft.md` / `stage7/book.txt`
#: ——它们不是 JSON，schema 是**字节形态**，守卫在写盘口那一层。
ARTIFACT_SPECS: dict[str, ArtifactSpec] = {
    STAGE0_REMOVAL_REPORT: ArtifactSpec(RemovalReport),
    STAGE1_MAIN_TEXT: ArtifactSpec(MainText),
    STAGE1_HOOK_COPY: ArtifactSpec(HookCopy),
    STAGE1_PARTITION_REPORT: ArtifactSpec(PartitionReport),
    STAGE2_GLOBAL_GROUNDING: ArtifactSpec(GlobalGrounding),
    # ⚠ 人产的 secret 订正决策集（`stage2.global --apply-edit` 吃它）——
    #   与下面三份 `*_edit.json` 同一类：不是节点产的，但落在产物目录、
    #   被 `store.read` 当上游边记账 ⇒ 形状同样是契约。
    STAGE2_GLOBAL_EDIT: ArtifactSpec(GlobalEdit),
    STAGE2_CHAPTER_BLUEPRINTS: ArtifactSpec(ChapterBlueprints),
    STAGE3_STYLE_GROUNDING: ArtifactSpec(StyleGrounding),
    # ⚠ 三份 `*_edit.json` 不是节点产的，是**人产的输入**——但它们落在产物目录、
    #   被 `--apply-edit` 当决策集吃掉，所以形状同样是契约（stage7 先例，§三）。
    STAGE3_STYLE_EDIT: ArtifactSpec(StyleEdit),
    STAGE4_PLAN_CHAPTERS: ArtifactSpec(PlanChapters),
    STAGE4_PLAN_EDIT: ArtifactSpec(PlanEdit),
    STAGE4_WRITER_CONTEXT: ArtifactSpec(WriterContext),
    # ⚠ **恒等基线是一份【产物】不是一次 print**（2026-08-12，F3-4）。
    #   `stage4.plan --identity` 必须让「跑过且写手手里有这些」与
    #   「从没跑过」在盘上长得一模一样（`Q-2`）。stage6/stage7 在同一道工序上都做对了。
    #   ⚠ 它与正式那份**同一个模型**：形状完全一样，差别只在三个 goal 是显式标记
    #   ——**换一个模型就等于说它是另一种东西**，而它恰恰要回答
    #   「计划什么都不改时，写手手里到底有什么」。
    STAGE4_WRITER_CONTEXT_IDENTITY: ArtifactSpec(WriterContext),
    STAGE5_CHAPTER: ArtifactSpec(Chapter),
    STAGE5_REJECTED_REVISION: ArtifactSpec(RejectedRevision),
    STAGE5_REVIEW: ArtifactSpec(Review),
    STAGE5_SEMANTIC: ArtifactSpec(SemanticReview),
    # ⚠ 逐层一份账（2026-08-15 裁决）：**层由文件路径承载，不进收敛键**。
    #   两份共用一个模型——同一件事的第 N 个落点必然分家。
    STAGE5_WONTFIX: ArtifactSpec(WontFix),
    STAGE6_HOOK: ArtifactSpec(Hook, migrations={0: _hook_v0}),
    STAGE6_HOOK_EDIT: ArtifactSpec(HookEdit, migrations={0: _hook_edit_v0}),
    # 旧版记录的是逐 claim 的 grounded/arc_leak；当前版要求六维质量分、矛盾证据与排序。
    # 这些事实不能从旧字节推回，故 v0 明确拒读，不能“迁移”出一组编造的 0 分。
    STAGE6_SEMANTIC: ArtifactSpec(HookSemanticReview, identity_v0=False),
    STAGE6_WONTFIX: ArtifactSpec(WontFix),
    STAGE7_NAME_MAP: ArtifactSpec(NameMap, migrations={0: _name_map_v0}),
    STAGE7_NAME_MAP_EDIT: ArtifactSpec(NameMapEdit),
}

# 每个 v0 产物至少有一条显式路径；“什么都不改”也是登记过的迁移决定。
ARTIFACT_SPECS = {
    key: (spec if spec.migrations or not spec.identity_v0 else ArtifactSpec(
        spec.model, spec.current_version, {0: _identity_v0}, spec.identity_v0))
    for key, spec in ARTIFACT_SPECS.items()
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
    # 旧实现曾用一个无编号名字保存被正式稿取代的 INVALID；它仍是 Chapter 的历史字节。
    normalized = normalized.replace('.INVALID.superseded.json', '.json')
    # 旧版 metadata-convergence 修复留下的诊断快照，载体仍是当轮 Review。
    normalized = normalized.replace('.invalid_metadata_convergence.json', '.json')
    normalized = re.sub(r'\.r\d+(?=\.json$)', '', normalized)
    return normalized.replace('.INVALID.json', '.json')


def artifact_key(rel: str) -> str | None:
    formal = formal_artifact_rel(rel)
    return next((key for pattern, key in ARTIFACT_ROUTES if pattern.fullmatch(formal)), None)


def artifact_spec(rel: str) -> ArtifactSpec | None:
    key = artifact_key(rel)
    return ARTIFACT_SPECS.get(key) if key is not None else None


def decode_artifact(rel: str, data: Any) -> DecodedArtifact:
    """把盘上版本升级成当前模型吃的逻辑 payload；不在注册表的测试产物原样返回。"""
    spec = artifact_spec(rel)
    if spec is None:
        return DecodedArtifact(data, 0, 0)

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

    version = 0 if marker is None else marker
    if isinstance(version, bool) or not isinstance(version, int) or version < 0:
        raise ArtifactVersionError(f'{rel}: 非法 schema version {version!r}')
    if version > spec.current_version:
        raise ArtifactVersionError(
            f'{rel}: schema v{version} 高于当前可读 v{spec.current_version}，明确拒读')

    source = version
    while version < spec.current_version:
        migrate = spec.migrations.get(version)
        if migrate is None:
            raise ArtifactVersionError(
                f'{rel}: schema v{version} 没有到 v{version + 1} 的迁移，明确拒读')
        payload = migrate(payload)
        version += 1
    return DecodedArtifact(payload, source, spec.current_version)


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
    'NovelList', 'JobList', 'LogChunk', 'LogStreamEvent', 'PreviewRequest', 'ErrorBody',
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
    'Chapter', 'RejectedAttempt', 'RejectedRevision',
    'Review', 'ReviewChapter', 'JudgedDraft', 'DraftConsumed',
    'SemanticClaim', 'SemanticChapterClaims', 'SemanticEvidenceRef',
    'SemanticClaimsResponse', 'SemanticAlignmentResponse',
    'SemanticSecretDecision', 'SemanticSecretAdjudication', 'SemanticReview',
    'Hook', 'Candidate', 'ApprovedHook', 'Mech',
    'HookSemanticResponse', 'HookSemanticReview', 'HookEdit',
    'NameMap', 'NameDecision', 'ExtraSub', 'Residual', 'StemBaseline', 'BookTxt',
    'IdentityBaseline', 'NameMapEdit', 'CharacterPatch', 'NameMapResponse',
    # 路由表
    'ArtifactSpec', 'ArtifactVersionError', 'DecodedArtifact',
    'ARTIFACT_SPECS', 'ARTIFACTS', 'ARTIFACT_ROUTES',
    'artifact_regex', 'formal_artifact_rel', 'artifact_key', 'artifact_spec',
    'decode_artifact', 'encode_artifact',
]
