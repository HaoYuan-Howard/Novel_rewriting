# 工程运维手册

本文供开发维护 agent 和维护者查环境、部署、测试、诊断与工程恢复。它不被 Executor 加载，也不参与
确定性调度。产品为全自动单模式：上传即授权开跑，Stage 页只读监控；历史人工审批闸与
受托审阅角色均已整层退役。
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

## AgentRun 与流水线恢复

自动运行的暂停、恢复、停止和按原配置继续使用跑书页；Stage 页只读监控。工程诊断先查询
`python -m novel.cli --help` 和目标子命令 `--help`。内部执行入口为
`novel/orchestrator/run_step.py`；命名 scratch
诊断在 `novel/orchestrator/preflight.py`。这些模块均可用 `python -m <模块名> --help` 查看参数。
运行代码和 workpack 身份覆盖必要资源；当前 start/epoch 使用 `identity_version=3`（引擎
`direct_llm` / `llm-executor/v1`），历史 ≤v2 epoch 只读原语义。
旧身份只作历史证据读取，不能补新摘要重签；旧 owner 排空后才可开当前 epoch，不续旧供应商会话。

现役唯一 executor 是直连 LLM（wire `kind=legacy` + `LegacyLlmProfile`：provider/model/
effort/thinking）。本地 Agent CLI（`kind=agent` / driver / execution_mode）已退役：命中即
`retired_executor_backend`（HTTP 410），不做静默降级。UI「模型执行配置」只消费
`/api/agent/capabilities` 的 legacy providers 面。当前 Legacy provider 为 DeepSeek；
如需其他 HTTP transport，须独立接入，不能复用 DeepSeek 的预算与 thinking 语义。
正文外发授权与费用确认彼此独立。

无回答的传输失败可由开发维护 agent 免费收口：先用
`python -m novel.orchestrator.work_unit_recovery --base <根> --sid <sid> --unit-id <id> --turn-id <id>`
取得预览；将 `preview_token`、执行者 `actor`、核实理由 `reason` 写入 JSON 后，同命令增加
`--apply-request <JSON路径>`。该入口逐项核成本、session、原请求、日志和 owner 排空证据，
只把已确认失败的 unknown 改为 failed，不生成语义决定、不退还额度、不清除未知用量。
存在已购回答或证据缺失时继续阻断，先恢复原结果；同一请求重放幂等。该入口不是产品跑书步骤；
admission 账沿用历史命名（`commander_admissions.jsonl`）。两个分支：存量书 Commander 连接失败
走原 Commander 证明；现役直连 executor 的**确定性 wire 拒收**（不可重试 `TransportError`，
如 4xx 拒付——拒收种类闭集 `contracts/schema/work_unit_recovery.py::EXECUTOR_WIRE_REFUSAL_KINDS`）
走 executor 分支（2026-10-02 R10c，DeepSeek 402 实证）。read_timeout 与未知异常的结果真未知，
不在闭集内，保持 unknown。新 wire 已把确定性拒收直接记 failed（`novel/llm/paid_requests.py`），
executor 分支服务存量轮与极端残差。
现役关闭式验收走定向 pytest / 交付终验，不维护 agent-only 验收报告。

### 小说流水线成本与耗时配对观测

并发试验通过同一份 `PipelineAgentProfiles.concurrency` 配置：界面“自定义并发”只覆盖目标节点，
并显式保存整书、供应商总上限；例如目标 6 并发时，整书总上限也须至少为 6。默认仍为四并发。
standalone 的等价入口为 `NOVEL_CONCURRENCY_POLICY` JSON；受管运行从冻结 profiles 下发，宿主环境
不能覆盖它。原配置续跑保留该配置。总槽在同一输出根内跨书、跨进程共享，不同实验输出根彼此隔离。
调整总槽容量前先排空旧调用；容量记录损坏时停止检查，不删除配置继续试跑。
工程测试、语料验收与性能实验分开报告；成本只认 `_service/ledger.jsonl`，reasoning 只作为
completion 子口径，不再次相加；重做性能配对前先按现役节点面重建只读读数工具并登记协议。

### 当前运行的收尾与恢复

模型进程结束不等于子执行已经闭合。RunSupervisor 关闭 admission 并排空 claim/execution 后，
调用 `novel/orchestrator/delivery.py::assess` 确定性交付终验。它不生成 Markdown 报告；小说下载和
Stage7 页面共用该判定，统计在运行页读取原账。工程诊断从 `reconcile --help` 与
`novel/orchestrator/admission.py` 定位 owner/坏尾；prepared 的来源与恢复约束见
`docs/schema/stage5.md` 和 `docs/schema/_orchestration.md`，不要用手工删锁代替排空证明。

append-only 坏尾先 inspect，再把返回的 current subject 连同 `actor/reason` 交回。两步必须由对应
agent run 子上下文之外的操作员执行，tail repair 不接受 `--force`：

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
& 'D:/Miniconda/envs/novel_refactor/python.exe' -B -m pytest -n 0 tests/test_agent.py tests/test_str06_body.py tests/test_str06_workflow.py --durations=35 --basetemp=E:/_tr -o cache_dir=.test_cache --junitxml=.test_cache/test-hotspots.xml
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

本节只服务显式选择的 legacy DeepSeek 回滚路径，不是 Claude Code / Codex executor 的
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


## Stage4 免费编译与候选生命周期补尾（新链）

STR-06 新链的 Stage4 免费聚合由结构性禁付节点 `str06.stage4.compile` 承担：Design/Hook/Bind
的已购 wire 经 `--check-only` 或默认免费采用路径编译，全部采用不创建模型客户端。跨执行补尾
不再是 `--recover-prepared` 一类人工恢复动作，而是候选生命周期自身的
`committed_set_recovery`／`deterministic_replay`（`novel/llm/paid_requests.py::PaidRequestSession`）：
同请求身份优先免费采用，缺失且未授权时停在该 owner 并报告需要授权（`PurchaseRequired`）。
付费与正文外发是两份独立授权（`--yes`／`--allow-egress`），入口只有
`novel.cli run <str06 节点> <sid>`；恢复入口与账务纪律见 `tests/test_str06_f7a_wiring.py`。

旧书按 `legacy_incompatible` 只读拒识，重跑须在独立输出根按新链重新上传
（裁决见 `docs/DECISIONS.md` 2026-09-09～10「一次整根切换」行）；旧链恢复动作
（reaggregate／recover-prepared 一类）已随旧链整层退役；存量解码面与退役台账
已随 STR-06 关闭批删除，旧根的恢复材料只按 Git 历史与冻结的盘上记录读取。
