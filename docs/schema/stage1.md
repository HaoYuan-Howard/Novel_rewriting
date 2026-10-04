# Stage1 分章 schema

Stage1 是 0 成本、确定性的分章层。它只把 Stage0 当前已批准的
`clean_text.txt` 分成下游真正消费的两份材料：

```text
stage1/main_text.json  [ {chapter_ordinal, text}, ... ]
stage1/hook_copy.json  {text}
```

原文章号、切片坐标、章数、断号/重号/倒退和告警都能从当前 clean
确定性复扫，因此不落盘第二份。`novel/core/ingest_audit.py::audit_surface`
是 producer、`--check-only`、Stage1 页、freshness 与 s1 审批共用的唯一审计口。

---

## 一、`main_text.json` v3

顶层是 RootModel 信封中的章节数组，最少两章。

| 字段 | 类型 | 必填 | 取值面 / 语义 | 证据 |
|---|---|---|---|---|
| `[].chapter_ordinal` | `int` | 是 | 从 1 开始的连续阅读序；是全链章节唯一键 | **契·码** |
| `[].text` | `str` | 是 | 去空白后非空；与当前 clean 的对应章切片逐字相等 | **契·码** |

`chapter_ordinal` 只回答「这是第几个可读章」。原文标号不是下游身份：它可重号、
断号、倒退，且下游没有一处需要信任它，所以 v3 删除了
`source_chapter_no`。Stage1 对照页仍显示原文标号，但值来自对当前 clean 的现扫。

下游按真实消费面取窄投影：Stage2 只读
`(chapter_ordinal, text)`；成书渲染也只使用这两格，不向 prompt 携带原文章号。

---

## 二、`hook_copy.json` v2

| 字段 | 类型 | 必填 | 取值面 / 语义 | 证据 |
|---|---|---|---|---|
| `text` | `str` | 是 | 去空白后非空；与当前 clean 从坐标 0 到第一个正文章标记之前的切片逐字相等 | **三源** |

它被 Stage2 源抽取消费：切成 `e00_*` 章前证据段进书级调用与第 1 章抽取，按信息职责
分流（开场前已成立的状态/关系/知情 → `initial_state`；实际发生的开场行动 → 第 1 章
beats；与正文重复的事件以正文为准；预告/概括不进 canonical；判断不了弃权），并与
`main_text.json` 共同构成候选链的组合源身份（`docs/BACKEND_CONTRACT.md` `R-2.9`）——
只改引子也换源身份，旧候选根不得沿用旧购买。Stage6 页与旧链消费者已随 Stage6 整层退役。

---

## 三、运行时审计（不是产物）

审计每次都从当前 Stage0 revision、`clean_text.txt`、`main_text.json` 与
`hook_copy.json` 重算，不读任何历史报告。现役九条读数是：
当前注册表共 **9 条**：**8 hard、1 info**。

| rule | 检查面 | 级别 |
|---|---|---|
| `s1_input_hash` | clean 实际 sha1 = Stage0 当前批准 `clean_sha1` | HARD |
| `s1_chapter_labels` | 阅读序连续；原文标号的重号、缺号、严格递增读数 | HARD |
| `s1_chapter_count` | 章标记数 = `main_text` 长度，逐章文本 = clean 切片 | HARD |
| `s1_decomposition` | hook + 章标记行 + 章正文 + tail 完整覆盖 clean | HARD |
| `s1_nonbody_residual` | 批准后 clean 不得残留非正文标记 | HARD |
| `s1_warnings` | 达阈值的原文章号重复；只报运行时 finding | INFO |
| `s1_hook` | 文案非空且与当前 clean 切片逐字相等 | HARD |
| `s1_min_chapters` | 至少两个正文章标记 | HARD |
| `s1_tail` | 正文切点后为空，或只有一行完结标记 | HARD |

producer 在落盘前处理「不可构造」的终态（无文案、少于两章、空章、残留非正文）；
可构造时先落两份材料，再经同一审计口产出 CheckReport，保留 landed-red 的
0 成本恢复语义。freshness 和 s1 审批不只绑输入：任意一份材料被单独篡改，
都会分别变为 stale 并撤销批准有效性。

已确认切分后，仅经 Stage0 批准修订钩子并准确重新物化两份材料，才可按
`novel/source_identity.py::revision_proof` 证明原切点与正文逐字不变。Stage1 只读审批
还要从祖先 revision 重建原 MainText / HookCopy 两份字节 SHA，与旧确认全部对上；
通过后沿用旧票而不改根、不补票。正文或章标记改变、原票缺失、任一旧 SHA 损坏
或当前源未批准时，仍需现役审计与显式切分确认。

---

## 四、视图与版本

Stage1 的 HTTP 面只有 `GET /api/novels/:sid/stage1 -> Stage1View`。响应把当前 clean
表示成从 0 开始、首尾相接的 `hook | chapter | tail` 分区；章分区附带当次复扫的
`source_chapter_no` / `label_delta` / `anomaly`。这些是视图诊断，不是产物字段。

`main_text.json` 因删除 `source_chapter_no` 从 v2 升到 v3；`hook_copy.json` 形状不变，
仍是 v2。业务产物不自动迁移，低版本不允许手补字段或改版本号冒充新产地。
当前两本存量书按用户裁决，待 Stage0–7 减法全部完成后一并从 producer 重建。
