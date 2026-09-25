# Schema 阅读入口

本目录维护字段的业务含义、证据要求、生产和消费边界。类型、必填项、取值域、当前版本和路径
以对应代码定义为准；不手写另一份版本总表。开发时按任务选一份，再追 producer、consumer 和反例。

| 文档 | 负责的语义 |
|---|---|
| [stage0](stage0.md) | 剔除决定、原文身份与 clean 的确定性物化 |
| [stage1](stage1.md) | 原文分章、开篇文案及即时章号诊断 |
| [characters](characters.md) | 人物主体、提及证据与称名投影 |
| [stage2](stage2.md) | Global grounding、Blueprint 原子事实、统一机械准入与生成依赖 |
| [stage4](stage4.md) | A 盲设计、B 证据绑定、计划与写作上下文 |
| [stage5](stage5.md) | canonical 正文、Review、人工改稿、trim 与授权改写恢复 |
| [stage7](stage7.md) | 改名决策、替换和可交付小说 |
| [_common](_common.md) | provenance、freshness、用量、归档及共有字段 |
| [_report](_report.md) | 当前 CheckReport 的输出语义 |
| [_orchestration](_orchestration.md) | 作业、Supervisor、执行 epoch、审批与运行记录 |
| [_api](_api.md) | HTTP 读写协议与前端消费约束 |
| [work_units](work_units.md) | 工作单元、内容轮、恢复来源和累计额度 |
| [corpus](corpus.md) | current-schema 语料 manifest 与硬验收分母 |
| [evaluation](evaluation.md) | 语义候选证据、统计边界与人工裁决 |
| [style_transfer_validation](style_transfer_validation.md) | 文风保留的验收对象和测量边界 |

## 类型、版本与路径

`contracts/artifacts.py::ARTIFACT_PATHS` 登记产物路径；`contracts/schema/__init__.py::ARTIFACT_SPECS`
登记 JSON 载体及当前版本。普通对象使用 `_schema_version`，RootModel 使用带版本的 payload 信封。
只接受登记的当前形状，不自动迁移；shape break 和重建 producer 由
`tests/test_artifact_versions.py::_SHAPE_BREAKS` 对账。不可手补版本、别名或来源字段冒充当前证据。
服务账的历史读取规则由各自协议显式定义，不能套用产物重建规则重签旧账。

## 证据与持久化

文档中的 `三源` 表示契约、代码与实物均有证据；`盘·码` 表示观察与代码有据、契约尚须核实；
`契·码` 表示契约与代码有据、尚无实物样本；`⚠冲突` 须先裁决。观察到的出现率不等于必填或非空，
不能用少数成功样本推断全取值域。这些标签说明证据来源，不自动代表当前 corpus 验收通过。

只有无法从当前上游重建的主体、人工决定或实际用量才需持久保存。可确定性重算的展示投影优先现算，
避免两份同源数据互相竞争。类型和代码也不能证明模型语义质量。

反推工具为 `tools/derive_schema.py`，只从显式 `NOVEL_OUTPUT` 读取，未配置就报告未查。
含人工修订的产物还须看 `_rounds/`：只看修好后的正式稿，会漏掉被修走的异常取值。
