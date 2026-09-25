# Stage 4 当前契约

Stage4 在新链（STR-06，F8a 激活）有五个节点：四个付费节点
`str06.stage4.preserve`（保存策略义务）→ `str06.stage4.body_design`（目标故事与
导演说明）→ `str06.stage4.bind`（义务到目标位置绑定）→ `str06.stage4.hook_design`
（Hook 必要选择，并经 `publish_graph` 唯一发布 PresentationGraph），加一个确定性
节点 `str06.stage4.compile`（ExecutionPlan／Guard／WriterBookIR／HookContext，
`executor_content_limit=0` 结构性禁付）。

Design 是 source-blind 的：实际输入只有 Preserve 投影（`project_brief`）、身份投影
与 Characters v4，源叙述拓扑不进 prompt。章数恒等于源章数；跨章重构合法——场景可跨
源章重组，输出章仍与源章一一同号（`R-5.2`）。Bind 机械校验拦下的稿进 `.INVALID`
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
| `stage4/design/body_design.json` | `BodyDesign` | `str06.stage4.body_design` | 目标故事与导演说明（正式派生产物；bind／hook_design 的单节点输入） |
| `stage4/grounding/semantic_binding.json` | `SemanticBinding` | `str06.stage4.bind` | 义务到目标位置的完整 canonical snapshot |
| `stage4/design/presentation_graph.json` | `PresentationGraph` | `str06.stage4.hook_design` | BodyDesign＋Hook 合并发布（唯一 publisher） |
| `stage4/compiled/execution_plan.json` | `ExecutionPlan` | `str06.stage4.compile` | 四类执行语义与依赖边 |
| `stage4/acceptance/guard/compile.json` | 自由形态派生记录 | `str06.stage4.compile` | Guard 编译错误清单 |
| `stage4/acceptance/guard/report.json` | 自由形态派生记录 | `str06.stage4.compile` | Guard 报告（planned-only 读数） |
| `stage4/context/hook/hook.json` | `HookContext` | `str06.stage4.compile` | HookContext 确定性投影（POV、起笔事实、材料序、禁止集、称谓释放） |
| `stage4/ir/writer_book_ir.json` | `WriterBookIR` | `str06.stage4.compile` | 全书静态写作 IR（BookBrief 导航＋ChapterIR 双视图） |

## Guard M1–M6

写前机械 Guard 在 `str06.stage4.compile` 聚合执行（`novel/stages/str06/guard.py`）：
M1 义务覆盖、M2 提前揭示、M3 提前效应、M4 承载位置、M5 称谓释放、M6 合法人物。
Guard 零错误不证明小说质量；本节点确定性重跑是 0 成本修复路。

## 判据表

| 判据 | sev | 要求 |
|---|---|---|
| `body_design_compile` | hard | BodyDesign 编译闭合 |
| `chapter_plan_shape` | hard | 目标章数等于源章数、章序 1..N 连续非空；允许跨章重构 |
| `length_budget_conserved` | hard | 章预算 largest-remainder 分配、总和守恒 |
| `preserve_compile` | hard | Preserve checkpoint 编译闭合 |
| `binding_mechanical` | hard | Bind 机械校验＋INVALID 孪生留证＋fail-fast |
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

当前注册表共 **17 条**：**15 hard、1 metric、1 unver**。唯一注册表为
`contracts/judges/str06.py`（F8c 起分流至本层判据模块）。
