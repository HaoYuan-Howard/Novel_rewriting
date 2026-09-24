#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""str06 —— Stage2–Stage7 终态架构的 RC 契约子包（STR-06 批次 F0–F6）。

【RC 边界（与本子包的存在理由）】
本子包承载 STR-06 终态架构的最小 schema、Policy、Brief、场景消费身份、路径与依赖
契约。Architecture（职责、权威源、依赖方向、窄修复、失败归属）已冻结，见
`docs/REQUIREMENTS.md` STR-06 与 `docs/DECISIONS.md` 2026-09-23 裁决；
**字段级 schema 保持 RC**：经 F1 反例与 F2–F6 真书验证后定版。

⚠ 三条硬边界，防「主仓出现两套现役定义」（S-04）：
  ① 本子包**不进** `contracts.schema.ARTIFACT_SPECS`、不注册 `NODES`、不进
     `contracts.judges` 判据注册表——现役链继续按旧契约验收，正式替代归属
     F8 切换提交一次合入。
  ② 本子包不 import `novel`（contracts 层依赖方向不变）。
  ③ 新链书在独立输出根重建；旧书 `legacy_incompatible`。

【模块地图】
  families     三级路径家族登记（含来源账键规则）
  policy       PreservationPolicy（版本化保存策略）
  grounding    SourceObligation / PreserveCheckpoint / ReconstructionBrief /
               OutputConstraints / SemanticBinding
  source       Characters v4（core + name_structure）/ Blueprints v12
  design       BodyDesign / PresentationGraph v1（source-blind Target Design）
  hook         HookDesign / HookContext / HookReceipt（F6）
  naming       NameMap v5 与目标名称位（F6）
  execution    ExecutionPlan（四类执行语义）与 ExecutionSlice 身份
  context      WriterContext 双视图（VerificationIR + WriterBrief）
  receipt      SceneReceipt（接受回执最小七字段）与 FidelityVerdict
  consumption  Stage5/7 消费投影族登记（F6 改版：六族，Prepare 退役）
  dependency   新链依赖方向登记（含 F6 迁移包真实模块规则）
  retirement   F6d 退役登记（纯数据，随 RC 包在 F8 删除）
"""
from __future__ import annotations

from contracts.schema.str06.consumption import (
    PROJECTION_COORDINATES,
    STR06_CONSUMPTION_PROJECTIONS,
    ProjectionCoordinate,
)
from contracts.schema.str06.dependency import (
    STR06_CROSS_CUTTING_RULES,
    STR06_DEPENDENCY_RULES,
    STR06_MIGRATION_DEPENDENCY_RULES,
    STR06_MIGRATION_MODULE_OWNERS,
    DependencyRule,
)
from contracts.schema.str06.families import (
    STR06_PATH_FAMILIES,
    STR06_TWO_LEVEL_PATHS,
    PathFamily,
    family_for,
)
from contracts.schema.str06.context import (
    KnowledgeEntry,
    StateEntry,
    SurfaceRelease,
    VerificationIR,
    WriterBrief,
    WriterContext,
)
from contracts.schema.str06.design import (
    BodyDesign,
    ChapterAssignment,
    PresentationGraph,
    SceneDelivery,
    SceneDriver,
    TargetBeat,
    TargetDelta,
    TargetScene,
)
from contracts.schema.str06.hook import (
    ForbiddenReveal,
    HookConstraints,
    HookContext,
    HookDesign,
    HookMaterial,
    HookMaterialView,
    HookOpeningFact,
    HookPov,
    HookReceipt,
    OpeningFact,
)
from contracts.schema.str06.execution import (
    ConstraintSpec,
    DependencyEdge,
    EffectDomain,
    Establishment,
    ExecutionPlan,
    ExecutionSlice,
    Reinforcement,
    SceneExecutionPlan,
)
from contracts.schema.str06.grounding import (
    BindingEntry,
    IdentityProjection,
    OutputConstraints,
    PreserveCheckpoint,
    ReconstructionBrief,
    SemanticBinding,
    SourceLineage,
    SourceObligation,
    UnboundEntry,
)
from contracts.schema.str06.naming import (
    NameAssignment,
    NameMapV5,
    TargetName,
)
from contracts.schema.str06.policy import PreserveKind, PreservationPolicy
from contracts.schema.str06.receipt import (
    FailureOrigin,
    FidelityFinding,
    FidelityVerdict,
    ReceiptStatus,
    SceneReceipt,
)
from contracts.schema.str06.source import (
    AliasEntry,
    Beat,
    BlueprintsArtifactV12,
    CausalEdge,
    ChapterBlueprint,
    CharacterEntry,
    CharactersArtifactV4,
    CharactersCore,
    Effect,
    EffectTarget,
    FamilyEntry,
    FamilyForm,
    InitialStateAtom,
    KnowledgeSpec,
    NameProjection,
    NameProjectionEntry,
    NameStructure,
    PersonNameStructure,
    Reveal,
    StateTarget,
    SurfaceWindow,
    WorldConstraint,
    render_form,
)
from contracts.schema.str06.retirement import (
    STR06_F6D_RETIREMENT,
    RetirementAction,
    RetirementItem,
)

#: 本子包契约的 RC 版式身份。判官契约版本（`SceneReceipt.judge_contract_version`）
#: 与 ledger code 引用本常量，避免各处手写字符串漂移。
STR06_RC_VERSION = 'str06-rc3'

__all__ = [
    'PROJECTION_COORDINATES',
    'STR06_CONSUMPTION_PROJECTIONS',
    'STR06_CROSS_CUTTING_RULES',
    'STR06_DEPENDENCY_RULES',
    'STR06_F6D_RETIREMENT',
    'STR06_MIGRATION_DEPENDENCY_RULES',
    'STR06_MIGRATION_MODULE_OWNERS',
    'STR06_PATH_FAMILIES',
    'STR06_RC_VERSION',
    'STR06_TWO_LEVEL_PATHS',
    'AliasEntry',
    'BindingEntry',
    'BodyDesign',
    'ChapterAssignment',
    'CharactersArtifactV4',
    'CharactersCore',
    'ConstraintSpec',
    'DependencyEdge',
    'DependencyRule',
    'EffectDomain',
    'Establishment',
    'ExecutionPlan',
    'ExecutionSlice',
    'FailureOrigin',
    'FamilyEntry',
    'FamilyForm',
    'FidelityFinding',
    'FidelityVerdict',
    'ForbiddenReveal',
    'HookConstraints',
    'HookContext',
    'HookDesign',
    'HookMaterial',
    'HookMaterialView',
    'HookOpeningFact',
    'HookPov',
    'HookReceipt',
    'IdentityProjection',
    'KnowledgeEntry',
    'NameAssignment',
    'NameMapV5',
    'NameProjection',
    'NameProjectionEntry',
    'NameStructure',
    'OpeningFact',
    'OutputConstraints',
    'PathFamily',
    'PersonNameStructure',
    'PreserveCheckpoint',
    'PreserveKind',
    'PreservationPolicy',
    'ProjectionCoordinate',
    'ReceiptStatus',
    'ReconstructionBrief',
    'Reinforcement',
    'RetirementAction',
    'RetirementItem',
    'SceneDelivery',
    'SceneDriver',
    'SceneExecutionPlan',
    'SceneReceipt',
    'SemanticBinding',
    'SourceLineage',
    'SourceObligation',
    'StateEntry',
    'SurfaceRelease',
    'TargetBeat',
    'TargetDelta',
    'TargetName',
    'TargetScene',
    'UnboundEntry',
    'VerificationIR',
    'WriterBrief',
    'WriterContext',
    'family_for',
    'render_form',
]
