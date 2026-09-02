# Stage 2 当前契约

Stage 2 产出四份严格 JSON，另收三份人产的 typed 请求/决策集：

| 路径 | 模型 | 生产节点 | 内容 |
|---|---|---|---|
| `stage2/global_grounding.json` | `GlobalGrounding` v4 | `stage2.global` | 全书人物、关系、设定、规则、秘密、弧线及代码回取证据 |
| `stage2/blueprint_character_projection.json` | `BlueprintCharacterProjection` v1 | `stage2.global` | 从最新 schema-valid global（formal 或 `.INVALID`）与 MainText 确定性物化的、Blueprint 唯一消费的逐章称名闭集 |
| `stage2/chapter_blueprints.json` | `ChapterBlueprints` v7 | `stage2.blueprint` | 逐章语义功能、原子事实/因果/叙事关系/状态契约、精确证据区间、保真材料、舞台与叙述信息 |
| `stage2/global_review.json` | `GlobalReview` | `stage2.global` | 由当前全局底座确定性枚举的逐项人工复核；详见 `docs/schema/evaluation.md` |
| `stage2/global_review_decisions.json` | `GlobalReviewDecisionSet` v2 | 人工输入 | actor、逐项 subject hash 决定，或绑定完整 blocking scope/正文章全集的默认决定 + 逐项例外；UNVER 可显式请求完整组隔离，也可经同形 API 提交 |
| `stage2/global_edit.json` | `GlobalEdit` v3 | 人工输入 | Stage2 typed 订正决策集（`stage2.global --emit-edit-template` 生成当前骨架，`--apply-edit` 消费） |
| `stage2/blueprint_relation_edit.json` | `BlueprintRelationEdit` v1 | 人工输入 | hash-bound 的指定章完整叙事关系集合；`stage2.blueprint --apply-edit` 吃它，不是 producer 产物 |

四份正式产物与三份人工输入都使用当前 Pydantic `StrictModel`，未知字段拒收。运行时不升级旧形状，也不为
缺失字段补默认值；需要进入普通 producer 的输入必须先按本文件的当前 schema 重新生成。旧
GlobalGrounding 由 producer 重建；ChapterBlueprints v6 不迁移、不自动补空关系数组。只有它与现存
Stage4A v1 truth/mapping、原始来源边及白名单下游正式制品逐字闭合时，冻结 reader 才供状态、审批、
交付披露与零 wire 完整章 edit 使用；普通 producer、付费 repair/replan 与 Stage4B 不得消费该内存形状，
也不得回写 v6。新的 clean base 只由 `stage2.blueprint` 生成 v7。GlobalEdit v1/v2 由人按 v3
重写，本批之前已经合法的 v3 无须补 `conflict_stages_replace` 仍可原样读取。

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

### 1.3 弧线

- `ConflictStage`：`stage` + `chapters[]`；`chapters` 是逐章显式枚举，不是区间端点，
  全部阶段合起来必须让 Stage1 的每个阅读序章号恰好出现一次（`R-2.14`）。
- `ArcTrack`：`subject`、`trajectory`、`trajectory_evidence`。
- `Arc` 的 `climax` 与 `ending` 均为必填非空文本。

### 1.4 自由事实证据：唯一 candidate wire，locator 只作 0-LLM 人工恢复

唯一现役的模型 evidence wire 是 `paragraph-candidate-v1`。代码按全书阅读序只从
`paragraph_spans` 建立 `e000001` 起的连续候选表，prompt 的带 ID 正文与响应 lookup 共用该表。
模型对每个证据槽只交 `{evidence_candidate_id}`；
`GlobalGroundingCandidateResponse` / `GlobalCandidateAgentTurnResponse` 不接受 locator 或
`SourceSpan`。格式合法但没有出现在本次下发表中的 ID，会在候选 acceptance、checkpoint 与业务
写盘之前关闭式拒绝；合法 ID 由代码编译成候选行所属章与精确 `SourceSpan`，随后完整复用既有
normalize、全部 HARD、GlobalReview、provenance、正式 readback 与 adoption。候选 ID 只是一轮
请求内的 wire 引用，不进入 GlobalGrounding v4、GlobalEdit v3 或正式 provenance。

这套结构只消灭章号/locator 的模型转抄错位，不判断所选段落是否真的支持主张；后者仍由
GlobalReview 的语义复核承担。2026-08-31 产品所有者在首个实验 wire 前取消 candidate/locator A/B
与盲审，并把 candidate 收成 sole active；实验状态是 `CANCELLED_BEFORE_WIRE`，真实 provider
wire=0，没有实验 verdict，质量、token、wall 与货币成本均未验证。`locator-v1` 不再是可选模型臂、
默认或 fallback，不能把本次产品裁决写成 candidate 已通过性能或质量门。

所有人工 `GlobalEdit` v3 对自由事实仍只交 `EvidenceLocator`，这是保留的 0-LLM 恢复口：

| 字段 | 类型 | 约束 |
|---|---|---|
| `chapter_ordinal` | `int` | 必须是 Stage1 现役阅读序章号 |
| `locator` | `str` | 1～160 字的短原文定位串 |

代码先在合法指定章内定位：raw 原文唯一命中优先，否则尝试 normalized 唯一命中。指定章
多命中立即失败；只有指定章零命中时才按阅读序逐章复用同一定位函数，且仅在全书恰一候选、
任何章都没有多命中时把章号纠正为真实章。非法章不扫描，禁止 fuzzy、拼接全文或静默取首命中；
已有正式 `SourceSpan` 只按原章复验，正文漂移时不自动搬家。成功后正式产物改写成
`FactEvidence {chapter_ordinal, source: SourceSpan}`，`SourceSpan` 继续服从 §2.2 的逐字/坐标不变量。失败时仍构造
`FactEvidence {chapter_ordinal, source: null}` 并登记失败账，使问题稿能落
`global_grounding.INVALID.json`；随后可用 typed locator 重新 `--apply-edit`，或手改 `.INVALID`
后 `--revalidate`，而不是在 Pydantic 构造前崩掉、把付费结果永久锁死。
存量问题稿重判时，只对 failure 原因明确来自 locator、路径与声明章仍相同且 `source` 仍为空的槽
恢复 locator；formal `SourceSpan` 校验失败永不反向恢复，所以正文漂移不能在第二次
`--revalidate` / `--renormalize` 时被静默搬到另一章。

成功跨章纠正不写入 `GlobalProvenance`，以保持 GlobalGrounding v4 / GlobalEdit v3；正式或
`.INVALID` 字节落地后，`_service/llm_audit.jsonl` 只追加一条绑定最终文件 SHA 的
`evidence_locator_rewrite`：claim path、原章、新章与 `exact｜normalized`。该事件不含 locator、
SourceSpan 或正文。

七类必举证事实是：人物 role/fate、relation predicate、setting 两格、world-rule desc、
secret fact、每条 knowledge event、arc trajectory。模型 wire 不允许直接填写正式 `SourceSpan`；
`--purge-role` 的窄响应仍只交 role/fate，claim 改变后旧证据
会被明确置为 `source=null` 并由 HARD 送入可恢复的 `.INVALID`，不会把旧证据错误绑定到新 claim。

Stage4/5/6 的实际材料与节点 freshness 投影共用代码剥掉全部 evidence：坐标或证据文本单独修订
不会触发无关付费重跑，也不会把原文证据作为额外故事材料外发给下游模型。

### 1.5 agent 私有的固定 turn 信封

agent executor 的 `stage2.global` 在整个 logical/provider session 内固定唯一的
`GlobalCandidateAgentTurnResponse` 联合信封；direct 单发同样只接受
`GlobalGroundingCandidateResponse`，不存在 locator agent 信封或 `evidence_patch` fallback。

- 首轮与普通业务 HARD 使用 `payload.kind=full_candidate`；候选 ID
  格式/成员失败仍在 acceptance、checkpoint 与业务写盘之前关闭式拒绝；普通业务 HARD 继续完整重交
  `GlobalGroundingCandidateResponse`。只有 HARD 集合精确等于 `role_leaks_fate` 时，代码才从生产判据
  取得完整人物 allowlist，开放 `role_fate_patch` 窄修：每个人必须原子提交 `role`、`role_evidence`、
  `fate`、`fate_evidence` 四格，并同时绑定当前 raw candidate SHA-256 与本轮 evidence candidate table
  SHA-256。人物集合必须与 allowlist 精确相等，未知 evidence ID、旧 SHA、role no-op、空 fate 或
  四字段掩码之外任何变化都关闭式拒绝。`candidate_sha256` 只绑定信封内、归一前
  `GlobalGroundingCandidateResponse` 的 canonical JSON，不含 turn envelope、provenance 或正式
  artifact；apply 前重新计算当前 base SHA 与 evidence table SHA。

代码在候选副本应用后证明非授权投影不变，再完整重跑 response schema、normalize、全部 HARD 与
downstream guard。中间业务候选不写 formal / `.INVALID` / `_rounds`。role/fate 的
`candidate_patch_applied` 只记 patch kind、attempt、前后 raw SHA、candidate table SHA 与改动格数，
不记人物、字段值或正文，也不冒充 adoption。role/fate 窄修最多接受两次；第一次开始后状态 sticky，第二次仍红、
引入 mixed/其它 HARD 或预算耗尽都直接按当前候选终态化，不得借通用剩余 turn 回到
`full_candidate`。最终经既有 finalize 写盘与正式 readback 后，唯一 `artifact_adoption` 才绑定最终
accepted attempt。候选始终归 `Stage2GlobalAgent` 所有，外层 Commander 不接收或改写。

### 1.6 Global agent checkpoint、拒稿 custody 与提交 journal

`stage2.global` 的 agent run 使用 `recovery_policy=stage2-global-v1`。每份通过固定信封与当轮 dynamic
validator 的 stdout 都先写 append-only revision checkpoint，再写无正文 patch 审计；checkpoint 精确
保存合并后的完整 typed candidate、turn kind、可选 base candidate、两者 SHA、MainText / candidate
table SHA、accepted role patch 计数，以及通用 acceptance/session/ledger 身份。恢复时必须重新通过当前
schema、同一 patch applicator、候选表成员闸、业务 HARD 与 MainText currentness；不能把旧 provider
session 冒充 live resume。fresh 与 restored 路共用 `Stage2GlobalAgent._repair_transition`：role/fate
accepted 计数从 checkpoint 恢复，最多两次和 sticky/no-full-re-entry 语义不因 kill 点改变。

若最后一次付费 stdout 已通过 schema、却被当轮 dynamic validator 拒绝，custody 必须绑定当轮
`expected_turn_kind`、完整 base candidate/SHA 与**拒绝前** accepted role patch 计数，并与最新 durable
checkpoint 对证；人工 typed replacement 仍重放同一个 validator 与 applicator，role/fate 计数只能
`0→1` 或 `1→2`，不能把第二次窄修伪装成第一次。fresh/restored 两路在业务状态机已终止时都以
`allow_resume=false` 关闭通用续轮，最后才写一次 `.INVALID`。

正式 Global bundle 另由 `novel/llm/stage2_global_runs.py` 的同书线程/进程所有权与
`prepared→正式 Global/review/projection 写入或退休 INVALID→typed readback/freshness→唯一
artifact adoption→committed` journal 收口。foreign `prepared` 会在任何新 run 覆盖前关闭；kill 后原
run 只能按已登记 touched-path SHA 幂等补齐 review/projection/readback/adoption/committed，不得再造新轮。
checkpoint、custody 与 journal 都是
`<base>/_service/runs/<sid>/stage2/<run_id>/global/` 的恢复 sidecar，不是正式
artifact、普通缓存或公开 API schema。旧书内 `_stage2_runs` 只读恢复，继续写入先原字节接入 service run。

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

Commander 的 Blueprint workpack 使用 `BLUEPRINT_REVIEW_CONTRACT=stage2-blueprint-review-v4`。
每章可见业务面必须原样投影完整 `narrative_relations`（含 kind、enforcement 与各 typed 端点），并把
这组可见关系计入 `review_identity_sha256`；不能只在 v7 artifact 中保存、却从人审包里漏掉。代码/
规则与机械复检仍只进入 `validation_seal_sha256`，公开包不再输出其原料 `code_version` /
`rules_fingerprint`。关系或其它可见 finding 变化要求重读，纯 validation 变化在免费复检后可复活
原 raw `seen.read`。公开提交继续只收 `bpr3_…` token；旧 `bpr2_…` 包只可留作历史阅读，不能
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

`Hinge` 含 `hinge_id`、`kind` 与 `source`。所属 beat 由父级 `Beat.hinges[]` 唯一表达，不重复存外键。正式 id 形如
`h<章:02d>_<序:03d>`，由代码编号；`kind` 取值为 `硬数字｜要害台词｜物件｜因果前提`；
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
  `extraction-ir-v2`、`extraction-ir-v3.1`；当前默认由同文件
  `BLUEPRINT_ARCHITECTURE` 唯一指定为 v3.1；
  定向修复按基线产物的架构选契约，不把存量产物伪装成当前默认；
- 必填 `stable_prefix`、`past_neighbor_chars_max`、`continuity_chars_max`、
  `past_names_chars_max`。

每条 `Repair` 必填：`chapter_ordinal`、`failure_items[]`、`failure_evidence`、`user_prompt`、
`output`、`model`、`cache_policy`、`fields[]`、`before_metrics`、`after_metrics`；其中指标均为
`beats`、`fact_atoms`、`hinges` 三个计数。

## 三之二、`global_edit.json`（Stage2 typed 事实的 0 成本人工订正）

全局 typed 事实抽错、漏抽或 evidence 回取失败时的**契约内 0 LLM 修正入口**。它覆盖
人物 role/fate/枚举、relation、setting、secret、world rule、冲突阶段、arc track 与 address；
逐格 patch 用稳定 ID/下标改已有记录，完整 replace 则表达新增、删除或复合事实拆分。
所有会改自由 claim 的入口必须同交新 locator，不能让旧 evidence 继续给新 claim 背书。

| 键 | 类型 | 语义 |
|---|---|---|
| `secrets` | `SecretPatch[]` | 按已有 `secret_id` 改 fact+locator、knowledge events、setup 或 `first_reveal_chapter` |
| `world_rules` | `WorldRulePatch[]` | 按已有 `rule_id` 改 kind 或带 locator 的完整 versions |
| `characters` | `GlobalCharacterPatch[]` | 按 `char_id` 改枚举、role+locator、fate+locator；显式 `fate=null` 同步清空证据 |
| `relations` | `RelationPatch[]` | 按 `relation_id` 改端点、predicate+locator、visibility 或有效窗口 |
| `setting` | `SettingPatch｜null` | 逐格改时代/世界观；每个 claim 与自己的 locator 成对 |
| `arc_tracks` | `ArcTrackPatch[]` | 按稳定 `track_index` 改弧线格；trajectory 与 locator 成对 |
| `addresses` | `AddressContextPatch[]` | 按 `char_id + address_index` 改已有项的 `context` |
| `characters_replace` | `dict[char_id, CharacterResponse] \| null` | 完整替换人物集合；可新增/删除人物，并为 role/fate 提交 locator；用于 `character_set_complete` 的漏抽恢复 |
| `secrets_replace` | `SecretResponse[] \| null` | 完整替换 secrets；可拆分、新增、删除，事实与知情事件交 locator |
| `world_rules_replace` | `WorldRuleResponse[] \| null` | 完整替换 world rules；可新增、删除，desc 交 locator |
| `relations_replace` | `RelationResponse[] \| null` | 完整替换 relations；ID 唯一，predicate 交 locator |
| `conflict_stages_replace` | `ConflictStage[] \| null` | 完整重画 `arc.conflict_stages`；至少一段，缺/重/越界与 producer 的 `conflict_stage_coverage` 共用同一覆盖量尺，并在赋值前原子拒绝 |
| `arc_tracks_replace` | `ArcTrackResponse[] \| null` | 完整替换 arc tracks；至少一项，trajectory 交 locator |
| `arc_replace` | `ArcResponse \| null` | 完整替换 conflict stages、climax、ending 与 arc tracks；用于整组 `arc_analysis` 恢复 |
| `addresses_replace` | `CharacterAddressReplacement[]` | 每项按 `char_id` 完整替换其 `address`；列表至少一条，可改 `surface/by/context/from_chapter` |
| `post_edit_review` | `GlobalEditReviewAttestation \| null` | 第二次 Commander 调用对本次精确 edit 的自审绑定：首轮 source、正文章全集、默认 PASS、理由及可选安全隔离组；单独出现不算编辑 |

集合 replacement 的 `null` 表示「没动」，允许为空的集合用显式 `[]` 表示「经复核删除全集」；
两者不能合成默认空列表。`characters_replace`、`conflict_stages_replace`、
`arc_tracks_replace`、`arc_replace` 与 `addresses_replace[].address` 不允许空，因为正式人物集合、
`Arc.conflict_stages` / `Arc.arc_tracks` / `Character.address` 至少一项。相同集合不能同时
patch+replace；`characters_replace` 不能与人物/address patch 或 address replacement 混用，
`arc_replace` 不能与任一 arc 局部 patch/replacement 混用。

所有载体/目标/外键/章号/窗口拒收都发生在调用者对象修改、正式写盘、`.INVALID` 与
`_rounds/` 归档**之前**：实现先在深拷贝候选上应用全部 patch/replace，再对**应用后的完整图**
校验外键、章号、窗口与证据；这些校验任一失败时调用者对象保持不变。随后才把候选交给同一
归一、正式载体与判据闸门；这一段只操作本轮局部对象，即使最终载体或 HARD 拒收也不会提前写盘。

| 拒收 | 落点 | 理由 |
|---|---|---|
| 未知字段 / 空编辑 / 重复 ID 或 subject | 载体（`GlobalEdit`） | 顺序不能决定哪份人工决定生效 |
| 同一 secret/world-rule/relation/arc-track 集合既逐项 patch 又完整 replace | 载体（`GlobalEdit`） | 两个写口争同一 subject，结果依赖应用顺序 |
| 完整人物/arc replacement 与同组任一局部写口混用 | 载体（`GlobalEdit`） | 完整替换会改变成员或下标身份，必须二选一 |
| 同一人物的 address 既按下标 patch 又完整 replace | 载体（`GlobalEdit`） | replacement 会改变下标含义，必须二选一 |
| 逐项 patch 的目标不存在；replacement 的人物目标不存在 | `TerminalState` | 错的是决策集，不该给它建 `.INVALID` |
| `conflict_stages_replace` 缺章、重章或越界 | `TerminalState` | 错的是 replacement；只在该字段出现时用 `checks.measure_conflict_stage_coverage` 预检，通过前不赋给候选，也不动正式稿、`.INVALID` 或 `_rounds/` |
| claim 改了却没同交 locator，或 evidence 被单独清空 | 载体（各 typed patch） | 旧证据不能继续绑定新事实，失败态也必须是显式 `FactEvidence` |
| 应用后任一窗口/知情/reveal/setup/规则/address/evidence 章号不在本书章集合，或 relation 截止早于起章 | `TerminalState` | 越界/倒序时间会让下发闸门失真，越界证据无法回取 |
| relation 端点、via-secret、KnowledgeEvent CharacterRef 或 `address.by` 的外键悬空 | `TerminalState` | 自由群体必须走 GroupRef，不能靠伪造 `char_id` 绕过 |
| 应用后的完整候选仍有 HARD（包括未由本次 replacement 引入的既有冲突覆盖错误） | `.INVALID` + `GateBlocked` | 与生成路同一道闸；正式全书底座不被覆盖，其他 typed edit 仍可逐步修复既有问题稿 |

应用后重走 `normalize_global`（含 `_prune_setups`）、当前判据与落盘闸门，旧轮进 `_rounds/`。
来源账走 `rewrite_provenance(by='--apply-edit')`：`prompts`/`model` 一字不动、`inputs` 换成
本次真读到的（含 `stage2/global_edit.json` 那条边）、标 `partial_rewrite`——这一份此后是
**混版的**，未编辑字段出自当初那次付费产出，被替换/订正的 typed facts 是人给的。

HTTP/Commander 的一次性输入落在 `_service/edits/<sid>/global-edit-<uuid>.json`；permit 与 producer
都只接受当前书或这个**同 SID** 根，scratch/兄弟 SID 不构成第三种输入语义。producer 先解码 typed
edit、在内存应用并归一；no-op 在 canonical 写入前拒绝。确有变化时才以保留字段出现性的
`exclude_unset` 形状原子写入书内 `stage2/global_edit.json`，后续 provenance 只绑定这份长期可复算
输入；同步执行返回后可精确删除一次性 UUID 文件。

成功落盘会从新 `GlobalGrounding` 重新枚举 `global_review` 必审 surface。人工决定只在
`item_id + kind + subject_path + subject_sha256` 四者均未变时继承；拆分 secret、新增规则或重建
address 后的新 subject 一律回到 `pending`，旧复合事实的 PASS 不能跟随到新时间线。

Agent 的 Stage2 global 语义闭环最多使用两次真实 Commander 调用。首轮完整审阅并落 hash-bound
PASS/FAIL/UNVER；第二轮是唯一返工，必须在同一 provider session 中一次提交完整 typed edit 和
`post_edit_review`。producer 先在内存应用、归一和重枚举，只把证明展开到**本次 edit 新打开的
blocking pending 项**；未变化的旧 FAIL/UNVER 继续保持原状态，不能被默认 PASS 洗绿。证明绑定的
source 或正文章全集漂移、存在未消解 blocking、或者隔离组不属于本次新打开且登记可隔离的
secret/relation/world-rule 时，整份 Global/review 在写盘前原子拒绝，并明确不启动第三轮 Commander。
没有 `post_edit_review` 的公开人工 GlobalEdit 仍按旧语义重枚举 pending，不代替人工复核。

`global_edit.json` 当前为 schema v3。`characters_replace`、`arc_replace`、
`post_edit_review` 与 `conflict_stages_replace` 都是可缺的兼容加法，旧 v3 决策集仍能原样读取，
因此没有为它升版。它与 GlobalGrounding v4 一样只保留
`first_reveal_chapter` 和无标签弧线；本仓没有迁移器，只有旧 v1/v2
决策集必须由人核对后按 v3 重写。

⚠ 把揭示章往前改到埋点章之前时，`_prune_setups` 会裁掉埋点章，而 `secret_setup_order`
（HARD）断言的正是那个裁剪计数 ⇒ **照样进 `.INVALID`**，0 成本出路是 `--renormalize`。
这不是人工路的特例，`stage2.global` 主路径在同一份数据上是同一个下场。

**路径口径**：相对路径按**当前书目录**解释，不按进程 cwd；书目录内的绝对路径同样接受。
Stage2 GlobalEdit/Review 决策还接受 `_service/edits/<same sid>` 的一次性绝对路径；其他书、scratch
与任意根外路径都显式拒绝。

## 四、生成、重算与晋升

- `stage2.global`：主路径只接受本次段落候选 ID，由代码铸成正式 `SourceSpan`；locator 不再是
  模型选择面，只保留给下述 0-LLM 人工恢复；
  `--purge-role` 付费重划 `role/fate`，其窄响应不重新索要正文 locator，claim 改变时旧 evidence
  明确失效并进入可由 typed edit 修复的 `.INVALID`；
  `--resample` 仅在已有 `global_grounding.INVALID.json` 时绕过同参数缓存，付费重采整份响应；
  `--renormalize` 仅对**已符合当前 schema**的产物重算代码派生字段；
  `--emit-edit-template` stdout-only 按当前 FAIL/未处置 UNVER 生成可执行但默认 no-op 的 v3 骨架；
  `--apply-edit <路径>` 吃 `global_edit.json` 落 Stage2 typed facts 的 0 成本人工订正（见 §三之二）；
  `--emit-review-template` stdout-only 从当前 `global_review.json` 投影逐项 hash/evidence，
  默认全部 `UNVER`，原样应用也不会打开事实闸。每次 formal 或
  schema-valid `.INVALID` 写回都同步确定性物化称名投影；旧盘面首次进入
  Blueprint 时也会用同一纯函数补齐。
- `stage2.blueprint`：逐章生成；默认 `extraction-ir-v3.1` 每章发一次请求，把召回脚手架和
  最终细节合在同一响应中；`micro_events` 只交短 `cue`、编译前丢弃，narration 选择本章可见
  `char_id`，hinge 优先选择确定性原文候选 id，候选覆盖不到仍交唯一 phrase。显式 v2 回退臂
  每章发两相请求：第一相同时列 `micro_events`（完整回忆层）与 `beats`（因果拍压缩层），
  两列止段严格递增且落到章尾，beat 止段必须取自 micro-event 止段；第二相只能在代码冻结的
  beat 边界上填 time layer、地点、hinge、叙述信息与可见 `char_id`。`--chapters` 定向付费
  重生成；`--renormalize` 重算当前 schema 的原文定位，不会在无显示名时猜测人物 ID；
  `--check-only` 联合读取两份业务产物复检。Blueprint 的 prompt、dynamic
  validator、compile 与 provenance 只读 MainText + 称名投影，不伪造一条 canonical
  global 输入边。两种现役契约的拓扑显式登记在 `ContractSpec.topology`，预览、整跑与定向修复共用
  该分派，不从可空 system 字段猜。`--emit-review-workpack` 零调用输出绑定当前 Blueprint、
  MainText、`stage2-blueprint-review-v4` 与完整 `narrative_relations`，并把免费机械复检另记为
  validation seal；对外只交 opaque review token。异常章先排，但不省略正常章、关系或语义消费字段，
  也不创建审批、决定或新 artifact。
- 正式写入前先跑严格载体校验与联合 HARD 闸门；失败响应进入 `.INVALID`，不会覆盖正式产物。
- producer/正式 artifact registry 没有旧 schema 迁移入口、字段别名或双形状读取路径；旧生成产物
  默认由 producer 重建。唯一例外是上文精确 Stage4A-bound v6 的冻结只读证明，它不返回 current
  `ChapterBlueprints`、不写回也不开放付费 consumer。GlobalEdit v1/v2 只由人重写为 v3；旧 v3
  即使没有新增的可缺字段也仍可读。

`--resample` 必须单独执行，并使用 `refresh-write`：不读同参数缓存，但合格的新响应仍写回缓存，
避免下一次正常运行再次付费。请求或响应契约失败发生在写盘前，当前正式稿与问题稿都不动；新候选
仍命中 HARD 时，旧 `.INVALID` 先进入 `_rounds/`、新问题稿接替原位，正式稿保持原字节；新候选
通过时，旧正式稿和旧问题稿都进入 `_rounds/`。每次真实调用与缓存命中仍由同一份成本账记录。

## 五、现行判据

当前注册表共 **68 条**：**39 hard、16 info、11 metric、2 unver**。两个 Stage 2 节点共用
同一张注册表。本次契约收缩同步删除只服务展示名、摘要、批次和已退役地点模式的判据。

| 分组 | 当前规则 id |
|---|---|
| 产物与章对齐 | `upstream_missing` · `section_id_leak` · `chapter_coverage` · `chapter_order` |
| 人物与称谓 | `canonical_unique` · `character_unattested` · `address_by_unresolved` · `address_from_out_of_range` · `address_from_too_early` · `address_bare_pronoun` · `address_all_pronoun` · `address_has_pronoun` · `address_selfref` · `address_missing` · `address_unlocatable` |
| 身份与结局 | `role_missing` · `role_leaks_fate` · `role_repeats_name` · `hedge_words` · `fate_hedge_words` · `distinguisher_lonely` · `role_fate_unverifiable` · `fate_report` |
| 规则、秘密、关系与弧线 | `world_rule_no_desc` · `canon_chapter_refs_dangling` · `world_rule_names_person` · `secret_setup_order` · `secret_cold_reveal` · `secret_known_by_dangling` · `reveal_unmarked` · `secrets_absent` · `relation_dangling` · `relation_visibility_mismatch` · `global_evidence_unlocated` · `conflict_stage_coverage` |
| 拍与区间 | `beat_id_unique` · `beat_id_format` · `span_unlocatable` · `verbatim_failed` · `edge_hole` · `beats_collapsed` · `beats_coarse` |
| 保真材料 | `hinge_cross_chapter` · `hinge_in_hole` · `hinge_anchor_void` · `hinge_ambiguous` · `hinge_cascade` · `hinge_drift` · `chapter_no_hinge` |
| 在场与叙述 | `time_layer_no_present` · `present_ids_dangling` · `present_continuity` · `present_ungrounded` · `present_early_named` · `event_early_name` · `pronoun_surface_unattested` · `narration_multi` |
| 范围与必填 | `scope_violation` · `fact_atom_empty` |
| 无条件读数 | `narration_voices` · `time_layer_mix` · `place_moves` · `present_broad` · `context_scope` · `span_coverage` · `span_coverage_per_chapter` · `uncovered_holes` · `hinge_density` |

机器可读修复路由共 **33 条**：

- `stage2.blueprint --chapters`: 15 条；
- `stage2.blueprint --patch`: 1 条（`hinge_ambiguous`——字段级候选选择，量级是条不是章）；
- `stage2.global --resample`: 15 条；
- `stage2.global --purge-role`: 1 条；
- `stage2.global --renormalize`: 1 条。

`global_evidence_unlocated` 不再把整份 `--resample` 暴露为默认路由：candidate wire 的 ID 在
acceptance 前关闭式校验并直接编译成 `SourceSpan`；普通业务 HARD 仍完整重交，只有 HARD 集合精确为
`role_leaks_fate` 时才开放 role/fate 窄修。终态 evidence 问题转人工 typed edit / `--revalidate`，
显式整份重采仅作最后手段。
`conflict_stage_coverage` 也不再默认指向付费 `--resample`；它转人工用
`GlobalEdit.conflict_stages_replace` + `stage2.global --apply-edit` 完整重画（0 LLM），
不在没有稳定行 ID 的数组上伪造逐项机器修复。
`hedge_words` 只检查会进入写作/改名材料的 `role` 与 `canonical`；确定判断「不可能」不命中，
「不太可能」「并非不可能」仍命中。`fate` 使用独立的 `fate_hedge_words` INFO，只披露、不阻断。
`beats_collapsed` 继续 HARD；`beats_coarse` 是同书长尾 INFO，没有付费重生成路由。

判据的权威定义在 `contracts/judges/stage2.py`，注册期会验证规则 id 唯一、严重度、需求落点、
节点与 flag 的存在性。报告形状见 [`_report.md`](./_report.md)。

两份现役 Blueprint 契约的选择口、`--patch` 修复阶梯，以及退役 Blueprint 实验臂的历史 A/B 读数，见
`docs/stage2_contract_experiment.md`（操作手册与实验档案）。
