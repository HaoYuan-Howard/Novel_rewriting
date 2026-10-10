#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""receipt —— 章级接受回执（F5；rc5 起绑定章包摘要，D2；rc6 拆分绑定身份）。

【为什么是章级】
各章独立成文、一次输出整章自然正文；整章自由正文不提供可靠的场景边界，
接受与提交的最小单位因此是章。场景级 SceneReceipt 与 FidelityVerdict
随生产正文 Judge 一并退役（取消 Judge 是已确认产品方向；机械检查不证明章内
揭示位置或逐场义务兑现，这一限制如实进入披露）。

【绑定语义（rc6，2026-09-28 R2：身份拆分）】
回执分别绑定三把摘要（`novel/stages/common.py::chapter_identities`）：

  · `writer_view_sha256`——写手视图摘要：写手实际可见的章包阅读面（renderer
    渲染字节），材料含 renderer 与写手协议版本。写手看到的变化（导演说明、
    义务、拍事实、篇幅目标、协议或渲染版本）⇒ 该章重购；
  · `validation_sha256`——验证输入摘要：禁用字面、称谓许可与篇幅带。验证
    材料变化 ⇒ 旧机械验证不再成立，先免费重验原章稿；
  · `machine_view_sha256`——Machine 层摘要，**仅审计**：只改 Machine 层字段
    （写手不可见、验证器不消费的工程位，如消费身份摘要）不触发重购。

正文绑定（`prose_sha256`）、被接受 attempt 的实际请求身份、协议、renderer
与校验版本照旧。**不绑定全书 IR 与前文链（D2）**：写手只收当前章包，各章
独立有效、可乱序提交与集合恢复。无有效回执的正文不能作为交付输入。
换 NameMap 不使回执失效，只改变交付身份。

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

__all__ = ['ChapterLengthDisclosure', 'ChapterReceipt',
           'BODY_VALIDATOR_VERSION', 'BODY_MECHANICAL_CHECKS']

#: 机械规则的独立版本轴；规则变化必须换版，消费者先免费重验原章稿。
#: v2（CS-H H3，RC-61）增加 required_literals：必保揭名承载章须出现称谓字面。
BODY_VALIDATOR_VERSION = 'str06-body-mechanical-v2'
#: 回执宣告通过的机械检查项闭集。
BODY_MECHANICAL_CHECKS = (
    'non_empty', 'encoding', 'no_wrapper', 'withheld_literals', 'required_literals', 'length_band',
)


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

    contract_version: Literal['str06-rc17'] = 'str06-rc17'
    chapter_ordinal: int = Field(ge=1)
    writer_view_sha256: str = Field(
        min_length=64, max_length=64,
        description='写手视图摘要（rc6 身份拆分）：写手实际可见的章包阅读面'
                    '（renderer 渲染字节，材料含 renderer 与写手协议版本）；'
                    '写手可见面变化使本章回执失效（重购）')
    validation_sha256: str = Field(
        min_length=64, max_length=64,
        description='验证输入摘要（rc6 身份拆分）：禁用字面、称谓许可与篇幅带；'
                    '验证材料变化使旧机械验证不再成立（先免费重验）')
    machine_view_sha256: str = Field(
        min_length=64, max_length=64,
        description='Machine 层摘要（仅审计，rc6）：只改 Machine 层字段不触发'
                    '重购；用于对账与漂移诊断，不进回执核对')
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
        for name in ('writer_view_sha256', 'validation_sha256',
                     'machine_view_sha256', 'prose_sha256'):
            value = getattr(self, name)
            if any(c not in '0123456789abcdef' for c in value):
                raise ValueError(f'{name} 必须是小写十六进制 SHA-256')
        return self
