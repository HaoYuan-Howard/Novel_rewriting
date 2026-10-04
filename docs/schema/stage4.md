# Stage 4 当前契约

Stage4 在新链（STR-06，F8a 激活）有四个节点：三个付费节点
`str06.stage4.preserve`（保存策略义务）→ `str06.stage4.body_design`（目标故事与
导演说明，同节点双产物发布 SemanticBinding——CS-3 起 bind 节点退役，义务承载位由
design wire 的 real 标注派生）→ `str06.stage4.hook_design`
（Hook 必要选择，并经 `publish_graph` 唯一发布 PresentationGraph），加一个确定性
节点 `str06.stage4.compile`（ExecutionPlan／Guard／WriterBookIR／HookContext，
`executor_content_limit=0` 结构性禁付）。

Design 是 source-blind 的：实际输入只有 Preserve 投影（`project_brief`）、身份投影
与 Characters v4，源叙述拓扑不进 prompt。章数恒等于源章数；跨章重构合法——场景可跨
源章重组，输出章仍与源章一一同号（`R-5.2`）。
孪生（typed witness）后 fail-fast，同指纹不重购。

## 现役 JSON 产物

本层产物**都是 schema v1**（三级路径是切换时新建的命名空间；形状破坏史由模型
`contract_version` 字面量与候选产物表的 RC 标签承载，见 `docs/BACKEND_CONTRACT.md`
`R-2.5`／`R-5.11b`）。`runtime/preserve_checkpoint.json` 是已购 canonical 证据，
例外登记在候选产物表。

| 路径 | 模型 | 生产节点 | 内容 |
|---|---|---|---|
| `runtime/preserve_checkpoint.json` | `PreserveCheckpoint` | `str06.stage4.preserve` | 保存策略义务（已购证据；checkpoint 存储不是下游读取接口） |
| `stage4/grounding/reconstruction_brief.json` | `ReconstructionBrief` | `str06.stage4.preserve` | ReconstructionBrief 确定性投影 |
| `stage4/design/body_design.json` | `BodyDesign` | `str06.stage4.body_design` | 目标故事与导演说明（正式派生产物；hook_design 的单节点输入） |
| `stage4/grounding/semantic_binding.json` | `SemanticBinding` | `str06.stage4.body_design` | 义务到目标位置的完整 canonical snapshot（CS-3：由 design wire 的 real 承载标注派生，双产物同 bundle 发布） |
| `stage4/design/presentation_graph.json` | `PresentationGraph` | `str06.stage4.hook_design` | BodyDesign＋Hook 合并发布（唯一 publisher） |
| `stage4/compiled/execution_plan.json` | `ExecutionPlan` | `str06.stage4.compile` | 四类执行语义与依赖边 |
| `stage4/acceptance/guard/compile.json` | 自由形态派生记录 | `str06.stage4.compile` | Guard 编译错误清单 |
| `stage4/acceptance/guard/report.json` | 自由形态派生记录 | `str06.stage4.compile` | Guard 报告（planned-only 读数） |
| `stage4/acceptance/packet_metrics.json` | 自由形态派生记录 | `str06.stage4.compile` | WriterPacket 逐章指标（分区条目数、基线事实数、活跃/受保护线程数、重复比例、必须写出密度；audit_only 读数） |
| `stage4/context/hook/hook.json` | `HookContext` | `str06.stage4.compile` | HookContext 确定性投影（POV、起笔事实、材料序、禁止集、称谓释放） |
| `stage4/ir/writer_book_ir.json` | `WriterBookIR` | `str06.stage4.compile` | 全书静态写作 IR（BookBrief 导航＋ChapterIR 双视图） |

## Guard M1–M8

写前机械 Guard 在 `str06.stage4.compile` 聚合执行（`novel/stages/stage4/guard.py`）：
M1 义务覆盖、M2 提前揭示、M3 提前效应、M4 承载位置、M5 称谓释放、M6 合法人物、
M7 声明读取已解析进 IR（入场值进 entry 视图、拍级值挂对应拍，AUD-03）、
M8 线程生命周期与投影（开一次、时序闭合、结局闭集；thread_ops／open_threads
按 Design 进 IR）。聚合入口遍历判据登记表 `GUARD_CHECKS`（判据码 → 检查函数，
M2/M3 共用 `check_entry_timing`），码集合与判据注册表的 `guard_m{n}_*` 逐码对账
（防漂移断言在 `tests/test_hard_counterexamples.py`）。Guard 零错误不证明小说
质量；本节点确定性重跑是 0 成本修复路。

## 判据表

| 判据 | sev | 要求 |
|---|---|---|
| `body_design_compile` | hard | BodyDesign 编译闭合 |
| `design_surface_release_closure` | hard | Design 放行面闭合在 Characters 登记面内（未登记字面不得释放） |
| `design_knowledge_mode_closure` | hard | Design knowledge 域知情模式闭集（known/suspected/heard/believes；believes 不代表世界为真） |
| `chapter_plan_shape` | hard | 目标章数等于源章数、章序 1..N 连续非空；允许跨章重构 |
| `length_budget_conserved` | hard | 章预算 largest-remainder 分配、总和守恒 |
| `preserve_compile` | hard | Preserve checkpoint 编译闭合 |
| `binding_mechanical` | hard | CS-3：binding 派生错误并入 body_design 的 ask 同节点修订轮（≤2）；real 覆盖闸漏标即拦；耗尽/无进展 fail-fast |
| `hook_design_mechanical` | hard | Hook 设计机械校验＋graph 唯一发布 |
| `design_semantics_unver` | unver | 目标故事与导演说明的语义质量未评 |
| `scene_budget_distribution` | metric | 章预算相对场景权重的分布读数 |
| `execution_plan_compile` | hard | ExecutionPlan 编译闭合 |
| `writer_ir_dual_view` | hard | WriterBookIR 双视图同源 |
| `hook_context_rebuildable` | hard | HookContext 仅凭正式输入可确定性重建 |
| `guard_m1_obligation_coverage` | hard | M1 义务完整进入 IR |
| `guard_m2_early_reveal` | hard | M2 不提前揭示 |
| `guard_m3_early_effect` | hard | M3 不提前效应 |
| `guard_m4_carrier_ref` | hard | M4 承载位置真实 |
| `guard_m5_surface_release` | hard | M5 称谓释放一致 |
| `guard_m6_cast_legality` | hard | M6 合法人物 |
| `guard_m7_read_projection` | hard | M7 声明读取已解析并进入 IR（入场值进 entry 视图、拍级值挂对应拍） |
| `guard_m8_thread_lifecycle` | hard | M8 线程生命周期合法且按 Design 投影进 IR（thread_ops／open_threads） |
| `chapter_closure_read_resolution` | hard | 章闭包：已声明状态读取在定位点闭合（先读旧值后写新值；unknown/unset/未建立分开） |
| `preserve_anchor_closure` | hard | R10a a2：义务 anchors 在投影句柄闭集（≥1）、lineage 由 compiler 派生、series 成员闭集互斥、evidence_conflicts 引用闭合 |
| `source_evidence_conflict_routing` | hard | R10a a3：源证据冲突按锚点句柄回派 Stage2 owner（拍句柄→逐章、书级→book、不可解析即停）；三分支恢复＝修订／保留解释（revise_self 恢复输入）／未决停止；同指纹不二次回派 |
| `design_lock_acceptance` | hard | R10a a4：Brief 锁定项（kp 命题登记＋知情锁唯一映射＋kf 事实锁）与 Design v5 锁规则——kp 键持有者须有锁且模式被授权（known 只能来自 known 锁）、读者确认须有 reader known 锁、kf 时态锁按锁 modality 承载、值未知/原文未回答不得 set、必保锁兑现 |
| `binding_series_collapse` | hard | 同一 series 成员接受拍不同（同拍即 series_collapsed，design 修订轮）；锁摘要经 design 义务清单送达；必保漏标由 real 覆盖闸拦 |
| `epistemic_lock_order_guard` | hard | R10a a5：kp 知情升级/读者确认不得早于授权该模式的义务承载位置（授权位＝必保义务全部承载点最早者；initial_state 与自由键不查；故事发生顺序不查，P5）；违例按 epistemic_lock_order 回派 body_design |
| `answer_reuse_four_conditions` | hard | R10a §二.4：已购回答复用四条件——模型输入未变→复用；验收条件变化→免费机械复验；实际写作材料变化→按请求身份判断新回答；仅审计/展示变化→不触发付费重写 |
| `writer_packet_background_narrowing` | hard | R10b §R10b.3：背景投影规则——写手面背景只投书级初态立足点＋建立之后仍被消费的当前状态（消费者＝声明读取/读者确认目标/Hook 地址/锁键）；unset/unknown 不作背景；补齐面（依赖边生成）保持全量 |

当前注册表共 **29 条**：**27 hard、1 metric、1 unver**（R10a 收口新增 6 hard；R10b 新增 1 hard：背景投影规则）。唯一注册表为
`contracts/judges/str06.py`（F8c 起分流至本层判据模块）。
