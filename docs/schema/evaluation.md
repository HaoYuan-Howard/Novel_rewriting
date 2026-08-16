# 语义评测与 ground-truth 契约

本契约把「人工真值」「校准 profile」与运行时模型判决分开。自动语义裁决不是默认能力：
ground truth、模型、prompt、schema 或 decoder 任一指纹变化，或固定门槛中任一项未达标，
该能力都保持 `human-only`，运行时输出 `UNVER`。

## 一、数据边界

ground-truth 工作流只搬运以下内容：

- 稳定 `case_id`、能力、题材；
- `subject_sha256`；
- `{artifact, subject_sha256, start, end}` 证据定位；
- 人工 `PASS｜FAIL｜UNVER` 标签与理由；
- 最终仲裁的 `PASS｜FAIL`。

原文片段、完整 prompt、模型原始响应和书稿正文不进入这些 JSON。证据定位指向私有语料，
`subject_sha256` 用于闭合「标的是哪一份内容」。工具的输入 schema 使用未知字段拒收，
所以把 `text`、`prompt`、`response` 一类字段塞进 catalog 会直接失败。

只有最终的 `GroundTruthManifest` 是无正文的发布面。候选 catalog、两份盲包、管理员 index、
两份标签、agreement report、冲突包和仲裁文件都是私有工作文件，不提交仓库。actor/annotator
均为操作者自报身份，不表示已认证身份。

## 二、八类能力与样本硬门

能力集合固定为：

1. `event_order`
2. `verbatim_fidelity`
3. `hard_number_fidelity`
4. `reference_boundary`
5. `secret_timeline`
6. `world_rule_consistency`
7. `trim_equivalence`
8. `style_transfer`

`prepare` 前每类至少有 100 个 held-out 候选，并至少有四个题材各 10 例。仲裁后的最终
manifest 对每类再次执行更强的硬门：总数至少 100，`PASS`、`FAIL` 各至少 40；每个标签
都至少覆盖四个题材，且该标签下出现的每个题材至少 10 例。`case_id` 必须全局唯一。

## 三、离线双标与仲裁

实现位于 `tools/ground_truth_workflow.py`，不联网、不加载 LLM。所有命令都是 fail-closed；
已存在的输出只有在 JSON 语义完全相同时才视为可恢复的同一次操作，不同内容不会被覆盖。

### 3.1 从私有 corpus 构造候选 catalog

`tools/build_ground_truth_catalog.py` 是候选 catalog 的真实离线入口。它不联网、不加载模型、
不猜书名对应的题材，也不接受 `..` 或绝对路径逃出私有根。首选入口是 schema-neutral 的
case-bundle adapter：原始语料、旧 schema 产物、当前产物或人工整理材料都先在私有目录里变成
同一份无答案 subject 形状，正文只存在于这里：

```json
{
  "ability": "event_order",
  "question": "按冻结口径显示给标注者的问题",
  "panels": [
    {"role": "来源", "text": "私有正文……"},
    {"role": "候选", "text": "私有正文……"}
  ]
}
```

先用不泄露正文的命令取得该私有文件的 raw/subject 双哈希：

```powershell
python -X utf8 tools/build_ground_truth_catalog.py inspect-subject `
  --corpus-root D:\private-eval\corpus `
  --artifact private-subjects\case-a.json
```

bundle inventory 只列 locator/hash 和显式人工题材，不含正文或 expected。`family_id` 表示同一个
原始 subject 的变体族，不表示一本书；`variants` 可以是从同一来源可复算出的原样/扰动候选，
但 `variant_id` 必须是不编码 PASS/FAIL 的不透明身份：

```json
{
  "inventory_version": 1,
  "dataset_id": "held-out-20260816-v1",
  "rubric_sha256": "64 位小写 SHA-256",
  "created_at": "2026-08-16T12:00:00Z",
  "families": [
    {
      "family_id": "family-a001",
      "ability": "event_order",
      "genre": "由人显式填写的题材",
      "variants": [
        {
          "variant_id": "v_a001",
          "subject": {
            "artifact": "private-subjects/case-a.json",
            "artifact_sha256": "64 位小写 SHA-256",
            "subject_sha256": "64 位小写 SHA-256"
          }
        }
      ]
    }
  ]
}
```

```powershell
python -X utf8 tools/build_ground_truth_catalog.py build-bundles `
  --corpus-root D:\private-eval\corpus `
  --inventory D:\private-eval\bundle-inventory.json `
  --catalog D:\private-eval\candidates.json `
  --case-store D:\private-eval\case-store `
  --audit D:\private-eval\catalog-audit.json
```

生成器每能力确定性选 100 个候选，先保证至少四个显式题材各 10 例，再从其余全部题材补足；
同一 family 至多选一个 variant，避免原样/扰动成对进入盲包。它不把 family 当作书，也不预填
正负比例——最终 `PASS/FAIL 各 >=40`、每标签四题材各 10 例仍由两次真实人工标签和最终 manifest
硬门验证。任一能力候选不足时退出码为 `2`，只写无正文 audit，不写半份 catalog/case-store；
`source_issues` 和每能力的 `source_families_by_genre/blockers` 就是补材料清单。

仓库还提供一个可选的 current-pipeline adapter：inventory 显式列每本书的 `book_id`、
`genre`、`directory`、`rewrite_modes` 后，`build` 从当前版本 Stage1/2/3/5 自动构造八类
可复算候选。它便于
current corpus，但不是 ground truth 的付费重建前置；原始或旧产物应走上面的 `build-bundles`，
不能把旧形状偷偷伪装成当前 schema。

catalog 和盲包只带不透明 `private://ground-truth/<dataset-hash>/<case-id>`、subject hash 与
题材/能力。生成的 resolver case-store 也只保存私有 subject locator、源文件 SHA-256 和
family/variant 身份；正文仍只在输入的私有 subject store。current-pipeline adapter 的 resolver
只保存 JSON pointer、源文件 SHA-256 和确定性操作参数，同样不复制原文。
标注者在本机按盲包给出的 locator 与 hash 临时渲染一例；渲染只写 stdout，不另存正文，且
源文件任何一个字节变化都会因 artifact/subject 双哈希失配而拒绝：

```powershell
python -X utf8 tools/build_ground_truth_catalog.py render `
  --corpus-root D:\private-eval\corpus `
  --case-store D:\private-eval\case-store `
  --artifact private://ground-truth/<dataset-hash>/<case-id> `
  --subject-sha256 <blind-pack 中的 64 位 hash>
```

private subject store、inventory、resolver case-store、候选 catalog、盲包和渲染输出都是私有
工作面，均不提交。仓库内只允许最终无正文 manifest 与聚合 profile。

### 3.2 生成盲包

候选 catalog 顶层为：

| 字段 | 约束 |
|---|---|
| `workflow_version` | 当前恒为 `1` |
| `dataset_id` | 非空版本化身份 |
| `rubric_sha256` | 冻结口径的 64 位 SHA-256 |
| `created_at` | 显式给定；工具不偷偷写当前时间 |
| `cases[]` | `case_id/ability/genre/subject_sha256/evidence[]`，不含答案与正文 |

```powershell
python -X utf8 tools/ground_truth_workflow.py prepare `
  --catalog D:\private-eval\candidates.json `
  --out-dir D:\private-eval\round-1 `
  --seed round-1-frozen-seed
```

输出 `blind_a.json`、`blind_b.json` 与 `index.json`。两份盲包使用不同的确定性别名和顺序，
均不含 `case_id` 或 expected；相同 catalog 与 seed 必须逐字段复现相同结果。管理员 index
保存两个 blind id 到 canonical `case_id` 的映射及两份 pack 的哈希。

### 3.3 导入两名独立标签

每份 `LabelFile` 必填：

- `workflow_version=1`、`dataset_id`、对应的 `pack_id`；
- 非空 `annotator`（自报身份）；
- `labels[]`，每个 blind id 恰好一次；
- 每条标签含 `verdict=PASS｜FAIL｜UNVER`、非空理由及零项或多项证据定位。

A/B 的 annotator 字符串必须不同，两份标签都必须完整覆盖各自 pack。导入命令为：

```powershell
python -X utf8 tools/ground_truth_workflow.py reconcile `
  --index D:\private-eval\round-1\index.json `
  --labels-a D:\private-eval\round-1\labels_a.json `
  --labels-b D:\private-eval\round-1\labels_b.json `
  --report D:\private-eval\round-1\agreement.json `
  --conflicts D:\private-eval\round-1\conflicts.json
```

一致度固定使用三分类 Cohen's κ，预先锁定门槛为 `0.8`；报告同时给出原始一致率，避免
把 κ 与 percent agreement 混称。两人同为 `UNVER` 仍进入仲裁。κ 低于 0.8 时工具仍写出
诊断报告与冲突包，但冲突包状态为 `rubric_relabel_required`，进程返回码为 2，任何仲裁结果
都不能绕过「修订口径并重新独立标注」。

### 3.4 第三方仲裁与 manifest

仲裁文件以 `conflict_sha256` 乐观锁绑定当前冲突集合，含自报 `adjudicator` 与
`decisions[]`。仲裁者必须与两名 annotator 不同；每个冲突必须恰好处理一次，不能缺、不能多、
不能重复。最终仲裁只允许 `PASS｜FAIL`。

```powershell
python -X utf8 tools/ground_truth_workflow.py finalize `
  --index D:\private-eval\round-1\index.json `
  --labels-a D:\private-eval\round-1\labels_a.json `
  --labels-b D:\private-eval\round-1\labels_b.json `
  --report D:\private-eval\round-1\agreement.json `
  --adjudications D:\private-eval\round-1\adjudications.json `
  --out-manifest D:\private-eval\round-1\manifest.json
```

无冲突时 `decisions=[]` 仍是显式、合法的仲裁文件。最终 `dataset_sha256` 的规范输入为：

```json
{"dataset_id":"…","rubric_sha256":"…","cases":["按 case_id 排序的 GroundTruthCase …"]}
```

按 UTF-8、JSON 键排序、无多余空白序列化后取 SHA-256。独立复核命令会重新计算该身份：

```powershell
python -X utf8 tools/ground_truth_workflow.py validate --manifest D:\private-eval\round-1\manifest.json
```

### 3.5 Stage2 全局逐项复核与 s2 gate

`stage2/global_review.json` 不是对「已经抽出来的行」逐行盖章后就算完整。确定性枚举器除人物
辨识度、称谓语境、每条世界规则 kind/version、每条秘密 fact/known-by/reveal 外，始终增加两个
集合级 subject：`canon.world_rules` 的 `world_rule_set_complete` 与 `canon.secrets` 的
`secret_set_complete`。即使数组为空，这两项仍存在且初始为 `pending`；只有人工对照来源后才能
裁为 `PASS｜FAIL｜UNVER`。因此「一行都没抽到」不能借空循环变成全绿。

每次生成或编辑 `GlobalGrounding` 后，review 的 `source_sha256` 绑定去除 schema/provenance 后的
完整 typed subject。旧决定只在 `(item_id, kind, subject_path, subject_sha256)` 四项完全不变时
继承；变过的项回到 `pending`。读 API 与 s2 gate 不信任盘上 `approved` 推论，而是从当前
`GlobalGrounding` 重算完整 subject SHA 和现役必审 item surface：hash 过期、缺任一必审项、
顺序/身份不一致或存在非 PASS 项，`approved=false`，不能开 gate。

真实决策入口为：

```powershell
python -m novel_refactor.cli run stage2.global <sid> `
  --review-decisions <书内或服务私有目录中的 GlobalReviewDecisionSet.json>
```

同形服务入口为 `POST /api/novels/:sid/stage2/global-review`，读面为对应 GET。请求可批量提交，
但每项恰好一次，并必须带自报 `actor`、非空 `reason`、显式 `evidence[]` 及
`expected_subject_sha256`；这是乐观锁，不是认证身份。CLI 在写前再次读取当前完整底座并拒绝
过期/缺项 review。POST 只有 CLI exit `0`（当前完整面全部 PASS）才尝试盖 s2 章；exit `1`
表示决定已落盘但仍有 FAIL/UNVER/pending，作业为 `landed_red`，gate 保持关闭。Inbox 把三种
未闭合状态都列为 `verification`，GlobalScreen 提供逐项或批量提交入口。

## 四、自动 evaluator 启用门

`EvaluatorProfile` 必须恰有八个 `CapabilityMetrics`。每个能力行分别记录
dataset/model/prompt/schema/decoder 五项指纹；不能让八套不同 prompt/schema 共用一个
看似匹配的全局指纹。profile 还记录至少三次复跑和「阈值在评测前已锁定」。每项能力只有同时满足以下条件才可
`enabled=true`：

- PASS precision 的 95% Wilson 下界 `>=0.90`；
- FAIL precision 的 95% Wilson 下界 `>=0.90`；
- 违规 recall 的 95% Wilson 下界 `>=0.90`；
- 证据闭合率 `>=0.95`；
- 三次复跑一致率 `>=0.95`。

运行时还必须是两个盲评 judge 同结论、均非 `UNVER` 且证据闭合，才产生 automated
`PASS` 或 `FAIL`。profile 缺失、指纹漂移、能力未启用、两 judge 分歧或证据不足一律降为
非自动 `UNVER`，进入人工 verification，不把「没有资格自动判断」伪装成质量通过。

### 4.1 受限校准 runner

仓库不内置八类通用语义口径，也不会从现役某一个 Stage 的 prompt 猜出另外七类的定义。
`tools/restricted_evaluator.py` 必须读取一份版本化的**私有** prompt bundle；少一个能力、bundle
的 `rubric_sha256` 与最终 manifest 不同、两名 judge 的 identity 相同，或两份 system surface
相同都会在预算、cache、ledger 和网络之前关闭式拒绝。prompt bundle、逐例响应、cache、ledger、
history、partial 与 `EvaluationRun` 都是私有评测材料，不提交；只有聚合 profile 可进入仓库。

bundle 固定模型、单一 effort/thinking、单次输出上限，并为每个能力给出两份真正不同的私有
system 定义。示意形状如下；`system` 的正文必须来自已冻结的产品口径，不能照示例占位文本运行：

```json
{
  "bundle_version": 1,
  "bundle_id": "private-evaluator-20260816-v1",
  "rubric_sha256": "与最终 manifest 相同的 64 位 SHA-256",
  "model": "deepseek-v4-pro",
  "reasoning_effort": "high",
  "thinking": true,
  "max_output_tokens": 800,
  "abilities": [
    {
      "ability": "event_order",
      "judge_a": {"identity": "judge-a", "system": "私有冻结口径 A"},
      "judge_b": {"identity": "judge-b", "system": "私有冻结口径 B"}
    }
  ]
}
```

`abilities` 必须恰好覆盖八类。工具不把 manifest 的 `expected`、canonical `case_id` 或人工理由
放进模型请求。每名 judge 只看到不透明 blind token、能力、问题与编号 panel；A/B 各自使用
不同 system identity 和按 `run_id/slot/case_id` 确定性生成的不同 case 顺序。模型 wire shape
只能返回 `verdict`、`evidence[].panel/quote` 与 `reason`，不能自报 artifact、subject hash 或
offset。代码要求 quote 在指定 panel 中恰好出现一次，才把它换成
`private://…#panels/<n>/text + subject_sha256 + Unicode 码点 start/end`；越界、不连续、找不到、
重复出现或多余字段都使该次调用失败，不自动重采样。

默认命令只读所有 resolver，按源 artifact SHA-256 与 manifest subject SHA-256 双重闭合重建
subject，并使用固定 tokenizer 对所有请求做预算预检，然后打印 JSON preview：

```powershell
python -X utf8 tools/restricted_evaluator.py `
  --manifest D:\private-eval\round-1\manifest.json `
  --corpus-root D:\private-eval\corpus `
  --case-store D:\private-eval\case-store `
  --prompt-bundle D:\private-eval\evaluator-prompts-v1.json `
  --output-dir D:\private-eval\eval `
  --run eval-run-a=2026-08-16T13:00:00Z `
  --run eval-run-b=2026-08-16T14:00:00Z `
  --run eval-run-c=2026-08-16T15:00:00Z
```

preview 按 run、能力与总计展示 exact calls、精确 input token、声明的最大 output token、
response schema hash、三项 runtime fingerprint 及 wire hard cap，并给出冻结的执行 argv。
固定 `batch_size=1`，每 case 每 run 恰好调用 A/B 各一次；因此 `N` 个最终 case 的三轮总硬上限
是 `6N` 次 wire，八类各 100 例时最低为 4,800 次。单一显式 rung 表示没有 fallback，
`max_retries=0` 表示 429、5xx、超时也不重发；任何一笔失败立即停下整个 campaign。

真执行必须在同一命令追加两个开关，缺一不可：

```powershell
# 先逐项核对 preview 与 frozen_execute_argv，再执行；这一步会产生付费调用。
python -X utf8 tools/restricted_evaluator.py <上面的全部冻结参数> --execute --yes
```

执行仍统一经过 `LlmClient.ask_json`：显式单 rung、固定 tokenizer 预算闸、独立空
`ResponseCache`、私有 `CostLedger`、heartbeat 与 provider prompt hit/miss 都保留。三轮分别使用
不同 run identity、cache 目录、ledger/history/result/partial 路径。runner 在创建这些文件前会
重新解析全部 resolver、重算双哈希、fingerprint 和整批预算，拒绝 preview 后的任何漂移。
所有结果文件 write-once；失败轮只写保留已完成 judge 的 partial 与 append-only history，已有
result/partial/history/ledger 或非空 cache 会阻止重跑，绝不覆盖历史，也不自动继续下一轮。

每轮完成后产出 `tools.build_evaluator_profile.EvaluationRun`，并恰好覆盖 manifest 全集。三轮
bindings 必须逐能力完全相同；prompt hash 绑定两份最终 system surface 与固定 user envelope，
schema hash 绑定唯一 wire model，decoder hash 绑定 batch、单 rung、retry=0、effort/thinking、
max output、blind 排序及证据回查版本。即使某能力暂无现役自动 consumer，也不需要为通过本轮
而强行启用；聚合 profile 可如实保留 `enabled=false`。

### 4.2 `trim_equivalence` 运行时闭环

收缩能力的 fingerprint 冻结三份 system surface（结构化收缩、judge A、judge B）、
`ChapterResponse + TrimJudgeResponse` 两份响应 schema，以及 `TRIM_DECODER_IDENTITY` 中的
生成/盲评 token 上限、收缩低推理阶梯、运行时 judge 阶梯、轮数、证据/确定性门和合并器版本。
任一字节或档位变化都使该能力失配，且失配发生在
收缩生成、预算、缓存、ledger 与 transport 之前。

候选按 producer slot 校验和组装。代码在任何 judge 调用前重算 before/after 的人物、地点、
因果、时间和结尾 `Claim`，并运行篇幅区间、严格变短、`trim_regressions` 及事件落点/顺序回归。
失败候选的 `TrimAudit.deterministic_passed=false`、`semantic=null`，因此盘上不能伪造一次
没有实际发生的语义裁决。机械通过后，两个盲评只返回 `before|after` 的字符 span；artifact
路径与 subject SHA-256 由代码补入，模型不能自报。PASS 必须同时闭合 before 和 after 证据。
最终采用还要求 `SemanticDecision.subject_key == TrimAudit.after_sha256`、双 judge 同 PASS 且
`automated=true`。FAIL、UNVER、分歧、证据越界或调用失败均 `adopted=false`，正式章继续使用
pretrim；audit 仍保留候选 hash、两组 claims、该能力的完整 evaluator fingerprint、judge 决定
与回滚理由供离线复算。`semantic` 与 `evaluator_fingerprint` 必须同时存在或同时为空，不能只凭
一个可复用的 `profile_id` 声称当时匹配。

三次或更多私有评测完成后，用离线聚合器生成 profile：

```powershell
python -X utf8 tools/build_evaluator_profile.py `
  --manifest D:\private-eval\round-1\manifest.json `
  --run D:\private-eval\eval\run-1.json `
  --run D:\private-eval\eval\run-2.json `
  --run D:\private-eval\eval\run-3.json `
  --profile-id evaluator-20260816-v1 `
  --calibrated-at 2026-08-16T12:00:00Z `
  --out D:\private-eval\eval\profile.json
```

`tools/build_evaluator_profile.py` 本身不联网。它要求每轮恰好覆盖 manifest 全集、两名
judge 身份不同、每能力指纹在各轮完全一致，并取三轮各项 Wilson 下界和证据闭合率的
最差值；复跑一致率按每个 case 的合并结论计算。任一缺例、指纹漂移或少于三轮直接拒绝，
门槛未达则该能力如实保留 `enabled=false`。私有逐例 run 不提交，只发布无正文的聚合 profile。
