# HTTP 响应（`/api/**`）

**载体已定：Pydantic v2**——但本文只定**内容**，模型照它写，**方向别搞反**。
所以下面的表里不出现任何 Python 类型标注。

⇒ `docs/schema/_api.md` ↔ `contracts/schema/api.py`，文件名一一对应
（同 `_common.md` ↔ `common.py`、`_report.md` ↔ `report.py`、`_orchestration.md` ↔ `orchestration.py`）。

## 本文**不定**路由表

路由表的唯一落点是 `web/DELIVERY.md` §3，该节明确记录
「**固定，不要改**」，前端已经照它接完了 14 屏。**在这里再抄一份就是第二个落点**，
而两份路由表分家的表现是：前端按 A 请求、后端按 B 注册，双方都不出声，只有 404。

本文定的是那张表右边那一列的**响应长什么样**，以及三条跨端点的纪律。

## 「证据」列

与 `_README.md` 那一列同义，但本文的三源不是「盘上/契约/代码」，而是：

| 取值 | 含义 |
|---|---|
| **`抓包`** | `web/src/mocks/*.json` 里有历史字节，哈希登记在 `web/scripts/mock-baseline.json`（来历见统一契约 §8.1）。**它是历史响应证据，不是当前 schema** |
| **`派生`** | 由已有模型或已有纯函数算出来的，**不是一件新事实**（改它等于改上游） |
| **`设计`** | 新形状，**零样本**——还没有任何东西验证过它 |

⚠⚠ **统一契约 §八登记的 `web/src/mocks/*.json` 永远不许用新后端的输出覆盖。** 它是 `tests/test_api_enums.py`
与 `tests/test_invariants.py` 全部对账的**证据基线**；覆盖之后那一族测试就变成
拿 `contracts/invariants.py` 跟它自己比——而「有测试」与「测试在查东西」的分界线就在这里。

---

## 一、出线纪律（三条，跨全部端点）

**① 键恒在，`null` 就写 `null`，不用 `exclude_none`。**
与 `novel/store.py::ArtifactStore._render` 的房规逐条相同（`by_alias=True` · 键恒在 · `None` 写成 `null`）。

> ⚠ `_orchestration.md` 与 `contracts/schema/orchestration.py` 里有几处写着
> 「`None` = 键不在」——**那说的是盘上那一份产物的纪律，不是 HTTP 出线的**。
> 两者今天不冲突（产物里省掉的键，视图层根本不会去构造），
> 但读起来会让人以为 HTTP 也该 `exclude_none`。**在这里一次说死：不。**
> 理由是消费方不同：产物的读者是下一层代码（键不在 ⇒ 它没发生过）；
> 响应的读者是**界面**，而界面要区分「这个字段是 null」与「后端这版根本没有这个字段」。

**② `by_alias=True` 不许关。**
`_provenance` 是 alias 字段（模型里叫 `provenance`）。FastAPI 的
`response_model_by_alias` 一旦被关掉，响应里那个键会**静悄悄**变成 `provenance`，
而前端读 `_provenance` 拿到 `undefined`——不报错，只是那一格空了。
⇒ 配一条测试：凡是模型声明该 alias，响应 JSON 里必须有字面量 `_provenance` 这个键。

**③ 措辞不是契约，id 才是（`Q-6`）。**
凡是「为什么」类的格子一律出 **id**（异常类名 / `FRESHNESS_REASON` / 判据 id），
不出人读的句子。人读的句子可以**另外**出一格，但前端不许拿前缀去认它——
`check_stage5_review.json` 的 `info` 桶靠 `〔度量〕` 前缀区分度量与告警，
一次措辞改动就会把 41 条度量全部渲染成告警色。

---

## 二、`/api/enums` **刻意没有响应模型**

它的键集就是它的内容（由 `contracts/invariants.py` 的公开常量派生；键数随公开取值面增长，
不作为协议常量）。
写成一个五十来个字段的模型，等于把「自动发布」重新变回一份**手写清单**——
而那正是 `novel/api/enums.py` 开头论证过要消灭的东西：

> 手写**发布清单**会**静默地烂**（新常量前端拿不到 ⇒ 前端自己硬编码一份，没人出声）；
> 手写**不发登记表**会**响亮地烂**（新常量落不进任何桶 ⇒ 当场抛 `ValueDomainRefused`）。

⇒ 它的守卫是**超集测试**（`tests/test_api_enums.py`），不是类型。
**这一行写在这里，是为了下一个人不会来「补齐」它。**

Prompt 选型完成后，`prompt_variants`、`prompt_defaults` 与 `--prompt-variant` 已从公开契约删除；
现役生产节点各只有一条 prompt 路径，前端和调用方无需也不能选择实验臂。

三个合成键与抓包的关系：

| 键 | E 期的变化 | 依据 |
|---|---|---|
| `invent` | 无（`allow`/`forbid`/`principle` 逐值相同） | `抓包` |
| `job_kinds` | 键从原型 12 个 `n1..n9` 换成**节点 id**，值含 `stage`/`section`/`flags`/`produces`；不再发布任何 mode 取值面 | `docs/BACKEND_CONTRACT.md`「命名体系」第 5 条；Stage5 v2 只有 canonical 写作 |
| `actions` | `kind` 取值同上；字段为 `kind`/`extra`/`label`/`paid`/`free_flag`/`writes`/`writes_disk`/`needs_value`/`required_inputs`，无 mode | 同上；`writes_disk` 是前端在 `web/DELIVERY.md` 里点名要的那一格 |

⚠ `job_kinds` 里的 `paid` 说的是**默认路径**贵不贵，**不许拿它做贵贱对账**。
真正的贵贱由 `(node, flags)` 决定，唯一落点 `novel/orchestrator/nodes.py::is_paid`（`R-11.7`），
出口在 `actions[]`。实测：抓包的 `check_blueprints` 是 `paid=false`，
而它映到的 `stage2.blueprint` 节点 `paid=true`——那不是不一致，
是原型把复检做成了独立 kind，而两层命名下它是一个 flag。

---

## 三、端点 → 响应

**当前落地端点**（其余端点见 §七；数量随路由增删，不在本文手写第二份常量）：

| 端点 | 响应 | 新旧 |
|---|---|---|
| `GET /api/enums` | 无模型（见 §二） | 新 |
| `GET /api/agent/capabilities` | `AgentCapabilitiesResponse`（registry 能力 + 本机 CLI/版本只读探针） | 新；Phase 1 发布，现役 UI 仍未消费 |
| `GET /api/novels` | `NovelList` | 新 |
| `GET /api/novels/:sid` | `NovelOverview` | **复用** `_orchestration.md` |
| `GET /api/novels/:sid/check/:kind` | `CheckReport` | **复用** `_report.md` |
| `GET /api/jobs?sid=` | `JobList` | 新 |
| `GET /api/jobs/:id` | `JobRecord` | **复用** `_orchestration.md` |
| `GET /api/jobs/:id/log?offset=N` | `LogChunk` | 新 |
| `GET /api/jobs/:id/stream?offset=N` | SSE：`LogStreamEvent` | 新；主日志通道 |
| `POST /api/jobs/preview` | `JobStart`（请求体 `PreviewRequest`） | **复用** `_orchestration.md`；现役 direct executor 为 8 节点 / 13 种 flag-token 组合（精确矩阵见下），并回显独立 `egress_authorized` 快照 |
| `POST /api/jobs` | `JobRecord`（202；请求体 `PreviewRequest`） | **复用** `_orchestration.md`；同一组 profile / egress 边界，profile 非空且 egress 不是 true 时不起 job |
| `GET /api/queues` | `QueueList` | 新 |
| `POST /api/queues/preview` | `QueuePlan`（请求体 `QueueCreateRequest`；纯预览） | **复用** `_orchestration.md` |
| `POST /api/queues` | `QueueRun`（202；确认并建立、启动持久队列） | **复用** `_orchestration.md` |
| `GET /api/queues/:id` | `QueueRun` | **复用** `_orchestration.md` |
| `POST /api/queues/:id/resume` | `QueueRun`（202；请求体 `QueueResumeRequest`，剩余付费命令要求 `confirmed=true`） | **复用** `_orchestration.md` |
| `POST /api/queues/:id/items/:index/cancel` | `QueueRun`（只取消尚未开始项） | **复用** `_orchestration.md` |
| `POST /api/upload?filename=` | `UploadResult`（请求体为原始字节） | 新 |
| `POST /api/novels/:sid/trash` | `TrashNovelResult`（请求体 `TrashNovelRequest.expected_title`；整本项目移入 `_service/trash/`） | 新 |
| `GET /api/novels/:sid/stage0/report` | `Stage0Report` | 从 `stage0/revisions.json` 派生的窄视图，见下 |
| `GET /api/novels/:sid/stage0/segments` | `Stage0Segments` | 新 |
| `POST /api/novels/:sid/stage0/edit` | `JobRecord`（请求体 `Stage0EditRequest`） | 新 |
| `POST /api/novels/:sid/stage0/approve` | `JobRecord`（请求体 `Stage0ApprovalRequest`：`expected_revision_id` + 自报 `actor` + `reason`） | 新 |
| `GET /api/novels/:sid/stage0/candidate` | `Stage0CandidateView`（待裁决的那条机器 revision + **当前稿的读数**） | 新 |
| `POST /api/novels/:sid/stage0/candidate/adopt` | `JobRecord`（请求体 `Stage0CandidateDecisionRequest`） | 新 |
| `POST /api/novels/:sid/stage0/candidate/dismiss` | `JobRecord`（同一请求体） | 新 |
| `POST /api/novels/:sid/stage1/partition` | `JobRecord` | 新 |
| `GET /api/novels/:sid/stage1` | `Stage1View`（一次返回连续分区、原文章号诊断与 `anomaly_count`；全部由当前 clean 复扫） | 新 |
| `GET /api/novels/:sid/approvals` | `ApprovalDoc` | 新 |
| `GET /api/novels/:sid/approvals/stage5/template` | `Stage5ClosureTemplate` | 只读、0 成本；按当前整书主体生成带 placeholder/token 的关闭式模板，不代签 |
| `POST /api/novels/:sid/approvals/stage5` | `ApprovalDoc`（请求体 `Stage5ClosureRequest`） | 原子保存 hash-bound Stage5 整书闭合回执；成功才打开 `gate.s5`，不启动作业 |
| `GET /api/novels/:sid/approvals/design/workpack` | `ApprovalWorkpack` v1（绑定 current A candidate + 完整 design subject；异常优先，只读、0 LLM） | 新；Commander/前端审阅入口，不预填决定 |
| `GET /api/novels/:sid/approvals/design/template?actor=&reviewer_kind=&review_run_id=&reason=` | `DesignReviewTemplate` | 四项 query 均必填；按当前 A 生成关闭式全书模板，可逐章复用仍完全绑定的历史 accept，但不代签；fresh B infeasible 时发布当前证明并禁止复用旧 accept |
| `POST /api/novels/:sid/approvals/design` | `ApprovalDoc`（请求体 `DesignReviewRequest`） | 原子保存 Stage4A 全书策略 + 逐章 typed 审阅；不启动修订、B 或任何作业 |
| `POST /api/novels/:sid/approvals/gate/:key` | `ApprovalDoc` | 新 |
| `POST /api/novels/:sid/approvals/seen/:key` | `ApprovalDoc` | 新 |
| `GET /api/novels/:sid/approvals/structure/workpack` | `ApprovalWorkpack` v1（绑定 current structure subject；异常优先，只读、0 LLM） | 新；完整披露 B concern / INFO / metric / UNVER，不自动裁决 |
| `GET /api/novels/:sid/approvals/stage6/workpack` | `Stage6ApprovalWorkpack` v1（绑定 current 候选集、代码/规则/产物主体与 A/B 完整证据；只读、0 LLM） | 新；定稿前唯一双 hash 阅读面，不自动 pick |
| `GET /api/novels/:sid/approvals/stage7/workpack` | `Stage7ApprovalWorkpack` v1（绑定 current 人物决策面、代码/规则/产物主体与完整替换证据；只读、0 LLM） | 新；定稿前唯一双 hash 阅读面，不自动批准 |
| `POST /api/novels/:sid/approvals/structure` | `ApprovalDoc`（请求体 `StructureApprovalRequest`：完整结构 subject SHA-256、actor、总理由与恰好覆盖全书的逐章 `accept / request_replan`） | 新；typed Stage4 结构裁决，提交本身不启动作业 |
| `POST /api/novels/:sid/approvals/writing` | `ApprovalDoc`（请求体可省略：空 body 保留旧 UI 的 `begin_writing`；非空必须是 `WritingApprovalRequest` 的 actor、reason 与当前完整 subject SHA-256） | typed 分支只写审批账、不启动作业；Commander 使用 CLI façade，不走空 body 兼容口 |
| `POST /api/novels/:sid/approvals/reset` | `ApprovalDoc` | 新 |
| `POST /api/novels/:sid/approvals/backfill` | `ApprovalBackfillResult`（按盘面显式补录，不在 GET 时静默发生） | 新 |
| `GET /api/novels/:sid/reading/:o` | `ReadingView` | 新 |
| `GET /api/novels/:sid/stage2/global` | `GlobalView` | 新 |
| `GET /api/novels/:sid/stage2/global-review` | `GlobalReviewView`（含当前完整 subject 重算状态与逐项审计） | 新 |
| `POST /api/novels/:sid/stage2/global-review` | `JobRecord`（请求体 `GlobalReviewDecisionSet` v2：逐项决定，或绑定 source/scope/workpack hash 与正文章全集的 blocking scope + 逐项例外；仅全部 blocking PASS 的 exit 0 可盖 s2） | 新 |
| `GET /api/novels/:sid/stage4/:o` | `Stage4View` | 新 |
| `GET /api/novels/:sid/stage5/:o` | `Stage5View` | 新 |
| `GET /api/novels/:sid/review/:o` | `ReviewView` | 新 |
| `GET /api/novels/:sid/inbox` | `InboxView` | 新 |
| `GET /api/novels/:sid/stage6` | `Stage6View`（同响应带 `source_hook_text`，Stage6 页不再另发 Stage1 请求） | 新 |
| `GET /api/novels/:sid/stage7` | `Stage7View` | 新 |
| `GET /api/novels/:sid/stage7/book.txt?download=` | 仅在 Stage7 人物表已批准、输入/正文/文案均 current/fresh 且组装账闭合时返回 UTF-8 纯文本（`download=true` 时为附件）；旧文件仅存在不构成交付资格。⚠ **URL 字面与盘上文件名是两件事**：盘上那份叫 `stage7/<书名>_rewriting.txt`，这条 URL 是稳定的 API 标识符、不随书名变；另存名由 `Content-Disposition` 给出（`filename*=UTF-8''…` 带书名，`filename=` 留 ASCII 回退） | 新 |
| `GET /api/novels/:sid/stage7/:o` | `Stage7View` | 新 |
| `GET /api/novels/:sid/run` | `RunProgressView`（信标折叠 + 完成度，观察轨迹非判定） | **复用** `_orchestration.md` |
| `POST /api/agent/runs/preview` | `AgentRunPreview`（与真跑同一构造函数） | 新 |
| `POST /api/agent/runs` | `AgentRunRecord`（202；请求体 `AgentRunRequest`） | **复用** `_orchestration.md` |
| `GET /api/agent/runs?sid=` | `AgentRunList` | 新 |
| `GET /api/agent/runs/:id/log?offset=N` | `LogChunk`（与作业日志同一形状） | 新 |
| `POST /api/agent/runs/:id/stop` | `AgentRunRecord`（熄火：terminate 子进程树） | 新 |
| `POST /api/novels/:sid/review/:o/edit/preview` | `DraftEditPreview`（请求体 `DraftEditPreviewRequest`） | 同步、只读、0 成本；与正式 apply 共用归一/判据/subject 构造，不写一次性文件、产物或 Review |
| `POST /api/novels/:sid/review/:o/edit` | `JobRecord`（请求体 `DraftEditRequest`） | 新 |
| `POST /api/novels/:sid/stage5/trim/:o/retain-pretrim` | `JobRecord`（请求体 `TrimResolutionRequest`；只允许保留原稿） | 新 |
| `POST /api/novels/:sid/stage5/trim/retain-pretrim-batch` | `JobRecord`（请求体 `BatchTrimResolutionRequest`） | 整批先核每章 audit/chapter 双 SHA，任一漂移则零写入；同一 manifest 中断后可幂等续写剩余章 |
| `POST /api/novels/:sid/stage6/approve` | `JobRecord`（请求体 `HookApproveRequest`：pick/actor/reason + 当前候选 subject SHA-256 + 刚读 workpack SHA-256） | 新 |
| `POST /api/novels/:sid/stage7/approve` | `JobRecord`（请求体 `NameMapApproveRequest`：actor/reason + 当前 decision subject SHA-256 + 刚读 workpack SHA-256） | 新 |

> #### `ApprovalWorkpack` v1 是只读阅读面，不是决定模板
>
> 两个端点共用同一外壳：`v=1`、`sid`、`kind=design｜structure`、当前完整
> `subject_sha256`、设计面必有而结构面必须没有的 `candidate_sha256`、完整有序
> `chapter_ordinals[]`、`disposition`、可选 `current_decision_sha256`，以及固定六组 `groups[]`。六组顺序恒为
> `blocking / semantic_attention / disclosure / metric_only / phase_deferred / coverage`；
> 空组也必须带 `scanned` 分母出线，不能用裁掉空数组暗示「没扫」。
>
> `WorkpackItem` 只投影服务端已算事实：`code`、`rule`、`sev`、`status`、
> `chapter_ordinal`、`scene_refs`、`refs`、`repair_owner`、`detail`、`effect`、
> `allowed_actions`、`changed`、`historical_accept_reusable`、`source_decision_sha256`。
> `allowed_actions` 是关闭集 `accept / request_repair / request_replan`，
> 不是 argv；INFO、metric、UNVER、skipped 和非 blocking/coverage effect 恒为空。coverage 恰覆盖
> `chapter_ordinals`，changed 章在前、段内按章号升序；changed 与 exact historical accept reuse
> 互斥且穷尽。工作包没有 actor/reason/decision 字段，GET 不写审批账、不启动 job，也不替用户签字。
> `disposition=already_current` 仅在 current typed 决定仍精确绑定同一 candidate/subject、代码、规则与
> 产物时出现，并必须携 `current_decision_sha256`；否则为 `review_required`。它让 caller 幂等跳过，
> 不让服务端把不同 actor/reason/typed 声明折成同一决定。

> #### Stage6/7 定稿 workpack 把“看到的证据”与 approve 乐观锁绑在一起
>
> 两种包公共携带 `v/kind/sid/code_version/rules_fingerprint/artifact_subject_sha256/check/
> workpack_sha256/disposition/current_approval?`。`check` 是完整分母 + 四态计数 + provenance + 全部非 ok
> 行 + 正常 metric 的 exception-first 投影；没有因紧凑而删除异常。digest 排除 disposition/current
> approval，因此批准或组装不会自废；代码、规则或业务证据变化一定改 hash。
>
> Stage6 另携三条完整候选（正文、subject SHA、mech/findings、semantic 状态、合并 decision、每名 judge
> 的 verdict/reason/evidence、质量分/名次）与 A/B 原始 `judge_rankings`。Stage7 另携完整
> `decision_subject_sha256`、surname map、characters/aliases、extra subs、kept 明细、逐旧字面的真实
> `replacement_hits`、residual 上下文/baseline、book findings 与 identity。approve 请求必须同时提交
> 同一份包的 subject SHA 与 workpack SHA；任一漂移关闭式拒绝。`already_current` 必须携仍绑定本包的
> current approval，页面不再显示可重复的默认批准动作。

> #### `Stage0Report` 是窄视图
>
> 它不继承产物模型，只返回 Stage0 页面实际消费的六格：
>
> | 字段 | 说明 |
> |---|---|
> | `original_filename` / `total_code_points` | 审校页标题与规模读数 |
> | `warnings[]` | 运行时证据 + `novel/core/warnings.py::describe` 的措辞；前端不拼措辞 |
> | `executable` | `novel/stages/stage0/eligibility.py::is_stage0_executable` 的投影；前端不从审批字段重算 |
> | `revision_id` / `revision_origin` | 当前版乐观锁与来源展示 |
>
> source ref、源/clean 指纹、removed、approval、producer code 均留在后端权威链或专用分区视图，
> 不因“可能有用”复制到这个响应。

⚠ **preview 直接返回 `JobStart` 是有意的**，不是图省事：那个模型自带后置条件
`paid == 任一步 paid` / `writes == 任一步 writes`，于是「整份贵贱是逐步折叠出来的」
这条 `R-11.7` 白拿；而且**真跑时 `jobs.start()` 构造的就是这个类**——
「预览与真跑是同一个对象」从一句话变成字面事实（`R-11.8`）。

队列 preview 同样只读；`POST /api/queues` 只有在 `confirmed=true`（或计划无付费项）时才会
先落 `QueueStart`、再起后台 runner。服务 lifespan 会恢复未完成队列，但只能消费创建时冻结的
命令；新一轮仍可能付费的恢复必须先重新展示这些冻结命令，并在 resume 请求体显式给
`confirmed=true`，否则返回 409 且不启动。取消运行中项返回 409；它不会 terminate 或把在途调用
改写成 cancelled。

`AgentRunRequest` 的授权面是两条正交轴：

| 字段 | 类型 | 缺省 | 只控制 |
|---|---|---|---|
| `authorized` | `bool` | `false` | 是否允许产生外部模型调用费用 |
| `egress_authorized` | `bool` | `true` | 是否允许向外部模型发送小说正文及派生上下文 |
| `profiles` | `PipelineAgentProfiles \| null` | `null` | commander、默认 executor 与逐节点覆盖的选择；`null` 只是旧客户端/迁移期未指定，不是某个隐含默认 |

只有正文外发轴的 wire 缺省是 `true`，不代表两项授权合并。UI 必须显示两个独立复选框并显式提交
二者；`AgentRunPreview` 回显两个布尔值，preview 与真跑都由同一 planner 把两段授权原文写入 prompt。
preview 可以在任一项为 false 时 0 成本展开；单本 start 与 batch 则在任何 Commander probe/spawn/wire
之前同时要求二者为 true。`profiles` 键同样恒在：非空值经 registry 解析并进入真实 Commander；
`null` 只保留请求兼容形状，当前 `default_profiles=null` 时 preview/start/batch 均关闭式拒绝，不能代选。

`GET /api/agent/capabilities` 是 agent 选择面的唯一公开数据源：

| 模型 | 字段 | 约束 |
|---|---|---|
| `AgentCapabilitiesResponse` | `drivers[]` · `executor_nodes[]` · `default_profiles` | `executor_nodes` 按 `AGENT_EXECUTOR_NODES` 完整有序发布，前端不维护第二份节点表。`default_profiles` 当前明确为 `null`：尚无 provider-neutral 默认，不能取 `drivers[0]` 猜；Phase 7 服务端冻结唯一默认后才可非空 |
| `AgentDriverCapability` | `id` · `provider` · `label` · `roles[]` · `availability` · `availability_reason` · `version` · `models[]` | `id` 是可执行 driver 身份，`provider` 只是供应商元数据，`label` 只展示。可用性四态为 `ready / missing / unknown / unauthenticated` |
| `AgentModelCapability` | `id` · `label` · `reasoning_efforts[]` · `execution_modes[]` | 精确 `id` 进入 argv/审计，label 可改；effort 与 execution mode 正交，只能提交该 model 实际发布的组合 |
| `AgentProfile` | `driver` · `model` · `reasoning_effort` · `execution_mode` | schema 只承载选择；driver/角色/model/effort/mode 的组合由同一个 capability registry 关闭式校验，客户端不得自造笛卡尔积 |
| `PipelineAgentProfiles` | `commander` · `executor_default` · `executor_overrides{node: profile}` | override 键只接受响应中的 executor 节点；代码节点、stage 名与退役节点不能伪装 executor |

探针只在本机解析 CLI 并读取版本，**不发模型请求、不产生费用**；因此 `ready` 只证明可执行文件与
版本探针成功，不证明账号认证、模型 entitlement 或真实调用可用。当前 registry 只发布实际受测的
execution mode；`ultra` 既不是 reasoning effort，也尚未被发布，调用方不得凭 UI 文案自行放开。

Phase 1 冻结上述能力响应与 nullable 配置槽；Phase 2 当时只开放一条窄纵切：直接
`POST /api/jobs/preview` / `POST /api/jobs` 在 `node=stage2.global` 且 `flags=[]` 时可携带非空
`executor_profile`。两端点共用 `plan_start`，registry 解析后的精确选择依次冻结进
`Step.executor_profile`、`JobStart.executor_profile` 与 `steps[].argv` 的 executor CLI 参数；子进程
CLI 再把同一选择交给 `RunContext`，由该节点自己的 executor 在同一 logical session 内循环接受代码
校验反馈，直至正式产物提交与终验。显式 profile 绝不回退 DeepSeek。

Phase 3 把相同 API 纵切扩到 `node=stage2.blueprint, flags=[]`。同一 profile 只进入付费 producer
Step；自动追加的 0 成本 `--check-only` Step 仍为 `executor_profile=null`。CLI 后由逐章 executor
按 phase/purpose/chapter 隔离 session，验证后的 checkpoint 严格联结 run policy、profile、prompt/schema、
session audit、accepted stdout/typed payload 与成本账；跨 CLI 恢复须复验并认领 prior attempts，完整命中
不产生新 wire。整书 compile、联合 HARD、正式提交、readback 和自动终验仍由代码决定。

Phase 4 再把 direct profile 扩到 `node=stage6.semantic, flags=[]`，同时增加与 `--yes`
正交的正文外发授权。direct `PreviewRequest.egress_authorized` 默认 false（不要与上方 outer
`AgentRunRequest` 当前默认 true 混读）；resolved profile 非空时 builder 必须收到显式 bool，并将它与
profile 同源冻结进 `JobStart` 和付费 `Step`，只有 true 才把运行控制参数 `--allow-egress` 加进 argv。
false 的 preview 仍返回完整但不可执行的 argv；`POST /api/jobs` 与手敲 CLI 在 job、probe、driver、wire
之前拒绝。profile 为空时 false 归一为 `null`，true 关闭式拒绝，不能从付费确认或 profile 选择倒推授权。

Stage6 A/B 各用独立 driver 对象、logical session、临时 cwd 与 provider session id，缺失角色并发；
逐角色 checkpoint 支持完整或单边恢复，恢复的历史 provider session 只作 adoption 身份，不冒充 live
resume。正式交付由代码在同一提交所有权内执行 prepared journal、artifact write、typed readback、
两条 adoption 与 committed，并继续承担双盲响应校验、合并和当前输入终验。

Phase 5 后，当前 API 只接受以下 **8 节点 / 13 种 flag-token 组合**的非空 profile：

| node | token 组合 |
|---|---|
| `stage2.global` | `[]` |
| `stage2.blueprint` | `[]` |
| `stage4.design` | `[]`、`[--replan]`、`[--repair-decision]` |
| `stage4.plan` | `[]` |
| `stage5.write` | `[]`、`[--chapters]`、`[--revise]`、`[--revise,--chapters]` |
| `stage6.hook` | `[]` |
| `stage6.semantic` | `[]` |
| `stage7.rename` | `[]` |

`--chapters` 后的章号是值，不是第十三种 token。Phase5 五个新增 producer 固定 typed 完整 replacement；
agent 只交候选，代码负责业务 HARD、结构化 feedback、append-only checkpoint、正式 journal、typed
readback、current/fresh 终验和 accepted-attempt adoption。显式 profile 的节点 dispatch 先于 legacy
client 构造，不能静默回退 DeepSeek。

矩阵之外的 direct executor 组合仍在 wire/dispatch 前拒绝。Phase 6 后，outer
`AgentRunRequest.profiles` 已接入 provider-neutral Commander：preview/start/batch 共用唯一 plan，
把规范化 base、起始代码指纹、resolved profiles、stdin-only invocation 与两项授权冻结进 started/queue；
排队出队不重读 capability 或默认。Commander child 只持 run id 与 contract SHA-256，再从 append-only
started 事件复核整份配置；operator runner 按 exact node/flags 选 default executor 或 override，不能手拼
`--executor-*`。Claude Code / Codex Commander 的 prompt 只走 stdin，argv/env/log 不复制全文；Commander
本身另记 `role=commander,node=null` 的 session 与 ledger，实际模型/effort 缺失保持 unknown，漂移、串
provider session 或账/审计不闭合只可把 delivery 降成 unknown。进程 state 与盘面 delivery outcome 正交。

页面现已从 capability 动态构造 Commander、默认 executor 与逐节点 override 表单；`default_profiles=null`
时必须显式选全，不取数组第一项。单本与批量 start 直接消费最后一次成功 preview 保存的同一请求快照；
profiles、两项授权或 capability 有任何漂移都先使 preview 失效，不能用新默认重解释旧确认。历史 run 的
`profiles=null` 只显示 unknown；direct Job 的 executor profile / egress 只读展示仍与 outer Commander
分开。Phase 1～6 的工程验证仍没有真实 provider 模型请求或 current-schema corpus 验收，不能据此宣称
模型质量、速度、账号认证或 entitlement 已验证。

通用启动返回 202 和 `running` 作业，由后台线程逐步启动 CLI 子进程。同一 `sid` 的书级
作业互斥并返回 409，不同小说可并行；Stage5 canonical 写作同样占书级键，并在启动前原子核对
current writing 授权、Review 状态与旧布局，不存在另一个按臂的并发口。

⚠ **一个响应模型都不许自己算判定**（`R-11.4`）。`badge` / `passed` / `counts`
只出现在 `CheckReport` 里，且由 `contracts/schema/report.py::badge_of` 折叠、
再由 `CheckReport` 的后置条件反过来断言——**服务端不重算，前端更不许算**。

---

## 四、四个小模型

| 模型 | 字段 | 说明 |
|---|---|---|
| `NovelList` | `base` · `novels[]`（`NovelOverview`） | 抓包 `novels.json` 的顶层就是 `{base, novels}`。⚠ **复用 `NovelOverview`，不另造一张轻量清单**——原型 `Web/novels.py` 那张第二清单的代价是 stage5 被迫整个排除在外，于是「有没有陈旧产物」那个徽章不覆盖成稿 |
| `TrashNovelResult` | `sid` · `title` · `trash_id` · `trash_rel` | `novel/orchestrator/trash.py` 的 CLI 子进程完成同盘移动后返回；`trash_rel` 是可恢复位置，不把“从语料库消失”偷换成“字节已永久销毁” |
| `ApprovalBackfillResult` | `approvals` · `backfilled[]` | `backfilled` 是本次**按正式盘面补录**的 gate id，空数组表示无事可补；界面必须明确这些不是用户逐屏点过的验收记录 |
| `ApprovalDoc` | `v=6` · `sid` · `gate` · `seen` · `writing` · `design_review` · `structure` · `writing_approval` · `closure` · `spawns` | HTTP v6 不等于 `_service/approvals/<sid>.json` 的盘上 v7。公开 gate 没有 s3；`design_review` 是 A 的独立相位，不是 gate：给出当前 candidate/aggregate hash、typed 当前决定、历史与 `unavailable / missing / repair_requested / strategy_replan_requested / accepted / expired`，只有 current `accepted` 才能授权 B。`structure` 是 B 编译 Plan 后的另一张票，另给当前 subject、B `binding_concerns`、typed 当前裁决、历史与 `unavailable / missing / accepted / replan_requested / expired`；`gate.s4=true` 只可能来自这张当前 `accepted`。`writing_approval` 同样给出 available、current subject、typed current decision/history；新 typed writing 决定绑定 Stage1 正文、Stage4 Plan、fresh WriterContext 与测量契约。`closure` 是 Stage5 当前整书工作包、typed 决定与不可擦除历史，`gate.s5=true` 只可能来自 current `closed`。旧盘 v6 只经显式 backfill 收成 v7：保留可对证的 s0～s4、seen 与 typed 历史，清除 s5～s7，不伪造 closure；普通 GET 不迁移 |
| `Stage5ClosureView / Workpack` | `state` · `workpack?` · `next_step?` · `current_decision?` · `history[]`；工作包含 Review 身份、`planned/missing`、`chapter_subjects[]`、`.INVALID` / HARD、`unresolved_trim_items[]`、逐项 must-appear、fact/unver 组、披露/已处置计数与 `workpack_sha256` | `state ∈ missing｜review_required｜blocked｜ready｜closed｜expired`，只由服务端重算，页面不得按计数重判。`review_required` 表示没有可接受的 current 最终 Review，不能携 current workpack/decision，且 `next_step` 必须指出重跑；`chapter_subjects[]` 精确覆盖全部当前 canonical 计划章；`unresolved_trim_items[]` 是章号、audit SHA 与正文 SHA 的精确批量提交面，章号投影必须等于 `unresolved_trim_chapters`。决定或正文漂移后旧决定只留 history，不能继续充当 current |
| `Stage5ClosureTemplate / Request / Decision` | `actor` · `reason` · `workpack_sha256` · `review_sha256` · `must_appear[]` · `group_acks[]`；decision 另有 `decision_sha256/decided_at` | must-appear 逐项绑定章号、evidence key、finding SHA 与 chapter SHA，只接受 `confirmed_present` 或理由非空的 `accepted_exception`；fact landing / 其余 UNVER 按完整 finding-set SHA 与条数整组确认。模板行带 `placeholder=true + template_token`，原样提交关闭式拒绝；正式决定不得保留 placeholder/token，确认不会把 UNVER 改写成 PASS |
| `BatchTrimResolutionRequest` | `actor` · 共享 `reason` · `items[{output_ordinal,audit_sha256,chapter_sha256,reason}]` | actor/共享理由非空，章号升序去重；单项空理由沿用共享理由。服务端在任何写入前重读并核完整批次，按章幂等落 hash-bound resolution；浏览器只复制当前 closure workpack 的 exact item，不自行计算 SHA |
| `DesignReviewTemplate / Request` | `actor` · `reviewer_kind` · `review_run_id` · `general_reason` · `candidate_sha256` · `aggregate_subject_sha256` · `binding_infeasibility?` · `repair_capability` · `strategy` · `chapters[]` | 一次提交必须覆盖当前完整策略与升序全章集。策略决定为 `accept / request_replan`；逐章决定为 `accept / request_repair`。repair 章必须给至少一条 `{code, scene_refs[], opaque_aliases[]}` 关闭式约束，accept 章不得夹带约束。GET 中每个未审行带 `placeholder=true + template_token`；显式确认须同时改为 `false + null`，且不能把默认 decision/reason/constraints sentinel 原样提交。`binding_infeasibility` 为 `{candidate_sha256,binding_concerns[],infeasibility_proof[]}`，仅发布当前 fresh B 负结论并进入 aggregate。`repair_capability={repairable,reason_code,chapters[{output_ordinal,committed_rounds,request_repair_available}]}` 由当前候选的 durable split lineage 唯一判定并进入 aggregate；legacy/untracked 或已提交 2 轮的章不得形成 `request_repair`。subject 只公开 opaque alias 的盲真相、候选业务面与服务端现场复算的结构证据，不公开 formal fact/person id；自然语言 reason 只作审计，不进入修订 prompt |
| `DesignReviewDecision / View` | request 全字段 + `decision_sha256` · `decided_at`；view 另含 state/current hashes/`binding_infeasibility?`/current/history | 历史 accept 只有在全局 basis、策略主体、本章主体且 B 证据面仍逐字相同时才能由 `source_decision_sha256` 复用；仍须对当前整书重新原子提交。全局 basis 或策略变化使全章过期，单章业务变化只使该章过期；fresh B infeasible 使旧决定过期并要求重新逐项审阅，拒绝决定不会自动变成 accept |
| `JobList` | `jobs[]`（`JobRecord`） | 抓包 `jobs.json` 就是 `{"jobs":[]}`——**一个样本都没有**，所以元素形状只能从 `_orchestration.md` 来 |
| `LogChunk` | `text` · `next_byte` | 按**字节**续读。⚠ 中文一字跨 3 字节，**只在读的一侧解码**（写的一侧按字节追加，见 `_orchestration.md`）。`next_byte` 原样回传给下一次 `?offset=` |
| `LogStreamEvent` | `text` · `next_byte` · `done` · `state` | SSE 的 `id` 与 `next_byte` 相同；普通 `log` 事件为 `done=false,state=null`，唯一的 `end` 事件为 `done=true,state=ok|landed_red|failed|unknown` 并随即释放连接。断线时浏览器用 `Last-Event-ID` 续读；显式 query offset 与它取较大者，避免刷新倒退 |

`PreviewRequest`：`sid`（已有书为非空字符串；首次 `stage0.remove --source` 为 `null`）·
`node` · `flags[]` · `executor_profile`（`AgentProfile \| null`；迁移期未指定为 `null`）·
`egress_authorized`（`bool`，默认 false；只授权 direct executor 外发正文）。
请求外壳是 strict，任何旧 `mode` 键都返回 422；老
`jobs.jsonl` 的兼容解码只在服务内部磁盘边界发生，不进入本请求或公开响应模型。
现役只允许 `_orchestration.md` 精确表中的 8 节点 / 13 组合提交非空 `executor_profile`；blueprint、Stage4 plan、Hook、Rename
自动追跑的 `--check-only` Step 不继承 profile。其余组合均以 `UnsupportedRunMode` 拒绝，不能接收后
再改走旧 LLM。Phase 2～4 的三条是历史增量，Phase 5 闭合当前矩阵。非空 profile 必须显式携带
bool egress：false 允许预览
但真启动返回 `ApprovalRequired`，true 才进入 argv；无 profile 时 false 归一成响应快照 `null`，true 拒绝。
⚠ `flags` 里的**带值 flag 要带上值**（`--apply-edit <路径>`）——
`/api/enums` 的 `actions[].needs_value` 就是用来告诉前端「这个按钮要先要一个文件」的。

---

## 五b、`GlobalView` —— S-05 全局设定那一屏

⚠ 与 S-06 不同，本屏**没有「左半边」**：stage2 没跑就没有任何东西可看
⇒ 产物不在盘上是 **404**（`UpstreamMissing`），不是 `exists=False` 的 200。
有降级形态的三屏在各节自注：S-04（stage2 没跑仍 200）、S-07（恒等基线）、
S-08（ctx 不在仍 200，`materials=null`）。

| 字段 | 类型 | 必填 | 说明 |
|---|---|---|---|
| `sid` / `title` | 串 | 是 | 由 `novel/paths.py` 从目录名派生 |
| `setting` | 对象 | 是 | `Setting`。经 `by_alias` 出线，键是那两个固定中文 key（`时代背景` / `世界观`） |
| `characters[]` | 数组 | 是 | 非空。`fate` **全量下发**——剧透遮蔽是前端的纯展示行为（`R-11.13`），服务端不做第二把尺子；`address[].by` 已解引用成 `NameRef` |
| `relations[]` | 数组 | 是 | 有向关系，两端已解引用 |
| `world_rules[]` | 数组 | 是 | `WorldRule` 原样 |
| `secrets[]` | 数组 | 是 | ⚠ 空数组 = 这本书没抽到秘密——**那可能是漏抽了，不是「无」**（探针盲区） |
| `arc` | 对象 | 是 | 解读层。`climax` / `ending` 全量下发（遮蔽归前端，同 `fate`） |
| `unresolved_refs` | 整数 | 是 | 全屏解引用失败的总数。**读数不是判定**——给人看「这份产物里有几个说不出名字的 id」 |
| `invalid_exists` / `freshness` / `reason` / `ghosts` | | 是 | 同 S-06 的来源账四格 |
| `_provenance` | 对象 | 是 | `GlobalProvenance` |

三条形状裁决：

**① `NameRef`：解不动就 `resolved=False` 且 `display == raw`——view 不编名字。**
编出来的名字比一个看得出来的 id 更坏（它看不出来）。
**② `GlobalSecretLine` 没有 `reader_knows_by_now`。** 那是「相对当前章」的量，
而本屏是全书视图，没有「当前章」；揭示章原样给，让人自己对。
**③ 判据结论不在这里**（`R-11.4`），同 §四第 ⑦ 条——本屏及以下各屏的结论
都只能来自 `/api/novels/:sid/check/:kind`。

## 五c、`ReadingView` —— S-04 原文 ↔ 骨架

左半边（原文）来自 **stage1**；右半边（骨架）来自 stage2 逐章蓝图。
⚠ **stage2 没跑 ⇒ 200 且 `blueprint=false`**：整章一个 hole、`beats` 空、
`summary=null`、`_provenance=null`——右半边整块缺席（后置条件焊死，不编一句摘要），
左半边与 stage2 无关（同 S-06 左右半分治的道理）。stage1 不在 / `:o` 越界 ⇒ 404。

| 字段 | 类型 | 必填 | 说明 |
|---|---|---|---|
| `chapter_ordinal` / `text_chars` | 整数 | 是 | 章号与全章码点数 |
| `blueprint` | 布尔 | 是 | 右半边在不在 |
| `summary` | 串 或 null | 是 | `blueprint=false` ⇒ `null`（没有蓝图就没有摘要，不编一句） |
| `segments[]` | 数组 | 是 | 连续覆盖整章 `[0, text_chars)`（后置条件）。⚠ **服务端已切好 `text`**，前端绝不按 start/end 自己切字符串（Python 码点 ≠ JS 码元）。`beat_id=null` ⇒ 空隙（两态由可空性表达，不另立 kind 字符串）；`flagged` 只属于空隙且 `chars >= uncovered_min_chars` |
| `beats[]` | 数组 | 是 | `located=false` 时坐标为 `null`（定位失败不编区间）；`hinges[].text=null` = 未定位 |
| `narration[]` | 数组 | 是 | 叙述者 / 聚焦者 / 人称面 |
| `loose` | 整数 | 是 | 没被任何 beat 覆盖的字数——与全部空隙合计**是同一个数**（后置条件） |
| `uncovered_min_chars` | 整数 | 是 | 「漏」的阈值**随响应自带**，是漏抓还是过渡交人工看摘录判断 |
| `blueprint_state` | 对象 | 是 | `stage2/chapter_blueprints.json` 的 `ArtifactState`（六格一次拿全）。`blueprint=false` 的两种来路——没跑 / 读不回——靠它的 `exists` 与 `readable` 分 |
| `_provenance` | 对象 或 null | 是 | `BlueprintProvenance`；`blueprint=false` ⇒ `null` |

## 五d、`Stage4View` —— S-07 计划 ↔ 写作上下文

⚠ **没跑过 stage4 时本屏也有内容**（`web/DELIVERY.md` §3）：`identity_plan=true`，
计划与上下文都是**本次现算的恒等基线**（不落盘）——「如果计划什么都不改，
写手手里会有什么」。恒等基线没有可审批的正式主体 ⇒ `structure_subject_sha256`、
`_provenance` / `_ctx_provenance` 必须为 `null`（后置条件）。计划在而 ctx 不在 ⇒ 200 且 `ctx_chapter=null`
（右半边缺席，`ctx_state` 说明处境）。

| 字段 | 类型 | 必填 | 说明 |
|---|---|---|---|
| `identity_plan` | 布尔 | 是 | `true` = 本屏是恒等基线。此时计划侧三个 typed goal 的 `intent` / `fact_refs[]` 指向恒等事实，**不是真决策** |
| `structure_subject_sha256` | SHA-256 或 null | 是 | 正式计划时由**本次读出的同一个 `PlanChapters` 对象**计算，并与 `index[]` / `plan_chapter` 同快照；恒等基线只能为 null。它标识完整 canonical subject，但本 view **不是该 subject 的逐字段序列化**。前端只有在它与 `ApprovalDoc.structure.current_subject_sha256` 完全相同时才可提交结构裁决，避免看 A 行却给 B 主体签字 |
| `chapters` / `ordinal` | | 是 | `ordinal ∈ chapters`（后置条件） |
| `index[]` | 数组 | 是 | 章索引；每行带 `chapter_goal`、完整 `structure_decision`、预算与场数，供一次提交时核对全章集合与逐章裁决，不额外发 N 次请求；它**不是**完整 canonical subject 的公开展开。⚠ **没有 `in_band` / `style_hits` / `span_chars` / `hidden`**；判据结论只来自 `/check/stage4`，派生结构向量由服务端 view 现场计算 |
| `plan_chapter` | 对象 | 是 | 章级三个 typed goal、`retain/reconstruct` 声明、从当前 blueprint 派生的语义契约、改写 brief 与有序 `scenes[]`；`computed_change_kinds` 同样只在 API view 派生，不写回计划。每场只展示 purpose/role/delivery/transition、land/context/turn facts、state refs 与预算 |
| `ctx_chapter` | 对象 或 null | 是 | 写手实际拿到的任务书。场景顺序以 `scenes[]` / `scenes[].slot` 为准；人物到场只在 `stages[].present`，`characters` 是带 `char_id` 的数组；不重复下发逐场源字数、participants、秘密/规则内部 id 或 must-not 清单 |
| `writer_target` | 二元组 或 null | 是 | 写手被要求的篇幅区间，**与写手同一把尺子**（`prompts.target_band`）；`null` ⇔ `ctx_chapter=null`（同进同出，后置条件） |
| `plan_state` / `ctx_state` | 对象 | 是 | 两份产物各自的 `ArtifactState` |
| `_provenance` / `_ctx_provenance` | 对象 或 null | 是 | 两份来源账分两个键出，不合并 |

## 五e、`Stage5View` —— S-08 成稿 ↔ 材料

⚠ `ctx_exists=false` ⇒ `materials=null`：**不发假材料**（`web/DELIVERY.md` §3，
后置条件焊死），给「先跑 stage4」——左半边缺席、右半边照常列已有成稿。
某章无 canonical 稿 ⇒ 200 且 `drafts=[]`（「还没写」不是错误）。
`planned=null` = 计划读不回来，「该有几章」判不了——与 0 是两件事。

| 字段 | 类型 | 必填 | 说明 |
|---|---|---|---|
| `ctx_exists` / `planned` / `written` / `invalid` / `missing[]` | | 是 | canonical 全书进度读数，不折叠成判定；不再有 `duplicates`（单一正式路径结构上消除了两臂重复） |
| `legacy_incompatible` / `legacy_paths[]` | bool / str[] | 是 | 旧 `stage5/deep|shallow/` 只读证据，以及仍占 live 位置的旧版 Review/trim schema；两格必须同时有意义，发现旧契约时不挑一臂或低版本冒充 current |
| `chapters` / `ordinal` / `index[]` | | 是 | 章索引只取 `stage5/chNN.json`；没有成稿的章 `chars` / `overlap_excl_verbatim` / `freshness` 三格全 `null` |
| `source_text` / `source_chars_raw` | 串/整数 或 null | 是 | 源章原文单独的落点（材料里不重复） |
| `materials` | 对象 或 null | 是 | 写手拿到的下发面，**与 canonical 闭卷写手同一份**（`prompts.build_material(...).blocks`——`R-11.8` 在读屏上的对应物：显示的材料 = 真发出去的材料） |
| `drafts[]` / `invalid_drafts[]` | 数组 | 是 | 正式稿与 `.INVALID` 分两个数组，数组归属已经表达 formal/invalid。每项只发篇幅读数、`segments[]`、对齐与 freshness/provenance；`segments[]` 是盘上的唯一内容权威，正文由消费者统一拼接，不再重复发 `text`、路径、轮次、来源字数或 origin 展示格 |
| `trim_audit` | 对象或 null | 是 | 当前章仍绑定 current 正文、且尚未人工销账的超上限事实；对象只发 `output_ordinal/reason/context`。单章 retain 在 CLI 边界从盘上重读锁；批量 retain 所需双 SHA 只来自 `ApprovalDoc.closure.workpack.unresolved_trim_items[]`，浏览器不计算也不从展示文本猜。`null` = 本章无待销账 trim |
| `drafts[].align` | 对象 或 null | 是 | **可空**：`null` = 对齐辅助尚未由服务端提供（本章无锚可对时 `align_rows` 为空、账仍在）。对齐账是对应关系不是判定，`note` 常驻这句话；锚点覆盖率必须常驻显示 |
| `drafts[].align.coverage_threshold` / `.coverage_state` | 浮点 / 串 | 是 | 阈值取自 `contracts/invariants.py::ANCHOR_COVERAGE_WARN`（0.6），`coverage_state ∈ low｜adequate` 由 `DraftAlignView` 的校验器按 `coverage` 与 threshold 反推 ⇒ **两格不可能各说各话**。前端只消费 `coverage_state`，一行阈值都不写（守卫 `tests/test_api_discipline.py::test_frontend_does_not_rejudge_anchor_coverage`） |
| `ctx_state` | 对象 | 是 | `ArtifactState` |

## 五f、`ReviewView` —— S-09 验收 / 修订

一份验收都没有 ⇒ 404（`UpstreamMissing`，前端渲染「先跑 stage5.review」）。
⚠ 判据表本身仍归 `/check/stage5`；本屏发的是**验收产物**（历史事实），
与「现在复检一遍」是两件事。

| 字段 | 类型 | 必填 | 说明 |
|---|---|---|---|
| `review_seq` / `book[]` | | 是 | 最新一份全书验收的序号与书级 findings；盘上的来源、基线与 provenance 只服务后端 currentness，不重复暴露 |
| `review_state` / `review_reasons[]` | `Stage5ReviewState` / str[] | 是 | `fresh｜stale｜unknown｜legacy_incompatible`（`missing` 由路由 404 表达，不能伪装成视图）；只有 fresh 的 reasons 为空。结论统一取 `review.review_status`，API 不重拼第二套 freshness |
| `finding_counts` | 对象 | 是 | 全书结论按 sev 现数，**四键恒在（0 也写）**。⚠ 不读产物的 `counts` 字段——那份是稀疏 Counter（`Review.counts` 登记过的病）；`index[]` 每行同理（后置条件逐行查） |
| `index[]` | 数组 | 是 | 每行只发 `output_ordinal/finding_counts/unresolved`；索引只回答验收结论，不重复轮次、篇幅、预算或未渲染 stale 格。`unresolved` = 有收敛账（`unresolved_rounds > 0`）且未豁免的结论条数 |
| `chapter` | 对象 | 是 | 只发 `output_ordinal/findings`。阅读、编辑与证据高亮使用并行 Stage5 响应中的当前 `Draft.segments`，避免 Review 携带另一份可能过期的正文 |

## 五g、`Stage6View` —— S-11 开篇文案

产物不在 ⇒ 404；读不回 ⇒ 409（本屏没有产物就没有内容）。

| 字段 | 类型 | 必填 | 说明 |
|---|---|---|---|
| `lead_upto` / `rationale` | | 是 | 开篇引到哪一章、为什么 |
| `candidates[]` | 数组 | 是 | 恒三条，`index` 恒 `1..3`。`chars` 在 Python 侧走 `measure()` 现算。质量审判字段包括 `semantic_state`（**只有 uncertain / unreviewed 两态**）、`semantic_reason`、`contradictions[]`、六维 `quality`、代码总分 `quality_score` 与 `quality_rank`；缺失或过期审判必须显示 unreviewed，与「判过、有证据、但服务端不替人盖章」的 uncertain 分开。⚠ 曾有 `clear` / `contradiction` 两个取值与 `selectable` / `human_resolvable` 两格布尔，随自动放行门整批下线——一个恒假的「可直接定稿」和一个恒真的「可人工消解」，在界面上分别是一条走不通的捷径与一道永远开着的闸 |
| `approved` | 对象 或 null | 是 | `null` = 还没定稿。`candidates[].approved` 与它说同一件事（后置条件）；`human_edit`（无前身）时不许有候选自称定稿 |
| `judged` / `sources` | | 是 | 生成时使用的成稿引用与来源账，原样 |
| `invalid_exists` / `freshness` / `reason` / `ghosts` / `_provenance` | | 是 | 来源账五格 |

## 五h、`Stage7View` —— S-12 人名替换 + 成书

**一个模型两问**：书级（`ordinal=null, rows=null`）与逐章对照（两者同非空，
后置条件焊死）；`:o` 不在判过的章集里 ⇒ 404（资源不存在）。
⚠ `rows` 用的是 **stage7 同一个替换器**（`novel/stages/stage7/subs.py`）对当前成稿
现算——不写第二个替换器，也不落盘。

| 字段 | 类型 | 必填 | 说明 |
|---|---|---|---|
| `surname_map` / `characters[]` / `extra_subs[]` / `subs_count` / `kept_count` | | 是 | 人名表的内容与读数。`characters[].hits` 是该角色全部字面在 `applied` 记账里的替换总次数，**按人分组求和——读数不是判定** |
| `residual[]` | 数组 | 是 | 旧名残留。**顺序是承重的**（照产物：置信度高在前、覆盖率降序） |
| `baselines` / `identity` / `approved` / `book[]` / `sources` | | 是 | 词干基线、恒等基线（可空）、定稿（可空）、书级结论、来源账 |
| `book_txt` | 对象 或 null | 是 | **可空**——`null` = 成书还没组装过 |
| `book_txt_on_disk` | 布尔 | 是 | 成书此刻真的在不在盘上。`book_txt` 记的是组装时的账，两者可分家 |
| `book_filename` | 字符串 | 是 | 成书的文件名（`<书名>_rewriting.txt`）。**另存名与按钮文案用它**——前端不许拿 `title` 再拼一次（`R-11.4`）：那是同一条命名规则的第二个落点，与 `novel/paths.py::book_txt_rel` 分家时盘上那份和下载那份会叫两个名字 |
| `chapters[]` / `ordinal` / `rows[]` / `hook` | | 是 | `chapters` = 可切换「逐章对照」的输出章号（本层判过的成稿章，升序）；`rows[].changed` 是 bool 不是 kind 字符串（两态本来就该是 bool） |
| `invalid_exists` / `freshness` / `reason` / `ghosts` / `_provenance` | | 是 | 来源账五格 |

`book_txt_on_disk=true` 只表示 live 路径有字节，不是交付授权。界面可据此展示预览/下载
入口，但专用纯文本端点仍会重跑 approved/current/fresh 与组装账闸；不把整本正文塞进
`Stage7View`，也不要求用户离开审校台去文件系统寻找。

## 五i、`InboxView` —— 待人工清单

`counts` **八键全出（0 也要出）**：「没有这个键」与「值是 0」不许混——
stage5 的 `counts` 字段就是这么烂掉的（稀疏 Counter，0 键消失）。
`counts` / `total` / `items` 三者相互一致由后置条件钉死。

`InboxItem` 的六条纪律：

- `kind`：八值 `INBOX_KIND`。`repairable` 是有确定本地修法的硬命中；
  `verification` 是证据不足但可由人工消解的 `UNVER`。其余不可操作的 `UNVER`
  不入列，避免制造永远清不空的待办。
- `rule`：`repairable` / `human_only` / `verification` / `upstream` / `stalled`
  填**判据 id**；`invalid` / `rejected` / `approval` 没有单条判据可指，
  填**节点 id**（如 `stage6.hook`）。
- `chapter`：**输出章**口径，可空——书级 / 整份产物 = `null`，
  后端不给书级条目编一个假章号。
- `wontfix`（视图顶层）：被人豁免的条数——豁免不删结论、不进清单，单独计数。
- `attention`：`pending` / `reviewed_retained`。后者必须同时带 typed `decision`
  （actor/reason/subject SHA-256/state/decided_at）；不能凭终审包散文或 agent 自述补推。
- `delivery_effect`：`blocking` / `disclosure`，与 attention 正交。顶层
  `attention_counts`、`delivery_counts` 都由 items 分组并由后置条件核一致；总数不再冒充失败数。

路由侧：对每个已移植层各跑一次 0 成本复检（stage2/4 会重算判据）——**纯读**，
大书上是秒级。**登记不优化**：盘上 3 本无压力，全语料嫌慢时在路由层加缓存，
别动生产者（`novel/views/inbox.py::inbox_view`）。

## 五j、人工写回的请求体

这些路径都是**单步 0 成本作业**：产物变更一律经 `jobs.run` 起 CLI 子进程（`R-11.3`），
HTTP 进程只写 `_service/`（一次性人工稿）。
⚠ **CLI 内红报告/失败 = HTTP 200 + `JobRecord.state='landed_red'|'failed'`**——作业终态不是 HTTP 错误
（`stage0.edit` 同形），前端读 `state` 与日志，不是读状态码。

| 端点 | 请求体 | 说明 |
|---|---|---|
| `POST /api/novels/:sid/stage0/approve` | `Stage0ApprovalRequest`（`expected_revision_id` + `actor` + `reason`） | `expected_revision_id` 必须等于人实际审阅的 current revision，读包后 edit/adopt 的旧请求以 409 拒绝；actor 是自报身份，空值拒绝。⚠ 零命中自动批已于 2026-08-21 停产、记录它的 `approval.origin` 一格已于 2026-08-22 删除 ⇒ **本端点是批准的唯一来源** |
| `POST /api/novels/:sid/stage0/candidate/adopt` / `dismiss` | `Stage0CandidateDecisionRequest`（`candidate_revision_id` + `expected_revision_id` 乐观锁 + actor/reason） | 页面上要决定的是**整版留哪一版**，不是逐个冲突选边——聚合根是 `{原文身份, 正文, 剔除账本, 告警}` 整团（`stage0.md` §2.5），只对正文做 diff3 保证不了整个聚合一致。`adopt` 落一条 `human_adopt` revision 并把审批退回 pending；`dismiss` 当前稿一字不动、候选进 `dismissed[]`（否则同一候选每跑一次机器就复活一次）。⚠ 「采纳后再编辑」不是第三个端点，是 adopt 之后照常 `POST /stage0/edit`。决定按内容寻址保留，崩溃后同一请求幂等重放；只有 `ok/landed_red` 才清理一次性 plan |
| `POST /api/novels/:sid/review/:o/edit/preview` | `DraftEditPreviewRequest`（完整 `segments[{slot,text}]`）→ `DraftEditPreview` | 只读同步预览；slot 在请求层先唯一，producer 再要求完整覆盖计划 slot 并按计划排序、归一、用当前 `_judge` 全量重判。响应给 `output_ordinal`、归一后的完整 segments、全部 findings、current/introduced/resolved HARD、`current_chapter_sha256`、`preview_subject_sha256` 与 `required_confirmation`。preview subject 精确绑定章号、current canonical 完整文件 SHA、Stage5 规则指纹、归一稿及上述 finding 集；不调用模型、不写上传文件、产物、Review 或人工账 |
| `POST /api/novels/:sid/review/:o/edit` | `DraftEditRequest`（完整 `segments` + `actor/reason` + `current_chapter_sha256` + `preview_subject_sha256` + `confirmation`） | typed segments 落 `_service/edits/` 一次性 JSON 稿，CLI 以文件名认章；apply 重新走 preview 的唯一纯评估口并核两枚 SHA，同书总锁→逐章跨进程锁内再核 current 文件 SHA。`confirmation` 只能是 `formal｜working_draft` 且必须等于本次预览要求：无新增 HARD 才是 formal；新增一般 HARD 必须明示 working draft；`paragraph_budget_missed` 两态都拒绝。成功稿始终落新的 canonical human 轮、旧轮归档，**不落 `.INVALID`**；一般 HARD 由 stale Review / closure / 下游闸继续阻断。无正文的 `_service/human_actions.jsonl` 先落 prepared，写后读回/report 成功再落 committed；kill 尾窗按 request + result SHA 补账，未换行坏尾先隔离原字节后重建 committed，都不造新轮。每次 apply 不自动追跑 Review，多章连续编辑结束后只跑一次最终全书 Review |
| `POST /api/novels/:sid/stage6/approve` | `HookApproveRequest`（`pick`、`actor`、`reason`、`subject_sha256`） | **不收文本**；完整 hash 与当前候选乐观绑定，actor 是自报而非认证身份。改字是另一个节点（`stage6.hook --apply-edit`），契约把编辑文件钉死在书目录内，`R-11.3` 禁止服务进程写产物盘。**`stage6/edit` / `stage7/edit` 刻意不在 HTTP**，走 CLI |
| `POST /api/novels/:sid/stage7/approve` | `NameMapApproveRequest`（`actor`、`reason`、`subject_sha256`） | 一本书只有一份人物表，因此不带候选序号；完整 hash 乐观绑定 `surname_map + characters + extra_subs` 决策面。成功后服务 s7 proof 同时绑定完整 typed 审批凭据，但不绑定 `book_txt` 等派生账，正常 assemble 不会自废批准 |

定稿类动作**落了**（退出码 0/1——先写后判：approve 落了，只是报告可能有红行）
才盖审批章；盖不盖章看「落没落」，红不红由 check 屏呈现，两件事不许混。
前置审批链尚未补齐 ⇒ 定稿照样落，检查单那一格在 approvals 屏补——
**不把检查单的顺序翻成定稿的失败**。

---

## 六、`ErrorBody`

| 字段 | 类型 | 必填 | 说明 |
|---|---|---|---|
| `kind` | 串 | 是 | **异常类名**。取值面 = `novel/errors.py` 的类名集合，由测试闭合 |
| `message` | 串 | 是 | 人话 |
| `missing` | 串 或 null | 是 | 缺的是**哪一个**（例如 `'novel'` 或某个现役 artifact 路径） |
| `hint` | 串 或 null | 是 | 下一步该做什么 |

⚠ **`kind` 用类名不用措辞**：前端的四态要求区分「后端没起来 / 这本书没有 / 这一步还没跑」
（`web/DELIVERY.md` §5「每屏四态」），靠句子前缀去认，一次措辞改动就静默断掉。

状态码表的唯一落点是 `novel/api/errors.py::STATUS`：**按类精确匹配再沿 MRO 上溯，没有默认格**
——`novel/errors.py` 开头写着「不设默认异常」，本表是它在 HTTP 侧的同一条纪律。
落不进表的异常说明那个失败还没想清楚，而一个默认的 500 会把它藏起来。

⚠ **`PathRefused` 是唯一一条两侧归属不同的**：CLI 侧它是退出码 3（代码自相矛盾），
HTTP 侧「书 id 不合法」的产地是**客户端**，那是 400。
解法不是给异常分子类，是**在边界上先验**——路由第一件事调 `paths.check_sid(sid)`
就地翻 400；验过之后再从深处冒出来的 `PathRefused`（例如「一个 id 匹配到两个目录」）
就真的是我们的 bug。**「边界验过之后再抛就是 bug」是一条说得清的线。**
