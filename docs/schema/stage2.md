# Stage 2 当前契约

Stage2 在新链（STR-06，F8a 激活）有两个付费节点：`str06.stage2.source`（book＋chNN
联合抽取与逐章 Blueprint v12 编译）与 `str06.stage2.names`（源名称结构窄调用，发布
Characters v4）。旧 `stage2.analyze`／`stage2.global`／`stage2.characters`／
`stage2.blueprint` 四节点及其 typed edit／revalidate 人工面随旧链退役；旧书只读拒识为
`legacy_incompatible`，不迁移、不手补。

源章原文只进 `str06.stage2.source` 自己的 prompt；下游一律消费编译产物（source-blind）。
语义未审以 unver 行常驻披露，不产生审批待办。机械检查只证明形状、引用与编译闭合，
不证明归因、因果或揭示的语义正确性。

## 章前文字（源引子）进本层

Stage6 退役后 `stage1/hook_copy.json` 的唯一消费者是本层：`chain.build_evidence` 把它切成
`e00_*` 证据段（`chapter=0`、`pre_chapter` 标记），书级调用与第 1 章抽取看得到，其余各章
看不到——引子只可能影响开场。**按信息职责分流，不对整篇打标签**：开场前已成立的状态、
关系、知情进 `initial_state`（知情范围按开场行动之前记录）；实际发生的开场行动、对话、
揭示过程进第 1 章最前面的 beats（`source_refs` 引 e00）；与第 1 章正文重复的同一事件以正文
为准、e00 不另建；后文预告、flash-forward、全书概括不进 canonical；判断不了弃权。三条不
变量：同一事件只有一个 canonical beat；由开场拍首次产生的事实或知情不得同时出现在
initial_state 中；删除纯摘要型引子后 canonical 语义应基本等价。

`source_fold` 另有两条**只报告、不阻断**的机械读数（落 `stage2/acceptance/fold.json`）：
引自 e00 的拍对初态同键 set 了同值；初态已知者又在引用同一 e00 证据段的拍里被告知。
语义正确性仍归 unver 披露与验收样本。

称谓闭合（wire 闸）：`canonical` 必须逐字等于该人物 `surfaces` 中的某一项；名称结构里
`surface_forms` 的键必须逐字等于人物账的 surface 集，正文出现而人物账没有登记的称谓不在
names 调用里私自加键（漏收是 book 级抽取的账）。

## 现役 JSON 产物

本层产物**都是 schema v1**（三级路径是切换时新建的命名空间，盘上没有旧信封字节；
形状破坏史由模型 `contract_version` 字面量与候选产物表的 RC 标签承载，见
`docs/BACKEND_CONTRACT.md` `R-2.5`／`R-5.11b`）。

| 路径 | 模型 | 生产节点 | 内容 |
|---|---|---|---|
| `stage2/compiled/book_extraction.json` | `CharactersCore` | `str06.stage2.source` | book 级联合抽取 core（正式派生产物；names／preserve 的单节点输入从盘面闭合） |
| `stage2/compiled/input_snapshots.json` | 自由形态派生记录 | `str06.stage2.source` | 逐章输入快照（确定性派生，同输入同快照） |
| `stage2/blueprints/chapter_blueprints.json` | `BlueprintsArtifactV12` | `str06.stage2.source` | 逐章 Blueprint v12（拍序、引用与状态键） |
| `stage2/acceptance/fold.json` | 自由形态派生记录 | `str06.stage2.source` | source fold 事件表（机械对账面） |
| `stage2/characters/characters.json` | `CharactersArtifactV4` | `str06.stage2.names` | Characters v4：core＋名称结构（人物闭集、家族、称谓面） |

`runtime/preserve_checkpoint.json` 与 Stage4 产物见 [stage4.md](./stage4.md)；
正文与 Hook 见 [stage5.md](./stage5.md)；命名与交付见 [stage7.md](./stage7.md)。
Stage1 `hook_copy.json`（章前文字）的消费者是 `str06.stage2.source`（切成 e00 证据段），
Hook 写手不读源引子。

## 消费投影

`novel/consumption.py::CONSUMED_PROJECTIONS` 登记本层两个节点的上游投影：
`str06.stage2.source` 消费正式 Stage1 `MainText`（阅读序与正文）与 `hook_copy`（章前文字），
`str06.stage2.names` 消费 book_extraction、MainText 与 hook_copy（字面校验含章前文字）。
投影统一为整份业务载荷（剥掉上游来源账）——新链的来源账边在读取时按完整字节指纹记录，任何上游字节变化
都如实过期。源身份是两份 Stage1 字节的组合摘要（`R-2.9`）：只改引子也换身份。

## 判据表

| 判据 | sev | 要求 |
|---|---|---|
| `source_book_extraction` | hard | book 级抽取编译闭合（无未知键、无悬空引用） |
| `source_canonical_surface` | hard | canonical 逐字等于该人物某个已登记 surface |
| `source_identity_covers_hook_copy` | hard | 源身份覆盖 main_text＋hook_copy 两份字节 |
| `source_chapter_dense_ordinals` | hard | 源章数组阅读序 1..N 密集且非空 |
| `source_chapter_blueprint` | hard | 逐章 Blueprint v12 编译闭合 |
| `source_fold_consistent` | hard | fold 事件与 Blueprint canonical 一致（含 e00 只读读数） |
| `source_fold_events` | metric | fold 事件计数与章分布读数 |
| `names_structure_compile` | hard | Characters v4 名称结构编译闭合 |
| `names_coverage` | hard | 应改名集合覆盖源名称结构（名称位一致） |
| `source_semantics_unver` | unver | 源语义未经语义审阅，保持未评披露 |

当前注册表共 **10 条**：**8 hard、1 metric、1 unver**。唯一注册表为
`contracts/judges/str06.py`（F8c 起分流至本层判据模块）；修复路由见各判据的 `fix`
字段——确定性节点默认重跑即 0 成本修复，付费节点重跑是新购，`fix` 留空。
