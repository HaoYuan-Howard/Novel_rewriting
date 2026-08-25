#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Stage4 A/B 当前相位的关闭式判定。

``evidence_binding.status=infeasible`` 是一份已经付费得到、正式落盘的负结论，
不是“B 还没跑”。只要它仍绑定当前且 fresh 的 A 候选，下一项就只能是显式
``stage4.design --replan``；再次执行 B 只会复用同一候选与同一负结论。

本模块不依赖服务审批或队列。审批视图、队列和 producer 共用这里的一把尺子，
避免浏览器说“重跑 A”、CLI 却仍允许原地重跑 B。
"""
from __future__ import annotations

from contracts.artifacts import STAGE4_BLIND_CANDIDATE, STAGE4_EVIDENCE_BINDING
from contracts.schema.stage4 import BlindCandidate, EvidenceBinding
from novel.errors import NovelError
from novel.stages.stage4.blind import BlindPlanError, candidate_subject_sha256
from novel.store import ArtifactStore


def current_infeasible_binding(store: ArtifactStore) -> EvidenceBinding | None:
    """返回当前 A 已有的 fresh typed 负结论；否则返回 ``None``。

    两份产物都必须能按现役 schema 读回、各自 provenance 明确 fresh，并且 B 的
    业务回显与 provenance 回显都等于当前 A 的完整业务主体 SHA-256。任何未知、
    陈旧或不一致都不能冒充“当前负结论”。
    """
    try:
        candidate_freshness = store.freshness(STAGE4_BLIND_CANDIDATE)
        binding_freshness = store.freshness(STAGE4_EVIDENCE_BINDING)
        candidate, _candidate_raw = store.read_own(
            BlindCandidate, STAGE4_BLIND_CANDIDATE)
        binding, _binding_raw = store.read_own(
            EvidenceBinding, STAGE4_EVIDENCE_BINDING)
        subject = candidate_subject_sha256(candidate.model_dump(
            by_alias=True, mode='json', exclude={'provenance'}))
    except (NovelError, BlindPlanError):
        return None

    if candidate_freshness.state != 'fresh' or binding_freshness.state != 'fresh':
        return None
    if binding.candidate_sha256 != subject:
        return None
    if binding.provenance.candidate_sha256 != subject:
        return None
    return binding if binding.status == 'infeasible' else None


__all__ = ['current_infeasible_binding']
