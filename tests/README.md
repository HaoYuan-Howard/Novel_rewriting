# 测试分层

四层职责与触发条件统一见 `AGENTS.md::测试选择与批次验收`。分层按测试回答的问题划分，
不要求移动现有文件或复制用例。`tests/suite_catalog.json` 显式登记模块层级、stage 归属和混合文件的
逐用例覆盖；`tests/selection.py` 将其接到 pytest。golden set 使用独立 `python -m tests.golden` 入口。

- 单元 / contract：用最小确定性输入验证函数、判据、schema 和接口约定；优先选择精确 node。
- stage：用小型合成输入或冻结的不可变上游 fixture，经该阶段真实入口验证职责、正式产物、
  判定、下游消费及拒绝 / 恢复。模型 transport 可替换为确定性回答，被测阶段的编排、校验与落盘
  必须真实执行；现役阶段为 stage0、stage1、stage2、stage4～stage7。仅文件名含 stage 不代表职责已覆盖。
- 回归：重跑受改动影响的既有单元、contract、stage 与跨模块场景，特别是历史故障反例。
  定向回归和完整非 corpus 回归分别报告，不另复制一套“回归用例”。
- golden set：在项目最终验收时，以根指南指定的 Workbench 源书生成最终小说，验证结果质量；
  工程检查与人工质量评价都要有证据。普通测试与 stage 测试不依赖 Workbench 或付费模型。

## 保留、合并与删除

测试按当前职责保留，不因文件老、名字含 legacy 或运行慢就保留或删除。清理时逐例核实：

| 情况 | 处理规则 |
|---|---|
| 现役行为、契约或消费者仍依赖 | 归入单元 / contract 或 stage；历史 bug 反例继续供回归复用 |
| 已退役流程的成功操作，现役契约与消费者都不再需要 | 删除；若仍承诺历史复现能力，先明确该能力是否退出维护 |
| 当前入口拒绝旧格式、旧授权或错误恢复 | 保留，它验证当前边界；不能和退役成功路径一并删除 |
| 两例输入、前置、故障点、判据和消费者均等价 | 合并或删除重复例；不同参数、层级、入口或提交窗口不算等价 |
| 没有 Python import 引用 | 继续查 CLI、API、动态注册、schema、运维和恢复入口；不能仅凭静态扫描删除 |
| fake 直接给出阶段成功，但仍能验证一侧约定 | 归单元 / contract；实际阶段职责由真实入口用例覆盖；完全没有有效断言的脚手架删除 |

删除同批清理仅为该用例服务的 fixture、数据、串行登记、目录表和文档引用，保留仍有消费者的 helper。
记录删除对象、原职责、现役依据及等价替代；前后用例数分开报告，不把清理后的较小分母写成旧集合全绿。
涉及支持范围或公开契约的变化先说明具体取舍；局部可证的冗余可直接清理。

### 分母变化登记（批次 C · 2026-09-15）

| 变更 | 原职责 | 现役依据 | 等价替代 / 分母影响 |
|---|---|---|---|
| `POST /stage2/analyze-draft` 人工写面 | HTTP 提交 owner 草稿订正 | 全自动裁决：用户修复写面退役 | 内部 CLI `--repair-analyze-draft` / `--recover-analyze-draft` 保留；`test_stage2_analyze_draft_http` 改断言 POST 405（读 GET 仍在） |
| enums.actions 隐藏 `--revalidate` 等 | Stage 页一键提升 INVALID | 队列以 `system:invalid-revalidate` 自动排入 | `test_orchestrator_queue` 的 live-INVALID 用例从 blocked 改为 todo 自动 revalidate；`Stage5Screen.request.test` 断言 actions 中无 `--revalidate` |
| Inbox `verification` → disclosure | 部分 verification 挡交付 | Q-13 质量类披露 | `test_inbox_projection` trim/verification 断言 `delivery_effect=disclosure`；交付终验只计 blocking |

### 分母变化登记（C5 · 2026-09-17 单 Executor 退役边界）

| 变更 | 原职责 | 现役依据 | 等价替代 / 分母影响 |
|---|---|---|---|
| `test_agent_drivers` / `test_agent_session_client` / `test_agent_runs` / `test_jsonl_runner` / `test_glm_drivers` / `test_verify_agent_e2e` 等（C3 已删） | Agent CLI driver / session / JSONL 子进程成功路径 | Agent CLI 硬切 | `tests/test_retired_formats.py` 负例 + `test_retired_surface_scan.py` 静态闸；ledger 永久 fixture `tests/fixtures/ledger/agent_era_ledger.jsonl` |
| `test_stage2_recovery_runs` 中 agent checkpoint 零线恢复组（C5 删） | node-executor/v1 + driver 形 session/ledger 闭环恢复 | 单 Executor；agent checkpoint 成功路径退出维护 | 同文件 legacy 零线用例保留；新增 `test_current_entry_refuses_agent_era_driver_profile_in_request_policy`；登记见本表 |
| `test_agent_session_audit` 改写 | 原 `AgentProfile`(driver) 会话账 | 现役 `LegacyLlmProfile` | 同文件职责保留：append-only / fold / provider session 互斥；profile 字段改为 provider/model/effort/thinking |
| 零付费恢复 | prepare / `--recover-analyze` 等 0 wire | C1 反例 + C5 invariant | `tests/test_zero_paid_call_invariants.py` 登记闸；禁止裸删表内用例 |

Stage3 文风迁移、Stage6 旧定性包 A/B 与 Stage2 旧五轮实验退出当前测试维护范围。
历史由 Git 和既存实验记录保存，不新增测试归档目录；旧解释器不能接受当前报告的守卫仍保留。
从旧实验中抽出的现役 Hook/Retry 夹具归 `tests/factories.py`，不再让当前测试依赖退役实验模块。

选测修订（2026-09-24）只校准目录登记：Stage0 状态反例与 Characters v3 用例名按现存代码同步，
不存在的旧 `cases` 键移除，未删除任何测试函数；现役 Characters producer／编辑用例纳入 stage 层。
补登记既存 `test_stage4_agent_boundary.py` 为 unit/contract，使无关范围可在导入前排除它；
该文件仍依赖已退役 `novel.agents`，完整收集的既存失败保持暴露，登记不证明旧流程重新成为现役。

后续新增测试须说明它保护的现役约定、失败机制或结果要求。bug 优先缩成最小可失败样例，
先让旧行为失败，再确认修复通过；若风险只在进程、并发、恢复或整链组合时出现，保留对应集成证据。
冻结 fixture 只替代未改动的上游准备，不能替代被测 producer、判据或真实消费者；schema 变化时同步重建。
高成本用例先根据 setup/call/teardown 实测定位成本，再复用准备、收窄输入或调整触发范围；
不能仅因慢或偶发失败删除。耗时和最近执行结果从报告产生，不手工登记“独有覆盖=true”或无依据的风险分数。
不硬限每层的用例数，也不为达到分钟目标缩小验收分母；日常反馈时间与完整回归时间分别衡量。

## 工程测试执行

单元 / contract 使用 `unit_contract`，阶段职责使用 `stage`；`regression` 复用二者，并包含独立的
跨阶段整书场景。`corpus` 保留原有数据依赖含义，普通选择默认排除；这些 marker 不代表模型质量。
新增测试模块必须登记目录表，未登记会拒绝收集；混合模块用 `cases` 指定例外层级。
模块涉及多个 stage 而单例范围更窄时，用 `pytest.mark.stage_scope('stage6')` 等显式收窄，
参数化用例也可在 `pytest.param` 上登记；声明不得超出模块的 stage 集合。

```powershell
# 小改：已知精确用例，或根据修改文件选择单元 / contract 消费链
conda run -n novel_refactor python -m pytest -n 0 --test-layer unit_contract tests/test_stage7_subs.py
conda run -n novel_refactor python -m pytest -n 0 --test-layer unit_contract --changed novel/stages/stage7/rename.py

# Stage4 重构：本层及传递下游的单元/contract，然后阶段职责测试
conda run -n novel_refactor python -m pytest -n 0 --test-layer unit_contract --test-stage stage4 --affected
conda run -n novel_refactor python -m pytest -n 0 --test-layer stage --test-stage stage4 --affected

# 先查看实际选择，保留精确 nodeid；不加 --affected 时只选指定 stage 的职责
conda run -n novel_refactor python -m pytest -n 0 --test-layer stage --test-stage stage6 --collect-only --selection-report .test_cache/stage6-selection.json

# 定向回归复用原用例，不另建回归副本
conda run -n novel_refactor python -m pytest -n 0 --test-layer regression --changed novel/stages/stage7/rename.py

# 隔离迁移：登记范围补充导入消费者；先查看实际收集，再去掉 --collect-only 执行
conda run -n novel_refactor python -m pytest -n 0 --changed novel/stages/str06/hook_context.py --collect-only --selection-report .test_cache/str06-selection.json

# 已提交的删除或目录登记：使用变更前的 commit；HEAD~1 仅适用于变更就在最近一次提交
conda run -n novel_refactor python -m pytest -n 0 --changed tests/suite_catalog.json --changed-base HEAD~1 --collect-only
```

`--test-stage` 和 `--changed` 可分别重复；二者不能组合，避免用交集漏掉真实消费者。
`--changed` 接受仓内相对或绝对文件路径，检查函数内、相对与测试 helper 导入的传递消费者，
并补上阶段依赖；多个修改路径取并集。特殊文件按以下证据选择：

| 修改 | 选择与兜底 |
|---|---|
| `docs/` 下 Markdown、各级 AGENTS／README | 文档引用守卫与可静态识别的读取消费者；schema 文档及 BACKEND_CONTRACT 还保留契约五文件。运行时读取的契约不能视为纯说明 |
| `tests/suite_catalog.json` 的普通模块登记 | 与基线比较，选择新增／变更条目及目录表守卫；阶段依赖、范围规则或版本变化仍扩大到完整工程集合 |
| 删除的 Python 文件 | 只读 Git 基线，将删除前与当前导入边合并，选择仍存在的消费者；基线缺失、不可分析或无可定位消费者时仍扩大 |
| 隔离迁移路径 | `change_scopes` 登记路径模式与测试模式，补充当前职责；始终并入范围外真实消费者，不截断现役依赖 |
| 共享执行配置、其它非 Python 或未知影响 | 保守扩大到完整工程集合并解释原因 |

`--changed-base REF` 默认 HEAD，只为删除项及目录登记提供比较基线，不自动推导修改文件列表。
已提交的变更须指定变更前的 commit；选择报告保留实际基线身份。不会切换 checkout、运行历史代码或恢复旧文件。
文档读取的静态补充识别完整路径／文件名字符串；动态拼接、外部配置等仍须人工核对真实消费者。
`change_scopes` 只增加覆盖，不豁免契约或最终验收；测试模式必须匹配已登记模块，迁移退役时同步删除登记。
例如 STR06 schema 经公共 paths 形成真实共享依赖时仍会扩大，不能因所属迁移包而截断。
修改 `contracts/` 仍纳入契约 §9.5 五文件，覆盖通过 AST/注册表而非直接 import 检查契约的消费者。
本工具是保守选择器，不能证明动态影响不存在；全量兜底不是独立验收门槛，最终仍按根指南决定验证范围。
根 `conftest.py` 提前注册插件，使 `--changed` 后的文档或源码参数在收集前被正确识别。
`--require-corpus` 不允许与新增范围过滤组合。

`--selection-report` 输出最后收集的精确 nodeid、层级、stage、corpus 标记、命令、代码起止身份、退出码、执行结果和各阶段耗时；
支持单进程及 xdist。`--collect-only` 只记录选择，不记为测试通过；零用例继续使用 pytest 非零退出码。
本机深 worktree 的上述命令追加 `--basetemp=E:/_tr`，不要把启动预检失败当测试结果。

默认测试必须可复现，不读取本地小说产物。开发依赖包含 pytest-xdist；默认命令以 4 个 worker
并行非 corpus 回归：

```powershell
conda run -n novel_refactor python -m pytest
```

`tests/conftest.py` 把普通测试按文件分到同一 worker，使模块 fixture、文档检查和 AST 缓存只
建立一次；登记在 `_SERIAL_TEST_MODULES` 的并发/进程生命周期模块进入同一个串行组。新增这类模块时
必须同步登记，AST 守卫会反查遗漏，不能靠本机一次碰巧不抖来判断可并行。

日常编辑先跑精确 node；单点用 `-n 0` 避免启动四个 worker。需要回放失败缓存时使用：

```powershell
conda run -n novel_refactor python -m pytest -n 0 -x tests/test_example.py::test_exact_node
conda run -n novel_refactor python -m pytest -n 0 --lf --lfnf=none -x
```

这些命令只能报告“定向测试通过”。只有不附测试路径和额外 marker、完整退出 0 的默认命令，才可称
“非 corpus 回归通过”。

测试选择和批次门槛统一见 `AGENTS.md::测试选择与批次验收`：局部修复先定向，允许多个检查点提交，
单个 stage 重构只验证该 stage 及实际受影响的消费链。完整回归在整项目验收、main／冻结发布或
共享基础设施影响无法定向闭合时统一执行；子批次交付、检查点提交与新增测试登记不自动触发。
同一契约批次形状稳定后先跑契约 §9.5 五文件，再补实际消费链。完整回归后生产代码又有变化，
旧结果只作基线，新增定向结果单列；说明文档、注释或测试记录变化只补相关校验。
测速和 profiler 仅用于已定位的热点，命令见
`docs/OPERATIONS.md::测试热点测量与准备复用`。

重复上游准备可由原 producer 生成模块级基线，再用 `tests/frozen_tree.py::FrozenTree` 保存不可变
字节，每例还原到自己的空 `tmp_path`。不共享可变 Store、模型对象、账或硬链接；不重用被测步骤的
结论。Stage4 B 测试复用 A/旧 B 前置盘面，Stage5～Stage7 职责测试复用逐层冻结的合成上游；故障注入、真实生产、HTTP/审批、
并发与最终字节断言仍逐例运行。并发生命周期的 serial 登记和真实代码身份校验保持原约束。

`tests/test_delivery_modes.py` 将当前工程文件复制并提交到独立临时 Git 根，从空输出执行
`tests/delivery_scenario.py`：人工上传与逐层审批、免费 CLI、自动 Supervisor 收尾、HTTP 下载和
服务重启均用真实入口。模型回答由合成 transport/driver 提供；自动收尾只准免费组装，任何额外
动作或模型调用都失败。这验证两种模式共用交付证明及无需 Markdown 报告，不证明模型质量或真实费用。
回答/决定/prepared 的中断恢复仍由所属恢复测试保留反例，不用成功场景替代。

阶段职责的证据定位如下；这里只索引现有用例，不复制断言。目录表同时区分同一文件内的纯契约与实际阶段用例。

| Stage | 生成、拒绝与恢复的主要证据 | 真实消费者 |
|---|---|---|
| stage0 | `tests/test_stage01.py` 的摄入、源漂移拒绝和人工修订保留 | `tests/test_api_stage01.py` 的批准与正式分章 |
| stage1 | `tests/test_stage01.py` 的分章、空章拒绝、章号与重新分章 | `tests/test_api_stage01.py` 的连续正文与诊断读面 |
| stage2 | `tests/test_stage2_characters.py`、`tests/test_stage2_knowledge_flow.py`、`tests/test_stage2_generation.py`、`tests/test_plot_producer.py` 的独立生成与恢复；`tests/test_plot_edit.py` / `tests/test_plot_repair.py` 的编辑与返修；`tests/test_blueprint_repair_queue.py` 的统一机械准入阻断与免费恢复 | `tests/test_stage2_node_lifecycle.py`、`tests/test_stage2_uploaded_flow.py` 的正式提交与下游释放，`tests/test_plot_edit_http.py` 的 CLI 与服务重启；Stage2 语义审阅已退役，语义未审以报告 unver 行与交付披露承接 |
| stage4 | `tests/test_stage4_v3_integration.py` 的 A/B/Context、错误证据拒绝与免费 binding 修订 | 同模块的正式读取、HTTP 与重启 |
| stage5 | `tests/test_stage_responsibilities.py` 的正式逐章生成、提交尾窗的第三方字节拒绝与免费恢复 | 正式 Chapter 读回与 Review；替换判据的 `tests/test_stage5_generation.py` 保留在单元 / contract 层 |
| stage6 | `tests/test_stage_responsibilities.py` 的 Hook、双判官、定稿及证据缺失 / 恢复 | 真实 Stage6 页面读面；此前仅 corpus 覆盖的成功路径已加入默认测试 |
| stage7 | `tests/test_stage_responsibilities.py` 的改名、定稿、组装、上游篡改拒绝和原字节恢复 | 正式下载读取与 golden set 收集器 |

`tests/test_test_selection.py` 检查目录表覆盖、混合分类、传递消费与实际 pytest 入口；
`tests/test_stage_responsibilities.py` 的上游由正式 producer 建立，再以 `FrozenTree` 逐例还原，
被测 stage 重新执行并读取真实结果。选择较早的 stage 时，准备不会为了后续阶段测试继续生成整书。

## Golden set 与 corpus

golden set 的源目录见 `AGENTS.md::测试选择与批次验收`，按所用工具的既有约定显式设置
`NOVEL_DATA` / `NOVEL_CORPUS`；`NOVEL_OUTPUT` 指向独立生成目录。源目录缺失或不可读时报告“没查”，
不得临时换书、拿旧输出冒充本轮结果或把跳过当通过。

验收前冻结代表性书目、源版本、质量判据及对照基线，记录候选代码和模型配置；已有冻结书目不得
为通过验收临时删减。经真实工作流生成 `stage7/<书名>_rewriting.txt` 后，逐书对照源书与已验收基线，
检查情节骨架、事实与因果、披露顺序、人物与改名一致性，以及行文流畅、风格和整体阅读体验。
首次验收尚无合格成书基线时，明确记录基线缺失，依据源书与预定质量判据验收后再冻结基线。
不要求自由生成的小说与旧稿逐字相等，也不把一次模型评分当作质量通过；人工验收须记录具体章节证据、
问题及结论，未评项保持未验证。付费、正文外发与追加额度沿用根指南的授权边界。

工具执行顺序如下。验收文件放在源根与生成根之外；工具拒绝覆盖已有冻结文件，修订时使用明确的新文件名。
`profiles.json` 使用现役 `PipelineAgentProfiles` 形状，由本轮授权的 Commander / Executor 配置提供，
不含密钥；示例的路径需要替换为实际独立目录。

```powershell
$env:NOVEL_DATA = 'E:\practice_group\Novel_rewriting\Test_data\Workbench'
$env:NOVEL_OUTPUT = 'E:\golden-output'
conda run -n novel_refactor python -m tests.golden inventory
conda run -n novel_refactor python -m tests.golden freeze --profiles E:/golden-review/profiles.json --manifest E:/golden-review/manifest.json

# 仅在付费和正文外发分别获得授权后执行；要求代码已提交且与 freeze 时一致
conda run -n novel_refactor python -m tests.golden run --manifest E:/golden-review/manifest.json --yes --egress-authorized

# 以下收集与质量复核不调用模型
conda run -n novel_refactor python -m tests.golden collect --manifest E:/golden-review/manifest.json --snapshot E:/golden-review/snapshot.json --review-template E:/golden-review/review.json
conda run -n novel_refactor python -m tests.golden evaluate --manifest E:/golden-review/manifest.json --snapshot E:/golden-review/snapshot.json --review E:/golden-review/review.json --report E:/golden-review/result.json
```

`inventory` / `freeze` 覆盖源根内全部 txt，记录原始 SHA-256 与生产解码器的归一化身份；不临时挑书缩分母。
本机同时存在 `NOVEL_CORPUS` 时须与 `NOVEL_DATA` 指向同一源根。`freeze` 可带
`--baseline <上次通过的 result.json>`；基线须匹配源版本，复核时还检查旧成书字节。
源目录只是样本来源，不自动证明样本具有足够代表性；扩充书目须显式建立新的冻结批次。

`run` 只接受空输出根：经正式 Stage0 CLI 摄入后交给 RunSupervisor，不自建阶段循环或自动填写审批。
运行阻塞时保留原账；遇暂停或 Ctrl+C，向本次 owner 请求停止并等待 drain。已产生输出的运行不能重用
`run` 再起一轮；按产品的人工 / 恢复入口处理，保持冻结代码与配置，再免费 `collect`。
收集检查原文身份、七层完成度、正式交付、稳定盘面，以及已结束运行的代码和 profiles；未观察到运行身份不会补绿。

先完成 `collect`，再由人工填写 `review.json` 的 reviewer、带时区的 reviewed_at，以及逐书六个维度的
state、chapter_ordinals 和 evidence。证据写定位与判断说明，不复制小说正文；每个通过/失败都需要具体章号。
有基线时还须填写 baseline_comparison 与 baseline_evidence。首次无基线明确保留 first_acceptance。
`evaluate` 重新读取实际盘面；缺维度、漏书、未知章号、旧候选或审批变化均拒绝。未评保持 `unver`，
工程通过与质量通过分别报告；只有两者通过才能返回最终 pass。退出码 0 为该命令完成，1 为验收未通过或运行阻塞，
2 为配置/身份拒绝；`run` 返回 generated 与 `collect` 返回工程 pass 都不表示小说质量已通过。

`tests/test_golden_set.py` 验证工具自己的授权、完整分母、陈旧证据和质量状态；它属于单元 / contract，
不能代替 Workbench 的真实生成与人工阅读。原 current-schema corpus 书目、manifest 与付费证据判据继续独立保留。

标记为 `corpus` 的测试是针对冻结生成产物或源语料的集成检查，默认 marker 表达式会跳过它们。
`corpus` 是现有数据依赖标记，与四层职责并非一一对应；它提供工程证据，不能替代上述成书质量评价。
最终硬验收必须同时提供隔离的当前 schema 输出盘和与它精确对应、已提交的无正文 manifest，
并使用 `--require-corpus`：

```powershell
$env:NOVEL_OUTPUT = 'E:\path\to\isolated-current-schema-output'
$env:NOVEL_CORPUS_MANIFEST = (Resolve-Path -LiteralPath 'docs\corpus\current_schema_manifest.json').Path
conda run -n novel_refactor python -m pytest -n 0 --require-corpus
```

只跑 `-m corpus` 不是最终验收：它不验证冻结 manifest/ledger/精确 skip allowlist。当前
manifest 尚未生成时，只能报告"非 corpus 回归"，不能声称全量已绿。manifest 的字段契约、
冻结书目与硬验收判据见 `docs/schema/corpus.md`。

## Fixture 与运行隔离

纯判据与 prompt 测试应使用 `tests/factories.py` 中的确定性构造器，不得随机选择一本本地
小说。测试若直接写注册 JSON 产物，须先用 `contracts.schema.encode_artifact()` 包装逻辑
payload。

普通测试由 autouse fixture 注入确定性的 `code_version`，避免每次构造来源账都启动三次 Git；专门验证
真实代码身份的模块声明 `pytest.mark.real_code_version`，corpus 验收也始终查询真实值。生产
`novel.prov.code_version()` 不得加进程级缓存，否则长驻服务会把旧代码身份写进新产物。

Windows 上的 `--basetemp` 有两条出路，选一条即可：

- **缩短路径**：仓库默认 `.tr` 是固定短根，并为 xdist 的 `popen-gwN` 子目录预留预算。测试启动会按
  Stage0 最长内容寻址归档路径预检，超出 `MAX_PATH` 时给出 `pytest.UsageError`。
- **一次性解除限制**（推荐，管理员一次）：设
  `HKLM\SYSTEM\CurrentControlSet\Control\FileSystem\LongPathsEnabled=1`。本仓 conda 环境的
  `python.exe` 带 `longPathAware` manifest，两者齐备后普通路径不再受 260 限制，
  `tests/conftest.py::_windows_basetemp_problem` 会**实测**到这一点并自动不再拦——它探测文件系统
  本身，不读注册表也不查 manifest（那是两个各自都会变的间接条件）。git 另需
  `git config --global core.longpaths true`，它不吃系统开关。

⚠ 不要改用 `\\?\` 前缀绕过：前缀总能写成功，但前缀建出来的文件**用普通路径读不回来**
（实测 `FileNotFoundError`），那是把明确报错换成「一部分代码看得见、一部分看不见」的静默不一致。

会话 fixture 会把遗留的无 `dir=` 标准库临时调用统一重定向到本次 worker 的 basetemp 子根，并在退出
时自动清理；新增测试直接使用 `tmp_path` / `tmp_path_factory`，不要继续新增 `tempfile.mkdtemp()`，也
不要在 `%TEMP%` 或按时间戳另造根。正常退出只恢复本次 worker 实际触碰根与 controller cache 的 ACL，
不会递归扫描未参与本轮的陈旧 basetemp。
