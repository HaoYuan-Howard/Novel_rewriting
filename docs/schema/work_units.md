# 工作单元与人工追加

当前运行根由 `WorkflowManifestV3` 标记 `pipeline-cost-latency-v3` 与 `plot-blueprint-v1`。只接受独立空根初始化，
既有 manifest v1/v2 和 ledger v1 不迁移、不补版本。存量由与原审计匹配的冻结代码恢复；发布时
按 `docs/OPERATIONS.md` 整根归档和切换，不删除单本账本换取预算。

`WorkUnitLedgerV2` 位于 `novel/paths.py::work_unit_dir`。工作单元身份仍由工作流任务、书、
节点、角色和 scope 决定。基础内容额度四次；新候选、新会话、新 run 和新 epoch 都沿用原账。
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
Blueprint 曾未向 CLI 下发完整动作 schema；`novel/orchestrator/stage2_blueprint_wire.py`
登记已发布完整动作的免费重验入口。仅接受原 observation 协议摘要和未下发 schema 的
冻结请求，或已登记完整 schema 的固定字节摘要，不能借此兼容其他 schema。
新的 provider 会话得到完整关联分支及稀疏补丁形状；
旧回答仍用原 JSON 免费校验，既不重写业务值，也不把格式失败归因于版本变化。

`WorkUnitView` 统一发布 content_base_limit、content_granted、grant_count、content_used、
content_limit、content_remaining 和恢复入口，运行页与指标工具复用同一投影。

`contracts/schema/work_unit_recovery.py` 定义无回答 Commander 传输失败的工程收口请求、预览与回执。
仅最后一轮 `unknown/commander_transport_failed`、全部 attempt 明确连接或超时失败、成本/session/
原请求/日志及原 owner 排空证明一致时可用；已保存或孤立回答、待补提交和未结束尝试均拒绝。
预览 token 绑定完整来源；提交在书级 writer、审批/工作单元锁及 owner 停止 lease 内复验。
回执写入内容寻址 blob，现有 `processing.kind=mechanical` 绑定它，随后原子发布 failed 状态。
读取及新 reserve 必须再次验证原轮次与回执；损坏保持拒绝。原轮次、attempt、追加额度与成本不变，
缺失 token 仍未知；仅允许另开内容轮消耗原剩余额度，不恢复旧 provider 会话。工程操作入口见 OPERATIONS。

Stage2 Commander 有剩余额度时，纠错请求从同一工作单元恢复最近两份同业务主体、同
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
Commander、默认 Executor 和全部逐节点覆盖必须完整；缺失、冲突、模型已不支持或原执行
未排空均不得用当前 settings/default 补齐。只读能力查询不会产生模型请求。

`AgentRunRequest.continuation` 在 preview 和 start 中绑定同一个 `RunContinuationSource`。
启动在书级 writer 与原 owner 停止 lease 内复验；新的 `RunContractV2.continuation` 保存原来源。
旧 contract 没有该字段时不向旧 hash 加入 null。原配置续跑重新明确付费与正文外发，保留
原工作单元累计额度；正在暂停的同一运行继续沿用原有 pause/resume 协议。
