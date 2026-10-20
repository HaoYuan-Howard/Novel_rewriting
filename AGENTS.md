# 项目开发指南

本项目将中文小说重写成新书，保留情节骨架并替换人名，交付 `stage7/final/{书名}_rewriting.txt`。
产品为全自动单模式：上传即授权开跑，Stage 页只读监控。调度、执行、恢复和交付检查由运行代码负责。
工程回复、文档、注释与 commit 使用中文；关键取舍和验收结果直接在会话说明。

## 开发入口

按任务读取所属目录指南，再定位契约、调用链和测试；跨目录时主动补读，不加载整份历史资料。

| 范围 | 入口 |
|---|---|
| 契约、schema、判据 | [contracts/AGENTS.md](contracts/AGENTS.md) |
| 后端、模型调用、产物与恢复 | [novel/AGENTS.md](novel/AGENTS.md)；[Stage0](novel/stages/stage0/AGENTS.md)／[Stage2](novel/stages/stage2/AGENTS.md) 另读局部指南 |
| 前端与 API 消费 | [web/AGENTS.md](web/AGENTS.md) |
| 后端测试与 fixture | [tests/AGENTS.md](tests/AGENTS.md) |
| 环境、启动、诊断与归档 | [docs/OPERATIONS.md](docs/OPERATIONS.md) |

后端固定使用 conda 环境 `novel_refactor`，不用 base；前端命令见 web 指南。
现役节点唯一登记在 `contracts/invariants.py::NODES`，产物与字段见 `contracts/` 和 [架构规格](docs/schema/str06.md)。

## 开工与协作

1. 先看 `git status --short`，保留已有改动，只处理本任务范围。
2. 用户指定任务直接推进；否则只从 [活动计划](docs/REQUIREMENTS.md) 选未完成项，无活动项时不自行立项。
3. 本地、可逆的实现选择先查仓内约定再自行解决；产品语义、公开契约、架构取舍或不可逆变化先说明并取得用户裁决。
4. 依据依次为根硬约束、[后端契约](docs/BACKEND_CONTRACT.md)、`contracts/` 与 `docs/schema/`、所属目录指南。
   实现是证据；冲突先核实。[历史裁决](docs/DECISIONS.md) 解释缘由，不覆盖现役契约。
5. 完成 producer/compiler、consumer、恢复、API/UI、测试与文档的相关变更，检查真实入口、下游消费和最终盘面；
   编译或单测通过不能单独证明功能完成。不为缩小补丁保留本次变更已淘汰的路径。

## 架构不变量

以下规则全部为 HARD；合并展示保留原编号，供契约和代码引用。

| 编号 | 规则 |
|---|---|
| I-01／I-02 | 模块职责单一。orchestrator 只编排与许可；compiler 只做确定性转换；validator 只验证报告；repository 只持久化；语义判断与生成由 semantic unit 承担。 |
| I-03／I-04／I-05 | 依赖单向、禁止循环；Stage 不得 import 后续 Stage 实现，跨 Stage 只经正式 contract/artifact；shared/common 不反向依赖具体 Stage。 |
| I-10／I-11／I-25 | Stage 间以 schema/contract 为边界；每类正式产物只有一个 canonical schema 和明确唯一 writer。 |
| I-12／I-38 | 下游不依赖 debug、prompt 临时字段或 reasoning；可观测元数据不进入核心领域 schema，领域事实除外。 |
| I-13／I-14 | schema 变化显式版本化，破坏性变更默认重建（S-06～S-10）；optional 字段不充当隐式控制流。 |
| I-15／I-20／I-21 | 可机械推导的转换、引用、ID、ordinal、统计、路径和 hash 由 compiler 计算；LLM 负责语义，compiler 不从自然语言猜补缺失语义。 |
| I-16／I-17 | 每类信息只有一个真相源，派生物不得反写 canonical；数据库、文件与内存不得各自决定同一状态。成本权威为 `<base>/_service/ledger.jsonl`，其它统计均为派生。 |
| I-22／I-23／I-24 | 区分 semantic output 与 canonical output；核心 domain 尽量 pure，I/O、网络和模型调用集中于明确边界；import 无业务副作用，helper 不偷偷落盘，validator 不改输入。 |
| I-30 | 支持幂等重跑：相同正式输入与配置产生语义等价的确定性输出；当前契约、相同请求身份下可复用合法已购回答，不要求模型逐字重现。 |
| I-31／I-32／I-33 | 重试不重复业务实体，局部失败定位到 node/item；状态机显式，文件存在只表明 I/O 事实；业务不依赖隐藏全局状态。 |
| I-34／I-35 | 契约违规立即失败，禁止吞异常当通过，未知保持 unknown；fallback 显式可观测，repair 独立于 validator。 |
| I-36／I-37 | 机械验证与语义判断分离；错误带 stage/node/item/context。昂贵节点记录输入输出、provider/model、耗时、token/cost 与失败类别，logging 不改变业务。 |
| I-40 | 章唯一键为阅读序整数 `chapter_ordinal`；输出章号等于源章号，不合并、删除、拆分或重排。 |
| I-41 | 契约单向流入运行时；产物 I/O 经 repository（`novel/store.py`），路径经 `novel/paths.py`；服务只写 `_service/`，由 CLI 子进程写产物，服务不发付费请求；前端只消费后端判定。 |
| I-42 | unknown、未运行、缺失、合法空值、失败和通过分别建模；未标定语义保持 `unver` 或人工裁决。新增硬闸同时提供免费重验、人工编辑或等价恢复入口。 |
| I-43 | 付费与正文外发分别授权，付费显式 `--yes`；缓存命中单列，未知用量不作零，reasoning 不重复计入。 |

semantic unit 仅使用独立 prompt/schema，不加载工程 AGENTS、skill、运维手册或额外盘上小说。
Agent 仅指整书代跑编排；`kind:"legacy"` 是现役直连 LLM 的稳定 wire 名，不因名称被视为旧实现。

## 重构与兼容

| 编号 | 规则（全部 HARD） |
|---|---|
| S-01／S-02 | 新代码不依赖 legacy 实现内部结构；旧盘面适配只按 S-10 例外实施，新系统不反向适配旧实现。 |
| S-03／S-10 | 兼容须有用户明确的旧根续跑或旧数据复用需求，优先评估一次性迁移；逻辑集中，需求依据、范围与删除条件登记于活动计划。旧实现、旧测试和旧登记不构成续保理由，重构时不自动继承。 |
| S-04／S-05 | 不新增双写真相源，不永久保留新旧两条主路径；新路径成为权威后，旧路径退出维护并删除，退役流程不得复活。 |
| S-06 | 运行代码默认只支持当前契约；破坏性变更显式升版，producer/compiler、consumer、恢复、API/UI、测试与文档同批更新。 |
| S-07 | 破坏性契约变更后旧根只读归档、新根重新生成；默认不提供跨版本续跑、旧版本候选采用或同根迁移。 |
| S-08 | 已购响应、成本账与成书等历史证据保留原字节，不当派生缓存删除；保存证据不要求新版运行时继续解码旧格式。 |
| S-09 | 保留版本拒识、来源绑定、幂等、当前版本中断恢复，以及相同请求身份下合法已购结果的免费重放。 |

## 测试选择与批次验收

- I-50：unit/contract 保护函数与边界，stage 验证真实入口到产物及下游消费，回归保护既有行为，golden set 验证最终成书质量；过程与质量结论分开。
- I-51／I-52：普通测试不调用真实线上 LLM；不为旧测试恢复退役耦合，以现役契约和有效职责反例为准。
- 先查 producer/compiler、consumer、共享模块和恢复路径，按影响选择测试；具体选择器、fixture 与命令见 [tests/README.md](tests/README.md) 和测试指南。
- 现役 stage 测试覆盖 stage0、stage1、stage2、stage4、stage5、stage7，不补造已退役 stage3／stage6。

| 改动 | 验证范围 |
|---|---|
| 文档、注释，未改可执行契约 | `git diff --check` 与相关文档校验 |
| 局部 bug 或单个 Stage | 定向 unit/contract；涉及阶段行为、下游消费或恢复时补相关 stage 与消费者测试 |
| R/Q/B、schema、节点或 API 形状 | 形状稳定后跑后端契约 §9.5 五文件及受影响消费链 |
| 前端或其 API | 受影响测试与 typecheck；数据缝/路由变化补 seams，构建配置变化补 build |
| 整项目最终验收 | 先工程回归，再 golden set，分别报告工程正确性与成书质量 |

含生产代码、可执行契约或测试基础设施变化的批次，在整项目验收、合入 main/冻结发布，
或共享基础设施影响无法由定向测试闭合时，按最终改动集合跑一次默认非 corpus 回归；涉及前端补 test/build/seams/typecheck。
子批次、检查点提交、schema 修改或新增测试登记本身不触发完整回归；精确消费者可闭合时报告定向覆盖，不排除真正未知影响。
启动完整回归先说明命中的门槛；选择器扩大范围先核对原因。不得并行运行共享 basetemp/cache 的 pytest，
不得删除现役职责反例、缩验收分母或放宽判据来提速。

golden set 固定源语料为 `E:\practice_group\Novel_rewriting\Test_data\Workbench`，由 `NOVEL_DATA`／`NOVEL_CORPUS` 显式提供；
`NOVEL_OUTPUT` 指向独立输出根，不覆盖源语料。日常小改和单 Stage 重构不默认运行 golden。

## 文档职责

- 根与局部 AGENTS 只写长期开发规则和任务入口；独立流程才新增局部指南，不写批次、版本快照或历史流水账。
- 产品行为与守卫归属在后端契约，字段与消费形状在 `contracts/` 和 `docs/schema/`；操作方法在运维手册。
- 活动计划只记当前范围、未完成批次、验收与必要兼容例外；完成记录进提交说明，历史裁决进 DECISIONS。
- 具体设计原因写在所属模块头、测试或对应规格中；引用优先用稳定编号或 `仓内路径::符号`。

## 交付与维护

- 沿用相邻代码风格与类型标注，保留解释设计原因的模块头；新增 Python 子包同步 `pyproject.toml` 的 packages。
- commit 使用中文 Conventional Commit；交付说明改动、契约影响、验证与限制；UI 变化附截图，真实付费验证单列。
- 不提交 `.env`、输出、缓存、测试临时目录或正文语料。
- 数据工具显式读取 `NOVEL_DATA`／`NOVEL_CORPUS`／`NOVEL_OUTPUT`，取不到就报告“没查”。
