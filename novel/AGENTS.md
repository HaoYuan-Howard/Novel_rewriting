# 后端开发

本目录实现 CLI、产物生产、模型调用、确定性调度与本机服务，消费 `contracts` 的定义。
使用 conda 环境 `novel_refactor`；Python 保留类型标注、4 空格、snake_case/PascalCase。
安装、服务启动与诊断见 `docs/OPERATIONS.md`；运行或修改测试先读 `tests/AGENTS.md`。

**术语**：本目录路径名里的 `agent`（如 `orchestrator/agent.py`、`/api/agent/*`）指**整书代跑
编排**，不是 LLM executor。现役付费执行唯一走直连 LLM（`kind:"legacy"`）；退役 Agent CLI
表面由 `tests/test_retired_surface_scan.py` 与 `retired_formats` 守门。

## 按调用链定位

| 问题 | 入口 |
|---|---|
| 产物、依赖与历史采用 | `novel/store.py`、`novel/paths.py`、`novel/consumption.py`、`novel/prov.py` |
| 本层生成、重验或人工编辑 | `novel/stages/` 对应 stage 的 producer 与恢复模块；对应 `docs/schema/`；Stage2 局部不变量见 `novel/stages/stage2/AGENTS.md` |
| 下一动作、授权、并发与暂停 | `novel/orchestrator/operator.py`、`novel/orchestrator/execution_permit.py`、`novel/orchestrator/agent_supervisor.py` |
| 历史审阅决定的解码与执行 | `novel/orchestrator/reviewer_legacy.py`（Commander 退役后的迁入地）、`novel/orchestrator/reviewer_action_evidence.py` |
| Executor、传输与成本 | `novel/llm/providers.py`、`novel/llm/run_journal.py`、`novel/llm/`、`novel/work_units.py` |
| CLI、HTTP 与展示不一致 | `novel/cli/` → `novel/api/` → `novel/views/`，继续追到前端真实消费者 |

模块头解释局部机制；按问题查相关测试和 `docs/LESSONS.md`，不为一次修改通读所有阶段历史。

## 产物与恢复

- producer 拥有本层生成、归一、人工写回与恢复；共用 schema、判据、归档和 provenance。
  实际依赖投影同时决定模型材料、freshness、采用和恢复身份；证据或邻章边界变化不能漏绑，也不整书扩大失效。
- 先按原始字节验证账与文件身份，再做 typed 校验；重序列化不能替代历史字节摘要。
  旁路决定在最低读取边界叠加到 check、inbox 和 API，不能等重新生成才生效。
- paid 回答先 durable，再验证、落决定与补提交。checkpoint、prepared、正式文件任一窗口中断，
  优先免费恢复合法已购结果；来源损坏或账未知时不得签发新调用许可。
- 退出码 1 是已落盘但不通过，2 是显式终态未落盘，70 是崩溃未知；落盘只调用
  `novel/exit_codes.py::landed` 判断。exit 0 与 delivery ready 分别判断，公开读面共用后端证明。

## 调度与模型边界

- orchestrator 只执行 planner 的冻结 action；`HUMAN_NODES` 为空（2026-09-15 全自动裁决，
  批次 B4），命中 `approval:*` waiting_on 为编排 bug（fail-closed）。F8a 起现役链是
  11 个 `str06.` 节点（`contracts/invariants.py::NODES`），没有任何人工闸或改选面。
  run 停在精确恢复/披露面后续跑。semantic unit 在校验过的空 cwd 中运行；
  不要把工程指南、skill 或额外盘上材料加入模型上下文。
- 业务工作单元、execution epoch 与 provider session 分开；代码 漂移走新 epoch，原 profiles 与累计额度保留。
  连续会话绑定原目录和输出 schema 字节，不静默切模型或 effort。
- 停止时先关 admission，再证明 claim、execution 和后代全部排空；父进程退出、超时或暂停字样不证明书锁可释放。
  追加额度用不可变授权及锁内复验，不重置预算，也不解除无进展或 unknown 阻断。
- 跨 producer prepared 补尾走登记的 deterministic action；批量决定先保存 manifest，逐项写前复验 frontier，
  冲突留证。会令自身 workpack 过期的改写先保存 request/authority receipt，再取新 epoch 的 permit。
- 历史决定仍需完整覆盖当前义务；修改或删除问题主体后仍需复审，较新否决优先。Stage6 judge 与
  `stage6.approve` typed 决定面随 Stage6 整层退役（F8a）；语义质量一律 unver 披露，
  不复活 evaluator profile、生产正文 Judge 或 Stage5 收缩臂。
- 内容轮、网络重试、格式拒绝、恢复分别记账；共享 slot 落实书级及供应商限制。累计调用耗时不作墙钟，
  性能比较保留失败、重试、未知用量与质量分母；公开日志不泄露正文。

## 服务与验证

- HTTP 只绑定 loopback；路由负责解析与适配，使用 `contracts/schema/api.py`，不另造判据、枚举或 argv。
  preview 与执行共用计划和身份，执行时复验权限、额度、冲突及停止条件。
- 后端缺能力就明确报缺口，不用普通 404、空数组、mock 或 UI 状态伪装实现。CLI 的有效决定由同一证明公开，
  不要求 HTTP 独有盖章；联调检查动作、产物、刷新与服务重启后的状态。
- 按变更入口选择定向测试：阶段及其直接下游、调度/授权恢复、API HTTP 或 provider 协议。
  进程协议、取消与恢复用零成本真实子进程补足 fake 的边界；已有结果恢复验证不重复调用模型。
- 新增子包同步 `pyproject.toml` 的 packages；第三方资源许可如 `novel/llm/resources/NOTICE.md` 必须保留。
