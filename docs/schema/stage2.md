# Stage 2 当前契约

Global 与 Blueprint 的独立审阅、实际问题和 `--close-review` 免费收口契约见
`docs/schema/stage2_review.md`。Global 接受仍先于 Blueprint，独立知识关联不回写抽取主体。

## Blueprint v10 剧情契约

完整节点、稀疏状态/约束、共享证据和分场职责见 [plot_blueprint.md](./plot_blueprint.md)。
普通对白的语义、施受关系和话语作用属于 beat.content；必要字面、数值和物件才成为约束。
不生成全量事实原子、逐项通过回执或独立对白正文副本。章节、证据和人物闭集由编译器核验，
完整剧情与重要边界由逐章五维审阅及跨章复核判断。数量与证据范围不能证明语义完整。

已有正式蓝图不能被首次抽取静默覆盖。人物或 Global 变化后，显式章修订或 typed 编辑可以读取
有完整来源的历史自身基线，仍须核对当前源文摘要与证据，并使用当前人物、Global 和编译契约
重新验证。源文变化、来源未知或损坏不能借旧基线免费追认；同 ID 也不自动继承已过期的接受。

Stage 2 的现役 JSON 与 typed 输入如下；人物登记详情见 `docs/schema/characters.md`。三份内部
恢复 artifact 只在 `--apply-edit` / Global `--revalidate` 动作尚未闭合或已有分类
terminal 等待人工后继时存活，不计作阶段输出：

| 路径 | 模型 | 生产节点 | 内容 |
|---|---|---|---|
| `stage2/characters.json` | `CharacterRegistry` v2 | `stage2.characters` | 独立静态人物、称谓与原文证据 |
| `stage2/analyze_receipt.json` | `AnalyzeReceipt` v1 | `stage2.analyze` 内部来源 | 绑定共同初始响应、原请求、唯一账务 attempt、冻结句柄及两侧首次物化目标；不参与业务知识消费 |
| `stage2/analyze_recovery.json` | `AnalyzeRecoveryJournal` v1 | `stage2.global --recover-analyze` 内部 WAL | 修复人物后用已购 GlobalDraft 免费编译的冻结目标与提交状态；闭合后不重开 |
| `stage2/analyze_owner_draft_edit.json` | `AnalyzeOwnerDraftEdit` v1 | 对应单 owner 的免费输入 | 原 receipt/草稿/映射 SHA 与 typed 替换；详见 `docs/schema/characters.md` |
| `stage2/analyze_characters_draft_repair.json` / `stage2/analyze_global_draft_repair.json` | `AnalyzeDraftRepairJournal` v1 | 单 owner 内部 WAL | 冻结来源、请求、编辑身份、精确目标；不是业务知识产物 |
| `stage2/characters_edit.json` | `CharactersEdit` v2 | 人工输入 | 绑定原始 SHA 的人物 add/replace/delete，免费同尺校验及回执 |
| `stage2/global_grounding.json` | `KnowledgeGrounding` v7 | `stage2.global` | 四类长期约束、共享证据表与必要人物投影 SHA；人物独立存储 |
| `stage2/blueprint_character_projection.json` | `BlueprintCharacterProjection` v2 | `stage2.characters` | 人物注册与 MainText 确定性物化的逐章称名闭集；Global 无写权限 |
| `stage2/chapter_blueprints.json` | `ChapterBlueprints` v10 | `stage2.blueprint` | 完整剧情、持续状态、稀疏约束、人物呈现绑定、共享证据表和有据源语境 |
| `stage2/knowledge_links.json` | `KnowledgeLinks` v3 | `stage2.blueprint --close-review` | Commander 已保存的知识与既有 Beat/State/Global 关联、明确人物受众和身份披露端点；独立免费物化，不进入 Blueprint 抽取响应 |
| `stage2/global_review.json` | `GlobalReview` v3 | `stage2.global` | 由当前全局底座确定性枚举的逐项人工复核；详见 `docs/schema/evaluation.md` |
| `stage2/global_review_decisions.json` | `GlobalReviewDecisionSet` v3 | 人工输入 | actor、逐项 subject hash 决定，或绑定完整 blocking scope/正文章全集的默认决定 + 逐项例外；UNVER 可显式请求完整组隔离，也可经同形 API 提交 |
| `stage2/global_edit.json` | `GlobalEdit` v6 | 人工输入 | Stage2 typed 订正决策集（`stage2.global --emit-edit-template` 生成当前骨架，`--apply-edit` 消费） |
| `stage2/blueprint_edit.json` | `PlotEdit` v2 | 人工输入 | 当前 token 绑定的完整指定章及 beats/states/constraints/actor_bindings 的 preserve/replace/add 身份操作，preview/apply 共用编译器 |
| `stage2/global_edit_input_commit.json` | `GlobalEditInputCommit` v2 | `stage2.global` 内部 WAL | 在改 canonical edit/failure 前冻结 sparse target、运输身份及 edit/failure/formal-journal baseline |
| `stage2/global_edit_failure.json` | `GlobalEditFailure` v1 | `stage2.global` 内部恢复事实 | reviewed 动作精确区分 `canonical_pending / hard_gate / candidate_validation / post_edit_review` 并绑定 failed edit、修前 formal、source/workpack；普通 edit 以 `ordinary_hard_gate / ordinary_candidate_validation` 留下不伪造 reviewed 身份的 terminal |
| `stage2/global_edit_commit.json` | `GlobalEditCommit` v3 | `stage2.global` 内部 WAL | 在改知识/review/INVALID/failure 前冻结 baseline、完整 typed target 与正式渲染 SHA；`action_kind` 区分 `apply_edit / revalidate`，后者从 target audit 冻结原 actor/reason，generic INVALID 可用 `null` 精确表达没有 action-owned canonical edit identity/既有 formal |

正式产物与人工输入都使用当前 Pydantic StrictModel，未知字段拒收。新链使用干净数据根，
旧根配对旧代码保留；不自动迁移旧事实、补字段或继承旧审批。GlobalEdit v1～v5 不能提交当前 v6 写口。

## 人物订正后的免费知识重绑

静态人物的实际知识投影发生变化时，消费者拒绝拼接两代材料，状态页保持知识 stale、审阅
unknown。`novel/stages/stage2/character_rebind.py::prepare_if_changed` 是队列与 producer 共用
的证明：只有人物边明确变更、其余实际输入边仍 fresh、原来源完整且人物外键闭合时，才提供
`stage2.global --renormalize`。源文或来源不明不能顺带追认。执行前后复验完整输入及
正式/INVALID 身份，归档旧知识、保留原 model/prompts/usage，以 partial_rewrite 标明新输入依据；
不新增模型调用，不授予语义接受。变化的审阅主体重新 pending，未变项保留已有有效决定。
人物自身的 HARD 在知识请求前拒绝，修复 owner 始终是 `stage2.characters`。

## 独立知识关联与免费收口

KnowledgeLinks v3 引用已有 Beat/State/Global 主体，支持 at_entry 或 at_beats（明确
trigger_beat_ref）；关联不另存同义剧情正文，也不覆盖每个普通隐含信息。State modality
说明命题性质，KnowledgeLinks 独占人物知晓、传播和读者披露许可。

必要关联的义务、有效继承、证据与候选集合由 novel/stages/stage2/knowledge_links.py
同源构造。ID 相同而实际内容、证据或成员变化必须使相关决定失效；未关联、unresolved、合法空值
与已接受分开。人物知情不自动授权读者，source 段号顺序不替代触发点。

人物受众使用 `ActorRef {char_id,presentation_ref?}`；`candidate_actors` 是目标章与必要携入
材料实际提供的呈现闭集。身份揭示沿既有秘密或必要读者披露的 `identity_actors` 连接同一实体
的多个明确表面身份，与 `identity_revealed_from` 交叉核验，不新增称谓知情矩阵。
关系的成立/披露义务可以只保留时间锚；实际陈述关系须从 `relation_uses` 或 State/Plan 的
完整引用取得两端呈现，不能把知识义务的默认实体端点当成呈现许可。详见
[knowledge_links.md](./knowledge_links.md)。

Blueprint 的 chapter:N 和 cross_chapter 工作包包含当前知识关联 scope、实际问题及必要材料。
人物目录按本章及已提供的关系、状态、知识和上下文引用闭包编译，给出可理解的姓名、称呼、
静态身份与原文证据；`plot_characters` 同源依赖投影保护实际正文与证据变化，不能只绑定 char_id。
人物或共享知识变更后，范围快照仅免费重验当前源文与机械引用；它不重签语义接受，不修改蓝图。
真实消费内容变化的章节及跨章接受过期，无依赖章节保留原决定。
Stage2NodeReviewRequest.knowledge_links 只提交当前范围新增决定，其他有效决定引用继承。
全部章审当前有效、跨章明确接受、关联齐全后，由 stage2.blueprint --close-review 免费物化
带来源证明的正式关联及放行。服务先保存 _service 决定，CLI producer 修改 artifact；中断优先
按原决定补尾，不重抽蓝图、不重购审阅。

## 一、`global_grounding.json` v7

Global 只收删除后会损害跨章节事实、因果或披露一致性的长期约束。局部行动、临时状态、
单场称呼的施受和语境不进入 Core；其完整语义归 Blueprint 的 Beat/State，人物知情归既有
KnowledgeLinks。Blueprint、KnowledgeLinks 与 Stage4–5 消费已按本批新版契约接线；工程回归与
真书质量验收分别记录。Stage6 Prepare 的独立生产、审阅及页面迁移仍由 Gate C 完成。

| 顶层字段 | 类型 | 内容 |
|---|---|---|
| `setting` | `GlobalSetting[EvidenceId]` | 时代与社会背景；源文未建立时明确 null |
| `world_rules` | `GlobalWorldRule[EvidenceId][]` | 具有条件、运行与后果的世界规律及明确版本 |
| `relations` | `GlobalRelation[EvidenceId][]` | 两个人物实体间的长期有向关系 |
| `arc_secrets` | `GlobalArcSecret[EvidenceId][]` | 影响跨章理解或披露的原子秘密 |
| `evidence_anchors` | `{ev_<24hex>: CoreEvidenceAnchor}` | 本文件共享原文锚点；无主体引用的条目不保留 |
| `characters_reference_sha256` | SHA-256 | 编译所需人物 canonical/surfaces/role 投影身份，由代码冻结 |
| `_provenance` | `GlobalProvenance` | 实际模型、输入、采用与免费改写来源；联合首抽保留共享回执 |

人物登记见 `characters.md`，Global 不保存静态或动态人物副本。旧 entities/canon/fate/
knowledge_events/source_blurb 字段无兼容别名，当前载体严格拒绝额外字段。

### 1.1 世界内成立与读者揭示

关系和每个规则版本共同使用 `effective_from`、`effective_until`、`revealed_from`。
前二者表示世界内事实成立区间；终章包含在区间内，null 表示未声明截止章。后者是读者首次
可获知该事实的章号。两轴相互独立，不能由生效章推算披露章，所有章号必须属于 MainText。

`GlobalRelation` 还含 `relation_id`、`subject_id`、`predicate`、`object_id`、`evidence_refs`。
关系两端均指向独立人物登记，predicate 非空且证据列表非空。

`GlobalWorldRule` 含 `rule_id`、`kind`、非空 `versions`。每版含 `version_id`、`desc`、上述
三个时点和非空 `evidence_refs`。版本 ID 在该规则内唯一；版本按生效时间排列，含末章区间
不重叠。排列或重叠检查只证明世界内时序合法，不能替代语义审阅。

### 1.2 背景和跨章秘密

`setting` 包含 `era`、`social_context` 和各自的 `*_evidence_refs`。每项有值时为非空字符串
并须有证据；null 时对应证据必须为空。null 表示源文未建立，不能填通用时代标签补空。

`GlobalArcSecret` 含 `secret_id`、`fact`、`fact_ref`、`setup_chapters`、`revealed_from`、
`evidence_refs`，其中 fact 与 fact_ref 恰有一个非空：

- 独立事实：fact 为非空字符串，fact_ref=null，evidence_refs 非空。
- 引用事实：fact=null，evidence_refs=[]，fact_ref 精确指向
  `{kind:relation, relation_id}` 或 `{kind:world_rule_version, rule_id, version_id}`。
  目标必须存在，秘密 revealed_from 与目标相同。事实正文与证据只在目标保存，不复制第二份。

setup_chapters 不重复且不得晚于 revealed_from。[] 明确表示未登记铺垫；同章内独立情节
单元可以先铺垫后揭示，Stage2 缺权威分场信息，机械检查不得硬拒绝，Commander 核实语义先后。
归一过程不裁剪或改写模型提交的铺垫章。合法空秘密集合仍须接受全书完整性审阅。

### 1.3 共享证据与局部句柄

人物和 Global 共用 MainText 段落候选表。模型只引用本轮闭集 `e000001` 等候选；代码编译
`CoreEvidenceAnchor{chapter_ordinal,start,end,text_sha256}` 与稳定 `ev_<24hex>`。
坐标是源章 Python 字符码点，end 不含末端，SHA-256 绑定该片段 UTF-8 字节。正式文件没有
重复 quote。HTTP 视图额外回取 `evidence_snippets`，不可验证时为 null，浏览器不得切偏移。

KnowledgeDraft 使用局部 c1 人物端点、r1 relation_ref、w1 rule_ref、v1 version_ref、s1
secret_ref；正式 char/rel/wr/secret ID 和版本 ID 由编译器生成。独立修订提供当前 draft
和冻结映射，重排不改既有 ID，新增只分配尚未占用的 ID；不另建全局 ID 管理服务。

人工编辑 evidence_refs 可复用当前 ev 引用，或提交 `{chapter_ordinal,locator}`。定位必须
在指定章唯一命中（exact 后 normalized）；非法章、零命中、多命中写盘前拒绝。正式锚点的
坐标或摘要漂移保持 HARD，不自动搬移、跨章猜测或清空证据。失败候选及已购草稿保留给原
owner 免费编辑与重验，不能以重新购买掩盖恢复缺口。

### 1.4 联合首抽与独立修订

全空冷启动由 `stage2.analyze` 一次响应产生 CharacterDraft 与 KnowledgeDraft，再独立编译、
校验和提交两份产物。共同实际请求、响应、schema 与 checkpoint 保存在原运行记录，
不可变 AnalyzeReceipt 以各自摘要绑定这些来源、唯一账务 attempt 与两侧首次物化目标；双方
_provenance.shared_analysis_source 引用回执，usage=null，账只认同一次调用。真实生成时看过
完整共同材料，不声称首抽只读窄人物投影；未来 Global freshness 仅绑定实际业务所需人物
投影，证据等非业务变更重新打开人物审阅，不把历史生成前缀强加为未来全对象 stale 边。

已有任一半、旧 epoch 或 pending prepared 不重新走联合首抽；人物/Global 独立修订仍分别
归 stage2.characters / stage2.global。人物 HARD 时 Global 已购草稿保持 awaiting_characters，
修复人物后由免费恢复载体证明原映射再物化；不能猜已删除、合并的人物 ID。Global HARD
只进入自己 INVALID，已通过的人物和称名投影保留。

初次提交中断由 `stage2.analyze --recover-analyze-run <原run_id>` 免费补尾；先验证原运行
及两侧冻结目标，再完成原提交，不覆盖其他 owner 的修改。原始回答已有但 checkpoint 缺失时，
先恢复已购回答；格式损坏交既有 `response-repair` 入口保存修复候选与独立封存来源，
不覆盖原 stdout、不追加内容调用、不重置累计额度。修复后 checkpoint 只在 Analyze 明确
支持的 v2 分支接受，其他运行及普通 checkpoint 仍按原 v1 严格校验。拒绝修复保持终态。
人物修复后的 GlobalDraft 则走 `stage2.global --recover-analyze`；联合提交和独立恢复各自
闭合后不因日后的业务编辑重新打开。

`draft_invalid` 不触发整书重抽。该 owner 从 `--emit-analyze-draft-template` 获得原候选，
`--repair-analyze-draft` 对证原 receipt/草稿/映射后独立编译，HARD 仍保存 INVALID；prepared
只由 `--recover-analyze-draft` 补尾。人物失配必须通过 Global 草稿的显式 `character_bindings`
typed 编辑修正，原映射和联合来源不改写。字段及独立 WAL 见 `docs/schema/characters.md`。

知识独立 agent 信封为 `KnowledgeAgentTurnResponse`，payload.kind=full_candidate，恢复策略
`stage2-knowledge-v3`。checkpoint 冻结原始输入、候选表、局部 ID 映射、模型/session/回答与
ledger；恢复回读初始正式或 INVALID 的精确历史字节，不把本次已落新稿当原始请求基线。

### 1.5 知识提交和审阅后继

独立模型回答先 checkpoint，再持 owner 锁执行知识/INVALID 的
`prepared → write → exact readback → adoption → committed`。任何 side writer 不得覆盖
foreign prepared，无法互验证据、输入、回答与账时保持 unknown。GlobalReview 是确定性后继，
按当前独立人物和 Global 重建，只继承主体身份仍有效的逐项决定，变化项 pending。审阅补尾
失败不得报告 delivered；原 run 或 --refresh-review 可免费补齐，不重复购买。

称名投影只由人物 owner 写入。联合首抽、人物编辑及统一审阅的双 owner 批次各自保留明确
prepared 载体和写入集合；两个 artifact 的版本、长期依赖与修复权限保持独立。

## 一之二、`blueprint_character_projection.json`

这是人物 producer 与 `stage2.blueprint` 之间的注册、版本化边界。由
`novel/stages/stage2/characters.py::_projection` 从当前 MainText 和人物产物确定性重算；
Global 不写入该投影。Blueprint 仍须等待 Global 接受，投影存在不构成越过审阅闸的许可。

顶层只含 `chapters[]`；每章四格全部必填：

| 字段 | 类型 | 内容 |
|---|---|---|
| `chapter_ordinal` | `int` | 当前 MainText 的阅读序章号 |
| `past_attested_surfaces` | `str[]` | 在当章之前已被原文证明出现的名册称名 |
| `visible_character_refs` | `{char_id: str[]}` | 登记称谓在截至本章原文字面出现的内部实体候选；同名或共享称谓的多实体全部保留，归属交语义审阅 |
| `character_bindings` | `{char_id: {surface, identity_revealed_from}[]}` | 与 visible_character_refs 的人物键、称谓及顺序逐项相同的身份映射候选；供实际 ActorRef 编译与审阅使用 |

它没有通用 `_provenance`：freshness 先强类型校验当前人物产物与 MainText，再逐值
对账。它只消费 Characters v2 的 aliases 与 MainText，角色、关系、秘密、设定、证据
不内嵌其中；只修这些字段且最终投影字节不变时，既有 Blueprint 不 stale。
`identity_revealed_from` 是称谓与实体的同一性揭示时点，不作为称谓出现禁闸。
投影按字面出现生成分析侧闭集，不能证明共同称谓的每次归属，也不是 Writer 可直接消费的
公开身份映射；隐藏同人关系及同 Beat 多表面身份由 Blueprint 的 ActorBinding 和 WriterContext v9
呈现边界承接。登记 surface、identity_revealed_from 或 MainText 改变且最终投影字节不同，
才使该确定性输入边 stale；审阅另按实际支持材料建立依赖。

## 二、`chapter_blueprints.json`

ChapterBlueprints v10 顶层保存 blueprints 与 _provenance。每章唯一键 chapter_ordinal
仍与源章同号且顺序不变。完整字段、拆分规则、证据和状态生命周期见
[plot_blueprint.md](./plot_blueprint.md)；类型源为 contracts/schema/plot.py。

### 2.1 完整剧情与辅助结构

beats[].content 是唯一完整剧情正文；在必要人物及世界背景下，仅读这些正文应能理解本章
关键因果。独立的重要事件/时层/揭示或必要知识依赖边界必须建模，多动作或普通分场不强制拆分。
states、constraints、entry_state_refs、carry_out_refs 允许合法空集合，不能用数量硬门逼出伪原子。

### 2.2 共享证据与源语境

响应 evidence 给起止段号，编译器按当前 MainText 编号物化 EvidenceAnchor，校验源章 SHA
和正式位置。各对象只留 evidence_refs，多个内容可共用同段，原文在工作包按需回取。
source_contexts 留有据的时层、地点、实际在场者；未知用 null，与合法空值区分。

### 2.3 叙述

narration 保存本章声音、叙述身份、自称字面及共享证据引用。不得把回忆或转述人物自动标为在场，
不能从字面人称检查推导叙述语义正确。

### 2.4 独立人物登记与表面身份

人物参与、State 持有者、叙述者/视点、观察者、实际在场者、知识受众及关系两端都使用
`ActorRef`。`actor_bindings` 仅保存本章实际使用的 `presentation_id`、`char_id`、已登记
`surfaces`、本章 `evidence_refs` 和可空 `public_state_ref`；它不复制人物介绍或剧情。
多个表面身份必须明确 `presentation_ref`，同一 Beat 的两个不同呈现不能按 char_id 去重。
普通公开昵称可以共享同一个绑定；不能因一个局部 null 写法而新造另一个实体。

`relation_uses` 只声明本章实际关系使用的完整两端呈现，不重复 Global 关系正文。
秘密 `fact_ref` 只闭包到唯一明确关系呈现或指定规则版本；缺失/歧义交 Blueprint owner
免费订正。跨章携入 State 同时携其实际人物绑定、公开 State 与所选证据，不累计全书人物文件。

Stage4 的内部 Plan 保留完整引用；WriterContext v9 统一改为 opaque `actor_ref`，不下发
char_id、presentation_ref、反向映射或共同实体分组。称谓可出现与同人映射已揭示分开，
隐藏呈现不继承真实人物的 role，局部施受和改口语境继续属于完整 Beat/State。

## 三、当前来源账

共有字段见 [`_common.md`](./_common.md)。本层专有字段全部按当前 schema 写出。

### 3.1 `GlobalProvenance`

| 字段 | 内容 |
|---|---|
| `evidence_locator_failures` | 显式定位失败账；当前人工定位在写盘前拒绝，正式锚点漂移由同尺 HARD 检查 |
| `shared_analysis_source` | 可空的原联合回执路径/SHA/run/attempt；不重签原付费输入，不重复认领 usage |
| `core_edit` | 可空的统一审阅批次免费写回凭据；绑定原 Global SHA、请求 SHA 和实际人物业务 SHA |

没有 setup_chapters_pruned 或 source_blurb 归一账。实际 provenance.inputs 只记录实际读取，
批次预检不伪造未来人物输入；人物正式落盘后 Global owner 再基于真实读取生成新来源。

### 3.2 `BlueprintProvenance`

| 字段 | 内容 |
|---|---|
| scope_violations | 每次响应越出指定章的明确记录 |
| context.architecture | 唯一现役 plot-blueprint-v1 |
| context.source_format | numbered-paragraphs-v1 |
| context.global_projection_contract | plot-global-scope-v2 |
| plot_revisions | 同一 artifact 原子提交的编辑/修订身份历史、前后主体、替代映射和 tombstone |

公共模型/request/usage 字段遵循 _common.md。历史不进入每次模型工作包，费用只认 ledger，
reasoning 不重复累加。节点 ID 不等于内容身份，修订不能免费继承旧接受。

代码 seal 变化先免费复验；语义、实际依赖或审阅契约变化重开受影响范围。已购定向修订的
prepared/formal 尾窗以同一提交日志恢复，不要求旧 token 重新生效后再购买。
人工编辑在正式替换后只可清理该次原基线的问题稿，后来的 INVALID 不得被历史成功清除。

## 三之二、`global_edit.json`（必要知识的免费 typed 订正）

GlobalEdit v6 只改四类 Core，人物使用 CharactersEdit v2。稀疏 patch 以字段是否提交为准，
WAL 必须保持 exclude_unset，不能将缺省 null 重序列化成显式清空。修改 claim 须同交证据。

| 键 | 类型 | 语义 |
|---|---|---|
| `arc_secrets` | `SecretPatch[]` | 按 secret_id 修改 fact/fact_ref、证据、铺垫或 revealed_from |
| `world_rules` | `WorldRulePatch[]` | 按 rule_id 修改 kind 或完整 versions |
| `relations` | `RelationPatch[]` | 按 relation_id 修改端点、predicate/证据及三个时点 |
| `setting` | `SettingPatch` 或 null | 逐格修改 era/social_context，与对应证据成对 |
| `arc_secrets_replace` | `SecretResponse[]` 或 null | 完整替换秘密集合 |
| `world_rules_replace` | `WorldRuleResponse[]` 或 null | 完整替换规则集合 |
| `relations_replace` | `RelationResponse[]` 或 null | 完整替换关系集合 |
| `collection_edits` | `GlobalCollectionEdit[]` | relations/arc_secrets/world_rules 绑定集合 SHA 的 add/delete/split |
| `post_edit_review` | `GlobalEditReviewAttestation` 或 null | 当前请求绑定的覆盖、自检和逐项例外；单独出现不算业务编辑 |

fact 分支切换必须同时明确提交 fact、fact_ref、evidence_refs。集合 replace 的 null 为不提交，
[] 为完整清空。add 交 item，delete 交 subject_id，split 交原 subject_id 和至少两个新 items。
删除/拆分主体、输出 ID、collection SHA 和所有 direct patch 先聚合预检；被移除主体不得同轮
patch，新增主体可在同轮 patch。最终 exact fact_ref、人物端点、共享证据和时点闭合后才写入。
人物端点预检消费独立登记；普通 apply 与只读 preflight 在 canonical 输入或 WAL 写入前
聚合拒绝悬空端点。跨 owner Core 批次使用本轮人物预览，允许同一 typed 决定新增人物并
引用该人物，预览不生成伪造的正式来源边。

联合首抽的人物已通过、Global 仅有当前可解析 INVALID 时，同样可用 `--emit-edit-template`
导出问题稿的完整可编辑主体。此读取不补造 GlobalReview；模板原样提交仍被 no-op 拒绝。
订正后 `--apply-edit` 保留 Characters、联合来源与唯一账务 attempt，经同一 HARD 通过后将
Global 和首次 pending 审阅一起提交。任意写点中断按原 WAL 免费补尾，不能重买联合首抽。
只有携带已有 current review 证明的 `post_edit_review` 才可提交编辑后的语义决定。
其中 `quarantine_groups[].group_key` 只接受 `secret:<id>`，用于完整秘密组的显式 UNVER
处置；关系、规则、人物、setting 和集合完整性不得隔离。隔离仅删除下游内存视图中的秘密，
不删除其引用的独立已接受关系或规则，不改写正式 Core 或原审计决定。

GlobalProvenance 的 `retired_global_ids` 独立记录已删除主体，采用 `relation:rel_01`、
`rule:wr_01`、`secret:secret_01`、`version:wr_01:v01` 等正式身份键；初始为空。
typed 编辑与完整 owner 修订均继承已退休集合，并追加本轮删除的身份。新增主体由代码
避开当前及已退休 ID；规则版本在所属正式规则内分配。该集合不下发模型、不生成审阅义务，
也不因新一次模型来源或人审回执替换而丢失。重复局部句柄是 wire 形状错误，须保留原回答
走响应修复，不能在证据编译前猜测多对一映射。

### Headless Reviewer 与真实 producer

完整 canonical action 沿用 `novel-reviewer-action-v1`。唯一 scope 决定使用
`novel-stage2-scope-review-delta-v1`，唯一 GlobalEdit 使用 `novel-stage2-reviewer-delta-v2`。
Supervisor 仅从 immutable exact request 注入 actor、身份、SHA、覆盖章集合与固定 wrapper；
provider 只提交开放的值。patch 行必须是模板的无重复有序子序列，collection 仅开放当前
模板允许的固定键。空数组/null 表示本次不提交，不能自动生成 no-op 业务编辑。异常字段、
重复 object key、越权主体、错误 expected digest 均拒绝，不以 last-wins 解释。

统一审阅可在同一请求携带两 owner 的 Core 编辑，但先纯预检并冻结两侧原 SHA 与 typed
业务候选，再分别由人物与 Global owner 提交。人物先落盘；Global 验证实际人物业务与冻结
候选相同后生成真实 provenance。跨 owner 仅局部批次 WAL 协调，不合并两份正式 artifact。
新产生的 pending 义务不因该次编辑自动 PASS，下一次 Commander 只审新的待决范围。

修复 owner 与审阅席位分开：Global Commander 可派修人物或知识，Blueprint Commander 可
派修人物、知识或本章 Blueprint。只有两位 Commander。静态问题只开放正确 owner 的动作，
不能把未执行的静态修改夹进 GlobalEdit。source/workpack/正文章全集及 surviving 旧 FAIL/UNVER
都要复验；原问题未变时必须显式决定，默认 PASS 不能覆盖例外或替省略项作证。

只有当前 task 与 exact request 都属于 reviewed-edit correction，才允许把原 durable failed
sparse edit 作基线。修改按稳定主体替换；撤销只能用模板铸造的 opaque drop_targets 闭集，
update/drop 同主体拒绝。普通新编辑的空值不继承历史失败输入。producer 聚合预检全部集合的
诊断后才应用操作；持久拒因只保留可信类别、计数与诊断 SHA，不回显模型正文或任意 loc。

### 原子提交、恢复与当前版本

人工输入先落 `GlobalEditInputCommit`：冻结运输身份、sparse canonical target 与
edit/failure/formal-journal 基线，再写 canonical edit。reviewed 失败分为 canonical_pending、
hard_gate、candidate_validation、post_edit_review；普通编辑保留独立 terminal 类别，不伪造
Commander 身份。已支付回答先耐久保存，再做组装、预检和提交；中断重放同一回答，不开新席位。

纠正工作包必须具有当前 typed 失败回执；缺回执时保持未知，不从旧 INVALID 反推
Commander 动作，不压缩旧 full-template 输入后接入新工作单元。当前 canonical_pending
或 prepared 仍由原输入 WAL 与提交 journal 免费补尾。

`GlobalEditCommit` v3 在正式写盘前冻结知识、审阅、INVALID/failure 的 baseline/target 与渲染
SHA。target 的知识载体与当前人物业务依赖必须互验；写口只渲染知识，
不写人物或称名投影。每个 live 路径必须仍为 baseline 或 exact target，第三方状态拒绝覆盖。
全部 readback 闭合后才 committed；重放不得制造第二份采用账或重复历史。

`--revalidate` 的 journal 还绑定原 actor/reason 和 revalidation audit。没有属于该动作的
canonical edit 或既有正式知识时，用 null 精确表达缺失。prepared、canonical_pending 优先
于 partial 正式盘面；审计与授权不会由 system 替换。`--refresh-review` 只处理当前知识的
审阅派生，不接管尚未完成的人工 journal、INVALID 或失败动作。

GlobalEdit v1～v5 不迁移；人工免费获取 v6 模板后重新核对。旧 GlobalEditCommit 不可按 v3
重放；整根切换前须用匹配旧代码排空，无法排空则与旧根一同归档，不能手补新 marker。
相对路径按当前书目录解释；绝对路径只接受当前书或 `_service/edits/<same sid>`。

## 四、生成、重算与晋升

- `stage2.global`：主路径只接受本次段落候选 ID，由代码铸成正式共享证据锚点；locator 不再是
  模型选择面，只保留给下述 0-LLM 人工恢复；
  `--refresh-review` 从 current 人物/知识免费刷新审阅，变化主体待审；
  `--resample` 仅在已有 `global_grounding.INVALID.json` 时绕过同参数缓存，付费重采整份响应；
  `--renormalize` 仅对**已符合当前 schema**的产物重算代码派生字段；
  `--emit-edit-template` stdout-only 从当前 INVALID 的可编辑主体，或正式审阅的 FAIL/未处置 UNVER
  生成默认 no-op 的 v6 骨架；无 review 的问题稿不因此获得接受；
  `--apply-edit <路径>` 吃 `global_edit.json` 落 Stage2 typed facts 的 0 成本人工订正（见 §三之二）；
  `--emit-review-template` stdout-only 从当前 `global_review.json` 投影逐项 hash/evidence，
  默认全部 `UNVER`，原样应用也不会打开事实闸。称名投影独归人物 producer，
  Global 写口不补建或采用它。
- stage2.blueprint：默认 plot-blueprint-v1，一章一次逻辑请求，原文一次、必要 Global 和人物
  投影及前章稀疏携带状态。初次按实际携带依赖顺序执行，独立 session、并发上限、验证后
  checkpoint 与全章原子提交保留。状态依赖尚未闭合不预先并行购买后章。
  推理通道采纳的响应在 checkpoint 写入及恢复时均核验原始 answer_lost 字节、成本账和
  唯一审计指针；重算 checkpoint 摘要不能替代供应商留证。正式提交后新出现的问题稿，
  不能由旧批次 prepared 尾窗自动清除；冲突保留原字节供当前 owner 处理。
  --chapters 主动定向生成使用 refresh-write 和新 run；Commander 返工使用 current
  --review-token，只处理裁决章。--emit-edit-template / --apply-edit 使用当前 PlotEdit，
  preview 与 apply 同源编译，普通修订 preserve、语义拆合 replace、新增 add。
  --renormalize / --revalidate 只做当前来源可证的免费复检与派生恢复，不推测证据或更改语义。
  --emit-review-workpack 按当前 chapter:N 或 cross_chapter 返回原文、蓝图、必要依赖和实际问题，
  不制造决定。--close-review 汇合当前章审、知识关联及独立跨章接受。

- 正式写入前先跑严格载体校验与联合 HARD 闸门；失败响应进入 `.INVALID`，不会覆盖正式产物。
- producer/正式 artifact registry 不自动迁移旧 schema，不用字段别名或双形状读取追认旧审批。
  已验收 Core 根的显式 WorkflowManifest V4→V5 转换只在服务与 owner 排空、下游无产物/历史/
  已购回答时开放；保留 Core、原 profiles 与累计 ledger，免费重建确定性人物投影，并以精确
  CAS 和不可变回执闭合。操作见 `docs/OPERATIONS.md` 与 `docs/schema/work_units.md`。
  其他不兼容旧根配对冻结旧代码保留；Stage4A-bound v6 的冻结消费例外已退役。
  GlobalEdit v1～v5 不能提交给当前 v6 写口。

`--resample` 必须单独执行，并使用 `refresh-write`：不读同参数缓存，但合格的新响应仍写回缓存，
避免下一次正常运行再次付费。请求或响应契约失败发生在写盘前，当前正式稿与问题稿都不动；新候选
仍命中 HARD 时，旧 `.INVALID` 先进入 `_rounds/`、新问题稿接替原位，正式稿保持原字节；新候选
通过时，旧正式稿和旧问题稿都进入 `_rounds/`。每次真实调用与缓存命中仍由同一份成本账记录。

## 五、现行判据

当前注册表共 **29 条**：**23 hard、1 info、2 metric、3 unver**。
规则、严重度、修复 owner 以 `contracts/judges/stage2.py` 为唯一注册；本节摘要由契约测试对账。
Core 的 `global_core_contract`、`global_temporal_contract`、
`canon_chapter_refs_dangling`、`relation_dangling`、`global_evidence_unlocated` 只验证形状、
外键、章范围、成立窗口与证据锚点。`secrets_absent` 无条件保留完整性不可机械判定的说明。
人物首次出场/称谓章、证据、role 泄漏和裸代词检查由独立人物 checker 执行；同名 canonical
允许，不能再由 canonical_unique 拒绝真实重名。`hedge_words` 仍只检查 role 与 canonical。

| 范围 | 当前规则 ID |
|---|---|
| 人物出场、称谓与证据 | `character_unattested`、`address_from_out_of_range`、`address_bare_pronoun`、`address_unlocatable` |
| 公开身份的机械探针 | `role_missing`、`role_leaks_fate`、`role_repeats_name`、`hedge_words`；改写式泄漏由 `role_fate_unverifiable` 明示不可机械判定 |
| 蓝图输入与章对齐 | `upstream_missing`、`section_id_leak`、`chapter_coverage`、`chapter_order`、`scope_violation` |
| 蓝图结构、引用与证据 | `plot_contract`、`plot_evidence`、`plot_state_continuity`、`plot_global_refs`、`present_ids_dangling` |
| 蓝图提示与读数 | `pronoun_surface_unattested`、`plot_shape`、`context_scope`、`plot_semantics` |

机器修复只派给拥有对应字段的 producer。证据问题先免费编辑/复验；正文锚点不得漂移后
静默追认。全书提升边界、事实正确性、称谓归属和揭示语义均由统一审阅裁决。
节点数量、字数密度及证据覆盖均不作为语义自动通过或付费返工的证明。

判据的权威定义在 `contracts/judges/stage2.py`，注册期会验证规则 id 唯一、严重度、需求落点、
节点与 flag 的存在性。报告形状见 [`_report.md`](./_report.md)。

现役 Blueprint 的选择口与字段修复能力见 `novel/stages/stage2/contract.py`；
旧逐事实 --patch 和关系编辑接口退役；完整章 typed 编辑走同一身份和证据编译器。
