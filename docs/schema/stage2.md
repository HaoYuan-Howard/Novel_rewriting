# Stage 2 当前契约

Stage 2 产出两份严格 JSON：

| 路径 | 模型 | 生产节点 | 内容 |
|---|---|---|---|
| `stage2/global_grounding.json` | `GlobalGrounding` | `stage2.global` | 全书人物、关系、设定、规则、秘密与弧线 |
| `stage2/chapter_blueprints.json` | `ChapterBlueprints` | `stage2.blueprint` | 逐章摘要、精确原文区间、事件、保真材料与叙述信息 |

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

## 四、生成、重算与晋升

- `stage2.global`：主路径生成全书抽取；`--purge-role` 付费重划 `role/fate`；
  `--renormalize` 仅对**已符合当前 schema**的产物重算代码派生字段。
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
