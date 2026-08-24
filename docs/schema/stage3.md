# stage3 · 机械基线（v2）

现役产物是 `stage3/style_grounding.json`，生产节点是免费
`stage3.style`。对应需求为 `R-4.1`～`R-4.15`。

## 一、职责

Stage3 v2 从 Stage1 的正文与原作文案中确定性计算机械形态。它不再保存
`voice`、`surface_habits`、`hook_style` 或 `chapter_styles`，也不再拥有
定性 prompt、模型响应、attempt、repair 或 `stage3/style_edit.json`。

删除依据分两层记录：

- 冻结的预注册 Stage6 A/B 统计决议仍为 `inconclusive`，不回写历史；
- 产品负责人随后采用“无定性包时至少有一篇事实安全且足以吸引人读下去即可删除”的产品门槛。
  真实 B 臂 10 次运行中，强认可候选覆盖 8 次，事实复核通过，因此明确授权删除。

实验、真实产物和产品裁决见 `docs/stage3-stage6-style-ab-result.md`。

## 二、v2 形状

```json
{
  "_schema_version": 2,
  "measurable": {
    "body": {
      "chars": 0,
      "paragraphs": 0,
      "sentences": 1,
      "avg_paragraph_chars": 0.0,
      "paragraph_sep_chars": 0.0,
      "avg_sentence_chars": 0.0,
      "dialogue_ratio": null,
      "dialogue_ratio_applicable": false,
      "quote_styles": [],
      "chapter_chars": {"min": 0, "max": 0, "avg": 0.0},
      "per_chapter_avg_sentence": {"min": 0.0, "max": 0.0}
    },
    "hook": {
      "chars": 0,
      "paragraphs": 0,
      "sentences": 1,
      "avg_paragraph_chars": 0.0,
      "paragraph_sep_chars": 0.0,
      "avg_sentence_chars": 0.0,
      "dialogue_ratio": null,
      "dialogue_ratio_applicable": false,
      "quote_styles": []
    }
  },
  "_provenance": {
    "node": "stage3.style",
    "code": "<git commit>",
    "root_clean_sha1": "<40 hex>",
    "inputs": {},
    "measurement_contract": "stage3-measurable-v1"
  }
}
```

上例省略 `inputs` 内两条 `InputEdge` 的具体指纹；它们仍是实际产物的必填来源边。
`FreeProvenance` 的其余可选公共字段按 `null` 不落盘。schema
由 `contracts/schema/stage3.py` 唯一声明，路径和版本分别由
`contracts/artifacts.py`、`contracts/schema/__init__.py` 登记。

### 字段口径

| 字段 | 证据 | 口径 |
|---|---|---|
| `chars` | 码·契 | 段内字符总数；段间换行另计 |
| `paragraphs` | 码·契 | 段数 |
| `sentences` | 码·契 | 句数，至少为 1 |
| `avg_paragraph_chars` | 码·契 | 平均段长 |
| `paragraph_sep_chars` | 码·契 | 平均段间分隔字符数 |
| `avg_sentence_chars` | 码·契 | 平均句长 |
| `dialogue_ratio_applicable` | 码·契 | 是否适合计算对白比例 |
| `dialogue_ratio` | 码·契 | 对白比例；不适用时为 `null` |
| `quote_styles` | 码·契 | 实际观测到的引号体 |
| `body.chapter_chars` | 码·契 | 逐章字符数 min/max/avg |
| `body.per_chapter_avg_sentence` | 码·契 | 各章平均句长 min/max |

正文和原作文案分别测量，不共享中间量。正文额外保存逐章统计；原作文案不伪造
不存在的逐章维度。

## 三、来源与新鲜度

Stage3 的真实 artifact 上游只有：

- `stage1/main_text.json` 的章节正文；
- `stage1/hook_copy.json` 的原作文案正文。

Stage2 全书抽取不再是 Stage3 输入。当前测量算法口径由
`contracts/invariants.py::STAGE3_MEASUREMENT_CONTRACT` 唯一定义（当前为
`stage3-measurable-v1`），并进入
`novel/consumption.py::stage3_main_text` 的 consumed 投影。

`_schema_version` 与 `measurement_contract` 是两根轴：

- schema 版本管 JSON 形状；v1、无版本、高版本和夹带退役字段的伪 v2 都拒读；
- 测量契约管计算口径；旧口径的 v2 仍可读，但 freshness 判为 stale。

这样算法口径变化而形状不变时，旧数字不会被误当成新鲜，也不会被误报为文件损坏。

## 四、生产、预览与复检

现役行为由 `contracts/invariants.py::NODES['stage3.style']` 与
`novel/stages/stage3/style.py` 共同约束：

| 行为 | 写盘 | LLM | 结果 |
|---|---:|---:|---|
| 默认生产 | 是 | 0 | 按当前输入重算并写入 v2 |
| `--measure-only` | 否 | 0 | 返回当前 `Measurable` 预览 |
| `--check-only` | 否 | 0 | 比较盘上 v2 与当前重算结果 |

当前注册表共 **1 条**：**1 hard**。唯一现役规则是 `measurable_drift`。
缺字段、多字段和非法值在进入检查前已由
strict schema 拒绝，不再注册一条结构上恒绿的“完整性规则”。

## 五、下游最小消费面

### Stage5：正文九叶

`novel/consumption.py::stage5_style` 只投影：

- `body.avg_sentence_chars`
- `body.avg_paragraph_chars`
- `body.paragraph_sep_chars`
- `body.dialogue_ratio_applicable`
- `body.dialogue_ratio`
- `body.quote_styles`
- `body.chapter_chars.avg`
- `body.per_chapter_avg_sentence.min`
- `body.per_chapter_avg_sentence.max`

它们用于正文句段档位、段间分隔、对白适用性/比例、引号归一、平均章长与章间
句长范围。Stage5 不消费 hook 机械块。

### Stage6：原作文案五叶

`novel/consumption.py::stage6_style` 只投影：

- `hook.chars`
- `hook.avg_sentence_chars`
- `hook.avg_paragraph_chars`
- `hook.quote_styles`
- `hook.dialogue_ratio`

Stage6 继续直接消费原作文案、目标字数、段间分隔、收尾形态和重写开篇；这些不是
Stage3 定性画像。生成、机械复检和 freshness 共用上述五叶，语义复核不再重复发送
Stage3 包。

## 六、shape break 与审批

v2 是破坏形状升级，不迁移、不手补：

- 普通旧书由免费 Stage3 默认生产重建；
- v1 正式文件和 v1 `.INVALID` 均不会进入 v2 `_rounds`；
- 当前 v2 写盘后，绑定旧 Stage3 字节的 s3 证明自然失效；
- s3 人工闸保留，名称为“Stage3 机械基线确认”，继续绑定当前产物完整 SHA；
- 本次两本实验书已按正式 trash/archive 路径移出 live discovery，明确不重建。

API `StyleView` 与前端 Stage3 屏只展示 stored/current 机械读数、机械档位和漂移状态，
不再暴露任何定性字段。历史实验工具可读取隔离目录里的冻结 v1 字节，但不得反向成为
生产 Stage3/Stage6 的依赖。
