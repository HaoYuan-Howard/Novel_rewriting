#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""str06 —— Stage2–Stage5 终态架构的 RC 契约子包（STR-06 批次 F0）。

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
  design       PresentationGraph v1（source-blind Target Design）
  execution    ExecutionPlan（四类执行语义）与 ExecutionSlice 身份
  context      WriterContext 双视图（VerificationIR + WriterBrief）
  receipt      SceneReceipt（接受回执最小七字段）与 FidelityVerdict
  consumption  Stage6/7 消费投影族登记与 Prepare 坐标分离
  dependency   新链依赖方向登记（F1 AST 守卫的数据源）
"""
from __future__ import annotations

from contracts.schema.str06.consumption import (
    PROJECTION_COORDINATES,
    STR06_CONSUMPTION_PROJECTIONS,
    PrepareMaterials,
    ProjectionCoordinate,
    SourceHookEvidence,
    TargetOpening,
)
from contracts.schema.str06.dependency import (
    STR06_CROSS_CUTTING_RULES,
    STR06_DEPENDENCY_RULES,
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
    ChapterAssignment,
    PresentationGraph,
    SceneDelivery,
    SceneDriver,
    TargetBeat,
    TargetDelta,
    TargetScene,
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
from contracts.schema.str06.policy import PreserveKind, PreservationPolicy
from contracts.schema.str06.receipt import (
    FailureOrigin,
    FidelityFinding,
    FidelityVerdict,
    ReceiptStatus,
    SceneReceipt,
)
from contracts.schema.str06.source import (
    Beat,
    BlueprintsArtifactV12,
    CausalEdge,
    ChapterBlueprint,
    CharacterEntry,
    CharactersArtifactV4,
    Effect,
    EffectTarget,
    InitialStateAtom,
    KnowledgeSpec,
    Reveal,
    StateTarget,
    SurfaceWindow,
    WorldConstraint,
)

#: 本子包契约的 RC 版式身份。判官契约版本（`SceneReceipt.judge_contract_version`）
#: 在 F5 定版前引用本常量，避免各处手写字符串漂移。
STR06_RC_VERSION = 'str06-rc1'

__all__ = [
    'PROJECTION_COORDINATES',
    'STR06_CONSUMPTION_PROJECTIONS',
    'STR06_CROSS_CUTTING_RULES',
    'STR06_DEPENDENCY_RULES',
    'STR06_PATH_FAMILIES',
    'STR06_RC_VERSION',
    'STR06_TWO_LEVEL_PATHS',
    'BindingEntry',
    'ChapterAssignment',
    'ConstraintSpec',
    'DependencyEdge',
    'DependencyRule',
    'EffectDomain',
    'Establishment',
    'ExecutionPlan',
    'ExecutionSlice',
    'FailureOrigin',
    'FidelityFinding',
    'FidelityVerdict',
    'IdentityProjection',
    'KnowledgeEntry',
    'OutputConstraints',
    'PathFamily',
    'PrepareMaterials',
    'PreserveCheckpoint',
    'PreserveKind',
    'PreservationPolicy',
    'ProjectionCoordinate',
    'ReceiptStatus',
    'ReconstructionBrief',
    'Reinforcement',
    'SceneDelivery',
    'SceneDriver',
    'SceneExecutionPlan',
    'SceneReceipt',
    'SemanticBinding',
    'SourceHookEvidence',
    'SourceLineage',
    'SourceObligation',
    'StateEntry',
    'SurfaceRelease',
    'TargetBeat',
    'TargetDelta',
    'TargetOpening',
    'TargetScene',
    'UnboundEntry',
    'VerificationIR',
    'WriterBrief',
    'WriterContext',
    'family_for',
]
