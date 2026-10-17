# Stage 4 当前契约

Stage4 在新链（STR-06，F8a 激活）有四个节点：三个付费节点
`str06.stage4.preserve`（保存策略义务）→ `str06.stage4.body_design`（目标故事与
导演说明，同节点双产物发布 SemanticBinding——CS-3 起 bind 节点退役，义务承载位由
design wire 的 real 标注派生）→ `str06.stage4.hook_design`
（Hook 必要选择，并经 `publish_graph` 唯一发布 PresentationGraph），加一个确定性
节点 `str06.stage4.compile`（ExecutionPlan／Guard／WriterBookIR／HookContext，
`executor_content_limit=0` 结构性禁付）。

Design 是 source-blind 的：实际输入只有 Preserve 投影（`project_brief`）、身份投影
与 Characters v6，源叙述拓扑不进 prompt。章数恒等于源章数；跨章重构合法——场景可跨
源章重组，输出章仍与源章一一同号（`R-5.2`）。
孪生（typed witness）后 fail-fast，同指纹不重购。

Stage2 是源约束下的结构化观察：原文是证据，事件、认知和关系的抽取仍包含 LLM
理解；Preserve 再由 LLM 选择应保骨架及必要条件，义务并非逐字摘要，也不保证每条
源观察都变成必保要求。compiler 只按正式选择派生引用与消费材料，不补做语义判断。

Preserve 现役保存策略为 v11，wire 为 `stage4-preserve-v6`。源盲交接的必要触发、
答话、限制、权衡与选择须在 statement、satisfaction 及 requires 所指义务的联合材料中
可理解；已经明确的前提不要求在 satisfaction 再抄一遍。同一时点、同一作用的触发与
响应可以合写，不同时点的必要前提仍分别承载。可改写动作进可选 details，源文未给的
桥梁不补猜；Design 可在既有约束内用事件、realization_brief 与 entry 组织目标演法。
源 anchors 只证明来处，不会把其原文自动送给 Design 或写手，compiler 不从原文另作
语义补全。这是材料与生成规则，因果自洽、人物转变自然与场景承接仍需阅读验收。

同一人物的多个已登记称谓可以自然共现。唯一绑定成立时逐次投影，保留各次提及及
中间行动、对白，不因同句混称拒收。只有原文独立邻接的显示面可折叠同位语；前次
替换生成的显示面及另一登记称谓内部的相同字面都不能吞掉重复提及。普通称谓共现
不构成揭名，正式必保揭名仍由下述三项类型化信号合取派生并受释放闸保护。

## 现役 JSON 产物

本层产物**都是 schema v1**（三级路径是切换时新建的命名空间；形状破坏史由模型
`contract_version` 字面量与候选产物表的 RC 标签承载，见 `docs/BACKEND_CONTRACT.md`
`R-2.5`／`R-5.11b`）。`runtime/preserve_checkpoint.json` 是已购 canonical 证据，
例外登记在候选产物表。

`obNN` 是 compiler 按当前义务列表派发的快照内编号，不是跨 Preserve 版本的稳定
身份。checkpoint／Brief 更新后，旧 Design／Binding 及 IR／Plan 按各自正式输入身份
判新鲜度，不能仅换义务编号就沿用；历史候选须经已有修订谱系、完整差异与当前输入
预检决定免费沿用或受影响拍窄复核，原响应、费用与累计轮数保留。

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
| `stage4/context/hook/hook.json` | `HookContext` | `str06.stage4.compile` | rc25 确定性投影（POV、起笔事实、有序材料与组织选择、阅读期待、正式风格与声线、禁止集、身份称谓许可与验证候选） |
| `stage4/ir/writer_book_ir.json` | `WriterBookIR` | `str06.stage4.compile` | 全书静态写作 IR（BookBrief 导航＋ChapterIR 双视图） |

## Hook 组织与事实交接（2026-10-10）

现役模型 wire 为 `stage4-hook-design-v4`。`materials` 是唯一有序材料列表，每条显式
选择 `emphasis`（develop／brief）和 `transition`（start／continue／time_skip／flashback／
return／scene_cut）；首条为 start。默认围绕一个清楚、尚在推进的核心冲突，允许倒叙与
跨场；切换须有时间定位并保留理解所需的因果前提。设计同时考虑正式第 1 章将新增什么
认识、选择、代价或关系变化，不把楔子完整重复成开章。上述文学选择不是机械质量硬闸。

`design_brief`／`delivery_brief` 已退出正式 Hook，`local_focus` 仅表达表现重点与反差
机制，不另写动作、动机或结果。API 原 `design_brief` 展示字段由正式有序选择与表现
重点确定性生成，没有第二份剧情真源。入场与截断沿用现役拍引用，不增加动作地址模型。

`reader_expectation` 区分 fact／action／relationship。fact 选正式事实目录的 `fact_ref`，
compiler 展开为 `{ref,address}`；action／relationship 引用后续发展拍。依据须在材料
故事视界之后；未来依据仅供设计与校验，写手只收到 kind／focus，不能获得可使用的未来
答案。引用存在和时序合法不证明期待与依据的语义匹配或悬念吸引力。
独立显式选择的 `forbidden_reveals` 仍按现役规则投影为负向禁区，阅读期待的引用
不自动加入其中，也不自动把未来发展全文发给写手。
整拍禁揭仍禁止选用该拍；缩小到具体事实地址的禁揭允许使用同拍其他材料，但必须
在每一条选中该拍的材料上，用 withheld 隐藏该完整事实地址，或用 presentation
替代整拍投影。其他地址的 withheld 不代替此遮罩，整拍禁揭也不能靠事实遮罩绕过。
免费重建与初答共用守卫，不能因材料遮罩放行起笔事实、阅读期待或其他条目的泄漏；
presentation 的文学呈现与语义保真仍保持未评披露。

Context 从同一 BodyDesign 投影 `book_style`、出场 `character_voices` 与材料场的
`scene_voices`，以及正式 `expression_decisions` 的已选机制 statement；表达决定的
投影是 Derived `expression_mechanisms`，不重定义 canonical 决定、不复制决定理由。
场声线按适用材料编号送达，来源场锚与表达 ex_id 只用于归因。人物所知、相信、怀疑
和世界事实保持区分，材料选择、称谓与披露纪律不变。
正文章包从 BodyDesign 独立编译，背景消费闭包不再把 Hook 选择当成正文消费者；
调整 Hook 的组织、期待或声线投影不改变已接受正文的章包与回执身份。
PresentationGraph、HookContext、BodyDesign 及正文章包使用 rc25 的身份文本协议。
旧 Hook v1／v2／v3 缺少当前组织或身份声明，不隐式升级；历史已购字节与成本
保留，现役接受需要新 v4 设计与正式输入重新编译。

现有 Stage4 条目窄修继续按受影响条目工作，合并后经过完整设计、Context 和材料预检；
免费重验只能接受与当前请求身份及契约一致的已购回答，不因修订放宽恢复身份。

## 契约驱动的返修计划（2026-10-17）

首次生成与返修使用相同的合法 wire 契约，但编辑权限不同。形状诊断保留错误类型、
原始故障位置与所属 schema；`shape_errors.issue_path` 保留故障证据，`wire_paths` 提供
结构定位依据，最终允许替换的位置还须经 owner 政策与计划校验，三者不能混同。
定位器依据 schema 计算结构上可完成修复的范围，再应用 owner 的语义边界：额外键须
替换包含该键的完整对象，缺少字段须允许替换可补齐它的对象，数组长度错误须允许替换
整个数组；联合分支标签不作为真实 JSON 键。合法字段取值错误按其合法子 schema 修复。
数组、对象、标量、空值等坏稿值的具体形态不决定修复权限。

Design 场景内部的额外字段统一以完整场景作为语义修复边界。拍上非法 `reads` 携带的
读取是否仍需保留、是否应写到合法场景级 `reads`，由模型在整场内判断；程序不搬字段、
不推断所读状态，也不把 `[]`／`null` 视作删除。正式 `TargetScene.state_reads` 与
`stage4-design-v11` 场景级 `reads` 契约均保持原样。
Hook 按正式 schema 的顶层记录或顶层数组条目作为完整语义单元返修，阅读期待的类型、
焦点与事实／后续引用共同复核；数组自身的长度错误仍替换数组，边界不由坏值形状决定。

共享接受循环把目标、目标子 schema、问题覆盖与基底摘要冻结为同一返修计划，再据此
生成目标列表、依赖切片、输出范围校验与合并操作。只读上下文不扩大编辑权限；整场
目标须闭合该场所有拍所引用的义务、知情、事实锁、人物、状态与称谓释放前缀，不因原
报错仅落在一个拍而漏送依赖。`state_history` 只读提供相关初始态和此前状态写入的原行及
路径，沿显式确认地址补齐依赖，不推断状态值；未来写入与目标场已有行不重复送达。
授权覆盖全书拍时，未标注承载的正式义务语义也须送达，不能只给合法编号。
每个目标值是对应子 schema 的完整替换；省略额外键才能
删除该键，不增加 `remove/move` 传输操作。发请求前检查子schema可执行性，回答合并前
用同一子schema验证每个替换值；形状非法的片段留证拒收，不覆盖携带原始语义的基底。

Design 返修切片统一为 `stage4-design-patch-slice-v4`，以 `wire_protocol_version` 原样标注
基底候选的 v9／v10／v11 协议；切片版本是派生请求格式，不改变正式设计或 wire 契约，
不据此重置节点额度，也不把历史候选冒充成新版本。

发出返修请求前先验证结构可修复性：目标必须合法、互不重叠、覆盖全部问题并容许必要
的新增或删除。计划无效或缺少定位依据时以工程错误停止并报告规则码，不购买一个无解
的局部修订，也不自动扩大成整份重生；只有正式路由登记的顶层结构／协议出口可整份
修订。合并版仍重跑完整 Design、Binding、执行计划与写手材料预检，未知、失败和未运行
分别披露。无进展、历史采用身份、修订上限与累计额度维持现役规则。

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
| `preserve_name_literal_gate` | hard | Preserve 自闸：statement／satisfaction／details[].text 的裸姓、未登记别名等无法唯一认领的字面带义务或细节条目路径窄修；唯一绑定的自然混称合法，不推断揭名 |
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
| `preserve_anchor_closure` | hard | 义务 anchors 与可选 details.anchor 在源投影闭集，lineage／detail_id 由 compiler 派生；series 与 summaries/covers 同组闭集；evidence_conflicts 引用闭合 |
| `source_evidence_conflict_routing` | hard | R10a a3：源证据冲突按锚点句柄回派 Stage2 owner（拍句柄→逐章、书级→book、不可解析即停）；三分支恢复＝修订／保留解释（revise_self 恢复输入）／未决停止；同指纹不二次回派 |
| `design_lock_acceptance` | hard | R10a a4：Brief 锁定项（kp 命题登记＋知情锁唯一映射＋kf 事实锁）与 Design v5 锁规则——kp 键持有者须有锁且模式被授权（known 只能来自 known 锁）、读者确认须有 reader known 锁、kf 时态锁按锁 modality 承载、值未知/原文未回答不得 set、必保锁兑现 |
| `design_requires_released_gate` | hard | CS-H H3（RC-61）必保揭名：必保 identity／reveal 引用、源证据为带名称谓首现句段、命题含该称谓三项合取派生 `requires_released`；该 know 所在拍必须已释放所列称谓（同拍合法），违例带 know 行路径可窄修；普通混称不触发 |
| `binding_series_collapse` | hard | 同一 series 的离散实例接受拍不同；显式 summary 不计作实例，可与最后实例同拍；必保漏标由 real 覆盖闸拦 |
| `binding_series_summary` | hard | 已采用 summary 覆盖的实例须已采用且不晚于概括；可重排实例、同拍概括。目标实例前情在概括拍送达，早于／未承载均有 Design 窄修与免费重编译 |
| `design_detail_ref_closure` | hard | 拍上细节只从正式 Brief 闭集引用；选中提示只进 creative_guidance，不选合法；悬空引用 typed 拒绝，可窄修并免费重验 |
| `epistemic_lock_order_guard` | hard | R10a a5：kp 知情升级/读者确认不得早于授权该模式的义务承载位置（授权位＝必保义务全部承载点最早者；initial_state 与自由键不查；故事发生顺序不查，P5）；违例按 epistemic_lock_order 回派 body_design |
| `answer_reuse_four_conditions` | hard | R10a §二.4：已购回答复用四条件——模型输入未变→复用；验收条件变化→免费机械复验；实际写作材料变化→按请求身份判断新回答；仅审计/展示变化→不触发付费重写 |
| `writer_packet_background_narrowing` | hard | R10b §R10b.3：背景投影规则——写手面背景只投书级初态立足点＋建立之后仍被消费的当前状态（消费者＝正文声明读取/读者确认目标/锁键）；unset/unknown 不作背景；补齐面（依赖边生成）保持全量 |

当前注册表共 **33 条**：**31 hard、1 metric、1 unver**（rc21 新增系列概括与细节引用闭集两条 hard）。唯一注册表为
`contracts/judges/str06.py`（F8c 起分流至本层判据模块）。

2026-10-17 身份引用文本现役协议与旧源输入边界见 [name_text.md](name_text.md)。本批输出使用 rc25；身份选择的语义准确性保持未评。
