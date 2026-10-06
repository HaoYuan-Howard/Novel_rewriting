# 工作单元与人工追加

`WorkUnitLedgerV2` 位于 `novel/paths.py::work_unit_dir`。工作单元身份仍由工作流任务、书、
节点、角色和 scope 决定。新工作单元只接受 `role=executor`（`role='commander'` 构造期
关闭式拒绝；历史 commander 账经 `read_only=True` 只读解码，仅服务 owner 停止证明与
历史决定重放，不参与任何新预算）。waiting_on 到 owner/scope 的投影用
`contracts/invariants.py::approval_work_unit` / `::approval_scope_allowed`。基础内容额度
四次；新候选、新会话、新 run 和新 epoch 都沿用原账。网络重试单列为 attempt，缓存命中
单列。追加金额只表示允许增加的内容调用次数，不是货币费用。

**整书级付费内容调用总闸**（2026-09-15）：`BookContentBudgetV1` 持久于
`novel/paths.py::book_budget_path`（`_service/runs/<sid>/book_budget.json`），对同一本书
**全部**工作单元的内容调用合账设绝对上限（默认 `BOOK_CONTENT_CALL_CEILING_DEFAULT=512`）。
只有 `WorkUnitBudget.reserve` 计数；网络重试共享同一 reservation、免费恢复与 prepared 补尾
不计数。坏账关闭式拒绝新调用，不作零（Q-13）。重启、恢复、重建 job 不重置；追加只能经
`novel/work_units.py::grant_book_budget` 显式人工（actor/reason 必填），自动恢复路径不得
追加。反例见 `tests/test_book_budget.py`。

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
外发授权。每个 role/scope 的额度互不流转；直连 `LlmClient` / `WorkUnitBudget` 恢复同一
logical request 时接受累计额度和全部历史 attempt links，不把第五次当成新的首次调用。
（历史 Agent CLI `SessionAgentClient` 已退役，仅见于只读账与 fixtures。）

`contracts/schema/work_unit_recovery.py` 定义无回答传输失败的工程收口请求、预览与回执
（现役 executor 同一入口收口；原 commander profile 自 profiles v3 读边界起不可恢复，
改用 append-only started 原字节核对）。
仅最后一轮 `unknown/commander_transport_failed`、全部 attempt 明确连接或超时失败、成本/session/
原请求/日志及原 owner 排空证明一致时可用；已保存或孤立回答、待补提交和未结束尝试均拒绝。
预览 token 绑定完整来源；提交在书级 writer、审批/工作单元锁及 owner 停止 lease 内复验。
回执写入内容寻址 blob，现有 `processing.kind=mechanical` 绑定它，随后原子发布 failed 状态。
读取及新 reserve 必须再次验证原轮次与回执；损坏保持拒绝。原轮次、attempt、追加额度与成本不变，
缺失 token 仍未知；仅允许另开内容轮消耗原剩余额度，不恢复旧 provider 会话。工程操作入口见
`docs/OPERATIONS.md`。

`WorkUnitLedgerV2.processing` 按写入顺序保存不可变来源回执；冻结请求时登记待处理单元，
包括尚未启动的并发章节，避免只统计已发请求。模型项精确引用内容轮次，
机械处理、历史采用和缓存命中引用 producer 已核验的输入与结果摘要。免费处理登记同一
业务单元但不创建 turn、不消耗或重发基础额度；同一来源重放幂等。回执损坏保持 unknown。
`WorkUnitView` 统一发布 content_base_limit、content_granted、grant_count、content_used、
content_limit、content_remaining 和恢复入口；`WorkUnitView.processing` 展示该单元最近一次
实际处理的总量、待处理、新生成、机械处理、历史采用、缓存、失败、未知和原因。这描述处理
成果，不代表产物通过质量审阅；缺失或坏账只令该投影 unknown 并保留原因，不拦截独立的
暂停、停止或排空控制。

## 接受记录与发布语义（CS-H，RC-54～56）

工作单元账管内容轮与已购回答，**不管哪份回答被接受**：采用状态只从已封存的 CommitBundle 派生
（`novel/llm/paid_requests.py::sealed_turn_references`），`AcceptanceRecord` 只回答「这份回答是否通过
owner 的完整验收、可发布」。二者各自独立：

- `accepted=true`：解析、救回、引用解析、编译与机械闸全部通过（语义恒 `unver`）；
- `accepted=false`：被拒（问题随记录留证），或问题归上游 owner 因而被回派——验收回调抛出类型化信号
  `UpstreamOwned`，`ask_json` 记 `accepted=false` 与这些问题后上抛，由节点编排登记跨节点回派，不在节点内
  再问、也不再以空错误列表冒充通过；文本节点（正文、楔子）的机械闸错误同样进入记录；
- 「已被采用」不是记录字段：只看已封存 CommitBundle 的 adoptions 是否引用该内容轮／attempt。

每个接受版本以**一个 CommitBundle 原子发布一次**；跨节点修订产生新版本，旧版本的 bundle 保留可追溯；
交付清单引用的各输入版本必须彼此一致。指标 M1 数的是接受版本，不是“全程只许发布一次”。
单节点路径跑了交付组装时，`runtime/workflow_status.json` 的终态与 `accepted_body` 取交付报告，与整链
同源（`delivered`／`assembly_not_ready`），不再恒记 `node_run`。

## 候选集合与沿用凭证（CS-H，RC-50～56）

已购候选的权威是工作单元账与响应 blob；候选登记（`runtime/candidates/<owner>.json`，
`novel/stages/candidates.py`）是**可由账重建的索引**：每个内容轮取最后一份有回答的 attempt，
patch 回合在上一份候选上合并成整份 wire，登记该单元全部已购候选（含补丁链合并结果），每条记
wire 原字节摘要、生成它的请求身份、采用引用链、生成时所依据的上游版本摘要与最近一次预检摘要。
登记丢失或过期即从账重建——不重购、不补造谱系；`tools/replay_candidates.py` 与生产判定共用
同一把读取尺。

**免费沿用（carried_forward）**：上游修订使请求身份变化后，节点在购买前先对候选集合做免费判定
（兼容性→上游差异→完整预检→择优）。零调用采用时：

- 修订记录新增 kind `carried_forward`（`OwnerRevisionRecord` V2，新增必填
  `applies_to_request_sha256`＝当前请求身份；`baseline_*` 仍是候选生成时的原请求/响应），
  落 `runtime/revisions/carried/<owner>/<applies_to_request_sha256>.json`——**不进** `active/`
  （它不是待办恢复级联），也不覆盖 `runtime/revisions/<owner>.json`（那格是历史采用记录，
  owner patch 链核验要读它）；同一应用身份＋同一候选重放保留首次建档（I-30/I-31）；
- 成本链 `ledger-row` 指向**原 attempt**：provenance 按 attempt_id 从成本账精确聚合原付费行，
  本根只记一行零 token 的 cached 行（`cache_policy='carried-forward'`，新 attempt_id），
  不把历史 token 抄第二遍；
- 重启按当前请求身份免费重放（沿用事件按次记零 token 行，凭证不重写）；
- 只读复检（`--check-only`）不走沿用凭证：同身份免费重放本就命中已购回答，
  不该把每次免费复检记成一次采用；
- 人工 wire 采用与仓外换算采用仍是各自档位，不并入沿用；**沿用只是没有更高优先级采用时的
  快路径**——候选请求身份＝当前身份（那是缓存重放）、本身份下已登记 owner patch（普通重放
  必须采用它，沿用旧候选会反向覆盖人工修订）、显式重采或已登记的采样选择，沿用闭包
  （`novel/stages/chain.py::carry_forward_candidate`）一律放弃，节点落回普通接受循环。

# 按原配置继续

`GET /api/agent/runs/:run_id/continuation` 返回历史完整 profiles、来源 hash、可用性及原因。
此处 **Agent** 仅指整书代跑编排；profiles 现役唯一 wire 为 `kind:"legacy"`。
默认 Executor 和全部逐节点覆盖必须完整；缺失、冲突、模型已不支持或原执行
未排空均不得用当前 settings/default 补齐。只读能力查询不会产生模型请求。

`AgentRunRequest.continuation` 在 preview 和 start 中绑定同一个 `RunContinuationSource`。
启动在书级 writer 与原 owner 停止 lease 内复验；新的 `RunContractV2.continuation` 保存原来源。
旧 contract 没有该字段时不向旧 hash 加入 null。原配置续跑重新明确付费与正文外发，保留
原工作单元累计额度；正在暂停的同一运行继续沿用原有 pause/resume 协议。

单本 `AgentRunRequest.pause_before_node` 是可选的登记节点上界，preview 回显最终范围，
`AgentRunStart` 与 `RunContractV2` 共同持久化。编排器按登记节点顺序，在该节点及之后节点
的任何执行、免费恢复或终验启动前结束本次范围；先关闭 epoch、排空并释放 owner，结果为
`stopped`，原因明确为范围边界，不伪造 `pipeline_complete`。返修按实际 owner 位置判断；
未知节点归属关闭式停止。

continuation 返回并继承原上界：请求省略该字段保持原范围，显式新节点改变范围，显式
`null` 解除上界。范围变化不改变已冻结 profiles、历史来源或工作单元累计额度；旧 contract
没有上界时不向原内容地址补入 `null`。
