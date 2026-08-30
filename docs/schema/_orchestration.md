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

## 零、agent 双角色配置 —— 形状稳定，能力组合由 registry 决定

`AgentProfile` 只记录一次选择，不自己声明“哪些组合可用”。可选 driver、角色、model、
reasoning effort 与 execution mode 的唯一公开来源是 `GET /api/agent/capabilities`；组合校验由
`novel/agent_capabilities.py` 的同一份 registry 完成，前端与 argv builder 都不能各抄一张表。

| `AgentProfile` 字段 | 类型 | 必填 | 内容 | 证据 |
|---|---|---|---|---|
| `driver` | `str` | **是** | 可执行 driver id；当前取值由 registry 发布，不拿供应商名代替 | **契·码** |
| `model` | `str` | **是** | 进入 argv 与审计账的精确 model id；展示 label 是 capability 的另一格 | **契·码** |
| `reasoning_effort` | `str` | **是** | 必须属于该 driver + model 发布的 effort，不在前端维护全局联合类型 | **契·码** |
| `execution_mode` | `str \| null` | **是（可空）** | 与 effort 正交；`null` 交给 registry 使用普通执行模式，显式值仍须由该 model 发布。当前只宣称 `standard`，不宣称 `ultra` | **契·码** |

一次整书代跑用 `PipelineAgentProfiles`：

| 字段 | 类型 | 必填 | 内容 | 证据 |
|---|---|---|---|---|
| `commander` | `AgentProfile` | **是** | 全程指挥、人审代批与 skill 接入的 agent 身份 | **契·码** |
| `executor_default` | `AgentProfile` | **是** | 现役付费 agent 节点的默认执行者身份 | **契·码** |
| `executor_overrides` | `{node: AgentProfile}` | 否；默认 `{}` | 只写逐节点差异；键严格属于 `AGENT_EXECUTOR_NODES`。stage 名、代码节点与退役节点一律拒绝 | **契·码** |

`None` / `null` 只服务旧历史与迁移期尚未指定的请求，**不表示 capability 数组第一项**。
服务端 capability 的 `default_profiles=null` 表示当前还没有 provider-neutral 默认；UI 已消费该响应，
但仍必须显式选择，不能替用户猜。Phase 2～4 依次接通 Stage2 global、blueprint 与 Stage6 semantic；
Phase 5 后 direct executor 的**现役精确面**为 8 节点 / 13 种 flag-token 组合：

| node | 接受非空 profile 的 `flags` token 集 | Step 说明 |
|---|---|---|
| `stage2.global` | `[]` | 1 个付费 producer Step |
| `stage2.blueprint` | `[]` | producer 带 profile；自动 `--check-only` Step 不带 |
| `stage4.design` | `[]`、`[--replan]`、`[--repair-decision]` | 1 个付费 producer Step；后两者分别消费当前正式策略驳回或逐章返修决定，不能裸调用 |
| `stage4.plan` | `[]` | producer 带 profile；自动 `--check-only` Step 不带 |
| `stage5.write` | `[]`、`[--chapters]`、`[--revise]`、`[--revise,--chapters]` | `--chapters` 的章号是 flag 值，不另算 token；逐章 session |
| `stage6.hook` | `[]` | producer 带 profile；自动 `--check-only` Step 不带 |
| `stage6.semantic` | `[]` | A/B 两个独立 executor session |
| `stage7.rename` | `[]` | producer 带 profile；自动 `--check-only` Step 不带 |

矩阵之外的 direct executor 组合仍关闭式拒绝。八条纵切共用一条身份链：`preview/start` → `Step/JobStart` → `argv` →
`CLI` → `RunContext` → 节点自己的 executor。registry 在 argv builder 边界物化 `execution_mode`；
快照与四个 CLI 参数必须逐字一致。Stage2 global 复用一条 logical session；blueprint 按
phase/purpose/chapter 隔离；Stage5 按章隔离；Stage6 semantic 管理 A/B 两条独立 session；其余 Phase5
节点均按 node 隔离。agent 只交 fixed typed 候选，代码掌握 schema / HARD、结构化反馈、正式归一、
checkpoint、提交 journal/readback/adoption 与终验。显式 profile 不得落回 DeepSeek。

**两根身份轴（TODO-45 批次 3）**：`JobStart.run_id` 是**业务 run**——同一业务节点从
首跑、恢复到聚合始终不变，账本行、agent session 审计、`_stage*_runs/<run_id>/`
checkpoint 与 run 目录都以它为键；`job_id`（进度行里的 `execution_id`）是**本次进程
attempt**，每次启动唯一。恢复续跑（队列重启确认或原授权自动续跑）= 新 `job_id` +
`JobStart.recovered_from_job_id` 联结 + **原 `run_id`** 经 `NOVEL_RUN_ID` 传给 CLI/
ledger/session/checkpoint。旧盘记录没有这三列（可缺、不回填、不按时间猜）；新 writer
必填。旧 orphan attempt 永久保持 `reconciled/unknown`，不根据新 execution 伪造旧
`ended`。

direct job 另有一条正交授权链：`PreviewRequest.egress_authorized` 默认 false，profile 非空的新计划必须
显式冻结 bool 到 `JobStart` 与付费 executor `Step`；只有 true 才在 argv 加 `--allow-egress`。false
允许 0 成本 preview 摊开不可执行命令，真 start 与手敲 CLI 会在 job/driver/probe/wire 前拒绝；无
profile 时 false 归一为 `null`，true 反而拒绝。它不是 `NodeSpec.flags`，也不能由 `--yes` 推导。
Phase 6 已把公开 agent run 的非空 `PipelineAgentProfiles` 接进真实 provider-neutral Commander。
preview/start/batch 共用一份 plan；规范化 base、起始代码指纹、完整 resolved profiles、stdin-only
invocation 与两项授权一起冻结。`profiles=null` 只保留旧请求形状，当前无服务端默认时关闭式拒绝。
页面从 capability 动态构造 Commander、默认 executor 与逐节点 override，成功 preview 保存的完整请求
对象就是 start 唯一可消费的快照；配置、授权或 capability 变化会使它失效，不重新取默认。Commander
child 只持 run id + contract SHA-256，从 append-only started 事件复核快照；`run_step.py` 再按 exact
node/flags 选择 executor profile。Phase 1～6 结论来自 fake driver、本机零模型 CLI 检查与非 corpus
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
| `executor_profile` | `AgentProfile \| null` | 否；旧盘可缺 | 本步真正解析后的 executor 快照。现役仅限上表 8 节点 / 13 组合的付费 producer Step 可非空；blueprint、Stage4 plan、Hook、Rename 自动追跑的 0 成本 `--check-only` Step 必须为 `null`。`null` / 缺失只能显示 unknown，不按今天默认倒推昨天身份 | **契·码** |
| `egress_authorized` | `bool \| null` | 否；旧盘可缺 | 独立正文外发授权快照。新付费 executor Step 必须为 bool，并与 argv 是否含 `--allow-egress` 逐字一致；profile 为空的 legacy/代码/追跑 Step 必须为 `null`。旧盘 `null` 只表示未记录，不倒推 | **契·码** |

### 1.1 ⚠ `--base` 用绝对路径，但 `-m novel.cli` 仍然吃 cwd

argv 的形态是：

```
<sys.executable> -X utf8 -u -m novel.cli run <node> <sid> [flags…]
  [--executor-driver <id> --executor-model <id> --executor-effort <id> --executor-mode <id>]
  [--allow-egress] --base <绝对路径> [--yes]
```

方括号里的 executor 四元组只在 resolved profile 非空时由 builder 整组加入；CLI 不接受半份选择。
`--allow-egress` 也只可跟在这组 profile 后，且只在独立授权为 true 时出现；它与 `--yes` 分别回答
正文能否外发、能否付费。当前只有上表 8 节点 / 13 组合会生成这组参数，不能把 argv 的可选形状
误解为任意节点或任意 flag 都已迁移。

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
| `executor_profile` | `AgentProfile \| null` | 否；旧盘可缺 | 直接节点作业解析后的 executor 快照；与付费 producer `steps[].executor_profile` 同源。现役仅上表 8 节点 / 13 组合可非空；未迁移组合不得冻结一份 profile 后仍走旧 backend | **契·码** |
| `egress_authorized` | `bool \| null` | 否；旧盘可缺 | 与所有付费 executor Step 同源的独立正文外发授权。新 profile 作业必须冻结 bool；无 profile 时必须 `null`。false 是“可预览、不可执行”，不是 unknown；旧盘 `null` 才是未记录 | **契·码** |
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
取值面唯一落点 `novel/cli/exit_codes.py`（五个数）。

### 2.4 `AgentRunStart` —— headless agent 代跑的授权快照

agent 代跑不是 `NODES` 中的流水线节点，不能塞进 `JobStart`；它使用独立的
`Output/_service/agent_runs.jsonl` 事件流，但沿用 append-only、折叠五态与全文留档原则。
`started` 事件中的 `AgentRunStart` 记录：

> **并发模型**：锁**按书**分（`jobs.agent_lock_key`，同书不并行），
> 同时在跑的总数由 `agent.MAX_CONCURRENT`（默认 3）单独限；两者是两件事，
> 合成一个数字就会得到「想并发就得放弃同书互斥」的假选择。
> 超出闸的经 `POST /api/agent/runs/batch` 排队，由结束的那个 agent 在 worker 的
> `finally` 里带起下一个。反向互斥同步收窄成**按书**：A 书的 agent 不再挡 B 书的节点作业
> （摄入 `sid=None` 仍一律拒——它要推导目录名）。
> ⚠ SKILL.md「不并行运行两本书」管的是**一个 agent 会话**别同时管两本
> （证据、闸门裁决、终审包会串），而 N 个 headless agent 各自只看见自己那一本。
> 那句原文当初刻意没动，于是它又被读成「系统不支持多书并行」一次
> ⇒ 2026-08-19 起 SKILL.md 与 `references/runtime-profiles.md` 把**作用域**写进原文
> （「本次会话内」），语义未变；跨书并行那一侧的权威仍是 `R-13.7`。

| 字段 | 类型 | 必填 | 内容 | 证据 |
|---|---|---|---|---|
| `sid` | `str` | **是** | 被代跑的书 id | **设计** |
| `base` | `str \| null` | **是（可空）** | 新 started 事件冻结规范化绝对 base；旧事件缺格为 null，不用当前服务配置回填 | **契·码** |
| `argv` | `str[]` | **是** | 拉起所选 Commander driver 的完整解析后命令行；不含 prompt | **设计** |
| `prompt` | `str` | **是** | 发给 Commander 的全文，只经 stdin 传入；必须分别写明付费授权与正文外发授权 | **设计** |
| `authorized` | `bool` | **是** | 只表示是否允许产生外部调用费用 | **契·码** |
| `egress_authorized` | `bool` | **是** | 只表示正文及派生上下文能否外发 | **契·码** |
| `code_version` | `str \| null` | **是** | 启动时仓库代码指纹；历史事件可空 | **契·码** |
| `profiles` | `PipelineAgentProfiles \| null` | **是（可空）** | Commander + 默认 executor + 节点覆盖的解析后快照；新 run 必为非空，null 只表示旧事件未记录，不得用当前默认回填 | **契·码** |

两项授权不能相互推定。preview 可在任一项为 false 时只读展示；start/batch 在任何 probe/spawn/wire
前同时要求二者为 true。API 请求可以有 wire 默认，但核心 prompt 构造与 `started` 事件必须显式取得
两项值。排队项 `AgentPending.profiles` 是必填的 resolved 深快照；出队不重新读 capability、默认值或
原请求，避免等待期间配置漂移。

Commander 的 stdout/stderr 只形成供应商 observation，不作为业务完成事实。`role=commander,node=null`
的 session/ledger 以 attempt id 联结；actual model/effort/provider session id 缺失保持 unknown，不拿请求值
倒填。身份漂移、provider session 跨 logical session 复用或账/审计失败会把 delivery 降为 unknown，
但外层 ended 仍尽力落账。child executor 通过 started 事件的 contract SHA 复验相同 base/sid/code/
profiles/授权；无法复验时不得启动付费节点。

`ended` 事件在 `exit_code` 外另带 `outcome`（旧事件缺失时折叠成 `unknown`）。
`AgentRunRecord.state` 仍只按外层进程退出码折叠；`outcome.state` 的唯一取值面是
`AGENT_DELIVERY_OUTCOME`：`ready` / `ready_with_disclosures` / `stopped` / `failed` /
`unknown`。终验从盘面重新核 Stage2 正式 Global + 称名投影 + Blueprint current、联合 HARD 通过、
global review 的 blocking 面逐项 PASS（disclosure 非 PASS 只披露）、当前可下载成书、终审包、阻塞待办与
启动/结束代码指纹；不解析 agent 自述。两轴不许互推，尤其 `state=ok` 不推出可交付。

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
> 判据的唯一落点是 `novel/cli/exit_codes.py::landed` / `landed_but_red`。
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
| `reason` | `str` | **是** | `FRESHNESS_REASON` 九值。**用 id 不用措辞** | **契·码** |
| `ghosts` | `str[]` | **是** | 指向已不在盘上的那些边，**按名字点名**（`Q-4` 亲自点过的 bug）。空列表也要写 | **盘·码** |
| `invalid` | `bool` | **是** | 盘上有一份 `.INVALID` 在等人处理 | **盘·码** |
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

### 4.1 续跑判定（`R-14.4`）——四态，`unknown` 同样不许折进别人

`resume_verdict(overview, node)` → `(结论, 一句话)`：

| 结论 | 什么时候 |
|---|---|
| `skip` | 产物在、读得回来、不陈旧 ⇒ **已存在即跳过** |
| `run` | 产物不在盘上，或盘上是一份被闸门拦下的 `.INVALID`；后者在队列里先停为 `inbox:invalid`，由人优先尝试 0 成本 `--revalidate`，不会直接重跑付费命令 |
| `redo` | **上游在中断期间变过** ⇒ **这不是「续」是「重来」** |
| `unknown` | 判不了；Stage5 章按当前 schema/version 读不回或 freshness 未知也停在这里，不折成付费重跑 |

⚠ `redo` / `unknown` **都不自动执行**：它们是一句话，不是一个动作。前者意味着
可能重新付费，后者连正确动作都尚不能判定；队列统一落空 `steps` 的
`resume:manual-decision` 阻塞项，只负责**说出来并停下**。

> ⭐ **这一节在捡回一个被静默弄丢的能力。**
> 原型退役的 `_legacy/run_book.py:14-22` 有过真正的续跑：
> 「每步产物各自落盘、**已存在即跳过**」+「**route 漂移安全**：已存在的产物
> 若与当前 routing 不符 ⇒ 重跑」。改成 Web 编排之后**整个消失，
> 且没有任何文档说明是有意放弃**——今天原型的 `n5_write <book>` 缺省就是
> 全部输出章重新付费，唯一兜底是 `.llm_cache/`。
> `skip` 是那条「已存在即跳过」，`redo` 是那条「漂移检测」。

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
| Stage5 Review 不是 `missing/fresh`，旧结论不能驱动写作续跑 | `stage5:review-currentness` |
| 发现旧 `stage5/deep|shallow/` 布局 | `legacy_incompatible` |
| 文案未定稿 | `stage6.approve` |
| 人名表未定稿 | `stage7.approve` |
| inbox 非空（包括本层 current `.INVALID`、被驳回、连续未消） | `inbox:<kind>` |
| 续跑判为 `redo` / `unknown`，不能自动构造安全命令 | `resume:manual-decision` |
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
Output/_service/agent_sessions.jsonl commander / executor CLI 会话审计（wire v2）
Output/_service/agent_progress.jsonl agent/runner 的 CLI 步骤观察轨迹
Output/_service/operator_phases/<session_id>.jsonl Commander/operator 阶段墙钟遥测（每会话一流）
Output/_service/human_actions.jsonl 已落盘人工动作的 append-only 身份审计（不含正文）
Output/_service/human_actions_tail_recovery/<sha>.json 被 kill 截断的 human action 尾行隔离证据
Output/_service/stage5_writer_locks/<book>/book.lock Stage5 CLI writer / API closure 共用总锁
Output/_service/logs/<job_id>.log 子进程 stdout+stderr，按字节追加
Output/_service/ledger.jsonl      跨书逐请求成本事实
Output/_service/*.jsonl.lock      上面八类 JSONL 各自的追加锁载体（见下）
Output/_service/uploads/…         浏览器上传的原始小说字节
Output/_service/edits/<sid>/…     交给 CLI 的一次性 typed 编辑稿、人工决定或批处理 manifest
Output/_service/approvals/<sid>.json 人工闸门、已阅状态与 canonical 写作授权（原子替换）
```

它们**不是产物**：不进来源账、不进 `_provenance`、`ARTIFACTS` 里没有它们；
`tests/test_layering.py` 的写口与路由所有权白名单为每一类带着明确理由。

> ### 八类 JSONL 的追加口只有一个：`novel/appendlog.py`
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

`approvals/<sid>.json` 的盘上服务记录当前为 **v7**；HTTP 投影 `ApprovalDoc.v=6` 是另一条
版本轴，不能据同一个数字互推。每个已盖 gate、已阅状态、typed Stage4A 设计审阅、最终结构裁决和 writing 授权
都必须带一份 current-subject proof：当前 `stage0/clean_text.txt` 的完整 SHA-1、所裁决正式
产物的完整 SHA-256，以及决定本身的规范 JSON SHA-256。读取时必须重新走当前 schema、typed
人工决定与 freshness 校验并逐字节比对；根变化、上游变化、正式产物被换字、损坏、出现
`.INVALID` 或发现旧 Stage5 双臂布局，都会让对应决定及依赖它的下游决定在有效视图中失效。
普通产物主体是正式文件字节；`s7` 是有意的窄主体：当前
`surname_map + characters + extra_subs` 决策面与完整 typed `ApprovedNameMap` 的规范摘要。
因此 `stage7.assemble` 写回 `book_txt` 交付账不会自废 s7，而决策面、actor/reason/origin
或 subject hash 变化仍会失效；读取还必须先证明 approval 存在且 subject 与当前决策面一致。
旧 v6 只为**显式** backfill 解码；迁入 v7 时保留仍可逐项对证的 s0～s4、seen 以及
design/structure/writing typed 历史，清除 s5～s7，绝不伪造 Stage5 closure 回执。更早记录迁入时只保留
仍可逐项对证的 gate、seen、最终结构与 writing 决定；旧 v5 的 `design_history` 恒为空，绝不从既有 A/B/Plan
或付费记录倒造 Stage4A 人审。已有 current Plan v4 + current bound EvidenceBinding v3（包括可零成本
恢复的 `.INVALID` Plan）可继续做最终结构审；未来再次产 B 才要求真实 A 审阅。旧 v2 Binding
不经审批迁移或补字段，仍由 B producer 重建。更早记录没有 typed Stage4 结构裁决时，最多保留按当前盘面逐项对证的
前置 gate；不得凭旧 s4/writing 反造逐章结构决定。
普通 GET / 作业启动绝不静默迁移。旧 v1、损坏记录或
不满足上述条件的记录只能显式 reset/backfill，不能猜。队列在一把锁内读取 current 结构裁决、
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
| 身份 | `role=commander / executor`；commander 不带 node/chapter，executor 的 node 只接受 `AGENT_EXECUTOR_NODES`。新写入必须有 subject SHA-256 与已解析 `AgentProfile`，requested model/effort 必须与 profile 相等 |
| 实际执行 | requested 与 actual model/effort 分格，CLI 与 protocol 版本分格；每个新事件带启动代码版本，ended 另带结束版本，代码漂移时不能声明 delivered。缺失事实留 `null` / `unknown`，不能把 requested 冒充 actual |
| 成本 | `usage` 的 prompt、cache hit/miss、completion、reasoning、total token 均可为 `null`；`null` 是未观测，不是 0 |
| 终态 | `process_state=ok / failed / stopped / unknown` 与 `delivery_state=delivered / blocked / failed / unknown` 正交；进程退出不推出产物已交付 |
| 提交诊断 | ended 四元组只描述正式提交史：`delivery_attempts` ≥ 1 为发生过提交尝试；`same_run_recovered=true` 必须 `delivery_attempts≥2`；`first_failure_phase` 属于 `STAGE5_COMMIT_PHASES`；`first_failure_detail` 经生产侧截断脱敏（≤512、无控制字符）。四者皆可 `null`（未记录/未尝试），不得由其他状态倒推 |
| 隐私 | 只存 skill / prompt / schema 的 SHA-256，不存 prompt、响应正文、stdout 或 stderr；`first_failure_detail` 只收异常摘要，同受此边界约束 |

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
`acceptance_protocol=node-executor/v1` 以及 accepted attempt/fingerprint/actual model、accepted stdout、
normalized text、resolved profile、logical subject、session/provider session/turn、prompt/schema SHA、
exact accepted request、全部 attempt links、recovery attempts 与 `salvaged=false`。同份记录另封存当前
parsed payload 与 typed payload 及各自 SHA。accepted request 精确绑定 node/chapter/purpose、system/user/
schema SHA、requested model/effort、requested ceiling 与 protocol；成本 links 的 agent 请求不得伪造
legacy thinking 或 provider max tokens。

写入与恢复都必须把 accepted attempt 唯一联到 `_service/agent_sessions.jsonl` 的 current wire
`started + turn`，逐项核 parent run、node/chapter、profile、subject、provider session、turn index、
prompt/schema 与 requested/actual model/effort，再与 ledger 的 attempt/fingerprint/response SHA 闭合。
恢复会认领 prior attempts，但不 probe/start、不新增 wire，也不把旧 provider session 伪造成可 resume
的 live client；联合 HARD 后确需修订时另开新 session。checkpoint 首次写入不可覆盖，最终整书 compile、
联合 HARD、正式 artifact/`.INVALID`、commit journal 与 readback 终验仍只由 producer 代码决定。
该 checkpoint 会保存被接受的 stdout；上方 session audit 仍只存 hash 与计量，两者不能混读。

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
替代。checkpoint 与 journal 都是 `_stage6_runs/<run_id>/` 内部 sidecar，不是正式 artifact、缓存或
公开 API 类型。

#### 7.0c Phase5 executor checkpoint / commit journal —— 五个 producer 共用一套 acceptance

Stage4 design/plan、Stage5 write、Stage6 hook 与 Stage7 rename 不另造 acceptance 协议；都复用
`novel/llm/agent_runs.py` 的 `node-executor/v1`、append-only revision checkpoint 与
`prepared / committed` journal。各薄 adapter 只登记路径、purpose、recovery policy、session isolation、
允许触碰的正式/伴生路径和同书 foreign prepared 排斥：Stage4 分别是 `stage4-design-v1` / `stage4-plan-v1`
且 per-node；Stage5 是 `stage5-write-v1` 且 per-chapter；Hook 是 `stage6-hook-v1`、Rename 是
`stage7-rename-v1`，后二者均 per-node。

agent schema 固定为 `BlindPlanResponse`、`EvidenceBindingResponse`、`ChapterResponse`、`HookResponse`
或 `NameMapResponse`；一次 feedback 后仍交完整 replacement，不能换成局部补丁 schema。代码先物化候选，
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

### 7.0d `operator_phases/<session_id>.jsonl` —— 墙钟归属，不是模型成本账

每个 operator session 独占一条 append-only 流；追加只重放当前 session，不随历史总量线性变慢。
它与 node beacon、`ledger.jsonl`、`agent_sessions.jsonl` 正交：人工审阅、批准与终审可以被量时，但不会
因此成为 pipeline node、provider turn 或交付闸。事件 wire 的字段证据如下：

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
| 3 | **账本行与产物之间** | **钱花了、产物还没写** | ⚠ **不自愈**。这一条是三条里唯一真会丢钱的，所以账本行先写——宁可多一条「花过但没产出」的记录，也不要一次看不见的支出 |
| 4 | **提升路三步的中间窗口** | 归档位已占、正式稿已落、`.INVALID` 还没挪走 | **再跑一次 `--revalidate` 就自愈**。⚠ 三步的顺序是挑过的：先占归档位并检查（唯一大概率的失败）→ 落正式稿 → 挪 `.INVALID`。反过来「先归档后落稿」崩掉的话，`.INVALID` 已经从它该在的位置消失而正式稿还没写 |
| 4b | **Stage5 canonical 章与 trim audit/resolution 的多文件提交窗口** | 普通异常会由 `history_transaction` 整组回滚；若进程被强杀，可能只换了 canonical，或 sidecar 仍绑定上一份正文 | **跑 `stage5.write --recheck` 自愈**：从当前 canonical 原文与冻结预算重建 trim 状态；正文相同且仍精确绑定的人工 resolution 保留，失配决定退休 |
| 5 | **互斥锁是进程内内存** | 服务重启即全丢；多实例、人在终端手敲 CLI 都绕得过去 | ⚠ **不打算补。** 锁文件要有陈旧清理策略，清理策略又要有守卫，而**一把会说谎的锁比没有锁更坏**——它让人以为互斥成立。⇒ 记在这里，不假装它不存在 |
| 5b | **agent 批量代跑的待跑队列也是进程内内存**（`agent._PENDING`） | 服务重启即清空：已经起来的 agent 是独立进程、照常跑完并留下事件流，但**排在队里还没起的那几本不会自己回来**，要重新提交 | ⚠ 与第 5 条同一条理由，且落点相同：一份**会说谎的队列**（重启后还列着其实没人会起的书）比没有队列更坏。⇒ 界面上照实写「排队只在服务进程内存里，重启不保留」 |
