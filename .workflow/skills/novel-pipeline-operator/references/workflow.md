# 工作流契约

## 目录

- [事实优先级](#事实优先级)
- [运行前检查](#运行前检查)
- [运行共同纪律](#运行共同纪律)
- [命令协议](#命令协议)
- [主链顺序](#主链顺序)
- [结果判定](#结果判定)
- [断点续跑](#断点续跑)
- [收尾](#收尾)

## 事实优先级

把本文件视为操作策略，不把它当第二份可执行注册表。发生冲突时：

1. 对已落盘 sid，以 `operator next --json` 的 `state/action` 决定当前下一动作；skill 不自行推演下一 node。
2. 以 `preview` 输出的 argv、贵贱和追跑步骤为本次执行事实。
3. 以 `contracts.invariants.PIPELINE_STEPS` 与 `NODES` 解释 node/flag 取值面，不用它们在 skill 里重写调度器。
4. 以 `novel/cli/exit_codes.py`、最新 check 报告和 finding 原文决定结果与修复入口。
5. 本文件负责解释执行协议和证据；发现漂移时停止手抄，并按维护协议更新。

## 运行前检查

先建立五个变量：

- `<PY>`：能导入当前仓库的 `novel_refactor` 环境解释器。优先使用启动服务或当前项目实际使用的 `sys.executable`，不要硬编码到另一套环境。
- `<BASE>`：输出根的绝对路径。
- `<SID>`：从既有目录读取，或由 `stage0.remove --source` 的源文件名尾部数字推导。
- `<STARTED_AT>`：本次会话开始的 epoch 秒。
- `<START_CODE>`：当前 execution epoch 的完整代码指纹；parent run 可以跨多个 epoch。

逐项确认：

1. cwd 是仓根；`<PY> -X utf8 -u -m novel.cli --help` 可用。
2. 已按[执行模式](execution-modes.md)确认本次 executor 的来源与对应零模型预检；只检查当前选择
   真正需要的 executable、协议或 transport，不用另一模式的配置制造假阻塞。不要打印账号、token 或认证材料。
3. 新跑与续跑使用**同一个** `<BASE>`：隔离靠每本书自己的 `<平台_书名_sid>/` 目录，不靠另建输出根。另建一个根会把同一本书的 `_service/ledger.jsonl` 分裂成两份，而[收尾](#收尾)把它当成本的唯一权威源。不要写入任何历史验收或证据目录。
4. 源文件或既有书目录存在，`<SID>` 唯一。直接交互式运行立即从仓根运行
   `<PY> -X utf8 -u .workflow/skills/novel-pipeline-operator/scripts/session_preflight.py --base <BASE> --sid <SID>`；新书把
   `--sid` 换成 `--source <TXT>`。从这一个 JSON 固化 `<STARTED_AT>` 与 `<START_CODE>`，
   也以它解析出的 sid/novel_dir 为准。headless 正式路由由 RunSupervisor 打开 parent run 与当前
   execution epoch；两种宿主都不得在结束时现取新代码指纹冒充旧 epoch。
5. 付费授权与正文外发授权各自取到值：交互式会话两项**默认 `granted`**，本步是记录不是索要；
   headless 取 launcher prompt 原文。两项不是同一授权，任何一项都不由另一项推定。
6. 不覆盖 launcher started 事件冻结的 commander / executor 模型与思考配置；运行时请求身份以
   resolved profile、session audit 与账本为准，wire 未回显的实际字段保留 unknown，不拿请求值倒填。
7. 同一时刻没有**这本书**的其他 API/agent 写盘作业在运行。⚠ 只看这一本：不同 `sid` 的作业互不阻塞（跨书并行是产品明确要求的能力），把别书也算进来会让本条在一次正常的并行跑里永远不成立。

## 运行共同纪律

- cwd 保持仓根；`--base` 永远使用绝对路径。交互宿主的 shell 可能共享并改变工作目录，
  每次调用都显式指定仓根；headless launcher 已把子进程 cwd 钉在仓根，不要在命令前拼 `cd`。
  不得 `cd` 进产物目录再运行 `-m novel.cli`。
- 所有正式命令和临时诊断都使用当前项目实际解释器 `<PY>`，包括读 JSON 与 import
  `contracts.*` / `novel.*` 的一次性脚本。裸 `python` 指向别的环境时出现的 pydantic 缺失，
  不是仓库故障。
- 直接取得子进程退出码，不在真跑后接会覆盖退出码的管道。文件重定向不改变退出码；先保存 rc，
  再过滤长报告。
- node 的 start/end 信标只由 `run_step.py --execute` 在同一 active claim 内生成；phase、ledger 联结与
  异常由 RunSupervisor 自动记录，不要手工补 `mark` 或 telemetry。
  一次只运行一个写盘节点；一个 operator 会话只管一本书，别书的独立会话可以并行。
- 长命令使用持久句柄或足够长的前台超时，每 60 秒以内汇报一次盘上/进程事实；不要用短 timeout
  杀进程来模拟更新。任何中断后重新调用 `operator next`，不从聊天记忆猜落盘位置。
- 会话先由 `scripts/session_preflight.py` 固化 `<STARTED_AT>` 与 `<START_CODE>`。已有 sid 的 ready node
  actions（含纯读诊断）直接复用同一次 `operator next` 返回的完整 execution token；blocked recovery
  才为 exact node/flags 另取 `operator permit`。两者都走
  `scripts/run_step.py --expect-code-version <START_CODE> --operator-permit-file <NEXT_FILE>`；只有非 node 的只读/typed approval façade 与
  `response-repair` 可直走，Commander 下的写 façade 会自行取得并释放 generic claim，且也在动作前复算并逐字比较。
  `unknown == unknown` 不证明同版，结束时现取当前值也不证明起点。

PowerShell 中可在取得真实 rc 后这样收窄 `--check-only` 报告；该正则保留全部命中、非 `ok` 状态、
来源、分母与告警：

```powershell
$nextFile = Join-Path $SP 'operator-next.json'
& $PY -X utf8 -u -m novel.cli operator next $SID --base $B --json > $nextFile
& $PY -X utf8 -u .workflow/skills/novel-pipeline-operator/scripts/run_step.py `
  --base $B --sid $SID --expect-code-version $START_CODE `
  --operator-permit-file $nextFile --execute <node> -- --check-only > "$SP\out.txt"
$rc = $LASTEXITCODE
Get-Content "$SP\out.txt" -Encoding UTF8 |
  Select-String -Pattern '^\s*\[!!\]|^\s*\[\?\?\]|^\s*\[--\]|^\s*\[\?{4}\]|^合计|^──|^\s*⚠|→\s+(PASS|AMBER|FAIL)|revalidated'
```

这条正则只用于 `--check-only` 长报告；`run` 的一两行摘要直接整读。各段分别接住：

| 模式 | 接住的事实 | 少了它会怎样 |
|---|---|---|
| `\[!!\]` `\[\?\?\]` `\[--\]` | 命中 / 够不着 / 没跑成 | 把 skipped 洗成 ok |
| `\[\?{4}\]` | 未记录的记账键 | 把“没人说”读成“没有” |
| `^──` | freshness、判据与记账分母 | 绿灯失去来源和分母 |
| `^\s*⚠` | `.INVALID` 稿与幽灵边告警 | 把无效主体当正式结论 |
| `^合计`、badge、`revalidated` | 四态合计与强审计标记 | 丢失最终判定 |

`inbox` 很长时仍须保留首行八类计数，再按类收窄明细：

```powershell
& $PY -X utf8 -u -m novel.cli inbox $SID --base $B |
  Select-String -Pattern '^── 待人工|^\s*\[(invalid|rejected|stalled|repairable|human_only|upstream|approval)\]' -Context 0,2
```

`verification` 可以只保留类计数，但计数不能省；真正销账时再读取逐项明细。

## 命令协议

所有 CLI 命令使用：

```text
<PY> -X utf8 -u -m novel.cli <subcommand> ... --base <BASE>
```

sid 已落盘后，每轮的第一条路由命令固定为：

```text
<PY> -X utf8 -u -m novel.cli operator next <SID> --base <BASE> --json
```

它是 0 成本纯读，并在同一份 operator snapshot 上返回 `state`、当前盘面摘要与可选 `action`。
stdout 原样保存为 scratch 内的 `<NEXT_FILE>`。`state=ready` 时同时携带完整 opaque
`execution_token`；调用方只读取 action 的路由字段，token 不拆开、不手搬 SHA，整份 `<NEXT_FILE>`
直接交 runner。`state=blocked` 时 `action` 仍只描述 blocker，按 `waiting_on` 进入对应审批或生产登记的
恢复路，不能把 blocker 的 action hash 当恢复授权。其中 `recovery:stage4-design-action` 表示新的
hash-bound typed A 决定已经落盘：headless RunSupervisor 会按 action 的 exact node/flags 现场铸
recovery permit 并直接交 Executor，不再启动 Commander；普通交互式运行才照下文显式取 permit、
preview 与确认。只有 `state=pipeline_complete` 时 `action=null` 且
无 token，此时进入收尾；任何状态都不自造下一节点。
新书在 sid 尚未落盘前只走现有 `stage0.remove --source <TXT>` 摄入；取得 sid 后立即进入上述 next 循环。
每次写盘、typed approval、人工修订或恢复动作后都重新调用 next，不从下表、目录存在性或聊天记忆
自行推演后继。

ready action 的完整许可已经由上述同一次 snapshot 铸好，不再调用第二次 planner。只有 blocked 当前
blocker 对应的已登记 node 恢复，才显式铸造执行许可：

```text
<PY> -X utf8 -u -m novel.cli operator permit <SID> <node> --base <BASE> --json -- [flags]
```

许可纯读并绑定 current next、代码、active surface、exact node/flags、贵贱/写盘效应以及登记的外部输入原始
字节；recovery 只接受生产闭集。把 stdout 的完整 ExecutionPermit JSON
原样保存为本轮 `<PERMIT_FILE>`；它只允许位于系统临时目录或
`_service/scratch/<sid>/<当前 session>/`，execute 结束即删除，证据文件随 session TTL 清理，
不得再污染仓根或书目录。任一拒绝都不得改 flags 猜路。
随后可用普通 `preview` 阅读生产 argv：

```text
<PY> -X utf8 -u -m novel.cli preview <node> <SID> [flags] --base <BASE>
```

`stage0.remove --source <txt>` 摄入新书时省略 `<SID>`。`preview` 不接受 `--yes`；它会通过与真跑共用的 `build_argv`，在付费步骤中生成已经带 `--yes` 的真实命令。它打印的命令**已按单引号规则转义**：含空格的取值（`--reason`、带空格的路径）自带引号，可逐字复制粘贴，不需要也不应该再自行改写 argv。

执行 `preview` 输出的**全部步骤**，不只执行第一条。写盘节点可能自动追加 `--check-only`。
已有 sid 的所有 node actions（包括纯读诊断与恢复）从仓根用下面两条调用生产 `build_steps` 并串联信标；不要另抄追跑规则：

```text
<PY> -X utf8 -u .workflow/skills/novel-pipeline-operator/scripts/run_step.py --base <BASE> --sid <SID> --expect-code-version <START_CODE> --operator-permit-file <NEXT_FILE> <node> -- [flags]
<PY> -X utf8 -u .workflow/skills/novel-pipeline-operator/scripts/run_step.py --base <BASE> --sid <SID> --expect-code-version <START_CODE> --operator-permit-file <NEXT_FILE> --execute <node> -- [flags]
<PY> -X utf8 -u .workflow/skills/novel-pipeline-operator/scripts/run_step.py --base <BASE> --sid <SID> --expect-code-version <START_CODE> --operator-permit-file <PERMIT_FILE> --run-id <RUN_ID> --execute stage2.blueprint -- [原 flags]
```

runner 控制参数在显式 `--` 之前可位于 node 前后，但以上是规范写法；`--` 之后全部属于
生产节点。新书摄入时省略 runner 的 `--sid`，在 node flags 里传 `--source <TXT>`。
第三行只用于 Stage2 人工暂停后的同身份 resume：`--run-id` 是 runner 控制参数，不是 node flag，
初次主跑省略。runner 在构建 Step 前、每一步执行前后逐字核对 `<START_CODE>`；漂移时不再启动下一步。
ready 的 preview 与 execute 复用同一份原样 next wrapper；blocked recovery 则复用显式 permit 文件。
runner 从两者提取同一种完整 token：preview 只解析一次并冻结渲染，不重新运行 planner；execute
在取得 claim 后、首个副作用前对 active surface 做一次 CAS。SHA-only 兼容参数会在 preview/execute
重新运行 planner，只保留给诊断旧脚本，不得写进默认主路。headless 时一个 permit/claim 覆盖
`build_steps` 的 root 与自动 follow-check：每个 child 收到完整 envelope + 零基 `step_index`，root 在副作用
前完成唯一 CAS，root 写盘后的 follow 只按同一 envelope 重建并核其 index/argv，不能因 surface 预期变化被误杀。

当前付费节点怎样绑定 Agent driver 或 legacy LLM transport，只按[执行模式](execution-modes.md)
中与本次冻结选择相符的一节；不要把 executor 参数或恢复协议混进 node/flag 路由。

真跑本身已经打印同一份 fresh 报告时，追检的价值是**复核产物完整性**，不是重读判据（那句「⚠ 自动追跑：退出码 0 也可能是残缺产物」说的就是这件事）：仍必须执行并打点，但只取它的退出码与合计行即可，不必重读全文。

每条实际 Step（包括自动 follow-check）的 `run_id`、独立 `execution_id`、start/end 与异常 end 都由
`run_step.py --execute` 在同一 active claim 内生成并闭合；operator 不直接调用 `mark`，也不在
runner 外补信标。preview 不写信标。信标只记录观察轨迹、不证明质量通过；任何 start/end 身份漂移
都会令 admission drain 关闭式停止，不能靠时间窗口或手工补行修复。

长节点使用足够长的前台超时或宿主提供的持久 job/cell 句柄，并至少每 60 秒汇报一次进度。不要用极短 shell timeout 模拟 yield；那会终止付费进程。

## 主链顺序

下表中的节点列由 `.workflow/skills/novel-pipeline-operator/scripts/validate_skill.py` 与 `PIPELINE_STEPS` 对账，
用于理解全链和复核覆盖面，不作为下一节点选择器。当前动作只取 `operator next`，实际 argv 始终取自 `preview`。

<!-- pipeline-steps:start -->
| # | 节点 | 主跑参数 | 放行证据 |
|---:|---|---|---|
| 1 | `stage0.remove` | 新书：`--source <txt>`，省略 sid | 摄入完成；追跑 check 除待批准项外无 HARD。批准前仅 `s0_approval=pending` 导致 exit 1 是预期状态 |
| 2 | `stage0.approve` | `--expected-revision-id <工作包 revision_id> --actor <ACTOR> --reason <text>` | 按代批契约审阅 Stage0 check 与剔除视图后批准，批准状态写入 revision 链；读包后 revision 漂移会关闭式拒绝。**每本书都要过这一步**——首跑恒 pending，零剔除的书也一样（见下） |
| 3 | `stage0.edit` | 仅当代批发现剔除错误时用 typed edit/edit-plan；无修改则 `state` 跳过 | 人工修订经同一 Stage0 闸门重判，不直改正式文本 |
| 4 | `stage1.partition` | 无 | 追跑 check 无 HARD；章节 ordinal 唯一且覆盖 `1..N` |
| 5 | `stage2.global` | 无；agent executor 原生同 session 修复 | 最新 Global 候选通过 schema 后即物化 current 称名投影；Global HARD 可让候选暂留 `.INVALID`，但只要投影 current/fresh 就不阻塞 blueprint 计算 |
| 6 | `stage2.blueprint` | current/fresh 称名投影可读后独立 preview/run，不等 Global 正式稿；新关系只经 `--emit-edit-template` → `--apply-edit` 完整集合替换 | 逐章蓝图正式落盘；Stage4 前再汇合 current 正式 global、唯一 Stage2 `--check-only` 联合 HARD 无命中，以及 current review 的 generation-safe blocking 面 |
| 7 | `stage4.design` | 首轮无参数；当前正式 A 审阅拒绝时只执行服务端给出的 `--review-token <opaque>`；单章模型 repair 耗尽可 `--apply-edit` 完整章，显式策略驳回则同时带 `--replan` | 只产生当前盲候选；本相未读取原文顺序、证据位置或必须材料，且不会顺带启动下一相。机械通过后还须提交全书策略 + 逐章正式设计审阅 |
| 8 | `stage4.plan` | 仅在当前 A 设计审阅全书 accepted 后重新独立 preview/run | B 只绑定证据；模型的 `binding_concerns` 进入最终人审但不阻断。模型声称 infeasible 只有代码构造性证明后才可正式落盘，否则同 session 返修为完整 bound |
| 9 | `stage4.context` | B 编译计划机械通过、且最终逐章结构裁决全书 accepted 后单独执行 | `stage4/writer_context.json` 与当前计划投影一致且 fresh；B、`--revalidate`、`--apply-edit`、`--apply-binding` 都不得自动组装它 |
| 10 | `stage5.write` | 不带 `--chapters` 即写全部缺少的 canonical 章 | `stage5/chNN.json` 恰好覆盖计划章集合；内部重试不等于整节点失败 |
| 11 | `stage5.review` | 无 | 最新机械验收完成；HARD 按 remediation 收敛，info/metric/unver 保持原态；所有 edit/trim/wontfix 收口后另提交 current Stage5 typed closure，才可进入 Stage6 |
| 12 | `stage6.hook` | 无；完整三候选 replacement 在同 session 内修复 | 至少一条候选通过落盘闸；追跑 check 可读正式候选 |
| 13 | `stage6.semantic` | 无；A/B 独立 session 并发 | 每个去重文本都有两名盲评判官的带证据结论与匿名质量排序；结论按完整 SHA-256 与当前候选对齐 |
| 14 | `stage6.approve` | `--pick <N> --actor <ACTOR> --reason <text> --review-token <opaque>` | 先完整读取 current Stage6 workpack；所选候选满足代批判据，opaque token 来自同一不可变包，`hook.json.approved` 与最新语义结论一致；`already_current` 时不重复提交 |
| 15 | `stage7.rename` | 无；完整映射 replacement 在同 session 内修复 | 人名表正式落盘；追跑 check 的 HARD 清零，info/unver 已人工审阅 |
| 16 | `stage7.approve` | `--actor <ACTOR> --reason <text> --review-token <opaque>` | 先完整读取 current Stage7 workpack；映射、别名、替换命中、漏网上下文与语义标记通过代批判据，opaque token 来自同一不可变包，批准状态落盘；`already_current` 时不重复提交 |
| 17 | `stage7.assemble` | 无 | `stage7/<书名>_rewriting.txt` 生成并通过独立成书对账 |
<!-- pipeline-steps:end -->

Stage4 的两次付费调用不得压成一次授权或一次 runner 执行。先对 `stage4.design` 单独 preview/run；
候选机械通过并正式落盘后，按[代批与终审契约](delegated-review.md#stage4-结构代批)先提交 Stage4A
完整 typed 设计审阅；裁决前先走只读 `approval workpack design`，按 current subject 阅读异常与全章
coverage。只有策略与全章 accepted，才能重新取得 `stage4.plan` 的 preview 并单独 run。
若逐章 `request_repair`，只执行服务端给出的 `--review-token <opaque>`，未变且已接受的章
由 checkpoint 复用；每轮 repair 必须携带当前问题和全部尚未关闭的历史 typed constraints，关闭状态也要
显式保留。同策略单章两轮模型 repair 耗尽时，允许为该章打开新的 repair epoch，或用
`stage4.design --apply-edit <stage4/blind_chapter_edit.json>` 预览并提交一份完整章响应；后者 0 wire，
仍走相同 compiler、schema、dynamic validator 与全书 HARD，且非目标章业务字节必须恒等。**单章耗尽
不得自动升级成整书 replan**；只有策略决定本身显式 `request_replan` 才可整书重做，并且要携带累计
策略/章节失败反馈，复用 effective input 未变的 accepted 章。自然语言 reason 只进审计，不充当模型
自由指令。B 的模型负结论不是正式终态；只有代码产出不可行 witness 时 exit 1 才表示负结论已落盘，
此时不得继续 Context 或 Stage5，也不得替人猜 repair/replan 决定。

计划编译并通过机械检查后，先走只读 `approval workpack structure`，再提交第二份、绑定 Plan 与 B
concerns 的完整逐章 typed 最终结构决定。工作包里 INFO/metric/UNVER 的 action 为空，只披露不返工。
通用 s4 gate 不是兼容入口；计划或 concerns 变化后旧决定立即过期。只有全部章 `accept`，才可单独
运行 0 成本 `stage4.context`；Context current/fresh 后再生成并提交 Stage5 writing 授权，才可执行
`stage5.write`。若任一章为 `request_replan`，这份 B 决定本身仍只落账、不启动作业；下一次 operator
必须发布不同的 `approval:stage4-structure-replan`。Commander 交叉读取该 B 决定/证据与当前 A 工作包，
形成一份新的 current typed A `request_repair / request_replan`，不得再次审同一份 B，也不得全量
accept 来绕开 B 的拒绝。新 A 决定落盘后，确定性恢复按 opaque token 只交 Executor 精确执行。

Stage2 有两条可重叠的支路，而不是一条全串行链：最新 Global 候选只要通过当前 schema，并物化出
current/fresh 称名投影，就可立即启动 blueprint；不必等待 Global 正式稿。Global HARD 可以把候选
留在 `.INVALID`，其修复与逐章计算重叠；若后续修订真的改变投影，freshness 会让旧 blueprint 失效。
Global 正式落盘后，
生成并完整阅读 `--emit-review-workpack`，记下顶层 `workpack_sha256`；再用
`--emit-review-template` 取得决策骨架，把模板中全零的 `expected_workpack_sha256` 手工换成刚读过的
hash（不得靠重发模板刷新阅读凭证），同时阅读原文、填写 decisions。这里的“并行”不放宽同书写盘
互斥：真正的 `--review-decisions`、typed edit 与 producer 落盘仍应错峰提交。review 若无需改 global，
提交后以 `stage2.blueprint --check-only` 做全局 + 逐章唯一联合复检；只有 current 正式 global、
current/fresh 投影与 blueprint、联合 HARD 无命中且 review current/**blocking 面 generation-safe**，才能
preview Stage4A；disclosure 未决项继续披露但不阻断。review 若发现 FAIL 或未隔离 UNVER，后续最多
两席 Commander 订正轮只接收仍未闭合或依赖变化重开的 subject cards；PASS/已隔离项只留摘要，不再
重发。blocking 面安全后，第四席只读终审读全量 blocking 行、整书正文与 Blueprint 工作包，confirm 才
经 `approval close-stage2` 收口 `seen.read`/`seen.global`/`s2`，reopen 则以终审者为 actor 把指定项改回
FAIL/UNVER 并回到订正席（若仍有席位）。每轮使用 fresh logical/provider session，并从生产模板取得 hash-bound typed 骨架；集合成员新增、
删除、拆分只填 `collection_edits.operations`，经 `--apply-edit` 同时提交事实修改与
`post_edit_review` 自审。producer 对本 edit 新打开项使用默认证明，仍保持同一 hash 的旧 FAIL/UNVER
必须列入 `review_scope` 内的 `decisions`；可删的 secret/relation/world_rule 完整组可显式
`quarantine_group`，但状态仍是 UNVER，写作视图删除该组且
最终交付只能 `ready_with_disclosures`。人物、setting、arc、集合完整性不能隔离。
若这份 reviewed edit 通过 schema 却被 canonical HARD 拦成当前 `.INVALID`，队列先暴露
`recovery:stage2-global-reviewed-edit`：最终 correction workpack 绑定失败 edit/INVALID 原始字节，只反馈
具体 HARD，不重新载入 Blueprint 或整份无关材料；下一轮另开 fresh Commander session，持久 ledger
在调用前记 `started`，重启不返还席位。修正通过后才重新计算 Blueprint freshness。
共享人物称名 inventory 改变时 blueprint 会 stale，必须重建，未消费字段变化则按 freshness 结果复用。
提前完成的 review 可以保存，但缺 blueprint 时 Stage2 仍未收口。机械测量不是独立节点：Stage5/6
会从各自已读的 Stage1 文本即时计算，operator
不需要预跑、确认或修复机械缓存。

新 clean base 的 Blueprint 关系契约使用 `narrative_relations`。producer 可提出关系，但 high-impact/HARD
关系须经 Stage2 typed review 才 binding；含糊关系只进入 Commander workpack。需要人工订正时先运行
`stage2.blueprint --emit-edit-template`，在当前 `review_token` 下只填写目标章的**完整关系集合**，再用
`stage2.blueprint --apply-edit <stage2/blueprint_relation_edit.json>` 预览、执行。它是 0 模型入口，所有
端点闭引用、无向规范排序、时序 DAG、观察者与全书 HARD 仍关闭式校验；不能拿字段 patch、手补旧版
Blueprint 或“同一 source beat”自动推断 `same_scene`。旧 Blueprint 不自动迁移到新关系契约。

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

定稿前运行只读 `approval workpack stage6 <SID> --base <BASE>`。包内按候选展开机械结论、
两名判官各自的 verdict/reason/evidence 与 A/B 原始 ranking；不得回到分散原始文件中只挑一部分
证据。提交批准时必须同时复制同一份包里的候选 `subject_sha256` 与顶层 `workpack_sha256`。
包已给出 `disposition=already_current` 时，当前 typed 批准仍绑定相同 review subject 与显式 review
contract，跳过重复批准；代码/机械规则变化只滚动 validation seal 与 execution epoch，免费复检通过且
可见内容/finding 未变时 raw 决定自动恢复。真正主体漂移才令 disposition 回到 `review_required`。

### Stage5 是 canonical 闭卷单臂

主跑命令不带任何深浅参数：不带 `--chapters` 时写全部缺章；只有定向维护 / 重写才带
`--chapters 3,7`。当前 CLI/API 没有选臂或人工深度选择；旧双臂目录只作
`legacy_incompatible` 历史证据，不能挑一臂、搬文件或补版本号冒充 `stage5/chNN.json`。

首次写作前仍须显式授权 current Stage5 全书写作，但授权不再携带章臂分配。若 `state` / `inbox`
报告旧布局，停在可恢复位置：在新隔离 base 由 `stage5.write` 从冻结 Stage0～4 上游全书重建，
不要让 runner 把旧快照解释成新命令。

写作授权只允许开始写，不证明成稿已闭合。全部 canonical 章完成后运行最终 `stage5.review`，按
remediation 收口 HARD，并完成所有 edit、trim resolution 与 wontfix；随后先用 `approval show` 读取
current closure workpack，再以 `approval template stage5` 取得逐项关闭式模板。必须真实核对
must-appear 与 fact/unver 分组，填 actor/reason，清除每项 placeholder/token 后，才可
`approval apply stage5`。只有 current `closure.state=closed` / `gate.s5=true` 才能进入 Stage6/7 或
assemble；旧 s5、写作前授权、机械 exit 0 与 Commander 身份都不能替代这张写后回执。

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
具体写法见[运行共同纪律](#运行共同纪律)。**漏掉 `[--]`、`── 来源 ──` 或 `⚠` 中任何一类，
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

正式 headless run 的 `pause` 只停止接纳新节点并等待当前 claim/beacon 安全闭合，不取消正在正常执行的
付费节点；普通 reviewer 在同一 epoch resume 精确续用 Commander provider session。Stage2 global
固定为一席审阅、至多两席订正、一席只读终审，每个主体最多四席真实 Commander 且每席开 fresh session；
0-wire typed 动作不占轮次。Executor 的候选→反馈
修复继续复用各节点 logical/provider session 与 checkpoint；Stage2 人工 GlobalEdit
是 0-wire 确定性动作，不另起一个模型 Executor。代码或 skill 身份变化时，
RunSupervisor 在同一 parent run 内关闭旧 epoch、打开新 epoch、先免费复检并创建新的 Commander
provider session；旧 provider context 与 execution token 均不得复用，但盘上 Stage2 typed ledger 的
已用轮次与 progress fingerprint 不重置。`stop` 是永久终止：取消该 run
拥有的 Commander/Executor 进程树、drain 后写 ended，不能 resume。服务重启后无法证明进程状态时保持
`unknown`，不得自动洗成 paused 或自行恢复。

## 收尾

完成主链后：

1. 执行代批文档中的逐字成书对账。
2. 再运行一次 `operator next --json` 与组合 `state --with-inbox`，只统计活动目录，排除 `_rounds/` 历史；`pipeline_complete` 只说明节点 lane 已尽，不替代成书、终审包与 fresh delivery 对账。
3. 此前不增量维护 Markdown；此时才从模板一次补完终审包中的必要人读叙述并清除全部占位符，然后从仓根只运行这一条 canonical 收尾命令：`.workflow/skills/novel-pipeline-operator/scripts/finalize_review.py --base <BASE> --sid <SID> --started-at <STARTED_AT> --start-code-version <START_CODE>`。它在同一进程内只 collect 一次机器事实，以内存快照 render → validate，通过后才原子提交终审包；提交后丢弃旧快照，再开一个全新读作用域执行 `delivery.assess`。最终成本只以 `<BASE>/_service/ledger.jsonl` 为权威源：本次表按 canonical `run_id + node/role` 精确联结，整书 Commander 单列 `role:commander`；无 run id 的历史行只标 `legacy/ungrouped`，本次与累计不相加，缓存与真实 wire 分开，产物 usage/日志不参与混加。
4. finalizer 的 fresh delivery 结果为 `ready*` 才只向用户交付成书与终审包；任一校验、原子提交或 fresh assess 失败都保留原盘并按其错误收口，不把 `pipeline_complete` 或旧快照写成 ready。中间产物仅作为终审包中的可追溯指针。
