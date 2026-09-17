# Stage2 局部架构

本文件维护 Stage2 的局部不变量与目录约束。跨 Stage 硬闸见根 `AGENTS.md` 的 I-\* / S-\*；
产品行为与 R 编号见 `docs/BACKEND_CONTRACT.md`；字段形状见 `docs/schema/stage2.md` 与
`contracts/schema/`。不复制根不变量全文。

心智公式：LLM = understand → Resolver = reconcile（仅必要时）→ Compiler = normalize →
Canonical = truth → Projection = expose → Validator = enforce。

## 局部不变量（S2-\*）

| 编号 | 规则 |
|---|---|
| S2-1 | Stage2 的外部业务真相只有已登记的正式 artifact。中间 candidate / semantic payload / recovery state 均不是下游 API。 |
| S2-2 | LLM 只负责语义购买；不得直接写正式 artifact。Characters 与 Global 共享一次 analyze 购买，但拥有独立的 compile / INVALID / edit / recovery 生命周期。 |
| S2-3 | Compiler 只做确定性转换：ID、ref、anchor、closed-set binding、normalization、projection。禁止补猜语义。 |
| S2-4 | 跨单元 reconcile 只在真实需要时存在。当前 Blueprint continuity 为 0-LLM；不得为架构对称制造空 Resolver。 |
| S2-5 | Projection 是 canonical artifact 的只读派生视图，不得反写 truth，也不得形成第二真相源。 |
| S2-6 | 已购买响应的 parse / wire / schema-shape 问题不得触发 paid resample。优先 deterministic recovery / typed repair；无法合法恢复则 INVALID，不得以重新购买掩盖契约或编译器缺陷。validator 本身不得修改 artifact。 |
| S2-7 | Blueprint 付费 chapter semantic extraction 不依赖前章 continuity，因此必须允许并行。章请求仍读取本章 source evidence 与 Characters/Global 的只读 projection，`entry continuity = ∅`（empty state dependency，非 empty context）。跨章 dependency 仅允许存在于 0-LLM continuity；Compile 消费 continuity 结果，是否串行由数据依赖决定。 |
| S2-8 | Stage2 只产出 artifact、projection 与机械证明。是否进入下一节点由 orchestrator 决定。 |

## 目录约束

外层按 owner 切分：

```text
stage2/
  analyze/      # 联合语义购买与双侧 draft custody（绞杀目标）
  characters/   # 人物 owner：compile / edit / checks / projection 发布
  global/       # 知识 owner：compile / edit / renormalize / checks
  blueprint/    # 剧情 owner：章语义 / continuity / compile / checks / recovery
  shared/       # 多 owner 无状态原语
```

- 正式 schema 只在 `contracts/schema/`（及 `docs/schema/`）；stage2 不复制 schema truth。
- `shared/` 准入：被 ≥2 个 Stage2 owner 使用，且自身不拥有业务状态。
  - 可进：evidence 原语、paragraph addressing、ref helpers。
  - 禁进：characters 专属归一、global rebind、blueprint continuity。
- 不为对称铺 `semantic/` / `resolve/` / `canonical/` 空目录树；公式落在各 owner 内部模块。

## Admission 边界

- Stage2 产出机械事实与证明（产物可读、检查通过、projection 可用等）。
- `novel/orchestrator/mechanical_admission.py` 消费这些证明并决定是否启动下一节点。
- producer / compile 不得顺手启动下游节点。

## 现役公开面（批次演进中）

| Owner | 权威 path |
|---|---|
| blueprint | `novel/stages/stage2/blueprint/` |
| analyze | `novel/stages/stage2/analyze/`（`purchase` / `recovery` / `draft_recovery`） |
| shared | `novel/stages/stage2/shared/`（`paragraphs` / `character_refs`） |

过渡期旧模块名可为薄 re-export shim，删除条件见
`docs/REQUIREMENTS.md` adapter 表。下游跨 Stage 应优先消费正式 artifact 与登记投影，
禁止依赖 semantic / recovery 中间态（S2-1）。
