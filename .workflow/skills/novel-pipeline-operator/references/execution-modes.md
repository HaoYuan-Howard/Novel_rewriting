# 执行模式

本文件只回答“付费生成节点由哪一种 executor 承接”。宿主是否交互、权限从哪里取得、如何等待进程，
见[运行宿主](runtime-profiles.md)；主链、闸门与完成定义不因执行模式改变。

## 选择规则

- 执行模式是一次 run 的输入事实，不由模型临场猜。headless Commander 只使用 started contract
  冻结的默认 executor 与逐节点 override；直接交互式运行则以本次明确选择和生产 preview 为准。
- profile 存在时走 Agent driver；没有 profile 的直接交互式付费节点才走 legacy LLM transport。
  两者共用相同 node、flags、schema、校验、落盘与 ledger 契约，不是两条 Stage0～Stage7 流水线。
- **禁止自动降级。** Agent driver 的 executable、协议、配置或受支持 flag 组合不可用时关闭式停止；
  legacy transport 失败时也不暗换 Agent driver。切换模式必须开始一条选择明确的新 run，并在终审包披露。
- 0 成本 check、typed approval、人工修订、state/inbox/operator next/permit 与最终机械对账不绑定任何
  executor，不得因为外部模型模式不可用而跳过；但已有 sid 的所有 node actions（纯读也一样）仍须
  走 permit+runner，“不绑定 executor”不等于可以绕过执行许可。只有非 node façade 可直走。

## Agent driver

在 headless Commander，或直接交互式命令已经显式冻结完整 executor profile 时读取本节。

1. capability registry 是 driver/provider/model/effort/mode 与本机可探测状态的唯一选项源；不得取数组首项、
   环境默认或聊天记忆代选。
2. headless 下所有 node actions（包括纯读诊断与 blocked recovery）都先取得 exact `operator permit`，再经
   `scripts/run_step.py`。runner 从 `run_id + contract SHA-256` 重读 append-only started 事件，为当前
   permit 的 node/flags 选择默认 profile 或逐节点 override，并把完整 permit、claim、profile 与
   `build_steps` 的逐 child `step_index` 写入 evidence/真实子 CLI；只有 root 重铸 current permit，
   自动 follow-check 复用同一 envelope 并核 canonical argv。
3. 节点 executor 会在正文外发前用同一 driver 做零模型 probe。缺 executable、协议、配置、两项授权，
   或当前 flag 组合尚未迁移时，必须在首个 wire 前停止；不得落回 DeepSeek。
4. Agent executor 自身完成“候选 → 代码校验反馈 → 同一 logical session resume”。主路不带 legacy
   `--response-repair agent`，也不运行 DeepSeek 的 `doctor --llm-transport`。
5. provider session 中断或候选被拒时，只使用该节点现有 durable checkpoint 与同一 session 恢复；
   不另起同节点副本，不把一次恢复记成新流水线阶段。

## Legacy LLM transport

仅在直接交互式 run 没有 executor profile，或操作者明确选择 DeepSeek 临时回滚时读取本节。

1. 首个正文请求前运行
   `<PY> -X utf8 -u -m novel.cli doctor --llm-transport`。它使用正式代理与超时，但不发送正文、
   API key，也不产生模型调用；收到任意 HTTP 状态即证明 DNS/TCP/TLS 通路成立。
2. `SSL UNEXPECTED_EOF` 等连接错误先在 doctor 复现并修复代理/TUN 的进程环境；不要烧一次正文请求诊断网络。
3. legacy transport 仍只执行 `operator next` 给出的 node/flags，并以同一生产 preview、schema、HARD、
   provenance、ledger 与落盘闸为准；“legacy”不代表可以手拼另一份 stage 顺序。
4. 只有响应已经到达 `ResponseRepairRequired` 终态、且明确点名
   `_llm_rejected/**/candidate.json` 时，才按[修复状态机](recovery.md#显式-deepseek-回滚)
   使用 sidecar `response-repair`，并沿用原 `run_id`。不要把它用于 Agent driver 的 session 恢复。
