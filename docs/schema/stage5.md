# stage5 产物 schema

写作 + 全书验收。**这一层是全链唯一产出「书」的地方**，也是全链**唯一带 LLM 返工回路**的层。

```
stage5/<mode>/chNN.json          LLM 产，逐章一次调用 —— 唯一承载正文的产物（deep / shallow 两条臂）
stage5/<mode>/chNN.INVALID.json  被落盘闸门拦下的问题稿（同一份章契约，只多一个 errs）
stage5/<mode>/chNN.rejected.json 两次都相对旧正式稿退化的修订证据；旧正式稿保持原位
stage5/<mode>/_rounds/…          上一轮与被替代的 INVALID / rejected 历史归档
stage5/review_<n>.json           确定性验收报告（**0 成本，不调 LLM**），整本写完之后跑
stage5/trim_audit_chNN.json      收缩候选的 durable 机械/语义审计（v1）
stage5/trim_resolution_chNN.json 人工明确保留 pretrim 的 hash-bound 销账（v1，0 成本）
stage5/wontfix.json              人的「这条我不修」（0 成本，理由必填）
stage5/draft.md                  两条臂合成的全书拼稿（**字节形态**，见 §五）
```

对应需求 **R-7**（写作）与 **R-8**（验收与返工）。
读法（证据列的取值）见 `_README.md`。
**载体 = Pydantic v2**（已定），但本文只定内容、不写模型——模型在骨架阶段照本文写。

> **样本面：真实盘上 3 本 = 31 输出章（deep 11 / shallow 20）· 12 份 review（每本 4 轮）·
> 6 份 `_rounds` 归档 · 0 份在场的 `.INVALID` · 0 份 `.rejected.json` · 0 份 `wontfix.json`；
> 可提交 fixture 另含一份 versioned `.rejected.json`，用于无版权回归。**
> 与 stage2~4 一样，这一层没有「全语料 163 本」可退——**跑一章就是一次高推理调用**。

---

> ## ⚠⚠ 五条贯穿本层的事实
>
> ### ① 这一层是**两条写作臂 + 两类验收**，产物职责分开
>
> | | 谁 | 差别 |
> |---|---|---|
> | **臂** | `stage5.write --mode deep`（闭卷）· `--mode shallow`（开卷） | **只差 (system prompt, 给不给源章原文)**，产物形状逐字节相同；`mode` 同时是目录名 |
> | **写作** | 首轮逐章写作 + 落盘闸门；仅把 `REPAIR_UNIQUE` 或独立语义审判点名的可引用问题送入定向自救 | 正式稿始终只有 `chNN.json`；失败尝试进 `.INVALID` / `_rounds` |
> | **验收** | `stage5.review` 做 0 成本机械验收（书级语义审判已下线，见 §三） | 47 条注册表，一份产物 |
>
> 正常整书写作不分别启动两个 mode，而用
> `stage5.write --deep <章号列表|none> --shallow-rest`：人工选择一次，两臂同时起跑，
> 各自的逐章线程池硬上限 32；单臂 mode 入口只留给维护与定向返工。
>
> ⚠⚠ **默认深度选择 = 全部计划章（2026-08-22 裁决）**，此前是常量 `(1, 2, 3)`
> （`novel/orchestrator/approvals.py::default_deep`）。推翻旧默认的是一次**成书级**
> 实测（无情道 23 章，`tools/book_overlap.py`：按 `stage7/name_map.json` 回映射人名后
> 逐章量 20 字窗重合）：
>
> | 臂 | n | 中位 | 最高 | 整句照搬率 |
> |---|--:|--:|--:|--:|
> | deep（闭卷） | 3 | **0.017** | 0.031 | **18.2%** |
> | shallow（开卷） | 20 | **0.226** | **0.547** | **55.4%** |
>
> **13 倍差距**；全书 290 条照搬句里只有 60 条是 `verbatim` 制度要求的，其余 230 条
> （79%）是写手自发照抄。
>
> ⚠ **开卷臂没有被删，只是不在默认路径上了**：`--mode shallow` 一个字没动，
> `B-03` 那个洞仍在代码里。⇒ 走开卷从此是**例外**，由 `arm_split_report`
> （`R-7.6c`，`度量`）无条件报出「本书 N/M 章走开卷 + 这几章逐章重合度跨度」，**只报不判**。
> ⚠ 连带后果两条，都是**已知代价不是缺陷**：`dialogue_halved` 在默认路径上分母归零
> （⇒ 落 `unver`，见 §七）；`dialogue_fewer` 会大面积命中（0.7× 那条线正压在闭卷的
> 自然效应上），**读它要看 `dialogue_report` 的分布，不看命中数**。
> ⚠ **`set_deep` 在 stage5 开写后锁死**（`approvals.py::_has_stage5_work`）
> ⇒ 已写完的书改不了它的深度选择，A/B 必须另起 `--base`。
>
> ### ①.5 下发面上 **events 是材料，不是句子**（2026-08-22）
>
> `CtxBeat.events[]` 的信息密度实测是源章正文的 **87%**（去标点 7716/8821，8 章超 100%），
> 而章预算锁在源章字数 ×0.90~1.10。**同样的事件点数 × 同样的总字数 ⇒ 每个事件点分到的
> 字数必然与原文相同** ⇒ 成稿必然是原文的等密度重述。而下发面此前把这份清单直接命名为
> 「本章要写的内容」「要写这 N 件事」——**那在物理上就是「一条一句地对译」**。
>
> ⇒ 改的**不是给什么材料**（一条都没删、没合并），是**给材料的名分**：
>
> | 落点 | 改成 |
> |---|---|
> | 块头 | 「本章要写的内容（**只写这些**…）」→「本章的**材料**（…）」+「⚠ 每一拍下面列的是【事实材料】，不是【要写的句子】。**事实只有这些**…」 |
> | 逐拍（`prompts._unit_rule`） | `scene` ⇒「把这一拍写成**一个连贯的场面**…**不要一条一句地各写一遍**」；`summary` ⇒「用约 N 字把这几件事**合并交代过去**：不要演成场面、不要给它们各写一句」 |
> | `ORDER_RULE`（两条臂共用） | 追加「一拍是一个叙述单位，不是一张待办清单」+「篇幅在拍与拍之间本来就不平均」 |
> | `prev_context` | 不再逐条列上一章每一拍的每一条 event ⇒ 只给两个 goal + **末拍** |
>
> ⚠⚠ **保真点原地不动。** 四组保真材料仍挂在**它所属的那一拍**下渲染；把它们剥离或
> 挪到块尾统一列，闭卷写手会丢引号句 ⇒ `verbatim_missing`（HARD）直接把该章打进 `.INVALID`。
> ⚠ 「只写这些」这条边界**不删只移**：它守的是「不得新增事实」，那是**事实的属性**，
> 不是句子单位的属性。
> ⚠ **events 不许降级成「只做事实校验的参照」**：闭卷写手能写的只有三样——三个 goal、
> 离散保真点（点不是内容）、`events`；降级之后它没内容可写，而 `FIDELITY_RULE` 禁止发明。
> **换的是单位，不是地位。**
> ⚠ **本批不加任何阈值闸**（判据 15：没有真值支撑的阈值不立）。先给自由度、跑一本、
> 看模型用不用得起来，再决定要不要立闸。
>
> #### 付费小步读数（2026-08-22，无情道 ch4-6，独立 base `_b1` + 独立缓存，3 次实付）
>
> 同一份 stage4 计划、同一个模型（flash）、同样三章，只换**臂 + 下发面**：
>
> | 章 | 旧 shallow（旧下发面） | 新 deep（新下发面） |
> |---|--:|--:|
> | ch4 | `overlap_excl_verbatim` **51.1%** | **12.2%** |
> | ch5 | **11.4%** | **0.0%** |
> | ch6 | **52.3%** | **0.0%** |
>
> ⚠⚠ **这个落差里「换臂」占了绝大部分，不是「换下发面」的功劳。** 同书旧 deep
> （ch1-3，**旧**下发面）已经是 3.1 / 0.0 / 1.7% ⇒ 闭卷臂本来就贴着地板。
> 本次**没有**「ch4-6 × 旧下发面 × 闭卷」那一格，两个自变量在这次读数里分不开。
> 要分开得再跑 3 章旧 prompt 的闭卷臂。**别把 51.1%→12.2% 记成下发面改动的效应。**
>
> 保真与对白（验收表要的那几条，与同章旧稿逐条对账）：
>
> · `verbatim_missing` / `keep_value_missing`：**两边都是 0** —— 没把保真点埋掉；
> · `must_appear_missing`（ch5「玉质的折扇」）：**旧稿同章也有**，不是本批造成的；
> · `dialogue_report`：1.00× / 0.67× / 1.00× —— 闭卷压对白那个 ~0.7× 的已知效应
>   在这三章上只出现在 ch5 一章，另两章持平。n=3，方向性；
> · 新增命中 `deep_overlap_high`（ch4，12.2% > 10%）—— 它只在闭卷臂存在，
>   shallow 结构上不可能有 ⇒ 是**判据面变了**，不是退化，但 12.2% 对闭卷偏高，记着；
> · 新增 `length_over`（ch5，515 > 504，1.02× 上限）、`band_drift`/`dash_drift`/
>   `xinli_drift`（ch6）—— 闭卷臂的已知文风倾向；
> · `event_landing` 1 → 2 条：**逐句核过正文，内容一件都没丢**，是代词化造成的假阳
>   （见 `contracts/judges/stage5.py` 那条 note）。
>
> ##### ⚠⚠ 代价：同样三章 completion **2.14×**（这是本批最贵的一条读数）
>
> | 章 | 旧 shallow（旧下发面） | 新 deep（新下发面） |
> |---|--:|--:|
> | ch4 | prompt 5072 · completion **3 469** | 5084 · **11 465** |
> | ch5 | 5226 · **11 104** | 5290 · **15 807** |
> | ch6 | 5482 · **5 637** | 5404 · **15 987** |
> | 合计 | 15 780 · **20 210** | 15 778 · **43 259** |
>
> **prompt 侧 1.00×，completion 侧 2.14×**，而新稿的 `reasoning` 占 completion **~97%**
> ⇒ 多出来的**全是推理**，不是正文（三章正文只有 235/515/553 字）。
>
> ⚠ 这里面有多少是「换臂」、多少是「换下发面」，本次分不开——但**换臂解释不了全部**：
> 同书旧 deep（ch1-3，**旧**下发面，源章 269/519/674 字，与 ch4-6 量级相当）
> 逐章 completion 是 9417 / 10419 / 9118（均值 **9 651**），而新 deep ch4-6 均值是
> **14 420**。⇒ 剩下那 ~1.5× 的嫌疑落在新下发面上：`_unit_rule` 要求写手先决定
> 「哪件事演开、哪几件合并」，那是**要想的**，而「一条一句地对译」不用想。
>
> ⚠ **n=3、单本一书、章很短（235~553 字）** ⇒ 倍率是方向性的，不许当系数引用
> （同判据 30 那条纪律）。但方向足够清楚，够格进决策：
> 按 23 章外推，全书闭卷 + 新下发面把 `stage5.write` 的 completion 从 ~194k 抬到 ~330k。
> **这是一次质量换成本的交易，不是免费的。** 是否接受由人裁决，本文只把数摆出来。
> ⚠ 三次调用**没有走降级阶梯**（三条 ledger 行全是 `high`、各一次）⇒ 这不是重试造成的。
>
> ### ② 判据 vs 载体的分界线在本层**几乎全部倒向判据**——因为拒收的代价是一整章
>
> 照 `stage4.md` §四立的那条规矩（**按失败成本划，不是按「类型表达得了吗」划**）：
>
> | | 本层的代价 |
> |---|---|
> | **载体拒收** | 整章响应作废 ⇒ 重试 ⇒ **再花一次高推理调用**（本层最贵的一次重试） |
> | **判据拦下** | 落 `.INVALID` ⇒ `--apply-edit` 人手改 / `--revalidate` 强审计重判 ⇒ **0 次 LLM 调用** |
>
> ⇒ 本层判据**一条都不该上移**：它们判的全是自由文本的内容属性，类型系统够不着；
> 而少数够得着的（`mode` / `from_source_ordinals` 长度恒 1）**本来就没有判据**，
> 归载体是原样承认现状，不是收权。
>
> 默认逐章调用与定向修订都走结构化 `ChapterResponse`：模型只返回
> `{segments:[{slot,text}]}`。代码在预算、缓存、ledger、transport 之前先检查 prompt-private
> 字段，再要求槽位集合完整且唯一，最后严格按 producer 顺序组装。缺槽、重槽、多槽或未知键均
> 是显式终态，不能靠模型返回顺序决定成书顺序。
> `--apply-edit` 吃的也是这份**分段人工输入形状**，即完整单章的
> `{"segments":[{"slot":"s01","text":"..."},...]}`；它不是一段可丢失 slot 边界的纯文本。
> 命令本身没有 LLM 费用，但操作者仍须提供所有计划 slot 的整章全文。
>
> ### ③ 机械基线：**7 条 HARD，盘上 31 章 0 命中；那一轮的 47 条里 29 条从未命中**
>
> ⚠ 这里的 47 是**当时那一轮的分母**，与今天注册表的 47 条只是巧合相等（中间下线 2 条语义判据、2026-08-22 新增 `arm_split_report`）。
> 下面那张表里的读数一律按它自己那一轮的盘面读，不要拿今天的分母去除。
>
> ```
> counts（三本 × 最近一轮）  hard 0 · info 32/18/14 · unver 3/2/5 · metric 62/41/48
> ```
>
> 这不是「干净」，要连它的基线一起读（判据 31）：
>
> | 判据 | 盘上 | 它的基线说明什么 |
> |---|---|---|
> | `verbatim_missing`（HARD） | 0 | **177 条逐字要求全部落地**——这条是真的绿 |
> | `keep_value_missing`（HARD） | 0 | 61 条硬数字全在 |
> | `secret_leak`（HARD） | 0 | ⚠ **21 条 secret 里 19 条「连它自己的揭示章都命中不了」**——这盏灯基本不会亮 |
> | `must_appear_missing` | 0 | 30 条物件全留痕 |
> | `must_hold` | — | 27 条**恒 `unver`**，按定义查不了文面 |
> | `draft_too_short` / `draft_truncated` / `meta_leak` / `dialogue_halved`（HARD） | 0 | 各自的失效模式盘上一次都没发生过；**一个会让它红的样本都没有** |
>
> 上面的 31 章数据是语义判据加入之前的机械基线，
> 不能被改写成「语义审判已在真实小说上 0 命中」。
>
> ### ④ 篇幅带是**两个数**：逐章 `0.80~1.20×`，全书 `0.90~1.10×`
>
> 逐章 `DRAFT_BAND` = **0.80~1.20**、全书 `BOOK_BAND` = **0.90~1.10**，**各自都没有第二层宽限**
> （「写下来的带」与「执行的带」仍不许是两个数——那条约束说的是同一个问题上的两份口径，
> 不是禁止逐章与书级各有各的带）。
>
> **逐章带按实测方差定**：31 章里 **10 章（32%）落在 ±10% 之外**，两端极值 **0.814 / 1.206**。
> 那不是"写坏了"，是这一层的自然方差 ⇒ 0.80~1.20 把两端恰好包住，
> 正常写作波动不再触发收缩臂与 `length_over` / `length_under`。
> **书级带刻意不跟随放宽**：全书总量是产品承诺，而逐章的正负偏差在书级相互抵消
> （实测 3/3 本 0.949 / 1.022 / 0.980，离 ±10% 两端都还有余量）。
> 现在的 0.80~1.20 是**显式的执行带**，不是隐式的二次宽限——这两者的区别是本条的全部要点。
> ⚠ 越带也不是静默：`length_report`（metric）无条件报出，并带着 `← 超出目标 1084~1325` 标记，
> 10/31 章的 `warns` 里都有。**判据 21 在这里是正确工作的**——出声的是度量，不是告警。
>
> ### ⑤ 落盘前有一次**确定性改写正文本身**，而本层**不为它记账**
>
> `normalize_chapter`（标点归一 / 引号体归一 / 段落分隔归一）跑在**落盘之前**，
> 存进产物的 `segments[].text` 是**逐槽归一之后的字节**，模型原样吐出来的那一份**不在任何产物里**。
> 整章 `text` 只是按 producer slot 顺序用空行拼接的确定性投影，不序列化成第二份权威。
>
> ⇒ **判据 24 在这里给出的答案是「不必记账」，但理由与 stage4 完全不同**：
> stage4 的 `op` 是「那个位置本来没东西」（初始化）；这里位置上**确实有东西且被销毁了**，
> 但**没有任何判据在断言归一后的属性** ⇒ 判据 24 要防的「一条判据因为改写而永远为真」
> 这个失效模式**在本层不存在**，于是记账没有服务对象。
> **这是判据 24 的第二条适用边界：先问「有没有判据依赖这次改写的结果」。**
> ⇒ 三个计数器落成**校验报告一行**（空也要报），`quote_problems` 升成**一条判据**（§1.1）。
> ⚠ 对照 `stage6.md` §1.6：那一层的答案相反，因为那边**有两条判据**在断言归一后的属性。

---

## 一、`stage5/<mode>/chNN.json`

本层全部产物——正式章与其 `.INVALID` / 轮次归档、`review_<n>.json`、rejected、wontfix、
trim audit 与 trim resolution——**都是 schema v1**（全仓恒 v1，见 `_README.md`）。

顶层 **10 个键**。**正式稿与 `.INVALID` 共用同一份章契约**——状态由**文件名**区分，
不由字段贫富区分。

> #### ⚠ 前五个字段是 `writer_context` 的**副本**，看起来违反「能 0 成本现算的不落盘」
>
> 它们**不是抄件，是冻结下来的任务书**。判准来自本层自己付过的学费：
> `writer_context` 可以被 `stage4.context` **0 成本重建并覆盖**，而这一份成稿是**付过钱**的。
> 实测发生过一次：23 章成稿与盘上任何一版 `writer_context` 都配不上（匹配 1/23），
> 配对关系**永久丢失**——那时留在成稿里的这几个数是唯一还说得出「写手当时被要求写多少字」的东西。
> ⇒ 与「能 0 成本现算的不落盘」（`_README.md`）裁掉的那些**不同类**：
> 那些的源头（目录名、原文）不会被覆盖，这个会。

| 字段 | 类型 | 必填 | 取值面 / 范围 | 证据 |
|---|---|---|---|---|
| `output_ordinal` | `int` | 是 | 1..N；**文件名里的 NN 就是它** | **三源** |
| `from_source_ordinals` | `int[]` | 是 | **长度恒为 1**（一章对一章）。透传自 ctx | **三源** |
| `mode` | `str` | 是 | `deep｜shallow`（`DEPTH_MODES`），同时是目录名 | **三源** |
| `source_chars` | `int` | 是 | 源章对应部分字数（计划算好的）。盘上 408~1963 | **三源** |
| `budget_chars` | `int` | 是 | 本章各拍预算之和，必须为正整数 | **三源** |
| `chars` | `int` | 是 | `len(整章确定性投影.strip())`，**含空白**。<br>⚠ 与 stage3 `measurable.body.chars`（**去空白**）不是同一个口径——跨口径运算要先对齐（判据 14） | **契·码** |
| `overlap_excl_verbatim` | `float｜null` | 是 | 20 字窗口重合率，**已扣掉按契约必须逐字保留的部分**。`null` = 正文短于一个窗口，**那是「量不了」不是「重合 0」**。<br>**闭卷有上界 `DEEP_OVERLAP_MAX` = 10%**（判据 `deep_overlap_high`）；**开卷无上界，只报分布** | **三源** |
| `segments` | `ChapterSegment[]` | 是 | **唯一内容权威**；每项为 producer-owned `{slot,text}`，slot 章内唯一，数组按 producer 顺序落盘。整章正文、API `text` 与 `draft.md` 全部由它确定性派生 | **契·码** |
| `warns` | `str[]` | 是 | 提示级判定的**字符串投影**。盘上长度 4~9，共 187 条。<br>⚠ **恒非空**：4 条 metric（篇幅 / 重合度 / 破折号密度 / 连续短段）无条件产出 ⇒「有 warns」不携带任何信息，只有**读它是哪几条**才有 | **盘·码** |
| `errs` | `str[]` | **否** | 阻断级判定。见下 | **盘·码** |
| `_provenance` | `obj` | 是 | 见 §五 | **三源** |

> #### ⚠ `errs` 一个键承担**两种语义**
>
> | 出现在 | 含义 | 盘上 |
> |---|---|---|
> | `chNN.INVALID.json` | **它为什么被拦下**（非空） | 2 份（都在 `_rounds/`） |
> | `chNN.json`（`--apply-edit` 落的） | **人工稿仍然违反了什么，但人决定照落**（**可能为空**） | 1 份，值是 `[]` |
> | `chNN.json`（其余） | 键不存在 | 28 份 |
>
> 「状态只由文件名与 `errs` 区分」这句话因此不再成立：一份**正式稿**也可以带 `errs`，
> 甚至带**非空**的。今天靠 `_provenance.origin=human` 还分得开，但那是第二个落点。
> ⚠ 非空 `errs` 的正式稿**盘上 0 样本**，而 §七 已说明它会一路进成书。

⚠ **已裁撤 3 个**（存量产物里还有，重跑后消失）：`op`（随 `stage4.md` §一）· `mech` · `dialogue_count`。

### 1.1 两个被裁掉的记账块 —— **信息一条都没丢，只是换了落点**

> #### `mech` —— 归一链的记账（原 `{punct_fixed, quotes_fixed, quote_problems[], paras_fixed}`）
>
> **0 代码读者 · 0 报告出口 · 盘上 31/31 四项全 0、`quote_problems` 全空。**
> ⚠ 但它**不是 stage4 `op_forced` 那种「恒定的噪声」**——那一份记的是「这里本来是空的」（0 bit），
> 这一份记的是「这一次没改动」，**它有真正的另一支**（写作日志里 `标点归一 N 处` 是活代码）。
> 所以处置**不是简单删掉**，是把它**拆成两半各归各位**：
>
> | 原来的键 | 去哪 | 为什么 |
> |---|---|---|
> | `punct_fixed` · `quotes_fixed` · `paras_fixed` | **校验报告的一行**（`Q-1`：空也要报） | 它们是「这一次归一改了多少」，那是**一次运行的读数**，不是产物的属性。记在产物里而没有出口，正是它 0 读者的根因 |
> | `quote_problems[]` | **升成一条 INFO 判据 `quote_unbalanced`** | 它根本不是记账，**它是一条结论**：「引号配对异常、未归一、留着交人工」。把一条结论塞进记账块，就是它既没有出口、也没有守卫的原因。<br>⇒ 顺带让 `R-7.7` 第一次有了真守卫（判据 35） |
>
> #### `dialogue_count` —— 原 `{src, got}`
>
> **0 代码读者，且可 0 成本现算**（源章 + 成稿都在盘上）⇒ 裁。
> 但它承载着本项目**效应量最大**的一条跨书结论，所以**同时补一条无条件的度量** `dialogue_report`
> ——本层原本只有 `dialogue_fewer` / `dialogue_ratio_drift` / `dialogue_halved` 三条**告警**，
> 「这本书的对白比是多少」**只有在漂了的时候才说得出来**，正是判据 21 要防的形状。
>
> 盘上重算（31 章）：逐章 **deep 中位 `got/src = 0.80`（0.53~1.07）· shallow 中位 `1.00`（0.62~1.75）**；
> 书级合计 **deep 0.86 / 0.79 / 0.87 · shallow 0.93 / 1.02 / 1.01**。
> 加上更早那批（deep 0.74/0.67/0.68 · shallow 1.00/1.01/0.94）⇒ **6/6 本方向一致**：
> **闭卷写手会系统性把台词压成转述**，而开卷不是靠抄（最低重合度 0.2% 的那一本仍保住 0.94）。
> ⚠ 但**这一批的效应弱了约一半**，别把旧的三个数当常数引用——
> 它是一个**有跨书方差**的效应，不是一个系数（判据 30）。

### 1.2 归一链本身（**代码做的后置条件，不落产物**）

顺序是 `归一 → 校验`，两遍的账**相加不覆盖**（只留后一份会让「首稿修了 9 处引号」凭空消失）。

⚠⚠ **这里曾有一条收缩臂**：`profile 精确绑定 → 结构化同槽收缩 → 再归一 → 确定性回归 →
双盲语义裁决 → 采用候选`。它由 `trim_equivalence` 的 evaluator profile 开闸，而那道闸从未在
任何宿主上打开过；随 profile 装置于 2026-08-18 整臂下线。**现在没有任何自动收缩路径**：
超上限章零调用落 `stage5/trim_audit_chNN.json`（四格：`output_ordinal` / `mode` /
`before_sha256` / `reason`）并进入 `verification`。

人工销账只允许 `action=retain_pretrim`：运行
`stage5.review --retain-pretrim --chapters N --actor <自报身份> --reason <理由>` 会写独立的
`trim_resolution_chNN.json`，绑定当前 versioned trim audit **整文件 SHA-256**、`mode`、
`stage5/<mode>/chNN.json` 路径及其确定性正文 SHA-256，并要求该正文仍精确等于 audit 的
`before_sha256`。actor 仅是自报身份，不是认证身份。任何 audit 字节、
正式章路径/正文或根哈希变化都会让旧 resolution 变 stale，verification 重新出现；再次决定时旧 resolution 进入
`stage5/_rounds/`，不得覆盖历史。该路径不改正文，且 schema 没有任何采用候选的 action——**自动的那条也没有了**，
所以 `retain_pretrim` 是超上限章唯一的销账口（另一条出路是 `--apply-edit` 改写整章，
那会产生新的一章、也就是新的 audit）。

⚠ **归一必须跑在收缩【之前】**，这一条是实测出来的：`normalize_paragraphs` 会把单换行改成空行分段，
**它往文本里加字符**，而 `budget_chars` 的口径含空白。曾有一章归一前 1408 字（未触发收缩，正确），
段落归一 +101 个换行 → 1509 字 → 被落盘闸门阻断——**它是被「修版式」这个动作推过线的**，
而唯一能救它的收缩已经跑完并正确地拒绝了。写手无从改，重跑也白搭。

⚠ **`--recheck` 会改写正文字节而不动来源账**（盘上 0 样本，机制确定）：
它逐章重跑 `normalize_chapter` 并把结果**写回产物**，刷新 `chars` / `overlap` / `warns`，
**但 `_provenance` 一个字不动**。两个后果：① 一份付费产物的正文字节被一次 0 成本操作改掉，
而来源账仍宣称是那次调用产出的；② `draft_text_sha1` 随之改变 ⇒ 下一份 review 会把它读成
「写手又落笔了」——**而 `draft_text_sha1` 的存在正是为了防这件事**。
⇒ 改写字节时必须出声，且**不许推进收敛计数**。

---

## 二、`stage5/review_<n>.json`

顶层 **8 个键**。**0 成本、不调 LLM**。它只做机械验收；改写式泄底由独立的
`stage5/semantic_<n>.json` 审判，不把付费结论塞进 0 成本产物。判**成品**，不判计划。
`<n>` 由盘上已有的最大序号 +1。

| 字段 | 类型 | 必填 | 内容 / 盘上 | 证据 |
|---|---|---|---|---|
| `sources` | `obj` | 是 | **先报来源再报判定**（`Q-4`）。见 §2.1 | **三源** |
| `judged` | `obj` | 是 | `{完整相对路径: {output_ordinal, round, sha1, mode}}` —— **判了哪几份稿** | **三源** |
| `chapters` | `obj[]` | 是 | 逐章。长度 = 计划章数（8~14），**没有成稿的章也在**（带一条 `draft_missing`） | **三源** |
| `book` | `obj[]` | 是 | 全书级结论。盘上长度 6~13 | **三源** |
| `baselines` | `obj` | 是 | `{rule: 'k/N'}`，**每条 hard/info 判据在多少章上命中**（判据 31）。含一条 `_beat_event_landing` | **盘·码** |
| `baselines_identity` | `obj` | 是 | 同一把尺子量**源章原文**的命中率。判据 20 的可执行形式 | **盘·码** |
| `counts` | `obj` | 是 | `{hard, info, unver, metric}` 计数。⚠ **盘上 12/12 份都没有 `hard` 这个键**（一条都没有） | **盘·码** |
| `_provenance` | `obj` | 是 | **没有 `model`**（0 成本）。`prompts` 只有一项：`RULES`（判据表自身的指纹） | **三源** |

⚠ **已裁撤 4 个**：`tag`（全仓 0 读者，盘上 12/12 恒 `''`）· `review_seq` · `title` · `source_id`
——**四个全在文件名或目录名里**，消费方（报告抬头 + 界面卡片）**都握着路径**，改现算
（`title_of(novel_dir)` / `derive_source_meta(novel_dir)`）。
⚠ `source_id` 那条**不改不行**：它取自 `ctx.get('source_id')`，而那个字段已随 `stage4.md` §二 裁撤
⇒ 不改就会**静默变 `null`**，报告抬头打印 `=== stage5·全书验收: 书名 (None) ===`（判据 23 的形状）。

> **`judged` 存完整相对路径是有来历的**：成稿一律不走 `read_json`——
> 那个入口的 `input_key` 只取两级，`stage5/deep/ch01.json` 会被记成 `deep/ch01.json`，
> `stale_inputs` 反查到一个**不存在的路径** ⇒ 指纹 `None` ⇒ 那条边**永远显示对得上**。
> **一个假的匹配比没有记录更坏。**

### 2.1 `sources`

| 字段 | 类型 | 必填 | 取值面 | 证据 |
|---|---|---|---|---|
| `writer_context.state` | `str` | 是 | `fresh｜stale｜unknown`（**三态**：没有 `inputs` 就是「不知道」，不是「匹配」）。盘上 12/12 `fresh` | **三源** |
| `writer_context.detail` | `obj[]` | 是 | `{key, was, now}`。盘上全空 | **盘·码** |
| `writer_context.code` | `str` | 是 | git 短 sha | **盘·码** |
| `drafts.stale` | `str[]` | 是 | 照的是另一份上游的成稿。盘上全空 | **盘·码** |
| `drafts.no_prov` | `str[]` | 是 | 没有来源账的成稿。盘上 3 份 review 里各 1 条 | **盘·码** |

⚠ `drafts` 那一半今天**只在非空时才报**，而 `Q-1` 要求**空也要报**。

### 2.2 `chapters[]`

| 字段 | 类型 | 必填 | 内容 / 盘上 | 证据 |
|---|---|---|---|---|
| `output_ordinal` · `from_source_ordinals` · `budget_chars` · `source_chars` | — | 是 | 取自 **ctx**（不取成稿） | **三源** |
| `mode` · `mode_dir` · `relpath` | `str` | **否** | 无成稿的章没有这三个（那一章只有一条 `draft_missing`） | **盘·码** |
| `draft_round` | `int` | **否** | 成稿的轮次。盘上 1~2 | **盘·码** |
| `draft_sha1` | `str` | **否** | **整份文件**指纹——管来源与过期 | **盘·码** |
| `draft_text_sha1` | `str` | **否** | **只管正文**——判「写手有没有再落笔」。⚠ 盘上只有 10/124（后加的） | **盘·码** |
| `chars` | `int` | **否** | 成稿字数 | **盘·码** |
| `findings` | `obj[]` | 是 | 见 §2.3。盘上长度 4~11 | **三源** |

> **两把指纹各管一件事，这是被一次误判逼出来的**：
> 来源账、`warns`、归一这类 **0 成本元数据修复**会改整份文件的指纹，
> 但写手**一个字都没再写**——拿整份指纹去推进收敛计数，等于把一次重判记成「写手又试了一次」。

### 2.3 `chapters[].findings[]` / `book[]`（**同一个形状**）

| 字段 | 类型 | 必填 | 取值面 | 证据 |
|---|---|---|---|---|
| `output_ordinal` | `int｜null` | 是 | `chapters[].findings[]` 必须等于所属输出章序；`book[]` 为 `null` | **契·码** |
| `rule` | `str` | 是 | 本层注册判据的 key（条数见 §七）。**key 撞名当场炸** | **三源** |
| `sev` | `str` | 是 | `hard｜info｜metric｜unver`。**`metric` 与 `info` 必须分开**（判据 21） | **三源** |
| `item` | `str` | 是 | 人读的一句话「这条在守什么」 | **三源** |
| `detail` | `str` | 是 | 判定正文。盘上 20~224 字 | **三源** |
| `evidence` | `str[]` | 是 | **机器可比的凭据**。盘上长度 0~6 | **三源** |
| `evidence_key` | `str` | 是 | `'\|'.join(evidence)`。**收敛键 = (章, rule, evidence_key)** | **三源** |
| `owner` | `str｜null` | 是 | `stage2/3/4/5｜corpus`；**`null` ⇔ 它不是结论（metric/unver）**，这条不变量在并表时抓出过一个 sev 写错的判据 | **三源** |
| `actionable` | `bool` | 是 | **[HARD] 蕴含 `owner == 'stage5'`**。⚠ 它**不等于「会进修复」**：还要过 `REPAIR_UNIQUE` 那道准入 | **三源** |
| `identity_hit` | `bool` | 是 | **这一章的源文**也触发了同一条判据 ⇒ 这条结论不携带信息，`actionable` 被降掉 | **盘·码** |
| `identity_dead` | `bool` | **否** | 书级恒等闸门：这条判据在这本书上**零判别力**。盘上 30/789 | **盘·码** |
| `converge` | `str` | 是 | `new｜unresolved_N｜same_draft`。**`draft_text_sha1` 没变时不许前进计数** | **三源** |
| `unresolved_rounds` | `int` | 是 | 连续未消的轮数；到 `STOP_REWRITE_AFTER`(2) 判「根因不在写手」 | **三源** |
| `wontfix` | `obj` | **否** | `{why, at}`。**盘上零样本**（没有一本有 `wontfix.json`） | **契·码** |

Review 产物保留逐章事实；对外 `CheckReport`、CLI 与 API 不能把同一本书的 `band_drift`
炸成 N 条同类待办。因此报告层将所有逐章 `band_drift` 合成**一条**书级 finding
（`output_ordinal=null`），detail 给命中章数/章号，evidence 保存可展开的逐章 JSON，包含原始
章序、detail/evidence、actionable、identity、converge、unresolved 与 wontfix；不会为了聚合丢审计状态。

当前、且与正式稿正文 hash 完全匹配的 `TrimAudit` 已是同一篇幅超限的权威待办。
这种情况下 Review 在**判据侧**移除独立 `length_over`，并由 `verification` 明写当前字数、目标区间、
before SHA-256 与保留 pretrim 的销账入口。audit 缺失/拒读、正文/路径不匹配、audit stale 时，
独立 `length_over` 仍照常保留；这不是靠 inbox 隐藏重复项。

⚠⚠ **判据侧的折叠不看销账状态。** `build_review` 走的是 `current_trim_audits`（**含**已销账），
而 inbox 的待核验清单走 `current_unresolved_audits`（**不含**）——**两个集合故意不是一个**：
一次 `retain_pretrim` 关掉的是待办，不是「这一稿确实超限」这个事实；销账后再产一条 `length_over`，
等于把人刚关掉的那个决定重新打开一遍。钉它的回归是
`tests/test_trim_resolution.py::test_matching_resolution_closes_inbox_but_any_audit_change_makes_it_stale`
（销账后 `current_trim_audits == {3}` 而 `current_unresolved_audits == {}`）。
⚠ 也没有「已采用」这一档：收缩候选与采用路径已整臂下线（见 §1.2）。

> #### 逐章豁免与书级闸门问的是**两个不同的问题**，都要
>
> - **逐章**（`identity_hit`）：这一条 finding 被**本章源文**的同一现象混淆了吗；
> - **书级**（`identity_dead`）：这条判据在这本书上，比它自己的**基础发生率**强吗。
>
> 只做逐章会漏掉这种情形：源文 3/8 章命中、成稿也 3/8 章命中，**但是不同的章**——
> 逐章看每条都「干净」，而整体上这条判据什么也没测出来。
> 盘上实测的恒等命中（三本合计，按章计）：
> `dialogue_ratio_drift` 10 · `band_drift` 7 · `simile_drift` 5 · `xinli_drift` 4 · `invented_number` 4。
> ⚠ **两道闸都只降 `actionable`，不删结论**：「查过、但这一章原文自己也这样」与「没查」必须分得开。
>
> ⚠ **`identity_hit` / `identity_dead` 是 0 代码读者，但不是孤儿**：它们是
> 「这条结论**为什么** `actionable=False`」的**唯一逐章解释**（`baselines_identity` 只到规则级）。
> 它们**已经随整份 findings 传给前端了**，只是没有一处在渲染 ⇒ 是「一份没人负责渲染的 blob」，
> 处置是**补渲染**，不是删。

---

## 三、书级语义审判 `stage5/semantic_<n>.json` —— 已下线

`stage5.semantic` 节点、`SemanticReview` 一族载体、`semantic_secret_leak` /
`semantic_secret_coverage` 两条判据与本产物**已整体删除**（用户裁决）。盘上不再产出这份文件，
主链从 18 个节点降为 17 个。

**裁决依据是实跑证据，不是偏好**：

* 本项目的重构**不跨章重组**——实测一本 18 章书 `from_source_ordinals` 全部是恒等映射，
  书级泄底的主风险面在计划形态上几乎不存在；
* 同一次实跑里它占单本**实付调用的 54%、输出 token 的 55%**（28 次调用 / 646 秒），
  而结论是 18 章里 5 章命题抽取 `coverage=uncertain`、8 条 secret 全部判不动；
* 同一本书上，**0 次模型调用**的「上游 info 下游投影」对账反而给出了确定结论
  （涉事角色新名首现第 12 章 > 原文首现 ch8，未提前指名）。

**改写式泄底现在由这两条承担**，都在 stage5 之外、都是 0 成本：

1. 标注层：stage2 的 `event_early_name` / `present_early_named` / `present_names_late`
   （见 `stage2.md`，需求编号 `R-3.7d`）——它们是确定性的，不存在"模型没把握"这一态；
2. 交付前：成书逐字对账的第 8 项「上游 info 的下游投影」，把上述 info finding
   逐条投影到成书章序核实（见 skill 的 `delegated-review.md`）。

⚠ **恢复条件**：计划开始出现跨章重组（`from_source_ordinals` 不再恒等）时，上面第 1 条的
覆盖面就变了，需要重新裁决是否恢复书级语义审判。恢复时要连同载体、判据、
`stage5.review` 里的语义结论合并与豁免那一步一起恢复。

## 四、其余四份产物

### 4.1 `stage5/wontfix.json` —— 人的「这条我不修」

`{'<章|book>|<rule>|<evidence_key>': {output_ordinal, rule, evidence_key, why, detail, at}}`
**键与收敛键同构**——只按 `rule` 记会把「换了个位置的同类问题」一并豁免掉，那不是人做的决定。

⚠ **机制是全链一份**（`novel/wontfix.py`），**账是逐层一份**：stage6 有一份同模型的
`stage6/wontfix.json`。持久身份是 `(层, 章|book, 判据, 证据键)`，而**层由文件路径承载**，
不进收敛键——每层各有自己的账，键里再写一遍层名就是同一个信息的第二个落点。

| 约束 | 说明 |
|---|---|
| **`why` 必填** | 写不出理由的豁免，与「没看过」在盘上没有区别 |
| **不给不存在的结论发豁免** | 该层**当前结论池**里没有那条 → **显式终态**（判据 28：缺省是「不知道」，不许猜） |
| **豁免不删结论** | 照常出现在报告与界面上，只是不进修复、不占「待人工」，并**单独计数** |
| **`at` 只进审计** | 人做这个决定时看的是哪一版结论（`review_4` / `semantic_2@review_4`）。它**不参与匹配** |

**结论池的书级语义结论位结构保留**（语义那一半随 §三下线后恒空）。语义结论**一律 book 级寻址**：章信息在证据键里（`ch<n>:<正文原字节>`）。

**失效由证据自身保证，不靠版本号**：证据是正文/候选的连续原字节，文本一改键就失配。
反过来，语义重跑但结论一字未变时豁免仍然有效——`stage6.semantic` 是付费节点、重跑是常态，
把审判版本塞进匹配键会让一次不改变任何结论的重审把整本账清空。

### 4.2 `stage5/<mode>/_rounds/` 与当前 `chNN.rejected.json`

| 文件 | 是什么 | 形状 |
|---|---|---|
| `chNN.r<N>.json` | 上一轮正式稿 | **与正式稿逐字段相同**（盘上 4 份，实测键集一致） |
| `chNN.INVALID.r<N>.json` | 上一轮被闸门拦下的问题稿 | 与对应 `.INVALID` 逐字段相同 |
| `chNN.rejected.json` | **当前被回归护栏驳回的自救尝试**；下一次成功或 INVALID 结果会将它归档 | `{output_ordinal, why[], fixes[[rule, evidence_key]…], attempts[{chars, text}]}`；`attempts` 恒为两份：首次候选与唯一一次绕缓存重采样。真实盘零样本，可提交 fixture 有一份 |

> **三个名字不能混**（判据 32）：`.INVALID` = 落盘闸门拦下的问题稿；
> `.rejected` = 护栏拒绝的一次自救；`.rN` = 正常的上一轮。**三者的下一步动作不同。**
> 修订候选在任何覆盖前先与旧正式稿跑内容保真回归；首次退化只允许一次
> `refresh-write` 重采样。第二份仍退化、为空或调用失败时，旧正式稿一个字不动，两个候选与原因
> 落进 `.rejected.json` 并进入 inbox。这个 per-call 缓存策略不修改共享 RunContext，避免并发章串扰。
>
> **归档目录只能放在 `stage5/<mode>/` 下面**，理由逐个核过：`_load_mode` / `recheck` 都是
> 不递归的 `glob('ch*.json')`；而 `Web` 把 **`stage5/` 的每个子目录**当成一条臂。
> ⚠ **盘上已经违反了这一条**：三岁那本有一个**人手工放进去**的
> `stage5/_rounds/review_4.…json`（全仓 grep 无任何代码会写它）。
> 今天的后果只是界面多列一条名叫 `_rounds` 的臂（它里面没有 `ch*.json`），
> 但那个论证的前提已经被现实破坏了：**这个目录会被人创建**。
> ⇒ 枚举臂时要跳过 `_` 开头的子目录——与 `derive_schema` 跳过 `_web` 同一个约定。

### 4.3 `stage5/<mode>/chNN.INVALID.json`

**与正式稿同一份章契约 + `errs`**（顶层 10 键 + `errs`，`_provenance` 含 `round`）。
⚠ **盘上仅有的两份是修好之前的贫血形**（只有 `{errs, mech, output_ordinal, text}`、
完全没有 `_provenance`）——别拿它们当取值面。**在场时盘上 0 份。**

⇒ stage4 的「`.INVALID` 裸写、提升路丢来源」在 stage5 这一侧**已经是修好的那个样子**，
移植时**照 stage5 抄**。⚠ 但**提升路还有另一半没修，而且方向相反**——见 §五。

---

## 五、`stage5/draft.md` —— 字节形态

`assemble_book` 把 **deep/ 与 shallow/ 两个目录**合成**一本** `stage5/draft.md`。
另有 `stage5/<mode>/draft.md` = **单 mode 预览，不是成书**（所以它**不对照计划算缺章**）。

```
# 重构稿（31/31 章：deep 11 · shallow 20）
[> ⚠ 缺章横幅]          ← 计划有、两条臂都没有的章；**写进 md，不静默跳过**

## 第 N 章（<mode> · 源 [n] · NNN字 · 重合(扣除逐字要求) 0.xxx）

<正文>
```

| 硬机制 | 处置 |
|---|---|
| 同一输出章在两个目录里都有成稿 | **显式终态**——哪一份是成品无从判断，不猜。<br>⚠ 而 review 与界面对同一情况**只报不判死**（判据 `draft_duplicated`）：把「呈现」变成「判死」会让人连报告都看不到 |
| 计划有、两边都没有 | **缺章横幅**写进 md |
| 章头行**不打篇幅方式** | 它恒为一个值，已随 `stage4.md` §一 裁撤；那句话归 system prompt |
| 两条臂并行收尾 | `run_selected` 等两臂线程池全部收口后只调用一次 `assemble_book`；单次复合任务内没有两个拼装器竞争 |

⚠ **拼装只验「两条臂没打架、合起来覆盖了计划」，验不了「这一章该不该 deep」**——
逐章深度是人的决策，存于 `_service/approvals/<sid>.json` 而不进小说产物；启动参数必须与
这份选择一致，写作一开始即锁定。默认选择第 1、2、3 章（计划不足三章时取交集）。

---

## 六、`_provenance`

四个跨产物共有的键（`prompts` / `model` / `code` / `inputs`）留给 `_common.md`。

### 6.1 `chNN.json._provenance` —— **全链唯一带轮次账的产物**

| 键 | 类型 | 必填 | 内容 / 盘上 | 证据 |
|---|---|---|---|---|
| `prompts.SYS` | `str` | 是（LLM 稿） | 盘上 3 个不同指纹 = deep · shallow · **deep+`REVISE_TAIL`**（修订轮的 SYS 是拼过的） | **盘·码** |
| `prompts.REVISE` | `str` | **否** | 修订轮才有。盘上 4/30 | **盘·码** |
| `model` | `str` | 是（LLM 稿） | 盘上 30/31 `deepseek-v4-pro`；**人工稿不记**（记了就是假账） | **三源** |
| `code` | `str` | 是 | git 短 sha。盘上 31/31 | **三源** |
| `inputs` | `obj` | 是 | 恒四条：stage1 正文 · stage2 global · stage3 style · **stage4 `writer_context`**；身份由来源边证明 | **三源** |
| `round` | `int` | 是 | 盘上 1~2。取**盘上见过的最大轮次 +1**，不按「刚归档的那一份」算；`.INVALID` 的轮次也算进去 | **三源** |
| `revision_of` | `str` | **否** | 上一轮**正文**的指纹。盘上 5/31 | **三源** |
| `review` | `str` | **否** | 触发这一轮的报告指纹。盘上 4/31 | **三源** |
| `fixes` | `[[rule, evidence_key]…]` | **否** | 这一轮针对性下发了哪几条。**只存引用不存渲染后的指令文本**（指令由 `RULES.directive` 生成，存第二份就会跟着表漂）。盘上 4/31 | **三源** |
| `origin` | `str` | **否** | `human` = `--apply-edit` 落的人工稿。盘上 1/31 | **三源** |
| `input_basis` | `str` | **否** | `apply_edit_current_validation` —— **「这份 inputs 是本次人工稿校验时读的，不是对旧问题稿历史来源的追认」**。它只属于新人工轮，不等于 `revalidated` | **盘·码** |
| `invalid_baseline_sha256` | 64 位 sha256 | **否** | 活动 `.INVALID` 首次写入时的不可变基线定位；提升后清除 | **契·码** |
| `revalidation_audit` | `RevalidationAudit` | **否** | `--revalidate` 成功后必有：actor/reason、raw/canonical 前后 SHA-256、RFC 6901 改动路径与变化布尔 | **契·码** |

⚠ **已裁撤**：`state`（原 `'invalid'`，只在 `.INVALID` 上）——0 读者，且**它恒等于文件名**。
一个叫 `chNN.INVALID.json` 的文件里再写一句「我是 invalid」就是第二落点（`Q-6`）。

> #### `--apply-edit` 那一份是**怎么记来源账的教科书**，移植时连注释一起搬
>
> 继承上一轮的 `inputs`（改的是同一份上游下的稿）· `pop` 掉 `prompts` 与 `model`
> （这一稿不是任何 prompt / 模型产的，记了就是假账）· 记 `origin=human` ·
> 上一轮 `inputs` 为空时**记下这次真实读过的那份并打上 `input_basis`**，
> 明说它不是对历史的追认（判据 28：不知道不能记成匹配）。

> #### `--revalidate` 是强审计提升，不是新一轮写作
>
> 命令必须给 `--actor` / `--reason`。`write_invalid` 保存不可变基线；提升前同时比较 raw 字节与
> 去掉 `_provenance` 后的 canonical 业务 JSON。原样重判、纯排版变化、人工业务修改分别由
> `raw_changed` / `content_changed` / `edited_by_human` 与 `changed_json_pointers` 区分。
> 成功后继承 `.INVALID` 自己的 prompts/model/inputs/usage/round，**round 不加一**；只更新代码身份、
> 清掉活动基线键并写 `revalidated=true + revalidation_audit`。因此它既不会把人工修改洗成纯模型输出，
> 也不会把一次复检伪造成新生成。
>
> `--apply-edit` 则消费完整 `segments[{slot,text}]` 人工稿、归档旧章并用
> `RoundFreeProvenance(origin=human)` 形成新的最大轮次。两者都是 0 次 LLM 调用，但只有后者是新轮。

### 6.2 `review_<n>.json._provenance`

| 键 | 类型 | 必填 | 内容 | 证据 |
|---|---|---|---|---|
| `prompts.RULES` | `str` | 是 | **判据表自身的指纹**（`repr(RULES)`）。盘上 12/12 同一个值 | **盘·码** |
| `code` | `str` | 是 | git 短 sha | **盘·码** |
| `inputs` | `obj` | 是 | 恒四条（同上）。⚠ **成稿不在里面**——它们记在 `judged`，因为 `read_json` 的两级键会造出一条假的匹配边 | **三源** |

**没有 `model`**——0 成本。`prompts.RULES` 是这份报告的**尺子版本**：
判据表改了而报告没重跑，这个指纹是唯一说得出来的地方（判据 28）。

---

## 七、判据清单（**唯一落点**，机械与语义共用一张注册表）

当前注册表共 **47 条**：**7 hard、25 info、10 metric、5 unver**。可反哺 23 条
（**13 条 `UNIQUE` + 10 条 `FREE`**，见本节末尾那条准入）。
owner 分布：`stage5` **28** · `None` **15**（＝非结论）· `stage4` 2 · `stage3`/`corpus` 各 1。
⚠ 上面这两行 2026-08-22 按 `REGISTRY.of_stage('stage5')` 现算改正过：此前写着
「可反哺 24 条（14 `UNIQUE`）· owner `stage5` 29」，与注册表各差 1。
⚠ 与原型那 43 条相比各多 2：新加的 `quote_unbalanced` / `deep_overlap_high` 归 `stage5`，
`dialogue_report` / `draft_duplicated` 是非结论。

落盘闸门 / `--recheck` / `--revalidate` / `--apply-edit` / 机械全书验收 / 语义审判**读同一张注册表**，
谁也不许再抄一份。但每条报告行用 `check_kind=mechanical|semantic` 明示检查性质，不拿严重度代替它。
「盘上」列 = 用当前代码在三本上重算（只读，不写盘），与盘上最近一份 review 的
`baselines` **逐条相同**（⇒ 那 12 份报告仍是现役读数，不是旧尺子）。

| 判据 | 级别 | 查什么 | 盘上 31 章 | 恒等基线 |
|---|---|---|---|---|
| `draft_too_short` | **HARD** | 成稿不是空的或残的（<50 字早退，其余判据不跑） | **0** | — |
| `draft_truncated` | **HARD** | 结尾是完整句（`_TERMINAL_PUNCT`） | **0** | — |
| `meta_leak` | **HARD** | 正文里没有章节标题/编号/任务术语 | **0** | — |
| `verbatim_missing` | **HARD** | 【原样写入】逐字出现（**允许被一处提示语切开**） | **0**（177 条全落地） | — |
| `keep_value_missing` | **HARD** | 【数值不得改动】都在（**比值不比字面**：`30岁` ↔「三十岁」= 满足） | **0**（61 条全在） | — |
| `secret_leak` | **HARD** | 没有提前点破本章不该揭示的事。检查面是 ctx 的 `must_not_reveal` **并集** Stage2 `reader_reveal_events` 推导的揭示前章，防止 ctx 漏投一个 id 就静默放行；改写式盲区仍交语义审判 | **0** ⚠ 而 19/21 条 secret 的探针连揭示章都命中不了 | — |
| `dialogue_halved` | **HARD** | 直接引语没被腰斩（**仅 `shallow` + 计划无净压缩 + 源章 ≥4 句**）。⚠⚠ 2026-08-22 起默认深度选择是**全书闭卷** ⇒ 默认路径上前件恒不成立、分母恒为零，这时落 `unver` 而**不是**绿灯（`checks.dialogue_halved_scope`）；单臂 `--mode shallow` 上它判别力完好，因此**没有**进 `_UNVER` | **0** | — |
| `length_over` / `length_under` | INFO | 篇幅上/下限 = **`DRAFT_BAND` 0.80~1.20×预算，无第二层宽限**。精确当前的 unresolved TrimAudit 存在时，`length_over` 在 Review 判据侧并入该 verification；stale/mismatch/adopted/resolved 时仍独立输出 | **10/31 章**（0.814~1.206 那批；该基线取自 ±10% 旧口径，放宽后这批全部落在带内） | — |
| `names_absent` | INFO | 本章人物名出现在正文里 | 0 | — |
| `verbatim_typo` / `verbatim_split` | INFO | 没被改字 / 被提示语切开也逐字出现 | 0 | — |
| `must_appear_missing` | INFO | 【必须在场】的物件留下痕迹 | 0（30 条全留痕） | — |
| `invented_number` | INFO | 没有编出下发面以外的「数字+单位」 | **3** | **4** ⚠ 基线高于命中 |
| `invented_name` | INFO | 没有编出下发面以外的人 | 0 | 0 |
| `secret_unknown` | INFO(stage4) | `must_not_reveal` 点到的都查得到 | 0 | — |
| `dialogue_fewer` / `dialogue_broken` / `dialogue_ratio_drift` | INFO | 引语数 / 引号体 / 对白占比 | 3 / 0 / **13** | — / — / **10** |
| `band_drift` / `para_len_drift` | INFO | 句段形态档位 / 段均字数。Review 保留逐章 `band_drift`，CheckReport/CLI/API 无损聚合为一条书级 finding | **18** / 0 | **7** / 0 |
| `layout_drift` | INFO | 提示语排版惯例 | 0 | — |
| `simile_drift` / `xinli_drift` / `dash_drift` | INFO | 明喻 /「心里」/ 破折号密度 | 7 / 7 / **11** | 5 / 4 / **0** |
| `quotes_in_unquoted_corpus` | INFO(corpus) | 引号用法与语料一致 | 0 | — |
| `event_landing` | INFO | 每一拍在成稿里有落点 | **0** | — |
| `cross_chapter_number` | INFO | 自造数字跨章不打架 | 0 | — |
| `book_length` | INFO(stage4) | 全书篇幅落在 **`BOOK_BAND` 0.90~1.10×源字数**（⚠ 与逐章 `DRAFT_BAND` 0.80~1.20 **不是同一个数**，见 §④） | **0**（实测 0.949 / 1.022 / 0.980） | — |
| `style_block_carries_plot` | INFO(stage3) | 风格块里没夹带情节 | 0 | — |
| `quote_unbalanced` | INFO | 引号配对异常、未归一——**留着报，不静默修**（原 `mech.quote_problems`） | **0/31** | — |
| `deep_overlap_high` | INFO | **闭卷臂**重合度 > `DEEP_OVERLAP_MAX`（10%）。**守的是臂的身份不是文风**；开卷不判 | **0/11 闭卷章**（今天 max 2.4%） | — |
| **〔度量〕** `length_report` · `overlap_report` · `dash_report` · `short_run_report` | metric | **逐章无条件报出** | 31 / 31 / 31 / 31 | — |
| **〔度量〕** `dialogue_report` | metric | **无条件**报出直接引语数与比值，**逐章 + 书级都报** | 31/31 + 每本 1 条书级 | — |
| **〔度量〕** `secret_timeline` | metric | 每条 secret 的关键词数、权威揭示章、全书命中章与「揭示章未命中」盲区；与逐章字面闸共用同一数字归一尺 | **21 条 secret** | — |
| **〔度量〕** `book_length_report` · `convergence_report` | metric | 全书篇幅总账 · 与上一轮相比消掉了什么 | 3 / 3（每本各 1） | — |
| **〔度量〕** `name_surface_survey` | metric | 全书自造称谓与撞名 | **0** ⚠ 见下 | — |
| **〔度量〕** `arm_split_report` | metric | 两条写作臂各写了几章 + 开卷章的**逐章** 重合度 min~max。**只报不判**（不为开卷发明阈值，`B-03`） | 每本 1 条（新加，2026-08-22；旧盘面 deep 11 · shallow 20） | — |
| `must_hold_unver` | unver | 【必须成立】那一组 | 3（每本 1）· **全书 27 条恒不检查** | — |
| `event_landing_unver` | unver | 提不出可回查原子的拍 | **7** | — |
| `draft_missing` | unver | 计划里每一章都有成稿 | 0 | — |
| `draft_duplicated` | unver | **同一输出章在两条臂里各有一份成稿** | 0/31 | — |
| `no_secrets_in_book` | unver | 这本书有可查的秘密 | 0（三本都有 secrets） | — |

> #### ⚠ `secret_leak` 报 0，而它的基线说这盏灯基本不会亮
>
> `secret_timeline` 这条 metric 的存在理由就是给它当基线（判据 31）。实测：
>
> ```
> 成年人  4 条 secret，3 条「连它自己的揭示章都命中不了」
> 头牌    7 条          7 条          ← 全军覆没
> 三岁   10 条          9 条
> ```
>
> ⇒ **21 条里 19 条**的字面探针在本书上**没有判别力**。
> 「`secret_leak` 0 命中」这句话现在的信息量接近 0——而它是 7 条 HARD 之一。
> 字面机械层仍然抓不住改写式泄底。`stage5.semantic` 已下线（见 §三），
> 守卫转授给 stage2 的确定性判据与成书逐字对账，仍不试图扩充这盏低判别力绿灯。
> 机械层仍有两条配套：报绿灯时**同一行**打出盲区计数；**一本书盲区 100% 时落 `unver` 而不是沉默**
> （头牌就是这种情况，7/7 条）。**不降级 sev**——它命中时是真泄底，降级会让唯一一道防线更弱。
>
> #### ⚠ `name_surface_survey` 是一条**有条件的度量**，而它不该是
>
> ```python
> if by_surface or collide:           # ← 只有「有东西」的时候才产出
>     fs.append(finding('name_surface_survey', …))
> ```
>
> 它的 `sev` 是 `metric`、`owner` 是 `None`（＝声明自己不是结论），而它**只在命中时才出现**
> ⇒ 盘上 12 份 review **0 次**。于是「全书 0 个自造称谓、0 组撞名」这个**读数**永远说不出来，
> 而「查过、干净」与「根本没在查」在报告上长得一模一样。
> 对照组就在同一个函数里：`secret_timeline` 是**逐条无条件**产出的。⇒ **要改成无条件。**
>
> #### `dash_drift` 是本层**唯一分母被测过程改不动**的判定量
>
> 它的判定线含一个**语料级常量** `DASH_CORPUS_P95`（由 `calibrate_style_corpus.py` 标定、可 0 成本重跑）。
> 恒等基线 **0/31**、成稿命中 **11/31** ⇒ **判据干净、有判别力**（原作几乎不用破折号，成稿在用）。
> ⚠ 而 `_simile_drift` 的 `+1.0` **没有这个来源**，应按同样办法重标（⏳ 未做）。
>
> #### `deep_overlap_high` 的 10% 是**拍的**，它守的不是文风
>
> | 它**不**在守 | 它在守 |
> |---|---|
> | 「闭卷写得像不像原文」——那是文风，没有阈值可言 | **臂跑错了**：`--chapters` 分工写反、`with_source_text` 被翻转、开卷稿落进 `deep/` 目录 |
>
> 按后者选，阈值就有硬约束：**要高过闭卷的自然波动（逐章 max 2.4%），
> 要远低于开卷的分布（逐章中位 49.8%、书级均值最低 36%）。** 10% 在两条之间，各有约 4 倍余量。
> ⚠ **它仍然只有 11 章的标定**（判据 18：调参之前先量方差）。deep 章数上到 30+ 之后要重看一次分布——
> 这句话写进了 `invariants.py` 的常量注释，免得下一个人把它当成量出来的。
> ⚠ **sev 是 INFO 不是 HARD**：越线的唯一真修法是**重写一整章**。付费重采样会产生
> LLM 费用；`--apply-edit` 是 0 LLM 费用，但不是“改一行”：人必须提供该章所有
> producer-owned slot 的完整 `segments[{slot,text}]` 整章全文。
>
> #### 重合度必须按**逐章**口径读，不是书级均值
>
> | | 书级均值 | **逐章**（判定量真正的粒度） |
> |---|---|---|
> | deep | 0.8% · 0.1% · 0.5% | 0.0% ~ **2.4%**，中位 0.1% |
> | shallow | 41.3% · 78.0% · 36.4% | **0.9%** ~ **96.8%**，中位 49.8% |
>
> ⚠ 两件事只有换成逐章口径才看得见：① **闭卷的逐章上界是 2.4%，不是 0.3%**——书级均值把它平掉了；
> ② **开卷有低到 0.9% 的章**，「开卷 = 高重合」不成立，它是**上界**问题不是**分布**问题。
> ⇒ `B-03`（开卷不定线）若哪天真要定，**必须先说清定在哪个口径上**（判据 14）。
>
> #### `actionable=False` 不等于「不管」——`REPAIR_UNIQUE` / `REPAIR_FREE` 是**第二道**准入
>
> **付费自救只开给「满足路径唯一」的判据**：目标字串被上游钉死，或动作就是「删掉指出来的那个」。
> 密度 / 比率 / 档位类一律进 `REPAIR_FREE`——满足它们的路径有无穷多条，
> 而**最省力的那条是删内容或编内容**。实测代价：可修复条目 **36 → 9（转人工 27 条，75%）**。
> 每条 `actionable` 的判据必须**进且只进其一并写明理由**，分不干净时当场炸。

### 7.1 判据 ↔ 需求 —— **逐条有落点**

`contracts/rules.py` 要求每条判据声明 `requirement`（正则 `^R-\d+\.\d+[a-z]?$`——
**`Q-x` 与裸 `R-3` 都不收**），`tests/test_coverage.py` 双向查：反向让指向不存在编号的判据变红，
正向让「声称有守卫却没有判据指着」的需求行变红。

**审计前的账：20 条判据在需求文档里没有落点**，另有 4 条指着收不了它们的地方
（`secret_timeline` / `no_secrets_in_book` / `event_landing_unver` 指 `Q-9`、
`draft_missing` / `draft_duplicated` 指 `Q-1`、
`name_surface_survey` 指 `R-7.5` 而那一行当时写着「无守卫 → `B-01`」）。
⇒ `docs/BACKEND_CONTRACT.md` 补 17 行、拆 1 行（`R-7.5`），R-8 一节九行**逐行重判**。

| 判据（族） | 落点 |
|---|---|
| `verbatim_missing` · `verbatim_typo` | `R-7.3` |
| `verbatim_split` | `R-7.3b` |
| `keep_value_missing` | `R-7.4` |
| `must_appear_missing` | `R-7.17` ⭐ |
| `must_hold_unver` | `R-7.11`（那一格是 `人`，判据侧 `who_checks='HUMAN'`） |
| `invented_number` · `invented_name` | `R-7.5` ⭐**拆行**（另五项留在 `R-7.5b`「无守卫 → `B-01`」） |
| `deep_overlap_high` | `R-7.6`（闭卷有上界）· `R-7.6b`（开卷仍无守卫 → `B-03`） |
| `quotes_in_unquoted_corpus` · `quote_unbalanced` | `R-7.7`（前者 `owner=corpus`；后者让这条需求第一次名副其实） |
| `length_over` · `length_under` · `book_length` | `R-7.8`（书级那条是它与 `R-5.3b` 那条带的延长线） |
| `dialogue_report` | `R-7.8b` |
| `draft_too_short` · `draft_truncated` · `meta_leak` | `R-7.15` ⭐ |
| `names_absent` | `R-7.16` ⭐ |
| `secret_leak` · `no_secrets_in_book` | `R-7.18` ⭐（`R-8.10` 只写了**改写式**那一半） |
| `dialogue_halved` | `R-7.19` ⭐ |
| `arm_split_report` | `R-7.6c` ⭐（**默认全书闭卷之后，走开卷是例外**） |
| `dialogue_fewer` · `dialogue_broken` · `dialogue_ratio_drift` | `R-7.20` ⭐ |
| `band_drift` · `para_len_drift` · `layout_drift` · `simile_drift` · `xinli_drift` · `dash_drift` | `R-7.21` ⭐ |
| `dash_report` · `short_run_report` | `R-7.21b` ⭐ |
| `event_landing` · `event_landing_unver` | `R-7.22` ⭐ |
| `cross_chapter_number` | `R-7.23` ⭐ |
| `length_report` · `overlap_report` · `book_length_report` | `R-8.1b` ⭐ |
| `convergence_report` | `R-8.3b` ⭐ |
| `secret_timeline` · `name_surface_survey` | `R-8.9b` ⭐（**探针自己的基线读数**） |
| `draft_missing` · `draft_duplicated` | `R-8.11` ⭐ |
| `style_block_carries_plot` | `R-4.5`（描述不许出现专名） |
| `secret_unknown` | `R-6.3`（逐章剧透闸门产的 `must_not_reveal`） |

> ⚠ **那 20 条不是「判据多余」，是需求文档漏写了。** 判准很直接：它们**每一条都在盘上跑着**，
> 其中 `dash_drift`（11/31 命中、恒等基线 0/31）与 `band_drift`（18/31）是本层**命中最多**的两条。
> 逐字核过：`docs/BACKEND_CONTRACT.md` 全文 **`破折号` 0 次 · `心里` 0 次 · `外壳` 0 次 ·
> `段落平均` 0 次**。
>
> ⚠⚠ **最刺眼的那条：`dialogue_halved` 是 7 条 HARD 之一**——
> 「闭卷会系统性把台词压短」是本项目**效应量最大**的一条跨书结论，它已补记为 `R-7.19`。

#### R-8 一节九行**没有一行是判据在守**

`R-8.1`~`R-8.9` 逐行查下来，说的都是**机制**不是对产物的判定：

| 行 | 改判 | 落点 |
|---|---|---|
| `R-8.1` | `结构` | `contracts/rules.py::_validate` ②：`sev` 与 `who_checks` 两侧必须成对 |
| `R-8.2` | `载体` | `Finding` 的两条后置条件（`owner is None` ⇔ 非结论 · `actionable ⇒ owner=='stage5'`） |
| `R-8.3` | `载体` | `Finding.evidence_key` 由 `evidence` 现算并断言 |
| `R-8.6` | `结构` | `contracts/rules.py::_validate` ③④：准入收成**一个字段的三个取值**之后，「进且只进一张桶」结构上不可能分不干净（原型那两张 dict 要另配一道断言） |
| `R-8.4` | `结构` | `novel/stages/stage5/review.py::_converge`（只在**正文**指纹变了时才推进计数） |
| `R-8.5` | `结构` | `novel/stages/stage5/write.py::revise_targets` 的第四道筛子（`STOP_REWRITE_AFTER`） |
| `R-8.7b` | `结构` | `novel/wontfix.py::mark` + `::applied`（全链一份机制；查不到 ⇒ 显式终态；豁免只降 `actionable`。结论池由 `novel/stages/stage5/review.py::mark_wontfix` 交出，含语义结论） |
| `R-8.7` | `载体` | `WontFix` 的 `why` 最小长度 + 键↔条目一致性 |
| `R-8.8` | `结构` | `novel/views/inbox.py::inbox_view` 只读盘聚合八类；`repairable` 与真人工 `human_only` 分开，可人工消解的 `UNVER` 进 `verification` |
| `R-8.9` | `结构` | `contracts/rules.py::_validate` ⑤：`wordlist` 不填 `baseline` 当场拒绝注册 |

⇒ 度量与「查不了」那两族因此各自有了自己的行（`R-8.1b` / `R-8.3b` / `R-8.9b` / `R-8.11`），
**不再挂在一行写着「阻断」的需求下面**。

> #### ⚠ `draft_missing` 与 `draft_duplicated` 是两条，不许共用一个 key
>
> 「没有成稿」与「有两份成稿」共用一个 key 会把两种现象加进 `baselines` 的同一个
> `draft_missing: k/N`，读报告的人**按名字去解释那个数**只会解释错
> （收敛键因 `evidence` 不同还分得开，**基线分不开**）。
> ⚠ 三处行为**故意不同**：`assemble_book` 对双份**显式终态**，而 review 与界面**只报不判死**。

当前只保留 `convergence_report` 收敛账；报告里没有 `upstream_ref` 占位。
上游各层的结论**已不在本产物里**（看的时候现算，不冻进一份会过期的报告）。

---

## 八、几条不进判据表的硬机制

| 机制 | 触发条件 | 处置 |
|---|---|---|
| `ChapterResponse` 载体门 | 任一请求缺槽、重槽、多槽、未知槽，或 `finish_reason=length` | 响应校验失败；不按模型数组顺序拼接，不产生正常/INVALID 章产物 |
| `_write_group` 收尾 | 某章没拿到正文 | **显式终态**（多章一批时提示长列表 wind-down） |
| `_over_length_audit` | 超上限 | **0 次调用**，正文原样保留，落 durable identity `trim_audit` 并转 `verification`。⚠ 该函数签名里没有 `client`——本层不再有第二条付费回路，它在结构上就花不了钱 |
| `_revise_regression` | 修订轮把上一轮弄坏 | 整轮弃用 + **一次 no-cache 重采样**（同 prompt 走缓存只会拿回同一份坏稿）+ 仍坏则**保留上一轮**，被驳回的稿留 `.rejected.json` |
| 落盘闸门 | 有 `errs` | **不覆盖已有成稿**，问题稿存 `.INVALID`，**一章失败不中断整批** |
| `assemble_book` | 同一章两条臂都有成稿 | **显式终态** |
| `--apply-edit` | 提交带全部计划 slot 的完整 `segments[{slot,text}]` 单章人工稿；命令为 0 次 LLM 调用 | 归一、判据、归档后形成新的人工 round；仍有 `errs` 时**只报不拦**，判定落进 `errs`/`warns`，界面照常红着 |
| `--revise` 无可自救项 | 全部转人工或连续未消 | **显式终态**并明说「这不是『没问题』」 |

> ⚠ **`--apply-edit` 只报不拦这一条，下游没有第二道**：`stage7` 只排除 `.INVALID.json`，
> **不看正式稿里的 `errs`** ⇒ 一份带阻断级判定的人工稿会一路进成书，没有一行报告会说。
> 盘上零样本（唯一那份人工稿的 `errs` 是空的）。这是裁决的后果，不是 bug——但要写进契约。

> 所有写作与修订调用共用 `ChapterResponse` 及同一槽位 validator；不存在另一路纯文本或
> batch-only 响应形状。旧 `text` 权威字段没有别名或双读分支，必须从 producer 重新生成
> 当前 `segments`。

---

## 九、零样本分支

盘上一次都没走过的分支，**别把它们读成「安全」**：

| 分支 | 为什么没有 |
|---|---|
| `.INVALID` 的**现行形状**（10 键 + `errs` + `_provenance`） | 盘上两份都是修好之前的贫血形 |
| `chNN.rejected.json` | 自救从没被回归护栏驳回过 |
| `wontfix.json` | 没有人用过 `--wontfix` |
| `overlap_excl_verbatim = null` | 需要正文短于 20 字，而那时 `draft_too_short` 已经早退——**可能构造不出来** |
| 归一的任何一项非 0 | 三本 31 章一次归一都没触发 ⇒ `--recheck` 改写字节那条潜伏故障（§1.2）**没有任何守卫** |
| 非空 `errs` 的正式稿 | 唯一那份人工稿的 `errs` 是空的 |
| `sources.writer_context.state ∈ {stale, unknown}` | 三本 ctx 都 fresh |

⚠ **7 条 HARD 一个会让它红的样本都没有。** 「见过它通过」不是证据，「见过它红一次」才是。

---

## 十、已经查清楚、结论就是「查不了」的

登记在这里免得被当成漏项。

1. **改写式泄底在本层没有守卫。** 字面探针 `secret_leak` 只挡转抄式，
   而它的基线说这盏灯基本不会亮（19/21 条 secret 连自己的揭示章都命中不了）。
   书级语义审判已随 `stage5.semantic` 下线（见 §三），守卫转授给 stage2 的
   确定性判据与成书逐字对账的「上游 info 下游投影」。
   ⚠ **那两条守的是"提前指名"，不是"语义等价地点破"**——后者在本层现在确实没人查。
2. **`must_hold` 恒 `unver`。** 全书 27 条，按定义查不了文面。
3. **「这一章该不该 deep」没有自动判据。** 选择虽持久化于 `_service/approvals/` 并在启动时
   校验/锁定，但 `assemble_book` 仍只能验「两臂没打架 + 覆盖了计划」，验不了选择本身好不好。
4. **`BEAT_MIN_CHARS` / `VERBATIM_BUDGET_FACTOR` / `_simile_drift` 的 `+1.0` 都没有独立校准**
   （只有 `DASH_CORPUS_P95` 有语料级来源）。
5. **`length_over` / `length_under` 不进任何回环**（`REPAIR_FREE`），`flat_chapter` 那一族在 stage4 也是——
   量出来了然后没有下文（判据 35 的又一处）。
6. **发明越界七项里五项没有判据**（`B-01`），**不补，直到有 ground truth**。
   七项的身份是**给人核对的料**（同 `name_surface_survey`），不是关卡；
   报告里必须一直写着**「探针报 0 条 ≠ 约束生效」**。
   ⚠ 唯一 `full` 的那一项自己也零判别力：`invented_number` 在 2/3 本上被书级恒等闸门判死。
