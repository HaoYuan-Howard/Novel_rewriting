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
| 后端实现 | [novel/AGENTS.md](novel/AGENTS.md)：产物、调度、模型调用、服务与恢复；Stage0 局部见 [novel/stages/stage0/AGENTS.md](novel/stages/stage0/AGENTS.md)；新链引擎见 `novel/stages/stage2|4|5|7/`（Stage2 局部指南见 `novel/stages/stage2/AGENTS.md`） |
| 前端实现 | [web/AGENTS.md](web/AGENTS.md)：页面、数据访问、交互与前端验证 |
| 运行或修改后端测试 | [tests/AGENTS.md](tests/AGENTS.md)：环境、fixture、并行与 corpus |
| 环境、诊断、备份与工程恢复 | [docs/OPERATIONS.md](docs/OPERATIONS.md)：开发维护 agent 按故障定位入口 |

后端固定使用 conda 环境 `novel_refactor`，不要用 base；安装、启动与诊断命令见
`docs/OPERATIONS.md`。前端为 React/Vite/TypeScript，开发命令见 `web/AGENTS.md`。
产品为全自动单模式：上传即授权开跑，Stage 页只读监控。Stage6 整层已随 STR-06
切换退役（三候选改选面不复存在；Hook 的必要选择进 Stage4 正式 Design）。确定性
调度、执行、恢复与交付检查由运行代码负责。

## 目标角色词典

绞杀式重构以语义最确切的名字为准；代码与文档逐步统一，不以历史别名继续扩展。

| 目标名 | 职责 | 过渡期可能仍出现的旧名 |
|---|---|---|
| **orchestrator** | 只编排节点与许可，不承载业务语义 | RunSupervisor、queue、operator |
| **compiler** | 确定性转换与归一；不做新的语义推断 | producer 内的 compile / renormalize |
| **validator** | 只验证并报告；不偷偷修复或改写输入 | judge 机械臂、check、preflight |
| **repository** | 只处理持久化与路径；不决定业务状态 | store、paths |
| **semantic unit** | LLM 负责的语义判断与生成 | LLM executor / profiled run |
| **Agent** | 仅指整书代跑编排（`/agent/runs`） | 禁止指 executor、CLI driver、session、provider |

现役付费执行唯一走直连 LLM。wire 字段 `kind:"legacy"` 是**稳定传输名**，不是「旧实现」；
UI 称「模型执行配置」。禁止把「不得依赖 legacy implementation」误读成禁用该 wire。

## 架构不变量

等级：`HARD` 必须遵守；`GOAL` 是绞杀目标，过渡例外须在 `docs/REQUIREMENTS.md` 登记删除条件。
产品行为、R/Q/B 与可执行守卫仍以 `docs/BACKEND_CONTRACT.md` 与 `contracts/` 为准；
本表管代码形状、依赖与真相源。冲突先核实，改产品语义或公开契约前取得用户裁决。

### 职责与依赖

| 编号 | 等级 | 规则 |
|---|---|---|
| I-01 | HARD | 一个模块有一个主要职责；禁止把「读输入 + LLM + schema 修复 + 落盘 + 状态更新」缠在同一抽象层。允许薄编排按固定阶段调用纯函数。 |
| I-02 | HARD | orchestrator 只编排；compiler 只做确定性转换；validator 只验证；repository 只持久化。 |
| I-03 | HARD | 依赖单向；禁止循环依赖。上层可依赖下层抽象，下层禁止反向 import 上层。 |
| I-04 | HARD | Stage N 不得 import Stage N+1 实现。跨 Stage 只经正式 contract / artifact，禁止直接调用对方内部函数。 |
| I-05 | HARD | shared/common 不得反向依赖具体 Stage。禁止为复用一个函数把本应独立的模块耦合起来。 |

### 数据契约与真相源

| 编号 | 等级 | 规则 |
|---|---|---|
| I-10 | HARD | Stage 间接口以 schema/contract 为边界，不以 Python 内部对象为边界。 |
| I-11 | HARD | 每类正式产物有唯一 canonical schema；禁止多模块各自定义「几乎一样」的同一领域模型。 |
| I-12 | HARD | 下游不得依赖上游 debug、prompt 临时字段或 LLM reasoning 中间字段。 |
| I-13 | HARD | schema 变化必须显式版本化，或提供登记过的迁移层；本仓无隐式通用迁移。 |
| I-14 | HARD | optional 字段不得充当隐式控制流。 |
| I-15 | HARD | 可机械推导的派生字段由 compiler 计算，不让 LLM 重复生成。 |
| I-16 | HARD | 每类信息只有一个 authoritative source。cache、compiled view、index、debug artifact 皆为派生物，不得反向覆盖 canonical。 |
| I-17 | HARD | 同一状态不得由数据库、JSON 文件与内存状态各自决定。成本权威是 `<base>/_service/ledger.jsonl`；其它统计只能派生。 |

### 语义、确定性与副作用

| 编号 | 等级 | 规则 |
|---|---|---|
| I-20 | HARD | LLM 负责语义判断；能用排序、映射、聚合、引用解析、schema normalization 完成的工作不得交给 LLM。 |
| I-21 | HARD | LLM 不生成可机械推导的 ID、ordinal、统计量、路径、hash。compiler 不得根据自然语言猜测缺失语义。 |
| I-22 | HARD | semantic output 与 canonical output 必须区分。 |
| I-23 | HARD | 核心 domain 函数尽量 pure。文件写入、网络、LLM API 等副作用集中在明确边界。 |
| I-24 | HARD | import 时禁止执行业务副作用；helper 禁止偷偷落盘；validator 禁止修改输入对象。 |
| I-25 | HARD | 一个 artifact 有明确唯一 writer。 |

### 执行、失败与可观测

| 编号 | 等级 | 规则 |
|---|---|---|
| I-30 | HARD | Stage 应支持 idempotent rerun。确定性段：相同正式输入 + 相同配置 → 语义等价正式输出。付费语义段允许在相同请求身份下历史采用，不要求逐字复现。 |
| I-31 | HARD | 重试不得产生重复业务实体。partial failure 必须定位到明确 node/item，不得污染整条 pipeline 状态。 |
| I-32 | HARD | Stage 状态机必须显式。盘上「文件是否存在」只作 I/O 事实，不得单独充当多分支业务状态。 |
| I-33 | HARD | 业务逻辑不得依赖调用顺序之外的隐藏全局状态。 |
| I-34 | HARD | contract violation fail fast。禁止 `except Exception: pass`。未知失败保持 unknown，不得吞成通过。 |
| I-35 | HARD | fallback 必须显式、可观测。repair 是独立步骤（FREE entry / recovery），不得混入 validator。 |
| I-36 | HARD | mechanical validation 与 semantic judgment 分离。错误须保留 stage/node/item/context，不能只有字符串 traceback。 |
| I-37 | HARD | 昂贵节点须可观测：输入、输出、provider/model、耗时、token/cost、failure kind。logging 不得改变业务行为。 |
| I-38 | HARD | observability metadata 不进入核心领域 schema，除非本身是领域事实。debug artifact 不得成为下游正式依赖。 |

### 产品跨模块边界（现役）

| 编号 | 等级 | 规则 |
|---|---|---|
| I-40 | HARD | 章唯一键是阅读序整数 `chapter_ordinal`；输出章号等于源章号；不合并、不删除、不拆分、不重排。 |
| I-41 | HARD | 契约单向流入运行时；产物 I/O 经 repository（现 `novel/store.py`），路径经 `novel/paths.py`。服务只写 `_service/`，由 CLI 子进程改产物；服务不能发付费模型请求；前端消费唯一后端判定。 |
| I-42 | HARD | unknown、未运行、缺失、合法空值、失败与通过必须分开；未经标定的语义保持 `unver` 或人工裁决。新增硬闸须同时提供免费重验、人工编辑或等价恢复入口。 |
| I-43 | HARD | 付费与正文外发分别授权；付费命令显式 `--yes`。缓存命中单列，未知不作零，reasoning 不重复计入。 |

### 测试

| 编号 | 等级 | 规则 |
|---|---|---|
| I-50 | HARD | contract test 保护 Stage 边界；unit test 保护 deterministic compiler / validator；stage / regression 保护跨 Stage 行为；golden set 保护成书质量。 |
| I-51 | HARD | 测试不得依赖真实线上 LLM，除非明确属于 integration / evaluation / golden 套件。 |
| I-52 | HARD | 不能为让旧测试通过而恢复已废弃的架构耦合。新架构的 contract 优先于旧实现细节。 |

### 绞杀式重构闸门

进度、迁移批次、adapter 清单与删除条件只写在 `docs/REQUIREMENTS.md`，不进本文件。

| 编号 | 等级 | 规则 |
|---|---|---|
| S-01 | HARD | 新代码不得依赖 legacy **实现**内部结构；可经登记过的 compatibility 层消费旧盘面。 |
| S-02 | HARD | legacy 可暂时适配新 contract；新系统不得反向适配 legacy 内部结构。 |
| S-03 | HARD | compatibility 逻辑必须集中；禁止兼容逻辑散落各模块。每个 adapter 须有明确删除条件。 |
| S-04 | HARD | 禁止为迁移方便新增双写真相源。禁止新旧两条主路径永久并存。 |
| S-05 | HARD | 新实现成为 authoritative path 后，旧 path 进入 deprecation → removal，禁止继续扩展功能。 |

## 开工与协作

1. 先看 `git status --short`，保留已有改动；只处理本任务涉及的内容。
2. 用户已指定的任务直接推进；未指定时从 `docs/REQUIREMENTS.md` 的未完成项选取，不另造活动计划。
3. 现役依据依次为根不变量、`docs/BACKEND_CONTRACT.md`、`contracts/` 与 `docs/schema/`、`web/AGENTS.md`。
   实现只是证据；冲突先核实。`docs/DECISIONS.md` 解释历史缘由，不覆盖现役契约；
   `docs/LESSONS.md` 按问题或编号检索。
4. 同一行为的契约、producer/compiler、consumer、恢复、API/UI、测试和文档同批闭合；完成要检查真实消费者与
   最终盘面，不能只凭函数存在、编译或单测通过。

自动跑书由确定性 orchestrator 推进；Stage 页只读监控。现役链是 STR-06 新链：
stage0/1 之后由 14 个 `str06.<stage>.<owner>` 节点串成（**B2 五节点重建（2026-09-27，
D9）**：Stage2 book／chapters／names／associate／compile 五 owner，联合抽取节点
`str06.stage2.source` 退役；Stage4 目标故事与确定性编译、Stage5 按章正文与 Hook、
Stage7 命名与交付），节点表唯一登记在 `contracts/invariants.py::NODES`。观察与
关联协议见 `contracts/schema/observation.py`，目标架构见 `docs/schema/str06.md`，
批次见 `docs/REQUIREMENTS.md`。
Stage6 整层与旧 Stage2/4/5/7 链已退役
（退役登记见 `contracts/schema/retirement.py`），旧书只读拒识为
`legacy_incompatible`。历史受托 Commander/Reviewer（含 `<immutable-workpack>`）与
人工逐 Stage 过闸模式已退役，材料只供存量解码。
semantic unit 使用独立 prompt/schema，不加载工程 AGENTS、运维手册或额外盘上小说来补充材料。

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
实际入口：pytest 的 `--test-layer` / `--test-stage` / `--affected` / `--changed`；
`python -m tests.golden` 独立执行 golden set。细则见 `tests/README.md`。

golden set 源语料固定使用 `E:\practice_group\Novel_rewriting\Test_data\Workbench`，经
`NOVEL_DATA` / `NOVEL_CORPUS` 显式提供；产物由 `NOVEL_OUTPUT` 指向独立输出根，不能覆盖源语料。
日常小改与单个 stage 重构不默认运行 golden set。

| 改动范围 | 本轮验证 |
|---|---|
| 说明、注释、测试记录，未改可执行契约 | `git diff --check` 与相关文档校验 |
| 边界明确的小改、局部 bug | 先复现目标反例，只跑受影响的定向单元 / contract；涉及阶段行为、消费或恢复时补 stage 测试 |
| 单个 stage 绞杀或职责变化 | 该 stage 的 unit/contract 与 stage 测试，再补受影响下游、API/UI 与恢复消费者 |
| R/Q/B、schema、节点、API 形状 | 形状稳定后跑契约 §9.5 五文件及受影响消费链；涉及前端时补其验证 |
| 前端或其消费的 API | 受影响测试与 typecheck；数据缝/路由变化补 seams；构建配置变化补 build |
| 整个项目最终验收 | 先工程回归，再 golden set；分别报告过程验证与结果质量 |

选择范围前检查 producer/compiler、consumer、共享模块与恢复路径。含生产代码、可执行契约或测试基础设施变化的批次，在整项目验收、合入 main/冻结发布、或共享基础设施影响无法由定向测试闭合时，按最终改动集合跑一次默认非 corpus 回归；涉及前端时补 test/build/seams/typecheck。
子批次完成、检查点提交、schema 修改或新增测试登记本身不触发完整回归；定向证据闭合即可交付该范围并继续迁移。
启动完整回归前说明命中的上述门槛；选择器扩大范围时先核对原因，不能把保守兜底等同于项目终验要求。
若登记范围或精确消费者足以闭合，可显式选择并报告定向覆盖；真正未知的影响不得直接排除。
不得并行启动共享同一 basetemp/cache 的 pytest。不得删现役职责反例、缩验收分母或放宽判据来提速。

## 文档职责

- 开发方式与架构不变量由根及所属目录 AGENTS 维护；仅在存在独立开发/验证流程时新增指南。
- 可执行行为、跨层守卫归属在 `docs/BACKEND_CONTRACT.md`；字段及消费形状在 `docs/schema/` 和代码。
- 绞杀进度、迁移批次、adapter 与删除条件在 `docs/REQUIREMENTS.md`；历史裁决在 `docs/DECISIONS.md`；
  复用经验在 `docs/LESSONS.md`。临时进度不进 AGENTS。
- 操作方法在 `docs/OPERATIONS.md`；测试分层与 corpus 在 `tests/README.md`。
- 引用使用稳定编号（I-\* / S-\* / R-\*）或 `仓内路径::符号`。

## 交付与维护

- 沿用相邻代码风格与类型标注，保留解释设计原因的模块头。新增 Python 子包同步 `pyproject.toml` 的 packages。
- commit 使用中文 Conventional Commit；交付说明契约影响、验证与限制，UI 变化附截图，真实付费验证单列。
- 不提交 `.env`、输出、缓存、测试临时目录或正文语料。数据工具显式读取 `NOVEL_DATA` / `NOVEL_CORPUS` /
  `NOVEL_OUTPUT`，取不到就报告“没查”。
