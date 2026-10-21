# HTTP 响应（`/api/**`）

历史受托审阅（Stage2 语义审阅、Commander/Reviewer 工作包、人工逐 Stage 过闸）已随
全自动单模式退役；2026-10-04 用户裁决进一步删除存量审批账的只读解码面
（ApprovalDoc 决定族与旧链 schema 模块整批移除）——旧根一律 `legacy_incompatible`
拒识，历史→新契约的转换走仓外临时脚本。本文件只描述现役只读监控与运维端点。

**载体已定：Pydantic v2**——但本文只定**内容**，模型照它写，**方向别搞反**。
所以下面的表里不出现任何 Python 类型标注。

⇒ `docs/schema/_api.md` ↔ `contracts/schema/api.py`，文件名一一对应
（同 `_common.md` ↔ `common.py`、`_report.md` ↔ `report.py`、`_orchestration.md` ↔ `orchestration.py`）。

## 本文**不定**路由表

页面职责统一由 `web/AGENTS.md`「路由与屏幕职责」维护，实际路由在 `web/src/routes.tsx`。
本文件维护对应的 HTTP 响应形状和跨端点纪律，不复制路由表；调整页面时同步核对真实端点。

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

（历史）旧链 Stage4A 已发行外部回执及审批历史曾承担内容寻址身份，其
`constraint_operations` 兼容例外与 `constraint_catalog` 目录面已随人工闸退役；
存量决定不再解码（2026-10-04 裁决：历史账只读冻结，旧根一律拒识）。

**② `by_alias=True` 不许关。**
`_provenance` 是 alias 字段（模型里叫 `provenance`）。FastAPI 的
`response_model_by_alias` 一旦被关掉，响应里那个键会**静悄悄**变成 `provenance`，
而前端读 `_provenance` 拿到 `undefined`——不报错，只是那一格空了。
⇒ 配一条测试：凡是模型声明该 alias，响应 JSON 里必须有字面量 `_provenance` 这个键。

**③ 措辞不是契约，id 才是（`Q-6`）。**
凡是「为什么」类的格子一律出 **id**（异常类名 / `FRESHNESS_REASON` / 判据 id），
不出人读的句子。人读的句子可以**另外**出一格，但前端不许拿前缀去认它——
历史 `check_stage5_review.json` 的 `info` 桶曾靠 `〔度量〕` 前缀区分度量与告警，
一次措辞改动就会把 41 条度量全部渲染成告警色——前缀认结论的教训对现役
`CheckReport`（`:kind` 只剩 stage0/stage1）同样成立。

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
| `actions` | `kind` 取值同上；字段为 `kind`/`extra`/`label`/`paid`/`free_flag`/`writes`/`writes_disk`/`needs_value`/`required_inputs`，无 mode | 同上；`writes_disk` 是前端在 `web/AGENTS.md` 里点名要的那一格 |

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
| `GET /api/settings/network` | `NetworkSettingsView`（当前 output base 的付费外发网络偏好） | no-store；三态 unset/configured/unreadable；未配置按直连 |
| `POST /api/settings/network` | `NetworkSettingsView`（请求体 `NetworkSettingsUpdate`） | CSRF 保护；CAS 保存或清空；run 起跑时注入付费子进程的 `DEEPSEEK_PROXY`，直连显式给空串压过 `.env` 残留；不影响已启动的 run |
| `GET /api/novels` | `NovelList` | 新 |
| `GET /api/novels/:sid` | `NovelOverview` | **复用** `_orchestration.md` |
| `GET /api/novels/:sid/operator-next` | `OperatorNext`（同一 state/check/inbox 只读快照上的确定性下一动作） | **复用** `_orchestration.md`；headless 执行者的机读口 |
| `GET /api/novels/:sid/check/:kind` | `CheckReport` | **复用** `_report.md`；`:kind` 取值面只有 `stage0`/`stage1`（新链 Stage2/4/5/7 是节点级 `--check-only`，层名不在取值面 ⇒ 400） |
| `GET /api/novels/:sid/run` | `RunProgressView`（信标折叠 + 完成度，观察轨迹非判定） | **复用** `_orchestration.md` |
| `GET /api/novels/:sid/run/metrics?run_id=` | `contracts/schema/run_metrics.py::RunMetricsView`；省略 run_id 为本书跨运行累计 | 只读权威成本、工作单元、运行和会话账；不依赖追加额度资格，不写状态或发模型请求 |
| `GET /api/novels/:sid/work-units/:unit_id/grant` | `WorkUnitGrantPreview` | **复用** `_orchestration.md`；付费授权的当前工作单元授予预览 |
| `POST /api/novels/:sid/work-units/:unit_id/grant` | `WorkUnitGrantResult` | 同一授予的显式确认（CSRF 保护；付费与正文外发是两份独立授权） |
| `GET /api/novels/:sid/stage0/report` | `Stage0Report` | 从 `stage0/revisions.json` 派生的窄视图，见下 |
| `GET /api/novels/:sid/stage0/segments` | `Stage0Segments` | 新 |
| `GET /api/novels/:sid/stage1` | `Stage1View`（一次返回连续分区、原文章号诊断与 `anomaly_count`；全部由当前 clean 复扫） | 新 |
| `POST /api/novels/:sid/stage1/partition` | `JobRecord` | 新 |
| `POST /api/novels/:sid/trash` | `TrashNovelResult`（请求体 `TrashNovelRequest.expected_title`；整本项目移入 `_service/trash/`） | 新 |
| `GET /api/jobs?sid=` | `JobList` | 新 |
| `GET /api/jobs/:id` | `JobRecord` | **复用** `_orchestration.md` |
| `GET /api/jobs/:id/log?offset=N` | `LogChunk` | 新 |
| `GET /api/jobs/:id/stream?offset=N` | SSE：`LogStreamEvent` | 新；主日志通道 |
| `POST /api/jobs/preview` | `JobStart`（请求体 `PreviewRequest`） | **复用** `_orchestration.md`；direct executor 能力从运行登记表派生，并回显独立 `egress_authorized` 快照 |
| `POST /api/jobs` | `JobRecord`（202；请求体 `PreviewRequest`） | **复用** `_orchestration.md`；同一组 profile / egress 边界，profile 非空且 egress 不是 true 时不起 job |
| `GET /api/queues` | `QueueList` | 新 |
| `POST /api/queues/preview` | `QueuePlan`（请求体 `QueueCreateRequest`；纯预览） | **复用** `_orchestration.md` |
| `POST /api/queues` | `QueueRun`（202；确认并建立、启动持久队列） | **复用** `_orchestration.md` |
| `GET /api/queues/:id` | `QueueRun` | **复用** `_orchestration.md` |
| `POST /api/queues/:id/resume` | `QueueRun`（202；请求体 `QueueResumeRequest`，剩余付费命令要求 `confirmed=true`） | **复用** `_orchestration.md` |
| `POST /api/queues/:id/items/:index/cancel` | `QueueRun`（只取消尚未开始项） | **复用** `_orchestration.md` |
| `POST /api/upload?filename=` | `UploadResult`（请求体为原始字节；`authorized=true` 时须幂等键，可附 `run`/`autostart`，见 R-11.28） | 新；上传即授权开跑（全自动单模式） |
| `POST /api/maintenance/retention/preview` / `apply` | `RetentionPlanView` / `RetentionApplyResultView`；apply 原样回传完整 plan + `confirmed=true` | 新 |
| `POST /api/maintenance/trash/restore/preview` / `apply` | `TrashRestorePlanView` / `TrashRestoreResultView`；apply 原样回传完整 plan + `confirmed=true`，真实移动走 CLI child | 新 |
| `POST /api/agent/runs/preview` | `AgentRunPreview`（与真跑同一构造函数） | 新 |
| `POST /api/agent/runs` | `AgentRunRecord`（202；请求体 `AgentRunRequest`） | **复用** `_orchestration.md` |
| `POST /api/agent/runs/batch` | `AgentQueueView`（202） | 批量提交；每条仍走同一计划与身份 |
| `GET /api/agent/runs?sid=` | `AgentRunList` | 新 |
| `GET /api/agent/runs/queue` | `AgentQueueView` | 批量队列只读面 |
| `GET /api/agent/runs/:id/log?offset=N` | `LogChunk`（与作业日志同一形状） | 新 |
| `GET /api/agent/runs/:id/continuation` | `AgentRunContinuationView` | 续跑面：同身份接 current epoch 的只读投影 |
| `POST /api/agent/runs/:id/stop` | `AgentRunRecord`（202） | 只受理永久终止：后台先关 admission gate、terminate 全部 Executor 子进程树并 drain，闭合后才追加 ended；stop 后不可 resume，HTTP 返回不冒充 drain 已完成 |
| `POST /api/agent/runs/:id/pause` | `AgentRunRecord`（202） | 只受理暂停：立即停止接纳新节点，后台等待当前 claim/beacon 闭合后才追加 paused；不取消正常付费节点，HTTP 返回不冒充已 paused |
| `POST /api/agent/runs/:id/resume` | `AgentRunRecord`（202） | 受理后由后台同身份续 current epoch/provider session；代码或执行契约 漂移时同 parent run 开新 epoch并免费复检 |
| `GET /api/str06/candidates` | `contracts/schema/api.py::Str06CandidateList`，当前输出根下的工作流根清单与 run 状态 | 全部只读；旧契约根明示不兼容，不能当作新链结果 |
| `GET /api/str06/candidates/:candidate/run` | `Str06RunView`：contract_version、revision、状态、typed失败、节点、epoch与成本 | produced是I/O事实；费用只来自权威ledger且按本书sid过滤，真实调用/缓存分列，缺用量保持unknown；revision变化后刷新材料 |
| `GET /api/str06/candidates/:candidate/source-story` | `StoryArtifactView`，data为完整当前 `SourceStory` | 校验当前版本、发布seal与输入；缺失、损坏与合法空字段分开 |
| `GET /api/str06/candidates/:candidate/target-story` | `StoryArtifactView`，data为完整当前 `TargetStory` | 唯一目标故事读面，含目标名称、整书模型与章计划 |
| `GET /api/str06/candidates/:candidate/chapters/:ordinal` | `StoryArtifactView`，data为chapter_ordinal、packet、body、receipt | packet=ChapterPacket或null，body=string或null，receipt=ProseReceipt或null；不能从正文存在推导已接受 |
| `GET /api/str06/candidates/:candidate/hook` | `StoryArtifactView`，data为packet、body、receipt | packet=HookPacket或null；正文和回执各自可缺失 |
| `GET /api/str06/candidates/:candidate/delivery` | `Str06DeliveryView`：当前可交付状态、manifest摘要与披露 | 从当前章包/正文/Hook/回执重新证明；存在文件不单独代表ready |
| `GET /api/str06/candidates/:candidate/book?download=true` | UTF-8纯文本，已核验最终TXT原样流出 | 核对当前包、前章短尾、正文与Hook回执及manifest后才下载；没有成书404，失效不返回空200；文件名来自Content-Disposition的UTF-8 filename* |

### 当前故事 DTO 与刷新

`contracts/schema/api.py::StoryArtifactView` 的固定外壳为 candidate_id、revision、
status（ready/missing/invalid）、data（对象或null）、errors（字符串数组）。
根不存在由路由返回404；根存在而材料未生成为missing，旧版或坏结构为invalid；
ready仍不表示文学质量通过。字段由 `contracts/schema/source_story.py`、
`contracts/schema/target_story.py`、`contracts/schema/story_prose.py` 的当前唯一模型定义。

run携带 `contract_version='story-v3'` 与 revision。前端轮询run，revision改变后取消在途旧材料并
重新查询对应书的source/target/章/Hook/delivery；相同revision不重复刷新全部长文。
typed错误保留owner/node/item/path/context，不用中文报错散文推导修复路线。
正文回执只证明协议、篇幅与身份等机械范围，semantic恒为unver；不将通过字节校验包装成文学质量通过。
旧characters/blueprint/design/ir/naming端点及其DTO/query key已经退出，没有兼容转发。
HTTP与视图只读，不购买模型，不补写产物或回执。


> #### 审批工作包 / Stage7 终审包（已退役）
>
> `ApprovalWorkpack` v2、Stage5/7 紧凑终审包及配套 `review_token` 提交协议已随
> 全自动单模式与 STR-06 关闭批删除：写面、模板、余额投影与 HTTP 路由都不存在，
> schema 载体不再保留。当前交付判定走三层故事只读面，机械与语义未评分开；
> 不恢复旧人审、旧IR或姓名定位报告作为当前放行依据。

> #### `Stage0Report` 是窄视图
>
> 它不继承产物模型，只返回 Stage0 页面实际消费的六格：
>
> | 字段 | 说明 |
> |---|---|
> | `original_filename` / `total_code_points` | 审校页标题与规模读数 |
> | `warnings[]` | 运行时证据 + `novel/core/warnings.py::describe` 的措辞；前端不拼措辞 |
> | `executable` | `novel/core/eligibility.py::is_stage0_executable` 的投影；前端不从审批字段重算 |
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
| `profiles` | `PipelineAgentProfiles \| null` | `null` | 默认 executor 与逐节点覆盖的选择（v3 起无 commander 字段，旧行在读边界丢弃）；`null` 只是旧客户端/迁移期未指定，不是某个隐含默认 |

只有正文外发轴的 wire 缺省是 `true`，不代表两项授权合并。UI 必须显示两个独立复选框并显式提交
二者；`AgentRunPreview` 回显两个布尔值，preview 与真跑都由同一 planner 把两段授权原文写入 prompt。
其中正文外发声明由 capability registry 把冻结 profiles 展开成实际可达的逐节点
executor 具体 provider 路由；这只是同一 bool 授权的精确接收方边界，不增加第三条授权轴。
preview 可以在任一项为 false 时 0 成本展开；单本 start 与 batch 则在任何
probe/spawn/wire 之前同时要求二者为 true。`profiles` 键同样恒在：非空值经 registry 解析并进入真实
executor 运行；`null` 只保留请求兼容形状，当前 `default_profiles=null` 时 preview/start/batch 均关闭式拒绝，不能代选。

`GET /api/agent/capabilities` 是 executor backend 选择面的唯一公开数据源：

原配置续跑通过 `GET /api/agent/runs/:run_id/continuation` 读取历史完整快照和可用性；
preview/start 都携相同 `RunContinuationSource` 并由后端复验。运行页工作单元的
`GET/POST /api/novels/:sid/work-units/:unit_id/grant` 提供只读累计成本、历史授权与人工追加。
详细的 v2 ledger、停止证明、锁内 CAS 和幂等规则见 `docs/schema/work_units.md`。

| 模型 | 字段 | 约束 |
|---|---|---|
| `AgentCapabilitiesResponse` | `legacy_providers[]` · `executor_nodes[]` · `legacy_model_pins{node: model}` · `default_profiles` | `executor_nodes` 按 `LLM_EXECUTOR_NODES` 完整有序发布，前端不维护第二份节点表。`legacy_model_pins` 发布产品契约钉死的 legacy 模型（`NODES[node].model`，质量裁决）：钉死的是**模型**不是 agent/legacy 模式，被钉节点在 legacy 下经覆盖选该模型即可纯 legacy 整跑；键⊆executor 节点，值必须是已发布的 legacy 模型 id。legacy 模型的 `executor_nodes[]` 覆盖全部现役 executor 节点（2026-09-16 起 `AGENT_ONLY_EXECUTOR_NODES` 为空集，不再有「必须配 agent 覆盖」的节点），前端从「executor 节点减 legacy 承接面」派生，不另抄清单。`default_profiles` 是经整书验收后才可发布的产品预置配置，不是 backend 模式选择器；当前明确为 `null`，不能取任一数组首项猜 |
| `AgentDriverCapability` | `id` · `provider` · `label` · `availability` · `availability_reason` · `version` · `models[]` | `id` 是可执行 driver 身份，`provider` 只是供应商元数据，`label` 只展示。可用性四态为 `ready / missing / unknown / unauthenticated`；Commander 角色退役（2026-09-15）后 driver 只承接 executor，`roles` 字段随角色概念删除 |
| `AgentModelCapability` | `id` · `label` · `reasoning_efforts[]` · `execution_modes[]` | 精确 `id` 进入 argv/审计，label 可改；effort 与 execution mode 正交，只能提交该 model 实际发布的组合 |
| `AgentProfile` | `driver` · `model` · `reasoning_effort` · `execution_mode` | schema 只承载选择；driver/model/effort/mode 的组合由同一个 capability registry 关闭式校验，客户端不得自造笛卡尔积 |
| `LegacyProviderCapability` | `id` · `label` · `models[]` | 直接 LLM API adapter 的稳定身份；当前只发布 DeepSeek，模型能力里继续给出 thinking/effort 与精确 `executor_nodes[]`，前端不得按模型名猜节点适用面 |
| `LegacyLlmProfile` | `provider` · `model` · `reasoning_effort` · `thinking_mode` | 只保存改变模型行为的稳定选择，不保存 API key、base URL、代理、超时或缓存目录 |
| `ExecutorBackend` | `{kind:'legacy',profile:LegacyLlmProfile}` | C3 起唯一现役 backend；`kind=agent` 由 retired_formats 关闭式拒绝 |
| `PipelineAgentProfiles` | `schema_version=4` · `executor_default` · `executor_overrides{node: backend}` · `concurrency` | 仅 legacy。override 键∈`LLM_EXECUTOR_NODES`。加载 v1–v3 丢弃 commander 与 agent 条目 |
| `PipelineConcurrency` | `node_overrides{node: int}` · `per_book` · `per_provider` | `PipelineAgentProfiles.concurrency` 的可选精确配置；随常用配置、preview/start、batch 与原配置续跑保留。三项都是 `total_min`～`total_max` 的整数。覆盖键只接受可并发节点。缺失时省略序列化，不改写旧身份 |
| `PipelineConcurrencyCapability` | `nodes[]` · `node_choices[]` · `default_node` · `defaults` · `total_min` · `total_max` · `scope` | `AgentCapabilitiesResponse.concurrency` 发布的唯一选择面；当前总槽作用域为同一输出根。界面按 `total_min`～`total_max` 接受整数，不复制节点表。`node_choices` 不再是允许档位 |

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

设置文件固定为 `<base>/_service/agent_profile_settings.json`。v2 保存默认/逐节点 executor 的显式
`kind + profile`（`PipelineAgentProfiles` v3，无 commander 字段，旧文件里的 commander 键在读边界丢弃）；
API key、CLI 登录态、base URL、代理、付费授权、正文外发
授权与 prompt 均不进入该文件。旧 v1 裸 AgentProfile 只在读边界无损包成 `kind=agent`，读取不会擅自
重写，下一次成功保存才发 v2。设置只初始化未来运行表单：单次运行仍可
临时覆盖，start/batch 仍逐次要求两项独立授权并冻结自己的 profiles，已启动、已排队和历史 run 不读取
当前设置。`default_profiles` 继续保持产品验收状态，用户保存偏好不得使它从 null 变成非空。

直接 `POST /api/jobs/preview` / `POST /api/jobs` 共用 `plan_start`，把解析后的
`executor_backend` 同时冻结进 `Step.executor_backend`、`JobStart.executor_backend` 与 argv；子 CLI
再把同一选择交给 `RunContext`。旧请求里的 `executor_profile` 只在输入边界无损迁移为 Agent variant，
公开响应和新请求不再发旧字段。独立旧 CLI/客户端省略 backend 的 DeepSeek 路只作兼容，新 AgentRun、
设置页和新 preview/start 都不得靠字段缺失猜 Legacy。

Agent backend 的合法节点与 flags 唯一登记于 `novel/orchestrator/nodes.py::PROFILED_LLM_RUNS`，
API capability 从同一登记派生；本文件不复制能力矩阵。

各 producer 固定 typed 完整 replacement；
agent 只交候选，代码负责业务 HARD、结构化 feedback、append-only checkpoint、正式 journal、typed
readback、current/fresh 终验和 accepted-attempt adoption。`kind=agent` 的节点 dispatch 先于 Legacy
client 构造，不能静默回退直接 API。

Legacy backend 不借用 Agent 的 flag 矩阵，而由 `LegacyLlmDriver` 对 provider/model/thinking/effort/node
关闭式校验，并继续服从节点本身的合法 flags、schema、HARD 与提交契约。2026-09-16 起取消 Stage4
Pro 钉死：DeepSeek Flash 与 Pro 均承接全部现役 executor 节点（现役无 AGENT_ONLY 节点）。
两种 backend 任一失败都不自动切换。

direct `PreviewRequest.egress_authorized` 默认 false（不要与上方 outer `AgentRunRequest` 当前默认 true
混读）；resolved backend 非空时 builder 必须收到显式 bool，并将它同源冻结进 `JobStart` 和付费
`Step`，只有 true 才把 `--allow-egress` 加进 argv。false preview 可返回完整但不可执行的命令，真 start
与手敲 CLI 在 job、probe、driver、wire 前拒绝。backend 为空时 false 归一为 `null`，true 关闭式拒绝。

outer `AgentRunRequest.profiles` 已接入显式 executor backend：
preview/start/batch 共用唯一 plan，
把规范化 base、起始代码指纹、resolved profiles 与两项授权冻结进 started/queue；
排队出队不重读 capability 或默认。child executor 只持 run id 与 contract SHA-256，再从 append-only
started 事件复核整份配置；operator runner 按 exact node/flags 选 default executor 或 override，不能手拼
任一 backend 参数。受托 Commander 已退役（2026-09-15）：审批闸一律人工，自动跑书停在闸前等
`next_handler='human'` 过闸后续跑；历史 `role=commander,node=null` 的 session 与 ledger 行只读解码，
实际模型/effort 缺失保持 unknown，漂移、串
provider session 或账/审计不闭合只可把 delivery 降成 unknown。进程 state 与盘面 delivery outcome 正交。

页面现已从 capability 动态构造默认 executor 与逐节点 override 表单；新表单优先载入
`configured` 用户常用配置，其次才用非空产品默认，两者都没有时必须显式选全，不取数组第一项；设置
不可读或请求失败时关闭预览/启动。单本与批量 start 直接消费最后一次成功 preview 保存的同一请求快照；
profiles、两项授权或 capability 有任何漂移都先使 preview 失效，不能用新默认重解释旧确认。历史 run 的
`profiles=null` 只显示 unknown；direct Job 的 executor backend / egress 只读展示仍与 outer agent run
分开。Phase 1～6 的工程验证仍没有真实 provider 模型请求或 current-schema corpus 验收，不能据此宣称
模型质量、速度、账号认证或 entitlement 已验证。

通用启动返回 202 和 `running` 作业，由后台线程逐步启动 CLI 子进程。同一 `sid` 的书级
作业互斥并返回 409，不同小说可并行；Stage5 canonical 写作同样占书级键，并在启动前原子核对
current writing 授权、Review 状态与旧布局，不存在另一个按臂的并发口。

⚠ **一个响应模型都不许自己算判定**（`R-11.4`）。`badge` / `passed` / `counts`
只出现在 `CheckReport` 里，且由 `contracts/schema/report.py::badge_of` 折叠、
再由 `CheckReport` 的后置条件反过来断言——**服务端不重算，前端更不许算**。

---

### 运行统计的归属与分母

响应字段以 `contracts/schema/run_metrics.py` 为准，聚合唯一落点为
`novel/views/run_metrics.py::get` 与 `novel/work_unit_usage.py::summarize`。
费用只读取服务 ledger；unit → turn → attempt 精确核对 sid、producer、角色和 request，
parent/epoch 与 session 只用于关联，不从日志文字或时间接近补身份。

省略 run_id 返回本书跨运行累计；指定则按显式 parent_run_id（无父时用 run_id）筛选。
同一业务单元可以跨运行，累计内容轮与本次调用不能互换。旧无关联行单列且保留成本，
无法证明归属、重复 attempt、未提交尾或缺失用量均披露；不以未知作为零，也不隐藏失败分母。
重复身份的行不进入已知小计，完整合计为 null。只有 cached=false 是确定网络调用，
当前请求缓存重放单列，不重复计入既有实付；不发布退役历史采用计数。reasoning 只作 completion 子口径。

运行历时来自运行起止，包含暂停和等待；模型尝试耗时取 latency_ms 累计；模型活跃区间由
原账完成时刻和耗时还原后取并集，保留原时间精度，不把重叠相加成墙钟。缺时刻/耗时/尝试时
完整区间为 null，已知部分另给小计；机械恢复和人工等待不并入生成耗时。首 token 没有可信事件就为 null。
每次查询只投影当时已提交记录，刷新和重启重新读原账，不生成第二份持久统计总账。

## 四、四个小模型

| 模型 | 字段 | 说明 |
|---|---|---|
| `NovelList` | `base` · `novels[]`（`NovelOverview`） | 抓包 `novels.json` 的顶层就是 `{base, novels}`。⚠ **复用 `NovelOverview`，不另造一张轻量清单**——原型 `Web/novels.py` 那张第二清单的代价是 stage5 被迫整个排除在外，于是「有没有陈旧产物」那个徽章不覆盖成稿 |
| `TrashNovelResult` | `sid` · `title` · `trash_id` · `trash_rel` | `novel/orchestrator/trash.py` 的 CLI 子进程完成同盘移动后返回；`trash_rel` 是可恢复位置，不把“从语料库消失”偷换成“字节已永久销毁” |
| `RetentionPlanView` | `wire_version` · `generated_at` · `policy` · `examined` · `candidates[]` · `plan_token` | preview 完整冻结策略、扫描分母与每项路径/mtime/size/SHA；apply 不接受前端重拼的候选 |
| `TrashRestorePlanView` | `wire_version` · `generated_at` · `trash_id` · `sid` · `title` · `original_dirname` · metadata/manifest 三枚 SHA · `plan_token` | book/approval/service run 三面由 manifest 闭合；持锁后再次核 token/CAS，拒绝覆盖 live 位置 |
| `JobList` | `jobs[]`（`JobRecord`） | 抓包 `jobs.json` 就是 `{"jobs":[]}`——**一个样本都没有**，所以元素形状只能从 `_orchestration.md` 来 |
| `LogChunk` | `text` · `next_byte` | 按**字节**续读。⚠ 中文一字跨 3 字节，**只在读的一侧解码**（写的一侧按字节追加，见 `_orchestration.md`）。`next_byte` 原样回传给下一次 `?offset=` |
| `LogStreamEvent` | `text` · `next_byte` · `done` · `state` | SSE 的 `id` 与 `next_byte` 相同；普通 `log` 事件为 `done=false,state=null`，唯一的 `end` 事件为 `done=true,state=ok|landed_red|failed|unknown` 并随即释放连接。断线时浏览器用 `Last-Event-ID` 续读；显式 query offset 与它取较大者，避免刷新倒退 |

`PreviewRequest`：`sid`（已有书为非空字符串；首次 `stage0.remove --source` 为 `null`）·
`node` · `flags[]` · `executor_backend`（`ExecutorBackend \| null`；迁移期未指定为 `null`）·
`egress_authorized`（`bool`，默认 false；只授权 direct executor 外发正文）。
请求外壳是 strict，任何旧 `mode` 键都返回 422；老
`jobs.jsonl` 的兼容解码只在服务内部磁盘边界发生，不进入本请求或公开响应模型。
旧客户端提交 `executor_profile` 时只无损迁移成 `kind=agent`，新响应不再发旧字段。Agent variant 只允许
`novel/orchestrator/nodes.py::PROFILED_LLM_RUNS` 的登记组合；Legacy variant 按 provider/model/node 能力表关闭式
校验。自动追跑的 `--check-only` Step 不继承 backend。非空 backend 必须显式携带 bool egress：false
允许预览但真启动返回 `ApprovalRequired`，true 才进入 argv；无 backend 时 false 归一成响应快照
`null`，true 拒绝。
⚠ `flags` 里的**带值 flag 要带上值**（`--apply-edit <路径>`）——
`/api/enums` 的 `actions[].needs_value` 就是用来告诉前端「这个按钮要先要一个文件」的。

---


## 五g、Stage6 定稿面（已退役）

Stage6 三候选定稿面（Stage6View/Stage6PrepareView/Stage6ApprovalWorkpack/HookApproveRequest）已随 Stage6 整层退役（F8b，2026-09-25）：新链开篇 Hook 单输出，只读监控见 `/api/str06/candidates/{candidate}/hook`。

## 五h、命名替换只读投影（已退役）

旧链 Stage7 改名对照屏（`Stage7View`／`NameDecisionLine`／`HookRename`）与配套
写回请求体已随旧链人工定稿面删除；当前目标名称随
`GET /api/str06/candidates/:candidate/target-story` 的TargetStory展示，交付走 `/book` 下载。


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
  填**节点 id**（如 `str06.stage5.body`）。
- `chapter`：**输出章**口径，可空——书级 / 整份产物 = `null`，
  后端不给书级条目编一个假章号。
- `wontfix`（视图顶层）：被人豁免的条数——豁免不删结论、不进清单，单独计数。
- `attention`：`pending` / `reviewed_retained`。后者必须同时带 typed `decision`
  （actor/reason/subject SHA-256/state/decided_at）；不能凭报告散文或 agent 自述补推。
- `delivery_effect`：`blocking` / `disclosure`，与 attention 正交。顶层
  `attention_counts`、`delivery_counts` 都由 items 分组并由后置条件核一致；总数不再冒充失败数。

路由侧：对每个已移植层各跑一次 0 成本复检（stage2/4 会重算判据）——**纯读**，
大书上是秒级。**登记不优化**：盘上 3 本无压力，全语料嫌慢时在路由层加缓存，
别动生产者（`novel/orchestrator/inbox_projection.py::inbox_view`）。

## 五j、人工写回的请求体（已退役）

DraftEdit／TrimResolution／NameMapApprove／ResidualAdoption 请求族已随
F8b 全自动单模式整族删除（405/404 由 `tests/test_api_edits.py` 钉住）；
新链没有人工写回面。


## 六、`ErrorBody`

| 字段 | 类型 | 必填 | 说明 |
|---|---|---|---|
| `kind` | 串 | 是 | **异常类名**。取值面 = `novel/errors.py` 的类名集合，由测试闭合 |
| `message` | 串 | 是 | 人话 |
| `missing` | 串 或 null | 是 | 缺的是**哪一个**（例如 `'novel'` 或某个现役 artifact 路径） |
| `hint` | 串 或 null | 是 | 下一步该做什么 |

⚠ **`kind` 用类名不用措辞**：前端的四态要求区分「后端没起来 / 这本书没有 / 这一步还没跑」
（`web/AGENTS.md` U-17），靠句子前缀去认，一次措辞改动就静默断掉。

状态码表的唯一落点是 `novel/api/errors.py::STATUS`：**按类精确匹配再沿 MRO 上溯，没有默认格**
——`novel/errors.py` 开头写着「不设默认异常」，本表是它在 HTTP 侧的同一条纪律。
落不进表的异常说明那个失败还没想清楚，而一个默认的 500 会把它藏起来。

⚠ **`PathRefused` 是唯一一条两侧归属不同的**：CLI 侧它是退出码 3（代码自相矛盾），
HTTP 侧「书 id 不合法」的产地是**客户端**，那是 400。
解法不是给异常分子类，是**在边界上先验**——路由第一件事调 `paths.check_sid(sid)`
就地翻 400；验过之后再从深处冒出来的 `PathRefused`（例如「一个 id 匹配到两个目录」）
就真的是我们的 bug。**「边界验过之后再抛就是 bug」是一条说得清的线。**


### Blueprint 阅读、诊断与进度（旧链，已退役）

Stage2 语义审阅、`blueprint-reading-workpack` 阅读工作包、`blueprint/edit` 人工写回面
与 `Run.blueprint_progress` 字段已随旧 Stage2/4/5/7 链整层退役（STR-06 切换，F8c）：
`GET /api/novels/:sid/check/:kind` 的取值面只剩 `stage0`/`stage1`，新链的复检是节点级
`--check-only`；当前事实读面为 `GET /api/str06/candidates/:candidate/source-story`，
运行状态为 `.../run`。节点produced仍是I/O事实，ledger成本单列；不为退役面保留第二个HTTP落点。

### 写作上下文的即时读数（旧链，已退役）

Stage4 `ctx_chapter` / `CtxChapterView` / WriterContext v10 与 Stage5 的成稿读数随旧链
整层退役；当前目标建模由 `GET /api/str06/candidates/:candidate/target-story` 展示，
实际写手材料在 `.../chapters/:ordinal` 和 `.../hook`。材料缺失、坏结构与合法空分区按外壳分别表达；
展示字段不能作为新任务书提交。
