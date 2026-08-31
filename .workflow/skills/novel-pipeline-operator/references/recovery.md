# 修复状态机

## 目录

- [入口原则](#入口原则)
- [判定顺序](#判定顺序)
- [四类 remediation](#四类-remediation)
- [允许写入面](#允许写入面)
- [结构化响应修复](#结构化响应修复)
- [付费修复与缓存](#付费修复与缓存)
- [活动终态文件](#活动终态文件)
- [停止条件](#停止条件)

## 入口原则

先读取当前失败报告或终态原文，再选择修复。finding 中的 `fix`、`how`、`remediation`、`repair` 和证据是本次故障的第一依据；经验库只帮助解释，不得覆盖当前指令。

始终修复最早的根因层。下游报告因上游 stale/unver 变红时，不在下游伪造结论或重复付费。

任何会写盘的修复都先 `preview`，执行 preview 给出的全部步骤，前后打点，并以最新 check 复验。不要仅因命令 exit 0 宣称修复成功。

## 判定顺序

按以下顺序处理一次异常：

1. 保存实际命令、真实退出码、stdout/stderr、节点和时间。
2. 运行 `state`，确认正式产物、活动 `.INVALID/.rejected/.stalled` 与 freshness。
3. 能读取报告时定位 HARD；不能读取时保留完整终态原文。
4. 判断根因是数据质量、流程前置条件、代码结构、外部连接还是宿主中断。
5. 按 finding 的 remediation 选择最小正式入口。
6. 修复后先完成机械 check；只有机械产物 fresh 且需要语义判断时才重跑 semantic。
7. 将“为何选择这条路、付费边界、复验结果”写入终审包。

退出码的恢复含义：

- `1`：产物或报告通常已存在，按 HARD 修复。
- `2`：前置条件/显式终态；照原文补上游、参数或批准，不盲重试。
- `3`：契约自相矛盾；停止，保留栈和最小复现。
- `70`：结论未知；先查盘。产物完整且 check 可证明时继续，否则同参数重跑；同一点两次崩溃后停止。

## 四类 remediation

| remediation | 处理 | 成本纪律 |
|---|---|---|
| `deterministic` | 执行 finding 的 `fix` 指向的节点/flag，再跑 preview 追加的 check | 0 外部调用 |
| `llm` | 仅当 `repair == "UNIQUE"` 且 `unresolved_rounds < STOP_REWRITE_AFTER` 时运行 `stage5.write --revise` 或 finding 明列的付费入口 | 每个 `(章, rule)` 不超过代码中的轮次上限；每轮仍需两项授权 |
| `human` | 由受托 agent **自己**走下表的受托编辑面收口——`human` 说的是"归非模型输入修"，不是"归用户修"；必要时用 `--pick` 或机械结论可达的 `--wontfix` | 默认 0 外部调用；编辑不得绕过同一闸门 |
| `none` | 保留 metric/info 读数，不做修复 | 不为“全绿”制造动作 |

### `sev` 决定谁有资格花钱：只有 HARD 能触发付费重试

`remediation` 说的是"这条该怎么修"，`sev` 说的是"这条该不该拦"。两格分开读：

| `sev` | 交付含义 | 允许的动作 |
|---|---|---|
| `hard` | 阻断。按上表的 `remediation` 收敛 | 可按纪律触发付费入口 |
| `info` | 越线要出声，但不阻断成书 | **只披露**。不重跑、不重采、不付费 |
| `metric` | 无阈值读数，产品尚未标定 | **只披露**。抄进终审包读数表即可 |
| `unver` | 判据**诚实地说自己这次够不着**（分母为零、缺输入、可判定域之外） | **只披露**，并在终审包写清它够不着什么 |

⚠ **`unver` 不是"红"，也不是"绿"**。它出现在报告里恰恰说明判据在正常工作：
Stage4 的 `state_closure` 就是现役例子——章内 `earlier`/`later`/`parallel`/`return`
这些非线性边界没有完整故事时点真值，判据只判 `continue` 承接、唯一无歧义支路的
`return` 锚点恢复与章首/章尾闭合，其余逐项证据放进 `semantic_closure_reading`
交结构人审。看到它**不要**去重跑 `stage4.design`——那是一次整书付费，而报告
本来就在如实说话。

⚠ 同理，`causal_disclosure`（披露逆因果）是 `info`：倒叙本来合法，读数只报不挡。

⇒ 判据表里那一格 `sev` 是唯一权威（`contracts/judges/`）。把 `info`/`metric`/`unver`
当成"还没修完"，会把一次正常交付变成一串付费重试，而盘上什么问题都没有。

### `repair` 与 `fix` 是两格，不要拿一格去否定另一格

`llm` 那一行的 `repair == "UNIQUE"` 前置**只管 stage5 的付费自救环**——
`contracts/rules.py::_validate` 断言 `repair != 'NONE'` 仅允许 `owner == 'stage5'`，
所以**其余层全表 `repair='NONE'` 是结构常态，不是"这条不许修"**。
其余层的付费修复入口在另一格：`Rule.fix`（节点 + flag）配 `Rule.fix_args_from_finding`
（实参从 finding 现算，例如把命中章算成 `--chapters 3,6`）。

⇒ 看到 `remediation='llm'` 而 `repair='NONE'` 时不要停机，那是 stage2 这类层的正常形状；
按 `fix` 那一格走，而最省事的读法是 `inbox`——它已经把这两格合成一条可复制命令了。

⚠ 反过来也要成立：`stage5.write --revise` 只吃 `repair='UNIQUE'` 的条目。
密度与篇幅类判据（篇幅上下限、形态/对白比/明喻/「心里」密度这一族）的满足路径不唯一
（"写足"最省力的写法就是注水，而 directive 明写不要注水），因此一律 `repair='NONE'` ⇒ 转人工。
拿 `--revise` 去修它们会得到 exit 2「这一轮没有可自救的条目」并列出被跳过的条数，
**且不产生任何调用**（账本可证）。那不是失败，是产品在说"这一层没有自动返工路"，
剩下的只有 `--apply-edit` 提交整章全文，或 `--wontfix` 记一条明确的不修裁决——
**两条都由受托 agent 自己定**（理由见下面「允许写入面」开头那一节），不是回头找用户的信号。

`deterministic` 那一行的入口**由 finding 的 `fix` 给出，不要自己挑**。注册在案的修复路由有三条 flag：
`--renormalize`（`stage2.global` / `stage2.blueprint`，按当前契约重算代码派生字段与原文定位并写回，
修锚点逐字、悬空 id、secret 时序这类定位问题）、`stage2.blueprint --chapters <N>`（定向重生成那几章，**这一条付费**）、
以及 `stage2.blueprint --patch`（**付费但量级是条不是章**：`extraction-ir-v1` 契约下 `hinge_ambiguous` 的修复路——
代码枚举候选命中位、模型只挑 candidate_id；有整章级根因时它会显式拒绝并指路 `--chapters`，先整章、后字段）。
另有同样 0 成本、会写盘但不一定由 finding flag 指名的重算入口，只在读数需要刷新时用：
`--recheck`（`stage5.write` / `stage6.hook` / `stage7.rename`，刷新已有成稿的派生指标；Stage5 同时对账 trim 状态）。
**它们都写盘**，所以照样先 preview、前后打点、以最新 check 复验；只是不发起外部调用。

`llm` 那一行的「finding 明列的付费入口」在 `stage5.write --revise` 之外还有三条注册在案的定向返工路，
**它们都要再调一次模型**，只在对应 finding 指名时才跑，不作为「提高质量」的自选项：

| flag | 节点 | 它修什么 |
|---|---|---|
| `--purge-role` | `stage2.global` | `role_leaks_fate` 的唯一修复路：把 role/fate 双向重划分一遍 |
| `--resample` | `stage2.global` | 全书响应被闸门拦下后，绕过同参数问题缓存重新采样整份响应 |
| `--patch` | `stage2.blueprint` | `extraction-ir-v1` 专属的字段级修复（`hinge_ambiguous` 候选选择 / 编的短语重给），逐条失败各调一次模型，量级是条不是章；详见 `docs/stage2_contract_experiment.md` |

同一修复臂连续失败达到上限时停止该臂；读新 finding 后切换到不同、证据支持的路由不算重复同一路。不要通过改参数、删缓存或删除 finding 伪装收敛。

## 允许写入面

正式产物必须由 CLI 生成。受托 agent 只可直接创建或编辑下表的输入，并只经下表的入口落盘；
路径、形状和前稿要求以**当前终态文案**为准（`stage2.global --emit-review-template` 与
`stage7.rename --emit-edit-template` 能现取绑定当前主体的骨架，其余层照终态报出的字段名与
schema version 写），不按记忆手抄。

### ⚠ 「人工」不是「不许 agent 用」

本仓的「人工」指**非模型生成、由署名者负责的输入**，不指「必须由真人操作」。下表这些入口
**就是本 skill 的工作面**——包括 `stage5.write --apply-edit` 提交整章正文、
`stage6.hook --apply-edit` 重写开篇文案这两条最像"该交回用户"的路。

读到「人工修订」「人产输入」「转人工」「human_only」时不要读成"这一步得回头找用户"。
那样读的代价是确定的：一条 0 成本、已经授权、你完全有能力走完的收口路被留成余额，
用户拿到的终审包里多一条本可销掉的待办——而本 skill 存在的理由正是消掉这类待办。
署名用 `<ACTOR>` = `agent:novel-pipeline-operator`，责任就落在这次代批上，可追溯、可回查。

真正的边界不在"谁能操作"，而在两条别的地方：**只走下表的入口**（不直接覆盖正式产物），
以及**代笔正文时自己补上没有守卫的那几项核验**（文风、保真材料、在场集——见
[经验库](field-notes.md) FN-017）。

下表按**节点**索引，覆盖每一条 0 成本、但**会改动盘上产物**的入口——包括不需要另写输入文件的那几条
（`--revalidate` / `--renormalize` / `--recheck` / `--identity` / `--adopt-residual` /
`--adopt-rejected`）。
**“0 成本”不等于“只看不动”**：真正只读的是 `--dry-run`、`--check-only`、
`--names-only`、`--emit-review-template`、`--emit-review-workpack`、`--emit-edit-template`、
`--emit-prepared-recovery-workpack`、`--validate-edit`，以及
`approval workpack design|structure`、`stage4.plan --identity` / `stage6.hook --identity`。
表外的 0 成本 flag 一律按会写盘对待。

| 节点 | 0 成本写盘入口 | 需要 agent 自己写的输入 | 约束 |
|---|---|---|---|
| `stage0.edit` | `--apply-edit` · `--apply-edit-plan` · `--apply-candidate-plan` | 正式 edit / edit-plan / candidate-plan 形状 | 不直接覆盖 `clean_text.txt`：三者都只向 `revisions.json` 追加一版，再由链物化唯一 clean 投影。`--apply-candidate-plan` 裁决机器候选（`adopt` / `dismiss`），与 edit-plan 同样带 `expected_revision_id` 乐观锁。⚠ 「采纳后再编辑」不是第三个动作，是 adopt 后照常走 `--apply-edit-plan` |
| `stage2.global` | `--apply-edit` · `--review-decisions` · `--renormalize` · `--revalidate` | 当前 `global_edit.json` 的 typed 修订；`stage2/global_review_decisions.json` | 本层**有**通用 `--apply-edit`。先生成并完整阅读 `--emit-review-workpack`，再取 `--emit-review-template`：默认 blocking 模板是一条 `UNVER` scope，且 workpack SHA 为全零占位；必须从实际读过的 workpack 顶层手工复制真实 SHA，不能靠重发模板刷新阅读凭证。`decisions[]` 只列逐项例外，服务端展开并按 item subject hash 持久化；要逐项裁 disclosure 时用 `--review-scope all`。subject/scope/MainText/workpack 任一漂移都关闭式拒绝 |
| `stage2.blueprint` | `--renormalize` · `--revalidate` | finding 明确允许时编辑活动 `.INVALID` | 本层确实没有 `--apply-edit`；逐章内容的修正走 `--chapters <N>` 定向重生成（**付费**）或回上游 `stage2.global`。只改证据指向字段，不改生成轮次与来源账 |
| `stage4.design` | `--adopt-rejected` | 旧整书 topology 隔离区里那份固定的 `_llm_rejected/<chain>/<attempt>/candidate.json`（不存在时按原响应原样重判） | **0 LLM 调用，只是 legacy whole-book validator 拒稿的原字节采用入口**——它不能采用当前 strategy + N chapter split-run 的单 unit 拒稿，也不能冒充正式 A 人审后的付费 `--repair-decision`。通用 `--response-repair` 只收 parse/require/schema。用法：`stage4.design --adopt-rejected <attempt_id> --actor <ACTOR> --reason <text>`；attempt_id 从 `inbox` 或 `_llm_rejected/` 取。候选按**当前**四道闸重判，改了什么由 JSON pointer diff 进 provenance。一份拒稿只有一次采用机会；失败后不得伪装成已修复。**A** split-run unit 的 0-wire adoption 仍是活动待办，当前不得把缺 lineage 的 unit 拼成正式 A；B 的章级 replacement 见下节 |
| `stage4.plan` | `--apply-binding` · `--apply-edit` · `--revalidate` · B unit `response-repair` | 当前 `EvidenceBindingResponse` 业务 JSON；`<novel-dir>/stage4/plan_edit.json`；终态明确允许时可修活动 `.INVALID`；B 终态点名的 `_llm_rejected/<chain>/<attempt>/candidate.json` | `--apply-binding` 只在当前 A 正式审阅全 accepted 时开放，服务端铸 `origin=human` 并走与 B 相同 compiler/HARD；B unit replacement 只接受终态给出的 attempt，封存后以原 run 重跑，同一章 validator、整书 compiler/HARD 全部重判且 0 wire。`--revalidate` 与 `--apply-edit` 也只落 Plan。各入口都**不生成 writer context**，须在最终结构全 accepted 后单独运行 `stage4.context`。`--identity` 只作内存诊断 |
| `stage5.write` | `--apply-edit` · `--recheck` · `--revalidate` · `--recover-prepared` | **`<base>/_service/edits/` 下**、文件名含 `chNN` 的完整 `ChapterResponse`：`{"segments":[{"slot":"s01","text":"…"},…]}`；prepared recovery 则消费前一步 emit 的 hash-bound workpack | **0 LLM 调用。** `--apply-edit` 必须提交该章全部 producer-owned slot 的全文；`--recheck` 不改正文或写作 provenance，只刷新当前契约的派生指标并对账 trim；`--revalidate` 原字节重判 `.INVALID` segments。`--recover-prepared` 只收口旧 run 已完成且完整复验通过的 initial-write checkpoint，写 canonical 章稿、target recovery manifest/两相 journal 与 append-only adoption lineage，旧 source sidecar 始终只读；完整顺序见下文专节。不直接改正式 chapter JSON 或其他章。⚠⚠ **编辑文件落点不要按上面四层类比写进 `<novel-dir>/stage5/`**：那四层的编辑文件登记在 `ARTIFACTS` 里（所以必须在书目录内），本层**刻意没有登记**（`novel/paths.py::edit_rel` 的例外一段），写进书目录会让 `tests/corpus.py::_classify` 关闭式失败 ⇒ **这本书从此当不成语料底座**，而报错文案还会把人引向「加进 ARTIFACTS」那条错路（真发生过）|
| `stage5.review` | `--retain-pretrim` · `--retain-pretrim-batch` · `--wontfix` | 无人产文件；决定本身就是输入 | `--retain-pretrim --chapters <N> --actor <ACTOR> --reason <text>` 恰好一章，写 hash-bound `trim_resolution_chNN.json`，不改正文、不采用收缩候选（见 FN-014）；`--retain-pretrim-batch <manifest.json>` 一次销账多章：manifest 含共享 actor/reason 与逐章 audit/chapter SHA，整批预检、任一漂移即整批不开始，已销账章幂等跳过（受托 agent 不使用它——逐章销账须由 Commander 亲自提交）；`--wontfix` 只把豁免记进 `stage5/wontfix.json`，**一份验收报告都不产** |
| `stage6.hook` | `--apply-edit` · `--recheck` · `--revalidate` · `--wontfix` | `<novel-dir>/stage6/hook_edit.json` | 一次只改目标候选；随后重跑机械与语义审判 |
| `stage7.rename` | `--apply-edit` · `--adopt-residual` · `--identity` · `--recheck` · `--revalidate` | `<novel-dir>/stage7/name_map_edit.json` | 先用 `--emit-edit-template` 取当前注册骨架，改后用只读 `--validate-edit <路径>` 走同一构建/判定链，确认后才 `--apply-edit`。⚠ `--adopt-residual high` **重写 `stage7/name_map.json`、清旧审批、退休现有成书**。`--identity` / `--recheck` 也重写 map 并退休旧成书，但会在覆盖 provenance 前先核旧消费输入：旧输入 stale/unknown 或决策主体变化时清批准；只有旧输入 proven fresh 且主体不变时才保留批准。任何清批分支都须重新 hash-bound approve、assemble 与逐字对账 |

非节点的第九个写入面是终审包 `<novel-dir>/<书名>_review.md`（落书目录根，不在任何 `stageN/` 下；确定路径由启动 prompt 给出）：只供用户读取，可随跑更新，不得承担产物修复功能。

不得直接修改：正式 JSON/TXT、成书、`_service/*.jsonl`、`.llm_cache/`、`_rounds/` 历史、旧 run 的输出目录。正常信标只由 runner 经 `mark` 写入；坏尾恢复也不能手改 JSONL。

### Append-only 编排账的 non-LF 坏尾

进程若恰在最后一行 LF 提交前被终止，admission 与 beacon 写口都会关闭式拒绝继续拼接。只能由
**Commander 子上下文之外**的操作员先取当前 committed-prefix/tail 双 hash 主体，再用完整
`actor + reason + subject_sha256` 应用；不得加 `--force`，不得补 JSON、补 LF 或猜一条 end：

```text
<PY> -X utf8 -u -m novel.cli reconcile --base <BASE> --beacon-repair-tail
<PY> -X utf8 -u -m novel.cli reconcile --base <BASE> --beacon-repair-tail --actor <ACTOR> --reason <REASON> --subject-sha256 <SUBJECT_SHA256>
<PY> -X utf8 -u -m novel.cli reconcile --base <BASE> --admission-repair-tail
<PY> -X utf8 -u -m novel.cli reconcile --base <BASE> --admission-repair-tail --actor <ACTOR> --reason <REASON> --subject-sha256 <SUBJECT_SHA256>
```

apply 只把 non-LF 原始字节隔离到 `_service/*_tail_repairs/` 并截回已提交前缀，不代签旧
execution 或 admission 已完成。Beacon 修复后由真实写手重放缺失的完整 end；孤儿 admission 仍须另取
`--admission-parent-run-id <RUN_ID>` 的 current subject 并走 typed reconcile。任一主体漂移都重新 inspect，
不得沿用旧 SHA。

`--apply-edit` 的路径解析**逐层不同，不要当成通则**（见 [经验库](field-notes.md) FN-016）：
`stage4.plan` / `stage6.hook` / `stage7.rename` 按**书目录**解析相对路径，
也接受书目录内的绝对路径，目录外路径和 `..` 穿越关闭式拒绝；`stage5.write` 与 `stage0.edit`
收任意路径——服务进程把人工改稿放在 `_service/edits/` 下，依赖这一点。
`--review-decisions` 与前一组同规矩。操作包一律优先记录解析后的绝对路径，便于复核。

### `--revalidate` 与 `--apply-edit` 不是同一条路

- `--revalidate --actor --reason` 是强审计：只提升当前 `.INVALID`，记录审计人、原因、
  raw/canonical hash 及结构化差异，保留原生成轮次和来源账。它不是“人改了一轮”。
- `--apply-edit` 消费明确的 typed 人工输入，开新人工修订轮，并让已绑定旧主体的下游结论/
  approval 按契约过期。它是 0 LLM 费用，不等于没有人工输入或可以跳过重判。

## 结构化响应修复

### Stage4B 章级 0-wire replacement（现役 agent 路）

现役 agent 主跑仍**不带** `--response-repair agent`。但 Stage4B 某一歧义章若在动态 validator
用尽轮次，或聚合后的 B-owner HARD 已按章唯一定位，节点会先保住其他章 checkpoint，再为该章留下
绑定原 `run/request/code/subject/attempt` 的一次性 work item，并在终态打印可复制命令：

```text
<PY> -X utf8 -u -m novel.cli response-repair <SID> --base <BASE> \
  --attempt <ATTEMPT_ID> --actor <ACTOR> --reason <REASON>
```

只编辑终态点名目录里的 `candidate.json`；不得改 `original.txt`、`rejection.json`、
`resolution.json` 或 checkpoint。提交会把候选与 SHA-256 封进一次性 resolution；随后用**同一 run id、
同一 Stage4B 参数**重跑。恢复必须 0 wire，把封存候选重新送过 `ChapterBindingResponse` schema、原章
dynamic validator、确定性聚合、整书 compiler 与全部 HARD；正式计划真正提交后 sidecar 才能记
`accepted`。任一身份漂移、第二份 resolution 或首次 replacement 仍失败都停止，不换 run、不删目录、
不重发其他已验章。完整 `stage4.plan --apply-binding` 是另一条整份人工绑定入口，不能拿来绕开该 unit
的 attempt/lineage。

### 显式 DeepSeek 回滚

八个现役 agent executor 的主跑**不带** `--response-repair agent`：候选解析、schema、动态判据与
业务 HARD 都由代码把结构化反馈交回同一 provider session，append-only checkpoint 与正式提交
journal 负责崩溃恢复。不要在 Commander 或直接 executor profile 的 argv 上补这个 flag；它不是
Claude Code/Codex executor 的能力开关，补上只会改变作业身份并被关闭式拒绝。

下面到“其他 legacy 结构化节点”为止只属于独立保留的**显式 DeepSeek 回滚路径**。只有操作者明确选择 legacy DeepSeek、
没有携带 executor profile，且当前终态逐字给出该 sidecar 时，主跑才可带
`--response-repair agent`。被拒响应保留在书目录
`_llm_rejected/<chain>/<attempt>/`。终态点名的 `candidate.json` 是**唯一允许直接编辑**的响应文件；
`original.txt`、`rejection.json`、`resolution.json`、`outcome.json` 与 Stage2 run sidecar 都是审计/
恢复状态，不得修改。修复原则是不发明剧情或事实：完整通过的 reasoning JSON 已由生产立即采用；
BOM / 容器尾逗号由代码窄修；agent 只忠实补 JSON、必需键或 schema 形状。

### Legacy Stage2 blueprint：一份 batch、一次 resume

Stage2 不在第一章 pending 时立刻暂停。它先收完同一并发波次，把所有已过完整闸的成功章写成验证后
checkpoint，再把全部可修 attempt 稳定排序到唯一 `repair_manifest.json`。按以下顺序处理：

1. 记录终态打印的 `run_id` 与 manifest 路径。逐项读取 manifest 点名的 `original.txt`、
   `rejection.json` 与 `candidate.json`；只编辑 candidate。
2. 建一份 decisions JSON，attempt 集合必须与 manifest **完全一致**，每项都写非空理由：

   ```json
   {"decisions":[
     {"attempt_id":"<ID>","action":"submit","reason":"<忠实修复依据>"},
     {"attempt_id":"<ID>","action":"decline","reason":"<无法安全补全的依据>"}
   ]}
   ```

3. 一次提交整包：
   `<PY> -X utf8 -u -m novel.cli response-repair <SID> --base <BASE> --batch <MANIFEST> --decisions <JSON> --actor <ACTOR>`。
   CLI 会先全量校验缺项、多项、重复、run/hash 漂移与每份 submit candidate，全部通过后才原子封存
   一份 `batch_resolution.json`；没有“先成功一半”的状态。resolution 内已封存 candidate 与 SHA，
   提交后再改草稿不生效。
4. 从 sidecar 打印的命令取得 canonical run id，再用 runner 的 `--run-id <ID>` 控制参数与原节点、
   原 flags（含 `--response-repair agent`）resume；runner 会把同一身份传给付费 Step、自动追检与
   start/end beacon。不要另起 run、改参数或把 batch attempt 改走单项 submit。恢复会重新执行当前
   schema/validator/scope、整书 compile 与联合 HARD；同一 run 不会形成第二份人工 workpack，后继
   失败只允许升级到已登记的自动动作或终态。

Stage2 的自动动作按失败类型收窄：有可忠实恢复材料的 `answer_lost` / parse / require / schema 在
batch 后最多一次 Flash 定向修复、再最多一次 Pro 约束重建；dynamic validator 不进通用 JSON
batch，只走 Stage2 定向恢复与 Pro 重建；
无材料 `answer_lost` 直接 Pro 重建；`finish_reason=length` 不做同配置重采。429/5xx/timeout 的有界
传输退避单独计算，不可重试错误立即终止。同一 run 的相同失败签名不得重复同一动作，看到“拒绝继续
白烧”时应停止，不得删账或换身份绕开。

### 其他 legacy 结构化节点：保留单 attempt 路径

非 Stage2 batch 终态仍按既有单份入口处理：能忠实修复时编辑 candidate 后执行
`<PY> -X utf8 -u -m novel.cli response-repair <SID> --base <BASE> --attempt <ID> --actor <ACTOR> --reason <text>`；
不能修时加 `--decline`。每份响应只允许一次决定，随后以原节点、原 flags 重跑。后继请求从不可变
失败证据构造定向修复或 Pro constraint rebuild；失败候选始终是**不可信数据**。client 按上下文预算
选择完整候选、诊断摘要或最小诊断，effort / thinking / max_tokens 与四道内容闸不变，审计以
`request_mode` / `recovery_from_attempts` 留下联结。

`response-repair` 是 legacy DeepSeek 的零 LLM CLI sidecar，不是 node，不另造 preview。账本会恢复中断前已发生的
调用与 accepted-attempt 联结；不能删隔离目录、Stage2 checkpoint、换参数或清缓存来“重新开始”。

## Stage5 foreign prepared 的显式 0-wire 收口

默认 Stage5 写作若在 client/driver 前报告某个旧 run 仍持有 foreign `prepared`，不要换
`--chapters`、不要清 sidecar，也不要再授权一次新写作。先选一个不同于 source 的新
`TARGET_RUN_ID`，并在 emit 与 apply 两步中复用**同一个** target run。仍须经本 skill 的唯一
runner 先 preview、再执行；下面四条中的 `<RUNNER>` 是
`.workflow/skills/novel-pipeline-operator/scripts/run_step.py`：
先分别把 emit 与 apply 的 exact node flags 交给 `operator permit`，固化返回的
`<EMIT_PERMIT_SHA256>` / `<APPLY_PERMIT_SHA256>`；两种动作不得共用一枚许可。

⚠ 这两个入口刻意不开放给 `POST /api/jobs/preview` 或 `POST /api/jobs`：通用 HTTP 作业每次都会
生成新的 job/run id，无法复用工作包已经绑定的 target。不要从作业面板启动，也不要删除
workpack subject 里的 target 绑定；只使用下面可显式传同一个 `--run-id` 的 CLI runner。

```powershell
<PY> -X utf8 -u <RUNNER> --base <BASE> --sid <SID> --expect-code-version <START_CODE> --operator-permit-sha256 <EMIT_PERMIT_SHA256> --run-id <TARGET_RUN_ID> stage5.write -- --emit-prepared-recovery-workpack <SOURCE_RUN_ID>
<PY> -X utf8 -u <RUNNER> --base <BASE> --sid <SID> --expect-code-version <START_CODE> --operator-permit-sha256 <EMIT_PERMIT_SHA256> --run-id <TARGET_RUN_ID> --execute stage5.write -- --emit-prepared-recovery-workpack <SOURCE_RUN_ID>
<PY> -X utf8 -u <RUNNER> --base <BASE> --sid <SID> --expect-code-version <START_CODE> --operator-permit-sha256 <APPLY_PERMIT_SHA256> --run-id <TARGET_RUN_ID> stage5.write -- --recover-prepared <SOURCE_RUN_ID> --actor <ACTOR> --reason <REASON> --subject-sha256 <WORKPACK_SUBJECT_SHA256>
<PY> -X utf8 -u <RUNNER> --base <BASE> --sid <SID> --expect-code-version <START_CODE> --operator-permit-sha256 <APPLY_PERMIT_SHA256> --run-id <TARGET_RUN_ID> --execute stage5.write -- --recover-prepared <SOURCE_RUN_ID> --actor <ACTOR> --reason <REASON> --subject-sha256 <WORKPACK_SUBJECT_SHA256>
```

前两条预览并执行纯读 emit，执行结果打印工作包；后两条预览并执行 apply，必须原样提交工作包的
`subject_sha256`，并一次覆盖 source run 全部 unresolved prepared，不能用 `--chapters` 挑选。
两种入口都禁止 executor profile、egress、`--revise`、response repair，不会构造 driver、probe、
命中缓存或发 wire。apply 会在 target current
code 下复验 source run/profile/request/schema/checkpoint/session/ledger/commit、当前上游授权、
HARD 与 live baseline；source sidecar 始终只读。中断后只可用同一 target run、同一 actor/reason/
subject 重跑，让逐章 recovery journal 从 `prepared` 补到 `committed`；另起 target 会被稳定
source adoption id 拒绝。全部收口后单独运行 `stage5.review`，旧 Review 不参与阻塞尾窗恢复，
但也不会被恢复动作自动刷新。

## 付费修复与缓存

付费命令中断、网络失败或 exit 70 后：

1. 先查 `state` 与目标产物；账本写入不证明产物完整。
2. 目标缺失或残缺时，用**完全相同参数**重跑，优先命中 `.llm_cache`。
3. Stage2 blueprint 若已经产生 run sidecar，必须复用终态打印的同一 `run_id`。通过全部单章闸的
   checkpoint 会在当前 schema/validator/scope 复验后恢复，命中记 `checkpoint_restored`、新增 wire 为零；
   它不是 `.llm_cache`。`--chapters` 主动刷新只有在本轮唯一 batch resolution 已完整封存后才接受
   这次同 run resume；普通重复执行不能冒充 resume。损坏、跨 run、输入/SHA/ledger 漂移必须停机，
   不能把它当 miss 后再付一次。
4. 不为“确保重跑”修改章节范围、模型、思考参数或 prompt；那会改变请求身份并形成新付费调用。
5. 只有 finding 或 current hash-bound typed 决定明确要求重新采样时才使用刷新入口：`stage2.global --resample` 重采整份响应，`stage2.blueprint --chapters <N>` 只重算目标章；首次执行都不得命中旧 checkpoint，后者仅可在已封存 batch 后恢复本轮 checkpoint。Stage4A 逐章拒绝只执行服务端给出的 `stage4.design --repair-decision <decision_sha256>`，仅重跑 `request_repair` 章并引用其余已验 checkpoint；策略 `request_replan` 才允许整书重做。两者都必须重新 preview/付费确认，修订后重新完成 A 全书原子审阅，B 仍另行确认。
6. 连接错误使用实现内有界退避；实现耗尽后不要再叠加无限外层重试。
7. **上游正式产物一旦变更，下游缓存全废。** `--revalidate` 提升、`--apply-edit` 新轮、定向重采样都算变更：以它为输入的下游节点缓存键随之失效，下一次运行是**实付**而不是回放，而且是整层重付、与该层首跑同量级。选择修复层之前先估算下游重算面：同一问题在下游有等价的正式修复入口时优先在下游修；只能在上游修时（如 `present_names_invented` 只有 stage2 有入口），在终审包把这笔重算记成**修复的直接代价**，不要读成重复付费。

stage5 调用次数高于接受章数可以来自传输重试、内建重采 / 降档，或后来显式授权的付费
`--revise` / 定向重写；现役没有自动长度收缩臂。最终调用/token 成本的唯一权威源是
`<BASE>/_service/ledger.jsonl`；产物 usage 和日志不另行混加。账本中的 `cached == true`
为缓存回放，实付为零；reasoning token 为零或空不等于失败。

修改 Stage5 正文后的依赖闭包（全部先 preview）：

1. 必须重跑 `stage5.review`；旧 `wontfix` 只按 `(章, rule, evidence_key)` 绑定，正文变化后
   新证据键不会继承旧豁免。只重新裁决**当前仍命中且未豁免**的条目，不删旧账。
2. 若修改章落在 `stage6/hook.json.lead_upto` 消费范围内，运行 `stage6.hook --recheck`。
   它刷新正文绑定；批准文本字节未变时可保留当前批准。`stage6.semantic` 绑定的是候选文本，
   候选字节与 evaluator 指纹均未变时不白跑付费语义审判；任一变化才重跑。
3. 任一正式章正文变化都会让 Stage7 的 `judged` 绑定过期，因为人物替换消费全书。
   运行 `stage7.rename --recheck`；它会退休旧成书。若当前决策面未变且实现保留批准，核对
   `approved.subject_sha256 == approval_subject_sha256` 后可直接组装；批准被清除则取当前完整
   SHA-256 重跑 `stage7.approve`。最后总要 `stage7.assemble` + 成书逐字对账。
4. 正文没变时不为“保险”重复跑上述写盘入口；先用只读 check/freshness 证明是否需要。

## 活动终态文件

只把书目录当前层级的 `.INVALID/.rejected/.stalled` 视为活动状态；`_rounds/` 中同名文件是审计历史。

正式稿与活动 `.INVALID` 同时存在时，不自行删除任一文件。读取当前 `state`、报告选择逻辑和最新轮次；用正式修复入口使活动失败归档。若成功重建后活动失败仍遮蔽正式稿，把它作为代码缺陷停止并报告。

`inbox` 八类的读法（顺序与 CLI 首行的计数顺序一致）：

- `invalid`：首次或当前轮被落盘闸拦截。
- `rejected`：修复稿再次被驳回，读取 `_rounds/` 指针但不编辑历史。
- `stalled`：达到自动修复轮次上限，转受托人工编辑。
- `repairable`：本层 HARD，且 finding 已经绑定确定性 `fix` 路由；按该行 `how` 给出的节点/flag 收敛，多为 0 成本。`fix` 不可执行时该行会写明原因，此时按 `upstream` 或人工路处理。
- `human_only`：需要语义/审美阅读才能判的项，**读的那个人就是你**；逐类裁决，但不一定阻断。名字里的 `human` 只是在说"没有判据能自动判它"。
- `verification`：可由人工证据消解的决策项，**三个来源各有自己的 0 成本销账入口**，应销账而不是留成余额：
  - stage2 全书评测项 → `stage2.global --review-decisions <书目录内 json>`（按 `actor + 理由 + subject_sha256` 提交 PASS/FAIL/UNVER，subject hash 变更后旧决策会被拒绝）；
  - stage5 超限章 `trim_audit_chNN` → `stage5.review --retain-pretrim --chapters <N>`（见 [经验库](field-notes.md) FN-014）；
  - stage6 候选级 UNVER → `stage6.approve --pick <N>` 的 hash-bound 人工核验。

  它**不阻断成书**，但必须在终审包按类计数披露，不得并进 `human_only` 或算作已解决。
- `upstream`：根因位于更早层，回上游处理。
- `approval`：标准人工闸门；按代批契约自行处理。

`unver` 永不伪装进任一已解决类别；在终审包单独披露。通用 check 报告里的 `unver` 结构上不进这张清单——它是判据的边界，不是待办。

## 停止条件

满足任一条件时使用 `stopped`，写清现状与恢复命令后结束：

- 用户明确收回了付费授权或正文外发授权（headless 则是 launcher prompt 未授予），而下一步必须调用外部模型；停止前仍完成只读 `state`、`inbox` 与下一节点 `preview`，不得执行 preview 中的付费步骤。⚠ 两项授权默认都在，**「没有人特意重申授权」不构成本条**。
- 同一节点同一根因在允许的修复路由与轮次上限内仍不能收敛。
- exit 3，或 exit 70 在同一点重复且无法由盘上产物证明结果。
- 需要改变产品语义、公开契约、模型策略或仓库代码才能继续。
- `redo/unknown` 无法通过只读证据确定安全的最小重放面。
- 输入产物疑似被人工直接改写、来源账失真或历史目录被污染。

停止不是失败的伪装。终审包要准确说明已完成范围、阻塞影响、是否产生费用、可复用缓存和下一条安全命令。
