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
opaque alias 使用冻结且不下发给模型的密钥，不能仅散列可枚举的源序编号；
跨章状态及 Global 引用也经过同一映射，不能从旁路暴露源序。
Writer 的章级剧情目录只保存父正文一次，各场引用该目录并带呈现指令。
逐场许可约束输出，不声称同一章级请求中后场秘密对模型物理不可见。
语义遗漏、重复、归因与隐含提前披露由既有设计、结构和成稿审阅负责。
