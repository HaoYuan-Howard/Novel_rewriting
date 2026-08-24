# 修复状态机

## 目录

- [入口原则](#入口原则)
- [判定顺序](#判定顺序)
- [四类 remediation](#四类-remediation)
- [允许写入面](#允许写入面)
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
`--recheck`（`stage5.write` / `stage6.hook` / `stage7.rename`，刷新已有成稿的指标并重拼 `draft.md`）与
默认 `stage3.style`（从当前 Stage1 完整重建当前机械基线）。
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
（`--revalidate` / `--renormalize` / `--recheck` / `--identity` / `--adopt-residual`，以及 Stage3 默认免费重建）。
**“0 成本”不等于“只看不动”**：真正只读的是 `--dry-run`、`--check-only`、`--measure-only`、
`--names-only`、`--emit-review-template`、`--emit-review-workpack`、`--emit-edit-template`、
`--validate-edit`，以及
`stage6.hook --identity`。表外的 0 成本 flag 一律按会写盘对待。

| 节点 | 0 成本写盘入口 | 需要 agent 自己写的输入 | 约束 |
|---|---|---|---|
| `stage0.edit` | `--apply-edit` · `--apply-edit-plan` · `--apply-candidate-plan` | 正式 edit / edit-plan / candidate-plan 形状 | 不直接覆盖 `clean_text.txt` 或 removal report——三者都只往 revision 链上追加一版，再由链物化成那两份产物。`--apply-candidate-plan` 是机器重跑候选的三选一裁决（`adopt` 采纳整版 / `dismiss` 保留当前版），与 edit-plan 同样带乐观锁（`expected_revision_id`）。⚠ 「采纳后再编辑」不是第三个动作，是 adopt 之后照常走 `--apply-edit-plan` |
| `stage2.global` | `--apply-edit` · `--review-decisions` · `--renormalize` · `--revalidate` | typed 修订（如 `reader_reveal_events` 时线）；`stage2/global_review_decisions.json` | 本层**有**通用 `--apply-edit`（见 [经验库](field-notes.md) FN-007）。先用只读 `--emit-review-template` 取得当前 item hash/evidence；模板默认全 `UNVER`，原样应用不会开闸。`--review-decisions` 按 `actor + 理由 + subject_sha256` 提交 PASS/FAIL/UNVER，subject hash 变更后旧决策被拒收 |
| `stage2.blueprint` | `--renormalize` · `--revalidate` | finding 明确允许时编辑活动 `.INVALID` | 本层确实没有 `--apply-edit`；逐章内容的修正走 `--chapters <N>` 定向重生成（**付费**）或回上游 `stage2.global`。只改证据指向字段，不改生成轮次与来源账 |
| `stage3.style` | 默认生产（无 flag） | 无 | 从当前 Stage1 完整重建机械基线；旧形状 formal/INVALID 走 shape-break 替换且不进入 `_rounds/`。`--measure-only` 与 `--check-only` 都只读 |
| `stage4.plan` | `--apply-edit` · `--identity` · `--revalidate` | `<novel-dir>/stage4/plan_edit.json`；终态明确允许时可修活动 `.INVALID` | `--revalidate` 强审计并重建 writer context；`--identity` 另写 `stage4/writer_context.identity.json` 基线，不覆盖正式计划。仍以追跑 check/state 验证 |
| `stage5.write` | `--apply-edit` · `--recheck` · `--revalidate` · `--rebuild-draft` | **`<base>/_service/edits/` 下**、文件名含 `chNN` 的完整 `ChapterResponse`：`{"segments":[{"slot":"s01","text":"…"},…]}`；或不带编辑文件，用 `--rebuild-draft` 从 canonical 逐章稿重建纯投影 | **0 LLM 调用。** `--apply-edit` 必须提交该章全部 producer-owned slot 的全文；`--rebuild-draft` 只重建 `stage5/draft.md`，不改逐章稿。不直接改正式 chapter JSON、draft.md 或其他章。⚠⚠ **编辑文件落点不要按上面四层类比写进 `<novel-dir>/stage5/`**：那四层的编辑文件登记在 `ARTIFACTS` 里（所以必须在书目录内），本层**刻意没有登记**（`novel/paths.py::edit_rel` 的例外一段），写进书目录会让 `tests/corpus.py::_classify` 关闭式失败 ⇒ **这本书从此当不成语料底座**，而报错文案还会把人引向「加进 ARTIFACTS」那条错路（真发生过）|
| `stage5.review` | `--retain-pretrim` · `--wontfix` | 无人产文件；决定本身就是输入 | `--retain-pretrim --chapters <N> --actor <ACTOR> --reason <text>` 恰好一章，写 hash-bound `trim_resolution_chNN.json`，不改正文、不采用收缩候选（见 FN-014）；`--wontfix` 只把豁免记进 `stage5/wontfix.json`，**一份验收报告都不产** |
| `stage6.hook` | `--apply-edit` · `--recheck` · `--revalidate` · `--wontfix` | `<novel-dir>/stage6/hook_edit.json` | 一次只改目标候选；随后重跑机械与语义审判 |
| `stage7.rename` | `--apply-edit` · `--adopt-residual` · `--identity` · `--recheck` · `--revalidate` | `<novel-dir>/stage7/name_map_edit.json` | 先用 `--emit-edit-template` 取当前注册骨架，改后用只读 `--validate-edit <路径>` 走同一构建/判定链，确认后才 `--apply-edit`。⚠ `--adopt-residual high` **重写 `stage7/name_map.json`、清旧审批、退休现有成书**，`--identity` 也重写同一份 name map——两者都不是“看一眼”，用后强制重新 hash-bound approve、assemble 和逐字对账 |

非节点的第十个写入面是终审包 `<novel-dir>/<书名>_review.md`（落书目录根，不在任何 `stageN/` 下；确定路径由启动 prompt 给出）：只供用户读取，可随跑更新，不得承担产物修复功能。

不得直接修改：正式 JSON/TXT、成书、`_service/*.jsonl`、`.llm_cache/`、`_rounds/` 历史、旧 run 的输出目录。`mark` 是写进度信标的唯一入口。

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

## 结构化响应的受托修复

主跑带 `--response-repair agent` 时，一份结构化响应命中可修闸后不会立刻消耗下一采，而是保留在
书目录 `_llm_rejected/<chain>/<attempt>/` 并以 `ResponseRepairRequired`（exit 2）停下。终态给出的
`candidate.json` 是**唯一允许直接编辑**的文件；`original.txt`、`rejection.json`、`resolution.json`
和 `outcome.json` 都是审计面，不得修改。

只修 `answer_lost`、JSON parse、必需顶层键和 schema 形状问题；其中截断、空无可用材料、调用点
validator 判出的内容/契约错误都不猜。代码会先尝试极窄的 BOM / 容器尾逗号确定性修正；过不了完整
闸才交 agent。修复原则是保留原响应表达，只补 JSON/形状，不发明剧情、事实或判据缺失的内容。

处置顺序：

1. 读取终态点名的 `original.txt`、`rejection.json` 与 `candidate.json`，只编辑后者。
2. 能忠实修复时，完成编辑后提交：
   `<PY> -X utf8 -u -m novel.cli response-repair <SID> --base <BASE> --attempt <ID> --actor <ACTOR> --reason <text>`。
   不能忠实修复时同命令加 `--decline`，不要为了省下一采硬造内容。
3. 每份响应只允许一次 submit/decline；提交后候选被封存，不再改第二遍。
4. 以原节点、原 flags（含 `--response-repair agent`）重跑。候选必须重新经过
   `json.loads → require → model_validate → 调用点 validator`；过闸才采纳，失败或 decline 就恢复
   原顺序继续下一采。完整链固定为
   `Flash 首发 → 修复 → Flash 同档重采 → 修复 → Pro 首发 → 修复 → Pro 同档重采 → 修复 → LlmFailed`。

`response-repair` 是零 LLM 的 CLI sidecar，不是 node，不需要为它另造 preview；原付费 node 仍按
run_step/mark 协议重跑。账本会恢复中断前已经发生的调用与 accepted-attempt 联结，不能删隔离目录、
换参数或清缓存来“重新开始”。

## 付费修复与缓存

付费命令中断、网络失败或 exit 70 后：

1. 先查 `state` 与目标产物；账本写入不证明产物完整。
2. 目标缺失或残缺时，用**完全相同参数**重跑，优先命中 `.llm_cache`。
3. 不为“确保重跑”修改章节范围、模型、思考参数或 prompt；那会改变缓存键并形成新付费调用。
4. 只有 finding 明确要求重新采样时才使用定向刷新入口（`stage2.global --resample` 绕过同参数问题缓存重采整份响应，`stage2.blueprint --chapters <N>` / `stage4.plan --chapters <N>` 只重算目标章）；仍需两项授权并记录原因。
5. 连接错误使用实现内重试阶梯；实现耗尽后不要再叠加无限外层重试。
6. **上游正式产物一旦变更，下游缓存全废。** `--revalidate` 提升、`--apply-edit` 新轮、定向重采样都算变更：以它为输入的下游节点缓存键随之失效，下一次运行是**实付**而不是回放，而且是整层重付、与该层首跑同量级。选择修复层之前先估算下游重算面：同一问题在下游有等价的正式修复入口时优先在下游修；只能在上游修时（如 `present_names_invented` 只有 stage2 有入口），在终审包把这笔重算记成**修复的直接代价**，不要读成重复付费。

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
