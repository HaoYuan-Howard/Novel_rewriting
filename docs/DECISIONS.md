# 关键裁决与证据索引

本文解释当前设计的理由及证据限制，不定义现役行为、不记施工流水账。契约见 BACKEND_CONTRACT，
待办见 REQUIREMENTS。完整历史原文保存在 Git：本轮整理前快照为
`1fbb2fd5482b966b4ec36f6b379b777028cf2f03`，可用 `git show <提交>:<路径>` 或
`git log --all -- <路径>` 查看；已删除的旧实验文档同样可按历史路径查询，不恢复为当前操作说明。

| 裁决时间 | 保留的决定与缘由 | 证据边界 / 当前落点 |
|---|---|---|
| 2026-09-10 | 产品保留前端自动跑书与人工逐 Stage 两种模式；退役 skill 组织与 Markdown 终审交付，执行/终验归代码、运维说明供开发维护、运行统计在前端读取 | 运行入口在 `novel/orchestrator/`，Stage0 材料在 `novel/stages/stage0/review_workpack.py`；E/C 本来不加载 skill，因此不把删除文档算作模型 token 收益。旧 canonical hash 面只读保留，整批验收状态见 REQUIREMENTS |
| 2026-09-10 | 测试按影响选、按稳定批次统一验收；checkpoint 提交和同书续跑不触发完整回归 | 根 AGENTS 的测试纪律；不会豁免 corpus、真实模型及发布门槛 |
| 2026-09-10 | 源钩子可以独立支持明确主张，正文不重复不构成错误；只服务钩子改写，不增加正文补写义务 | `docs/schema/stage2_review.md`；矛盾、歧义和开篇范围不明仍 typed 裁决 |
| 2026-09-10 | 已购回答先持久化，纠错须见当前合法分支 schema 与同主体拒绝证据，不能反复直接 PASS 未解决项 | `novel/orchestrator/reviewer_tasks.py`、`novel/orchestrator/reviewer_contract.py`；不能改旧回答或退还真实调用 |
| 2026-09-09～10 | Stage2 三 producer、两 Commander；Global 接受先于 Blueprint；E/C 各工作单元基础四次，可按不可变人工授权有界追加 | `docs/schema/work_units.md`、`novel/work_units.py`；恢复不重置预算，真实验收见 REQUIREMENTS |
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
