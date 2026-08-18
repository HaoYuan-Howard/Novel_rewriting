# 现役 schema 语料硬验收 manifest

最终硬验收会在下方 build/verify 命令给出的路径生成一份已提交、不含正文的语料身份清单，
覆盖冻结的两本书：`2019402867061048656`（17 章）与 `1668963766200045568`（25 章），
要求完整 Stage0～7 正式产物（含最终 `stage7/book.txt`）。它不含书名、原文、prompt、
生成正文，只逐文件记录 SHA-256 与验收身份：

- 每本书的 `sid`、归一化源 SHA-1 与章数；
- Stage0～7 每份预期产物路径，及每份 JSON 产物的精确 schema version；
- 冻结的产地代码身份与精确重建命令；
- 每本书的账本调用数、token 汇总、provider 缓存命中/未命中覆盖、模型/思考档位分布与结果；
  provider 为 `null` 与显式上报为零分别计数；
- 精确的 pytest skip allowlist（`nodeid` 与理由）。

> 实现在 `tests/corpus_manifest.py`。
> 它不是一次性脚本：`tests/conftest.py` 在模块级 import 它的 `verify`，
> `--require-corpus` 那条闸就是它把着的 ⇒ 它是**测试基础设施**，
> 与 `tests/corpus.py` / `tests/sandbox.py` 同族。

只针对隔离的输出目录 build 与 verify：

```powershell
conda run -n novel_refactor python -m tests.corpus_manifest build `
  --base <isolated-current-corpus> `
  --output docs/corpus/current_schema_manifest.json `
  --command "<exact rebuild command>" `
  --skip-file <recorded-skips.json>

conda run -n novel_refactor python -m tests.corpus_manifest verify `
  --base <isolated-current-corpus> `
  --manifest docs/corpus/current_schema_manifest.json
```

硬验收的调用方式：

```powershell
$env:NOVEL_OUTPUT='<isolated-current-corpus>'
conda run -n novel_refactor python -m pytest --require-corpus
```

`--require-corpus` 会覆盖默认的省钱 marker 表达式，同时收集 `corpus` 与非 `corpus` 两类测试；
它拒收缺环境变量或缺 manifest、书目/产物减少、源文本或章数漂移、schema version 漂移、ledger
漂移，以及任何未精确列在 manifest 里的 skip。ledger 的行分区（`wire + local-cache`、结果、
模型与思考档位）必须逐一闭合；每个 token 列分别记录「上报/为空」的行数，上报行数为零的列
不能声称有非零总数。pytest 的 `xfail` 不会被静默改记成 allowlist 里的一项 skip。

manifest 的 `commands` 字段必须是 `-m novel.cli run <node> <sid> ... --yes` 的 16 条真实
production dispatch：每书各一条 Stage2 global/blueprint、Stage3、Stage4、Stage5
write/semantic、Stage6 hook/semantic、Stage7 rename；`stage6.semantic` 只带 `--base --yes`（`--advisory` 已随自动放行门一并删除），
write 固定 `--deep 1,2,3 --shallow-rest`。ledger 必须对两书、八节点逐一有非零记录并与这
16 条命令对账。⚠ 这里的 16 = 2 书 × 8 付费节点，**现算不写死**——它曾是 18，
`stage5.semantic` 于 2026-08-17 退役后没跟上。条数以 `contracts/schema/corpus.py` 的
`CURRENT_CORPUS_BOOKS × CURRENT_CORPUS_PAID_NODES` 为准。`preview`、`mark` 或缺 `run` 的展示命令不能冒充生产 dispatch。

语料 JSON 的盘上原始 `_schema_version` 必须直接等于 artifact registry 当前版本。这道闸**独立于
`decode_artifact`**：盘面清点要的是盘上那个数字，不是解码器愿意接受的那个数字。（本仓现已没有
迁移机制，全表恒 v1；这条闸原本防的是「迁移之后冒充 current」，梯子拆掉后它继续守住直比。）skip
allowlist 不能收录“缺语料 / missing corpus / 0 collected”一类候选，且硬验收若实际收集到
0 条 corpus 测试会失败。

finalise manifest 时用 `--record-corpus-skips <path>` 采集候选 allowlist；这个记录动作是
显式的，普通测试跑不会自动开启。
