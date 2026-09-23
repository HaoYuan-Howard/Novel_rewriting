# STR-06 终态架构 RC 契约（Stage2–Stage5 迁移）

本文件是 STR-06 迁移的 schema 侧登记：四类 truth、最小 RC 模型、路径家族、
Stage6/7 消费投影、Prepare 坐标分离、Golden rubric 迁移与 F8 切换清单。
批次进度与参考件清单见 `docs/REQUIREMENTS.md` STR-06；架构裁决见
`docs/DECISIONS.md` 2026-09-23。

**RC 边界**：代码落点为 `contracts/schema/str06/` 子包。字段级 schema 保持 RC，
经 F1 反例与 F2–F6 真书验证后定版。本子包**不进** `ARTIFACT_SPECS`、不注册
`NODES`、不进判据注册表；现役链按旧契约验收，正式替代一次合入（F8）。
RC 版式身份 `str06-rc1`。

## 四类 semantic truth

| Truth | 唯一职责 | 目标产物 |
|---|---|---|
| Characters | 源人物身份、人物初始状态、稳定约束 | `stage2/characters/characters.json`（v4） |
| Blueprint | 源事件、变化、揭示、因果及书级初始事实／约束 | `stage2/blueprints/chapter_blueprints.json`（v12） |
| Target Design | 目标初始语义选择、场景、目标拍、戏剧驱动、目标章归属 | `stage4/design/presentation_graph.json`（v1） |
| Semantic Binding | Preservation 决定、source lineage、保留约束和目标承载 | `stage4/grounding/semantic_binding.json`（v1，完整 snapshot） |

其余全部 Derived：`stage4/grounding/reconstruction_brief.json`、
`stage4/compiled/execution_plan.json`、`stage4/context/scene_{nnn}.json`、
`stage5/scenes/scene_{nnn}.txt`、`stage5/acceptance/fidelity/scene_{nnn}.json`、
`stage5/rewritten_body.txt`、`runtime/`。`T₀ = Init(Compile(D,G))` 确定性派生，
Binding 不维护第二份目标初始状态。

## RC 模块地图（contracts/schema/str06/）

| 模块 | 承载 | 关键不变量（构造期表达） |
|---|---|---|
| `policy.py` | `PreservationPolicy`（版本化策略）、`PreserveKind` 闭集 | 类别集变化必须升版；执行身份不进 Policy |
| `grounding.py` | `SourceObligation` / `PreserveCheckpoint` / `ReconstructionBrief` / `IdentityProjection` / `OutputConstraints` / `SemanticBinding` | 必保义务必有非空 `satisfaction`；checkpoint 无 Design/Bind 字段；Brief 无源章/源拍/叙述顺序/证据定位字段；Binding 是完整 snapshot，entries 与 unbound 不交集 |
| `design.py` | `PresentationGraph` v1（`SceneDriver`/`TargetBeat`/`TargetDelta`/`SceneDelivery`/`ChapterAssignment`） | source-blind（无 source_ref 族字段且 forbid extra）；章号连续 1..N、场景全覆盖、每章非空 |
| `execution.py` | `ExecutionPlan`（establishment/constraint/dependency/reinforcement）与 `ExecutionSlice` | 必保只落 establishment/constraint；`Reinforcement` 无 obligation_id（0..N 不稀释必保）；依赖边禁自指 |
| `context.py` | `WriterContext` 双视图（`VerificationIR` + `WriterBrief`） | 两视图 scene_id 与 `execution_slice_sha256` 同源；IR 带 `continuity_fact_view`（跨场回指误报的根因修复输入） |
| `receipt.py` | `SceneReceipt`（最小七字段）与 `FidelityVerdict` | 无 plan hash/时间戳等无关 metadata 字段；`prose_sha256` 绑定改名前正文；`failure_origin ∈ {writer, upstream_ir, unverifiable}`；无修复字段 |
| `consumption.py` | 五个消费投影族登记与 `PrepareMaterials`（坐标分离） | 见下两节 |
| `families.py` | 三级路径家族登记 | 来源账键=完整相对路径（不塌两级） |
| `dependency.py` | 新链依赖方向登记 | F1 AST 守卫的数据源 |

跨产物不变量（不由 schema 自身保证，归 F4 机械 Guard）：Binding 的 obligations
分母必须与采用的 Preserve checkpoint 一致；ExecutionPlan 的依赖图无环；
entry_state/knowledge 的 `established_by` 均早于本场起点（试点 M2/M3 的迁移）。

## 路径机制

三级路径按登记家族准入（`contracts/schema/str06/families.py`，12 个家族）；
家族外多级路径仍拒，两级一般规则不变。实现在 `novel/paths.py`：
`check_rel_family`（准入）与 `family_input_key`（来源账键=完整路径）。
INVALID 孪生名与 `_rounds/` 归档沿用现役机制。两级特例
`stage5/rewritten_body.txt` 走现役两级准入。正文（`stage5/scenes/`）与付费
checkpoint 不是可随意删除的缓存；kind 登记是删除政策的输入，F8 收口前不实现
任何按 kind 的清理。

## Stage6/7 消费投影（F0 登记，F3/F4 免费干跑）

| 投影族 | 形状（RC） | 来源 owner | 坐标域 |
|---|---|---|---|
| world_view | era/setting/world rules/relations/secrets 的目标有效视图 | Characters+Blueprint+Binding+Design | target |
| character_exposure | 身份称谓 + 目标实际建立/揭示/使用位置 | Characters+Binding+compiled | dual |
| chapter_map | scene→target chapter 映射与目标章集分母 | Design+compile | target |
| body_access | accepted body 完整读取与目标开篇读取 | receipt+body+prepare | dual |
| rename_materials | 替换实体/关系/时代/候选文案/有效正文投影 | Characters+Blueprint+Design+body | dual |

坐标域纪律：源时点（首次提及、身份揭示、姓名可用）是源事实；目标时点由
Design/Binding/ExecutionPlan 的明确语义位置派生，不能换算源章号，不能用
Binding lineage 猜缺失语义。dual 投影两套坐标分别表达。

## Prepare 坐标分离

`PrepareMaterials = SourceHookEvidence + TargetOpening|null`。源
`through_chapter` 只作用于源证据，不控制目标材料；`TargetOpening` 基于
accepted target body；尚无 accepted body 或目标开篇时为 null——显式未就绪，
不制造成功状态。必要时升版现役 Prepare 契约（Stage6 owner），不新增节点或
故事 truth；源文支持 ≠ 目标文案可用，不反写目标故事事实。

## 接受正文与交付正文分离

Stage5 receipt 绑定改名前正文；Stage7 用有效 name_map + 选定 Hook + accepted
body 做确定性替换组装，另行绑定交付身份。同一 accepted body、同一 Hook、同一
有效映射与组装契约 ⇒ 重放得到相同交付字节；换 name_map 不使 Stage5 回执失效。
残留检查沿用 `claimed/kept`：拒绝未认领、应替换的残留；合法保留称谓及认领
区间内的字面不误报。

## Golden rubric 迁移（F0 登记，F7 工具适配，F8 执行）

六维：Core Preservation、Structural Originality、Causal Continuity、
Character Continuity、Natural Progression、Local Prose Coherence。人工阅读
验收为主、模型评分辅助；未评或证据不足保持未验证。随目标语义版本化的判据
改写：现役「遵守源书阅读时序」职责改由 Core Preservation + Causal Continuity
按目标语义位置承载（跨章重构后源时序不再是输出约束）；事实、因果、披露与
改名质量覆盖不得丢失。Structural Originality 以书/场景及语义承载关系比较，
不要求同号章对应；源书、新书、baseline 的证据位置分别记录。跨章自由度：
F1/F4 以反例证明能力接通；F8 报告 Design 是否实际使用及证据，未使用则记录
「真实生成中的跨章质量未获验证」，不强迫模型搬移情节。Golden 不代替
STR-05 的 current-schema corpus 验收。

## 依赖方向（F1 AST 守卫消费）

规则数据在 `contracts/schema/str06/dependency.py`（11 条模块规则 + 3 条横向
规则）。要点：Design 禁 Grounding 实现/Blueprint internals/checkpoint 裸读；
Grounding 禁 Design 实现；Compile/Context 禁 LLM 与 producer 草稿；Stage5 禁
Stage2 internals 与 Design/Grounding 实现；Stage6/7 禁 Stage2/4/5 internals；
guard/judge 禁写 canonical；shared/repository 禁反向依赖 Stage 业务。

## F8 契约切换清单（准备项）

F8 一次合入时同步闭合，候选可提前准备但不得提前现役化：

1. `ARTIFACT_SPECS` 版本翻转：characters v4、blueprints v12、新路径
   presentation_graph/semantic_binding/execution_plan/scene contexts/scenes/
   fidelity/rewritten_body + runtime 家族；旧 stage2/4/5 条目移入 retired。
2. 公开契约改写：I-40 与 `R-5.2`（一章对一章×5）按目标 N 章连续非空语义改写；
   R-2.*（Characters v4）、R-3.*（Blueprints v12）、R-5/R-6（Stage4 新链）、
   R-7/R-8（Stage5 新链）、R-9/R-10 中消费旧产物的条款同步。
3. `NODES` 与编排：付费节点面（Stage2 抽取、Preserve/Design/Bind、
   Write/Judge、Stage6/7）注册与阶段依赖；旧节点移入 RETIRED。
4. 判据注册表与 `docs/schema/` 现役文档：试点 M1–M6 机械 Guard、Judge 判定
   按新坐标正式化；`tests/test_artifact_versions.py` shape-break 账本同批答
   「谁重建」。
5. retired 登记：旧链产物路径进 `RETIRED_ARTIFACT_PATHS`；旧书
   `legacy_incompatible` 只读解码。
6. 前端：消费投影对应 API/Views 形状与 generated types 重生成。
7. 验收：契约 §9.5 五文件、默认非 corpus 回归、前端四件套 → Golden → 合入。
