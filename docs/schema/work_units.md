# 工作单元与人工追加

当前运行根由 `WorkflowManifestV6` 标记 `stage6-prepare-v1` 与 `independent-hook-preparation-v1`。
`artifact_versions` 保存当前版本快照，产物读取仍以现役 schema 为准。普通入口只读当前 manifest，
独立空根初始化时 `transition_ref=null`；既有 manifest v1/v2/v3 和 ledger v1 不迁移、不补版本。
仅明确登记的 v4 → v5、v5 → v6 可以经离线 `workflow transition` 免费推进：prepared 固定源/目标原字节、
冻结代码、原 task_id/created_at、实际 Core 接受、owner 排空及全盘保留清单，completed 闭合后才放行
当前写口。目标 `transition_ref` 绑定 transition_id 与 source_manifest_sha256；缺 completed 不自动恢复。
人物投影可按冻结 Characters/MainText 免费重编译呈现绑定，其源/目标字节同属 CAS；人物、Global、
profiles、审批、已购回答和所有工作单元累计账保持原字节。不以旧 gate bool 继承批准，也不为升级重发额度。
v5 → v6 使用独立 prepared v2，原冻结代码必须另行证明 Stage5 当前闭合；保留全部 Stage4–5 产物、审批和历史，不重编译投影，仅拒绝 Stage6/7 已存在的产物、INVALID、checkpoint 或已购来源。旧 v4 → v5 回执与恢复保持原形，普通写口只接当前 v6。
操作及中断补尾见 `docs/OPERATIONS.md`。其他存量仍由与原审计匹配的冻结代码恢复，不能删账换取预算。

`WorkUnitLedgerV2` 位于 `novel/paths.py::work_unit_dir`。工作单元身份仍由工作流任务、书、
节点、角色和 scope 决定。Commander 角色已退役（2026-09-15）：新工作单元只接受
`role=executor`，`role='commander'` 在构造期关闭式拒绝；历史 commander 账经
`read_only=True` 只读解码，仅服务 owner 停止证明与历史决定重放（`validate_reviewer_record` /
`reconcile_commander_response` 保留为历史解码），不参与任何新预算。waiting_on 到 owner/scope 的
投影用 `contracts/invariants.py::approval_work_unit` / `::approval_scope_allowed`（由原
commander_* 更名）。基础内容额度四次；新候选、新会话、新 run 和新 epoch 都沿用原账。
网络重试单列为 attempt，缓存命中单列。追加金额只表示允许增加的内容调用次数，不是货币费用。

人工追加入口为运行页工作单元详情，API 为
`GET/POST /api/novels/:sid/work-units/:unit_id/grant`，唯一实现是
`novel/orchestrator/work_unit_grants.py`。GET 不创建锁、账或模型请求；成本只来自实际 ledger，
reasoning 不重复加到 completion，缺失用量显示 unknown。默认追加一次，单次一至四次，实际
选项由后端下发。POST 必须有 CSRF 头、预览 token、稳定 intent_id、追加次数和理由。

提交先取得同书 writer、审批和工作单元锁，再证明全部原 owner 已停止并在锁内复验主体、
代码、成本和 session/epoch 归属。预算未耗尽、未闭合尝试、已保存回答或 producer/group
待提交均不能追加。只在现有 ledger 的 grants 末尾记录不可变 `BudgetGrant`；原轮次与费用
完整保留。相同 intent_id 的原内容重试返回同一授权；改金额、理由或 token 必须拒绝。
授权自身不发模型、不改变 typed 决定、不解除无进展停点。后续模型调用仍经过独立付费与正文
外发授权。每个 role/scope 的额度互不流转，SessionAgentClient 恢复同一 logical request 时
接受累计额度和全部历史 attempt links，不把第五次当成新的首次调用。

已购 Blueprint、Stage6/7 审阅以实际主体、裁决契约和义务维护业务身份；临时 review token
仍用于提交竞态，代码版本仍记入 epoch 与审计。恢复先核原工作单元的请求、回答、成本、
session 和 owner 停止证明，再比对原包和当前包；只有登记的运输/validation 字段变化时，
运行代码才更新提交凭证并交当前 typed loader/producer 免费重验。原始字节和首次 typed
证明保持不可变，后继 action 逐字段保留决定、理由和编辑值；新记录先耐久并验证后才
退休旧记录，两个记录之间中断可继续补尾。真实材料、finding、义务、裁决说明或输出 schema
变化不属于这条恢复路径；格式或内容拒绝仍保存准确反馈，不把免费重验等同于验收通过。
Blueprint 曾未向 CLI 下发完整动作 schema；当时的 blueprint wire 模块（已随
Stage2 语义审阅退役删除）登记过已发布完整动作的免费重验入口。仅接受原 observation
协议摘要和未下发 schema 的冻结请求，或已登记完整 schema 的固定字节摘要，
不能借此兼容其他 schema。
新的 provider 会话得到完整关联分支及稀疏补丁形状；
旧回答仍用原 JSON 免费校验，既不重写业务值，也不把格式失败归因于版本变化。

`WorkUnitView` 统一发布 content_base_limit、content_granted、grant_count、content_used、
content_limit、content_remaining 和恢复入口，运行页与指标工具复用同一投影。

`contracts/schema/work_unit_recovery.py` 定义无回答传输失败的工程收口请求、预览与回执（Commander
角色已退役，仅服务存量书历史工作单元的 owner 停止证明与失败收口；原 commander profile 自 profiles
v3 读边界起不可恢复，改用 append-only started 原字节核对）。
仅最后一轮 `unknown/commander_transport_failed`、全部 attempt 明确连接或超时失败、成本/session/
原请求/日志及原 owner 排空证明一致时可用；已保存或孤立回答、待补提交和未结束尝试均拒绝。
预览 token 绑定完整来源；提交在书级 writer、审批/工作单元锁及 owner 停止 lease 内复验。
回执写入内容寻址 blob，现有 `processing.kind=mechanical` 绑定它，随后原子发布 failed 状态。
读取及新 reserve 必须再次验证原轮次与回执；损坏保持拒绝。原轮次、attempt、追加额度与成本不变，
缺失 token 仍未知；仅允许另开内容轮消耗原剩余额度，不恢复旧 provider 会话。工程操作入口见 OPERATIONS。

Stage2 语义审阅已退役；本段纠错恢复只描述存量书历史 Commander 工作单元的账面语义。历史单元有剩余
额度时，纠错请求从同一工作单元恢复最近两份同业务主体、同
waiting_on 的被拒原回答及拒因，供新 provider 会话自足定位错误。每份原回答、拒因 blob
及运输摘要必须相互匹配；损坏保持 unknown 并在调用前停止。遇到已采用回答或业务主体
变化即停止回溯，不把旧上下文带给新主体。它们只是明确隔离的不可信诊断数据，绑定和
可选动作仍以当前工作包为准；不会因此采用旧决定、改写原回答、返还或新增调用额度。

`WorkUnitLedgerV2.processing` 按写入顺序保存不可变来源回执；冻结请求时登记待处理单元，
包括尚未启动的并发章节，避免只统计已发请求。模型项精确引用内容轮次，
机械处理、历史采用和缓存命中引用 producer 已核验的输入与结果摘要。免费处理登记同一
业务单元但不创建 turn、不消耗或重发基础额度；同一来源重放幂等。回执损坏保持 unknown。
`WorkUnitView.processing` 展示该单元最近一次实际处理的总量、待处理、新生成、机械处理、
历史采用、缓存、失败、未知和原因；这描述处理成果，不代表产物通过质量审阅。

`WorkUnitView.review` 直接复用 Stage2 义务覆盖及 Stage4 当前审阅模板，发布当前义务总量、
待审和有效决定继承；二者之和必须等于总量。无模型账的免费继承仍可显示，标记
`registered=false`，只读查询不会创建预算账。缺失或坏账只令该投影 unknown，并保留原因，
不拦截独立的暂停、停止或排空控制；模型调用累计账与当前审阅义务不可互相替代。

# 按原配置继续

`GET /api/agent/runs/:run_id/continuation` 返回历史完整 profiles、来源 hash、可用性及原因。
默认 Executor 和全部逐节点覆盖必须完整；缺失、冲突、模型已不支持或原执行
未排空均不得用当前 settings/default 补齐。只读能力查询不会产生模型请求。

`AgentRunRequest.continuation` 在 preview 和 start 中绑定同一个 `RunContinuationSource`。
启动在书级 writer 与原 owner 停止 lease 内复验；新的 `RunContractV2.continuation` 保存原来源。
旧 contract 没有该字段时不向旧 hash 加入 null。原配置续跑重新明确付费与正文外发，保留
原工作单元累计额度；正在暂停的同一运行继续沿用原有 pause/resume 协议。

单本 `AgentRunRequest.pause_before_node` 是可选的登记节点上界，preview 回显最终范围，
`AgentRunStart` 与 `RunContractV2` 共同持久化。Supervisor 按登记工作流顺序，在该节点及
之后节点的任何 Executor、Reviewer、免费恢复或终验启动前结束本次范围；先关闭 epoch、
排空并释放 owner，结果为 `stopped`，原因明确为范围边界，不伪造 `pipeline_complete`。
返修按实际 owner 位置判断，联合首抽按两侧物化归属守界；未知节点归属关闭式停止。
blocked 动作中的 node 是等待中的消费者，审阅范围则采用工作单元登记的
waiting_on 经 `approval_work_unit` 投影的审批闸 owner（现役为人工）；
因此 Blueprint 等待 Global 时，仍允许上界前的
Global 审阅，接受后才在真正的 Blueprint 启动点停下。

continuation 返回并继承原上界：请求省略该字段保持原范围，显式新节点改变范围，显式
`null` 解除上界。范围变化不改变已冻结 profiles、历史来源或工作单元累计额度；旧 contract
没有上界时不向原内容地址补入 `null`。Gate A 验收传 `stage2.blueprint`，Global 接受后
不会启动 Blueprint 的 Executor；再次继续仍须显式沿用或扩大范围。
