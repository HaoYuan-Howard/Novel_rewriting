# Novel rewriting

把一部中文网络小说处理成一本新书：保留情节骨架，全文重写，替换人名，最终交付
`stage7/<书名>_rewriting.txt`。现役链路包含 stage0、stage1、stage2、stage4～stage7；Stage3 编号保留
为空洞，不对应产物层。

单书主链、agent 驱动和前端真实服务已经接通。当前工程重点是正确性、恢复能力、质量闭环与契约收口；
未完成事项只登记在 `docs/REQUIREMENTS.md`，README 不维护任务状态。

## 产品边界

- 单人、本机、桌面浏览器；服务只绑定 `127.0.0.1`，不做登录、协作、通知或移动端。
- 输入是一部中文网络小说，输出章号恒等于源章号，不合并、不拆分、不删除、不重排。
- 系统的首要风险是 LLM 造事实、提前泄密或漏内容后仍写得通顺。因此产品是证据审校台，不是只看
  绿灯和分数的 dashboard。
- 看、免费但写盘、付费 LLM 三类动作必须可区分。付费前展示完整命令并再次确认；未知成本不伪造估价。
- 人工批准是正式契约的一部分：stage0、Stage2 全局事实、Stage4 设计与结构、Stage5 写作与闭合、
  Stage6 文案、Stage7 人名各有独立决定面，不能由另一张票或机械绿灯代开。

## 处理链与成本

| 产物层 | 系统动作 | 模型成本与人工闸 |
|---|---|---|
| stage0 | 识别并剔除番外、后记、尾声、作者有话说 | 0 LLM；人工审阅批准后才能下行 |
| stage1 | 确定性摄入与分章 | 0 LLM |
| stage2 | 全书事实底座；逐章事实原子、因果、舞台与保真材料 | 全书级调用 + 逐章调用；global 人审可与 blueprint 计算重叠，Stage4 前汇合 |
| stage4 | A 盲设计；B 证据绑定与计划编译；确定性写作上下文 | A/B 独立付费授权和独立人审；Context 0 LLM |
| stage5 | canonical 闭卷写作；全书机械 Review 与修订闭环 | 逐章付费写作；Review/人工销账/最终闭合 0 LLM |
| stage6 | 一次生成三个开篇文案候选 | 1 次调用；人选一条定稿 |
| stage7 | 生成改名决策，确定性替换并组书 | 1 次改名调用；替换、组装 0 LLM；人定稿 |

全链调用数随章节数、真实重试和缓存命中变化，不承诺固定次数或价格。节点、并发、恢复、授权和当前
响应契约的冻结细节见 `docs/BACKEND_CONTRACT.md`；重大选择的实测依据见 `docs/DECISIONS.md`。

## 七个产物层的人类任务

| 层 | 人的主要检查 |
|---|---|
| stage0 | 整本审阅删除范围，可直接修正文或范围，并提交首个硬批准 |
| stage1 | 原文与切分并排，确认无漏章、错章、断号 |
| stage2 | 逐项复核全书事实与证据；所有 current blocking 项闭合后批准进入 Stage4 |
| stage4 | 先审 A 的全书策略和逐章候选，再独立审 B 的结构、证据落点与 concerns |
| stage5 | 对照材料、原文、成稿与 findings；销账 edit/trim/wontfix 后提交 typed 闭合回执 |
| stage6 | 三个候选并排，人工选择或编辑最终开篇文案 |
| stage7 | 审新旧名表、替换前后和残留候选，人工定稿后组书 |

界面路由、屏幕职责和 UI 硬规则见 `web/DELIVERY.md`。

## 快速开始

Windows 下双击 `scripts\start.cmd`：脚本按需构建前端、启动本机服务并打开浏览器。默认地址为
`http://127.0.0.1:8765/`，默认数据根为仓库下的 `outputs/`。

- 改过后端代码或怀疑旧服务残留：运行 `scripts\start.cmd -Restart`，或双击 `scripts\restart.cmd`。
- 指定书库根：使用 `scripts\start.cmd -Base <含各书目录的那一层>`。
- 前端热更新：追加 `-Dev`。
- 后端 CLI、测试、网络诊断、AgentRun、备份与恢复命令见 `docs/OPERATIONS.md`。
- 完整跑书与恢复不要手拼命令，使用 `.workflow/skills/novel-pipeline-operator/SKILL.md`。

## 当前能力与验收边界

- `novel.cli` 可驱动七层单书主链并组装成书；前端默认以 `VITE_USE_MOCK=false` 连接真实服务。
- 服务进程只写 `_service/`，产物变化由 CLI 子进程完成；页面写操作遵循 preview → 确认 → 作业执行。
- 左栏“设置”可保存当前输出目录常用的 Commander、默认 Executor 与逐节点覆盖；跑书时自动载入，付费与正文外发授权仍须逐次确认。
- 人工闸可以按规则委托 agent 审阅，依据持续写入书目录根的 `<书名>_review.md`。
- 默认 pytest 刻意排除 corpus；仓内 current-schema manifest 尚未完成。只有按 `tests/README.md` 运行
  `--require-corpus` 并闭合精确盘面后，才可以称“全量语料绿”。

## 文档职责

| 文档 | 唯一职责 |
|---|---|
| `README.md` | 人类入口：产品边界、处理链、人类任务、快速启动和文档地图 |
| `AGENTS.md` | agent 入口：任务路由、不可破坏的不变量、验证和代码纪律 |
| `docs/BACKEND_CONTRACT.md` | 可执行的现役 R/Q/B 契约、双向覆盖率、修复路由和证据基线 |
| `docs/DECISIONS.md` | 重大产品裁决、实验边界和可继续引用的历史验证证据 |
| `docs/REQUIREMENTS.md` | 仅列仍未完成的事项、阻塞、验收条件与待裁决问题 |
| `docs/LESSONS.md` | 按任务路由的工程原则索引，不承担施工历史或活动状态 |
| `docs/OPERATIONS.md` | 本机启动、测试、AgentRun、网络诊断、备份与恢复手册 |
| `docs/schema/` | 已实现后端 artifact/API 契约的现在时说明 |
| `web/DELIVERY.md` | 界面规格、技术与数据缝、路由、组件和前端开发命令 |
| `tests/README.md` | 测试分层、corpus 硬验收和运行口径 |

## 接手工程

1. 先检查并保留工作区已有改动。
2. 用户未直接点名任务时，只从 `docs/REQUIREMENTS.md` 仍存在的条目开工。
3. 按 `AGENTS.md` 的任务路由读取相关契约、schema、经验和测试。
4. 验证真实消费者、失败/恢复路径和最终盘面；通过后删除对应活动条目，重大裁决写入
   `docs/DECISIONS.md`。

## 关键目录

```text
contracts/        后端 schema、判据注册与跨层常量
novel/            CLI、运行时、API、视图与 stage 实现
web/              React + Vite + TypeScript 前端
docs/schema/      artifact/API 契约
tests/            后端、语料与文档守卫
tools/            探针、对比、备份与 schema 派生工具
.workflow/skills/ agent 端到端操作规程的 canonical 契约
.agents/skills/   Codex 宿主发现 shim
.claude/skills/   Claude 宿主发现 shim
```

本仓不提交正文、付费产物或本地密钥。需要原始语料的工具必须显式读取 `NOVEL_DATA`、`NOVEL_CORPUS`
或 `NOVEL_OUTPUT`；取不到时应明确报告“没查”。
