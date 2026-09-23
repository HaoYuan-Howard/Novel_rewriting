#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""delivery —— 接受正文 → 交付正文的确定性组装与残留判定（F1）。

【职责边界】compiler：确定性转换与归一，不做语义推断。Stage7 根据有效
name_map、选定 Hook 和 accepted body 做替换与组装；同一 accepted body、同一
Hook、同一有效映射与组装契约，重放得到**相同交付字节**——本模块就是那条
「组装契约」的 RC 实现，重放稳定性由排序与拼接规则唯一决定。

【claimed/kept 语义】拒绝「未认领、应替换的残留」；「合法保留称谓及被认领
区间内的字面」不误报。不得用「所有源名字串都不能出现」替代——kept 就是
认领账，逐条给出理由。
"""
from __future__ import annotations

import hashlib
from typing import Mapping, NamedTuple

from pydantic import Field

from contracts.schema.base import StrictModel


class Substitution(StrictModel):
    """一条名称替换：源名 → 目标名。长短并存时按长度降序应用（长名优先），
    防止「裴书远」被「裴」先拆掉。"""

    source_name: str = Field(min_length=1)
    target_name: str = Field(min_length=1)


class KeptLiteral(StrictModel):
    """一条认领保留：这个字面允许出现在交付正文里，理由登记在案。"""

    literal: str = Field(min_length=1)
    reason: str = Field(min_length=1)


class DeliveryContract(NamedTuple):
    """一次交付的完整身份材料：重放字节相同 ⟺ 三件相同。"""

    hook: str
    substitutions: tuple[Substitution, ...]
    kept: tuple[KeptLiteral, ...]


def render_delivery(body: str, contract: DeliveryContract) -> bytes:
    """确定性组装：hook + '\n\n' + 替换后的 body，UTF-8 字节。

    替换顺序唯一：substitutions 按源名长度降序、同长按字典序——顺序本身就是
    契约的一部分，改顺序就是改契约（会破坏重放一致性，测试钉住）。
    """
    text = contract.hook + '\n\n' + body
    ordered = sorted(
        contract.substitutions,
        key=lambda s: (-len(s.source_name), s.source_name))
    for sub in ordered:
        text = text.replace(sub.source_name, sub.target_name)
    return text.encode('utf-8')


def check_residual(
    delivered_text: str,
    contract: DeliveryContract,
) -> list[str]:
    """残留判定（claimed/kept）。

    拒绝：应替换的源名仍出现且未被 kept 认领。
    不误报：kept 认账的字面。判定单位是「源名字面」，不是任意源名子串的
    存在性——「所有源名字串都不能出现」不是本判据。
    """
    kept_literals = {k.literal for k in contract.kept}
    problems: list[str] = []
    for sub in contract.substitutions:
        if sub.source_name in delivered_text and sub.source_name not in kept_literals:
            problems.append(f'应替换的源名 {sub.source_name!r} 在交付正文中残留且未认领')
    return problems


def delivery_identity(contract: DeliveryContract) -> str:
    """交付身份材料（供 Stage7 另行绑定交付身份；不与 Stage5 receipt 混同）。"""
    parts = [contract.hook]
    parts.extend(
        f'{s.source_name}=>{s.target_name}'
        for s in sorted(
            contract.substitutions,
            key=lambda s: (s.source_name, s.target_name)))
    parts.extend(
        f'keep:{k.literal}({k.reason})'
        for k in sorted(contract.kept, key=lambda k: (k.literal, k.reason)))
    return hashlib.sha256('\x1f'.join(parts).encode('utf-8')).hexdigest()
