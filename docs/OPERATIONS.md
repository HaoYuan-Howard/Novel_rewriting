# 工程运维手册

本文供开发维护 agent 和维护者查环境、部署、测试、诊断与工程恢复。它不被 Executor 或受托
Commander 加载，也不参与确定性调度。正常使用通过前端自动跑书或人工各 Stage 页面完成；
页面应提供阻塞原因、材料与可用动作，不要求用户先读本手册。产品语义见
`docs/BACKEND_CONTRACT.md`，前端开发见 `web/AGENTS.md`。

## 启动审校台

Windows 下双击 `scripts\start.cmd`。它按需构建前端、启动服务并打开浏览器；界面与 API 同源，默认
地址是 `http://127.0.0.1:8765/`，Ctrl+C 一起停止。

再次启动会复用已经监听 8765 的本审校台，不会自动加载后端改动。改过 Python 或遇到旧服务残留时：

```powershell
scripts\start.cmd -Restart
```

也可双击 `scripts\restart.cmd`。重启入口只在端口确认为本项目，且没有运行/暂停中的 Agent、待启动
Agent 或运行中的 API 作业时停止旧进程；否则关闭式拒绝。

常用参数：

- `-Base <目录>`：指定包含各书目录的书库根；省略时使用仓根 `outputs/`。
- `-Dev`：使用 Vite 热更新。

## 后端命令

后端固定使用 conda 环境 `novel_refactor`，不要用 Miniconda base。

```powershell
conda run -n novel_refactor python -m novel.api --base <Output目录>
conda run -n novel_refactor python -X utf8 -u -m novel.cli state <sid> --base <Output目录>
conda run -n novel_refactor python -X utf8 -u -m novel.cli inbox <sid> --base <Output目录>
conda run -n novel_refactor python -X utf8 -u -m novel.cli operator next <sid> --base <Output目录> --json
```

`operator next` 在同一份冻结快照上返回 `ready / blocked / complete`。`ready` 已携带 canonical runner
需要的 exact execution token；不得重新扫描全链或凭聊天记忆推演下一步。`operator permit` 只保留给
blocked recovery 和诊断兼容，blocked action hash 本身不是恢复许可。

## 新旧工作流的执行边界

当前 `pipeline-cost-latency-v2` 只用于独立空根；manifest v1 / 工作单元 ledger v1 不迁移。首次合法写入前固定
`_service/workflow.json`；其后所有节点、直接 producer、审批和恢复共用该身份。
不得删除 manifest、工作单元账或原审批来重新初始化；旧 base 的读取与诊断不授予新写入权限。

本分支改造前的冻结基线为 `6473b78053ddb66eb111e835e41eb8d1f120eec7`。存量中断任务先核对原
run 审计中的代码身份，再在匹配的旧代码工作区使用该版本的恢复入口；不能仅因主分支名相同就认为版本匹配。
对于确认属于上述基线的旧任务，可建立独立、固定提交的恢复工作区：

```powershell
git worktree add --detach .worktrees/legacy-workflow-6473b78 6473b78053ddb66eb111e835e41eb8d1f120eec7
```

在该工作区读取对应版本的运维入口，沿用原 base、账本与审批恢复。
如果原任务属于其他代码身份，先恢复对应的原版本；代码身份缺失或脏工作区内容无法复原时保持停点。
不得用当前版本替旧 base 补造新预算、两份 Stage2 接受证明或当前版本产物。

token/耗时优化前的工程基线是 `24e4cf42c76309adaf1f4ee4df0dca3cdbea4d37`；采用
`workflow-role-boundaries-v1` 的根仍与其真实原代码身份配对恢复。人工追加在运行页工作单元
详情预览后提交，默认一次、单次一至四次；它不清账，也不代替付费与正文外发授权。
“按原配置继续”复用历史完整 profiles，缺配置或原 owner 未排空则明确拒绝。完整 schema 与
停止、幂等和累计成本边界见 `docs/schema/work_units.md`。

## AgentRun 与流水线恢复

自动运行的暂停、恢复、停止和按原配置继续使用跑书页；人工生成、编辑、审批使用各 Stage 页面。
工程诊断先查询 `python -m novel.cli --help` 和目标子命令 `--help`。内部执行入口为
`novel/orchestrator/run_step.py`；Stage0 材料在 `novel/stages/stage0/review_workpack.py`，
命名 scratch 诊断在 `novel/orchestrator/preflight.py`。这些模块均可用 `python -m <模块名> --help` 查看参数。
运行代码和 workpack 身份覆盖必要资源；当前 start/epoch 使用 `identity_version=2`，不再计算 skill 整树摘要。
旧身份只作历史证据读取，不能补新摘要重签；旧 owner 排空后才可开当前 epoch，不续旧供应商会话。

Agent driver 与显式 legacy LLM transport 共用同一条 Stage0～Stage7 主链。付费节点由判别联合
`executor_backend` 决定：`kind=agent` 携 `AgentProfile`，`kind=legacy` 携 `LegacyLlmProfile`；不得再用
profile 是否存在推断模式。任一模式失败都不得自动降级到另一模式。当前 Legacy provider 为 DeepSeek；
未来 GLM 必须作为独立 provider driver 接入。正文外发授权与费用确认彼此独立。

对实际结束的 agent-only 整书做关闭式、0 模型请求验收：

```powershell
conda run -n novel_refactor python -X utf8 tools/verify_agent_e2e.py `
  --base <Output目录> --sid <sid> --run-id <Commander-run-id>
```

默认 `fresh` 验收仍要求九个付费 Agent 节点均在指定 run 中有完整采纳证据。
续跑可加 `--resume`：七层 current 产物、强闸、typed 定稿与交付检查不变，
费用/session/adoption 只核本轮实际调用，报告显式标为 `mode=resume`，
不能据此宣称九个节点在本轮重新执行。两种模式均逐 attempt 核对独立 Commander 席位；
Stage6/7 定稿复用 production current proof，不把 API 审批检查单当第二套人工决定。
同时核对公开 gate 与该 proof 的一致性，不能用 producer 真值掩盖 API 的错误状态。
同席瞬态失败不从账本删除，也不要求失败 attempt 伪装成成功：验收须将每次成本行与
attempt、实际存在的 provider turn 逐项联结，并证明同席有界重试最终成功；未观察到
provider 身份的尝试保留实际 usage，不伪造 turn，也不由此推断没有付费。未恢复、
非重试失败、身份冲突与未知状态仍拒绝，未知用量保留为未知。
v2 生命周期核对持久 epoch 开闭和 drain，旧 host phase 遥测仅用于 v1。
零模型调用的续跑从 durable started/epoch 核对唯一干净代码身份，报告仍明确
`experiment_valid=false`；没有模型执行不能冒充一次性能实验，也不要求伪造成本账行。

### 小说流水线成本与耗时配对观测

并发试验通过同一份 `PipelineAgentProfiles.concurrency` 配置：界面“自定义并发”只覆盖目标节点，
并显式保存整书、供应商总上限；例如目标 6 并发时，整书总上限也须至少为 6。默认仍为四并发。
standalone 的等价入口为 `NOVEL_CONCURRENCY_POLICY` JSON；受管运行从冻结 profiles 下发，宿主环境
不能覆盖它。原配置续跑保留该配置。总槽在同一输出根内跨书、跨进程共享，不同实验输出根彼此隔离。
调整总槽容量前先排空旧调用；容量记录损坏时停止检查，不删除配置继续试跑。

当前活动验收以 `docs/REQUIREMENTS.md::TODO-49` 为准：原三书，每书三组
基线/候选配对；首轮、局部返工与故障恢复分别取证。两臂各自冻结干净代码和契约版本，跨臂
固定源 hash、模型、effort 及非目标并发；使用独立空输出与空缓存并交替执行。
运行仍从 operator 或界面产生正式执行计划，付费与正文外发分别授权。

每臂运行结束或失败后，以 `tools/workflow_metrics.py --base <输出根> --sid <SID>
--run-id <parent-run-id> --json <输出根外报告>` 读取证据。新增 `performance` 节按现役节点登记
归属 Stage2/4/5 的 Executor 与 Commander；`role_content_phases` 从工作单元 ledger 联结内容轮，
首轮与修订不凭 purpose 名称猜测，同轮网络 attempts 另列。找不到精确关联时保留 unknown。
Stage5 首轮输入须读 Executor 的 first/prompt_tokens，不把 Commander 首轮混入。

活跃墙钟取精确 CLI execution 区间与 Commander 实测区间的并集，另报首尾 elapsed；
`known_interval_union_seconds` 只是已知片段，缺尾或身份不闭合时正式墙钟为空。
该节不表示节点交付或质量通过；adoption/恢复审计事件数量也不能当作完成工作单元数。
Ledger 未提交尾、未知 cache 状态或无法归属的调用使成本分区保持 unver，已知小计仍保留。
Reasoning 只作为 completion 子口径，不能再次相加；所有失败与人工追加后的调用留在分母。

质量样例由同一组源章冻结，覆盖事实、因果、人物/世界状态、揭示顺序、普通对白语义和必要字面
例外，并保持正文目标篇幅带。必须先核对各臂真实交付、HARD/skip 和人工语义裁决，再按书汇总配对
结果；任一失败、未完成或未知不得因更少消耗被计为节约。尚无配对数据时只报工程结果和未验证项。

### 当前报告与历史验收边界

`tools/workflow_metrics.py` 当前只生成 `novel-workflow-metrics-v3`。Stage2 辅助段与
`performance` 共用三 producer、两 Commander 的角色归属与墙钟，人物抽取、缓存、失败和
未知均计入。辅助段的载体、采用与审阅状态只供核对，不能直接形成质量结论。

当前验收入口是 `tools/workflow_pairs.py`，登记、收据与九组分母见
本节的性能配对执行协议。旧 `tools/stage2_acceptance.py` 仅保留对已经存在的
v2 历史报告的核对；不接收当前 v3 报告，不作为现役运行或发布入口。旧五轮、30 分钟标准
已退出活动目标，不沿用旧报告中的接受结论证明本次性能或质量。

### 性能配对执行协议

本协议服务 `docs/REQUIREMENTS.md` TODO-49。工程测试、语料验收与性能实验分开报告。
性能数字只从 `tools/workflow_metrics.py` 的当前 `novel-workflow-metrics-v3` 冻结报告读取；
既存报告若需重测，须回到对应冻结盘面重新采集，不能只改版本字段。成本只来自原 ledger，
reasoning 已包含在 completion 内，不再相加。工具不运行模型，也不创建生产审批。

基线 `24e4cf4` 的工作单元账是原始 v1。读数器仅在统计模块中按冻结形状只读解析它，
用于首轮、返修及网络尝试归属；候选的 v2 账继续调用当前 schema，报告显式列出
`performance.work_unit_ledger_versions`。同书混合版本、未知格式或身份/轮次/证据损坏时，
已知成本仍保留，内容阶段保持未知并禁止计算对应节约。此读取不修改旧根、不补字段，
不授予恢复、额度或生产写入权限；现役运行入口继续拒绝旧根。

#### 实验前登记

首轮、局部返工、故障恢复和并发对照分别建清单；每份清单固定三本书，每书三次配对。
失败和未知的实验也必须保留原位置，不能用后来的成功实验替换。每书第一次及第三次先跑基线，
第二次先跑候选；同对的两臂顺序执行，输出目录分别隔离，三次重复不复用该书的输出。

预登记冻结各臂代码身份、源文 SHA-256、模型与 effort 声明、非目标并发、篇幅目标带、
质量样例和输出命名空间。模型声明覆盖实验会用到的全部节点，新增人物节点在基线中没有执行
也是明确的拓扑差异，不能从 Stage2 的候选总消耗中扣除。普通对照保持目标节点并发相同；
并发实验单独指定现役 `target_node`，比较 4 与 6 或 8，不把两个候选并发混入同一份九组清单。

在 `novel_refactor` 环境导出当前输入 schema：

```powershell
python tools/workflow_pairs.py --emit-schema
```

清单 `schema=novel-workflow-pairs-v1`，包含 `registered_at`、`scenario`、`target_node`、
三项 `books`。每本书含 `sid` 和三项 `pairs`；每对含 `repeat=1/2/3`、`baseline`、`candidate`。
每臂必须有唯一 `run_id`、`report_path`、报告完整字节 SHA-256、带时区的开始/结束时刻、
`freeze` 与可空的 `review`。未完成时结束时刻和收据保持 null。报告路径由清单显式指定；
工具不搜语料目录、不向输出根写入任何内容。输入清单含本地路径，应按实验材料保管。

所有 `freeze` 哈希都必须来自实际冻结材料：源文件按原始字节计算，配置和样例索引按
UTF-8、排序键、无多余空白的 JSON 计算。`output_namespace_sha256` 绑定实验实际使用的独立
书输出目录。原始配置、样例索引与目录对应表另存本地供核验；不能拿固定占位哈希冒充冻结。
同书三次的源、篇幅目标和质量样例必须一致；两臂及全部重复的模型声明与非目标并发保持一致。

#### 固定质量样例与收据

实验前从原三书登记样例及证据锚，至少覆盖：事实遗漏、因果倒置、人物进出状态、埋点与揭示、
同章揭示场景重排、普通对白改述、承诺/对象/时间/数字及必要字面例外。按完整事实义务取样，
不截断正文或减少事实数量来获得较小输入。样例索引保存来源及完整 SHA-256；性能报告只带哈希。

每臂完成后，由人工核对配置和输出，形成 `ReviewReceipt`：`reviewer_kind=human`、具名 actor、
理由、带时区的核验时刻，以及报告、freeze 和最终输出的完整哈希。`freeze_sha256` 使用
`tools/workflow_pairs.py::freeze_sha256` 的唯一序列化口径。收据的配置状态为
`verified/mismatch/unknown`，完成状态为 `complete/failed/incomplete/unknown`。

质量逐项填写 `facts/causality/states/disclosure/dialogue/body_length/first_landing_rate/delivery_rate`，
每项只接受 `not_worse/worse/unknown`。这些是人工实验结论，不能从机械全绿、Agent 自述或 token
减少推导。未经标定的语义判断保留 unknown。缺任何维度、收据过期、输出未完成或质量退化，
该臂仍展示已知消耗，但不能贡献节约结论。

#### 采集与比较

使用已有读数入口，对每臂指定准确 run，将结果放在生产输出根之外：

```powershell
python tools/workflow_metrics.py --base <独立输出根> --sid <SID> --run-id <RUN_ID> --json <根外报告路径>
python tools/workflow_pairs.py --manifest <九组清单.json>
python tools/workflow_pairs.py --manifest <九组清单.json> --json
```

比较工具校验报告完整字节、schema、SID、run、唯一干净代码、ledger 完整性、冻结条件和具名收据。
配置和语义质量的实际核验责任仍在人工收据；工具不声称从哈希独立证明了该语义结论。
先逐书展示三对读数及中位数，再汇总九对。某指标有缺失或不合格配对时，保留完整分母，
不给剔除坏样本后的“验收中位数”。逐对原值、绝对变化、可计算的观察下降率和拒绝原因均在 JSON 中。
零基线发生增长时绝对变化照常显示，百分比未定义，不记为节约。

`performance.stages` 与 `performance.nodes` 使用同一聚合器。节点计入其 Executor 与 Commander；
`chapter_executor_usage` 只统计该章实际 Executor 调用，失败及重试均在内。Blueprint
`chapter_total_tokens_p50` 是这些单章总量的中位数，不能把书级 Commander 平均摊进每章。
缺任一章的用量时不出该中位数。Commander 在阶段和节点总量中完整保留。

活跃墙钟取完整 CLI 执行区间与 Commander 调用区间的并集，跨并发重叠不重复计算。
`observed_elapsed_seconds` 则包含这些区间之间的等待。累计调用 latency 仅作另一个观测口径，
不代替节点墙钟。Stage5 首轮输入必须有完整工作单元归属；归属未知时不能因为首轮分组为空而算成零。

首轮目标沿 TODO-49：Stage4 token/活跃墙钟下降 30%/20%；Stage2 token 下降 20%，阶段墙钟不恶化；
Blueprint 单章 token 中位数及节点活跃墙钟各下降 15%；Stage5 首轮输入下降 20%，阶段 token/
活跃墙钟各下降 15%。局部返工单独比较，Stage5 token 目标下降 20%。并发对照的节点活跃墙钟
目标下降 20%，同时保留 token、失败调用和网络重试；工具默认按无增长检查，不隐含容许退化。
故障恢复只展示观察读数，不复用首轮或局部返工的节约结论。

实验进度与材料缺口只在 REQUIREMENTS 的 TODO-49 维护。
付费执行、正文外发及最终整根归档继续走既有授权入口；本工具不能代替那些授权。

### 当前运行的收尾与恢复

模型进程结束不等于子执行已经闭合。RunSupervisor 关闭 admission 并排空 claim/execution 后，
调用 `novel/orchestrator/delivery.py::assess` 确定性交付终验。它不生成 Markdown 报告；小说下载和
Stage7 页面共用该判定，统计在运行页读取原账。工程诊断从 `reconcile --help` 与
`novel/orchestrator/admission.py` 定位 owner/坏尾；prepared 的来源与恢复约束见
`docs/schema/stage5.md` 和 `docs/schema/_orchestration.md`，不要用手工删锁代替排空证明。

append-only 坏尾先 inspect，再把返回的 current subject 连同 `actor/reason` 交回。两步必须由对应
Commander 子上下文之外的操作员执行，tail repair 不接受 `--force`：

```powershell
python -X utf8 -u -m novel.cli reconcile --base <Output目录> --beacon-repair-tail
python -X utf8 -u -m novel.cli reconcile --base <Output目录> --beacon-repair-tail `
  --actor <操作者> --reason <依据> --subject-sha256 <上一步SHA>
python -X utf8 -u -m novel.cli reconcile --base <Output目录> --admission-repair-tail
```

## pytest 与 corpus

`pyproject.toml` 默认登记 `-n 4 --dist loadgroup -m "not corpus"`，并固定复用仓根短路径 `.tr` /
`.test_cache`。普通测试按文件成组，登记为 `serial` 的线程/跨进程/取消生命周期模块进入同一个 worker；
模块级 fixture 与仓库扫描不会被拆散重跑。因此普通 pytest 只能称“非 corpus 回归”。最终语料验收必须
按 `tests/README.md` 同时提供 current-schema 输出盘、精确无正文 manifest，并显式使用
`-n 0 --require-corpus`；缺书、缺产物、版本漂移、ledger 不闭合或新增 skip 都会关闭式失败。

日常单点反馈用 `pytest -n 0 -x <精确 node>`；失败回放用 `pytest -n 0 --lf --lfnf=none -x`。这两类
命令没有覆盖完整收集面，只能报告定向测试。测试选择和批次门槛唯一见
`AGENTS.md::测试选择与批次验收`；局部修复可在定向通过后提交或继续已授权验收，提交本身不触发
完整回归。达到整批集成、候选冻结或 main／发布门槛时，才统一运行不带路径/额外 marker 的默认回归。

agent 执行 pytest 时必须绕开文件系统沙箱。Windows 沙箱可能给新目录写入仅沙箱 SID 可用的 ACL：

- 默认始终复用 `.tr` / `.test_cache`，不按批次、测试名或时间戳另造目录；`.tr` 为 xdist 的
  `popen-gwN` 子目录预留了 Windows 路径预算。
- 仓库路径预算不足时固定使用同一个仓外短根，例如 `--basetemp=E:\_tr`；pytest 下次启动会清空复用。
- 遗留的无 `dir=` 标准库临时调用会被会话 fixture 收进本次 basetemp 并自动清理；新增测试使用
  `tmp_path` / `tmp_path_factory`，不得继续向系统 `%TEMP%` 写 `tempfile.mkdtemp()`。
- 正常退出由 `tests/conftest.py::pytest_unconfigure` 只恢复本次 worker 实际触碰根和 controller cache
  的普通用户 ACL，不递归扫描本轮未使用的陈旧 runtime。
- 进程被强杀、钩子未运行时，以管理员权限一次性接管并删除那一个遗留目录；不要继续换新名字回避。

普通测试使用确定性 `code_version`；`real_code_version` marker 与 corpus 验收读取真实 Git。不得把
`novel.prov.code_version()` 加进程级缓存来换速度，长驻服务的代码身份语义必须保持实时。

改 R/Q/B、schema、节点或 API 时的最小契约验证：

```powershell
& 'D:\Miniconda\envs\novel_refactor\python.exe' -B -m pytest -q `
  tests/test_docs_references.py tests/test_coverage.py tests/test_rules_registry.py `
  tests/test_hard_counterexamples.py tests/test_ingest_fixtures.py
```

pytest 汇总行有时到不了宿主工具输出；需要精确 passed/skipped 时记录 JUnit，或核对完整进度点，不从
缺失的汇总行猜结果。

### 测试热点测量与准备复用

开发反馈只跑受影响文件或失败项；完整回归按 `AGENTS.md::测试选择与批次验收` 的门槛运行。
测试速度比较必须保持相同文件、环境和 worker 配置，并同时记录用例分母。先用 pytest 自带的阶段耗时区分 setup、
call 与 teardown，再对少量代表用例使用 cProfile；profiler 的附加开销不与普通测试秒数混比。

```powershell
& 'D:/Miniconda/envs/novel_refactor/python.exe' -B -m pytest -n 0 tests/test_agent.py tests/test_stage4_binding_input_recovery_integration.py tests/test_stage4_plan_chapters.py --durations=35 --basetemp=E:/_tr -o cache_dir=.test_cache --junitxml=.test_cache/test-hotspots.xml
```

JUnit 的单用例 time 合并准备、执行与清理；不能据此把整个慢用例归因于某一阶段。首项 setup 还可能
包含上一轮 basetemp 清理。所有 serial 模块共用一个 worker，因此先降低这些用例的实际成本，不能
只增加 `-n` 或删掉 serial 登记。真实进程测试的 `wait(..., timeout)` 是等待上限，不等于固定 sleep。
运行真实代码身份测试时，须冻结同一 checkout 的全部写入，包括未跟踪的规划文档；否则代码漂移
会触发真实 epoch 守卫，让测试耗时包含反复重开而非被测步骤本身。

上游准备只在同一次 pytest 内复用：原 producer 生成一次，`FrozenTree` 保存不可变字节，每例还原
独立目录，重新创建当前 Store/模型对象。它不是跨运行 fixture 缓存，改代码后下一轮必须重新生成。
被测 producer、校验、故障窗和恢复调用不能替换为基线结果，生产 `code_version()` 也不能缓存。

## 前端与真实联调

API 只监听 `127.0.0.1:8765`。前端默认配置在 `web/.env`，真实联调应为
`VITE_USE_MOCK=false`。`npm ci`、`typecheck`、`seams`、`build`、`dev` 的唯一命令说明见
`web/AGENTS.md`。

编译通过不等于联调完成。交付前在真实服务上覆盖正常、未运行、错误和合法空态，并确认关闭 mock 后
没有样本正文进入网络响应或构建产物。

## legacy DeepSeek 传输诊断

本节只服务显式选择的 legacy DeepSeek 回滚路径，不是 Claude Code / Codex Commander 或 executor 的
启动条件。若本机代理使用 TUN/fake-IP，后台 DeepSeek 请求出现 `SSL UNEXPECTED_EOF`，可在根 `.env`
显式设置：

```text
DEEPSEEK_PROXY=http://127.0.0.1:<本地 HTTP 代理端口>
```

随后先运行：

```powershell
conda run -n novel_refactor python -m novel.cli doctor --llm-transport
```

该命令只探测 DNS/TCP/TLS/HTTP，不发送正文或 API key；401/403 也足以证明传输已经建立。只有探针成功
后才值得启动一次显式授权的真实付费节点。正常 agent 路径不得因这项配置缺失而受阻，也不得在旧传输
失败后自动切 provider。

## 代码备份与恢复

### 工作流发布时整根归档

此入口只封存已经排空的旧输出根，不替代语料/性能验收，也不自动启动新生产。停止旧服务、调度器和
独立 CLI，用配对旧代码完成 stop/drain/reconcile；无法解释的 owner、未知账、未提交尾保持阻塞。
`--port` 重复填写所有服务端口，已停止的服务/独立进程可重复填写 `--stopped-pid`，工具逐一核验。
`--offline-ack` 记录操作员已经停止其他启动入口，端口占用与锁探测不能替代这项事实。

旧根、归档根、新根、事务目录互不包含，后三者在 preview 前不存在；它们与配对旧代码 checkout
分开存放，父目录预先存在。配对旧代码必须是干净 commit。归档根、事务目录与旧根同卷；链接、
reparse point、输入文件的硬链接别名和跨卷复制全部拒绝。preview 不改旧根文件内容或目录结构。
Windows 的目录移动不能同时持有内部文件句柄，因此工具在外部事务目录给已有锁文件创建同 inode
的临时别名，并从别名持有同一 OS byte-lock；这不释放 owner 证明，也不复制文件内容。正常结束删除
精确别名，hard-kill 后原事务校验 samefile 并复用。预览前中断可原样重放 preview；已有完整 preview
后只能 apply，不另开事务清扫残留锁。

```powershell
python -X utf8 -B tools/archive_output_root.py preview --source <OLD_ROOT> --archive <ARCHIVE_ROOT> --new-root <NEW_ROOT> --journal <TRANSACTION_DIR> --old-code <CLEAN_OLD_CHECKOUT> --port 8765 --actor <ACTOR> --reason <REASON> --offline-ack
python -X utf8 -B tools/archive_output_root.py apply --journal <TRANSACTION_DIR> --expected-sha256 <PREVIEW_SHA256> --yes
```

使用 `novel_refactor` 的解释器。操作员批准的具体对象是 `preview.json` 的路径、旧 commit、完整清单和
停止证明。apply 中断后原样重放第二条命令，不另做 preview、不删除 prepared，也不手工搬单书补齐。
提交后新根最初为空，服务以显式 `--base <NEW_ROOT>` 启动；根外 `.名称.novel-root.json` 是维护证据，
不能当作临时文件删除。已提交事务重放只核验归档并返回回执，绝不清空后来产生的新输出。
回滚时使用清单绑定的旧 commit 和归档根；当前代码会拒绝原地址与归档地址。

### 代码灾备

当前验收范围只有代码灾备。版权语料与付费产物不备份；丢失后需要重新取得语料并重新付费生成，这是
已接受的残余风险。代码权威副本是 GitHub 私库；同一磁盘快照只用于恢复演练，不冒充异地副本。

```powershell
conda run -n novel_refactor python tools/backup_snapshot.py create-code `
  --repo . --archive E:\offline-backup\novel-code.tar.gz

conda run -n novel_refactor python tools/backup_snapshot.py restore `
  --archive E:\offline-backup\novel-code.tar.gz --destination E:\restore-drill\novel
```

`create-code` 的文件集来自 Git tracked + untracked/non-ignored 文件；只放行 `.env.example` 与 `web/.env`，
硬排除其他 `.env*`、`settings.local.json`、`outputs/`、`Novel_data/`、`Test_data/`、缓存和构建目录，且
拒绝跟随符号链接。恢复只允许空目录，并在落盘前后检查路径穿越、重复成员和逐文件 SHA-256。

恢复演练后确认 `.env.example`、`pyproject.toml`、`web/.env`、`web/package.json`、
`tests/fixtures/manifest.json` 存在，根 `.env`、`.claude/settings.local.json`、`outputs/`、`Test_data/`、
`.llm_cache/` 等敏感与语料路径不存在，再运行：

```powershell
conda run -n novel_refactor python -m pytest -q tests/test_fixture_registry.py
```

记录演练日期、归档 SHA-256、恢复文件数和测试结果。只记录“备份任务成功”不算恢复演练；最近一次可继续
引用的读数见 `docs/DECISIONS.md`。

若未来明确决定备份私密数据，`create-private` 必须显式给出数据根和受控加密离线目标，并带
`--acknowledge-encrypted-offline-target`；该 flag 是操作者对介质的声明，工具本身不提供加密。


## Stage4 免费聚合与跨执行补尾

Operator/队列由 `novel/stages/stage4/plan_recovery.py` 做完整来源预览后，才发布
`stage4.design --reaggregate` 或 `stage4.plan --reaggregate`。A 必须已有当前策略与全部章响应，
B 可以混合机械与已保存成果；缺章/冲突/未知均停止。A 的来源逐单元冻结，允许互补历史运行；
全部采用不创建模型客户端，随后仍进入正式设计审阅。
已结束 producer 的 `aggregate-committed.json` 停在 prepared 时，先于审批返回
`--recover-prepared <source run> --subject-sha256 <proof>`；执行入口和凭据来自现役
operator/permit，不手选最近 run。此动作不需要付费 profiles，不启动模型客户端。

恢复分别由 `novel/stages/stage4/design_recovery.py` 和 `novel/stages/stage4/plan_recovery.py`
复验 A/B，保持原 Candidate 或 Binding/Plan 的准确冻结字节，补齐原工作单元采用与独立恢复执行审计；
只承认写前基线或冻结目标，ledger 调用不增加。原 Agent parent 必须终态且所有
epoch drained，执行期间继续持有 owner-stopped lease。证据、LF 审计尾或目标文件
无法证明时保持 unknown，不清空账本、不修改旧 journal 以跳过守卫。
