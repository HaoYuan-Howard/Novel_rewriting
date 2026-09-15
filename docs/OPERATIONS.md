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
Agent 或运行中的 API 作业时停止旧进程；否则关闭式拒绝。agent 只读端点故障时（旧代码损坏正是最需要
重启的时刻），agent 活动无法核实：脚本显式警告并仅以 `/api/jobs` 空闲为停机依据，不冒充已验证空闲。

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

当前 manifest v6 登记 `stage6-prepare-v1`、`independent-hook-preparation-v1` 及初始化产物版本快照，
普通入口只接受当前版本，独立空根才可初始化；旧 manifest、事实蓝图和审批不自动迁移。首次合法写入前固定
`_service/workflow.json`；其后所有节点、直接 producer、审批和恢复共用该身份，不另建执行 epoch。
不得删除 manifest、工作单元账或原审批来重新初始化；旧 base 的读取与诊断不授予新写入权限。

已登记的版本推进为 Gate A 的 v4 → Gate B 的 v5、Gate B 的 v5 → Gate C 的 v6。前者用冻结来源
代码收口 Stage2 实际语义接受，后者还须证明 Stage5 当前闭合。排空所有 owner，停止服务、队列调度器
和独立 CLI，再在干净、固定提交的目标代码中执行：

```powershell
conda run -n novel_refactor python -m novel.cli workflow transition preview --base <原输出根> --source-code <冻结来源checkout> --port <原服务端口> --stopped-pid <已退出的服务PID> --offline-ack --actor codex --reason <推进理由>
conda run -n novel_refactor python -m novel.cli workflow transition apply --base <原输出根> --transition-id <preview返回ID> --prepared-sha256 <preview返回摘要> --yes
```

多个原服务端口/PID分别重复对应参数。`--offline-ack` 明确包括未监听端口的独立 CLI；工具同时占住
声明端口、检查 PID、持有现有 writer/owner 文件锁，并用来源代码复核生命周期完整分母和实际 Core 接受。
来源 checkout 必须仍是对应干净提交；当前工作区已经修改时，另建固定该提交的 checkout，不能冒称旧版本。

preview 保存不可变 `_service/workflow_transitions/<ID>/prepared.json` 和 pending 围栏，绑定原 manifest、
目标 manifest、两侧代码、全盘保留清单及审批来源。apply 只按精确 source/target 原字节提交；原 task_id、
created_at、profiles、累计 ledger、工作单元、人物、Global 和审批保持原字节。唯一派生产物例外是
Characters 的独立 Blueprint 人物投影：调用现役纯编译器补全呈现绑定，原/目标投影及 Characters/MainText
输入摘要一起冻结，不用默认空值兼容旧投影，也不据旧 gate bool 签发新审批。

任何目标下游 formal、INVALID、history、checkpoint、prepared 或已购回答残留都拒绝推进，不能删文件绕过。
投影、manifest 或 completed 的任一提交窗口中断，都用同一 ID/SHA 免费补尾：

```powershell
conda run -n novel_refactor python -m novel.cli workflow transition recover --base <原输出根> --transition-id <原ID> --prepared-sha256 <原摘要> --yes
```

若 preview 在返回前中断，从该目录的不可变 prepared 取 ID，并对原始文件计算 SHA-256；不能重新生成
另一份 prepared。第三方改动不是可恢复的 source/target 窗口，必须停下核对。completed 未闭合时普通写口
拒绝；completed 重放只返回原回执，不重新检查已开始的下游或重开事务。这些命令不调用模型、不增加或重置
内容额度；后续运行仍开新 execution epoch，并按原 profiles、原累计额度继续。v5 → v6 保留全部 Stage4–5
产物、审批、运行来源，不重建人物投影；仅拒绝 Stage6/7 的 formal、INVALID、history、prepared、checkpoint
和已购回答残留。两个版本对保留各自不可变回执与恢复，不能泛开任意 manifest 升级。

上一迁移已写 completed、但 pending 指针尚未清理时，下一迁移只在旧回执闭合且旧 target 原字节
恰等于新 source 时接管围栏。新 prepared 已保存而指针交接中断，仍用新 ID/SHA recover；代码在
离线、来源清单和生命周期证明全部通过后才清理旧指针。不要手删 pending 或改写旧 completed。

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
如需 GLM 的直接 HTTP transport，仍须独立接入，不能复用 DeepSeek 的预算与 thinking 语义。
正文外发授权与费用确认彼此独立。

Claude CLI 的智谱路线使用独立 `claude-code-glm` driver，provider 为 `bigmodel`。开发维护者先在
Claude CLI 用户配置中设置 `ANTHROPIC_BASE_URL=https://open.bigmodel.cn/api/anthropic` 及认证；
运行代码检查并固定该接收端，不复制密钥或修改用户设置。当前发布 `glm-5.3-flash[1M]`，
支持 `low/high/max` 三档；当前 Stage2 Core 验收两角色固定 `high`，以 `TODO-51` 的授权配置为准。
`[1M]` 是上下文窗口请求后缀，
只接受登记的同模型回显；实际模型、实际 effort 和用量未回显时仍保持未知，不能用请求值倒填。
切换供应商或模型要在旧 owner 排空后显式开新配置的运行；原配置继续入口不承担换模型，
工作单元额度与已购成果仍保留。版本探针不证明网络、账号或模型权限。

无回答的 Commander 连接失败可由开发维护 agent 免费收口：先用
`python -m novel.orchestrator.work_unit_recovery --base <根> --sid <sid> --unit-id <id> --turn-id <id>`
取得预览；将 `preview_token`、执行者 `actor`、核实理由 `reason` 写入 JSON 后，同命令增加
`--apply-request <JSON路径>`。该入口逐项核成本、session、原请求、日志和 owner 排空证据，
只把已确认失败的 unknown 改为 failed，不生成语义决定、不退还额度、不清除未知用量。
存在已购回答或证据缺失时继续阻断，先恢复原结果；同一请求重放幂等。该入口不是产品跑书步骤。

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

### 性能配对工具的退役边界

Stage2 语义审阅退役（2026-09，工作流 v7）同批删除了读数与配对工具：`tools/workflow_metrics.py`
的主体是 Stage2 Commander 工作单元、审阅状态与 `global_review` 盘面读数，`tools/workflow_pairs.py`
只消费前者的冻结报告，`tools/stage2_acceptance.py` 是更早的五轮历史解释器。三者的历史实现与
解释边界由 Git 历史保存，历史实验记录不再由现役代码解释。

TODO-49 记录的 token/墙钟目标与“首轮、局部返工、故障恢复分开取证”的实验纪律仍然有效；
但在 v7 工作流上重新执行性能配对前，须先按新的节点面重建只读读数工具并在本节登记协议，
不能拿旧工具或旧报告的结论充当 v7 基线。工程测试、语料验收与性能实验继续分开报告；
成本只认 `_service/ledger.jsonl`，reasoning 只作为 completion 子口径，不再次相加。

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
完整回归。达到根指南规定的完整回归门槛时，才统一运行不带路径/额外 marker 的默认回归。

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
