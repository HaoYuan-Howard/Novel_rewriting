#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""receipt —— 章级接受回执（F5 RC4）。

【为什么是章级】
正文严格按章串行生成、一次输出整章自然正文；整章自由正文不提供可靠的场景
边界，接受与提交的最小单位因此是章。场景级 SceneReceipt 与 FidelityVerdict
随生产正文 Judge 一并退役（取消 Judge 是已确认产品方向；机械检查不证明章内
揭示位置或逐场义务兑现，这一限制如实进入披露）。

【绑定语义】
回执绑定：章序、全书 IR 身份（`book_ir_sha256`，IR 任何 Writer 可见变化使全
书回执失效）、实际前文（`prior_text_sha256`，第 k 章正文改变使后缀失效）、
改名前正文、被接受 attempt 的实际请求身份、协议与校验版本。无有效回执的
文本不能作为前文或交付输入。`prose_sha256` 绑定改名前正文：换 NameMap 不使
回执失效，只改变交付身份。

【语义边界】
回执只记录机械结果；`semantic` 恒为 `unverified`——机械通过不升级为语义已验
（I-42）。篇幅偏差是唯一允许在重写额度耗尽后披露放行的软检查
（`length_disclosed`）；专名泄漏、空文、编码、包装与截断不能披露放行。
"""
from __future__ import annotations

from typing import Literal

from pydantic import Field, model_validator

from contracts.schema.base import StrictModel
from contracts.schema.common import FreeProvenance, LlmProvenance

__all__ = ['ChapterLengthDisclosure', 'ChapterReceipt']


class ChapterLengthDisclosure(StrictModel):
    """篇幅读数：计数排除空白；程序章号与 Hook 不在本章文本内。"""

    char_count: int = Field(ge=0, description='非空白字符数')
    target_chars: int = Field(ge=1)
    band_low: int = Field(ge=0)
    band_high: int = Field(ge=0)
    within_band: bool
    #: (char_count - target) / target；带内候选也可能带非零偏差读数。
    deviation_ratio: float

    @model_validator(mode='after')
    def _window(self) -> 'ChapterLengthDisclosure':
        if self.band_low > self.band_high:
            raise ValueError('篇幅带下界不得大于上界')
        return self


class ChapterReceipt(StrictModel):
    """章级接受回执（`stage5/acceptance/body/ch{nn}.json`）。"""

    contract_version: Literal['str06-rc4'] = 'str06-rc4'
    chapter_ordinal: int = Field(ge=1)
    book_ir_sha256: str = Field(min_length=64, max_length=64)
    prior_text_sha256: str = Field(min_length=64, max_length=64,
                                   description='本章之前全部有效前文（改名前）的身份；第 1 章为空文本 SHA')
    prose_sha256: str = Field(min_length=64, max_length=64,
                              description='绑定改名前本章正文')
    request_identity: str = Field(min_length=1, description='被接受 attempt 的实际请求身份')
    protocol_version: str = Field(min_length=1)
    renderer_version: str = Field(min_length=1)
    validator_version: str = Field(min_length=1)
    status: Literal['accepted', 'rejected']
    mechanical_checks: tuple[str, ...] = Field(
        min_length=1, description='validator 版本执行的机械检查项名单')
    length: ChapterLengthDisclosure
    #: 重写额度耗尽后按偏差最小提交的带外候选必须显式披露，不得静默当带内。
    length_disclosed: bool = False
    semantic: Literal['unverified'] = 'unverified'
    #: 来源账（F7a）：盘上键 `_provenance`；`exclude=True` 保 prompt 字节不变。
    provenance: FreeProvenance | LlmProvenance | None = Field(
        default=None, alias='_provenance', exclude=True)

    @model_validator(mode='after')
    def _hash_charset(self) -> 'ChapterReceipt':
        for name in ('book_ir_sha256', 'prior_text_sha256', 'prose_sha256'):
            value = getattr(self, name)
            if any(c not in '0123456789abcdef' for c in value):
                raise ValueError(f'{name} 必须是小写十六进制 SHA-256')
        return self
