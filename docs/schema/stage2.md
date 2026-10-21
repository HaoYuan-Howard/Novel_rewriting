# Stage2：事实理解层

依赖顺序为 chapters → book → associate → names → compile。chapters只看本章及获准前文，book整合全书，associate只处理具体未决，names只解析已确定实体的姓名构词。免费compile唯一发布SourceStory。

人物存在、原文指称、认知和读者披露分开表达。猜测不变成事实，未具名人物可以在正式姓名出现前参与事件。状态槽以主体、属性与具体事项区分；回忆和计划不覆盖当前实际状态。原文证据段由compiler定位，语义支持程度保持unver。

正式模型在 `contracts/schema/source_story.py`，纯编译入口在 `novel/stages/stage2/story.py`，材料查询在 `novel/core/source_index.py`。章事件漏抽或错抽按source_issue回派对应章观察，book不得暗改其动作。详细交接见 [三层规格](str06.md)。

章事件与局部指称、全书各身份表均使用明确语义键；compiler以源身份、领域和该键派发确定性句柄。
修改同键的字段不改变身份，增删或重排其它条目不使未改条目重编号。阅读位置由证据顺序独立派生，
不兼任身份或故事时间。语义换键表示显式新身份，compiler不从文本相似度猜测同一性。

## 全书综合的冻结与窄修

首次book综合即使回报source_issue，已购完整BookWire、章观察快照及原响应trace也会耐久保存。
章owner修复后，由 `book_repair_scope` 按显式引用和证据计算受影响条目闭包；未改变问题章时
不新增book调用。`book_repair_prompt` 交给同一book owner，只提交带明确path的修订。
`BookRepairWire` 使用 `kind: book_repair`，changes逐项带operation(set/remove)、value、reason与evidence_refs。
越过闭包须显式声明有证据的扩大，不能改成又一次全书综合；合并后仍完整通过compile_book。

`runtime/source_repairs/{book_request,book_candidate,book}.json` 保留输入身份、候选、修订权限和历史。
每步都能从本根当前WorkUnit耐久原响应重建，不能用自报hash或最后一个patch冒充全书理解。
正式book采用初次综合与窄修的真实响应引用；断电、重新开进程及完整只读重跑不重复购买。
章节当前接受的修订请求也冻结输入身份，防止从头重放时重新采用原来的坏答。

## 当前版本与判据

本层正式故事产物**都是 schema story-v1**，旧根只读归档，须在新根由当前producer重建。

当前注册表共 **7 条**：**6 hard、1 unver**。

| 判据 | 级别 | 职责 |
|---|---|---|
| `story_source_contract` | hard | SourceStory 当前版本、闭合字段与非空章集合；缺失、旧版与未知字段拒绝 |
| `story_source_identity_evidence` | hard | 源身份与逐字证据必须匹配当前 SourceIndex |
| `story_source_references` | hard | 实体/事件/命题/认知引用闭合，误信与世界真值、读者确认分开 |
| `story_source_state_address` | hard | 状态地址按主体、方面与具体焦点唯一，拒绝重址和悬空引用 |
| `story_source_threads` | hard | 整合后线程有序开合，终止后不得继续操作 |
| `story_names_structure` | hard | 名称结构覆盖全人物实体，名称组成与原文字面可机械还原 |
| `source_semantics_unver` | unver | 原作理解的完整性、因果归因与命题粒度未经语义验收 |
