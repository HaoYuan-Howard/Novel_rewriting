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
| **移植** | 🚧 **F 期只剩 stage2**：`F0` 清障 ✅ · `F1` = stage6 ✅ · `F2` = stage7 ✅ · `F3` = stage4 ✅ · `F4` = stage5 ✅ · **`F6` = stage1 ✅ · `F7` = stage0 ✅**<br>`PORTED_STAGES = {'stage0','stage1','stage3','stage4','stage5','stage6','stage7'}`，**八层去七**。<br>**`F5` = stage2 正在推进**（最难的一层，见下）：`F5-0` 落点审计 ✅（`docs/schema/stage2.md` §五 从零建出 **75 条**判据表 + `docs/REQUIREMENTS.md` 补 17 行）·**下一步是 `F5-1` 契约层四格** |

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

**基线：F6/F7 收工时实测 `1597 passed / 2 skip` 全绿**（`452b22f`）。
F5-2（`ffbc7c7`）给 `tests/test_core.py` 加了 5 条 ⇒ **该是 `1602 / 2`**。

> ⚠⚠ **这个数 F5-2 收工时【没能实测到】，别把它当核过的。** 2026-08-12 19:25 起
> 另一个会话在同一工作树里加 stage0 的摄入/上传路（9 个新端点），树上因此挂着
> **4 红 + 5 错**（全是它的：`test_api_http` 的「九个端点」面 · `test_api_enums`
> 的 `len(actions) == 69` · `test_api_discipline` 的 409）。
> 实测到的是 **`1600 passed / 2 skip / 4 failed / 5 error`**（共 1611），
> 其中本轮自己那几个文件全绿（`test_core.py` 37 条 + `test_stage4_beats.py` 21 条）。
> 详情与「哪些文件被它占着」见下方 **F5 从这里接** 那一节开头的方框。

> ⚠ **`git worktree` 里跑不出这个数，那不是环境坏了。** 拿 HEAD 开一个干净 worktree
> 实测 `1593 / 3 / 1F / 2E`——差的那几条是 **F6/F7 的 `tests/test_stage01.py`
> 依赖 gitignore 掉的 `Test_data/` 与 `outputs/`**（`test_test_data_has_three_byte_comparable_prototype_outputs`
> 当场红）。⇒ **一份全新 clone 跑不出全绿**，那几条断言只在有实跑产物的那棵树上成立。
> 登记在这里，别当成回归。

**每一条 skip 都明说「没在查」——改动之后先核对这个数**，它变了就说明有守卫被静默关掉或打开了。
2 条的来历：

| 条数 | 是什么 | 什么时候消失 |
|---|---|---|
| **2** | 都指着**同一份产物**：`成年人不做选择` 那本的 `stage4/plan_chapters.json` 在新契约下加载不了，因为它**根本没有 `_provenance`**（0 成本提升路把来源账整个丢了）。语料体检与落盘往返各跳过它一次 | **不该消失**。⚠ **不许靠伪造一份来源账把它变绿**——那样这份产物就再也不会提醒任何人「提升路会丢掉来源账」 |

⚠ 还有一处**刻意避开的 skip**：那本书在 `tests/test_stage4.py` 的三条参数化里本可以 `pytest.skip` 掉，
但那会给「每一条 skip 都明说没在查」多出**三条本来就不该存在**的记录——它不是「没查」，
是**由另一条专门的测试查着**。⇒ 参数化改成只走读得回来的那两本（`readable_books`）。

### 按什么顺序读文档

| # | 读什么 | 得到什么 |
|---|---|---|
| 1 | `docs/schema/_README.md` | **方法**：三源合并 · 证据列的四个取值 · 为什么文档只定内容、模型照它写 |
| 2 | `docs/schema/stage2.md` | **模板（首选）**，而且**它就是下一步要移植的那一层**（F5，最难）。调 LLM 的层的完整样板：模型写的 vs 代码写的怎么分开读、落盘前改写怎么记账、记账键的出口规则。⚠ **§五 那张 75 条的判据表是 F5-0 从零建的**（原型那边一个 id 都没有，严重度只由方法名 `fail`/`note`/`unverified` 承载）——判据行、`checks.py`、需求补行、会红的样本**全以它为源**，先读 §5.0 的计数账再往下 |
| 3 | `docs/schema/stage1.md` | 不调 LLM 那种层的模板 |
| 4 | `docs/schema/stage0.md` | 同上，另含新写摄入层的审批状态机、人工编辑与归档契约；F7 已有盘上临时跑盘证据 |
| 5 | `docs/REQUIREMENTS.md` | 每条需求的「谁在查」列——schema 里的 `[HARD]` 都要能追到它 |
| 6 | `docs/LESSONS.md` | 15 条已实测的坑。**移植前过一遍**，它们多数是「改一行就静默失效」那一类 |

⚠ 已移植七层的契约（`stage0.md` / `stage1.md` / `stage3.md` / `stage4.md` / `stage5.md` / `stage6.md` / `stage7.md`）**按需回查**，
不必通读——它们的结论已经落进 `contracts/judges/` 与各层实现。

---

## 下一步：F5（F6/F7 已完成）

### 顺序：**按验证价值挑，不按管线顺序挑**

施工方案写的是 stage2→4→5→6→7→(0/1)，那是**管线**顺序。实测下来体量极不均匀
——**stage2 一层就占 prompt 的 57%（20 804 / 36 709 字）**，
而且**它是当时唯一一层没有判据表的**：原型把判据塞在 `Stage2/check_blueprints.py`
（**2077 行**）的一个 `check_one()` 里，全是 `rep.fail('锚点唯一性', …)` 这种中文首参，
**一个 id 都没有**，严重度只由调用的方法名承载。移植它 = 先从零建一张表。

> ⚠ **本节此前记的「判据的 62%（91 / 147）」两个数都不能用**（F5-0 逐项数过）：
> `91` 是**「(中文首参, 严重度) 对」**的数，**不是判据数**——其中 6 次是跨严重度同名
> （同一条判定的两三种结局），并回去只有 85 个不同的中文首参；分母 `147` 是当时对原型的**估数**。
> F5-0 从零建表的结果是 **75 条**（41 HARD / 15 INFO / 17 metric / 2 unver），
> 账在 `docs/schema/stage2.md` §5.0。本仓已注册 **159** 条（七层）⇒ stage2 占 **75 / 234 = 32%**。
> **prompt 那个 57% 是准的**：`20804` = 3 个 SYS（8682 + 7448 + 2008）+ 3 个 HINT
> （按 `json.dumps(ensure_ascii=False, indent=2)` 共 2666），一个不多一个不少。

⇒ 已裁决**先用最便宜的一层把「加一层」那张清单走通一遍**（同 C 期选 stage3 当第一条纵切的判准）。
剩下的顺序与实测体量：

| 顺位 | 层 | 原型行数 / prompt 字 / 判据 | 备注 |
|---|---|---|---|
| **F5** | **stage2** | **5674 行 / 4 文件** / **20804** / **75（F5-0 建表）** | 最难。⚠ 体量此前记的 `3597 + 1110` 是错的：`1110` 是 `stage2_common.py`，**它已经在 3597 里被数过一次**，而 `check_blueprints.py` 的 **2077 行整个不在这个数里**——判据一条不落全在那个文件。真实体量 = `n2_global 912` + `n2_blueprint 1575` + `stage2_common 1110` + `check_blueprints 2077`。⚠ `novel/core/spans.py` 已有 `content_index` / `locate_spans` / **`lcs`**（F4 带进来的），还缺 `recover` 那条臂与 `HINGE_LCS_MIN`（F5-2） |
| **F6 ✅** | stage1 | 828 / 0 / 13（无 id） | 已沿新边界只保留确定性分章；契约收敛为 8 条有 id 判据 |
| **F7 ✅** | stage0 | **0** / 0 / 0 | 已从零实现摄入、剔除、人工编辑/批准、轮次归档与 13 条有 id 判据 |

### ⭐ F5 从这里接 —— `F5-0` / `F5-2` 已落，`F5-1` 与 `F5-3` **被另一个会话堵着**

> ## ⚠⚠ 接手第一件事：确认 `contracts/invariants.py` 没人在写
>
> **2026-08-12 19:25 实况：另一个会话正在同一工作树里加 stage0 的摄入/上传路**
> ——已改 `contracts/invariants.py` · `contracts/schema/{__init__,api,stage0}.py` ·
> `novel/cli/main.py` · `novel/api/app.py` · `novel/stages/stage0/removal.py` ·
> `tests/test_layering.py`，并新建了 `novel/orchestrator/uploads.py` ·
> `novel/views/ingest.py` · `novel/api/routes/ingest.py`（9 个新端点）。
>
> **树上那 4 红 + 5 错全是它的**（`test_api_http` 的「九个端点」面 · `test_api_enums`
> 的 `len(actions) == 69` · `test_api_discipline` 的 409）。⇒ **别去修它们**。
>
> **`F5-1` 与 `F5-3` 都要改 `contracts/invariants.py`，与它硬冲，必须串行**
> （同 F5-0 那次的处置）。`F5-2` 之所以能先落，是因为它只碰
> `novel/core/spans.py` 与 `tests/test_core.py`，与那一批不相交。

**F5-0（`e5dd5d5`）交的是落点审计，一行 `.py` 都没改：**

- `docs/schema/stage2.md` **+398 行**：§五 判据清单（**唯一落点**，两个节点共用一张表）
  —— **75 条** = 41 HARD / 15 INFO / 17 metric / 2 unver，`repair` 全表 `NONE`，
  owner `stage2` 56 / `None` 19。含 §5.0 计数账 · §5.1 表 · §5.2 不进表的 5 条 ·
  §5.3 退役而不删 9 条 · §5.4 `fix` 那一列 · §5.5 五条要连基线一起读的 · §5.6 判据↔需求。
  另补 §一 的 `ROLE_SPLIT_SYS` **第三付费臂**、§3.2/§3.3 的**三个 verbatim 记账键**。
- `docs/REQUIREMENTS.md` **+24 行**：补 17 行 · 拆 `R-2.11` · `R-3.6` 改判 `载体`。

**F5-1~F5-5 的路线图**（每一步的落点已在审计里写死）：

| 提交 | 内容 |
|---|---|
| `F5-1` | 🚧 **被堵** 契约层四格：`Rule.fix` + 注册期不变量 · 四个 flag 登记 · 三个 verbatim 记账键 · `corpus.py::ADDED`。⚠ 审计漏了三处，**不补会当场炸**：① `--chapters` 必须进 `NODES['stage2.blueprint'].flags`（§5.1 有 **19 条**判据的 `fix` 指着 `blueprint --chapters`，而它今天只挂在 `stage5.write` 上）② `--drop` / `--replay-cache` **都是带值 flag**（原型 `--drop <ids>` 配必填 `--reason`、`--replay-cache <KEY>` 且必须配 `--chapters`）⇒ 还要进 `VALUED_FLAGS` ③ `--reason` **不单独立 flag**，折进 `--drop` 的值形态，照 `--wontfix` 那个已有先例（本仓没有「一个 flag 要求另一个 flag」的机制）。⚠ `tests/test_api_enums.py` 那个 `len(actions)` 要 **+4** |
| `F5-2` | ✅ **已落**（`ffbc7c7`）`novel/core/spans.py` 的 `recover` 臂与 `HINGE_LCS_MIN` |
| `F5-3` | 🚧 **被堵** 本层的 prompt 模块 —— 三条臂逐字节搬。groundwork 已探明，见下 |
| `F5-4` | 本层的判据行文件 —— 75 条，**故意还不被 import**（照 F4-4） |
| `F5-5` | 模块 + 接线一次走完（照 F4-6）。⚠ **stage2 是最后一个未移植层** ⇒ 它一进 `PORTED_STAGES`，`STAGES − PORTED_STAGES` 就是空集，`NodeNotPorted` 在生产路径上再也触发不了。用户裁决（2026-08-12）：**退役它**（501 仍由 `RunModeNotImplemented` 可达，HTTP 状态面不变） |

#### `F5-3` 的 groundwork（**已实测探明，别再推一遍**）

**① 取值器不用改一个字。** `tests/test_stage5_prompts.py` 那个受限求值器
（`_Proto` / `_prototype` / `_want`）**原样就能算出 stage2 全部 7 个常量**，
文件表换成 `Stage2/{stage2_common,n2_global,n2_blueprint}.py` 即可。实测渲染字数：

| 常量 | 字数 | 出处 |
|---|---|---|
| `RULES_V2` | **938** | `stage2_common.py:96`。`GLOBAL_SYS` / `BLUEPRINT_SYS` 共用前缀 ⇒ **单独一格搬、单独钉一次**（照 stage5 逐段钉共用片段的理由：合起来那条对不上时，逐段这一条才说得出**是哪一段**漂了） |
| `GLOBAL_SYS` | **8682** | `n2_global.py:43` = `RULES_V2 + fill(…)` |
| `BLUEPRINT_SYS` | **7448** | `n2_blueprint.py:89` = `RULES_V2 + fill(…)` |
| `ROLE_SPLIT_SYS` | **2008** | `n2_global.py:435` = `fill(…)`，**无** `RULES_V2` 前缀 |
| `GLOBAL_HINT` / `BLUEPRINT_HINT` / `ROLE_SPLIT_HINT` | 1028 / 531 / 65 | `:179` / `:224` / `:454`（按 `ensure_ascii=False`） |

⇒ `20804` 那个数 = 三个 SYS（`8682+7448+2008`）+ 三个 HINT 按 `indent=2`（`2666`）。

**② 那 10 个插值点从哪儿来 —— 6 个本仓已有、2 个要新立、1 个是共用片段。**

| 占位符 | 原型来源 | 本仓落点 |
|---|---|---|
| `$LAYERS`（blueprint） | `TIME_LAYERS_ENUM` | ✅ `TIME_LAYERS` 逐值相同 |
| `$KINDS`（blueprint） | `HINGE_KINDS_ENUM` | ✅ `HINGE_KINDS` 逐值相同 |
| `$RULE_KINDS`（global） | `WORLD_RULE_KINDS` | ✅ `WORLD_RULE_KINDS` 逐值相同 |
| `$NAME_DISTINCT`（global） | `NAME_DISTINCT` | ✅ `NAME_DISTINCTIVENESS`（**名字不同、取值相同**） |
| `$CONTEXTS`（global） | `ADDRESS_CONTEXTS` | ✅ `ADDRESS_CONTEXT`（同上） |
| `$BY_SPECIAL`（global） | `ADDRESS_BY_SPECIAL = ['narration','public']` | ✅ 由 `ADDRESS_BY` **减掉 `char_id`** 派生，别手抄第二份 |
| `$PRONOUNS`（global） | `BARE_PRONOUNS = ['我','你','他','她','它','咱']` | ⚠ **本仓没有，要新立在 `contracts/invariants.py`**——它的第二个消费方是判据 `address_bare_pronoun` / `address_has_pronoun` / `present_names_pronoun`（F5-4）。**这一格就是 F5-3 被 `contracts/invariants.py` 堵住的原因** |
| `$CHANGE_TYPES`（global） | `CHANGE_TYPES_ENUM = 'stable\|development\|revelation\|deterioration\|reversal\|…'` | ⚠ **本仓没有**。⚠⚠ **末尾那个 `…` 是语义的一部分**（`stage2.md` §1.6 明写）⇒ 它是**开放枚举，不许做成 `Literal`**，只能是一个字符串常量 |
| `$ROLE_FATE`（global **与** role_split 都用） | `ROLE_FATE_RULE`（约 1.6k 字，压着六条实测反例） | **一个落点两处引用**——两条臂读同一份，各抄一份就是下一次漂移的源头 |

**③ `_legacy` 的同名并存比 F4-5 那次更深。** `_legacy/n2_semantic.py` 里
**同时**有 `BLUEPRINT_SYS`（`:252`）**与** `BLUEPRINT_HINT`（`:274`），
与现役 `n2_blueprint.py` 的 `:89` / `:224` **同名**（F4-5 那次是**改名**：
`N5_SYS` → `DEEP_SYS`，这次是同名并存）。而取值器那张文件表的语义是
**「先出现的赢」**——把 `_legacy/` 放进去就会静默拿到旧世代（它拼的是 `RULES` v1
不是 `RULES_V2`）。⇒ 文件表**只列现役三份**，并配一条断言
「拿到的那一份带 `RULES_V2` 前缀」把两代分开。

**④ 唯一一处不许逐字搬 —— `R-3.8b`。** `BLUEPRINT_SYS` 通篇没有「第一拍要从章首起」，
**还明写着「两条之间允许有空隙」**，而三本 **5 处漏抓全在章首、0 在章尾**。
⇒ F5-3 要在它里面**插一句**，逐字节测试写成「**除这一处外与原型逐字相同**」
并**同时断言原型那一份里没有这句话**（照 `test_the_shallow_arm_still_has_no_layer_gloss`
那两个「有意留着的洞」的形态），同批把 `R-3.8b` 那行改判 `结构`
——它是三行待改判里**唯一**落点在 prompt 上的，`prompts.py` 一建出来就能写死。
⚠ 散文里**不许出现那七个记号字面**（「谁在查」是子串匹配，F5-0 已被绊过一次）。

**⑤ 求值器该不该抽成共用一份。** 它今天只长在 `tests/test_stage5_prompts.py` 里，
而 F5-3 是第二个消费方。照 `tests/legacy_names.py` 的先例
（那份的 docstring 记着「各抄一份的分家表现是**贵贱对账假绿**」）
⇒ **抽进一个 `tests/` 下的共用模块**，别复制第二份 70 行的 AST 求值器。

#### ⚠ stage2 自己欠着的（登记，**不是**验过了）

| 欠账 | 说明 |
|---|---|
| **41 条 HARD 里盘上只有 4 条有真反例** | `address_bare_pronoun`（1 本）· `role_leaks_fate`（2 本）· `hedge_words`（2 本）· `hinge_in_hole`（1 本）。**其余 37 条盘上 0 命中——那不是「干净」，是「那种失效模式在这三本上一次都没发生过」。** 用户裁决：每条各配一份会让它红的样本（F5-5）。⚠ 前提是**判据吃 `dict` 不吃模型**——跑在已过 Pydantic 的对象上，「必填齐全」那类在结构上构造不出反例 |
| **`role_fate_unverifiable` 在最该说话的两本上反而不说** | 它挂在 `if not leaky` 那一支上，`role_leaks_fate` 命中的两本**根本走不到它**。实测词表在头牌上命中 **0/17 而人读真违规 6/17**（`char_009.role` 写「…**后成为**李宝珠的下属」，差一个字避开了 `后被`）⇒ 本仓改成**无条件产出**（同 F4-6 对 `name_surface_survey` 的处置） |
| **`world_rule_names_person` 这道闸门在三本上什么也没拦住** | 含人名的规则里，最晚叫法的 `from_chapter` 是 ch1 的有两本 ⇒ **等于全程下发**。判据必须**报出自己的基线**，否则「没报违规」与「闸门一条都没拦住」在报告上同形 |
| **`secret_setup_order` 三本全绿而 20/21 条 secret 没有埋点** | 「冷揭示」（据实留空，合法）与「被裁空」（产地把揭示章号抄进埋点、落盘前被裁）在**字段值上完全同形** ⇒ 它断言的是 `_provenance.setup_chapters_pruned` 那个**计数**，不是字段值。⚠ 而那个键**盘上 3/3 本一份都没有** ⇒ 今天只能落 `unver`，不能发绿灯 |
| **`present_ungrounded` 不是幻觉检测器** | 三本 128 对里只报 1 条，逐条回原文核过，**幻觉在场者 0 例**。它实际抓的是 ① `address[]` 漏收原文在用的叫法 ② **后见之明标注**（那是一条剧透旁路）⇒ 判 INFO 不判 HARD，且名字表**不许复用 `present_broad` 那份**（带 `len>=2` 过滤，搬过来会造假阳） |
| **三条「查不了」已结案** | `role` 藏中段才揭示的事实（词表只抓转抄式）· `event` 面的跨章引用（只能从输入侧解，现已是默认且无旋钮）· `place` 不能当相等判断的键（自由文本、粒度不稳）。见 `docs/schema/stage2.md` §六 |

---

**F4 落地七个提交**（`git log --oneline`，每条的病因与修法都在 commit body 里）：
`F4-0` 落点审计 · `F4-1` 契约层（`Rule.directive` / `REPAIR_BUCKETS` / 三条裁决）·
`F4-2` `novel/core/` 数值词法与保真回写 · `F4-3` `measure.py` 确定性核心 ·
`F4-4` 47 条判据行 · `F4-5` 两条臂的 system · **`F4-6` 四个模块 + 七处接线**。

⚠ **原型那两条 system 叫 `DEEP_SYS` / `SHALLOW_SYS`**（`Stage5/n5_write.py:50` / `n6_write.py:55`），
**不叫 `N5_SYS` / `N6_SYS`**——后两个只存在于 `_legacy/`，是**已废弃的旧世代**。
本文件此前记的是那两个失效的名字（LESSONS 第 11 条那一类：指向改名过的符号，而不会报错）。

#### stage5 带进来的四个「第一次」，移植下一层之前值得知道

| 第一次 | 它逼出了什么 |
|---|---|
| **第一个 `repair != 'NONE'` 的层** | `Rule.directive` 与 `bool(directive) == actionable` 的双向注册期不变量（F4-1） |
| **第一个逐章产物的层** | 完成度与来源账都按**章数**；`draft.md` **不许进 `produces`**（它在只写了一章时也存在） |
| **第一个 `ask_text` 调用点** | 逐章响应是**纯文本** ⇒ stage4 那条「响应侧 `extra='forbid'` 当场挡回」的出路**在本层默认路径上不存在**，全部把关都在落盘闸门这一侧 |
| **第一个有「臂」的节点** | `RunContext.mode`（`--mode` 此前解析出来就被丢掉了）· `--chapters` 进 `VALUED_FLAGS` |

#### 本轮的三条用户裁决（2026-08-12）与它们的落点

| # | 决定 | 落点 |
|---|---|---|
| 1 | **`--chapters` 进 `VALUED_FLAGS`** —— 「逐章重构深度是人的决策」（`R-7.2`）不落任何产物，只活在这个实参里；不给它那一行就是假话 | `invariants.py::VALUED_FLAGS` + `NODES['stage5.write'].flags`（**不进 `FREE_ENTRIES` / `WRITE_EFFECTS`**，理由同 `--pick`） |
| 2 | **7 条 HARD 各配一份会让它红的样本** —— 本层不再往「36 条 HARD 盘上 0 反例」那个数上加 | `tests/test_stage5.py` 一族 |
| 3 | **一次做到 F4 收官** —— 判据一进 `REGISTRY`，四张表立刻索要实现 ⇒ `checks`/`write`/`review`/接线只能同一次提交 | `F4-6` |

#### 三条实测结论

- **归一幂等 14/14**（这一本）：产物里存的是归一**之后**的字节 ⇒ 再归一必须一个字不动。
  `--recheck` 在三本上一个字节都没改（`punct/quotes/paras` 全 0）。
- **本层徽章结构上永远不可能是 `pass`**（5 条 `unver`，`must_hold_unver` 每本都产）
  ⇒ 实跑就是 `amber` + 退出码 0。**与 stage4 相反**，那一层没有一条 `unver`。
- **`name_surface_survey` 已改成无条件产出**：原型 `if by_surface or collide:` 让
  「全书 0 个自造称谓」这个**读数**永远说不出来，而「查过、干净」与「根本没在查」同形。

#### ⚠ stage5 自己欠着的（登记，不是验过了）

| 欠账 | 说明 |
|---|---|
| **付费两条路一次都没跑** | `run_write` / `run_revise` 只走过 `--dry-run`（渲染出的 system 与原型逐字节相同）。**真发第一次要先说一声。** ⚠ 它是**全链第一个 `ask_text` 调用点**，契约 §七 那条降级阶梯（正文写进 `reasoning_content`、`content` 空串）**在真机上一次都没验过** |
| **`secret_keywords` 两侧口径不同** | 它把 fact 里的中文数字剥掉（「一直」→「直」）而成稿这一侧没剥 ⇒ 带数字词的片段**永远匹配不上**。这是「21 条 secret 里 19 条连揭示章都命中不了」的成因之一。**本轮不改**：改它会动全部三本的基线，而手上没有 ground truth（落点 `novel/stages/stage5/checks.py::fidelity_findings` ④ 的注释） |
| **`--revise` 的回归护栏没有真机样本** | `checks.trim_regressions` 的六条只在合成输入上验过；`.rejected.json` 那条归档路盘上仍是零样本 |
| **`--tag` / `--batch` 未移植** | 原型的 A/B 实验设施。`docs/schema/stage5.md` 事实①下面已标注——**读到它们时当不存在，别照着补一个本仓没有的入口** |

### ✅ F6/F7 收工验收

- `tests/test_ingest_fixtures.py` 的 18 个 `importorskip` 已全部换成真实断言；建模块与补断言同轮完成。
- stage0/stage1 共用 `novel/core/ingest.py` 的一次扫描词法器；首次摄入可不带 `sid`，
  写盘后由原文身份派生 `sid`，随后自动复检。
- `Test_data` 的 8 本真实小说全部完成摄入、批准与分章：章节数为
  8 / 14 / 8 / 9 / 19 / 9 / 15 / 10，其中 2 本命中并剔除番外。
- 与原型有重叠产物的 3 本书，`main_text.json` / `hook_copy.json` 共 6 份逐字节相同。
- stage0 的人工语义正确性不可由代码替人裁决，因此批准后的报告仍是 `amber`、退出码 0；
  这是显式 `unver`，不是运行失败。

---

## 仍欠着的（登记，**不是**验过了）

### 跨层

| 欠账 | 状态 |
|---|---|
| **付费路一次都没跑** | 已移植的四层（stage4/5/6/7）都是：合成样本走通了闸门与提升路，但 `DeepSeekTransport` **从没发过一次请求**。已用 `--dry-run` 验过渲染出的 system 与原型逐字节相同（stage4 2192 字 / stage6 1075 字 / stage7 1658 字）。<br>⚠ stage4 那次是**全链最贵的一次请求**（全书一次调用，`max_tokens=96000`，user 实测 22773 字）。**真发第一次要先说一声**——那是本仓第一次真花钱 |
| **36 条 HARD 盘上 0 反例样本** | stage4 16 · stage6 5 · stage7 15。已补的合成样本只走通了 3 条（`style_words` / `rn_ambiguous` / `context_link`）。跨层规矩「每新加一条 HARD 就要有会让它红的样本」在这三层仍未兑现<br>⚠ **stage5 那 7 条已在 F4-6 配齐**（`tests/test_stage5.py`），所以这个数没有变成 43 |
| **`_provenance.code` 那条没修** | 11 份产物 `code` 全是同一个短 sha，而同一批的 `N9_SYS` 取过 **3 个**值。⚠ **处置只定一次，别按层分别裁**——`provenance()` 在共享层，改 `git describe --always --dirty` 一改就是八层。目前只**钉在测试里** |
| ~~**两套新鲜度机制并存**~~ | ✅ F4-6 落地：`ReviewSources.writer_context` 是 `store.freshness()` 的**投影**，不是第二次计算（`novel/stages/stage5/review.py::_context_source`） |
| **`to_human_edit()` 只长在 `LlmProvenance` 上** | stage6/stage7 的 `--apply-edit` 够不着它（`--approve` 之后上一份来源账可能已是 `FreeProvenance`）。⇒ 真要用它的那一刻，连同一份会红的样本一起挪到基类 |
| **重试与并发那三个数是拍的，不是量的** | 手上没有真实 429 分布。`Q-10` 已把「重试退避调参」登记为「现在不建」；调它之前先拿到真数据 |
| **4 行 `结构` 仍是旧形态** | 29 行 `结构` 里 `R-4.2` / `R-4.3` / `R-4.3b` / `R-4.10` 四行写的是裸符号名，`tests/test_coverage.py::test_structural_rows_that_name_a_repo_path_actually_point_at_it` 对它们**不可见**（现扫得到 35 个落点）。改成 `路径::名字`，**改一行那条守卫就多查一行** |

### 按层

| 层 | 欠账 |
|---|---|
| **stage4** | `stage4.plan --apply-edit` 登记在案而**没接线**（显式终态说清「有，但还没实现」；接线时 `docs/schema/_common.md` §5.3 那份 `human_provenance` 是教科书，**连注释一起搬**）<br>`--identity` 在 `/api/enums` 上的 `writes` 串说的是**错的文件名**（说 `plan_chapters.json`，实际写 `writer_context.identity.json`）。⚠ 方向是安全的（它确实写盘），已钉在测试里，**别顺手去改 `WRITE_EFFECTS`**——那张表按 flag 索引，而这个 flag 在三层行为各不相同 |
| **stage6** | `stage6.hook --apply-edit` 同样**没接线**<br>`hook_para_sep` 对单段文案给的是**错的诊断**：detail 写着「归一化应该已经修掉它了」，而单段文本没有分隔符可归一。判定本身不冤，只是那句话在这一类输入上是假的 |
| **stage5** | 付费两条路（`run_write` / `run_revise`）**一次都没跑**，只走过 `--dry-run`。⚠ 它是全链第一个 `ask_text` 调用点，契约 §七 那条降级阶梯（正文写进 `reasoning_content`、`content` 返回空串）**在真机上一次都没验过**<br>`novel/core/facts.py::secret_keywords` 把 fact 里的中文数字剥掉而成稿这一侧没剥 ⇒ 带数字词的片段**永远匹配不上**（「19/21 条 secret 连揭示章都命中不了」的成因之一）。**本轮不改**：改它会动全部三本的基线，而手上没有 ground truth<br>`checks.trim_regressions` 的六条护栏只在合成输入上验过；`chNN.rejected.json` 那条归档路盘上仍是零样本 |
| **stage7** | 盘上那三份 `name_map_edit.json` **没有被端到端跑过**（它们是 `character_patches` 那条路，沙箱里没有构造过一次完整重放） |
| **F5 要连带补的** | `novel/core/spans.py` 缺 `recover` 那条臂（stage2 落盘路才用得上，连同 `HINGE_LCS_MIN` 一起进来；⚠ `lcs` 本身**已在 F4 进来了**，stage5 的 `must_appear_missing` 是它的第一个消费方）<br>`--drop` 与 `--replay-cache` 两个 0 成本 flag **从没被任何守卫查过**（它们在原型的 `FREE_FLAGS` 里而抓包的 `actions` 里没有）。⚠ 不全的方向是安全的（未登记 ⇒ 按付费算），但那是「界面把免费动作画成橙色」 |

### 界面与前端

| 欠账 | 状态 |
|---|---|
| **`R-11.10` 只验了半格** | 「第二个请求 409」只验到状态码映射表；没有 `POST /api/jobs`（用户裁决：不让浏览器能花钱）⇒ **没有第二个请求可以撞**。**别把这一格读成验过了。**<br>三处索引：`docs/REQUIREMENTS.md` 第五节那个框 · `docs/schema/_api.md` §七 · `tests/test_api_discipline.py` 那条名字很长的测试（它同时钉着「POST 面只有 `preview`」，哪天多了一个会逼人回来补） |
| **check 路由改名的账** | `stage5_review` → `stage5`。八个 kind 现在与 `invariants.STAGES` 逐值相等、由它派生（落点 `novel/api/deps.py::checked_kind`），`docs/design_brief/04_delivery.md` 那一格已改并带标注，**`web/` 里的引用还欠着** |
| **`novel/views/` 下三屏没做** | stage4 / stage6 / stage7 各自那一屏与那条 API 路由（现在只有 `stage3.py`）。⚠ 做的时候：stage4 的**三个 `source_chars` 口径别混** · stage6 的 `candidates[].chars` 现算**必须留在 Python** · stage7 的 `book_txt.chars` 是全链第三个 `chars` 口径，**别拿它和另外两个比** |
| **`web/` 一个字没动**（已裁决） | 欠着四笔：71 处写死的原型作业名（`n4_plan` / `n9_rename` / `stage5_review` …）· S-13 屏按 `rules[]` 重画 · 两份 check mock 重生成 · `web/src/constants/actions.ts` 那张 `WRITE_EFFECTS` 第二落点可删（`/api/enums` 现在发 `writes` / `writes_disk` 了）<br>⚠⚠ **`docs/design_brief/05_samples/` 里的 json 永远不许用新后端的输出覆盖**——它是 `tests/test_api_enums.py` 与 `tests/test_invariants.py` 全部对账的**证据基线**，覆盖之后那一族测试就变成拿 `invariants` 跟它自己比。新形状的样例要另起路径 |
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
> 现状：`contracts/judges/` 下 **138 条判据指向 85 个不同编号**，覆盖 `R-3`~`R-10`。

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
