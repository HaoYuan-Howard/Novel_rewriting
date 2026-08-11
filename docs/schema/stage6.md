# stage6 产物 schema

开篇文案重构。**全书级单次产出**——一次调用产三个候选，**人定稿**。

```
stage6/hook.json            LLM 产 + 确定性判据 —— 一本书一份，定长（`--tag` 时是 hook<tag>.json）
stage6/_rounds/hook.rN.json 归档：上一轮的整份拷贝（形状与 hook.json 逐字段相同）
```

对应需求 **R-9**（开篇文案）。
读法（证据列的取值、为什么三源合并）见 `_README.md`。
**载体 = Pydantic v2**（已定），但本文只定内容、不写模型——模型在骨架阶段照本文写。

> **样本面：盘上 3 本 = 3 份 `hook.json` · 3 份 `_rounds/hook.r1.json` · 9 个候选 · 3 份定稿
> （其中 1 份是 `--apply-edit` 的人工稿）· 0 份 `--tag` 分叉。**
> 每本 **1 次付费调用**——这是全链最便宜的一层，但也因此**样本最少**：
> 一条判据在盘上的分母只有 9（候选）或 3（书）。

---

> ## ⚠⚠ 六条贯穿本层的事实
>
> ### ① 判据表分成两半，边界是**产物类**，不是判据质量
>
> 文案是一个「全文改写」的产物类。**查连续文本的字面探针在它身上结构性失效，
> 查离散符号的探针仍然有效。** 这不是判据没写好，是那一类判据与这个产物类不兼容。
>
> | | 判据 | 为什么 |
> |---|---|---|
> | **有效** | 篇幅 · 段落 · 引号体 · 段间分隔 · 收尾形态 · 人名 · 数字 · 重合度 | 前几类是**离散符号或可数量**，改写改不掉；重合度是**反向**判据——抄了才命中，改写正是它要的信号 |
> | **失效** | 说破答案 · 引语真伪 · 歪曲关系 | 全是**语义**判定，一律落 `unver`（三条） |
>
> 两条实测把这条边界钉死了（都是 0 成本）：
> **`secret_keywords` × 原作文案 = 0 命中**（三本 0/4 · 0/7 · 0/10），而書2 的原作文案
> 确实把一条 secret 讲了出来；**「文案引语必须能在正文里找到」曾是 HARD**，
> 恒等基线当场判死——两本原作文案共 7 条引语在**原作全书**里逐字命中 0 条。
>
> ### ② 现役的剧透闸门是**人定稿**那一步，不是代码
>
> 这句话一直印在产物的三条 `unver` 里，别读成谦虚。它的可执行含义是：
> **5 条 HARD 里没有一条在查「文案有没有替故事把话说完」。**
> `hook_arc_literal`（唯一还禁的那道）是 `info` 且是字面探针，改写式说出结局挡不住。
>
> ### ③ 三条 0 成本写盘路**全部重盖来源账 ⇒ 伪造**，而盘上有逐字节的铁证
>
> `--approve` / `--apply-edit` / `--recheck` 都走同一个 `save_hook`，它无条件盖上
> **当前**的 prompt 指纹与**当前**的 inputs 指纹（三条路都在 `load_all` 之后才分支，
> 于是四份上游全被重新指纹一遍）。实测（三岁那本，逐字节比对）：
>
> | | `_rounds/hook.r1.json` | `hook.json`（当前） |
> |---|---|---|
> | `prompts.N8_SYS` | `bf8ec47b0d` | **`eb82e3e962`** |
> | `prompts.RULES7` | `95345d4eb5` | `95345d4eb5` |
> | 三个候选的 `text` / `findings` | | **逐字相同** |
>
> 当前代码算出来的 N8_SYS 指纹就是 `eb82e3e962`，而它是改了 `APPROACHES['result_flashback']`
> 之后才有的值 ⇒ **那批候选只可能是旧 prompt 产的，当前那份来源账是假的。**
> ⚠ 更细一层：`--approve` **根本不重判**（它复制候选的 `findings`），却照样盖上当前的
> `prompts.RULES7`——判据表一改再 `--approve`，产物就会宣称「我是拿新尺子量的」而实际不是。
>
> **正确形状（`Q-5`，与 `stage5.md` §五 同族）**：
>
> | 路 | 产生了新文本吗 | 重判了吗 | 应该更新什么 |
> |---|---|---|---|
> | 裸跑（付费） | 是 | 是 | 全部 |
> | `--recheck` | 否（只归一） | 是 | **只 `prompts.RULES7`**，`N8_SYS` 不许动 |
> | `--approve` | 否 | **否** | **一个都不许动**，只加一条「谁在什么时候定的稿」 |
> | `--apply-edit` | 是（人写的） | 是 | `prompts.RULES7` + `origin=human`，`N8_SYS` 不许动 |
>
> ### ④ `_provenance` 缺 `model` 与 `params`，而这是一个**付费节点**
>
> 判据 28 要的是 `{prompts, model, code, inputs, params}`，原型只有 `{prompts, code, inputs}`。
> 换了模型、换了 `--max-tokens`，产物里没有任何一处说得出来。
> 对照：`stage5/<mode>/chNN.json` 有 `model`。⚠ `stage7/name_map.json` 缺同样两项。
>
> ### ⑤ `--identity`（恒等基线）是本层最值钱的一道工序——**但它只有一半在工作**
>
> 它**在写 prompt 之前就能跑**：拿本书的原作文案段当"候选"喂进整套判据。
> 原作文案是这本书一份**天然合法**的文案，判据在它身上报红就说明判据把合法情况排除在外了
> （判据 20）。它已经判死过两条：**`hook_invented_quote`（HARD，退役成 `hook_quote_unver`）**
> 与 **`hook_para_sep` 的旧口径**（读 `paragraph_sep_chars` 那个有 off-by-one 的平均值，
> 3 段的文案被判成单换行）。本轮重跑三本：**13 格恒等基线全部 `0/1`**。
>
> ⚠⚠ **但这句话只能读一半：13 格里有 6 格构造上不可能非零。** 逐条查过（判据 31）：
>
> | 格 | 恒等跑时两侧是什么 | |
> |---|---|---|
> | `hook_para_sep` · `hook_ending_shape` | `f(body)` vs `f(src_hook)`，而 `body` **就是** `src_hook` | **恒等式** |
> | `hook_length` · `hook_shape` · `hook_quote_style` | `measure(body)` vs `style_hook`，而 `style_hook` **就是** `measure(src_hook)`（实测逐值相同：319/319 · 21.3/21.3 · `['「」']`/`['「」']`） | **恒等式** |
> | `hook_downsend_leak` | 它**不由 `hook_findings` 产**（只进 `book[]`） | **恒缺** |
> | 其余 7 条 | 拿原作文案去比**别的东西**（成品正文 / 下发面 / arc） | **真读数** |
>
> ⇒ **7 真 6 假，而报告必须把这 6 格标出来**，不许再拿它们当「判据没有误伤原作」的证据。
> ⚠ 那 5 条形态判据**是被修成恒等式的**：修 off-by-one 之前两侧用的是两把不同的尺子，
> 恒等基线因此报过红并抓出了 bug；**修好之后它就不再携带信息了**。
> **同一个基线格，修 bug 前有判别力、修 bug 后没有。**
>
> ⚠ **`--identity` 也必须走 `normalize_and_judge`**（今天直接调 `hook_findings`，跳过归一），
> 否则 `hook_quote_unbalanced` 在恒等基线里**恒缺**，又是一格结构性 0。
>
> ### ⑥ 定稿那一份与候选**不同形**，而它才是进成书的那一份
>
> `candidates[]` 有 4 个键、`approved` 有 5 个（= 候选 + `origin`）——**只差一个 `origin`**
> ⇒ 载体上可以是同一个模型加一个可选字段，而不是两份。
> `approved.text` 是 `n9_rename.assemble_txt` 拼进 `stage7/book.txt` 的那一份——成书的第一段。

---

## 一、`stage6/hook.json`

顶层 **9 个键**，全部必填（`approved` 缺省是 `null`，键恒在）。

| 字段 | 类型 | 必填 | 取值面 / 范围 | 证据 |
|---|---|---|---|---|
| `lead_upto` | `int` | 是 | 下发范围＝`arc.conflict_stages[0]` 的**末章号**，至少 1。盘上 `1 / 2 / 2`。<br>⚠ **只数 `chapters`，绝不读 `stage` 那一列文字**——实测阶段名本身就是结局（「系统揭示与最终选择」） | **三源** |
| `rationale` | `str` | 是 | 一句话，说明三个候选各自赌什么。盘上 89~144 字。<br>⚠ **人定稿时唯一的择优说明**（界面渲染它），而结构化调用点**没有把它声明为必需键**——见 §五 | **盘·码** |
| `candidates` | `obj[]` | 是 | 恒 **3** 条（`APPROACHES` 三种策略各一）。见 §1.1 | **三源** |
| `approved` | `obj｜null` | 是（键恒在） | 定稿。缺省 `null`；**没有 auto_approved**（用户裁决）。见 §1.2 | **三源** |
| `sources` | `obj` | 是 | 只有一个子键 `writer_context`。见 §1.4 | **盘·码** |
| `judged` | `obj` | 是 | `{成稿相对路径: 引用}`。见 §1.3 | **三源** |
| `book` | `obj[]` | 是 | 书级结论，与 `candidates[].findings[]` **同形**（见 §1.5）。盘上恒 **4** 条（3 `unver` + 1 `metric`） | **三源** |
| `baselines_identity` | `obj` | 是 | **恒等基线**：同一把尺子量原作文案段，分母恒 1。hard+info 每条一格，**减去 `hook_reuses_source`**（原作 vs 原作必然 100%，构造使然）⇒ **13 格**。<br>⚠⚠ **其中 6 格是恒等式**（事实⑤）——**报告必须标出来**，别当读数。<br>⚠ 它**不裁**：判据 20 的落点、两个真读者，而且**不可再生**（上游一变就不是当时那把尺子了） | **盘·码** |
| `_provenance` | `obj` | 是 | 见 §三 | **盘·码** |

> #### ⚠ 已裁撤的四个键（存量产物里还有，重跑后消失）
>
> | 键 | 为什么裁 | 信息去哪了 |
> |---|---|---|
> | `title` · `source_id` | 目录名就是唯一落点（`title_of` / `derive_source_meta`）。**第四次撞上同一把尺子**（stage1 ×1 · stage4 ×2 · stage5 ×2） | 消费方现算；两处消费方本来就握着路径 |
> | `baselines`（`{判据: 'k/3'}`） | 0 读者 + 100% 可从同文件的 `candidates[].findings` 现算（盘上 3/3 逐值验过）+ **它自陈不是判据 31 要的基线**（三个候选是同一次调用的相关三条）+ **其中一格结构性说谎**（`hook_downsend_leak` 恒 `0/3`，因为它压根不由 `hook_findings` 产） | 要看就现算；真正的基线是 `baselines_identity` |
> | `counts`（`{sev: 条数}`） | 0 读者 + 100% 现算 + **稀疏**：`dict(Counter)` 的 0 键会消失，盘上 1/3 本没有 `info` 键、3/3 本没有 `hard` 键——「没有这个键」与「值是 0」混掉，正是载体那条默认值警告要防的三态 | 报告现算 |

> #### `lead_upto` 看起来能从 `judged` 推出来——**不能**
>
> 盘上三本 `max(judged[].output_ordinal)` 恰好等于 `lead_upto`，但那是因为开篇几章都有成稿。
> **缺章时 `judged` 会短一截**（`hook_body_missing` 那条报的就是它），而 `lead_upto` 记的是
> **要过的范围**，不是**拿到的范围**。两个量在正常路径上相等，在异常路径上必须分得开。
> ⇒ 与「能 0 成本现算的不落盘」不冲突：它记的是一次付费调用当时的**决策**。

### 1.1 `candidates[]`

| 字段 | 类型 | 必填 | 取值面 / 范围 | 证据 |
|---|---|---|---|---|
| `approach` | `str` | 是 | `conflict_first｜result_flashback｜identity_contrast`（`APPROACHES`，**唯一落点**，prompt 那一份是插值来的）。<br>⚠ 产地**不许 `.get` 兜底**：写歪的 approach 一律显式终态（判据 26） | **三源** |
| `text` | `str` | 是 | 文案本身。盘上**串长** 123~308（含段间分隔符）。⚠ 存的是**归一之后**的字节（见 §1.6） | **三源** |
| `mech` | `obj` | 是 | 归一链的记账。见 §1.6 | **盘·码** |
| `findings` | `obj[]` | 是 | 这一条候选的全部判定。盘上长度 2~3 | **三源** |

> #### ⚠ 已裁撤：`chars`
>
> 它是 `n3_style.measure(text)['chars']`，盘上 9/9 与现算逐值相同。唯一读者是界面卡片。
> ⚠⚠ **裁掉它之后，现算必须留在 API 层（Python，走 `measure`）**，同一条候选实测：
>
> ```
> measure(text)['chars'] = 253   ← 去空白。判据 hook_length 用的就是它
> len(text)              = 277   ← 界面若在 JS 里 text.length 会拿到这个
> ```
>
> 差 24 字（**9.5%**）。下沉到 JS 的后果不是判据坏掉，是**同一页上人会看到两个字数**：
> 卡片写「277 字」，而判据的结论写着「253 字，本书文案段 319 字」。那是判据 14 的原样重演。

### 1.2 `approved`

| 字段 | 类型 | 必填 | 取值面 / 范围 | 证据 |
|---|---|---|---|---|
| `approach` | `str` | 是 | `APPROACHES` **∪ `'human_edit'`**。见下面那个框 | **盘·码** |
| `text` | `str` | 是 | ⚠ **这一份进成书**（`assemble_txt` 拼在 `book.txt` 最前面） | **三源** |
| `origin` | `str` | 是 | 盘上恒 `'human'`。**没有别的取值**——`auto_approved` 是被裁决掉的东西，不是还没做的 | **三源** |
| `mech` | `obj` | 是 | 同 §1.6。⚠ `--approve` 时是**候选那一份的拷贝**；`--apply-edit`/`--recheck` 时是重新归一算出来的 | **盘·码** |
| `findings` | `obj[]` | 是 | ⚠ **三条写它的路都必须带**（`--approve` / `--apply-edit` / `--recheck`）：定稿那一项要携带**它自己这份字节**的判定。只要有一条不带，「这份没问题」与「这份没判过」在 JSON 里就长得一模一样（判据 7） | **三源** |

> #### ⚠ `approach` 在 `approved` 上承担着**两件事**
>
> - `--approve N` ⇒ 写候选的 `approach`，它答的是「定稿是哪一条候选」；
> - `--apply-edit` ⇒ 写 `(上一版 approved).approach or 'human_edit'`，
>   它答的是「这份人工稿是从哪一条改出来的」，**没有前身时才是 `human_edit`**。
>
> 于是：先 `--approve 2` 再 `--apply-edit`，`approach` 会**留着 `result_flashback`**，
> 而文本已经是人手写的。「它是不是人写的」这件事 `origin` 已经答了。
> 盘上 1/3 本是 `human_edit`（那本从没 `--approve` 过就直接上了人工稿）。

### 1.3 `judged{<成稿相对路径>}`

「这份文案是照哪几份成稿写的」。键是**完整相对路径**（`stage5/deep/ch01.json`）。

| 字段 | 类型 | 必填 | 内容 | 证据 |
|---|---|---|---|---|
| `output_ordinal` | `int` | 是 | 输出章号。盘上 1~2 | **盘·码** |
| `round` | `int` | 是 | 那份成稿当时是第几轮。盘上 1~2。**不可再生**（成稿被覆盖后就没了） | **盘·码** |
| `sha1` | `str` | 是 | **整文件**指纹，只作审计 | **三源** |
| `consumed` | `obj` | **否** | 本节点**真正消费**的投影：`{output_ordinal, text_sha1}`。**盘上 1/3 本有**（后加的键） | **盘·码** |
| ~~`mode`~~ | — | — | **已裁**：可从键（路径的中间一段）现算 | **盘·码** |

> #### ⚠ 为什么 `judged` 而不是 `_provenance.inputs`
>
> `read_json` 记的 `input_key` **只取「父目录名/文件名」两级**，`stage5/deep/ch01.json`
> 会被记成 `deep/ch01.json`，`stale_inputs` 反查时拼出来的路径根本不存在 →
> `file_fingerprint` 返回 `None` → 那条边被**静默跳过、永远显示「对得上」**。
> **一个假的匹配比没有记录更坏。** 所以成稿一律裸 `json.load`，账记在 `judged` 里。
>
> #### ⚠ 「文案过没过期」今天有**两把尺子**
>
> | 谁 | 用什么判 |
> |---|---|
> | `n8_hook.check_one` | `judged[].consumed`（投影），**旧产物才退回整文件 `sha1`** |
> | `Web/views.hook_view` | **一律** `judged[].sha1`（整文件） |
>
> 盘上 3/3 两把尺子结论一致（**分歧零样本**），但只是还没发生：1/3 本有 `consumed`、
> 2/3 本没有，而整文件指纹会因 stage5 侧任何一次补 `_provenance` 或 `--recheck` 而变，
> 那些改动**本节点一个字都没消费**。
> ⇒ 判据 28 的正确形状是 **`consumed` 判过期、`sha1` 只作审计**，两处必须合成一把尺子。

### 1.4 `sources.writer_context`

| 字段 | 类型 | 必填 | 取值面 | 证据 |
|---|---|---|---|---|
| `state` | `str` | 是 | `fresh｜stale｜unknown`（三态，`unknown` = ctx 自己没有 `_provenance.inputs`）。盘上 3/3 `fresh` | **三源** |
| `detail` | `obj[]` | 是 | `{key, was, now}`，`state != 'stale'` 时为空。**盘上 3/3 空——非空分支零样本** | **契·码** |
| `code` | `str｜null` | 是 | ctx 的 `_provenance.code` | **盘·码** |

> ⚠ 只有 `writer_context` 一个来源被这样记。stage1/2/3 三份上游的新鲜度靠
> `_provenance.inputs` 兜（`Web/novels._freshness` 会读），**两套机制并存**：
> 一套结构化进产物、一套只在界面上算。移植时应当合成一套。

### 1.5 `findings[]` —— `candidates[].findings` 与 `book[]` **同一个形状**

`pipeline_common.Finding`，与 stage5 / stage7 **逐字段相同**（同一个 namedtuple）。

| 字段 | 类型 | 必填 | 内容 | 证据 |
|---|---|---|---|---|
| `rule` | `str` | 是 | 判据 key，取值面 = §四那 25 条 | **三源** |
| `sev` | `str` | 是 | `hard｜info｜metric｜unver`（`SEVERITY`） | **三源** |
| `item` | `str` | 是 | **肯定式**措辞——它同时是报告里 `[OK]` 那一行的文字（「查过了，这条是好的」）。<br>⚠ **由判据表插值，调用点不许自己写**（判据 25）；拼错的 `rule` 当场炸，不许兜底（判据 26） | **三源** |
| `detail` | `str` | 是 | 这一次的具体情况 | **三源** |
| `evidence` | `str[]` | 是 | 机器可比的凭据。**盘上 `book[]` 的 12 条全空**；`candidates[].findings` 里 3 条非空 | **盘·码** |

⚠ 本层的 `Finding` **没有** `directive`（stage5 反哺回环专用）、**没有** `chapters`/`fix`（stage4 有）。
本层没有 LLM 返工回路：「重写」就是重跑一次这个节点，或人按 `--approve` / `--apply-edit`。

⚠ `evidence` 能不能在文案里**定位**，由判据表的 `quote` 列答（**8/25** 条为真）。
界面的凭据高亮按它取舍：`hook_invented_name` 的凭据是人名（定位得到），
`hook_length` 的是 `136/180`（定位不到）。

### 1.6 `mech` —— 落盘前那次确定性改写的记账

| 字段 | 类型 | 必填 | 盘上 | 证据 |
|---|---|---|---|---|
| `punct_fixed` | `int` | 是 | **恒 0**（9 候选 + 3 定稿） | **盘·码** |
| `quotes_fixed` | `int` | 是 | 0~2（**2 个候选非零**） | **盘·码** |
| `paras_fixed` | `int` | 是 | 0~1（**6/9 候选 = 1**） | **盘·码** |
| ~~`quote_problems`~~ | — | — | **恒空**。**已裁**：它不是记账，是**结论** → 升成判据 `hook_quote_unbalanced`（§四） | **盘·码** |

三条基准全部取**原作文案段**，不取正文：文案与正文原则上可以用不同引号体、
不同段落分隔（实测本书两处一致，那是巧合不是契约）。

⚠ **`mech` 有 0 个读者**（全仓 grep：只有产地写；`hook_view` 把它打包进 API 响应但
`app.js` 全文 0 处渲染它）——**「有 API 字段」不等于「有出口」。**
⇒ 按 `Q-1` 补出口：进校验报告、空也要报、一行常驻非零才展开。

> #### ⚠⚠ 同一个字段，**stage5 裁掉了它，stage6 留着**——这不是不一致，是同一条判准的两个答案
>
> | | stage5 | **stage6** |
> |---|---|---|
> | 有判据在断言归一后的属性吗 | **没有** | **有两条**：`hook_para_sep`（**HARD**，明写是 `_normalize_hook` 的后置条件）· `hook_quote_style`（INFO，查归一之后的引号体） |
> | 记账在盘上携带信息吗 | 31/31 **四项全 0** | **6/9 候选 `paras_fixed=1` · 2/9 `quotes_fixed=2`** |
> | 处置 | 裁（换成报告一行） | **留 + 补出口** |
>
> ⇒ 判准是**「有没有判据依赖这次改写的结果」**，不是「哪一层」。
> 而 stage6 恰恰是那条边界的**正面**：`hook_para_sep` 是一条**被代码提前满足**的 HARD，
> 它报 `[OK]` 时的全部信息量**都在 `paras_fixed` 那个数里**——
> 6/9 非零说明归一真动过手，所以那盏绿灯是有理由的；`mech` 一裁，它就变成
> 一盏说不清为什么亮的绿灯（判据 31）。

---

## 二、`stage6/_rounds/hook.rN.json`

**整份拷贝**，形状与 `hook.json` **逐字段相同**（盘上 3 份，顶层键一个不少）。
不是差量、不是投影——归档是「出事后唯一能回去的那一份」。

| | |
|---|---|
| **何时写** | 付费重跑之前 · `--approve` 之前 · `--apply-edit` 之前 · `--recheck` **且字节真的变了**时 |
| **编号** | `next_round` = 盘上见过的最大轮次 **+1**。⚠ 曾经是 `len(glob(...))+1`，归档目录里少一份就会与现存的某一轮同号并**静默覆盖**它 |
| **`--recheck` 的条件** | 只在 `_texts_of(new) != _texts_of(rec)` 时归档——重判是幂等的，无变化也归档会刷出一串同内容轮次，把「哪一轮换掉了什么」这唯一的证据埋掉 |
| **`_texts_of` 包含定稿** | 三个候选的 `text` **加上** `approved.text`。漏掉定稿会出现「候选归一了、成书里那段没归一」而归档说「没变化」的假账 |

⚠ 盘上三份 `r1` 的 `approved` **全是 `null`**——它们是**定稿之前**的那一版。
这说明归档点选对了：换掉的是「谁被选中」这个决定，而那正是下次调 prompt 时唯一的证据。

⚠ **归档的 `_provenance` 保留的是那一次落盘时的指纹，而正式产物的被 0 成本路盖过**——
事实③那份铁证就是从这里比出来的。

---

## 三、`_provenance`

| 键 | 类型 | 必填 | 内容 | 证据 |
|---|---|---|---|---|
| `prompts.N8_SYS` | `str` | 是 | **插值之后**的 system prompt 指纹（`APPROACHES` 变了它就变）。盘上**两个值**：`bf8ec47b0d` ×2 · `eb82e3e962` ×1 | **盘·码** |
| `prompts.RULES7` | `str` | 是 | 判据表自身的指纹（`repr(RULES7)`）。盘上 3/3 同一个值，且**与当前代码对得上** | **盘·码** |
| `code` | `str` | 是 | `git describe --always --dirty` ⇒ `b371244` 或 **`b371244-dirty`**。<br>⚠ **原型只记 `git rev-parse --short HEAD`，而它在工作树脏时说谎**：盘上 3/3 = `b371244` 而其中一份的 prompt 指纹与另两份不同 ⇒ **代码确实变过，而 `code` 一个字没动**。<br>⚠ 这条改动**对全链生效**（`provenance()` 在共享层） | **盘·码** |
| `model` | `str` | 是 | 本层是付费节点，换了模型产物里没有一处说得出来（事实④） | **契·码** |
| `params` | `obj` | 是 | 至少 `max_tokens`（默认 8000）与 `tag` | **契·码** |
| `inputs` | `obj` | 是 | 恒四条：`stage1/hook_copy.json` · `stage2/global_grounding.json` · `stage3/style_grounding.json` · `stage4/writer_context.json`。由 `read_json` 自动记账，**结构上不可能漏** | **三源** |

⚠ `inputs` 里**没有成稿**——它们在 `judged` 里，理由见 §1.3。
⚠ `prompts` / `code` 今天**0 个比对方**（只被界面原样显示），而事实③/`code` 那两条证明
**记的那个值本身会错** ⇒ 校验器要补一行「本产物的 prompt 指纹 vs 当前代码」。

> #### ⚠ `-dirty` 那个后缀的粗糙处，先说清楚
>
> `git describe --dirty` 对**整个仓**任何一个被跟踪文件的改动都会亮，不只是本节点的代码——
> 噪声偏大。**但它错的方向是安全的**：它说的是「不知道确切是哪一版」，
> 而不是给出一个精确的假答案。
> ⇒ 选它而不是新加一个 `code_dirty` 布尔字段，是因为后者会让旧产物的
> 「没有这个字段」与「工作树干净」混掉——又一个三态（同载体那条默认值警告）。

---

## 四、判据清单（**唯一落点**，25 条现役）

**5 HARD · 9 INFO · 3 metric · 8 unver**。
owner：`stage6` 13 · `None` 11（＝非结论）· `stage3` 1。
**本层一条付费自救都不开**（已经有更便宜的路：`--pick N` 换一条候选、
`--apply-edit` 改一个字，都是 0 成本）⇒ 全表 `repair='NONE'`，于是 `actionable` 全 `False`。
⚠ 原型这里标着 `actionable=True` **12 条**，那是**另一个意思**的 actionable
（「人能 0 成本改掉」而不是「写作层能修」）。**已裁决不放宽载体侧那两条不变量**，
那 12 条的「人怎么改」写进 `Rule.note` —— 理由见 `_report.md` §2.1 那个框。

「盘上」列 = 9 个候选 + 3 本书级；「恒等」列 = `--identity` 在三本上跑的结果。
⚠ **恒等那一列有 6 格是恒等式**（事实⑤），下表用 `≡` 标出来——别把它读成读数。

| 判据 | 级别 | 查什么 | 盘上 | 恒等 |
|---|---|---|---|---|
| `hook_empty` | **HARD** | 文案不是空的或残的（<20 内容字符早退，其余判据不跑） | **0/9** | 0/1 |
| `hook_truncated` | **HARD** | 结尾是完整句（`_TERMINAL_PUNCT`） | **0/9** | 0/1 |
| `hook_reuses_source` | **HARD** | 与**原作文案段**的 20 字窗口重合率 ≤ `REUSE_MAX=2%`（**无豁免**：文案里一句逐字要求都没有）。<br>⚠ **它守的是人，不是模型**——见下面那个框 | **0/9** | ⚠ 剔除（构造必然 100%） |
| `hook_invented_name` | **HARD** | 人物称谓都在**下发面**里有据 | **0/9** | 0/1 |
| `hook_para_sep` | **HARD** | 段间分隔档位 == 原作文案段的档位。**后置条件**，`actionable=False`；它报绿的信息量全在 `mech.paras_fixed` 里（§1.6） | **0/9** | `≡` 恒等式 |
| `hook_transcribes_body` | INFO | 与开篇正文的窗口重合率 ≤ **0.10**（⚠ 行内字面量，无落点——应提成模块常量并写明理由） | 0/9 | 0/1 |
| `hook_invented_number` | INFO | 【数字+单位】都在下发面里有据 | 0/9 | 0/1 |
| `hook_arc_literal` | INFO | 结局/高潮的字面片段没进文案（**改版后唯一还禁的那道**） | 0/9 | 0/1 |
| `hook_length` | INFO | 篇幅 ∈ `HOOK_BAND (0.6, 1.6)` × 原作文案段字数 | 0/9 | `≡` 恒等式 |
| `hook_shape` | INFO | 段均字数 ∈ `SHAPE_BAND (0.5, 2.0)` × 原作文案段 | **1/9** | `≡` 恒等式 |
| `hook_quote_style` | INFO | 引号体与本书一致 | 0/9 | `≡` 恒等式 |
| `hook_quote_unbalanced` | INFO | 引号配对异常、未归一——**留着报，不静默修**（原 `mech.quote_problems`，owner=`stage6`） | **0/9** | ⚠ 恒缺，除非 `--identity` 也走归一 |
| `hook_ending_shape` | INFO | 收尾形态 ∈ `ENDING_SHAPES`（引号台词/问句/省略号/陈述），与原作文案段同类 | **2/9** | `≡` 恒等式 |
| `hook_downsend_leak` | INFO(**stage3**) | **下发面自己**有没有混进结局（`_arc_sources` 回扫拼好的 prompt） | 0/3 书 | `≡` **恒缺**（它不由 `hook_findings` 产） |
| **〔度量〕**`hook_secret_literal` | metric | 文案与秘密原文的**逐字**重合条数（按读者时点分已揭示/未揭示） | **9/9**（值恒 0 条） | 0 条 |
| **〔度量〕**`hook_quote_shape` | metric | 引语条数 · **最长一条字数** · 对白比，与原作**并排**报出 | **9/9** | 6条/36字 · 3/27 · 2/22 |
| **〔度量〕**`hook_report` | metric | 下发范围 · 各候选字数 · 原作文案段字数 | **3/3 书** | — |
| `hook_spoiler_unver` | unver | 「不说破答案」被有效检查过 | **3/3 书** | — |
| `hook_quote_unver` | unver | 「引语在正文里有着落」被检查过 | **3/3 书** | — |
| `hook_relations_unver` | unver | 「没歪曲人物关系」被检查过 | **3/3 书** | — |
| `hook_no_secrets` | unver | 这本书有可查的秘密 | 0 | — |
| `hook_secret_no_time` | unver | 每条秘密都说得出自己什么时候揭示 | 0 | — |
| `hook_not_approved` | unver | 有人给这份文案定过稿（`check_one` 产，不落产物） | 0（三本都已定稿） | — |
| `hook_body_stale` | unver | 判的是照盘上这份正文写的文案（`check_one` 产） | 0 | — |
| `hook_body_missing` | unver | 开篇那几章都有成稿 | 0 | — |

⚠ **已退役（退役而不删，报告里常驻列出）**：`hook_no_source`（「这本书有原作文案段可比」）
——缺 `hook_copy.json` 已改**显式终态**，见 §五。

> #### ⚠ `hook_reuses_source` 的**主语**是人，不是模型——`item` 必须说出来
>
> 三条实测串起来就是理由：判据的基准是 `_hook_copy` · `build_material` 里**没有** `_hook_copy` ·
> 而 `N8_SYS` 的【硬禁·不复用原句】说的是「不要复用你在给定**正文**里读到的句子」。
> ⇒ **模型看不见原作文案段，就不可能复用它** ⇒ 这条 HARD 对模型**结构上不可能红**。
> 反过来，模型**看得见**的正文，对应判据 `hook_transcribes_body` 只是 `info`、阈值 0.10——
> **严格程度与「模型能不能违反」正好反着来。**
>
> ⚠ **不降级、不拆条**：它守的是走 `--apply-edit` 那条路的**人**（人手里有原作文案），
> 而且恒等基线整个建立在它上面（原作 vs 原作必然 100%，所以它被单独剔除）。
> 拆成两条就要给 `--apply-edit` 另立一套判据，而「三条路共用同一条 `normalize_and_judge`」
> 是本层最值钱的一条纪律（§五）。
> ⇒ 处置只有一条：**`item` 说出主语与基准**（判据 32：名字先于证据定下来，
> 后来读报告的人就会按名字去解释数字）。
>
> #### ⚠ 13 条 hard+info 里 **11 条从未命中**，5 条 HARD 全 0 —— 要连基线一起读
>
> - `hook_reuses_source` —— 见上，**对模型结构性不可能红**；
> - `hook_invented_name` / `hook_invented_number` —— 下发面在剧透上界改版后**变宽了**
>   （`secrets[].fact` 全量下发），名字与数字自动有据，判别力随之下降；
> - `hook_empty` / `hook_truncated` —— 失效模式盘上一次都没发生过，**一个会让它红的样本都没有**；
> - **`hook_para_sep` 是唯一一条「绿得有理由」的 HARD**：归一化真的动过手
>   （9 个候选里 6 个 `paras_fixed=1`），而它 0 命中 ⇒ 后置条件确实在成立。
>
> #### ⚠ 三条 `unver` 是**永远输出**的，不是「还没写完」
>
> `hook_spoiler_unver` 尤其：它不是「我们还没来得及做」，是**已实测判别力为零**。
> 省略掉，读报告的人会默认它守住了。这三条与 5 条 HARD 加在一起才是本层真实的防线图：
> **能查的都查了，查不了的写在脸上。**
>
> #### ⚠ `check_one` 今天把 3 条 metric 整个丢掉了
>
> ```python
> # Stage6/n8_hook.py:1179          # Stage7/n9_rename.py:1555
> elif f['sev'] == 'info':          elif f['sev'] in ('info', 'metric'):
> ```
>
> 后果是双重的：`metric` 的结论既不作为命中出现，也不作为 `[OK]` 出现——**它在这份报告里等于不存在**。
> 丢掉的三条是 `hook_report` · `hook_quote_shape` · `hook_secret_literal`，
> 而 **`hook_quote_shape` 正是 `R-9.5` 的唯一落点**。
> ⚠ `Web/validate.CHECKERS['stage6']` 调的就是 `check_one` ⇒ 界面的「校验」页同样看不到；
> 而界面的 **stage6 页**是另一条路（`hook_view` → `renderHook`），它**显示** metric
> ⇒ **同一个数字在一个界面上有、在另一个界面上没有**，取决于人点了哪个 tab。
>
> #### 阈值与词表的落点
>
> | 常量 | 值 | 落点 |
> |---|---|---|
> | `HOOK_BAND` / `SHAPE_BAND` | `(0.6,1.6)` / `(0.5,2.0)` | 模块常量 ✅ |
> | `QUOTE_MIN` | 6 字（短引语在任何一章正文里都必然命中＝保证会亮的绿灯） | 模块常量 ✅ |
> | `REUSE_MAX` | 0.02 | 模块常量 ✅ |
> | **`hook_transcribes_body` 的 0.10** | — | ❌ **行内字面量**，同一族的两个阈值一个有落点一个没有 |
> | `ENDING_SHAPES` | 四类 | 模块常量 ✅（分类器 / 判据措辞 / 报告共用） |
> | `_BAND_SEP` | `{空行分段:2.0, 单换行:1.0}` | ✅ 且有 **import 期断言**钉住它与 `normalize_paragraphs` 的 1.5 阈值对应——阈值一改当场炸 |
> | `APPROACHES` | 三条 | ✅ **唯一落点**，prompt 那一份由 `fill()`（`string.Template`）插值；缺值抛异常，不是渲染成空串 |

### 4.1 判据 ↔ 需求

`contracts/rules.py` 要求每条判据声明 `requirement`，`tests/test_coverage.py` 的反向覆盖率
会让指向不存在编号的判据变红。`R-9` 为此从 8 条补到 **14 条 + `R-9.3b`**（2026-08-10），
移植时又补到 **18 条 + `R-9.3b`**（2026-08-11，见本节末尾那个框）：

| 判据 | 落点 |
|---|---|
| `hook_para_sep` + `mech` 三个计数器 | `R-9.3`（三步归一 + 记账 + 后置条件） |
| `hook_quote_unbalanced` | `R-9.3b`（与 stage5 的 `quote_unbalanced` / `R-7.7` 同型） |
| `hook_ending_shape` | `R-9.4` |
| `hook_quote_shape` | `R-9.5`（「谁在查」＝`度量`） |
| `hook_not_approved` | `R-9.6`（`人`） |
| `hook_spoiler_unver` · `hook_quote_unver` · `hook_relations_unver` | **`R-9.18`**（`人`）—— 原写 `R-9.7`/`R-9.8`，那两行标着「无守卫 → `B-12`」，指过去会被判说假话 |
| `hook_secret_literal` | **`R-9.15`**（`度量`）—— 原写 `R-9.7`，同上；且它本来就是读数不是门 |
| `hook_arc_literal` | `R-9.9` —— **剧透上界**，本层的产品语义 |
| `hook_downsend_leak` | `R-9.10` |
| `hook_reuses_source` · `hook_transcribes_body` | `R-9.11` |
| `hook_invented_name` · `hook_invented_number` | `R-9.12` |
| `hook_empty` · `hook_truncated` | `R-9.13` |
| `hook_length` · `hook_shape` · `hook_quote_style` | `R-9.14` |
| `hook_body_stale` | **`R-9.17`**（来源账；理由 `Q-4` / `Q-5` 写进 `Rule.note`） |
| `hook_report` | **`R-9.15`**（读数无条件报出；理由 `Q-1`） |
| `hook_no_secrets` · `hook_secret_no_time` · `hook_body_missing` | **`R-9.16`**（判定前提不成立时要出声；理由 `Q-1`） |

> #### ⚠ 那 9 条落点是移植时才被逼出来的（2026-08-11）
>
> 上表原有 **5 条**写着 `Q-1` / `Q-4` / `Q-5`，而 `contracts/rules.py::_REQ_RE` 只收
> `R-x.y` ⇒ 它们**注册不进去，import 期当场炸**；另有 **4 条**指着
> `R-9.7` / `R-9.8`，而那两行写着「**无守卫 → `B-12`**」⇒
> `tests/test_coverage.py::test_no_rule_claims_a_requirement_marked_unguarded` 会判它们说假话。
> **9 条判据，一条都注册不进来。**
>
> ⇒ 按 `REQUIREMENTS.md` 的跨层裁决②（`Q` 是问题与裁决的记录，不是需求；
> 指向 `Q` 的判据不进任何一节的正向覆盖率分母）**在 R 侧补四行**收下它们：
> `R-9.15`（度量）· `R-9.16`（前提不成立要出声）· `R-9.17`（来源账）· `R-9.18`（三条查不了的常驻）。
> `Q-x` 降为 `Rule.note` 里的理由。
>
> ⚠ **`R-9.7` / `R-9.8` 原样保留「无守卫 → `B-12`」，从此没有任何判据指着它们**——
> 那是对的：它们说的是「这两件事没人在查」，而 `hook_spoiler_unver` 那三条**正是在说同一件事**，
> 只是换了个地方说。判据指着「无守卫」那一行，等于让同一件事既算无守卫又算有守卫。

> #### ⚠⚠ `R-9` 里曾经 **0 处**提到剧透上界
>
> 逐字核过——`REQUIREMENTS.md` 全文「结局」**6 次全在 stage2**（`R-2.6`/`R-2.7`/`R-2.12`/`B-14`），
> 「卖点」**0 次**、「誊抄」**0 次**、「段落形态」**0 次**。
> 而「秘密全量下发、可以说破；只有 `arc.climax`/`arc.ending`/`arc_tracks[].trajectory` 不下发」
> 是一次**用户裁决**，还是**量出来的**（5 本原作文案无一提结局，而其中一本的原作文案
> 确实说破了一条 secret）。⇒ 现在是 `R-9.9`。
>
> #### ⚠ `R-9.1` / `R-9.2` 的「谁在查」是 `结构`，不是 `HARD`
>
> 它们是**代码结构**的性质（`n8_hook.main` 里只有一次 `ask_json`、`lead_upto()` 的返回值），
> 25 条判据里没有一条在查它们。写着 `HARD` 等于替一个不存在的守卫声称了存在（判据 35）。
> ⚠ **`结构` 的准入条件是那一格必须写出代码落点**——否则它只是「无守卫」的化妆品。
> 取值面的唯一落点是 `contracts/invariants.py` 的 `WHO_CHECKS`。

---

## 五、几条不进判据表的硬机制

| 机制 | 内容 |
|---|---|
| **归一必须在判定之前** | `normalize_and_judge` 是**唯一落点**，三条路共用（候选 / `--apply-edit` / `--recheck`）。分家的代价实测过：`--recheck` 曾把 `approved` 原样搬过去（注释还写着「定稿文本要跟着重判一遍」），于是候选归一了、而 `n9_rename` 真正拼进 `book.txt` 的那一份没有——**报告全绿，交付的书里文案是坏版式** |
| **缺 `hook_copy.json` ⇒ 显式终态** | `R-1.12` 已让它不可达（认不出文案段的书在 stage1 就被挡住），而**一旦执行它会造出 6 盏假绿灯**：0 成本实测把 `_hook_copy` 与 `style_hook` 一起置空重跑，13 条 hard+info 打了 13 个 `[OK]`，其中 **6 条根本没跑**（`hook_reuses_source` · `hook_para_sep` · `hook_ending_shape` · `hook_length` · `hook_shape` · `hook_quote_style`）。<br>⚠ **终态消息必须点名它会废掉哪 6 条判据**——一条不说清代价的终态与静默降级只差一层窗户纸 |
| **段间分隔按字节取档位** | `sep_band_of_text`，**不许读 `measurable.*.paragraph_sep_chars`**：`measure()` 把分隔字符总数除以**段数**，而分隔符只有「段数 − 1」个。七本原作文案真值一律 2.00，那个平均值给出 11 段→1.82 · 8 段→1.75 · **3 段→1.33**——最后一个掉到 1.5 阈值以下。**触发条件：段数 ≤ 3 的文本会被判反**（正文章节段数都在几十以上，现在不咬） |
| **下发面三处收窄 + 一处兜底** | 收窄：人物表取**逐章名册的并集**（不是 `world_facts.characters` 全书名册）· `voice` 剥掉 `mode_variation`（按定义遍历全书各阶段，必然携带后文）· `reconstruction_strategy` **不下发**。兜底：`_downsend_leak` 拿结局的字面片段回扫**拼好的 prompt** |
| **`hook_copy` 在三处方向相反**（判据 33） | **下发面＝挡住**（样例会被模仿，`hook_style` 存在的意义就是把「怎么写」抽象出来）· **检测器＝用满** · **恒等基线＝用满** · **收尾形态与段间分隔的参照集＝用满**（仍是检测器侧） |
| **`_name_stems` 绝不收窄** | 人物表在下发面按章收窄，而发明越界探针的名字池**用全书的**。同一份数据在两处方向正相反——按章收窄探针，第 1 章就检不出真编出来的名字 |
| **`given` 由 `build_material` 一处产出** | 发明越界探针拿它当「给过什么」的全集。探针与 prompt 一旦分家，探针就会开始误报或漏报 |
| **形态给档位、篇幅给数值** | 「喂档位不喂数值」是对**描述性节点**（stage3）说的；stage6 是**生成**节点，字数是它的**目标**不是它要描述的对象。首跑两本全部偏短（118/114 vs 180；116/74/79 vs 319）就是把那条用过了头 |
| **`--approve 0` 必须与「没给这个参数」分开** | `a.approve is not None`——`--approve 0` 是 falsy，从前会**静默跳过整个分支**落到付费路径上，而人以为自己定了稿。端到端实测踩到过 |
| **6 条 0 成本入口全部在 `from llm_client import DeepSeek` 那一行之前 return** | `--dry-run` · `--check-only` · `--identity` · `--recheck` · `--approve` · `--apply-edit`。⚠ 后三条**免费但会写盘** |

⚠ **结构化调用点漏声明一个必需顶层键**：`require=('candidates',)` 没有 `rationale`。
缺了它不会报错——`build_hook` 存 `None`，CLI 报告与界面**静默不显示那一行**，
而它是人定稿时唯一的择优说明，本层的现役闸门恰恰就是人定稿。⇒ 应为 `require=('candidates', 'rationale')`。
✅ **空壳那一半本层做对了**：`if not cands: sys.exit('显式终态：模型没有产出任何候选')`。

---

## 六、零样本分支

盘上一次都没走过的，**别把它们读成「安全」**：

| 分支 | 为什么没有 |
|---|---|
| `sources.writer_context.state ∈ {stale, unknown}` + `detail` 非空 | 三本全 `fresh` |
| `judged[].consumed` 缺席 | 2/3 本就是这个形状——反过来说，**新形状只有 1 个样本**，而两个消费方各用一个（§1.3） |
| 引号配对异常（`hook_quote_unbalanced`） | 从没异常过；它是新加的一条 INFO，按纪律必须有一份会让它红的样本 |
| `book[].evidence` 非空 | 唯二会带凭据的 `hook_body_missing` / `hook_downsend_leak` 都 0 命中 |
| `hook_no_secrets` / `hook_secret_no_time` | 三本都有 secrets，且都有 `reader_reveal_events` |
| `approved.origin` 的第二个取值 | **不存在**——`auto_approved` 是被裁决掉的 |
| 同一输出章在 `judged` 里有两份（双臂各一） | `lead` 只取 `recs[0]`，而 `lead_upto ≤ 2` |
| 5 条 HARD 的红灯 | 9 候选全过、恒等基线全过。⚠ 而恒等基线那一半在 **6 格上根本不可能红**（事实⑤）——别把它算成第二份证据 |

---

## 七、已经查清楚、结论就是「查不了」的

登记在这里免得被当成漏项。

1. **改写式说出结局没有任何防线。** `hook_arc_literal` 是字面探针；同一把切法在原作文案上命中 0/4。
   归语义层，而 `--semantic` **未接线**（`hook_spoiler_unver` 就是这件事的显式登记）。
2. **引语真伪查不了，而且现在也不要求查。** 原作文案的台词在原作正文里根本不存在
   （7 条逐字命中 0 条、最长公共子串 8~29%）——那是这个体裁的常规手法。
3. **人物关系有没有被歪曲查不了。** 谓词是自由文本、人名共现零判别力（两条都在 stage4 被证伪过）。
4. **`hook_quote_shape` 不给阈值。** 禁令已撤（模型现在有合法路径了），但
   per-book 方向不一致（只有一本三条全升），**没有同配置的重复观测就不该给阈值**（判据 17/18）。
5. **`characters[].role` 是一条已知的、未修的结局旁路。** 实测某本的 `孟青兰` 写着
   「后嫁富商老王为妾」——那是这个角色的**归宿**，属【硬禁·结局】要挡的东西，
   而 `_downsend_leak` 只扫 `arc` 的字面、扫不到它。**它是 stage4 投影的账。**
6. **`paragraph_sep_chars` 的 off-by-one 未动**（被 stage3/4/5 一路消费，改分母会动到
   所有书的所有产物指纹）。本层靠 `sep_band_of_text` 绕开它；触发条件已钉进单测：**段数 ≤ 3 会被判反**。
