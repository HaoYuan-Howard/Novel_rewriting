#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Stage2 v6 → 已审 Stage4A 主体的只读兼容证明。

本模块刻意要求 ``Blueprint v6`` 与一份现存 current-schema Stage4A 候选同时
在场，并逐项证明：v6 自己的上游仍匹配、候选记录的文件/consumed 边匹配、旧
v1 blind truth 与候选公开映射完全一致。缺任一证据都不是兼容书，只是普通旧版
不可读产物。返回值只供状态、交付披露、审批 workpack 与零 wire 完整章 edit；
普通 producer、付费 repair/replan、Stage4B 不得调用。
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from contracts.artifacts import (
    STAGE2_CHAPTER_BLUEPRINTS, STAGE4_BLIND_CANDIDATE,
    STAGE4_EVIDENCE_BINDING, STAGE4_PLAN_CHAPTERS, STAGE4_WRITER_CONTEXT,
)
from contracts.invariants import FROZEN_V6_DESIGN_REVIEW_CONTRACT
from contracts.schema import ArtifactVersionError, artifact_spec
from contracts.schema.legacy_stage2 import FrozenChapterBlueprintsV6
from contracts.schema.stage4 import BlindCandidate
from novel import paths, prov
from novel.consumption import (
    frozen_v6_stage4_context_blueprints,
    frozen_v6_stage4_design_blueprints,
    frozen_v6_stage4_plan_blueprints,
)
from novel.errors import ArtifactUnreadable, UpstreamMissing, UpstreamStale
from novel.prov import FreshnessReport
from novel.snapshot_clone import readonly_snapshot_clone
from novel.stages.stage4.blind import (
    BlindPlanError, FROZEN_V6_BLIND_CONTRACT,
    build_frozen_v6_blind_projection,
)
from novel.store import ArtifactStore


LEGACY_FROZEN_CONTRACT = 'stage2-blueprint-v6-stage4a-bound-v1'
LEGACY_FROZEN_REVIEW_CONTRACT = FROZEN_V6_DESIGN_REVIEW_CONTRACT
LEGACY_DISCLOSURE = (
    'Stage2 ChapterBlueprints 为经现存 Stage4A v1 truth 精确绑定的冻结 schema v6；'
    '它没有 v7 narrative_relations 权威面，不会自动补字段、迁移或进入付费关系返修。'
)
_FROZEN_DOWNSTREAM_PROJECTIONS = {
    STAGE4_EVIDENCE_BINDING: frozen_v6_stage4_plan_blueprints,
    STAGE4_PLAN_CHAPTERS: frozen_v6_stage4_plan_blueprints,
    STAGE4_WRITER_CONTEXT: frozen_v6_stage4_context_blueprints,
}
_FROZEN_DELIVERY_DOWNSTREAMS = (
    STAGE4_EVIDENCE_BINDING,
    STAGE4_PLAN_CHAPTERS,
    STAGE4_WRITER_CONTEXT,
)


@dataclass(frozen=True)
class FrozenV6Stage4Subject:
    """一份已完成三重绑定的历史主体；所有 payload 都只存在内存。"""

    blueprint: FrozenChapterBlueprintsV6
    blueprint_payload: dict[str, Any]
    blueprint_sha256: str
    source_freshness: FreshnessReport
    truth: dict[str, Any]
    mapping: dict[str, Any]
    candidate: BlindCandidate
    candidate_sha256: str


@dataclass(frozen=True)
class FrozenV6DeliverySubject:
    """冻结 v6 能进入最终组装的完整、只读下游证明。

    ``stage4`` 证明历史 Blueprint 与 A 候选是同一主体；三份 downstream
    digest 继续证明 B、计划与写作任务书都只因 v6→v7 shape break 显示
    ``consumed_unresolved``，其余来源边仍逐项 ``fresh/match``。它不是迁移
    结果，也不制造任何 current-v7 Blueprint。
    """

    stage4: FrozenV6Stage4Subject
    downstream_sha256: dict[str, str]


def is_exact_frozen_v6_shape_break(exc: ArtifactUnreadable) -> bool:
    """只识别 ``chapter_blueprints`` 的精确 v6→v7 拒读。

    Stage7 先走 current-v7 总闸；只有那道闸明确因这一份历史 shape break
    拒读时才允许进入 frozen proof。坏 JSON、未来版、别的旧版本或未来 v8
    契约都不得借异常类型宽捕获进入兼容面。
    """
    cause = exc.__cause__
    return (
        isinstance(cause, ArtifactVersionError)
        and cause.rel == STAGE2_CHAPTER_BLUEPRINTS
        and cause.found == 6
        and cause.expected == 7
    )


def require_frozen_v6_stage4_subject(
    store: ArtifactStore,
    *,
    record_human_edit_edge: bool = False,
) -> FrozenV6Stage4Subject:
    """证明当前 v6 字节就是现存 Stage4A 候选所审的那份历史真相。"""
    if store.ctx.readonly and not record_human_edit_edge:
        return readonly_snapshot_clone(
            store.ctx, ('frozen-v6-stage4-subject',),
            lambda: _require_frozen_v6_stage4_subject_uncached(store),
        )
    return _require_frozen_v6_stage4_subject_uncached(
        store, record_human_edit_edge=record_human_edit_edge)


def _require_frozen_v6_stage4_subject_uncached(
    store: ArtifactStore,
    *,
    record_human_edit_edge: bool = False,
) -> FrozenV6Stage4Subject:
    blueprint, payload, blueprint_sha = store.read_frozen_stage2_blueprints_v6(
        record_stage4_design_edge=record_human_edit_edge)
    if blueprint.provenance.root_clean_sha1 != store.ctx.root_clean_sha1:
        raise UpstreamStale(
            '冻结 v6 Blueprint 的 root_clean_sha1 不等于当前 clean_text 根')
    provenance_payload = payload.get('_provenance')
    recorded_inputs = (
        provenance_payload.get('inputs')
        if isinstance(provenance_payload, dict) else None
    )
    source_report = store.freshness_of_inputs(
        STAGE2_CHAPTER_BLUEPRINTS, recorded_inputs)
    if source_report.state != 'fresh':
        raise UpstreamStale(
            '冻结 v6 Blueprint 上游无法证明仍匹配：'
            f'{source_report.state}/{source_report.reason}')

    if not store.exists(STAGE4_BLIND_CANDIDATE):
        raise UpstreamMissing(
            '冻结 v6 兼容只对已存在且绑定它的 Stage4A 候选开放')
    candidate, candidate_sha = store.read_own_with_sha256(
        BlindCandidate, STAGE4_BLIND_CANDIDATE)
    if candidate.provenance.root_clean_sha1 != store.ctx.root_clean_sha1:
        raise UpstreamStale('Stage4A 候选不是当前 clean_text 根上的主体')

    edge = (candidate.provenance.inputs or {}).get(
        paths.input_key(STAGE2_CHAPTER_BLUEPRINTS))
    if edge is None or set(candidate.provenance.inputs or {}) != {
            paths.input_key(STAGE2_CHAPTER_BLUEPRINTS)}:
        raise ArtifactUnreadable(
            'Stage4A 候选没有且仅有一条 Blueprint 来源边，不能证明历史主体')
    raw = store._read_bytes(STAGE2_CHAPTER_BLUEPRINTS)
    expected_file = prov.fingerprint_bytes(raw)
    expected_consumed = prov.projection_fingerprint(
        frozen_v6_stage4_design_blueprints(payload))
    if edge.file != expected_file or edge.consumed != expected_consumed:
        raise UpstreamStale(
            'Stage4A 候选绑定的 v6 Blueprint 文件/consumed 指纹与当前字节不一致')

    try:
        truth, mapping = build_frozen_v6_blind_projection(
            payload, root_clean_sha1=store.ctx.root_clean_sha1)
    except BlindPlanError as exc:
        raise ArtifactUnreadable(
            f'冻结 v6 无法重建当时的 blind truth：{exc}') from exc
    if truth.get('blind_contract') != FROZEN_V6_BLIND_CONTRACT:
        raise ArtifactUnreadable('冻结 v6 重建出了非 v1 blind contract')
    if (
        candidate.alias_algorithm != mapping.get('alias_algorithm')
        or candidate.truth_sha256 != mapping.get('truth_sha256')
        or candidate.provenance.alias_algorithm != mapping.get('alias_algorithm')
        or candidate.provenance.truth_sha256 != mapping.get('truth_sha256')
        or candidate.fact_aliases != mapping.get('fact_aliases')
        or candidate.participant_aliases != mapping.get('participant_aliases')
    ):
        raise UpstreamStale(
            '冻结 v6 重建的 v1 truth/mapping 与当前 Stage4A 候选主体不一致')
    return FrozenV6Stage4Subject(
        blueprint=blueprint,
        blueprint_payload=payload,
        blueprint_sha256=blueprint_sha,
        source_freshness=source_report,
        truth=truth,
        mapping=mapping,
        candidate=candidate,
        candidate_sha256=candidate_sha,
    )


def frozen_v6_stage4_subject_or_none(
    store: ArtifactStore,
) -> FrozenV6Stage4Subject | None:
    """状态面探针；任何不完整/漂移都保留普通 unreadable 语义。"""
    try:
        return require_frozen_v6_stage4_subject(store)
    except (ArtifactUnreadable, UpstreamMissing, UpstreamStale, OSError, ValueError):
        return None


def require_frozen_v6_downstream_subject(
    store: ArtifactStore, rel: str,
) -> str:
    """证明一份现存 Stage4 正式制品只因 v7 shape break 显示 unknown。

    该口不注册进普通 freshness 投影：只有 approvals/state/delivery 的只读兼容
    调用方能使用。除了 Blueprint 这条旧 consumed 边外，制品的所有其他来源边
    必须仍由活动量尺判为 fresh/match；因此代码变化不会把真实上游漂移洗掉。
    """
    if store.ctx.readonly:
        return readonly_snapshot_clone(
            store.ctx, ('frozen-v6-downstream-subject', rel),
            lambda: _require_frozen_v6_downstream_subject_uncached(store, rel),
        )
    return _require_frozen_v6_downstream_subject_uncached(store, rel)


def _require_frozen_v6_downstream_subject_uncached(
    store: ArtifactStore, rel: str,
) -> str:
    frozen = require_frozen_v6_stage4_subject(store)
    return _prove_frozen_v6_downstream_subject(store, rel, frozen)


def _prove_frozen_v6_downstream_subject(
    store: ArtifactStore,
    rel: str,
    frozen: FrozenV6Stage4Subject,
) -> str:
    """在已证明的同一 frozen 主体上闭合一份白名单下游。"""
    projection = _FROZEN_DOWNSTREAM_PROJECTIONS.get(rel)
    if projection is None:
        raise ArtifactUnreadable(f'{rel} 不属于冻结 v6 Stage4 下游兼容白名单')
    spec = artifact_spec(rel)
    if spec is None:
        raise ArtifactUnreadable(f'{rel} 没有 current artifact contract')
    artifact, _payload = store.read_own(spec.model, rel)
    report = store.freshness(rel)
    edge_by = {edge.key: edge for edge in report.edges}
    blueprint_key = paths.input_key(STAGE2_CHAPTER_BLUEPRINTS)
    blueprint_status = edge_by.get(blueprint_key)
    if (
        report.state != 'unknown'
        or report.reason != 'consumed_unresolved'
        or report.ghosts
        or blueprint_status is None
        or blueprint_status.state != 'unknown'
        or blueprint_status.reason != 'consumed_unresolved'
        or any(
            edge.state != 'fresh' or edge.reason != 'match'
            for key, edge in edge_by.items() if key != blueprint_key
        )
    ):
        raise UpstreamStale(
            f'{rel} 并非仅由冻结 v6 consumed shape break 导致 unknown：'
            f'{report.state}/{report.reason}')
    inputs = artifact.provenance.inputs or {}
    edge = inputs.get(blueprint_key)
    if edge is None:
        raise ArtifactUnreadable(f'{rel} 没有 Blueprint 来源边')
    raw = store._read_bytes(STAGE2_CHAPTER_BLUEPRINTS)
    if (
        edge.file != prov.fingerprint_bytes(raw)
        or edge.consumed != prov.projection_fingerprint(
            projection(frozen.blueprint_payload))
    ):
        raise UpstreamStale(f'{rel} 的冻结 v6 Blueprint 来源边不匹配当前原始字节')
    digest = store.full_sha256_or_none(rel)
    if digest is None:
        raise UpstreamMissing(f'{rel} 在关闭式读取后消失')
    return digest


def require_frozen_v6_delivery_subject(
    store: ArtifactStore,
) -> FrozenV6DeliverySubject:
    """证明最终组装实际依赖的 frozen-v6 Stage4 链仍逐字闭合。

    Stage7 不能只看到一份历史 A 候选就跳过 current Blueprint：B 的 evidence、
    最终计划和写作任务书必须同时存在，且各自只有 Blueprint consumed shape
    break 这一条 ``unknown``。缺失、其他 ghost、真实 stale 或任一字节漂移都
    原样关闭；本口不补字段、不重写产物，也不开放任何 producer。
    """
    frozen = require_frozen_v6_stage4_subject(store)
    downstream = {
        rel: _prove_frozen_v6_downstream_subject(store, rel, frozen)
        for rel in _FROZEN_DELIVERY_DOWNSTREAMS
    }
    return FrozenV6DeliverySubject(
        stage4=frozen,
        downstream_sha256=downstream,
    )


__all__ = [
    'LEGACY_FROZEN_CONTRACT', 'LEGACY_FROZEN_REVIEW_CONTRACT',
    'LEGACY_DISCLOSURE',
    'FrozenV6Stage4Subject', 'FrozenV6DeliverySubject',
    'is_exact_frozen_v6_shape_break',
    'require_frozen_v6_stage4_subject',
    'frozen_v6_stage4_subject_or_none',
    'require_frozen_v6_downstream_subject',
    'require_frozen_v6_delivery_subject',
]
