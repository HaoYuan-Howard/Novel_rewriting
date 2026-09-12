# Stage2 范围审阅、实际问题与免费收口

Global 与 Blueprint 两位 Commander 的职责保持独立。Global 按必要知识主体核查当前
blocking 面；披露项和隔离组保留原 PASS/FAIL/UNVER 状态。Blueprint 使用完整章节范围
和独立跨章复核，不生成逐节点、逐约束或逐 delivery 正向通过回执。

## Global 的审阅

Global 的 Stage2ReviewScope / obligation_receipt 只服务现役 Global 必要知识。
当前主体与问题的稳定身份、逐项有效继承、较新否定遮住较早接受及 removed_problem
保留规则继续生效。删除知识主体不能消除待处置问题；未知、缺失、合法空值和接受分开。

Global 的 typed patch、collection delta 和 post_edit_review 由自己的 producer 预检。
静态人物归 stage2.characters，不得通过 Global 写口越权修改。具体字段与恢复见
[stage2.md](./stage2.md)，GlobalReview 的判据面见 [evaluation.md](./evaluation.md)。

## Blueprint 的范围和材料

审阅契约为 stage2-plot-scope-review-v1，载体在 contracts/schema/plot_review.py。
范围是 chapter:N 或 cross_chapter；自动模式按阅读序选择下一个待审章，人工可选当前可审范围。
工作包携 scope、当前 opaque token、dependency_manifest、原文/剧情/必要依赖及实际问题，
代码验证 seal 与语义身份分别保存。11 章样本为 11+1 个范围，重试/返修仍逐次计费。

章审读取完整本章原文、蓝图、必要 Global/持续状态及知识依赖；跨章工作包再次提供原文
各章一次、紧凑蓝图一次、必要 Global、跨章依赖、问题及章节接受引用，不嵌套历史回执正文。
全部章审通过不能自动推导跨章通过，也不能跳过反查原文。

响应包含 decision、reason、coverage、new_issues 与 dispositions。固定五维为
completeness、attribution、causal_continuity、epistemic_reveal、carry_state。
每维 pass/issue/unver/not_applicable 明确区分；issue 关联实际问题，无法判定/不适用有理由。
accept 与未解决问题、未判维度冲突时拒收；空 issues 不生成接受。所有当前未解决问题必须
明确继续保留、解决或撤销。判断质量仍需语义验收，不把填全维度当作阅读正确的证明。

## 实际问题与身份

PlotIssueRecord 保存稳定 issue_id、first_scope、dimension、问题、主体、证据及追加事件。
主体改名、删除、拆合或回滚不删除问题。较新否定不被旧接受覆盖，历史必须是合法追加后继。
漏抽没有现成主体时可引用 chapter 与原文段落范围；它是证据定位，不是自动认定缺陷。

普通节点订正可 preserve ID，但内容及证据摘要仍变化；语义拆合分配新 ID，旧节点保存
tombstone 和 superseded_by。历史不进入每次模型工作包，也不能因为 ID 未变继承接受。

Stage4 的 blueprint_refinement 可先登记疑点并转本章 Commander 核实。转交绑定原 A/B
token、章与允许主体；A 不见原文，由代码定位证据，未核实不能自动改剧情或启动付费。
撤销误报或确认真实缺陷后，均对原问题 ID 追加明确处置。定向生成之后仍需复审。

## 依赖、提交与恢复

novel/views/plot_review.py::PlotReviewSnapshot 与
novel/stages/stage2/plot_review_dependencies.py 共享实际消费投影。manifest 记录
主体值、证据摘要、必要集合成员及投影契约，不只记 ID。内容或成员变化重开真实消费者，
无关章保留接受；全文件 SHA 与代码 seal 另守当次读取/写入竞态。

提交在同书锁内复验当前 token、scope、问题与依赖。原回答、typed 决定、审批持久记录分别
保存；同义 replay 只补尾，不新造接受或费用。代码/seal 变化先免费复验；语义、材料或审阅
契约变化不能免费重签成已接受。未知/损坏响应不作缓存未命中重买。

chapter:N 与 cross_chapter 预算持久存在，同受已授权书级总额约束；换 scope、分场、run、
session 或执行 epoch 不清空累计用量。格式错误和实际修订按真实调用计账，免费复检/补提交
不作新内容调用；reasoning 不重复计入成本。

## KnowledgeLinks 与收口

KnowledgeLinks v2 只引用已有主体，State modality 不描述传播权限。必要知识义务保留
typed 关联及有效历史闭合，不恢复全量事实审计清单。未决关联继续阻断。

Global 当前接受、Blueprint 全章有效接受、独立跨章接受及知识闭合齐全后，
stage2.blueprint --close-review 免费物化关联、补 seen/gate。服务只写 _service，
artifact 由 CLI producer 写入；中断先用原已保存决定恢复，不重抽或重购审阅。
汇合前后重读真实代码与输入，不跨请求缓存 Git 身份。

## 进度读面

进度仅用轻量验证快照，不构建 Commander 工作包。生成检查点、正式提交、有效章审、
当前章、跨章状态、实际未解决问题和最近活动分别显示；未知不显示为零。
刷新或服务重启后从持久产物、检查点和审批读取真实状态，不把生成完成误报为全书放行。
