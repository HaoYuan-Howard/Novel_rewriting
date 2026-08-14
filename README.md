# Novel rewriting

把一本中文网络小说经过 stage0～stage7 处理为一部新书：保留情节骨架，重写文字，
替换人名，最终交付 `book.txt`。

当前开发阶段是**真实前端联调**。后端开发范围已关闭；尚未完成、尚未验证和待裁决事项
统一登记在 `docs/REQUIREMENTS.md`。

## 文档职责

| 文档 | 负责 | 不负责 |
|---|---|---|
| 本文件 | 项目入口、当前阶段、运行方式、接手顺序 | 需求明细、完成日志、踩坑长文 |
| `docs/REQUIREMENTS.md` | 仅列未完成事项、阻塞、验收条件与待裁决问题 | 已完成能力、施工过程、界面设计细节 |
| `docs/LESSONS.md` | 可迁移的工程经验与真实踩坑，最多 20 条 | 任务状态、需求清单 |
| `docs/design_brief/` | 产品流程、屏幕、UI 硬规则和交付形状 | 当前实现状态 |
| `web/DELIVERY.md` | 前端现有结构、数据缝和联调事实 | 项目总任务清单 |
| `docs/schema/` | 已实现后端的产物与 API 契约 | 后续工作排序 |
| `docs/REPAIR.md` | 修复机制（旧称「反哺」）：四类处置的路由表、准入与成本上界 | 判据明细 |
| `docs/BACKEND_CONTRACT.md` | 冻结的后端编号与判据追踪，供现有覆盖率守卫读取 | 活动开发指导 |

完成事项从 README 与活动需求中删除；“做过什么”由 Git 历史回答。若完成过程产生了可迁移
经验，再整合进 `docs/LESSONS.md`。

## 当前工作边界

- 活动目标：让 `web/` 在 `VITE_USE_MOCK=false` 下使用真实服务完成可达流程。
- 后端不是默认可修改范围。需求中标为“需重开后端”的条目必须先取得明确裁决。
- 不能用 mock、空数组、普通 404 或本地存储伪装尚未提供的后端能力。
- 产品语义与屏幕行为以 `docs/design_brief/` 为准；若它与真实 API 冲突，按
  `docs/REQUIREMENTS.md` 的待裁决项处理，不静默选择一边。

## 第二阶段：前后端打通

薄 API/view 层已按 DEC-01 的裁决重开并完成：九条读屏 GET
（`novel/api/routes/screens.py`）加三条人工写回 POST（`novel/api/routes/edits.py`）。
服务进程只写 `_service/`，产物变更一律经 CLI 子进程（R-11.3）；CLI 内失败以
HTTP 200 + `JobRecord.state='failed'` 出线，作业失败不是 HTTP 错误。
前端已处真实模式（`web/.env` 的 `VITE_USE_MOCK=false`）：校验面板按 `rules[]`
两轴渲染、写操作走 preview → 确认 → 执行链、旧作业名与旧 check kind 已清除，
批 1–6 真实模式联调通过。

付费链路冒烟语料在 `outputs/e2e_20260813/`（gitignored；三本书 stage0–7 全程，
记录见 `docs/E2E_VALIDATION_20260813.md`）。免费端到端路径——上传 txt → stage0 批准 →
stage1 分章，所涉节点均 `paid=False`——已在真实服务上走通；付费屏在免费语料上
显示诚实的「还没跑」，不拿空数据伪装。

两笔账登记在这里（`docs/schema/_api.md` §七 与 `novel/api/deps.py` 都指着本文件）：

- **改名账**：check kind `stage5_review` → `stage5`（E 期裁决）。八个 kind 现在与
  `invariants.STAGES` 逐值相等、由它派生（`checked_kind` 不手写第二份清单）；
  前端引用已随第二阶段同步清除。
- **裁决账**（2026-08-17 用户裁决）：`stage6/edit` 与 `stage7/edit` 浏览器端
  **明确不可用，走 CLI**（`stage6.hook --apply-edit` / `stage7.rename --apply-edit`）
  ——编辑文件是书目录内的契约产物（`_edit_rel`），R-11.3 禁止服务进程写产物盘，
  浏览器文本没有合规通道。stage6 在浏览器只挂 `POST /stage6/approve`
  （`--pick` 定稿，只收序号不收文本），stage7 只有 `POST /stage7/approve`（无请求体）。

## 接手顺序

1. 运行 `git status --short`，确认并保留工作区已有改动。
2. 阅读 `docs/REQUIREMENTS.md`，只从仍存在的条目中选任务。
3. 做前端时再读 `docs/design_brief/00_START_HERE.md` 与 `web/DELIVERY.md`；
   做后端缺口时按条目点名的 schema 和代码落点回查。
4. 在真实消费路径验收；通过后删除对应活动条目。

## 运行与验证

后端使用 conda 环境 `novel_refactor`：

```powershell
conda run -n novel_refactor python -m pytest -q
conda run -n novel_refactor python -m novel.api --base <Output目录>
```

API 只监听 `127.0.0.1:8765`。前端默认配置位于 `web/.env`；真实联调时应为
`VITE_USE_MOCK=false`。

```powershell
cd web
npm ci
npm run typecheck
npm run seams
npm run build
npm run dev
```

只跑编译检查不代表联调完成。交付前还要在真实服务上覆盖正常、未运行、错误和空态，
并确认关闭 mock 后没有样本正文进入网络响应或构建产物。

## 关键目录

```text
contracts/       后端 schema、判据注册与跨层常量
novel/           CLI、运行时、API、视图与 stage 实现
web/             React + Vite + TypeScript 前端
docs/schema/     后端产物/API 契约
docs/design_brief/  前端产品与交付规格
tests/           后端与文档守卫
```

快速原型 `../Novel_refactoring/` 只作为证据来源，不在本仓任务中修改。
