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
stage5/reviewer_rewrite_request.json
                                 Reviewer 明确授权的多章 LLM 改写批请求（v2）
stage5/wontfix.json              人工豁免账（v1）
```

人工改稿的身份审计另落 `<base>/_service/human_actions.jsonl`；它是服务账，不是 Stage5
产物，不进 artifact registry、来源账或上面的路径清单，字段见 §4.3。

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
| 只读人工稿预览 | `--preview-edit <ChapterResponse.json>` | 0；不写盘 |
| 人工整章写回 | `--apply-edit <ChapterResponse.json> --actor ... --reason ... --current-chapter-sha256 ... --preview-subject-sha256 ... --edit-confirmation formal\|working_draft` | 0 LLM |
| 只读复验旧 run 的 prepared 尾窗 | `--emit-prepared-recovery-workpack <SOURCE_RUN_ID>` | 0 |
| 按已审工作包收口 prepared 尾窗 | `--recover-prepared <SOURCE_RUN_ID> --actor ... --reason ... --subject-sha256 ...` | 0 wire |
| 生成最新 Review | `run stage5.review <sid> --base <base>` | 0 |
| 读取已有 Review | `run stage5.review <sid> --check-only --base <base>` | 0 |
| 批量保留 pretrim | `stage5.review --retain-pretrim-batch <BatchTrimResolutionRequest.json>` | 0 |
| Supervisor 把 Reviewer 改写回答正式化（内部） | `stage5.review --request-reviewer-rewrite @reviewer-input` | 0；`@reviewer-input` 只能由 Supervisor 从已验证的 ReviewerActionRecord 与 seat/session/cost 证据注入，不能手写或经 HTTP jobs 提交 |
| 消费 current 正式改写请求 | `stage5.write --reviewer-rewrite stage5/reviewer_rewrite_request.json --yes` | 付费；仅执行批内 ready 章，并发上限 4 |
| 补完 Reviewer 改写 checkpoint/prepared 尾窗 | `stage5.write --recover-reviewer-rewrite-prepared <SOURCE_RUN_ID> --reviewer-rewrite-request-sha256 <SHA256>`；已有 target WAL 时再原样携 `--reviewer-rewrite-recovery-target-run-id <TARGET_RUN_ID> --reviewer-rewrite-recovery-journal-sha256 <SHA256>` | 0 wire；不新建 provider/session/cache |
| 退休从未开始且已过期的 Reviewer 改写请求 | `stage5.write --retire-reviewer-rewrite-request stage5/reviewer_rewrite_request.json --reviewer-rewrite-request-sha256 <SHA256>` | 0 wire；只在全根零执行证据闭合时可用 |
| 生成 / 提交闭合回执 | `approval template stage5 ...` / `approval apply stage5 ... --input <决定文件>` | 0 |

不带 `--chapters` 就是全书，不需要也不接受 `--deep`、`--shallow-rest` 或 `--mode`。
`novel/orchestrator/nodes.py` 在预览与真跑共用的命令构造口关闭式拒绝 `mode`，CLI/API
也不再提供“选深度章”入口。

prepared recovery 是默认付费续跑的关闭式例外，不是第二条写作臂。若旧 run 已有通过
checkpoint、但进程停在 canonical 提交后的 `prepared` 尾窗，默认 `stage5.write` 会在构造
client/driver 前拒绝，并给出显式恢复命令。操作员先以一个**不同于 source、且 emit/apply
两次完全相同**的 `--run-id <TARGET_RUN_ID>` 运行只读 workpack；审阅后把打印出的完整
`subject_sha256` 与 `actor/reason` 交给 `--recover-prepared`。两条 flag 禁止与 executor backend、
egress、`--chapters`、`--revise` 或 response repair 混用；apply 覆盖 source run 当时全部
unresolved prepared 章，不能挑章。

两条 prepared recovery flag 仅供 operator CLI runner 使用，`POST /api/jobs/preview` 与
`POST /api/jobs` 都会在构造 argv/job 前拒绝。HTTP 作业每次生成新 run id，无法安全复用已进入
workpack subject 的 target；不得通过删除 target 绑定来迁就通用作业面。

apply 不 probe/start、不读缓存、不发 wire，也不改 source run 的任何 sidecar。它以 source
自己的 code/profile/session/ledger 复验 run、request、checkpoint 与 commit 的 raw file SHA 和
semantic self-hash，再以 target 当前代码复算输入、prompt、schema、HARD 与 live baseline。
target 的 `_service/runs/<sid>/stage5/<TARGET>/prepared_recovery.json` 冻结整书 workpack/action，逐章
`prepared_recovery.json` 走 `prepared → committed`；只有 replacement journal、全局 adoption
audit 与 live canonical 三者精确闭合，旧 foreign reservation 才算 superseded。写回会令旧
Review stale，因此恢复成功后必须**另跑** `stage5.review`。旧书内 `_stage5_runs` 仅作只读恢复来源。

上面两步人工工作包只服务 initial write 的通用 foreign prepared；Reviewer LLM 改写有另一条更窄的
自动恢复。顶部卡回答先经 FREE `--request-reviewer-rewrite` 写 current canonical request，queue 再以
`recovery:stage5-reviewer-rewrite` 滚新 execution epoch，唯一允许 PAID `--reviewer-rewrite` 消费它。
若旧 source run 已留下同 request 的 matching checkpoint/prepared，queue 改发上述
`--recover-reviewer-rewrite-prepared` 精确 FREE 动作，不要求第二次人工审批，也不续旧 provider session；
若 target recovery WAL 已经存在，后继 run 必须复用其 target run 与 WAL 原始 SHA，不能另造一份 WAL。
source 的 generic prepared commit 仍归 `recoverable_checkpoint`，作为原 accepted checkpoint 的恢复材料；
只有 target recovery WAL 已实际创建并能绑定 target run/SHA，才归 `recoverable_prepared`。
复验 request 的 Review/trim/frontier 是授权校验，必须隔离在 `RunContext.validation_reads()`；这些读取
不得进入 writer 正文的普通来源边，冻结 request、旧正文与语义指令仍由 prompt/执行 journal 的 SHA 绑定。
queue/permit 的只读恢复探针显式读取本书 `<base>/_service/ledger.jsonl` 的历史成本证据，不要求或替换
`readonly_ctx` 的内存账；查询不得追加成本。`in_flight_or_unknown` 没有执行 argv，Supervisor 保留 unknown
并正常关闭本次运行，不申请空参数许可、不启动默认 writer 或新 Commander。
执行状态关闭为九态：`ready_paid / in_flight_or_unknown / recoverable_checkpoint /
recoverable_prepared / landed_formal / landed_invalid / landed_rejected / expired_unstarted /
consumed_resolved`。首轮 `started` 是首轮 wire intent；每次后续 writer turn 都先把 `retry_started` 和
上一 checkpoint 追加到 request 专属自哈希 journal。只有最新 turn 的 matching checkpoint 且其后没有新
session/cost/wire 迹象才可恢复。仅有 pre-wire started、原 parent 已 stopped 且全根
session/cost/checkpoint/commit/WAL/wire 均为空时，才可在 request 专属跨进程执行 lease 内写
`reclaimed_unstarted` 后重新 claim；其余有 wire 而无可验 checkpoint、身份不唯一、坏件或任一原字节
漂移都保持 unknown，禁止自动重发模型请求。

formal、`.INVALID` 或 rejected 均以 journal + exact typed carrier 的 terminal 事实消费本次意图；把文件手工
移进 `_rounds/` 不算已解决。`--recheck`、`--revalidate`、`--apply-edit` 先冻结 request-specific
successor WAL 与 terminal 原字节，再执行各自既有 0-wire 事务，最后才提交 immutable successor receipt；
硬杀后只可按 exact landed successor 补尾。receipt 把 exact terminal、动作证据与 exact successor 连成
`consumed_resolved`。formal 正文改变后旧 Review/closure 过期，下一步只能免费重跑 Review；旧请求只有
已正式落稿或已有合法 successor/expiry receipt 时才可被新的 fixed-path request 替换。`landed_invalid /
landed_rejected / in_flight_or_unknown / recoverable_* / ready_paid` 都不能被覆盖。

### 1.1 Stage5 从 Stage1 即时计算正文机械基线

`novel/stages/stage5/review.py::load_book` 读取完整 Stage1 正文后调用
`novel/core/measure.py::measure_body`，再由 `body_style_metrics` 精确保留：
`avg_sentence_chars`、`avg_paragraph_chars`、`paragraph_sep_chars`、`dialogue_ratio`、`quote_styles`、
`chapter_chars.avg`、`per_chapter_avg_sentence.min`、`per_chapter_avg_sentence.max`。
`review.load_book` 只把这九个叶子放进 `Book.measurable`；结构上没有 `voice`、
`surface_habits`、`chapter_styles` 或 `hook_style`，prompt 也不能接收整份 Stage3 包。
对白适用性由 `dialogue_ratio is not None` 推出，不再保存一格重复布尔值。测量算法版本进入
`novel/consumption.py::stage5_main_text`，所以正文或算法任一变化都会让 Stage5 来源账自然 stale。
Stage2 的 secrets 仍从剥除原文 evidence 的全局事实投影读取，不因退役 Stage3 而缩小检查面。
写作投影保持 secrets-only，以免人物展示信息变化让既有付费正文整体 stale；Review 另绑定
`char_id/canonical/address[].surface/name_distinctiveness`，因为旧名、发明名和专名探针会实际读取
这些叶子。人物 evidence-only 定位不进入消费指纹。

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
| `errs` | str[] / 键不在 | 否 | `.INVALID` 中为阻断原因；人工稿相对 current 未新增 HARD 时以 `formal` 写回，新增一般 HARD 时只可显式 `working_draft` 写成 canonical 人工轮并由 Review/closure 阻断；`paragraph_budget_missed` 两种确认态都不得正式化 | **契·码** |
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
逐场目标由 `measure.paragraph_slot_plan` 唯一计算：章节目标 clamp 进同一合法区间，每个非空
slot 至少一段，余量按场景预算作 largest-remainder 分配，slot 顺序是平局规则，总和精确等于
章目标；场景数使目标不可行时在首个 wire 前拒绝。prompt、修订反馈与常驻 metric
`paragraph_slot_report` 只消费这一份计划。`reflow_shadow` 只增删换行，强制非空白字符 SHA
不变、不跨对白合并、只在句末拆分；它只向 observer 报可行性与 HARD 前后差，不写产物、
不改变候选或 Review 结论，TODO-21 三书标定前不得默认采用。
超上限不触发模型自动裁剪：正文原样保留，落 identity `TrimAudit`，再由人选择
`retain_pretrim`、整章 `--apply-edit`，或让受托 Reviewer 以 §4.2 的关闭式 request 授权写手重写
一份完整章。最后一种仍是明确的人审决定，不是 `length_over` 自动触发或采用短候选。

所有 canonical 正文落盘入口共用同一个提交口：付费生成、重采、`--apply-edit`、
`--revalidate` 与 `--recheck` 都在章与 trim sidecar 的同一提交事务中对账。正文变化且超上限时，
audit 绑定新正文 SHA-256、旧 resolution 失效；回到区间内时退休旧 audit/resolution；正文未变且
二者仍匹配时保留人工决定。被闸门拦在 `.INVALID` 的候选没有替换旧 canonical，因此不得改动旧稿
的 trim 状态。

LLM 正式稿用 `RoundLlmProvenance`；人工整章写回用 `RoundFreeProvenance`，删除
`prompts/model`、记 `origin=human` 并继承上一轮 inputs。`--revalidate` 只按当前判据重判并
提升原问题稿，直接判 `.INVALID` 保存的原始 segments，不借重判归一或修改正文；来源账继承，
一个字未改就不得伪造新 prompt、模型或轮次。`--recheck` 同样不归一正文、不改 segments、任务
快照或 provenance，只刷新 v2 派生指标并补齐历史遗漏的 trim audit。

人工写回先经 `prepare_edit_preview`：读取 current canonical 完整文件 SHA，按计划校验全部 slot、
归一并用当前 `_judge` 同时计算全部 finding、current/introduced/resolved HARD，再把章号、current
SHA、规则指纹、归一 segments 和这些 finding 规范化为 `preview_subject_sha256`。preview 零模型、
零写盘。apply 必填 actor/reason、current SHA、preview subject SHA 与 `formal｜working_draft`，重算
同一评估面并在提交事务内再次核 current 文件 SHA；任一漂移在归档前拒绝。成功稿始终写新的
canonical human 轮而不是 `.INVALID`，不自动追跑 Review；连续编辑多章后只需显式跑一次最终全书
Review。已落动作另由 §4.3 的服务账保存身份与前后摘要，正文仍只在 canonical / `_rounds/`。

0-wire prepared recovery 的正式章仍是 `RoundLlmProvenance`，因为正文确由 source model 生成；
本次 usage 为零，并额外携带 typed `prepared_checkpoint_adoption`。该证据逐项绑定 source/target
run、source code、run/commit/checkpoint 的 raw+self SHA、attempt、response、稳定
`source_adoption_id`、人工 workpack subject/actor/reason；不能只靠旁路 journal 或审计行替正文
解释“为何 model 产物能在零调用的新 run 落盘”。兼容旧 v2 的缺席态只表示普通写作或字段引入前
的历史章，不反推为一次恢复。

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
| `fresh` | schema、RULES、上游、canonical 章集与正文投影、`judged` 全部相符 |
| `stale` | 已知某一主体或尺子发生变化 |
| `unknown` | 产物 / 来源账读不回或当前投影无法确定 |
| `missing` | 盘上没有 Review；首轮写作可继续，`--revise` / 豁免 / s5 审批不可据此继续 |
| `legacy_incompatible` | 发现旧 deep/shallow 布局，或最新 Review / live trim 低于当前 schema |

`require_fresh_review` 关闭式守住付费 `--revise` 与 wontfix；它只回答“这份结论是否当前”。
`require_accepted_review` 在此之上拒绝活动 `.INVALID`、缺正式章和任何当前 HARD，是闭合回执
可以提交的产物级前置。Stage6、Stage7 与最终交付还必须经过 `require_stage5_closed`，证明
`gate.s5` 来自一份绑定当前 workpack 的 typed closure；accepted Review 不能替代人工回执。
旧报告不得驱动新付费动作。正文、HARD 或 canonical 章集合真的改变后，必须在全部改稿完成时
零成本重跑一次最终 `stage5.review`，再重新生成闭合模板；`retain_pretrim` 只落 hash-bound
人工处置、不改正文或 Review 主体，因此纯 trim 销账保留同一份 fresh Review，只重建当前 closure
workpack/token，不能为“保险”再买一席或重跑 Review。旧 Review v1 仍含
`mode/mode_dir`；现役 v2 删除这两格，由 producer 免费重建，不做迁移。

## 四、审批与人工服务账：HTTP v6，盘上 v7

### 4.1 审批版本与职责

`ApprovalDoc.v=6` 是 HTTP 公共视图版本，字段为 `sid`、完整 `gate`、完整 `seen`、
`writing`、typed `design_review`、typed `structure`、typed `writing_approval`、typed `closure` 与
`spawns`；它不再暴露 `deep`。前三份 typed 决定分别服务 Stage4A、最终结构和写作授权，不能
互相推出；`closure` 服务 Stage5 成稿后的整书人工闭合。`gate.s4=true` 只可能来自当前结构主体的
全章 `accept`，`gate.s5=true` 只可能来自当前 `closed` closure。匿名 `approve_gate('s5')`、
`POST .../approvals/deep` 和 `DeepSelectionRequest` 都关闭式拒绝或已退役。

`<base>/_service/approvals/<sid>.json` 不是产物，现役内部记录是
`_ApprovalRecordV7(v=7)`：无 s3 的完整 gate/seen，design/structure/writing 的 current typed 决定与
history/proof，以及 `closure_decision/closure_history`。proof 绑定根 `clean_text` SHA-1、正式主体
完整 SHA-256 与决定面规范 SHA-256；bool 必须与 proof/typed 决定逐格闭合。结构或写作主体变化会
关闭依赖 gate；Review、章节、finding 或 trim 身份变化会使 closure 进入 `review_required/expired`，
旧决定只留 history。

旧 `_ApprovalRecordV6Legacy` 及更早记录只供显式 backfill 读取，普通 GET / 启动绝不静默迁移。
v6→v7 保留可逐项对证的 s0～s4、seen 与 design/structure/writing typed 历史，清除 s5～s7，
绝不伪造 closure。旧 v5 的 design history 恒空，不得从已有 A/B/Plan 或付费账伪造 A 人审；
已有 current Plan v4 + current bound EvidenceBinding v3 可继续最终结构裁决，但未来再次产 B 必须
补 current A review。旧 v2 Binding 不迁移，仍由 B producer 重建；更早记录最多迁移仍能按当前
盘面对证的前置 gate，不得凭旧 s4/writing 推造 typed 决定。v1 或损坏记录只能显式 reset/backfill。

### 4.2 `Stage5ClosureView` / `Stage5ClosureWorkpack`

workpack 是从 current 盘面确定性重算的只读工作面，不是又一份产物。它只接受 fresh Review；
只有 Stage5 尚无任何工作或回执历史时才是 `missing`。Stage5 已有稿件/历史但 Review 缺失，
或 Review 为 stale/unknown/legacy/unreadable 时，都是 `review_required`：不发布 current
workpack/decision，但不擦除 history。fresh Review 下先看 blockers：任一存在即 `blocked`；
无 blocker 且已有决定精确绑定同一 workpack SHA 时才是 `closed`，旧决定不再 current
时只留 history 并投影为 `expired`，其余可提交面为 `ready`。

构包前后重走唯一 Review freshness，并对 Review 字节、全部 canonical 章、活动
`.INVALID`、trim/wontfix 处置面做完整双采样；两次不同就拒绝返回混合工作包，由读方重试。

`approval:stage5-closure` 是一张状态感知的顶部卡，不是永远只收 closure 回执。fresh Review 下若
唯一 blocker 是 `unresolved_trim_items[]`，同一卡的一席 Reviewer 只发布三类互斥出口：覆盖全部待销账
章的 `stage5.review --retain-pretrim-batch @reviewer-input`、每章各一份但回答只能选择其中一份的
`stage5.review --request-reviewer-rewrite @reviewer-input`、以及 typed stop；它不发布 closure approval，
也不替 Commander 默认选择 retain/rewrite。若缺章、活动 `.INVALID` 或 HARD 与 trim 并存，构包前
直接 stopped/2 并指向各自正式恢复面，不为必然不可执行的批次开席。只有 `ready/expired` 才发布
closure receipt。trim 两出口共用 `action_mode=trim_disposition` 和同一席位，closure receipt 独立使用
`action_mode=closure_receipt`；旧 closure 席位/回答不得复用成 trim 决定，retain/rewrite 也不得按同一
Review/frontier 拆成两席。

| 字段 | 类型 | 必填 | 取值面 / 语义 | 证据 |
|---|---|---:|---|---|
| `state` | str | 是 | `missing｜review_required｜blocked｜ready｜closed｜expired`；页面只展示，不按 counts 重判 | **契·码** |
| `workpack` | `Stage5ClosureWorkpack` / null | 是 | `blocked/ready/closed/expired` 必有；`missing/review_required` 必为 null | **契·码** |
| `next_step` | str / null | 是 | `review_required` 必须给出最终 Review 的 0 成本恢复动作 | **契·码** |
| `current_decision` | `Stage5ClosureDecision` / null | 是 | 只在 `closed` 存在，且绑定当前 workpack；过期决定不得继续冒充 current | **契·码** |
| `history[]` | `Stage5ClosureDecision[]` | 是 | append-only 人工闭合历史；`closed` 时 current 是末项 | **契·码** |

| Workpack 字段 | 类型 | 必填 | 取值面 / 语义 | 证据 |
|---|---|---:|---|---|
| `review_seq/review_rel/review_sha256` | int / str / SHA-256 | 是 | 当前 fresh Review 的序号、相对路径与完整文件摘要 | **契·码** |
| `planned_chapters/missing_chapters` | int[] | 是 | 计划章升序去重；missing 只能是其子集 | **契·码** |
| `chapter_subjects[]` | `{output_ordinal,chapter_sha256}` | 是 | 精确覆盖 `planned - missing`；这里的 SHA 是 canonical **完整文件**摘要 | **契·码** |
| `invalid_chapters/hard_findings` | int[] / str[] | 是 | 任一非空都使 workpack blocked | **契·码** |
| `unresolved_trim_chapters` | int[] | 是 | 待销账 trim 的升序章号投影 | **契·码** |
| `unresolved_trim_items[]` | `{output_ordinal,audit_sha256,chapter_sha256}` | 是 | 与上一格章号精确同集；chapter SHA 是 trim audit 绑定的 canonical **正文**摘要，是浏览器批量提交的唯一合法 identity 来源 | **契·码** |
| `trim_subjects[]` | `{output_ordinal,audit_sha256,chapter_sha256,resolution_sha256}` | 是 | 升序精确覆盖全部 current trim audit；`resolution_sha256` 为完整 resolution 文件摘要或 null，null 子集必须恰等 unresolved。已销账决定改 actor/reason 也会换 workpack/token | **契·码** |
| `wontfix_sha256` | SHA-256 / null | 是 | current `stage5/wontfix.json` 完整文件摘要；即使命中计数不变，理由或身份字节变化也使旧 closure 过期 | **契·码** |
| `must_appear[]` | `Stage5MustAppearRow[]` | 是 | 每项带章号、evidence key、detail、finding SHA 与完整 chapter file SHA | **契·码** |
| `fact_group/unver_group` | `Stage5FindingGroup` / null | 是 | 各绑定组名、完整 finding-set SHA、条数与规则闭集；确认不把 UNVER 改成 PASS | **契·码** |
| `disclosure/resolved` | `{rule:int}` | 是 | INFO/metric 披露计数与 wontfix/typed 已处置计数，不是放行判定 | **契·码** |
| `workpack_sha256` | SHA-256 | 是 | 绑定除自身外全部当前工作包字段；模板、提交与过期判定共用 | **契·码** |
| `review_token` | `s5cr1_<64hex>` | 是 | 封装本次 exact workpack；调用方只回交此 token，不搬运 review/workpack/chapter/finding 多枚 SHA | **契·码** |

`Stage5ClosureRequest` 必填 actor/reason、一枚 opaque `review_token`、逐项 `must_appear[]` typed ruling 与
整组 `group_acks[]`；不接受 workpack/review/finding/chapter SHA。must-appear 只接受
`confirmed_present` 或理由非空的 `accepted_exception`。服务端在同书锁内重建 current workpack，
用 token 找到完整 finding/chapter/review 身份并要求提交集合精确覆盖；任一漂移即整份过期。
fact landing / 其余 UNVER 仍按服务端物化的完整集合 digest 与条数确认。Template 的 placeholder/
template token 只属于只读材料，不作为 Request 字段；`Stage5ClosureDecision` 由服务端另加
`decision_sha256/decided_at`，盘上审计继续保存完整 materialized 身份。
缺章、`.INVALID`、HARD 或 unresolved trim 任一非空都不得提交。历史 must-appear 预填只在 finding
SHA 与 chapter SHA 同时不变时复用；`gate.s5`、Stage6/7 写效果与 assemble 都消费同一 current closure。

`BatchTrimResolutionRequest` 是 retain 出口的整批人审回答：顶层 actor/reason 非空，`items[]` 必须按章序
精确覆盖 workpack 的全部 `unresolved_trim_items[]`，逐项原样绑定 audit SHA 与正文 SHA，并给出
非空 reason（可显式继承顶层 reason）。Supervisor 只把这份已通过 typed loader 的请求物化到
`_service/edits/<sid>/`，再经 permit 的外部输入字节 CAS 交给 0-LLM producer；typed stop 表示需要
但无法安全形成登记的批量改写请求，不落 resolution/request、permit 或 node。

| Batch 字段 | 类型 | 必填 | 取值面 / 语义 | 证据 |
|---|---|---:|---|---|
| `actor/reason` | str / str | 是 | 均不可只含空白；reason 是整批共享理由 | **契·码** |
| `items[]` | `BatchTrimResolutionItem[]` | 是 | 至少一项，按 `output_ordinal` 升序且去重；fresh 提交必须精确覆盖 current unresolved 全集 | **契·码** |
| `items[].output_ordinal` | int | 是 | current workpack 中对应待销账章 | **契·码** |
| `items[].audit_sha256/chapter_sha256` | SHA-256 / SHA-256 | 是 | 逐字复制 workpack 双 SHA，Commander 与 Supervisor 都不得重算或替换 | **契·码** |
| `items[].reason` | str | 是 | 空串表示显式继承顶层 reason；非空时作为该章理由，effective reason 始终非空 | **契·码** |

trim 人审的稳定业务身份只含同一 fresh Review 完整 SHA、原始全量有序 audit/chapter 双 SHA、
reviewer contract 与 `action_mode=trim_disposition`，刻意不含会随逐章落盘变化的 closure token/workpack；
retain 与 rewrite 是这份业务的一席互斥回答。
进程在批次中被 kill 后，只在盘面仍严格等于“原 manifest 的已落连续前缀 + current unresolved 完整
后缀”时重放原 actor/reason/items；已落前缀逐章按同一决定核验，后缀不得遗漏、夹入或重排，活动
trim audit inventory 的坏文件、路径/章号不一致或任一 SHA 漂移都关闭式停止。重放不再调用
Commander；producer 在同书锁内全量预检、首写前复采，并按章幂等补尾。每一笔后继未落项写入前还要
重验完整 exact prefix/current suffix frontier、该项 audit/正文与既有 resolution，首项落盘后的盘外漂移、
删除或 foreign 决定不得被旧 facts 覆盖。全部 retain 落盘后仍使用原 fresh Review，只因 current
audit/resolution/wontfix 处置摘要变化重建 closure workpack/token；不得
把纯处置写成一次新的 Review。

`Stage5ReviewerRewriteRequest` 是 Commander/provider 回答的公开运输形状，不是
`--request-reviewer-rewrite` 所读的 CLI 文件。Supervisor 验证回答和 durable ReviewerActionRecord 后，
才生成私有 `ReviewerRewriteBatchTrustedInput` 信封并以一次性 `@reviewer-input` 交给 FREE producer；
该信封不能由人工、HTTP jobs 或普通 operator 输入自报。公开回答仍完整携原有序
`unresolved_trim_items[]`，以有序非空 `targets[]` 选择独立改写的子集，各项分别携
`target_ordinal/target_chapter_file_sha256/directives`。未选章保持待处理，不形成 retain。
多个章节可独立修复时按上限 4 并发生成，整批正式落盘后统一免费重算 Review。
其中每项既有
`chapter_sha256` 始终是 `rendered_text` 的正文 SHA，target 另带从 closure
`chapter_subjects[target]` 冻结的 `target_chapter_file_sha256` 完整文件 SHA，两者不得互换。
actor/reason 非空，`directives[]`
至少一项，code 只能是 `remove_repetition｜compress_exposition｜repair_wording｜smooth_transition`，
`current_text_evidence` 必须是该章 current `rendered_text` 中恰好唯一命中、非空且不超过 240 字的片段，
同一 code/evidence 不得重复。Review SHA、frontier、target 与 actor 从 immutable option 逐字保留，本次只可选择
targets 非空有序子集、填写 reason 和各章 directive code/evidence。reason/evidence 不会直接拼进 writer prompt；
可信 producer 按 code 注入固定指令，并总是附加预算、plan slots、事实、时序与逐字材料约束。

回答先按“provider response 绑定 seat turn → 内容寻址 `ReviewerActionRecord` → seat finish → 一次性
`@reviewer-input`”耐崩溃持久化，再由 FREE producer 构造并 typed readback 正式
`ReviewerRewriteBatchRequest` v2。批内 `requests[]` 为逐章自哈希的内部
`ReviewerRewriteRequest` 单元，执行账、checkpoint 和恢复参数继续按单元 SHA 定位；
批信封及 authority receipt 共同绑定全部目标。正式 request 另绑定 target 完整文件 SHA、原 ReviewerActionRecord 摘要、
logical session、seat turn、provider response、自身 subject 与 self hash；仅它是后续 paid writer 的 durable intent，运输 payload、
action record、旧 stop 或手写同形 JSON 都不是执行权。request 落稳后 queue 必须先收口其执行态，不能因
正文尚未改变、Review 仍 fresh 而重新开顶部卡。部分交付先恢复未收口章，
不得重复购买已完成章；同批 exact formal 才能豁免对应兄弟章的旧 Review 输入边，外部漂移仍拒绝。
旧 canonical v1 不自动转换或手补；先在原版本按原执行账收口，再由 fresh Commander
决定与 FREE producer 重建 v2。未知执行不得借升版重新授权。

request typed readback 后不能直接删除唯一来源。Supervisor 先把原 action 原始字节复制到
`_service/runs/<sid>/reviewer_action_authorities/reviewer-action-<sha>.json`，再写同 stem 的
`.receipt.json`；receipt 绑定 request 原始文件与 self SHA、stable business/current-then contract、
session/seat/provider response 及 seat/session/cost 三份证据摘要并自哈希。receipt 是 seal 的 commit point；
其后才退休 active action。active request 恢复可在 old-owner-stopped 证明下补 seat/session 与 seal，
sealed authority 的 queue/permit/writer 复核则严格只读。历史 terminal/expiry 只要求当时已封存的 contract，
新的 paid claim 与 active checkpoint recovery 还必须要求 Reviewer contract current。

### 4.3 `_service/human_actions.jsonl`（wire v1）

这份 append-only 服务账以 `prepared → committed` 两相记录人工改稿意图与已落主体，不保存
正文、不属于 artifact，也不能替代 Chapter provenance、Review 或 closure。公开读视图只返回
已有同形 `prepared` 的 `committed`；孤立 prepared 是失败/被 kill 尾窗的恢复证据，不冒充成功动作。
字段形状：

| 字段 | 类型 | 必填 | 取值面 / 语义 | 证据 |
|---|---|---:|---|---|
| `wire_version/action/phase` | `1 / stage5_edit / prepared｜committed` | 是 | 当前唯一人工动作 wire、种类与两相状态 | **契·码** |
| `request_id` | SHA-256 | 是 | 由 sid/章、actor/reason、confirmation、current chapter SHA 与 preview subject SHA 规范计算的 preview-bound 意图身份 | **契·码** |
| `action_id` | SHA-256 | 是 | `request_id + result_chapter_sha256` 的 content-addressed 结果身份；同一结果跨进程幂等 | **契·码** |
| `sid/output_ordinal` | str / int | 是 | 书与 canonical 输出章身份 | **契·码** |
| `actor/reason` | str | 是 | 自报身份与非空理由；不是认证系统 | **契·码** |
| `confirmation` | str | 是 | `formal｜working_draft` | **契·码** |
| `current_chapter_sha256` | SHA-256 | 是 | preview 时 current canonical 完整文件摘要 | **契·码** |
| `preview_subject_sha256` | SHA-256 | 是 | preview 归一候选、规则与 finding 的完整主体摘要 | **契·码** |
| `result_chapter_sha256` | SHA-256 | 是 | 成功落盘后的 canonical 完整文件摘要 | **契·码** |
| `introduced_hard_rules` | str[] | 是 | 升序去重；formal 必为空，working draft 明示新增一般 HARD | **契·码** |
| `destination_rel/run_id/code/ts` | str / str / str / int | 是 | canonical 相对路径与写该 phase 的运行/代码/时间；目标只允许 `stage5/chNN.json` | **契·码** |

producer 按“同书总锁 → 逐章锁 → append-only 账”的唯一锁序持有线程+跨进程提交所有权，
重算双 SHA 后先追加 `prepared`，再在章/
trim 历史事务内写 canonical、读回预期结果 SHA 并构造 `CheckReport`，最后追加 `committed`。
普通异常会补偿 artifact/sidecar，prepared 仍保留为失败证据；若进程在 canonical 落盘后被 kill，
后续同一 request 以 current result SHA 精确命中 prepared，只补 trim/report/committed，不再造一轮。
同一 request 已 committed 为另一结果时关闭式拒绝。写口复用跨进程 append lock；读侧只容忍被 kill
截断的最后一行。下一次追加会先把该尾的原始字节与前缀摘要内容寻址地隔离到
`_service/human_actions_tail_recovery/`，证据原子落稳后才截去坏尾并补相；中间或已换行的
完整坏行仍关闭式失败。preview 本身、纯 Review 都不写账。全部 Stage5 writer、Review/wontfix、
trim 与 closure submit 另共用 `_service/stage5_writer_locks/<book>/book.lock` 总锁；API 不因取锁写书目录。

## 五、其余产物与退役投影

### 5.1 rejected / trim / wontfix / 历史

- `RejectedRevision`（v1）保存 `output_ordinal`、非空 `why[]`、`fixes[[rule,evidence_key]]`
  和 1～2 份 `{chars,text}` 尝试；旧正式稿不被退化候选覆盖。
- `TrimAudit`（v2）只保存 ordinal、pretrim 正文 SHA-256 与 reason。没有候选、没有 adopted。
- `TrimResolution`（v2）唯一 action 是 `retain_pretrim`；同时绑定 audit SHA-256、当前
  `stage5/chNN.json` 路径与正文 SHA-256、actor/reason 和免费来源账。单章 CLI 与顶部卡 full-batch
  共用这一个 canonical writer；batch 不是第二种 resolution，也不改变正文或 Review。
- `wontfix.json`（v1）按 `(stage, chapter|book, rule, evidence_key)` 绑定人工理由；豁免不删结论。
- `_rounds/` 是审计历史，不计活动 `.INVALID` / rejected / stalled 余额；反推 schema 时仍必须读。

### 5.2 退役的 `stage5/draft.md`

`stage5/draft.md` 曾是 canonical 章集合的 Markdown 展示副本，2026-08-26 起退役。它不再登记为
artifact、不再由节点生成，也不参与完成度、Review freshness、审批 subject 或 corpus manifest。
旧 base 里的文件不自动删除，但运行时不发现、不读取；需要全书文本的消费者直接按阅读序拼接
`Chapter.segments`。

## 六、判据注册表

当前注册表共 **51 条**：**7 hard、26 info、13 metric、5 unver**。
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
| `paragraph_slot_report` | metric | NONE | `R-7.25` |
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
现役全链语料基线。Stage5 消费 Stage1/2/4 的事实、即时机械基线、计划与 writer context，上游质量会
显著改变成稿；即使 TODO-21 通过，也不能把全链读数因果归到 Stage5 写作契约。在它完成前，
单元 / 非 corpus 回归只能证明工程守卫通过，不能证明现役语料全量绿。
