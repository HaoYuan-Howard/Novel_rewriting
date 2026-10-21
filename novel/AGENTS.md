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
| 产物、依赖与发布证明 | `novel/store.py`、`novel/paths.py`、`contracts/story_paths.py`、`novel/consumption.py`、`novel/prov.py` |
| 理解、建模、写作与重验 | `novel/stages/chain.py` 编排当前阶段；纯引擎在 `novel/stages/stage2|4|5|7/story.py`，正文共享判据在 `novel/core/story_prose.py`；字段见 `docs/schema/str06.md`（Stage2 指南见 `novel/stages/stage2/AGENTS.md`） |
| 下一动作、授权、并发与暂停 | `novel/orchestrator/operator.py`、`novel/orchestrator/execution_permit.py`、`novel/orchestrator/agent_supervisor.py` |
| 历史审阅决定的解码与执行 | 已随 STR-06 关闭批删除（历史账只读冻结；旧面不得回来由 `tests/test_retired_surface_scan.py` 守卫） |
| Executor、传输与成本 | `novel/llm/providers.py`、`novel/llm/run_journal.py`、`novel/llm/`、`novel/work_units.py` |
| CLI、HTTP 与展示不一致 | `novel/cli/` → `novel/api/` → `novel/views/`，继续追到前端真实消费者 |

模块头解释局部机制；按问题查相关契约与测试，不为一次修改通读所有阶段历史。

## 产物与恢复

- 运行只支持当前显式契约，破坏性变更后旧根原字节归档、新根重建。
  不转换旧故事产物，不把旧响应、旧候选或历史语义账采用成当前生成结果。
- semantic unit 负责语义，compiler 负责机械派生，validator 只报告，repository 唯一持久化。
  Stage2 正式事实只有 SourceStory；Stage4 目标只有 TargetStory；包和审计映射不得反向覆盖真相源。
  正文顺序消费前章已接受短尾，实际字节参与请求身份；不消费的前章改动不得扩大失效。
- 先按原始字节验证账与文件身份，再做 typed 校验；重序列化不能替代历史字节摘要。
  正式读取同时验当前 schema 与输出组 seal；未封存字节只作诊断，不能成为正式输入。
- paid 回答先 durable，再验证、落决定与补提交。checkpoint、prepared、正式文件任一窗口中断，
  优先免费恢复当前契约与相同请求身份下的合法已购结果；--check-only 不产生新购。
  来源损坏、输入漂移或账未知时明确停止，不捏造 provider/model/response_id 来补回执。
- 退出码 1 是已落盘但不通过，2 是显式终态未落盘，70 是崩溃未知；落盘只调用
  `novel/exit_codes.py::landed` 判断。exit 0 与 delivery ready 分别判断，公开读面共用后端证明。

## 调度与模型边界

- orchestrator 只执行 planner 的冻结 action；`HUMAN_NODES` 为空，命中 `approval:*`
  waiting_on 为编排 bug。节点与顺序唯一登记 `contracts/invariants.py::NODES`，不在指南手抄快照。
  Stage2 章观察后置整书综合，Stage4 整书模型再逐章展开，Stage5 正文按阅读序顺序执行。
  没有人工闸或改选面。架构见 `docs/schema/str06.md`，批次只记 `docs/REQUIREMENTS.md`。
  run 停在精确 owner/node/item 后按当前恢复策略继续。semantic unit 只接收独立任务协议；
  不要把工程指南、skill 或额外盘上材料加入模型上下文。
- 业务工作单元、execution epoch 与 provider session 分开；代码漂移走新 epoch，原 profiles 与累计额度保留。
  连续会话绑定原目录和输出 schema 字节，不静默切模型或 effort。
- 停止时先关 admission，再证明 claim、execution 和后代全部排空；父进程退出、超时或暂停字样不证明书锁可释放。
  追加额度用不可变授权及锁内复验，不重置预算，也不解除无进展或 unknown 阻断。
- 跨文件发布按同 writer 输出组封存，崩溃后按真实 durable 回答免费重放/重编/重验补尾。
  不用文件存在性制造接受，不从旧根寻找兼容输入，不把新请求冒充原请求。
- Stage6 judge、人工 typed 审阅、旧 owner patch 与 contract-upgrade 均不构成现役故事入口。
  机械验证不证明语义完整或文学质量；保持 unver，不复活生产正文 Judge 或收缩臂。
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
