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

## 未完成项

- **CS-A 证据寻址（RC-26～29，rc15）**：另两本《三人成行》《夫君不行但我有孕啦》在 rc15 根只完成 book 与章观察（17／22 章），
  names／associate／compile 未运行（需付费授权）；完整首答通过率等下一本新书实测。
- **CS-2 后续**：book 观察仍同时使用全文排版与证据地址，不能未经语义充分性证明删除；basis 已指针化
  （2026-10-07：wire 只给段 id、引文＝段全文由代码切出，转写失败类错误构造上消失），Preserve 输入／wire 瘦身
  仍有余项（Preserve 必要事实补全与 Design Brief 投影已闭合；补齐语义使 Preserve 体积增加，
  不能记作降费成功）。
- **R7 质量优化**：持续登记，按实际阅读反例启动：漏保／过保、因果、承接、声线、导演说明、重复与输入不足。风格画像仅在
  一句一段、环境密度、幽默通道、POV 漂移等既有批评复现且有阅读证据时提出具体方案；under-spec、过度失效、状态过建模、
  checklist prose、章内披露及表达力未验证继续登记；第一人称书不能代签第三人称稳定性。
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

| 批 | 内容 | 状态 |
|---|---|---|
| 1a | book／chapters wire v8：`src`→`start`（单段 id，区间由代码派生：本拍 start 到下一拍 start−1，末拍到章末）、`who`→`on_stage`、SlimEffect 删 `scope`/`hidden`/`prop`；Book wire 删 `InitialFactWire.knowledge`；oracle＝今日两本 L1 观察以每拍首段作 start 重新派生区间 378/383 逐字一致 | 待做 |
| 1b | L1／L2 死字段收口：`InitialFact.knowledge`、`ObsBeat.effects[].{disclosure,proposition,scope}`、`InitialStateAtom.knowledge`、`KnowledgeSpec`、`Disclosure` 删；book／chapters canonical v5、Characters V5／Blueprints V16、rc18；`R-2.14` 改读时按登记表派生；Stage4 grounding 知情面收口；死面守卫扩 `tests/test_retired_surface_scan.py` | 待做 |
| 1c | names v6（A＋B＋C）：C＝wire 删 `surface_forms`，构词模板由 compiler 按 family／given／aliases 字面派生（`form_overrides` 例外）；B＝结构答案后代码生成变体候选（锚＝≥3 字含姓名部件 surface、同首字编辑距离 1），有候选才发新 owner `name_variants` 窄调用（`stage2-name-variants-v1`，逐条 same／unresolved／not_name）；A＝S2-10 反例守卫（增删拆合拒、same 仅证据段内局部替换、未决阻断发布） | 待做 |
| Phase 2 | Book 增 `facts:[{statement, evidence_refs, known_at_start}]`（F 编号代码派发）＋chapters `know.fact` 引用；近义重复只报 candidate（确定性相似度），Associate 退为条目级窄 resolver（未决候选＋最小文本）；wire／L1 升版、rc19。默认裁决：D-3 不上 `address`（无消费者）；D-5 删 `public` 持有者 | 待做 |
| Phase 3 | closed change keys（从 ≥3 本书真实键聚类定稿）＋`state→change`、`ev→event`；拍级 `access`（获知通道枚举按真实分布冻结）；reader 知情由 compile 派生、`know.who` 禁 reader；`same_state_dimension` 消失（associate 协议升版）；免费 oracle＝rc15 冻结根 25 条模型自写 reader 条目对比派生结果 | 待做 |

批完成即撤行（验证分母与命令写提交说明）。Phase 4（devices A/B）未立项，不随本路线执行。

## 待删除的兼容层与删除条件

过渡例外必须在此登记删除条件；新增兼容层先取得用户裁决。

| 对象 | 有效义务与删除条件 |
|---|---|
| associate 整书投影（S2-9 过渡例外） | 现役 associate 仍整书投影并生产 `cross_chapter_cause`／`scene_continuation`。删除条件＝CS-S Phase 2 落地：输入面改为「代码给出的未决候选＋最小文本」（近义命题对并入 facts 候选；悬念配对候选＝跨章 open×close；`cross_chapter_cause` 按 D-2 A 保留为独立窄任务——候选＝loop 配对＋fact 流给出的拍对，模型只选 kind 或否决；续场候选按相邻章 seam 投影）；无候选＝零调用。落地并验证后删除本行 |
|---|---|
| rc12／rc13 五类产物 adapter | `novel/store.py::_upgrade_rc12_payload` 只迁移白名单无损信息（rc17 起 brief／writer_book_ir 的 rc12／rc13 载荷迁移目标直接钉 rc17；盘上 rc14～rc16 载荷不在白名单内，随模型字面量按契约表漂移拒读——`ChapterIR.display_prefix` 语义已变，不能当缺省字段读入；旧根续跑同样按候选契约表拒收）；旧 Brief 缺认知模式免费重放 Preserve，旧 Design 缺 confirms 走显式 owner patch。存量全部重建或退役后删除此 adapter 及 Str06Repository.read_graph_hook 的旧组件迁移，禁止无限新增版本 |
| OwnerIssue v1／v2 读兼容 | v3 显式 dispatch_pending、指纹历史与更正记录；只经 `contracts/schema/workflow.py::owner_issue_record_of_payload` 集中解码；所有存量 v1／v2 根只读归档／不再续跑后删除。旧错误路由账的更正按 RC-15 显式恢复，不重置轮数 |
| OwnerRevisionRecord V1 读兼容 | V2 增必填 `applies_to_request_sha256` 与 kind `carried_forward`（CS-H H1b，RC-54/56：免费沿用的采用凭证）；只经 `contracts/schema/workflow.py::owner_revision_record_of_payload` 集中解码（v1 的应用身份即其 `baseline_request_sha256`——V1 只可能是 owner_patch／resample）。现存根盘上不再存在 `schema_version=1` 的修订记录（只读归档／不再续跑）后删除 V1 读兼容；未知版本显式拒绝，不向领域 schema 塞默认值 |
| names／Characters旧信封 | names v2与两类信封v2显式拒识旧版；须新协议names重建后compile重建Characters，不推断空resolutions。原冻结根只读，授权新epoch再重建；无隐式adapter |
| 历史 Step 缺请求配置 | Step 的 CS-T 加法契约集中在 `contracts/schema/orchestration.py::Step`，读取缺字段为 None；展示为未知，不从默认配置推断旧请求。新执行和 followcheck 必须有同源配置。旧 JobStart 只读归档不再消费后删除缺字段兼容 |
| Golden评价v1 | v2显式拒识旧manifest／review／snapshot／result；按冻结六维重新收集、重评现有成书，不自动迁移旧结论、不要求为评价升级重购 |
| 候选身份冻结 | CS-R补齐信封版本与operator_redo；names／Characters信封v2、Hook有限owner修订改变候选身份。新身份由`tests/test_str06_f7e_freeze.py`冻结，旧根不追改；旧身份按不兼容只读拒识 |
| 字符串判据身份 | `novel/core/validation.py::validation_identity` 集中解码仍现役字符串判据；材料、章内悬念冲突与关联问题已用结构化身份。剩余判据全部提供稳定身份后删除字符串前缀兼容，不在各节点扩散解析 |
| 旧 associate 误派更正 | `novel/stages/issue_recovery.py::_legacy_loop_correction` 只从完整正式章观察重建历史重复 close 错误，证明旧问题全部覆盖后才显式 superseded；不解析文案猜 owner、不退费、不重置轮数。旧字符串 association_undecided 账全部只读归档且不再恢复后删除该兼容分支 |
| 已退役链 | 拒识legacy_incompatible，RETIRED_ARTIFACT_PATHS和盘面探测维持闭合；守卫`tests/test_retired_surface_scan.py`，不复活历史解码面。三批退役名册统一登记在`tests/test_retired_surface_scan.py::CLEANUP_2026_10_04_MODULES`，回来即红 |
| basis wire 引文面（≤v6／names ≤v4 wire） | 2026-10-07 指针化（用户裁决「模型只选段、代码切原文」）＋同日二次收口（wire 词汇 `evidence_refs`、book/chapters wire v7、names wire v5）：旧 wire 回答（逐字引文、或 v6/v4 段 id 词汇）按协议字面量关闭式拒收、不做推断采用（prompt 变化本就换请求身份）；`locate_quote`/`canonical_quote`/`content_equivalent_slice`/`norm_index` 已随批删除。canonical 三者（v4/v4/v3）不动照读——canonical 直通读面（`{segment,quote}` 形 basis）作为 S-01 登记过的兼容层保留，删除条件＝canonical 自身瘦身升版时与 slim 适配层一并退役（见 `novel/stages/stage2/slim.py` 模块头） |

### 候选根运行记录映射与删除条件

B1 迁移已完成，保留以下恢复索引：旧 request 一体记录由 SemanticRequest＋ProviderAttempt 取代，validation 由
AcceptanceRecord 取代，adopt 由 ContentRevision＋CommitBundle 取代，body／hook attempts 由 ContentRevision＋ProviderAttempt
取代。旧 admission 平行计数已退出，额度由现役工作单元、整书总闸和 ledger 承担，unknown 也占额。旧文件仅留原根只读证据；
不恢复旧写路、不双写、不补造旧 runtime/request 文件。完整映射见重置前 `e84a169b` 本文件。
