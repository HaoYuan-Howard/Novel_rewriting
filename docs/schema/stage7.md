# stage7 产物 schema

人名替换与成书导出。**LLM 只产一份新人物表（决策），替换与组装全部是确定性代码，人定稿。**
全链最后一层，也是唯一产出交付物 `book.txt` 的一层。

```
stage7/name_map.json              LLM 产（两个键）+ 确定性展开 + 判据 —— 一本书一份，定长
stage7/_rounds/name_map.rN.json   归档：上一轮的整份拷贝
stage7/name_map_edit.json         ⚠ 不是节点产的，是【人产的输入】——被 `--apply-edit` 当决策集吃掉
stage7/book.txt                   ★ 交付物。纯文本，带不了 `_provenance`（§四）
```

对应需求 **R-10**（人名替换与成书）。
读法（证据列的取值、为什么三源合并）见 `_README.md`。
**载体 = Pydantic v2**（已定），但本文只定内容、不写模型——模型在骨架阶段照本文写。

> **样本面：盘上 3 本 = 3 份 `name_map.json` · 8 份归档 · 3 份人工编辑文件 · 3 份 `book.txt`
> （14/8/9 章，共 31 章）· 75 个角色（32 个改了名）· 72 条映射 · 97 条保留 · 21 条人工补充
> · 0 份 `--tag` 分叉。**
> 每本 **1 次付费调用**——与 stage6 并列为全链最便宜的层。三本 `--check-only` **全 PASS**。

---

> ## ⚠⚠ 七条贯穿本层的事实
>
> ### ① 模型只写两个键，其余全是代码算的
>
> `ask_json` 的响应只有 `surname_map` 与 `characters[]`。产物里另外那些顶层键
> ——`subs` / `kept` / `applied` / `residual` / `baselines` / `book` / `book_txt` ……
> **一个字都不经模型**。这是 `Q-7`（中间层只存决策与引用）在全链贯彻得最彻底的一层：
> 正文字节由 `apply_subs` 单次扫描搬运，模型连看都看不到正文。
>
> ⚠ 因此**本层的零消费审计比前几层可靠**：stage4 那类假阴（`writer_context` 整份 dump 进
> 写手 prompt，grep 报 0 下游而其实全在用）**在本层结构上不存在**——
> 下发材料由 `build_material` 从 registry 现造，**它不读 `name_map` 的任何一个字段**。
> 本层的下游只有确定性代码和人。
>
> ### ② 中文没有词边界 —— 这是本节点全部的风险，也是三道防线的由来
>
> | 防线 | 挡什么 | 判据 |
> |---|---|---|
> | 保留项进 alternation 当**恒等条目** | 更短的映射项把更长的保留项切成半截 | 无判据，**由构造保证**（`sub_table` 把 `kept` 也编进正则） |
> | 称呼最短 **2 字** | registry 没收的词（「大姐」「姐妹」） | `rn_surface_short`（HARD） |
> | 多主称呼一律否决 | 换掉别人的称呼 | `rn_shared_surface`（HARD）+ `rn_shared_surface_human`（INFO） |
>
> 第一道之所以必须是**结构性**的：实测某本 `char_011` 唯一的称呼是**单字「姐」**，
> 而「姐姐」在成稿里有 4 处。「姐」是**单主**，`rn_shared_surface` 抓不到它——
> 只剩一道**词表**防线，而判据 11 明说验收判据不该用词表。
> 把「姐姐」编进正则，这件事就与词表判得准不准无关了。
>
> ### ③ 现役的质量闸门是**人定稿**，不是代码（同 stage6）
>
> 「新名好不好、读起来串不串味」没有确定性判据（`rn_taste_unver` / `B-22`）。
> `--approve` 在还有 HARD 时**拒绝定稿**、`--assemble` 在四种残缺态下**拒写 txt**——
> 人被挡住，不是被静默放行。
>
> ### ④ 判据一条都不上移到载体，而本层的理由是**两个叠加**
>
> stage5 的理由是「拒收＝重写一整章＝最贵的重试」，stage6 的理由是「自救太便宜」。
> **本层两条同时成立**：拒收 ⇒ 本层**唯一那次付费调用**整个作废；
> 而 `--apply-edit` 收的是**完整决策集 JSON**，人可以直接把映射表改对，**0 成本**。
> ⇒ 31 条判据一条都不该变成类型。见 §六开头。
>
> ### ⑤ `rn_new_collide`（HARD）的名字与它的取值面对不上，而盘上正好有一次从缺口进来的违规
>
> 它判「新名全书唯一」，而取值面 `new_of_char` **只收模型写的 `new_canonical`**——
> `extra_subs`（人工补充）在它下面 30 行才被并进 `fwd`，**从不进 `new_of_char`**；
> `aliases[].new` 也不进。盘上（書1）：
>
> ```
> characters[char_009]  祈颜 → 叶翎        （模型写的）
> extra_subs            祈辕 → 叶翎        （人写的，origin=human）
> subs                  两条都在 · book.txt 里「叶翎」出现 16 次
> --check-only          [OK]  新名全书唯一，没有两个角色共用一个新名
> ```
>
> **这一次撞名恰好是对的**（`祈辕` 就是 `祈颜`，见 §1.4），
> **但「它是对的」这件事没有任何东西验证过**——判据结构上够不到那扇门。
> 而 `extra_subs` 恰恰是**唯一一条人手打新名、且没有模型的全局视野**的通道。
> ⇒ 处置照本层自己的先例（`rn_shared_surface_human`）：**人明写的照样执行，另落一条 info
> 报出它撞上了谁**。⚠ **不要直接把 `extra_subs` 并进 `rn_new_collide` 的取值面**——
> 那会让这条正确的人工裁决在盘上当场判红，而唯一的出路是撤销一个对的决定（判据 20 的标准信号）。
>
> ### ⑥ 判据表里有一条**全仓 0 行实现**，它每一本每一次都打绿灯
>
> `rn_unregistered`（info，「成稿里没有 registry 从没收过的疑似人名」）在整个仓库里
> `grep` 只有**一处**命中：判据表那一行本身。没有任何调用点。
> 而 `check_one` 的收尾循环给「没开过口的 hard/info 判据」补打 `[OK]`
> ⇒ 它**永远不在 `spoke` 里** ⇒ 每本书每一次都打印「成稿里没有 registry 从没收过的疑似人名」。
> ⚠ 更糟的是它在源码里的位置：被归在注释「**漏网：本节点唯一能发现『白名单不够』的东西**」
> 这一组下面，与 `rn_residual_stem` 并列——**读的人会以为漏网这件事有两道防线，其实只有一道。**
> ⇒ 见 §九 待决 ⑤。
>
> ### ⑦ 四条 0 成本写盘路全部重盖来源账 ⇒ 伪造（与 stage5/stage6 同族，本层是第三例）
>
> `--recheck` / `--approve` / `--apply-edit` / `--assemble` 走同一个 `save_name_map`，
> 它无条件盖上**当前**的 prompt 指纹与 inputs 指纹。⚠ `--approve` **根本不重判**
> （它只写一个 `{origin:'human'}`），照样盖新指纹。**铁证（書1）**：
>
> ```
> _rounds/name_map.r1.json   prompts.N9_SYS = 5a581a64ba
> name_map.json（当前）       prompts.N9_SYS = 8d4d309ad7
> characters[]                ← 逐字段相同
> ```
>
> `--apply-edit` 只往 `extra_subs` 里加了 4 条人工映射，`characters[]` 一个字没动
> ⇒ **那批新名只可能是 `5a581a64ba` 那版 prompt 产的，而当前产物声称的是另一版。**
> 盘上 11 份产物里 `N9_SYS` 取过 **三个**值，而每一份的 `characters[]` 都只可能出自它
> **被创建**那一刻的 prompt。
> ⇒ 处置同 stage5/stage6（`Q-5`）：**0 成本路不许重盖来源账**。
> 参照 `stage5.md` §五 那个正面例子——`--apply-edit` 在同一个文件里、相隔 30 行把这件事做对过。

---

## 一、`stage7/name_map.json`

顶层 **21 个键**（`hook_consumed` 是第 22 个，见 §1.5）。

| 字段 | 类型 | 必填 | 取值面 / 范围 | 证据 |
|---|---|---|---|---|
| `source_id` | `str` | 是 | 纯数字串，取自**目录名**（`derive_source_meta`）。⚠ **可 0 成本现算 ⇒ 该裁** | **三源** |
| `title` | `str` | 是 | 原书名，取自**目录名**（`title_of`）。⚠ `check_one` **不读它**，自己重算了一遍（源码注「书名只认目录名，不认命令行怎么敲」）⇒ 同一件事两个落点。**可 0 成本现算 ⇒ 该裁** | **三源** |
| `title_new` | `str` | 是 | 书名过一遍同一份替换器的结果。**盘上 3/3 与 `title` 逐字节相同**（三本书名都不含 registry 专名）。⚠ 见 §九 待决 ① | **三源** |
| `surname_map` | `obj` | 是 | **模型写的**。`{原姓: 新姓}`，只覆盖【字面同姓且 ≥2 人】的族群。盘上 8/3/9 条，**新姓 ∩ 旧姓 = ∅、新姓无重复**（3/3） | **三源** |
| `characters` | `obj[]` | 是 | **模型写的**（`old_canonical` 由代码从 registry 回填）。长度 = registry 大小，盘上 12/15/48。见 §1.1 | **三源** |
| `extra_subs` | `obj[]` | 是 | **人写的**补充映射。盘上 4~12 条、共 21 条。见 §1.2 | **三源** |
| `subs` | `obj` | 是 | 代码合成的最终替换表 `{old: new}`。盘上 15/24/33 条。见 §1.2 | **三源** |
| `kept` | `str[]` | 是 | **明确保留**的称呼——不改字节，但**必须进 alternation**（事实②第一道防线）。盘上 11/27/59 条 | **三源** |
| `applied` | `obj` | 是 | 三个替换面各自的记账。见 §1.3 | **三源** |
| `residual` | `obj[]` | 是 | 漏网候选。盘上 2/2/15 条。**顺序是承重的**，见 §1.4 | **三源** |
| `baselines` | `obj` | 是 | `{姓字: {total, covered, residual, ratio}}`。盘上 8/4/10 个姓字。**残留为 0 时也必须输出**（判据 31） | **三源** |
| `approved` | `obj｜null` | 是（键恒在） | 缺省 `null`；定稿后 `{origin: 'human'}`，**只有这一个子键、只有这一个取值**。没有 auto_approve | **三源** |
| `book_txt` | `obj｜null` | 是（键恒在） | 交付物的账。缺省 `null`（还没组装）。见 §1.7 | **三源** |
| `judged` | `obj` | 是 | `{成稿相对路径: 引用}`，盘上 31 份。见 §1.5 | **三源** |
| `hook_sha1` | `str｜null` | 是 | `stage6/hook.json` 的**整文件**指纹 | **三源** |
| `hook_consumed` | `obj` | **否** | 本节点**真正消费**的文案投影。⚠ **盘上 1/3 本有**——后加的键。见 §1.5 | **盘·码** |
| `sources` | `obj` | 是 | 只有一个子键 `writer_context`，形状与 stage6 §1.4 逐字段相同。盘上 3/3 `fresh` | **三源** |
| `book` | `obj[]` | 是 | 书级结论。盘上恒 **7 条**（1 info + 3 metric + 3 unver），三本**规则集完全相同**。见 §1.6 | **三源** |
| `counts` | `obj` | 是 | `{sev: 条数}`。⚠ **稀疏**：`Counter` 的 0 键消失 ⇒ 盘上 3/3 **没有 `hard` 键**。可 0 成本现算——但它与 stage5/6 同形，**一起裁或一起留** | **盘·码** |
| `_groups` | `obj[]` | 是 | 下发给模型的姓氏族群表（**键名是中文**：`原姓` / `成员`）。盘上 1/1/3 组、共 5 组 11 人。⚠ 加载回来 **0 读者**，见 §1.8 与 §九 待决 ③ | **盘·码** |
| `_provenance` | `obj` | 是 | 见 §五 | **盘·码** |

⚠ **已裁撤**：`_problems`（`build_subs` 攒下的冲突三元组，盘上 3/3 空）——
加载回来 **0 读者**，而且 `rename_findings` 已经把它逐条翻成 `book[]` 里的 Finding
⇒ **同一信息两个落点**。

### 1.1 `characters[]`

**模型写的那一半**（`old_canonical` 除外）。长度必须与 registry 一一对应——多一个少一个都是 `rn_llm_shape`（HARD）。

| 字段 | 类型 | 必填 | 取值面 / 范围 | 证据 |
|---|---|---|---|---|
| `char_id` | `str` | 是 | `char_\d{3}`，**必须 ∈ registry**（盘上恒 8 字符） | **三源** |
| `old_canonical` | `str` | 是 | **代码从 registry 回填**，不是模型写的。⚠ `--apply-edit` 的 patch **显式禁止改它**（`_CHARACTER_PATCH_KEYS` 不含它） | **三源** |
| `new_canonical` | `str` | 是 | `renamed=false` 时原样填旧名。盘上长度 1~8 | **三源** |
| `renamed` | `bool` | 是 | 盘上 32/75 为真 | **三源** |
| `not_renamed_reason` | `str｜null` | 是（键恒在） | **`renamed=true` 时必须是 `null`**（不是空串，不是缺席），由载体守——它是**代码回填**的位置，不是模型的自由度。见下框 | **⚠冲突** |
| `aliases` | `obj[]` | 是 | `[{old, new}]`。盘上 136 条，其中 **88 条 `old == new`**（＝这一条不换，是决定不是错误）。⚠ **canonical 不得在这里再列一遍**——实测 registry 的 `address` 重复收了 canonical，模型于是同时交出 `new_canonical` 与一条同名 alias，被判成「一旧名两新名」（`rn_ambiguous`） | **三源** |
| `reason` | `str` | 是 | 一句话说明新名为什么贴合。盘上 0~39 字；**空串 11 条，全部落在 `renamed=false` 的角色上**（那些角色的理由在 `not_renamed_reason` 里，由 `rn_no_reason` 查）。<br>⚠ **0 代码读者但不是孤儿**：界面渲染它，它是人定稿时唯一的判断材料 | **三源** |

> #### ⚠ `not_renamed_reason` 正是 `README.md` 那条 Pydantic 警告②的实例
>
> 「没有这个字段」「字段是 `null`」「字段是空串」在盘上**同时存在**
> （`renamed=true` 时盘上有 `null` 9 个与 `''` 23 个两种「没有」），
> 而 `rn_no_reason` 只查 `renamed=false` 的那一侧 ⇒ 另一侧的两种拼法**谁也没在看**。

### 1.2 `extra_subs[]` · `subs` · `kept` —— 人的补充、代码的展开、屏蔽面

**`subs` 与 `kept` 是 `build_subs` 的两个输出，一次遍历同时产出**（判定与展开在同一处发生，
分开写就会长出第二把尺子）。两者**保证不交**（`kept -= set(subs)`，换赢）。

| 字段 | 类型 | 必填 | 取值面 / 范围 | 证据 |
|---|---|---|---|---|
| `extra_subs[].old` | `str` | 是 | 人手写的旧字面。盘上 2~5 字 | **三源** |
| `extra_subs[].new` | `str` | 是 | 人手写的新字面 | **三源** |
| `extra_subs[].origin` | `str` | 是 | 盘上恒 `'human'`。它答的是「这条是谁加的」——`residual.suggest` 一旦有第二条来源，这个字段就是唯一能分开的地方 | **三源** |
| `extra_subs[].note` | `str` | 否 | 契约的例子里有，**盘上零样本、代码也不产**。⚠ 它能进产物只是因为**整条透传**，见下框 | **契·码** |
| `subs{old}` | `str` | — | 最终替换表。落盘顺序是「长的在前」，**但替换器不依赖它**（`sub_table` 自己按长度重排）⇒ 顺序是装饰 | **三源** |
| `kept[]` | `str` | — | 屏蔽面。盘上有**单字条目**（「姐」「哥」「妈」）——`MIN_SURFACE` 管的是替换面，不管屏蔽面，这是对的。<br>⚠ 它可由 `characters` 重算，但它是**第一道防线的载体**，重算等于把防线交给调用方 ⇒ **落盘** | **三源** |

> #### ⚠ `extra_subs` 是**人产的输入直接落进产物**，全链没有一处校验它的形状
>
> `merge_mapping_edit` 把 `edit['extra_subs']` 原样传出，`build_name_map` 原样 `list(...)` 落盘，
> `build_subs` 只读 `old`/`new`/`origin` 三个键。多写的键**静默搭车进产物**（`Q-1`）。
> ⇒ `extra_subs[]` 与 `name_map_edit.json` 都要**用载体收（`extra='forbid'`）**。
> 这一条**不违反「判据不上移」**——它挡的是**人手写的编辑文件**，
> 拒收的代价是人改一行，不是再花一次钱。

### 1.3 `applied` —— 三个替换面的记账

```
applied.chapters{"<output_ordinal>"}   {counts, chars_before, chars_after}
applied.hook                           {counts, chars_before, chars_after}
applied.title                          {counts}          ← ⚠ 少两个字段
```

| 字段 | 类型 | 必填 | 取值面 / 范围 | 证据 |
|---|---|---|---|---|
| `chapters{*}.counts{称呼}` | `int` | 是 | 该称呼在这一章**独立命中**并被换掉的次数。**恒等条目不计数**（屏蔽不算替换） | **三源** |
| `chapters{*}.chars_before` | `int` | 是 | 盘上 399~1967。⚠ 唯一读者是 `rn_report`（metric），见 §九 待决 ② | **三源** |
| `chapters{*}.chars_after` | `int` | 是 | 盘上 399~1968。同上 | **三源** |
| `hook.*` | 同上 | 是 | 盘上 `chars_before` 128~240 | **三源** |
| `title.counts` | `obj` | 是 | 盘上 3/3 **全空**。⚠ **没有 `chars_before`/`chars_after`**——三个平行块唯独它缺两个字段 | **⚠冲突** |

⚠ **`counts` 的口径不能用 `str.count`**：称呼之间大量嵌套，`count` 会把长称呼的字节
重复计给短称呼（实测「阿禾」`count`=4 而独立命中只有 **1**，另外 3 次在「阿禾姊姊」里）。
落盘的这份数来自**单次扫描的实际命中**，与下发给模型的 `aliases[].count` 同源。

⚠ **`applied` 整块都是 `(成稿, subs, kept)` 的确定性函数**，可 0 成本重算。留着的理由不是
「算不出来」，是它**记的是那一次替换的实况**——而成稿变了没有靠 `judged` 兜（`R-10.9`）。

### 1.4 `residual[]` 与 `baselines` —— 本层唯一能发现「白名单不够」的通道

| 字段 | 类型 | 必填 | 取值面 / 范围 | 证据 |
|---|---|---|---|---|
| `stem` | `str` | 是 | 单个姓字。**取值面 = 「原文有据的姓」∪「`surname_map` 的键」**（`R-10.6`，少了后者有盲区） | **三源** |
| `where` | `str` | 是 | `ch<N>` ｜ `hook` ｜ `title`。盘上见过 `ch1`~`ch11` 与 `hook` | **三源** |
| `hit` | `str` | 是 | 命中处起 **3 个字**（恒 3 字） | **三源** |
| `context` | `str` | 是 | 前后各 12 字（恒 24 字），换行已压成空格。**只影响可读性，不是门** | **三源** |
| `confidence` | `str` | 是 | `high｜low`。**`high` ⟺ `hit[1]` ∈ `家府氏宅门`**。⚠ 见下框 | **三源** |
| `unit` | `str｜null` | 是 | 建议替换对的左边（`沈家`）。没有下一个字时 `null` | **三源** |
| `suggest` | `str｜null` | 是 | 建议替换对的右边（`林家`）。`surname_map` 里查不到新姓时 `null`。**只为省一次敲字，不参与任何判定，绝不自动生效** | **三源** |
| `baselines{stem}` | `obj` | 是 | `{total, covered, residual, ratio}`。**分母 `total` 量在替换前**（判据 19：比率的分母要是被测过程改不动的量） | **三源** |

**`residual[]` 的顺序是承重的**，与 `subs` 相反：排序键是
`(confidence != 'high', -ratio, stem, where)`，而设计口径是「人从上往下读到覆盖率掉下去为止」。
⇒ schema 必须把顺序写成契约的一部分，不能当成装饰。

> #### ⚠ 只看正式产物会把 `confidence` 的取值面定成**只有 `low`**
>
> 3 份正式产物里 19 条残留**全部是 `low`**——`high` 的那些早就被人勾选、
> 走 `extra_subs` 修掉了，正式产物是**修完之后的状态**。
> 而 8 份 `_rounds/` 归档里有 **35 条 `high`**（`谈家生` / `沈家的` / `祈家唯` / `杜家开` / `苏府上` …）。
> ⇒ **凡是有「人修完再落盘」这条路的层，归档必须一起反推**（方法论已写进 `_README.md`）。

> #### ⚠ 覆盖率判别器会**反向**，而这条警告已经写在产物里了
>
> `ratio = (total − residual) / total`。**漏掉的形就是该姓的主用法时，分母里装的主要就是分子**，
> 比率自我压低——实测 `沈` 40% / `孙` 60% 落在「基本是普通词」那一侧，而两个都是**真漏网**。
> ⇒ 两个信号打架时**以 `confidence` 为准**（结构信号，不受这个反馈回路影响，实测 6/6 全真）。
> 本轮三本重跑：`沈`/`孙` 双双回到 **100%**（`R-10.6` 的修复在盘上验证过了）。

> #### ⚠ 残留探针捡回来的是**哪一类**东西 —— 一条被四处抄过的误读
>
> 旧文档（含 `REQUIREMENTS.md` `R-10.10` 下面那条注、界面常驻说明、运行时
> `rn_coverage_unver` 的文案）写着：「**她**改了名而**她的家人**原样留着」。**逐条复算，三处不成立**：
>
> | 文档说 | 实测 |
> |---|---|
> | 是「她」 | 是**他**——`char_009` 的 role 写着「谈默最小的兄弟，刚成年」 |
> | 是「她的家人」，另一个人 | **是同一个人**。`祈辕`「哥！她根本不是单纯的人！」「你才小！我十八了！」与 `祈颜`「哥！我就说她不单纯！」「我十八了！成年了！」——同一场戏、同一个人 |
> | registry 漏收了一个**角色** | 漏收的是**同一个角色的第二种写法**（原作自己把「颜」写成了「辕」）。registry 里含「辕」的条目：**0** |
>
> **结论不变，改的是它捡回来的是哪一类东西**：残留探针仍然是必需件（除它之外全链没有第二处
> 能发现这个字面），但它捡回来的是「**原作对同一个人有第二种写法/错字**」，
> 而不是「人物表漏收了一个人」——**两者的修法不同**（前者补一条同目标映射，后者要补整个角色）。
> ⇒ 这也解释了为什么事实⑤那次撞名是对的：两个字面本来就该指向同一个新名。

### 1.5 `judged` / `hook_sha1` / `hook_consumed` / `sources`

`judged` 的形状与 stage6 §1.3 **逐字段相同**（同一套理由：成稿一律裸 `json.load`，
因为 `read_json` 的 `input_key` 只取两级路径 ⇒ 反查拼不出路径 ⇒ 那条边**静默跳过、
永远显示「对得上」**。**一个假的匹配比没有记录更坏**）。

| 字段 | 类型 | 必填 | 内容 | 证据 |
|---|---|---|---|---|
| `judged{<相对路径>}.output_ordinal` | `int` | 是 | 盘上 1~14 | **三源** |
| `…​.round` | `int` | 是 | 那份成稿当时是第几轮。盘上 1~2。**不可再生** | **三源** |
| `…​.sha1` | `str` | 是 | **整文件**指纹，只作审计 | **三源** |
| `…​.consumed` | `obj` | **否** | `{output_ordinal, text_sha1, has_budget_chars}`。**盘上 1/3 本有** | **盘·码** |
| `…​.mode` | `str` | 是 | `deep｜shallow`。⚠ 可从键（路径中间那一段）现算 | **盘·码** |
| `hook_sha1` | `str｜null` | 是 | 整文件指纹 | **三源** |
| `hook_consumed` | `obj` | **否** | `{present: bool, approved_text_sha1: str}` | **盘·码** |
| `sources.writer_context.state` | `str` | 是 | `fresh｜stale｜unknown`。盘上 3/3 `fresh` | **三源** |
| `sources.writer_context.detail` | `obj[]` | 是 | `{key, was, now}`。**盘上 3/3 空——非空分支零样本** | **契·码** |
| `sources.writer_context.code` | `str｜null` | 是 | ctx 的 `_provenance.code` | **盘·码** |

⚠ **「文案过没过期」在本层是两把尺子**（与 stage6 §1.3 **完全同形**）：
`check_one._hook_drift` 用 `hook_consumed.approved_text_sha1`（缺席时退回整文件），
而**界面 `rename_view` 只用 `hook_sha1`**。盘上三本结论一致（零分歧样本），
但 2/3 本根本没有 `hook_consumed`。
⇒ 两个节点都长了两把尺子，移植时**合成一套：`consumed` 判过期、整文件 sha1 只作审计**。

### 1.6 `book[]` —— findings 形状

`pipeline_common.Finding`，与 stage5 / stage6 **逐字段相同**（同一个 namedtuple）：
`rule` · `sev` · `item`（肯定式，由判据表插值，调用点不许自己写）· `detail` · `evidence`。

盘上 `book[]` 恒 7 条，**三本的规则集完全相同**：

| sev | 条数 | 规则 |
|---|---|---|
| `info` | 1 | `rn_residual_stem` |
| `metric` | 3 | `rn_stem_noise` · `rn_report` · `rn_style_report` |
| `unver` | 3 | `rn_taste_unver` · `rn_coverage_unver` · `rn_title_unver` |

⚠ 本层的 `Finding` **没有** `directive`（stage5 反哺回环专用）、**没有** `chapters`/`fix`。
本层没有 LLM 返工回路：「重写」就是重跑一次这个节点，或人走 `--apply-edit` / `--approve`。

### 1.7 `book_txt` —— 交付物的账

| 字段 | 类型 | 必填 | 取值面 | 证据 |
|---|---|---|---|---|
| `sha1` | `str` | 是 | **回查盘上那份**的唯一依据（txt 带不了 `_provenance`） | **三源** |
| `chars` | `int` | 是 | 盘上 11105 / 11807 / 13308。⚠ **第三个 `chars` 口径**，见 §四 | **三源** |
| `chapters` | `int` | 是 | 盘上 8 / 14 / 9 | **三源** |
| `has_hook` | `bool` | 是 | 盘上 3/3 `true`。⚠ `false` 分支**零样本**——`--assemble` 在文案未定稿时就拒写了，**可能不可达**，见 §九 待决 ④ | **契·码** |

### 1.8 `_groups` —— 一个被顺手落盘的进程内中间量

下发给模型的姓氏族群表（`{原姓: [...], 成员: [{char_id, canonical}]}`，**键名是中文**）。
**加载回来 0 读者**：`rn_surname_split` 读的是同一次 `build_name_map` 里内存中的那份；
`--recheck` 会从 registry 重新算一遍覆盖它；界面不发送它。⇒ 见 §九 待决 ③。

---

## 二、`stage7/_rounds/name_map.rN.json`

**整份拷贝，与 `name_map.json` 逐字段同形**——实测 8 份归档与正式产物的顶层键集合
**完全相同**，唯一差异是三岁那本的正式产物多一个 `hook_consumed`（后加的键，归档时还没有）。

| | |
|---|---|
| 编号 | `rN`，`pipeline_common.next_round` 产。⚠ 旧实现是 `len(glob)+1`，**少一份归档就会重号并覆盖**，已修 |
| 什么时候写 | 裸跑（付费）覆盖前 · `--apply-edit` 前。**`--recheck` / `--approve` / `--assemble` 不归档** |
| 盘上 | 1 / 3 / 4 份 |
| 为什么归档而不是覆盖 | 它是**花过钱的产物**；而且人从此才有得比较「改了 prompt 之后新名是变好还是变坏」 |

⚠ **归档是本层 `confidence='high'` 分支的唯一样本来源**（§1.4）。
⚠ 归档的 `_provenance` 保留的是**那一次落盘时**的指纹，而正式产物的被后续 0 成本路盖过 ——
事实⑦那份铁证就是从这里比出来的。

---

## 三、`stage7/name_map_edit.json` —— 人产的输入，也是契约

不是节点产的，但它落在产物目录里、被 `--apply-edit` 当**决策集**吃掉 ⇒ 它的形状同样要定死。
**盘上 3 份，三本都用过这条路。**

| 字段 | 类型 | 必填 | 取值面 / 范围 | 证据 |
|---|---|---|---|---|
| `characters` | `obj[]` | 否 | **完整决策集**。盘上零样本（三本用的都是 patch 那条路）；**盘上没有旧映射时它是必填**——否则显式终态 | **契·码** |
| `character_patches` | `obj[]` | 否 | 按 `char_id` **浅合并**。盘上 2/3 份有，共 13 条 | **盘·码** |
| `character_patches[].char_id` | `str` | 是 | 必须 ∈ 现有 `characters`；**重复即报错** | **盘·码** |
| `character_patches[].{renamed, new_canonical, not_renamed_reason, aliases, reason}` | — | 否 | 可编辑的五个键。⚠ **`old_canonical` 不在白名单里**（它由 registry 派生），写了就显式终态 | **盘·码** |
| `surname_map` | `obj` | 否 | 缺席 = 沿用盘上那份。盘上 1/3 份有 | **盘·码** |
| `extra_subs` | `obj[]` | 否 | 缺席 = 沿用盘上那份。盘上 3/3 份有，共 21 条 | **三源** |

> #### ⭐ `character_patches` 是全链**唯一**把「定向返工只许改判据点名的部分」做成数据结构的地方
>
> 跨层规矩里那一条（`R-4.10`）在 stage3/stage5 都只是 prompt 里的一句叮嘱，
> **没有任何东西查「其余一字不动」**。这里不一样：不在 patch 里的角色**在结构上改不到**，
> 而 `_CHARACTER_PATCH_KEYS` 把 `old_canonical` 显式挡在外面。
> 它的由来也值得记：旧接口只有 `characters` 完整决策集，实测 48 人的映射表里唯一那条 HARD
> 只涉及一个角色的一条重复 alias，却要人复制其余 47 人的决定——**0 成本人工出路名存实亡**。
> ⇒ 移植时照搬这个形状，并把它当成 `R-4.10` 的实现范例。

---

## 四、`stage7/book.txt` —— 字节形态

**交付物**，不是审阅稿。里面没有地方写「缺章」抬头横幅。

```
<文案>
<空行>
1
<空行>
<第 1 章正文>
<空行>
2
<空行>
<第 2 章正文>
…
<末尾一个换行>
```

| 硬机制 | 实测（3/3 吻合） |
|---|---|
| **换行只有 LF** | `\r` 出现 **0** 次（写盘时 `newline='\n'`） |
| **章分隔＝裸阿拉伯数字单独成行**，不写「第 N 章」 | 裸数字行数 = `book_txt.chapters` = 8/14/9，且恰好是 `1..N` |
| **文案在最前**，其后每块之间恒一个空行 | 首字符即文案首字 |
| **没有任何审阅元信息**（不写书名、不写章标题、不写重合度） | — |
| **原子落盘**（写 `.tmp` 再 `os.replace`） | 并发读者读不到半截文件 |
| **四类残缺态一律拒写**：缺章 / 同章双份 / 混契约稿 / 文案未定稿 | 盘上 3/3 都是齐备态，**四条拒写分支零样本** |

> #### ⚠ `book_txt.chars` 是全链**第三个** `chars` 口径（判据 14）
>
> | 落点 | 口径 |
> |---|---|
> | `stage6/candidates[].chars`（已裁，改现算） | **去空白** |
> | `stage5/chNN.chars` | 含空白 |
> | `stage7/book_txt.chars` | 含空白，**并且含章号行与全部块间分隔符** |
>
> 精确公式（实测三本逐字吻合）：
> `chars = len(文案) + Σlen(各章正文) + 2×(2n) + Σ位数(1..n) + 1`
> 实测差额 76 / 41 / 46 = 分隔符 + 章号 + 末尾换行，一个字节不差。
> ⇒ **别拿它和另外两个 `chars` 做任何比较**。三个量在界面上会**并排出现在同一个人的视野里**
> （stage5 页 / stage6 页 / stage7 页）⇒ 移植时各带口径后缀。

⚠ **判死留在 CLI 调用点，`collect_chapters` 里不许 `sys.exit`**——它会被 `check_one` 经
Web 工作线程调用，而 `SystemExit` 是 `BaseException`，anyio 会把它抛进事件循环、
**一次「这本书还没写完」就杀掉整个服务**。这条是付过一次代价的。

---

## 五、`_provenance`

```jsonc
{ "prompts": {"N9_SYS": "…", "RULES8": "…"}, "code": "b371244",
  "inputs": {"stage1/main_text.json": "…", "stage2/global_grounding.json": "…",
             "stage4/plan_chapters.json": "…", "stage4/writer_context.json": "…"} }
```

| | 实况 |
|---|---|
| `prompts` | 两个键。`RULES8` 存的是**判据表的 `repr`**——判据改版会让指纹变，这是对的（判据 24） |
| `code` | git 短 sha。⚠ 盘上 11 份产物**全是 `b371244`**，而同一批的 `prompts.N9_SYS` 取了**三个不同的值** ⇒ **代码确实变过三次，而按 git HEAD 算的 `code` 一个字没动**。按内容算的指纹戳穿了它，而全链没有一层比对它们。<br>⇒ 与 `stage6.md` §三 是**同一条**（`provenance()` 在共享层）：改成 `git describe --always --dirty`，**处置只定一次，别按层分别裁** |
| `inputs` | 四条边。⚠ **本层没有一处校验它们**，见下 |
| **`model`** | ❌ **没有**。而这是一个**付费节点** |
| **`params`** | ❌ **没有**。`--max-tokens` 默认 16000，换了没有任何一处说得出来 |

⚠ 缺 `model`/`params` 与 stage6 **完全同形**——两个付费节点犯同一个错。
对照：`stage5/<mode>/chNN.json` 有 `model`。

> #### ⚠ `inputs` 记了四条边，而 `check_one` 一处都没在查
>
> 它的第 ① 步确实先报来源——但报的是 `sources.writer_context.state`，
> 也就是 `stale_inputs(ctx, novel_dir)`：**上游的上游**。
> 本层自己那四条边**没有任何一处调 `stale_inputs`**。**洞在哪里**：
> `stage2/global_grounding.json`（整份人物表的来源）改了之后——
> 若 ctx **没有**重建 ⇒ ctx 变 stale ⇒ 报出来（今天靠这条兜住）；
> 若 ctx **重建过** ⇒ ctx 是 `fresh`，而 stage7 记的那份 stage2 指纹已经过期，**没人看**。
> ⚠ 顺带记一笔：書1 的 ctx `inputs` 里有一条 `stage4/plan_chapters.INVALID.json` 的**幽灵边**
> （文件已不在盘上），`stale_inputs` 对它静默跳过——那是 stage4 已经登记过的账。
> ⇒ `check_one` 要补一行本层自己的 `stale_inputs`。

---

## 六、判据清单（**唯一落点**，31 条）

**31 条 = 15 `hard` / 7 `info` / 3 `metric` / 6 `unver`**。
owner：`stage7` 17 · `stage5` 3 · `stage2` 2 · `None` 9。
`actionable` **16 条，全部进 `REPAIR_FREE`——一条付费自救都不开**（同 stage6）。

> #### 判据 vs 载体：**一条都不上移**，而本层的两个理由叠加
>
> 模型只写两个键，可上移的取值面本来就只有 `renamed`（bool）与 `char_id`（∈registry）。
> 而这两条**都不该上移**：
> · 拒收 ⇒ 本层**唯一那次付费调用**整个作废（stage5 的理由）；
> · `--apply-edit` 收完整决策集，人直接改对，0 成本（stage6 的理由）。
> ⇒ 与 stage4 相反，本层**不给响应侧开 `extra='forbid'`**：模型多写一个键在这里不会坏事
> （`build_name_map` 只挑它认识的键），而拒收要再花一次钱。
> **但不许静默**——多出来的键要报一行。
> ⚠ 唯一该归载体的是**人写的编辑文件**（§1.2 那个框）：拒收的代价是人改一行。

| # | 判据 | sev | owner | 产地 | 盘上命中 | 需求 |
|---|---|---|---|---|---|---|
| 1 | `rn_llm_shape` 模型给每个角色都交出了决定与理由 | hard | stage7 | `rename_findings` | 0/3 | `R-10.13①` |
| 2 | `rn_map_coverage` 下发的每一个称呼在映射表里都有明确去向 | hard | stage7 | `build_subs` | 0/3 | `R-10.13②` |
| 3 | `rn_new_collide` 新名全书唯一 | hard | stage7 | `build_subs` | 0/3 ⚠**假绿灯**，见事实⑤ | `R-10.13③` |
| 4 | `rn_ambiguous` 每个旧称呼只映射到一个新称呼 | hard | stage7 | `build_subs` | 0/3 | `R-10.13④` |
| 5 | `rn_shared_surface` 被替换的称呼没有同时挂在多个角色身上 | hard | stage7 | `build_subs` | 0/3 | `R-10.3③` |
| 6 | `rn_shared_surface_human` 多主称呼只在人明写时才替换，且报出它还碰到了谁 | info | stage7 | `build_subs` | 0/3 | `R-10.3b` / `R-10.13b` |
| 7 | `rn_surface_short` 要替换的称呼都不短于两个字 | hard | stage7 | `build_subs` | 0/3 | `R-10.3②` |
| 8 | `rn_already_present` 每个新称呼在替换目标里出现 0 次 | hard | stage7 | `rename_findings` | 0/3 | `R-10.13⑤` |
| 9 | `rn_new_in_source` 每个新称呼在原作正文里也没有出现过 | info | stage7 | `rename_findings` | 0/3 | `R-10.13⑤` |
| 10 | `rn_title_dropped` 称谓与亲属后缀在新形里原样保留 | hard | stage7 | `rename_findings` | 0/3 | `R-10.13⑥` |
| 11 | `rn_kinship_touched` 通用亲属/敬称一个都没被替换 | hard | stage7 | `rename_findings` | 0/3 | `R-10.5` |
| 12 | `rn_surname_split` 同一姓氏族群的成员共用同一个新姓 | hard | stage7 | `rename_findings` | 0/3 ⚠见下 | `R-10.4` |
| 13 | `rn_overlap` 人工补充的映射之间没有跨界重叠 | hard | stage7 | `build_subs` | 0/3 | `R-10.13c` |
| 14 | `rn_residual_key` 替换后没有任何一条映射的键残留 | hard | stage7 | `rename_findings` | 0/3 | `R-10.2` |
| 15 | `rn_book_incomplete` 每一章都有且只有一份成稿 | hard | stage5 | `rename_findings` | 0/3 | `R-10.14` |
| 16 | `rn_mixed_contract` 成书的各章出自同一套契约 | hard | stage5 | `rename_findings` | 0/3 | `R-10.14` |
| 17 | `rn_txt_stale` 盘上的 book.txt 与产物记的指纹一致 | hard | stage7 | **只在 `check_one`** | 0/3 ⚠见下 | `R-10.9` |
| 18 | `rn_invalid_drafts` 没有被拦下的废稿需要排除 | info | stage5 | `rename_findings` | 0/3 | `R-10.14b` |
| 19 | `rn_residual_stem` 旧姓字在成稿里没有替换面之外的残留 | info | stage2 | `rename_findings` | **3/3** | `R-10.6` |
| 20 | `rn_unregistered` 成稿里没有 registry 从没收过的疑似人名 | info | stage2 | ❌ **无实现**（事实⑥） | 0/3 | — **（待决 ⑤）** |
| 21 | `rn_no_reason` 每个不改名的角色都说明了为什么不改 | info | stage7 | `rename_findings` | 0/3 | `R-10.13①` |
| 22 | `rn_kept_contains_mapped` 保留下来的称呼里没有夹着已经改掉的名字 | info | stage7 | `rename_findings` | 0/3 | `R-10.13d` |
| 23 | `rn_stem_noise` 〔度量〕新姓字在原文里另有多少次非人名用法 | metric | — | 无条件 | 3/3 ⚠见下 | `R-10.17` |
| 24 | `rn_report` 〔度量〕替换总账 | metric | — | 无条件 | 3/3 | `R-10.17` |
| 25 | `rn_style_report` 〔度量〕成书写法密度（正文／文案／原作） | metric | — | 无条件 | 3/3 | `R-10.17` |
| 26 | `rn_taste_unver` 新名的时代感与雅俗合适 | unver | — | 无条件 | 3/3 | `R-10.11`／`B-22` |
| 27 | `rn_coverage_unver` 替换面覆盖了正文里所有指称人物的字面 | unver | — | 无条件 | 3/3 | `R-10.10`／`B-21` |
| 28 | `rn_title_unver` 书名里的人名替换正确 | unver | — | 无条件 | 3/3 | `R-10.16` |
| 29 | `rn_not_approved` 有人给这份人物表定过稿 | unver | — | 只在 `check_one` | 0/3 | `R-10.11` |
| 30 | `rn_body_stale` 判的是照盘上这些成稿做的替换 | unver | — | 只在 `check_one` | 0/3 | `R-10.9` |
| 31 | `rn_hook_missing` 开篇文案已定稿，可以拼进成书 | unver | stage6→— | `build_name_map` | 0/3 | `R-10.14` |

> ### ⚠ 22 条 hard+info 里，盘上只有 **1 条**命中过；**15 条 HARD 全部 0 命中**
>
> `--check-only` 在每本书上打出 **21 行 `[OK]`**。跨层规矩「每新加一条 HARD 判据，
> 就要有一份会让它红的样本」在本层是 **0/15**。
> ⚠ **别把它读成「判据都是多余的」**——`--identity`（恒等基线）在三本上逐字节无损，
> 说明替换器与组装器确实是对的；而 `rn_residual_stem` 每本都在报东西，说明探针在工作。
>
> #### ⚠ `rn_txt_stale` 的红灯与绿灯会**同时打印**
>
> 它是 **hard**，但**从不进 `book[]`**——只在 `check_one` 第 ② 步现算，
> 而第 ④ 步的兜底循环给「没开过口的 hard/info」补打 `[OK]`，它**永远不在 `spoke` 里**。
> ⇒ txt 一旦被手改或映射变了没重拼，同一份报告里会**同时**出现
> `[FAIL] 盘上的 book.txt … 不符` 与 `[OK] 盘上的 book.txt 与产物记的指纹一致`。
> ⚠ 同族的 `rn_not_approved` / `rn_body_stale` 逃过一劫，只是因为它们是 `unver`
> 而 ④ 只覆盖 `hard`/`info`——**这是运气，不是设计**。
> ⇒ ④ 的循环要排除「不由 `book[]` 承载」的那三条，或让 ② 把结论写进 `spoke`。
>
> #### ⚠ `rn_stem_noise` 的「新姓字」里混着不是姓的字
>
> ```python
> new_stems = sorted({n[0] for n in subs.values() if len(n) >= 2})
> ```
>
> 它假定「新值的第一个字是新姓」。实测这个假设在三本上都破了：
> `姓苏的→姓容的` 取到 `姓`(3 次) · `东街张媒婆→东街薛媒婆` 取到 `东`(**12 次**) ·
> `胭脂铺赵婶→胭脂铺程婶` 取到 `胭`(3 次) · `言言→晴晴` 取到 `晴`(0 次)。
> 于是报告写着「新姓字在原文里的非人名用法次数：`{'东': 12, …}`」——**`东` 与任何新姓都无关**。
> 它只影响一条 metric，但**名字叫「新姓字」，读的人就会按这个名字去解释这些数字**（判据 32）。
> ⇒ 取值面改成 `surname_map` 的**值**（那才是新姓），叠字小名与描述性称呼不进这个统计。
>
> #### ⚠ `require=` 漏声明 `surname_map`，而它缺席会**静默关掉一条 HARD**
>
> `N9_SYS` 明写输出两个键，而 `require=('characters',)` 只声明了一个。`surname_map` 缺席时：
>
> | 后果 | 机制 |
> |---|---|
> | `rn_surname_split`（HARD）**恒不触发** | `smap.get(s)` 全 `None` ⇒ `news` 恒空 ⇒ `len(news) > 1` 恒假 |
> | `R-10.6` 的探针盲区**原样回来** | `stems` 少了 `surname_map` 的键那一半 |
> | `rn_kinship_touched` 变严 | `_surs` 空 ⇒ `is_generic_appellation` 的姓氏豁免失效 ⇒ 「孙大娘」这类重新被误判 |
>
> 三处都靠 `model_out.get('surname_map') or {}` 兜底 ⇒ **一条违约数据的静默通行证**（判据 26）。
> ⇒ 应为 `require=('characters', 'surname_map')`，并把三处兜底改成显式终态。
>
> #### ⚠⚠ 恒等基线（`--identity`）**跑完把结论扔掉了**，而 stage6 在同一道工序上做对了
>
> 它是本层**最该先点的一条**（替换器在恒等下不逐字节守恒时，后面每一分钱都白花），
> 标着 `R-10.7`。而它的整个输出是 `print(...)` + 返回码——
> **`name_map.json` 里没有任何一个键记得它跑过没有**，`check_one` 不知道、界面不知道、
> `_provenance` 不知道。**「跑过且逐字节无损」与「从没跑过」在盘上长得一模一样**（`Q-2`）。
>
> **对照 stage6**：`hook.json` 里有 `baselines_identity`，落在产物里、进界面、进报告——
> 而正因为它有落点，才有人回头去读它，才判死过两条坏判据。
> ⇒ 结论要落进产物（照 `baselines_identity` 的先例），`check_one` 出一行，
> **没跑过时要说「没跑过」，不是不打印这一行**。
> ⚠ 落点里必须带**它验不了什么**（§十那三样），否则下一个人会把绿灯读成「替换是安全的」。

### 6.1 判据 ↔ 需求

上表末列是对应关系。补齐之前缺的是成建制的两族：

| 缺的族 | 条数 | 说明 | 补成 |
|---|---|---|---|
| **映射表自身的结构** | 10 | `rn_llm_shape` · `rn_map_coverage` · `rn_new_collide` · `rn_ambiguous` · `rn_already_present` · `rn_new_in_source` · `rn_title_dropped` · `rn_overlap` · `rn_no_reason` · `rn_kept_contains_mapped`。<br>它们全是 `N9_SYS` 六条规则的**后置条件**，而那六条规则在 `REQUIREMENTS.md` 里一个字都没有——`R-10` 从前只写了替换器与探针，没写「模型该交出什么」 | `R-10.13` + `13b`/`13c`/`13d` |
| **成书的前置条件** | 3 | `rn_book_incomplete` · `rn_mixed_contract` · `rn_invalid_drafts`。`R-10.8` 只规定了 txt 的**格式**，没规定**什么情况下不许写** | `R-10.14` + `14b` |
| 三条 metric | 3 | 尤其 `rn_style_report`——**文案那一列全链只有这里量** | `R-10.17`（`度量` ×3） |
| 书名替换 | 1 | `rn_title_unver`。它是用户裁决，而需求文档里没有 | `R-10.16`（`度量`） |
| 恒等基线 | — | `R-10.7` 在，但它的结论没有落点（见上） | `R-10.7b` |

⚠ **不是判据多余，是需求漏写。** 最刺眼的一条：**「新名彼此不得重名、不得与任何一个原作的
名字相同或近音」**——它在 `N9_SYS` 规则 5 里写得清清楚楚，有三条 HARD 判据在守
（`rn_new_collide` / `rn_already_present` / `rn_new_in_source`），而需求文档**曾经 0 处落点**。
⇒ `R-10` 从 **14 行补到 22 行**（同 stage6 那轮 `R-9` 从 8 补到 14）。

### 6.2 「谁在查」那一列 —— 本层**不需要**第九个取值

取值面已一次定全（`WHO_CHECKS` 八个：
`HARD`/`INFO`/`GATE`/`METRIC`/`CARRIER`/`STRUCTURAL`/`DELEGATED`/`N_A`）。
本层有 6 行标着 `HARD` 而 31 条判据里没有一条在查它们——逐条过一遍：

| 需求 | 该写什么 | `STRUCTURAL` 要求的代码落点 |
|---|---|---|
| `R-10.1` 只读上游、绝不回写 | **`STRUCTURAL`** | 本模块**没有任何一处**往上游目录写；产物只落 `stage7/` |
| `R-10.2` 单次 alternation，级联在结构上不可表示 | **`STRUCTURAL`** | `apply_subs` 的单次 `pat.finditer`（游标不回头）；`rn_residual_key` 是它的后置条件之一 |
| `R-10.3①` 保留项进 alternation 当恒等条目 | **`STRUCTURAL`** | `sub_table` 把 `kept` 与 `subs` 合成同一张表、按长度倒排 |
| `R-10.6` stems = 有据姓 ∪ `surname_map` 键 | **`STRUCTURAL`** | `build_name_map` 里那一行并集 |
| `R-10.6b` 修探针不修下发面 | **`STRUCTURAL`** | `_attested_surname` 只被 `family_groups` 调，`stems` 另走一条路 |
| `R-10.7` 恒等基线逐字节守恒 | **`INFO`/`METRIC`**（落点补上之后） | 它不是一条无法表达的守卫，它是一条**跑完就把结论扔掉**的守卫 —— 真正的修法是让它有落点，见 §六那个框 |

---

## 七、几条不进判据表的硬机制

1. **`_rename_all` 不能复用，这是一条论证不是偏好。** `n4_plan._rename_all` 的安全性由构造保证——
   它的键是**在原文里出现 0 次**的自造标签，任何位置替换都不可能破坏真词。
   **真名替换的键是原文里真有的词，这个论证整个不成立。** 所以另起 `apply_subs`。
2. **姓氏族群的判据只有「字面同姓且 ≥2 人」，relations 那条边已删。** 逐条复算：某本的姐妹
   本就字面同姓（字面那条已抓到）；某本的兄弟在 relations 里**根本没有边**（只有字面抓得到）；
   而谓词词表会把「是妻子」「是结拜兄弟」算成合并边，把三个姓并成一族 ⇒ 夫妻同姓、
   结拜兄弟同姓，**两样都错**。一条只造成错误合并、从未独立抓到过东西的边，删掉是化简。
3. **「≥2 人」同时兜住假姓。** `采草贼`→「采」、`府医`→「府」、`圣上`→「圣」全能过
   `_attested_surname` 的三道过滤，但它们**全是单例**——一个人不需要跟谁协调姓氏。
   ⚠ 单例**照样改名**，只是不进 `surname_map`（`李嬷嬷`/`王婆` 带的是真姓）。
4. **不做 `<姓>家` 的自动派生。** 实测 6/6 全真，仍然不做：「不允**许家**人」会被换成
   「不允苏家人」。只标 `high` 排最前，处置永远是人勾选。
5. **不落改名后的逐章正文。** 它是 `(成稿, 映射)` 的确定性函数，落第二份就是第二个落点。
   界面的前后对照**现算**。代价要记着：txt 因此带不了来源账（§四、§五）。
6. **本节点没有剧透闸门，这是对的不是疏漏。** stage6 的三处收窄是为了保护**下游模型**；
   stage7 的下游只有确定性替换器和人。不发 `fate`/`secrets`/`arc`/正文的理由只有判据 1
   （改名不需要知道结局）与噪声，**不是**剧透。**写明它，否则下一个人会来「修」一个不存在的洞。**
7. **不重起书名**（用户裁决）· **不建 `stage7_common`**（只有一个节点，共享层保持薄）·
   **不产有向称呼表**（现役管道没有消费方，那是抢跑）。

---

## 八、零样本分支

盘上一次都没走过的，**别把它们读成「安全」**：

| 分支 | 为什么零样本 |
|---|---|
| 四类拒写 txt（缺章 / 同章双份 / 混契约稿 / 文案未定稿） | 三本都是齐备态 |
| `book_txt.has_hook = false` | 上一条挡在前面 ⇒ **结构上只有「拒写」或「有文案」两种结局**，可能**不可达**（待决 ④） |
| `residual[].confidence = 'high'` **在正式产物上** | 人已经修完了；样本只在 `_rounds/` 里（35 条，§1.4） |
| `approved = null` 之后再 `--check-only` | 三本都已定稿 |
| `sources.writer_context.state ∈ {stale, unknown}` | 三本 ctx 都 fresh |
| `name_map_edit.characters`（完整决策集） | 三本用的都是 `character_patches` |
| `judged[].consumed` / `hook_consumed` 在场 | 各只有 1/3 本，而两个消费方各用一把尺子（§1.5） |
| `--tag` 分叉产物 | 从没用过 |
| **15 条 HARD 的红灯** | 全 0 命中 ⇒ **0/15 有反例样本** |

---

## 九、待决

| # | 决策 | 建议 | 代价 |
|---|---|---|---|
| ① | `title_new` + `applied.title` 裁不裁 | **裁**。`title_new = apply_subs(title, subs, kept)`，现算 0 成本；`applied.title.counts` 盘上 3/3 空 | `rn_title_unver` 的文案改成现算；**产品语义不变** |
| ② | `applied.chapters{*}.chars_before/chars_after` 裁不裁 | **裁**。唯一读者是 `rn_report`（metric），而它要的两个总数可以直接写进 finding 的 detail | 逐章篇幅变化从产物里消失；但替换不改篇幅（3/3 都是 1.000×~1.001×），信息量本就接近 0 |
| ③ | `_groups` 裁不裁 | **裁**，并把「N 个族群 / M 人」并进 `rn_report` 那一行 | 失去「当时下发给模型的族群表长什么样」的记录；⚠ 它可由 registry + 原文确定性重算，且 `_provenance.inputs` 记着 registry 的指纹 |
| ④ | `has_hook=false` 是否可达（§八） | **先查再裁**：可达 ⇒ 留；不可达 ⇒ 删字段（`op` 先例：一个恒等于常量的字段） | 这是**产品语义**问题：「允不允许出一本没有开篇文案的书」 |
| ⑤ | `rn_unregistered`：补实现还是退役（事实⑥） | **退役 + 标注**，直到有 ground truth 能定阈值（同 `B-01` 的裁法）。⚠ 有一个**弱版本**是可实现的：`residual_scan` 已经在做掩码，把「掩码后剩下的连续 2~3 字专名形」单独报一族即可——但那需要 ground truth 才能定阈值 | 退役后那盏绿灯消失，报告里换成一行「这一类没在查」——**信息量增加** |

---

## 十、已经查清楚、结论就是「查不了」的

登记在这里免得被当成漏项。

| 项 | 为什么查不了 |
|---|---|
| 替换覆盖率（`R-10.10`／`B-21`） | 只能替换人物表里有的称呼，**人物表够不到的这一层结构上够不着**。残留探针只是候选探针，不是覆盖率证明 |
| 新名的时代感与雅俗（`R-10.11`／`B-22`） | 没有结构不变量。现役闸门是**人定稿** |
| 名字之间的结构关系（`R-10.12`／`B-12`） | 「同辈共用排行字」「兄弟只差一字」——原作有没有做这个文章本身就没有判据 |
| `--identity` 验不了的三样 | 正向对**真名**安不安全（占位符天然无歧义，真名不是）· 漏网覆盖率 · 新名好不好。<br>⚠ **别把它跑绿了读成「替换是安全的」**——那三样由 pytest 的对抗性合成映射覆盖，不由这条 CLI |
