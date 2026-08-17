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

先建立三个变量：

- `<PY>`：能导入当前仓库的 `novel_refactor` 环境解释器。优先使用启动服务或当前项目实际使用的 `sys.executable`，不要硬编码到另一套环境。
- `<BASE>`：输出根的绝对路径。
- `<SID>`：从既有目录读取，或由 `stage0.remove --source` 的源文件名尾部数字推导。

逐项确认：

1. cwd 是仓根；`<PY> -X utf8 -u -m novel.cli --help` 可用。
2. `.env` 中存在外部模型密钥，但不输出值；`.llm_cache/` 可用。
3. 新跑使用新的 `<BASE>`；续跑使用原 `<BASE>`。不要写入任何历史验收或证据目录。
4. 源文件或既有书目录存在，`<SID>` 唯一。
5. 付费授权与正文外发授权分别有明确值。两项不是同一授权。
6. 不覆盖项目默认模型与思考配置；运行时实际模型以配置和账本为准。
7. 同一时刻没有另一本文书或同一本书的 API/agent 写盘作业在运行。

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

执行 `preview` 输出的**全部步骤**，不只执行第一条。写盘节点可能自动追加 `--check-only`；`stage2.global --full-extract` 会追加 blueprint 与 style 两条复检。不要另抄追跑规则。

真跑本身已经打印同一份 fresh 报告时，追检的价值是**复核产物完整性**，不是重读判据（那句「⚠ 自动追跑：退出码 0 也可能是残缺产物」说的就是这件事）：仍必须执行并打点，但只取它的退出码与合计行即可，不必重读全文。

对每条实际命令，包括自动追跑的 check 与修复命令：

1. 运行 `mark <SID> --node <node> --phase start --base <BASE>`。
2. 直接运行命令并保存真实子进程退出码。**不要让管道消费退出码**；把 stdout 重定向到文件、取到退出码之后再过滤该文件是允许的，也是长报告的推荐读法（见[结果判定](#结果判定)）。
3. 运行 `mark <SID> --node <node> --phase end --exit <rc> --base <BASE>`。

`mark` 只记录观察轨迹，不证明质量通过。不要给 `preview` 打点。

长节点使用足够长的前台超时或宿主提供的持久 job/cell 句柄，并至少每 60 秒汇报一次进度。不要用极短 shell timeout 模拟 yield；那会终止付费进程。

## 主链顺序

下表中的节点列由 `scripts/validate_skill.py` 与 `PIPELINE_STEPS` 对账。参数列是本 skill 的主跑策略；实际 argv 始终取自 `preview`。

<!-- pipeline-steps:start -->
| # | 节点 | 主跑参数 | 放行证据 |
|---:|---|---|---|
| 1 | `stage0.remove` | 新书：`--source <txt>`，省略 sid | 摄入完成；追跑 check 除待批准项外无 HARD。批准前仅 `s0_approval=pending` 导致 exit 1 是预期状态 |
| 2 | `stage0.approve` | `--actor <id> --reason <text>` | 按代批契约审阅剔除报告后批准，批准状态落盘 |
| 3 | `stage0.edit` | 仅当代批发现剔除错误时用 typed edit/edit-plan；无修改则 `state` 跳过 | 人工修订经同一 Stage0 闸门重判，不直改正式文本 |
| 4 | `stage1.partition` | 无 | 追跑 check 无 HARD；章节 ordinal 唯一且覆盖 `1..N` |
| 5 | `stage2.global` | `--full-extract` | global 正式落盘；执行 preview 列出的后续 blueprint/style 步骤 |
| 6 | `stage2.blueprint` | 由 `--full-extract` preview 列出；中断恢复时只跑缺失项 | 逐章蓝图正式落盘，check 无 HARD |
| 7 | `stage3.style` | 由 `--full-extract` preview 列出；中断恢复时只跑缺失项 | 文风画像正式落盘，check 无 HARD |
| 8 | `stage4.plan` | 无 | 计划 check 无 HARD，写作上下文由当前计划组装 |
| 9 | `stage4.context` | 计划落盘已自动组装时 `state` 跳过；只在需重建时执行 | `stage4/writer_context.json` 与当前计划投影一致且 fresh |
| 10 | `stage5.write` | `--deep 1,2,3 --shallow-rest`；不足三章时截到 `N` | deep+shallow 的正式章集合恰好覆盖计划章集合，内部收缩/重试不等于失败 |
| 11 | `stage5.review` | 无 | 最新机械验收完成；HARD 按 remediation 收敛，info/metric/unver 保持原态 |
| 12 | `stage6.hook` | 无 | 至少一条候选通过落盘闸；追跑 check 可读正式候选 |
| 13 | `stage6.semantic` | `--advisory`（无标定 profile 时的现役主路径） | 每个去重文本都有两名盲评判官的带证据结论与匿名质量排序；结论按完整 SHA-256 与当前候选对齐 |
| 14 | `stage6.approve` | `--pick <N> --actor <id> --reason <text> --subject-sha256 <64hex>` | 所选候选满足代批判据，hash 与当前文本一致，`hook.json.approved` 与最新语义结论一致 |
| 15 | `stage7.rename` | 无 | 人名表正式落盘；追跑 check 的 HARD 清零，info/unver 已人工审阅 |
| 16 | `stage7.approve` | `--actor <id> --reason <text> --subject-sha256 <64hex>` | 映射、漏网称呼与语义标记通过代批判据，hash 与当前决策面一致，批准状态落盘 |
| 17 | `stage7.assemble` | 无 | `stage7/book.txt` 生成并通过独立成书对账 |
<!-- pipeline-steps:end -->

`stage2.global --full-extract` 在中途被闸门拦下时，不机械重跑整组。按最新 `state` 与 finding 修好已落下的部分，再分别 preview 缺失的 `stage2.blueprint`、`stage3.style`；只执行缺失项。

### `stage6.semantic --advisory`：不带这个 flag 等于白丢一层证据

不带 `--advisory` 时，在未配置匹配 evaluator profile 的宿主上必然 exit 2 且**不发起任何模型调用**。
它是注册在案的付费 flag（`contracts/invariants.py::PAID_FLAGS`），用它仍运行双盲判官，
只是结论恒为带证据的 `UNVER`、`automated=false`——**它不能替代标定，但它是"有证据"与"没有证据"的分界**。
本节点不接 `--check-only`。实测量级：2 次调用 / 约 150 秒 / 占单本输出 token 的 1.7%。

### `--deep 1,2,3` 是默认，不是判据

依据是开篇三章决定读者留存，值得更高的单章成本；其余章走 shallow。deep 与 shallow 的单章成本不同，
所以这是一个**有成本含义的默认值**。用户在[接口契约](../SKILL.md#接口契约)的可选约束里指定了章节深浅分配时以用户为准，
并在终审包记录实际分配与依据。不要为「提高质量」自行扩大 deep 集合——那是未经授权的成本变更。

### 书级语义审判（原 `stage5.semantic`）已下线

该节点于 2026-08-17 整节点删除（用户裁决）。**书级剧透防线现在是 0 成本的那一组**：
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

质量判定只认最新报告：

- 只把实际命中的 HARD 当阻塞；`info`、`metric`、`unver`、`skipped` 各自保留语义。
- 报告必须 fresh/match；旧报告即使全绿也不能为新产物背书。
- 记录每条 badge 的分母。分母为零或全部 `unver` 不是绿灯。
- stage6 总报告可以因落选候选 HARD 返回 exit 1；所选候选是否可批准按候选级证据判断，并将总报告残红披露。

check 报告按设计逐条印出判据的设计说明与历史盘面基线（`stage2.blueprint --check-only` 单份实测
**35.9 KB / 71 条判据**，且在 stage2 至少要读三次），其中与本次判定相关的只有命中行。
**先按 `[!!]`（命中）、`[??]`（够不着）、`合计` 与 badge 行过滤，只在需要展开某条命中时读全文。**
过滤只改变读法、不改变任何判定；分母、freshness 与命中数仍以报告原文为准，具体写法见[运行环境](runtime-profiles.md#共同规则)。

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
3. 最终成本只以 `<BASE>/_service/ledger.jsonl` 为权威源：按 sid 过滤 `outcome == "ok"`
   的记录，缓存命中与实付分开计数，token 合计只累加 `cached == false`。产物
   `_provenance.usage`、控制台日志和历史报告只用于单产物追溯，不得与 ledger 混加或替代它。
4. 完成终审包，确保每个异常、残红和不可验证项都有“影响、证据、恢复路径”。
5. 只向用户交付 `book.txt` 与终审包；中间产物仅作为终审包中的可追溯指针。
