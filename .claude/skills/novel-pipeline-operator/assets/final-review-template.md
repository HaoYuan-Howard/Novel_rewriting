# 终审包 · <书名>（<sid>）

> 交付状态：`<ready | ready_with_disclosures | stopped>`
>
> 建议结论：<一句话说明是否建议用户阅读/接收成书>
>
> 成书：`<绝对路径或未生成>`
>
> 本包对应成书 SHA1：`<sha1 或不适用>`

## 1. 用户终审入口

### 需要用户重点审阅

1. <最重要的内容风险或“无额外重点”>
2. <第二项；没有则删除>

### 不需要用户重做的中间工作

- stage0 剔除审阅：<agent 已代批结论>
- stage6 开篇文案选择：<所选候选与理由>
- stage7 人名映射审阅：<agent 已代批结论>

### 尚未收口事项

| 事项 | 影响 | 当前证据 | 用户是否必须决定 |
|---|---|---|---|
| <无则写“无”> | <...> | <...> | <是/否> |

## 2. 交付与完整性

| 检查 | 结果 | 分母/事实 |
|---|---|---|
| 源书与成书字数 | <pass/fail> | <源字数> → <成书字数>（<比值>） |
| 章号 | <pass/fail> | <N/N>，独占数字行恰为 `1..N` |
| 批准文案前缀 | <pass/fail> | <逐字相等/差异位置> |
| 所有 subs 旧键归零 | <pass/fail> | <0/M；例外> |
| 旧 canonical/alias/简称补扫 | <pass/fail> | <扫描词数、命中与解释> |
| 新旧混名 | <pass/fail> | <扫描结果> |
| 正式章唯一且同契约 | <pass/fail> | <N/N；重复/缺失> |
| 成书指纹一致 | <pass/fail> | <盘上 sha1 vs 产物 sha1> |
| 最终 `state` / `inbox` | <pass/fail> | <状态摘要；八类计数> |
| 活动终态文件 | <pass/fail> | <排除 `_rounds/` 后的计数与路径> |

## 3. Agent 代批记录

### stage0.approve

- 决定：<approve / reject-and-repair / stop>
- 检查对象：<路径 + sha1/round>
- 机械证据：<HARD 命中/分母、保留率、warning>
- 语义审阅：<剔除段逐类结论、正文首尾与边界>
- 修复与复验：<无或命令/结果>

### stage6.approve

- 决定：`--pick <N>` / <其他>
- 所选对象：<label + 定稿文本完整 SHA-256>
- 批准凭据：<actor + reason + 提交/当前 subject_sha256 一致>
- 机械证据：<所选候选 HARD/info/分母/freshness>
- 语义证据：<质量/矛盾决议、evaluator 指纹、分母/freshness>
- 落选项：<逐候选缺陷>
- 判断理由：<与 blueprint、体裁、首章的一致性>
- 修复与复验：<无或 hook_edit + semantic 结果>

### stage7.approve

- 决定：<approve / reject-and-repair / stop>
- 检查对象：<name_map 路径 + round + 当前决策面完整 SHA-256>
- 批准凭据：<actor + reason + 提交/当前 subject_sha256 一致>
- 机械证据：<HARD/info/unver 与分母>
- 人工审阅：<新旧名、kept、marker、漏网 surface>
- 修复与复验：<无或 name_map_edit + approve/assemble 结果>

## 4. 各层质量读数

| 节点/报告 | freshness | HARD | info | metric | unver | skipped | 结论/处置 |
|---|---|---:|---:|---:|---:|---:|---|
| <stage0...stage7 最新报告逐行> | <...> | <命中/分母> | <...> | <...> | <...> | <...> | <...> |

### 修复记录

| 根因层 | rule/终态 | 路由 | 命令/人工输入 | 退出码 | 最新验证 |
|---|---|---|---|---:|---|
| <无则写“无”> | <...> | <deterministic/llm/human-new-revision/human-revalidation-audit> | <...> | <...> | <...> |

## 5. 残红、不可验证项与实现边界

| 类型 | rule/来源 | 影响对象 | agent 判断 | 为何可交付或为何停止 |
|---|---|---|---|---|
| <落选残红/unver/human_only/open-gap；无则写“无”> | <...> | <...> | <...> | <...> |

不得把 `unver` 或分母为零改写为“已检查无问题”。同一输入的语义判断冲突要同时列出。

## 6. 调用与耗时账本

口径：`<base>/_service/ledger.jsonl` 是最终成本的**唯一权威源**。只取本 sid 且
`outcome == "ok"`；`cached == true` 单列缓存次数，不重复累计其携带 token；不把
`_provenance.usage`、日志或历史报告混加，不写金额。

| 节点 | 实付调用 | 缓存命中 | prompt tokens | completion tokens | reasoning tokens | 可证实耗时 |
|---|---:|---:|---:|---:|---:|---:|
| <逐节点> | <...> | <...> | <...> | <...> | <...> | <...> |
| **合计** | <...> | <...> | <...> | <...> | <...> | <...> |

- 付费授权：<granted/not_granted + 证据来源；交互式默认授予时写“调用本 skill 即授予”，被收回则写收回原文>
- 正文外发授权：<granted/not_granted + 证据来源；同上，两项分别写，不合并>
- 缓存/中断说明：<无或事实>

## 7. 恢复路径与证据指针

### 如需恢复

```text
<ready 时写“不需要恢复”；否则给出下一条可复制的 preview/CLI 命令>
```

### 证据

- 书目录：`<绝对路径>`
- 成书：`<绝对路径或未生成>`
- 最新 state/inbox：<命令与摘要>
- 活动人工输入：<路径或无>
- 历史轮次：`<novel-dir>/**/_rounds/`（只读）
- 账本：`<base>/_service/ledger.jsonl`
- agent 运行记录/日志：<路径或不适用>

### 退出码序列

`<复制 collect_final_evidence.py 的 exit_sequence_compact_text；它按执行顺序列 node:rc，修复轮包含在内，相邻同项用 ×N 无损压缩>`

### 机器事实锚点

运行 `scripts/collect_final_evidence.py --base <绝对 base> --sid <sid>`，把输出中的
`review_anchor_line` 原样粘贴到这里；`scripts/validate_review.py` 会与当前盘面逐项对账。

`<review_anchor_line>`
