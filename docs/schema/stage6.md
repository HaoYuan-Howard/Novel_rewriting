# Stage6 产物与质量契约

Stage6 生成放在正文前的开篇文案。一次模型调用产三个候选，随后由两名互不可见的
匿名 judge 独立完成实质矛盾核对与吸引力排序，最后由人定稿。

⚠⚠ **合并决议恒是带证据的 `UNVER`**：本仓没有把 judge 结论升级成自动 `PASS`/`FAIL` 的装置
（evaluator profile 已于 2026-08-18 整批删除）。判官的价值是**证据**——逐判官的 verdict、
理由与逐字定位——不是绿灯。

对应需求：`docs/BACKEND_CONTRACT.md` 的 R-9。

## 一、质量语义

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
stage6/semantic_N.json       A/B 双盲质量审判；决议按完整 SHA-256 绑定
stage6/hook_edit.json        人工编辑一条候选或定稿的决策集
stage6/wontfix.json          人的「这条我不修」（可选；`--wontfix` 记账，纯 0 成本）
```

`hook.json` 与轮次归档当前为 **schema v3**；登记的 v2→v3 迁移只补候选重试账与
`Finding.output_ordinal=null` 的中性默认。`hook_edit.json` 为 v1 人工输入，semantic 产物为 v2。

### 2.1 `hook.json`

| 字段 | 类型 | 说明 |
|---|---|---|
| `lead_upto` | `int >= 1` | 连续开篇成稿下发上界；全书结构地图不受此上界限制 |
| `rationale` | `str` | 三条候选各自押注的吸引点 |
| `candidates` | `Candidate[3]` | 三种当前策略各一条，文本不得重复 |
| `approved` | `ApprovedHook \| null` | 人工定稿；进入 `stage7/book.txt` 的第一段 |
| `sources.writer_context` | `ContextSource` | 写作上下文来源状态 |
| `judged` | `{path: JudgedDraft}` | 生成时使用的开篇成稿及其消费指纹 |
| `_provenance` | `HookProvenance` | 模型、prompt、输入、代码与参数来源账 |

`Candidate`：

| 字段 | 类型 | 说明 |
|---|---|---|
| `approach` | 枚举 | 三个当前策略之一，见 §三 |
| `text` | 非空 `str` | 归一后的文案字节 |
| `mech` | `Mech` | 标点、引号、分段归一的计数 |
| `findings` | `Finding[]` | 该候选的机械判定；候选不属于正文章，`output_ordinal` 恒为 `null` |

`ApprovedHook = Candidate + origin='human' + resolution`。`resolution` 必须记录自报
`actor`（不是认证身份）、非空 `reason`、定稿文本的完整 `subject_sha256`、
恒为 `human_verification` 的判定基础，以及可用的语义审判序号
（`semantic_review_seq` 可空——「人在零证据下拍的板」与「人读了第 3 轮判官证据」必须分得开）。`approach` 还可为
`human_edit`；从既有候选编辑时保留其 approach，没有候选前身才写 `human_edit`。
`hook_edit.json` 的 `subject_sha256` 必须绑定经过本书标点、引号与分段归一链后的定稿文本；
校验发生在 `run_apply_edit` 写盘前，并复用与普通候选批准相同的字节绑定闸，
不能用 raw 上传字节的哈希绕过。

`HookProvenance.params` 除初始 `max_tokens` 外，还冻结单候选重试的 max tokens、固定一次上限、
`low` effort 与 `thinking=false`；`candidate_retries[]` 逐条保存 approach、失败规则/证据指纹、
user/response 指纹、前后 HARD 数、cache policy、状态、是否采纳与失败理由。发生重试时
`prompts.HOOK_RETRY_SYS` 必须存在；没有脏候选时该键与历史均为空。

初始三候选归一并机械判定后，每条命中 HARD 的候选恰好获得一次 refresh-write 定向重试；
请求只允许返回原 approach，并看见完整事实包、整组三候选和该候选的具体 HARD 证据。只有重试后
HARD 数严格下降且文本不与另两条重复时才替换该候选和整组 rationale；wrong approach、请求失败、
重复或 HARD 未下降均保留已经付费取得的初始候选，并把原因写入重试账。调用数因此是
`1 + 初始脏候选数`，不再把“初始一次调用”误写成整条路径的成本上限。

### 2.2 `semantic_N.json`

| 字段 | 类型 | 说明 |
|---|---|---|
| `judged_texts` | `{真实标签: Fp10}` | 审过的去重文本集合 |
| `hook_file_sha1` | `Fp10` | 整份 hook 文件的审计指纹，不作新鲜度绑定键 |
| `reviews` | `HookCandidateReview[]` | 合并后的诊断视图（质量分取 A/B 平均） |
| `ranking` | `str[]` | 代码从合并决议与平均分确定性派生的真实标签排序 |
| `judge_rankings` | `{A: str[], B: str[]}` | 两名 judge 各自的原始解匿名排序 |
| `decisions` | `SemanticDecision[]` | 每份去重文本一条合并 PASS/FAIL/UNVER；唯一运行时判定源 |
| `evaluator_fingerprint` | `EvaluatorFingerprint` | 运行时 dataset/model/prompt/schema/decoder 全指纹 |
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

## 三、候选策略

生成响应必须恰好覆盖：

| approach | 功能 |
|---|---|
| `source_architecture` | 最大程度保住原作文案已经验证有效的结构与最后一击，换成正文声线和新措辞 |
| `payoff_preview` | 从全书挑最有吸引力的后期兑现/反转，制造“怎么走到这一步”的阅读欲 |
| `scene_punchline` | 用可视可听的场面或对白砸出核心关系，以人物反应、危险或笑点收尾 |

`HookResponse` 的模型级 validator 保证三种策略各一次、三条文本不重复。

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

本节点没有 flag（`--dry-run` 只打印下发面）。运行时先冻结这一次的请求身份
`EvaluatorFingerprint`（`model` / `prompt_sha256` / `schema_sha256` / `decoder_sha256` 四格，
**没有 dataset**），然后把同一份匿名材料分别以 A/B 角色后缀发给两名 judge，
候选标签都是 `T1/T2/...`。评论、证据与质量排序必须显示在报告中，不能因为结论是 `UNVER`
就伪装成“0 份当前审判”。
validator 必须确认：

1. `reviews` 与 `ranking` 恰好覆盖全部匿名标签，且无重复；
2. 每条矛盾 evidence 是对应候选的连续原字节；唯一空白差异可回取成原字节；
3. 所有 `complete` 且无矛盾的候选排在矛盾或 uncertain 候选之前；
4. 六维分数都在 0~10；总分由代码计算；
5. 当前 hook 文本集合与审判的文本 sha1 / 决议 SHA-256 集合一致，且 semantic 产物新鲜。

批准动作 `stage6.approve --pick N --actor <id> --reason <why>
--subject-sha256 <hash>` 在归档和写盘之前执行三道硬检查：

- 该候选没有机械 HARD；
- 请求里的完整 SHA-256 与候选当前字节一致，不允许旧页面误批。

⚠ 这里曾有第三道：「当前合并决议不是校准 FAIL，也不存在与本文本绑定的历史校准 FAIL」。
它的输入是 automated FAIL，随自动放行门一并消失。**⇒ 没有任何机制会替人否决一个候选。**
全部语义结论进 `verification` 清单，人逐项核对后以 actor/reason/hash 路径记
`human_verification`；矛盾线索仍逐条呈现（三条候选各一行，凭据键 `<候选>:<文本指纹>`，
可逐条 wontfix），但它们**报**而不**拦**。

`--revalidate --actor <id> --reason <why>` 与 `--apply-edit` 不能互换：前者对活动 `.INVALID`
重建派生字段、重判并强审计提升，保留原 prompts/model/inputs 与生成轮次，记录 raw/canonical
SHA-256、RFC 6901 改动路径和 `edited_by_human`；后者消费 v1 `hook_edit.json`，归档当前正式
hook 并形成一轮新的局部人工修订，改字后原 semantic 按文本身份自动过期。两条都是 0 次 LLM
调用，但 apply-edit 的人工内容工作量不等于零。

### 5.1 `stage6/wontfix.json` —— 语义结论的人工闭环

本层两条语义判据（`hook_semantic_contradiction` 阻断、`hook_candidate_contradiction` 提示）
的人工豁免走这里。

`stage6.hook --wontfix '<book>|<判据>[|<证据键>]=<理由>'`，形状与账本模型与 stage5 那份
**完全相同**（`novel/wontfix.py` 是唯一的机制落点）。本层特有的三条：

| 约束 | 说明 |
|---|---|
| **一律 `book` 级** | 候选是同一次调用产的三条，没有章这一维；候选身份在证据键里（`<候选>:<原字节>`）。写章号 ⇒ 显式终态 |
| **`coverage=uncertain` 逐条可寻址** | 它的证据是 `<候选>:<文本指纹>`。⚠ 原来是空列表 ⇒ 收敛键恒为空串 ⇒ 三条候选共用一个键，一次豁免会一并豁免三条 |
| **豁免不开闸门** | `require_selectable` 与落盘闸门照旧拒绝有矛盾的候选。「有人看过并决定不修」≠「这条不再是问题」 |

`at` 记的是人当时看的那一版：语义判据记 `semantic_<n>:<产物指纹>`，机械判据记
`hook:<产物指纹>`。**它是审计账，不参与匹配**——失效由证据自身保证（文本一改键就失配）。

## 六、现役判据（21 条）

分布：6 HARD、8 INFO、4 metric、3 unver。

| 判据 | 级别 | 作用 |
|---|---|---|
| `hook_empty` | HARD | 至少 20 个内容字符 |
| `hook_truncated` | HARD | 以完整句末标点收束 |
| `hook_reuses_source` | HARD | 与原作文案无任何相同 20 内容字窗口 |
| `hook_invented_name` | HARD | 人物称谓在完整下发面有据 |
| `hook_para_sep` | HARD | 分段档位符合原作文案，作为归一后置条件 |
| `hook_semantic_contradiction` | HARD/semantic | 合并决议 FAIL 阻断；UNVER 保持可见并只允许显式人工核验，不能冒充自动 PASS |
| `hook_transcribes_body` | INFO | 不整段誊抄开篇正文 |
| `hook_invented_number` | INFO | 数字+单位在下发面有据 |
| `hook_length` | INFO | 篇幅贴近原作文案 |
| `hook_shape` | INFO | 段落形态贴近原作文案 |
| `hook_quote_style` | INFO | 引号体贴近原作文案 |
| `hook_quote_unbalanced` | INFO | 引号配对异常不静默修 |
| `hook_ending_shape` | INFO | 最后一击形态贴近原作文案 |
| `hook_candidate_contradiction` | INFO/semantic | 按 A/B 合并决议逐候选列出 FAIL 矛盾与 UNVER 不确定项 |
| `hook_report` | metric | 下发范围、各文本字数、原作文案字数 |
| `hook_quote_shape` | metric | 引语条数、最长引语、对白比 |
| `hook_secret_literal` | metric | 与 secret 原文的字面重合读数，不是剧透门 |
| `hook_quality_rank` | metric/semantic | 六维分、代码总分与匿名排序 |
| `hook_not_approved` | unver | 是否已有人工定稿 |
| `hook_body_missing` | unver | 连续开篇范围是否缺成稿 |
| `hook_body_stale` | unver | 文案绑定的开篇成稿消费投影是否仍一致 |
