# Novel rewriting

把一本中文网络小说经过 stage0～stage7 处理为一部新书：保留情节骨架，重写文字，
替换人名，最终交付 `book.txt`。

单书 stage0～stage7、agent 驱动全链和前端真实服务主路径已经验收。当前开发重点是
**正确性、恢复能力、质量闭环与契约收口**；尚未完成、尚未验证和待产品裁决的事项统一登记在
`docs/REQUIREMENTS.md`。它是唯一活动待办，README 不重复维护任务状态。

## 文档职责

| 文档 | 负责 | 不负责 |
|---|---|---|
| 本文件 | 项目入口、当前阶段、运行方式、接手顺序 | 需求明细、完成日志、踩坑长文 |
| `docs/REQUIREMENTS.md` | 仅列未完成事项、阻塞、验收条件与待裁决问题 | 已完成能力、施工过程、界面设计细节 |
| `docs/LESSONS.md` | 可迁移的工程经验与真实踩坑，最多 20 条 | 任务状态、需求清单 |
| `web/DELIVERY.md` | 前端现有结构、数据缝和联调事实 | 项目总任务清单 |
| `docs/schema/` | 已实现后端的产物与 API 契约 | 后续工作排序 |
| `docs/BACKEND_CONTRACT.md` | 统一产品/UI 规格、冻结编号、修复路由、守卫耦合、证据基线与历史验收账 | 活动开发指导 |

README 只保留稳定入口和当前能力；“做过什么”由 Git 历史与
`docs/BACKEND_CONTRACT.md` §十回答。可迁移经验整合进 `docs/LESSONS.md`。

## 当前工作边界

- 只从 `docs/REQUIREMENTS.md` 仍存在的条目开工，优先处理 P0；待产品裁决项先确认语义再实现。
- 条目需要跨后端、前端、契约、测试或文档时，完成整条真实消费路径，不按历史阶段边界截断。
- 不能用 mock、空数组、普通 404 或本地存储伪装尚未提供的后端能力。
- 产品语义与屏幕行为以 `docs/BACKEND_CONTRACT.md` §六为准；若它与真实 API 冲突，按
  `docs/REQUIREMENTS.md` 的待裁决项处理，不静默选择一边。

## 当前已具备的主路径

- `novel.cli` 可驱动单书 stage0～stage7，最终组装 `book.txt`；完整操作规程位于
  `.claude/skills/novel-pipeline-operator/SKILL.md`。
- `web/` 默认以 `VITE_USE_MOCK=false` 连接真实服务。九条读屏 GET 与三条人工写回 POST
  已接通；写操作遵循 preview → 确认 → CLI 作业执行，作业失败由 `JobRecord.state='failed'`
  表达，而不是伪装成 HTTP 错误。
- 服务进程只写 `_service/`，产物变更经 CLI 子进程完成（R-11.3）。`stage6/edit` 与
  `stage7/edit` 因此只走 CLI；浏览器仅提供对应批准入口。
- 人工闸门可委托 agent 按规则代审，依据持续写入输出目录根的
  `final_review_<sid>.md`。真实书全链验收和账本摘要见 `docs/BACKEND_CONTRACT.md` §十。

## 接手顺序

1. 运行 `git status --short`，确认并保留工作区已有改动。
2. 阅读 `docs/REQUIREMENTS.md`，只从仍存在的条目中选任务；待裁决项先取得产品决定。
3. 做前端时再读 `docs/BACKEND_CONTRACT.md` §六、§八与 `web/DELIVERY.md`；
   做后端缺口时按条目点名的 schema 和代码落点回查。
4. 覆盖真实消费者和失败路径；验收通过后删除对应活动条目。

## 运行与验证

后端使用 conda 环境 `novel_refactor`：

```powershell
conda run -n novel_refactor python -m pytest -q
conda run -n novel_refactor python -m novel.api --base <Output目录>
conda run -n novel_refactor python -X utf8 -u -m novel.cli inbox <sid> --base <Output目录>   # 待人工六类清单（0 成本）
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
tests/           后端与文档守卫
tools/           探针、对比与 schema 派生工具
.claude/skills/  agent 端到端操作规程
```

本仓无外部目录依赖。早期快速原型仓 `../Novel_refactoring/`（含 163 本语料与 3 本书的
历史产物）已于 2026-08-16 删除，证据一律取 `outputs/` 下的批次产物；需要原始语料的工具与
测试改为显式指路（`NOVEL_DATA` / `NOVEL_CORPUS` / `NOVEL_OUTPUT`），指不到就明说「没查」。
