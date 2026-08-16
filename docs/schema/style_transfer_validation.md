# 文风画像跨题材可迁移性验证 manifest

`tools/validate_style_transfer.py` 是一条独立、离线、无正文的评测链。它只判断 Stage3
画像中的某个字段是否取得进入硬判据的**资格**；它不生成画像、不调用 LLM、不联网，也不改变
生产阈值。没有通过全部门槛的字段统一输出 `diagnostic`，不得用总体均值或某一本的好结果将其
提升为 `hard`。

## 1. 私有数据边界

输入是至少六份本地逐书摘要。每份只允许：

- `sid`、归一化源书的 `source_sha256`、题材 `genre`；
- 协议预声明的 `field_id`；
- 四个维度各自的 `{passed,total}` 聚合计数。

四个固定维度为：

1. `transferability`：该字段在未参与本书判断的画像/规则下仍判对；
2. `position_coverage`：预声明的开头、中段、结尾等采样位置得到覆盖；
3. `ab_discrimination`：盲化 A/B 对中能区分本书画像与跨书干扰画像；
4. `material_partition`：正文、开篇文案等素材分区没有串用。

摘要与发布 manifest 都不能带原文或模型材料。加载器在 schema 校验前递归拒绝键名中独立出现
`excerpt`、`prompt`、`response`、`text` 的字段（例如 `source_text`、`raw_response`）；
Pydantic 的 `extra=forbid` 再拒绝其余未声明字段。输入本身属于私有评测资料，不提交仓库。

## 2. 先冻结协议，再读取结果

阈值和字段种类单独写入 protocol 文件。protocol 必须包含：

```json
{
  "schema_version": 1,
  "protocol_id": "style-transfer-v1",
  "declared_at": "2026-08-16T00:00:00Z",
  "thresholds_locked_before_evaluation": true,
  "thresholds": {
    "confidence": 0.95,
    "min_books": 6,
    "min_genres": 4,
    "min_book_trials": 200,
    "min_training_trials": 1000,
    "min_genre_trials": 200,
    "min_wilson_lower": 0.90,
    "max_holdout_rate_drop": 0.05
  },
  "fields": [
    {"field_id": "voice_perspective", "kind": "semantic"},
    {"field_id": "paragraph_length", "kind": "deterministic"}
  ]
}
```

结构下限不可放宽为少于六书或四题材；Wilson 下界不可低于 `0.90`，held-out 命中率下降上限
不可高于 `0.05`。实际计数门和性能门可以在评测前收紧。逐书摘要必须与
protocol 的字段集合完全相等，防止看完结果后删除难例字段。段长、对话率、比喻词计数等可复算
项目应预声明为 `deterministic`；这类字段即使所有数值过门也永久只得到 `diagnostic`。

## 3. 字段级判定

每个维度都报告命中率和两侧 95% Wilson 区间的下界，最终比较使用未舍入数值。一个
`semantic` 字段只有同时满足以下条件才得到 `hard`：

- 至少六本书、至少四个题材，且每本书都提供该字段；
- 每一本的四个维度均达到逐书样本数和 Wilson 下界门；
- 依次把每一本当 held-out：其余书聚合的四维指标和 held-out 本身都过门，且 held-out 命中率
  相对训练聚合的下降不超过预声明上限；
- 每个题材单独聚合后的四个维度均达到题材样本数和 Wilson 下界门。

任一条件失败都输出 `diagnostic`，并列出失败的 held-out `sid` 或题材。工具不提供“多数通过”
兜底，也不会把 `null`、缺字段或小样本当作零失败。

## 4. 可复验身份与命令

protocol、按 `sid` 排序的输入摘要和输出 payload 都使用 UTF-8、JSON 键排序、无多余空白的
规范序列化后取 SHA-256。manifest 分别记录 `protocol_sha256`、`input_sha256` 和
`output_sha256`；`output_sha256` 的计算范围是除它自身外的完整 manifest。输入文件顺序不影响
结果。

```powershell
python -X utf8 tools/validate_style_transfer.py evaluate `
  --protocol D:\private-eval\style_protocol.json `
  --summary D:\private-eval\book_01.json `
  --summary D:\private-eval\book_02.json `
  --summary D:\private-eval\book_03.json `
  --summary D:\private-eval\book_04.json `
  --summary D:\private-eval\book_05.json `
  --summary D:\private-eval\book_06.json `
  --manifest D:\private-eval\style_transfer_manifest.json
```

同一输出路径只接受语义完全相同的恢复写；不同结果不会覆盖旧 manifest。复核时重新提供冻结
protocol 与全部逐书摘要，工具会重算三个哈希和每项判定：

```powershell
python -X utf8 tools/validate_style_transfer.py validate `
  --protocol D:\private-eval\style_protocol.json `
  --summary D:\private-eval\book_01.json `
  --summary D:\private-eval\book_02.json `
  --summary D:\private-eval\book_03.json `
  --summary D:\private-eval\book_04.json `
  --summary D:\private-eval\book_05.json `
  --summary D:\private-eval\book_06.json `
  --manifest D:\private-eval\style_transfer_manifest.json
```

发布 manifest 只含 protocol/输入/输出哈希、冻结阈值、`sid/source_sha256/genre`、字段聚合指标、
leave-one-book 与跨题材结果，以及字段的 `diagnostic|hard` 资格；不含私有素材位置、正文、
prompt 或原始 response。
