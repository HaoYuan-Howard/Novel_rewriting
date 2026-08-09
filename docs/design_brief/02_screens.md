# 屏幕清单

13 屏 + 1 个常驻面板。每屏写的是**信息**，不是版式——
布局、层级、配色、组件形态由你定。**只有标了「★ 版式是需求」的几屏，并排结构本身不能改**。

数字全部来自 `05_samples/`（一本 8 章的书）。真实的书 8~44 章。

---

## S-01 · 语料库 　`/`

**回答**：我手上有哪几本书？各自跑到哪一步了？有没有哪本出问题了？新书怎么进来？

**屏上必须同时看见**
- 书列表（现有 3 本）。每本：书名、章数、**7 个阶段各自的产物状态**、两个徽章 `any_stale` / `any_broken`
- 一本书的阶段状态不是「进度百分比」，是**一串产物在不在盘上**：
  `stage1` 3 份、`stage2` 2 份、`stage3` 1 份、`stage4` 2 份、`stage6` 1 份、`stage7` 1 份，
  每份带 `exists` / `bytes` / `mtime` / **`freshness` 三态**（见 `U-01`）
- `stage5` 特殊：它不在上面那张表里，只给一个 `stage5_modes: ["deep","shallow"]`（跑过哪几条臂）
- 上传入口：拖一个 `.txt` 进来（≤20MB）

**数据来源**：`novels.json`
**状态**：正常 / 一本书都没有（首次使用，上传引导应该是主角）/ 加载 / 后端没起来
**难点**：一本书要展示 10 份产物 × 3 种新鲜度，很容易变成一片密密麻麻的小圆点。
想清楚「扫一眼要得到什么」——大概率是「哪本书需要我现在去管」。

---

## S-02 · 一本书的总览 　`/n/:sid`

**回答**：这本书跑到哪儿了？下一步该跑什么？哪份产物已经对不上它的上游了？

**屏上必须同时看见**
- 逐产物的状态与新鲜度（同上，但这里是详情：`provenance` 里记着它是从哪份上游产出的；
  `stale` 数组会具体列出「哪个上游变了、原来的指纹是什么、现在是什么」）
- **「跑一步」面板**：36 个动作（`enums.json` 的 `actions`），每个带 `kind` / `label` / `extra` / **`paid`**
- 作业记录（历史跑过什么，见 `P-01`）

**数据来源**：`novel_overview.json` + `enums.json` + `jobs.json`
**难点**：36 个动作按 stage 分组后仍然很多，且**大部分是 0 成本的诊断动作**（`--dry-run` /
`--check-only` / `--identity` / `--prompt-only`），真正花钱的只有 12 个 `kind` 里的一部分。
不要做成 36 个等权重的按钮墙。

---

## S-03 · 分章报告 　`/n/:sid/stage1`

**回答**：这本 txt 切对了吗？

**屏上必须同时看见**
- 报告：总字数、正文字数、`body_ratio`、章数、**章号有没有断号/重号/是否严格递增**、
  有没有结尾标记、被剔除的内容有多少字（`dropped`）
- **`warnings[]` 要醒目**——这是这一屏的主角，它是「切得可疑但不算错，要人看一眼」
- `notes[3]`：说明性文字，次要
- 章表：8 章 × {章号、原文里的章节标记原样、字数、开头预览}
- 开篇文案段（`hook_copy`）单独一块

**数据来源**：`stage1_report.json` + `stage1_chapters.json` + `stage1_hook_copy.json`
**状态**：`warnings` 为空（本例就是）时要明确说「没有可疑项」，而不是留白

---

## S-04 · 原文 ↔ 骨架对照 　`/n/:sid/read/:o`　★ 版式是需求

**回答**：这一章的原文里，哪些内容被抽成了骨架，哪些**漏了**？

这是整个工具存在的主要理由之一。

**屏上必须同时看见**
- **左：原文**。已经按服务端切好的 `segments[19]` 分块下发，每块带 `{kind, start, end, chars, flagged, text}`。
  `flagged` 的块 = **没有被任何 beat 覆盖**，要高亮出来（本章 19 块）
- **右：抽出来的 9 个 beat**。每个 beat：`beat_id`、`time_layer`（过去/现在/将来）、`place`、
  `present`（在场的人）、`event`（一句事件摘要）、`hinges`（必须保真的原文「点」）、
  `span`（它对应原文的哪个区间）、`located`（这个区间在原文里定位成功了吗）
- **左右要能互相定位**：点一个 beat，左边对应的原文区间要能被找到；反之亦然
- 章摘要（307 字）、叙述人称、`loose`（没被覆盖的字数）、`problems[]`

**数据来源**：`reading_ch3.json`
**难点**：这是本工具里最长的一屏（原文 1461 字 + 9 个 beat 的结构化内容），
两栏高度天然不等；滚动联动做到什么程度是设计决策。
`start`/`end` 是**服务端给的字符下标，切片已经在服务端做好了**，前端不要自己按下标切字符串（`U-14`）。

---

## S-05 · 全局设定 　`/n/:sid/global`

**回答**：这本书的人物、关系、世界规则、秘密、剧情弧线，抽对了吗？

**屏上必须同时看见**
- `characters[15]`：`char_id`、`canonical`（正典名）、`display`（下发给写手的名字）、
  `role`、`address`（这个人在书里被怎么称呼）、`name_distinctiveness`
- `relations[15]`：主谓宾三元组 + **`valid_from_chapter` / `valid_to_chapter`**（这条关系从第几章起成立）
- `world_rules[6]`：每条带 `versions`（规则会随剧情改版）
- `secrets`（本例为空数组 —— **空态要设计**）、`arc`（冲突阶段 + 人物弧线）
- **剧透开关**：`spoilers: false` 时 `hidden` 里的四类（`fate` / `secrets` / `climax` / `ending`）被遮住。
  开关旁边必须常驻一句 `partial_mask_note`——**这是部分遮蔽，不是完整遮蔽**（`U-13`）
- 正文里出现的 `char_001` 这种 id 要能就地解析成人名

**数据来源**：`stage2_global.json`
**难点**：五类内容形状完全不同（人物是卡片、关系是三元组、规则是带版本的条目、
弧线是分阶段的），硬塞进一个统一的表格会很难看。

---

## S-06 · 文风画像 　`/n/:sid/style`

**回答**：量出来的文风事实，和真正发给写手的指令，是不是一回事？

**屏上必须同时看见（左右两栏是这一屏的骨架）**
- **左：量出来的事实**。`measured_now` / `measurable_stored`（正文 + 文案段两组数字），
  以及由数字换算出的 `bands_now` / `bands_stored` 六个维度的**档位词**：
  句子形态 / 段落形态 / 对白形态 / 章篇幅 / 章间形态差异 / 文案段形态
- **右：真正发出去的指令**。`voice[8]`（每条带 `chars`）、`surface_habits[5]`、`hook_style[7]`
- **`bands_now` vs `bands_stored` 的差异要显眼**：数字漂一点可能仍落在同一档，
  **落到另一档才是换了指令**。`drift[]` 就是这个差异
- 一句话说明：**实测数字本身不下发给写手**，只有档位词下发

**数据来源**：`stage3_style.json`
**特殊**：`exists: false` 时这一屏**仍然有内容**——左半边（实测那一半）服务端 0 成本现算。
空态不是白纸，是「右半边还没买」。

---

## S-07 · 计划 ↔ 写作上下文 　`/n/:sid/stage4/:o`　★ 版式是需求

**回答**：计划决定了什么？写手实际会拿到什么？

**屏上必须同时看见**
- 顶部：全书级的 `reconstruction_strategy`（重构策略，一段自由文本）+ 章索引
  `index[8]`（每章 `op` / `span_chars` / `budget_chars` / `in_band` / `beats` 数）
- 本章：`chapter_goal` / `open_hook_goal` / `end_hook_goal` / `budget_chars` / `writer_target`（字数区间）
- **主轴：逐拍左右并排**，本章 8 拍。每拍一行：
  - **左（计划这一侧）**：`i`、`from_beats`（由源章哪几拍合成）、`budget_chars`（分到多少字）、
    `source_chars`、`verbatim_chars`（其中多少字必须逐字保留）
  - **右（写手这一侧）**：`events`（事件摘要，1~3 句）、`verbatim`（必须逐字写进去的原话）、
    `keep_value`（必须保住的硬数字）、`must_appear`、`must_hold`、
    `time_layer`、`place`、`present`、`segments`（舞台，**跨时间层时分行画，不许压平**）
- 底部常驻两句：`plan_scope_note`（计划这一侧真正由 LLM 决定的只剩逐章目标 + 逐拍预算，
  拍怎么切是代码定的）与 `not_in_writer_prompt`（`must_not_reveal` 等**不进写手 prompt**）
- 本章还带 `characters{13}` / `facts{setting,relations,secrets,world_rules,arc,address}` /
  `must_not_reveal[6]`——写手拿到的全套材料

**数据来源**：`stage4_ch3.json`
**特殊**：没跑过 stage4 时这一屏**也有内容**（`plan_source: "identity"` = 恒等基线，
「如果计划什么都不改，写手手里会有什么」）。`plan_source` / `ctx_source` 两个字段告诉你现在显示的是哪种。
**难点**：右栏一拍的内容量差异极大（有的拍只有一句 event，有的带 3 条原话 + 5 个人物），行高无法统一。

---

## S-08 · 成稿 ↔ 材料 　`/n/:sid/stage5/:o`　★ 版式是需求

**回答**：这一章写出来了吗？它守住了该守的材料吗？和原文比，改成什么样了？

**屏上必须同时看见**
- 顶部：`planned 8 / written 8 / invalid 0`、`missing[]`、`duplicates[]`、
  章索引 `index[8]`（每章 `modes` / `chars` / `overlap_excl_verbatim` / **`freshness`**）
- **左：材料**（`materials`）——章目标、预算、逐拍 events + 原文点 + 舞台、`facts`、
  `must_not_reveal_count`。**它先于任何成稿存在**，对着它决定「这几章写不写、写哪种深度」
- **右：成稿卡**，一本书可能同时有 `deep` 和 `shallow` 两份。每份带
  `chars` / `budget_chars` / `overlap_excl_verbatim`（与原文的重合度）/ **`freshness` 三态** / `provenance`
- **成稿卡内还有第二层并排：原文 ↔ 重构稿**（这是本屏最难也最值钱的部分）
  - 分界线由**「必须逐字写入的原话」**定：同一串字节在原文和成稿里各定位一次，**两边都锚到才切**
  - `align: {beats: 8, anchors: 3, rows: 3, inversions: 0, coverage: 0.375}`
    —— 本章 8 拍只有 3 拍有锚，所以只切成 3 块
  - **没有锚的拍并进相邻块，不给它编分界线**。这种块要有明确的视觉标记
    （现有实现用虚线左边框），并标出「这一行盖了第 N,M 拍」
  - **锚点覆盖率 `锚 3/8 拍` 必须常驻显示**：没有这个数，
    「这一行盖了 3 拍」看起来和「这一行就是第 3 拍」一模一样
  - 常驻 `align.note`：**这是对应关系，不是判定**。块里字多字少、像不像，这一屏不下结论
  - 两态切换：【原文并排】/【只看成稿】
- `invalid_drafts[]`：被落盘闸门拦下的问题稿（本例为空）
- 页内跑面板：`--chapters` 必填，空则按钮禁用

**数据来源**：`stage5_ch3.json`
**特殊**：`ctx_exists: false` 时给「先跑 stage4」，**不现算假材料**。

---

## S-09 · 验收 / 修订 　`/n/:sid/review/:o`　★ 版式是需求

**回答**：这一章判出来哪些问题？我怎么直接改？

**屏上必须同时看见**
- **左：这一章的结论** `findings`。每条：`rule` / **`sev`** / `item` / `detail` / `evidence[]` /
  `owner`（根因在哪个 stage）/ `actionable` / `unresolved_rounds`（连续几轮没消）/ `converge`
  - `sev` 四级：**`hard` 阻断 · `info` 提示 · `metric` 度量 · `unver` 查不了**。
    **`metric` 不是问题**——全书 61 条结论里 **41 条是 metric**，不能画成警告色（`U-03`）
  - ⚠ 单章的结论数很少（样本第 3 章只有 **5 条**：1 条 info + 4 条 metric），
    **全书级的 `book[10]` 和 8 章索引才是这一屏的体量来源**。别按「左边一长列错误」设计
- **右：成稿正文，可编辑**
  - 读态：把左边选中那条结论的 `evidence` 在正文里高亮
  - 改态：textarea，保存**落成新的一轮**（`rounds`）
- 全书级结论 `book[10]` 与章索引 `index[8]`（每章 hard/info/unver/unresolved 计数）
- 常驻 `scope_note`：**上游各层的结论不在这一屏**（它们对写手无从执行），落点是 S-13

**数据来源**：`review_ch3.json`
**难点**：左右两栏必须能互相定位（点结论 → 高亮正文里的证据）。
`evidence` 是字符串数组（例：`["二十岁","半月"]`），高亮靠在正文里找这些串。

---

## S-10 · 待人工 　`/n/:sid/inbox`　★

**回答**：自动流程搞不定、**在等我**的，一共有哪些？

**屏上必须同时看见**
- `items[6]`，每条：`kind` / `stage` / `chapter` / `rule` / **`what`（出了什么事）** / **`how`（怎么修）**
- **六类 `kind` 语义不同，要分得开**：
  | kind | 含义 |
  |---|---|
  | `invalid` | 被落盘闸门拦下的问题稿，没有覆盖现役产物，在等人改 |
  | `rejected` | 自救试过并被回归护栏驳回（钱花了，稿丢了） |
  | `stalled` | 连续 N 轮未消 → 判「根因不在写手」，反哺已停手 |
  | `human_only` | 不进付费自救的判据——人改，或裁决不改 |
  | `upstream` | 阻断级，且重写修不掉，根因在上游 |
  | `approval` | 两个「人定稿」闸门（stage6 文案 / stage7 人名表）——**这是按设计就该人做的一步，不是故障** |
- `total: 6` 与 **`wontfix` 单独计数**：「有人看过并决定不修」与「没人看过」必须分得开
- 每条旁边挂「跳到对应章」和它的修法

**数据来源**：`inbox.json`
**状态**：**清空是常态也是目标**。空态要让人觉得「好了」，而不是「没数据」。
**难点**：`approval` 和其他五类混在一起会让人误以为出了故障——但它确实该在这张清单上。

---

## S-11 · 开篇文案 　`/n/:sid/stage6`

**回答**：三个候选文案哪个好？定哪个？

**屏上必须同时看见**
- **三候选并排**：`candidates[3]`，每个 `{index, approach, chars, text, segments, findings, hard, info, approved}`。
  `approach` 是路数（例：`conflict_first`）。文案本身 100~200 字，带段落
- 每个候选的判据结论（`findings`）挂在自己下面
- **定稿按钮**。这不是流程摆设：文案好坏没有确定性判据，**人选的那一下就是现役的唯一闸门**
- 已定稿时显示 `approved`
- 全书级结论 `book[4]`、判据表 `rules[13]`（每条带 `identity` = 原作文案自己过不过得了）

**数据来源**：`stage6_hook.json`
**注意**：这一屏**没有章号**——文案是全书级的一份产出。
三候选是**一次**调用产出的，不是三次（成本提示不要按三倍算）。

---

## S-12 · 人名替换 + 成书 　`/n/:sid/stage7`

**回答**：新人名起得对吗？替换干净了吗？有没有漏掉一个人？

**屏上必须同时看见**
- **新旧人物表并排**：`characters[15]`，每条 `{char_id, old_canonical, new_canonical, renamed,
  not_renamed_reason, reason, aliases, changed_aliases, hits}`。`reason` 是模型给的起名理由
- `surname_map`（姓氏映射，本例 3 个）、`extra_subs[5]`、`subs_count` / `kept_count`
- **「漏网叫法候选」`residual[2]`**：`{stem, where, hit, context, confidence, unit, suggest}`。
  **这是全链唯一能发现「人物表本身漏了人」的通道**——实测靠它捡回过一个根本不在名册里的人。
  它值得比一个折叠区更好的位置
- **逐章替换前后对照** `rows[76]`：每行 `{l, r, kind}`（左旧、右新、变化类型）。
  正文**只读**（改正文要回 S-08/S-09）
- 开篇文案的前后对照 `hook: {before, after}`
- **定稿按钮**（新名的时代感与雅俗没有确定性判据）
- 成书状态 `book_txt: {sha1, chars, chapters, has_hook, on_disk}`

**数据来源**：`stage7_names.json`（全书）+ `stage7_ch3.json`（含某一章的 `rows`）
**注意**：这一屏的章号只切换「正文对照」那一块，人物表是全书级的。

---

## S-13 · 校验报告 　`/n/:sid/check`

**回答**：七个 0 成本校验器现在怎么说？

**屏上必须同时看见**
- 七份报告：`stage1` / `stage2` / `stage3` / `stage4` / `stage5_review` / `stage6` / `stage7`
- 每份：**`badge` 三态**（`pass` / `amber` / `fail`，见 `U-04`）、`hard_count`、`unver_count`
- 三类结论分开：
  - `oks[24]` —— 查过没问题的（**数量最多，默认应该收起来**）
  - `info[28]` —— 提示级
  - `hard[2]` —— 阻断级
  - `unver[]` —— **查不了**（不是「没问题」）
- `info` / `hard` 每条带 `chapters`（涉及哪几章，可点击跳转）和 **`fix: {kind, extra, paid}`**
  ——「重跑修它」按钮，`paid` 由服务端给（`U-11`）
- 缺上游时返回 `{error, kind: "missing_upstream", missing[]}`，要有对应的空态

**数据来源**：`check_stage2.json`（badge=fail，2 条 hard）+ `check_stage5_review.json`（badge=amber，2 条 unver）
**难点**：一份报告 **54~86 条结论 × 7 份**。默认视图必须能一眼看出「哪份红了」，
而不是先滚过 25 条 `oks`。

---

## P-01 · 作业面板（常驻，不是一屏）

**回答**：现在在跑什么？跑完了吗？跑的是哪条命令？

**要素**
- 发起：确认框里**显示将要执行的完整命令行**（`U-09`）。花钱的动作要显式确认
- 运行中：**日志实时流**（SSE，以日志文件为真源，刷新页面不丢）
- 作业记录：`argv` / `python` / `code`(git sha) / `started` / `ended` / `exit_code` / `sid` /
  **`state ∈ {running, ok, failed, unknown}`**
  - **`unknown` 是真状态**：服务重启后，原本 `running` 的一律改判 `unknown`——重启前它是死是活我们真不知道
- **一本书同时只跑一个作业**，第二个请求 409。唯一例外：stage5 的两条臂（deep/shallow）可以并发
- **没有取消功能**（调用在途时钱已经花了）——不要设计取消按钮

**数据来源**：`jobs.json`（本例空数组 —— 空态要设计）+ `enums.json` 的 `actions`
**难点**：它要能跨屏存在（在 S-08 点了「写第 3 章」，切到 S-10 也得看得见它在跑）。
是底部抽屉、侧栏还是顶部条，你定。
