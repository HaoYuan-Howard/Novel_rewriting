# Stage 4 当前契约：场景编译器

Stage4 不是源事件清单的改写器。它读取 Stage2 v5 的章级原子事实契约，产生两份 v2 产物：

| 路径 | 模型 | 生产节点 | 职责 |
|---|---|---|---|
| `stage4/plan_chapters.json` | `PlanChapters` v2 | `stage4.plan` | 冻结同号章语义契约，设计新的场景入口、边界、顺序、转折与篇幅 |
| `stage4/writer_context.json` | `WriterContext` v2 | `stage4.context` | 把 typed 引用确定性解析成人话，成为 Stage5 唯一输入 |

`stage4/plan_edit.json` v2 与正式计划同形，是人工整份替换入口；
`stage4/writer_context.identity.json` v2 与正式上下文同形，是 0 成本显式基线。

旧 v1 的 `PlanBeat`、`end_beat`、`from_beats`、`segments[].events` 不兼容、不迁移，也不得作为
任何新写作消费者的兼容面。存量计划由 `stage4.plan` 重建；writer context 由
`stage4.context` 免费重建；既有 Stage5 章稿因写作任务变化判 stale 后重写。

## 一、`plan_chapters.json`

顶层：

| 字段 | 类型 | 约束 |
|---|---|---|
| `reconstruction_strategy` | `str` | 全书规划依据；不直接进入逐章写手 prompt |
| `output_chapters` | `PlanChapter[]` | 与 Stage1 正文章一一同号对应 |
| `_provenance` | `PlanProvenance` | 输入指纹、模型、prompt、编译参数与修复账 |

### 1.1 `PlanChapter`

| 字段 | 类型 | 产地/语义 |
|---|---|---|
| `output_ordinal` | `int` | 恒等于唯一 `from_source_ordinals[0]` |
| `from_source_ordinals` | `int[1]` | 禁合并、拆分、删章与跨章 |
| `chapter_goal` / `open_hook_goal` / `end_hook_goal` | `PlanGoal` | typed `FactRef`；首尾 goal 必须落在新首尾场景 |
| `weight` | `CHAPTER_WEIGHTS｜null` | 章级篇幅分量档 |
| `semantic_contract` | `SemanticContract` | 代码从同号 Stage2 确定性投影；规划模型不产、不改 |
| `chapter_rewrite_brief` | `str` | Stage4 把全书策略编译成的本章安全简报，只含本章及此前可知信息 |
| `scenes` | `PlanScene[]` | 非空；数组序即 Stage5 落笔顺序 |

`FactRef.kind` 允许 `fact_atom`、character、world_rule、secret、relation、setting 与
chapter_boundary；`ref` 是内部 join，只留在计划审计面，进入 writer context 前解析成人话。

### 1.2 `SemanticContract`

| 字段 | 内容 |
|---|---|
| `chapter_function` | Stage2 冻结的本章语义功能 |
| `information_delta` | 本章新增信息的 fact refs |
| `character_state_delta` | 人物状态变化的 fact refs |
| `relationship_delta` | 关系变化的 fact refs |
| `reader_knowledge_delta` | 读者认知变化的 fact refs |
| `must_land_fact_refs` | obligation=land 的完整 fact_id 集 |
| `context_fact_refs` | obligation=context 的完整 fact_id 集 |
| `causal_edges` | Stage2 因果边，逐条原样保留 |
| `entry_state_refs` / `ending_state_refs` | 章首输入与章尾输出状态 |
| `material_refs` | 本章所有 hinge_id |

它是不可变章级真相，不是 planner 的自由文本。代码在分配 slot/预算时注入，校验器再与 Stage2
逐字段相等比较，防止人工稿或未来重构器绕过 producer。

### 1.3 `PlanScene`

| 字段 | 类型 | 约束 |
|---|---|---|
| `slot` | `sNN` | 代码按场景数组序注入，章内唯一 |
| `budget_chars` | 正整数 | 代码按章/场 weight 与 atom claim 规模分配 |
| `weight` | `UNIT_WEIGHTS` | 场内分量档 |
| `purpose` | `str` | 本场要完成的局面变化，不写风格套话 |
| `role` | `STRUCTURAL_ROLES` | cold_open/setup/escalation/turn/payoff/bridge |
| `delivery` | `scene｜summary` | 展开成场或压缩交代 |
| `transition` | `TEMPORAL_LINKS` | start/continue/earlier/later/parallel/return；首场必须 start |
| `land_fact_refs` | `fact_id[]` | 非空；每个 land atom 全章恰出现一次 |
| `context_fact_refs` | `fact_id[]` | 只引用 context atom，可跨场复用 |
| `material_refs` | `hinge_id[]` | 每个 material 全章恰出现一次 |
| `turn_fact_ref` | `fact_id` | 必须属于本场 land refs，表示场景真正转向的一刻 |
| `entry_state_refs` / `exit_state_refs` | `fact_id[]` | 场间状态链；章首/章尾与 semantic contract 闭合 |
| `participant_refs` | `char_id[]` | 只限本章人物目录 |
| `stage_refs` | `beat_id[]` | 只借 Stage2 beat 的舞台信息，不借它的源序作骨架 |

同一个源 beat 内的 atoms 可以拆进不同场，不相邻 atoms 可以合进同一场。事实引用数组没有写作
先后含义；Stage5 只锁 scene slot 顺序，场内调度由 purpose、turn 与 exit state 决定。

## 二、模型响应与确定性编译

规划响应没有 `semantic_contract`、`slot` 或 `budget_chars`。模型只交策略、typed goals、章/场
weight、`chapter_rewrite_brief` 与 scene refs；响应枚举使用 `G_/CH_/W_/R_/D_/T_` 前缀，
`decode_plan_response` 是唯一映射落点。

`novel/stages/stage4/measure.py::adopt_plan_response` 依次：

1. 解码响应枚举；
2. 验证同章 fact/material/stage/participant 引用与 land 精确覆盖；
3. 由 `semantic_contract_of` 注入 Stage2 契约；
4. 由 `allocate_budgets` 注入连续 slot 和正整数预算；
5. 再交正式 `PlanChapters` strict schema 与整张 Stage4 判据表。

`identity_plan` 也走同一个编译器。它按源舞台生成一组显式基线 scenes，只用于免费预览和反例，
不冒充已经完成重构。

## 三、`writer_context.json`

`WriterContext` 顶层只有 `world_facts`、`chapters` 与免费 `_provenance`；不含全书
`reconstruction_strategy`。每个 `CtxChapter` 含：

- 同号章身份与三个已解析 `CtxGoal`；
- 人话 `CtxSemanticContract`；
- `chapter_rewrite_brief`；
- 章预算、源章字数与 `CtxScene[]`；
- 本章可见人物、叙述、关系、秘密、规则、弧线、称谓与 `must_not_reveal`。

`CtxSemanticContract` 把四类 delta、must-land/context、因果边与首尾状态全部确定性解引用成
人话。四类 delta 不是第二份事实源，只是 Stage2 atom kind 的章级投影。

### 3.1 `CtxScene`

| 字段 | 写手含义 |
|---|---|
| `slot` / `budget_chars` | 输出段身份与本场篇幅 |
| `purpose` / `role` / `delivery` / `transition` | 场景执行要求 |
| `required_facts` | 本场必须成立的人话 land claims；无源序含义 |
| `context_facts` | 已成立的承接信息，不重复宣告 |
| `turn_fact` | 本场转折 |
| `entry_state` / `exit_state` | 场景进入与退出状态 |
| `participants` | 人话人物名 |
| `stages[]` | time_layer/place/present；跨叙述层保持分段，不取并集 |
| `verbatim` / `keep_value` / `must_appear` / `must_hold` | 四类原作材料，各守自己的维度 |

Stage5 提示词按这个场景任务直接渲染，不接收 fact_id、beat_id、hinge_id 或 planner rationale。
`coverage` 不落 writer context，也不进入提示词；覆盖只由代码按 typed refs 复算。

## 四、常驻验收读数

HARD：一章一章同号、semantic contract 精确相等、引用闭合、land/material 精确覆盖、因果边不变、
章首/章尾状态闭合、首尾 goal 落场、slot/transition/预算可执行、writer context 精确投影。

不再以“合并了相邻 beat”“改了 weight”“加了 summary”或字面重合低证明完成重构。常驻 metric：

- 原相邻事实保留率；
- 非相邻事实重新组场比例；
- 场景边界变化；
- 入口是否重建；
- 事实在新场景中的重新分布；
- 章级事实、因果与状态闭合读数。

这些读数当前不设强制重构阈值。顺叙可能就是正确设计，阈值必须先经真实书人工盲读标定。

## 五、现行判据

当前注册表共 **33 条**：**21 hard、2 info、10 metric**。两份 Stage4 产物共用同一张表。

| 分组 | 当前规则 id |
|---|---|
| 章映射与场景闭合 | `one_to_one` · `ordinal_seq` · `empty_chapter` · `semantic_contract` · `fact_ref` · `fact_coverage` · `causal_closure` · `state_closure` · `material_coverage` · `scene_transition` · `goal_landing` |
| 预算与文本策略 | `budget_int` · `budget_band` · `plan_book_band` · `style_words` · `verbatim_starved` |
| writer context | `context_link` · `ctx_facts_exact` · `ctx_name_attested` · `ctx_secret_gate` · `ctx_stage_explicit` · `ctx_hinge_delivered` |
| 因果提示 | `causal_disclosure` |
| 常驻读数 | `scene_budget_distribution` · `verbatim_share` · `adjacency_retention` · `nonadjacent_regroup` · `scene_boundary_change` · `entry_rebuilt` · `fact_redistribution` · `semantic_closure_reading` · `plan_params` · `ctx_source` |

判据权威定义在 `contracts/judges/stage4.py`；报告按注册表编排，度量没有阈值也不冒充放行结论。
