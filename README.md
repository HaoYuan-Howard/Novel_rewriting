# Novel rewriting

把一本中文网络小说的原作，经 7 个阶段处理成一本**新书**：情节骨架保留，文字全部重写，
人名全部替换，最终交付一个 `book.txt`。

**这是正式开发仓。** 快速原型在 `../Novel_refactoring/`，那边**只读**（见最后一节）。

---

## 这三份文档各管什么

| 文档 | 只管这件事 |
|---|---|
| **本文件** | 这个仓是什么 · 现在在哪 · 下一步怎么接手 |
| `docs/REQUIREMENTS.md` | 系统**该做到什么** + 每条**谁在查**（`R` / `Q` / `B` 三族 + 双向覆盖率） |
| `docs/LESSONS.md` | 踩过的坑与可迁移的经验，15 条，每条带本仓落点 |

另有两份不在这条轴上：`docs/schema/`（产物契约，十二份，**它们是源，模型照它写**）·
`docs/design_brief/`（前端 brief 包 + 19 份真实 API 响应样本）。

> ⚠ **本文件不复述需求内容，也不记已完成期的施工日志。** 前者去 `docs/REQUIREMENTS.md`，
> 后者在 git 历史里（`git log --oneline`，每条的病因与修法都写在 commit body 里）。
> 值得带走的那部分已经提炼进 `docs/LESSONS.md`。

---

## 现在做到哪一步

顺序：**需求 → 契约（= 产物 schema）→ 骨架 → 移植**。

| 阶段 | 状态 |
|---|---|
| **需求** | ✅ `docs/REQUIREMENTS.md` —— `R-0`~`R-14` · `Q-1`~`Q-12` · `B-01`~`B-22` |
| **契约** | ✅ **十二份全部产出** —— `docs/schema/` 的 stage0~stage7 + `_common.md` + `_report.md` + `_orchestration.md` + `_api.md` |
| **骨架** | ✅ **A~E 期全部完成**（A = 契约层 · B = 共享运行时 / LLM 层 / 报告构造器 · C = stage3 纵切 · D = 编排 + CLI · E = HTTP 层） |
| **移植** | 🚧 **F 期进行中**：`F0` 清障 ✅ · `F1` = stage6 ✅ · `F2` = stage7 ✅ · `F3` = stage4 ✅<br>`PORTED_STAGES = {'stage3','stage4','stage6','stage7'}`，**八层已过半**。**下一步 = F4 = stage5** |

---

## 新 session 从这里接手

> ⚠ **记忆读不到。** Claude Code 的记忆按目录键，之前几轮的上下文全在 `../Novel_refactoring` 那一侧。
> **本仓的交接一律以文档为准。**

### 跑测试

```
D:\Miniconda\envs\novel_refactor\python.exe -m pytest -q
```

⚠ 项目环境是 **conda env `novel_refactor`**（Python 3.11，已装 pydantic 2.13 / openai / fastapi）。
默认的 `python` 指向 Miniconda base，**那里没有 pydantic**。
⚠ 本机 `%TEMP%\pytest-of-yh255` 的 ACL 坏了，pytest 的 `tmp_path` 一用就炸，测试里用 `tempfile.mkdtemp()`。
⚠ 语料是按**相对路径**找 `../Novel_refactoring/` 的。把树抽到别处（比如 `%TEMP%`）会让
`tests/test_corpus_fixtures.py` 从 279 条塌到 6 条**而不报错**——要重量历史基线，
必须抽到 `E:\practice_group\` 的同级目录下。

**当前基线：全绿，1465 passed / 20 skip。**

**每一条 skip 都明说「没在查」——改动之后先核对这个数**，它变了就说明有守卫被静默关掉或打开了。
20 条的来历：

| 条数 | 是什么 | 什么时候消失 |
|---|---|---|
| **18** | `tests/test_ingest_fixtures.py` 的 `importorskip` | F6/F7 落地那天，一次变成 **18 个 `pytest.fail`**（见下） |
| **2** | 都指着**同一份产物**：`成年人不做选择` 那本的 `stage4/plan_chapters.json` 在新契约下加载不了，因为它**根本没有 `_provenance`**（0 成本提升路把来源账整个丢了）。语料体检与落盘往返各跳过它一次 | **不该消失**。⚠ **不许靠伪造一份来源账把它变绿**——那样这份产物就再也不会提醒任何人「提升路会丢掉来源账」 |

⚠ 还有一处**刻意避开的 skip**：那本书在 `tests/test_stage4.py` 的三条参数化里本可以 `pytest.skip` 掉，
但那会给「每一条 skip 都明说没在查」多出**三条本来就不该存在**的记录——它不是「没查」，
是**由另一条专门的测试查着**。⇒ 参数化改成只走读得回来的那两本（`readable_books`）。

### 按什么顺序读文档

| # | 读什么 | 得到什么 |
|---|---|---|
| 1 | `docs/schema/_README.md` | **方法**：三源合并 · 证据列的四个取值 · 为什么文档只定内容、模型照它写 |
| 2 | `docs/schema/stage2.md` | **模板（首选）**。调 LLM 的层的完整样板：模型写的 vs 代码写的怎么分开读、落盘前改写怎么记账、记账键的出口规则 |
| 3 | `docs/schema/stage1.md` | 不调 LLM 那种层的模板 |
| 4 | `docs/schema/stage0.md` | 同上，另含「盘上零产物的一层怎么写」（全部标 `设计`） |
| 5 | `docs/schema/stage5.md` | **下一步要移植的那一层**：两条臂 + 两相 · 47 条判据一张表 · 四条 0 成本路 · 一份字节形态产物（`draft.md`） |
| 6 | `docs/REQUIREMENTS.md` | 每条需求的「谁在查」列——schema 里的 `[HARD]` 都要能追到它 |
| 7 | `docs/LESSONS.md` | 15 条已实测的坑。**移植前过一遍**，它们多数是「改一行就静默失效」那一类 |

⚠ 已移植四层的契约（`stage3.md` / `stage4.md` / `stage6.md` / `stage7.md`）**按需回查**，
不必通读——它们的结论已经落进 `contracts/judges/` 与各层实现。

---

## 下一步：F4 ~ F7

### 顺序：**按验证价值挑，不按管线顺序挑**

施工方案写的是 stage2→4→5→6→7→(0/1)，那是**管线**顺序。实测下来体量极不均匀
——**stage2 一层就占 prompt 的 57%（20 804 / 36 709 字）与判据的 62%（91 / 147）**，
而且**它是唯一一层没有判据表的**：原型把 91 条判据塞在一个 1688 行的 `check_one()` 里，
全是 `rep.fail('锚点唯一性', …)` 这种中文首参，**一个 id 都没有**。移植它 = 先从零建一张表。

⇒ 已裁决**先用最便宜的一层把「加一层」那张清单走通一遍**（同 C 期选 stage3 当第一条纵切的判准）。
剩下的顺序与实测体量：

| 顺位 | 层 | 原型行数 / prompt 字 / 判据 | 备注 |
|---|---|---|---|
| **F4** | **stage5** | 4370 / 6164 / 43（有表） | 体量最大；`stage5.md` §6.1 已列 **20 条判据在需求里没有落点** |
| F5 | **stage2** | 3597 + 1110 / **20804** / **91（无 id）** | 最难。要先建 `novel/core/` 的 spans / overlap 两块 |
| F6 | stage1 | 828 / 0 / 13（无 id） | 零 prompt 零调用；沿 stage0/stage1 的新边界把「摄入+分章」切开 |
| F7 | stage0 | **0** / 0 / 0 | **不是移植是新写**——原型这一层一行代码都没有 |

### F4 = stage5 的施工提要

它与前面几层**不同形**，四条在开工前必须知道（全文见 `docs/schema/stage5.md`）：

- **两条臂 + 两相**：臂＝`deep`（闭卷）/ `shallow`（开卷），**只差 (system prompt, 给不给源章原文)**，
  产物形状逐字节相同；相＝逐章写作 + 全书验收。**判据表只有一张**（47 条），两相共用，
  而 `R-7` / `R-8` 两节同时管它 ⇒ 正向覆盖率取**两节的并集**，**不能只移植一半**。
- **第一个逐章产物的层**（`stage5/{mode}/chNN.json`）：完成度与来源账都要按章数。
- **唯一有 LLM 返工回路的层**（`--revise`）：护栏有 4 条，但**没有一条查「其余一字不动」**
  ——它们查的是「没变得更坏」。
- **默认调用不是结构化的**：逐章走 `ask_text`（纯文本响应）⇒ stage4 那条
  「响应侧 `extra='forbid'` 当场挡回」的出路**在本层默认路径上不存在**。

本层还要就地裁两件事：

| 待裁 | 分歧 |
|---|---|
| `--revise` 挂哪个节点 | `contracts/invariants.py::NODES` 现在把它挂在 `stage5.review`，而原型挂在写作节点（它产的是新一轮成稿，不是新一份验收报告）。两种读法都说得通，**指向两段不同的代码**，今天没有任何实现可以裁定谁对 ⇒ 连同一份会红的样本一起裁 |
| 两套新鲜度机制合并 | `sources.writer_context` 与 `_provenance.inputs` 并存。合并要动 stage5 的 `ReviewSources`，前三层移植时都**有意不合** |

### ⚠ F6/F7 那颗地雷

**`tests/test_ingest_fixtures.py` 的 18 个 `importorskip` 会一次变成 18 个 `pytest.fail`。**
触发条件是**模块存在**，不是进 `PORTED_STAGES`——只要建出 `tests/fixtures_ingest.py` 那张
`_IMPL_MODULE` 表点名的模块（`novel.stages.stage0.removal` / `novel.stages.stage1.partition`，
哪怕空壳）就会发作。**「建模块」与「补 18 条断言」必须在同一次提交里。**
这是那份测试有意设计的机制。

---

## 仍欠着的（登记，**不是**验过了）

### 跨层

| 欠账 | 状态 |
|---|---|
| **付费路一次都没跑** | 已移植的三层（stage4/6/7）都是：合成样本走通了闸门与提升路，但 `DeepSeekTransport` **从没发过一次请求**。已用 `--dry-run` 验过渲染出的 system 与原型逐字节相同（stage4 2192 字 / stage6 1075 字 / stage7 1658 字）。<br>⚠ stage4 那次是**全链最贵的一次请求**（全书一次调用，`max_tokens=96000`，user 实测 22773 字）。**真发第一次要先说一声**——那是本仓第一次真花钱 |
| **36 条 HARD 盘上 0 反例样本** | stage4 16 · stage6 5 · stage7 15。已补的合成样本只走通了 3 条（`style_words` / `rn_ambiguous` / `context_link`）。跨层规矩「每新加一条 HARD 就要有会让它红的样本」在这三层仍未兑现 |
| **`_provenance.code` 那条没修** | 11 份产物 `code` 全是同一个短 sha，而同一批的 `N9_SYS` 取过 **3 个**值。⚠ **处置只定一次，别按层分别裁**——`provenance()` 在共享层，改 `git describe --always --dirty` 一改就是八层。目前只**钉在测试里** |
| **两套新鲜度机制并存** | `sources.writer_context` 与 `_provenance.inputs`。属 F4（见上） |
| **`to_human_edit()` 只长在 `LlmProvenance` 上** | stage6/stage7 的 `--apply-edit` 够不着它（`--approve` 之后上一份来源账可能已是 `FreeProvenance`）。⇒ 真要用它的那一刻，连同一份会红的样本一起挪到基类 |
| **重试与并发那三个数是拍的，不是量的** | 手上没有真实 429 分布。`Q-10` 已把「重试退避调参」登记为「现在不建」；调它之前先拿到真数据 |
| **4 行 `结构` 仍是旧形态** | 29 行 `结构` 里 `R-4.2` / `R-4.3` / `R-4.3b` / `R-4.10` 四行写的是裸符号名，`tests/test_coverage.py::test_structural_rows_that_name_a_repo_path_actually_point_at_it` 对它们**不可见**（现扫得到 35 个落点）。改成 `路径::名字`，**改一行那条守卫就多查一行** |

### 按层

| 层 | 欠账 |
|---|---|
| **stage4** | `stage4.plan --apply-edit` 登记在案而**没接线**（显式终态说清「有，但还没实现」；接线时 `docs/schema/_common.md` §5.3 那份 `human_provenance` 是教科书，**连注释一起搬**）<br>`--identity` 在 `/api/enums` 上的 `writes` 串说的是**错的文件名**（说 `plan_chapters.json`，实际写 `writer_context.identity.json`）。⚠ 方向是安全的（它确实写盘），已钉在测试里，**别顺手去改 `WRITE_EFFECTS`**——那张表按 flag 索引，而这个 flag 在三层行为各不相同 |
| **stage6** | `stage6.hook --apply-edit` 同样**没接线**<br>`hook_para_sep` 对单段文案给的是**错的诊断**：detail 写着「归一化应该已经修掉它了」，而单段文本没有分隔符可归一。判定本身不冤，只是那句话在这一类输入上是假的 |
| **stage7** | 盘上那三份 `name_map_edit.json` **没有被端到端跑过**（它们是 `character_patches` 那条路，沙箱里没有构造过一次完整重放） |
| **F5 要连带补的** | `novel/core/spans.py` 缺 `recover` 那条臂（stage2 落盘路才用得上，连同 `_lcs` 与 `HINGE_LCS_MIN` 一起进来）<br>`--drop` 与 `--replay-cache` 两个 0 成本 flag **从没被任何守卫查过**（它们在原型的 `FREE_FLAGS` 里而抓包的 `actions` 里没有）。⚠ 不全的方向是安全的（未登记 ⇒ 按付费算），但那是「界面把免费动作画成橙色」 |

### 界面与前端

| 欠账 | 状态 |
|---|---|
| **`R-11.10` 只验了半格** | 「第二个请求 409」只验到状态码映射表；没有 `POST /api/jobs`（用户裁决：不让浏览器能花钱）⇒ **没有第二个请求可以撞**。**别把这一格读成验过了。**<br>三处索引：`docs/REQUIREMENTS.md` 第五节那个框 · `docs/schema/_api.md` §七 · `tests/test_api_discipline.py` 那条名字很长的测试（它同时钉着「POST 面只有 `preview`」，哪天多了一个会逼人回来补） |
| **check 路由改名的账** | `stage5_review` → `stage5`。八个 kind 现在与 `invariants.STAGES` 逐值相等、由它派生（落点 `novel/api/deps.py::checked_kind`），`docs/design_brief/04_delivery.md` 那一格已改并带标注，**`web/` 里的引用还欠着** |
| **`novel/views/` 下三屏没做** | stage4 / stage6 / stage7 各自那一屏与那条 API 路由（现在只有 `stage3.py`）。⚠ 做的时候：stage4 的**三个 `source_chars` 口径别混** · stage6 的 `candidates[].chars` 现算**必须留在 Python** · stage7 的 `book_txt.chars` 是全链第三个 `chars` 口径，**别拿它和另外两个比** |
| **`web/` 一个字没动**（已裁决） | 欠着四笔：71 处写死的原型作业名（`n4_plan` / `n9_rename` / `stage5_review` …）· S-13 屏按 `rules[]` 重画 · 两份 check mock 重生成 · `web/src/constants/actions.ts` 那张 `WRITE_EFFECTS` 第二落点可删（`/api/enums` 现在发 `writes` / `writes_disk` 了）<br>⚠⚠ **`docs/design_brief/05_samples/` 里的 json 永远不许用新后端的输出覆盖**——它是 `tests/test_api_enums.py` 与 `tests/test_invariants.py` 全部对账的**证据基线**，覆盖之后那一族测试就变成拿 `invariants` 跟它自己比。新形状的样例要另起路径 |
| **`/check/stage0` 那颗地雷** | stage0 三个节点 `flags` 全空 ⇒ 一个都不接 `--check-only`。守卫已经写下且**今天是绿的**（`test_every_ported_stage_has_exactly_one_check_node`），它在 stage0 进 `PORTED_STAGES` 而没人给它登记跑法的那一刻变红 |
| **`/api/novels` 在全语料 163 本上会慢** | 每本跑一遍完成度。**登记不优化**——要优化就给 `overview()` 加浅层分支，**别造第二张清单**（原型那张的代价是 stage5 被整个排除） |
| **`PARTIAL_MASK_FIELDS` 那四个字段名不立** | 零消费方，F5 落地屏蔽实现时才生，并同时加一条「本句里必须逐个出现那四个字段名」的测试 |

---

## 待裁决

| # | 决策 | 卡住什么 |
|---|---|---|
| 1 | **审阅闸门覆盖哪些书**：全量 / 只审有检出的 / 全量但零检出可批量放行 | `docs/schema/stage0.md` §四。实测 27/163 本有检出、136 本干净；「只审检出的」会让盲区①永远不进队列。**先跑通几本，按真实上传的书再定** |
| 2 | 重跑 stage0 时**人工编辑的合并策略** | 同上。`R-0.8` 只规定了「不许静默覆盖」，没规定怎么合并 |
| 3 | `clean_text.txt` 是否也参与下游 `_provenance.inputs` 的指纹 | 同上。多记一层能定位「问题出在剔除还是分章」，代价是指纹链更长 |
| 4 | 人工**改正文**要不要逐条记账 | 同上。本轮有意不做，代价是 `clean_sha1` 的差异不可分解 |
| 5 | **建远程仓库** | ⚠ **本仓仍然没有远程，本机是唯一一份拷贝**——`Output/` 那批语料同样只有一份 |

---

## 仓库结构

```
docs/REQUIREMENTS.md     需求规格。自足（0 处外部引用），每条带「谁在查」列
docs/LESSONS.md          15 条踩过的坑，每条带本仓落点
docs/schema/             产物契约十二份。**它们是源，模型照它写**
docs/design_brief/       前端重做的 brief 包 + 19 份【真实 API 响应】样本
contracts/               契约层（见下）。可单独 import，不拉运行时
novel/                   运行时。**单向依赖 contracts**，由 tests/test_layering.py 钉死
web/                     前端（审校台 mock 版）。骨架阶段当它不存在
tests/                   守卫。契约层的 + 运行时的
tools/                   0 成本工具（schema 反推 / 剔除词闸门）
```

### `contracts/` 是什么

**不只是 schema。** 光有 schema 会长出这个项目最典型的那种故障——
一条读起来和有守卫的约束一模一样、但**没有任何代码在查**的条款。三块：

| 模块 | 作用 |
|---|---|
| `contracts/invariants.py` | 跨层取值域的**唯一落点**：三态 / 四级严重度 / 拍的时间层 / 保真类型 / 篇幅带 / 写法词表 / 发明清单…… 所有别处（prompt、API、前端）都从这里生成或插值，**不许手抄** |
| `contracts/rules.py` | **判据注册表**。每条判据必须声明 `sev` / `owner` / `who_checks` / **`requirement`**（指向 `R-x.y` 或 `B-xx`）；缺一项注册失败 |
| `contracts/requirements.py` | 解析 `docs/REQUIREMENTS.md`，把 `R`/`Q`/`B` 编号与「谁在查」列读成数据 |

⚠ **改 `docs/REQUIREMENTS.md` 的表格形态之前先读 `contracts/requirements.py`。**
它只认三种行首形态（R 节标题 / **恒三列**的 R 条目行 / Q 节标题 / B 清单行），
读不懂一个格子时**不会报错**——它只是把那一行读成「什么也没声明」。

### 它的验收判据是**双向覆盖率**（`tests/test_coverage.py`）

- **反向**：每条判据的 `requirement` 必须在需求文档里存在。指向不存在的编号 → 测试红
- **正向**：每条「谁在查 ≠ 无守卫/人/待建」的需求，必须至少有一条判据指着它。声称有守卫却没有 → 测试红
- **B 清单一致性**：`无守卫 → B-xx` 的 `B-xx` 必须存在；而**一旦有判据指向某条 `B-xx`，
  那条需求就不该再写「无守卫」**——补了守卫要同步改需求，否则文档开始说假话

> 这三条把「谁在查」那一列从**装饰**变成**可执行**。
> 现状：`contracts/judges/` 下 **91 条判据指向 61 个不同编号**，覆盖 `R-4`/`R-5`/`R-6`/`R-9`/`R-10`。

---

## 与快速原型仓的关系

`../Novel_refactoring/` = **原型 + 证据，只读**。

它值钱的不是代码，是三样东西：

1. **契约与实测数字** —— 已合成进 `docs/REQUIREMENTS.md`（自足，不引用那边）
2. **约 3.5 万字 system prompt** —— 压着 120+ 条真机 findings 的学费，**移植阶段逐字搬不许转述**。
   已进来的四层各配一条会红的测试钉着「与原型逐字节相同，**且渲染之后也相同**」
3. **确定性核心** —— 分章词法（161 本语料标定）· 锚点定位与四级回取 · 重合度 ·
   人名替换的三道防线 · 形态档位 · 来源指纹 · 原子落盘。**重写等于把踩坑的证据丢掉**，
   移植时连注释一起搬（注释里写的是「为什么不能用那个更直观的写法」）

**不带走的**：目录布局 · argparse dispatch · `web/` 的实现（交互设计带走，代码不带）。
