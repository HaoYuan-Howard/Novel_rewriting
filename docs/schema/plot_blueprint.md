# 完整剧情与呈现契约（ChapterBlueprints v11）

本契约使用完整剧情节点，替换逐谓词事实目录。普通分场不改变 Stage2
语义主体；真实漏抽、错抽或必要知识边界缺失才修订上游。

V11（STR-03-S2-BP）为**无损结构压缩**：统一 evidence 解析入口；`source_contexts`
稀疏编码（保留列表基数）；书级 `narration_default`；`public_state_ref` 保留；
`story_before` 仅去完全重复登记。拒读 v10；存量付费整跑重建。

## 正式蓝图

`ChapterBlueprints` 保存 `blueprints[]`、可选书级 `narration_default` 与 provenance。
PlotBlueprint 保存章号、源文 SHA-256、chapter_function、beats、
evidence_anchors（章内物化表；读取经 `blueprint.evidence` 统一入口）、states、
inherited_state_endings、constraints、entry_state_refs、carry_out_refs、
actor_bindings、relation_uses 和 narration（可空——空时须有书级 default）。

PlotBeat 只在 content 保存完整剧情，另含稳定 beat_id、参与人物、
evidence_refs、可选稀疏 `source_contexts`。SourceContext 稀疏语义：
省略 evidence_refs≡beat；省略 time_layer≡已确定当下；省略 present≡participants；
显式 null=未知；[]=明确空。列表整体省略≡一条默认 context；有显式条目则只用显式。
`scx_*` 摘要基于规范化展开视图。

EvidenceAnchor 由段号响应编译，含证据 ID、起止段及零起点半开字符区间。
相同段范围去重；原文只在源产物保存。证据覆盖和重叠不是语义通过判据。
消费者须经 resolve/slice，禁止把整书证据表下发 Stage4 A。

PlotState 保存 state_id、content 或 global_ref（二选一）、modality、
holder（ActorRef）、established_at、ended_at、supersedes_state_refs、evidence_refs。
modality 为 world、belief、claim、unresolved；belief/claim 明确持有者；
world 不得带 holder。wire 响应（PlotStateResponse）是 Extract 形：必填
`semantic` + 可选 `canonical_ref_hint`；正式 XOR 由
`plot_compile.resolve_state_extract` 落地（hint 命中闭集→global_ref，否则
semantic→content）。仍接受旧 content/global_ref 双写并在 before 规范化。
状态性质不描述传播权限。成立可在章首或节点，解除须有节点锚点。
**准入（prompt，非数量 HARD）**：只登记跨过当前 beat 仍成立、且后章仍会消费
或必须解除的状态；拍内即时结果只进 beat.content。`plot_shape` 另报
`carry_out` / `same_beat_ended` / `chapter_local_ended` 读数，不设配额。
章间状态复用原 ID；章末不能携出已结束状态，不复制上章正文。
上章携入的状态在本章解除时，inherited_state_endings 只保存 state_ref、
at_beat_ref 和 evidence_refs，不回写旧章正文。被取代状态的结束锚点由新状态的
成立锚点导出；同一状态不能重复解除、重复取代或在结束后继续携出。

约束采用关闭式联合。cause/story_before/response_to/same_scene/observes
保存两端节点，observes 另含观察者；literal/value/object 保存目标节点及必要值。
所有约束有稳定 ID 和证据，节点内部普通关系归 content，不生成自环。
因果与故事先后的环是错误；披露逆序可能是合法倒叙，不据此判失败。

## 人物呈现与内部身份

所有参与者、在场者、状态持有者、叙述者、视点、观察者统一为
`ActorRef {char_id, presentation_ref?}`。`presentation_ref` **只引用本章**
`actor_bindings` 的局部句柄；历史章正式 ID（如 `ap05_01`）不得作为本章引用。
编译器在 `char_id` 合法时把历史句柄确定性压成 `null`（0 LLM），不按全书目录
碰巧解析。需要特定表面身份时在本章新建 binding。
`presentation_ref` 缺失、错归属或同章多呈现未指定时，保留错误交 owner 免费订正。
逐章 ActorRef 闭集来自 Blueprint 称名投影：已到 `first_chapter` 的实体即使登记名
尚未字面出现（常见第一人称「我」）也进入闭集，`surfaces` 可为空——可引用
`char_id`，不可伪造未证实的 `actor_bindings.surfaces`；裸代词走
`narration.pronoun_surfaces`。
唯一绑定允许省略 presentation_ref；晚章新增绑定不改写旧章已解析身份。
历史 State 按其原始使用章解析，后章新增绑定不改写此前默认身份。
entry_states 可附带历史绑定与证据供 Continuity / 定向修订阅读，但**不得**把历史
`presentation_id` 当作本章 ActorRef 句柄；本章引用只用 `char_id` 或本章新建 binding。
首次抽取并行时请求内 `entry_states` 为空，正式 entry/carry 由 Continuity Pass 缝合。
同 Beat 可以含同 char_id 的两个不同 presentation，不能先按 char_id 去重。

ActorBinding 保存 `presentation_id`、`char_id`、登记字面 `surfaces`、本章 `evidence_refs`
和可选 `public_state_ref`。普通已公开昵称、排行、头衔可属于同一个 binding；隐秘表面身份
必须分开。代码只检查登记归属、原文字面、闭集和 ID，不推断两个称谓在场景内是否已公开同人。
绑定不另存角色介绍；未揭示身份不能继承人物内部 role。公开 State 仍须遵循状态/知识端点许可。
第一人称代词归 narration.pronoun_surfaces；“我”不成为登记称谓。

BlueprintCharacterProjection v2 保存实际截止本章出现的人物候选及每项
`surface/identity_revealed_from`。canonical 未出现时不强制改称；相同字面涉及多个人物时
保留所有候选，人工按原文消歧（原 Commander 审阅已随 2026-09-15 角色退役）。首次出场尚未发生的实体不因共享字面被引入。
映射时点不禁止称谓本身出现；本章揭示不等于章首已知。

Narration 保存 `narrator/focalizer` ActorRef、`beat_refs` 与原文证据。
单条 narration 的空 beat_refs 明确表示整章；多条片段必须逐项给适用 Beat。
人物主体引用带 `actor`，关系主体引用带有向 `relation_actors.subject/object`。
引用的 `.key()` 包含所有表面身份，`.raw_subject_key()` 仅供内部命题查询，不能用于消费去重。
规则主体 ref 精确为 `wr_NN/vNN`。

Writer 使用独立 opaque actor_ref，不下发 char_id、反向映射或按共同真实身份分组。
跨章携入只带实际 State 及其绑定、原证据和必要公开 State 支持，不复制整个人物表。
同章两个表面身份保持独立引用，同人命题按绑定候选与章级揭示字段判定；
以前章节的 Writer 输出不能作为新的身份许可直接复制，须按当前公开边界重新投影。

## 响应与编译

响应使用 beat_key、state_key、constraint_key、presentation_key 和局部引用，证据只给
start_para/end_para。编译器分配正式 ID、共享证据表及坐标并核人物闭集。
历史修订使用单调 ID 游标，退役 ID 不复用。免费重验复用同一证据检查。
来源、段号、必要字面或引用错误明确拒绝，不能补写语义修复输入。

章节依赖清单绑定实际下发的材料及集合，不只保存主体 ID。
只为核验 Global 证据而引用另一章时，依赖该证据所用段落；无关段落变化不重开本章。
确实消费另一章完整候选或持续状态时，仍绑定相应候选/状态内容和证据，不能缩成 ID。
依赖摘要由同一投影生成，问题可以引用其中实际可见的外章证据。

## 语义审阅退役与依赖登记

原 KnowledgeLinks V3 与章级 Commander 的知识事件登记已随 Stage2 语义审阅整体退役
（2026-09）：`knowledge_links.event_operations`、reader_reveal/character_knowledge 事件、
identity_actors 许可端点及五维章审不复存在。共享主体的可见性只按 Global 章级揭示字段与
蓝图自身端点判断，同章安排由 Stage4 现有计划表达；依赖登记继续绑定实际下发的材料，
机械检查不宣称枚举了全部知识边界（`plot_semantics` unver 行披露）。

State 的未来读者披露会进入当前 PlannerTruth 与结构审阅材料，尚未开放的 State 正文
不进入此前章节 WriterVisible；当前章已开放的命题仍保持 belief/claim 等性质。
必要的跨章剧情正文在 background_beats 中只保存一次，不成为新的本章落地义务。

## 改编安排

PlotChapterArrangement 保存场景、唯一 beat_placements 和 anchor_bindings。
场景保留 slot、purpose、role、delivery、transition、context_beat_refs。
BeatPlacement 只有 beat_ref 与 deliveries；每项含 scene_ref、
presentation_instruction（整体呈现显式 null）和 evidence_refs。
同一节点同一场只出现一项；分场必须逐场说明呈现任务。
Delivery 不成为上游可引用的子事实，不保存子 claim。

PlotAnchorBinding 指定 kind/ref/endpoint/scene_ref。state 的端点为
established/ended，constraint 为 before/after/target，knowledge 为 trigger，
goal 为 open/close/turn/chapter。端点目录从既有主体派生；不能自由添主体。
单场自动定位；分场缺明确端点拒绝，不用首次/末次出现兜底。
所有主要呈现完成前，不能把整个节点作为背景引用。

A 只读去源序的 opaque 语义投影；证据和源舞台留给章级 B。
规划内部材料与 Writer 身份视图分开；Writer 只接受当前呈现许可和有据称谓。自由文本回显本书已登记正式 ID 时拒绝构包，
不裁剪正文，也不误拒未登记的编号字面。局部响应只接受本章下发的局部 alias。
opaque alias 使用冻结且不下发给模型的密钥，不能仅散列可枚举的源序编号；
跨章状态及 Global 引用也经过同一映射，不能从旁路暴露源序。
Writer 的章级剧情目录只保存父正文一次，各场引用该目录并带呈现指令。
逐场许可约束输出，不声称同一章级请求中后场秘密对模型物理不可见。
语义遗漏、重复、归因与隐含提前披露由既有设计、结构和成稿审阅负责。

结构计划的已编译引用若仍不闭合，由 PlotReferenceRefused 保留章归属：候选校验
转成 placement_ref 或 typed repair；正式消费中外溢到 HTTP 时显式归为 500 的结构错误，
不通过默认异常映射掩盖编译器缺口。缺上游或审阅未闭合仍分别使用已有 404/409。
结构页面从同一批计划、绑定、蓝图及必要 Global/原文材料编译身份，
不把只有计划正文的摘要冒充正式工作包；页面 subject 不是授权，提交仍核 freshness。
只读请求结束时复核盘面，期间变更拒绝返回拼接状态。

生成进度只读取 stage2_run_state 的运行记录/检查点载体，与 producer 共用摘要协议，
不导入模型执行模块。检查点 current 仅表示当前输入与载体一致，不代表 paid-response
恢复许可、章节审阅接受或整书放行；破损、代码变化和无法核验仍独立显示 unver。

PlotEdit v2 的 `bpe2_` token 绑定当前实际输入。actor_bindings 与 Beat/State/Constraint
同样经过显式 preserve/replace/add、单调 ID 和退役账；恢复重放原已购请求，不重新按列表序分配身份。

`relation_uses` 稀疏登记本章实际消费的 Global 关系及两个 ActorRef 端点，不为建立关系呈现而造 State。
同一关系使用不同表面身份时保留全部 use；正式方向必须匹配 Global，默认呈现只在唯一无歧义时解析。
未实际使用的关系不产生 Writer 呈现或事实许可；缺省呈现按候选判据解析，不能凭内部端点推定。
关系引用型秘密展开时须存在唯一实际关系 use，缺失或多解由 Blueprint owner 免费补齐。

当前 Blueprint 的人物删除、绑定缺失或歧义通过只读诊断保留原稿与问题，审阅 API 返回 409 并指出
免费编辑入口。编辑模板可保留尚未闭合的语义引用，提交候选仍须完整编译、HARD 和原文证据校验；
不能把诊断改成空结果、自动重抽或用虚构身份修补工作包。
