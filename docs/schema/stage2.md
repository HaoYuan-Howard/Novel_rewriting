# Stage 2 当前契约

Stage2 在新链（STR-06）按语义单元与作用域拆**五个节点**（B2 五节点重建，2026-09-27
裁决 D3/D8/D9）：`str06.stage2.book`（书级观察）、`str06.stage2.chapters`（逐章观察，
并行）、`str06.stage2.names`（源名称结构观察）、`str06.stage2.associate`（跨章关联，
付费）与 `str06.stage2.compile`（确定性编译，免费）。联合抽取节点 `str06.stage2.source`
退役；派生输入 `stage2/compiled/book_extraction.json` 与
`stage2/compiled/input_snapshots.json` 退役（旧根只读保留，新协议下新根全量重购，
I-13 无转换层）。旧 `stage2.analyze`／`stage2.global`／`stage2.characters`／
`stage2.blueprint` 四节点随旧链退役；旧书只读拒识为 `legacy_incompatible`，不迁移、不补。

源章原文只进观察节点自己的 prompt；下游一律消费编译产物（source-blind）。语义未审以
unver 行常驻披露，不产生审批待办。机械检查只证明形状、引用与编译闭合，不证明归因、
同一性、因果或揭示的语义正确性——关联误并/漏并的风险接受到 B6 与 v13 对照读数
（DECISIONS D3/D8）。

## 观察与关联协议（B2；R1 associate 独立版本）

book／chapters 两个付费观察面与名称结构观察共用协议版本字面量
`stage2-observation-v1`；associate 用**独立版本** `stage2-associate-v2`
（R1，2026-09-28；`contracts/schema/observation.py`；进请求身份与盘面信封，
升版＝换字面量＋改模型＋登记，不做运行时转换层）。R1 只升 associate：typed
作用对象显式序列化（人物带 `char_id`、关系保留主客体方向、world 是显式类型，
不拿空 dict 兜底）＋状态句柄位置编码——三类观察的请求与字面量逐字不变。

**句柄编码（R1 v2：唯一的关联登记表）**：`AssociationSpace`
（`novel/stages/stage2/associate.py::project_association_space`）是 prompt、
闭集核对与 compile（`_StateClasses` 确定性序、初态与章效应句柄定位）共用的
唯一登记表；构建时重复句柄即拒。拍 `ch{nn}.b{拍序}`、命题/悬念
`ch{nn}.{本地 ref}`、状态 `ch{nn}.b{拍}.e{效应序}.{key}`／`book.e{条目序}.{key}`
（位置编码：key 只是载荷，不承担唯一性；book 初态无拍位，独立命名空间）、
场景 `ch{nn}.sc{序}`。

**模型输出面去机械化（I-20/I-21）**：协议里没有拍号、状态/约束编号、命题/loop/场景
正式 ID、章序、offset、hash——一律由 compiler 生成；人物 slug 仍由模型给出（可读
语义句柄，无法机械推导）。

| 观察 | 节点 | 内容 | 去机械化要点 |
|---|---|---|---|
| `BookObservation` | `str06.stage2.book` | 人物候选（可读 slug、称谓及证据句段）、初态、世界约束 | 无 `state_id`／`constraint_id`；surface 无 `from_chapter`（`R-2.10` 锚点派生） |
| `ChapterObservation` | `str06.stage2.chapters` | 有序事件＋三类转移＋场景边界＋时间模式＋证据 | 无 `beat_id`（列表序即事件序）、无章序、无因果边；只含**章内本地句柄**（D3） |
| `AssociateObservation` | `str06.stage2.associate` | 同一命题、同一状态维度、悬念开合配对、跨章因果、跨章续场 | **只选已给句柄**（compiler 投影闭集）；只建立同一性与引用，不移动事实成立、知情或揭示时点（D8） |
| `NamesObservation` | `str06.stage2.names` | 源名称结构（家族、名、化名、称谓构词） | 形状与 v4 `NameStructure` 同源（D9「names 不变」） |

转移语义逐字复用 v13 拍模型（`contracts/schema/source.py` 的 `Effect`／`Cognition`／
`LoopAction`；I-11 同一领域模型不复制）：三类转移、可见性（concealed 必声明 known_by
并带命题锚）、认知模式闭集、登记键作用域、悬念开闭形状不变。命题/loop 用章内本地
句柄（wire `ref`，不进 canonical）：新建带 statement，引用不带文本；同拍先建后引合法，
章间不引用——跨章同一性归 associate，配不上即未决，compile 拒绝发布并按 item 报告，
恢复走 owner patch 或 associate 定向修订。

**Resolver**（`novel/stages/stage2/resolver.py`）：证据定位的唯一实现——句段 id 闭集
选择；逐字锚（称谓、原话）在所引句段内唯一定位；重复（ambiguous_literal）、不存在
（missing_literal）、闭集外引用（unknown_segment）产生精确 issue（带 code/context），
不模糊匹配、不猜笔误；称谓 `from_chapter` 锚点派生沿用 `R-2.10`（最早逐字包含段，
e00 映射第 1 章）。

## 章前文字（源引子）进本层

Stage6 退役后 `stage1/hook_copy.json` 的唯一消费者是本层：切成 `e00_*` 证据段
（`chapter=0`、`pre_chapter` 标记）进书级观察与第 1 章观察，其余各章看不到——引子只
可能影响开场。**按信息职责分流，不对整篇打标签**：开场前已成立的状态、关系、知情进
初态（知情范围按开场行动之前记录）；实际发生的开场行动、对话、揭示过程进第 1 章最前
面的拍；与第 1 章正文重复的同一事件以正文为准、e00 不另建；后文预告、flash-forward、
全书概括不进 canonical；判断不了弃权。三条不变量：同一事件只有一个 canonical 拍；
由开场拍首次产生的事实或知情不得同时出现在初态中；删除纯摘要型引子后 canonical 语义
应基本等价。

`source_fold` 另有两条**只报告、不阻断**的机械读数（落 `stage2/acceptance/fold.json`）：
引自 e00 的拍对初态同键 set 了同值；初态已知者又在引用同一 e00 证据段的拍里被告知。
语义正确性仍归 unver 披露与验收样本。

称谓闭合（wire 闸）：`canonical` 必须逐字等于该人物 `surfaces` 中的某一项；名称结构里
`surface_forms` 的键必须逐字等于人物账的 surface 集，正文出现而人物账没有登记的称谓不
在 names 调用里私自加键（漏收是 book 观察的账）。

## 现役 JSON 产物

观察产物自 B2 起挂协议模型（`protocol_version` 字面量即版本信封）；canonical 形状版本
口径不变（`docs/BACKEND_CONTRACT.md` `R-2.5`／`R-5.11b`）。

| 路径 | 模型 | 生产节点 | 内容 |
|---|---|---|---|
| `stage2/observations/book.json` | `BookObservation` | `str06.stage2.book` | 书级观察：人物候选（可读 slug、称谓及证据）、初态、世界约束 |
| `stage2/observations/ch{nn}.json` | `ChapterObservation` | `str06.stage2.chapters` | 逐章观察：有序事件＋三类转移＋场景边界＋时间模式＋证据（章内本地句柄） |
| `stage2/observations/names.json` | `NamesObservation` | `str06.stage2.names` | 源名称结构观察（`NameStructure`） |
| `stage2/observations/associate.json` | `AssociateObservation` | `str06.stage2.associate` | 跨章关联决定：同一命题／同维状态／悬念配对／跨章因果／跨章续场（只选已给句柄；R1 起协议 `stage2-associate-v2`、形状 `str06-rc9`） |
| `stage2/blueprints/chapter_blueprints.json` | `BlueprintsArtifactV13` | `str06.stage2.compile` | 逐章 Blueprint（三类转移、场景分段与因果边；compile 唯一 writer） |
| `stage2/acceptance/fold.json` | 自由形态派生记录 | `str06.stage2.compile` | source fold 读数表（事件＋每章空转移拍比例＋书末未揭示 concealed） |
| `stage2/characters/characters.json` | `CharactersArtifactV4` | `str06.stage2.compile` | Characters v4：core＋名称结构（compile 合并 book 与 names 观察后唯一发布，D9） |

已退役：`stage2/compiled/book_extraction.json`（联合抽取派生 core；由 compile 直接发布
Characters 双真源取代）、`stage2/compiled/input_snapshots.json`（逐章输入快照；D3 章观察
互不依赖后不再有快照）。两者只读拒识，随旧根保留。

`runtime/preserve_checkpoint.json` 与 Stage4 产物见 [stage4.md](./stage4.md)；
正文与 Hook 见 [stage5.md](./stage5.md)；命名与交付见 [stage7.md](./stage7.md)。

## 消费投影

`novel/consumption.py::CONSUMED_PROJECTIONS` 登记本层五个节点的上游投影：
`str06.stage2.book` 消费正式 Stage1 `MainText` 与 `hook_copy`；`str06.stage2.chapters`
消费 book 观察与 `MainText`（本章句段＋前文原文，互不依赖）；`str06.stage2.names` 消费
book 观察与全文（字面校验含章前文字）；`str06.stage2.associate` 消费 book 与 chapters
观察；`str06.stage2.compile` 消费四份观察。Stage4 一律消费 canonical
（`stage2/characters/characters.json`＋`stage2/blueprints/chapter_blueprints.json`），
不再有派生输入面。投影统一为整份业务载荷（剥掉上游来源账）——新链的来源账边在读取时
按完整字节指纹记录，任何上游字节变化都如实过期。源身份是两份 Stage1 字节的组合摘要
（`R-2.9`）：只改引子也换身份。

## 判据表

| 判据 | sev | 要求 |
|---|---|---|
| `source_book_observation` | hard | book 观察编译闭合（无未知键、无悬空引用、slug 闭集自洽） |
| `source_canonical_surface` | hard | canonical 逐字等于该人物某个已登记 surface |
| `source_surface_anchor_literal` | hard | 称谓 from_chapter 由证据锚点派生：锚点段逐字包含该 surface，章前锚点映射第 1 章（Resolver 唯一实现） |
| `source_identity_covers_hook_copy` | hard | 源身份覆盖 main_text＋hook_copy 两份字节 |
| `source_chapter_dense_ordinals` | hard | 源章数组阅读序 1..N 密集且非空 |
| `source_chapter_blueprint` | hard | 章观察编译闭合（三类转移、场景分段、闭集；章观察只含章内本地句柄） |
| `source_loop_lifecycle` | hard | loops 只许 open/close：loop 一次性 open、close 必带 resolution、问题文本不可改写 |
| `source_proposition_closure` | hard | 命题引用闭合：章内本地句柄可解析、新建带 statement、引用不重抄文本；跨章同一性配对归 associate |
| `source_effect_disclosure` | hard | effect 可见性形状：必填 on_page/concealed；concealed 必声明已知者并带命题锚 |
| `source_registered_key_scope` | hard | 登记键闭集与作用域：表内键作用域由 compiler 绑定（scene/residence/身体状态键）；退役位置键拒识 |
| `source_fold_consistent` | hard | fold 事件与 Blueprint canonical 一致（含 e00 只读读数） |
| `source_fold_events` | metric | fold 事件计数与章分布读数 |
| `source_fold_concealed_unrevealed` | metric | 书末仍未向读者确认的 concealed 命题（只读报告） |
| `source_fold_empty_transition_ratio` | metric | 每章空转移拍比例（三类转移全空的拍所占比例） |
| `source_fold_knowledge` | metric | 知情 fold 读数（按命题＋持有者＋模式；读者确认时点） |
| `names_structure_compile` | hard | Characters v4 名称结构编译闭合 |
| `names_coverage` | hard | 应改名集合覆盖源名称结构（名称位一致） |
| `source_semantics_unver` | unver | 源语义与关联决定未经语义审阅，保持未评披露 |

当前注册表共 **18 条**：**13 hard、4 metric、1 unver**。唯一注册表为
`contracts/judges/str06.py`（F8c 起分流至本层判据模块）；修复路由见各判据的 `fix`
字段——确定性节点默认重跑即 0 成本修复，付费节点重跑是新购，`fix` 留空。

## 拍模型与登记键（v13 延续，B2 起为观察面）

每一拍是一次状态转移：`event` 是给人读的事件表述；需要延续的变化全部由三类转移承载——
`effects`（叙述确立的事实；必填 `scope` 与 `disclosure`，concealed 须声明 known_by 并带
命题锚）、`cognition`（谁对哪条命题持什么模式；holder 为人物 id 或 reader/public，
mode 为 learns/suspects/confirms/hears_claim/believes/asserts）、`loops`（悬念；op 只有
open/close，close 必带 resolution）。拍上另带 `scene_boundary`（new/continue）与
`temporal_mode`（single/iterative）；章蓝图带 compiler 生成的 `scenes` 分段（跨章场景在
续章同样出现）。命题与 loop 的正式 ID（kNN／loopNN）由 compiler 生成：观察 wire 内用
章内本地句柄；跨章同一性由 associate 配对后由 compiler 授予唯一正式 ID。

位置分轴（登记键闭集，`contracts/schema/source.py::REGISTERED_STATE_KEYS`）：
`scene_location`（scene 作用域，当前所在，换场即失效）、`residence`（book 作用域，长期
居所，换场不清除）与身体状态键（各自一键，书作用域）；`whereabouts`／`hiding_place`／
`location` 已退役（`RETIRED_STATE_KEYS`）。章观察带本章句段与第 1..k-1 章原文（只读、
不带证据 id，D3：无快照、无登记表）；正文优先。
