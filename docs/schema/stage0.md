# stage0 产物 schema

非正文剔除 + **风险分层审批**。**这一层不调 LLM。** 对应需求 `R-0.1` ~ `R-0.14`。

读法（证据列的取值）见 `_README.md`。
**载体 = Pydantic v2**（已定），但本文只定内容、不写模型——模型在骨架阶段照本文写。

> ## ✅ 本层实现已落地
>
> 这里的 **`盘·码`** 指本仓实现生成并复读的盘上证据；
> **`三源`** 仍只用于原本就有三源证据的字段。

三类持久载体（2026-08-22 阶段 C 从六类收到三类）：

```
stage0/revisions.json        **本层唯一可变的那一格**：不可变 revision 链 + current
                             指针 + 待裁决候选 + 审批状态（v1）
stage0/clean_text.txt        删掉非正文之后的全文。**stage1 的唯一输入**
stage0/removal_report.json   current 的物化投影：内容 + 审批（v3）
```

> ### ⚠ 后两份是**投影**，不是权威
>
> 它们的路径、字节形态、来源账的键与 `root_clean_sha1` 一个字没动（stage1~7 零改动），
> 但「它们是不是最新的」不再靠跨文件事务保证：任何时刻按 `revisions.json`
> 重新物化一次即可（`novel/stages/stage0/revisions.py::materialize`，幂等纯函数）。
>
> **阶段 C 同批下线三份载体**：`machine_snapshot.json`（机器那一轮的完整快照）·
> `merge_candidate.json`（base/ours/theirs 三份全文 + 码点级 diff3 的 typed 冲突）·
> `_audit/edit_N.json`（SHA-256 串联的决定账，同时充当跨文件提交的 WAL），
> 外加 stage0 的 `_rounds/` 逐轮归档。四套机制守的是**同一件事**——
> 「两份 live 产物必须同进同出，而文件系统只保证单文件原子」。
> 一次提交只写一份文件之后，那件事本身不存在了。

---

> ## ⚠ 三条贯穿本层的事实
>
> **① 番外体在这里被真正删掉，而且只在这里。**
> `clean_text.txt` 里没有它，产物目录里也没有第二份副本（`R-0.2`）。
> **原始 txt 仍在 `Novel_data`，番外在那里面**——「彻底抛弃」说的是**产物目录**不留副本。
> 审阅屏读的就是原始 txt。
>
> **② 本层是唯一允许人改正文的地方。**
> 下游 `main_text.json` / `hook_copy.json` 只读钉死（`stage1.md` 事实 ②）。
> 人要修剔除的结果必须回到这一层改——**这是有意的单点**：
> 编辑发生在上游，stage1 及其后仍是确定性纯函数，一改就全链重跑。
>
> **③ 未批准 = 不能往下走，而批准只有一个诚实来源：人**（`R-0.3a` / `R-0.3b`）。
> 批准必须由人显式给出 actor 与 reason，记为 `approval.origin = human`。
> 「零命中自动批」（`auto_zero_hit`）已于 **2026-08-21 停产**：零 removed、零 warning、
> clean == source 恰好是纯数字番外盲区的形状——那正是这道闸门要拦的东西，
> 自动放行等于宣称「人工是唯一防线」的同时把它拆了。**存量自动批产物只读不承行**：
> 读得、展示得、审计得，但不具执行资格（`legacy_auto_approval_requires_review`），
> 人工重批（`stage0.approve`）后恢复——批准写在 `current.approval` 里，带 `at` / `by` / `reason`。
> 「**不满足执行资格时 stage1 拒绝启动**」是 `HARD`——**它必须有判据**。
> 压在一条需求上标 `人`，正向覆盖率就不会要求它有任何判据，
> 而按本层的设计，这道闸门是盲区①（番外用纯数字章节标记写）**全链唯一的防线**。
> stage2 是第一个花钱的层，闸门必须在它之前、且必须有人真的看过。

---

## 一、`clean_text.txt`

**不是 JSON。它的 schema 是字节形态。**

| 性质 | 约定 |
|---|---|
| 编码 | 恒 `utf-8`，**无 BOM** |
| 换行 | 恒 `\n`（`R-0.12` 的归一化口径，`v1:bom+crlf`） |
| 内容 | **机器那一趟**：原始 txt 归一化后删去全部非正文段，其余**一个字都不改**——只删段，不做规范化、不修错别字、不统一异体人名（那是 stage2 的实体消解，见 `docs/BACKEND_CONTRACT.md` 的 `R-1` 节）。<br>**人那一趟**：可以再删段、也可以**直接改正文**（`R-0.4`）——判据漏掉的那类只能这么修 |
| 保留 | 开篇文案段 · 全部正文章 · 章节标记行 · **完结标记行** |

> ⚠ **完结标记行留给 stage1 处理，本层不删。** 它不是非正文体，是切点标记——
> 两条不同的路（`END_MARKER_WORDS` 与 `NONBODY_KINDS` 不许有交集，见 `contracts/invariants.py`）。
> 本层删了它，stage1 的 `R-1.14` 就永远查不到东西，变成一条恒真判据。

> ⚠ **代价：`clean_sha1` 的差异不可分解。** 人那一趟能改字，于是「删了番外」与「人改了字」
> 混在同一个指纹里。三条连带：
> ⇒ 审阅屏算 diff **只能拿链上的上一条 revision 比**，不能拿 `source ↔ clean` 比；
> ⇒ `revision_origin` 因此是**唯一**能一眼看出「这一版是怎么来的」的那一格，不许省
>   （⚠ 它替掉了 2026-08-22 阶段 C 之前的 `edited_by_human`：那一格是
>   `origin == 'human_edit'` 的同义反复，而且说不出「人**采纳了机器新结果**」
>   与「人自己改的」之间的差别——两者的正文含义完全相反）；
> ⇒ 逐条记账（`text_edits[]`：`span` + before/after）**本轮不做**，登记为待办
>   （⚠ 这里原本写「登记为 §四 #5」，而当时全文只有三节——那个编号从来就指不到东西）。

---

## 二、`removal_report.json`

### 2.1 指纹与身份

| 字段 | 类型 | 必填 | 取值面 / 范围 | 证据 |
|---|---|---|---|---|
| `original_filename` | `str` | 是 | 含平台前缀与扩展名 | **码** |
| `source_ref` | `obj` | 是 | **判别联合**（按 `kind`）：`external_file{absolute_path}` ｜ `managed_upload{blob_sha256, filename}`。**文件被移走时是「不知道」，不是「不匹配」**，两支处置相反，见 §2.1 注 | **码** |
| `code` | `str` | 是 | 本轮写报告所用代码版本；本层没有 `_provenance`，故落在顶层 | **盘·码** |
| `normalized_source_sha1` | `str` | 是 | **原始 txt 归一化之后那段文本**的 sha1（⚠ **不是文件字节的 digest**，2026-08-22 从 `source_sha1` 改名，见下）。追溯用 | **码** |
| `clean_sha1` | `str` | 是 | **`clean_text.txt`** 的 sha1。**这才是 stage1 记的那个输入指纹** | **盘·码** |
| `machine_normalized_source_sha1` / `machine_clean_sha1` | `str` | 是 | 本轮机器快照的 source/clean 身份；人工正式稿可与 machine clean 不同 | **盘·码** |
| `machine_snapshot_sha256` | `str` | 是 | 完整 `machine_snapshot.json` 的内容身份；三方合并的 base 绑定到这一值 | **盘·码** |
| `source_encoding` | `str` | 是 | 五候选 `utf-8-sig` / `utf-32` / `utf-16` / `utf-8` / `gb18030` 之一。⚠ 全语料 163 本**只出现过 `utf-8`，另四个零样本**——取值面只能从代码来，样本再多也补不出没发生过的分支 | **码** |
| `text_norm` | `str` | 是 | 恒 `v1:bom+crlf` | **码** |

> #### ⚠ 两个指纹是 `R-0.6` 的全部内容
>
> 只记一个，**「有人换过原始小说」和「有人改过剔除结果」就分不开了**——
> 而这两件事的处置完全相反：前者要从头重跑，后者**是人有意为之、必须保住**。
>
> | 变了的是 | 说明 | 处置 |
> |---|---|---|
> | `normalized_source_sha1` | 原始 txt 被换/被改 | 全链重跑（`R-0.14` + stage1 的 `R-1.3`） |
> | `clean_sha1`（`normalized_source_sha1` 未变） | 人编辑过剔除结果 | **保住**，上一版留在 revision 链上不可覆盖（`R-0.5`） |
> | 两个都变 | 换了原文又编辑过 | 说清楚是两件事，别合并成一条「陈旧」 |

> #### ⚠ `normalized_source_sha1` 为什么叫这个名字（2026-08-22 从 `source_sha1` 改名）
>
> 它算的是**解码 + 去 BOM + CRLF→LF 之后**那个串（`text_norm` 恒 `v1:bom+crlf` 说的
> 就是这道工序），**不是原始文件字节的 digest**。⇒ 同一本书存成 UTF-8 和存成
> GB18030，文件字节完全不同而这一格**相同**——那是对的、也正是它该有的语义，
> 但拿旧名字去核 `sha1sum 原文.txt` 的人会以为对不上是出了问题。
> 五个派生名同批改：`expected_` / `machine_` / `theirs_` / `before_` / `after_`。

> #### ⚠ `source_ref` 的两支，「找不到」的含义**相反**（2026-08-22 从 `original_path` 换成判别联合）
>
> | 那一支 | 它记什么 | 找不到时 |
> |---|---|---|
> | `external_file` | 一条书目录之外的绝对路径 | 文件**真的**不在了 ⇒ `ghost`／不可用，**正确**。契约有意接受，补偿是两个 preview 残片 |
> | `managed_upload` | `blob_sha256` + `filename`，**不记路径** | 位置由**当前 base** 现算（`<base>/_service/uploads/<blob_sha256>/<filename>`）⇒ Output 根搬家对它没有影响；真找不到才是真没了 |
>
> **病不在重锚逻辑，在「它必须去猜」。** 上一版一个字符串同时表达两种来源，读取侧只好
> 「先试记录的路径、断了再猜它是不是上传目录那种形状」。那套回落救回过一本
> （《夫君与鲛人孰》：记的是旧 base，文件就在当前 base 的同一个内容地址目录下、
> 字节逐一相等，而 `state` 报 ghost、复跑直接 `UpstreamMissing`），但它**只能靠猜**。
> ⇒ 「它是哪一种」改由**产地当时判一次**并写进产物（`novel/core/source.py::source_ref_for`，
> 唯一落点）。读取侧每一支只有一条路，**没有"回落"这回事了**；`managed_upload` 的候选
> 仍须自证 `sha256(bytes) == blob_sha256`（那是「永远不可能把另一份文件冒充成原文」的
> 全部依据）。唯一读取落点 `novel/core/source.py::resolve_recorded_source`。
> 产物目录仍然**不存**任何正文副本（那会撞 `R-0.2`）。
>
> ⚠⚠ **一处语义随之澄清**：「源被就地改写」在两支上本来就是两件事，此前被同一格
> `original_path` 压平，只能靠「记录的那条路径碰巧还在不在」区分——而那恰好是 Output
> 根搬没搬家的偶然。现在：`external_file` 就地改写 → `stale/file_changed`
> （人真的改了原文，重跑即可）；`managed_upload` 就地改写 → `unknown/ghost`
> （目录名声称是 `sha256(bytes)` 而里面的字节不是它 ⇒ 内容寻址存储被破坏，
> **这份候选不可信**；报 file_changed 反而更坏，它暗示「重跑一次就好」，
> 而重跑会再读一遍同一个坏掉的存储）。
> ⚠ 两种都不是 `fresh`，`_require_stage0_fresh` 两种都照拦——变的是给人的那句话，
> 不是闸门的松紧。

### 2.2 剔除记账

| 字段 | 类型 | 必填 | 取值面 / 范围 | 证据 |
|---|---|---|---|---|
| `removed` | `obj[]` | 是 | 全语料 27/163 本非空，单本最多 4 条 | **三源** |
| `removed[].kind` | `str` | 是 | `NONBODY_KINDS` 之一；实测 **`番外`×31 · `后记`×2 · `尾声`×1** | **三源** |
| `removed[].marker_line` | `str` | 是 | 标记行原文（strip 后），长度 2 ~ 18 | **三源** |
| `removed[].line_no` | `int` | 是 | 1 起。实测 541 ~ 2143 | **三源** |
| `removed[].span` | `[int,int]` | 是 | 码点半开区间，**在原始 txt 上**（不是在 `clean_text` 上）。恒 `span[0] < span[1]` | **三源** |
| `removed[].code_points` | `int` | 是 | 94 ~ 3290 | **三源** |
| `removed[].head_preview` | `str` | 是 | 恒 80 | **三源** |
| `removed[].tail_preview` | `str` | 是 | 恒 40 | **三源** |
| `removed[].origin` | `str` | 是 | `machine`（判据认出的）\| `human`（人手工圈的） | **契·码** |

> ⚠ **`span` 的坐标系是原始 txt。** 审阅屏要在原文上画高亮，而 `clean_text` 里那段已经没了。
> 混用坐标系是这类设计最常见的 bug，而且它**不会报错**——只会把高亮画到错误的位置上。

> ⚠ **`origin` 不许省。** 「判据认出的」与「人圈的」必须能区分：
> 重跑 stage0 时前者可以重算覆盖，后者**不许静默覆盖**（`R-0.8`）。
> 没有这个字段，重跑一次就把人的工作悄悄抹掉了。

#### 判据标定时的全语料基线

**命中 27/163 本、共 34 条真标记、0 假阳。**

番外体**在产物目录里不留任何副本**，所以 `removed[]` 是**唯一**能核对「切对了没有」的东西——
剔除是破坏性的，一次假阳会静默吃掉半本书。**这是本份产物存在的第一理由**：
它记的是一段已经从 `clean_text` 里消失的文本，删掉就是真的丢信息，
不像其它字段那样能 0 成本现算回来。

**两个 preview 保留**，尽管人不会去读 JSON。它们是被删掉那段文本的**唯一残片**：
`span` 只有在原文还在原地、且没被换过时才能取回内容，而 `source_ref` 那条恰恰说了
「文件被移走时是不知道」。34 条、每条 120 码点——留着的成本可以忽略，丢了就没有第二次机会。

⚠ **`作者有话说` 这一类在全语料 163 本上零样本。** 枚举有 4 个值，实际只见过 3 个。
它现在有一份合成样本（`kind_author_note`，见 `tests/fixtures_ingest.py`），
那是「这条分支被执行过」，仍然不是「在真实语料上验证过」。

> #### ⚠ `marker_line`（整行）与 `core`（剥壳后）是两个量纲
>
> `marker_line` 长度 2~18，而**所有阈值判的都是 `core`**——剥掉前导 `【[(（`/数字、
> 剥掉尾部标点之后剩下的部分。别混着用。
>
> | `core` 长度 | 命中？ | 告警？ | 说明 |
> |---|---|---|---|
> | ≤ 17 | ✅ 切 | 不告警 | 真标记区。全语料 34 条真标记全在这（最长 17） |
> | **18 ~ 20** | ✅ 切 | ⚠ 告警 | 可疑区 |
> | > 20 | ❌ 不切 | — | 一律当正文。宁可漏切也不误切 |
>
> 两个阈值（`NONBODY_HEAD_MAX_CP` = 20 · 告警线 = 17）**必须一起看**：
> 命中上限高于告警线多少，静默区就有多宽。现在**静默区为 0**。
> ⚠ 静默区不是理论风险：`番外的那天她再也没有对任何人提起过了`（`core` = 18 码点）
> 会被认成番外标记、把其后 105 字正文全部吃掉，而**告警不阻断，正文照样没了**。
> ⚠ 代价写在明处：将来真出现 21~30 码点的番外标记会**漏切**，番外留在正文里——
> 但漏切在章表上看得见，误切看不见。

真实样本（去重后）：`番外` · `番外:` · `番外一` · `番外一:` · `(番外)` · `番外 19` ·
`13 番外` · `15番外:药人` · `18.各种番外` · `12 婚后番外。` · `公主番外：` · `周京时番外` ·
`番外-宋温瑶` · `【番外：两年后】` · `【后记】` · `19.后记` · `【尾声】`

### 2.3 规模与可疑项

| 字段 | 类型 | 必填 | 取值面 / 范围 | 证据 |
|---|---|---|---|---|
| `total_code_points` | `int` | 是 | 原始 txt 归一化后的长度。全语料 8088 ~ 41224 | **三源** |
| `body_code_points` | `int` | 是 | 剔除后剩下的长度 = `len(clean_text)` | **盘·码** |
| `warnings` | `obj[]` | 是 | 每条按 `kind` 判别（`STAGE0_WARNINGS` 之一）+ 该类的**结构化证据**；⚠ **不带措辞**，见下 | **盘·码** |

**`body_ratio` 与 `removed_code_points` 都不落盘**——前者恒等于
`body_code_points / total_code_points`，后者恒等于 `sum(removed[].code_points)`，
校验器现算（判准见 `_README.md`：能 0 成本现算的就别落盘）。
`R-0.9` 的 `< 0.60` 判 FAIL 不受影响。

`STAGE0_WARNINGS` 三类，**全语料实测全部 0 命中**：

| `kind` | 触发 | 结构化证据 | 阈值（不落盘） | 全语料 | 样本 |
|---|---|---|---|---|---|
| `early_cut` | 切点太靠前 | `body_code_points` · `total_code_points` | `body/total < 0.60` | 0 | 合成 `early_cut` |
| `drop_ratio` | 丢弃占比过大 | `removed_code_points` · `total_code_points` | `> 0.35` | 0（实测最大 0.2405） | 合成 `drop_ratio_only` |
| `long_marker` | 标记行过长 | `line_no` · `marker_core_code_points` | `core > 17` | 0（实测最长 17） | 合成 `long_marker_silent_band` |

> #### ⚠ 2026-08-22（阶段 B）：产物**只记证据不记措辞**
>
> 上一版是 `{kind, message}`。三个后果：① 措辞是展示层的东西却被写进长期持久化产物；
> ② 判据只能拿 `kind` 复算，于是**数值错了不会红**——一条 `line_no` 记错的
> `long_marker` 照样绿，而那几个数就是这条告警的全部内容；③ 界面想换个说法就得重跑产地。
>
> ⇒ 措辞唯一落点 `novel/core/warnings.py::describe`（穷举 kind、不留 else 兜底）。
> **阈值不进产物**：它的唯一落点是 `contracts/invariants.py`。阈值改了旧产物的措辞
> 跟着变**才是对的**——它说的是「按今天的判准，当时那份数据长这样」。
> HTTP 层把渲染好的那句话随响应发下去（`Stage0WarningView`），前端不自己拼。
> ⇒ 判据 `s0_warnings` 随之从「只比 kind」升级成**逐条比整证据**。

> ⚠ **`early_cut` 与 `drop_ratio` 几乎是同一条判据。** `body + removed ≈ total`，
> 所以 `removed > 0.35` 基本等价于 `body < 0.65`。`drop_ratio` 独立起作用的窗口只有
> `body_ratio ∈ [0.60, 0.65)` 这 5 个百分点。两条都留是因为**严重度不同**
> （`early_cut` 伴随 `R-0.9` 的 FAIL，`drop_ratio` 只是告警），不是因为它们独立。
> 有测试盯着这个窗口——塌成空的就该合并成一条。

> ⚠ **三类全是零命中。** 现在各有一份合成样本，那证明的是「这条分支被执行过、
> 行为符合预期」，**不是「在真实语料上验证过」**。而 `early_cut` / `drop_ratio`
> 恰恰是这一层最要紧的两道防线——剔除是破坏性的。

### 2.4 审阅状态

| 字段 | 类型 | 必填 | 取值面 / 范围 | 证据 |
|---|---|---|---|---|
| `approval` | `obj` | 是 | **判别联合**（按 `status`，`APPROVAL_STATE`）：`{status:'pending'}` ｜ `{status:'approved', origin, at, by}`。**不满足执行资格时 stage1 拒绝启动**（`R-0.3b`，`HARD`）；执行资格还要求 `origin == 'human'`（`novel/stages/stage0/eligibility.py::is_stage0_executable`，唯一落点） | **盘·码** |
| ↳ `origin` | `auto_zero_hit \| human` | 是 | `auto_zero_hit` 是**历史只读取值**（2026-08-21 停产），现役唯一来源是 `human` | **盘·码** |
| ↳ `at` | `str` | 是 | ISO 时间戳。批过就一定说得出是什么时候批的 | **盘·码** |
| ↳ `by` | `str \| null` | 是 | 只有 `human` 必填；是自报身份，不宣称认证身份 | **盘·码** |
| ↳ `reason` | `str \| null` | 是 | 只有 `human` 必填。⚠ 2026-08-22 阶段 C 从审计条目搬进来：审计链折叠进 revision 之后，「批准」是唯一一件**不改变正文因而不产生新 revision** 的人工决定 ⇒ 理由没有别的落点 | **盘·码** |
| `revision_id` | `str` | 是 | 当前这一版的内容身份（sha256）。**同时是编辑/裁决路的乐观锁** | **盘·码** |
| `revision_origin` | `machine \| human_edit \| human_adopt` | 是 | 这一版怎么来的。⚠ 阶段 C 一次换掉六格：`round`、三格 `machine_*`、两格 `audit_*`（见 §2.5） | **盘·码** |

> #### ✅ `approved` 两态；「没跑过」不由产物表达
>
> 原型曾按「没跑过 · 跑了没批 · 批了」定三态，其中 `not_run` 是**死值**：
> 一份合法报告一旦存在，机器摄入必然已经跑过（报告自带机器指纹），
> 「没跑过」在产物内不可诚实表达，全仓从未有写入方（2026-08-21 preflight 存量
> 扫描 0 实例）。「没跑 / 崩了 / 阻断」是**执行状态**，由「产物不存在 + JobState」
> 表达——消费方读不到报告时的 `UpstreamMissing` 就是那句「先跑产它的那一层」。
>
> ⇒ 取值面的唯一落点是 `contracts/invariants.py::APPROVAL_STATE`，
> 与 `FRESHNESS` / `JOB_STATE` / `BADGE` 同构，随 `/api/enums` 整份下发。
>
> #### ⚠ 2026-08-22（阶段 B）：四个平铺可空字段收成**两个分支**
>
> 上一版顶层平铺 `approved` + `approved_at` + `approval_origin` + `approved_by`，
> 后三格全部可空，靠一个 20 行 validator 维持五条互斥规则。五条守的其实是**同一件事**
> ——这里有两种状态、字段集不一样。用四个可空字段表达两个分支，就是把类型系统能做的事
> 挪进了 validator，而 validator 只在构造那一刻起作用，`model_copy(update=…)` 绕得过去。
> ⇒ 收成判别联合之后，「pending 却带着批准时间」**在类型上就构造不出来**。
> `PendingApproval` 一个字段都没有，这正是重点：「没批」说不出批准时间、来源、批准人。
>
> #### ⚠ 执行资格的第五处消费方（前端）同批收口
>
> 2026-08-21 把 `is_stage0_executable` 立为唯一落点时收了四处（`s0_approval` 判据、
> stage1 启动前置、队列、付费防线）；外部评审列的第五处是「API 返回的可继续状态」，
> 而前端一直在自己拼 `approved === 'approved' && approval_origin === 'human'`
> ——那是第二个落点，漏一次就是历史自动批自动绕过的复活口。
> ⇒ `GET /stage0/report` 的响应多两格投影：`executable` + 稳定理由码 `block_reason`
> （见 `_api.md`），前端只读结论。守卫在
> `tests/test_stage01.py::test_execution_eligibility_has_exactly_one_home`（含前端那一条）。
> ⚠ **`R-0.3b` 的判据不判字符串、判执行资格**：一律走
> `novel/stages/stage0/eligibility.py::is_stage0_executable` / `block_reason`。
> 历史自动批（`auto_zero_hit`）在报告上也显示 `approved`——不判来源，
> 「显示已批准」与「跑不得」就会并存（2026-08-21 裁决）。

#### 批准前的只读分章预览（`GET /api/novels/:sid/stage0/partition-preview`）

`R-0.3a` 说「这一段到底是不是番外，只能人看」。但只给人看**原文 + 删除高亮 + warnings**
是不够的：**纯数字番外**在这三样里一个信号都不产生——章标记只是一个数字，正文里
一个非正文关键词都没有 ⇒ `removed=[]`、`warnings=[]`、`clean == source`。它恰好是
`auto_zero_hit` 停产之前会被自动放行的那一类，也恰好是人工闸门宣称要兜住的那一类。

⇒ 审批页在人点批准**之前**同屏给出一次分章推演：开篇文案字数 + 逐章（阅读序 /
原始章号 / 字数 / 首行预览 / 章号异常）+ 尾段字数。异常分类的唯一落点是
`contracts/invariants.py::CHAPTER_LABEL_ANOMALY`，**三类**互斥、判定优先级即元组顺序
（`reset` > `gap` > `repeat`）。⚠ 曾有第四类 `non_numeric`，2026-08-22 随
`source_chapter_no` 从 `str` 改型成 `int` 一并删除：载体自己保证了它是数字，
这一档从此**结构上不可达**，留着就是第二个 `not_run`。

- **不落盘**：视图直接调 `novel/stages/stage1/partition.py::partition_text` 这个纯函数，
  不写 `stage1/` 任何一份产物，也不经 `jobs.run`。正式 stage1 产物仍然只来自已批准的
  stage0（`R-0.3b` 一个字没动）——**闸门形状不变，变的只是人批之前能看见什么**。
- **不查审批状态**：这条路存在的全部理由就是「人还没批」那一刻要能看。
- `available=false` 不是错误页：它说的是「按当前剔除结果分不出章」，而那正是应当在
  批准**之前**知道的事，不是等 stage1 跑起来才炸。
- ⚠ **别把它当番外检测器**：番外若接着正文往下编号（50 → 51），四类异常一条都不命中。
  那种形态机器看不出来，只能靠同屏的逐章首行预览让人读出来。**它降低盲区，不消灭盲区。**

### 2.5 revision 链与原子发布（2026-08-22 阶段 C）

聚合根是 **`Stage0Revision`**，整团一起不可变：`{原文身份, 正文, 剔除账本, 告警}`。

> #### ⚠ 为什么聚合根不是 `clean_text`
>
> 外部评审 P0-3：此前的三方合并只对 `clean_text` 做 diff3，于是**接受之后正式
> `removed[]` 该写什么在契约里答不出来**——用 ours 的 span 可能指向旧原文、
> 用 theirs 会丢人工圈选、取并集坐标系不一致、从最终文本反推则因为允许任意人工
> 改字而不唯一。四种选择全都不正确，因为被合并的东西根本不是一个字段。
> ⇒ 换就整团换；`diff3` / merge conflict / conflict resolution 三套 schema 一并出局。

**链的形状**（`stage0/revisions.json`）：

| 字段 | 说明 |
|---|---|
| `revisions[]` | 全部历史版本，**按拓扑序**（parent 必须在自己之前出现），恰好一个根 |
| ↳ `revision_id` | `sha256(前像)`，前像**含 parent** ⇒ **这条链本身就是哈希链** |
| ↳ `parent_revision_id` | 链上的父；只有首次摄入那条是 `null` |
| ↳ `origin` | `machine`（无人参与 ⇒ `actor`/`reason` 恒 `null`）｜ `human_edit`（人改了正文或圈选，`R-0.4`）｜ `human_adopt`（人把候选采纳为当前稿） |
| ↳ `content` | 聚合根内容：原文身份 + `clean_text` + `removed[]` + `warnings[]` |
| `current` | `{revision_id, approval}`。⚠ **审批与版本绑在同一个对象里**：换稿必须重新批准，而「换了稿却把上一版的批准留着」在类型上就写不出来 |
| `candidate_revision_id` | 机器重跑算出的、与当前稿不同的那一版。⚠ 它**已经在 `revisions[]` 里**——候选不是另一种东西，只是一条还没被指向的 revision |
| `dismissed[]` | 人看过并否掉的候选（`{revision_id, at, by, reason}`）。它不进链（否掉不改变正文），但必须留痕，否则同一个候选每跑一次机器就复活一次 |

**机器重跑的三条出路**（`novel/stages/stage0/removal.py::run_source`）：

| 情况 | 处理 |
|---|---|
| 机器结果与当前稿**整团内容**逐字相同 | 什么都不做（⚠ 比的是整团，不是两个哈希：只比正文会漏掉账本的差别） |
| 原文变化（`normalized_source_sha1` 对不上） | `stage0_source_changed`（`R-0.14`）：旧稿的 span 坐标系已失效，正式稿仍对旧原文成立；换原文等于摄入另一本书 |
| 当前稿是 `machine` **且**未批准 | 直接发布新结果：没有人工内容可覆盖，也没有「人看过并批准了这一版」可绕过 ⇒ 换掉**不丢任何东西** |
| 其余（当前稿含人工内容，或已被批准） | 正式稿一个字不动，新结果只挂成**候选**，等人三选一 |

**三选一**（`stage0.edit --apply-candidate-plan` / `POST /stage0/candidate/{adopt,dismiss}`）：

- **保留当前** = `dismiss`：当前稿与它的批准状态都不动，候选进 `dismissed[]`；
- **换成候选** = `adopt`：内容逐字取候选，但它是**人做的一次状态转移** ⇒ 单独成一条
  `human_adopt` revision（谁在何时以什么理由换掉了正文，这是审计链折叠进来之后唯一的落点），
  审批退回 `pending`；
- **换成候选后再编辑**：**不是第三个动作**，是 `adopt` 之后照常走 `stage0.edit`。
  多一个复合动作就多一条要各自崩溃恢复的路，而这两步各自已经是一次原子提交。

**崩溃恢复只剩两件事**，都不需要 WAL：

- 崩在 `revisions.json` 落稳**之前** ⇒ 那次提交没有发生过，盘面就是上一状态；
- 崩在落稳**之后**、物化完成之前 ⇒ 下一条写路径重新物化一次即可
  （`ensure_materialized`）。⚠ 补物化之前先分辨两件长得一样的事：中断留下的一定是
  **链上某一版**的正文，而**有人绕过入口手改** live `clean_text.txt` 留下的字节
  链上一版都对不上 ⇒ 对不上就拒绝并指路（`stage0.edit --apply-edit`），不猜、
  更不「补一次物化」把人的字悄悄冲掉。
- 同一份候选裁决重放两次是幂等的：链是权威且一次提交只写一份文件，
  「做过了没有」永远可以直接从链上读出来。

**恢复出口（edit-plan）的 provenance 一律服务端落定**（`run_edit_plan` →
`verified_removals`）：计划里只有 `span`/`kind`；span 与当前版本机器段完全一致的条目
原样保留（origin 仍是 machine，人只是确认了机器的圈选），人工新圈的段按
`s0_removal_ledger` 同一口径复算（标记行=span 内首个非空行、预览=标记行之后/段尾、
`origin='human'`）。
⚠ 2026-08-22 阶段 C（`TODO-36`）起**连类型也分了家**：命令 DTO 是 `RequestedRemoval`
（只有 span + kind），产物 DTO 是 `RemovedSegment`。此前两者共用一个模型，
「哪些格是人说了算、哪些格只能由服务端算」全靠一段注释维持；现在伪造 `origin='machine'`
或伪造预览**构造不出来**。

**机器新候选在 S-00 视图可见**：`_gap_segments` 把扫描器此刻新认出、且不落在当前
`removed[]` 里的非正文段标成 `flagged=true + removed_kind=<kind>` 的 keep 候选。
预选集合只认 `kind='remove'`——候选只提示、不替人勾选。

新正文提交后，`root_clean_sha1` 变化会使 Stage1～7 的 provenance-bearing 产物立即 stale；
生产/付费读拒绝 stale，只读视图仍可展示旧产物用于诊断。

#### 哈希前像定义（canonical preimage，2026-08-21 成文；2026-08-22 阶段 C 收到一处）

`revision_id` 的前像**唯一**由 `novel/stages/stage0/revisions.py::revision_digest`
定义，文档与实现不各自漂移的锚是 `tests/test_stage0_revisions.py` 的 golden vector
（⚠ 锚值与阶段 C 之前的 `snapshot_digest` golden vector **逐字相同**：换掉的是
「谁被哈希」，不是「怎么哈希」）：

| 项目 | 定义 |
|---|---|
| 字符编码 | UTF-8 |
| JSON 序列化 | `sort_keys=True`、紧凑 separators `(',',':')`、`ensure_ascii=False` |
| Unicode 归一 | **不做** NFC/NFKC（与 `R-0.12` 的归一化口径一致） |
| digest 算法 | SHA-256，全宽 hex（64 位小写） |
| `_schema_version` 信封 | **不进前像**——digest 对 Pydantic payload 计算，**不是**盘上 JSON 文件的字节 |
| `revision_id` 的前像 | `{parent_revision_id, origin, actor, reason, content}`。⚠ **不含 `at`、不含 `code`**：机器重跑算出同一份内容、挂在同一个 parent 上就必须得到同一个 id，否则 `dismissed[]` 永远认不出被否过的候选 |

版本口径：`revisions.json` 是阶段 C 新增产物，盘上没有它的存量 ⇒ 恒 `v1`；
`removal_report.json` 为 `v3`（阶段 B 三刀 + 阶段 C 身份六格换两格，逐条登记在
`tests/test_artifact_versions.py::_SHAPE_BREAKS`）。落盘信封恒写注册表 `current_version`，
由 `tests/test_artifact_versions.py::test_every_registered_artifact_round_trips_through_the_v1_envelope`
对全注册表钉住。

---

## 三、判据清单

当前注册表共 **13 条**：**11 hard、1 info、1 unver**。落点是 `contracts/judges/stage0.py`；
条数与分布由 `tests/test_coverage.py` 对着注册表现算，改判据必须同步改这一行。

| 判据 | 级别 | 作用 |
|---|---|---|
| `s0_source_identity` | HARD | 原始小说没有被换成另一份正文 |
| `s0_decode` | HARD | 原始小说仍能按记录的编码无损解码 |
| `s0_hashes` | HARD | 原始正文与清理正文的两个指纹都对得上 |
| `s0_normalization` | HARD | 只做 BOM 与换行归一化 |
| `s0_removal_ledger` | HARD | 每段剔除都有可回到原文的完整账 |
| `s0_body_ratio` | HARD | 清理后正文至少保留六成 |
| `s0_nonbody_shared` | HARD | 剔除与残留断言使用同一套非正文判据 |
| `s0_approval` | HARD | stage0 已获人工批准才可继续（历史自动批须重批） |
| `s0_human_preserved` | HARD | 人工编辑状态没有被机器重跑抹掉 |
| `s0_revision_chain` | HARD | revision 链逐条自证，两份 live 产物是它的物化投影 |
| `s0_dir_roundtrip` | HARD | 书目录能被同一把寻址尺子解析回来 |
| `s0_warnings` | info | 可疑剔除逐项显式报出 |
| `s0_correctness` | unver | 剔除段在语义上确实不是正文——**本层验不了**，见 §四末 |

## 四、不变量

| 不变量 | 对应需求 | 证据 |
|---|---|---|
| `clean_sha1` == 盘上 `clean_text.txt` 的复算值 | `R-0.6` | **盘·码** |
| **机器轮且无人改正文时**：`sum(removed[].code_points) + len(clean_text) == len(归一化原文)` | `R-0.2` | **盘·码** |
| `removed[].span` 两两不重叠，且都落在原始 txt 长度内 | `R-0.2` | **盘·码** |
| `removed[].kind` ∈ `NONBODY_KINDS` | `R-0.1` | **三源** |
| 不满足执行资格（`approval.status != 'approved'` 或 `origin != 'human'`）→ stage1 必须拒绝启动 | `R-0.3b`（`HARD`） | **盘·码** |
| `clean_text` 里**认不出任何非正文标记** | `R-0.1` / `R-1.7` | **盘·码** |

> #### 最后那条就是 stage1 的断言（`R-1.7`）
>
> 同一件事在两层各查一次，而且**两次都不切东西**：
> 本层查「我删干净了吗」，stage1 查「递给我的真的干净吗」。
> ⇒ stage0 漏掉的番外会在 stage1 撞上**显式终态**，而不是一路流到 stage2 去花钱。
>
> ⚠ 它是**同一套判据的重放**，所以只能抓「判据本来就认得、但 stage0 没删」这一类
> （例如人误删了一条 machine 记录）。
> **判据本来就不认得的（盲区①：番外用纯数字章节标记写），它同样抓不到。**
> 那一类只有 `R-0.3a` 的人工闸门能挡。**别把这条断言当成盲区的解药。**

### 验不了的那条

**剔除的正确性验不了**（`R-0.7` / `B-20`）：「这一段到底是不是番外」没有结构不变量可查。
本层的存在**不改变这个事实**——它改变的是**谁来兜底、在什么时候兜**：
从「stage1 切完之后事后审阅」变成「切之前人批准，且能直接改」。

所以每本仍要打印 `[????] 剔除的正确性：本层验不了`——
**「没报违规」与「根本没在查」必须能区分**，加了人工闸门也不例外。

