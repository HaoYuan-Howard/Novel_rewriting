# 流水线成本与耗时的配对验收

本协议服务 `docs/REQUIREMENTS.md` TODO-49。工程测试、语料验收与性能实验分开报告。
性能数字只从 `tools/workflow_metrics.py` 的当前 `novel-workflow-metrics-v3` 冻结报告读取；
既存报告若需重测，须回到对应冻结盘面重新采集，不能只改版本字段。成本只来自原 ledger，
reasoning 已包含在 completion 内，不再相加。工具不运行模型，也不创建生产审批。

基线 `24e4cf4` 的工作单元账是原始 v1。读数器仅在统计模块中按冻结形状只读解析它，
用于首轮、返修及网络尝试归属；候选的 v2 账继续调用当前 schema，报告显式列出
`performance.work_unit_ledger_versions`。同书混合版本、未知格式或身份/轮次/证据损坏时，
已知成本仍保留，内容阶段保持未知并禁止计算对应节约。此读取不修改旧根、不补字段，
不授予恢复、额度或生产写入权限；现役运行入口继续拒绝旧根。

## 实验前登记

首轮、局部返工、故障恢复和并发对照分别建清单；每份清单固定三本书，每书三次配对。
失败和未知的实验也必须保留原位置，不能用后来的成功实验替换。每书第一次及第三次先跑基线，
第二次先跑候选；同对的两臂顺序执行，输出目录分别隔离，三次重复不复用该书的输出。

预登记冻结各臂代码身份、源文 SHA-256、模型与 effort 声明、非目标并发、篇幅目标带、
质量样例和输出命名空间。模型声明覆盖实验会用到的全部节点，新增人物节点在基线中没有执行
也是明确的拓扑差异，不能从 Stage2 的候选总消耗中扣除。普通对照保持目标节点并发相同；
并发实验单独指定现役 `target_node`，比较 4 与 6 或 8，不把两个候选并发混入同一份九组清单。

在 `novel_refactor` 环境导出当前输入 schema：

```powershell
python tools/workflow_pairs.py --emit-schema
```

清单 `schema=novel-workflow-pairs-v1`，包含 `registered_at`、`scenario`、`target_node`、
三项 `books`。每本书含 `sid` 和三项 `pairs`；每对含 `repeat=1/2/3`、`baseline`、`candidate`。
每臂必须有唯一 `run_id`、`report_path`、报告完整字节 SHA-256、带时区的开始/结束时刻、
`freeze` 与可空的 `review`。未完成时结束时刻和收据保持 null。报告路径由清单显式指定；
工具不搜语料目录、不向输出根写入任何内容。输入清单含本地路径，应按实验材料保管。

所有 `freeze` 哈希都必须来自实际冻结材料：源文件按原始字节计算，配置和样例索引按
UTF-8、排序键、无多余空白的 JSON 计算。`output_namespace_sha256` 绑定实验实际使用的独立
书输出目录。原始配置、样例索引与目录对应表另存本地供核验；不能拿固定占位哈希冒充冻结。
同书三次的源、篇幅目标和质量样例必须一致；两臂及全部重复的模型声明与非目标并发保持一致。

## 固定质量样例与收据

实验前从原三书登记样例及证据锚，至少覆盖：事实遗漏、因果倒置、人物进出状态、埋点与揭示、
同章揭示场景重排、普通对白改述、承诺/对象/时间/数字及必要字面例外。按完整事实义务取样，
不截断正文或减少事实数量来获得较小输入。样例索引保存来源及完整 SHA-256；性能报告只带哈希。

每臂完成后，由人工核对配置和输出，形成 `ReviewReceipt`：`reviewer_kind=human`、具名 actor、
理由、带时区的核验时刻，以及报告、freeze 和最终输出的完整哈希。`freeze_sha256` 使用
`tools/workflow_pairs.py::freeze_sha256` 的唯一序列化口径。收据的配置状态为
`verified/mismatch/unknown`，完成状态为 `complete/failed/incomplete/unknown`。

质量逐项填写 `facts/causality/states/disclosure/dialogue/body_length/first_landing_rate/delivery_rate`，
每项只接受 `not_worse/worse/unknown`。这些是人工实验结论，不能从机械全绿、Agent 自述或 token
减少推导。未经标定的语义判断保留 unknown。缺任何维度、收据过期、输出未完成或质量退化，
该臂仍展示已知消耗，但不能贡献节约结论。

## 采集与比较

使用已有读数入口，对每臂指定准确 run，将结果放在生产输出根之外：

```powershell
python tools/workflow_metrics.py --base <独立输出根> --sid <SID> --run-id <RUN_ID> --json <根外报告路径>
python tools/workflow_pairs.py --manifest <九组清单.json>
python tools/workflow_pairs.py --manifest <九组清单.json> --json
```

比较工具校验报告完整字节、schema、SID、run、唯一干净代码、ledger 完整性、冻结条件和具名收据。
配置和语义质量的实际核验责任仍在人工收据；工具不声称从哈希独立证明了该语义结论。
先逐书展示三对读数及中位数，再汇总九对。某指标有缺失或不合格配对时，保留完整分母，
不给剔除坏样本后的“验收中位数”。逐对原值、绝对变化、可计算的观察下降率和拒绝原因均在 JSON 中。
零基线发生增长时绝对变化照常显示，百分比未定义，不记为节约。

`performance.stages` 与 `performance.nodes` 使用同一聚合器。节点计入其 Executor 与 Commander；
`chapter_executor_usage` 只统计该章实际 Executor 调用，失败及重试均在内。Blueprint
`chapter_total_tokens_p50` 是这些单章总量的中位数，不能把书级 Commander 平均摊进每章。
缺任一章的用量时不出该中位数。Commander 在阶段和节点总量中完整保留。

活跃墙钟取完整 CLI 执行区间与 Commander 调用区间的并集，跨并发重叠不重复计算。
`observed_elapsed_seconds` 则包含这些区间之间的等待。累计调用 latency 仅作另一个观测口径，
不代替节点墙钟。Stage5 首轮输入必须有完整工作单元归属；归属未知时不能因为首轮分组为空而算成零。

首轮目标沿 TODO-49：Stage4 token/活跃墙钟下降 30%/20%；Stage2 token 下降 20%，阶段墙钟不恶化；
Blueprint 单章 token 中位数及节点活跃墙钟各下降 15%；Stage5 首轮输入下降 20%，阶段 token/
活跃墙钟各下降 15%。局部返工单独比较，Stage5 token 目标下降 20%。并发对照的节点活跃墙钟
目标下降 20%，同时保留 token、失败调用和网络重试；工具默认按无增长检查，不隐含容许退化。
故障恢复只展示观察读数，不复用首轮或局部返工的节约结论。

当前尚无本轮三书实验材料和人工质量收据，不能据此宣称已冻结真实样例或已达性能目标。
付费执行、正文外发及最终整根归档继续走既有授权入口；本工具不能代替那些授权。
