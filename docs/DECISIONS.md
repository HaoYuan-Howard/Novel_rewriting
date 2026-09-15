# 关键裁决与证据索引

本文解释当前设计的理由及证据限制，不定义现役行为、不记施工流水账。契约见 BACKEND_CONTRACT，
待办见 REQUIREMENTS。完整历史原文保存在 Git：本轮整理前快照为
`1fbb2fd5482b966b4ec36f6b379b777028cf2f03`，可用 `git show <提交>:<路径>` 或
`git log --all -- <路径>` 查看；已删除的旧实验文档同样可按历史路径查询，不恢复为当前操作说明。

| 裁决时间 | 保留的决定与缘由 | 证据边界 / 当前落点 |
|---|---|---|
| 2026-09-15 | **全自动流水线重构开工（用户裁决）**：目标为 stage0–stage7 仅保留机械校验、全自动单模式、前端产物只读；stage6 语义审阅取消，改为人工从 3 候选中选一篇且不阻塞 stage7。落地顺序：批次 0 前置契约（Q-13 未知分层——仅内容质量类不可验证项可进交付披露，完整性/新鲜度/检查执行/付费副作用状态不得披露绕过；交付硬约束 vs 可降级目标分级（`Rule.degradable`，现役仅 `paragraph_budget_missed`）；整书级付费内容调用总闸（`book_budget.json`，默认 512，重启不重置、仅显式人工追加）；确定性重建白名单（R-11.27））与本批 stage6 判官退役（`stage6.semantic` 移入 RETIRED_NODES；`stage6.approve` 改默认 `--auto` 固定槽位序自动定稿，人工改选 `--pick` 保留、`basis` 改 `default_policy/human_selection`；判据三条与 schema 家族删除；review identity 去掉 judge_rankings 分量） | `docs/BACKEND_CONTRACT.md` Q-10/Q-13/§七/R-7.25/R-9.6/R-9.8/R-9.18b/R-9.25/R-9.26/R-11.27；`contracts/rules.py`（disclosable_unver/degradable）；`novel/work_units.py::grant_book_budget`；`novel/stages/stage6/{hook,semantic}.py`；`docs/schema/stage6.md` |
| 2026-09-15 | 删除 Commander 受托审阅角色，审批一律人工：自动跑书（AgentRun）由确定性 RunSupervisor 推进，在 11 个人工审批闸（`stage0.approve`、`approval:stage4-design-review / -binding-resolution / -structure-replan / -structure-review`、`approval:stage5-writing / -closure`、`approval:stage6-prepare`、`stage6.approve`、`stage7.approve`）以 exit 2 `stopped`（reason「停在人工审批闸：{blocker}；在 Stage 页面完成裁决后用原配置续跑」）停下，`next_handler='human'`；用户在现有人工 Stage 页面（同一 typed schema/token/审批 façade）过闸后用原配置续跑。`PipelineAgentProfiles` v3 只剩 executor（agent CLI 与 legacy llm 双模式、节点间禁混、agent-only 节点单向豁免），v1/v2 的 commander 字段在读边界丢弃、原始 append-only 字节不动。代价：每本书自动跑约 8–11 个人工触点，用户以人工触点换取删除受托模型的裁决风险与维护面 | `docs/BACKEND_CONTRACT.md` R-11.14、R-11.19～R-11.21、R-11.25、R-11.26 与各审批条款（R-5.20/R-5.25/R-8.17/R-9.25 等）；`novel/orchestrator/agent_supervisor.py`（`_human_gate_turn`、`ExecutionTurnResult`）、`novel/orchestrator/operator.py`（`_frontier_view` 的 `human` 投影）。历史执行链保留为只读/恢复解码：`novel/orchestrator/reviewer_legacy.py`（reviewer_tasks/contract/seat/revalidation/schema/protocol 迁入地）、`reviewer_action_evidence.py`、`stages/stage5/reviewer_rewrite.py` 与 `recovery:stage5-reviewer-rewrite`、wire schema（`CommanderSessionPointer`/`ReviewerActionRecord`）、ledger/session_audit `role='commander'` 行、`AGENT_RUN_EXIT_ORIGINS` 的 `'commander'` 值、`CommanderAdmission*` 常量与 admission 账（executor 现役，文件名沿用历史命名） |
| 2026-09-14 | novel/ 顶层只留跨层脊柱（store/paths/prov/run/errors/config/appendlog/consumption/output_root/runtime_layout/snapshot_clone/source_identity/run_identity/observability/wontfix/model_slots/human_actions/work_units 族）；有主模块归位——agent 域三件进 agents/、Commander 契约进 commanders/（该包已于 2026-09-15 随 Commander 角色删除，环境变量型执行上下文改由 `novel/orchestrator/execution_context.py` 承接）、机械准入与节点分发进 orchestrator/（admission 改名 mechanical_admission 消同名）、stage2 专属 run_state 进 stages/stage2/；被 orchestrator 与 llm 两层共同消费的模块（work_units、model_slots）必须留顶层，搬入任一层都会造成反向 import | 各提交；顶层由 26 件收至 18 件，同名冲突（admission/work_units↔views/agent_capabilities↔routes）中 admission 与 agent_capabilities 已消，work_units↔views/work_units 属不同包合法共存 |
| 2026-09-14 | 架构治理批次：依赖方向由测试钉死——退出码词表上提 `novel/exit_codes.py`（子进程协议非 CLI 私有物，orchestrator/api 不再反向 import CLI）；inbox 八类生产者下沉 `novel/orchestrator/inbox_projection.py`（编排域投影，views 单向依赖 orchestrator）；孤儿 scope 依赖族删除（产物单口零 stage 触达，顶层禁、惰性走 grandfather）；前端取值域联合单源 `backend.generated.ts` 生成 + 对账；跨 stage 深路径 import 冻结 71 条只减不增 | `tests/test_layering.py` 四道新守卫、`scripts/export_backend_types.py`、`tools/README.md` 三栏、`docs/REQUIREMENTS.md` TODO-52/53；行为不变验证见各提交 |
| 2026-09-13 | Stage2 随审阅退役的周边机制同批收口：统一双 owner `core_edits` 批次（`novel/stages/stage2/core_edits.py` 及其队列/封印消费点）删除，独立 CharactersEdit/GlobalEdit（含独立预检）是唯一 typed 编辑入口；性能读数器与配对工具（`tools/workflow_metrics.py`、`tools/workflow_pairs.py`、`tools/stage2_acceptance.py`）删除——它们的读数主体是已退役的 Stage2 Commander 盘面，v7 重建读数工具前 TODO-49 性能配对保持未验 | `docs/OPERATIONS.md` 性能配对工具的退役边界、`docs/REQUIREMENTS.md` TODO-49；`tools/verify_agent_e2e.py` 改为按注册表与 ledger 现算产物版本/代码身份（报告 schema `novel-agent-e2e-verification-v2`） |
| 2026-09-13 | Stage2 语义审阅（Global 逐项审阅、Blueprint 章审与跨章复核、问题义务、审批继承、阅读凭证）与 KnowledgeLinks 整体退役：机械检查不证明人物完整、主张获原文支持或剧情抽取正确，这类语义维度以报告 unver 行常驻披露且不产生审批待办；`gate.s2` 退出审批契约，Stage4 起改用统一机械准入（`novel/orchestrator/mechanical_admission.py`） | `docs/BACKEND_CONTRACT.md` `R-2.15`～`R-2.17`、`R-2.22`、`docs/schema/stage2.md`；退役产物路径进 `contracts/artifacts.py::RETIRED_ARTIFACT_PATHS`，旧字节只配对冻结旧代码读取，不实现 v6→v7 迁移 |
| 2026-09-10 | 产品保留前端自动跑书与人工逐 Stage 两种模式；退役 skill 组织与 Markdown 终审交付，执行/终验归代码、运维说明供开发维护、运行统计在前端读取 | 运行入口在 `novel/orchestrator/`；Stage0 工作包曾落 stage0 包内 `review_workpack` 模块，该入口已随 2026-09-15 Commander 角色退役删除；E/C 本来不加载 skill，因此不把删除文档算作模型 token 收益。旧 canonical hash 面只读保留，整批验收状态见 REQUIREMENTS |
| 2026-09-10 | 测试按影响选、按稳定批次统一验收；checkpoint 提交和同书续跑不触发完整回归 | 根 AGENTS 的测试纪律；不会豁免 corpus、真实模型及发布门槛 |
| 2026-09-10 | 源钩子可以独立支持明确主张，正文不重复不构成错误；只服务钩子改写，不增加正文补写义务 | `docs/schema/evaluation.md`；矛盾、歧义和开篇范围不明仍 typed 裁决 |
| 2026-09-10 | 已购回答先持久化，纠错须见当前合法分支 schema 与同主体拒绝证据，不能反复直接 PASS 未解决项 | 决定解码符号曾落 orchestrator 的 `reviewer_tasks` 与 `reviewer_contract` 模块（均已随 2026-09-15 Commander 角色退役删除），历史链解码迁入 `novel/orchestrator/reviewer_legacy.py`；不能改旧回答或退还真实调用 |
| 2026-09-09～10 | Stage2 三 producer 独立产物；E/C 各工作单元基础四次，可按不可变人工授权有界追加。原“两 Commander、Global 接受先于 Blueprint”的审阅安排已于 2026-09-13 随语义审阅退役，生成顺序前置改由统一机械准入表达 | `docs/schema/work_units.md`、`novel/work_units.py`、`novel/orchestrator/mechanical_admission.py`；恢复不重置预算，真实验收见 REQUIREMENTS |
| 2026-09-09～10 | 一次整根切换，删除旧版兼容通道；旧代码配旧归档根，新生产用独立空根 | `novel/output_root.py`、`tools/archive_output_root.py`；切换条件及执行状态见 REQUIREMENTS |
| 2026-09-01～08 | 确定性 Supervisor 管调度，E/C 管生成/语义裁决；没有第三个模型 root | `novel/orchestrator/agent_supervisor.py`；工程交互代理只用于调试和恢复 |
| 2026-08-29～09-08 | semantic business、执行 epoch、会话和证据完整性分开；回答、决定、prepared、正式提交分别恢复 | `novel/orchestrator/`、`novel/agents/`；断点不能等同重新购买，也不能将 unknown 洗成可恢复 |
| 2026-09-07～08 | Stage5 Reviewer 可提交批量整章改写，每章独立 session/checkpoint，先正式化授权意图 | `novel/stages/stage5/reviewer_rewrite.py`；跨章联动超出关闭式指令仍需 stop，未选章不代签 |
| 2026-08-31 | host 发现 shim 与 canonical 操作包分离，整树资源进入运行指纹 | 历史上为避免宿主同步覆盖而拆分；现已退役，必要资源由 code/prompt/schema/input 身份覆盖，旧摘要仅解释原证据 |
| 2026-08-25～26 | 原子事实 → Stage4 盲设计/证据绑定 → canonical 闭卷整章写作；收走源序自由拼装与重复持久投影 | 各 stage 的当前 schema；这是架构裁决，不能以工程测试证明叙事质量提高 |
| 2026-08-26 | Stage3 持久产物层退役，Stage5/6 即时消费机械测量 | `novel/core/measure.py`、`novel/consumption.py`；不恢复定性文风包或 s3 门 |
| 2026-08-24 | Stage3→Stage6 定性包消融统计未决，随后按独立产品门槛删除 | 历史冻结批 20 wire/60 candidates，盲评 A:B 为 6:4，严重事实错误 8:8；后续产品认可不能改写成预登记统计阳性 |
| 2026-08-23～24 | Blueprint 降耗必须先过召回与事实质量门，旧 v3/v3.1/v2.1 实验曾因质量或落盘失败被否决 | 旧两书 v2.1 的 hinge 召回仅为配对 v2 的 69.1%/59.5%；历史架构并非当前入口，当前选择见 `novel/stages/stage2/contract.py` |
| 2026-08-23 | 钩子候选的单次耗时/输出存在明显方差，字面结构距离不作为审美门 | 历史同模型同 prompt 三次 completion 为 16,035/8,220/10,085；n=1 书/评审，不外推稳定收益 |
| 2026-08-22 | 空 content、reasoning 救回与结构化拒因分开留证，不把所有失败混为 parse | `novel/llm/transport.py`、`novel/llm/ledger.py`；11 wire 对照只支持有限结论，不复用旧模型默认 |
| 2026-08-17 | Stage5 语义节点下线，暴露边界交证据与人工复核 | `contracts/judges/stage5.py`；多一层模型审判不等于可靠真值，不恢复旧节点 |
| 2026-08-18 | evaluator profile 与自动收缩臂删除；未标定语义不自动放行 | Stage6 judge 只产带证据 UNVER，typed 人工决定收口；不能把双票一致当 ground truth |
| 2026-08-18 | 不保留通用产物迁移，shape break 升版并由 producer 重建 | `contracts/schema/__init__.py`；历史文件不补版本或缺字段冒充当前产地 |

## 仍可引用的工程基线

`4524673` 的默认非 corpus 回归为 6476 项：6474 通过、2 项缺 Stage2 审阅语料跳过，0 失败/错误；
相关定向 741 项、前端 230 项及 typecheck/seams/build 通过。`1fbb2fd` 只改测试纪律说明，
文档契约定向 331 项通过。这些结果不是 current-schema corpus、性能配对或整本模型质量证明。
历史更早的逐次测试记录只查 Git，不在此追加流水账。

## 维护边界

新增条目只记录会影响未来选择的产品/架构裁决：决定、原因、证据限制、当前落点各一句。
普通 bug、提交次数、逐轮 pytest 输出留 Git 与本地验证记录。被新裁决替代时合并摘要，
历史详情留原提交；不累积两套都像现役的说明，也不删除仍承重的质量反例。
