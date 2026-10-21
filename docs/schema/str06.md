# 三层故事流水线契约

现役契约为 `story-v3`。Stage2 理解原小说，Stage4 设计目标小说，Stage5 执行写作，
Stage7 核对与装配。新代码只支持当前契约；旧根原字节归档，新根从 Stage1 全量重建。
旧观察、Blueprint、Design、IR、回执和历史模型响应不转换成当前正式产物。
进度与删除清单见 `docs/REQUIREMENTS.md`；R/Q/B 归属见 `docs/BACKEND_CONTRACT.md`。

## 职责与正式边界

| 层 | 语义责任 | 正式输出 | 下游消费 |
|---|---|---|---|
| Stage2 事实层 | 原作中的实体、事件、命题、认知、披露、状态、因果、线程与表达 | `SourceStory` | Stage4 命名与保真决策 |
| Stage4 建模层 | 必保什么、如何改造、人物为何选择、事件怎样形成整书与各章 | `TargetStory`、`AdaptationMap`；派生章包与 Hook 包 | Stage5 只消费目标故事材料 |
| Stage5 执行层 | 在目标故事边界内组织动作、对白、感受与节奏 | 正文/Hook 原字节及 `ProseReceipt` | Stage7 当前产物核对 |
| Stage7 交付 | 无新增语义；验证集合、来源、摘要并确定性装配 | 最终 TXT、`FinalManifest` | 下载与免费终验 |

semantic unit 负责语义选择；compiler 只转换、派发 ID、解析引用、折叠状态与投影；
validator 只报告；repository 只持久化与验封；orchestrator 只编排与执行许可。
运行入口 `novel/stages/chain.py::run_workflow`；正式路径唯一在 `contracts/story_paths.py`。
所有 canonical 产物显式携带 `contract_version='story-v3'`，缺版本/旧版/未知字段拒绝。

## 节点与顺序

节点唯一登记 `contracts/invariants.py::NODES`，Stage0/1 后为以下 15 个节点：

| 节点 | 材料与职责 | 成本 |
|---|---|---|
| `str06.stage2.chapters` | 独立章内观察；当前章原文，补料读取上限为本章 | 语义 |
| `str06.stage2.book` | 完整章观察＋原文；整合实体、命题、状态及跨章关系 | 语义 |
| `str06.stage2.associate` | 仅裁决 book 明示的未决对象及证据；无候选不调用 | 条件语义 |
| `str06.stage2.names` | 在整合后的实体闭集内拆解原作名称结构 | 语义 |
| `str06.stage2.compile` | 核对实际源与观察身份，编译唯一 `SourceStory` | 免费 |
| `str06.stage4.naming` | 从源人物名称结构确定目标姓名 | 语义 |
| `str06.stage4.preserve` | 必保约束、世界规则改造、源冲突的处理决定 | 语义 |
| `str06.stage4.body_design` | 源盲整书建模，事件、人物转变、因果、状态与章边界 | 语义 |
| `str06.stage4.chapters` | 按已冻结目标模型逐章展开场景与演出行动 | 语义 |
| `str06.stage4.compile` | 核对必保承载，编译目标故事与六分区章包 | 免费 |
| `str06.stage4.hook_design` | 从当前目标故事选择 Hook 事件与明确知情/显示授权 | 语义 |
| `str06.stage4.hook_compile` | 确定性投影 Hook 包 | 免费 |
| `str06.stage5.hook` | 按 Hook 包生成普通目标文本 | 语义 |
| `str06.stage5.body` | 按源章阅读序逐章写作，消费已接受前章短尾 | 语义 |
| `str06.stage7.assemble` | 核对当前包、正文/Hook、回执并装配 | 免费 |

“语义”不是无限重试许可；实际付费和正文外发分别由已冻结授权治理。
模型只看到独立任务协议和正式材料，不加载工程 AGENTS、运维手册或额外盘上小说。

## Stage2：事实层

唯一 schema：`contracts/schema/source_story.py`；纯引擎：`novel/stages/stage2/story.py`。
章观察先发生，book 后置综合；book 不重抄本地事件文本，也不能暗改章观察。
发现局部漏错时报告 `SourceObservationIssue`，带章序、item、path、类型和证据，回原章职责处理。

`SourceStory` 包含 `entities/events/propositions/knowledge_updates/exposures/state_slots/`
`state_updates/causal_links/threads/world_rules/expression/conflicts/evidence`。

章节模型的 `ChapterWire` 只选择代码派发的整书阅读序正整数证据序号；引子与正文不共用序号，
补料不重编号，章节补丁及材料查询也使用同一整数闭集。compiler转换为正式字符串证据ID后
发布 `ChapterObservation`；book等后续消费者只读正式观察，不依赖模型整数表。
整数协议显式升为story-v3，v1/v2根只读保留、在独立新根重建，不跨版本采用已购候选。

- 实体有稳定 ID；未命名婴儿仍是 person。实体成立与显示姓名披露分开，不以真名初现章限制人物存在。
  章观察的 `referents.surface` 是证据原文中连续出现的一个代表称呼；整书 `surfaces` 中每条
  `literal` 同样只承载一个原文字面，不拼接别名。机械校验一次报告全部独立可定位的称谓错误，
  不按标点拆字面或猜补称呼。多个局部指称统一后，参与者按实体 ID 稳定去重。
  称谓与证据不匹配时，精确联修该条目的 `surface/evidence_refs`，保留所指主体含义；
  普通字段与 `context_needs` 的证据问题同轮汇总，各自范围分别验证。
- 事件有阅读章序/章内顺序、参与者、场景边界 new/continue/unknown、时间框架与证据。
  阅读序不等于故事时间；回忆和未来不会覆盖当前状态。
- 命题保留模型给出的完整陈述，世界地位为 established/refuted/undetermined。
  compiler 不根据连词猜分原子命题；语义粒度与召回另验。
- 人物认知独立记录 holder、mode、polarity、at、supersedes；开场已有认知用 story_start。
  believes/hears_claim 不等于世界真相。读者披露另用 confirmed/claimed/suggested；
  confirmed 不能把 refuted/undetermined 命题升级成真。
  `knowledge_updates` 的语义字典顺序不承担时序；compiler 根据阅读位置和 `supersedes` 依赖
  派生稳定顺序，跨持有者、未来依赖、自指与循环明确拒绝。
- 状态槽地址由主体＋aspect＋具体 focus 组成；同一人可同时筹钱、隐瞒身份。
  更新保留 set/unset/unknown、时态、modality 与 persistence；缺席、未知和显式解除不同。
- 因果边明确 causes/motivates/enables/setup_payoff 与 stated/inferred；引用闭合不证明因果语义。
- 线程允许 open/progress/resolve/abandon；章内孤立收束可作为观察保留，整合产物不能孤立关闭或终止后继续更新。
- 世界规则与叙事表达都带证据；原作冲突/歧义保留，模型没判清的 unresolved 不冒充原文未知。

关联回答的 `same/distinct` 必须在传递同一闭包内一致；已决回答在接受前实际应用并验证完整事实。
`same` 只授予同一性合并，不能顺便改写命题世界地位、线程生命周期或具体状态。
完全相同的线程更新可合并证据；其余合并冲突明确停止并报告修订范围不足，不诱导改答 `distinct` 过闸。
names 与 associate 的 prompt schema 下发实际人物、候选和证据闭集，以及判定分支约束；
姓名部件未知用 null，真正无法机械区分的部件歧义明确拒绝。

`NamesWire.people[*].surfaces` 恰好覆盖该人物全部原称谓。每个称谓用非空分段数组声明
`text` 原文字面、`family`、`given` 或 `alias` 原化名字面；compiler派发化名ID并编译为
`StoryNameStructure.surface_forms` 的typed分段，核对拼接结果逐字等于原称谓。
纯称谓使用全text分段，不再另存pure_titles或字符串模板；非null部件与化名都须实际被分段使用。
例如“王王妃”明确为family＋text“王妃”，改姓后只渲染被声明的姓名位，不全局替换“王”字。
回派可替换精确既有称谓的完整分段数组，允许语义重分段时增减段数；此原子权限只针对该称谓，
不授予姓名整表、其他称谓或业务实体数组的增删重排。改变分段kind须定位整个segment，不能留下旧分支字段。

`AssociateWire` 只含语义判定。补料请求显式指定已给候选的 `candidate_key`，可引用证据范围
仅为该候选初始证据与实际送达该候选的补充段。`AssociateObservation` 由compiler额外写入
源身份、book摘要及按候选分组的正式补充证据；模型不得自报这些绑定字段。
材料控制回答及确定性查询结果进入耐久trace，免费恢复按当前源索引重算验证；修改runtime材料
不能扩大接受集。正式SourceStory承载真正采用的证据，下游不读取runtime临时材料文件。

`SourceIndex` 是原文码点段落索引，见 `novel/core/source_index.py`。证据文本由代码回取，
正式验证与当前索引逐字核对。`read_segments/search_literal` 有范围、分母、游标、
complete/truncated/empty 状态与清单摘要；空命中不是“不存在”的证明。
补料不改变原任务写权限；不静默截断整书材料。
窄修请求逐项保留原始错误、定位与授权范围；补料续答继续携带冻结候选及补丁协议。
错误无法安全定位或需要授权外字段时立即停止，不只修部分位置后继续购买。

## Stage4：建模层

唯一 schema：`contracts/schema/target_story.py`；纯引擎：`novel/stages/stage4/story.py`。

`NameMap` 确定目标名称；`PreservationContract` 把原作理解转为目标语义的必保要求、
满足条件、依赖、世界规则 keep/adapt/omit 决策和局部一致性决定。
必保机制不能靠 omit 删除；源语义漏错报告 `SourceFactIssue`，不在保真决策中偷偷重写原作事实。
问题须给正式source_ref、相对记录的path和显式replace/remove/append。共享契约按字段确定唯一writer，
运行时以真实已购raw重新编译证明后映射地址；不按证据章猜归属。causal_links、exposures、conflicts、
expression的保留selector仅用于问题定位，保存要求不能以selector代替正式行引用。

整书建模只看目标称呼与保真陈述，不看源原句、源事件 ID、源章拓扑。
`TargetModelDraft` 声明 premise、叙述/风格、人物声音与弧线、命题、初始状态/认知/
显示称呼、事件和章边界。事件以 trigger → choice → result 解释变化，附参与者、
状态/认知变化、requires/after、realizes 与名称释放。

compiler 校验事件恰好进入一章，章集合严格为源 1..N，因果/时间联合图无环，
已声明的知情读取按持有者与模式成立。人物可以误信；确知不能引用已否定命题。
目标持续状态以主体、dimension、qualifier、modality 分址，实际位置与计划位置可以并存；
unset 只解除同模态槽，unknown 保留显式未知，不把 null 打印为事实值。
最大余数分配整章预算，总和等于书级目标。逐章展开只决定场景与行动表达，
新增跨章状态、知识、事件或改变章边界要结构化回整书模型。

TargetModelIssue以event_no、component、operation、item_no明确骨架修订位置：
事件knowledge/state/requires/after按本章事件映射回全书既有条目；propositions、initial_state、
initial_knowledge等全书组件显式event_no=null。replace选择既有条目，append选择明确容器；
新增命题与对应认知/状态/因果条件可在同一有界修订中提交，既有位置和ID不变。
修订后完整重验保真、时间因果与跨章状态/知情闭包，再由章规划继续展开。

`TargetStory` 合并模型与章计划，是目标小说唯一真相源；`AdaptationMap` 只用于追溯
每条必保要求的目标承载和世界规则决定，不能反向覆盖故事。
Hook 从明确选择的事件、allowed_knowledge、allowed_displays 投影，不借未来事件自动释放真名。

## 写手章包与 Hook 包

`ChapterPacket` 字段：版本、chapter_ordinal、source_chapter_count、length_budget、
min_chars/max_chars、participants、names、entry_state/exit_state（安全自然语言提示）、actions 与六分区。
`HookPacket` 有版本、篇幅边界、names 与同六分区。

| 分区 | 写手职责 |
|---|---|
| must_realize | 必须演出的目标动作、变化与落点 |
| must_respect | 已成立的目标条件、当前知情与叙述边界 |
| continuity | 当前章入场/离场及需要承接的前置情况 |
| creative_guidance | 允许自主选择的表达、声音与节奏 |
| background | 当前写作所需背景 |
| guard_only | 抽象禁区，仅供约束、不得写成正文；不能携带未来答案或未释放真名 |

names 是当前授权显示，不是整书最终真名表。章语义 owner 的 WriterContext 明确按当章边界给出
安全称呼、入场/离场提示、行动与六分区；compiler 核对并投影，Stage5 不再从机器状态自然值
替换名字或推导一份阅读材料，避免单字姓名误改普通词及全局信息泄底。
源原文与内部 ID 不作为必须抄写的内容。
材料的自然语言是否仍隐含泄底需要语义验收，闭合引用不证明没有泄底。

## Stage5：顺序写作与窄修

schema：`contracts/schema/story_prose.py`；请求/验证纯函数：`novel/core/story_prose.py`；
公开阶段入口：`novel/stages/stage5/story.py`。正文按 1..N 顺序，前章接受后才构建后章请求。

每个正文请求送当前章包与前章已接受正文的连续短尾；短尾按完整非空自然段边界截取，
最多 800 非空白字符，保留实际换行字节。末段单独超过上限时不截半段，
明确标记 no_complete_paragraph_fits；第一章标记 first_chapter。
实际短尾字节进入 request_id；前章其他部分变化且短尾不变，不改变后章请求身份。

响应是 tagged JSON：prose/text 或 needs_design/problems。表达可自由组织；故事缺口必须
结构化回 Stage4。机械 validator 只检查协议、完整结束、非空、篇幅与控制字节，不宣称文学质量。

送达项带`[section:序号]`；设计缺口逐项给section、严格正整数item_no和detail。
compiler按投影顺序定位ChapterPlan/HookPlan的自然字段，行动修订可以补齐必要的即时决定与剧情桥梁；
不能改事件编号、参与者、既有知情授权或跨章边界。story_structure须显式item_no=null，范围不足停止。

章回执绑定 packet_sha256、prior_tail_sha256、body_sha256、request_id、response_id、
provider/model、实际 system/user 摘要及 finish_reason。生成请求与原始正文摘要保留，
补丁另外记录实际来源。ID/hash 由代码计算，真实响应来源由 durable 调用边界提供，
单元测试必须明确使用 synthetic 来源。

开场窄修只授权前 8 个非空自然段，每段一个稳定句柄。响应逐句柄 keep/replace，
或 needs_scope 升级范围；replace 必须单自然段，禁止新增第九段句柄。
补丁先纯验证，再采用与发布；未授权正文、分隔符和后文逐字不变。
请求绑定 base_body/base_receipt/packet/current prior_tail；前章短尾变化时允许用窄修
重新衔接，同时保留原生成来源与 original_prior_tail_sha256，不冒充整章重新生成。
修复后章尾不变，后章不级联；若章尾变化，按真实短尾身份重新核对后章。

## Stage7：核对与装配

`novel/stages/stage7/story.py::assemble_story` 要求章包与正文集合恰好是 1..N，
核对每章当前包、前章短尾、正文及补丁链，另核对当前 Hook 包与回执。
不静默排序、补章或删除。最终 TXT 为书名、Hook、逐章「第N章」和已接受正文；
正文不 strip、不归一换行、不末端改名。

`FinalManifest` 记录源章数、有序章节的 packet/tail/body/receipt 摘要与请求身份、
Hook 包/正文/回执摘要、最终 TXT 摘要。`verify_final_story` 从当前正式材料免费重建核对；
manifest 存在不等于就绪，下载也要使用当前后端判定。
mechanical=passed 只表示机械范围；semantic 始终 unver，质量验收另经 golden set 与人工阅读。

## 发布、恢复与只读监控

repository 对同 writer 的输出组先准备完整字节，再原子写各文件、回读核对，最后封存
CommitBundle。正式读取核验 seal 与当前字节摘要；免费产物同样封存，未封存不是正式输入。
已购响应先 durable，恢复只在当前契约、同请求身份与完整来源证明下免费重放/重编/重验；
缺响应、未知来源、旧版或输入漂移不得隐式新购。旧根不提供升版换算或历史语义采用。

跨owner修订以显式repair种类冻结RevisionSeed：真实已购基线、原请求/响应引用与trace、
typed替换位置、明确append权限与remove_paths。基线必须从本根耐久回答重建；首次修订响应即为字段补丁，
禁止整份JSON替代。授权外条目保持原字节，既有列表不得删项、压缩或重排；追加只能在授权容器
显式append并保留原位置；removes必须恰好覆盖授权的字典语义键，不能借删除压缩列表身份。
全书综合的原始理解、后续BookRepairWire和owner补丁保存在同一可验证history，组合trace还原完整BookWire，
BookRepairWire不能冒充完整基线。已发布理解的章输入改变后只修引用闭包，无改变则免费重编，
不重新购买全书综合。修订后重过完整compiler；范围不足或无进展停止，同请求中断恢复免费重放。

只读页面展示 source-story、target-story、章节包/正文/回执、Hook、状态、错误 owner/node/item
和权威账成本；不提供人物/Blueprint/旧 Design/IR 编辑或批准入口。
HTTP artifact envelope：candidate_id、revision、status(ready/missing/invalid)、data、errors。
run 携带当前版本与 revision；修订变化才失效对应材料缓存。不存在、合法空集合、损坏、
未运行、失败与语义未评分别显示，不能由 UI 猜通过。

## 验证与证据范围

`tests/test_source_story.py`、`tests/test_target_story.py`、`tests/test_story_prose.py`
保护纯函数与契约；`tests/test_story_pipeline.py` 走真实发布/恢复/下游；
`tests/test_story_api.py` 检查真实 HTTP 与最终下载边界。
`tests/test_hard_counterexamples.py` 对注册的每条故事 HARD 执行生产红绿对照，
不以源码出现规则名替代覆盖。测试分层与完整回归门槛见根 `AGENTS.md`。
工程通过不等于原作理解完整或成书质量合格；真实首答率与成本需另用授权真实运行核验，
已接受最终产物不能倒算模型首答通过率。
