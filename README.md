# Novel rewriting

把一本中文网络小说的原作，经 7 个阶段处理成一本**新书**：情节骨架保留，文字全部重写，
人名全部替换，最终交付一个 `book.txt`。

**这是正式开发仓。** 快速原型在 `../Novel_refactoring/`，那边**只读**（见下）。

---

## 现在做到哪一步

顺序：**需求 → 契约（= 产物 schema）→ 骨架 → 移植**。

| 阶段 | 状态 |
|---|---|
| **需求** | ✅ `docs/REQUIREMENTS.md` —— `R-0`~`R-14` · `Q-1`~`Q-12` · `B-01`~`B-22` |
| **契约** | ✅ **十二份全部产出** —— `docs/schema/` 的 stage0~stage7 + `_common.md` + **`_report.md`**（校验报告，B3 补）+ **`_orchestration.md`**（作业/完成度/队列，D 补）+ **`_api.md`**（HTTP 响应，E 补）。stage0 / stage7 的本层待决已裁掉 4 条（2026-08-11），只剩 stage7 的 ④ `has_hook` 与 ⑤ `rn_unregistered` |
| **骨架** | ✅ **A~E 期完成**（E = HTTP 层，取值面与判定结果第一次有网络出口）。**下一步 = F（逐层移植）**，路线图与交接见下一节 |
| 移植 | 🚧 **stage3 已移植**（`PORTED_STAGES={'stage3'}`）。其余七层见 F 期 |

### 🚩 新 session 从这里接手骨架

**跑测试**：`D:\Miniconda\envs\novel_refactor\python.exe -m pytest -q`
⚠ 项目环境是 **conda env `novel_refactor`**（Python 3.11，已装 pydantic 2.13 / openai / fastapi）。
默认的 `python` 指向 Miniconda base，**那里没有 pydantic**。
⚠ 本机 `%TEMP%\pytest-of-yh255` 的 ACL 坏了，pytest 的 `tmp_path` 一用就炸，测试里用 `tempfile.mkdtemp()`。

**当前基线**：全绿，**1244 passed / 20 skip**（13 stage0 + 5 stage1 + **2 条都指着同一份产物**）。
⚠ **E 期一个 skip 都没动**（1126→1244，+118 全是新增的真测试）。
⚠ E 期新增的 `tests/test_api_http.py` 挂着 `importorskip('fastapi')`——
conda env `novel_refactor` 里 fastapi 现成，所以它**不该** skip。
**那 27 条要是变成 skip，说明 env 选错了**，而 skip 掉的东西看起来像绿的。
每一条 skip 都明说「没在查」——**改动之后先核对这个数**，它变了就说明有守卫被静默关掉或打开了。
⚠⚠ **这两个数在 D 期那次提交里写错了，E0 重量过**（`17897ca` 记的是「1066→1125」，
两个数都不对）：把 `HEAD~1` 的树 `git archive` 出去实跑，得 **889 collected − 20 skip = 869 passed**
——而 `fedaf2b` 那一版 README 第 28 行自己写的就是 869，**它是被同一次提交覆盖掉的那一行**。
⇒ D 期真实是 **869 → 1126（+257）**，不是 +59。
⚠ 量这个数有个坑：语料是按**相对路径**找 `../Novel_refactoring/` 的，
把树抽到别处（比如 `%TEMP%`）会让 `test_corpus_fixtures.py` 从 279 条塌到 6 条而**不报错**
——重量历史基线必须抽到 `E:\practice_group\` 的同级目录下。
⚠ **D 期一个 skip 都没动**（869→1126 全是新增的真测试）——编排层与 CLI 落地
不改变那 20 条的处境，但它**第一次走到**了 `tests/test_layering.py` 那个
`novel/cli/` 的 `sys.exit` 白名单分支（B1 就开好了，此前从未被执行过）。
⚠ 那 2 条是**有意的**，而且是**同一份产物**：`成年人不做选择` 那本的 `stage4/plan_chapters.json`
在新契约下**加载不了**，因为它根本没有 `_provenance`（`stage4.md` 事实⑤ 的字节级铁证）——
语料体检与落盘往返各跳过它一次。
**不许靠伪造一份来源账把它变绿**——那样这份产物就再也不会提醒任何人「提升路会丢掉来源账」。
⚠ skip 数的每一次变化都有账：B1 让**分层那 2 条从 skip 变成真测试**（22→21，`novel/` 落地
之前它们没有扫描面）；B2 加了一条 `ask_json` 调用点守卫（21→22）；
**C 期一次退掉 2 个**（22→20）——`PORTED_STAGES` 不再是空的，
于是**正向覆盖率**与那条 **`ask_json` 调用点守卫**同时从「没在查」变成真的在查。
⚠ 剩下的 18 个是 stage0/stage1 的 `importorskip`，**A~C 期一个都没动**，
它们落地那天会一次变成 18 个 `pytest.fail`（`tests/test_ingest_fixtures.py` 有意设计的机制）。

| 期 | 做什么 | 状态 |
|---|---|---|
| A0 | 三处隐形缺陷 + 命名裁决 | ✅ |
| A1 | `docs/schema/_common.md` | ✅ |
| A2 | `contracts/schema/{base,common}.py` | ✅ |
| A3 | **八层产物模型 + 盘上语料体检**（`contracts/schema/stage3.py` 等八份 · `tests/corpus.py` · `tests/test_corpus_fixtures.py`） | ✅ |
| A4 | `tests/test_layering.py` | ✅ |
| **B1** | **共享运行时**（`novel/{errors,paths,prov,store,run}.py` + `novel/llm/{cache,ledger}.py`） | ✅ |
| **B2** | **LLM 层 + `FakeTransport`**（`novel/config.py` · `novel/llm/{transport,client,prompts}.py`） | ✅ |
| **B3** | **报告构造器**（`build()` 遍历判据注册表，**不遍历 findings**）· `docs/schema/_report.md` ↔ `contracts/schema/report.py` | ✅ |
| **C** | **stage3 纵切**（第一个真正跑起来的节点）· 判据行 + 确定性核心 + 九条判据 + 盘上三本对账 | ✅ |
| **D** | **编排 + CLI**（R-11.3/11.7/11.8/11.10/11.11 · R-12.3 · R-13 · R-14）· `docs/schema/_orchestration.md` ↔ `contracts/schema/orchestration.py` | ✅ |
| **E** | **HTTP 层**：`/api/enums`（整份取值面）+ 九个端点 + `docs/schema/_api.md` ↔ `contracts/schema/api.py` · `novel/views/` · **界面的结构纪律测试**（R-11 那一节第一次有守卫） | ✅ |
| F | 逐层移植 stage2→4→5→6→7→（stage0/1 最后） | **← 下一步** |

⚠ **付费路写好了但一次都没跑。** `novel/stages/stage3/style.py::run_style()` 的
`ask_json` 调用点在，而 `build_prompt()` 是 0 成本的 `--dry-run`（展示与执行**共用同一个构造函数**，
`R-11.8`）。**真发第一次请求要先说一声**——那是本仓第一次真花钱。
⚠ D 期又给它加了一道：**CLI 上的付费跑法必须带 `--yes`**（`Q-10`）。
`build_argv` 会替付费的那条自动补上——到了拼 argv 那一步，确认已经在界面/队列那层发生过了。

### 骨架 E 已完成（2026-08-11）：**取值面与判定结果第一次有网络出口**

在 E 之前，`contracts/invariants.py` 自称是跨层取值域的唯一落点，而它**一个网络出口都没有**。
于是前端只好自己抄一份：`web/src/constants/vocabulary.ts:12-24` 硬编码了 7 个联合类型、
`web/src/constants/actions.ts:8-11` 手抄了一张 `WRITE_EFFECTS` 并在注释里写着「迟早会漂」。
**那份注释是前端自己写的诊断书。** 现在这条命令是真的：

```
python -X utf8 -u -m novel.api --base <Output>        # 只绑 127.0.0.1:8765
```

**九个端点**（`04_delivery.md` §路由表是唯一落点，本仓不抄第二份）：
`GET /api/enums` · `/api/novels` · `/api/novels/:sid` · `/api/novels/:sid/stage3/style` ·
`/api/novels/:sid/check/:kind` · `/api/jobs` · `/api/jobs/:id` · `/api/jobs/:id/log` ·
**`POST /api/jobs/preview`**（唯一的 POST，0 成本，CSRF 闸门今天全靠它验）。

#### `/api/enums` 的三个桶 —— 本期最要紧的一处设计

每个 `invariants` 的公开常量必须**恰好落进一个桶**：自动发布（容器）/ 被合成键吃掉 /
登记为不发（标量，每条写得出理由）。两种烂法的**成本相同、方向相反**：

- 手写**发布清单**会**静默地烂** —— 新加一个常量前端拿不到，于是前端自己硬编码一份，没人出声；
- 手写**不发登记表**会**响亮地烂** —— 新加一个常量落不进任何桶，`payload()` 当场抛 `ValueDomainRefused`。

实测：发布 **54** 个键，抓包那 14 个**一个不少**，其中 **12 个逐值相等**
（比原来 `test_invariants.py` 那 6 条多一倍）；只有 `job_kinds` / `actions` 的取值按已裁决变了。
`actions` 从 `NODES` 派生出 **57** 条，全部 `build_argv` 接得住、label 全部唯一，
而**抓包那 36 条的 `paid` 与 `is_paid` 36/36 一致**
——那是全仓唯一一条能抓到 `is_paid` 对现役后端回归的测试。

#### E 期发现并修掉的三处

**① `_KIND_TO_NODE` 少了 mode、`_FLAG_REDIRECT` 少了目标节点。**
`n5_write`/`n6_write` 原来都映到 `('stage5.write', ())`（丢了臂）、`--context-only` 只说
「变成了一个节点」而不说是**哪个**。拿旧表做贵贱对账**红 2 条**
（`n4_plan --context-only` 被算成 stage4.plan 的付费默认路径）。
⇒ 两张表搬进 `tests/legacy_names.py` 并加宽，两个测试文件共用一份。

**② `R-11.13` 第一次有了机器可读的落点。** 它原来标 `INFO`（要一条注册判据），
而判据表按 **stage** 编排、**界面不是任何一层的产物** ⇒ 它必然落在反向覆盖率表外。
现在 `PARTIAL_MASK_NOTE` 进了 `invariants` 随 `/api/enums` 下发，「谁在查」改成 `结构`。

**③ 「每个异常类都要有一个状态码」逼出了 LLM 那一族。**
`LlmFailed` / `TransportError` / `MissingApiKey` 都不该有状态码，理由是**结构性**的：
`R-11.3` 规定一切花钱的操作走子进程 ⇒ 它们只在子进程里抛，父进程只看到退出码。
⇒ 建了第二张表 `UNREACHABLE_OVER_HTTP`，**键是类名字符串**——写成类对象就得
`import novel.llm`，那会让服务层在结构上够得到发请求的代码。

#### ⚠ E 期结束时仍欠着的（登记，**不是**验过了）

- **`R-11.10` 只验了半格。** 「第二个请求 409」在 E 期只验到状态码映射表；
  没有 `POST /api/jobs`（用户裁决：不让浏览器能花钱）⇒ **没有第二个请求可以撞**。
  三处索引：`docs/REQUIREMENTS.md` 第六节那个框 · `docs/schema/_api.md` §七 ·
  `tests/test_api_discipline.py` 那条名字很长的测试（它同时钉着「POST 面只有 preview」，
  哪天多了一个会逼人回来补）。
- **`web/` 一个字没动**（已裁决）。欠着四笔：71 处写死的原型作业名 ·
  S-13 按 `rules[]` 重画 · 两份 check mock 重生成 · `constants/actions.ts` 那张
  `WRITE_EFFECTS` 第二落点（`/api/enums` 现在发 `writes` / `writes_disk` 了，它可以删）。
  ⚠⚠ **`docs/design_brief/05_samples/*.json` 永远不许用新后端的输出覆盖**——
  它是 `test_api_enums.py` / `test_invariants.py` 全部对账的**证据基线**，
  覆盖之后那一族测试就变成拿 `invariants` 跟它自己比。
- **`/check/stage0` 那颗地雷**：stage0 三个节点 `flags` 全空 ⇒ 一个都不接 `--check-only`。
  守卫今天就写下了且**今天是绿的**（`test_every_ported_stage_has_exactly_one_check_node`），
  它在 stage0 进 `PORTED_STAGES` 而没人给它登记跑法的那一刻变红。
- **`/api/novels` 在全语料 163 本上会慢**（每本跑一遍完成度）。**登记不优化**——
  要优化就给 `overview()` 加浅层分支，**别造第二张清单**（原型那张的代价是 stage5 被整个排除）。
- **`PARTIAL_MASK_FIELDS` 那四个字段名不立**（零消费方，F 期 stage2 落地时才生）。

⚠ **盘上原样的三本，`/check/stage3` 是 409（`ArtifactUnreadable`）不是 200**，那是对的：
那三份还带着新契约裁掉的 `source_id`/`title`。迁移之后三本全 `pass`
（9 条判据 ok，三岁那本 1 条 `micro_quote` / 2 处凭据，与原型逐条一致）。
而同一时刻 `/stage3/style` 是 **200 且 `exists=true, readable=false`，左半边照常有内容**
——那正是 `04_delivery.md:123` 要的那一态。

### 骨架 D 已完成（2026-08-11）：**第一次有东西真的把一个节点从头跑到尾**

在 D 之前，`style.py` 那四个节点函数**零调用点、零覆盖**：写好了，从没有人跑过
（`tests/test_stage3.py` 绕开它直接调 `checks.check`）。现在这条命令是真的：

```
python -X utf8 -u -m novel.cli run stage3.style <sid> --check-only --base <Output>
```

盘上三本（按 `tests/corpus.py::CUT` 现算成新契约、落进 `mkdtemp()` 沙箱）跑下来，
**与 C 期的对账逐条一致**，一分钱没花：

| 对账 | 结果 |
|---|---|
| `--check-only` | **PASS / PASS / PASS**，三岁那本 1 条命中（`micro_quote`，INFO），退出码全 0 |
| `--remeasure` | `measurable` **变化 0 项 × 3 本**；紧接着追跑的 `--check-only` 全部 PASS 且 `measurable_drift` 打 `[OK]` |

| 模块 | 它守的那件事 |
|---|---|
| `contracts/schema/orchestration.py` | `Step` / `JobEvent` / `JobRecord` / `ArtifactState` / `StageState` / `QueuePlan`。⚠ 抓包 `jobs.json` 是 **`{"jobs":[]}`**——一个样本都没有，所以形状**只能从代码来**（`_README.md` 那条纪律，`platform` 就是这么被推错的） |
| `novel/orchestrator/nodes.py` | 四个纯函数。`is_paid` 的位置**是被两处注释锁死的**（`invariants.py` 与 `run.py`），D 期兑现了那个名字 |
| `novel/orchestrator/state.py` | 完成度**只看盘上有什么**（`R-14.1`）。一条 AST 守卫盯着它的**代码里**不许够到 `_service` |
| `novel/orchestrator/queue.py` | `plan()` 纯函数；**够不到 `novel.report`**——「队列不产生新判定」由结构保证，不是由自觉 |
| `novel/orchestrator/jobs.py` | 互斥 · append-only 事件流 · 重启改判。日志**按字节存、只在读时解码** |
| `novel/cli/` | **全仓唯一能 `sys.exit` 的一层**。退出码四态一张表 |

#### ⭐ 这一期最值钱的五件事

**① `would_write` 补上了一个原型只写在注释里的维度。**
「贵贱」与「写不写盘」正交，而原型**只实现了前者**：后者一字不差地出现在
**8 条注释**里（「`FREE_FLAGS` 只管钱不管写」），代码里 0 处落点。
于是界面上一个写着「0 成本」的按钮可能归档旧轮、覆盖正式产物、重拼成书 txt，
**而用户看到的只有「0 成本」三个字**。
⇒ 现在它是 `invariants.WRITE_EFFECTS`，值是**一句话说它写什么**（空串 = 不写盘），
没登记的一律按会写算——方向与 `is_paid` 同构（`Q-10`：倒向危险的那一边）。

**② 追跑规则的判准从「花不花钱」改成「写不写盘」，是被实跑逼出来的。**
第一版写的是「付费且写盘才追一条 `--check-only`」，于是 `--remeasure` 这条
**免费但会写盘**的路一条都不追——而它那一趟**按设计不排** `measurable_drift`
（`measurable` 刚被它自己覆盖），⇒ HARD 的 `skipped` 让徽章恒为 `fail`。
**`--remeasure` 会变成一条永远拿不到绿灯的路。** 改判准之后两步链才完整：
写回那一步 exit 0（不拿 badge 当退出码），紧接着的复检去跑那道闸并给出结论。

**③ 「完成度」第一次撞上三态，而且是真数据逼的。**
stage5 的产物是**逐章**的，「该有几章」要读 stage4 的计划——而那份计划在新契约下读不回来。
⇒ 盘上有 4 章这件事**既不能证明跑完了、也不能证明没跑完**。
压成 `False` 的代价是**真金白银**（队列会去重跑一个可能已经写完的付费层），
压成 `True` 更坏。`StageState.done` 因此是 `bool | None`，`resume_verdict` 也补了第四态。

**④ 捡回一个被静默弄丢的能力。** 原型退役的 `_legacy/run_book.py:14-22` 有过真正的续跑
（「每步产物各自落盘、**已存在即跳过**」+「**route 漂移安全**」），
改成 Web 编排之后**整个消失，且没有任何文档说明是有意放弃**——
今天原型的 `n5_write <book>` 缺省就是全部输出章重新付费。
`resume_verdict` 的 `skip` 与 `redo` 就是那两条。

**⑤ `R-11.3` 收窄了一格，而且带标注。**「绝不落盘」→「绝不落**产物**盘」，
理由是一句话：**「子进程被 OOM 杀掉了」这条记录只有父进程写得出来**
（子进程写不了自己的死讯，而退出码是编排层判成败的依据）。
⚠ 收窄配了守卫：写口白名单单开一条带理由，外加一条
`test_the_service_only_writes_under_service_dir`。**只收窄而不标注，读的人会以为规则被偷偷放宽了。**

#### D 顺带定下的几处

- **`_provenance.partial_rewrite`（新键，进基类）**：`--remeasure` / `--recheck` / `--migrate`
  改完之后那份产物是**混版的**。⚠ **一个带值的键，不是三个布尔**——原型标的是
  `remeasured: true`，而那三条干的是同一件事，分成三个键 F 期必然分家（`_common.md` §5.1b）。
- **`--context-only` 挪进 `SUPERSEDED_FREE_ENTRIES`**：`NODES` 里早就有 `stage4.context`
  这个节点了，而它同时还留在 `FREE_ENTRIES` 里——**与 `--assemble` 逐字同形**的第二个落点。
- **`NodeSpec` 加了 `flags` 与 `produces` 两列**，取值**只有两个来源**（真实抓包的 36 条
  `actions` + 原型各脚本的 argparse），两处都没有的填 `()`——**空元组是诚实的**，随手编几个是抢跑。
- **付费跑法在 CLI 上要 `--yes`**，而 `build_argv` 替付费那条自动补上：
  摊开显示的那条命令**就是会跑的那条**（`R-11.8`），粘进终端不会再问一遍。
- **退出码 `1` 与 `2` 必须分开**：「跑成了，结论是不合格」与「压根没跑成」。
  合成一个数，编排层就分不出「这一层查出问题了」与「这一层没能跑」。

#### ⚠ D 发现、但**没有**在 D 修的三条（下一个 session 要先看）

**① `stage5.review.paid_flags = ('--revise',)` 与原型对不上。**
原型的 `--revise`（付费二次修订）挂在 **`n5_write`/`n6_write`** 上——它把可反哺的结论
连同上一轮成稿发回**写手**，产的是新一轮成稿，不是新一份验收报告。
两种读法都说得通，但它们**指向两段不同的代码**，而 stage5 属 F 期、今天没有任何实现可以裁定谁对。
⇒ 登记在 `contracts/invariants.py` 的 `NODES` 后面，移植 stage5 那一刻连同一份会红的样本一起裁。

**② 两个 0 成本 flag 从没被任何守卫查过。**
`n2_global --drop` 与 `n2_blueprint --replay-cache` 在原型的 `FREE_FLAGS` 里，
而**抓包的 `actions` 里没有**，所以 `test_free_entries_covers_every_free_flag_the_live_backend_uses`
从来扫不到它们。⚠ 不全的方向是**安全的**（未登记 ⇒ 按付费算），但那是「界面把免费动作
画成橙色」——`Q-3` 说的「系统在对用户说假话」。F 期移植 stage2 时补。

~~**③ `R-11.13` 落在反向覆盖率表外。**~~ **已在 E 期清掉**（2026-08-11）。
病根是它标着 `INFO`（要一条注册判据），而判据表按 **stage** 编排、
**界面不是任何一层的产物** ⇒ 它必然挂不上，而不是有人忘了挂。
⇒ 「谁在查」改成 `结构`：`contracts/invariants.py::PARTIAL_MASK_NOTE` 随 `/api/enums`
整份下发，由 `tests/test_api_discipline.py` 查；第六节那一行也补上了 `R-11.13`。
详见 `docs/REQUIREMENTS.md` 里 `R-11.13` 那个改写框。

### 骨架 C 已完成（2026-08-11）：**stage3 纵切跑通，`PORTED_STAGES` 第一次不是空的**

盘上三本对账，与原型逐条一致，**一分钱没花**：

| 对账 | 结果 |
|---|---|
| `--check-only` | **PASS / PASS / PASS** + 三岁那本 `micro_quote` **2 处**（`别说话别说话——` / `结果没过三天`） |
| `--remeasure` | `measurable` **23 项（body 14 + hook 9）× 3 本逐值相同，0 处漂移** |

**不一致就是骨架抄错了，不是数据变了**——那三份产物一个字节都没动过。
选 stage3 的理由兑现了：三份上游都在盘上 ⇒ **不实现 stage0/1/2 也能端到端跑通**。

| 模块 | 它守的那件事 |
|---|---|
| `contracts/judges/stage3.py` | 判据的**行**与它守的契约同层（`Q-12`）。九条 + 两条退役。`__init__.py` **显式列 import 不 glob**——`REGISTRY` 的内容不许取决于文件系统 |
| `novel/core/{text,measure,bands,naming}.py` | 确定性核心，**连注释一起搬**。半角 `?!` 那条、`paragraph_sep_chars` 那条、`hook_shape_band` 为什么不并进 `bands()`，每一条都带着一次实测的账 |
| `novel/stages/stage3/checks.py` | 九条判据的**实现**。`name_leak` 三态 · `measurable_drift` 只在复检时排 |
| `novel/stages/stage3/prompts.py` | `STYLE_SYS` / `HABIT_REPAIR_SYS` **与原型逐字节相同**，配一条会红的测试钉着 |

#### ⭐ 这一期最值钱的四件事

**① 判据吃 `dict` 不吃模型——而这是被新契约逼出来的一次裁决。**
A3 把 `voice` 八维、`surface_habits` 全做成了 `Field(min_length=1)` + `extra='forbid'`，
于是一份**过了模型**的产物再跑 `required` **必然全绿**——那正是判据 31 点名的
「保证会亮的绿灯」。而按本项目自己的分界线（**判据 vs 载体按失败成本划**），
`required` 该留在判据侧：载体拒收 ⇒ 整份响应作废 ⇒ **再花一次钱**；
判据拦下 ⇒ `.INVALID` + 人手改一行 + `--revalidate` ⇒ **0 成本**。
⇒ 判据跑在模型**之前**那份 dict 上，付费路的落盘闸门与 `--check-only` **同一段代码两个入口**。
⚠ 副产品：每条 HARD 现在都配得出一个会让它红的反例（模型化之后**构造不出来**）。

**② `require` 从 `model_cls` 派生，把原型漏掉的那条闸门补上了。**
原型这个调用点写的是 `require=('voice', 'surface_habits')`——**漏了 `hook_style`**。
漏传不报错，它只是**静默停掉一条 HARD 闸门**（判据 39）。
新建的 `StyleResponse`（模型只写那三个键，与 `StyleGrounding` **不是同一个东西**）
派生出三个必需顶层键，这件事**结构上不可能再发生**。

**③ `consumed` 投影全链第一次有人声明。** 在此之前八层一个都没有，新鲜度恒 `unknown`
——那是契约要的，不是 bug。stage2 那条尤其值：那份上游几千行而本层只拿它当名表，
不声明投影的话，stage2 改一个不相干的 `role` 措辞就会把 stage3 判成 `stale`
（`Q-4` 说的「把一批结论降级成『读数不可用』，且没有重新挂钩的路」）。

**④ `stage3.md` §五 那笔账还上了。** `attempts` / `revalidated` / `gates_skipped`
三个键原来**一个出口都没有**，而它们记的恰好是最该被人看见的三件事。
现在 `ACCOUNTING['stage3']` 给了它们出口，`gates_skipped` 盘上零样本 ⇒ 报告打 `[????] 未记录`。

#### C 顺带定下的几处

- **`store.read_own()`**：读**自己产的**那一份，**刻意不记来源边**——把产物自己记成
  自己的上游是假账，而且那条边永远显示对得上。原型这里是裸 `json.load`，
  连解码与校验也一起绕过去了；新方法只少 `record_edge` 那一行。
- **`RuleLine.scanned`**：绿灯必须报出自己的分母（判据 31）。
  「拿 37 个专名扫过 21 个文本字段，0 命中」与「根本没扫」，在旧形状上同形。
- **两条退役判据（`num_vs_desc` / `cliche`）进 `RetiredLine`**，带理由常驻。
  ⚠ **退役的是判据不是要求**：`STYLE_SYS` 里那句「不要用『语言流畅／节奏明快』这类
  放之四海皆准的话填格」**仍在下发**，而那张九词表没有任何现役消费方——判据 35 的教科书实例。
- **`Registry.retire()`**：一个 id 不许同时又活着又退役（报告会既列判定又列「已退役」，
  而人只会信其中一行）。

#### ⚠ C 发现、但**没有**在 C 修的两条（下一个 session 要先看）

**① `measurable_drift` 在落盘那一趟是 `skipped`，于是徽章 `fail`——这是对的，别顺手兜掉。**
落盘时 `measurable` 刚由代码算出来，与「照盘上 stage1 重算」必然相等，
所以那一趟**不排**它（一条永远不会红的检查不该打 `[OK]`）。
而报告里没排上就是 `skipped`，HARD 的 `skipped` ⇒ `fail`。
**你确实没跑那道闸，就不该拿到一个绿灯。** `--check-only` 恒会算它。

**② `hook_style` 在新契约里是必填，于是 `required` 的「无文案段」那一半分支已不可达。**
`R-1.12` 把无文案段的书挡在 stage1，`stage3.md` §三 明写「`hook_style|null` 这个类型已作废」。
本层**没有留兜底分支**（`R-4.6` 的教训：留了就是一条永不执行、也永不被验证的路）。
⚠ 但 `checks.required` 里那句「hook_style 缺失」仍在——它守的是**模型没写**这一种，
不是「这本书没有文案段」。两件事别混。

### 缺陷审计轮（2026-08-11，`cc0b729..d58d20b`）：**11 条，B1/B2 落地后的一次自审**

`H1`~`H7` + `M1`~`M4`，9 个 commit 逐条修完，测试从 729 涨到 785。
⚠ **那张编号表只活在当时那个 session 的上下文里，仓库里没有留痕**——
本节是**唯一**的索引，细节看 `git log cc0b729..d58d20b`（每条的病因与修法都写在 commit body 里）。
⚠ 交接教训：**本仓的交接一律以文档为准**，而这一轮差点只剩 commit message。

### 骨架 B3 已完成（2026-08-11）：**报告按判据编排，一条判据不可能因为「实现忘了调」而消失**

`build()` 遍历 `REGISTRY.of_stage(stage)`，**不遍历 findings**。整层就是这一句话，
两条结构性质由它推出来：

- **注册了而 outcomes 里没有 ⇒ 一行 `skipped`**（徽章至少 amber）。
  一条判据不可能因为「实现忘了调」而从报告里消失——**因为报告不是由实现的输出生成的**。
- **outcomes 里有而注册表里没有 ⇒ 抛**（镜像疾病：那条结论的 `sev` / `owner` 是谁定的？）。

它直接杀掉 stage7 的 `rn_unregistered` 病：判据表里注册着、**全仓 0 行实现**、
每本每次打绿灯，而报告由**产出**生成时这种判据会从报告里**消失**
——消失与「跑过没事」在界面上长得一模一样。

#### 一处要通知前端的契约变更：`CheckReport` 换形状

**`rules: list[RuleLine]` 为权威，四个 sev 桶不再由服务端发**（前端按 `sev` groupby）。
`badge` / `passed` / `counts` 仍只在服务端算（`R-11.7`）。

旧形状（盘上两份真实抓包）的 `oks` 是一串**没有 rule id 的散文字符串**，
于是「跑了没命中」与「压根没跑」在响应里**无法区分**；补一个 `skipped` 桶也救不了
——桶由**产出**填，而没跑的那条本来就没有产出。
⚠ 另一半账：`check_stage5_review.json` 的 `info` 桶有 **59** 条，其中 **41 条**是 `〔度量〕`
——`SEVERITY` 四级在载体上只剩三级，靠**措辞前缀**区分。
⚠ **影响 `web/` 的 S-13 屏 + 两份 mock**，按已裁决「骨架阶段当 `web/` 不存在」记到 Phase E。
⚠ **E 期的实况**（2026-08-11）：后端这一侧已落地——`GET /api/novels/:sid/check/:kind`
发的就是新形状（`rules[]` 权威，四个 sev 桶不发）。**`web/` 那一侧仍然一个字没动**
（用户裁决：E 期不动前端），所以这笔账**还欠着**，只是欠款方从后端变成了前端。
⚠⚠ **重生成那两份 mock 时别覆盖 `docs/design_brief/05_samples/`**——
那是原型真实抓包，是本仓多条对账测试的证据基线。新形状的样例要另起路径。

#### B3 顺带定下的几处

- **度量的读数与结论共用 `findings` 字段，区别在 `status` 上**：度量**永不 `hit`**。
  另开一个 `readings` 等于同一件事两个落点，而 `Finding` 已经是全链唯一那一份。
- **`actionable` 从 `repair != 'NONE'` 派生，不给 `Rule` 加字段**——两条已有的不变量
  正好同构（`rules.py::_validate` ③ 与 `Finding._postconditions` ③）。
  ⚠ **stage6/stage7 会推翻它**（那两层的 `actionable` 意思是「人能 0 成本改掉它」），
  **那是一次语义裁决不是笔误**，登记在 `docs/schema/_report.md` §2.1。
- **HARD 上的 `skipped` 进 `fail` 不进 amber**：一条阻断级判据没被执行，
  与它执行了并且命中，对调用方是同一件事——**你不能拿这份产物往下走**。
- **记账键无声明 ⇒ `AccountingKeyUnregistered`（`RefusedByStructure`）。**
  `_common.md` §九 写的是「显式终态」，那是口语义；按 `novel/errors.py` 自己的分界线
  「谁的错」，记账与声明表是**同一批代码**的两处，对不上就是 bug。细化登记在 `_report.md` §六。
- **`EdgeLine` 的指纹是 `str` 不是 `Fp10`**：报告最该跑得通的时刻恰恰是
  「这份产物已经不合当前 schema」的时候，给它受约束类型会让一条畸形旧指纹**废掉整份报告**。

### 骨架 B2 已完成（2026-08-11）：**降级阶梯第一次有了测试，而且一分钱没花**

| 模块 | 它守的那件事 |
|---|---|
| `novel/config.py` | `load_config()` **不在 import 期跑、不改 `os.environ`**（原型的模块级 `_load_dotenv` 是它被迫走子进程的五条理由之一）· **密钥永不打印**（`api_key` 的 `repr` 关掉 + 自带 `__repr__`）· 写错一个数字**当场炸不兜底** |
| `novel/llm/transport.py` | ⭐ **那条缝**：`Transport` 协议 + 真的那个 + `FakeTransport`。缝一开，阶梯的每一档都能零成本跑一遍 |
| `novel/llm/prompts.py` | `fill()` = `Template.substitute`（**缺值抛，不渲染成空串**）· `ENUM_BLOCKS` 从 invariants 生成 · `required_top_level()` |
| `novel/llm/client.py` | ⭐ **`LADDER` 全仓一份**，`ask_text` / `ask_json` 共用 · `require` 从 `model_cls` 派生 · 传输层重试与阶梯是**两条**循环 · 并发闸 |
| `novel/llm/cache.py` | `ResponseCache`：原子写 · **空 content 永不写** · **空壳 evict** · 半截文件当 miss 不当失败 |

#### ⭐ 这一期最值钱的三件事

**① 那条阶梯第一次有了测试——四档逐档，零花费。**
它是全链最值钱的逻辑之一（扛着两类实测发生过的故障），而原型**一行测试都没有**，
因为跑一次要花钱。把「发请求」抽成一条缝之后，`FakeTransport` 可以按剧本回答，
而 `t.rungs` 让「第一档就成功」与「降到第四档才成功」**第一次可区分**
——只看最终结果的话这两件事长得一模一样。
⚠ 顺带把三类故障的**边界**写清了：空正文（答案流丢了，`finish_reason='stop'`）·
JSON 截断（`finish_reason='length'`，降 effort 是**给正文腾预算**）·
合法但结构上是空的（原型 F-125，那次 74 条派生症状盖住了一句话能说清的事实）。
**三类走同一条阶梯**，而**传输层重试是另一条循环**——混起来一次网络抖动就吃掉整条阶梯。

**② `require` 从 `model_cls` 派生 = 判据 39 从纪律变成结构。**
原型把 `require=()` 设成默认值 ⇒ **漏传不报错，只是静默停掉一条 HARD 闸门**。
现在 `model_cls` 是关键字必填、`require` 默认等于它的必需顶层键，**显式传只许收窄**。
⚠ 配一条 AST 守卫盯着「有没有人把 `ask_json` 包一层绕开它」——
今天 0 个调用点所以它 skip 着（明说「没在查」），**C 期第一个调用点出现时自动变真**。

**③ 测试当场抓出一个真 bug：`MissingApiKey` 被当成网络错误重试。**
`DeepSeekTransport.send` 原来把「建客户端」放在 `try` 里，于是「你没设 key」这条**显式终态**
被 `except Exception` 抓走、经 `_classify` 变成 `TransportError`——
**重试三次、带指数退避**，而人看到的报错是 `TransportError` 不是「去填 `.env`」。
⇒ 建客户端挪到 `try` 外面。**这条是「没有 key 也要能跑完每个 0 成本 flag」那条测试逼出来的**，
不是读代码读出来的。

#### B2 顺带定下的几处

- **`ENUM_BLOCKS` 自动并进 `fill()`，而调用点传同名 kwarg 会被拒。**
  自动并之后**手抄一个子集在结构上做不到**（那正是实测漂过两次的动作：`STYLE_WORDS`
  15 个词 prompt 里只提了 2 个 · 篇幅带漏了整整一档）；拒同名是因为静默覆盖比手抄更难发现
  ——它连 diff 都看不出来。
- **占位符全账对得上**：原型全仓用过 **20** 个 `$XXX`，现在 13 个 live + 6 个待补 + 1 个已退役。
  ⚠ 待补的每一条都写得出「为什么现在给不了」（多半是它要的是 **prompt 正文**，属移植期）。
  少登记一个的后果不是少一个功能，是移植期渲染时抛 `KeyError`——**抛在正要发请求那一刻**。
- **`$` 的三类事故边界写清了**：拼错 → `KeyError` · `$99` → `ValueError`（都是 `Template` 自己抛）·
  **只有 `$$` 会安静活下来**，由 `fill()` 那条残留检查兜住。
  ⚠ 我第一版把这段写反了（说 `Template` 会静默留下 typo），是测试把它纠正过来的。
- **`openai` 只在 `_sdk()` 里懒 import**，`pyproject.toml` 把它放进 optional 的 `llm` 分组。
  全仓 0 处顶层 import 它 ⇒ 没装它照样跑得完整条 0 成本链路，`tests/test_llm.py` 一次都不碰它。
- **`.env.example` 进仓**（真 `.env` 被 `.gitignore` 盖着）。

#### ⚠ B2 没做、留给后面的

- **`--dry-run` 的 CLI 接线**在 D 期。`FakeTransport` 已经是它的实现，缺的只是命令行那一头。
- **重试与并发那三个数是拍的，不是量的**（手上没有真实 429 分布）。`Q-10` 已把
  「重试退避调参」登记为「现在不建」；调它之前先拿到真数据。
- **prompt 正文一个字都没有**，这是有意的：那 3.5 万字压着 120+ 条真机 findings 的学费，
  移植期**逐字搬不许转述**。B2 只建了把常量塞进模板的那条路。

### 骨架 B1 已完成（2026-08-11）：**共享运行时落地，`R-11.3` 的旧理由被它消灭**

`novel/` 从此存在。**它单向依赖 `contracts`，反向一条边都没有**，由 `tests/test_layering.py` 钉死
（那两条测试在 B1 之前是 skip 的——没有扫描面）。

| 模块 | 它守的那件事 |
|---|---|
| `novel/errors.py` | 显式终态**用异常不用 `sys.exit`**（`SystemExit` 是 `BaseException`，穿透 `except Exception`）。两族分界线是「谁的错」：`TerminalState` = 数据/流程说走不下去了（不是 bug）· `RefusedByStructure` = 代码自相矛盾（是 bug，栈要留全） |
| `novel/paths.py` | 书名 ↔ 目录名的**两把尺子必须互为逆**（原型 F-088：同一本书裂成两个目录）· 书名的唯一权威来源是**目录名**（F-118：书名一度变成数字串发给每一章的写手）· `.INVALID` 与 `_rounds/` 的形态 |
| `novel/prov.py` | 两把指纹尺子（10 位 / 40 位，**没有 `width=` 参数**）· `git describe --always --dirty` · **新鲜度算法**（`_common.md` §四那张表，纯函数、不碰盘）· `round = max + 1` 不是 `len + 1` |
| `novel/run.py` | ⭐ **`RunContext`：一本书 · 一个节点 · 一次跑 = 一个实例**。进程全局 `_READ` 死在这里 |
| `novel/store.py` | ⭐ **`ArtifactStore(ctx)`：唯一读写口**。原子写 + 落盘前往返校验 · 写盘口拒绝 · `.INVALID` / 提升 / 归档 / 新鲜度 |
| `novel/llm/` 的 cache 与 ledger | 缓存**两个开关永不合并** · 成本账 append-only JSONL，`calls_made` 与 `calls_cached` **两个计数器** |

#### ⭐ 这一期最值钱的三件事

**① 落盘往返把一个真 bug 逼了出来（A2 埋的，A3 的体检抓不到）。**
`Provenance.to_disk()` 原来是 `model_dump(exclude_none=True)`，而 **`exclude_none` 是全局的**：
它连着剥掉两处**必填但可为 `null`** 的键——`inputs[].consumed` 与 `usage.*_tokens`
——于是**落下去的产物读不回来**。
根因值得记：**「可为 null」的字段与「三态字段」长得一样、语义相反**
（三态的 `None` 意思是「这个键不该在」，可空的 `None` 意思是「键在，值就是不知道」），
一个全局开关同时管两种，必然把其中一种搞错。
⚠ **它今天才发作，是因为 A2/A3 只做过单向的加载体检，从来没有过一次「模型 → 盘上 → 模型」。**
现在两道守卫：落盘**之前**先证明读得回来（`novel/store.py`）+
`tests/test_store.py` 拿盘上 **92 份真实产物**跑一遍往返，八层全覆盖，0 成本。

**② 「0 成本节点不许记 `model`」那道闸，实测必须开在【铸造口】而不是写盘口。**
施工方案原写在写盘口，而那样会**误伤五条真路径**：`--revalidate` / `--remeasure` / `--recheck` /
`stage6.approve` / `stage7.approve` 改写的都是**付费节点产的**那份产物，
它记的 `model` 属于当初那次付费产出，**不是本次跑的新宣称**。
⇒ 真正该拦的是「0 成本的跑**现造**一份声称模型产了它的账」，而现造只有一个入口
（`RunContext.llm_provenance`）。**放在写盘口的闸迟早会被人加白名单绕开，那比没有更坏。**
写盘口留下的那一道是 `prompts` 非空而 `inputs` 为空 ⇒ **拒**（原型这里是 `print` 一句然后照写）。

**③ 来源账的键是【两级】的，于是成稿必须走另一条路——这条现在由类型拦。**
`stage5/deep/ch01.json` 记进 `inputs` 会塌成一个**反查不到文件**的键 ⇒ 指纹取不到
⇒ 那条边**永远显示对得上**。⇒ `store.read` 只收两级路径，三级当场 `PathRefused`
并指向 `read_judged`（存完整相对路径 + 指纹，进 `judged` 不进 `inputs`）。

#### B1 顺带定下的几处（施工方案没写死的）

- **`.INVALID` 归档叫 `_rounds/<stem>.INVALID.r<N>.json`，带轮次号。**
  原型叫 `chNN.INVALID.superseded.json`——**没有编号**，第二次被拦下的稿会**静默覆盖**第一次那份。
  归档是「出事之后唯一能回去的那一份」，把它做成全系统最容易丢的东西，方向是反的。
- **提升路三步的顺序是挑过的**：先占归档位并检查（唯一大概率的失败）→ 落正式稿 → 挪 `.INVALID`。
  剩下那个窗口崩掉**再跑一次 `--revalidate` 就自愈**；反过来「先归档后落稿」崩掉的话，
  `.INVALID` 已经从它该在的位置消失而正式稿还没写。⚠ 这一处进 `R-14.6` 那张表（D 期建表别漏）。
- **`write_text` 拿到 `\r` 一律拒收，不静默归一。** 归一是**内容决策**属产地（它要为「我改了什么」
  负责、可能还要记账）；写盘口只保证**平台不掺东西**。读的一侧同样不翻译换行——两侧口径必须一致。
- **`CachePolicy` 的第四种组合 `(read=True, write=False)` 被拒**，理由不是「它没有意义」，
  是**它在 `CACHE_POLICY` 里没有名字** ⇒ 记不进 `_provenance` ⇒ 事后说不出那一次是怎么跑的。
  要它就先往 `contracts/invariants.py` 加一个值——**那是一次契约改版，该被看见**。
- **新增 `contracts/invariants.py` 里的 `FRESHNESS_REASON`（8 个）**：一条来源边**为什么**得到那个结论。
  用 id 不用措辞（同 `STAGE0_WARNINGS`）——报告不在逐字节验收范围内，拿字符串前缀认原因会静默断掉。
- **`tests/test_layering.py` 加了写口守卫**：`novel/` 里往盘上写只许出现在
  store 与 ledger 两处（白名单带理由）。原型没有写口守卫，
  于是落盘闸门的失败支用**裸 `json.dump`** 写 `.INVALID`，盘上留下 1 份没有来源账的铁证。

#### ⚠ B1 发现、但**没有**在 B1 修的两条（下一个 session 要先看）

**① `to_human_edit()` 只长在 `LlmProvenance` 上，而 stage6/stage7 的 `--apply-edit` 够不着它。**
那两层的 `--approve` 之后再 `--apply-edit`，上一份来源账可能已经是 `FreeProvenance`。
B1 没动它，因为**本期没有消费方**——挪到基类等于写一条没被跑过的路。
⇒ **C 期或 F 期真要用它的那一刻，连同一份会红的样本一起挪。**

**② 新鲜度今天几乎恒为 `unknown`，而那是契约要的，不是 bug。**
八层现在一个 `consumed` 投影都没声明（`_common.md` §三「**建槽位，不填槽位**」），
而「没声明读了什么」**不是**「读的东西没变」⇒ 该边 `unknown`。
⚠ 别把这读成「新鲜度没做完」就顺手把它兜成 `fresh`——那正是这一格要防的事。
**C 期 stage3 纵切时给那三条上游各声明一次投影**，`fresh` 才会第一次真的出现。

### 骨架 A3 已完成（2026-08-11）：**八层模型落地，92 份真实产物过了一遍新 schema**

`contracts/schema/` 下的八份（`stage0.py` ~ `stage7.py`）与 `docs/schema/*.md`
**文件名一一对应**，文档是源。`contracts/schema/__init__.py` 里的 `ARTIFACTS`
是「产物相对路径 → 模型」的**唯一落点**（语料体检 / API 响应类型 / 将来产前端 TS 类型，三个消费方共用）。
每层带一个**自己的** `_provenance` 子类收下本层的记账键（`attempts` / `batches` /
`repairs` / `scope_violations` / `params` …）——`extra='forbid'` 会把没声明的全拒掉，
而那正是要的：**记账键必须被显式声明**。

**语料体检**（`tests/test_corpus_fixtures.py`，3 本书 × 92 份真实产物含 `_rounds/` 归档，
零成本、不落任何盘）把三件事各变成一条会红的断言：

| 表（`tests/corpus.py`） | 断言 | 规模 |
|---|---|---|
| `CUT` | 原样加载时 `extra_forbidden` **恰好等于**它 | **70** 条裁撤路径，覆盖 12 种产物 |
| `ADDED` | 裁完之后缺的必填键**恰好等于**它 | **46** 个新增键，落在 5 种产物上 |
| `RESHAPED` | 形状改版，机械转换 | `inputs` 的边 · `Finding` 5→13 · `book_txt.sha1` 10→40 |
| `MISSING_PROVENANCE` | 「没有 `_provenance` 的产物」**恰好等于**它 | 1 份（stage4 那条铁证） |

⚠ **三张表不能合成一张**：裁撤 / 新增 / 改形状**性质不同**，合并之后
「少裁了一个」与「多加了一个必填键」在测试输出上会长得一样。

**A3 顺带修的、以及量出来的**（详见各自的注释）：

- **`OWNER` 少了 `stage6` / `stage7` 两个值。** `_common.md` §七 要求全链只有一份 `Finding`，
  而 stage6/stage7 各有十几条结论的 `owner` 是自己那一层。少这两个值时
  `Literal[OWNER]` 会把它们当非法数据拒收，`contracts/rules.py::_validate` 也会在那两层移植时当场炸。
  **它今天看不见**（`PORTED_STAGES` 空、`Finding` 还没有生产者）——与 A0 那三处同形。
- **`stage4/writer_context.json` 的 `facts.address[].by` 不是 `char_id`。** 它已被逐章投影
  **解析成人名**（盘上 27 个不同值，只有 `narration`/`public` 是原样字面）。
  第一版模型与 stage2 的 `by` 共用了同一个受约束类型，**1007 条里 364 条当场被拒**——
  这条是语料体检抓出来的，不是读出来的。
- **`stage2` 的 `_provenance.setup_chapters_pruned` 盘上 3/3 本根本没有**，
  而文档把它的证据列标成 `三源`。已改 `契·码` 并登记进 `ADDED`。
- `judged[].mode` 与 `counts` **两处文档打架**（stage6 写「已裁」、stage7 自己的表写「必填」），
  已按用户裁决**三层一起裁**；`JudgedDraft` 全链只有一个类。

#### ⚠ A3 发现、但**没有**在 A3 修的一条（下一个 session 要先看它）

**`Finding.actionable` 的不变量与 stage6/stage7 打架。**
`_common.md` §七 与 `stage5.md` §2.3 都写着 `actionable` **蕴含 `owner == 'stage5'`**
（那是「进不进写作层反哺」的语义），而 `stage6.md` §四 写着本层 `actionable=True` **12 条**、
`stage7.md` §六 写着 **16 条**，owner 全是它们自己那一层——
在那两层，`actionable` 的意思是「**人能 0 成本改掉它**」（`--approve` 换一条候选 /
`--apply-edit` 改一个字），不是「写作层能修」。

⇒ `contracts/schema/common.py::Finding._postconditions` 与
`contracts/rules.py::_validate` 第 ③ 条**都会在 stage6/stage7 移植时炸**。
今天不发作（那两层的 findings 盘上没有这个字段，体检里一律填 `False`）。
**这是一次语义裁决，不是一处笔误**：要么给 `actionable` 分层定义，
要么给 stage6/stage7 另起一个字段名。**别顺手改掉那条断言**——它抓过真 bug。

#### 施工中要回来问用户的（不许替他定）

~~`CheckReport` 换形状~~ **已裁决（2026-08-11，B3）**：`rules[]` 为权威，四桶不再由服务端发 ·
~~`stage5_review` → `stage5` 的 check 路由改名~~ **已裁决（2026-08-11，E）**：改。
八个 kind 现在与 `invariants.STAGES` 逐值相等、由它派生，落点 `novel/api/deps.py::checked_kind`；
`docs/design_brief/04_delivery.md:71` 那一格已改并带标注，`web/` 里的引用还欠着 ·
`Finding.actionable` 那条语义裁决（见上，**仍待裁**；B3 已按 `repair != 'NONE'` 派生，
stage6/stage7 移植时必然撞上） · stage7 待决 ④（`book_txt.has_hook=false`
是否可达，是**产品语义**）与 ⑤（`rn_unregistered` 补实现还是退役）· 建远程仓库。

⚠ **`web/` 已进仓**（`1f61013`，92 个文件 / 12060 行；`dist/` 与 `node_modules/` 按 `.gitignore` 排除）。
用户已裁决**骨架阶段当它不存在**（不对齐、不改名），E 期又确认了一次「不动 `web/`」。
⇒ **前端对齐单独成一期**，欠着四笔（清单见上面「骨架 E 已完成」那一节末尾）：
71 处写死的原型作业名（`n4_plan` / `n9_rename` / `stage5_review` …）· S-13 按 `rules[]` 重画 ·
两份 check mock 重生成 · `constants/actions.ts` 那张 `WRITE_EFFECTS` 第二落点
（`/api/enums` 现在发 `writes` / `writes_disk` 了，它可以删）。
⚠ **本仓仍然没有远程，本机是唯一一份拷贝**——这一条没变，`Output/` 那批语料同样只有一份。

### 骨架 A1 已完成（2026-08-11）：**`_common.md` 落地，契约收口**

四条裁决写进 `docs/schema/_common.md`：

- **两把尺子故意留两把**，但做成**两个类型**：40 位 = 内容身份（人可能手工复核）·
  10 位 = 来源指纹。规则只有一条：**10 位永不与 40 位比较**，由载体层拦。
- **`inputs` 的一条边 = `{file, consumed}`**（`Q-4` 明写字段级指纹值得在新骨架就做对）。
  `consumed=null` ⇒ 该边新鲜度是 `unknown` 不是 `fresh`。**建槽位不填槽位**，八层逐步补。
- **提升路的函数签名里没有 `prompts`/`model`/`inputs`**——**没有参数就没有可以传错的东西**。
  全链三处实测伪造 + 一处实测丢失，都是因为提升分支够得到那个会造新账的函数。
- **`usage` 进每份产物**，只存 token 数与调用数、**不存钱**（`Q-10`）；
  `calls_made` 与 `calls_cached` **两个计数器不许合并**。

⚠ 另修了 `FREE_ENTRIES`：拿真实抓包对了一遍，现役后端有 **13 个** 0 成本 flag 而表里只有 9 个。
**漏登记的后果是反的**——`is_paid` 拿它当白名单、未知 flag 一律按付费算，
于是免费动作会被画成橙色并弹二次确认。补了 4 个，另 3 个登记进 `SUPERSEDED_FREE_ENTRIES`。
⚠ 其中 `--identity-plan` 是 `Q-3` 自己那条「同一个语义必须叫同一个名字」的**违例实例**。

### 骨架 A0 已完成（2026-08-10）：**三处隐形缺陷 + 命名裁决**

三处缺陷今天全都**看不见**——`PORTED_STAGES` 是空的，正向覆盖率整条 `pytest.skip`，
第一个 stage 移植进来那一刻它们会同时发作：

| # | 病 | 修法 |
|---|---|---|
| **F-A** | 正向覆盖率用 `'R-' + stage.removeprefix('stage')` 找需求节，而 **R 节的编排轴是节点不是 stage**（`R-2`/`R-3` 是 stage2 的两个节点、`R-5`/`R-6` 是 stage4 的、`R-7`/`R-8` 是 stage5 的两相）。**只有 stage0/stage1 是 1:1**，`stage3` 拿到的是 stage2 逐章抽取的需求 ⇒ 要么报一串假 missing，要么**绿着而查的是另一层** | `STAGE_SECTIONS` 从 `contracts/invariants.py` 的 `NODES` 派生；另加一条测试专盯「有人把它改回那个式子」 |
| **F-B** | `R-4.6` 的「谁在查」写着 `` `HARD`（在 `R-1.12`） ``，而解析器**只认行首的「见」** ⇒ 读成 `HARD` ⇒ 会为一条**按设计走不到本层**的需求索要一条 stage3 判据 | 改成行首形态 `` 见 `R-1.12` `` |
| **F-C** | R-4 有 9 行写着 `HARD`、stage3 也恰好有 9 条判据，而**两个集合几乎不重合**：只有 3 条判据找得到落点，6 条判据无落点、6 行需求无判据 | 新增 `R-4.11`~`R-4.14` 收下那 6 条判据；那 6 行改成真正的守卫形态（4 行 `结构` · 1 行 `载体` · 1 行转授）。**同 stage6 给 `R-9` 补 6 条那一笔** |

**命名裁决**（`REQUIREMENTS.md` 那个 ⚠ 框已换成裁决记录）：**两层**——
`stage0`~`stage7` 只做产物层、永不用作作业 id；作业 id = `<stage>.<动词>`（`stage2.global` /
`stage5.write` …），唯一落点 `contracts/invariants.py::NODES`。**`n1..n9` 整套退役**
（它是「节点轴用 stage 编号命名」的产物，所以必然跳号、重号、缺号，且 label 会说谎）。
写作两条臂是**参数**（`--mode deep|shallow`）不是两个节点；**没有 `check` 节点**，
复检一律是 `--check-only` flag，作业 kind = `(node, flags)` 二元组。

⚠ **`e1339ac` 那次 stage1 移植已被 `9f19a94` revert。** 当时跳过了「骨架」这一步，
而移植这个动作本身会把骨架一条条替人定掉（目录布局、共享层形状、错误约定…）。
**别照着那个 commit 恢复**——它验收过的东西（163 本逐字节等价那套方法）值得复用，
放代码的位置不值得。

---

## 新 session 从这里接手

> ⚠ **记忆读不到。** Claude Code 的记忆按目录键，这个仓的记忆目录是空的——
> 之前几轮的上下文全在 `../Novel_refactoring` 那一侧。**本仓的交接一律以文档为准。**

按这个顺序读，六份文档就够：

| # | 读什么 | 得到什么 |
|---|---|---|
| 1 | `docs/schema/_README.md` | **方法**：三源合并 · 证据列的四个取值 · 为什么文档只定内容、模型照它写 |
| 2 | `docs/schema/stage2.md` | **模板**（首选）。它是**调 LLM 的层**的完整样板：模型写的 vs 代码写的怎么分开读、落盘前改写怎么记账、记账键的出口规则（§3.3） |
| 3 | `docs/schema/stage1.md` | 不调 LLM 那种层的模板 |
| 4 | `docs/schema/stage0.md` | 同上，另含「盘上零产物的一层怎么写」（全部标 `设计`） |
| 5 | `docs/schema/stage4.md` | **两份产物的那种层**怎么写（一份 LLM 产、一份确定性组装），以及**收口点**长什么样 |
| 5b | `docs/schema/stage5.md` | **带返工回路的层**怎么写：两条臂 + 两相 · 47 条判据一张表 · 轮次账 / 归档 / 豁免 / 人工介入四条 0 成本路 · 一份**字节形态**产物（`draft.md`） |
| 5c | `docs/schema/stage6.md` | **全书级单次产出**怎么写（不是逐章），以及**判据表分两半的边界是「产物类」不是判据质量**；<br>另含全链最干净的一次「来源账被伪造」的**逐字节铁证** |
| 5d | `docs/schema/stage7.md` | **交付物那一层**怎么写：一份**字节形态**产物（`book.txt`，§四）· 一份**人产的输入**也是契约（`name_map_edit.json`，§三）；<br>另含**一条 HARD 判据在它自己的违规旁边打绿灯**、以及**一条判据表里注册着却全仓 0 行实现**的两个实例 |
| 6 | `docs/REQUIREMENTS.md` | 每条需求的「谁在查」列——schema 里的 `[HARD]` 都要能追到它 |

### 产下一份 schema 的步骤

1. **机械反推**：`tools/derive_schema.py`，把 `../Novel_refactoring/Output/` 下 3 本书的该层产物
   逐路径统计（类型 / 出现率 / 取值面）。⚠ **只报观察到了什么，不推断语义。**
2. **对照 `../Novel_refactoring/docs/CONTRACTS.md`**。它**不是完整 schema**——
   实测它只覆盖 stage1 `partition_report` 的 15/24 个字段。
3. **对照原型代码的产地**，补那些盘上零样本的分支。
4. **实跑那一层的 0 成本校验器**（`check_blueprints --all` / `n3_style --check-only` …）。
   这一步在 stage2 那轮才加进来，收益很大：它直接给出**每条判据在盘上的基线**，
   而「判据打了 `[OK]`」与「判据根本没有输入」在报告上长得一模一样。
   ⚠ 顺带会发现盘上产物**是不是干净的**——stage2 三本全 FAIL、stage3 三本全 PASS。
5. **零消费审计**（方法见下方「已经踩过的坑」）：字段名 grep 全部下游模块 + **逐个人工核实**。
6. 三源对不上的地方**就是发现**，不能挑一份照抄。

### 已经踩过的坑，别再踩

- **`CONTRACTS.md` 的一条 `[HARD]` 不变量在 42/163 本上不成立**（`stage1.md` §四）。
- **零消费审计有三类假阴**，照着 grep 结果删字段会造成真损失：
  整份 dict 迭代下发（键名从不字面出现）· 整份 dict 复制下发 · 模型写的决策 vs 代码解析的结果。
  实测 `voice` 八维初次 grep **全部报「0 下游」**——删了等于把写手的整个声音底座删掉。
  ⚠ 还有第四类：**工具本身用错了**（在错的目录下跑的 grep）——
  报「0 读者」之前先确认那次搜索真的搜过那个目录。
- **先问「谁在读」，再问「读它干什么」。** 只问第一问会把
  `chapter_blueprints.source_id` 留下来——而它唯一的读者是把一个内部数字 id 插进规划 prompt。

### ⚠ stage2/stage3 立下的**跨层规矩**，stage5~7 一律照办

这几条是逐条裁决过的，不是建议。产下一份 schema 时**先拿它们过一遍**。
下表右列是 **stage4 拿它们量出来的结果**（2026-08-10），可当范例：

| 规矩 | 出处 | stage4 量出来什么 → 裁决 |
|---|---|---|
| **每个结构化调用点声明【全部】必需顶层键** | 判据 39 · `Q-1` | ✅ 已声明全（`reconstruction_strategy` + `output_chapters`），不必改。四个调用点的实况：stage2 全书**漏 `arc`** · stage2 逐章 ✅ · stage3 **漏 `hook_style`** · stage4 ✅ |
| **记账键必须有出口**（进校验报告、空也要报、一行常驻非空才展开） | `Q-1` | ❌ **两个键 0 出口**（`op_forced` / `params`），且 `params` 全仓 0 读者 → `params` **补出口**（`R-5.11`）、`op_forced` **删**（见下一行） |
| **落盘前的确定性改写：校验器断言【计数】，不许断言字段值** | 判据 24 | ⚠ **这一条在 stage4 上【不适用】，而我们差点套上去**：`op` 是代码**初始化**的（模型根本不产它），那个位置本来是空的 ⇒ 没有原值被销毁 ⇒ 不必记账。<br>**判准：先问「那个位置本来有东西吗」。** 有＝改写（记账 + 断言计数），没有＝初始化（不记账）。<br>模型越界多产一个字段，归**响应侧 `extra='forbid'`** 挡 |
| **判据 vs 载体的分界线**〔本轮新立〕 | `Q-6` · `Q-3` | **按失败成本划，不是按「类型表达得了吗」划**：载体拒收 ⇒ 整份响应作废 ⇒ **再花一次钱**；判据拦下 ⇒ `.INVALID` + 人手改一行 + `--revalidate` ⇒ **0 成本**。<br>⇒ 模型写的、**人手改一个数就能救**的（`budget_int` / `style_words`）留判据；跨产物不变量（`beat_seq` 那三条）只能是判据。<br>⚠ **但在划这条线之前先问**：这个字段**恒等于一个常量吗**？是 ⇒ 该做的是**删字段**、把常量放回唯一落点，而不是给它一个类型（`op` 就是这个下场）。<br>⚠ ~~这一列现在没有取值能表达「载体在守」~~ → **已解决（2026-08-10）**：取值面一次加全，唯一落点 `contracts/invariants.py` 的 `WHO_CHECKS` |
| **能 0 成本现算的不落盘**（`title` / `source_id` 已三份全裁） | `_README.md` | 两份产物共 4 个 + `plan_beat` + `book.beat_density` → **全裁**（多出一条硬证据：产地自己每次落盘都重认目录，因为存的那份是坏的）。<br>⚠ **同一把尺子的第二种形态：恒等于常量的字段**（`op`）——不是「能现算」，是「不必算」，一样该删 |
| **定向返工只许改判据点名的部分，其余一字不动** | `R-4.10` | 本层无 LLM 返工回路；`--apply-edit` 是人工整份替换，走同一道闸 |
| **每新加一条 HARD 判据，就要有一份会让它红的样本** | `tests/test_ingest_fixtures.py` | ❌ **12 条 HARD（裁掉 `op_enum` 之后）一个反例都没有**（盘上三本全 PASS，判据表见 `stage4.md` §四） |
| **校验器先报来源再报判定** | `Q-4` | ❌ 本层校验器**从不调 `stale_inputs`**，而契约点名说它会 → **补来源行**（`R-5.10`） |
| **0 成本提升路不许弄丢来源**〔本轮新立〕 | `Q-5` | ❌ **实测 1/3 本的计划没有任何 `_provenance`**——问题稿裸写绕过带来源的落盘、提升路又不传 → **三处一起补** |

### 三条已被实测证明有效的纪律

- **「盘上 3/3」≠「必填非空」。** `platform` 在盘上三本全是 `'FQ'`，全语料 163 本里 **120 本是 `null`**。
  只按盘上定 schema 会定出一个在 74% 语料上崩掉的契约。
- **样本再多也补不出没发生过的分支。** 扩到 163 本修好了 `platform`，
  但 `source_encoding` 仍然只有 `utf-8`（候选集有五个）。那种字段的取值面**只能从代码来**。
- **每新加一条 HARD 判据，就要有一份会让它红的样本。**
  「见过它通过」不是证据，「见过它红一次」才是（`tests/test_ingest_fixtures.py`
  的 `test_every_hard_judge_this_layer_added_has_a_violating_sample`）。

### 已定：schema 的载体 = **Pydantic v2**

2026-08-10 裁决。判据是「这一份要同时喂**四个**消费方，谁当源头谁就必须能生成另外三份」：

| 消费方 | 从 Pydantic 怎么来 |
|---|---|
| 运行时校验 | 模型自身 |
| 前端 TypeScript 类型 | `model_json_schema()` → JSON Schema → TS |
| 人读的契约文档 | **反过来**：`docs/schema/*.md` 是源，模型照它写 |
| prompt 里的枚举插值 | **不经 Pydantic**，仍从 `contracts/invariants.py` 取 |

> #### ⚠ 两条 Pydantic 默认行为与本项目的纪律相反，骨架阶段必须显式改掉
>
> **① `extra` 默认是 `ignore`——未知字段被静默丢弃。**
> 这正是「不静默」那条纪律要挡的形态：上游多写了一个字段、或字段名拼错，
> 校验照常通过而那个值消失了。**一律 `extra='forbid'`。**
>
> **② 给可选字段配默认值，会让「没有这个字段」和「字段是 `null`」混掉。**
> 那是三态问题的重现（`FRESHNESS` / `JOB_STATE` 的 `unknown` 防的就是它）。
> 需要区分时用 `Field(...)` 强制必填，或显式建模成三态，**不要靠默认值兜**。

⚠ **`docs/schema/*.md` 仍然只定内容。** 载体定了不等于文档要改成模型——
那些 `.md` 是人读的源，Pydantic 模型在骨架阶段照它们写。**方向别搞反。**

### 待裁决

| # | 决策 | 卡住什么 |
|---|---|---|
| **stage7 另有 5 条** | `title_new` + `applied.title` 裁不裁（可现算）· `applied.*.chars_before/after` 裁不裁（唯一读者是一条 metric）· `_groups` 裁不裁（加载后 0 读者，但它是唯一的下发面记录）· **`book_txt.has_hook=false` 是否可达**（**产品语义**：允不允许出一本没有开篇文案的书；不可达就该删字段，`op` 先例）· `rn_unregistered` 补实现还是退役（**全仓 0 行实现，每本每次都打绿灯**） | `stage7.md` §九 |

> ### stage6 已清零（2026-08-10）：**四条裁决 + 两笔需求侧的账全落**
>
> **四条裁决**（全按建议）：
> ① `chars` / `baselines` / `counts` **全裁**（顶层 13 → 9 键）——⚠ `chars` 的现算**必须留在 API 层**
> （`measure=253` vs `len(text)=277`，差 9.5%；下沉到 JS 会让同一页出现两个字数，判据 14）。
> `baselines` 另有一条独立理由：它 13 格里有一格 **结构性说谎**（`hook_downsend_leak` 恒 `0/3`，
> 因为它压根不由 `hook_findings` 产）；
> ② `hook_reuses_source` **只改 `item` 措辞**说出主语——它守的是走 `--apply-edit` 的**人**，
> 不是模型（基准是**不下发**的原作文案段 ⇒ 模型看不见 ⇒ 对模型结构上不可能红）。
> `sev` 与切法一个字不动，**不拆条**；
> ③ 缺 `hook_copy.json` 改**显式终态**（`R-1.12` 已让它不可达，而它一旦执行会造出 **6 盏假绿灯**：
> 13 格判据打 13 个 `[OK]`，其中 6 条根本没跑），`hook_no_source` 随之退役；
> ④ `_provenance.code` 改 `git describe --always --dirty`，**这一条对全链生效**。
>
> **连带两条**：`mech.quote_problems` 升成判据 `hook_quote_unbalanced`（照 stage5 先例）·
> `mech` 三个计数器 **留 + 补出口** ——
> ⚠⚠ **与 stage5 的裁决相反，而这正是同一条判准的两面**：判准是
> 「**有没有判据依赖这次改写的结果**」。stage5 没有（记账没有服务对象 ⇒ 裁）；
> stage6 有两条（`hook_para_sep` HARD 明写是 `_normalize_hook` 的后置条件 · `hook_quote_style` INFO），
> 而且盘上 **6/9 候选 `paras_fixed=1`** ⇒ 那盏绿灯的信息量全在这个数里 ⇒ 留。
>
> **两笔需求侧的账**：`R-9` 从 8 条补到 **14 条 + `R-9.3b`**（25 条判据里 17 条曾找不到落点；
> 最刺眼的是**剧透上界 0 处落点**——「结局」二字一度 6 次全在 stage2）·
> **「谁在查」那一列的取值面一次定全**（见下）。
>
> ⚠ 顺带修了我自己写宽的一条：**恒等基线的 12 格里 6 格是恒等式**
> （`style_hook` 就是 `measure(原作文案)` 本身，实测逐值相同），
> 「全绿 ⇒ 判据没误伤原作」只能读一半。更值得记的是它的形状：那 5 条形态判据
> **是被修成恒等式的**——F-124 修 off-by-one 之前两侧是两把尺子、基线因此报过红并抓出了 bug；
> **修好之后它就不再携带信息了**。同一个基线格，修 bug 前有判别力、修 bug 后没有。

> ### 「谁在查」那一列的取值面 —— **已一次定全（2026-08-10）**
>
> 原病：解析器只认 6 个记号，而 `REQUIREMENTS.md` **有 10 行是它读不懂的**——
> 读不懂就等于**这一行什么也没声明**，却在所有测试里与「填了 `HARD`」一样绿。
>
> 加四个取值 + 一个行首形态，唯一落点 `contracts/invariants.py` 的 `WHO_CHECKS`（8 个）：
> **`度量`**（`R-7.8b`/`R-2.7`/`R-9.5`）· **`载体`**（`R-5.3` 前半）·
> **`结构`**（`R-9.1`/`R-9.2`）· **`不适用`**（`R-11.5`/`R-11.12`）· **「见 `R-0.x`」= 转授**（6 行）。
> 判据那一侧另有 `WHO_CHECKS_RULE`（5 个）——**少四个**：一条判据的存在本身就否定了
> 「载体在守 / 结构在守 / 转授 / 不适用」。
>
> ⚠ **`结构` 的准入条件**：那一格**必须写出代码落点**，否则它只是「无守卫」的化妆品。
> 六条新测试各配一个会让它红的样本（`tests/test_coverage.py`）；
> ⚠ 其中 `test_no_rule_claims_a_non_judge_requirement` **今天红不了**——`REGISTRY` 是空的，
> 与既有的反向覆盖率同一处境。**登记在这里，别当成已验证。**
>
> ✅ **stage7 拿它试过一遍，八个取值够用，不必加第九个。** 本层有 6 行标着 `HARD`
> 而 31 条判据里没有一条在查：五行按准入条件写出代码落点后改 `结构`
> （`R-10.1`/`R-10.2`/`R-10.3①`/`R-10.6`/`R-10.6b`）。
> ⚠ 剩下那一行（`R-10.7` 恒等基线）我一度想为它加取值「测试在守」——**那是判宽了**：
> `--identity` 不是一条无法表达的守卫，它是一条**跑完就把结论扔掉**的守卫
> （只 `print`，产物里一个键都没有 ⇒「跑过且无损」与「从没跑过」分不开）。
> 落点补上之后它就是普通的 `INFO`/`METRIC`。**先问「它是表达不了，还是根本没落点」。**

> ### stage5 已清零（2026-08-10）：**孤儿清理 + 六条裁决全落**
>
> **孤儿字段 / 断言**（用户指示「都可以清理掉」）：裁 `chNN.mech` · `chNN.dialogue_count` ·
> `review.tag` · `_provenance.state`，信息**换落点不丢**——归一计数器 → 校验报告一行、
> `quote_problems` → **新判据 `quote_unbalanced`**（`R-7.7` 第一次名副其实）、
> `dialogue_count` → **新度量 `dialogue_report`**；另把 `draft_missing` 挂着的第二件事拆成 **`draft_duplicated`**。
> ⚠ **两个「0 读者」我拦下来了**：`identity_hit`/`identity_dead`（已传到前端、只是没人渲染）·
> `_provenance.input_basis`（不可复算，且与 stage3/4 正要新增的 `revalidated` 同类）。
>
> **六条裁决**（全按建议）：
> ① **篇幅第二层宽限拿掉** —— `DRAFT_BAND` / `BOOK_BAND` 都是 0.90~1.10，与 stage4 同源的**产品语义变更**
> （10/31 章开始报提示级，全书 3/3 仍绿）；
> ② review 的 `title`/`source_id`/`review_seq` **全裁**（连 `tag` 共 4 个，顶层 12 → 8）；
> ③ `dialogue_report` **逐章 + 书级都报**，连带立 `R-7.8b`；
> ④ `secret_leak` **sev 不动**，但报绿灯时同一行打盲区计数、**盲区 100% 的书落 `unver`**；
> ⑤ **`B-03` 裁了一半**：闭卷上界 `DEEP_OVERLAP_MAX = 10%`（新判据 `deep_overlap_high`，`INFO`，
> **守的是臂的身份不是文风**），开卷不定线只报分布；
> ⑥ **`B-01` 裁了**：五项**不补判据，直到有 ground truth**，七项身份改成「给人核对的料」。
> ⇒ 判据 43 → **47 条**；`REQUIREMENTS.md` §五「待裁决」**已清空**。

`docs/schema/stage0.md` §四另有 5 条本层待决。
**`stage4.md` §六那条（「谁在查」缺「载体在守」）已随本轮一次定全清掉**——
它在三层上各露过一次头（stage4「载体在守」· stage5「度量在报」· stage6「代码结构在守」），
最后加了**四个取值 + 一个行首形态**一起解决，见上方那个框。
**stage1 ~ stage6 六层的本层待决现已全部清零**；
**stage7 的 5 条待决在上表**（`stage7.md` §九）。
⚠ **stage5 不撞它**：47 条判据**一条都不该上移**——本层拒收的代价是**重写一整章**，
是全链最贵的一次重试（`stage5.md` 事实②）。「判据 vs 载体按失败成本划」第一次被反向用上。
⚠ **stage6 也不撞它，但理由相反**：那里是**自救太便宜**（`--approve N` 换一条候选＝0 成本），
不是重试太贵。同一条分界线，两个方向都用过一次了。

> **stage4 的裁决（2026-08-10）一句话版**：**9 个字段 + 1 条判据裁掉**
> （`title`/`source_id` ×2 份 · `plan_beat` · `reconstruction_strategy` · `book.beat_density` ·
> `_provenance.op_forced` · **`op`** · 判据 `op_enum`）、预算带**去掉那第二次 ±10% 宽限**、
> 外加 **8 条「直接补」**（来源账要活过 `.INVALID` 提升 · 校验器补来源行 · 记账键补出口 …）。
> ⚠ **裁掉这么多但一条信息都没丢**——改现算、改落点、改成常量，或者本来就没携带信息。
>
> ⚠⚠ **`op` 那一族是被用户连追三轮追出来的，三轮都是我判宽了**：
> ① 首版给 `op_forced` 分两栏 + 补出口 —— 而模型根本不产 `op`，代码做的是
> **初始化不是改写**，那份记账 17/17 全是「本来是空的」；
> ② 第二版把 `op_enum` 归成「代码的后置条件，留着」—— 而**单字段取值面归载体**，
> 「读存量」那个理由也已被上一轮的裁撤本身杀掉（原型 3/3 本产物在新 schema 下**全部加载失败**）；
> ③ 第三版仍留着 `op` 字段，理由是「它是预算带的键」—— **循环论证**：
> 一个只有一个合法值的「键」是常量查表，`budget_band` 取 `OP_BAND[LOCKED_OP]` 即可。
> ⇒ **`license` 的先例这次完全适用**（第二轮我还专门写了「别拿它套」）：
> **一个常量伪装成 artifact 字段。**
>
> **三条教训都写进了下表**：补守卫前先问「那个位置本来有东西吗」；判据与载体的分界线
> **按失败成本划**；而在那之前还要先问一句——**「消费方读的是这个字段，还是它恒等于的那个常量？」**

### ⚠ stage4 与前面几层不同形（已产，详见 `docs/schema/stage4.md`）

| | 差别 |
|---|---|
| **两份产物** | `plan_chapters.json`（LLM 产，**只存决策与引用，一个字内容都不经它**）+ `writer_context.json`（**确定性组装**，非 LLM）。实测两者章级/拍级字段**逐值相同，0 处不一致**。<br>裁决后顶层各剩 **3 个键**（`plan`: 策略 + 输出章 + 来源账；`ctx`: 书级人物表 + 逐章 + 来源账） |
| **它是收口点** | `writer_context` 是**写作节点的唯一输入**——前面三层所有的逐章下发闸门（叫法时点、秘密揭示章、规则放行章、关系剧透闸门、setting 闸门、弧线只在末章）**最后都汇到它这一份上** |
| **判据只长在计划那一半** | 判据（原型 17 条 → 裁决后 **16 条** = 12 HARD + 4 INFO）**全部**查 `plan_chapters`；`writer_context` 落盘后**一条判据都没有**，它的正确性由「组装代码 + 上游合格」推出来 |
| **`writer_context` 可 0 成本重建** | `--context-only`。⚠ 也正因如此它**被覆盖过**：曾有 23 章成稿与盘上任何一版 `writer_context` 都配不上（匹配 1/23），配对关系永久丢失 |
| **⚠ 最该先看的一条** | **`.INVALID` 提升路会把 `_provenance` 整个丢掉**——盘上 1/3 本的计划因此没有任何来源信息，而没有一行报告会说出来（`stage4.md` 事实 ⑤） |

### ⚠ stage5 与前面几层不同形（已产，详见 `docs/schema/stage5.md`）

| | 差别 |
|---|---|
| **两条臂 + 两相** | 臂＝`deep`（闭卷）/ `shallow`（开卷），**只差 (system prompt, 给不给源章原文)**，产物形状逐字节相同；相＝逐章写作 + 全书验收。**判据表只有一张**（47 条），两相共用 |
| **唯一有 LLM 返工回路的层** | `--revise` 把可反哺的结论连同上一轮成稿发回写手。护栏有 4 条，但**没有一条查「其余一字不动」**——它们查的是「没变得更坏」（`R-4.10` 在 stage3 量到的那个缺口，这里同样在） |
| **默认调用不是结构化的** | 逐章走 `ask_text`（**纯文本响应**）⇒ stage4 那条「响应侧 `extra='forbid'` 当场挡回」的出路**在本层默认路径上不存在**。只有 `--batch N`（A/B）走 `ask_json`——而**它没有声明 `require=`**（`stage5.md` §七） |
| **判据一条都不上移** | 拒收＝重写一整章＝全链最贵的一次重试。47 条判据判的全是自由文本的内容属性，类型系统够不着；够得着的三个字段（`op`/`mode`/`from_source_ordinals` 长度）**本来就没有判据** |
| **7 条 HARD 盘上 0 命中，43 条里 26 条从未命中** | 而 `secret_leak`（HARD）的基线说明这盏灯基本不会亮：**21 条 secret 里 19 条连它自己的揭示章都命中不了** |
| **⚠ 最该先看的一条** | **`--revalidate` 提升路不是丢来源账，是【伪造】一份**——盖上当前的 SYS 指纹 + `model` + 当前 inputs，`round` 还 +1，而没有任何一次新落笔发生过。<br>而 `--apply-edit` **在同一个文件里、相隔 30 行，把这三件事全做对了**（`stage5.md` §5.1） |

### ⚠ stage6 与前面几层不同形（已产，详见 `docs/schema/stage6.md`）

| | 差别 |
|---|---|
| **全书级单次产出** | 一本书 **1 次调用产 3 个候选**，没有 `output_ordinal`、进不了逐章骨架。全链最便宜的一层，**也因此样本最少**：一条判据的分母只有 9（候选）或 3（书） |
| **判据表分两半，边界是【产物类】** | 文案是「全文改写」的产物类 ⇒ **查连续文本的字面探针结构性失效**（`secret_keywords` 在三本原作文案上 0/4 · 0/7 · 0/10），查离散符号的仍然有效。这不是判据没写好——把失效那一半标成 `unver`（9 条里 3 条）才是对的 |
| **现役闸门是【人定稿】，不是代码** | 25 条判据、5 条 HARD，**没有一条在查「文案有没有替故事把话说完」**。`--approve N` / `--apply-edit` 都是 0 成本 ⇒ 判据一条都不上移（同 stage5，但理由相反：这里是**自救太便宜**，那里是**重试太贵**） |
| **`--identity` 是写 prompt 之前就能跑的一道工序** | 拿原作文案段当"候选"喂进整套判据。**已判死两条坏判据**（一条 HARD 退役、一条阈值口径改版）。本轮三本重跑：13 条 hard+info **恒等基线全 `0/1`** |
| **5 条 HARD 盘上 0 命中、恒等 0 命中** | 而其中 `hook_reuses_source` **对模型结构性不可能红**：它的基准是原作文案段，而那份**按设计不下发**——模型看不见的东西谈不上复用。它守的是 `--apply-edit` 那条路上的**人** |
| **⚠ 最该先看的一条** | **三条 0 成本路（`--approve`/`--apply-edit`/`--recheck`）全部重盖来源账，而盘上有逐字节铁证**：三岁那本的归档与正式产物**候选文本、findings 逐字相同**，`prompts.N8_SYS` 却是两个值。<br>同一份 `_provenance` 里还有第二处矛盾：**按内容算的指纹变了，而按 git HEAD 算的 `code` 一个字没动**——工作树脏时它照样给出一个看起来精确的答案（`stage6.md` 事实③ · §三） |

### ⚠ stage7 与前面几层不同形（已产，详见 `docs/schema/stage7.md`）

| | 差别 |
|---|---|
| **模型只写两个键** | 响应只有 `surname_map` + `characters[]`，产物里另外 **19 个顶层键一个字都不经模型**。`Q-7` 在这一层贯彻得最彻底：正文字节由单次扫描搬运，**模型连正文都看不到**。<br>⇒ 本层的**零消费审计比前几层可靠**：stage4 那类假阴（整份 dump 进 prompt、grep 报 0 下游）**结构上不存在**——下发材料由代码从人物表现造，不读产物的任何字段 |
| **四份产物，其中两份不是常规 JSON** | `book.txt` 是**字节形态**的交付物（`stage7.md` §四，同 `stage5/draft.md` 并进本层文档）；`name_map_edit.json` **不是节点产的，是人产的输入**——但它落在产物目录、被 `--apply-edit` 当决策集吃掉，所以形状同样是契约（§三） |
| **判据一条都不上移，而理由是【两个叠加】** | stage5 是「重试太贵」、stage6 是「自救太便宜」——**本层两条同时成立**：拒收 ⇒ 本层**唯一那次付费调用**整个作废；而 `--apply-edit` 收完整决策集，人直接改对，0 成本。<br>⇒ 与 stage4 相反，本层**不给响应侧开 `extra='forbid'`**；唯一该归载体的是**人写的编辑文件**（拒收＝人改一行） |
| **31 条判据 / 22 条 hard+info，盘上只命中过 1 条** | 15 条 HARD **全 0 命中**，`--check-only` 每本打 **21 行 `[OK]`**。⚠ 别读成「判据多余」——恒等基线三本逐字节无损、残留探针每本都在报东西。真问题只有两条，见下两行 |
| **⚠ 最该先看的一条** | **一条 HARD 判据在它自己的违规旁边打绿灯**：`rn_new_collide`（「新名全书唯一」）只看模型写的 `new_canonical`，**看不见人手补的 `extra_subs`**——而盘上書1 恰好有两条映射指向同一个新名，从那扇门进来的。<br>⚠ 那一次撞名**恰好是对的**（原作对同一个人有两种写法），所以修法不是扩面判红，是照本层自己的先例落一条 `INFO`（`stage7.md` 事实⑤ · §1.4） |
| **⚠ 第二条** | **`rn_unregistered` 在判据表里注册着，全仓 0 行实现**——`grep` 只有判据表那一行本身，而它是 `info` ⇒ **每本每次都打绿灯**。更糟的是它就挂在源码注释「本节点唯一能发现『白名单不够』的东西」下面，读的人会以为漏网有两道防线（`stage7.md` 事实⑥） |
| **恒等基线跑完把结论扔掉了** | `--identity` 是 `R-10.7`、标着 `HARD`，而它**只 `print`**：产物里没有任何一个键记得它跑过没有。**对照 stage6 在同一道工序上做对了**（`baselines_identity` 落在产物里，正因为有落点才判死过两条坏判据）（`stage7.md` §六） |

---

## 目录

```
docs/REQUIREMENTS.md     需求规格。自足（0 处外部引用），每条带「谁在查」列
docs/schema/             schema 十一份（八层 + `_common.md` + `_report.md` + `_orchestration.md`）。**它们是源，模型照它写**
docs/design_brief/       前端重做的 brief 包 + 19 份【真实 API 响应】样本
contracts/               契约层（见下）。可单独 import，不拉运行时
novel/                   运行时。**单向依赖 contracts**，由 tests/test_layering.py 钉死
web/                     前端（审校台 mock 版）。骨架阶段当它不存在
tests/                   守卫。契约层的 + 运行时的
```

## `contracts/` 是什么

**不只是 schema。** 光有 schema 会长出这个项目最典型的那种故障——
一条读起来和有守卫的约束一模一样、但**没有任何代码在查**的条款。三块：

| 模块 | 作用 |
|---|---|
| `invariants.py` | 跨层取值域的**唯一落点**：三态 / 四级严重度 / 拍的时间层 / 保真类型 / 篇幅带 / 写法词表 / 发明清单…… 所有别处（prompt、API、前端）都从这里生成或插值，**不许手抄** |
| `rules.py` | **判据注册表**。每条判据必须声明 `sev` / `owner` / `who_checks` / **`requirement`**（指向 `R-x.y` 或 `B-xx`）；缺一项注册失败 |
| `requirements.py` | 解析 `docs/REQUIREMENTS.md`，把 `R`/`Q`/`B` 编号与「谁在查」列读成数据 |

### 它的验收判据是**双向覆盖率**（`tests/test_coverage.py`）

- **反向**：每条判据的 `requirement` 必须在需求文档里存在。指向不存在的编号 → 测试红
- **正向**：每条「谁在查 ≠ 无守卫/人/待建」的需求，必须至少有一条判据指着它。
  声称有守卫却没有 → 测试红
- **B 清单一致性**：`无守卫 → B-xx` 的 `B-xx` 必须存在；而**一旦有判据指向某条 `B-xx`，
  那条需求就不该再写「无守卫」**——补了守卫要同步改需求，否则文档开始说假话

> 这三条把 `REQUIREMENTS.md` 的「谁在查」那一列从**装饰**变成**可执行**。
> 上一版原型的教训就是这个：三条写在 prompt 里的硬约束在盘上各违反一次，全链没有一层出声。

---

## 与快速原型仓的关系

`../Novel_refactoring/` = **原型 + 证据，只读**。

它值钱的不是代码，是三样东西，都已经提取到本仓或仍需按需回查：

1. **契约与实测数字** —— 已合成进 `docs/REQUIREMENTS.md`（自足，不引用那边）
2. **约 3.5 万字 system prompt** —— 压着 120+ 条真机 findings 的学费，移植阶段逐条搬
3. **确定性核心** —— 分章词法（161 本语料标定）· 锚点定位与四级回取 · 重合度 ·
   人名替换的三道防线 · 形态档位 · 来源指纹 · 原子落盘。**重写等于把踩坑的证据丢掉**，
   移植时连注释一起搬（注释里写的是「为什么不能用那个更直观的写法」）

**不带走的**：目录布局 · argparse dispatch · `Web/` 的实现（交互设计带走，代码不带）。

⚠ 本仓**还没有远程**。旧仓有（私有 GitHub）。在建远程之前，这边的东西只存在于本机一份。
