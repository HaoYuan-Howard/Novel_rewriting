#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""receipt —— 接受回执最小接口与 Fidelity 判定（F0 RC）。

【最小七字段】
计划原文钉死的接受回执接口：scene_id / attempt_id / prose_sha256 /
writer_context_sha256 / execution_slice_sha256 / judge_contract_version / status。
`extra='forbid'` 同时拒绝两类混入：全书 plan hash、审计时间戳等无关 metadata
（它们**不作为场景有效性的硬匹配条件**），以及任何「顺手」的修复字段。

【绑定语义】
`prose_sha256` 绑定**替换人名前**的具体正文——接受正文与交付正文分离：更换
name_map 不使本回执失效，只影响 Stage7 交付结果。Context／slice 摘要绑定本场
实际消费的规范化业务内容及必要契约身份（`ExecutionSlice` 是其唯一身份来源）。

【Judge 边界】
FidelityVerdict 只验证并报告：`failure_origin ∈ {writer, upstream_ir,
unverifiable}`（未知不伪装通过），没有 edit/repair 字段——Judge 不编辑 truth，
修复回到 owner（Detection ≠ Ownership）。
"""
from __future__ import annotations

from enum import Enum

from pydantic import Field, model_validator

from contracts.schema.base import StrictModel


class ReceiptStatus(str, Enum):
    ACCEPTED = 'accepted'
    REJECTED = 'rejected'


class FailureOrigin(str, Enum):
    """Judge 的错误归属闭集：只诊断，不修。"""

    WRITER = 'writer'
    UPSTREAM_IR = 'upstream_ir'
    UNVERIFIABLE = 'unverifiable'


class SceneReceipt(StrictModel):
    """场景接受回执（`stage5/acceptance/fidelity/scene_{nnn}.json` 的核心）。"""

    scene_id: str = Field(min_length=1)
    attempt_id: str = Field(min_length=1)
    prose_sha256: str = Field(min_length=64, max_length=64)
    writer_context_sha256: str = Field(min_length=64, max_length=64)
    execution_slice_sha256: str = Field(min_length=64, max_length=64)
    judge_contract_version: str = Field(min_length=1)
    status: ReceiptStatus

    @model_validator(mode='after')
    def _slice_matches_context_owner(self) -> 'SceneReceipt':
        # receipt 不携带 Context 内容，但三份 hash 都必须是小写十六进制 64 位——
        # 大写/短 hash 的混入在构造期即拒，防「看起来绑定了、实际对不上账」。
        for name in ('prose_sha256', 'writer_context_sha256', 'execution_slice_sha256'):
            value = getattr(self, name)
            if any(c not in '0123456789abcdef' for c in value):
                raise ValueError(f'{name} 必须是小写十六进制 SHA-256')
        return self


class FidelityFinding(StrictModel):
    """单条判定发现：保留 stage/node/item/context 的定位（I-36）。"""

    code: str = Field(min_length=1)
    scene_id: str = Field(min_length=1)
    detail: str = Field(min_length=1)
    origin: FailureOrigin


class FidelityVerdict(StrictModel):
    """Fidelity Judge 的完整判定：receipt + findings。

    刻意没有 verdict 级「修复建议落地」字段；fix_layer 只报 writer|context 的
    试点结论由 findings.origin 承载，上游归因交回写前链路。
    """

    receipt: SceneReceipt
    findings: tuple[FidelityFinding, ...] = Field(default=(), min_length=0)

    @model_validator(mode='after')
    def _findings_bound_to_scene(self) -> 'FidelityVerdict':
        stray = {f.scene_id for f in self.findings} - {self.receipt.scene_id}
        if stray:
            raise ValueError(f'finding 挂在了别的场景上：{sorted(stray)}')
        return self
