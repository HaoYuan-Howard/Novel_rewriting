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
2. `.env` 中存在外部模型密钥，但不输出值；`.llm_cache/` 可用。
3. 新跑与续跑使用**同一个** `<BASE>`：隔离靠每本书自己的 `<平台_书名_sid>/` 目录，不靠另建输出根。另建一个根会把同一本书的 `_service/ledger.jsonl` 分裂成两份，而[收尾](#收尾)把它当成本的唯一权威源。不要写入任何历史验收或证据目录。
4. 源文件或既有书目录存在，`<SID>` 唯一。立即从仓根运行
   `<PY> -X utf8 -u .claude/skills/novel-pipeline-operator/scripts/session_preflight.py --base <BASE> --sid <SID>`；新书把
   `--sid` 换成 `--source <TXT>`。从这一个 JSON 固化 `<STARTED_AT>` 与 `<START_CODE>`，
   也以它解析出的 sid/novel_dir 为准。终验不得在结束时现取新代码指纹冒充起点。
5. 付费授权与正文外发授权各自取到值：交互式会话两项**默认 `granted`**，本步是记录不是索要；
   headless 取 launcher prompt 原文。两项不是同一授权，任何一项都不由另一项推定。
6. 不覆盖项目默认模型与思考配置；运行时实际模型以配置和账本为准。
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
```

runner 控制参数在显式 `--` 之前可位于 node 前后，但以上是规范写法；`--` 之后全部属于
生产节点。新书摄入时省略 runner 的 `--sid`，在 node flags 里传 `--source <TXT>`。
runner 在构建 Step 前、每一步执行前后逐字核对 `<START_CODE>`；漂移时不再启动下一步。

真跑本身已经打印同一份 fresh 报告时，追检的价值是**复核产物完整性**，不是重读判据（那句「⚠ 自动追跑：退出码 0 也可能是残缺产物」说的就是这件事）：仍必须执行并打点，但只取它的退出码与合计行即可，不必重读全文。

对每条实际命令，包括自动追跑的 check 与修复命令：

1. 运行 `mark <SID> --node <node> --phase start --base <BASE>`。
2. 直接运行命令并保存真实子进程退出码。**不要让管道消费退出码**；把 stdout 重定向到文件、取到退出码之后再过滤该文件是允许的，也是长报告的推荐读法（见[结果判定](#结果判定)）。
3. 运行 `mark <SID> --node <node> --phase end --exit <rc> --base <BASE>`。

`mark` 只记录观察轨迹，不证明质量通过。不要给 `preview` 打点。

长节点使用足够长的前台超时或宿主提供的持久 job/cell 句柄，并至少每 60 秒汇报一次进度。不要用极短 shell timeout 模拟 yield；那会终止付费进程。

## 主链顺序

下表中的节点列由 `.claude/skills/novel-pipeline-operator/scripts/validate_skill.py` 与 `PIPELINE_STEPS` 对账。参数列是本 skill 的主跑策略；实际 argv 始终取自 `preview`。

<!-- pipeline-steps:start -->
| # | 节点 | 主跑参数 | 放行证据 |
|---:|---|---|---|
| 1 | `stage0.remove` | 新书：`--source <txt>`，省略 sid | 摄入完成；追跑 check 除待批准项外无 HARD。批准前仅 `s0_approval=pending` 导致 exit 1 是预期状态 |
| 2 | `stage0.approve` | `--actor <ACTOR> --reason <text>` | 按代批契约审阅剔除报告后批准，批准状态落盘。**每本书都要过这一步**——首跑恒 pending，零剔除的书也一样（见下） |
| 3 | `stage0.edit` | 仅当代批发现剔除错误时用 typed edit/edit-plan；无修改则 `state` 跳过 | 人工修订经同一 Stage0 闸门重判，不直改正式文本 |
| 4 | `stage1.partition` | 无 | 追跑 check 无 HARD；章节 ordinal 唯一且覆盖 `1..N` |
| 5 | `stage2.global` | 无 | global 正式落盘；随即生成 review workpack，逐项核验并提交 typed 决策；只有 current 且全部 PASS 才继续 |
| 6 | `stage2.blueprint` | 无；闸开后独立 preview/run | 逐章蓝图正式落盘，check 无 HARD |
| 7 | `stage3.style` | 无；免费机械测量，独立 preview/run | 当前机械基线正式落盘，check 无漂移；s3 机械基线确认绑定当前产物 |
| 8 | `stage4.plan` | 无 | 计划 check 无 HARD，写作上下文由当前计划组装 |
| 9 | `stage4.context` | 计划落盘已自动组装时 `state` 跳过；只在需重建时执行 | `stage4/writer_context.json` 与当前计划投影一致且 fresh |
| 10 | `stage5.write` | 无（不带 `--chapters` 即写全部缺少的 canonical 章） | `stage5/chNN.json` 恰好覆盖计划章集合，`draft.md` 是它们的当前纯投影；内部重试不等于整节点失败 |
| 11 | `stage5.review` | 无 | 最新机械验收完成；HARD 按 remediation 收敛，info/metric/unver 保持原态 |
| 12 | `stage6.hook` | 无 | 至少一条候选通过落盘闸；追跑 check 可读正式候选 |
| 13 | `stage6.semantic` | 无 | 每个去重文本都有两名盲评判官的带证据结论与匿名质量排序；结论按完整 SHA-256 与当前候选对齐 |
| 14 | `stage6.approve` | `--pick <N> --actor <ACTOR> --reason <text> --subject-sha256 <64hex>` | 所选候选满足代批判据，hash 与当前文本一致，`hook.json.approved` 与最新语义结论一致 |
| 15 | `stage7.rename` | 无 | 人名表正式落盘；追跑 check 的 HARD 清零，info/unver 已人工审阅 |
| 16 | `stage7.approve` | `--actor <ACTOR> --reason <text> --subject-sha256 <64hex>` | 映射、漏网称呼与语义标记通过代批判据，hash 与当前决策面一致，批准状态落盘 |
| 17 | `stage7.assemble` | 无 | `stage7/<书名>_rewriting.txt` 生成并通过独立成书对账 |
<!-- pipeline-steps:end -->

Stage2 的顺序硬约束是：global 落盘 → `--emit-review-workpack` 辅助逐项对原文 → `--emit-review-template` 取得版本化决策骨架 → 提交 `--review-decisions` → 确认全部 PASS → 才运行 blueprint。当前 Stage3 只读 Stage1、零 LLM，不受这道 Stage2 人工闸约束；初始抽取实现可让它与 global 并行，operator 按主链表顺序补跑也得到同一份机械结果。

### 零剔除的书同样要过 stage0 闸门

**自动批准已停产**：`stage0.remove` 首跑恒
`pending`，一段都没剔的书也一样，追跑 check 照常是主链表第 1 行那个 `s0_approval=pending`
红灯，`stage0.approve` 对每本书都适用，**没有例外分支**。停产的理由正是零命中这条路——
纯数字番外恰好产生零 removed、零 warning、`clean == source`，旧的自动批准会让全链唯一那道
人工防线对它失效。

⚠ **不存在「历史自动批」这种状态**：记录它的取值与配套的稳定理由码都已删除。
存量自动批产物停在旧 schema 版本上，而 `stage0/removal_report.json` 已经破坏过形状、
本仓又不迁移旧产物 ⇒ 它们连读都读不进来。遇到读不进来的旧 stage0，
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

从第一个非 `skip` 的主节点接续，但不要把旧 `.INVALID`、旧 review 或保留的 approval 当作当前结论。相同参数的中断重跑优先利用 `.llm_cache`；改变参数意味着新调用语义，必须重新 preview 和记账。

## 收尾

完成主链后：

1. 执行代批文档中的逐字成书对账。
2. 再运行一次 `state` 与 `inbox`，只统计活动目录，排除 `_rounds/` 历史。
3. 从仓根运行 `.claude/skills/novel-pipeline-operator/scripts/collect_final_evidence.py --base <BASE> --sid <SID> --started-at <STARTED_AT> --start-code-version <START_CODE>`。最终成本只以 `<BASE>/_service/ledger.jsonl` 为权威源：先按 sid、`outcome == "ok"` 和 `<STARTED_AT>` 切出本次会话；同 SID 全历史另列累计审计，**两张表不相加**。缓存命中与实付分开计数，token 合计只累加 `cached == false`。产物 `_provenance.usage`、控制台日志和历史报告只用于单产物追溯，不得与 ledger 混加或替代它。
4. 对按模板建立的终审包运行同参数的 `.claude/skills/novel-pipeline-operator/scripts/sync_review_evidence.py`，由脚本更新质量表、两张账本、退出序列与机器锚点；agent 只写内容判断、代批证据和恢复说明。
5. 清除剩余占位符，用同一组会话参数运行 `.claude/skills/novel-pipeline-operator/scripts/validate_review.py`，再运行 `.claude/skills/novel-pipeline-operator/scripts/verify_delivery.py --base <BASE> --sid <SID> --start-code-version <START_CODE>`。确保每个异常、残红和不可验证项都有“影响、证据、恢复路径”。
6. 只向用户交付成书与终审包；中间产物仅作为终审包中的可追溯指针。
