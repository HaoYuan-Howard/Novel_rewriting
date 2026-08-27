# stage4 · 盲设计、证据绑定与场景编译

Stage4 由两个隔离的付费相和一个确定性上下文相组成：

```text
Stage2 v6
  ├─ 去序真相图 ─ stage4.design（A，付费）─ blind_candidate v2
  └─ 原文证据层 ─ stage4.plan（B，付费）── evidence_binding v2
                                                │
                    A + B ─ 确定性编译 ─────────┴─ plan_chapters v4
                                                         │
                                  stage4.context（免费）── writer_context v4
```

A、B 是两个独立节点、两次独立授权；A 落盘后不会自动连跑 B。`stage4.plan --identity`
仍完整构造并校验恒等计划与上下文，但只输出诊断，不产生 artifact。

## 一、产物与版本

| 路径 | 模型 / 版本 | 生产者 | 责任 | 证据 |
|---|---|---|---|---|
| `stage4/blind_candidate.json` | `BlindCandidate` v2 | `stage4.design` | 保存 A 的盲场景候选、编译映射与 A 自己的来源账 | 码·契 |
| `stage4/evidence_binding.json` | `EvidenceBinding` v2 | `stage4.plan` | 保存 B 的证据绑定或 typed `infeasible` | 码·契 |
| `stage4/plan_chapters.json` | `PlanChapters` v4 | `stage4.plan` 的编译段 | 保存 Stage5 真正消费的结构计划与 A/B 完整字节哈希 | 码·契 |
| `stage4/plan_edit.json` | `PlanEdit` v4 | 人工输入 | 人工整份替换计划的 typed 入口 | 码·契 |
| `stage4/writer_context.json` | `WriterContext` v4 | `stage4.context` | 把计划引用和当前 Stage2 真相投影成人话 | 三源 |

旧 Blind/Evidence v1、Plan/Edit v3、WriterContext v3 均关闭式拒读，不迁移、不手补版本号。
compiled 计划从 `stage4.design` → `stage4.plan` 重建；人工稿提交 v4 `PlanEdit`；上下文用
`stage4.context` 免费重建。旧 `stage4/writer_context.identity.json` 已退役：不登记路径、不发现、
不读取，也不主动删除用户盘上的历史文件。

## 二、Stage4A：`blind_candidate.json` v2

### 2.1 A 的输入边界

A 只看同号章身份、冻结 `chapter_function`、事实 claim/kind/obligation 及人物 alias、因果边、
章首与章尾状态。事实和人物引用换成 opaque alias，事实数组独立稳定散列排序。因果边是真相约束，
不是源披露顺序，因此继续保留。

A 看不到原文、SourceSpan、`source_beat_ref`、beat/stage、材料、正式 fact id 或源出现顺序。
alias 映射只供确定性编译和审计，不能拼回 A 的 user message。现役算法为
`hmac-sha256-v1`；算法名、真相图 SHA-256 与 prompt 指纹互不替代。

### 2.2 顶层与章场字段

| 字段 | 类型 / 约束 | 语义 | 证据 |
|---|---|---|---|
| `alias_algorithm` | `hmac-sha256-v1` | opaque alias 算法 | 码·契 |
| `truth_sha256` | 64 位 SHA-256 | A 实际看见的去序真相图主体 | 码·契 |
| `fact_aliases` | `{alias: fact_id}`；非空 | 编译期一一映射，不进入 A prompt | 码·契 |
| `participant_aliases` | `{alias: char_id}` | 人物别名编译映射，不进入 B 回填 | 码·契 |
| `reconstruction_strategy` | 非空字符串 | 仅 A 相使用的全书规划脚手架 | 码·契 |
| `output_chapters` | 非空数组 | 一章对一章的盲候选 | 码·契 |
| `_provenance` | `BlindCandidateProvenance` | A 自己的 LLM 来源账 | 码·契 |

每章保存 `output_ordinal`、`structure_decision`、三个 goal、章级 `weight`、
`chapter_rewrite_brief` 与候选场数组。goal 只含 `intent + fact_aliases`，不保存 rationale。
候选场保存代码注入的 `candidate_scene_id`、场级 `weight`、purpose/role/delivery/transition、
land/context/turn/state aliases。权重只服务预算编译，进入正式计划前删除。

候选场没有 `material_refs`、`stage_refs` 或人物到场清单；A 未看见这些目录，不能用空引用冒充核证。

`BlindStructureDecision` 只保存：

| 字段 | 约束 | 证据 |
|---|---|---|
| `mode` | `retain｜reconstruct` | 码·契 |
| `reason` | 非空人工可审阅理由 | 码·契 |
| `declared_change_kinds` | 契约序去重；retain 为空，reconstruct 至少两项 | 码·契 |

变化类型关闭为 `entry_anchor`、`ending_anchor`、`fact_grouping`、`disclosure_order`、
`noncontiguous_regroup`。A 只声明意图，不能自证实际变化。

## 三、Stage4B：`evidence_binding.json` v2

B 收到冻结候选、候选业务主体 SHA-256，以及原文证据、源舞台和必须材料的关闭式目录。人物到场
由绑定舞台的 `present` 唯一给出，B 不再回填第二份人物清单。B 的输出没有结构字段入口，不能修改
A 的场景顺序、事实分组、goal、结构声明、brief、purpose/role/delivery/transition 或权重。

| 字段 | `bound` | `infeasible` | 证据 |
|---|---|---|---|
| `status` | `bound` | `infeasible` | 码·契 |
| `candidate_sha256` | 必须等于冻结候选主体 | 同左 | 码·契 |
| `chapters` | 非空；章场集精确匹配候选 | `null` | 码·契 |
| `reason_kind` / `reason` | `null` | typed 原因 + 非空说明 | 码·契 |
| `evidence_refs` | `null` | 非空关闭式引用 | 码·契 |
| `_provenance` | B 自己的 LLM 来源账 | 同左 | 码·契 |

`bound.chapters[].scenes[]` 只含 `candidate_scene_id`、`stage_refs`、`material_refs`。每个候选场恰绑定
一次，每条材料全章恰落一次；返回数组顺序不产生叙事语义，编译始终沿 A 顺序。

`infeasible` 是正式、可审计的终态，不允许部分 binding，也不自动生成 A2。其引用只能来自本次下发的
候选场、opaque alias、stage 或 material 目录；自由 token 和悬空引用在落盘前拒绝。

## 四、正式计划 v4

### 4.1 编译与顶层

`PlanChapters` 顶层只保存 `output_chapters` 和 `_provenance`。编译器依次：验证 B 回显 SHA；沿 A
顺序合入 B 的舞台与材料；把 alias 解成正式 id；验证同章引用及 land/material 覆盖；按 A 权重分配
连续 slot 和正整数预算；删除所有权重与 A 策略；最后过 strict schema 和 Stage4 判据。

当前 Stage2 `semantic_contract` 不复制进计划。需要它的 Context 与 API view 每次从同号当前 blueprint
确定性派生，防止人工计划携带一份过期真相。

### 4.2 `PlanChapter` 与 `PlanScene`

| `PlanChapter` 字段 | 约束 / 语义 | 证据 |
|---|---|---|
| `output_ordinal` | 正整数，等于唯一来源章 | 码·契 |
| `from_source_ordinals` | 长度恒 1；本批保留供跨层审计 | 码·契 |
| 三个 `*_goal` | `intent + FactRef[]`；FactRef 只含 kind/ref | 码·契 |
| `structure_decision` | 只含 mode/reason/declared vector | 码·契 |
| `chapter_rewrite_brief` | A 的本章可执行简报 | 码·契 |
| `scenes` | 新披露顺序，非空 | 码·契 |

| `PlanScene` 字段 | 约束 / 语义 | 证据 |
|---|---|---|
| `slot` / `budget_chars` | 连续场号与正整数预算 | 码·契 |
| `purpose` / `role` / `delivery` / `transition` | A 的结构执行要求 | 码·契 |
| `land_fact_refs` / `context_fact_refs` | 同章关闭式事实引用 | 码·契 |
| `turn_fact_ref` | 属于本场 land refs | 码·契 |
| `entry_state_refs` / `exit_state_refs` | 场间与章边界状态承接 | 码·契 |
| `material_refs` | B 回填；材料全章精确覆盖 | 码·契 |
| `stage_refs` | B 回填、非空；只借舞台证据 | 码·契 |

正式计划不保存章/场 `weight`、`reconstruction_strategy`、`semantic_contract`、FactRef 章号、
人物到场引用或 `computed_change_kinds`。

### 4.3 结构向量与审批主体

`StructureDecision` 只保存 `mode`、`reason`、`declared_change_kinds`。实际结构向量每次由
`novel/stages/stage4/measure.py::structure_metrics_of` 对当前 blueprint 和实际计划场景计算；
`structure_alignment` 展示声明、实际、未实现声明与未声明实际，不回写计划。

结构审批主体绑定同号章、`from_source_ordinals`、三个 goal、brief、预算、全部场景执行字段和人工结构
声明；不包含来源账、已删除字段或现场派生向量。改变实际计划会改变 subject SHA-256；只改变算法读数
不会改写计划字节。审批 proof 另外绑定 `plan_chapters.json` 完整字节，两层缺一不可。

### 4.4 `PlanProvenance`

| 字段 | 约束 | 证据 |
|---|---|---|
| `origin` | `compiled｜human` | 码·契 |
| `blind_candidate_sha256` | compiled 必填，human 为 `null` | 码·契 |
| `evidence_binding_sha256` | compiled 必填，human 为 `null` | 码·契 |
| `inputs` | 普通 Stage1/2 输入边；compiled 另含 A/B 输入边 | 码·契 |

计划 provenance 不复制 A/B 的 model/prompts/usage，也不保存编译参数。A/B 完整文件 SHA-256 是正式计划
可消费当前中间产物的精确凭据。人工 v4 edit 产生 `origin=human`，不得继续冒充旧 A/B 编译物。

## 五、`writer_context.json` v4

顶层只含 `chapters` 与免费 `_provenance`。每章保留同号身份、三个已解析 CtxGoal、由当前 blueprint 派生
的 `semantic_contract`、brief、章预算、章级源字数、场景、叙述、章级人物数组及安全投影后的事实。

| 字段 | 写手含义 | 证据 |
|---|---|---|
| `characters[]` | `{char_id, display_name, role}`；消费者按数组遍历 | 码·契 |
| `CtxScene.slot / budget_chars` | 输出段身份与篇幅 | 三源 |
| `purpose / role / delivery / transition` | 冻结场景执行要求 | 码·契 |
| `required_facts / context_facts / turn_fact` | typed refs 的人话解析 | 三源 |
| `entry_state / exit_state` | 场景进入与退出状态 | 码·契 |
| `stages[]` | `time_layer/place/present`；人物到场只从这里读取 | 三源 |
| `verbatim / keep_value / must_appear / must_hold` | 四类保真材料 | 三源 |

Context 不保存顶层 `world_facts`、逐场源字数、逐场 participants、secret/rule 内部 id 或章级
`must_not_reveal`。Stage5 的全书名称检测基线由当前 `GlobalGrounding + MainText` 经共享名称投影重建；
秘密边界只读 Stage2 权威时间线。Stage4 和 Stage5 共用 `novel/core/naming.py`，避免 `display_name`、
名称区分度和残留名检查口径漂移。

Stage5 prompt 不接收 fact/beat/hinge/secret/rule id、alias、A 策略、结构 metric、逐场源字数或源正文。

## 六、结构度量与人工门

`partition_change`、`disclosure_order`、`noncontiguous_regroup`、`anchor_migration`、
`structure_eligibility`、`structure_alignment` 与 `semantic_closure_reading` 都是现场读数，不设自动美学
阈值。`causal_closure` 直接依据 Stage2 因果边检查事实是否落入实际场景——`land` 与 `context` 两种引用
都算入场，端点只作本章上下文出现不是漏落；事实、状态、材料和上下文
精确性仍由 HARD 守卫关闭。

公开 `ApprovalDoc` 是 v4；服务盘上 `_service/approvals/<sid>.json` 是 v5。旧 v4 只经显式
`backfill_from_disk` 逐项对证，普通 GET / 启动不静默迁移，迁移结果也不保留退役 s3。一次结构决定必须提交当前
subject SHA-256、actor、全书理由及与计划章集精确相等的逐章 accept/request_replan。全章 accept 才
打开 s4；任何 request_replan 都关闭 s4、保留 typed 决定并撤销旧 writing/下游证明，但不自动启动
付费 A/B。计划变化后旧决定进入 history，当前态变 expired。

## 七、失败与恢复

| 状态 | 允许的恢复 | 禁止的捷径 | 证据 |
|---|---|---|---|
| A 响应暂停或拒收 | 显式 response-repair，或重新批准 A | 补字段冒充正式候选 | 码·契 |
| B typed `infeasible` | 人审阅；必要时另行批准 `stage4.design --replan`，再独立批准 B；或提交完整 v4 人工计划 | B 私改 A、自动生成 A2、一次批准串跑 A/B | 码·契 |
| 计划落 `.INVALID` | 修订 v4 `plan_edit.json` 后 `--apply-edit`，或 `--revalidate --actor --reason` | 改名提升、绕过 schema/判据/归档 | 三源 |
| Context 缺失或 stale | `stage4.context` 免费重建 | 用旧上下文继续写 | 三源 |
| 旧版本 | 从 producer 重建；人工稿重写为 v4 | 迁移、猜字段、手补版本号 | 码·契 |

## 八、现行判据

当前注册表共 **32 条**：**20 hard、2 info、10 metric**。

| 分组 | 当前规则 id |
|---|---|
| 章场与事实安全 | `one_to_one` · `ordinal_seq` · `empty_chapter` · `fact_ref` · `fact_coverage` · `causal_closure` · `state_closure` · `material_coverage` · `scene_transition` · `goal_landing` |
| 预算与文本策略 | `budget_int` · `budget_band` · `plan_book_band` · `style_words` · `verbatim_starved` |
| A/B 与 Context | `phase_artifact_link` · `context_link` · `ctx_facts_exact` · `ctx_name_attested` · `ctx_stage_explicit` · `ctx_hinge_delivered` |
| 因果提示 | `causal_disclosure` |
| 常驻读数 | `scene_budget_distribution` · `verbatim_share` · `partition_change` · `disclosure_order` · `noncontiguous_regroup` · `anchor_migration` · `structure_eligibility` · `structure_alignment` · `semantic_closure_reading` · `ctx_source` |

判据权威定义在 `contracts/judges/stage4.py`。metric 不产生自动结论，API、前端与审批服务不得再造第二个判官。
