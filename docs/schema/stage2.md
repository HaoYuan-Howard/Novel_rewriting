# Stage 2 当前契约

两位 Commander 的义务回执、差异材料和 `--close-review` 免费收口契约见
`docs/schema/stage2_review.md`。Global 接受仍先于 Blueprint，独立知识关联不回写抽取主体。

## Blueprint v8 材料契约

每条 Hinge 必须保存 `source_fact_refs`（非空、唯一的本章事实引用）与后台 `source`。
`material_contract` HARD 校验材料类型、事实外键、对白字段与字面例外依据的结构闭合。
模型响应使用 `atom_key`，同一编译器在所有现役抽取架构和修复反投影中转换引用；定位失败时
保留材料身份、关联和语义，只把 source 置为 null。免费 revalidate/renormalize 不猜测新的关联。

- 普通对白使用 `kind=要害台词` 与 `dialogue={speaker,addressees,meaning,speech_act}`。
  speaker/addressees 中的人物引用只能来自本章可见人物，群体用 typed group；null 表示未知，
  addressees 空数组表示明确无特定对象。必保事实由 source_fact_refs 引用，不重复复制事实正文。
- `kind=字面例外` 必须填写 `literal={kind,reason}`，kind 只允许 password/code/riddle/identifier。
  reason 必须说明剧情为何依赖字面，不能把一般承诺或警告当作逐字要求。
- Stage4B 按显式关联事实寻找落场；同拍不同事实不再互相制造候选，相关事实分属多场时仍有歧义。
  Blueprint C 核验关联证据及对白忠实性，机械结构合法不代表语义通过。

旧 Blueprint v7 不迁移。最终切换在独立空根由 stage2.blueprint 付费重建 v8，再完成关联审阅与
Stage4 免费/付费消费链；不能手补旧 Hinge 冒充新抽取。旧根与旧代码配对归档回滚。

Stage 2 的现役 JSON 与 typed 输入如下；人物登记详情见 `docs/schema/characters.md`。三份内部
恢复 artifact 只在 `--apply-edit` / Global `--revalidate` 动作尚未闭合或已有分类
terminal 等待人工后继时存活，不计作阶段输出：

| 路径 | 模型 | 生产节点 | 内容 |
|---|---|---|---|
| `stage2/characters.json` | `CharacterRegistry` v1 | `stage2.characters` | 独立静态人物、称谓与原文证据 |
| `stage2/characters_edit.json` | `CharactersEdit` v1 | 人工输入 | 绑定原始 SHA 的人物 add/replace/delete，免费同尺校验及回执 |
| `stage2/global_grounding.json` | `KnowledgeGrounding` v6 | `stage2.global` | 冻结人物实际引用 SHA、必要动态状态、关系、设定、规则、秘密、源文案及范围证据；不保存静态人物副本 |
| `stage2/blueprint_character_projection.json` | `BlueprintCharacterProjection` v1 | `stage2.characters` | 人物注册与 MainText 确定性物化的逐章称名闭集；Global 无写权限 |
| `stage2/chapter_blueprints.json` | `ChapterBlueprints` v8 | `stage2.blueprint` | 逐章语义功能、原子事实/因果/叙事关系/状态契约、精确证据区间、材料事实关联、对白语义、舞台与叙述信息 |
| `stage2/knowledge_links.json` | `KnowledgeLinks` v1 | `stage2.blueprint --close-review` | Commander 已保存的知识与事实关联；独立免费物化，不进入 Blueprint 抽取响应 |
| `stage2/global_review.json` | `GlobalReview` v2 | `stage2.global` | 由当前全局底座确定性枚举的逐项人工复核；详见 `docs/schema/evaluation.md` |
| `stage2/global_review_decisions.json` | `GlobalReviewDecisionSet` v2 | 人工输入 | actor、逐项 subject hash 决定，或绑定完整 blocking scope/正文章全集的默认决定 + 逐项例外；UNVER 可显式请求完整组隔离，也可经同形 API 提交 |
| `stage2/global_edit.json` | `GlobalEdit` v5 | 人工输入 | Stage2 typed 订正决策集（`stage2.global --emit-edit-template` 生成当前骨架，`--apply-edit` 消费） |
| `stage2/blueprint_relation_edit.json` | `BlueprintRelationEdit` v1 | 人工输入 | hash-bound 的指定章完整叙事关系集合；`stage2.blueprint --apply-edit` 吃它，不是 producer 产物 |
| `stage2/global_edit_input_commit.json` | `GlobalEditInputCommit` v1 | `stage2.global` 内部 WAL | 在改 canonical edit/failure 前冻结 sparse target、运输身份及 edit/failure/formal-journal baseline |
| `stage2/global_edit_failure.json` | `GlobalEditFailure` v1 | `stage2.global` 内部恢复事实 | reviewed 动作精确区分 `canonical_pending / hard_gate / candidate_validation / post_edit_review` 并绑定 failed edit、修前 formal、source/workpack；普通 edit 以 `ordinary_hard_gate / ordinary_candidate_validation` 留下不伪造 reviewed 身份的 terminal |
| `stage2/global_edit_commit.json` | `GlobalEditCommit` v2 | `stage2.global` 内部 WAL | 在改知识/review/INVALID/failure 前冻结 baseline、完整 typed target 与正式渲染 SHA；`action_kind` 区分 `apply_edit / revalidate`，后者从 target audit 冻结原 actor/reason，generic INVALID 可用 `null` 精确表达没有 action-owned canonical edit identity/既有 formal |

正式产物与人工输入都使用当前 Pydantic `StrictModel`，未知字段拒收。运行时不升级旧形状，也不为
缺失字段补默认值；需要进入普通 producer 的输入必须先按本文件的当前 schema 重新生成。旧
GlobalGrounding 由 producer 重建；ChapterBlueprints v6 不迁移、不自动补空关系数组。只有它与现存
Stage4A v1 truth/mapping、原始来源边及白名单下游正式制品逐字闭合时，冻结 reader 才供状态、审批、
交付披露与零 wire 完整章 edit 使用；普通 producer、付费 repair/replan 与 Stage4B 不得消费该内存形状，
也不得回写 v6。新的 clean base 只由 `stage2.blueprint` 生成 v8。GlobalEdit v1～v4 由人免费重发当前 v5 模板并核对重写；旧输入保留历史字节，不迁移。

## 人物订正后的免费知识重绑

静态人物的实际知识投影发生变化时，消费者拒绝拼接两代材料，状态页保持知识 stale、审阅
unknown。`novel/stages/stage2/character_rebind.py::prepare_if_changed` 是队列与 producer 共用
的证明：只有人物边明确变更、其余实际输入边仍 fresh、原来源完整且人物外键闭合时，才提供
`stage2.global --renormalize`。源文、文案或来源不明不能顺带追认。执行前后复验完整输入及
正式/INVALID 身份，归档旧知识、保留原 model/prompts/usage，以 partial_rewrite 标明新输入依据；
不新增模型调用，不授予语义接受。变化的审阅主体重新 pending，未变项保留已有有效决定。
人物自身的 HARD 在知识请求前拒绝，修复 owner 始终是 `stage2.characters`。

## 独立知识关联与免费收口

`contracts/schema/knowledge_links.py` 定义当前关联载体；`novel/stages/stage2/knowledge_links.py`
是唯一义务构造、继承验证及物化实现。Blueprint 审阅契约为 v7，工作包包含当前 `scope`、
`inherited` 和 `pending_obligation_ids`。逐项主体绑定实际知识、源章正文、证据以及完整候选事实
集合；候选正文、证据或唯一性改变使受影响决定失效，无关知识修改可继承原逐项关联。全文件 SHA
另用于本次读写竞态。最新同主体否定或未决不得被早先接受覆盖，损坏来源保持 unknown。

读者揭示、伏笔、人物/群体知情、世界规则生效与揭示、关系成立与公开、必要人物状态、时代背景和
世界观分别列义务。`at_facts.fact_refs` 显式选择支持证据，`trigger_fact_ref` 必须是其中一条当前
本章 `land` 事实，唯一确定实际披露场景；不得以数组首末项或源序推导时点。`at_entry` 仅对明确开放的
世界既有状态生效，仍需当前 `entry_fact_refs` 证据。未能证明的关联使用 `unresolved`，不进入正式
写手授权。源证据出现章不自动等于世界生效章；人物知情也不授予读者揭示。关联不会把 UNVER 改为 PASS。

Blueprint Commander 在 `Stage2NodeReviewRequest.knowledge_links` 提交当前范围和新增 typed 决定；
actor/reason 与本次审阅一致。新决定与有效历史必须完整覆盖当前义务才可接受，全部继承时允许空
`decisions`。服务先保存 `_service` 决定，随后 `stage2.blueprint --close-review` 免费读取源文、
Knowledge 和 Blueprint，物化带决定来源 SHA 的 `KnowledgeLinks`，再补审批。决定或关联落盘后
中断重放同一免费入口，不重复购买 Commander；此产物缺失不会使正式 Blueprint 再抽取。

这次切换不手补旧文件：旧根配对旧代码保留，当前空根由两位 Commander 的新决定和 Blueprint
producer 生成关联。关联范围变化只复核对应义务；关联规则不加入 Blueprint E 请求或其 provenance。

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

本节的人物字段是 `characters.json` 与知识的显式只读组装视图。`grounding.py::compose`
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
按 `docs/PERFORMANCE_EXPERIMENTS.md` 另行实测。模型 locator 与 role/fate 窄修均已退役。

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

这是 `stage2.global` 与 `stage2.blueprint` 之间的注册、版本化边界，而不是
GlobalGrounding 的通用摘要。产地从当前 MainText 与盘上最新的 schema-valid
GlobalGrounding（有 `.INVALID` 时取它，否则取 formal）确定性重算；因此
global 的非称名 HARD 仍然会阻止它自身正式落盘，但不会让已有合法称名
的 Blueprint 一起空等。

顶层只含 `chapters[]`；每章三格全部必填：

| 字段 | 类型 | 内容 |
|---|---|---|
| `chapter_ordinal` | `int` | 当前 MainText 的阅读序章号 |
| `past_attested_surfaces` | `str[]` | 在当章之前已被原文证明出现的名册称名 |
| `visible_character_refs` | `{char_id: str[]}` | 截止当章已出现、且能唯一区分 owner 的关闭式人物候选 |

它没有通用 `_provenance`：freshness 先强类型校验完整 latest GlobalGrounding 与 MainText，再逐值
对账。`role` / `fate` / relation / secret / setting / evidence 等没有载体位置；
只修这些字段且投影字节不变时，既有 Blueprint 不 stale。显示名、实际可用
称呼或会改变 owner 的首现章变化时，投影变化并使 Blueprint stale。

## 二、`chapter_blueprints.json`

顶层只含必填的 `blueprints[]` 与 `_provenance`。

### 2.1 章节、原子事实契约与舞台拍

`Blueprint`：

| 字段 | 类型 | 约束 |
|---|---|---|
| `chapter_ordinal` | `int` | 与 Stage 1 正文章序一一对应 |
| `chapter_function` | `str` | 本章对全书推进的语义功能；Stage4 只读，不得重写 |
| `beats` | `Beat[]` | 非空并按原文证据顺序排列；只承担区间、舞台与材料归属 |
| `fact_atoms` | `FactAtom[]` | 非空；本章唯一事实源 |
| `causal_edges` | `CausalEdge[]` | 颠倒会改变剧情的因果边；引用闭合、无自环/环 |
| `narrative_relations` | `NarrativeRelation[]` | 明确的故事先后、回应、同场或观察关系；引用闭合、规范排序，不从源相邻关系自动推导 |
| `entry_state_refs` | `fact_id[]` | 只能引用 `obligation=context` |
| `ending_state_refs` | `fact_id[]` | 只能引用 `obligation=land` |
| `narration` | `Narration[]` | 至少一项 |

`Beat`：

| 字段 | 类型 | 约束 |
|---|---|---|
| `beat_id` | `str` | `b<章:02d>_<序:02d>`，全书唯一且章内连续 |
| `span` | `SourceSpan｜null` | 成功态必须为精确区间；`null` 只表示回取失败 |
| `time_layer` | `present｜recall｜hearsay` | 闭合枚举 |
| `place` | `str｜null` | 场所 |
| `present_char_ids` | `str[]` | 模型从截止本章可见人物闭集中选择 |
| `hinges` | `Hinge[]` | 保真材料 |

`FactAtom`：

| 字段 | 类型 | 约束 |
|---|---|---|
| `fact_id` | `str` | `f<章:02d>_<序:03d>`，由代码按证据位置编号 |
| `claim` | `str` | 一个可独立判真假的最小命题，不把动作/决定/结果揉成复合 event |
| `kind` | `event｜information｜character_state｜relationship｜reader_knowledge` | 闭合枚举；供章级 delta 确定性投影 |
| `obligation` | `land｜context` | 本章新增必须落地 / 章首已成立只供承接 |
| `source_beat_ref` | `beat_id` | 必须指向同章舞台拍 |
| `source` | `SourceSpan` | claim 的原文证据；由代码在所属拍内回取 |
| `participant_char_ids` | `char_id[]` | 只引用本章可见人物 |

`CausalEdge` 只有 `before` 与 `after` 两个 fact_id。数组顺序只记录证据位置，**不是 Stage4/5
的写作顺序**；同一源 beat 可拆成多个 atom 分到不同新场景，不相邻 atom 也可在 Stage4 合成一场。

### 2.1a `narrative_relations`：显式关系，不是源顺序副本

四类关系使用 `kind` 判别联合，且都带 `enforcement=review｜hard`：

| `kind` | 端点 | 语义 |
|---|---|---|
| `story_before` | `before_fact_ref / after_fact_ref` | 两件事在故事世界中的真实先后，不等于读者披露顺序 |
| `response_to` | `stimulus_fact_ref / response_fact_ref` | 回应必须仍指向它真正回应的刺激事实 |
| `same_scene` | `left_fact_ref / right_fact_ref` | 两事实在语义上必须同场；端点按 fact id 严格升序 |
| `observes` | `observer_char_id / observed_fact_ref / observation_fact_ref` | 指定人物通过 observation fact 见证 observed fact |

全部事实端点必须指向同章 `fact_atoms`，不得自环或重复；`causal_edges + story_before` 合并后不得
成环。`observes.observer_char_id` 必须属于本章人物闭集，且 observation fact 必须把 observer 列入
参与者。`same_scene` 只在语义确实要求不可拆场时出现；同一 source beat、相邻证据或数组位置本身
都不生成关系。

producer 只能提出 `enforcement=review`。明确且高影响的关系须经 0 模型 typed edit 晋升为 `hard`，
才会在 Stage4A 变成确定性落盘闸；review 关系仍完整进入 Commander 工作包，但不自动阻断模型。
Stage4A 只收到 opaque aliases 与这些显式关系，不收到 `SourceSpan`、source beat 或完整源叙述顺序。

Commander 的 Blueprint workpack 使用 `BLUEPRINT_REVIEW_CONTRACT=stage2-blueprint-review-v7`。
每章可见业务面必须原样投影完整 `narrative_relations`（含 kind、enforcement 与各 typed 端点），并把
这组可见关系计入 `review_identity_sha256`；不能只在当前 artifact 中保存、却从人审包里漏掉。代码/
规则与机械复检仍只进入 `validation_seal_sha256`，公开包不再输出其原料 `code_version` /
`rules_fingerprint`。关系或其它可见 finding 变化要求重读，纯 validation 变化在免费复检后可复活
原 raw `seen.read`。公开提交继续只收 `bpr4_…` token；旧 `bpr2_…` 包只可留作历史阅读，不能
提交新的已读证明。

人工修订不使用字段 patch。先运行 `stage2.blueprint --emit-edit-template` 得到当前 v1 请求，填写
`actor/reason`，并为目标章提交**完整** `narrative_relations` 集合，再用
`stage2.blueprint --apply-edit <path>` 应用。请求中的 opaque `brt1_…` review token 绑定当前完整
Blueprint 内容与 `blueprint-relations-v1` 审阅契约；过期 token、未知章、非法闭包、时序环、非法
观察者或非目标章业务漂移都在正式写盘前拒绝。成功稿记 `origin=human`，复用当前 Stage2 schema、
全部 HARD、轮次归档与 freshness，不伪造模型、prompt 或 usage。

### 2.2 精确原文区间

正式产物只使用 `SourceSpan`：

| 字段 | 类型 | 不变量 |
|---|---|---|
| `text` | `str` | 严格等于 `main_text[chapter][start:end]` |
| `start` / `end` | `int` | `0 ≤ start < end ≤ len(chapter)` |

相邻 beat 的区间不得回退；第一拍覆盖章首、最后一拍覆盖章尾。Stage4 只把 beat 当作舞台和
证据目录，不把区间顺序变成写作骨架。

SourceSpan 只保留可直接复验的字节和坐标。内容空间定位是编译过程，不是下游契约。

`Hinge` 含 `hinge_id`、`kind`、`source_fact_refs`、`dialogue`、`literal` 与 `source`。所属 beat 由父级 `Beat.hinges[]` 唯一表达，不重复存外键。正式 id 形如
`h<章:02d>_<序:03d>`，由代码编号；`kind` 取值为 `硬数字｜要害台词｜物件｜因果前提｜字面例外`；
成功态的 `source` 必须完整落在所属 beat 的 `span` 内。`source=null` 会进入失败账并阻断晋升。

### 2.3 叙述

`Narration` 的以下字段全部必填：

- `grammatical_person`: `first｜second｜third`；
- `narrator_char_id`、`focalizer_char_id`: `str｜null`；
- `pronoun_surfaces`: `str[]`。

## 三、当前来源账

共有字段见 [`_common.md`](./_common.md)。本层专有字段全部按当前 schema 写出。

### 3.1 `GlobalProvenance`

| 字段 | 类型 | 内容 |
|---|---|---|
| `setup_chapters_pruned` | `(secret_id, old[], new[])[]` | 秘密埋点裁剪明细。⚠ `new` 为空 ⇒ 那条 `setup` 已降级成 `cold`，与声明的冷揭示同形，只有本账分得开 |
| `evidence_locator_failures` | `(claim_path, chapter, locator, cause)[]` | 指定章多命中、非法章、全书 0/多命中或 formal span 漂移；非空由 `global_evidence_unlocated` HARD 阻断。成功跨章唯一纠正另进无正文服务审计，不改变本形状 |

### 3.2 `BlueprintProvenance`

| 字段 | 类型 | 内容 |
|---|---|---|
| `scope_violations` | `str[]` | 逐章请求越出任务范围；空也必须写 |
| `context` | `BlueprintContext` | 逐章上下文架构与边界 |
| `verbatim_failures` | `(beat_id, position, cause)[]` | 回取失败；非空阻断晋升。`position ∈ span｜hinge`，根因取值面见下 |
| `fact_atom_failures` | `(fact_id, source_beat_ref, cause)[]` | 原子证据无法在声明 beat 定位，且不能凭全章唯一命中确定性改绑到唯一其他 beat；非空阻断晋升。失败 artifact 不保存原 `evidence_phrase`，存量 0-wire 恢复只从来源账/ledger/当前输入与重编译结果全部闭合的唯一 checkpoint 取回，绝不从 claim 猜 locator |
| `hinge_ambiguous_pending` | `(beat_id, kind, phrase)[]` | 拍区间内多处命中的原短语，供 `--patch` 确定性枚举候选 |

成功归一、边界外扩、地点实验读数与修复历史不再重复落盘：它们只供展示或与 ledger/运行时前置条件
重复，既不驱动闸门，也不提供恢复输入。来源账只保留会阻断或能直接驱动零成本恢复的正面失败态。

`verbatim_failures` 的根因是**闭合取值面**，不是自由文本：
`position == 'span'` 那一族见 `contracts/invariants.py::SPAN_FAILURE_CAUSES`，
`position == 'hinge'` 那一族见同文件的 `HINGE_FAILURE_ROUTE`——后者同时是
「哪一句话落到哪一条判据」的对应表，判据侧**按相等查它**，不按中文子串猜。

⚠ 这里曾有第三个字段 `verbatim_recovered`（LCS 回取明细），随
`bounded-fidelity-v3` 与判据 `anchor_not_verbatim` 一并删除：beat 的区间改由段号算出，
hinge 那一侧的 LCS 兜底本来就是禁的 ⇒ 那笔账**结构性恒空**。

`BlueprintContext` 固定边界为：

- `architecture` 取值登记在 `contracts/invariants.py::BLUEPRINT_ARCHITECTURES`：
  `extraction-ir-v2`、`extraction-ir-v3.1`、`extraction-ir-v3.2`；当前默认由同文件
  `BLUEPRINT_ARCHITECTURE` 唯一指定为 v3.2；v3.2 的模型响应只交 micro-event
  止段与 fact 段落候选 ID，代码在 checkpoint/业务写盘前将其展开为 v3.1 同形的
  拍边界与正式 `SourceSpan`，并验证候选属于 fact 所在 beat；
  定向修复按基线产物的架构选契约，不把存量产物伪装成当前默认；
- 必填 `stable_prefix`、`past_neighbor_chars_max`、`continuity_chars_max`、
  `past_names_chars_max`。

每条 `Repair` 必填：`chapter_ordinal`、`failure_items[]`、`failure_evidence`、`user_prompt`、
`output`、`model`、`cache_policy`、`fields[]`、`before_metrics`、`after_metrics`；其中指标均为
`beats`、`fact_atoms`、`hinges` 三个计数。

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
- `stage2.blueprint`：逐章生成；默认 `extraction-ir-v3.2` 每章发一次请求，把召回脚手架和
  最终细节合在同一响应中；`micro_event_ends` 只交严格递增且到章尾的段号，fact evidence
  只交本章关闭式段落候选 id，正文每段只渲染一次，compiler 确定性回取正式 `SourceSpan`；
  narration 选择本章可见 `char_id`，hinge 优先选择确定性原文候选 id，候选覆盖不到仍交唯一
  phrase。显式 v3.1 回退臂保留带短 `cue` 的 `micro_events`，同样每章一请求；显式 v2 回退臂
  每章发两相请求：第一相同时列 `micro_events`（完整回忆层）与 `beats`（因果拍压缩层），
  两列止段严格递增且落到章尾，beat 止段必须取自 micro-event 止段；第二相只能在代码冻结的
  beat 边界上填 time layer、地点、hinge、叙述信息与可见 `char_id`。`--chapters` 定向付费
  重生成；`--renormalize` 重算当前 schema 的原文定位，不会在无显示名时猜测人物 ID；
  它要求已有正文与称名投影来源边，来源未知时拒绝重签；旧 `{MainText, Global}` 输入
  不再有免费迁移或独立待办入口。旧书内 Stage2 run/checkpoint 保持原位，不复制到 service 根，
  也不把旧目录或坏来源解释成首次请求；原恢复需配对冻结代码。
  `--check-only` 联合读取两份业务产物复检。Blueprint 的 prompt、dynamic
  validator、compile 与 provenance 只读 MainText + 称名投影，不伪造一条 canonical
  global 输入边。三种现役契约的拓扑显式登记在 `ContractSpec.topology`，预览、整跑与定向修复共用
  该分派，不从可空 system 字段猜。`--emit-review-workpack` 零调用输出绑定当前 Blueprint、
  MainText、`stage2-blueprint-review-v7` 与完整 `narrative_relations`，并把免费机械复检另记为
  validation seal；对外只交 opaque review token。异常章先排，但不省略正常章、关系或语义消费字段，
  也不创建审批、决定或新 artifact。
- 正式写入前先跑严格载体校验与联合 HARD 闸门；失败响应进入 `.INVALID`，不会覆盖正式产物。
- producer/正式 artifact registry 没有旧 schema 迁移入口、字段别名或双形状读取路径；旧生成产物
  连同证据整根归档，当前 producer 只在独立空根重新生产。Stage4A-bound v6 的冻结消费例外已退役。
  GlobalEdit v1～v4 不能提交给当前 v5 写口。

`--resample` 必须单独执行，并使用 `refresh-write`：不读同参数缓存，但合格的新响应仍写回缓存，
避免下一次正常运行再次付费。请求或响应契约失败发生在写盘前，当前正式稿与问题稿都不动；新候选
仍命中 HARD 时，旧 `.INVALID` 先进入 `_rounds/`、新问题稿接替原位，正式稿保持原字节；新候选
通过时，旧正式稿和旧问题稿都进入 `_rounds/`。每次真实调用与缓存命中仍由同一份成本账记录。

## 五、现行判据

当前注册表共 **69 条**：**40 hard、16 info、11 metric、2 unver**。

当前规则、严重度与修复 owner 以 `contracts/judges/stage2.py` 为唯一注册；三位 producer
共用机械判据，但只修复自己拥有的字段。语义未标定的检查保持 UNVER 或人工决定。

| 分组 | 当前规则 id |
|---|---|
| 产物与章对齐 | `upstream_missing` · `section_id_leak` · `chapter_coverage` · `chapter_order` |
| 人物与称谓 | `canonical_unique` · `character_unattested` · `address_by_unresolved` · `address_from_out_of_range` · `address_from_too_early` · `address_bare_pronoun` · `address_all_pronoun` · `address_has_pronoun` · `address_selfref` · `address_missing` · `address_unlocatable` |
| 身份与结局 | `role_missing` · `role_leaks_fate` · `role_repeats_name` · `hedge_words` · `fate_hedge_words` · `distinguisher_lonely` · `role_fate_unverifiable` · `fate_report` |
| 规则、秘密、关系与开篇范围 | `world_rule_no_desc` · `canon_chapter_refs_dangling` · `world_rule_names_person` · `secret_setup_order` · `secret_cold_reveal` · `secret_known_by_dangling` · `reveal_unmarked` · `secrets_absent` · `relation_dangling` · `relation_visibility_mismatch` · `global_evidence_unlocated` · `opening_scope_valid` |
| 拍与区间 | `beat_id_unique` · `beat_id_format` · `span_unlocatable` · `verbatim_failed` · `edge_hole` · `beats_collapsed` · `beats_coarse` |
| 保真材料 | `hinge_cross_chapter` · `hinge_in_hole` · `hinge_anchor_void` · `hinge_ambiguous` · `hinge_cascade` · `hinge_drift` · `chapter_no_hinge` |
| 在场与叙述 | `time_layer_no_present` · `present_ids_dangling` · `present_continuity` · `present_ungrounded` · `present_early_named` · `event_early_name` · `pronoun_surface_unattested` · `narration_multi` |
| 范围与必填 | `scope_violation` · `fact_atom_empty` |
| 无条件读数 | `narration_voices` · `time_layer_mix` · `place_moves` · `present_broad` · `context_scope` · `span_coverage` · `span_coverage_per_chapter` · `uncovered_holes` · `hinge_density` |

机器修复路由按当前 Rule.fix 分派。静态姓名/称谓/role 问题返回人物 producer；
知识问题返回 Global，章事实问题返回 Blueprint。证据定位终态先走所属 owner 的免费
人工编辑或复验；显式整份重采需付费授权。不能为 role/fate 重新开放已退役的模型分支。

`opening_scope_valid` 转人工用 `GlobalEdit.source_blurb_replace` + `stage2.global --apply-edit`
提交有证据的范围（0 LLM）。unresolved 保持未知，不能以机械合法替代语义裁决。
`hedge_words` 只检查会进入写作/改名材料的 `role` 与 `canonical`；确定判断「不可能」不命中，
「不太可能」「并非不可能」仍命中。`fate` 使用独立的 `fate_hedge_words` INFO，只披露、不阻断。
`beats_collapsed` 继续 HARD；`beats_coarse` 是同书长尾 INFO，没有付费重生成路由。

判据的权威定义在 `contracts/judges/stage2.py`，注册期会验证规则 id 唯一、严重度、需求落点、
节点与 flag 的存在性。报告形状见 [`_report.md`](./_report.md)。

两份现役 Blueprint 契约的选择口、`--patch` 修复阶梯，以及退役 Blueprint 实验臂的历史 A/B 读数，见
`docs/stage2_contract_experiment.md`（操作手册与实验档案）。
# 免费复验与显式归一

Global INVALID 的普通 `--revalidate` 从可信裁剪失败账恢复尚未被明确修改的原埋点意图，
再按当前揭示章重判；不能因为旧归一结果已变成 cold 而自动清空失败。显式 `--renormalize`
仍按已登记的确定性修复路由确认当前归一结果，并保留改写来源账；typed edit 可以明确改变埋点。
