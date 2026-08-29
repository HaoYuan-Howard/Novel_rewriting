# 工作流契约

## 目录

- [事实优先级](#事实优先级)
- [运行前检查](#运行前检查)
- [命令协议](#命令协议)
- [主链顺序](#主链顺序)
- [结果判定](#结果判定)
- [断点续跑](#断点续跑)
- [收尾](#收尾)

## 事实优先级

把本文件视为操作策略，不把它当第二份可执行注册表。发生冲突时：

1. 以 `preview` 输出的 argv、贵贱和追跑步骤为本次执行事实。
2. 以 `contracts.invariants.PIPELINE_STEPS` 决定主节点顺序，以 `NODES` 决定节点/flag 取值面。
3. 以 `novel/cli/exit_codes.py`、最新 check 报告和 finding 原文决定结果与修复入口。
4. 本文件负责选择主跑法和解释证据；发现漂移时停止手抄，并按维护协议更新。

## 运行前检查

先建立五个变量：

- `<PY>`：能导入当前仓库的 `novel_refactor` 环境解释器。优先使用启动服务或当前项目实际使用的 `sys.executable`，不要硬编码到另一套环境。
- `<BASE>`：输出根的绝对路径。
- `<SID>`：从既有目录读取，或由 `stage0.remove --source` 的源文件名尾部数字推导。
- `<STARTED_AT>`：本次会话开始的 epoch 秒。
- `<START_CODE>`：本次会话开始时的完整代码指纹。

逐项确认：

1. cwd 是仓根；`<PY> -X utf8 -u -m novel.cli --help` 可用。
2. headless Commander 已冻结的 commander/executor profile 都能由 capability registry 解析，所选
   CLI 在本机为可探测状态；不要打印账号、token 或认证材料。`.env` 密钥与 `.llm_cache/` 只在
   操作者明确选择 legacy DeepSeek 临时回滚时检查，不能拿缺 DeepSeek 配置阻断 agent 主路。
3. 新跑与续跑使用**同一个** `<BASE>`：隔离靠每本书自己的 `<平台_书名_sid>/` 目录，不靠另建输出根。另建一个根会把同一本书的 `_service/ledger.jsonl` 分裂成两份，而[收尾](#收尾)把它当成本的唯一权威源。不要写入任何历史验收或证据目录。
4. 源文件或既有书目录存在，`<SID>` 唯一。立即从仓根运行
   `<PY> -X utf8 -u .claude/skills/novel-pipeline-operator/scripts/session_preflight.py --base <BASE> --sid <SID>`；新书把
   `--sid` 换成 `--source <TXT>`。从这一个 JSON 固化 `<STARTED_AT>` 与 `<START_CODE>`，
   也以它解析出的 sid/novel_dir 为准。终验不得在结束时现取新代码指纹冒充起点。
5. 付费授权与正文外发授权各自取到值：交互式会话两项**默认 `granted`**，本步是记录不是索要；
   headless 取 launcher prompt 原文。两项不是同一授权，任何一项都不由另一项推定。
6. 不覆盖 launcher started 事件冻结的 commander / executor 模型与思考配置；运行时请求身份以
   resolved profile、session audit 与账本为准，wire 未回显的实际字段保留 unknown，不拿请求值倒填。
7. 同一时刻没有**这本书**的其他 API/agent 写盘作业在运行。⚠ 只看这一本：不同 `sid` 的作业互不阻塞（跨书并行是产品明确要求的能力），把别书也算进来会让本条在一次正常的并行跑里永远不成立。

## 命令协议

所有 CLI 命令使用：

```text
<PY> -X utf8 -u -m novel.cli <subcommand> ... --base <BASE>
```

对每个节点先运行：

```text
<PY> -X utf8 -u -m novel.cli preview <node> <SID> [flags] --base <BASE>
```

`stage0.remove --source <txt>` 摄入新书时省略 `<SID>`。`preview` 不接受 `--yes`；它会通过与真跑共用的 `build_argv`，在付费步骤中生成已经带 `--yes` 的真实命令。它打印的命令**已按单引号规则转义**：含空格的取值（`--reason`、带空格的路径）自带引号，可逐字复制粘贴，不需要也不应该再自行改写 argv。

执行 `preview` 输出的**全部步骤**，不只执行第一条。写盘节点可能自动追加 `--check-only`。
推荐从仓根直接用下面两条调用生产 `build_steps` 并串联信标；不要另抄追跑规则：

```text
<PY> -X utf8 -u .claude/skills/novel-pipeline-operator/scripts/run_step.py --base <BASE> --sid <SID> --expect-code-version <START_CODE> <node> -- [flags]
<PY> -X utf8 -u .claude/skills/novel-pipeline-operator/scripts/run_step.py --base <BASE> --sid <SID> --expect-code-version <START_CODE> --execute <node> -- [flags]
<PY> -X utf8 -u .claude/skills/novel-pipeline-operator/scripts/run_step.py --base <BASE> --sid <SID> --expect-code-version <START_CODE> --run-id <RUN_ID> --execute stage2.blueprint -- [原 flags]
```

runner 控制参数在显式 `--` 之前可位于 node 前后，但以上是规范写法；`--` 之后全部属于
生产节点。新书摄入时省略 runner 的 `--sid`，在 node flags 里传 `--source <TXT>`。
第三行只用于 Stage2 人工暂停后的同身份 resume：`--run-id` 是 runner 控制参数，不是 node flag，
初次主跑省略。runner 在构建 Step 前、每一步执行前后逐字核对 `<START_CODE>`；漂移时不再启动下一步。

headless Commander 不手拼运行期 agent profile 参数：launcher 把 resolved profiles、两项授权、base/sid 与
起始代码指纹冻结在 append-only started 事件中，runner 只从环境取得 `run_id + contract SHA-256`
指针并重读该事件。默认 executor 与逐节点 override 由代码选择，付费 producer Step 才携 profile 与
egress；自动 `--check-only` 等 0 成本 Step 明确不携带。preview、evidence 与 execute 任一不一致都在
spawn 前关闭式拒绝，不能静默落回 DeepSeek。

八个 agent executor 自身已经把完整候选依次送过 JSON、必需键、schema、调用点 validator 与业务
HARD，并把结构化代码反馈交回同一 provider session；因此 agent 主路**不得**追加
`--response-repair agent`。那条 sidecar 只服务显式 DeepSeek 临时回滚：legacy 响应停在
`ResponseRepairRequired` 时，才按[修复状态机](recovery.md#显式-deepseek-回滚的结构化响应修复)处理隔离候选，
并沿用同一 `run_id`。不得让一个 legacy flag 同时冒充 node-agent 的 session 恢复协议。

真跑本身已经打印同一份 fresh 报告时，追检的价值是**复核产物完整性**，不是重读判据（那句「⚠ 自动追跑：退出码 0 也可能是残缺产物」说的就是这件事）：仍必须执行并打点，但只取它的退出码与合计行即可，不必重读全文。

对每条实际命令，包括自动追跑的 check 与修复命令：

1. 若没有父进程 `NOVEL_RUN_ID`，先运行 `mark <SID> --node <node> --phase start --base <BASE>`；
   它会生成并打印 `<RUN_ID>`。已有父身份时沿用它；也可从一开始就在 `mark` 前显式给全局
   `--run-id <RUN_ID>`。
2. 把同一个值显式用于真实命令：`--run-id <RUN_ID> run ...`，并保存真实子进程退出码。
   **不要让管道消费退出码**；把 stdout 重定向到文件、取到退出码之后再过滤该文件是允许的，
   也是长报告的推荐读法（见[结果判定](#结果判定)）。
3. 用同一身份结束打点：`--run-id <RUN_ID> mark <SID> --node <node> --phase end --exit <rc> --base <BASE>`。
   start/end 或真实命令任一换 run id，终审成本就无法精确联结，禁止靠时间补猜。

`mark` 只记录观察轨迹，不证明质量通过。不要给 `preview` 打点。

长节点使用足够长的前台超时或宿主提供的持久 job/cell 句柄，并至少每 60 秒汇报一次进度。不要用极短 shell timeout 模拟 yield；那会终止付费进程。

## 主链顺序

下表中的节点列由 `.claude/skills/novel-pipeline-operator/scripts/validate_skill.py` 与 `PIPELINE_STEPS` 对账。参数列是本 skill 的主跑策略；实际 argv 始终取自 `preview`。

<!-- pipeline-steps:start -->
| # | 节点 | 主跑参数 | 放行证据 |
|---:|---|---|---|
| 1 | `stage0.remove` | 新书：`--source <txt>`，省略 sid | 摄入完成；追跑 check 除待批准项外无 HARD。批准前仅 `s0_approval=pending` 导致 exit 1 是预期状态 |
| 2 | `stage0.approve` | `--expected-revision-id <工作包 revision_id> --actor <ACTOR> --reason <text>` | 按代批契约审阅 Stage0 check 与剔除视图后批准，批准状态写入 revision 链；读包后 revision 漂移会关闭式拒绝。**每本书都要过这一步**——首跑恒 pending，零剔除的书也一样（见下） |
| 3 | `stage0.edit` | 仅当代批发现剔除错误时用 typed edit/edit-plan；无修改则 `state` 跳过 | 人工修订经同一 Stage0 闸门重判，不直改正式文本 |
| 4 | `stage1.partition` | 无 | 追跑 check 无 HARD；章节 ordinal 唯一且覆盖 `1..N` |
| 5 | `stage2.global` | 无；agent executor 原生同 session 修复 | 最新 Global 候选通过 schema 后即物化 current 称名投影；Global HARD 可让候选暂留 `.INVALID`，但只要投影 current/fresh 就不阻塞 blueprint 计算 |
| 6 | `stage2.blueprint` | current/fresh 称名投影可读后独立 preview/run，不等 Global 正式稿 | 逐章蓝图正式落盘；Stage4 前再汇合 current 正式 global、唯一 Stage2 `--check-only` 联合 HARD 无命中，以及 current review 的 blocking 面逐项 PASS |
| 7 | `stage4.design` | 首轮无参数；只有当前正式 A 审阅明确拒绝时，才另行执行服务端给出的 `--repair-decision <SHA>` 或 `--replan` | 只产生当前盲候选；本相未读取原文顺序、证据位置或必须材料，且不会顺带启动下一相。机械通过后还须提交全书策略 + 逐章正式设计审阅 |
| 8 | `stage4.plan` | 仅在当前 A 设计审阅全书 accepted 后重新独立 preview/run | B 只绑定证据；模型的 `binding_concerns` 进入最终人审但不阻断。模型声称 infeasible 只有代码构造性证明后才可正式落盘，否则同 session 返修为完整 bound |
| 9 | `stage4.context` | B 编译计划机械通过、且最终逐章结构裁决全书 accepted 后单独执行 | `stage4/writer_context.json` 与当前计划投影一致且 fresh；B、`--revalidate`、`--apply-edit`、`--apply-binding` 都不得自动组装它 |
| 10 | `stage5.write` | 不带 `--chapters` 即写全部缺少的 canonical 章 | `stage5/chNN.json` 恰好覆盖计划章集合；内部重试不等于整节点失败 |
| 11 | `stage5.review` | 无 | 最新机械验收完成；HARD 按 remediation 收敛，info/metric/unver 保持原态 |
| 12 | `stage6.hook` | 无；完整三候选 replacement 在同 session 内修复 | 至少一条候选通过落盘闸；追跑 check 可读正式候选 |
| 13 | `stage6.semantic` | 无；A/B 独立 session 并发 | 每个去重文本都有两名盲评判官的带证据结论与匿名质量排序；结论按完整 SHA-256 与当前候选对齐 |
| 14 | `stage6.approve` | `--pick <N> --actor <ACTOR> --reason <text> --subject-sha256 <64hex>` | 所选候选满足代批判据，hash 与当前文本一致，`hook.json.approved` 与最新语义结论一致 |
| 15 | `stage7.rename` | 无；完整映射 replacement 在同 session 内修复 | 人名表正式落盘；追跑 check 的 HARD 清零，info/unver 已人工审阅 |
| 16 | `stage7.approve` | `--actor <ACTOR> --reason <text> --subject-sha256 <64hex>` | 映射、漏网称呼与语义标记通过代批判据，hash 与当前决策面一致，批准状态落盘 |
| 17 | `stage7.assemble` | 无 | `stage7/<书名>_rewriting.txt` 生成并通过独立成书对账 |
<!-- pipeline-steps:end -->

Stage4 的两次付费调用不得压成一次授权或一次 runner 执行。先对 `stage4.design` 单独 preview/run；
候选机械通过并正式落盘后，按[代批与终审契约](delegated-review.md#stage4-结构代批)先提交 Stage4A
完整 typed 设计审阅。只有策略与全章 accepted，才能重新取得 `stage4.plan` 的 preview 并单独 run。
若逐章 `request_repair`，只执行服务端给出的 hash-bound `--repair-decision <SHA>`，未变且已接受的章
由 checkpoint 复用；只有策略 `request_replan` 才可整书重做。自然语言 reason 只进审计，不充当模型
自由指令。B 的模型负结论不是正式终态；只有代码产出不可行 witness 时 exit 1 才表示负结论已落盘，
此时不得继续 Context 或 Stage5，也不得替人猜 repair/replan 决定。

计划编译并通过机械检查后，再提交第二份、绑定 Plan 与 B concerns 的完整逐章 typed 最终结构决定。
通用 s4 gate 不是兼容入口；计划或 concerns 变化后旧决定立即过期。只有全部章 `accept`，才可单独
运行 0 成本 `stage4.context`；Context current/fresh 后再生成并提交 Stage5 writing 授权，才可执行
`stage5.write`。

Stage2 有两条可重叠的支路，而不是一条全串行链：最新 Global 候选只要通过当前 schema，并物化出
current/fresh 称名投影，就可立即启动 blueprint；不必等待 Global 正式稿。Global HARD 可以把候选
留在 `.INVALID`，其修复与逐章计算重叠；若后续修订真的改变投影，freshness 会让旧 blueprint 失效。
Global 正式落盘后，
生成并完整阅读 `--emit-review-workpack`，记下顶层 `workpack_sha256`；再用
`--emit-review-template` 取得决策骨架，把模板中全零的 `expected_workpack_sha256` 手工换成刚读过的
hash（不得靠重发模板刷新阅读凭证），同时阅读原文、填写 decisions。这里的“并行”不放宽同书写盘
互斥：真正的 `--review-decisions`、typed edit 与 producer 落盘仍应错峰提交。review 若无需改 global，
提交后以 `stage2.blueprint --check-only` 做全局 + 逐章唯一联合复检；只有 current 正式 global、
current/fresh 投影与 blueprint、联合 HARD 无命中且 review current/**blocking 面逐项 PASS**，才能
preview Stage4A；disclosure 未决项继续披露但不阻断。review 若发现 global 要改，先走 typed edit；
共享人物称名 inventory 改变时 blueprint 会 stale，必须重建，未消费字段变化则按 freshness 结果复用。
提前完成的 review 可以保存，但缺 blueprint 时 Stage2 仍未收口。机械测量不是独立节点：Stage5/6
会从各自已读的 Stage1 文本即时计算，operator
不需要预跑、确认或修复机械缓存。

### 零剔除的书同样要过 stage0 闸门

**自动批准已停产**：`stage0.remove` 首跑恒
`pending`，一段都没剔的书也一样，追跑 check 照常是主链表第 1 行那个 `s0_approval=pending`
红灯，`stage0.approve` 对每本书都适用，**没有例外分支**。停产的理由正是零命中这条路——
纯数字番外恰好产生零 removed、零 warning、`clean == source`，旧的自动批准会让全链唯一那道
人工防线对它失效。

⚠ **不存在「历史自动批」这种状态**：记录它的取值与配套的稳定理由码都已删除。
存量自动批产物停在旧 schema 版本上，而现役权威已收成 `stage0/revisions.json`、
本仓又不迁移旧产物。遇到读不进来的旧 stage0，
处置是**免费重跑 `stage0.remove` 再人工批**，不是找兼容路。

代批责任在剔除分母为零时换方向：没有"被误删的段"可审，改做**反向漏剔**抽查，
见[代批与终审契约](delegated-review.md#stage0-代批)。

### `stage6.semantic`：它的产出是**证据**，不是绿灯

合并决议**恒是带证据的 `UNVER`**——本仓没有把 judge 结论升级成自动 `PASS`/`FAIL` 的装置。
所以别按合并 verdict 筛候选（三条会一模一样）：要读的是 `decisions[].judges` 里两名盲评判官
**各自**的 verdict、reason 与逐字 evidence，以及 `reason` 那一句（它会说明属于四种情形的哪一种，
其中「A/B 同判且各自证据闭合」是信息量最大的一条）。

⚠ 跳过这一层与跑了它是两回事：跳过 ⇒ 没有任何判官看过这三条文案，
`stage6.approve` 只能在零证据下拍板。本节点不接 `--check-only`，也不再有 flag。
它是全链最小的付费面：两名盲判官各一次调用，不随章数增长。

### Stage5 是 canonical 闭卷单臂

主跑命令不带任何深浅参数：不带 `--chapters` 时写全部缺章；只有定向维护 / 重写才带
`--chapters 3,7`。当前 CLI/API 没有选臂或人工深度选择；旧双臂目录只作
`legacy_incompatible` 历史证据，不能挑一臂、搬文件或补版本号冒充 `stage5/chNN.json`。

首次写作前仍须显式授权 current Stage5 全书写作，但授权不再携带章臂分配。若 `state` / `inbox`
报告旧布局，停在可恢复位置：在新隔离 base 由 `stage5.write` 从冻结 Stage0～4 上游全书重建，
不要让 runner 把旧快照解释成新命令。

`source_overlap_high` 现役含义是 canonical 闭卷逐章 20 字窗重合越过注册阈值的 INFO 异常信号，
不是臂身份判断；`overlap_report` 与 `dialogue_report` 都按逐章读。后者没有书级汇总，不能用
`dialogue_fewer` 命中数代替逐章 m/n 分布。

### 书级剧透防线是 0 成本的那一组

全链没有书级语义审判节点，这条防线**不花钱**：
stage2 的 `event_early_name` / `present_early_named` / `present_names_late` 在标注层拦后见之明，
交付前由[代批与终审契约](delegated-review.md)成书逐字对账的「上游 info 下游投影」复核它有没有传导进成书。
遇到**跨章重组**的计划（`from_source_ordinals` 不是恒等映射）时，这条防线的覆盖面会变——
那时停下来找用户裁决，不要自行降低披露口径。

## 结果判定

退出码只回答进程发生了什么：

| exit | 含义 | 动作 |
|---:|---|---|
| 0 | 命令完成 | 继续读取 preview 追加的 check；不能据此宣称质量通过 |
| 1 | 命令完成但 badge 失败 | 读取 HARD、remediation、fix/how 与 freshness，进入修复状态机 |
| 2 | 显式终态或命令形状不合法 | 按原文修正前置条件；不是自动重试信号 |
| 3 | 结构性拒绝 | 视为代码/契约矛盾，停止并保留栈与复现命令 |
| 70 | 未捕获异常，结论未知 | 先查 `state` 与产物完整性；不得假定成功或失败 |

**非零退出后的第一条命令是 `inbox`，不是翻判据注册表。** 它 0 成本，且已经把 finding 的
`remediation` 渲染成**可直接复制的命令**——连贵贱标注和从 finding 里算出来的实参都填好了，形如
「`→ 跑 stage2.blueprint --chapters 3,6`（付费）」「`→ 0 成本人工路：--apply-edit 改内容`」。
`--check-only` 报告负责**读证据与分母**（命中了什么、扫过多少、fresh 不 fresh），
不负责告诉你走哪条路；两者分工不要互换。绕过 `inbox` 去 `contracts.rules` 里反查
`Rule.fix` / `remediation_of` 得到的是同一个答案，但要多花好几个来回，还容易撞上
「那些函数收的是 `Rule` 不是 rule id」这类与本书无关的弯路。

质量判定只认最新报告：

- 只把实际命中的 HARD 当阻塞；`info`、`metric`、`unver`、`skipped` 各自保留语义。
- 报告必须 fresh/match；旧报告即使全绿也不能为新产物背书。
- 记录每条 badge 的分母。分母为零或全部 `unver` 不是绿灯。
- stage6 总报告可以因落选候选 HARD 返回 exit 1；所选候选是否可批准按候选级证据判断，并将总报告残红披露。

check 报告按设计逐条印出**每一条**判据的设计说明与历史盘面基线，不只印命中的那几条，所以单份可达数十 KB，
而 stage2 一层至少要读三次；其中与本次判定相关的只有命中行与状态行。
**先按状态标记（`[!!]` 命中、`[??]` 够不着、`[--]` 没跑成、`[????]` 没人说）、`合计`、`── 来源 ──`、
`⚠` 告警与 badge 行过滤，只在需要展开某条命中时读全文。**
过滤保留全部命中、非 `ok` 状态、来源与告警行，因此不改变任何判定；分母、freshness 与命中数仍以报告原文为准，
具体写法见[运行环境](runtime-profiles.md#共同规则)。**漏掉 `[--]`、`── 来源 ──` 或 `⚠` 中任何一类，
过滤就变成了改判定**——它们分别承载“没跑成”“这份 fresh 不 fresh”“判的这一份是 `.INVALID` 稿”。

## 断点续跑

每次启动或异常后运行：

```text
<PY> -X utf8 -u -m novel.cli state <SID> --base <BASE>
<PY> -X utf8 -u -m novel.cli inbox <SID> --base <BASE>
```

按四态处理：

| 状态 | 处理 |
|---|---|
| `skip` | 产物 fresh/match，跳过 |
| `run` | 无产物，按主链运行 |
| `redo` | 产物 stale；先定位变更来源，再决定最小重放面。任何外部重跑仍受两项授权约束 |
| `unknown` | 读取产物、来源指纹、活动终态文件与日志；无法证明安全路径时以 `stopped` 结束 |

CLI `state` 会显示共享的 deterministic freshness badge；这个 badge 才是“当前消费边是否
仍匹配”的可视化结论。API `NovelOverview` 保持现有公开形状，不为 CLI 展示重复建第二套
状态字段。

从第一个非 `skip` 的主节点接续，但不要把旧 `.INVALID`、旧 review 或保留的 approval 当作当前结论。
相同参数的普通中断重跑优先利用 `.llm_cache`。Stage2 blueprint 还有一层独立的验证后章节检查点：
它只在终态点名的同一 `run_id` 下恢复，命中时会记录 `checkpoint_restored` 且新增 wire 为零；恢复仍会
重跑当前 schema/validator/scope、整书编译与联合 HARD。跨 run、输入/SHA/ledger 漂移或检查点损坏
不是普通 cache miss，应关闭式停止。`--resample` / `--chapters` 表示主动刷新，绝不能带旧 run 身份
去绕过刷新。改变其他参数同样意味着新调用语义，必须重新 preview 和记账。

## 收尾

完成主链后：

1. 执行代批文档中的逐字成书对账。
2. 再运行一次 `state` 与 `inbox`，只统计活动目录，排除 `_rounds/` 历史。
3. 从仓根运行 `.claude/skills/novel-pipeline-operator/scripts/collect_final_evidence.py --base <BASE> --sid <SID> --started-at <STARTED_AT> --start-code-version <START_CODE>`。最终成本只以 `<BASE>/_service/ledger.jsonl` 为权威源：`started_at` 只切本次 beacon/退出序列；脚本从这些 beacon 取得 canonical run id，再按 `run_id + node/role` 精确汇总本次 ledger。节点 executor 使用真实 node，整书 Commander 单列 `role:commander`，不伪装成 NODES 或 `unknown`。无 run id 的历史行只标 `legacy/ungrouped` 并进入同 SID 累计审计，不按时间窗口猜进本轮；本轮与累计两张表不相加。缓存命中与 wire 分开计数，token 合计只累加 `cached == false`；每个非缓存 wire 的 `outcome` 都必须入表，`error` 代表已上线但供应商是否计费未知，单列披露而不冒充确定实付。产物 `_provenance.usage`、控制台日志和历史报告只用于单产物追溯，不得与 ledger 混加或替代它。
4. 对按模板建立的终审包运行同参数的 `.claude/skills/novel-pipeline-operator/scripts/sync_review_evidence.py`，由脚本更新质量表、两张账本、退出序列与机器锚点；agent 只写内容判断、代批证据和恢复说明。
5. 清除剩余占位符，用同一组会话参数运行 `.claude/skills/novel-pipeline-operator/scripts/validate_review.py`，再运行 `.claude/skills/novel-pipeline-operator/scripts/verify_delivery.py --base <BASE> --sid <SID> --start-code-version <START_CODE>`。确保每个异常、残红和不可验证项都有“影响、证据、恢复路径”。
6. 只向用户交付成书与终审包；中间产物仅作为终审包中的可追溯指针。
