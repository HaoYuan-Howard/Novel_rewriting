# 小说重构系统 — 统一契约、产品规格与证据基线（冻结）

> 本文件不是活动开发指南。前后端开发结束后，它统一保留产品与界面规格、既有判据编号、
> 双向覆盖率、修复路由、schema 追踪、证据基线和历史验收结论；不要在这里登记新任务或进度。
> 当前未完成工作统一见 `docs/REQUIREMENTS.md`。

本文件冻结前后端开发结束时的契约、产品规格、判据与编号口径。正文中的 `待建` / `无守卫` 只用于
保留追踪关系；它们的现行处置以 `docs/REQUIREMENTS.md` 为准。

**本仓没有自动放行门。** 语义审判的合并决议恒是带证据的 `UNVER`：两名互不可见的 judge
各自给 verdict、理由与逐字证据，合并只做证据并集与人读理由，不产生任何自动 `PASS`/`FAIL`。
唯一的收口是 actor / reason / 当前字节完整 SHA-256 绑定的 `human_verification`。
这不是「还没做」——那套要求双人独立标注、第三方仲裁与三轮 A/B 校准的 evaluator profile 装置
已于 2026-08-18 整批删除（§十）。agent 判断同样不是 ground truth；证据不足时保持 `UNVER`。

---

## 这份文档怎么读

### 它是自足的

**不引用任何会被归档的东西**：不引原型仓的 `CONTRACTS.md` / `PROTOTYPE_FINDINGS.md`、
不引任何施工期编号、不引任何交接件。需要的事实一律**就地写出来，包括实测数字**。

理由：这份文档要活得比它的来源久。一条写着「详见某文档某节」的需求，在那份文档被归档、
改版或删除之后，就变成了一条**读起来完整、实际上空心**的需求——而没有任何东西会为此报错。

代价是它会重复一些别处也有的话。这是有意的。

⚠ **唯一允许的外部引用是【本仓内】的落点**（`contracts/...` / `novel/...` / `tests/...` /
`docs/schema/...`），因为它们由 `tests/test_docs_references.py` 逐条验着存在——
**指向一个不存在的文件或编号会当场红**。指向仓外的东西没有这条保障。

### 核心机制：每条需求带「谁在查」

需求表的最后一列是 **「谁在查」**。取值面的**唯一落点**是
`contracts/invariants.py` 的 `WHO_CHECKS`（8 个记号），外加三个不进那个元组的：

| 取值 | 含义 | 准入条件 |
|---|---|---|
| **`HARD`** | 有确定性代码在查，违反即阻断（不落盘 / 非零退出 / 显式终态） | 必须有一条注册判据 |
| **`INFO`** | 有确定性代码在查，违反只报不拦 | 同上 |
| **`闸门`** | 落盘之前跑的校验，不通过就不覆盖现役产物 | 同上 |
| **`度量`** | **无条件产出的读数，不是结论**——它永远不「命中」 | 同上（`sev=metric`，且两者必须成对） |
| **`载体`** | **类型系统在守**：`Literal` / `extra='forbid'` 收走了取值面，构造期就拦住 | **必须点名是哪个字段** |
| **`结构`** | **代码结构使然，要违反它必须先改代码** | **必须写出代码落点**，形态 `路径::名字`。说不出是哪段代码在守，就不算守 |
| **`不适用`** | 按定义无从「查」（一份清单 / 一条负空间声明） | — |
| **见 `R-0.11`** | **职责转授**（行首形态，不用记号）。「转授了」与「没人查」必须分得开 | 目标编号必须存在 |
| **`人`** | 按设计就由人判，没有也不该有确定性判据 | — |
| **`待建`** | 已经决定要做、今天还没有落点 | **不许在实现之后还留着** |
| **`无守卫 → B-xx`** | **这条要求只写在 prompt 或文档里，没有任何代码在查它** | 目标 `B-xx` 必须存在 |

最后一个是这份文档存在的主要理由。

> **判据那一侧另有一份 `WHO_CHECKS_RULE`（5 个），比这里少四个**——
> 一条判据的存在本身就否定了「载体在守 / 结构在守 / 转授 / 不适用」。
>
> ⚠ **写任何一格时，散文里不许出现「闸门 / 度量 / 载体 / 结构 / 不适用 / HARD / INFO」
> 这几个字面，除非那正是要声明的记号。** `contracts/requirements.py::_MARKER_TOKENS`
> 是**子串包含**，散文里提到这些词会被误认成额外声明的记号。
>
> ⚠ **每个取值都配了一个会让它红的样本**（`tests/test_coverage.py`）：
> 格子里写着字而解析器一个记号也没认出来（`test_every_r_item_declares_something`）·
> 加了一个没人用的取值 · `结构` / `载体` 说不出落点 · 转授指向不存在的编号 ·
> 标了「载体/结构/转授/不适用」却有判据指着它。
> **读不懂一个记号时解析器不会报错**，它只是把那一行读成「什么也没声明」——
> 而那正是这一列存在的理由要防的事。

> **一条只写在 prompt 里的约束，它的遵守率是没人在测的。**
> 而它读起来与有守卫的那些**一模一样**——同样出现在【硬约束】段里、同样被别处当成
> 「结构上不可能」引用。要分辨「在守」与「只是在写」，唯一的办法是去问**哪一段代码在查它**。
>
> 已实测的代价：写作节点的三条硬约束（只并相邻拍 / 只并同层拍 / 按写作顺序排）
> 在盘上**各违反一次，全链没有一层出声**。

反过来的同一个病也要防：**一条只写在关卡里、产地从没被告知过的要求**。
两者的共同点是**约束与守卫不在同一个地方**，而后者更亏——模型本可以第一次就做对，
现在要为同一件事付第二次费。

⚠ 「把要求写进 prompt」本身**没有遵守率保证**——实测过 prompt 已明说「空隙只能在中间，
两头不许留」之后，章首漏抓仍然发生。prompt 不能替代守卫，只能与守卫并存；
一条只写在 prompt 里的约束，遵守率没有任何东西在测——那正是 `Q-6` 要防的另一半。

⇒ **验收本文档的方法是双向的**：
① 每条 `无守卫` 都要有处置（补判据 / 收走自由度 / 明知不查但写明）；
② 每条现役守卫都要能追到本文档里的一条需求（见最后一节的反向覆盖率）。

### 编号

- **`R-0` ~ `R-12`** — 功能需求，**按节点职责编排，不按 stage 号码机械映射**；同一职责节可以覆盖
  多个独立节点（stage2 两个节点 = `R-2`/`R-3`；stage4 三个节点中 design/plan 共属 `R-5`、
  context 属 `R-6`；stage5 两相 = `R-7`/`R-8`）。
  **1:1 重建**：形态可以变，能力与守卫不许缩水
- **`R-13` ~ `R-14`** — **相对原型新增的两项能力**（批量队列 · 断点续跑），用户裁决
- **`Q-1` ~ `Q-12`** — 工程约束，跨阶段成立
- **`B-01` ~ `B-23`** — 无守卫清单 = **关卡层的施工清单**

> #### 两条改编号时必须照办的惯例（跨层，每一层移植都会撞上）
>
> **① 一格里同时写 `载体`/`结构` 与 `HARD` 的行，一律【拆行】，不放宽守卫。**
> 载体/结构那半留原号，判据那半**加字母后缀**（`R-3.8b` / `R-7.3b` / `R-9.3b` / `R-5.3b` 都是它）。
> ⚠ **后缀是惯例不是算法**：撞号时顺延（`R-10.3` 拆出来的是 `R-10.3c`/`R-10.3d`，
> 因为 `R-10.3b` 早被占着）。**逐节查过再拆**，别照着上一次的结论套。
> ⚠ 拆出来的新号**在真拆的那一刻才写进表里**——`tests/test_docs_references.py`
> 会（正确地）把一个还不存在的编号判成悬空引用。
> **不放宽守卫的理由**：这一列存在的全部意义就是让「我们真的知道」与「我们只是写着」
> 读起来不一样；放宽之后一行同时声称两种守卫也不再有人出声，那等于把这一列改回散文。
> ⇒ **现存欠账只剩 `R-14.2` 一条**（编排层，属它自己那一期）。
>
> **② 判据的落点写 `Q-x` 的，要在 R 侧【补一条真需求行】收下它，不放宽 `contracts/rules.py` 的正则。**
> **`Q` 是问题与裁决的记录，不是需求**：指向 `Q` 的判据不进任何一节的正向覆盖率分母，
> 放宽正则等于给覆盖率开一个不出声的口子。
> ⇒ 逐条判：多数在**本层的 R 节补一行**收下它，`Q-x` 写进 `Rule.note` 当理由；
> 确实由代码结构保证的那几条改判 `结构` 并点名代码落点。

---

## 一、命名体系与章的唯一键

〔产品叙述（这是什么、部署形态、成本模型、处理链图、最高风险）的现役落点是仓根
`README.md`，契约不再保存第二份。本节只留**跨层架构约束**：作业单元怎么命名、章怎么编号。
它们是 `contracts/invariants.py::NODES` 与 `STAGE_SECTIONS` 的直接依据，不是产品规格。〕

**章的唯一键一律是阅读序整数 `chapter_ordinal`。输出章号恒等于源章号**——
不合并、不重排、不删、不拆分，四条是同一个恒等式的分解。

> ### 命名体系：**两层**（`stage` 与 `node` 是两个正交的轴）
>
> `stage` 是产物目录 + schema 层 + 需求节的归属；`node` 是可跑的作业单元。
> **两者不是 1:1**——stage2 有两个节点；stage4 有两个付费相与一个 0 成本上下文节点；
> stage5 有写作与验收两相；stage0 有三个人参与的动作。
> 原型的 `n1`~`n9` 是**节点轴用 stage 编号命名**的产物，于是它必然跳号、重号、缺号，
> 而且 label 是手写的会说谎（`n1_partition` 的 label 写着「摄入+分章」，而摄入早已搬去 stage0）。
> **`n1..n9` 整套退役。** 五条规则：
>
> 1. **`stage0`~`stage7` 只做产物层**（目录 / schema / Pydantic 模块 / `Rule.stage` /
>    `PORTED_STAGES` / 校验报告路由），**永不用作作业 id**。
> 2. **作业 id = `<stage>.<动词>`**，唯一落点 `contracts/invariants.py` 的 `NODES`。
>    用动词不用新编号：**编号会过期，动词不会**——`n` 那套坏掉正是因为把序数编进了标识符。
>    label 从 `NODES` 派生，**不再手写**。
> 3. **Stage5 只有一条 canonical 闭卷写作路径**：`stage5.write` 默认按全书计划补齐所有缺章，
>    已有章不重复付费；`--chapters <章号列表>` 是定向重写的唯一范围参数。所有调用共享一个
>    书级互斥键、单池最多 32 并发，正式章唯一落 `stage5/chNN.json`。旧
>    `stage5/deep|shallow/` 目录与旧作业记录只作历史证据，现役读模型报
>    `legacy_incompatible`，不在原地迁移、选臂或触发付费重建。
> 4. **没有 `check` 节点**，复检一律是 `--check-only` flag；作业 kind = `(node, flags)` 二元组。
>    原型两头都有（`check_partition` 是独立脚本、`n3_style --check-only` 是 flag），
>    正是 `Q-3` 点名的「三个节点行为不一致」。
> 5. <a id="enums-superset"></a>**`/api/enums` 从 `invariants` 整份生成，必须是抓包那份 `enums.json` 的【超集】**——
>    `tests/test_invariants.py::test_enum_matches_live_backend` **正在逐值比对**其中 6 个，
>    前端也是运行时取（不许硬编码）。`job_kinds` / `actions` 的**形状不变，只有 `kind` 的取值变**。
>
> ⚠ **R 节的编排轴因此是【节点职责】不是 stage**（`R-2`/`R-3` 是 stage2 的两个节点；
> stage4 的 design/plan 两节点共用 `R-5`，context 节点用 `R-6`；`R-7`/`R-8` 是 stage5 的两相）。
> 正向覆盖率**不许**写成 `'R-' + stage.removeprefix('stage')`——那个式子只在 stage0/stage1 上对；
> Stage3 已整层退役，stage2/4/5 又各有多节点或多相职责。`STAGE_SECTIONS` 从 `NODES` 派生，
> 且 `tests/test_coverage.py::test_stage_sections_is_not_the_naive_stage_number` 专盯「有人把它改回去」。

---

## 二、功能需求 R-0 ~ R-12

### R-0 · 非正文剔除与人工审阅（stage0）　★ 2026-08-10 新增

把原始 txt 里的**非正文体**（番外/后记/尾声/作者有话说）删掉，交人审阅，
**批准之后才允许进 stage1**。产物是「一本只有文案和正文的小说」。**这一层不调 LLM。**

> #### 为什么值得单独一层，而不是留在 stage1 里
>
> **① 它把破坏性操作变成了可审阅的操作。** 剔除的正确性没有任何结构判据能查
> （`R-1.8` / `B-20`：番外体已丢弃、盘上不留副本）。留在 stage1 里，判据错一次就静默吃掉半本书；
> 提到 stage0 并配审阅闸门之后，**stage1 里的同一套判据降级成一道断言**——
> 它不再切任何东西，只在「已批准的文本里居然还认出非正文标记」时报错。
> ⇒ 判据出错的代价从「吃掉半本书」变成「报一个假警」。**方向反过来了。**
>
> **② 闸门必须在 stage2 之前。** stage2 是第一个花钱的层。
> 事后审阅（S-03）发现问题时钱已经花了。
>
> **③ 番外混进正文不是「有点脏」，是会污染文风测量。**（用户裁决时给的理由）
> 某些番外**换视角、换人称**。它留在正文里会一路往下污染：
> stage2 抽出番外里的人物、关系与叙述信息 · **Stage5/6 从 Stage1 文本即时测得的句段/对白机械基线混入番外**
> （`R-4.1`）· stage5 按被污染的上游事实与即时读数写。
> `R-4.2` 已经规定「正文与开篇文案段分开测、互不污染」——**番外是第三个污染源，
> 此前它在需求里没有名字。**
>
> **④ 模式匹配补不掉的那部分，只能靠人直接编辑。** 实测确认过：番外若用纯数字章节标记写
> （`9` 换行、内容是番外），它和正常章节**长得一模一样**，没有任何结构信号
> （「章号回退」信号实测 0/3 精确率，见 `R-1.7` 的加词流程一节）。

| # | 要求 | 谁在查 |
|---|---|---|
| R-0.1 | 自动剔除用的是 `R-1.7` 的同一套判据与同一张 `NONBODY_KINDS`——**只有一份** | `HARD` |
| R-0.2 | 剔除逐段只记不可再生的决定与最小定位证据：类型、标记行、原文码点区间、来源。行号、字数与预览从原文和 span 现算；**番外体不进产物目录** | `HARD` |
| R-0.3a | **人工批准这个动作本身**——「这一段到底是不是番外」没有结构不变量可查，只能人看。**批准的唯一来源是人**：`POST /stage0/approve` 是唯一入口。批准请求必须带本次实际审阅的完整 `expected_revision_id`；工作包 A 与提交之间若经 edit/adopt 变成 B，乐观锁关闭式拒绝，不能把 A 的人眼结论盖到 B。审批页显示完整原文、删除高亮与运行时 warnings；正式分章对照在免费 Stage1 落盘后进行 | **`人`** + revision 乐观锁，见 `B-20` |
| R-0.3b | **未获人工批准的书，stage1 必须拒绝启动**；执行资格的唯一落点是 `novel/stages/stage0/eligibility.py::is_stage0_executable`，六处消费方（含前端，只读响应里的 `executable`）一律 import、**不许复述条件**；队列要明确说出在等谁（同 `R-13.5`）。⚠ 2026-08-22 删掉 `auto_zero_hit` 之后该谓词恰好等于 `approval.status == 'approved'`——**唯一落点这条纪律不随之失效**：条件越像废话越容易被顺手抄进消费方，而下一次收紧时漏掉的就是那一处 | `HARD` |
| R-0.4 | 人可以**直接编辑**待剔除的范围，也可以直接改正文——判据漏掉的那类只能这么修 | **`人`** |
| R-0.5 | 人工编辑记 `origin=human`，上一版**不可覆盖地留在 revision 链上**（`stage0/revisions.json`；判据 `s0_revision_chain` 逐条复算 `revision_id`，并要求 `clean_text.txt` 是 `current` 的唯一物化投影） | `HARD` |
| R-0.6 | 必须能核对**两个内容身份**：链根保存 `normalized_source_sha1`（原始 txt 归一化文本，不是文件字节 digest）；`clean_sha1` 从 current `clean_text` 与 live 投影现算。Stage1 的真正输入身份是后者 | `HARD` |
| R-0.7 | 剔除的**正确性**——「这一段到底是不是番外」 | **`人`**，见 `B-20` |
| R-0.8 | 重跑 stage0 **不许静默覆盖人工编辑**；要覆盖必须显式（同 `REVALIDATE_ONLY_NEW_ERRORS` 的精神）。**只有「当前稿是机器起源且未批准」才允许直接替换**（此时换掉不丢任何东西：没有人工内容可覆盖，也没有「人看过并批准了这一版」可绕过）；其余一律只挂候选、正式稿一字不动，由人整版三选一（保留当前 / 采纳 / 采纳后再编辑）；原文变化连候选都不谈。provenance 服务端落定，且命令 DTO 与产物 DTO 在类型上分家 | `HARD` |
| R-0.9 | 剔除后正文占比 < 0.60 判失败——**红在花钱之前**。〔从 `R-1.6` 移交〕 | `HARD` |
| R-0.10 | 可疑但不算错的按 current 内容现算为显式 `warnings[]` 视图（`STAGE0_WARNINGS` 三类），**不落 revision、不静默、不自动修正、不硬报错**。〔从 `R-1.9` 分出〕 | `INFO` |
| R-0.11 | 解码：BOM 优先探测，无 BOM 时试 `utf-8` → `gb18030`。**解码失败入显式终态，绝不用替换字符**。〔从 `R-1.1` 移交〕 | `HARD` |
| R-0.12 | 归一化**只做两件事**：去 BOM、换行统一为 `\n`。**不做 NFC/NFKC、不 strip 行尾**。〔从 `R-1.2` 移交〕 | `HARD` |
| R-0.13 | 目录寻址只有一把尺子：文件名结尾有 `_<数字>` 就用它当 id，否则用书名本身当键；写盘前断言目录名能被同一把尺子解析回来且是不动点。〔从 `R-1.10` 移交〕 | `HARD` |
| R-0.14 | 同目录不同正文 → 显式终态（除非显式强制覆盖）。〔从 `R-1.11` 移交〕 | `HARD` |

> #### 为什么摄入的这四条（`R-0.11` ~ `R-0.14`）也跟着搬
>
> **谁读原始 txt，谁就负责解码、归一化、寻址和「同目录不同正文」。**
> 加了 stage0 之后读原始 txt 的是它，stage1 的输入变成 `stage0/clean_text.txt`——
> 一份**恒 utf-8 / 恒 `\n`** 的文件，没有编码可猜、没有 BOM 可去。
> 把这几条留在 stage1，就是四条永远走不到的分支：
> `source_encoding` 恒 `utf-8`、BOM 分支恒不触发。
> ⚠ 这正是本项目最忌讳的形态——**一条从不执行的判据，和一条不存在的判据，在报告上长得一样。**

> #### ⚠ `R-0.6` 为什么必须是两个指纹
>
> 加了 stage0 之后，**stage1 的输入不再是原始 txt**。
> 只记一个指纹，「有人换过原始小说」和「有人改过 stage0 的剔除结果」就分不开了——
> 而这两件事的处置完全相反：前者要从头重跑，后者是**人有意为之、必须保住**。
> `R-1.3` / `R-1.11` 那两条 `HARD` 判的是前者，`R-0.5` 的 revision 链管的是后者。

> #### ⚠ 已知代价，登记在案
>
> **① 人工编辑打破 0 成本复算（`Q-3`）。** 人一改，stage0 就不再是确定性的。
> Stage0 用 `--apply-edit` / `origin=human` / revision 链留痕，影响面大得多：
> stage5 的编辑只影响那一章，**stage0 的编辑是全链输入，一改下游七层全部陈旧**。
> 那是真回归不是假回归，但界面要让人预期到。
>
> **② 闸门的吞吐成本。** 实测 163 本里只有 **27 本**有能被判据检出的非正文，**136 本是干净的**。
> 「全量审阅」能覆盖盲区但要 163 次人工；「只审检出的」吞吐高，但**盲区①（检出数为 0）
> 永远不会进队列**——而防它正是加这一层的理由之一。
> ⇒ 现役策略是**每本都停在人工闸门**，零命中也不自动批准；Stage0 审批只回答剔除是否正确，分章与章号异常由随后的 Stage1 对照页回答。

---

### R-1 · 摄入与分章（stage1）

把 **stage0 批准过的文本**变成结构化的章表。**这一层不调 LLM。**

> **`R-1.7` 的角色已变**（2026-08-10）：非正文剔除搬到 `R-0`，stage1 **不再切除任何内容**。
> 同一套判据在这里降级成一道断言——见 `R-1.7`。

| # | 要求 | 谁在查 |
|---|---|---|
| R-1.1 | ~~解码~~ → **已移交 `R-0.11`**。本层的输入是 `stage0/clean_text.txt`，**恒 utf-8 / `\n`，没有编码可猜** | 见 `R-0.11` |
| R-1.2 | ~~归一化~~ → **已移交 `R-0.12`**。同上：输入已经是归一化过的 | 见 `R-0.12` |
| R-1.3 | 当前 `clean_text` 的全宽 sha1 必须等于 Stage0 当前批准 revision 的 `clean_sha1`；对不上即阻断。Stage1 不再透传源身份或落盘第二份指纹 | `HARD` |
| R-1.4 | 原文章号的断号 / 重号 / 倒退由当前 `clean_text` 复扫，只在 Stage1 对照视图与校验报告里显示；不复制进 `main_text` | `HARD` |
| R-1.5 | 复扫得到的章标记数必须等于 `main_text` 长度，且每一章 `text` 必须与当前 clean 切片逐字相等 | `HARD` |
| R-1.6 | ~~正文占比 < 0.60 判失败~~ → **已移交 `R-0.9`**。本层不再剔除，切点只由完结标记决定，`body_ratio` 恒 ≈1.0——**留在这里就是一盏保证会亮的绿灯** | 见 `R-0.9` |
| R-1.7 | **降级成断言**：在 stage0 已批准的文本里若还认出非正文标记 → **显式终态**（说明 stage0 漏了）。<br>**本层一个字都不切。** 判据与词表仍与 `R-0.1` 同源——只有一份 | `HARD` |
| R-1.8 | ~~剔除的正确性~~ → **已移交 `R-0.7`**。本层不再做剔除，也就不再有这条 | 见 `R-0.7` |
| R-1.9 | 同一原文章号重复达 `NOISY_DUPLICATE_MIN` 时产生 `s1_warnings` 的 `INFO` finding；证据从当前 clean 复扫，**不落盘、不静默、不自动修正、不阻断** | `INFO` |
| R-1.10 | ~~目录寻址~~ → **已移交 `R-0.13`**（谁读原始 txt 谁负责寻址） | 见 `R-0.13` |
| R-1.11 | ~~同目录不同正文~~ → **已移交 `R-0.14`** | 见 `R-0.14` |
| R-1.12 | **认不出开篇文案段 → 显式终态**：不写任何产物、后续 stage 一步都不跑。文案段不是可选的 | `HARD` |
| R-1.13 | **正文章数 < 2 → 显式终态**：只有一章的「书」不接受重构。**不带字数阈值** | `HARD` |
| R-1.14 | 切点与第一个非正文标记之间那一段，**要么为空，要么只有一个完结标记行**——正文里不许漏进第三种东西 | `HARD` |
| R-1.15 | **源文本覆盖不变量**：`[0, total_code_points)` 必须完整分解为 hook + 逐章标记行 + 章正文 + tail——无未记账码点、无重复覆盖；每条标记行须通过章标记文法解析，并与同一次 clean 复扫得到的标号相等。判据走覆盖游走 + 文法解析，**不与分章产物自证** | `HARD` |

**剔除判据已按 161 本语料标定**：命中 27 本 / 34 条标记 / 0 假阳，切点全落在 75.9%~99.3%。
判据细节（它们各自都是踩出来的，重写要连着搬）：

- 逐行 strip 后剥掉前导 `【 [ ( （` 与 `\d{1,3}[.、．:：]?`、尾部 `】 ] ) ） 。 : ：`，得到 `core`
- `core` 以「番外」开头 → 命中（`len(core) <= 20`）；「番外」在中间或结尾 → 命中（`len(core) <= 12`）
- `后记`/`尾声` 要求 `core` **全等**，`作者有话说` 要求前缀相等。
  **不能用包含**：语料里有「节目的尾声」（5 字）、「前厅的宴席似乎已经接近尾声」、
  「孩子出生后记在你的名下」——它们**比真标记还短**，长度上限救不了，全等才救得了
- 两条长度上限是假阳的唯一防线（实测真标记开头分支最长 17 码点、中/尾分支最长 6，
  而含「番外」的正文句最短 77）。**这些数字必须按码点数**：「番外」一个词就是 6 字节，
  拿字节数当字数会把上限放大三倍
- **开头分支的上限 2026-08-10 由 30 收到 20，同时告警线由 20 降到 17**。原型那组数留了一段
  **静默假阳区**：实跑出来是四段带状——`core ≤ 17` 真标记 · **`18~20` 切了但不出声** ·
  `21~30` 切了且出声 · `>30` 不切。中间三档就是洞：一句 18 码点、以「番外」开头的正文句
  （`番外的那天她再也没有对任何人提起过了`）会把其后内容全部吃掉，一声不响。
  改后：`≤17` 静默切 · `18~20` 切但告警 · `>20` 一律当正文不切，静默区为 0。
  **宁可漏切也不误切**——漏切是番外留在正文里、章表上看得见；误切是正文被删掉、看不见。
  全语料 163 本行为不变（真标记最长 17）
- **切点要往回退一行**：标记行之前（跳过空行）紧邻的若是一个章节数字行，那个数字属于番外
  （实测 5 本这样写）。不退的后果不是报错，是多出一个被当空白跳过的空章，而章数仍把它算进去

> **番外体在盘上不留任何副本**（用户裁决：彻底抛弃，不是「纳入别处」）。
> 因此剔除记账的摘要是**唯一**能核对「切对了没有」的东西——剔除是破坏性的，一次假阳会静默吃掉半本书。

> **`R-1.12` 为什么是 `HARD` 而不是一条 warning**（2026-08-10 裁决）：文案段有三个下游消费方——
> Stage5/6 从它即时测量机械形态（`R-4.x`）、stage6 的三候选参照它的收尾形态（`R-9.4`）、
> stage7 成书的第一段就是文案（`R-10.8`）。带着一个空文案段往下跑，
> 等于把一次本该在 0 成本层红掉的失败推迟到花钱之后。
> 全语料 163 本上它**从没空过**，但那是「没有失败样本」不是「不可能」——
> 所以判据按代码的可达性定，不按样本定。
>
> **`main_text.json` / `hook_copy.json` 落盘后即只读**（2026-08-10 裁决）：分章是确定性的，
> 切错了改算法重跑，不在界面上手改数据。⇒ 这两份没有 `--apply-edit` 那条回写路径。
>
> **错别字与异体人名不在本层统一**（例：原文里同一个人写成「徐喻朗」和「徐瑜朗」）——
> 本层一个字都不改（`R-1.2`），归并留给 **stage2 的实体消解**。
> 〔这条分工原先只写在 `partition_report.notes[0]` 里，该字段已随报告瘦身删除，搬到这里。〕
>
> **`R-1.13` 影响面已实测**：全语料 163 本里 `detected_chapter_marker_count == 1` 的正好 **10 本**
> （10670 / 11608 / 12357 / 14924 / 15516 / 15648 / 15785 / 18912 / 24862 / 28223 字），
> 全部拒收。核过原文：它们确实只有一个 `1` 标记、之后再没有数字行——**真·未分章，不是漏切**。
> 原型对这种情况只出一条 warning（「仅识别到 1 个正文章节，但全文 N 字」），
> 而那条 warning 占了全语料 11 条告警里的 **10 条**。⇒ 它一直在报的是一件该拒收的事。
>
> #### `R-1.7` 的加词流程（`NONBODY_KINDS` 是开放表）
>
> 遇到新写法（「外传」「特别篇」「小剧场」…）就加一个，**但必须按顺序走完四步**。
> 理由：剔除是破坏性的，一个假阳静默吃掉半本书；而**词表只会单向增长，加错了没有任何东西提醒你**。
>
> 1. **过闸门**：`python tools/probe_nonbody_word.py <词>`。它查三件事——
>    词在正文里以整行出现过吗（直接误切）· 以它开头的短行有吗（开头分支误切）·
>    和 `END_MARKER_WORDS` 撞车吗（完结标记走的是另一条路，不进 `dropped`）。
>    退出码非零就别加。
> 2. **选匹配强度**：除 `番外`（有 `番外一` / `番外：xxx` 这类变体，用「开头 + 长度上限」）
>    之外，**一律全等匹配**。⚠ 别用「放宽长度上限」去兜变体——那只会扩大静默假阳区。
> 3. **补一份合成样本**进 `tests/fixtures_ingest.py`，让这一支被执行过。
> 4. **两张表一起改**：`NONBODY_KINDS` 末尾追加 + `NONBODY_KINDS_ADDED` 登记同一个词。
>    只许追加不许删——删掉一个词，存量产物里那个 `kind` 就读不回来了。
>
> **已跑过的候选词**：`外传` / `特别篇` / `HE线` / `其他视角` / `番外篇` / `小剧场` /
> `幕间` / `终章` / `后日谈` / `结语` **全部安全**（`core` 全等命中 0）。
> ⚠ 两个实测教训：`外传` 在正文里出现 **26 行**（「不外传的秘药」「**门外传**来脚步声」）——
> **全等安全、包含灾难**；`正文完` 有 1 处全等命中，但它**已经是完结标记**，加进来就是两条路记同一段。
>
> **加词补不掉的那个盲区**：番外用纯数字章节标记写（`9` 换行、内容是番外）。
> 找过两个结构信号：「完结标记之后还有内容」**已经在用**（46/163 本有完结标记）；
> 「章号回退重启」全语料只有 3 本，**逐本核对没有一本是番外**，`0/3` 精确率。
> 其中一本回退 4 且发生在全书 86% 处——「幅度大 + 位置靠后」这个看起来最强的信号是假的，
> 按它去切会砍掉一本书的最后一章正文。⇒ **这条只能靠 S-03 对照屏由人兜底（`R-1.8`）。**

> **`R-1.14` 是替换掉一条错契约来的**。原契约（原型 `CONTRACTS.md` §1.5.2）写的是
> `body_code_points + dropped_code_points == total_code_points`，**它在 42/163 本上不成立**，
> 而 `check_partition` 根本没在查。缺口恒等于那一行 `（全文完）`：它既不在正文里
> （切点停在它之前），也没进 `dropped`（它不是非正文标记）。
> 按原契约去「修数据」就得把它硬塞进正文或塞进 `dropped`，两个都是假的——
> **判据错的信号就是：按它去修数据会让产物变得更差。**
> 新写法在 163/163 上成立（121 本那一段为空，42 本恰好是一个完结标记行），
> 并且顺带接管了「完结标记之后还有内容」那条从没触发过的 warning，把它变成 `HARD`。

---

### R-2 · 全书级抽取（stage2 · 一次调用）

抽出跨章成立的东西：人物、关系、世界规则、秘密、剧情弧线、设定。**纯描述，不做规约。**

| # | 要求 | 谁在查 |
|---|---|---|
| R-2.1 | 人物正典名**全书唯一** | `HARD` |
| R-2.2 | 人物的叫法（称谓表）逐条**必须在原文里原样搜得到** | `HARD` |
| R-2.3 | 称谓**整条不许是代词**。有据的叫法若全都含代词，下游必然取到「我妈」这种相对说话人的说法，而它不触发任何终态 | `HARD` |
| R-2.4 | 「谁在这么叫」的取值域闭合为 `<人物id> / 叙述者 / 公众`。**指称群体一律记「公众」**——「苏府下人」这类群体名解析不出人，而这个字段的用途是派发键 | `HARD` |
| R-2.5 | 每条叫法带「从第几章起可用」，且**不早于该叫法在原文里首次出现的章**（单边判定：早于字面首现一定错；晚于合法） | `HARD` |
| R-2.5b | 称谓表自己的两个覆盖度读数：**「自己这么叫自己」**（自称正当，把别人对他的称呼挂错了人不正当）· **一个称呼都没有的角色**（叙述里总得指称他，多半是漏了）。两条各报各的——合成一个「称谓表有问题」就分不出该修谁。⚠ 「叫法在不在原文里」不在这里，那是 `R-2.2`（同一件事不许有第二个落点） | `INFO` |
| R-2.6 | 人物的静态身份描述 `role` **非空、不含结局词、不含模棱语、不得与正典名同字**；`role` / `canonical` 会进入写作与改名材料，故命中继续 `HARD`。`fate` 不进 Stage4/5 写手材料且复核效果为 disclosure，其中的模棱语只单列 `INFO`，不得借 `hedge_words` 阻断 Global 或 Stage4。词表必须按完整语义短语匹配：确定判断「不可能」不是「可能」的模棱命中；「不太可能」「并非不可能」仍命中 | `HARD`（`role` / `canonical`）+ `INFO`（`fate_hedge_words`） |
| R-2.6b | 正典名只在两种情况下可以自拟：为消歧加区分词 / 原文只用亲属称呼指代这个人。**加了区分词就必须真有同名对象**——没有重名就没有消歧可言，而区分词会被写手原样写进正文 | `HARD` |
| R-2.7 | 结局单独存一个字段，与静态身份分开——**它是故意造出来接住结局的**，删了结局会混回身份描述 | `度量`（`fate` 的缺失率无条件报出）<br>⚠ **只管「两个字段分开」这件事**；「结局有没有漏进身份描述」在 `R-2.6` / `R-2.12` |
| R-2.8 | 关系带 `valid_from_chapter`（从第几章成立）与 `valid_through_chapter`（最后仍成立的章，**含该章**；没有截止章填 `null`）。旧名 `valid_to_chapter` 没说清边界是否包含，随 GlobalGrounding v3 同批改名，不改变既有包含式判定 | `HARD`（范围） |
| R-2.9 | 秘密带「埋点」与「读者第几章知情」两套时间——**它们不是一回事**。埋点是**判别联合**（`{"kind":"cold"}` / `{"kind":"foreshadowed","chapters":[…]}`，2026-08-23 v2）：可空列表的 `[]` 同时表示「声明冷揭示」与「模型没想过」，而这一格是关系闸门隐瞒窗口与 secret 闸门第②臂**两道下发闸门的前件**。⚠ 判据断言的仍是 `_provenance.setup_chapters_pruned` 那个**裁剪计数**，不是字段值——裁剪跑在落盘前，断言值永远为真；裁空的那条降级成 `cold`，与真·冷揭示同形，只有那份账分得开 | `HARD` |
| R-2.9b | 埋点分布的读数**分两档报**：`setup.kind == "cold"` 的条数（模型显式声明没有埋点，据实声明是允许的）· 其中**由裁空降级来的**条数（那说明产地在把揭示章号抄进埋点，不是合法冷揭示）· **这本书有没有可查的秘密**。最后一条不是统计口味——一条秘密都没有时，逐章剧透闸门与「本章不许揭示什么」在这本书上**恒不执行**，任何「剧透闸门验过了」的说法对它都不成立 | `度量` |
| R-2.9c | 「谁从何时起知情」由 `knowledge_events[]` 明说：每项带包含式 `known_from_chapter`，主体是判别联合 `CharacterRef {kind="character", char_id}` / `GroupRef {kind="group", label}`。人物臂必须解析到 registry；群体臂保留「在场士兵」这类自由文本，**不许逼模型伪造人物 id**。知情时点只裁本章下发的 `known_by`，不能替代读者揭示时点打开 secret | `HARD`（引用/章范围）+ Stage4 前章/当章投影回归 |
| R-2.9d | 秘密声称在某章向读者点破，那一章的原文里应当有点破痕迹。**这是筛子不是判据**：「演而不说」是合法写法，它只把「需要有人回原文核一眼」的清单从全书压到个位数 | `INFO`（词表） |
| R-2.10 | 五个枚举：`人名区分度` · `规则类型` · `称谓场合` · `弧线变化类型`（开放枚举）· `叙述人称`（= 我／你／他，**只此一轴**）。限知/全知随 Stage3 定性画像退役后当前没有独立载体，不得谎称仍由下游约束 | **无守卫 → `B-08` / `B-09`** |
| R-2.11 | 抽取判据本身里**仍然没有守卫**的三项：只收结构性规则 / 秘密的四类收录判据 / 冲突阶段名要能定位 | **无守卫 → `B-08`** |
| R-2.11b | 规则描述**必须连「怎么运作 / 怎么判读」一起写**——只写「存在」等于没记，而规则正文是唯一落点。规则正文里出现人物名字时按**叫法时点**扣住，且**必须报出这道闸门自己的基线**（拦下几条、几条其实全程放行） | `HARD` |
| R-2.12 | 身份描述里不许藏结局或未揭示的秘密 | **无守卫 → `B-14`** |
| R-2.13 | Stage2 有一条**契约内的 0 LLM 人工订正入口**。`GlobalEdit` v3 覆盖人物枚举/role/fate、关系、setting、secret、world-rule、冲突阶段、arc track 与 address；自由 claim 的 patch/replace 必须同交 evidence locator，完整替换面允许新增、删除、拆分与全书冲突分段重画。`conflict_stages` 没有稳定行 ID，因此只提供非空整组 `conflict_stages_replace`，不伪造依赖数组下标的逐项 patch。完整候选严格 typed、ID 唯一；patch+replace 冲突、未知目标/下标、越界章号、悬空人物/secret/address 引用都先在内存副本关闭式拒绝，失败不得半应用；其中 `conflict_stages_replace` 只在字段出现时于赋值前共用 `checks.measure_conflict_stage_coverage` 精确校验缺/重/越界，失败为 `TerminalState`，正式稿、既有 `.INVALID`、review 与 `_rounds/` 字节全不动。应用后重走 locator 回取、归一、当前判据、来源账/轮次归档与 `global_review` surface 枚举；旧人工决定仅在 claim+evidence 的 subject path+hash 完全相同时继承；完整候选其余 HARD（包括未由本次 replacement 引入的既有覆盖错误）仍进 `.INVALID`，不覆盖正式底座。旧 v1/v2 决策集不能凭空补出时点/证据，须人工按 v3 重写；本字段是可缺的兼容加法，旧 v3 原样可读 | `结构`（`novel/stages/stage2/extract.py::run_apply_edit`，决策集形状由 `contracts/schema/stage2.py::GlobalEdit` 收走）+ 原子性/正反例/真实 consumer 回归 |
| R-2.14 | `conflict_stages[].chapters` 是**逐章显式枚举**，不是区间端点；全部阶段合起来必须让 Stage1 的每个阅读序章号**恰好出现一次**，不得缺章、重章或越界。producer/完整候选命中时问题稿进 `.INVALID`；显式提交了坏的 `conflict_stages_replace` 则在赋值和任何写盘前 `TerminalState`，两条路径共用 `novel/stages/stage2/checks.py::measure_conflict_stage_coverage`，不得重抄第二套判据。恢复优先用 `GlobalEdit.conflict_stages_replace` 完整重画后重跑 `--apply-edit`（0 LLM），或直接人工补全 `.INVALID` 后 `--revalidate`（0 LLM），最后才是显式 `--resample` 重采 | `HARD`（`conflict_stage_coverage`）+ replacement 缺/重/越界原子反例 + 既有坏候选经统一 HARD 闸回归 |
| R-2.15 | `stage2.global --emit-review-template` 必须 stdout-only 从**当前正式** `global_review.json` 投影一份 `GlobalReviewDecisionSet` v2。默认 blocking 面使用一个关闭式 `blocking_scope`：绑定当前 source SHA-256、完整 blocking `(item_id, subject_sha256)` 集合 SHA、项目数与正文章全集，状态默认 `UNVER`，所以原样应用不能打开 s2 gate；核验后可显式改为 `PASS`，`decisions[]` 只列逐项 FAIL/UNVER/PASS 例外，服务端仍展开成逐项 hash-bound 持久决定，且 scope 永不自动裁 disclosure。scope 还必须绑定**实际读过的 workpack**：workpack 顶层与 binding 同时包含完整 typed MainText 的 SHA-256，最终 workpack SHA 覆盖它；模板的 `expected_workpack_sha256` 固定为全零占位并被 apply 明确拒绝，operator 只能从本次实际读过的 workpack 手工复制真实值，重新生成模板不得替人刷新阅读凭证。MainText 输入身份变化时，人物/关系/规则/秘密集合完整性、冲突分区与 arc 分析六类整书决定必须重开；其余可独立修正的事实项仍按自身 subject 继承。`--review-scope all` 仍逐项携带当前 item id、完整 subject SHA-256 与 evidence；两种形状都不得从 grounding 重拼第二套 item 身份。v1 没有 scope hash/正文章全集声明，不迁移；零成本重发 v2 workpack/template 后由人复核再交 | `结构`（`novel/stages/stage2/global_review.py::review_template` / `::review_workpack` + `novel/quality/global_review.py::blocking_scope_sha256` / `apply_decision_set`）+ MainText 漂移/旧包新模板/CLI/API 正反回归 |
| R-2.16 | Stage2 全局逐项复核与**非称名** Global HARD 不再是 `stage2.blueprint` 的串行前置：通过当前 schema 且称名 HARD 已收口的最新 Global 候选，无论正式落在 `global_grounding.json` 还是因其他 HARD 留在 `.INVALID`，都必须从它与 current MainText 确定性物化注册、版本化的 `blueprint_character_projection.json`；称名 HARD 未收口时不得物化可消费投影，freshness 必须报 `unknown/global_naming_blocked`，Blueprint 在任何 LLM 前关闭，state/queue 只停在 Global typed edit / `--revalidate`（0 LLM），不得 `--renormalize` 或默认付费重采。Blueprint 的 prompt、validator、compiler、provenance 只消费 current/fresh 的该投影 + MainText，不读取或伪记 role/fate/secret/relation/setting 等 Global 边。投影 freshness 必须强类型校验完整 GlobalGrounding 与 MainText，不能因未消费字段恰好不改变投影就把 schema-invalid 底座洗成 fresh。人对 workpack 的阅读/填写可与逐章计算重叠，但同书的 review、typed edit 与 producer 写回仍服从同一互斥。排除 `global_naming_blocked` 后，投影缺失、损坏、或相对最新 formal/INVALID Global stale 时，真实 Blueprint consumer 仍须在任何 LLM 前关闭；state/queue/inbox 只给出或执行 `stage2.global --renormalize` 从现有底座重物化（0 LLM），不得把缺一份伴生产物解释成默认付费重采 Global。旧 ChapterBlueprints v6 若仍记录 `{MainText, Global}` 输入边，不升 artifact version；store 对这一精确 legacy 键形诚实报 `unknown/consumed_unresolved`，state/queue/inbox 只在 current/fresh projection 已在场时把它解释为 `stage2.blueprint --renormalize` 零调用刷新，不得伪称 stale 或泛化到其他 unknown。live Blueprint `.INVALID` 无论是否与旧 formal 并存，state/queue 都只停在 `stage2.blueprint --revalidate --actor <id> --reason <why>`（0 LLM）并留空默认 paid argv；`--revalidate` 只重判 inputs 已是 projection 的当前 `.INVALID`，不得改写旧问题稿的 immutable baseline inputs 冒充迁移。Global 的 role/fate 等修订若投影逐值相同，不重写投影字节、旧 Blueprint 保持 fresh；称名候选变化则投影 stale，重物化后旧 Blueprint stale。`address.from_chapter` 只在同一归一 surface 有多个 owner 且确实改变候选时进入投影，canonical shadow、唯一称呼及 role/secret/relation/setting/address.by/context 等未消费变化不得制造付费 stale。统一硬闸仍从 Stage4 首项开始：Stage4A/B、`stage4.context`、其余下游付费执行及 Stage7 组装/下载都必须同时看到 current **正式** global、current/fresh projection + blueprint、`stage2.blueprint --check-only` 的联合 HARD 通过，以及与当前 grounding 完整一致、且**全部 blocking 项**逐项 `PASS` 的 review；Global `.INVALID`、blocking 面的 `FAIL` / `UNVER` / `pending` 一律不放行。**交付效果分级见 `R-2.22`**：`disclosure` 三类不阻断，但不自动 `PASS`、不从评测面消失，且必须进 inbox 与终审包。review 早于 blueprint 完成可以正常保存，但 `s2` 必须保持关闭，不能因缺 blueprint 回滚有效决定或伪装成已开闸。`dry-run`、联合 `--check-only`、typed edit、`--revalidate` 等零成本恢复路始终开放；Stage2 唯一显式复检仍是 blueprint，禁止新增 `stage2.global --check-only`。决策模板、书内相对/绝对输入与 `_service/edits` API 输入统一使用注册表版本化的 `stage2/global_review_decisions.json`；`--emit-review-template` / `--emit-review-workpack` 默认只输出 blocking 面（`--review-scope blocking|all`），裁剪的是**只读阅读范围**而不是评测面——没进输出的项在盘上仍是 `pending`；workpack 只给主体、注册证据与原文精确命中上下文，不自动做决定 | `结构`（`contracts/invariants.py::stage2_review_required` + `novel/stages/stage2/global_review.py::require_approved` + `novel/stages/stage2/character_refs.py::materialize_blueprint_character_projection` + `novel/store.py::_blueprint_character_projection_freshness` + `novel/consumption.py::CONSUMED_PROJECTIONS` + state/queue/CLI/API/Stage7 消费边）+ formal/INVALID 物化、schema-invalid/legacy unknown 反例、0-LLM 恢复、review 并行/早交/freshness/模板/API/下载回归 |
| R-2.17 | `R-2.16` 那道闸的**评测面**必须覆盖：每个角色的 `role` 与 `fate`、`canon.setting` 两条事实、每条 relation、每条 secret 的 fact / knowledge event / 埋点章，外加 `arc.conflict_stages` 与 `arc` 其余部分两项分组裁决；同时必须为 characters / relations / world_rules / secrets 四个集合各枚举一项完整性裁决，反向检查有没有整项漏抽。粒度按「**能不能被单独修正**」分：事实逐条、解读分组；自由事实的 subject hash 同时绑定 claim 与正式 evidence，证据改变会重开该项，集合完整性项则故意剥 evidence，避免只改坐标冲掉「没有漏项」的决定。**没被枚举出来的字段等于没人审**——`R-2.16` 说的「与当前 grounding 完整一致」指的是这份清单一致，不是整份 grounding 过了人眼。⚠ 风险面包括整个人物/关系漏抽、`role` 的结局旁路、`setting.世界观` 的泄底、relations 第二剧透通道、人物知情时点与 secret 埋点 | `结构`（`novel/quality/global_review.py::enumerate_required`，取值面在 `contracts/schema/evaluation.py::GlobalReviewItem`）+ 覆盖面/继承回归 |
| R-2.18 | **一条 secret 只承载一个原子事实**，并只保留一个 `first_reveal_chapter`。复合真相分步点破时必须拆成多条 secret；对下发无作用的分组与确认事件不进正式 schema。逐章下发闸门与全部下游读数共用 `novel/core/facts.py::first_reveal_chapter`；`fact` 是否仍塞了多个原子事实由 `R-2.17` 人工复核 | `结构`（`contracts/schema/stage2.py::Secret`）+ 逐章下发路径回归 |
| R-2.19 | relation 的**世内成立时点**与**读者可见时点**必须分开。`visibility` 是判别联合：`public.from_chapter` 或 `via_secret.secret_id`；生产下发只认 `novel/core/facts.py::relation_visibility_gates` 的显式声明，分别取 `max(valid_from, public.from)` / `max(valid_from, secret.first)`，悬空 secret 或说不出的 first 关闭为不可见。旧人名+埋点启发式只由 `relation_visibility_mismatch` 作 INFO 对账，绝不能再决定 Stage4 事实投影 | `HARD`（引用/章范围）+ `INFO`（显式/启发式分歧）+ Stage4 context/fact_refs 共用落点回归 |
| R-2.20 | 七类自由事实（role/fate、relation predicate、setting 两格、world-rule desc、secret fact、knowledge event、arc trajectory）都必须带**代码回取**的原文证据。模型/人工只交 `{chapter_ordinal, locator}`。合法指定章先 raw、后 normalized 且必须唯一命中；指定章**零命中**时才把章号降为可纠正提示，按阅读序逐章复用同一定位尺子，且仅当全书恰一候选、任何章都没有多命中时纠正为真实章，再统一过 `exact_span`。指定章多命中、非法章、全书零/多命中或 formal span 漂移仍落 `source=null` + 正面 failure 账，由 `global_evidence_unlocated` HARD 拦到 `.INVALID`；禁止 fuzzy、拼接全文、猜首命中，也不得让 response 直接伪造 `SourceSpan`。存量 `.INVALID` 只有在 failure 原因明确来自 locator、claim path 与声明章仍相同且 `source` 仍为空时，才可反向恢复 locator 后重跑同一算法；formal `SourceSpan` 漂移的失败账永不反向恢复，连续 `--revalidate` 也不得把它自动搬家。成功跨章纠正不改变 GlobalGrounding v4 / GlobalEdit v3，也不进正式 provenance；只在 `_service/llm_audit.jsonl` 留绑定最终 artifact SHA 的无正文事件（claim path、原/新章、exact/normalized）。agent 首轮若只命中本 HARD，在同一 session、同一固定 response schema 下只接收 evidence patch；其 `candidate_sha256` 是信封内、归一前 raw `GlobalGroundingResponse` 的 canonical SHA，不绑定 provenance 或正式 artifact。apply 必须重新计算当前 base SHA，changes 的 canonical claim path 集合必须与失败槽**精确相等**；旧 hash、base 变异、重复/缺失/额外路径与 no-op 均关闭式拒绝。合并后仍完整重跑 response schema、normalize、全部 HARD、provenance、正式 readback，再由唯一 adoption 绑定最终 patch attempt；`candidate_patch_applied` 只记 attempt、前后 raw SHA 与改动格数，不记 path/locator，也不冒充 adoption。候选所有权留在 `Stage2GlobalAgent`，外层 Commander 不接收或改写候选。终态后的 0 LLM 恢复路是 `GlobalEdit` v3 locator 或手改 `.INVALID` 后 `--revalidate`；显式整份 `--resample` 只作最后手段，不是本规则的默认修复路由。Stage4/5/6 的实际材料与 freshness 投影一律剥 evidence，避免证据原文外发和无关付费 stale | `HARD`（`global_evidence_unlocated`）+ exact/normalized/0/多/非法章/formal drift/patch hash 与越权/审计无正文/恢复/下游隔离回归 |
| R-2.21 | canon 中所有决定逐章下发的章引用都必须落在当前 Stage1 正文章集合：`world_rules[].versions[].effective_from_chapter`、`secrets[].setup.chapters[]`、`secrets[].first_reveal_chapter`。越界值不是“未来再生效”，而是永远不生效/不揭示；生成、复检与 GlobalEdit 必须同判。relation、knowledge event、address、evidence 与 conflict stage 由各自专门规则守，不在本条重报 | `HARD`（`canon_chapter_refs_dangling`）+ 三类越界反例 + 生成/人工同判回归 |
| R-2.22 | Stage2 复核项按**交付效果**分 `blocking` / `disclosure` 两级，分类是**白名单 + 关闭式**，唯一落点 `contracts/invariants.py::review_effect_of`：未登记的 kind 当场拒绝，没有「默认 disclosure」。当前 `disclosure` 只有三类——`name_distinctiveness`、`address_context`、`fate`（前两类另有 naming 与 Stage7 改名审批 + 恒等检查在守，`fate` 本来就不下发写手）；legacy `known_by` 保守按 `blocking` 读。⚠ **分级只改「谁能拦」，不改「谁必须在场」**：`global_review.json` 形状与版本一格不动，`current` 仍要求 item/path/hash 集合完全一致，`disclosure` 的 pending/FAIL/UNVER 不得自动 `PASS`、不得从审计面消失，必须逐条进 inbox（`delivery_effect='disclosure'`）与终审包。API row 随行下发服务端派生的 `effect` 与 `group_key`（同一主体的全部项共用一张人审卡），并给出 blocking/disclosure 各自的四态计数；**分类表不经 `/api/enums` 出门**，前端不得复制一份 | `结构`（`contracts/invariants.py::review_effect_of` + `novel/quality/global_review.py::review_status` / `::review_group_key` + `novel/views/inbox.py::_classify`）+ 分级、开门条件、清单、CLI scope 与前端卡片的双向回归 |

> **R-2.6 的词表只抓「结局」，抓不到中段才揭示的事实。** 实测基线：
> 某本书上词表命中 **0/17**，而人读真违规 **6/17**（「…后成为某人的下属」差一个字避开了词表里的「后被」；
> 「勾结外族叛国」是一条第 6 章才揭示的秘密，一个结局词都没用上）。另一本命中 2/40。
> ⇒ 这条判据**不是恒为 0，只是判别力低**。它命中时照报失败是对的，但
> **「没报违规」在这一维上不等于「没问题」**（`B-14`）。
> 处置不能是加词（那是个案补丁），也不能是按章收窄身份描述（会掏空写作节点对角色的认知）。

> **⚠ 一条跨节点的规矩（2026-08-10 裁决）：每个结构化调用点必须声明自己【全部】必需顶层键。**
> 不逐点决定「这一块有没有后置兜底判据」——**兜底判据本身是会退役的**（stage3 已退役过两条），
> 把判准挂在会消失的东西上等于没有判准。
> 已知要补的是全书抽取的 `arc`（**裸缺口，缺整块全链不出声**）。
> 旧 Stage3 `hook_style` 结构化调用点已随定性生产整体退役，不再保留一条假兜底。

> **⚠ `R-2.9` 的判据必须断言【裁剪记账】，不能断言字段值**（2026-08-10 裁决）。
> 原型有一道确定性修复跑在**落盘前**，把不早于揭示章的埋点章直接裁掉——
> 于是校验器读到的一定合法，**这条 `HARD` 在新鲜产物上结构性不可能红**，
> 而它照常打印 `[OK] 7 条 secret / 0 个埋点章，均严格早于其回收章`（一条零输入的判据在报通过）。
> 实测 21 条 secret：结构上不可能有埋点（最早揭示 = ch1）**4** · 可以有却空 **16** · 真有 **1**，
> 而「合法的冷揭示」与「被裁空的假埋点」**在产物上完全同形**。
> ⇒ 裁剪次数与清单落进来源记录，判据改断言那个计数——与「称谓兜底次数」同规格。
> **下游两条剧透闸门以「最早埋点章」为前件，埋点被裁空时它们不触发，而不触发时没有任何输出。**

---

### R-3 · 逐章抽取（stage2 · 分批调用）

逐章抽出「发生了什么」：事件拍、以及必须逐字保真的原文「点」。

| # | 要求 | 谁在查 |
|---|---|---|
| R-3.1 | 舞台拍、事实原子与保真材料的正式编号分别由代码确定性归一为 `b<章>_<序>`、`f<章>_<序>`、`h<章>_<序>`；LLM 只交章内临时 atom_key，不自拟跨层正式 id；正式载体正则是同一条约束的类型面 | `HARD` |
| R-3.2 | 每一拍带它在原文里的区间，且**区间可定位**；游标单调 | `HARD` |
| R-3.2b | 模型响应里的 locator/phrase 只用于定位；正式产物必须由代码归一成唯一的 `SourceSpan{text,start,end}`，下游不再重新搜索 locator | `结构`（`novel/stages/stage2/normalize.py` + `novel/core/spans.py`） |
| R-3.3 | **原文以「点」不以「段」下发**：离散原子（要害台词 / 硬数字 / 物件 / 因果前提）可逐字下发；**连续文本段一律不下发** | `结构`（`novel/stages/stage4/beats.py::verbatim_pieces` —— 四类各归各组，**未知 kind 丢弃并交调用方报出**）<br>⚠ **F5-5 改判**（原阻断级）：这一条守在**下发那一刻**，不守在产物上。它此前有一版 `HINGE_GROUPS.get(kind, 'verbatim')` 的兜底，把模型自造的 kind（实测「行动描述」）**升级**成「原样写入，一个字都不能改」——而那正是一句叙述文，正是本条要挡的东西。⚠ 取值面那一半归**载体**（`R-3.6`），本行只管「段不许当点发」；**「点有没有真的发出去」是另一条**（`R-3.3b`） |
| R-3.3b | **抽到的点必须条条出现在下发面上**：stage2 有多少个保真点，写作上下文里就必须有多少个（比条数不比文字——组装最后一步的确定性改名会让文字合法地变） | `HARD`（`ctx_hinge_delivered`，分母为零走 `unver`）<br>⚠⚠ **它是拿一次真事故换来的，而那次事故里 `R-3.3` 与 `R-3.6b` 两条都在、都绿。** `beat_index` 曾把 hinge 归一成 `{kind,text}` 这个中间形态，而那个键名与已废弃的旧 stage2 契约同名，一次「清理遗留兼容」（`08240cf`）把读它的分支删掉后，**五本书 898 条 hinge 一条都没能下发**：`R-3.6b` 在上游看 stage2 产物（材料齐的）、`R-3.3` 在下游看函数（代码还在），**中间这一步的丢失不在任何一条的扫描面里**；`R-7.3` / `R-7.4` / `R-7.17` 三条 HARD 又因分母同时归零而一起报绿，闭卷写手手里一个原文字节都没有。⇒ **跨越一步的丢失，只能由一条横跨那一步、比两边条数的判据抓住**（`docs/LESSONS.md` 第 22 条） |
| R-3.4 | 一条跨段落的「要害台词」下发前**必须按段落边界确定性拆成独立的点**。实测一章 4 条跨段原话被写作节点**一条不剩地全丢** | `结构`（`novel/stages/stage4/beats.py::verbatim_pieces` —— 与上一行同一个函数，本行说的是它里面按段落边界的切分）<br>⚠ **F5-5 改判**：它与上一行是**同一段代码的两半**，而那段代码**今天就在仓里**——F5-0 当时判断落点「在本层的模块目录里」是错的：拆点发生在 `stage4` 的下发投影里，不在 stage2 的产地。⚠ 那份实现是**唯一一份**：真正发给写手的字节与告诉规划器「这一拍有多少字压不掉」的那个数**都调它**，各写一份就会让报给规划者的数与写手实际收到的字节对不上 |
| R-3.5 | Beat span、fact atom evidence 与 hinge 原文字节**只能由确定性代码按响应 locator/phrase 回取**，不许 LLM 转抄；正式 `SourceSpan{text,start,end}` 晋升前必须验证 offset/text 一致并落在所属 beat 内 | `HARD` |
| R-3.6 | 保真类型是闭合枚举，**兜底分支不许静默通过**——把写歪的类型兜底成「原样写入，一个字都不能改」，会把一段叙述文变成逐字要求 | `载体`（`contracts/schema/stage2.py::Hinge` 的 `kind` 是 `Literal[HINGE_KINDS]`，叙述层同理落在 `Beat` 上）<br>⚠ **F5-0 改判**（原判阻断级）：取值面枚举归载体是 `contracts/schema/stage2.py` 文件头已立的规矩，判准是**失败成本**——拒收＝整份响应作废＝再花一次钱，而那要在生成下一章之前就停住。「兜底分支不许静默通过」由 `Literal` 保证：没有 `dict.get(kind, 默认值)` 可写。⚠ 本格措辞刻意避开那七个记号字面，否则子串匹配会让它同时声明三种取值 |
| R-3.6b | **一整章一条保真材料都没有 = 失败**。凡有对白、或交代了因果/身份/利害关系的章几乎不可能一条都没有；而保真材料是闭卷写作**唯一**的通道，整章为 0 等于这一章的原文材料一条都没往下游走。另报全书抽取密度——⚠ **按原文字数归一，不按拍数**：拍数是同一个 prompt 都能改动 ±44% 的自由变量，拿它做分母量到的是切分粗细不是材料多少 | `HARD` |
| R-3.6c | **全书 0 条「现场」层是结构性不可能**——一本书总得有一条主线，全判成回忆/转述只有一个来源：模型按**叙述时态**判层，而这个字段判的是**叙述层次**（回顾式叙述的主线本身就是现场）。代价确定：全书没有一场现场戏（实测某本 0/70）。**枚举合法 ≠ 分布合理**，所以分布本身也要无条件报出 | `HARD` |
| R-3.7 | 在场人物与叙述身份只保存人物 id，不保存可由 registry 解出的显示名 | `载体`（`contracts/schema/stage2.py::Beat` / `Narration`） |
| R-3.7b | 在场人物和叙述身份只使用截止本章可见的关闭式 `char_id`。正式产物不保存显示名，复检只查 ID 是否悬空；无显示名时 `--renormalize` 不得猜测人物身份 | `HARD`（`present_ids_dangling`）+ 响应闭集校验 |
| R-3.7c | 在场标注的松紧与连续性两个读数。⚠ **两个都不判阻断**：按它去修数据会让另一半更差——「内心剖析某人」时逼着把他列进在场，就是造一个不存在的在场者。⚠ 且窄筛依赖地点字面相等，**系统性漏检，0 条不等于没有漏标**。另有一条「在场者在本章原文里有没有字面依据」——**它不是幻觉检测器**（三本 128 对报 1 条，逐条核过、幻觉 0 例），它实际抓的是名册漏收与后见之明标注 | `INFO` |
| R-3.7d | **后见之明的两条通道**：① 在场者早于该角色在原文里被指名的章 ② 本层自己写的散文里就带着晚出现的名字。**第二条下游堵不住**（在下游做文本替换会摧毁人名替换那条构造性安全论证），只能从这一层修。⚠ 第一人称叙述者是**系统性合法反例**（通篇写「我」，名字晚出现是常态），必须豁免且单独计数 | `INFO` |
| R-3.8 | 章首章尾必须被第一/最后一拍覆盖 | `HARD` |
| R-3.8b | **且抽取 prompt 必须明说这一条**——三本书 5 处漏抓全在章首、0 在章尾。现役边界相要求两张表都从第 1 段起步、严格递增并到达最后一段。⇒ 教训不变：即使 prompt 已明确要求，仍没有遵守率保证，守结果的是 `R-3.8` 的确定性判据；不要重复堆叠同义提示 | `结构`（`novel/stages/stage2/prompts.py::BOUNDARY_SYS_V2`；`tests/test_stage2_prompts.py`） |
| R-3.9 | ≥300 字却只有一拍的**切分坍缩**继续 `HARD`；章级「字/拍超过本书中位数 3 倍」只是一条长尾读数，不能独立证明漏抓，故 `beats_coarse` 只报 `INFO`、没有付费修复路由。真正的证据错拍、漏抓与区间失败仍由各自 HARD 阻断 | `HARD`（`beats_collapsed`）+ `INFO`（`beats_coarse`） |
| R-3.10 | fact claim 主谓宾明确、不省略施受关系；一个动作/决定/结果/揭示/状态变化各自拆成最小可判命题；原文自相矛盾时如实拆记，不替它调和；摘要不照抄原文措辞 | 原子引用/证据面有守卫；语义最小性 **无守卫 → `B-07`** |
| R-3.11 | 定向重生成（只重跑某几章，下游引用面零扰动）**必须配「不读缓存」**——它的 prompt 与整跑时该章那一批逐字相同，缓存必然命中，于是一个字都没重新生成而它照样打印「已重生成」。该约束在首个 LLM 调用前直接拒绝错误 cache policy，不再为已被前置条件排除的状态持久化第二份修复历史并设置恒绿后验判据 | `结构`（`novel/stages/stage2/extract.py::run_blueprint`） |
| R-3.12 | 定向重生成被回归护栏驳回之后**没有下一条路** | **无守卫 → `B-18`** |
| R-3.13 | 每章必须有非空 `chapter_function` 和 `fact_atoms`。每个正式 atom 有非空 claim、关闭 kind/obligation、同章 source beat 与可回取证据。`Beat.event` 与人读摘要均不进正式 schema，避免第二份事实源 | `HARD`（`fact_atom_empty` / `verbatim` 证据账）+ strict schema |
| R-3.14 | **一章内出现一个以上叙述声音时必须出声**——报的不是「这本小说有问题」（章内接一段他人视角是合法写法），而是**本层不记段界，写作节点只知道分几段、不知道界在哪**。番外由 `R-0` 剔除，但剔除词表**不含「他人视角」「书信」**，所以这条不是不可能发生；它只是从没被观测到过（全语料 + 盘上 31 章零样本） | `INFO` |
| R-3.14b | 叙述者**自称的字面**必须照原文用字（「我」/「咱」/「本宫」/「朕」），第三人称交空数组合法。凡是契约要求逐字照抄的就该有一条确定性检查盯着，而不是只在 prompt 里叮嘱——实测 262 个照抄锚点 30 个对不上，**prompt 劝说压不住** | `INFO` |
| R-3.15 | **下发材料必须是完整的词句**。hinge 回取只接句末标点，不接冒号、开引号或孤立闭引号；精确 source 的 offset/text 一致性在 Stage 2 晋升前由 `SourceSpan` 守住 | `载体`：`contracts/schema/stage2.py::SourceSpan` |
| R-3.16 | 逐章产物必须与正文章**一一对应**：不缺章、不多章、不重复、顺序一致，且**不得出现上游的分节键**（章的唯一键全链只有一个）。⚠ 两份 stage2 产物**齐备才跑得动**——缺一份判**失败**而不是提示：什么都没查却打通过，正是「一条从不执行的判据与一条不存在的判据在报告上长得一样」那个病 | `HARD` |
| R-3.16b | 已登记架构只保留 `extraction-ir-v2`、`extraction-ir-v3.1`（唯一落点 `contracts/invariants.py::BLUEPRINT_ARCHITECTURES`），当前默认 v3.1。共同运行边界是稳定全书索引前缀 + 当前章全文 + 最多 4000 字相邻过去原文 + 最多 1600 字更早连续性尾摘，未来章正文不可见。产物只常驻下游可用来识别产地的 `architecture`、`stable_prefix` 和三个过去窗口上限；调用拓扑由 `ContractSpec` 显式登记，越界由 `scope_violations` 无条件记录。不保存可从架构与运行参数直接推导的批次/布尔字段 | `HARD`（边界）+ `度量`（`context_scope`） |
| R-3.17 | 转场点由地点字面推导、**不单存字段**，且它的读数要带着自己的偏差一起报——地点是自由文本、粒度不稳（街上/街边、公寓/公寓卫生间门口），按它相等推导出来的转场数**系统性高估**。⚠ 它作为**下发给写手的舞台信息**是对的，不改；但凡引用这个数的地方都要带上这一句 | `度量` |
| R-3.18 | **落盘前的原文回取必须把失败态正面留账**：span/hinge 与 fact atom 分开记录稳定根因，不能只靠 `source` 缺席反推。fact atom 的原始 `evidence_phrase` 若在声明 beat 内零命中、但在全章恰好唯一命中且完整落入恰好一个其他有效 beat，compiler 必须零 LLM 重写 `source_beat_ref` 并落精确 `SourceSpan`；全章零/多命中、跨边界或不能唯一归属时仍留正向失败账并把问题稿落 `.INVALID`。存量 INVALID 的 `--revalidate` / `--renormalize` 只能从与不可变来源账、当前 schema/system、精确 phase↔purpose↔chapter、accepted request/attempt/完整 purpose ledger 及当前 MainText/称名投影重编译结果逐字段闭合的唯一 checkpoint 恢复原 phrase；reasoning 救回稿还必须逐字命中书内 `_answer_lost` 原响应，并由同 run/attempt 的唯一 `answer_lost` 审计指针把其 content 绑回 ledger response hash。不得从转述 claim 猜 locator，也不得只靠可重签的 checkpoint self-hash 给 reasoning 正文作证 | `HARD`（`verbatim_failed`）+ compiler 正反例 + checkpoint/ledger 0-wire 恢复回归 |
| R-3.18b | 成功的精确/内容空间回取由正式 `SourceSpan{text,start,end}` 表达，不重复持久化只供展示的改写清单；失败根因分别落在 `verbatim_failures` / `fact_atom_failures` | `载体`（`contracts/schema/stage2.py::SourceSpan` / `BlueprintProvenance`） |
| R-3.19 | blueprint 的模型请求、内容恢复与 Flash→Pro 兜底都以**单章**隔离；一章失败不得取消其他已起跑章。每章只有依次通过 JSON parse、必需键、当前 Pydantic、dynamic validator 与章节 scope 切分后，才原子写入同一 `run_id` 下的 `_stage2_runs/<run_id>/blueprint/checkpoints/<phase>-chNNNN.json`；普通 content 与完整通过的 reasoning salvage 均保存 accepted channel、原始 attempt/model/request 指纹、原文/payload SHA、contributing attempts、recovery mode 与 `salvaged`。该目录是内部 v1 sidecar，不是正式 artifact 或 `.llm_cache`，不参与完成度、schema 版本、缓存命中或正式 provenance。resume 必须显式复用原 `run_id`：恢复时先核对代码/契约/架构/输入/request/SHA 与 ledger 联结，再重新跑当前 schema、validator、scope、整书 compile 与 global+blueprint 联合 HARD；任何损坏、跨 run 或漂移都关闭式停止，不能退化成 cache miss 后重新付费。主动 `--chapters` 刷新首次进入必须使用全新 run，不得命中先前逻辑运行的 checkpoint；只有本次 refresh run 已生成唯一 manifest、并完整封存 batch resolution 后，显式同 `run_id` 的唯一人工 resume 才可恢复**本轮新采**的成功章，不能把普通重复执行冒充 resume。并发波次全部结束后先保存所有成功章，再把全部 `ResponseRepairRequired` 稳定排序为唯一 immutable manifest；decisions 必须完整一一覆盖 attempt 集合并逐项给 `submit|decline` + 非空理由，候选及 SHA 经一次性全量校验后只原子写一份 batch resolution，batch attempt 禁止再走单项 submit。每个逻辑 run 最多一次人工 workpack、一次人工 resume；恢复后再失败只能进入已登记自动动作或终态，v2 第二相也不得制造第二次人工暂停。正式 `chapter_blueprints.json` 仍必须覆盖完整章集；全书 artifact 已写而提交哨兵未写时按 artifact 指纹幂等收尾，不新增 `_rounds` 或重复 adoption | `结构`（`novel/stages/stage2/extract.py::_run_chapter_requests` / `_raise_response_repair` + `novel/llm/stage2_runs.py`）+ 检查点零 wire/损坏漂移/八章单 workpack/单 resolution/单 resume/幂等提交回归 |
| R-3.20 | `land` 是本章新增且必须落地的事实，`context` 是章首已成立状态；`entry_state_refs` 只能引用 context、`ending_state_refs` 只能引用 land。因果边引用必须闭合、不得自环或成环；数组位置只表示证据位置，不是未来写作顺序 | response validator + 正式 `Blueprint` 载体 |

> **R-3.3 的判据出处**：离散原子无法被模仿成文风，只能被放进去或不放进去；
> 连续文本段一律不下发——写作节点必然沿着它的句序和节奏走，那正是重写要避免的。

> **⚠ 定向重生成的成功率不是 1**：实测 9 次 → **7 次修好 · 1 次无变化 · 1 次净负**
> （一次重生成后章首仍未覆盖；另一次覆盖率 98%→90% 且在别处新添一处锚点失败）。
> 报「定向重生成能修好它」时要带上这个基数。

---

### R-4 · 即时机械测量（无 Stage3 产物）

Stage5/6 从各自已经读取的 Stage1 文本即时计算确定性机械读数；测量函数保留，持久化 Stage3 整层退役。

| # | 要求 | 谁在查 |
|---|---|---|
| R-4.1 | **形态数字由代码算，不是 LLM 产**：正文与文案的字符、段句长度、对白与引号体等读数都由当前代码现场计算；没有可漂移的机械缓存 | `结构`（`novel/core/measure.py::measure_body` / `novel/core/measure.py::measure`） |
| R-4.2 | 正文与开篇文案段**分开测、互不污染**：Stage5 只测完整正文，Stage6 只测原作文案 | `结构`（`novel/stages/stage5/review.py::load_book` / `novel/stages/stage6/hook.py::_Upstreams`） |
| R-4.3 | **没有现役 Stage3 节点或产物**：`stage3.style` 与 `stage3/style_grounding.json` 只登记为退役身份，不能由 CLI、队列、API 或 producer 重新创建 | `结构`（`contracts/invariants.py::RETIRED_NODES` + `contracts/artifacts.py::RETIRED_ARTIFACT_PATHS`） |
| R-4.3b | **【Stage5 侧】只消费正文机械精确投影**：即时结果只保留 `avg_sentence_chars`、`avg_paragraph_chars`、`paragraph_sep_chars`、对白比值/引号体、章均字数、逐章平均句长 min/max；对白适用性由 `dialogue_ratio is not None` 推出，不存重复布尔值。`shape_block` 只把这些值序列化为句子/段落/对白/章篇幅/章间差异 5 个正文档位 | `结构`（`novel/core/measure.py::body_style_metrics` + `novel/core/bands.py::body_bands` + `novel/stages/stage5/review.py::load_book`） |
| R-4.4 | **Stage6 只消费原作文案机械五叶**：`chars`、平均句长、平均段长、引号体、对白比例；生成与机械复检共用同一次即时投影，不序列化完整测量对象 | `结构`（`novel/core/measure.py::hook_style_metrics` + `novel/stages/stage6/prompts.py::build_material`） |
| R-4.5 | 测量模型只是**进程内 strict 载体**，不带 `_schema_version`、`_provenance`、prompt、模型或 usage，也不进入 artifact registry | `载体`（`contracts/schema/measurement.py`） |
| R-4.5b | 旧 Stage3 v2 只保留冻结的 strict 解码器，用于解释历史来源边和显式审批迁移；它不是当前 schema、producer 或迁移后的新产物 | `载体`（`contracts/schema/legacy_stage3.py::decode_legacy_stage3_v2` + `contracts/schema/__init__.py::ARTIFACT_SPECS`） |
| R-4.6 | 原作文案是**必填机械输入**；无文案段已由 `R-1.12` 挡在 Stage1，本层不造 nullable hook 或空兜底 | 见 `R-1.12` |
| R-4.7 | 当前机械测量不独立记 provenance；Stage5/6 各自的来源账直接记录真正读取的 Stage1 主体 | `结构`（`novel/consumption.py::stage5_main_text` / `novel/consumption.py::stage6_hook_copy`） |
| R-4.8 | 测量算法口径是独立版本轴；只改算法、不改 Stage1 JSON 形状时，下游也必须自然 stale | `结构`（`contracts/invariants.py::STYLE_MEASUREMENT_CONTRACT`） |
| R-4.9 | 当前测量的真实上游只有 Stage1 正文或 Stage1 原作文案；测量本身不等待、也不消费 Stage2 | `结构`（`novel/stages/stage5/review.py::load_book` / `novel/stages/stage6/hook.py::_Upstreams`） |
| R-4.10 | 测量契约必须进入 Stage5/6 对相应 Stage1 文本的 consumed 投影，使算法改动与输入文本改动走同一 freshness 机制 | `结构`（`novel/consumption.py::stage5_main_text` / `novel/consumption.py::stage6_hook_copy`） |
| R-4.11 | 不再注册 Stage3 规则或 check 端点；即时读数只由 Stage5/6 的真实消费者继续进入 prompt、归一化或机械检查 | `结构`（`contracts/rules.py` + `novel/core/measure.py`） |
| R-4.12 | 不再生产 Stage3 `.INVALID`、轮次稿或修复命令；Stage1 变更后下游现场重新计算 | `结构`（`contracts/artifacts.py::ARTIFACT_PATHS` + `contracts/invariants.py::NODES`） |
| R-4.13 | 旧 Stage3 v2 字节不迁移、不重建，只能在读历史 Stage5/6 provenance 与显式审批 v4 backfill 时保守核验；核不准就拒绝兼容 | `结构`（`novel/store.py::_project_current` + `novel/orchestrator/approvals.py::_require_legacy_stage3_fresh`） |
| R-4.14 | `--measure-only`、Stage3 `--check-only` 与默认生产入口全部退役；诊断由 Stage5/6 的 0 成本检查展示实际消费后的读数 | `结构`（`contracts/invariants.py::RETIRED_NODES` + `novel/orchestrator/nodes.py::build_argv`） |
| R-4.15 | s3 闸、Stage3 API 与页面全部撤下。盘上审批为 v7、公开 API 为 v6；旧盘 v6 只能经显式 `backfill_from_disk` 收进 v7，保留可逐项对证的 s0～s4、seen 与 typed 历史，同时清除 s5～s7，绝不伪造 Stage5 闭合回执。Stage4A 设计审阅只授权 B，不打开 s4；Stage5 写作证明绑定当前 Stage1 正文、Stage4 计划、**经最终结构批准后单独生成的当前 WriterContext**和测量契约；s4 仍只由 B 编译后的独立 typed 计划结构裁决打开 | `结构`（`novel/orchestrator/approvals.py::backfill_from_disk` / `::authorize_plan` / `::authorize_context` / `::begin_writing` + `contracts/schema/api.py::ApprovalDoc`） |

> **`R-4.3b` 为什么必须写死消费叶子**：进程内测量对象有更多字段；只写「Stage5 可以发
> 实测数值」，发哪几项就成了自由变量。同形的坑已经踩过一次（写法词表在 prompt 里手抄子集，
> 模型按 A 算、校验按 B 判，两边都不出声）。投影闭合之后，多发或少发一个、把定性块接回来，
> 都是一次**契约变更**。精确叶子由 `novel/core/measure.py::body_style_metrics` 与
> `novel/core/measure.py::hook_style_metrics` 各自唯一声明。

---

### R-5 · 盲场景设计与证据绑定（stage4 · 两个独立付费节点；A 内含 1+N 个逻辑请求）

**只存决策，一个字内容都不经它。**

| # | 要求 | 谁在查 |
|---|---|---|
| R-5.1 | 输出章号 1..N 连续 | `HARD` |
| R-5.2 | **一章对一章**：源章列表长度恒为 1 · 源章序非递减 · 源章全覆盖 · 无源章被两个输出章共用 · **源章号 == 输出章号**（前四条的合取式，做成会报错的检查而不是一句注释） | `HARD` ×5 |
| R-5.3 | 篇幅方式恒为「保持」 | `载体`（`op` 字段**已整个裁撤**，常量回到 `contracts/invariants.py::LOCKED_OP`） |
| R-5.3b | 逐章预算之和落在源字数的 **±20%**（`PLAN_BUDGET_BAND`），**无豁免**（原型在带之外又乘了一次 ±10%，那第二层已裁掉）。⚠ 2026-08-22 从 ±10% 放宽：这是把「哪一章厚、哪一章薄」这个自由度还给 stage4，治的是「成稿只改了措辞」；**放宽的对价是同批的 `R-5.3c`**——不配它，全书总量从此无人守。⚠⚠ **2026-08-22（批 4）起它是代码的后置条件**：模型给章级档位（`CHAPTER_WEIGHTS`），整数由 `novel/stages/stage4/measure.py::allocate_budgets` 归一化 + 钳带算出 ⇒ 生成路上这条判据恒绿。**判据不撤**：`--apply-edit` 的人工稿与 `--revalidate` 的存量产物不经过那个函数，在那两条路上它仍然是真守卫 | `HARD` |
| R-5.3c | **全书**预算之和落在全书 span 合计的 `BOOK_BAND`（0.90~1.10）内。逐章带一放宽，「章章 ±10% ⇒ 全书必然 ±10%」这条蕴含就断了，而总量是对用户的产品承诺。⚠ `unver` 臂比 `R-5.3b` 严：**缺一章就不判**（少一章分子分母同时缺一块，得出的倍率既不能说合格也不能说不合格）。⚠ finding 必须打出**逐章倍率明细**，否则本层那条 0 成本恢复路名存实亡。⚠⚠ **2026-08-22（批 4）起同样是代码的后置条件**（同 `R-5.3b`）：全书总量恒等于全书 span 合计（带的中心 1.0），逐章档位只决定它**怎么分**。定向修复那一路把本章总量钉死在基线合计上，因此也顶不出带外——而从前它可以 | `HARD` |
| R-5.4 | Stage4A 的整书策略 `reconstruction_strategy`、逐章 `chapter_rewrite_brief` 与场景 `purpose` 只说**写什么与怎样组场**，不许用风格套话代替结构决策。**事实词义不是写法指令**：与该字段可引用的 typed fact claim 连续对齐、且 payload 不短于 8 个字母/数字的成对中文引文内的命中不计（`purpose` 只认本场 land/context claim，`chapter_rewrite_brief` 只认本章场景实际引用的 claim，整书策略 session 看不到 fact claim ⇒ `reconstruction_strategy` 无豁免）；无引号、短引号、跨场跨章与同字段第二次无据出现一律照红。A 策略不进入正式计划，Stage2 冻结的 `chapter_function` 只读、不由 Stage4 改写 | `HARD`（`style_words`，A 相与正式复检共用 `novel/stages/stage4/checks.py::style_word_findings`） |
| R-5.5 | Stage4 正式生产是两个**独立公开付费节点/授权**，不是两个逻辑请求：`stage4.design` 仍只有一条公开 CLI dispatch、一次预览/`--yes` 授权，内部固定为 **1 条整书策略请求 + N 条逐章私有 session**；策略验证并 checkpoint 后才扇出逐章请求，逐章硬上限 4 并发，每个 unit 独占 provider session、purpose、ledger attempt 与验证后 checkpoint。A 通过机械闸并原子落盘后，必须先提交独立、hash-bound 的正式 `design_review`：全书策略决定为 `accept / request_replan`，逐章决定为 `accept / request_repair`；它只授权下一次 B，绝不打开 s4。只有同一全局 review basis、策略主体不变且章主体逐字相同的旧 `accept` 才能预填复用，并仍须对当前整书主体原子提交一次；全局 basis/策略变化使全章过期，单章业务变化只使该章过期，旧拒绝不静默变接受。`request_repair` 必须带关闭式修复码及章内 scene/opaque-alias 证据，CLI 以当前决定 SHA-256 的 `--repair-decision` 只重跑被拒章并复用其余 checkpoint；策略被拒才允许绑定同一决定的 `--replan` 整书重做，裸刷新或把自然语言理由直接发给模型均拒绝。`stage4.plan` 仍是一条独立逻辑调用，只把已审阅接受的原子候选绑定证据并确定性编译为计划；A 完成或审批完成都不得静默连跑 B，批量队列必须重新 plan/preview/confirm 才能得到 B 的付费 argv。A 的策略 prompt 只见同号章 `chapter_function` 与确定性计数，不见 fact/人物 alias；单章 prompt 只见冻结策略、本章去序真相，以短小、稳定、局部且不含源序含义的 opaque alias 引用事实与人物；两类 prompt 均不得出现原文、SourceSpan、`source_beat_ref`、beat/stage 或材料。传输、内容重采与定向修复仍可能产生额外 wire，必须逐条进 ledger | `结构`（`contracts/invariants.py::NODES` 的 `stage4.design` / `stage4.plan` + `novel/stages/stage4/prompts.py` + `novel/llm/stage4_runs.py` + `novel/orchestrator/approvals.py::approve_design` / `::resolve_design_action` / `::authorize_plan` + `novel/orchestrator/queue.py::plan_book`） |
| R-5.6 | 计划不复制 `semantic_contract`；需要它的 Context 与 API view 每次从当前同号 Stage2 blueprint 派生。Stage4A 每个单章响应先独立校验本章 scope/alias/schema/动态语义并写 checkpoint；策略与全部章节齐全后，代码才还原正式 alias、按章排序、注入派生字段并聚合整书候选，再重跑整书 strict schema 与全部 HARD。每个 land 原子和 material 恰落一个新场景；`context_fact_refs` 只能引用**此刻读者已经知道**的事实（`obligation=context` 的原子，或已在**更早**场落地的 land 原子）——本场或后续才落地的一律红，那是提前引用未披露事实；context 可跨场复用；引用只限所在计划章；首尾 goal 落在新首尾场景；**章首输入与章尾输出闭合恒为 HARD**——章尾比的是「本章有没有让它成立」（落地即成立），不是「末场有没有把它再抄一遍」；章内状态承接按 `transition` 说的**故事时间**判，且**由代码派生而不是问模型**：模型只交本场新增的最小状态，编译器按 `transition` 的定义补全（首场承接章首状态、`continue` 承接紧邻上一场有效 exit、唯一无歧义支路的 `return` 恢复锚点有效状态），于是这三支成为**后置条件**——`--apply-edit` 的人工稿与 `--revalidate` 的存量产物不经过编译器，那两条路上它们仍是真守卫。支路配对不唯一时不派生，只报 `unver`。判据本身一条不撤：只有 `continue` 强制承接紧邻上一场退出状态，唯一无歧义的 `earlier|parallel → continue* → return` 支路检查锚点恢复，其余非线性边界（第二个 opener、支路内 `later`、章末未收口）产 `unver` 并把逐项证据交结构人审，**不得洗成 PASS**；Stage2 因果边两端事实必须落进实际场景，端点可由 land 或 context 引用入场、多次出现取首次可见场 | `HARD`（`fact_ref` / `fact_coverage` / `causal_closure` / `state_closure` / `material_coverage` / `goal_landing`） |
| R-5.7 | Stage4A 策略响应只产全书 `reconstruction_strategy` 与完整章权重表；每章响应由请求身份注入章号与冻结权重，只产本章 `retain/reconstruct` 决定、三个 local-alias goal、brief 与按新披露序排列的非空候选 `scenes[]`，不回显章号、权重或全书策略。代码把 local alias 映回正式稳定 alias，注入 `output_ordinal` / `weight` / `candidate_scene_id` 并按章确定性聚合；只有 N+1 个 unit 全部验证并 checkpoint、聚合后整书 schema/HARD 全通过，才原子写入完整 `blind_candidate.json`，中断、缺章或失败不得写部分正式候选。goal 不存展示 rationale；候选场没有 material/stage refs，不能冒充最终计划 | `HARD`（`empty_chapter` / `budget_int`）+ strict schema（`contracts/schema/stage4.py::BlindBookStrategyResponse` / `::BlindChapterDesignResponse` / `::BlindCandidate`） |
| R-5.8 | Stage4B 才看原文证据、源舞台、人物与必须材料；它只能按 `candidate_scene_id` 回填 `stage_refs` / `material_refs`，人物到场统一从绑定舞台的 `present` 推导，并回显冻结候选完整 SHA-256。B 不得改变 A 的章/场顺序、事实分组、首尾锚点、goal、`retain/reconstruct`、purpose/role/delivery/transition 或 weight；能绑定则返回完整 `bound`。模型可以另报 typed `binding_concerns` 供最终结构人审查看，但 concern 不阻断编译、不能冒充 infeasible。模型返回 `infeasible` 只是一项待核证主张：代码必须在同一输入目录上构造性求解；当前合法约束下，每章至少一项舞台、每场可复用首项舞台、全部材料可唯一落到首场，因此通常可证可绑定，错误的负结论必须在同一 session 拒收并要求完整 bound。只有代码能产出关闭式 `BindingInfeasibilityWitness`（例如章目录缺失或合法舞台集合为空）时才允许正式落盘 infeasible；其余任何自由文本、悬空引用、部分 binding 或静默重规划均拒绝。另提供 0-wire `stage4.plan --apply-binding <path>`：人工提交同一 `EvidenceBindingResponse` 业务形状，服务端铸造 `origin=human` 来源账并复用同一 compiler/HARD | `结构`（`contracts/schema/stage4.py::EvidenceBinding` / `::BindingInfeasibilityWitness` + `novel/stages/stage4/prompts.py::evidence_bind_system` + `novel/stages/stage4/blind.py::prove_binding_infeasible` / `::validate_infeasible_binding` + `novel/stages/stage4/plan.py::run_apply_binding`）；自由文本泄漏风险仍 **无守卫 → `B-06`** |
| R-5.9 | 场景详略由章级与场级 `weight` 决定，整数预算由代码分配；“多少场算过多、何种详略最好、哪一种顺序更好”没有 ground truth，不设自动审美阈值 | **`人`**；预算硬门见 `R-5.3b/c`，分布见 `R-5.9b`；旧盲区见 `B-17` |
| R-5.9b | 场景预算与原文舞台 span 的分布、章内轻重必须常驻报出；读数不自动升级为质量结论 | `度量`（`scene_budget_distribution`） |
| R-5.10 | A 与 B 各只在自己的中间产物记录来源账与 usage；A 的正式 provenance 聚合本 run 下策略/逐章 N+1 个稳定 purpose 为得到最终获采纳 units 发生的完整成本与实际模型集合，不伪装成一次供应商调用：除 accepted checkpoint 的 turn/recovery links 外，同 purpose 先前无 checkpoint 的失败 session 成本也必须计入，且部分恢复、全恢复、定向修复与 aggregate prepared 尾窗重启都要重建出逐字相同的 usage。逐 unit 的 accepted attempt、provider session、request identity 与 usage 仍由 ledger/session audit/checkpoint 精确联结；定向修复另绑定审批决定 SHA、被替换单元与其基线 unit SHA，其余单元只引用既有 checkpoint，不复制冒充新调用。B 的章级路同样不得伪装成一次供应商响应：`architecture=chapter_baseline_v1` 时，机械章记 baseline、歧义章记实际 accepted attempt/model/request 与 replacement lineage；全机械书模型集合及 usage 为空。旧整书路显式保留 `whole_book_v1`；`--apply-binding` 明确记 human provenance，不伪造模型、prompt 或 usage。最终 `plan_chapters.json` 是代码编译产物，只记 `FreeProvenance(origin=compiled)`，同时绑定 A/B 完整 SHA-256 与实际 consumed inputs，**不得复制 B 的模型/usage 冒充计划自身调用**。人工整份替换记 `origin=human`，不得继续冒充旧 A/B 编译结果。问题稿与 0 成本提升稿同样带来源，ghost input 必须显式报出 | `结构`（`BlindCandidateProvenance` / `EvidenceBindingProvenance` / `PlanProvenance` + `novel/llm/stage4_runs.py::design_aggregate_usage` / `::plan_chapter_*` + ledger/session checkpoint + `novel/prov.py::FreshnessReport`） |
| R-5.10b | **写作上下文照的必须是盘上这份计划**：两处各存一份 goals，手改计划而不重建上下文就会分家。四态分开报——对得上 / 照的是另一版 / 没记这条边 / 上下文不在场 | `HARD` |
| R-5.10c | 校验器要为**两份产物各报一行来源**：报告第一节只覆盖被复检的那一份，另一份的来源结论要另有一行常驻（空也要报） | `度量` |
| R-5.10d | `origin=compiled` 的计划在 freshness、Stage4 全判据复检和正式写盘后置条件中，都必须把 provenance 保存的两枚完整 SHA-256 与盘上**当前 A/B 版本化文件的原始字节**逐一比较；缺失或不符即关闭式阻断，不得因 `artifact_payload` / `inputs[].consumed` 的业务投影仍相同而放行。`origin=human` 不声明 A/B 编译身份，跳过这两条 exact 边但仍走普通 provenance freshness | `HARD`（`phase_artifact_link` + `novel/stages/stage4/phase_binding.py::verify_plan_phase_artifacts`） |
| R-5.11 | 编译参数、fact atom 数、源舞台数与结构算法版本均可由当前代码和上游现算，不进入计划或 provenance；结构读数必须现场计算，算法读数变化不得改写计划字节 | `载体`（strict schema + `novel/stages/stage4/measure.py::structure_metrics_of`） |
| R-5.11b | `blind_candidate.json` 为 v2、`evidence_binding.json` 为 v3；`plan_chapters.json` / `plan_edit.json` 与 writer context 为 v4；identity artifact 退役。旧版本关闭式拒读、不迁移、不手补字段：正式计划由 `stage4.design` → 设计审阅 → `stage4.plan` 重建，writer context 只在最终计划结构审批后由 `stage4.context` 免费重建；人工绑定恢复提交当前 `EvidenceBindingResponse`，人工计划恢复提交当前 v4 `plan_edit.json` | `载体`（`ARTIFACT_SPECS` + `tests/test_artifact_versions.py::_SHAPE_BREAKS`） |
| R-5.12 | land 原子不得重复落场或漏场；同一个复合源 beat 可以拆成多个原子分给不同场，场景也可合并不相邻原子 | `HARD`（`fact_coverage`） |
| R-5.13 | 一场的预算不许被必须逐字写入的原话挤满：低于 2 倍只提示，不自动改场或改事实 | `INFO`（`verbatim_starved`） |
| R-5.14 | 原话占场景预算的分布要无条件报出；阈值没有独立质量真值，读数不作放行门 | `度量`（`verbatim_share`） |
| R-5.15 | 结构变化必须从同一个逐章函数复算且带分子、分母与 `unver`：事实→场景分区用全部 land-fact pair 的 split/merge 距离；披露顺序只比较 SourceSpan 严格可排序的 fact pair，并把同场 tie 与逆序分开；非连续重组按同一新场覆盖多少段互不相邻 source locus 计算。事实映射不闭合、SourceSpan 不可判或分母为零时不得产漂亮比例 | `度量`（`partition_change` / `disclosure_order` / `noncontiguous_regroup`，唯一数学落点 `novel/stages/stage4/measure.py::structure_metrics_of`） |
| R-5.16 | `causal_edges` 约束故事成立顺序，不等于披露顺序；披露先于因果前提可能是合法倒叙，必须报逆序读数但不自动阻断。先后一律按端点的**首次可见场**算（`context` 可在多场复用，取最后一场会让先后取决于数组尾部） | `INFO`（`causal_disclosure`） |
| R-5.17 | 场景 `transition` 必须是关闭式可执行标签，首场必须 `start`；回溯/返回的审美是否合适无 ground truth | `HARD`（`scene_transition`）+ 人工盲读 |
| R-5.18 | 首尾锚点必须同时报 typed goal 相对源首尾 locus 的迁移，以及首/末新场 payload 是否仍含源首/尾 locus；另单报每章 source locus/源分组数、跨组可比较事实对、因果传递闭包约束/不可比对与非连续重组机会，作为“本章是否有重构空间”的保守分母。它们不以场景数量变化、合并两个相邻场或字面低重合冒充重构，也不自动判优劣 | `度量`（`anchor_migration` / `structure_eligibility`） |
| R-5.19 | 每章必须显式记 `structure_decision`：`retain` 的声明变化为空；`reconstruct` 必须从 `entry_anchor / ending_anchor / fact_grouping / disclosure_order / noncontiguous_regroup` 中声明至少两类。计划只存 mode/reason/declared；`computed_change_kinds` 每次从当前 blueprint 与实际场景现场计算，声明已实现、未实现或出现未声明变化常驻对证，但不回写计划，也**不把“至少两项实际变化”或整书距离写成未经标定的自动美学门**。事实/状态/因果闭合仍由 HARD 守 | `度量`（`structure_alignment` / `semantic_closure_reading`） |
| R-5.20 | Stage4 有两次相互独立、主体不同的人审。第一次是 `R-5.5` 的 A 设计审阅，只决定候选是否可交 B；第二次发生在 B 已绑定并编译计划、机械检查通过之后：服务端给出包含计划业务面、现场结构对证读数及 B `binding_concerns` 的完整结构 subject SHA-256，人提交 actor、总理由以及**恰好覆盖当前全部章**的 `accept / request_replan` 与逐章理由。全部 accept 才打开 s4；任一 request_replan 只保存决定并保持 s4 关闭，绝不自动启动或付费重规划。通用 `/gate/s4` 必须拒绝；决定同时绑定结构 subject 与当前计划完整字节，计划、上游结构证据或 concern 变化后即时 expired。计划的生成、`--revalidate`、`--apply-edit`、`--apply-binding` 均不得顺带生成 Context；只有当前最终结构决定全部接受后，`stage4.context` 才能单独、0 成本落盘。Stage5 的任何付费写作在首个 LLM 调用前必须同时验证当前 s4 结构批准、fresh Context 和独立 writing 授权；0 成本 check/人工编辑/阅读面不得被挡 | **`人`** + `结构`（`contracts/schema/api.py::DesignReviewRequest` / `::StructureApprovalRequest` + `novel/orchestrator/approvals.py::approve_design` / `::approve_structure` / `::authorize_context` + `novel/cli/main.py::_cmd_run`）；没有自动审美阈值 |

| R-5.21 | Stage4 的运行时失败必须携带**闭合式 typed witness**：`novel/llm/typed_failures.py::TypedFailure` 九字段冻结（candidate_sha256 / code / rule / chapter_ordinal / scene_refs / refs / repair_owner / retryable / failure_fingerprint）。发射点 → `repair_owner` 的路由表唯一落在 `contracts/invariants.py::STAGE4_FAILURE_OWNERS`，未登记的发射点关闭式归 `code` owner。`repair_owner ∈ {stage4.design, stage2.blueprint, code}` 的失败在 B 首轮立即停止（不排队反馈、不消耗四轮预算）；B owner 回同一 session；同一 fingerprint 集**连续两轮**未闭合提前终止该 unit；`failure_fingerprint` 是语义身份（不含 candidate SHA），换候选重演的同一违约指纹相等。判据在已知章号的发射点必须给 finding 填 `output_ordinal`（章级判据按章拆 finding），typed witness 只从发射点结构化产出，**禁止从 detail 散文反推章节**。三份出口各取所需：完整反馈只回同一 provider；`_service/llm_audit.jsonl` 的 reject_detail 只追加无正文的 `code/chapter_ordinal/fingerprint` 读数；完整 witness 与被拒响应进现有 `_llm_rejected/<chain>/<attempt>/` 恢复面（`repair_eligible=false`，收口走 `--revalidate` / decision-bound repair / hash-bound replacement，不建第二份审计文件） | `结构`（`contracts/invariants.py::stage4_failure_owner` + `novel/llm/typed_failures.py` + `novel/stages/stage4/checks.py::check(witness=)` + `novel/stages/stage4/plan.py::run_plan` 的关闭式路由与 `::_keep_business_witness`）；`tests/test_stage4_typed_failures.py` 正反例回归查 |
| R-5.22 | Stage4A 候选在授权 B 之前必须经过唯一 **A→formal 零调用预检** （`novel/stages/stage4/plan.py::preflight_binding_candidate`）：从当前 A 与 Stage2 关闭目录构造零自由度构造性绑定（每场 catalog 首项舞台、材料全落首场），复用正式 merge/plan ref validator/WriterContext 组装与同一联合 HARD，返回绑定 candidate/blueprint/review-contract SHA 的 typed pass/blocked 结论，**不落任何新 artifact**。「B 选得不好」在该预检里结构性不存在；失败 owner 由 `R-5.21` 闭合表分类，不可行 witness 的两类约束码归 `stage2.blueprint`。调用点四处：A 审阅 workpack生成、approve 闸（blocked 或 missing 预检拒绝任何 accept；repair/replan 不受闸）、`authorize_plan`（复用现场 subjects 的预检投影）、`prepare_plan_candidate`（任何 driver/probe/provider 副作用前重跑）。元数据归一：`computed_change_kinds` 是权威代码读数，`mode/declared_change_kinds` 保留为设计意图只进 `structure_alignment` 度量（metric_only，无 fix 路由、不得生成付费 action）；泛化 `structure_delta` 退役为 `DESIGN_REPAIR_CODES_RETIRED`（历史可解码、新决定 approve 闸拒绝），新修复决定只接受带 scene/alias 的具体修复码；`binding_constraint_unsat` 必须有机械 witness（预检不可行证明或 fresh B infeasible 终态）支撑；`DESIGN_REVIEW_CONTRACT` 升 v2，旧 v1 决定经主体相等性自然过期，重审 0 成本 | `结构`（`plan.py::preflight_binding_candidate` / `::_preflight_blocked_text` + `novel/orchestrator/approvals.py::_design_preflight_summary` / `::_require_design_preflight_clear` / `::_require_design_codes_active` + `contracts/invariants.py::DESIGN_REVIEW_CONTRACT` / `::DESIGN_REPAIR_CODES_RETIRED`）；`tests/test_stage4_preflight.py` 与 `tests/test_design_review.py` 正反例回归查 |
| R-5.23 | Stage4B 内部拆为「机械基线 → 歧义章私有 session → 确定性聚合」，公开仍只有一个 `stage4.plan` 节点、一次预览和一次授权，不开放模型 `--chapters`。唯一机械基线落点 `novel/stages/stage4/baseline.py::build_mechanical_baseline`（纯函数）：scene fact → `source_beat_ref` → 舞台 allowlist，material → 属主 beat → eligible 场景；唯一归属由代码确定，签名等价的多候选取规范首项；只有 `stage_signature_conflict` / `material_multi_scene` / `material_unplaceable` 三类真自由度生成内容寻址 ambiguity id。机械章 **0 wire**（绑定由冻结输入确定性重算，不落章级 checkpoint——其价值是免重付模型调用，确定性重算零成本）；歧义章各占一条 single-session run/checkpoint 链（`stage4_runs.plan_chapter_run_spec`，`_stage4_runs/<run>/plan/chapters/chNNNN/`），真实 worker 池最多 `STAGE4_PLAN_MAX_CONCURRENCY=4` 并发，任何两章不共享 driver/logical/provider session；章 prompt 只含本章候选场景、本章相关原文/hinge、blueprint、关闭目录与基线歧义 + 相邻章一行边界摘要，**不含其他章正文**；模型只返回 `ChapterBindingResponse`（歧义逐项闭合 + 非阻断 concerns，无章号/hash/完整绑定面）。每个 worker 内按原 wire validator 完成 parse/require/schema/本章闭合，通过即 checkpoint；后章失败不得抹掉已验章，恢复只补未闭合 unit。全章闭合后按章号确定性聚合并过同一 compiler 与整书联合 HARD；typed 失败按 `R-5.21` 路由，B owner 且带章号只回该章 session；重问通过后必须同步重写 checkpoint、accepted attempt/model/request、unit lineage 与 aggregate anchor，不能让正式稿指向旧响应；同一 fingerprint 两轮早停。时间层冲突模型无法给出可执行选择时不采用任意 fallback，保留他章 checkpoint 转人工 typed 替换。该 replacement 必须绑定原 run/request/code/subject 与被拒 candidate SHA，消费一次性封存 resolution，复用同一 `ChapterBindingResponse` validator、compiler 与全书 HARD，0 wire 且不得伪造模型 provenance；整份 `--apply-binding` 仍是另一条明确人工入口。聚合 commit journal 锚定**最终获采纳锚章 checkpoint 链**（`stage4_runs.plan_chapter_commit_spec`——聚合响应是代码编译物不是 provider turn，不得在整书链伪铸 acceptance）。来源账 additive：`architecture ∈ {whole_book_v1, chapter_baseline_v1}` 与逐章 `unit_lineage`（kind 只取 baseline/chapter_session + attempt；人工 replacement 以同一 chapter_session 行的 resolution/candidate/audit 字段留证，不伪造第三种模型 unit）；全机械书落 `origin=compiled` 免费账（0 模型声明）；旧 `EvidenceBinding` v3 仍可读，旧整书单发路径保留且不作 fallback | `结构`（`baseline.py` + `stage4_runs.py::plan_chapter_*` + `node_agents.py::Stage4PlanSessionPool` + `plan.py::_run_plan_with_chapters` / `::_finalize_mechanical_plan` / unit replacement）；`tests/test_stage4_baseline.py`、`::test_stage4_plan_sessions.py`、`::test_stage4_plan_chapters.py` 正反例回归查 |
| R-5.24 | Stage4B 机械基线发现同一候选场的 source facts 对应多个不同 `time_layer/place/present` 签名时，必须为每个 `stage_signature_conflict` 常驻报出章、场、事实与全部候选签名；最终 binding 选中一个候选也不能让其从报告消失。该读数没有自动优劣阈值，只作为结构人审的完整披露面，不阻断编译、不生成付费修复动作 | `INFO`（`novel/stages/stage4/checks.py::check`） |
| R-5.25 | Stage4 两次人审各有一份只读、异常优先的 Commander 工作包 v1；公开面固定为 `GET /api/novels/:sid/approvals/design/workpack`、`GET /api/novels/:sid/approvals/structure/workpack` 与 0 成本 CLI `approval workpack design|structure`。工作包必须绑定当前 candidate/完整审阅 subject 与精确章全集；服务端复用生产 A→formal preflight、Stage4 check/metric（含 `R-5.24` 的完整 INFO）、机械 baseline、binding concerns 及审批历史投影，不在 façade 重拼判据，不预填决定、不自动 accept、不启动模型或作业。六组以 `blocking → semantic_attention → disclosure → metric_only → phase_deferred → coverage` 固定全集顺序出线，空组也保留并给扫描分母；coverage 恰覆盖全章、changed 优先且各段章号升序，changed 与 exact historical accept reuse 互斥并穷尽。条目携闭集 `sev/status/effect/allowed_actions`：actions 只可为 `accept / request_repair / request_replan`；INFO、metric、UNVER、skipped 及非 blocking/coverage effect 恒无动作，不能借工作包制造付费返工理由。Design 面突出 preflight witness、首尾锚点、因果/时间与 repair 章；Structure 面完整投影 B concerns、非线性 UNVER、所有 stage signature conflicts 和结构读数。Context 才能判的 `R-6` 规则只进 `phase_deferred(stage4.context)`，不进入结构 subject。Inbox 同步发布 Stage4 A/B 审批余额；已被 current hash-bound 结构决定实际覆盖的 disclosure 保留为 `reviewed_retained` 并附 typed decision 证据，不洗成 pending，也不从审计面消失 | `结构`（`contracts/schema/api.py::ApprovalWorkpack` + `novel/orchestrator/approvals.py::design_workpack` / `::structure_workpack` + `novel/api/routes/approvals.py` + `novel/cli/main.py::_cmd_approval` + `novel/views/inbox.py::inbox_view`）；API/CLI/schema/投影/Inbox 正反例与前端 seams 查 |
> **2026-08-25 替换边界**：旧 `end_beat → from_beats → events[]` 链已整体退休，
> 不是在 `PlanBeat` 上加字段。Stage2 v6 是事实唯一源；Stage4A 先脱离源序设计，Stage4B
> 只能给冻结候选安放证据，最终 v4 计划由代码编译。旧的拍合并率、`structure_changed`、
> 预算 reshape 与 v2 那五个失真的结构读数保留在 Git 历史，不再构成现役契约或阈值依据。

---

### R-6 · 写作上下文的确定性组装（stage4 · 0 成本）

**这是写作的唯一输入。** 它由代码按引用组装，不经任何 LLM。

| # | 要求 | 谁在查 |
|---|---|---|
| R-6.1 | **内容一律由代码按 typed 引用搬运，中间层不转抄**：`semantic_contract` 从当前同号 Stage2 blueprint 精确解析成章级人话契约；`scenes[]` 只携带 purpose、进入/退出态、required/context facts、turn、舞台与材料；不得出现旧 `end_beat/from_beats/events` 写作骨架键 | `HARD`（`ctx_facts_exact`） |
| R-6.1a | Context 是最终计划审批后的独立 0 成本产物：`stage4.context` 必须先核当前完整结构决定全章 accept，B 编译与所有计划修复入口都不得提前或顺带落它 | `结构`（`novel/orchestrator/approvals.py::authorize_context` + `novel/cli/main.py::_run_stage4`） |
| R-6.2 | 下发的人名**必须在原文里有据**：正典名无据时退回出现最多的那个叫法，都没有则显式终态；Stage4 Context 与 Stage5 全书残留名检测共用 `novel/core/naming.py`，Stage5 基线从当前 `GlobalGrounding + MainText` 重建，不复制 Context 顶层人物表 | `HARD` |
| R-6.3 | **逐章剧透闸门**：一条秘密下发的充要条件是「读者已在本章或之前知情」**或**「本章是埋点章」；后者只下发人话事实与「读者尚不知情」语义。Stage5 的未揭示边界只读 Stage2 权威时间线，不读取 Context 里的重复 secret id 清单 | `结构`（`novel/stages/stage4/context.py::_project_facts` + `novel/stages/stage5/checks.py`） |
| R-6.4 | 每个新场景必须带可执行舞台；**跨叙述层不压平**，人物到场只读取每个绑定舞台自己的 `present`，另存 participants 会制造漂移 | `HARD`（`ctx_stage_explicit`） |
| R-6.5 | 设定块也要逐章投影——它曾是唯一一个书级直发、无任何闸门的事实块 | `结构`（`novel/stages/stage4/context.py::_gate_setting`，纯字面子串包含，判别力见 `R-6.7`） |
| R-6.6 | 剧透屏蔽要**按信息盘点通道，不按字段名盘点**：同一批事实会从身份描述 / 关系生效章 / 世界规则描述 / 冲突阶段摘要 / 弧线多条旁路漏出 | `结构`（`novel/stages/stage4/context.py::_project_facts` 逐块投影，`novel/core/naming.py::person_naming_surfaces` 认全部别名而不只是正典名） |
| R-6.7 | 设定块的剧透闸门是**字面子串包含**，只挡转抄式 | **无守卫 → `B-16`** |
| R-6.8 | Stage5 可发明不改变状态的动作、走位、即时反应、对白措辞、感官/心理/调度与非持久细节；不得新增因果、决定、结果、关系、知识、往事、秘密、规则或会被后文引用的人/地/物/事。确定性层只覆盖新数字与新称谓探针，其余仍无语义真值 | 探针见 `R-7.5`；其余 **无守卫 → `B-01`** ★ |
| R-6.9 | **写作上下文里不许装「写作节点明确拒收」或没有独立作用的字段**：闸门要开在产物侧，不在渲染侧。Context 不含 A 策略、顶层 `world_facts`、逐场源字数/participants、内部 secret/rule id 或 `must_not_reveal`；`extra='forbid'` 关闭式拒收旧载荷 | `载体`（`contracts/schema/stage4.py::WriterContext`） |
| R-6.10 | **能 0 成本现算的不落进这份上下文**：书名 / 书 id / 拍密度 / 全书名称检测基线 / 未揭示秘密 id 集都从当前上游现场重建，存第二份只会制造漂移 | `载体`（同上 + `novel/stages/stage5/review.py::load_book`） |

> **`R-6.1`~`R-6.4` 为什么各配一条【后置条件】判据，而不是四行一起改 `结构`。**
> 组装器带着多道逐章闸、比分组函数大一个数量级，而它一条报告行都不产
> ——改坏了没有任何东西会出声。本文档自己有现役先例：`fact_coverage` / `material_coverage`
> 对缺失与重复引用分别出声，`state_closure` 对章级状态边界与 `continue` 承接断裂出声；它们都是正式场景计划的
> 后置条件，不是只给模型的叮嘱。同一条理由在这里更强，不是更弱。
>
> ⚠ 而 `R-6.5` / `R-6.6` **不补**：前者的判别力已被 `R-6.7` 量死（字面子串包含，命中 0/8
> 含一条**已知真泄漏**）——给它一条判据就是造第二盏说不清的灯；后者说的是一条**盘点方法**，
> 没有可判定的产物形态。
> **命中 0 时打一条「查不了」，不打「通过」**——一盏保证会亮的绿灯不携带信息。

---

### R-7 · 逐章写作（stage5 · canonical 闭卷）

| # | 要求 | 谁在查 |
|---|---|---|
| R-7.1 | 全书只有一条 canonical **闭卷**写作路：写手收到写作上下文与保真材料，源章原文只供确定性校验、绝不进入 prompt；每章正式产物唯一落 `stage5/chNN.json`，正文由 producer-owned `segments` 按计划 slot 顺序确定性拼出 | `结构`（`novel/stages/stage5/prompts.py::build_material` / `write_system` + `novel/stages/stage5/write.py::_rel` / `_ordered_segments`） |
| R-7.2 | 人只批准「按当前 Stage1 正文与 Stage4 计划启动全书闭卷写作」，不再逐章选择写作臂。授权落 `_service/approvals/<sid>.json` 的 `writing/writing_proof`，不是小说产物、不进 provenance，且不能替代 `R-5.20` 的当前 typed s4 结构批准；任何 Stage5 付费写作在首个 LLM 调用前必须两证齐全。默认按全书计划补齐所有缺章，已有章不重写，`--chapters` 才明确指定要重写的章 | `结构`（`novel/orchestrator/approvals.py::begin_writing` / `authorize_writing` + Stage5 付费最低边界 + `novel/stages/stage5/write.py::_chapters_of`）+ **`人`**（是否授权本轮付费写作，见 `B-19`） |
| R-7.2b | 目标章在一个并发池内逐章起跑，硬上限 32；一章失败不取消其他已经起跑的章，结果仍按阅读序落盘和报告。`ResponseRepairRequired` 是该章停在下一采之前，不是全批网络故障：不得取消尚待起跑的其他章，同批成功章先落各自 canonical，再把修复终态交给外层 agent。40 章时同时在途峰值只能是 32；不再生成全书 Markdown 投影 | `结构`（`novel/stages/stage5/write.py::_run_group_parallel`）+ 并发、失败隔离、修复暂停与顺序回归测试 |
| R-7.3 | 必须逐字写入的原话，逐条回写校验 | `闸门` |
| R-7.3b | 原话允许**被一处提示语切开**（`「…，」我说，「…」`）——中文小说的标准写法。插入必须在原文字节上长成「收引号 + ≤12 内容字 + 开引号」；改词、插动作描写、只写半句照旧判缺失。放行单独留痕 | `闸门` + `INFO` |
| R-7.4 | 必须保住的硬数字，**按数值比不按字面比**：下发面「30岁」、成稿「三十岁」= 满足 | `闸门` |
| R-7.5 | 发明越界的**两个探针**：新数字（下发面以外的「数字+单位」逐个列出）· 新人物（只抓「本书已有的名字用字 + 称谓后缀」造出来的人，召回有缺口） | `INFO` |
| R-7.5b | 发明边界按**是否产生契约外持久状态变化**判，不按“原文没写的动作一律禁止”判。新因果/决定/结果/关系/知识/往事/秘密/规则及可被后文引用的新人物地点道具事件，确定性层一个可靠探针都没有 | **无守卫 → `B-01`** ★ |
| R-7.6 | 成稿与源章的重合度用 20 字窗口逐章计算，**扣掉契约要求逐字保留的部分**；canonical 闭卷上界为 `SOURCE_OVERLAP_MAX`（10%）。它用于暴露原文意外进入下发面、缓存或落盘链的异常，不是文风质量线 | `INFO`（`source_overlap_high`） |
| R-7.6b | **历史关闭说明**：旧开卷路径逐章重合跨度 0.9%~96.8%，没有可标定的一刀切阈值；该无守卫面没有靠发明阈值“修好”，而是随双路径执行能力整体退役。旧盘面仍按原字节保留但为 `legacy_incompatible`，不能作为现役章稿读取 | `结构`（`novel/stages/stage5/measure.py::legacy_stage5_paths` / `canonical_chapter_paths`；历史缺口见 `B-03`） |
| R-7.6c | 现役章稿不携带写作路径标记，故不再产历史路径分报；重合度由 `overlap_report` 逐章无条件报出，且只有 `source_overlap_high` 这一条提示级结论 | `结构`（`novel/stages/stage5/checks.py::fidelity_findings`） |
| R-7.7 | 机械层归一（标点 / 引号体 / 分段）由代码做，**归一改了多少进校验报告一行**（不落产物）；**引号配对异常留着报、不静默修** | `INFO`（`quote_unbalanced`） |
| R-7.8 | 篇幅：成稿字数落在**章预算 `DRAFT_BAND`（±20%）**、全书落在 **`BOOK_BAND`（±10%）**，**两个数不同且都没有第二层宽限**（逐章放宽给单章节奏留余量，全书总量是产品承诺，逐章正负偏差在书级抵消）；超限**由阻断降为提示** | `INFO` |
| R-7.8b | 源章 / 成稿的**直接引语数与比值逐章无条件报出**；现役没有书级 `dialogue_report`，不得把逐章 producer 的读数宣传成另一条不存在的聚合 | `度量`（`dialogue_report`） |
| R-7.9 | 按给定的叙述视角写，但只在确定性可判域内给结论。**人称轴**：`narration_person_report` 逐章常驻报告声明人称、锚点、自称命中与未判原因；只有「单 narration + 全章边界 + first/third + 非空锚」进入弱信号 `narration_person_drift`，且只作人工线索。**章尾轴**：`end_hook_surface_report` 只盘点姓名/数字原子落在末 slot、其他 slot 或整章未见；代词化、换说法、同名与撞值都让字面位置无法推出语义交付，所以每个有稿章节保留 `end_hook_unver`，绝不据字面探针指挥付费改稿 | `度量`（`narration_person_report` / `end_hook_surface_report`）+ `INFO`（`narration_person_drift`）+ `unver`（`end_hook_unver`）。<br>⚠ 章尾末 slot 由 Stage4 新 `scenes[-1]` 给出；两轴都没有 ground truth，故不设 HARD |
| R-7.9b | 不把潜台词说破、换叙述人时声音也要换，仍没有确定性真值；历史开卷路径的表层修饰与整段照抄约束已随该能力退役 | **无守卫 → `B-10`** |
| R-7.10 | Stage4 新场景的 slot 顺序就是落笔顺序；**场内 fact 数组没有源序含义**，写手按 purpose/turn/exit 自由调度，不做“事件倒序”机械判定 | `结构`（`contracts/schema/stage5.py::ChapterResponse` 闭合 slot；`novel/stages/stage5/prompts.py::chapter_facts` 渲染场景任务）；旧盲区 `B-02` 已随源序事件骨架退休 |
| R-7.11 | 「必须成立、不必写在纸面上」那一组，按定义查不了文面 | **`人`**，见 `B-04` |
| R-7.12 | 篇幅超上限时不许由模型自动裁剪：**没有任何自动收缩路径**，超上限一律零调用保留 pretrim 并转人工。付费生成、重采、`--apply-edit`、`--revalidate`、`--recheck` 的 canonical 提交共用同一个 trim 对账口；新 `.INVALID` 未替换旧 canonical 时不得改动旧稿的 audit/resolution，正文未变且人工决定仍匹配时必须保留 | `结构`（`novel/stages/stage5/write.py` 的 canonical 提交与 trim 同步函数；该函数签名里没有 client，结构上花不了钱）<br>⚠ 这里曾有「同槽候选生成 + 确定性 claims 对账 + A/B 双盲 + 采用」一整条路，由 `trim_equivalence` 的 evaluator profile 开闸。那个闸从未在任何宿主上打开过，现已随 profile 整批下线；`novel/stages/stage5/checks.py::trim_regressions` 保留，它现在只服务 `--revise` 的退化守卫 |
| R-7.13 | 超上限章的唯一出路是人：保留 pretrim，或改写整章 | `人`：`novel/stages/stage5/trim_resolution.py::retain_pretrim` 写 hash-bound resolution（0 次调用，绑 audit 完整 SHA-256、当前正式章路径与正文 SHA-256），或 `stage5.write --apply-edit` 落成新的一轮。⚠ **没有任何机制能替人采用一个更短的候选**——`TrimResolution.action` 在类型上只有 `retain_pretrim`（`B-11` 已关闭） |
| R-7.14 | **历史关闭说明**：旧开卷 system prompt 对「现场 / 回忆 / 转述」解释不足；实验补强在三本真实小说中没有稳定净收益，随后整条开卷能力退役。现役单一路径使用一份闭卷 system prompt，因此该差异面已结构性消失 | `结构`（`novel/stages/stage5/prompts.py::write_system`；历史缺口见 `B-13`） |
| R-7.15 | 成稿必须是**完整的一章正文**：不是空的或残的（内容字 < 50 早退，其余判定一条都不跑）· 结尾是完整句 · 正文里不含章节标题 / 编号 / 任务术语这类叙述外壳 | `HARD` |
| R-7.16 | 必须**用下发的那张称谓表**称呼人物：本章相关人物一个名字都没出现是漏写，不是简洁。⚠ 它与 `R-7.5` 是一对反向条款——那一条只管「不许编新的」，没管「要用给的」 | `INFO` |
| R-7.17 | 【必须在场】的物件要在成稿里**留下痕迹**（怎么描写随写手）——四组保真材料里的第三组，另两组是 `R-7.3` / `R-7.4`，第四组是 `R-7.11` | `INFO` |
| R-7.18 | **不许提前点破本章还不该揭示的事**（字面探针那一半）。⚠ 报绿灯时**同一行必须打盲区计数**：实测 21 条秘密里 19 条的字面探针连它自己的揭示章都命中不了，一本书盲区 100% 时要落「查不了」而不是沉默 | `HARD` |
| R-7.18b | **书级改写式泄底的守卫已于 2026-08-17 转授**（用户裁决）。原形态是 `stage5.semantic` 的两相付费语义审判（`semantic_secret_leak`），节点与判据一并下线。裁决依据是实跑证据：本项目的重构**不跨章重组**（实测 18/18 章 `from_source_ordinals == [自身]`），书级泄底的主风险面在计划形态上几乎不存在；而该节点占单本实付调用 54%、输出 token 55%，同一本书上 18 章里 5 章命题抽取返回 uncertain、8 条 secret 全部判不动 | 见 `R-3.7d` |
| R-7.18c | **语义审判的覆盖闭合条款随 R-7.18b 一并转授**。原形态是 `semantic_secret_coverage`：覆盖不确定时只能 `UNVER`、不得洗成通过。⚠ 该条款的**精神**仍然有效并落在别处：stage2 的后见之明判据是确定性的（不存在"模型没把握"这一态），交付前由成书逐字对账第 8 项把上游 info 逐条投影到成书章序复核 | 见 `R-3.7d` |
| R-7.19 | **历史关闭说明**：旧开卷路径曾以 `dialogue_halved` 阻断「计划无净压缩却把直接引语腰斩」；该判据只在已退役路径上有前件，现役注册表与执行路径一并删除，不留一条永远零分母的假绿灯 | `结构`（现役注册表 `contracts/judges/stage5.py`；历史风险见 `B-03`） |
| R-7.20 | 直接引语的**条数 / 引号对白体 / 对白占比**要贴着源章与本书——canonical 闭卷写作在历史六本书上都显示把台词压成转述的方向性效应，因此当前只以逐章 `dialogue_report` 给分母、三条 INFO 留线索，不自动补台词 | `INFO` |
| R-7.21 | 成稿要贴着机械基线：`R-4.3b` 的正文精确投影与 Stage5 从源文现算的句段形态档位 · 段均字数 · 提示语排版惯例 · 明喻密度 ·「心里」密度 · 破折号密度。这里不消费 `R-4` 的定性画像 | `INFO` |
| R-7.21b | 破折号密度与连续短段成串**无条件报出读数**——它们是上一条的基线，只在漂了的时候才说得出来就没有基线可言 | `度量` |
| R-7.22 | 每个 `required_fact` 都要在所属新场景留下落点；只用专名/数字作确定性表面探针，**提不出探针的事实显式 UNVER**，不许算作通过；不再检查 fact 数组的源文先后 | `INFO`（`fact_landing`）+ `unver`（`fact_landing_unver`） |
| R-7.22b | Stage2 事实 claim 的表面原子以及 `verbatim` / `keep_value` / `must_appear` 材料锚点，都要在**源章原文**里找得到；找不到是上游契约或投影错，账记上游不记写手 | `INFO`（`fact_atom_unattested` / `material_anchor_unattested`，`owner=stage2`） |
| R-7.22c | **守卫自陈排除掉的样本，要说清它们此后没有被判过**：`noatom` fact 被计数、被报出，但语义落点没有可靠第二把自动尺子 | **无守卫 → `B-23`** |
| R-7.23 | 自造的数字在**跨章之间**不许打架——同一件事在两章里写成两个数，两处都不是原作给的，选哪个都行**但必须一致** | `INFO` |
| R-7.24 | `stage5.write --apply-edit` 是 **0 LLM 调用的新人工修订轮**，不是一个局部文本补丁。输入必须是文件名指向的单章完整 `ChapterResponse`，`segments` 覆盖该章全部 producer-owned slot 且每个恰好一次；缺 slot、多 slot或只交一段差异均关闭式拒绝。`--recheck` 只刷新 v2 派生字段，不归一或改写 segments、任务快照与写作 provenance；`--revalidate` 按 `.INVALID` 保存的原始 segments 重判，提升不能借机修改正文 | `结构`（`ChapterResponse` + `novel/stages/stage5/write.py::run_apply_edit` / `run_recheck` / `run_revalidate`）+ 回归测试 |
| R-7.25 | 写手收到的自然段数量目标必须与落盘判据共用同一公式：逐章篇幅带除以本书 `avg_paragraph_chars + paragraph_sep_chars`，下限至少 3、上限至少 4。`paragraph_count_report` 无条件报实际值与目标；越界由 `paragraph_budget_missed` **HARD** 拦成 `.INVALID`，不得由 `--apply-edit` 正式化 | `HARD`（`paragraph_budget_missed`）+ `度量`（`paragraph_count_report`）；两者共用 `novel/stages/stage5/measure.py::target_paragraphs` 与 `novel/stages/stage5/checks.py::_paragraph_budget` |
| R-7.26 | fresh 只代表 Review 结论仍绑定当前主体，不代表当前主体可以正式下游消费。s5 审批、Stage6、Stage7 与最终交付必须再要求 accepted Review：无活动 `.INVALID`、计划章均有正式稿、当前 Review 无 HARD | `结构`（唯一出口 `novel/stages/stage5/review.py::require_accepted_review`；`novel/orchestrator/approvals.py::_stage5_subject_rels`、`novel/stages/stage6/hook.py::_Upstreams`、`novel/stages/stage7/rename.py::_Upstreams` 共用）+ 回归测试 |
| R-7.27 | foreign `prepared` 是旧 run 已完成有效 initial-write 响应、却未可靠提交 canonical 的尾窗，不是第二条写作臂。默认付费写作必须在 client、driver factory、session、probe、cache 与 wire 前关闭式拒绝其目标章；唯一恢复路是同一个新 target run 先 `--emit-prepared-recovery-workpack <SOURCE_RUN_ID>`、再以工作包完整 SHA-256、actor/reason 执行 `--recover-prepared`。两入口均为 FREE，禁止 executor profile/egress、`--chapters`、`--revise` 与 response repair，恢复期间 target run 的持久 ledger/session 及当前 run 计数必须共同证明零 wire、零 cache、零 session | `结构`（`contracts/invariants.py::FREE_ENTRIES` / `VALUED_FLAGS` + `novel/stages/stage5/write.py` 的付费最低边界与 recovery producer）+ `tests/test_stage5_prepared_recovery.py` |
| R-7.27b | apply 不是“checkpoint 存在即采用”：必须重验 current Stage4 typed 最终结构批准、current WriterContext、独立 writing proof、source 章仍在当前计划，以及当前代码下的输入、prompt、schema、业务 validator、HARD 与 live baseline。正常 Stage5 Review 因尾窗未闭合而 stale 时不反向堵死这条专用恢复路；恢复会使 Review stale，全部收口后仍须单独重跑 `stage5.review` | `结构`（`novel/orchestrator/approvals.py::authorize_prepared_recovery` + `novel/stages/stage5/write.py::run_prepared_recovery`）+ 审批与 recovery 回归测试 |
| R-7.27c | source run 的 identity、commit、checkpoint 均同时核 semantic self-hash 与原文件 SHA-256，并与历史 ledger/session/request/attempt/response 逐项闭合；恢复不得修改 source 目录下任何 sidecar。`source_adoption_id` 只由稳定 source 身份与章组成、不含 target run：同一 source/章已由一个 target 正式采用后，另一个 target 不能重放；source 任一 raw/self 漂移必须重新阻断 | `结构`（`novel/llm/stage5_runs.py` 的 source sidecar 验证与稳定 adoption identity + `novel/llm/ledger.py` 的 append-only adoption audit）+ raw/self、只读与跨 run replay 回归测试 |
| R-7.27d | target 首次 apply 先原子冻结 hash-bound 整书 workpack/action，再逐章按 `prepared journal → canonical 共用提交口 → typed readback → 完整 adoption audit → committed` 收口；同 target 崩溃重跑只能补齐原声明。commit 当下须精确核 live post hashes；其后合法 rewrite/recheck/retain-pretrim 不得让旧 foreign source 复活，须由 current typed adoption 或 `stage5/_rounds/` 中与 journal 精确一致的 recovered payload/adoption 提供持久 witness，并继续核 source raw/self、journal self 与除时间外的完整 audit 行。任一 lineage 缺失或漂移均关闭式拒绝 | `结构`（`novel/llm/stage5_runs.py` 的 manifest/journal/replacement 判定 + `novel/store.py::write_round` 的历史归档 + `novel/llm/ledger.py::require_prepared_recovery_lineage`）+ 崩溃、合法后继与篡改回归测试 |
| R-7.27e | prepared recovery 的 subject 必须保留 `target_run_id` 绑定；因此通用 HTTP 作业面**不提供**这两个入口：`POST /api/jobs/preview` 与 `POST /api/jobs` 都必须在 argv、审批与 job 构造前拒绝 `stage5.write --emit-prepared-recovery-workpack` / `--recover-prepared`。HTTP 每个 job 都生成新 run_id，不能安全复用 target；操作员只能用 operator 的 CLI runner 显式给 emit/apply 同一个 `--run-id` | `结构`（`novel/api/routes/jobs.py::_refuse_prepared_recovery_api`）+ `tests/test_api_http.py::test_http_jobs_refuse_cli_only_prepared_recovery` |
| R-7.28 | **逐 slot 段落计划只有一个算法落点**：章节目标由章预算和本书段落成本计算并 clamp 进 `R-7.25` 的合法区间；每个非空场景至少 1 段，余量按场景预算 largest-remainder 分配，slot 顺序是唯一平局规则，总和必须精确等于章目标。场景数已使目标不可行时在首个 wire 前关闭式拒绝；prompt、修订反馈与常驻 `paragraph_slot_report` 必须消费同一计划。`reflow_shadow` 只允许改空白、不改变非空白字符 SHA，不跨对白合并且只在句末拆分；它只把可行性和 HARD 前后差写观察流，不写产物、不改变候选或 Review 结论，TODO-21 三书标定前不得默认采纳 | `结构`（`novel/stages/stage5/measure.py::paragraph_slot_plan` / `::reflow_shadow` + `novel/stages/stage5/prompts.py::build_material` + `novel/stages/stage5/checks.py::_paragraph_budget` + `novel/stages/stage5/write.py::_record_reflow_shadow`）+ `tests/test_stage5_paragraph_plan.py` |
| R-7.29 | **人工改稿是 preview→双 hash 提交，不是盲写覆盖**：只读 preview 与正式 apply 共用归一、完整 slot 校验和当前 `_judge`，零模型调用且 preview 不写盘；响应同时给归一稿、完整 finding、当前/新增/消除 HARD、当前 canonical 完整文件 SHA-256 与 preview subject SHA-256。apply 必填 actor/reason、两枚 SHA 与 `formal｜working_draft`：同书总锁→逐章锁的线程+跨进程所有权覆盖同一纯评估口重算、preview subject 匹配、提交前 current 文件 SHA 复核以及整条写回，任一漂移都在归档前拒绝；`formal` 不得新增 HARD，`working_draft` 可明示落一般新增 HARD 为新的 canonical human 轮但不得绕过 `paragraph_budget_missed`，旧轮照常归档，Review/closure/下游保持 stale/阻断，**不写 `.INVALID`**。`human_actions.jsonl` 以不含正文的 `prepared → canonical/trim 落盘+读回+report → committed` 两相收口，request 绑 preview 意图、action 再绑预期结果 SHA；普通异常补偿产物并保留 prepared 证据，kill 留下精确 landed 结果时新 run 只补后相而不造新轮，公开审计只认 committed。未换行尾必须在 append 全局锁内先把原始字节与前缀摘要内容寻址地隔离到 `_service/human_actions_tail_recovery/`，再截尾补相；中间或已换行完整坏行仍关闭式失败。逐次 apply 不自动跑 Review；人可连续预览/改多章，只在全部编辑与 trim 收口后显式跑一次最终全书 `stage5.review` | `结构`（`contracts/schema/api.py::DraftEditPreview` / `::DraftEditRequest` + `novel/stages/stage5/write.py::prepare_edit_preview` / `::run_apply_edit` + `novel/human_actions.py::HumanEditAction` + `novel/api/routes/edits.py::preview_draft_edit` / `::edit_draft`）+ CLI/API/hash/审计账回归测试 |
| R-7.30 | **正式提交事务与 same-run sweep 是一次 run 内的确定性收口**：阶段闭集为 `prepare_commit → formal_write → typed_readback → adoption → mark_committed → committed_verify → session_end`；首轮逐章提交失败先保留阶段/摘要并让其余候选完成，随后只对失败章做恰好一次有界 sweep，复用同一 checkpoint、候选、章所有权与 usage 认领，只重放提交链，禁止新 driver/probe/wire/cache/session。提交和 batch 记账分别以 resume/once 幂等续接，session 只结束一次并记 delivery attempts、same-run recovery 与首个失败阶段；所有 preflight/readback/trim 核验读必须隔离在 `RunContext.validation_reads()`，提交前后来源边新增、消失或改值均关闭式拒绝 | `结构`（`contracts/invariants.py::STAGE5_COMMIT_PHASES` + `novel/stages/stage5/write.py::_commit_phase` / `::_run_group_parallel` / `::_assert_commit_edges_stable`）+ `tests/test_stage5_commit_sweep.py` |

> **审批版本边界**：`_service/approvals/<sid>.json` 的现役盘上形状是
> `_ApprovalRecordV7`（`v=7`）：七闸不含 s3；Stage4A 的 typed `design_decision/history` 只授权
> 下一次 B，不打开 s4；B 编译后的 `structure_decision/history` 才能打开 s4。`writing` 必须与绑定
> 当前 Stage1 正文、Stage4 Plan、经最终结构批准后单独生成的 current WriterContext 及测量契约的
> `writing_proof` 成对，三项授权不能互相推出。对外 `ApprovalDoc` 是 **v6**；这是 API 响应版本，
> 不是盘上版本的别名。旧盘 v6 及更早记录在普通 GET/启动时一律拒读，只有显式
> `backfill_from_disk` 可逐项对证后保守收进 v7；其中 v6→v7 保留 current 的 s0～s4、seen 与
> design/structure/writing typed 历史，清除 s5～s7，绝不倒造 Stage5 typed 闭合回执；更早迁移
> 同样不得从已有 A/B/Plan 或付费账倒造 A 人审，
> 已有 **current Plan v4 + current bound EvidenceBinding v3** 只可继续走最终结构审与零成本恢复；
> 旧 v2 Binding 仍按 artifact policy 从 A 审阅后重跑 B，不设迁移解码器。缺少 typed 最终结构决定或现役写作主体证明时不得
> 代造 s4、writing 或其后证明。

> **历史证据（旧双路径，现已不可执行）——R-7.6 在 2026-08-10 当时的裁决**：
> 旧闭卷路径只定上界，旧开卷路径不定线。
> ⚠ 定线之前先统一口径——上面那组「闭卷 0.0/0.1/0.3%；开卷 41.2/42.6/92.9/95.9%」是**书级均值**
> （而且量的是另一批四本书），而判定量是**逐章**。当前三本 31 章重算：
>
> | | 书级均值 | **逐章** |
> |---|---|---|
> | 旧闭卷 | 0.8 / 0.1 / 0.5% | 0.0% ~ **2.4%**，中位 0.1% |
> | 旧开卷 | 41.3 / 78.0 / 36.4% | **0.9%** ~ **96.8%**，中位 49.8% |
>
> 两件事只有换成逐章才看得见：**闭卷的上界是 2.4% 不是 0.3%**；**开卷有低到 0.9% 的章**
> ——「开卷 = 高重合」不成立，它是**上界**问题不是分布问题。
> ⇒ 这些数字只解释为什么旧开卷能力不适合靠发明阈值续命；现役不再有写作臂身份，
> 10% 是 canonical 闭卷链路的异常探针，不是臂分类器。`B-03` 保留的是历史裁决记录，不是可调用的现役漏口。

> **旧六本书对照的历史结论**：旧闭卷组的对白比是原作的
> 0.74 / 0.67 / 0.68 与 **0.86 / 0.79 / 0.87**，旧开卷组是 1.00 / 1.01 / 0.94 与
> **0.93 / 1.02 / 1.01**。它说明现役 canonical 闭卷写作需要常驻暴露对白读数，不能说明应恢复已退役路径。
> ⚠ **但这一批的效应弱了约一半**——它是一个**有跨书方差的效应，不是一个系数**，
> 别把 0.7× 当常数引用（判据 30）。这正是 `R-7.8b` 要求那条比值**无条件报出**的理由。
> ⚠ 效应量有跨书方差，不得把 0.7× 当常数；现役处置是 `R-7.8b` / `R-7.20` 的逐章常驻报数。

---

### R-8 · 全书验收与修复（stage5 第二相）

**判成品，不判计划。** 整本写完之后才算得出来。

| # | 要求 | 谁在查 |
|---|---|---|
| R-8.1 | 严重度四级：`阻断 / 提示 / 度量 / 查不了`。**「度量」与「提示」必须分开**——读数无条件产出，不许挂在「没问题」那一支上 | `结构`（`contracts/rules.py::_validate` 第 ② 条：`sev` 与 `who_checks` 两侧必须成对，写歪一侧当场拒绝注册） |
| R-8.1b | 篇幅 · 与源章的重合度 · 全书篇幅总账这几个读数**逐章 + 书级无条件报出** | `度量` |
| R-8.2 | 每条结论带**根因第一嫌疑层**；「可修复」**蕴含根因在写作层**——根因不在写手身上的条目重写一百遍也修不好 | `载体`（`contracts/schema/common.py::Finding` 的两条后置条件：`owner is None` ⇔ 它不是结论 · `actionable` 蕴含 `owner == 'stage5'`） |
| R-8.3 | 收敛键 = `(章, 判据, 证据键)`。只按判据去重会把「换了个位置」读成「已修复」 | `载体`（`contracts/schema/common.py::Finding` 的 `evidence_key` 由 `evidence` 现算并断言，对不上当场炸） |
| R-8.3b | 收敛账**无条件报出**：与上一轮相比消掉了什么、还剩什么 | `度量` |
| R-8.4 | 成稿指纹没变时**不许前进未消轮次计数**——0 成本重跑一次验收不是「写手又试了一次」 | `结构`（`novel/stages/stage5/review.py::_converge` 只在**正文**指纹 `draft_text_sha1` 变了时才 +1，没变一律记 `same_draft`；`converge` 由「稿子变没变」决定、不由计数值决定） |
| R-8.5 | 连续 2 轮未消 → 判「根因不在写手」，修复主动跳过 | `结构`（`novel/stages/stage5/write.py::revise_targets` 的第四道筛子按 `contracts/invariants.py::STOP_REWRITE_AFTER` 筛掉，并把跳过的两类**单独报出来**——「不进修复」不等于「不管」） |
| R-8.6 | **付费自救的第二道准入**：可修复之外还要「满足路径唯一」——目标字串被上游钉死，或动作就是「删掉指出来的那个东西」，写手编不出第三种答案。密度 / 比率 / 档位类**一律不进**，因为最省力的满足路径是删内容或编内容 | `结构`（`contracts/rules.py::_validate` ③④：准入是**一个字段的三个取值**，「进且只进一张桶」于是不可能分不干净；写歪一个值当场拒绝注册） |
| R-8.7 | 「这条我不修」要**理由必填**，且豁免只挂在它真正豁免的那一条结论上 | `载体`（`contracts/schema/common.py::WontFix` 的 `why` 最小长度 + 键↔条目的收敛键一致性断言。⚠ 模型已从 `stage5.py` 搬到 `common.py`：语义豁免闭环把它变成跨层机制，形状必须只有一份） |
| R-8.7b | 不给**不存在的**结论发豁免（该层**当前结论池**里没有那条 ⇒ 显式终态）；豁免**不删结论**，只是不再进修复、不再占待办，并**单独计数** | `结构`（`novel/wontfix.py::mark` 先在结论池里反查，查不到 / 同一 (章,判据) 有多条而没写出证据键 ⇒ 显式终态；落进 `Finding.wontfix` 由 `novel/wontfix.py::applied` **只降 `actionable` 不删结论**） |
| R-8.7c | **持久豁免要跨层共用同一个闭环**。身份 = `(层, 章\|book, 判据, 证据键)`，层由 `<stage>/wontfix.json` 路径承载；产物版本只进审计账 `at`、不参与匹配，失效由当前证据键保证。现役 Stage5 结论池只有 0 成本机械验收；语义结论只在 Stage6 文案层进同一闭环 | `结构`（`novel/wontfix.py` 是全链唯一机制落点；结论池分别由 `novel/stages/stage5/review.py::mark_wontfix` 与 `novel/stages/stage6/hook.py::run_wontfix` 交出） |
| R-8.8 | 待办清单聚合八类，**只读盘、不重判**：`invalid` / `rejected` / `stalled` / `human_only` / `repairable` / `verification` / `upstream` / `approval`。`repairable` 仅表示本层有结构化修复路，不再冒充「只能人修」；`verification` 只收有明确人工消解路的 UNVER。固有盲区仍不制造永远清不空的待办 | `结构`（`novel/views/inbox.py::inbox_view` 只读正式产物、审批、语义决定与 durable trim audit/resolution；`INBOX_KIND` 八键始终全出，不调用模型） |
| R-8.9 | 词表类判据**必须报出它的基线命中率** | `结构`（`contracts/rules.py::_validate` 第 ⑤ 条：`wordlist` 不填 `baseline` 当场拒绝注册） |
| R-8.9b | **探针自己的基线读数**要无条件报出：未揭示秘密在全书成稿里的出现分布 · 全书自造称谓与撞名的名字面。⚠ 后者原来只在**命中时**才产出 ⇒「全书 0 个自造称谓」这个读数永远说不出来，而「查过、干净」与「根本没在查」在报告上长得一模一样 | `度量` |
| R-8.10 | **历史关闭说明**：Stage5 曾有独立付费的两相书级语义审判；它已连节点、产物与判据一并下线，现役 Stage5 只产 0 成本机械 Review，不得宣称仍有一层书级语义守卫 | `结构`（`contracts/invariants.py::NODES`；转授与历史依据见 `R-7.18b` / `R-7.18c` 与 `B-15`） |
| R-8.11 | 当前 Stage4 计划里的每一章都要有且只有一份 canonical `stage5/chNN.json`；缺章单独报 `draft_missing`，旧目录则进 `legacy_incompatible`，不把两种状态合成一个比例 | `INFO`（`draft_missing`） |
| R-8.12 | 逐章 finding 必须携带 `output_ordinal`；书级 finding 显式为 `null`。Review、CheckReport、CLI/API 与 inbox 不得丢掉这个定位键 | `载体`（`Finding.output_ordinal`）+ 端到端回归 |
| R-8.13 | 当章有一份当前 schema 可读、相对路径与 `output_ordinal` 指向当前 canonical 稿、且正文 SHA-256 精确匹配的 `TrimAudit` 时，`length_over` 不再单独产 finding，而把 before/目标及超限事实吸收到同一条 trim verification detail。⚠ **折叠不看销账状态**：已 `retain_pretrim` 销账的 audit 照样折叠——销账关掉的是待办，不是「这一稿确实超限」这个事实；销账后再产一条 `length_over` 等于把人刚关掉的那个决定重新打开（判据侧取 `current_trim_audits`，inbox 待核验侧取 `current_unresolved_audits`，**两个集合故意不是一个**）。audit 缺失/拒读、正式稿缺失/不可读/过期、路径·`output_ordinal` 对不上或正文 hash 已变时，独立篇幅 finding 仍照常保留。⚠ 没有「已采用」这一档：自动收缩候选与采用路径已于 2026-08-18 下线 | `结构`（`novel/stages/stage5/review.py::_merge_length_over_into_trim`；当前审计集合由 `novel/stages/stage5/trim_resolution.py::current_trim_audits` 给出）+ trim resolution 回归 |
| R-8.14 | `band_drift` 的逐章事实保留在 `Review.chapters`；对外 `CheckReport`/CLI/API/inbox 聚合为一条书级 finding，detail 给命中章数/章号，evidence 可展开逐章明细，且 wontfix/收敛信息不得静默丢失 | `结构`（`novel/stages/stage5/review.py::_aggregate_band_drift`）+ CheckReport/inbox 回归 |
| R-8.15 | `stage5.review --retain-pretrim` 必须显式提交非空 `--actor` / `--reason`，并用当前章主体 hash 锁定 resolution；不允许默认空凭据 | `结构`（`novel/stages/stage5/trim_resolution.py::retain_pretrim`）+ CLI/ArtifactStore 后置条件回归 |
| R-8.16 | 字面 secret timeline 以 Stage2 `first_reveal_chapter` 为权威读者时点；Stage4/5/6 必须共用 `novel/core/facts.py::first_reveal_chapter`，不得自造兼容字段或第二套判定 | `结构`（`novel/stages/stage5/checks.py::fidelity_findings`）+ 时线回归 |
| R-8.17 | **Stage5 只有一份强制、hash-bound 的整书闭合回执**。只读 workpack 必须绑定当前规则指纹、fresh Review 的完整文件 SHA，以及全部计划内 canonical 章的完整文件 SHA；构包前后必须重验同一 Review 文件并对全章、活动 `.INVALID`、trim/wontfix 处置面做完整双采样，任一中途漂移都不得返回新旧混合包。全部 Stage5 canonical/INVALID/recheck/revalidate/Review/wontfix/trim writer 与 closure submit 必须共用同一个位于 `<base>/_service/` 的线程+跨进程书锁，逐章提交再按序取章锁；closure 二次构包至 approval/gate 原子写回之间不得有 writer 尾窗，API 不得因取锁写书目录。只有 Stage5 完全未开始且无历史时可为 `missing`；已有稿件/历史时 Review 缺失、过期或不可证明 current，状态只能是 `review_required`，不发布 current workpack/decision 但保留 history。fresh Review 下若仍有缺章、活动 `.INVALID`、当前 HARD 或未销账 trim，workpack 必须优先为 `blocked`；只有 accepted 面才可进入 `ready/closed/expired`。`must_appear_missing` 只接受逐项 `confirmed_present` 或理由非空的 `accepted_exception`；fact landing 与其余 UNVER 只按完整 finding-set digest 整组确认，未登记的 actionable 规则关闭式失败；历史预填只复用 finding SHA 与 chapter SHA 都未变的逐项裁决。workpack 的 `unresolved_trim_items[]` 必须精确投影每章 audit SHA + 正文 SHA，浏览器批量请求只能复制该面；批处理在首写前核完整集合，任一漂移零写入，中断后相同 manifest 按章幂等续接。`gate.s5` 只能由 current typed closure 打开，匿名 `approve_gate('s5')` 拒绝；Stage6/7 写效果 CLI 与最终 assemble 共用 `require_stage5_closed` 硬闸，0 成本检查、改稿、trim 与回执恢复入口不得被反向封死 | `结构`（`novel/stages/stage5/closure.py::closure_workpack` / `::closure_view` / `::require_stage5_closed` + `novel/stages/stage5/trim_resolution.py::retain_pretrim_batch` + `novel/orchestrator/approvals.py::approve_stage5_closure` + `novel/api/routes/approvals.py` / `novel/api/routes/edits.py`）+ `tests/test_stage5_closure.py` / 批量 trim 与 CLI 闸回归 |

> **Stage5 Review 的 freshness 只有一份**：续跑、豁免、付费返工与审批只消费
> `novel/stages/stage5/review.py::review_status` 的 `fresh / stale / unknown / missing / legacy_incompatible`
> 五态结论。`fresh` 同时核当前 Review schema、通用 provenance、`RULES` 指纹、计划章集、
> `chapters.relpath` / `judged` / canonical 章集、逐章来源与 `draft_text_sha1` 正文投影；任一不成立
> 都由 `require_fresh_review` 关闭式拒绝。写作的 Stage2 投影保持 secrets-only；Review 因实际使用
> 名称注册表而额外绑定 `char_id/canonical/address[].surface/name_distinctiveness`，人物 evidence-only
> 定位变化不触发 stale。
>
> **恒等基线两道闸故意不同**：逐章 `identity_hit` 仍可让同现象结论退出付费反哺，
> 书级 `identity_dead` 不用上游锚点错误判死本层判据；根因由 `R-7.22b` 的
> `material_anchor_unattested(owner=stage2)` 单列。`web/src/components/check/CheckPanel.tsx` 同时显示
> `identity_hit · 恒等基线也命中` 与 `identity_dead · 判据失去判别力`，不再只写 JSON 不给人看。

> **R-8.6 的历史依据与代价**：某章为满足「对白变少了」这条（引语 7→11 句）**凭空编了一句台词**，
> 源章 0 次且语义讲不通；而第二轮全部 4 条判定**没有一条指向它**，报告还记它「消掉 2 条」。
> 收窄后的实测代价：盘上 5 本可修复条目 **36 → 9（转人工 27 条，75%）**，
> 其中一本是 **10 → 0**。⚠ 这是裁决的后果不是 bug；转人工那些**照常列在报告与界面上等人改**。

> **R-8.9 的出处**：一张「揭示标记」词表（其实/原来/一直/根本…）实测在 **21/22 章**命中，
> 与那一章有没有点破什么无关——于是它的「通过」分支是**一盏保证会亮的绿灯**。
> 没有基线数字的词表判据，等于不知道自己在说什么。有判别力的往往只有它的**反面**
> （一个标记都没有＝反常）。

> **另一条已可判死的**：秘密的字面探针 **4 本 12 条 secret，0 命中，连各自的揭示章都不命中**
> （四个题材）。「改写式泄底确定性层抓不到」不再是推论（`B-15`）。

> **判据的判别力是（判据 × 书）的属性，不是判据的属性。** 恒等基线在每本书上判死的是**不同**的集合；
> 三本书的干净集**交集只剩一条**。别把某本书上「这条判据有用」外推成判据的属性。

---

### R-9 · 开篇文案（stage6）

放在正文之前、引读者点进第一章的那段话。**一次调用产三个候选，人定稿。**

| # | 要求 | 谁在查 |
|---|---|---|
| R-9.1 | 三候选是**一次**调用产出，不是三次（成本口径） | `结构`（`novel/stages/stage6/hook.py::run_hook` 是全层唯一一处 `client.ask_json`） |
| R-9.2 | 连续的**重写正文**只下发到第一个冲突阶段末章；同时下发全书离散事实与逐章结构地图，使文案能选择后期兑现而不靠猜 | `结构`（`novel/stages/stage6/measure.py::lead_upto` 决定连续正文上界；`novel/stages/stage6/prompts.py::story_map` 与 `novel/stages/stage6/prompts.py::build_material` 投影全书结构） |
| R-9.3 | 机械层归一（标点 / 引号体 / **段间分隔**）由代码做**并记账**；段间分隔另有后置条件校验 | `HARD`（后置条件）+ 记账进报告 |
| R-9.3b | 引号配对异常**留着报，不静默修** | `INFO`（`hook_quote_unbalanced`） |
| R-9.4 | 收尾形态参照原作文案段的四分类 | `INFO` |
| R-9.5 | 引语的条数与最长一条字数**无条件报出**、与原作并排；**不给阈值** | `度量`（`hook_quote_shape`） |
| R-9.6 | **定稿是人做的，而且决定绑定当前字节**：`stage6.approve` 必须同时提交自报 `actor`、`reason` 与候选完整 SHA-256。**本仓没有自动放行门**：双盲合并决议恒是带证据的 `UNVER`，`basis` 因此恒是 `human_verification`（`calibrated_pass` 已随 evaluator profile 一并裁决删除）。人逐项核验后记录决定，但不得把它宣传成自动 PASS。⚠ 与之相随：**再也没有任何机制能替人否决一个候选**——「已有校准 FAIL 不得人工绕过」那道硬闸随自动 FAIL 一起消失，矛盾线索仍逐条呈现（`R-9.20`），裁决权完全在人 | **`人`**（`hook_not_approved`）；批准边界使用 `HookApprovalResolution`，并由 `novel/stages/stage6/semantic.py::require_human_resolution` 在归档/写盘前校验乐观锁与字节绑定 |
| R-9.7 | **剧透不是违规，也不是质量目标**：只判断它是否增强期待；不会因为说出后期信息而扣分或阻断 | `不适用`（产品质量口径的负空间声明） |
| R-9.8 | 文案允许重写台词、压缩时间、倒叙与合成场面；只有改变人物身份、关系、关键因果、故事结果或新增改变理解的设定才算矛盾 | 见 `R-9.19` / `R-9.20` |
| R-9.9 | **完整故事真相面下发**：`entities` / `canon` / `arc` 与全书逐章事件地图都给生成器和质量审判；模型自主选择披露多少 | `结构`（`novel/stages/stage6/prompts.py::build_material`） |
| R-9.10 | **原作文案作为创作金标准下发**：学其信息次序、升级节拍、反差、对白密度与最后一击；Stage6 从同一份 Stage1 文案即时计算字符数、平均句/段长、引号体、对白比例五叶机械参照，定性包不得进入生成或语义复核 | `结构`（`novel/core/measure.py::hook_style_metrics` + `novel/stages/stage6/prompts.py::build_material`） |
| R-9.11 | 这是一次**重写**：不复用**原作文案段**的句子 · 不整段誊抄成品正文 | `HARD` + `INFO` |
| R-9.12 | 文案里的**人物称谓**与**数字+单位**都要在下发面里有据 | `HARD` + `INFO` |
| R-9.13 | 文案是**完整的一段**：不是空的、以完整句子收束 | `HARD` ×2 |
| R-9.14 | 篇幅 / 段落形态 / 引号体贴合**本书自己的文案段**（不是正文） | `INFO` ×3 |
| R-9.15 | 本层三条读数**无条件报出**：下发范围 / 各候选字数 / 原作文案段字数；文案与秘密原文的逐字重合条数（按读者时点分已揭示 / 未揭示）。⚠ 它们**不是门**——秘密允许说破（`R-9.9`），这一条要的只是「数字必须出现在报告里」 | `度量`（`hook_report` / `hook_secret_literal`） |
| R-9.16 | 开篇连续正文有缺稿时必须常驻出声；secret 缺失或没有揭示时点不再限制文案，因为生成器已有完整事实与故事地图 | `INFO`（`hook_body_missing`） |
| R-9.17 | 判的必须是**照盘上这份正文写的那一版**文案：逐份成稿记 `judged[].consumed`（字段投影）判过期，整文件 `sha1` 只作审计；**没有 `consumed` ⇒ 说不清，不许算作没过期** | `INFO`（`hook_body_stale`） |
| R-9.18 | 原作文案式的重写台词与合成场面是合法体裁手法，不再要求台词逐字存在于正文，也不为这件事保留一条永恒的 `unver` 假检查 | `不适用`（负空间声明；边界由 `R-9.8` 定义） |
| R-9.19 | **实质矛盾的结论恒是带证据的 `UNVER`，裁决交人**：两名互不可见的 judge 各自给 verdict、理由与逐字证据，合并只做两件事——证据并集去重，以及算出四选一的人读理由（任一 judge 判 UNVER / 两人不一致 / 证据不闭合 / **A/B 同判且证据闭合但本仓没有自动放行门**）。第四条是信息量最大的一条，报告必须原样呈现两名判官实际说了什么，不能压成一句“未校准”。`SemanticDecision.verdict` 在**类型上**就是 `Literal['UNVER']`——不是靠一条校验器拦住。`HookSemanticReview` v2 持久保存两份排序、完整 `SemanticDecision[]`、运行时 evaluator 指纹与 `judged`：指纹只含 model / prompt / schema / decoder 四格（**没有 dataset**），其中 prompt 指纹由 `judge_request` 产出的两份实际静态 `(system,user-role-suffix)` 请求面计算，不冒充逐书动态正文身份；`judged` 则只绑定真正下发进 `rewritten_opening` 的 canonical Stage5 开篇章稿，不把未进入 prompt 的后半书正文伪装成审判主体。**全书 Stage5 accepted Review 仍是 `R-7.26` 的既有前置门**：后半书改字会先令 Review 暂不可用；零成本重跑 Review 后，若开篇正文与其余实际 prompt 输入均未变化，旧 semantic 可重新成为 current。候选 evidence 先回查为连续原字节，决议按完整 subject SHA-256 绑定 | **`人`**（`hook_semantic_contradiction`，`sev=unver`）；合并路径是 `novel/quality/evaluator.py::double_blind_decision`，物化、新鲜度与实际正文投影在 `novel/stages/stage6/semantic.py::materialize_double_blind` / `::_prompt_judged` / `::_current_artifact` |
| R-9.20 | 每条候选都按 **combined `SemanticDecision`** 单独展示 FAIL 矛盾或 UNVER 原因，不能从某一名 judge 的原始 review 倒推，也不能因为另有 PASS 候选而隐藏。UNVER 进入 `verification`，并通过 `R-9.6` 的 actor / reason / subject-hash 人工核验路径收口。⚠ 这里曾有一句「自动 FAIL 只能改字形成新主体，不能以普通审批或 wontfix 绕过」——自动 FAIL 已不可产，那条限制随之消失；矛盾**报**而不**拦**。逐候选不确定项仍须用 `<候选>:<文本指纹>` 寻址，禁止空证据让多条候选共用收敛键 | `INFO`（`hook_candidate_contradiction`）+ `verification` |
| R-9.21 | **吸引力六维评分与匿名排序常驻输出**：阅读欲 30% + 正文声线 20% + 具体场面 15% + 期待承诺 20% + 文案技法 15% − 摘要感 10%；总分由代码按固定权重计算，模型不能自报。剧透本身不加分也不扣分 | `度量`（`hook_quality_rank`） |
| R-9.22 | 初始三候选机械判定后，每条初始命中 HARD 的候选恰好获得一次定向 refresh-write；请求只能回该 approach，并见完整事实面、整组候选及具体 HARD 证据。只有 HARD 数严格下降且不与其他候选重复时才采纳；失败/wrong approach/重复/无净减均保留初始候选并完整记账。真实调用数为 `1 + 初始脏候选数` | `结构`（重试与取舍在 `novel/stages/stage6/hook.py::run_hook`，账面载体为 `contracts/schema/stage6.py::HookCandidateRetry`）+ 端到端回归 |
| R-9.23 | **呈现结构距离无条件报出、不给阈值**：候选与原作文案段的多档窗口重合率、最长逐字重合字数、末行逐字重合，与 `R-9.11` 那道 20 字闸的读数并排展示。⚠ 它**不是第二道复用闸**——`R-9.11` 尾注明许学结构、节拍与最后一击的功能，结构贴近本身不违约；这条读数要回答的是「这条候选离原作有多近」，供人裁决策略去留与定稿取舍。⚠ 设阈值须先有多轮采样的跑间方差，同 `R-9.5` 的纪律 | `度量`（`hook_source_distance`） |
| R-9.24 | **候选身份是位置，策略是可重复的属性**：一次调用按 `HOOK_RECIPE` 逐槽产出，响应校验必须按列表位置逐项等于这份配方，而不是只比计数、更不是「每种策略各一次」——同一种策略可以占两槽，模型却不能把 c1/c2 对调后再让位置身份替错误顺序背书。同一种策略占两槽时，prompt **现算**出「这两条必须锚在不同事件上」那句硬要求（配方改回全不重复时它自己消失，不会留下一句不再成立的话）。⚠ `source_architecture` 已于 2026-08-23 退场：不再下发、响应里出现即违约，但**仍留在 `Candidate.approach` 的取值面上**——v2 对照臂里仍有这个历史取值，删掉会让退场证据拒读 | `结构`（`contracts/invariants.py::HOOK_RECIPE` / `HOOK_RETIRED_APPROACHES` 是唯一落点，import 期断言守住配方⊆取值面、配方∩退场=∅、配方长度=槽位数）+ `HookResponse` 的逐槽配方校验器 |

> **本轮语义裁决由质量 A/B 触发**（2026-08-15，用户明确选择「留存优先于剧透」）：
> 三本书上，最佳实验候选的匿名主编均分 **8.78**，当前定稿 **4.88**，提升 **+3.90/10**，
> 且实验候选 **3/3 获胜**。当时的 bundle 同时给了原作文案、完整故事结构与正文
> `surface_habits`，并撤销剧透禁令；它没有单独证明定性字段有效。后续双书消融与产品裁决已将
> Stage3 定性包从现役生产删除，保留的收益不能再归因于 `surface_habits`。一次生成三候选的调用
> 结构没有改变，因为该部分没有单独验证。
>
> **完整故事不是让模型写摘要，而是给它选择权与真实性边界。** `payoff_preview` 可以从后期兑现
> 反向制造期待；`scene_punchline` 强制落在可视可听的关系场面，当前配方给它**两个槽**（两条锚
> 不同事件）。三条都必须围绕一个最强承诺写，不得把全书秘密逐条翻译。
>
> ⚠ 这一段原本还有第三种 `source_architecture`（保住原作文案已验证的钩子机制）。它**已于
> 2026-08-23 退场**，见 `R-9.24` 与 §十。这里保留它的名字而不是抹掉：它是上一段那次
> 2026-08-15 三书 A/B 的组成部分，抹掉会让「3/3 本获胜」这句话读不懂。
>
> **`R-9.11` 现在同时守模型与人。** 原作文案已下发，所以 20 内容字窗口重合率必须为 0；
> 模型可以学结构、节拍、对白密度与最后一击的功能，不能复用长句。正文誊抄仍是另一条 INFO。
>
> A/B 中一条候选把不存在的「地下室血迹 / 陪坐牢」写成关键结果，匿名审判正确把它排到最后。
> 这说明放开体裁自由后真正需要的不是剧透闸，而是**实质矛盾闸**：合成场面和新台词可以，
> 改人物关系、关键因果和故事结果不可以。

---

### R-10 · 人名替换与成书（stage7）

把成品里的**人物真名**换掉，好让成品表达原创；关系、身份、职位、称谓体系一个字不变。
**一次调用产新人物表（只产决策），替换与组装全部是确定性代码，人定稿。**

| # | 要求 | 谁在查 |
|---|---|---|
| R-10.1 | **改名只能待在链尾**：全链底座的判据是「人名必须在原文里有据」，把新名写回上游会被静默撤销。本节点**只读上游、只产自己的产物、绝不回写** | `结构`（`novel/stages/stage7/rename.py::run_rename` 与 `novel/stages/stage7/assemble.py::run_assemble` 是全层仅有的两个写盘点，两处都只落 `stage7/`；写口本身也只有 `novel/store.py::ArtifactStore` 一个） |
| R-10.2 | 替换是**单次 alternation 扫描**，`A→B`+`B→C` 的级联在结构上不可表示 | `结构`（`novel/stages/stage7/subs.py::apply_subs` 的单次 `pat.finditer`，游标不回头） |
| R-10.2b | 上一条的**后置条件**：替换跑完，映射表里**任何一条键都不许在替换目标里留下**——残留一条键就说明那次扫描漏掉了一段字节 | `HARD`（`rn_residual_key`） |
| R-10.3 | **三道防线之①**（中文没有词边界，这是本节点全部的风险；另两道是下面的 `R-10.3c` / `R-10.3d`）：保留项进 alternation 当**恒等条目** | `结构`（`novel/stages/stage7/subs.py::sub_table` 把保留项与替换项合成**同一张表**、按长度倒排，短项抢不走长项的字节） |
| R-10.3c | **三道防线之②**：要替换的称呼都**不短于两个字**。单字面在中文里没有左右锚，换掉它就是在正文里随机改字 | `HARD`（`rn_surface_short`） |
| R-10.3d | **三道防线之③**：被替换的称呼**没有同时挂在多个角色身上**，多主一律否决——否则换的是别人的称呼 | `HARD`（`rn_shared_surface`） |
| R-10.3b | 但**有一类书里多主是情节本身**（顶名 / 替身 / 女扮男装）：人在补充映射里明写的按人的裁决执行，另落一条提示 **照样报出它还碰到了谁**。`R-10.3d` 那条否决防的是**静默**误伤，不是「多主一律不能换」 | `INFO`（`rn_shared_surface_human`） |
| R-10.4 | 姓氏族群的判据**只有「字面同姓且 ≥2 人」**。关系边那条已删：一本书的兄弟在关系里**根本没有边**，而谓词词表会把夫妻同姓并成一族 | `HARD` |
| R-10.5 | 亲属词与姓氏同形（`孙`/`公`/`子`/`女`/`母`）时，用**本书的姓氏表**判，不硬编码字表——否则会让原作的姓氏活着进入「原创」成品 | `HARD` |
| R-10.6 | 残留探针的取值面 = 「原文有据的姓」**∪** 「姓氏映射的键」。少了后者有盲区：正典名是自造标签的姓、与亲属字同形的姓都会被过滤判掉 | `结构`（`novel/stages/stage7/rename.py::_build` 里 `stems` 那一行并集，喂给 `novel/stages/stage7/subs.py::residual_scan`） |
| R-10.6b | ⚠ **修探针，不修「原文有据」那一侧**——同一份数据在下发面该严、在检测器该宽，两处的正确方向相反 | `结构`（`novel/core/naming.py::attested_surname` 只被 `novel/core/naming.py::family_groups` 调用，`stems` 另走一条路） |
| R-10.6c | 探针的**结论**要常驻报出：逐个姓字的覆盖率与残留条数，**残留 0 条时也要出声**——「扫过、一条都没捡到」与「根本没扫」不许在报告上同形 | `INFO`（`rn_residual_stem`） |
| R-10.7 | 恒等基线：占位符双射往返，验替换器与组装器**逐字节守恒**。⚠ **它的结论必须落进产物**——「跑过且逐字节无损」与「从没跑过」不许在盘上同形（`Q-2`；原型只 `print`，产物里没有任何一个键记得它跑过没有） | `INFO`（`rn_identity_baseline`；**没跑过时报「没跑过」**，不是不报这一行） |
| R-10.7b | 恒等基线**验不了**三样：正向对真名安不安全（占位符天然无歧义，真名不是）· 漏网覆盖率 · 新名好不好。**这三样必须与基线的结论印在一起**，否则下一个人会把它跑绿了读成「替换是安全的」 | **`人`**（`rn_identity_blind`），见 `B-21` / `B-22` |
| R-10.8 | 成书 = `文案 \n\n 1 \n\n 第1章正文 \n\n 2 \n\n …`，LF、无「第N章」、无任何审阅元信息 | `结构`（`novel/stages/stage7/assemble.py::assemble_txt` 是唯一的组装路径，章分隔与换行取自 `contracts/invariants.py`，是常量不是参数） |
| R-10.9 | 成书是纯文本、带不了来源账，所以账记在它的 sha1 + 「判了哪几份稿」上 | `HARD` |
| R-10.10 | **替换覆盖率验不了**——只能替换人物表里有的称呼，**人物表漏掉的角色这一层结构上够不着** | **无守卫 → `B-21`** |
| R-10.11 | **新名好不好、读起来串不串味没有确定性判据** | **`人`**，见 `B-22` |
| R-10.12 | 新名贴合时代与性别气质 · 保住名字之间的结构关系（排行字、兄弟姐妹只差一字） | **无守卫 → `B-12`** |
| R-10.12b | **改名不得改变旧称呼自带的字面语义标记**：中文数字/阿拉伯数字、排行、亲属关系、头衔官阶必须在新形中原样保留；例如「阿九→阿七」必须阻断，「阿九→玄九」可通过 | `HARD`（`rn_semantic_marker_changed`） |
| R-10.13 | **映射表自身的六条后置条件**（＝下给模型那六条规则的验收面）：① 每个角色都交出决定与理由、编号与人物表一一对应 ② 下发的每一个称呼都有明确去向（「漏了」与「决定不改」必须分得开）③ **新名全书唯一** ④ 一个旧称呼只映射到一个新名 ⑤ **新名不得已经出现在替换目标里**（成稿+文案+书名），只出现在原作正文里的另报一条提示 ⑥ 称谓/亲属后缀在新形里原样保留（职位与关系不变） | ①②④⑥ `HARD`<br>③ `HARD` + `INFO`，见下方那条<br>⑤ `HARD` + `INFO` |
| R-10.13b | **人手补的映射不受 ③ 的否决，但必须报出它撞上了谁**——同 `R-10.3b` 的形状：这条否决防的是**静默**撞名。⚠ 实测有一次撞名**是对的**（原作对同一个人有两种写法），而硬拦它的唯一出路是撤销一个对的决定 | `INFO` |
| R-10.13c | 人手补的映射之间不许**首尾重叠**（`a` 的后缀 == `b` 的前缀 ⇒ 单次扫描会切出半截残串） | `HARD` |
| R-10.13d | 被**保留**的称呼里不许夹着已经改掉的名字（否则成品里半改不改：「阿禾」成了「阿舒」而「阿禾姊姊」原样留着） | `INFO` |
| R-10.14 | **成书的前置条件**：Stage2 全局复核必须 current 且 blocking 面逐项 PASS（分级见 `R-2.22`；`disclosure` 未决项不挡成书，但终审包必须逐条披露）；人物表必须已人工定稿且当前 fresh；Stage5 缺章或 `legacy_incompatible` / 各章不出自同一套当前契约 / 正文或文案消费指纹漂移 / 文案未定稿或其 resolution 不再绑定当前字节 —— 一律拒写 txt。人物表编辑会清空旧批准与 `book_txt` 账并把旧 txt 移出 live 下载路径；下载端点复用同一道 Stage2 + approved/current/fresh 闸，不以“文件存在”冒充可交付。文案语义闸只接受一种可审计基础：actor / reason / 完整 SHA-256 绑定的 `human_verification`，且该 resolution 必须仍绑着当前进成书的那份字节（改一个字即失效）。⚠ 「当前 profile 下的 calibrated PASS」那条路已随自动放行门删除；这里说的 UNVER 不阻断只指 Stage6 双盲结论，Stage2 blocking 面的 typed UNVER 按 `R-2.16` 阻断。txt 是交付物，里面没有地方写风险抬头横幅 | `HARD` ×2 + `闸门` ×5（含 Stage2 typed review、`stage6.semantic.require_approved_clear` 与 Stage7 delivery gate） |
| R-10.14b | 被拦下的废稿**排除但要报计数**——「排除了 N 份」与「一份都没有」必须分得开 | `INFO` |
| R-10.15 | **人工编辑面按 `char_id` 浅合并**，不要求人重抄整份决策集；不在补丁里的角色**结构上改不到**，且**不许改由人物表派生的旧名** | `结构`（`novel/stages/stage7/measure.py::merge_mapping_edit` 只认白名单 `_CHARACTER_PATCH_KEYS`，`old_canonical` 不在其中，写了就显式终态） |
| R-10.16 | **书名里的人名跟着换，其余不动**（不重起书名）——它走的是同一份替换表，是**确定性应用**不是第二次决策 | **`人`**（`rn_title_unver`：现语料上是**恒等变换**，三本书名都不含人名 ⇒ 这一条**从来没有过失败样本**，只能常驻报出换了几处、不能当绿灯读；换得对不对由人定稿时看），见 `B-22` |
| R-10.17 | **三条书级读数常驻输出**（不是告警，也不许挂在「没问题」那一支上）：替换总账（几条映射、换了几处、篇幅变化）· 新姓字在原文里另有多少次非人名用法 · **成书的写法密度**（正文／文案／原作三列）。⚠ **文案那一列全链只有这里量**——逐章判定够不到它，因为文案不属于任何一章 | `度量` ×3（`rn_report` / `rn_stem_noise` / `rn_style_report`） |
| R-10.18 | **「替换覆盖率查不了」这件事本身要常驻列在报告里**，不是不出现——省略掉，读报告的人会默认它守住了。代码侧只负责把「够不着」写在脸上，把关的是 `R-10.11` 那一下 | **`人`**（`rn_coverage_unver`），见 `B-21` |
| R-10.19 | `stage7.rename --emit-edit-template` 必须 stdout-only 输出 registry 绑定的 `NameMapEdit` v1 骨架，不手抄版本。`stage7.approve` 必须显式提交 actor/reason 与当前决策面（`surname_map` + `characters` + `extra_subs`）canonical JSON 的完整 SHA-256；旧页面 hash 不得批准新决策。服务 s7 proof 绑定同一决策面与完整 typed 审批凭据，并持续复核 subject current；派生 residual/book/book_txt 字节不进该 proof，故 assemble 写交付账不自废批准，真正决策或凭据变化仍失效。改名 prompt 实际下发人物 `role` 与 `setting.时代背景`，所以 grounding 消费投影必须包含 role 和真实下发的 era：typed role/era 修订会让旧 name map stale；未下发的 fate/secret/arc/世界观不得反向制造本节点 stale。`--identity` / `--recheck` 在覆盖 provenance 前必须先核旧消费输入：stale/unknown 或决策主体变化一律清批准，只有 proven fresh 且主体不变才可保留；两条路都清 `book_txt` 并原子退休旧交付物，不能用零调用重写 provenance 洗白旧审批 | `结构`（模板、决策面与免费重写锁在 `novel/stages/stage7/rename.py`，服务 proof 在 `novel/orchestrator/approvals.py`，交付闸在 `novel/stages/stage7/assemble.py`，消费投影在 `novel/consumption.py::stage7_grounding`）+ hash-bound `人` + role/era/identity/recheck/assemble/freshness 回归 |
| R-10.20 | **来源账那几条边的结论要在复检那一步就报出来**，不能只在组装那一步算。`_provenance.inputs`（含 `stage2/global_grounding.json`）一直记着 `file`+`consumed` 双指纹，比对算法也一直在，但此前**只有 `stage7.assemble` 调用它** —— 而人是在 `stage7.approve` 拍板的，approve 在 assemble **之前** ⇒ 一个人可以给「照旧人物表算出来的映射」签字，直到组装才被拒。⚠ 复检这一条是 `INFO` 不是 `HARD`：hard 会连 `--recheck` / `--adopt-residual` 这些 0 成本恢复路一起拦死，而 assemble 那道真闸原样不动。⚠ 三态分开：`stale`（可复算的断言）与 `unknown`（来源账证明不了）不许合成一句 `!= fresh` | `INFO`（`rn_upstream_stale`）+ `闸门`（`novel/stages/stage7/assemble.py` 原有那道）；判定共用 `novel/store.py::ArtifactStore.freshness_of_inputs`，**不另起第二把尺子** |

> **`R-10.15` 值得单记**：它是全链**唯一**把「定向返工只许改判据点名的部分」
> 做成**数据结构**的地方——别处都只是 prompt 里的一句叮嘱。移植同类人工补丁面时照搬这个形状。

> **漏网要分成两个子类，两者的修法不一样**（`R-10.10` / `B-21` 的操作面）：
> **「漏收一个人」补整个角色** · **「原作异写/错字」只补一条指向同一个新名的映射**。
> 实测那次漏网属后者——原作自己把同一个人的名字写成了两种形，人物表只收了其中一种。
> 别把「残留 0 条」读成「人名都换干净了」。
>
> ⚠ **残留探针的覆盖率会反向**：比率 =（总数−残留）/总数，当漏网形就是该姓的**主用法**时，
> 分母里装的主要就是分子，比率自我压低——实测两个姓分别是 40% / 60%
> （都落在「基本是普通词」那一侧）而**两个都是真漏网**。
> **两个信号打架时以结构信号为准**（它不受这个反馈回路影响，实测 6/6 全真）。
>
> ⚠ **一族已知同形的残留**：`X家` / `X府` / 职务式称呼，**四本同形**（`陆家`×3 · 30 处 ·
> `刘家`×6 + 手帕上绣的「柳」字 · `沈总`/`周秘书`）。根因是人物表的称谓收的是「人的叫法」，
> 不收「门第」形，也常漏收职务式称呼。补充映射后覆盖率回到 100%。

---

### R-11 · 人工验收界面

**给人眼核对用的工具，不是自动化看板。** 主体动作永远是「把两份东西并排放在一起，让人自己看」。

| # | 要求 | 谁在查 |
|---|---|---|
| R-11.1 | 只绑 `127.0.0.1`。四条各自独立成立的理由：会在本机执行进程 · 那些进程花真钱 · 会往盘上写文件 · 传输的是不外流的版权正文 | `HARD` |
| R-11.2 | localhost 也要防 CSRF：变更类路由要求自定义头 + 校验来源。**一个花钱的端点开在本机上，浏览器里任何页面都够得着它** | `HARD` |
| R-11.3 | **服务进程绝不落【产物】盘**，产物变更一律走 CLI 子进程；它只写 `_service/` 中的上传、一次性编辑计划、作业事件/日志与人工审批状态。上述都不是小说产物。⚠ 这一条经过带标注的收窄，但没有放宽产物写口，见下方那个框 | `HARD`（测试钉死） |
| R-11.4 | **界面不重算任何已经在校验器/闸门里的判据**，只呈现或链接。造第二个判官就是造第二个落点 | `HARD` |
| R-11.5 | **七**个核心对照视图：**原文↔切分（`R-1.8` 的唯一闸门）** · 原文↔骨架 · 计划↔写作上下文（逐场景）· 成稿↔材料（含原文↔重构稿按锚点切块）· 结论↔可编辑成稿 · 三候选文案 · 新旧人名表 | `不适用`（这是一份**清单**，不是一条可判定的约束；每个视图各自的纪律在 `R-11.9`/`R-11.13` 等条里） |
| R-11.6 | 产物新鲜度**三态**（新 / 陈旧 / **不知道**）；校验徽章**三态**（通过 / 通过但有查不了的 / 失败） | `HARD` |
| R-11.7 | 付费与 0 成本动作在界面上分色，且**贵贱只有一处算**（服务端），前端一行判贵贱的代码都没有 | `HARD` |
| R-11.8 | 付费入口一律经二次确认，并把**将要执行的那条命令**摊开显示（展示与执行共用同一个构造函数） | `HARD` |
| R-11.9 | 原文↔重构稿的分界线由**必须逐字写入的原话**定：同一串字节在两侧各定位一次，**两边都锚到才切**。没有锚的拍并进相邻块，**不给它编一条分界线**，并常驻显示锚点覆盖率 | `HARD` |
| R-11.10 | 同一本书同时只跑一个作业（第二个请求 409）；Stage5 的全书默认与 `--chapters` 定向写作共用这一把书级键，只在作业内部按章并发。不同 `sid` 的后台作业互不阻塞 | `HARD` |
| R-11.11 | 作业记录 = 这次跑用的是哪条命令、哪版代码。**服务重启后 `运行中` 一律改判 `不知道`**，不是「成功」；已冻结的 agent profile 快照槽若为 `null` / 缺失，只能解释为未记录，不能按今天的默认配置倒推历史身份 | `HARD` |
| R-11.12 | **不做作业取消**——调用在途时钱已经花了，kill 掉省不回来 | `不适用`（一条**负空间**声明：它要求的是「不建某个功能」，没有可判定的对象） |
| R-11.13 | 剧透开关只关四个字段，**已知旁路仍在**，界面上常驻这句标注 | `结构`（`contracts/invariants.py::SPOILER_DISCLOSURE`，随 `/api/enums` 整份下发；由 `tests/test_api_discipline.py::test_the_spoiler_disclosure_boundary_is_published` 查） |
| R-11.14 | agent 的 driver → provider / role / model / reasoning effort / execution mode 组合与 executor 节点只有一个公开事实源：`GET /api/agent/capabilities` 背后的 capability registry。前端不得硬编码选项、拼笛卡尔积或取数组第一项猜默认；`default_profiles=null` 明确表示尚无 provider-neutral 默认。可用性必须保留 `ready / missing / unknown / unauthenticated` 四态；本机探针只解析 CLI 与版本，不发付费请求，`ready` 不得冒充账号认证、模型 entitlement 或真实调用已验证 | `结构`（`novel/agent_capabilities.py` + `novel/api/routes/agent_capabilities.py`；`tests/test_agent_capabilities.py`、`tests/test_schema_api.py` 与 `tests/test_api_http.py` 查） |
| R-11.15 | **直接 job 的 executor profile 必须按已迁移组合关闭式传递，不能只收配置不生效。** Phase 2 当时唯一开放组合是 `stage2.global` 且 `flags=[]`：preview/start 共用同一计划，把 registry 解析后的 profile 同时冻结进 `Step`、`JobStart` 与 argv，CLI 再原样交给 `RunContext` 和该节点独立的同一 session executor，直至代码校验、正式提交与终验；显式 profile 绝不回退 DeepSeek。该节点任何 flag（含 `--purge-role`）、当时其他未迁移节点及 outer commander 非空 `profiles` 都必须在 wire/dispatch 前拒绝。前端仍未消费 capability，不得把后端窄纵切冒充页面已可配置；没有真实模型请求时不得宣传质量、速度或可用性实证 | `结构`（`novel/orchestrator/nodes.py::resolve_executor_profile` + `novel/orchestrator/jobs.py::plan_start` + `novel/cli/main.py::_run_stage2` + `novel/node_agents.py::Stage2GlobalAgent`；`tests/test_orchestrator_nodes.py`、`tests/test_orchestrator_jobs.py`、`tests/test_api_http.py` 与 `tests/test_cli.py` 查） |
| R-11.16 | **Phase 3 把同一直接 job 纵切扩到 `stage2.blueprint + flags=[]`，但不放宽校验、恢复或终验。** preview/start 必须把同一 resolved profile 冻结到 `JobStart` 与付费 producer `Step`/argv；自动追加的 0 成本 `--check-only` Step 保持 `executor_profile=null`，不能伪装成第二次 agent 调用。CLI 把 profile 原样交给 `RunContext` 与 `Stage2BlueprintAgent`，后者按 phase/purpose/chapter 隔离 driver、logical session 与 provider session；每章在同一 session 内接受 schema/dynamic/scope 及可路由联合 HARD 的代码反馈。验证后 checkpoint 必须用 `node-executor/v1` 逐项闭合 resolved profile、原始 subject/prompt/schema、requested ceiling、session/turn、accepted stdout、typed payload、attempt links 与 ledger/audit；恢复须复验同一闭环并认领 prior attempts，不能伪造 live provider resume 或重新付费。整书 compile、联合 HARD、正式提交、readback 与自动 `--check-only` 仍归代码，agent 自述不能放行；显式 profile 绝不回退 DeepSeek。两个 Stage2 节点的任何 flag、其余未迁移节点、outer commander 非空 `profiles` 与尚未消费 capability 的 UI 继续关闭 | `结构`（`novel/orchestrator/nodes.py::EXECUTOR_PROFILED_NODES` + `novel/node_agents.py::BlueprintSessionPool` / `::Stage2BlueprintAgent` + `novel/llm/stage2_runs.py::write_checkpoint` / `::restore_checkpoint` + `novel/stages/stage2/extract.py::finalize_blueprint_candidate`；`tests/test_stage2_blueprint_agent.py`、`tests/test_stage2_blueprint_agent_boundary.py`、`tests/test_stage2_recovery_runs.py`、jobs/API/CLI 回归查） |
| R-11.17 | **Phase 4 把 direct executor 纵切扩到 `stage6.semantic + flags=[]`，并把正文外发从付费确认中拆成独立授权。** `PreviewRequest.egress_authorized` 默认 false；resolved profile 与这枚 bool 必须从 preview/start 同源冻结到 `JobStart`、付费 `Step` 与 argv，只有 true 才加入不属于 `NodeSpec.flags` 的运行控制参数 `--allow-egress`。false 预览可以完整摊开不可执行命令，POST start 与手敲 CLI 则必须在 job、probe、driver 与 wire 之前以 `ApprovalRequired` 拒绝；无 profile 时 egress 快照归一为 null，显式 true 反而关闭式拒绝，不能从 `--yes` 或 profile 推导正文外发授权。执行时 A/B 各自取得不同 driver 对象、logical session、临时 cwd 与 provider session id，同一双盲池的 driver/provider 元数据必须闭合，缺失角色并发请求；每角色验证后 checkpoint 不可变，任一新 wire 前先预检两边，完整恢复零 driver/probe/start/wire，单边恢复只为缺失角色新建 session，绝不伪造历史 provider resume。代码负责匿名响应校验、合并、序号与提交所有权，并在同一临界区按 `prepared journal → 正式 artifact write → typed readback → A/B 两条 adoption → committed` 收口，最后再跑当前输入、代码与交付终验；显式 profile 在通用 dispatch 先于任何旧 `_llm_client`，绝不回退 DeepSeek。该节点任何 flag、其余未迁移节点、outer commander 非空 `profiles` 与尚未消费 capability 的 UI 继续关闭 | `结构`（`novel/orchestrator/nodes.py::resolve_executor_egress` + `novel/orchestrator/jobs.py::_require_executor_egress` + `novel/cli/main.py::_dispatch` + `novel/node_agents.py::SemanticSessionPool` / `::Stage6SemanticAgent` + `novel/llm/stage6_runs.py::prepare_commit` / `::mark_committed` + `novel/stages/stage6/semantic.py::finalize_semantic_candidate`；`tests/test_stage6_semantic_agent.py`、`tests/test_stage6_semantic_agent_boundary.py`、`tests/test_stage6_agent_runs.py` 与 jobs/API/CLI 回归查） |
| R-11.18 | **Phase 5 把 direct executor 的现役面闭合为 8 节点 / 12 种 flag-token 组合，并继续由代码独占裁决与提交。** 在前三条纵切之外，新增 `stage4.design + flags=[] / [--replan]`、`stage4.plan + flags=[]`、`stage5.write + flags=[] / [--chapters] / [--revise] / [--revise,--chapters]`、`stage6.hook + flags=[]`、`stage7.rename + flags=[]`；任何未登记组合在 wire/dispatch 前关闭式拒绝。producer 都使用固定 typed response，agent 只提交候选，schema 与业务 HARD 由代码判断；其中 Stage4A 不再提交一份整书 response，而由一个节点内私有 pool 依次接收 1 份策略 response 与 N 份单章 response，每份验证后独立 checkpoint，代码再聚合成唯一整书候选。安全的结构化规则元数据只回到对应 logical session。凡登记了正式 primary 的交付都在同书提交所有权内按 `prepared journal → artifact write / retire → typed readback → current input/code/HARD/freshness 终验 → 精确 accepted-attempt adoption → committed` 收口；Stage4 typed infeasible EvidenceBinding 仍是正式 primary 但保持 landed-red，没有正式 primary 的 INVALID / rejected / 耗尽终态不得伪造 adoption/committed，既有 0 成本 revalidate / 恢复路继续保留。恢复必须复核 profile、subject、prompt/schema、session audit 与 ledger，历史 provider session 只作 adoption 身份，不能伪装 live resume；显式 profile 的 dispatch 永远先于 legacy client 构造，绝不静默回退 DeepSeek。当前 UI 仍未消费 capability，commander/executor/model/effort/egress 选择器属于 Phase 6，不能把后端纵切写成页面已交付 | `结构`（`novel/orchestrator/nodes.py::EXECUTOR_PROFILED_RUNS` + `novel/node_agents.py::EXECUTOR_FACTORIES` + `novel/llm/agent_runs.py::prepare_commit` / `::mark_committed`；`tests/test_stage4_design_session_pool.py`、`tests/test_stage4_agent_boundary.py`、`tests/test_stage5_write_agent.py`、`tests/test_stage5_write_agent_boundary.py`、`tests/test_stage5_agent_runs.py`、`tests/test_stage6_hook_agent.py`、`tests/test_stage7_rename_agent.py` 与 jobs/API/CLI 回归查） |
| R-11.19 | **Phase 6 把整书 Commander、节点 executor 与页面选择面闭合成同一份不可漂移契约。** `/api/agent/capabilities` 是 driver/provider/model/effort/mode 与本机四态的唯一选项源；`default_profiles=null` 时页面必须显式完成 Commander、默认 executor 与逐节点 override 选择，不能取数组首项代选。preview、单本 start 与 batch 共用唯一 planner：完整 resolved profiles、规范化 base、起始代码指纹、stdin-only Commander invocation 与两项授权冻结进 `AgentRunStart`/排队快照；start/batch 在任何 probe/spawn/wire 前同时要求付费与正文外发授权，preview 可在未授权时只读展开。子 Commander 只持 `run_id + contract SHA-256`，从 append-only started 事件复核同一快照；operator runner 再按 exact node/flags 选择 executor profile，显式 agent 主路不带 legacy `--response-repair agent`。Commander 的 provider-neutral driver、session turn、`role=commander,node=null` 成本行与 actual identity 均须闭合，进程状态与独立盘面交付终验正交，账/审计/会话复用或身份漂移只可降为 unknown。所有 headless 审批只经 0 成本 typed `approval show/backfill/seen/gate/template/apply` façade：`seen` 只收 read/global，通用 `gate` 只收 s0/s1/s2 且复用服务 proof，s4 必须经 typed structure apply；模板绑定 current full subject，未审 Stage4A 行带 typed marker 且原样 sentinel 不得提交，fresh B infeasible concerns/proof 与由 durable split lineage 现算的逐章 repair capability 一并纳入 A aggregate，legacy/untracked 或已提交两轮的章不得形成不可执行 request_repair；结构模板默认逐章 `request_replan`，审批写盘不启动作业。前端 start 只能消费最后一次成功 preview 的原请求快照；capability 或任一配置/授权漂移即关闭式拒绝，不从当前表重解析已预览请求 | `结构`（`novel/commanders` + `novel/orchestrator/agent.py::plan` / `::start` + `novel/commander_context.py::load_from_environment` + `novel/agent_session_audit.py` + `novel/llm/ledger.py::append_commander_call` + `.claude/skills/novel-pipeline-operator/scripts/run_step.py` + `novel/cli/main.py::_cmd_approval` + `web/src/components/agent/AgentProfilesForm.tsx`；`tests/test_commander_drivers.py`、`tests/test_commander_context.py`、`tests/test_agent.py`、`tests/test_api_agent.py`、`tests/test_skill_operator.py`、`tests/test_approval_cli.py` 与前端 agent profile / dialog 回归查） |

> ### `R-11.3` 的三条理由（其余两条已被骨架消灭，`Q-8`：**退役要带标注**）
>
> 原型走子进程有**五条**理由。骨架落地消灭了其中三条——来源账的进程级串扰（改成
> `RunContext` 之后**结构上不存在**）· import 期 `_load_dotenv` 单例 · 拿 `sys.exit` 当终态。
> **只换一句话而不标注，将来读的人会以为那个坑还在。**
>
> **剩下三条仍然独立成立，所以规则一个字都不放宽**：
>
> · **CPU 密集会阻塞。** 重合度 / LCS 是秒级的纯 Python，跑在服务进程里会把界面卡死，
>   而这一层的主体动作恰恰是「把两份东西并排放在一起让人看」。
> · **`R-11.8` 要求展示与执行共用同一个 argv 构造函数**：摊开显示的那条命令必须**真的能粘进终端跑**。
>   进程内直调会让这两者慢慢分家，而分家之后界面上显示的命令就成了一句**看起来精确的假话**。
> · **一个节点崩了不许带走整个队列。** 本项目是批量服务，而 OOM / 段错误**在进程内拦不住**。
>
> ⚠ 别读反：`sys.exit` 那条被消灭的是「**运行时**拿它当终态」，不是「子进程用不着退出码」。
> 子进程的退出码仍然是编排层判成败的依据（`R-12.3`）。
>
> **⚠ 本条的范围是「绝不落【产物】盘」，不是「绝不落盘」——这是一次带标注的收窄。**
> 理由没有第二种解法：**「子进程被 OOM 杀掉了」这条记录只有父进程写得出来**。
> 让子进程自己记，`spawn` 失败与被 kill 这两种情况**盘上零痕迹**。
> ⇒ 服务进程写、且只写 `_service/` 下的上传、一次性编辑计划、作业事件/日志与人工审批状态；
> 它们都**不是产物**（不进来源账、不进 `_provenance`、`ARTIFACTS` 里没有它们），
> 由 `tests/test_layering.py` 的写口白名单 + `test_the_service_only_writes_under_service_dir` 钉着。
> ⚠ 那份事件流是 **append-only** 的：**改判也是追加**，不是回去改 `started` 那一行——
> 回去改就毁掉了「被 kill 不损坏」这条性质。
> 〔原型每次状态变化都 `os.replace` 整份 `<job_id>.json`，并**为此写了一段重试**
> ——`PermissionError: [WinError 5]`，因为服务一边写一边被 SSE 轮询读，**实测在测试里必现**。〕

> **R-11.9 为什么不照抄逐段 diff**：那个比的是同一章的两轮稿子，段落大体还是那些段落。
> 这里比的是原文与重写，实测配对率**开卷 44~82% / 闭卷只有 8~25%**——
> 闭卷写手没见过原文，句子没有字面关系。而**逐字原话的锚与臂无关**（闭卷覆盖 50~100%），
> 正好补上塌掉的那一半。实测两本 22 章，带原话的拍 **31/31 与 49/49 两侧全部定位成功**，
> 覆盖 70% / 77% 的拍，**稿内锚点逆序 0**。

---

### R-12 · 编排与作业执行

| # | 要求 | 谁在查 |
|---|---|---|
| R-12.1 | **编排只表达真实依赖。** `stage2.blueprint` 只等待 current/fresh 的称名投影 + MainText，不等待未消费的 Global 字段或 global review；非称名 HARD 留下的 schema-valid Global `.INVALID` 可先物化投影并与人类阅读/填写重叠，同书写回仍串行。最新 Global 的称名 HARD 未收口时，投影必须诚实保持 `unknown/global_naming_blocked`，Blueprint 在任何 wire 前关闭，state/queue 只停在 Global typed edit / `--revalidate`（0 LLM），不得 `--renormalize` 或默认付费重采。Stage4 首项才汇合两支：current **正式** global、current/fresh projection + blueprint、`stage2.blueprint --check-only` 联合 HARD 通过、global review current 且 blocking 面逐项 PASS，缺一项都不得继续；disclosure 非 PASS 只披露、不阻断。Stage4A 虽主要读取 blueprint，也不得对未收口 Stage2 投机付费。Stage2 不新增 global 复检入口，唯一显式 `--check-only` 仍是 blueprint。机械测量不是作业，Stage5/6 在真实消费点即时执行，不占队列项也不制造伪并行边 | `结构`（`contracts/invariants.py::NODES` / `::stage2_review_required` + `novel/orchestrator/queue.py::nodes_up_to`）+ CLI/API 零 wire 早拒与联合复检回归 |
| R-12.1b | 节点要诚实报出「这条规则本次没有跑，不是跑过没事」，且**流程里必须钉死一次 0 成本复检**。⚠ 别拿「这次补跑是绿的」当结论——那是运气，不是保证 | `HARD` |
| R-12.1c | Stage2 逐章抽取按章全量并发、硬上限 32；20 章即 20 个同时调用，40 章峰值只能是 32。Stage4A 是独立的节点级私有 session pool：策略请求串行先行，策略 checkpoint 后的逐章设计实际取 `min(4, NOVEL_CONCURRENCY, 待跑章数)`；配置可以把单书 Stage4 池下调，但不能把它放大到 4 以上，也不复用/改写 Stage2/5 的产品上限。任何并发完成顺序都不得串错逐章 usage/provenance/checkpoint | `结构`（`contracts/invariants.py::STAGE4_DESIGN_MAX_CONCURRENCY` + `novel/cli/main.py::_dispatch` + `novel/llm/stage4_runs.py::DESIGN_MAX_CONCURRENCY` + `novel/llm/ledger.py::chapter_usage`）+ 回归测试 |
| R-12.1d | 供应商限流、账号 entitlement 与产品/节点并发闸必须分开陈述：Stage2/5 项目硬上限是 32，DeepSeek V4 Pro 当前官方账号级上限是 500；Stage4A 的 4 是当前 Claude executor 的**本进程、单书节点安全边界**，依据同一 executor 在 Stage2 真实整书 13 路首波出现快速 403 拒绝后的保守收敛，不冒充 Claude 账号级通用上限。它只约束一条 `stage4.design` 的逐章池；多书 Commander 的总并发仍由独立的 `novel/orchestrator/agent.py::MAX_CONCURRENT` 管，不得把 4 写成账号总闸。正常产品路径不为制造 429/403 越过自身硬闸。真实观测必须显式确认付费、禁缓存、限制请求/重试/token，并保留逐次 ledger 与不含 prompt/响应正文的报告；429 重试/退避由可重复合成故障守卫 | `结构`（`tools/observe_provider_limits.py` + `novel/llm/stage4_runs.py::DESIGN_MAX_CONCURRENCY`）+ 2026-08-16 DeepSeek 真实 32 并发观测 + Stage2 Claude 13 路首波拒绝实证 + 合成 429/退避回归；DeepSeek 供应商口径见 [官方限速说明](https://api-docs.deepseek.com/zh-cn/quick_start/rate_limit) |
| R-12.2 | **结构化调用点必须声明全部必需顶层键**；parse / require / schema / validator 任一闸失败都驱逐坏缓存，修复稿始终重走 `json.loads → require → model_validate → validator`。禁止异常驱动地降低 effort / thinking，也不改供应商或 schema mode；Flash→Pro 保持同 effort / thinking / max_tokens。Stage2 blueprint 使用按失败类型登记的恢复策略：reasoning 中完整 JSON 一旦通过全部闸立即采纳并铸 checkpoint，不再为正常 content 多发一采；BOM / 容器尾逗号只做窄本地修正并全量复验；有可忠实恢复材料的 `answer_lost` / parse / require / schema 首次进入本 run 唯一 batch，修复拒绝或未通过后最多一次 Flash 定向修复、再最多一次 Pro constraint rebuild；dynamic validator 不交给通用 JSON 修复，只走一次 Stage2 定向恢复与一次 Pro rebuild；无有效材料的 `answer_lost` 不重复相同 Flash 原请求，直接 Pro rebuild；`finish_reason=length` 不做同配置重采，进入下一种已登记动作，仍失败即终态。429 / 5xx / timeout 只走传输层有界退避，不与内容动作混算；不可重试 4xx、配置错误、预算拒绝立即终态。失败签名统一绑定 request fingerprint、模型/effort/thinking、request mode、finish reason、gate 与归一 reject detail；同一 `run_id` 内相同签名不得重复相同动作，只能升级或结束。其他结构化调用继续使用各自已登记的通用恢复链；失败候选一律视作不可信数据，动态恢复 prompt 仍按完整候选/诊断摘要/最小诊断逐份预检并记录 `request_mode` + `recovery_from_attempts`。来源账的 adopted model 只认 accepted attempt → ledger 精确联结；拿不到联结就拒绝落盘 | `HARD`（`novel/llm/client.py::ask_json` + `novel/llm/response_repair.py::failure_signature` + `novel/llm/stage2_runs.py`）+ reasoning 单 wire/失败类型路由/动作去重/模型来源账回归 |
| R-12.2b | 下游节点在开始逐条报症状之前，先判一次「上游这份东西是不是整个空的」 | `HARD` |
| R-12.2c | Claude agent 的结构化请求保留 provider schema gate（`--json-schema`），最终 stdin 必须且只能附加一次版本化 delivery instruction：只经内建 `StructuredOutput` 交最终对象，不先打印普通 JSON、不套 `response/result/structured_output` wrapper、不调用其他工具。有 output schema 时成功信封缺 `structured_output` 必须关闭式失败；只有 schema-less 请求可读取字符串 `result`。prompt SHA、request fingerprint 与 checkpoint/恢复守卫必须共用 `AgentRequest.stdin_bytes()` 的真实 UTF-8 字节，生产子进程必须以 binary stdin 原样收取，不得另拼一份或经文本换行转写；Claude 内部 `num_turns` 只作读数，不得被误当成「单次提交」闸门 | `结构`（`novel/agents/base.py::AgentRequest.stdin_bytes` + `novel/agents/claude_code.py` + session/checkpoint 共用渲染落点）+ footer 唯一/错误 wrapper/缺 structured_output/schema-less fallback/raw-stdin/hash 对账回归 |
| R-12.3 | 子进程环境三件套（UTF-8 相关）缺一不可：缺了它，任何打印 `✓` 的节点进管道就非零退出——而一次**成功的付费跑**会被报成失败 | `HARD`（回归测试） |
| R-12.4 | 普通缓存仍有三条语义：关缓存是**同时关读写**；参数不变的重跑可以零 wire；坏响应不得留在可命中的内容缓存。验证后 checkpoint 是第四种、明确独立的恢复状态：命中不记 cache hit，不产生新 wire，保留原 accepted attempt/model/成本联结并发出 `checkpoint_restored`；只允许同 `run_id`、同代码/契约/架构/输入/request/SHA 与闭合 ledger 恢复。Stage2 按章 checkpoint；Stage4A 先铸策略 checkpoint，再为每个已验章节铸独立 checkpoint，策略或任一章失败不得抹掉其他已验 unit，同 run 只补缺失/失败单元。Stage4 checkpoint 不是正式 artifact：N+1 单元齐全后仍须确定性聚合、整书 schema/HARD 与 commit journal 原子提交，之前不得出现部分 `blind_candidate.json`。主动 `--resample` / `--chapters` 首次执行不得命中旧 checkpoint；Stage4A 不开放公开 `--chapters`，恢复只由同一公开 node/run 自动发现 unit checkpoint。canonical `RunContext.run_id` 覆盖首轮 → 修复 → resume → 聚合提交，并进入 ledger、LLM audit、progress、repair、checkpoint 与 adoption；job/queue/agent/chain/attempt id 保持各自含义只建父子关系。手敲 CLI 首次自动生成并打印，resume 必须显式 `--run-id` 复用；历史 JSONL 无 run_id 仍可读但只标 legacy/ungrouped，不回填、不按时间窗口猜。本字段不进正式 artifact provenance、缓存键或贵贱判断 | `HARD`（`novel/run_identity.py` + `novel/run.py::RunContext` + `novel/llm/stage2_runs.py` + `novel/llm/stage4_runs.py` + ledger/audit/progress/adoption 联结回归） |
| R-12.5 | **发出去的 prompt 里不许出现内部结构名**（拍号 `bNN_NN` / 人物 id `char_001` / 规则 id `wr_01` / 内部枚举名 / 节点名）。每层只说自己的语言：prompt 只讲任务，不提节点名、不提消费方、不提归属 | **无守卫 → `B-05`** |
| R-12.6 | **每一份最终逻辑请求先过对应的精确上下文预算，再碰其缓存、外部请求、ledger 或正常产物。** Stage4A 的策略请求与每章请求分别是登记在架构中的最终请求，必须逐份预检；`stage2.global`、`stage4.plan` 与所有统一 `LlmClient` 调用同样按本次最终 system、user 与官方消息 framing 精确计 input tokens。原始 Flash 请求及同 prompt 的 Pro 兜底必须在该逻辑调用首个副作用前同批预检，任一项拒绝即该逻辑调用 0 副作用。R-12.2 的定向恢复 prompt 依赖尚未发生的响应，不能在首发前伪造“最终内容”：它在每次后继采样前按完整候选 → 诊断摘要 → 最小诊断逐份精确预检，只允许读取/发送第一份通过者；全拒时退回已经通过首轮预检的原请求，不能在已经付费后新增一个预算终态。`deepseek-v4-pro` / `deepseek-v4-flash` 的保守有效上下文上限固定为 1,000,000，单次输出上限 384,000，且要求 `input_tokens + max_tokens <= 1,000,000`。tokenizer 固定为 DeepSeek V4 官方 revision `0e1a0e5e52aea73055f50fef6f2423db370265b6`，资源 SHA-256 `8f9f37ca37fdc4f5fd36d5cf4d3b0e8392edb4e894fd10cc0d70b4957c8633cf`；未知模型、依赖/资源不可用或 hash 不符全部关闭式拒绝，不用字符比例猜测。Stage4A 的策略 + N 单章是**语义契约预先登记的聚合拓扑**，不是预算失败后的动态分块；任何一份已登记请求仍不得截断或临时再拆，预算拒绝本身不触发模型兜底。错误必须给出模型、tokenizer 版本、输入、声明输出、合计、两项上限及缩减正文的行动建议 | `结构`（`novel/llm/budget.py` + `novel/llm/client.py::_preflight` + Stage4 agent request 预检）+ 首发零副作用/动态恢复预算降级/边界/Stage2/Stage4/CLI 回归测试 |

> **R-12.5 的守卫开在错的门上。** 现有的泄漏扫描扫的是**模型的产物**，
> 而这条约束管的是**我们发出去的 prompt**——两者是两扇不同的门，而现在只有前一扇有守卫。
> 覆盖率 95% 而漏掉的正是主通道，与没有守卫的差别很小。

> **R-12.2 的实测**：某次规划调用拿回 `{"reconstruction_strategy": "…"}`——**0 个输出章**、
> 正常结束、4477 token 里推理占 4317。它是合法 JSON，于是一路放行**并进了缓存**；
> 旧下游把它摊成 **74 条**派生症状，还附一句「说明 prompt 漂了」。
> **74 条派生症状盖住了一句话能说清的事实。**
> 「提示模型该产什么键」不是校验——校验要单独写。

> **R-12.1d 的可重复入口**：从仓根使用
> `conda run -n novel_refactor python -m tools.observe_provider_limits ...`。
> 不用 `python tools/observe_provider_limits.py`：文件路径启动只把 `tools/` 放进导入路径，
> 在当前 Windows/Conda 布局下找不到同级 `novel/`，会在联网前以 `ModuleNotFoundError` 终止。

---

### R-13 · 批量队列 【**新增能力**，原型没有】

> **`R-13` 与 `R-14` 是本次正式开发相对原型的两项新增**（用户裁决）。
> 其余 `R-1` ~ `R-12` 是 1:1 重建——**形态可以变，能力与守卫不许缩水**。

一次排入多本书，按顺序自动跑到指定阶段，人只在需要裁决的地方介入。

| # | 要求 | 谁在查 |
|---|---|---|
| R-13.1 | 队列以「书 × 目标阶段」为单位，可排入、可取消**尚未开始**的项 | **`结构`**（`novel/orchestrator/queue.py::create` 冻结 `QueueStart` 并追加 `queues.jsonl`；`novel/orchestrator/queue.py::cancel` 只接收 `pending/blocked`，运行中明确拒绝）+ API/CLI 行为回归 |
| R-13.2 | **一本书同时只跑一个作业**这条不变，Stage5 也没有书内互斥例外；队列只决定顺序，不放宽互斥 | **`结构`**（互斥键唯一落点 `novel/orchestrator/jobs.py::active_key`；队列不碰它） |
| R-13.3 | **花钱的项必须一次性预先确认**，不能因为「排进队列了」就变成静默扣费。确认时摊开的仍是**将要执行的那些命令**。⚠ **2026-08-29 用户裁定修订（TODO-45 批次 3）**：durable queue/job 父记录下**身份完全闭合的同一 run**，其原付费授权可跨重启自动续跑——前提同时满足：durable start 中 `paid_confirmed=true` 且正文外发授权仍有效；run/代码/argv/输入/candidate/审批/profile/模型/effort 完全未漂移；仍在原 max-turn/token cap 内；provider session/checkpoint 身份可精确对证（checkpoint 恢复的既有关闭式校验）；不切 provider、不 fallback、不新造命令。自动续跑 = 新 `job_id`（execution）+ `recovered_from_job_id` + 原 `run_id`。任一字段缺失、legacy（原 started 无 run 身份）、漂移或可能串错 session 时停止并落持久 restart-confirmation 提醒，不自动猜；**从未启动过的付费项与手工 CLI 不自动复活**，仍需 CLI `queue-resume --yes` / API `{"confirmed":true}`。恢复会重新逐字展示创建时冻结的剩余付费命令；调用底层 runner 本身不构成授权 | **`结构`**（`novel/orchestrator/queue.py::confirm` / `_confirm_resume` / `::_auto_resume_block_reason` / `resume_incomplete`；`novel/orchestrator/jobs.py::plan_start` 的 run_id/recovered_from_job_id 与 `::_run_steps` 的 env 注入）+ **`人`**（创建与每次不可自动续跑的付费恢复各完成当轮确认，见 `B-19`） |
| R-13.4 | 任一项未绿 → **同书停在那里**，不跳过、不继续往下扣费；另一书继续。exit 1 单列为 `landed_red`：正式产物已经落盘，但本次 typed 结论不通过（包括报告阻断红行与 Stage4B `infeasible`），不能误报成命令没跑成或没有落盘的 `failed`。它与 `failed` 一样会停住该书 lane，并把后续项记 `not_run` 等人处理；四类计数不得合并 | **`结构`**（`novel/cli/exit_codes.py::landed` + `novel/orchestrator/queue.py::_run_lane`；`QueueLaneCounts` 分列 done / landed_red / failed / not_run）+ 两书一红一成与一败一成回归 |
| R-13.5 | 需要人裁决的闸门会挡住队列并明确说出在等谁。Stage2 的特殊顺序是：global 与 blueprint 可先作为各自独立付费命令执行；在最早 Stage4 项前插入无 argv 的 `approval:stage2-global-review` 阻塞项，不能让 review pending 阻断 blueprint，也不能让 Stage4 越过它产生 wire/dispatch。若 review 在 blueprint 运行中提交，决定正常保存；同书写盘互斥仍适用。其余 stage0 批准、Stage5 全书写作授权、文案定稿、人名表定稿与待人工非空照常阻塞；没有 current `writing/writing_proof` 不拿只读默认值猜授权。续跑为 `redo/unknown` 时必须形成无命令的 `resume:manual-decision`，不能把“要重来/判不了”伪装成 runnable | **`结构`**（`novel/orchestrator/queue.py::plan_book` / `_human_gate_satisfied` + `novel/orchestrator/approvals.py::has_record` + Stage2 并行/Stage4 阻塞/REDO/UNKNOWN 队列回归） |
| R-13.6 | 队列本身**不产生新的判定**，也不放宽任何落盘闸门 | **`结构`**（`tests/test_layering.py::test_the_queue_cannot_reach_the_judgement_layer`：`queue.py` 够不到 `novel.report`，连「顺手判一下」都做不到） |
| R-13.7 | 不同小说的作业可以并行执行；互斥范围只落在各自 `sid`。每个后台作业独立启动 CLI 子进程，一本书失败或阻塞不占用另一本书的键 | `结构`（`novel/orchestrator/queue.py::_execute` 每书一个线程，调用 `novel/orchestrator/jobs.py::start`；互斥仍唯一落在 `novel/orchestrator/jobs.py::active_key`）+ barrier 证明两本书同时进入子进程的回归 |

⚠ **批量的风险与单本不同**：单本跑错，人当场看得见；批量跑错，是**一批产物同时坏掉且没人在看**。
所以 R-13.4 的「停在那里」不是保守，是这条能力成立的前提。

持久载体是 `_service/queues.jsonl`：创建、运行、取消、阻塞和恢复全部 append-only，服务重启后
旧 `running` 先改判 `unknown`，再对照 `jobs.jsonl` 与正式产物恢复。作业已经成功，或正式产物
存在且 fresh 时直接记 `done`。仍需执行时重新生成当前计划并与冻结命令逐项比较：相同且全免费才
自动续；相同但含付费项等显式 `queue-resume`；不同则要求重新预览/建队列，不能在恢复时扩张或偷换
授权面。`stage5.write` 的冻结项是 canonical 全书默认命令，或带明确
`--chapters` 的定向命令；执行前还会在作业互斥锁内用 `approvals.authorize_writing`
复核当前全书写作授权、目标章集与 Review 状态。

### R-14 · 断点续跑 【**新增能力**，原型没有】

一次跑被打断（人为中止 / 进程死 / 机器重启）之后，能从断点继续，**不重复付费**。

| # | 要求 | 谁在查 |
|---|---|---|
| R-14.1 | 已完成的阶段按**产物存在性 + 来源新鲜度**判定，不靠一份单独的进度文件——进度文件会与盘上的实际产物分家 | **`结构`**（`novel/orchestrator/state.py::overview`；配一条 AST 守卫 `test_completeness_never_peeks_at_the_job_records`：那份文件的**代码里**够不到 `_service`） |
| R-14.2 | 逐章类阶段（写作）的续跑粒度是**章**：已落盘且通过当前 schema/version/freshness 的章不重跑；文件名发现本身不得冒充完成 | **`结构`**（`novel/orchestrator/state.py::chapters_done` 只发现 canonical 并排除 `.INVALID.json`；`artifact_state` + `stage_state` 逐章关闭式校验后才允许 `done/skip`） |
| R-14.3 | **被打断的跑可以几乎免费续**——已完成的调用命中缓存。这要求缓存键稳定，且**参数不变的「重跑」什么都没跑**这条语义被显式说出来 | **`结构`**（`novel/llm/cache.py::cache_key` 稳定键 + `novel/llm/ledger.py` 的 `calls_made` / `calls_cached` **两个计数器**） |
| R-14.4 | 续跑前必须**重新判定新鲜度**：上游在中断期间变过，就不是「续」而是「重来」，要说出来；判不了就是 `unknown`，也不能折成“待继续”。队列对 `redo/unknown` 均不得构造或自动执行命令 | **`结构`**（`novel/orchestrator/state.py::resume_verdict` 四态；`redo/unknown` 与 `skip/run` 分开，`novel/orchestrator/queue.py::plan_book` 将前两者关闭式阻塞） |
| R-14.5 | 中断留下的半成品（写到一半的章 / 空壳响应）**入显式终态并踢出缓存**，不许被当成「已完成」续过去 | `结构`（`novel/llm/client.py` 空壳响应 evict；`ArtifactStore.write` 临时文件 + 原子替换，所以被杀时不会留下半份 `chNN.json`；stage5 续跑只认通过契约与落盘闸门的正式章，fixture 覆盖“已有章跳过、缺章补跑”） |
| R-14.6 | 进程被杀时**不保证原子性的地方要列出来**。⚠ 原型里「不做取消」的理由曾写着「落盘非原子，中途 kill 会留下截断的 JSON」，而落盘**早已是原子替换**——结论仍成立（钱已经花了），但它现在只剩一条腿撑着 | **`结构`**（那张表在 `docs/schema/_orchestration.md` §八，**六类**，每类带「崩在中间会留下什么 / 怎么自愈」） |

⚠ **`R-14` 与「不做作业取消」不冲突**：不做取消是因为**调用在途时钱已经花了**；
续跑处理的是**已经被打断之后**怎么办。两件事。

> ### `R-14.6` 那张表的两条结论（全文在 `docs/schema/_orchestration.md` §八）
>
> 六类里 **四类会自愈**（多文件写 / `_rounds/` 归档与新稿之间 / 提升路三步的中间窗口 /
> Stage5 canonical 与 trim sidecar 的提交窗口），
> **一处不自愈**——「账本行与产物之间」：**钱花了、产物还没写**。
> 所以账本行**先写**：宁可多一条「花过但没产出」的记录，也不要一次看不见的支出。
>
> 第六类**不打算补**：**互斥锁与 agent 待跑队列是进程内内存**，服务重启即全丢，
> 多实例、人在终端手敲 CLI 都绕得过去。不用锁文件补的理由是——陈旧锁要有清理策略，
> 清理策略又要有守卫，而**一把会说谎的锁比没有锁更坏**：它让人以为互斥成立。
> ⇒ 记在表里，不假装它不存在。
>
> ⚠ 这张表本身就是 `R-14.6` 要的东西。原型那条「不做取消」的理由
> **退役了而没人标注**，只剩一条腿撑着——这张表就是那次标注。

---

## 三、工程约束 Q-1 ~ Q-12

跨阶段成立。它们不是「最佳实践」，每一条都对应一次已发生的损失。

### Q-1 · 不静默
截断 / 缺章 / 解码失败 / 任何异常一律入**显式终态 + 报警**，绝不悄悄接受、不自动修正。
可疑但不算错的进显式 `warnings[]`，交人工审核。

**记账不是终点，报出来才是。**「落盘前的确定性改写」与「返工/提升的历史」都必须记进产物
（因为改写跑在落盘前，校验器已经不可能再从字段值上看出发生过什么），
而**每一个这样的记账键都必须在校验报告里有一行出口**。三条形式要求：

- **空也要报**，报成 `0 处`，不是不打印这一行——「没改过」与「没在报」必须分得开（`Q-2`）；
- **一行常驻 + 非空才展开**，否则报告变噪声，而噪声会让人不看报告；
- **出口在校验报告，不在 stdout**——stdout 会随会话消失，那不算出口。

> **实测代价**：stage2 的 8 个记账键有 5 个没有任何出口
> （叫法起点被抬了几处、区分词剥掉了什么、人删过哪条秘密、哪几章重生成过、各修了什么），
> 退役 Stage3 v2 当时已删除 LLM attempt / revalidated / gates_skipped 三笔旧账；当前机械测量
> 连独立产物都不再生成，更不能为填槽显示三行恒定的“未记录”。

> ⚠ **反过来的那一面：不是每一次「落盘前写入」都值得记账。**
> 判准是**那个位置本来有没有东西**——有，才叫改写（原值被销毁 ⇒ 记账 + 判据改断言计数）；
> 没有，那叫**初始化**，它的后置条件由代码自己保证，记一份「本来是空的」只会制造恒定的噪声。
> 实测反例：stage4 的「篇幅方式」记了 17 条改写，**17 条全是「本来是 null」**
> （规划节点根本不产这个字段），而那份记账既没有出口、也没有一个 bit 的信息。
> ⇒ **模型越界多写一个字段**这件事该由**响应侧 `extra='forbid'`** 挡，不是由产物侧记账。

> **结构化响应一律 `extra='forbid'`。** 未知字段被静默丢弃正是本条要挡的形态：
> 上游多写了一个字段、或字段名拼错，校验照常通过而那个值消失了。
> 它同时是「模型产出了我们没要的字段」这一类的**唯一守卫**——落盘之后再记账已经晚了一层，
> 而且那时已经花过钱。

### Q-2 · 「不知道」与「没问题」必须可区分
这是本系统最贵的一条。它有四个落点：
- 产物新鲜度**三态**（新 / 陈旧 / **不知道**），指纹缺失记作「不知道」不是「匹配」
- 结论严重度**四级**，其中「查不了」独立成一级，既不是通过也不是失败
- 校验徽章**三态**：「通过 + N 项未验证」渲染成琥珀**而不是绿**
- 作业状态含「不知道」

> **实测代价**：某次 23 章成稿与任何一版上下文都配不上（匹配 1/23），
> 而产出它的那份上游已被覆盖，**配对关系永久丢失**——而界面当时全绿。

### Q-3 · 每一层确定性的部分都必须能 0 成本重算
否则修自己的 bug 要付费。命名体系本身是资产，**唯一落点是 `contracts/invariants.py` 的
`FREE_ENTRIES`**（本节不再抄第二份清单——抄一份就是又一个会分家的落点）。

> **⚠ 这张表不全的后果是【反】的，所以缺口必须量、不能想。**
> `is_paid` 拿它当白名单，未知 flag 一律按付费算（`Q-10`：不知道要倒向贵的那一边）
> ——于是一个漏登记的 0 成本动作会被画成橙色、弹二次确认，**而它根本不花钱**。
> **表不全 ⇒ 系统在对用户说假话。**
> 实测过一次：拿冻结证据基线（本文 §八登记的 `enums.json`，含 36 条 `actions`）
> 逐条对，现役后端真正当 0 成本用的 flag 有 **13 个**而表里只有 **9 个**，交集 8 个。
>
> 同一个语义在不同节点上必须叫同一个名字；恒等基线统一使用 `--identity`。

**配套纪律**：重算时只对**本次新引入**的错误入终态——旧违约照报但不挡路，
否则一条不相干的旧问题会把重算这条路永久堵死。

⚠ 原型里这条**三个节点行为不一致**（一个有提升路、一个没有、一个把问题稿双双排除掉，
代码直接叫你重付一次钱）。新骨架**必须一致**。

### Q-4 · 每份产物必须记住自己是从哪份上游来的
`{prompts 指纹, 主模型 model, 实际采纳 adopted_models, code 版本, inputs 指纹, params}`。
**校验器先报来源，再报判定**——对不上时报出来的一堆违规是「拿新尺子量旧布」，
不说破就会被当成一次回归。

⚠ **读上游只有一条路**（那条路记账）。原型里有十几处裸读绕过它，绕过就记不上账。
⚠ **取不到指纹的条目 = 那份上游【已经不在盘上】，要报出来。** 原型的比对函数对这种条目
**静默跳过**——于是一条指向已删文件的边既不报错、也不报「不知道」，
只是安静地当作一条正常的上游边（实测就有一条：0 成本提升路读完问题稿就把它删了）。
⚠ 指纹是**文件级**的：改一个不相干的字段会让整份产物的指纹失配，把一批结论降级成「读数不可用」，
且没有重新挂钩的路。字段级指纹是一次契约改版，值得在新骨架里就做对。

### Q-5 · 先校验后落盘
阻断级错误**不覆盖已有产物**，问题稿单独存一份带 `.INVALID` 标记的文件。
配 0 LLM 成本的强审计提升路（`--revalidate --actor --reason`）：按**当前**
判据重建派生字段并重判，记录被拦时 baseline hash、当前 raw/canonical SHA-256、
RFC 6901 改动路径、自报 actor/reason 与 `edited_by_human`，合格才提升。

**为什么必须有提升路**：没有它，修一条**判错**的判据要为每一章再付一次费，
而重掷回来的是**另一份稿**，修没修好无从对照。

⚠ **问题稿与被提升的稿【同样要带来源】，并且要记「它是被提升上来的」**（2026-08-10 补）。
提升保留原 prompts/model/adopted_models/inputs 与生成轮次，审计不得把人工改过的字节洗成新模型结果。
`--apply-edit` 则是另一条 typed 人工修订路：它开新人工轮次并使旧主体绑定结论过期；
两者不得互换或共用一个 `revalidated: true` 布尔值代表。
原型这条整个漏了：问题稿用裸写绕过了带来源的落盘、提升路又不传来源，
而它的注释还写着「来源信息沿用原稿里那一份」——**实测盘上 1/3 本的计划因此没有任何来源信息**
（用哪个 prompt 产的、哪个模型、照哪份上游、落盘前改写了什么，全部永久丢失），
而**没有一行报告会说出来**。⇒ 这一条同时是 `Q-1`（不静默）与 `Q-4`（来源）的交叉缺口。

### Q-6 · 单一落点
一个枚举 / 词表 / 区间 / 判定，全系统只能有一份定义。
**prompt 里那一份必须是插值来的**，不许手抄。

模板引擎的选型判据只有一条：**占位符缺值时必须抛异常**。
缺值渲染成空串会静默产出一个带窟窿的 prompt，与「显式终态」正相反。

⚠ 已实测的分家：prompt 手抄的写法词表写着「象征/强化感官」而真词表是「意象/感官」、另 8 词一个没提；
另一处漏了整个一档。**模型按 A 算、校验按 B 判，谁也不会出声。**

### Q-7 · 中间层只存决策与引用，内容由确定性代码搬运
**凡是让 LLM 转抄的地方，都会长出一个新的漂移源。** 对原文字节成立，对任何上游产物也成立。

⚠ 已实测：某中间层把上游的事件文本改写成合并散文，106 拍相似度中位 0.84、最低 0.44，
既造事实（「某人身边人」→「某家丫头」）又丢内容（整段具体材料被压没）。

### Q-8 · 当前模型与读取边界都只保持单形

契约字段、prompt 指令、CLI 参数、判据和报告模型只保留当前形状；不在 Pydantic 模型里提供字段别名、
双形状 union、退役判据占位、字段解释键别名或旧命令转发。所有载体继续 `extra='forbid'`。
`contracts/schema/__init__.py::ARTIFACT_SPECS` 是“路径 → 当前版本、模型”的唯一注册表；普通对象
顶层带 `_schema_version`，RootModel 使用 `{_schema_version,payload}` 信封。写盘恒写该路径在注册表的
`current_version`，不手抄一个全局版本号。

**本仓没有迁移机制。** 读取只接受注册表声明的当前版本：无版本、低版本、高版本与非法值一律明确
拒读，错误必须同时报出文件、盘上版本、当前版本与下一步（从 producer 重建）。要破坏某个产物的形状，
做法是把该路径的 `current_version` +1，并在同一批里回答“存量产物由谁重建”——不是加一条迁移把旧
字节读成新形状。overview、inbox 与只读视图仍按产物边界降级，坏一份不抹掉整本其余可读区域。
`.INVALID` 与 `_rounds/` 是当前运行证据，同样必须是当前版本。

### Q-9 · 判据要报出自己的基线，且探针的名字要等标定完再起
- 词表类判据没有基线命中率，等于不知道自己在说什么
- **告警与度量不要共用同一个分支**：把度量挂在「没问题」那一支上,
  等于规定「一出问题就不许知道现状」——而那恰恰是最需要知道现状的时候
- **抢跑的命名会误导**：一个叫「幻觉探针」的东西真跑出来幻觉 0 例，它抓到的是另外两件事——
  都是真问题，都不在它的名字上。后来读报告的人会按名字去解释数字

### Q-10 · 钱
- 花钱前必须人确认；能 0 成本离线验证的先离线验证
- **贵贱只有一处算**，且取决于「跑什么」与「带什么参数」**两者**
- 免费判定用**白名单**：没逐个核对过分支的参数、认不出的动作，一律按花钱算。
  「不知道」必须倒向贵的那一边
- **不显示预估花费**——猜一个数写在界面上，比不显示更坏
- **终审成本只认 `<base>/_service/ledger.jsonl`**：按 sid/outcome 过滤，实付与缓存命中
  分开统计。产物 `_provenance.usage`、日志和报告是局部追溯证据，不得与 ledger
  混加或替代它。

### Q-11 · 缓存
三条语义必须显式：关缓存**同时关读写** · 参数不变的「重跑」什么都没跑 · 被打断的跑可几乎免费续。
**空壳响应必须踢出缓存**，否则「重试」在下一次进程里会退化成「把同一个坏响应读了两遍」，
而它长得与一次真调用一模一样。

### Q-12 · 守卫本身要有守卫
- **关卡的判据必须跟着被它检查的契约一起改版。** 契约改了而关卡没改，关卡不会报错——
  它会继续输出一个看起来正常的结论。已实测：四条确定性检查在新契约下**三条静默失效、
  一条全量误报**（结论恒为「需修订」）。两种故障方向相反、危害相同：**报告不再携带信息**
- **改字段名时，搜旧名的范围必须覆盖所有下游节点，不只是校验器。** 读着已改名字段的消费方
  **不会报错，它只是安静地什么也不做**。已实测三例，其中一例让「必须覆盖的事件」清单
  变成一串空项目符号，而解释「为什么必须列出来」的注释还原样挂在上面
- **修复步骤和关卡一样会静默失效**，而且更隐蔽：关卡至少还会输出一个 verdict

---

## 四、无守卫清单 B-01 ~ B-23

> **这是新骨架关卡层的施工清单。**
> 下表每一条都问过「哪一段代码在查它」，答案都是「没有」。
> 处置只有三种：**补一条判据** / **把这个自由度整个收走** / **明知不查但要写明**。
>
> ⚠ 补 prompt 之前先确认那条要求**本身是对的**——已有一例是要求错了（`B-12` 那条打架的）。
> ⚠ 收走自由度之后**别把它记成质量改进**：去掉的是一个无守卫、高方差的自由变量。
> **没有守卫 ≠ 已经出事。**

### 4.1 最要紧的五条

| # | 约束 | 现状 | 建议处置 |
|---|---|---|---|
| **B-01** ★ | **发明越界七项里的五项**：新事件 · 新地点 · 新道具 · 新因果 · 新时间跳跃 | **零校验。** 只有「新数字」有探针、「新人物」有部分探针（召回有缺口：用本书没出现过的姓造人、不带称谓后缀的光名字都抓不到）。地名后缀是普通名词、无硬左锚，实测 82 条候选几乎全假 | **已裁决（2026-08-10）：不补，直到有 ground truth。** 三条理由都有实测：① gate1 在抽象层做语义判定时**护栏自己成了发明源**，已整节退役；② **唯一 `full` 的那一项自己就零判别力**——`invented_number` 在 3 本里有 2 本被书级恒等闸门判死（头牌 2/8 vs 源章 2/8；三岁 1/9 vs 源章 **2/9**）；③ 修复回环**实测会诱发**自造内容而当轮判定一条都指不出它。<br>⇒ 七项的身份是**给人核对的料**（同 `name_surface_survey`），不是关卡。**「探针报 0 条 ≠ 约束生效」必须写在报告里** |
| **B-02** | **旧事件拍的落笔顺序** | **现役暴露面已关闭。** 旧 event/beat 写作骨架已经退役；Stage4A 决定新场景披露顺序，Stage5 响应由 `novel/stages/stage5/write.py::_ordered_segments` 按计划 slot 确定性归位，不能临场另排一套。至于 A 选出的顺序是否更好没有 ground truth，留在 `B-17` 的结构读数 + 人工裁决面 | 不恢复“源拍位置单调”判据；那会把已去序的规划重新锁回源顺序。执行顺序由 `R-7.1` 的结构口关闭，审美优劣按 `R-5.20` 交人 |
| **B-03** | **旧开卷路径的重合度没有阈值** | **历史闭环记录，现役暴露面为空。** 旧盘面逐章重算：旧闭卷 0.0~2.4%，旧开卷 0.9%~96.8%，不存在可标定的开卷一刀切线 | **2026-08-24 以收走能力关闭，不是以发明阈值关闭。** 现役只有 canonical 闭卷 `stage5/chNN.json`，上界为 `SOURCE_OVERLAP_MAX = 10%`（`INFO`）；旧目录只读保留并报 `legacy_incompatible`，CLI 没有可恢复旧能力的入口 |
| **B-04** | **「必须成立、不必写在纸面上」那一组** | 机械层没有，且对应判据明确标为「查不了」；历史 Stage5 书级语义节点已退役，不再是现役覆盖 | 机械层继续诚实标注 `unver`；没有 ground truth 时不为了消掉盲区恢复一个自动语义门 |
| **B-05** | **Stage4A prompt 不许泄漏源序或正式内部 id** | **本缺口已按 A 相边界关闭。** `build_blind_projection` 是唯一构造口，正式外发面只含去序 opaque alias、事实语义、因果与状态；生产集成测试直接检查真发 prompt 不含 fact/source beat/stage/material/人物正式 id、原文或源区间。B 相在结构冻结后才有意接收 candidate scene id 与关闭式证据目录，不能把它反推成 A 的输入 | 维持 `R-5.5` 的 prompt 侧隔离守卫；不要把“B 为绑定证据而看正式引用”误报成泄漏，也不要把其它节点的 typed id 需要偷换成 Stage4A 的源序暴露 |

### 4.2 其余（按节点分组，逐条已核实「谁在查 = 没有」）

| # | 节点 | 无守卫的约束 |
|---|---|---|
| **B-06** | 计划 | A/B 的 strategy、goal intent、brief、purpose/reason 与 infeasible reason 等自由文本仍可能发明人物、事件或设定，也可能提前泄底。typed fact/stage/material/participant 引用能守引用闭合，却不能证明这些自由文本的语义忠实 |
| **B-07** | 逐章抽取 | 不为首尾相接拉伸区间 · 主谓宾必须明确不许省略施受关系 · 别把「靠什么成立」摘要掉 · 硬数字必须原样进事件文本 · 原文自相矛盾时如实记录矛盾 · 不许照抄例子里的名字 · 摘要不照抄原文措辞 |
| **B-08** | 全书抽取 | 只收结构性规则 · 规则描述必须连「怎么运作」一起写 · 秘密的四类收录判据 · 冲突阶段名要能定位 · 弧线两个字段的形状。**另加五个枚举**（人名区分度 / 规则类型 / 称谓场合 / 弧线变化类型〔开放枚举〕/ 叙述人称）——已有常量、两处 prompt 已插值，**但仍无任何校验**。<br>⚠ `叙述人称` 的取值面 2026-08-10 收正成 `first｜second｜third`：原型写的是 `first｜third_limited｜omniscient`，**把人称与限知/全知两个轴塞进了一个字段**，而且表达不了第二人称（语料里有一本真的第二人称小说）。Stage3 v2 已删除 `narrative_distance` / `interiority`，所以限知/全知当前是明确缺口，不得继续声称有现役载体 |
| **B-09** | 叙述视点与退役文风 | Stage3 v1 的“可迁移、逐章可见、可执行规则、素材分区”等定性质量面已随字段和消费方整体退役，**不再是待补守卫**。仍存在的缺口只有限知/全知与语法人称可能混淆（见 `R-2.10`）；它没有现役独立载体，不得以此为由静默复活 Stage3 定性 schema |
| **B-10** | canonical 写作 | 不把潜台词说破·换叙述人时声音也要换，两者没有确定性真值。历史开卷路径的表层修饰/整段照抄要求已随能力退役，不是现役条款。见 `R-7.9b` | 人称的狭可判域已由 `R-7.9` 常驻报数；剩余语义盲区继续明示，不冒充已守住 |
| **B-11** | 修剪 | **已关闭（2026-08-18 收缩臂整臂下线）**：曾经的关闭方式是「同槽结构化候选 + 确定性 claims 对账 + 校准 A/B 双盲同 PASS 才采用」，而那道校准闸从未在任何宿主上打开过。现在**没有任何自动收缩路径**：超上限章零调用落 identity `TrimAudit` 转 `verification`，由人 `--retain-pretrim` 销账或 `--apply-edit` 改写整章。 |
| **B-12** | 文案 / 改名 | 不说破任何答案（字面探针**自陈判别力为零，别当门用**）· 只引用正文真实出现过的句子〔标「查不了」〕· 不歪曲人物关系〔标「查不了」〕· 新名贴合时代与性别气质 · 同辈共用字、兄弟姐妹只差一字等**跨名字**结构关系。<br>⚠ 单个旧称呼内部的数字、排行、亲属、头衔标记已由 `R-10.12b` 确定性守住；本 B 只保留无法靠字面不变量判定的语感与跨名关系。 |
| **B-13** | 旧开卷写作 | **历史缺口已随能力关闭。** 旧 user 消息曾发「现场/回忆/转述」标签，system prompt 没有解释；实验补强没有稳定净收益 | 现役只有一份 canonical 闭卷 system prompt，旧路径为 `legacy_incompatible`，差异面结构性消失 |

### 4.3 已量出来、但没有下文的

| # | 条目 | 状态 |
|---|---|---|
| **B-14** | 身份描述里的结局 / 未揭示秘密 | **没有确定性防线。** 词表命中 0/17 而人读真违规 6/17（另一本 2/40）。处置不能是加词、也不能按章收窄（会掏空写作节点对角色的认知）。结局字段本身是对的，病在身份描述把它又讲了一遍，而两者的最长公共子串只对 1/3 强，**做不成可靠探测器** |
| **B-15** | 改写式泄底 | **历史闭环记录。** 曾以 secret-blind claims → secret alignment 两相付费审判接线；2026-08-17 根据实跑证据转授到 Stage2 后见之明与交付对账，旧 Stage5 节点、产物和判据全部下线 | 现役处置见 `R-3.7d` / `R-7.18b` / `R-7.18c`；不得把历史实验描述成当前 Stage5 能力 |
| **B-16** | 设定块的剧透闸门 | 字面子串包含，**命中 0/8，包括一条已知真泄漏**。真根因在产地：设定块里装着情节，修法是收紧抽取判据——那要重跑（付费） |
| **B-17** | 计划的场景轻重与顺序 | 场景数量、章/场 `weight`、预算分布与披露顺序哪种更好没有 ground truth。现役 `scene_budget_distribution`、逐字占比与结构度量只常驻报读数，不自动升成审美阈值；收口依赖 hash-bound typed 人工结构裁决 |
| **B-18** | 定向修复被驳回之后 | **没有下一条路。** 回归护栏会正确地驳回一次「顺手少了一条 hinge」的重生成并保留旧稿，但这一层**没有**写作层那套「换一次采样再试一次」⇒ 一次付费调用白花、那一章仍是红的。**同一个问题形状在写作层有解、在抽取层没有** |

### 4.4 按设计就没有判据的（`人` 是正确答案，但必须写明）

| # | 条目 | 说明 |
|---|---|---|
| **B-19** | Stage5 全书写作授权 | 是否按当前 Stage1 正文与 Stage4 计划启动一轮付费写作是人的决策，无自动判据可言。决定只落 `_service/approvals/` 的 `writing/writing_proof`，不进入小说产物；系统能验主体当前、测量契约当前、目标章在计划内，不能替人授权支出 |
| **B-20** | 非正文剔除的正确性 | **确定性验不了**——「这一段到底是不是番外」没有结构不变量可查，而正文体已按契约丢弃、盘上不留副本。判据已按 161 本语料标定（27 本命中 / 0 假阳），但那是「在这批语料上没抓错」，不是「对任何输入都不会抓错」。每本必须打印一条「剔除的正确性：本层验不了」 |
| **B-21** | 改名的替换覆盖率 | **验不了。** 只能替换人物表里有的称呼，人物表漏掉的角色这一层**结构上够不着**——实测漏过一个（靠残留探针捡回来）。别把「残留 0 条」读成「人名都换干净了」 |
| **B-22** | 新名的质量 | 时代感与雅俗没有确定性判据。**现役闸门就是「人定稿」那一步**，这一点不会变。同理开篇文案的定稿 |
| **B-23** | 提不出表面探针的事实 | `R-7.22` 会诚实地把无专名/数字的 fact claim 记为 `fact_landing_unver`，但确定性层随后没有第二把语义尺子证明它是否落地。这不是“查过没事”。同场的 verbatim / keep_value / must_appear 只能守各自材料维度，不能替代事实语义；代词化又已实测会让专名探针假阳。⇒ 先用真书盲读标定，不凭手感开自动阈值 |

---

## 五、反向覆盖率

> **验收本文档的第二个方向**：每一条现役守卫都要能追到本文档里的一条需求。
> 追不到的守卫有两种可能——需求漏写了，或者那条守卫本身该退役。
> **上一版做这个检查时抓出了 5 处需求漏写。**

新骨架落地时按这个清单逐条对：

| 守卫所在 | 应对应的需求 |
|---|---|
| 非正文剔除的判据表（`stage0`，**13 条现役**） | `R-0.1` ~ `R-0.14` 中声明了守卫的那些 |
| 分章校验器（`stage1`，**9 条现役**） | `R-1.1` ~ `R-1.15` |
| 抽取的判据表（`stage2`，**68 条现役**，**两个节点共用一张**） | `R-2.1` ~ `R-2.9`、**`R-2.5b`**、**`R-2.6b`**、**`R-2.9b`** ~ **`R-2.9d`**、**`R-2.11b`**、**`R-2.14`**、**`R-2.19`** ~ **`R-2.21`**、`R-3.1`、`R-3.2`、**`R-3.2b`**、`R-3.5`、**`R-3.6b`**、**`R-3.6c`**、**`R-3.7b`** ~ **`R-3.7d`**、`R-3.8`、`R-3.9`、`R-3.13`、`R-3.14`、**`R-3.14b`**、**`R-3.16`**、**`R-3.16b`**、**`R-3.17`**、**`R-3.18`**<br>（`R-3.6` / `R-3.7` / `R-3.18b` 归 `载体`，`R-3.11` 归调用前结构闸、`R-2.10`/`R-2.11` 无守卫 → `B-08`、`R-2.12` 无守卫 → `B-14`、`R-3.10` 无守卫 → `B-07`、`R-3.12` 无守卫 → `B-18`；`R-3.15` 由 Stage 2 `SourceSpan` 载体守住） |
| 计划的判据表（`stage4`，**33 条现役**，计划与上下文共用一张） | `R-5.1` / `R-5.2`、**`R-5.3b` / `R-5.3c`**、`R-5.4`、`R-5.6` / `R-5.7`、**`R-5.9b`**、**`R-5.10b` / `R-5.10c` / `R-5.10d`**、**`R-5.11`**、**`R-5.12` ~ `R-5.19`**、**`R-5.24`**（`R-5.3` / `R-5.11b` 归 `载体`；`R-5.5` / `R-5.8` / `R-5.10` 归 `结构`；`R-5.9` 与 `R-5.20` 由人结合无阈值读数判断，旧自由文本缺口仍见 `B-06`） |
| 上下文组装的后置条件（**同属上面 `stage4` 那 33 条，不另计**） | `R-6.1` ~ `R-6.4`，另加一条**跨层**的 **`R-3.3b`**（`ctx_hinge_delivered`：抽到的保真点必须条条出现在下发面上——它守的是 stage2 与 stage4 之间那一步，因此挂在 `R-3` 而实现在本层）（`R-6.5` / `R-6.6` 归 `结构`、`R-6.9` / `R-6.10` 归 `载体`、`R-6.7` 无守卫 → `B-16`、`R-6.8` 无守卫 → `B-01`） |
| 写作与验收的判据表（`stage5`，**51 条现役**，全为机械判据） | 严重度实算为 **hard 7 / info 26 / metric 13 / unver 5**；`repair` 实算为 **UNIQUE 12 / FREE 12 / NONE 27**。对应 `R-7.3` / `R-7.3b`、`R-7.4` ~ `R-7.9`、`R-7.11`、`R-7.15` ~ `R-7.18`、`R-7.20` ~ `R-7.25`、`R-8.1b`、`R-8.3b`、`R-8.9b`、`R-8.11`。Stage5 不接收 Stage3 定性块；未揭示秘密边界直接从 Stage2 时间线计算，不再登记重复 Context 清单的 `secret_unknown`。`R-7.18b` / `R-7.18c` 的旧 Stage5 语义节点已转授；`R-7.19` 的旧判据已随非 canonical 路径退役。`R-7.1` / `R-7.12` / `R-7.24` / `R-7.26` / `R-8.1` / `R-8.4` ~ `R-8.6` / `R-8.7b` / `R-8.9` / `R-8.13` ~ `R-8.16` 归 `结构`，`R-8.2` / `R-8.3` / `R-8.7` / `R-8.12` 归 `载体`，`R-7.2` / `R-7.11` 是人，`R-7.5b` / `R-7.9b` / `R-7.10` 仍是明示盲区。<br>⚠ 本层不再有现役语义判据池；结构/载体行说的是机制，不是另加一条产物判据 |
| 文案的判据表（`stage6`，**23 条现役**，机械判据与语义审判共用一张注册表） | `R-9.3`、**`R-9.3b`**、`R-9.4` ~ `R-9.6`、**`R-9.9` ~ `R-9.21`**、**`R-9.23`**（`R-9.1`/`R-9.2`/`R-9.22`/**`R-9.24`** 归 `结构`、`R-9.7` 见 `R-9.19`、`R-9.8` 见 `R-9.20`） |
| 人名替换与成书的判据表（`stage7`，**35 条现役**） | `R-10.2b`、**`R-10.3b` ~ `R-10.3d`**、`R-10.4` / `R-10.5`、**`R-10.6c`**、**`R-10.7` / `R-10.7b`**、`R-10.9`、`R-10.11`、**`R-10.12b`**、**`R-10.13` ~ `R-10.14b`**、**`R-10.16` ~ `R-10.18`**、**`R-10.20`**（`R-10.1`/`R-10.2`/`R-10.3`/`R-10.6`/`R-10.6b`/`R-10.8`/`R-10.15`/`R-10.19` 归 `结构`，`R-10.10` 无守卫 → `B-21`、`R-10.12` 仅余语感与跨名关系无守卫 → `B-12`） |
| 界面的结构纪律测试（`tests/test_api_discipline.py`） | `R-11.1` ~ `R-11.4`、`R-11.6` ~ `R-11.11`、**`R-11.13`** |
| agent capability / profile 契约（`tests/test_agent_capabilities.py`、`tests/test_schema_api.py`、`tests/test_api_http.py`） | **`R-11.14`**；直接 job 纵切另由 `tests/test_orchestrator_nodes.py`、`tests/test_orchestrator_jobs.py`、`tests/test_api_http.py`、`tests/test_cli.py` 及 Stage2/4/5/6/7 agent / recovery 回归守 **`R-11.15` ~ `R-11.18`**；Commander、页面双角色配置、运行契约与 typed 代批由 `tests/test_commander_drivers.py`、`tests/test_commander_context.py`、`tests/test_agent.py`、`tests/test_api_agent.py`、`tests/test_skill_operator.py`、`tests/test_approval_cli.py` 与前端 agent profile/dialog 回归守 **`R-11.19`**；并守 `R-11.11` 的 nullable 历史身份不得倒推 |
| 编排层 | `R-12.1` ~ `R-12.4` |
| 跨层 | `Q-1` ~ `Q-12` |
| **（新建，原型无对应守卫）** | `R-13.1` ~ `R-13.6`、`R-14.1` ~ `R-14.6` |

> ### `R-11.10` 已覆盖真实 HTTP 行为
>
> 通用 `POST /api/jobs` 已开放，并与预览共用 `plan_start()`。同一本书第二个启动请求
> 返回 409；不同小说的后台作业可并行。Stage5 的 `--chapters` 也只是同一书级作业的目标参数，
> 不创造书内互斥例外。
> 覆盖见 `tests/test_api_discipline.py` 与 `tests/test_orchestrator_jobs.py`。
>
> 持久批量入口同步开放：`/api/queues`（列表/创建）、`/preview`、`/:id`、`/:id/resume`
> 与 `/:id/items/:index/cancel`。预览纯读；创建先确认并冻结命令；取消只接收尚未开始项；
> 服务 lifespan 自动恢复非终态队列。公开面由 `tests/test_api_http.py` 精确钉住。

⚠ `R-13` / `R-14` 是新增能力，**原型里没有任何守卫可供反查**。
它们的「谁在查」一律是 `待建`——这一列不许在实现之后还留着 `待建`，
否则新增的两项能力会长成本文档第四节警告的那个形状：**只写在需求里，没人在查**。

**没有守卫的，一律指向 `B-01` ~ `B-23`。** 一条需求既没有守卫、也不在 B 清单里，
就是这份文档的漏洞。

---

## 六、产品与界面规格

**本节内容已迁出，契约不再保存第二份。** 现役落点：产品边界与八阶段的人类任务 → 仓根
`README.md`；当前路由与屏幕职责、UI 硬规则 U-01～U-20、技术与数据缝 → `web/DELIVERY.md`。

节号保留不重排：§七~§十 在仓内有几十处引用。

## 七、修复机制与成本上界

校验结论按 `remediation` 分四类；路由是策略，不在 CLI、界面或 agent 中临时猜：

| remediation | 谁修 | 准入与路由 | 自动成本上界 |
|---|---|---|---|
| deterministic | 代码 | 运行 Rule.fix 指向的同层免费节点与 flag，注册期验证路由真实存在 | 一次免费命令 |
| llm | stage5 写手 | 只接受 `repair == UNIQUE` 且 `unresolved_rounds < 2`；directive 从 Rule 唯一派生 | 每章每判据最多两轮付费 |
| human | 人 | 真人工路由为 human_only / upstream / approval / verification；通过 typed apply-edit、pick、hash-bound resolution 或 wontfix 收口 | 无自动费用 |
| none | 无 | metric 只报告读数 | 无 |

处理顺序：首轮生成 → 机械检查 → deterministic 先修 → UNIQUE 才可付费修 → 连续两轮未消转人 →
全规则回归 → Stage5 Review 只在 `review_status == fresh` 时驱动后续；Stage6 才有独立语义审判。unver 与 skipped 不属于四种修复，
也不得洗成通过。

唯一落点：路由算法 `novel/report/builder.py::remediation_of`；付费筛选
`novel/stages/stage5/write.py::revise_targets`；轮次上限
`contracts/invariants.py::STOP_REWRITE_AFTER`；八类清单 `novel/views/inbox.py::inbox_view`。

现役 Stage5 注册表共 51 条：严重度 **7 / 26 / 13 / 5**（hard / info / metric / unver），
`repair` **12 / 12 / 27**（UNIQUE / FREE / NONE）。历史收窄实测曾将盘上可修复条目从 36 条收到 9 条、
其余 27 条转人工；那是当时语料读数，不是现役规则数。现役路由仍禁止把密度、比率和档位这类多解问题交给模型“修绿”。

## 八、冻结证据基线

### 8.1 唯一保留位置与守卫

历史交接包和 design brief 曾各保存一份相同 JSON，`web/src/mocks/` 又保存第三份。清理后只保留
`web/src/mocks/` 的 20 份原始字节。

⚠ **字节真值不在本文档。** 文件名、字节数与 SHA-256 逐份登记在
`web/scripts/mock-baseline.json`，由 `web/scripts/check-seams.mjs` 读取，逐份验证文件清单恰好
一致、字节数与哈希均不变。人读的散文与机读的数据不许同存于一张 markdown 表：
它既不能自由排版（改一下对齐就可能解析不到），坏掉时又可能表现为一个恰好正确的份数。
本节只负责**来历与纪律**，不是任何脚本的输入。

要换基线必须有明确产品裁决，并同时更新 `web/src/mocks/` 的样本字节、
`web/scripts/mock-baseline.json` 与本节说明。

### 8.2 样本读法与不可推导项

这些是历史抓包，不是当前 schema：旧 job kind、旧 check kind、stage0_extras 和部分旧字段只证明
“曾经出现过”，不得重新接入 real mode。当前类型以 `contracts/schema/` 与真实 API 为准。

保留的关键反例：`jobs.json` 是合法空数组；stage2 secrets 可以为空；stage5 一章可以只有一条臂；
锚点覆盖率可低至 0.375 且未对齐块必须明示；已完成书的旧 check 可以是 fail；review diff_rows 可以
为空；stage7 允许 renamed=false 并要求理由。空值和红灯都不能被 mock 适配器“美化”。

`web/src/fixtures/backend_v1/` 是当前后端形状的版本化 fixture，与本节证据基线职责不同；前者可随
新版本新增目录，后者未经裁决不得改字节。

## 九、文档与测试的直接耦合

### 9.1 可执行 Markdown 语法

`contracts/requirements.py` 直接解析本文件：R 节必须以 `### R-N` 开头，R 条目必须是至少三列的
`| R-N.m | 要求 | 谁在查 |`，Q 定义必须以 `### Q-N` 开头，B 定义必须是
`| **B-NN** | ... |`。编号重复、表格缺列或完全解析不到条目会失败。

现役自检：R 家族大于 50、Q 恰好 12、B 恰好 23 且连续。每个 B 必须被某条 R 引用；每个声明
HARD / INFO / 闸门 / 度量的 R 必须被注册 Rule 覆盖；每条 Rule 必须反向指向现存 R。结构行的
可解析落点使用 `` `仓内路径.py::symbol` ``，当前扫描下限为 32 个，只应增加。

### 9.2 文档引用与扫描自检

`tests/test_docs_references.py` 扫描本文件、`docs/LESSONS.md`、`docs/REQUIREMENTS.md`、README 与
`docs/schema/*.md`。反引号中的 R/Q/B 编号必须存在；以 tests、contracts、tools、novel、docs 开头
的仓内路径必须存在。扫描总数还要超过 100 个编号和 10 个路径，防止正则失配后假绿。

### 9.3 判据注册与反例资产

- Rule id 全局唯一；severity、owner、check_kind、who_checks、repair bucket 都是闭合枚举。
- metric 与 METRIC 双向一致；unver 不进自动修复；词表规则必须有 baseline。
- repair 非 NONE 只属于 stage5 且必须有 directive；NONE 时 directive 必须为空。
- fix 指向同层真实节点与真实 flag，费用可由唯一入口推导。
- semantic 判据只在 stage6，且必须非空（`stage5.semantic` 于 2026-08-17 整节点下线）。
- stage4/6/7 每条 HARD 在 `tests/hard_cases.py` 有真实会红的反例；stage0/1 的 warning、编码、kind、
  新增 HARD 与阈值带由 `tests/test_ingest_fixtures.py` 双向覆盖。
- ⚠ **一条 HARD 有多个判定分支时，「红过一次」不等于每一支都红过。** Stage4 现役定向反例覆盖
  `fact_coverage` 的缺失/重复事实、`material_coverage` 的缺失/重复材料，以及 `state_closure` 的章尾
  ending 未闭合；它另外三支（`continue` 未承接、唯一支路 `return` 未恢复锚点、transition 语法）由
  `tests/test_stage4_beats.py` 直接走生产 `temporal_state_findings` 逐支覆盖；靶子自检与绿对照一并放在
  `tests/test_hard_counterexamples.py`，靶子一旦因契约变动而散掉，自检当场红。

### 9.4 架构与前端 seam

`tests/test_layering.py` 守 contracts/runtime/web 分层、store 独占 JSON 读写、API/view 不直写产物、
服务不能花钱、view/queue 不产生判定、API 不造第二个 argv builder、完成度不偷看作业记录。
`tests/test_api_discipline.py` 守 loopback、无 CORS、状态域、unknown、唯一 paid 来源、部分遮蔽说明与
同书 409。`web/scripts/check-seams.mjs` 守唯一 fetch 缝、mock/fixture 消费边界、颜色令牌，
以及 `web/scripts/mock-baseline.json` 登记的 20 份冻结字节（来历见 §8.1）。

### 9.5 最小验证命令

```powershell
& 'D:\Miniconda\envs\novel_refactor\python.exe' -B -m pytest -q `
  tests/test_docs_references.py tests/test_coverage.py tests/test_rules_registry.py `
  tests/test_hard_counterexamples.py tests/test_ingest_fixtures.py
npm --prefix web run seams
npm --prefix web run typecheck
npm --prefix web run build
```

修改业务判据、schema、节点或 API 时追加对应 stage/API 测试，交付前运行完整测试集。测试通过只说明
已登记守卫没有发现问题，仍需检查真实消费者和最终盘面。

## 十、历史验证证据账

本节只保留会影响现役判断的结论，不保留逐日施工日志。

| 日期 | 范围 | 可继续引用的结论 |
|---|---|---|
| 2026-08-30 | **Stage4B 章级恢复与 Stage4 A/B 异常优先工作包**（TODO-45 批次 4/5；工程/合成，真实 provider wire=0） | 批次 4 收实 `R-5.23`：机械唯一解 0 wire，真歧义章私有 session 真实最多 4 并发，worker 内完成 dynamic validator 后即时 checkpoint；后章失败不抹他章，乱序完成仍按章号确定聚合；B-owner 只重问目标章并同步刷新 checkpoint、accepted attempt/models/revision、unit lineage 与 aggregate anchor，无章号 B-owner 视为 code failure。章 prompt 只含本章原文/事实/hinge 与 ID 级相邻边界；validator 耗尽或章级业务 HARD 可留下绑定原 run/request/code/subject/attempt 的一次性 replacement，同 run 恢复 0 wire，仍走同一 `ChapterBindingResponse` validator、compiler 与整书 HARD；checkpoint 已写而 workitem 写失败的尾窗可幂等补建，replacement 只有正式 finalize 成功后才记 accepted。批次 5 收实 `R-5.24` / `R-5.25`：A/B 各有当前 hash-bound、固定六组的只读 workpack，GET/CLI/Inbox/UI 不 spawn、不预填决定；INFO/metric/UNVER/skipped 无动作，舞台签名冲突完整披露 fact→beat 与全部签名；结构审批私有记录在现役盘 v7 中保存逐章 subject 快照（公开 API v6 不暴露），单章变化时未变章可 exact reuse，构造前后 currentness 对账关闭 TOCTOU；current typed 决定覆盖的 disclosure 在 Inbox 保留为 `reviewed_retained`。验证：批4/5联合定向 **43 项**通过；冻结契约五件套、Stage4/API/审批/Inbox 扩大集及完整默认非 corpus pytest 均 exit 0（2 条既有语料依赖 skip）；前端 seams **115 文件 / 20 mocks**、typecheck、Vitest **30 文件 / 121 项**、production build 全过；operator skill 自校验 PASS。**没有真实模型请求、没有 current-schema corpus manifest；本行不证明模型质量/成本/速度，也不销 `TODO-30` 的两书付费实证与人工盲评。** |
| 2026-08-29 | **Stage5 稳定性与 Commander 工作台 P0/P1 收口**（工程/合成/默认非 corpus；真实 provider wire=0） | **P0 提交事务**：正式提交链冻结为 `prepare_commit → formal_write → typed_readback → adoption → mark_committed → committed_verify → session_end`；首轮异常不提前结束 session，全批候选完成后只对失败章做一次 same-run deterministic sweep，复用 checkpoint/候选/章所有权与 usage，只重放提交链，零 driver/probe/wire/cache/新 session。session audit wire v2 持久记录 attempts、same-run recovery 与首次失败阶段/摘要；preflight、typed readback、trim 核验读统一隔离并断言来源边不漂移。**P0 闭合闸**：唯一 hash-bound `Stage5ClosureWorkpack` 只接受 fresh Review 并绑定其完整文件、全部 canonical 章、trim/wontfix 与规则指纹；缺失/stale Review 进入 `review_required`，缺章、`.INVALID`、HARD 与未销账 trim 必须全部闭合后才可回执。must-appear 逐项 typed 裁决，fact/UNVER 按完整集合 digest 确认，未知 actionable 分类关闭式失败。盘上审批 v7、公开 API v6；v6→v7 只经显式 backfill，保留可对证的 s0～s4/seen/typed 历史并清除 s5～s7，不伪造回执；`gate.s5`、Stage6/7 写效果与 assemble 共用当前 closure 硬闸，0 成本恢复路保持开放。**P1 段落与人工收口**：逐 slot 段落计划以 largest-remainder 确定性分配并由 prompt/反馈/常驻读数同源；whitespace-only reflow 只进 shadow 观察、不改产物或结论。人工改稿先走与正式 apply 同判据的只读 preview，再以 current chapter SHA + preview subject SHA、actor/reason 与 `formal｜working_draft` 提交；一般新增 HARD 只可明示落 working draft，段落预算 HARD 仍不得正式化，动作身份追加进不含正文的 `_service/human_actions.jsonl`。多次改稿不逐次自动跑 Review，只在编辑与 trim 全部收口后跑一次最终全书 Review。闭合工作包投影批量 trim 所需的 audit/chapter 双 SHA；批请求整批先验、任一漂移零写入，中断后按章幂等续接；Commander Stage5 顶部闭合卡提供最终 Review、批量 trim、typed 回执入口。**证据边界**：全程真实 provider wire 为 0，仅做工程、合成与默认非 corpus 验证；没有完成 TODO-21 current-schema corpus 硬验收，也没有付费自然跑书的段落合规率、formal commit、same-run recovery、操作数、质量或速度取证，因此不能宣称质量/速度目标已经达成。未修改 `outputs/`。 |
| 2026-08-29 | **恢复身份两轴拆分与原授权自动续跑前提**（TODO-45 批次 3；工程/合成，真实 provider wire=0） | `run_id`（业务 run，跨恢复不变）与 `job_id/execution_id`（进程 attempt）拆开：`JobStart` 新增 `run_id`/`recovered_from_job_id`、`ProgressEvent` 新增 `execution_id`（旧盘可缺、新 writer 必填、不回填）。队列恢复续跑（`--yes` 确认路与原授权自动续跑路）都是新 job_id + recovered_from_job_id + 原 run_id 经 `NOVEL_RUN_ID` 传 CLI/ledger/session/checkpoint；旧 orphan 永久 `reconciled/unknown`。R-13.3 修订：durable queue 父记录 + 身份完全闭合（paid_confirmed、代码/profile/argv 零漂移、egress 仍有效、checkpoint 可对证）的同一 run 可跨重启自动续跑付费；legacy、漂移、从未启动或手工 CLI 一律仍需显式确认。验证：orchestrator jobs/queue/api_run/agent_runs 定向集 + 新增 4 条身份用例（自动续跑同 run 身份、代码漂移拒绝、legacy 拒绝、显式确认同身份）+ 完整非 corpus 回归 exit 0；**未发生任何真实模型请求** |
| 2026-08-29 | **Stage4 A→formal 零调用预检与审阅元数据归一**（TODO-45 批次 2；工程/合成，真实 provider wire=0） | A 审阅模板、approve 闸、`authorize_plan` 与 `prepare_plan_candidate` 四处共用唯一 `preflight_binding_candidate`（`R-5.22`）：构造性绑定 + 同一联合 HARD 的 typed pass/blocked 结论零落盘；blocked（A/Stage2/code owner 或不可行 witness）时 accept 关闭式拒绝、B 在任何 wire 前拒绝。`structure_alignment` 固定 metric_only（无 fix 路由）；`structure_delta` 退役（历史可解码、新决定拒绝、模板 sentinel 改带 scene/alias 的 `scene_goal`）；`binding_constraint_unsat` 必须有机械 witness；`DESIGN_REVIEW_CONTRACT` 升 v2（v1 决定自然过期，重审 0 成本）。验证：`tests/test_stage4_preflight.py` 6 条 + 审批/预检/agent 全定向集 + 完整非 corpus 回归 exit 0；前端 seams/typecheck/vitest/build 全过；**未发生任何真实模型请求** |
| 2026-08-29 | **Stage4 typed failure witness 与 B 的关闭式路由**（TODO-45 批次 1；工程/合成，真实 provider wire=0） | Stage4 运行时失败改为九字段闭合 witness（`R-5.21`）：owner 路由表唯一落在 `contracts/invariants.py::STAGE4_FAILURE_OWNERS`，未登记发射点关闭式归 `code`。B 的联合 HARD 拒稿不再一律烧满四轮：A/Stage2/code owner 的失败首轮即停；同一 fingerprint 集（语义身份，不含候选 SHA）连续两轮未闭合提前终止该 unit；只有 B owner 的失败回同一 session 续改。章级判据按章拆 finding 并必填 `output_ordinal`，typed witness 只从发射点结构化产出，聚合 HARD 不再解析中文 detail 猜章节。三份出口：完整反馈只回同一 provider；`llm_audit.jsonl` 只追加无正文 code/chapter/fingerprint 读数；完整 witness 与被拒响应进现有 `_llm_rejected` 恢复面且 `repair_eligible=false`。验证：`tests/test_stage4_typed_failures.py` 9 条正反例（干净计划零发射、A owner 首轮停、同指纹两轮早停、审计无正文）+ Stage4 全定向集 + 完整非 corpus 回归 exit 0；**本行只有合成证据，未发生任何真实模型请求** |
| 2026-08-29 | **Stage4A 正式审阅、定向返修与 Stage4B 不可行证明闭环**（工程/合成/默认非 corpus；真实 provider wire=0） | Stage4 顺序冻结为 A 机械闸 → 当前全书 typed design review → B 证据绑定/编译 → 独立最终逐章结构审批 → 0 成本 Context → writing proof → Stage5；A 票只授权 B，绝不打开 s4。A 审阅同时绑定策略、逐章 subject、fresh B 不可行证据及由 durable split lineage 现场计算的 repair capability；服务端模板的每一未审行都带不可原样提交的 `placeholder + template_token`，历史同 subject accept 只生成可追溯复用行。章级 `request_repair` 只把关闭式 code/scene/opaque-alias scope 交给模型，复用其余 checkpoint；策略级 `request_replan` 才整书重做；已有 current A 的裸 design 覆盖、legacy/untracked 候选返修、不可执行章、错误 decision SHA 及单章跨 run 第三轮均在任何 wire 前拒绝。B 的模型 `infeasible` 只是待证声称：代码必须在同一关闭目录中无法构造 bound witness 才铸 typed proof；语义担忧只作 nonblocking concern。新增 0-wire 人工 binding bootstrap，与模型 B 共用 schema、编译/HARD、provenance/readback；B 编译和人工计划恢复都不再提前生成 Context。审批盘 v6、公开 ApprovalDoc v5；显式保守回填不从已有 A/B/付费记录伪造 A 人审，只有 current Plan v4 + current bound EvidenceBinding v3 可直接继续最终结构审。验证：Stage4/审批/API/CLI/queue 定向集、冻结契约五文件与完整默认非 corpus pytest 均 exit 0（进度流 2 条既有 skip）；前端 typecheck、112 文件 seams / 20 份 mock、29 文件 / 118 项 Vitest 与 production build 全过。**没有 current-schema corpus manifest，也尚未在本行记录任何真实 Stage4/Stage5 模型请求、质量、token 或墙钟实证；自然跑书结果必须另按实际盘面陈述。** |
| 2026-08-29 | **Stage2 冲突分段补齐 0-LLM typed 整组替换入口**（工程/合成/默认非 corpus；真实 provider wire=0） | `GlobalEdit` 新增可缺、非空的 `conflict_stages_replace`，因为冲突阶段没有稳定行 ID，只允许整组重画，不开放数组下标 patch。该字段是兼容加法，`global_edit.json` 保持 v3，旧 v3 无新字段仍可逐字解码，v1/v2 仍须人工重写。producer HARD 与 replacement 预检共用 `checks.measure_conflict_stage_coverage`：生成或既有完整候选缺/重/越界仍由 `conflict_stage_coverage` 落 `.INVALID`；显式坏 replacement 只在该字段出现时于赋值前 `TerminalState`，正式稿、既有 `.INVALID`、review 与 `_rounds/` 均不动，其他 typed edit 不会因既有坏覆盖被预检冒充成“坏决策集”。报告默认 remediation 改为 human、`fix=null`，Inbox 明示 `conflict_stages_replace + stage2.global --apply-edit stage2/global_edit.json`，付费 `--resample` 仅作最后手段。Stage2/Inbox 定向回归、冻结契约五文件、artifact 版本、API fixture/报告回归均 exit 0；前端 seams（112 文件/20 份冻结 mock）、typecheck 与 29 文件/118 项 Vitest 全过。**没有运行真实 LLM、没有 current-schema corpus manifest，也未修改 `outputs/`；本行只证明恢复入口、原子性和消费链闭合，不证明模型质量、速度或成本改善。** |
| 2026-08-28 | **Stage0～Stage2 审批与恢复路径降等待/降重复付费；Stage7 role freshness 补齐**（工程与只读真书验证；真实模型 wire=0） | Stage0 新增只读、固定口径的审阅工作包，只呈现剔除决定面并以 `expected_revision_id` 锁定实际读过的 revision；用户点名书只读实跑约 **2.5s**，13 类反扫共 2 命中、removed=0、warning=0，脚本不审批、不写盘。Stage2 将 `不可能` 从 `可能` 子串假阳中排除，fate 模棱语与 `beats_coarse` 降为只披露 INFO；唯一证据归错拍由编译/归一代码自动 rehome，歧义仍关闭。Global 人审默认收成绑定完整正文、blocking 集合与实际已读 workpack SHA 的单一 scope，disclosure 不阻断但不消失；schema-valid 且称名可靠的 `.INVALID` 可先物化称名投影并与人审重叠，Stage4 才汇合正式 Global、联合 HARD 与 blocking PASS。称名 HARD 在任何 Blueprint wire 前停止；live Blueprint `.INVALID` 只走 0-LLM revalidate，队列不保留默认 paid argv；非称名 HARD 仍可并行落 Blueprint。Stage7 rename 的 role/era 真实消费与 freshness 对齐，identity/recheck 不再用新 provenance 洗白旧审批。完整默认非 corpus 后端回归 exit 0（进度流只有 2 条既有 skip）；冻结契约/分层/枚举定向集与 operator skill validator 均 exit 0；前端 typecheck、109 项 Vitest、109 文件 seams / 20 份冻结 mock 与 production build 全过。**没有 current-schema corpus manifest、没有真实 LLM/付费请求，也没有整链 token 或墙钟降幅实证；只确认了恢复命令不增 wire、问题稿可落盘与重复付费入口关闭。** 未修改 `outputs/`。 |
| 2026-08-28 | **Stage4A 拆为小型整书策略 + N 个单章私有 session，并以 N+1 checkpoint 聚合提交**（fake driver / 合成 / 默认非 corpus；真实 provider wire=0） | 对外仍只有一个 `stage4.design` 付费节点、一次 `--yes` 授权且不开放公开 `--chapters`；内部先让策略 session 只看章功能与确定性计数，通过后立即 checkpoint，再按 `min(4, NOVEL_CONCURRENCY, 待跑章数)` 为每章建立互不共享 driver、logical session、provider session 与 cwd 的私有请求。单章只见 HMAC 派生的短 local alias；自由文本若回显本书已登记的 fact、char、beat 或 hinge 正式 ID 会关闭式拒绝。每章通过 schema、动态闸与局部 HARD 后立即 checkpoint；恢复先串行复验全部已有 unit，损坏时零新 wire，合法时只补缺失或失败 unit。代码把 local alias 确定性还原、按章聚合为现有 `BlindCandidate` v2，随后完整重跑原有整书 schema/HARD；只有 N+1 unit 全闭合才进入 aggregate `prepared → ArtifactStore 原子 replace → typed readback/freshness → N+1 幂等 adoption → committed`。prepared 正式写盘尾窗可同 run 零 wire 收尾；正式 provenance 从 ledger 按精确 run/sid/node/N+1 purpose 重建全部 calls，包含先前失败且无 checkpoint 的 session，不依赖当前进程 usage mark。每个 acceptance 只联结自己的 logical session 加显式恢复链，旧同 purpose 失败 session 不会污染 checkpoint。旧 whole-book `--adopt-rejected` 只接受 `BlindPlanResponse` 历史拒稿，并受同书提交所有权、foreign prepared 与 adoption/outcome 尾窗保护；它不冒充新 unit 修复入口。冻结契约五文件 314 项、Stage4/agent/ledger/恢复定向集与最终完整默认非 corpus 后端回归均 exit 0（进度流只有 2 条既有 skip）；前端 seams（109 文件、20 份 mock 基线）、typecheck 与 production build 均 exit 0。**没有真实 Claude/DeepSeek/Codex 请求，没有 current-schema corpus/manifest，也没有首次交付率、token 或墙钟降幅实证；这些只能在后续一次显式正文外发与付费授权的自然跑书中验证。** |
| 2026-08-28 | **Stage2 global evidence 拒绝降本与 Claude 结构化出口闭合**（离线 fake / 合成 / 非 corpus；真实 provider wire=0） | 问题实证来自本批之前的主分支跑书：同一 agent session 首轮耗时 **25:37**，仅因一条 `global_evidence_unlocated` 又修复 **9:48**；修复轮 completion **44,255**，而 1,062 个标量叶只改了一个 evidence 章号。该读数只证明旧协议会重复输出大对象，不是本批提速实证。本批不拆成两次固定大调用：Claude schema 请求只经一次版本化 `StructuredOutput` 出口，stdin 实际 UTF-8 bytes 与 prompt/session/checkpoint hash 同源；Stage2 agent 首轮交完整候选，纯 evidence HARD 才在同 session 交 candidate-SHA 绑定、canonical claim-path 闭集的窄 patch，代码合并后仍重跑完整 response schema、normalize、全部 HARD、provenance、adoption/readback；合法声明章零命中时，统一 normalizer 仅在全书恰一处且没有章内歧义时 0 调用纠正，非法章、多命中、零命中、纯标点与 formal drift 继续关闭。Commander 不改候选，人工 locator 仍走 GlobalEdit v3；`.INVALID --revalidate` 只从不可变 creation baseline 恢复旧 locator，live provenance 不作人工输入。定向 Stage2/agent/session/driver/contract/artifact-version 合并回归与最终主路径聚焦回归均 exit 0；实现过程中完整默认非 corpus 曾 exit 0，但最后一次来源账收窄后按用户要求未再次跑长回归，当前无 corpus manifest，不能称语料全量绿。**未运行真实 Claude、未验证真实单次 StructuredOutput、token 或 wall-time 降幅；这些仍待正文外发与付费授权。** 未修改 `outputs/`，未合并 main。 |
| 2026-08-28 | **Stage4 假拒绝的逐条实证与收窄**（0 成本；`_llm_rejected/` 真拒稿重放，无新的真实 LLM 调用） | 拿 v8 那次 opus5+xhigh 整书跑留下的 **5 份 Stage4 拒稿**逐份重放当前判据，结论是**那次跑的内容拒绝几乎全部买在「代码本来就精确知道的账」上**：① `declared_change_kinds` 排序（2 份 design schema 拒稿的 `value_error`）；② `bound` 下的空 `evidence_refs` 数组（1 份 plan schema 拒稿）；③ 同一时间线的状态承接——那份 8 章 38 场的候选里 **25 个 `X_CONTINUE` 场有 18 个没把前面仍成立的状态抄全**，外加 1 处章首未承接，而 return 锚点 0、transition 语法 0、因果漂移 0、写法词 0；④ context 引用面——两份候选共 54 条 context 引用，**43 条是已在更早场落地的事实**、10 条合法 context、1 条从未落地，**提前引用未来事实 0 条**；⑤ 章尾闭合——7 条命中 **7/7 都已在第 2~3 场落地**，判据比的却是「末场有没有再抄一遍」。逐条收窄后重放：**5 份里 4 份 0 调用通过**；剩下 1 份仍红，红在 ch5 应落 15 条 land 只落了 14 条——一条事实被真的漏掉，那正是判据该拦的。⇒ 可继续引用的结论有三条：**(a)** 「让模型逐场复述已经成立的状态/已经披露的事实」是本仓最贵的一类假拒绝，它的代价单位是**一次整书重出**；**(b)** 判据要问「读者此刻知不知道」，不要问「模型写没写第二遍」；**(c)** 收窄之后真问题（漏落事实）照旧红，判据一条没撤——`--apply-edit` 的人工稿与 `--revalidate` 的存量产物不经过编译器，那两条路上它们仍是真守卫。⚠ **本行没有新的付费实证**：改动能否真的降低下一次整跑的重出次数，要等一次真实整书才能回答。⚠ 那次跑的 3 次 `agent_timeout` 属于 worktree 私有的 Claude CLI transport（P0 明确不移植），**main 的 agent driver 默认 1800s**，而观测到的整书 Stage4A 成功调用是 349~612s ⇒ main 这一侧当时不存在同一个超时问题，不要照着那批读数去调 main 的超时。 |
| 2026-08-27 | **Provider-neutral Commander、typed 代批与前端双角色配置 Phase 6**（合成/本机零成本验证；真实模型请求 **0**） | 整书外层不再写死 `claude -p`：Claude Code 与 Codex Commander driver 各自构造 exact argv、把长 prompt 仅经 stdin 传入，并拥有进程树取消与供应商观察协议。preview/start/batch 共用唯一 plan，完整 resolved Commander/default executor/逐节点 overrides、两项授权、规范化 base、起始代码指纹与 invocation 冻结进 started/queue；`default_profiles=null` 不猜默认，start/batch 缺付费或外发任一授权、代码指纹 unknown、profile/actual identity/审计链不闭合时均关闭式拒绝或降 unknown。child 只凭 run id + contract SHA 重读 append-only started，operator runner 按 exact node/flags 把冻结 executor profile/egress 送进 8 节点 agent，主路不再携 legacy response-repair。Commander 本身另写 `role=commander,node=null` session/ledger，费用工具按角色分栏；外层 process state 与独立盘面 delivery outcome 正交。新增 `approval show/template/apply` 统一 typed Stage4 结构与 Stage5 写作代批，保留旧 UI 空 body writing 兼容且所有审批/回收 RMW 共用进程锁。页面从 capability 动态呈现 Commander、默认 executor 与逐节点 override 的精确 model/effort/mode，null 不代选；单本/批量 start 只复用最后一次成功 preview 的完整请求快照，配置、能力或双授权漂移即关闭。验证：冻结契约最小集、Phase6 定向集、8 节点恢复/API/CLI 纵切及完整默认非 corpus 后端回归均 exit 0（进度流仅 2 条既有 corpus skip）；前端 typecheck、28 文件 / 106 项 Vitest、109 文件 seams / 20 份 mock 基线与 production build 全过。**没有真实 Claude/Codex 模型请求、账号 entitlement、质量/速度或 current-schema 整书验收结论**。Phase 7 真实整书验收与默认切换仍未执行。 |
| 2026-08-27 | **Stage4 design/plan、Stage5 write、Stage6 hook、Stage7 rename direct executor Phase 5**（仅 fake driver / 非 corpus，真实模型请求 **0**） | direct profile 的现役边界由 3 节点扩为完整 **8 节点 / 12 种 flag-token 组合**：Stage2 global/blueprint 各 1 种；Stage4 design 2 种、plan 1 种；Stage5 write 4 种；Stage6 hook/semantic 各 1 种；Stage7 rename 1 种。preview/start、Step/JobStart、argv、CLI 与 RunContext 继续冻结同一 resolved profile / egress，未登记组合关闭式拒绝，显式 profile 在 legacy client 构造前派生节点 agent，绝不回退 DeepSeek。Phase5 五个 producer 都由 agent 提交固定 typed 完整候选，代码负责 schema、HARD、结构化反馈、正式归一与终验；append-only checkpoint 闭合 accepted attempt/session/ledger，有正式 primary 的交付再经同书 `prepared → write/retire → typed readback → current/fresh/HARD → adoption → committed` journal 收口（包括保持 landed-red 的 typed infeasible EvidenceBinding），恢复不伪造 live provider resume；没有正式 primary 的终态不伪造 adoption。验证范围只有合成输入、fake driver 和默认排除 corpus 的非 corpus 回归，**Claude / Codex / 其他真实 provider 模型请求数为 0；没有质量、速度、账号 entitlement 或 current-schema 整书验收结论**。UI 仍未请求 capability，也没有 commander/executor/model/effort/egress 选择器；该前端工作明确留给 Phase 6。 |
| 2026-08-27 | **Stage6 semantic 直接 job executor 与独立正文外发授权 Phase 4**（fake driver / 真实 provider wire=0 / 非 corpus 回归） | 现役 direct profile 边界扩到 `stage6.semantic + flags=[]`；`PreviewRequest.egress_authorized` 默认 false，与 `--yes` 付费确认正交，resolved profile / egress 快照经 preview/start、Step/JobStart、argv、CLI、RunContext 逐项一致，false 只可预览，真启动和手敲 CLI 都在 job/driver/probe 前拒绝，只有 true 才加入 `--allow-egress`。显式 profile 在通用 dispatch 先于旧 `_llm_client`，因此不回退 DeepSeek。A/B 使用两个独立 driver 对象、logical/provider session 与临时 cwd，缺失角色并发；checkpoint 逐角色不可变且先全量验证，完整恢复不造 driver/probe/start/wire，单边恢复只重跑缺失角色。代码独占 schema/dynamic/双盲身份、合并、序号与终验，并以 `prepared journal → 正式写盘 → typed readback → 双 adoption → committed` 关闭提交尾窗。semantic v2 的 `judged` 只绑定实际下发的开篇正文，但全书 Stage5 accepted Review 仍是既有前置门；后半书改字先使 Review 暂不可用，零成本重跑 Review 后若实际 prompt 输入未变，旧审判可恢复 current。验证只用了 fake driver、合成账与零真实 provider wire，**没有真实 Claude / Codex 请求、模型质量/速度/entitlement 实证或 current-schema corpus 验收**；UI 仍未消费 capability，也没有 commander/executor/egress 控件。 |
| 2026-08-27 | **Stage2 blueprint 直接 job executor 纵切 Phase 3**（fake driver / 非 corpus 回归，无真实 LLM 实证） | 在 Phase 2 仅 `stage2.global + flags=[]` 的历史边界上，现役直接 job 又开放 `stage2.blueprint + flags=[]`：preview/start 仍共用 `plan_start`，resolved profile 冻结到 JobStart 与付费 producer Step/argv，自动 `--check-only` Step 明确为 `executor_profile=null`；CLI 将同一 profile 写入 RunContext，并在任何 DeepSeek client 构造前选择 `Stage2BlueprintAgent`。蓝图执行按 phase/purpose/chapter 一对一创建 driver、logical session 与 provider session，逐章响应先过当前 schema、dynamic validator 与 scope；整书 compile/联合 HARD 的安全元数据反馈只路由给相应章节 session，正文、未来章证据和裁决权不交给 agent。`node-executor/v1` run policy 与 checkpoint 把 resolved profile、requested ceiling、accepted stdout/normalized text/current typed payload、session turn、prompt/schema/subject SHA、attempt links 和 ledger response SHA 逐项闭合；跨 CLI 恢复先复验 agent session audit 与成本账，再认领 prior attempts，完整恢复可做到零新 driver/probe/start/wire，联合 HARD 要求修订时另开新 session，不能冒充旧 provider resume。最终 `.INVALID`/正式 artifact、commit journal、readback 与自动终验仍只由代码决定。两个 Stage2 节点带任何 flag、其余 executor 节点和 outer commander `profiles` 仍关闭；前端仍无 capability hook 或选择器。验证范围只有 fake driver 与默认排除 corpus 的非 corpus 回归，**没有真实 Claude / Codex 调用、current-schema corpus 验收或质量/速度/entitlement 结论**。 |
| 2026-08-27 | **Stage2 global 直接 job executor 纵切 Phase 2**（0 成本工程改动，无真实 LLM / corpus 实证） | 仅 `stage2.global + flags=[]` 的 `PreviewRequest.executor_profile` 从 `/api/jobs/preview` 与 `/api/jobs` 进入同一个 `plan_start`，经 capability registry 关闭式解析后，同时快照到 Step、JobStart 与 argv 的 driver/model/effort/mode 参数；CLI 将同一 profile 写入 RunContext，节点派生的 `Stage2GlobalAgent` 复用同一 logical session，按“候选 → 代码校验反馈 → resume → 正式提交 → 终验”循环。显式 profile 的分支位于任何 DeepSeek client 构造之前，不会静默降回旧 LLM。`stage2.global` 的任何 flag（含 `--purge-role`）、其他七个尚未迁移 executor 节点、以及 outer commander 的非空 `PipelineAgentProfiles` 仍在 wire/dispatch 前拒绝；profile 为空的旧/未指定直接 job 保持既有路径。前端只有 nullable TS 槽，尚未请求 capability 或提供 commander/executor 选择器，不能把本行解释成 UI 已交付。验证只使用 fake spawn、合成 driver 与零 wire 回归，**没有真实 Claude / Codex 模型请求，不能据此宣称质量、速度、账号认证或模型 entitlement 已验证**。 |
| 2026-08-27 | **Agent 双角色配置契约与只读 capability 面 Phase 1**（0 成本工程改动，无真实 LLM / corpus 实证） | 新增 `AgentProfile` 与 commander / executor 双角色组合，Job/Step、agent run 请求/预览/启动只先冻结 nullable 配置槽；旧记录缺失或为 `null` 恒是 unknown，不用当前默认倒推。`GET /api/agent/capabilities` 从单一 registry 动态发布 driver、provider 元数据、角色、精确 model id/展示 label、effort、实际 execution mode 与八个现役 executor 节点，并仅做本机 CLI/版本探针；`ready` 不代表认证、模型授权或真实请求成功，探针没有付费调用。当前 `default_profiles=null`，表示尚无 provider-neutral 默认。`_service/agent_sessions.jsonl` 冻结 wire v1、attempt 身份、requested/actual、启动/结束代码版本、token usage、正交 process/delivery 状态与仅 hash 不存正文的边界。**本行只记录 Phase 1 当时边界，不宣称 runtime 已完成替换**：当时非空 profile 在接线前关闭式拒绝，Stage2/4/5/6/7 的现役 DeepSeek 生产链未因这些 schema 槽自动切成 executor agent；其后接通的直接纵切以本节 Phase 2～5 各行分别为准。现有前端仍尚未消费 capability、没有 commander/executor 选择器；后续 UI 属于 Phase 6。 |
| 2026-08-26 | **Stage2 可恢复批次与复核并行闭环**（用户裁决；0 成本工程改动，无真实 LLM / corpus 实证） | **顺序裁决**：global 经 schema / 归一 / producer HARD 正式落盘后即可启动 blueprint；global 人审的阅读与填写可与逐章计算重叠，同书写回仍互斥。Stage4A 前必须汇合 current global + current blueprint、`stage2.blueprint --check-only` 联合 HARD 与 current 全 PASS review；Stage2 不新增 global check-only，Stage4A 不对未收口结果投机付费。CLI、API、queue、Stage7 组装/下载共用同一 Stage2 review gate；queue 把无 argv 的 `approval:stage2-global-review` 放在最早 Stage4 项。**恢复闭环**：canonical `run_id` 贯穿首轮、唯一 batch repair 与唯一 resume，并联结 ledger/audit/progress/repair/checkpoint/adoption；通过 parse/require/schema/validator/scope 的成功章原子写验证后 sidecar checkpoint，恢复时重跑当前单章与整书联合闸，损坏、跨 run、输入/SHA/ledger 漂移关闭式停止。主动 refresh 首次进入绕过所有旧 run；`--chapters` 只有在本轮唯一 batch resolution 完整封存后，才可在同 run 的唯一 resume 恢复本轮成功章。并发 pending 稳定打包成一份 immutable manifest，完整 decisions 一次校验并原子封存一份 resolution，batch attempt 不再接受单项 submit，后继失败不得形成第二轮人工暂停；正式 artifact 已写但哨兵未写按指纹幂等收尾。**失败路由**：完整通过的 reasoning JSON 立即采纳；BOM/尾逗号本地修；parse/require/schema、validator、无材料 answer_lost、length、传输故障与不可重试错误各走登记动作，同一 run 的相同失败签名不得重复同一动作，effort/thinking/schema mode 不降级。**freshness**：prompt、编译与消费指纹共用人物称名 inventory；只有实际影响歧义逐章候选的 `address.from_chapter` 变化令 blueprint stale，canonical shadow、唯一称呼与未消费字段不制造付费重跑。默认 v3.1 与 warm-up 保持开启，`micro_events`、`beats`、fact atoms、Stage2 正式 schema 与联合 HARD 不变；不做独立付费 A/B，效果只在下一次自然跑书按 run_id 回读，不能冒充 corpus 验收。 |
| 2026-08-26 | **Stage5 安全减法：保留付费正文与人工 trim 决定，退役重复投影**（用户裁决；0 成本工程改动，无真实 LLM/语料实证） | **正文与 trim**：canonical Chapter 的生成、重采、人工写回、`--revalidate` 与 `--recheck` 收口到同一提交事务；trim audit/resolution 只随 canonical 正文实际变化而失效，同正文且 hash 仍闭合的人工决定保留，回到篇幅区间内时退休旧 trim，失败候选只落 `.INVALID`、不扰动旧正式稿及其决定；`--recheck` 只刷新 v2 派生读数，`--revalidate` 按原 segments 重判并继承写作来源账。**Review 绑定**：`stage5.write` 的 Stage2 消费投影保持 secrets-only，避免现有付费章稿整体 stale；`stage5.review` 另绑定人物 `char_id/canonical/address[].surface/name_distinctiveness`，人物基线变化只要求免费重跑 Review，纯 evidence 定位变化不扩散。**退役展示副本**：`stage5/draft.md` 从 artifact、NODES、CLI `--rebuild-draft`、拼稿/freshness、完成度、Review 对账、corpus manifest、枚举与 operator 文档整套撤下；旧文件可留盘但不再发现或消费。s5 审批 subject 改为全部 canonical chapters + 当前 accepted Review，旧 subject 仍多绑 `draft.md` 时 s5 及 s6/s7 前置证明关闭式失效。**HTTP / 前端**：`DraftView` 只以 `segments` 承载正文，删除路径、formal/invalid 重复状态、轮次、origin、来源字数与 `text`；Review 章卡只留 `output_ordinal/findings`，索引只留章号、finding counts 与 unresolved，顶层删除 Web 不消费的 sources/baselines/wontfix/provenance，页面与编辑统一读取并行 Stage5 响应中的当前完整 slots。**代码与延期边界**：合并重复 segment 叶类型，`Material.given` 改为 blocks 的唯一 property，删除 Stage5 semantic 空占位与重复 legacy 发现；来源章号、源字数、章预算统一现场派生并与 WriterContext v4 存量字段断言一致。为保留现有 41 章与 4 份人工 trim 决定，本批盘上版本保持 WriterContext v4、Chapter/Review/Trim v2，不改 `outputs/`；下一次新 base 的 WriterContext v5、Chapter/Review/Trim v3 与 Rejected v2 单批切换登记在 `docs/REQUIREMENTS.md` `TODO-31`。定向审批回归证明旧 draft subject 不会误续签下游；当前 manifest 仍由 `TODO-21` 管理，未运行 `--require-corpus`，不得称 corpus 全绿。 |
| 2026-08-26 | **Stage4 schema 与工作流减法**（用户裁决；0 成本工程改动，无真实 LLM/语料实证） | 持久化边界收窄为会影响编译、写作、机械校验、人工审批或来源追踪的字段：BlindCandidate 删除 goal rationale；EvidenceBinding 删除人物重复绑定；正式 Plan 删除 goal rationale、两级 weight、策略、语义契约、FactRef 章号、params 与持久化结构向量；结构变化改为按实际场景实时计算，审批 subject 绑定实际计划、goal、预算、场景和人工声明。WriterContext 删除全书 world_facts、逐场源字数/人物、秘密与规则内部 id、must-not-reveal，人物改为带 char_id 的数组；Stage5 从当前 GlobalGrounding + MainText 共用名称投影，并只按 Stage2 时间线判秘密边界。identity 产物退役，`--identity` 只做内存诊断且节点写效果为空；A/B 两相、typed infeasible、A/B 原始字节 SHA-256、人工结构门、`from_source_ordinals` 与事实安全闸均保留。破坏性版本：blind/evidence v2、plan/edit v4、writer context v4；旧版关闭式拒读，计划按 `stage4.design` → `stage4.plan` 重建，上下文由免费 `stage4.context` 重建，旧 identity 不发现、不读取也不自动删除。Stage4 判据表收为 **32 条（20 HARD / 2 INFO / 10 metric）**，Stage5 收为 **50 条（7 HARD / 26 INFO / 12 metric / 5 unver）**。验证：冻结契约最小集 317 passed；Stage4→5 相关链约 600 项通过；完整默认非 corpus 后端 **2733 passed / 3 skipped / 0 failed**（2736 项，仓外短 basetemp；两个既存 ACL 损坏临时目录仅经临时 Git excludes 隔离，未跳过测试、未删除目录）；前端 28 文件 / 91 tests、seams、typecheck、build 全过。当前 corpus manifest 未生成，故不称真实语料全绿。 |
| 2026-08-26 | **Stage2 减法：只保留下游真正消费的事实与闸门**（用户裁决；0 成本工程改动，无真实 LLM/语料实证） | 消费审计后删除 Blueprint `summary`、Beat `present_names`、Narration 显示名、Hinge 重复父键、SourceSpan `normalized/recovery`、secret 分组/确认事件、arc 标签和无下游消费的批次/解析/布尔来源账；随后按用户追加裁决继续删除只进入报告的成功归一清单、地点探针、边界外扩明细与修复历史。secret 只留 `first_reveal_chapter`，弧线只留 subject/trajectory/evidence，人物与叙述只留闭集 ID，来源账只保留会阻断或直接驱动恢复的正面失败态；`refresh-write` 改由首个 LLM 调用前的结构闸独占。同批修复 Stage4 grounding freshness 漏算 climax/ending/arc_tracks。破坏性版本：GlobalGrounding v4、GlobalEdit v3、ChapterBlueprints v6、writer context/identity v3；旧生成产物由各自 producer 重建，旧人工 edit 重写，不迁移不手补。Stage2 判据表 77→67（40 HARD / 14 INFO / 11 metric / 2 unver）。删除显示名后 `--renormalize` 不猜测悬空 ID，继续显式失败。影响面跨 Stage2–6 回归已执行；当前 corpus manifest 未生成，不称真实语料全绿。 |
| 2026-08-25 | **Stage1 减法：两份承重材料 + 一个当前审计面**（用户裁决；0 成本工程改动，无真实 LLM/语料实证） | 下游消费审计表明 `partition_report.json` 零下游输入，其章号、坐标、计数、指纹与 warning 全部可从当前 Stage0 + clean + 两份 Stage1 材料重算，因此整份下线；`main_text.json` 只留 `(chapter_ordinal, text)`，删除下游不读也不应信任的 `source_chapter_no`，v2→v3。原文章号只在当前 clean 复扫的 Stage1 对照视图显示；Stage2 消费投影与 prompt 同步收窄到阅读序 + 正文。`novel/stages/stage1/audit.py::audit_surface` 成为 check、视图、freshness 与 s1 审批的唯一当前事实口；main/hook 任一单独篡改都会 stale，s1 proof 同时绑两份。HTTP 从 3 个 Stage1 GET 合为 `GET /stage1`，删除重复的 Stage0 分章预演；Stage6 响应自带 `source_hook_text`，前端少一次请求。旧产物不迁移、不手补；当前两本书按用户裁决，待 Stage0–7 减法结束后一并从 producer 重建。验证：完整非 corpus 后端回归 exit 0（仅 3 条仓内既有语料依赖 skip）；前端 86 tests、typecheck、104 文件 seams 与 build 均通过。当前 corpus manifest 仍未生成，故不称真实语料全绿。 |
| 2026-08-25 | **活动待办生命周期清理**（只改文档，无代码、语料或付费调用） | `docs/REQUIREMENTS.md` 只保留仍有现役缺口和可执行验收的 TODO-21 / 28 / 30。销账依据：TODO-29 只追踪供应商输入前缀缓存的低杠杆读数，既无法隔离首发闸贡献，也没有读数对应的产品动作；TODO-32 指向已删除且非 current-schema 的旧 Flash 实验臂，本节 2026-08-23 已裁定不补终审包、不批量盖章；TODO-38 的结构化 `reject_detail` 与防正文泄漏守卫已经落地，按字段聚合是拿到真实账本后的分析动作，不是缺失能力；TODO-40 已由 `JSON_OBJECT_MODE=True`、同配置重采、合约内 reasoning salvage、Stage4 固定 Pro 与 Flash→Pro 同配置兜底闭合，继续扩大旧 json-mode 样本不会改变现役处置。FC-08 的旧密度对照已被 Stage2 v5 / Stage4 v2 / canonical Stage5 整体换约，无法再作单变量归因；FC-13 的现役异常降档已退役，Stage3 effort 面消失、Stage2/5 low 被否、Stage7 保持 high，已无生产候选。历史实验结论留在本节与 Git，不再占活动清单。 |
| 2026-08-25 | **Stage5 段落目标从 prompt 约束闭合为正式落盘与交付契约**（0 成本工程改动，无付费/语料实证） | 根因不是单一 reasoning 档位，而是模型可不遵守明确段落目标、`para_len_drift` / `band_drift` 又只作 INFO，导致问题稿仍可正式交付。现把段落公式收口到 `measure.target_paragraphs`，prompt 与判据同算；新增常驻读数 `paragraph_count_report` 与 HARD `paragraph_budget_missed`。完整付费响应仍保留并落 `.INVALID`，不在 transport 层触发高成本重采；`--apply-edit` 不得绕过该 HARD。`review_status` 继续只回答 freshness，新增 `require_accepted_review` 统一要求无活动 `.INVALID`、无缺章、无当前 HARD，并接到 s5 审批、Stage6、Stage7 / 最终交付。Stage5 注册表现为 **51（7 HARD / 27 INFO / 12 metric / 5 unver；repair 12 UNIQUE / 12 FREE / 27 NONE）**。Finding / Review 载体形状未变，故 artifact schema 不升版；RULES 指纹会让旧 Review stale，存量章须零成本重跑 Review 后按新 HARD 修复，已存在的交付 txt 不自动删除或改写。本批没有真实 LLM 调用，也没有 current-schema corpus 验收，不能据此宣称三份问题稿已被自动修好或模型遵约率提高。 |
| 2026-08-15 | 模型×思考 A/B | pro+thinking 保留；flash 未达到质量门；no-thinking 在 stage2.global 稳定失败。模型选择先过质量门，再比较速度与价格；生成与评审能力分开评价 |
| 2026-08-15 | 盘面 36 条红逐条定性 | **生产代码 bug 0 条**：11 个病灶在守卫侧（测试台重拼生产实参 / 断言了产品明说不存在的闸 / fixture 比载体老），3 个在语料侧（缺 `--identity` 那一趟 · stage6 语义产物是旧版剧透审判 · `hook.json` 对自身上游 `consumed_changed`）。守卫侧全部修掉并配反漂移守卫；语料侧改成**显式声明依赖哪一版产物**（`tests/corpus.py::declared_dependency` 要求写出缺哪一份、补它跑哪个节点、要不要花钱）。收官盘面：0 失败 / 4 条声明式 skip。教训见 `docs/LESSONS.md` §21 |
| 2026-08-15 | 语义结论的持久身份 | 裁为 `(层, 章\|book, 判据, 证据键)`，**层由文件路径承载**（`<stage>/wontfix.json`，逐层一份账），审判产物版本只进审计账 `at`、不参与匹配。备选「版本进匹配键」被否：语义审判是付费节点、重跑是常态，一次不改变任何结论的重审会把整本账清空 ⇒ 等于没有豁免。失效改由**证据自身**保证（语义证据是候选/正文的连续原字节）。豁免入口逐层挂在该层的 0 成本复检节点上（`stage5.review` / `stage6.hook`） |
| 2026-08-16 | DeepSeek V4 Pro 真实并发观测 | 经显式付费授权，以禁缓存、非思考、8 token 上限运行 32 个逻辑请求：32 成功、0 失败、实际峰值 32、线上尝试 32、ledger 32 行、未记账尝试 0、429 0；prompt 640 token、completion 45 token。`report.json` SHA-256 `511cd2d9879329c63b96c68e8ba2b86aa800ba5195177c0b1bf1141b1297d2c4`，`ledger.jsonl` SHA-256 `64750e6a609218f99e42207eb161eb526169870e2fbcc03167dbd6599ea0d24b`。报告位于本机忽略目录 `outputs/provider_limits_20260816_32x32/`；429 重试/退避证据仍来自不花钱的合成回归，不冒充真实 429 |
| 2026-08-16 | 两本新书全链探索复跑（**历史问题来源，不是 current-schema 验收**） | 从空目录 `outputs/fullchain_20260816` 起，经语料库屏 `POST /api/upload` 摄入两本此前未跑过的书，推到当时版本的 `stage7/book.txt`：《收养三只反派幼崽后》17 章 / 12,094 字、《常春杜若》25 章 / 18,344 字。账本 215 次调用（100 次命中本地缓存、115 次真上线，0 次 error），prompt 958,038 / completion 1,666,673，reasoning 占 completion 89%（`stage5.write` 94%、`stage2.blueprint` 87%）；21 条作业记录 18×exit 0 / 1×exit 1 / 2×exit 2。该盘面真实暴露了定向重生成、Stage6 段间分隔、原文案重合、exit 1 停队、轮次编号、`.INVALID` 退休和作业状态区分等问题，因而保留为根因与成本证据。此后 artifact schema、provenance、语义闸和测试选择面均已变化，且该目录没有现役无正文 manifest；不得再把它的旧测试结果或“0 failed”表述用作当前通过证据。current-schema corpus、FC-08 与 FC-13 的未完成实证统一由 `docs/REQUIREMENTS.md` 追踪 |
| 2026-08-15 | Stage6 开篇文案质量 A/B（12 次付费调用。**原始响应目录与两个探针脚本均已删除，本行是唯一存活记录**） | 改版最佳候选匿名主编均分 8.78 对当前定稿 4.88（+3.90/10），实验最佳 3/3 获胜 ⇒ 现役 stage6 采用三策略 `source_architecture` / `payoff_preview` / `scene_punchline`、一次调用产三候选、六维固定权重（阅读欲 30% + 正文声线 20% + 具体场面 15% + 期待承诺 20% + 文案技法 15% − 摘要感 10%）。补测「单份直出」均分 6.60 对三候选最佳 8.75（−2.15，仅 1/3 获胜），且三条单份都没有实质事实矛盾 ⇒ 失败原因是**创作搜索方差**，不是校验器误杀 ⇒ **想为省钱改成单份直出，必须先拿新样本推翻这一条**。放开剧透与体裁自由的同时保留实质矛盾硬闸，依据是那条虚构了「会改变故事理解的结果」却仍得 7.55 原始质量分的候选 |
| 2026-08-16 | Stage5 文风漂移闭环标定（3 本 65 章，calibration 2 本 / 留出 1 本，`tools/calibrate_style_corpus.py`。**源盘面已删，本行是唯一存活记录**） | 够格 `UNIQUE` 自动付费修的只有两族：`dialogue_halved`（前件收窄到 shallow、计划无净压缩、源章至少 4 句，且只许写回眼前已有台词）与 `layout_drift`（只插换行，不改一个字）。`dialogue_ratio_drift`（命中 19/39 与 11/26，而同批 shallow 章对白**句数**中位仍是 1.00×）、`band_drift`、`para_len_drift`、`dialogue_fewer` / `dialogue_broken` 一律 `INFO + FREE` 进待人工——**「比例偏了」不等于「台词丢了」，按比例自动加台词或删叙述会把一个真实读数变成错误修法**。`short_run_report` 保持 `metric`：本批源文 p95（5.47 / 1.06 每千字）不足以替代冻结的 163 本体裁基线 7.68，因此不调线。执行落点是 `contracts/judges/stage5.py` 的注册表与 `novel/stages/stage5/write.py::revise_targets`，不靠文档约定；要扩大 `UNIQUE` 范围必须新增未参与定线的真实修订对，并同时交付内容回归与目标指标变化 |
| 2026-08-16 | 代码灾备恢复演练 | 384 个文件 / 5,062,923 字节的 code snapshot，归档 SHA-256 `c303064a565ecad877d76b9bb488ae2a1c0d687052edfa53f7d1f898d2e088be`；空目录恢复完成归档内与落盘后两轮哈希核验，根 `.env`、`outputs/`、`Test_data/`、`.llm_cache/` 与 `settings.local.json` 均未进入归档，`tests/test_fixture_registry.py` 在恢复目录 6 passed。**验收范围只有代码**：版权语料与付费产物按同日裁决不备份，丢失即需重新取语料并重新付费生成 ⇒ 不得把代码恢复能力宣传成数据灾备能力。操作规程见 README「备份与恢复」 |
| 2026-08-17 | `stage5.semantic` 整节点下线裁决 | 实跑证据而非偏好：① 本项目重构**不跨章重组**（实测 18/18 章 `from_source_ordinals == [自身]`）⇒ 书级剧透那条主风险面在计划形态上几乎不存在；② 同一本书上书级语义判官 18 章里 5 章命题抽取返回 uncertain、8 条 secret 全部判不动，而 stage2 的确定性判据加上成书逐字对账的「上游 info 下游投影」（**0 次调用**）给出了确定结论；③ 成本上它占单本实付调用的 54%、输出 token 的 55%。`stage6.semantic` 保留（2 次调用、1.7% token，实测直接纠正过一次定稿）。**下线的是节点不是能力**：要恢复须连同 `contracts/schema/stage5.py` 的 `SemanticReview` 一族、stage5 的 semantic 模块（已随节点删除）与两条判据一起恢复。⚠ 保留下来的那个节点，其**自动放行装置**已于次日（2026-08-18）另行下线，见下一行 |
| 2026-08-18 | evaluator profile 装置与 stage5 收缩臂整批下线 | 裁决依据是「它从未被标定过，而那条分支一次都没有走通过」：`stage6.semantic` 的默认路在每台宿主上都是 exit 2（`require_enabled` 在 transport 前终止），stage5 收缩臂的 `trim_equivalence` 闸同理——盘上 14 份 `trim_audit` 的 reason 全是「未发起收缩调用或语义调用」。⇒ 删 `EvaluatorProfile` / `CapabilityMetrics` / `GroundTruthManifest` / `GroundTruthCase` 与八类能力枚举、`novel.quality.profile`、四个 `tools/` 校准件（3254 行）及其测试；`SemanticDecision.verdict` 收成 `Literal['UNVER']`，`hook_semantic_contradiction` 由 HARD 降为 `unver`（它命中 Finding 的两条路都要求 automated FAIL，已不可达；继续挂 HARD 只能靠伪造反例或给双向守卫开豁免口）。**留下来的是判官不是闸门**：双盲 A/B 照跑，产带证据的 UNVER 与匿名质量排序。⚠ **产品语义变了**：过去带校准 FAIL 的候选会被永久拒绝，现在没有任何机制会替人否决一个候选，横在「有矛盾的文案」与成书之间的只剩人读判官证据这一道（R-9.6 / R-9.19 / R-10.14 已同批改写）。⚠ 三个产物版本随之破坏：`STAGE6_SEMANTIC` v2→v3（**刻意不登记 migration**——把一份 `automated=true` 的旧 PASS 迁成普通 UNVER 是改写历史）、`STAGE5_TRIM_AUDIT` v1→v2、`STAGE5_TRIM_RESOLUTION` v1→v2 ⇒ 盘上三本书的这三层一律拒读，按同日裁决作废，后续新书走新 schema 重跑。验证：全量非 corpus `pytest -q` 0 failed，`validate_skill.py` PASS，`preview` / `state` / 直接读旧产物三条 0 成本路径逐条核对。**本次没有任何付费实证** |
| 2026-08-18 | 语料闸 251 条红逐条定性（`-m corpus` 全量，`NOVEL_OUTPUT=outputs`，盘上 3 本） | **收官 898 条：800 通过 / 16 失败 / 82 跳过**（基线 646 / 251 / 1）。四类根因：① **127 条**是往返测试在空沙箱里撞写盘口后置条件——`_assert_self_fresh` 把三态里的 `unknown` 与 `stale` 压成「不是 fresh」，而空沙箱里每条上游边恒是 `ghost`。**生产代码没动**：git 考古证明这道闸防的是 `stale/consumed_changed`，而 `unknown` 那一半也确实抓到过 `consumed_undeclared`（`novel/consumption.py` 里 `STAGE2_GLOBAL_EDIT` 那行投影就是被它逼出来的）⇒ 改测试装置，只在往返那一条上收窄成「只放行 `unknown` 家族」，并配一条两侧各钉一次的守卫。② **5 处守卫漂移**，其中 4 处同源于 `R-5.5` 那次契约转向（详见 `docs/LESSONS.md` §21 第三形状）。③ **103 条**是语料链条真过期：`stage4/writer_context.json` 被本轮保真点修复重生成，两本书的 stage5 成稿相对它 `consumed_changed`（它们是照空下发面写的）、三本书的 `stage7/name_map.json` 同理。改成由生产落点现算的声明式前提；单本用例改挑「链条当前」的那一本而不是排序第一的那一本，因此 24 条是真跑而非跳过。④ 剩余 16 条见 `docs/REQUIREMENTS.md` TODO-21。⚠ 现有 3 本都不是「用最终代码从来源重建」的，**不能**为 TODO-21 提供通过数字 |

| 2026-08-18 | 产物 schema 归零 v1、迁移机制整批删除、三本书与 `_service/` 清空 | 删除依据是可复算的盘面读数而不是偏好：删前 3 本书共 **177 份 JSON，其中 46 份今天已拒读**（26 份 `trim_audit` v1/当前 v2、18 份 `trim_resolution` v1/当前 v2、2 份 `semantic_1` v2/当前 v3）——**这 46 份全部落在未登记 migration 的家族上**；反过来，登记了 v2→v3 migration 的 8 个家族在盘上**一份低版本文件都没有** ⇒ 整条迁移梯子零消费者，却让 `AGENTS.md` 与 `docs/schema/_README.md` 里「只接受当前版本」那句话假了一整段时间。⇒ 删 `ArtifactSpec.migrations`、6 个迁移函数与 5 张 `MIGRATE_*` 表，30 个产物全部归零到 v1，`decode_artifact` 收成「版本 != current 一律拒读」。**这次是让文档变真，不是改文档迁就实现。** 版本号自此是「这个产物的形状被破坏过几次」的计数器：破坏形状要 `current_version` +1 **并同批回答「存量产物由谁重建」**。⚠ 同批发现并修掉一个会让守卫静默失效的形状：拿**模板路径**（带 `{n}`/`{nn}`/`{mode}`）去 `decode_artifact`，`artifact_regex` 把占位符编译成 `\d+` ⇒ 模板自己匹配不上任何路由 ⇒ 走未登记分支原样返回、一个异常都不抛，用模板写的「拒读」用例会全程绿着却一次闸都没查到（`tests/test_artifact_versions.py::_concrete` 与它自己的前提守卫）。版本守卫的分母同批从十几条提到 **30 份产物 × 8 种非法标记**，且从注册表派生。⚠ 三本书与 `outputs/_service/` 已整批删除（保留三份 `final_review_*.md` 与 `.llm_cache/`）——它们本就不能充当验收盘面（上一行）。删后 `-m corpus` 在**不设 `NOVEL_OUTPUT`** 时 0 failed 全 skip；设成一个空 `outputs/` 会红 4 条断言语料存在的闸，**那是正确报警不是缺陷**。验证：全量非 corpus `pytest -q` 0 failed，外加一次**真跑闭环**（0 成本）：`stage0.remove` + `stage1.partition` 跑通新书 `2019402867061048656`（17 章 / 11,887 码点，两层判据全 OK，`state` 与 `inbox` 均 exit 0），盘上 6 份产物 `_schema_version` **全部为 1** 且逐份经 `decode_artifact` 真读通过——其中 `removal_report` 正是从 v2 降到 v1 的那一族，⇒ 归零后的「真写盘 → 真读盘 → 真新鲜度」闭环已走通，不是只有合成断言；把 marker 改成 2（仅内存）当场拒读并报出文件与两个版本号。**本次没有任何付费实证；语料批整批 skip，不构成验收** |
| 2026-08-18 | LLM 成本口径实测 + 两处零语义风险的缓存对齐（**形状已验，供应商侧命中率未付费实证**） | 先纠一条口径：账本的 `completion_tokens` **包含** `reasoning_tokens`，两列**不可相加**——判据是 `.llm_cache/` 里 1639 份带 `usage` 的真实响应：`(completion−reasoning)/content_chars` 中位 0.460、`reasoning_tokens/reasoning_chars` 中位 0.602，两者同量级且都是合理的中文 token/汉字比；若两列可加，正文得是 3–5 token/汉字。⇒ 三份 `final_review_*.md` §6 的表头已补注。全量读数：prompt 7,787,698（其中供应商前缀缓存命中 2,668,160 = **34.3%**）、completion 13,031,022（其中 reasoning 11,457,627 = **87.9%**）、净成品输出仅 1,573,395；写作类调用（n=720）reasoning 中位 **9109** token 而成品中位 **1213**。**⇒ 输出侧是成本主体，reasoning 一项约占八成，输入侧优化的天花板约 7%**——「提高缓存命中」不是降本的主路，`reasoning_effort` 降档才是（仍受 `docs/REQUIREMENTS.md` FC-13 的付费实验前置约束，本次未动）。命中率随 prompt 变大而崩：0–1k 档 48.5%、6k–12k 档 47.6%、12k–30k 档 20.1%、30k+ 档 **3.5%**。据此落两处**不改变模型看到的内容**的改动：① `stage6.semantic` 双盲角色后缀由 system 末尾移到 user 末尾，唯一拼装口 `novel/stages/stage6/semantic.py::judge_request` 同时供 `_runtime_contract` 取用（两处各拼一份必然分家）⇒ 两名 judge 的 system 逐字节相同、judge B 可命中整包约 45k token 的材料前缀；`_provenance.prompts` 键随之从 `HOOK_SEMANTIC_SYS_A/_B` 改为 `HOOK_SEMANTIC_SYS` + `HOOK_SEMANTIC_ROLE_A/_B`（旧键把已不在 system 里的后缀继续自称 SYS，是来源账说假话），`evaluator_fingerprint` 因此改变、存量 semantic 产物会 stale ⇒ **盘上零存量，无实际影响**。② `novel/llm/client.py::LlmClient._warmup_gate` 首发独占：逐章扇出默认并发 32，N 份请求共享的 system 前缀（`stage2` 的 4073 token / `stage5` 的约 1900 token）此前互相错过；闸只守顺序不守并发度，`finally` 无条件放行（首发失败不许把整批锁死），本地缓存命中不消耗名额。⚠ `tools/observe_provider_limits.py` 显式 `warmup=False` 并配守卫——它量的恰恰是并发峰值，闸开着 `peak_active`/`observed_429` 会全部失真。**验证：全量非 corpus `pytest -q` 0 failed；本次没有任何付费实证。** 供应商侧命中率须在**一本新书**上按 `tools/ab_compare.py` 的 `prompt_cache_hit_rate` 取证（改动 ② 不改 prompt 字节 ⇒ 重跑旧书会全部命中本地缓存、0 次真上线，证明不了任何事） |
| 2026-08-18 | `reasoning_effort` 真实档位纠正，降级阶梯由 4 档收成 3 档（**依据是用户提供的官方文档，无 wire 实证**） | 本模型的档位是 **low / high / max** 三个；`medium` 与 `xhigh` **是合法参数**——服务端只是把它们**静默映射回 `high`**。⇒ 原阶梯 `high→medium→low→low/nothink` 的第二档是一次**白花的钱**：它不报错、不重试、看起来一切正常，而 `cache_key` 含 `reasoning_effort` ⇒ 它与首档是**两个缓存键、同一种模型行为**——首档拿回空响应/截断之后，第二档换个键把同样的行为再跑一遍，大概率同样失败，**一分预算都没腾出来**，而「降 effort 就是给正文腾预算」正是这条阶梯管用的全部理由。四档里只有三种行为，却付四次钱。⇒ `LADDER` 收成 `high→low→low/nothink`，救场能力**一点没少**。⚠ 三处闸同批改成关闭式拒绝 `medium`/`xhigh`（`novel/llm/client.py::rungs_from_effort`、`novel/llm/budget.py::_render_prompt`、`contracts/invariants.py::NodeSpec` 的 `effort` 取值面），而拒绝理由**不是「API 不认」**，是「放行等于允许一个写着降了档、实际一档没降的配置，而账本 `reasoning_effort` 那一列会如实记下 `medium`，事后分辨不出来」——那正是 A/B 最怕的脏数据。⚠ `max` 仍不在梯上且仍被 budget 关闭式拒绝：它是**升档**（多 79 token 官方 effort 前缀，且前缀明令写出完整推敲过程 ⇒ reasoning 更多），拿它救「推理吃光 `max_tokens` 导致截断」是火上浇油；它属于质量实验（FC-13）。**恢复坐标已查明**：原始前缀字节在 `4744479^:novel/llm/budget.py`，476 字符 / 79 token，framing 规则是「low/high 不加文字前缀，只有 max 在 BOS **之前**加这一段」——前缀是发出去的字节，凭记忆重写等于换了一个 prompt。⚠ 附带效果：`tools/current_corpus_paid_preview.py` 的全条件 wire 硬上限由 **574 降到 432**（−24.7%），因为它本就用 `rungs_from_effort()` + `len(rungs)` 从生产落点复算档数——**生产侧算对了，红的是测试里 4 档时代的冻结快照**，同批把那些期望值改成「逻辑请求数 × `len(LADDER)`」的乘法形态，钉住的仍是逻辑请求数。同理 `tests/test_llm.py` 里六处硬编码档位列表改为 `LADDER[:N]` 派生。**验证：全量非 corpus `pytest -q` 0 failed；本次没有任何付费实证**，档位映射行为本身也未经 wire 验证（若要钉死，4 次 8-token 请求即可） |

| 2026-08-19 | `structure_changed` 换口径 + 补 `R-5.16`~`R-5.18`（**0 成本层，无付费实证**） | 起因是用户反馈「重排后某些章看起来很混乱，不知道要表达什么」。根因不是「允许重组」是**强制重组**：旧口径把「源单元 ≥4 的章保持源序」判成 HARD 红，够大的章模型没有「本章顺序本来就对」这个选项。盘上唯一那本的读数支持这个诊断——**首拍 `narrative_role` 恒为 `cold_open`，11/11 章**；全书 37 拍 `transition` = `earlier 18 / start 11 / return 6 / later 1 / continue 1`，**向前推进的 `continue` 只出现 1 次**；书级 `reconstruction_strategy` 逐字写着「用冷开场和倒叙制造追读钩子」，11 章一章不落照做。⇒ 换成「与恒等基线（`measure.identity_plan` 的结构切片）逐格相同才红」，**顺叙不再违规**。⚠ 判定式只有两项（分组序列 ≠ 基线 / 存在 `summary` 单元），因为「入口移位」被前者蕴含；写成三个松散的 `or` 会**恒绿**（盘上 11/11 章有合并、9/11 有 summary）——反例改取自生产落点 `identity_plan()`，两条方向相反的测试成对钉住「不再逼迫移位」与「没被放宽成恒绿」。⚠ 同批补了重排之后那一段的守卫：此前 `structure_transition` 只查标签的**句法自洽**，标签方向对不对、回溯有没有收口一条都没人查。`R-5.16`（HARD）比接缝方向、`R-5.17`（INFO）查回溯收口、`R-5.18`（度量）报书级构成。⚠⚠ **`R-5.16` 的第一版设计误红 5/26**：`transition` 说的是**故事时间**，而源叙述位置只在 `present` 层内才是它的代理（那本 39/106 个 beat 是 `recall`、2 个 `hearsay`）⇒ 加 `time_layer` 门后 13/26 可判、**0 命中**；那 5 处逐一是 3× `recall→recall` · 1× `hearsay→present` · 1× 上游漂移，一处都不是可证的违约。`R-5.17` 定 INFO 不定 HARD 的理由是**测量确定但规范未标定**——小说可以在闪回里结束一章；`R-5.18` 刻意无阈值、刻意不叫「饱和度」（`Q-9`）。三条读数的正常区间待标定，见 `docs/REQUIREMENTS.md` TODO-30，且**立条目的读数不能当基线**（旧 prompt 逼出来的分布 + 对着旧版 stage2 产的计划）。⚠ prompt 同批改了五处（删强制措辞 · 禁止在书级策略里定全书统一手法 · `transition` 加「说真话」条款 · 冷开场要稀缺 · 回溯要收口），阈值 `PLAN_RECONSTRUCT_MIN_UNITS` 提为唯一落点经 `ENUM_BLOCKS` 插值。⚠ **`PLAN_SYS` 指纹因此改变**：盘上那份计划从此只能整跑重来，不能按章 `--chapters` 修（`plan.py` 会拒绝在旧基线上做定向重规划）。**验证：非 corpus 套件 0 failed（含 4 条新增反例/绿对照与 3 条口径断言）；`-m corpus tests/test_stage4.py` 相对动手前基线无新增红——那 5 条红在改动前就存在，根因是 stage2 升 v2 后 stage4 未重建（16/106 个 `beat_id` 已漂）。prompt 改动能否真的降低 `cold_open` 饱和度，需要一次付费重跑，本轮未验证** |
| 2026-08-20 | 生成侧迁移 `deepseek-v4-flash`（判官例外）+ 空正文同档重采（**裁决 + 0 成本实现，无付费实证**） | 三条用户拍板（看过全部反面证据后决定，不再以成本以外理由重议）：① **除语义判官外全链 flash**——开发期成本不可接受（pro 约 9 元/本 vs flash 约 3 元/本）；② **`stage6.semantic` 恒 pro**——实测（2026-08-19/20，无情道有 ground truth 的样本）flash 判官把唯一与正文矛盾的候选判 PASS 且合并排名**第 1**，两名判官排序近乎相反，证据区间圈成 `[0,85)` 等于候选全文——「给不出有区分度的证据」对结论必须绑证据的判官是死刑；③ 盲读对照（用户亲自读）两版区分不出高下 ⇒ 「flash 文笔更差」不成立，但它只背书 `stage5.write`，不背书 stage2/stage4/判官。落成形状：`novel/config.py::DEFAULT_MODEL` 改 `deepseek-v4-flash`；`NodeSpec` 新增 `model` 一格，`stage6.semantic` 钉 `deepseek-v4-pro`（优先于 `DEEPSEEK_MODEL` 与全局默认——「判官例外」从纪律变成形状，忘带环境变量烧不成 flash 判官；全表唯一钉死值由 `tests/test_invariants.py` 钉住，与 `MODEL_BUDGETS` 的登记交叉核对在 `tests/test_llm_budget.py`，接线在 `novel/cli/main.py::_llm_client` 并由 `tests/test_cli.py` 钉住）。⚠ flash 迁移实测背景（三本书，判官全程 pro）：3/3 本在 stage2 层被拦、3/3 本在 `stage4.plan` 被拦；两臂 reasoning token 几乎相等（差 0.2%）而 flash 多用 26% 调用、15% 输入；flash 最终稿 10/23 与 8/19 章产自降档档位（pro 对照 0/23）。该缺口后来由 `artifact_adoption → attempt_id → ledger` 收口，见 2026-08-23 行。同批修**空正文降档**：`stage4.plan` 的 `high` 档实测返回 `content_chars=0` 而 `reasoning_chars` 上万、`finish_reason=stop`（非截断，`novel/llm/cache.py` 早有记录），旧阶梯当「这一档不行」直接降档 ⇒ 3 本里 2 本的计划**从未在 `high` 档被评估过**、pro 同受影响（`ab_compare` 里 pro 在 `stage4.plan` 也降了 3 步）。⇒ `novel/llm/client.py` 两个入口的 wire 循环对「答案流丢失」（`_answer_lost`：正文空 + stop）先**同档重采一次**（`MAX_SAMPLES_PER_RUNG=2`；与传输层重试、降档是三条独立循环，不许合并）再降档；截断（`length`）与非空内容被闸拒不重采（腾不了预算 / 大概率同形状）。`llm_rung_rejected` 事件新增 `resample` 字段且重采那一声的 `next_*` 指回同档——「重采」与「降档」在统计里分得开。⚠ `tools/current_corpus_paid_preview.py` 的 wire/输入/completion 上限同批 ×2（重采对单档重试臂一样适用），期望值继续从生产落点派生。动 schema 前的基线快照已做（`tools/backup_snapshot.py` create-private，255 份 / 7.7MB，含两臂四书与 ledger）：`outputs/_snapshots/pre_schema_work_20260820.tar.gz`（本机盘，非加密离线介质）。**验证：全量非 corpus `pytest -q` 0 failed + `npm run seams` 通过；本次没有任何付费实证**（后续真实救回率、Flash 能力实测与活动清单收口见 2026-08-23 相关记录） |

| 2026-08-21 | Stage0 审批收口：**停产零命中自动批 + `not_run` 出局 + 存量 auto 降为只读不承行**（Stage0/1 改进第一批之一，**0 成本路径，无付费实证**） | 起因是外部评审指出 `auto_zero_hit` 与「人工闸门是纯数字番外盲区全链唯一防线」的宣称直接矛盾：纯数字番外恰好产生零 removed/零 warning/clean==source，恰好绕过人工闸门（P0-1）。裁决（`Stage01_improvement.md` + `plan_reciew_stage01.md` 审阅后定案）：① **首跑恒 pending**，`_auto_approval_eligible`/`_with_auto_approval` 删除；② 执行资格 = `approved=='approved'` **且** `approval_origin=='human'`，唯一落点 `novel/stages/stage0/eligibility.py::is_stage0_executable`，五处消费方（s0_approval 判据 / stage1 启动前置 / 队列人工闸 / 付费审批防线 / stage1 物料视图）统一改调，`tests/test_stage01.py::test_execution_eligibility_has_exactly_one_home` 钉住不许复述条件；③ **存量 auto 产物只读不承行**——2026-08-21 preflight 扫描 `outputs/` 树：2/2 本正式报告都是 `auto_zero_hit`（不是假设），它们可 decode/展示/审计，但 stage1/队列/付费全拦，UI 给稳定理由码 `legacy_auto_approval_requires_review`，人工重批（`run_approve` 对 legacy 不再幂等 no-op，追加 `approve` 审计条目、origin 改 human、round 不变）后恢复；④ `APPROVAL_STATE` 三态改两态——`not_run` 是从未落盘的死值（preflight 0 实例），「没跑/崩了/阻断」由「产物不存在 + JobState」表达，五行状态解释由 `tests/test_stage01.py::test_unapproved_states_are_interpreted_not_conflated` 钉住；⑤ `ApprovalOrigin.auto_zero_hit`/`AuditAction.auto_approve` **保留为历史只读取值**（枚举不收窄，旧产物照常 decode），validator 不变。**取值收紧以 preflight 零违例为前提**：扫描实际数据目录（`outputs/` 2 本全链产物 + `Test_data` 8 本原文过纯函数）确认新约束 0 违例——这是「纠正从未被合法生产过的宽松 contract」，不是迁移，故不升 `current_version`。**验证：`tests/test_stage0_revisions.py`（当时叫 test_stage0_merge.py） + `tests/test_stage01.py` 50 用例 0 failed（含 legacy 三连测：可 decode / 不可执行 / 重批恢复）；API/队列/审批/夹具/分层 10 个文件 273 用例 0 failed；付费路径无涉** |
| 2026-08-21 | Stage0 三方合并双端防御：**人工分叉不自动 merge + source 变化拒之门外 + edit-plan 恢复出口收口**（Stage0/1 改进第一批之二，**0 成本路径，无付费实证**） | 起因是外部评审 P0-3：`run_merge_accept` 的 `removed[]` 整体取 `snapshot.removed`（theirs 机器账本），人工圈选的删除记录在接受合并后从正式账本丢失；且 diff3 跨原文合并时旧 span 坐标系无意义。落成形状：① 当时的 `stage0/merge.py::has_human_content_divergence`（2026-08-22 阶段 C 随三方合并整份删除）（唯一落点）——`edited_by_human` / `removed[]` 含 `origin='human'` / **`clean_sha1 != machine_clean_sha1`（客观哈希，不信标志位）** 三条任一为真即分叉；② `run_source` 分诊：原文变化 → `stage0_source_changed`（SourceConflict，R-0.14）连 candidate 都不写；有人工分叉 → `stage0_human_edit_protected`（HumanEditProtected）不写 candidate、正式对一字节不动；仅「同原文 + 无分叉」允许 candidate（此时 `ours == base`，diff3 退化为取 theirs，账本可证正确）；③ `run_merge_accept` 接受端二次防御：重读正式 report，分叉即拒绝并**撤销归档** live candidate——生成端守卫挡不住升级前落盘/WAL 恢复/手工构造的 candidate；唯一例外是盘上已有该 accept 的 WAL 意图行（拒绝会让审计链永不闭合）；④ 恢复出口收口：`run_edit_plan` 的 provenance 一律服务端落定（span 与现机器段一致的条目原样保留 origin=machine，人工新段按 `s0_removal_ledger` 同口径复算 origin=human，伪造 origin/line_no/preview 无效——测试以「视图生成自洽计划后篡改 provenance」钉住）；⑤ S-00 视图把扫描器新认出的非正文段标成 `flagged + removed_kind` 的 keep 候选（`contracts/schema/api.py::Stage0Segment` 契约放宽为「remove 或带 removed_kind 的候选才可 flagged」；预选仍只认 kind='remove'）——有人工分叉的轮次采纳机器新结果只有重选段一条路，候选必须看得见。测试重构：`test_stage0_merge.py` 的「机器结果变化」不再靠改源文件制造（那现在是另一条分支），改 patch `ingest.nonbody_kind` 模拟扫描器学会新番外词——单点 patch 让产地与视图两条路看到同一份新结果。**验证：test_stage0_merge + test_stage01 + test_api_stage01 + test_api_edits + test_schema_api 0 failed；队列/审批/夹具/不变量/分层/根身份/CLI 13 个文件 0 failed；付费路径无涉** |
| 2026-08-21 | Stage1 覆盖不变量 R-1.15 + 载体收紧；哈希前像成文 + golden vector（Stage0/1 改进第一批之三/四，**0 成本路径，无付费实证**） | 起因是外部评审 P0-5/P0-4：载体 validator 只查三条，且「投影一致」只在判据层间接成立。落成形状：① 新判据 `s1_decomposition`（**R-1.15**，HARD）——`[0,total)` 完整分解为 hook+逐章标记行+章正文+tail，覆盖游走 + 算术恒等 + `chapter_number` 文法解析，**不与 `build_sections` 产物自证**（生产算法验证自己是评审点名的洞）；反例为 schema-valid 定向篡改（整条第一章标记行吞进 hook），同用例内对照绿——真实产地构造不出分解违例，故不入 ingest 夹具纪律（那是真实可产生违例的样本面）。② 载体收紧（**不改键形状、承重产物字节不变**）：`_spans_are_consistent` 扩充（hook_end≤body、body≤total、章区间有序不重叠、end≤body）；`HookCopy.section_id` 收紧为 `Literal['sec_0001']`；两份产物的 text non-blank（共享 `NonBlankText`）——合法性以 preflight 零违例为前提。③ Unicode 码点 seam：含 emoji/扩展区字符端到端用例钉住 span 恒 Python 码点、服务端切片逐字对齐、视图 join 回原文；前端按设计不拿 start/end 切 JS 串（`HighlightedText` 无下标 props），UTF-16 的坑结构上踩不进。④ 字节等价：现存可用输入（`Test_data` 8 本 + `outputs` 现存 1 本）重生成 `main_text`/`hook_copy` 与改动前基线 **18/18 一致**；⚠ 163 本全语料已随原型仓删除（2026-08-16）⇒ 全量等价本机不可再验，如实记账；⚠ `outputs` 的「逼一个恋爱脑」目录在本次会话期间从盘上消失（非本会话操作；`outputs/_snapshots/pre_schema_work_20260820.tar.gz` 有全量备份），legacy 自动批实测实例因此从 2 本变为 1 本。⑤ 前像成文（P0-4）：`stage0.md` §2.5 新增前像定义表（UTF-8 / sort_keys / 紧凑 separators / 无 NFC / SHA-256 / **envelope 不进前像** / 三处剔除清单），锚以 `tests/test_stage0_revisions.py`（当时叫 test_stage0_merge.py） 的 golden vector（中文/非 BMP/None/bool/list/tuple→array/键序无关 + 剔除语义双向钉）。⑥ v2 漂移**先证后改**：git 考古确认 `removal_report` 曾是 v2（`a039121`），2026-08-18 归零 v1（`32643c5`，旧 v2 产物当日随三本书整批删除、做过真写读闭环）⇒ `stage0.md` 头部「(v2)」是漂移，统一为注册表 v1 并留历史注；envelope 恒写 `current_version` 由既有 `test_every_registered_artifact_round_trips_through_the_v1_envelope` 对全注册表钉住。⑦ 后续工作登记 `docs/REQUIREMENTS.md`：**TODO-34（P0-1b，置顶）统一 Pre-LLM Intake Review**、TODO-35 immutable revision 减法、TODO-36 DTO 分离 + SourceRef、TODO-37 stage1 字段类型变更包。**验证：stage0/stage1/API/夹具/覆盖率/规则注册表 0 failed；付费路径无涉** |
| 2026-08-22 | **Stage0/1 评审整改阶段 B：唯一一次形状破坏**（TODO-37 + TODO-36 后半 + 评审第三批改名；顺带销 TODO-38/39 —— ⚠ **是 `stage01` 那一份 38/39**（enums 夹具 / 宿主环境守卫），与 `docs/REQUIREMENTS.md` 里活着的同号两条无关，那张对照表在那份文档 `TODO-38` 上方。**0 成本路径，无付费实证**） | 起因：阶段 A 刻意不升版，把五项「都动形状」的整改攒到一批——分开做等于把`current_version` +1、`_SHAPE_BREAKS` 登记、「存量由谁重建」的回答各付三遍。窗口条件（用户 2026-08-22 裁决）：`outputs/` 只有两本，无情道已到 stage7（stale 无害、成书 txt 不是注册产物不受影响）、四人成行卡在 stage2 判作废 ⇒ **存量代价为零**。**七份产物 v1→v2**：`stage0/removal_report`（warnings 结构化 + 审批联合 + source_ref）· `stage0/machine_snapshot`（同上，digest 前像随之改变）· `stage0/merge_candidate`（内嵌 snapshot ⇒ candidate_id 前像改变）· `stage1/main_text`（删 section_id、`source_chapter_label: str`→`source_chapter_no: int`）· `stage1/hook_copy`（收成 `{text}`）· `stage1/partition_report`（warnings 结构化 + `num_chapters`→`detected_chapter_marker_count`）——重建人逐条登记在 `tests/test_artifact_versions.py::_SHAPE_BREAKS`：stage0 四份由 `stage0.remove` 免费重跑（⚠ 重建后回到 pending，须人工重批），stage1 三份由 `stage1.partition` 免费重跑，stage2+ 整批判 stale。**六项施工**：① 身份收敛——`section_id` 出局（一份产物只留一个系统主身份，让 stage2 的 `section_id_leak` 判据守的事实在源头成立）、章号回归 int；同批删死值 `CHAPTER_LABEL_ANOMALY.non_numeric`（载体保证是数字 ⇒ 结构不可达，同 `not_run` 先例）。② warning 退成结构化 evidence，措辞归 `novel/core/warnings.py::describe`（穷举 kind、不留 else 兜底），**阈值不进产物**（阈值改了旧产物措辞跟着变才是对的）；两条判据顺势从「只比 kind / 只对条数」升级成**逐条比整证据**。③ 审批四个平铺可空字段收成判别联合——五条 validator 互斥规则守的是同一件事（两种状态、字段集不一样），收成联合之后「pending 却带着批准时间」**在类型上就构造不出来**。④ `original_path` → `SourceRef` 判别联合：病不在重锚逻辑而在「它必须去猜」，改由产地判一次写进产物 ⇒ 读取端每支一条路、**没有回落这回事了**。⑤ `source_sha1` → `normalized_source_sha1`（270 处 / 35 文件，含五个派生名）：它算的是归一化之后那段文本，不是文件字节的 digest，旧名字会诱导人去核 `sha1sum 原文.txt`。⑥ `num_chapters` → `detected_chapter_marker_count`：名字说得出「独立来路」，判据才守得住（旧名字诱导人写成 `len(main_text)`，那一改把 R-1.5 变成恒等式）。**顺带收口两处第二落点**：执行资格的第五处消费方（前端自己拼 `approved==='approved' && origin==='human'`）改读响应里的 `executable`/`block_reason`；enums 夹具与 `payload()` 的对账守卫落地（此前静默分家两个月：新增 18/消失 5/变化 14 键）。**一处语义澄清**：「源被就地改写」在两支上本来是两件事，此前被 `original_path` 压平、只能靠「记录路径碰巧还在不在」区分（那是 Output 根搬没搬家的偶然）⇒ 现在 `external_file` 就地改写报 `stale/file_changed`（重跑即可）、`managed_upload` 报 `unknown/ghost`（内容寻址存储被破坏，重跑会再读一遍同一个坏存储）；两种都不是 fresh，闸门松紧没变。**验证**：后端全量 **2219 passed / 0 failed / 0 skipped**（`--basetemp=E:/_tr`，本机 LongPathsEnabled=0 且仓内已放不下 basetemp）；前端 54 passed、tsc 0、check-seams 96 文件通过且 mocks 20 份冻结基线未动。`Test_data` 8 本重跑 stage0+stage1 到临时 base：8/8 落新形状（四份产物全 v2）、8×9 条判据全 pass、逐章读数与阶段 A 实测**逐字一致**（7/8 零剔除、7/8 零异常，《逼一个恋爱脑当皇帝》仍报 2 处）。⚠⚠ **本批唯一能变成真钱的风险点是 prompt 字节**（`BLUEPRINT_SYS` 逐字节冻结、缓存键含全部 messages）：`f'{1}' == f'{"1"}'` 是改型安全的全部依据，因此配了专条守卫（做过变异验证）+ 8 本真书 **381,344 字节逐字节相同**的实测。⚠ `test_digest_golden_vector_pins_the_canonical_preimage` 的锚值一个字没动。 |
| 2026-08-22 | **Stage0/1 评审整改阶段 C：结构减法**（TODO-35 immutable revision + TODO-36 command DTO 分离。**0 成本路径，无付费实证**） | 起因是外部评审 P0-3 的终态裁决：本层此前是一个带三方合并、WAL、哈希链、乐观锁、版本历史的小型版本控制系统，而**四套机制守的是同一件事**——「`clean_text.txt` 与 `removal_report.json` 必须同进同出，而文件系统只保证单文件原子」；更要命的是 diff3 只合并了 `clean_text` **一个字段**，接受之后正式 `removed[]` 该写什么在契约里答不出来（用 ours 的 span 可能指向旧原文、用 theirs 会丢人工圈选、取并集坐标系不一致、从最终文本反推则因 `R-0.4` 允许任意人工改字而不唯一）——四种选择全都不正确，因为被合并的东西根本不是一个字段。**落成形状**：① 聚合根 = `Stage0Revision`（`{原文身份, 正文, 剔除账本, 告警}` 整团不可变）；`stage0/revisions.json` 是本层**唯一可变的那一格**（链 + `current` 指针 + 待裁决候选 + 审批），**一次原子单文件写就是一次提交** ⇒ 跨文件提交这件事本身消失，WAL、「唯一可恢复条目」、`_completed_merge` 那台崩溃重放状态机随之无事可守。② `clean_text.txt` 与 `removal_report.json` 退成 `current` 的**物化投影**——路径、字节形态、来源账的键与 `root_clean_sha1` 一个字没动（stage1~7 零改动），「是不是最新的」改由幂等纯函数 `materialize()` 回答。⚠ 补物化之前先分辨两件长得一样的事：中断留下的一定是**链上某一版**的正文，手改留下的字节链上一版都对不上 ⇒ 对不上就拒绝并指路（`stage0.edit --apply-edit`），不猜、更不把人的字悄悄冲掉。③ 审计链折叠进 revision：`revision_id = sha256(前像)`，前像**含 parent**、**不含 `at`/`code`** ⇒ 链本身就是哈希链，且同内容同 parent 恒得同一 id——`dismissed[]` 认的就是它，否则被否掉的候选每跑一次机器就复活一次。④ 机器重跑三条出路：整团内容相同 ⇒ 无操作；**当前稿是 machine 且未批准 ⇒ 直接发布**（无损：没有人工内容可覆盖，也没有「人看过并批准了这一版」可绕过）；其余只挂候选、正式稿一字不动，由人**整版三选一**（保留当前 / 采纳 / 采纳后再编辑 = adopt 之后照常走 `stage0.edit`，不做成第三个复合动作）。⑤ **TODO-36**：`Stage0EditPlan.removed` 从产物模型 `RemovedSegment` 换成命令模型 `RequestedRemoval`（只有 `span` + `kind`）——伪造 `origin='machine'` 或伪造预览从「服务端会重算所以无效」变成**构造不出来**；视图层顺带删掉一份纯属摆设的 provenance 复算（服务端必然再算一遍并以自己那份为准）。⑥ 判据 `s0_edit_archive` → `s0_revision_chain`（`R-0.5`）：逐条复算 `revision_id` + 两份 live 是 `current` 的物化投影；`edited_by_human` 出局（它是 `origin == human_edit` 的同义反复，且说不出「人**采纳了机器新结果**」与「人自己改的」之别，两者正文含义相反），换成 `revision_origin`。⑦ 批准的 `reason` 从审计条目搬进 `ApprovedApproval`——审计链折叠之后，批准是唯一一件不改变正文因而不产生新 revision 的人工决定，理由没有别的落点。**顺带回答 TODO-35 的第三条（统一 Pre-LLM Intake Review）：不再另做**——审批屏此刻已经是「原文分区 + 删除高亮 + warnings + 批准前分章预览」同屏，且一次动作就走完 编辑→批准→分章；revision 定下来之后「批准即原子发布」也真的成立了（一次单文件写）。接口上仍是两个端点是对的：编辑与批准的凭据、幂等语义与乐观锁对象都不同。**形状破坏**：`stage0/removal_report.json` v2→v3（身份六格 `round` + 三格 `machine_*` + 两格 `audit_*` 换成 `revision_id` + `revision_origin`），新增 `stage0/revisions.json`（v1，盘上无存量），**整份下线三份产物** `machine_snapshot.json` / `merge_candidate.json` / `_audit/edit_{n}.json` 与 stage0 的 `_rounds/`；重建人登记在 `tests/test_artifact_versions.py::_SHAPE_BREAKS`：`stage0.remove --source <原文>` 免费重跑，⚠ 重建后回到 pending 须人工重批。**验证**：后端全量 **2197 passed / 0 failed**（`--basetemp=E:/_tr`）；前端 54 passed、tsc 0、seams 96 文件通过且 mocks 20 份基线未动、build 0。**语料实证**（`Test_data` 8 本各跑一遍 stage0 + 人工批准 + stage1 + 批准前预览，0 成本）：8/8 落新形状、8/8 过 stage1，批准前只红 `s0_approval`（预期语义）、批准后 0 红；读数与阶段 A/B 实测**逐字一致**（7/8 零剔除、7/8 零章号异常，《逼一个恋爱脑当皇帝》仍报 2 处）。⚠⚠ **付费风险点是 prompt 字节**：本批不碰剔除算法与 stage1 ——`git diff 57e8a31..HEAD -- novel/stages/stage1 contracts/schema/stage1.py novel/core/ingest.py` 为空，且 8 本的 `clean_sha1` 与阶段 B 代码算出来的**逐字节相同（8/8）**⇒ `main_text` 不变 ⇒ prompt 字节不变。 |
| 2026-08-22 | Stage0 批准前只读分章预览（TODO-34 / 外部评审 P0-1b，**0 成本路径，无付费实证**） | 起因：08-21 第一批只**关闭了自动绕过**（停产 `auto_zero_hit`），人现在必须点批准——但审批页上只有原文 + 删除高亮 + warnings，而**纯数字番外在这三样里零信号**（章标记只是一个数字、正文无非正文关键词 ⇒ `removed=[]`、`warnings=[]`、`clean == source`），人点批准时照样看不见它。**裁决（用户 2026-08-22）：保持两步，只加只读 preview**；评审推荐的「合并成一次 Pre-LLM Intake Review」**否决于此刻、不否决于终态**——理由是 TODO-35 要换掉 stage0 的聚合根（revision + 原子发布），现在合并等于把统一审阅页建在即将删掉的 `_rounds` / live candidate 上，届时要再拆一遍；该余项已并入 TODO-35。落成形状：① 视图 `novel/views/ingest.py::stage0_partition_preview` 直接调 `partition_text` 纯函数——**不落 `stage1/` 任何产物、不经 `jobs.run`、刻意不查审批状态**（这条路存在的理由就是「人还没批」那一刻要能看）；② 新取值面 `CHAPTER_LABEL_ANOMALY`（`non_numeric` > `reset` > `gap` > `repeat`，四类互斥、优先级即元组顺序），随 `/api/enums` 自动下发，前端夹具同批补键；③ `GET /api/novels/:sid/stage0/partition-preview` → `Stage0PartitionPreview`，S-00 在 `ActorReasonFields` **之前**渲染逐章 outline，异常行高亮；④ `available=false` 走「按当前剔除结果分不出章」而不是抛异常——那正是应当在批准**之前**知道的事。**验收**：`enc_utf8_sig` 样本尾部接一段纯数字标记的番外（正文里一个关键词都没有，机器确认零命中）⇒ 第 4 章 `label_delta=-2`、`anomaly=reset`，第 1 章 `repeat`，`anomaly_count=2`；同批的对照用例要求连号书**一条都不亮**（处处报异常等于没报）。**真实语料读数**（8 本 `Test_data` 各过一遍 stage0 + preview，0 成本）：**7 本 `anomaly_count=0`**，1 本报 2 处——《逼一个恋爱脑当皇帝》第 16 与第 18 章**原标号都是 16**（`repeat` + `reset`，delta=-1）⇒ 信号稀疏、有区分度，不是到处亮的噪音。同一趟还读到一件更要紧的事：**8 本里 7 本 stage0 一段都没剔**（剩下 1 本剔 1 段）——**零命中不是边角情形而是常态**，即「审批页原有的三样对多数书本来就近乎全白」，这一屏补的是那个常态。⚠ **已知边界，别把它宣传成番外检测器**：番外若接着正文往下编号（50 → 51），四类异常一条都不命中——那种形态只能靠同屏首行预览让人读出来，**它降低盲区，不消灭盲区**。⚠ 顺带发现（未修，另账）：`web/src/fixtures/backend_v1/enums.json` 相对 `payload()` 已漂 19 个新增键 / 5 个消失键 / 14 个内容变化，本批**只插了 `chapter_label_anomaly` 一个键**，没有顺手整份重生成。**验证**：后端 `test_stage01` + `test_api_stage01` 0 failed（含纯数字番外验收、连号对照、不落盘断言、HTTP 路径）；前端 `Stage0Screen.test.tsx` 4 passed、`seams` / `typecheck` / `build` 全 0；付费路径无涉 |
| 2026-08-22 | Stage0 死值 `ApprovalOrigin.auto_zero_hit` 删除 + `Stage0Report.block_reason` 一格下线（Stage0/1 改进收尾，**0 成本路径，无付费实证**） | **为什么它现在是死值**——两条独立成立，缺一条都不足以删：① **没有现役写入方**：产地（零命中自动批）2026-08-21 已停产，全仓构造 `ApprovedApproval` 的生产代码只剩`novel/stages/stage0/removal.py::run_approve` 一处且硬编码 `human`；`revisions.py` 只写 `PendingApproval`；`RemovalReport.approval` 是 `revisions.json` 中 `current.approval` 的物化投影，没有第二条写入路径；HTTP 侧 `Stage0ApprovalRequest` 只收 `actor` + `reason`，不接受 origin。② **没有任何合法产物能携带它**——这一条是 08-21 当时**还不成立**、被后来两批改动变成立的：阶段 B（warnings/审批/source_ref）与阶段 C（身份六格换 `revision_id` + `revision_origin`）两次形状破坏把 `stage0/removal_report.json` 推到 **v3**，而本仓**没有迁移机制**（`decode_artifact` 只接受 `current_version`）⇒ 真正带着 `auto_zero_hit` 的存量产物（v1，顶层还是 `approved`/`approval_origin`/`approved_by`/`approved_at` 四格平铺）**连 decode 都过不去**；阶段 C 的权威格 `stage0/revisions.json` 更是新产物，存量书一份都没有。⇒ 该取值在盘上**结构不可达**，只有测试手工构造得出来 —— 与阶段 B 删 `APPROVAL_STATE.not_run` / `CHAPTER_LABEL_ANOMALY.non_numeric` 同一把尺子（载体保证 ⇒ 结构不可达 ⇒ 删死值）。**实证读数（0 成本，只读盘）**：`outputs/` 现存 2 本正式 stage0 报告，`_schema_version` 都是 **1**，其中《无情道第一她来自合欢宗》正是 `approval_origin='auto_zero_hit'`（另一本是 `human`）；过 `decode_artifact` 两本都报 `schema v1 不是当前 v3；本仓不迁移旧产物，请从 producer 重新生成` ——即那份唯一真实存在的自动批产物**现在连读都读不进来**，「存量产物仍须可 decode、可展示、可审计」这条当初的保留理由已经**被后两批改动作废**，而不是被本批推翻。**落成形状**：① `ApprovalOrigin` Literal 删除、`ApprovedApproval.origin` 出局，`by` / `reason` 随之从 `str \| null` 收紧为**必填 `str`**（那两格的可空只服务于「没有人批的那一支」；`min_length=1` 拦不住全空白串，非空判仍留在 validator 里）；② `eligibility.py` **保留为唯一落点**——用户 2026-08-22 裁决，`block_reason()` 与常量 `LEGACY_AUTO_APPROVAL` 整格下线、`is_stage0_executable` 留下。理由：谓词此刻恰好等于 `status == 'approved'`，但收口的依据从来不是条件复杂，而是「同一条 R 只有一个守卫」（`docs/LESSONS.md` §21），而 `block_reason` 在理由码只剩一个取值之后与 `executable` 是**同一个 bit**（那正是 `Stage0Report._verdict_and_reason_are_one_state` 当年校的东西），**一个 bit 只留一个投影** ⇒ 后端函数 + API 字段 + TS 类型 + mock + 前端 `legacyAuto` 分支一并删除；③ 连带：`s0_approval` 判据措辞与 legacy 分支、`checks.py` / `partition.py` / `ingest.py` 的双分支合一、`approvals.py` 报错措辞、`queue.py` 注释、`tools/current_corpus_paid_preview.py` 的 `stage0_auto_approval_eligible`（它还在说「首次零命中可自动批准」，08-21 起就是假的）整格删除、skill FN-001 与 workflow.md 的存量段改写。**不升版**：删一个 Literal 取值不改变现存 v3 产物的可读性（没有产物携带它），故 `current_version` 保持 3 —— 与「破坏形状才 +1 并同批回答谁来重建」那条一致。⚠ **顺带修掉一条恒绿守卫**：`test_execution_eligibility_has_exactly_one_home` 断言的是已改名的旧平铺字段`.approved == 'approved'`，阶段 C 换成 `approval.status` 之后它再也匹配不上任何东西——本批改成认当前表达式。本批把这条守卫看得比以前更重而不是更轻：条件越像一句废话，「顺手在消费方里直接写一遍」就越顺手。**验证**：`tests/test_stage0_revisions.py` + `test_stage01.py` + `test_invariants.py` + `test_api_stage01.py` 90 用例 0 failed；**全量非语料 `pytest` 2196 用例 0 failed / 0 error / 0 skipped**（含 skill 漂移校验器与文档↔测试耦合那一族）；前端 `typecheck` 0 · `vitest` 19 文件 54 用例全过 · `seams` 96 文件通过 · `build` 0；付费路径无涉 |
| 2026-08-22 | `stage0/revisions.json` 接进确定性 freshness 桥 + 一条反向守卫；`Test_data` 8 本 stage0/stage1 语料重建（**0 成本路径，无付费实证**） | **现象**：阶段 C 新增的权威格 `stage0/revisions.json` 在完成度视图里恒 `unknown/no_provenance`——盘上明明有一份判得出新鲜度的产物，视图却说「没有来源账」，而同层两份**投影** `clean_text.txt` / `removal_report.json` 都是 `fresh/match`。**8 本语料 8/8 复现**。**根因**：本层三份产物都没有通用 `_provenance` 槽位，只能走 `novel/store.py::_deterministic_freshness` 那座桥；阶段 C 新增产物时把它加进了 `ARTIFACT_PATHS` / `ARTIFACT_SPECS` / 三个节点的 `produces`，唯独**漏了桥**，而漏接不报错、只是静默退回通用那条路 ⇒ 找不到 `_provenance` ⇒ `unknown/no_provenance`。**为什么不是给它补 `_provenance`**：本层是全链源头、`inputs` 恒空，「这份产物照什么产的」由 `source_ref` / `normalized_source_sha1` 答完（理由 2026-08-22 之前就写在 `contracts/schema/stage0.py` 模块头，`code` 落顶层也是同一条），补一个 `inputs` 恒空的来源块只是为了让通用那条路走通，等于给同一件事记第二本账。**落成形状**：① `_stage0_revisions_freshness()` 用 `current` 指向那一版的 `content.source_ref` + `normalized_source_sha1` 回答——**取 `current` 不取 `revisions[0]`**：全链同一份原文由 schema 保证，但 `source_ref` 没有这条约束，读错指针会拿一份不生效的旧 locator 去判，得到一个永远自洽的假答案；② **只有原文这一条上游边**——`clean_text.txt` 与 `removal_report.json` 在它**下游**，把投影算成自己的上游就是让权威格拿自己的影子自证，那条边永远 match、判别力是零 ⇒ 人手改 `clean_text.txt` 时两份投影 `stale` 而权威格仍 `fresh`，「两份 live 还是不是 `current` 的物化投影」由判据 `s0_revision_chain`（`R-0.5`）逐条复算，完成度只读它、不另造一份账；③ 三份产物共用的那条源文边抽成 `_stage0_source_edge()`（原 `_stage0_freshness` 内联的解析/重锚/取指纹逐字搬过去，`DecodeProblem`/`OSError` 仍是 `None` ⇒ `unknown`，「读不到原文」与「原文变了」不许压成一个值）。**不进 `SELF_FRESH_DETERMINISTIC`**：stage0 整层都不在那张表里，那是有理由的——确定性边要去 resolve 真实原文，而「换了一份原文」在本层是一条**合法的、由判据去拒**的路（`R-0.14`），写盘口再拒一次就把它变成写不进去。**反向守卫**（这条才是真正防复发的那一半）：`tests/test_store.py::test_every_produced_artifact_without_provenance_reaches_the_deterministic_bridge`——凡是节点 `produces` 的、没有 `_provenance` 槽位、又会落进完成度 `artifacts[]` 的产物，必须在桥上有落点；作用域刻意不含 `*_edit.json` / `wontfix.json` 这类**人写的输入**（它们不是任何节点的产出，没有来源账是本分）与带占位符的 `stage5/{mode}/draft.md`（`state.py::_is_pattern` 本就把它挡在 `artifacts[]` 之外）。⚠ 守卫做过**变异验证**：临时摘掉桥上那条分支，它当场红在 `['stage0/revisions.json']`。**语料实证（0 成本，全链无 LLM）**：`Test_data` 8 本各跑 `stage0.remove --source` → 追检 → `stage0.approve` → `stage1.partition` → 追检。8/8 落盘；stage0 追检 8/8 只红 `s0_approval=pending`（预期语义）+ `s0_correctness` unver，批准后 8/8 `ok 12 · 命中 0`；stage1 8/8 `ok 9 · 命中 0`。剔除 1/8 命中（《四人成行》1 段 `kind=番外`，位于正文「(完)」之后，切口不截句，保留率 0.9880），其余 7 本零剔除、保留率 1.0000，按代批契约改做**反向漏剔抽查**（每本扫 8 类平台尾巴/广告模式，合计命中 5 处**全部**是正文自然用词：「评论区」叙述、「用手打/伸手打」里的「手打」子串、剧情里的监控 APP）。《逼一个恋爱脑当皇帝》仍是唯一有章号异常的一本（`s1_chapter_labels` 报**重号 [16]**、缺号 []），与阶段 A/B/C 三次读数一致。修复前后对照：`state` 里 `stage0/revisions.json` 由 **8/8 `unknown/no_provenance`** 变为 **8/8 `fresh/match`**，同屏两份投影读数一个字未变。**顺带修一条文档漂移**（阶段 C 遗留、与本批同一份产物）：`docs/schema/stage0.md` §2.1 的字段表仍列着阶段 C 已删的 `machine_normalized_source_sha1` / `machine_clean_sha1` / `machine_snapshot_sha256` 三行，而 §2.4 同一份文档里已经写着「阶段 C 一次换掉六格」并给出 `revision_id` + `revision_origin`——两处自相矛盾，删掉旧的三行（新格在 §2.4 已有，不重复登记）。**不升版**：本批不碰任何产物形状，`revisions.json` 仍 v1、`removal_report.json` 仍 v3。**验证**：非 corpus 全量 `pytest` **2199 passed / 0 failed / 0 error / 0 skipped**；前端零改动（`revisions.json` 的新鲜度是服务端算的，前端只渲染既有徽章，`web/src` 全仓搜 `no_provenance` 只有 enum 取值一处）。 |
| 2026-08-24 | **Stage7 前置整改批**（`Stage7_improvement.md` 评审的可解耦一半；**0 成本路径，无付费实证**） | **起因与边界**：外部文档提议把 stage7 的模型响应改造成「名称槽位生成器」（`RenameRequestV2` + `NameChoiceResponseV2` + compiler）。逐条复算之后**否决于此刻、不否决于终态**：① **成本论证不成立**——`ledger.jsonl` 423 次调用里 `stage7.rename` 只有 **6 次 ＝ 1.4% 调用 / 1.81% reasoning / 0.52% prompt**，FC-13 也早已单独量过它（占单本生成 completion 的 3.2%，`effort=low` 臂 reasoning −51.4% 且五门未见劣化），「总 token 降 ≥70%」即便满分也只是全书约 2%，而那一半收益是一格配置就能拿的；② **V2 有两处结构性回归**——`title_only 不进模型请求` 会漏掉「canonical 是描述词、alias 里却藏着原作真名」这一类（盘上实证：`七皇子` renamed=false，其 alias 是乳名 `壮壮`，现行 prompt 规则 6 最后一条专门写了这个情况），而「模型只选 group_id → new_surname」会关掉 `rename.py::_build` 那条兜底（模型的 `surname_map` 是唯一能捞回 `attested_surname` 四道过滤漏掉的真姓的东西，注释记着漏掉两个姓的代价是 6 处旧姓活着进了成书）；③ **V2 此刻验不了**——stage7 是链尾，而合并 main 之前盘上一本走完全链的书都没有（本行落地时 `outputs/` 只剩 `偶像公寓打工日常_2066575217393144601` 一本，它**确实**已到 stage7，见下面「存量」一段；V2 的三臂对照仍要等 `stage2-improvement`/`stage5-improvement` 也合进来再开）。⇒ 本批只做与 V2 解耦、0 付费、纯确定性的那一半。**落成形状**：**A** `not_renamed_reason` 归一 + 载体收口——契约注释早写着「`renamed=true` 时必须是 `null`」，而 `_build` 一直原样透传 ⇒ 盘上两本 flash 书 **7/7 是空串**，`rn_no_reason` 又只查另一侧 ⇒ 三种拼法谁也没在看；现在 `_build` 落盘前归零、`NameDecision` 加 `model_validator` 兜底。⚠ **只收这半边**：`renamed=false` 却没写理由仍归 info 判据，搬进载体会让模型漏写一条理由就作废本层唯一那次付费调用。⚠ 归一掉空串**不报**（丢一个空串没有信息），归一掉实质内容要报（那说明模型误读了契约）。**B** `surname_map` 契约文字放宽（用户 2026-08-24 裁定）——schema 两处 + prompt + judge note 四处都写着「只覆盖字面同姓且 ≥2 人的族群」，而 `rn_surname_split` 实际 enforce 的是「每条映射被某个**改名角色**真实使用」，且显式允许「姓只由别名承载」⇒ 盘上无情道空族群表 + 4 条单人映射全部合法通过，**文字与代码从来不是一回事**。改文字、代码一行不动；`family_groups` 的「≥2 人才成族」**保留不动**（`novel/core/naming.py` 靠它兜住 `采`/`府`/`圣`/`老` 那类假姓）。收窄会关掉 `R-10.6` 的兜底，故取宽口径。**C** `ExtraSub.origin` 加 `adopted` 一格——`--adopt-residual high` 把探针算出的 `unit → suggest` 写进 `extra_subs` 却硬编码 `'human'`（盘上把夫君那本 4 条 `孙家/孙府/宋家/宋府` 全是这么记的），而那一格的注释本人就预言过「一旦有第二条来源，这一格是唯一能分开的地方」。⚠ **判定上两者同权**（`rn_new_collide_human` 豁免的语义是「有人看过并决定这么做」，而 `--adopt-residual` 是人显式敲的 CLI，不是静默自动路），分开只为审计。**D** 残留探针降噪三件：**D1** 重叠窗口合并（`迟迟未登` 里 `迟` 命中两次、旧实现报两行）——⚠⚠ `baseline[st].residual` 是覆盖率的**分子**，计数留在合并之前，合并的只是呈现行，`rn_residual_stem` 的 detail 同批改成把「处数」与「候选行数」两个数**都**读出来；**D2** `suggest` 只在 `high` 时生成，理由不是启发式而是取值域推出来的——`RESIDUAL_ADOPTION_CONFIDENCE` 只有 `high`、`--adopt-residual` 也只读 high ⇒ low 那一侧的 `suggest` **结构上没有消费者**，实测某本 16 条 low 全是普通词却产出「苏钝」「柳糕」「柳眼」；**D3** 新增第三个结构信号 `RESIDUAL_INTERRUPT_MARKS`（旧姓 + 成串中断号 ⇒ 被打断的称呼），⚠ **只认 `…{2,}`/`—{2,}`，绝不认终止标点**——同一本书 ch18「迟……」是真漏网（最后靠人读正文才补上）而 ch8「高岭之花。」必须留在 low，两条正反例已钉成 fixture；⚠ **证据 n=1**，样本量写进了判据 note 与常量注释（`docs/LESSONS.md` 第 17 条）。**E**（**形状与原计划不同，见下**）新增 `R-10.20` / `rn_upstream_stale`（info）。**⚠⚠ 本批推翻了一条契约自述**：`NameMapProvenance` 的 docstring 写着「`inputs` 四条边今天一处都没在查」，**那是错的**——盘上产物实证 `_provenance.inputs` 一直记着六条边的 `file`+`consumed` 双指纹（`stage2/global_grounding.json` 在内），`ArtifactStore.freshness` 也一直在比。真正的洞更窄也更准：那次比对**只发生在 `stage7.assemble`**，`_judge`（`--check-only` 与落盘闸共用）从头到尾没调用过它 ⇒ 复检报告里唯一与来源有关的那行是 `sources.writer_context.state`，而它**落盘那一刻由构造保证是 `fresh`**。**代价是顺序**：人在 `stage7.approve` 拍板，approve 在 assemble **之前** ⇒ 今天一个人可以给「照旧人物表算出来的映射」签字，直到组装才被拒。⇒ 原计划的 `rename_request_sha1` **不做**（那会成为第三套新鲜度机制，而 `_context_source` 的注释已把「两套并存」登记为债），改成把现成结论搬到复检也看得见；判定共用新的 `ArtifactStore.freshness_of_inputs`（同一算法开两个入口，区别只在 `recorded` 从哪来——`_judge` 也判**还没落盘**的重建产物，读盘会判成上一份），**不另起第二把尺子**。⚠ info 不是 hard：hard 会把 `--recheck` / `--adopt-residual` 这些 0 成本恢复路一起拦死，而 assemble 那道真闸一步没松。**形状破坏**：`stage7/name_map.json` v1→v2（A 的 validator 让带 `''` 的存量读不进来；C 是 Literal **加**格，旧值 `human` 仍合法，不构成破坏），重建人登记在 `tests/test_artifact_versions.py::_SHAPE_BREAKS`。⚠⚠ **存量代价不是零——这一条在合并 main 时被实测推翻，原文写的是「零」。**分支落地时 `outputs/` 确实 8 本全停在 stage1；而合并 main 之后盘上只剩 **1 本**（`偶像公寓打工日常_2066575217393144601`），它**走完了 stage0~stage7**、已人工定稿、已交付 `_rewriting.txt`（22044 字 / 18 章），`name_map.json` 是 v1 ⇒ 升版后它读不进来，**须付费重跑 `stage7.rename` 并重新定稿**（⚠ `--apply-edit` 进不去：`run_apply_edit` 先 `store.read_own` 读旧决策集，版本闸就拒在那一步）。两条减轻情节：它的 `RULES` 指纹在本批之前**就已经 stale**（`rn_derived_alias_candidate` 落地晚于那次跑），而本批改了 `RENAME_SYS` 字节 ⇒ 它要变 current 本来就得付费重跑一次。⚠ `outputs/_ab/flash/` 那两份 v1 快照不在此列——已由同表 `stage6/hook.json` 那一条退役为**只读历史实验证据**，且本批之前就读不进来。⚠ **因此原计划的「升版前先取 v1 语料基线」这一步作废**（那两本连 pytest collect 都过不去），验证重心全部落在合成 fixture 上，字面一律取自盘上实况。**顺带销账**：`docs/REQUIREMENTS.md` **整条 TODO-31 删除**——它此刻只剩 stage7 那一项（其余各项已由 2026-08-23 那行结案），而那一项是一次误判——`RESIDUAL_HIGH_STEMS` 里本来就有 `家/府/氏/宅/门`，那 35 条一直被标 high 且带着算好的 `suggest`，`--adopt-residual high` 是现成的 0 成本命令，书2 那 35 条正是被它一条命令清掉的；⚠ 而且**不该**改成自动，`residual_scan` 的注释记着反例「不允**许家**人」会被写成「不允苏家人」。**判据表 34 → 35 条**（15 hard、**10** info、3 metric、7 unver；owner `stage7=20`/`None=10`/`stage2=3`/`stage5=2`），`R-10` 节 32 → 33 条，两处计数快照与 §五 反向覆盖率同批对齐。⚠ 本行是**先落分支、后合并 main 的 70 个提交**：合并时 main 已独立给本层加了 `rn_derived_alias_candidate`（`R-10.18`，派生称谓探针）与 `unclaimed_residual_keys`，两边都是加法、逐条共存，计数按合并后的注册表实算。**验证**（合并 main 的 70 个提交之后重跑）：后端全量非 corpus `pytest` **2411 passed / 0 failed / 0 error / 3 skipped**（531.8s，`--basetemp=E:/_tr`，`--junitxml` 留档）；⚠ 那 3 条 skip **不是本批引入的**——全是语料依赖（`tests/test_cli.py` 的两条 stage2 复核模板 + `R-2.15` 派单闸），在 **main 上逐条复跑同样 skip**，根因是盘上只剩 1 本书。前端 `typecheck` 0 · `vitest` 26 文件 79 用例全过 · `seams` 104 文件通过且 mocks 20 份基线未动 · `build` 0。⚠ **本批一分钱没花，也没有任何付费或真实 LLM 路径的实证**——prompt 字节确实变了（B 改了 `RENAME_SYS` 与下发面），故**存量 stage7 产物的 prompt 指纹会 stale**，而盘上零存量 ⇒ 无实际影响；换模型/换臂的实测留给 stage7 收尾那一批。 |

| 2026-08-22 | 「答案流丢失」从 `parse` 里劈出来：独立闸名 `answer_lost` + 整份留证落盘（**0 成本，判定式一行未动**） | 起因是一次复盘挡在了账面上：flash 在 `high` 档会把整段答案写进 `reasoning_content`、`content` 返空且 `finish_reason=stop`（**非截断**），而 `ask_json` 的空正文照旧走 `json.loads('')` 必然抛 ⇒ 与「JSON 被截断」记成同一个 `gate: parse`。四份账本（`_service` / `_ab/flash` / `_b1` / `_b2`）752 次 wire `ok` 调用，按 `thinking=True` + `finish=stop` + 正文 0 token 统计：**flash@high 60/418（14%，五个节点都有：plan 33% / blueprint 20% / write 8% / style 7.5%）· flash@low 0/70 · pro@high 0/249 · pro@low 0/6**；每一行的供应商 `usage` 自称 `reasoning_tokens == completion_tokens`、`response_sha256` 恒为空串的 sha（两个口径在有 sha 的 44 行上 9/9 一致）⇒ 空 `content` 是**服务端切出来的**，不是我们解析丢的。体积：60 次空响应总 token ÷ 同 purpose 有正文响应的正文中位数**中位 1.06、48/60 ≤1.3×**（`stage4.plan` 那 5 次是 9 208~11 241，成功正文 9 258~11 665）⇒ 不是「想很久然后放弃」，是**不推敲、整份答案写进了思考通道**。⚠ 参数逐项对照官方文档全部合法（`reasoning_effort` 取值 · `extra_body.thinking` · 思考模式下的 JSON 模式 · `max_tokens=96000 ≤ 384K` · 思考模式下没传 `temperature`），且 `pro` 255 次零空响应 ⇒ 代码 bug 与参数错都被排除。**落成形状**：`REJECT_GATES` 增 `answer_lost`（正文空**且** `finish_reason != 'length'`；空**且**截断仍归 `parse` / `empty_text`），`ask_json` 在 `json.loads` **之前**先判空（而不是事后按 `finish_reason` 改写闸名——那样 `parse` 的含义会取决于读的人记不记得改写规则）；整份响应落一份留证 `<书目录>/_answer_lost/<attempt_id>.json`（含供应商原样 `usage` 与全文 `reasoning`），指针与长度读数进 `llm_audit.jsonl` 新行 `kind=answer_lost`——**token 数不抄**（归成本账同 `attempt_id` 那一行）、**正文不进账**。留证跟**书**走不跟服务走：那份 `reasoning` 装的是这本书的正文，删书即删证据。写盘收进 `novel/llm/keepsake.py` **单独一个模块**并加进 `tests/test_layering.py::_WRITE_ALLOWED`——那张表按**文件**豁免，把 `llm/client.py` 整个放进去等于给全仓最长、改动最频繁的模块开永久后门。⚠⚠ **判定式一行没动**：`_worth_resampling` 仍只看 `finish_reason`，重采与降档的走法与从前逐字相同（`tests/test_llm.py::test_an_empty_body_is_answer_lost_only_when_it_is_not_a_truncation` 拿**走过的档位序列**钉住这一条，不是拿闸名钉的）。⚠ 留证写不成**不抛**（盘满 / Windows `MAX_PATH` / 权限）：诊断不该弄死一次已经付过钱的调用，只让证据缺席，失败原因记进 `dump_error`；吞的只有 `OSError`。⚠ 这一行**没有把根因定死**：`response_format=json_object` 是否共谋待一次 0 改动对照（`docs/REQUIREMENTS.md` TODO-40，判据已预登记）。**验证：全量非 corpus `pytest -q` exit 0 + `npm run seams` 通过；本行没有任何付费实证** |
| 2026-08-22 | `json_object` 0 改动对照 + 「答案流丢失」机制直证（**11 次实付 wire，同场次 A/B；主结论方向对但 n 不够，机制那条坐实**） | 同一本书（无情道 / `outputs/_b2`）、同一节点 `stage4.plan`、`flash` / `high` / `thinking` / `max_tokens=96000`，两臂请求指纹**只差 `json_mode` 一位**：`on`=`c61c671c559fa4a852ef1f89fc126dde`（**与批 3 那 4 次全空的请求逐字相同**）、`off`=`0f5529c170b4245672ea989bdfa60a10`；工具 `tools/json_mode_control.py`，原始报告 `outputs/_b2/_service/json_mode_control_20260822.json`，11 次 wire 全部记在同目录 `ledger.jsonl`（`purpose=stage4.plan:json_mode_control`）。**读数**：`off` n=4 —— 空正文 **0/4**、解析得动 JSON 4/4、合 `PlanResponse` 3/4、completion 201 083；`on` n=7 —— 空正文 **3/7**、解析得动 4/7、合契约 2/7、completion 136 427。Fisher 单侧 **p≈0.21 ⇒ 方向对但没到显著**。**① 机制坐实（本行最值钱的一条）**：批 1 落地的留证第一次在真实空响应上落盘，3/3 份里推理通道那段**整体就是一份合法 JSON**——23 章齐全、括号配平、**一个字推敲过程都没混进去**，其中 1 份**完全合 `PlanResponse` 契约**，也就是一份**可用的计划**被当成「这一档不行」丢掉且付了钱。⇒ `novel/llm/client.py` 模块 docstring ① 与 `transport.py::ChatResponse` 里那句「混着推敲过程、救回来是半成品」**已证伪并同批改口**；⚠ **行为一格未改**——要不要在 `answer_lost` 上开「整体解析得动就采纳」的路是产品语义裁决，留在 `docs/REQUIREMENTS.md` TODO-40。**② 撤回一句旧结论**：批 3「4/4 全空」**没复现**（今天同一份 prompt 是 3/7≈43%）⇒ 此前「`spine` 把空响应率抬到 100%、Fisher p≈0.04」建立在一个不稳的基线上，撤回；`spine` 是不是调节因子现有数据答不了。**③ `json_object` 并不买到 schema 合法**：它只保证语法是 JSON，两臂都在同一种 enum 越界上翻车（`G_TURN` / `R_SCENE` / 多出 `rationale_note`，属 TODO-38 与批 4 前缀化的地盘），而 `off` 臂 4/4 都靠 `_strip_fences` 解析得动 ⇒ 去掉 `response_format` 在「还拿不拿得到 JSON」这一维上没有代价。**④ 换臂省不了钱**：每份可用计划的 completion 两臂几乎相等（67 028 vs 68 214），换的只是失败形态（空响应 ↔ schema 拒），而两者都是同档重采就能救的那一类。⚠ 成本口径要认一句：事前按 10~20k completion 估的「单次约 $0.03」偏低，`off` 臂中位约 55k。**验证：全量非 corpus `pytest -q` exit 0 + `npm run seams` 通过；产物一个字节没动（`plan_chapters.json` mtime 未变），本次只写账本、留证与报告** |
| 2026-08-22 | **`ask_json` 不再发 `response_format=json_object`**（用户裁决；`novel/llm/client.py::JSON_OBJECT_MODE`，0 成本改动，无付费验证） | ⚠ **这是裁决不是实验结论**——同日实验只做到「方向对但 n 不够」（TODO-40：`off` 0/4 空 vs `on` 3/7 空，Fisher 单侧 p≈0.21）。裁决理由是**收益/代价不对称**：① `json_object` **买不到任何我们没有的东西**——它只保证「是合法 JSON」、一格 schema 都不保证（实测两臂都出 `G_TURN` / `R_SCENE` 越界枚举），而「是不是合法 JSON」`ask_json` 本来就自己判（`_strip_fences` 扛围栏 · `json.loads` 扛语法 · `require` 扛空壳 · `model_validate` 扛形状），关掉后 4/4 仍解析得动；② 它**疑似买来一个很贵的失败形态**——官方 JSON Output 文档自己写着这个模式「偶尔返回空 content」，而账上 63/397（15.9%）空正文**全部**出自 flash@high+json_object，pro 0/255、flash@low 0/77；空响应要么白付一次重采，要么把最终产物推到 `low`（已知劣化）。**代价认两条**：① `json_mode` 进 `cache_payload` ⇒ 翻这一位**让既有响应缓存整批作废**（翻的时候主仓 583 份）；② 本仓历史上 `json_mode=False` **只跑过 4 次真调用**（全是 TODO-40 那 4 个样本、同一个节点 `stage4.plan`）⇒ **5/6 个付费节点在这一位上是 0 覆盖**，「plan 上没事」不等于「全链没事」，第一次带它的整跑要盯着看。⇒ 撤回只需把 `JSON_OBJECT_MODE` 改回 `True`：每次调用的 `json_mode` 都进账本与 `_provenance`，两种口径的响应事后分得开。⚠ 同批把 `tests/test_llm.py::_json_req` 与两处审计断言改成**跟着常量走**——写死 `True` 的话，那几条测的就不是「存取同一个缓存键」而是「恒发 json_object」，而后者是可裁决的、前者不是。`novel/stages/stage2/contract.py` 的 `schema_mode` 标签**不变**：它本来说的就是「prompt 约束 + 本地校验，不是 constrained decoding」。**验证：全量非 corpus `pytest -q` exit 0 + `npm run seams` 通过；无付费验证** |
| 2026-08-22 | TODO-38 前半落地：`gate_decision` 记**结构化拒因**`reject_detail`（0 成本，判定式一行未动） | 施工动因是同日现场咬的一口：一次 `stage4.plan` 整跑被 10 分钟超时杀掉，stdout 连同 `why` 一起没了 ⇒ 账上只剩 `gate: schema` 四个字，**两次拒再也查不出拒在哪个字段**。⇒ 能机器数的那一半必须落账：`reject_detail = [{loc, type, count}, …]`，pydantic `ValidationError.errors()` 只取 `loc` + `type`、按 (字段路径, 错误类型) 去重计数，**下标压成 `[]`**（`beats[].role` 而不是 `beats.0.role`，否则答不了「是不是同一种错」，而那是这份摘要唯一的用途）；`require` 记缺的顶层键，`parse` 记 `error`/`pos`/`doc_chars`/`truncated`。⚠⚠ **正文一个字都不进账**：`input` 不取、`msg` 也不取（`literal_error` 的 msg 里带着 `input_value=...`）——这份账与产物同一棵目录树，正文外发边界管的是同一批字节；由一条断言「模型编的那个值不出现在整行 JSON 里」钉住。⚠⚠ **`truncated` 取自 `finish_reason` 而不是从 `pos` 推**——写这条时先写错过一次，被测试当场抓出：`Unterminated string starting at` 的 `pos` 指向字符串**起点**，实测 `{"voice": {"a": 1}, "surface_hab` 是 `pos=20` / `len=32`，最典型的截断会被「pos 贴不贴末尾」的启发式判成「中间坏的」，然后被推去同档重采——而截断是唯一不该重采的那一类。**验证：全量非 corpus `pytest -q` exit 0；无付费验证** |
| 2026-08-22 | 带 `spine` 的首次全书整跑**失败在 `spine` 上**；同批把 `json_object` 改回 `True`（**2 次实付 wire / 59 720 completion，产物未落盘**） | 跑的是 `_b2` 无情道、批 3 的 `PLAN_SYS`。第 1 次 `answer_lost`（`json_mode=False` 下的空正文）→ 同档重采；第 2 次**过了内容闸**（`decision=accepted`、schema 合法），却倒在下游确定性展开 `measure.py::expand_plan_beats`：`RefusedByStructure`，**10 处 `spine` 越界**。**诊断（0 成本，从缓存里那份被接受的响应逐单元核）**：60 个写作单元 **50 对 / 10 错**，而 10 个错的里 **9 个是「本单元只有 1 件事、模型填了 2 或 3」**——prompt 里白纸黑字写着「只有一件事时填 1」。试过「模型按章内连续编号数」这个解释：**只能解释 10 个里的 2 个** ⇒ 不是系统性误读口径，是**数错了**。根因：要知道 `p08_04` 展开后有几件事，模型得在用户消息那张扁平的 `b→p` 映射里扫一遍并计数，而 `groups` 里代码本来就精确知道——正是 `docs/REQUIREMENTS.md` 写的「代码本来就知道、却去问模型」那一类。**反证很干净**：同一份响应的 `plan_beats` 编号引用**一个都没错**（0 处不存在/重复/漏掉）⇒ 让模型**引用**可靠，让它**数数**不可靠。⚠ 另一条独立的洞：`expand_plan_beats` 跑在内容闸**之后** ⇒ `spine` 越界是终态而不是重采触发器，一份其余都合格的计划连同 59 720 token 一起报废（修法候选：接成 `ask_json` 的 `validator=`）。**同批把 `JSON_OBJECT_MODE` 改回 `True`**：关掉它的实证随这两跑蒸发了——`json_mode=False` 照样出空正文（两份留证），累计 `off` **2/9（22%）** vs `on` 3/7（43%），Fisher 单侧 **p≈0.37 完全不显著**，而代价（缓存整批够不着）是确定的 ⇒ 一边是没测出来的收益、一边是确定的代价，维持现状。⚠ **不要连着撤回的两条**：① `json_object` 只保证语法是 JSON、不保证 schema；② 「答案写进推理通道」不是它的产物——5 份留证横跨两臂、5/5 都是完整 JSON。⚠ 测试早已改成跟着 `JSON_OBJECT_MODE` 走，所以这次翻回来**一条断言都不用改**。**验证：全量非 corpus `pytest -q` exit 0** |
| 2026-08-22 | **`stage4.plan` 钉死 `deepseek-v4-pro`**（用户裁决；`NodeSpec.model` 的第二个钉死值，0 成本改动，无付费验证） | 起因是用户追问「其他 stage 的空正文是怎么解决的」，复盘给出的答案是**没有任何一个 stage 单独解决过它**——处理它的是 `novel/llm/client.py` 那三条独立循环（传输重试 / 同档重采 `MAX_SAMPLES_PER_RUNG=2` / 降级阶梯），对全链一视同仁，2026-08-20 加同档重采时 stage2/3/5 是顺带受益的。**读数**（跨 4 份账本 880 行、严格口径=正文空 **且** `finish_reason=stop`；`finish=length` 的 26 次是截断，处置相反不许混算）：`stage4.plan` flash@high **13/30=43.3%** · `stage2.blueprint` 25/109=22.9% · `stage5.write` 8/66=12.1% · `stage3.style` 17/215=7.9%；合计 flash@high **65/433=15.0%** · flash@low **0/79** · pro@high **0/249**。**裁决理由不是「flash 在这里更容易空」，是重采粒度**：审计账里 `stage2.blueprint` 4 次被拒（ch4/9/10/13）**4/4 都在同档第二个样本过关、一次没掉档**，代价 ≈ 5.7k output/章；而 `stage4.plan` 走过 `r0/s1→r0/s2→r1/s1→r1/s2→r2/s1→r2/s2→r0/s1→r0/s2:OK`——整条阶梯走完再从头来，每一次都是整本书。⚠ 43.3% 下双空掉档概率 ≈ 18.7%，而本节点在 `low` 上有已知劣化（猜 event 编号）⇒ 掉档在这里不是安全网。**它不是一次加钱**：flash 产出一份可用计划累计 completion ≈ 67k（TODO-40 同场次两臂 67 028 / 68 214），pro 一次过 out 中位 22 848（n=6）⇒ 按 pro:flash ≈ 3:1 的单价比几乎持平，而 flash 最终稿 10/23 与 8/19 章产自降档档位（pro 对照 0/23）。**落成形状**：`contracts/invariants.py::NODES` 的 `stage4.plan` 加 `model='deepseek-v4-pro'`；接线**一处未动**（`novel/cli/main.py::_llm_client` 早就是 `NODES[ctx.node].model or cfg.model`）；`tests/test_invariants.py` 的「全表唯一钉死值」改成「恰好这两个例外」并同批改名，`tests/test_cli.py` 的接线测试一次断三个节点。⚠ 两个钉死值的**理由不同、不许互相援引**（判官是证据力、`stage4.plan` 是重采粒度），两处注释各写各的。⚠ 预算闸无涉：`MODEL_BUDGETS` 两个模型的 `(context_limit, max_output_tokens)` 同为 `(1 000 000, 384 000)`，`PLAN_MAX_TOKENS=96000` 在两边都合法。⚠ **这是治标**：本节点空正文率最高、失败最贵、全书级判据（`plan_book_band` / `structure_entry_mix`）难守，根因同一个——**全书一次调用这个粒度**；治本是两段式拆分（全书一次只产策略+逐章配额 → 逐章产 `segments`），未施工。**验证：全量非 corpus `pytest -q` exit 0（2278 用例 0 failed / 0 error）；无付费验证** |
| 2026-08-22 | 批 3 收尾两件：`spine` **从下标改成引用**，确定性展开**接上内容闸**（0 成本改动，**无付费实证**；判定式一行未动） | 起因是批 3 两次整跑买回来的两条读数（见上一行）。**(b) 引用**：`PlanBeatResponse.spine` `int(ge=1)` → `str(pattern=BEAT_ID_PATTERN)`，模型填**哪一件事**（一个 `bNN_NN`），编号→下标的换算交给 `expand_plan_beats`（`from_beats.index(...)+1`）⇒ **产物侧 `PlanBeat.spine` / `CtxBeat.spine` / API / 前端一格没动**。依据是那份坏响应里最干净的一条反证：60 个写作单元错 10 个、9 个是「只有 1 件事却填 2/3」，而同一份响应的 `plan_beats` 编号引用 **0 处错** ⇒ 让模型引用可靠、让它数数不可靠。⚠⚠ **代价是推翻一条刻意的下发面约束**：`project_blueprints_for_planner` 从前把 `events` 下发成一串**无编号**文本，理由写着「源 beat 的编号一旦下发，模型就有了『重新编组』的抓手」；现在改成 `{beat_id: 这件事}`。推翻的理由与挡它的闸都写进了那段 docstring：响应侧 `plan_beats[]` 只收 `pNN_NN`（`beat_id` 在 schema 上就填不进去）、`structure_units` 再把「组内拆散」判成 HARD ⇒ 模型即使读到编号也**表达不出**一个 b 级重编组。顺带解掉第二处「猜编号」：`open_hook_goal`/`end_hook_goal` 的 event ref 从前只能去另一张表（`event_units`）里反查，low 档两份响应都倒在这上面（`b07_07` 而本章只有 6 条）——现在编号与正文同处一行，两处都从「数 / 猜」变成「抄」。**(c) 接闸**：`plan.py::_expansion_validator` 把 `adopt_plan_response` + `validate_plan_refs` 挂上 `ask_json(validator=)`（整跑一处、`--chapters` 一处），跑在响应副本上。⚠⚠ 这一条治的是实测那笔账：一份 `decision=accepted`、只错了 10 个 `spine` 的计划从前是**终态**（`expand_plan_beats` 在内容闸之后），连同 59 720 completion 一起报废、坏响应还留在缓存里等着被读回；现在与 schema 拒同型（`gate='validator'`）⇒ evict、同档重采、必要时降档。**归族随之变**：走完阶梯仍不合格是 `LlmFailed`（`TerminalState`，exit 2、不打栈）而不是「结构性拒绝（是 bug，exit 70 + 栈）」——模型填错编号从来不是本仓的 bug；⚠ **写盘那一侧的同两道闸一格没撤**（`--apply-edit` / `--revalidate` 不经过 validator，那一侧的输入是代码派生或人工稿，出现编号错才真的是 bug），两处调的是**同一份代码**。同批把「响应是别的章」那句 `TerminalState` 也搬进 validator：判词一个字没改，但这一类同样**先重采**（`0e2492a` 那条默认处置），代价是越界响应会走满阶梯（测试从 `calls==1` 改成 `calls==6`，承重的两条断言——正式产物零字节改动、不落 `.INVALID`——原样保留）。**两处配套**：① `encode_plan_baseline` 要把 `spine` 连**类型**一起翻回去（下标 → `from_beats[spine-1]`），且**必须跑在 `from_beats` 被换成 `plan_beats` 之前**（`_response_baseline` 调序因此调换）——不然定向修复的基线块会印一个数字给一个只收编号的字段，与那三张前缀表踩同一格、只是错在类型；② `beat_id` 的正则提成 `contracts/schema/stage2.py::BEAT_ID_PATTERN` 一份，两处共用。⚠ `expand_plan_beats` 对**数字** `spine` 不再静默放过（落进同一批 `bad`）：schema 上游已经拦得住，但静默放过等于把旧形状悄悄当成「没做过主次决策」。**验证：全量非 corpus `pytest -q` exit 0（2279 passed / 0 failed / 0 skipped，基线 2271）+ `npm run seams` 通过；0 成本真实路径两条——`--dry-run` 渲染出 `"events":{"b01_01":"…"}`（system 7 473 字，骨架编号使 user 增约 0.4%）、`--check-only` 过 `_b2` 现有计划 exit 0，产物一个字节没动。⚠ 本行没有任何付费实证：`spine` 到底会不会因此填对，要下一次整跑才知道** |
| 2026-08-22 | **批 3 整跑验收通过**（1 次实付 wire / 25 621 completion / exit 0 / HARD 全绿 / 产物落盘）—— 引用式 `spine` 一次填对，`TODO-39` 拿到新分布 | 盘面 `_b2` 无情道、缓存 `E:\_cache_b2`、`deepseek-v4-pro` @ `high`（`3aa903b` 钉死后的第一次整跑）。**① `spine` 一次过**：56 个写作单元全部带 `spine`，内容闸 `decision=accepted` / `gate=null` / `sample=1`——**一次拒绝都没有**。最关键那一格：**只有一件事的 6 个单元 100% 填 1**（上一次同一格 60 单元错 10、其中 9 个正是「只有 1 件事却填 2/3」）。⚠⚠ **这一条的功劳分不清是引用式还是换臂**（`flash→pro` 同时发生）；但「只有一件事却填 2/3」这一类在引用式下**结构上不可能发生**（编号只能从本单元的 `events{}` 里抄），那一半与模型无关。**② 模型真在做主次判断**：`spine` 非 1 的占 **33/56（59%）**，取值分布 `{1:23, 2:18, 3:10, 4:2, 5:3}`，单元大小分布 `{1件:6, 2件:23, 3件:17, 4件:6, 5件:4}`；多件事的 50 个单元里 **33 个挑了后面那件**（不是省力地恒取第一条）。⚠ 对照批 3 那两份 `low` 档响应（43% 非 1 / 6% 非 1）——`low` 的两个极端在 `pro@high` 上都没出现。**③ `TODO-39` 的新分布（本次整跑要回收的读数）**：`axis_reshape` **10/13**（批 3 之前 7/13）、换项反事实会红 **3 章 `[10, 11, 22]`**（之前 6 章 `[3,6,11,13,19,22]`）、判不动 **0**、现役 `axis_group` 仍 **13/13**⇒ **换项的代价降了一半**，裁决条件比上一版好。⚠⚠ **但归因不成立**：`spine` / 换臂 / prompt 三个自变量一起动了，不能说「是 `spine` 把它抬上去的」。**④ 「`spine` 让模型给主干拍加预算吗」= 没有**：挑第一件的单元每件事平均 **76 字**、挑后面的 **77 字** ⇒ 主次与预算是两个各自独立的决策，模型没有把它们绑在一起。**⑤ 一条没预料到的回吐**（与批 2 的验收尺子同口径，从 `writer_context` 的 `CtxBeat` 算）：章级倍率极差 **0.357 → 0.209**、`≤0.85 / ≥1.15` 的章 **1 / 3 → 0 / 0**、全书 1.027× → 0.993×、章内最长拍/最短拍中位 1.57 → 1.42、`flat_chapter` 命中 **14/22 → 16/19 章** ⇒ **批 2 花三轮实付买到的那点章间轻重，在 `pro` 上回吐了一部分**（写作单元也从 62 降到 56）。⚠ 同样是三个自变量一起动，但这条值得单独跟：批 2 的验收线（至少一章 ≤0.85、至少一章 ≥1.15）**这一版两端都没有**，只是没冲出 HARD 的带而已。⚠ 命中的 2 条都是 INFO：`beat_size`（章 18/22/23 有 >300 字的拍）、`flat_chapter`（10 章各拍几乎一样平）。**⑥ (c) 这一跑没被验证**：`validator` 零触发（没有任何一次拒绝），它是保险不是本次的功臣——要等下一次真出拒绝才看得到同档重采。**验证：`run stage4.plan --yes` exit 0，报告 `ok 30 · 命中 2 · 够不着 0 · 没跑成 0`；`plan_chapters.json` + `writer_context.json` 双双落盘（旧那份已备份）；全程 stdout 落 `E:\_tr\plan_run.out`** |
| 2026-08-22 | 空正文两条改动：`insufficient_system_resource` **分流到传输层重试** + TODO-40 落地 **`answer_lost` 过得了同一套闸就采纳**（用户裁决；0 成本改动，无付费验证） | 起因是用户带来一份 DeepSeek V4 空响应处置材料。**逐条对着本仓代码与 880 行账本核完，大部分不适用**：本仓非流式（`transport.py` `stream: False`）、不带 `tools`、`messages()` 恒 2 条无历史 ⇒ 材料里「流式分别累计 delta」「空 assistant 不写进历史」「带 tools 时回传 `reasoning_content`」「连续空时重建会话」四条整块落空；而「`length` 降 effort 腾预算」「临时降档/关思考」本仓 `LADDER` 早就是这个逻辑。⚠ **材料引的现象与本仓的不是同一个**：那边是 `completion_tokens=0`（零内容完成），本仓 880 行里 `completion_tokens==0` **出现 0 次**——空正文那些行是 9 208~11 241，全部记在 reasoning 上 ⇒ 模型干了活也写完了答案，「当瞬时错误重试」在这里是**为一份已经写好的答案再付一次钱**。**两条真正有用的落成形状**：① `RESOURCE_INTERRUPTED = 'insufficient_system_resource'`——它归**传输层重试**（退避、不消耗 `MAX_SAMPLES_PER_RUNG`、不推向降档、内容闸看不到），落 `_send_attempts` 的成功分支；账上仍记一行 wire（`usage` 是真的、钱花了），靠既有的 `finish_reason` 列分辨，**不新增账本字段**；额度用尽抛 `LlmFailed`（`TerminalState` ⇒ exit 2）且报错里说明**已经计费**——与连接故障那种「没到达模型、不计费」是两回事。⚠ 本仓账本里它**出现 0 次**（`finish_reason` 只有 `stop` 814 / `length` 30 / `None`+connection 36）⇒ 这一格是**照文档预备的**，理由是代价不对称：判成 `answer_lost` 的话两次服务端抖动就能把付费节点推进 `low`（`stage4.plan` 在 low 上有已知劣化）。② `SALVAGE_ANSWER_FROM_REASONING = True`——`content` 空而 `reasoning` 那段**整体过得了同一套四道闸**（`json.loads` → `require` → `model_validate` → 调用点 `validator`，与正常路径共用同一个 `gate_payload` 闭包）时**采纳**，不再重采。依据是留证 5/5 份横跨两个 `json_mode` 臂：推理通道那段整体就是合法 JSON、一个字推敲过程都没混进去，其中 1 份完全合 `PlanResponse` 契约——那一次丢掉的是一份**可用的计划**且又付了一次钱。⚠ **落地比 TODO-40 预登记的判据更严**（预登记是「`json.loads` + `model_validate` 双过」）：中途稿过不了 `require` 与 `model_validate`，⇒ 风险面靠闸挡住、不靠 n。**三条边界**：只在 `ask_json`（`ask_text` 没有等价的客观闸，结构上不给它这条路）· 只在 `_answer_lost` 为真时（截断那一半的 reasoning 自己也是半截的）· 必须四道闸全过。**两条认下来的代价**：捞回来的那份仍进不了缓存（`ResponseCache.put` 拒写空正文是对的，塞回 `content` 再存等于改写供应商响应、与 `response_sha256` 分家）⇒ 省的是本次的重采不是下次重跑的钱；以及账上必须分得出来 ⇒ `gate_decision` 与 `answer_lost` 两种审计行都加 `salvaged`，留证照写不误（捞成功时它是**被采纳产物的来源**）。⚠⚠ **判定式一行没动**：`_answer_lost` 与 `_worth_resampling` 与从前逐字相同 ⇒ 捞不动时的走法（同档重采 → 降档）完全不变，由现存那条参数化测试原样守着。⚠ 紧迫性认一句：同日 `stage4.plan` 已钉 pro（pro 全表 `answer_lost` 0/249）⇒ 这条路现在的受益者是仍跑 flash 的 stage2/3/5，真实收益要等下一次 flash 整跑才有读数。**验证：全量非 corpus `pytest -q` exit 0（2285 用例 0 failed / 0 error，新增 6 条：资源中断不碰采样预算 · 中断到底是终态且说已计费 · 捞成功不重采 · 四道闸全过才算 · 截断与 `ask_text` 都不捞 · 两种审计行带 `salvaged` 且捞回来的不进缓存）+ `npm run seams` 通过；无付费验证** |
| 2026-08-22 | **批 4：`stage4.plan` 的响应契约两处收窄** —— `plan_beats[]` → `end_beat`、`budget_chars` → 两级档位（**0 成本改动，尚无付费验收**；判定式一行未动，一条判据都没撤） | 两处收窄各自把一批 HARD 从「模型的责任」搬成**代码的后置条件**，理由都是同一条：**代码本来就精确知道的事，不要去问模型**。① **切分**：模型从前要逐条列出本写作单元用到的每个源内容单元编号、全章恰好覆盖一次；现在只交本单元的**结束拍** `end_beat`，切分由排序推出、起点 = 上一个切点 +1 ⇒ `beat_coverage` / `beat_ref` / `structure_units` **按构造成立**，模型只剩三件便宜的事（编号是本章的 · 互不重复 · 最大的落在本章最后一个源内容单元上）。⚠⚠ **「重排」一格没丢**：`segments[]` 的数组序仍然是成稿序 ⇒ 入口移位、`cold_open`、现场层接缝上的 `earlier`/`return` 全都照样表达得出来，`R-5.6` 的三根轴一根不少。**丢掉的只有「把不相邻的两个源单元并进同一个写作单元」**——0 成本读数（盘上 5 份计划，`beats.group_beats` 复算）：272 个写作单元里只有 **2 个**用过它（0.7%），而**重排 9/94 章真的用过**，所以那一头不能砍。② **篇幅**：模型从前要给五六十个整数并同时满足逐章带与全书带；现在给章级 `weight`（`CH_MAJOR`…`CH_MINOR`）与单元级 `weight`（`W_PEAK`…`W_BRUSH`），整数由 `measure.allocate_budgets` 算（归一化到全书源字数 → 钳进 `PLAN_BUDGET_BAND` → 逐字下限重分配 → 最大余数取整）⇒ `budget_int` / `budget_band` / `plan_book_band` 三条成为后置条件，且**全书合计逐字等于源字数**。账：批 2 run1 就是一次全书实付倒在 `budget_band`（ch18 压到 0.442×）。定向修复那一路把本章总量钉死在基线合计上 ⇒ **它再也顶不出全书带**，而从前它可以。⚠ **六条判据一条都不撤**：`--apply-edit` 的人工稿与 `--revalidate` 的存量产物不经过那两个函数，在那两条路上它们仍然是真守卫。⚠ **不升 `current_version`**：`PlanBeat.from_beats` / `budget_chars` 的类型与含义都没变，变的是生产路径与响应契约；两个新增的 `weight` 是带默认值的加法 ⇒ 存量产物照样读得进来（`_SHAPE_BREAKS` 的语义是「存量读不进来」）。⚠ 同批**结案不做** TODO-31 里的 `cold_open` 代码推导与 `FactRef` 自动补尾单元：`goal_landing` 在批 3 验收整跑上 0/46、入口移位 0/23 ⇒ 两项杠杆已归零，而后者还要把一条 HARD 换成一次静默自动改写。⚠⚠ **本行没有付费实证**：`PLAN_SYS` 指纹已变 ⇒ 盘上那份计划只能整跑重来，不能按章 `--chapters` 修。验收读数照批 2/3 那三样：章级倍率极差与两端章数 · 写作单元数与分组重画 · `structure_reshaped` 的 `axis_reshape` 分布。⚠ **0 成本端到端探针**（真书 23 章 / 90 源单元，合成一份新契约响应走生产入口再跑整张判据表）：**HARD 全绿** · 章级倍率 0.826~1.162（极差 0.335，两端章 4/5）· 全书 **1.0000×**。它证明的是机器跑得通、后置条件成立，**不证明模型会把档位填得好**——那要等整跑。⚠⚠ 同一份探针带出一条**给 `TODO-39` 的坏消息**：`W_PEAK`(2.6) 与 `W_BRUSH`(0.38) 都在 `PLAN_BUDGET_BAND` 之外 ⇒ 一章里用过一次非 `W_MID` 档位，`axis_reshape` 就成立，探针上是 **13/13**（批 3 整跑是 10/13）。那条候选轴很可能**变成第二个恒真项** ⇒ 换项裁决要拿批 4 的整跑分布重算，别用批 3 那张表。 |
| 2026-08-22 | **批 4 整跑验收通过**（1 次实付 wire / 24 667 completion / exit 0 / HARD 全绿 / 产物落盘）—— 两级档位模型真的用起来了，章内轻重大幅改善，代价是重头拍变大 | `pro@high`，base `outputs/_b4`（`_b2` 的副本，**批 3 那份成品一格没动**），缓存 `E:\_cache_b4`。`ok 30 · 命中 2 · 够不着 0 · 没跑成 0`，命中的两条都是 INFO。**sample 1 直接 accepted，零重采**——那三条引用/覆盖 HARD 与三条算术 HARD 在生成路上一次都没有机会红，这正是它们变成后置条件的意思。**① 档位真的花出去了**（这是本批最该怕的失败模式：全填默认 ⇒ 买的自由度等于没花）：章级五档全用上（`major` 2 · `above` 7 · `even` 10 · `below` 3 · `minor` 1，**13/23 章非 even**）；单元级用了四档（`peak` 13 · `strong` 22 · `mid` 16 · `quick` 4 · **`brush` 0**）。**② 章间轻重追回了批 3 的回吐**：章级倍率极差 **0.209 → 0.324**（批 2 run3 是 0.357）、≤0.85 的章 **0 → 1**；⚠ 但 **≥1.15 的章仍然是 0**（max 1.129）⇒ 批 2 那条验收线只过了下端，上端两批都没有。**③ 全书 1.0000×**（代码保证，不再是模型配平出来的）。**④ 章内轻重是本批最大的赢面**：`flat_chapter` **10 章 → 2 章**。⚠⚠ **对价明确**：`beat_size` **3 章 → 9 章**有 >300 字的拍，拍预算 max **480 → 727**、>300 的拍 3/56 → 10/55。根因是 `W_PEAK` 那 2.6 倍乘在一个本来就不小的单元上，而模型看不见乘完之后的字数。两条都是 INFO，没有挡落盘；要不要收窄顶档（2.6 → 2.0）或给单元预算加一道上界，是一次**要标定**的裁决，本批不动。**⑤ 重排那条被保下来的自由度这一跑没被用**（入口移位 0/13，同批 3 的 0/23）；但 `cold_open` 出现 5/23 章而入口一次都没移 ⇒ **那 5 个标签与结构对不上**，现有判据只查反方向（移了入口必须标 cold_open），这一格没人查，登记为观测缺口。**⑥ 写作单元 56 → 55**，`writer_context` 同批重建、`context_link` 对得上。**⑦ 成本**：completion 24 667 vs 批 3 的 25 621（−3.7%），prompt 28 985 vs 28 554（+1.5%） |
| 2026-08-23 | Stage6 候选配方 A/B：`source_architecture` 退场（**3 次实付**，材料与逐条读数见 `docs/stage6-hook-recipe-ab.md`） | 同书同上游，A 臂 3 条（现役三策略）对 B 臂 9 条（`payoff_preview` ×1 + `scene_punchline` ×2，3 次独立采样、独立缓存目录）。**人工闭卷盲选选出 3 条，全部来自 B 臂，A 臂 0 中选**（含那条 `source_architecture`）⇒ 退场落地，配方改为 `HOOK_RECIPE`（`R-9.24`）。⚠ **只能读成「退场没造成可感知损失」，不能读成「新配方更好」**：n=1 评审、1 本书、三选，12 条里 7 条是 `scene_punchline`（随机三选全中它约 16%、全避开 A 臂约 38%）。⭐ 同批量到 stage6 的**跑间方差基线**：同 prompt 同模型三次 completion **16,035 / 8,220 / 10,085 = 1.95 倍** —— 任何单次 stage6 读数的比较都要先过这一关。⭐ 中选的一条恰是 12 条里**结构距离最高**的（最长逐字 10 字 > A 臂 `source_architecture` 的 9 字）⇒ **贴着原作写不是质量缺陷**，`hook_source_distance` 永远留在度量位、不升成门 |

| 2026-08-23 | **Stage2 默认翻到 `extraction-ir-v2`：逐章边界/细节两相 + 在场者闭集引用**（同书 Pro/Flash 付费 A/B，n=1） | 用户明确授权《无情道第一她来自合欢宗》全文及派生上下文外发 DeepSeek API 并承担费用。严格冻结正文 SHA-256、全书底座与 `high + thinking` 后，旧 `extraction-ir-v1.1` 的 Pro/Flash 分别为 **130/91 拍、168/145 hinge**；Flash 把 effort 提到 max 反而降为 87/105，排除「多给推理档位」这一杠杆。根因是单次响应同时切边界并填十余个字段时 Flash 系统性粗切，且自由 `present_names` 与三态地点给了廉价失败面。v2 每章先在同一响应列 `micro_events`（完整回忆脚手架）再压成 `beats`（止段必须取自 micro-event、两列递增到章尾），第二相只能在冻结边界上填细节；在场者从截止本章可见 `char_id` 闭集选，地点回到字面或 `null`。最终产物仍是现役 `ChapterBlueprints` v2，**不升 artifact version**，旧架构继续登记且定向修复按基线 architecture 选。23 章完整 Flash v2 经 ch17 一次定向重生成后：**126 拍 / 169 hinge / 126/126 span / 98% 覆盖 / 0 HARD**；276 个在场者引用的歧义/名册外/代词/过早均为 0。相对旧 Flash，拍数绝对缺口 39→4（约补回 90%），hinge 缺口 23→1（约补回 96%）。**代价不隐瞒**：真调用 25→43、prompt 158,365→213,880、completion（已含 reasoning）136,431→271,462、墙钟 385.0s→621.0s；它是质量换成本，不是降本。**未达成**：named place 84/126=66.7%，仍低于预登记 81% 底线；冷揭示 secret 1/4 未追平 Pro 0/4；没有成稿盲读；n=1 不得外推。完整配对、smoke 反证与成本表见 `docs/stage2_contract_experiment.md` §十四。默认落点 `contracts/invariants.py::BLUEPRINT_ARCHITECTURE`，回退只改这一格；真实 consumer、定向修复、复检与非 corpus 回归同批验证。 |
| 2026-08-23 | **Stage2 冲突阶段章覆盖在产地关闭；清理旧 Flash 实验尾账（TODO-31/32/33）**（0 成本改动，无付费验证） | `conflict_stages[].chapters` 明确定为逐章显式枚举，新增 `conflict_stage_coverage` HARD：按 Stage1 阅读序章集合校验每章恰好出现一次，缺章、重章、越界都在 `stage2.global` 落盘前进入 `.INVALID`；问题稿可人工补全后走既有 `--revalidate` 0 LLM 提升，或 `--resample` 重采。prompt 同批禁止用 `[3,5]` 省略中间章，Stage4 的唯一区间解释保留为存量防御，不再替当前 producer 定义语义。活动清单同时收口：TODO-31 的 Stage2 v2 已由上一行付费 A/B 验收；`setup_chapters` 只能由揭示章推出上界、推不出真实埋点，现有 `_prune_setups` 已做唯一安全的确定性裁剪；成稿盲读无法把差异单独归因给 Stage2，受约束解码又不被当前供应商支持，均不继续。TODO-32 指向 2026-08-19/20 的非 current-schema 实验臂，不是交付物；为它补终审包或批量人工盖章会制造伪证据，且“最终采纳档位”已由 `artifact_adoption → attempt_id → ledger` 成为一等公民，`stage4.plan` 也已钉 Pro，故删除而不补跑。TODO-33 随本行销账。 |

| 2026-08-23 | **Stage2 reasoning 降耗两轮实验臂 v3/v3.1 工程就绪**（0 成本实现，**无付费实证，默认不翻**） | 目标是收回 v2 为质量增加的第二次逐章 high 推理，同时不撤“先召回再压缩”的完整性脚手架。`extraction-ir-v3` 把 micro-event cue 与最终详细 beats 合在每章一次响应中，动态闸仍关闭式校验两列严格递增到章尾、beat 止段来自 micro-event；cue 编译前丢弃，正式 event 仍只有一份。`extraction-ir-v3.1` 再把 narrator/focalizer 收成截止本章可见 char_id，并让 hinge 对引号、带单位数字、显式因果短句优先选确定性候选 id；物件/隐式因果覆盖不到仍可交 phrase，避免以漏抓换 token。两臂的 response model/compiler/repair projection/能力与显式 `ContractSpec.topology` 一并注册；最终仍编译成现役 `ChapterBlueprints` v2，不升 artifact version，前端枚举夹具同步。零成本回归覆盖每章一次调用、动态闸、精确 SourceSpan、关闭叙述引用、phrase 兜底与 repair round-trip。⚠ 本行没有任何 v3/v3.1 模型响应、reasoning 降幅或成稿证据；付费矩阵与 25% 降幅/95% 质量底线已预注册在 `docs/stage2_contract_experiment.md` §十五；后续结果与最终产品裁决见同文 §十六～§十九。本行当时默认继续 `extraction-ir-v2`。 |
| 2026-08-23 | **Stage2 v2 → v3 → v3.1 付费 A/B：两条降耗臂均否决，默认保持 v2** | 用户明确授权《无情道第一她来自合欢宗》正文与 Stage2 上下文外发 DeepSeek API，三臂按同正文/全书底座 hash、同冻结代码、`deepseek-v4-flash + high + thinking`、独立空缓存串行运行。v2 经两轮定向修复正式落盘：56 wire、370,647 reasoning、127 拍、161 hinge、人物关闭引用四项全 0、0 HARD；v3 经一轮定向修复落盘：37 wire、112,731 reasoning（−69.6%），但 hinge 138（仅 v2 的 85.7%，低于 95% 线），且 6 个在场者歧义、2 个名册外引用 ⇒ 成本线过、一级质量门失败。v3.1 为 25 wire、161,218 reasoning（比 v3 +43.0%），全部调用结束后因空 `SourceSpan.normalized` 在 `ChapterBlueprints.model_validate` 崩溃、exit 70，正式产物未落盘，独立 check exit 2；无 adoption、candidate/phrase 成品读数不可得。三臂合计 118 次真调用、0 cache replay。完整 ledger/audit/产物 hash、冻结身份、质量表与人工抽查见 `docs/stage2_contract_experiment.md` §十五；后续修复、迭代与最终产品裁决见同文 §十六～§十九。 |
| 2026-08-23 | **Stage2 v3.1 候选 20 双开发书整跑失败；reasoning 救回改为同档后备** | 用户授权的两书空缓存开发验收最终均 fresh/match、0 HARD、span 100%、关闭人物引用四项全 0；无情道 133 beat / 141 hinge，偶像 160 / 168。但 41 章合计实际 69 wire、497,452 prompt / 564,994 completion / 490,676 reasoning，两书都需恢复，偶像 ch10 真实粗切；原文对照确认事件召回增强与普通对白 hinge 过抽、ch4 笑点漏标同时存在。故开发门失败，默认继续 `extraction-ir-v2`，盘上 `ChapterBlueprints` 继续 v2，第三本 holdout 不启封。19 次 `answer_lost` 证明 v3.1 全拒合法 reasoning 救回稿会系统性放大成本；响应契约改为正常 `content` 优先、首份过完整 schema/动态 validator 的救回稿作同档后备，第二采仍不可用就采用后备而不是降到 low。其他结构化节点保持合法救回立即采纳；账本仍以 `salvaged` 区分来源。该优化不增 prompt，定向回归通过，但历史空响应不进缓存，尚无付费节省实证。完整成本、人工对照、恢复链与 hash 见 `docs/stage2_contract_experiment.md` §十七；后续双书复验与最终产品裁决见同文 §十九。 |
| 2026-08-23 | **Stage2 `extraction-ir-v2.1` 混合 effort 实验臂工程就绪**（0 成本实现，默认不翻） | 用户把目标转向优化已经翻默认、但成本较高的 v2 两相：保留第一相 `high + thinking` 负责 micro-event 回忆与 beat 边界，冻结边界后的第二相改为 `low + thinking`，书级摘要仍 `high + thinking`。`ContractSpec` 新增相级 rungs，首跑与 `--chapters` 恢复共用同一接线；v2.1 由 v2 `dataclasses.replace` 派生，除 architecture 与三处 rungs 外，system/user、响应 schema、compiler、字段修复能力及最终 `ChapterBlueprints` 全部相同。相级固定单档仍保留同档重采，但不滑到关思考档；模型名不与契约硬绑，本轮实验另冻结 `deepseek-v4-flash`。新增 architecture 是放宽枚举，artifact schema 仍为 v2、不迁移存量；`batch_density` 从 `ContractSpec.topology` 派生，v2.1 仍如实报告两相。付费前的共享边界配对、人工盲读与 25%/15% reasoning、15% 墙钟门已登记在 `docs/stage2_contract_experiment.md` §十八。工程就绪当时尚未获得正文外发/费用授权，故本行只有 0 成本结论；随后获得的授权与付费否决结果见下一行，默认始终未翻。 |
| 2026-08-23 | **Stage2 v2.1 双书付费配对失败：low 细节降耗不稳定且 hinge 召回塌陷** | 用户随后明确授权《无情道第一她来自合欢宗》《偶像公寓打工日常》正文与 Stage2 派生上下文外发 DeepSeek API 并承担费用。冻结代码/输入后串行运行 v1.1 Pro、v2 Flash 与复用 high 边界的 v2.1 Flash 配对臂；v2.1 的完整成本按“v2 high 边界 wire + v2.1 low 细节/high 摘要 wire”重建，不拿缓存命中冒充零成本。两书合计 v1.1 / v2 / v2.1 重建分别为 43/87/87 实付调用、410,652/558,912/503,177 completion、357,859/485,689/424,559 reasoning；v2.1 相对 v2 仅降 10.0% completion、12.6% reasoning，未过总 reasoning −15% 门，且细节相无情道 −48.7%、偶像 +6.2%，不具跨书稳定性。严格同边界章为无情道 20/23、偶像 13/18：hinge 分别 152→105（69.1%）、131→78（59.5%），named-place 比例各降 11.2/11.4pp，均远低于 ≥95% / ≤3pp 门；人工核对确认漏标含因果、关系、承诺与笑点锚点，不只是删普通对白。候选 HARD 变少只说明交出的少量锚点更易校验，不能给召回背书。故按预登记在质量门处停止：不烧独立空缓存 v2.1 速度臂、不启封第三本 holdout，真实 v2.1 墙钟记为未测，默认保持 `extraction-ir-v2`；完整方法、逐书成本/墙钟、失败章与哈希见 `docs/stage2_contract_experiment.md` §十八。 |
| 2026-08-23 | **Stage2 契约面收口为 v1.1 / v2 / v3.1；chapter blueprints schema v2 → v3** | 用户裁定只保留 `extraction-ir-v1.1`、`extraction-ir-v2`、`extraction-ir-v3.1`，默认仍为 v2。`bounded-fidelity-v3`、`extraction-ir-v1`、`extraction-ir-v2.1`、`extraction-ir-v3` 从 `BLUEPRINT_ARCHITECTURES`、`CONTRACTS`、CLI/env 配置校验、冒烟白名单和前端枚举夹具同时撤下；历史实验读数留档但不再是可运行入口。由于 `BlueprintContext.architecture` 的 Literal 从七值收窄为三值，`stage2/chapter_blueprints.json` 属于破坏性 schema 变更，注册版本升为 v3；本仓无迁移机制，所有 v2 信封（即便 architecture 恰属保留三值）、`.INVALID` 与 `_rounds/` 均不能手补冒充当前产地，重建人是 `stage2.blueprint` 付费整跑。清理 v2.1 相级 rungs 时，回归显影并修正摘要前临时候选提前结算 usage 的问题：临时候选只预览来源账，只有真的写成 `.INVALID` 或形成正式稿才结算，ledger 权威总账不受影响。 |
| 2026-08-24 | **评审整改 17 条（0 成本改动，无付费实证）；同批修好一个让`code_version` 恒 `unknown` 的真 bug** | 只记会改变现役判断的那几格。① **付费预览少报了 stage2 的一半以上**：`tools/current_corpus_paid_preview.py` 把 `stage2.blueprint` 写死成每章 1 调，而现役契约（`BLUEPRINT_ARCHITECTURE = extraction-ir-v2`）是**每章两相 + 一次书级摘要批**（`2N+1`）。17+25 章两本因此少报 44 个逻辑请求、264 个 wire 上限——而这份预览的全部用途就是开跑前拿它决定花不花这笔钱。现改为从 `active_contract()` 的 `topology` / `needs_summary_batch` 派生，翻默认会跟着动；期望值 136→180 / 148→192。② **`novel/prov.py::_worktree_digest` 对未跟踪目录直接 `open()`**：`git ls-files --others` 把整块未跟踪的目录（嵌套仓库、链接 worktree）折叠成一条**目录项**，目录 `open` 抛 `PermissionError` ⇒ 摘要 `None` ⇒ `code_version()` 恒 `'unknown'`。**本机实测已发生**（仓根有 `.worktrees/`）：那段时间产出的每一份产物都把 `code` 记成 unknown，而它不报错。目录项改为按名字入摘要。③ 交付终验补两道：`unknown == unknown` 不再读成「同一版代码」（倒向 `unknown`）；终审包不再只查 `is_file()`（空文件、早于成书都倒向 `stopped`）。④ **删除与 agent 准入的原子边界**：审批写入住在服务父进程而目录移动在 CLI 子进程，两条竞态各补一半——`approvals.frozen_for_trash`（防「书已删除、审批复活」）与锁内 `agent.drop_pending`（防「书删完了队列才把它带起来」，已复现）；`agent.start`/`enqueue` 拒绝不在盘上的书（HTTP 404），队列对这种永久失败**出队丢弃**并留 `_service/agent_queue_drops.jsonl`；回收站条目加 `complete` 哨兵 + `sweep_incomplete` 硬中断恢复（带在途名单与 300s 宽限期，不许把正在进行的那次收走）。⑤ **Stage2 四条**：本章可见称呼完全相同的多个 `char_id` 整组不再下发（模型在那一格只能抛硬币，而选错就是一个静默的错误人物身份）；v3.1 的权威叙述身份不再被按歧义名字反解覆盖成 `null`；定向修复的边界基线 `end_para=None` 改为重切边界（原来 `int(None)` ⇒ exit 70，且发生在任何 LLM 调用之前）；定向修复补上首轮那道**摘要批前闸**（原来注定被拦的一轮仍会先白付一次全书摘要）。⑥ 「跳过旧蓝图」由**按异常类型**收窄成**按版本号大小**——`ArtifactVersionError` 有六个抛出点，宽捕获会把缺 marker、非法 marker、坏 envelope 与**比当前还高的版本**一起放过（`_schema_version: 999` 已复现让守卫整个消失）。⑦ Stage6 候选互异闸补到 `--recheck` 与 `--apply-edit --candidate` 两条写盘路（候选身份是**位置**，两个 slot 同字之后判官结论就分不开了）。⑧ 已过完整契约的推理通道救回稿，不再因为「再争取一次正常正文」那一采死在传输层而被丢弃（丢了就要重新付一次，而它连缓存都进不去）。⑨ 前端：未勾付费授权**不等于这一趟不花钱**（agent 本身是一次 headless Claude 会话），单本与批量两个框的文案同批改口；交付失败原因从只挂 `title` 改成可聚焦可展开；作业日志只在已停在底部时才自动跟随；删除请求在途时关不掉框。**验证：全量非 corpus `pytest -q` 0 failed；stage6 那三条新守卫只在 `-m corpus` + `NOVEL_OUTPUT` 下真跑（已单独跑过）；前端 26 文件 / 79 项通过 + typecheck + seams + build。本次没有任何付费实证。** |

| 2026-08-24 | **Stage5 契约收口为单一 canonical 闭卷路径**（0 成本工程改动，无付费/语料实证） | 现役章稿唯一落 `stage5/chNN.json`，默认补齐当前计划全部缺章，`--chapters` 只定向重写；旧双路径目录只读保留并报 `legacy_incompatible`，不原地迁移、不触发付费重建。Stage5 chapter / Review / trim audit / trim resolution 盘上均为 v2；审批盘上为 `_ApprovalRecordV3`、公开 `ApprovalDoc` 保持 v2，旧审批 v2 只可显式 backfill。统一 `review_status` 以 `fresh/stale/unknown/missing/legacy_incompatible` 关闭式核 RULES、provenance、计划章集、judged/正文投影、逐章 freshness 与 canonical `draft.md`；旧结论不再驱动付费返工、wontfix 或审批。现役 Stage5 只有 49 条机械判据：严重度 hard/info/metric/unver = **6/27/11/5**，repair UNIQUE/FREE/NONE = **12/11/26**；`R-7.9` 收窄为人称弱信号 + 章尾字面原子报数，章尾语义恒 `unver`；`dialogue_report` 只逐章。同批以 `material_anchor_unattested(owner=stage2)` 显式报上游锚点错误，并在 UI 同时呈现 `identity_hit` / `identity_dead`，因而关闭 TODO-42；移除 prompt 中恒 null `focalizer` 关闭 TODO-43；旧非 canonical 写作路径与专属 `dialogue_halved` 判据整体退役，关闭 TODO-44。工程结论只到 canonical v2、单臂工作流与守卫可靠性；beat-material 相对旧 event-list 是设计改进，原计划的受控双书付费 A/B 已在开跑前取消，没有实验正文、盲评或 manifest，故不宣称质量、成本或速度胜出。Stage5 又显著受 Stage1～4 上游材料影响，后续全链读数也不能直接归因给这一层。`TODO-21` 仍保留：本行没有生成 current-schema corpus/manifest，也没有跑 `--require-corpus`，不得宣称 corpus 闭合。 |
| 2026-08-24 | **Stage2 全局事实升级为 v3：显式可见性、带时点知情与代码回取证据（TODO-42）** | `Relation.visibility` 收成 `public(from_chapter)` / `via_secret(secret_id)` 判别联合；secret 的无时间 `character_known_by` 换成 CharacterRef/GroupRef + `known_from_chapter` 的 `knowledge_events`，Stage4 只投影截至本章已知主体，旧启发式只由 `relation_visibility_mismatch` INFO 对账。七类自由事实由模型/typed edit 只交“指定章 + 短 locator”，代码按 raw→normalized 在指定章内要求唯一命中并铸成正式 `SourceSpan`；0/多命中与纯标点定位都落 `source=null` + 正面 provenance 失败账，由 `global_evidence_unlocated` HARD 送入可读 `.INVALID`，可经 typed edit / `--revalidate` 零 LLM 恢复。GlobalEdit 同批补齐 role/fate/relation/setting/rule/secret/arc 的 typed 面，并关闭了 INVALID 因“正在替换的 edit 输入变了”而无法恢复的缝；付费 `--purge-role` 仍只改 claim，不索取未授权的原文 locator，旧 evidence 明确失效后走同一恢复闸。`valid_to_chapter` 全栈改名 `valid_through_chapter` 并钉住含端点语义。新增 `canon_chapter_refs_dangling`，Stage2 现役共 **77 条（43 HARD / 16 INFO / 16 metric / 2 unver）**；规则版本、setup、reader reveal 越界与 typed patch 的倒序窗口/重复时点/重复 subject 都在落盘前关闭式拒收。Global review 的身份改为 claim+evidence hash，workpack 直接消费 formal span；Stage4/5/6 的真实 prompt 与 freshness 共用 `without_global_evidence`，不把 SourceSpan 原文或纯 evidence 修订扩散给下游付费节点。形状破坏如实登记：`global_grounding.json` v2→v3，v1/v2 由 `stage2.global` 付费整跑重建；`global_edit.json` v1→v2，旧人工决定必须核正文后重写，不迁移、不猜时点/locator。**合成验证**：Stage2/冻结契约/Stage4～6 联合回归 100% exit 0；完整非 corpus 套件在仅排除一条被 worktree 内 ACL 损坏 `.t` 测试目录阻塞的快照扫描后 100% exit 0，前端 typecheck / seams / 26 文件 80 项通过。**真实付费验收**（同日补齐，用户逐字授权正文外发与一次费用）：在隔离 base `E:\_novel_stage2_v3_validation_20260824` 对《偶像公寓打工日常》运行 `deepseek-v4-flash@high`、thinking=true；ledger 唯一 wire 行 `ok/stop`，prompt **20 439** / completion **39 029** / reasoning **31 528** token。第一次响应成功后暴露“当前 v3 蓝图反向比较时严格读取旧 v1 global、锁死 producer 重建”的通用 bug，修复后以**完全相同请求键**重放本地缓存（账为 1 wire + 1 cached，未发生第二次外发）。模型稿唯一 HARD 为 `role_leaks_fate`，经书内 `GlobalEdit v2` typed role+locator **0 LLM** 修复后正式落 `global_grounding v3`，HARD **0**、INFO 4、UNVER 2；60/60 条自由事实 evidence 均为正式精确 span，workpack unresolved **0**。秘密原子化在生产 Stage4 投影上实测：ch4 仅放 `secret_01`（女装事实），`secret_02`（当偶像的动机）到 ch7 才放，知情主体也随 `known_from_chapter` 递增。全局复核面 115 项中继承 PASS 25、仍 pending 90，**未冒充完成人审**。正式 `outputs` 没有写入：隔离归档的旧 v1 `global_grounding.r3.json` 与正式文件 SHA-256 同为 `74c899019c34f5e721131440739c911a3afe7b5b013cebd3a0843794e656c90c`；全部新稿、ledger 与历史只在隔离副本。⚠ 这是一本文本的一次真实模型实证，不外推为 current-schema corpus 验收；corpus manifest 仍未生成。 |
| 2026-08-24 | **Stage2 v3 收尾：新旧真实产物逐项对读、补齐召回与集合完整性闸（0 LLM）** | 对同一本《偶像公寓打工日常》完整读取旧 v1、初始 v3、正式证据与关键原文章节，不能只看 schema 更丰富就宣称质量上升：初始 v3 虽有 60/60 精确 SourceSpan、显式 relation visibility、带时点 knowledge event 与 6 条世界规则，却漏了旧稿已有的江尽夏→谢秋愿好感、母子关系及 5 条关系/立场 arc，`rel_11` 还把原文 ch10 已成立的家人关系迟标到 ch18，`secret_03` 同条混入“叛逆动机+A大录取”两个事实，而逐条复核只会审已经抽出的行，发现不了整行漏抽。产线因此新增 `character_set_complete` / `relation_set_complete` 两项全书级人工裁决，与既有 world_rules / secrets 合成四道集合反查；空集合也 pending，集合改动重开，纯 evidence 坐标改动不重开，GlobalReview v1 只放宽 kind、不伪造迁移。隔离副本 `E:\_novel_stage2_v3_finalization_20260824` 再经两轮 `GlobalEdit v2` typed edit（均由 operator preview 确认为不花钱）修正关系时点与方向、补齐旧稿有效关系和 arc、把 `secret_03` 收成单一动机事实；最终 old→new 的 characters / relations / world_rules / secrets / arc_tracks 为 **9→10 / 10→17 / 3→6 / 5→5 / 9→10**，旧稿有效关系与弧线不再因换契约丢失，新稿另保留显式时点和证据优势。正式证据 **70/70** 唯一回取，`evidence_locator_failures=0`，77 条 Stage2 报告 HARD 0；workpack **122/122** 项均 `unresolved_formal_evidence=[]` 且 `auto_decisions=false`。复核仍是 PASS 25 / pending 97，**未冒充完成人审或开 s2 gate**；正式 `outputs` 未改。成本/速度不粉饰：唯一新 wire 仍是上一行的 336.722s、20,439 prompt / 39,029 completion / 31,528 reasoning；旧 v1 provenance 仅有一组不同运行条件下的缓存 usage（18,983 / 21,728 / 17,150），不能作配对墙钟，且 token 表面更低，所以本轮只成立“质量提高”，不成立“更快/更省”。收尾 typed edit 没有新增 wire，故修复本身新增 LLM 成本为 0。⚠ 单书实证不外推为 current-schema corpus；现役 manifest 仍未生成。 |
| 2026-08-24 | **`stage7.rename` 的 `max_tokens` 16000 → 64000；同批裁定该节点不降 `effort`**（用户裁决；**0 成本改动，本行本身无付费实证**，但依据是 17 次真实调用的账本读数） | **起因是一次真实截断**：当日那本《偶像公寓打工日常》重跑时，第 1 次 `effort=high` 的 `finish_reason=length`、`completion_tokens=reasoning_tokens=16000` —— **满额预算全被推理吃掉，`content` 一个 token 都没有**，随后由 `LADDER` 落到 `low` 才拿到答案 ⇒ 那一次**交付的人名表其实是 low 档产的**。**账本复算**（`_service/ledger.jsonl` 按 `node == stage7.rename` 过滤，17 次）：high 成功 8 次（completion 均 12,289 · reasoning 均 11,064 · **正文均 1,225**）· high **截断 4 次**（三次 16,000、一次 15,999，正文恒 0）· low 5 次（completion 均 6,932 · reasoning 均 5,729 · **正文均 1,203**）。⇒ **high 档截断率 4/12 = 33%**，且**两档的正文需求量几乎相同**——差别全在推理量。**两条路各自的性质因此完全不同**：① 降 `effort` 到 low 是**降本**，单本期望 completion 从 15,837 → 6,932（**−56%**）；② 抬 `max_tokens` 是**买质量的确定性**，让 high 档大概率不再落梯，代价是真跑飞时浪费从 16k 变 64k。**用户裁定取 ②、明确否决 ①**，理由是唯一可能被降档伤到的维度是「名字起得好不好」，而 `rn_taste_unver` 明说全链没有判据 ⇒ FC-13 那五道门全是机械判据、n=1，**证不了这一维**，「未见劣化」不等于「没有劣化」。⚠ 16000 本来就是全链最低档之一：同量级的 `stage6.hook` 用的就是 **64000**（同模型、已实证），`stage2.blueprint` 256000、`stage4.plan` 96000 ⇒ 这个数更像没调过而不是刻意收紧。**核过四处不会被带坏的落点**：① 预算两道闸都过得去（`novel/llm/budget.py::MODEL_BUDGETS` 的 `max_output_tokens=384_000` / `context_limit=1_000_000`，本节点输入仅约 2.4k）；② `tools/current_corpus_paid_preview.py` 早就 import 生产常量 `RENAME_MAX_TOKENS`，**不是第二落点**；③ `tests/test_prov.py` 里那个 16000 只是 provenance 形状测试的夹具值，不钉生产常量；④ **不升 schema 版本**——`NameMapParams.max_tokens: int` 形状没变，存量产物照样读得进来，且 `params` 不进新鲜度计算 ⇒ 当日刚交付那本书实测仍 `fresh/match`。⚠⚠ **一条必须记的副作用**：`max_tokens` 在缓存键里（`novel/llm/transport.py::ChatRequest.cache_payload`）⇒ 本节点**既有缓存整批不再命中**（旧字节不删，只是够不着），「同模型同参数重跑 0 元」对旧的 stage7 跑法不再成立。⚠ **这是一次有尾部风险的下注，不是无损改进**：4 次截断只证明「想要 ≥16000」，没证明尾巴到底有多长。⇒ **回读约定**（写进了 `MAX_TOKENS` 的注释）：下一次跑书要回看这个数，截断若仍发生，说明是推理跑飞而不是预算不够，那时该动的是 `effort` 而不是继续抬上限。⚠ **这是本行当时的活动状态**：FC-13 只销掉 `stage7.rename`，旧 `stage3.style` 尚待裁决；紧随其后的 Stage3 v2 历史行已补上消融与产品裁决，现役不再有 Stage3 effort 问题。**验证**：`tests/test_stage7*.py` + `test_prov` + `test_llm` + `test_invariants` 0 failed；全量非 corpus `pytest` 见下一次回归。本行**没有新的付费调用**——依据全部来自既有账本。 |

| 2026-08-24 | **Stage3 → Stage6 定性文风双书消融未决；后续产品裁决删除定性包；Stage5 定性消费面独立下线** | 冻结批共 **20 wire / 60 candidates**，candidate retry、cache replay、reuse、invalid 与最终 HARD 均为 **0**；prompt / completion（含 reasoning）为 **650,995 / 152,050** token。盲评聚合 A:B 为 **6:4**，分书 B01 **4:1**、B02 **2:3**，`mean_advantage_a=-0.5`；严重事实错误 **8:8**。两道预登记门均未通过，决策输出 SHA-256 为 `9dac48892c6f1ab36d87f049471611d544183ea858be0f9c6ade4ceeadc57434`。⚠ 人工流程没有严格闭合：用户给出部分偏好/首轮判断，其余由 Codex 补全、交叉复核与归一；arm 定义、映射、计分与阈值没有事后更改，故冻结统计结论仍是未决，不能重解释成统计删除依据。**后续独立产品裁决**采用“B 臂至少一篇事实安全且足以吸引继续阅读即可删除”的门槛；真实 B 臂强认可覆盖 8/10 次运行且事实复核通过，产品负责人据此明确授权删除。现役 Stage3 升 v2 为纯机械免费节点，Stage6 只消费 hook 五叶机械投影。Stage5 则依据既有证据独立收窄为 `stage5_style` 9 叶与 `body_bands` 5 档；`style_block_carries_plot` 随不可达消费面删除；经后续 canonical 收口后的最终注册表为 **49（6 HARD / 27 INFO / 11 metric / 5 unver；repair 12 UNIQUE / 11 FREE / 26 NONE）**。两层结论见 `docs/stage3-stage6-style-ab-result.md`。 |

| 2026-08-24 | **Stage2 blueprint 默认由 v2 翻到 `extraction-ir-v3.1`；v1.1/v2 继续显式可选** | 用户在两本书的同模型、同上游、独立空缓存三臂对照后作出产品裁决。`deepseek-v4-flash + high + thinking` 下，v1.1 / v2 / v3.1 的两书匿名盲审分别为 **22.0→20.2 / 20.0→24.4 / 26.0→25.0**，最终正式产物率 **2/2 / 1/2 / 2/2**，首轮正式率 **0/2 / 0/2 / 1/2**；累计 wire **46 / 97 / 61**、prompt+completion **748,688 / 939,545 / 1,043,290**、付费命令墙钟 **855s / 1,073s / 958s**。因此 v3.1 取质量与可交付可靠性，v1.1 保留为显式低 token 臂，v2 保留为显式回退/存量复检臂。⚠ 本次没有跑预登记的第三本 holdout，旧开发门也没有被重写成通过；第二书 v3.1 与 v2 共同发生 ch6 实体串线且机械 HARD 未检出，因此该裁决不等于可跳过语义复核。唯一默认落点改为 `contracts/invariants.py::BLUEPRINT_ARCHITECTURE`；配置、整跑、预算预览与 provenance 从生产落点派生，默认预览拓扑由两相 `2N+1` 变为一相 `N+1`。取值面仍为 v1.1/v2/v3.1，`ChapterBlueprints` schema 仍 v3，不迁移、不自动重建存量书；本次切换本身 0 LLM。完整逐书证据与限制见 `docs/stage2_contract_experiment.md` §十九。工程验证：Stage2 定向 147 项、冻结契约五文件 325 项与完整非 corpus 回归均 exit 0；前端 seams（104 文件/20 份 mock）、typecheck、build 均 exit 0；当前仍无 corpus manifest，不称 corpus 全量绿。 |
| 2026-08-24 | **Stage2 blueprint 退役 `extraction-ir-v1.1`；契约面收为 v2 / v3.1，chapter blueprints schema v3 → v4** | 用户明确裁决删除 v1.1，仅保留 `extraction-ir-v2` 与默认 `extraction-ir-v3.1`。v1.1 从 `BLUEPRINT_ARCHITECTURES`、`CONTRACTS`、CLI/env 配置校验、响应载体与 prompt 导出、冒烟白名单和前端枚举夹具同时撤下；v1.1 专用的旧蓝图重放工具与 corpus 守卫删除，历史 A/B 数据保留。由于 `BlueprintContext.architecture` 从三值收窄为两值，`stage2/chapter_blueprints.json` 形状破坏并升 v4；本仓无迁移机制，所有 v3 信封、`.INVALID` 与 `_rounds/` 都由 `stage2.blueprint` 付费整跑重建，不手补版本或 architecture 冒充当前产地。本批工程改动 0 LLM；实验证据与后续裁决见 `docs/stage2_contract_experiment.md` §二十。 |

| 2026-08-24 | **取消异常驱动的 effort/thinking 降级，生产恢复链改为同配置 Flash→Pro 兜底**（用户裁决；**0 成本实现，无真实 LLM/付费实证**） | **先纠正旧行为口径**：传输层默认不是“失败后重试两次”，而是首发 + **3** 次 retry（只对 429/5xx/timeout 等 retryable 故障）；内容层除截断外是同一配置首发 + **1** 次重采；旧实现随后才走 `high→low→low/off`。本批保留前两层、整批删除异常驱动的 effort/thinking 降级：默认与显式 effort 都只形成一组配置，多组 `rungs=` 在缓存/预算/wire 前关闭式拒绝，防止旧降级借参数复活。主模型为 `deepseek-v4-flash` 时，内容采样耗尽或 retryable 传输/`insufficient_system_resource` 重试耗尽后，才由 `deepseek-v4-pro` 保持**同 effort / thinking / prompt / schema / validator / max_tokens**兜底；Pro 内容或传输仍失败统一 `LlmFailed`。主模型本来就是 Pro 时不重复兜底；不可重试 4xx、配置/API key 错误与上下文预算拒绝不花 Pro 调用。Flash 与 Pro 的完整请求在碰缓存/ledger/wire 前一起做预算预检；缓存先横扫两模型同配置，已有合格 Pro 缓存可以 0 wire 恢复。`tools/json_mode_control.py` / `tools/observe_provider_limits.py` 明确关闭模型兜底，避免诊断变量被替换。**来源账闭环**：既有 `_provenance.model` 保留“主模型配置”这条 A/B 轴；新增 additive、旧产物可缺的 `adopted_models`，由 accepted attempt → ledger 精确反查真正进入产物的 Flash/Pro 集合，现役 producer 拿不到联结即拒绝落盘；局部修复合并旧集合，Stage5 每章绑定自己的 accepted purpose，避免 Pro 产物被静默标成 Flash。前端 Stage5 显示实际采纳模型。**验证**：全量非 corpus **2673 passed / 0 failed / 0 error / 3 skipped**（406.347s；3 条均为既有语料依赖，默认 corpus 仍被排除，故不称全量语料绿）；冻结契约五文件与模型/ledger/各 stage 生产落盘回归通过；前端 typecheck 0、seams 104 文件通过且 mocks 20 份基线未动、Vitest **27 文件 / 86 用例**全过、production build 通过。⚠ 全部 LLM 场景使用 FakeTransport/合成故障，**没有真实 DeepSeek 调用，尚不能把“Pro 真实稳定性”写成这一批的实测结论**。 |

| 2026-08-25 | **受托响应恢复从独立重采改为显式消费失败证据**（用户裁决；**0 成本实现，无真实 LLM/付费实证**） | 采样数、模型顺序与 agent 每响应一次额度均不增加，但后继请求不再失忆：Flash 第二采以最新供应商/封存 agent 候选 + 本地失败详情定向修复；Pro 首采读取全部 wire 失败史并约束重建，避免被 Flash 局部解锚定；Pro 第二采针对自己的最新失败再定向修复。`llm-response-recovery-v1` 把候选标为不可信数据，要求只交完整 JSON；原 `json.loads → require → model_validate → validator` 一道不减。运行时精确预算按 full / diagnostic / minimal 逐档选择，全部装不下才回退已经预检的原请求，不截正文、不降 effort/thinking/max_tokens；付费预览因此只把首发 input 叫精确数，完整链动态 input 改报 unknown + context 硬上限。动态 prompt 进入缓存键与 request fingerprint，拒绝状态按不可变前序重建 fingerprint，跨 CLI 来源账不信盘上自报；`gate_decision` 新增 `request_mode` / `recovery_from_attempts`。普通 CLI 不带 `--response-repair agent` 时仍四次原 prompt 独立采样。合成回归覆盖 agent 候选进入下一采、三种策略顺序、预算降级、普通 CLI 不变及 LLM 审计/预算/客户端基础面；未发真实 DeepSeek，实际修复率仍待后续配对数据。 |
| 2026-08-25 | **结构化坏响应增加一次受托 agent 修复机会；不放宽闸门、不增加采样数**（用户裁决；**0 成本实现，无真实 LLM/付费实证**） | 受托跑书为现役结构化节点显式追加 `--response-repair agent`，恢复链固定为 `Flash 首发 → agent 修复 → Flash 同档重采 → agent 修复 → Pro 首发 → agent 修复 → Pro 同档重采 → agent 修复 → LlmFailed`；普通 CLI 不带 flag 时仍立即重采。通用修复只覆盖非截断且材料完整的 `answer_lost` / parse / require / schema，validator 内容失败、截断和空材料不猜；代码先只尝试 BOM 与容器尾逗号。每份拒绝在书内 `_llm_rejected/` 留不可变原文、一次性候选/决定与同闸结果，`response-repair` sidecar 只封存 submit/decline，重跑仍过原 `json.loads → require → model_validate → validator`。隔离稿不进缓存或正式产物；跨 CLI 从 ledger 恢复既有调用/token/attempt 联结，修复稿采纳时实际 Flash/Pro 来源仍可反查。Stage2 blueprint 与 Stage5 write 的暂停按章隔离：同批其他章先完成，Stage2 不把暂缺章写成 `.INVALID`，Stage5 成功章先落 canonical/draft。合成回归覆盖修复成功、修复仍失败、四采耗尽、截断禁修、确定性尾逗号、一次性决定、缓存/来源账和两层逐章隔离；operator skill 静态对账、headless prompt、文档引用、前端 seams 与完整非 corpus 回归均 exit 0（3 条既有 skip；默认 corpus 仍排除，故不称语料全量绿）。⚠ 本行没有真实 DeepSeek 响应，故不宣称实际修复命中率或成本节省。 |

| 2026-08-25 | **Stage2 原子事实契约 + Stage4 场景编译器整体替换；旧源序写作骨架退役**（用户裁决；0 成本工程改动，无真实 LLM/语料实证） | Stage2 `Beat.event` 从正式响应与产物删除，新增带稳定 `fact_id`、同章证据、关闭 kind/obligation 的 `fact_atoms`，并常驻章级 `chapter_function`、因果边、入口/结尾状态引用；保真材料获得稳定 id。`summary` 不再走书级模型转述，改由代码按证据序从 atom claims 确定性投影，双书预算预览因此再少 2 个逻辑请求（94→92），atom 始终是唯一事实源。Stage4 不在旧 `PlanBeat` 上叠字段，直接以 `semantic_contract + narrative_design.scenes[]` 替换计划形状：场景带目的、进入/退出状态、事实承载、转折、delivery/transition/weight 与舞台引用，coverage 由代码计算且不进入写手上下文；`end_beat/from_beats/events[]` 在正式 plan、writer context、Stage5 prompt、Stage6 story map 与 freshness 投影中全部退役。Stage5 只锁新场景 slot 顺序，允许不改变持久状态的动作/走位/反应、对白措辞、感官心理和调度，仍禁止新因果、决定、结果、关系、知识、规则、往事及可供后文引用的新实体；验收改为事实与材料落点。Stage4 注册表为 33 条（21 HARD / 2 INFO / 10 metric），新增原相邻关系保留率、非相邻事实重组、场景边界变化、入口重建、事实再分布、语义闭合等常驻读数，无 ground truth 前不设重排阈值。破坏性版本如实提升：`chapter_blueprints.json` v4→v5；`plan_chapters.json`、`plan_edit.json`、`writer_context.json` 与 identity 伴生产物 v1→v2；旧产物不迁移，分别由 Stage2/Stage4 producer 重建，依赖 Stage5 章稿按新任务投影判 stale。验证：冻结契约 §9.5、Stage2/4/5/6、API/freshness 与完整非 corpus 回归均 exit 0；前端 typecheck、seams（104 文件、20 份冻结 mock）与 build 通过。完整非 corpus 仍有 3 条既有语料依赖 skip，当前 corpus manifest 未生成，故不称真实语料全绿；本批未调用真实 LLM，也未进行两本书人工盲读。 |

| 2026-08-26 | **持久化 Stage3 整层折叠为 Stage5/6 即时机械测量；审批与 UI 同步退役 s3**（用户裁决；0 成本工程改动，无真实 LLM/语料实证） | Stage5 从 Stage1 `main_text` 现场调用 `measure_body`，Stage6 从 Stage1 `hook_copy` 现场调用 `measure`；测量契约进入各自 Stage1 consumed projection。删除 current Stage3 artifact/schema/node/flags/rule/API/page/queue item 和 s3 gate；盘上 approval v4→v5、public v3→v4，writing 改绑 Stage1 + Stage4 + measurement contract。旧 Stage3 v2 不迁移、不重建，冻结 strict decoder 只服务历史 Stage5/6 freshness 与显式 v4 backfill；旧字节原位保留为可恢复历史证据，不进入 current discovery。验证：冻结契约 §9.5 与定向 Stage5/6/store/approval/API/queue 回归通过；完整非 corpus pytest exit 0（3 条既有语料依赖 skip，不称 corpus 全量绿）；前端 seams 104 文件/20 冻结 mocks、typecheck、27 文件/90 测试、production build 全过。本批无真实 LLM、未改 `outputs/`。 |

证据解释纪律：阶段性 smoke 会被后续完整验收覆盖；历史报告中的“未完成”不能越过
`docs/REQUIREMENTS.md` 重新成为活动状态。模型或语料结论只适用于当时样本与预登记规则，不外推成
普遍质量保证。
