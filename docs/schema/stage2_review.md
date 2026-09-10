# Stage2 审阅义务、继承与免费收口

两位 Commander 的节点范围不变。Global 接受覆盖当前 blocking 审阅面；披露项和隔离组
继续保持原 PASS/FAIL/UNVER 状态。Blueprint 覆盖逐事实、逐章结构及逐章知识一致性，
知识关联仍使用独立的 typed 关联决定，不扩展抽取主体。

载体见 `contracts/schema/stage2_review.py`。Stage2ReviewScope 的 contract 为
`stage2-review-obligations-v1`，包含 node、完整 scope_sha256 和 obligations。每项有稳定
obligation_id、实际材料 subject_sha256、owner、chapter_ordinals、subject_path、kind 和
context_refs。移除问题后保留 removed_problem 义务，problem_subject_sha256 指向原问题身份，
当前身份绑定相关父集合与引用上下文；删除不能自动撤销否决。

Stage2NodeReviewRequest.review 可提供当前 scope_sha256、default_decision 与 exceptions。
默认决定只展开到本轮待审项；例外逐项填写 obligation_id、非接受 decision 和 reason。
未提供 review 时，原完整节点决定明确覆盖全部当前项；非接受决定会阻断历史接受。
不允许把未知、未填或删除误算成接受。scope、token 与服务端当前材料必须一致。

每次决定在 `_service/approvals` 保存完整 obligation_receipt：scope 和逐项 units。
每项保留原 actor/reason；继承项必须有 source_decision_sha256 并回溯到有效原决定。
新决定加有效继承必须完整覆盖当前义务。较新否决遮住更早接受；损坏或缺失来源保持 unknown。
旧决定缺少当前版本 receipt 时不授予继承，也不再视为当前节点接受。

GlobalReviewView 与 BlueprintReviewWorkpack 的 review_coverage 提供完整 scope、inherited、
pending_obligation_ids 和 prior_problems。历史推进不进入稳定 review token；提交仍在锁内
重新核验当前材料与完整覆盖。模型材料只包含待审义务及必要上下文，material_projection 明示
完整工作包及范围 SHA。裁剪后的知识关联材料命名为 pending，不冒充完整 KnowledgeLinkScope。

`novel/stages/stage2/review_obligations.py` 是请求裁剪、失效、提交和恢复的唯一义务投影。
普通事实、正式证据、登记证据、候选集合和人物引用均参加实际依赖身份；Blueprint 审阅
直接绑定正式事实/材料 SourceSpan，展示层省略坐标不影响失效。

`stage2.global --close-review` 在待审为空时重放有效原决定。Blueprint 沿已有
`stage2.blueprint --close-review` 先闭合继承，再免费物化知识关联并补 seen/gate。
两个入口不产生新语义决定或模型请求；队列只消费后端 closure_available 判定。
每项原审阅者和理由保留，聚合系统 actor 仅标明机械回放。相同当前决定重复收口不新增回执。

免费汇合的一次调用使用同一代码坐标，不复用写前盘面。每次审批写入前和汇合返回前
重新读取真实代码身份；漂移立即拒绝继续，已保存决定、关联和审批尾部沿原免费入口恢复。
这由 `novel/orchestrator/approvals.py::stage2_closure_projection` 承担，不缓存跨请求的 Git 身份。
