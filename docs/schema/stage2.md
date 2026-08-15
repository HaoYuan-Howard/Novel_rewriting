# Stage 2 当前契约

Stage 2 产出两份严格 JSON，另收一份人产的决策集：

| 路径 | 模型 | 生产节点 | 内容 |
|---|---|---|---|
| `stage2/global_grounding.json` | `GlobalGrounding` | `stage2.global` | 全书人物、关系、设定、规则、秘密与弧线 |
| `stage2/chapter_blueprints.json` | `ChapterBlueprints` | `stage2.blueprint` | 逐章摘要、精确原文区间、事件、保真材料与叙述信息 |
| `stage2/global_edit.json` | `GlobalEdit` | 人工输入 | secret 订正决策集（`stage2.global --apply-edit` 吃它） |

两份正式产物都使用当前 Pydantic `StrictModel`，未知字段拒收。运行时不升级旧形状，也不为
缺失字段补默认值；需要保留的输入必须先按本文件的当前 schema 重新生成。

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
| `fate` | `str｜null` | 结局信息单独承载 |

`Address` 的 `surface`、`by`、`context`、`from_chapter` 全部必填；`context` 只允许
`address｜reference`。`from_chapter` 不得早于该字面在原文首次出现的章节。

`Relation` 的 `relation_id`、`subject_id`、`object_id`、`predicate`、
`valid_from_chapter`、`valid_to_chapter` 全部必填，关系两端使用 `char_id`。

### 1.2 设定、规则与秘密

- `Setting` 只含必填的 `era` 与 `worldview`。
- `WorldRule` 含 `rule_id`、闭合枚举 `kind` 与非空 `versions[]`；正文唯一落点是
  `WorldRuleVersion.desc`，每个版本带 `effective_from_chapter`。
- `Secret` 含 `secret_id`、`fact`、`character_known_by[]`、`setup_chapters[]` 与
  `reader_reveal_events[]`。每个揭示事件只含必填 `chapter`。
- `setup_chapters` 必须严格早于最早揭示章；落盘前的裁剪记录在来源账中。

### 1.3 弧线

- `ConflictStage`：`stage` + `chapters[]`。
- `ArcTrack`：`subject`、`dimension`、`change_type`、`trajectory`。
- `Arc` 的 `climax` 与 `ending` 均为必填非空文本。

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
| `recovery` | `exact｜normalized｜lcs` | 本次精确回取方式 |

相邻 beat 的区间不得回退；第一拍覆盖章首、最后一拍覆盖章尾。Stage 4 直接消费该区间，
不再搜索自由文本锚点。

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
| `setup_chapters_pruned` | `(secret_id, old[], new[])[]` | 秘密埋点裁剪明细 |

### 3.2 `BlueprintProvenance`

| 字段 | 类型 | 内容 |
|---|---|---|
| `scope_violations` | `str[]` | 逐章请求越出任务范围；空也必须写 |
| `batches` | `int[][]` | 恒为 `[[1], [2], …]` |
| `context` | `BlueprintContext` | 逐章上下文架构与边界 |
| `char_refs` | `CharRefs` | 八类人物引用解析计数 |
| `verbatim_rewrites` | `(beat_id, position, old, new)[]` | 归一命中明细 |
| `verbatim_recovered` | `(beat_id, position, old, recovered, ratio)[]` | LCS 回取明细 |
| `verbatim_failures` | `(beat_id, position, cause)[]` | 回取失败；非空阻断晋升 |
| `partial_regen_chapters` | `int[]` | 定向重生成章节，空也必须写 |
| `repairs` | `Repair[]` | 定向重生成账，空也必须写 |

`BlueprintContext` 固定为：

- `architecture = bounded-fidelity-v2`；
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

## 三之二、`global_edit.json`（secret 的 0 成本人工订正）

`reader_reveal_events`、知情人或 secret 事实抽错时的**契约内**修正入口。此前本层没有
任何人工决策集：`--purge-role` 只修 `role/fate`、`--renormalize` 只重算代码派生字段，
剩下的路是手改产物——既没有 schema 管形状，也在来源账上留不下「这是人改的」。

`GlobalEdit` 只有一个键 `secrets: SecretPatch[]`，每条补丁按 `secret_id` 定位：

| 字段 | 类型 | 语义 |
|---|---|---|
| `secret_id` | `secret_\d{2}` | 定位键，必须在盘上已有 |
| `fact` | `str \| null` | `null` = 这一格人没动过 |
| `character_known_by` | `str[] \| null` | 整字段替换，不做组内合并 |
| `setup_chapters` | `int[] \| null` | ⚠ `[]` 是「冷揭示」的合法值，与 `null` 不同形 |
| `reader_reveal_events` | `ReaderRevealEvent[] \| null` | 整字段替换 |

**只订正已有的 secret，不新增也不删除**：新增要凭空发明它的全部时间线（那是一次抽取），
删除等于宣称这条秘密不存在（而逐章剧透闸门按 secret 全集开）。两者都走重新抽取那条路。

四条拒收，各挡一种**不会报错**的错：

| 拒收 | 落点 | 理由 |
|---|---|---|
| 未知字段 / 空补丁 / 同一 secret 两份补丁 | 载体（`GlobalEdit`） | 拼错一个键被静默忽略时，「改过了」与「什么都没改」在盘上同形 |
| `secret_id` 不在盘上 | `TerminalState`，一个字节不落盘 | 错的是决策集本身，不该给它建一份 `.INVALID` 归档 |
| 章号不在本书章集合内 | `TerminalState`，一个字节不落盘 | 越界的揭示章让逐章剧透闸门（`R-6.3`）**永不触发**，而产物那一格看着正常 |
| 打完补丁仍有 HARD | `.INVALID` + `GateBlocked` | 与生成路同一道闸；正式全书底座不被覆盖 |

应用后重走 `normalize_global`（含 `_prune_setups`）、当前判据与落盘闸门，旧轮进 `_rounds/`。
来源账走 `rewrite_provenance(by='--apply-edit')`：`prompts`/`model` 一字不动、`inputs` 换成
本次真读到的（含 `stage2/global_edit.json` 那条边）、标 `partial_rewrite`——这一份此后是
**混版的**，`entities`/`arc` 出自当初那次付费产出，被改的 secret 是人给的。

⚠ 把揭示章往前改到埋点章之前时，`_prune_setups` 会裁掉埋点章，而 `secret_setup_order`
（HARD）断言的正是那个裁剪计数 ⇒ **照样进 `.INVALID`**，0 成本出路是 `--renormalize`。
这不是人工路的特例，`stage2.global` 主路径在同一份数据上是同一个下场。

**路径口径**（stage2/3/4/6/7 共用 `novel/paths.py::edit_rel`）：相对路径按**当前书目录**
解释，不按进程 cwd；书目录内的绝对路径同样接受；落在书目录之外（含 `..` 穿越）是显式终态。

## 四、生成、重算与晋升

- `stage2.global`：主路径生成全书抽取；`--purge-role` 付费重划 `role/fate`；
  `--renormalize` 仅对**已符合当前 schema**的产物重算代码派生字段；
  `--apply-edit <路径>` 吃 `global_edit.json` 落 secret 的 0 成本人工订正（见 §三之二）。
- `stage2.blueprint`：逐章生成；`--chapters` 定向付费重生成；`--renormalize` 重算当前
  schema 的原文定位与人物引用；`--check-only` 联合读取两份产物复检。
- 正式写入前先跑严格载体校验与联合 HARD 闸门；失败响应进入 `.INVALID`，不会覆盖正式产物。
- 本层没有旧 schema 迁移入口、字段别名或双形状读取路径。

## 五、现行判据

当前注册表共 **71 条**：**38 hard、15 info、16 metric、2 unver**。两个 Stage 2 节点共用
同一张注册表。

| 分组 | 当前规则 id |
|---|---|
| 产物与章对齐 | `upstream_missing` · `section_id_leak` · `chapter_coverage` · `chapter_order` |
| 人物与称谓 | `canonical_unique` · `character_unattested` · `address_by_unresolved` · `address_from_out_of_range` · `address_from_too_early` · `address_bare_pronoun` · `address_all_pronoun` · `address_has_pronoun` · `address_selfref` · `address_missing` · `address_unlocatable` |
| 身份与结局 | `role_missing` · `role_leaks_fate` · `role_repeats_name` · `hedge_words` · `distinguisher_lonely` · `role_fate_unverifiable` · `fate_report` |
| 规则、秘密与关系 | `world_rule_no_desc` · `world_rule_names_person` · `secret_setup_order` · `secret_cold_reveal` · `secret_known_by_dangling` · `reveal_unmarked` · `secrets_absent` · `relation_dangling` |
| 拍与区间 | `beat_id_unique` · `beat_id_format` · `span_unlocatable` · `anchor_not_verbatim` · `verbatim_failed` · `edge_hole` · `beats_collapsed` · `beats_coarse` |
| 保真材料 | `hinge_cross_chapter` · `hinge_in_hole` · `hinge_anchor_void` · `hinge_cascade` · `hinge_drift` · `chapter_no_hinge` |
| 在场与叙述 | `time_layer_no_present` · `present_ids_dangling` · `present_names_invented` · `present_continuity` · `present_ungrounded` · `present_early_named` · `event_early_name` · `pronoun_surface_unattested` · `narration_multi` |
| 范围、必填与返工 | `scope_violation` · `event_empty` · `summary_empty` · `repair_cache_policy` |
| 无条件读数 | `narration_voices` · `time_layer_mix` · `place_moves` · `present_names_ambiguous` · `present_names_unlisted` · `present_names_pronoun` · `present_names_late` · `present_broad` · `batch_density` · `context_scope` · `span_coverage` · `span_coverage_per_chapter` · `uncovered_holes` · `hinge_density` |

机器可读修复路由共 **21 条**：

- `stage2.blueprint --chapters`: 17 条；
- `stage2.blueprint --renormalize`: 2 条；
- `stage2.global --purge-role`: 1 条；
- `stage2.global --renormalize`: 1 条。

判据的权威定义在 `contracts/judges/stage2.py`，注册期会验证规则 id 唯一、严重度、需求落点、
节点与 flag 的存在性。报告形状见 [`_report.md`](./_report.md)。
