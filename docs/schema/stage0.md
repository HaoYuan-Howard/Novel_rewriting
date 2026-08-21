# stage0 产物 schema

非正文剔除 + **风险分层审批**。**这一层不调 LLM。** 对应需求 `R-0.1` ~ `R-0.14`。

读法（证据列的取值）见 `_README.md`。
**载体 = Pydantic v2**（已定），但本文只定内容、不写模型——模型在骨架阶段照本文写。

> ## ✅ 本层实现已落地
>
> 这里的 **`盘·码`** 指本仓实现生成并复读的盘上证据；
> **`三源`** 仍只用于原本就有三源证据的字段。

六类持久载体：

```
stage0/clean_text.txt        删掉非正文之后的全文。**stage1 的唯一输入**
stage0/removal_report.json   正式稿身份 + 机器快照身份 + 审批/审计链头（v1）
stage0/machine_snapshot.json 本次机器剔除的完整、可复算快照（v1）
stage0/merge_candidate.json  `base / ours / theirs` 与 typed 冲突；未接受前不改正式稿（v1）
stage0/_audit/edit_N.json    不可覆盖、SHA-256 串联的决定账（v1）
stage0/_rounds/              正式稿、快照、候选与已提交状态的可恢复历史
```

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
> 批准必须由人显式给出 actor 与 reason，记为 `approval_origin=human`。
> 「零命中自动批」（`auto_zero_hit`）已于 **2026-08-21 停产**：零 removed、零 warning、
> clean == source 恰好是纯数字番外盲区的形状——那正是这道闸门要拦的东西，
> 自动放行等于宣称「人工是唯一防线」的同时把它拆了。**存量自动批产物只读不承行**：
> 读得、展示得、审计得，但不具执行资格（`legacy_auto_approval_requires_review`），
> 人工重批（`stage0.approve`，追加一条 `approve` 审计条目）后恢复。
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
> ⇒ 审阅屏算 diff **只能拿 `_rounds/` 里的上一版比**，不能拿 `source ↔ clean` 比；
> ⇒ `edited_by_human` 因此是**唯一**能一眼看出「这本书被人动过」的标志位，不许省；
> ⇒ 逐条记账（`text_edits[]`：`span` + before/after）**本轮不做**，登记为待办
>   （⚠ 这里原本写「登记为 §四 #5」，而当时全文只有三节——那个编号从来就指不到东西）。

---

## 二、`removal_report.json`

### 2.1 指纹与身份

| 字段 | 类型 | 必填 | 取值面 / 范围 | 证据 |
|---|---|---|---|---|
| `original_filename` | `str` | 是 | 含平台前缀与扩展名 | **码** |
| `original_path` | `str` | 是 | 绝对路径。**文件被移走时是「不知道」，不是「不匹配」**；界面上传的那一份例外，见 §2.1 注 | **码** |
| `code` | `str` | 是 | 本轮写报告所用代码版本；本层没有 `_provenance`，故落在顶层 | **盘·码** |
| `source_sha1` | `str` | 是 | **原始 txt** 归一化后的 sha1。追溯用 | **码** |
| `clean_sha1` | `str` | 是 | **`clean_text.txt`** 的 sha1。**这才是 stage1 记的那个输入指纹** | **盘·码** |
| `machine_source_sha1` / `machine_clean_sha1` | `str` | 是 | 本轮机器快照的 source/clean 身份；人工正式稿可与 machine clean 不同 | **盘·码** |
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
> | `source_sha1` | 原始 txt 被换/被改 | 全链重跑（`R-0.14` + stage1 的 `R-1.3`） |
> | `clean_sha1`（`source_sha1` 未变） | 人编辑过剔除结果 | **保住**，归档进 `_rounds/`（`R-0.5`） |
> | 两个都变 | 换了原文又编辑过 | 说清楚是两件事，别合并成一条「陈旧」 |

> #### ⚠ `original_path` 断掉的两种情形，处置**相反**
>
> 它记的是**书目录之外**的绝对路径，所以「路径失效」有两个完全不同的成因：
>
> | 源是哪来的 | Output 根搬家之后 | 结论 |
> |---|---|---|
> | 外部语料（`Test_data/…`） | 文件**真的**不在了 | `ghost`／不可用，**正确**。契约有意接受，补偿是两个 preview 残片 |
> | 界面上传（`<base>/_service/uploads/<内容地址>/`） | 文件**跟着 base 一起搬了** | 报 `ghost` 是**假阴性**：变的不是世界，是我们自己给自己起的目录名 |
>
> ⇒ 读取侧只对后者按当前 base **重锚**，且候选必须自证内容地址（目录名就是
> `sha256(原始字节)`）才采信；`original_path` 字段本身**仍然记绝对路径，写入侧没变**，
> 产物目录也**不会**因此多存任何正文（那会撞 `R-0.2`）。唯一落点
> `novel/core/source.py::resolve_recorded_source`，四个读取侧共用它。
>
> ⚠ **记录的位置优先，重锚只是回落。** 顺序反过来，「源被就地换掉」会从
> `stale/file_changed`（重跑即可）变成 `ghost`（找不着东西）——两句话对人的下一步相反。

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
`span` 只有在原文还在原地、且没被换过时才能取回内容，而 `original_path` 那条恰恰说了
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
| `warnings` | `obj[]` | 是 | 每条带 `kind`（`STAGE0_WARNINGS` 之一）与人读的一句话 | **盘·码** |

**`body_ratio` 与 `removed_code_points` 都不落盘**——前者恒等于
`body_code_points / total_code_points`，后者恒等于 `sum(removed[].code_points)`，
校验器现算（判准见 `_README.md`：能 0 成本现算的就别落盘）。
`R-0.9` 的 `< 0.60` 判 FAIL 不受影响。

`STAGE0_WARNINGS` 三类，**全语料实测全部 0 命中**：

| `kind` | 触发 | 阈值 | 全语料 | 样本 |
|---|---|---|---|---|
| `early_cut` | 切点太靠前 | `body/total < 0.60` | 0 | 合成 `early_cut` |
| `drop_ratio` | 丢弃占比过大 | `> 0.35` | 0（实测最大 0.2405） | 合成 `drop_ratio_only` |
| `long_marker` | 标记行过长 | `core > 17` | 0（实测最长 17） | 合成 `long_marker_silent_band` |

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
| `approved` | `str` | 是 | **两态**：`pending` ｜ `approved`（`APPROVAL_STATE`）。**不满足执行资格时 stage1 拒绝启动**（`R-0.3b`，`HARD`）；执行资格还要求 `approval_origin == 'human'`（`novel/stages/stage0/eligibility.py::is_stage0_executable`，唯一落点） | **盘·码** |
| `approved_at` | `str \| null` | 是 | ISO 时间戳；未批准时 `null`。**有值 ⟺ `approved == 'approved'`**（载体的后置条件） | **盘·码** |
| `approval_origin` | `auto_zero_hit \| human \| null` | 是 | 未批时 `null`；`auto_zero_hit` 是**历史只读取值**（2026-08-21 停产），现役唯一来源是 `human` | **盘·码** |
| `approved_by` | `str \| null` | 是 | 只有 `human` 必填；是自报身份，不宣称认证身份 | **盘·码** |
| `round` | `int` | 是 | 第几轮。人每编辑一次 +1，上一轮进 `_rounds/` | **盘·码** |
| `edited_by_human` | `bool` | 是 | 本轮有没有人工编辑。= `any(r.origin == 'human' for r in removed)` 之外还含直接改正文 | **盘·码** |
| `audit_seq` / `audit_head_sha256` | `int` / `str \| null` | 是 | `0/null` 或精确指向 `_audit/edit_N.json`；盘上多出/少一条都拒绝 | **盘·码** |

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
> ⚠ **`R-0.3b` 的判据不判字符串、判执行资格**：一律走
> `novel/stages/stage0/eligibility.py::is_stage0_executable` / `block_reason`。
> 历史自动批（`auto_zero_hit`）在报告上也显示 `approved`——不判来源，
> 「显示已批准」与「跑不得」就会并存（2026-08-21 裁决）。

### 2.5 机器快照、三方合并与 WAL

- `MachineRemovalSnapshot` 保留机器本轮的 source/clean 指纹、全文、removed 与 warnings；
  它从不冒充正式人工稿。
- 重跑结果变化时**先分诊再谈合并**（2026-08-21 裁决，P0-3）：
  `has_human_content_divergence`（`novel/stages/stage0/merge.py`，唯一落点）判
  「正式稿含机器结果之外的人工内容」——三条独立证据任一为真即分叉：
  `edited_by_human` · `removed[]` 含 `origin='human'` · **`clean_sha1 != machine_clean_sha1`
  （客观哈希分叉，不信标志位）**。

  | 情况 | 处理 |
  |---|---|
  | 原文相同、机器结果变化、**无**人工内容分叉 | 允许 diff3 candidate（`ours == base`，diff3 退化为「取机器新结果」，theirs 账本可证正确） |
  | 原文相同、机器结果变化、**有**人工内容分叉 | 不写 candidate，正式对一字节不动；`stage0_human_edit_protected` 显式终态，出路=走 `stage0.edit --apply-edit-plan` 重选段 |
  | 原文变化（`machine_snapshot.source_sha1` 对不上） | 连 candidate 都不谈（`stage0_source_changed`，R-0.14）：旧正式稿的 span 坐标系已失效；正式稿仍对旧原文成立 |
  | 已落盘的 candidate 其基线有人工分叉 | accept 端拒绝并**撤销归档**该 candidate（`_retire_merge_candidate`） |

- **安全条件同时存在于生成端与接受端**：生成端不产危险 candidate，不等于接受端
  收不到——升级前已落盘的、WAL 崩溃恢复出来的、手工构造的都会走到
  `run_merge_accept`。唯一例外：盘上已有该 accept 的不可覆盖 WAL 意图行
  （升级前崩溃在接受途中）——意图行本身就是决定时刻，拒绝它会让审计链永远
  不闭合、书永久卡死；已提交的合并同理，重放只认账不翻案。
- `Stage0MergeCandidate` 固定 `base=上轮机器稿`、`ours=当前批准稿`、`theirs=新机器稿`。
  Unicode 码点级 diff3 只自动合并不相交或相同改动；重叠改动产出
  `Stage0MergeConflict`，每个 id 必须恰好决议一次。
- **恢复出口（edit-plan）的 provenance 一律服务端落定**（`run_edit_plan`）：计划里
  只信 span/kind；span 与现报告机器段完全一致的条目原样保留（origin 仍是 machine，
  人只是确认了机器的圈选），人工新圈的段按 `s0_removal_ledger` 同一口径复算
  （标记行=span 内首个非空行、预览=标记行之后/段尾、origin='human'）。客户端伪造
  origin/行号/标记行/预览一律无效。
- **机器新候选在 S-00 视图可见**：`_gap_segments` 把扫描器此刻新认出、且不落在当前
  `removed[]` 里的非正文段标成 `flagged=true + removed_kind=<kind>` 的 keep 候选
  （API 契约已放宽为「remove 分区或带 removed_kind 的候选才可 flagged」）。预选集合
  只认 `kind='remove'`——候选只提示、不替人勾选；有人工分叉的轮次采纳机器新结果
  只能走这条路。
- `Stage0AuditEntry` 以 `previous_sha256` 串联，文件不可覆盖。合并决定的 `seq+1`
  同时是 write-ahead log：accept 以 report 为 commit marker，reject 同样在移走 live candidate 前先提交 report。
  进程可在任一 durable phase 崩溃；同一 actor/reason/乐观锁/决议重放必须幂等完成，
  不同决定不得覆盖未完事务。
- 新正文提交后，`root_clean_sha1` 变化会使 Stage1～7 的 provenance-bearing 产物立即 stale；
  生产/付费读拒绝 stale，只读视图仍可展示旧产物用于诊断。

#### 哈希前像定义（canonical preimage，2026-08-21 成文）

三处 SHA-256（`snapshot_sha256` / `entry_sha256` / `candidate_id`）的前像**唯一**由
`novel/stages/stage0/merge.py` 的三个函数定义，文档与实现不各自漂移的锚是
`tests/test_stage0_merge.py` 的 golden vector：

| 项目 | 定义 |
|---|---|
| 字符编码 | UTF-8 |
| JSON 序列化 | `sort_keys=True`、紧凑 separators `(',',':')`、`ensure_ascii=False` |
| Unicode 归一 | **不做** NFC/NFKC（与 `R-0.12` 的归一化口径一致） |
| digest 算法 | SHA-256，全宽 hex（64 位小写） |
| `_schema_version` 信封 | **不进前像**——digest 对 Pydantic payload 计算，**不是**盘上 JSON 文件的字节 |
| 自身 digest 字段 | 一律剔除（见下表） |
| `snapshot_sha256` | 剔 `snapshot_sha256`、`created_at`、`code`（时间与代码是审计元数据，机器输入/输出不变则身份稳定）——`merge.py::snapshot_digest` |
| `entry_sha256` | 只剔 `entry_sha256`（含 `at` 与 `previous_sha256`，即链式）——`merge.py::audit_digest` |
| `candidate_id` | 剔 `candidate_id`、`created_at`——`merge.py::candidate_digest` |

版本口径：`removal_report.json` 头部的 `(v1)` 与注册表一致；这一族**曾是 v2**
（risk-tiered audited approvals，`a039121`），2026-08-18 随「产物 schema 归零 v1」
（`32643c5`，见 `docs/BACKEND_CONTRACT.md` §十）降回 v1——旧 v2 产物当时已随三本书
整批删除，归零后做过「真写盘 → 真读盘」闭环。落盘信封恒写注册表 `current_version`，
由 `tests/test_artifact_versions.py::test_every_registered_artifact_round_trips_through_the_v1_envelope` 对全注册表钉住。

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
| `s0_edit_archive` | HARD | 人工编辑的上一轮已归档 |
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
| `approved != 'approved'` → stage1 必须拒绝启动 | `R-0.3b`（`HARD`） | **盘·码** |
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

