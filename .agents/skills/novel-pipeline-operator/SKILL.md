---
name: novel-pipeline-operator
description: 发现并调用本仓唯一的小说流水线操作 skill；用于跑书、断点续跑、流水线修复、agent 代批和最终成书审阅，真实契约位于 .workflow/skills/novel-pipeline-operator/SKILL.md。
---

# Novel Pipeline Operator · 宿主发现入口

这是当前宿主的仓级发现入口，不是第二份操作规程。

若本次提示内含 `<immutable-workpack>`（你是被 RunSupervisor 唤起的受托 Commander/Reviewer 轮次），
不要加载本 skill 及其任何 reference：材料与裁决契约已随提示下发，只审随包材料并写出唯一 typed 请求。

其他任务开始任何动作前，完整读取仓根
`../../../.workflow/skills/novel-pipeline-operator/SKILL.md`，把它所在目录视为 skill 根，
再严格按其中的“按需加载资源”选择 reference。所有主循环、脚本、模板、授权与完成定义都只以
那一份 canonical skill 为准；不要从本入口补写、猜测或缓存另一份流程。
