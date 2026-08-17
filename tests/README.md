# 测试分层

默认测试必须可复现，不读取本地小说产物：

```powershell
conda run -n novel_refactor python -m pytest
```

标记为 `corpus` 的测试是针对冻结生成产物或源语料的集成检查。最终硬验收必须同时提供
隔离的当前 schema 输出盘和与它精确对应、已提交的无正文 manifest，并使用
`--require-corpus`；该模式会覆盖默认 marker，运行 corpus 与 non-corpus 两层，缺书、缺产物、
ledger 漂移、产物任一字节变化或新增 skip 均关闭式失败。manifest 只接受本轮冻结的
`2019402867061048656`（17 章）与 `1668963766200045568`（25 章），要求完整 Stage0--7
正式产物（含最终 `stage7/book.txt`），并逐文件记录 SHA-256：

```powershell
$env:NOVEL_OUTPUT = 'E:\path\to\isolated-current-schema-output'
$env:NOVEL_CORPUS_MANIFEST = (Resolve-Path -LiteralPath 'docs\corpus\current_schema_manifest.json').Path
conda run -n novel_refactor python -m pytest --require-corpus
```

只跑 `-m corpus` 不是最终验收：它不验证冻结 manifest/ledger/精确 skip allowlist。
当前 manifest 尚未生成时，只能报告“非 corpus 回归”，不能声称全量已绿。

manifest 的 `commands` 必须是 `-m novel.cli run <node> <sid> ... --yes` 的 18 条真实
production dispatch：每书各一条 Stage2 global/blueprint、Stage3、Stage4、Stage5
write/semantic、Stage6 hook/semantic、Stage7 rename；两条 semantic 必须保留
`--advisory`，write 固定 `--deep 1,2,3 --shallow-rest`。ledger 必须对两书、九节点逐一
有非零记录并与这 18 条命令对账。`preview`、`mark` 或缺 `run` 的展示命令不能冒充生产
dispatch。

语料 JSON 的盘上原始 `_schema_version` 必须直接等于 artifact registry 当前版本；即使
registry 有 migration，manifest build/verify 也不会先迁移再把旧文件冒充 current。skip
allowlist 不能收录“缺语料 / missing corpus / 0 collected”一类候选，且硬验收若实际收集到
0 条 corpus 测试会失败。纯判据与 prompt 测试应使用
`tests/factories.py` 中的确定性构造器，不得随机选择一本本地小说。测试若直接写注册 JSON
产物，须先用 `contracts.schema.encode_artifact()` 包装逻辑 payload。

Windows 上不要把 `--basetemp` 指到深目录。测试启动会按 Stage0 最长内容寻址归档路径预检，
超出 `MAX_PATH` 时直接给出 `pytest.UsageError`；仓库默认 `.test_runtime` 即推荐短路径。
