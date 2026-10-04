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

## 零、executor backend —— C3 仅 legacy 直连 LLM

现役唯一 wire 是 `ExecutorBackend = {kind:'legacy', profile:LegacyLlmProfile}`。
本地 Agent CLI（`kind=agent` / `AgentProfile`）已从契约删除；历史账经
`HistoricalAgentBackend` + `novel/orchestrator/retired_formats.py` 只读拒绝。
可选 provider/model/thinking/effort 的唯一公开来源是 `GET /api/agent/capabilities`
（路径保留，只回 legacy providers）；组合校验由 `novel/llm/providers.py` 与
`novel/orchestrator/executor_capabilities.py` 完成。

直接 LLM API 使用不含密钥的 `LegacyLlmProfile`。`legacy` 表示“本进程直接调用
LLM API”；provider 的模型、thinking、effort、节点适用面与 client 工厂统一登记在
`novel/llm/providers.py`。以后接入 GLM 必须新增完整 driver。

| `LegacyLlmProfile` 字段 | 类型 | 必填 | 内容 | 证据 |
|---|---|---|---|---|
| `provider` | `str` | **是** | 稳定 adapter id；当前只发布 `deepseek` | **契·码** |
| `model` | `str` | **是** | provider 发布的精确模型 id | **契·码** |
| `reasoning_effort` | `str` | **是** | 必须属于该 model + thinking mode 的能力组合 | **契·码** |
| `thinking_mode` | `str` | **是** | provider 自己定义并发布的思考协议 | **契·码** |

`ExecutorBackend` 现役只认：

| `kind` | `profile` | 执行边界 |
|---|---|---|
| `legacy` | `LegacyLlmProfile` | 经 DirectLlmExecutor / 直接 API client 执行 |

`PipelineAgentProfiles.schema_version=4`：只含 `executor_default + executor_overrides + concurrency`；
加载 v1–v3 丢弃 commander 与全部 agent 条目；覆盖键属于 `LLM_EXECUTOR_NODES`。
合法节点与 flags 唯一登记于 `novel/orchestrator/nodes.py::PROFILED_LLM_RUNS`。

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
| `executor_backend` | `ExecutorBackend \| null` | 否；旧盘可缺 | 本步真正解析后的 executor 快照。付费 producer Step 可非空；自动追跑的 0 成本 `--check-only` Step 必须为 `null`。现役唯一 wire 是 `kind=legacy` + `LegacyLlmProfile`；旧盘若仍带 `kind=agent` 由 `retired_formats` 拒绝续跑，列表只读不崩溃。新序列化不再发旧 `executor_profile` / `--executor-driver` 字段 | **契·码** |
| `request_profile` | `LegacyLlmProfile \| null` | CS-T加法契约；历史可缺 | 无密钥请求配置，与CLI JSON全等。新运行从冻结profiles派生，免费followcheck沿用同一配置而无新购权限；历史缺失为未知，不补high。与backend同时存在时必须全等 | **契·码** |
| `egress_authorized` | `bool \| null` | 否；旧盘可缺 | 独立正文外发授权快照，与 argv 是否含 `--allow-egress` 一致；免费Step必须为`null`，付费Step与backend有无解耦。旧盘`null`只表示未记录，不倒推 | **契·码** |

### 1.1 ⚠ `--base` 用绝对路径，但 `-m novel.cli` 仍然吃 cwd

argv 的形态是：

```
<sys.executable> -X utf8 -u -m novel.cli run <node> <sid> [flags…]
  [--request-profile <LegacyLlmProfile JSON>]
  [--allow-egress] --base <绝对路径> [--yes]
```

现役生成无密钥request-profile JSON，付费与免费复检共用同一配置；手工CLI仍接受稳定legacy参数组。
旧复制命令若仍带 `--executor-backend agent` 或
`--executor-driver/--executor-mode`，在 CLI/API/jobs 入口统一 `retired_executor_backend`
拒绝。`--allow-egress`只在独立授权为true时出现，与backend解耦；它与
`--yes` 分别回答正文能否外发、能否付费。

run_step旁路证据写`novel-operator-step-evidence-v5`，付费与免费步骤均投影request_profile，
与实际Step和argv绑定。旧v2／v4仅保留原字节审计，不自动补字段或覆盖为v5；该旁路证据不是调度真源。

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
状态机，不调用模型；模型角色只有 Executor（受托 Commander 已于 2026-09-15 退役；
全自动裁决后 `HUMAN_NODES` 为空，`approval:*` fail-closed）。
`started` 保存稳定 parent contract，
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
| `profiles` | `PipelineAgentProfiles` | **是** | 稳定默认 Executor + 节点 override；v1 旧 null 不回填，也不能构造 v2 contract | **契·码** |
| `argv / prompt / code_version` | 首 epoch 审计投影 / `ExecutionEpoch` | **否（不属于 parent contract）** | 新 v2 started 把首次 argv/prompt 放在不参加 parent hash 的 `start` 兼容投影；code 与实际 invocation 身份只属于 `ExecutionEpoch`。早期 v2 内嵌字段仅由 reader 保守解码，重新序列化与 contract hash 均排除。Commander 退役后新 run 的 argv/prompt 保持 `None` 并序列化省略 | **契·码** |
| `epoch_id` | `str` | **epoch 必填** | 同一 parent run 内单调打开的 execution epoch 身份 | **契·码** |
| `argv / prompt_sha256` | `str[] / SHA-256` | **epoch 必填** | 历史 Commander invocation 与 stdin prompt 身份（Commander 退役后新 epoch 保持 `None`，历史行原值只读解码）；prompt 正文不复制进 epoch | **契·码** |
| `code_version / protocol_version` | `str` | **epoch 必填** | 本 epoch 的代码与协议身份；无法证明不得打开 epoch | **契·码** |
| `identity_version / skill_sha256` | 见 `contracts/schema/orchestration.py::ExecutionEpoch` | 版本化 | 当前 identity v2 不带 skill 摘要；v1 保留原字段和 canonical hash，仅供历史复验，不用于当前执行或重签 | **契·码** |
| `cli_version` | `str \| null` | **epoch 必填（可空）** | provider CLI 未观测时为 null，不用请求值倒填 | **契·码** |
| `session_run_id / resume_provider_session_id` | `str / str \| null` | **epoch 必填** | 本 epoch 的 producer 身份与可续接 provider session；Commander 退役后 `resume_provider_session_id` 新 epoch 保持 `None`（历史行只读解码）。同 epoch exact resume 才可复用 provider context。代码 漂移或 Stage4 typed repair/replan 的 producer 边界都必须换 fresh `session_run_id`，不得续旧 provider | **契·码** |

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
免费复检并新建 provider session。（历史）旧链的 hash-bound Stage4 typed repair/replan 滚动点
（`recovery:stage4-design-action` 前 `reason=continue` 的 fresh epoch）与 Stage2 blueprint／
Stage4B plan 的 `delivery_recovery.json` 生产者举证暂停，已随旧链 prepared 恢复面退役
（R-12.4b）。stop 在后台 cancel 全进程树并 drain，闭合后才永久 ended，且不可
resume；重启后证明不了存活状态仍为 unknown，不自动洗成 paused。内部阻塞 helper
只供诊断/测试，不改变公开异步语义。

历史 Commander 的 stdout/stderr 只形成供应商 observation，不作为业务完成事实；`role=commander,node=null`
的 session/ledger 以 attempt id 联结、仅历史解码，actual model/effort/provider session id 缺失保持 unknown，
不拿请求值倒填。ledger 行的 `latency_ms` 记该轮 Commander 子进程墙钟（含其内部全部模型往返与工具调用，
不含之后的 execution drain），进程未启动为 `null`；可选 `activity` 只把同一 `role=commander` 事实标成
`stage2.global.prereview` / `stage2.global.review-or-edit` / `stage2.blueprint.review` 等墙钟分段，
不创建假 node、也不改变成本归属。身份漂移、provider session 跨 logical session 复用或账/审计失败会把 delivery 降为 unknown，
但 outer state 仍尽力落账。child executor 通过 parent contract SHA 与 current epoch identity 复验相同
base/sid/profiles/授权/code/provider context；无法复验时不得启动付费节点。

以下两段是历史 Commander reviewer 调用面的运输纪律，已随角色退役，只作为历史记录保留；
其解码符号（reviewer_legacy）已随 STR-06 关闭批删除，历史行关闭式拒读。历史 Reviewer 某轮若携 provider-native output
schema，invocation 先用 Executor 共用的严格公共子集校验，冻结为排序键、无空白的
canonical JSON 字节与 SHA-256；带 schema 的 exact resume 必须显式回带上一轮 SHA 且与本轮规范字节
完全一致。该真实 schema SHA 进入本 logical session 的 `started / attempt / turn / ended` 四类
`agent_sessions.jsonl` 事件和在线 `CommanderSessionPointer`；只有 schema-less 请求才继续用
provider observation protocol hash，不能在同一席位混用两种身份。

公共 schema 编译器只移除 `$ref` 的 `title / description` 注解；仍有验证关键词的引用在 wire 前
关闭式拒绝，不能把 `pattern / minLength` 等业务约束一起丢掉。完全同形的枚举可共享 `$defs`，
Stage4 native v3 从冻结 options 建立逐章局部引用表，保留节点、人物呈现、场景、状态、约束、
知识端点与 Global 的完整闭集；历史约束句柄同样恢复原始身份。完整 options SHA 防止映射
漂移，exact request SHA 继续绑定决定入口；只共享完全同构定义，避免重复展开相同验证条件。
预检同时检查服务支持的 schema 资源上限；压缩只改变运输，不扩大合法回答集合。

历史两家 CLI 只在运输方式上不同。Codex invocation 的 `--output-schema` 路径按 schema SHA 固定在本 epoch
scratch 直属目录，但文件在 build/preview 时不存在：driver 只于实际 runner 生命周期以独占普通文件创建、
逐字回读，结束后清理。硬杀留下的孤儿只在非 symlink、普通文件且内容与本轮冻结字节完全相同时回收；
异字节或异常类型在 runner 前拒绝。清理失败不得覆盖已经观测到的 provider result，残留由下一次同字节
调用安全回收。该临时文件不是 artifact、checkpoint、cache 或 provenance。Claude 不造文件，直接把同一
canonical 字节内联进 `--json-schema`；因为这会占用 Windows 命令行长度，完整 argv 必须在任何
runner/wire 前按 UTF-16 上限预检，超限以零 wire 配置失败返回。

#### 2.4a `commander_admissions.jsonl` —— 顶层退出与子执行之间的持久准入账

只扫 `agent_progress.jsonl` 抓不到“已经决定启动、尚未来得及写 start beacon”的窗口；顶层 agent run
若在这时退出，空扫描会过早验盘并释放书锁。每条外层 AgentRun 因此先取得 per-lifecycle OS
byte-lock owner lease，并持有到 ended/书锁收口的最外层 finally；每个 execution action 再以独立
admission id 写 `opened`，并在 v2 行中把 `lifecycle_run_id` 固化为该 outer AgentRun；headless
`run_step.py --execute` 在 evidence、beacon 与节点子进程前写 `claimed`，完整 Step 序列结束后写
`released`；typed approval、response-repair、终审与 operator telemetry 等非 node writer 也用
`write:<operation>` claim 覆盖自己的完整 currentness＋write 窗口。一次 execution turn 返回或异常后
关闭该 execution 的 claim 窗；claim 与 close 共用同一个 append transaction，所以 close 后迟到 claim
必拒，close 前成功 claim 必留在 active 集合里等待 release。双面 drain 正常闭合后追加 `drained`；
RunSupervisor 随后重读 `operator next`：仍 ready 时由确定性主路直接消费同快照 execution token；
命中 `DETERMINISTIC_RECOVERY_WAITING_ON` 的 blocker 时现场铸 exact recovery permit 并直接交
Executor；其余非 deterministic-recovery 的 blocker：`approval:*` 为编排 bug（failed）；
`human:*` / inbox / resume:manual-decision 走精确故障或披露停机话术（批次 B4）。
只有显式、hash-bound 的孤儿恢复才追加 `reconciled`。

（历史）`recovery:stage4-design-repair-aggregate-commit` 的 FREE/0-wire 确定性 producer 补尾
（及 `stage4.design --recover-repair-aggregate` action、双重 prepared 所有权关闭式 unknown 规则）
已随旧链 prepared 恢复面退役（R-12.4b）；现役孤儿恢复只保留显式、hash-bound 的 `reconciled` 追加。

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
`AGENT_RUN_EXIT_ORIGINS` 登记为 `novel_cli / commander / supervisor`（`commander` 仅历史行解码，
新 run 不再产生该来源），只允许 ended producer 明确填写；
事件和 `AgentRunRecord` 在来源为 None 时省略该新字段，保持旧账的序列化形状，`wire_version` 不变。
外部 CLI 的 raw 退出码、parent 进程状态、独立盘面交付终验是三条轴：parent 保留收到的原始数字，
不把供应商 exit 1 改写成小说 CLI 的“已落盘”。`AgentRunRecord.state` 中 0 为 `ok`，仅
`1 + exit_origin=novel_cli` 为 `landed_red`；历史 Commander、Supervisor 或旧账缺来源的 1 均为 `failed`，
不从诊断、理由或旧 note 推断来源，也不为未知来源补“已经落盘”说明。旧 note 原样保留。
`run_step.py` 透传正式 CLI 的退出码，但自身未捕异常统一为 70；显式控制异常保持原语义。
确定性交付终验的退出码归 `supervisor` 来源；终验失败不承诺有新产物落盘。
已发行恢复 policy 的 parent 摘要曾以 parent_proof_projection_v1
复现发行时的完整投影（该解码符号已随 STR-06 关闭批删除；下述校验语义作为历史记录保留），只隔离新增字段和已变更的派生 state/note；源事件、授权、epoch、outcome
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
阻断面 turn 以 exit 2 `stopped` 结束（精确恢复/披露话术，见批次 B4）后，RunSupervisor 等
`operator next` 变化：恢复后 ready 交确定性 execution-token 主路；`approval:*` 不得再投影为
正常人工推进态。所有模型节点按 `NODES.lifecycle` 的工作单元划分 E/C 身份；同一 Executor 单元
连续生成/自修，局部返修的 surface 或候选变化不重置预算。（历史）旧链 Stage4 A/B 与 Stage6 双盲 judge 曾按席位分别隔离；这些业务边界不因共用
基础设施而合并。现役语义隔离由 `NODES.lifecycle` 的工作单元身份承担。

（历史）旧链 Stage2 冷启动顺序为 Analyze E → 独立 Characters/Global 编译 → Global C
统一接受 → 逐章并发 Blueprint E → Blueprint C 逐章及跨章接受。cold-start 判定先排除历史回答、checkpoint、
prepared 与任一侧既有产物；已有来源优先恢复，后续问题只交对应 Characters 或 Global owner。
Global 必须正式有效、逐项审阅 generation-safe 且节点接受 current；`.INVALID` 与称名投影不能释放
Blueprint。联合首抽只记 Analyze 的一次真实 attempt，两份产物共享来源但独立编辑、失效与恢复，
不把完整联合回答挂为可变人物文件的失效边。Analyze E、独立 owner 修订 E、每章 Blueprint E、
Global C 与每个 Blueprint 审阅范围各有独立工作单元，
内容额度为基础 4 次加已生效的不可变人工追加，包含首次、格式和内容纠错。
不存在人物审阅席位、预审、额外 Global 终审或按序号保留的第四席；Commander 角色已于 2026-09-15 整体删除。

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

`submission_options` 同时是可执行 binding（本段描述历史 Stage2 审阅 wire，已随 2026-09-13 语义审阅与
2026-09-15 Commander 角色退役，只约束账上历史行解码）：Stage2 既有字段 patch 的行数、顺序、subject id 与字段集合
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
request 交历史 action loader（解码符号已随 STR-06 关闭批删除），不得把未变 no-op 行带回 sparse edit；assembler 不判断哪些旧 review item 在修后仍存活，
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
物理会话按当前 schema/profile 续用或重建；相同请求字节且无进展立即停。新内容调用计入该工作单元
基础额度与已生效人工追加的累计总额，不另发纠错轮次。权限失败、未知提交结果与明确 uncertain/stop 分别保留原因，
不能当普通模型格式错误重试；已保存决定的补提交不占模型额度。预算、授权、pause/stop、current blocker
与 admission 均关闭式生效，surface 自变不能重开预算或制造循环。已经 `pipeline_complete` 而
delivery 终验未闭合时只运行`novel/orchestrator/delivery.py` 的确定性终验，不追加无关模型 turn；next 自身不可读则降 unknown。

当前性能取证工具（`tools/workflow_metrics.py` 与 `tools/workflow_pairs.py`）已随 Stage2 语义
审阅退役（2026-09，工作流 v7）：其读数主体是 Stage2 Commander 工作单元与审阅盘面，v7 不再
存在该盘面。在 v7 上重建只读读数工具前，性能配对验收停在 `docs/REQUIREMENTS.md` 的 Stage4 性能未完成项
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
> | 具名 | `stage7/naming/name_map.json` | — | `artifacts[]`（一次 `is_file()`） |
> | 轮次编号（历史：仅旧链产物用；分类器仍在） | `stage5/review_{n}.json` · `stage6/semantic_{n}.json` | 盘上有几份 | **`rounds_done[节点]`** |
> | 逐章 | `stage5/body/ch{nn}.txt` | 计划里有哪些章 | `chapters_done` |
> | **书级参数化** | `stage7/final/{title}_rewriting.txt` | **这本书是谁** | `artifacts[]`（**先解析再筛**） |
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
复用同一次 `NovelOverview + queue planner` 快照；信标只回答历史命令有没有打点，
不能用来猜“现在从哪续”。书目录尚未形成时唯一允许的状态是 `unknown`。

| 字段 | 内容 |
|---|---|
| `state` | `ready / blocked / pipeline_complete / unknown`；前三项来自 operator lane，最后一项只表示尚无法形成书级快照 |
| `node / seq / label` | 当前真实续跑点，必须逐字来自 `NODES`；完成或 unknown 时全为空 |
| `resume_verdict / why / waiting_on` | planner 对当前动作的判定、证据说明与 blocker；UI 不重算 |
| `paid / writes` | exact node/flags 的登记贵贱与写盘效果；不是前端估算 |
| `next_handler` | 后端闭合投影：免费 ready=`code`、付费 ready=`executor`、登记的确定性恢复=`executor`、其余 blocker（含全部人工审批闸）=`human`、终态=`none`、未知=`unknown` |
| `verified_skipped_nodes / verified_skipped_count` | 同一 planner 快照已明确无需再次执行、且严格位于续跑点之前的有序前缀；只能来自 `NODES`，数量必须一致 |

运行页默认折叠 `verified_skipped_nodes`，但允许展开审计；这只是呈现压缩，不删除步骤和信标历史。
普通规划返回续跑点之前已验证的完整前缀；原 owner 或 prepared 提交优先恢复时，不重读可能半提交的
审批材料，前缀可为空或短于续跑点。空前缀只表示尚未验证前置节点，不能按节点位置补造跳过证明。
（历史）以下三段描述旧链的人工审阅前沿——Stage4B `request_replan` 的
`approval:stage4-structure-replan / human`、prepared repair/replan aggregate 的 0-wire 补尾
`recovery:stage4-design-repair-aggregate-commit`、以及 Stage5 顶部卡
`approval:stage5-closure / human` 与 retain batch／reviewer rewrite 出口。这些审阅闸与
prepared 恢复面已随 2026-09-15 全自动裁决与 STR-06 关闭批退役（R-12.4b）；现役续跑前沿
只表达完成度/续跑判定：登记缺失为普通付费 todo、live `.INVALID` 为收件箱阻断、未证明为
`unknown` 留人，免费复检统一走节点级 `--check-only`。

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

`R-13.5` 与 `R-0.3b` 用的是同一句话：「**明确说出在等谁**」。F8a 后无审阅 blocker、
无 `gate.*` 待办，现役取值包括：

| 情形 | `waiting_on` |
|---|---|
| stage0 机械批准尚未成立（存量 pending；现役由 remove auto seal） | 排 `stage0.remove` 等 0 成本补算；`stage0.approve` 已退役 |
| 发现旧 `stage5/deep|shallow/` 布局 | `legacy_incompatible` |
| 人名表未定稿（rename auto seal 后仍无效） | 排 `str06.stage7.naming --check-only` 等 0 成本恢复；`stage7.approve` 已退役 |
| inbox 非空（包括本层 current `.INVALID`、被驳回、连续未消） | `inbox:<kind>`（质量类可进交付披露，见批次 C） |
| 普通队列的 `redo/unknown`，或 operator 无法证明唯一 canonical REDO 命令 | `resume:manual-decision` |
| 服务重启后旧快照仍含付费项、等人重新确认 | `queue:restart-confirmation` |
| 当前命令面与创建时快照不同、必须重建队列 | `queue:plan-changed` |

（历史）旧链队列还产生过 `stage5:review-currentness`、`recovery:stage6-prepare-edit`、
`stage6.approve --auto` 等值——它们只能由旧链节点写出，生产入口已拒识；存量队列事件的
只读解码仍认这些字面量（`queue.py` 的历史 waiter 常量表）。

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
Output/_service/commander_admissions.jsonl executor node/generic writer 六态准入账（文件名沿用历史 commander 命名）
Output/_service/commander_owner_leases/<run>.lock per-parent 顶层 agent run OS owner lease 载体（文件名沿用历史命名；文件存在不代表存活）
Output/_service/commander_admission_tail_repairs/ non-LF 坏尾原字节与 authorized/applied 两相恢复账
Output/_service/agent_sessions.jsonl executor CLI 会话审计（wire v2；历史行含 commander 角色，只读解码）
Output/_service/agent_progress.jsonl agent/runner 的 CLI 步骤观察轨迹
Output/_service/agent_progress_tail_repairs/ beacon non-LF 坏尾原字节与 authorized/applied 两相恢复账
Output/_service/operator_phases/<session_id>.jsonl agent run/operator 阶段墙钟遥测（每会话一流）
Output/_service/human_actions.jsonl （历史）旧人工动作身份账；写入面已退役，存量只读
Output/_service/human_actions_tail_recovery/<sha>.json （历史）上述旧账的尾行隔离证据
Output/_service/stage5_writer_locks/<book>/book.lock （历史）旧链 Stage5 写手／closure 总锁
Output/_service/logs/<job_id>.log 子进程 stdout+stderr，按字节追加
Output/_service/ledger.jsonl      跨书逐请求成本事实
Output/_service/*.jsonl.lock      上面九类 JSONL 各自的追加锁载体（见下）
Output/_service/uploads/…         浏览器上传的原始小说字节
Output/_service/workflow.json    新 clean base 的固定 task_id 与工作流契约，不从旧 base 自动初始化
Output/_service/runs/<sid>/work_units.json 工作单元注册索引，固定 task/node/role/scope 身份
Output/_service/runs/<sid>/work_units/<unit_id>/ledger.json 每角色工作单元的内容轮次、attempts、响应/决定/提交状态与最大 4 次额度
Output/_service/runs/<sid>/work_units/<unit_id>/<sha256>.json 不可变 request、raw response/prepared receipt、typed 决定、feedback 与提交证明
Output/_service/edits/<sid>/…     （历史）旧链交给 CLI 的一次性 typed 编辑稿；写入面已随 F8b 退役
Output/_service/edits/<sid>/reviewer-action-<sha>.json （历史）旧 Commander/Reviewer 决定；typed loader 已随 STR-06 关闭批删除，关闭式拒读
Output/_service/runs/<sid>/reviewer_action_authorities/reviewer-action-<sha>.json （历史）旧 reviewer rewrite authority 归档；解码面已删除
Output/_service/runs/<sid>/reviewer_action_authorities/reviewer-action-<sha>.receipt.json （历史）上述归档的 authority commit point
Output/_service/runs/<sid>/sessions/<session_run_id>.json （历史）Commander 会话指针；schema 载体已删除，关闭式拒读（R-11.26 历史条）
Output/_service/approvals/<sid>.json 人工闸门、已阅状态与 canonical 写作授权（原子替换）
```

#### 历史工作单元决定恢复（只读）

`ReviewerActionRecord` 一族（approval/node 两形状、`reviewer_session_run_id` 等五字段证据链、
`reconcile_commander_call/event` 对账）已随 STR-06 关闭批删除：历史决定按关闭式拒读，
不再提供重建、loader 回调或跨 parent 补尾。旧 Stage5 retain/rewrite 工作单元、
reviewer rewrite FREE request producer 与 `stage5.write --reviewer-rewrite` 授权链一并退役。
历史字节的有效形状见 Git 历史；现役付费恢复入口只有候选生命周期登记的
saved_response／historical_adoption／owner_patch／committed_set_recovery／
same_context_rewrite／deterministic_replay。

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

#### 历史 Stage2 producer 提交收敛（已退役）

reviewed `stage2.global --apply-edit` 的 producer 收敛、`recovery:stage2-global-reviewed-edit-commit`
确定性补尾、`close_stage2_accepted` 的 `s0 → s1 → seen.*` 顺序补齐与
`stage2.blueprint --close-review` 零模型补提交，均已随 Stage2 语义审阅与旧链整层退役
（R-11.22：Stage2 不设审阅席位，席位账冻结拒读）。本节仅存历史形状记录。

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
（历史）旧链 Stage5 人工改稿两相账（`action=stage5_edit`，含 `formal｜working_draft`、
current canonical 完整文件 SHA、preview subject SHA、写回后 canonical 完整文件 SHA、新增 HARD
规则、目标相对路径，以及写该 phase 的 run/code/时间。同书总锁→逐章锁的线程+跨进程所有权覆盖
“重算双 SHA → prepared → canonical/trim 写入与读回 → CheckReport → committed”。普通异常
补偿产物而保留 prepared 失败证据；kill 留下的 prepared + 精确 landed SHA 由新 run 只补
trim/report/committed，不造新轮。公开读视图只认 committed，同一 request 不得 committed 为两份结果。
账不复制 segments/正文；尾行被 kill 截断时，追加口先把原字节内容寻址隔离到
`human_actions_tail_recovery/`，再截去坏尾补相；中间或已换行的完整坏行仍关闭式失败。
全部 Stage5 canonical/INVALID/recheck/revalidate/Review/wontfix/trim writer 与 closure submit 共用一把位于
`_service/` 的书锁，closure 的二次 workpack 与 approval/gate 写回在同一临界区；逐章提交再按序取章锁。
正文身份由 SHA 联结，账本不能反向充当产物或 Review。）该改稿面与 closure/approval
写回临界区已随 F8b 全自动模式退役；现存历史账只读。

（已删除）`approvals/<sid>.json` 的存量审批账**不再解码**（2026-10-04 用户裁决：
不保留旧产物读兼容）：`approvals.py` 与 `ApprovalDoc` 决定族整批删除，旧根一律
`legacy_incompatible` 拒识；文件仅作为历史字节参与 surface seal（原始字节快照）与
回收站归档清单。互斥锁迁至 `novel/orchestrator/trash.py::approval_exclusion_lock`。每个已盖 gate、已阅状态、typed Stage4A 设计审阅、最终结构裁决和 writing 授权
都必须带一份 current-subject proof：当前 `stage0/clean_text.txt` 的完整 SHA-1、所裁决正式
产物的完整 SHA-256，以及决定本身的规范 JSON SHA-256。读取时必须重新走当前 schema、typed
人工决定与 freshness 校验并逐字节比对；根变化、上游变化、正式产物被换字、损坏、出现
`.INVALID` 或发现旧 Stage5 双臂布局，都会让对应决定及依赖它的下游决定在有效视图中失效。
普通产物主体是正式文件字节；`s7` 是有意的窄主体：当前
`surname_map + characters + extra_subs` 决策面与完整 typed `ApprovedNameMap` 的规范摘要。
因此 `stage7.assemble` 写回 `book_txt` 交付账不会自废 s7，而决策面、actor/reason/origin
或 subject hash 变化仍会失效；读取还必须先证明 approval 存在且 subject 与当前决策面一致。
旧 v1～v8 审批解码与迁移分支已随各自批次删除；缺版本、未来版本和损坏均拒绝且保留原字节，
旧版本、旧 token、旧 Binding 不手工补字段冒充现役来源；reset/backfill 公开入口已退役。
普通 GET、事件观测与历史读取保持只读，损坏或证据不够保持 unknown。当前 Stage4 必须先有真实 A 审阅，
由 B producer 产出当前 Binding/Plan，再经独立 B 结构审阅；不得从既有产物或付费记录倒造审阅。
队列在一把锁内读取 current 结构裁决、
writing 授权与盘面；不得先查文件、再另读授权，从而留下两次读取之间主体变化仍生成付费命令的窗口。

⚠ 契约收窄时必须同步更新需求与 schema 文档；运行时只保留收窄后的当前规则。

### 7.0 `agent_sessions.jsonl` —— 内部 session wire，不是公开 HTTP 模型

该账记录 executor（历史行含 commander 角色，只读解码）的多轮 CLI 会话，供重放、成本对账和失败定位使用；它不属于
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
| 身份 | `role=commander / executor`（commander 仅历史行解码）；commander 不带 node/chapter，其 `parent_run_id` 指外层 AgentRun；executor 的 node 只接受 `LLM_EXECUTOR_NODES`，其 `parent_run_id` 指 checkpoint/ledger 共用的 producer run。整书归因经 AgentRun started 事件登记的 epoch `session_run_id` 集合闭合，不按时间猜。新写入必须有 subject SHA-256 与已解析 `AgentProfile`，requested model/effort 必须与 profile 相等 |
| 实际执行 | requested 与 actual model/effort 分格，CLI 与 protocol 版本分格；每个新事件带启动代码版本，ended 另带结束版本，代码漂移时不能声明 delivered。缺失事实留 `null` / `unknown`，不能把 requested 冒充 actual。checkpoint 的 adopted model 是另一格：actual 非空时须精确相等；actual 缺失时只接受 capability registry 登记的 `driver-enforced` 单模型无 fallback argv，并要求 turn 留有 CLI 版本 |
| 成本 | `usage` 的 prompt、cache hit/miss、completion、reasoning、total token 均可为 `null`；`null` 是未观测，不是 0 |
| 终态 | `process_state=ok / failed / stopped / unknown` 与 `delivery_state=delivered / blocked / failed / unknown` 正交；进程退出不推出产物已交付 |
| 提交诊断 | ended 四元组只描述正式提交史：`delivery_attempts` ≥ 1 为发生过提交尝试；`same_run_recovered=true` 必须 `delivery_attempts≥2`；`first_failure_phase` 属于 `STAGE5_COMMIT_PHASES`；`first_failure_detail` 经生产侧截断脱敏（≤512、无控制字符）。四者皆可 `null`（未记录/未尝试），不得由其他状态倒推 |
| 隐私 | 只存 prompt / schema 的 SHA-256 与历史 skill 摘要，不存 prompt、响应正文、stdout 或 stderr；`first_failure_detail` 只收异常摘要，同受此边界约束 |

旧行没有 `wire_version` 时只在读盘边界投影成 legacy wire 0；wire 1 按 legacy 接受（v2 四元组为
`null`）。缺失事实继续为 `None` / `unknown`。新写入必须用当前 wire 版本，不得生成 wire 0/1，
也不得借当前 profile 默认补全旧 session 身份。

#### 7.0a Stage2 blueprint agent checkpoint（历史，已退役）

旧链 `stage2.blueprint` 的 per-purpose-chapter checkpoint 冻结与
`stage2-blueprint-v1` 恢复策略已随旧链退役；`R-14.1` 仍只认产物存在性与
freshness 的裁决保留（单独进度文件会与盘上真实产物分家，因此不新增）。

---

## 八、⚠ 不保证原子性的地方 —— `R-14.6` 那张表

`R-14.6` 要的就是这张表本身：**进程被杀时不保证原子性的地方要列出来。**

| # | 哪儿 | 崩在中间会留下什么 | 怎么自愈 |
|---|---|---|---|
| 1 | **多文件写**：stage0 首次导入只写了 clean/report 之一；stage1 只写了 main/hook/report 的一部分 | 同一轮产物不齐 | stage0 明确拒绝不完整的首次产物对，确认它是中断留下的新产物后移走残件再重跑；stage1 用同一输入重跑会覆盖补齐三件 |
| 2 | **`_rounds/` 归档与新稿之间**（含 stage0 的 JSON/TXT 成对轮次） | 旧轮可能只归档了一份，或新轮只换了 `clean_text.txt`、报告仍指向旧指纹 | 相同字节的归档复制可幂等重放；stage0 用 `clean_sha1` 把半轮状态明确拒出。若已进入新轮两文件提交窗口，先从同轮归档恢复当前 JSON/TXT，再重跑 |
| 3 | **回答、成本行、checkpoint/prepared、正式产物与采纳账之间** | 已产生调用但回答或提交只完成一部分 | 先保留成本和原始回答；各 producer 按登记的 checkpoint/prepared 证明免费补尾，不重新计费或重签旧来源。只有完整回答、输入、身份及原 owner 停止均可证明时才恢复；缺证据保持 unknown。Stage5 维护恢复边界见 `docs/schema/stage5.md` |
| 4 | （历史）**提升路三步的中间窗口** | 归档位已占、正式稿已落、`.INVALID` 还没挪走 | 旧链 `--revalidate` 提升路已随人工修复写面退役；现役 `.INVALID` 由 owner 修订／`--check-only` 恢复（历史自愈记录：再跑一次 `--revalidate`，顺序为先占归档位→落正式稿→挪 `.INVALID`） |
| 4b | （历史）**Stage5 canonical 章与 trim audit/resolution 的多文件提交窗口** | 普通异常会由 `history_transaction` 整组回滚；若进程被强杀，可能只换了 canonical，或 sidecar 仍绑定上一份正文 | 旧链 `stage5.write --recheck` 自愈路已退役；现役正文提交是章回执链原子窗口（str06.stage5.body），恢复走 committed_set_recovery |
| 5 | **服务内存 registry 与跨进程 owner/admission 的生命周期不同** | 重启会丢内存 registry，但未闭 admission/claim 或仍活着的子进程不会因此消失 | 新写者检查持久 admission 与 owner lease；旧执行先关闭 admission，再排空 claim/execution/后代。不能用父进程退出、过时锁文件或重启推定安全，见 `novel/orchestrator/write_admission.py`、`novel/orchestrator/admission.py` |
| 5b | **Agent 批量代跑待跑列表在服务内存中**（`novel/orchestrator/agent.py::_PENDING`） | 重启后尚未启动的项不自动复活；已开始的执行按持久账和 owner 证据解释，不能假称仍由原 Supervisor 推进 | 页面按当前后端列表显示，待跑项需重新提交；已开始项按停止、恢复与原配置续跑边界处理。这与持久 `queues.jsonl` 的显式恢复协议不同 |


### 工作流职责与运行页投影

`NODES.lifecycle` 唯一登记现役节点的工作单元、生成与审阅模式、`review_actions` 动作子集、
机械 `repair_actions`、修复属主、会话、预算与恢复入口；`approval_projection` 只指向既有有效审批字段。
`GET /api/novels/:sid/run` 的 `resume_frontier.node / next_handler` 来自同一 operator 快照；
`resume_frontier.review_states[]` 按该快照审批原状态显示各审批节点、范围和状态。只有布尔 gate
或 writing 授权的 Stage0/5/6/7 显示 `accepted / not_accepted`，后者不猜测缺失或未通过原因；未取得审批快照为 null。
`work_units[]` 是稳定内容账的只读投影，包含原角色、范围、已用/上限/剩余、原请求/回答摘要和恢复动作。
`current_recovery_actions` 只选当前 blocker 的登记属主、当前下一角色与范围；历史工作单元不决定下一角色。
预算或当前恢复信息尚不可读时为 null，不能用空列表表示未知。
