# 完整剧情与呈现契约（plot-blueprint-v1）

本契约使用完整剧情节点，替换逐谓词事实目录。普通分场不改变 Stage2
语义主体；真实漏抽、错抽或必要知识边界缺失才修订上游。

## 正式蓝图

PlotBlueprint 保存章号、源文 SHA-256、chapter_function、beats、
evidence_anchors、states、inherited_state_endings、constraints、entry_state_refs、
carry_out_refs 和 narration。

PlotBeat 只在 content 保存完整剧情，另含稳定 beat_id、参与人物、
evidence_refs、source_contexts。SourceContext 只有证据、时层、地点、
实际在场者；null 表示未知，不能从参与者推断在场。

EvidenceAnchor 由段号响应编译，含证据 ID、起止段及零起点半开字符区间。
相同段范围去重；原文只在源产物保存。证据覆盖和重叠不是语义通过判据。

PlotState 保存 state_id、content 或 global_ref（二选一）、modality、
holder_char_id、established_at、ended_at、supersedes_state_refs、evidence_refs。
modality 为 world、belief、claim、unresolved；belief/claim 明确持有者。
状态性质不描述传播权限。成立可在章首或节点，解除须有节点锚点。
章间状态复用原 ID；章末不能携出已结束状态，不复制上章正文。
上章携入的状态在本章解除时，inherited_state_endings 只保存 state_ref、
at_beat_ref 和 evidence_refs，不回写旧章正文。被取代状态的结束锚点由新状态的
成立锚点导出；同一状态不能重复解除、重复取代或在结束后继续携出。

约束采用关闭式联合。cause/story_before/response_to/same_scene/observes
保存两端节点，observes 另含观察者；literal/value/object 保存目标节点及必要值。
所有约束有稳定 ID 和证据，节点内部普通关系归 content，不生成自环。
因果与故事先后的环是错误；披露逆序可能是合法倒叙，不据此判失败。

## 响应与编译

响应使用 beat_key、state_key、constraint_key 和局部引用，证据只给
start_para/end_para。编译器分配正式 ID、共享证据表及坐标并核人物闭集。
历史修订使用单调 ID 游标，退役 ID 不复用。免费重验复用同一证据检查。
来源、段号、必要字面或引用错误明确拒绝，不能补写语义修复输入。

章节审阅与知识关联的依赖清单绑定实际下发的材料及集合，不只保存主体 ID。
只为核验 Global 证据而引用另一章时，依赖该证据所用段落；无关段落变化不重开本章。
确实消费另一章完整候选或持续状态时，仍绑定相应候选/状态内容和证据，不能缩成 ID。
工作包与依赖摘要由同一投影生成，问题可以引用该包中实际可见的外章证据。

## 稀疏知识登记

KnowledgeLinks V2 保留 Global 的必要知识候选；State 的存在不自动生成读者披露。
章级 Commander 在同轮 knowledge_links.event_operations 显式 put 或 retire 必要事件，
不增加固定模型工位。put 引用已有 Beat/State，kind 为 reader_reveal 或
character_knowledge；后者带受众。证据给 chapter_ordinal/evidence_ref，代码核对本轮
实际原文并规范为段范围；证据章、命题来源章与事件目标章可以不同。

put 同时给 at_beats 关联或 unresolved 原因。触发节点属于目标章，支持引用包含完整命题；
缺必要知识边界仍由五维语义审阅登记问题，不能声称机械枚举了全部知识。
完整 Beat 的读者呈现不能借知识登记跨章移动；跨章人物得知既有剧情可以独立登记。
retire 必须给理由。稳定 event_id 与 expected_event_sha256 防止覆写错误版本，
原始请求和代码物化的 knowledge_application 同时留在审批审计中，退役身份不复用。
注册、修改与撤销按实际消费集合和内容使相关范围过期，不清除既有问题。

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
引用人物的 alias 同时带 canonical、可辨认称呼及 role；规划者可以辨认未来人物，
Writer 的称呼仍按原文证据开放。自由文本回显本书已登记正式 ID 时拒绝构包，
不裁剪正文，也不误拒未登记的编号字面。局部响应只接受本章下发的局部 alias。
opaque alias 使用冻结且不下发给模型的密钥，不能仅散列可枚举的源序编号；
跨章状态及 Global 引用也经过同一映射，不能从旁路暴露源序。
Writer 的章级剧情目录只保存父正文一次，各场引用该目录并带呈现指令。
逐场许可约束输出，不声称同一章级请求中后场秘密对模型物理不可见。
语义遗漏、重复、归因与隐含提前披露由既有设计、结构和成稿审阅负责。
