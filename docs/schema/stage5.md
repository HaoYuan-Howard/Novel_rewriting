# stage5 产物 schema

Stage5 负责逐章闭卷写作与全书零成本验收，是全链唯一产出正文、也是唯一保留 LLM
返工回路的层。对应 `R-7`（写作）与 `R-8`（验收 / 收敛）。读法与证据列见
`docs/schema/_README.md`；路径字面以 `contracts/artifacts.py::ARTIFACT_PATHS` 为准，
Pydantic 载体以 `contracts/schema/stage5.py` 为准。

## 一、现役落盘面：单臂、单一正文位置

```text
stage5/chNN.json                 canonical 正式章（schema v2）
stage5/chNN.INVALID.json         落盘闸门拦下的问题稿（Chapter v2）
stage5/chNN.rejected.json        两次修订候选都相对旧稿退化的证据（v1）
stage5/_rounds/...               被替代正式稿、INVALID、rejected 与 Review 的不可变历史
stage5/review_<n>.json           全书确定性验收（schema v2，0 LLM 调用）
stage5/trim_audit_chNN.json      超上限但未自动裁剪的事实（schema v2）
stage5/trim_resolution_chNN.json 人工保留 pretrim 的 hash-bound 决定（schema v2）
stage5/wontfix.json              人工豁免账（v1）
stage5/draft.md                  canonical 章集合的 UTF-8 纯投影
```

现役没有 `stage5/<mode>/...`，也没有深 / 浅两条可执行写作臂。`deep`、`shallow`、
`mode`、`depth` 只会出现在旧目录或不可变的旧作业 / 队列记录里；运行时把
`stage5/deep/`、`stage5/shallow/` 识别为 `legacy_incompatible`，只读保留，绝不选一臂
冒充 canonical，也不自动迁移。存量正文只能由 `stage5.write` 在冻结的 Stage0～4 上游上
重新生成。

现役命令面由 `contracts/invariants.py::NODES` 唯一发布：

| 目的 | canonical 跑法 | 费用 |
|---|---|---:|
| 写全部缺章 | `run stage5.write <sid> --base <base> --yes` | 付费 |
| 定向重写 | `run stage5.write <sid> --chapters 3,7 --base <base> --yes` | 付费 |
| 根据 fresh Review 修订 | `run stage5.write <sid> --revise --chapters 3,7 --base <base> --yes` | 付费 |
| 预览 prompt | `run stage5.write <sid> --dry-run --chapters 3 --base <base>` | 0 |
| 重判正式章 / 问题稿 | `--recheck` / `--revalidate --actor ... --reason ...` | 0 |
| 人工整章写回 | `--apply-edit <ChapterResponse.json>` | 0 LLM |
| 只重建全书投影 | `--rebuild-draft` | 0 |
| 生成最新 Review | `run stage5.review <sid> --base <base>` | 0 |
| 读取已有 Review | `run stage5.review <sid> --check-only --base <base>` | 0 |

不带 `--chapters` 就是全书，不需要也不接受 `--deep`、`--shallow-rest` 或 `--mode`。
`novel/orchestrator/nodes.py` 在预览与真跑共用的命令构造口关闭式拒绝 `mode`，CLI/API
也不再提供“选深度章”入口。

### 1.1 Stage3 → Stage5 只投影正文九叶机械基线

`novel/consumption.py::stage5_style` 是唯一消费入口。它从 Stage3 v2 的
`measurable.body` 精确保留：`avg_sentence_chars`、`avg_paragraph_chars`、
`paragraph_sep_chars`、`dialogue_ratio_applicable`、`dialogue_ratio`、`quote_styles`、
`chapter_chars.avg`、`per_chapter_avg_sentence.min`、`per_chapter_avg_sentence.max`。
`review.load_book` 只把这九个叶子放进 `Book.measurable`；结构上没有 `voice`、
`surface_habits`、`chapter_styles` 或 `hook_style`，prompt 也不能接收整份 Stage3 包。
Stage2 的 secrets 仍从剥除原文 evidence 的全局事实投影读取，不因收窄 Stage3 消费面而缩小检查面。

### 1.2 Stage4 → Stage5：只执行新场景设计

写手收到 `CtxChapter.semantic_contract`、`chapter_rewrite_brief` 与按 slot 排列的 `scenes[]`。
章级契约把 `information_delta`、`character_state_delta`、`relationship_delta` 与
`reader_knowledge_delta` 分栏下发，并同时给出完整 must-land/context、因果边和章首/章尾状态；
这些都是 Stage2 atom 的确定性人话投影，不由 Stage4/5 转述出第二份真相。
每场明确 purpose、进入/退出态、required/context facts、turn、舞台、人物、材料和预算；不接收
fact_id/beat_id/hinge_id，也不接收旧源序 `events[]`。slot 顺序是唯一硬顺序，场内事实数组没有
源文先后含义。

允许写手发明不改变持久状态的舞台表面：动作、走位、即时反应、符合身份且不新增信息的对白措辞、
感官/心理/场景调度和不会被后文引用的承接细节。禁止新增因果、决定、结果、关系、知识、往事、
秘密、规则及可被后文引用的新人物、地点、道具或事件。判准是写完后人物/关系/知识/世界状态是否
产生契约外变化，不是“原文没写的动作一律禁止”。

## 二、`stage5/chNN.json`（schema v2）

### 2.1 模型响应与正式产物不是同一个形状

写手与 `--apply-edit` 都提交 v2 `ChapterResponse`：

```json
{"segments":[{"slot":"s01","text":"……"},{"slot":"s02","text":"……"}]}
```

slot 必须非空、唯一，并与 producer-owned 计划 slot 集合恰好相等；最终顺序只取 producer
顺序，不信模型数组顺序。默认写作在 transport 前检查 prompt-private 字段，响应到达后先闭合
slot、逐槽归一，再跑内容判据。缺槽、重槽、多槽、未知键是显式终态；内容 HARD 不上移成
响应拒收，而是保留已付费正文并落 `.INVALID`，让 `--revalidate` / `--apply-edit` 提供 0 成本
恢复路。段落目标也遵守这条边界：完整响应不因段落数越界而被 transport 重采并丢弃，
而是在归一后由 `paragraph_budget_missed` 拦成 `.INVALID`。

正式 `Chapter` 的字段如下；`segments[]` 是唯一正文权威，整章 `text` 只在运行时 / API
由 `"\n\n".join(segment.text.strip())` 确定性投影，不再序列化第二份可漂移正文。

| 字段 | 类型 | 必填 | 取值面 / 语义 | 证据 |
|---|---|---:|---|---|
| `output_ordinal` | int | 是 | `>=1`；与文件名 NN 相同 | **契·码** |
| `from_source_ordinals` | int[] | 是 | 长度恒 1；输出章号恒等于源章阅读序 | **契·码** |
| `source_chars` | int | 是 | `>=0`；冻结写作任务书中的源章字数 | **契·码** |
| `budget_chars` | int | 是 | `>0`；冻结写作任务书中的计划预算 | **契·码** |
| `chars` | int | 是 | `len(text.strip())`，含正文内部空白 | **契·码** |
| `overlap_excl_verbatim` | float / null | 是 | 20 字窗口重合率，扣除契约要求逐字保留的片段；短于窗口为 `null`，不是 0 | **契·码** |
| `segments` | `ChapterSegment[]` | 是 | `{slot,text}`；slot 匹配 `^s\\d{2,4}$`、章内唯一 | **契·码** |
| `warns` | str[] | 是 | finding 的人读字符串投影；metric 常驻，非空本身不代表异常 | **契·码** |
| `errs` | str[] / 键不在 | 否 | `.INVALID` 中为阻断原因；人工正式稿可带其他 HARD 供 Review / wontfix 收口，但 `paragraph_budget_missed` 不得由 `--apply-edit` 正式化，正式下游拒绝任何当前 HARD | **契·码** |
| `_provenance` | object | 是 | LLM 轮次账或人工 `RoundFreeProvenance` | **契·码** |

v1 → v2 是破坏形状：删除 `mode`，路径从 `stage5/{mode}/chNN.json` 折平为
`stage5/chNN.json`。本仓没有迁移机制；旧稿不能补版本号或搬文件冒充 v2 产地。

### 2.2 正文归一、篇幅与来源账

`normalize_chapter` 在落盘前改标点、引号体和段落分隔；正式 `segments[].text` 记归一后的
字节。归一计数进入报告，不另落一份 `mech`；异常引号以 INFO `quote_unbalanced` 出线。

逐章目标带 `DRAFT_BAND=0.80~1.20`（相对本章预算），全书带
`BOOK_BAND=0.90~1.10`（相对全书源字数）。两把尺子回答不同问题，不得合并。
自然段目标由 `measure.target_paragraphs` 把逐章篇幅带除以
`avg_paragraph_chars + paragraph_sep_chars` 得出；prompt 与判据共用该函数。实际段数
由 `paragraph_count_report` 无条件报出，未落入明确区间由 `paragraph_budget_missed` HARD
拦截；`para_len_drift` / `band_drift` 仍只回答风格漂移，保持 INFO。
超上限不触发模型自动裁剪：正文原样保留，落 identity `TrimAudit`，由人工
`retain_pretrim` 或整章 `--apply-edit` 收口。

LLM 正式稿用 `RoundLlmProvenance`；人工整章写回用 `RoundFreeProvenance`，删除
`prompts/model`、记 `origin=human` 并继承上一轮 inputs。`--revalidate` 只按当前判据重判并
提升原问题稿，来源账继承，一个字未改就不得伪造新 prompt、模型或轮次。

## 三、Review（schema v2）与 currentness

`stage5.review` 不调 LLM，`review_<n>.json` 的 n 是盘上最大序号 +1。顶层字段：

| 字段 | 类型 | 语义 | 证据 |
|---|---|---|---|
| `sources` | object | 先报 `writer_context` 与 drafts 的来源状态，再报判定 | **契·码** |
| `judged` | `{rel: JudgedDraft}` | rel 必须是完整 canonical 路径 `stage5/chNN.json`；整文件 SHA-1 只审计，`consumed.text_sha1` 才判正文漂移 | **契·码** |
| `chapters[]` | `ReviewChapter[]` | 长度等于计划章数；缺稿章也在，并产 `draft_missing` | **契·码** |
| `book[]` | Finding[] | 全书 / 跨章结论 | **契·码** |
| `baselines` | `{rule:"k/N"}` | 当前稿的判据命中分母 | **契·码** |
| `baselines_identity` | `{rule:"k/N"}` | 同一把尺子量源章原文；只降 actionable，不删 finding | **契·码** |
| `counts` | `{sev:int}` | 产物侧是稀疏 Counter；API 必须现算四键恒在的投影 | **契·码** |
| `_provenance` | `ReviewProvenance` | 0 成本，含 `RULES` 指纹；成稿主体另记在 `judged` | **契·码** |

`novel/stages/stage5/review.py::review_status` 是 Review 能否驱动返工、豁免、s5 审批的唯一
currentness 出口。状态取值：

| 状态 | 含义 |
|---|---|
| `fresh` | schema、RULES、上游、canonical 章集与正文投影、`judged`、`draft.md` 全部相符 |
| `stale` | 已知某一主体或尺子发生变化 |
| `unknown` | 产物 / 来源账读不回或当前投影无法确定 |
| `missing` | 盘上没有 Review；首轮写作可继续，`--revise` / 豁免 / s5 审批不可据此继续 |
| `legacy_incompatible` | 发现旧 deep/shallow 布局，或最新 Review / live trim 低于当前 schema |

`require_fresh_review` 关闭式守住付费 `--revise` 与 wontfix；它只回答“这份结论是否当前”。
`require_accepted_review` 在此之上拒绝活动 `.INVALID`、缺正式章和任何当前 HARD，是 s5 审批、
Stage6、Stage7 与最终交付的共同闸。旧报告不得驱动新付费动作；恢复命令是先在 canonical
章集合上零成本重跑 `stage5.review`。旧 Review v1 仍含
`mode/mode_dir`；现役 v2 删除这两格，由 producer 免费重建，不做迁移。

## 四、审批服务记录：HTTP v3，盘上 v4

`ApprovalDoc.v=3` 是 HTTP 公共视图版本，字段为 `sid`、完整 `gate`、完整 `seen`、
`writing`、typed `structure` 与 `spawns`；它不再暴露 `deep`。`structure` 明示当前结构主体、
逐章 `accept / request_replan` 决定、历史与五态，且 `gate.s4=true` 只可能来自当前主体的
全章 `accept`。`POST .../approvals/deep` 路由和 `DeepSelectionRequest` 已退役；结构批准与
writing 授权是两个独立目的、两份 proof，后者只表达“允许当前 canonical 全书写作”。

`<base>/_service/approvals/<sid>.json` 不是产物，现役内部记录是
`_ApprovalRecordV4(v=4)`：完整 gate / seen、typed 结构决定与历史、`writing` 及各自
current-subject proof。proof 绑定根 `clean_text` SHA-1、正式主体完整 SHA-256 与决定面规范
SHA-256；bool 必须与 proof 是否存在逐格一致。结构 subject 或计划完整字节变化都会使 s4
即时失效，writing 与下游 gate 随之关闭。

旧 `_ApprovalRecordV2Legacy` / `_ApprovalRecordV3Legacy` 只供显式 backfill 读取，普通 GET /
启动绝不静默迁移。v3 没有 typed Stage4 结构裁决，最多保留能按当前盘面对证的 s0～s3；不得
凭旧 s4 或 writing 推造逐章决定，s4、writing 与 s5～s7 一律清空。v2 同样只迁移能对证的
前置 gate；v1 或损坏记录只能显式 reset/backfill，已有 Stage5 工作时 reset 受保护。

## 五、其余产物与 `draft.md`

### 5.1 rejected / trim / wontfix / 历史

- `RejectedRevision`（v1）保存 `output_ordinal`、非空 `why[]`、`fixes[[rule,evidence_key]]`
  和 1～2 份 `{chars,text}` 尝试；旧正式稿不被退化候选覆盖。
- `TrimAudit`（v2）只保存 ordinal、pretrim 正文 SHA-256 与 reason。没有候选、没有 adopted。
- `TrimResolution`（v2）唯一 action 是 `retain_pretrim`；同时绑定 audit SHA-256、当前
  `stage5/chNN.json` 路径与正文 SHA-256、actor/reason 和免费来源账。
- `wontfix.json`（v1）按 `(stage, chapter|book, rule, evidence_key)` 绑定人工理由；豁免不删结论。
- `_rounds/` 是审计历史，不计活动 `.INVALID` / rejected / stalled 余额；反推 schema 时仍必须读。

### 5.2 `stage5/draft.md`

它是 canonical `chNN.json` 的纯投影，不是第二份正文权威。字节形态：

```markdown
# 重构稿（M/N 章）
> ⚠ 计划里的第 [...] 章没有 canonical 成稿

## 第 1 章（源 [1] · 1234字 · 重合(扣除逐字要求) 0.012）

正文……
```

章按阅读序排列；缺章和计划外章显式写横幅。`--rebuild-draft` 只从 canonical 章零成本恢复
这份投影，不改变章产物；Review fresh 还要求盘上 draft 与当前投影逐字相同。

## 六、判据注册表

当前注册表共 **51 条**：**7 hard、27 info、12 metric、5 unver**。
repair 分桶为 **12 UNIQUE、12 FREE、27 NONE**；repair 不是 severity，只有 UNIQUE 才能在
`unresolved_rounds < 2` 时进入付费 `--revise`，FREE / NONE 不得被包装成自动修复。

| 判据 | sev | repair | 需求 |
|---|---|---|---|
| `draft_too_short` | hard | UNIQUE | `R-7.3` |
| `draft_truncated` | hard | UNIQUE | `R-7.3` |
| `meta_leak` | hard | UNIQUE | `R-7.3` |
| `names_absent` | info | UNIQUE | `R-7.4` |
| `verbatim_missing` | hard | UNIQUE | `R-7.4` |
| `verbatim_typo` | info | NONE | `R-7.4` |
| `verbatim_split` | info | NONE | `R-7.4` |
| `keep_value_missing` | hard | UNIQUE | `R-7.4` |
| `must_appear_missing` | info | UNIQUE | `R-7.4` |
| `must_hold_unver` | unver | NONE | `R-7.4` |
| `secret_leak` | hard | UNIQUE | `R-7.5` |
| `secret_unknown` | info | NONE | `R-7.5` |
| `no_secrets_in_book` | unver | NONE | `R-7.5` |
| `invented_number` | info | UNIQUE | `R-7.10` |
| `invented_name` | info | UNIQUE | `R-7.10` |
| `length_over` | info | FREE | `R-7.8` |
| `length_under` | info | FREE | `R-7.8` |
| `paragraph_budget_missed` | hard | FREE | `R-7.25` |
| `book_length` | info | NONE | `R-7.8` |
| `source_overlap_high` | info | NONE | `R-7.6` |
| `dialogue_fewer` | info | FREE | `R-7.20` |
| `dialogue_broken` | info | FREE | `R-7.20` |
| `dialogue_ratio_drift` | info | FREE | `R-7.20` |
| `quotes_in_unquoted_corpus` | info | NONE | `R-7.7` |
| `band_drift` | info | FREE | `R-7.21` |
| `para_len_drift` | info | FREE | `R-7.21` |
| `layout_drift` | info | UNIQUE | `R-7.21` |
| `simile_drift` | info | FREE | `R-7.21` |
| `xinli_drift` | info | FREE | `R-7.21` |
| `dash_drift` | info | NONE | `R-7.21` |
| `fact_landing` | info | FREE | `R-7.22` |
| `fact_landing_unver` | unver | NONE | `R-7.22` |
| `cross_chapter_number` | info | UNIQUE | `R-7.23` |
| `quote_unbalanced` | info | NONE | `R-7.7` |
| `length_report` | metric | NONE | `R-8.1b` |
| `paragraph_count_report` | metric | NONE | `R-7.25` |
| `overlap_report` | metric | NONE | `R-8.1b` |
| `book_length_report` | metric | NONE | `R-8.1b` |
| `dash_report` | metric | NONE | `R-7.21b` |
| `fact_atom_unattested` | info | NONE | `R-7.22b` |
| `material_anchor_unattested` | info | NONE | `R-7.22b` |
| `end_hook_surface_report` | metric | NONE | `R-7.9` |
| `end_hook_unver` | unver | NONE | `R-7.9` |
| `narration_person_report` | metric | NONE | `R-7.9` |
| `narration_person_drift` | info | FREE | `R-7.9` |
| `short_run_report` | metric | NONE | `R-7.21b` |
| `dialogue_report` | metric | NONE | `R-7.8b` |
| `secret_timeline` | metric | NONE | `R-8.9b` |
| `name_surface_survey` | metric | NONE | `R-8.9b` |
| `convergence_report` | metric | NONE | `R-8.1b` |
| `draft_missing` | unver | NONE | `R-8.1` |

### 6.1 四组容易误读的信号

- `source_overlap_high` 只看 canonical 闭卷成稿的逐章 20 字窗重合是否超过 10%。它用来暴露
  原文意外进入下发面、缓存或落盘链的异常，不再判别 deep/shallow；是 INFO、NONE，不阻断也
  不自动返工。`overlap_report` 逐章无条件报同口径读数，不能拿书级均值藏掉局部高值。
- `end_hook_surface_report` 只盘点姓名 / 数字原子落在计划末 slot、其他 slot 还是整章未见；
  代词化、换说法、同名或撞值都使字面位置无法证明语义交付。因此每章同时保留
  `end_hook_unver`，永不据 surface metric 自动定罪或付费修订。
- `narration_person_report` 逐章无条件写声明人称、弱信号锚、叙述层命中数，或明确写
  `UNVER/未判`。只有单段“全章”的 first/third 且锚非空才运行弱信号；台词先由
  `novel/core/text.py::narration_layer` 剔除。`narration_person_drift` 是 INFO + FREE 的人工
  线索：省略主语、书信、自由间接引语都是合法反例，不得升级 HARD 或自动 revise。
- `material_anchor_unattested` 把 verbatim / keep_value / must_appear 的来源字节逐条回查同章
  源文。找不到说明 Stage2 抽取或 Stage4 投影造了伪锚，`owner=stage2` 且无 Stage5 repair；
  绝不能让 `verbatim_missing` 的 UNIQUE 路由付费把伪材料硬塞进正文。

`dialogue_report` **只逐章产出**：成稿直接引语数、源章直接引语数及比值。现役代码没有书级
汇总；文档或 UI 不得宣称“逐章 + 书级”。对白告警与这个 metric 分开，0 命中不能代替分布。

## 七、历史基线与验证边界

旧双臂盘面曾有 31 章（deep 11 / shallow 20），并留下逐章带极值 0.814 / 1.206、全书比值
0.949 / 1.022 / 0.980、旧规则命中与对白差异等读数。这些数字只用于解释阈值来历和识别
判据退化，是 **v1 旧布局历史基线**；它们不是 canonical v2 current-schema corpus，不能用于
宣称现役 Stage5 通过率、当前 HARD“0 命中”或当前 51 条判据的质量结论。

canonical v2 产物、单臂入口、freshness、恢复路与 51 条判据的工程可靠性已经由定向及
非 corpus 回归验证。现役 beat-material 写作契约在设计上改为按 beat / material 组织闭卷材料，
但原计划与旧 event-list 契约进行的受控双书付费 A/B 已在开跑前取消：没有实验正文、盲评结果
或无正文 manifest。因此不得宣称 beat-material 在质量、成本或速度上胜出。

TODO-21 的隔离双书重跑、current-schema manifest 与 `--require-corpus` 是另一件事：它只建立
现役全链语料基线。Stage5 消费 Stage1～4 的事实、机械基线、计划与 writer context，上游质量会
显著改变成稿；即使 TODO-21 通过，也不能把全链读数因果归到 Stage5 写作契约。在它完成前，
单元 / 非 corpus 回归只能证明工程守卫通过，不能证明现役语料全量绿。
