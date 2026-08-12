# stage2 产物 schema

抽取。**这一层是全链第一个调 LLM 的地方**，两个节点、**三个付费调用点**：

```
stage2.global     stage2/global_grounding.json     全书一次调用   → R-2.1 ~ R-2.12
                  （同一节点的第二条臂：--purge-role，见下）
stage2.blueprint  stage2/chapter_blueprints.json   一次调用产一章 → R-3.1 ~ R-3.18
```

> ### ⚠⚠ 第三个付费调用点：`stage2.global --purge-role`
>
> **本文件此前只写了两个调用点，而原型有三个**（F5-0 补，2026-08-12）。
> 漏的这一条不是一个小旋钮：它是 `role 泄结局`（`R-2.6`，**盘上 2/3 本命中、共 7 处**）
> **唯一的修复路**。不写它，读的人会以为那条 HARD 红了之后只能人工改 json。
>
> | | 符号 | 干什么 |
> |---|---|---|
> | 主路径 | `GLOBAL_SYS` | 全书一次抽取 |
> | **第二条臂** | **`ROLE_SPLIT_SYS`** | **语义判官**：把每个角色的 `role`/`fate` **双向重划分**（结局挪进 `fate`，身份留在 `role`），`char_id` 一个不动 |
>
> 三条它自己的纪律，移植时一条都不许丢：
> 1. **`_provenance.prompts` 因此有两个指纹键**（`GLOBAL_SYS` + `ROLE_SPLIT_SYS`）——
>    `_common.md` §一 那条「指纹算在**渲染结果**上」在这里第一次遇到「一个节点两条臂」。
> 2. **判官漏了任何一个 `char_id`、或交回空 `role`，当场入显式终态**，不兜底。
> 3. ⚠ **`_RETIRED_ROLE_PURGE_SYS` 不许搬**：它与 `GLOBAL_SYS` 各写了一份 role/fate 规则，
>    两份说的不是同一条判据，**在真实数据上把两个角色判反了方向**（F-084）。
>    产物里的旧键 `ROLE_PURGE_SYS` 由产地主动 `pop` 掉。
>    ⚠ 同理 `--migrate-identity` 已退役，读到它时当不存在。
>
> `--purge-role` 进 `PAID_FLAGS` + `NODES['stage2.global'].flags`（F5-1）。

读法（证据列的取值、为什么三源合并）见 `_README.md`。
**载体 = Pydantic v2**（已定），但本文只定内容、不写模型——模型在骨架阶段照本文写。

> **样本面：盘上 3 本 = 31 章。**（14 / 8 / 9 章；75 个角色 / 251 拍 / 282 条保真点）
> 与 stage1 不同，**这一层没有「全语料 163 本」可退**——跑一本要花钱。
> 所以本文里凡是「盘上 N/N」的地方，可信度都**系统性低于 `stage1.md`**，
> 而下面第 ① 条事实说的正是为什么它比 3 个样本本身还要低。

---

> ## ⚠⚠ 三条贯穿本层的事实
>
> ### ① 这一层**每一个字段都是模型写的**，而产地**只校验顶层键**
>
> 两个调用点各声明一份必需顶层键（判据 39 / 需求 `Q-1`）：
>
> | 调用 | 必需顶层键 |
> |---|---|
> | 全书抽取 | `entities` · `canon` · **`arc`** |
> | 逐章抽取 | `blueprints` |
>
> **全链规矩：每个结构化调用点声明自己【全部】必需顶层键。**
> 判准不能是「这一块有没有后置兜底判据」——**兜底判据本身是会退役的**，
> 把判准挂在一个会消失的东西上等于没有判准。
>
> **除顶层键之外没有任何解析期校验。** 往下每一层读字段都是 `x.get(k) or []` / `or ''`——
> 模型少写一个 `event`、少写一个 `summary`、少写整个 `narration`，
> **不会有任何一层出声**，产物照常落盘、下游照常跑。
>
> ⇒ **本文表里的「必填」几乎全部是【契约要求它必填】，不是【结构上保证它在】。**
> 「盘上 3/3」在这一层的含义只有一个：**这三次调用它都写了。**
> 换一本书、换一次采样，它可以不在——目前只有带 `[HARD]` 的那些会因此报错。
>
> ### ② 有一批字段是**代码写的**，它们与模型写的那些必须分开读
>
> 模型只写「决策与引用」，字节由确定性代码按引用搬运（判据 12 / 需求 `Q-7`）。
> 落盘前跑的确定性归一有**七类**，**它们全部跑在校验之前**：
>
> | 代码写的 | 由谁 | 模型写的对应物 |
> |---|---|---|
> | `beats[].beat_id` | 归一为 `b<章>_<序>`（模型自拟一律覆盖） | —（模型也写，但被无条件覆盖） |
> | `beats[].hinges[].text` | 按锚点从原文切 | `starts_with` / `ends_with` |
> | `beats[].span.{starts_with,ends_with}` | 回取成原文字节 | 同名字段（模型的版本被覆盖） |
> | `beats[].present_char_ids[]` | 从 `present_names` 解析 | `present_names[]` |
> | `narration[].{narrator,focalizer}_char_id` | 同上 | `{narrator,focalizer}_name` |
> | `characters{}.address[].{surface,by,from_chapter}` · `canonical` | 回取 / 回解 / 抬首现章 / 剥区分词 | 同名字段 |
> | `secrets[].setup_chapters[]` | **裁掉不早于揭示章的** | 同名字段 |
>
> ⚠ **凡是被落盘前改写过的字段，校验器就不能再断言它的值**——那会是一条永远为真的判定
> （判据 24：闸门静默失效，报告照常输出、只是不再携带信息）。七类分三档：
>
> | 档 | 哪些 | 为什么 |
> |---|---|---|
> | **安全** | `beat_id` 归一 · `present_char_ids` / `narration.*_char_id` 解析 | 改写结果本身有**可独立复算的后置条件**（格式 / 在不在 registry），判据没有变空 |
> | **已解决** | `address` 四项 · `setup_chapters` 裁剪 · **verbatim 回取**（F5-0 补，见下） | 改写次数与逐条清单进 `_provenance`，**校验器改断言那个计数**，闸门于是重新可达 |
>
> ⭐ **verbatim 回取原来是这张表上唯一的「未解决」**——「改写不留任何产物痕迹，
> 而它对应的判据在改写之后**恒真**」。F5-0 按 `address_*` 的先例给它立了**三个**记账键
> （`verbatim_rewrites` / `verbatim_recovered` / `verbatim_failures`，见 §3.2），
> 于是这一档现在是空的。⚠ 三个而不是一个：**「一个字都没动」「按最长公共子串救回来的」
> 「锚不到」是三件事**，合成一个计数就等于没记（判据 14）。
>
> ### ③ **盘上这三本，是 FAIL 的**（0 成本校验器实跑）
>
> ```
> 成年人不做选择   FAIL(1)  + 1 项未验证（上游指纹对不上）
> 我把夫君捧成头牌 FAIL(2)
> 我三岁嘴强月老   FAIL(3)
> ```
>
> 四类违规：`模棱语`×2本 · `role 泄结局`×2本 · `address 裸代词`×1本 · `hinges 落在漏抓区`×1本。
> **读盘上那一源时必须知道这件事**：它不是「一份通过了的产物」，
> 拿它当「合法取值面」会把已知违规读成合法分支。逐条见 §四。

---

## 一、`global_grounding.json`

顶层 **4 个键**。**全书一次调用**，三块都是模型产的。

| 字段 | 类型 | 必填 | 取值面 / 范围 | 证据 |
|---|---|---|---|---|
| `entities` | `obj` | 是 | `{characters, relations}`。**在必需键里** | **三源** |
| `canon` | `obj` | 是 | `{setting, world_rules, secrets}`。**在必需键里** | **三源** |
| `arc` | `obj` | 是 | `{conflict_stages, climax, ending, arc_tracks}`。**在必需键里**（有真实消费方：逐章下发阶段名，`arc_tracks`/`climax`/`ending` 末章下发） | **三源** |
| `_provenance` | `obj` | 是 | 见 §三 | **三源** |

⚠ **已裁撤（存量产物里还有，重跑后消失）**：`title` · `source_id`——书名 = 目录名、
`source_id` = 目录名后缀，都能 0 成本现算，落盘只是抄第二份。

### 1.1 `entities.characters{<char_id>}`

顶层是**映射**，键即 `char_id`。盘上 75 个，全部形如 `char_\d{3}`，主角恒 `char_001`。
⚠ **键的格式没有任何判据在查**——下游只做成员检查（`x in registry`），不做形状检查。
形状实际承重的地方在别处：`secrets[].character_known_by` 的 `char_\d+` 分流（§1.5）。

| 字段 | 类型 | 必填 | 取值面 / 范围（盘上 75 人） | 证据 |
|---|---|---|---|---|
| `canonical` | `str` | 是 | 长度 1~8。**[HARD] 全书唯一**（`R-2.1`） | **三源** |
| `address` | `obj[]` | 是 | 长度 1~10，共 156 条。见下表 | **三源** |
| `name_distinctiveness` | `str` | 是 | `high` / `medium` / `common`。**无守卫 → `B-08`**（`R-2.10`） | **三源** |
| `role` | `str` | 是 | 长度 12~106。**[HARD] 非空 · 不含结局词 · 不含模棱语 · 不得与 `canonical` 同字**（`R-2.6`） | **三源** |
| `fate` | `str｜null` | 是（键恒在） | 长度 2~58；**盘上 6/75 为 `null`**。只统计（`R-2.7`） | **三源** |

**`address[]` 的四个子字段**——`R-2.2` ~ `R-2.5` 全部落在这里：

| 字段 | 类型 | 必填 | 取值面 / 范围（156 条） | 证据 |
|---|---|---|---|---|
| `surface` | `str` | 是 | 长度 1~10。**[HARD] 整条不许是代词**（`R-2.3`）；**[INFO] 必须在原文里原样搜得到**（`R-2.2`）。<br>⚠ 落盘前会被**最长子串回取**改写，记进 `_provenance.address_surface_rewrites` | **三源** |
| `by` | `str` | 是 | **闭合**：某个 `char_id` ｜ `narration` ｜ `public`。**[HARD]**（`R-2.4`）<br>盘上 `narration` **86** · `char_id` **52** · `public` **18** | **三源** |
| `context` | `str` | 是 | **闭合**：`address`（当面）｜ `reference`（背后）。盘上 44 / 112。**无守卫 → `B-08`** | **三源** |
| `from_chapter` | `int` | 是 | ≥1 且在全书章范围内。**[HARD] 不早于该 `surface` 在原文里首次出现的章**——**单边**（`R-2.5`）<br>⚠ 落盘前会被**只往后抬**，记进 `_provenance.address_from_chapter_raised` | **三源** |

> #### `by` 为什么把群体压成 `public`（以及它的已知代价）
> 「苏府下人」「怡红院下人」这类群体名解析不出人，而 `by` 的消费是**派发键**（谁在称呼谁）——
> 一个解析不出人的串对派发毫无用处。回解两级（`canonical` → 任一 `address.surface` 唯一命中）
> 之后仍解析不了的**确定性兜底成 `public`**，次数进 `_provenance.address_by_fallback`。
> **已知代价：丢掉「哪个群体」。** 盘上三本兜底 **0 次**——
> ⚠ 这**不是**「没发生过」的证据，它同时也可以是「这三本恰好没有群体称呼」。
>
> ⚠ 这与 `secrets[].character_known_by` 的处置**故意相反**：那边要的是「谁知情」，
> 群体本身就是答案，逼它写 id 只会逼出假 id（`R-3.7` 的同一条道理，见 §1.5）。
> **同一个「无名群体」在两个字段上的正确处置方向相反**——改之前先问它在这里是派发键还是答案。

### 1.2 `entities.relations[]`

盘上 13 / 15 / 42 条 = 70 条。**一律有向**。

| 字段 | 类型 | 必填 | 取值面 / 范围 | 证据 |
|---|---|---|---|---|
| `relation_id` | `str` | 是 | 形如 `rel_\d{2}`，70/70 符合 | **三源** |
| `subject_id` · `object_id` | `str` | 是 | **必须是 `characters` 里的 `char_id`**；地点/机构/物品不入 relations。**[INFO] 两端可解析**（不是 HARD） | **三源** |
| `predicate` | `str` | 是 | 长度 2~11 | **三源** |
| `valid_from_chapter` | `int` | 是 | 关系**世内**成立的章。**[HARD] 在章范围内**（`R-2.8`） | **三源** |
| `valid_to_chapter` | `int｜null` | 是（键恒在） | 失效点（和离/亡故/决裂），没失效填 `null`。**盘上 22/70 非 `null`** | **三源** |

> #### ⚠ `valid_from_chapter` 是【世内生效时点】，不是【读者知情时点】
> 秘密类关系上两者系统性错位，于是 relations 是 secrets 之外的**第二条剧透通道**
> （判据 8：要屏蔽一条信息，就得问它还能从哪儿说出来）。
> **不加字段**（同一语义两个落点必然漂移），在**下发侧**解决：
> 两端人名都在某条 secret 的 `fact` 里、且 `valid_from` 落在隐瞒窗口
> `min(setup) ≤ valid_from < min(reveal)` 内的关系，改按 `reader_reveal_events` 闸门。
> ⚠ **这条闸门的前件是 `setup_chapters`，而盘上 20/21 是空数组**（§1.5）——
> `min(setup)` 不存在时这条闸门等于没开。

### 1.3 `canon.setting`

| 字段 | 类型 | 必填 | 取值面 / 范围 | 证据 |
|---|---|---|---|---|
| `时代背景` | `str` | 是 | 长度 12~42。故事事实，不是文体判断 | **三源** |
| `世界观` | `str` | 是 | 长度 33~69 | **三源** |

> ⚠ **中文 JSON key。** 盘上 12 类产物 1269 条路径逐个扫过，**固定**的中文键只有三处：
> 这两个 · 加 stage7 `name_map` 的 `_groups[].{原姓,成员}`。
> （另有一大批中文键是 **`name_map` 按人名做的映射键**——那是数据不是 schema，别混着数。）
> 这两个还有一处**第二落点**：`stage4/writer_context.json` 的 `chapters[].facts.setting.*`
> 原样透传它们（31/31 章）——改 key 要跟着动那一层（判据 23：改名要 grep 全下游）。
> 载体是 Pydantic 时需要 `alias`；**不要顺手改成拼音/英文**，要改就单独当一次裁决做。

### 1.4 `canon.world_rules[]`

盘上 4 / 6 / 1 条 = 11 条。

| 字段 | 类型 | 必填 | 取值面 / 范围 | 证据 |
|---|---|---|---|---|
| `rule_id` | `str` | 是 | 形如 `wr_\d{2}` | **三源** |
| `kind` | `str` | 是 | **7 值**：`social_norm` · `institution` · `legal_rule` · `ability_system` · `supernatural_rule` · `technology_rule` · `physical_constraint`。**无守卫 → `B-08`**<br>⚠ 盘上只见 **4** 个：`social_norm`×4 · `supernatural_rule`×3 · `legal_rule`×3 · `ability_system`×1。**另三个零样本** | **契·码** |
| `versions` | `obj[]` | 是 | **至少一条**。**[HARD] 规则正文的唯一落点**。盘上 10 条长度 1 · 1 条长度 2 | **三源** |
| `versions[].effective_from_chapter` | `int` | 是 | 从头没变过的规则也要给一条，填 `1` | **三源** |
| `versions[].desc` | `str` | 是 | 长度 23~94。**必须连「怎么运作 / 怎么判读」一起写**——只写「存在」等于没记。**无守卫 → `B-08`** | **三源** |

> #### `desc` 里的人名不是产地判据，是**下发闸门**
> 「`desc` 不许出现人物名字」写在产地 prompt 里，**没有产地判据**。
> 现役的是一条**按叫法时点扣住**的下发闸门：规则里用到的所有叫法各自 `address[].from_chapter`
> 取**最大值**，那一章之前不下发。
> **闸门按【叫法】不按【角色】**——同一个人从 ch1 就在场（叫「龟公」），
> 泄底的是「百晓生」这个 ch8 才出现的叫法。
>
> ⚠ **盘上这道闸门什么也没拦住**：三本各报 2 / 1 / 1 条含人名的规则，
> 其中两本的全部叫法在 ch1 就叫得出 ⇒ 等于全程下发。
> **报出这个基线才知道它有没有判别力**（判据 31）——只有第一本的 `wr_02`/`wr_04` 真被扣到 ch14。

### 1.5 `canon.secrets[]`

盘上 4 / 7 / 10 条 = 21 条。

| 字段 | 类型 | 必填 | 取值面 / 范围 | 证据 |
|---|---|---|---|---|
| `secret_id` | `str` | 是 | 形如 `secret_\d{2}` | **三源** |
| `fact` | `str` | 是 | 长度 17~48。写**被瞒住的真相**，不是揭示那章的情节 | **三源** |
| `character_known_by` | `str[]` | 是 | 长度 1~11，共 73 项。**[HARD] 按【形状】分流**：`char_\d+` 必须能解析；其余是自由文本（「在场士兵」），放行（`R-3.7` 同一条道理）<br>⚠ **盘上 73/73 全是 `char_id`——自由文本那一臂零样本** | **契·码** |
| `setup_chapters` | `int[]` | 是（可为空数组） | 埋点章。**[HARD] 每个都严格 `<` 最早揭示章**（`R-2.9`）——⚠ **判据断言的是 `_provenance.setup_chapters_pruned` 这个计数**，不是字段值：裁剪跑在落盘前，再断言字段值就永远为真<br>盘上 21 条 secret：结构上不可能有埋点（最早揭示 = ch1）**4** · 可以有却空 **16** · 真有 **1** | **三源** |
| `reader_reveal_events` | `obj[]` | 是 | 长度 1~2。分几次揭示记几条 | **三源** |
| `reader_reveal_events[].chapter` | `int` | 是 | 读者第几章被揭示——**逐章剧透闸门的依据**（`secrets` 与 `relations` 两条通道都按它闸） | **三源** |

> #### ⚠ 「冷揭示」与「被裁空」在报告里必须**分开报**
> 盘上 **20/21 条 `setup_chapters` 是空数组**，而「据实无埋点（冷揭示，合法）」
> 与「原本有埋点、被落盘前裁空了」**在字段值上完全同形**。
> 前者合法；后者说明产地在把揭示章号抄进埋点。⇒ 靠 `_provenance.setup_chapters_pruned` 区分。
>
> ⚠ **不采纳「不裁剪、让违约数据留着让判据真的红」**：这是一个**确定性可修**的问题，
> 留着等于每跑一次就要人手修一遍，而判据 15 说确定性的部分不该让人付第二次费。
> **记账拿到的是同样的可见性，代价小得多。**

### 1.6 `arc` —— 这一块是**解读层**，不是事实层

`entities` / `canon` 记的是原文写出来的事实；`arc` 记的是「高潮在哪」「人物弧怎么走」——**判断**。
它的**确定性消费方近乎恒空**（逐章只发一个阶段名），几乎全靠 prompt 消费。

| 字段 | 类型 | 必填 | 取值面 / 范围 | 证据 |
|---|---|---|---|---|
| `conflict_stages` | `obj[]` | 是 | 长度 4~5 | **三源** |
| `conflict_stages[].stage` | `str` | 是 | 长度 5~9。阶段名要**能定位**（「政治清算」），不是对经过的复述——复述必含后几章的结果 = 剧透。**无守卫 → `B-08`** | **三源** |
| `conflict_stages[].chapters` | `int[]` | 是 | 长度 1~5。**盘上 3/3 本恰好覆盖全部章、无重无缺**——但**没有任何判据在查这件事** | **盘·码** |
| `climax` | `str` | 是 | 长度 25~52。**仅末章下发** | **三源** |
| `ending` | `str` | 是 | **仅末章下发** | **三源** |
| `arc_tracks` | `obj[]` | 是 | 长度 4~7，共 16 条。**仅末章下发** | **三源** |
| `arc_tracks[].subject` | `str` | 是 | `char_id` ｜ `"reader"` ｜ `relation_id`。**三种都必须能解析成人话**，否则显式终态<br>盘上 `char_id`×11 · `reader`×3 · `relation_id`×2 | **三源** |
| `arc_tracks[].dimension` | `str` | 是 | **自由短词**（处境／权力／立场／认知／关系亲疏…）。盘上 11 个互不相同 | **三源** |
| `arc_tracks[].change_type` | `str` | 是 | **开放枚举**：`stable｜development｜revelation｜deterioration｜reversal｜…`（末尾的 `…` 是语义的一部分）。**无守卫 → `B-08`**<br>⚠ 盘上只见 3 个：`development`×11 · `revelation`×3 · `deterioration`×2 | **契·码** |
| `arc_tracks[].trajectory` | `str` | 是 | 长度 16~38。要说清**从什么变到什么**，不能只写「有成长」。**无守卫 → `B-08`** | **三源** |

> ⚠ **`subject` 允许 `relation_id` 是一条真链路，而它曾经断着**：`relation_id` 被引用却从未被解析，
> 未解析的 `rel_05` 直接进了写作 prompt。现在由确定性代码解析成「A与B的关系（predicate）」。
> **这类字段的审计必须两维都测**（判据 16）：有确定性消费方 ≠ 那条链路接通了。
>
> ⚠ `dimension` / `change_type` **只在与 `trajectory` 捆在一起时才携带信息**
> （剥掉 `trajectory` 之后剩下的 `{char_001, 道德底线, deterioration}` 对写手是零信息），
> 而 `trajectory` 仅末章下发。**别把它们当成三个独立的信号。**

---

## 二、`chapter_blueprints.json`

顶层 2 个键。**一次调用产一章，只吃到该章为止的正文，没有旋钮。**

| 字段 | 类型 | 必填 | 取值面 / 范围 | 证据 |
|---|---|---|---|---|
| `blueprints` | `obj[]` | 是 | 长度 = 正文章数（盘上 8~14）。**在必需键里** | **三源** |
| `_provenance` | `obj` | 是 | 见 §三 | **三源** |

⚠ **已裁撤（存量产物里还有）**：`source_id`。它**有一个读者**——把这个内部数字 id
（`7615069877019300633`）原样插进规划节点的 **prompt 载荷**，而那正是删它的理由
（`B-05`：prompt 不许泄漏内部结构名/编号）。
**移植时要连投影一起改**：只删字段不删投影，那处会安静地发一个 `None` 过去。

### 2.1 `blueprints[]`

| 字段 | 类型 | 必填 | 取值面 / 范围（盘上 31 章） | 证据 |
|---|---|---|---|---|
| `chapter_ordinal` | `int` | 是 | 全链的章唯一键。**[HARD] 覆盖度 / 顺序 / 不得出现 `section_id`** | **三源** |
| `summary` | `str` | 是 | 长度 81~329。**[HARD] 存在且非空**（`R-3.13`）。**0 确定性消费方**，唯一消费方是计划节点的 prompt——⚠ 规划节点手里**有全书原文**，所以 `summary` 是一层**摘要**而不是它唯一的章级线索：留着是因为「帮它快速理解本章、无害」，不是因为不可替代 | **三源** |
| `beats` | `obj[]` | 是 | 长度 5~16，共 251 条 | **三源** |
| `narration` | `obj[]` | 是 | **盘上恒长度 1**（31/31）。见 §2.5 | **三源** |

### 2.2 `beats[]`

251 条。**「beat」= 一件独立发生的事**；比它大的那一层（连续区间 + 恒定 place + 恒定在场集）
叫**场景**，是**推导出来的、不存字段**（判据 1）。

| 字段 | 类型 | 必填 | 取值面 / 范围（251 拍） | 证据 |
|---|---|---|---|---|
| `beat_id` | `str` | 是 | **`b<章:02d>_<序:02d>`**，章内连续。**代码无条件归一**，模型自拟一律覆盖（`R-3.1`）。**[HARD] 全书唯一 + 格式 + 连续** | **三源** |
| `span` | `obj` | 是 | `{starts_with, ends_with}`，见 §2.3。**[HARD] 按序定位**（`R-3.2`） | **三源** |
| `time_layer` | `str` | 是 | **闭合**：`present` ｜ `recall` ｜ `hearsay`。**[HARD] 取值** · **[HARD] 全书 `present` 不得为 0**（枚举合法 ≠ 分布合理）<br>盘上 `present` 239 (95%) · `recall` 7 · `hearsay` 5 | **三源** |
| `place` | `str｜null` | 是（键恒在） | 长度 1~11，**盘上 40/251 为 `null`**。[INFO] 只做转场计数<br>⚠ **自由文本、粒度不稳**（街上/街边、公寓/公寓卫生间门口）：作为**下发内容**合格，作为**相等判断的键**不合格 | **三源** |
| `present_names` | `str[]` | 是（可为空数组） | 长度 0~7，共 515 项；**21/251 拍为空**。**模型写的决策**：这一拍在场的人，各用原文里称呼他的那个字面。**[HARD] 有据**——全书原文都找不到 = 自造（`R-3.7`）<br>⚠ **盘上仍有 15 项是代词**（`我`），见下 | **三源** |
| `present_char_ids` | `str[]` | 是（可为空数组） | 长度 0~7，共 451 项；**29/251 拍为空**。**代码从 `present_names` 解析出来的，模型不写它**。**[HARD] 都在 registry**——这是解析器的**后置条件**：违反它 = 代码坏了，不是数据坏了 | **三源** |
| `event` | `str` | 是 | 长度 13~175。**按引用逐字搬进下游**，中间层不转抄（`Q-7`）。**[HARD] 存在且非空**（`R-3.13`）——⚠ 这条判据判在**本层**而不是 stage4：留在 stage4 意味着要先付掉一次规划调用才发现 stage2 产坏了 | **三源** |
| `hinges` | `obj[]` | 是（可为空数组） | 长度 0~7，共 282 条；**63/251 拍为空（25%）**。**[HARD] 整章一条都没有 = FAIL**（单拍为空正常） | **三源** |

> #### ⚠ `present_names` 里的代词：**第三类，不能并进另外两类**
> 第一人称书里模型会照原文写「我」，而 `address[].surface` **按设计永远不会收代词**
> （那是 HARD：代词相对说话人，收了它下游必然取到「我妈」这种说法）。
> 所以「我」解析不出来**不是 address 漏收，也不是自造**，是两条契约在这里对不上。
> 产地按**本章唯一叙述者**定人（整章一个叙述声音、且他被解析出来了，
> 「我」在这一章里就只可能是他）；定不了人就空着。
> **盘上：书1 有 15 项代词（5 项定人成功、10 项定不了）· 另两本 0 项。**
>
> ⇒ schema 上 `present_names[]` 的取值面是「原文里的叫法 **∪** 代词」，
> 而**代词永远不会在 registry 里** —— 这两句话必须一起写，少一句就会有人去「修」其中一边。
>
> #### 解析结果的五分，**处置完全不同，绝不合成一个「无据」**
>
> 下表的分母是 **569**：校验器把 `beats[].present_names[]`（515 项）与
> `narration[].{narrator,focalizer}_name`（54 项非 `null`）**一起数**。
> ⚠ 这两个数是两把尺子——`515` 是本字段自己的规模，`569` 才是下面这几个比例的分母。
>
> | 去向 | 判据 | 处置 | 盘上 /569 |
> |---|---|---|---|
> | 唯一命中 | 这个字面只挂一个人 | 解析成 `char_id` | **501**（其余） |
> | **代词** | 字面落在裸代词表里 | 按本章唯一叙述者定人，定不了就空着 | **15**（5 成功 / 10 定不了） |
> | **歧义** | 同一字面挂着好几个人且本章都叫得出 | **不猜**，条目留在 `present_names` 里，[INFO] | **1** |
> | **后见之明** | 名字要到本章之后才出现 | 拦下——**这是收益不是违约**，[INFO] | **0** |
> | **漏收** | 原文里有这个叫法，registry 没收 | [INFO]，该修全书抽取的 `address` | **52** |
> | **自造** | 全书原文里都找不到 | **[HARD]**，只能重生成该章 | **0** |
>
> ⚠ **歧义不判 HARD**：那是**原文自己的歧义**，重跑一百次也一样，判 HARD 只会得到
> 一盏永远红着的灯（判据 20）。三条候选消歧规则实测 **0/9 有效**，所以**没有**消歧逻辑。

### 2.3 `beats[].span`

| 字段 | 类型 | 必填 | 取值面 / 范围（251 条 = 502 个锚点） | 证据 |
|---|---|---|---|---|
| `starts_with` | `str` | 是 | 长度 2~43 | **三源** |
| `ends_with` | `str` | 是 | 长度 1~25 | **三源** |

**契约要的是「原样照抄 8~30 字」，而这是【生成期指导】不是产物不变量**——
落盘前的回取会把两头改长度。盘上 **66/502 个锚点内容字符 < 6**（最短 1），
校验器**明说不判违约**。

> ⚠ **产地按 8~30 要求、校验侧承认做不到，中间那段差额会被模型用编造填上**：
> 实测一条 `ends_with` 写成 `我“嗯”。`（原文只有 `“嗯。”`），补出来的「我」原文里不存在，
> 整条 beat 因锚点定位不到而作废，而且**扛过了一次带证据的定向重生成**。
> ⇒ prompt 里必须有那条逃生口：「锚点本来就短时，照抄它有多少字，不要补字凑长度」
> （`hinges` 那一段一直有，`span` 这一段是补上的）。

**[HARD] 按序定位**（`R-3.2`）：第 n 条的 `starts_with` 必须出现在第 n−1 条的 `ends_with` 之后。
**两条之间允许有空隙**（过渡句、环境交代），**但两头不许留**——
**[HARD] 章首章尾必须被第一/最后一拍覆盖**（`R-3.8`）。
盘上三本合计覆盖率 97~98%，未覆盖区间全在章的中段。

### 2.4 `beats[].hinges[]`

282 条。**闭卷写作唯一的保真通道**，所以它的字节必须是原文的字节。

| 字段 | 类型 | 必填 | 取值面 / 范围 | 证据 |
|---|---|---|---|---|
| `kind` | `str` | 是 | **闭合 4 值**：`硬数字` ｜ `要害台词` ｜ `物件` ｜ `因果前提`。**[HARD]**（`R-3.6`）<br>盘上 `要害台词` 167 · `硬数字` 61 · `物件` 30 · `因果前提` 24 | **三源** |
| `starts_with` | `str` | 是 | 长度 2~24。**模型写的决策**：这段材料在原文的哪一段 | **三源** |
| `ends_with` | `str` | 是 | 长度 1~24 | **三源** |
| `text` | `str` | **否 —— 见下** | 长度 2~81。**代码按锚点从原文切出来的，模型不写它** | **盘·码** |

> #### ⚠⚠ `text` 缺失 = **失败态标记**，不是一个合法的可选字段
>
> 契约把它写成「必须在本 beat 的 span 原文内（锚点化后这是结构上必然成立的）」——
> 那句话只在**成功路径**上成立。锚不到时的设计是**原样留着、不猜不删**（判据 7：
> 删了这一次会响亮报错，下一次跑就没得报了），于是产物里留下一条**没有 `text` 的 hinge**。
>
> **盘上实测 281/282**：三岁 `b07_05` 的「要害台词」锚不到。
> 它是这份产物**唯一**能看出「这次跑是失败态」的痕迹——`_provenance` 里没有记 failures。
>
> ⇒ schema 上 `text` 必须记成**可选**，同时把「缺失」定义成**显式终态标记**而不是缺省值。
> **两件事都要写**：只写「可选」会被读成「有时候没有很正常」；只写「必填」则盘上这份产物不合法。
>
> ⚠ **两个配套事实**：
> ① 锚不到有**三种根因、修法各不相同**——锚点不在本章原文（重生成）/ 本 beat 的 `span`
> 自己就没定位成功（级联，修上游那条就好）/ 锚点在本章但落在**没被任何 beat 覆盖的空档**
> （漏抓的下游后果，重生成）。盘上这一条属于第三种。
> ② 产地的显式终态是**进程非零退出**，它**拦不住下一条命令**——那本书照样跑完了 stage1→stage7。
> 「显式终态」在多命令流水线里只等于「这一步红了」，**不等于「链路被挡住了」**。
>
> ⚠ **verbatim 回取的 failures 从前没有进 `_provenance`**：「这份产物是不是失败态」
> 只能靠遍历全部 hinge 去反推——**靠一个字段的【缺席】去表达失败态**。
> ⇒ F5-0 已给它立 `_provenance.verbatim_failures`（§3.2），失败态从此有正面的落点；
> `text` 仍按判据 7 原样留着（不猜不删），两者互不替代。

**两个锚点允许重叠、甚至完全重合**：128 条实测里 **75 条（59%）** 的内容字符比两个锚点加起来还短——
「硬数字」「物件」天生就是短的（`丝绢`、`二十两银子`）。
按「`ends_with` 必须在 `starts_with` 之后开始」实现的话，这类一律锚不到（实测 97/128 失败）。

**不加单调游标**：`span` 明写「按原文顺序前进」，`hinges` **没有顺序约定**——
它是「这一幕靠什么成立」的清单，模型按重要性列很正常。强加游标会误伤 7/128（判据 20）。

### 2.5 `narration[]`

**盘上 31 章全是单段**，所以这一节的**多段分支整段是零样本**。

| 字段 | 类型 | 必填 | 取值面 / 范围（31 段） | 证据 |
|---|---|---|---|---|
| `grammatical_person` | `str` | 是 | **闭合 3 值**：`first` ｜ `second` ｜ `third`——**只管人称这一个轴**。盘上恒 `first`（31/31），另两个值在**全语料上各有真样本**。**无守卫 → `B-08`** | **三源** |
| `narrator_name` | `str｜null` | 是（键恒在） | **模型写的决策**，与 `present_names` 同一条规矩（用原文里的叫法，叫不出填 `null`）。盘上 4/31 为 `null` | **盘·码** |
| `focalizer_name` | `str｜null` | 是（键恒在） | 同上。盘上 4/31 为 `null` | **盘·码** |
| `narrator_char_id` | `str｜null` | 是（键恒在） | **代码解析出来的**。**[HARD] 在 registry 或 `null`** | **三源** |
| `focalizer_char_id` | `str｜null` | 是（键恒在） | 同上 | **三源** |
| `pronoun_surfaces` | `str[]` | 是 | 该段叙述者**自称的字面**，照原文用字（`我`/`咱`/`本宫`/`朕`）。第三人称填空数组。<br>盘上 31/31 长度恒 1，全是 `我`。[INFO] 有据性 0/31 落空 | **盘·码** |

⚠ **已裁撤（存量产物里还有，恒 `null`）**：`last_beat_id`。它只表达章内段界，
零消费 + 零样本（「一章内换叙述者」在全语料 163 本 + 盘上 31 章里一个正样本都没有）；
真实发生的是**章之间**换人称，那由 `grammatical_person` **逐章存**承担。

> #### ⚠ 人称**逐章存**是实测辩护过的，不是保守
>
> 全语料 163 本剥掉引号内对白只数叙述文，扫出三件事：
>
> | 书 | 叙述文 我 / 你 / 他她（每千字） | 说明 |
> |---|---|---|
> | `功名利禄` | 4.2 / **29.2** / 14.7 | **真·第二人称**（「**你**是山野村姑…**你**五岁那年就想好了」）⇒ `second` 有真样本 |
> | `FQ_皇帝亲自替我宫斗` | 2.6 / 0.6 / **18.6** | **ch1 第一人称 → ch2~ch19 第三人称**，且 ch7 那段是**全知** |
> | `FQ_群臣逼朕送镇国大将军去青楼` | **0.1** / 0.2 / 10.1 | ⚠ **仍是第一人称**，自称是「朕」 |
> | 其余 160 本 | 我 ≥ 8 | 典型第一人称 |
>
> ⚠ 第三本是最要紧的反例：**拿代词密度判人称会把这类书判反**——这正是 `pronoun_surfaces` 存在的理由。
> 第二本钉死了「**人称在章之间会变**」，一个全书标量表达不了它。
>
> #### ⚠ 「限知 vs 全知」这一轴**不在本层立字段**
> 它已经有落点：stage3 的 `narrative_distance` / `interiority`（实测产物逐字写着
> 「紧贴主角内心，以第一人称即时吐露所有真实想法…」——这就是限知，携带的信息比一个二值枚举多）。
> 再立一个就是第二落点（判据 7），而且加字段有实测代价（判据 6：实测加 3 个字段 → 4 个整章的 hinges 归零）。
> ⚠ **已知代价，不假装它被覆盖了**：stage3 是全书一份，表达不了「ch1 限知、ch2 全知」这种章间差异。
> 这是文风保真的缺口，归 **`B-09`**。
>
> #### `grammatical_person` 与 `pronoun_surfaces` 是这一节**唯一真正下发给写作节点**的两项
> 下发投影实测只发：段落位置标签 + `grammatical_person` + `pronoun_surfaces`
> （叫得出名字时再加 `narrator`/`focalizer` 的**本名**，叫不出就**不写这个键**、不兜底成 id）。
> ⇒ **这一节没有「存着好看」的字段**，每一项都验证过有消费方。
>
> ⚠ 它们目前是原样 `json.dumps` 进 prompt 的：
> `叙述视角（按段）：[{"segment":"全章","grammatical_person":"first","pronoun_surfaces":["我"]}]`
> ——一句中文 prompt 里夹着英文枚举 token，是 `B-05` 的一个实例。
> 骨架阶段接投影时一起解决：**取值面定死了，渲染成人话就是确定性的**。
>
> #### 配套：`narration` **保持数组** + 一条绊线
> 数组是「本章有几个叙述声音」**唯一**的表达方式。
> ⇒ **`len(narration) > 1` 出声，`INFO` 不是 `HARD`**（`R-3.14`）。
> ⚠ **报的不是「这本小说有问题」**：章内接一段他人视角 / 书信是**合法写法**，
> 判 `HARD` 就是「判据把合法情况排除在外」（判据 20）。
> 它报的是**我们这一层的能力边界**：「本章有 N 个叙述声音，而本层不记段界」。
> ⚠ 它必然零命中（全语料 + 盘上 31 章零样本），合成样本必须跟上。

---

## 三、`_provenance`（本层特有的部分）

四个跨产物共有的键（`prompts` / `model` / `code` / `inputs`）留给 `_common.md`。
下面只列 **stage2 这两份产物自己的键**。

### 3.1 `global_grounding._provenance`

| 键 | 类型 | 必填 | 内容 | 证据 |
|---|---|---|---|---|
| `address_by_fallback` | `int` | 是 | `by` 兜底成 `public` 的次数。**校验器断言这个计数**——兜底跑在落盘前，再断言字段值就是一条永远为真的判定 | **三源** |
| `address_surface_rewrites` | `[str,str,str][]` | 是 | `(char_id, 原值, 回取值)`。盘上 0 / 2 / 8 条 | **三源** |
| `address_from_chapter_raised` | `[str,str,int,int][]` | 是 | `(char_id, surface, 原章, 抬到的章)`。盘上共 7 条 | **盘·码** |
| `canonical_distinguishers_stripped` | `[str,str,str][]` | 是 | `(char_id, 原值, 剥完的值)`。**盘上 3/3 本为空** | **盘·码** |
| `setup_chapters_pruned` | `[str,int[],int[]][]` | 是 | `(secret_id, 原 setup_chapters, 裁完的)`。**校验器断言的是它，不是 `setup_chapters` 的值**。空列表 = 这一趟没裁过；**键不在 = 「不知道」，不发绿灯** | **契·码** |
| `human_adjudicated` | `obj[]` | **否** | 人工裁掉某条 secret / 规则时记裁决理由。**盘上零样本** | **契·码** |

> #### ⚠ `setup_chapters_pruned` 的证据列 2026-08-11 从 `三源` 改成了 `契·码`
>
> 骨架 A3 的语料体检实测：**盘上 3/3 本的 `_provenance` 里根本没有这个键**
> （键组合逐字为 `[address_by_fallback, address_from_chapter_raised,
> address_surface_rewrites, canonical_distinguishers_stripped, changed, code,
> inputs, model, prompts]`）。契约要它必填是对的——「键不在 = 不知道，不发绿灯」
> 正是它自己写下的规矩——但那一格标 `三源` 是**说了一件盘上没发生过的事**。
> ⇒ 它已登记进 `tests/corpus.py::ADDED`，是一条**新增的必填键**，不是既有事实。

⚠ **已裁撤**：`changed`。它是一个**进程内**的开关（「什么都没改时不落盘——0 成本的记账遍历
不该打断下游的来源链」），确定性迁移那条路径 `pop` 掉它、正常产出那条路径忘了 pop，
于是它落了盘——盘上 3/3 恒 `True`，**没有任何人读它**。进程内那个开关照旧。

### 3.2 `chapter_blueprints._provenance`

| 键 | 类型 | 必填 | 内容 | 证据 |
|---|---|---|---|---|
| `scope_violations` | `str[]` | 是 | 某一批没按「本次任务范围」产出。**[HARD] 非空即失败**；**键不在 = 「不知道」，不发绿灯**。<br>**产地无条件写这个键**（没越界写空列表），否则「键不在 = 没越界」会给所有旧产物发绿灯 | **三源** |
| `batches` | `int[][]` | 是 | 新产物恒 `[[1],[2],…]`；**旧产物记的是别的值**。校验器靠它区分「逐章」「分了 N 批」「不知道」三种产物 | **三源** |
| `context` | `obj` | 是 | `{batch_size, context_upto}`。新产物恒 `{1, true}`。**必须常驻打印**——不打印它，读的人会把「这次没报跨章引用」当成模型学乖了，而其实只是没抽到 | **三源** |
| `char_refs` | `obj` | 是 | 8 个计数：`resolved` / `pronoun_ok` / `ambiguous` / `late` / `unknown` / `invented` / `no_names` / `pronoun`。**解析是可重跑的确定性步骤**，不记这几个数，「这份产物解析时有几条没解出来」在下一次重算之后就永远失去答案 | **三源** |
| `partial_regen_chapters` | `int[]` | **否** | 定向重生成过哪几章（累积）。盘上 2/3 本有 | **盘·码** |
| `repairs` | `obj[]` | **否** | 定向重生成的历史，每条 12 个子字段（`chapter_ordinal` / `failure_items[]` / `failure_evidence` / `user_prompt` / `output` / `model` / `cache_policy` / `fields[]` / `before_metrics{beats,hinges,present_names}` / `after_metrics{…}`）。盘上 2/3 本、共 3 条 | **盘·码** |
| **`verbatim_rewrites`** | `arr[]` | 是 | **F5-0 新增**。精确回取：锚点归一后命中，字节被换成原文的。`(beat_id, 位置, 原值, 回取值)` | **契·码**※ |
| **`verbatim_recovered`** | `arr[]` | 是 | **F5-0 新增**。**LCS 救回**（`recover` 那条臂命中）：`(beat_id, 位置, 原锚点, 救回的片段, 重合度)` | **契·码**※ |
| **`verbatim_failures`** | `arr[]` | 是 | **F5-0 新增**。锚不到：`(beat_id, 位置, 三种根因之一)`。**[HARD] 非空即失败态** | **契·码**※ |

> ### ⚠⚠ 这三个键把事实② 那张三档表里**唯一的「未解决」**那一格填上
>
> 事实② 原文：**verbatim 回取**「改写不留任何产物痕迹，而它对应的判据在改写之后**恒真**」，
> 且 §2.4 记着「**这份产物是不是失败态目前只能靠遍历全部 hinge 去反推**」。
> 修法**照 F4 给 `address_*` 立账的先例**——那一档已因此从「未解决」变成「已解决」：
> 生产侧把「改了多少、改了什么」交出来，校验器**断言那份账**而不再断言字段值。
>
> **为什么是三个键不是一个**（判据 14：一个量纲一个口径）：
>
> | | 它记的事 | 为什么不能与邻居合并 |
> |---|---|---|
> | `verbatim_rewrites` | 锚点**是**原文、只是标点/换行被归一过 | 与下一条合并 ⇒「一个字都没动」与「救回来的」同形 |
> | `verbatim_recovered` | 锚点**不是**原文字节，是按最长公共子串**救回来的**（`HINGE_LCS_MIN` 双门槛：绝对 ≥4 字 + 相对 ≥0.8） | 这条**必须单独可见**：救回来的那一条，锚点本来就不是原文——同 F4-2 把 `split_by_speech_tag` 的放行单独留痕 |
> | `verbatim_failures` | **锚不到**。三种根因修法各不相同（锚点不在本章 / 本 beat 的 span 自己没定位成功 / 落在漏抓区） | 它是**失败态的正面落点**，替掉今天那条「靠 `hinges[].text` 的**缺席**去反推」 |
>
> ⚠ **`verbatim_failures` 非空时产地照旧入显式终态**（原型 `report_verbatim_failures` 就是这么做的），
> 但**账要先落盘**——原型那条 `sys.exit` 之后什么都没留下，
> 而「显式终态」在多命令流水线里只等于「这一步红了」，**不等于「链路被挡住了」**（§2.4 已记）。
>
> ※ **证据列写 `契·码` 而不是 `三源`，也不是裸 `契`**：盘上 3/3 本零样本（这三个键**原型根本没有**），
> 「码」指的是**本仓 F5-5 的产地**。这一格与 `setup_chapters_pruned` 那一格是同一种处置
> （它也是 `契·码` 且盘上一份都没有，已登记进 `tests/corpus.py::ADDED`）——
> 三个新键同样要进 `ADDED['stage2/chapter_blueprints.json']`，否则盘上那三本加载不了。

> `cache_policy` 盘上恒 `refresh-write` —— **定向重生成必须配「不读缓存」**（`R-3.11`）：
> 它的 prompt 与整跑时该章那一批**逐字相同**，缓存必然命中，
> 于是一个字都没重新生成而它照样打印「已重生成」。

### 3.3 ★ 记账键的**出口**（`Q-1`）

**记账不是终点，报出来才是。** 同一条结论 `stage1.md` §3.4 已经下过一次——
**写进文件没人看才是真的静默。**

| 键 | 出口 |
|---|---|
| `address_by_fallback` | 校验器**断言这个计数** |
| `address_surface_rewrites` | 逐条列出给人扫 |
| `setup_chapters_pruned` | **断言计数 + 逐条列出**；「冷揭示」与「被裁空」分开报 |
| `address_from_chapter_raised` | **逐条列出**（`char_id` · 叫法 · 原章 → 抬到的章） |
| `canonical_distinguishers_stripped` | **逐条列出** |
| `human_adjudicated` | **逐条列出裁决理由**——人删掉过东西这件事必须留在报告里 |
| `partial_regen_chapters` | 合并成一行：**哪几章是定向重生成过的** |
| `repairs` | 同上一行展开：**各修了什么**（`failure_items` + before/after 三个计数） |
| **`verbatim_rewrites`** | 逐条列出（形态照 `address_surface_rewrites`） |
| **`verbatim_recovered`** | **断言计数 + 逐条列出**——判据 `anchor_not_verbatim` 断言的就是它 |
| **`verbatim_failures`** | **断言计数 + 逐条列出**，按三种根因分开；**非空即失败态** |

#### 三条形式要求

**① 空也要报，报成 `0 处` 而不是不打印这一行。**
「没改过」与「没在报」在报告上必须分得开（`Q-2`）。
`canonical_distinguishers_stripped` 盘上恒空——如果空就不打印，
这个键从落地到今天**一次都不会出现在任何报告里**，与不存在毫无区别。

**② 一行常驻 + 非空才展开。** 八条记账各占一屏是噪声，而噪声会让人不看报告。
形态：`address.by 兜底成 public 0 处；surface 回取改写 2 处：[…]`。

**③ 出口在【校验报告】，不在 stdout。** stdout 会随会话消失，那不算出口。

> #### ⚠ 这条规则**不止管 stage2**
> `stage3.md` §五那三个键（`attempts` / `revalidated` / `gates_skipped`）
> 与 `stage4.md` §3.3 的 `params` / `revalidated` **同样按这条规则补出口**。

---

## 四、跨产物的不变量

**盘上实况一列请连事实 ③ 一起读：这三本是 FAIL 的。**

| 不变量 | 盘上实况（3 本 / 31 章） | 对应需求 |
|---|---|---|
| `canonical` 全书唯一 | 3/3 本成立 | `R-2.1` |
| `address[].surface` 在原文里原样搜得到 | 3/3 本成立 | `R-2.2` |
| **`address[].surface` 整条不许是代词** | **2/3 本成立**（三岁 `char_034` = `她`） | `R-2.3` |
| 一个角色不得「有据的叫法全都含代词」 | 3/3 本成立（三岁另有 2 条**含**代词的 surface，但该角色另有干净叫法） | `R-2.3` |
| `address[].by` ∈ `char_id｜narration｜public` | 3/3 本成立（兜底 0 次） | `R-2.4` |
| `address[].from_chapter` 不早于该字面首现章 | 3/3 本成立（落盘前已抬 7 处） | `R-2.5` |
| **`role` 不含结局词** | **1/3 本成立**（另两本共 7 处） | `R-2.6` |
| **`role`/`fate`/`canonical` 不含模棱语** | **1/3 本成立**（另两本各 1 处） | `R-2.6` |
| `role` 非空、不与 `canonical` 同字 | 3/3 本成立 | `R-2.6` |
| `relations` 两端可解析 | 3/3 本成立（70/70） | `R-2.8`（[INFO]） |
| **`setup_chapters` 严格 < 最早揭示章** | **3/3 本「成立」，但 20/21 条是空数组** —— 见下 | `R-2.9` |
| `world_rules[].versions` 至少一条带 `desc` | 3/3 本成立（11/11） | `B-08` |
| `arc_tracks[].subject` 三种取值都能解析 | 3/3 本成立（16/16） | — |
| `beat_id` 全书唯一 · 格式 · 章内连续 | 3/3 本成立（251/251） | `R-3.1` |
| `span` 按序可定位 | 3/3 本成立（251/251） | `R-3.2` |
| 章首章尾被首/末拍覆盖 | 3/3 本成立 | `R-3.8` |
| `hinges[].kind` ∈ 四值 | 3/3 本成立（282/282） | `R-3.6` |
| **每条 `hinge` 的锚点能在本 beat 的 span 内定位** | **2/3 本成立**（三岁 1 条落在漏抓区） | `R-3.5` |
| 锚点在定位窗口内只匹配一处 | 3/3 本成立（502/502） | — |
| `present_char_ids` 都在 registry | 3/3 本成立（451/451，0 悬空） | `R-3.7` |
| `present_names` 全书原文里找得到（无自造） | 3/3 本成立（`invented` = 0） | `R-3.7` |
| `time_layer` ∈ 三值 且全书 `present` ≠ 0 | 3/3 本成立 | — |
| 每章至少一条 hinge | 3/3 本成立 | — |
| 切分未坍缩、章级字/拍 ≤ 本书中位 3× | 3/3 本成立 | `R-3.9` |
| 无 `section_id` 泄漏 | 3/3 本成立 | — |
| `_provenance.scope_violations` 为空 | 3/3 本成立 | — |

### 一条**恒真**的不变量与一条**恒假前件**的判据

`secrets 埋点`（§1.5）与 `world_rules 含人名闸门`（§1.4）都会打印一条读起来像通过的结论，
而它们在盘上这批数据里**分别是「输入被落盘前改成合法的了」与「闸门一条都没拦住」**。
**「没报违规」与「根本没在查」必须能区分**（`Q-2`）——这两条现在区分不了。
⇒ 前者已改成断言 `_provenance.setup_chapters_pruned` 的计数；后者要**报出自己的基线**。

---

## 五、判据清单（**唯一落点**，两个节点共用一张表）

**75 条** = **41 HARD / 15 INFO / 17 metric / 2 unver**。`repair` 全表 `NONE`
（本层没有「花一次钱让写手重写」这条路——反哺是 stage5 独有的，`contracts/rules.py::_validate` ④
把 `repair != 'NONE'` 锁在 `owner == 'stage5'` 上）。
owner 分布：`stage2` **56** · `None` **19**（＝非结论，17 metric + 2 unver）。

⚠ 表里标 **✚** 的 **4 条原型没有**——它们是需求行索要的，而原型那两个判定面都没写：
`event_empty` / `summary_empty`（`R-3.13`）· `repair_cache_policy`（`R-3.11`）· `verbatim_failed`（`R-3.18`）。
**不补这四条，stage2 进 `PORTED_STAGES` 那一刻正向覆盖率会点名这三行需求。**

两个节点（`stage2.global` / `stage2.blueprint`）**读同一张表**，谁也不许再抄一份。
⚠ **`--check-only` 只挂在 `stage2.blueprint` 上**（`NODES` 里只有它有这个 flag，
而 `test_every_ported_stage_has_exactly_one_check_node` 要求每层恰好一个）——
那一相**两份产物一起读**（判 `canonical` 唯一要 global，判 `present_char_ids` 要 registry），
所以「global 的判据没有 0 成本复检」这句话不成立。

### 5.0 ⚠ 这张表是从零建的，而原型那个「91」不是判据数

原型 `Stage2/check_blueprints.py::check_one` 里**一个 id 都没有**，严重度只由调用的方法名
（`fail` / `note` / `unverified`）承载。逐项数下来：

| 口径 | 数 |
|---|---|
| `rep.*` 调用点 | **131**（44 `fail` · 58 `note` · 25 `ok` · 4 `unverified`） |
| **不同的中文首参 × 严重度** | **91** ← 仓根 README 记的就是这个数 |
| 其中跨严重度同名的 | **6 次**（`char_id` · `hinges 命中` · `secrets 埋点` · `越界产出` 各 2 家族；**`present_char_ids` 占 3 家族**） |
| ⇒ **不同的中文首参** | **85** |

`91` 数的是「(名字, 严重度) 对」，不是判据。那 5 个名字在原型里是**同一条判定的两三种结局**
（命中 / 前件不成立 / 上游对不上），而本仓 `RuleLine` 的 `sev` 与 `status` 是**两格**
（`StatusCounts` 分 ok/hit/unver/skipped）⇒ 一条 `sev='hard'` 的判据完全可以落 `unver` 状态。
**并成一条**：拆两条会让「这条判了没有」在报告上出现两行，而 `wontfix` 的键要写哪一个说不清。

85 → 75 的账（**每一条都在下面 §5.2 / §5.3 有落点，一条都没有蒸发**）：

```
85  不同的中文首参
 −2  取值面枚举 → 载体（§5.2 甲）
 −3  来源/新鲜度/记账 → 结构（§5.2 乙、丙）
 −9  存量契约探针 → 退役而不删（§5.3）
 −1  `揭示痕迹` 并进 `揭示无痕迹`（原型自己写着「这一支不许打 [OK]」，它是同一条的反面）
 +1  `narration 样本量` 拆成告警与度量各一条（判据 21：度量不许挂在告警的反面）
 +4  ✚ 原型没有、而需求行索要的（见上）
 ─────
 75
```

⚠ **仓根 `README.md` 与 `novel/stages/stage6/__init__.py` 里那个「91」要跟着改成 75**
（形态照 F4-0 把 stage5 的 43 改成 47）。

### 5.1 表

「盘上」列 = **2026-08-12 用原型校验器在三本上实跑一遍量的**（`--all`，0 成本、纯 stdout、
一个文件都不写；原型仓 `git status` 事后 0 改动）。**这三本是 FAIL 的**（事实③），
所以这一列里非 0 的那几格是**真反例样本**，不是噪声。

| 判据 | 级别 | 查什么 | 盘上 3 本 / 31 章 | 修法 |
|---|---|---|---|---|
| **一、产物完整性与章对齐** | | | | |
| `upstream_missing` | **HARD** | 两份 stage2 产物齐备才跑得动——**缺一份判 FAIL 不判 note**：什么都没查却打 PASS 是把两件事混为一谈 | 0/3 | — |
| `section_id_leak` | **HARD** | blueprint 不含上游的分节键 `section_id` | 0/3 | — |
| `chapter_coverage` | **HARD** | 每个正文章恰有一份 blueprint（不缺 / 不多 / 不重） | 0/3（14+8+9 全覆盖） | `blueprint --chapters` |
| `chapter_order` | **HARD** | blueprint 顺序与 `main_text` 章序一致 | 0/3 | `blueprint --chapters` |
| **二、称谓表** | | | | |
| `canonical_unique` | **HARD** | 一个显示名只挂一个 `char_id`（重名 ⇒ 下游按名字解析**静默**歧义） | 0/3（12 / 15 / 48 个显示名） | — |
| `character_unattested` | **HARD** | `canonical`（剥区分词）与全部 `surface` **至少有一个**能在原文里搜到 | 0/3 | — |
| `address_by_unresolved` | **HARD** | `by` 是 registry 里的 `char_id`，或 `narration` / `public` | 0/3（27 / 47 / 82 条） | — |
| `address_from_out_of_range` | **HARD** | `from_chapter` 在全书章范围内（越界＝这条称呼**永不下发**） | 0/3 | — |
| `address_from_too_early` | **HARD** | `from_chapter` 不早于该字面在原文里首次出现的章。**单边**：早于一定错，晚于合法 | 0/3（落盘前已抬 7 处） | — |
| `address_bare_pronoun` | **HARD** | `surface` 整条不是代词 | **1/3** ⭐ 三岁 `char_034='她'` | — |
| `address_all_pronoun` | **HARD** | 一个角色**有据的叫法不得全都含代词**（否则下游 `display_name` 只能取到「我妈」） | 0/3 | — |
| `address_has_pronoun` | INFO | 含代词但该角色另有干净叫法（违约，但下游取不到它） | **1/3**（三岁 2 条） | — |
| `address_selfref` | INFO | 「自己这么叫自己」——自称（本宫/哀家）正当，挂错人不正当 | **1/3**（三岁 1 条） | — |
| `address_missing` | INFO | 每个角色至少有一条称呼 | 0/3 | — |
| `address_unlocatable` | INFO | 每条 `surface` 都能在原文里原样找到 | 0/3 | — |
| **三、身份与结局** | | | | |
| `role_missing` | **HARD** | 每个角色都有 `role`（下游只下发 role，缺了写手不认识人） | 0/3 | — |
| `role_leaks_fate` | **HARD**（词表） | `role` 里没有结局词——拆 role/fate 的**唯一理由**就是只下发 role | **2/3** ⭐ 头牌 2 处 · 三岁 5 处 | `global --purge-role`（**付费**） |
| `role_repeats_name` | **HARD** | `role` 不与 `canonical` 完全同字（写手拿到的会是「X：X」） | 0/3 | — |
| `hedge_words` | **HARD**（词表） | `role`/`fate`/`canonical` 里没有模棱语 | **2/3** ⭐ 成年人 1 · 头牌 1，**两处都在 `fate`** | — |
| `distinguisher_lonely` | **HARD** | 带区分词的 `canonical` 必须真有同名对象（区分词会被写手原样写进正文） | 0/3 | — |
| `role_fate_unverifiable` | unver | **改写式结局泄漏没有确定性防线**——词表只挡转抄式 | **1/3 打出** ⚠ 见下 | — |
| `fate_report` | metric | `fate` 的缺失率**无条件报出**（`R-2.7`） | 12/12 · **9/15** · 48/48 | — |
| **四、世界规则 · 秘密 · 关系** | | | | |
| `world_rule_no_desc` | **HARD** | 每条规则至少一个带 `desc` 的 version（`versions[].desc` 是正文**唯一**落点） | 0/3 | — |
| `world_rule_names_person` | INFO | 规则正文里出现人名 ⇒ 下游按**叫法时点**扣住。**必须报出闸门自己的基线** | **3/3**（2 / 1 / 1 条）⚠ 见下 | — |
| `secret_setup_order` | **HARD** | 埋点章**严格早于**最早揭示章（`==` 也违约）。**断言 `_provenance.setup_chapters_pruned` 的计数，不断言字段值** | 0/3 | `global --migrate`（0 成本） |
| `secret_cold_reveal` | metric | 没有埋点的 secret 条数（凭空点破，据实留空是允许的） | **3/4 · 7/7 · 10/10** ⚠ 见下 | — |
| `secret_known_by_dangling` | **HARD** | `character_known_by` 按**形状**分流：`char_\d+` 必须能解析，自由文本（「在场士兵」）放行 | 0/3（24 / 24 / 25 条） | — |
| `reveal_unmarked` | INFO（词表） | 揭示章里有没有点破痕迹。**这是筛子不是判据** | **2/3**（头牌 3/8 · 三岁 6/10） | `global --drop`（0 成本） |
| `secrets_absent` | unver | 这本书有可查的秘密（一条都没有 ⇒ 逐章剧透闸门恒不执行） | 0/3（三本都有） | — |
| `relation_dangling` | INFO | `relations` 两端都是 registry 里的 `char_id` | 0/3（13 / 15 / 42 条） | — |
| **五、拍与区间** | | | | |
| `beat_id_unique` | **HARD** | 全书 `beat_id` 不重复 | 0/3（93 / 67 / 91 个） | — |
| `beat_id_format` | **HARD** | `b<章:02d>_<序:02d>` 且章内序连续、章号与所属章一致 | 0/3 | — |
| `span_unlocatable` | **HARD** | 每条 `span` 的首尾锚点都能在原文里**按序**定位（定位不到＝这条没有量纲） | 0/3（251/251 全中） | `blueprint --chapters` |
| `anchor_ambiguous` | **HARD** | 锚点在**定位器还要搜的那段**里只匹配到一处 | 0/3（502/502） | `blueprint --chapters` |
| `anchor_not_verbatim` | INFO | 锚点是原文字节。**期望恒为 0**（落盘前已回取）⇒ 改断言 `_provenance.verbatim_recovered` | 0/3 | `blueprint --migrate`（0 成本） |
| `verbatim_failed` | **HARD** ✚ | `_provenance.verbatim_failures` 非空即失败态。**原型没有这条**——它那边只有一次 `sys.exit`，账不落盘 | 0/3（键还不存在 ⇒ 今天落 `unver`） | `blueprint --chapters` |
| `edge_hole` | **HARD** | ≥30 字的未覆盖原文不得落在**第一拍之前或最后一拍之后** | 0/3（未覆盖区间全在中段） | `blueprint --chapters` |
| `beats_collapsed` | **HARD** | ≥300 字的章不得只有 ≤1 拍（锚用**契约自己的反例**，不跨书比） | 0/3 | `blueprint --chapters` |
| `beats_coarse` | **HARD** | 章级 字/拍 不得超本书中位数 3 倍（**同一本书内部**才可比） | 0/3（中位 143 / 169 / 138） | `blueprint --chapters` |
| **六、保真材料** | | | | |
| `hinge_cross_chapter` | **HARD** | hinge 引的不是**别的章**的原文（逐字无误但归错了章） | 0/3 | `blueprint --chapters` |
| `hinge_in_hole` | **HARD** | 锚点在本章原文里，却落在任何 span 都没覆盖到的空档——**漏抓的下游后果** | **1/3** ⭐ 三岁 `b07_05` | `blueprint --chapters` |
| `hinge_anchor_void` | **HARD** | 锚点在本章原文里**根本找不到**（是转述或编的） | 0/3 | `blueprint --chapters` |
| `hinge_drift` | **HARD** | `text` 在本 beat 的 span 原文内（转抄漂移：位置对、字节错） | 0/3（113 / 66 / 103 条全中） | `blueprint --chapters` |
| `hinge_cascade` | INFO | 锚不到是因为**本 beat 的 span 自己就没定位成功**——不是独立故障，别重复计数 | 0/3 | — |
| `hinge_unanchored` | **HARD** | 每条 hinge 都带 `starts_with`/`ends_with`（`text` 是代码取的，不是模型默写的） | 0/3（282/282 全带锚点） | — |
| `chapter_no_hinge` | **HARD** | 整章一条 hinge 都没有＝这一章的原文材料一条都没往下游走 | 0/3 | `blueprint --chapters` |
| **七、在场与叙述** | | | | |
| `time_layer_no_present` | **HARD** | 全书不得 0 条 `present`——**枚举合法 ≠ 分布合理**（实测某本 0/70） | 0/3（present 95% / 100% / 92%） | `blueprint --chapters` |
| `present_ids_dangling` | **HARD** | `present_char_ids` 都在 registry。⚠ 这是**解析器的后置条件**：违反它＝代码坏了不是数据坏了 | 0/3（130 / 148 / 173 个） | `blueprint --migrate`（0 成本） |
| `present_names_partial` | **HARD** | **混版探测**：不许一部分 beat 给了 `present_names` 而另一部分没给 | 0/3 | `blueprint --chapters` |
| `present_names_invented` | **HARD** | 在场者称呼全书原文里找得到（找不到＝模型凭空造了一个人） | 0/3（177 / 176 / 216 个） | `blueprint --chapters` |
| `present_continuity` | INFO | 相邻拍同地同层在场、本拍被点名却未列。⚠ **依赖 `place` 字面相等，系统性漏检** | **2/3**（5 / 7 / 0 处） | — |
| `present_ungrounded` | INFO | （章, 在场者）对在本章原文里有字面依据。**叙述者豁免且单独计数** | **1/3**（1/36 · 0/43 · 0/49）⚠ 见下 | — |
| `present_early_named` | INFO | 在场者不早于该角色在原文里被指名的章 | 0/3 | — |
| `event_early_name` | INFO | `event`/`summary` 里不写出本章尚未指名的角色名（**只查 `canonical`，排除通名**） | 0/3 | `blueprint --chapters` |
| `pronoun_surface_unattested` | INFO | `pronoun_surfaces` 照原文用字（第三人称交空数组合法） | 0/3（14 / 8 / 9 个全有据） | — |
| `narration_multi` | INFO | 一章内出现一个以上叙述声音时出声——报的是**本层不记段界**这个能力边界（`R-3.14`） | 0/31 ⚠ **零样本，合成反例必须跟上** | — |
| `scope_violation` | **HARD** | `_provenance.scope_violations` 非空即失败 | 0/3 | — |
| `event_empty` | **HARD** ✚ | 每一拍都有非空 `event`。**原型没有这条**——`R-3.13` 索要它，而它判在**本层**不判 stage4（留在 stage4 意味着要先付掉一次规划调用才发现这一层产坏了） | 0/3（251/251） | `blueprint --chapters` |
| `summary_empty` | **HARD** ✚ | 每一章都有非空 `summary`。同上 | 0/3（31/31） | `blueprint --chapters` |
| `repair_cache_policy` | **HARD** ✚ | 有 `repairs[]` 时 `cache_policy` 必须是 `refresh-write`。**原型没有这条**——`R-3.11` 索要它：定向重生成的 prompt 与整跑时那一批**逐字相同**，缓存必然命中，于是**一个字都没重新生成而它照样打印「已重生成」** | 0/3（盘上 3 条 `repairs` 恒 `refresh-write`） | — |
| **八、度量（无条件报出，`owner=None`）** | | | | |
| `narration_voices` | metric | 多叙述声音的章数＝`narration` 这一整维的**样本量** | 0/14 · 0/8 · 0/9 | — |
| `time_layer_mix` | metric | 叙述层分布（`time_layer_no_present` 的输入） | 88/5/0 · 67/0/0 · 84/2/5 | — |
| `place_moves` | metric | 转场点数。⚠ **系统性高估**：`place` 自由文本、粒度不稳（街上/街边） | 23 / 22 / 39 处 | — |
| `present_names_ambiguous` | metric | 称呼在 registry 里挂着不止一个人 → 未解析（**不猜**） | **1** / 0 / 0 | — |
| `present_names_unlisted` | metric | 原文里真有、只是 `address` 没收它。**这是 global 该修的，不是本层违约** | **14 / 14 / 24** | — |
| `present_names_pronoun` | metric | 在场者写成了代词。**代词按设计永远不在 registry 里**——既不是自造也不是漏收 | **15** / 0 / 0 | — |
| `present_names_late` | metric | 称呼要到本章之后才出现 → 已拦下。**这是收益不是违约** | 0 / 0 / 0 | — |
| `present_broad` | metric | 宽筛：`event` 点名了某角色而本拍未列它。⚠ 精度约 1/4，**分子分母不同量纲**（拍×角色 vs 拍） | 45 / 74 / 69 处 | — |
| `batch_density` | metric | 按**一次调用**分组的抽取量。逐章产物上说明「批与章是同一个东西」 | 14 / 8 / 9 次调用 | — |
| `context_scope` | metric | 这一趟抽某章时后面的章在不在 prompt 里。**必须常驻打印** | 3/3 恒 `{1, true}` | — |
| `span_coverage` | metric | 定位成功条数 / 覆盖率 / 每条字数分位 | 98% / 98% / 97% | — |
| `span_coverage_per_chapter` | metric | **逐章**覆盖率——全书一个数看不出哪一章塌了 | 最低 ch5 95% · ch4 96% · **ch7 90%** | — |
| `anchor_short` | metric | 内容字符 <6 的锚点计数。契约的「8~30 字」是**生成期指导**，此处不判违约 | 23/186 · 13/134 · 30/182（合 **66/502**） | — |
| `uncovered_holes` | metric | 未覆盖原文区间（位置 × 长度两维切分，**分到哪一类交人工**） | 2% / 2% / 3% | — |
| `hinge_density` | metric | 全书 hinges / 千字原文。⚠ **按字数归一不按拍数**——拍数是自由变量 | 9.3 / 6.2 / 7.8 条每千字 | — |

### 5.2 不进这张表的（**登记在这里，免得被当成漏项**）

**甲 · 取值面枚举归载体（2 条）** —— `contracts/schema/stage2.py` 文件头已立的规矩，
判准是**失败成本**：拒收＝整份响应作废＝再花一次钱，而那要在生成下一章之前就停住。

| 原型判据 | 本仓落点 |
|---|---|
| `time_layer` 取值合法 | `contracts/schema/stage2.py::Beat.time_layer`（`Literal[TIME_LAYERS]`） |
| `hinges kind` 取值合法 | `contracts/schema/stage2.py::Hinge.kind`（`Literal[HINGE_KINDS]`） |

⚠ `address.by` **不在这一档**：它的判定要查 registry（跨产物），`AddressBy` 那个正则只管形状。

**乙 · 来源与新鲜度归结构（2 条）** —— `_common.md` §四 已定「校验报告第一节恒是
新鲜度 + 幽灵边 + 指纹对照，**空也要报**」。

| 原型判据 | 本仓落点 |
|---|---|
| `产物来源`（产自另一份上游 / 无 `_provenance`） | `contracts/schema/report.py::ProvenanceSection` + `EdgeLine` |
| `引用可信度`（blueprint ↔ global 的挂钩状态） | `novel/prov.py::freshness()` 三态（`stale` > `unknown` > `fresh`） |

**丙 · 记账键的出口归 `ACCOUNTING`（1 条）** —— `_common.md` §九 已把它做成一张会炸的表。

| 原型判据 | 本仓落点 |
|---|---|
| `落盘前改写`（`address_by_fallback` / `address_surface_rewrites` 的账） | `novel/report/accounting.py::ACCOUNTING['stage2']`（今天是空元组，F5-5 填，出口见 §3.3） |

### 5.3 退役而不删（9 条，`RetiredRule`）

判准一律是「**在本仓结构上不可表示**」，不是「不重要」。
**退役而不删**（LESSONS 15）：删掉的话「没报违规」与「根本没在查」就再也分不开。

| 原型判据 | 为什么在本仓不可表示 |
|---|---|
| `address`（无 address 称谓表，旧契约 aliases/titles） | 新契约 `Character.address` 是必填且 `min_length=1` |
| `残留死字段`（`address.to_chapter` · 四张 `DEAD*` 表 · 七条 `DEAD_G` 点号路径） | `StrictModel` 的 `extra='forbid'`；存量那一侧由 `tests/corpus.py::CUT` 逐条钉着 |
| `narration 段界`（`last_beat_id` 的三条） | 该字段**已裁撤**（`CUT['stage2/chapter_blueprints.json']`），它唯一的读者 `segment_sizes` 随 split 一起退役 |
| `narration.last_beat_id`（零消费方声明） | 同上，字段已不在 |
| `beat 新字段`（`time_layer`/`place`/`present_char_ids` 整体缺失） | 三者在 `Beat` 上都是必填 |
| `批间离散`（批间 拍/千字 max/min > 2×） | 分批机制已随 F-100 整个删除，`BlueprintContext.batch_size` 新产物恒 1 ⇒ **前件恒不成立**。原型自己写着「一条恒不触发的 HARD 和一条恒亮的绿灯是同一个病」 |
| `span`（本产物无 `beat.span`，旧契约） | `Beat.span` 必填 |
| `role/fate`（characters 仍是旧的 identity 单字段） | `Character.role` / `.fate` 都是必填键 |
| `★跨批`（`end_hook.pays_to_chapter` 残留 / 产物无跨章引用） | `end_hook` 整字段已删；后一支是一句**恒真陈述**，不是判定 |

### 5.4 `fix` 那一列 —— 它今天在本仓没有落点

原型每条判据带一个**结构化的修复路由**，而不是一句散文：

```python
FIX_MIGRATE_BP = {'kind': 'n2_blueprint', 'extra': ['--migrate']}    # 0 成本
FIX_PURGE_ROLE = {'kind': 'n2_global',    'extra': ['--purge-role']} # 一次小调用（付费）
def fix_chapters(chs): ...   # {'kind': 'n2_blueprint', 'extra': ['--chapters', '2,7', '--retry']}
```

它的理由原型自己写着：「文案里那句『修法：…』是给人读的；**界面要执行它，就必须有一份
机器可读的同源表达**，否则只能去正则抠文案」。

本仓 `Rule`（13 字段）与 `Finding`（13 字段）**都没有这一格**——
`directive` 被注册期不变量 `bool(directive) == (repair != 'NONE')` 锁死在 stage5。

⇒ **用户裁决（2026-08-12）：`Rule` 加一格**，形态照 F4-1 加 `directive` 的先例。
上表「修法」那一列就是它的取值，F5-1 落地：

```python
fix: tuple[str, ...] | None = None      # ('stage2.blueprint', '--migrate')
```

配一条注册期不变量：**只能指向 `NODES` 里真实存在的节点、与那个节点 `flags` 里真实存在的 flag**。
收益是它**能过 `is_paid`**——界面据此把「0 成本修」与「要再花一次钱」画成两个颜色；
写进 `note` 就只是一段散文，拼错一个 flag 名不会有任何东西出声（LESSONS 第 11 条）。

### 5.5 五条要连基线一起读的

> #### ⚠ `role_fate_unverifiable`：三本里只有一本打得出这条 unver
>
> 它挂在 `if not leaky` 那一支上——`role_leaks_fate` 命中的两本**根本走不到它**。
> 于是「这一维没有确定性防线」这句话在最需要说的那两本上反而不说。
> 实测：词表 `('最终','后被','而死','下狱','被处死','走向','结局','沦为')`
> 在头牌上**命中 0/17 而人读真违规 6/17**（`char_009.role` 写「…**后成为**李宝珠的下属」，
> 差一个字避开了 `后被`）。
> ⇒ **本仓改成无条件产出**（同 F4-6 对 `name_surface_survey` 的处置）：
> `role_leaks_fate` 报绿灯或报红灯，都要在同一份报告里说清这道防线只挡转抄式。
>
> #### ⚠ `world_rule_names_person`：这道闸门在三本上**什么也没拦住**
>
> | 本 | 含人名的规则 | 最晚叫法的 `from_chapter` |
> |---|---|---|
> | 成年人 | 2 条（`宿主`/`系统`） | ch14（全书 14 章）⇒ 几乎全程扣住 |
> | 头牌 | 1 条（`夫君`） | **ch1** ⇒ 等于全程下发 |
> | 三岁 | 1 条（`念念`/`月老`/`沈念念`） | **ch1** ⇒ 等于全程下发 |
>
> ⇒ 判据必须**报出自己的基线**，否则「没报违规」与「闸门一条都没拦住」在报告上同形。
>
> #### ⚠ `secret_cold_reveal`：20/21 条 secret 没有埋点，而 `secret_setup_order` 三本全绿
>
> 「冷揭示」（据实留空，合法）与「被裁空」（产地把揭示章号抄进了埋点，落盘前被裁掉）
> 在字段值上**完全同形**。⇒ `secret_setup_order` 断言的是 `_provenance.setup_chapters_pruned`
> 那个计数，不是字段值——再断言字段值就是一条**永远为真**的判定。
> ⚠ 而 `setup_chapters_pruned` 这个键**盘上 3/3 本一份都没有**（证据列 `契·码`，
> 已登记进 `tests/corpus.py::ADDED`）⇒ 今天它只能落 `unver`，不能发绿灯。
>
> #### ⚠ `present_ungrounded` 不是幻觉检测器
>
> 三本 128 对里只报 1 条（成年人 `ch10 char_009(祈颜)`），**幻觉在场者 0 例**。
> 它实际抓的是两件别的事：① `address[]` 漏收了原文在用的叫法（绰号 / 无归属台词）
> ② **后见之明标注**（身份本章尚未揭示，而 `present_char_ids` 是逐拍下发给写手的
> ⇒ 那是一条剧透旁路）。也确有合法反例（整段用代词、`recall`/`hearsay` 的在场者本章不留字面）。
> ⇒ **判 INFO 不判 HARD**，且名字表**不许复用 `present_broad` 那份**——那份带 `len>=2` 过滤，
> 搬过来会把「唯一 surface 是『姐』」的角色报成找不到依据，那是**判据自己造的假阳**。
>
> #### ⚠ 41 条 HARD 里，盘上有真反例的只有 4 条
>
> `address_bare_pronoun`（1 本）· `role_leaks_fate`（2 本）· `hedge_words`（2 本）·
> `hinge_in_hole`（1 本）。**其余 37 条盘上 0 命中**——那不是「干净」，
> 是「那种失效模式在这三本上一次都没发生过」。
> ⇒ 用户裁决（2026-08-12）：**盘上 0 命中的 HARD 各配一份会让它红的样本**，
> 落点是 stage2 那一族测试（F5-5 才建）。⚠ 反例配得出来的前提是**判据吃 `dict` 不吃模型**
> ——一旦跑在已过 Pydantic 的对象上，「必填齐全」那类在结构上就构造不出反例。

### 5.6 判据 ↔ 需求

`contracts/rules.py` 要求每条判据声明 `requirement`（正则 `^R-\d+\.\d+[a-z]?$`——
`Q-x` 与裸 `R-2` 都不收），`tests/test_coverage.py` 双向查。逐条对下来
**R-2 十二行 + R-3 十六行接不住 75 条**，`docs/REQUIREMENTS.md` 本次
**补 17 行、拆 1 行、改判 1 行**：

| 新行 | 收哪几条判据 |
|---|---|
| `R-2.5b` INFO | `address_selfref` · `address_missing` · `address_unlocatable` |
| `R-2.6b` HARD | `distinguisher_lonely` |
| `R-2.9b` 度量 | `secret_cold_reveal` · `secrets_absent` |
| `R-2.9c` HARD | `secret_known_by_dangling` |
| `R-2.9d` INFO | `reveal_unmarked` |
| `R-2.11b` HARD | `world_rule_no_desc` · `world_rule_names_person` |
| `R-3.2b` HARD | `anchor_ambiguous` · `anchor_short` |
| `R-3.6b` HARD | `chapter_no_hinge` · `hinge_density` |
| `R-3.6c` HARD | `time_layer_no_present` · `time_layer_mix` |
| `R-3.7b` HARD | `present_names_partial` · `present_names_ambiguous` · `present_names_unlisted` · `present_names_pronoun` |
| `R-3.7c` INFO | `present_continuity` · `present_ungrounded` · `present_broad` |
| `R-3.7d` INFO | `present_early_named` · `event_early_name` · `present_names_late` |
| `R-3.14b` INFO | `pronoun_surface_unattested` |
| `R-3.16` HARD | `upstream_missing` · `section_id_leak` · `chapter_coverage` · `chapter_order` |
| `R-3.16b` HARD | `scope_violation` · `batch_density` · `context_scope` |
| `R-3.17` 度量 | `place_moves` |
| `R-3.18` HARD | `verbatim_failed` ✚ · `anchor_not_verbatim`（断言 `verbatim_recovered`）——见 §3.2 新增的三个键 |

**改判一行**：`R-3.6`（保真类型闭合枚举）`HARD` → **`载体`**，落点
`contracts/schema/stage2.py::Hinge`。理由是 `contracts/schema/stage2.py` 文件头已立的那条规矩
（取值面枚举归载体，判准是失败成本），而**那个文件今天就在仓里**——
所以这一行可以现在改，不必等模块（与下面那六行的处境相反）。
⚠ 「兜底分支不许静默通过」由 `Literal` 在结构上保证：没有 `dict.get(kind, 默认值)` 可写。
⚠ `载体` 行**不在** `test_structural_rows_that_name_a_repo_path_actually_point_at_it` 的扫描面里
（那条只扫 `结构`），所以本次可解析落点数不变。

**另外三行 `R-3.11` / `R-3.13` 不动**：它们照旧是 `HARD`，只是从「没有判据指着」变成
「由 ✚ 那三条指着」——需求文本一个字都不用改。

**拆行一处**：`R-2.11`（抽取判据本身）今天整行写着「无守卫 → `B-08`」，
而其中「规则描述必须连『怎么运作』一起写」现在**有判据指着**
⇒ 拆出 `R-2.11b`，留在 `R-2.11` 的是仍然没有守卫的那三项（只收结构性规则 /
秘密的四类收录判据 / 冲突阶段名要能定位）。**有判据的那一半与没有守卫的那一半必须分得开**，
否则 `test_no_rule_claims_a_requirement_marked_unguarded` 判它说假话。

⚠ **`R-2.10`（五个枚举）不拆**：枚举取值面归载体（§5.2 甲），它仍然没有判据 ⇒ 那一格照旧。
⚠ **`R-3.10`（`B-07` 那七项）不拆**：七项没有一项会有 stage2 判据。

⚠⚠ **`结构` 行本次一行都不改。** 还剩三行（`R-3.3` 点不下发 · `R-3.4` 跨段拆点 ·
`R-3.8b` prompt 必须明说章首）的落点在**本层的模块目录里，而那个目录今天还不存在**，
`test_structural_rows_that_name_a_repo_path_actually_point_at_it` 会去查它真实存在。
**指向一个还不存在的模块，与说不出是哪段代码在守，对读的人是同一件事。**
⇒ 留到 F5-5 与模块同批改（形态照 F4-0 对那六行的处置）。

⚠ 同一条纪律**本次已经真的绊到过一次**：`tests/test_docs_references.py::test_every_referenced_repo_path_exists`
把本文件里两处指向尚不存在落点的**反引号路径**当场判红。⇒ 本节一律写成散文
（「本层的模块目录」「stage2 那一族测试」），**等 F5-5 建出来再写成路径**。

---

## 六、已经查清楚、结论就是「查不了」的（登记在这里免得被当成漏项）

1. **`role` 里藏着中段才揭示的事实，确定性验不了**（`R-2.6` / `B-14`）。
   词表只抓「转抄式结局词」：实测一本上命中 0/17 而人读真违规 6/17，另一本 2/40。
   **它不是恒为 0，只是判别力低**——命中时照报失败是对的，但**「没报违规」在这一维上不等于「没问题」**。
   校验器为此常驻打印一条 `[????] role 不含结局`。
2. **`event` 面的跨章引用漏检。** `event` 是抽象概括，没有可锚定的原文区间，
   两种通用检测都已证伪。**只能从输入侧解**（逐章 + 只吃到本章为止，现已是默认且无旋钮）。
3. **`place` 不能当相等判断的键。** 自由文本、粒度不稳，凡按它相等做的推导
   （转场计数、场景派生、在场连续性）都在量措辞。
