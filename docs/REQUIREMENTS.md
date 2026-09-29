# STR-06 · Stage2–Stage7 终态架构迁移（Stage6 退役）

本文件是唯一活动计划，登记当前进度、下一批任务、验收、恢复与删除条件。目标职责、
真源和产物接口以 [str06.md](schema/str06.md) 为准；现役行为、根不变量与局部指南仍按
[AGENTS.md](../AGENTS.md) 读取，历史理由见 [DECISIONS.md](DECISIONS.md)。
2026-09-23 活动账重置为 STR-06 唯一主线（STR-01～05 退出活动面）；重置与迁移期的
历史原文按 Git 历史读取（`git log -- docs/REQUIREMENTS.md`），不恢复为操作说明。

## 目标与范围

Stage2 负责源语义；Stage4 负责目标故事、Body/Hook 呈现设计与确定性 WriterIR；Stage5
负责正文逐章独立并发实现和单篇 Hook；Stage7 负责 Naming、机械 ApplyNames 与 Assembly。
四类故事语义真源保持 Characters／Blueprint／TargetDesign／SemanticBinding；NameMap
是交付变换契约，接受正文、回执与已购证据独立保护。Stage6 整层退役，不重排阶段号。

职责、权威源、单向依赖、窄修复和失败归属已冻结；字段级 schema 保持 RC。Hook 的必要
选择必须进入正式 Design 并可重建 Context；源名称结构归 Characters，目标名字归
NameMap。完整边界及目标产物布局只在 str06.md 维护，本文件不复制第二份字段契约。

**本轮终点（2026-09-27 调整，D1）**：职责解耦计划取代 F9 成为收尾路径：新链产出
《夫君的报恩》由用户通读，工程验收与用户验收同时有效后关闭 STR-06（B6-B；合入时点
已按 D11 调整为提前合入 main，验收口径不变）；F5e 成书读数作对照基线。产品范围固定为
短篇小说（中文约 2 万字），永不做长篇；不新增书长预检，沿用现有逐请求容量闸（D7）。
验收采用迁移口径：错章、漏章、改名残留、交付不一致、恢复失败等工程缺陷必须修到通过；
文学质量意见逐条登记为下一轮迭代优化的输入，不阻塞本轮关闭。F5e 的 agent 开发性阅读
不代替用户验收，机械全绿也不改写语义未评状态。

B0–B5 与收尾批全部 fake transport、零模型调用，已按 D11 提前合入 main；付费与正文
外发只在 B6-A／B6-B 按实际报价分别授权。目标架构（Stage2 五节点、章包三件套、执行
设施、提交顺序、精确失效）唯一登记在 [str06.md](schema/str06.md)；现役注册面为
stage0/1 ＋ 14 个 `str06.<stage>.<owner>` 节点（B2 五节点重建，`str06.stage2.source`
退役）。`tests/suite_catalog.json` 的 `change_scopes.str06` 登记选测范围，与实际导入
消费者取并集。

旧书保持 `legacy_incompatible`，独立根重建，不手补旧产物、不继承旧批准、不清 ledger。
新协议改变全部请求身份，历史采用（F7a 通路）在新链上不会命中；旧输出根与已购证据
只读保留。

定向证据闭合即可交付子批次；不因提交、改计划或登记新测试重复全仓。默认非 corpus 回归
按最终改动集合执行；中途仅在命中根指南的完整回归门槛时提前执行，并记录具体原因。
B6 若再改生产代码、契约或测试基础设施，须按最终提交重新完成工程回归。

本轮范围外：

- Stage0/1 未吸收的整层重构延期（Stage0 v3 回归收尾归 F4R、Stage1 输入边界联调归 F7，
  均已完成）。
- current-schema corpus 基线及其全量验收保持独立；Golden 不等于“全量语料绿”。
- 文学质量问题驱动的迭代优化轮，在 STR-06 关闭后另行立项。
- 第二本与 Golden 按 D10 固定为 Workbench 两本短篇（《夫君的报恩》《三人成行》），
  排在 B6；长篇／多线永不做（D7），不再是待验项。

### 撤销与移出

F3b 第二本 artifact-only smoke 撤销，不再作为本轮待办或付费点。第二本表达力、
NameMap v5 在非书1上的真实赋值连同两本 Golden 移交后续 Golden 轮；长篇／多线结构
与 IR／前文容量增长义务经 2026-09-27 D7 裁决删除（产品范围固定短篇，永不做长篇；
旧章包的容量增长读数随 D2 章包形态失去对象）。不重新加回 F7–F9。Golden 的样本
（D10：Workbench 两本短篇）、六维评价、冻结 manifest 与成本要求见“本轮之后”。

## 当前状态与下一步

**当前状态（2026-09-29）：B0–B5 与收尾批已完成并按 D11 提前合入 main；质量重设计
R0–R6、R5、R5b 与语义冻结闸（裁决 D8）全部完成，交付记录见「质量重设计批次」。**

待办：

- **B6-A 语义与写作 canary（付费）**：前置闸（R5b＋语义冻结闸）已过；canary 输出根
  已建立（outputs/str06_b6a_canary_20260929b），按报价与授权推进，完成后登记交付
  记录。
- **B6-B 全书真实验收（付费）**：新链产出《夫君的报恩》供用户通读（D1 口径），与
  B6-A 工程复验共同构成关闭条件。
- **R7 数据驱动优化**：输入为 B6 阅读问题清单与「B6 执行与后续优化次序」的后续优化
  表；含 D10 两本短篇 Golden。

## 质量重设计批次（2026-09-28～09-29；R0–R7）

批次执行序与交付记录在本节，裁决见 [DECISIONS.md](DECISIONS.md)——原临时计划
PLAN.md 已完成使命删除（R0–R6、R5、R5b、语义冻结闸交付后），未竟事项见
「当前状态与下一步」。付费与正文外发只在 B6-A／B6-B 分别授权；R 批全部
fake transport、零模型调用。

- [x] **R0 · 语义送达与送达方式守卫＋故障注入底座（免费）**（2026-09-28 完成）：
  - `contracts/schema/consumption.py` 新增字段级送达登记
    `STR06_FIELD_DELIVERY`（128 项）：送达链 canonical 面（WriterBookIR、章包派生
    RealizationContract、HookContext、NameMapV5）逐叶子字段登记消费者
    （writer_packet/hook_context/naming/validator/audit）、受影响章范围
    （every_chapter/chapter_field/scene_owner/hook_only/naming_only/not_delivered）与
    `delivery_mode` 七档（must_realize/must_respect/continuity/creative_guidance/
    background/guard_only/audit_only）；background 当前无独立字段（R2 编译器补齐投影
    产生），以投影规则注记在 `entry_state[].value`。顺带把六族投影的 source_owners
    从退役节点名（stage2.characters 等）换成 `NODES` 现役名。
  - 守卫 `tests/test_str06_semantic_delivery.py`（51 项）：①canonical 字段未登记即红
    （面走查双向对账）；②变异只动登记范围的章（`project_chapter_packet`＋
    `packet_sha256` 比较面，29 个变异用例覆盖六档身份与全部范围规则）；③变异值只进
    其 delivery_mode 对应送达面（audit_only 材料进章包但不进阅读面；Hook 面同理）；
    ④写手视图无 audit_only 内容（禁用字面清单、lineage、provenance、导航与称谓总表；
    语义文本中的源侧字面属 R2 renderer 目标名解析问题，非本检查对象）。
  - 故障注入 fake 底座 `tests/str06_faults.py`：五类脚本化模型式错误（证据 id 越界、
    未知 slug、漏配对、必保义务未承载、wire 多键）＋带跨章悬念的绿基线 wires；经
    PaidRequestSession＋fake transport 走真实链入口，实测拦截位——book 校验闭集、
    compile 的 participants 闭集与未决拒绝（D8）、stage4 compile 的必保义务覆盖
    Guard（bind 对带 owner 的 unbound 是合法返回）、`_decode_json` 重复键拒绝；
    sitecustomize 注入路径（环境门 `NOVEL_STR06_FAULT`）与
    `tests/test_str06_product_entry.py` 的 B5 注入同形，子进程注入有环境门双向验证。
    本批不接修订逻辑（R3 复用）。
  - 验证：`tests/test_str06_semantic_delivery.py` 51/51；定向同跑 test_str06_contracts、
    test_test_selection、test_docs_references、test_coverage（295/295）与
    test_str06_body、test_str06_hook（引其夹具，43/43）；零真实付费。文档：
    `docs/schema/str06.md` 增「字段级送达登记」节；`tests/suite_catalog.json` 登记
    新模块（三条链级用例标 stage）。

- [x] **R1 · Stage2 关联无损（associate 协议 v2，免费）**（2026-09-28 完成；AUD-01/02
  转绿，反例与修复同批）：
  - `novel/stages/stage2/associate.py`：typed 作用对象显式序列化
    `serialize_target`（人物带 char_id、关系保留主客体方向、world 是显式类型；
    未知类型／缺必要字段／字段与 kind 不匹配／关系自环精确报错，不再拿空 dict
    兜底显示 world——AUD-01）；状态句柄改位置编码
    `ch{nn}.b{拍}.e{效应序}.{key}`，book 初态独立命名空间 `book.e{条目序}.{key}`
    （key 只是载荷不承担唯一性——AUD-02 的句柄碰撞根因）；`AssociationSpace`
    成为**唯一关联登记表**，`add_state` 构建时重复句柄即拒。
  - `novel/stages/stage2/compile.py`：初态与章效应句柄经登记表构造函数定位并
    对表核对（漂移即机械错误，不再第二处重写 occurrence 计数）；`_state_rank`
    改查登记表 typed 字段（E-5 消歧结论：序取自唯一登记表后「静默退化为 fallback
    rank」结构性消失）；`_target_label` 双份复制收敛到 associate 的 `target_label`
    （I-11）。
  - `contracts/schema/observation.py`：`STAGE2_ASSOCIATE_PROTOCOL`＝
    `stage2-associate-v2`（`AssociateObservation.protocol_version` Literal 同步，
    旧字面量关闭式拒读）；book／chapters／names 三类观察请求与字面量逐字不变。
    观察产物 `stage2/observations/associate.json` 形状升 `str06-rc9`；canonical 无
    形状变化不升版（A1.5）。PLAN.md R2.9 的「IR rc9」同批改写为 rc10（rc9 被本批
    占用）。
  - 新反例（先红后绿）：AUD-01 typed 标签到达关联输入；AUD-02 同章两拍不同对象
    同名键句柄独立、只配一方可发布另一方不受影响；句柄数量守恒；登记表重复句柄
    即拒；同对象跨章同维发布、不同对象同键未决、反向关系未决、同章跨拍配对拒、
    协议字面量独立升版。
  - 验证（fake transport、零付费、`-n 0`）：定向 stage2 集＋全链消费面＋外围共
    565 项全绿。文档：stage2.md／str06.md／BACKEND_CONTRACT R-2 行／stage2 AGENTS
    同步。

- [x] **R2 · Stage4 语义送达＋两层 IR（免费；契约批 3bfb7e17＋引擎批同日完成）**
  （AUD-03/04、世界约束通道、承接转绿；fake 全链绿）：
  - 契约批（3bfb7e17）：Design v2（拍上线程 threads／书级 world_rules／章级
    entry；四个静态跨章列表删除）；拍级读取值挂拍；WriterPacket 分区模型；
    ChapterIR.continuity 数据面；回执身份拆分（rc6）；M7/M8 判据；升版
    rc10／renderer v5／写手协议 v5。
  - 引擎批：`compile.py` 依赖边改由**实际解析来源**生成（`SceneReadViews.
    read_anchors`＋参与者背景来源场；`last_writer` 扫描删除）；`fold.py` 新增
    **独立 ThreadFold**（共用 guard.scene_order，与 M8 基准逐场一致）；
    `project_context` 按参与者机械补齐入场基线进 `background_state`／
    `background_knowledge`；`grounding.project_brief` 把 Blueprint 的
    world_constraints 机械填入 `book_constraints`（恒空通道打通）；renderer
    v5＝WriterPacket 六分区渲染（写手视图清零内部 ID／字段名／char slug／
    禁用字面；R0 守卫全绿）；Hook 分层渲染＋线程收束机械并入禁区（E-3 读法 b）；
    compile 写 `stage4/acceptance/packet_metrics.json`（E-6；25 份产物）。
  - `planned_delta`（扁平效应副本）与 `writer_brief.usable_surfaces`（冗余称谓
    面）改登记 audit_only——同一语义分别经 beats[].deltas 与 released_surfaces
    逐拍/按场送达（避免同值双区）；`_render_fact`／知识条目去 key= 机器位。
  - 新反例（先红后绿，tests/test_str06_r2_engine 21 项）：ThreadFold 与 M8
    基准逐场一致；假边/漏边/绿对照三例；背景补齐三域与声明读取排重；
    book_constraints；六分区渲染与分区归属；内部 ID 清零；承接自然措辞；
    禁区自然时点；Hook 禁区并入；指标逐章读数与真实链落盘。
  - 收绿批（R2 完成清单逐项复核）：AUD-03/04 反例**正身**落测——前章建立、
    后章拍级读取、当前场不重写：值挂拍进 IR 与「不可违背」分区，变异前章值
    只动建立章与读取章（test_str06_chapter_closure）；Design 级线程增／改
    （open 文本／advance 进展）／删（收束）经真实编译链按消费面传导；ThreadFold
    补「开启前」入场快照断言。复核无遗留红：str06 全集＋§9.5 五文件＋layering
    936 项全绿。

- [x] **R3 · 有界自愈：typed issue 路由与跨节点回派（免费）**（2026-09-28 完成；
  核心批 bba84849＋监控面批 55055491）：每单元最多 2 轮、同一问题集重复即停，只处理
  typed、可定位的机械问题，文学质量永不进入自动修复（裁决①）。落点
  `novel/stages/owner_revision.py`（修订轮与跨节点回派）、`contracts/invariants.py` 的
  `OPERATOR_REDO_STRATEGIES`（增 `owner_revision` 回派策略）、`novel/views/str06.py`
  修订轮回派账投影与监控页并排；反例与故障注入入口 `tests/test_str06_r3_selfheal.py`
  （R5 把 R3 故障注入钉进真实子进程产品链）。

- [x] **R4 · 漂移收口与文档、阅读验收协议登记（免费）**（2026-09-28 完成；收口批
  883cd0da＋登记批 ce84bd01）：`run_chapters` 复用 `run_body` 同槽同池并发、单章失败
  隔离；`str06.stage5.body` 生命周期改章包输入与集合恢复语义
  （`committed_set_recovery`）；文档入册（`docs/schema/str06.md` 两层 IR／线程／承接／
  自愈／发布、BACKEND_CONTRACT R-5.14）；**人工阅读验收协议正式登记进本文件「B6 执行
  与后续优化次序」节**（唯一登记面，B6-A 与 B6-B 共用）；PLAN.md 全部裁决由本批写入
  `docs/DECISIONS.md`。

- [x] **R6 · 统一发布权威（免费；seal 验过才发布）**（2026-09-29 完成；AUD-05
  转绿，反例与修复同批）：
  - `novel/store.py::Str06Repository` 新增**输出组发布** `publish`＋`PublishItem`：
    铸账渲染全部字节后才落盘，组内产物逐字回读后写一个组 seal（`CommitBundleV1`
    不变形状，`outputs` 覆盖全组、`artifact` 保持主产物键）；输出组＝
    `NODES.produces` 同 writer 集合，组内跨 writer 即拒。`write` 收敛为单项组；
    **免费产物同样封存**（免费 bundle `adoptions` 为空），不再有付费/免费两套发布
    语义。落盘纪律抽 `_atomic_write` 共用（OS 原子替换）。
  - `read_model` 对正式输入校验 seal 与摘要：新增 `UnsealedArtifact`（显式终态
    2，携带免费恢复入口）——无覆盖当前字节的封存、seal 损坏或摘要漂移都拒绝正式
    消费；诊断 `inspect` 不受限。编排侧候选桥 `_candidate_body_design` 对未封存按
    未知章集处理（fail-closed）。
  - `run_compile_stage4` 改**验过才发布**：机械 Guard 问题非空时四个正式位一概
    不落盘，问题清单只进 guard report 诊断；`run_hook`／`_write_and_commit_chapter`／
    `run_assemble` 的正文＋回执（manifest）改同组发布，正文字节进组 seal 的输出摘要。
  - 反例（先红后绿，`tests/test_str06_runtime_records.py`）：①免费产物封存；
    ②AUD-05——fake 全链交付后删全部 CommitBundle，单跑 Stage7 组装必须拒绝，
    重跑全链零新购、交付字节不变、重新封存；③Guard 失败不发布正式位。崩溃窗口靠
    重跑免费补尾（付费段请求身份重放、免费段确定性重编译），无补封或迁移层
    （新协议无已购产物，I-13）。
  - 闸门复跑收口（同日）：独立 basetemp 复跑暴露
    `novel/model_slots.py::prepare_model_slot_gate` 的 create-then-write 竞态——
    run_chapters／run_body 逐章线程并发首开总槽时，第二个初始化者把活初始化误判成
    「损坏」。修复：初始化循环序列化进 `capacity.json` 的事务锁（线程＋进程，
    `path_transaction_lock`），等待者只在完整文件出现后核对；崩溃残留的零长度槽文件
    仍按损坏关闭式拒绝。反例见 tests/test_model_slots.py 的
    `test_concurrent_gate_initialization_never_reads_half_written_slot`。

- [x] **R5 · 最终零成本产品回归（免费；B6 前基线）**（2026-09-29 完成）：
  - 契约 §9.5 五文件全绿；R3 故障注入与 R6 发布钉进
    `tests/test_str06_product_entry.py` 的真实子进程产品链（HTTP 上传 →
    Supervisor → 真实 run_step/CLI 子进程 → 交付 → 重启读回）：
    `test_faulted_product_entry_heals_via_revision_and_delivers`（missing_pairing
    ＋fault_once：associate 首响应带故障、修订轮改对——14 笔实付如实多记一笔
    修订轮，全链照常交付，重启读回一致，二次代跑零新购）与
    `test_seal_loss_refuses_formal_consume_then_free_recovery_reseals`
    （交付后删全部 CommitBundle：单跑组装显式终态拒读「未封存」；重跑全部节点
    零新购、交付字节不变、重新封存）。
  - 故障注入底座扩参：`tests/str06_faults.py` 新增 `body_texts`（带内章稿走独立
    JSON 文件，子进程不 import 测试模块）。
  - 收口 R4/R6 登记遗留红：`tests/test_str06_stage2_owners.py`（threading.Barrier）
    补进 `tests/conftest.py::_SERIAL_TEST_MODULES`，`-n 4 --dist loadgroup`
    完整回归不再被该项卡住。
  - 闸门复跑收口（同日）：首轮完整回归红 6 项、三类根因，全部修复——①嵌套 pytest
    深度锚点撞 MAX_PATH（`_pytest` 把嵌套 basetemp 锚到 worker 根）；②R6 新增
    `UnsealedArtifact` 未登记归属——按结构证据归 `UNREACHABLE_OVER_HTTP`；
    ③R4 后 `web/src/fixtures/backend_v1/enums.json` 未重生成（经
    `tools/regen_web_enums_fixture.py` 重生成，diff 恰为该批三处生命周期字段）。

- [x] **R5b · 付费路径遗留项收口（B6-A 前置批；免费；评审意见逐条处理）**
  （2026-09-29 完成）：B6-A 报价前把挡在付费路径上的遗留项一次改完。评审
  七条意见全部有落点：① `body_chapters` 生产接口定为 None＝全书／非空 tuple＝恰好
  这些章／`()` 非法；② 选章不进任何身份（写死并由测试钉住）；③ 补 HTTP→CLI→HTTP
  端到端验收——续跑是**新 run**（continuation 指向原 run）；④ 留证语义按「最近一次
  完成执行」；⑤ Guard 聚合改表驱动＋集合相等断言；⑥ Stage2/4 canonical owner 冻结闸
  列为 B6 前置闸；⑦ B6 run manifest 报价授权时写入输出根，不进仓库。
  - W1 选章：`novel/stages/chain.py::resolve_body_chapters`（`()`／乱序／重复／
    越界 1..N 在付费调用前拒绝，不静默截断），`_committed_set` 按目标集合迭代、
    `body_complete` 仍按全书判断；CLI `--body-chapters` 只对 `str06.stage5.body`
    有效、编排器 `build_argv` 永不生成（产品端仍全书自动）；选章只记
    `workflow_status.json` 审计字段。工具 `tools/canary_chapters.py`：读**已封存**
    WriterBookIR，逐章计分＋机械三章＋下一条命令。
  - W2 逐章留证：`runtime/body_failures.json` 与
    `runtime/chapter_observation_failures.json` 共用「最近一次完成执行」形状
    （`requested/attempted/chapters/recorded_at`）；正常完成写空清单、带失败
    完成上抛前写、崩溃不写（缺失＝没有完成执行）。契约视图抽为
    `Str06ChapterFailuresView`（I-11），`Str06RunView` 增
    `chapter_observation_failures`；监控屏两块并排（章观察／正文）。
  - W3 契约登记漂移：`GUARD_CHECKS` 表驱动（表序即聚合序，为 guard report 留证
    字节逐字不变而冻结），判据注册表补 `guard_m7_read_projection`（R-6.14）与
    `guard_m8_thread_lifecycle`（R-6.15）；`tests/test_hard_counterexamples.py`
    新增防漂移断言；`chapter_prefix_replay` 旧名统一为
    `committed_set_recovery`（R-12.4／R-12.4b／R-13.5 与三份 schema 文档）。
  - W4 端到端验收（本批闸门）：`tests/test_str06_product_entry.py` 第七节——
    未授权 HTTP 上传（只跑 stage0）→ HTTP 起 AgentRun（`pause_before_node`）跑完
    Stage2/Stage4/Hook 停在正文前 → 真实 CLI 子进程 `--body-chapters 2` 只落 ch02
    → HTTP continuation 新 run 显式解除边界，跑完正文、命名、组装交付 ready；
    新增 ledger 恰为 body_ch01＋naming 两笔，canary 章回执与正文字节不变；重启
    读回三面一致。
  - W5 登记：裁决 D1–D7 入 `docs/DECISIONS.md`（D8 留到语义冻结闸），PLAN.md
    执行序插入 R5b 与语义冻结闸、E-9 标注已消歧。
  - 验证（fake transport、零付费、`-n 0`、独立 basetemp）：R5b 新增用例与全部
    受影响既有文件全绿；契约 §9.5 五文件＋test_api_enums＋test_str06_f7e_freeze
    全绿；前端四件在 web/ 实跑通过。

- [x] **语义冻结闸 · Stage2/4 语义层按现状冻结（B6-A 前置闸；免费）**
  （2026-09-29 完成）：用户在 R5b 合入后裁决 D8——按合入后 main tip（`0508b447`）
  冻结语义层，`world_constraints` 不拆。本批只登记与加测试，未动任何被冻结面；
  冻结面自 `0508b447` 起逐字有效（契约表身份哈希不变）。冻结面（与
  `tests/test_str06_f7e_freeze.py` 的冻结记录逐字对账）：

  | 层 | 冻结内容 |
  |---|---|
  | Stage2 观察协议 | book／chapters／names 共用 `stage2-observation-v1`；associate 独立 `stage2-associate-v2`（R1 起） |
  | Stage4 wire 协议 | `stage4-preserve-v1`／`stage4-design-v2`／`stage4-bind-v1`／`stage4-hook-design-v1` |
  | canonical 版式 | `STR06_RC_VERSION`＝`str06-rc10`；Stage2/4 canonical 产物版本照 `contracts/artifacts.py::STR06_ARTIFACT_RC_VERSIONS`（Characters rc3、chapter_blueprints rc5、fold rc5、preserve_checkpoint rc3、reconstruction_brief rc3、semantic_binding rc3、execution_plan rc3、guard report rc4、packet_metrics／body_design／presentation_graph／writer_book_ir rc10、stage4 hook rc3） |
  | 契约表 | 节点表 14（付费 11、免费 compile×2＋assemble）、产物表 25 份、运行期配置——由原 `candidate_contract_identity()` 冻结基线继续钉 |
  | 边界 | 四类语义真源（Characters／Blueprint／TargetDesign／SemanticBinding）与 owner 边界；`world_constraints` 通道维持 Blueprint 观察 → grounding 机械填 `book_constraints` → Design `world_rules` 决策沿用／改写，不拆字段、不添节点 |

  - 分层冻结边界：写作层（renderer v5、WriterPacket 呈现、写手协议 v5）
    不在本闸内，B6-A canary 收敛后再冻；本闸不代签语义未评项，B6 阅读验收协议不变。
  - 机械拦截：`tests/test_str06_f7e_freeze.py` 新增语义协议冻结钉
    （`FROZEN_SEMANTIC_PROTOCOLS` 六条字面量，与契约表身份冻结同文件）——协议
    字面量不是 `candidate_contract_identity` 的输入，单靠身份哈希拦不住「顺手升版」
    静默混用不同协议的已购产物，故单独钉；漂移即红，红因写「先取得用户裁决并登记
    再动」。
  - 生效范围：B6-A／B6-B 期间任何语义层升版＝已购 Stage2/4 产物作废、须重购，
    属用户级议程；写作层问题只重跑 canary 写手，不动语义层。
  - 验证（免费、`-n 0`、独立 basetemp）：`tests/test_str06_f7e_freeze.py` 全绿
    （含新增语义协议冻结钉与既有身份／节点／产物／配置四查）。

### AUD 审计反例（A0 登记；已全部转绿）

A0–A6 提案（2026-09-28）已吸收为 R0–R7 批次并全部执行完毕（原临时计划 PLAN.md
已删除）；下表是当时
固化的反例基线——AUD-01/02 随 R1、AUD-03/04 随 R2、AUD-05 随 R6 转绿，反例保存在
对应批次的正式测试中：

| 编号 | 必须固化的反例 | 原行为与通过标准 |
|---|---|---|
| AUD-01 | 合法 StateTarget／EffectTarget 的人物、关系、world 输入 | 原投影将 typed 对象变成空 dict，prompt 显示 world；修复后各对象、关系方向与 key/value 原样到达关联输入 |
| AUD-02 | 初态 bob.trust；同章两拍分别写 alice.trust_level 与 bob.trust_level，关联只选择 bob | 原跨拍同名句柄覆盖，compiler 将同一关联套给两拍并误拒；修复后两个句柄独立，合法 bob 关联可发布，alice 不受影响 |
| AUD-03 | 前章建立口令，后章在具体 beat 读取，当前场不重写 | 原 compile/Guard 通过，但章包与 prompt 无值；改前章值后后章摘要不变。修复后值到达消费点且相关身份改变，无关章不变 |
| AUD-04 | 当前四类合法非空字段；新协议另补完整生命周期样例 | 原添加字段后 WriterIR 完全不变；修复后每个已声明消费有真实输入；采纳生命周期契约后，再验证建立前、建立拍、有效期和关闭后 |
| AUD-05 | fake 全链生成后，删除全部 CommitBundle，再只运行 Stage7 组装 | 原 14 份 bundle 全删仍可读且 assemble=ready；统一发布启用后正式消费必须拒绝，恢复可从原证据免费补尾再交付 |

## 职责解耦批次（2026-09-27 起；B0–B6）

裁决 D1–D10 各行见 [DECISIONS.md](DECISIONS.md) 2026-09-27；目标架构（Stage2 五节点、
ChapterWriterPacket 三件套、执行设施与提交顺序、精确失效表）唯一登记在
[str06.md](schema/str06.md)。每批同时闭合 producer、consumer、恢复、测试与文档；
B0–B5 全部 fake transport、零模型调用。

| 批次 | 内容与结果 | 完成条件 |
|---|---|---|
| [x] B0 契约与文档 | 登记 D1–D10 裁决、候选根运行记录映射表与删除条件、各付费节点 token 量级表；删除长篇／多线／容量增长待验项 | §9.5 五文件；文档引用检查 |
| [x] B1 执行设施 | 五层记录对齐（SemanticRequest／ContentRevision／ProviderAttempt／AcceptanceRecord／CommitBundle＋seal）、attempt 与传输重试按是否已送达分类、D5 空回答救回（先写含 `write_chapter` IndexError 场景的回归反例）、截断即停 D6（`max_tokens` 131072）、ExecutionProfile run 启动冻结、并发登记新链化、provenance 显式引用、整书总闸按 attempt 计数（unknown 占额、重启不重置） | 故障注入全绿：空回答、截断、多 attempt、权限拒绝、未知执行、预算竞争、重启不重置、各提交窗口崩溃 |
| [x] B2 Stage2 五节点重建 | `str06.stage2.book`／`chapters`／`names`／`associate`／`compile` 五 owner；`str06.stage2.source` 退役（整包删除登记见 B5 行）；观察与关联协议（`contracts/schema/observation.py`）、Resolver（句段闭集＋逐字锚唯一定位）、compile 双真源发布（Characters＋Blueprint 唯一 writer）；公开节点 11→14；上游两处缺陷就地修复（chapters 逐章产物进完成度三账、监控面章文件后缀） | 下方「各批验收细节 · Stage2」全部项（`tests/test_str06_stage2_publish.py` 27 项覆盖） |
| [x] B3 Stage4 章闭包 | 四协议独立版化、Design wire 瘦身与 compiler 派生面（ID／预算／复合引用归 compiler）、TargetDesign 结构化跨章字段、读者确认地址（D4）、按拍状态读取先读旧值后写新值、T₀ 四态（unknown/unset/set/未建立，F5e 垫底失败保留为回归反例）、章闭包证明、Hook 与 HookContext 归位 Stage4 包（F7e 新链落位清单）；判据 `chapter_closure_read_resolution`（R-6.13）。余项 Hook 输入投影瘦身随后续批次（R2 已做 Hook 分层渲染） | 下方「各批验收细节 · Stage4 章闭包」全部项（`tests/test_str06_chapter_closure.py` 12 项覆盖） |
| [x] B4 Stage5/7 | ChapterWriterPacket 三件套与章级验证材料（D2，rc8）；章包投影/渲染/摘要唯一产地；章回执改绑章包摘要＋请求身份＋验证版本＋改名前正文摘要；逐章并发（`novel/model_slots.py` 共享书级/供应商总槽，并发度不进语义身份）；集合恢复按有效提交集合只补缺章、局部失败留证；Stage7 逐章核对与五态按 item 报告（I-42）、乱序完成得到相同有序交付、Hook 失败不破坏章提交 | 下方「各批验收细节 · Stage5/7 与 Runtime」全部项 |
| [x] B5 产品与退役 | 真实子进程 fake 全链（HTTP 上传→autostart→Supervisor→真实 run_step／CLI 子进程→交付→重启读回逐字节一致）；**S-05 旧路径删除：`novel/stages/stage2/source.py` 整包删除（B5 行删除登记）**，反例迁五 owner 引擎（判据 `source_book_extraction` 更名 `source_book_observation`）；执行期配置重读路径清偿（`load_config()` 执行期唯一调用点是 `chain.execute`）；旧链 runner 形态与 prepared-recovery 审计族删除；test_store 旧链 store 族 40 项红账清零；`tests/suite_catalog.json` 迁移叙事清理。存量解码面（`RETIRED_NODES`／reviewer legacy／workflow_transition）删除条件未到，随 STR-06 关闭删除 | 真实 HTTP 上传到 Stage7 的 fake 全链；默认非 corpus 回归＋前端 test/typecheck/seams/build |
| [ ] B6 验收（付费） | B6a 按最终提交复验工程基线；B6-B 新链产出《夫君的报恩》供用户通读（F9 口径）；B6c Workbench 两本 golden（见「本轮之后」与验收细节）。**D11：main 已提前合入，B6a 基线以合入后 main 为准**；当前 B6-A canary 进行中（见「当前状态与下一步」） | 下方「各批验收细节 · B6」；工程与用户验收同时有效后关闭 STR-06 |
| [x] 收尾批（2026-09-28） | S-05 尾项三项复核清偿（死投影删除＋陈旧注释更正）；`Str06RunView` 补失败形状直投影（failure_kind／detail＋ExecutionError 六字段，I-36）与逐章留证、装配五态报告的 API/前端消费面同批闭合 | 契约 §9.5 五文件；定向 store／layering／views／product_entry／api 全绿；前端四件全绿 |

### 各批验收细节

**Stage2（B2）**
- 模型不生成机械 ID（拍号、状态/约束编号、命题/loop/场景 ID、章序、offset、hash、
  登记表可推导作用域一律 compiler 生成）；Validator 不改输入；Compiler/Resolver 不猜
  语义（重复、不存在、冲突定位产生精确 issue，不模糊匹配、不猜笔误；称谓锚点沿用 `R-2.10`）。
- 章观察互不依赖：保留前文原文、去掉前章快照/命题表/beat 表（D3）；前章编译或关联失败
  不改变后章观察请求；缺失单元恢复不重购已存结果。
- 覆盖同名不同人、同义命题、状态键近义合并、重复引文、不存在证据、错误嵌套、同拍引用、
  跨章因果、悬念开合、跨章续场；关联未决时 compile 拒绝发布并按 item 报告，恢复走
  owner patch 或 associate 定向修订。
- 后文身份确认不提前改变角色知情、读者确认或初始事实；关联只建立同一性与引用，不移动
  事实成立、角色知情或读者揭示时点；下游只凭 canonical 运行。
- 源 fold 只消费 canonical transitions；未揭示 concealed、空转移拍比例等读数保留
  （度量，不是门，`R-2.17`）。

**Stage4 章闭包（B3）**
- 覆盖先读后写、先写后读、跨章续场、作用域失效、unknown/unset/未建立；含 F5e「用
  unknown 初态垫底、毒化 fold」失败回归反例。
- 读者确认缺地址即拒（D4）；未确认项只投影到含该地址的章包，确认前不作为已确认信息。
- 修改无关未来章不改变当前章包；修改真实依赖只影响相关章包；场景权重变化按整数预算
  （全书 largest-remainder 归一）传导，可能波及多章。
- Compiler 证明「已声明依赖的引用闭合」；语义依赖是否遗漏归质量验收，机械检查不冒充证明。

**Stage5/7 与 Runtime（B4/B5）**
- Writer 请求不含前文正文与其他章 IR（D2）；验证材料全在章包内；新增跨章事实没有机械
  检测手段，按 `R-7.6` 扩充未评披露（转场、跨章无名细节、声线归 B6 阅读）。
- 乱序完成得到相同有序交付；单章失败、缺口恢复与 Hook 失败不破坏其他合法提交；已完成
  1、2、4、6 章时只补 3、5。
- 并发不串输入、来源或费用；冻结 ExecutionProfile 与实际 transport 参数一致（含
  `max_tokens` 131072）；并发度不进语义身份。
- 章回执绑定章包摘要、请求身份、验证版本与改名前正文摘要；Stage7 逐章核对回执与当前
  编译出的章包摘要一致、集合齐备，再按阅读序组装交付。
- 真实 HTTP 上传 → 自动编排 → CLI 子进程 → Repository → Stage7 交付，页面刷新与
  服务重启后读回一致（B5）。

**B6 验收**
- B6a：按最终提交复验工程基线（默认非 corpus 回归、契约 §9.5 五文件、前端四件；若
  B5 后无生产代码/契约/测试基础设施修改可复用其证据）。
- B6-B（取代 F9）：经产品入口产出《夫君的报恩》；agent 先核验章连续非空、回执与集合、
  改名零残留、manifest/hash、零新购重放、Stage 页与盘面一致、ledger 成本，再请用户
  通读。工程缺陷修到通过；文学意见登记为下一轮输入，不阻塞关闭。
- B6c golden：Workbench 两本短篇（《夫君的报恩》《三人成行》），按「本轮之后」六维、
  人工阅读为主；先把 `tests/golden.py::CRITERIA` 更新为六维（现为旧六项且引用已退役的
  R-9 条款）。《夫君的报恩》对照 F5e 成书读数（原始根已清理出盘，对照以本文件历史
  登记的 F5e 数字为准），《三人成行》对照 Workbench 的 Golden Set v2.1 oracle。
  阅读项沿用 F9 预登记清单（H1–H4 验收假设：H1 Preserve 义务从「转写」转向「作用」；
  H2 Design 章目的与分场一致、不改写人物；H3 Hook presentation 不再漂移；H4 Stage2
  消除抽取缺口且引子按 owner 正确分流），另加四项新链风险：转场与逐章重开场、跨章
  重复交代、跨章无名细节冲突、声线漂移。预登记阅读清单（判据以原文为准）：Stage2
  四条（女主的自我标榜记为说辞且反差两面都在；离开不外推成「送离住处」；不同次藏身
  用不同 knowledge_key；第 11 章快照无并存双人选键、无「尚未决定」残留、孕期状态
  已解除）；引子分流三条（本书 ch01 有引自 e00 的开场拍且与正文不重复、初态无由
  开场拍首次产生的知情；《三人成行》两臂基本等价；合成样本概括段零引用）；Preserve
  七条（女主义务体现口头守礼与内心清醒的反差、失言→自曝→引起怀疑的连接、第二夜
  误会链、首夜床下确认时点为 reveal、离家半月与摊牌受阻之间不挂因果、无整场转写且
  must_preserve 有真实 false、主线事件无漏保）；Design 四条（11 章 purpose 与
  scene_ids 一致、不改写女主且 voice 不同质、床下确认拍有 reader delta 且此前线索
  仍在、称谓全部落在登记面内）；Hook 一条（presentation 对照引用拍）；正文与交付
  五条（新婚夜/死结与剪子/反对换人失言/结尾四处成立、床下藏身在确认拍前未被当作
  已确认叙述、无改变时间线/身份/前史的新设定、Hook 与正文一致、新名全书一致且混合
  名零次、「第N章」齐全）。单列指标（不参与语义判定）：各付费 owner 首次输出成功
  率。v13 已购结果的零成本对照读数：原始根已清理，以 `docs/DECISIONS.md` v13 行登记
  读数与 B6 新读数对照。保留全部失败与重试分母。
- 合入条件：B6a 与 B6-B 同时有效；B6c 结论分别报告。机械全绿不代替语义验收。
  （2026-09-28 D11：main 已按用户裁决提前快进合入，本条转为 STR-06 关闭条件；
  B6 缺陷在 main 上修复。）

### 候选根运行记录映射与删除条件（B0 登记，B1 执行）

候选根现有 request、admission、validation、adopt 四类运行记录逐项映射到执行设施五层
（SemanticRequest／ContentRevision／ProviderAttempt／AcceptanceRecord／CommitBundle，
契约锚点见 str06.md）；映射目标是账面语义，不是把旧根文件迁移或复制——旧输出根与
已购证据只读保留，新协议改变全部请求身份，历史采用在新链上不会命中。

| 候选根记录（现状） | 内容 | 映射目标 | 处置与删除条件（B1） |
|---|---|---|---|
| `runtime/request_<身份>.json` | 冻结请求＋原响应＋usage，按请求身份寻址（已购恢复证据） | SemanticRequest（业务任务、输入投影与语义协议）＋ProviderAttempt（冻结配置、准确请求、原响应、成本；**原响应按 attempt 存储并被 attempt 引用，空回答不再占据请求身份**） | 新链改由 attempt 引用的耐久响应存储后停止写旧形状；「请求与响应一体、按请求身份整存整取」的旧布局退役；历史文件继续作为 F7a 历史采用的只读证据源，不删除 |
| `runtime/validation_<身份>.json` | 单请求的解析/编译/校验结果 | AcceptanceRecord（解析、救回、引用解析、编译、机械验证分项结果；语义恒 `unver`） | AcceptanceRecord 落地后停止逐请求写 validation 文件；旧文件随旧根只读保留 |
| `runtime/admission.json`（含 `commander_admissions.jsonl` 命名的历史账） | 整书调用准入平行计数（只计成功应答、不计 unknown） | 删除，不映射——与「unknown 不释放额度」不符；额度唯一由工作单元内容轮＋整书总闸（外发前按 attempt 计数、unknown 也计入）＋ledger 承担 | B1 额度账切换并验证「重启不重置、unknown 占额」后删除写路与读取；文件随旧根只读保留 |
| `runtime/adopt_<owner>.json` | 历史采用与 owner patch 记录（cost_link 三档、patch note 基线身份） | ContentRevision（首次生成或修订；owner patch 是无外发的修订，绑定基线请求身份与修订说明）＋CommitBundle 的采用证据（采用状态从 seal 派生） | CommitBundle 落地后停止写独立 adopt 文件；F-4 的 allow_patch 建档语义迁入修订记录；旧文件随旧根只读保留 |
| `runtime/body_attempts_chNN.json`、`runtime/hook_attempts.json` | 同输入有限重写的候选尝试与选择 | ContentRevision（内容轮额度沿用）＋ProviderAttempt（每轮至多 `contracts/invariants.py::WORK_UNIT_MAX_DELIVERY_ATTEMPTS`＝4 次） | B4 回执改绑时随章回执迁移到新记录；旧文件随旧根只读保留 |
| ——（无旧对应物） | —— | CommitBundle：输入清单、采用证据、输出摘要与 seal | B1 新增；正式提交记录，无删除条件 |

### 各付费节点 token 预期量级（B0 登记；D6）

每个付费节点的 reasoning 与正文 token 逐次报出归 B1 执行设施；本表登记预期量级，
真实读数显著超出量级即回查节点设计（协议瘦身、字段裁剪），不以降 effort 代替。
量级依据为迁移期真实读数（v13 验证轮 book／ch01–ch06、F9 根 names／preserve、F7e
recheck 采用记录里的 F5e 实付、F6c 冒烟），原始输出根已清理出盘，数字以本表与
`docs/DECISIONS.md` 登记为准、不再可逐行重读。表值为「prompt / completion 合计（含
reasoning）」量级；DeepSeek usage 当前不单独报 reasoning，B1 的逐次分列按传输实际
能力闭合，取不到时保持 null 不猜。

| 付费节点 | prompt 量级 | completion 量级 | 说明 |
|---|---|---|---|
| `str06.stage2.book` | 1.3万–1.6万 | 0.5万–1.5万 | v13/F9 实测；含 e00 引子证据段 |
| `str06.stage2.chapters`（每章） | 3千–9千 | 0.8万–4.5万 | completion 高值主要是 reasoning（v13 ch05 42515 为高 effort 对话章）；D3 去登记表后预期趋低 |
| `str06.stage2.names` | ~9千 | 3千–2万 | F9 8920/17532、F6c 8.6k/2.6k（effort 差异） |
| `str06.stage2.associate` | ≤1万（初始闸，无实测） | ≤1万（初始闸，无实测） | 新节点：输入为句柄与最小文本，应显著小于 book；B6 真实读数回填后修订本表 |
| `str06.stage4.preserve` | ~1.9万 | ~5.5万 | F9 实测 18569/54928；接近旧 65536 上限，128K（D6）后仍按此量级预期，超出即回查协议 |
| `str06.stage4.body_design` | ~1.2万 | ~6万 | F5e 11952/61040；整书一次调用、最可能触上限的节点，协议瘦身首要对象 |
| `str06.stage4.bind` | ~3.6万 | ~3.2万 | F5e 36150/32146 |
| `str06.stage4.hook_design` | 1.7万–2.6万 | 1.9万–3.2万 | F5e/F6c 两点实测 |
| `str06.stage5.body`（每章） | 旧形态 5.4万–6.1万；新章包预期 ≤2万（B4 以 renderer 字节数免费实测钉住） | 1千–7千 | F5e body_ch01–ch11；completion 含少量 reasoning，正文 1–2k 字 |
| `str06.stage5.hook` | ~1.6千 | 4千–7千 | F5e 1571/4195、F6c 1.3k/6.5k |
| `str06.stage7.naming` | ~1千 | ~2千 | F6c 0.8k/1.9k |
| `str06.stage2.compile`、`str06.stage4.compile`、`str06.stage7.assemble` | 0 | 0 | 结构性禁付（`executor_content_limit=0`），无 token |

### 按裁决接受、到 B6 才首次检验的风险

- D2 不做切换前 A/B：转场、重复交代、无名细节一致与声线只在 B6 阅读中检验。
- D3 不做切换前探针：关联的误并与漏并只在 B6 及 v13 对照读数中暴露。
- 并发 4（`contracts/invariants.py::PIPELINE_DEFAULT_CONCURRENCY` 沿用）在新链未实测，
  靠 429 重试兜底；此前实测（旧 agent 驱动）32 并发大半章被秒退、3 并发正常；B6 报出
  429 与重试计数。
- 《夫君的报恩》是开发用书，golden 独立性有限（D10，显式披露）。
- 新协议改变全部请求身份：历史采用在新链上不会命中，B6 全部新购（F5e 成书作零成本
  对照基线，不复制缓存）。
- 报告恒等基线：IDENTITY_UPSTREAM 只在书级闸被排除、逐章闸未排除——现状登记
  （渲染记号已补，`novel/report/render.py`），是否收敛到统一口径 B6 后再议。

## B6 执行与后续优化次序

1. 冻结代码、协议、配置、输入和输出根。先盘点可核验的已购回答与不可采用节点，
   按实际章数、模型参数、重试规则与剩余额度给出会话报价；付费与正文外发分别授权。
   仍用 `NOVEL_DATA`／`NOVEL_CORPUS` 读取指定 Workbench，`NOVEL_OUTPUT` 独立输出，
   不覆盖源语料。
2. 从真实产品入口生成《夫君的报恩》。失败定位 node/item：确定性缺陷免费修；语义缺口
   回所属 owner 窄修订；pending／unknown 先恢复。不得为了拿到成书临时关闭新闸或整书
   反复重购。逐调用保留 provider/model、耗时、token、reasoning、缓存与未知费用。
3. 开发 agent 在验收会话中先核查章连续非空、回执与发布组、改名零残留、交付字节、
   下载、重启恢复、ledger；再离线通读全书和 Hook，对照源书与 F5e 基线，给用户当前
   版本成书与问题记录。这是开发性验收，不给整书 Agent／Runtime 新增语义评审节点。
   阅读重点为事实／因果／知情／揭示，以及转场重开、重复交代、无名细节冲突、声线漂移。
   - **人工阅读验收协议（R4 批 2026-09-28 正式登记；B6-A 与 B6-B 共用，此处为
     协议全文的唯一登记面）**：
     - **维度（九维，冻结）**：
       1. 情节与因果是否完整；
       2. 揭示时点是否正确；
       3. 章间衔接是否自然；
       4. 人物声音是否稳定；
       5. 写手是否把 IR 写成了说明书；
       6. 信息有没有重复交代；
       7. 场景是否通过行动与对话展开；
       8. Hook 是否有吸引力而不提前兑现；
       9. 整体节奏与流畅度是否优于基线。
     - **记录方式**：不打分。每条问题记「章与段位置 → 维度 → 归因 → 处理」。
       - 归因取值：Stage2 抽取／Preserve／Design／Bind／编译投影／renderer／
         写手协议／写手发挥。
       - 处理取值：canary 收敛或 R7。
     - **存放约定**：阅读记录存运行根，不进仓库，也不在仓库里摘录正文；仓库只写
       汇总。
     - **边界**：语义保持 unver，机械回执不代签质量。
     上文阅读重点是工程核查与开发性阅读视角的补充，以本协议维度为准，不另立
     第二份维度表。
4. 用户通读结论绑定确切成书版本。遵守 D1/D11：工程缺陷修复，文学意见登记为下一轮输入；
   已提前合入 main 不表示 B6 完成。机械回执仍保留语义未评，不由免费 Guard 代签质量通过。
5. STR-06 关闭后的文学优化仍依现有“本轮之后”执行，再做 D10 两本短篇 Golden；
   B6c 保留为该后续质量工作编号。

后续优化按实证推进，不与 R 批混为无边界重构：

| 顺序 | 优化对象 | 决策证据与约束 |
|---|---|---|
| 质量优先 | 漏保／过保、源结构泄漏、目标因果、衔接、声线和导演说明 | 从 B6 的具体章／拍反例定位 Source、Design、Binding 或 Writer；先确认章包是否已充分提供语义，再改所属 prompt；不让 Writer 代做缺失规划 |
| 输入精度 | 无关人物声线、重复约束、重复状态与冗长协议 | R2 完整后统计实际 renderer 字节与真实 token；只按显式依赖裁剪。全书声线现为正式书级材料，收窄需同步契约和对照验证；不截断、概括掉必要事实 |
| 付费效率 | 局部失败重跑、相同请求复用、重试与并发 | 先看请求身份与采用命中、429/unknown、活跃时间和成本分母，再调槽位；并发度不进语义身份。保留 D6 上限与截断即停，不降 effort 掩盖设计问题 |
| 质量回归 | 两本固定短篇的六维评价 | 先升级 `tests/golden.py` 的退役 rubric 引用，再冻结样本、baseline、评价协议和成本；人工为主，模型辅助另授权；评价变化先重评现有成书，不自动重生成 |

执行方案的最终成功标准是：已声明语义完整到达实际消费者，未声明的语义不被确定性代码
猜造；无关改动不扩大付费失效；未发布或证据不全不能伪装交付；真实成书的工程结果与
文学评价分别有证据。到达这一状态后再按测量结果优化成本与速度。

## 恢复、替代与删除条件

本节是从迁移期接管的有效义务与删除条件的唯一活动登记。F8c 已按登记完成迁移包删除、
旧链整层退役与红账清偿（F8d 默认非 corpus 回归 3034 项零失败）；`contracts/schema/retirement.py`
台账（Stage6 106 项、旧 Stage2/4/5/7 补充登记）保留至 STR-06 关闭，删除口径＝先迁
有效独有反例再删（按家族登记，`tests/test_str06_f7e_retirement.py` 双向对账）。

仍有效的登记：

| 对象 | 现役语义与条件 |
|---|---|
| 候选根运行记录映射 | request／admission／validation／adopt 四类旧记录与 attempts 已按「候选根运行记录映射与删除条件」表并入五层记录（B1 执行）；旧输出根已清理出盘，历史采用通路与证据按 Git 及本文件登记读取 |
| 迁移期 owner→节点归属表 STR06_OWNER_UNITS | 已随 F7b 拆 owner runner 删除；节点归属现由 owner 名当场解析（`novel/llm/paid_requests.py`） |
| Stage1 `hook_copy` | 2026-09-26 改写：源引子恢复唯一消费者——`e00_*` 章前证据段进 Stage2 book 与第 1 章抽取（按信息职责分流，判断不了弃权），并与 main_text 构成组合源身份（只改引子即拒复用旧购买，`novel/store.py` 同口径） |
| 书级篇幅、Hook 长度与章篇幅带 | 从正式 MainText 派生书级篇幅、固定目标章数为源章数；篇幅带与 Hook 长度有正式配置来源，配置变化进入实际消费身份 |
| 存量解码面 | `RETIRED_NODES`／reviewer legacy／workflow_transition 的删除条件未到，随 STR-06 关闭删除 |

历史响应先耐久再校验；同请求身份优先免费恢复，pending／unknown 不自动重购。
语义修复核对 owner patch 的基线身份，验证无关 canonical 内容及稳定 ID 保持；
derived 仅重算受影响 slice，不能以保护已购结果为由忽略真源写入保护。
章回执绑定改名前正文、全书 IR、实际前文与请求身份；恢复按已提交目标章集合
（committed_set_recovery）核对，planned state 由正式计划确定性重放，完整前文从接受
正文重建。全书 IR 的 Writer 可见变化须按新身份重验正文；第 k 章正文变化保留其前缀、
后续章按实际请求重验。输入未变的已购结果继续采用；扫描全书不等于付费重跑全书。
换 NameMap 只影响交付，Hook 窄改不使无关正文接受失效。

## 全链验收与关闭条件

写作验收：Writer 在章包（导演说明＋事实约束）边界内落实可信的欲望、阻力、选择与后果；
不读前文正文与其他章 IR（D2），衔接依赖章包内的承接与声线约束。B6-B 用户通读正式入口
的交付版本，问题按工程缺陷与文学质量意见记录。重复、剧透、虚假悬念及语气错配等阅读
证据保留；机械篇幅披露与未执行的语义质量评估分别报告，不互相替代。HookReceipt／章级
回执只证明机械范围，语义未评如实披露；正文或检查证据缺失、未知不得据 Q-13 伪造交付
成功。

切换与关闭条件：四类 truth、唯一 writer、依赖与完整消费链闭合；Preserve／Bind 隔离；
窄修复和实际消费身份生效；恢复可按有效提交集合重建；交付可重放，旧链退役登记和红账
全部闭合。B6a 工程复验加 B6-B 用户验收共同构成本轮终点；main 已按 D11 提前合入，关闭
后发现的缺陷在 main 上修复。跨章重构自由度与第二本表达力在 B6c 中分别报告，不以迁移
验收冒充质量普适性；长篇永不做（D7），无相应待验项。

## 成本与授权

B0–B5 免费实现与验证（fake transport）；B6 付费与正文外发分别授权，开跑前按实际章数
与可采用结果报价。新链每本基础调用约为书级 1＋章 N＋names 1＋关联 1＋Stage4 4
（Preserve／Body Design／Bind／Hook Design）＋正文 N＋Hook 1＋Naming 1（11 章约
31 次）；正文重写至多每章 2 次（合计至多 2N）、Hook 重写至多 2 次；同轮 attempt 重试
与未知成本分列。次数不是承诺的新购次数或费用上限。F5e 成功新购 23 次、约 111 万
prompt＋23 万 completion tokens 仅作历史参照；各付费节点 token 预期量级见
「职责解耦批次」的量级表（B0 登记），超出即回查节点设计（D6）。开跑前按最终节点、
配置、授权及可核验的同请求历史结果重新报价。未知历史成本不记为零，reasoning 不重复
计入 completion。

历史采用（`--adopt-root`／`--wire-dir`）是正式机制；迁移期候选根与 F5e／F9 输出根已
清理出盘，历史账按 Git 与本文件登记读取。新协议改变全部请求身份，历史采用在新链上
不会命中，B6 全部新购。目标根续跑复用本轮身份与累计工作单元／书级额度，重启、换 job
不重置额度。付费显式 `--yes`，正文外发分别授权，`--check-only` 表示零新购复验、可执行
恢复写入，文档不得称为纯只读检查。

## 本轮之后

以下只保留范围和验收要求，STR-06 关闭后另行立项，不是并行活动计划。先进行一轮新契约
与流水线迭代优化，输入为 B6 文学质量意见清单及下列持续登记项；之后再做 Golden。

Golden（D10）：源语料固定 Workbench 两本短篇——《夫君的报恩》（开发用书，golden
独立性有限，显式披露）与《三人成行》；删除「至少一本长篇或多线结构」要求，长篇／
多线永不做（D7）。先把 `tests/golden.py::CRITERIA` 更新为六维（现为旧六项且引用已退役的
R-9 条款）。评价沿用六维：Core Preservation、Structural Originality、Causal
Continuity、Character Continuity、Natural Progression、Local Prose Coherence；人工
阅读为主，模型评分只作辅助。冻结输入集、源版本、baseline 成书、rubric/version、
评价方式、模型辅助评审的 prompt／provider／model／参数、输出根和成本信封；评价过程
变化时重评双方现有成书，不因此重跑生成；不得临时缩减已有冻结 manifest 分母；书目在
显式 `NOVEL_DATA`／`NOVEL_CORPUS` 下核实后冻结。《夫君的报恩》对照 F5e 成书读数
（原始根已清理，以历史登记为准），《三人成行》对照 Workbench 的 Golden Set
v2.1 oracle；Structural Originality 以书／场景及语义承载关系比较，不要求同号章对应；
源书、新书、baseline 的证据位置分别记录；「遵守源书阅读时序」判据随目标语义版本化
改写，保留事实、因果、披露和改名质量覆盖；未评或证据不足保持未验证。NameMap v5
跨书赋值与第二本表达力随《三人成行》检验。Golden 不代替独立的 current-schema corpus
验收。

持续登记：漏保／过保、源结构泄漏、under-spec、状态过建模、checklist prose、
导演 brief 携带未登记事实或退化为通用形容词、章级机械检查的边界（章内揭示
位置与逐场义务兑现不可证）、过度失效、下游语义材料不足、Golden 成本；新链风险四项
（转场与逐章重开场、跨章重复交代、跨章无名细节冲突、声线漂移）归 B6 阅读项。

## 必要证据与标定知识

迁移期与重置期的临时证据根（repair／f2b／f3／f4／review_stage4_c／vnext_*／f6_smoke／
F5e／F7a–F7e／F9／v13／probe／e2e 等）均已清理出盘（未跟踪输出，不可恢复）；其有效
结论已固化进本文件登记（token 量级表、B6c 对照口径与历史批次索引）、`docs/DECISIONS.md`
与现役测试。历史原文与逐次记录按 Git 查询；证据不存在或身份不符时报告未查／不可用，
不补猜、也不重新付费掩盖。

| 现役入口 | 用途 |
|---|---|
| `tests/test_str06_contracts.py`、`tests/test_str06_invariants.py`、`tests/test_str06_fold_guard.py` | 新链契约、跨产物、章级回执绑定、窄失效、交付与 Fold／Guard 反例入口 |
| `tests/test_str06_source.py`、`tests/test_str06_target.py`、`tests/test_str06_execution_ir.py`、`tests/test_str06_repairs.py`、`tests/test_str06_workflow.py` | Source／Target／IR／修复与隔离入口回归，选测按真实影响，不凭文件名宣布全链通过 |
| `tests/test_str06_body.py`、`tests/test_str06_hook.py` | 按章成文与 Hook 免费反例入口：fake transport 全链到交付＋零新购恢复＋失效边界 |
| `tests/test_str06_views.py`、`tests/test_str06_product_entry.py` | 只读监控面与真实子进程产品链（HTTP 上传→交付→重启读回）入口 |
| `tests/test_str06_f7e_freeze.py` | 契约表身份与语义协议字面量冻结钉（改冻结面先取得用户裁决并登记） |

旧链标定知识（Guard 试点 bridge、Fidelity C1–C3、Judge 跨场回指、b07_01 窄修复案例、
16k thinking 截断教训）已随旧链退役，当前以现役判据与 `docs/schema/str06.md` 为准；
Guard 现役清单 M1–M8 见 `docs/schema/stage4.md`。

## 历史批次索引

迁移与整改期批次的完整交付记录见 Git 历史（随本文件历史版本保存）；此处只保留仍被
引用的口径与数字：

- **F4R（2026-09-24）红账清偿**：uploads 死引用修复（test_store 恢复收集）、深导入
  grandfather 补账 11 条（分母 77→88）、`_SHAPE_BREAKS` 补三条 v3 账。
- **F5a–F5e（2026-09-24）按章成文与真书成书**：F5e 用户授权付费 23 次新购、成书
  11461 字（全书偏差 +1.63%、逐章全部带内、改名零残留、同根重放零新购）；agent
  开发性阅读六维无硬伤，不代替用户验收。
- **F6（2026-09-24）Hook 链与 Stage7 终态契约**：RC3、Hook 唯一 publisher、NameMap
  v5、退役登记 106 项；全量非 corpus 回归 4902 通过、420 失败（全部为当时既有账，
  F8d 清零）。
- **F7a–F7e（2026-09-24/25）新链接线**：候选契约与身份、owner runner、API/Views、
  候选产物表 20→22、F7e recheck 零新购复现；**delete 口径＝先迁有效独有反例再删**
  （按家族登记，retirement.py 台账沿用至今）。
- **F8a–F8d（2026-09-24/25）切换与退役**：契约与编排翻转到新链；F8c 把迁移包
  `contracts/schema/str06/`、`novel/stages/str06/` 与 `tools/str06_workflow.py`
  解散分流进正式包（str06_* 旧 LLM 模块同批）；F8d 默认非 corpus 回归 3034 项零失败、
  零错误、零未说明跳过。
- **F9 首轮付费验收未通过**（停在 `str06.stage4.body_design` 之前），按 D1 由职责
  解耦计划取代；其口径由 B6 沿用——重跑建议用新独立根、不原地升级本根
  （`novel/store.py` 消费）；H1–H4 验收假设与预登记阅读清单并入「各批验收细节 ·
  B6 验收」。
- **质量改进批次（2026-09-26）**：确定性修复与 `R-2.8`／`R-2.9`／`R-10.10` 新增；
  代价披露——源身份公式变化，旧候选根在新代码下拒用，续跑用新独立根。
- **质量改进批次二（2026-09-26）v13 契约**：拍三类转移（effects/cognition/loops，
  D1–D17 见 DECISIONS）；批 0–2 完成、批 3 取消，语义结论止于 ch01–ch04，生产语义
  保持 unver。
- **A0–A6 提案（2026-09-28）**：已吸收为临时计划 PLAN.md 的 R0–R7 批次并全部执行
  完毕（PLAN.md 已删除，历史原文按 Git 查询）；AUD-01–05 反例全部转绿（随 R1／R2／R6）。

## 批次推进记录规则

每批开工核对实际状态、输入身份、许可与受影响消费者；验收覆盖生成、恢复、最终盘面及
适用的 unit/contract、stage、API/UI。通过后更新本文件的状态摘要、证据位置和下一批；
未通过保留精确 node/item、失败原因及恢复入口。不得以代码存在、收集通过或机械全绿勾选质量验收。
历史长记录进入快照／Git；可复用教训按需进入 LESSONS，裁决缘由进入 DECISIONS。
B0 只修订计划与说明文档，不代表开始 B1、不扩展付费授权、不修改现役可执行契约。
