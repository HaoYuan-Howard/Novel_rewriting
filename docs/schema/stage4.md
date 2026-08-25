# stage4 · 盲设计、证据绑定与场景编译

Stage4 的正式路径不是“让一个模型看着原文改顺序”，而是两个互相隔离的付费相，后接一次
确定性编译：

```text
冻结的 Stage2 v5
  ├─ 去序真相图 ── stage4.design（A，付费）── blind_candidate v1
  └─ 原文证据层 ── stage4.plan（B，付费） ─── evidence_binding v1
                                                 │
                         A + B ── 代码编译 ───────┴─ plan_chapters v3
                                                          │
                                      stage4.context（免费）─ writer_context v3
```

两个付费相是两个独立节点、两个独立逻辑调用，分别进入付费预览与 `--yes`；A 落盘不自动连跑 B。
供应商传输失败或内容拒收仍可能触发重采，实际 wire 与 token 以 ledger 为准，不能把“两个逻辑调用”
解释为“账本永远只有两行”。

## 一、产物与版本

| 路径 | 模型 / 版本 | 生产者 | 成本 | 责任 | 证据 |
|---|---|---|---|---|---|
| `stage4/blind_candidate.json` | `BlindCandidate` v1 | `stage4.design` | 付费 | 保存 A 在看不到源叙述顺序时设计的场景候选及 A 自己的来源账 | 码·契 |
| `stage4/evidence_binding.json` | `EvidenceBinding` v1 | `stage4.plan` | 付费 | 保存 B 对冻结候选的完整证据绑定，或 typed `infeasible` 终态 | 码·契 |
| `stage4/plan_chapters.json` | `PlanChapters` v3 | `stage4.plan` 的确定性编译段 | 0 次额外调用 | 合并 A 的结构决策与 B 的证据引用，注入不可变语义契约、slot、预算和结构复算向量 | 码·契 |
| `stage4/plan_edit.json` | `PlanEdit` v3 | 人工输入 | 0 LLM | 人工整份替换计划的 typed 入口 | 码·契 |
| `stage4/writer_context.json` | `WriterContext` v3 | `stage4.context` | 0 LLM | 把计划引用确定性解析成人话，成为 Stage5 唯一写作输入 | 三源 |
| `stage4/writer_context.identity.json` | `WriterContext` v3 | `stage4.plan --identity` | 0 LLM | 显式恒等基线，不冒充重构候选 | 码·契 |

旧 `plan_chapters.json` / `plan_edit.json` v2 没有章级结构决定，也不能证明来自隔离的 A/B 两相，
因此关闭式拒读、不迁移、不手补字段。正式计划从 `stage4.design` → `stage4.plan` 重建；人工稿按
v3 重写；writer context 再由 `stage4.context` 免费重建。旧 v1 `PlanBeat`、`end_beat`、
`from_beats`、`segments[].events` 同样只留历史字节，不进入任何现役兼容面。

## 二、Stage4A：去序真相图与 `blind_candidate.json`

### 2.1 A 能看见什么

A 的 prompt 只含同号章身份、冻结的 `chapter_function`、事实 claim/kind/obligation 及其人物 alias、
因果边、章首与章尾状态；事实和人物引用换成无源序含义的 opaque alias，事实数组由独立稳定散列排序。因果边仍保留，
因为它是真相约束，不是原文披露顺序。

A 明确看不到：原文正文、SourceSpan、`source_beat_ref`、beat/stage、材料、原始 fact id、按原文
出现次序排列的事实数组。alias 映射可以保存在中间产物供确定性编译与审计，但不得重新拼回 A 的
user message。现役别名算法登记为 `hmac-sha256-v1`；算法名、真相图 SHA-256 与 A prompt 指纹是
三件不同的身份。

### 2.2 `BlindCandidate` 顶层

| 字段 | 类型 / 必填 | 语义 | 证据 |
|---|---|---|---|
| `alias_algorithm` | `hmac-sha256-v1`；是 | 本候选使用的 opaque alias 算法 | 码·契 |
| `truth_sha256` | 64 位 SHA-256；是 | A 实际看见的去序真相图主体 | 码·契 |
| `fact_aliases` | `{alias: fact_id}`；非空 | 编译期 alias→正式事实引用的一一映射；不进入 A prompt | 码·契 |
| `participant_aliases` | `{alias: char_id}` | 编译期人物别名映射；不得借 alias 字面携带源序 | 码·契 |
| `reconstruction_strategy` | 非空字符串 | 只基于真相图作出的全书场景组织意图 | 码·契 |
| `output_chapters` | `BlindCandidateChapter[]`；非空 | 一章对一章的盲设计候选 | 码·契 |
| `_provenance` | `BlindCandidateProvenance`；是 | A 自己的 LLM 来源账，另带 `alias_algorithm` 与 `truth_sha256` | 码·契 |

### 2.3 章、goal 与候选场景

| 字段 | 类型 / 必填 | 语义 | 证据 |
|---|---|---|---|
| `output_ordinal` | 正整数；是 | 阅读序章号；不得跨章取材 | 码·契 |
| `structure_decision` | `BlindStructureDecision`；是 | A 选择 `retain` 或 `reconstruct` 及声明的结构变化 | 码·契 |
| `chapter_goal` / `open_hook_goal` / `end_hook_goal` | `BlindGoalResponse`；是 | intent + 本章 fact aliases；首尾 goal 对应候选首尾场 | 码·契 |
| `weight` | 响应侧章级档位；是 | 章级篇幅分量，整数由编译器算 | 码·契 |
| `chapter_rewrite_brief` | 非空字符串；是 | 入口、事实组合、披露与收束的可执行简报 | 码·契 |
| `scenes` | `BlindCandidateScene[]`；非空 | 数组顺序就是 A 设计的新披露顺序 | 码·契 |
| `candidate_scene_id` | 关闭式 `c…s…`；是 | 代码按 A 的章/场顺序注入，供 B 定位；模型不自报 | 码·契 |
| `purpose` / `role` / `delivery` / `transition` | 关闭式结构字段；是 | 只描述场景承载，不指定文风；首场 transition 为 start | 码·契 |
| `weight` | 响应侧场级档位；是 | 场内分量，整数由编译器算 | 码·契 |
| `land_fact_aliases` / `context_fact_aliases` | alias 数组 | land 恰落一次；context 可复用 | 码·契 |
| `turn_fact_alias` | 本场 land alias；是 | 本场真正改变局面的事实 | 码·契 |
| `entry_state_aliases` / `exit_state_aliases` | alias 数组 | 候选场间状态承接 | 码·契 |

候选场没有 `material_refs`、`participant_refs` 或 `stage_refs`。A 既没有这些目录，也不能用一组貌似
完整的空引用冒充“已经核过证据”。

### 2.4 `BlindStructureDecision`

| 字段 | 约束 | 证据 |
|---|---|---|
| `mode` | `retain｜reconstruct` | 码·契 |
| `reason` | 非空、可供人工审阅的本章选择理由 | 码·契 |
| `declared_change_kinds` | 去重并按契约顺序；retain 必为空，reconstruct 至少两项 | 码·契 |

变化类型只有 `entry_anchor`、`ending_anchor`、`fact_grouping`、`disclosure_order`、
`noncontiguous_regroup`。A 只声明意图；实际变化由最终计划复算，不能由 A 自证。

## 三、Stage4B：`evidence_binding.json`

B 收到冻结候选、候选完整 SHA-256，以及原文证据、源舞台、本章人物与必须材料的关闭式目录。
B 的输出模型没有结构字段入口，因此不能改写候选场景数组、事实分组、goal、锚点、structure decision、
purpose、role、delivery、transition 或 weight。

### 3.1 判别联合

| 字段 | `bound` | `infeasible` | 证据 |
|---|---|---|---|
| `status` | `bound` | `infeasible` | 码·契 |
| `candidate_sha256` | 必须等于当前冻结候选 | 同左 | 码·契 |
| `chapters` | 非空；章集与候选相等 | 必须为 `null` | 码·契 |
| `reason_kind` | 必须为 `null` | `causal_conflict｜stage_conflict｜material_conflict｜future_leak｜insufficient_evidence` | 码·契 |
| `reason` | 必须为 `null` | 非空具体理由 | 码·契 |
| `evidence_refs` | 必须为 `null` | 非空证据引用 | 码·契 |
| `_provenance` | B 自己的 `LlmProvenance`，另带 `candidate_sha256` | 同左 | 码·契 |

`bound.chapters[].scenes[]` 逐场只含 `candidate_scene_id`、`material_refs`、`participant_refs`、
`stage_refs`。每个候选场恰好绑定一次，引用只来自同章目录，每条必须材料全章恰落一次；B 返回的数组
顺序不产生叙事语义，合并始终沿 A 的候选顺序进行。`infeasible` 是正式、可审计的终态，不允许夹带
一份“先绑一半”的候选，也不会自动再花钱生成 A2。其 `evidence_refs` 同样是关闭式引用：只接受
本次候选的 `candidate_scene_id` / opaque alias，或 B prompt 中同章 stage、material、participant
目录里的正式 ref；任意自由 token 或悬空 ref 都在落盘前拒绝。

## 四、`plan_chapters.json` v3

### 4.1 顶层与确定性编译

| 字段 | 类型 / 必填 | 产地 / 语义 | 证据 |
|---|---|---|---|
| `reconstruction_strategy` | 非空字符串；是 | 原样继承 A | 码·契 |
| `output_chapters` | `PlanChapter[]`；非空 | A 的结构 + B 的引用 + 代码注入字段 | 码·契 |
| `_provenance` | `PlanProvenance`；是 | 免费编译账，不是第三次 LLM 来源账 | 码·契 |

编译器按以下顺序关闭：验证 B 回显的候选 SHA；沿 A 顺序合入 B 的三类引用；把 alias 解成正式 id；
注入同号 Stage2 `semantic_contract`；验证本章关闭式引用与 land/material 精确覆盖；按 weight 分配连续
slot 与正整数预算；从最终事实→场景映射重算 `computed_change_kinds`；最后经过 strict
`PlanChapters` 与整张 Stage4 判据表。任何一步失败都不得把部分结果晋升为正式计划。

### 4.2 `PlanChapter`

| 字段 | 类型 / 必填 | 产地 / 语义 | 证据 |
|---|---|---|---|
| `output_ordinal` | 正整数；是 | 恒等于唯一 `from_source_ordinals[0]` | 码·契 |
| `from_source_ordinals` | `int[1]`；是 | 同号一章一章；禁合并、拆分、删章、跨章 | 码·契 |
| 三个 `*_goal` | `PlanGoal`；是 | A 的 alias goal 确定性解成 typed `FactRef` | 码·契 |
| `structure_decision` | `StructureDecision`；是 | A 的 mode/reason/声明 + 编译器复算向量 | 码·契 |
| `weight` | 章级档位或 `null` | 章级篇幅分量 | 码·契 |
| `semantic_contract` | `SemanticContract`；是 | 从同号 Stage2 注入；A/B 都不能产或改 | 码·契 |
| `chapter_rewrite_brief` | 非空字符串；是 | 原样继承 A 的本章安全简报 | 码·契 |
| `scenes` | `PlanScene[]`；非空 | 数组顺序继承 A，Stage5 按此落笔 | 码·契 |

`SemanticContract` 包含冻结的 `chapter_function`、四类 delta、must-land/context facts、因果边、章首/章尾
状态与全部材料引用。它是不可变真相，不是 planner 的自由文本；校验器再与 Stage2 逐字段相等比较，
防止人工稿或未来编译器绕过 producer。

### 4.3 `StructureDecision`

| 字段 | 约束 | 证据 |
|---|---|---|
| `mode` | `retain｜reconstruct` | 码·契 |
| `reason` | 非空 | 码·契 |
| `declared_change_kinds` | 继承 A；retain 为空，reconstruct 至少两项 | 码·契 |
| `computed_change_kinds` | 代码按最终计划复算，去重并按关闭式契约顺序 | 码·契 |

`structure_vector` HARD 只守“盘上复算向量没有漂移”；`structure_alignment` 常驻列出声明、实际、
未实现声明与未声明实际。reconstruct 的声明至少两项不等于机器有资格判“实际至少两项才好”，
retain 也不等于机器替人证明顺叙最好。结构取舍最终交给独立人工审批。

### 4.4 `PlanScene`

| 字段 | 类型 / 约束 | 证据 |
|---|---|---|
| `slot` | `sNN`；代码按 A 顺序注入、章内唯一 | 码·契 |
| `budget_chars` | 正整数；代码分配 | 码·契 |
| `weight` | `UNIT_WEIGHTS` | 码·契 |
| `purpose` / `role` / `delivery` / `transition` | A 的结构决定；首场必须 start | 码·契 |
| `land_fact_refs` | 非空；每个 land atom 全章恰出现一次 | 码·契 |
| `context_fact_refs` | 只引用 context atom，可跨场复用 | 码·契 |
| `material_refs` | B 绑定；每个 material 全章恰出现一次 | 码·契 |
| `turn_fact_ref` | 必须属于本场 land refs | 码·契 |
| `entry_state_refs` / `exit_state_refs` | 场间承接，并与章首/章尾状态闭合 | 码·契 |
| `participant_refs` | B 绑定；只限本章人物目录 | 码·契 |
| `stage_refs` | B 绑定、非空；只借舞台证据，不借源序作写作骨架 | 码·契 |

事实引用数组没有场内写作顺序含义；Stage5 只锁 scene slot 顺序，场内措辞与调度仍由 purpose、turn
和 exit state 驱动。

### 4.5 `PlanProvenance`

最终计划的来源账继承 `FreeProvenance`，不会复制 A 或 B 的 `model/prompts/usage`：

| 字段 | 约束 | 证据 |
|---|---|---|
| `origin` | `compiled｜human` | 码·契 |
| `blind_candidate_sha256` | compiled 必填，human 必须为 `null` | 码·契 |
| `evidence_binding_sha256` | compiled 必填，human 必须为 `null` | 码·契 |
| `params.compiler` | `atomic-scenes-v2` | 码·契 |
| `params.structure_metric_contract` | `atomic-structure-v2` | 码·契 |
| `params.fact_atoms` / `source_stages` | 正数；实际编译输入规模 | 码·契 |
| `inputs` | A、B 与编译器实际读取的 Stage1/2 consumed edges | 码·契 |

A、B 各自的模型、prompt、attempt、usage 只在自己的 v1 `_provenance` 与 ledger 出现。人交
`plan_edit.json` v3 后产生 `origin=human` 的免费来源账；继续携带旧 A/B hash 会被 schema 拒绝。

## 五、结构度量契约 `atomic-structure-v2`

所有逐章结构读数由 `novel/stages/stage4/measure.py::structure_metrics_of` 唯一复算。前件先检查
land fact 的源集合与计划落点是否完整、一一对应且非空；前件不闭合时，所有结构小节落 `unver`，
不得在残缺映射上算比例。

| 报告规则 | 分子 / 分母与解释 | 自动结论 | 证据 |
|---|---|---|---|
| `partition_change` | 全部 land-fact pair；分别报源同组保留、拆分、源异组合并、继续分离与 pairwise distance | 无 | 码·契 |
| `disclosure_order` | 只比较 SourceSpan 严格可排序的 fact pair；分开报 concordant、inverted、同场 ties、不可比 pair、cross-scene inversion 与 Kendall tau-b | 无 | 码·契 |
| `noncontiguous_regroup` | 分母是至少覆盖两个 source locus 的新场；按 source run 是否多于一段判断真正非连续组场并给 witness | 无 | 码·契 |
| `anchor_migration` | 开场与结尾各报 typed goal 的源 locus offset，以及首/末场 payload 是否排除源首/尾 locus | 无 | 码·契 |
| `structure_eligibility` | 报 source locus/源分组数、跨组可比较事实对、因果约束/不可比对、可重排与非连续机会 | 无 | 码·契 |
| `structure_alignment` | 对照 mode、声明向量、复算向量、未实现声明与未声明实际 | 只有向量被篡改由 `structure_vector` HARD 拦；取舍交人 | 码·契 |
| `semantic_closure_reading` | fact / causal / state / material 的 HARD 命中数 | 闭合由各自 HARD 守 | 码·契 |

分区、顺序、非连续、锚点与 eligible 的读数都不设自动重构阈值。顺叙可能正确，入口迁移也可能机械；
在真实书人工盲评标定前，系统只能准确展示差异，不能把距离翻译成“好结构”。

## 六、独立结构审批

结构批准不是普通 s4 盖章。公开 `ApprovalDoc` v3 暴露结构态
`unavailable / missing / accepted / replan_requested / expired`；服务盘上
`_service/approvals/<sid>.json` 为 `_ApprovalRecordV4`。旧审批记录只能走既有显式 backfill 规则，
普通 GET 不静默改版本。

服务端从当前 v3 计划计算 canonical 结构主体：全书策略、同号章、结构决定、三个 goal 的 intent/refs、
weight、brief 与全部场景执行字段；来源账与 goal rationale 不混进审美主体。`subject_sha256` 绑定这份
完整结构决策面，审批 proof 另外绑定当前 `plan_chapters.json` 完整字节，二者缺一不可。

一次 `StructureApprovalRequest` 必须提交：

- 当前完整 `subject_sha256`；
- 非空 actor 与全书理由；
- 与当前计划章集升序、去重、精确相等的逐章决定；
- 每章 `accept` 或 `request_replan` 及非空理由。

全章 accept 才打开 s4；任何 request_replan 都保留 typed 决定、关闭 s4、撤销旧 writing/下游证明，
但绝不自动启动 `stage4.design` 或 `stage4.plan`。计划改变后旧决定保留在 history，同时当前态变
expired。通用 `/api/approvals/gate/s4` 关闭式拒绝，只有 `/api/approvals/structure` 能生成这份证明。
浏览器可以在该决定落盘后另行展示 `stage4.design --replan` 的付费预览入口；提交审批请求本身仍是
零作业、零 LLM，只有人再次查看并确认预览后才允许启动重规划 A，A 完成后 B 还要再次独立确认。

Stage5 付费写作在首个 LLM 调用前必须同时持有当前 s4 accepted 与独立 writing 授权。免费 check、
revalidate、apply-edit、阅读与审批材料仍可进入，否则人会被闸在修复证据之外。

## 七、`writer_context.json` v3

`WriterContext` 顶层只有 `world_facts`、`chapters` 与免费 `_provenance`；不含全书
`reconstruction_strategy`。每章含同号身份、三个已解析 `CtxGoal`、人话
`CtxSemanticContract`、brief、章预算、源章字数、`CtxScene[]`，以及本章可见人物、叙述、关系、
秘密、规则、弧线、称谓与 `must_not_reveal`。

| `CtxScene` 字段 | 写手含义 | 证据 |
|---|---|---|
| `slot` / `budget_chars` | 输出段身份与本场篇幅 | 三源 |
| `purpose` / `role` / `delivery` / `transition` | A 决定、最终计划冻结的场景执行要求 | 码·契 |
| `required_facts` / `context_facts` / `turn_fact` | typed fact refs 的确定性人话解析；无源序含义 | 三源 |
| `entry_state` / `exit_state` | 场景进入与退出状态 | 码·契 |
| `participants` / `stages[]` | B 引用的人话解析；跨叙述层保持分段，不取并集 | 三源 |
| `verbatim` / `keep_value` / `must_appear` / `must_hold` | 四类原作材料，各守自己的维度 | 三源 |

Stage5 提示词不接收 fact_id、beat_id、hinge_id、alias、planner rationale、结构 metric 或源章正文。
coverage 不落 writer context，也不进入提示词；它始终由代码从 typed refs 复算。

## 八、失败与恢复

| 状态 | 允许的恢复 | 禁止的捷径 | 证据 |
|---|---|---|---|
| A 响应在协议/内容闸前暂停 | 按 `stage4.design --response-repair` 的显式恢复协议继续；或重新明确批准付费 A | 把不完整响应补字段后冒充正式候选 | 码·契 |
| B 返回 `infeasible` | 人阅读 typed 理由；如需新候选，另行预览并批准付费 `stage4.design --replan`（refresh-write，只跑 A），A 落盘后再独立预览并批准 B；也可提交完整 v3 人工计划 | B 私自改 A；自动花钱生成 A2；一次批准串跑 A/B；用部分 binding 编译 | 码·契 |
| 最终计划落 `.INVALID` | 人改完整 `plan_edit.json` v3 后 `--apply-edit`，或修复问题稿后 `--revalidate --actor --reason`；两者均 0 LLM | 直接改名提升、绕过 schema/判据/归档/provenance | 三源 |
| 人工结构决定含 `request_replan` | 保留决定与证据；另行预览并批准付费 `stage4.design --replan`，新 A 完成后再独立预览 B；也可走完整人工计划入口 | 审批动作顺手启动付费节点；沿用旧候选冒充已重规划 | 码·契 |
| writer context 缺失或 stale | `stage4.context` 免费重建 | 继续用照着旧计划组装的上下文写作 | 三源 |
| 旧 plan/edit v2 | 从 producer 重建；人工稿按 v3 重写 | 迁移、猜测 structure decision、手补 provenance | 码·契 |

## 九、现行判据

当前注册表共 **36 条**：**23 hard、2 info、11 metric**。计划与 writer context 共用同一张表。

| 分组 | 当前规则 id |
|---|---|
| 章映射、语义与场景闭合 | `one_to_one` · `ordinal_seq` · `empty_chapter` · `semantic_contract` · `fact_ref` · `fact_coverage` · `causal_closure` · `state_closure` · `material_coverage` · `scene_transition` · `goal_landing` · `structure_vector` |
| 预算与文本策略 | `budget_int` · `budget_band` · `plan_book_band` · `style_words` · `verbatim_starved` |
| A/B 编译身份 | `phase_artifact_link` |
| writer context | `context_link` · `ctx_facts_exact` · `ctx_name_attested` · `ctx_secret_gate` · `ctx_stage_explicit` · `ctx_hinge_delivered` |
| 因果提示 | `causal_disclosure` |
| 常驻读数 | `scene_budget_distribution` · `verbatim_share` · `partition_change` · `disclosure_order` · `noncontiguous_regroup` · `anchor_migration` · `structure_eligibility` · `structure_alignment` · `semantic_closure_reading` · `plan_params` · `ctx_source` |

判据权威定义在 `contracts/judges/stage4.py`；报告按注册表编排。metric 没有阈值，不得在 API、前端或
人工审批服务里再造第二个判官。
