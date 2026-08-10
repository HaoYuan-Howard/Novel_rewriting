#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
base.py —— 全部产物模型的底座。

【本层的方向】`docs/schema/*.md` 是**源**，这里的模型照它写。方向别搞反：
文档不是从模型生成的，模型也不许自己长出文档里没有的字段。

【两条 Pydantic 默认行为与本项目的纪律相反，必须显式改掉】

  ① `extra` 默认是 `ignore` —— 未知字段被**静默丢弃**。
     这正是 `Q-1`（不静默）要挡的形态：上游多写了一个字段、或字段名拼错，
     校验照常通过而那个值消失了。⇒ 一律 `extra='forbid'`。
     它同时是「模型产出了我们没要的字段」这一类的**唯一守卫**——
     落盘之后再记账已经晚了一层，而且那时已经花过钱。

  ② 给可选字段配默认值，会让「没有这个字段」和「字段是 null」混掉。
     那是三态问题的重现（`FRESHNESS` / `JOB_STATE` 的 `unknown` 防的就是它）。
     ⇒ 需要区分时用 `Field(...)` 强制必填，或显式建模成三态，**不要靠默认值兜**。

【为什么模型放在 `contracts/` 而不是运行时包里】
这一份要同时喂四个消费方，而其中**三个不需要运行时**：生成前端 TS 类型、
拿盘上语料给 schema 做体检、给人读的契约对照。它们如果要 import 运行时包，
就会连带把 LLM 客户端与 HTTP 栈一起拖进来。`tests/` 现在只 import `contracts`，
这个性质值钱。
"""
from __future__ import annotations

from typing import Annotated

from pydantic import BaseModel, ConfigDict, StringConstraints

# ─────────────────────────────────────────────────────────────────────────────
# 两把尺子 —— 故意的两把，规则只有一条：**10 位永不与 40 位比较**
# ─────────────────────────────────────────────────────────────────────────────
#
# 盘上实测：`stage1/partition_report.json` 的 `source_sha1` 是 40 位全宽，
# 而 `_provenance.inputs` / `draft_sha1` / `judged[].sha1` 是 10 位。
# 详细论证见 `docs/schema/_common.md` §二。
#
# 把它做成**两个类型**而不是两个约定，是为了让混用在建模期就是类型错误、
# 在加载期就是校验失败——这一格在需求表的「谁在查」里填 `载体`。
# ⚠ 原型的病不是「有两个宽度」，是**同一个字段在两处用不同宽度**
#   （`n1_partition` 记全宽而 `stage2_common` 记 10 位），
#   于是同一份文件的指纹在两份产物里长得不一样，谁也不敢拿它们对比。

#: 内容身份。回答「**有人换过原文吗**」，人可能拿 `sha1sum` 手工复核。截断买不到任何东西。
Sha1 = Annotated[str, StringConstraints(pattern=r'^[0-9a-f]{40}$')]

#: 来源指纹。40 bit，本项目量级碰撞概率约 1e-6，而它天天出现在报告里，短一点可读。
Fp10 = Annotated[str, StringConstraints(pattern=r'^[0-9a-f]{10}$')]


class StrictModel(BaseModel):
    """全部产物模型的基类。

    `extra='forbid'` 是**不可协商**的那一条，理由见模块 docstring ①。

    ⚠ `validate_assignment` 也开着：产物模型在落盘前会被代码改写几处
    （`beat_id` 归一 / `span` 回取 / `address` 四项…），改写之后如果不再校验，
    「先校验后落盘」就只校验了模型刚被构造出来的那一刻。
    """

    model_config = ConfigDict(
        extra='forbid',
        validate_assignment=True,
        # 盘上产物是 JSON，键名就是字段名；别让 Pydantic 顺手接受别名以外的东西。
        populate_by_name=True,
        # 字符串两端的空白是内容的一部分——`clean_text` 那一层明写「不 strip 行尾」。
        str_strip_whitespace=False,
    )


class FrozenModel(StrictModel):
    """已经落过盘、不该再被改的那些（`Finding` / `Provenance`）。

    冻结不是洁癖：一条 finding 的 `sev` 若能在构造之后被改掉，
    「它与判据表说的一致」这个后置条件就只在构造那一刻成立过。
    """

    model_config = ConfigDict(
        extra='forbid',
        frozen=True,
        populate_by_name=True,
        str_strip_whitespace=False,
    )
