# 运行宿主

本文件只回答当前 operator 运行在哪一种宿主、权限事实从哪里取得，以及怎样等待或停止进程。
共同 CLI/打点/报告读法见[工作流契约](workflow.md)，付费节点由哪一种 executor 承接见
[执行模式](execution-modes.md)。只读取与当前宿主匹配的小节。

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

把启动 prompt 与 append-only started contract 一起当作本次权限/身份事实，不从 UI 行为或字段默认推定：

- 新 launcher 在任何 Commander probe/spawn 前同时要求付费授权与正文外发授权；任一不足只允许
  API/UI 做 0 成本 preview，**不会产生一个 headless 进程**。因此新启动 prompt 中两项都应为 granted；
  若缺句、为 false，或与 started 事件不一致，按 contract 损坏安全停止，不能把另一项推成真。
- 启动后先运行 `state <sid>` 与 `inbox <sid>`，从盘上续跑。
- 标准 `approval` 由本 skill 代批，并只走
  `novel.cli approval show/backfill/seen/gate/template/apply` 的 typed façade；
  不直接 import 审批服务或临时拼 HTTP。`redo/unknown` 在可验证恢复路径具备时自行处置，否则安全停止。
- 只在 launcher 授权的 base 范围写人工输入和终审包。权限拒绝说明动作不在允许面，不换 shell 或路径绕过。
- 进程日志与生死记录由 launcher 管理；不要手改 `_service/agent_runs.jsonl` 或日志来改变状态。
- launcher 记录的进程 `state` 与交付 `outcome` 是两根轴：只有 outcome 为 `ready` 或
  `ready_with_disclosures` 才表示服务端已从盘面复验交付；`exit 0` 本身只表示 Agent 正常退出。
- 启动 prompt 带代码指纹；先确认它与 `session_preflight.py` 的 `<START_CODE>` 一致，再按工作流契约
  由 runner 逐步比较。结束终验继续使用这一份起始指纹，跨版本 run 不会被判 ready。
- launcher 只把 run id 与 contract SHA-256 放进环境；runner 必须重读 started 事件，精确复核 base、sid、
  起始代码指纹、Commander/default executor/逐节点 override 与两项授权。不得拿环境里的自由 JSON
  或当前 capability 替换已冻结快照。

当前 launcher 使用两个独立字段：`authorized` 只表示付费授权，`egress_authorized`
只表示正文外发授权。新 API 请求对 `egress_authorized` 的产品默认值为 `true`，UI 也默认勾选；
`authorized` 的产品默认值是 `false`。这只影响 preview 初态；start/batch 的产品闸是两项都为 true。
preview、成功启动的 prompt 与新事件记录必须始终把两项值分别物化；不得因其中一项为真推定另一项。
历史事件若该字段为 `null`，只表示当时未采集，按未知处理，不能追认为已授权。

## 只读进度观察

用户需要实时观察时，可由独立服务用同一 `<BASE>` 启动 `python -m novel.api --base <BASE>`，并打开对应 `/n/<sid>/run` 页面。该服务只是观察者：

- 不改变 CLI 的产物所有权和判定逻辑；
- 一个服务实例只绑定一个 base；
- 切换 base 前停止旧实例；
- 不因页面显示“完成”跳过终审包中的独立对账。
