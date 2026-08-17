# 运行环境

只读取与当前宿主匹配的小节；主链、判据和修复规则不因宿主改变。

## 共同规则

- cwd 保持仓根；`--base` 永远使用绝对路径。
- 使用当前项目实际解释器执行 `-X utf8 -u -m novel.cli`，不要切到 base 环境。
- 直接取得子进程退出码，不在命令后接会覆盖退出码的管道。**文件重定向不是管道**：
  `> 文件` 不影响退出码，取到退出码之后再过滤那个文件是长报告的推荐读法。
- `mark start` / 真跑 / `mark end` 在**同一次 shell 调用内串联**，取到真实退出码后再打 end 点，
  不要拆成三次独立往返。单次 `mark` 约 1 秒（Python 冷启动），**成本在往返次数不在耗时**：
  一条 28 命令的全链拆开跑是 84 次往返，串联是 28 次。
- 一次只运行一个写盘节点；一次只运行一本书。
- 对长命令使用宿主提供的持久句柄或足够长的前台超时。每 60 秒以内给出一次状态更新，不用短 timeout 杀进程来模拟更新。
- 任何宿主中断后先运行 `state`/`inbox`，不要从聊天记忆推断落盘位置。

「打点串联」与「重定向后过滤」合起来在 PowerShell 里的写法（36 KB 报告压到 5 行，判定信息一条不少）：

```powershell
& $PY -X utf8 -u -m novel.cli mark $SID --node <node> --phase start --base $B
& $PY -X utf8 -u -m novel.cli run <node> $SID --check-only --base $B > "$SP\out.txt"
$rc = $LASTEXITCODE
& $PY -X utf8 -u -m novel.cli mark $SID --node <node> --phase end --exit $rc --base $B
Get-Content "$SP\out.txt" -Encoding UTF8 |
  Select-String -Pattern '^\s*\[!!\]|^\s*\[\?\?\]|^合计|→\s+(PASS|AMBER|FAIL)|revalidated'
```

## 交互式主会话

1. 在首个外部模型节点前一次性确认付费授权与正文外发授权；用户已明确给出时只记录，不重复询问。
2. 一次性 preview 即将发生的付费主路径，让用户知道调用节点；不要编造金额。
3. 获得两项总授权后，自行处理标准闸门和文档内修复，不在中间正常节点打断用户。
4. 若工具返回可等待的 job/cell id，保留该句柄并定期 wait；不要另起同节点副本。
5. 只有扩大权限、改变产品语义或授权缺失时才需要用户；否则以终审包作为唯一人工界面。

## headless agent

把启动 prompt 当作本次权限事实，不从 UI 行为或布尔字段名称推定授权：

- prompt 必须分别明确“付费授权”和“正文外发授权”。只出现其中一项时，另一项视为 `not_granted`。
- 无用户可追问。授权不足时仍运行只读的 `state`、`inbox` 和下一节点 `preview`，把 preview 生成的精确恢复命令写入终审包；在执行首个外部调用前安全停止。
- 启动后先运行 `state <sid>` 与 `inbox <sid>`，从盘上续跑。
- 标准 `approval` 由本 skill 代批；`redo/unknown` 在两项授权和可验证恢复路径都具备时自行处置，否则安全停止。
- 只在 launcher 授权的 base 范围写人工输入和终审包。权限拒绝说明动作不在允许面，不换 shell 或路径绕过。
- 进程日志与生死记录由 launcher 管理；不要手改 `_service/agent_runs.jsonl` 或日志来改变状态。

当前 launcher 使用两个独立字段：`authorized` 只表示付费授权，`egress_authorized`
只表示正文外发授权。新 API 请求对 `egress_authorized` 的产品默认值为 `true`，UI 也默认勾选，
但 preview、启动 prompt 与新事件记录必须始终把两项值分别物化；不得因其中一项为真推定另一项。
历史事件若该字段为 `null`，只表示当时未采集，按未知处理，不能追认为已授权。

## 只读进度观察

用户需要实时观察时，可由独立服务用同一 `<BASE>` 启动 `python -m novel.api --base <BASE>`，并打开对应 `/n/<sid>/run` 页面。该服务只是观察者：

- 不改变 CLI 的产物所有权和判定逻辑；
- 一个服务实例只绑定一个 base；
- 切换 base 前停止旧实例；
- 不因页面显示“完成”跳过终审包中的独立对账。
