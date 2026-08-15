# Stage6 产物与质量契约

Stage6 生成放在正文前的开篇文案。一次模型调用产三个候选，随后一次匿名质量审判同时做
实质矛盾核对与吸引力排序，最后由人从可选候选中定稿。

对应需求：`docs/BACKEND_CONTRACT.md` 的 R-9。

## 一、质量语义（2026-08-15）

三本书快速 A/B 中，最佳实验候选的匿名主编均分为 8.78，当前定稿为 4.88，提升
+3.90/10，且实验候选 3/3 获胜。因此生产设计采用以下已验证变化：

- 原作文案作为创作金标准下发，保留其信息顺序、升级节拍、反差、对白密度和最后一击；
- 下发完整 `entities` / `canon` / `arc` 与全书逐章事件地图，使候选可选择后期兑现；
- 下发正文 `voice`、`surface_habits` 和开篇源章的 `chapter_styles`，使文案与正文像同一本书；
- 剧透不再是错误，也不因剧透本身加减分；
- 合法体裁自由包括重写台词、时间压缩、倒叙、合并数章和合成场面；
- 唯一语义硬边界是不能改变人物身份、关系、关键因果、故事结果，或新增会改变读者理解的设定。

仍保持“一次调用产三候选”。三次独立生成没有在本轮 A/B 中验证，不纳入此次改版。

## 二、产物

```text
stage6/hook.json             生成候选、机械判定与人工定稿
stage6/_rounds/hook.rN.json  上一轮 hook.json 的整份归档
stage6/semantic_N.json       匿名质量审判；按文本 sha1 绑定
stage6/hook_edit.json        人工编辑一条候选或定稿的决策集
```

### 2.1 `hook.json`

| 字段 | 类型 | 说明 |
|---|---|---|
| `lead_upto` | `int >= 1` | 连续开篇成稿下发上界；全书结构地图不受此上界限制 |
| `rationale` | `str` | 三条候选各自押注的吸引点 |
| `candidates` | `Candidate[3]` | 三种当前策略各一条，文本不得重复 |
| `approved` | `ApprovedHook \| null` | 人工定稿；进入 `stage7/book.txt` 的第一段 |
| `sources.writer_context` | `ContextSource` | 写作上下文来源状态 |
| `judged` | `{path: JudgedDraft}` | 生成时使用的开篇成稿及其消费指纹 |
| `book` | `Finding[]` | 书级机械读数/状态 |
| `baselines_identity` | `{rule: ratio}` | 历史恒等基线读数 |
| `_provenance` | `HookProvenance` | 模型、prompt、输入、代码与参数来源账 |

`Candidate`：

| 字段 | 类型 | 说明 |
|---|---|---|
| `approach` | 枚举 | 新产物见 §三；旧枚举只读兼容 |
| `text` | 非空 `str` | 归一后的文案字节 |
| `mech` | `Mech` | 标点、引号、分段归一的计数 |
| `findings` | `Finding[]` | 该候选的机械判定 |

`ApprovedHook = Candidate + origin='human'`。`approach` 还可为 `human_edit`；从既有候选编辑时
保留其 approach，没有候选前身才写 `human_edit`。

### 2.2 `semantic_N.json`

| 字段 | 类型 | 说明 |
|---|---|---|
| `judged_texts` | `{真实标签: Fp10}` | 审过的去重文本集合 |
| `hook_file_sha1` | `Fp10` | 整份 hook 文件的审计指纹，不作新鲜度绑定键 |
| `reviews` | `HookCandidateReview[]` | 新版矛盾与质量审阅 |
| `ranking` | `str[]` | 解匿名后的真实标签排序 |
| `texts` / `decisions` | 旧版字段 | 只读兼容旧剧透审判；新版恒为空 |
| `_provenance` | `LlmProvenance` | 本次质量审判来源账 |

`HookCandidateReview`：

| 字段 | 类型 | 说明 |
|---|---|---|
| `label` | `str` | 落盘时恢复成真实候选标签；请求里只见 `T1/T2/...` |
| `text_sha1` | `Fp10` | 文本绑定键；`--pick` 不过期、改字自动过期 |
| `coverage` | `complete \| uncertain` | `uncertain` 必须给原因且不可定稿 |
| `contradictions` | `HookContradiction[]` | 实质矛盾；`evidence` 必须由代码回查为候选连续原字节 |
| `quality` | `HookQualityScores` | 六维 0~10 分 |
| `concise_reason` | `str` | 主编简评 |

质量总分不接受模型字段，由代码固定计算：

```text
0.30 * read_on_desire
+ 0.20 * body_voice_match
+ 0.15 * concrete_scene_and_specificity
+ 0.20 * curiosity_and_payoff_promise
+ 0.15 * hook_craft
- 0.10 * synopsis_penalty
```

结果裁到 0~10，保留两位小数。

## 三、候选策略与兼容面

新生成响应必须恰好覆盖：

| approach | 功能 |
|---|---|
| `source_architecture` | 最大程度保住原作文案已经验证有效的结构与最后一击，换成正文声线和新措辞 |
| `payoff_preview` | 从全书挑最有吸引力的后期兑现/反转，制造“怎么走到这一步”的阅读欲 |
| `scene_punchline` | 用可视可听的场面或对白砸出核心关系，以人物反应、危险或笑点收尾 |

载体仍能读取旧值 `conflict_first` / `result_flashback` / `identity_contrast`，但模型响应不能再
写这些值。`HookResponse` 的模型级 validator 同时保证三种新策略各一次、三条文本不重复。

## 四、生成材料

`prompts.build_material` 是生成 prompt 与机械“给过什么”探针的唯一材料落点：

- `source_hook`：原作文案全文；
- `story_truth`：完整 `entities`、`canon`、`arc`；
- `whole_story_map`：每章章号、源章号、章目标、开头目标、全部 beat events、结尾目标；
- `rewritten_opening`：第 1 章至 `lead_upto` 的连续成稿；
- `style`：`hook_style`、完整书级 `voice`、`surface_habits`、开篇源章 `chapter_styles`、
  原作文案形态与 measurable。

原作文案可学结构但不可抄长句：任意连续 20 个内容字符相同即命中 `hook_reuses_source`。

## 五、匿名质量审判与批准

质量请求将每条去重文本改名为 `T1/T2/...`。validator 必须确认：

1. `reviews` 与 `ranking` 恰好覆盖全部匿名标签，且无重复；
2. 每条矛盾 evidence 是对应候选的连续原字节；唯一空白差异可回取成原字节；
3. 所有 `complete` 且无矛盾的候选排在矛盾或 uncertain 候选之前；
4. 六维分数都在 0~10；总分由代码计算；
5. 当前 hook 文本集合与审判的文本 sha1 集合一致，且 semantic 产物新鲜。

批准动作 `stage6.approve --pick N` 在归档和写盘之前执行两道硬检查：

- 该候选没有机械 HARD；
- `semantic.require_selectable` 判为 `clear`。

因此缺审判、旧版审判、过期、文本未审、coverage=uncertain 或实质矛盾都不能被人工误点绕过。

## 六、现役判据（21 条）

分布：6 HARD、8 INFO、4 metric、3 unver。

| 判据 | 级别 | 作用 |
|---|---|---|
| `hook_empty` | HARD | 至少 20 个内容字符 |
| `hook_truncated` | HARD | 以完整句末标点收束 |
| `hook_reuses_source` | HARD | 与原作文案无任何相同 20 内容字窗口 |
| `hook_invented_name` | HARD | 人物称谓在完整下发面有据 |
| `hook_para_sep` | HARD | 分段档位符合原作文案，作为归一后置条件 |
| `hook_semantic_contradiction` | HARD/semantic | 未定稿时至少一条 clear；定稿后被选文本必须 clear |
| `hook_transcribes_body` | INFO | 不整段誊抄开篇正文 |
| `hook_invented_number` | INFO | 数字+单位在下发面有据 |
| `hook_length` | INFO | 篇幅贴近原作文案 |
| `hook_shape` | INFO | 段落形态贴近原作文案 |
| `hook_quote_style` | INFO | 引号体贴近原作文案 |
| `hook_quote_unbalanced` | INFO | 引号配对异常不静默修 |
| `hook_ending_shape` | INFO | 最后一击形态贴近原作文案 |
| `hook_candidate_contradiction` | INFO/semantic | 逐候选列出矛盾和不确定项 |
| `hook_report` | metric | 下发范围、各文本字数、原作文案字数 |
| `hook_quote_shape` | metric | 引语条数、最长引语、对白比 |
| `hook_secret_literal` | metric | 与 secret 原文的字面重合读数，不是剧透门 |
| `hook_quality_rank` | metric/semantic | 六维分、代码总分与匿名排序 |
| `hook_not_approved` | unver | 是否已有人工定稿 |
| `hook_body_missing` | unver | 连续开篇范围是否缺成稿 |
| `hook_body_stale` | unver | 文案绑定的开篇成稿消费投影是否仍一致 |

已退役且不再出现在新报告：`hook_arc_literal`、`hook_downsend_leak`、
`hook_semantic_spoiler`、`hook_semantic_grounding`、`hook_quote_unver`、`hook_no_secrets`、
`hook_secret_no_time`。历史 `hook.json` / `semantic_N.json` 只读兼容，不把旧结论冒充新质量审判。
