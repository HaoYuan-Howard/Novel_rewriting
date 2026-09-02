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

`pyproject.toml` 默认登记 `-m "not corpus"`，并固定复用仓根 `.test_runtime` / `.test_cache`。因此普通
pytest 只能称“非 corpus 回归”。最终语料验收必须按 `tests/README.md` 同时提供 current-schema 输出盘、
精确无正文 manifest，并使用 `--require-corpus`；缺书、缺产物、版本漂移、ledger 不闭合或新增 skip
都会关闭式失败。

agent 执行 pytest 时必须绕开文件系统沙箱。Windows 沙箱可能给新目录写入仅沙箱 SID 可用的 ACL：

- 默认始终复用 `.test_runtime` / `.test_cache`，不按批次、测试名或时间戳另造目录。
- 仓库路径预算不足时固定使用同一个仓外短根，例如 `--basetemp=E:\_tr`；pytest 下次启动会清空复用。
- 正常退出由 `tests/conftest.py::pytest_unconfigure` 恢复普通用户 ACL。
- 进程被强杀、钩子未运行时，以管理员权限一次性接管并删除那一个遗留目录；不要继续换新名字回避。

改 R/Q/B、schema、节点或 API 时的最小契约验证：

```powershell
& 'D:\Miniconda\envs\novel_refactor\python.exe' -B -m pytest -q `
  tests/test_docs_references.py tests/test_coverage.py tests/test_rules_registry.py `
  tests/test_hard_counterexamples.py tests/test_ingest_fixtures.py
```

pytest 汇总行有时到不了宿主工具输出；需要精确 passed/skipped 时记录 JUnit，或核对完整进度点，不从
缺失的汇总行猜结果。

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
