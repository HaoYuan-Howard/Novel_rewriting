# HTTP 响应（`/api/**`）

Stage2 节点审阅的 `review` 范围提交、`obligation_receipt`、工作包与状态的
`review_coverage` 统一见 `docs/schema/stage2_review.md`。完整范围由后端派生；界面只展示
待审/继承数量、原问题及 typed 例外，不从摘要相似或主体删除推断接受。

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

Stage4A 已发行外部回执及审批历史同时承担内容寻址身份。新增逐章
`constraint_operations` 是精确兼容例外：缺省只省略这个新字段，不改变原有字段的 null；
非空处置必须完整保留。GET template 单独发布 `constraint_catalog` 全章目录，不能把旧回执的
字段缺席推断成当前没有历史义务。

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
| `GET /api/agent/capabilities` | `AgentCapabilitiesResponse`（registry 能力 + 本机 CLI/版本只读探针） | Agent 表单与设置页的唯一选项源；no-store |
| `GET /api/settings/agent-profiles` | `AgentProfileSettingsView`（当前 output base 的用户常用配置） | no-store；保留 unset/configured/unreadable 三态 |
| `POST /api/settings/agent-profiles` | `AgentProfileSettingsView`（请求体 `AgentProfileSettingsUpdate`） | CSRF 保护；按 expected revision CAS 保存或清空，只写 `_service/` |
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
| `POST /api/maintenance/retention/preview` / `apply` | `RetentionPlanView` / `RetentionApplyResultView`；apply 原样回传完整 plan + `confirmed=true` | 新 |
| `POST /api/maintenance/trash/restore/preview` / `apply` | `TrashRestorePlanView` / `TrashRestoreResultView`；apply 原样回传完整 plan + `confirmed=true`，真实移动走 CLI child | 新 |
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
| `POST /api/novels/:sid/approvals/stage5` | `ApprovalDoc`（请求体 `Stage5ClosureRequest`：actor/reason + opaque `review_token` + typed must-appear/group rulings） | 服务端重建当前完整 closure workpack 后原子保存回执；成功才打开 `gate.s5`，不启动作业 |
| `GET /api/novels/:sid/approvals/design/workpack` | `ApprovalWorkpack` v2（绑定 current A candidate + 完整 design subject；异常优先，只读、0 LLM） | 新；Commander/前端审阅入口，不预填决定 |
| `GET /api/novels/:sid/approvals/design/template?actor=&reviewer_kind=&review_run_id=&reason=` | `DesignReviewTemplate` | 四项 query 均必填；按当前 A 生成关闭式全书模板，可逐章复用仍完全绑定的历史 accept，但不代签；fresh B infeasible 时发布当前证明并禁止复用旧 accept |
| `POST /api/novels/:sid/approvals/design` | `ApprovalDoc`（请求体 `DesignReviewRequest`：actor/reviewer/run/reason + opaque `review_token` + 策略/逐章 typed rulings） | 服务端重建当前 Design workpack 与内部全部 SHA 后原子保存；不启动修订、B 或任何作业 |
| `POST /api/novels/:sid/approvals/gate/:key` | `ApprovalDoc` | 新 |
| `POST /api/novels/:sid/approvals/seen/:key` | `ApprovalDoc` | `key=read` 必须带 `BlueprintSeenRequest.review_token`；服务端重建同一完整工作包并复验，漂移零写入。raw 阅读事实只绑 review subject/contract，代码/规则漂移时 effective proof 暂不可用，复检通过且审阅身份未变则自动恢复；`key=global` 必须带 `Stage2GlobalSeenRequest`（`review_token` + `actor`）：token 由服务端从当前 Global 主体、review 完整字节、MainText 主体与 blocking scope 现算（`s2gr1_` 前缀），任一格变化即拒绝；proof 绑定 grounding + review 两份字节，review 改判后旧阅读事实过期 |
| `GET /api/novels/:sid/approvals/structure/workpack` | `ApprovalWorkpack` v2（绑定 current structure subject；异常优先，只读、0 LLM） | 新；完整披露 B concern / INFO / metric / UNVER，不自动裁决 |
| `GET /api/novels/:sid/approvals/stage6/workpack` | `Stage6ApprovalWorkpack` v3（绑定 current review identity、opaque validation seal 与 A/B 完整证据；只读、0 LLM；不输出 code/rules 原料） | 定稿前唯一 `s6r3_` opaque-token 阅读面，不自动 pick；旧 v2 包只读 |
| `GET /api/novels/:sid/approvals/stage7/workpack` | `Stage7ApprovalWorkpack` v3（绑定 current review identity、opaque validation seal 与完整替换证据；只读、0 LLM；不输出 code/rules 原料） | 定稿前唯一 `s7r3_` opaque-token 阅读面，不自动批准；旧 v2 包只读 |
| `POST /api/novels/:sid/approvals/structure` | `ApprovalDoc`（请求体 `StructureApprovalRequest`：actor/reason + opaque `review_token` + 恰好覆盖全书的逐章 `accept / request_replan`） | 服务端在同书锁内重建 current structure subject；typed 裁决不启动作业 |
| `POST /api/novels/:sid/approvals/writing` | `ApprovalDoc`（请求体 `WritingApprovalRequest`：actor/reason + opaque `review_token`） | 服务端重建当前 Stage1/Plan/WriterContext/测量契约主体；不接受空 body 或 caller 自算 subject，不启动作业 |
| `POST /api/novels/:sid/approvals/reset` | `ApprovalDoc` | 新 |
| `POST /api/novels/:sid/approvals/backfill` | `ApprovalBackfillResult`（按盘面显式补录，不在 GET 时静默发生） | 新 |
| `GET /api/novels/:sid/reading/:o` | `ReadingView` | 新 |
| `GET /api/novels/:sid/stage2/blueprint-review-workpack` | `BlueprintReviewWorkpack` | 绑定 current formal/INVALID Blueprint、MainText、完整可见 check 与审阅契约，另携 validation seal 和 opaque review token；异常优先但全章保留，只读、不自动决定 |
| `GET /api/novels/:sid/stage2/global` | `GlobalView` | 新 |
| `GET /api/novels/:sid/stage2/global-review` | `GlobalReviewView`（含当前完整 subject 重算状态、逐项审计与 `review_token`） | `review_token` 只在 review/grounding/MainText 都 current 时现算，否则 `null`；页面据此决定是否提供 seen.global 确认动作 |
| `POST /api/novels/:sid/stage2/global-review` | `JobRecord`（请求体 `GlobalReviewDecisionSet` v2：逐项决定，或绑定 source/scope/workpack hash 与正文章全集的 blocking scope + 逐项例外；exit 0 要求 blocking generation-safe：全部 PASS，或仅余显式完整组隔离的 secret/relation/world-rule UNVER） | 新 |
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
| `POST /api/agent/runs/:id/stop` | `AgentRunRecord`（202） | 只受理永久终止：后台先关 admission gate、terminate 全部 Commander/Executor 子进程树并 drain，闭合后才追加 ended；stop 后不可 resume，HTTP 返回不冒充 drain 已完成 |
| `POST /api/agent/runs/:id/pause` | `AgentRunRecord`（202） | 只受理暂停：立即停止接纳新节点，后台等待当前 claim/beacon 闭合后才追加 paused；不取消正常付费节点，HTTP 返回不冒充已 paused |
| `POST /api/agent/runs/:id/resume` | `AgentRunRecord`（202） | 受理后由后台同身份续 current epoch/provider session；代码或 skill 漂移时同 parent run 开新 epoch并免费复检 |
| `POST /api/novels/:sid/review/:o/edit/preview` | `DraftEditPreview`（请求体 `DraftEditPreviewRequest`） | 同步、只读、0 成本；与正式 apply 共用归一/判据/subject 构造，不写一次性文件、产物或 Review |
| `POST /api/novels/:sid/review/:o/edit` | `JobRecord`（请求体 `DraftEditRequest`） | 新 |
| `POST /api/novels/:sid/stage5/trim/:o/retain-pretrim` | `JobRecord`（请求体 `TrimResolutionRequest`；只允许保留原稿） | 新 |
| `POST /api/novels/:sid/stage5/trim/retain-pretrim-batch` | `JobRecord`（请求体 `BatchTrimResolutionRequest`） | 整批先核每章 audit/chapter 双 SHA，任一漂移则零写入；同一 manifest 中断后可幂等续写剩余章 |
| `POST /api/novels/:sid/stage6/approve` | `JobRecord`（请求体 `HookApproveRequest`：pick/actor/reason + opaque `review_token`） | 新 |
| `POST /api/novels/:sid/stage7/approve` | `JobRecord`（请求体 `NameMapApproveRequest`：actor/reason + opaque `review_token`） | 新 |

> #### `ApprovalWorkpack` v2 是只读阅读面，不是决定模板
>
> 两个端点共用现役 v2 外壳：`v=2`、`sid`、`kind=design｜structure`、当前完整
> `subject_sha256`、封装该 subject 的 `review_token`、设计面必有而结构面必须没有的 `candidate_sha256`、完整有序
> `chapter_ordinals[]`、`disposition`、可选 `current_decision_sha256`，以及固定六组 `groups[]`。六组顺序恒为
> `blocking / semantic_attention / disclosure / metric_only / phase_deferred / coverage`；
> 空组也必须带 `scanned` 分母出线，不能用裁掉空数组暗示「没扫」。
>
> `WorkpackItem` 只投影服务端已算事实：`code`、`rule`、`sev`、`status`、
> `chapter_ordinal`、`scene_refs`、`refs`、`repair_owner`、`detail`、`effect`、
> `allowed_actions`、`changed`、`historical_accept_reusable`、`source_decision_sha256`、`review_token`、`reading`。
> `allowed_actions` 是关闭集 `accept / request_repair / request_replan`，
> 不是 argv；INFO、metric、UNVER、skipped 和非 blocking/coverage effect 恒为空。coverage 恰覆盖
> `chapter_ordinals`，changed 章在前、段内按章号升序；changed 与 exact historical accept reuse
> 互斥且穷尽。工作包没有 actor/reason/decision 字段，GET 不写审批账、不启动 job，也不替用户签字。
> Stage4/6/7 工作包 disposition 均为 `review_required / already_current`；旧 frozen-v6 工作包退役，
> 版本不匹配明确拒绝。盘上 raw typed 决定不因前置闸暂时失效而删除；重新闭合且
> subject/contract 未变时可恢复 `already_current` 并携 `current_decision_sha256`。Design/Structure POST 只回交本 workpack
> 的 opaque `review_token` 与 typed rulings；Stage4A 完整章 edit 使用独立 `s4er1_…` token。三者都由
> 服务端现场重建内部 subject/contract/validation 身份，不共享也不要求调用方搬运多枚 SHA。
>
> v2 在外壳增加顶层 `reading`（`kind=stage4_workpack_reading_v1`：`legend`、design 面的
> `reconstruction_strategy / chapter_weights`、structure 面的 `global_binding_concerns`），并让每个
> coverage 行携带 `reading`（design 面 `stage4_design_chapter_reading_v1`：章功能/权重/重写要点/
> `structure_decision`、盲真相 `facts` 表、goal/因果边/关系/入口收束状态与逐场的 alias；structure 面
> `stage4_structure_chapter_reading_v1`：本章 `facts` 表（id→claim/kind/obligation）、逐场 slot/purpose/
> role/delivery/transition/budget 与 land/context/turn/entry/exit 的 id、舞台 time_layer/place/present、
> 材料四组计数与摘录、本章 `structure_metrics`
> 与 `alignment`、解开 closed_refs 的 `binding_concerns`、`signature_conflict_codes`）。`reading` 只能出现在
> coverage 行且类型随 `kind`，它不进 subject/token/proof；`metric_only` 只保留一行摘要（`refs=[]`），
> 完整读数以逐章 reading 承载。渲染落点 `novel/stages/stage4/reading.py`。

> #### Stage6/7 定稿 workpack 把“看到的证据”与 approve 乐观锁绑在一起
>
> 两种包公共携带 `v/kind/sid/review_identity_sha256/review_contract_version/validation_seal_sha256/
> review_token/check/disposition/current_approval?`。`check` 是完整分母 + 四态计数 + provenance +
> 全部非 ok 行 + 正常 metric 的 exception-first 投影；没有因紧凑而删除异常。review identity 排除
> disposition/current approval 与纯机械身份，批准或组装不会自废；代码/规则变化只滚 validation seal，
> 业务证据、可见 finding 或审阅契约变化才改变人审适用性。
>
> Stage6 另携三条完整候选（正文、subject SHA、mech/findings、semantic 状态、合并 decision、每名 judge
> 的 verdict/reason/evidence、质量分/名次）与 A/B 原始 `judge_rankings`。Stage7 另携完整
> 内部 decision subject、surname map、characters/aliases、extra subs、kept 明细、逐旧字面的真实
> `replacement_hits`、residual 上下文/baseline、book findings 与 identity。approve 请求必须同时提交
> 同一份包的 opaque `review_token`；服务端重建后任一漂移关闭式拒绝。`already_current` 必须携仍绑定本包的
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
其中正文外发声明由 capability registry 把冻结 profiles 展开成实际可达的 Commander 与逐节点
executor 具体 provider 路由；这只是同一 bool 授权的精确接收方边界，不增加第三条授权轴。
preview 可以在任一项为 false 时 0 成本展开；单本 start 与 batch 则在任何 Commander probe/spawn/wire
之前同时要求二者为 true。`profiles` 键同样恒在：非空值经 registry 解析并进入真实 Commander；
`null` 只保留请求兼容形状，当前 `default_profiles=null` 时 preview/start/batch 均关闭式拒绝，不能代选。

`GET /api/agent/capabilities` 是 Commander 与 executor backend 选择面的唯一公开数据源：

原配置续跑通过 `GET /api/agent/runs/:run_id/continuation` 读取历史完整快照和可用性；
preview/start 都携相同 `RunContinuationSource` 并由后端复验。运行页工作单元的
`GET/POST /api/novels/:sid/work-units/:unit_id/grant` 提供只读累计成本、历史授权与人工追加。
详细的 v2 ledger、停止证明、锁内 CAS 和幂等规则见 `docs/schema/work_units.md`。

| 模型 | 字段 | 约束 |
|---|---|---|
| `AgentCapabilitiesResponse` | `drivers[]` · `legacy_providers[]` · `executor_nodes[]` · `default_profiles` | `executor_nodes` 按 `AGENT_EXECUTOR_NODES` 完整有序发布，前端不维护第二份节点表。`default_profiles` 是经整书验收后才可发布的产品预置配置，不是 backend 模式选择器；当前明确为 `null`，不能取任一数组首项猜 |
| `AgentDriverCapability` | `id` · `provider` · `label` · `roles[]` · `availability` · `availability_reason` · `version` · `models[]` | `id` 是可执行 driver 身份，`provider` 只是供应商元数据，`label` 只展示。可用性四态为 `ready / missing / unknown / unauthenticated` |
| `AgentModelCapability` | `id` · `label` · `reasoning_efforts[]` · `execution_modes[]` | 精确 `id` 进入 argv/审计，label 可改；effort 与 execution mode 正交，只能提交该 model 实际发布的组合 |
| `AgentProfile` | `driver` · `model` · `reasoning_effort` · `execution_mode` | schema 只承载选择；driver/角色/model/effort/mode 的组合由同一个 capability registry 关闭式校验，客户端不得自造笛卡尔积 |
| `LegacyProviderCapability` | `id` · `label` · `models[]` | 直接 LLM API adapter 的稳定身份；当前只发布 DeepSeek，模型能力里继续给出 thinking/effort 与精确 `executor_nodes[]`，前端不得按模型名猜节点适用面 |
| `LegacyLlmProfile` | `provider` · `model` · `reasoning_effort` · `thinking_mode` | 只保存改变模型行为的稳定选择，不保存 API key、base URL、代理、超时或缓存目录 |
| `ExecutorBackend` | `{kind:'agent',profile:AgentProfile}` 或 `{kind:'legacy',profile:LegacyLlmProfile}` | `kind` 是唯一模式选择器；两套 profile 不得混搭，运行中也不得自动降级到另一种 backend |
| `PipelineAgentProfiles` | `schema_version=2` · `commander` · `executor_default` · `executor_overrides{node: backend}` | Commander 固定使用 AgentProfile；默认 executor 和逐节点覆盖使用 `ExecutorBackend`。override 键只接受响应中的 executor 节点 |
| `PipelineConcurrency` | `node_overrides{node: int}` · `per_book` · `per_provider` | `PipelineAgentProfiles.concurrency` 的可选精确配置；随常用配置、preview/start、batch 与原配置续跑保留。缺失时省略序列化，不改写旧身份 |
| `PipelineConcurrencyCapability` | `nodes[]` · `node_choices[]` · `default_node` · `defaults` · `total_min` · `total_max` · `scope` | `AgentCapabilitiesResponse.concurrency` 发布的唯一选择面；当前总槽作用域为同一输出根。界面不复制节点、档位和数值边界 |

Agent 探针只在本机解析 CLI 并读取版本，**不发模型请求、不产生费用**；因此 `ready` 只证明可执行文件与
版本探针成功，不证明账号认证、模型 entitlement 或真实调用可用。当前 registry 只发布实际受测的
execution mode；`ultra` 既不是 reasoning effort，也尚未被发布，调用方不得凭 UI 文案自行放开。
Legacy capability 是 provider driver 的静态关闭表，也不读取密钥、不联网；它同样不证明账号、额度或
真实请求可用。

用户常用配置与上述 `default_profiles` 分开建模：

| 模型 | 字段 | 约束 |
|---|---|---|
| `AgentProfileSettingsView` | `state` · `profiles` · `revision` · `updated_at` · `issue` | `configured` 才携完整 profiles；从未配置或显式清空为 `unset`；文件字节存在但不合当前契约为 `unreadable`，携原始字节 revision 与不回显正文的诊断。读取失败不得静默回退 |
| `AgentProfileSettingsUpdate` | `profiles` · `expected_revision` | profiles 非空时再次经 capability registry 关闭式校验并显式物化 execution mode；null 表示清空。revision 不等返回 409 `AgentProfileSettingsConflict`，不得最后写入者覆盖 |

设置文件固定为 `<base>/_service/agent_profile_settings.json`。v2 保存 Commander 的 AgentProfile，以及
默认/逐节点 executor 的显式 `kind + profile`；API key、CLI 登录态、base URL、代理、付费授权、正文外发
授权与 prompt 均不进入该文件。旧 v1 裸 AgentProfile 只在读边界无损包成 `kind=agent`，读取不会擅自
重写，下一次成功保存才发 v2。设置只初始化未来运行表单：单次运行仍可
临时覆盖，start/batch 仍逐次要求两项独立授权并冻结自己的 profiles，已启动、已排队和历史 run 不读取
当前设置。`default_profiles` 继续保持产品验收状态，用户保存偏好不得使它从 null 变成非空。

直接 `POST /api/jobs/preview` / `POST /api/jobs` 共用 `plan_start`，把解析后的
`executor_backend` 同时冻结进 `Step.executor_backend`、`JobStart.executor_backend` 与 argv；子 CLI
再把同一选择交给 `RunContext`。旧请求里的 `executor_profile` 只在输入边界无损迁移为 Agent variant，
公开响应和新请求不再发旧字段。独立旧 CLI/客户端省略 backend 的 DeepSeek 路只作兼容，新 AgentRun、
设置页和新 preview/start 都不得靠字段缺失猜 Legacy。

Agent backend 当前只接受以下 **8 节点 / 13 种 flag-token 组合**：

| node | token 组合 |
|---|---|
| `stage2.global` | `[]` |
| `stage2.blueprint` | `[]` |
| `stage4.design` | `[]`、`[--review-token]`、`[--replan, --review-token]` |
| `stage4.plan` | `[]` |
| `stage5.write` | `[]`、`[--chapters]`、`[--revise]`、`[--revise,--chapters]` |
| `stage6.hook` | `[]` |
| `stage6.semantic` | `[]` |
| `stage7.rename` | `[]` |

`--chapters` 后的章号是值，不是第十三种 token。各 producer 固定 typed 完整 replacement；
agent 只交候选，代码负责业务 HARD、结构化 feedback、append-only checkpoint、正式 journal、typed
readback、current/fresh 终验和 accepted-attempt adoption。`kind=agent` 的节点 dispatch 先于 Legacy
client 构造，不能静默回退直接 API。

Legacy backend 不借用 Agent 的 flag 矩阵，而由 `LegacyLlmDriver` 对 provider/model/thinking/effort/node
关闭式校验，并继续服从节点本身的合法 flags、schema、HARD 与提交契约。当前 DeepSeek V4 Flash 不支持
契约钉死 Pro 的 `stage4.design`、`stage4.plan`、`stage6.semantic`；V4 Pro 支持现役九个 executor 节点。
两种 backend 任一失败都不自动切换。

direct `PreviewRequest.egress_authorized` 默认 false（不要与上方 outer `AgentRunRequest` 当前默认 true
混读）；resolved backend 非空时 builder 必须收到显式 bool，并将它同源冻结进 `JobStart` 和付费
`Step`，只有 true 才把 `--allow-egress` 加进 argv。false preview 可返回完整但不可执行的命令，真 start
与手敲 CLI 在 job、probe、driver、wire 前拒绝。backend 为空时 false 归一为 `null`，true 关闭式拒绝。
Stage6 A/B 的 session/checkpoint/两阶段提交与代码裁决不因 backend 种类改变。

outer `AgentRunRequest.profiles` 已接入 provider-neutral Commander 和显式 executor backend：
preview/start/batch 共用唯一 plan，
把规范化 base、起始代码指纹、resolved profiles、stdin-only invocation 与两项授权冻结进 started/queue；
排队出队不重读 capability 或默认。Commander child 只持 run id 与 contract SHA-256，再从 append-only
started 事件复核整份配置；operator runner 按 exact node/flags 选 default executor 或 override，不能手拼
任一 backend 参数。Claude Code / Codex Commander 的 prompt 只走 stdin，argv/env/log 不复制全文；Commander
本身另记 `role=commander,node=null` 的 session 与 ledger，实际模型/effort 缺失保持 unknown，漂移、串
provider session 或账/审计不闭合只可把 delivery 降成 unknown。进程 state 与盘面 delivery outcome 正交。

页面现已从 capability 动态构造 Commander、默认 executor 与逐节点 override 表单；新表单优先载入
`configured` 用户常用配置，其次才用非空产品默认，两者都没有时必须显式选全，不取数组第一项；设置
不可读或请求失败时关闭预览/启动。单本与批量 start 直接消费最后一次成功 preview 保存的同一请求快照；
profiles、两项授权或 capability 有任何漂移都先使 preview 失效，不能用新默认重解释旧确认。历史 run 的
`profiles=null` 只显示 unknown；direct Job 的 executor backend / egress 只读展示仍与 outer Commander
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
| `RetentionPlanView` | `wire_version` · `generated_at` · `policy` · `examined` · `candidates[]` · `plan_token` | preview 完整冻结策略、扫描分母与每项路径/mtime/size/SHA；apply 不接受前端重拼的候选 |
| `TrashRestorePlanView` | `wire_version` · `generated_at` · `trash_id` · `sid` · `title` · `original_dirname` · metadata/manifest 三枚 SHA · `plan_token` | book/approval/service run 三面由 manifest 闭合；持锁后再次核 token/CAS，拒绝覆盖 live 位置 |
| `ApprovalBackfillResult` | `approvals` · `backfilled[]` | `backfilled` 是本次**按正式盘面补录**的 gate id，空数组表示无事可补；界面必须明确这些不是用户逐屏点过的验收记录。该入口不制造 `seen.read/global`，也不因 Stage2 产物齐备越过未审的 s2 |
| `Stage2GlobalSeenRequest / Stage2FinalReviewRequest` | 前者：`review_token`（`s2gr1_`）+ `actor`；后者：`actor` + `reason` + `global_review_token` + `blueprint_review_token` | 前者记录 `seen.global` 的当前已读主体；已读不授予节点接受。后者只核验已落盘的 Global/BP 两份当前节点接受并补齐 `s0 → s1 → seen.read → seen.global → s2`，经 `close_stage2_accepted` 或 CLI `stage2.blueprint --close-review` 零模型恢复，不产生第四席终审决定 |
| `Stage2NodeReviewRequest / Stage2NodeReviewDecision / Stage2NodeReviewView` | 请求：`node / review_token / decision / actor / reason / chapter_ordinals / owner`；决定增加 `review_subject_sha256 / review_contract_version / decided_at / review_scope_sha256`；视图：`state / current_decision / history` | Global/BP 分别接受；`decision` 为 accept/rework/uncertain/stop。返工必须指向登记 owner，Blueprint owner 的返工明确章节；Global C 不拥有未生成 Blueprint 的修复权。HTTP/CLI 使用同一 shape；视图 missing/accepted/rework/uncertain/stopped/expired 不混同，未接受不给 paid 许可 |
| `BlueprintReviewWorkpack / BlueprintSeenRequest` | 工作包：内部 review identity、`stage2-blueprint-review-v7`、opaque validation seal、`bpr4_` review token、全章语义面与 `review_delta`；已读请求：`review_token` | 页面、已读和节点接受均绑定同一份 current workpack；`seen.read` 只证明阅读，不能代替 Blueprint Commander 接受。模型/人工提交前服务端重建并核 token。代码复验与实际可见主体变化分别处理，旧 bpr2/bpr3 token 只读、不接受新提交；delta 接受基线前移不使当前 token 自失效 |
| `ApprovalDoc` | `v=6` · `sid` · `gate` · `seen` · `stage2_reviews` · `writing` · `design_review` · `structure` · `writing_approval` · `closure` · `spawns` | HTTP v6 不等于 `_service/approvals/<sid>.json` 的盘上 v7。`stage2_reviews` 分别投影 Global/BP 当前决定与历史，旧已读不生成接受。公开 gate 没有 s3；`design_review` 是 A 的独立相位，不是 gate：给出当前 candidate/aggregate hash、typed 当前决定、历史与 `unavailable / missing / repair_requested / strategy_replan_requested / accepted / suspended / expired`；只有 current `accepted` 才能授权 B，`suspended` 保留当前 raw 决定但不携 action token。`structure` 是 B 编译 Plan 后的另一张票，另给当前 subject、B `binding_concerns`、typed 当前裁决、历史与 `unavailable / missing / accepted / replan_requested / expired`；`gate.s4=true` 只可能来自这张当前 `accepted`。`writing_approval` 同样给出 available、current subject、typed current decision/history；新 typed writing 决定绑定 Stage1 正文、Stage4 Plan、fresh WriterContext 与测量契约。`closure` 是 Stage5 当前整书工作包、typed 决定与不可擦除历史，`gate.s5=true` 只可能来自 current `closed`。当前写入要求已有新 clean 工作流 manifest，backfill 只接受当前盘上 v7；旧版本、未来版本和损坏均返回明确错误，不迁移或重签。历史恢复使用配对的冻结旧代码与旧根。旧 read proof 不升级成节点接受，Structure subject 未携 current `STRUCTURE_REVIEW_CONTRACT` 的历史决定不重签 s4，普通 GET 不迁移 |
| `Stage5ClosureView / Workpack` | `state` · `workpack?` · `next_step?` · `current_decision?` · `history[]`；工作包含 Review 身份、`planned/missing`、`chapter_subjects[]`、`.INVALID` / HARD、`unresolved_trim_items[]`、逐项 must-appear、fact/unver 组、披露/已处置计数与 `workpack_sha256` | `state ∈ missing｜review_required｜blocked｜ready｜closed｜expired`，只由服务端重算，页面不得按计数重判。`review_required` 表示没有可接受的 current 最终 Review，不能携 current workpack/decision，且 `next_step` 必须指出重跑；`chapter_subjects[]` 精确覆盖全部当前 canonical 计划章；`unresolved_trim_items[]` 是章号、audit SHA 与正文 SHA 的精确批量提交面，章号投影必须等于 `unresolved_trim_chapters`。决定或正文漂移后旧决定只留 history，不能继续充当 current |
| `Stage5ClosureTemplate / Request / Decision` | Template 是服务端完整 materialized workpack + `review_token`；Request 只有 `actor/reason/review_token/must_appear[]/group_acks[]`；Decision 在 Request 上加 `decision_sha256/decided_at` | must-appear 与 group 的 finding/chapter/review/workpack SHA 只在模板和盘上完整决定中物化；调用方只提交 token 与 typed ruling。服务端展开 current workpack 后要求 ruling 精确覆盖；模板 placeholder/token 不进入 Request，确认不会把 UNVER 改写成 PASS |
| `BatchTrimResolutionRequest` | `actor` · 共享 `reason` · `items[{output_ordinal,audit_sha256,chapter_sha256,reason}]` | actor/共享理由非空，章号升序去重；单项空理由沿用共享理由。服务端在任何写入前重读并核完整批次，按章幂等落 hash-bound resolution；浏览器只复制当前 closure workpack 的 exact item，不自行计算 SHA |
| `DesignReviewTemplate / Request` | Template 是服务端完整 materialized subject + `review_token` + 全章 `constraint_catalog`；Request 只有 `actor/reviewer_kind/review_run_id/general_reason/review_token/strategy/chapters[]` | 一次提交必须覆盖当前完整策略与升序全章集。策略决定为 `accept / request_replan`；逐章决定为 `accept / request_repair`，关系 repair 使用 typed relation effect。历史目录逐章给当前集合摘要，以及每条约束的 SHA、typed payload 和来源决定列表；新裁决用可选 `constraint_operations` 绑定 expected_constraint_set_sha256，并对全部 active SHA 明确 keep/withdraw/supersede，替代项携 typed replacements。旧回执缺省省略该新字段；新候选有 active 时不得省略处置。candidate/aggregate/chapter subject、binding infeasibility、repair capability、placeholder 与历史 reuse 只在 Template 展示并由服务端重建，不让 caller 回传。subject 只公开 opaque alias 的盲真相与现场结构证据；自然语言 reason 只作审计，不进入修订 prompt |
| `DesignReviewDecision / View` | Decision 是服务端 materialized 的完整 candidate/aggregate/chapter subject、review contract、typed rulings、actor/reviewer/run/reason + `decision_sha256/decided_at`；View 另含 state/current hashes/`binding_infeasibility?`/current/history | POST 的 opaque token 只用于找到并复验本次 exact materialized subject，不能删减盘上审计。历史 accept 只有在全局 basis、strategy epoch、本章主体及可见 B 证据仍相同时才可显式复用；拒绝不会自动变 accept |
| `JobList` | `jobs[]`（`JobRecord`） | 抓包 `jobs.json` 就是 `{"jobs":[]}`——**一个样本都没有**，所以元素形状只能从 `_orchestration.md` 来 |
| `LogChunk` | `text` · `next_byte` | 按**字节**续读。⚠ 中文一字跨 3 字节，**只在读的一侧解码**（写的一侧按字节追加，见 `_orchestration.md`）。`next_byte` 原样回传给下一次 `?offset=` |
| `LogStreamEvent` | `text` · `next_byte` · `done` · `state` | SSE 的 `id` 与 `next_byte` 相同；普通 `log` 事件为 `done=false,state=null`，唯一的 `end` 事件为 `done=true,state=ok|landed_red|failed|unknown` 并随即释放连接。断线时浏览器用 `Last-Event-ID` 续读；显式 query offset 与它取较大者，避免刷新倒退 |

`PreviewRequest`：`sid`（已有书为非空字符串；首次 `stage0.remove --source` 为 `null`）·
`node` · `flags[]` · `executor_backend`（`ExecutorBackend \| null`；迁移期未指定为 `null`）·
`egress_authorized`（`bool`，默认 false；只授权 direct executor 外发正文）。
请求外壳是 strict，任何旧 `mode` 键都返回 422；老
`jobs.jsonl` 的兼容解码只在服务内部磁盘边界发生，不进入本请求或公开响应模型。
旧客户端提交 `executor_profile` 时只无损迁移成 `kind=agent`，新响应不再发旧字段。Agent variant 只允许
`_orchestration.md` 精确表中的 8 节点 / 13 组合；Legacy variant 按 provider/model/node 能力表关闭式
校验。自动追跑的 `--check-only` Step 不继承 backend。非空 backend 必须显式携带 bool egress：false
允许预览但真启动返回 `ApprovalRequired`，true 才进入 argv；无 backend 时 false 归一成响应快照
`null`，true 拒绝。
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
| `blueprint_state` | 对象 | 是 | `stage2/chapter_blueprints.json` 的 `ArtifactState`（formal 与活动 `.INVALID` 分栏一次拿全）。`blueprint=false` 的两种来路——没跑 / 读不回——靠它的 `exists` 与 `readable` 分 |
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
| `structure_subject_sha256` | SHA-256 或 null | 是 | 正式计划时由**本次读出的同一个 `PlanChapters` 对象**计算，并与 `index[]` / `plan_chapter` 同快照；恒等基线只能为 null。它只供显示与 currentness 对照；正式结构裁决必须从 Structure ApprovalWorkpack 取得 opaque `review_token`，不能拿本 view 的 SHA 自建提交载荷 |
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
| `POST /api/novels/:sid/stage6/approve` | `HookApproveRequest`（`pick`、`actor`、`reason`、opaque `review_token`） | **不收文本**；服务端在同书锁内重建完整候选 workpack 并核 token。改字是另一个节点（`stage6.hook --apply-edit`）；**`stage6/edit` / `stage7/edit` 刻意不在 HTTP**，走 CLI |
| `POST /api/novels/:sid/stage7/approve` | `NameMapApproveRequest`（`actor`、`reason`、opaque `review_token`） | 一本书只有一份人物表，因此不带候选序号；服务端重建决策面与可见 evidence 后核 token。成功后 s7 proof 绑定完整 typed 审批凭据，但不绑定 `book_txt` 等派生账，正常 assemble 不会自废批准 |

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


### Blueprint 交叉复核差异

`BlueprintReviewWorkpack.review_delta` 是只读 `BlueprintReviewDelta`，包括 `mode`（initial/unchanged/targeted/full）、
基线与当前 review subject SHA、`changed_global_items[]`、改变的 Blueprint 章、受影响章、可复用接受依据的章与原因。
每项 `BlueprintReviewGlobalChange` 明示 item/path、added/removed/changed、changed_fields、前后主体 SHA、
受影响章与 references、scope_reason。无法证明局部范围时保持 full；前端只呈现服务端范围，不重新计算依赖。
`Stage2NodeReviewDecision.review_scope_sha256` 可为空，保存实际已审范围的摘要。delta 不进入 review token；
接受后只推进基线，不让刚保存的当前接受自行过期。公共 TypeScript 与页面沿用同形字段。

### 写作上下文的即时读数

Stage4 `ctx_chapter` 使用 `CtxChapterView`；章节主体来自当前 WriterContext v7，
`from_source_ordinals / budget_chars / source_chars` 由章号、场景预算和 Stage1 源正文现算。
源文缺失或不可读时 `source_chars=null`，界面显示“未知”。展示字段不能作为新任务书提交；
Stage5 的成稿字数、预算及重合率也经后端唯一度量入口现算，不从 Chapter v3 读取副本。
