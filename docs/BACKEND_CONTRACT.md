# 小说重构系统 — 统一契约、产品规格与证据基线（冻结）

> 本文件不是活动开发指南。它只保留现役 R/Q/B、双向覆盖率、修复路由、schema 追踪和冻结证据
> 基线；不要在这里登记新任务、施工进度或历史日志。当前未完成工作见 `docs/REQUIREMENTS.md`，
> 重大裁决与历史验收结论见 `docs/DECISIONS.md`。

本文件冻结前后端开发结束时的契约、产品规格、判据与编号口径。正文中的 `待建` / `无守卫` 只用于
保留追踪关系；它们的现行处置以 `docs/REQUIREMENTS.md` 为准。

**本仓没有自动放行门。** 语义审判的合并决议恒是带证据的 `UNVER`：两名互不可见的 judge
各自给 verdict、理由与逐字证据，合并只做证据并集与人读理由，不产生任何自动 `PASS`/`FAIL`。
唯一的收口是 actor / reason / 当前字节完整 SHA-256 绑定的 `human_verification`。
这不是「还没做」——那套要求双人独立标注、第三方仲裁与三轮 A/B 校准的 evaluator profile 装置
已于 2026-08-18 整批删除（证据见 `docs/DECISIONS.md`）。agent 判断同样不是 ground truth；
证据不足时保持 `UNVER`。

---

## 这份文档怎么读

### 现役契约是自足的

**不引用任何会被归档的东西**：不引原型仓、施工期编号或临时交接件。定义当前行为所需的事实一律
就地写出；历史实验数字和裁决缘由独立保存在 `docs/DECISIONS.md`，不能反向覆盖本文件。

理由：这份文档要活得比它的来源久。一条写着「详见某文档某节」的需求，在那份文档被归档、
改版或删除之后，就变成了一条**读起来完整、实际上空心**的需求——而没有任何东西会为此报错。

代价是现役约束会重复一些别处也有的话。这是有意的。

⚠ **唯一允许的外部引用是【本仓内】的稳定落点**（`contracts/...` / `novel/...` / `tests/...` /
`docs/...`），因为它们由 `tests/test_docs_references.py` 逐条验着存在——
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
| R-0.3a | **人工批准这个动作本身**——「这一段到底是不是番外」没有结构不变量可查，只能人看。**批准的唯一来源是人**：`POST /stage0/approve` 是唯一入口。批准请求必须带本次实际审阅的完整 `expected_revision_id`；工作包 A 与提交之间若经 edit/adopt 变成 B，乐观锁关闭式拒绝，不能把 A 的人眼结论盖到 B。审批页显示完整原文、删除高亮与运行时 warnings；Agent Reviewer 的 immutable workpack 也必须逐字携带完整 current clean_text 与全部剔除段，首尾/词表/章号摘要只是导航，不能代替全文反查纯数字章号下的语义漏剔。首个章号前的非空片段是 `R-1.12` 要求下游保留的开篇文案：叙事性概述、提前呈现后文情节/对白或与正文形成重复钩子均不得仅凭“不是第一章正文”判为漏剔；只把书名/作者署名、平台网址、站内导航、更新订阅/票赏推广、作者站务元话语等不属于作品叙事文案的站务或元数据残留视为问题。发现误删/漏剔时必须有不写审批、不执行节点的 typed stop 出口，不得被唯一 approve option 逼成违心通过。正式分章对照在免费 Stage1 落盘后进行 | **`人`** + revision 乐观锁，见 `B-20` |
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
| R-2.13 | Stage2 有一条**契约内的 0 LLM typed 订正入口**。`GlobalEdit` v3 覆盖人物枚举/role/fate、关系、setting、secret、world-rule、冲突阶段、arc track 与 address；自由 claim 的 patch/replace 必须同交 evidence locator。已有记录走稳定 ID/下标 patch；characters/relations/secrets/world_rules 的成员新增、删除与复合事实拆分优先走 hash-bound `collection_edits` 的 `add/delete/split`，只携 delta，不复制整组成功主体；完整 `*_replace` 仍保留为整组人工恢复面，且同集合 delta/replace、局部 patch/完整 replace 与完整人物/arc replacement 的重叠写口均关闭式拒绝。`conflict_stages` 没有稳定行 ID，只提供非空整组 replacement，并在赋值前共用 `checks.measure_conflict_stage_coverage`。同一 collection 的 split 输出不得复用其 subject，delete/split 输入 ID 不得重复，add/split 输出 ID 不得重复，输入与输出 ID 不得相交。所有 typed 形状、集合 SHA、目标/输出 ID、外键、章号、窗口与 evidence 都先在内存副本验证，失败不得半应用。`--emit-edit-template` 从当前 FAIL/未处置 UNVER 生成 schema-valid no-op v3 骨架；集合完整性项生成带当前 collection SHA 的空 operations，原样/no-op 在 canonical 输入写入前拒绝。HTTP/Commander 输入只可落同 SID `_service/edits`；确认有业务变化后才按 `exclude_unset` 原子写书内 `stage2/global_edit.json`，provenance 只绑定该长期输入。应用后重走 locator 回取、归一、HARD、来源账、轮次归档与 review 枚举；subject hash 按登记依赖闭包失效，成员 ID 集未变时不重开集合完整性，互不相关的成功决定继续继承。Commander 增量轮可同交 `post_edit_review`：它绑定修前 source/workpack、完整正文章集合与未闭合 `review_scope`；新打开项才吃默认 PASS/白名单隔离，仍保持同一 hash 的旧 FAIL/UNVER 必须在 `decisions` 中显式复判。任何未闭合 blocking、绑定漂移或非白名单隔离都在 Global/review 原子提交前拒绝；公开人工 edit 可省略该证明并照常重枚举 pending。新增字段均为 v3 可缺兼容加法，旧 v3 原样可读；旧 v1/v2 仍须人工重写 | `结构`（`contracts/schema/stage2.py::GlobalEdit` / `::GlobalCollectionEdit` + `novel/stages/stage2/extract.py::run_apply_edit` / `::_build_global_edit_candidate` / `::preflight_global_edit` / `::_review_after_global_edit`）+ delta add/delete/split、stale hash、悬空引用、复合 edit+decision、原子性与真实 consumer 回归 |
| R-2.14 | `conflict_stages[].chapters` 是**逐章显式枚举**，不是区间端点；全部阶段合起来必须让 Stage1 的每个阅读序章号**恰好出现一次**，不得缺章、重章或越界。producer/完整候选命中时问题稿进 `.INVALID`；显式提交了坏的 `conflict_stages_replace` 则在赋值和任何写盘前 `TerminalState`，两条路径共用 `novel/stages/stage2/checks.py::measure_conflict_stage_coverage`，不得重抄第二套判据。恢复优先用 `GlobalEdit.conflict_stages_replace` 完整重画后重跑 `--apply-edit`（0 LLM），或直接人工补全 `.INVALID` 后 `--revalidate`（0 LLM），最后才是显式 `--resample` 重采 | `HARD`（`conflict_stage_coverage`）+ replacement 缺/重/越界原子反例 + 既有坏候选经统一 HARD 闸回归 |
| R-2.15 | `stage2.global --emit-review-template` 必须 stdout-only 从**当前正式** `global_review.json` 投影一份 `GlobalReviewDecisionSet` v2。默认 blocking 面使用一个关闭式 `blocking_scope`：绑定当前 source SHA-256、完整 blocking `(item_id, subject_sha256)` 集合 SHA、项目数与正文章全集，状态默认 `UNVER`，所以原样应用不能打开 s2 gate；核验后可显式改为 `PASS`，`decisions[]` 只列逐项 FAIL/UNVER/PASS 例外，服务端仍展开成逐项 hash-bound 持久决定，且 scope 永不自动裁 disclosure。scope 还必须绑定**实际读过的 workpack**：当前只读包为 `stage2-global-review-workpack-v4`，把是否通读全书拆成 `reading_scope=whole_book｜localized`，把展示材料拆成 `evidence_mode=unresolved_formal_evidence｜formal_evidence｜claim_literal｜none`，顶层 legend 是唯一解释；正式 evidence 在场时不重复写 `exact_literal_hits`，`source=None`/缺失/坐标无效不得回退 claim 字面命中。只要包内存在 `reading_scope=whole_book`，顶层 `main_text_chapters[]` 就必须把完整 MainText 各章正文**恰好携带一次**；只给 MainText SHA 与局部 snippet 不能证明已提供整书证据。每行 `generation_impact` 由 `review_generation_impact_of` 的显式闭集派生，`effect/group_key` 再由生产分类派生；含 blocking 的组优先，同优先级按原 items 的组首次出现顺序，组内保持原序。默认 blocking 包的 role 行必须携同人物 fate 的只读主体、状态与正式/未决 evidence companion，不复制额外 claim-literal 材料，也不新增、删除或代替 decision item。workpack 顶层与 binding 同时包含完整 typed MainText 的 SHA-256，legend、正文全集、两轴、分组和 companion 全部进入最终 workpack SHA；Commander 传输层可在不改变公开 workpack、binding 或 SHA 的前提下投影 `stage2-global-review-delta-v1`：只发送 pending/FAIL/未隔离 UNVER 的 subject cards，PASS/已隔离项只留 identity digest/count；subject、evidence、reason 分别内容寻址，并以 claim/evidence index 闭合引用。旧 v2/v3 SHA 必须在 formal/review/`_rounds` 的首个写动作前拒绝。模板的 `expected_workpack_sha256` 固定为全零占位并被 apply 明确拒绝，operator 只能从本次实际读过的 v4 workpack 手工复制真实值，重新生成模板不得替人刷新阅读凭证。MainText 输入身份变化时，人物/关系/规则/秘密集合完整性、冲突分区与 arc 分析六类整书决定必须重开；其余可独立修正的事实项仍按自身 subject 继承。`--review-scope all` 仍逐项携带当前 item id、完整 subject SHA-256 与 evidence；逐项 `UNVER` 可选携 `generation_disposition={action: quarantine_group, reason}`，PASS/FAIL 在 schema 上不能携带，服务端仍按 `R-2.22` 的组白名单复核；两种形状都不得从 grounding 重拼第二套 item 身份。DecisionSet v1 没有 scope hash/正文章全集声明，不迁移；零成本重发 v4 workpack / v2 template 后由人复核再交 | `结构`（`novel/stages/stage2/global_review.py::review_template` / `::review_workpack` + `novel/quality/global_review.py::blocking_scope_sha256` / `apply_decision_set` + `novel/orchestrator/reviewer_tasks.py::_compact_stage2_materials`）+ MainText 漂移/旧 v2-v3 workpack SHA 零写拒绝/完整正文恰好一次/集合顺序/fate companion/reason catalog/连续生成 SHA/CLI/API 正反回归 |
| R-2.16 | Stage2 全局逐项复核与**非称名** Global HARD 不再是 `stage2.blueprint` 的串行前置：通过当前 schema 且称名 HARD 已收口的最新 Global 候选，无论正式落在 `global_grounding.json` 还是因其他 HARD 留在 `.INVALID`，都必须从它与 current MainText 确定性物化注册、版本化的 `blueprint_character_projection.json`；称名 HARD 未收口时不得物化可消费投影，freshness 必须报 `unknown/global_naming_blocked`，Blueprint 在任何 LLM 前关闭，state/queue 只停在 Global typed edit / `--revalidate`（0 LLM），不得 `--renormalize` 或默认付费重采。Blueprint 的 prompt、validator、compiler、provenance 只消费 current/fresh 的该投影 + MainText，不读取或伪记 role/fate/secret/relation/setting 等 Global 边。投影 freshness 必须强类型校验完整 GlobalGrounding 与 MainText，不能因未消费字段恰好不改变投影就把 schema-invalid 底座洗成 fresh。RunSupervisor 只在 ready Blueprint token 的同一冻结 snapshot 上确认 Global blocking review items 非空且全部为 pending、且未命中登记的 `stage2_invalid_projection_ready`（最新 Global `.INVALID` + current/fresh projection）时，才可构造 v4 immutable workpack 并启动一次**只读预审**，再启动 Blueprint；命中该登记路径时必须在读取 formal Global workpack 或旧/stale Blueprint 前跳过预审，直接执行该 snapshot 已铸造的 ready Blueprint token；预审不得写 live request/review/artifact，Blueprint 落盘后只有当 Global 业务 SHA 与 workpack SHA 均未漂移，才把 typed 决定重新绑定到 live surface 并走原 canonical action；预审输出不依赖、也不续用 provider session 身份。二者共同使用 `min(4, NOVEL_CONCURRENCY)` 个跨进程 OS 模型槽：Reviewer 占一槽时 Blueprint 最多取余槽，Reviewer 完成后槽位自动归还；配置为 1 时保持串行，总并发绝不因重叠而增加。Stage2 后续增量审阅每轮使用 fresh logical/provider session，连续性只来自持久 ledger、workpack identity 与存活决定，不依赖隐藏对话。其他同书 review、typed edit 与 producer 写回仍服从同一互斥。排除 `global_naming_blocked` 后，投影缺失、损坏、或相对最新 formal/INVALID Global stale 时，真实 Blueprint consumer 仍须在任何 LLM 前关闭；state/queue/inbox 只给出或执行 `stage2.global --renormalize` 从现有底座重物化（0 LLM），不得把缺一份伴生产物解释成默认付费重采 Global。旧 ChapterBlueprints v6 若仍记录 `{MainText, Global}` 输入边，不升 artifact version；store 只有同时亲见原始 inputs 键集合精确等于 `{MainText, Global}`、MainText edge current/fresh、Global 文件指纹仍 match 且仅因历史 consumed 投影不可重算而为 `unknown/consumed_unresolved`，才铸造专用 `unknown/legacy_blueprint_inputs` marker。state/queue/inbox 只在 current/fresh projection 已在场时把它解释为 `stage2.blueprint --renormalize` 零调用刷新；通用 unknown、额外或未来来源边、MainText 漂移、Global file/consumed 漂移或 ghost 都不得借该路迁移。这个精确 legacy marker 即使只存在于 live `.INVALID`、或与旧 formal 并存，仍优先走 renormalize，不得先被通用 INVALID 路由截成 revalidate。其余 inputs 已是 projection 的 live Blueprint `.INVALID`，无论是否与旧 formal 并存，state/queue 都只停在 `stage2.blueprint --revalidate --actor <id> --reason <why>`（0 LLM）并留空默认 paid argv；`--revalidate` 只重判当前 immutable baseline，不得改写旧问题稿 inputs 冒充迁移，手工误用于 legacy 问题稿也须在写盘前以显式终态指回 renormalize。Global 的 role/fate 等修订若投影逐值相同，不重写投影字节、旧 Blueprint 保持 fresh；称名候选变化则投影 stale，重物化后旧 Blueprint stale。`address.from_chapter` 只在同一归一 surface 有多个 owner 且确实改变候选时进入投影，canonical shadow、唯一称呼及 role/secret/relation/setting/address.by/context 等未消费变化不得制造付费 stale。统一硬闸仍从 Stage4 首项开始：Stage4A/B、`stage4.context`、其余下游付费执行及 Stage7 组装/下载都必须同时看到 current **正式** global、current/fresh projection + blueprint、`stage2.blueprint --check-only` 的联合 HARD 通过，以及与当前 grounding 完整一致、且 blocking 面 **generation-safe** 的 review：每项须为 `PASS`，或为逐项携带 hash-bound `quarantine_group`、且所属 `secret:<id>` / `relation:<id>` / `world_rule:<id>` 完整组可从写作视图确定性删除的 `UNVER`；人物、setting、arc 与集合完整性不可隔离。Global `.INVALID`、blocking 面的 `FAIL` / `pending`、未处置或不可隔离 `UNVER` 一律不放行。原始 grounding/review 与 UNVER 审计不改写；Stage4～7 消费净化视图并同时记录 review 输入边，秘密隔离还级联移除引用它的 `via_secret` relation；净化后 schema 不成立仍关闭。**交付效果分级见 `R-2.22`**：`disclosure` 三类不阻断，但不自动 `PASS`、不从评测面消失，且必须进 inbox 与终审包。review 早于 blueprint 完成可以正常保存，但 `s2` 必须保持关闭，不能因缺 blueprint 回滚有效决定或伪装成已开闸。`dry-run`、联合 `--check-only`、typed edit、`--revalidate` 等零成本恢复路始终开放；Stage2 唯一显式复检仍是 blueprint，禁止新增 `stage2.global --check-only`。决策模板、书内相对/绝对输入与 `_service/edits` API 输入统一使用注册表版本化的 `stage2/global_review_decisions.json`；`--emit-review-template` / `--emit-review-workpack` 默认只输出 blocking 面（`--review-scope blocking|all`），裁剪的是**只读阅读范围**而不是评测面——没进输出的项在盘上仍是 `pending`；immutable workpack 只给主体、注册证据与原文精确命中上下文，不自动做决定；Commander wire 再确定性裁为只含未闭合 subject cards 的 delta，成功且依赖未失效的项不重复发送 | `结构`（`contracts/invariants.py::stage2_review_required` + `novel/stages/stage2/global_review.py::require_approved` + `novel/stages/stage2/character_refs.py::materialize_blueprint_character_projection` + `novel/store.py::_blueprint_character_projection_freshness` + `novel/model_slots.py` + `novel/orchestrator/reviewer_tasks.py::build_stage2_prereview_task` / `::bind_stage2_prereview` + state/queue/CLI/API/Stage7 消费边）+ formal/INVALID 物化、schema-invalid/legacy unknown/额外键形/上游漂移反例、0-LLM 恢复、只读预审/live 重绑/共享槽不增并发/review 早交/freshness/模板/API/下载回归 |
| R-2.17 | `R-2.16` 那道闸的**评测面**必须覆盖：每个角色的 `role` 与 `fate`、`canon.setting` 两条事实、每条 relation、每条 secret 的 fact / knowledge event / 埋点章，外加 `arc.conflict_stages` 与 `arc` 其余部分两项分组裁决；同时必须为 characters / relations / world_rules / secrets 四个集合各枚举一项完整性裁决，反向检查有没有整项漏抽。粒度按「**能不能被单独修正**」分：事实逐条、解读分组；自由事实的 subject hash 同时绑定 claim 与正式 evidence，证据改变会重开该项，集合完整性项则故意剥 evidence，避免只改坐标冲掉「没有漏项」的决定。**没被枚举出来的字段等于没人审**——`R-2.16` 说的「与当前 grounding 完整一致」指的是这份清单一致，不是整份 grounding 过了人眼。⚠ 风险面包括整个人物/关系漏抽、`role` 的结局旁路、`setting.世界观` 的泄底、relations 第二剧透通道、人物知情时点与 secret 埋点 | `结构`（`novel/quality/global_review.py::enumerate_required`，取值面在 `contracts/schema/evaluation.py::GlobalReviewItem`）+ 覆盖面/继承回归 |
| R-2.18 | **一条 secret 只承载一个原子事实**，并只保留一个 `first_reveal_chapter`。复合真相分步点破时必须拆成多条 secret；对下发无作用的分组与确认事件不进正式 schema。逐章下发闸门与全部下游读数共用 `novel/core/facts.py::first_reveal_chapter`；`fact` 是否仍塞了多个原子事实由 `R-2.17` 人工复核 | `结构`（`contracts/schema/stage2.py::Secret`）+ 逐章下发路径回归 |
| R-2.19 | relation 的**世内成立时点**与**读者可见时点**必须分开。`visibility` 是判别联合：`public.from_chapter` 或 `via_secret.secret_id`；生产下发只认 `novel/core/facts.py::relation_visibility_gates` 的显式声明，分别取 `max(valid_from, public.from)` / `max(valid_from, secret.first)`，悬空 secret 或说不出的 first 关闭为不可见。旧人名+埋点启发式只由 `relation_visibility_mismatch` 作 INFO 对账，绝不能再决定 Stage4 事实投影 | `HARD`（引用/章范围）+ `INFO`（显式/启发式分歧）+ Stage4 context/fact_refs 共用落点回归 |
| R-2.20 | 七类自由事实（role/fate、relation predicate、setting 两格、world-rule desc、secret fact、knowledge event、arc trajectory）都必须带**代码回取**的原文证据。唯一现役的模型 evidence wire 是 `paragraph-candidate-v1`：候选表只由 `paragraph_spans` 生成，prompt 渲染与 lookup 共用同一张表，模型/agent 的 candidate-only 响应只交 `evidence_candidate_id`，不得填写 locator 或 `SourceSpan`。候选 ID 必须在候选被接受、写 checkpoint 或落任何业务产物前通过本次下发表的关闭式成员校验，再由代码铸成既有 `FactEvidence + SourceSpan`；ID 不进入正式 artifact、人工编辑或 provenance。该结构只消灭章号/locator 转抄错位，主张是否受该段支持仍由既有 GlobalReview 语义复核承担。2026-08-31 产品所有者在首个实验 wire 前取消 candidate/locator A/B 与盲审，并把 candidate 收成 sole active；实验状态为 `CANCELLED_BEFORE_WIRE`，真实 provider wire=0，没有实验 verdict，质量、token、wall 与货币成本均未验证。该产品裁决不得写成 candidate 已通过质量非劣、成本或等待时间门；`locator-v1` 不再是可选模型臂、默认或 fallback。正式 GlobalGrounding v4 / GlobalEdit v3 不升版：`GlobalGrounding` 仍只落正式 `SourceSpan`，所有人工 `GlobalEdit` v3 仍以 `{chapter_ordinal, locator}` 提供 0-LLM 订正，手改 `.INVALID` 后的 `--revalidate` 也继续走同一 locator 恢复尺子。人工 locator 在合法指定章先 raw、后 normalized 且必须唯一命中；指定章**零命中**时才把章号降为可纠正提示，按阅读序逐章复用同一定位尺子，且仅当全书恰一候选、任何章都没有多命中时纠正为真实章，再统一过 `exact_span`。指定章多命中、非法章、全书零/多命中或 formal span 漂移仍落 `source=null` + 正面 failure 账，由 `global_evidence_unlocated` HARD 拦到 `.INVALID`；禁止 fuzzy、拼接全文、猜首命中，也不得让人工输入伪造 `SourceSpan`。存量 `.INVALID` 只有在 failure 原因明确来自 locator、claim path 与声明章仍相同且 `source` 仍为空时，才可反向恢复 locator 后重跑同一算法；formal `SourceSpan` 漂移的失败账永不反向恢复，连续 `--revalidate` 也不得把它自动搬家。成功跨章纠正不改变 GlobalGrounding v4 / GlobalEdit v3，也不进正式 provenance；只在 `_service/llm_audit.jsonl` 留绑定最终 artifact SHA 的无正文事件（claim path、原/新章、exact/normalized）。agent 整段 session 固定使用 `GlobalCandidateAgentTurnResponse`，联合分支只允许 `full_candidate｜role_fate_patch`，不存在 locator agent 信封或 `evidence_patch` fallback。ID 格式/成员失败仍在 acceptance/checkpoint/业务写盘之前关闭；普通业务 HARD 仍完整重交。只有 HARD 集合精确等于 `role_leaks_fate` 时，生产 typed target 才开放窄修：目标人物集合必须精确相等，每人原子提交 `role/role_evidence/fate/fate_evidence` 四格，同时绑定当前 raw candidate 与 evidence table 双 SHA；未知 ID、旧 SHA、role no-op、空 fate、缺/多人物或 masked projection 之外任何变化均关闭。accepted role patch 最多两次；第一次开始即 sticky，第二次仍红或转成 mixed/其它 HARD 都不得借剩余 turn 回到 full candidate。role/fate patch 审计只记 kind、attempt、前后 raw SHA、table SHA 与改动格数，不记人物/字段值/正文，也不冒充 adoption。候选所有权留在 `Stage2GlobalAgent`，外层 Commander 不接收或改写候选。Global fresh/restored 两路必须共用同一个 role patch reducer；append-only accepted checkpoint 精确保存合并 candidate、turn kind、可选 base、MainText/table/candidate SHA、accepted role patch 计数及 session/ledger 身份，并在恢复时重跑同一 schema/applicator/membership/HARD/currentness。dynamic-validator 拒稿 custody 按当轮 `expected_turn_kind + base + 拒绝前 accepted count` 封存并对证 latest durable checkpoint；typed replacement 仍走同一 validator/applicator，不能把第二次窄修伪装成第一次。正式 Global bundle 只在同书所有权内经 `prepared → Global/review/projection/INVALID touched paths → typed readback/freshness → 唯一 adoption → committed` journal 收口；foreign prepared 关闭新 run 覆盖，kill 后只允许原 run 幂等补尾。终态后的默认恢复路是上述 0-LLM locator 人工订正；显式整份 `--resample` 只作最后手段。Stage4/5/6 的实际材料与 freshness 投影一律剥 evidence，避免证据原文外发和无关付费 stale | `HARD`（`global_evidence_unlocated`）+ sole candidate wire/退役 locator selector 关闭/同源候选表/未知 ID 早拒/SourceSpan 编译/人工 locator exact/normalized/0/多/非法章/formal drift/role-fate 四字段双 SHA/masked projection/两次 sticky/checkpoint+custody+journal kill-window/审计无正文/恢复/下游隔离回归 |
| R-2.21 | canon 中所有决定逐章下发的章引用都必须落在当前 Stage1 正文章集合：`world_rules[].versions[].effective_from_chapter`、`secrets[].setup.chapters[]`、`secrets[].first_reveal_chapter`。越界值不是“未来再生效”，而是永远不生效/不揭示；生成、复检与 GlobalEdit 必须同判。relation、knowledge event、address、evidence 与 conflict stage 由各自专门规则守，不在本条重报 | `HARD`（`canon_chapter_refs_dangling`）+ 三类越界反例 + 生成/人工同判回归 |
| R-2.22 | Stage2 复核项先按**生成影响**分成 `must_fix / quarantine_or_fix / disclose` 三档，唯一闭集落点是 `contracts/invariants.py::GLOBAL_REVIEW_GENERATION_IMPACT_BY_KIND` 与 `review_generation_impact_of`；每个现役 kind 必须逐项登记，未知或漏登在 import/workpack 前关闭，没有默认放行。`must_fix` 覆盖人物/setting/arc/集合完整性等无法从写作视图安全删除的主体，非 PASS 必须真实订正或停止；`quarantine_or_fix` 覆盖 secret/relation/world_rule 及其时点/依赖，证据支持时 PASS，事实错误时修订，原文确无答案时才可对完整组提交 hash-bound `quarantine_group`；`disclose` 当前只有 `name_distinctiveness`、`address_context`、`fate`，不阻断生成但不自动 PASS。交付 `effect` 由生成影响确定性投影：前两档为 blocking，后一档为 disclosure；legacy `known_by` 保守归 `quarantine_or_fix`。分档只改变生成处置，不缩减评测面：`global_review.json` 形状与版本不变，current 仍要求 item/path/hash 全一致；任何 pending/FAIL/UNVER 都留在审计、inbox 与终审包。隔离只作用于下游内存写作视图，原始 grounding/review 不改写，完整组删除后仍须过 schema；否则保持关闭。workpack/Commander delta 每行携服务端派生的 `generation_impact/effect/group_key`，前端和模型都不得按路径另猜分类 | `结构`（`contracts/invariants.py::review_generation_impact_of` / `::review_effect_of` + `novel/quality/global_review.py::generation_safety` / `::review_group_key` + `novel/stages/stage2/global_review.py::generation_grounding` + `novel/orchestrator/reviewer_tasks.py::_compact_stage2_materials` + `novel/views/inbox.py::_classify`）+ 三档覆盖闭包、未知 kind、隔离白名单、开门条件、审计保留与下游净化回归 |

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
| R-3.2b | 模型响应里的 locator/phrase/关闭式证据候选 ID 只用于定位；正式产物必须由代码归一成唯一的 `SourceSpan{text,start,end}`，下游不再重新搜索 locator。v3.2 的候选 ID 与章内段落一一对应，prompt 渲染与 lookup 必须共用同一不可变候选表 | `结构`（`novel/stages/stage2/normalize.py` + `novel/stages/stage2/blueprint_evidence_candidates.py` + `novel/core/spans.py`） |
| R-3.3 | **原文以「点」不以「段」下发**：离散原子（要害台词 / 硬数字 / 物件 / 因果前提）可逐字下发；**连续文本段一律不下发** | `结构`（`novel/stages/stage4/beats.py::verbatim_pieces` —— 四类各归各组，**未知 kind 丢弃并交调用方报出**）<br>⚠ **F5-5 改判**（原阻断级）：这一条守在**下发那一刻**，不守在产物上。它此前有一版 `HINGE_GROUPS.get(kind, 'verbatim')` 的兜底，把模型自造的 kind（实测「行动描述」）**升级**成「原样写入，一个字都不能改」——而那正是一句叙述文，正是本条要挡的东西。⚠ 取值面那一半归**载体**（`R-3.6`），本行只管「段不许当点发」；**「点有没有真的发出去」是另一条**（`R-3.3b`） |
| R-3.3b | **抽到的点必须条条出现在下发面上**：stage2 有多少个保真点，写作上下文里就必须有多少个（比条数不比文字——组装最后一步的确定性改名会让文字合法地变） | `HARD`（`ctx_hinge_delivered`，分母为零走 `unver`）<br>⚠⚠ **它是拿一次真事故换来的，而那次事故里 `R-3.3` 与 `R-3.6b` 两条都在、都绿。** `beat_index` 曾把 hinge 归一成 `{kind,text}` 这个中间形态，而那个键名与已废弃的旧 stage2 契约同名，一次「清理遗留兼容」（`08240cf`）把读它的分支删掉后，**五本书 898 条 hinge 一条都没能下发**：`R-3.6b` 在上游看 stage2 产物（材料齐的）、`R-3.3` 在下游看函数（代码还在），**中间这一步的丢失不在任何一条的扫描面里**；`R-7.3` / `R-7.4` / `R-7.17` 三条 HARD 又因分母同时归零而一起报绿，闭卷写手手里一个原文字节都没有。⇒ **跨越一步的丢失，只能由一条横跨那一步、比两边条数的判据抓住**（`docs/LESSONS.md` 第 22 条） |
| R-3.4 | 一条跨段落的「要害台词」下发前**必须按段落边界确定性拆成独立的点**。实测一章 4 条跨段原话被写作节点**一条不剩地全丢** | `结构`（`novel/stages/stage4/beats.py::verbatim_pieces` —— 与上一行同一个函数，本行说的是它里面按段落边界的切分）<br>⚠ **F5-5 改判**：它与上一行是**同一段代码的两半**，而那段代码**今天就在仓里**——F5-0 当时判断落点「在本层的模块目录里」是错的：拆点发生在 `stage4` 的下发投影里，不在 stage2 的产地。⚠ 那份实现是**唯一一份**：真正发给写手的字节与告诉规划器「这一拍有多少字压不掉」的那个数**都调它**，各写一份就会让报给规划者的数与写手实际收到的字节对不上 |
| R-3.5 | Beat span、fact atom evidence 与 hinge 原文字节**只能由确定性代码按响应 locator/phrase/关闭式候选 ID 回取**，不许 LLM 转抄；v3.2 fact evidence 只交候选 ID，代码回取的整段 `SourceSpan` 必须完全落在该 fact 所属 beat 内。正式 `SourceSpan{text,start,end}` 晋升前必须验证 offset/text 一致并落在所属 beat 内 | `HARD` |
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
| R-3.16 | 逐章产物必须与正文章**一一对应**：不缺章、不多章、不重复、顺序一致，且**不得出现上游的分节键**（章的唯一键全链只有一个）。⚠ 两份 stage2 产物**齐备才跑得动**——缺一份判**失败**而不是提示。Commander 的 Blueprint 人读面是书级 immutable workpack：异常章优先但全章一份不少；隐藏代码已证明的结构 ID、字符坐标与计数，保留章功能、覆盖原文、事实、参与人物、hinge、状态、因果、`narrative_relations` 与全部叙述声音。显式审阅契约为 `BLUEPRINT_REVIEW_CONTRACT=stage2-blueprint-review-v4`；v4 必须把每章完整关系集合投影进实际可见 workpack 与 review identity，不能只在 artifact 里存在。身份分三层：`review_identity_sha256` 只覆盖人实际看到的内容/finding 与该显式契约，`validation_seal_sha256` 在服务端覆盖当前代码、规则、artifact 字节和完整机械复检，`review_token=bpr3_<binding_sha256>` 只负责本次提交的 exact TOCTOU。公开工作包不输出 `code_version` / `rules_fingerprint` 原料；页面和 Commander 只提交 opaque token，不手搬多枚 SHA；服务端在同书锁内重建工作包并二次快照。代码/规则变化可让 effective `seen.read` 暂停，免费复检后只要 review identity 未变，盘上 raw 阅读事实自动恢复适用；内容或可见 finding 变化才要求重读。旧 `bpr2_` 包只读且不能提交新的 seen。工作包不生成决定、不新增 Stage2 approval/gate、不改变 artifact；CLI 空请求、mock 与 `backfill_from_disk` 均不得制造阅读事实 | `HARD`（现役判据）+ `novel/views/stage2.py::blueprint_review_workpack` / `novel/orchestrator/approvals.py::_blueprint_seen_proof` 与 CLI/API/UI 的全章、关系可见、opaque-token、raw/effective 恢复回归 |
| R-3.16b | 已登记架构只保留 `extraction-ir-v2`、`extraction-ir-v3.1`、`extraction-ir-v3.2`（唯一落点 `contracts/invariants.py::BLUEPRINT_ARCHITECTURES`），当前默认 v3.2。共同运行边界是稳定全书索引前缀 + 当前章全文 + 最多 4000 字相邻过去原文 + 最多 1600 字更早连续性尾摘，未来章正文不可见。v3.2 与 v3.1 编译后的正式业务语义同形，但响应 wire 删除编译前即丢弃的 micro-event 文本，只交严格递增且到章尾的止段数组；fact evidence 不再转抄原文或坐标，只交本章段落候选 ID，由代码在 checkpoint/业务写盘前复核候选属于该 fact 所属 beat 并回取 `SourceSpan`。产物只常驻下游可用来识别产地的 `architecture`、`stable_prefix` 和三个过去窗口上限；调用拓扑由 `ContractSpec` 显式登记，越界由 `scope_violations` 无条件记录。不保存可从架构与运行参数直接推导的批次/布尔字段。该默认切换只有结构等价与工程回归证据，真实质量/速度须经至少五次冷缓存 clean run 验收；v2/v3.1 保留为显式回退 | `HARD`（边界/候选闭集/所属拍）+ `度量`（`context_scope`） |
| R-3.17 | 转场点由地点字面推导、**不单存字段**，且它的读数要带着自己的偏差一起报——地点是自由文本、粒度不稳（街上/街边、公寓/公寓卫生间门口），按它相等推导出来的转场数**系统性高估**。⚠ 它作为**下发给写手的舞台信息**是对的，不改；但凡引用这个数的地方都要带上这一句 | `度量` |
| R-3.18 | **落盘前的原文回取必须把失败态正面留账**：span/hinge 与 fact atom 分开记录稳定根因，不能只靠 `source` 缺席反推。fact atom 的原始 `evidence_phrase` 若在声明 beat 内零命中、但在全章恰好唯一命中且完整落入恰好一个其他有效 beat，compiler 必须零 LLM 重写 `source_beat_ref` 并落精确 `SourceSpan`；全章零/多命中、跨边界或不能唯一归属时仍留正向失败账并把问题稿落 `.INVALID`。存量 INVALID 的 `--revalidate` / `--renormalize` 只能从与不可变来源账、当前 schema/system、精确 phase↔purpose↔chapter、accepted request/attempt/完整 purpose ledger 及当前 MainText/称名投影重编译结果逐字段闭合的唯一 checkpoint 恢复原 phrase；reasoning 救回稿还必须逐字命中书内 `_answer_lost` 原响应，并由同 run/attempt 的唯一 `answer_lost` 审计指针把其 content 绑回 ledger response hash。不得从转述 claim 猜 locator，也不得只靠可重签的 checkpoint self-hash 给 reasoning 正文作证 | `HARD`（`verbatim_failed`）+ compiler 正反例 + checkpoint/ledger 0-wire 恢复回归 |
| R-3.18b | 成功的精确/内容空间回取由正式 `SourceSpan{text,start,end}` 表达，不重复持久化只供展示的改写清单；失败根因分别落在 `verbatim_failures` / `fact_atom_failures` | `载体`（`contracts/schema/stage2.py::SourceSpan` / `BlueprintProvenance`） |
| R-3.19 | blueprint 的模型请求、内容恢复与 Flash→Pro 兜底都以**单章**隔离；一章失败不得取消其他已起跑章。每章只有依次通过 JSON parse、必需键、当前 Pydantic、dynamic validator 与章节 scope 切分后，才原子写入同一 `run_id` 下的 `_service/runs/<sid>/stage2/<run_id>/blueprint/checkpoints/<phase>-chNNNN.json`；旧书内 `_stage2_runs/<run_id>/blueprint/` 只作逐字校验后的兼容读源，新 writer 不再向其追加。普通 content 与完整通过的 reasoning salvage 均保存 accepted channel、原始 attempt/model/request 指纹、原文/payload SHA、contributing attempts、recovery mode 与 `salvaged`。该目录是内部 v1 sidecar，不是正式 artifact 或 `.llm_cache`，不参与完成度、schema 版本、缓存命中或正式 provenance。resume 必须显式复用原 `run_id`：恢复时先核对代码/契约/架构/输入/request/SHA 与 ledger 联结，再重新跑当前 schema、validator、scope、整书 compile 与 global+blueprint 联合 HARD；任何损坏、跨 run 或漂移都关闭式停止，不能退化成 cache miss 后重新付费。主动 `--chapters` 刷新首次进入必须使用全新 run，不得命中先前逻辑运行的 checkpoint；只有本次 refresh run 已生成唯一 manifest、并完整封存 batch resolution 后，显式同 `run_id` 的唯一人工 resume 才可恢复**本轮新采**的成功章，不能把普通重复执行冒充 resume。并发波次全部结束后先保存所有成功章，再把全部 `ResponseRepairRequired` 稳定排序为唯一 immutable manifest；decisions 必须完整一一覆盖 attempt 集合并逐项给 `submit|decline` + 非空理由，候选及 SHA 经一次性全量校验后只原子写一份 batch resolution，batch attempt 禁止再走单项 submit。每个逻辑 run 最多一次人工 workpack、一次人工 resume；恢复后再失败只能进入已登记自动动作或终态，v2 第二相也不得制造第二次人工暂停。正式 `chapter_blueprints.json` 仍必须覆盖完整章集；全书 artifact 已写而提交哨兵未写时按 artifact 指纹幂等收尾，不新增 `_rounds` 或重复 adoption | `结构`（`novel/stages/stage2/extract.py::_run_chapter_requests` / `_raise_response_repair` + `novel/llm/stage2_runs.py`）+ 检查点零 wire/损坏漂移/八章单 workpack/单 resolution/单 resume/幂等提交回归 |
| R-3.20 | `land` 是本章新增且必须落地的事实，`context` 是章首已成立状态；`entry_state_refs` 只能引用 context、`ending_state_refs` 只能引用 land。因果边引用必须闭合、不得自环或成环；数组位置只表示证据位置，不是未来写作顺序 | response validator + 正式 `Blueprint` 载体 |
| R-3.21 | ChapterBlueprints v7 新增显式 `narrative_relations` 判别联合：`story_before(before_fact_ref,after_fact_ref)`、`response_to(stimulus_fact_ref,response_fact_ref)`、`same_scene(left_fact_ref,right_fact_ref)`、`observes(observer_char_id,observed_fact_ref,observation_fact_ref)`，共同带 `enforcement=review｜hard`。它只记录重构不可丢失的语义关系，不从相邻事实、同一 source beat 或数组位置自动补边；端点必须同章闭合、无自环/重复，same_scene 无向端点规范排序，`causal_edges + story_before` 不得成环，observer 必须在本章人物闭集且参与 observation fact。producer 只能提出 review；明确且高影响的关系只经 hash-bound `BlueprintRelationEdit` 完整章关系集替换晋升为 hard，复用同一 schema/归档/freshness 且不调用模型。Stage4A 只把 hard 关系用 opaque aliases 送给 Executor；review 关系只进入 Commander workpack，二者都不泄漏 SourceSpan、source beat 或完整源叙述顺序。v6 不迁移、不补空数组；只有与现存 Stage4A v1 truth、来源边和下游制品逐字闭合的冻结旧书可走只读兼容，普通 producer、付费 repair/replan 与新 Stage4B 不得消费。旧 A 的 review contract 变化仍令决定过期、不得复活；唯一例外是既有 EvidenceBinding/Plan/WriterContext 分别通过 frozen exact downstream proof，且 current `gate.s4=true`、最终 structure accepted，此时 A 作为一次性 B 生产授权不反向阻断既有下游消费。缺任一 proof/产物/最终结构决定，或需要新 B/Context 时仍关闭式停止；新 v7 只在 clean base 由 producer 重建 | `载体`（`contracts/schema/stage2.py::NarrativeRelation` / `::BlueprintRelationEdit` + `ARTIFACT_SPECS` v7/v1）+ `结构`（`novel/stages/stage2/relation_edit.py`、`novel/stages/stage2/frozen_v6.py`、producer review-only 与 Stage4 blind alias 投影）+ closure/cycle/observer/同源非同场/非目标章字节恒等/冻结下游正反例回归 |
| R-3.21b | Stage4 对 `enforcement=hard` 的显式关系必须共用一把生产判官：`response_to` 保留刺激—回应指向，`same_scene` 要求端点至少共享一个实际场景，`observes` 要求观察事实在被观察事实之后并把人物在场证明留给 B/formal preflight；`story_before` 表示故事世界真实时间，只能用显式 transition 可证明的偏序判反向。同场内部、parallel/歧义支路或偏序不充分一律落带证据 `UNVER`，绝不得用 scenes 披露数组位置猜 PASS/FAIL。违反可判 HARD 时阻止 A/formal 落盘；review 关系不进入 Executor 判定 | `HARD`（`narrative_relation`；`novel/stages/stage4/checks.py::narrative_relation_scene_findings` / `::narrative_story_time_findings`） |

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
| R-4.15 | s3 闸、Stage3 API 与页面全部撤下。盘上审批为 v7、公开 API 为 v6；旧盘 v6 只能经显式 `backfill_from_disk` 收进 v7，保留仍能按**现役同一 proof（包括 current review contract）**逐项对证的 s0～s4、seen 与 typed 历史，同时清除 s5～s7，绝不伪造 Stage5 闭合回执。旧 `seen.read` 未绑定完整 Blueprint 工作包，不能升级成现役阅读事实；backfill 不从“产物齐备”制造 read/global seen 或 s2 人审，Structure subject 未携 current `STRUCTURE_REVIEW_CONTRACT` 的历史决定也不得被重签为 current s4。Stage4A 设计审阅只授权 B，不打开 s4；Stage5 写作证明绑定当前 Stage1 正文、Stage4 计划、**经最终结构批准后单独生成的当前 WriterContext**和测量契约；s4 仍只由 B 编译后的独立 typed 计划结构裁决打开 | `结构`（`novel/orchestrator/approvals.py::backfill_from_disk` / `::authorize_plan` / `::authorize_context` / `::begin_writing` + `contracts/schema/api.py::ApprovalDoc`） |

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
| R-5.5 | Stage4 正式生产是两个**独立公开付费节点/授权**，不是两个逻辑请求：`stage4.design` 仍只有一条公开 CLI dispatch、一次预览/`--yes` 授权，内部固定为 **1 条整书策略请求 + N 条逐章私有 session**；策略验证并 checkpoint 后才扇出逐章请求，逐章硬上限 4 并发，每个 unit 独占 provider session、purpose、ledger attempt 与验证后 checkpoint。A 通过机械闸并原子落盘后，必须先提交独立、hash-bound 的正式 `design_review`：全书策略决定为 `accept / request_replan`，逐章决定为 `accept / request_repair`；它只授权下一次 B，绝不打开 s4。只有同一全局 review basis、策略主体不变且章主体逐字相同的旧 `accept` 才能预填复用，并仍须对当前整书主体原子提交一次；全局 basis/策略变化使全章过期，单章业务变化只使该章过期，旧拒绝不静默变接受。`request_repair` 必须带关闭式修复码及章内 scene/opaque-alias 证据，CLI 以服务端给出的单一 opaque `--review-token` 现场展开当前决定，只重跑被拒章并复用其余 checkpoint；策略被拒才允许在同一 token 动作上附加 `--replan` 整书重做，裸刷新或把自然语言理由直接发给模型均拒绝。`stage4.plan` 仍是一条独立逻辑调用，只把已审阅接受的原子候选绑定证据并确定性编译为计划；A 完成或审批完成都不得静默连跑 B，批量队列必须重新 plan/preview/confirm 才能得到 B 的付费 argv。A 的策略 prompt 只见同号章 `chapter_function` 与确定性计数，不见 fact/人物 alias；单章 prompt 只见冻结策略、本章去序真相，以短小、稳定、局部且不含源序含义的 opaque alias 引用事实与人物；两类 prompt 均不得出现原文、SourceSpan、`source_beat_ref`、beat/stage 或材料。传输、内容重采与定向修复仍可能产生额外 wire，必须逐条进 ledger | `结构`（`contracts/invariants.py::NODES` 的 `stage4.design` / `stage4.plan` + `novel/stages/stage4/prompts.py` + `novel/llm/stage4_runs.py` + `novel/orchestrator/approvals.py::approve_design` / `::resolve_design_action` / `::authorize_plan` + `novel/orchestrator/queue.py::plan_book`） |
| R-5.6 | 计划不复制 `semantic_contract`；需要它的 Context 与 API view 每次从当前同号 Stage2 blueprint 派生。Stage4A 每个单章响应先独立校验本章 scope/alias/schema/动态语义并写 checkpoint；策略与全部章节齐全后，代码才还原正式 alias、按章排序、注入派生字段并聚合整书候选，再重跑整书 strict schema 与全部 HARD。每个 land 原子和 material 恰落一个新场景；`context_fact_refs` 只能引用**此刻读者已经知道**的事实（`obligation=context` 的原子，或已在**更早**场落地的 land 原子）——本场或后续才落地的一律红，那是提前引用未披露事实；context 可跨场复用；引用只限所在计划章；首尾 goal 落在新首尾场景；**章首输入与章尾输出闭合恒为 HARD**——章尾比的是「本章有没有让它成立」（落地即成立），不是「末场有没有把它再抄一遍」；章内状态承接按 `transition` 说的**故事时间**判，且**由代码派生而不是问模型**：模型只交本场新增的最小状态，编译器按 `transition` 的定义补全（首场承接章首状态、`continue` 承接紧邻上一场有效 exit、唯一无歧义支路的 `return` 恢复锚点有效状态），于是这三支成为**后置条件**——`--apply-edit` 的人工稿与 `--revalidate` 的存量产物不经过编译器，那两条路上它们仍是真守卫。支路配对不唯一时不派生，只报 `unver`。判据本身一条不撤：只有 `continue` 强制承接紧邻上一场退出状态，唯一无歧义的 `earlier|parallel → continue* → return` 支路检查锚点恢复，其余非线性边界（第二个 opener、支路内 `later`、章末未收口）产 `unver` 并把逐项证据交结构人审，**不得洗成 PASS**；Stage2 因果边两端事实必须落进实际场景，端点可由 land 或 context 引用入场、多次出现取首次可见场 | `HARD`（`fact_ref` / `fact_coverage` / `causal_closure` / `state_closure` / `material_coverage` / `goal_landing`） |
| R-5.7 | Stage4A 策略响应只产全书 `reconstruction_strategy` 与完整章权重表；每章响应由请求身份注入章号与冻结权重，只产本章 `retain/reconstruct` 决定、三个 local-alias goal、brief 与按新披露序排列的非空候选 `scenes[]`，不回显章号、权重或全书策略。代码把 local alias 映回正式稳定 alias，注入 `output_ordinal` / `weight` / `candidate_scene_id` 并按章确定性聚合；只有 N+1 个 unit 全部验证并 checkpoint、聚合后整书 schema/HARD 全通过，才原子写入完整 `blind_candidate.json`，中断、缺章或失败不得写部分正式候选。goal 不存展示 rationale；候选场没有 material/stage refs，不能冒充最终计划 | `HARD`（`empty_chapter` / `budget_int`）+ strict schema（`contracts/schema/stage4.py::BlindBookStrategyResponse` / `::BlindChapterDesignResponse` / `::BlindCandidate`） |
| R-5.8 | Stage4B 才看原文证据、源舞台、人物与必须材料；它只能按 `candidate_scene_id` 回填 `stage_refs` / `material_refs`，人物到场统一从绑定舞台的 `present` 推导，并回显冻结候选完整 SHA-256。B 不得改变 A 的章/场顺序、事实分组、首尾锚点、goal、`retain/reconstruct`、purpose/role/delivery/transition 或 weight；能绑定则返回完整 `bound`。模型可以另报 typed `binding_concerns` 供最终结构人审查看，但 concern 不阻断编译、不能冒充 infeasible。模型返回 `infeasible` 只是一项待核证主张：代码必须在同一输入目录上构造性求解；当前合法约束下，每章至少一项舞台、每场可复用首项舞台、全部材料可唯一落到首场，因此通常可证可绑定，错误的负结论必须在同一 session 拒收并要求完整 bound。只有代码能产出关闭式 `BindingInfeasibilityWitness`（例如章目录缺失或合法舞台集合为空）时才允许正式落盘 infeasible；其余任何自由文本、悬空引用、部分 binding 或静默重规划均拒绝。另提供 0-wire `stage4.plan --apply-binding <path>`：人工提交同一 `EvidenceBindingResponse` 业务形状，服务端铸造 `origin=human` 来源账并复用同一 compiler/HARD | `结构`（`contracts/schema/stage4.py::EvidenceBinding` / `::BindingInfeasibilityWitness` + `novel/stages/stage4/prompts.py::evidence_bind_system` + `novel/stages/stage4/blind.py::prove_binding_infeasible` / `::validate_infeasible_binding` + `novel/stages/stage4/plan.py::run_apply_binding`）；自由文本泄漏风险仍 **无守卫 → `B-06`** |
| R-5.9 | 场景详略由章级与场级 `weight` 决定，整数预算由代码分配；“多少场算过多、何种详略最好、哪一种顺序更好”没有 ground truth，不设自动审美阈值 | **`人`**；预算硬门见 `R-5.3b/c`，分布见 `R-5.9b`；旧盲区见 `B-17` |
| R-5.9b | 场景预算与原文舞台 span 的分布、章内轻重必须常驻报出；读数不自动升级为质量结论 | `度量`（`scene_budget_distribution`） |
| R-5.10 | A 与 B 各只在自己的中间产物记录来源账与 usage；A 的正式 provenance 聚合本 run 下策略/逐章 N+1 个稳定 purpose 为得到最终获采纳 units 发生的完整成本与实际模型集合，不伪装成一次供应商调用：除 accepted checkpoint 的 turn/recovery links 外，同 purpose 先前无 checkpoint 的失败 session 成本也必须计入，且部分恢复、全恢复、定向修复与 aggregate prepared 尾窗重启都要重建出逐字相同的 usage。逐 unit 的 accepted attempt、provider session、request identity 与 usage 仍由 ledger/session audit/checkpoint 精确联结；定向修复另绑定审批决定 SHA、被替换单元与其基线 unit SHA，其余单元只引用既有 checkpoint，不复制冒充新调用。B 的章级路同样不得伪装成一次供应商响应：`architecture=chapter_baseline_v1` 时，机械章记 baseline、歧义章记实际 accepted attempt/model/request 与 replacement lineage；全机械书模型集合及 usage 为空。旧整书路显式保留 `whole_book_v1`；`--apply-binding` 明确记 human provenance，不伪造模型、prompt 或 usage。最终 `plan_chapters.json` 是代码编译产物，只记 `FreeProvenance(origin=compiled)`，同时绑定 A/B 完整 SHA-256 与实际 consumed inputs，**不得复制 B 的模型/usage 冒充计划自身调用**。人工整份替换记 `origin=human`，不得继续冒充旧 A/B 编译结果。问题稿与 0 成本提升稿同样带来源，ghost input 必须显式报出 | `结构`（`BlindCandidateProvenance` / `EvidenceBindingProvenance` / `PlanProvenance` + `novel/llm/stage4_runs.py::design_aggregate_usage` / `::plan_chapter_*` + ledger/session checkpoint + `novel/prov.py::FreshnessReport`） |
| R-5.10b | **写作上下文照的必须是盘上这份计划**：两处各存一份 goals，手改计划而不重建上下文就会分家。四态分开报——对得上 / 照的是另一版 / 没记这条边 / 上下文不在场 | `HARD` |
| R-5.10c | 校验器要为**两份产物各报一行来源**：报告第一节只覆盖被复检的那一份，另一份的来源结论要另有一行常驻（空也要报） | `度量` |
| R-5.10d | `origin=compiled` 的计划在 freshness、Stage4 全判据复检和正式写盘后置条件中，都必须把 provenance 保存的两枚完整 SHA-256 与盘上**当前 A/B 版本化文件的原始字节**逐一比较；缺失或不符即关闭式阻断，不得因 `artifact_payload` / `inputs[].consumed` 的业务投影仍相同而放行。`origin=human` 不声明 A/B 编译身份，跳过这两条 exact 边但仍走普通 provenance freshness | `HARD`（`phase_artifact_link` + `novel/stages/stage4/phase_binding.py::verify_plan_phase_artifacts`） |
| R-5.10e | A/B 完整字节失配不等于一律需要人工：若同一条普通来源边已证明**声明消费投影**变化，freshness 汇总必须保留 `consumed_changed`，让登记 producer 按 canonical 动作重建；只有消费投影仍相同、而未消费字节或格式单独变化时才是 `file_changed`，继续关闭式停在人工决定。两类都仍由 `R-5.10d` 的 exact SHA 闸阻断，不得把“可确定重建”误报为“仍然 fresh” | `结构`（`novel/store.py::_stage4_plan_phase_freshness`）+ 业务投影变化/仅空白变化正反例回归 |
| R-5.11 | 编译参数、fact atom 数、源舞台数与结构算法版本均可由当前代码和上游现算，不进入计划或 provenance；结构读数必须现场计算，算法读数变化不得改写计划字节 | `载体`（strict schema + `novel/stages/stage4/measure.py::structure_metrics_of`） |
| R-5.11b | Stage4 正式产物仍为 `blind_candidate.json` v2、`evidence_binding.json` v3、`plan_chapters.json` / `plan_edit.json` 与 writer context v4；外部 typed 请求新增 `blind_chapter_edit.json` v1。identity artifact 退役。旧版本关闭式拒读、不迁移、不手补字段：正式计划由 `stage4.design` → 设计审阅 → `stage4.plan` 重建，writer context 只在最终计划结构审批后由 `stage4.context` 免费重建；人工计划恢复分别提交当前 `BlindChapterEditRequest` / `EvidenceBindingResponse` / v4 `PlanEdit`，都复用生产 schema、动态 validator 与全书 HARD。Stage2 v6 旧书不因本条自动迁移，v7 关系契约只在 clean base 由 producer 重建 | `载体`（`ARTIFACT_SPECS` + `tests/test_artifact_versions.py::_SHAPE_BREAKS`） |
| R-5.12 | land 原子不得重复落场或漏场；同一个复合源 beat 可以拆成多个原子分给不同场，场景也可合并不相邻原子 | `HARD`（`fact_coverage`） |
| R-5.13 | 一场的预算不许被必须逐字写入的原话挤满：低于 2 倍只提示，不自动改场或改事实 | `INFO`（`verbatim_starved`） |
| R-5.14 | 原话占场景预算的分布要无条件报出；阈值没有独立质量真值，读数不作放行门 | `度量`（`verbatim_share`） |
| R-5.15 | 结构变化必须从同一个逐章函数复算且带分子、分母与 `unver`：事实→场景分区用全部 land-fact pair 的 split/merge 距离；披露顺序只比较 SourceSpan 严格可排序的 fact pair，并把同场 tie 与逆序分开；非连续重组按同一新场覆盖多少段互不相邻 source locus 计算。事实映射不闭合、SourceSpan 不可判或分母为零时不得产漂亮比例 | `度量`（`partition_change` / `disclosure_order` / `noncontiguous_regroup`，唯一数学落点 `novel/stages/stage4/measure.py::structure_metrics_of`） |
| R-5.16 | `causal_edges` 约束故事成立顺序，不等于披露顺序；披露先于因果前提可能是合法倒叙，必须报逆序读数但不自动阻断。先后一律按端点的**首次可见场**算（`context` 可在多场复用，取最后一场会让先后取决于数组尾部） | `INFO`（`causal_disclosure`） |
| R-5.17 | 场景 `transition` 必须是关闭式可执行标签，首场必须 `start`；回溯/返回的审美是否合适无 ground truth | `HARD`（`scene_transition`）+ 人工盲读 |
| R-5.18 | 首尾锚点必须同时报 typed goal 相对源首尾 locus 的迁移，以及首/末新场 payload 是否仍含源首/尾 locus；另单报每章 source locus/源分组数、跨组可比较事实对、因果传递闭包约束/不可比对与非连续重组机会，作为“本章是否有重构空间”的保守分母。它们不以场景数量变化、合并两个相邻场或字面低重合冒充重构，也不自动判优劣 | `度量`（`anchor_migration` / `structure_eligibility`） |
| R-5.19 | 每章必须显式记 `structure_decision`：`retain` 的声明变化为空；`reconstruct` 必须从 `entry_anchor / ending_anchor / fact_grouping / disclosure_order / noncontiguous_regroup` 中声明至少两类。计划只存 mode/reason/declared；`computed_change_kinds` 每次从当前 blueprint 与实际场景现场计算，声明已实现、未实现或出现未声明变化常驻对证，但不回写计划，也**不把“至少两项实际变化”或整书距离写成未经标定的自动美学门**。事实/状态/因果闭合仍由 HARD 守 | `度量`（`structure_alignment` / `semantic_closure_reading`） |
| R-5.20 | Stage4 有两次相互独立、主体不同的人审。第一次是 `R-5.5` 的 A 设计审阅，只决定候选是否可交 B；第二次发生在 B 已绑定并编译计划、机械检查通过之后：服务端给出包含计划业务面、现场结构对证读数及 B `binding_concerns` 的完整结构 workpack 与 opaque token，人只回交 token、actor、总理由以及**恰好覆盖当前全部章**的 `accept / request_replan` 与逐章理由；完整 subject/计划字节/证据 SHA 由服务端重建并持久审计。coverage 是独立人审动作槽，可以发现工作包未预登记的真实语义问题；但 `request_replan` 必须指出具体 scene/fact/goal 的结构损害，INFO、metric、UNVER、`binding_concern`、声明—实算差异及 `allowed_actions=[]` 的行只有披露/定位权，不能单独制造付费动作。全部 accept 才打开 s4；任一 request_replan 只保存决定并保持 s4 关闭，绝不自动启动或付费重规划。其后 operator 发布独立的 Stage4A resolution blocker，Commander 交叉读取 B 的逐章拒绝证据与当前 A 工作包并二选一：若能独立确认具体结构损害，提交新的 current typed A `request_repair / request_replan`；若现有 B 拒绝仅由只读材料支撑且没有具体损害，提交 hash-bound typed stop，安全结束本轮且不写 A/B、不改 gate、不签 token、不调用 Executor。不得重复审或用同一 subject/token 改写 B、以 all-accept 绕过拒绝、把 B 自由文本直接交给 Executor，或为关闭 blocker 发明 A 约束；只有新 A 决定真实落盘后，确定性 Supervisor 才按 opaque token 精确恢复 `stage4.design`。同一 current A/B 业务 subject 的决定一经落盘只允许 exact typed replay；proof 也未变时纯幂等不写，只有完整 artifact provenance 字节变化但业务 subject 与外部 typed payload 逐字段相同时，才可保留旧 history 并追加一条绑定当前 proof 的同语义记录，不能用旧 token 改票。A 的 proof rebound 不改 B 或 s4；B 的 rebound 只按原逐章决定重签 s4/proof。唯一不同 payload 的后续写入，是 current A 已 accept 且 current B 为 request_replan 时另交一份非 all-accept A resolution。`STRUCTURE_REVIEW_CONTRACT` 与 `DESIGN_REVIEW_CONTRACT` 均进入 subject，契约升级使旧决定保留在 history 但自然 expired。通用 `/gate/s4` 必须拒绝；决定同时绑定结构 subject 与当前计划完整字节，业务计划、上游结构证据或 concern 变化后即时 expired；纯 provenance proof 漂移只开放上述 exact rebound。计划的生成、`--revalidate`、`--apply-edit`、`--apply-binding` 均不得顺带生成 Context；只有当前最终结构决定全部接受后，`stage4.context` 才能单独、0 成本落盘。Stage5 的任何付费写作在首个 LLM 调用前必须同时验证当前 s4 结构批准、fresh Context 和独立 writing 授权；0 成本 check/人工编辑/阅读面不得被挡 | **`人`** + `结构`（`contracts/schema/api.py::DesignReviewRequest` / `::StructureApprovalRequest` + `novel/orchestrator/approvals.py::approve_design` / `::approve_structure` / `::authorize_context` + `novel/orchestrator/reviewer_tasks.py::_submission_options` / `::load_action` + `novel/cli/main.py::_cmd_run`）；没有自动审美阈值 |

| R-5.21 | Stage4 的运行时失败必须携带**闭合式 typed witness**：`novel/llm/typed_failures.py::TypedFailure` 九字段冻结（candidate_sha256 / code / rule / chapter_ordinal / scene_refs / refs / repair_owner / retryable / failure_fingerprint）。发射点 → `repair_owner` 的路由表唯一落在 `contracts/invariants.py::STAGE4_FAILURE_OWNERS`，未登记的发射点关闭式归 `code` owner。`repair_owner ∈ {stage4.design, stage2.blueprint, code}` 的失败在 B 首轮立即停止（不排队反馈、不消耗四轮预算）；B owner 回同一 session；同一 fingerprint 集**连续两轮**未闭合提前终止该 unit；`failure_fingerprint` 是语义身份（不含 candidate SHA），换候选重演的同一违约指纹相等。判据在已知章号的发射点必须给 finding 填 `output_ordinal`（章级判据按章拆 finding），typed witness 只从发射点结构化产出，**禁止从 detail 散文反推章节**。三份出口各取所需：完整反馈只回同一 provider；`_service/llm_audit.jsonl` 的 reject_detail 只追加无正文的 `code/chapter_ordinal/fingerprint` 读数；完整 witness 与被拒响应进现有 `_llm_rejected/<chain>/<attempt>/` 恢复面（`repair_eligible=false`，收口走 `--revalidate` / decision-bound repair / hash-bound replacement，不建第二份审计文件） | `结构`（`contracts/invariants.py::stage4_failure_owner` + `novel/llm/typed_failures.py` + `novel/stages/stage4/checks.py::check(witness=)` + `novel/stages/stage4/plan.py::run_plan` 的关闭式路由与 `::_keep_business_witness`）；`tests/test_stage4_typed_failures.py` 正反例回归查 |
| R-5.22 | Stage4A 候选在授权 B 之前必须经过唯一 **A→formal 零调用预检** （`novel/stages/stage4/plan.py::preflight_binding_candidate`）：从当前 A 与 Stage2 关闭目录构造零自由度构造性绑定（每场 catalog 首项舞台、材料全落首场），复用正式 merge/plan ref validator/WriterContext 组装与同一联合 HARD，返回绑定 candidate/blueprint/review-contract SHA 的 typed pass/blocked 结论，**不落任何新 artifact**。「B 选得不好」在该预检里结构性不存在；失败 owner 由 `R-5.21` 闭合表分类，不可行 witness 的两类约束码归 `stage2.blueprint`。调用点四处：A 审阅 workpack生成、approve 闸（current-v7 的 blocked 或 missing 预检拒绝任何 accept；repair/replan 不受闸）、`authorize_plan`（复用现场 subjects 的预检投影）、`prepare_plan_candidate`（任何 driver/probe/provider 副作用前重跑）。唯一兼容例外是 exact frozen-v6：允许把真实 raw all-accept 决定落账，但因不存在 v7 relation/preflight authority，effective design review 必须投影为 `suspended`，不得授权新 B。元数据归一：`computed_change_kinds` 是权威代码读数，`mode/declared_change_kinds` 保留为设计意图只进 `structure_alignment` 度量（metric_only，无 fix 路由、不得生成付费 action）；泛化 `structure_delta` 退役为 `DESIGN_REPAIR_CODES_RETIRED`（历史可解码、新决定 approve 闸拒绝），新修复决定只接受带 scene/alias 的具体修复码；`binding_constraint_unsat` 必须有机械 witness（预检不可行证明或 fresh B infeasible 终态）支撑；`DESIGN_REVIEW_CONTRACT` 升 v4，旧 v1/v2 与 v3 决定经主体相等性自然过期，重审 0 成本；v3 split-capability 历史仍可严格解码，但不取得 current authority | `结构`（`plan.py::preflight_binding_candidate` / `::_preflight_blocked_text` + `novel/orchestrator/approvals.py::_design_preflight_summary` / `::_require_design_preflight_clear` / `::_require_design_codes_active` + `contracts/invariants.py::DESIGN_REVIEW_CONTRACT` / `::LEGACY_V3_DESIGN_REVIEW_CONTRACT` / `::DESIGN_REPAIR_CODES_RETIRED`）；`tests/test_stage4_preflight.py` 与 `tests/test_design_review.py` 正反例回归查 |
| R-5.23 | Stage4B 内部拆为「机械基线 → 歧义章私有 session → 确定性聚合」，公开仍只有一个 `stage4.plan` 节点、一次预览和一次授权，不开放模型 `--chapters`。唯一机械基线落点 `novel/stages/stage4/baseline.py::build_mechanical_baseline`（纯函数）：scene fact → `source_beat_ref` → 舞台 allowlist，material → 属主 beat → eligible 场景；唯一归属由代码确定，签名等价的多候选取规范首项；只有 `stage_signature_conflict` / `material_multi_scene` / `material_unplaceable` 三类真自由度生成内容寻址 ambiguity id。机械章 **0 wire**（绑定由冻结输入确定性重算，不落章级 checkpoint——其价值是免重付模型调用，确定性重算零成本）；歧义章各占一条 single-session run/checkpoint 链（`stage4_runs.plan_chapter_run_spec`，`_service/runs/<sid>/stage4/<run>/plan/chapters/chNNNN/`；旧书内 `_stage4_runs/<run>/plan/chapters/chNNNN/` 仅作只读兼容），真实 worker 池最多 `STAGE4_PLAN_MAX_CONCURRENCY=4` 并发，任何两章不共享 driver/logical/provider session；章 prompt 只含本章候选场景、本章相关原文/hinge、blueprint、关闭目录与基线歧义 + 相邻章一行边界摘要，**不含其他章正文**；模型只返回 `ChapterBindingResponse`（歧义逐项闭合 + 非阻断 concerns，无章号/hash/完整绑定面）。每个 worker 内按原 wire validator 完成 parse/require/schema/本章闭合，通过即 checkpoint；后章失败不得抹掉已验章，恢复只补未闭合 unit。全章闭合后按章号确定性聚合并过同一 compiler 与整书联合 HARD；typed 失败按 `R-5.21` 路由，B owner 且带章号只回该章 session；重问通过后必须同步重写 checkpoint、accepted attempt/model/request、unit lineage 与 aggregate anchor，不能让正式稿指向旧响应；同一 fingerprint 两轮早停。时间层冲突模型无法给出可执行选择时不采用任意 fallback，保留他章 checkpoint 转人工 typed 替换。该 replacement 必须绑定原 run/request/code/subject 与被拒 candidate SHA，消费一次性封存 resolution，复用同一 `ChapterBindingResponse` validator、compiler 与全书 HARD，0 wire 且不得伪造模型 provenance；整份 `--apply-binding` 仍是另一条明确人工入口。聚合 commit journal 锚定**最终获采纳锚章 checkpoint 链**（`stage4_runs.plan_chapter_commit_spec`——聚合响应是代码编译物不是 provider turn，不得在整书链伪铸 acceptance）。来源账 additive：`architecture ∈ {whole_book_v1, chapter_baseline_v1}` 与逐章 `unit_lineage`（kind 只取 baseline/chapter_session + attempt；人工 replacement 以同一 chapter_session 行的 resolution/candidate/audit 字段留证，不伪造第三种模型 unit）；全机械书落 `origin=compiled` 免费账（0 模型声明）；旧 `EvidenceBinding` v3 仍可读，旧整书单发路径保留且不作 fallback | `结构`（`baseline.py` + `stage4_runs.py::plan_chapter_*` + `node_agents.py::Stage4PlanSessionPool` + `plan.py::_run_plan_with_chapters` / `::_finalize_mechanical_plan` / unit replacement）；`tests/test_stage4_baseline.py`、`::test_stage4_plan_sessions.py`、`::test_stage4_plan_chapters.py` 正反例回归查 |
| R-5.24 | Stage4B 机械基线发现同一候选场的 source facts 对应多个不同 `time_layer/place/present` 签名时，必须为每个 `stage_signature_conflict` 常驻报出章、场、事实与全部候选签名；最终 binding 选中一个候选也不能让其从报告消失。该读数没有自动优劣阈值，只作为结构人审的完整披露面，不阻断编译、不生成付费修复动作 | `INFO`（`novel/stages/stage4/checks.py::check`） |
| R-5.25 | Stage4 两次人审各有一份只读、异常优先的 Commander 工作包 v1；公开面固定为 `GET /api/novels/:sid/approvals/design/workpack`、`GET /api/novels/:sid/approvals/structure/workpack` 与 0 成本 CLI `approval workpack design|structure`。工作包必须绑定当前 candidate/完整审阅 subject 与精确章全集；服务端复用生产 A→formal preflight、Stage4 check/metric（含 `R-5.24` 的完整 INFO）、机械 baseline、binding concerns 及审批历史投影，不在 façade 重拼判据，不预填决定、不自动 accept、不启动模型或作业。六组以 `blocking → semantic_attention → disclosure → metric_only → phase_deferred → coverage` 固定全集顺序出线，空组也保留并给扫描分母；coverage 恰覆盖全章、changed 优先且各段章号升序，changed 与 exact historical accept reuse 互斥并穷尽。条目携闭集 `sev/status/effect/allowed_actions`：actions 只可为 `accept / request_repair / request_replan`；INFO、metric、UNVER、skipped 及非 blocking/coverage effect 恒无动作，不能借工作包制造付费返工理由。Design 面突出 preflight witness、首尾锚点、因果/时间与 repair 章；Structure 面完整投影 B concerns、非线性 UNVER、所有 stage signature conflicts 和结构读数。Context 才能判的 `R-6` 规则只进 `phase_deferred(stage4.context)`，不进入结构 subject。Inbox 同步发布 Stage4 A/B 审批余额；已被 current hash-bound 结构决定实际覆盖的 disclosure 保留为 `reviewed_retained` 并附 typed decision 证据，不洗成 pending，也不从审计面消失。Stage4 工作包 disposition 为 `review_required / already_current / suspended`；第三态只允许 Design exact frozen-v6，表示 raw all-accept 已精确落账、但缺 v7 relation/preflight validation authority，故不得重复唤 Commander、不得发布 Structure approval、`s4rr1`、`s4er1`、paid repair/replan 或 B，operator 只报 legacy validation suspension 且 execution token/steps 为空；clean v7 才能继续。frozen-v6 有真实 `request_repair` 时不投影 `suspended`，只在对应章工作包行发布零 wire `s4er1` 完整章 edit，仍不得发布 `s4rr1` 或付费 spawn。Stage6/7 的同名公开字段继续只接受 `review_required / already_current`。盘上 raw typed 决定不因前置闸暂时失效而删除；免费复检后 subject/contract 未变可恢复 `already_current`，不同 actor/reason/typed 决定不得自动合并。Design 与 Structure POST 均只收各自 workpack 的 opaque `review_token`、actor/reason 与 typed rulings；candidate/aggregate/chapter/contract/validation 等完整 SHA 由服务端在同书锁内重建并写审计，调用方不再搬运。Stage4A 完整章 edit 的独立单 token 见 `R-5.26`。v2：每个 coverage 行携服务端确定性渲染的 `reading`（B：本章 facts 表（id→claim/kind/obligation，每个 claim 只出现一次）、逐场 slot/purpose/role/delivery/transition/budget 与 land/context/turn/entry/exit 的 id、舞台 time_layer/place/present、材料四组计数与摘录、本章 `structure_metrics` 与 alignment、解开 closed_refs 的 binding concerns、signature conflict 交叉引用；A：本章盲真相 facts 表、goal/因果边/关系/入口收束状态与逐场的 alias 及 purpose/role/delivery/transition/weight），顶层携一次 `legend` 与整书策略/全局 concerns；`metric_only` 只保留一行摘要且 `refs=[]`，完整读数以逐章 reading 承载。reading 不进 subject/token/proof，纯增渲染不使既有决定过期；Commander 逐章裁决只读 reading，不需要也不得读取盘上产物 | `结构`（`novel/stages/stage4/reading.py::structure_chapter_reading` / `::design_chapter_reading` + `contracts/schema/api.py::ApprovalWorkpack` + `novel/orchestrator/approvals.py::design_workpack` / `::structure_workpack` + `novel/api/routes/approvals.py` + `novel/cli/main.py::_cmd_approval` + `novel/views/inbox.py::inbox_view`）；API/CLI/schema/投影/Inbox 正反例与前端 seams 查 |
| R-5.25b | RunSupervisor 为 Stage4 reviewer 组包时必须在**一个 Python 进程、同一份 operator overview/code 与一次调用栈只读 memo**内完成，首尾以 active surface seal 做 CAS；不得为 design workpack、template、structure workpack 与 approval show 串行启动多棵 CLI 并各自重扫仓库/书面。B→A 回审给 Commander 的阅读面固定为“完整当前 A + 当前 B typed 决定 + 仅 request_replan 章/全局项的 B delta”；完整 B workpack 仍以 canonical SHA-256 绑定业务身份和审计。Commander 回交的策略/章/约束集合先作规范排序再进 typed schema，重复项仍必须拒绝，不能用排序顺手去重或改写引用顺序。B→A 回审 delta 中被点名章保留完整 coverage `reading`；A 工作包的 reading 由内存中的 subject 直接渲染，B 工作包的 reading 与 plan/binding 同一次快照读出当前蓝图，不另开读路 | `结构`（`novel/orchestrator/reviewer_tasks.py::_stage4_workpack` / `::_stage4_structure_replan_materials` / `::_canonicalize_design_review_payload` + `novel/orchestrator/approvals.py::read_projection`）+ 单进程/双 seal/delta/重复拒绝回归 |
| R-5.26 | Stage4A 返修的 current 契约固定为 `stage4-design-review-v4`；v4 延续 v3 的 split-capability 与 repair 载体，v3 仅作历史严格解码、不再取得 current authority。每轮 Executor 必须同时收到本轮具体 finding 与此前全部尚未关闭的 typed constraints，关系约束与 Stage2 的 `story_before / response_to / same_scene / observes` 同构；自由文本 reason 只进审计，不能混入 prompt。每份 hash-bound repair/replan 动作都是 producer 边界：RunSupervisor 在执行 `recovery:stage4-design-action` 前按 exact action 身份只滚一次 `reason=continue` 的 fresh execution epoch，重读 operator next/CAS 后才启动 Executor，不重复调用 Reviewer；修前 aggregate producer 与修订 producer 不得相同。一个 repair epoch 最多两次模型内容提交；耗尽只关闭该 epoch，允许同策略、同章的新 repair epoch，或以 `BlindChapterEditRequest` v1 对当前候选中的一章做完整响应替换，绝不自动升级为全书 replan。人工计划必须先 preview 后 apply，两步共用同一 compiler、schema、动态 validator 与全书 HARD；public request 只含 opaque `s4er1_…` review token、actor/reason、目标章号与完整章响应，服务端由 token 现场重建并核对 current candidate、aggregate/chapter subject、design decision、strategy epoch/chapter strategy 与 constraint set。只改目标章，非目标章业务字节恒等，provenance 明记 `origin=human`，不伪造 prompt/model/usage，也不新增正式展示 artifact。真正策略驳回才允许 replan；新策略请求必须携累计策略/章节失败和旧 accepted 章约束，只有 effective strategy/input digest 改变的章节才可重发 wire | `结构`（`contracts/schema/api.py::DesignRepairConstraint` + `contracts/schema/stage4.py::BlindChapterEditRequest` + `novel/orchestrator/agent_supervisor.py` + `novel/stages/stage4/plan.py` / `novel/stages/stage4/design_evolution.py` 的 accumulated constraints、repair epoch、preview/apply 与 effective-input checkpoint 复用）+ 新 producer epoch 重读 exact action、第二轮继承首轮约束、零 wire 人工替换、非目标章字节恒等、真策略 replan 反例回归 |
| R-5.26b | Stage4A typed targeted repair 在执行前必先进入 fresh producer execution epoch；代码/skill 漂移同样不得把旧 provider session 当 live resume。新 producer run 可在首个新 wire 前采用另一个 ended run 的**只读已验章响应**。仅限本次 repair 目标章，不含策略 unit、普通首轮或 strategy replan；source 与 target 的 resolved profile、完整请求、输入、schema、token ceiling、恢复协议必须逐字段相等，source checkpoint/session/attempt/ledger 与当前动态 validator/业务状态必须全部闭合。只在恰有一个历史 run 提供唯一最大合法章集时写 immutable `design/historical-checkpoint-adoption.json`；零个或并列来源都不猜。采用不复制 revision、不注册或续接旧 provider session；正式 `DesignUnitLineage.origin=historical_checkpoint` 同时绑定 source attempt 与 manifest SHA，aggregate usage 按 source/target run 分段精确合并，manifest/source/target 任一字节漂移都关闭式拒绝 | `结构`（`novel/llm/stage4_runs.py::restore_or_select_design_historical_adoption` / `::design_repair_sources_usage` + `novel/stages/stage4/plan.py::_run_design_repair`）+ 同 parent 动作边界、跨 code 零 wire、profile 漂移拒绝、无 revision 复制与账面联结回归 |
| R-5.26c | Stage4A repair/replan aggregate 不得把 Python 进程或原 producer 存活当作提交原子性。任一正式候选变更前先在 source run 写自哈希 `design-repair-aggregate` v3 prepared journal，冻结 source/producer-parent/code、repair|replan scope、parent/decision/targets、完整 unit source、候选 baseline/target、整份 typed provenance 与 finalization。source execution epoch 结束后只能由新 recovery run 执行 FREE/0-wire `stage4.design --recover-repair-aggregate <source_run_id> --source-journal-sha256 <raw_sha> --source-commit-sha256 <prepared_self_sha>`：不解析已 stale review token，不构造 profile/client/session/checkpoint/wire，不恢复旧 provider。operator 在 approval/inbox/付费 planner 前只接受全书物理根下唯一可验 prepared；service/legacy 双副本、多份或任一其他 Stage4 prepared 都关闭。Stage2 与 Stage4 两类 prepared 同时存在也是不可能双所有权，不得按顺序猜 winner。恢复在任一候选写前必须证明 source Agent epoch 已 `drained` 关闭且 parent ended（direct CLI 只允许 producer-parent=source），并持有 owner-stopped lease；逐格复验 source checkpoint/session/attempt/ledger/code/request，用当前 compiler 从响应重建候选，并要求重建后的 usage、model、prompts、inputs、root/truth、unit lineage 与 adoption provenance 连同业务体逐字等于 sealed typed payload，再过当前 truth/input/schema/HARD。live candidate 只允许 journal baseline 或 target；第三态拒绝。候选写前先对 source adoption、source-stable canonical recovery relation 与全局唯一的 per-target run/parent attempt relation 作 0-or-exact preclaim；帐尾非 LF、多行或漂移都不得先改候选。baseline 才写 sealed target，target 只验证；幂等补 adoption/recovery audit 后最后原位将 source journal 提为 committed。再杀于 prepare/候选/adoption/audit/mark 任一窗口都必须由下一新 run 零 wire 收敛；已 committed 的下游读取仍逐字要求 adoption、canonical relation 与 target attempt audit，不得删账后继续消费 | `结构`（`novel/llm/stage4_runs.py::inspect_prepared_design_repair_aggregate_recovery` / `::preclaim_design_repair_aggregate_recovery` / `::mark_design_repair_aggregate_committed` + `novel/stages/stage4/plan.py::run_design_repair_aggregate_recovery` + `novel/orchestrator/operator.py` / `execution_permit.py` + `novel/cli/main.py::_run_stage4`）+ repair/replan×baseline/target、物理双副本/多 prepared/第三态、source ended/ledger/session、完整 provenance 重建漂移、无 LF、adoption/audit 唯一与各 hard-kill 写点回归 |
> **2026-08-25 替换边界**：旧 `end_beat → from_beats → events[]` 链已整体退休，
> 不是在 `PlanBeat` 上加字段。current Stage2 Blueprint 是事实唯一源（新 base 为 v7，旧书冻结 v6）；Stage4A 先脱离源序设计，Stage4B
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
| R-7.12 | 篇幅超上限时不许由模型**自动**裁剪：`length_over` 只会零调用保留 pretrim、落 current `TrimAudit` 并转人工，不能自行生成、挑选或采用较短候选。付费生成、重采、人工整章写回、Reviewer 明确授权的单章 LLM 重写、`--revalidate` 与 `--recheck` 的 canonical 提交共用同一个 trim 对账口；新 `.INVALID` / rejected 未替换旧 canonical 时不得改动旧稿的 audit/resolution，正文未变且人工决定仍匹配时必须保留 | `结构`（`novel/stages/stage5/write.py` 的 canonical 提交与 trim 同步函数）<br>⚠ 这里曾有「同槽候选生成 + 确定性 claims 对账 + A/B 双盲 + 采用」一整条自动收缩路，由 `trim_equivalence` 的 evaluator profile 开闸。那个闸从未在任何宿主上打开过，现已随 profile 整批下线；`R-7.31` 的新路由是人先选中一章和关闭式指令、再由写手重写**完整章**，不是旧候选臂复活，也不由 `length_over` 自行触发 |
| R-7.13 | 超上限章的最终处置权属于人：可以保留 pretrim、提交人工完整章，或让受托 Reviewer 对**恰好一章**提交关闭式 LLM 改写请求。同一 fresh Review 下仅有多章 trim 待销账时，一席 Reviewer 必须在三类互斥出口中择一：覆盖全部 current unresolved 项的 typed retain full-batch、某一章的 typed rewrite request、或 typed stop；Supervisor 不得逐章代选、替人把自由理由变成 prompt，或为同一证据分别购买 retain/rewrite 席位 | `人`：`novel/stages/stage5/trim_resolution.py::retain_pretrim` / `::retain_pretrim_batch` 写 hash-bound resolution（0 次调用），`stage5.write --apply-edit` 落人工新轮，或按 `R-7.31` 先落正式 Reviewer rewrite request 再执行唯一 paid writer。typed stop 不落 resolution/request。⚠ `TrimResolution.action` 仍只有 `retain_pretrim`；系统不会替人采用一个预先生成的较短候选（`B-11` 仍关闭） |
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
| R-7.31 | **Reviewer 授权的 LLM 改写先落正式意图，再由独立 fresh epoch 消费；它不是 `--revise` 或裸 `--chapters`。** pure-trim 顶部卡的 typed 请求必须绑定同一 fresh Review 原始字节、原完整有序 unresolved audit/chapter 双 SHA 与 target canonical 完整文件 SHA；frontier 的 `chapter_sha256` 始终是正文 SHA，target 文件 CAS 另用 `target_chapter_file_sha256`，两域不得互换。每份 v2 批请求用 `targets[]` 选择有序非空子集，各章分别下达指令；未选章保持 unresolved，不随 request 形成 retain 或销账。多章可独立安全修复时按 `STAGE5_WRITE_MAX_CONCURRENCY=4` 并发改写，整批完成后统一免费 Review；必须跨章联动或超出关闭式指令的修复仍 stop。directive 只取 `remove_repetition｜compress_exposition｜repair_wording｜smooth_transition`；每条 evidence 非空、不超过 240 字且在 current target 正文中恰好唯一命中。actor/reason/evidence 只作审计和定位，writer prompt 只消费登记 code 的固定指令，并硬带“压回预算上限、保留全部 plan slots/事实/时序/必须逐字内容”。Supervisor 先以 FREE `stage5.review --request-reviewer-rewrite @reviewer-input` 写 `stage5/reviewer_rewrite_request.json` 并 typed readback，再把原 ReviewerActionRecord 原始字节与绑定 request raw/self、stable business、当时 Reviewer contract、原 session/seat/provider response 及 seat/session/cost 三账摘要的 self-hashed receipt 封入 `_service/runs/<sid>/reviewer_action_authorities/`；receipt 是 commit point，之后才退休 active record。queue、permit 与 writer 只读复核 sealed authority；历史 terminal/expiry 按封存当时 contract 解读，新的 paid claim 与 active checkpoint recovery 还必须要求 contract current。queue 在 stale Review、closure 与新 Reviewer 前优先发布 `recovery:stage5-reviewer-rewrite`，滚新 execution epoch，并只允许 PAID `stage5.write --reviewer-rewrite stage5/reviewer_rewrite_request.json`；旧 fixed-path 批 request 仅在所有单元 landed formal 或已有合法 successor/expiry receipt 时可换代，ready/unknown/recoverable/活动 INVALID/rejected 不可覆盖。permit、CLI 与 producer 在任何 provider/计费/session/cache/wire 副作用前共同复核 current writing/egress/profile/code、request 原字节、Review/audit/chapter/contract 与 sealed Reviewer 来源；手写 JSON、旧 stop、旧席位或孤儿 CLI 不能取得权限。paid/direct CLI 与 FREE recovery/retirement 全程持批 request lease，再按章取得各执行单元 lease；worker 自己持有线程本地单元 lease，不能继承父线程的锁。同批各章使用自哈希单元身份与独立 checkpoint；已提交兄弟章只有 exact formal execution 证明才允许其 Review 输入边过期，其他漂移仍拒绝。聚合状态先收口 recoverable，再阻断 unknown/negative，仅 ready 单元可生成，所有单元收口前不开新 Review；append-only 自哈希 journal 先写首轮 `started`，每次后续 turn 先写绑定上一 checkpoint 的 `retry_started`，再允许 checkpoint SHA 随新 acceptance 推进，故只有最新 intent 的 exact checkpoint 且其后无新 session/cost/wire 迹象才可恢复。commit SHA 仅在 `delivery_prepared→landed_formal` 可随 generic commit 更新：旧摘要必须等于同一 committed envelope 的 prepared 投影，新摘要必须等于已验 committed self SHA，其余变化拒绝。仅原 parent stopped 且全根 session/cost/checkpoint/commit/WAL/wire 为空的 pre-wire chain 可写 `reclaimed_unstarted` 后重 claim；其余 started/wire 无可验最新 checkpoint均保持 unknown、禁止再次付费。queue 投影关闭为 `ready_paid / in_flight_or_unknown / recoverable_checkpoint / recoverable_prepared / landed_formal / landed_invalid / landed_rejected / expired_unstarted / consumed_resolved` 九态；matching checkpoint/prepared 只经 `--recover-reviewer-rewrite-prepared <SOURCE_RUN_ID> --reviewer-rewrite-request-sha256 <SHA256>` 零 driver/session/cache/wire补尾，若已有 target WAL 还必须原样携 `--reviewer-rewrite-recovery-target-run-id <TARGET> --reviewer-rewrite-recovery-journal-sha256 <SHA256>`，不得另造 WAL。未开始却已过期只经 `--retire-reviewer-rewrite-request ... --reviewer-rewrite-request-sha256 ...` 落 immutable disposition。formal/INVALID/rejected 都须由 execution journal + exact typed carrier证明 terminal consumption；terminal 后的 `--recheck / --revalidate / --apply-edit` 先冻结 request-specific successor prepared WAL 与 terminal 原字节，再执行既有 0-wire 事务，最后写 committed receipt，硬杀后只按 exact landed successor 补尾，手工搬进 history 不能冒充解决。整批 formal 改稿使旧 Review/closure 自然过期，下一步只能统一免费重跑 `stage5.review`。canonical request 当前只接受 v2 `ReviewerRewriteBatchRequest`；内部 v1 `ReviewerRewriteRequest` 仅作章执行单元，不再登记为独立 artifact。旧 v1 不自动转换：须先在原版本按原执行账收口，之后由 fresh Commander 决定与 FREE producer 重建 v2；未知执行不得删除或手补为新授权 | `结构`（`contracts/schema/stage5.py::ReviewerRewriteBatchRequest` / `::ReviewerRewriteExecutionJournal` / `::ReviewerRewriteRecoveryJournal` / `::ReviewerRewriteDisposition` + `contracts/schema/orchestration.py::ReviewerRewriteAuthorityReceipt` + `novel/orchestrator/reviewer_action_evidence.py` + `novel/stages/stage5/reviewer_rewrite.py::record_reviewer_rewrite_request` / `::reviewer_rewrite_execution_state` + `novel/stages/stage5/write.py::run_reviewer_rewrite` / `::run_reviewer_rewrite_prepared_recovery` + `novel/llm/stage5_runs.py::reviewer_rewrite_execution_ownership` + `novel/orchestrator/queue.py` / `execution_permit.py` / `agent.py`）+ request/authority/九态 journal/多轮 intent/并发 claim/全硬杀窗/合法后继/零重复 wire 回归测试 |

> **审批版本边界**：`_service/approvals/<sid>.json` 的现役盘上形状是
> `_ApprovalRecordV7`（`v=7`）：七闸不含 s3；Stage4A 的 typed `design_decision/history` 只授权
> 下一次 B，不打开 s4；B 编译后的 `structure_decision/history` 才能打开 s4。`writing` 必须与绑定
> 当前 Stage1 正文、Stage4 Plan、经最终结构批准后单独生成的 current WriterContext 及测量契约的
> `writing_proof` 成对，三项授权不能互相推出。对外 `ApprovalDoc` 是 **v6**；这是 API 响应版本，
> 不是盘上版本的别名。旧盘 v6 及更早记录在普通 GET/启动时一律拒读，只有显式
> `backfill_from_disk` 可逐项对证后保守收进 v7；其中 v6→v7 只保留仍符合现役 proof 的
> s0～s4、seen 与 design/structure/writing typed 历史，清除 s5～s7，绝不倒造 Stage5 typed
> 闭合回执。旧 read proof 缺完整 Blueprint workpack binding 时自然失效，backfill 不制造任何
> seen 或 s2 人审；更早迁移
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
| R-8.15 | `stage5.review --retain-pretrim` 必须显式提交非空 `--actor` / `--reason`，并用当前章主体 hash 锁定 resolution；`--retain-pretrim-batch` 同样要求非空 actor/顶层 reason，且每项有非空 effective reason 并绑定 current audit/chapter 双 SHA，不允许默认空凭据或由 Supervisor 代填决定 | `结构`（`novel/stages/stage5/trim_resolution.py::retain_pretrim` / `::retain_pretrim_batch`）+ CLI/ArtifactStore 后置条件回归 |
| R-8.16 | 字面 secret timeline 以 Stage2 `first_reveal_chapter` 为权威读者时点；Stage4/5/6 必须共用 `novel/core/facts.py::first_reveal_chapter`，不得自造兼容字段或第二套判定 | `结构`（`novel/stages/stage5/checks.py::fidelity_findings`）+ 时线回归 |
| R-8.17 | **Stage5 只有一份强制、hash-bound 的整书闭合回执；同一 `approval:stage5-closure` 顶部卡按 current closure 状态发布互斥动作。** 只读 workpack 必须绑定当前规则指纹、fresh Review 的完整文件 SHA、全部计划内 canonical 章的完整文件 SHA，以及 current trim audit/resolution 与 wontfix 处置面的稳定完整摘要；构包前后必须重验同一 Review 文件并对全章、活动 `.INVALID`、trim/wontfix 处置面做完整双采样，任一中途漂移都不得返回新旧混合包。全部 Stage5 canonical/INVALID/recheck/revalidate/Review/wontfix/trim writer 与 closure submit 必须共用同一个位于 `<base>/_service/` 的线程+跨进程书锁，逐章提交再按序取章锁；closure 二次构包至 approval/gate 原子写回之间不得有 writer 尾窗，API 不得因取锁写书目录。只有 Stage5 完全未开始且无历史时可为 `missing`；已有稿件/历史时 Review 缺失、过期或不可证明 current，状态只能是 `review_required`，不发布 current workpack/decision 但保留 history。fresh Review 下若仍有缺章、活动 `.INVALID`、当前 HARD 或未销账 trim，workpack 必须优先为 `blocked`；其中**仅有 unresolved trim** 时，同一卡的一席 Reviewer 只可互斥选择：覆盖全部待销账章的 typed retain full-batch、恰好一章的 typed LLM rewrite request、或 typed stop，绝不给 closure approval，也不由队列自动 retain/rewrite；缺章、活动 `.INVALID` 或 HARD 与 trim 并存时在开席前 stopped/2 并指向各自恢复面。只有 accepted 面才可进入 `ready/closed/expired` 并只发布 closure receipt。`must_appear_missing` 只接受逐项 `confirmed_present` 或理由非空的 `accepted_exception`；fact landing 与其余 UNVER 只按完整 finding-set digest 整组确认，未登记的 actionable 规则关闭式失败；历史预填只复用 finding SHA 与 chapter SHA 都未变的逐项裁决。workpack 的 `unresolved_trim_items[]` 必须精确投影每章 audit SHA + 正文 SHA；retain batch 只能原样复制完整有序全集并逐项给非空理由，rewrite request 也复制同一完整 frontier，但只选一章和 `R-7.31` 的关闭式 directive/evidence。trim Reviewer 的稳定 business 统一绑定 `action_mode=trim_disposition`、同一 Review 原始 SHA、原始全量有序 audit/chapter 双 SHA 与 reviewer contract，刻意不绑定会随处置落盘变化的 closure token/workpack；retain/rewrite 不能拆成两个席位，closure receipt 仍以 `action_mode=closure_receipt` 绑定 opaque token。retain producer 在书锁内全量预检并于首写前复采；中断后只有同一 manifest 的连续 exact 已落前缀与 current unresolved 完整后缀可零 Commander 重放，前缀决定须同 actor/reason/双 SHA，后缀不得漏、夹、重排，坏 audit inventory、路径/章号或任一身份漂移都保留 durable 证据并停止。rewrite 回答先按 `R-7.31` 零成本落正式 request，之后其 pending/unknown/prepared/terminal 执行态在 stale Review、closure 与新 Reviewer 前收口。纯 `retain_pretrim` 不改正文或 Review 主体，批次完成后沿用同一 fresh Review，只以 current 处置摘要重建 closure workpack/token；rewrite 正式改稿后必须先免费重跑 Review。`gate.s5` 只能由 current typed closure 打开，匿名 `approve_gate('s5')` 拒绝；Stage6/7 写效果 CLI 与最终 assemble 共用 `require_stage5_closed` 硬闸，0 成本检查、改稿、trim、request 与回执恢复入口不得被反向封死 | `结构`（`novel/stages/stage5/closure.py::closure_workpack` / `::closure_view` / `::require_stage5_closed` + `novel/stages/stage5/trim_resolution.py::retain_pretrim_batch` + `novel/stages/stage5/reviewer_rewrite.py` + `novel/orchestrator/reviewer_tasks.py` + `novel/orchestrator/agent.py` + `novel/orchestrator/approvals.py::approve_stage5_closure` + `novel/api/routes/approvals.py` / `novel/api/routes/edits.py`）+ Stage5 closure/trim/rewrite、Reviewer durable replay 与 CLI 闸回归 |

> **`R-8.17` 的首写前复采只是最低门槛，不是后续写入的豁免。** batch 每准备写下一项时，
> 仍须在同一书锁内重验完整 manifest 是连续 exact 已落前缀 + current unresolved 完整后缀，并复核
> 该项 audit、正文与既有 resolution；首项落盘后出现的删除、漂移或 foreign 决定必须在下一写前
> 关闭式停止，绝不能用批次开始时缓存的 facts 覆盖。

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
| R-9.25 | Stage6 定稿必须先读取只读、异常优先、hash-bound 的公开 v3 workpack：公开面为 `GET /api/novels/:sid/approvals/stage6/workpack` 与 CLI `approval workpack stage6`。同一包完整覆盖三候选正文、机械异常与度量、A/B 各自 verdict/reason/evidence、两份原始 ranking，并在内部区分 review subject/contract 与当前 validation seal；不自动 pick、不把合并 UNVER 洗成 PASS。调用方只提交刚读包给出的 opaque `review_token=s6r3_…` 与 actor/reason/pick，不能手搬 subject/workpack/code/rules 多枚 SHA；公开包只输出 opaque seal，不输出 `code_version` / `rules_fingerprint` 原料。服务端在同书锁内重建同一包、核 token 与 current validation seal，任一漂移关闭式拒绝；旧 v2 包与 `s6r2_` token 只读，不接受新批准。raw typed 批准只由 review subject/contract 判断适用；代码或规则变化先投影为 `suspended`，免费复检通过且人实际看到的内容/finding 未变时自动复活，不要求重签。approval/current 字段不进入 review identity，批准本身不自废；只有同一 raw 决定 effective current 时 `disposition=already_current`，Commander 幂等跳过，不同声明不自动合并 | `结构`（`contracts/schema/api.py::Stage6ApprovalWorkpack` + `novel/stages/stage6/hook.py::approval_workpack` + `novel/views/stage6.py::stage6_approval_workpack` + `novel/api/routes/approvals.py::get_stage6_workpack` + `novel/cli/main.py::_cmd_approval`）+ hash-bound `人` + opaque-token、raw/effective、自动复活正反例回归 |
| R-9.26 | Stage6 prompt 的无损瘦身与前缀缓存只开放为显式实验臂：默认 `pretty-v1` 与零错峰的实际请求字节必须不变；`compact-v1` 只删 JSON 语法空白，不排序键、不改字符串/数组/字段；layout 必须进 Hook provenance 与 Semantic decoder fingerprint。A/B 继续使用互不可见的独立 session 并并发提交；可选错峰只在 B worker 内、上限 10 秒，不得为追求 cache hit 串行两名判官或共享 session。静态前缀 observation 只证明真实 driver stdin 的公共字节，不保证 provider 命中；命中/成本唯一认分角色 ledger usage。任何默认切换、字段投影、模型或 effort 变化都须先过干净缓存的多书质量回归，不以单书墙钟替代质量与稳定性证据 | `结构`（`novel/stages/stage6/prompts.py::dump_prompt_json` / `::prompt_json_layout` / `::semantic_stagger_ms` + `novel/stages/stage6/semantic.py::judge_prefix_observation` / `::prepare_semantic_candidate` + `contracts/schema/stage6.py::HookParams`）+ 默认字节、独立 session、fingerprint 与 ledger 回归 |

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
> 2026-08-23 退场**，见 `R-9.24` 与 `docs/DECISIONS.md`。这里保留它的名字而不是抹掉：它是上一段那次
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
| R-10.4 | 姓氏族群只从**原文有据的姓**生成：完整 `canonical` 精确等于语言层关系/官职/位号标记时先排除（`皇贵夫` / `皇太女` 不姓「皇」），只以标记结尾的带姓称谓仍保留（`王夫人→王`、`李将军→李`）；其余候选按「字面同姓且 ≥2 人」聚类。关系边那条已删：一本书的兄弟在关系里**根本没有边**，而谓词词表会把夫妻同姓并成一族。确定性族群不能由模型自己的 `renamed` 决定退出，表中原姓一个不能漏、组内必须共用新姓 | `HARD`（`rn_surname_split`；族群生产唯一落点 `novel/core/naming.py::attested_surname` / `::family_groups`，精确排除与正向召回由生产回归查） |
| R-10.5 | 亲属词与姓氏同形（`孙`/`公`/`子`/`女`/`母`）时，用**本书的姓氏表**判，不硬编码字表——否则会让原作的姓氏活着进入「原创」成品 | `HARD` |
| R-10.6 | 残留探针的取值面 = 「原文有据的姓」**∪** 「姓氏映射的键」。少了后者有盲区：正典名是自造标签的姓、与亲属字同形的姓都会被过滤判掉 | `结构`（`novel/stages/stage7/rename.py::_build` 里 `stems` 那一行并集，喂给 `novel/stages/stage7/subs.py::residual_scan`） |
| R-10.6b | ⚠ 真姓被保守过滤漏掉时，**不为召回放宽下发面**，由探针把 `surname_map` 键并回来；反方向的完整纯称谓/位号是假姓，必须在 `attested_surname` 以共享语义标记的整串相等精确排除。同一份数据在下发面该严、在检测器该宽，两处的正确方向相反 | `结构`（`novel/core/naming.py::attested_surname` 只被 `novel/core/naming.py::family_groups` 调用，`stems` 另走一条路） |
| R-10.6c | 探针的**结论**要常驻报出：逐个姓字的覆盖率与残留条数，**残留 0 条时也要出声**——「扫过、一条都没捡到」与「根本没扫」不许在报告上同形 | `INFO`（`rn_residual_stem`） |
| R-10.7 | 恒等基线：占位符双射往返，验替换器与组装器**逐字节守恒**。⚠ **它的结论必须落进产物**——「跑过且逐字节无损」与「从没跑过」不许在盘上同形（`Q-2`；原型只 `print`，产物里没有任何一个键记得它跑过没有） | `INFO`（`rn_identity_baseline`；**没跑过时报「没跑过」**，不是不报这一行） |
| R-10.7b | 恒等基线**验不了**三样：正向对真名安不安全（占位符天然无歧义，真名不是）· 漏网覆盖率 · 新名好不好。**这三样必须与基线的结论印在一起**，否则下一个人会把它跑绿了读成「替换是安全的」 | **`人`**（`rn_identity_blind`），见 `B-21` / `B-22` |
| R-10.8 | 成书 = `文案 \n\n 1 \n\n 第1章正文 \n\n 2 \n\n …`，LF、无「第N章」、无任何审阅元信息 | `结构`（`novel/stages/stage7/assemble.py::assemble_txt` 是唯一的组装路径，章分隔与换行取自 `contracts/invariants.py`，是常量不是参数） |
| R-10.9 | 成书是纯文本、带不了来源账，所以账记在它的 sha1 + 「判了哪几份稿」上 | `HARD` |
| R-10.10 | **替换覆盖率验不了**——只能替换人物表里有的称呼，**人物表漏掉的角色这一层结构上够不着** | **无守卫 → `B-21`** |
| R-10.11 | **新名好不好、读起来串不串味没有确定性判据** | **`人`**，见 `B-22` |
| R-10.12 | 新名贴合时代与性别气质 · 保住名字之间的结构关系（排行字、兄弟姐妹只差一字） | **无守卫 → `B-12`** |
| R-10.12b | **改名不得改变旧称呼自带的字面语义标记**：中文数字/阿拉伯数字、排行、亲属关系、头衔官阶必须在新形中原样保留；例如「阿九→阿七」必须阻断，「阿九→玄九」可通过 | `HARD`（`rn_semantic_marker_changed`） |
| R-10.13 | **映射表自身的六条后置条件**（＝下给模型那六条规则的验收面）：① 每个角色都交出决定与理由，`char_id` 与实际下发人物恰好一次对应；`renamed=true` 时 visible canonical 或至少一条该角色实际收到的 alias 必须真实变化（描述 canonical 恒等而带姓 alias 真改合法，全恒等的账面假改名不合法）；`renamed=false` 时 canonical/alias 必须全恒等 ② 每个角色的 `aliases[].old` 与该角色实际下发 alias 精确一一对应（不漏、不多、不重复、不串角色），下发的每一个称呼都有明确去向 ③ **新名全书唯一** ④ 一个旧称呼只映射到一个新名 ⑤ **新名不得已经出现在替换目标里**（成稿+文案+书名），只出现在原作正文里的另报一条提示 ⑥ 称谓/亲属后缀在新形里原样保留（职位与关系不变） | ①②④⑥ `HARD`<br>③ `HARD` + `INFO`，见下方那条<br>⑤ `HARD` + `INFO` |
| R-10.13b | **人手补的映射不受 ③ 的否决，但必须报出它撞上了谁**——同 `R-10.3b` 的形状：这条否决防的是**静默**撞名。⚠ 实测有一次撞名**是对的**（原作对同一个人有两种写法），而硬拦它的唯一出路是撤销一个对的决定 | `INFO` |
| R-10.13c | 人手补的映射之间不许**首尾重叠**（`a` 的后缀 == `b` 的前缀 ⇒ 单次扫描会切出半截残串） | `HARD` |
| R-10.13d | 被**保留**的称呼里不许夹着已经改掉的名字（否则成品里半改不改：「阿禾」成了「阿舒」而「阿禾姊姊」原样留着） | `INFO` |
| R-10.14 | **成书的前置条件**：Stage2 全局复核必须 current 且 blocking 面 generation-safe（分级见 `R-2.22`：逐项 PASS，或仅余显式完整组隔离的 secret/relation/world-rule UNVER；隔离项与 `disclosure` 未决项不挡成书，但终审包必须逐条披露）；人物表必须已人工定稿且当前 fresh；Stage5 缺章或 `legacy_incompatible` / 各章不出自同一套当前契约 / 正文或文案消费指纹漂移 / 文案未定稿或其 resolution 不再绑定当前字节 —— 一律拒写 txt。人物表编辑会清空旧批准与 `book_txt` 账并把旧 txt 移出 live 下载路径；下载端点复用同一道 Stage2 + approved/current/fresh 闸，不以“文件存在”冒充可交付。文案语义闸只接受一种可审计基础：actor / reason / 完整 SHA-256 绑定的 `human_verification`，且该 resolution 必须仍绑着当前进成书的那份字节（改一个字即失效）。⚠ 「当前 profile 下的 calibrated PASS」那条路已随自动放行门删除；Stage6 双盲 UNVER 仍按 hash-bound 人工批准收口；Stage2 blocking typed UNVER 只有按 `R-2.16` 完整组隔离后不阻断，否则继续关闭。txt 是交付物，里面没有地方写风险抬头横幅 | `HARD` ×2 + `闸门` ×5（含 Stage2 typed review、`stage6.semantic.require_approved_clear` 与 Stage7 delivery gate） |
| R-10.14b | 被拦下的废稿**排除但要报计数**——「排除了 N 份」与「一份都没有」必须分得开 | `INFO` |
| R-10.15 | **人工编辑面按 `char_id` 浅合并**，不要求人重抄整份决策集；不在补丁里的角色**结构上改不到**，且**不许改由人物表派生的旧名** | `结构`（`novel/stages/stage7/measure.py::merge_mapping_edit` 只认白名单 `_CHARACTER_PATCH_KEYS`，`old_canonical` 不在其中，写了就显式终态） |
| R-10.16 | **书名里的人名跟着换，其余不动**（不重起书名）——它走的是同一份替换表，是**确定性应用**不是第二次决策 | **`人`**（`rn_title_unver`：现语料上是**恒等变换**，三本书名都不含人名 ⇒ 这一条**从来没有过失败样本**，只能常驻报出换了几处、不能当绿灯读；换得对不对由人定稿时看），见 `B-22` |
| R-10.17 | **三条书级读数常驻输出**（不是告警，也不许挂在「没问题」那一支上）：替换总账（几条映射、换了几处、篇幅变化）· 新姓字在原文里另有多少次非人名用法 · **成书的写法密度**（正文／文案／原作三列）。⚠ **文案那一列全链只有这里量**——逐章判定够不到它，因为文案不属于任何一章 | `度量` ×3（`rn_report` / `rn_stem_noise` / `rn_style_report`） |
| R-10.18 | **「替换覆盖率查不了」这件事本身要常驻列在报告里**，不是不出现——省略掉，读报告的人会默认它守住了。代码侧只负责把「够不着」写在脸上，把关的是 `R-10.11` 那一下 | **`人`**（`rn_coverage_unver`），见 `B-21` |
| R-10.19 | `stage7.rename --emit-edit-template` 必须 stdout-only 输出 registry 绑定的 `NameMapEdit` v1 骨架，不手抄版本。`stage7.approve` 必须显式提交 actor/reason 与当前公开 v3 workpack 给出的 opaque `review_token=s7r3_…`；调用方不得手算或搬运当前决策面、workpack、代码、规则的多枚 SHA，公开包也不得输出 `code_version` / `rules_fingerprint` 原料。服务端在同书锁内重建 `surname_map + characters + extra_subs` 决策面、review contract 与 validation seal 后核 token；旧 v2 包与 `s7r2_` token 只读，不得批准新决策。服务 s7 proof 保存内部 review identity 与完整 raw typed 审批凭据，并持续复核 effective current；派生 residual/book/book_txt 字节不进 review identity，故 assemble 写交付账不自废批准，真正决策、可见 finding、review contract 或凭据变化仍失效。改名 prompt 实际下发人物 `role` 与 `setting.时代背景`，所以 grounding 消费投影必须包含 role 和真实下发的 era：typed role/era 修订会让旧 name map stale；未下发的 fate/secret/arc/世界观不得反向制造本节点 stale。`--identity` / `--recheck` 在覆盖 provenance 前必须先核旧消费输入：stale/unknown 或决策主体变化令 effective approval suspended，只有 proven fresh 且主体不变才可复活；两条路都清 `book_txt` 并原子退休旧交付物，不能用零调用重写 provenance 洗白旧审批 | `结构`（模板、决策面与免费重写锁在 `novel/stages/stage7/rename.py`，服务 proof 在 `novel/orchestrator/approvals.py`，交付闸在 `novel/stages/stage7/assemble.py`，消费投影在 `novel/consumption.py::stage7_grounding`）+ hash-bound `人` + opaque-token、role/era/identity/recheck/assemble/freshness 回归 |
| R-10.20 | **来源账那几条边的结论要在复检那一步就报出来**，不能只在组装那一步算。`_provenance.inputs`（含 `stage2/global_grounding.json`）一直记着 `file`+`consumed` 双指纹，比对算法也一直在，但此前**只有 `stage7.assemble` 调用它** —— 而人是在 `stage7.approve` 拍板的，approve 在 assemble **之前** ⇒ 一个人可以给「照旧人物表算出来的映射」签字，直到组装才被拒。⚠ 复检这一条是 `INFO` 不是 `HARD`：hard 会连 `--recheck` / `--adopt-residual` 这些 0 成本恢复路一起拦死，而 assemble 那道真闸原样不动。⚠ 三态分开：`stale`（可复算的断言）与 `unknown`（来源账证明不了）不许合成一句 `!= fresh` | `INFO`（`rn_upstream_stale`）+ `闸门`（`novel/stages/stage7/assemble.py` 原有那道）；判定共用 `novel/store.py::ArtifactStore.freshness_of_inputs`，**不另起第二把尺子** |
| R-10.21 | Stage7 人物定稿必须先读取只读、异常优先、hash-bound workpack：公开面为 `GET /api/novels/:sid/approvals/stage7/workpack` 与 CLI `approval workpack stage7`。同一包完整携人物/别名/补充映射、kept 明细、逐旧字面的真实单次扫描命中、完整 residual 上下文与 baseline、替换计数及书级/identity findings；内部保存 review subject/contract 与 validation seal，不让人从分散文件猜。公开提交只接受同一包的 opaque `review_token`、actor/reason，服务端现场重建并关闭式防漂移。approval 与 assemble 派生账不进入 review identity，批准/组装本身不自废。代码、规则或机械来源漂移只令 raw 决定的 effective 投影 `suspended`；免费复检后 subject/contract 未变则 `already_current` 幂等复活，人物决策、可见 finding 或 review contract 变化才回到 `review_required` | `结构`（`contracts/schema/api.py::Stage7ApprovalWorkpack` + `novel/stages/stage7/rename.py::approval_workpack` + `novel/views/stage7.py::stage7_approval_workpack` + `novel/api/routes/approvals.py::get_stage7_workpack` + `novel/cli/main.py::_cmd_approval`）+ hash-bound `人` + API/CLI/页面 opaque-token 与 raw/effective 正反例回归 |

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

> **R-11.21 lifecycle 联结补充：** action admission id 与 outer AgentRun id 是两枚身份。当前
> `CommanderAdmissionEvent` v2 从 `opened` 起逐行保存 `lifecycle_run_id`；owner lease、全量 beacon
> 与 AgentRun 投影只认 outer id。旧 v1 先按 exact epoch/canonical `-tN` 查 `epoch_opened` 结构命中；
> 任一命中的唯一 v2 started、contract、sid 或 fold 漂移均关闭式拒绝，不能降级 generic self。结构
> 唯一且各格闭合才映射 outer，多条拒绝；只有完全没有 canonical 命中才按 generic self gate。reconcile
> v3 subject 绑定两枚 id、opened/claimed/released 全量事实和 outer beacon，使旧 seal 升级后自然
> invalidated/unresolved。成功投影只追加 outer
> `reconciled` 并清 active epoch，保持 unknown；不得伪造 `epoch_closed`、`ended`、exit、drain 或
> delivery，跨账补尾还须 subject 与 actor/reason/force 全部相同。

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
| R-11.11 | 作业记录 = 这次跑用的是哪条命令、哪版代码。**服务重启后 `运行中` 一律改判 `不知道`**，不是「成功」；已冻结的 `executor_backend` 快照槽若为 `null` / 缺失，只能解释为未记录，不能按今天的默认配置倒推历史身份。旧 `executor_profile` 只在读边界无损迁移为 `kind=agent`，新序列化不得继续发旧字段 | `HARD` |
| R-11.12 | **不做作业取消**——调用在途时钱已经花了，kill 掉省不回来 | `不适用`（一条**负空间**声明：它要求的是「不建某个功能」，没有可判定的对象） |
| R-11.13 | 剧透开关只关四个字段，**已知旁路仍在**，界面上常驻这句标注 | `结构`（`contracts/invariants.py::SPOILER_DISCLOSURE`，随 `/api/enums` 整份下发；由 `tests/test_api_discipline.py::test_the_spoiler_disclosure_boundary_is_published` 查） |
| R-11.14 | Commander 与 executor 选择只有一个公开事实源：`GET /api/agent/capabilities`。Executor 模式必须是判别联合 `ExecutorBackend = {kind:'agent', profile:AgentProfile} \| {kind:'legacy', profile:LegacyLlmProfile}`，不得再以 profile 是否存在猜模式；Agent 端由 `AgentDriver` 抽象及 capability registry 关闭 driver/role/model/effort/mode，Legacy 端由 `LegacyLlmDriver` 抽象及 provider registry 关闭 provider/model/thinking/effort/node，任一失败都不得降级到另一 variant。当前 Legacy 只发布 DeepSeek；以后接入 GLM 必须新增完整 driver/spec/client，不能换 base URL 后复用 DeepSeek 的模型回退、tokenizer、预算或 thinking 语义。`PipelineAgentProfiles.schema_version=2` 的 Commander 仍为 AgentProfile，默认 Executor 与逐节点覆盖均为 ExecutorBackend；v1 裸 AgentProfile 只读迁移为 Agent variant，新写入只发 v2。前端不得硬编码选项、拼笛卡尔积或取数组第一项猜默认；`default_profiles=null` 只表示尚无经 TODO-45 整书验收的**产品预置配置**，不是 Agent/Legacy 开关。用户常用配置是另一份 `_service/agent_profile_settings.json` 服务状态，只经 `GET/POST /api/settings/agent-profiles` 读写并由同一 registry 关闭式校验；读取保留 `unset / configured / unreadable` 三态，写入携 revision 做 CAS，坏文件或读取失败不得静默回退产品默认。设置不得保存 API key、CLI 登录态、base URL、代理、付费或正文外发授权；只初始化未来运行草稿，不倒推已启动、排队或历史 run。Agent 可用性保持 `ready / missing / unknown / unauthenticated` 四态，本机探针不发模型请求；Legacy 能力表同样不读取密钥、不联网。R-11.15～R-11.18 中历史 Phase 所称 `executor profile` 只指今天的 Agent variant，不得据此覆盖本条现役联合 | `结构`（`contracts/schema/orchestration.py::ExecutorBackend` + `novel/agents/base.py::AgentDriver` + `novel/llm/providers.py::LegacyLlmDriver` + `novel/agent_capabilities.py` + `novel/orchestrator/agent_settings.py` + capability/settings API 路由；`tests/test_agent_capabilities.py`、`tests/test_agent_settings.py`、`tests/test_orchestrator_nodes.py`、`tests/test_cli.py`、`tests/test_schema_api.py` 与前端 settings/dialog 回归查） |
| R-11.15 | **直接 job 的 executor profile 必须按已迁移组合关闭式传递，不能只收配置不生效。** Phase 2 当时唯一开放组合是 `stage2.global` 且 `flags=[]`：preview/start 共用同一计划，把 registry 解析后的 profile 同时冻结进 `Step`、`JobStart` 与 argv，CLI 再原样交给 `RunContext` 和该节点独立的同一 session executor，直至代码校验、正式提交与终验；显式 profile 绝不回退 DeepSeek。该节点任何 flag（含 `--purge-role`）、当时其他未迁移节点及 outer commander 非空 `profiles` 都必须在 wire/dispatch 前拒绝。前端仍未消费 capability，不得把后端窄纵切冒充页面已可配置；没有真实模型请求时不得宣传质量、速度或可用性实证 | `结构`（`novel/orchestrator/nodes.py::resolve_executor_profile` + `novel/orchestrator/jobs.py::plan_start` + `novel/cli/main.py::_run_stage2` + `novel/node_agents.py::Stage2GlobalAgent`；`tests/test_orchestrator_nodes.py`、`tests/test_orchestrator_jobs.py`、`tests/test_api_http.py` 与 `tests/test_cli.py` 查） |
| R-11.16 | **Phase 3 把同一直接 job 纵切扩到 `stage2.blueprint + flags=[]`，但不放宽校验、恢复或终验。** preview/start 必须把同一 resolved profile 冻结到 `JobStart` 与付费 producer `Step`/argv；自动追加的 0 成本 `--check-only` Step 保持 `executor_profile=null`，不能伪装成第二次 agent 调用。CLI 把 profile 原样交给 `RunContext` 与 `Stage2BlueprintAgent`，后者按 phase/purpose/chapter 隔离 driver、logical session 与 provider session；Agent 逐章池受 `STAGE2_BLUEPRINT_MAX_CONCURRENCY=4` 的供应商稳定性护栏约束，配置可调低不能调高，且该上限不改变 legacy LLM transport；每章在同一 session 内接受 schema/dynamic/scope 及可路由联合 HARD 的代码反馈。验证后 checkpoint 必须用 `node-executor/v1` 逐项闭合 resolved profile、原始 subject/prompt/schema、requested ceiling、session/turn、accepted stdout、typed payload、attempt links 与 ledger/audit；live restore / 同 run resume 须复验同一闭环、严格等于当前 executor profile 并认领 prior attempts，不能伪造 provider resume 或重新付费。若一批仅因登记的 Agent delivery failure 未交付，成功章继续保留；producer 必须以绑定当前 run/code/完整章节集合的 `delivery_recovery.json` 区分等待态，恢复只补缺章。0-LLM `--revalidate` / `--renormalize` 的 standalone 历史证据扫描没有当前 executor profile，只验证盘上 profile 本身仍登记有效及 checkpoint/session/ledger/audit 完整，不把历史成本 claim 进本轮，也不得因此放宽 live profile 漂移。整书 compile、联合 HARD、正式提交、readback 与自动 `--check-only` 仍归代码，agent 自述不能放行；显式 profile 绝不回退 DeepSeek。两个 Stage2 节点的任何 flag、其余未迁移节点、outer commander 非空 `profiles` 与尚未消费 capability 的 UI 继续关闭 | `结构`（`novel/orchestrator/nodes.py::EXECUTOR_PROFILED_NODES` + `novel/agents/unit_session_pool.py::UnitSessionPool` + `novel/node_agents.py::BlueprintSessionPool` / `::Stage2BlueprintAgent` + `novel/llm/stage2_runs.py::write_checkpoint` / `::restore_checkpoint` / `::read_delivery_recovery` + `novel/stages/stage2/extract.py::finalize_blueprint_candidate`；`tests/test_stage2_blueprint_agent.py`、`tests/test_stage2_blueprint_agent_boundary.py`、`tests/test_stage2_recovery_runs.py`、jobs/API/CLI 回归查） |
| R-11.17 | **Phase 4 把 direct executor 纵切扩到 `stage6.semantic + flags=[]`，并把正文外发从付费确认中拆成独立授权。** `PreviewRequest.egress_authorized` 默认 false；resolved profile 与这枚 bool 必须从 preview/start 同源冻结到 `JobStart`、付费 `Step` 与 argv，只有 true 才加入不属于 `NodeSpec.flags` 的运行控制参数 `--allow-egress`。false 预览可以完整摊开不可执行命令，POST start 与手敲 CLI 则必须在 job、probe、driver 与 wire 之前以 `ApprovalRequired` 拒绝；无 profile 时 egress 快照归一为 null，显式 true 反而关闭式拒绝，不能从 `--yes` 或 profile 推导正文外发授权。执行时 A/B 各自取得不同 driver 对象、logical session、临时 cwd 与 provider session id，同一双盲池的 driver/provider 元数据必须闭合，缺失角色并发请求；每角色验证后 checkpoint 不可变，任一新 wire 前先预检两边，完整恢复零 driver/probe/start/wire，单边恢复只为缺失角色新建 session，绝不伪造历史 provider resume。代码负责匿名响应校验、合并、序号与提交所有权，并在同一临界区按 `prepared journal → 正式 artifact write → typed readback → A/B 两条 adoption → committed` 收口，最后再跑当前输入、代码与交付终验；显式 profile 在通用 dispatch 先于任何旧 `_llm_client`，绝不回退 DeepSeek。该节点任何 flag、其余未迁移节点、outer commander 非空 `profiles` 与尚未消费 capability 的 UI 继续关闭 | `结构`（`novel/orchestrator/nodes.py::resolve_executor_egress` + `novel/orchestrator/jobs.py::_require_executor_egress` + `novel/cli/main.py::_dispatch` + `novel/agents/unit_session_pool.py::UnitSessionPool` + `novel/node_agents.py::SemanticSessionPool` / `::Stage6SemanticAgent` + `novel/llm/stage6_runs.py::prepare_commit` / `::mark_committed` + `novel/stages/stage6/semantic.py::finalize_semantic_candidate`；`tests/test_stage6_semantic_agent.py`、`tests/test_stage6_semantic_agent_boundary.py`、`tests/test_stage6_agent_runs.py` 与 jobs/API/CLI 回归查） |
| R-11.18 | **Phase 5 把 direct executor 的现役面闭合为 8 节点 / 12 种 flag-token 组合，并继续由代码独占裁决与提交。** 在前三条纵切之外，新增 `stage4.design + flags=[] / [--replan]`、`stage4.plan + flags=[]`、`stage5.write + flags=[] / [--chapters] / [--revise] / [--revise,--chapters]`、`stage6.hook + flags=[]`、`stage7.rename + flags=[]`；任何未登记组合在 wire/dispatch 前关闭式拒绝。producer 都使用固定 typed response，agent 只提交候选，schema 与业务 HARD 由代码判断；其中 Stage4A 不再提交一份整书 response，而由一个节点内私有 pool 依次接收 1 份策略 response 与 N 份单章 response，每份验证后独立 checkpoint，代码再聚合成唯一整书候选。Stage4B 只对歧义章作有界惰性 admission；首个登记的可恢复交付失败立即关闭新 admission、drain 已在途章并留下严格绑定当前 run/code/请求全集的 `delivery_recovery.json`，同 run 恢复只补缺章。已经 ended 的旧 Stage4B run 不得伪装 live resume；新 run 只能在重新复验旧 run/profile/request/schema/checkpoint/session/ledger、当前输入与动态 validator 后，以 immutable `historical-checkpoint-adoption.json` 采用**唯一最大且不完整**的历史章集合，不复制 checkpoint、不续旧 provider session；正式逐章 lineage 保留 source-stable 身份，usage 精确合并 source/target ledger，而 target commit anchor 必须来自本 run checkpoint。安全的结构化规则元数据只回到对应 logical session。凡登记了正式 primary 的交付都在同书提交所有权内按 `prepared journal → artifact write / retire → typed readback → current input/code/HARD/freshness 终验 → 精确 accepted-attempt adoption → committed` 收口；Stage4 typed infeasible EvidenceBinding 仍是正式 primary 但保持 landed-red，没有正式 primary 的 INVALID / rejected / 耗尽终态不得伪造 adoption/committed，既有 0 成本 revalidate / 恢复路继续保留。恢复必须复核 profile、subject、prompt/schema、session audit 与 ledger，历史 provider session 只作 adoption 身份，不能伪装 live resume；显式 profile 的 dispatch 永远先于 legacy client 构造，绝不静默回退 DeepSeek。当前 UI 仍未消费 capability，commander/executor/model/effort/egress 选择器属于 Phase 6，不能把后端纵切写成页面已交付 | `结构`（`novel/orchestrator/nodes.py::EXECUTOR_PROFILED_RUNS` + `novel/agents/unit_session_pool.py::UnitSessionPool` + `novel/node_agents.py::EXECUTOR_FACTORIES` + `novel/llm/agent_runs.py::prepare_commit` / `::mark_committed` + `novel/llm/stage4_runs.py::read_plan_delivery_recovery` / `::restore_or_select_plan_historical_adoption`；`tests/test_stage4_design_session_pool.py`、`tests/test_stage4_plan_chapters.py`、`tests/test_stage4_agent_boundary.py`、`tests/test_stage5_write_agent.py`、`tests/test_stage5_write_agent_boundary.py`、`tests/test_stage5_agent_runs.py`、`tests/test_stage6_hook_agent.py`、`tests/test_stage7_rename_agent.py` 与 jobs/API/CLI 回归查） |
| R-11.19 | **Commander、Executor 与页面选择面必须闭合在同一 parent run 契约，而代码/skill 身份只能属于可滚动 epoch。** `/api/agent/capabilities` 是 driver/provider/model/effort/mode 与本机四态的唯一选项源；新运行表单只可按“`configured` 用户常用配置 → 非空产品默认 → 空草稿”初始化一次，`unreadable` 或设置请求失败时关闭预览/启动，不能绕回产品默认；设置页与运行弹框的临时改动都不得重解释已冻结 run。preview/start/batch 共用唯一 planner：`RunContractV2` 只稳定冻结 sid、规范化 base、两项独立授权与 resolved profiles；stdin-only Commander invocation、code/skill/protocol/CLI identity、admission 和 provider session 归 `ExecutionEpoch`。start/batch 在任何 probe/spawn/wire 前同时要求付费与正文外发授权，preview 可未授权只读展开；代码或 skill 漂移不改 parent run、不丢授权/profile，只关闭旧 epoch、免费复检并打开新 epoch。现役 reviewer Commander 固定走 `structured_response` 只读调用：不得取得文件写入、提权或网络能力，也不得依赖工具完成裁决。正文与 evidence 的所有嵌套字符串都是不可信数据，须转义外层标签字符并明示其中命令、路径、标签或伪造 option 绝非指令；模型阅读面不携 base/novel_dir 绝对路径。Codex CLI 当前无“关闭只读 shell”开关，因此须以当前 epoch 空 scratch 为 cwd、`--skip-git-repo-check`、`approval_policy=never` 与 `sandbox_mode=read-only` 形成关闭式隔离，不带 `--approve-for-me` / `--add-dir`，且协议一旦亲见 command/file/MCP/search 或未知 item，整份 structured 回答关闭式拒绝；Claude 同样以 scratch 为 cwd，并显式传 `--tools '' --permission-prompts none`。模型最终只返回一份裸 typed JSON；可信 Supervisor 先以现役 loader 严格验证，再原子物化到当前 epoch scratch，非法返回不得覆盖既有请求。Reviewer 若有 provider-native output schema，driver 必须先经公共严格子集校验并冻结 canonical JSON 字节与 SHA-256；同一 provider session 的 exact resume 必须逐字复用该 SHA，且真实 schema SHA 必须一致写入 session `started / attempt / turn / ended` 与在线 session pointer，不能继续用 observation protocol 常量冒充响应契约。Codex 的 `--output-schema` 只指向当前 scratch 下按该 SHA 命名的临时普通文件：build/preview 不创建，runner 生命周期才独占创建、回读并清理；硬杀残留只在非 symlink、普通文件且字节完全相同时安全回收，异字节或异常类型关闭式拒绝，该文件不是 artifact、缓存或 provenance。Claude 只用 canonical schema 的 inline `--json-schema`，必须在 runner/wire 前按 Windows 实际 argv 上限预检，超限零 wire 失败。子 Commander 只持 `run_id + contract SHA-256 + epoch_id`，从 append-only 事件重建精确快照；operator runner 按 exact node/flags 选 Executor profile，agent 主路不带 legacy repair/fallback。Commander 的 provider-neutral driver、每个 exact-resume turn、`role=commander,node=null` 成本行与 actual identity 必须闭合；账、会话或 epoch 身份证明不了时只能 unknown。所有 headless 审批只经 0 成本 typed façade；Stage2 Blueprint/关系编辑、Stage4 design/structure/完整章 edit、Writing、Stage5 closure 与 Stage6/7 的 public write payload 均只接收各自 opaque `review_token` 加 actor/reason/typed ruling，内部多枚 SHA 继续完整审计但不要求 Commander/skill/UI 搬运。写审批不启动作业。前端 start 只消费最后一次成功 preview 的原请求快照，capability、配置或授权漂移关闭式拒绝。模型对 scratch 没有写面；服务进程只在 `_service` 内承担上述验证后运输，正式产物仍只经确定性入口与 CLI 子进程 | `结构`（`novel/commanders/base.py::CommanderInvocation` + `novel/commanders/codex.py::_materialized_output_schema` + `novel/commanders/claude_code.py::ClaudeCodeCommanderDriver.build_invocation` + `novel/orchestrator/agent.py::plan` / `::start` / `::_write_session_pointer` + `novel/commander_context.py::load_from_environment` + `novel/agent_session_audit.py` + `novel/llm/ledger.py::append_commander_call` + `.workflow/skills/novel-pipeline-operator/scripts/run_step.py` + `novel/cli/main.py::_cmd_approval` + `web/src/components/agent/AgentProfilesForm.tsx` + `web/src/screens/settings/SettingsScreen.tsx`）；RunContractV2/epoch/profile/授权/UI/会话/原生 schema SHA/临时文件与 argv 早拒回归查 |
| R-11.20 | **Commander 不得靠 Stage 表或自身记忆猜下一步；确定性 RunSupervisor 才是唯一调度者。** `operator next` 在每节点唯一一次冻结 snapshot 上复用 queue planner/inbox，返回 `ready / blocked / pipeline_complete`。ready 响应直接携 opaque execution token：Supervisor 不启动 Commander，preview 只渲染 token 冻结的 canonical steps；execute **先取得原子 admission claim，再在首个 evidence/beacon/subprocess 副作用前唯一执行一次**活动 surface + 登记外部输入原始字节 CAS，不重跑 current next/planner。贵贱与写盘效果只按 token 内 exact node/flags 和生产登记表复算，默认主路不得再调用 `operator permit` 重扫全链。`operator permit` 只保留给 blocked recovery 与诊断兼容，且必须由 Supervisor 交 exact node/flags；blocked action 自身不能冒充执行权。blocked 分成两个关闭注册表：命中 `reviewer_tasks` 才以 immutable workpack 调度 Commander/Reviewer；每个非 Stage2 reviewer **业务主体**只开一份持久席位账，surface seal 只是当次运输 CAS，代码/skill 导致的 seal 变化不得制造新首审；同一运行会话内的 typed 纠错可续用该席 provider session，只有 opaque review token、Stage0 revision/完整 retained+removed 主体或 A/B 裁决材料真的变化才形成新业务与新席——请求返修的那个会话不可能自己复审；Stage2 global 每轮都是新席；命中 `DETERMINISTIC_RECOVERY_WAITING_ON` 则表示 current typed 决定已经存在，Supervisor 现场铸 exact recovery permit 后直接交 Executor，禁止再唤 Commander。Stage4B 的 current `request_replan` 必须先路由到独立 `approval:stage4-structure-replan`：若交叉阅读得到具体 scene/fact/goal 结构损害，形成新的非 all-accept typed A 决定后再转 `recovery:stage4-design-action`；若 B 拒绝仅由 INFO/metric/UNVER/binding concern 等只读材料支撑，则提交同包 typed stop 并以 exit 2 安全停机，不写审批、不铸 token、不调用 Executor。两路都不能重审或用同一 subject/token 改写 B。Stage2 global 固定为一次 Executor 产出加每个 Global/MainText 主体最多**四席** Commander（`contracts/invariants.py::STAGE2_COMMANDER_MAX_SEATS`）：席 1 完整审阅（可为 Blueprint 期间只读 prereview），席 2～3 定向订正（只接收仍未闭合或依赖失效的增量 subject cards，可合并 GlobalEdit 与 hash-bound `post_edit_review`），席 4 **只读独立终审**（`approval:stage2-global-final-review`）：它读全部 blocking 项（含已 PASS 项的 current_reason 与证据）、整书正文与 Blueprint 工作包，只能提交 `Stage2FinalReviewRequest` confirm 或以自己为 actor 把指定项 reopen 为 FAIL/UNVER；订正席不得占第四席，终审席不得编辑，“最后一个会话既修改又自证通过”在席位账层不可表达。每席都开 fresh logical/provider session，持久 ledger 在调用前记 `started`，重启不返还席位；同席内的 launch/resume attempts 记在同一行不占新席；同 progress 或未闭合数倒退拒绝继续（终审 reopen 之后的回升除外）；中间 0-wire typed 动作不计席位且不创建 Executor，席位用尽后不得旁路第五席。付费 Executor 继续复用自己的 logical/provider session 与 checkpoint。reviewer 的 business/action fingerprint 忽略自变 workpack binding；同一业务主体上的等价已落盘动作关闭式 stopped，不能循环。未登记 blocker 关闭式 stopped，ready 节点、INFO、metric 与**判据型** UNVER 不得唤起 Commander；Stage2 typed UNVER 只在已登记 review blocker 内走 GlobalEdit/显式隔离。`seen.read` 的纯读恢复只在登记的 Stage2/Stage4 审批 blocker 下可达且不越界到 Stage5；同快照 effective `gate.s2=false` 时 operator 必须先回到 Stage2 reviewer，不能把 Commander 派到必然拒绝的 Stage4A template。第四席终审实际读完 Global 与 Blueprint 后只提交 `Stage2FinalReviewRequest`（Global 审阅面 `s2gr1_` token + Blueprint `bpr3_` token + actor/reason）；Supervisor 以一枚 launch ticket 启动唯一 `approval close-stage2` façade 进程，由它按 `s0 → s1 → seen.read → seen.global → s2` 依次复用生产服务，不在同一 ticket 内另启五棵进程，不生成决定或修改 artifact；中途失败保留 raw/effective 可恢复事实且绝不误开 s2。每个 root/follow-check 继承同一 permit envelope、独立 step index 与原子 claim；同 epoch 同 node/surface 的多轮 permit 与 approval input 按内容身份分配 write-once 文件名，相同字节幂等、不同字节绝不覆盖；Stage2 typed 外部输入在 permit 与 producer 消费完前保持存活。旧 token、伪索引、手改 argv、孤儿 envelope在 producer/LLM 前拒绝。新书首次 `stage0.remove --source` 仍是唯一落 sid 前例外，并须按真实 sid 检 admission。canonical skill 整树身份归当前 ExecutionEpoch，漂移关闭旧 epoch而非终止 parent run。已经成功落过 `start` 的 exact execution，其 `end` 是收尾事实而非新执行：code/skill 漂移后只允许凭旧 epoch 的 hash-bound pointer、仍 active 的原 claim 与同 parent 下完全匹配的 open start 追加一次 end；不得借清理慢路再落 start、启动节点或写业务产物。Supervisor 持有 per-parent owner lease 与 durable admission，等待 active claims 与 execution beacon 全归零才释放书锁；300 秒只报警/cancel，不是释放期限，重启、孤儿 gate、迟到 child 与 non-LF 坏尾继续关闭式 unknown/reconcile，`force` 不越过 live owner。`tools/verify_agent_e2e.py` 仍只对已经发生的 agent-only 整书 run 做 0 模型纯读验收，不替代付费、语义或 corpus 证据；已通过 typed loader 的可执行决定按 R-11.25 内容寻址持久化，同 blocker 可零模型重放 | `结构`（`novel/orchestrator/operator.py::next_action` / `::permit` + `novel/orchestrator/agent_supervisor.py` + `novel/orchestrator/reviewer_tasks.py` + `.workflow/skills/novel-pipeline-operator/scripts/run_step.py` + `novel/orchestrator/admission.py` + `novel/orchestrator/write_admission.py::operation_scope` + `tools/verify_agent_e2e.py::collect`）；单 snapshot execution-token、claim 后单 CAS、blocked reviewer 每业务一席、Stage2 fresh session/四席上限与终审只读、drain、reconcile 与 agent E2E 回归查 |
| R-11.21 | RunSupervisor 是现有 orchestrator 内的**确定性状态机**，不得调用 LLM、不得形成第三个模型角色。Agent 状态独立取 `pending / running / paused / ok / landed_red / failed / unknown`；append-only 生命周期至少含 `started / epoch_opened / paused / resumed / epoch_closed / ended / reconciled`。公开 pause/resume/stop API 统一返回 202，只确认请求已受理，不能把 HTTP 返回冒充 lifecycle 终态：pause 先停止接纳新节点，后台等待当前 claim/beacon 安全闭合后才写 paused，不取消正常执行中的付费节点；代码与 skill 身份未变时 resume 在后台复用当前 epoch；普通 blocker 只在同一 binding 内续用本席 Commander provider session，Stage2 global 只续持久审阅账并在下一轮开 fresh session；漂移时同一 parent run 打开新 epoch、免费复检并新建 provider session。Stage4 typed repair/replan 是另一条有意的 producer epoch 边界：Supervisor 对 exact `recovery:stage4-design-action + action_sha256` 在本 parent 内只滚一次 `reason=continue`，封存旧 provider、重读 current operator next/CAS 后才交 Executor，Stage2 deterministic recovery 不借此滚动；stop 在后台取消该 run 所有 Commander/Executor 进程树、drain 后才写 ended，之后不可 resume。另有一条关闭式的系统暂停：`stage2.blueprint` 或 `stage4.plan` 以 exit 2 结束，且对应 producer 在当前 epoch 留下严格校验、只包含可恢复 Agent 交付失败的 `delivery_recovery.json` 时，Supervisor 保持 parent/epoch 与书锁、追加带缺章摘要的 paused，并由既有 `resume` 控件继续；普通 exit 2、凭据缺失/漂移/损坏仍结束或 unknown，不能借此复活。Stage4B 的 ended 历史 run 采用属于后续新 run 的 producer 恢复，不把已结束 parent/epoch 复活成 paused。内部阻塞 helper 只用于诊断/测试。服务重启后无法证明进程状态必须保持 unknown，不得洗成 paused 或自动恢复；人工 `reconciled` 只能复用 Commander admission 的 owner-stopped 证明、完整 beacon 主体与 hash-bound CAS，seal 成功后才向同一个 AgentRun v2 事件流投影，崩在两账之间可按同 subject 幂等补尾，不得另造一套恢复状态 | `结构`（`novel/orchestrator/agent_supervisor.py` + `novel/orchestrator/agent.py::_delivery_pause_note` / `::reconcile_run` + agent pause/resume/stop API + append-only event fold）；202 异步受理、Stage2/Stage4B 系统暂停、Stage4 action producer rollover、零 LLM 调度、并发 resume、同/跨 epoch session、stop 永久、崩溃 unknown 与 reconciled 单一 CAS 回归查 |
| R-11.22 | Reviewer workpack 的 `submission_options` 是可执行 binding，不是说明文字。所有 node / approval 都必须从同包唯一匹配：Stage0 的 revision/actor/flag 顺序、Stage6/7 的 actor/review-token/flag 顺序与 pick 闭集，以及 Stage2 终审、Design、Structure、Writing、Stage5 closure 的 actor/opaque token/章号或 evidence/group 分母均逐字保留；Stage5 trim disposition 则共用同一完整有序 audit/chapter 双 SHA：retain full-batch 逐字保留 actor、顶层/逐项 reason 并只开放 fresh option 明列的理由字段，partial replay payload 完全不可编辑；rewrite request 逐字保留 actor、Review/frontier 与唯一 target，只开放非空 reason 及登记 directive 的 code/current 正文 evidence。Stage0 若全文反向核对发现误剔/漏剔，可提交 typed `stop`，绝不能因唯一 approve 出口被迫批准。Stage2 既有字段 patch 只可提交模板行的无重复有序子序列，已提交行的 subject id、expected hash 与字段集合必须逐字保留；characters/relations/secrets/world_rules 的新增、删除、拆分走生产模板预置 collection/expected collection SHA 的 `collection_edits.operations`，Commander 只填关闭式 delta：`add={op,item}`、`delete={op,subject_id}`、`split={op,subject_id,items>=2}`，绝无 `value` 兼容字段；option 按模板实际 collection 下发 collection→member model 映射与从现役成员/operation Pydantic model 现场生成的局部 schema，同集合不得再走完整 replacement；同一 collection 的全部 operations 还必须满足 split 输出不复用 subject、delete/split 输入 ID 不重复、add/split 输出 ID 不重复、全部输入/输出 ID 不相交四条生产不变量。compact 中 patch/collection 的空数组与 setting/replacement 的 null 是“本次不提交”，可信组装器只生成模板顶层字段及行的无重复有序子序列，不把全量 no-op 骨架带进 sparse canonical GlobalEdit。Stage2 global 的业务预算是**整个 Global/MainText 审阅闭环最多四席真实 Commander invocation**（`Stage2ReviewLedger` v2）：pending 首轮可作 Blueprint 期间只读预审，订正席只发送 pending/FAIL/未隔离 UNVER 或依赖变化重开的 subject cards，PASS 与已隔离项只留 digest/count；第四席只读终审收到不压缩的全量 blocking 行、整书正文与 Blueprint 工作包，其 progress 身份并入 review 字节 SHA，confirm 经 `approval close-stage2` 同时写 hash-bound `seen.read` 与 `seen.global`（后者从此必须携 `Stage2GlobalSeenRequest`，交互界面亦然，不再有“打开即已读”）。每轮都开新的 logical/provider session，连续性只认持久 typed ledger、workpack identity、subject/evidence/reason catalog、claim/evidence index 与存活决定，不依赖隐藏上下文。ledger 在调用前原子写 `started`，宿主崩溃不返还；同 progress fingerprint、未闭合数增加、预算耗尽或 complete 账都在 wire 前拒绝。GlobalEdit 可同交 `post_edit_review`：它绑定修前 source/workpack、完整正文章集合与未闭合 `review_scope`；producer 内存应用、归一、HARD 与重枚举后，只把默认证明用于本 edit 新打开项，review_scope 中仍以相同 `item_id + subject_sha256` 存活的旧 FAIL/UNVER 必须在 `decisions` 中逐项复判，不能因逻辑上随 edit 修好就省略（例如只改 relation 字段而未改变 relation ID 集时，同 hash 的 `relation_set_complete` 仍须显式决定）；动态 decision 行必须按 transport 分开明示闭合键集：canonical full-action 提交 `item_id / expected_subject_sha256 / state / reason / evidence`；compact v2 提交 `item_id / state / reason / evidence / generation_disposition`，subject hash 只由 Supervisor 注入且无 disposition 时显式为 `null`。两种载体没有可登记证据时都必须显式提交 `evidence: []`，不得让提示要求与原生 schema 冲突或让一席已完成的语义审阅因输出脚手架漏字段而报废。非 `quarantine_or_fix` 组不得隔离，Global/review 作为一个历史字节集原子提交。typed loader 或 canonical action exit 2 时，当前席在 Stage2 ledger 持久记录被拒请求 identity、拒因摘要与完整拒因摘要 hash；下一 fresh session 只在业务/workpack 仍匹配时收到自足 delta 包和该确定性拒因，重启不得退回普通首审或丢掉根因，预算耗尽的 stopped 也必须同时呈现原拒因；相同请求字节立即停，纠错同样占订正席。reviewed edit 落为绑定当前 edit 的 `.INVALID` 时，queue 仍优先返回 `recovery:stage2-global-reviewed-edit`；correction workpack v2 只携正式 review、失败 edit/INVALID SHA、HARD 与当前增量，不重复 Blueprint。其他 reviewer blocker 按 `(sid, blocker, business_sha256)` 在 `_service/runs/<sid>/reviewer_seats/` 持久记账（`ReviewerSeatLedger`，`contracts/invariants.py::COMMANDER_REVIEWER_MAX_CORRECTIONS`）：Stage0 的业务 identity 绑定 revision、完整 retained hash、removed 分段身份与裁决契约；Stage4/6/7 与 Stage5 closure receipt 绑定真实 opaque review token 与契约，A/B resolution 绑定完整裁决材料；Stage5 trim disposition 是唯一非 token 的 Stage5 reviewer 业务，统一绑定 `action_mode=trim_disposition`、同一 Review SHA、原始全量有序 audit/chapter 双 SHA 与 reviewer contract；retain/rewrite 是同一席的互斥回答，绝不绑定会随处置落盘自变的 closure token/workpack，也不使用含 code/surface/why 的 action SHA。每份业务账只允许一轮 `review`，其后最多两轮 `correction`；pending 拒因、请求 identity 与 provider session 持久化，席位在 Popen 后、stdin 前预留，重启/换 epoch不返还，不能靠重启再开一轮首审，同轮 attempts 也记在账内。pause/resume 或代码/skill epoch 滚动不重置任一种业务账；纯运输漂移不得跨 session 伪续隐藏对话。design 的 `review_run_id` 由 waiting_on + review token 稳定派生，选项 `allowed_values` 继续从 `contracts/invariants.py::DESIGN_REPAIR_CODE_INSTRUCTIONS` / `*_EXAMPLES` 现场校验；reviewer prompt 随包下发 `novel/orchestrator/reviewer_contract.py::for_blocker`，与 `delegated-review.md` 的 `reviewer-contract` 锚块逐字一致，Commander 不读取 operator skill 或盘上产物 | `结构`（`novel/orchestrator/reviewer_tasks.py::_validate_global_edit_binding` / `::_validate_non_stage2_node_binding` / `::_validate_approval_binding` / `::_validate_stage5_trim_binding` / `::_validate_stage5_reviewer_rewrite_binding` / `::_non_stage2_business_identity` / `::stage5_trim_partial_replay_options` / `::_reviewed_global_edit_template` / `::_collection_operation_schema` / `::_global_edit_allowed_values` / `::_compact_stage2_materials` / `::correction_prompt` + `novel/orchestrator/reviewer_seat_ledger.py` + `novel/orchestrator/stage2_review_ledger.py` + `novel/orchestrator/agent.py::_run_reviewer` + `novel/stages/stage2/extract.py::_review_after_global_edit` / `::global_edit_correction_workpack`）；binding、typed stop、业务 identity、delta、fresh session、持久拒因/预算、精确失效、无效 request、exit 2、重启与 epoch 漂移回归查 |

| R-11.23 | 生产 AgentRun 只能从**干净、可复验的冻结 commit**启动或打开新 execution epoch：`preview` 可如实展示 `-dirty.<digest>` 或 unknown 代码身份，但 `start`、批量 `enqueue` 与因 code/skill 漂移触发的 epoch rollover 必须在 probe、spawn、模型 wire 和生命周期写入前拒绝 dirty/unknown。运行中编辑工作仓不是可接受的“热更新”，应在独立 clean worktree 完成跑书；该限制不改变普通开发 job/CLI 的既有边界 | `结构`（`novel/orchestrator/agent.py::_require_reproducible_code_version`）+ preview 可读/start 与新 epoch 早拒回归 |
| R-11.24 | 运行页必须把三种事实分开呈现：节点 beacon 给“当前正在执行什么”，operator 同快照给“下一节点/下一处理者”，AgentRun lifecycle 给“当前或最后 execution epoch、rollover 原因与累计换 epoch 次数”。空 beacon 不得把下一节点伪装成正在执行，epoch rollover 也不得藏在日志里；三者都只呈现后端事实，不由前端推断新鲜度或调度 | `结构`（`web/src/screens/run/RunScreen.tsx::ResumeFrontier` / `::AgentRunBlock`）+ 前端回归 |
| R-11.25 | **Commander 已通过 typed loader 的可执行 approval/node 回答必须先于 scratch 晋升、席位成功闭账及 canonical 执行 durable，且可零模型成本重放。** `ReviewerStopAction` 是明确例外：typed loader 通过后直接以 stopped/2 收口，不写 `ReviewerActionRecord`、不铸 permit、不执行 approval/node。通用 reviewer 在 turn 内把 approval 或任一 node 决定按内容寻址落 `_service/edits/<sid>/reviewer-action-<sha256>.json`：通用身份为 sid/run/epoch/waiting_on/surface_seal/business，二选一保存 surface/payload 或 node/flags/input_payload；通常携可用 review_token，Stage5 trim disposition（retain full-batch 或 single-chapter rewrite request）例外为 `review_token=null`，改为必带 `stage5_review_sha256 / stage5_reviewer_contract_sha256 / reviewer_session_run_id / reviewer_seat_turn_id / provider_response_sha256`，完整有序 audit/chapter 双 SHA 则在 typed payload 与 stable business 中自证。文件名必须等于规范 record 原始字节 SHA，保存失败不得继续消费。下一窗口先构造当前 workpack，再以同 waiting_on 且 surface seal、真实 review token 或 R-11.22 稳定 business 任一匹配重走现役 loader，之后仍须取得当前 permit；记录不是审批账、不进 surface seal。Stage5 trim disposition 的 durable 顺序固定为“把 provider response digest 绑定具体 seat turn → 保存 ReviewerActionRecord → finish 席位 → 物化 `@reviewer-input` → permit/canonical”；崩在 record→finish 窗时，跨 parent 恢复须持 owner-stopped OS lease，逐项闭合唯一 seat attempt 的 wire+exit0、logical-session start/attempt/turn、成本行的 code/model/effort/protocol/response digest 与 record 身份，open session 只能诚实补为 process-ok/delivery-unknown，started seat 才可幂等 finish；同 parent 换 epoch也必须走同一证据尺。partial batch 只在 current business 未变且盘面恰为原 manifest 的连续 exact 已落前缀 + current unresolved 完整后缀时重放原 payload；proven stale 的新 Review/contract 可退休旧 record，证据损坏/frontier 冲突则保留并 unknown，不得删后重买席。更晚 pending/canonical 拒因只有在 seat ordinal、同 session/provider response、成功 attempt 与成本账同样闭合时才能压过并退休旧 action；no-progress 或 exit 2 必须先 durable 新拒因再严格删 record，不能靠秒级时间或 best-effort 删除猜先后。Stage2 Blueprint 前没有 live surface：预审回答通过 typed loader 后必须立即另存 `_service/edits/<sid>/stage2-prereview-<sha256>.json` 的 `Stage2PrereviewRecord`，绑定 business/workpack/source/main/progress/blocker_count、原 logical session、provider 原始回答 SHA 与 typed payload，不携 provider session、更不假冒可执行 action；Blueprint 成功不能掩盖 record/席位审计失败。恢复须六元身份全等并验证原 turn `purpose=prereview`；若 turn 仍 `started`，仅当原 parent 已不活动，且同 attempt 的 Stage2 attempt 为 wire+exit0、logical-session audit 为 process-ok、成本账为 ok/0 三证齐全时才可幂等补 finish，否则 unknown 且不得重买席。出现 live blocker 后必须按当前 `bind_stage2_prereview → load_action → 保存完整 ReviewerActionRecord → 删除 prereview record` 顺序转换；两记录并存的崩溃窗以后态 action 为准，绑定拒绝则先把拒因绑回原 session/provider-answer SHA，落账成功后才消费 prereview 并进 correction。live Stage2 初审、订正与 final-review approval 也必须先保存 full action record、后 finish 原席；新 writer 在 workpack/session/provider-answer 三元组外还保存 source/main/effective reserved progress/blocker_count，correction 不得拿 base progress 校账。action 对应 turn 若仍 `started`，执行前按同一三账证据补 finish；席位 ok/0 后、canonical Executor 前必须调用 producer 同源纯候选预检，业务拒绝以原 provider-answer SHA 记回该席并退休 action，读盘/身份/审计异常则 exit 70 并保留 action；若 turn 已有 typed/canonical rejection，则拒因是更晚事实，残留 action 必须删除、绝不重放。canonical landed（0/1）后删除 action；exit 70、finish/拒因/保存失败一律保留且不执行；Stage2 exit 2 先 durable 拒因再删 action，并让 planner 重读 current correction workpack，no-progress 同样不得留下可执行 record。reviewed apply-edit 若已落 terminal `GlobalEditFailure`，新 parent 在 planner 前将其精确绑回原 action/席位补 canonical 拒因；若 input/formal WAL 仍为 prepared，则先返回 `recovery:stage2-global-reviewed-edit-commit`，只由 Executor 以 exact `stage2.global --apply-edit stage2/global_edit.json` 零 wire 补尾，首次 canonical 文件尚缺也由 input WAL target 物化；committed 只退休 action。final-review approval 已落地后须先幂等 `mark_complete`、再退休 action，即使 planner 已越过 blocker 也能补账。Stage2 ledger 只有非空且全部 turn 为 ok/0 才可 complete。所有重放都沿用原 actor/review_run_id，不续旧 Stage2 provider session，也不绕过 Supervisor、loader、预算或 permit | `结构`（`novel/orchestrator/agent.py::_materialize_reviewer_response` / `::_record_reviewer_action` / `::_find_replayable_reviewer_action` / `::_validate_stage5_action_record_ledger` / `::_stage5_pending_supersedes_record` / `::_find_replayable_stage2_prereview` / `::_validate_stage2_action_record_ledger` / `::_reconcile_stage2_apply_edit_actions` / `::_reconcile_landed_stage2_final_actions` / `::_run_reviewer` + `novel/orchestrator/uploads.py::save_reviewer_action` / `::load_reviewer_actions` / `::save_stage2_prereview` / `::load_stage2_prereviews` + `novel/orchestrator/reviewer_tasks.py::bind_stage2_prereview` / `::stage5_trim_partial_replay_options` / `::replay_request_document` + `novel/orchestrator/reviewer_seat_ledger.py` + `novel/orchestrator/stage2_review_ledger.py` + `novel/stages/stage2/extract.py::_build_global_edit_candidate` / `::preflight_global_edit` + `contracts/schema/orchestration.py::Stage2PrereviewRecord` / `::ReviewerActionRecord` / `::Stage2ReviewLedger`）；loader 后先 durable、pause→stop→新 parent、record-save→finish 硬崩三账补完、Stage5 retain batch partial-prefix/current-suffix 与拒因优先、prereview→action 两文件缝、initial/correction/final-review 零 wire重放、effective progress、no-progress、final approval 两窗、terminal sidecar补拒因、WAL-only operator→permit→Executor纵切、landed/exit 2/70 与 complete 守卫回归查 |
| R-11.26 | **Commander 与 executor CLI 共用一层可恢复 JSONL 子进程调用（`novel/agents/jsonl_runner.py`），网络/瞬态故障在同一席位内续接，不新开 logical session、不扣新席。** 公共 runner 只定义一次进程组创建、三线程泵、按 owner 的 cancel、幂等 close 与 Popen 后 launch 回调；stdout 逐行流式解析，协议事件即时回调，Commander 首个事件（Claude `system/init` 的 `session_id`、Codex `thread.started.thread_id`）到达时 RunSupervisor 把 `CommanderSessionPointer` 在线写到 `_service/runs/<sid>/sessions/<session_run_id>.json`；三档超时（首事件 `COMMANDER_FIRST_EVENT_TIMEOUT_SECONDS`、空闲 `COMMANDER_IDLE_TIMEOUT_SECONDS`、总时长）超时后先终止整棵进程树，再把部分事件随 `CommanderTimeoutError` 交回。非 0 退出与超时都经 executor 侧同一分类器（`classify_agent_command_failure`）得到 `failure_kind / retryable / wire_evidence`：可重试且已捕获 provider session 的失败在同席 **resume**（Claude `--resume`、Codex `exec resume`，续接提示不重发工作包），没有首事件的 0-wire 启动失败在同席 **relaunch**，每席最多 `COMMANDER_MAX_ATTEMPTS_PER_SEAT` 次真实进程尝试并按 `COMMANDER_RETRY_BACKOFF_SECONDS` 退避；authentication / usage_limit / permission / 协议错 / model drift / provider session 被他人占用一律不重试。每次 attempt 都逐次落成本账（`attempt_id` 唯一）、`agent_sessions.jsonl` 的 `attempt` 事件（席内序号 `delivery_attempts`）与 Stage2 席位账的 `attempts[]`；已取得 provider session 的失败尝试再记一条带 `error_kind` 的 provider turn，同一 logical session 的 `turn_index` 严格递增。席位预留、`started`、launch ticket 只属于第一次尝试；宿主崩溃留下的 `started` 仍按 R-11.22 不返还 | `结构`（`novel/agents/jsonl_runner.py::JsonlProcessRunner` + `novel/commanders/base.py::CliCommanderDriver.run` / `::CommanderTimeoutError` + `novel/orchestrator/agent.py::_run_commander_turn` / `::_write_session_pointer` + `novel/agent_session_audit.py::fold` + `novel/orchestrator/reviewer_seat_ledger.py::bind_response` + `novel/orchestrator/stage2_review_ledger.py::record_attempt` / `::finish_attempt`）；流式事件/三档超时/owner 取消、分类与部分观测、同席 resume/relaunch/上限/不可重试、attempt/response 逐次落账与指针文件回归查 |

> **`R-11.25` 对 Stage5 rewrite 的 durable 顺序比通用 disposition 多一层永久来源 seal。**
> `@reviewer-input` 只供 FREE request producer 一次性运输；canonical request typed readback 后必须先把
> 原 `ReviewerActionRecord` 原始字节复制到
> `_service/runs/<sid>/reviewer_action_authorities/reviewer-action-<sha>.json`，再写绑定 request
> raw/self、stable business、当时 contract、原 session/seat/provider response 与 seat/session/cost
> evidence SHA 的同 stem `.receipt.json`。receipt 才是 commit point，active action 最后退休；archive-only、
> receipt-only 或坏三账一律 unknown。sealed consumer 严格只读；历史 terminal/expiry 只要求当时 contract，
> 新 paid claim/active recovery 还要 current contract。
>
> request execution 另有 request 专属 OS lease 与九态 journal。首轮 `started`、后续
> `retry_started(previous checkpoint)` 都先于相应 driver/session/cache/wire；只恢复 latest intent 的 exact
> checkpoint，只有全根零执行且 old owner stopped 才可 reclaim。terminal 后的
> `recheck/revalidate/apply-edit` 必须先落 request-specific successor prepared WAL 并保存 exact terminal，
> 再执行原 0-wire 事务，最后 committed receipt；硬杀后只按 exact successor 补尾。普通 history 移动不构成
> consumption，旧 fixed-path request 也只在 landed formal 或合法 successor/expiry receipt 后可换代。
>
> **`R-11.25` 的拒因退休顺序不包含 Stage5 partial batch replay 的 exit 2。** fresh/correction
> action 的 no-progress 或 canonical exit 2 必须先 durable 新拒因、再严格退休旧 record；partial replay
> 没有独立替代拒因账，必须保留唯一 record 并以 stopped 收口，后续只准零模型重验，或在
> Review/contract 已证明换代时退休，绝不能删后重买 Commander 席。
>
> **Stage5 reviewer rewrite 的 durable 回答也不是付费执行权。** 它先沿通用顺序绑定原 provider
> response、保存 `ReviewerActionRecord` 并 finish 席位，再把同一回答物化给 FREE
> `stage5.review --request-reviewer-rewrite`；只有 canonical request typed readback 成功后才能退休
> action record。request 已落而 record 尚存的崩溃窗由下一次 planner 精确对证后补退休，不再买
> Commander。request 的 pending/unknown/checkpoint/prepared/terminal 状态优先于 stale Review、closure
> 与新 Reviewer；PAID writer 必须在新 execution epoch 取得 current permit。其 execution journal 在首个
> driver/probe/wire 前写 started，逐章绑定 request 原字节与 Reviewer seat 来源；有 wire 而无可验
> checkpoint 时保持 unknown，matching checkpoint/prepared 只做零 wire补尾，formal/INVALID/rejected
> 都是可审计的 terminal consumption。任何路径都不得拿 ReviewerActionRecord、旧 stop 或手写 request
> 直接越过 canonical request 与 execution permit。
>
> **`R-11.20` 所说“真实业务变化才开新席”对 Stage5 顶部卡有一条显式非 token 形状。**
> closure receipt 仍以 current `s5cr1_` token 为业务；trim disposition 则以同一 Review SHA、原始
> full ordered audit/chapter 双 SHA、reviewer contract 与 `action_mode=trim_disposition` 为业务，retain
> 与 rewrite 不拆席。逐章落 resolution 或先落 rewrite request 导致的 closure workpack/token 自变不得
> 开新席；Review/原始 manifest/contract 真变才是新业务。

> **`R-11.22` 的 typed stop 也覆盖 Stage4 Structure-replan 的证据权威停点。** 它只在
> exact-current B 为 `replan_requested` 的 `approval:stage4-structure-replan` immutable workpack 中出现，
> wrapper 的 surface seal 绑定当前 B decision/subject/workpack。Reviewer 交叉阅读后若确认拒绝理由只有
> INFO/metric/UNVER/binding concern 等只读材料、没有具体 scene/fact/goal 结构损害，才可填写 stop reason；
> loader 只验证 blocker、surface 与唯一 option，不尝试机器判断语义。该动作不写 ReviewerActionRecord、
> A/B 审批、gate 或 execution token，不调用 Executor；Supervisor 仅把它投影为本 AgentRun 的 stopped/2。
> 同一 B subject 仍保持原拒绝终态，不能随后用同 token 的 all-accept 覆盖，只有 artifact、review contract
> 或 subject 变化后才会出现新审阅。
>
> **`R-11.20` 的 deterministic recovery 先于普通审批/inbox，但不对两份 prepared 所有权猜顺序。**
> Stage4 repair/replan aggregate 唯一 prepared 时，operator 只发布
> `recovery:stage4-design-repair-aggregate-commit` 与固定顺序的
> `stage4.design --recover-repair-aggregate <source> --source-journal-sha256 <raw> --source-commit-sha256 <self>`；
> 它属于 `DETERMINISTIC_RECOVERY_WAITING_ON`，Supervisor 不叫 Commander，不为这个
> FREE child 构造模型 Executor profile，也不触发 Stage4 typed action 的 fresh-epoch 付费边界。
> permit 将 source journal 作为本次唯一历史 external input，在签发与 claim 后都复核
> raw bytes；两枚 SHA flag 是值锁，不冒充两份文件。Stage2 commit tail 与 Stage4
> aggregate tail 若同时在盘，operator 必须关闭式报不可能双 prepared，不可用
> `if/else` 选先遇到的一份。
>
> **`R-11.22` 有两层响应载体，不能把 transport 当 canonical action。** `submission_options[].exact_request`
> 始终是 Supervisor 最终交给现役 loader 的完整 canonical request；只有 headless Stage2 immutable task
> 的唯一 submission option 恰为 `stage2.global --apply-edit` 时，provider response 才使用
> `novel-stage2-reviewer-delta-v2` compact wire。同一 task 还开放 review-decisions、隔离或其他合法
> option 时必须继续使用完整 action response，transport 优化不得让任何可执行分支失去可达性。
> 模型只交当前模板开放的 patch 行、collection operations、replacement 与修后判断，并回带 option id
> 与 exact-request SHA。canonical `collection_edits` 仍是 list；compact v2 把本模板开放的集合铸成
> 固定键 object，所有键 required、额外键与旧 v1 row list 关闭式拒绝，每键直接携该集合专型
> operations，因此同一集合在 provider typed 值中天然至多一次。动态 schema 从同一冻结 workpack
> 的 current collection 铸造 ID enum：`delete/split.subject_id` 只能选该 enum，合法 add 的新 ID 不受限；
> direct patch 主体不从 enum 剔除，同交 delete/split 时仍由 canonical 跨写口规则拒绝。collection 固定键
> 与其他 patch 的空数组、setting/replacement 的 null 默认是“本次不提交”。只有 task 与 exact request
> 的 `waiting_on` 同为 `WAITING_ON_STAGE2_EDIT_CORRECTION` 且携 durable failed edit 时才启 merge/drop：
> 此时空 collection 键保留 failed baseline，撤销只走动态 opaque `drop_targets`；ledger
> `purpose=correction` 本身不能启用该语义。未提交的 no-op 骨架不进业务 edit。文本 JSON 解码必须递归拒绝
> 重复 object key，不能接受解析器 last-wins 后的另一份 typed 值。v1 不是登记 artifact：历史 ledger
> 仅存 digest/rejection，无需迁移，已 durable 的完整 canonical ReviewerActionRecord 仍可零 wire 重放。
> wrapper/action/flags、actor/source/workpack/chapter/default/review_scope、已提交行的
> `expected_collection_sha256` 及 decision 的 `expected_subject_sha256` 全部从同一 immutable
> `exact_request` 可信注入，不允许模型重抄，也不允许 Supervisor 猜值。组装出完整 wrapper 与
> sparse canonical `GlobalEdit` 后仍必须走
> `reviewer_tasks.load_action`；producer 的 apply → normalize → HARD → reenumerate/completeness 是唯一
> 业务权威，compact assembler 不复制 surviving 判据。direct patch 与同集合 delete/split 同主体的
> 静态冲突由 `GlobalEdit` loader 拒绝；依赖正式 Global/MainText 的 post-edit registry 与外键判据由
> producer 共用纯预检在 R-11.25 durable action 与原席闭账后、canonical Executor 前执行。预检先在
> 修前 current collection 上扫描同一回答的全部 `collection_edits`，一次聚合所有 stale/invalid
> operation diagnostics，再应用任何 operation，不能逐集合首错退出而消耗订正席。业务拒绝
> 只把代码定义的可信 `collection/category/count` 与完整 `diagnostic_sha256` 记回原 provider-response
> digest，绝不传播模型 ID、正文或任意 `loc/repr/detail`，随后删除 action 且不启动 Executor；预检读盘、身份或审计异常则以
> unknown 停止并保留 action 供零模型重放。provider compact 原始 canonical JSON digest
> 用于拒因、重复请求与 no-progress 身份；assembled full candidate/residue 另按它实际保存的内容计算
> digest，两个内容地址不得互换。
>
> **`R-2.13` / `R-11.25` 的候选预检也遵守 durable-first。** `GlobalEdit` 的结构 loader
> 先拒绝 direct patch 与同集合 delete/split 同主体；需要正式 Global/MainText 才能判断的修后 registry、
> 外键、章号与完整候选形状只由 `novel/stages/stage2/extract.py::_build_global_edit_candidate` /
> `::preflight_global_edit` 这一 producer 判据生成。Agent 必须先保存 full ReviewerActionRecord 并把原席
> finish 为 ok/0，再在 canonical Executor 前调用预检；业务拒绝以原 session/provider-response digest
> 持久化后退休 action，预检读盘/身份/审计异常则 exit 70 并保留 action，不能丢回答或启动 Executor。
>
> **`R-11.22` 的整账归档不是普通预算重置。**只有已经终态、且其冻结 reviewer contract / output
> shape 被后来已提交的契约证明无法表达现役 typed request 的旧账，才可由 Commander 上下文之外的
> 操作员按 current original SHA-256 做锁内 CAS，整体归档后为新契约业务开空账。旧
> turns/rejections/reconciliations 与 actor/reason 必须保留；普通模型答错、运输失败、一般 typed 拒绝或
> 单纯预算耗尽不得走这条例外。
>
> **`R-11.20` / `R-11.22` 的 Stage2 首轮允许是 Blueprint 期间的只读预审。**它仍是
> 四席预算中的第一席真实 Commander invocation，必须读取 v4 完整整书证据；Blueprint
> 完成后只做 business/workpack 双重同一性重绑，不把同一决定重新问一遍，也不续旧 provider session。
> 旧 v3 包因只给 SHA 与局部 snippet 而留下的 UNVER 不是“事实必然错误”：v4 完整证据下可走
> `resolve-unver` 重新裁决；确认事实内容有错才走 GlobalEdit，安全组仍可显式隔离。Reviewer
> wire 把未闭合项投影成 subject cards，并把 subject/evidence/reason 收成内容寻址 catalog；canonical
> workpack 与 SHA 不变，成功且依赖未失效的项不重复发送。canonical action
> 失败日志必须保留头尾两段，不能只留尾部而裁掉根因；这些改动都不放宽 typed loader、写盘闸或
> 三次硬上限。
>
> **上述“调用前记 `started`”的唯一生命周期缝是 Commander 子进程 `Popen` 成功后、
> stdin/prompt 发送前。** prompt/context/epoch/env 的本地预检失败以及 Popen 本身失败都是
> 0-wire，不得进入 Stage2 有效预算或 `role=commander` 成本账；一旦该回调成功并持久化预算，
> 后续宿主崩溃、审计失败或结论未知仍保守消耗本格。旧版若在这道缝之前因只读预审 admission
> 哨兵不属于 epoch 而误写了一格，只能由 Commander 子上下文之外的操作员走
> `reconcile --stage2-review-no-wire-session-run-id ... --stage2-review-sid ...`：系统必须同时证明
> parent AgentRun 已终止、目标恰为即时 exit 70 的 prereview、日志含精确旧失败签名、
> `agent_sessions.jsonl` 与成本 `ledger.jsonl` 的完整 committed 字节都没有该 session，并以
> current ledger/log/双账 hash 铸 subject。apply 必须带 actor/reason/subject SHA 且禁止 force；
> 原始 turn 移入 `Stage2ReviewLedger.reconciliations` 后才从有效三次预算面排除，不能自动返还、
> 直接删行或把一般失败洗成 0-wire。
>
> **`R-11.18` 的 executor accepted-model 证据是关闭的两路判定，不等同于强求每种 CLI
> 回显同一字段。** CLI/provider 回显非空 actual model 时，必须与 resolved profile 精确相等；
> 没有回显时，只有 capability registry 明确登记为 `driver-enforced`、受测的 start/resume argv
> 均显式钉死唯一 requested model 且没有 fallback、并且该 turn 留有 CLI 版本，才可把 requested
> model 作为 checkpoint / provenance 的 adopted identity。后一条路仍把 session `actual_model`
> 保持 `null`、ledger `model` 保持 `unknown`，不得倒填；任何非空冲突回显照旧拒绝，未登记 driver
> 或缺 CLI 版本也关闭式拒绝。运行、checkpoint 恢复、历史采用、聚合与 commit 必须共用
> `novel/agent_model_identity.py`，不得按 provider 或模型名前缀另造例外。`node-executor/v1` 的
> 字段形状不变；既有 provider-reported checkpoint 逐字沿用。
>
> **`R-11.19` 的两级运行联结：**成本 ledger 与 LLM adoption audit 在 producer
> `run_id` 之外显式携外层 AgentRun `parent_run_id`；`agent_sessions.jsonl` 的 executor
> `parent_run_id` 则仍指向 producer run。整书 E2E 只经 started 事件登记的 epoch
> `session_run_id` 集合把后者归回外层 parent，任何一级都不按时间窗口猜。
>
> **`R-11.19` 的具体第三方授权边界：**正文外发为 true 时，launcher 必须从 capability
> registry 将冻结 profiles 展开为实际可达的 Commander 与 node→executor
> provider/driver/model/effort/mode 接收路由，并在同一授权段逐项点名；被逐节点 override
> 完全遮蔽的默认项不得扩入，false 不得产生允许语义。笼统写“外部模型”或只在后文另列 profile
> 都不足以形成具体第三方授权，凭据内容也不得进入模型输入、参数、日志或产物。
>
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
| R-12.1 | **编排只表达真实依赖。** `stage2.blueprint` 只等待 current/fresh 的称名投影 + MainText，不等待未消费的 Global 字段或 global review；非称名 HARD 留下的 schema-valid Global `.INVALID` 可先物化投影并与人类阅读/填写重叠，同书写回仍串行。最新 Global 的称名 HARD 未收口时，投影必须诚实保持 `unknown/global_naming_blocked`，Blueprint 在任何 wire 前关闭，state/queue 只停在 Global typed edit / `--revalidate`（0 LLM），不得 `--renormalize` 或默认付费重采。Stage4 首项才汇合两支：current **正式** global、current/fresh projection + blueprint、`stage2.blueprint --check-only` 联合 HARD 通过、global review current 且 blocking 面 generation-safe（逐项 PASS，或仅余显式完整组隔离的 secret/relation/world-rule UNVER），缺一项都不得继续；隔离项与 disclosure 非 PASS 只披露、不阻断。Stage4A 虽主要读取 blueprint，也不得对未收口 Stage2 投机付费。Stage2 不新增 global 复检入口，唯一显式 `--check-only` 仍是 blueprint。机械测量不是作业，Stage5/6 在真实消费点即时执行，不占队列项也不制造伪并行边 | `结构`（`contracts/invariants.py::NODES` / `::stage2_review_required` + `novel/orchestrator/queue.py::nodes_up_to`）+ CLI/API 零 wire 早拒与联合复检回归 |
| R-12.1b | 节点要诚实报出「这条规则本次没有跑，不是跑过没事」，且**流程里必须钉死一次 0 成本复检**。⚠ 别拿「这次补跑是绿的」当结论——那是运气，不是保证 | `HARD` |
| R-12.1c | legacy LLM transport 的 Stage2 逐章抽取仍按章全量并发、硬上限 32；生产 Agent 路径改用独立逐章 session pool，实际取 `min(4, NOVEL_CONCURRENCY, 待跑章数)`。RunSupervisor 的 Stage2 Global 只读预审与 Blueprint pool 共用这同一份 `min(4, NOVEL_CONCURRENCY)` 跨进程 OS 槽预算：预审占用一槽时逐章池只能取余槽，完成后归还；配置为 1 时不重叠，任何时候都不把并发增加到用户配置或节点硬上限之外。Stage4A 是另一份节点级私有 session pool：策略请求串行先行，策略 checkpoint 后的逐章设计实际取 `min(4, NOVEL_CONCURRENCY, 待跑章数)`；配置可以把单书池下调但不能放大到 4 以上，也不复用/改写其他节点的产品上限。任何并发完成顺序都不得串错逐章 usage/provenance/checkpoint | `结构`（`contracts/invariants.py::STAGE2_BLUEPRINT_MAX_CONCURRENCY` / `::STAGE4_DESIGN_MAX_CONCURRENCY` + `novel/model_slots.py` + `novel/agents/unit_session_pool.py::UnitSessionPool` + `novel/node_agents.py::BlueprintSessionPool` + `novel/cli/main.py::_dispatch` + `novel/llm/stage4_runs.py::DESIGN_MAX_CONCURRENCY` + `novel/llm/ledger.py::chapter_usage`）+ 共享槽峰值/槽位归还/配置 1 串行/逐章账回归 |
| R-12.1d | 供应商限流、账号 entitlement 与产品/节点并发闸必须分开陈述：legacy Stage2/5 transport 项目硬上限是 32，Stage2/Stage4 的 Agent 逐章 session pool 上限分别是 4/4；DeepSeek V4 Pro 当前官方账号级上限是 500。Agent pool 的 4 是当前 Claude executor 的**本进程、单书节点安全边界**，依据同一 executor 在 Stage2 真实整书 13 路首波出现快速 403 拒绝后的保守收敛，不冒充 Claude 账号级通用上限。HTTP 403 单码也不能推出五小时额度或账号总限：必须再区分明确用量窗口、登录过期、永久权限与“`Request not allowed` + 已确认 0 token”的瞬时拒绝。它只约束对应单书节点池；多书 Commander 的总并发仍由独立的 `novel/orchestrator/agent.py::MAX_CONCURRENT` 管，不得把 4 写成账号总闸。正常产品路径不为制造 429/403 越过自身硬闸。真实观测必须显式确认付费、禁缓存、限制请求/重试/token，并保留逐次 ledger 与不含 prompt/响应正文的报告；429/瞬时 403 的重试/退避由可重复合成故障守卫 | `结构`（`tools/observe_provider_limits.py` + `novel/agents/base.py::classify_agent_command_failure` + `novel/model_slots.py` + `novel/agents/unit_session_pool.py::UnitSessionPool`）+ 2026-08-16 DeepSeek 真实 32 并发观测 + Stage2 Claude 13 路首波拒绝实证 + 合成 429/403/退避回归；DeepSeek 供应商口径见 [官方限速说明](https://api-docs.deepseek.com/zh-cn/quick_start/rate_limit) |
| R-12.2 | **结构化调用点必须声明全部必需顶层键**；parse / require / schema / validator 任一闸失败都驱逐坏缓存，修复稿始终重走 `json.loads → require → model_validate → validator`。禁止异常驱动地降低 effort / thinking，也不改供应商或 schema mode；Flash→Pro 保持同 effort / thinking / max_tokens。Stage2 blueprint 使用按失败类型登记的恢复策略：reasoning 中完整 JSON 一旦通过全部闸立即采纳并铸 checkpoint，不再为正常 content 多发一采；BOM / 容器尾逗号只做窄本地修正并全量复验；有可忠实恢复材料的 `answer_lost` / parse / require / schema 首次进入本 run 唯一 batch，修复拒绝或未通过后最多一次 Flash 定向修复、再最多一次 Pro constraint rebuild；dynamic validator 不交给通用 JSON 修复，只走一次 Stage2 定向恢复与一次 Pro rebuild；无有效材料的 `answer_lost` 不重复相同 Flash 原请求，直接 Pro rebuild；`finish_reason=length` 不做同配置重采，进入下一种已登记动作，仍失败即终态。传输交付 attempt 与内容/语义修订 turn 必须分账：Agent 对 429、408/425/5xx、已识别连接/timeout，以及“403 `Request not allowed` 且 token 明确全为 0”执行首发加最多三次的 2/4/8 秒有界退避；四次逐项进入 ledger，但没有可采纳回答的 attempt 不进入 session turn/checkpoint `attempt_links`。五小时用量窗口与登录过期不秒级热重试而进入可恢复等待，其他 403 按权限错误立即终态；legacy transport 的不可重试 4xx、配置错误、预算拒绝仍立即终态。失败签名统一绑定 request fingerprint、模型/effort/thinking、request mode、finish reason、gate 与归一 reject detail；同一 `run_id` 内相同签名不得重复相同动作，只能升级或结束。其他结构化调用继续使用各自已登记的通用恢复链；失败候选一律视作不可信数据，动态恢复 prompt 仍按完整候选/诊断摘要/最小诊断逐份预检并记录 `request_mode` + `recovery_from_attempts`。来源账的 adopted model 只认 accepted attempt → ledger 精确联结；拿不到联结就拒绝落盘 | `HARD`（`novel/llm/client.py::ask_json` + `novel/agents/base.py::classify_agent_command_failure` + `novel/agents/session_client.py::_invoke` + `novel/llm/response_repair.py::failure_signature` + `novel/llm/stage2_runs.py`）+ reasoning 单 wire/失败类型路由/交付退避/动作去重/模型来源账回归 |
| R-12.2a | 所有 Agent executor 的响应 schema 必须在公共构造口归一并通过 Claude/Codex 都可接收的严格子集，再由同一份 canonical 字节计算 identity、schema SHA 并交给具体 driver；禁止 driver 私自删改。根必须是 object 且不得为联合，每层 object 必须 `additionalProperties=false` 并把全部 `properties` 无重复列入 `required`；`discriminator` 只作分支注解予以删除，`oneOf` 等价归一为 `anyOf`，单值 `const` 等价归一为单元素 `enum`，`default` 注解删除且对应键仍必须在线上显式返回，其余子集外关键字在拉起 CLI 前关闭式失败。业务动态键映射不得发布开放 object：必须设计成带显式 key/value 的行数组，Pydantic 收到后确定性还原为既有内部 dict，重复键拒绝；正式 artifact 与新 checkpoint 的内部 payload 形状不变。旧 checkpoint 不迁移、不改字节；因 schema SHA 是恢复身份，协议变化后必须关闭式拒绝旧 restore 并由新 run 重建。任何 provider 的 `invalid_json_schema` 都归类为不可热重试的 `agent_schema`，不得洗成普通 `agent_command` 或连发四次 | `结构`（`novel/agents/session_client.py::agent_json_schema` + `novel/agents/base.py::validate_agent_output_schema` + `contracts/schema/agent_wire.py::keyed_rows_to_mapping`）+ 全部现役响应模型词表/object 闭合/动态映射 roundtrip/Claude 与 Codex 共闸/错误分类回归 |
| R-12.2b | 下游节点在开始逐条报症状之前，先判一次「上游这份东西是不是整个空的」 | `HARD` |
| R-12.2c | Claude agent 的结构化请求保留 provider schema gate（`--json-schema`），最终 stdin 必须且只能附加一次版本化 delivery instruction：只经内建 `StructuredOutput` 交最终对象，不先打印普通 JSON、不套 `response/result/structured_output` wrapper、不调用其他工具。有 output schema 时成功信封缺 `structured_output` 必须关闭式失败；只有 schema-less 请求可读取字符串 `result`。prompt SHA、request fingerprint 与 checkpoint/恢复守卫必须共用 `AgentRequest.stdin_bytes()` 的真实 UTF-8 字节，生产子进程必须以 binary stdin 原样收取，不得另拼一份或经文本换行转写；Claude 内部 `num_turns` 只作读数，不得被误当成「单次提交」闸门 | `结构`（`novel/agents/base.py::AgentRequest.stdin_bytes` + `novel/agents/claude_code.py` + session/checkpoint 共用渲染落点）+ footer 唯一/错误 wrapper/缺 structured_output/schema-less fallback/raw-stdin/hash 对账回归 |
| R-12.3 | 子进程环境三件套（UTF-8 相关）缺一不可：缺了它，任何打印 `✓` 的节点进管道就非零退出——而一次**成功的付费跑**会被报成失败 | `HARD`（回归测试） |
| R-12.4 | 普通缓存仍有三条语义：关缓存是**同时关读写**；参数不变的重跑可以零 wire；坏响应不得留在可命中的内容缓存。验证后 checkpoint 是第四种、明确独立的恢复状态：命中不记 cache hit，不产生新 wire，保留原 accepted attempt/model/成本联结并发出 `checkpoint_restored`；只允许同 `run_id`、同代码/契约/架构/输入/request/SHA 与闭合 ledger 恢复。Stage2 按章 checkpoint；Stage4A 先铸策略 checkpoint，再为每个已验章节铸独立 checkpoint，策略或任一章失败不得抹掉其他已验 unit，同 run 只补缺失/失败单元。Stage4 checkpoint 不是正式 artifact：N+1 单元齐全后仍须确定性聚合、整书 schema/HARD 与 commit journal 原子提交，之前不得出现部分 `blind_candidate.json`。主动 `--resample` / `--chapters` 首次执行不得命中旧 checkpoint；Stage4A 不开放公开 `--chapters`，恢复只由同一公开 node/run 自动发现 unit checkpoint。canonical `RunContext.run_id` 在同一 producer identity 内覆盖一次首轮或 repair/replan epoch 的请求 → 响应修复 → resume → 聚合提交，并进入 ledger、LLM audit、repair、checkpoint 与 adoption；AgentRun 跨代码/skill epoch，或在 Stage4 typed repair/replan 动作边界滚动 fresh epoch 时，均按 R-11.19/R-5.26 换新 producer identity，只由显式 `parent_run_id` 维持父子关系。`agent_progress.jsonl` 是外层 execution beacon，继续使用 parent run。job/queue/agent/chain/attempt id 保持各自含义只建父子关系。手敲 CLI 首次自动生成并打印，resume 必须显式 `--run-id` 复用；历史 JSONL 无 run_id 仍可读但只标 legacy/ungrouped，不回填、不按时间窗口猜。本字段不进正式 artifact provenance、缓存键或贵贱判断 | `HARD`（`novel/run_identity.py` + `novel/run.py::RunContext` + `novel/llm/stage2_runs.py` + `novel/llm/stage4_runs.py` + ledger/audit/progress/adoption 联结回归） |
| R-12.4b | `R-12.4` 的“只允许同 run”约束专指会恢复当前 logical/provider session 的普通 checkpoint hit。`R-5.26b` 的 Stage4A targeted-repair bridge 与 `R-11.18` 的 Stage4B bridge 是显式的**跨 run 响应采用**，不是 session/checkpoint restore：source 始终只读，target 不复制 checkpoint、不续旧 provider，会以 immutable manifest、source-stable lineage、当前 validator 与分 run ledger 单独证明；表外节点、策略 unit、普通首轮和 replan 仍不得借此跨 run。`R-5.26c` 是另一种跨 ended-producer **提交补尾**：它只重验并提交 source journal 已 sealed 的 aggregate，不采用新响应、不复制 checkpoint、不续 provider，因此不放宽 replan 禁止 historical response adoption 的边界 | `结构`（`novel/llm/agent_runs.py::restore_historical_run` + `novel/llm/stage4_runs.py` 的 historical adoption 与 repair aggregate recovery）+ ordinary restore/cross-run adoption/commit-tail 正反例回归 |
| R-12.5 | **发出去的 prompt 里不许出现内部结构名**（拍号 `bNN_NN` / 人物 id `char_001` / 规则 id `wr_01` / 内部枚举名 / 节点名）。每层只说自己的语言：prompt 只讲任务，不提节点名、不提消费方、不提归属 | **无守卫 → `B-05`** |
| R-12.6 | **每一份最终逻辑请求先过对应的精确上下文预算，再碰其缓存、外部请求、ledger 或正常产物。** Stage4A 的策略请求与每章请求分别是登记在架构中的最终请求，必须逐份预检；`stage2.global`、`stage4.plan` 与所有统一 `LlmClient` 调用同样按本次最终 system、user 与官方消息 framing 精确计 input tokens。原始 Flash 请求及同 prompt 的 Pro 兜底必须在该逻辑调用首个副作用前同批预检，任一项拒绝即该逻辑调用 0 副作用。R-12.2 的定向恢复 prompt 依赖尚未发生的响应，不能在首发前伪造“最终内容”：它在每次后继采样前按完整候选 → 诊断摘要 → 最小诊断逐份精确预检，只允许读取/发送第一份通过者；全拒时退回已经通过首轮预检的原请求，不能在已经付费后新增一个预算终态。`deepseek-v4-pro` / `deepseek-v4-flash` 的保守有效上下文上限固定为 1,000,000，单次输出上限 384,000，且要求 `input_tokens + max_tokens <= 1,000,000`。tokenizer 固定为 DeepSeek V4 官方 revision `0e1a0e5e52aea73055f50fef6f2423db370265b6`，资源 SHA-256 `8f9f37ca37fdc4f5fd36d5cf4d3b0e8392edb4e894fd10cc0d70b4957c8633cf`；未知模型、依赖/资源不可用或 hash 不符全部关闭式拒绝，不用字符比例猜测。Stage4A 的策略 + N 单章是**语义契约预先登记的聚合拓扑**，不是预算失败后的动态分块；任何一份已登记请求仍不得截断或临时再拆，预算拒绝本身不触发模型兜底。错误必须给出模型、tokenizer 版本、输入、声明输出、合计、两项上限及缩减正文的行动建议 | `结构`（`novel/llm/budget.py` + `novel/llm/client.py::_preflight` + Stage4 agent request 预检）+ 首发零副作用/动态恢复预算降级/边界/Stage2/Stage4/CLI 回归测试 |
| R-12.7 | 全链身份必须显式拆为四类：`review_subject_sha256` 覆盖人实际看到的内容、证据与 finding，`review_contract_version` 只表示影响审阅语义的显式契约，`validation_seal` 表示当前代码/规则下的免费机械复检，`execution_epoch` 表示本轮 code/skill/profiles/admission/provider 上下文。人工决定的适用性只由前两类决定；代码变化先令 effective 投影暂停，新 validation seal 通过且 subject/contract 未变时 raw typed decision 自动复活。审批盘必须永久保存 raw 决定，前置闸失效只产生只读 effective 投影，禁止把级联 false 覆盖回 raw proof；任何人工修改从 raw record 出发。所有现役人审写入口——Stage2 Blueprint/关系编辑、Stage4 design/structure/完整章 edit、Writing、Stage5 closure 与 Stage6/7——对外均只收一枚 opaque review token 加 actor/reason/typed ruling；execution permit 同样只暴露 opaque execution token。完整 subject/contract/validation/workpack SHA 仍在内部审计并由服务端重建，调用方不得建立第二套 public hash builder | `结构`（`novel/orchestrator/approvals.py::_read_state` / `::_effective_record`、`novel/orchestrator/inspection.py::capture_operator_snapshot`、`contracts/schema/orchestration.py::ExecutionEpoch`）；前置失效/复活、可见 finding 精确过期、代码漂移不重签和全 token 表面的 TOCTOU 回归查 |

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
| R-13.5 | 需要人裁决的闸门会挡住队列并明确说出在等谁。Stage2 的特殊顺序是：global 与 blueprint 可先作为各自独立付费命令执行；在最早 Stage4 项前插入无 argv 的 `approval:stage2-global-review` 阻塞项，不能让 review pending 阻断 blueprint，也不能让 Stage4 越过它产生 wire/dispatch；operator 同快照若见 Global review 已 current、但完整审批投影的 `gate.s2` 因 seen/proof 漂移为 false，仍复用这个 Stage2 blocker 完成阅读事实恢复，不能先发布 Stage4 design reviewer。若 review 在 blueprint 运行中提交，决定正常保存；同书写盘互斥仍适用。其余 stage0 批准、Stage5 全书写作授权、文案定稿、人名表定稿与待人工非空照常阻塞；没有 current `writing/writing_proof` 不拿只读默认值猜授权。Stage6/7 定稿也不得只看盘上 `approved != null`：队列必须复用各节点的 production `approval_is_current`，要求声明仍绑定当前代码、规则、主体与 workpack；旧声明必须先形成对应 `stage6.approve` / `stage7.approve` blocker，使 exact recovery permit 可达，不能先暴露一个下游 ready 动作、再由下游拒绝旧批准而形成路由死锁。普通 durable 多步队列遇到 `redo/unknown` 时必须形成无命令的 `resume:manual-decision`，不能把冻结快照上的“要重来/判不了”伪装成 runnable；唯一例外是 AgentRun 单步 operator：有正式产物的 producer 仅当每份 stale 输出的权威汇总 reason 都是 declared projection 的 `consumed_changed`、节点在 `NodeSpec.operator_redo` 登记唯一策略、且没有 unreadable、盘上既有却 `unknown` 的输出或 INVALID 时，才可导出 canonical exact flags；同一 producer 的缺失输出可以由该 canonical 动作一并补齐。另允许专用 currentness 已明确为 stale 的 Stage5 Review 走登记的零成本编号重算。两类都须铸同快照 execution token，每落一步重新规划；producer 汇总为 `file_changed` 与任何盘上既有却 `unknown` 的输出永不走该例外 | **`结构`**（`novel/orchestrator/queue.py::plan_book` / `_human_gate_satisfied` + `novel/orchestrator/state.py::operator_redo_flags` + `novel/orchestrator/operator.py::_next_from_snapshot` + `novel/stages/stage6/hook.py::approval_is_current` + `novel/stages/stage7/rename.py::approval_is_current` + 普通队列关闭、AgentRun Stage2/Stage5 exact REDO、Stage4 阻塞与 Stage6-7 current-workpack 回归） |
| R-13.6 | 队列本身**不产生新的判定**，也不放宽任何落盘闸门 | **`结构`**（`tests/test_layering.py::test_the_queue_cannot_reach_the_judgement_layer`：`queue.py` 够不到 `novel.report`，连「顺手判一下」都做不到） |
| R-13.7 | 不同小说的作业可以并行执行；互斥范围只落在各自 `sid`。每个后台作业独立启动 CLI 子进程，一本书失败或阻塞不占用另一本书的键 | `结构`（`novel/orchestrator/queue.py::_execute` 每书一个线程，调用 `novel/orchestrator/jobs.py::start`；互斥仍唯一落在 `novel/orchestrator/jobs.py::active_key`）+ barrier 证明两本书同时进入子进程的回归 |
| R-13.8 | 正常主路的权威时间线由确定性 RunSupervisor、agent lifecycle events、admission claim、node beacon 与付费 ledger **自动形成**；Commander/Executor 不得在 prompt、skill 或正常主路中手工调用 telemetry。`_service/operator_phases/<session_id>.jsonl` 只保留 legacy/诊断兼容：它与上述权威账正交，不进入 `NODES`、不冒充模型调用、不成为交付闸；缺失、未闭合或无法联结时完整 phase 墙钟分解必须是 unknown，不能据此阻断运行或洗成 0。若显式诊断使用该账，phase 仍闭合为 preflight/inspect/preview/execute/review/approve/finalize/verify，区间并集不重复计时，余量为 unattributed；prompt、正文、reason、argv、路径、密钥、模型 token/价格不得落账，宿主 usage 越界时声明 `unavailable_host_boundary`，不拿墙钟估成本 | `结构`（`novel/orchestrator/agent_supervisor.py` + agent event/admission/beacon/ledger fold + `novel/operator_telemetry.py::summarize` 的 legacy 诊断）+ 无手工埋点主路、缺账 unknown、隐私与 layering 回归 |

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
| R-14.4 | 续跑前必须**重新判定新鲜度**：上游在中断期间变过，就不是「续」而是「重来」，要继续保留 `redo` 原结论；判不了就是 `unknown`，也不能折成“待继续”。普通队列对 `redo/unknown` 均不得构造或自动执行命令。AgentRun 因为每步后重新采完整快照，可在注册 producer 的每份 stale 输出都汇总为 `consumed_changed` 时导出唯一 canonical flags：书级 producer 用默认完整重建，Stage5 写作必须导出 stale/missing 的精确 `--chapters`；Stage5 机械 Review 则只在专用 currentness 已明确 stale 时零成本新增一轮。同一 producer 的缺失输出可由 canonical 动作补齐；producer 有输出汇总为 `file_changed`、存在 unreadable、盘上既有却 `unknown` 的输出或 INVALID、策略未登记或 flags 不唯一，仍关闭式停在 `resume:manual-decision` | **`结构`**（`novel/orchestrator/state.py::resume_verdict` 五态保持原值 + `::operator_redo_flags` 的封闭策略；`novel/orchestrator/queue.py::plan_book_from_overview` 默认关闭、仅 operator opt-in；Stage2 与 Stage5 精确重放回归） |
| R-14.5 | 中断留下的半成品（写到一半的章 / 空壳响应）**入显式终态并踢出缓存**，不许被当成「已完成」续过去 | `结构`（`novel/llm/client.py` 空壳响应 evict；`ArtifactStore.write` 临时文件 + 原子替换，所以被杀时不会留下半份 `chNN.json`；stage5 续跑只认通过契约与落盘闸门的正式章，fixture 覆盖“已有章跳过、缺章补跑”） |
| R-14.6 | 进程被杀时**不保证原子性的地方要列出来**。⚠ 原型里「不做取消」的理由曾写着「落盘非原子，中途 kill 会留下截断的 JSON」，而落盘**早已是原子替换**——结论仍成立（钱已经花了），但它现在只剩一条腿撑着 | **`结构`**（那张表在 `docs/schema/_orchestration.md` §八，**六类**，每类带「崩在中间会留下什么 / 怎么自愈」） |
| R-14.7 | Operator 的 state/inbox/终审读取必须使用调用栈内一次性冻结快照，不做 module/global/LRU 或跨写缓存：一次 capture 固化同一代码身份、overview 与七层 check，state/inbox 只投影这份 snapshot。active surface seal **只**覆盖 registry 登记且当前活动的正式产物、当前审批 head 与本次登记外部输入；历史 runs、`_rounds/`、拒稿仓、scratch、临时文件和别书追加不得制造假漂移。普通 state/inbox 不扫描 ledger/beacon，终审才显式纳入当前 sid/run 的权威账。canonical finalizer 在节点结束时只 collect 一次，基于同一 snapshot 一次性 render → validate → compare-and-swap → 原子写终审包；Commander 不逐动作增量修改 Markdown。验证失败不改 review、不调交付终验；写后逐字 readback并丢弃 snapshot，再以原始 epoch identity 在全新读作用域独立 `delivery.assess`。写盘后不得复用 collect facts；旧 collect/sync/validate/verify 入口只留诊断兼容 | `结构`（`novel/orchestrator/inspection.py::capture_operator_snapshot` / `::read_surface_seal` + `novel/views/inbox.py::inbox_from_snapshot` + finalizer + `novel/orchestrator/delivery.py::assess`）+ active-surface、历史噪声、call-counter、漂移、原子写与 fresh-assess 回归 |

> **`R-14.7` 的历史 journal 特例是动态 external-input CAS，不是扩大 active surface。**
> 普通 snapshot 仍不扫描历史 runs。只有 operator 已选中唯一 Stage4 repair aggregate
> prepared 时，才将 `<source_run_id>/design/design-repair-aggregate.json` 的固定解析路径、
> 原始字节 SHA 与 journal self/commit SHA 收进该枚 `ExecutionPermit.external_inputs`；
> claim 后重读不等即拒绝。不得把全部历史目录塞进 surface seal，也不得因别书或
> 无关 run 追加制造假漂移。
>
> **服务空间生命周期的冻结边界：**新 writer 的 run/checkpoint/history 只写
> `_service/runs/<sid>/stageN`；旧书内 `_stage*_runs` 仅在 producer 明确登记时兼容读，不搬动、不回写。
> scratch 使用 `_service/scratch/<sid>/<session>` 并按精确 session 收尾。日志、legacy telemetry、完整
> v3 trash 与 delivered+committed checkpoint 的维护只走 `service_lifecycle preview → apply --yes`：四类
> TTL 默认全关，候选总量有硬上限，apply 对冻结 plan 作 exact CAS。每项删除前先写 item prepared；
> checkpoint 另先写永久无正文 receipt，trash 使用永久 purge receipt；aggregate/item receipt 使同 plan
> 在中途崩溃后可以幂等补尾，不能把“目标恰好不见了”猜成成功。账本、admission、beacon、agent/job/
> session 事件与恢复 manifest/receipt 永久保留。v3 trash 同时承载 book、approval 与该 sid 的 service run；
> retention 与 trash restore 的正式 API/UI 已交付：两者都先 preview，apply 原样回传完整 token/CAS 并
> 显式确认；restore 仍由受控 CLI 子进程移动产物，服务进程只写 `_service` scratch。restore 的 hard-kill
> 窗口由永久 v2 prepared receipt 逐面绑定 book/approval/service run 的 archive/live 精确路径与
> SHA/tree 身份；显式重放、trash sweep 与服务启动时的有界 CLI child reconcile 只在每面恰有一个
> 权威副本时幂等补尾，双份、缺失或漂移均
> 原样留盘并关闭式拒绝。未编辑 `.INVALID` 进入 numbered `_rounds` 后，只有 locator-bound
> `_revalidation` 基线逐字相同时才把重复正文压成绑定 numbered 路径与 raw SHA-256 的小引用；numbered
> 归档仍保留原字节。若恢复把该轮精确搬回 live，只有 live SHA 仍匹配才可续跑；人工编辑导致内容不同
> 则两份完整证据都保留。旧 `_revalidation` 完整文件继续只读兼容，不使用 symlink/hardlink 或目录清扫。

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
| **B-11** | 修剪 | **自动收缩臂仍关闭（2026-08-18 整臂下线）**：曾经的关闭方式是「同槽结构化候选 + 确定性 claims 对账 + 校准 A/B 双盲同 PASS 才采用」，而那道校准闸从未在任何宿主上打开过。现役没有由 `length_over` 自行生成、比较或采用短候选的路径：超上限章先零调用落 identity `TrimAudit` 转人工；人可 `--retain-pretrim` 销账、`--apply-edit` 提交完整章，或由受托 Reviewer 按 `R-7.31` 对一章给出关闭式指令、先落正式 request 后调用 Writer 重写完整章。最后一种是显式人审授权的写作动作，不是自动候选采用，也没有证明其语义质量可由机器裁定。 |
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
| 计划的判据表（`stage4`，**34 条现役**，计划与上下文共用一张） | `R-5.1` / `R-5.2`、**`R-5.3b` / `R-5.3c`**、`R-5.4`、`R-5.6` / `R-5.7`、**`R-5.9b`**、**`R-5.10b` / `R-5.10c` / `R-5.10d`**、**`R-5.11`**、**`R-5.12` ~ `R-5.19`**、**`R-5.24`**，以及跨层的 **`R-3.21b`**（`narrative_relation`；`R-5.3` / `R-5.11b` 归 `载体`；`R-5.5` / `R-5.8` / `R-5.10` 归 `结构`；`R-5.9` 与 `R-5.20` 由人结合无阈值读数判断，旧自由文本缺口仍见 `B-06`） |
| 上下文组装的后置条件（**同属上面 `stage4` 那 34 条，不另计**） | `R-6.1` ~ `R-6.4`，另加一条**跨层**的 **`R-3.3b`**（`ctx_hinge_delivered`：抽到的保真点必须条条出现在下发面上——它守的是 stage2 与 stage4 之间那一步，因此挂在 `R-3` 而实现在本层）（`R-6.5` / `R-6.6` 归 `结构`、`R-6.9` / `R-6.10` 归 `载体`、`R-6.7` 无守卫 → `B-16`、`R-6.8` 无守卫 → `B-01`） |
| 写作与验收的判据表（`stage5`，**51 条现役**，全为机械判据） | 严重度实算为 **hard 7 / info 26 / metric 13 / unver 5**；`repair` 实算为 **UNIQUE 12 / FREE 12 / NONE 27**。对应 `R-7.3` / `R-7.3b`、`R-7.4` ~ `R-7.9`、`R-7.11`、`R-7.15` ~ `R-7.18`、`R-7.20` ~ `R-7.25`、`R-8.1b`、`R-8.3b`、`R-8.9b`、`R-8.11`。Stage5 不接收 Stage3 定性块；未揭示秘密边界直接从 Stage2 时间线计算，不再登记重复 Context 清单的 `secret_unknown`。`R-7.18b` / `R-7.18c` 的旧 Stage5 语义节点已转授；`R-7.19` 的旧判据已随非 canonical 路径退役。`R-7.1` / `R-7.12` / `R-7.24` / `R-7.26` / `R-8.1` / `R-8.4` ~ `R-8.6` / `R-8.7b` / `R-8.9` / `R-8.13` ~ `R-8.16` 归 `结构`，`R-8.2` / `R-8.3` / `R-8.7` / `R-8.12` 归 `载体`，`R-7.2` / `R-7.11` 是人，`R-7.5b` / `R-7.9b` / `R-7.10` 仍是明示盲区。<br>⚠ 本层不再有现役语义判据池；结构/载体行说的是机制，不是另加一条产物判据 |
| 文案的判据表（`stage6`，**23 条现役**，机械判据与语义审判共用一张注册表） | `R-9.3`、**`R-9.3b`**、`R-9.4` ~ `R-9.6`、**`R-9.9` ~ `R-9.21`**、**`R-9.23`**（`R-9.1`/`R-9.2`/`R-9.22`/**`R-9.24` ~ `R-9.26`** 归 `结构`/hash-bound 人审面、`R-9.7` 见 `R-9.19`、`R-9.8` 见 `R-9.20`） |
| 人名替换与成书的判据表（`stage7`，**35 条现役**） | `R-10.2b`、**`R-10.3b` ~ `R-10.3d`**、`R-10.4` / `R-10.5`、**`R-10.6c`**、**`R-10.7` / `R-10.7b`**、`R-10.9`、`R-10.11`、**`R-10.12b`**、**`R-10.13` ~ `R-10.14b`**、**`R-10.16` ~ `R-10.18`**、**`R-10.20`**（`R-10.1`/`R-10.2`/`R-10.3`/`R-10.6`/`R-10.6b`/`R-10.8`/`R-10.15`/`R-10.19`/**`R-10.21`** 归 `结构`/hash-bound 人审面，`R-10.10` 无守卫 → `B-21`、`R-10.12` 仅余语感与跨名关系无守卫 → `B-12`） |
| 界面的结构纪律测试（`tests/test_api_discipline.py`） | `R-11.1` ~ `R-11.4`、`R-11.6` ~ `R-11.11`、**`R-11.13`** |
| agent capability / profile 契约（`tests/test_agent_capabilities.py`、`tests/test_schema_api.py`、`tests/test_api_http.py`） | **`R-11.14`**；直接 job 纵切另由 `tests/test_orchestrator_nodes.py`、`tests/test_orchestrator_jobs.py`、`tests/test_api_http.py`、`tests/test_cli.py`、`tests/test_unit_session_pool.py` 及 Stage2/4/5/6/7 agent / recovery 回归守 **`R-11.15` ~ `R-11.18`**；Commander、页面双角色配置、运行契约与 typed 代批由 `tests/test_commander_drivers.py`、`tests/test_commander_context.py`、`tests/test_agent.py`、`tests/test_api_agent.py`、`tests/test_skill_operator.py`、`tests/test_approval_cli.py` 与前端 agent profile/dialog 回归守 **`R-11.19`**；确定性下一动作/execution permit、canonical skill 整树指纹、node/generic admission、reconcile、子执行闭合与 agent-only 整书验收由 `tests/test_operator_next.py`、`tests/test_execution_permit.py`、`tests/test_skill_contract.py`、`tests/test_skill_operator.py`、`tests/test_commander_admission.py`、`tests/test_write_admission.py`、`tests/test_beacon.py`、`tests/test_agent.py` 与 `tests/test_verify_agent_e2e.py` 守 **`R-11.20`**；并守 `R-11.11` 的 nullable 历史身份不得倒推 |
| 编排层 | `R-12.1` ~ `R-12.4` |
| 跨层 | `Q-1` ~ `Q-12` |
| **（新建，原型无对应守卫）** | `R-13.1` ~ `R-13.8`、`R-14.1` ~ `R-14.7` |

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

**本节内容已迁出，契约不再保存第二份。** 现役落点：产品边界与七个产物层的人类任务 → 仓根
`README.md`；当前路由与屏幕职责、UI 硬规则 U-01～U-20、技术与数据缝 → `web/DELIVERY.md`。

节号保留不重排：§七～§九仍被仓内引用。

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

`tests/test_docs_references.py` 扫描本文件、`AGENTS.md`、README、`docs/LESSONS.md`、
`docs/OPERATIONS.md`、`docs/DECISIONS.md`、`docs/REQUIREMENTS.md` 与 `docs/schema/*.md`。
反引号中的 R/Q/B 编号必须存在；以 tests、contracts、tools、novel、docs 开头
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

## 历史决策与验证证据

重大产品裁决、实验边界和真实运行证据已迁至 `docs/DECISIONS.md`。本文件只保留现役、可执行的契约；
历史记录不得覆盖这里的 R/Q/B 与当前 schema。
