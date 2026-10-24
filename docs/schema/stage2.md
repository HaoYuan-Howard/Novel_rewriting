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

rc27 的开场知识正式归 CharactersCore.initial_knowledge：每项包含
knowledge_id（k0_NN，代码派发）、holder、闭合的 proposition、polarity 与 basis。
Book 的 known_at_start 声明须有同 holder、同 polarity 的 confirmed 证据；证据文本
出现章节不作为知识产生时间。Preserve 可选 k0_*，Brief 分开初始知识锁与逐次变化，
必保初始知识须在 Design.initial_state 以正／负命题承载，后文补获知不能代替。
fold 从正式 Characters 开场基线起步；Book 观察不是下游的第三份真源。

rc27 的 book／chapters canonical 协议为 `stage2-book-v8`／`stage2-chapters-v8`，
对应模型 wire 为 `stage2-book-v11`／`stage2-chapters-v11`；
names 为 `stage2-names-v5`、wire 为 `stage2-names-v7`。每个人物显式给出
`additional_surfaces`（可为空），仅补充有源段证据的称谓，不增删或拆合人物；唯一
compiler 合入 Characters，Book 原观察不改。旧 canonical v4 的集中只读适配保留
原身份与字节形状，不补造缺失称谓证据；
associate 用**独立版本** `stage2-associate-v5`
（R1，2026-09-28 起独立；CS-H H4，2026-10-06 升 v3；CS-S Phase 2，2026-10-07
升 v4；CS-S Phase 3，2026-10-08 升 v5；`contracts/schema/observation.py`；进请求
身份与盘面信封，升版＝换字面量＋改模型＋登记，不做运行时转换层）。R1 只升
associate：typed 作用对象显式序列化（人物带 `char_id`、关系保留主客体方向，
world 是显式类型，不拿空 dict 兜底）＋状态句柄位置编码。v4（CS-S Phase 2，
S2-9 落地）：**条目级窄 resolver**——输入不再是整书投影，而是代码给出的未决
候选＋最小文本（近义命题对＝书级 facts 词表近义/跨章近义/悬空引用×更早章命题；
跨章 open×close 且时序正；相邻章 seam），逐候选恰答一次 verdict，无候选＝零
调用；跨章因果独立窄调用 `stage2-cross-cause-v1`（D-2 A：候选＝loop 配对拍对＋
facts 流拍对，模型只选 kind 或否决）。**v5（CS-S Phase 3）：`same_state_dimension`
消失**——change 键入 `CHANGE_KEYS` 闭集（键即 canonical，「键不同但同维」构造上
不可能），维度句柄（`dim{nn}`）与整类合并面（候选生成、`_StateClasses` 规范键
传播、`dimension_of` 反查）整体退役；v3/v4 wire 关闭式拒读。

**人物身份与称谓（rc27）**：人物候选与 Characters 条目显式携带
`identity_bindings:[{description,appearance_refs,identity_refs}]`，空数组是合法明确值。
Book 判断语境指称与人物的同一性；compiler 只核对人物、证据与引用闭合。
人物可以在姓名出现前匿名参与事件，不能用最早称谓章拒绝更早的内部人物 ID。
“孩子”等同字面指称可属于不同人物，不做全书唯一通用别名；身体、血缘事实保留
实际主体。姓名窗口仍由 surface 证据派生，只约束称谓；身份与姓名何时被谁知道归
认知转移。出场章统计从章观察参与者／认知持有者派生。章节人物材料只投影本章相关
指称，不把后文同一性证据送成当前已知。

**书级原子命题词表（rc27）**：canonical 为
`facts:[{fact_id,statement,evidence:[{kind,polarity,holder,basis}],known_at_start:[{holder,polarity}]}]`。
kind 取 `confirmed/claim/inference`，polarity 取 `positive/negative`，证据 holder
可为人物 slug 或 null；wire 每项证据以 `evidence_refs` 代替 basis，引文由代码切段。
F 编号 `f{nn}` 按列表序派发，不进 wire。一个命题只承载可独立确认或否定的主张，
病因、送来者、运输方式不能无据合成一句；词表引用只说明在讨论同一命题，不赋予真实性。
编号复用／associate 的 same 须完整语义等价，部分支持、包含、因果关系及否定不得合并。
原子性、证据性质和语义同一性由语义单元负责，compiler 不按连接词拆句或判真伪。

`known_at_start` 只表达第 1 章第一拍开始时已确认的正／负知识，持有者只收人物
slug；e00 中已完成告知可进入基线，后置预告不收。章 wire 的 fact 与 what 恰写其一，
canonical 的 `proposition.ref=f{nn},statement=None` 由 compiler 回填文本。
本章词表只携带本章相关证据性质，后章确认／反证不倒灌本章确知；fold 保留逐次认知
流水和初始基线，属于只读度量。

**句柄编码（R1 v2：唯一的关联登记表）**：`AssociationSpace`
（`novel/stages/stage2/associate.py::project_association_space`）是 prompt、
闭集核对与 compile 共用的唯一登记表。拍 `ch{nn}.b{拍序}`、命题/悬念
`ch{nn}.{本地 ref}`、场景 `ch{nn}.sc{序}`。可配对 open／close 闭集
（`pairable_opens`／`pairable_closes`）对称排除章内已自开自闭的悬念句柄——
提示词不列、wire 引用即带「已在章内自开自闭」说明的精确 issue；open 端
2026-10-08 补齐（真实书实证：章内已闭环 open 进跨章候选被模型答 pair 后
compile 必判重复 close、烧尽重问轮——close 端防了 open 端没防是候选生成
缺陷，不是模型缺陷）。

**（已删除，CS-S Phase 3）维度句柄面**：v3 的 `dim{nn}` 聚合层与 v4 的
`same_state_dimension` 候选已随 `CHANGE_KEYS` 键闭集整体退役——键即 canonical，
无「键不同但同维」可配；状态写入句柄（`ch{nn}.b{拍}.e{效应序}.{key}`／
`book.e{条目序}.{key}`）与 `states`／`dimensions` 登记面同批删除（S-05）。

最终组装的关联观察由
`novel/stages/stage2/associate.py::associate_observation_issues` 重算当前候选分母：
身份与因果各候选恰有一次判定，因果的 `none` 同样保留为已答证明；无候选且无答案
是合法零调用。续场是有序的前章末场→后章首场，只有命题对可左右互换。
`unresolved` 保持未决并阻断 canonical 发布，不能当成 `distinct`。第一身份任务
完成时因果尚未购买，仅经 `novel/stages/stage2/compile.py::inspect_identity_associations`
取得非发布诊断；`associate` 主身份 wire 的免费补丁预览同样显式检查身份阶段，
因果尚未组装时不声明最终通过。`cross_cause` 补丁与正式编译复验最终组装分母。

节点内窄调用的工作单元登记在 `contracts/invariants.py::NARROW_OWNER_UNITS`：
`name_variants` 与 `cross_cause` 各用自己的 scope 与内容轮额度，仍受原整书总闸限制；
最终观察的来源账和发布引用同时认领主调用及实际发生的窄调用。

**变化键闭集与拍级 access（CS-S Phase 3，rc20）**：`CHANGE_KEYS`＝27 个通用维度
（`contracts/schema/source.py`，rc15 冻结根三本书 184 个真实键聚类定稿——98 个
单次出现的书内自造键并入通用维度，语义细节由 val 承载）；`scene_location` 只由
compiler 从拍级字段合成（scene 作用域），不进 change 模型面。每拍必填
`location_reader_visible`：位置已向读者展示才为 true，无位置时必须为 false。
合成效应复制该值，内部场景位置不自动成为读者确知。拍级 `access`＝获知通道
闭集（`witnessed` 默认不写／`narrated` 叙述直陈例外——回溯、背景、概括、
「叙述者点明」；844 拍启发式实测 narrated 约 3%，told 语义由 know 的
hears_claim/asserts 模式逐条承载，不重复拍级标注）。rc27 起 access 仅表示呈现通道，
不能证明命题真实或已向读者展示。

**认知与读者知情（rc27）**：Cognition 显式携带 `op,mode,polarity,support,reader_visible`；
wire 的 mode 名为 how，其余同名。set 的模式及极性非空；unset 的两项为 null，保留
撤回依据 basis。learns/confirms 只许 confirmed；对话的确认性质仍由语义单元依据
上下文判断，单纯否认先记为负极性说法。听到否认不自动撤回误信，unset 不证明世界
命题为假，也不删除曾经听到／说过的历史。

Effect 的 reader_visible 必填。`_derive_reader_knowledge` 只从明确可见原子派生：
actual/set 事实生成保留主体、键与完整 JSON 值的正命题确知；set＋confirmed 的
learns/confirms 镜像正负极性为 reader learns；可见 hears_claim/asserts 只生成同极性
说法层记录。猜测、相信、撤回、access 或 close 文本不独立授予读者确知。
wire 禁自填 reader；正式 reader 条目及其新命题 ID 仍由 compiler 派发，不占观察句柄，
参与 Preserve 知情锁而不进入人物 lineage／participants。Book 与 Characters 人物表、
known_at_start 均禁止把 reader 注册为人物；reader_proxy 等真实人物名仍可正常登记。
历史 rc15 的 access 启发式读数不作为 rc27 的质量验收结果。

**悬念答案（rc27）**：wire 使用 `SlimLoopAction`，open 的 answer_refs 为空且 question
非空；close 的 question 为 null。数组答案使用本拍 `{kind:change/know,index:1-based}`，
索引只数模型数组，原有编号不变。拍级地点答案使用 `{kind:scene_location,index:1}`，
index 只许 1，且本拍必须实际给出 scene_location；不存在时不能误认普通 change 的 e1。
位置原子仍须 location_reader_visible=true。仅读者获知地点、人物仍未知时直接选择该
位置原子，不伪造人物认知、public 或其他 world 键。
展开到观察的 eN/cN，编译到正式 bNN_NN.eN/cN；scene_location 独立选择器映射合成 e1，
其它 change 的合成偏移及三类选择器的逆映射由代码确定性换算。
答案必须是 actual/set/reader_visible 事实，或 set/learns|confirms/confirmed/reader_visible
认知；未知、计划、撤回、猜测及未确认说法不能关闭悬念。resolution 不接受模型自由文本，
由答案原子派生；正式 Blueprint 检查答案同拍闭合、可见成立和派生文本非空。
已有生命周期与跨章配对纪律保持，不能靠删除参与者或改挂 world 迁就错误判据。

**模型输出面去机械化（I-20/I-21）**：协议里没有拍号、状态/约束编号、命题/loop/场景
正式 ID、章序、offset、hash——一律由 compiler 生成；人物 slug 仍由模型给出（可读
语义句柄，无法机械推导）。

| 观察 | 节点 | 内容 | 去机械化要点 |
|---|---|---|---|
| `BookObservation` | `str06.stage2.book` | 人物候选（可读 slug、称谓及证据句段）、初态、世界约束、书级命题词表 facts | 无 `state_id`／`constraint_id`／fact 编号（F 编号代码派发）；surface 无 `from_chapter`（`R-2.10` 锚点派生） |
| `ChapterObservation` | `str06.stage2.chapters` | 有序事件＋三类转移＋场景边界＋时间模式＋证据 | 无 `beat_id`（列表序即事件序）、无章序、无因果边；只含**章内本地句柄**（D3） |
| `AssociateObservation` | `str06.stage2.associate` | 逐候选 verdict：同一命题、悬念开合、跨章续场（身份窄调用）＋跨章因果 kind（独立窄调用） | **答案只能是已给候选**（候选由代码给出；逐候选恰答一次）；只建立同一性与引用，不移动事实成立、知情或揭示时点（D8） |
| `NamesObservation` | `str06.stage2.names` | 源名称结构（家族、名、化名；构词模板 `surface_forms` 由 compiler 派生，CS-S 1c）＋变体同一性判定（`resolutions`：same／unresolved／not_name，来自 `name_variants` 窄调用） | 形状与 v4 `NameStructure` 同源；wire 无 `surface_forms`，只有部件与 `form_overrides` 例外 |

转移语义复用拍模型（`contracts/schema/source.py` 的 `Effect`／`Cognition`／
`LoopAction`；I-11 同一领域模型不复制）：三类转移、认知模式闭集、登记键作用域、
rc27 增加认知操作、极性、支持性质、可见性与答案引用。CS-S 1b 起 effect 不再带 `disclosure`／`proposition`／`scope`
（命题锚只在 cognition）；读者知情不由观察面自写，由 compile 按原子可见性与确认性质派生。命题/loop 用章内本地
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
| `stage2/observations/associate.json` | `AssociateObservation` | `str06.stage2.associate` | 跨章关联判定：逐候选 verdict（R1 起独立协议，现役 `stage2-associate-v5`、形状 `str06-rc27`；无候选＝零调用） |
| `stage2/blueprints/chapter_blueprints.json` | `BlueprintsArtifactV18` | `str06.stage2.compile` | 逐章 Blueprint（三类转移、场景分段、拍级 access 与因果边；compile 唯一 writer） |
| `stage2/acceptance/fold.json` | 自由形态派生记录 | `str06.stage2.compile` | source fold 读数表（事件＋每章空转移拍比例＋知情时间线读数） |
| `stage2/characters/characters.json` | `CharactersArtifactV7` | `str06.stage2.compile` | Characters v7：core＋名称结构（compile 合并 book 与 names 观察后唯一发布，D9） |

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
| `source_loop_lifecycle` | hard | loops 只许 open/close：loop 一次性 open、close 引用本拍可见确认原子，resolution 由代码派生、问题文本不可改写 |
| `source_proposition_closure` | hard | 命题引用闭合：章内本地句柄可解析、新建带 statement、引用不重抄文本；fact 引用（f{nn}）按词表闭集核对、不带文本，跨章同一性按引用构造成立；残余近义漂移配对归 associate 候选判定 |
| `source_registered_key_scope` | hard | 变化键闭集与作用域（R-2.14）：change 键入 `CHANGE_KEYS` 闭集，表外键（含退役位置键）一律拒；作用域读时按登记表派生（scene_location＝scene，由拍级字段合成；其余键＝book） |
| `source_fold_consistent` | hard | fold 事件与 Blueprint canonical 一致（含 e00 只读读数） |
| `source_fold_events` | metric | fold 事件计数与章分布读数 |
| `source_fold_empty_transition_ratio` | metric | 每章空转移拍比例（三类转移全空的拍所占比例） |
| `source_fold_knowledge` | metric | 知情 fold 读数（按命题＋持有者保留极性、撤回及说法历史；含 facts 知情时间线——known_at_start 起步＋逐认知转移事件＋冗余告知读数；度量不是门） |
| `source_basis_quote_literal` | hard | R10a a1：basis 段属条目自身 source_refs 且引文逐字存在于所引句段（canonical 直通面不归一化、不模糊匹配；C4；wire 面只给段 id、引文＝段全文由 compiler 派生，2026-10-07）；Blueprint v15 证据窗口（被引段全文＋同章前后各句、重叠机械合并）由 compiler 派生——邻句被送达≠邻句成为证据 |
| `source_effect_modality` | hard | R10a a1：Effect.modality 闭集 actual/planned/expected/conditional 必填（C2 源侧）——计划/预计/条件与已发生分开；认识限定不混入命题文本；源文未说明不得自动填 actual |
| `names_structure_compile` | hard | Characters v7 名称结构编译闭合 |
| `names_coverage` | hard | 应改名集合覆盖源名称结构（名称位一致） |
| `source_semantics_unver` | unver | 源语义与关联决定未经语义审阅，保持未评披露 |

当前注册表共 **18 条**：**14 hard、3 metric、1 unver**（CS-S 1b 删 source_effect_disclosure 与 source_fold_concealed_unrevealed）。唯一注册表为
`contracts/judges/str06.py`（F8c 起分流至本层判据模块）；修复路由见各判据的 `fix`
字段——确定性节点默认重跑即 0 成本修复，付费节点重跑是新购，`fix` 留空。

## 拍模型与登记键（v13 延续，B2 起为观察面）

每一拍是一次状态转移：`event` 是给人读的事件表述；需要延续的变化全部由三类转移承载——
`effects`（叙述确立的事实；作用域读时按登记表派生——CS-S 1b 起 effect 不再携带
`scope`／`disclosure`／`proposition`，命题锚只在 cognition 上）、`cognition`（谁对哪条
命题持什么模式与极性或撤回；holder 为人物 id（观察面不写 reader——读者知情按
原子可见性与确认性质派生；public 持有者已删），set 的 mode 为
learns/suspects/confirms/hears_claim/believes/asserts，unset 的 mode／polarity 均 null）、`loops`（悬念；op 只有
open/close，close 必选本拍答案原子并派生 resolution）。拍上另带 `scene_boundary`（new/continue）与
`temporal_mode`（single/iterative）；章蓝图带 compiler 生成的 `scenes` 分段（跨章场景在
续章同样出现）。命题与 loop 的正式 ID（kNN／loopNN）由 compiler 生成：观察 wire 内用
章内本地句柄；跨章同一性由 associate 配对后由 compiler 授予唯一正式 ID。

位置分轴（登记键，`contracts/schema/source.py::REGISTERED_STATE_KEYS`；change 键闭集
`CHANGE_KEYS`）：`scene_location`（scene 作用域，当前所在，换场即失效；拍级字段合成）、
`residence`（book 作用域，长期居所，换场不清除）与身体状态键（各自一键，书作用域）；
`whereabouts`／`hiding_place`／`location` 已退役——不在闭集内，一律按表外键拒识。
章观察带本章句段与第 1..k-1 章原文（只读、
不带证据 id，D3：无快照、无登记表）；正文优先。
