# Stage 4 当前契约

Stage 4 把 Stage 2 的事实底座与章节蓝图收口成真正的叙事结构计划和写作上下文。代码先把
过碎 beat 合成不可再拆的源内容单元；规划调用决定章内叙事入口、信息次序、写作单元分组、
场景/概述承载、时间衔接和预算。引用展开与写作上下文仍由确定性代码生成。

| 路径 | 模型 | 生产节点 | 角色 |
|---|---|---|---|
| `stage4/plan_chapters.json` | `PlanChapters` | `stage4.plan` | 当前正式计划 |
| `stage4/plan_edit.json` | `PlanEdit` | 人工输入 | 完整计划修订集 |
| `stage4/writer_context.json` | `WriterContext` | `stage4.context` | Stage 5/6 的唯一写作材料 |
| `stage4/writer_context.identity.json` | `WriterContext` | `stage4.plan --identity` | 确定性组装基线 |

本层四份产物**都是 schema v1**（全仓恒 v1，见 `_README.md`「版本与路径注册表」）。
未知字段拒收，运行时只读 `ARTIFACT_SPECS` 声明的当前版本——**本仓没有迁移机制**，
也不根据历史常量切换预算语义。

## 一、规划调用边界

模型响应使用 `PlanResponse`：

| 字段 | 类型 | 约束 |
|---|---|---|
| `reconstruction_strategy` | `str` | 非空的全书重构意图 |
| `output_chapters` | `PlanChapterResponse[]` | 非空，覆盖全部源章 |

每个 `PlanChapterResponse` 必填：

- `output_ordinal` 与唯一的 `from_source_ordinals[0]`；
- typed `chapter_goal`、`open_hook_goal`、`end_hook_goal`（形状是 `PlanGoalResponse`）；
- 非空 `segments[]`。

模型响应中的每个写作单元包含 `plan_beats[]`、`narrative_role`、`delivery`、`transition`
与正整数 `budget_chars`。`plan_beats[]` 引用下发的 `pNN_NN` 源内容单元，数组顺序就是
重构后的叙述顺序。

**响应侧的四个枚举字段带前缀，与产物侧不是同一张表**（2026-08-22 起）：

| 字段 | 响应侧取值 | 产物侧取值 |
|---|---|---|
| `narrative_role` | `R_COLD_OPEN` … `R_BRIDGE` | `cold_open` … `bridge` |
| `delivery` | `D_SCENE` / `D_SUMMARY` | `scene` / `summary` |
| `transition` | `X_START` … `X_RETURN` | `start` … `return` |
| goal 的 `intent` | `G_ESTABLISH` … `G_RESOLVE` | `establish` … `resolve` |

理由：这四张表在响应 JSON 里**串行相邻**且语义两两邻近，模型会串表，而串出来的值
**在另一张表里是合法的** ⇒ schema 闸只能在整份响应收完之后才发现。实测代价两次——
交接时 `delivery.summary` 串进 `narrative_role`；2026-08-22 一次全书规划出现
`intent='turn'`×6 章、`narrative_role='recall'`（从骨架下发面抄的），整条降档梯走到底、
**6 次 wire 全被内容闸拒、completion 86 150 全白花、0 产物**。手法与 stage2
`extraction-ir-v1` 的 `T_PRESENT`/`P_EXPLICIT` 同型（那次读数是非法 enum 归零）。

⚠ **产物侧取值一个字都不许出现在下发面上**——出现了模型就会照抄。
`tests/test_stage4.py::test_no_artifact_side_enum_value_reaches_the_planner_prompt` 钉着。
⚠ **映射只有一个落点**：`novel/stages/stage4/measure.py::decode_plan_response`，
反方向 `encode_plan_baseline` 只给定向修复的「现有章基线」用（不回编码，同一份 user 里
system 要 `D_SCENE`、基线印着 `scene`，模型照基线抄就被拒，而那一拒要花钱才看得见）。
⚠ **已是产物值的原样过，不抛**：`--apply-edit` 的人工稿与 `--revalidate` 的存量产物
从来没有前缀，两端的 schema 闸各守各的。

`adopt_plan_response`（= `decode_plan_response` + `expand_plan_beats`）是模型响应进入
产物形状的**唯一入口**：确认编号存在、不重复、没有漏项，再展开成正式 `from_beats[]`，
并按规划顺序分配稳定 `slot=s01...`；不按源序重新排序。

## 二、`plan_chapters.json`

顶层只有三个必填字段：

| 字段 | 类型 | 内容 |
|---|---|---|
| `reconstruction_strategy` | `str` | 模型给出的全书重构意图 |
| `output_chapters` | `PlanChapter[]` | 当前正式逐章计划 |
| `_provenance` | `PlanProvenance` | LLM 来源账与分组参数 |

`PlanChapter`：

| 字段 | 类型 | 约束 |
|---|---|---|
| `output_ordinal` | `int` | 从 1 连续 |
| `from_source_ordinals` | `int[1]` | 唯一值必须等于 `output_ordinal` |
| `chapter_goal` | `PlanGoal` | typed 目标，不得用自由文本代替事实引用 |
| `open_hook_goal` | `PlanGoal` | typed 开篇目标 |
| `end_hook_goal` | `PlanGoal` | typed 收束目标 |
| `segments` | `PlanBeat[]` | 非空，完整覆盖源内容单元；顺序就是重构后成稿顺序 |

`PlanGoal` 必填 `intent` 与非空 `fact_refs[]`。每个 `FactRef` 记录闭合 `kind`、可复算 `ref`
及适用 `chapter`；`ref` 与可选 `rationale` 都标为 prompt-private，写手永远收不到它们。

正式 `PlanBeat` 只含：

- `slot: sNN`，由 producer 按正式顺序生成且章内唯一；
- `budget_chars: int > 0`；
- `from_beats: str[]`，引用一个或多个 Stage 2 `beat_id`；
- `narrative_role: cold_open|setup|escalation|turn|payoff|bridge`；
- `delivery: scene|summary`；
- `transition: start|continue|earlier|later|parallel|return`。

每章预算总和必须落在其引用 `SourceSpan` 字数总和的 `PLAN_BUDGET_BAND`（**0.80~1.20**）内；
该常量就是唯一执行带，没有第二层宽限。若某个引用无法提供精确基准，该章的预算规则报告 `unver`，
不会拿不完整分母给出通过或失败结论。

**全书预算总和另有一道带**：`plan_book_band`（`R-5.3c`）要求全书各章预算之和落在
全书 span 合计的 `BOOK_BAND`（**0.90~1.10**）内。两条带一松一紧是**同一次裁决的两半**——
逐章放宽是给「哪一章厚、哪一章薄」留出表达详略的空间，全书收紧守住对用户的总量承诺，
逐章的正负偏差在书级相互抵消。⚠ 书级那条的 `unver` 臂**比逐章严**：任何一章没进基准
（缺预算 / span 覆盖残缺 / 源字数为 0）就整条不判——少一章，分子分母同时缺一块。
⚠ 它与 stage5 `book_length` 共用 `BOOK_BAND` 但**分母不是同一个数**（那边是整章字数、
这边是 span 合计，见 §2.2 那张三口径表），两个读数不许做相等断言。

⚠⚠ **全书合计与目标区间由代码算好发下去**（`source_chars_table(..., book_total=True)`，
只在整跑那一路开）。让模型自己把二十多个章级合计再加一遍，正是它最不擅长的事，
而这条 HARD 落红的代价是整份计划退回、一次全书付费白花。

`PlanProvenance` 继承 `LlmProvenance`，并必填：

```json
{
  "params": {
    "grouping": "absorb-small-v1",
    "beat_min_chars": 80,
    "plan_beats": 0
  }
}
```

另有两个 v3 修复账字段：`partial_regen_chapters: int[]` 累积记录定向重规划过的阅读序章号，
`repairs: PlanRepair[]` 逐次冻结目标章、HARD 失败项/证据指纹、user/response/前后章指纹、
前后 HARD 数、model 与 cache policy。全书初跑两者均为空，不用缺字段冒充零次。

`inputs` 必须覆盖 `stage1/main_text.json`、`stage2/global_grounding.json` 与
`stage2/chapter_blueprints.json`。复检会先比较来源账中的分组参数与当前参数，再运行拍顺序、
相邻性和时间层规则。

## 三、`plan_edit.json`

`PlanEdit` 是严格的完整决策集，只含：

- `reconstruction_strategy`；
- `output_chapters: PlanChapter[]`。

它编辑的是正式计划形状，不使用模型响应中的 `plan_beat`。`--apply-edit <路径>` 只接受当前
小说产物目录内、能由 `ARTIFACTS` 解析的文件；应用后重新运行全部 Stage 4 判据并重建
`writer_context.json`。

`--apply-edit` 是一轮新的人工修订：以当前正式稿或活动 `.INVALID` 为基线，归档被替换版本，
更新实际 inputs 并标 `partial_rewrite=--apply-edit`。它与 `--revalidate` 不同；后者只把当前
`.INVALID` 按现行判据重判后提升，不创建新生成轮，并在 provenance 中保存 actor/reason、
raw/canonical SHA-256 与 RFC 6901 改动路径的 `revalidation_audit`。

**路径口径**（stage3/4/6/7 共用 `novel/paths.py::edit_rel`）：相对路径按**当前书目录**
解释，不按进程 cwd；书目录内的绝对路径同样接受；落在书目录之外（含 `..` 穿越）是显式终态。

## 四、`writer_context.json`

写作上下文由当前正式计划和 Stage 1/2 产物确定性组装，顶层只有：

| 字段 | 类型 | 内容 |
|---|---|---|
| `world_facts` | `WorldFacts` | 给确定性消费者使用的全书人物投影 |
| `chapters` | `CtxChapter[]` | 写手逐章实际消费的全部材料 |
| `_provenance` | `CtxProvenance` | 纯代码来源账 |

`WorldFacts.characters{char_id}` 的每项只含 `display_name`、`role` 与闭合枚举
`name_distinctiveness`。它不向写手暴露人物结局。

### 4.1 章节上下文

每个 `CtxChapter` 必填：

| 字段 | 类型 | 内容 |
|---|---|---|
| `output_ordinal` | `int` | 输出章号 |
| `from_source_ordinals` | `int[1]` | 唯一源章 |
| 三个 `*_goal` | `CtxGoal` | 代码把 typed refs 按本章闸门解析为 `facts[]` 人话；内部 id 与 `rationale` 均不存在 |
| `budget_chars` | `int > 0` | 本章各拍预算之和 |
| `source_chars` | `int ≥ 0` | 本章各拍精确原文区间总字数 |
| `segments` | `CtxBeat[]` | 展开后的写作单位，顺序与 plan 相同 |
| `characters` | `dict[str,ChapterCharacter]` | 截止本章可安全叫出的角色 |
| `narration` | `CtxNarration[]` | 当前叙述人称、叙述者与视角人物 |
| `facts` | `CtxFacts` | 当前章可见设定、关系、秘密、规则、弧线和称谓 |
| `must_not_reveal` | `str[]` | 当前章不得点破的事实 |

`CtxBeat` 必填 producer `slot`、`events[]`、`budget_chars` 与 `source_chars`。同时间层、同地点时可平铺
`time_layer`、`place`、`present`；跨地点时使用 `segments[]`，不把在场者取并集。

保真材料按语义分四组：

- `verbatim`: 必须逐字写入的原话；
- `keep_value`: 数值必须保持，措辞可变；
- `must_appear`: 物件必须出现并起作用；
- `must_hold`: 因果前提必须成立，不要求字面复现。

`CtxFacts` 只投影当前章允许看见的 `setting`、`relations`、`secrets`、`world_rules`、`arc`
与 `address`。秘密以 `reader_knows_by_now` 作为唯一读者时点标记；人物叫法必须在原文中有据，
内部 `char_id` 不得作为显示名下发。

`CtxProvenance` 是 `FreeProvenance`，`inputs` 必须记录当前计划、蓝图、全书事实和正文来源。

## 五、当前入口

| 入口 | 成本 | 行为 |
|---|---|---|
| `stage4.plan` | 付费 | 生成并严格校验新计划 |
| `stage4.plan --chapters N[,M...]` | 付费 | 逐目标章用 refresh-write 定向重规划；其余章与书级策略原样合并，最后跑一次全书闸并重建上下文 |
| `--dry-run` | 0 | 展示当前下发面，不写正式产物 |
| `--check-only` | 0 | 联合复检计划与写作上下文 |
| `--revalidate --actor <id> --reason <why>` | 0 LLM | 强审计重判当前 `.INVALID` 计划；合格后保留原生成来源/轮次晋升并重建上下文 |
| `--names-only` | 0 | 只重算当前人物显示名投影 |
| `--identity` | 0 | 生成确定性写作上下文基线 |
| `--apply-edit <路径>` | 0 | 应用严格完整计划修订集。路径相对**书目录**（`paths.edit_rel`） |
| `stage4.context` | 0 | 从当前正式上游重建 `writer_context.json` |

定向重规划在任何请求前拒绝混合 model、`PLAN_SYS` 或 grouping 参数；单章响应必须只返回所请求的
`output_ordinal/from_source_ordinals`，不得改 `reconstruction_strategy`。目标章失败时合并结果只落
`.INVALID`、正式计划不被覆盖；成功时 usage 与历史修复账累加，而不是覆盖成“只调用了一次”。

## 六、现行判据

当前注册表共 **31 条**：**20 hard、6 info、5 metric**。计划和写作上下文共用同一张表。

| 严重度 | 当前规则 id |
|---|---|
| hard | `one_to_one` · `ordinal_seq` · `empty_chapter` · `beat_coverage` · `beat_ref` · `structure_changed` · `transition_direction` · `structure_units` · `structure_transition` · `goal_landing` · `budget_int` · `budget_band` · `plan_book_band` · `context_link` · `style_words` · `ctx_events_verbatim` · `ctx_name_attested` · `ctx_secret_gate` · `ctx_layer_flat` · `ctx_hinge_delivered` |
| info | `flashback_closed` · `flat_chapter` · `beat_size` · `beat_reuse` · `verbatim_starved` · `params_drift` |
| metric | `verbatim_share` · `plan_params` · `ctx_source` · `structure_distance` · `structure_entry_mix` |

`budget_band` 与 `plan_book_band` 是**一对**：前者管单章、后者管全书，2026-08-22 同批落地。
逐章带此前是 0.90~1.10，于是「章章 ±10% ⇒ 全书必然 ±10%」，总量是被顺带兜住的；带放宽到
0.80~1.20 之后那条蕴含断了，总量需要它自己的一道门。⚠ 在此之前全书唯一的守卫是 stage5 的
`book_length`（INFO）——**钱花完才响**。

`structure_distance` 除结构四项外，还逐章报**详略**两项：`拍级 预算/源 相对本章均值 min~max`
与 `summary 承载源字数 X%`。⚠ 比的是**相对本章均值**不是相对 1.0——章级倍率本来就允许在带内漂，
拿绝对值比会把「这一章整体写厚了」误读成「章内有轻重」，而那两件事分属不同判据。
⚠ 任一拍算不出 span 就整章不报这一维并说出来（不拿部分拍充数）。

`structure_changed` 判的是「与**恒等基线**（`novel/stages/stage4/measure.py::identity_plan` 的结构
切片：一个源单元一个写作单元 · 按源序排开 · `delivery` 恒 `scene`）逐格相同」——三根轴（叙事入口 /
单元分组 / 承载方式）至少动一根。**保持源序本身不违规。**
`transition_direction` 与 `flashback_closed` 只在接缝两端都是**现场层**时判：源叙述位置只在
`present` 层内才是故事时间的代理，任一端是 `recall`/`hearsay` 记「判不动」而不是「通过」。

本层没有旧规则占位、旧字段兼容分支或双形状读取路径。权威定义在
`contracts/judges/stage4.py`；报告形状见 [`_report.md`](./_report.md)。
