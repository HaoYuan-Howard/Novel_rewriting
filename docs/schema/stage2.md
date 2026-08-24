# Stage 2 当前契约

Stage 2 产出三份严格 JSON，另收两份人产的决策集：

| 路径 | 模型 | 生产节点 | 内容 |
|---|---|---|---|
| `stage2/global_grounding.json` | `GlobalGrounding` v3 | `stage2.global` | 全书人物、关系、设定、规则、秘密、弧线及代码回取证据 |
| `stage2/chapter_blueprints.json` | `ChapterBlueprints` | `stage2.blueprint` | 逐章摘要、精确原文区间、事件、保真材料与叙述信息 |
| `stage2/global_review.json` | `GlobalReview` | `stage2.global` | 由当前全局底座确定性枚举的逐项人工复核；详见 `docs/schema/evaluation.md` |
| `stage2/global_review_decisions.json` | `GlobalReviewDecisionSet` | 人工输入 | actor、理由、证据及 subject hash 乐观锁；也可经同形 API 提交 |
| `stage2/global_edit.json` | `GlobalEdit` v2 | 人工输入 | Stage2 typed 订正决策集（`stage2.global --apply-edit` 吃它） |

三份正式产物与两份人工决策集都使用当前 Pydantic `StrictModel`，未知字段拒收。运行时不升级旧形状，也不为
缺失字段补默认值；需要保留的输入必须先按本文件的当前 schema 重新生成。GlobalGrounding
v1/v2 由 `stage2.global` 付费重建；GlobalEdit v1 里的旧知情形状没有时点、旧自由事实没有
locator，代码无法安全推断，必须由人按 v2 重写，不能手补默认值冒充当前产地。

## 一、`global_grounding.json`

顶层字段全部必填：

| 字段 | 类型 | 内容 |
|---|---|---|
| `entities` | `Entities` | `characters{char_id: Character}` 与 `relations[]` |
| `canon` | `Canon` | `setting`、`world_rules[]`、`secrets[]` |
| `arc` | `Arc` | `conflict_stages[]`、`climax`、`ending`、`arc_tracks[]` |
| `_provenance` | `GlobalProvenance` | LLM 来源账及确定性归一账 |

### 1.1 人物与称谓

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
- `Secret` 含 `secret_id`、`fact` / `fact_evidence`、可空 `group_id`、
  `knowledge_events[]`、`setup` 与 `reader_reveal_events[]`。
- 每条 `knowledge_events[]` 含 `known_from_chapter`、`evidence` 与 typed `subject`：
  `{"kind":"character","char_id":"char_XXX"}` 或
  `{"kind":"group","label":"自由群体名"}`。前者是人物外键，后者保留无名群体；
  Stage4 只下发 `known_from_chapter <= 当前章` 的主体，且不把 evidence 发给写手。
- `setup` 是**判别联合**：`{"kind":"cold"}`（声明没有埋点）或
  `{"kind":"foreshadowed","chapters":[…]}`。v1 的可空列表里 `[]` 同时表示
  「声明冷揭示」与「模型没想过」，而这一格是两道下发闸门的前件。
  ⚠ **裁空降级成 `cold` 的那条与真·冷揭示同形**，只有来源账
  `setup_chapters_pruned` 分得开——v2 消除的是「模型意图」那层歧义，不是这一层。
- 每个揭示事件含 `chapter` 与 `kind`（`first` | `confirm`，默认 `first`）。
  载体强制**恰有一条 `first`** 且没有事件早于它；复合真相拆成多条、`group_id` 归组
  （`R-2.18`）。逐章下发闸门与全部下游读数以 `first` 为基准，`confirm` 不参与。
- `foreshadowed.chapters` 必须严格早于 `first` 揭示章；落盘前的裁剪记录在来源账中
  ——判据断言那份账，不断言字段值（`R-2.9`）。

### 1.3 弧线

- `ConflictStage`：`stage` + `chapters[]`；`chapters` 是逐章显式枚举，不是区间端点，
  全部阶段合起来必须让 Stage1 的每个阅读序章号恰好出现一次（`R-2.14`）。
- `ArcTrack`：`subject`、`dimension`、`change_type`、`trajectory`、`trajectory_evidence`。
- `Arc` 的 `climax` 与 `ending` 均为必填非空文本。

### 1.4 自由事实证据：locator 只在入口，SourceSpan 只在正式产物

模型响应与 `GlobalEdit` v2 对自由事实只交 `EvidenceLocator`：

| 字段 | 类型 | 约束 |
|---|---|---|
| `chapter_ordinal` | `int` | 必须是 Stage1 现役阅读序章号 |
| `locator` | `str` | 1～160 字的短原文定位串 |

代码只在指定章内定位：raw 原文唯一命中优先，否则尝试 normalized 唯一命中；0 处或多处都不猜，
更不能静默取首命中。成功后正式产物改写成 `FactEvidence {chapter_ordinal, source: SourceSpan}`，
`SourceSpan` 继续服从 §2.2 的逐字/坐标不变量。失败时仍构造
`FactEvidence {chapter_ordinal, source: null}` 并登记失败账，使问题稿能落
`global_grounding.INVALID.json`；随后可用 typed locator 重新 `--apply-edit`，或手改 `.INVALID`
后 `--revalidate`，而不是在 Pydantic 构造前崩掉、把付费结果永久锁死。

七类必举证事实是：人物 role/fate、relation predicate、setting 两格、world-rule desc、
secret fact、每条 knowledge event、arc trajectory。模型响应使用独立的 `*Response` 载体，
不允许直接填写正式 `SourceSpan`；`--purge-role` 的窄响应仍只交 role/fate，claim 改变后旧证据
会被明确置为 `source=null` 并由 HARD 送入可恢复的 `.INVALID`，不会把旧证据错误绑定到新 claim。

Stage4/5/6 的实际材料与节点 freshness 投影共用代码剥掉全部 evidence：坐标或证据文本单独修订
不会触发无关付费重跑，也不会把原文证据作为额外故事材料外发给下游模型。

## 二、`chapter_blueprints.json`

顶层只含必填的 `blueprints[]` 与 `_provenance`。

### 2.1 章节与拍

`Blueprint`：

| 字段 | 类型 | 约束 |
|---|---|---|
| `chapter_ordinal` | `int` | 与 Stage 1 正文章序一一对应 |
| `summary` | `str` | 非空 |
| `beats` | `Beat[]` | 非空并按原文顺序排列 |
| `narration` | `Narration[]` | 至少一项 |

`Beat`：

| 字段 | 类型 | 约束 |
|---|---|---|
| `beat_id` | `str` | `b<章:02d>_<序:02d>`，全书唯一且章内连续 |
| `span` | `SourceSpan｜null` | 成功态必须为精确区间；`null` 只表示回取失败 |
| `time_layer` | `present｜recall｜hearsay` | 闭合枚举 |
| `place` | `str｜null` | 场所 |
| `present_names` | `str[]` | 模型给出的在场称呼 |
| `present_char_ids` | `str[]` | 代码按当前人物表确定性解析 |
| `event` | `str` | 非空事件文本 |
| `hinges` | `Hinge[]` | 保真材料 |

### 2.2 精确原文区间

正式产物只使用 `SourceSpan`：

| 字段 | 类型 | 不变量 |
|---|---|---|
| `text` | `str` | 严格等于 `main_text[chapter][start:end]` |
| `start` / `end` | `int` | `0 ≤ start < end ≤ len(chapter)` |
| `normalized` | `str` | 严格等于 `content_only(text)` |
| `recovery` | `exact｜normalized` | 本次回取方式（取值面：`contracts/invariants.py::SPAN_RECOVERY`） |

相邻 beat 的区间不得回退；第一拍覆盖章首、最后一拍覆盖章尾。Stage 4 直接消费该区间，
不再搜索自由文本锚点。

`recovery` 的两档各自的来源（`bounded-fidelity-v3`）：**beat 的区间恒 `exact`**——
它由段号查表算出，模型根本没写过那段字节；`normalized` 只可能出自 `hinge.phrase`
（模型把半角标点写成全角、或压掉了换行）。⚠ 曾经有第三档 `lcs`（按最长公共子串把被改掉
一两个字的锚点救回来），随 v3 一并下线：新架构下它**结构性不可产生**。

`Hinge` 只含 `kind` 与 `source`。`kind` 取值为 `硬数字｜要害台词｜物件｜因果前提`；
成功态的 `source` 必须完整落在所属 beat 的 `span` 内。`source=null` 会进入失败账并阻断晋升。

### 2.3 叙述

`Narration` 的以下字段全部必填：

- `grammatical_person`: `first｜second｜third`；
- `narrator_name`、`focalizer_name`: `str｜null`；
- `narrator_char_id`、`focalizer_char_id`: `str｜null`；
- `pronoun_surfaces`: `str[]`。

## 三、当前来源账

共有字段见 [`_common.md`](./_common.md)。本层专有字段全部按当前 schema 写出。

### 3.1 `GlobalProvenance`

| 字段 | 类型 | 内容 |
|---|---|---|
| `address_by_fallback` | `int` | `by` 确定性兜底次数 |
| `address_surface_rewrites` | `(char_id, old, new)[]` | 叫法回取改写明细 |
| `address_from_chapter_raised` | `(char_id, surface, old, new)[]` | 首现章抬升明细 |
| `canonical_distinguishers_stripped` | `(char_id, old, new)[]` | 区分词剥离明细 |
| `setup_chapters_pruned` | `(secret_id, old[], new[])[]` | 秘密埋点裁剪明细。⚠ `new` 为空 ⇒ 那条 `setup` 已降级成 `cold`，与声明的冷揭示同形，只有本账分得开 |
| `evidence_locator_rewrites` | `(claim_path, locator, source_text)[]` | locator 经 normalized 回取后实际绑定的原文字节；空也必须写 |
| `evidence_locator_failures` | `(claim_path, chapter, locator, cause)[]` | 0 命中、多命中、错章或 formal span 漂移；非空由 `global_evidence_unlocated` HARD 阻断 |

### 3.2 `BlueprintProvenance`

| 字段 | 类型 | 内容 |
|---|---|---|
| `scope_violations` | `str[]` | 逐章请求越出任务范围；空也必须写 |
| `batches` | `int[][]` | 恒为 `[[1], [2], …]` |
| `context` | `BlueprintContext` | 逐章上下文架构与边界 |
| `char_refs` | `CharRefs` | 八类人物引用解析计数 |
| `verbatim_rewrites` | `(beat_id, position, old, new)[]` | 归一命中明细。v3 起只剩 `hinge.phrase` 与 `hinge.owner` 两种来源 |
| `verbatim_failures` | `(beat_id, position, cause)[]` | 回取失败；非空阻断晋升。`position ∈ span｜hinge`，根因取值面见下 |
| `partial_regen_chapters` | `int[]` | 定向重生成章节，空也必须写 |
| `repairs` | `Repair[]` | 定向重生成账，空也必须写 |
| `place_inherit_without_base` | `beat_id[]` | IR 三态地点声称继承、但链上无基；空也必须写 |
| `place_explicit_unlocatable` | `(beat_id, name)[]` | 显式地点在截止本章正文中找不到的 INFO 探针；v2 nullable 字面同样记账 |
| `hinge_ambiguous_pending` | `(beat_id, kind, phrase)[]` | 拍区间内多处命中的原短语，供 `--patch` 确定性枚举候选 |
| `boundary_nudges` | `(beat_id, old_end, new_end, cause)[]` | IR 编译器护栏式外扩止段的明细；空也必须写 |

`verbatim_failures` 的根因是**闭合取值面**，不是自由文本：
`position == 'span'` 那一族见 `contracts/invariants.py::SPAN_FAILURE_CAUSES`，
`position == 'hinge'` 那一族见同文件的 `HINGE_FAILURE_ROUTE`——后者同时是
「哪一句话落到哪一条判据」的对应表，判据侧**按相等查它**，不按中文子串猜。

⚠ 这里曾有第三个字段 `verbatim_recovered`（LCS 回取明细），随
`bounded-fidelity-v3` 与判据 `anchor_not_verbatim` 一并删除：beat 的区间改由段号算出，
hinge 那一侧的 LCS 兜底本来就是禁的 ⇒ 那笔账**结构性恒空**。

`BlueprintContext` 固定边界为：

- `architecture` 取值登记在 `contracts/invariants.py::BLUEPRINT_ARCHITECTURES`：
  `extraction-ir-v1.1`、`extraction-ir-v2`、`extraction-ir-v3.1`；当前默认由同文件
  `BLUEPRINT_ARCHITECTURE` 唯一指定为 v2；
  定向修复按基线产物的架构选契约，不把存量产物伪装成当前默认；
- `batch_size = 1`；
- `current_chapter_full = true`；
- `future_visible = false`；
- 必填 `stable_prefix`、`past_neighbor_chars_max`、`continuity_chars_max`、
  `past_names_chars_max`。

`CharRefs` 的八个必填非负计数为 `resolved`、`pronoun_ok`、`ambiguous`、`late`、
`unknown`、`invented`、`no_names`、`pronoun`。

每条 `Repair` 必填：`chapter_ordinal`、`failure_items[]`、`failure_evidence`、`user_prompt`、
`output`、`model`、`cache_policy`、`fields[]`、`before_metrics`、`after_metrics`；其中指标均为
`beats`、`hinges`、`present_names` 三个计数。

## 三之二、`global_edit.json`（Stage2 typed 事实的 0 成本人工订正）

全局 typed 事实抽错、漏抽或 evidence 回取失败时的**契约内 0 LLM 修正入口**。它覆盖
人物 role/fate/枚举、relation、setting、secret、world rule、arc track 与 address；
逐格 patch 用稳定 ID/下标改已有记录，完整 replace 则表达新增、删除或复合事实拆分。
所有会改自由 claim 的入口必须同交新 locator，不能让旧 evidence 继续给新 claim 背书。

| 键 | 类型 | 语义 |
|---|---|---|
| `secrets` | `SecretPatch[]` | 按已有 `secret_id` 改 fact+locator、group、knowledge events、setup 或 reveal timeline |
| `world_rules` | `WorldRulePatch[]` | 按已有 `rule_id` 改 kind 或带 locator 的完整 versions |
| `characters` | `GlobalCharacterPatch[]` | 按 `char_id` 改枚举、role+locator、fate+locator；显式 `fate=null` 同步清空证据 |
| `relations` | `RelationPatch[]` | 按 `relation_id` 改端点、predicate+locator、visibility 或有效窗口 |
| `setting` | `SettingPatch｜null` | 逐格改时代/世界观；每个 claim 与自己的 locator 成对 |
| `arc_tracks` | `ArcTrackPatch[]` | 按稳定 `track_index` 改弧线格；trajectory 与 locator 成对 |
| `addresses` | `AddressContextPatch[]` | 按 `char_id + address_index` 改已有项的 `context` |
| `secrets_replace` | `SecretResponse[] \| null` | 完整替换 secrets；可拆分、新增、删除，事实与知情事件交 locator |
| `world_rules_replace` | `WorldRuleResponse[] \| null` | 完整替换 world rules；可新增、删除，desc 交 locator |
| `relations_replace` | `RelationResponse[] \| null` | 完整替换 relations；ID 唯一，predicate 交 locator |
| `arc_tracks_replace` | `ArcTrackResponse[] \| null` | 完整替换 arc tracks；至少一项，trajectory 交 locator |
| `addresses_replace` | `CharacterAddressReplacement[]` | 每项按 `char_id` 完整替换其 `address`；列表至少一条，可改 `surface/by/context/from_chapter` |

集合 replacement 的 `null` 表示「没动」，允许为空的集合用显式 `[]` 表示「经复核删除全集」；
两者不能合成默认空列表。`arc_tracks_replace` 与 `addresses_replace[].address` 不允许空，因为正式
`Arc.arc_tracks` / `Character.address` 至少一项。相同集合不能同时 patch+replace；相同 address
不能既按下标 patch 又整组 replace。

所有载体/目标/外键/章号/窗口拒收都发生在调用者对象修改、正式写盘、`.INVALID` 与
`_rounds/` 归档**之前**：实现先在深拷贝候选上应用全部 patch/replace，再对**应用后的完整图**
校验外键、章号、窗口与证据；这些校验任一失败时调用者对象保持不变。随后才把候选交给同一
归一、正式载体与判据闸门；这一段只操作本轮局部对象，即使最终载体或 HARD 拒收也不会提前写盘。

| 拒收 | 落点 | 理由 |
|---|---|---|
| 未知字段 / 空编辑 / 重复 ID 或 subject | 载体（`GlobalEdit`） | 顺序不能决定哪份人工决定生效 |
| 同一 secret/world-rule/relation/arc-track 集合既逐项 patch 又完整 replace | 载体（`GlobalEdit`） | 两个写口争同一 subject，结果依赖应用顺序 |
| 同一人物的 address 既按下标 patch 又完整 replace | 载体（`GlobalEdit`） | replacement 会改变下标含义，必须二选一 |
| 逐项 patch 的目标不存在；replacement 的人物目标不存在 | `TerminalState` | 错的是决策集，不该给它建 `.INVALID` |
| claim 改了却没同交 locator，或 evidence 被单独清空 | 载体（各 typed patch） | 旧证据不能继续绑定新事实，失败态也必须是显式 `FactEvidence` |
| 应用后任一窗口/知情/reveal/setup/规则/address/evidence 章号不在本书章集合，或 relation 截止早于起章 | `TerminalState` | 越界/倒序时间会让下发闸门失真，越界证据无法回取 |
| relation 端点、via-secret、KnowledgeEvent CharacterRef 或 `address.by` 的外键悬空 | `TerminalState` | 自由群体必须走 GroupRef，不能靠伪造 `char_id` 绕过 |
| 应用后仍有 HARD | `.INVALID` + `GateBlocked` | 与生成路同一道闸；正式全书底座不被覆盖 |

应用后重走 `normalize_global`（含 `_prune_setups`）、当前判据与落盘闸门，旧轮进 `_rounds/`。
来源账走 `rewrite_provenance(by='--apply-edit')`：`prompts`/`model` 一字不动、`inputs` 换成
本次真读到的（含 `stage2/global_edit.json` 那条边）、标 `partial_rewrite`——这一份此后是
**混版的**，未编辑字段出自当初那次付费产出，被替换/订正的 typed facts 是人给的。

成功落盘会从新 `GlobalGrounding` 重新枚举 `global_review` 必审 surface。人工决定只在
`item_id + kind + subject_path + subject_sha256` 四者均未变时继承；拆分 secret、新增规则或重建
address 后的新 subject 一律回到 `pending`，旧复合事实的 PASS 不能跟随到新时间线。

`global_edit.json` 当前为 schema v2。它内嵌的 secret/world-rule/character 形状已增加时点与
locator，属于破坏性变更；本仓没有迁移器。旧 v1 决策集必须由人按当前正文核对后重写，代码不能
从无时间的 `character_known_by` 推出知情起章，也不能从一段自由 claim 猜一处唯一原文证据。

⚠ 把揭示章往前改到埋点章之前时，`_prune_setups` 会裁掉埋点章，而 `secret_setup_order`
（HARD）断言的正是那个裁剪计数 ⇒ **照样进 `.INVALID`**，0 成本出路是 `--renormalize`。
这不是人工路的特例，`stage2.global` 主路径在同一份数据上是同一个下场。

**路径口径**（stage2/3/4/6/7 共用 `novel/paths.py::edit_rel`）：相对路径按**当前书目录**
解释，不按进程 cwd；书目录内的绝对路径同样接受；落在书目录之外（含 `..` 穿越）是显式终态。

## 四、生成、重算与晋升

- `stage2.global`：主路径生成全书抽取；模型交自由事实 locator，代码回取正式证据；
  `--purge-role` 付费重划 `role/fate`，其窄响应不重新索要正文 locator，claim 改变时旧 evidence
  明确失效并进入可由 typed edit 修复的 `.INVALID`；
  `--resample` 仅在已有 `global_grounding.INVALID.json` 时绕过同参数缓存，付费重采整份响应；
  `--renormalize` 仅对**已符合当前 schema**的产物重算代码派生字段；
  `--apply-edit <路径>` 吃 `global_edit.json` 落 Stage2 typed facts 的 0 成本人工订正（见 §三之二）；
  `--emit-review-template` stdout-only 从当前 `global_review.json` 投影逐项 hash/evidence，
  默认全部 `UNVER`，原样应用也不会打开事实闸。
- `stage2.blueprint`：逐章生成；默认 `extraction-ir-v2` 每章发两相请求——第一相同时列
  `micro_events`（完整回忆层）与 `beats`（因果拍压缩层），两列止段严格递增且落到章尾，
  beat 的止段必须取自 micro-event 止段；第二相只能在代码冻结的 beat 边界上填
  time layer、地点、hinge、叙述信息与可见 `char_id`。`--chapters` 定向付费重生成；
  `--renormalize` 重算当前 schema 的原文定位与人物引用；`--check-only` 联合读取两份产物复检。
  `extraction-ir-v3.1` 把召回脚手架和细节合在每章一次响应中，`micro_events` 只交短
  `cue`、编译前丢弃；narration 选择本章可见 `char_id`，hinge 优先选择确定性原文候选 id、
  候选覆盖不到仍交唯一 phrase。三种现役契约的拓扑显式登记在 `ContractSpec.topology`，预览、整跑与
  定向修复共用该分派，不从可空 system 字段猜。
- 正式写入前先跑严格载体校验与联合 HARD 闸门；失败响应进入 `.INVALID`，不会覆盖正式产物。
- 本层没有旧 schema 迁移入口、字段别名或双形状读取路径。GlobalGrounding v1/v2 只由
  `stage2.global` 付费重建；GlobalEdit v1 只由人重写为 v2。

`--resample` 必须单独执行，并使用 `refresh-write`：不读同参数缓存，但合格的新响应仍写回缓存，
避免下一次正常运行再次付费。请求或响应契约失败发生在写盘前，当前正式稿与问题稿都不动；新候选
仍命中 HARD 时，旧 `.INVALID` 先进入 `_rounds/`、新问题稿接替原位，正式稿保持原字节；新候选
通过时，旧正式稿和旧问题稿都进入 `_rounds/`。每次真实调用与缓存命中仍由同一份成本账记录。

## 五、现行判据

当前注册表共 **77 条**：**43 hard、16 info、16 metric、2 unver**。两个 Stage 2 节点共用
同一张注册表。（2026-08-20 随 `extraction-ir-v1` 契约新增 `hinge_ambiguous`（HARD）与
`place_inherit_without_base`（INFO）；2026-08-21 随 `extraction-ir-v1.1` 再增
`place_explicit_unlocatable`（INFO）。三条在 `bounded-fidelity-v3` 产物上结构性不产出，
空行是「不适用」不是绿灯。2026-08-23 的 v2 没有增删判据：它收窄的是响应契约与调用拓扑，
最终产物仍走同一张表。2026-08-24 的 GlobalGrounding v3 新增
`relation_visibility_mismatch`（INFO）、`global_evidence_unlocated` 与
`canon_chapter_refs_dangling`（HARD）。）

| 分组 | 当前规则 id |
|---|---|
| 产物与章对齐 | `upstream_missing` · `section_id_leak` · `chapter_coverage` · `chapter_order` |
| 人物与称谓 | `canonical_unique` · `character_unattested` · `address_by_unresolved` · `address_from_out_of_range` · `address_from_too_early` · `address_bare_pronoun` · `address_all_pronoun` · `address_has_pronoun` · `address_selfref` · `address_missing` · `address_unlocatable` |
| 身份与结局 | `role_missing` · `role_leaks_fate` · `role_repeats_name` · `hedge_words` · `distinguisher_lonely` · `role_fate_unverifiable` · `fate_report` |
| 规则、秘密、关系与弧线 | `world_rule_no_desc` · `canon_chapter_refs_dangling` · `world_rule_names_person` · `secret_setup_order` · `secret_cold_reveal` · `secret_known_by_dangling` · `reveal_unmarked` · `secrets_absent` · `relation_dangling` · `relation_visibility_mismatch` · `global_evidence_unlocated` · `conflict_stage_coverage` |
| 拍与区间 | `beat_id_unique` · `beat_id_format` · `span_unlocatable` · `verbatim_failed` · `edge_hole` · `beats_collapsed` · `beats_coarse` · `place_inherit_without_base` · `place_explicit_unlocatable` |
| 保真材料 | `hinge_cross_chapter` · `hinge_in_hole` · `hinge_anchor_void` · `hinge_ambiguous` · `hinge_cascade` · `hinge_drift` · `chapter_no_hinge` |
| 在场与叙述 | `time_layer_no_present` · `present_ids_dangling` · `present_names_invented` · `present_continuity` · `present_ungrounded` · `present_early_named` · `event_early_name` · `pronoun_surface_unattested` · `narration_multi` |
| 范围、必填与返工 | `scope_violation` · `event_empty` · `summary_empty` · `repair_cache_policy` |
| 无条件读数 | `narration_voices` · `time_layer_mix` · `place_moves` · `present_names_ambiguous` · `present_names_unlisted` · `present_names_pronoun` · `present_names_late` · `present_broad` · `batch_density` · `context_scope` · `span_coverage` · `span_coverage_per_chapter` · `uncovered_holes` · `hinge_density` |

机器可读修复路由共 **38 条**：

- `stage2.blueprint --chapters`: 17 条；
- `stage2.blueprint --patch`: 1 条（`hinge_ambiguous`——字段级候选选择，量级是条不是章）；
- `stage2.blueprint --renormalize`: 1 条；
- `stage2.global --resample`: 17 条；
- `stage2.global --purge-role`: 1 条；
- `stage2.global --renormalize`: 1 条。

判据的权威定义在 `contracts/judges/stage2.py`，注册期会验证规则 id 唯一、严重度、需求落点、
节点与 flag 的存在性。报告形状见 [`_report.md`](./_report.md)。

三份现役契约的选择口、`--patch` 修复阶梯，以及退役实验臂的历史 A/B 读数，见
`docs/stage2_contract_experiment.md`（操作手册与实验档案）。
