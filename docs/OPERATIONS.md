# 本机运行、验证与恢复手册

本文只保存操作步骤。产品语义与 R/Q/B 以 `docs/BACKEND_CONTRACT.md` 为准，跑书的逐步决策以
`.workflow/skills/novel-pipeline-operator/SKILL.md` 为准，前端开发命令以 `web/DELIVERY.md` 为准。

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
conda run -n novel_refactor python -m pytest -q
conda run -n novel_refactor python -m novel.api --base <Output目录>
conda run -n novel_refactor python -X utf8 -u -m novel.cli state <sid> --base <Output目录>
conda run -n novel_refactor python -X utf8 -u -m novel.cli inbox <sid> --base <Output目录>
conda run -n novel_refactor python -X utf8 -u -m novel.cli operator next <sid> --base <Output目录> --json
```

`operator next` 在同一份冻结快照上返回 `ready / blocked / complete`。`ready` 已携带 canonical runner
需要的 exact execution token；不得重新扫描全链或凭聊天记忆推演下一步。`operator permit` 只保留给
blocked recovery 和诊断兼容，blocked action hash 本身不是恢复许可。

## 新旧工作流的执行边界

`workflow-role-boundaries-v1` 只用于新的 clean base。首次合法写入前固定
`_service/workflow.json`；其后所有节点、直接 producer、审批和恢复共用该身份。
不得删除 manifest、工作单元账或原审批来重新初始化；旧 base 的读取与诊断不授予新写入权限。

本分支改造前的冻结基线为 `6473b78053ddb66eb111e835e41eb8d1f120eec7`。存量中断任务先核对原
run 审计中的代码身份，再在匹配的旧代码工作区执行其版本的运行 skill；不能仅因主分支名相同就认为版本匹配。
对于确认属于上述基线的旧任务，可建立独立、固定提交的恢复工作区：

```powershell
git worktree add --detach .worktrees/legacy-workflow-6473b78 6473b78053ddb66eb111e835e41eb8d1f120eec7
```

在该工作区读取 `.workflow/skills/novel-pipeline-operator/SKILL.md`，沿用原 base、账本与审批恢复。
如果原任务属于其他代码身份，先恢复对应的原版本；代码身份缺失或脏工作区内容无法复原时保持停点。
不得用本分支替旧 base 补造新预算、两份 Stage2 接受证明或当前版本产物。本轮工程验收未运行或修改存量任务。

## AgentRun 与流水线恢复

完整跑书、断点续跑、代批和最终成书审阅必须使用
`.workflow/skills/novel-pipeline-operator/SKILL.md`。CLI 子命令包括 `run`、`preview`、`state`、`inbox`、
`operator`、`queue`、`queue-list`、`queue-cancel`、`queue-resume`、`jobs`、`reconcile`、
`response-repair`、`approval` 与 `mark`；先以 skill 当前说明为准，不从这张列表反推参数。

Agent driver 与显式 legacy LLM transport 共用同一条 Stage0～Stage7 主链。付费节点由判别联合
`executor_backend` 决定：`kind=agent` 携 `AgentProfile`，`kind=legacy` 携 `LegacyLlmProfile`；不得再用
profile 是否存在推断模式。任一模式失败都不得自动降级到另一模式。当前 Legacy provider 为 DeepSeek；
未来 GLM 必须作为独立 provider driver 接入。正文外发授权与费用确认彼此独立。

对实际结束的 agent-only 整书做关闭式、0 模型请求验收：

```powershell
conda run -n novel_refactor python -X utf8 tools/verify_agent_e2e.py `
  --base <Output目录> --sid <sid> --run-id <Commander-run-id>
```

默认 `fresh` 验收仍要求八个付费 Agent 节点均在指定 run 中有完整采纳证据。
续跑可加 `--resume`：七层 current 产物、强闸、typed 定稿与交付检查不变，
费用/session/adoption 只核本轮实际调用，报告显式标为 `mode=resume`，
不能据此宣称八个节点在本轮重新执行。两种模式均逐 attempt 核对独立 Commander 席位；
Stage6/7 定稿复用 production current proof，不把 API 审批检查单当第二套人工决定。
同时核对公开 gate 与该 proof 的一致性，不能用 producer 真值掩盖 API 的错误状态。
同席瞬态失败不从账本删除，也不要求失败 attempt 伪装成成功：验收须将每次成本行与
attempt、实际存在的 provider turn 逐项联结，并证明同席有界重试最终成功；未观察到
provider 身份的尝试保留实际 usage，不伪造 turn，也不由此推断没有付费。未恢复、
非重试失败、身份冲突与未知状态仍拒绝，未知用量保留为未知。
v2 生命周期核对持久 epoch 开闭和 drain，旧 host phase 遥测仅用于 v1。
零模型调用的续跑从 durable started/epoch 核对唯一干净代码身份，报告仍明确
`experiment_valid=false`；没有模型执行不能冒充一次性能实验，也不要求伪造成本账行。

### Stage2 五次冷缓存性能与质量验收

这不是模型 A/B。操作者可以在第一次跑数前从设置界面更换一次默认 Agent profile；同一批五次
clean run 之间不得再改 Blueprint/Commander 的实际模型、effort 或 `NOVEL_CONCURRENCY`。每次使用
独立 clean base 与空缓存，并继续按 operator skill 或界面启动真实流水线，不在这里复制生产跑书 argv。
本次《夫君不行_但我有孕啦》的历史 Stage2 墙钟基线是 3044 秒；“显著提速”在跑前登记为五次
新 run 的 P50 至少改善 20%，而 30 分钟目标要求五次里的 **max** 都不超过 1800 秒。

每次跑完立即把只读报告写到对应 base 之外；`--run-id` 使用顶层 parent/Commander run id：

```powershell
conda run -n novel_refactor python -X utf8 tools/workflow_metrics.py `
  --base <本次clean-Output目录> --sid 2062949822106228156 `
  --run-id <parent-run-id> --json <仓外报告目录\stage2-run-N.json>
```

每个 run 至少固定抽查 5 个章序（建议同一组首章、前中段、中段、后中段、末章），逐项对照源章核
事实召回、因果方向、人物/世界状态与 evidence 是否落在所属 beat；不能只看“读起来合理”。人工记录
不放正文，只保存章序、结论、操作者和可审计理由：

```json
{
  "schema": "novel-stage2-manual-quality-v1",
  "runs": [
    {
      "run_id": "<parent-run-id>",
      "decision": "pass",
      "sampled_chapters": [1, 6, 11, 17, 22],
      "reviewer": "<操作者>",
      "reason": "逐章核过事实、因果、状态与证据所属拍；无漏项或错锚"
    }
  ]
}
```

五份报告与五条人工记录齐备后统一收口；`--report` 精确重复五次：

```powershell
conda run -n novel_refactor python -X utf8 tools/stage2_acceptance.py `
  --report <stage2-run-1.json> --report <stage2-run-2.json> `
  --report <stage2-run-3.json> --report <stage2-run-4.json> `
  --report <stage2-run-5.json> --manual-quality <manual-quality.json> `
  --target-seconds 1800 --baseline-seconds 3044 `
  --minimum-speedup-percent 20 --json <仓外报告目录\stage2-acceptance.json>
```

工具同时关闭式核对：五个不同 clean run、当前 schema 与目标 run adoption、无 `.INVALID`、生产
HARD/skip、Global generation-safe、冷缓存、首次落盘率不低于 95%、Global 与 Blueprint 两个 Commander 各不超过 4 次内容调用、
Global 接受先于 Blueprint 生成且 Blueprint 经独立交叉接受，以及五次实际模型/effort 一致。人工记录缺一轮、每轮少于 5 章，或任一
读数未知时结论保持 `unver`；在汇总 `claim_ready=true` 前不得宣称质量未退化、显著提速或已稳定达到
30 分钟。

顶层 Commander 结束不等于子执行已经闭合。正常主路由 RunSupervisor、admission claim、beacon、ledger
和 finalizer 自动收口；故障恢复、prepared bridge、坏尾与 owner lease 的精确步骤只读 skill 的
`references/recovery.md`，不要用手工删锁文件代替 reconcile。

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
命令没有覆盖完整收集面，只能报告定向测试；提交前仍须运行不带路径/额外 marker 的默认回归。

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

开发反馈只跑受影响文件或失败项；同一批改动稳定后再跑一次默认非 corpus 回归。测试速度比较必须
保持相同文件、环境和 worker 配置，并同时记录用例分母。先用 pytest 自带的阶段耗时区分 setup、
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
`web/DELIVERY.md`。

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
