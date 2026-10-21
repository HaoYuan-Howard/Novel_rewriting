# Stage4：目标建模层

依赖顺序为 naming → preserve → body_design → chapters → compile → hook_design → hook_compile。PreservationContract决定必保内容和有记录的局部一致化；TargetModel先建立全书事件、人物变化、认知与因果，逐章计划再展开场景和行动。免费compile唯一发布TargetStory、AdaptationMap和写手章包，Hook单独设计、单独编译。

章号与源章号一一对应。关键身份、结局、骨架和受保护的信息差不得由写手改变。起始状态、起始认知和称谓释放必须显式，未来答案不得作为guard_only泄漏给写手。写手材料按must_realize、must_respect、continuity、creative_guidance、background、guard_only分区。

源世界规则逐项选择keep/adapt/omit。keep和adapt都由Preserve用目标称谓提交target_statement；keep保留规则机制，adapt另说明改造理由。omit只说明省略理由。compiler不复制带源名称的规则，也不按字面猜名称指代。

持续状态以主体、维度、限定对象和modality共同定位。actual与planned等模态分别占槽；计划不能覆盖实际状态。unset只解除同模态槽，unknown明确保留该槽的未知值；过去事件不倒写当前状态。

事件时态、状态模态与操作、因果/状态/认知/揭名等语义集合必须显式给出；遗漏不能由默认值补成current、actual或合法空。

chapters语义owner在WriterContext中按本章许可称谓、认知与揭示时点表达安全提示，引用正式状态快照、认知、世界规则和本章保存要求。章包只投影这些局部提示和完整演出行动；compiler不按字面替换名字，也不自动复制全书事件、风格、规则、状态值、命题或约束原文。Hook同理由hook_design输出带事件引用的beats与局部表达。自然提示的语义忠实度保持unver，不能把引用闭合冒充无泄漏的语义证明。

正式模型在 `contracts/schema/target_story.py`，纯编译入口在 `novel/stages/stage4/story.py`。引用、ID、章序和整数预算由compiler派生；源事实缺陷回派Stage2，章计划越界回派整书建模。详细交接见 [三层规格](str06.md)。

Preserve的SourceFactIssue必须给source_ref、相对该正式记录的path与replace/remove/append操作。正式字段的唯一writer由共享契约确定；运行时重编译真实Book/Chapter/Names输入后映射回已购raw路径。Book命题即使只有单章证据仍回book，事件summary回对应章；不按证据章推测归属。无行ID的causal_links、exposures、conflicts、expression仅在SourceFactIssue中使用保留selector，不扩大保存要求的source_refs。remove只删除明确授权的原生语义字典条目，不能压缩身份列表；派生字段或无法证明唯一writer的关联结果停止要求明确范围。

章规划报告骨架缺口时，TargetModelIssue显式给event_no、component、operation与item_no。事件号只引用本章事件；propositions、initial_state、initial_knowledge等全书组件显式event_no=null。replace必须定位既有条目，append显式保留原位置并在尾部增配。新命题及其知情/状态/因果前提可用多项typed问题共同回派；整书owner只提交授权补丁，完整compiler重新核对保真、引用与跨章条件。无法定位的结构变化停止要求明确范围。

## 当前版本与判据

本层正式故事产物**都是 schema story-v3**，旧根只读归档，须在新根由当前producer重建。

当前注册表共 **6 条**：**6 hard**。

| 判据 | 级别 | 职责 |
|---|---|---|
| `story_target_chapters` | hard | 事件恰分配一次且章集合等于源章集合，局部展开不得制造跨章事实 |
| `story_target_realization` | hard | 必保约束有目标承载，依赖有序且已声明因果与时间图无环 |
| `story_target_knowledge` | hard | 知情读取按持有者与模式成立，确知不得指向已否定事实 |
| `story_packet_projection` | hard | 章/Hook 包只投影授权动作、知情与显示名，六分区形状闭合 |
| `story_artifact_seal` | hard | 正式产物有覆盖当前字节的封存证明，损坏或未封存拒读 |
| `story_target_naming` | hard | 目标命名覆盖源人物，名称组成、保留理由与家族对应关系闭合 |
