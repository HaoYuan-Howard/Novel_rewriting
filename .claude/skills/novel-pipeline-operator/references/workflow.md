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

`stage0.remove --source <txt>` 摄入新书时省略 `<SID>`。`preview` 不接受 `--yes`；它会通过与真跑共用的 `build_argv`，在付费步骤中生成已经带 `--yes` 的真实命令。

执行 `preview` 输出的**全部步骤**，不只执行第一条。写盘节点可能自动追加 `--check-only`；`stage2.global --full-extract` 会追加 blueprint 与 style 两条复检。不要另抄追跑规则。

对每条实际命令，包括自动追跑的 check 与修复命令：

1. 运行 `mark <SID> --node <node> --phase start --base <BASE>`。
2. 直接运行命令，避免通过管道消费输出；保存真实子进程退出码。
3. 运行 `mark <SID> --node <node> --phase end --exit <rc> --base <BASE>`。

`mark` 只记录观察轨迹，不证明质量通过。不要给 `preview` 打点。

长节点使用足够长的前台超时或宿主提供的持久 job/cell 句柄，并至少每 60 秒汇报一次进度。不要用极短 shell timeout 模拟 yield；那会终止付费进程。

## 主链顺序

下表中的节点列由 `scripts/validate_skill.py` 与 `PIPELINE_STEPS` 对账。参数列是本 skill 的主跑策略；实际 argv 始终取自 `preview`。

<!-- pipeline-steps:start -->
| # | 节点 | 主跑参数 | 放行证据 |
|---:|---|---|---|
| 1 | `stage0.remove` | 新书：`--source <txt>`，省略 sid | 摄入完成；追跑 check 除待批准项外无 HARD。批准前仅 `s0_approval=pending` 导致 exit 1 是预期状态 |
| 2 | `stage0.approve` | 无 | 按代批契约审阅剔除报告后批准，批准状态落盘 |
| 3 | `stage1.partition` | 无 | 追跑 check 无 HARD；章节 ordinal 唯一且覆盖 `1..N` |
| 4 | `stage2.global` | `--full-extract` | global 正式落盘，preview 追加的 blueprint/style 两份 check 均无 HARD |
| 5 | `stage4.plan` | 无 | 计划 check 无 HARD，`stage4/writer_context.json` 已按当前计划组装且 fresh |
| 6 | `stage5.write` | `--deep 1,2,3 --shallow-rest`；不足三章时截到 `N` | deep+shallow 的正式章集合恰好覆盖计划章集合，内部收缩/重试不等于失败 |
| 7 | `stage5.review` | 无 | 最新机械验收完成；HARD 按 remediation 收敛，info/metric/unver 保持原态 |
| 8 | `stage5.semantic` | 无 | 语义结论 fresh，按正文 sha1 与最新机械 review 绑定 |
| 9 | `stage6.hook` | 无 | 至少一条候选通过落盘闸；追跑 check 可读正式候选 |
| 10 | `stage6.semantic` | 无 | 每条候选的 grounding/spoiler 结论按 label 与文本 sha1 对齐 |
| 11 | `stage6.approve` | `--pick <N>` | 所选候选满足代批判据，`hook.json.approved` 与最新语义结论一致 |
| 12 | `stage7.rename` | 无 | 人名表正式落盘；追跑 check 的 HARD 清零，info/unver 已人工审阅 |
| 13 | `stage7.approve` | 无 | 映射、漏网称呼与语义标记通过代批判据，批准状态落盘 |
| 14 | `stage7.assemble` | 无 | `stage7/book.txt` 生成并通过独立成书对账 |
<!-- pipeline-steps:end -->

`stage2.global --full-extract` 在中途被闸门拦下时，不机械重跑整组。按最新 `state` 与 finding 修好已落下的部分，再分别 preview 缺失的 `stage2.blueprint`、`stage3.style`；只执行缺失项。

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

从第一个非 `skip` 的主节点接续，但不要把旧 `.INVALID`、旧 review 或保留的 approval 当作当前结论。相同参数的中断重跑优先利用 `.llm_cache`；改变参数意味着新调用语义，必须重新 preview 和记账。

## 收尾

完成主链后：

1. 执行代批文档中的逐字成书对账。
2. 再运行一次 `state` 与 `inbox`，只统计活动目录，排除 `_rounds/` 历史。
3. 按 sid 过滤 `<BASE>/_service/ledger.jsonl` 中 `outcome == "ok"` 的记录；缓存命中与实付分开计数，token 合计只累加 `cached == false`。
4. 完成终审包，确保每个异常、残红和不可验证项都有“影响、证据、恢复路径”。
5. 只向用户交付 `book.txt` 与终审包；中间产物仅作为终审包中的可追溯指针。
