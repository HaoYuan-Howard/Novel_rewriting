# 编排（作业 · 完成度 · 队列）

**载体已定：Pydantic v2**——但本文只定**内容**，模型照它写，**方向别搞反**。
所以下表里不出现任何 Python 类型标注，只出现「类型 / 必填 / 取值面 / 证据」。
证据列的取值见 `docs/schema/_README.md`；本文大量使用 stage0 那个 **`设计`**
——**新形状盘上零样本**，还没有任何东西验证过它。

## 为什么它单独一份，而不是 `_report.md` 的第七节

三份文档管三样东西，生命周期与改版节奏都不一样：

| 文档 | 管什么 | 谁产的 |
|---|---|---|
| `_common.md` | **每份产物里都有的那几个键**（`_provenance` / `Finding` / `usage`） | 节点，落在 artifact 里 |
| `_report.md` | **一次校验的输出**（`--check-only` 打印的、`/api/check/<node>` 返回的那一份） | 判据注册表，不落在 artifact 里 |
| **本文** | **一次跑、一批跑、以及「这本书跑到哪了」** | **编排层**，不落在 artifact 里，也不属于任何一个 stage |

合成一节会让**产物契约**与**服务契约**共用一个版本号——而前者改一次要重跑一层书，
后者改一次只影响界面。

⇒ `docs/schema/_orchestration.md` ↔ `contracts/schema/orchestration.py`，文件名一一对应
（同 `_common.md` ↔ `common.py`、`_report.md` ↔ `report.py`）。

## ⚠ 本文的证据面比前十份都差，说清楚差在哪

`docs/BACKEND_CONTRACT.md` §八冻结的 `jobs.json` 是 **`{"jobs":[]}`**——**一个样本都没有**。
所以 `JobRecord` 的形状**只能从代码来**（原型 `Web/jobs.py` 的 `meta` 字典），
而那正是 `_README.md` 立过的那条纪律：

> 凡是「盘上零样本」或「盘上取值面明显偏窄」的字段，都要去**全语料**或**代码**再确认一次。
> （`platform` 就是这么被推错的：盘上 3/3 是 `'FQ'`，全语料 120/163 是 `null`）

`NovelOverview` 那一半好一些：统一契约 §八冻结的 `novel_overview.json` 是**一本真实的书**，
它是 Stage3 退役前的历史抓包，八层产物 + `_provenance` + 三态新鲜度都在里面，标 `盘·码`；
这不代表当前仍有八个现役层。
`QueuePlan` 整个是新的（`R-13` 是本次相对原型的**新增能力**），全部标 `设计`。

## 它要消灭的那四种已发生的故障

| # | 故障 | 实测证据 |
|---|---|---|
| ① | **一个写着「0 成本」的按钮会归档旧轮、覆盖正式产物、重拼成书 txt** | 原型 8 条注释都在说同一句「**`FREE_FLAGS` 只管钱不管写**」，而**代码里没有任何东西在算「写不写」**（全仓 `would_write`/`writes_disk` 零命中） |
| ② | **同一条命令的第二个渲染落点** | 原型前端用 `argv.slice(4)` 硬编码切掉解释器前缀（`Web/static/app.js:291`）——`build_argv` 的前缀长度一变它就**静默错位** |
| ③ | **互斥键算了两遍，于是锁永远留在表里** | 原型 `start` 写进去的是 `sid or '_global'`、释放时拿 `meta['sid']` 去查，在 `sid=None` 上分家：第二本书起全部 409，直到重启服务 |
| ④ | **一个能续跑的编排器被静默弄丢了** | 退役的 `_legacy/run_book.py:14-22` 有过「已存在即跳过 + 漂移检测」，改成 Web 编排之后**整个消失，且没有任何文档说明是有意放弃**——今天 `n5_write <book>` 缺省就是全部章重新付费 |

---

## 零、Commander 与 executor backend —— 模式由判别字段决定

`AgentProfile` 只记录一次选择，不自己声明“哪些组合可用”。可选 driver、角色、model、
reasoning effort 与 execution mode 的唯一公开来源是 `GET /api/agent/capabilities`；组合校验由
`novel/agents/capabilities.py` 的同一份 registry 完成，前端与 argv builder 都不能各抄一张表。

| `AgentProfile` 字段 | 类型 | 必填 | 内容 | 证据 |
|---|---|---|---|---|
| `driver` | `str` | **是** | 可执行 driver id；当前取值由 registry 发布，不拿供应商名代替 | **契·码** |
| `model` | `str` | **是** | 进入 argv 与审计账的精确 model id；展示 label 是 capability 的另一格 | **契·码** |
| `reasoning_effort` | `str` | **是** | 必须属于该 driver + model 发布的 effort，不在前端维护全局联合类型 | **契·码** |
| `execution_mode` | `str \| null` | **是（可空）** | 与 effort 正交；`null` 交给 registry 使用普通执行模式，显式值仍须由该 model 发布。当前只宣称 `standard`，不宣称 `ultra` | **契·码** |

直接 LLM API 使用另一份不含密钥的 `LegacyLlmProfile`。`legacy` 在这里表示“本进程直接调用
LLM API”，不是“无契约旧代码”；provider 的模型、thinking、effort、节点适用面与 client 工厂统一
登记在 `novel/llm/providers.py::LegacyLlmDriver`。以后接入 GLM 必须新增完整 driver，不能只换
DeepSeek base URL 后继续复用它的模型回退、tokenizer、thinking wire 或预算规则。

| `LegacyLlmProfile` 字段 | 类型 | 必填 | 内容 | 证据 |
|---|---|---|---|---|
| `provider` | `str` | **是** | 稳定 adapter id；当前只发布 `deepseek`，不拿 URL 代替 provider 身份 | **契·码** |
| `model` | `str` | **是** | provider 发布的精确模型 id | **契·码** |
| `reasoning_effort` | `str` | **是** | 必须属于该 model + thinking mode 的能力组合 | **契·码** |
| `thinking_mode` | `str` | **是** | provider 自己定义并发布的思考协议；不得套用 Agent execution mode | **契·码** |

`ExecutorBackend` 是唯一模式选择器：

| `kind` | `profile` | 执行边界 |
|---|---|---|
| `agent` | `AgentProfile` | 经 `AgentDriver` / 节点 agent 执行；当前可扩展 Claude Code 与 Codex |
| `legacy` | `LegacyLlmProfile` | 经 `LegacyLlmDriver` / 直接 API client 执行；当前为 DeepSeek，未来 GLM 独立登记 |

两种 variant 都必须同时携带 `kind + profile`，不得再用“有没有 AgentProfile”推断模式，也不得自动
降级到另一种 backend。一次整书代跑用 `PipelineAgentProfiles` v2：

| 字段 | 类型 | 必填 | 内容 | 证据 |
|---|---|---|---|---|
| `schema_version` | `2` | **是** | v1 的裸 `AgentProfile` 只在读边界无损迁移为 `kind=agent`；所有新写入只发 v2 | **契·码** |
| `commander` | `AgentProfile` | **是** | 本轮工作包内的受托语义审阅身份；Commander 本身不走 legacy API | **契·码** |
| `executor_default` | `ExecutorBackend` | **是** | 所有未覆盖付费 executor 节点的默认 backend | **契·码** |
| `executor_overrides` | `{node: ExecutorBackend}` | 否；默认 `{}` | 只写逐节点差异；键严格属于 `AGENT_EXECUTOR_NODES`。stage 名、代码节点与退役节点一律拒绝 | **契·码** |
| `concurrency` | `PipelineConcurrency` | 否 | 显式冻结逐节点并发与整书、同根供应商总上限；未配置时序列化省略，保留既有 profiles 的字节身份 | **契·码** |

`PipelineConcurrency.node_overrides` 只接受 `PIPELINE_PARALLEL_NODES` 中的节点和
`PIPELINE_CONCURRENCY_TRIALS` 档位。默认逐节点并发为 4，显式对照可选 6/8；其他节点保持原配置。
`per_book` 默认 4，`per_provider` 默认 8，均为 1～32 的整数。两个总槽作用于同一输出根的实际
Executor/Commander 调用；不把不同输出根或不同供应商混算。节点并发提高后仍受两个总槽约束。
服务端 capability 发布节点、档位、默认与范围；设置、preview/start、batch、历史和原配置续跑
完整保留这一字段。旧 profiles 缺失该字段不回填历史测量值。

总槽由 `novel/model_slots.py::pipeline_model_slot` 在实际 wire 前取得；同书等待者归还已取得的
供应商槽，取消或配置拒绝不产生模型费用。模型单次 latency 从取得槽后计时，排队仍属于节点墙钟。
容量变更先占有全部旧槽，尚有活跃 wire 时拒绝；配置损坏不按默认重建。锁持有者退出后由 OS 释放。

`None` / `null` 只服务旧历史与迁移期尚未指定的请求，**不表示 capability 数组第一项**。
服务端 capability 的 `default_profiles=null` 表示当前还没有通过 TODO-45 真实整书验收的**产品预置
配置**；它不是 Agent/Legacy 模式开关。UI 已消费该响应，但仍必须显式选择。用户在设置页保存的
常用配置与产品预置分开，既可以选择 Agent，也可以选择 Legacy。Agent backend 的 direct executor
合法节点与 flags 唯一登记于 `novel/orchestrator/nodes.py::EXECUTOR_PROFILED_RUNS`，
能力接口与执行校验共同消费该登记，本文件不复制机械矩阵。

Agent 矩阵之外的 direct 组合仍关闭式拒绝。Legacy backend 则按 provider registry 发布的精确
model/node 能力关闭式校验：DeepSeek V4.1 Flash 不承接契约钉死 Pro 的 `stage4.design`、`stage4.plan`、
`stage6.semantic`，必须改用 Pro 或逐节点覆盖；DeepSeek V4 Pro 承接现役九个 executor 节点。

两种 backend 共用一条身份链：`preview/start` → `Step/JobStart` → `argv` → `CLI` → `RunContext` →
按 `kind` 选择的 executor。Agent registry 在 argv builder 边界物化 `execution_mode`；Legacy registry
同样校验 provider/model/effort/thinking/node。Stage2 global Executor 复用一条 logical session；blueprint 按
phase/purpose/chapter 隔离；Stage5 按章隔离；Stage6 semantic 管理 A/B 两条独立 session；其余 Phase5
节点均按 node 隔离。agent 只交 fixed typed 候选，代码掌握 schema / HARD、结构化反馈、正式归一、
checkpoint、提交 journal/readback/adoption 与终验。显式 Agent backend 不得落回 Legacy；显式 Legacy
backend 也不得改走 Agent。

**三根运行身份轴**：`JobStart.run_id` 是**业务 run**——同一业务节点从
首跑、恢复到聚合始终不变，账本行、agent session 审计、`_stage*_runs/<run_id>/`
checkpoint 与 run 目录都以它为键；`job_id`（进度行里的 `execution_id`）是**本次进程
attempt**，每次启动唯一。恢复续跑（队列重启确认或原授权自动续跑）= 新 `job_id` +
`JobStart.recovered_from_job_id` 联结 + **原 `run_id`** 经 `NOVEL_RUN_ID` 传给 CLI/
ledger/session/checkpoint。旧盘记录没有这三列（可缺、不回填、不按时间猜）；新 writer
必填。旧 orphan attempt 永久保持 `reconciled/unknown`，不根据新 execution 伪造旧
`ended`。整书 headless 路在此之外再有稳定 `parent_run_id` 与可滚动 `execution_epoch`：代码、执行契约、
provider session 和 admission 属于 epoch，不得反向写进稳定 parent contract。

direct job 另有一条正交授权链：`PreviewRequest.egress_authorized` 默认 false，backend 非空的新计划必须
显式冻结 bool 到 `JobStart` 与付费 executor `Step`；只有 true 才在 argv 加 `--allow-egress`。false
允许 0 成本 preview 摊开不可执行命令，真 start 与手敲 CLI 会在 job/driver/probe/wire 前拒绝；无
backend 时 false 归一为 `null`，true 反而拒绝。它不是 `NodeSpec.flags`，也不能由 `--yes` 推导。
公开 agent run 的非空 `PipelineAgentProfiles` 已接进 provider-neutral Commander。
preview/start/batch 共用一份 plan；规范化 base、完整 resolved profiles 与两项授权冻结进
`RunContractV2`，起始代码、执行契约、stdin-only invocation、admission 与 provider session 冻结进可滚动
`ExecutionEpoch`。`profiles=null` 只保留旧请求形状，当前无服务端默认时关闭式拒绝。
页面从 capability 动态构造 Commander、默认 executor 与逐节点 override，成功 preview 保存的完整请求
对象就是 start 唯一可消费的快照；配置、授权或 capability 变化会使它失效，不重新取默认。Commander
child 只持 run id + contract SHA-256 + epoch id，从 append-only 事件复核快照；`run_step.py` 再按 exact
node/flags 选择 executor backend。现有结论来自 fake driver、本机零模型 CLI 检查与非 corpus
回归，**真实 provider 模型请求仍为 0，没有质量、速度、entitlement 或 current-schema corpus 实证**。

---

## 一、`Step` —— 一条将要执行的命令

**`R-11.8` 的全部内容是一句话：展示与执行共用同一个构造函数。**
所以「界面上摊开给人看的那条命令」与「真的会被 `Popen` 的那条」是**同一个对象**，不是两份。

| 字段 | 类型 | 必填 | 内容 | 证据 |
|---|---|---|---|---|
| `argv` | `str[]` | **是** | 完整命令行。**`argv[0]` 恒是 `sys.executable`**，绝不硬编码解释器路径 | **盘·码** |
| `cwd` | `str` | **是** | 绝对路径。见下面那条 ⚠ | **契·码** |
| `label` | `str` | **是** | 人读的一句话。**从 `NODES` 派生，不许手写**（手写的 label 会说谎：原型 `n1_partition` 的 label 写着「stage1 摄入+分章」，而「摄入」早已搬到 stage0） | **契·码** |
| `paid` | `bool` | **是** | 这一步会不会花钱。**唯一落点 `novel/orchestrator/nodes.py::is_paid`**（`R-11.7`：贵贱只有一处算） | **盘·码** |
| `writes` | `str` | **是** | ⭐ 这一步会往盘上写什么。**空串 = 不写盘。** 见 §1.2 | **设计** |
| `note` | `str \| 键不在` | 否 | 这一步的特别提醒（例：追跑的那条 `--check-only` 为什么在） | **契·码** |
| `executor_backend` | `ExecutorBackend \| null` | 否；旧盘可缺 | 本步真正解析后的 executor 快照。付费 producer Step 可非空；自动追跑的 0 成本 `--check-only` Step 必须为 `null`。旧 `executor_profile` 只在读边界包成 `kind=agent`；新序列化不再发旧字段。两格都缺只能显示 unknown，不按今天默认倒推昨天身份 | **契·码** |
| `egress_authorized` | `bool \| null` | 否；旧盘可缺 | 独立正文外发授权快照。新付费 executor Step 必须为 bool，并与 argv 是否含 `--allow-egress` 逐字一致；backend 为空的兼容/代码/追跑 Step 必须为 `null`。旧盘 `null` 只表示未记录，不倒推 | **契·码** |

### 1.1 ⚠ `--base` 用绝对路径，但 `-m novel.cli` 仍然吃 cwd

argv 的形态是：

```
<sys.executable> -X utf8 -u -m novel.cli run <node> <sid> [flags…]
  [--executor-backend agent
   --executor-driver <id> --executor-model <id> --executor-effort <id> --executor-mode <id>]
  [--executor-backend legacy
   --legacy-provider <id> --legacy-model <id> --legacy-effort <id> --legacy-thinking <id>]
  [--allow-egress] --base <绝对路径> [--yes]
```

两个 backend 参数组互斥，builder 只生成其中一组；CLI 不接受半份选择或混搭参数。旧复制命令若只有
完整的四个 `--executor-*` 参数，可在读边界无损解释为 `kind=agent`，但所有新命令都显式写
`--executor-backend`。`--allow-egress` 只可跟在非空 backend 后，且只在独立授权为 true 时出现；它与
`--yes` 分别回答正文能否外发、能否付费。

`--base` 用绝对路径是原型定下来的，理由是「**好让 argv 与 cwd 无关**」。
但 `-m novel.cli` 这一段**本身**要求 cwd 在仓根（`pythonpath=["."]` 只对 pytest 生效）。
⇒ 两件事都要做：**`Step.cwd` 必填**（粘命令的人要知道在哪儿粘），
且 `pyproject.toml` 补 `[build-system]`——装成 editable 之后这条依赖就没了，
而 `cwd` 这一格仍然如实记着那一次是在哪儿跑的。

⚠ **不加 `[project.scripts]`**：那会让 `argv[0]` 变成一个包装脚本，
与「`argv[0]` 恒是 `sys.executable`」直接冲突。

### 1.2 ⭐ `writes` —— 「贵贱」与「写不写」是两个正交的维度

原型只实现了前者。后者只活在注释里，而它一共出现了 **8 次**，措辞完全一致：

> **`FREE_FLAGS` 只管钱不管写。**（`--apply-edit` 落新一轮 + 归档旧轮 ·
> `--revalidate` 把 `.INVALID` 提升为正式产物 · `--recheck` 刷指标并对账 trim ·
> `stage7.assemble` 重拼成书）

一条信息在注释里重复四次以上，说明它是真的，只是**没有落点**（同 `_common.md` 立的那条判准）。
⇒ 本字段就是它的落点。三条形式要求：

1. **它是声明表，不是从 flag 名推的。** 通用 flag 落点是
   `contracts/invariants.py::WRITE_EFFECTS`；同一 flag 在不同节点写不同文件时，
   例外落在同文件的 `NODE_WRITE_EFFECTS[(node, flag)]`（值同样以空串表示不写盘）。
2. **值是一句话「它写什么」，不是 `bool`。** 空串 = 不写盘——
   与记账那边「`recorded=False` ⇒ `[????] 未记录`」是同一个纪律：**「不写」与「没人说」必须分得开**
3. **未知动作 / 未核对的 flag 一律按「会写」算。** 方向与 `is_paid` 同构
   （`Q-10`：「不知道」必须倒向贵的那一边；这里是倒向危险的那一边）

---

## 二、`JobEvent` / `JobRecord` —— 一次跑

### 2.1 ⚠ 记录是**事件折叠**出来的，不是一份被反复改写的 JSON

原型每次状态变化都 `os.replace` 整份 `<job_id>.json`，并且**为此写了一段重试**——
`PermissionError: [WinError 5]`，因为服务一边写一边被 SSE 轮询读，**实测在测试里必现**。

⇒ 本设计改成 **append-only JSONL**（`Output/_service/jobs.jsonl`），
理由与 `_common.md` §八 给成本账定的是同一条：

> **永不需要读-改-写** ⇒ 并发安全、被 kill 不损坏、可 grep。

`JobRecord` 是把同一个 `job_id` 的事件按顺序折叠出来的**视图**，盘上不存在这样一份文件。

| `JobEvent.kind` | 何时写 | 携带 |
|---|---|---|
| `started` | 子进程 `Popen` 成功之后 | 全部静态字段（见 2.2） |
| `step` | 每一步开始 | `step_index` + 那一步的 `argv` |
| `ended` | 进程回收之后 | `exit_code` · `ended` |
| `reconciled` | 服务启动时的改判 | `state='unknown'` + 一句原因 |

⚠ **`reconciled` 是追加一条，不是回去改 `started` 那一行。** 回去改就毁掉了 append-only
的全部意义——而那正是「被 kill 不损坏」这条性质的来源。

### 2.2 `JobRecord`（折叠后的形状）

| 字段 | 类型 | 必填 | 内容 | 证据 |
|---|---|---|---|---|
| `job_id` | `str` | **是** | 单调 + 随机后缀，形如 `1786199530-3f2a1b8c`。**不是自增数**（多进程下自增会撞） | **契·码** |
| `sid` | `str \| null` | **是** | 书 id。⚠ **可为 `null`**：上传新书那条路不带 sid（那会儿目录还不存在） | **契·码** |
| `node` | `str` | **是** | `NODES` 之一 | **设计** |
| `flags` | `str[]` | **是** | 这一次带的 flag。**空列表也要写**——「没带 flag」与「没记」不是一回事 | **设计** |
| `steps` | `Step[]` | **是** | 这个作业要依次跑哪几条命令 | **设计** |
| `python` | `str` | **是** | `sys.version`。哪版解释器跑的 | **盘·码** |
| `code` | `str` | **是** | `git describe --always --dirty`。哪版代码跑的（同 `_provenance.code`，全链一个实现） | **三源** |
| `paid` | `bool` | **是** | 整个作业花不花钱 = 任一步花钱 | **盘·码** |
| `writes` | `bool` | **是** | 整个作业写不写盘 = 任一步写盘 | **设计** |
| `executor_backend` | `ExecutorBackend \| null` | 否；旧盘可缺 | 直接节点作业解析后的 executor 快照；与付费 producer `steps[].executor_backend` 同源。旧 `executor_profile` 只读迁移为 Agent variant；新写入用判别联合，不能冻结一种 backend 后执行另一种 | **契·码** |
| `egress_authorized` | `bool \| null` | 否；旧盘可缺 | 与所有付费 executor Step 同源的独立正文外发授权。新 backend 作业必须冻结 bool；无 backend 时必须 `null`。false 是“可预览、不可执行”，不是 unknown；旧盘 `null` 才是未记录 | **契·码** |
| `started` | `int` | **是** | epoch 秒 | **盘·码** |
| `ended` | `int \| null` | **是** | 必填但可为 `null`——**`null` = 还没结束或不知道**，不是 0 | **盘·码** |
| `exit_code` | `int \| null` | **是** | 同上。⚠ **`exit_code == 0` 不等于成功**，见 §2.3 | **盘·码** |
| `state` | `str` | **是** | §三那五态 | **三源** |
| `note` | `str \| 键不在` | 否 | `reconciled` 那一条的原因 | **契·码** |

⚠ **`argv` 不在这张表上，它在 `steps[].argv` 里。**
原型的 `meta` 同时有 `argv`（第一步）与 `all_argv`（全部）——**两个字段说同一件事**，
而界面只渲染前者，于是一个两步作业在界面上看起来只跑了一步。

旧 append-only `jobs.jsonl` 里可能还有 `stage5.write` 的 deep/shallow `mode`。
它只由 `novel/orchestrator/jobs.py::_decode_event` 在读盘边界校验后剥离；
`JobStart`/`JobRecord` 公开模型、OpenAPI 与前端类型都没有该字段。
旧 selector 与它的可执行 `argv` 也只留在 append-only 原记录；公共投影仅显示
`legacy_incompatible` 占位命令与「命令已退役」说明，避免把历史快照画成仍可重放的入口。

### 2.3 ⚠ 退出码 0 不等于成功

⇒ 凡是**写盘的付费节点**，`build_steps` 都自动追一条 `--check-only`
（见 §五），界面上必须能出现 `exit=0 · check=FAIL(3)` 这种状态。

⚠ **退出码 1 不等于崩溃**：`1` 只表「跑完了，判定不通过」；
崩溃是 **`70`（结论未知，是 bug，栈在 stderr）**，与 `jobs.py` worker 的数同源。
取值面唯一落点 `novel/exit_codes.py`（五个数）。

### 2.4 `RunContractV2` / `ExecutionEpoch` —— headless agent 的稳定合同与可滚动执行身份

agent 代跑不是 `NODES` 中的流水线节点，不能塞进 `JobStart`；它使用独立的
`Output/_service/agent_runs.jsonl` append-only 事件流。RunSupervisor 只是 orchestrator 内的确定性
状态机，不调用模型；模型角色仍只有 Commander / Executor。`started` 保存稳定 parent contract，
`epoch_opened` 保存本 epoch 的可执行身份：

> **并发模型**：锁**按书**分（`jobs.agent_lock_key`，同书不并行），
> 同时在跑的总数由 `agent.MAX_CONCURRENT`（默认 3）单独限；两者是两件事，
> 合成一个数字就会得到「想并发就得放弃同书互斥」的假选择。
> 超出闸的经 `POST /api/agent/runs/batch` 排队，由结束的那个 agent 在 worker 的
> `finally` 里带起下一个。反向互斥同步收窄成**按书**：A 书的 agent 不再挡 B 书的节点作业
> （摄入 `sid=None` 仍一律拒——它要推导目录名）。

| 字段 | 类型 | 必填 | 内容 | 证据 |
|---|---|---|---|---|
| `sid / base` | `str / str` | **是** | v2 parent run 的书与规范化 base；v1 旧事件缺 base 仍不用当前配置回填 | **契·码** |
| `authorized / egress_authorized` | `bool / bool` | **是** | 稳定 parent run 的付费与正文外发授权，二者正交 | **契·码** |
| `profiles` | `PipelineAgentProfiles` | **是** | 稳定 Commander + 默认 Executor + 节点 override；v1 旧 null 不回填，也不能构造 v2 contract | **契·码** |
| `argv / prompt / code_version` | 首 epoch 审计投影 / `ExecutionEpoch` | **否（不属于 parent contract）** | 新 v2 started 把首次 argv/prompt 放在不参加 parent hash 的 `start` 兼容投影；code 与实际 invocation 身份只属于 `ExecutionEpoch`。早期 v2 内嵌字段仅由 reader 保守解码，重新序列化与 contract hash 均排除 | **契·码** |
| `epoch_id` | `str` | **epoch 必填** | 同一 parent run 内单调打开的 execution epoch 身份 | **契·码** |
| `argv / prompt_sha256` | `str[] / SHA-256` | **epoch 必填** | 本 epoch 的 Commander invocation 与 stdin prompt 身份；prompt 正文不复制进 epoch | **契·码** |
| `code_version / protocol_version` | `str` | **epoch 必填** | 本 epoch 的代码与协议身份；无法证明不得打开 epoch | **契·码** |
| `identity_version / skill_sha256` | 见 `contracts/schema/orchestration.py::ExecutionEpoch` | 版本化 | 当前 identity v2 不带 skill 摘要；v1 保留原字段和 canonical hash，仅供历史复验，不用于当前执行或重签 | **契·码** |
| `cli_version` | `str \| null` | **epoch 必填（可空）** | provider CLI 未观测时为 null，不用请求值倒填 | **契·码** |
| `session_run_id / resume_provider_session_id` | `str / str \| null` | **epoch 必填** | Commander session run 与本 epoch 的 producer 身份；同 epoch exact resume 才可复用 provider context。代码 漂移或 Stage4 typed repair/replan 的 producer 边界都必须换 fresh `session_run_id`，不得续旧 provider | **契·码** |

两项授权不能相互推定。preview 可在任一项为 false 时只读展示；start/batch 在任何 probe/spawn/wire
前同时要求二者为 true。API 请求可以有 wire 默认，但核心 prompt 构造与 `started` 事件必须显式取得
两项值。排队项 `AgentPending.profiles` 是必填的 resolved 深快照；出队不重新读 capability、默认值或
原请求，避免等待期间配置漂移。

生命周期事件闭集为 `started / epoch_opened / paused / resumed / epoch_closed / ended / reconciled`；
agent state 闭集独立为 `pending / running / paused / ok / landed_red / stopped / failed / unknown`，
不借用 `JOB_STATE`。`stopped` 只由服务端在 ended 同时携带 `exit_code=2` 与
`outcome.state=stopped` 时投影；exit 70、旧事件缺 outcome、或 exit 2 的终验不是 stopped 时仍为
`failed`，浏览器不得从裸退出码补算。公开 pause/resume/stop 路由统一返回 202，只确认 Supervisor 已受理，不同步等待生命周期
终态：pause 先关新 admission，后台等待当前 claim/beacon 闭合后才追加 paused，且不 cancel 正常付费
节点；resume 在后台同身份续当前 epoch 和 provider session，代码 漂移则同 parent run 开新 epoch、
免费复检并新建 provider session。另一个有意的滚动点是 hash-bound Stage4 typed repair/replan：Supervisor
在执行 `recovery:stage4-design-action` 前按 exact action 身份只滚一次 `reason=continue` 的 fresh epoch，
随后重读 operator next/CAS，且不重启 Reviewer；stop 在后台 cancel 全进程树并 drain，闭合后才永久 ended，且不可
resume。Stage2 blueprint 与 Stage4B plan 各有一条生产者举证的系统暂停：仅当 exit 2 同时留下绑定当前
epoch/run/code、完整请求章集合和可恢复交付失败的 `delivery_recovery.json`，Supervisor 才追加带缺章
摘要的 paused；前端继续消费同一 `allowed_controls=resume`，恢复时保持 parent/epoch 并只补 checkpoint
缺章。普通 exit 2 或凭据缺失、损坏、漂移不得开放 resume。已经 ended 的 Stage4B 历史 run 也不得由
Supervisor 复活；其已验章只能由后续新 run 的 producer 按下述只读 adoption 契约采用。内部阻塞 helper
只供诊断/测试，不改变公开异步语义。重启后证明不了存活状态仍为 unknown，不自动洗成 paused。

Commander 的 stdout/stderr 只形成供应商 observation，不作为业务完成事实。`role=commander,node=null`
的 session/ledger 以 attempt id 联结；actual model/effort/provider session id 缺失保持 unknown，不拿请求值
倒填。ledger 行的 `latency_ms` 记该轮 Commander 子进程墙钟（含其内部全部模型往返与工具调用，不含之后的
execution drain），进程未启动为 `null`；可选 `activity` 只把同一 `role=commander` 事实标成
`stage2.global.prereview` / `stage2.global.review-or-edit` / `stage2.blueprint.review` 等墙钟分段，
不创建假 node、也不改变成本归属。身份漂移、provider session 跨 logical session 复用或账/审计失败会把 delivery 降为 unknown，
但 outer state 仍尽力落账。child executor 通过 parent contract SHA 与 current epoch identity 复验相同
base/sid/profiles/授权/code/provider context；无法复验时不得启动付费节点。

Reviewer 某轮若携 provider-native output schema，`CommanderInvocation` 会先用 Executor 共用的严格
公共子集校验，冻结为排序键、无空白的 canonical JSON 字节与 SHA-256；调用方之后修改原 mapping 不会
改变真实 wire。带 schema 的 exact resume 必须显式回带上一轮 SHA 且与本轮规范字节完全一致。该真实
schema SHA 进入本 logical session 的 `started / attempt / turn / ended` 四类
`agent_sessions.jsonl` 事件和在线 `CommanderSessionPointer`；只有 schema-less 请求才继续用
provider observation protocol hash，不能在同一席位混用两种身份。

公共 schema 编译器只移除 `$ref` 的 `title / description` 注解；仍有验证关键词的引用在 wire 前
关闭式拒绝，不能把 `pattern / minLength` 等业务约束一起丢掉。完全同形的枚举可共享 `$defs`，
Stage4 native v3 从冻结 options 建立逐章局部引用表，保留节点、人物呈现、场景、状态、约束、
知识端点与 Global 的完整闭集；历史约束句柄同样恢复原始身份。完整 options SHA 防止映射
漂移，exact request SHA 继续绑定决定入口；只共享完全同构定义，避免重复展开相同验证条件。
预检同时检查服务支持的 schema 资源上限；压缩只改变运输，不扩大合法回答集合。

两家 CLI 只在运输方式上不同。Codex invocation 的 `--output-schema` 路径按 schema SHA 固定在本 epoch
scratch 直属目录，但文件在 build/preview 时不存在：driver 只于实际 runner 生命周期以独占普通文件创建、
逐字回读，结束后清理。硬杀留下的孤儿只在非 symlink、普通文件且内容与本轮冻结字节完全相同时回收；
异字节或异常类型在 runner 前拒绝。清理失败不得覆盖已经观测到的 provider result，残留由下一次同字节
调用安全回收。该临时文件不是 artifact、checkpoint、cache 或 provenance。Claude 不造文件，直接把同一
canonical 字节内联进 `--json-schema`；因为这会占用 Windows 命令行长度，完整 argv 必须在任何
runner/wire 前按 UTF-16 上限预检，超限以零 wire 配置失败返回。

#### 2.4a `commander_admissions.jsonl` —— 顶层退出与子执行之间的持久准入账

只扫 `agent_progress.jsonl` 抓不到“已经决定启动、尚未来得及写 start beacon”的窗口；顶层 Commander
若在这时退出，空扫描会过早验盘并释放书锁。每条外层 AgentRun 因此先取得 per-lifecycle OS
byte-lock owner lease，并持有到 ended/书锁收口的最外层 finally；每个 execution action 再以独立
admission id 写 `opened`，并在 v2 行中把 `lifecycle_run_id` 固化为该 outer AgentRun；headless
`run_step.py --execute` 在 evidence、beacon 与节点子进程前写 `claimed`，完整 Step 序列结束后写
`released`；typed approval、response-repair、终审与 operator telemetry 等非 node writer 也用
`write:<operation>` claim 覆盖自己的完整 currentness＋write 窗口。一次 execution turn 返回或异常后
关闭该 execution 的 claim 窗；claim 与 close 共用同一个 append transaction，所以 close 后迟到 claim
必拒，close 前成功 claim 必留在 active 集合里等待 release。双面 drain 正常闭合后追加 `drained`；
RunSupervisor 随后重读 `operator next`：仍 ready 时由确定性主路直接消费同快照 execution token，
不会启动或续跑 Commander；命中 `DETERMINISTIC_RECOVERY_WAITING_ON` 的 blocker 时现场铸 exact
recovery permit 并直接交 Executor，同样不启动 Commander；只有命中关闭 `reviewer_tasks` 注册表的
blocker 才以 immutable workpack 进入同 epoch 的 Commander exact-resume turn。只有显式、hash-bound
的孤儿恢复才追加 `reconciled`。

其中 `recovery:stage4-design-repair-aggregate-commit` 是一条 FREE/0-wire 的确定性
producer 补尾：编排上的 `next_handler=executor` 只表示由 child execution 消费，并不构造模型
Executor profile、client、provider session、checkpoint 或 wire。operator 必须在普通 approval、inbox
和付费 planner 之前探测它；若 Stage2 edit commit 与 Stage4 repair aggregate 两类 prepared 同时存在，
这是双重提交所有权，必须关闭式报 unknown，不能按探测顺序挑一份。单独命中 Stage4 时 action 固定为
`stage4.design --recover-repair-aggregate <source_run_id> --source-journal-sha256 <raw_sha> --source-commit-sha256 <self_sha>`。
permit 把 source run 映射到固定 journal 路径，并把原始字节 SHA 作为唯一动态 external input；签发和
claim 后各重读一次，journal self SHA、live candidate baseline/target 与 source 生命周期仍由 producer
再次闭合。它不把所有历史 run 纳入 active surface seal，也不为这次补尾再滚一个 paid repair epoch。

`CommanderAdmissionEvent` 同文件兼容读 v1、当前只写 v2：

| 字段 | 类型 | 必填 | 内容 | 证据 |
|---|---|---|---|---|
| `wire_version` | `1 \| 2` | **是** | v1 只读兼容；当前写 v2 | **契·码** |
| `parent_run_id` | `str` | **是** | admission gate 自身 id（历史字段名保留）；同一 gate 的六态只认这一格 | **契·码** |
| `lifecycle_run_id` | `str \| null` | **是（可空）** | v2 每行必填：持有 owner lease、写 beacon、接收 AgentRun 投影的 outer lifecycle；v1 必须为空 | **契·码** |
| `sid` | `str` | **是** | 所属书；同 parent run 漂移即坏账 | **契·码** |
| `kind` | `str` | **是** | `opened / claimed / released / closed / drained / reconciled` 六态；opened 只能一次，closed 最多一次且可幂等重试 | **契·码** |
| `ts` | `int` | **是** | epoch 秒 | **契·码** |
| `claim_id` | `str \| null` | **是（可空）** | claimed/released 必填且成对唯一；其余态为空 | **契·码** |
| `node` | `str \| null` | **是（可空）** | claimed/released 绑定真实 node，或非 node writer 的 `write:<operation>`；两态必须逐字相同，其余态为空 | **契·码** |
| `claim_sha256` | `str \| null` | **是（可空）** | claimed/released 绑定 execution permit 或 generic write payload 的 64 位小写 SHA；两态必须逐字相同，其余态为空 | **契·码** |
| `actor / reason / subject_sha256 / force` | `str / str / str / bool \| null` | **是（可空）** | 仅 reconciled 四格同时非空；v3 subject 同时绑定 admission/lifecycle id、opened/claimed/released 全量事实与 outer 完整 beacon 生命周期 | **契·码** |

父 worker 在 close 后必须同时等 active claims 与该 lifecycle run 的 open beacon executions 两面归零，才可
做 delivery、写 ended 和释放书锁/并发槽。300 秒只是报警与尝试 cancel 的阈值；越线后继续持锁等待，
最终闭合也只能 unknown。服务重启后，没有 current `drained` 或 `reconciled` seal 的 gate 由
`admission.unresolved` 继续阻断同书 agent/job/trash/API writer；这份 durable 身份不能由进程内 live
表替代，queue startup 也必须整 lane 跳过而不读产物、不追加状态、不启动；typed reconcile 后才允许
操作员显式 resume。reconcile apply 全程先 nonblocking 取得 `lifecycle_run_id` 对应 owner lease 的
stopped 证明，`force` 不能越过 live owner；崩溃由内核自动释放，残留 `.lock` 文件不作为存活依据。
reconcile subject 同时绑定 admission/lifecycle 两枚 id、opened/claimed/released 全量事实与 outer
beacon 生命周期；terminal 后
出现迟到 release/start/end 等任一事件都会令旧 seal 失效并重新 unresolved，不能让一张旧人工票继续放行。

旧 v1 gate 没有 lifecycle 联结。typed reconcile 只可扫描 append-only AgentRun 账中的
`epoch_opened`：admission id 必须逐字等于 epoch id，或是无前导零且从 2 开始的 canonical `-tN`。
先按这条 canonical id 判据找结构命中；任一命中的唯一 v2 started、contract、sid 或可折叠 record
不闭合都直接拒绝，不能丢掉坏候选再降级 generic self。结构上唯一且各格闭合才映射 outer，多条拒绝；
只有完全没有 canonical epoch-id 命中才按 generic self gate。v3 subject 的协议与双 id 会让
历史 v1/v2 reconcile seal 在升级后自然失效，因此 `admission.unresolved` 继续挡住服务 startup/new
writer，直到操作员按新 preview 重新落 v2 seal。AgentRun 投影只追加 `reconciled`、清 active epoch
指针并保持 unknown；它不补造 `epoch_closed`、`ended`、exit、drain 或 delivery 事实。若崩在 admission
seal 与 AgentRun 事件之间，只允许同 subject 且同 actor/reason/force 幂等补尾。

admission 与 beacon 都只把 LF 结尾行视为 committed；读侧忽略被 kill 截断的最后一行，写侧遇到
non-LF 尾关闭式拒绝续写。零成本恢复先只读给出 committed prefix 与 tail 的 size/SHA 双身份；apply
需要 actor/reason/current subject，把坏尾原始字节写入对应
`_service/commander_admission_tail_repairs/` 或 `_service/agent_progress_tail_repairs/` 并 fsync，再在原 append
lock 内截回 committed prefix、fsync，并分别追加 `authorized / applied` 的
`CommanderAdmissionTailRepairRecord` 或 `BeaconTailRepairRecord`。记录另带
`wire_version / ts / quarantine_name`；不得解析、补全、补 LF 或猜测半条 JSON/end。

`ended` 事件在原始 `exit_code` 外另带 `exit_origin` 与 `outcome`。来源闭集由
`AGENT_RUN_EXIT_ORIGINS` 登记为 `novel_cli / commander / supervisor`，只允许 ended producer 明确填写；
事件和 `AgentRunRecord` 在来源为 None 时省略该新字段，保持旧账的序列化形状，`wire_version` 不变。
外部 CLI 的 raw 退出码、parent 进程状态、独立盘面交付终验是三条轴：parent 保留收到的原始数字，
不把供应商 exit 1 改写成小说 CLI 的“已落盘”。`AgentRunRecord.state` 中 0 为 `ok`，仅
`1 + exit_origin=novel_cli` 为 `landed_red`；Commander、Supervisor 或历史缺来源的 1 均为 `failed`，
不从诊断、理由或旧 note 推断来源，也不为未知来源补“已经落盘”说明。旧 note 原样保留。
`run_step.py` 透传正式 CLI 的退出码，但自身未捕异常统一为 70；显式控制异常保持原语义。
确定性交付终验的退出码归 `supervisor` 来源；终验失败不承诺有新产物落盘。
已发行恢复 policy 的 parent 摘要经 `reviewer_protocol_recovery.parent_proof_projection_v1`
复现发行时的完整投影，只隔离新增字段和已变更的派生 state/note；源事件、授权、epoch、outcome
等事实仍逐字进入原摘要，session/attempt/cost/log 仍按原策略交叉校验。该投影不回写旧账，
不用于 UI 或执行授权，也不因旧投影含 `landed_red` 而放宽恢复条件。
仅 `2 + outcome.stopped` 为 `stopped`，其余非零为 `failed`；旧事件缺 outcome 时折叠成 `unknown`。运行页信标的
`RunStepView.beacon` 与 `events_tail[].beacon` 也由后端同一折叠口给出；exit 2 是 `stopped`，浏览器
只呈现，不从 `exit_code` 重建状态。`outcome.state` 的唯一取值面是
`AGENT_DELIVERY_OUTCOME`：`ready` / `ready_with_disclosures` / `stopped` / `failed` /
`unknown`。终验从盘面重新核 Stage2 正式 Global + 称名投影 + Blueprint current、联合 HARD 通过、
global review 的 generation-safe 面（blocking 逐项 PASS，或仅含已显式完整组隔离的
secret 组 UNVER；关系与规则不允许隔离，前者仍是 disclosure-ready 而不是 PASS）、当前可下载成书、阻塞待办与
启动/结束代码指纹；不解析 agent 自述。两轴不许互推，尤其 `state=ok` 不推出可交付。
一次 Commander reviewer turn `exit_code=0` 但 fresh outcome=`stopped` 时，RunSupervisor 再读一次
`operator next`：ready 交确定性 execution-token 主路；只有下一个状态仍是登记的 reviewer blocker 才
继续审阅。所有模型节点按 `NODES.lifecycle` 的工作单元划分 E/C 身份；同一 Commander 单元
连续审阅，局部返修的 surface 或候选变化不重置预算，也不强制换审阅者。Stage4 A/B 分别隔离，
Stage6 双盲 judge 分别隔离；这些业务边界不因共用基础设施而合并。

Stage2 冷启动执行 Analyze E → 独立 Characters/Global 编译 → Global C 统一接受 →
逐章并发 Blueprint E → Blueprint C 逐章及跨章接受。cold-start 判定先排除历史回答、checkpoint、
prepared 与任一侧既有产物；已有来源优先恢复，后续问题只交对应 Characters 或 Global owner。
Global 必须正式有效、逐项审阅 generation-safe 且节点接受 current；`.INVALID` 与称名投影不能释放
Blueprint。联合首抽只记 Analyze 的一次真实 attempt，两份产物共享来源但独立编辑、失效与恢复，
不把完整联合回答挂为可变人物文件的失效边。Analyze E、独立 owner 修订 E、每章 Blueprint E、
Global C 与每个 Blueprint 审阅范围各有独立工作单元，
内容额度为基础 4 次加已生效的不可变人工追加，包含首次、格式和内容纠错。
不存在人物 Commander、预审、额外 Global 终审或按序号保留的第四席。

Stage5 当前闭合后才进入 Prepare E / C；两席各有独立累计额度。Prepare C 只消费完整正文、
原钩子和准备主体，使用同源段号并去掉正式证据中重复的原文。native wire 绑定 options SHA 与
exact request，accept/rework/uncertain 均落本 owner 的 PrepareDecision，stop 只结束当前席位。
独立接受后才生成 Hook 三候选，再经既有 A/B 与最终定稿；Prepare 不能替代 gate.s6。
已购回答与 prepared 提交先走免费恢复；pending_commit 在队列与人工页给出 --recover-edit。

网络 attempts 是每次内容请求的独立有界预算，逐次记成本与会话事实；模型纠错仍占角色内容额度。
E/C 的工作单元不含 run/epoch/candidate/provider session，重启或换会话不补发。确定性 typed 提交
本身不调用模型；已保存 request/raw/receipt、typed 决定和 producer journal 优先重验、幂等补尾。
Blueprint 工作包消费当前章完整剧情、原文、必要人物与知识依赖和实际问题，不传逐事实义务目录。
范围接受绑定实际消费投影摘要；跨章范围独立反查原文和全书连续性。全部章级接受、KnowledgeLinks
与跨章接受同时有效后，运行确定性 seen/gate 收口；旧逐项回执和 `review_delta` 不参与新链。

`submission_options` 同时是可执行 binding：Stage2 既有字段 patch 的行数、顺序、subject id 与字段集合
逐字保留；集合新增/删除/拆分走模板给出的 hash-bound `collection_edits.operations`，不复制整组成功主体。
三种 operation 是关闭式形状：`add={op,item}`、`delete={op,subject_id}`、
`split={op,subject_id,items>=2}`，没有 `value` 字段；submission option 按模板实际 collection 下发
collection→member model 映射与局部 Pydantic operation schema；其中 `delete/split.subject_id` 由同一
冻结 workpack 的 current collection 稳定 ID enum 约束，合法 add 的新 ID 不受该 enum 限制。现存
direct patch 主体不从 enum 剔除；若同交 delete/split，仍由现役 GlobalEdit 跨写口规则拒绝。空集合
只开放 add，最终仍由现役 GlobalEdit 复验。
`submission_options[].exact_request` 仍是本地 canonical action 的完整模板；只有 headless Stage2 task
唯一开放的 submission option 恰为 `--apply-edit` 时，provider 回答才换成
`novel-stage2-reviewer-delta-v3` compact wire。多选项或非单一 scope 的任务使用
`novel-stage2-core-review-choice-v1`，覆盖当前所有已开放 typed 选项；单一 scope 沿用专用 scope wire。
这些运输最终都交原完整 action loader，不能让格式缩减吞掉分支。compact 只交被开放的
patch 行、collection operations、replacement 与 `post_edit_review` 判断，并以 option id 与
exact-request SHA 绑定当前模板。compact v3 的 `collection_edits` 是只含本模板开放集合的固定键
object：所有键 required、额外键关闭、每键值直接是专型 operation 数组；空数组默认表示本轮不提交。
只有 task 与 exact request 的 `waiting_on` 同为 `WAITING_ON_STAGE2_EDIT_CORRECTION` 且携 durable failed
edit 时才启用 merge/drop：此时空数组保留 failed baseline，撤销只走 opaque `drop_targets`；ledger
`purpose=correction` 本身不能启用该语义。旧 v1 row list 不兼容。Supervisor 从
这份 immutable exact request 注入 wrapper/action/flags、actor/source/workpack/chapter/default/
review_scope、collection identity/SHA 与 decision subject SHA，只为模型显式提交的业务行组装完整
request 交 `reviewer_tasks.load_action`，不得把未变 no-op 行带回 sparse edit；assembler 不判断哪些旧 review item 在修后仍存活，
该结论仍只来自 producer 的 apply/normalize/HARD/reenumerate 链。compact 原始 JSON 的规范 digest 只用于
拒因、重复请求和 no-progress；物化后的 full candidate/residue 使用其自身内容 digest，不能拿前者命名
后者。
旧 compact v1 当时只是 provider 瞬时运输而非 artifact；历史 ledger 的 digest/rejection 不迁移，已经持久化为完整
canonical ReviewerActionRecord 的动作仍按原记录重放。文本 JSON 解码递归拒绝重复 object key。
结构上可判断的 direct patch 与同集合 delete/split 同主体冲突由 canonical `GlobalEdit` loader
统一拒绝。依赖正式 Global/MainText 的 post-edit registry 与外键判据不在 orchestration 重写：
Stage2 回答先按 R-11.25 保存原响应、prepared receipt 与 full ReviewerActionRecord，复验原工作单元的
成本/会话尾账，再调用 producer 与正式执行共用的
纯候选预检；producer 先在修前 current collection 上预扫同一回答的全部 `collection_edits`，一次聚合
所有 stale/invalid operation diagnostics，之后才可应用任一 operation。业务拒绝以原
provider-response digest 记回该席并删除 action，不启动 canonical Executor，
且持久拒因只暴露可信 `collection/category/count` 与完整 `diagnostic_sha256`，不回显模型主体 ID、
正文或任意 `loc/repr/detail`；预检读盘、身份或审计异常则以
unknown 停止并保留 action 供零模型重放。
返工可在同一 typed 输入合并 GlobalEdit 与 `post_edit_review`；后者只准对模板 `review_scope` 中仍保持同一
`item_id + subject_sha256` 的旧项提交 `decisions`，且这类 surviving 旧 FAIL/UNVER 必须全部显式复判；
不能因 edit 逻辑上似乎修好就漏掉 hash 未变的集合完整性（例如只订正 relation 的证据定位，业务字段和 ID 集均未变时的
`relation_set_complete`）。新打开项才使用默认证明。typed loader 拒绝或 canonical action exit 2 时，
Supervisor 在仍有预算且可修复时把完整紧凑包与确定性拒因交回同一工作单元继续修订，
物理会话按当前 schema/profile 续用或重建；相同请求字节且无进展立即停。新内容调用计入该 Commander
基础额度与已生效人工追加的累计总额，不另发纠错轮次。权限失败、未知提交结果与明确 uncertain/stop 分别保留原因，
不能当普通模型格式错误重试；已保存决定的补提交不占模型额度。预算、授权、pause/stop、current blocker
与 admission 均关闭式生效，surface 自变不能重开预算或制造循环。已经 `pipeline_complete` 而
delivery 终验未闭合时只运行`novel/orchestrator/delivery.py` 的确定性终验，不追加无关模型 turn；next 自身不可读则降 unknown。

当前性能取证工具（`tools/workflow_metrics.py` 与 `tools/workflow_pairs.py`）已随 Stage2 语义
审阅退役（2026-09，工作流 v7）：其读数主体是 Stage2 Commander 工作单元与审阅盘面，v7 不再
存在该盘面。在 v7 上重建只读读数工具前，性能配对验收停在 `docs/REQUIREMENTS.md::TODO-49`
的未验事项；`ledger`/`llm_audit.jsonl` 的 `run_id` 与 `parent_run_id` 归属纪律继续有效——
精确 run 的成本只按显式身份筛，同 sid 旧 run、缺身份旧行与别的 run 全部排除，
不能按时间窗口或当前 artifact 倒推归属。

---

## 三、`JOB_STATE` 五态与 `reconcile`

取值面的唯一落点是 `contracts/invariants.py::JOB_STATE`：`running` / `ok` /
`landed_red` / `failed` / `unknown`。

**`unknown` 是真状态，不是缺省值**（`R-11.11`）：
服务重启后，原本 `running` 的**一律改判 `unknown`**——重启前它是死是活我们真的不知道。
写成 `ok` 就是凭空发一张通行证；写成 `failed` 是另一个方向的编造。

| 折叠规则 | 结果 |
|---|---|
| 有 `ended` 且 `exit_code == 0` | `ok` |
| 有 `ended` 且 `exit_code == 1` | `landed_red` |
| 有 `ended` 且 `exit_code` 为其他非零值 | `failed` |
| 只有 `started`，且本进程的互斥表里还占着这个键 | `running` |
| 只有 `started`，而互斥表里没有（服务重启过） | **`unknown`** |

⚠ **互斥表是进程内内存**，所以第四行成立的条件是「本进程没起过它」。
这条限制不能靠锁文件补，理由见 §八 第 5 条。

> ### `landed_red` 与 `failed` 是两种处置完全不同的终态
>
> | `exit_code` | 发生了什么 | 人该做什么 |
> |---|---|---|
> | `1`（`CHECK_FAILED`） | 产物**已经落盘**，校验报告有阻断级红行 | 打开那一层的屏看红行并裁决，**多半不必重跑** |
> | `2` / `3` / `70` | 压根没跑成 | 修完重跑 |
>
> 判据的唯一落点是 `novel/exit_codes.py::landed` / `landed_but_red`。
> exit 1 是独立的第五态 `landed_red`，并保留 `JobRecord.note`
> （`jobs.LANDED_RED_NOTE`）与队列的停止理由。它仍然**停住同书 lane**等待人工裁决；
> 状态分辨率提升不等于授权队列越过红报告继续扣费。

---

## 四、`ArtifactState` / `StageState` / `NovelOverview` —— 这本书跑到哪了

**`R-14.1`：已完成的阶段按【产物存在性 + 来源新鲜度】判定，不靠一份单独的进度文件**
——进度文件会与盘上的实际产物分家。

| `ArtifactState` 字段 | 类型 | 必填 | 内容 | 证据 |
|---|---|---|---|---|
| `rel` | `str` | **是** | `<stage>/<文件名>`。**取自 `contracts/schema/__init__.py::ARTIFACTS`**，不新建第二张清单 | **三源** |
| `exists` | `bool` | **是** | | **盘·码** |
| `bytes` | `int \| null` | **是** | 必填可空：不存在时是 `null`，不是 0 | **盘·码** |
| `mtime` | `int \| null` | **是** | 同上。⚠ **mtime 不参与新鲜度判定**，它只是给人看的 | **盘·码** |
| `freshness` | `str` | **是** | `FRESHNESS` 三态 | **三源** |
| `display_freshness` | `str` | **是** | 界面直接显示的**四态** = `FRESHNESS` 三态 + `missing`。⚠ `missing` **在服务端合并**——前端不许再拿 `!exists` 另算一遍（`R-11.4`） | **契·码** |
| `condition` | `str` | **是** | `ready` / `missing` / `invalid` / `unreadable`。界面只按这个 id 选文案，不再自己组合 `exists`/`invalid`/`readable` 三个 bool | **契·码** |
| `reason` | `str` | **是** | `FRESHNESS_REASON` 十值。**用 id 不用措辞**；其中 `legacy_blueprint_inputs` 只由 store 在旧输入键形精确、Main current、Global file match 且仅历史 consumed 投影不可重算时铸造 | **契·码** |
| `ghosts` | `str[]` | **是** | 指向已不在盘上的那些边，**按名字点名**（`Q-4` 亲自点过的 bug）。空列表也要写 | **盘·码** |
| `invalid` | `bool` | **是** | 盘上有一份 `.INVALID` 在等人处理 | **盘·码** |
| `invalid_freshness` / `invalid_reason` | `str \| null` | **是** | 活动 `.INVALID` 自己的来源三态与理由；与 formal 的 `freshness/reason` 分开，避免 formal+INVALID 混主体。二格只陈述来源账，不证明 INVALID 按当前 schema 可读；恢复命令仍负责强类型读取。无 INVALID 时两格均为 `null` | **契·码** |
| `readable` | `bool` | **是** | 能不能按当前 schema 加载。⚠ 见下面那条 | **盘·码** |

⚠ **`readable=False` 不等于 `exists=False`。** 盘上真有一份加载不了的产物
（`成年人不做选择` 那本的 `stage4/plan_chapters.json` 根本没有 `_provenance`）。
把它折成「不存在」会让界面说「还没跑」——而它跑过，只是那次把来源账丢了。

`global_naming_blocked` 是 Stage2 的专用 unknown：latest Global 候选虽能按当前 schema 读取，
但称名 HARD 尚未收口，因而不能铸成 Blueprint 可消费的投影。state/queue 只允许停在 Global typed
edit / `--revalidate`（0 LLM）等待人工决定；不得把它解释成可 `--renormalize` 的伴生产物缺失，
更不得生成默认付费 Global / Blueprint argv。

**`StageState`** = 一层的全部 `ArtifactState` + 一个折叠出来的 `done`。
`done` 的判据是**全部必需产物 `exists` 且 `readable` 且 `freshness != 'stale'` 且 `not invalid`**。
「一层要哪几份」取的是本层各节点 `NodeSpec.produces` 的并集，**不另写一张清单**
（原型那张在 `Web/novels.py` 里，是第二份，而它分家的代价具体可见：stage5 被迫整个排除在外）。

> ### ⚠ `done` 是**三态**：`True` / `False` / **`None`＝判不了**
>
> 这一格是实跑盘上三本时被逼出来的，不是设计洁癖。
> stage5 的产物是**逐章**的（`stage5/chNN.json`），做不了一次 `is_file()`；
> 它的判据是「stage4 计划里的每个输出章都有成稿吗」——
> **而那份计划读不回来的时候，盘上有 4 章这件事既不能证明跑完了、也不能证明没跑完。**
>
> 压成 `False` 的代价是**真金白银**：队列会去重跑一个可能已经写完的付费层。
> 压成 `True` 更坏：跳过一个根本没跑的层，下游拿着半份成品继续走。
> ⇒ 同 `FRESHNESS` / `JOB_STATE` 的 `unknown`，这是 `Q-2` 那条三态在完成度这一侧的样子。

> ### ⚠ `produces` 有**四种形状**，各有各的账，谁也不许拿另一条的答案顶替
>
> | 形状 | 例 | 占位符由谁决定 | 完成度落点 |
> |---|---|---|---|
> | 具名 | `stage7/name_map.json` | — | `artifacts[]`（一次 `is_file()`） |
> | 轮次编号 | `stage5/review_{n}.json` · `stage6/semantic_{n}.json` | 盘上有几份 | **`rounds_done[节点]`** |
> | 逐章 | `stage5/ch{nn}.json` | 计划里有哪些章 | `chapters_done` |
> | **书级参数化** | `stage7/{title}_rewriting.txt` | **这本书是谁** | `artifacts[]`（**先解析再筛**） |
>
> `required_artifacts()` 收第一种与第四种，于是**中间两种不作为正式清单进入 `artifacts[]`**。
> Stage5 有一个关闭式例外：逐章校验发现 missing/INVALID/unreadable/stale/unknown 时，
> 只把这些**问题投影**临时放进 `artifacts[]`，供总览与队列消费；干净章不重复列出，
> 问题投影也不计入 `artifact_count/latest_mtime`。
> ⇒ `rounds_done` 就是那第三本账。判据是**存在性不是新鲜度**（轮次产物没有单份
> 新鲜度账），这条限制写在 `resume_verdict` 返回的理由里，不藏着。
> ⚠ 它与 `done` **刻意不联动**：`stage5.review` 跑没跑过不改变「这一层的成稿齐不齐」。
>
> ⚠⚠ **第四种必须在筛选之前解析掉，而「谁是第四种」只能查声明表**
> （`contracts/artifacts.py::BOOK_SCOPED_PATHS`）**，不能从字面猜。**
> 它解析之后仍是**一份具名产物**——问的是「它叫什么名字」，不是「盘上有几份」。
> 而两处现役分类器都会误判它：`state._is_pattern` 只看 `'{' in rel`
> ⇒ 把成书整个丢出 `artifacts[]`（完成度、freshness 徽章、inbox 同时看不到它）；
> `paths.is_numbered_pattern` 曾只看「tail 里恰好一个占位符」
> ⇒ 把它当轮次编号，再拿 `title` 走 `isdigit()` 得到空集。
> 两条失效**都不报错**，形状是「跑过的那一层看起来没跑」。
> 守卫：`tests/test_orchestrator_state.py` 的
> `test_the_delivery_stays_in_stage7s_completion_surface_with_the_book_name` 与
> `test_every_placeholder_in_the_path_registry_is_claimed_by_exactly_one_classifier`。

⚠ **`.INVALID` 不计入已完成**，它进 inbox（`INBOX_KIND` 已有 `invalid` 这个值）。
⚠ **一份合格的新正式稿落盘时，同名 `.INVALID` 由 `ArtifactStore.write` 送进 `_rounds/`**
（不是删——那份稿花过钱，还可能被别人的 `inputs` 指着）。留着它的后果不是「多一个文件」，
是四个只看兄弟文件在不在的消费方一起说假话：语料库把已出书的书报成 `broken` ·
续跑判定去**重跑并重新付费**一个已有新鲜正式稿的节点 · 人定稿盖不上审批章 ·
下游整条链被 `inbox:invalid` 挡住。
⚠ 普通具名产物的 **`freshness == 'unknown'` 计入已完成**，这不是漏判：存量语料是在统一投影注册前生成的，
大量边仍为 `consumed=null`；把 `unknown` 当未完成会让升级后的队列重跑一批可能已完成的付费层。
「不知道它新不新」与「它是旧的」是两件事——后者才该挡路。
Stage5 例外：`done=True` 会直接让付费写作 SKIP，且 canonical 章已是退役 `draft.md` 后的唯一证据，
因此必须逐章达到当前 schema/version 且 `fresh`；未知会停在 `unknown`，不会冒充 current。

**`NovelOverview`** 另有 `chapters` 与 `stage5_legacy_paths`。`chapters` 只从可读的
`stage1/main_text.json` 主章表长度派生；stage1 未跑或读不回时为 `null`，语料库不为此
逐书追加请求，也不等 stage5 才猜章数。Stage5 的粒度是**章**（`R-14.2`），逐章状态只由
canonical `stage5/ch*.json` 给；文件名只做发现，每份仍须通过当前 schema/version/freshness。
`stage5_legacy_paths` 仅列旧 `deep/shallow` 只读证据，
并使该层进入 `legacy_incompatible`，绝不挑一臂折成 current。
⚠ **必须排除 `.INVALID.json`**——原型这里踩过；把被拦下的那份数进去就把语义反过来了：
续跑会跳过一章**根本没写成的**稿子，而那一章的钱到时候还得花。

### 4.1 续跑判定（`R-14.4`）——五态，`unknown` 同样不许折进别人

`resume_verdict(overview, node)` → `(结论, 一句话)`：

| 结论 | 什么时候 |
|---|---|
| `skip` | 产物在、读得回来、不陈旧 ⇒ **已存在即跳过** |
| `run` | 产物不在盘上，或盘上是一份被闸门拦下的 `.INVALID`；后者在队列里先停为 `inbox:invalid`，由人优先尝试 0 成本 `--revalidate`，不会直接重跑付费命令 |
| `redo` | **上游在中断期间变过** ⇒ **这不是「续」是「重来」** |
| `unknown` | 判不了；Stage5 章按当前 schema/version 读不回或 freshness 未知也停在这里，不折成付费重跑 |
| `legacy_incompatible` | 旧契约只作历史证据；不迁移、不冒充 missing，也不静默触发付费重建 |

⚠ 普通 durable 多步队列对 `redo` / `unknown` **都不自动执行**：前者可能重新付费，
后者连正确动作都尚不能判定；它们统一落空 `steps` 的 `resume:manual-decision`，
只负责**说出来并停下**。AgentRun 的 operator 是窄例外，因为它每落一步都会重新采
完整快照：有正式产物的 producer 只有在产物仍可读、没有 INVALID 或盘上既有却 unknown 的输出、每份 stale
输出的权威汇总 reason 都是 `consumed_changed`，且节点在 `NodeSpec.operator_redo` 登记
了唯一策略时，才把
原结论仍为 `redo` 的动作变成 ready 并铸 exact execution token。书级节点用默认完整重建；
Stage5 写作必须从完成度行导出 stale/missing 的精确 `--chapters`，不能拿空 flags 冒充
重写；同一 producer 的缺失输出可由 canonical 动作一并补齐。Stage5 Review 是另一条登记的
零成本编号新增策略，只要求专用 currentness 已明确
stale。producer 输出汇总为 `file_changed`、策略未登记或参数不唯一仍停给人。

> ⭐ **这一节在捡回一个被静默弄丢的能力。**
> 原型退役的 `_legacy/run_book.py:14-22` 有过真正的续跑：
> 「每步产物各自落盘、**已存在即跳过**」+「**route 漂移安全**：已存在的产物
> 若与当前 routing 不符 ⇒ 重跑」。改成 Web 编排之后**整个消失，
> 且没有任何文档说明是有意放弃**——今天原型的 `n5_write <book>` 缺省就是
> 全部输出章重新付费，唯一兜底是 `.llm_cache/`。
> `skip` 是那条「已存在即跳过」，`redo` 是那条「漂移检测」。

### 4.2 运行页的权威续跑前沿

`GET /api/novels/:sid/run` 除信标时间线外必须返回 `resume_frontier`。它与 `operator next`
复用同一次 `NovelOverview + ApprovalDoc + queue planner` 快照；信标只回答历史命令有没有打点，
不能用来猜“现在从哪续”。书目录尚未形成时唯一允许的状态是 `unknown`。

| 字段 | 内容 |
|---|---|
| `state` | `ready / blocked / pipeline_complete / unknown`；前三项来自 operator lane，最后一项只表示尚无法形成书级快照 |
| `node / seq / label` | 当前真实续跑点，必须逐字来自 `NODES`；完成或 unknown 时全为空 |
| `resume_verdict / why / waiting_on` | planner 对当前动作的判定、证据说明与 blocker；UI 不重算 |
| `paid / writes` | exact node/flags 的登记贵贱与写盘效果；不是前端估算 |
| `next_handler` | 后端闭合投影：免费 ready=`code`、付费 ready=`executor`、登记的确定性恢复=`executor`、reviewer blocker=`commander`、其余 blocker=`human`、终态=`none`、未知=`unknown` |
| `verified_skipped_nodes / verified_skipped_count` | 同一 planner 快照已明确无需再次执行、且严格位于续跑点之前的有序前缀；只能来自 `NODES`，数量必须一致 |

运行页默认折叠 `verified_skipped_nodes`，但允许展开审计；这只是呈现压缩，不删除步骤和信标历史。
普通规划返回续跑点之前已验证的完整前缀；原 owner 或 prepared 提交优先恢复时，不重读可能半提交的
审批材料，前缀可为空或短于续跑点。空前缀只表示尚未验证前置节点，不能按节点位置补造跳过证明。
例如 Stage4B 的 `request_replan` 会先显示 `stage4.context` 上的
`approval:stage4-structure-replan / commander`。若交叉核证得到具体结构损害，形成新的 non-all-accept
typed A 决定后才显示 `stage4.design / recovery:stage4-design-action / executor`；若只有 read-only
INFO/metric/UNVER/binding concern，则同包 typed stop 以 exit 2 结束本轮，不出现 execution permit 或
Executor。它不会把 Stage0～Stage2 的历史 pending 信标画成“必须从头执行”，也不会把既有 typed
决定误报成再次调用 Commander。

若 Stage4 repair/replan aggregate 已 prepared 而 source producer 已结束，前沿必须先显示
`stage4.design / recovery:stage4-design-repair-aggregate-commit / executor`。这条 0-wire 补尾
committed 后，才允许恢复普通 approval、inbox 或后续节点；已经 stale 的 review token 不再进入前沿。

Stage5 继续只使用 `stage5.review / approval:stage5-closure / commander` 这一张顶部卡，`waiting_on`
名称不代表当前一定提交 closure。current view 为纯 unresolved trim 时，同一席只可互斥选择：full-batch
`stage5.review --retain-pretrim-batch @reviewer-input`、其中恰好一章的 typed
`stage5.review --request-reviewer-rewrite @reviewer-input`、或 typed stop；ready/expired 时只给 closure
approval；缺章、活动 `.INVALID` 或 HARD 与 trim 并存时在开席前 stopped/2。retain batch 是一席人审后的
0-LLM node，不构造 Executor profile；部分落盘重启时，同一 durable record 在 exact prefix/current suffix
证明下零 Commander 补尾，全部 resolution 落稳后仍使用原 fresh Review，只因
`trim_subjects/wontfix_sha256` 变化重建 closure workpack/token。rewrite 回答先由 0-LLM node 落正式
single-chapter request；queue 随后在 stale Review/closure/新 Reviewer 前以
`recovery:stage5-reviewer-rewrite` 滚新 epoch，并只给唯一 paid writer 或 matching prepared 的 0-wire
恢复。正文真正改变后才重跑免费 Review。

---

## 五、`QueueItem` / `QueuePlan` —— 一批书（`R-13`，新增能力）

`plan()` 是**纯函数**：给它一批 sid 与一个目标 stage，它算出「这条链长什么样」，
**不跑、不写、不发请求**。于是「排这一批会花多少次调用、会写哪些文件」可以 0 成本地摊开给人看。

| `QueueItem` 字段 | 类型 | 必填 | 内容 |
|---|---|---|---|
| `sid` | `str` | **是** | |
| `node` | `str` | **是** | |
| `flags` | `str[]` | **是** | 空列表也要写 |
| `steps` | `Step[]` | **是** | 与真跑**同一个 `build_steps`**（`R-11.8`）。仅尚未满足人工闸门、因而命令必填凭据尚不可构造的 blocked 项可为空；可运行项为空时载体拒收 |
| `why` | `str` | **是** | 为什么排它（`产物不存在` / `上游变过 ⇒ 重来` / `.INVALID 待处理`…） |
| `waiting_on` | `str \| 键不在` | 否 | ⭐ 在等谁。见下 |

| `QueuePlan` 字段 | 类型 | 内容 |
|---|---|---|
| `items` | `QueueItem[]` | 顺序即执行顺序（`NODES` 的声明顺序就是处理链顺序） |
| `paid_items` | `int` | `items + blocked` 中花钱的项数。**闸门后面的命令也要在建队列时一次确认**，不能把“人后来清了闸门”偷换成付费授权；不显示预估花费 |
| `blocked` | `QueueItem[]` | 被人工闸门挡住的那些，各带 `waiting_on` |

### 5.1 ⭐ 「停在那里」的计数形状 —— 四个数不许合并

`R-13.4`：**一本书任一项失败 → 该书停在那里，不跳过、不继续往下扣费；其他书的 lane 继续。失败项与同书其后未跑的项分开计数。**

```
done: N     跑完了的
landed_red: 0|1  已落盘但报告红、把队列停下来的那一项
failed: 0|1      没跑成、把队列停下来的那一项
not_run: M  它后面那些，一步都没跑
```

**合并成「N 成功 / M+1 失败」是一次真实的信息损失**：后面那 M 项
既没有失败也没有成功，它们**根本没被执行**——而人要决定的恰恰是「修完这一项之后从哪儿续」。

⚠ 批量的风险与单本不同：单本跑错人当场看得见；**批量跑错是一批产物同时坏掉且没人在看**。
所以「停在那里」不是保守，是这条能力成立的前提。

### 5.2 `waiting_on` —— 说出在等谁

`R-13.5` 与 `R-0.3b` 用的是同一句话：「**明确说出在等谁**」。现役取值包括：

| 情形 | `waiting_on` |
|---|---|
| stage0 未批准（`APPROVAL_STATE != 'approved'`） | `stage0.approve` |
| stage5 尚未授权 current canonical 全书写作 | `approval:stage5-writing` |
| Stage5 Review 不是 `missing/fresh`，且本次不是已证明的上游 consumed-change 精确重写；旧结论不能驱动普通写作续跑 | `stage5:review-currentness` |
| 发现旧 `stage5/deep|shallow/` 布局 | `legacy_incompatible` |
| 文案未定稿 | `stage6.approve` |
| 人名表未定稿 | `stage7.approve` |
| inbox 非空（包括本层 current `.INVALID`、被驳回、连续未消） | `inbox:<kind>` |
| 普通队列的 `redo/unknown`，或 operator 无法证明唯一 canonical REDO 命令 | `resume:manual-decision` |
| 服务重启后旧快照仍含付费项、等人重新确认 | `queue:restart-confirmation` |
| 当前命令面与创建时快照不同、必须重建队列 | `queue:plan-changed` |

⚠ **队列本身不产生新的判定，也不放宽任何落盘闸门**（`R-13.6`，指回 `R-11.4`：
造第二个判官就是造第二个落点）。
这条**由结构保证**：`novel/orchestrator/queue.py` **不许 import `novel.report`**，
由 `tests/test_layering.py` 的一条 AST 测试钉着。

### 5.3 付费项一次性预先确认

`R-13.3`：**花钱的项必须一次性预先确认**，不能因为「排进队列了」就变成静默扣费。
确认时摊开的仍是**将要执行的那些命令**（复数——所以是 `steps[].argv`，不是一条）。

CLI 这一侧：没给 `--yes` 而计划里有付费项 ⇒ 打印全部命令并落显式终态 `ApprovalRequired`。

### 5.4 `QueueStart` / `QueueEvent` / `QueueRun` —— 可恢复的真实队列

`plan()` 仍是纯预览；`create()` 把确认过的命令冻结为 `QueueStart`，追加到
`Output/_service/queues.jsonl`。runner **只能执行这份快照里的 `steps`**：恢复前会重新读取
产物存在性、新鲜度与人工闸门，但重判只能使一项 `done/blocked/pending`，不能临时发明一条
用户没有确认过的新付费命令。

旧 `queues.jsonl` 的 `mode=null` 只在私有读盘投影中剥离；任一非空
deep/shallow 表明该快照依赖旧契约，整份计划投影为 `queue:plan-changed`
并清空可执行 `steps`。因此旧记录可被读取审计，但不会在公开序列化中出现 mode，
也不可能被 runner 重放。

| 模型 | 关键字段 | 作用 |
|---|---|---|
| `QueueStart` | `sids` · `until_stage` · `plan` · `paid_confirmed` | 创建时的目标与一次性授权快照；有付费项却没确认时载体直接拒收 |
| `QueueEvent` | `queue_id` · `kind` · `ts` · `item_index/state/job_id/exit_code/waiting_on/note` | append-only 状态变化；`created` 与 `item_state` 两种载荷严格互斥 |
| `QueueEntry` | `index` · `item` · `state` · 作业/等待信息 | 一项的折叠视图 |
| `QueueRun` | `queue_id` · `start` · `state` · `entries` · `counts_by_sid` | 整个持久队列的当前视图 |

条目状态是 `pending / running / unknown / done / landed_red / failed / not_run / cancelled / blocked`。
`running` 只在本进程 runner 活锁仍在时成立；服务重启后旧 `running` 先显示 `unknown`，再按
`jobs.jsonl` 与盘面恢复：作业已有终态就照终态折；作业 unknown 但正式产物存在且 fresh 就记
`done`、不重启。仍需执行时先重新 `plan_book`：命令面与冻结快照一致且剩余项全是 0 成本，才自动
续跑；只要仍含付费项就停在 `queue:restart-confirmation`，恢复时重新展示冻结命令，且 CLI
`queue-resume --yes` / API `confirmed=true` 才是新一轮确认；无确认的底层 runner 也不能移除该 block；命令面
已经变化则停在 `queue:plan-changed`，旧队列绝不把新命令偷换进已确认快照，需重新预览并创建队列。

每本书一条 lane，不同书并行。同书首个 `landed_red` 或 `failed` 后，后续未启动项逐条记
`not_run`；另一书不受影响。取消只接收 `pending/blocked`，被取消项记 `cancelled`，同书后续记 `not_run`；
`running` 明确拒绝，不能把已经在途的 LLM 请求伪装成可无损取消。

人工闸门每项启动前重读。`stage0.edit` 是可选人工能力，不是流水线必经闸门；三道真正的
approve 闸门读取现役类型里的批准字段，已经批准就跳过，未批准才持久显示 `waiting_on`。

---

## 六、`active_key(sid, node)` 与 409

`R-11.10`：**一本书同时只跑一个作业**（第二个请求 409）。Stage5 v2 已无并发写作臂；
不同 `sid` 生成不同键，因此不同书的后台作业与 CLI 子进程仍可并行。

互斥键的**唯一落点**就是这一个函数：

```
active_key(sid, node) -> str
    sid 为 null      → '_global'（上传新书那条路；串行比并发安全——
                       两个并发的摄入可能推导出同一个目录名、互相覆盖）
    sid 非 null      → sid
```

旧 jobs 记录的兼容只在私有 JSONL 解码器，互斥键与现役作业构造口都没有 mode 参数。
通用 `start()` 拿锁、执行可选
审批准备、追加 `started` 后立即返回 `running`，后台线程与同步 `run()` 共用同一个逐步子进程
执行内核。

⚠⚠ **这个函数必须只有一个落点，而这一条是拿一个真 bug 换来的。**
原型 `start` 写进去的是 `sid or '_global'`、而 `_run_chain` 释放时拿 `meta['sid']` 去查——
同一个键算了两遍，于是在 `sid=None` 上分家：**写的是 `'_global'`、删的时候查 `None`，
锁永远留在表里**。而上传新书这条路恰恰不带 sid，所以第一本上传成功、
第二本起全部 409，**直到重启服务**。

配套两条同样是踩出来的：

- **拿到锁之后到线程真跑起来之前，任何一步抛出去都要把锁还回去**——
  否则这本书被一个**从未存在过的作业**永久锁死
- **释放时调用同一个 `active_key`**——同一把锁不得在启动与释放两处重拼。

旧 `n5_write` / `n6_write`、deep / shallow 与臂级互斥只属于历史事件解码面。现役节点恒为
`stage5.write`，现役输出恒为 `stage5/chNN.json`；兼容旧记录不等于恢复旧命令能力。

---

## 七、`_service/` 的边界 —— `R-11.3` 收窄成「绝不落**产物**盘」

`R-11.3` 原文是「服务进程只读，绝不落盘，一切写操作走子进程」。
**它已收窄成「绝不落产物盘」**，理由是一句话：

> **「子进程被 OOM 杀掉了」这条记录只有父进程写得出来。**
> 子进程写不了自己的死讯，而退出码正是编排层判成败的依据（`R-12.3`）。

⇒ 服务进程写、且只写这些非产物服务状态：

```
Output/_service/jobs.jsonl        append-only 事件流
Output/_service/queues.jsonl      持久批量队列 append-only 事件流
Output/_service/agent_runs.jsonl  headless agent append-only 事件流（含双授权快照）
Output/_service/commander_admissions.jsonl Commander node/generic writer 六态准入账
Output/_service/commander_owner_leases/<run>.lock per-parent 顶层 Commander OS owner lease 载体（文件存在不代表存活）
Output/_service/commander_admission_tail_repairs/ non-LF 坏尾原字节与 authorized/applied 两相恢复账
Output/_service/agent_sessions.jsonl commander / executor CLI 会话审计（wire v2）
Output/_service/agent_progress.jsonl agent/runner 的 CLI 步骤观察轨迹
Output/_service/agent_progress_tail_repairs/ beacon non-LF 坏尾原字节与 authorized/applied 两相恢复账
Output/_service/operator_phases/<session_id>.jsonl Commander/operator 阶段墙钟遥测（每会话一流）
Output/_service/human_actions.jsonl 已落盘人工动作的 append-only 身份审计（不含正文）
Output/_service/human_actions_tail_recovery/<sha>.json 被 kill 截断的 human action 尾行隔离证据
Output/_service/stage5_writer_locks/<book>/book.lock Stage5 CLI writer / API closure 共用总锁
Output/_service/logs/<job_id>.log 子进程 stdout+stderr，按字节追加
Output/_service/ledger.jsonl      跨书逐请求成本事实
Output/_service/*.jsonl.lock      上面九类 JSONL 各自的追加锁载体（见下）
Output/_service/uploads/…         浏览器上传的原始小说字节
Output/_service/workflow.json    新 clean base 的固定 task_id 与工作流契约，不从旧 base 自动初始化
Output/_service/runs/<sid>/work_units.json 工作单元注册索引，固定 task/node/role/scope 身份
Output/_service/runs/<sid>/work_units/<unit_id>/ledger.json 每角色工作单元的内容轮次、attempts、响应/决定/提交状态与最大 4 次额度
Output/_service/runs/<sid>/work_units/<unit_id>/<sha256>.json 不可变 request、raw response/prepared receipt、typed 决定、feedback 与提交证明
Output/_service/edits/<sid>/…     交给 CLI 的一次性 typed 编辑稿、人工决定或批处理 manifest
Output/_service/edits/<sid>/reviewer-action-<sha>.json 已通过 typed loader 的 Commander 决定；绑定 work_unit_id/work_unit_turn_id、原 logical session 与 provider response SHA；它可零模型重放但不授予执行 permit（R-11.25）
Output/_service/runs/<sid>/reviewer_action_authorities/reviewer-action-<sha>.json Stage5 canonical rewrite request 对应 ReviewerActionRecord 的 immutable 原始字节归档；内容 SHA 必须等于文件名，并以该原字节 SHA 对证 receipt/request；不得用 typed decode 后补入默认字段的重序列化摘要代替
Output/_service/runs/<sid>/reviewer_action_authorities/reviewer-action-<sha>.receipt.json 上述归档的 authority commit point；绑定 request raw/self、stable business/current-then contract、原 session/seat/provider response 与三账 evidence SHA，自身再 self-hash
Output/_service/runs/<sid>/sessions/<session_run_id>.json Commander 一席的在线 provider session 指针（`CommanderSessionPointer`）：首个协议事件到达即写，同席 resume 原地覆盖；hash-bound 到本次 prompt/schema/profile，只是运维事实，不是审计账也不是预算（R-11.26）
Output/_service/approvals/<sid>.json 人工闸门、已阅状态与 canonical 写作授权（原子替换）
```

#### 当前工作单元与决定恢复

`ReviewerActionRecord` 不是执行授权，也不替代 Supervisor 的 live permit。通用字段为
`kind / sid / run_id / epoch_id / waiting_on / surface_seal / business_sha256 / recorded_at`；
approval 形状携带 `surface / payload / review_token`，node 形状携带
`node / flags / input_payload / review_token`。新模型决定必须关联以下原调用证据：

| 字段 | 类型 | 新 Commander 决定 | 含义 |
|---|---|---|---|
| `work_unit_id` | 64 位 SHA-256 | **是** | 固定 task/node/role/scope 的身份，scope 由 NODES 登记；candidate/run/session 不参与 |
| `work_unit_turn_id` | 非空字符串 | **是** | 同一角色单元具体内容请求；占用基础额度与已生效人工追加的累计总额，不因重启变化 |
| `reviewer_session_run_id` | 非空字符串 | **是** | 原 Commander logical session，恢复新 parent 不重签原成本 |
| `reviewer_seat_turn_id` | 非空字符串 | **是** | 兼容字段名，现役值等于 `work_unit_turn_id`，不引用旧席位预算 |
| `provider_response_sha256` | 64 位 SHA-256 | **是** | 原始模型回答的规范字节摘要，不以 assembler 展开的 request SHA 代替 |

恢复先验证 immutable request/raw 与 `prepared_receipt` 中冻结的原 cost kwargs、attempt/turn 事件。
`reconcile_commander_call` 与 `reconcile_commander_event` 严格比较已有行，缺尾才幂等补原行；
任何字段漂移、重复矛盾或不能闭合的会话顺序均拒绝。跨 parent 补尾先取得原 owner-stopped lease。
`validate_reviewer_record` 对账原 request、response、typed action、等待面的登记属主与完整成本/会话证据；
schema 配置拒绝恢复还必须复验原日志、schema 和代码证明，仍消耗原内容轮次。

raw 成功后先保存 typed 决定，再由当前 loader、producer 与 live permit 消费。一次重建与 loader 回调
复用唯一活动记录；已提交决定标为 adopted，planner 前严格验证原 adopted 记录并退休中断残留，
不受新候选或新 Reviewer 配置影响，也不重新制造审批。失败或 unknown 不凭文件存在洗成采用。

Stage5 `node=stage5.review` 且 flags 恰为 `--retain-pretrim-batch @reviewer-input` 或
`--request-reviewer-rewrite @reviewer-input` 时，还成组保存 `stage5_review_sha256` 与
`stage5_reviewer_contract_sha256`，`review_token` 为 null；payload 内完整有序 audit/chapter 双 SHA
绑定处置范围，不携随处置变化的 closure token。retain 与 rewrite 是同一工作单元的一次互斥回答。
Review/contract 已换代、精确已落前缀 + current unresolved 后缀、身份不可读三种情况分别处理：
已证明换代才退休，合法剩余范围只重放原 actor/reason/items，坏 inventory、错序或 foreign 决定保留
记录并 unknown 停止。partial batch 的 exit 2 保留唯一决定，后续只能零模型重验或明确退休。

reviewer rewrite 的 FREE request producer 先 typed readback，再把 record 原始字节与 authority receipt
两相封入 `reviewer_action_authorities`，最后退休活动记录；receipt 是 commit point。
queue 在 stale Review、closure 与新 Reviewer 前先处理 request，创建当前 execution epoch 后才授权
`stage5.write --reviewer-rewrite`。Executor 仅按已保存 request 指定章节修订，不重新调用 Commander，
不复用旧 provider 物理 session。正式 request 不是 permit；后继仍逐写核验 Review/audit/正文及精确前后缀。

#### 冻结旧工作流记录（只读）

旧 `stage2-prereview-<sha>.json` 与 `Stage2ReviewLedger` 的
`stage2/global_review_ledgers/<sha>.json`、`ReviewerSeatLedger` 的
`reviewer_seats/<blocker>/<business_sha256>.json` 仅解释冻结旧工作流。旧协议曾使用预审、四席/前三席
订正、第四席终审和按业务换席；新工作单元不读取它们来发预算、不新建它们、不通过归档开启空账。
旧 `workpack_sha256 / source_sha256 / main_text_sha256 / progress_sha256 / blocker_count` 字段组、
`purpose=prereview` 仅保留历史账解码意义；预审记录、终审 action、旧 finish/complete 与重开命令均无现役写口。

旧 `protocol_recovery` 的 `codex-recovered-transport-notice-v1`、
`codex-redundant-option-suffix-v1` 以及 `schema_recovery_response={response, exact_request}`
也只解释原账如何恢复，不是新回答宽免出口。原 raw/日志/schema/session/cost 证据仍不可改写，
新代码不把它们补成当前模型来源。旧 base 的实际恢复使用 `docs/OPERATIONS.md` 登记的冻结代码，
核对原审计 code_version；不得把旧审批、旧 token 或旧账迁入当前 clean base。

#### 当前 Stage2 producer 提交收敛

reviewed `stage2.global --apply-edit` 在 canonical 子进程内还有更晚的 producer
事实。Supervisor 每次读 planner 前都先用 `ReviewerActionRecord` 及当前工作单元对账。
原回答、typed 动作、成本和会话证明、已提交产物及当前节点接受完整闭合后，先标记原 turn
已采用，再退休活动 record；删除中断仍可据已采用证明免费补尾。失败 turn 保留给当前
Reviewer 的拒绝恢复，不把终态 sidecar 或 generic revalidate 误认成已采用动作。
若 input journal、`canonical_pending` 或 apply-edit formal journal 仍为
prepared，必须优先交给 `recovery:stage2-global-reviewed-edit-commit` 的确定性 Executor，且只允许
exact `stage2.global --apply-edit stage2/global_edit.json`；首次 canonical 文件尚未出现、只有 input
WAL 时，permit 也不伪造一份 external-input 字节身份，由 producer 从 WAL target 物化并收敛。
若 formal journal 的 `action_kind=revalidate`，则唯一恢复动作改为逐字复用 target audit 冻结的
`--revalidate --actor ... --reason ...`；generic INVALID 没有 canonical edit/formal 也不改变这条
身份。所有补尾均为零 Commander wire。

Stage2 Global/BP 两份接受落盘后，`close_stage2_accepted` 只核验当前接受与 token，顺序补齐
`s0 → s1 → seen.read → seen.global → s2`。中途失败保留已落 raw 证明，由
`stage2.blueprint --close-review` 零模型补提交；不追加第四席或新的全局审阅。

它们**不是产物**：不进来源账、不进 `_provenance`、`ARTIFACTS` 里没有它们；
`tests/test_layering.py` 的写口与路由所有权白名单为每一类带着明确理由。

> ### 九类 JSONL 的追加口只有一个：`novel/appendlog.py`
>
> 写入方**不止服务进程**：`agent_progress.jsonl` 由 N 个 agent 各自的
> `novel.cli mark` 子进程写，`jobs.jsonl` / `queues.jsonl` 由服务进程**和**
> CLI `queue` 一起写，而 `R-13.7` 允许不同小说并行。
>
> ⇒ 追加同时握**线程锁**（同进程内多个写手）与**操作系统文件锁**
> （Windows `msvcrt` / POSIX `flock`，`<目标>.lock`，30s 超时后显式 `TimeoutError`）。
> 统一追加口落地前只有 `ledger.jsonl` 有那把文件锁，当时其余五份仅有线程锁——
> 而读侧「只容忍**最后一行**」的容错在并发下兜不住：A 进程的半截行会被
> B 进程紧接着写的完整行**顶到中间**，于是读侧对一次正常的并行跑开火，
> 且是**所有书**的视图一起失效。锁补齐之后那条容忍才真正成立。

新写入的 `agent_progress.jsonl` 行带可选 `run_id`：jobs 子进程沿用
`job_id`，headless agent 的全部 CLI 沿用 agent run ID，operator runner 则在一次
首轮、batch repair 与唯一 resume 之间显式复用同一值。该键只用于恢复身份和观测联结，
不进入正式 artifact provenance，也不参与 cache key。旧行没有该列时仍可读，但只能标为
legacy / ungrouped；成本汇总不得用时间窗口把它猜进某个新 run。

ledger 与 `llm_audit.jsonl` 的 `run_id` 是 checkpoint/adoption 共用的 producer 身份；v2 AgentRun
内它等于当前 epoch 的 `session_run_id`。当外层 AgentRun 与 producer 不同时，新行另带显式
`parent_run_id`，直接 job 则省略这格并退回自身 `run_id`。按 run 取证的任何工具都必须
沿这条显式 parent 投影筛 ledger/audit，并按逐字相等的 `run_id` 筛 `agent_progress.jsonl`；同 sid 的
旧 run、缺身份的旧行与别的 run 全部排除，不能按时间窗口或当前 artifact 倒推归属。产物、人审与
结构面仍是当前盘面快照，不携 `run_id`，不得反向加入目标 run 的代码
身份。不带 run 过滤的跨历史汇总与单次 run 口径不得混称。

`human_actions.jsonl` 当前只写 `HumanEditAction` wire v1 的 `prepared → committed`
两相行：`request_id` 绑 preview 意图，`action_id` 再绑预期结果 SHA；其余字段是
`action=stage5_edit`、sid/章号、自报 actor/reason、`formal｜working_draft`、preview 时
current canonical 完整文件 SHA、preview subject SHA、写回后 canonical 完整文件 SHA、新增 HARD
规则、目标相对路径，以及写该 phase 的 run/code/时间。同书总锁→逐章锁的线程+跨进程所有权覆盖
“重算双 SHA → prepared → canonical/trim 写入与读回 → CheckReport → committed”。普通异常
补偿产物而保留 prepared 失败证据；kill 留下的 prepared + 精确 landed SHA 由新 run 只补
trim/report/committed，不造新轮。公开读视图只认 committed，同一 request 不得 committed 为两份结果。
账不复制 segments/正文；尾行被 kill 截断时，追加口先把原字节内容寻址隔离到
`human_actions_tail_recovery/`，再截去坏尾补相；中间或已换行的完整坏行仍关闭式失败。
全部 Stage5 canonical/INVALID/recheck/revalidate/Review/wontfix/trim writer 与 closure submit 共用一把位于
`_service/` 的书锁，closure 的二次 workpack 与 approval/gate 写回在同一临界区；逐章提交再按序取章锁。
正文身份由 SHA 联结，
账本不能反向充当产物或 Review。

`approvals/<sid>.json` 的盘上服务记录当前为 **v8**；HTTP 投影 `ApprovalDoc.v=8` 是另一条
版本轴，不能据同一个数字互推。每个已盖 gate、已阅状态、typed Stage4A 设计审阅、最终结构裁决和 writing 授权
都必须带一份 current-subject proof：当前 `stage0/clean_text.txt` 的完整 SHA-1、所裁决正式
产物的完整 SHA-256，以及决定本身的规范 JSON SHA-256。读取时必须重新走当前 schema、typed
人工决定与 freshness 校验并逐字节比对；根变化、上游变化、正式产物被换字、损坏、出现
`.INVALID` 或发现旧 Stage5 双臂布局，都会让对应决定及依赖它的下游决定在有效视图中失效。
普通产物主体是正式文件字节；`s7` 是有意的窄主体：当前
`surname_map + characters + extra_subs` 决策面与完整 typed `ApprovedNameMap` 的规范摘要。
因此 `stage7.assemble` 写回 `book_txt` 交付账不会自废 s7，而决策面、actor/reason/origin
或 subject hash 变化仍会失效；读取还必须先证明 approval 存在且 subject 与当前决策面一致。
当前工作流只在新 clean base 写入 v8 记录；审批变更和新写作业的公开入口先验证当前 workflow manifest，
不能用 reset/backfill 把旧 base 收进新协议。旧 v1～v7 审批解码与迁移分支已删除；
HTTP、CLI 及服务补录只接受当前 v8，缺版本、未来版本和损坏均拒绝且保留原字节；旧版本、旧 token、旧 Binding 不手工补字段冒充现役来源。
普通 GET、事件观测与历史读取保持只读，损坏或证据不够保持 unknown。当前 Stage4 必须先有真实 A 审阅，
由 B producer 产出当前 Binding/Plan，再经独立 B 结构审阅；不得从既有产物或付费记录倒造审阅。
队列在一把锁内读取 current 结构裁决、
writing 授权与盘面；不得先查文件、再另读授权，从而留下两次读取之间主体变化仍生成付费命令的窗口。

⚠ 契约收窄时必须同步更新需求与 schema 文档；运行时只保留收窄后的当前规则。

### 7.0 `agent_sessions.jsonl` —— 内部 session wire，不是公开 HTTP 模型

该账记录 commander / executor 各自的多轮 CLI 会话，供重放、成本对账和失败定位使用；它不属于
artifact，也没有同名公开响应类型。`wire_version=2` 的每行键集固定为：

`wire_version` · `session_run_id` · `parent_run_id` · `role` · `node` · `chapter` ·
`subject_sha256` · `profile` · `provider_session_id` · `turn_index` · `attempt_id` ·
`requested_model` · `actual_model` · `requested_effort` · `actual_effort` · `cli_version` ·
`protocol_version` · `start_code_version` · `end_code_version` · `skill_sha256` ·
`prompt_sha256` · `schema_sha256` · `usage` ·
`process_state` · `delivery_state` · `error_kind` ·
`delivery_attempts` · `same_run_recovered` · `first_failure_phase` ·
`first_failure_detail` · `kind` · `ts`。

wire v2 在 v1 字段集之上只新增 ended 事件的提交诊断四元组，其余逐字相同；v1/legacy 0 只作
只读接受，缺失事实投影为 `null`。

| 轴 | 现役约束 |
|---|---|
| 事件 | `kind=started / turn / ended`。turn 必须有正整数 `turn_index`、`attempt_id` 与 `provider_session_id`；started / turn 绑定 prompt 与 schema hash；ended 显式封存两条终态轴 |
| 身份 | `role=commander / executor`；commander 不带 node/chapter，其 `parent_run_id` 指外层 AgentRun；executor 的 node 只接受 `AGENT_EXECUTOR_NODES`，其 `parent_run_id` 指 checkpoint/ledger 共用的 producer run。整书归因经 AgentRun started 事件登记的 epoch `session_run_id` 集合闭合，不按时间猜。新写入必须有 subject SHA-256 与已解析 `AgentProfile`，requested model/effort 必须与 profile 相等 |
| 实际执行 | requested 与 actual model/effort 分格，CLI 与 protocol 版本分格；每个新事件带启动代码版本，ended 另带结束版本，代码漂移时不能声明 delivered。缺失事实留 `null` / `unknown`，不能把 requested 冒充 actual。checkpoint 的 adopted model 是另一格：actual 非空时须精确相等；actual 缺失时只接受 capability registry 登记的 `driver-enforced` 单模型无 fallback argv，并要求 turn 留有 CLI 版本 |
| 成本 | `usage` 的 prompt、cache hit/miss、completion、reasoning、total token 均可为 `null`；`null` 是未观测，不是 0 |
| 终态 | `process_state=ok / failed / stopped / unknown` 与 `delivery_state=delivered / blocked / failed / unknown` 正交；进程退出不推出产物已交付 |
| 提交诊断 | ended 四元组只描述正式提交史：`delivery_attempts` ≥ 1 为发生过提交尝试；`same_run_recovered=true` 必须 `delivery_attempts≥2`；`first_failure_phase` 属于 `STAGE5_COMMIT_PHASES`；`first_failure_detail` 经生产侧截断脱敏（≤512、无控制字符）。四者皆可 `null`（未记录/未尝试），不得由其他状态倒推 |
| 隐私 | 只存 prompt / schema 的 SHA-256 与历史 skill 摘要，不存 prompt、响应正文、stdout 或 stderr；`first_failure_detail` 只收异常摘要，同受此边界约束 |

旧行没有 `wire_version` 时只在读盘边界投影成 legacy wire 0；wire 1 按 legacy 接受（v2 四元组为
`null`）。缺失事实继续为 `None` / `unknown`。新写入必须用当前 wire 版本，不得生成 wire 0/1，
也不得借当前 profile 默认补全旧 session 身份。

#### 7.0a Stage2 blueprint agent checkpoint —— 书内恢复态，不是公开 HTTP 模型

`stage2.blueprint` 的每个 phase/purpose/chapter 使用独立 executor session；run 首次进入时冻结
`protocol_version=node-executor/v1`、resolved `profile`、`session_isolation=per-purpose-chapter`、
`recovery_policy=stage2-blueprint-v1` 与每相的 response model/schema SHA、requested output token ceiling
及 content 偏好。agent policy 不伪造旧 LLM 的 model chain、rungs、thinking 或 provider `max_tokens`；
没有 discriminator 的旧 run/checkpoint 继续走 legacy v1 验证，不按新协议补字段。

一份 agent checkpoint 只在当前 schema、dynamic validator 与逐章 scope 已通过后写入，保存
`acceptance_protocol=node-executor/v1` 以及 accepted attempt/fingerprint/evidence-backed adopted model、accepted stdout、
normalized text、resolved profile、logical subject、session/provider session/turn、prompt/schema SHA、
exact accepted request、全部 attempt links、recovery attempts 与 `salvaged=false`。同份记录另封存当前
parsed payload 与 typed payload 及各自 SHA。accepted request 精确绑定 node/chapter/purpose、system/user/
schema SHA、requested model/effort、requested ceiling 与 protocol；成本 links 的 agent 请求不得伪造
legacy thinking 或 provider max tokens。

写入与恢复都必须把 accepted attempt 唯一联到 `_service/agent_sessions.jsonl` 的 current wire
`started + turn`，逐项核 parent run、node/chapter、profile、subject、provider session、turn index、
prompt/schema 与 requested/actual model/effort，再与 ledger 的 attempt/fingerprint/response SHA 闭合。
模型证据统一由 `novel/agents/model_identity.py` 重算：回显 actual 必须精确等于 profile；未回显只允许
registry 登记的 `driver-enforced`，且该 session turn 必须有 CLI 版本。后一种 checkpoint 的 `model`
记录 adopted identity，但 session actual 仍为 `null`、ledger model 仍为 `unknown`；恢复、历史采用、
聚合和 commit 均不得把它们倒填为已回显。未登记 driver、缺 CLI 版本或任何非空冲突一律拒绝。
恢复会认领 prior attempts，但不 probe/start、不新增 wire，也不把旧 provider session 伪造成可 resume
的 live client。联合 HARD 后确需修订时仍属同一工作单元：进程内优先连续 resume；旧物理会话不可恢复或
输出 schema 确有变化时可新建物理会话，但不得重新发放内容预算。checkpoint 首次写入不可覆盖，最终整书 compile、
联合 HARD、正式 artifact/`.INVALID`、commit journal 与 readback 终验仍只由 producer 代码决定。
该 checkpoint 会保存被接受的 stdout；上方 session audit 仍只存 hash 与计量，两者不能混读。

Agent CLI 的 delivery attempt 与上述语义 turn 正交：实现中分别命名为
`max_delivery_retries`（默认 3 次重试，即首发在内共 `max_attempts=4`）和
`max_semantic_turns`（默认最多 4 轮候选/纠错）。首轮语义请求走 `start`；已有可信 provider session 后的
语义纠错走同一 session 的 `resume`，不是独立重采。交付失败发生在取得可采纳回答之前，只消耗 delivery
attempt，不消耗第二个 semantic turn，也不建立可恢复的 provider turn。

429、408/425/5xx、已识别连接/timeout，以及
`403 Request not allowed` 且供应商 token 明确全为 0 时，首发失败后最多按 2/4/8 秒再发三次；每次
真实发送各占 ledger 一行并写 `attempt/max_attempts/retryable/will_retry/backoff_ms/status/error_kind`。
未形成可采纳回答的失败不进入 `agent_sessions.jsonl` 的 turn，也不进入 checkpoint `attempt_links`。
五小时用量窗口（包括 Claude 的 `session limit`、`rateLimitType=five_hour`、`out_of_credits` 与带 reset
时刻的信封）与登录过期不作秒级热重试，其他 403 作为永久权限错误停止。若一批所有缺章都只因登记的
可恢复交付错误结束，producer 在逐章 future 全部 drain 后原子写 `delivery_recovery.json`；该文件只含
run/code/phase、completed/pending/deferred 章、稳定错误类别/status/attempts、请求全集身份与 `run.json`
SHA，不保存错误正文。下一轮同 run 启动先撤销旧等待标记，再逐份严格恢复成功 checkpoint；本轮仍失败
才重铸标记，全部成功则保持删除。Stage4B 不一次性向 executor 提交整本 future：只维持并发窗，首个
可恢复交付失败后停止接纳新章并 drain 已在途章，因此尚未发 wire 的 deferred 章可以在恢复后再请求。
该 sidecar 不是正式 artifact、缓存或跨代码迁移面。

Stage4A 的普通 live checkpoint 恢复同样只认当前 run。每份 typed targeted repair/replan 都先打开
fresh producer epoch；代码 漂移也会打开新 epoch。只有 targeted repair 的目标章可以走显式
historical response adoption：target 先创建自己的 immutable run
identity，再逐字段对证 source/target profile、完整请求、schema、input、ceiling 与恢复协议，并用 source
自己的 code/session/checkpoint/attempt/ledger 加当前动态 validator 复验响应。只有唯一最大来源可写
target `design/historical-checkpoint-adoption.json`；并列或零来源直接不采用、由 target 新发 wire。旧
provider session 不注册进 target pool，source revision 不复制，正式 lineage 和 usage 分别保留 manifest
指针与 source/target 分账。策略 unit、普通首轮和 strategy replan 不在该桥内。

repair/replan aggregate 的 prepared 提交尾窗是另一条显式跨 ended-producer 边界，不是 historical
response adoption。source v3 journal 先封存 producer parent、repair/replan scope、完整 unit sources、
候选 baseline/target 与 full typed provenance；新 recovery run 只能在唯一物理 prepared、source
drained/ended、当前 truth/input/code/compiler/HARD 与 source checkpoint/session/attempt/ledger 全部闭合时，
以 baseline→写 sealed target、target→只验证、第三态→拒绝的两态协议补尾。任何候选写之前，先对 source
`artifact_adoption`、source-stable canonical relation 与当前 recovery run/parent attempt 做全局
0-or-exact preclaim；JSONL 无 LF 尾、重复身份、额外字段或另一 Stage4 prepared 都关闭式拒绝。
审计事件固定为 `kind=stage4_repair_aggregate_recovery` 与
`kind=stage4_repair_aggregate_recovery_attempt`：共同绑定 stable recovery id、source run、source journal
raw/self SHA、artifact/fingerprint、target file SHA 与 delivery kind；attempt 另绑定实际 recovery
`run_id/parent_run_id`。候选验收后先幂等补 source adoption，再追加两层 recovery audit，最后才把 source
journal 标为 committed；committed 下游每次仍逐字要求 journal 所记 adoption/canonical/attempt 三类账。
补尾不复制 source checkpoint，不续 provider，也不放宽 strategy replan 禁止 historical response
adoption 的边界。

若 Stage4B 旧 run 已结束，新 run 在 wire 前通过生产采用入口复验历史章 checkpoint。
允许多来源、部分或全章采用；聚合由独立 deterministic commit 身份绑定当前输入及每章来源，
无需本轮至少一个模型 checkpoint。source run/profile/request/schema/checkpoint/session/ledger、
当前候选与动态 validator 必须全部闭合；歧义或损坏来源不得猜测采用。来源原账保持不变，
当前 unit lineage 引用 source 身份，历史成本保留在原 ledger，不复制冒充本轮费用。
具体采用与补尾由 `novel/stages/stage4/plan.py` 及 `novel/llm/stage4_runs.py` 负责。

#### 7.0b Stage6 semantic A/B checkpoint 与 commit journal —— 双盲恢复态，不是公开 HTTP 模型

`stage6.semantic` 的 agent run 首次进入时冻结 `protocol_version=node-executor/v1`、resolved `profile`、
`session_isolation=per-purpose`、`recovery_policy=stage6-semantic-v1` 与 A/B 两相各自的 response
model/schema SHA、requested output token ceiling 和 content 偏好。A/B 必须使用两个不同 driver 对象、
两条 logical session、两个临时 cwd 与不同 provider session id；两个 driver 的元数据与复用的一份
只读 probe 所声明的 driver/provider 仍须与同一 resolved profile 闭合。把同一对象或同一 provider session 给两名 judge 都在
双盲边界关闭式拒绝。

每名 judge 只有一份不可覆盖的验证后 checkpoint：精确绑定 role/purpose、实际 `(system,user)`、
response schema、resolved profile、subject、requested ceiling、accepted stdout/normalized/typed payload、
attempt links、ledger response SHA 以及 logical/provider session/turn。恢复时重新闭合 current code、输入、
prompt/schema、typed validator、ledger 与 `_service/agent_sessions.jsonl`；**两份 checkpoint 都完成预检后
才允许任一新 wire**。完整恢复不构造 driver、不 probe/start、不发 wire；单边恢复保留已验证一侧，
只为缺失角色新建 driver/session 并请求，不把历史 provider session 冒充 live resume。A/B 都缺失时，
两份 client 先预建并闭合同一只读 probe，再由 agent 并发请求；任一成功侧可以保留 checkpoint，
另一侧失败不能让单份结果冒充双盲完成。

正式 artifact 仍只由 Stage6 producer 写。代码持有跨线程/进程的 commit ownership，选择唯一 seq 后按
`prepared journal → ArtifactStore 正式写入 → HookSemanticReview typed exact readback → 恰好两条
artifact adoption → committed` 收口；foreign prepared 序号不能被 legacy 或另一 run 抢走，尾窗恢复
必须复验 journal 的 artifact/finalization SHA 与两份 ledger identity。双盲响应的 schema/dynamic 校验、
匿名标签闭合、合并、当前输入与代码漂移检查、最终交付验证都由代码承担，agent 自述或进程成功不能
替代。checkpoint 与 journal 都是
`<base>/_service/runs/<sid>/stage6/<run_id>/` 内部 sidecar，不是正式 artifact、缓存或
公开 API 类型。旧书内 `_stage6_runs` 已退役；不再将其 run、checkpoint 或 journal 复制到 service 根。
当前 Stage6 run 必须显式保存完整 `experiment_arm`；缺字段或 null 的旧记录保持未知，
不能补成默认实验臂后继续采用。新运行默认仍使用 0 ms，两侧 checkpoint 恢复须闭合同一冻结实验配置。

#### 7.0c Phase5 executor checkpoint / commit journal —— 现役 producer 共用一套 acceptance

所有运行记录的读取、历史采用和免费恢复先经 `novel/runtime_layout.py::stage_run_roots`
检查布局。当前只有 `<base>/_service/runs/<sid>/stageN/` 一条来源；旧书内
`_stage2_runs` / `_stage4_runs` / `_stage5_runs` / `_stage6_runs` /
`_stage7_runs` 即使为空、不可读或损坏，也明确拒绝，不能当作无历史而重新付费。
当前 run、checkpoint、prepared、Reviewer rewrite journal 和恢复 manifest 不搬运旧字节；
原 service 根中的有效尾窗与同工作单元累计账仍可按原身份免费续完。旧代码与整根归档配对读取。

Stage2 Global、Stage4 design/plan、Stage5 write、Stage6 hook 与 Stage7 rename 不另造 acceptance
协议；都复用
`novel/llm/agent_runs.py` 的 `node-executor/v1`、append-only revision checkpoint 与
`prepared / committed` journal。各薄 adapter 只登记路径、purpose、recovery policy、session isolation、
允许触碰的正式/伴生路径和同书 foreign prepared 排斥：Global 是 `stage2-knowledge-v2` 且 per-node；
Stage4 分别是 `stage4-design-v1` / `stage4-plan-v1` 且 per-node；Stage5 是 `stage5-write-v2` 且
per-chapter；Hook 是 `stage6-hook-v1`、Rename 是 `stage7-rename-v1`，后二者均 per-node。

Analyze 的联合首次提交另以 AnalyzeReceipt/AnalyzeRecoveryJournal 冻结两侧基线、局部到正式
ID 映射及共享已购来源。Prepare 复用 agent_runs 的原请求/回答/checkpoint 保管，recovery policy
为 `stage6-prepare-v1`、session 为 per-node；首次发布与免费修订/决定共用 PrepareMutationJournal
冻结 CAS 目标，费用采用与会话尾账闭合后才 completed，不把它冒充第三份创作知识产物。

Global 每个 accepted turn 保存完整 Knowledge candidate、MainText 身份和候选表身份；静态人物由
独立 `stage2.characters` 完整候选负责。旧 role/fate 窄修信封、accepted patch 计数与编译入口退役。
两个现役 owner 分别遵守同一工作单元累计额度，恢复不能换 run 清零。dynamic-validator 拒稿先封存
精确当轮响应与验证身份；人工 replacement 必须重放当前同一 validator。正式尾窗只在同书所有权内
走 `prepared → 所属正式/伴生产物 → typed readback + freshness → adoption → committed`；foreign
prepared 先拒，新 run 不得覆盖中断窗口。历史窄修审计行保留只读，不能据此产生现役候选或追加额度。

所有 executor 共用 `agent_json_schema` 生成的 provider wire，不按 Claude/Codex 分叉 schema。该 wire
限制为两端都能严格校验的子集：根是 object，每层 object 关闭动态属性且全部字段必填；Pydantic 的
`discriminator/default` 注解被移除，`oneOf` 等价归一为 `anyOf`，单值 `const` 等价归一为单元素
`enum`。业务上必须保留的动态键映射不伪装成
开放 object：Stage2 的人物表/必要经历表与 Stage7 的 surname map 在线上改为显式 key/value 行数组，
验收时拒绝重复键并确定性还原成原有 dict。这个适配只存在于 agent wire；正式 artifact、内部 typed
对象与新 checkpoint 的 response payload 都不改形，恢复后仍重跑当前 schema/HARD。旧 checkpoint
字节不迁移；其旧 schema SHA 与当前 wire 不同，因此 live restore 关闭式失败、由新 run 重建。
归一后的 canonical schema 同时用于 identity、checkpoint 与具体 CLI，且在拉起任一 driver 前通过公共预检。

除上述 Global 固定联合信封的窄补丁外，其余 agent schema 固定为 `BlindPlanResponse`、
`EvidenceBindingResponse`、`ChapterResponse`、`HookResponse` 或 `NameMapResponse`；一次 feedback 后仍交
完整 replacement，不能换成局部补丁 schema。代码先物化候选，
再运行当前业务 HARD / scope / identity；反馈只带安全的结构化规则元数据，不把裁决权交回 agent。
accepted checkpoint 精确绑定当前 profile、subject、prompt/schema、requested ceiling、session/turn、
accepted stdout/typed payload、attempt links 与 ledger；跨进程恢复复核这些字段，旧 provider session 只作
历史 adoption 身份，需要继续修订时启动新 live provider session，并继承已用 turn / attempt 预算。

只有正式交付进入 journal：在同书所有权内写 `prepared`，按冻结 baseline 改写/退休 touched paths，
typed readback 后重核 current input、code、freshness 与全部 HARD，再将正式 artifact 精确 adoption 到
accepted attempt，最后写 `committed`。Stage4 typed infeasible 的 EvidenceBinding 是该节点登记的正式
primary，照常 journal/adoption 但保持 landed-red；Stage5 rejected/INVALID、Hook/Rename turn 耗尽等
没有正式 primary 的终态不得伪造 adoption 或 committed，并保留既有 blocked 与 0 成本 revalidate /
人工恢复语义。该整套 sidecar 都是内部恢复态，不进入公开 HTTP schema。

### 7.0d `operator_phases/<session_id>.jsonl` —— legacy 诊断墙钟，不是主路权威账

正常主路的权威时间线来自 RunSupervisor lifecycle events、admission claims、node beacon、
`ledger.jsonl` 与 `agent_sessions.jsonl`；这些由生产动作自动形成。Commander/Executor 不在 prompt
或正常主路里手工埋点。`operator_phases` 只保留 legacy/显式诊断兼容：缺失、未闭合或无法与权威账
联结时，完整 phase 墙钟分解必须是 unknown，不能阻断运行或洗成 0。若显式使用，每个 session 仍独占
append-only 流；人工审阅、批准与终审可以被量时，但不会因此成为 pipeline node、provider turn 或
交付闸。事件 wire 的字段证据如下：

| 字段 | 含义 | 证据 |
|---|---|---|
| `wire_version / session_id / sid / source / kind / ts_ms` | 每行公共身份、来源、事件种类与毫秒时间 | 契·码 |
| `parent_run_id / start_code_version / end_code_version` | session 起点的可选父 run 与起止代码身份；只出现在对应 session 事件 | 契·码 |
| `phase_execution_id / phase / action / run_id` | 一次 phase 的唯一执行身份、闭集阶段、短 ASCII 动作种类与可选业务 run 联结 | 契·码 |
| `state / exit_code / elapsed_ms / duration_source` | phase/session 终态、真实退出码与 monotonic/wall-clock 耗时来源 | 契·码 |
| `evidence_sha256 / error_kind` | 可选无正文证据 hash 与短 ASCII 错误类别；不得装自由理由 | 契·码 |

phase 闭集为 `preflight / inspect / preview / execute / review / approve / finalize / verify`；终态闭集为
`completed / failed / cancelled / unknown`。折叠时 start/end 必须同 phase/action/run、时间不得倒序，
failed 必须有 `error_kind`。summary 对已结束 phase 的时间区间取并集，重叠只算一次；会话总墙钟减去
该并集得到 unattributed。没有 session、未闭合 session 或未闭合 phase 时完整分解保持 unknown。
读侧只容忍最后一条**无换行**坏尾并把 session 留在未闭合态；完整换行坏行一律报错。写侧发现这种
截断尾时拒绝继续拼接，保留旧 session 证据并要求另开 session，不能把下一条 JSON 接进半行。

本 wire **刻意没有** node、model、usage、token、price、prompt、正文、reason、argv、路径或密钥。
Commander 宿主内部模型 usage 没经过仓库边界时，summary 固定给
`model_usage_state=unavailable_host_boundary`；不得按墙钟、字数或 executor 均价反推成本。

### 7.0e `_service/runs`、scratch、trash 与 retention

新 writer 的 run/checkpoint/history 唯一根为 `_service/runs/<sid>/stageN`。旧书内
`_stage*_runs` 不迁移、不追加，只能由具体 producer 的 legacy reader 在逐字校验通过后兼容读取。
operator 临时材料落 `_service/scratch/<sid>/<session>`；正常收尾只删除本 session，不能按 sid 或
scratch 根递归猜目标。

服务维护使用 `novel.cli.service_lifecycle preview|apply` 两相口。policy 的
`log_ttl_seconds / telemetry_ttl_seconds / trash_ttl_seconds / checkpoint_ttl_seconds` 默认全是
`null`，即关闭；`max_entries` 同时限制扫描量与候选量。preview 只读生成内容绑定的 plan token；apply
必须显式 `--yes`，逐 sid 取 writer ownership，并对每个目标重核路径、symlink、owner、TTL 和冻结
tree/file identity。日志只收已 ended owner，legacy telemetry 只收已 closed session；checkpoint 还须
同时证明 parent delivered、run committed、正式文件 SHA 对得上，并在删除前永久写
`_service/run_receipts/` 的无正文 receipt。ledger、admission、beacon、agent/job/session 事件及恢复
manifest/receipt 永不进入候选。

apply 先写 aggregate prepared，再为每项写自哈希 item prepared；删除成功后逐项 committed，最后
aggregate committed。中途崩溃后，同一个 plan 可凭逐项证据区分“本次已删”与“外部拿走”，幂等补尾；
缺 prepared 的目标消失仍关闭式失败。trash purge 同样先写永久 receipt，v3 trash 的 book、approval 与
该 sid service run 是一个回收边界。retention 与 restore 都有正式 preview/apply HTTP/UI；restore apply
仍只由受控 CLI 子进程移动产物。restore 在首个移动前写永久 v2 prepared receipt，逐面保存无正文
manifest、archive/live 精确路径与 SHA/tree 身份；hard-kill 后，显式重放或 trash sweep 只有在每面恰好
一侧存在且身份匹配时才幂等补尾；服务启动只作有界 receipt 扫描并拉同一受控 CLI child，不在服务
进程内移动书。双份、缺失、漂移或陌生 trash 壳条目一律关闭式留盘，且不阻断恢复 UI 启动。
`ArtifactStore` 仍把 numbered `_rounds` 当作原字节恢复面；未编辑 `.INVALID` 归档后，若其
locator-bound `_revalidation` 创建基线与 numbered 轮次逐字相同，只把重复正文压成绑定 numbered 路径
与 raw SHA-256 的小引用；恢复把该轮搬回 live 时，仅在 live SHA 精确匹配时解析为创建基线。人工编辑
导致两份内容不同则完整保留；旧 full baseline 继续由读取口兼容，不使用 symlink/hardlink，也不递归
扫描或删除历史目录。

### 7.1 ⚠ 日志按**字节**存，只在读的时候解码

原型这一段是对的，连注释一起搬：

> 中文一个字跨 3 字节，而日志是按字节切块读的：切在字中间时 `errors='replace'`
> 会把那个字变成 `�` —— 一个**不可逆**的损坏，而且下一次从 `end` 续读也补不回来。
> 所以宁可少读几个字节，留给下一次。
> **不能靠「切到最后一个换行」来代替**：日志里可能整段没有换行（实测就挂在这上面）。

### 7.2 ⚠ 子进程的三件套是环境变量，不是 argv flag（`R-12.3`）

```
PYTHONUTF8=1  ·  PYTHONIOENCODING=utf-8  ·  PYTHONUNBUFFERED=1
```

原型注释逐字：「**这三个变量是必需项**——没有它们，任何打印 `✓` 的 node 进管道就 rc=1。
用 `utf-8` 而不是 `utf-8:replace`：`:replace` 会把编不出来的字悄悄换掉，
那是『静默糟蹋输出』，比崩溃更坏（也比崩溃更难发现）。」

病因：父进程的 `locale.getpreferredencoding()` 是 `cp936`；子进程 stdout 接到**控制台**时是
UTF-8，接到**管道**时退回 cp936——而报告里满篇 `✓ ✗ ↻ ⚠`。
最要命的形态是：**先落盘（钱已经花了）、再打印 `✓`** ⇒
**一次成功的付费跑会被报成失败**；而失败分支打印 `'✗'*20`，
**报告失败这件事本身就是崩溃触发器**。

⇒ 两道都要：`build_argv` 里带 `-X utf8 -u`（人手敲那条命令时也生效），
`spawn` 时带这三个环境变量（子进程的完整保证）。
⚠ CLI 入口另外还要显式把 stdout/stderr 重配成 UTF-8——那是给
「人在 GBK 控制台里直接敲 `python -m novel.cli`」准备的第三道。

### 7.3 结构化运行心跳复用作业日志，不另造进度文件

长请求把一行 UTF-8 JSON 写到 stdout，固定前缀为 `@observe `。父进程已把 stdout/stderr
按字节追加到 `logs/<job_id>.log`，所以终端直跑与 Web 作业共享同一条出口：

- `llm_attempt_heartbeat`：发送开始即报；仍在途时默认每 30 秒报一次，含
  `attempt/max_attempts`、elapsed 与可选章进度；
- `llm_attempt_finished`：每次真实发送结束即报 `ok/error` 与错误分类；
- `chapter_progress`：Stage 2/5 每章结算后报本批 `completed/succeeded/failed`。

这些事件只回答「当前长作业有没有活着、走到哪一章」，**不参与完成度判定**。
`R-14.1` 仍只认产物存在性与 freshness；单独进度文件会与盘上真实产物分家，因此不新增。

---

## 八、⚠ 不保证原子性的地方 —— `R-14.6` 那张表

`R-14.6` 要的就是这张表本身：**进程被杀时不保证原子性的地方要列出来。**

| # | 哪儿 | 崩在中间会留下什么 | 怎么自愈 |
|---|---|---|---|
| 1 | **多文件写**：stage0 首次导入只写了 clean/report 之一；stage1 只写了 main/hook/report 的一部分 | 同一轮产物不齐 | stage0 明确拒绝不完整的首次产物对，确认它是中断留下的新产物后移走残件再重跑；stage1 用同一输入重跑会覆盖补齐三件 |
| 2 | **`_rounds/` 归档与新稿之间**（含 stage0 的 JSON/TXT 成对轮次） | 旧轮可能只归档了一份，或新轮只换了 `clean_text.txt`、报告仍指向旧指纹 | 相同字节的归档复制可幂等重放；stage0 用 `clean_sha1` 把半轮状态明确拒出。若已进入新轮两文件提交窗口，先从同轮归档恢复当前 JSON/TXT，再重跑 |
| 3 | **回答、成本行、checkpoint/prepared、正式产物与采纳账之间** | 已产生调用但回答或提交只完成一部分 | 先保留成本和原始回答；各 producer 按登记的 checkpoint/prepared 证明免费补尾，不重新计费或重签旧来源。只有完整回答、输入、身份及原 owner 停止均可证明时才恢复；缺证据保持 unknown。Stage5 维护恢复边界见 `docs/schema/stage5.md` |
| 4 | **提升路三步的中间窗口** | 归档位已占、正式稿已落、`.INVALID` 还没挪走 | **再跑一次 `--revalidate` 就自愈**。⚠ 三步的顺序是挑过的：先占归档位并检查（唯一大概率的失败）→ 落正式稿 → 挪 `.INVALID`。反过来「先归档后落稿」崩掉的话，`.INVALID` 已经从它该在的位置消失而正式稿还没写 |
| 4b | **Stage5 canonical 章与 trim audit/resolution 的多文件提交窗口** | 普通异常会由 `history_transaction` 整组回滚；若进程被强杀，可能只换了 canonical，或 sidecar 仍绑定上一份正文 | **跑 `stage5.write --recheck` 自愈**：从当前 canonical 原文与冻结预算重建 trim 状态；正文相同且仍精确绑定的人工 resolution 保留，失配决定退休 |
| 5 | **服务内存 registry 与跨进程 owner/admission 的生命周期不同** | 重启会丢内存 registry，但未闭 admission/claim 或仍活着的子进程不会因此消失 | 新写者检查持久 admission 与 owner lease；旧执行先关闭 admission，再排空 claim/execution/后代。不能用父进程退出、过时锁文件或重启推定安全，见 `novel/orchestrator/write_admission.py`、`novel/orchestrator/admission.py` |
| 5b | **Agent 批量代跑待跑列表在服务内存中**（`novel/orchestrator/agent.py::_PENDING`） | 重启后尚未启动的项不自动复活；已开始的执行按持久账和 owner 证据解释，不能假称仍由原 Supervisor 推进 | 页面按当前后端列表显示，待跑项需重新提交；已开始项按停止、恢复与原配置续跑边界处理。这与持久 `queues.jsonl` 的显式恢复协议不同 |


### 工作流职责与运行页投影

`NODES.lifecycle` 唯一登记现役节点的工作单元、生成与审阅模式、`review_actions` 动作子集、
机械 `repair_actions`、修复属主、会话、预算与恢复入口；`approval_projection` 只指向既有有效审批字段。
`GET /api/novels/:sid/run` 的 `resume_frontier.node / next_handler` 来自同一 operator 快照；
`resume_frontier.review_states[]` 按该快照审批原状态显示各 Commander 节点、范围和状态。只有布尔 gate
或 writing 授权的 Stage0/5/6/7 显示 `accepted / not_accepted`，后者不猜测缺失或未通过原因；未取得审批快照为 null。
`work_units[]` 是稳定内容账的只读投影，包含原角色、范围、已用/上限/剩余、原请求/回答摘要和恢复动作。
`current_recovery_actions` 只选当前 blocker 的登记属主、当前下一角色与范围；历史工作单元不决定下一角色。
预算或当前恢复信息尚不可读时为 null，不能用空列表表示未知。
