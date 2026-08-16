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

三份带 `_provenance` 的正式 JSON 使用 **schema v2**；`plan_edit.json` 是无来源账的人工
决策集，仍为 v1。未知字段拒收，运行时不读取旧形状、不补缺失字段，也不根据历史常量切换
预算语义。

## 一、规划调用边界

模型响应使用 `PlanResponse`：

| 字段 | 类型 | 约束 |
|---|---|---|
| `reconstruction_strategy` | `str` | 非空的全书重构意图 |
| `output_chapters` | `PlanChapterResponse[]` | 非空，覆盖全部源章 |

每个 `PlanChapterResponse` 必填：

- `output_ordinal` 与唯一的 `from_source_ordinals[0]`；
- typed `chapter_goal`、`open_hook_goal`、`end_hook_goal`；
- 非空 `segments[]`。

模型响应中的每个写作单元包含 `plan_beats[]`、`narrative_role`、`delivery`、`transition`
与正整数 `budget_chars`。`plan_beats[]` 引用下发的 `pNN_NN` 源内容单元，数组顺序就是
重构后的叙述顺序。`expand_plan_beats` 必须确认编号存在、不重复、没有漏项，再展开成正式
`from_beats[]`，并按规划顺序分配稳定 `slot=s01...`；不按源序重新排序。

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

每章预算总和必须落在其引用 `SourceSpan` 字数总和的 `PLAN_BUDGET_BAND` 内；该常量就是唯一
执行带，没有第二层宽限。若某个引用无法提供精确基准，该章的预算规则报告 `unver`，不会拿
不完整分母给出通过或失败结论。

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
| `--dry-run` | 0 | 展示当前下发面，不写正式产物 |
| `--check-only` | 0 | 联合复检计划与写作上下文 |
| `--revalidate` | 0 | 重判当前 `.INVALID` 计划，合格后晋升并重建上下文 |
| `--names-only` | 0 | 只重算当前人物显示名投影 |
| `--identity` | 0 | 生成确定性写作上下文基线 |
| `--apply-edit <路径>` | 0 | 应用严格完整计划修订集。路径相对**书目录**（`paths.edit_rel`） |
| `stage4.context` | 0 | 从当前正式上游重建 `writer_context.json` |

## 六、现行判据

当前注册表共 **26 条**：**17 hard、5 info、4 metric**。计划和写作上下文共用同一张表。

| 严重度 | 当前规则 id |
|---|---|
| hard | `one_to_one` · `ordinal_seq` · `empty_chapter` · `beat_coverage` · `beat_ref` · `structure_changed` · `structure_units` · `structure_transition` · `goal_landing` · `budget_int` · `budget_band` · `context_link` · `style_words` · `ctx_events_verbatim` · `ctx_name_attested` · `ctx_secret_gate` · `ctx_layer_flat` |
| info | `flat_chapter` · `beat_size` · `beat_reuse` · `verbatim_starved` · `params_drift` |
| metric | `verbatim_share` · `plan_params` · `ctx_source` · `structure_distance` |

本层没有旧规则占位、旧字段兼容分支或双形状读取路径。权威定义在
`contracts/judges/stage4.py`；报告形状见 [`_report.md`](./_report.md)。
