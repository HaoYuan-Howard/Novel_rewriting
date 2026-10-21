# 当前三层产物语料验收

冻结书目仍为两本17／25章源书，SID与章数唯一登记于
`contracts/schema/corpus.py::CURRENT_CORPUS_BOOKS`，不读取旧输出补足当前分母。
当前 manifest_version=2、contract_version=story-v1；旧清单拒识，输出在独立新根重建。

清单可提交且不含书名、正文或prompt：仅保存SID、归一化源SHA-1、章数、每份产物的
相对路径、当前版本、原始文件SHA-256、冻结代码身份、真实生产dispatch与ledger汇总。
成书路径记模板 `stage7/final/{title}_rewriting.txt`，摘要取实际文件原字节。

正式表面由 `contracts/schema/corpus.py::required_current_corpus_artifacts` 定义：
Stage0／1、SourceStory及观察、NameMap／PreservationContract、全书与逐章目标计划、
TargetStory／AdaptationMap、逐章和Hook写作包、正文与机械接受回执、最终清单及成书。
章集合严格等于源阅读序，正文与回执成对存在。编译器和组装器的免费产物同样必备。

Stage0／1固定数字schema信封；三层故事产物直接携带必填 `contract_version: story-v1`，
不额外增加 `_schema_version`。两种现役载体都经同一注册表版本拒识。
当前运行证据只允许 `runtime_evidence_rel` 登记的固定路径和家族；保留原字节hash，
不把它们当领域模型。未知JSON、已退役路径、INVALID或错版均拒绝。

每个付费owner的派发坐标显式冻结于 `CURRENT_CORPUS_PAID_NODES`，并与NODES精确对账。
associate无待决项时必须免费完成；它可以没有wire行，其余必需付费owner均须有非零记录。
禁止为满足清单强迫一次无意义调用。成本来自唯一ledger，token的reported与null分别计数。
真实corpus要求独立空缓存生成，local cache hit为0；付费、源文外发仍分别授权。

## 生成与验证

```powershell
conda run -n novel_refactor python -m tests.corpus_manifest build `
  --base <isolated-current-corpus> `
  --output docs/corpus/current_schema_manifest.json `
  --command "<actual production dispatch>" `
  --skip-file <recorded-skips.json>

conda run -n novel_refactor python -m tests.corpus_manifest verify `
  --base <isolated-current-corpus> `
  --manifest docs/corpus/current_schema_manifest.json
```

每条command恰含一次 `-m novel.cli run <paid-node> <sid> --base <output> --yes`，
覆盖冻结书目乘当前owner闭集。条数与顺序由登记现算；preview、缺run、重复或附退役flag均拒绝。

最终硬验收必须同时提供盘面与清单：

```powershell
$env:NOVEL_OUTPUT='<isolated-current-corpus>'
$env:NOVEL_CORPUS_MANIFEST='docs/corpus/current_schema_manifest.json'
conda run -n novel_refactor python -m pytest -n 0 --require-corpus
```

`--require-corpus`强制检查corpus，不以默认省钱marker跳过；缺环境、缺manifest、减少书目、
源／版本／字节／ledger漂移、0条corpus或未精确登记的skip全部失败。skip allowlist不得掩盖
语料缺失或未设置NOVEL_OUTPUT；xfail不改记为skip。

本批未购买真实模型回答，也未生成当前冻结双书清单。非corpus工程回归与合成全链通过不能
报告为语料验收完成。corpus证明当前盘面闭合；人物转变、因果、场景衔接和自然推进还须经
`tests/golden.py`的冻结六维质量评价逐章提供证据。未经评价保持unver。

`python -m tools.story_metrics --base <当前输出根> --sid <源书ID>`只读不可变首次接受证据与权威成本账。
`first_response_direct_pass_rate`要求首次尝试就是接受的完整结果；材料控制与格式失败不算直接可用。
`first_semantic_candidate_pass_rate`只统计首个完整业务形状候选，材料控制、上游问题、格式响应及窄修次数另列，未知分母不填零。
免费重验不能覆盖首次结论。过程读数不能证明事实理解或文学质量；离线事实标注见 `tools/story_facts.py`。
