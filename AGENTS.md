# AGENTS.md

This file provides guidance to Claude Code (claude.ai/code) and other coding agents when working
with code in this repository. **`CLAUDE.md` 是指向本文件的链接——要改在这里改，不要改那一份。**

（本仓所有文档、注释、commit 都是中文，回复与新增文档保持中文。本文件与
`docs/BACKEND_CONTRACT.md` 冲突时，以后者为准：它是冻结契约，本文件是入口摘要。）

## 这个系统是什么

把一本中文网络小说经 stage0～stage7 处理成**一本新书**：情节骨架保留、文字全部重写、
人名全部替换，交付 `stage7/book.txt`。产品边界、处理链与成本见 `README.md`。

**最高风险不是「出不了书」，是「错误的剧情被静默接受」**——模型的错误不会崩溃，它会造一个
读起来毫无破绽的假事实。仓里绝大多数看起来"过度设计"的机制（三态建模、单一落点、先校验后落盘、
守卫的守卫）都是为这一条服务的，改动前先理解它想防什么。

## 常用命令

命令细节的唯一落点：后端运行与验证见 `README.md`「运行与验证」，前端命令见
`web/DELIVERY.md` 开头。agent 高频提醒两条：

- 后端一律用 conda 环境 `novel_refactor`（**不是 Miniconda base**，base 里连 pydantic 都没有）。
- `docs/BACKEND_CONTRACT.md` §9.5 的最小验证命令直接写死解释器路径
  `D:\Miniconda\envs\novel_refactor\python.exe`，改判据/schema/节点/API 时先跑那五个文件。

跑 pytest 的三个坑：

- **必须绕开 Claude Code 沙箱**（`dangerouslyDisableSandbox`）。沙箱会把 `.test_runtime` 的 ACL
  写坏，且非管理员修不回来。救急可改 `--basetemp`，但**必须是短路径**——`tests/conftest.py`
  按 Stage0 最长归档路径预检 `MAX_PATH`，深目录会给出 `pytest.UsageError` 这种假红。
- **汇总行到不了工具输出**，要核 passed/skipped 就数进度点，别指望抓 `N passed`。
- **语料用例有两道闸**：`addopts` 默认 `-m "not corpus"` 会整批 deselect；即使加 `-m corpus`，
  不设 `NOVEL_OUTPUT` 还会整批 skip。最终硬验收是
  `--require-corpus` + `NOVEL_OUTPUT` + `NOVEL_CORPUS_MANIFEST`（缺书、缺产物、版本漂移、
  ledger 不闭合、新增 skip 一律关闭式失败）。当前 manifest 尚未生成，所以**任何通过数字只能
  称「非 corpus 回归」，不能称「全量绿」**（`docs/REQUIREMENTS.md` TODO-21）。

**跑书不要手拼命令**：完整操作规程是 `.claude/skills/novel-pipeline-operator/SKILL.md`
（`novel-pipeline-operator` skill）。CLI 子命令：`run` / `preview` / `state` / `inbox` /
`queue` / `queue-list` / `queue-cancel` / `queue-resume` / `jobs` / `reconcile` / `mark`。
`preview` 是 0 成本的、且它给出的 argv 才是真跑那一条。

## 架构要点

### 两根正交的轴：`stage` 与 `node`

`stage0`~`stage7` **只做产物层**（目录 / schema / `Rule.stage` / 校验报告路由），永不用作作业 id。
作业 id 是 `<stage>.<动词>`（`stage2.global`、`stage5.write`、`stage7.assemble` …），
唯一落点 `contracts/invariants.py::NODES`，label 从 NODES 派生不手写。两者**不是 1:1**：
stage2 两个节点、stage5 写作与验收两相、stage0 三个人参与的动作。

三条容易踩的推论：**没有 `check` 节点**（复检一律是 `--check-only` flag，作业 kind 是
`(node, flags)` 二元组）；**写作两条臂是参数不是节点**（`stage5.write --deep 1,2,3 --shallow-rest`）；
**正向覆盖率不许写成 `'R-' + stage.removeprefix('stage')`**，`STAGE_SECTIONS` 从 `NODES` 派生。

章的唯一键恒是阅读序整数 `chapter_ordinal`，**输出章号恒等于源章号**（不合并、不重排、不删、不拆）。

### 分层，由测试守而不是靠约定

```
contracts.artifacts   路径叶子，只 import typing
  ↑ contracts.invariants   取值域叶子，不知道 pydantic 的存在
  ↑ contracts.rules / contracts.schema.*   契约层，可单独 import（base 依赖只有 pydantic）
  ↑ novel.*   运行时，单向依赖 contracts
```

单一落点清单（改这些之前先读对应模块头的注释，那里写着"为什么不能用更直观的写法"）：

| 只有一份的东西 | 落点 | 守卫 |
|---|---|---|
| 产物读写（`json.load` 只许出现在这里） | `novel/store.py` | `tests/test_layering.py` |
| 路径运算与书名↔目录名双向尺子 | `novel/paths.py` | 同上 |
| 一次跑的来源账（**实例字段，不是进程全局**） | `novel/run.py::RunContext` | 同上 |
| 退出码 0/1/2/3/70 与 `landed()` | `novel/cli/exit_codes.py` | 同上 |
| 节点、flag、取值域、`STOP_REWRITE_AFTER` | `contracts/invariants.py` | `tests/test_invariants.py` |
| 修复路由 | `novel/report/builder.py::remediation_of` | `tests/test_rules_registry.py` |
| 八类待办清单 | `novel/views/inbox.py::inbox_view` | `tests/test_views_inbox.py` |
| 前端唯一 fetch 缝 / 唯一状态色出口 | `web/src/api/endpoints.ts` / `src/constants/vocabulary.ts` | `npm run seams` |

`tests/test_layering.py` 与 `tests/test_api_discipline.py` 机械地守着：contracts 不 import 运行时、
orchestrator 不 import web 栈、API/view 不直写产物、**服务进程不能花钱**、API 不造第二个 argv
builder、完成度不偷看作业记录、loopback、无 CORS、同书 409。绕过这些不会"省事"，只会红。

### 状态必须分开建模

「不知道」「没运行」「缺失」「空结果」「没问题」是五件事，不许用 `false`/`null`/空数组压成一个值。
退出码同理：`1`（跑完了但判定不通过，**已落盘**）与 `2`（显式终态，没落盘）与 `70`（崩了，结论未知）
必须分开；`landed()` 是唯一的"这次落没落盘"谓词。`unver` / `skipped` / 分母为零**不得洗成 `ok`**，
绿灯必须带扫描分母和 freshness。

### 钱与授权

`NodeSpec.paid` 决定基线；`FREE_ENTRIES`（把付费节点变 0 成本的 flag）、`PAID_FLAGS`（反向）、
`VALUED_FLAGS`（带值的参数，**不是入口**）三张表在 `contracts/invariants.py`，`is_paid` 只有一个。
付费跑法必须 `--yes`。成本的唯一权威源是 `<base>/_service/ledger.jsonl`，不与产物 `usage` 或日志混加；
缓存命中（`.llm_cache/`）单独记账，不能写成"没有执行过"。
**付费授权与正文外发授权是两件独立的事**，任一缺失时仍可跑 0 成本的 `state`/`inbox`/`preview`。

### 修复路由与恢复路

校验结论按 `remediation` 分四类：`deterministic`（代码修，一次免费命令）、`llm`（只接受
`repair == UNIQUE` 且 `unresolved_rounds < 2`）、`human`（typed apply-edit / pick / hash-bound
resolution / wontfix 收口）、`none`（metric 只报读数）。处理顺序：首轮生成 → 机械检查 →
deterministic 先修 → UNIQUE 才可付费修 → 连续两轮未消转人 → 全规则回归 → 机械干净且 fresh
后再跑语义审判。

**加落盘闸门必须同批提供 0 成本恢复路**（`--revalidate` 或人工编辑入口），否则一次有效付费产出会被
`.INVALID` 永久锁死。人工写回与生成路径共享同一把尺子：输入来源不同，schema、归一、判据、归档、
provenance 的口相同。

### 产物、schema 与目录

盘上布局是 `<base>/<平台_书名_sid>/stage0..stage7/`，服务自己的账在 `<base>/_service/`
（`ledger.jsonl` / `agent_progress.jsonl`），终审包是 `<平台_书名_sid>/<书名>_review.md`
（落书目录根、不是产物，落点唯一落在 `novel/paths.py::review_package_path`）。

路径字面只登记在 `contracts/artifacts.py::ARTIFACT_PATHS`；`contracts/schema/__init__.py::ARTIFACT_SPECS`
为 JSON 子集叠加当前模型与 schema version。产物带 `_schema_version` 信封，**只接受注册表声明的当前
版本**，无标记/低版本/高版本都明确拒读——**本仓没有迁移机制**，现役全表恒 v1（仍以注册表为准，
破坏形状就 `current_version` +1 并同批回答「存量产物由谁重建」）；
旧产物**不手补字段冒充当前产地**，要从 producer 重建。
`novel/` 里加一层包，`pyproject.toml` 的 `[tool.setuptools] packages` 要同步加一行——漏了没有任何测试
会出声（`pythonpath=["."]` 让 pytest 照样 import 得到），只有非 editable 安装才崩。

产物 schema 的人读源在 `docs/schema/*.md`，每个字段带「证据」列（`三源` / `盘·码` / `契·码` / `⚠冲突`）。
**「盘上 3/3」不等于必填**，盘上零样本的字段要回代码或全语料确认；有人工修订路的层，`_rounds/` 归档
必须一起反推，否则会把只在修之前出现的取值定成零样本分支。

### 前端

界面规格的唯一落点是 `web/DELIVERY.md`（三道缝、路由表、U-01～U-20、seam 红线）。agent 最常踩的三条：

- 屏与组件只经 hooks → `api.*`，不直接 fetch、不 import mock；枚举运行时从 `/api/enums` 取，不硬编码。
- **前端只呈现判定，不补后端没给的结论**：服务端没给结论时显示原始读数或 unknown。
- **服务进程只写 `_service/`，产物变更一律经 CLI 子进程**（`R-11.3`）——`stage6/edit` 与
  `stage7/edit` 因此只走 CLI，浏览器只提供批准入口。

### 文档是可执行的

`contracts/requirements.py` **直接解析** `docs/BACKEND_CONTRACT.md`：R 节须以 `### R-N` 开头、
R 条目须是至少三列的 `| R-N.m | 要求 | 谁在查 |`、Q 须 `### Q-N`、B 须 `| **B-NN** | ... |`。
把表格改成更自然的散文会让覆盖率静默归零。`tests/test_docs_references.py` 扫本文档、`LESSONS.md`、
`REQUIREMENTS.md`、README 与 `docs/schema/*.md`：反引号里的 R/Q/B 编号必须存在，以
tests/contracts/tools/novel/docs 开头的仓内路径必须存在。文档引用用稳定编号或`仓内路径::符号`，不用行号。

## 编码风格

全仓没有配置 formatter，靠 typecheck、seam 守卫和"与相邻代码一致"约束。

- **Python**：4 空格、`snake_case` 模块与函数、`PascalCase` 类；保留类型标注；不要破坏
  store / orchestration 既有的落盘边界。模块头那段中文注释是"为什么不能用更直观的写法"的
  证据，改动前先读，别当装饰删掉。
- **TypeScript**：2 空格、单引号、无分号、`PascalCase` 组件、`useXxx` hooks。屏与组件只消费
  hooks / API 适配器，**不直接 `fetch`、不 import mock**。

## 工作纪律

- **`docs/REQUIREMENTS.md` 是唯一活动待办**，只从仍存在的条目开工，验收通过即删条目；
  已完成的历史归 Git 与 `docs/BACKEND_CONTRACT.md` §十。文档职责分工见 README 的表。
- 动手前读 `docs/LESSONS.md`（21 条可迁移经验），尤其第 21 条：**守卫也会假红**。红了先分清是产物
  问题还是守卫自己漂了——典型形状是「守卫重拼了生产的实参」和「守卫断言了一道产品明说不存在的闸」
  （`info` 判据的正确守法是"越线必出声"而不是"不许越线"）。守卫要**调用生产那一个落点**，不要重拼一份。
- 不能用 mock、空数组、普通 404 或本地存储伪装尚未提供的后端能力；类型同型只证明适配器对齐，
  联调要走通「页面动作 → API 方法 → HTTP 路由 → 服务实现 → 最终产物」并检查重启后的状态。
- 语义判据在没有标定过真值前，倾向 `unver` / 人工裁决；**没有 ground truth 时多一条语义判据可能更危险**。
  本仓因此**不设**自动放行门：`stage6.semantic` 的双盲判官只产**带证据的 UNVER**，
  收口一律走 `stage6.approve` 的 `actor + reason + 完整 subject SHA-256`。
  不要为了「让它变绿」去恢复 evaluator profile 那套装置——它已整批下线，
  连同 stage5 的收缩臂（超上限章现在零调用转人工）。
- commit 用中文 Conventional Commit 主题（`feat(stage6): …` / `fix(cli): …` / `docs: …`），按 stage
  或子系统收敛。PR 要说明行为与契约影响、列出跑过的验证命令、引用 R/Q/B 或 TODO 编号、带 UI 截图；
  **付费或真实 LLM 路径的验证必须显式点名**，不能混在"测试已过"里。
- 不提交 `.env`、`outputs/`、`Test_data/`、`.llm_cache/`、`.test_runtime/` 与任何正文语料。
  证据一律取 `outputs/` 下的批次产物；需要原始语料的工具必须显式指路
  （`NOVEL_DATA` / `NOVEL_CORPUS` / `NOVEL_OUTPUT`），指不到就明说"没查"。
