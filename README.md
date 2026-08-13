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
| `docs/BACKEND_CONTRACT.md` | 冻结的后端编号与判据追踪，供现有覆盖率守卫读取 | 活动开发指导 |

完成事项从 README 与活动需求中删除；“做过什么”由 Git 历史回答。若完成过程产生了可迁移
经验，再整合进 `docs/LESSONS.md`。

## 当前工作边界

- 活动目标：让 `web/` 在 `VITE_USE_MOCK=false` 下使用真实服务完成可达流程。
- 后端不是默认可修改范围。需求中标为“需重开后端”的条目必须先取得明确裁决。
- 不能用 mock、空数组、普通 404 或本地存储伪装尚未提供的后端能力。
- 产品语义与屏幕行为以 `docs/design_brief/` 为准；若它与真实 API 冲突，按
  `docs/REQUIREMENTS.md` 的待裁决项处理，不静默选择一边。

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
