# 运行环境

只读取与当前宿主匹配的小节；主链、判据和修复规则不因宿主改变。

## 共同规则

- cwd 保持仓根；`--base` 永远使用绝对路径。**cwd 会被别的工具改掉**：交互宿主的多个 shell
  工具可能共享工作目录，此时每次 shell 调用都显式指定/切回仓根；headless launcher 已把
  子进程 cwd 钉在仓根，且 Bash 白名单要求命令以项目 Python 开头，因此不要在命令前拼 `cd`。
  两种宿主都不得 `cd` 进产物目录，读产物一律使用绝对路径，否则随后的 `-m novel.cli` 会报
  `ModuleNotFoundError: No module named 'novel'`。
- 使用当前项目实际解释器执行 `-X utf8 -u -m novel.cli`，不要切到 base 环境。
  **这条同样管临时诊断脚本**：读产物 JSON、import `contracts.*` / `novel.*` 的一次性
  `python -c` 也必须用 `<PY>`。裸 `python` 多半解析到另一套环境，症状是
  `ModuleNotFoundError: No module named 'pydantic'`——那不是仓库坏了。
- 直接取得子进程退出码，不在命令后接会覆盖退出码的管道。**文件重定向不是管道**：
  `> 文件` 不影响退出码，取到退出码之后再过滤那个文件是长报告的推荐读法。
- `mark start` / 真跑 / `mark end` 在**同一次 shell 调用内串联**，取到真实退出码后再打 end 点，
  不要拆成三次独立往返。单次 `mark` 只是一次 Python 冷启动，**成本在往返次数不在耗时**：
  拆开跑等于把全链的往返数乘以三。
- 一次只运行一个写盘节点；**本次会话**一次只管一本书。⚠ 作用域是会话，不是系统：
  别书的作业同时在跑不构成障碍（互斥只落在各自 `sid`），也不需要等它。
- 对长命令使用宿主提供的持久句柄或足够长的前台超时。每 60 秒以内给出一次状态更新，不用短 timeout 杀进程来模拟更新。
- 任何宿主中断后先运行 `state`/`inbox`，不要从聊天记忆推断落盘位置。
- 真跑前先执行 `<PY> -X utf8 -u -m novel.cli doctor --llm-transport`。它使用正式代理与超时，
  但不发送正文、API key，也不产生模型调用；收到任意 HTTP 状态即证明 DNS/TCP/TLS 通路成立。
  `SSL UNEXPECTED_EOF` 等连接错误应在这里先复现并修代理/TUN 进程环境，不要烧一次正文请求来诊断网络。
- 任何宿主都先用 `scripts/session_preflight.py` 固化 `<STARTED_AT>` 与 `<START_CODE>`。正常节点走
  `scripts/run_step.py --expect-code-version <START_CODE>`；特殊诊断/人工入口也在动作前复算并逐字比较。
  结束时把同一指纹传给终验。`unknown == unknown` 不证明同版；结束时现取当前值也不证明起点。

「打点串联」与「重定向后过滤」合起来在 PowerShell 里的写法（把数十 KB 的报告压到个位数行，
保留全部命中、非 `ok` 状态、来源与告警行）：

```powershell
& $PY -X utf8 -u -m novel.cli mark $SID --node <node> --phase start --base $B
& $PY -X utf8 -u -m novel.cli run <node> $SID --check-only --base $B > "$SP\out.txt"
$rc = $LASTEXITCODE
& $PY -X utf8 -u -m novel.cli mark $SID --node <node> --phase end --exit $rc --base $B
Get-Content "$SP\out.txt" -Encoding UTF8 |
  Select-String -Pattern '^\s*\[!!\]|^\s*\[\?\?\]|^\s*\[--\]|^\s*\[\?{4}\]|^合计|^──|^\s*⚠|→\s+(PASS|AMBER|FAIL)|revalidated'
```

⚠ **这条正则只对 `--check-only` 报告有效。** `run` 本身打印的是一两行摘要（成功那行以 `✓` 起头，
显式终态以 `✗` 起头），套同一条正则会得到**零行输出**——那不是"节点没产出"，是读法用错了。
`run` 的输出短，直接整读。

这条正则**每一段都在接住一类会改变判定的行**，删任何一段过滤就不再是"只改变读法"：

| 模式 | 接住的行 | 少了它会怎样 |
|---|---|---|
| `\[!!\]` `\[\?\?\]` `\[--\]` | 命中 / 够不着 / **没跑成** | `[--]` 是"这条判据根本没跑"，压掉它等于把 skipped 洗成 ok |
| `\[\?{4}\]` | `[????] <键>：未记录` | "没人说"与"没有"分不开，记账分母失真 |
| `^──` | `── 来源 ──` / `── 判据 ── N 条` / `── 记账 ── N 个键` | freshness 与两个分母全部消失，绿灯就没了分母 |
| `^\s*⚠` | `⚠ 判的这一份是 .INVALID 稿`、`⚠ 幽灵边 N 条` | 把 `.INVALID` 稿的结论读成正式结论（见[经验库](field-notes.md) FN-015） |
| `^合计` `→ (PASS\|AMBER\|FAIL)` `revalidated` | 四态合计、badge、强审计标记 | — |

`inbox` 需要同一种收窄——它会随书变长，`verification` 那一类每项一条、条条同形，
整读会把首行的八类计数冲出屏幕：

```powershell
& $PY -X utf8 -u -m novel.cli inbox $SID --base $B |
  Select-String -Pattern '^── 待人工|^\s*\[(invalid|rejected|stalled|repairable|human_only|upstream|approval)\]' -Context 0,2
```

首行的八类计数是余额本身，必须留；`-Context 0,2` 带出每条的 detail 与处置行。
`verification` **刻意不逐条打印**——它按类计数即可（要逐条只在真正销账那一步单独取），
但计数绝不能省：它进终审包。

## 交互式主会话

1. **不索要授权。** 用户让你用本 skill 跑书，付费授权与正文外发授权就都已经在了
   （见[接口契约](../SKILL.md#接口契约)）。**这条最常见的破法是把「先确认一句更稳妥」当成礼貌**：
   它每次都要用户回一轮，答案恒是同一个，而「用户只审最终成稿」正是本 skill 要交付的东西。
   只有用户在本次对话里明确收回某一项时，才按 `not_granted` 处理那一项。
2. 开跑前 preview 一次即将发生的付费主路径并**陈述**哪些节点会调用外部模型。
   这是通报，不是提问：说完直接开跑，不停下等确认，也不要编造金额。
3. 自行处理标准闸门和文档内修复，不在中间正常节点打断用户。
4. 若工具返回可等待的 job/cell id，保留该句柄并定期 wait；不要另起同节点副本。
5. 只有扩大权限、改变产品语义或用户明确收回了授权时才需要用户；否则以终审包作为唯一人工界面。

### 长节点：进度从 `@observe` 读，不从猜测读

付费节点自己在 stderr 上打进度事件，够满足"每 60 秒汇报一次"而不必打断进程：

- `llm_attempt_heartbeat`：带 `attempt` / `max_attempts` / `elapsed_ms` / `state`
  （`started` → `in_flight` → 终态）。它还在 `in_flight` 就说明进程活着，不要重启。
- `chapter_progress`：逐章节点专用，带 `completed` / `succeeded` / `failed` / `chapter_total`。
  **逐章生成成功不等于落盘**——闸门在合并落盘那一步判，两者可以一个全绿一个 exit 2。

把真跑的 stdout/stderr 一起重定向到文件后，用 `Select-String -NotMatch 'heartbeat'`
看结论行、用 `Select-String 'chapter_progress'` 数完成章；重定向不影响退出码。

### 后台句柄被终止时

宿主提供的后台/detached 句柄**不保证活到进程结束**，且它报告的"完成/退出码"可能是外层
shell 的而非真跑的。判断真伪只看三样，全部指向盘上而不是通知：串联脚本最后那行退出码回显
在不在、目标产物在不在、账本有没有该节点的记录。

被终止后按[修复状态机](recovery.md)的付费中断条款处理，并优先改用**前台 + 分批**：
逐章节点用 `--chapters` 把一次运行切到宿主超时以内。这条之所以安全，是因为
`--chapters` 缺省的语义就是"补写所有缺的章" ⇒ 逐章节点天然增量，
已落盘的章不会被重写、也不会重复付费。⚠ 前台的超时上限是**硬杀**，
宁可分批多跑几轮，也不要设一个刚好够呛的超时去赌——那正是"用短 timeout 杀付费进程"。

### 仓库代码在跑书途中被改

同一个仓库可能有别的会话在改代码。症状是 CLI **整体**崩在 import 期
（例如某模块 `NameError`），而同一条命令几分钟前还正常，且报错栈与本书、本节点无关。

判据：`git status` 显示 `novel/` 下有未提交改动，且报错文件正在其中。
处置：**不改仓库代码**（那是本 skill 的硬边界），也不把当前新指纹追认为本次起点。
等对方编辑完成；只有代码逐字恢复到 `<START_CODE>` 才能在同一会话重试，并重新取乐观锁 hash。
若稳定落在另一指纹，本次以 `stopped` 收口，重新运行 preflight 后才开始另一条 run。命令崩在
import 期时通常没有写盘，但仍以 `state`、目标产物与信标核实，不从栈形状猜。把干扰如实记进
终审包，不要读成本书的产物问题。

## headless agent

把启动 prompt 当作本次权限事实，不从 UI 行为或布尔字段名称推定授权：

- **交互式那条「默认已授予」在这里不成立。** launcher 恒把两项授权各写成一句整话，
  取值以 prompt 原文为准：这里没有可追问的人，而 launcher 的付费授权字段产品默认为**假**
  （正文外发那一项默认为真），两者不对称，靠默认值推定会推反。
  prompt 只出现其中一项时，另一项视为 `not_granted`。
- 授权不足时仍运行只读的 `state`、`inbox` 和下一节点 `preview`，把 preview 生成的精确恢复命令写入终审包；在执行首个外部调用前安全停止。
- 启动后先运行 `state <sid>` 与 `inbox <sid>`，从盘上续跑。
- 标准 `approval` 由本 skill 代批；`redo/unknown` 在两项授权和可验证恢复路径都具备时自行处置，否则安全停止。
- 只在 launcher 授权的 base 范围写人工输入和终审包。权限拒绝说明动作不在允许面，不换 shell 或路径绕过。
- 进程日志与生死记录由 launcher 管理；不要手改 `_service/agent_runs.jsonl` 或日志来改变状态。
- launcher 记录的进程 `state` 与交付 `outcome` 是两根轴：只有 outcome 为 `ready` 或
  `ready_with_disclosures` 才表示服务端已从盘面复验交付；`exit 0` 本身只表示 Agent 正常退出。
- 启动 prompt 带代码指纹；先确认它与 `session_preflight.py` 的 `<START_CODE>` 一致，再按共同规则
  由 runner 逐步比较。结束终验继续使用这一份起始指纹，跨版本 run 不会被判 ready。

当前 launcher 使用两个独立字段：`authorized` 只表示付费授权，`egress_authorized`
只表示正文外发授权。新 API 请求对 `egress_authorized` 的产品默认值为 `true`，UI 也默认勾选；
`authorized` 的产品默认值是 `false`，**必须由调用方显式给真**——这条不对称正是上面
「headless 以 prompt 原文为准」的理由。
preview、启动 prompt 与新事件记录必须始终把两项值分别物化；不得因其中一项为真推定另一项。
历史事件若该字段为 `null`，只表示当时未采集，按未知处理，不能追认为已授权。

## 只读进度观察

用户需要实时观察时，可由独立服务用同一 `<BASE>` 启动 `python -m novel.api --base <BASE>`，并打开对应 `/n/<sid>/run` 页面。该服务只是观察者：

- 不改变 CLI 的产物所有权和判定逻辑；
- 一个服务实例只绑定一个 base；
- 切换 base 前停止旧实例；
- 不因页面显示“完成”跳过终审包中的独立对账。
