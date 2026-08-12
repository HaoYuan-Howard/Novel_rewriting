# stage5 产物 schema

写作 + 全书验收。**这一层是全链唯一产出「书」的地方**，也是全链**唯一带 LLM 返工回路**的层。

```
stage5/<mode>/chNN.json          LLM 产，逐章一次调用 —— 唯一承载正文的产物（deep / shallow 两条臂）
stage5/<mode>/chNN.INVALID.json  被落盘闸门拦下的问题稿（同一份章契约，只多一个 errs）
stage5/<mode>/_rounds/…          三种归档：上一轮 · 被提升掉的问题稿 · 被回归护栏驳回的自救尝试
stage5/review_<n>.json           确定性验收报告（**0 成本，不调 LLM**），整本写完之后跑
stage5/wontfix.json              人的「这条我不修」（0 成本，理由必填）
stage5/draft.md                  两条臂合成的全书拼稿（**字节形态**，见 §四）
```

对应需求 **R-7**（写作）与 **R-8**（验收与返工）。
读法（证据列的取值、为什么三源合并）见 `_README.md`。
**载体 = Pydantic v2**（已定），但本文只定内容、不写模型——模型在骨架阶段照本文写。

> **样本面：盘上 3 本 = 31 输出章（deep 11 / shallow 20）· 12 份 review（每本 4 轮）·
> 6 份 `_rounds` 归档 · 0 份在场的 `.INVALID` · 0 份 `.rejected.json` · 0 份 `wontfix.json`。**
> 与 stage2~4 一样，这一层没有「全语料 163 本」可退——**跑一章就是一次高推理调用**。

---

> ## ⚠⚠ 五条贯穿本层的事实
>
> ### ① 这一层是**两条臂 + 两相**，而 schema 只有一份
>
> | | 谁 | 差别 |
> |---|---|---|
> | **臂** | `n5_write`（deep，闭卷）· `n6_write`（shallow，开卷） | **只差 (system prompt, 给不给源章原文)**，产物形状逐字节相同；`mode` 同时是目录名 |
> | **相** | 第一相＝逐章写作 + 落盘闸门 · 第二相＝`stage5_review` 全书验收 | 判据表**只有一张**（`RULES`，47 条）；第二相的增量只有 §六 那 8 条「单章视角看不到的」 |
>
> ⚠ **臂不是 tag。** `--tag _v2` 给**目录**加后缀做 A/B，而「这是 deep 还是 shallow」是**臂**的属性。
> 两者曾被拼成同一个串传下去，于是 `dialogue_halved`（判 `mode == 'shallow'`）
> 在每一条 tag 臂上**恒假**——闸门没有报错，它是**静默地不再存在**。
>
> ### ② 判据 vs 载体的分界线在本层**几乎全部倒向判据**——因为拒收的代价是一整章
>
> 照 `stage4.md` §四立的那条规矩（**按失败成本划，不是按「类型表达得了吗」划**）：
>
> | | 本层的代价 |
> |---|---|
> | **载体拒收** | 整章响应作废 ⇒ 重试 ⇒ **再花一次高推理调用**（本层最贵的一次重试） |
> | **判据拦下** | 落 `.INVALID` ⇒ `--apply-edit` 人手改 / `--revalidate` 重判 ⇒ **0 成本** |
>
> ⇒ 47 条判据里**一条都不该上移**：它们判的全是自由文本的内容属性，类型系统够不着；
> 而少数够得着的（`mode` / `from_source_ordinals` 长度恒 1）**本来就没有判据**，
> 归载体是原样承认现状，不是收权。
>
> ⚠ **还有一处不对称必须先说**：stage4 那条「模型越界多产一个键 ⇒ 响应侧 `extra='forbid'` 当场挡回」
> 的出路，**在本层的默认路径上不存在**——默认逐章调用走的是 `ask_text`，**响应是纯文本，没有 JSON 可校验**。
> 只有 `--batch N`（A/B 用）走 `ask_json`。别把 stage4 的那套结论直接搬过来。
>
> ### ③ **7 条 HARD，盘上 31 章 0 命中；47 条判据里 29 条从未命中**
>
> （原型那 43 条里 26 条，加上新契约新加的三条：`quote_unbalanced` 0/31 ·
> `deep_overlap_high` 0/11 闭卷章 · `draft_duplicated` 0/31。第四条新加的
> `dialogue_report` 是度量，逐章 31/31 都产。）
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
> ### ④ 篇幅带**只有一个数**：`0.90~1.10×`，逐章与全书同一个带
>
> 逐章 `DRAFT_BAND` / 全书 `BOOK_BAND` 都是 **0.90~1.10**，**没有第二层宽限**
> （与 `stage4.md` §四同源：「写下来的带」与「执行的带」不许是两个数）。
>
> **实测代价是可量的**：31 章里 **10 章（32%）落在 ±10% 之外**，两端极值 **0.814 / 1.206**
> ——旧口径下它们恰好卡在 0.81 / 1.21 的内侧、各差不到 1%，`length_over` / `length_under` 一次都没响。
> 收窄后那 10 章报**提示级**（不阻断落盘）；全书 3/3 本仍绿（0.949 / 1.022 / 0.980）。
> ⚠ 但**旧口径也不是静默**：`length_report`（metric）无条件报出，并带着 `← 超出目标 1084~1325` 标记，
> 10/31 章的 `warns` 里都有。**判据 21 在这里是正确工作的**——出声的是度量，不是告警。
>
> ### ⑤ 落盘前有一次**确定性改写正文本身**，而本层**不为它记账**
>
> `normalize_chapter`（标点归一 / 引号体归一 / 段落分隔归一）跑在**落盘之前**，
> 存进产物的 `text` 是**归一之后的字节**，模型原样吐出来的那一份**不在任何产物里**。
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

顶层 **10 个键**。**正式稿与 `.INVALID` 共用同一份章契约**——状态由**文件名**区分，
不由字段贫富区分。

> #### ⚠ 前五个字段是 `writer_context` 的**副本**，看起来违反「能 0 成本现算的不落盘」
>
> 它们**不是抄件，是冻结下来的任务书**。判准来自本层自己付过的学费：
> `writer_context` 可以被 `--context-only` **0 成本原地重建并覆盖**，而这一份成稿是**付过钱**的。
> 实测发生过一次：23 章成稿与盘上任何一版 `writer_context` 都配不上（匹配 1/23），
> 配对关系**永久丢失**——那时留在成稿里的这几个数是唯一还说得出「写手当时被要求写多少字」的东西。
> ⇒ 与「能 0 成本现算的不落盘」（`_README.md`）裁掉的那些**不同类**：
> 那些的源头（目录名、原文）不会被覆盖，这个会。

| 字段 | 类型 | 必填 | 取值面 / 范围 | 证据 |
|---|---|---|---|---|
| `output_ordinal` | `int` | 是 | 1..N。**文件名里的 NN 就是它**（旧 `.INVALID` 缺它时界面从文件名兜底解析） | **三源** |
| `from_source_ordinals` | `int[]` | 是 | **长度恒为 1**（一章对一章）。透传自 ctx | **三源** |
| `mode` | `str` | 是 | `deep｜shallow`（`DEPTH_MODES`）。**同时是目录名**。⚠ 旧成稿缺它时 `_load_mode` 按目录补 | **三源** |
| `source_chars` | `int` | 是 | 源章对应部分字数（计划算好的）。盘上 408~1963 | **三源** |
| `budget_chars` | `int｜null` | 是（键恒在） | 本章各拍预算之和。盘上 380~1960。<br>**兼任契约版次的结构标记**：旧契约成稿没有这一项，`_write_draft` 据此认出混契约的拼稿 | **三源** |
| `chars` | `int` | 是 | `len(text.strip())`，**含空白**。盘上 399~1967。<br>⚠ 与 stage3 `measurable.body.chars`（**去空白**）不是同一个口径——跨口径运算要先对齐（判据 14） | **三源** |
| `overlap_excl_verbatim` | `float｜null` | 是 | 20 字窗口重合率，**已扣掉按契约必须逐字保留的部分**。`null` = 正文短于一个窗口，**那是「量不了」不是「重合 0」**。<br>**闭卷有上界 `DEEP_OVERLAP_MAX` = 10%**（判据 `deep_overlap_high`）；**开卷无上界，只报分布** | **三源** |
| `text` | `str` | 是 | 正文本身，**唯一承载内容的字段**，`draft.md` 由它拼。⚠ 存的是**归一之后**的字节（事实 ⑤） | **三源** |
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
> ⚠ 非空 `errs` 的正式稿**盘上 0 样本**，而 §七 已说明它会一路进 `book.txt`。

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

顺序是 `归一 → 收缩(LLM) → 再归一 → 校验`，三道门与落盘看的是同一份最终字节；
两遍的账**相加不覆盖**（只留后一份会让「首稿修了 9 处引号」凭空消失）。

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

顶层 **8 个键**。**0 成本、不调 LLM**（`--semantic` 尚未接线）。判**成品**，不判计划。
`<n>` 由盘上已有的最大序号 +1；`--tag` 落成 `review<tag>_<n>.json`。

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
| `rule` | `str` | 是 | 47 条判据的 key（`RULES`）。**key 撞名当场炸** | **三源** |
| `sev` | `str` | 是 | `hard｜info｜metric｜unver`。**`metric` 与 `info` 必须分开**（判据 21） | **三源** |
| `item` | `str` | 是 | 人读的一句话「这条在守什么」 | **三源** |
| `detail` | `str` | 是 | 判定正文。盘上 20~224 字 | **三源** |
| `evidence` | `str[]` | 是 | **机器可比的凭据**。盘上长度 0~6 | **三源** |
| `evidence_key` | `str` | 是 | `'\|'.join(evidence)`。**收敛键 = (章, rule, evidence_key)** | **三源** |
| `owner` | `str｜null` | 是 | `stage2/3/4/5｜corpus`；**`null` ⇔ 它不是结论（metric/unver）**，这条不变量在并表时抓出过一个 sev 写错的判据 | **三源** |
| `actionable` | `bool` | 是 | **[HARD] 蕴含 `owner == 'stage5'`**。⚠ 它**不等于「会进反哺」**：还要过 `REPAIR_UNIQUE` 那道准入 | **三源** |
| `identity_hit` | `bool` | 是 | **这一章的源文**也触发了同一条判据 ⇒ 这条结论不携带信息，`actionable` 被降掉 | **盘·码** |
| `identity_dead` | `bool` | **否** | 书级恒等闸门：这条判据在这本书上**零判别力**。盘上 30/789 | **盘·码** |
| `converge` | `str` | 是 | `new｜unresolved_N｜same_draft`。**`draft_text_sha1` 没变时不许前进计数** | **三源** |
| `unresolved_rounds` | `int` | 是 | 连续未消的轮数；到 `STOP_REWRITE_AFTER`(2) 判「根因不在写手」 | **三源** |
| `wontfix` | `obj` | **否** | `{why, at_review}`。**盘上零样本**（没有一本有 `wontfix.json`） | **契·码** |

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

## 三、其余四份产物

### 3.1 `stage5/wontfix.json` —— 人的「这条我不修」

`{'<章|book>|<rule>|<evidence_key>': {output_ordinal, rule, evidence_key, why, detail, review_seq}}`
**键与收敛键同构**——只按 `rule` 记会把「换了个位置的同类问题」一并豁免掉，那不是人做的决定。

| 约束 | 说明 |
|---|---|
| **`why` 必填** | 写不出理由的豁免，与「没看过」在盘上没有区别 |
| **不给不存在的结论发豁免** | 最近一份 review 里没有那条 → **显式终态**（判据 28：缺省是「不知道」，不许猜） |
| **豁免不删结论** | 照常出现在报告与界面上，只是不进反哺、不占「待人工」，并**单独计数** |

### 3.2 `stage5/<mode>/_rounds/` —— **三种归档，形状各不相同**

| 文件 | 是什么 | 形状 |
|---|---|---|
| `chNN.r<N>.json` | 上一轮正式稿 | **与正式稿逐字段相同**（盘上 4 份，实测键集一致） |
| `chNN.INVALID.superseded.json` | 已被正式稿取代的问题稿 | 同 `.INVALID`（盘上 2 份，都是修好之前的 4 键贫血形） |
| `chNN.rejected.json` | **被回归护栏驳回的自救尝试** | `{output_ordinal, why[], fixes[[rule, evidence_key]…], attempts[{chars, text}]}`。**盘上零样本** |

> **三个名字不能混**（判据 32）：`.INVALID` = 落盘闸门拦下的问题稿；
> `.rejected` = 护栏拒绝的一次自救；`.rN` = 正常的上一轮。**三者的下一步动作不同。**
>
> **归档目录只能放在 `stage5/<mode>/` 下面**，理由逐个核过：`_load_mode` / `recheck` 都是
> 不递归的 `glob('ch*.json')`；而 `Web` 把 **`stage5/` 的每个子目录**当成一条臂。
> ⚠ **盘上已经违反了这一条**：三岁那本有一个**人手工放进去**的
> `stage5/_rounds/review_4.…json`（全仓 grep 无任何代码会写它）。
> 今天的后果只是界面多列一条名叫 `_rounds` 的臂（它里面没有 `ch*.json`），
> 但那个论证的前提已经被现实破坏了：**这个目录会被人创建**。
> ⇒ 枚举臂时要跳过 `_` 开头的子目录——与 `derive_schema` 跳过 `_web` 同一个约定。

### 3.3 `stage5/<mode>/chNN.INVALID.json`

**与正式稿同一份章契约 + `errs`**（顶层 10 键 + `errs`，`_provenance` 含 `round`）。
⚠ **盘上仅有的两份是修好之前的贫血形**（只有 `{errs, mech, output_ordinal, text}`、
完全没有 `_provenance`）——别拿它们当取值面。**在场时盘上 0 份。**

⇒ stage4 的「`.INVALID` 裸写、提升路丢来源」在 stage5 这一侧**已经是修好的那个样子**，
移植时**照 stage5 抄**。⚠ 但**提升路还有另一半没修，而且方向相反**——见 §五。

---

## 四、`stage5/draft.md` —— 字节形态

`assemble_book` 把 **deep/ 与 shallow/ 两个目录**合成**一本**，`--tag` 臂拼到 `draft<tag>.md`。
另有 `stage5/<mode>/draft.md` = **单 mode 预览，不是成书**（所以它**不对照计划算缺章**）。

```
# 重构稿（31/31 章：deep 11 · shallow 20）
[> ⚠ 混契约横幅]        ← 有旧契约稿（没有 budget_chars）且不是全部时
[> ⚠ 缺章横幅]          ← 计划有、两条臂都没有的章；**写进 md，不静默跳过**

## 第 N 章（<mode> · 源 [n] · NNN字 · 重合(扣除逐字要求) 0.xxx）

<正文>
```

| 硬机制 | 处置 |
|---|---|
| 同一输出章在两个目录里都有成稿 | **显式终态**——哪一份是成品无从判断，不猜。<br>⚠ 而 review 与界面对同一情况**只报不判死**（判据 `draft_duplicated`）：把「呈现」变成「判死」会让人连报告都看不到 |
| 计划有、两边都没有 | **缺章横幅**写进 md |
| 章头行**不打篇幅方式** | 它恒为一个值，已随 `stage4.md` §一 裁撤；那句话归 system prompt |
| 混了两套契约下写的章 | 横幅 + 控制台告警。判别用**结构特征**（旧稿没有 `budget_chars`），不用时间戳 |
| 两条臂并行收尾 | 文件锁（15s 等待、120s 陈旧锁自清）。拼装幂等，**每跑都拼**是自愈的 |

⚠ **拼装只验「两条臂没打架、合起来覆盖了计划」，验不了「这一章该不该 deep」**——
逐章深度是人的决策，**不落任何产物**（只活在 `--chapters` 实参里）。

---

## 五、`_provenance`

四个跨产物共有的键（`prompts` / `model` / `code` / `inputs`）留给 `_common.md`。

### 5.1 `chNN.json._provenance` —— **全链唯一带轮次账的产物**

| 键 | 类型 | 必填 | 内容 / 盘上 | 证据 |
|---|---|---|---|---|
| `prompts.SYS` | `str` | 是（LLM 稿） | 盘上 3 个不同指纹 = deep · shallow · **deep+`REVISE_TAIL`**（修订轮的 SYS 是拼过的） | **盘·码** |
| `prompts.REVISE` | `str` | **否** | 修订轮才有。盘上 4/30 | **盘·码** |
| `model` | `str` | 是（LLM 稿） | 盘上 30/31 `deepseek-v4-pro`；**人工稿不记**（记了就是假账） | **三源** |
| `code` | `str` | 是 | git 短 sha。盘上 31/31 | **三源** |
| `inputs` | `obj` | 是 | 恒四条：stage1 正文 · stage2 global · stage3 style · **stage4 `writer_context`**（最要紧的一项——`budget_chars` 只能证明契约版次，证明不了身份） | **三源** |
| `round` | `int` | 是 | 盘上 1~2。取**盘上见过的最大轮次 +1**，不按「刚归档的那一份」算；`.INVALID` 的轮次也算进去 | **三源** |
| `revision_of` | `str` | **否** | 上一轮**正文**的指纹。盘上 5/31 | **三源** |
| `review` | `str` | **否** | 触发这一轮的报告指纹。盘上 4/31 | **三源** |
| `fixes` | `[[rule, evidence_key]…]` | **否** | 这一轮针对性下发了哪几条。**只存引用不存渲染后的指令文本**（指令由 `RULES.directive` 生成，存第二份就会跟着表漂）。盘上 4/31 | **三源** |
| `origin` | `str` | **否** | `human` = `--apply-edit` 落的人工稿。盘上 1/31 | **三源** |
| `input_basis` | `str` | **否** | `apply_edit_current_validation` —— **「这份 inputs 是本次重判时读的，不是对旧问题稿历史来源的追认」**。盘上 1/31。<br>⚠ **0 代码读者，但不是孤儿**：它记的是**不可复算**的一件事，删了两种来源在盘上同形（判据 28）；而且它与 stage3/stage4 的 `revalidated` 是同一个东西 | **盘·码** |

⚠ **已裁撤**：`state`（原 `'invalid'`，只在 `.INVALID` 上）——0 读者，且**它恒等于文件名**。
一个叫 `chNN.INVALID.json` 的文件里再写一句「我是 invalid」就是第二落点（`Q-6`）。

> #### `--apply-edit` 那一份是**怎么记来源账的教科书**，移植时连注释一起搬
>
> 继承上一轮的 `inputs`（改的是同一份上游下的稿）· `pop` 掉 `prompts` 与 `model`
> （这一稿不是任何 prompt / 模型产的，记了就是假账）· 记 `origin=human` ·
> 上一轮 `inputs` 为空时**记下这次真实读过的那份并打上 `input_basis`**，
> 明说它不是对历史的追认（判据 28：不知道不能记成匹配）。

> #### ⚠⚠ 而 `--revalidate` 那条路**伪造**来源账——方向与 stage4 恰好相反
>
> stage4 那条路是**丢掉** `_provenance`；本层这条是**盖上一份当前的**：
>
> ```
> run_chapters:  data['_sys'] = sys_prompt          ← 在 --revalidate 分支【之前】
> revalidate  →  save_chapter(...)
>                prov = provenance(prompts={'SYS': data['_sys']}, model=DEFAULT_MODEL)
>                                  ↑ 当前的 SYS 指纹      ↑ 当前的 inputs（进程读账）
> ```
>
> 于是一份**上一轮付费产出**的问题稿被提升为正式稿之后，它的来源账写着：
> **这一轮的 system prompt 指纹 · `model=deepseek-v4-pro` · 这一轮读到的上游指纹**，
> `round` 还 +1 —— 而**没有任何一次新的落笔发生过**。
> `.INVALID` 自己那份真实的 `_provenance` 被整个丢弃。
>
> ⚠ 三件事同时错：① 记了一个没产出它的 prompt；② 记了一个没写它的模型；
> ③ 把「重判提升」记成了「第 N 轮写作」。而 `--apply-edit` 在**同一个文件里、相隔 30 行**，
> 把这三件事全做对了。
> ⇒ 正确形状（照 `stage3.md` §五 / `R-5.10`）：**继承 `.INVALID` 自己的 prov ·
> 补 `revalidated: true` · 刻意不宣称 `origin=human`**（「判据改了」与「人手改过」在节点这一侧分不开）。

### 5.2 `review_<n>.json._provenance`

| 键 | 类型 | 必填 | 内容 | 证据 |
|---|---|---|---|---|
| `prompts.RULES` | `str` | 是 | **判据表自身的指纹**（`repr(RULES)`）。盘上 12/12 同一个值 | **盘·码** |
| `code` | `str` | 是 | git 短 sha | **盘·码** |
| `inputs` | `obj` | 是 | 恒四条（同上）。⚠ **成稿不在里面**——它们记在 `judged`，因为 `read_json` 的两级键会造出一条假的匹配边 | **三源** |

**没有 `model`**——0 成本。`prompts.RULES` 是这份报告的**尺子版本**：
判据表改了而报告没重跑，这个指纹是唯一说得出来的地方（判据 28）。

---

## 六、判据清单（**唯一落点**，两相共用一张表）

**47 条** = **7 HARD / 26 INFO / 9 metric / 5 unver**，`actionable` 23 条。
owner 分布：`stage5` 26 · `None` 12（＝非结论）· `stage4` 2 · `stage2`/`stage3`/`corpus` 各 1。

落盘闸门 / `--recheck` / `--revalidate` / `--apply-edit` / 全书验收相**读同一张表**，谁也不许再抄一份。
「盘上」列 = 用当前代码在三本上重算（只读，不写盘），与盘上最近一份 review 的
`baselines` **逐条相同**（⇒ 那 12 份报告仍是现役读数，不是旧尺子）。

| 判据 | 级别 | 查什么 | 盘上 31 章 | 恒等基线 |
|---|---|---|---|---|
| `draft_too_short` | **HARD** | 成稿不是空的或残的（<50 字早退，其余判据不跑） | **0** | — |
| `draft_truncated` | **HARD** | 结尾是完整句（`_TERMINAL_PUNCT`） | **0** | — |
| `meta_leak` | **HARD** | 正文里没有章节标题/编号/任务术语 | **0** | — |
| `verbatim_missing` | **HARD** | 【原样写入】逐字出现（**允许被一处提示语切开**） | **0**（177 条全落地） | — |
| `keep_value_missing` | **HARD** | 【数值不得改动】都在（**比值不比字面**：`30岁` ↔「三十岁」= 满足） | **0**（61 条全在） | — |
| `secret_leak` | **HARD** | 没有提前点破本章不该揭示的事。**报绿灯时同一行必须打盲区计数；一本书盲区 100% 时落 `unver` 而不是沉默** | **0** ⚠ 而 19/21 条 secret 的探针连揭示章都命中不了 | — |
| `dialogue_halved` | **HARD** | 直接引语没被腰斩（**仅 `shallow` + 计划无净压缩**） | **0** | — |
| `length_over` / `length_under` | INFO | 篇幅上/下限 = **`DRAFT_BAND` 0.90~1.10×预算，无第二层宽限** | **10/31 章**（0.814~1.206 那批） | — |
| `names_absent` | INFO | 本章人物名出现在正文里 | 0 | — |
| `verbatim_typo` / `verbatim_split` | INFO | 没被改字 / 被提示语切开也逐字出现 | 0 | — |
| `must_appear_missing` | INFO | 【必须在场】的物件留下痕迹 | 0（30 条全留痕） | — |
| `invented_number` | INFO | 没有编出下发面以外的「数字+单位」 | **3** | **4** ⚠ 基线高于命中 |
| `invented_name` | INFO | 没有编出下发面以外的人 | 0 | 0 |
| `secret_unknown` | INFO(stage4) | `must_not_reveal` 点到的都查得到 | 0 | — |
| `dialogue_fewer` / `dialogue_broken` / `dialogue_ratio_drift` | INFO | 引语数 / 引号体 / 对白占比 | 3 / 0 / **13** | — / — / **10** |
| `band_drift` / `para_len_drift` | INFO | 句段形态档位 / 段均字数 | **18** / 0 | **7** / 0 |
| `layout_drift` | INFO | 提示语排版惯例 | 0 | — |
| `simile_drift` / `xinli_drift` / `dash_drift` | INFO | 明喻 /「心里」/ 破折号密度 | 7 / 7 / **11** | 5 / 4 / **0** |
| `quotes_in_unquoted_corpus` | INFO(corpus) | 引号用法与语料一致 | 0 | — |
| `event_landing` | INFO | 每一拍在成稿里有落点 | **0** | — |
| `cross_chapter_number` | INFO | 自造数字跨章不打架 | 0 | — |
| `book_length` | INFO(stage4) | 全书篇幅落在 **`BOOK_BAND` 0.90~1.10×源字数** | **0**（实测 0.949 / 1.022 / 0.980） | — |
| `style_block_carries_plot` | INFO(stage3) | 风格块里没夹带情节 | 0 | — |
| `material_malformed` | INFO(stage2) | 下发材料是完整词句（锚点没切过界） | **2** | — |
| `quote_unbalanced` | INFO | 引号配对异常、未归一——**留着报，不静默修**（原 `mech.quote_problems`） | **0/31** | — |
| `deep_overlap_high` | INFO | **闭卷臂**重合度 > `DEEP_OVERLAP_MAX`（10%）。**守的是臂的身份不是文风**；开卷不判 | **0/11 闭卷章**（今天 max 2.4%） | — |
| **〔度量〕** `length_report` · `overlap_report` · `dash_report` · `short_run_report` | metric | **逐章无条件报出** | 31 / 31 / 31 / 31 | — |
| **〔度量〕** `dialogue_report` | metric | **无条件**报出直接引语数与比值，**逐章 + 书级都报** | 31/31 + 每本 1 条书级 | — |
| **〔度量〕** `secret_timeline` | metric | 未揭示秘密在全书成稿里的出现分布 | **21 条 secret** | — |
| **〔度量〕** `book_length_report` · `convergence_report` | metric | 全书篇幅总账 · 与上一轮相比消掉了什么 | 3 / 3（每本各 1） | — |
| **〔度量〕** `name_surface_survey` | metric | 全书自造称谓与撞名 | **0** ⚠ 见下 | — |
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
> **改写式泄底无任何防线**，归语义层，而语义层未接线。
> ⇒ 因此有两条配套：报绿灯时**同一行**打出盲区计数；**一本书盲区 100% 时落 `unver` 而不是沉默**
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
> ⚠ **sev 是 INFO 不是 HARD**：越线的唯一真修法是**重写一整章（付费）**，
> 而本层的 0 成本出路（`--apply-edit` 改一行）对它无效。
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
> 而**最省力的那条是删内容或编内容**。实测代价：可反哺条目 **36 → 9（转人工 27 条，75%）**。
> 每条 `actionable` 的判据必须**进且只进其一并写明理由**，分不干净时当场炸。

### 6.1 判据 ↔ 需求 —— **47 条逐条有落点**（F4-0 平掉，2026-08-12）

`contracts/rules.py` 要求每条判据声明 `requirement`（正则 `^R-\d+\.\d+[a-z]?$`——
**`Q-x` 与裸 `R-3` 都不收**），`tests/test_coverage.py` 双向查：反向让指向不存在编号的判据变红，
正向让「声称有守卫却没有判据指着」的需求行变红。

**审计前的账：20 条判据在需求文档里没有落点**，另有 4 条指着收不了它们的地方
（`secret_timeline` / `no_secrets_in_book` / `event_landing_unver` 指 `Q-9`、
`draft_missing` / `draft_duplicated` 指 `Q-1`、`material_malformed` 指裸 `R-3`、
`name_surface_survey` 指 `R-7.5` 而那一行当时写着「无守卫 → `B-01`」）。
⇒ `REQUIREMENTS.md` 补 17 行、拆 1 行（`R-7.5`），R-8 一节九行**逐行重判**。

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
| `dialogue_fewer` · `dialogue_broken` · `dialogue_ratio_drift` | `R-7.20` ⭐ |
| `band_drift` · `para_len_drift` · `layout_drift` · `simile_drift` · `xinli_drift` · `dash_drift` | `R-7.21` ⭐ |
| `dash_report` · `short_run_report` | `R-7.21b` ⭐ |
| `event_landing` · `event_landing_unver` | `R-7.22` ⭐ |
| `cross_chapter_number` | `R-7.23` ⭐ |
| `length_report` · `overlap_report` · `book_length_report` | `R-8.1b` ⭐ |
| `convergence_report` | `R-8.3b` ⭐ |
| `secret_timeline` · `name_surface_survey` | `R-8.9b` ⭐（**探针自己的基线读数**） |
| `draft_missing` · `draft_duplicated` | `R-8.11` ⭐ |
| `material_malformed` | `R-3.15` ⭐（stage2 的账，由本层回过头来报） |
| `style_block_carries_plot` | `R-4.5`（描述不许出现专名） |
| `secret_unknown` | `R-6.3`（逐章剧透闸门产的 `must_not_reveal`） |

> ⚠ **那 20 条不是「判据多余」，是需求文档漏写了。** 判准很直接：它们**每一条都在盘上跑着**，
> 其中 `dash_drift`（11/31 命中、恒等基线 0/31）与 `band_drift`（18/31）是本层**命中最多**的两条。
> 审计当时逐字核过：`REQUIREMENTS.md` 全文 **`破折号` 0 次 · `心里` 0 次 · `外壳` 0 次 ·
> `段落平均` 0 次**。
>
> ⚠⚠ **最刺眼的那条：`dialogue_halved` 是 7 条 HARD 之一，而需求里没有它**，
> 而「闭卷会系统性把台词压短」是本项目**效应量最大**的一条跨书结论——
> 它当时只作为 `R-7.6` 下面的**一条注**存在（判据 35 的镜像：有守卫、却没有一条要求说它在守什么）。
> 现在它是 `R-7.19`。

#### R-8 一节九行**没有一行是判据在守**

审计发现的第二件事：`R-8.1`~`R-8.9` 全写着 `HARD`，而 47 条判据里只有四行接得住
（`R-8.1` 的四条逐章度量 · `R-8.3`/`R-8.4` 的收敛账 · `R-8.9` 的词表基线），
而那四条接住的其实是**度量**不是阻断。逐行查下来它们说的是**机制**不是对产物的判定：

| 行 | 改判 | 落点 |
|---|---|---|
| `R-8.1` | `结构` | `contracts/rules.py::_validate` ②：`sev` 与 `who_checks` 两侧必须成对 |
| `R-8.2` | `载体` | `Finding` 的两条后置条件（`owner is None` ⇔ 非结论 · `actionable ⇒ owner=='stage5'`） |
| `R-8.3` | `载体` | `Finding.evidence_key` 由 `evidence` 现算并断言 |
| `R-8.4` / `R-8.5` / `R-8.6` / `R-8.7b` | `结构` | 落点在本层实现里，**与模块同批改**（形态照 F3-0 对 `R-6.5`/`R-6.6` 的处置） |
| `R-8.7` | `载体` | `WontFix` 的 `why` 最小长度 + 键↔条目一致性 |
| `R-8.8` | **`待建`** | `INBOX_KIND` 六个取值今天只有 `invalid` 有消费方，**聚合本身一行都没有** |
| `R-8.9` | `结构` | `contracts/rules.py::_validate` ⑤：`wordlist` 不填 `baseline` 当场拒绝注册 |

⇒ 度量与「查不了」那两族因此各自有了自己的行（`R-8.1b` / `R-8.3b` / `R-8.9b` / `R-8.11`），
**不再挂在一行写着「阻断」的需求下面**。

> #### ⚠ `draft_missing` 曾经挂着**两件相反的事**
>
> 「没有成稿」与「有两份成稿」同用一个 key ⇒ `baselines` 里的 `draft_missing: k/N`
> 把两种现象加在一起，读报告的人**按名字去解释那个数**只会解释错
> （收敛键因 `evidence` 不同还分得开，**基线分不开**）。⇒ 已拆成 `draft_missing` + `draft_duplicated`。
> ⚠ 三处行为**故意不同**：`assemble_book` 对双份**显式终态**，而 review 与界面**只报不判死**。

**退役而不删**：`upstream_ref`（原借这个名字挂收敛账，已改叫 `convergence_report`——
一个名字下挂着两件事，读报告的人会按名字去解释数字，判据 32）。
上游各层的结论**已不在本产物里**（看的时候现算，不冻进一份会过期的报告）。

---

## 七、几条不进判据表的硬机制

| 机制 | 触发条件 | 处置 |
|---|---|---|
| `ask_text` 的降级阶梯 | 模型把正文写进 `reasoning_content`、`content` 返回空串（`finish_reason='stop'`） | 换 effort 重试，**全都空则抛**。⚠ 空回答**从不写进缓存** |
| `_write_group` 收尾 | 某章没拿到正文 | **显式终态**（多章一批时提示长列表 wind-down） |
| `trim_to_fit` | 超上限 → 退回去「只删不改」，最多 2 轮；每轮必须真的变短 | 删坏（丢 verbatim / 削断结尾 / 对白腰斩）**整轮弃用**，不做局部修补 |
| `_revise_regression` | 修订轮把上一轮弄坏 | 整轮弃用 + **一次 no-cache 重采样**（同 prompt 走缓存只会拿回同一份坏稿）+ 仍坏则**保留上一轮**，被驳回的稿留 `.rejected.json` |
| 落盘闸门 | 有 `errs` | **不覆盖已有成稿**，问题稿存 `.INVALID`，**一章失败不中断整批** |
| `assemble_book` | 同一章两条臂都有成稿 | **显式终态** |
| `--apply-edit` | 人工稿仍有 `errs` | **只报不拦**（用户裁决：人手动改往往正是为了机器修不动的地方）。判定落进 `errs`/`warns`，界面照常红着 |
| `--revise` 无可自救项 | 全部转人工或连续未消 | **显式终态**并明说「这不是『没问题』」 |

> ⚠ **`--apply-edit` 只报不拦这一条，下游没有第二道**：`stage7` 只排除 `.INVALID.json`，
> **不看正式稿里的 `errs`** ⇒ 一份带阻断级判定的人工稿会一路进 `book.txt`，没有一行报告会说。
> 盘上零样本（唯一那份人工稿的 `errs` 是空的）。这是裁决的后果，不是 bug——但要写进契约。

> ⚠ **本层唯一的结构化调用点没有声明必需顶层键。** `write_batch`（`--batch N`，A/B 用）走 `ask_json`
> 却**没有 `require=`**，拿 `(got.get('chapters') or [])` 兜底 ⇒ 一份**合法 JSON 但没有 `chapters`**
> 的响应会被**正常缓存**，然后由调用方判成显式终态。终态是对的，**但那份坏响应留在缓存里**：
> 下一次不带 `--no-cache` 的重跑会**原样读回来**，而它长得与一次真调用一模一样。
> `ask_json` 的 `require=` 分支**会把这种空壳踢出缓存**。⇒ 应为 `require=('chapters',)`（判据 39）。
> ⚠ 不夸大：现役 8 个结构化调用点里 5 个声明了 `require=`，本层这一个的暴露面最小
> （`--batch` 只用于 A/B，默认 1），**但形状完全相同**。

---

## 八、零样本分支

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

## 九、已经查清楚、结论就是「查不了」的

登记在这里免得被当成漏项。

1. **改写式泄底无任何防线。** 字面探针只挡转抄式，而 19/21 条 secret 连揭示章都命中不了。
   归语义层，语义层未接线。
2. **`must_hold` 恒 `unver`。** 全书 27 条，按定义查不了文面。
3. **「这一章该不该 deep」不落任何产物。** 逐章深度是人的决策，只活在 `--chapters` 实参里；
   `assemble_book` 验的是「两臂没打架 + 覆盖了计划」，验不了这个。
4. **`BEAT_MIN_CHARS` / `VERBATIM_BUDGET_FACTOR` / `_simile_drift` 的 `+1.0` 都没有独立校准**
   （只有 `DASH_CORPUS_P95` 有语料级来源）。
5. **`length_over` / `length_under` 不进任何回环**（`REPAIR_FREE`），`flat_chapter` 那一族在 stage4 也是——
   量出来了然后没有下文（判据 35 的又一处）。
6. **发明越界七项里五项没有判据**（`B-01`），**不补，直到有 ground truth**。
   七项的身份是**给人核对的料**（同 `name_surface_survey`），不是关卡；
   报告里必须一直写着**「探针报 0 条 ≠ 约束生效」**。
   ⚠ 唯一 `full` 的那一项自己也零判别力：`invented_number` 在 2/3 本上被书级恒等闸门判死。
