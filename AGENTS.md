# AGENTS.md

本文件是 coding agent 的仓库入口；`CLAUDE.md` 是指向它的链接，只改本文件。仓内文档、注释、
commit 与对用户的工程回复统一使用中文。

## 项目与权威顺序

本系统把一部中文网络小说经 stage0、stage1、stage2、stage4～stage7 七个现役产物层处理成一本
新书：保留情节骨架，全文重写，替换人名，最终交付 `stage7/<书名>_rewriting.txt`。

最高风险不是程序崩溃，而是错误剧情被静默接受。实现与评审都优先保护证据关系、未知状态和人工
裁决边界，不为“全绿”伪造结论。

发生冲突时按以下顺序判断：

1. `docs/BACKEND_CONTRACT.md`：冻结 R/Q/B 契约、守卫归属与修复路由。
2. `contracts/` 与 `docs/schema/`：现役取值域、schema、路径和 API 形状。
3. `README.md` 与 `web/DELIVERY.md`：产品边界、人类任务与界面规格。
4. `docs/REQUIREMENTS.md`：唯一活动待办；未受用户点名的新工程只从仍存在的条目开工。
5. `docs/DECISIONS.md`：历史裁决和实测证据，只解释缘由，不覆盖现役契约。

## 任务路由

### 工程修改

1. 先看 `git status --short`，保留工作区已有改动；冲突无法绕开时再请用户决定。
2. 阅读相关代码、模块头、契约与测试，不把现状自动当成正确设计。
3. 按 `docs/LESSONS.md` 开头的任务索引，只读与本次改动相关的经验条目；不再要求每项工程全文通读。
4. 修改完整消费链：契约、producer、consumer、恢复路、测试和文档属于同一行为时必须同批闭合。
5. 验证真实消费者与最终盘面；单函数存在、能 import 或单测通过都不等于完成。

### 纯运行任务

跑书、断点续跑、`state` / `inbox`、流水线恢复和 agent 代批只遵循
`.workflow/skills/novel-pipeline-operator/SKILL.md` 及其按需 references，不读工程经验全集，也不手拼
生产命令。只有故障转为代码诊断或修复时，才切回工程修改流程。受托 Commander/Reviewer 轮次
（提示内含 `<immutable-workpack>`）例外：只读随提示下发的裁决契约与材料，不加载 skill、不读工程
文档、不读盘上产物。

## 不可破坏的系统不变量

- `stage` 是产物层，`node` 是作业 id；节点唯一登记在 `contracts/invariants.py::NODES`。Stage3 已退役，
  复检用 `--check-only` flag，不造 `check` 节点。章唯一键是阅读序整数 `chapter_ordinal`，输出章号恒等
  于源章号，不合并、不删、不拆、不重排。
- 依赖只能单向流动：`contracts.artifacts → contracts.invariants → contracts.rules/schema → novel.*`。
  产物读写只经 `novel/store.py`，路径只经 `novel/paths.py`，服务层与前端不能另造第二套判定或 argv。
- “不知道、没运行、缺失、空结果、没问题”必须分开。退出码 1（已落盘但不通过）、2（显式终态未落盘）、
  70（崩溃、结论未知）不得互相洗白；是否落盘只调用 `novel/cli/exit_codes.py::landed`。
- artifact 只接受 `contracts/schema/__init__.py::ARTIFACT_SPECS` 登记的当前版本。本仓没有通用迁移机制；
  破坏形状时升版，并同批明确存量由哪个 producer 重建和需要什么授权，禁止手补旧文件冒充新产地。
- 付费授权与正文外发授权正交。付费命令必须显式 `--yes`；成本只认 `<base>/_service/ledger.jsonl`，
  缓存命中单列，不能写成“没有执行”。服务进程本身不能发付费模型请求。
- 校验修复只走登记的 `deterministic / llm / human / none` 路由。新增落盘硬闸时必须同批提供 0 成本
  `--revalidate`、人工编辑或等价恢复面；生成、人工写回与提升共享 schema、归一、判据、归档和 provenance。
- 服务进程只写 `_service/`；产物变化经 CLI 子进程。前端只呈现后端判定，unknown 保持 unknown，屏与组件
  只经 hooks → `api.*`，不直接 fetch、不 import mock、不硬编码后端枚举。
- 未经标定的语义判断保持 `unver` 或人工裁决。Stage6 双盲 judge 只产带证据的 UNVER；收口走
  `stage6.approve` 的 typed 人工决定，不复活已退役的 evaluator profile 或 Stage5 收缩臂。

## 运行与验证入口

- 后端固定使用 conda 环境 `novel_refactor`，不要用 base。启动、测试、网络诊断、备份与恢复命令统一见
  `docs/OPERATIONS.md`；前端开发命令见 `web/DELIVERY.md`。
- pytest 默认 `-m "not corpus"`，且当前 current-schema manifest 尚未完成。只有按 `tests/README.md` 使用
  `--require-corpus` 并闭合精确 manifest/ledger/skip 面，才可称“全量语料绿”；其余只能称非 corpus 回归。
- 默认非 corpus 回归使用 pytest-xdist `-n 4 --dist loadgroup`；`tests/conftest.py` 保证普通测试按文件分组，
  含线程、跨进程或取消生命周期断言的模块必须登记到 `_SERIAL_TEST_MODULES`。不得为追求速度拆散模块
  fixture 或移除 serial 登记；corpus 硬验收必须显式 `-n 0 --require-corpus`。
- 日常反馈跑精确文件/node，单点用 `-n 0 -x` 免去 worker 启动；`--lf --lfnf=none -x` 只回放已失败项。
  这两类结果只能称“定向测试”，提交前仍跑不带路径/额外 marker 的默认非 corpus 回归。
- agent 跑 pytest 必须绕开文件系统沙箱，复用 `.tr` / `.test_cache`；路径过长时固定复用 `E:\_tr`，
  不按测试或时间另造目录。遗留 `tempfile.mkdtemp()` 由会话 fixture 收进本次 basetemp 并自动清理；新增测试
  一律用 `tmp_path` / `tmp_path_factory`，不得回到 `%TEMP%`。ACL 只恢复本次触碰根；异常强杀按
  `docs/OPERATIONS.md` 一次性恢复。
- 普通测试由 autouse fixture 使用确定性 `code_version`；只有 `real_code_version` marker 与 corpus 测试查询
  真实 Git。禁止为了测试速度缓存生产 `novel.prov.code_version()`，长驻进程必须每次看到当前代码身份。
- 修改 R/Q/B、schema、节点或 API 时先跑 `docs/BACKEND_CONTRACT.md` §9.5 的五文件，再补受影响测试；
  不从汇总行缺失推断测试结果，必要时用 JUnit 或进度点核对。

## 文档与代码纪律

- `contracts/requirements.py` 直接解析 `docs/BACKEND_CONTRACT.md`。R 标题、三列表格、Q/B 形状和“谁在查”
  记号都是接口；文档引用用稳定编号或 `仓内路径::符号`，不用行号。
- 新增 `novel/` 子包时同步更新 `pyproject.toml` 的 `[tool.setuptools] packages`；pytest 的 `pythonpath=["."]`
  不会替非 editable 安装发现这个遗漏。
- Python 保留类型标注、4 空格、snake_case/PascalCase；TypeScript 2 空格、单引号、无分号、PascalCase
  组件与 `useXxx` hooks。沿用相邻代码，不删除解释“为什么不能用更直观写法”的模块头注释。
- 不用 mock、空数组、普通 404 或 localStorage 伪装尚未提供的后端能力。联调覆盖页面动作 → API → HTTP
  路由 → 服务 → 最终产物，并检查重启后的状态。
- commit 使用中文 Conventional Commit；PR 说明契约影响、验证命令和 R/Q/B/TODO，UI 改动带截图；真实
  LLM/付费路径是否验证必须单列，不能混进“测试已过”。
- 不提交 `.env`、`outputs/`、`Test_data/`、`.llm_cache/`、`.tr/`、`.test_runtime/` 或正文语料。需要语料的工具必须
  显式读取 `NOVEL_DATA` / `NOVEL_CORPUS` / `NOVEL_OUTPUT`，取不到就明确写“没查”。
