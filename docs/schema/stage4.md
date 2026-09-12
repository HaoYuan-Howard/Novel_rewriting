# stage4 · 完整剧情的盲设计、分场呈现与证据绑定

Stage4 规划呈现，不重新抽取剧情。Beat.content 只有一份，BeatPlacement 是节点到场景的唯一
可编辑映射；少数节点可用多个同章 delivery 共同呈现。普通分场不修改 Blueprint，不重开章审，
不新增固定模型工位。字段职责与拆分规则见 [plot_blueprint.md](./plot_blueprint.md)。

执行顺序是 stage4.design → 设计审阅 → stage4.plan → 结构审阅 → stage4.context。
A 与章级 B 使用独立授权和 session；Context 独立零调用生成。Stage5 仍一次写整章。

Commander 使用 `novel-stage4-reviewer-rulings-v3` 运输契约：每章完整引用目录映射为局部
短句柄，历史约束按冻结目录映射为 `hN`；代码从不可变 submission_options 恢复原引用与
摘要。完整 options SHA 与 exact request SHA 同时校验，未知或跨章引用拒绝。只共享完全
同构的 schema 定义，不缩减章节、判断、修复码或引用基数；正式审批仍经过原 typed loader。
原始请求、schema 和回答留在运行记录，旧运输回答不自动继承新 schema 的审批。

## 一、产物与版本

| 文件 | 当前载体 | 职责 |
|---|---|---|
| blind_candidate.json | BlindCandidate v5 | A 整书策略、完整章设计、opaque 主体映射与来源 |
| blind_chapter_edit.json | BlindChapterEdit v3 | 单章完整设计的免费 typed 编辑输入 |
| evidence_binding.json | EvidenceBinding v5 | 每项呈现的证据、舞台与实际 B 来源 |
| plan_chapters.json | PlanChapters v6 | 编译完成的正式场景、placements、端点和整数预算 |
| plan_edit.json | PlanEdit v6 | 正式计划的完整 typed 编辑输入 |
| writer_context.json | WriterContext v9 | 章级剧情目录、逐场呈现和表达许可 |

路径完整名与版本以 contracts/schema/__init__.py::ARTIFACT_SPECS 为准，workflow manifest
登记 plot-blueprint-v1 组合。旧数据配对旧代码保留，当前干净根重新生成；不手补字段或迁移审批。

## 二、Stage4A：blind_candidate.json v5

### 2.1 A 的输入边界

整书策略请求只见章功能及确定性概况；章级请求见冻结策略、当前章 PlannerTruthView 与短小
opaque 引用。PlannerTruthView 提供设计所需的完整节点、稀疏状态、约束、必要知识与显式端点，
可以看后台真相，但不看原文、源坐标、EvidenceAnchor、源顺序编号或源舞台。

local/opaque/formal 映射由 HMAC 编译器持有。密钥不下发，不能仅散列可枚举正式编号；跨章状态、
Global 和必要背景引用同样去序。公开目录与编译映射共同绑定请求身份，不能漏掉映射后复用回答。

### 2.2 顶层与章场字段

候选保存 alias_algorithm、truth_sha256、subject_aliases、participant_aliases、
reconstruction_strategy、output_chapters 和 _provenance。映射用于编译及 B 解引用，不回灌 A。

每章含 structure_decision、三个 goal、weight、chapter_rewrite_brief、scenes、
beat_placements 与 anchor_bindings。场景只安排 purpose、role、delivery、transition、
context 引用与 turn；完整节点正文仍在 Blueprint。代码注入章号、候选场身份和预算。

每节点有一份 placement，deliveries[] 每项给 scene_ref、presentation_instruction；
单次整体呈现可显式 null，多次呈现必须逐场说明承担的任务。同一节点同一场只许一次。
delivery 用节点与场景组合定位，不创建可供 State/KnowledgeLinks 引用的子主体或子 claim。
单场端点可派生，多场的知识触发、状态成立/结束、跨节点约束、开篇/结尾/turn 必须明确落场。
父节点全部主要呈现完成前，不得用 context_beat_refs 复用整段正文。

### 2.3 调度、检查点与原子提交

策略 session 是逐章扇出的串行前置；策略验证并 checkpoint 后，N 个逐章 session 才能启动。节点内
实际并发取 `min(4, NOVEL_CONCURRENCY, 待跑章数)`：配置可以下调，但硬上限始终为 4。策略和每章分别
绑定同一个 `run_id` 下自己的 purpose、provider session、请求身份、
accepted attempt、ledger 与验证后 checkpoint。进程中断后只恢复匹配当前代码/契约/输入/prompt/schema
且 ledger 闭合的已验 unit，只补缺失或失败单元；恢复 checkpoint 不记 cache hit，也不新增 wire。
正式 provenance 的 usage 不取“本次重启以后”的增量：它按当前 `run_id` 下 N+1 个稳定 purpose
从 ledger 重建全部成本，包含先前无 checkpoint 的失败私有 session；因此部分恢复、完整恢复与
aggregate prepared 尾窗恢复必须得到同一份 usage，不能把已付费历史显示成 0 或漏掉。

普通 checkpoint 恢复仍严格限于同 run。普通 A 运行另有逐单元历史采用：策略与各章按当前实际请求
分别核验来源，可以合用多个历史 run；同请求出现不同有效回答时保持未知，不按最近或命中数选取。
每个选择冻结到当前 `design/units/{strategy|chNNNN}/historical-adoption.json`；普通聚合 journal v2
在每个 adoption 行中保存 `source_run_id`，同时冻结原 producer parent、完整文件字节与补提交回执。
来源 run、完整 checkpoint 链、回答、session、成本账及
当前 compiler/validator 均需闭合；损坏不可解释为未命中。同一次普通运行的 usage 仍只认本 run
完整成本，历史已购费用由原 ledger 行与新采用引用关联，不再复制成新消耗。

`stage4.design --reaggregate` 可在正式候选缺失时免费汇合全部策略/章响应，也可按同 run journal
幂等完成自己的提交。它不解析默认模型配置、不启动 driver、不补买缺失单元；全部采用产物使用
`BlindCandidateCompiledProvenance(origin=compiled, architecture=book_strategy_adopted_v1)`，
`sources_sha256` 绑定完整聚合来源行，不伪造本次 model/usage。普通付费配置运行即使全部命中，
仍保留各来源实际模型和明确的采用账；下游 repair/human evolution 读取同一份 v2 source 身份。

普通 A 的 prepared 聚合由 `stage4.design --recover-prepared <run> --subject-sha256 <proof>`
免费补尾。原代码、原配置、策略与全部章来源均须通过当前投影与完整 compiler；证明原 owner 停止后，
新 committer 持锁复验写前基线，仅补写已封存字节与缺失采用审计。恢复回执在主产物写入前预认领，
最后进入原 journal；下游 lineage 继续验证该回执。损坏、第三方漂移与多份 prepared 保持未知。

typed targeted repair 仍使用独立桥：新
execution epoch 的 target run 在首个 wire 前，可采用唯一最大历史 run 中与 target 的 resolved profile、
完整请求、输入、schema、token ceiling 和恢复协议逐字段相等，且 source checkpoint/session/ledger、
当前动态 validator 与闭合业务状态全部通过的目标章响应。它不覆盖策略、首次整书或 strategy replan；
并列来源不猜。选择写入 target `design/historical-checkpoint-adoption.json`，正式 lineage 使用
`origin=historical_checkpoint` 同时绑定 source attempt 与 manifest SHA。source revision 始终只读，不复制
到 target、不续旧 provider session；usage 按 source/target run 分段从 ledger 精确合并。

repair/replan 的整书 aggregate 另有一条不发 wire 的提交尾窗。source producer 在改动
`stage4/blind_candidate.json` 前先写 `design/design-repair-aggregate.json` v3
prepared；journal 冻结 producer parent、code、repair/replan scope、parent/decision/targets、
完整 unit sources、candidate baseline/target 与 finalization，并同时保存完整原始字节 SHA
与 prepared self/commit SHA。原 producer 已 ended 后，operator 只在全书物理历史根下恢复
唯一、无其他 Stage4/Stage2 prepared 冲突的这一份；service/legacy 同相对路径双副本、
多份 prepared 或任一物理副本漂移都不猜 winner。唯一入口为：

```text
stage4.design --recover-repair-aggregate <source_run_id> \
  --source-journal-sha256 <raw_sha256> \
  --source-commit-sha256 <prepared_self_sha256>
```

该路是 FREE/0-wire deterministic producer：不解析已 stale review token，不构造 executor
profile/client/provider session/checkpoint，不续旧 provider。Agent source 必须能由 strict-LF
`agent_runs.jsonl` 证明唯一 epoch 已 `drained` 关闭、parent ended 且 owner-stopped lease
持续覆盖本次 commit；direct CLI 只在 journal 明记 producer parent 就是 source run 时成立。
恢复先逐格复验 source checkpoint/session/attempt/ledger/code/request，再用当前 compiler
从已验响应重建候选，要求整份 typed candidate（含 usage、model、prompts、inputs、root/truth、
unit lineage 与 adoption provenance）与 journal sealed payload 完全相等，并重跑当前
truth/input/schema/HARD。live candidate 只能是 journal baseline 或 target；baseline 才写目标，
target 只验证，第三态拒绝。首个候选写之前必须对 source adoption、source-stable
recovery 关系和当前 target run/parent attempt 做全局 0-or-exact preclaim；事件尾非 LF、
重复、多余字段或归因漂移都要在写候选前拒绝。候选验收后幂等补 source adoption
和 recovery-run→source-run audit，最后才原位将 source journal 提为 committed。重杀于
candidate/adoption/audit/mark 各点由下一个新 recovery run 继续；committed 之后的下游
仍必须复核 journal 记录的 canonical/target-attempt/adoption audit，删账不能被当成已提交。

unit checkpoint 不是正式 artifact。只有策略和全部章节齐全，代码才能把 local alias 映回正式 alias、
注入章号/权重/`candidate_scene_id`、按章排序并组装完整 `BlindCandidate`；聚合物必须再过 strict schema、
单章动态闸与整书全部 HARD。通过后才由 commit journal 原子写 `blind_candidate.json`；中断、缺章、
聚合失败或 HARD 命中都不得留下部分正式文件，也不得覆盖旧正式候选。

### 2.4 正式 A 设计审阅与收敛修订

DESIGN_REVIEW_CONTRACT=stage4-design-review-v6。设计审阅同时阅读父节点及全部 delivery，
检查联合完整性、重复呈现、施受关系、truth/belief/claim 性质、因果与知识边界；不生成逐节点
或逐 delivery 正向回执，不因指标、证据重叠或合法倒叙自动判失败。

当前工作包的 opaque token 绑定当次可见材料。章节决定只复用实际依赖、审阅契约均未变的历史接受；
同主体较新否定遮住较早接受。request_repair 只改当前被拒章，request_replan 才涉及策略，
关闭式修复约束保留历史处置。普通换场、分段表现和已有端点绑定均留在 Stage4。

完整章免费编辑先 preview，再由同一 compiler 应用当前 token 的完整设计；不能通过字段 patch
偷偷创造子事实。更新 placement、指令、顺序或端点使相应 Stage4 接受失效，不回写上游章审。
设计接受只授权 B，不打开最终 s4，也不顺带生成 Context。

## 三、Stage4B：evidence_binding.json v5

STAGE4_BINDING_INPUT_CONTRACT=stage4-chapter-binding-input-v4。B 保持一次逻辑章级请求，
实际输入为完整本章已接受设计、必要节点/人物/其他主体映射、状态/约束及舞台和证据闭包。
原文只按证据需要回取，其他章整段正文不默认进入。模型不得改变 A 的顺序、placements、指令、
目标、端点或权重；回答仅解决当前机械基线留下的歧义并可登记 concerns。

novel/stages/stage4/plot_binding.py::build_mechanical_baseline 是唯一确定性基线。每项 delivery
按父节点证据和 source_contexts 构造可选舞台；签名等价的选择规范化，真歧义保留关闭式候选。
多个 delivery 可以共用同段证据，不强迫无重叠，也不按坐标覆盖宣布语义完整。未知时层/地点/
在场者保持 null，不能由参与人物补成在场。B 的证据身份与 A 的呈现身份分开。

机械章零 wire，无需假 checkpoint；歧义章各占私有 session、稳定 purpose、ledger 和验证后
checkpoint。默认并发受冻结节点/全书/供应商限制。首个失败停止后续接收，排空已在途章并保存
合格回答；只有全章绑定齐全、引用闭合和全书 HARD 通过才聚合提交。不同候选回答或损坏来源
不能按最新一条选取，已购候选优先免费恢复。

模型声称不可绑定时，须由同一证据目录上的确定性 witness 核证，不能让 B 自行重规划。
binding concerns 是结构审阅材料，不授予新剧情或付费动作。人工 --apply-binding 使用同一当前
typed schema 和编译器，来源记 human，不伪造 model/usage。

## 四、正式计划 v6

### 4.1 编译与顶层

同一编译器将 accepted A 与 verified B 还原为完整正式章计划，章号与源章一一对应。
Plan 不复制 beat.content，场景到节点的列表从 placements 派生。结构、证据和联合呈现仍需
独立结构审阅；通过后 Context 才可编译。

### 4.2 PlanChapter 与 PlanScene

章保存 output_ordinal、from_source_ordinals、structure_decision、三个 goal、
chapter_rewrite_brief、scenes、beat_placements 与 anchor_bindings；来源身份放在 provenance。
场景保存 slot、purpose、role、delivery、transition、budget_chars、context_beat_refs、turn_beat_ref。
具体类型为 contracts/schema/stage4.py 与 contracts/schema/plot.py，不维护 fact 分配或第二份映射。

### 4.3 结构向量与审批主体

STRUCTURE_REVIEW_CONTRACT=stage4-structure-review-v4。结构包包含父节点、逐场呈现安排、
必要证据、状态、约束、知识及当前 B concerns。每章独立审阅联合语义，当前全部章明确 accept
才打开 s4。证据位置、重叠、成员闭合只证明结构，不证明剧情完整或隐含知识未泄漏。

较新 request_replan 不允许旧接受覆盖。现有 A resolution 路径由 Commander 交叉核验具体损害后
提交新的 typed A repair/replan；只读指标不足以触发付费。当前同主体仅允许 exact replay 或
provenance 变化后的同语义 proof rebound，不能用旧 token 改票。完整规则见 BACKEND_CONTRACT R-5.20。

### 4.4 PlanProvenance

compiled Plan 精确绑定当前 A/B 版本化文件完整 SHA 和实际消费依赖，不复制 B 的模型费用。
人工替换记 human。业务消费投影变化与仅文件/证明变化分开，二者都不能绕开精确 A/B 链；
未知来源不作 fresh。历史采用费用保留原 ledger 及来源关联，不重复计入新调用。

## 五、writer_context.json v9

PlannerTruthView 用于去源序设计；WriterVisibleView 由当前章计划及 KnowledgeLinks 编译。
章级 beats 目录保存一次父节点正文，各 scenes[].deliveries 只引用节点和呈现指令/舞台，
并带必要 state_events、knowledge_event_refs、constraint_refs 与 context_beat_refs。

知识事件及已登记状态按显式端点编译逐场许可。尚未完成的分场节点不能当完整背景下发。
State modality 只说明命题性质，人物信念/声称不能升级为世界真相。Shared Global 正文只在有
当前事实许可或明确 belief/claim/unresolved 状态许可时进入材料。

Stage5 一次生成整章，模型同次会见后场材料。allowed_slots 是输出约束，不是物理隔离；
编译器不通过字符串裁剪隐藏秘密，也不增 visible_content。自由文本遗漏、重复、错误归因与
提前揭示由既有审阅及成稿检查承担，不宣称机械检查能证明语义安全。

Context 只在当前最终结构接受后独立零成本生成。它的目录、引用、材料和许可与同源编译结果
逐项对证；结构通过不能替代独立 writing 授权。

### 5.1 身份与披露消费

内部 `ActorRef {char_id,presentation_ref?}` 在 Beat 参与人、State 持有者、知识受众、
叙述/视点、观察者、舞台在场者、人物目标与关系两端的每个实际使用位置闭合。一个实体有多个
表面身份时必须指明呈现；同一 Beat 可以同时携两个不同呈现。普通已公开昵称属于同一个
ActorBinding，不因称呼不同创造两个身份。BlindReferenceMaps 的 participant_aliases 映到
完整 ActorRef，不能按 char_id 去重；PlanGoal 同样保留完整呈现引用。

WriterContext 只下发独立的 opaque `actor_ref` 与公开称谓，所有人物位置使用同一目录；
不下发 char_id、presentation_ref、反向映射或共同实体分组。不同呈现默认使用不同 token，
关系命题及知识事件的公开标识也绑定完整呈现，不能通过共同 rel_ID 或义务 ID 泄底。显式表面身份只可引用有据
公开 State 的角色描述；role 只在 role_allowed_slots 许可场景表达，不继承真实身份的 role。
角色描述还受该 State 的成立、解除和替代端点约束：解除场包含在有效窗口内，之后不再作为
当前角色描述。State 本身仍按披露许可保留为历史命题，不能因 role 失效删除历史事实。
缺失/歧义保留候选并交 Blueprint/Plan owner 免费订正，不择首项或回退 canonical。

跨章普通称谓只有在两个绑定各自至少一个登记称谓的 identity_revealed_from 严格早于
当前章、且登记归属相同时，才归一为同一公开 token；不要求重复的字面称谓，也不为普通昵称
制造身份事件。归一保留双方有据称谓。每个称谓另有 surface_allowed_slots：此前原文已出现的
称谓沿当前人物使用范围；本章首次出现的称谓从支持它的实际 Beat 呈现场开始，不能借同一
actor_ref 的早场使用提前开放。角色描述仍服从各自公开 State，不同角色描述不能择首项。

KnowledgeLinks 的 identity_actors 编译为现有表面身份 token 列表；只有对应事件开放后才允许
陈述同人关系。标量 identity_revealed_from 不替代同章场景端点；称谓字面可出现不代表身份
映射已知。跨章背景和上章收束沿原公开 token 携带必要名册，不附内部映射。
继承 State、背景 Beat 和历史知识受众按引用所属章解析缺省呈现，再冻结其公开身份；
后章增加另一种表面身份不会令原来唯一的旧引用歧义，也不会把旧身份吸收到新呈现。
当前章的缺省引用则使用截至该章原文已出现的登记称谓候选：即便只有一个显式绑定，
若候选中仍有未包含的称谓，且称谓间身份映射本章或未来才揭示，缺省引用仍须拒绝；
只登记旧身份或只登记新身份都不能排除另一种已经出现的候选。
Blueprint、盲规划、绑定与 Writer 共用该判据；真正唯一的此前公开绑定仍可省略 presentation。
原文只供私有候选解析，不因这项校验进入盲模型请求；未出现的未来别名不使早章误报歧义。
当前绑定候选仅含本章声明和上一章 carry_out 实际提供的 State／绑定闭包；不把其余历史
绑定扩成当前歧义，也不允许显式引用未提供的旧绑定。携入 State 的原章 null 若有唯一绑定，
携入材料保留原引用并带出实际绑定及证据，后续消费者仍按原章冻结解析。

关系/规则的 effective_from、effective_until（包含该章）只描述世界成立窗口；revealed_from
及 KnowledgeLinks 端点控制读者许可，不要求两种时间先后。已经结束的事实可以作为历史披露，
提前透露未来事实也合法。规则引用精确到 rule_id/version_id；secret.fact_ref 展开关系时必须有
唯一明确的实际关系呈现，缺失或多义不猜端点。完整自由文本仍由语义审阅核查。

关系披露同时绑定已接受 KnowledgeLinks 决定支持的公开端点：优先取明确支持 State 的
global_ref；仅有 Beat 支持时，必须在其来源章实际关系 use 中唯一定位。默认内部实体端点
不能充当披露证明。当前 use 与已公开端点不同，只能通过现有 identity_actors 端点连接，
两端映射许可与关系披露许可取交集；secret.fact_ref、知识事件与目标使用同一守卫。
没有映射则不下发该呈现的关系真相；支持缺失或多义交 Blueprint/KnowledgeLinks 免费订正。

`Blueprint.relation_uses` 只声明本章实际关系使用的两端呈现，不重复关系正文或强造 State。
知识义务中的默认实体端点不是呈现许可：仅记录事实成立、没有本章实际 use 的事件可保留内部
时间锚；同一关系存在多个明确 use 时全部分别编译，不能择首项或按实体 ID 合并。

Stage5 最终请求只消费此公开任务书。全量秘密、人物检测基线和原文保留为私有验证输入，
不进入 Writer 提示。证据/审计变化可以重开受影响审阅，但不改变实际材料时不制造无关付费重抽。
新版 Context 经严格载体和同源编译检查；旧 prepared/回答的 schema、输入或代码身份不符时
只保留来源，不能靠默认字段或旧审批静默恢复成新版本。

## 六、结构度量与人工门

结构读数按完整节点及实际 placements 计算；多场呈现不按原子分区算法偷偷展开成子事实。
披露顺序使用明确端点，严格可比较的源证据才进分母；未知、零分母和不可比保留 unver。
source_contexts 数量/证据重叠/场景数量不能作为语义完整或重构优劣的自动门。
读数随当前代码计算，不写回计划、不清空原接受历史。

## 七、失败与恢复

| 状态 | 允许的恢复 | 禁止的捷径 | 证据 |
|---|---|---|---|
| A 的策略或单章响应暂停/拒收 | 同一 `run_id` 恢复，只补失败/缺失 unit；已保存回答可免费重判并聚合，旧整书拒稿采用入口已退役 | 重发已验 unit、公开 `--chapters`、拿旧整书入口冒充 unit adoption、补字段或部分聚合冒充正式候选 | 码·契 |
| A 正式审阅逐章拒绝 | 当前 opaque `--review-token` 由服务端展开决定；Supervisor 在执行前打开 fresh producer epoch、重读 exact action 且不再调用 Reviewer，只返修 request_repair 章；每个 repair epoch 最多两轮，累计约束不可遗忘；耗尽后可新开同策略 repair epoch或走完整章 preview/apply | 在修前 producer run 内续写、把单章耗尽自动升级成整书 replan、把默认模板理由当真实反馈、字段 patch、无决定随机重采整书 | 码·契 |
| A 正式审阅策略拒绝 | 同样先切 fresh producer epoch；`--replan` 携 typed 累计策略/章节失败反馈和 accepted 章约束，只重跑 effective 输入变化的章 | 在原 aggregate 的 producer run 内 replan、丢掉失败史重新采样、让未变已验章重发 wire、把自由文本 reason 当执行约束 | 码·契 |
| A repair/replan aggregate 已 prepared，source producer 已结束 | operator 只发布 raw/self 双 SHA 绑定的 `--recover-repair-aggregate`；新 recovery run 在任一写前复核 source 生命周期/账/响应与当前 compiler/HARD，幂等补 candidate/adoption/audit/committed | 重解 stale review token、重唤 Reviewer/Executor、续旧 provider、按 newest 挑 journal、从多份/双副本/第三态中猜 winner、先改候选再验账 | 码·契 |
| B 章池遇到可恢复交付失败 | 当前 run 由严格 `delivery_recovery.json` 系统暂停并只补 pending；已 ended 的旧 run 由新 run 逐章严格复验后采用，允许多来源和全部采用；prepared 尾窗优先免费补尾 | 预提交全部章节、把普通 exit 2 洗成 paused、复活 ended parent、复制 checkpoint、续旧 provider session、在冲突业务回答间猜一个、把未知来源当未命中、漏记历史 usage | 码·契 |
| 模型声称 B `infeasible` | 同 session 结构化反馈要求 bound；真实代码 witness 可落正式终态；亦可提交 `--apply-binding` 或完整 v5 人工计划 | 信任模型自证、B 私改 A、自动生成 A2、要求已有 Plan 才给首次恢复 | 码·契 |
| 最终 Structure 人审 `request_replan` | 独立 resolution Reviewer 交叉读 B 决定与 current A；有具体 coverage 语义损害才提交非 all-accept typed A resolution，仅有 read-only 理由则 typed stop 安全停机；review contract/subject 变化后再审 | 用同 token 覆盖 B、把 metric/INFO/UNVER/binding concern 变成 A constraint、强迫 all-accept 或无依据付费返工 | 人·契 |
| 计划落 `.INVALID` | 修订 v5 `plan_edit.json` 后 `--apply-edit`，或 `--revalidate --actor --reason` | 改名提升、绕过 schema/判据/归档 | 三源 |
| Context 缺失或 stale | 最终结构全章 accept 后用 `stage4.context` 免费重建 | B/计划修复时提前代写，或用旧上下文继续写 | 三源 |
| 旧版本 | 从 producer 重建；人工稿重写为 v5 | 迁移、猜字段、手补版本号 | 码·契 |

## 八、现行判据

当前注册表共 **29 条**：**15 hard、4 info、10 metric**。唯一注册表为 contracts/judges/stage4.py。

| 分组 | 当前规则 |
|---|---|
| 章场与呈现 | `one_to_one` · `ordinal_seq` · `empty_chapter` · `placement_ref` · `scene_transition` · `goal_landing` |
| 预算与写法 | `budget_int` · `budget_band` · `plan_book_band` · `style_words` · `verbatim_starved` |
| 来源与 Context | `phase_artifact_link` · `context_link` · `ctx_plot_exact` · `ctx_name_attested` · `ctx_permission_exact` |
| 语义与舞台提示 | `presentation_semantics` · `causal_disclosure` · `stage_signature_conflict` |
| 度量 | `scene_budget_distribution` · `verbatim_share` · `partition_change` · `disclosure_order` · `noncontiguous_regroup` · `anchor_migration` · `structure_eligibility` · `structure_alignment` · `semantic_closure_reading` · `ctx_source` |

### 上游局部重建与合法父节点

当前 A producer 在 provenance 保存 `design_dependencies`：策略和逐章普通请求的实际依赖摘要。
宏观策略审阅同样消费 `build_blind_strategy_projection`；`basis.truth_sha256` 保留整书证据快照，
不再单独令宏观策略或无关章义务失效。候选、证据、策略、候选集合、边界或义务实际变化仍须复审。

正式候选明确 stale 时，普通 A 重建先由其唯一 committed aggregate、完整文件 SHA 和全部来源链
确认合法父节点。实际依赖不变的单元保留该父节点中的来源，包括人工章替换和 typed 修复；变化章
收到当前父设计及新剧情，再走同一 compiler 与 HARD。人工正式别名与模型局部别名通过编译往返
相等校验转换，原人工来源、理由和费用不改写。没有依赖凭据或来源不闭合时保持 stale/unknown，
不取修复前旧缓存覆盖当前父节点。

聚合 journal 的 `finalization_payload.rebuild` 冻结父节点身份与保留源全集；普通新回答、历史采用和
保留源必须无重叠覆盖策略及全部章。提交、免费 prepared 恢复与下游 repair/human 消费验证同一来源
集合，重建不清空已提交修复轮数或未关闭义务。上游变化若发生于同一 Agent epoch，Supervisor 先
关闭并 drain 已提交 producer 的旧 epoch，换新身份后重读 next/token，边界本身不调用 Reviewer。

原回答落盘早于 checkpoint 的窗口由 `stage4.design --recover-responses` 免费闭合。queue 的只读发现优先于新的生成和聚合；执行持原 owner 停止锁，以原请求、原完整配置和费用回执恢复原 checkpoint。正式候选仍由下一次确定性聚合提交，失败或损坏保留 unknown。

### 上游真实缺陷的例外路径

设计或结构审阅可提交 BlueprintRefinementRequest：当前 origin/token、章节、维度、主体及具体疑点。
A 只可用当前 opaque 主体或 chapter；B 可用本章正式主体。代码定位证据并保存稳定实际问题，
不会因为定位到证据就宣布问题成立。转交本身零调用，只让 Blueprint Commander 重新反查该章。

Commander 可撤销不成立的疑点；确认漏抽、错抽、重要揭示/知识/独立依赖边界缺失时，才按当前
Stage2 编辑或定向生成入口修订。Executor 写回不能自动解决问题，仍需章审和独立跨章复核。
无据的新剧情不能借转交进入真相层。exact 转交重放只证明已保存，不覆盖后续撤销/解决，
不重置 scope 或书级累计预算。
