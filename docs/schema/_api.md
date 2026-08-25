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
⇒ 配一条测试：S-06 的响应 JSON 里必须有字面量 `_provenance` 这个键。

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
Stage 3/5 各只有一条生产 prompt 路径，前端和调用方无需也不能选择实验臂。

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
| `GET /api/novels` | `NovelList` | 新 |
| `GET /api/novels/:sid` | `NovelOverview` | **复用** `_orchestration.md` |
| `GET /api/novels/:sid/stage3/style` | `StyleView` | 新 |
| `GET /api/novels/:sid/check/:kind` | `CheckReport` | **复用** `_report.md` |
| `GET /api/jobs?sid=` | `JobList` | 新 |
| `GET /api/jobs/:id` | `JobRecord` | **复用** `_orchestration.md` |
| `GET /api/jobs/:id/log?offset=N` | `LogChunk` | 新 |
| `GET /api/jobs/:id/stream?offset=N` | SSE：`LogStreamEvent` | 新；主日志通道 |
| `POST /api/jobs/preview` | `JobStart`（请求体 `PreviewRequest`） | **复用** `_orchestration.md` |
| `POST /api/jobs` | `JobRecord`（202；请求体 `PreviewRequest`） | **复用** `_orchestration.md` |
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
| `POST /api/novels/:sid/stage0/approve` | `JobRecord`（请求体 `Stage0ApprovalRequest`：自报 `actor` + `reason`） | 新 |
| `GET /api/novels/:sid/stage0/candidate` | `Stage0CandidateView`（待裁决的那条机器 revision + **当前稿的读数**） | 新 |
| `POST /api/novels/:sid/stage0/candidate/adopt` | `JobRecord`（请求体 `Stage0CandidateDecisionRequest`） | 新 |
| `POST /api/novels/:sid/stage0/candidate/dismiss` | `JobRecord`（同一请求体） | 新 |
| `POST /api/novels/:sid/stage1/partition` | `JobRecord` | 新 |
| `GET /api/novels/:sid/stage1` | `Stage1View`（一次返回连续分区、原文章号诊断与 `anomaly_count`；全部由当前 clean 复扫） | 新 |
| `GET /api/novels/:sid/approvals` | `ApprovalDoc` | 新 |
| `POST /api/novels/:sid/approvals/gate/:key` | `ApprovalDoc` | 新 |
| `POST /api/novels/:sid/approvals/seen/:key` | `ApprovalDoc` | 新 |
| `POST /api/novels/:sid/approvals/structure` | `ApprovalDoc`（请求体 `StructureApprovalRequest`：完整结构 subject SHA-256、actor、总理由与恰好覆盖全书的逐章 `accept / request_replan`） | 新；typed Stage4 结构裁决，提交本身不启动作业 |
| `POST /api/novels/:sid/approvals/writing` | `ApprovalDoc` | 新 |
| `POST /api/novels/:sid/approvals/reset` | `ApprovalDoc` | 新 |
| `POST /api/novels/:sid/approvals/backfill` | `ApprovalBackfillResult`（按盘面显式补录，不在 GET 时静默发生） | 新 |
| `GET /api/novels/:sid/reading/:o` | `ReadingView` | 新 |
| `GET /api/novels/:sid/stage2/global` | `GlobalView` | 新 |
| `GET /api/novels/:sid/stage2/global-review` | `GlobalReviewView`（含当前完整 subject 重算状态与逐项审计） | 新 |
| `POST /api/novels/:sid/stage2/global-review` | `JobRecord`（请求体 `GlobalReviewDecisionSet`；仅全部 PASS 的 exit 0 可盖 s2） | 新 |
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
| `POST /api/novels/:sid/review/:o/edit` | `JobRecord`（请求体 `DraftEditRequest`） | 新 |
| `POST /api/novels/:sid/stage5/trim/:o/retain-pretrim` | `JobRecord`（请求体 `TrimResolutionRequest`；只允许保留原稿） | 新 |
| `POST /api/novels/:sid/stage6/approve` | `JobRecord`（请求体 `HookApproveRequest`） | 新 |
| `POST /api/novels/:sid/stage7/approve` | `JobRecord`（无请求体） | 新 |

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

缺省 `true` 是当前产品偏好，不代表两项授权合并。UI 必须显示两个独立复选框并显式提交二者；
`AgentRunPreview` 回显两个布尔值，preview 与真跑都由同一函数把两段授权原文写入 prompt。

通用启动返回 202 和 `running` 作业，由后台线程逐步启动 CLI 子进程。同一 `sid` 的书级
作业互斥并返回 409，不同小说可并行；Stage5 canonical 写作同样占书级键，并在启动前原子核对
current writing 授权、Review 状态与旧布局，不存在另一个按臂的并发口。

⚠ **一个响应模型都不许自己算判定**（`R-11.4`）。`badge` / `passed` / `counts`
只出现在 `CheckReport` 里，且由 `contracts/schema/report.py::badge_of` 折叠、
再由 `CheckReport` 的后置条件反过来断言——**服务端不重算，前端更不许算**。

---

## 四、`StyleView` —— S-06 Stage3 机械基线

`web/src/mocks/stage3_style.json` 是 v1 定性画像的冻结历史抓包，保留原字节与哈希但**不是现役
API 形状**；假后端在 `web/src/api/mock.ts::currentMockStyle` 只投影 v2 仍存在的字段。

⚠ **它是视图，不是产物。** 盘上的 `stage3/style_grounding.json` v2 提供存量机械基线；
当前代码同时读取 Stage1 免费重算。因此 `exists=false` 时仍可展示 `measured_now`，旧 v1 或坏
v2 则表现为 `exists=true, readable=false`，不能洗成“没运行”。

| 字段 | 类型 | 必填 | 取值面 / 说明 | 证据 |
|---|---|---|---|---|
| `sid` / `title` | 串 | 是 | **由 `novel/paths.py` 从目录名派生** | `派生` |
| `exists` | 布尔 | 是 | 盘上有没有那份产物 | `抓包` |
| `invalid_exists` | 布尔 | 是 | 有没有一份 `.INVALID` 在等人处理 | `抓包` |
| `readable` | 布尔 或 null | 是 | 当前 schema 可读；产物不存在时为 null | `设计` |
| `hook_copy_text` | 串 或 null | 是 | 开篇文案段原文（来自 stage1，与本层产物无关） | `抓包` |
| `measurable_stored` / `measured_now` | 对象 或 null | 是 | `Measurable`。前者来自产物，后者**现算** | `抓包` |
| `bands_stored` / `bands_now` | 对象 或 null | 是 | 由对应机械数字即时派生的展示档位，**不落盘** | `设计` |
| `measure_error` | 串 或 null | 是 | 量不出来时的**人话**。与 `measured_now` **恰好一个非空** | `抓包` |
| `measure_error_kind` | 串 或 null | 是 | **异常类名**（`UpstreamMissing` / `ArtifactUnreadable`） | `设计` |
| `drift` | 数组 **或 null** | 是 | 存/现两份实测的逐项差异 | `抓包`（形状）+ `设计`（三态） |
| `freshness` / `reason` / `ghosts` | 串 / 串 / 串数组 | 是 | `FRESHNESS` / `FRESHNESS_REASON` / 幽灵边**点名** | `设计` |
| `_provenance` | 对象 或 null | 是 | 产物里那一份，原样 | `抓包` |

### v2 形状裁决

**① 定性字段整体删除。** 当前响应没有 `voice`、`voice_extra`、`surface_habits` 或
`hook_style`。它们既不在 v2 产物，也不以空数组占位；空占位会让消费者误以为字段仍是契约的一部分。

**② 算不出来时不 404、不抛，填槽。** 三态由后置条件焊死：
`measured_now` 与 `measure_error` **恰好一个非空**，
同空或同非空一律炸。
⚠ 另出一格 `measure_error_kind`（**id 不是措辞**）：
「stage1 还没跑」与「stage1 的产物坏了」对人的下一步是**相反**的指示。

**③ `drift` 是 `数组 | null`，不是 `数组`。**
`[]` = 「两份实测比过了，没有漂移」；`null` = 「有一边算不出来，**无从比**」。
抓包里是 `[]`，把两者压成一个 `[]` 就回到本项目一路在防的那个病
（`Q-2`：「不知道」与「没问题」必须能区分）。

**④ `bands_error` 删除。**
`novel/core/bands.py::bands()` 在一个**合法的** `Measurable` 上是**全函数**
（每一项都是纯算术与查表，没有失败支）。⇒ `bands_now` 为 null 当且仅当
`measured_now` 为 null，而那件事已经由 `measure_error` 说过了。
一个只可能与另一格说同一件事的格子，就是**同一条信息的第二个落点**。
⇒ 载体上改成两条后置条件：`bands_now` 与 `measured_now` 同进同出、
`bands_stored` 与 `measurable_stored` 同进同出。

**⑤ `readable` 是三态（`是 / 否 / null`），不是布尔。**
`null` ⟺ 产物不在盘上——「不在盘上的东西读不读得回来」是个没有意义的问题。
⚠ `readable=false` 与 `exists=false` **必须分开**：盘上真有一份读不回来的产物
（`成年人不做选择` 那本的 `stage4/plan_chapters.json` 根本没有 `_provenance`），
折成「不存在」会让界面说「还没跑」——**而它跑过，只是那次把来源账丢了**。

**⑥ 存量区三格同进同出。** `measurable_stored`、`bands_stored`、`_provenance` 只有在
v2 正式产物存在且可读时才同时出现；半块在没有合法 producer 来源。

**⑦ 本视图不带 `badge` / `passed` / `counts`。** 那一屏的结论只能来自
`/api/novels/:sid/check/stage3`。视图给的是「两份数字并排放着让人自己看」
（`R-11` 节首语的原话），判定给的是「合不合格」——**混在一格里就是第二个判官**（`R-11.4`）。

---

## 五、四个小模型

| 模型 | 字段 | 说明 |
|---|---|---|
| `NovelList` | `base` · `novels[]`（`NovelOverview`） | 抓包 `novels.json` 的顶层就是 `{base, novels}`。⚠ **复用 `NovelOverview`，不另造一张轻量清单**——原型 `Web/novels.py` 那张第二清单的代价是 stage5 被迫整个排除在外，于是「有没有陈旧产物」那个徽章不覆盖成稿 |
| `TrashNovelResult` | `sid` · `title` · `trash_id` · `trash_rel` | `novel/orchestrator/trash.py` 的 CLI 子进程完成同盘移动后返回；`trash_rel` 是可恢复位置，不把“从语料库消失”偷换成“字节已永久销毁” |
| `ApprovalBackfillResult` | `approvals` · `backfilled[]` | `backfilled` 是本次**按正式盘面补录**的 gate id，空数组表示无事可补；界面必须明确这些不是用户逐屏点过的验收记录 |
| `ApprovalDoc` | `v=3` · `sid` · `gate` · `seen` · `writing` · `structure` · `spawns` | HTTP v3 不等于 `_service/approvals/<sid>.json` 的盘上 v4。`structure` 给出 current subject、typed 当前裁决、历史与 `unavailable / missing / accepted / replan_requested / expired` 状态；`gate.s4=true` 只可能来自当前 `accepted` 裁决。公共视图不再含 `deep`，writing 只授权 current canonical 全书写作。旧 v2/v3 内部记录仅由显式 backfill 收敛，普通 GET 不迁移 |
| `JobList` | `jobs[]`（`JobRecord`） | 抓包 `jobs.json` 就是 `{"jobs":[]}`——**一个样本都没有**，所以元素形状只能从 `_orchestration.md` 来 |
| `LogChunk` | `text` · `next_byte` | 按**字节**续读。⚠ 中文一字跨 3 字节，**只在读的一侧解码**（写的一侧按字节追加，见 `_orchestration.md`）。`next_byte` 原样回传给下一次 `?offset=` |
| `LogStreamEvent` | `text` · `next_byte` · `done` · `state` | SSE 的 `id` 与 `next_byte` 相同；普通 `log` 事件为 `done=false,state=null`，唯一的 `end` 事件为 `done=true,state=ok|landed_red|failed|unknown` 并随即释放连接。断线时浏览器用 `Last-Event-ID` 续读；显式 query offset 与它取较大者，避免刷新倒退 |

`PreviewRequest`：`sid`（已有书为非空字符串；首次 `stage0.remove --source` 为 `null`）·
`node` · `flags[]`。请求外壳是 strict，任何旧 `mode` 键都返回 422；老
`jobs.jsonl` 的兼容解码只在服务内部磁盘边界发生，不进入本请求或公开响应模型。
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
| `reconstruction_strategy` / `chapters` / `ordinal` | | 是 | `ordinal ∈ chapters`（后置条件） |
| `index[]` | 数组 | 是 | 章索引；每行带 `chapter_goal`、完整 `structure_decision`、预算、场数与隐藏材料数，供一次提交时核对全章集合与逐章裁决，不额外发 N 次请求；它**不是**完整 canonical subject 的公开展开。⚠ **没有 `in_band` / `style_hits`**——那是判据结论，唯一来源 `/check/stage4`（`R-11.4`），要并排显示就去 join 那份报告。`hidden=null` = ctx 不在盘上**判不了**，不编 0 |
| `plan_chapter` | 对象 | 是 | 章级三个 typed goal、`retain/reconstruct` 决定、语义契约、改写 brief 与有序 `scenes[]`；每场只展示公开模型实际下发的 purpose/role/delivery/transition、land/context/turn facts、state refs 与预算。已解析的 stage/material/participant 执行证据在 `ctx_chapter` 可用时从上下文查看，不虚构计划 view 不存在的字段 |
| `ctx_chapter` | 对象 或 null | 是 | 写手实际拿到的任务书。场景顺序以 `scenes[]` / `scenes[].slot` 为准；只下发执行所需事实、状态与证据，不给 Stage5 重新规划权 |
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
| `drafts[]` / `invalid_drafts[]` | 数组 | 是 | 正式稿与 `.INVALID` 分两个数组。`segments[]` 是盘上的唯一内容权威，保留 producer slot；`text` 必须等于代码按段顺序拼出的确定性投影，只供展示。`origin`（`human` / `llm`）由 `_provenance` 的载体类派生 |
| `trim_audit` | 对象或 null | 是 | 当前章仍绑定 current 正文、且尚未人工销账的超上限事实；对象只发 `output_ordinal/reason/context`，hash 锁由专用 retain 路由执行时从盘上重读，浏览器不代抄。`null` = 本章无待销账 trim |
| `drafts[].align` | 对象 或 null | 是 | **可空**：`null` = 对齐辅助尚未由服务端提供（本章无锚可对时 `align_rows` 为空、账仍在）。对齐账是对应关系不是判定，`note` 常驻这句话；锚点覆盖率必须常驻显示 |
| `drafts[].align.coverage_threshold` / `.coverage_state` | 浮点 / 串 | 是 | 阈值取自 `contracts/invariants.py::ANCHOR_COVERAGE_WARN`（0.6），`coverage_state ∈ low｜adequate` 由 `DraftAlignView` 的校验器按 `coverage` 与 threshold 反推 ⇒ **两格不可能各说各话**。前端只消费 `coverage_state`，一行阈值都不写（守卫 `tests/test_api_discipline.py::test_frontend_does_not_rejudge_anchor_coverage`） |
| `ctx_state` | 对象 | 是 | `ArtifactState` |

## 五f、`ReviewView` —— S-09 验收 / 修订

一份验收都没有 ⇒ 404（`UpstreamMissing`，前端渲染「先跑 stage5.review」）。
⚠ 判据表本身仍归 `/check/stage5`；本屏发的是**验收产物**（历史事实），
与「现在复检一遍」是两件事。

| 字段 | 类型 | 必填 | 说明 |
|---|---|---|---|
| `review_seq` / `sources` / `baselines` / `baselines_identity` / `book[]` | | 是 | 最新一份全书验收的账，原样 |
| `review_state` / `review_reasons[]` | `Stage5ReviewState` / str[] | 是 | `fresh｜stale｜unknown｜legacy_incompatible`（`missing` 由路由 404 表达，不能伪装成视图）；只有 fresh 的 reasons 为空。结论统一取 `review.review_status`，API 不重拼第二套 freshness |
| `finding_counts` | 对象 | 是 | 全书结论按 sev 现数，**四键恒在（0 也写）**。⚠ 不读产物的 `counts` 字段——那份是稀疏 Counter（`Review.counts` 登记过的病）；`index[]` 每行同理（后置条件逐行查） |
| `wontfix` | 整数 | 是 | 被人豁免的条数——豁免不删结论，单独计数 |
| `index[]` | 数组 | 是 | `unresolved` = 有收敛账（`unresolved_rounds > 0`）且未豁免的结论条数 |
| `chapter` | 对象 | 是 | 该章明细 + 当前正式稿的 `segments[]` 与派生 `text`（改稿入口按 slot 放在原文旁边）；两者同为 `null` 表示当前正式稿缺席，非空时服务端强制 `text` 等于 segments 投影。`stale` 是三态：验收判的那份与盘上当前那份还是不是同一份（整文件指纹）；`null` = 判不了。`rounds[]` 是盘上已归档的轮次号（`_rounds/`），升序——改稿历史从这里回看 |
| `_provenance` | 对象 | 是 | `ReviewProvenance` |

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
| `POST /api/novels/:sid/stage0/approve` | `Stage0ApprovalRequest`（`actor` + `reason`） | actor 是自报身份；空值拒绝。⚠ 零命中自动批已于 2026-08-21 停产、记录它的 `approval.origin` 一格已于 2026-08-22 删除 ⇒ **本端点是批准的唯一来源** |
| `POST /api/novels/:sid/stage0/candidate/adopt` / `dismiss` | `Stage0CandidateDecisionRequest`（`candidate_revision_id` + `expected_revision_id` 乐观锁 + actor/reason） | 页面上要决定的是**整版留哪一版**，不是逐个冲突选边——聚合根是 `{原文身份, 正文, 剔除账本, 告警}` 整团（`stage0.md` §2.5），只对正文做 diff3 保证不了整个聚合一致。`adopt` 落一条 `human_adopt` revision 并把审批退回 pending；`dismiss` 当前稿一字不动、候选进 `dismissed[]`（否则同一候选每跑一次机器就复活一次）。⚠ 「采纳后再编辑」不是第三个端点，是 adopt 之后照常 `POST /stage0/edit`。决定按内容寻址保留，崩溃后同一请求幂等重放；只有 `ok/landed_red` 才清理一次性 plan |
| `POST /api/novels/:sid/review/:o/edit` | `DraftEditRequest`（仅 `segments[{slot,text}]`，无 mode） | typed segments 落 `_service/edits/` 一次性 JSON 稿（文件名带 `ch{o}`，`--apply-edit` 从文件名认章）；slot 必须唯一，最终与计划 slot 集合完全一致且顺序由 producer 决定的校验归 `stage5.write --apply-edit`。随后走**与生成稿同一个判定落点**（归一 → 判据 → 归档 → 写回，`origin=human` 记账）；仍有阻断级**只报不拦**（人改的往往正是机器修不动的地方）。⚠ **没有乐观锁**：并发改稿以整份归档兜底，覆盖的代价是多花一轮归档——登记为已知限制，不在 API 层造第二套锁 |
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
| `missing` | 串 或 null | 是 | 缺的是**哪一个**（`'novel'` / `'stage3/style_grounding.json'`） |
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
