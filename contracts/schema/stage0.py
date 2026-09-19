#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
stage0.py —— 对应 `docs/schema/stage0.md`。非正文剔除，**不调 LLM**。

⚠ 另一份产物 `stage0/clean_text.txt` **不是 JSON**，它的 schema 是字节形态
（utf-8 无 BOM · 恒 `\\n` · 只删段不改字）。字节形态的守卫在写盘口那一层
（`novel/store.py::write_text`），不在这里。

【本层唯一没有 `_provenance` 的理由】
本层是全链源头，`inputs` 恒空；原文身份由 `Stage0Source` 表达，producer 代码
身份记在 `Stage0Transform.code`。

── 2026-09-18 STR-03-S0：v3 单次确定性变换 ───────────────────────────────

全自动裁决后 Stage0 不再有人工审批 / 候选稿 / 修订史。权威 JSON 从
「revision 链 + approval」收成一次机械 transform：

  · `stage0/revisions.json` 路径保留（避免无收益 rename），内容为
    `Stage0Transform` v3；v2 拒读，由 `stage0.remove --source` 重建。
  · 正文权威在可重建前提下由「归一化原文 − removed spans」确定；
    `clean_text.txt` 是物化投影，JSON 只存 `clean_sha1` / `body_code_points`。
  · 闸门语义是 `admission.status=passed`（mechanical），不是 `approved`。
"""
from __future__ import annotations

from typing import Annotated, Literal

from pydantic import Field, StringConstraints, model_validator

from contracts.invariants import NONBODY_KINDS, REMOVED_ORIGIN
from contracts.schema.base import Sha1, StrictModel

Sha256 = Annotated[str, StringConstraints(pattern=r'^[0-9a-f]{64}$')]

#: API / 旧屏兼容：v3 机跑恒为 machine；人工写面已退役，不再产生其它取值。
Stage0RevisionOrigin = Literal['machine', 'human_edit', 'human_adopt']


# ── 原文住在哪儿：**两种来源，处置相反** ────────────────────────────────────

class ExternalFileRef(StrictModel):
    """外部语料：书目录之外的绝对路径（定位符）。

    内容身份以 `Stage0Source.raw_sha256` / `normalized_source_sha1` 为主；
    路径消失即 ghost，不猜别处同名文件。
    """

    kind: Literal['external_file']
    absolute_path: str = Field(min_length=1)


class ManagedUploadRef(StrictModel):
    """界面上传：`<base>/_service/uploads/<blob_sha256>/<filename>`。"""

    kind: Literal['managed_upload']
    blob_sha256: Sha256
    filename: str = Field(min_length=1)


SourceRef = Annotated[
    ExternalFileRef | ManagedUploadRef, Field(discriminator='kind')]


class RemovedSegment(StrictModel):
    """已验证的剔除事实；坐标恒为归一化原文码点半开区间。"""

    kind: Literal[NONBODY_KINDS]
    marker_line: str = Field(min_length=1)
    span: tuple[int, int]
    origin: Literal[REMOVED_ORIGIN]

    @model_validator(mode='after')
    def _span_is_a_half_open_interval(self) -> RemovedSegment:
        lo, hi = self.span
        if not 0 <= lo < hi:
            raise ValueError(f'span={self.span} 不是合法的半开区间（要求 0 <= 起 < 止）')
        return self


class RequestedRemoval(StrictModel):
    """历史编辑命令 DTO（节点已退役）；保留类型供存量解码，现役不写。"""

    kind: Literal[NONBODY_KINDS]
    span: tuple[int, int]

    @model_validator(mode='after')
    def _span_is_a_half_open_interval(self) -> RequestedRemoval:
        lo, hi = self.span
        if not 0 <= lo < hi:
            raise ValueError(f'span={self.span} 不是合法的半开区间（要求 0 <= 起 < 止）')
        return self


# ── 可疑项：按 current 内容现算，不落 transform ───────────────────────────


class EarlyCutWarning(StrictModel):
    kind: Literal['early_cut']
    body_code_points: int = Field(ge=0)
    total_code_points: int = Field(ge=1)


class DropRatioWarning(StrictModel):
    kind: Literal['drop_ratio']
    removed_code_points: int = Field(ge=1)
    total_code_points: int = Field(ge=1)


class LongMarkerWarning(StrictModel):
    kind: Literal['long_marker']
    span_start: int = Field(ge=0)
    marker_core_code_points: int = Field(ge=1)


Stage0Warning = Annotated[
    EarlyCutWarning | DropRatioWarning | LongMarkerWarning,
    Field(discriminator='kind'),
]


class Stage0Admission(StrictModel):
    """机械契约通过收据。不是语义批准。"""

    status: Literal['passed']
    scope: Literal['mechanical']
    at: str = Field(min_length=1)
    by: str = Field(min_length=1)

    @model_validator(mode='after')
    def _actor_not_blank(self) -> Stage0Admission:
        if not self.by.strip():
            raise ValueError('admission.by 不得空白')
        return self


class Stage0Source(StrictModel):
    """一次 transform 的原文前提。"""

    source_ref: SourceRef
    #: 原始文件字节 SHA-256（未解码前）。
    raw_sha256: Sha256
    #: 实际用于无损解码的编码名（utf-8 / gb18030 等）。
    encoding: str = Field(min_length=1)
    #: 去 BOM、换行归一后文本的 sha1（Stage1 根身份链仍消费此量纲）。
    normalized_source_sha1: Sha1
    total_code_points: int = Field(ge=1)


class Stage0Transform(StrictModel):
    """`stage0/revisions.json` v3。一次确定性机械变换的唯一 JSON 权威。"""

    source: Stage0Source
    code: str = Field(min_length=1)
    #: 内容身份：前像含 source 身份 + removed + clean_sha1，不含 at/code。
    transform_id: Sha256
    removed: list[RemovedSegment] = Field(default_factory=list)
    clean_sha1: Sha1
    body_code_points: int = Field(ge=0)
    admission: Stage0Admission

    @model_validator(mode='after')
    def _removed_ordered_and_machine_only(self) -> Stage0Transform:
        spans = [segment.span for segment in self.removed]
        if spans != sorted(spans):
            raise ValueError('removed 必须按原文 span 顺序排列')
        for index, segment in enumerate(self.removed):
            if segment.origin != 'machine':
                raise ValueError('v3 transform 只允许 origin=machine 的剔除段')
            lo, hi = segment.span
            if index and self.removed[index - 1].span[1] > lo:
                raise ValueError(f'removed[{index - 1}] 与 removed[{index}] 重叠')
        return self


# ── 运行时投影（不落盘）───────────────────────────────────────────────────


class Stage0Snapshot(StrictModel):
    """当前 Stage0 状态的运行时投影；不落盘、不进 artifact registry。"""

    original_filename: str = Field(min_length=1)
    source_ref: SourceRef
    code: str = Field(min_length=1)
    raw_sha256: Sha256
    encoding: str = Field(min_length=1)
    normalized_source_sha1: Sha1
    clean_sha1: Sha1
    removed: list[RemovedSegment]
    total_code_points: int = Field(ge=1)
    body_code_points: int = Field(ge=0)
    warnings: list[Stage0Warning]
    #: 与盘上 transform_id 同值；API 仍投影为 revision_id。
    transform_id: Sha256
    admission: Stage0Admission

    @property
    def revision_id(self) -> str:
        return self.transform_id

    @property
    def revision_origin(self) -> Stage0RevisionOrigin:
        return 'machine'
