# 活动账：未完成项

本文件是唯一活动计划，只保留**未完成项**与**待删除的兼容层**。完成项、验证分母与命令写进提交说明，
复用教训进 [LESSONS.md](LESSONS.md)，裁决缘由进 [DECISIONS.md](DECISIONS.md)。
行为依据为 [BACKEND_CONTRACT.md](BACKEND_CONTRACT.md) 与 contracts；目标架构见 [str06.md](schema/str06.md)，
本轮规格见 [repair_convergence.md](schema/repair_convergence.md)。
[返修场景](specs/repair_convergence.feature)、[计量场景](specs/cost_accounting.feature)、
[整书场景](specs/book_acceptance.feature) 是目标行为说明；文件存在不表示已通过。

**2026-10-05 重置（用户指令）**：仅保留未完成项。重置不清账、不删输出，不改变冻结实验身份、分母、额度或既有授权。
已移出的非待办内容——已完成批次索引、各批验证记录与证据路径、运行基线、成本与授权口径、长期义务行
（已购证据不可当派生缓存删除、章集合与交付恢复、可信 owner 修订准入等）——完整记录见提交 `eb5f0d5a` 中本文件；
退役清理三批与清理收尾批的批次登记见提交 `1b086afd`；更早执行史见 `e84a169b` 及 `git log -- docs/REQUIREMENTS.md`。
R10 执行指南 PLAN.md 已于 2026-10-05 退役（用户裁决，原文见提交 `68b5802f`）；其仍有效的 R10d 段、对照裁决与验收口径并入下方 CS-G。

**授权现状**：既有付费与正文外发授权只覆盖原三书 manifest、原根、原配置与额度（见 CS-T）；rc16 新根上的任何
付费调用与正文外发都须单独授权（付费显式 `--yes`，外发 `--allow-egress`）。
**CS-F3 标定授权（2026-10-05 用户批准）**：语义核验判官的真实标定调用，上限 **90 次**；档位经用户中途调整
（2026-10-05）：deepseek-flash、thinking on、**effort low、max_tokens 65536**（首轮 high/4000 的 15 次实测全部把
额度耗在 reasoning、content 为空，如实留账不退还）；材料为仓外 Golden_Set_v3.0 三本 yaml 的 source_checks 与
rc15 冻结根（`outputs/cs_golden_20261004_rc15`，只读）的真实产物片段，正文外发随之授权（核验请求含源文片段）。
纪律：标签先冻结提交、后发付费调用；账记 ledger；不授权之外的模型／额度／根。
**（2026-10-05 封口）**45/90 次已用（末轮判官不满足启用硬规则，见下），剩余 45 次授权额度**作废**，
不再提示词迭代重测；重开须新授权并满足 DECISIONS 同日登记的重开条件。金标 yaml 三本复认为 CS-G／CS-F5
成书对比评估金标（原「即 CS-F3 金标」的判官标定用途随本批封口终止）。

## 未完成项

### CS-F Stage4 停点收敛（RC-30～49）

规格见 [repair_convergence.md](schema/repair_convergence.md)“Stage4停点解决方案”。已提交（`edf965d7`）：完整候选预检、
事实锁引用（Design wire v8）、写手称谓投影、局部修订；历史采用的来源账与相对 `--adopt-root` 缺陷已在 `eb5f0d5a` 修复。
契约因此升 rc16：`cs_golden_20261004_rc15` 三本旧根按契约表漂移拒收，保留为冻结失败证据，**续跑须新根**。约束不变：
不新建通用验收框架、不增加工作流节点、不顺带引入任意目标称谓创作功能、不提高重试上限。

| 子批 | 状态 | 余项与完成条件 |
|---|---|---|
| CS-F2 事实引用与名称呈现 | [~] | Hook 称谓投影已闭合（2026-10-05）：`HookContext.withheld_bindings` 按 Hook 自选 `surface_releases` 推导（与章 IR 同一把尺），`render_hook_context` 渲染期投影正向材料行与禁区行，上下文字段与来源归因保持原字段文本，留痕 `hook_name_projections` 进 guard report；`hook.json` 升 rc16，旧 rc12 楔子上下文拒读、免费重编译。余项：v8 提示词与 `lock` 闭集在真实模型上的表现未验证。Stage7 无需改动 |
| CS-F3 关键语义离线标定 | [x] | 已封口（2026-10-05 用户裁决）：判官 flash/low 不满足启用硬规则，语义闸不启用、生产语义维持 `unver`；提示词迭代余项撤销，剩余 45 次授权作废。结论、归因更正与重开条件见 DECISIONS 同日行 |
| CS-F4 本书正式恢复 | [~] | 见下 |
| CS-F5 成书与成本对照 | [ ] | 相关目标章节与最终正文核验；六维与自动程度分别报告；核验新增成本与返修节省量同口径比较；不以 Stage4 继续运行代替完成 |

**CS-F3 已封口（2026-10-05 用户裁决，裁决全文见 DECISIONS 同日行）**：开发集标定执行 45/90 次真实调用
（三轮报告与账本在 `outputs/csf3_calibration_20261005/`，已随 rc15 冻结根等证据安置主仓；装置代码可查提交
`2d17a898`，harness／装载器／冻结标签与测试登记已删除）。末轮 59/70 案测得：正确 51（支持 29／否决 18／
未确定 4），错误支持 1＝ch03 已裁决误派关系被 judge 背书（判官与抽取器同为 flash 的同源背书，恰为历史事故
模式），错误否决 5，证据不足被断言 5，未确定漏判 2，wire_error 9＋missing 2（11 案未测得；其中引用渲染行
前缀与首轮锚点窗口 ±2 量程不足属测量管道缺陷，归因更正见 DECISIONS）。启用硬规则违反，**flash/low＋当前
提示词不可启用语义闸**，留出集未运行（先决条件不满足）。裁决：提示词迭代余项撤销、剩余 45 次授权作废；
生产语义继续按现役 `unver` 披露，已确认错误经 owner patch 纠正（ch03／ob44 走 owner patch，见 CS-F4）；
fake transport 绿例仍不得记成语义核验已通过。重开三条件（判官与抽取器不同源、两本以上书有观察级已裁决
案例、装置缺陷修复）加新授权缺一不可。金标回归用户原意：Golden_Set_v3.0 三本 yaml 为 CS-G／CS-F5 成书
对比金标（见 CS-G 前置），不再承担判官标定用途。

**CS-F4 本书正式恢复**（《夫君的报恩》，11 章）：

- 起点：旧根 `outputs/cs_golden_20261004_rc15` 在 Design 首答＋两轮修订后 `validation_rounds_exhausted` 停止（整书额度
  11/512），冻结为失败证据。零付费前置核对已做（2026-10-05）：在干净新根重放 stage0/1 后，用免费入口 `--check-only`
  复核——Stage2 全节点与 Preserve（62 项义务）可经历史采用 0 成本进入 rc16 新根（book 与 8 章经 `--wire-dir`，wire 由
  rc15 根 `runtime/revisions/evidence` 按 owner 摆出；ch04／06／08、names、associate、Preserve 按同请求身份；
  `stage2.compile` 确定性重放）；**Design 无同请求身份结果（wire v8 与提示词换版），必须重购**。探针根
  `outputs/cs_f2_adopt_probe` 只含零 token 采用行，不是冻结证据；复用只经现役采用入口，换算与摆放脚本不入库。
- 待办：① 正式 owner 修订：ch03 主体误判（夫君模仿表弟称谓的场景被记成芸娘与表弟已发生关系，Preserve ob18 又锁为必保）、
  ob44 无依据限制、Preserve 编造的源文没有的短姓称呼 14 处（陆×13、裴×1，写手材料预检仍报出，不可投影）——先修源观察
  及派生产物，不能靠改目标稿掩盖；② 审计 62 项义务的重复／过保（不设目标条数；同机制不同时点／持有者／认知模式不得为
  降费合并）；③ 按实际依赖重建 Stage2／Preserve／Design／Hook／Writer／Stage7，原响应、接受历史、费用与累计轮数保留，
  人工恢复不得记为全自动成功；④ 新 wire 的新语义输出明确列明不可免费复用的原因，不隐式换根、换模型、增预算或授权外发。
- **①②已闭合（2026-10-05，零付费）**：补丁 wire 与申报理由在 `outputs/csf4_fuhun_restore_20261005/wires/`（ch03：beat3
  主体更正＋删 relation 状态、beat4 认知更正、「（陆郎）」批注 4 处清除；preserve：ob18 改写＋锚点迁 `b03_04.c1`、ob44 删
  无依据否定限制、短姓 14 处清除；判据＝用户裁决＋金标 SIG-01＋源文逐字核对，构造脚本不入库）。义务审计报告在
  `outputs/csf4_fuhun_restore_20261005/obligation_audit.md`：无语句级重复、共享锚点均不同承载面不合并、否定限制 66 条
  除已修 ob44 外未见无源依据、事实主张抽查（含楔子）全部有据、必保比例 100% 如实登记。全链免费段已在 scratch 根
  `outputs/csf4_patch_probe_20261005` 端到端验证（stage0/1 重放→book/8章/names/ch04·06·08 零成本带成本链采用→compile
  →补丁 preserve 62 义务锚点全解析；Characters 错误关系已消失）。同批修复 `chain.py` wire 采用拒收分支闭包晚绑定
  NameError（错误面丢坐标，违 I-36），回归测试 `tests/test_str06_f7a_wiring.py::test_chain_wire_rejection_reports_with_coordinates_not_nameerror`。
- **机制发现（待用户裁决）**：ch03 补丁改变关联空间与蓝本 ⇒ **associate／preserve 请求身份漂移**，rc15 付费行不再覆盖
  （REQUIREMENTS 原「Preserve 可 0 成本采用」仅对未打补丁基线成立）；且 v5 章 wire 系仓外换算产物，其身份无任何已购基线，
  owner_patch 绑定档不可达 ⇒ ch03 补丁当前只能 unverified 采用（机械闸全过、无正式 OwnerRevisionRecord 账）。恢复路线二选一：
  （A）付费重购 associate＋preserve（机制最干净；preserve 若复现短姓类错误可在新根内走正式补丁链）；
  （B）零成本采用手工 wire（unverified 如实留痕，登记为人工恢复）。④ 的不可免费复用清单已在 wires/README 登记。
- 阻塞与口径：须独立新根与付费／外发授权；即使只做免费采用，CLI 入口也要求 `--yes`（`--check-only` 是唯一免费入口，且会写
  探针根的采用产物与零 token 账行，不称纯只读）；ch03／ob44 已裁决（2026-10-05）走手工 owner patch——flash 抽取时
  读错一次、核验时两次都认同该读法，付费重抽不保证读对。Design 重购量级参照
  （rc15 根账均值，非报价）：约 34k 输入／62k 输出（reasoning 约 52k）；associate 约 17k 输入／4k 输出、preserve 约
  45k 输入／39k 输出（rc15 读数）；hook／正文×11／naming 无本书历史读数，账行以 ledger 为准；账内金额为空，只报 token。

### CS-T 分节点思考对照（含 CS-4：稳定输入前缀）

[~] 冻结清单草案 `outputs/cs_review/experiment_plan.draft.json`；不预设所有节点降低思考，节点正式输入等制备通过后再锁请求摘要。

- 控制配置：DeepSeek、`deepseek-flash`、thinking on、effort high；`str06.stage2.chapters` 与 `str06.stage5.body` 节点并发各 32，
  整书总槽 32，供应商总槽 200（三项均为 1～200 整数，可再改）。
- 唯一实验变量：`str06.stage2.book`、`str06.stage2.chapters`、`str06.stage4.preserve`、`str06.stage4.body_design` 四节点的
  high／low，思考保持开启，其余节点不降档。
- 停止规则沿用现役常量：整书内容调用 512、每内容轮最多 4 次、owner 修订 2 轮、跨节点回派每书 12 次、输出上限 131072；
  截断、无进展、unknown 和额度耗尽都停止，不自动切档、换模型或追加额度。
- 开发书《夫君的报恩》11 章，制备停在 `str06.stage5.hook` 之前，无修订预期 17 次，加四节点对照臂共 45 次；三书章数
  17／22／11（三人成行／夫君不行但我有孕啦／夫君的报恩），后置整书无修订预期 124 次（均为预期，不是上限）。
- 授权边界：原授权只覆盖 `outputs/rc14_acceptance`、代码 `14312181`、全节点 high 和总槽 4／8（已用 54／512）；新根、新代码、
  总槽 32／200 与 low 臂不在其内。未创建实验目录，未发付费或外发请求。
- 前置：草案钉的代码基线（`497d0a38`）与契约早于 rc15／rc16（Design wire v8，`semantic_binding`／`writer_book_ir` 升 rc16），
  授权前须按当前基线重钉清单与分母。

### CS-G＝R10d／B6c 整书验收

[ ] 按用户要求后置，未发起新付费；六维工具已对齐。等其余问题（CS-F）修复后冻结三书并真实执行，人工与自动分别报告。

- 前置与冻结判据：Workbench 三本源书以显式 `NOVEL_DATA`／`NOVEL_CORPUS` 核验，`NOVEL_OUTPUT` 是独立输出根。Golden Set v3.0
  《夫君的报恩》《三人成行》双格式已冻结（源 sha 与 216＋139 锚点曾核验）；夫君为开发可见用书，三人成行 oracle 保持隔离，
  不得进入生成／修复材料。Workbench 另有《夫君不行但我有孕啦》的 Golden Set v3.0 文件，冻结状态与源 sha 核验待确认（此前
  只登记前两本）；若其 oracle 未冻结，依据源书与同一六维评价，不伪造 oracle。
- **金标复认（2026-10-05 用户裁决）**：Golden_Set_v3.0 三本 yaml 确认为本节成书对比评估金标（CS-F5 同）；
  其此前短暂承担的 CS-F3 判官标定用途已随该批封口终止（裁决见 DECISIONS 同日行）。
- 冻结源版本、代码、模型、协议、预算、停止规则、评价方式与输出根；不得缩三书分母、择优替换失败轮或把恢复后的产物冒充
  独立新生成；旧冻结运行与新候选分别报告。不做缺乏完整基线的新旧 renderer 付费对照（历史 F5e 原根已清理，只可引用历史
  读数）；没有可比完整成书时不宣称整书相对改善。版本结论只报绝对达标（六维＋冻结关键案例＋九维阅读），对照缺失如实登记；
  普适性由 R10d 第二本书与重复生成披露，不外推。
- 评价以阅读为主，模型辅助另授权；只改评价过程时重评现有成书，不自动重生成；每个结论绑定精确成书 hash，改文后重验受影响
  结论；工程通过不代签质量。
- 六维：Core Preservation、Structural Originality、Causal Continuity、Character Continuity、Natural Progression、Local Prose
  Coherence；Structural Originality 按场景与语义承载比较，不要求源目标同号章对应。NameMap 跨书与第二本表达力须独立证据。
- **R10d 重复轮**（原 R10 执行指南的 R10d 段）：《三人成行》新根全链，对照其 v3.0 oracle 隔离评测；《夫君的报恩》至少一次独立
  重复生成（独立空输出根，同候选版本固定源文／代码／协议／模型／预算／停止规则，不采用上一轮已购回答），用以区分「对反例书
  定制」与「普适改善」；模型 A/B 只在此后按瓶颈层单独做。原三书 manifest 与该长期重复义务分别核对，不把一次三书运行记成
  重复轮已完成。
- **结果分类与自主完成度**：反例结果至少三分类记录——错误未再出现／错误被系统发现并修好／错误仍静默进入下游；停止、未完成、
  未评价另列，不计入「错误已消除」；SourceTruth 错标 known 后下游正确继承只算工程传递通过、源语义仍可能失败，分开表述。
  自主完成度三类：原定规则内自主完成（预算／修订／停止规则预先冻结，无人工内容 patch、基于质量的人工重采、临时加额度或
  影响内容选择的人工决定）／无人改稿但有人辅助完成／未完成，纯运维重启单独标记。「评测完成」（规定运行的结果、失败、干预、
  评价与成本记录完整；系统性错误致剩余运行未启动时标记中止与未执行单元）与「版本通过本轮样本验收」（每书两份新稿分别
  达标，不挑最好的一份、不用辅助重采覆盖原轮失败）分开报告。防作弊分母：同时报义务总数与必保比例、未知锁数量、停止次数，
  不以删义务、标未知或中止运行换通过率。
- **成本口径**：以 `_service/ledger.jsonl` 为权威，逐轮记录付费调用、缓存、自动修订、失败尝试、耗时与人工干预；未知不作零、
  reasoning 不重复计入。每份可接受成书成本按该版本评测总费用分摊（含失败尝试与自动修订），旧版对照与一次性研发费另列；
  合格成书数为零时报告总投入与零成功。

#### 人工阅读验收协议

九项观察继续用作六维证据：情节因果、揭示时点、章间衔接、人物声线、说明书式正文、重复交代、行动对话展开、Hook 吸引力与
披露边界、整体节奏流畅。记录“章段→维度→归因→处理→证据”，归因用现役 Stage2／Preserve／Design／compiler／renderer／
写手协议／写手发挥；bind 已退役。正文及细读记录留运行根，不在仓库摘录。未读保持 `unver`。

### 其余未完成项

- **CS-A 证据寻址（RC-26～29，rc15）**：另两本《三人成行》《夫君不行但我有孕啦》在 rc15 根只完成 book 与章观察（17／22 章），
  names／associate／compile 未运行（需付费授权）；完整首答通过率等下一本新书实测。
- **CS-2 后续**：book 观察仍同时使用全文排版与证据地址，不能未经语义充分性证明删除；`basis.quote` 逐字要求不因省 token
  取消；Preserve 输入／wire 瘦身仍有余项（Preserve 必要事实补全与 Design Brief 投影已闭合；补齐语义使 Preserve 体积增加，
  不能记作降费成功）。
- **R7 质量优化**：持续登记，按实际阅读反例启动：漏保／过保、因果、承接、声线、导演说明、重复与输入不足。风格画像仅在
  一句一段、环境密度、幽默通道、POV 漂移等既有批评复现且有阅读证据时提出具体方案；under-spec、过度失效、状态过建模、
  checklist prose、章内披露及表达力未验证继续登记；第一人称书不能代签第三人称稳定性。
- **工程余项**：同身份强制重采、一般 REDO 级联政策仍属既有产品裁决边界，只闭合已证明的消费链，不扩普通队列权限；
  调并发前先看真实 429／unknown。
- **token 量级表**：新量级由账与补证逐节点重建（旧 B0／F5e／F9 量级仅作诊断参照，非现役报价，见 `e84a169b` 历史表）；
  异常先回查输入和协议；JSON 字符下降只证明 wire 瘦身，未证明整书费用下降；已退役 bind 不再作为预算项。

## 待删除的兼容层与删除条件

过渡例外必须在此登记删除条件；新增兼容层先取得用户裁决。

| 对象 | 有效义务与删除条件 |
|---|---|
| rc12／rc13 五类产物 adapter | `novel/store.py::_upgrade_rc12_payload` 只迁移白名单无损信息；旧 Brief 缺认知模式免费重放 Preserve，旧 Design 缺 confirms 走显式 owner patch。存量全部重建或退役后删除此 adapter 及 Str06Repository.read_graph_hook 的旧组件迁移，禁止无限新增版本 |
| OwnerIssue v1／v2 读兼容 | v3 显式 dispatch_pending、指纹历史与更正记录；只经 `contracts/schema/workflow.py::owner_issue_record_of_payload` 集中解码；所有存量 v1／v2 根只读归档／不再续跑后删除。旧错误路由账的更正按 RC-15 显式恢复，不重置轮数 |
| names／Characters旧信封 | names v2与两类信封v2显式拒识旧版；须新协议names重建后compile重建Characters，不推断空resolutions。原冻结根只读，授权新epoch再重建；无隐式adapter |
| 历史 Step 缺请求配置 | Step 的 CS-T 加法契约集中在 `contracts/schema/orchestration.py::Step`，读取缺字段为 None；展示为未知，不从默认配置推断旧请求。新执行和 followcheck 必须有同源配置。旧 JobStart 只读归档不再消费后删除缺字段兼容 |
| Golden评价v1 | v2显式拒识旧manifest／review／snapshot／result；按冻结六维重新收集、重评现有成书，不自动迁移旧结论、不要求为评价升级重购 |
| 候选身份冻结 | CS-R补齐信封版本与operator_redo；names／Characters信封v2、Hook有限owner修订改变候选身份。新身份由`tests/test_str06_f7e_freeze.py`冻结，旧根不追改；旧身份按不兼容只读拒识 |
| 字符串判据身份 | `novel/core/validation.py::validation_identity` 集中解码仍现役字符串判据；材料、章内悬念冲突与关联问题已用结构化身份。剩余判据全部提供稳定身份后删除字符串前缀兼容，不在各节点扩散解析 |
| 旧 associate 误派更正 | `novel/stages/issue_recovery.py::_legacy_loop_correction` 只从完整正式章观察重建历史重复 close 错误，证明旧问题全部覆盖后才显式 superseded；不解析文案猜 owner、不退费、不重置轮数。旧字符串 association_undecided 账全部只读归档且不再恢复后删除该兼容分支 |
| 已退役链 | 拒识legacy_incompatible，RETIRED_ARTIFACT_PATHS和盘面探测维持闭合；守卫`tests/test_retired_surface_scan.py`，不复活历史解码面。三批退役名册统一登记在`tests/test_retired_surface_scan.py::CLEANUP_2026_10_04_MODULES`，回来即红 |

### 候选根运行记录映射与删除条件

B1 迁移已完成，保留以下恢复索引：旧 request 一体记录由 SemanticRequest＋ProviderAttempt 取代，validation 由
AcceptanceRecord 取代，adopt 由 ContentRevision＋CommitBundle 取代，body／hook attempts 由 ContentRevision＋ProviderAttempt
取代。旧 admission 平行计数已退出，额度由现役工作单元、整书总闸和 ledger 承担，unknown 也占额。旧文件仅留原根只读证据；
不恢复旧写路、不双写、不补造旧 runtime/request 文件。完整映射见重置前 `e84a169b` 本文件。
