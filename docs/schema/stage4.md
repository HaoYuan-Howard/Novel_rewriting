# stage4 · 盲设计、证据绑定与场景编译

Stage4 由两个隔离的付费相和一个确定性上下文相组成：

```text
Stage2 v6
  ├─ 章功能/计数 ─ stage4.design（A，付费）─ 整书策略 checkpoint
  │                                      └─ 冻结策略 + 单章去序真相 ─ N 个私有 session（≤4 并发）
  │                                                                      └─ 单章 checkpoints
  │                              策略 + 全部章节 ─ 确定性聚合 + 整书 HARD ─ blind_candidate v2（原子落盘）
  │                                                                        │
  │                                                            A 正式设计审阅（服务账）
  └─ 原文证据层 + 已审阅候选 ─ stage4.plan（B，独立授权）─ 机械基线（唯一解 0 wire）
                                                              └─ 真歧义章私有 session（≤4 并发）
                                                                           │
                                      A + 逐章 B ─ 确定性聚合/编译 ────────┴─ plan_chapters v4
                                                                                    │
                                                               最终逐章结构审批（s4）
                                                                                    │
                                                             stage4.context（免费）── writer_context v4
```

A、B 是两个独立公开节点、两次独立授权；A 的一次授权覆盖预览中明示的 1+N 个内部逻辑请求，
不是 N+1 次公开授权。A 落盘后先过正式设计审阅，不会自动连跑 B。B 编译 Plan 后再过另一份最终
逐章结构审批；B 的公开授权也不等于一次整书 wire：代码先解机械唯一项，只有真歧义章各自发 wire。
Context 只能在该决定全书接受后独立生成。`stage4.plan --identity` 仍完整构造并校验
恒等计划与上下文，但只输出诊断，不产生 artifact 或审批。

## 一、产物与版本

| 路径 | 模型 / 版本 | 生产者 | 责任 | 证据 |
|---|---|---|---|---|
| `stage4/blind_candidate.json` | `BlindCandidate` v2 | `stage4.design` | 保存 A 的 N+1 个已验 unit 确定性聚合后的盲场景候选、编译映射与聚合来源账 | 码·契 |
| `stage4/evidence_binding.json` | `EvidenceBinding` v3 | `stage4.plan` / `--apply-binding` | 保存 B 的完整绑定、非阻断 concerns，或仅由代码铸造 witness 的 typed `infeasible` | 码·契 |
| `stage4/plan_chapters.json` | `PlanChapters` v4 | `stage4.plan` 的编译段 | 保存 Stage5 真正消费的结构计划与 A/B 完整字节哈希 | 码·契 |
| `stage4/plan_edit.json` | `PlanEdit` v4 | 人工输入 | 人工整份替换计划的 typed 入口 | 码·契 |
| `stage4/writer_context.json` | `WriterContext` v4 | `stage4.context` | 把计划引用和当前 Stage2 真相投影成人话 | 三源 |

旧 Blind v1、Evidence v1/v2、Plan/Edit v3、WriterContext v3 均关闭式拒读，不迁移、不手补版本号。
compiled 计划从 `stage4.design` → `stage4.plan` 重建；人工稿提交 v4 `PlanEdit`；上下文用
`stage4.context` 在最终结构审批后免费重建。旧 `stage4/writer_context.identity.json` 已退役：不登记路径、不发现、
不读取，也不主动删除用户盘上的历史文件。

## 二、Stage4A：`blind_candidate.json` v2

### 2.1 A 的输入边界

Stage4A 不是一份全书事实请求。首个整书策略 session 只看按阅读序排列的章身份、冻结
`chapter_function` 与代码计算的事实/因果/状态计数；它看不到 claim、事实或人物 alias，也不设计
逐章场景，只返回 `reconstruction_strategy + chapter_weights[]`。

策略通过后，每章各开一个独立私有 session。单章请求只看冻结策略与本章权重、本章事实
claim/kind/obligation、因果边与章首/章尾状态；不会看见其他章真相。
事实与人物引用使用该章独有的短 local alias（`f_*` / `p_*`）：由 root 身份、章号和正式 alias
确定性 HMAC 派生，碰撞时只延长摘要，因此跨恢复稳定且不携带源序；数组继续按 alias 稳定排序。
因果边是真相约束，不是源披露顺序，因此在本章请求中保留。

两类 session 都看不到原文、SourceSpan、`source_beat_ref`、beat/stage、材料、正式 fact id 或源出现
顺序。local→正式 alias 映射只供确定性编译和审计，不能拼回 A 的 user message。正式算法仍为
`hmac-sha256-v1`；算法名、真相图 SHA-256、各 unit 输入 SHA 与 prompt 指纹互不替代。

### 2.2 顶层与章场字段

| 字段 | 类型 / 约束 | 语义 | 证据 |
|---|---|---|---|
| `alias_algorithm` | `hmac-sha256-v1` | opaque alias 算法 | 码·契 |
| `truth_sha256` | 64 位 SHA-256 | 完整去序真相图主体；各 unit 绑定它，但没有 session 看见整份图 | 码·契 |
| `fact_aliases` | `{alias: fact_id}`；非空 | 编译期一一映射，不进入 A prompt | 码·契 |
| `participant_aliases` | `{alias: char_id}` | 人物别名编译映射，不进入 B 回填 | 码·契 |
| `reconstruction_strategy` | 非空字符串 | 仅 A 相使用的全书规划脚手架 | 码·契 |
| `output_chapters` | 非空数组 | 一章对一章的盲候选 | 码·契 |
| `_provenance` | `BlindCandidateProvenance` | A 的策略/逐章获采纳调用聚合来源账；逐 unit 明细留在 ledger/session/checkpoint | 码·契 |

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

### 2.3 调度、检查点与原子提交

策略 session 是逐章扇出的串行前置；策略验证并 checkpoint 后，N 个逐章 session 才能启动。节点内
实际并发取 `min(4, NOVEL_CONCURRENCY, 待跑章数)`：配置可以下调，但硬上限始终为 4。策略和每章分别
绑定同一个 `run_id` 下自己的 purpose、provider session、请求身份、
accepted attempt、ledger 与验证后 checkpoint。进程中断后只恢复匹配当前代码/契约/输入/prompt/schema
且 ledger 闭合的已验 unit，只补缺失或失败单元；恢复 checkpoint 不记 cache hit，也不新增 wire。
正式 provenance 的 usage 不取“本次重启以后”的增量：它按当前 `run_id` 下 N+1 个稳定 purpose
从 ledger 重建全部成本，包含先前无 checkpoint 的失败私有 session；因此部分恢复、完整恢复与
aggregate prepared 尾窗恢复必须得到同一份 usage，不能把已付费历史显示成 0 或漏掉。

unit checkpoint 不是正式 artifact。只有策略和全部章节齐全，代码才能把 local alias 映回正式 alias、
注入章号/权重/`candidate_scene_id`、按章排序并组装完整 `BlindCandidate`；聚合物必须再过 strict schema、
单章动态闸与整书全部 HARD。通过后才由 commit journal 原子写 `blind_candidate.json`；中断、缺章、
聚合失败或 HARD 命中都不得留下部分正式文件，也不得覆盖旧正式候选。

### 2.4 正式 A 设计审阅与定向修订

A 的 schema/HARD 通过只说明候选可读、引用闭合，不替人判断它是否实现了足够的结构重构。审批服务对
当前 A 构造完整 review subject：全局 basis 绑定 root/truth/alias/review contract；策略 subject 绑定
重构策略与章权重；每章 subject 绑定同一策略摘要、安全的本章盲真相、候选业务面，以及服务端调用
`structure_metrics_of` 现场计算的声明/实际/已实现/未实现/未声明变化和无 id 标量读数。formal fact/person
id 与来源位置不公开；opaque alias 只在本章 subject 内闭合。

一次 `DesignReviewRequest` 必须原子覆盖当前策略和全部章：策略 `accept / request_replan`，逐章
`accept / request_repair`。repair 章必须给关闭式 `{code, scene_refs[], opaque_aliases[]}` constraints，
各 code 的最小引用数只定义在 `DESIGN_REPAIR_CONSTRAINT_ARITY`；reason 只作审计，不发给模型。
全局 basis 或策略变化使全章旧决定过期；单章业务变化只使该章过期。同一策略下、subject 完全相同的
历史 accept 可由模板带 `source_decision_sha256` 复用，但仍须对当前整书重新提交一次，不静默代签。

逐章拒绝只允许 `stage4.design --repair-decision <当前决定 SHA-256>`：重跑 request_repair 章，其他章
引用既有已验 checkpoint；正式 provenance 记录 parent candidate、决定摘要与逐 unit lineage。旧首轮
候选尚无 lineage 时，core 以正式 candidate 完整 SHA、业务主体和 committed aggregate journal 唯一反查
基线 run；零或多匹配关闭式拒绝。策略被拒才允许 `--replan` 整书重做。两条付费路都只产新 A，之后
必须重新审阅；任何审批提交本身都不启动作业。

## 三、Stage4B：`evidence_binding.json` v3

B 收到冻结候选、候选业务主体 SHA-256，以及原文证据、源舞台和必须材料的关闭式目录。代码先用
`build_mechanical_baseline` 把 scene fact 经 `source_beat_ref` 映射到舞台 allowlist、把 material 经属主
beat 映射到 eligible scene；唯一归属和签名等价候选均由代码规范化决定。只有
`stage_signature_conflict`、`material_multi_scene`、`material_unplaceable` 三类真自由度形成内容寻址
ambiguity id，并只把对应章送入私有 session。人物到场由绑定舞台的 `present` 唯一给出，B 不再回填
第二份人物清单。B 没有结构字段入口，不能修改 A 的场景顺序、事实分组、goal、结构声明、brief、
purpose/role/delivery/transition 或权重。

其中每一个 `stage_signature_conflict` 都按 `R-5.24` 常驻为 INFO：选定舞台只闭合 binding，不会把
“这里曾存在多个时间/地点/在场签名”洗掉。该披露进入 structure workpack，但没有付费 action。

机械章不建模型 session、不写章级 checkpoint，恢复时从冻结输入 0 成本重算。每个歧义章独占
provider session、purpose、ledger attempt 与 checkpoint；实际并发取配置、待跑章数与硬上限 4 的最小值。
单章响应在 worker 内依次过 parse/必需键/schema/动态 validator 与本章 baseline 闭合，成功即原子
checkpoint，后章失败不能取消或抹掉此前已验章。聚合始终按章号确定，B-owner 的整书 HARD 若能唯一
路由到一章，只回该章原 session；新响应通过后同时替换 checkpoint、accepted attempt、unit lineage 与
commit anchor，不能让正式来源账继续指向旧稿。A/Stage2/code owner 立即停止，不消耗该章后续轮次。

章 prompt 只含本章候选、相关原文/hinge 文本、本章 blueprint 与关闭目录、机械歧义，以及相邻章一行
边界摘要；不得携带其他章正文。时间层冲突无法闭合时不选任意 fallback，保留其他章 checkpoint，转
hash-bound unit replacement。人工 replacement 与模型章响应复用同一个 `ChapterBindingResponse`
validator、聚合 compiler 与全书 HARD；它只消费已封存且绑定原 run/request/code/subject 的 resolution，
不新增 provider wire。整份人工 `--apply-binding` 继续作为另一条明确入口，不与 unit replacement 混用。

| 字段 | `bound` | `infeasible` | 证据 |
|---|---|---|---|
| `status` | `bound` | `infeasible` | 码·契 |
| `candidate_sha256` | 必须等于冻结候选主体 | 同左 | 码·契 |
| `chapters` | 非空；章场集精确匹配候选 | `null` | 码·契 |
| `binding_concerns[]` | 可空；非阻断语义担忧 | 模型主张阶段必须非空；正式稿保留作审阅证据 | 码·契 |
| `infeasibility_proof[]` | `null` | 非空、由代码铸造的关闭式 witness | 码·契 |
| `_provenance` | `architecture=chapter_baseline_v1` 时含 baseline/chapter session 的逐章 `unit_lineage`；全机械书明确 0 模型；旧整书路为 `whole_book_v1`；`--apply-binding` 为 human 免费来源账 | 同左 | 码·契 |

`bound.chapters[].scenes[]` 只含 `candidate_scene_id`、`stage_refs`、`material_refs`。每个候选场恰绑定
一次，每条材料全章恰落一次；返回数组顺序不产生叙事语义，编译始终沿 A 顺序。

模型的 `infeasible` 只是待核证主张，不是 artifact。代码在同一目录上先作构造性求解：合法章至少有
一个舞台、每场可复用首项舞台、全部材料可唯一落到首场，因此常规输入可绑定；错误主张在同一 session
返回结构化反馈，要求完整 bound。只有 `prove_binding_infeasible` 能从关闭式目录铸造 witness（例如章目录
缺失或合法舞台集合为空），才允许正式 `infeasible`；它不允许部分 binding，也不自动生成 A2。
`binding_concerns` 的 scene/closed refs 同样闭合，并进入最终结构审批 subject，但不自动阻断编译。

0-wire `stage4.plan --apply-binding <path>` 接受 `EvidenceBindingResponse` 业务 JSON，不接受客户端伪造
`_provenance` 或 proof；core 现场铸 `origin=human`、独立证明任何 infeasible，并复用同一 compiler/HARD、
写盘 journal 与 readback。

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

计划业务主体绑定同号章、`from_source_ordinals`、三个 goal、brief、预算、全部场景执行字段和人工结构
声明；不包含来源账、已删除字段或现场派生向量。最终人审 subject 再把该 plan subject 与当前 B 的
完整 `binding_concerns` 合成唯一 SHA-256。改变实际计划或 concerns 都会使决定过期；只改变算法读数
不会改写计划字节。审批 proof 另外绑定 `plan_chapters.json` 与 `evidence_binding.json` 完整字节，缺一不可。

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

公开 `ApprovalDoc` 是 v5；服务盘上 `_service/approvals/<sid>.json` 是 v6。旧 v5 只经显式
`backfill_from_disk` 逐项对证，普通 GET / 启动不静默迁移。迁移保留可对证的 gate/seen/结构/writing，
但 design history 恒空，绝不从既有 A/B/Plan 或付费记录伪造 A 审阅；只有同时 current 的 Plan v4
与 bound EvidenceBinding v3（含其 INVALID Plan 的零成本恢复）可继续最终结构审批，未来再次运行 B
才要求 current A accept。旧 v2 Binding 不迁移、不补字段，须按 A 审阅 → B producer 重建。一次最终结构决定必须提交当前
subject SHA-256、actor、全书理由及与计划章集精确相等的逐章 accept/request_replan。全章 accept 才
打开 s4；任何 request_replan 都关闭 s4、保留 typed 决定并撤销旧 writing/下游证明，但不自动启动
付费 A/B。计划变化后旧决定进入 history，当前态变 expired。

## 七、失败与恢复

| 状态 | 允许的恢复 | 禁止的捷径 | 证据 |
|---|---|---|---|
| A 的策略或单章响应暂停/拒收 | 同一 `run_id` 恢复，只补失败/缺失 unit；旧 whole-book validator 拒稿才可走 `--adopt-rejected`，当前 split-run unit 仍按活动待办收口 | 重发已验 unit、公开 `--chapters`、拿旧整书入口冒充 unit adoption、补字段或部分聚合冒充正式候选 | 码·契 |
| A 正式审阅逐章拒绝 | 当前 decision SHA 绑定的 `--repair-decision` 只返修 request_repair 章；策略拒绝才 `--replan` | 把默认模板理由当真实反馈、自然语言自由指令、无决定随机重采整书 | 码·契 |
| 模型声称 B `infeasible` | 同 session 结构化反馈要求 bound；真实代码 witness 可落正式终态；亦可提交 `--apply-binding` 或完整 v4 人工计划 | 信任模型自证、B 私改 A、自动生成 A2、要求已有 Plan 才给首次恢复 | 码·契 |
| 计划落 `.INVALID` | 修订 v4 `plan_edit.json` 后 `--apply-edit`，或 `--revalidate --actor --reason` | 改名提升、绕过 schema/判据/归档 | 三源 |
| Context 缺失或 stale | 最终结构全章 accept 后用 `stage4.context` 免费重建 | B/计划修复时提前代写，或用旧上下文继续写 | 三源 |
| 旧版本 | 从 producer 重建；人工稿重写为 v4 | 迁移、猜字段、手补版本号 | 码·契 |

## 八、现行判据

当前注册表共 **33 条**：**20 hard、3 info、10 metric**。

| 分组 | 当前规则 id |
|---|---|
| 章场与事实安全 | `one_to_one` · `ordinal_seq` · `empty_chapter` · `fact_ref` · `fact_coverage` · `causal_closure` · `state_closure` · `material_coverage` · `scene_transition` · `goal_landing` |
| 预算与文本策略 | `budget_int` · `budget_band` · `plan_book_band` · `style_words` · `verbatim_starved` |
| A/B 与 Context | `phase_artifact_link` · `context_link` · `ctx_facts_exact` · `ctx_name_attested` · `ctx_stage_explicit` · `ctx_hinge_delivered` |
| 因果与舞台提示 | `causal_disclosure` · `stage_signature_conflict` |
| 常驻读数 | `scene_budget_distribution` · `verbatim_share` · `partition_change` · `disclosure_order` · `noncontiguous_regroup` · `anchor_migration` · `structure_eligibility` · `structure_alignment` · `semantic_closure_reading` · `ctx_source` |

判据权威定义在 `contracts/judges/stage4.py`。metric 不产生自动结论，API、前端与审批服务不得再造第二个判官。
