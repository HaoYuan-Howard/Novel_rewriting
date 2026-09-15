# Stage6 产物与质量契约

Stage6 先由独立 Prepare E/C 接受原钩子事实支持和连续开篇范围，再生成放在正文前的开篇文案。
Hook 一次模型调用产三个候选，随后由两名互不可见的
匿名 judge 独立完成实质矛盾核对与吸引力排序，最后由人定稿。

⚠⚠ **合并决议恒是带证据的 `UNVER`**：本仓没有把 judge 结论升级成自动 `PASS`/`FAIL` 的装置
（evaluator profile 已于 2026-08-18 整批删除）。判官的价值是**证据**——逐判官的 verdict、
理由与逐字定位——不是绿灯。

对应需求：`docs/BACKEND_CONTRACT.md` 的 R-9。

Prepare 仅消费 MainText 与 HookCopy，完整正文和原钩子各提供一次。`supported` 表示正文支持，
`hook_supported` 表示原钩子独立支持；`unresolved` 保留未决主张和原因，不能作为已确定事实。
正文未重复不等于反证，正文明确反证也不能被原钩子覆盖。未决开篇不得默认第一章；
未接受 Prepare 不得生成 Hook。Prepare 接受与最终 `gate.s6` 各自绑定自己的主体。

源材料经 Stage0 审计修订后，免费完整 typed 编辑重新编译当前两份材料。若全 clean 根
改变，Prepare 专属 provenance 的 `source_rebuild` 绑定旧候选完整 SHA、旧／新根、两端
Stage0 revision、当前材料 SHA 和最初模型的生成根；旧候选仍从历史原字节回读。
当前候选声明当前根，保留的 model/prompts/usage 明确属于历史生成，并非模型看过新源。
同根后续订正保留这条来源证明；WAL 复算原请求、源链及候选后补尾，不能用新材料重包
旧提交，也不能借一次免费修订继承原接受。仅原钩子变化且正文完全相同时，Stage2–5
依 `source_identity` 的窄证明保留自身来源与有效审批，Prepare / Hook 仍按真实输入失效。

Hook 和 A/B 的 `judged` 只记录实际下发的连续开篇章稿；最终文案人工审批的 v4 工作包
另绑定全书成稿、原钩子和 `prepare_subject_sha256`，token 前缀 `s6r4_`。Prepare 证据定位
或 rationale 订正可重开 Prepare 审阅，但不改变创作语义时不扩大 Hook / A/B 付费输入。
全书故事地图保留 Writer9 的公开人物、global_subjects、narration、background_beats 和所有
状态、知识事件、分场许可目录。opaque actor_ref 在各章目录中解释，不提供反向 char_id 映射。

## 一、质量语义

此前三本书文案设计 A/B 中，最佳实验候选的匿名主编均分为 8.78，当时定稿为 4.88，差值
+3.90/10，且实验候选 3/3 获胜。这份历史实验不作为本批 Prepare 或全链验收通过的证明。
当前生产保持该文案设计，并接入以下独立材料边界：

- 原作文案作为创作金标准下发，保留其信息顺序、升级节拍、反差、对白密度和最后一击；
- 下发独立 Characters v2、Global v7 四类长期事实、已接受 Prepare 语义投影与全书逐章事件地图，使候选可选择后期兑现；
- 从 Stage1 原作文案即时计算机械五叶（字符数、平均句/段长、引号体、对白比例），不再下发任何定性文风包；
- 剧透不再是错误，也不因剧透本身加减分；
- 合法体裁自由包括重写台词、时间压缩、倒叙、合并数章和合成场面；
- 唯一语义硬边界是不能改变人物身份、关系、关键因果、故事结果，或新增会改变读者理解的设定。

仍保持“一次调用产三候选”。三次独立生成没有在本轮 A/B 中验证，不纳入此次改版。

## 二、产物

```text
stage6/prepare.json          原文案事实支持与连续开篇范围，独立 E/C 生命周期
stage6/prepare_review.json   对当前 Prepare 完整字节的独立接受
stage6/prepare_edit.json     段引用形式的免费完整修订请求
stage6/prepare_review_decisions.json  当前主体的 accept/rework/uncertain 原决定
stage6/prepare_commit.json   内部 prepared/completed 提交记录，不是创作知识库
stage6/hook.json             生成候选、机械判定与人工定稿
stage6/_rounds/hook.rN.json  上一轮 hook.json 的整份归档
stage6/semantic_N.json       A/B 双盲质量审判；决议按完整 SHA-256 绑定
stage6/hook_edit.json        人工编辑一条候选，或修改当前定稿的候选前身
stage6/wontfix.json          人的「这条我不修」（可选；`--wontfix` 记账，纯 0 成本）
```

本层业务载体按以下版本登记（见 `_README.md`「版本与路径注册表」）；不自动迁移业务产物，
破坏形状就 +1 并同批明确重建 owner。离线 workflow manifest 过渡另见 `work_units.md`，
不能借工作流过渡给旧 Hook、审判或批准补字段：

- `hook.json` 与轮次归档**都是 schema v4**。本批改为独立 Prepare / Characters / Global 来源，
  旧 v3 不自动继承生成、审阅或 prepared 身份。2026-09-08 取消每槽只修一次的限制：
  legacy 的 `candidate_retry_limit=4` 表示整书工作单元的内容上限，Agent 完整替换协议为 0；
  同槽 `candidate_retries.attempt` 按实际请求连续编号 1..4，不能重复或跳号。
  旧 v1/v2/v3 产物保留原代码和原账，不迁移、不手改版本标记；当前根在接受 Prepare 后，
  `stage6.hook` 经显式付费与正文外发授权生成 v4，再由独立双 judge 与当前 typed pick 定稿。
  v4 的已封存提交尾窗可走 `--recover-prepared` 免费补尾，不追认旧 schema 的提交来源。
- 各份 `hook_edit.json` 编辑请求**都是 schema v2**。候选由位置身份 `c1/c2/c3`
  定位；本轮未改编辑形状。
  2026-08-23 的候选身份与策略名解耦仍保持：策略可重复，位置才唯一。旧实验快照只读，
  不纳入 current-schema corpus；存量重建归属见 `tests/test_artifact_versions.py::_SHAPE_BREAKS`。
- 各轮 `semantic_<n>.json` **都是 schema v3**，独立 Prepare 及 Writer9 公开目录参与实际请求。2026-08-27 新增 `judged`，把 A/B 判官实际
  看见的 Stage5 开篇成稿按 `JudgedDraft.consumed.text_sha1` 绑定。旧 v1 只绑
  hook 候选文本，正文改字后仍可被误当 current，因此不迁移、不手补；由
  `stage6.semantic` 在当前 hook + canonical Stage5 上重跑（legacy 是 A/B 各一次调用；显式 executor
  profile 是 A/B 各一条可多轮 session，wire 数由代码反馈轮次决定）。当前 v3 不把
  未进入判官 prompt 的后半书稿塞进 `judged`；全书 Stage5 accepted Review 仍是既有前置门，
  后半书改字先令 Review 暂不可用，零成本重跑 Review 后若实际 prompt 输入未变，原 v3
  semantic 可以重新成为 current，不白做 A/B。
  Agent 的 run、A/B acceptance 与 checkpoint 恢复共用
  `novel/agents/session_client.py::agent_json_schema` 归一后的实际发送 schema 指纹；
  不得用 Pydantic 原始 schema 另算一套身份。旧 run 的指纹不改写、不冒充当前请求；
  未能落下有效 checkpoint 的历史调用保留成本证据，由前端 Agent 在当前输入上重新执行该节点。
- 五份 Prepare 载体与 `wontfix.json` **都是 schema v1**。

### 2.0 Prepare v1

五份 Prepare 载体均从 v1 登记。`prepare.json.source_blurb` 保留 `facts` 和 `opening`：
事实严格判别为正文 `supported`、原钩子 `hook_supported` 或 `unresolved`；未决项保存原因。
范围是从第 1 章到 `through_chapter` 的连续 `proposed`，或显式 `unresolved`。
模型与 typed 编辑只引用 `chapter_ordinal/start_para/end_para`；Hook 证据没有章号。
代码从对应段落编译精确 `SourceSpan`，机械检查证据切片、章范围与结束章证据。
坏引用保留为 INVALID，不补造证据；编辑工作包对无法按当前材料反投影的旧引用明确标未决。
全文和原钩子各提供一次，正式证据仅存所选片段。E/C 的文本编号都来自 `paragraph_spans`；
C formatter 用带编号原文替换 API 原文呈现，并以段号版 `candidate_source_blurb` 代替
正式 Prepare 中的重复证据片段。原 API workpack 不变，实际格式进入 immutable 请求摘要。
`novel-stage6-prepare-review-v1` 同时绑定 options SHA 与 exact request；来源、actor 和 token
由可信代码注入，accept/rework/uncertain 均通过独立 typed 决定，stop 不冒充审阅接受。

`main_text_sha256`、`hook_copy_sha256` 是正文阅读序与文本、原钩子文本的 canonical SHA-256，
不把源文件排版或无关审计元数据变成业务依赖。`_provenance` 保留原模型和用量；免费编辑
另记 `prepare_edit` 来源与理由。`subject_sha256()` 是支持分类、主张、连续范围及两份材料的
语义摘要；它与独立审阅使用的候选完整字节 SHA 职责不同。

`PrepareEdit` 和 `PrepareDecision` 都绑定当前完整候选 SHA 与 `s6p1_` 令牌；令牌还绑定
当前材料和上一份审阅的字节身份。新否定即使没有改候选，也会令旧编辑/旧接受失效。
同批决定可以携带完整 edit；actor、reason、主体和 token 必须一致。没有当前材料修订的
stale 候选不得先写负决定再卡进提交，必须先编辑或同批提交 edit。未决事实可以诚实保留，
未决开篇不得接受；Prepare 接受不生成 Hook，也不产生最终定稿许可。

所有业务写入共用 owner 锁。先整体预检，再将原请求、两材料身份、候选、审阅和全部目标
基线写入提交记录，随后按逐文件 CAS 补尾；全部目标、费用采用及会话尾账闭合后才 completed。
`--recover-edit` 只恢复已冻结请求，不能提交新 payload；第三方修改保留并拒绝覆盖。
`--recover-prepared <run> --subject-sha256 <proof>` 免费恢复原购回答，proof 为 typed checkpoint
SHA，或尚未生成 checkpoint 时的原 stdout SHA。JSON/形状损坏沿既有 `response-repair` 封存
修复稿，原 stdout、请求、费用不改写；生成 checkpoint 后原 stdout proof 仍可幂等补尾。
`stage6.prepare --revalidate` 只报告该节点固定的 `prepare_evidence_valid` 与
`prepare_semantic_review` 两项；机械通过可返回成功退出码，语义行仍为带当前独立决定状态的
`unver`，不能借此打开 `gate.s6`。Hook、恒等复检和最终 Stage6 报告复用 Prepare owner 的
实际证据核验与接受来源检查，并保留整层全部登记规则；恒等 0/1 表不为这两项生成假格子。
Hook 的接受闸校验材料与原决定时不把完整 MainText 再登记成模型输入；实际消费只读取
Prepare 语义投影，因此纯证据订正只重开受影响审阅，不无谓购买新 Hook。

### 2.1 `hook.json`

| 字段 | 类型 | 说明 |
|---|---|---|
| `lead_upto` | `int >= 1` | 连续开篇成稿下发上界；全书结构地图不受此上界限制 |
| `rationale` | `str` | 三条候选各自押注的吸引点 |
| `candidates` | `Candidate[3]` | 按当前逐槽配方产三条；策略可重复，归一后的文本不得重复 |
| `approved` | `ApprovedHook \| null` | 人工定稿；进入成书的第一段 |
| `sources.writer_context` | `ContextSource` | 写作上下文来源状态 |
| `judged` | `{path: JudgedDraft}` | 生成时使用的开篇成稿及其消费指纹 |
| `_provenance` | `HookProvenance` | 模型、prompt、输入、代码与参数来源账 |

`Candidate`：

| 字段 | 类型 | 说明 |
|---|---|---|
| `approach` | 枚举 | `HOOK_APPROACHES` 的一个取值；当前配方及退场取值见 §三 |
| `text` | 非空 `str` | 归一后的文案字节 |
| `mech` | `Mech` | 标点、引号、分段归一的计数 |
| `findings` | `Finding[]` | 该候选的机械判定；候选不属于正文章，`output_ordinal` 恒为 `null` |

`ApprovedHook = slot + text + origin='human' + mech + findings + resolution`。`resolution` 必须记录自报
`actor`（不是认证身份）、非空 `reason`、定稿文本的完整 `subject_sha256`、
刚读 workpack 的完整 `workpack_sha256`、
恒为 `human_verification` 的判定基础，以及可用的语义审判序号
（`semantic_review_seq` 可空——「人在零证据下拍的板」与「人读了第 3 轮判官证据」必须分得开）。`slot` 记的是**从哪一条候选来**（位置身份 `c1`/`c2`/`c3`）；
`None` = 没有候选前身（只可能见于既有的人手写记录；当前 apply-edit 不再新造这种绕过
三候选工作包的定稿）。⚠ 它曾是 `approach`（策略名 + 一个 `human_edit`），
而同一种策略采两条样本时策略名**答不出「是哪一条」**——那正是定稿要回答的问题。
策略名没有丢：由 `candidates[slot].approach` 现算（能 0 成本现算的字段不落盘）。
「它是不是人写的」由 `origin` 回答，与「有没有前身」是两件事。
`hook_edit.json` 的 `subject_sha256` 必须绑定经过本书标点、引号与分段归一链后的编辑文本；
校验发生在 `run_apply_edit` 写盘前，并复用与普通候选批准相同的字节绑定闸，
不能用 raw 上传字节的哈希绕过。`candidate=null` 只允许修改当前定稿的候选前身：运行时把
新文本写回原 slot 并清除批准，旧 workpack-bound approval 不再算 current；人须刷新工作包、
重读后再运行 `stage6.approve`。若旧定稿没有 slot，运行时关闭式拒绝，要求人工显式指定
`candidate=c1|c2|c3`；这样编辑后的正文一定进入三候选工作包，不存在绕过证据包的「第四条定稿」。

`HookProvenance.params` 除初始 `max_tokens` 外，还冻结单候选重试的 max tokens、共享内容上限、
`low` effort、`thinking=false` 与 `prompt_json_layout=pretty-v1｜compact-v1`；默认 `pretty-v1` 保持既有
请求字节，compact 只删 JSON 语法空白。`candidate_retries[]` 逐条保存 `slot`（哪一条候选被重试，不是哪种策略）、失败规则/证据指纹、
user/response 指纹、前后 HARD 数、cache policy、状态、是否采纳与失败理由。发生重试时
`prompts.HOOK_RETRY_SYS` 必须存在；没有脏候选时该键与历史均为空。

初始三候选归一并机械判定后，命中 HARD 的候选按槽位顺序进入 refresh-write 定向修订；
定位靠 `target_slot`（位置身份，唯一能选中一条候选的键）；请求只允许返回该条原有的 approach，
并看见完整事实包、整组候选和该候选的具体 HARD 证据。只有重试后
HARD 数严格下降且文本不与另两条重复时才替换该候选和整组 rationale；wrong approach、请求失败、
重复或 HARD 未下降均保留最近一次已验真的候选，并把原因写入修订账、提前停止该无进展路径。
若 HARD 严格下降但仍未清零，继续同槽修订，再处理后续槽位。整书初始生成、格式纠错和局部修订
共同计入同一工作单元的基础四次内容额度与已生效的不可变人工追加；缓存命中不买内容轮，
后续 wire 只能使用当前累计余额，每次网络尝试仍独立留账。
重启、换模型会话、槽位切换或候选变化不重发余额；同槽记录可有多条，各次 attempt 必须唯一连续。
若 wire 上互异的候选在归一后收敛成同一文本，producer 把完整付费产出落
`hook.INVALID.json` 并明确报出重复槽位；人改开至少一条后走 `--revalidate` 0 LLM 提升，
不能用一次无落盘的终态把缓存里的有效响应永久锁死。

### 2.2 `semantic_N.json`

| 字段 | 类型 | 说明 |
|---|---|---|
| `judged_texts` | `{真实标签: Fp10}` | 审过的去重文本集合 |
| `hook_file_sha1` | `Fp10` | 整份 hook 文件的审计指纹，不作新鲜度绑定键 |
| `reviews` | `HookCandidateReview[]` | 合并后的诊断视图（质量分取 A/B 平均） |
| `ranking` | `str[]` | 代码从合并决议与平均分确定性派生的真实标签排序 |
| `judge_rankings` | `{A: str[], B: str[]}` | 两名 judge 各自的原始解匿名排序 |
| `decisions` | `SemanticDecision[]` | 每份去重文本一条带 A/B 原始证据的合并 `UNVER`；唯一运行时判定源，不存在自动 PASS/FAIL |
| `evaluator_fingerprint` | `EvaluatorFingerprint` | 运行时 model/prompt/schema/decoder 四格指纹，**没有 dataset**；prompt SHA 只冻结 `judge_request` 产出的两份实际静态请求面，不冒充逐书动态材料身份 |
| `judged` | `{stage5/chNN.json: JudgedDraft}` | A/B 当时真正收到的开篇成稿；整文件 sha1 审计，`consumed.text_sha1` 判过期，未下发的后半书章稿不在此表 |
| `_provenance` | `LlmProvenance` | 本次质量审判来源账 |

逐书动态身份没有丢：hook、匿名材料、story truth/map 与正文输入由 provenance、agent run/checkpoint 的
prompt/input hash 及 `judged` 分别绑定。evaluator fingerprint 只回答静态 evaluator 是否换了 model、
prompt 模板、响应 schema 或 decoder；把动态正文也混进这里会让“判官实现变了”和“本书输入变了”
无法区分。

实验 layout 与 B-worker stagger 都进入 semantic decoder fingerprint；默认仍为 `pretty-v1 + 0ms`。
两名判官继续使用独立 session 并发，stagger 只在 B worker 内等待。静态公共前缀的字节数/SHA 是
诊断 observation，不写进 semantic artifact，也不保证 provider cache hit；实际命中只认 ledger usage。

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

## 三、候选身份与候选配方

**候选的身份是位置**（`HOOK_SLOTS = c1 / c2 / c3`），策略只是它的一个属性，**可以重复**。
两者曾是同一个东西（「三种策略各一条」），2026-08-23 拆开。

当前配方 `HOOK_RECIPE`（`contracts/invariants.py` 是唯一落点）：

| 位置 | approach | 功能 |
|---|---|---|
| `c1` | `payoff_preview` | 从全书挑最有吸引力的后期兑现/反转，制造“怎么走到这一步”的阅读欲 |
| `c2` | `scene_punchline` | 用可视可听的场面或对白砸出核心关系，以人物反应、危险或笑点收尾 |
| `c3` | `scene_punchline` | 同上，但 prompt 现算出一句硬要求：**必须锚在与 `c2` 不同的事件上** |

`HookResponse` 的模型级 validator 按列表位置逐槽校验 `HOOK_RECIPE`，并要求三条 wire 文本不重复；
producer 在归一后、落盘前再守一次正式文本唯一性。这里**不是**「每种策略各一次」——
后者在配方允许重复之后本身就是错的口径。

**已退场**：`source_architecture`（保住原作文案已验证的结构与最后一击）。
它仍留在 `Candidate.approach` 的取值面 `HOOK_APPROACHES` 里，因为盘上有存量产物；
但它在 `HOOK_RETIRED_APPROACHES` 上，排进配方会在 import 期当场断言失败，
出现在模型响应里会被配方校验器打回。

## 四、生成材料

`prompts.build_material` 是生成 prompt 与机械“给过什么”探针的唯一材料落点：

- `source_hook`：原作文案全文；
- `story_truth`：`characters` 为独立登记人物业务投影，`global` 为 setting/world_rules/relations/arc_secrets，`prepare` 为已接受事实支持与开篇范围；
- `whole_story_map`：每章章号、源章号、章目标、开头目标、全部 beat events、结尾目标；
- `rewritten_opening`：第 1 章至 `lead_upto` 的连续成稿；
- `source_hook_mechanical`：由 `novel/core/measure.py::hook_style_metrics` 从同一份 `source_hook` 即时计算，只有 `chars`、
  `avg_sentence_chars`、`avg_paragraph_chars`、`quote_styles`、`dialogue_ratio`；
- 原作文案目标字数、段间分隔、收尾形态仍由直接读取的 `source_hook` 现算，
  不把完整进程内测量对象或退役定性字段混入材料。测量契约进入
  `novel/consumption.py::stage6_hook_copy`，算法变化会让下游来源账自然 stale。

2026-08-24 的真实 A/B 冻结统计决议仍为 `inconclusive`；产品负责人随后以“无定性包时
至少有一篇事实安全且足够吸引人继续读”为删除门槛，确认 B 臂已满足。两层结论与历史证据入口
见 `docs/DECISIONS.md`，不得把产品裁决改写成预注册统计阳性。

原作文案可学结构但不可抄长句：任意连续 20 个内容字符相同即命中 `hook_reuses_source`。

## 五、匿名质量审判与批准

本节点没有 flag（`--dry-run` 只打印下发面）。运行时先冻结这一次的请求身份
`EvaluatorFingerprint`（`model` / `prompt_sha256` / `schema_sha256` / `decoder_sha256` 四格，
**没有 dataset**）。其中 `prompt_sha256` 由唯一拼装口 `judge_request` 对空动态 user 生成的
`[system, user-role-A, system, user-role-B]` 实际静态字节面计算；逐书匿名材料不在这格重复绑定。
随后把同一份匿名材料分别以 A/B 角色后缀发给两名 judge，
候选标签都是 `T1/T2/...`。评论、证据与质量排序必须显示在报告中，不能因为结论是 `UNVER`
就伪装成“0 份当前审判”。

⚠⚠ **角色后缀挂在 user 末尾，不挂在 system 末尾**，两名 judge 的 system 逐字节相同
（唯一拼装口 `novel/stages/stage6/semantic.py::judge_request`）。这是一条**成本判据**：
供应商按 `<BOS>{system}<｜User｜>{user}…` 的字节前缀做上下文缓存，后缀一旦回到 system 末尾，
前缀就在那里分叉、judge B 那一份约 45k token 的匿名材料**全额 miss**——而两份材料逐字节相同。
现役显式 Agent executor backend 路径为 A/B 各建独立 driver/session/cwd/provider session，并发请求缺失角色；
legacy `LlmClient` 包装仍按 A 后 B 顺序执行。后缀位置只保证公共前缀不会被代码确定性拆断，实际是否
命中供应商缓存仍取决于调度和供应商，不能把并发回归写成 cache hit 实证。双盲语义由「两次独立请求」
承担，不由后缀落在哪一侧或串并行承担。守卫见 `tests/test_stage6_semantic.py`、
`tests/test_stage6_semantic_agent.py` 与 `tests/test_stage6_semantic_agent_boundary.py`。

⇒ 因此本层 `_provenance.prompts` 的三个键与 `_common.md` §1.1「system prompt 指纹」的通用
说法有别，是本层特例：`HOOK_SEMANTIC_SYS`（两名 judge 共用的那一份）+
`HOOK_SEMANTIC_ROLE_A` / `_B`（挂在 user 末尾的角色后缀）。产地是同一个模块的
`judge_prompts`。⚠ 它们曾是 `HOOK_SEMANTIC_SYS_A` / `_B` 两个键、各存一份
`SEMANTIC_SYS + 后缀`：既把同一段 SYS 存了两遍，又在后缀已经不在 system 里之后继续自称 SYS。
validator 必须确认：

1. `reviews` 与 `ranking` 恰好覆盖全部匿名标签，且无重复；
2. 每条矛盾 evidence 是对应候选的连续原字节；唯一空白差异可回取成原字节；
3. 所有 `complete` 且无矛盾的候选排在矛盾或 uncertain 候选之前；
4. 六维分数都在 0~10；总分由代码计算；
5. 当前 hook 文本集合与审判的文本 sha1 / 决议 SHA-256 集合一致；
6. 当前 Stage5 **实际下发的开篇**成稿与 `judged` 的路径集合及 `consumed.text_sha1` 逐项一致，且
   semantic 产物新鲜。全书 accepted Review 仍须先可用；后半书改字并零成本重跑 Review 后，若这份
   开篇投影及其余 prompt 输入没变，不得让未下发正文继续制造假 stale。

批准动作 `stage6.approve --pick N --actor <id> --reason <why>
--review-token <s6r4_…>` 在归档和写盘之前执行两道硬检查：

- 该候选没有机械 HARD；
- 服务端在同书锁内重建工作包，opaque token 必须同时绑定当前 review identity 与本次
  validation seal，不允许旧页面误批。公开 v4 工作包只输出 opaque seal，不输出
  `code_version` / `rules_fingerprint` 原料；旧版本包不能再提交批准。

公开工作包固定 `check_basis=candidate_without_approval`：`check` 是去除 `approved`
后的候选检查，批准后也保留同一报告中的“尚未定稿”提示，不冒充当前审批状态。当前接受只看
`disposition/current_slot/current_approval`。基准说明进入本次 exact workpack/token，
不改变既有报告的 `review_identity_sha256`；批准动作本身不能使授权它的审阅身份失效。

⚠ 这里曾有第三道（所以上面那句原本写作“三道”）：「当前合并决议不是校准 FAIL，
也不存在与本文本绑定的历史校准 FAIL」。
它的输入是 automated FAIL，随自动放行门一并消失。**⇒ 没有任何机制会替人否决一个候选。**
全部语义结论进 `verification` 清单，人逐项核对后以 actor/reason/review-token 路径记
`human_verification`；矛盾线索仍逐条呈现（三条候选各一行，凭据键 `<候选>:<文本指纹>`，
可逐条 wontfix），但它们**报**而不**拦**。

`--revalidate --actor <id> --reason <why>` 与 `--apply-edit` 不能互换：前者对活动 `.INVALID`
重建派生字段、重判并强审计提升，保留原 prompts/model/inputs 与生成轮次，记录 raw/canonical
SHA-256、RFC 6901 改动路径和 `edited_by_human`；后者消费 v2 `hook_edit.json`，归档当前正式
hook 并形成一轮新的局部人工修订，改字后原 semantic 按文本身份自动过期。两条都是 0 次 LLM
调用，但 apply-edit 的人工内容工作量不等于零。

### 5.1 `stage6/wontfix.json` —— 语义结论的人工闭环

本层两条语义判据（`hook_semantic_contradiction` 与 `hook_candidate_contradiction`，
**都只提示不阻断**）的人工豁免走这里。前者曾是 HARD，随自动放行门下线降为 `unver`。

`stage6.hook --wontfix '<book>|<判据>[|<证据键>]=<理由>'`，形状与账本模型与 stage5 那份
**完全相同**（`novel/wontfix.py` 是唯一的机制落点）。本层特有的三条：

| 约束 | 说明 |
|---|---|
| **一律 `book` 级** | 候选是同一次调用产的三条，没有章这一维；候选身份在证据键里（`<候选>:<原字节>`）。写章号 ⇒ 显式终态 |
| **`coverage=uncertain` 逐条可寻址** | 它的证据是 `<候选>:<文本指纹>`。⚠ 原来是空列表 ⇒ 收敛键恒为空串 ⇒ 三条候选共用一个键，一次豁免会一并豁免三条 |
| **豁免不是通行证** | 它只降 `actionable` 并打标，批准闸那把尺子一格不松：字节绑定照拒、`basis` 仍只能是 `human_verification`、结论照旧留在报告里。⚠ 曾写作「`require_selectable` 照旧拒绝有矛盾的候选」——那个函数已删除，且**没有任何机制会替人否决一个候选**。「有人看过并决定不修」≠「这条不再是问题」，但拦住它的是读证据的人，不是闸 |

`at` 记的是人当时看的那一版：语义判据记 `semantic_<n>:<产物指纹>`，机械判据记
`hook:<产物指纹>`。**它是审计账，不参与匹配**——失效由证据自身保证（文本一改键就失配）。

## 六、现役判据

当前注册表共 **25 条**：**6 hard、8 info、6 metric、5 unver**。

| 判据 | 级别 | 作用 |
|---|---|---|
| `prepare_evidence_valid` | HARD | 准备事实的证据与连续开篇范围可从当前来源机械回证 |
| `prepare_semantic_review` | unver/semantic | 当前 Prepare 的事实支持与开篇范围已独立语义接受；机械通过不能替代接受 |
| `hook_empty` | HARD | 至少 20 个内容字符 |
| `hook_truncated` | HARD | 以完整句末标点收束 |
| `hook_reuses_source` | HARD | 与原作文案无任何相同 20 内容字窗口 |
| `hook_invented_name` | HARD | 人物称谓在完整下发面有据 |
| `hook_para_sep` | HARD | 分段档位符合原作文案，作为归一后置条件 |
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
| `hook_fact_attribution` | metric | 人物赠与类事实的弱字面归因读数 |
| `hook_source_distance` | metric | 与原作文案段的呈现结构距离：多档窗口重合率 · 最长逐字重合 · 末行重合。**度量不是门**——它补的是 `hook_reuses_source` 看不见的那一带（逐句改写措辞、却复用信息出场顺序并落在同一句收尾上，那道 20 字闸报 0 违规）。结构贴近本身不违约（`R-9.11` 尾注明许学结构），读数是给人裁决策略去留与定稿取舍的依据 |
| `hook_quality_rank` | metric/semantic | 六维分、代码总分与匿名排序 |
| `hook_semantic_contradiction` | unver/semantic | ⚠ **它永远产不出 Finding**：合并决议在类型上恒 `UNVER`，实现只有一条 `unverifiable(...)` 出口。曾是 HARD（靠 automated FAIL 阻断），随自动放行门下线降级。⇒ 它报的是「这条够不着，去读判官证据」，不是「查过没问题」 |
| `hook_not_approved` | unver | 是否已有人工定稿 |
| `hook_body_missing` | unver | 连续开篇范围是否缺成稿 |
| `hook_body_stale` | unver | 文案绑定的开篇成稿消费投影是否仍一致 |

## 9. 已封存完成组的零模型恢复

legacy Hook 与双 judge 的原回答均受持久工作单元预算约束。完成组封存原 typed 目标、完整 SHA、
原 run/parent、全部来源回答与采纳身份。`inspect_prepared_recovery` 只读验证原回答、费用、当前输入
和提交状态；结果为 recoverable/complete/unknown，缺新工作流 manifest 不创建服务账。
`stage6.hook` 与 `stage6.semantic` 的 `--recover-prepared <source_run_id> --subject-sha256 <proof_sha256>`
只按 exact 原证明补正式提交和采纳，不构造模型客户端、不需要付费确认或密钥、不重发内容预算。

队列与 Supervisor 在 fresh skip 和定稿之前优先处理未闭组；proof 损坏时不给 argv。完成后的旧组
核验原审计链后退出待恢复集合，合法批准改写 Hook 或新一轮输入不会复活旧补尾任务。此入口与
`--revalidate` 的 INVALID 原字节提升不同；它不会授予语义接受，也不替换独立 A/B judge。
