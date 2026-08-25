# Stage0 schema：非正文剔除与人工闸门

Stage0 把原始 txt 变成只含文案和正文的文本，并在进入 Stage1 前要求人工批准。
本层不调用 LLM。产品要求与判据编号见 `docs/BACKEND_CONTRACT.md::R-0`；本文只说明
现役载体、字段语义和读写边界。

## 一、产物与权威关系

| 路径 | 版本 | 职责 | 权威性 |
|---|---:|---|---|
| `stage0/revisions.json` | v2 | 原文身份、不可变 revision 链、current、候选、审批 | **唯一 JSON 权威** |
| `stage0/clean_text.txt` | 字节形态 | current revision 正文，供 Stage1 及全链根指纹消费 | 可由 revisions 幂等重建 |

不再落 `stage0/removal_report.json`。报告、告警、正文长度和指纹均由
`novel/stages/stage0/revisions.py::snapshot_of` 在运行时从 revisions 派生。

一次提交先原子写 `revisions.json`，再物化 `clean_text.txt`。若进程在两步之间中断，
下一次入口会恢复投影；若 live clean 不等于链上任何一版，则视为绕过正式编辑入口的手改并拒绝覆盖。

## 二、`revisions.json`

### 2.1 顶层

| 字段 | 类型 | 必填 | 语义 | 证据 |
|---|---|---:|---|---|
| `source` | `Stage0Source` | 是 | 整条链共享的原文身份 | **契·码** |
| `revisions` | `Stage0Revision[]` | 是 | 按拓扑序保存的不可变版本，至少一条 | **契·码** |
| `current` | `Stage0Current` | 是 | 当前正文与其审批状态 | **契·码** |
| `candidate_revision_id` | `sha256 \| null` | 是 | 待人工裁决的机器候选 | **契·码** |
| `dismissed` | `Stage0DismissedCandidate[]` | 是 | 已否决候选，防同一候选重跑复活 | **契·码** |

### 2.2 `Stage0Source`

| 字段 | 类型 | 必填 | 语义 | 证据 |
|---|---|---:|---|---|
| `source_ref` | `SourceRef` | 是 | 原文定位符 | **契·码** |
| `normalized_source_sha1` | 40 位 sha1 | 是 | 去 BOM、换行归一后原文的内容身份 | **契·码** |
| `total_code_points` | `int >= 1` | 是 | 归一化原文码点数 | **契·码** |

`SourceRef` 是判别联合：

- `external_file`：`{kind, absolute_path}`。路径消失即 `unknown/ghost`，不猜别处文件。
- `managed_upload`：`{kind, blob_sha256, filename}`。位置按当前 base 的
  `_service/uploads/<blob_sha256>/<filename>` 解析，并复核内容地址。

换原文就是新链；源身份不在每条 revision 内重复。

### 2.3 `Stage0Revision`

| 字段 | 类型 | 必填 | 语义 | 证据 |
|---|---|---:|---|---|
| `revision_id` | 64 位 sha256 | 是 | revision 前像身份，前像含 parent 与 content | **契·码** |
| `parent_revision_id` | `sha256 \| null` | 是 | 父版本；全链恰有一个根 | **契·码** |
| `origin` | `machine \| human_edit \| human_adopt` | 是 | 本版来源 | **契·码** |
| `actor` | `str \| null` | 是 | 人工版本的自报操作者；机器版恒 null | **契·码** |
| `reason` | `str \| null` | 是 | 人工版本理由；机器版恒 null | **契·码** |
| `at` | `str` | 是 | 产生时间 | **契·码** |
| `code` | `str` | 是 | producer 代码身份 | **契·码** |
| `content` | `Stage0RevisionContent` | 是 | 本版正文与剔除账本 | **契·码** |

`revision_id` 不含 `at` 与 `code`：同一 parent 上得到同一内容时身份必须稳定，
否则已否决候选会在每次机器重跑后变成“新候选”。

### 2.4 `Stage0RevisionContent`

| 字段 | 类型 | 必填 | 语义 | 证据 |
|---|---|---:|---|---|
| `clean_text` | `str` | 是 | 该版正文；current 同步投影到 `clean_text.txt` | **契·码** |
| `removed` | `RemovedSegment[]` | 是 | 从归一化原文剔除的区间账本 | **契·码** |

`clean_text` 与 `removed` 是一个聚合根，版本切换必须整团切换，不能只合并正文。

### 2.5 `RemovedSegment`

| 字段 | 类型 | 必填 | 语义 | 证据 |
|---|---|---:|---|---|
| `kind` | `NONBODY_KINDS` | 是 | 非正文类型 | **契·码** |
| `marker_line` | 非空 `str` | 是 | span 内用于判断的标记行 | **契·码** |
| `span` | `[start, end]` | 是 | 归一化原文码点半开区间，`0 <= start < end` | **契·码** |
| `origin` | `machine \| human` | 是 | 机器识别或人工圈选 | **契·码** |

不保存行号、区间长度、首尾预览：行号和长度可从原文与 span 无损复算；预览是原文切片，
保存会形成正文残片副本。审校视图需要可读正文时，按 `source_ref` 读取原文并切片。

removed 必须按 span 排序、两两不重叠；机器轮且未人工改正文时，删除区间与 clean 必须完整覆盖原文。

### 2.6 current、候选与否决记录

`Stage0Current` 保存 `revision_id` 和与该版本绑定的 `approval`。
审批是判别联合：`{status: pending}` 或
`{status: approved, at, by, reason}`。换 current 必须重新批准。

候选必须是 revisions 内的机器版本，且不能同时是 current 或 dismissed。
否决记录保存 `{revision_id, at, by, reason}`；否决不改正文，因此不另建 revision。

## 三、字节投影 `clean_text.txt`

- UTF-8、无 BOM、换行恒为 `\n`。
- 内容逐字等于 current revision 的 `content.clean_text`。
- Stage1 同时消费这份字节投影和 revisions 的窄投影
  `source.normalized_source_sha1`；不消费 Stage0 的运行时报告。

## 四、运行时投影与命令 DTO

### 4.1 `Stage0Snapshot`（不落盘）

运行时从 source、current revision 与阈值现算：原文件名、source ref、producer code、
源/clean 指纹、removed、总长度、正文长度、warnings、revision 身份、origin 与 approval。
它供判据、CLI 和服务端视图使用，不进入 artifact registry。

warnings 也不参与 revision 身份：

- `early_cut`：正文占比低于 `BODY_RATIO_MIN`；
- `drop_ratio`：剔除占比高于 `DROP_RATIO_MAX`；
- `long_marker`：标记 core 长于 `NONBODY_MARKER_WARN_CP`。

阈值唯一落在 `contracts/invariants.py`，措辞唯一落在 `novel/core/warnings.py::describe`。

### 4.2 编辑计划

`Stage0EditPlan` 只携带：

| 字段 | 语义 |
|---|---|
| `actor` / `reason` | 人工审计信息 |
| `expected_revision_id` | 乐观锁，同时钉住基线版本与内容 |
| `removed[]` | 用户意图 `RequestedRemoval{kind, span}` |

计划不携带原文哈希、全文、clean 哈希或派生长度。执行端重读 revisions 与原文，校验根身份，
再从 span 重建 clean 和已验证的 `RemovedSegment`。原文或 current 漂移时关闭式拒绝。

候选裁决计划只携带 `action`、候选 id、expected current id、actor 与 reason。

### 4.3 HTTP 视图

- `/stage0/report` 只返回页面实际消费的：`original_filename`、`total_code_points`、
  渲染后的 warnings、`executable`、`revision_id`、`revision_origin`。
- `/stage0/segments` 返回原文连续分区和可用状态，不回传源哈希。
- `/stage0/candidate` 只返回候选 id、origin、clean text、正文长度、剔除数，
  以及 current 的比较摘要。

源定位符、审批细节、完整 revision 元数据不属于浏览器决策输入，不在这些响应中复制。

## 五、判据、版本与重建

当前注册表共 **13 条**：**11 hard、1 info、1 unver**。落点是
`contracts/judges/stage0.py`。

| 关注点 | 判据/机制 |
|---|---|
| 解码与换行归一 | `s0_decode`、`s0_normalization` |
| 原文身份与目录不动点 | `s0_hashes`、`s0_source_identity`、`s0_dir_roundtrip` |
| 剔除账本与共用识别尺子 | `s0_removal_ledger`、`s0_nonbody_shared` |
| revision 链与 clean 投影 | `s0_revision_chain` |
| 正文保留比例 | `s0_body_ratio` |
| 人工闸门 | `s0_approval` |
| 人工编辑保留 | `s0_human_preserved` |
| 运行时可疑项 | `s0_warnings` |
| 人工不可判的正确性 | `s0_correctness`（UNVER） |

`stage0/revisions.json` v2 是破坏性收窄：旧产物不迁移、不手补字段，由
`stage0.remove --source <原文>` 重建并重新人工批准。`clean_text.txt` 随之免费重建；
Stage1–7 均按新根重新生成。当前 `outputs/` 两本书留待 Stage0–7 全部减法完成后统一重建。
