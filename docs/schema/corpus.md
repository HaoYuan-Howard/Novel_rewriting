# 现役 schema 语料硬验收 manifest

本契约为两本冻结验收书建立一份可提交、无正文的 current-schema 身份清单：
`2019402867061048656`（17 章）与 `1668963766200045568`（25 章）。书目与付费节点唯一落点是
`contracts/schema/corpus.py::CURRENT_CORPUS_BOOKS` / `::CURRENT_CORPUS_PAID_NODES`，本文不再
手抄第二张节点表。

manifest 不含书名、原文、prompt 或生成正文，只记录：

- sid、归一化源 SHA-1 与章数；
- Stage0～7 预期产物路径、每份 JSON 的精确 current schema version 与文件 SHA-256；
- 冻结产地代码身份、逐节点 production dispatch；
- 每本书的 ledger 行数、wire / local-cache 分区、outcome、模型、思考档位与 token 覆盖；
- 精确 pytest skip allowlist（nodeid + reason）。

成书在 manifest 中登记模板 rel `stage7/{title}_rewriting.txt`，但 SHA-256 取盘上按书名命名的
实体字节；这样既不泄漏书名，又与 `contracts/artifacts.py::ARTIFACT_PATHS['stage7.book']`
保持同一落点。

Stage5 current 盘面必须恰有 `stage5/ch01.json`～`stage5/chNN.json` 与
`stage5/review_1.json`；不得出现旧 `stage5/deep|shallow/` 目录来替代
canonical 章。正式章为 schema v2，Review 为 schema v2；版本逐份从 registry 现算，不能手补
marker 冒充 current producer。

## 生成与验证

只对隔离输出、独立空本地缓存执行：

```powershell
conda run -n novel_refactor python -m tests.corpus_manifest build `
  --base <isolated-current-corpus> `
  --output docs/corpus/current_schema_manifest.json `
  --command "<production dispatch 1>" `
  --command "<production dispatch ...>" `
  --skip-file <recorded-skips.json>

conda run -n novel_refactor python -m tests.corpus_manifest verify `
  --base <isolated-current-corpus> `
  --manifest docs/corpus/current_schema_manifest.json
```

每条 command 必须恰含一次：

```text
-m novel.cli run <paid-node> <sid> --base <isolated-current-corpus> --yes
```

所有节点（包括 `stage5.write` 与 `stage6.semantic`）的冻结 flags 都恰为 `--base --yes`；
`stage5.write` 不再带 `--deep` / `--shallow-rest` / `--mode`，`stage5.semantic` 已退役。
两书 × 八个付费节点 = 当前 16 条 dispatch，但校验器按两个 current 元组现算条数与顺序，
不信本文数字。`preview`、`mark` 或缺少 `run` 的展示命令不能冒充 production dispatch。

最终硬验收显式指定**盘面与 manifest 两个身份**：

```powershell
$env:NOVEL_OUTPUT='<isolated-current-corpus>'
$env:NOVEL_CORPUS_MANIFEST='docs/corpus/current_schema_manifest.json'
conda run -n novel_refactor python -m pytest --require-corpus
```

`--require-corpus` 覆盖默认的省钱 marker，实际收集 `corpus or not corpus`；它关闭式拒绝缺环境
变量 / manifest、书目或产物减少、源 hash / 章数 / schema / 文件字节 / ledger 漂移，以及任何未
精确列入 manifest 的 skip。ledger 必须满足 `wire_attempts + local_cache_hits == rows`，current
corpus 还要求 local cache hit 为 0、每个 dispatch 节点均有非零 wire 行；每个 token 列分别记录
reported/null 行数，分母不闭合就失败。

manifest 的 `commands` 字段必须是 `-m novel.cli run <node> <sid> ... --yes` 的 16 条真实
付费 production dispatch：每书各一条 Stage2 global/blueprint、Stage4 A `design`、
Stage4 B `plan`、Stage5 write、Stage6 hook/semantic、Stage7 rename；Stage3 v2 是免费机械节点，
不进入付费 dispatch，
但仍须在 required artifacts 中存在 current v2。`stage6.semantic` 只带 `--base --yes`
（`--advisory` 已随自动放行门一并删除）；canonical `stage5.write` 也只带 `--base --yes`，
默认补齐当前计划的全部缺章。
ledger 必须对两书、八个付费节点逐一有非零记录并与这 16 条命令对账；Stage4 A/B 是两次
独立付费 dispatch，不能用 B 相的一行账或最终计划替代 A 相的调用与中间产物。
⚠ 这里的 16 = 2 书 × 8 付费节点，**现算不写死**——它曾因 Stage5 semantic 退役从 18
降为 16、又因 Stage3 v2 免费化降为 14，Stage4 拆成独立 A/B 后回到 16。条数以
`contracts/schema/corpus.py` 的 `CURRENT_CORPUS_BOOKS × CURRENT_CORPUS_PAID_NODES` 为准。
`preview`、`mark` 或缺 `run` 的展示命令不能冒充生产 dispatch。

finalise manifest 时可用 `--record-corpus-skips <path>` 收集候选 allowlist；普通测试不自动记录。
allowlist 禁止掩盖 missing corpus、未设 `NOVEL_OUTPUT` 或 0 collected，xfail 也不能静默改记为
skip。

## TODO-21 边界

截至当前活动待办，默认目标 docs/corpus/current_schema_manifest.json 尚未由隔离双书现役重跑生成，
TODO-21 **未闭合**。因此：

- 默认 pytest 或任何“非 corpus 回归”通过数字都不能称为“全量绿”；
- 历史双臂 Stage5 的 31 章盘面、旧 schema 文件与旧 ledger 不能用来 build current manifest；
- 只有隔离双书从 producer 重建、manifest 提交、上述 `--require-corpus` 命令通过后，才能报告
  current-schema 语料硬验收完成。

这份 manifest 只证明现役全链盘面闭合，不比较 Stage5 的旧 event-list 与现役 beat-material
写作契约，也不能证明后者在质量、成本或速度上胜出。Stage5 消费 Stage1～4 上游；全链重跑的
读数不能单独归因给 Stage5。

实现落点为 `tests/corpus_manifest.py`；`tests/conftest.py` 在 `--require-corpus` 时调用同一
`verify`，所以它是长期测试基础设施，不是一次性脚本。
