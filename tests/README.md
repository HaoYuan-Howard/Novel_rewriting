# 测试分层

默认测试必须可复现，不读取本地小说产物：

```powershell
conda run -n novel_refactor python -m pytest
```

标记为 `corpus` 的测试是针对冻结生成产物或源语料的集成检查，默认 marker 表达式会跳过它们。
最终硬验收必须同时提供隔离的当前 schema 输出盘和与它精确对应、已提交的无正文 manifest，
并使用 `--require-corpus`：

```powershell
$env:NOVEL_OUTPUT = 'E:\path\to\isolated-current-schema-output'
$env:NOVEL_CORPUS_MANIFEST = (Resolve-Path -LiteralPath 'docs\corpus\current_schema_manifest.json').Path
conda run -n novel_refactor python -m pytest --require-corpus
```

只跑 `-m corpus` 不是最终验收：它不验证冻结 manifest/ledger/精确 skip allowlist。当前
manifest 尚未生成时，只能报告"非 corpus 回归"，不能声称全量已绿。manifest 的字段契约、
冻结书目与硬验收判据见 `docs/schema/corpus.md`。

纯判据与 prompt 测试应使用 `tests/factories.py` 中的确定性构造器，不得随机选择一本本地
小说。测试若直接写注册 JSON 产物，须先用 `contracts.schema.encode_artifact()` 包装逻辑
payload。

Windows 上的 `--basetemp` 有两条出路，选一条即可：

- **缩短路径**：仓库默认 `.test_runtime` 就是推荐值。测试启动会按 Stage0 最长内容寻址归档
  路径预检，超出 `MAX_PATH` 时给出 `pytest.UsageError`。
- **一次性解除限制**（推荐，管理员一次）：设
  `HKLM\SYSTEM\CurrentControlSet\Control\FileSystem\LongPathsEnabled=1`。本仓 conda 环境的
  `python.exe` 带 `longPathAware` manifest，两者齐备后普通路径不再受 260 限制，
  `conftest.py::_windows_basetemp_problem` 会**实测**到这一点并自动不再拦——它探测文件系统
  本身，不读注册表也不查 manifest（那是两个各自都会变的间接条件）。git 另需
  `git config --global core.longpaths true`，它不吃系统开关。

⚠ 不要改用 `\\?\` 前缀绕过：前缀总能写成功，但前缀建出来的文件**用普通路径读不回来**
（实测 `FileNotFoundError`），那是把明确报错换成「一部分代码看得见、一部分看不见」的静默不一致。
