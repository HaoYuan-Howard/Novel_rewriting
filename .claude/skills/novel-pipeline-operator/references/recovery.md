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
| `human` | 由受托 agent 使用下表的正式人工输入面；必要时用 `--pick` 或机械结论可达的 `--wontfix` | 默认 0 外部调用；人工编辑不得绕过同一闸门 |
| `none` | 保留 metric/info 读数，不做修复 | 不为“全绿”制造动作 |

`llm` 那一行的「finding 明列的付费入口」在 `stage5.write --revise` 之外还有三条注册在案的定向返工路，
**它们都要再调一次模型**，只在对应 finding 指名时才跑，不作为「提高质量」的自选项：

| flag | 节点 | 它修什么 |
|---|---|---|
| `--purge-role` | `stage2.global` | `role_leaks_fate` 的唯一修复路：把 role/fate 双向重划分一遍 |
| `--repair-surface-habits` | `stage3.style` | 表层习惯的定向返工 |
| `--resample` | `stage2.global` | 全书响应被闸门拦下后，绕过同参数问题缓存重新采样整份响应 |

同一修复臂连续失败达到上限时停止该臂；读新 finding 后切换到不同、证据支持的路由不算重复同一路。不要通过改参数、删缓存或删除 finding 伪装收敛。

## 允许写入面

正式产物必须由 CLI 生成。受托 agent 只可直接创建或编辑下列人工输入；路径、形状和前稿要求以当前终态文案与 `docs/schema/` 为准。

| 层 | 允许的人产输入 | 消费入口 | 约束 |
|---|---|---|---|
| stage0 | 正式 edit/edit-plan 形状 | `stage0.edit --apply-edit ...` 或 `--apply-edit-plan ...` | 不直接覆盖 `clean_text.txt` 或 removal report |
| stage2 global/blueprint | finding 明确允许时编辑活动 `.INVALID` | 对应节点 `--revalidate --actor <id> --reason <text>` | 本层没有通用 `--apply-edit`；只改证据指向字段，不改生成轮次与来源账 |
| stage3 | `<novel-dir>/stage3/style_edit.json`；终态明确允许时可修活动 `.INVALID` | `stage3.style --apply-edit <绝对路径>` 或 `--revalidate --actor <id> --reason <text>` | 决策集只改 schema 开放字段 |
| stage4 | `<novel-dir>/stage4/plan_edit.json`；终态明确允许时可修活动 `.INVALID` | `stage4.plan --apply-edit <绝对路径>` 或 `--revalidate --actor <id> --reason <text>` | `--revalidate` 强审计并重建 writer context；仍以追跑 check/state 验证 |
| stage5 | 文件名含 `chNN` 的完整 `ChapterResponse`：`{"segments":[{"slot":"s01","text":"…"},…]}` | `stage5.write --apply-edit <绝对路径>` | **0 LLM 调用，但必须提交该章全部 producer-owned slot 的全文**；不直接改正式 chapter JSON、draft.md 或其他章 |
| stage5 超限章销账 | 无人产文件；决定本身就是输入 | `stage5.review --retain-pretrim --chapters <N> --actor <id> --reason <text>` | **0 LLM 调用**，恰好一章，写 hash-bound `trim_resolution_chNN.json`，不改正文、不采用收缩候选。见 [经验库](field-notes.md) FN-014 |
| stage6 | `<novel-dir>/stage6/hook_edit.json` | `stage6.hook --apply-edit <绝对路径>` | 一次只改目标候选；随后重跑机械与语义审判 |
| stage7 | `<novel-dir>/stage7/name_map_edit.json` | 先用 `stage7.rename --emit-edit-template` 取当前 v1 骨架，再跑 `stage7.rename --apply-edit <绝对路径>` | 不手抄 schema 版本；映射变更后强制重新 hash-bound approve、assemble 和逐字对账 |
| 终审 | `<base>/final_review_<sid>.md` | 用户读取 | 可随跑更新；不得承担产物修复功能 |

不得直接修改：正式 JSON/TXT、`book.txt`、`_service/*.jsonl`、`.llm_cache/`、`_rounds/` 历史、旧 run 的输出目录。`mark` 是写进度信标的唯一入口。

`--apply-edit` 的相对路径按**书目录**解析，也接受书目录内的绝对路径；目录外路径和 `..`
穿越关闭式拒绝。操作包仍优先记录解析后的绝对路径，便于复核。

### `--revalidate` 与 `--apply-edit` 不是同一条路

- `--revalidate --actor --reason` 是强审计：只提升当前 `.INVALID`，记录审计人、原因、
  raw/canonical hash 及结构化差异，保留原生成轮次和来源账。它不是“人改了一轮”。
- `--apply-edit` 消费明确的 typed 人工输入，开新人工修订轮，并让已绑定旧主体的下游结论/
  approval 按契约过期。它是 0 LLM 费用，不等于没有人工输入或可以跳过重判。

## 付费修复与缓存

付费命令中断、网络失败或 exit 70 后：

1. 先查 `state` 与目标产物；账本写入不证明产物完整。
2. 目标缺失或残缺时，用**完全相同参数**重跑，优先命中 `.llm_cache`。
3. 不为“确保重跑”修改章节集、mode、模型、思考参数或 prompt；那会改变缓存键并形成新付费调用。
4. 只有 finding 明确要求重新采样时才使用定向刷新入口（`stage2.global --resample` 绕过同参数问题缓存重采整份响应，`stage2.blueprint --chapters <N>` / `stage4.plan --chapters <N>` 只重算目标章）；仍需两项授权并记录原因。
5. 连接错误使用实现内重试阶梯；实现耗尽后不要再叠加无限外层重试。
6. **上游正式产物一旦变更，下游缓存全废。** `--revalidate` 提升、`--apply-edit` 新轮、定向重采样都算变更：以它为输入的下游节点缓存键随之失效，下一次运行是**实付**而不是回放（实测一次 stage4 重规划 = 233 秒 / 约 8 万 token）。选择修复层之前先估算下游重算面：同一问题在下游有等价的正式修复入口时优先在下游修；只能在上游修时（如 `present_names_invented` 只有 stage2 有入口），在终审包把这笔重算记成**修复的直接代价**，不要读成重复付费。

stage5 调用次数高于章数可以来自写作重试和长度收缩。最终调用/token 成本的唯一
权威源是 `<BASE>/_service/ledger.jsonl`；产物 usage 和日志不另行混加。账本中的
`cached == true` 为缓存回放，实付为零；reasoning token 为零或空不等于失败。

修改正文后：

- 重新运行 `stage5.review`（0 成本）。书级语义审判已下线，本层没有第二个要跟着重跑的节点。
- 若修改开篇消费范围内章节，检查 `stage6.semantic` 是否因正文 sha1 过期，并按需带 `--advisory` 重跑。
- 不在正文未变时无谓重跑 `stage5.review`。

## 活动终态文件

只把书目录当前层级的 `.INVALID/.rejected/.stalled` 视为活动状态；`_rounds/` 中同名文件是审计历史。

正式稿与活动 `.INVALID` 同时存在时，不自行删除任一文件。读取当前 `state`、报告选择逻辑和最新轮次；用正式修复入口使活动失败归档。若成功重建后活动失败仍遮蔽正式稿，把它作为代码缺陷停止并报告。

`inbox` 八类的读法（顺序与 CLI 首行的计数顺序一致）：

- `invalid`：首次或当前轮被落盘闸拦截。
- `rejected`：修复稿再次被驳回，读取 `_rounds/` 指针但不编辑历史。
- `stalled`：达到自动修复轮次上限，转受托人工编辑。
- `repairable`：本层 HARD，且 finding 已经绑定确定性 `fix` 路由；按该行 `how` 给出的节点/flag 收敛，多为 0 成本。`fix` 不可执行时该行会写明原因，此时按 `upstream` 或人工路处理。
- `human_only`：需要人工语义/审美阅读；逐类裁决，但不一定阻断。
- `verification`：可由人工证据消解的决策项，有三个来源——stage2 全书评测项（按 `actor + 理由 + subject_sha256` 提交 PASS/FAIL/UNVER，subject hash 变更后旧决策会被拒绝）、stage5 超限章 `trim_audit_chNN`（见 [经验库](field-notes.md) FN-014）、stage6 候选级 UNVER（`stage6.approve --pick <N>` 的 hash-bound 人工核验）。它**不阻断成书**，但必须在终审包按类计数披露，不得并进 `human_only` 或算作已解决；后两类都有 0 成本销账入口，应销账而不是留成余额。
- `upstream`：根因位于更早层，回上游处理。
- `approval`：标准人工闸门；按代批契约自行处理。

`unver` 永不伪装进任一已解决类别；在终审包单独披露。通用 check 报告里的 `unver` 结构上不进这张清单——它是判据的边界，不是待办。

## 停止条件

满足任一条件时使用 `stopped`，写清现状与恢复命令后结束：

- 缺少付费授权或正文外发授权，而下一步必须调用外部模型；停止前仍完成只读 `state`、`inbox` 与下一节点 `preview`，不得执行 preview 中的付费步骤。
- 同一节点同一根因在允许的修复路由与轮次上限内仍不能收敛。
- exit 3，或 exit 70 在同一点重复且无法由盘上产物证明结果。
- 需要改变产品语义、公开契约、模型策略或仓库代码才能继续。
- `redo/unknown` 无法通过只读证据确定安全的最小重放面。
- 输入产物疑似被人工直接改写、来源账失真或历史目录被污染。

停止不是失败的伪装。终审包要准确说明已完成范围、阻塞影响、是否产生费用、可复用缓存和下一条安全命令。
