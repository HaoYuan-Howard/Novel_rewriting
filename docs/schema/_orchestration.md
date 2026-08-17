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
八层产物 + `_provenance` + 三态新鲜度都在里面，标 `盘·码`。
`QueuePlan` 整个是新的（`R-13` 是本次相对原型的**新增能力**），全部标 `设计`。

## 它要消灭的那四种已发生的故障

| # | 故障 | 实测证据 |
|---|---|---|
| ① | **一个写着「0 成本」的按钮会归档旧轮、覆盖正式产物、重拼成书 txt** | 原型 8 条注释都在说同一句「**`FREE_FLAGS` 只管钱不管写**」，而**代码里没有任何东西在算「写不写」**（全仓 `would_write`/`writes_disk` 零命中） |
| ② | **同一条命令的第二个渲染落点** | 原型前端用 `argv.slice(4)` 硬编码切掉解释器前缀（`Web/static/app.js:291`）——`build_argv` 的前缀长度一变它就**静默错位** |
| ③ | **互斥键算了两遍，于是锁永远留在表里** | 原型 `start` 写进去的是 `sid or '_global'`、释放时拿 `meta['sid']` 去查，在 `sid=None` 上分家：第二本书起全部 409，直到重启服务 |
| ④ | **一个能续跑的编排器被静默弄丢了** | 退役的 `_legacy/run_book.py:14-22` 有过「已存在即跳过 + 漂移检测」，改成 Web 编排之后**整个消失，且没有任何文档说明是有意放弃**——今天 `n5_write <book>` 缺省就是全部章重新付费 |

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

### 1.1 ⚠ `--base` 用绝对路径，但 `-m novel.cli` 仍然吃 cwd

argv 的形态是：

```
<sys.executable> -X utf8 -u -m novel.cli <node> <sid> [flags…] --base <绝对路径>
```

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
> `--revalidate` 把 `.INVALID` 提升为正式产物 · `--recheck` 刷指标 + 重拼 `draft.md` ·
> `stage7.assemble` 重拼 `book.txt`）

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
| `mode` | `str \| 键不在` | 否 | 只有 `stage5.write` 有（`DEPTH_MODES`） | **设计** |
| `flags` | `str[]` | **是** | 这一次带的 flag。**空列表也要写**——「没带 flag」与「没记」不是一回事 | **设计** |
| `steps` | `Step[]` | **是** | 这个作业要依次跑哪几条命令 | **设计** |
| `python` | `str` | **是** | `sys.version`。哪版解释器跑的 | **盘·码** |
| `code` | `str` | **是** | `git describe --always --dirty`。哪版代码跑的（同 `_provenance.code`，全链一个实现） | **三源** |
| `paid` | `bool` | **是** | 整个作业花不花钱 = 任一步花钱 | **盘·码** |
| `writes` | `bool` | **是** | 整个作业写不写盘 = 任一步写盘 | **设计** |
| `started` | `int` | **是** | epoch 秒 | **盘·码** |
| `ended` | `int \| null` | **是** | 必填但可为 `null`——**`null` = 还没结束或不知道**，不是 0 | **盘·码** |
| `exit_code` | `int \| null` | **是** | 同上。⚠ **`exit_code == 0` 不等于成功**，见 §2.3 | **盘·码** |
| `state` | `str` | **是** | §三那五态 | **三源** |
| `note` | `str \| 键不在` | 否 | `reconciled` 那一条的原因 | **契·码** |

⚠ **`argv` 不在这张表上，它在 `steps[].argv` 里。**
原型的 `meta` 同时有 `argv`（第一步）与 `all_argv`（全部）——**两个字段说同一件事**，
而界面只渲染前者，于是一个两步作业在界面上看起来只跑了一步。

### 2.3 ⚠ 退出码 0 不等于成功

原型实测（F-087）：`n2_blueprint` 打印「blueprint 缺章（勿静默）」之后**没有 `sys.exit`**
——**残缺产物退出码 0**。
⇒ 凡是**写盘的付费节点**，`build_steps` 都自动追一条 `--check-only`
（见 §五），界面上必须能出现 `exit=0 · check=FAIL(3)` 这种状态。

⚠ **退出码 1 不等于崩溃**（2026-08-15 修）：`1` 只表「跑完了，判定不通过」；
未捕获异常此前穿透 `main()` 走 Python 默认的 1，与它同数——
真崩溃被编排层读成「跑完了但不合格」。现在崩溃是 **`70`（结论未知，是 bug，栈在 stderr）**，
与 `jobs.py` worker 一直在用的那个数同源。取值面唯一落点 `novel/cli/exit_codes.py`（五个数）。

### 2.4 `AgentRunStart` —— headless agent 代跑的授权快照

agent 代跑不是 `NODES` 中的流水线节点，不能塞进 `JobStart`；它使用独立的
`Output/_service/agent_runs.jsonl` 事件流，但沿用 append-only、折叠五态与全文留档原则。
`started` 事件中的 `AgentRunStart` 记录：

> **并发模型（2026-08-17 放开）**：锁**按书**分（`jobs.agent_lock_key`，同书不并行），
> 同时在跑的总数由 `agent.MAX_CONCURRENT`（默认 3）单独限；两者是两件事，
> 合成一个数字就会得到「想并发就得放弃同书互斥」的假选择。
> 超出闸的经 `POST /api/agent/runs/batch` 排队，由结束的那个 agent 在 worker 的
> `finally` 里带起下一个。反向互斥同步收窄成**按书**：A 书的 agent 不再挡 B 书的节点作业
> （摄入 `sid=None` 仍一律拒——它要推导目录名）。
> ⚠ SKILL.md「不并行运行两本书」原文不动：那条管的是**一个 agent 会话**别同时管两本
> （证据、闸门裁决、终审包会串），而 N 个 headless agent 各自只看见自己那一本。

| 字段 | 类型 | 必填 | 内容 | 证据 |
|---|---|---|---|---|
| `sid` | `str` | **是** | 被代跑的书 id | **设计** |
| `argv` | `str[]` | **是** | 拉起 headless agent 的完整解析后命令行 | **设计** |
| `prompt` | `str` | **是** | 发给 agent 的全文；必须分别写明付费授权与正文外发授权 | **设计** |
| `authorized` | `bool` | **是** | 只表示是否允许产生外部调用费用 | **契·码** |
| `egress_authorized` | `bool` | **是** | 只表示正文及派生上下文能否外发 | **契·码** |

两项授权不能相互推定。API 请求可以有产品默认，但核心 prompt 构造与 `started`
事件必须显式取得两项值。

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
> 它此前只活在 `api/routes/edits.py::_completed` 这个私有函数里，编排层根本没有
> ⇒ `jobs.fold` 自己写了一遍 `code == 0`，两个码同名。实测代价：一次 8 分钟的
> 付费批量在 `stage6.hook` exit 1 上整条停下，而 `hook.json` 好好地在盘上
> （三条候选里只有一条命中 HARD），后续三项被记成 `not_run`，账面上与一次真崩一模一样。
>
> 现状：exit 1 已升格为第五态 `landed_red`，并保留 `JobRecord.note`
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
| `reason` | `str` | **是** | `FRESHNESS_REASON` 八值。**用 id 不用措辞** | **契·码** |
| `ghosts` | `str[]` | **是** | 指向已不在盘上的那些边，**按名字点名**（`Q-4` 亲自点过的 bug）。空列表也要写 | **盘·码** |
| `invalid` | `bool` | **是** | 盘上有一份 `.INVALID` 在等人处理 | **盘·码** |
| `readable` | `bool` | **是** | 能不能按当前 schema 加载。⚠ 见下面那条 | **盘·码** |

⚠ **`readable=False` 不等于 `exists=False`。** 盘上真有一份加载不了的产物
（`成年人不做选择` 那本的 `stage4/plan_chapters.json` 根本没有 `_provenance`）。
把它折成「不存在」会让界面说「还没跑」——而它跑过，只是那次把来源账丢了。

**`StageState`** = 一层的全部 `ArtifactState` + 一个折叠出来的 `done`。
`done` 的判据是**全部必需产物 `exists` 且 `readable` 且 `freshness != 'stale'` 且 `not invalid`**。
「一层要哪几份」取的是本层各节点 `NodeSpec.produces` 的并集，**不另写一张清单**
（原型那张在 `Web/novels.py` 里，是第二份，而它分家的代价具体可见：stage5 被迫整个排除在外）。

> ### ⚠ `done` 是**三态**：`True` / `False` / **`None`＝判不了**
>
> 这一格是实跑盘上三本时被逼出来的，不是设计洁癖。
> stage5 的产物是**逐章**的（`stage5/<mode>/chNN.json`），做不了一次 `is_file()`；
> 它的判据是「stage4 计划里的每个输出章都有成稿吗」——
> **而那份计划读不回来的时候，盘上有 4 章这件事既不能证明跑完了、也不能证明没跑完。**
>
> 压成 `False` 的代价是**真金白银**：队列会去重跑一个可能已经写完的付费层。
> 压成 `True` 更坏：跳过一个根本没跑的层，下游拿着半份成品继续走。
> ⇒ 同 `FRESHNESS` / `JOB_STATE` 的 `unknown`，这是 `Q-2` 那条三态在完成度这一侧的样子。

> ### ⚠ `produces` 有**三种形状**，各有各的账，谁也不许拿另一条的答案顶替
>
> | 形状 | 例 | 完成度落点 |
> |---|---|---|
> | 具名 | `stage7/name_map.json` | `artifacts[]`（一次 `is_file()`） |
> | 轮次编号 | `stage5/review_{n}.json` · `stage6/semantic_{n}.json` | **`rounds_done[节点]`** |
> | 逐章（目录段也带占位符） | `stage5/{mode}/ch{nn}.json` | `chapters_done[臂]` |
>
> `required_artifacts()` 只收第一种，于是**后两种在 `artifacts[]` 里没有行**。
> 此前只有逐章那一种有自己的账，轮次编号那一种没有 ⇒ 续跑判定只剩 stage 级 `done`
> 可问，而 stage5 的 `done` 说的是「章稿写全了没有」。章一写全，
> `stage5.review`（0 成本全书验收）与当时的 `stage5.semantic`（付费书级语义审判，
> 已于 2026-08-17 下线）就恒被判 `skip`，理由还写着「计划里的每个输出章都有成稿了」
> ——**一句正确的话，答的是另一个问题**。
> 实测两本书跑完整条队列，盘上一份 `review_*.json` 都没有而队列把它们记成 `done`：
> 全链唯一的书级质量闸门静默缺席。`stage6.semantic` 同形。
> ⇒ `rounds_done` 就是那第三本账。判据是**存在性不是新鲜度**（轮次产物没有单份
> 新鲜度账），这条限制写在 `resume_verdict` 返回的理由里，不藏着。
> ⚠ 它与 `done` **刻意不联动**：`stage5.review` 跑没跑过不改变「这一层的成稿齐不齐」。

⚠ **`.INVALID` 不计入已完成**，它进 inbox（`INBOX_KIND` 已有 `invalid` 这个值）。
⚠ **一份合格的新正式稿落盘时，同名 `.INVALID` 由 `ArtifactStore.write` 送进 `_rounds/`**
（不是删——那份稿花过钱，还可能被别人的 `inputs` 指着）。留着它的后果不是「多一个文件」，
是四个只看兄弟文件在不在的消费方一起说假话：语料库把已出书的书报成 `broken` ·
续跑判定去**重跑并重新付费**一个已有新鲜正式稿的节点 · 人定稿盖不上审批章 ·
下游整条链被 `inbox:invalid` 挡住。
⚠ **`freshness == 'unknown'` 计入已完成**，这不是漏判：存量语料是在统一投影注册前生成的，
大量边仍为 `consumed=null`；把 `unknown` 当未完成会让升级后的队列重跑一批可能已完成的付费层。
「不知道它新不新」与「它是旧的」是两件事——后者才该挡路。

**`NovelOverview`** 另有 `chapters` 与 `stage5_modes` 两格。`chapters` 只从可读的
`stage1/chapters.json` 的主章表长度派生；stage1 未跑或读不回时为 `null`，语料库不为此
逐书追加请求，也不等 stage5 才猜章数。`stage5_modes` 取 glob `stage5/*` 的目录名：
stage5 的粒度是**章**（`R-14.2`），逐章状态由 `stage5/<mode>/ch*.json` 给，
⚠ **必须排除 `.INVALID.json`**——原型这里踩过，`chNN.json` 的存在本身就是
「过了写作时的落盘闸门」这个语义，把被拦下的那份数进去就把语义反过来了：
续跑会跳过一章**根本没写成的**稿子，而那一章的钱到时候还得花。

### 4.1 续跑判定（`R-14.4`）——四态，`unknown` 同样不许折进别人

`resume_verdict(overview, node)` → `(结论, 一句话)`：

| 结论 | 什么时候 |
|---|---|
| `skip` | 产物在、读得回来、不陈旧 ⇒ **已存在即跳过** |
| `run` | 产物不在盘上，或盘上是一份被闸门拦下的 `.INVALID`（0 成本提升路是 `--revalidate`，不必重新付费） |
| `redo` | **上游在中断期间变过**，或那一份按当前 schema 读不回来 ⇒ **这不是「续」是「重来」** |
| `unknown` | 判不了（见上面那个框） |

⚠ `redo` **不自动执行**：它是一句话，不是一个动作。重来意味着重新付费，
那是人的决定——编排层只负责**说出来并停下**。

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
| `steps` | `Step[]` | **是** | 与真跑**同一个 `build_steps`**（`R-11.8`）。仅「还没做深度选章，因而命令本身尚不可构造」的 blocked 项可为空；可运行项为空时载体拒收 |
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
| stage5 尚未持久化人工深度选章 | `approval:stage5-depth` |
| 文案未定稿 | `stage6.approve` |
| 人名表未定稿 | `stage7.approve` |
| inbox 非空（`.INVALID` / 被驳回 / 连续未消） | `inbox:<kind>` |
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

## 六、`active_key(sid, node, mode)` 与 409

`R-11.10`：**一本书同时只跑一个作业**（第二个请求 409）。唯一例外：写作的两条臂可并发。
不同 `sid` 生成不同键，因此后台作业与其 CLI 子进程可以并行，互不占用对方的锁。

互斥键的**唯一落点**就是这一个函数：

```
active_key(sid, node, mode) -> str
    sid 为 null      → '_global'（上传新书那条路；串行比并发安全——
                       两个并发的摄入可能推导出同一个目录名、互相覆盖）
    mode 非 null     → f'{sid}:{node}:{mode}'（单臂维护入口）
    否则             → sid（包括同时调度 deep/shallow 的 Stage5 整书入口）
```

注册表按层级判断冲突：书级键 `sid` 与其任一 `sid:node:mode` 冲突；deep 与 shallow
两个同级臂键彼此不冲突。通用 `start()` 拿锁、执行可选审批准备、追加 `started` 后立即
返回 `running`，后台线程与同步 `run()` 共用同一个逐步子进程执行内核。

⚠⚠ **这个函数必须只有一个落点，而这一条是拿一个真 bug 换来的。**
原型 `start` 写进去的是 `sid or '_global'`、而 `_run_chain` 释放时拿 `meta['sid']` 去查——
同一个键算了两遍，于是在 `sid=None` 上分家：**写的是 `'_global'`、删的时候查 `None`，
锁永远留在表里**。而上传新书这条路恰恰不带 sid，所以第一本上传成功、
第二本起全部 409，**直到重启服务**。

配套两条同样是踩出来的：

- **拿到锁之后到线程真跑起来之前，任何一步抛出去都要把锁还回去**——
  否则这本书被一个**从未存在过的作业**永久锁死
- **释放时用同一个函数算键，且带上 `mode`**——臂级键少了它就还错了半个键

> **为什么例外是「同一个节点的两个 mode 值」而不是「两个 kind」**：
> 写作两条臂只差 (system prompt, 给不给源章原文)，产物形状逐字节相同，判据表只有一张，
> 且 `R-7` 一节同时管两臂 ⇒ 它们是**一个节点的参数**。
> 原型给了它们两个 kind（`n5_write` / `n6_write`），于是不得不在互斥表里
> 为这两个名字开一条特例分支（`STAGE5_ARM_KINDS`）——**特例是命名错误的下游症状**。

---

## 七、`_service/` 的边界 —— `R-11.3` 收窄成「绝不落**产物**盘」

`R-11.3` 原文是「服务进程只读，绝不落盘，一切写操作走子进程」。
**2026-08-11（骨架 D）把它收窄成「绝不落产物盘」**，理由是一句话：

> **「子进程被 OOM 杀掉了」这条记录只有父进程写得出来。**
> 子进程写不了自己的死讯，而退出码正是编排层判成败的依据（`R-12.3`）。

⇒ 服务进程写、且只写这些非产物服务状态：

```
Output/_service/jobs.jsonl        append-only 事件流
Output/_service/queues.jsonl      持久批量队列 append-only 事件流
Output/_service/agent_runs.jsonl  headless agent append-only 事件流（含双授权快照）
Output/_service/logs/<job_id>.log 子进程 stdout+stderr，按字节追加
Output/_service/ledger.jsonl      跨书逐请求成本事实（旁有 ledger.jsonl.lock）
Output/_service/uploads/…         浏览器上传的原始小说字节
Output/_service/edits/<sid>/…     交给 stage0 CLI 的一次性编辑计划
Output/_service/approvals/<sid>.json 人工闸门、已阅状态与深度选章（原子替换）
```

它们**不是产物**：不进来源账、不进 `_provenance`、`ARTIFACTS` 里没有它们；
`tests/test_layering.py` 的写口与路由所有权白名单为每一类带着明确理由。

`approvals/<sid>.json` 的盘上服务记录当前为 **v2**。每个已盖 gate、已阅状态、
深浅章选择和 writing 授权都必须带一份 current-subject proof：当前
`stage0/clean_text.txt` 的完整 SHA-1、所裁决正式产物的主体 SHA-256，以及决定本身
（例如 `deep=[1,2,3]`）的规范 JSON SHA-256。读取时必须重新走当前 schema、typed
人工决定与 freshness 校验并逐字节比对；根变化、上游变化、正式产物被换字、损坏、
出现 `.INVALID` 或 Stage5 选错臂，都会让对应决定及依赖它的下游决定在有效视图中失效。
普通产物主体是正式文件字节；`s7` 是有意的窄主体：当前
`surname_map + characters + extra_subs` 决策面与完整 typed `ApprovedNameMap` 的规范摘要。
因此 `stage7.assemble` 写回 `book_txt` 交付账不会自废 s7，而决策面、actor/reason/origin
或 subject hash 变化仍会失效；读取还必须先证明 approval 存在且 subject 与当前决策面一致。
旧 v1 只有 bool，无法证明人当时看的是哪份产物，故不猜迁移；只能显式 reset 或按当前
盘面 backfill。`ApprovalDoc.v=1` 仍只是 HTTP 界面视图版本，不等于这份内部服务记录版本。
队列用一次加锁读取取得“当前可用深浅章选择”；不得先查文件存在、再另读选择，从而留下
两次读取之间产物变更仍生成付费命令的窗口。

⚠ 契约收窄时必须同步更新需求与 schema 文档；运行时只保留收窄后的当前规则。

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
| 1 | **多文件写**：stage0 首次导入只写了 clean/report 之一；stage1 只写了 main/hook/report 的一部分；或 `chNN.json` 已写、`draft.md` 还没重拼 | 同一轮产物不齐，或拼出来的全书少一章 | stage0 明确拒绝不完整的首次产物对，确认它是中断留下的新产物后移走残件再重跑；stage1 用同一输入重跑会覆盖补齐三件；`--recheck` 会重拼全书 |
| 2 | **`_rounds/` 归档与新稿之间**（含 stage0 的 JSON/TXT 成对轮次） | 旧轮可能只归档了一份，或新轮只换了 `clean_text.txt`、报告仍指向旧指纹 | 相同字节的归档复制可幂等重放；stage0 用 `clean_sha1` 把半轮状态明确拒出。若已进入新轮两文件提交窗口，先从同轮归档恢复当前 JSON/TXT，再重跑 |
| 3 | **账本行与产物之间** | **钱花了、产物还没写** | ⚠ **不自愈**。这一条是三条里唯一真会丢钱的，所以账本行先写——宁可多一条「花过但没产出」的记录，也不要一次看不见的支出 |
| 4 | **提升路三步的中间窗口** | 归档位已占、正式稿已落、`.INVALID` 还没挪走 | **再跑一次 `--revalidate` 就自愈**。⚠ 三步的顺序是挑过的：先占归档位并检查（唯一大概率的失败）→ 落正式稿 → 挪 `.INVALID`。反过来「先归档后落稿」崩掉的话，`.INVALID` 已经从它该在的位置消失而正式稿还没写 |
| 5 | **互斥锁是进程内内存**（本期新增） | 服务重启即全丢；多实例、人在终端手敲 CLI 都绕得过去 | ⚠ **不打算补。** 锁文件要有陈旧清理策略，清理策略又要有守卫，而**一把会说谎的锁比没有锁更坏**——它让人以为互斥成立。⇒ 记在这里，不假装它不存在 |
| 5b | **agent 批量代跑的待跑队列也是进程内内存**（`agent._PENDING`） | 服务重启即清空：已经起来的 agent 是独立进程、照常跑完并留下事件流，但**排在队里还没起的那几本不会自己回来**，要重新提交 | ⚠ 与第 5 条同一条理由，且落点相同：一份**会说谎的队列**（重启后还列着其实没人会起的书）比没有队列更坏。⇒ 界面上照实写「排队只在服务进程内存里，重启不保留」 |

⚠ **原型的 `R-14.6` 只剩一条腿撑着**：它「不做取消」的理由曾写着「落盘非原子，
中途 kill 会留下截断的 JSON」，而落盘**早已是原子替换**。
结论仍成立（钱已经花了），但**理由退役了而没人标注**——这张表就是那次标注。
