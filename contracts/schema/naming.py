#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""naming —— NameMap v6 与目标名称位（命名前移批，2026-10-18）。

【交付变换契约，不是第五类 semantic truth】
NameMap 只拥有「char_ref → 最终表层名称」一个新事实。旧 canonical／surfaces／
化名归属归 Stage2 的 `name_structure`（见 source.py）；本模块不复制旧信息。
`absence=unchanged` 不能掩盖模型漏答：compile 侧以明确应改名集合核对完整覆盖，
合法保留必须带理由（`keep_reason`）。

【命名前移（rc26）】
`str06.stage4.naming` 置于 Stage2 compile 之后、Preserve 之前：目标姓名先进
正式产物（`stage4/naming/name_map.json`），再进入目标简报与设计链；末端改名
与替换证明随之退役（见 docs/REQUIREMENTS.md 目标命名前置批次）。

【名称位与 full】
name 的 family/given 由 Naming 按源名称结构显式赋值；`full` 由 compiler 拼接，
wire 不携带。独立化名（aliases）不由新姓或新全名机械猜出：需要改名时由 Naming
对已登记 alias_id 显式赋值，缺席即保留。
"""
from __future__ import annotations

from typing import Literal

from pydantic import Field, model_validator

from contracts.schema.base import StrictModel
from contracts.schema.common import FreeProvenance, LlmProvenance

NamePart = str  # 非空名称部件；长度/字符面由 compile 校验（schema 只保非空语义）
NAMING_VALIDATOR_VERSION = 'str06-naming-v1'


class TargetName(StrictModel):
    """目标名称：按源名称结构给出必要名称位。

    `full` 是 compiler 拼接结果（family+given）；wire 里允许 None。family/given
    可以全空——只改化名位的人物（源结构无姓名位）走这一形态；全空且无化名位
    的条目在 compile 侧拒绝，不是合法改名。"""
    family: NamePart | None = None
    given: NamePart | None = None
    full: NamePart | None = None


class NameAssignment(StrictModel):
    """一个人物的目标名称或保留声明。

    「name 与 keep 恰好有一个」：改名给 name（keep_reason 必须为 None）；
    保留给 keep_reason（name 必须为 None，理由登记在案）。aliases 只登记
    需要改名的化名（alias_id → 新字面），缺席 = 保留。
    """
    char_ref: str = Field(min_length=1)
    name: TargetName | None = None
    aliases: dict[str, NamePart] = Field(default_factory=dict)
    keep_reason: str | None = None

    @model_validator(mode='after')
    def _exactly_one_choice(self) -> 'NameAssignment':
        if (self.name is None) == (self.keep_reason is None):
            raise ValueError(
                f'{self.char_ref} 的 name 与 keep_reason 必须恰好给出一个：'
                '改名给 name，保留给带理由的 keep_reason')
        return self


class NameMapV6(StrictModel):
    """NameMap v6（canonical，`stage4/naming/name_map.json`；命名前移批）。

    entries 覆盖应改名集合的每一个人（漏项/多余项归 compile 核对）；char_ref
    唯一。空 entries 非法——至少要显式处理一个人物。v5→v6：产物从 Stage7 末端
    前移到 Stage4（`str06.stage4.naming`），目标姓名成为 Preserve 与设计链的
    正式输入；旧路径旧字节不静默采用（I-13）。"""
    contract_version: Literal['str06-rc26'] = 'str06-rc26'
    entries: tuple[NameAssignment, ...] = Field(min_length=1)
    provenance: FreeProvenance | LlmProvenance | None = Field(
        default=None, alias='_provenance', exclude=True)

    @model_validator(mode='after')
    def _unique_char_refs(self) -> 'NameMapV6':
        refs = [e.char_ref for e in self.entries]
        if len(set(refs)) != len(refs):
            raise ValueError(f'char_ref 重复：{sorted(r for r in refs if refs.count(r) > 1)}')
        return self
