#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""policy —— PreservationPolicy：Grounding.Preserve 的版本化策略（F0 RC）。

【为什么 Policy 是 Preserve 的独立输入】
Preserve 只消费 SourceTruth、版本化 Policy 和执行配置；不接收 Design 或 Bind 的
失败结果。把策略从 prompt / 运行时代码里提出来做成显式版本化对象，是为了让
「同一份 SourceTruth + 同一版 Policy ⇒ 可恢复的 paid checkpoint」这句话有可校验的
身份——checkpoint 记 `policy_id + policy_version`，重放与审计都对着它对账。

【RC 边界】
`PreserveKind` 闭集是初版（试点三轮的保存类别归纳）；F1 反例标定后允许增删，
但**必须升 `version`**——类别集合的变化会改变 Preserve 结果，旧 checkpoint 的
身份绑在旧版 Policy 上，不能被静默重解释。

【版本沿革】
v1（RC 初版）：闭集与 `PRESERVE_RULES` 一行式字段表。
v2（2026-09-26 质量改进批次 B2）：prompt 规则重写——七类 kind 的定义与保存对象
按 kind 分列、satisfaction 写「保住同样作用必须成立什么」、must_preserve 改用
反事实判据、同一事实不跨 kind 重复。类别集合不变，但判据变化会改变 Preserve
结果，故升版；旧 checkpoint 不重解释。
v3（2026-09-26 质量改进批次二）：源真值改 v13 拍模型（三类转移＋场景边界＋时间
模式），prompt 规则随之补写两类必须保住的反差（说辞与行为、隐藏与揭示的时点）、
iterative 重复行为的作用与 loop 问题／resolution 的兑现。类别集合不变，规则变化
改变 Preserve 结果，故升版；旧 checkpoint 不重解释。
v4（2026-09-30 R9b）：satisfaction 明写动机、主体、因果及禁止改法；阶段义务
分别承载并声明必要条件，回看未入选拍中的关键铺垫；身份与称谓揭示时点分离。
类别集合不变，判据集中升版；版本唯一登记为 STAGE4_PRESERVE_POLICY_VERSION。
"""
from __future__ import annotations

from enum import Enum

from pydantic import Field

from contracts.schema.base import StrictModel


class PreserveKind(str, Enum):
    """保存语义类别的闭集（RC）。

    Obligation 的 `kind` 取值域；「必保要求必须有实质满足条件，不能被
    reinforcement 的 0..N 稀释」落在 `SourceObligation.must_preserve` 与
    `satisfaction` 的搭配上（见 grounding.py），不在本枚举里分档。
    """

    EVENT_CORE = 'event_core'          # 事件内核（谁对谁做了什么、结果如何）
    REVEAL = 'reveal'                  # 读者揭示时机
    CHARACTER_ARC = 'character_arc'    # 人物内在变化
    CAUSAL = 'causal'                  # 必要因果（前因后果不可断）
    WORLD_FACT = 'world_fact'          # 世界事实 / 规则
    IDENTITY = 'identity'              # 身份与称谓约束
    INITIAL_STATE = 'initial_state'    # 书级初始事实（Source initial facts）


class PreservationPolicy(StrictModel):
    """一次 Preserve 请求携带的策略身份。

    只有身份与类别闭集；**执行配置不进 Policy**——provider/model/请求身份记在
    `PreserveCheckpoint` 的执行身份里，同一版 Policy 换模型重跑是可区分的两份
    checkpoint，不能混作同一身份。
    """

    policy_id: str = Field(min_length=1)
    version: int = Field(ge=1)
    preserve_kinds: tuple[PreserveKind, ...] = Field(min_length=1)
