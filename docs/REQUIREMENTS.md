# 活动账：未完成项

本文件是唯一活动计划，只保留**未完成项**与**待删除的兼容层**。完成项、验证分母与命令写进提交说明，
复用教训进 [LESSONS.md](LESSONS.md)，裁决缘由进 [DECISIONS.md](DECISIONS.md)。
行为依据为 [BACKEND_CONTRACT.md](BACKEND_CONTRACT.md) 与 contracts；目标架构见 [str06.md](schema/str06.md)，
返修收敛规格见 [repair_convergence.md](schema/repair_convergence.md)。
[返修场景](specs/repair_convergence.feature)、[计量场景](specs/cost_accounting.feature)、
[整书场景](specs/book_acceptance.feature) 是目标行为说明；文件存在不表示已通过。

**2026-10-07 重置（用户指令）**：销账 CS-F、CS-H、CS-G、CS-T 四个待办，整节移出活动账（裁决登记见 DECISIONS 同日行）。
CS-F（Stage4 停点收敛）：F3 判官标定已封口、F4 本书正式恢复与 F5 成书对照已完成，F2 余项（v8 提示词与 `lock`
闭集在真实模型上的表现）撤销追踪。CS-H（接受版本只发一次、首答通过、窄返修）：H0～H4 已完成，H5 验证与收口
（四批合并后最终回归、csf4／rc15 免费重放前后对照表、真实运行）关闭——真实运行未获授权从未发起；其待用户裁决
第 1／2 项随销账闭合（真实运行授权范围不再作为待办追踪；sc11 计划与实际不一致的付费修订选项关闭，D1 是否走
owner patch 修订仍以 R7 行为准），第 4 项按默认保留闭合（承载章必保姓名字面与事实锁主体包含两处补强已是现役
HARD 判据 R-7.8／R-6.7）。CS-G（R10d／B6c 整书验收，含 R10d 重复轮义务与人工阅读验收协议）与 CS-T（分节点
思考对照，仅冻结清单草案，未创建实验目录、未发付费或正文外发请求）一并移出，未来如再启动须新立项。销账不删
输出、不改变冻结实验身份、分母、额度或授权边界，也不撤销已登记的历史裁决、规格与兼容层删除条件。四节完整原文
见重置前最近提交 `eae0c449` 中本文件及 `git log -- docs/REQUIREMENTS.md`。

**2026-10-05 重置（用户指令）**：仅保留未完成项。重置不清账、不删输出，不改变冻结实验身份、分母、额度或既有授权。
已移出的非待办内容——已完成批次索引、各批验证记录与证据路径、运行基线、成本与授权口径、长期义务行
（已购证据不可当派生缓存删除、章集合与交付恢复、可信 owner 修订准入等）——完整记录见提交 `eb5f0d5a` 中本文件；
退役清理三批与清理收尾批的批次登记见提交 `1b086afd`；更早执行史见 `e84a169b` 及 `git log -- docs/REQUIREMENTS.md`。
R10 执行指南 PLAN.md 已于 2026-10-05 退役（用户裁决，原文见提交 `68b5802f`）；其 R10d 段、对照裁决与验收口径
当时并入 CS-G，后随 CS-G 于 2026-10-07 销账移出（原文查法见上）。

**授权现状**：既有付费与正文外发授权只覆盖原三书 manifest、原根（`outputs/rc14_acceptance`）、原配置与额度
（全节点 high、总槽 4／8，已用 54／512；范围明细原登记于销账前 CS-T 节）；任何新根、新代码、新档位或新槽位上的
付费调用与正文外发都须单独授权（付费显式 `--yes`，外发 `--allow-egress`）。CS-F3 判官标定授权（上限 90 次）已于
2026-10-05 封口作废——45 次已用、剩余作废，重开须新授权并满足 DECISIONS 同日登记的重开条件；
Golden_Set_v3.0 三本 yaml 的成书对比评估金标复认（2026-10-05 用户裁决）仍有效，口径见 DECISIONS 同日行。

**2026-10-09 R7 单书新跑授权**：用户批准《夫君的报恩》的付费与向 DeepSeek 外发正文；原输出根在本工作树
outputs/rc21_quality_20261009，同日用户要求整根转移至 E:/practice_group/Novel_rewriting/outputs/rc21_quality_20261009，
保留原产物、账与额度。配置为 deepseek-flash／high／thinking on，用户单独指定下一节点 stage2.chapters 使用 low。
范围先 Stage2～4，再按新设计
选最多 6 章正文；逐节点执行，每节点产物交用户验收，用户确认后才进入下一节点。沿用既有单元修订上限，
额度耗尽或无法窄修即停，不追加预算、不扩书目、不增加 Judge。此授权不复活其他冻结实验额度。

同日用户确认空拍漏抽两部分修复：本书 ch04 第 4 拍以免费 owner patch 新增芸娘与裴书远
获知“夫君从床底现身”，分别绑定 e04_17/e04_19；原已购响应不改，其余十章 SHA-256
一致、该章其他拍去除派生命题编号后的内容一致，账本仍为 12 次付费（本次新增 0）。
Preserve 升独立 wire v4/policy 7：允许既有拍＋该拍证据报告遗漏，义务/details 原子锚点
闭集不变；chapters 回派只修所报拍、保留其他章，普通空知情合法。没有新增字段、节点或
Judge。回派整链反例另暴露节点间输入边串入上游修订，已按节点边界清空来源窗口；保留章只验封/版本/新鲜度、不记内容依赖。章执行、派单关闭、补丁激活均使用同一局部范围。names/associate/compile 及后续节点已随 2026-10-09 跑书全链执行并交付（13 个 str06 节点全执行，workflow_status=delivered、through=stage7，accepted_body=true，总字数 9084/9000）；强化提示能否改善
首答漏抽率、成书阅读质量和整书成本仍待后续实测，不把机械回归当作语义检出率。

本批定向验证（2026-10-09，HEAD 97fcf290 上工作树）：§9.5 五文件及受影响的
Stage2/4、发布、恢复、下游消费等共 861 项通过，0 失败/跳过；收尾范围边界变更再跑
回派、瘦 wire、workflow、runtime records、CLI 五文件，86 项通过，0 失败/跳过。
JUnit 分别为 .test_cache/missing-conflict-final.xml 与 missing-conflict-scope-final.xml。
这是定向工程回归，不是全仓终验或 golden 成书质量验收。

同日用户确认 Design 首轮可用率修复：按 Preserve 可交接条件、原子条件与自然叙事拍分离、
版本化请求压缩的顺序实现。授权用本书冻结材料向 DeepSeek 付费配对，保持 flash/high/thinking on；
费用仍走原根工作单元与整书总闸，不追加额度。首轮完整可用率必须含写手材料完整预检，失败、
返修、累计 provider 调用耗时与设计核读分别记录；单书结果不外推总体成功率或成书质量。
2026-10-17 身份引用批次后，v9/v10 缺少当前逐次语义声明，只供只读诊断；不能免费补造后签成 v11。

## 未完成项

2026-10-09 用户确认独立事件锚点方案：现役版式 rc22、Preserve v5/policy 8，
六类质量产物显式升版；冻结身份为 `9a7e1067bbbcca471d237f52ee2df63fc75020ed2166ff0aceb4272b2a51932d`。
真实 Design/正文已随 2026-10-09 跑书在 rc22 版式下全链交付（13 节点全执行，
workflow_status=delivered、through=stage7、accepted_body=true，总字数 9084/9000）；
首答成功率及成书阅读质量仍属下列 R7 待核，工程测试
和免费人工修订不代签这些结论。详细前后证据在本书 runtime/review/rc22_event_anchor_review.json。

- **CS-A 证据寻址（RC-26～29，rc15）**：另两本《三人成行》《夫君不行但我有孕啦》在 rc15 根只完成 book 与章观察（17／22 章），
  names／associate／compile 未运行（需付费授权）；完整首答通过率等下一本新书实测。
- **CS-2 后续**：book 观察仍同时使用全文排版与证据地址，不能未经语义充分性证明删除；basis 已指针化
  （2026-10-07：wire 只给段 id、引文＝段全文由代码切出，转写失败类错误构造上消失），Preserve 输入／wire 瘦身
  仍有余项（Preserve 必要事实补全与 Design Brief 投影已闭合；补齐语义使 Preserve 体积增加，
  不能记作降费成功）。
- **R7 质量优化**：持续登记，按实际阅读反例启动：漏保／过保、因果、承接、声线、导演说明、重复与输入不足。风格画像仅在
  一句一段、环境密度、幽默通道、POV 漂移等既有批评复现且有阅读证据时提出具体方案；under-spec、过度失效、状态过建模、
  checklist prose、章内披露及表达力未验证继续登记；第一人称书不能代签第三人称稳定性。
  2026-10-09 rc21 创作材料交接的阅读效果、首答可用率与整书成本待用户新跑反馈；原子条件、可选细节、
  系列前情与姓名窄修的工程实现不代签这些质量结论（裁决见 DECISIONS 同日行）。
  同日阅读反例推进顺序：先由 book／chapters 修自我表述的归属、关键目睹者与跨章悬念收束；
  Preserve 发现证据冲突走已有源 owner 回派；Design 修重复确认、直接承接的反应归属与章末停点。
  称谓保留显式释放，普通姓名在需要前释放，区分叙述与对白用途；暂不增加已写前文依赖。
  独立新跑待核：目睹渠道、求娶—反应—跃梁连续性、系列概括不超前、救人动机、礼物愿望铺垫、
  改口与亲属称谓、未解守卫、楔子与开章协调及语气、已说／未说、婚前／婚后告知。
  先核对新源事实和设计，再选定点正文供用户阅读；不默认扩大整书或其他 golden，不增生产 Judge。
  2026-10-10 用户授权目标故事通用修复与有限补桥，复核后现役 Preserve policy 10 联合交接已有源证据
  的必要对话触发、行动衔接与决策条件；Design 可补局部行动、沟通和决策，保持骨架、
  关键事实、结局、锁、知情授权与刻意未知，情节桥梁落既有正式字段；Stage5 呈现
  既定故事，可补自然移步、短转场与即时动作，补写时点不得倒置触发和行动。
  statement/satisfaction/requires 合起来保住骨架与必要因果，不要求各字段重复条件；
  唯一登记称谓自然混用逐次投影，保留正式必保揭名。canonical/wire 形状不变、renderer v12，
  正文写手协议 v11；不新增场级 entry、
  人物状态模型、Judge 或节点，不做本书特判。旧 checkpoint 不静默重解释，恢复按现役
  身份与完整预检闭合。待核：源盲交接充分性、转场／因果／人物转变的设计阅读、
  首轮含完整写手材料预检的可用率、定点正文阅读质量及失败／返修／缓存分列的整书成本。
  首答单列第一份耐久响应的形状通过、完整机械预检首次接受与人工阅读达标；返修、
  缓存或免费复验后的接受不能回算成历史首答通过。用多书新请求对照，不从单书外推。
  本批工程验证零付费，机械通过不代签上述质量结论；新付费及正文外发验证须另行授权。
  工程证据（`434f7506` 工作树，`novel_refactor`）：§9.5 五文件及版本／登记 477 项通过；
  25 文件消费定向回归 525 通过、4 失败，修正现役 rc24 预期与新增用例的章间承接断言后，
  4 项回放通过，无 skip。覆盖源→义务→Design→IR→实际章请求、未知锁拒绝、system
  漂移后的重写／恢复及 HTTP／CLI 合成书交付；未执行完整非 corpus 回归或 golden set。
  同日用户复核后，旧 Design/IR/正文对照撤回 ob46/ob53/ob67 仅凭措辞的失败推断：
  半月保留预估、可见退场后藏梁不必提前交代、递锁仍是“像是看穿”且用途未解。
  已坐实的阅读缺口为第8章廊下直接跳床边、第11章晚饭后告知接当天午后出发，后续
  新 Design/正文验收重点核对自然转场与新增钟点的先后。自然混称、重复呼唤、标题词
  误拒和 Hook 地址级隐藏的通用修复不增加生产语义闸，工程通过仍不代签成书质量。
  已购 Preserve policy9 的原首答免费复验与 policy10 显式干跑均可用，历史首次失败不
  重算；现有 policy9 checkpoint/Brief 仍 fresh/match/ready，可继续进 Design，无需
  为本轮代码修复再购 Preserve；盘面仍明确登记 policy9，不冒充已经用 policy10 新提示生成。旧 87 条 Design／Binding 及其 IR／Plan 已因当前 73 条 checkpoint／Brief 输入身份变化而过期，后续须基于当前正式材料重新设计或按现役候选规则复核，再编译与写作；不能只换 ob 编号沿用。相邻同人提及不再折叠为免费等价，保留次数的登记称谓替换仍可零调用。本轮没有调用模型、采用候选或修改真实输出与额度。
  2026-10-10 用户批准 Hook 局部优化：以现役 contracts 精简组织契约，正式材料／声线
  同源投影，取消固定字数门槛，保留倒叙与跨场、单 Hook 和现有 Stage4 条目窄修。
  工程验证零真实付费，不新增生产审稿节点或 Stage5 段落 patch。首答文学质量仍待
  《夫君的报恩》旧新楔子配合同一第 1 章阅读验证（冲突、因果、喜剧声线、期待、简洁、
  重复与新增推进），同时记录真实生成成本；本批工程通过不代签阅读结论。
  CS-F5 阅读反例（2026-10-05，绑定成书 `16d26c4b…`，详单 `outputs/csf5_review_20261005/csf5_review.md` §二）：
  D1 婚史融合（GS-01b 负控：童年坠河＝救命同一次，财貌动机与婚前感情连带丢失，语义层静默漏过）；
  D2 藏身元计数错×2（ch8「上回是床底」应为衣柜、ch9「第三回」应为第四回）；D3 死结载体衣带→裤绳漂移；
  D4 雨夜顺序改写致站位调度生硬（GS-03d 功能保留）；D5 ch6「这才确准」与 ch3 矛盾；D6 受孕时点自认超可知；
  D7「正经人家」公开否认元素未复现。D1 是否走 owner patch 修订待用户裁决，改文后 CS-F5 受影响结论重验。
- **工程余项**：同身份强制重采、一般 REDO 级联政策仍属既有产品裁决边界，只闭合已证明的消费链，不扩普通队列权限；
  调并发前先看真实 429／unknown。
- **换算链 wire 的 owner patch 绑定档缺口（CS-F4 遗留，2026-10-05）**：v5 章 wire 系仓外换算产物，其请求身份在任何根都
  无已购行，`bind_wire_adoption` 的 owner_patch 档（要求同身份已购基线）不可达，手工补丁只能 unverified 采用（CS-F4 的
  ch03 即此例；跨身份漂移后的手工 wire 同类，Design 已购 wire 的采用亦然）。是否为「已裁决手工修订」建正式登记通道
  （如 batch-documented 补丁臂＋换算清单基线）待用户裁决；未裁决前 unverified 如实留痕、不计全自动。
- **token 量级表**：新量级由账与补证逐节点重建（旧 B0／F5e／F9 量级仅作诊断参照，非现役报价，见 `e84a169b` 历史表）；
  异常先回查输入和协议；JSON 字符下降只证明 wire 瘦身，未证明整书费用下降；已退役 bind 不再作为预算项。

### CS-S · Stage2 观察面收敛（2026-10-07 立项）

背景：当日跑书产物审阅暴露四类问题——字段长尾与死字段、区间与证据错位靠人工 owner patch
收口、names 变体字面靠模型通读全书发现、`initial_state.knowledge` 含义含混。路线＝把观察面
收敛到「观察、判断、消歧、编译输入」，不是第三套故事事实（S2-8）。**验收方式（用户同日
指令）**：不执行原方案的度量装置与付费 A/B 臂（0b／0c／1d 不做）；1a–1c、Phase 2、Phase 3
完成后由用户手动经 CLI 逐节点跑书验收，自行判断首答通过率、token 消耗与产物质量。
工程验证全程零付费（fake transport＋免费测试）；任何付费跑书由用户自跑，不走本路线预算。

CS-S 全部批次（1a–1c、Phase 2、Phase 3）已落地销账；验证分母与命令见各批提交说明。
Phase 4（devices A/B）未立项，不随本路线执行。

## 待删除的兼容层与删除条件

过渡例外必须在此登记删除条件；新增兼容层先取得用户裁决。

| 对象 | 有效义务与删除条件 |
|---|---|
| rc22→rc23 同根升级登记 | `contracts/contract_upgrades.py::HOOK_UPGRADE` 只接受冻结的精确身份边。旧 Hook 原字节归档后重建，不提供语义字段 adapter；正文按当前章包局部核对，账与 unknown 不改。所有 rc22 存量根已升级或只读归档后删除该历史身份边；通用计划／CAS／恢复机制保留。未登记版本继续阻断。2026-10-10 裁决追加：①旧升级返还调用适配——`refund_nodes` 仅限定该产物升级的同步范围，2026-10-11 起所有 LLM 节点正式升版共用 V3 节点版本重置；旧 `UpgradeRoundRefund` 只读保存，不能重复恢复同版；②免费换算登记面——`novel/orchestrator/upgrade_converters.py` 只收无损纯函数边，换出 wire 走 `--wire-dir` 免费采用；hook-rc22-rc23 因 emphasis/transition/reader_expectation/local_focus 语义缺口不登记，删除条件＝该升级边从 REGISTERED_UPGRADES 退役时一并删除；③hook_design 请求面派生标注（later_writes/name_exposure）与结构失败时的材料预检聚合（rc23 重跑 6/6 耗尽的反例驱动），目标是机械判据不消耗付费轮次 |
| rc12／rc13 ExecutionPlan adapter | rc21 质量批删除已废止的五类旧质量产物迁移，仅 `novel/store.py::_upgrade_rc12_payload` 对形状未变的 ExecutionPlan 保留 rc12／rc13→rc14 无损适配。checkpoint／Brief／Binding 保持 rc22；BodyDesign／PresentationGraph／WriterBookIR／HookContext 须显式 rc25（身份引用与坐标表）；旧版或缺版本拒读，不补造细节、概括、Hook 组织或阅读期待。read_graph_hook 先验证当前图版本再摘组件，不绕版本读取旧图。存量 ExecutionPlan 全部重建或退役后删除此 adapter，禁止无限新增版本 |
| 工作单元账 V2 读迁移／旧契约返还适配 | `contracts/schema/workflow.py::work_unit_ledger_of_payload` 只无损读 V2→V3，不猜历史节点版本；首次显式同步恢复基础额度并建立版本基线。所有存量 V2 账升级或只读归档后删除 V2 读迁移；`novel/work_units.py::WorkUnitBudget.refund_for_upgrade` 只复读旧返还并调用统一版本同步，REGISTERED_UPGRADES 旧边退役后删除适配入口，历史账仍只读保留。 |
| Design v9/v10 已购候选只读解码 | `novel/stages/stage4/slim.py::design_wire_model` 集中读取历史形状，仅供诊断与原始证据展示；当前生成、采用和修订必须是 v11，并经完整身份声明预检。旧字节不能靠改 protocol 或包入普通义片段机械升级。所有历史候选已归档且不再诊断后删除 v9/v10 schema 与分支。 |
| OwnerIssue v1／v2 读兼容 | v3 显式 dispatch_pending、指纹历史与更正记录；只经 `contracts/schema/workflow.py::owner_issue_record_of_payload` 集中解码；所有存量 v1／v2 根只读归档／不再续跑后删除。旧错误路由账的更正按 RC-15 显式恢复，不重置轮数 |
| OwnerRevisionRecord V1 读兼容 | V2 增必填 `applies_to_request_sha256` 与 kind `carried_forward`（CS-H H1b，RC-54/56：免费沿用的采用凭证）；只经 `contracts/schema/workflow.py::owner_revision_record_of_payload` 集中解码（v1 的应用身份即其 `baseline_request_sha256`——V1 只可能是 owner_patch／resample）。现存根盘上不再存在 `schema_version=1` 的修订记录（只读归档／不再续跑）后删除 V1 读兼容；未知版本显式拒绝，不向领域 schema 塞默认值 |
| names／Characters旧信封 | names v2与两类信封v2显式拒识旧版；须新协议names重建后compile重建Characters，不推断空resolutions。原冻结根只读，授权新epoch再重建；无隐式adapter |
| 历史 Step 缺请求配置 | Step 的 CS-T 加法契约集中在 `contracts/schema/orchestration.py::Step`，读取缺字段为 None；展示为未知，不从默认配置推断旧请求。新执行和 followcheck 必须有同源配置。旧 JobStart 只读归档不再消费后删除缺字段兼容 |
| Golden评价v1 | v2显式拒识旧manifest／review／snapshot／result；按冻结六维重新收集、重评现有成书，不自动迁移旧结论、不要求为评价升级重购 |
| 候选身份冻结 | CS-R补齐信封版本与operator_redo；names／Characters信封v2、Hook有限owner修订改变候选身份。新身份由`tests/test_str06_f7e_freeze.py`冻结，旧根不追改；旧身份按不兼容只读拒识 |
| 字符串判据身份 | `novel/core/validation.py::validation_identity` 集中解码仍现役字符串判据；材料、章内悬念冲突与关联问题已用结构化身份。剩余判据全部提供稳定身份后删除字符串前缀兼容，不在各节点扩散解析 |
| 旧 associate 误派更正 | `novel/stages/issue_recovery.py::_legacy_loop_correction` 只从完整正式章观察重建历史重复 close 错误，证明旧问题全部覆盖后才显式 superseded；不解析文案猜 owner、不退费、不重置轮数。旧字符串 association_undecided 账全部只读归档且不再恢复后删除该兼容分支 |
| 已退役链 | 拒识legacy_incompatible，RETIRED_ARTIFACT_PATHS和盘面探测维持闭合；守卫`tests/test_retired_surface_scan.py`，不复活历史解码面。三批退役名册统一登记在`tests/test_retired_surface_scan.py::CLEANUP_2026_10_04_MODULES`，回来即红 |
| basis wire 引文面（≤v6／names ≤v4 wire） | 2026-10-07 指针化（用户裁决「模型只选段、代码切原文」）＋同日二次收口（wire 词汇 `evidence_refs`、book/chapters wire v7、names wire v5）：旧 wire 回答（逐字引文、或 v6/v4 段 id 词汇）按协议字面量关闭式拒收、不做推断采用（prompt 变化本就换请求身份）；`locate_quote`/`canonical_quote`/`content_equivalent_slice`/`norm_index` 已随批删除。当批 canonical 三者为 v4/v4/v3；CS-S 完成后只接受现役 v7/v7/v4，旧字面量关闭式拒收。canonical 直通读面（`{segment,quote}` 形 basis）作为 S-01 登记过的兼容层保留，删除条件＝canonical 自身证据面瘦身时与 slim 适配层一并退役（见 `novel/stages/stage2/slim.py` 模块头） |

### 候选根运行记录映射与删除条件

B1 迁移已完成，保留以下恢复索引：旧 request 一体记录由 SemanticRequest＋ProviderAttempt 取代，validation 由
AcceptanceRecord 取代，adopt 由 ContentRevision＋CommitBundle 取代，body／hook attempts 由 ContentRevision＋ProviderAttempt
取代。旧 admission 平行计数已退出，额度由现役工作单元、整书总闸和 ledger 承担，unknown 也占额。旧文件仅留原根只读证据；
不恢复旧写路、不双写、不补造旧 runtime/request 文件。完整映射见重置前 `e84a169b` 本文件。

## 身份引用文本（2026-10-17）

用户授权按 Stage4 阻塞链做通用修复：identity-text-v1，Names wire v7/canonical v5，Preserve v6/policy11、Design v11、Hook Design v4；正文协议 v12、renderer v13。工程回归与用户节点实跑分别验收，不代签标注语义或文学质量。

待验收：用户在原失败根升级后重跑 body_design，核对本节点新首答/返修、完整材料预检与后续身份改名；旧已购答费用和未接受状态保留，不把免费检查当成新首过。

| 兼容面 | 删除条件 |
|---|---|
| NamesObservation v4 | `contracts/schema/observation.py::NamesObservation` 只读保留 v4 身份和原序列化形状，不声称源称谓完整；新 v5 必须显式提供补充称谓证据。所有 v4 存量当前根已重建或归档后删除读适配。 |
| Preserve policy≤10 checkpoint | 仅保留原有正式 checkpoint 作为源材料；不把未标记源文字机械包装成普通义声明，不声明其具备新提及证明。当前 producer 只产 policy11。现役根全部重建 Preserve 或归档后删除旧输入口径。 |
| rc24→rc25 同根升级 | 精确登记身份边，归档需重建的 Design、IR、Hook、正文与交付；保留原始源、Preserve、已购答、原成本与历史轮。无损转换不能补出新提及语义。所有 rc24 当前根升级或归档后删除该边。 |

## Stage4 契约驱动返修计划（2026-10-17）

用户要求按五点方案修复 `body_design` 的非法拍级 `reads` 死锁：逐项形状诊断、契约决定
结构可修范围、owner声明语义边界、范围与依赖切片同源、完整预检和无进展纪律闭合。
正式 artifact／wire 字段不变，继续完整对象替换；程序不搬移读取语义，不改历史已购
响应、产物、成本账、采用身份或累计额度。
Design派生返修切片统一v4并显式携带基底`wire_protocol_version`，不机械升级旧候选或返还额度。

工程验证：基于 `7cea2f431a00f57a331d6eefd8d6aa631b31db05` 的当前工作树，使用
`novel_refactor` 环境串行运行定向 unit/contract、stage 与恢复回归。最终子 schema
校验版本的定向测试 **651 passed，0 failed/error/skipped**；真实 CLI 子进程修订用例
**1 passed，0 failed/error/skipped**。JUnit 分母分别见
`.test_cache/repair-schema-final.xml` 与 `.test_cache/repair-schema-cli.xml`。
覆盖契约 §9.5 五文件、依赖层级、返修计划、动态 schema、非法字段整场替换、依赖切片、
完整预检、双产物发布和免费恢复；CLI 用例继续验证下游交付与零新增调用重跑。
按用户要求将身份引用文本与本批返修一并提交 main，追加默认非 corpus 回归。
首轮 4435 项中 4426 通过、9 失败：补齐 Hook 完整语义记录边界，修正测试好稿
越界删除合法对照字段，并精确登记纯返修原语的架构守卫。原失败断言保留，新增
边界和越界留账反例；定向复验 **208 passed**。最终默认回归 **4444 passed，
0 failed/error/skipped**，JUnit 为 `.test_cache/identity-repair-final.xml`。
未运行 corpus 或 golden set，不以工程回归代签真实模型语义或成书质量。

复现命令（在文件系统沙箱外运行）：

```powershell
& 'D:/Miniconda/envs/novel_refactor/python.exe' -B -m pytest -n 0 -x tests/test_repair_plan.py tests/test_str06_item_patch.py tests/test_str06_patch_routes.py tests/test_str06_design_slim.py tests/test_str06_design_patch.py tests/test_str06_hook.py tests/test_str06_candidate_preflight.py tests/test_docs_references.py tests/test_coverage.py tests/test_rules_registry.py tests/test_hard_counterexamples.py tests/test_ingest_fixtures.py tests/test_layering.py tests/test_test_selection.py::test_catalog_covers_every_module_and_every_override_is_a_real_case --basetemp=E:/_tr -o cache_dir=.test_cache --junitxml=.test_cache/repair-schema-final.xml
& 'D:/Miniconda/envs/novel_refactor/python.exe' -B -m pytest -n 0 -x 'tests/test_str06_product_entry.py::test_obligation_unbound_replans_across_single_node_subprocesses[obligation_unbound]' --basetemp=E:/_tr -o cache_dir=.test_cache --junitxml=.test_cache/repair-schema-cli.xml
& 'D:/Miniconda/envs/novel_refactor/python.exe' -X utf8 -B -m pytest -n 4 --dist loadgroup -m 'not corpus' -v --tb=short --basetemp=E:/_tr -o cache_dir=.test_cache --junitxml=.test_cache/identity-repair-final.xml
```

待实跑验收：按用户授权重跑原 `run_20261017_夫君不行_但我有孕啦` 根的 `stage4.body_design`，
核对实际返修目标能删除非法键、整场依赖完整、读取语义裁决与合并后的完整预检及双产物
发布。工程测试使用fake transport且零真实付费，不代替真实模型语义或成书质量验收。

## 目标命名前置与普通正文主链（2026-10-18 立项）

执行文档见 [specs/target_naming_mainline.md](specs/target_naming_mainline.md)。目标：唯一主链改为
「源书抽取 → 确定目标人物名字 → 目标故事设计 → Stage5 直接生成最终名字的普通正文 → 核对与装配」，
删除末端改名所派生的全量文本标注负担。已定产品规则（用户裁决）：普通名字默认可用，不再要求所有
人物逐场声明名称释放；仅对有源剧情依据的隐藏身份、化名归属和揭名时点设限；正文中的疑似旧名、
疑似提前揭名只形成定位报告，不阻断流水线、不自动触发付费返修；结构、引用、产物完整性与来源绑定
等确定错误仍阻断并提供恢复路径；不新增付费 Judge、名称清洗节点或人工逐阶段审批；接受命名前置
代价——目标名字修改后相关目标材料与正文重建，不再提供末端只替换姓名能力。范围边界：
Stage0～Stage2 源 schema 与正式字节不变；`str06.stage4.naming` 前移（唯一正式产物
`stage4/naming/name_map.json`，置于 Stage2 compile 之后、Preserve 之前），`str06.stage7.naming`
退役，节点总数保持 13、不增加付费语义节点；统一升版 `str06-rc26`，禁止旧目标稿静默采用。

| 批次 | 内容 | 状态 |
|---|---|---|
| A 冻结新契约 | 命名节点前移登记（str06.stage4.naming＋stage4/naming/name_map.json＋NameMap v6）、模块迁 stage4 包、版式头升 rc26、登记 naming-forward-rc25-rc26 同根升级边（含升级器 preserve checkpoint 归档缺口修复）、判据/计数/冻结基线重钉 | **已完成**（2026-10-18） |
| B 打通目标材料 | 命名前移；Preserve 生成目标表述；关闭源自由文本直接流入 Design、Binding、正文及 Hook 的入口 | **已完成**（2026-10-18） |
| C 切换正文与装配 | 删 identity-text 协议、候选覆盖硬闸、两类区间回执及末端替换；修订普通正文回执与交付身份 | 未开始 |
| D 恢复与公开读面 | 闭合存量升级、历史账读取、预算、CLI、API 和监控页 | **已完成**（2026-10-18） |
| E 验收与删除旧路径 | 完成工程验证及真实正文验收，删除失去消费者的代码和测试 | 未开始 |

批次 B 工程验证（2026-10-18，fake transport、零付费）：Preserve 正式读取
`stage4/naming/name_map.json`（来源账边记账，名字一变 checkpoint／Brief 及下游
经新鲜度失效）；PRESERVE_RULES 升 policy 12／wire v7，prompt 带「目标名称表」
确定性投影；checkpoint 增 `name_map_sha256` 绑定；Brief／Binding 升 rc26；
IdentityProjection 增 `target_name`（Design 输入用目标姓名）；policy 12 硬闸按
义务/细节条目定位源旧字面与身份文本标记残片（进 preserve 同节点修订环）；
run_body_design 读取序与 Preserve 一致（来源账边顺序稳定，同身份重放字节一致）；
契约五文件与默认非 corpus 回归 **4446 passed，0 failed/error/skipped**。
中间态说明：写手材料（IR 释放面／合法身份引用）仍按源登记面，目标姓名以 Brief
直书文本到达写手包——切目标面属批次 C。

批次 D 工程验证（2026-10-18，fake transport、零付费）：rc25→rc26 同根升级边
（naming-forward-rc25-rc26，随 B 重钉 target）可用；新增
`novel/work_units.py::historical_unit_ledger` 集中历史账只读入口——退役的旧
`str06.stage7.naming` 工作单元照读、禁执行/授权/改历史身份，升级返还范围按
节点过滤不再被退役单元卡死；监控页「目标人物命名」面板前移到故事设计之前；
CLI through 映射与 API 命名路由读新产物（批次 A 已闭合）。默认非 corpus
回归 **4447 passed，0 failed/error/skipped**；前端 typecheck／124 测试／
seams／build 全绿。

批次 A 工程验证（2026-10-18，fake transport、零付费）：契约五文件（docs_references／
coverage／rules_registry／hard_counterexamples／ingest_fixtures）与 naming／store／
views／api／cli／orchestrator／corpus／work_units／beacon／product_entry／f7e_freeze
等 40 个受影响文件全绿；冻结契约身份随节点前移重钉为
`7f6cfc22efdae5de37718d6226241becb6623376bef68af081e08b38f0298d75`（13 节点／10 付费，
Stage7 腿只剩 assemble）；旧 `stage7/naming/name_map.json` 进
`RETIRED_ARTIFACT_PATHS` 拒读。前端夹具同步新节点 id；typecheck/test/seams 随批验证。

两个已确认工程缺口（随批次 D 闭合）：① `novel/orchestrator/contract_upgrade.py` 的 `_files`
无条件保留 `runtime/preserve_checkpoint.json`，须改为服从归档集合，防止同一文件同时进入保留和
归档清单；② 旧 naming 节点退出后其历史工作单元仍须可读——设集中的历史账只读入口，禁止旧节点
继续执行、追加授权或修改历史身份，旧成本继续计入整书账（I-17）。

本书实际验收（工程验证完成后由用户手动跑书，未授权不发付费请求）：以《夫君不行，但我有孕啦》
为验收书，免费预览并执行契约升级（确认 Stage2 保留、旧目标产物归档、历史账完整）→ 沿新主链
生成目标命名、目标设计、Hook 和全部正文 → Stage5 实际文本阅读记录（人物混淆、旧名残留、提前
揭示、因果断裂、重复、文风）→ Stage7 装配核对成书与正文一致 → 分列报告工程正确性、阅读质量、
首答接受、返修原因、调用量与成本，失败与未知用量均保留。未过时区分真实内容问题、结构错误和
工程缺口，不以加额度、过滤失败记录或新增未经验证硬闸代替诊断。

| 兼容面 | 删除条件 |
|---|---|
| rc25→rc26 同根升级登记 | `contracts/contract_upgrades.py` 新增精确身份边：保留 Stage0～Stage2、原始证据、已购响应、历史轮次与成本账；归档旧 Preserve checkpoint、Brief 及全部 Stage4～Stage7 目标产物；从新目标命名节点重建；不恢复内容额度，不用移动文件／删除标记／重新序列化把旧语义稿转成新稿。所有 rc25 当前根升级或只读归档后删除该边；通用计划／CAS／中断恢复机制保留，未登记版本继续阻断。 |
| 旧 Stage7 naming 历史账只读入口 | 集中只读解码旧 naming 工作单元与 ledger；禁止执行、追加授权或修改历史身份；旧成本计入整书账。所有存量根只读归档且不再续跑后删除该入口，不扩展为通用旧链兼容层（S-03）。 |
| identity-text-v1 及两类区间回执旧协议测试 | 随批次 C 退出维护；删除依据＝普通正文回执新职责反例覆盖同一行为面。保留仍有效的职责反例（结构、引用、完整性、来源绑定）与现役判据红例，不删反例提速（I-50～I-52）。 |
| Preserve policy ≤11 候选覆盖闸 | 随 identity-text 退役：旧口径（`name_text_unclassified` 候选覆盖、标记声明）只服务 v6 wire 历史回答，现役 wire v7 拒绝旧协议字面量；历史 checkpoint 已由 rc25→rc26 升级归档，不再进现役接受环。存量根全部升级或只读归档后删除 `_legacy_preserve_name_issues` 残留与相关测试登记。 |
| 旧 Stage7 naming 历史账只读入口 | 集中只读解码旧 naming 工作单元与 ledger；禁止执行、追加授权或修改历史身份；旧成本计入整书账。所有存量根只读归档且不再续跑后删除该入口，不扩展为通用旧链兼容层（S-03）。 |
