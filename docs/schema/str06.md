# STR-06 终态架构契约（Stage2–Stage7，Stage6 退役）

本文件唯一登记 STR-06 的目标架构与现役 RC 契约。**2026-09-27 起目标架构按用户裁决的
职责解耦计划（D1–D10，各行见 `docs/DECISIONS.md` 2026-09-27）改写**：重建生成、编译、
执行和恢复边界，产品范围固定短篇小说（中文约 2 万字，永不做长篇，D7）。批次进度、
验收与删除条件唯一见 `docs/REQUIREMENTS.md`；架构裁决见 `docs/DECISIONS.md`。

**实施边界**：B0–B5 全部 fake transport、零模型调用，付费与正文外发只在 B6 按实际
报价分别授权。**B5（2026-09-28）已落地产品接入与旧路径删除（S-05）**：
上传→autostart→Supervisor→**真实 `run_step`／`novel.cli` 子进程**→Repository→
Stage7 交付、页面刷新与服务重启读回一致的 fake 全链钉在
`tests/test_str06_product_entry.py::test_service_full_chain_real_subprocess_and_restart_readback`
（fake transport 经 `sitecustomize` 注入子进程，`real_code_version` 过 run_step
代码身份闸；12 次付费、10 个付费节点——CS-3 bind 退役后现数、`--check-only` 追跑零新购、二次
`agent.start` 零派发）；旧联合抽取引擎 `novel/stages/stage2/source.py` 整包删除
（反例迁至五 owner 引擎，`tests/test_str06_source.py`／`tests/hard_cases.py`
换轨）；`_llm_client`／`create_legacy_client`／`default_legacy_backend`（执行期
`load_config()` 重读的最后残留）删除；`nodes.validate_run` 的旧链逐节点分支与
主操作互斥表、ledger 的旧链 prepared recovery 审计族、consumption 的
stage4/5/6/7 死投影函数随之清偿；test_store 旧链 store 族红账（40 项）随采样面
换轨（NameMap v5×canonical Characters、names×book 观察）清零。**B5 收尾批
（2026-09-28）**：终审遗留 S-05 尾项清偿——consumption 的
`stage4_plan_blueprints`／`stage7_hook`／`character_knowledge_reference`
三个零调用方死投影删除（后两者曾属旧链 stage2.source／stage7.rename 注册位），
陈旧 docstring 与旧链分节头更正；run 视图补失败类别与盘面证据投影——
`Str06RunView` 直投影 `workflow_status.json` 的失败形状（`failure_kind`／`detail`，
ExecutionError 另带 `error_code/stage/node/item/phase/context`）与
`runtime/body_failures.json`、`runtime/delivery/assembly_report.json` 两份盘面
留证（前端 str06 监控屏可展示失败类别与 item 定位）。**R5b（2026-09-29）**：
逐章失败留证改「最近一次完成执行」语义并扩到章观察（两份同形共用），调用层
选章 `--body-chapters`（canary）只作元数据不进身份（详见「Stage5 按章正文」节）。
**以小说质量为中心的重设计（2026-09-28；R0–R6 已落地，R4 批登记本文件；首版临时计划 PLAN.md 与后来的 R10 执行指南 PLAN.md 均已退役（原文分别见 Git 2fc03832、68b5802f），交付记录见提交 eb5f0d5a 中的 `docs/REQUIREMENTS.md`）**：
审计结论「最后一公里断了」（付费语义漏送达写手、跨章字段无消费者、系统不自愈、
发布权威名不副实）催生八项裁决（`docs/DECISIONS.md` 2026-09-28 行）与批次
R0→R1→R2→R3→R4→R6→R5→B6-A→B6-B→R7。已落地：R0 字段级送达登记
（`contracts/schema/consumption.py::STR06_FIELD_DELIVERY`＋守卫
`tests/test_str06_semantic_delivery.py`＋故障注入 fake 底座 `tests/str06_faults.py`）；
R1 associate 协议升 `stage2-associate-v2`；R2 两层 IR 与 rc10（WriterPacket 六分区、
renderer v5、写手协议 v5、拍上线程与 ThreadFold、章缝合 `entry`、世界规则通道、
回执身份拆分 rc6、Guard M7/M8）；R3 typed 自愈（`OwnerIssueV1` 路由表、有界修订轮、
跨节点回派 `owner_revision`、监控面 `owner_issues`）。R6 统一发布权威已落地
（2026-09-29；输出组 seal、免费同封存与正式读取校验，见「发布权威与读取校验」节）。
执行拓扑是默认拓扑、不是不变量（裁决 4）；CS-3（2026-10-03）bind 退役后现役
13 节点。

### R9 集中升版（2026-09-30，D8 修订二）

现役版式为 `str06-rc11`；节点仍为 14 个（CS-3 bind 退役后 13），运行配置未变。book 观察独立使用
`stage2-book-v2`，chapters／names 保持 `stage2-observation-v1`，associate 保持 v2。
书级 `expression` 逐项记录叙述人称／叙述者、视角权限、幽默及内外反差并带证据；
compiler 产生 Blueprint v14 的 `source_expression`、`ex_nn` 和非空白段长／句长／
对白比例读数。未测量读数明确为 unavailable／null，不作为质量闸。
源盲 ReconstructionBrief 只送表达陈述、人称许可及读数，不送证据原文和源章拓扑。

Design 的唯一形状源为 `contracts/schema/design.py::DesignWireV5`（R10a a4 起
v5：删 dramatic_intent；非知情/非读者 set 必填 modality；拍增 expression_refs），请求 Schema
由该模型生成并注入本书人物／称谓闭集。`narration` 复用 HookPov；first 的场景 POV
必须是唯一叙述者，third 的 POV 为许可 focalizers 或 narrator。每个表达项恰好一个
keep／change，change 必须有理由与目标表达。BookBrief 的人称进不可违背分区，
表达决定的陈述进导演说明；ex_id、行动与理由仅审计，不混入写手阅读面。

ChapterEntry 的 immediate_continuation／flashback 必须带较早事件拍 anchor；引用
须存在且早于本章，紧接分支必须指向上章末拍。compiler 投影 `anchor_event`，renderer
按 transition 渲染继续或倒叙，内部引用只留审计。本人已释放称谓及其登记家族构词
使用同一纯推导；正文／Hook 均禁用未释放专名，Stage7 按 NameMap 实际替换家族构词。
正向材料终检扫描实际 WriterPacket／Hook 请求分区；负向禁区排除，先认领全称再查
单字残余；未释放人物声线与收尾人物指认省略，禁止 slug 兜底。

全链字符计数使用 `novel/core/text.py::count_non_whitespace_chars`（标点计入、空白不计），
预算仅来自 MainText 正文，不含系统章标题或 Hook；Hook 单独配置。Design 接受与 compile
共用读取闭包，单章接受与 Stage2 compile 共用首现判据。ValidationStop／机械缺陷／
owner 停止保留 node、item 与问题坐标，CLI 负结论 exit 1，程序错误仍为 exit 70。

人工修订／重采记录为 `OwnerRevisionRecordV1`／`SamplingSelectionV1`，原补丁字节
持久保存在 runtime/revisions/evidence 的内容寻址文件。staged 不授权恢复级联；
open 必须精确绑定当前产物与原字节，传播只能来自封存输入边，最近成功的 Stage2
compile／Stage4 compile／Stage7 assemble 才 resolve。无登记或后续手改仍关闭式停住。
`tools/str06_review.py` 的 views／obligations／patch-preview 共用生产 renderer、
接受函数及下游编译器，默认只读；缺下游输入报告 not_run，`--save` 只写本书 runtime/review。
`--resample OWNER --reason 理由` 只在明确授权下分配新 nonce；重启复用已选 nonce，
请求身份改变则不沿用序号，原购买与工作单元／整书额度保留。
**B2 前半（2026-09-27）已落地注册面**：`contracts/invariants.py::NODES`
扩为 14 个公开节点（Stage2 五节点，`str06.stage2.source` 退役），观察与关联协议
（`contracts/schema/observation.py`）与 Resolver（`novel/stages/stage2/resolver.py`）
落地，冻结基线随 B2 重新钉定（`a0f5fb5e…`，`tests/test_str06_f7e_freeze.py`）。
**B2 第二步（2026-09-27）已落地 book／chapters 接线**：`novel/stages/stage2/book.py`
（书级观察 owner：闭集、`R-2.10` 锚点、`R-2.8` canonical 闭合）与
`novel/stages/stage2/chapters.py`（逐章观察 owner：本章证据闭集、`R-2.14` 作用域
归一、章内句柄纪律；请求互不依赖 D3）两 owner 引擎入列，`chain.py` 的
`run_source` 写路退役，`run_names` 重挂书级观察输入（names 引擎不变，D9），
付费 owner 记账映射（`_owner_unit`）改挂 `str06.stage2.book`／
`str06.stage2.chapters`。**B2 第三步（2026-09-27）已落地 associate／compile**：
`novel/stages/stage2/associate.py`（跨章关联 owner：句柄投影、只选已给句柄、
五类配对）与 `novel/stages/stage2/compile.py`（双真源唯一发布 owner：解析证据、
生成 ID、执行关联、未决拒发＋源 fold 唯一实现）入列，`chain.py` 的
`run_associate`／`run_compile` 接线；派生输入
`stage2/compiled/book_extraction.json` 写路与读取全部退役，Stage4 三腿
（Preserve／BodyDesign／HookDesign）改读 canonical（人物 core 由 Characters
投影）；顺带修复 chapters 节点完成度盲区导致的 Supervisor 重复派发死循环与
监控面逐章产物扫描（`novel/views/str06.py`）只认 `.txt` 的既存缺陷。F8a 激活基线契约身份 `04b6fb22…`、质量改进
批次二批 2 后随 Design 换 rc5 更新为 `7fb5ecfa…`（历史基线，见 Git 历史）。
下文「目标」各节是 B1–B5 的实施依据；「现役实装」各节描述切换期仍然有效的契约
与落点，B2 起逐批由目标形态取代。

### R10 关键语义交接（2026-10-01，D8 修订三；a1–a5＋收口已落地）

现役版式为 `str06-rc12`（全表统一；R10a 收口冻结身份
`7d24e572…`，`tests/test_str06_f7e_freeze.py`）。节点仍为 14 个（CS-3 bind
退役后 13），运行配置未变。
book 观察 `stage2-book-v3`、chapters 独立 `stage2-chapters-v2`（names 保持
`stage2-observation-v1`、associate 保持 v2，输入投影变经 RC 表换身份）；
Preserve `stage4-preserve-v2`＋policy 5、Design `stage4-design-v5`；
hook-design 字面不变。写作层 renderer／写手协议 v6 不在本批（R10b 升 v7）。

**章观察瘦 wire（CS-2 第一步，2026-10-03）**：`str06.stage2.chapters` 的
LLM 输出面升 `stage2-chapters-v3`（`contracts/schema/observation.py` 瘦模型：
`at` 地址串、默认值只写例外、src 连续区间、命题句柄按文本归并由代码派发），
经 `novel/stages/stage2/slim.py::expand_chapter_wire` 确定性派生回
`stage2-chapters-v2`（rc12）形状后进既有管道——canonical、盘面与全部下游
消费者不变（S-03 适配集中在 slim 模块；v2 wire 直通＝盘面与领养读面）。
r10b 实证：wire 体积降至 56%，`expand(slim(现产物)) == 现产物` 11/11 章
（模本地句柄重命名）；basis 逐字证据、闭集闸、D2 引文归一判据不放宽。

**证据寻址改版（RC-26～29，2026-10-04，rc15；2026-10-07 basis 指针化＋二次收口）**：
cs_golden 三书全部停在 `str06.stage2.chapters`，根因是证据切分只在「。」后切——
对白收尾「」」落到下一段、问号叹号收尾的多个自然段并成一段，段号标签悬在上一段
文字下方；模型系统性把引文挂到偏大的段号上（61 份被拒回答 52 份只因段号），返修在
「地址错／不在 src」间来回。改为：① 证据段＝一个非空自然段，请求一段一行
（`build_evidence`、`render_evidence_block`）；② src 数组元素可为区间；③ 条目返修轮
收到整份回答按整份验收。RC-27 先把段号交给代码（wire 只给引文）；2026-10-07
用户裁决「模型只选段、代码切原文」完成另一半——book／chapters 的 wire 只给
**段 id**，引文＝段全文由 compiler 派生（`resolver.basis_quote`），转写失败类
错误构造上消失；names 同批新增独立 wire 面（canonical 三者 v4/v4/v3 不变，旧盘面
照读）。**同日二次收口（用户四项裁决，flash/low 首跑实证 ch01/ch07 各因 state 漏
basis 字段整份返修后）**：① wire 词汇改 `evidence_refs`（与 canonical 的 basis
分层）——book/chapters wire 升 v7、names wire 升 v5；② `scene_location` 上移
`SlimBeat` 拍级字段（compiler 合成登记键 world 效应、证据面＝本拍 src 全体，
state 里出现即拒——单一通路）；③ wire 形状失败按 pydantic 路径条目级窄修
（`wire_shape_issues`：`beats[i].state[j]`／`initial_state[i]` 一类缺字段窄修该
条目，级联派生错误同根因只报最深一条，仅顶层结构／协议字面量失败退整份）；
④ 已核实的 ②既有事实——op/modality/scope/disclosure/proposition 默认、target
地址串、命题句柄、knowledge 缺省均已是 compiler-owned（瘦 wire 本就如此，verbose
null 四元组只存在于 canonical）。规格 `docs/specs/repair_convergence.feature`
RC-26～29，反例 `tests/test_stage2_evidence_addressing.py`、
`tests/test_str06_chapter_slim.py`。

**设计瘦 wire（CS-3 审阅修复，2026-10-03；事实锁引用升 v8，2026-10-04）**：LLM 输出为
`stage4-design-v8`（`contracts/schema/design.py::DesignWireV8`）。`real` 保留
义务接受拍/支撑拍；Brief 每条 requirement 的 `knowledge_changes` 从正式
anchor 派生，完整保留每次变化的盲身份、holder、kp、mode。代码按盲身份排序
派发 `obNN.kNN` 短引用，模型在对应义务 real 拍的 `know:[{ref}]` 声明实际
认知时点。每个必保变化恰好一次；已经承载的非必保义务也须标全其认知变化。
同一拍同一命题不能同时给多个模式；先怀疑、后证实分别落拍。

读者 known 的 know 标注须另带 `confirms` 目标事实地址，代码派生
knowledge(holder=reader) 增量并保留该关联。确认只能引用此刻已实际建立且未
解除/未知的事实，计划不能充当已发生。pending 禁区进入事实建立/读取场和
确认场；按完整地址匹配，kp 句柄与自由事实键可以不同。世界规则成立断言
默认 actual；immediate_continuation 锚点按每章场数取上一章末场末拍。
机械默认值和书级派生继续由 compiler 完成，v5 只是内部展开形状。

**事实锁引用（CS-F2，rc16，RC-36～39）**：v7 把 Brief 事实锁当状态键另写一条复述事实的状态，
《夫君的报恩》第三份回答因此把锁原文（含第 2 章尚未释放的全名）连同 8 条重复状态抄进自己的 val。
v8 改为在**已选状态**上写 `state[].lock`（kf 句柄，闭集由请求按 Brief 注入、不含原文未回答锁）：
时态锁标在同 modality 的 set 上、值未知锁标在 op=unknown 上、一个锁至多一条承载状态；kf 作状态键、
lock 标在 kp 知情行上、initial_state 带 kf 键一律拒。标注经 `novel/stages/stage4/slim.py::design_fact_marks` 投影为
`FactLockMark`（拍位＋状态下标＋由 `at`/`key` 派生的地址），`derive_semantic_binding` 据此在 body_design
同次发布的 SemanticBinding 里写 `fact_bindings`（`FactLockBinding`：锁句柄＋承载拍＋状态地址，
**不复制目标 value**，承载拍内该地址须恰好对应一条状态，悬空或歧义即拒）；BodyDesign 与中间 v5 都不携带锁引用，仍是目标事实的唯一持有者。
Design 接受判据携带所有同址条目的 wire 路径，Binding 派生与正式产物免费重编译复验同一唯一性；
后续拍对同地址的合法更新不受该引用限制。
映射只证结构；承载状态的语义是否满足锁（如把暂缓和离绑到赏花）需要标定的语义核验，未运行保持 unver。
`SemanticBinding` 与 `WriterBookIR` 因此升 rc16（旧 rc12／rc14 产物按版本字面量拒读，不迁移）；全局 RC 版式头
升 rc16，旧 rc15 候选根按契约表漂移关闭式拒收（`init_or_resume_candidate_workflow`），原根留作冻结失败证据，
续跑用新根并经历史采用按请求身份复用已购响应。

Brief、BodyDesign、PresentationGraph、ExecutionPlan、WriterBookIR 升 rc14；
其它产物版本不变。旧 rc12/rc13 仅做登记的无损读取迁移，地址、读取、Hook
引用与 IR 状态容器一起闭合。已经丢失逐义务模式的 Brief 免费重放 preserve
重建；丢失 confirms 的 rc13 Design 须免费 owner patch 补 v7 know/confirms，
复编译后恢复下游。旧 seal 字节不改写；迁移删除条件见 REQUIREMENTS。

关键语义交接的契约面（判据见 BACKEND_CONTRACT R-2.18/19、R-5.15～R-5.20）：

- **跨章命题同一性（R-2.12）**：associate 负责声明同义关系，措辞不同也可为
  同一命题；compiler 只按配对建等价类，不做文本全等或相似度语义判断。
  正式引用统一使用阅读序最早建立的代表文本，原观察与逐条 basis 不改写，
  事实成立、角色知情与读者揭示仍留在原拍。未配对的独立命题不自动合并；
  缺文本、无配对的悬空引用与未来引用继续未决。观察协议与产物形状不变，
  已购相同请求可经 `--check-only` 免费重验；机械通过不证明关联语义正确。
- **证据（a1）**：Stage2 canonical basis 是 `EvidenceQuote{segment, quote}`——该段
  属条目自身 source_refs 且 quote 是段全文（compiler 派生；canonical 直通面仍按
  `verify_quote` 逐字复验，不模糊匹配）；2026-10-07 起三方 wire（book v7→v8／
  chapters v7→v8／names v5）的 `evidence_refs` 只给段 id，引文由 `resolver.basis_quote`
  按段全文切出；`Effect.modality` 闭集必填。CS-S 1a（2026-10-07）：book／chapters
  wire 升 v8——chapters wire 每拍只写单段 `start`（拍覆盖区间由代码派生：
  `[start, 下一拍 start−1]`、末拍到章末、start 数值严格递增、首拍只允许跳过前导
  e00 引子段），`who`→`on_stage`，SlimEffect 删 `scope`／`hidden`／`prop`
  （404 效应实证 0 命中）；book wire 删 `InitialFactWire.knowledge`（初始知情
  改由 Phase 2 的 facts `known_at_start` 承载）。Blueprint v15 派生证据窗口
  （被引段全文＋同章前后各一句、重叠机械合并）；Preserve 输入从整份 JSON 倾倒
  改为 `project_preserve_view` 确定性渲染（R10-0.3 实证 29.9k token/轮）。
- **锚定与 series（a2）**：义务以 `anchors` 引用投影句柄（`b拍.e|c|l`／`s0_*`／
  `wc_*`，≥1），lineage 由 compiler 派生；锚点类型化拷贝源原子（持有者、mode、
  命题、modality、可见性、逐字引文）。顶层 `series` 登记同一机制的离散兑现
  时点（members≥2 闭集互斥、mechanism 属源表达）——不为满足阶段规则合并。
- **证据冲突回派（a3）**：Preserve 报 `evidence_conflicts` 时 checkpoint 不
  发布，按锚点句柄回派 Stage2 语义 owner（拍句柄→逐章 chNN／书级→book；
  不可解析即停）。三分支：修订（发布新 SourceTruth、按材料变化恢复）／保留
  解释（处理结果作为同一 Preserve 任务 revise_self 的恢复输入，仍分歧即停止
  不二次回派）／未决（保持阻塞，不自动标源文未知）。同指纹重现无进展停。
- **锁定项与 Design v5（a4）**：Brief 由 anchors 与 canonical loops 确定性
  派生 kp 命题登记、知情锁（唯一映射 learns/confirms→known、suspects→
  suspected、hears_claim→heard、believes→believes；asserts 转公开声称标记）、
  kf 事实锁（时态／值未知／原文未回答）；锁内容是语义、坐标仍源盲。Design
  v5 删 `dramatic_intent`；非知情/非读者 set 必填 modality；拍增
  `expression_refs`（正向表达任务落具体拍）。kp 键规则、kf 锁标注（CS-F2：见上「事实锁引用」，
  不再是状态键）与必保锁兑现归 `assemble_body_design`（revise_self ≤2）。
- **承载位（a5＋CS-3 bind 退役）**：`str06.stage4.bind` 节点与
  stage4-bind-v1 wire 协议退役——义务落在哪一拍由 design wire（v6 起，现役 v8）的
  `real:["obNN"]` 标注唯一声明：末次标注拍＝接受拍、其余标注拍＝支撑位，
  `derive_semantic_binding` 派生 SemanticBinding（series 成员接受拍互斥，
  同拍 `series_collapsed` 并入 design 同节点修订轮；无标注的非必保义务进
  unbound，义务不删）。body_design 同节点接受校验：必保义务 real 覆盖闸
  （漏标即拦）、派生错误并报同一修订轮。`semantic_binding.json` 的唯一
  writer 是 `str06.stage4.body_design`（双产物同 bundle 同 seal 发布）。
  compile 保留全书级锁位置 Guard
  （`novel/stages/stage4/guard.py::lock_order_violations`）：kp 知情升级/读者确认不得早于授权该
  模式的义务承载位置（授权位＝必保义务全部承载点最早者，支撑拍算承载；
  initial_state 与自由键不查；故事发生顺序不查——顺序三分）；违例按
  `epistemic_lock_order` 回派 `str06.stage4.body_design`。
- **回答复用（§二.4）**：模型输入未变→复用；验收条件变化→免费机械复验；
  实际写作材料变化→按请求身份判断（材料变化＝新身份，含冲突处理结果附注）；
  仅审计/展示变化→不触发付费重写。

旧候选根按版本关闭式拒识（协议不兼容，不计语义检出）；R10b（WriterIR
投影与写手协议 v7）、R10c（付费无人工 patch 全书）已交付，R10d（普适性）见
`docs/REQUIREMENTS.md` CS-G；R10 执行指南 PLAN.md 已退役（原文见 Git 68b5802f，裁决见
`docs/DECISIONS.md` 2026-10-01、2026-10-05 行）。



## 目标总览：职责与数据流

保留现有心智模型（Stage2 源语义 → Stage4 目标设计与确定性编译 → Stage5 语言实现 →
Stage7 命名交付），四类语义真源不变：Characters、Blueprint、TargetDesign、
SemanticBinding。Stage2 的观察与关联决定是付费的内部材料（归已购恢复证据，不是普通
缓存）；状态、执行计划、WriterIR 为可重建派生物。

```text
Stage2  book（书级观察）──┬── chapters（逐章观察，并行）── associate（跨章关联）──┐
                         └── names（名称结构）─────────────────────────────────────┴→ compile
                                                                  → Characters＋Blueprint
Stage4  Preserve → body_design（Design＋SemanticBinding 双产物）→ Hook Design → compile → 自足 ChapterIR＋HookContext
Stage5  各章与 Hook 并发成文 → 独立验证、提交
Stage7  Naming → ApplyNames → 按阅读序组装交付
```

| 角色 | 负责 | 输出 |
|---|---|---|
| Semantic unit | 理解原文、设计目标故事、判断同一性与关联、选择语义承载位置 | 独立版本的语义响应 |
| Resolver | 定位逐字证据、查找已提供引用、执行明确关联决定 | 已解析对象或结构化歧义 |
| Compiler | 生成 canonical ID、引用拓扑、派生字段、预算和消费投影 | Canonical 或 derived artifact |
| Continuity | 按明确 transition 顺序执行状态变化 | 可重算的 typed state |
| Validator | 检查输入与不变量并报告 | 只读验证结果 |
| Runtime | 外发、额度、槽位、尝试、取消、证据与恢复 | 执行记录 |
| Repository | 路径、字节、原子持久化与并发写入保护 | 已持久化数据 |

输出保留 N 个非空目标章与连续章号（I-40、`R-5.2`）；Design 决定场景归章，源情节允许
跨章重构。`R = Compile(D,G)`，`T₀ = Init(R)`；compiler 不读 SourceTruth 补猜目标状态，
Binding 不维护第二份初始态。

## Stage2：先取得语义，再建立 canonical（目标五节点）

| 节点 | 付费 | 作用域 | 输入 | 输出 |
|---|---|---|---|---|
| str06.stage2.book | 是 | 书 | 全书原文（含 e00 引子句段）与证据句段清单 | 人物候选（可读 slug、称谓及证据）、初态、世界约束 |
| str06.stage2.chapters | 是 | 章（并行） | 本章句段、第 1..k-1 章原文、书级人物表、抽取协议 | 有序事件及事实/认知/悬念转移、场景边界、时间模式、证据；只含章内局部句柄 |
| str06.stage2.names | 是 | 书 | 书级人物表与全文 | 名称结构（家族、名、化名、称谓构词） |
| str06.stage2.associate | 是 | 书 | book 与 chapters 观察的句柄及最小文本 | 同一命题、同一状态维度、悬念开合配对、跨章因果、跨章续场；只选已给句柄 |
| str06.stage2.compile | 否 | 书 | 以上四份 | 解析证据、生成 ID、执行关联，以同一提交记录发布 Characters＋Blueprint；源 fold 读数 |

1. **编号归 compiler**：拍号、状态与约束编号、命题/loop/场景 ID、章序、offset、hash
   及登记表可推导的作用域一律由 compiler 生成；下游 prompt 继续显示可读名。人物 slug
   仍由模型给出——它是可读语义句柄、无法机械推导（I-20/I-21）。
2. **证据**：证据段＝源文一个非空自然段（RC-26），沿用句段 id 闭集选择；需要字面锚处
   （称谓、原话）附逐字引文，由 Resolver 在所引句段内定位（basis 的所在段由引文定位，
   RC-27）；重复、不存在、冲突定位产生精确 issue，不模糊匹配、不猜笔误；称谓锚点派生
   沿用 `R-2.10`。
3. **章观察互不依赖（D3）**：保留前文原文；去掉前章快照、命题/loop 登记表与 beat 表。
   前章编译或关联失败不改变后章请求；缺失单元恢复不重购已存结果。
4. **关联（D8）**：只建立同一性与引用，不移动事实成立、角色知情或读者揭示时点；缺项、
   歧义、冲突保持未决，不默认合并或默认不同。存在未决项时 compile 拒绝发布并按 item
   报告，恢复走 owner patch 或 associate 的定向修订。
5. **发布（D9）**：compile 是 Characters＋Blueprint 两份真源的唯一 writer；下游只读
   canonical；`stage2/compiled/book_extraction.json` 派生输入退役，Stage4 的人物 core
   由 Characters 投影。
6. **读数**：源 fold 只消费 canonical transitions；未揭示 concealed、空转移拍比例等
   读数保留（度量，不是门，`R-2.17`）。

现役 v14 契约（保留拍三类转移 effects/cognition/loops、场景边界、`temporal_mode`，并增加源表达，见
`contracts/schema/source.py` 与 `R-2.3`/`R-2.12`）是五节点重建的起点。B2 前半已落地：
观察与关联协议（`contracts/schema/observation.py`，三类观察 `stage2-observation-v1`；
R1 起 associate 独立版本——v2 typed 目标显式序列化＋状态句柄位置编码，CS-H H4 起
`stage2-associate-v3` 同维状态改配维度句柄 `dim{nn}`，见 `docs/schema/stage2.md`）、
Resolver
（`novel/stages/stage2/resolver.py`）与节点/产物注册（RC6；associate 观察形状
R1 升 rc9、H4 升 rc17）；associate／compile
的引擎与 Stage4 改读 canonical 已随 B2 第三步闭合。

**CS-S 观察面收敛路线（2026-10-07）**：Stage2 观察面按 Phase 收敛——wire v8
（`start`／`on_stage`、区间代码派生）→ L1／L2 死字段清除（rc18）→ names A＋B＋C
（候选先行的条件窄调用＋构词模板 compiler 派生）→ facts＋knowledge transitions＋
associate 条目级窄 resolver（rc19）→ closed change keys＋拍级 access＋reader 派生。
观察层是抽取／消歧／编译输入，不是第三套 canonical（S2-8～10 与批次进度见
`docs/REQUIREMENTS.md` 的 CS-S 节；裁决见 `docs/DECISIONS.md` 2026-10-07 行）。

## Stage4：把章依赖编译完整（目标）

**落地状态（2026-09-27，B3 前半＋后半，rc7）**：四协议独立版化与 Design 瘦身已落地
（`STAGE4_{PRESERVE,DESIGN,BIND,HOOK_DESIGN}_PROTOCOL`，wire 顶层
`protocol_version` 逐字对账，漏带/错带即拒）；场景/拍 ID（`sc{序:02d}`／
`b{序:02d}`）、归章切片、复合锚定引用、权重归一与整数预算由 compiler 生成
（`novel/stages/stage4/design.py`，I-21）；TargetDesign 结构化跨章字段
（book_style／character_voices／standing_goals／ongoing_actions／open_questions／
promises）落地，书级条目随 BookBrief 进每章请求（rc10 起四个静态列表已删、跨章
语义改走拍上线程，见下「跨章线程与章缝合」节）；读者确认必填事实地址（D4，
schema 构造期 + assemble 引用闭合），投影按地址精确到消费该地址的章包；
预算传导机械规则 `chapters_with_budget_change` 落地。**B3 后半已落地**：
状态读取定位到入场或具体拍（`StateRead.at`，wire 给 1-based 拍序由 compiler
解析），按「先读旧值、后写新值」解析（`novel/stages/common.py::scene_read_views`
是 compile 章闭包证明与写手投影的唯一实现，取消「本场会写该地址就删除全部
入场读取」）；T₀ 保留 unknown／unset／set（含合法空值）与未建立的区别
（`novel/stages/stage4/compile.py::init_establishments` 不再按 op 过滤）；
compiler 按阅读序折叠逐场证明已声明依赖的引用闭合；Hook 设计与唯一 graph
publisher（`novel/stages/stage4/hook.py`）与 HookContext 编译
（`novel/stages/stage4/hook_context.py`）已归位 Stage4 包。

- Preserve、Design、Bind、Hook Design 各用独立协议；模型只表达选择、结构和相对篇幅
  重点，场景/拍 ID、复合引用、权重归一与整数预算由 compiler 生成。协议瘦身（不回显
  输入、不写可派生字段）以 Design 为首要对象——Design 是整书一次调用（全局归章与跨章
  重构需要整书视野），也是最可能触及 128K 上限的节点（现役实测 design 单次 completion
  约 6.1 万，见 `docs/REQUIREMENTS.md` token 量级表）。
- TargetDesign 正式承载整书风格、稳定人物声线、持续目标、进行中行动、悬念与承诺等
  跨章依赖；后文需要记住的事实必须进结构化字段，不能只写在导演说明里。
- **读者确认必须声明它确认的事实地址（D4，必填，漏填即拒）**；compiler 把未确认项
  投影进引用该地址的章包，并标注确认拍（现役 `R-6.11` 的逐场全量清单升级为按地址投影）。
- 状态读取定位到入场或具体拍，按「先读旧值、后写新值」解析；「本场会写该地址就
  删除全部入场读取」的旧行为已取消（B3 后半，`novel/stages/common.py::scene_read_views`）。
- 初态保留 unknown、unset、未建立与合法空值的区别，非 set 初态不再无条件丢弃
  （B3 后半，`novel/stages/stage4/compile.py::init_establishments`）；Design 词汇同时
  分开「值未知」与「人物不知道」（后者用知情缺席或 believes 表达）。F5e「用 unknown
  初态垫底、毒化 fold」的失败保留为回归反例（`tests/test_str06_chapter_closure.py`）。
- Hook 设计与唯一 graph publisher（`novel/stages/stage4/hook.py::publish_graph`）、
  HookContext 编译（`novel/stages/stage4/hook_context.py::compile_hook_context`）
  的代码已迁回 Stage4 包（B3 后半）；Hook 语义边界不变（见现役实装一节）。
  wire 接受与正式产物重编译共用 `novel/stages/stage4/hook_validation.py` 的纯判据；
  上下文编译器依赖 validator，不反向依赖 Hook 装配与发布模块。
- 章预算保持全书最大余数分配（现役
  `novel/stages/stage4/book_ir.py::allocate_chapter_budgets`），总和恰等于书级目标；
  代价是权重变化会波及多章预算（见精确失效表；机械规则
  `novel/stages/stage4/book_ir.py::chapters_with_budget_change` 已随 B3 前半落地）。

### ChapterWriterPacket（三件套；B4 落地）

```text
ChapterWriterPacket
├── BookBrief：整书风格、稳定人物声线与表达规则（不含导航、其他章内容与称谓总表）
├── ChapterIR
│   ├── 章目的、预算、入场状态与承接（上一章结束态、本章开场所需）
│   ├── SceneIR：事实约束＋导演说明
│   ├── 人物、知情、持续目标、进行中行动与悬念依赖
│   ├── 尚未向读者确认的事实（按地址投影）
│   └── 称谓许可、禁用字面与验证材料（compile 时算好）
└── RealizationContract：写作协议与交付要求
```

**落地状态（2026-09-27，B4 前半）**：`contracts/schema/book_ir.py::ChapterWriterPacket`
（rc8）三件套落地；章级验证材料（`ChapterIR.released_surfaces`／
`withheld_literals`）由 `novel/stages/stage4/book_ir.py::compile_writer_book_ir`
在 compile 时算好（projection/structure 为必填参数），推导唯一产地随 Stage4
编译器；投影唯一产地 `novel/stages/common.py::project_chapter_packet`，摘要
`packet_sha256` 覆盖章包全部内容（含验证材料）进请求身份与章级回执。

完整 WriterIR 保留供存储与审计；运行时确定性投影章包，不另落重复章包文件。Compiler
证明**已声明依赖的引用闭合**；语义依赖是否遗漏归质量验收，机械检查不冒充证明。

**称谓投影（CS-F2，RC-40～43，IR rc16／renderer v10）**：`ChapterIR.withheld_bindings`（`WithheldNameBinding`：
字面＋人物＋包含它的更长称谓）是禁用字面中已证明唯一绑定人物的**完整登记称谓**子集——登记 surface、含姓名部件
（构词非 null）、全书只属一个人物（归属统计包含纯称谓登记）、长度≥2、非姓／家族构词（`novel/core/name_materials.py::withheld_name_bindings`）。
renderer 在正向材料行（含禁区行）把这类字面换成本场已释放称谓（`novel/stages/common.py::project_withheld_names`：绑定字面与其
更长称谓一起长名优先认领，只替换绑定字面）；投影只依赖绑定表与本场称谓，不依赖禁用／许可字面，故二者仍是纯验证材料。
原字段与来源文本不变（归因仍按原字段），六分区（含 `guard_only`）的替换留痕于 `name_projections`（候选预检报告、guard report、packet_metrics），
不含行文本；人物本场没有已释放称谓时不投影，泄漏如实保留并按原归因回派。短姓、关系称呼、多义字面、未登记表达
（如源文没有的「陆郎」）与源引文永不投影；Design 自有文本的名称冲突仍由 Design 接受判据拦下，compiler 不自动提前释放全名。
写手视图摘要含投影后的渲染字节，验证输入摘要含绑定表。**Hook 同尺投影（rc16，2026-10-05）**：释放面由 Hook Design
自选（`surface_releases`，非章释放表的投影），`compile_hook_context` 按本场释放算好 `HookContext.withheld_bindings`
（同一把 `WithheldNameBinding` 尺，随 `stage4/context/hook/hook.json` rc16 携带），`render_hook_context` 在正向材料行与
禁区行做同一投影——上下文字段保持原文本，条目返修的来源归因仍按原字段核对（`_hook_source_matches`），投影只改送达
写手的字节；留痕 `hook_name_projections`（field／literal／char_id／replacement）进 guard report。concealed 人物不可
释放面，绑定存在也无显示面，不投影、泄漏如实报告。

### 两层 IR 与送达方式（R2 基础，R9 rc11）

**Machine ChapterIR**（`contracts/schema/book_ir.py`／`context.py`，照旧落
`stage4/ir/writer_book_ir.json`）以完整为先：ID、地址、来源（`established_by`）、
依赖边、Guard 材料全在，供审计与 Stage7 核对。**WriterPacket**
（`contracts/schema/context.py::WriterPacket`，I-11 唯一 canonical）是写手层章包：
按 `delivery_mode` 分节、全部自然语言条目、不带内部 ID 与字段名（`sc01#b02` 式
引用、`th{nn}`、工程字段名出现在任何分区即构造期拒），六个分区全部必填、空分区
合法（I-14/I-42）；从不落盘（派生视图，I-16），其渲染字节进章回执的写手视图摘要。
由确定性 renderer v7（`novel/stages/common.py::build_writer_packet`）从 Machine IR
加送达登记生成；`render_chapter_packet` 渲染六分区——必须写出／不可违背／承接／
导演说明／背景／禁区——不再把 VerificationIR 以 JSON 倾倒进 prompt。Hook 渲染
（`novel/stages/stage4/hook_context.py` 与 Hook 写手协议）同规则分层：材料是
必须写出、禁止揭示与受保护线程是禁区、起笔事实是延续或背景，`withheld_literals`
不进阅读面。

送达方式七档身份登记在 `contracts/schema/consumption.py::STR06_FIELD_DELIVERY`
（R0，见「字段级送达登记」节）；同一字段可按投影规则逐场换身份，线程的分支条件
（E-4）是机械的：先判本场操作→`must_realize`，再判保护类（question）→`guard_only`，
其余→`continuity`；按参与者相关性过滤，不相关不投影
（`novel/stages/stage4/guard.py::expected_thread_faces` 是 M8 对账的机械执行面）。

**背景投影收窄（R10b）**：写手面背景只投书级初态立足点＋建立之后仍被
消费的当前状态（消费者＝声明读取/读者确认目标/Hook 地址/锁键；R-6.16）；
unset/unknown 与无消费者拍状态不投影（留在 Machine IR 与拍级「必须写出」）；
依赖边生成仍消费全量参与者基线（`participant_background`）。
**编译器补齐的背景**：`project_context` 按参与者机械补入入场状态（人物域、参与者
之间的关系域、持有者为参与者的知情域）进 background 分区；声明读取解析过的地址
不重复补。写手协议 v7 明写「背景事实是约束，不是写作任务；除非场景自然需要，
不复述」。拍级读取解析值挂到对应拍（`BeatIR.reads`，AUD-03）以 `must_respect`
呈现「本场依赖的事实」；Guard M7 钉住每条声明读取已解析并进入 IR。

**身份拆分（rc6）**：章回执分别绑定写手视图摘要（`writer_view_sha256`：renderer
与写手协议版本＋渲染字节）和验证输入摘要（`validation_sha256`：禁用字面、称谓
许可、篇幅带）；Machine 摘要（`machine_view_sha256`）仅审计——只改 Machine 层
字段不触发重购。`novel/stages/common.py::chapter_identities` 是唯一产地，
Stage7 核对与 chain 恢复集合（E-7）同批同步。

**packet_metrics（派生读数，不是门）**：compile 写
`stage4/acceptance/packet_metrics.json`（`novel/stages/common.py::packet_metrics`
纯函数），逐章记录写手视图字数、各 `delivery_mode` 条目数、基线事实数、活跃与
受保护线程数、世界规则数、相邻章延续与背景重复比例、目标篇幅与「必须写出」密度；
送达登记里标 `audit_only`。

### 跨章线程与章缝合（R2）

跨章语义都是**拍上的转移**（设计原则 4）：状态走现有叠加折叠，线程走独立
ThreadFold，不留静态列表。`TargetBeat.threads` 与源侧 `LoopAction` 对称：

- `open` 带 kind 闭集（goal／action／question／promise）＋主体＋文本——文本只在
  open 写一次、question 不写答案；
- `advance` 带进展说明，不改写声明内容；
- `close` 带按 kind 限定的结局闭集（`contracts/schema/design.py::THREAD_ENDINGS`）
  与简短收束。

wire 用模型自选本地标签（open 带 `tag`、advance/close 带 `ref` 引用），compiler
按 open 先后派发 `th{nn}`（I-21）；生命周期闭合（open 一次、操作不早于 open、
close 后无操作、结局在闭集内）由 schema 构造期校验，主体人物闭集由 assemble
校验。rc7 的四个静态跨章列表（standing_goals／ongoing_actions／open_questions／
promises，AUD-04 无消费者）已删除，wire 旧键关闭式拒读（I-13）。

**折叠与投影**：`novel/stages/stage4/fold.py::fold_thread_faces`（共用
`novel/stages/stage4/guard.py::scene_order` 阅读序，先快照后重放）把 `thread_ops`／
`open_threads` 投进 VerificationIR；M8 生命周期段＋投影段对账。呈现分三种身份：

| 情形 | 身份 | 写手看到的意思 |
|---|---|---|
| 本场有该线程操作 | `must_realize` | 这一场推进或收束它 |
| 开放中、与参与者相关、本场无操作 | `continuity` | 仍影响人物选择，不必点明 |
| 开放中的问题线程 | `guard_only` | 不得提前回答，也不主动提醒读者 |

全场景保护不等于每场提醒；Hook 的禁区同样把未到时点的线程 close 机械并入
`forbidden_reveals`（E-3 读法 b：compiler 并入、不动 wire、不升 hook 协议）。

**章缝合**：`ChapterAssignment.entry` 必填、第 1 章固定开篇、编译器不猜（裁决 7）——
`transition` ∈ {immediate_continuation, time_skip, location_cut, pov_shift,
new_scene}、可选 `carryover`、`recap` ∈ {do_not_recap, brief_orientation}。
`novel/stages/stage4/book_ir.py` 把 `entry` 与上一章末场的机械投影
（`PreviousChapterEnding`：驱动转折、末拍事件、末场锚、末拍在场人物——Design
无地点字段，地点语义不由 compiler 猜）合成承接分区，例如「上一章收在……；本章以
时间跳跃开场，不复述上章结尾」。

**世界规则**：`novel/stages/stage4/grounding.py::project_brief` 把 Blueprint 的
world_constraints 机械填入 `ReconstructionBrief.book_constraints`（世界约束通道
恒空即红，AUD 反例已转绿；rc13 起带源稳定编号 `wc_{nn}`——`BookConstraint`
结构，design wire 的 world 规则断言以该编号引用，渲染面经映射送命题文本）；
Design 侧决定沿用或改写成目标 `world_rules`（BodyDesign 书级字段），随
BookBrief 进每章，以 `must_respect` 呈现「作为世界边界，不要讲解」。

### 自愈：typed issue 路由与有界修订（R3）

系统自愈**只修 typed、可定位的机械问题**（schema、闭集、锚点、引用闭合、关联
未决、必保义务未承载这类）；文学质量永不进入自动修复（裁决 1）。

- **路由表**：`contracts/schema/workflow.py::OwnerIssueV1` 与
  `OWNER_ISSUE_ROUTES`（code → `revise_self`｜`revise_upstream:<节点>`｜未登记
  即 stop；起点复用 `novel/stages/stage2/resolver.py::ResolverIssue` 四码）。
  `unbound.owner` 轴经 `UNBOUND_OWNER_ROUTES` 闭合，未映射即停（E-8）。
- **左移校验**：章观察的人物闭集在 `novel/stages/stage2/chapters.py` 验收时就查；
  associate 验收时做一次 compile 干跑，未决项作为它自身的问题（compile 侧拒绝
  发布保留为防御）。
- **同节点修订**：`novel/llm/paid_requests.py::ask_json` 在「非空但不过闸」时发
  修订内容轮（原请求＋上一版＋typed 问题），每单元最多
  `contracts/invariants.py::OWNER_REVISION_MAX_ROUNDS`＝2 轮，同一问题集重复即停；
  Runtime 的 `send` 仍不收业务回调。
- **跨节点回派**：`run_compile` 的关联未决 → associate；`run_compile_stage4` 的
  必保义务未承载 → 按 `unbound.owner` 回 body_design 或 preserve。下游写
  `runtime/issues/<owner>.json`；`novel/orchestrator/state.py::resume_verdict` 把
  上游判为 `owner_revision` 修订重跑，operator redo 按登记策略放行——普通队列对
  redo 仍是关闭式停止，不放宽（`R-14.4`）。
- **可观测**：修订轮计入整书总闸与 ledger；run 视图 `owner_issues`
  （`novel/views/str06.py`／`contracts/schema/api.py::Str06OwnerIssuesView`）投影
  轮数、回派账与停止原因，前端 str06 监控屏并排展示。无进展按 item 停下；崩溃后
  恢复免费。故障注入反例见 `tests/test_str06_r3_selfheal.py`。

## Stage5/7：独立实现与集合恢复（B4 前半已落地）

- **已落地（2026-09-27，rc8）**：Writer 与 Validator 只收当前章包
  （`novel/stages/stage5/body.py::write_chapter` 的唯一输入是 `ChapterWriterPacket`），
  不读前文正文、Design、Characters 或其他章 IR（D2）。Stage5 只做语言实现；新增跨章
  事实没有机械检测手段，按 `R-7.6` 扩充未评披露（转场、跨章无名细节、声线归 B6 阅读）。
- **已落地**：各章独立执行、验证、提交；`str06.stage5.body` 待补章经
  `novel/model_slots.py::pipeline_model_slot` 共享书级与供应商总槽并发执行。
  默认是书级 4、供应商 8、节点 4；本次冻结的 `PipelineConcurrency` 可以把
  可并发节点和两个总槽改成同一整数区间内的值。节点并发由
  `novel/config.py::concurrency_for` 按节点读出，不进语义身份。Hook 仍是独立节点
  （`str06.stage5.hook`）。
- **已落地**：恢复按有效提交集合（`novel/stages/chain.py::_committed_set`）：已完成
  1、2、4、6 章时只补 3、5；局部失败保留其他合法结果，授权、账务或取消故障以
  原始类型关闭相应准入。
- **已落地（2026-09-29，R5b/D3）**：逐章失败留证按「**最近一次完成执行**」语义
  落盘，正文与章观察（`str06.stage2.chapters`）共用一个形状（I-11）：
  `{requested, attempted, chapters, recorded_at}`——`requested` 是本次请求的章范围
  （正文＝调用层选章 `--body-chapters` 或全书），`attempted` 是实际进入逐章执行器
  的章（正文不含已提交章），`chapters` 是 `attempted` 的失败子集。正常完成写一次
  空清单、带失败完成在上抛前写一次、崩溃不写：**文件缺失＝该节点还没有一次完成
  执行**（与合法空清单是两件事，I-42）。选章只作调用元数据（记入
  `runtime/workflow_status.json` 的 `body_chapters` 审计字段），不进请求身份、
  章回执三把摘要、provenance 或契约身份——从 canary 续跑到全书不改变已提交章
  的采用与恢复身份（W1）。
- **已落地**：每章首次＋最多两次内容重写保持；截断不再触发重写（D6，见执行设施失败表）。
- **已落地**：章回执（rc6 身份拆分，R2）绑定写手视图摘要（renderer＋写手协议版本＋
  渲染字节）、验证输入摘要（禁用字面／称谓许可／篇幅带）、请求身份与改名前正文摘要，
  不再绑定全书 IR 与前文链（`R-7.4`、`R-10.3` 已改写；Machine 摘要仅审计）；Stage7
  逐章核对回执与当前编译出的写手视图／验证输入摘要一致
  （`novel/stages/stage7/assembly.py::verify_chapter_receipts`）、集合齐备，
  再按阅读序组装交付。
- **已落地（2026-09-28，B4 后半）**：Stage7 集合核对升级为五态按 item 报告（I-42）——
  `run_assemble` 逐章装载盘上回执与正文原样（`inspect`，不经 `_committed_set` 预过滤），
  与当前编译出的章包摘要逐章核对（分类与逐条问题共用
  `novel/stages/stage7/assembly.py::_binding_problems`）：有效／缺失／拒绝／未知／
  失效（stale＝接受态回执但章包换版或正文脱钩，须重做、不是缺失）分开列进
  `runtime/delivery/assembly_report.json`（收尾批起随 `Str06RunView.assembly_report`
  直投影进监控面，五态与逐条问题原样转出）；not_ready 不落新交付、旧 manifest 原样。
  乱序完成得到相同有序交付（组装按阅读序加章标记、身份按有序
  `(chapter_ordinal, prose_sha256)` 派生，并发完成顺序不影响交付字节）；Hook 失败停
  在 `str06.stage5.hook`，章提交与已交付字节原样保留，恢复输入后全链零新购复原。
  顺带修复 Hook 拒绝回执写路在付费 provenance 闸下必被 `ProvenanceRefused` 拦下、
  掩盖 typed 失败的缺陷（拒绝回执现引用实际 attempt；无已应答 attempt 不伪造写盘）。

## 执行设施（目标；B1）

**落地状态（2026-09-27，B1 完成）**：身份与记录层已落地——五层记录对齐工作单元账
（`WorkUnitAttempt` 增 attempt 级响应引用；`AcceptanceRecordV1`／`CommitBundleV1`
落 `contracts/schema/workflow.py`）、Runtime 改耐久响应引用
（`novel/llm/paid_requests.py::DurableAnswer`／`send`，错误统一
`ExecutionError` 带 stage/node/item/phase/code/context）、D5 空回答救回与同轮
重试、provenance 显式引用纯函数构造（`AdoptionRef`＋
`CostLedger.usage_for_attempt_rows` 精确 attempt 行聚合，删共享窗口与时间窗
归账）、CommitBundle 封存落候选根 `runtime/commit/`（采用状态从
seal 派生）、候选根 request/validation/admission/adopt 四类写路按映射表退役、
额度按 attempt 计数。**B1 后半**：截断即停（`send` 对
`finish_reason='length'` 统一抛 `code='truncated'`，缓存命中同样停止，不重试、
不加预算；`max_tokens` 统一 131072＝`LLM_MAX_OUTPUT_TOKENS`）、
ExecutionProfile run 启动冻结（`novel/stages/chain.py::ExecutionProfile`／
`freeze_execution_profile`；`load_config()` 的唯一执行期调用点是 `execute`，
`assemble_paid_boundary` 只消费冻结 profile；effort 取值面 low/high/max，
CLI 的 medium 移除；整书上限从持久准入账派生传入付费边界）、并发登记改
新链逐章节点（B2 起 `PIPELINE_PARALLEL_NODES＝('str06.stage2.chapters',
'str06.stage5.body')`，旧链键与四个 `STAGE*_MAX_CONCURRENCY` 常量删除；
槽位运行接入已随 B4 逐章并发落地：`run_body` 待补章经
`novel/model_slots.py::pipeline_model_slot` 共享书级（4）与供应商（8）总槽）。
反例与完成条件见
`tests/test_str06_runtime_records.py`（17 项故障注入）。

### 身份与记录：对齐现有工作单元账，不另起第二套

| 层 | 含义 | 落点 |
|---|---|---|
| SemanticRequest | 业务任务、输入投影与语义协议 | 工作单元 (node, scope)＋请求身份 |
| ContentRevision | 首次生成或针对明确问题的修订；owner patch 是无外发的修订（绑定基线请求身份与修订说明） | `contracts/schema/workflow.py::WorkUnitContentTurn`，内容轮额度沿用 |
| ProviderAttempt | 一次实际外发：冻结配置、准确请求、原响应、成本 | `contracts/schema/workflow.py::WorkUnitAttempt`，每轮至多 4 次（`contracts/invariants.py::WORK_UNIT_MAX_DELIVERY_ATTEMPTS`） |
| AcceptanceRecord | 解析、救回、引用解析、编译、机械验证分项结果；语义恒 `unver`；`accepted` 只表示“通过 owner 完整验收、可发布”，被拒或被回派上游的回答记 false 并带问题，是否被采用只看 CommitBundle（见 `docs/schema/work_units.md` 接受记录与发布语义） | 取代候选根的 validation 记录 |
| CommitBundle | 输入清单、采用证据、输出摘要与 seal | 新增；采用状态从 seal 派生 |
| OwnerRevisionRecord（V2） | owner_patch／resample／**carried_forward** 三类修订凭证；V2 增必填 `applies_to_request_sha256`（记录应用到的请求身份），v1 经 `owner_revision_record_of_payload` 集中迁移（删除条件见 REQUIREMENTS 兼容层表）。carried_forward＝免费沿用的采用凭证：`baseline_*` 是候选生成时的原请求/响应，落 `runtime/revisions/carried/<owner>/<身份>.json`，成本链 ledger-row 指向原 attempt | `contracts/schema/workflow.py`；原 `runtime/adopt_*` 的迁移面上浮 |
| 候选登记 | 该单元全部已购候选（含补丁链合并）的索引：wire 原字节摘要、生成请求身份、采用引用链、上游版本摘要、最近一次预检摘要；**可由工作单元账与响应 blob 重建**，不是第二本账 | `runtime/candidates/<owner>.json`（CS-H H1b，RC-52） |

- 原响应按 attempt 存储并被 attempt 引用，**空回答不再占据请求身份**。候选根现有
  request、admission、validation、adopt 四类运行记录的逐项映射与删除条件登记在
  `docs/REQUIREMENTS.md`「候选根运行记录映射与删除条件」，B1 执行；旧输出根与已购
  证据只读保留。
- Runtime 返回耐久响应引用，不再接收业务 validate 回调；错误统一带
  stage/node/item/phase/code/context。
- provenance 由纯函数按显式引用构造，移除
  `novel/llm/paid_requests.py::CandidateRunContext` 的共享进程内窗口；成本只从 ledger
  的精确 attempt 行聚合（I-17：成本唯一权威是 `<base>/_service/ledger.jsonl`）。

### 失败分类与恢复

| 情况 | 行为 |
|---|---|
| 未送达：连接/DNS/TLS 失败、连接超时、HTTP 429、5xx | 同一内容轮内重试，首发＋最多 3 次，退避沿用 `novel/llm/retry.py::TransportRetryPolicy`（2/4/8 秒）；重启不重置 |
| 已送达后结果未知：读超时、送达后连接中断 | 记 unknown，核对原响应、工作单元证据与 ledger；证明不了就保持 unknown，不自动重试（改现役 `novel/llm/transport.py::_classify` 对超时的一刀切判定） |
| 正常结束但正文为空 | JSON 调用先免费救回（D5）；救不回或属文本调用，则记一次失败 attempt，在同轮内重试（占尝试额度，如实计费） |
| 截断（finish_reason=length，上限 128K＝131072） | 节点设计缺陷：停在该 item，记录 reasoning 与正文 token，不重试、不加预算（D6） |
| 解析、Resolver、Compiler、Validator 问题 | 免费重放；确定性缺陷修代码，不触发重新购买 |
| 语义缺失或表达错误 | owner patch（免费）或所属 semantic unit 的定向修订（付费，受内容轮上限与无进展停止约束） |
| 配置、协议、权限错误（含缺 key） | 明确终止，修复后恢复 |

### 额度、并发与配置

- 整书总闸（`contracts/invariants.py::BOOK_CONTENT_CALL_CEILING_DEFAULT`，持久账）在
  外发前按 attempt 计数，unknown 也计入；ledger 是成本唯一权威。单次运行上限从同一
  准入账派生，删除候选根 admission 的平行计数（现状只计成功应答、不计 unknown，与
  「unknown 不释放额度」不符）。
- 内容轮与物理尝试分开计额；拆出 attempts 不隐式扩大授权；缓存采用不耗额度；未知费用
  不计为零。
- ExecutionProfile 在 run 启动时冻结，经 CLI、Runtime、transport 实参、请求身份到
  ledger 全程贯通（已落地：`novel/stages/chain.py::ExecutionProfile`）：执行时重读
  `load_config()` 的通道已删除（唯一执行期调用点是 `execute`）；effort 取值面与
  NodeSpec 一致（low/high/max，CLI 的 medium 已移除）；`max_tokens` 统一
  `LLM_MAX_OUTPUT_TOKENS`＝131072；产品路径把 run 冻结的整书上限传到付费边界。
- 并发登记是新链两个逐章节点：`PIPELINE_PARALLEL_NODES＝('str06.stage2.chapters',
  'str06.stage5.body')`。节点并发、整书总槽、供应商总槽都取
  `PIPELINE_TOTAL_CONCURRENCY_MIN`～`MAX` 的整数；没写覆盖的节点保持默认。
  此前实测（旧 agent 驱动）32 并发大半章被秒退、3 并发正常；B6 报出 429 与重试计数。
  把槽开大仍受供应商限流约束，429 由既有重试处理。
- 每个付费节点的 reasoning 与正文 token 逐次报出；预期量级表登记在
  `docs/REQUIREMENTS.md`（B0），超出即回查节点设计（D6）。

### 提交顺序

原响应耐久保存 → 免费处理（救回、解析、编译、验证）→ prepare → 写入并核对输出 →
seal → 标记采用（可由 seal 重建）。任一窗口中断优先免费补尾。停止时先关准入，再排空
执行与提交，最后释放书级锁。`--check-only` 继续表示零新购复验，可执行恢复写入，文档
不得称为纯只读检查。

### 发布权威与读取校验（现役，R6 已落地；2026-09-29）

裁决 2 把统一发布权威前置于 B6，让 B6-A 真正代表「最终产品链第一次接真实模型」。
现役形态（AUD-05 已转绿：删掉全部 seal 后正式消费被拒，免费恢复后重新交付）：

- `novel/store.py::Str06Repository` 按**输出组**发布（`publish`＋`PublishItem`）：
  组内产物先铸账、渲染全部字节再落盘，全部逐字回读后写**组 seal**——输出组对应
  `NODES.produces` 上同 writer 的产物集合（Stage2 双真源＋fold、Preserve checkpoint＋
  brief、Stage4 免费编译四产物、每章正文＋章回执、Hook＋Hook 回执、最终 TXT＋
  delivery manifest）；单项写 `write` 是单项组。**免费产物同样封存**（免费 bundle 的
  `adoptions` 为空），不另立付费/免费两套发布语义；组内跨 writer 即编排缺陷 fail fast。
- `read_model` 对正式输入校验 seal 与摘要（`UnsealedArtifact`，显式终态 2）：没有
  覆盖当前字节的已封存 CommitBundle——未封存、seal 损坏（`sealed_commit_bundles`
  关闭式上抛）或摘要不符——即拒绝正式消费，失败结果只进诊断面、不冒充可交付；
  诊断 `inspect` 可展示未发布证据但不反向放行。编排侧候选桥
  （`novel/orchestrator/state.py::_candidate_body_design`）对未封存按未知章集处理。
- `run_compile_stage4` 等**验过才发布**：Guard 问题非空时四个正式派生产物
  （execution_plan／hook 上下文／写手 IR／packet 指标）一概不落盘，问题清单只进
  guard report 诊断；新协议没有任何已购产物，不需要补封或迁移层（I-13）。
- 崩溃窗口（组内产物已写、seal 未写）靠免费补尾：重跑所属 owner，付费段按请求
  身份重放耐久响应（`WorkUnitBudget.usable_response`）、免费段确定性重编译，零新购
  重新发布并封存（复用 `CommitBundleV1` 与 `sealed_commit_bundles`，无第二本账）。
  反例：`tests/test_str06_runtime_records.py`（免费封存／未封存拒绝＋免费恢复＋
  Guard 失败不发布正式位）。

## 精确失效（目标）

正文请求身份只绑定 Writer 实际可见的章包、ExecutionProfile、请求参数、协议与 renderer
版本；回执另绑验证版本、验证输入与正文摘要。

| 变化 | 影响 |
|---|---|
| 某章导演说明或其实际依赖变化 | 重编译后章包变化的章 |
| 场景相对篇幅（权重）变化 | 整数预算变化的章（全书归一，可能波及多章；`chapters_with_budget_change`） |
| 后章读者确认变化 | 只影响章包引用该确认地址的章 |
| Design 线程操作或章缝合 `entry` 变化 | 按消费面传导：操作章、携入章与主体相关的章；无操作且主体不相关的章不变（R2 变异守卫，`tests/test_str06_r2_engine.py`） |
| 第 5 章正文重写 | 第 5 章及最终组装 |
| BookBrief（整书风格、稳定声线）变化 | 全部正文 |
| 无关的未来剧情变化 | 当前章包与请求不变 |
| Validator 升级 | 免费重验已有正文；重验不过的章进入付费修订，需授权 |
| Hook 或 NameMap 变化 | 对应产物与交付 |
| ExecutionProfile（模型、effort、max_tokens）变化 | 全部请求身份，新购 |
| 并发度变化 | 不改变请求身份 |

## 现役实装

### RC 模块与路径（现役，R9 rc11）

| 模块 | 承载 | 关键不变量（构造期表达） |
|---|---|---|
| `contracts/schema/policy.py` | `PreservationPolicy`（版本化策略，现役 v4）、`PreserveKind` 闭集 | 类别集或判据变化必须升版；现役版本唯一为 `STAGE4_PRESERVE_POLICY_VERSION`；执行身份不进 Policy |
| `contracts/schema/grounding.py` | `SourceObligation` / `PreserveCheckpoint` / `ReconstructionBrief` / `IdentityProjection` / `OutputConstraints` / `SemanticBinding` | 必保义务必有非空 `satisfaction`；checkpoint 无 Design/Bind 字段；Brief 无源章/源拍/叙述顺序/证据定位字段；Binding 是完整 snapshot |
| `contracts/schema/design.py` | `BodyDesign`（rc11：narration、expression_decisions、可定位 entry；保留拍上线程、world_rules）／`PresentationGraph(BodyDesign)+hook` | source-blind；章号连续 1..N、场景全覆盖；POV 服从正文人称许可；表达决定恰好覆盖；entry 锚满足分支条件；hook 引用与线程生命周期闭合 |
| `contracts/schema/hook.py` | `HookDesign`／`HookContext`／`HookReceipt`／`HookConstraints` | 起笔事实只引用已建立目标事实；first 恰一人且非 narrator；回执 semantic 恒 unverified |
| `contracts/schema/naming.py` | `NameMapV5`／`NameAssignment`／`TargetName` | name 与 keep_reason 恰好一个；full 由 compiler 拼接；char_ref 唯一 |
| `contracts/schema/source.py` | Characters v4（`CharactersCore`＋`name_structure`）／Blueprints v14（拍三类转移＋场景分段＋带证据源表达） | 名称结构 surface 恰好覆盖、模板渲染逐字相等；canonical 逐字等于某个已登记 surface；from_chapter 由证据锚点派生（`R-2.10`）；命题引用闭合与同拍两遍解析（`R-2.12`）；登记键作用域由 compiler 绑定；表达编号及读数由 compiler 生成 |
| `contracts/schema/execution.py` | `ExecutionPlan`（establishment/constraint/dependency/reinforcement）与 `ExecutionSlice` | 必保只落 establishment/constraint；依赖边禁自指 |
| `contracts/schema/context.py` | ChapterIR 双视图（`VerificationIR`＋`WriterBrief`）与写手层 `WriterPacket`（rc11 六分区，带人称与表达） | IR 带 `beats` 逐拍定位（`BeatIR.reads` 挂拍级读取解析值）与 `prohibitions`、`thread_ops`／`open_threads` 投影；Brief 只承载呈现；两视图 scene_id 与 slice 身份同源；WriterPacket 全自然语言、机械拒识内部 ID 与未释放正向字面 |
| `contracts/schema/book_ir.py` | `WriterBookIR`＝`BookBrief`＋`ChapterIR[]`；`ChapterWriterPacket` 章包三件套 | BookBrief 确定性投影 world_rules、人称及表达决定，不调 LLM 总结或补造文风；章预算 largest-remainder 分配；验证材料 compile 时计算；`ChapterContinuity` 投影 entry、anchor_event 与前章末拍，不猜时地语义 |
| `contracts/schema/workflow.py` | Stage4 四协议常量、运行记录模型与 R3 的 `OwnerIssueV1`／`OWNER_ISSUE_ROUTES` | 未知 issue code 一律停；`unbound.owner` 轴经 `UNBOUND_OWNER_ROUTES` 闭合，未映射即停；修订轮上限 `OWNER_REVISION_MAX_ROUNDS` |
| `contracts/schema/receipt.py` | `ChapterReceipt`（章级回执，rc6 身份拆分） | 绑定章序、写手视图摘要（renderer＋写手协议版本＋渲染字节）、验证输入摘要（禁用字面／称谓许可／篇幅带）、改名前正文、实际请求身份；Machine 摘要仅审计（`machine_view_sha256`），只改 Machine 层字段不触发重购；不绑全书 IR 与前文链（D2）；只有篇幅偏差可在额度耗尽后披露放行 |
| `contracts/schema/consumption.py` | 六个消费投影族＋字段级送达登记（R0） | body_access 按章级有效回执判定；rename_materials 写明 Naming 只读投影与 Apply 唯一应用者；字段级登记见下节 |
| `contracts/schema/families.py` | 三级路径家族登记（16 个家族） | 来源账键=完整相对路径 |
| `contracts/schema/dependency.py` | 新链依赖方向登记＋per-stage 模块 owner 规则 | 由 AST 对真实文件执行（`tests/test_str06_hook.py`） |

**PreserveCheckpoint 接口声明（R4 批如实登记）**：`runtime/preserve_checkpoint.json`
的正式面就是 `contracts/schema/grounding.py::PreserveCheckpoint` 的全部字段，别无
隐式接口——`checkpoint_id`、`policy_id`、`policy_version`、`characters_sha256`
（绑 core view＝characters＋initial_state，名称结构修订不使已购 checkpoint 失效，
F6 裁决）、`blueprints_sha256`、`provider`、`model`、`request_sha256`（供同请求
身份历史采用与成本对账，I-37/I-43）、`obligations`（`obligation_id` 在 checkpoint
内唯一，I-31）与 `checkpoint_sha256`（规范化字节内容 hash，构造先占位再回填）；
`_provenance` 是落账排除项、不进盘面字段。它**不是下游读取接口**——下游消费
ReconstructionBrief／SemanticBinding；checkpoint 不携带任何 Design/Bind 字段。

引擎落点（现役）：`novel/stages/stage2/book.py`（book 观察 owner，B2 第二步）、
`novel/stages/stage2/chapters.py`（chapters 观察 owner，B2 第二步）、
`novel/stages/stage2/names.py`、`novel/stages/stage2/resolver.py`（证据定位唯一实现）、
`novel/stages/stage2/associate.py`（跨章关联 owner，B2 第三步）、
`novel/stages/stage2/compile.py`（双真源唯一发布 owner＋源 fold 唯一实现，
B2 第三步；旧联合抽取 `source.py` 已随 B5/S-05 整包删除，反例已迁五 owner 引擎）、
`novel/stages/stage4/grounding.py`（preserve＋Brief 投影＋binding 派生）、
`novel/stages/stage4/design.py`、`novel/stages/stage4/hook.py`（hook design 与唯一
graph publisher，B3 后半归位）、`novel/stages/stage4/compile.py`、`novel/stages/stage4/book_ir.py`、
`novel/stages/stage4/hook_context.py`（B3 后半归位）、`novel/stages/stage4/guard.py`、
`novel/stages/stage5/body.py`（按章 writer）、`novel/stages/stage5/body_checks.py`、
`novel/stages/stage5/hook_writer.py`、`novel/stages/stage7/naming.py`、
`novel/stages/stage7/assembly.py`；跨 stage 共用原语与 renderer 住
`novel/stages/common.py`；薄编排与设施装配住 `novel/stages/chain.py`（CS-3 bind 退役后 13 节点执行序；
全部 runner 在位——含 `run_associate`／`run_compile`（B2 第三步）、
`derive_semantic_binding`（binding 随 body_design 双产物发布），
生产入口 `novel.cli run <node> <sid>`）。

依赖单向：Design 禁 Grounding 实现/Blueprint internals/checkpoint 裸读；Grounding 禁
Design 实现；Compile/Context 禁 LLM 和草稿；Stage5 只消费 WriterIR/acceptance；Hook
Writer 禁 Body prose、源 Hook 和源小说。Stage7 只消费正式契约与已接受文本，不得反向
修改 truth 或依赖 Stage2/4/5 internals；validator 不修输入，repository 不决策。

### 按章成文链（B4 章包写手已落地；「完整 IR＋完整前文」旧形态退役）

旧形态（完整 WriterBookIR＋全部有效前文进请求，章级 prompt 53.9k–60.9k 线性增长）
已由 D2 章包三件套取代（2026-09-27，rc8）。现役：每章运行时从完整 `WriterBookIR`
确定性投影章包（`project_chapter_packet`），renderer v7＝`build_writer_packet` 六分区
阅读面（必须写出／不可违背／承接／导演说明／背景／禁区，R2 落地；见「两层 IR 与
送达方式」节），请求只含当前章包；回执按 rc6 身份拆分绑定写手视图摘要与验证输入
摘要（Machine 摘要仅审计）。
章篇幅带默认 `[0.6, 1.3] × 预算`，每章首次＋最多两次重写（上一稿＋typed 机械错误），
硬检查失败不能披露放行、只有篇幅偏差可披露；截断即停（D6）。
提交顺序：原响应耐久保存 → 编译与检查 → 选定候选 → 原子写正文 → 原子写章回执；
各章经共享总槽并发执行，恢复按有效提交集合补缺。

### Hook 的正式选择与确定性投影（现役；B3 代码归位 Stage4，语义不变）

Hook 是同一目标故事的另一种呈现。设计负责材料及顺序、起笔、时间跳转、披露和收束；
Writer 负责句法、措辞、对白、局部动作、节奏、意象和语气。`PresentationGraph(BodyDesign)+hook`
必填、`publish_graph` 唯一发布；`pov`、`opening_facts`（按 world/pov/reader 分获准面）、
`materials`（呈现序，默认 2–4 份材料指引）、`concealed`、`surface_releases`、
`forbidden_reveals`、两份 brief 由 Hook Design 保存。机械校验臂钉住引用闭合、withheld
⊆ 该拍 delta、起笔事实时序、first 恰一人且非 narrator、隐藏人物字面三层防线。
HookContext 包含 POV、按 audience 分组的起笔事实、材料投影、禁止揭示、两份 brief、
长度边界、可用称谓与 `withheld_literals`（名称结构 ∩ 专名/带名 surface 推导）；事实
渲染保留 domain/holder/knowledge_mode 认知维度。Hook Writer 机械验非空、长度、编码、
包装行与 withheld 字面泄漏；超长同 Context 有限重写（再 2 次，attempt 进请求身份）；
回执 semantic 恒 unverified。

### NameMap v5 与接受／交付文本分离（现役）

NameMap 是交付变换契约，不是第五类 semantic truth。源姓名构成、称谓归属及独立化名
身份归 Stage2（Characters name_structure），目标名字归 Stage7；不在 NameMap 复制旧
canonical。按源名称结构给出必要名称位（登记了姓必须给新姓，反之不得给）；compile 以
应改名集合核对完整覆盖，合法保留必须带理由；共享姓氏按 surface_forms 模板逐人渲染，
同 family_id 新姓一致、不同源姓不得合并同姓；独立化名由 Naming 对已登记 alias_id
显式赋值。`compile_surface_replacements(Characters, NameMap)` 是纯函数；ApplyNames
单遍长名优先；残留检查只查应替换字面、跳过认领区间与合法保留；diff proof 机械证明
差异恰为替换表。Assembly 的 body_access 按 accepted/rejected/missing/unknown 四态
判定；交付身份由（逐章 (chapter_ordinal, prose_sha256)、hook hash、替换表、kept、
`ASSEMBLY_FORMAT_VERSION`）派生，分章不同即不同交付（`R-10.10`）；换 NameMap 只变
交付身份，不使 Stage5 回执失效。

### 恢复与历史采用（现役）

付费边界（`novel/llm/paid_requests.py::PaidRequestSession`）经工作单元内容轮＋整书
总闸＋唯一 ledger：缓存先于新调用授权（`PurchaseRequired` 停在 owner 写
needs_authorization），pending/unknown 不自动重购或释放额度；未采纳轮在下一次 reserve
前关闭且拒绝证据保留。恢复入口沿用候选生命周期：saved_response、historical_adoption
（`--adopt-root` 登记源根，身份/响应/成本三方核对，不复制缓存、不续旧 session）、
owner_patch（基线身份核验；同根补丁允许记录缺失即建档，F-4）、committed_set_recovery、
same_context_rewrite、deterministic_replay。`novel/work_units.py` 的
`node_lifecycle_spec` 以 NODES 优先、候选表回退。历史采用配方（`--wire-dir`／
`--adopt-root`／`--reasoning-effort`）与零新购复验口径见 `docs/REQUIREMENTS.md` F9
执行史；新协议改变全部请求身份，历史采用在新链上不会命中。

### 目标产物布局

```text
stage2/characters/characters.json
stage2/blueprints/chapter_blueprints.json
stage2/compiled/、stage2/acceptance/
stage4/grounding/{reconstruction_brief,semantic_binding}.json
stage4/design/presentation_graph.json
stage4/compiled/execution_plan.json
stage4/ir/writer_book_ir.json
stage4/context/hook/hook.json
stage4/acceptance/guard/、stage4/acceptance/packet_metrics.json
stage5/body/ch{nn}.txt
stage5/acceptance/body/ch{nn}.json
stage5/hook/hook.txt、stage5/hook/receipt.json
stage7/naming/name_map.json
stage7/final/<书名>_rewriting.txt、stage7/final/delivery_manifest.json
runtime/progress/、runtime/body_attempts_ch{nn}.json、
runtime/body_failures.json、runtime/chapter_observation_failures.json、
runtime/revisions/<owner>.json、runtime/issues/<owner>.json、
runtime/commit/<node>/<seal16>_<artifact>.json
```

正文与已购证据不可按「非 semantic truth」批量清理；kind 登记是删除政策的输入。
运行记录五层（SemanticRequest／ContentRevision／ProviderAttempt／AcceptanceRecord/
CommitBundle）已按 B1 前半落地，取代候选根四类运行记录（request／validation/
admission／adopt）的写路（映射表见 REQUIREMENTS）；旧输出根的同类文件只读保留。

### 消费投影（现役六族）

| 投影族 | 正式来源与消费面 |
|---|---|
| world_view | Design/Binding 的目标有效事实与约束，不以源时点冒充目标时点 |
| character_exposure | Characters 身份与 Design 显式称谓/披露选择；源与目标坐标分开 |
| chapter_map | Design/compile 的 scene→target chapter 映射及目标章集分母 |
| body_access | 有效 receipt 与 accepted body；未就绪显式返回 |
| hook_material | 正式 Hook Design 选择与 compiled target 的最小投影，不读 Body prose/Source Hook |
| rename_materials | Naming 用身份/源名称结构/风格投影；Apply/Assembly 用 Characters、NameMap 与接受文本，两种权限分开 |

### 字段级送达登记（R0，2026-09-28）

`contracts/schema/consumption.py::STR06_FIELD_DELIVERY` 把送达粒度降到字段：送达链
canonical 产物（WriterBookIR、章包派生的 RealizationContract、HookContext、NameMapV5）
的每个叶子字段都登记消费者（写手章包分区／HookContext／Naming／验证器／仅审计）、
受影响章范围（every_chapter／chapter_field／scene_owner／hook_only／naming_only／
not_delivered）与 `delivery_mode` 七档身份（must_realize／must_respect／continuity／
creative_guidance／background／guard_only／audit_only）；同一字段可按投影规则逐场
决定身份（如入场状态：声明读取解析值=must_respect，R2 起编译器补齐的入场状态=
background）。守卫在 `tests/test_str06_semantic_delivery.py`：canonical 字段未登记即红、
变异只动登记章（`packet_sha256` 比较面）、变异值只进对应送达面、写手视图无
audit_only 材料。R2（renderer v5／写手协议 v5）落地后 `r2_face_pending` 已清零：
写手阅读面不再以 JSON 倾倒 VerificationIR／HookContext，`sc01#b02` 式 ID、
`established_by` 与字段名的阅读面清零由 WriterPacket 分区渲染收口（见「两层 IR
与送达方式」节）。故障注入 fake 底座（`tests/str06_faults.py`，五类脚本化模型式
错误＋sitecustomize 注入路径）已供 R3 修订循环复用，R5 产品路径测试继续复用。

## 验收与关闭（B6-A／B6-B）

裁决 5 把 B6 拆成两步，付费与正文外发分别授权；裁决 8 冻结人工阅读维度与归因记录，
生产不设 LLM 评审、也不设文学评分闸。**人工阅读验收协议**（九维度、记录方式、归因与
处理取值、存放约定）全文登记在 `docs/REQUIREMENTS.md`「人工阅读验收协议」节
（R4 批登记），B6-A 与 B6-B 共用，不另立第二份维度表。

- **B6-A 语义与写作 canary**：冻结语义层协议后实购 Stage2 与 Stage4，免费编译出全部
  章包与 packet 指标；写 Hook 与 3 个按规则机械选出的章（开篇章、线程操作与跨章读取
  最多的中段章、读者确认与线程收束最多的后段章）。agent 先按协议读并逐条归因、用户
  确认；问题在 renderer、WriterPacket 呈现、写手协议或分区优先级 → 只重跑 canary
  写手，Stage2/4 不动；问题在语义层 → 报用户裁决（改冻结协议意味着重购）。用户认可
  canary 后冻结写作层。
- **B6-B 全书**：补写其余章（写手视图未变的 canary 章直接复用），再跑 Naming 与组装。
  工程核验：章连续非空、回执、改名零残留、交付字节、零新购重放、重启读回、ledger。
  交用户通读，按协议维度记录归因问题（处理取值：canary 收敛或 R7），汇总运行指标与
  packet 指标。

错章、漏章、改名残留、交付不一致、恢复失败等工程缺陷修到通过；文学质量意见逐条
登记为下一轮迭代优化的输入，不阻塞关闭。工程绿、planned-only 干跑和机械回执都不
替代成文质量，语义未评保持未验证。D11 已把合入 main 前提到 B6 之前，合入与验收
先后解耦，关闭条件不变。STR-06 关闭后按 R7 以归因问题与指标优化（含 v13 遗留
F-1/F-2/F-3），再做 D10 两本 golden（先修 `tests/golden.py` 的退役 rubric 引用）。
批次表、各批验收细节与删除条件唯一见 `docs/REQUIREMENTS.md`；Golden 的样本、六维
与冻结要求见 REQUIREMENTS「本轮之后」。

### 名称同一性与材料返修（CS-R）

names wire 使用 `stage2-names-v3`（rc15 段号按自然段寻址），显式提供 `resolutions`。每条包含 literal、
status（same／unresolved）、char_id、surface、basis 和 reason。same 指向已登记
人物称谓；unresolved 的两个指向字段必须为 null。basis 保留源句字面。
名称观察与 Characters 的信封为 v2：旧版本不能补空 resolutions，须由 names
按新协议重建后通过 compile 重建双真源。原观察不改；关联投影和 canonical
语义文本按有证明的局部句段归一，重复编译幂等。未决时不购买关联、不发布双真源。
Preserve 同时收到正式归一文本、原句及名称同一性依据，不能私改命题消除字面差异。

材料来源和错误是运行时派生值，见 `docs/schema/repair_convergence.md`。回派记录
使用 OwnerIssue v3，dispatch_pending 区分已登记待执行与已提交待复验；轮数、
问题指纹历史及显式更正证据耐久保存，superseded 仅表示旧归因退出调度，不表示
问题已解决。最终状态仍由原发现节点对当前正式输入复验决定。

### 语义输入投影（CS-I）

- 名称观察输入`stage2-names-input-v2`只发送一次完整evidence与书级观察，不再重复拼全文；evidence 是按阅读序排列的 `{segment,chapter,text}` 数组（v1 以段号为键，请求 JSON 键排序会把一章第 100 段排到第 11 段之前，RC-26 追加）。
  原文引用、Hook和所有章证据完整保留。
- Preserve输入`stage4-preserve-input-v2`补齐转移op／scope／known_by、认知命题引用、
  悬念操作／问题／解决、时序模式、场景边界和因果边。相同拍内相同证据段与逐字quote合并展示，
  保留全部锚点；初始状态只发一次。必要语义不能为降低字符数删除。
- Design输入`stage4-design-input-v1`由`project_design_input`确定性投影：Brief义务以ob键编址，
  requires与knowledge ref复用同一映射；去除可推导ID和审计摘要，保留完整satisfaction、locks、
  propositions、facts、world约束与表达选择。动态schema将等价know分支按mode分组，仍验证每个ref。
  producer和compiler使用相同映射，旧canonical Brief不能作为新wire模板。

这些版本只描述语义输入，不能当正式artifact版本；输入变化自然进入请求身份。

### 职责边界补充（RC-18～25，2026-10-04）

- 章内悬念open／close各至多一次，本地close不得早于open（按拍序及同拍条目序）；
  没有本地open的close仍允许跨章配对。同源只读判据在chapters接受、associate输入
  预检和compile防御复验执行。章观察错误按node＋chapter scope回派，关联只修配对。
- 关联判据返回带规则码、位置、owner和scope的派生诊断；展示文字不是控制接口。
  章内重复操作的身份按句柄、操作及同类操作出现序号区分实例，精确物理位置在
  context留证；部分删除能被接受循环及回派指纹识别，无关位置移动不冒充进展。
  旧误派账的免费更正使用现役issue-correction预览/CAS入口，不清费用和轮数。
- Hook设计输入新增stage4-hook-semantics-v1派生投影，所引用kp和世界约束的完整
  文本来自Brief；悬空引用在外发前拒绝。正式领域artifact形状不变，请求摘要随材料变化。
- renderer升str06-render-v10（CS-F2 称谓投影，见上）；此前 v9 与正文协议 str06-body-protocol-v8：义务在接受拍兑现，
  状态在对应拍末成立，持续到被后继转移替代或作用域结束。版本参与请求与章包身份；
  旧正文/回执不静默补新身份，已购响应仍按正式免费复验与恢复入口处理。
- Design的requires沿用顺序三分：同场允许接受拍交错，跨场逆序仍拒绝；事实读取与
  知情披露保留独立硬闸。修订权限由业务owner声明，通用接受循环只消费声明。
