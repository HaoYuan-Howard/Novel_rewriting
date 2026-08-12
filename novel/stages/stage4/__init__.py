#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""novel.stages.stage4 —— 重构计划 + 写作上下文组装。**全链的收口点。**

【本层与前三层的四处形状差异，都是承重的】

**① 一个 stage 两份产物、两个节点、两节需求。**
`stage4.plan`（付费，`R-5`）产 `plan_chapters.json`；`stage4.context`（0 成本，`R-6`）产
`writer_context.json`。判据表只有**一张**，两份产物共用（`docs/schema/stage4.md` §四）。
⚠ `PORTED_STAGES` 按 **stage** 索引，正向覆盖率取 `R-5 ∪ R-6` ⇒ **不能只移植一半。**

**② LLM 在这一层只产三样东西，其余全是代码写的。**
`reconstruction_strategy` · 逐章三个 goal · **逐拍 `budget_chars`**。
拍怎么切、怎么排、怎么并**不是模型的决定**（`beats.group_beats` 按源文切，只吸收碎拍），
整份 `writer_context` 更是**一个字都不经模型**。
⇒ 读本层的 schema 时**不要套用 stage2 那边「每一个字段都是模型写的」的怀疑口径**。

**③ 确定性核心一律吃 `dict`，不吃模型 —— 这是一次有理由的裁决，不是省事。**
节点走 `store.read(...)` 拿模型（那是载体的活：`PathRefused` + 拒收 + 记上游边），
拿到之后 `model_dump(by_alias=True)` 再交给 `beats` / `measure` / `context` / `checks`。
两条理由，缺一不可：
  · **判据要合成得出反例**（同 `tests/test_stage3.py:157-161`）——`PlanChapter` 上挂着
    `output_ordinal == from_source_ordinals[0]` 的 `model_validator`，模型化之后
    `one_to_one` 那条 HARD 根本构造不出会红的样本；
  · **盘上有一本的计划载体拒收**（`成年人不做选择` 缺 `_provenance`，
    `tests/corpus.py::MISSING_PROVENANCE` 登记在案）——组装器吃 dict，
    那本才进得了「ctx 重组 == 存盘」那条对账。**节点照样拒绝跑它，那是对的。**

**④ 本层的徽章【可以】是 `pass`，而 stage6/stage7 结构上永远不可能。**
判据表里**没有一条 `sev='unver'`**：`budget_band` 的基准残缺、`context_link` 的
`unknown`/`missing`、`params_drift` 的参数缺失都是**产出层的 `unver` 臂**，
不是判据的级别。⇒ 基准齐、边对得上的那几本，徽章就是绿的。
"""
