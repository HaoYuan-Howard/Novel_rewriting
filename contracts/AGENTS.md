# 契约开发

本目录定义节点、取值域、schema 与判据，供运行时共同消费。它不执行流水线、不读取产物、不导入 `novel`。
依赖方向为 artifacts → invariants → rules/schema；Python 使用类型标注、4 空格与现有命名习惯。

## 修改入口

| 变更 | 先定位 |
|---|---|
| 节点、枚举与规则归属 | `contracts/invariants.py::NODES`、`contracts/rules.py` |
| artifact 版本、路径与载体 | `contracts/schema/__init__.py::ARTIFACT_SPECS`、对应领域 schema 与 `docs/schema/` |
| 机械判据与修复路由 | `contracts/judges/`、同一 R 编号的全部守卫及反例 |
| R/Q/B 定义或覆盖关系 | `docs/BACKEND_CONTRACT.md`、`contracts/requirements.py` |

`stage` 是产物层，`node` 是作业 id；Stage3 已退役，复检用 `--check-only`，不另造 check 节点。
BACKEND_CONTRACT 的 R 标题、三列表格、Q/B 形状和“谁在查”记号是解析接口；按稳定编号读取相关条目。

## 变更边界

- 改字段时追实际语义消费者，包括整对象传递、派生投影、prompt、恢复和 API，不能只搜字段名。
- 只接受登记的当前 artifact 版本；本仓没有通用迁移机制。破坏形状必须升版，并明确重建 producer、
  成本和授权；不手补旧文件或用默认值冒充历史证据。
- 保留不可重建的主体与证据，可从上游稳定重算的值优先放消费投影；unknown、缺失和合法空值分别建模。
- 模型 wire、正式 artifact 与 API DTO 的权限、身份不同；紧凑回答由可信代码组装为完整 typed 主体。
  动态 schema 下发完整分支约束，拒绝重复 JSON 键，不靠宽松类型或忽略字段接纳坏输入。
- 每条规则登记 R 编号、owner、严重度、检查域及 `deterministic / llm / human / none` 修复路由。
  HARD 在真实判据入口提供红例与绿对照，多分支逐支验证；读数与未经标定的语义不升级为自动返工。

## 验证

先按根指南确定验证批次，运行测试前读 `tests/AGENTS.md`。契约形状稳定后执行
`docs/BACKEND_CONTRACT.md` §9.5 五文件，再补实际 producer/consumer 与恢复测试。
版本变更覆盖具体路径、`.INVALID`、旧版本拒绝和免费恢复；API 变更同批闭合前端 types、fixture 与页面。
