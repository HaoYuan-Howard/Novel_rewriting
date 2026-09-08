# 工作流职责统一推进计划

本次覆盖全部 17 个现役节点。Stage2 优先闭合只是实施顺序，交付必须包含其余节点、
API／界面、工作单元账、恢复路径和指标。新契约为 `workflow-role-boundaries-v1`，只从新 clean base
由当前 producer 生成；旧书、旧预算账与旧审批保留只读，不迁移、不补字段、不借重启重新发放预算。

## 行为与边界

- Executor 生成候选并按机械错误或明确返工请求自修；Commander 核验事实与变化，提交 typed
  接受、局部订正、定向返工、不确定或停止。producer 统一校验、归一、归档和正式提交；状态机独占推进权。
- 五类审阅动作由 `NODES.lifecycle.review_actions` 登记实际子集，机械修复能力另在 `repair_actions`；所有现役 Commander 都有带当前工作包绑定的 uncertain/stop，明确终止并保留原因，不自动进入内容纠错。
- 正式落盘与审阅接受分开。下游必须同时满足当前有效产物、所需接受、授权和剩余预算；unknown 不转成重采。
- 同一工作单元 Executor／Commander 各自连续会话，各最多 4 次内容调用（包含首次、格式纠错和返工）。
  候选改变、换会话、换 run／epoch 或进程重启不重置预算；网络尝试独立有界、逐次留账，无进展提前停止。
- Stage2 顺序固定为 Global E → Global C 接受 → 逐章并发 Blueprint E → 独立 Blueprint C 全章交叉接受。
  两个 C 各有 4 次预算；取消与 Blueprint 重叠的预审和额外 Global 终审。已读记录不授予语义接受。
- Stage4A 与 B 分别持有持续 Commander；A 的 strategy 与各章 Executor、B 的歧义章 Executor 隔离。
  Stage6 A/B judge 独立且互不可见，合并仍为 UNVER，最终 pick 仍是 typed 人工决定。
  legacy Hook 同槽仅在 HARD 严格下降时连续修订，初始生成与全部槽位共用四次内容预算；
  Hook 正式产物升为 v3，旧 base 冻结原代码，新 clean base 从原材料与 producer 重建。
- 机械分章、Context、Review 和 assemble 不新增生成模型；只有已登记 blocker 才触发对应审阅。
  Blueprint Commander 可用 typed owner 自动回源 Global。Stage4 发现 Global 人物名册或事实错误时，
  提交 typed stop，在理由中列明 Global 错项、证据与人工修复坐标；操作员再经现有 Global producer
  编辑入口处理，状态机按依赖重验。Stage4 没有自动跨阶段回源权限，也不能自行补写上游事实。
  代码缺陷与不可确定问题停止，不把修复预算烧在无权修改的节点。
- 保存回答、typed 决定、prepared journal 与检查点先于执行成功记账；恢复先重验和补提交，只有未完成
  模型内容且预算仍在时才继续调用。当前会话不可恢复时以落盘工作包重建，不依赖隐式模型记忆。

## 分批实现与验收

1. 登记全部节点职责和持久工作单元契约；验证 E/C、章、相位、judge 隔离，内容预算预占与恢复不返还。
2. Stage2 闭合严格前置接受、两个独立 C、定向返工、候选／决定持久化和零调用恢复；Blueprint 工作包
   完整携全章原文、beats 与 facts，生产者自报正确不等于人工接受。
3. 全节点接入同一预算与生命周期；Stage4 返修持续审阅、Stage5 逐章重写、Stage6 双 judge、Stage7 定稿
   保留各自语义权限。Stage5 batch rewrite 的文档与现役 `targets[]` 同批对齐。
4. API 和运行页呈现后端工作单元、角色、已用／剩余预算、状态与恢复入口；成本预览和指标分别计两个
   Stage2 Commander，不把网络重试算成内容轮，不要求已经退役的预审重叠。

工程验收只使用零模型确定性测试：先契约五文件，再受影响单元／真实 API／恢复故障注入，最后默认非 corpus
后端回归与前端 test/build/seams/typecheck。断点覆盖预算预占、已保存回答、typed 决定、prepared 与正式提交。
真实付费、语义质量、速度与 current-schema corpus 均另列未验证，不以工程测试证明收益。

## 节点职责表

本区间只由 `tools/node_role_table.py::render` 从 `contracts/invariants.py::NODES` 生成。
`none` 表示该节点本身不新增模型审阅；现有下游批准仍按对应登记执行。工作单元账是实际余量的唯一来源。

<!-- node-lifecycle:start -->
| 节点 | 工作单元／输入 | 生成／审阅 | 审阅动作 | 修复属主／动作 | 会话 | E/C 内容上限 | 恢复 |
|---|---|---|---|---|---|---|---|
| `stage0.remove` | book：原始文本与当前 revision | deterministic / none：剔除结果由 stage0.approve 接受 | none | stage0.remove, stage0.edit；recompute | none | 0/0 | --check-only |
| `stage0.approve` | book：完整 retained 与 removed 工作包 | human_submission / commander：当前剔除 revision | accept, uncertain, stop | stage0.edit；accept, stop | commander:stage0 | 0/4 | typed_replay |
| `stage0.edit` | book：revision 与 typed 编辑计划 | deterministic / none：编辑后重新批准当前 revision | none | stage0.edit；typed_edit | none | 0/0 | --apply-edit, --apply-edit-plan, --apply-candidate-plan |
| `stage1.partition` | book：已批准的 clean_text | deterministic / none：机械覆盖与章号校验 | none | stage0.edit, stage1.partition；recompute, stop | none | 0/0 | --check-only |
| `stage2.global` | book：完整 MainText 与 Global 事实工作包 | executor / commander：生成所必需的全书事实 | accept, patch, rework, uncertain, stop | stage2.global；accept, typed_edit, targeted_rework, stop | executor:book / commander:global | 4/4 | --revalidate, --apply-edit, --review-decisions, saved_response, prepared_commit |
| `stage2.blueprint` | chapter：已接受 Global 投影与本章正文 | executor / commander：全章 Blueprint 与原文的交叉核验 | accept, patch, rework, uncertain, stop | stage2.global, stage2.blueprint；accept, typed_edit, targeted_rework, stop | executor:phase/purpose/chapter / commander:blueprint | 4/4 | --revalidate, --apply-edit, --close-review, --chapters, saved_response, prepared_commit |
| `stage4.design` | strategy_and_chapter：去序盲事实与独立章工作包 | executor / commander：A 设计及本轮明确变更 | accept, rework, uncertain, stop | stage2.blueprint, stage4.design；accept, typed_edit, targeted_rework, replan, stop | executor:strategy/chapter / commander:design | 4/4 | --apply-edit, --recover-repair-aggregate, saved_response, prepared_commit |
| `stage4.plan` | ambiguous_chapter：已接受 A 与本章关闭目录、机械基线 | executor_or_deterministic / commander：独立 B 结构及证据绑定 | accept, rework, uncertain, stop | stage2.blueprint, stage4.design, stage4.plan；accept, typed_edit, targeted_rework, stop | executor:ambiguous_chapter / commander:structure | 4/4 | --revalidate, --apply-binding, --apply-edit, saved_response, prepared_commit |
| `stage4.context` | book：已接受 B 计划与当前事实 | deterministic / none：机械投影闭合 | none | stage2.blueprint, stage4.design, stage4.plan；recompute, stop | none | 0/0 | canonical |
| `stage5.write` | chapter：当前 Context、计划与独立 writing 授权 | executor / commander：独立 writing 授权；产出交机械 Review 与整书 closure | accept, uncertain, stop | stage4.design, stage5.write；typed_edit, targeted_rework, stop | executor:chapter / commander:writing | 4/4 | --revalidate, --apply-edit, --recover-prepared, --recover-reviewer-rewrite-prepared |
| `stage5.review` | book：全章 canonical、计划与 trim 处置 | deterministic / commander：独立 closure 或 trim disposition 工作包 | accept, rework, uncertain, stop | stage5.write, stage5.review；accept, typed_edit, targeted_rework, stop | commander:closure/disposition | 0/4 | --check-only, --retain-pretrim-batch, typed_replay |
| `stage6.hook` | candidate_batch：原文文案、限定正文与全书事实地图 | executor / none：三候选经独立 judge 与定稿 | none | stage6.hook；typed_edit, targeted_rework, stop | executor:hook | 4/0 | --revalidate, --apply-edit, --recheck, --recover-prepared, saved_response, prepared_commit |
| `stage6.semantic` | judge：匿名候选与独立 A/B 证据工作包 | executor / none：只产带证据 UNVER，交 stage6.approve | none | stage6.semantic；self_correct, stop | executor:judge_A / executor:judge_B | 4/0 | --recover-prepared, saved_response, prepared_commit |
| `stage6.approve` | candidate_batch：三候选与两名 judge 的完整 workpack | human_submission / commander：当前候选的 typed pick | accept, uncertain, stop | stage6.hook；accept, stop | commander:hook_approval | 0/4 | typed_replay, --review-token |
| `stage7.rename` | book：全书人物、别名与已闭合正文 | executor / none：人名表交独立定稿 | none | stage7.rename；typed_edit, targeted_rework, stop | executor:rename | 4/0 | --revalidate, --apply-edit, --adopt-residual, --recover-prepared, saved_response, prepared_commit |
| `stage7.approve` | book：人物表与真实替换、残留扫描工作包 | human_submission / commander：当前人名与替换表 | accept, uncertain, stop | stage7.rename；accept, stop | commander:rename_approval | 0/4 | typed_replay, --review-token |
| `stage7.assemble` | book：已批准人名表、文案与 canonical 正文 | deterministic / none：机械替换与交付对账 | none | stage7.rename, stage7.assemble；recompute, stop | none | 0/0 | canonical |
<!-- node-lifecycle:end -->


## 零模型验收入口

下列是故障边界的验证入口；最终通过数由完整回归单独记录。

| 边界 | 入口 |
|---|---|
| Commander 原回答、成本、会话尾窗 | `tests/test_commander_work_unit_recovery.py` |
| schema 拒绝后的同预算恢复 | `tests/test_reviewer_schema_recovery_integration.py` |
| Stage4 B 逐章恢复 | `tests/test_stage4_legacy_chapter_budget.py` |
| Stage5 正式提交尾窗 | `tests/test_stage5_legacy_delivery_recovery.py` |
| Hook 总四次预算、连续修订与已完成组恢复 | `tests/test_stage6_hook_content_budget.py`、`tests/test_stage6_legacy_delivery_recovery.py` |
| Hook/Semantic/Rename 的真实免费 CLI、队列、Supervisor 与 HTTP 预览 | `tests/test_stage6_prepared_cli.py` |
| 双 judge 隔离 | `tests/test_stage6_semantic_agent.py` |
| Rename Agent 与 legacy prepared 恢复 | `tests/test_stage7_rename_agent.py`、`tests/test_stage7_legacy_delivery_recovery.py` |
| 审阅终态闭集与运行页审批/预算恢复 | `tests/test_reviewer_tasks.py`、`tests/test_api_run.py`、`web/src/screens/run/RunScreen.test.tsx` |

合成 UI 联调使用独立 `.test_runtime/ui-clean`、书号 99001 与本轮后端 8797。Global/Blueprint 经
当前 producer 的 FakeTransport 生成；页面保存两份 typed 接受，实际 `--close-review` 免费恢复补齐
seen/gate，重启后检查 API 中的接受历史、五个工作单元与预算未变化。未执行 Stage4 付费后继。
截图和 HTTP 前后盘面在 `.test_runtime/ui/`，不包含真实小说。最初访问原 8765 服务的截图不算新链验收。
供应商探针仅用确定性 transport 测并发、退避与逐 wire 留账，不调用外部模型；它使用独立实验账，
node/sid 均为 null，不占小说工作单元，也不借小说预算绕开探针自身总请求上限。


最终运行页证据：`current-review-and-budgets-final.png`、`current-review-budget-table-final.png` 与
`http-final-role-projection.json`。重启后两个 accepted 与五个工作单元 1/4、剩余 3 的事实未变；
API 仅新增 nullable `review_scope_sha256` 字段。`blueprint-review-delta-final.png` 展示只读范围可达，
该早期合成接受没有后加的差异基线，页面如实显示 initial，未补造接受范围；targeted 展示另由前端测试验证。
8797 验收服务用完即关闭，8765 原服务未操作。

## 四批收尾记录（2026-09-08）

| 批次 | 工程状态 | 已闭合内容 |
|---|---|---|
| 第一批 | 完成 | 17 节点职责登记、WorkUnitLedgerV1、角色内容预算、独立网络尝试、连续会话与持久恢复 |
| 第二批 | 完成 | Global 接受后才能生产 Blueprint；两位 Commander 分账；回源、最小失效与两份当前接受的免费收口 |
| 第三批 | 完成 | Stage0～Stage7 全节点接入；Stage4 A/B、逐章 Writer、双 judge 保持隔离；确定性节点不新增模型 |
| 第四批 | 完成 | opaque token、CLI/API/queue/Supervisor、运行页、预览与指标、权威契约及运行 skill 同步 |

本轮验收覆盖已确认计划中的预算不返还、五类崩溃尾窗、Stage2 顺序、回源与最小失效、角色隔离、
真实消费者和全节点登记。旧 base 的 CLI、直接 producer、HTTP、job 与 queue 写入口保持零写拒绝；
无回答、无接受与 UNVER 均不被解释为通过。人工编辑和已保存回答／决定的免费恢复仍可达。

| 验证 | 实际结果与本地记录 |
|---|---|
| 契约 §9.5 五文件 | 325 通过；`.test_cache/workflow-final-contract-v4.xml` |
| 恢复与真实入口的最终定向组 | 89 通过；`.test_cache/workflow-final-recovery-consumers.xml` |
| 默认非 corpus 完整回归 | 5923 项：5918 通过、3 失败、2 跳过，621.81 秒；`.test_cache/workflow-commit-non-corpus.xml` |
| 上述 3 个失败的修复复验 | 34 通过；`.test_cache/workflow-final-paid-preview.xml` |
| 前端 | 37 文件、217 测试通过；seams、typecheck、build 通过 |
| 真实页面与重启 | 两份 Stage2 接受、五个工作单元 1/4 与剩余 3 保持；证据见上节 |

完整回归的 3 个失败全部来自付费预览仍以「槽数 × 四次」计算 Hook 修订上限。已改为从
`NODES` 的整组四次预算扣除冷缓存首次请求，所有槽位共享剩余三次；原预算断言保留，
预览与指标共 34 项复验通过。两项跳过因本地没有对应 Stage2 语料，不计为已验；前端构建
保留既有大于 500 kB 的 chunk 提示，未在本轮追加拆包优化。

完整回归命令在 `novel_refactor` 环境、沙箱外执行，沿用默认四 worker 与 serial 登记：

```powershell
& 'D:/Miniconda/envs/novel_refactor/python.exe' -B -m pytest --basetemp=E:/_tr -o cache_dir=.test_cache --junitxml=.test_cache/workflow-commit-non-corpus.xml --tb=short
```

用户随后明确要求上述定向修复后直接 commit，不再等待重复完整回归。第二次重复回归已中止，
不作为完整通过证据；最终提交按上表的完整回归与修复复验联合记录，不宣称最终代码另获一次全绿。
本轮不包含真实模型请求、current-schema corpus 验收、降耗提速或事实质量实测；这些继续由
`docs/REQUIREMENTS.md::TODO-48` 跟踪。后续 Stage0～Stage7 的其他优化不并入本次收尾。
