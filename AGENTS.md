# 项目开发指南

本仓将中文小说经 stage0、stage1、stage2、stage4～stage7 重写成新书，保留情节骨架并替换人名，
最终交付 `stage7/<书名>_rewriting.txt`。开发首先防止错误事实、证据缺失或提前披露被静默接受。
仓内文档、注释、commit 与工程回复使用中文。用户负责提出想法与需求、验收结果和兜底；
agent 负责维护工程上下文、实现与验证，关键取舍和结果直接在会话中说明，不要求用户先读文档。

## 开发入口

按任务读取下列局部指南，再定位相关代码、模块头、契约与测试；无需加载其他目录指南或整份历史文档。
从仓根跨目录工作时也要主动读取对应指南，不能假定工具会自动加载所有子目录文件。

| 工作范围 | 入口与职责 |
|---|---|
| 契约定义 | [contracts/AGENTS.md](contracts/AGENTS.md)：节点、schema、判据与版本变更 |
| 后端实现 | [novel/AGENTS.md](novel/AGENTS.md)：产物、调度、模型调用、服务与恢复 |
| 前端实现 | [web/AGENTS.md](web/AGENTS.md)：页面、数据访问、交互与前端验证 |
| 运行或修改后端测试 | [tests/AGENTS.md](tests/AGENTS.md)：环境、fixture、并行与 corpus |
| 环境、诊断、备份与工程恢复 | [docs/OPERATIONS.md](docs/OPERATIONS.md)：开发维护 agent 按故障定位入口 |

后端固定使用 conda 环境 `novel_refactor`，不要用 base；安装、启动与诊断命令见
`docs/OPERATIONS.md`。前端为 React/Vite/TypeScript，开发命令见 `web/AGENTS.md`。
产品仅有前端自动跑书和人工逐 Stage 两种模式；确定性调度、执行、恢复与交付检查由运行代码负责。

## 开工与协作

1. 先看 `git status --short`，保留已有改动；只处理本任务涉及的内容。
2. 用户已指定的任务直接推进；未指定时从 `docs/REQUIREMENTS.md` 的未完成项选取，不另造活动计划。
3. 现役依据依次为 `docs/BACKEND_CONTRACT.md`、`contracts/` 与 `docs/schema/`、`web/AGENTS.md`。实现只是证据；冲突先核实，改变产品语义、公开契约或架构归属前取得用户裁决。
   `docs/DECISIONS.md` 解释历史缘由，不覆盖现役契约；`docs/LESSONS.md` 按问题或编号检索。
4. 同一行为的契约、producer、consumer、恢复、API/UI、测试和文档同批闭合；完成要检查真实消费者与
   最终盘面，不能只凭函数存在、编译或单测通过。

自动跑书由 RunSupervisor 推进；人工模式由用户在 Stage 页面选择推进动作。受托 Commander/Reviewer
（含 `<immutable-workpack>`）只消费本轮裁决契约与材料；Executor 使用独立 prompt/schema。
这些模型工作单元不加载工程 AGENTS、运维手册或额外盘上小说来补充材料。

## 跨模块边界

- 章唯一键是阅读序整数 `chapter_ordinal`，输出章号等于源章号；不合并、不删除、不拆分、不重排。
- 契约单向流入运行时；产物 I/O 只经 `novel/store.py`，路径只经 `novel/paths.py`。服务只写
  `_service/`，由 CLI 子进程修改产物；服务不能发付费模型请求，前端消费唯一后端判定。
- unknown、未运行、缺失、合法空值、失败与通过必须分开；未经标定的语义保持 `unver` 或人工裁决。
  新增硬闸要同时提供免费重验、人工编辑或等价恢复入口，不用重买结果掩盖恢复缺口。
- 付费与正文外发分别授权，沿用会话中已获授权的范围；付费命令显式 `--yes`。成本只认
  `<base>/_service/ledger.jsonl`，缓存命中单列，未知不作零，reasoning 不重复计入。

## 测试选择与批次验收

**按四层职责组织测试，按改动影响选择范围，同一批次稳定后统一验收。**

| 层级 | 回答的问题 | 验证边界 |
|---|---|---|
| 单元测试 / contract 测试 | 代码有没有按约定工作？ | 函数、模块、schema、判据和接口契约；使用确定性最小样例，覆盖正例、反例和边界 |
| stage 测试 | 每个 stage 有没有完成自己的职责？ | 从阶段输入经真实入口到产物、判定及下游消费，覆盖拒绝与恢复；验证过程及阶段产物符合契约 |
| 回归测试 | 改动有没有破坏以前正常的行为？ | 复用单元、contract、stage 及跨模块集成用例，保留历史 bug 反例；按影响范围做定向回归，达到整批门槛再做完整回归 |
| golden set | 最终生成的新小说到底好不好？ | 使用冻结的代表性源书，经真实工作流生成最终成书，对照源书、质量标准及已验收基线评价结果质量 |

stage 测试覆盖现役 stage0、stage1、stage2、stage4～stage7；stage3 已退役，不补造占位流程。
stage 测试验证过程正确性，golden set 验证最终结果正确性与阅读质量，两者不能相互替代。
回归是对已有用例的选择与复用，不要求为四层复制用例或按目录机械隔离。
实际入口：pytest 的 `--test-layer` / `--test-stage` / `--affected` / `--changed` 选择工程测试，
`python -m tests.golden` 独立执行 golden set；分类目录表、覆盖证据与用法统一见 `tests/README.md`。

golden set 源语料固定使用 `E:\practice_group\Novel_rewriting\Test_data\Workbench`，按现有工具约定通过
`NOVEL_DATA` / `NOVEL_CORPUS` 显式提供；生成产物由 `NOVEL_OUTPUT` 指向独立输出根，不能覆盖源语料。
golden set 用于整个项目的最终质量验收；日常小改和单个 stage 重构不默认运行。冻结样本、质量评价与
现有 corpus 检查的关系见 `tests/README.md`；付费与正文外发仍分别沿用已获授权的范围。

批次是同一目标的一组相关修改，可以有多个检查点提交；提交、局部修复交付、单个 stage 重构交付、
同一本书再次续跑，都不单独触发全仓回归或 golden set。

| 改动范围 | 本轮验证 |
|---|---|
| 说明、注释、测试记录，未改可执行契约 | `git diff --check` 与相关文档校验；不跑完整回归或前端四件套 |
| 边界明确的小改、局部 bug | 先复现目标反例，只跑受影响的定向单元 / contract 测试；涉及阶段行为、消费或恢复路径时补对应 stage 测试 |
| 单个 stage 重构、提示或阶段职责变化 | 跑该 stage 的单元 / contract 与 stage 测试，再按实际依赖补受影响 stage、API/UI 和恢复消费者；上游可复用不可变 fixture，不默认重跑所有 stage |
| R/Q/B、schema、节点、API 形状 | 形状稳定后跑契约 §9.5 五文件及受影响消费链；涉及前端时补其验证，契约继续变化才重跑五文件 |
| 前端或其消费的 API | 受影响测试与 typecheck；数据缝、路由变化补 seams，构建配置、依赖变化补 build |
| 整个项目最终验收 | 先完成工程回归，再使用 golden set 评价最终成书；分别报告过程验证和结果质量结论 |

选择范围前检查 producer、consumer、共享模块与恢复路径，说明受影响范围；不能只按文件名或修改行数选测试。
已有测试无法覆盖目标职责时补最小有效反例，不把缺失覆盖当作无需验证。

含生产代码、可执行契约或测试基础设施变化的批次，在以下门槛按最终改动集合跑一次默认非 corpus 回归：

- 整个项目最终工程验收，或新工作流首次进行全链工程验收、需要建立完整基线。
- 合入／覆盖 main、冻结发布或性能实验候选；同时完成前端 test/build/seams/typecheck。
- 共享持久化、身份、调度、额度、权限、测试基础设施的影响不能由定向测试闭合，或出现范围不明的跨模块失败。

启动完整回归前说明触发条件和定向尚未覆盖的范围；集中修复已知问题，失败后先定向修复，批次稳定再补整轮。
不得并行启动共享同一 basetemp/cache 的 pytest。已通过且相关内容未变的检查不重复执行。

真实验收中途的局部修复，在受影响验证通过、固定代码并遵循旧 owner 停止／新 epoch 规则后，
可以继续已授权验收；原配置续跑不视为新发布，付费、外发与追加额度的授权边界不变。
最终切 main 或发布前仍须闭合整批门槛。

记录测试层级、实际覆盖范围、命令、结果与代码基线；完整回归后只有说明变化可复用结果，生产代码再变时
旧结果只作基线，新增定向结果单列。现有 corpus 检查验证冻结语料与产物的工程约束，不能单凭其通过声称
golden set 质量验收通过。普通回归、corpus 与真实模型质量／性能结论分别报告，不删现役职责反例、缩验收分母或放宽判据。
退役及等价重复测试可以按 `tests/README.md` 的删除规则清理，同步登记与消费者，并说明分母变化。

## 文档职责

- 开发方式由根及所属目录 AGENTS 维护；仅在存在独立开发/验证流程时新增指南，不按目录数量铺文件。
- 关键行为、跨层约束和守卫归属在 `docs/BACKEND_CONTRACT.md`；字段及消费形状在 `docs/schema/` 和代码。
  修改行为时同步负责文档，保留对应反例，不在契约里重复字段手册和退役过程。
- 未完成事项在 `docs/REQUIREMENTS.md`；仍影响选择的理由在 `docs/DECISIONS.md`，复用经验在 `docs/LESSONS.md`。
  临时进度不进 AGENTS，已完成过程保留 Git，不另建同义计划或交接账。
- 操作方法在 `docs/OPERATIONS.md`，产品/界面与人工验收职责在 `web/AGENTS.md`，测试分层与 corpus 规程在
  `tests/README.md`。只更新对应负责文档，不复制多套命令或状态。
- 引用使用稳定编号或 `仓内路径::符号`；删改文档先查代码读取、链接与测试，修复全部实际消费者。

## 交付与维护

- 沿用相邻代码风格与类型标注，保留解释设计原因的模块头。新增 Python 子包同步 `pyproject.toml` 的 packages。
- commit 使用中文 Conventional Commit；交付说明契约影响、验证与限制，UI 变化附截图，真实付费验证单列。
- 不提交 `.env`、输出、缓存、测试临时目录或正文语料。数据工具显式读取 `NOVEL_DATA` / `NOVEL_CORPUS` /
  `NOVEL_OUTPUT`，取不到就报告“没查”。
