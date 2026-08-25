#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""novel.stages.stage4 —— 重构计划 + 写作上下文组装。**全链的收口点。**

【本层与前三层的四处形状差异，都是承重的】

**① 一个 stage 四份产物、三个节点、两节需求。**
`stage4.design`（付费，`R-5`）产盲候选；`stage4.plan`（付费，`R-5`）只绑定证据并
确定性编译 `plan_chapters.json`；`stage4.context`（0 成本，`R-6`）产
`writer_context.json`。判据表只有**一张**，正式计划与上下文共用
（`docs/schema/stage4.md` §四）。
正向覆盖率按 stage 取 `R-5 ∪ R-6`，因此两份产物必须作为同一层共同验证。

**② LLM 在这一层设计场景，不重述事实。**
`reconstruction_strategy` · 逐章安全 brief · 按新叙事顺序排列的 `scenes[]` 与权重由模型产生；
事实真值、因果边、状态增量由 Stage2 冻结，`writer_context` 再按引用确定性组装。

**③ 确定性核心一律吃 `dict`，不吃模型。**
节点走 `store.read(...)` 拿模型（那是载体的活：`PathRefused` + 拒收 + 记上游边），
拿到之后 `model_dump(by_alias=True)` 再交给 `beats` / `measure` / `context` / `checks`。
这样载体边界与纯确定性变换分离，判据也能直接构造反例；正式节点仍只接收通过
Pydantic 当前 schema 的产物。

**④ 本层的徽章【可以】是 `pass`，而 stage6/stage7 结构上永远不可能。**
判据表里**没有一条 `sev='unver'`**：`budget_band` 的基准残缺、`context_link` 的
`unknown`/`missing`、`params_drift` 的参数缺失都是**产出层的 `unver` 臂**，
不是判据的级别。⇒ 基准齐、边对得上的那几本，徽章就是绿的。
"""
