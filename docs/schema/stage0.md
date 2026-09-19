# Stage0 schema：非正文剔除

Stage0 把原始 txt 变成只含文案和正文的文本；机械校验（含正文占比闸）通过即写入
`admission.status=passed`（`system:stage0-auto`），进入 Stage1。全程 0-LLM。
产品要求见 `docs/BACKEND_CONTRACT.md::R-0`；本文只说明现役载体与字段。

**STR-03-S0（2026-09-18）**：权威从 revision 链 + approval 收成一次
`Stage0Transform` v3；路径仍为 `stage0/revisions.json`。v2 拒读，须
`stage0.remove --source` 重建。

## 一、产物与权威关系

| 路径 | 版本 | 职责 | 权威性 |
|---|---:|---|---|
| `stage0/revisions.json` | **v3** | 一次确定性机械 transform | **唯一 JSON 权威** |
| `stage0/clean_text.txt` | 字节形态 | transform 正文投影，供 Stage1 及全链根指纹 | 可由 transform 幂等重建 |

报告、告警、正文长度由 `revisions.snapshot_of` 运行时派生，不落第二份 JSON。

## 二、`Stage0Transform`（`revisions.json`）

| 字段 | 类型 | 语义 |
|---|---|---|
| `source` | `Stage0Source` | 原文前提（定位 + 字节/归一身份） |
| `code` | `str` | producer 代码身份（不进 transform_id 前像） |
| `transform_id` | 64 位 sha256 | 内容身份；前像 = source + removed + clean_sha1 |
| `removed` | `RemovedSegment[]` | 按 span 排序、不重叠；v3 仅 `origin=machine` |
| `clean_sha1` | 40 位 sha1 | 正文内容身份 |
| `body_code_points` | `int >= 0` | 正文码点数 |
| `admission` | `Stage0Admission` | `{status: passed, scope: mechanical, at, by}` |

### `Stage0Source`

| 字段 | 语义 |
|---|---|
| `source_ref` | `external_file` / `managed_upload` |
| `raw_sha256` | 原始文件字节 SHA-256 |
| `encoding` | 无损解码实际编码名 |
| `normalized_source_sha1` | 去 BOM、换行归一后文本 sha1 |
| `total_code_points` | 归一化原文码点数 |

### `RemovedSegment`

`kind` / `marker_line` / `span` / `origin=machine`。不存行号、预览、长度。

## 三、字节投影 `clean_text.txt`

- UTF-8、无 BOM、换行恒 `\n`。
- 内容 = `reconstruct_clean(归一化原文, removed)`。
- live 与 transform 期望不一致且 sha1 对不上 ⇒ `HumanEditProtected`（v3 无人工编辑入口）。

## 四、运行时 `Stage0Snapshot`

不落盘。含原文件名、source 字段、removed、warnings、`transform_id`、`admission`；
兼容属性 `revision_id`（= transform_id）、`revision_origin`（恒 `machine`）。无 `.approval`。

## 五、判据与重建

注册表仍 13 条（`contracts/judges/stage0.py`）。`s0_approval` 查 mechanical admission；
`s0_revision_chain` 复算 `transform_id` 与 clean 投影一致性。版本破坏升版、拒读旧版，
无通用迁移。
