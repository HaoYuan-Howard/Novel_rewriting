# 测试分层

默认测试必须可复现，不读取本地小说产物。开发依赖包含 pytest-xdist；默认命令以 4 个 worker
并行非 corpus 回归：

```powershell
conda run -n novel_refactor python -m pytest
```

`tests/conftest.py` 把普通测试按文件分到同一 worker，使模块 fixture、skill 校验快照和 AST 缓存只
建立一次；登记在 `_SERIAL_TEST_MODULES` 的并发/进程生命周期模块进入同一个串行组。新增这类模块时
必须同步登记，AST 守卫会反查遗漏，不能靠本机一次碰巧不抖来判断可并行。

日常编辑先跑精确 node；单点用 `-n 0` 避免启动四个 worker。需要回放失败缓存时使用：

```powershell
conda run -n novel_refactor python -m pytest -n 0 -x tests/test_example.py::test_exact_node
conda run -n novel_refactor python -m pytest -n 0 --lf --lfnf=none -x
```

这些命令只能报告“定向测试通过”。只有不附测试路径和额外 marker、完整退出 0 的默认命令，才可称
“非 corpus 回归通过”。

同一批工程按「受影响文件 → 修复失败项 → 稳定后一次默认回归」推进，不在每次局部编辑后重跑全仓。
契约、schema、节点或 API 改动仍先跑契约 §9.5 五文件；完整回归之后若又修改源码，按改动影响重新
验证，不能把修复前的结果写成最终全绿。测速和 profiler 仅用于已定位的热点，命令见
`docs/OPERATIONS.md::测试热点测量与准备复用`。

重复上游准备可由原 producer 生成模块级基线，再用 `tests/frozen_tree.py::FrozenTree` 保存不可变
字节，每例还原到自己的空 `tmp_path`。不共享可变 Store、模型对象、账或硬链接；不重用被测步骤的
结论。当前仅 Stage4 B 的两个测试模块复用 A/旧 B 前置盘面，故障注入、真实生产、HTTP/审批、
并发与最终字节断言仍逐例运行。并发生命周期的 serial 登记和真实代码身份校验保持原约束。

标记为 `corpus` 的测试是针对冻结生成产物或源语料的集成检查，默认 marker 表达式会跳过它们。
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
  `conftest.py::_windows_basetemp_problem` 会**实测**到这一点并自动不再拦——它探测文件系统
  本身，不读注册表也不查 manifest（那是两个各自都会变的间接条件）。git 另需
  `git config --global core.longpaths true`，它不吃系统开关。

⚠ 不要改用 `\\?\` 前缀绕过：前缀总能写成功，但前缀建出来的文件**用普通路径读不回来**
（实测 `FileNotFoundError`），那是把明确报错换成「一部分代码看得见、一部分看不见」的静默不一致。

会话 fixture 会把遗留的无 `dir=` 标准库临时调用统一重定向到本次 worker 的 basetemp 子根，并在退出
时自动清理；新增测试直接使用 `tmp_path` / `tmp_path_factory`，不要继续新增 `tempfile.mkdtemp()`，也
不要在 `%TEMP%` 或按时间戳另造根。正常退出只恢复本次 worker 实际触碰根与 controller cache 的 ACL，
不会递归扫描未参与本轮的陈旧 basetemp。
