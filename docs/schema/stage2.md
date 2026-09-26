# Stage 2 当前契约

Stage2 在新链（STR-06，F8a 激活）有两个付费节点：`str06.stage2.source`（book＋chNN
联合抽取与逐章 Blueprint v13 编译）与 `str06.stage2.names`（源名称结构窄调用，发布
Characters v4）。旧 `stage2.analyze`／`stage2.global`／`stage2.characters`／
`stage2.blueprint` 四节点及其 typed edit／revalidate 人工面随旧链退役；旧书只读拒识为
`legacy_incompatible`，不迁移、不补。

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
| `stage2/compiled/input_snapshots.json` | 自由形态派生记录 | `str06.stage2.source` | 逐章输入快照（确定性派生，同输入同快照；v13 起逐条带来源与标识登记） |
| `stage2/blueprints/chapter_blueprints.json` | `BlueprintsArtifactV13` | `str06.stage2.source` | 逐章 Blueprint v13（三类转移、场景分段与因果边） |
| `stage2/acceptance/fold.json` | 自由形态派生记录 | `str06.stage2.source` | source fold 读数表（事件＋每章空转移拍比例＋书末未揭示 concealed） |
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
| `source_surface_anchor_literal` | hard | 称谓 from_chapter 由证据锚点派生：锚点段逐字包含该 surface，章前锚点映射第 1 章 |
| `source_identity_covers_hook_copy` | hard | 源身份覆盖 main_text＋hook_copy 两份字节 |
| `source_chapter_dense_ordinals` | hard | 源章数组阅读序 1..N 密集且非空 |
| `source_chapter_blueprint` | hard | 逐章 Blueprint v13 编译闭合（三类转移、场景分段、闭集与因果边） |
| `source_loop_lifecycle` | hard | loops 只许 open/close：loop 一次性 open、close 必带 resolution、问题文本不可改写 |
| `source_proposition_closure` | hard | 命题引用闭合：本地标签/正式 ID 可解析，重抄文本与登记逐字一致 |
| `source_effect_disclosure` | hard | effect 可见性形状：必填 on_page/concealed；concealed 必声明已知者并带命题锚 |
| `source_registered_key_scope` | hard | 登记键闭集与作用域：表内键作用域由 compiler 绑定（scene/residence/身体状态键）；退役位置键拒识 |
| `source_fold_consistent` | hard | fold 事件与 Blueprint canonical 一致（含 e00 只读读数） |
| `source_fold_events` | metric | fold 事件计数与章分布读数 |
| `source_fold_concealed_unrevealed` | metric | 书末仍未向读者确认的 concealed 命题（只读报告） |
| `source_fold_empty_transition_ratio` | metric | 每章空转移拍比例（三类转移全空的拍所占比例） |
| `source_fold_knowledge` | metric | 知情 fold 读数（按命题＋持有者＋模式；读者确认时点） |
| `names_structure_compile` | hard | Characters v4 名称结构编译闭合 |
| `names_coverage` | hard | 应改名集合覆盖源名称结构（名称位一致） |
| `source_semantics_unver` | unver | 源语义未经语义审阅，保持未评披露 |

当前注册表共 **18 条**：**13 hard、4 metric、1 unver**。唯一注册表为
`contracts/judges/str06.py`（F8c 起分流至本层判据模块）；修复路由见各判据的 `fix`
字段——确定性节点默认重跑即 0 成本修复，付费节点重跑是新购，`fix` 留空。

## v13 拍模型（质量改进批次二）

每一拍是一次状态转移：`event` 是给人读的事件表述；需要延续的变化全部由三类转移承载——
`effects`（叙述确立的事实；必填 `scope` 与 `disclosure`，concealed 须声明 known_by 并带
命题锚）、`cognition`（谁对哪条命题持什么模式；holder 为人物 id 或 reader/public，
mode 为 learns/suspects/confirms/hears_claim/believes/asserts）、`loops`（悬念；op 只有
open/close，close 必带 resolution）。拍上另带 `scene_boundary`（new/continue）与
`temporal_mode`（single/iterative）；章蓝图带 compiler 生成的 `scenes` 分段（跨章场景在
续章同样出现）。命题与 loop 的正式 ID（kNN／loopNN）由 compiler 生成：wire 内用本地标签、
跨章用 prompt 列出的正式 ID 引用；新建命题保留 statement，引用不重抄文本。

位置分轴（登记键闭集，`contracts/schema/source.py::REGISTERED_STATE_KEYS`）：
`scene_location`（scene 作用域，当前所在，换场即失效）、`residence`（book 作用域，长期
居所，换场不清除）与身体状态键（各自一键，书作用域）；`whereabouts`／`hiding_place`／
`location` 已退役（`RETIRED_STATE_KEYS`）。章请求带前文原文前缀（第 1..k-1 章，只读、
不带证据 id）与带来源快照；快照是参考与标识登记，正文优先（CHAPTER_PROMPT 要求 7）。
