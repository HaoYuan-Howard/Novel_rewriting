# Stage 2 当前契约

Global 与 Blueprint 的独立审阅、实际问题和 `--close-review` 免费收口契约见
`docs/schema/stage2_review.md`。Global 接受仍先于 Blueprint，独立知识关联不回写抽取主体。

## Blueprint v9 剧情契约

完整节点、稀疏状态/约束、共享证据和分场职责见 [plot_blueprint.md](./plot_blueprint.md)。
普通对白的语义、施受关系和话语作用属于 beat.content；必要字面、数值和物件才成为约束。
不生成全量事实原子、逐项通过回执或独立对白正文副本。章节、证据和人物闭集由编译器核验，
完整剧情与重要边界由逐章五维审阅及跨章复核判断。数量与证据范围不能证明语义完整。

Stage 2 的现役 JSON 与 typed 输入如下；人物登记详情见 `docs/schema/characters.md`。三份内部
恢复 artifact 只在 `--apply-edit` / Global `--revalidate` 动作尚未闭合或已有分类
terminal 等待人工后继时存活，不计作阶段输出：

| 路径 | 模型 | 生产节点 | 内容 |
|---|---|---|---|
| `stage2/characters.json` | `CharacterRegistry` v1 | `stage2.characters` | 独立静态人物、称谓与原文证据 |
| `stage2/characters_edit.json` | `CharactersEdit` v1 | 人工输入 | 绑定原始 SHA 的人物 add/replace/delete，免费同尺校验及回执 |
| `stage2/global_grounding.json` | `KnowledgeGrounding` v6 | `stage2.global` | 冻结人物实际引用 SHA、必要动态状态、关系、设定、规则、秘密、源文案及范围证据；不保存静态人物副本 |
| `stage2/blueprint_character_projection.json` | `BlueprintCharacterProjection` v1 | `stage2.characters` | 人物注册与 MainText 确定性物化的逐章称名闭集；Global 无写权限 |
| `stage2/chapter_blueprints.json` | `ChapterBlueprints` v9 | `stage2.blueprint` | 完整剧情、持续状态、稀疏约束、共享证据表和有据源语境 |
| `stage2/knowledge_links.json` | `KnowledgeLinks` v2 | `stage2.blueprint --close-review` | Commander 已保存的知识与既有 Beat/State/Global 关联；独立免费物化，不进入 Blueprint 抽取响应 |
| `stage2/global_review.json` | `GlobalReview` v2 | `stage2.global` | 由当前全局底座确定性枚举的逐项人工复核；详见 `docs/schema/evaluation.md` |
| `stage2/global_review_decisions.json` | `GlobalReviewDecisionSet` v2 | 人工输入 | actor、逐项 subject hash 决定，或绑定完整 blocking scope/正文章全集的默认决定 + 逐项例外；UNVER 可显式请求完整组隔离，也可经同形 API 提交 |
| `stage2/global_edit.json` | `GlobalEdit` v5 | 人工输入 | Stage2 typed 订正决策集（`stage2.global --emit-edit-template` 生成当前骨架，`--apply-edit` 消费） |
| `stage2/blueprint_edit.json` | `PlotEdit` v1 | 人工输入 | 当前 token 绑定的完整指定章及 preserve/replace/add 身份操作，preview/apply 共用编译器 |
| `stage2/global_edit_input_commit.json` | `GlobalEditInputCommit` v1 | `stage2.global` 内部 WAL | 在改 canonical edit/failure 前冻结 sparse target、运输身份及 edit/failure/formal-journal baseline |
| `stage2/global_edit_failure.json` | `GlobalEditFailure` v1 | `stage2.global` 内部恢复事实 | reviewed 动作精确区分 `canonical_pending / hard_gate / candidate_validation / post_edit_review` 并绑定 failed edit、修前 formal、source/workpack；普通 edit 以 `ordinary_hard_gate / ordinary_candidate_validation` 留下不伪造 reviewed 身份的 terminal |
| `stage2/global_edit_commit.json` | `GlobalEditCommit` v2 | `stage2.global` 内部 WAL | 在改知识/review/INVALID/failure 前冻结 baseline、完整 typed target 与正式渲染 SHA；`action_kind` 区分 `apply_edit / revalidate`，后者从 target audit 冻结原 actor/reason，generic INVALID 可用 `null` 精确表达没有 action-owned canonical edit identity/既有 formal |

正式产物与人工输入都使用当前 Pydantic StrictModel，未知字段拒收。新链使用干净数据根，
旧根配对旧代码保留；不自动迁移旧事实、补字段或继承旧审批。GlobalEdit v1～v4 不能提交当前 v5 写口。

## 人物订正后的免费知识重绑

静态人物的实际知识投影发生变化时，消费者拒绝拼接两代材料，状态页保持知识 stale、审阅
unknown。`novel/stages/stage2/character_rebind.py::prepare_if_changed` 是队列与 producer 共用
的证明：只有人物边明确变更、其余实际输入边仍 fresh、原来源完整且人物外键闭合时，才提供
`stage2.global --renormalize`。源文、文案或来源不明不能顺带追认。执行前后复验完整输入及
正式/INVALID 身份，归档旧知识、保留原 model/prompts/usage，以 partial_rewrite 标明新输入依据；
不新增模型调用，不授予语义接受。变化的审阅主体重新 pending，未变项保留已有有效决定。
人物自身的 HARD 在知识请求前拒绝，修复 owner 始终是 `stage2.characters`。

## 独立知识关联与免费收口

KnowledgeLinks v2 引用已有 Beat/State/Global 主体，支持 at_entry 或 at_beats（明确
trigger_beat_ref）；关联不另存同义剧情正文，也不覆盖每个普通隐含信息。State modality
说明命题性质，KnowledgeLinks 独占人物知晓、传播和读者披露许可。

必要关联的义务、有效继承、证据与候选集合由 novel/stages/stage2/knowledge_links.py
同源构造。ID 相同而实际内容、证据或成员变化必须使相关决定失效；未关联、unresolved、合法空值
与已接受分开。人物知情不自动授权读者，source 段号顺序不替代触发点。

Blueprint 的 chapter:N 和 cross_chapter 工作包包含当前知识关联 scope、实际问题及必要材料。
Stage2NodeReviewRequest.knowledge_links 只提交当前范围新增决定，其他有效决定引用继承。
全部章审当前有效、跨章明确接受、关联齐全后，由 stage2.blueprint --close-review 免费物化
带来源证明的正式关联及放行。服务先保存 _service 决定，CLI producer 修改 artifact；中断优先
按原决定补尾，不重抽蓝图、不重购审阅。

## 一、`global_grounding.json`

顶层字段全部必填：

| 字段 | 类型 | 内容 |
|---|---|---|
| `characters_reference_sha256` | SHA-256 | Global 请求实际读取的 canonical/surfaces/role 投影，由代码冻结，模型不得自报 |
| `entities` | `KnowledgeEntities` | `character_states{char_id: {fate, fate_evidence}}` 与 `relations[]`；静态身份仅在 `characters.json` |
| `canon` | `Canon` | `setting`、`world_rules[]`、`secrets[]` |
| `source_blurb` | `SourceBlurbGrounding[FactEvidence]` | 源文案事实证据、显式连续开篇范围；未确定保持 unresolved |
| `_provenance` | `GlobalProvenance` | LLM 来源账及确定性归一账 |

### 1.1 人物与称谓

本节的人物字段是 `characters.json` 与知识的显式只读组装视图。`novel/stages/stage2/grounding.py::compose`
供审阅、判据和下游消费，视图不登记为 artifact；知识写口以 `decompose` 拒绝任何静态修改。
Global v5 存量不得自动拆分或手补成 v6；整根归档后由人物和知识两个 producer 重新生产。

`Character`：

| 字段 | 类型 | 约束 |
|---|---|---|
| `canonical` | `str` | 显示名；全书唯一 |
| `address` | `Address[]` | 至少一条在原文中有据的叫法 |
| `name_distinctiveness` | `high｜medium｜common` | 闭合枚举 |
| `role` | `str` | 静态身份，不含结局 |
| `role_evidence` | `FactEvidence` | role 的代码回取原文证据 |
| `fate` | `str｜null` | 结局信息单独承载 |
| `fate_evidence` | `FactEvidence｜null` | 与 fate 同空同非空 |

`Address` 的 `surface`、`by`、`context`、`from_chapter` 全部必填；`context` 只允许
`address｜reference`。`from_chapter` 不得早于该字面在原文首次出现的章节。

`Relation` 的 `relation_id`、`subject_id`、`object_id`、`predicate`、
`predicate_evidence`、`visibility`、`valid_from_chapter`、`valid_through_chapter` 全部必填，
关系两端使用 `char_id`；后者表示关系最后仍成立的章（含该章），没有截止章时填 `null`，
且不得早于起章。`visibility` 与世内成立窗口正交，是判别联合：
`{"kind":"public","from_chapter":N}` 或 `{"kind":"via_secret","secret_id":"secret_XX"}`。
Stage4 下发只认这格；旧的人名/埋点启发式只作 INFO 对账。

### 1.2 设定、规则与秘密

- `Setting` 含必填的 `era` / `era_evidence` 与 `worldview` / `worldview_evidence`。
- `WorldRule` 含 `rule_id`、闭合枚举 `kind` 与非空 `versions[]`；正文唯一落点是
  `WorldRuleVersion.desc`，每个版本带 `effective_from_chapter` 与 `desc_evidence`。
- `Secret` 含 `secret_id`、`fact` / `fact_evidence`、`knowledge_events[]`、`setup` 与
  `first_reveal_chapter`。
- 每条 `knowledge_events[]` 含 `known_from_chapter`、`evidence` 与 typed `subject`：
  `{"kind":"character","char_id":"char_XXX"}` 或
  `{"kind":"group","label":"自由群体名"}`。前者是人物外键，后者保留无名群体；
  Stage4 只下发 `known_from_chapter <= 当前章` 的主体，且不把 evidence 发给写手。
- `setup` 是**判别联合**：`{"kind":"cold"}`（声明没有埋点）或
  `{"kind":"foreshadowed","chapters":[…]}`。v1 的可空列表里 `[]` 同时表示
  「声明冷揭示」与「模型没想过」，而这一格是两道下发闸门的前件。
  ⚠ **裁空降级成 `cold` 的那条与真·冷揭示同形**，只有来源账
  `setup_chapters_pruned` 分得开——v2 消除的是「模型意图」那层歧义，不是这一层。
- `first_reveal_chapter` 是这条原子秘密首次对读者点破的章号，是下发闸门的唯一时点。
  复合真相必须拆成多条 secret，不再保存对下发无作用的分组与确认事件。
- `foreshadowed.chapters` 必须严格早于 `first_reveal_chapter`；落盘前的裁剪记录在来源账中
  ——判据断言那份账，不断言字段值（`R-2.9`）。

### 1.3 源文案事实与开篇范围

KnowledgeGrounding v6 正式读取 MainText、HookCopy 与人物实际投影。`source_blurb.facts` 按 `claim_id`
保存三种显式分支；空列表也必须接受完整性审阅：

- `supported`：主张与正文 `FactEvidence`。
- `hook_supported`：原作者在开头钩子独立给出的主张，与 `hook_evidence: HookFactEvidence`。
  证据只指向 HookCopy，`source` 为其中的精确 SourceSpan，不伪造正文章号。
  模型和人工编辑只交 `{locator}`；代码从当前文案回取坐标。失败保留 `source=null` 与
  `unresolved_locator`，供免费 typed 编辑或 `--revalidate`，不得用正文猜补。
- `unresolved`：来源不明、歧义或与正文存在明确矛盾的主张与原因，继续阻断。

正文没有重复钩子信息本身不构成错误。`hook_supported` 仍须由 Commander 阅读文案与全书
排查矛盾并显式裁决；不是自动 PASS。这一分支只支持 Stage6 文案创作，不要求逐章正文补写，
不据此新增人物动态、关系、秘密或世界规则，也不改变 Stage5 场景披露边界。
本次只扩展现役 v6 的判别联合，原 supported/unresolved 字节与语义不变；不迁移或补写旧版产物。
已有 unresolved 用现役 GlobalEdit typed 替换后重审，已购买人物、知识与无关决定继续保留。

`opening` 是 proposed（`through_chapter/rationale/boundary_evidence`）或 unresolved（reason）。
提案代表从第 1 章到终点的连续材料范围；终点与边界证据属于同章，语义合理性仍由 Global Commander 判断。
未知事实、未知范围的审阅项 `resolution_required=true`，不能直接 PASS；先 typed 修订再审阅。
Stage6 只消费已接受提案，缺失或未知时停止。文学 arc 整体退役，结局事件继续留在事实层。

### 1.4 证据与唯一模型响应

人物与知识共用 `GlobalEvidenceCandidateTable` 的段落候选算法。每轮只从 MainText 的
`paragraph_spans` 构造连续候选 ID，prompt 和 lookup 使用同一张表。人物响应为
`CharactersResponse`，知识响应为 `KnowledgeResponse`；正文证据槽只交 `{evidence_candidate_id}`。
格式正确但不在本表的 ID，须在 acceptance、checkpoint 与业务落盘前拒绝。代码编译
`FactEvidence + SourceSpan`，模型不提交章号、locator、文件摘要或正式区间。
独立钩子证据是上述正文候选表之外的明确分支，只交文案 `{locator}`，由同一 producer 回取、
HARD 验证与恢复；审阅 EvidenceRef 的 artifact 明确为 `stage1/hook_copy.json`。

动态人物键只在 provider wire 上转为带 `char_id` 的行数组，重复 ID 拒绝；落盘时仍按
当前 typed 模型保存字典。知识仅输出 `character_states`，不重新输出静态身份。知识请求
实际消费 canonical、surfaces、role；称谓使用者、语境、出现时点与静态证据不参与该请求。
知识人物引用 SHA 由代码冻结。下游分别读人物与知识，不能隐式回写或猜测重绑。

候选 ID 的成员校验只保证证据来自所给原文，不证明段落支持主张；语义仍由两位 Commander
按各自义务核验。此前取消的 candidate/locator A/B 没有真实模型或性能结论；本期收益必须
按 `docs/OPERATIONS.md` 的性能配对执行协议 另行实测。模型 locator 与 role/fate 窄修均已退役。

人工 `CharactersEdit` 和 `GlobalEdit` 的证据使用 `{chapter_ordinal, locator}`。合法指定章
先 exact、后 normalized，且必须唯一命中；该章零命中才允许按相同尺子全书回取，并要求
全书恰一候选且任何章都没有多命中。非法章、多命中或正式 SourceSpan 漂移保持拒绝；禁止
fuzzy、全文拼接、猜首命中。定位失败以 `source=null` 与失败账表达，不能洗成没有证据需求。
存量 INVALID 仅在失败来源确为 locator、claim path 与声明章未变且 source 仍空时重试；
正式 span 漂移不得自动搬家。跨章纠正只留无正文审计，绑定最终 artifact SHA。

### 1.5 agent 固定信封与恢复

知识整段 session 使用 `KnowledgeAgentTurnResponse`，仅有 `payload.kind=full_candidate`。
业务 HARD 反馈只作用于知识候选；静态身份问题派回 `stage2.characters`。动态成员 validator
拒稿先进入绑定原回答的 custody；typed replacement 仍经同一关闭表、schema、编译和 HARD。
原回答与组装候选分别留 SHA，不得混比。只有真实接受的回答才能进入 append-only checkpoint。

知识 run 的恢复策略是 `stage2-knowledge-v2`。checkpoint 绑定输入投影、MainText、源文案、
候选表、模型/profile、session、响应与 ledger；恢复重走当前编译与判据。同一工作单元的
调用额度不因重启、换 run 或换版本复原；已保存回答和确定性补提交不再消耗内容调用。

### 1.6 知识提交与确定性审阅后继

模型回答先 checkpoint，再在同书所有权锁内对知识与其 INVALID 执行
`prepared → write → exact readback → adoption → committed`。foreign prepared 阻止其他
run 覆盖；主文件/ledger/checkpoint/当前实际依赖不能互验时保持 unknown。

人物未被知识请求使用的字段变化，允许已落且精确命中原 journal 的知识保留原来源账，
但须同时验证业务字段全等、非输入来源账全等及当前实际输入；不能把相似新稿当成原产出。
GlobalReview 是随后执行的确定性工作：按当前人物与知识重建，只继承仍有效的逐项决定，
新主体待审。审阅写回失败不能报 delivered，可沿原 run 或 `--refresh-review` 免费补尾。
称名投影不属于 Global 的写入、采用或提交日志，缺失时由人物 producer 免费恢复。

## 一之二、`blueprint_character_projection.json`

这是人物 producer 与 `stage2.blueprint` 之间的注册、版本化边界。由
`novel/stages/stage2/characters.py::_projection` 从当前 MainText 和人物产物确定性重算；
Global 不写入该投影。Blueprint 仍须等待 Global 接受，投影存在不构成越过审阅闸的许可。

顶层只含 `chapters[]`；每章三格全部必填：

| 字段 | 类型 | 内容 |
|---|---|---|
| `chapter_ordinal` | `int` | 当前 MainText 的阅读序章号 |
| `past_attested_surfaces` | `str[]` | 在当章之前已被原文证明出现的名册称名 |
| `visible_character_refs` | `{char_id: str[]}` | 截止当章已出现、且能唯一区分 owner 的关闭式人物候选 |

它没有通用 `_provenance`：freshness 先强类型校验当前人物产物与 MainText，再逐值
对账。`role` / `fate` / relation / secret / setting / evidence 等没有载体位置；
只修这些字段且投影字节不变时，既有 Blueprint 不 stale。显示名、实际可用
称呼或会改变 owner 的首现章变化时，投影变化并使 Blueprint stale。

## 二、`chapter_blueprints.json`

ChapterBlueprints v9 顶层保存 blueprints 与 _provenance。每章唯一键 chapter_ordinal
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

## 三、当前来源账

共有字段见 [`_common.md`](./_common.md)。本层专有字段全部按当前 schema 写出。

### 3.1 `GlobalProvenance`

| 字段 | 类型 | 内容 |
|---|---|---|
| `setup_chapters_pruned` | `(secret_id, old[], new[])[]` | 秘密埋点裁剪明细。⚠ `new` 为空 ⇒ 那条 `setup` 已降级成 `cold`，与声明的冷揭示同形，只有本账分得开 |
| `evidence_locator_failures` | `(claim_path, chapter, locator, cause)[]` | 指定章多命中、非法章、全书 0/多命中或 formal span 漂移；非空由 `global_evidence_unlocated` HARD 阻断。成功跨章唯一纠正另进无正文服务审计，不改变本形状 |

### 3.2 `BlueprintProvenance`

| 字段 | 内容 |
|---|---|
| scope_violations | 每次响应越出指定章的明确记录 |
| context.architecture | 唯一现役 plot-blueprint-v1 |
| context.source_format | numbered-paragraphs-v1 |
| context.global_projection_contract | plot-global-scope-v1 |
| plot_revisions | 同一 artifact 原子提交的编辑/修订身份历史、前后主体、替代映射和 tombstone |

公共模型/request/usage 字段遵循 _common.md。历史不进入每次模型工作包，费用只认 ledger，
reasoning 不重复累加。节点 ID 不等于内容身份，修订不能免费继承旧接受。

代码 seal 变化先免费复验；语义、实际依赖或审阅契约变化重开受影响范围。已购定向修订的
prepared/formal 尾窗以同一提交日志恢复，不要求旧 token 重新生效后再购买。
人工编辑在正式替换后只可清理该次原基线的问题稿，后来的 INVALID 不得被历史成功清除。

## 三之二、`global_edit.json`（必要知识的免费 typed 订正）

`GlobalEdit` v5 只改必要知识；静态人物、称谓、role、枚举及人物集合使用
`CharactersEdit`，由 `stage2.characters` 校验和提交。Global 编辑不能靠忽略额外字段接受越权
输入。改自由 claim 必须同交新 locator，不能让旧证据为新主张背书。

| 键 | 类型 | 语义 |
|---|---|---|
| `secrets` | `SecretPatch[]` | 按 secret_id 修改事实、证据、知情事件、setup 或揭示章 |
| `world_rules` | `WorldRulePatch[]` | 按 rule_id 修改 kind 或带证据的完整 versions |
| `characters` | `GlobalCharacterPatch[]` | 只改已登记人物的 fate/fate_evidence；显式 null 同时清空二者 |
| `relations` | `RelationPatch[]` | 按 relation_id 修改端点、predicate/证据、visibility、有效窗口 |
| `setting` | `SettingPatch` 或 null | 逐格修改时代/世界观，claim 与 locator 成对 |
| `secrets_replace` | `SecretResponse[]` 或 null | 完整替换秘密集合 |
| `world_rules_replace` | `WorldRuleResponse[]` 或 null | 完整替换规则集合 |
| `relations_replace` | `RelationResponse[]` 或 null | 完整替换关系集合 |
| `source_blurb_replace` | `SourceBlurbGrounding[EvidenceLocator]` 或 null | 完整替换文案事实与连续开篇范围 |
| `collection_edits` | `GlobalCollectionEdit[]` | 对 relations/secrets/world_rules 绑定集合 SHA 的 add/delete/split |
| `post_edit_review` | `GlobalEditReviewAttestation` 或 null | 当前增量 edit 的修前身份、正文覆盖与修后 typed 决定；单独出现不算编辑 |

未提交与显式清空必须分开。集合 replace 的 null 是不提交，[] 是完整清空；同集合 patch、
replace、delta 不可冲突。`add` 只交 item，`delete` 只交 subject_id，`split` 交 subject_id
和至少两个新 items；没有通用 value 字段。输入主体至多移除一次，输出 ID 唯一且不与输入
相交，split 不复用原 ID。direct patch 不能同时修改被移除主体。外键必须落在当前人物、
秘密及规则闭集；检查在内存预检完成，不等到逐件写盘才发现冲突。

### Headless Reviewer 与真实 producer

完整 canonical action 沿用 `novel-reviewer-action-v1`。唯一 scope 决定使用
`novel-stage2-scope-review-delta-v1`，唯一 GlobalEdit 使用 `novel-stage2-reviewer-delta-v2`。
Supervisor 仅从 immutable exact request 注入 actor、身份、SHA、覆盖章集合与固定 wrapper；
provider 只提交开放的值。patch 行必须是模板的无重复有序子序列，collection 仅开放当前
模板允许的固定键。空数组/null 表示本次不提交，不能自动生成 no-op 业务编辑。异常字段、
重复 object key、越权主体、错误 expected digest 均拒绝，不以 last-wins 解释。

多动作工作包保留完整 action 运输，但编辑选项必须从现役 Pydantic 模型下发
`source_blurb_replace` 的局部完整 schema，不能只展示当前 unresolved 模板。
`supported.evidence` 使用 `{chapter_ordinal, locator}`，`hook_supported.hook_evidence`
只使用 `{locator}`，两者不可互换。带 `resolution_required` 的 UNVER 不开放直接 PASS
入口，必须先 typed 编辑并在同轮 `post_edit_review` 复核；普通可重新判定的 UNVER
仍保留复裁选项。逐项事实未闭合时节点选项不开放 accept，但保留 rework、uncertain、stop。

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

`GlobalEditCommit` v2 在正式写盘前冻结知识、审阅、INVALID/failure 的 baseline/target 与渲染
SHA。target 的全局视图只是一份冻结审阅上下文；写口必须与当前人物逐字段互验，只渲染知识，
不写人物或称名投影。每个 live 路径必须仍为 baseline 或 exact target，第三方状态拒绝覆盖。
全部 readback 闭合后才 committed；重放不得制造第二份采用账或重复历史。

`--revalidate` 的 journal 还绑定原 actor/reason 和 revalidation audit。没有属于该动作的
canonical edit 或既有正式知识时，用 null 精确表达缺失。prepared、canonical_pending 优先
于 partial 正式盘面；审计与授权不会由 system 替换。`--refresh-review` 只处理当前知识的
审阅派生，不接管尚未完成的人工 journal、INVALID 或失败动作。

GlobalEdit v1～v4 不迁移；人工免费获取 v5 模板后重新核对。GlobalEditCommit v1 不可按 v2
重放；整根切换前须用匹配旧代码排空，无法排空则与旧根一同归档，不能手补新 marker。
相对路径按当前书目录解释；绝对路径只接受当前书或 `_service/edits/<same sid>`。

## 四、生成、重算与晋升

- `stage2.global`：主路径只接受本次段落候选 ID，由代码铸成正式 `SourceSpan`；locator 不再是
  模型选择面，只保留给下述 0-LLM 人工恢复；
  `--refresh-review` 从 current 人物/知识免费刷新审阅，变化主体待审；
  `--resample` 仅在已有 `global_grounding.INVALID.json` 时绕过同参数缓存，付费重采整份响应；
  `--renormalize` 仅对**已符合当前 schema**的产物重算代码派生字段；
  `--emit-edit-template` stdout-only 按当前 FAIL/未处置 UNVER 生成可执行但默认 no-op 的 v5 骨架；
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
- producer/正式 artifact registry 没有旧 schema 迁移入口、字段别名或双形状读取路径；旧生成产物
  连同证据整根归档，当前 producer 只在独立空根重新生产。Stage4A-bound v6 的冻结消费例外已退役。
  GlobalEdit v1～v4 不能提交给当前 v5 写口。

`--resample` 必须单独执行，并使用 `refresh-write`：不读同参数缓存，但合格的新响应仍写回缓存，
避免下一次正常运行再次付费。请求或响应契约失败发生在写盘前，当前正式稿与问题稿都不动；新候选
仍命中 HARD 时，旧 `.INVALID` 先进入 `_rounds/`、新问题稿接替原位，正式稿保持原字节；新候选
通过时，旧正式稿和旧问题稿都进入 `_rounds/`。每次真实调用与缓存命中仍由同一份成本账记录。

## 五、现行判据

当前注册表共 **45 条**：**29 hard、9 info、4 metric、3 unver**。

当前规则、严重度与修复 owner 以 `contracts/judges/stage2.py` 为唯一注册；三位 producer
共用机械判据，但只修复自己拥有的字段。语义未标定的检查保持 UNVER 或人工决定。

| 分组 | 当前规则 id |
|---|---|
| 产物与章对齐 | `upstream_missing` · `section_id_leak` · `chapter_coverage` · `chapter_order` |
| 人物与称谓 | `canonical_unique` · `character_unattested` · `address_by_unresolved` · `address_from_out_of_range` · `address_from_too_early` · `address_bare_pronoun` · `address_all_pronoun` · `address_has_pronoun` · `address_selfref` · `address_missing` · `address_unlocatable` |
| 身份与结局 | `role_missing` · `role_leaks_fate` · `role_repeats_name` · `hedge_words` · `fate_hedge_words` · `distinguisher_lonely` · `role_fate_unverifiable` · `fate_report` |
| 规则、秘密、关系与开篇范围 | `world_rule_no_desc` · `canon_chapter_refs_dangling` · `world_rule_names_person` · `secret_setup_order` · `secret_cold_reveal` · `secret_known_by_dangling` · `reveal_unmarked` · `secrets_absent` · `relation_dangling` · `relation_visibility_mismatch` · `global_evidence_unlocated` · `opening_scope_valid` |

| 剧情与状态 | `plot_contract` · `plot_evidence` · `plot_state_continuity` · `plot_global_refs` · `present_ids_dangling` · `scope_violation` |
| 剧情读数与语义边界 | `plot_shape` · `context_scope` · `plot_semantics` · `pronoun_surface_unattested` |

机器修复路由按当前 Rule.fix 分派。静态姓名/称谓/role 问题返回人物 producer；
知识问题返回 Global，章事实问题返回 Blueprint。证据定位终态先走所属 owner 的免费
人工编辑或复验；显式整份重采需付费授权。不能为 role/fate 重新开放已退役的模型分支。

`opening_scope_valid` 转人工用 `GlobalEdit.source_blurb_replace` + `stage2.global --apply-edit`
提交有证据的范围（0 LLM）。unresolved 保持未知，不能以机械合法替代语义裁决。
`hedge_words` 只检查会进入写作/改名材料的 `role` 与 `canonical`；确定判断「不可能」不命中，
「不太可能」「并非不可能」仍命中。`fate` 使用独立的 `fate_hedge_words` INFO，只披露、不阻断。
节点数量、字数密度及证据覆盖均不作为语义自动通过或付费返工的证明。

判据的权威定义在 `contracts/judges/stage2.py`，注册期会验证规则 id 唯一、严重度、需求落点、
节点与 flag 的存在性。报告形状见 [`_report.md`](./_report.md)。

现役 Blueprint 的选择口与字段修复能力见 `novel/stages/stage2/contract.py`；
旧逐事实 --patch 和关系编辑接口退役；完整章 typed 编辑走同一身份和证据编译器。
# 免费复验与显式归一

Global INVALID 的普通 `--revalidate` 从可信裁剪失败账恢复尚未被明确修改的原埋点意图，
再按当前揭示章重判；不能因为旧归一结果已变成 cold 而自动清空失败。显式 `--renormalize`
仍按已登记的确定性修复路由确认当前归一结果，并保留改写来源账；typed edit 可以明确改变埋点。
