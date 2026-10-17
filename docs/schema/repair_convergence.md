# 返修归因、计量与成书验收规格

本规格对应 2026-10-04 用户确认的推进顺序。行为场景使用中文 Gherkin；
架构边界写在本文，进度只登记于 [REQUIREMENTS.md](../REQUIREMENTS.md)。
本批以根 I-02／I-16／I-20／I-35／I-43、后端 R-5.16～R-5.20 与现役额度为基础。
这是目标规格；未完成的场景不能冒充当前实现。契约实现时同步后端契约和代码注册。

## 行为规格与验收映射

Gherkin 使用 [Cucumber 官方语法](https://cucumber.io/docs/gherkin/reference/)。
`.feature` 是行为说明，不引入第二个生产规则引擎；场景由现有 pytest 和 golden
入口验证，只有绑定具体测试或实测证据后才能记为通过。场景编号稳定，测试名可演进。

| 规格 | 场景 | 实现与验证边界 |
|---|---|---|
| [返修闭环](../specs/repair_convergence.feature) | RC-01～08 | 正式材料来源、源目标坐标、纯归因、统一路由、Design接受与最终材料验证 |
| 同上 | RC-09～15 | 问题身份、有限修订、耦合patch、消费级联、免费恢复与旧根处理 |
| 同上 | RC-17 | names v2 同一性留证；`tests/test_str06_name_resolution.py`：真实Stage2发布、Preserve消费、未决零下游购买、局部归一与旧版本拒识 |
| 同上 | RC-16 | 真实CLI／调度／交付／重启的零付费stage与产品入口测试 |
| 同上 | RC-18～25 | 作业单位、结构化诊断、修订权限、材料语义、时点和共用摄入依赖；逐项证据见下方职责边界表 |
| 同上 | RC-26～29 | 证据寻址（2026-10-04，rc15）：证据段＝自然段且一段一行、basis 段号由 Resolver 按引文定位、src 区间组合闭合、patch 轮整份回答按整份验收；`tests/test_stage2_evidence_addressing.py`（真实 book／chapters 入口与 `ask_json` 接受循环），见下方证据寻址表 |
| 同上 | RC-30～35 | 高影响源事实、叙述者与人物知情、Preserve限制依据及义务去重；目标行为，工程协议测试已落地；真实语义标定已执行并封口（2026-10-05，判官不满足启用硬规则，重开条件见 DECISIONS 同日行） |
| 同上 | RC-36／38 | 已实现（2026-10-04，rc16，`stage4-design-v8`）：事实锁在已选状态上标注`lock`，映射随SemanticBinding发布且不复制value；`tests/test_str06_a4_design_locks.py`（CS-F2一节：真实v8入口的正反例与RC-38四行）、`tests/test_str06_fact_lock_stage.py`（真实Stage2→Preserve→body_design→compile链）、`tests/test_str06_design_slim.py`、`tests/test_str06_contracts.py` |
| 同上 | RC-37／39 | **仅结构层已实现**：结构检查只报引用与模式的实际结果，语义对应未运行即保持unver；局部语义核验已随CS-F3离线标定封口（2026-10-05：判官不可启用，生产语义维持unver，重开条件见DECISIONS同日行） |
| 同上 | RC-40～43 | 已实现Writer路径（2026-10-04，IR rc16／renderer v10）：只投影已证明唯一绑定人物的完整登记称谓，短姓／未登记表达不猜人物，留痕可观测，Design自有文本冲突不由compiler代选；`tests/test_str06_name_projection.py`、`tests/test_str06_material_repair.py`（归因仍按原字段）、`tests/test_str06_r10b_writer_packet.py`。**Hook材料投影已随 rc16（2026-10-05）落地**：释放面由 Hook Design 自选，`compile_hook_context` 按本场释放算好 `HookContext.withheld_bindings`，`render_hook_context` 在正向材料行与禁区行做同一确定性投影（上下文字段与来源文本不改，条目返修归因仍按原字段），留痕 `hook_name_projections`——对齐 `docs/BACKEND_CONTRACT.md` R-6.2 的「Hook 同尺投影」；新称谓创作不在本批，RC-42只证拒绝未登记别名 |
| 同上 | RC-44～45 | `tests/test_str06_candidate_preflight.py`：同一候选聚合自身及继承材料问题，真实接受入口上游优先且失败不发布，免费恢复与已有Hook复验，依赖缺失明确未运行；验证记录见提交`edf965d7`说明及重置前（`eb5f0d5a`）的REQUIREMENTS |
| 同上 | RC-47 | 已实现（2026-10-04；2026-10-05 CS-F1 收口；2026-10-17 契约驱动规划）：Design错误在判定处带wire证据路径，结构错误保留逐项类型与schema，替换范围由schema与owner语义边界共同确定；合并后重跑完整预检。读取闭合→read行、确认闭合→初始行／know行（判据在`read_closure`带结构化位置）；Hook判据错误（材料、起笔事实、称谓释放、位置引用，含禁止揭示×材料的双侧路径）同约定接条目返修。Design／Hook共用`design_paths`；无法定位按RC-59停机，不自动整份重生，漏标责任拍走可选目标闭集。`tests/test_str06_design_patch.py`覆盖真实`patches`回答、整场读取修复、完整预检及免费重放，`tests/test_str06_patch_routes.py`覆盖真实形状错误与修复路由 |
| 同上 | RC-46／48／49 | 责任归因的语义面、版本恢复和分项成本验收；目标行为，未绑定绿证据前不得宣称实现 |
| 同上 | RC-50～56 | CS-H（2026-10-05，目标行为）：已购候选免费沿用与候选集合、记录如实、接受版本发布语义与三个中断恢复；见下方「CS-H」A 节，批次 H1 落地前不得宣称实现 |
| 同上 | RC-57～59 | CS-H 窄返修协议：目标路径与可选目标闭集、整份出口闭集、无法定位即停、无进展按根因；批次 H2 落地前不得宣称实现 |
| 同上 | RC-60～62 | CS-H 称谓显示面、投影、必保揭名与确定性转换；批次 H3 落地前不得宣称实现 |
| 同上 | RC-63～65 | CS-H 共享事实锁派生与主体包含（H3）、Preserve 自闸与条目返修（H3）、associate 维度句柄（H4）；落地前不得宣称实现 |
| [计量与对照](../specs/cost_accounting.feature) | CU-01～06 | provider usage解析、新购和缺账恢复、缓存去重、只读历史补证 |
| 同上 | CU-07～10 | 必要输入送达、精确请求身份、分节点配置对照、可比成本报告 |
| 同上 | CU-11～12 | `tests/test_str06_cli_dispatch.py` 配置／权限与篡改反例；`tests/test_str06_product_entry.py` 实际请求、免费复检及HTTP到真实子进程全链 |
| [整书验收](../specs/book_acceptance.feature) | BA-01～06 | 工程冻结、三书分母、人工干预披露、六维质量、完整成本 |

当前实现：材料来源在 `novel/core/material_trace.py`，归因在 `novel/stages/stage4/repair.py`，
共享预检在 `novel/stages/stage4/preflight.py`，旧账更正在 `novel/stages/issue_recovery.py`。
计量解析和补证分别在 `novel/llm/usage.py` 与 `novel/llm/usage_audit.py`；对应
`tests/test_llm.py`、`tests/test_str06_f7a_wiring.py`、`tests/test_repair_rounds.py`。
现役 names wire 为 `stage2-names-v6`，Characters 正式产物为 v6；历史 v2 是当时
同一性留证的里程碑，不是当前接受版本。旧版按登记的重建边界处理，不能补空声明冒充核验。

既有测试入口：`tests/test_str06_a5_positions.py`、`tests/test_str06_r3_selfheal.py`、
`tests/test_str06_item_patch.py`、`tests/test_str06_product_entry.py` 与
`tests/test_str06_f7a_wiring.py`。扩展反例必须执行真正判据和消费者，不以字符串
存在、函数存在或规格语法正确代替行为证明。新增模块同步 suite_catalog。

## 责任规则

### 作业单位与控制边界（RC-18～25）

作业单位固定为node＋scope；内容修订和网络尝试不产生新单位。发现节点不自动
取得上游字段的修改权：章内句柄纪律由章观察接受前验证，已存错误从正式章位置
回派；associate只修配对选择。无法证明责任或确定性代码缺陷停止，不购买语义修复。
同一规则在接受前与最终compile复用；validator只报告，薄编排持久登记和执行回派。
章内生命周期按拍序及同拍loops条目序判定：本章有同句柄open时，close不得先于
该open；只有close的观察仍是合法跨章候选，不猜它的配对，也不强制补本地open。

关联诊断使用规则码与句柄/正式位置组成身份，文案、列表展示顺序和当前实得值
不参与。兼容字符串只留在尚未迁移的判据，不能重新包裹已结构化诊断而丢失身份。
收敛沿用现役有界政策：A+B→B可以继续；A→A、A→B→A停止；此前未出现的
A+B→B+C可在原轮数上限内继续，但不报告为问题减少，也不新增额度。
章内重复操作按句柄、操作、同类操作的出现序号区分实例；精确拍位与条目位置
保留在诊断context，不纳入重复实例的身份。因此三次close减为两次时身份集严格
缩小；插入无关条目或只改展示文字不能冒充进展。接受循环与跨owner指纹共用该身份。

每个语义owner明确权威输入与可改输出；观察可纠正自身错误结论，设计可在义务
约束内调整选择，关联只选句柄，写手按材料展开。整份、条目及跨owner修订共用
权限描述。原文和只读上游不随修订修改；“不得改换事实”不得禁止纠正错误观察。
模型实际请求必须包含其决策引用的完整语义，投影与编译共用编号映射。依赖语义
沿用现役顺序三分：同场允许交错，跨场逆序拒绝，认知披露独立校验。拍末结果
在对应拍成立，持续状态到后续转移替代或作用域结束；不延长成全部章末条件。

2026-10-04用户裁决：跨层分章纯转换、摄入审计和Stage0放行判据下沉独立底层
模块，原Stage实现删除；不使用转导出掩盖六条深依赖，不保留双实现。
架构测试验证依赖方向；现役Stage、来源证明和读面测试验证消费者行为等价。

本组证据按下表追溯；未列文件名的`test_rc*`均在`tests/test_workflow_role_boundaries.py`，
测试名中的RC编号与Gherkin同号。RC-23在本批基线已通过，
不再把完整义务投影算作待修缺陷。免费fake transport只证明接口和执行行为。

| 规则／场景 | 失败反例与实现 | 真实消费者验证 |
|---|---|---|
| R-2.11、I-24／RC-18 | 同一本地句柄重复close；`novel/stages/stage2/chapters.py::chapter_loop_issues`复用于整份接受与条目定位 | `tests/test_workflow_role_boundaries.py::test_rc18_duplicate_close_rejected_by_chapter_acceptance`，合法单次close绿对照；不修改输入 |
| I-31、I-35／RC-19 | 历史章观察重复close不能派associate；`novel/stages/chain.py::_check_chapter_loops`按正式chapter scope回派 | `test_rc19_saved_duplicate_routes_to_chapter_without_associate_purchase`经两个真实发现入口、章owner修订、原入口复验及重启读回；`test_rc19_legacy_wrong_owner_correction_keeps_history_and_cost`经显式恢复保留耗尽轮与费用 |
| I-36／RC-20 | 两个同前缀关联问题修掉一个曾误停；`ValidationIssue`及associate／compile判据产生稳定身份，接受循环和跨owner指纹直接消费 | `test_rc20_real_associate_issues_shrink_and_replay_free`真实associate请求、3轮接受、免费重放、compile发布；`test_rc20_identity_does_not_depend_on_wording_or_order`验证文案与顺序无关 |
| R-2.11、I-31／RC-18、19追加 | 章内先close后open仍曾误派associate；同一`chapter_loop_issues`判据两遍解析本地open | `test_rc18_close_before_local_open_rejected_and_located`覆盖v3同拍／跨拍、条目定位、不改输入和合法跨章close；`test_rc19_saved_close_before_open_routes_and_recovers`覆盖两个发现入口、章owner修订、无关章采用、最终发布与重启读回 |
| I-36／RC-20追加 | 多个重复操作曾共用身份；诊断按同类操作出现序号区分重复实例 | `test_rc20_repeated_loop_instances_have_stable_distinct_identities`保护位置移动无关性；`test_rc20_repeated_close_shrinks_through_acceptance_and_replays_free`覆盖整份／patch的2→1→0及免费重放；`test_rc20_repeated_close_shrinks_in_saved_owner_fingerprint`验证正式回派账进展、轮数不复位及compile发布 |
| I-02、I-20／RC-21 | 纠正观察与“不得改换事实”矛盾；`novel/stages/common.py::semantic_revision_rules`统一声明，paid_requests与跨owner修订共用 | `test_rc21_actual_revision_request_allows_correcting_observation`截获整份／patch请求；`test_rc21_owner_permissions_reach_full_and_patch_requests`覆盖各权限面；RC-19实际章修订复用同一声明 |
| I-12、I-34／RC-22 | 缺语义仍发Hook请求；`novel/stages/stage4/slim.py::project_hook_semantics`投影已引用命题／世界约束，悬空在请求前停 | `test_rc22_hook_request_contains_referenced_proposition_or_stops`截获真实Hook入口，覆盖两类语义存在与缺失；`test_str06_hook.py`验证编译与消费 |
| I-15、I-21／RC-23 | 原短表已退役；`project_design_input`及`design_obligation_index`统一完整映射；缺条目拒绝，不再截断 | `tests/test_str06_design_slim.py::test_design_projection_preserves_complete_semantics_and_references`及真实Design链测试保护既有正确行为 |
| I-20、I-22／RC-24 | Design同场指令冲突、正文把拍末延长到章末；Design规则与writer协议／renderer同步校正并升身份版本 | `tests/test_str06_body.py::test_same_scene_requires_interleave_is_legal_cross_scene_inversion_is_not`；`test_rc24_successive_states_reach_their_own_beats`编译两拍相继状态并渲染实际章包；`test_str06_r10b_writer_packet.py`保护材料 |
| I-03～05／RC-25 | 六条跨Stage深依赖；唯一实现迁至`core/partition.py`、`core/ingest_audit.py`、`core/eligibility.py`，删除旧实现与白名单 | `test_layering.py`同时查顶层与延迟import并禁止底层反向依赖；`test_stage01.py`、`test_stage0_revisions.py`、`test_store.py`、`test_api_stage01.py`验证正式摄入、恢复和API读面 |

红例记录见`.test_cache/role-boundaries-red.xml`中的RC-18、RC-19 associate与RC-21，
`role-boundaries-red-associate.xml`中的RC-20，以及`role-boundaries-red-materials.xml`
中的RC-22缺失语义与RC-24。首轮记录中另有fixture来源过期、导入与路径错误，
不计入缺陷证据；修正fixture后的绿证据与分母记在提交说明及重置前（`eb5f0d5a`）的REQUIREMENTS，现行REQUIREMENTS只登记未完成项。
RC-19收口追加`test_rc19_three_digit_chapter_can_persist_and_resume_its_revision`：
`chapter_owner_product_rel`原先把两位最小格式宽度误当章号上限；修正后通过正式
问题账、重启摘要与修订门控验证。红例见`.test_cache/role-boundaries-scope-red.xml`。

归因回答两个问题：文本从哪里产生；哪个正式选择使它出现在当前消费位置。
“包含该字面的材料”不自动等于“应改写该文本”。源章与目标章永不按同号相认。

| 证明 | 修复责任与边界 |
|---|---|
| 原文存在未登记的姓名异写 | Stage2 names 留原字面、原句与同一性理由，same 指向已登记人物称谓；unresolved 停止；compiler 只在证明的句段范围归一语义文本，下游不私改命题 |
| 源称谓、源观察或证据确实错误 | 按正式来源回 Stage2 book／章观察；不根据目标章倒推源首现 |
| Preserve自写摘要含裸姓、未登记别名等无法唯一认领的字面 | Preserve只能改自身wire字段，保留anchors和语义义务；已证明同人的自然混称不返修 |
| 正确源命题的必保揭名认知被放在释放之前 | Design统一处理know拍位与目标称谓释放；普通带名材料先投影，compiler不替其选择 |
| Design自写文本或默认keep表达经合法投影后仍冲突 | Design文本／expression_overrides与释放选择；不能修改继承源文来掩盖 |
| Hook自有材料选择或释放冲突 | Hook Design；正文Design字段不可被Hook暗改 |
| 正式设计正确、确定性投影有误 | code缺陷停止，修compiler／renderer；不消耗语义返修 |
| 来源缺失、多义、摘要过期或码未登记 | 未决停止，给出具体缺口；不默认派Preserve或Design |

修复必须重新通过保产、模式授权、引用、披露和实际写手材料的原判据。
不删除义务、不把known改成suspected、不自动提前释放名称来“修绿”。
语义节点可在已有契约允许的选择空间内生成合法新设计，compiler不作语义推断。

### 证据寻址（RC-26～29）

2026-10-04 cs_golden 三书（三人成行／夫君不行／夫君的报恩）全部停在章观察：
50 章仅 16 章首答通过，61 份被拒回答中 52 份只因段号或引文定位被拒；引用段号
恒比真实段号大（194 条中 84 条恰好大 1），207 个段号超出本章末段。返修轮平均
输出 17.8K token、首答 23.6K——条目返修几乎不省 reasoning，降本靠首答通过率。
用户批准按下表一次改完；旧根已购观察的复用用仓外一次性脚本按字符位置换算，
不进产品代码（新书走新契约，不再需要兼容）。

| 规则／场景 | 失败反例与实现 | 真实消费者验证 |
|---|---|---|
| I-15、I-21／RC-26 | 「。」后切分把收尾「」」切到下一段、问叹号收尾的自然段并段，标签悬在上一段下方；`novel/stages/chain.py::_segment_paragraphs` 一段一自然段，`novel/stages/stage2/resolver.py::render_evidence_block` 唯一渲染、段含换行即拒 | `test_rc26_each_paragraph_is_exactly_one_segment`、`test_rc26_requests_render_one_segment_per_line`（真实 book／chapters prompt 组装）、`test_rc26_multiline_segment_stops_before_request`；names 输入改为阅读序数组（`novel/stages/stage2/names.py::names_evidence_view`，input v2）由 `test_rc26_names_input_keeps_reading_order_past_99_paragraphs` 经真实 run_names 入口验证 |
| I-21、R-2.18／RC-27（2026-10-07 指针化收口） | 模型被要求抄可由引文推导的段号／逐字引文，验收器已算出正确段与原文却让模型付费重抄；现为 `novel/stages/stage2/resolver.py::basis_quote`＋`novel/stages/stage2/chapters.py::_derive_basis_quotes`／`novel/stages/stage2/book.py::_derive_basis_quotes`（wire 只给段 id、引文＝段全文由代码切出），范围外→整拍、闭集外→该转移 | `test_chapter_basis_quote_is_derived_from_segment_text`、`test_basis_segment_outside_derived_interval_revises_whole_beat`、`test_basis_segment_absent_from_closed_set_revises_only_that_transfer`、`test_real_chain_publishes_derived_basis` 经真实 run_book／run_chapters 落盘 |
| I-35／RC-28 | `["e11_29","e11_31","e11_34..e11_36"]` 数组内区间不展开（三人成行 ch11 停点）；曾由 slim 的 `_expand_src` 对组合闭合，CS-S 1a（2026-10-07）v8 起区间语法整体关闭——模型只写单段 `start`，区间由 `novel/stages/stage2/slim.py::derive_beat_intervals` 代码派生 | `test_v8_range_and_array_syntax_are_closed`（旧语法拒收）；区间纪律反例见 `tests/test_str06_chapter_slim.py`（数值序、首拍引子弃权、越界 start） |
| I-35／RC-29 | patch 轮仍可收到整份回答；`novel/llm/paid_requests.py::accept_patch_answer` 先按冻结计划提取替换，并用 JSON 摘要证明范围外未变，再合并与完整验收；未提供子schema的 owner 同样不得绕过范围 | `test_rc29_full_answer_in_patch_round_is_accepted_as_full_revision`：第二次调用即采纳，不多耗内容轮，保留基底与本轮耐久引用 |

红例记录 `.test_cache/evidence-addressing-red.xml`（10 例全红，原因分别是切分、
渲染、缺渲染函数、只给引文的 basis 被拒、书级 wire 协议缺失、patch 轮多一次调用）。

## Stage4停点解决方案（RC-30～49）

本节是2026-10-04《夫君的报恩》Stage4停点的实施规格。采纳用户审阅的范围收缩：
复用现有Stage2、Preserve、Design、compiler、repository及owner patch；不另建通用
验收框架、第二份事实账、全人物全时点认知模型或新的工作流节点。进度、启用决定、
版本兼容与删除条件只登记于REQUIREMENTS。本文规定目标行为，不把设计写成现役能力。

### 已复验的问题与结论边界

开发样本为`cs_golden_20261004_rc15`中的《夫君的报恩》11章，停止节点为
`str06.stage4.body_design`。原始响应的免费内存重放得到299→8→2条接受错误：
首答291条名称问题、8条事实锁问题；第一修订仅剩事实锁；第二修订补写锁后在
目标第2章两处重新带入尚未释放的全名。事实锁与普通状态重复表达同一事实。

在内存中仅消除末轮两处字面冲突后，Design与Binding构造通过，但真实写手材料
预检还有14处问题，现有路由全部回派Preserve。另将该锁的值换成无关的“明日去
城外赏花”，现有Design接受校验仍通过：这只证明现有结构检查不能证明语义对应，
不宣称该反例通过整条生产链。此两项均为诊断对照，未写回产物。

源语义另有两类反例：第3章连续主体为夫君，却因“嫂嫂”称呼被观察成表弟；
Preserve ob18继承并强化为必保。第8章第一人称叙述向读者揭示意图，Preserve
ob44却添加“不得改成芸娘当场识破”。叙述时知情、故事当时知情和读者获得信息
的时点必须分开；缺少角色当时知情证据，不能推导知道或不知道。

“陆郎”未出现在源文不是禁用目标称谓的充分理由。本次失败在于其未正式绑定，
并且缺少名称替换消费闭合。新增目标称谓是合法的产品扩展方向，但本批优先使用
已有登记面，不顺带新增目标称谓创作功能；不能把源名称表当成目标新增别名的落盘处。

### A．源事实与保存要求各守本职

Stage2维护源文的观察与证据。高影响事实核验限于改变主线、人物关系或关键行动
依据的断言：亲密／亲属关系、背叛、死亡／生还、真实身份与身份揭示、决定行动的
关键知情与误解。高影响性的判断属于语义工作，不由关键词白名单代替；未选中的
事实不能冒充“已核验”，抽样须检查是否漏选高影响项。

核验复用本章原文、必要前文与现有证据窗口，逐项比较主体、行为／命题、事实
状态、说话者／知情者与时间限定。对代词保留可判别的前后文；明确场景或主体切换
必须被识别，不能用“总沿用上一主体”修复。核验结果只允许支持、矛盾、未确定，
带具体对象与证据；引文存在、人物ID合法和模型自报“已检查”均不构成支持证明。

职责归现有章观察owner；若选择自动核验，执行形态是在章观察节点内对高影响
候选做一次集中核验，已有同身份核验复用，仅未确定条目进入有界局部补判。输入
超过已登记预算时显式分组、保留完整分母，不截断证据，也不把语义工作塞进禁付
compile或只负责同一性关联的associate。不修改13节点拓扑、不增加所有节点的
固定二次调用。核验记录绑定正式输入／候选／协议身份，复用既有响应与接受账，
不复制一份canonical事实；语义结论变化仍由章观察owner修订其正式产物。

自动语义阻断属于现役`unver`政策的变化。用户于2026-10-04明确选择先做离线
标定再启用；标定已于2026-10-05执行并封口——判官不满足启用硬规则，自动语义阻断不启用、
生产语义保持unver，重开条件见DECISIONS同日行；启用验收完成前，不能把工程接口或fake transport
绿例记成语义核验已通过。已确认的具体错误通过现有owner patch纠正，未确定项不补猜；任何新增硬闸必须有
免费重验、显式人工编辑或等价恢复，沿用既有付费、外发与预算纪律。

Preserve从源事实中选择本次重构不可丢的功能，不能把每条源事实默认升级为硬锁。
statement和satisfaction中的肯定、否定限制都须有源依据；不得从“reader已知”
推导“角色不知”，也不得把目标设计选择冒充源文要求。确实允许多种演法的项目
保留功能与因果，不锁死源文措辞、具体物件或偶然位置。存在源冲突仍回原Stage2
owner，Preserve不私建新事实版本。

对62项义务按“事实／叙事功能＋知情／因果限制＋发生时点”审计重复项，不设
目标条数。共享同一事实引用不要求复制事实；同一机制不同发生时点、不同持有者
或不同认知模式不可为了降费合并。义务去重由Preserve作语义判断，compiler只
对已声明的引用做确定性去重；审计分母与被保留的主线功能同时记录。

### B．事实锁引用已有事实，不再生成第二份状态

事实内容的唯一承载位仍是Design已有的事件／状态；事实锁只声明该内容受什么
源约束及在哪里兑现。建议wire采用在已选目标条目上标注锁引用的方式：模型选择
已有短引用与承载条目，compiler由条目所在位置派生正式拍引用和状态地址。
不得要求模型生成正式ID、hash，或将源状态的自由key直接猜成目标状态key。

正式映射由body_design同次发布的SemanticBinding承载，每条只保留锁引用、
目标拍及需要时的状态地址，不复制目标value。承载拍内该地址必须恰好对应一条
状态，不存在或歧义均拒绝；Design接受、Binding派生及正式产物免费重编译共用
这一引用要求。其他拍对同地址的合法更新不受限制。BodyDesign继续唯一持有目标事实。
多个义务共用同一锁／同一承载时可共享映射，但共享拍须同时属于各相关必保义务
的接受／显式支撑拍集合；全书出现过该锁不能代替对应义务兑现，未登记的锁位
不能隐式成为支撑拍。修改目标内容后对应接受证明必须
失效，不能仅凭目标ID未变化沿用核验。现有`real`仍决定义务接受／支撑拍，
`know`仍决定认知变化拍；事实绑定不再另造一套义务承载位。

本批优先闭合当前kf对应的状态约束：planned／expected／conditional绑定已有
状态及其建立拍，校验该时点的mode与op；后续合法转为actual不因先前计划锁而
被永久禁止。unknown_value绑定显式未知状态，不补值；unanswered继续作为源文
未回答禁区，不要求生成一条“已兑现事实”。即时事件已有`real`足以表达且无需
新增状态时，不为了对称强造状态；确需事件映射时用显式分支，不能靠可空地址猜类型。

验收分两层，结果分开记录：

| 层次 | 可以证明什么 | 不能冒充什么 |
|---|---|---|
| 结构检查 | 锁、拍和地址存在；参与者、时点、op、modality合法；必保锁映射齐全且位于对应接受／显式支撑拍 | 不证明自由文本与源事实同义 |
| 确定性派生 | 明确不可变且有结构化对应的事实核心保持一致 | 不从statement自然语言猜地址或关系 |
| 局部语义核验 | 允许重构的目标内容确实保留所绑定的事实及限制 | 不因结构通过或自报布尔值直接判“已兑现” |

Design的局部语义核验只消费正式Brief和对应候选片段，不引入源章号、原文窗口
或源叙述拓扑，保留源盲边界。无关“赏花”即使引用合法，也必须在语义核验中被
否决；未执行／未标定则如实未验证。其启用与Stage2高影响核验共用本批裁决和
成本对照，不默认给全书全部事件添加审查调用。

### C．内部人物身份与写手可见称谓分层

内部结构使用已有稳定人物ID；设计器提前知道一个人的身份不是对读者的泄露。
硬检查针对真正送达当前章节／Hook的正向材料以及正文消费，不对仅供内部审计的
字段套用正文禁词表。源引文保持原字节并标明证据用途，不能作为未经投影的正文材料。

只扩展已有名称投影，不把全部自由文本模板化。对正式登记、能唯一绑定人物的
完整称谓，归属统计包含所有人物的登记surface（含纯称谓）；只有唯一人物的
对应构词非空时才可投影。投影可在明确的正向材料字段内选用该位置已经允许的表述；保留原字段
及人物来源，衍生文本不反写事实。短姓、关系称呼、多义字面、未登记表达不能据
字符串相似性推断人物；不能用全局替换“陆→夫君”或“裴→表弟”。无法证明指向
时返回具体源字段给其owner登记／改写，不买Design去修确定性投影。

新增称谓在未来扩展中须具有目标人物、构词／替换规则、释放位置及冲突验证，
同时进入Stage7替换与残留检查。本批不因源文零出现自动判非法，也不在这个缺少
正式登记入口的批次暗中开放任意称谓。现有已登记表述足以恢复这本书。

释放时点是Design选择，源文身份悬念及已保留的披露限制是其边界。没有语义锁的
偶然选择可以由Design重新选择，compiler不得为了清除错误自动提前释放；确有
身份悬念时，改用已授权称谓或调整材料，而不是放宽硬闸。对本书第3章全名释放
是否有叙事价值须作语义核读，不把它先验认作源文硬要求或无用限制。

### D．完整候选预检与局部返修

复用`novel/stages/stage4/preflight.py`及现有编译器。候选处理分为wire解析／
结构构造、Design局部检查、Binding派生、执行计划／Writer材料预检；Hook尚未
生成时明确未运行；恢复预览对已有Hook先重跑正式接受判据，再做上下文与材料
检查。安全可构造的候选同时执行所有
独立检查，不能因为一处名称错误就遮住已有输入足以检测的14处继承材料问题。

内部诊断统一包含规则身份、责任依据、正式来源、目标位置、可编辑wire路径与
检查状态。缺少结构导致后续无法执行时标为“未运行＋依赖失败”，不记通过、不
制造悬空ID的连锁噪声。未通过候选只留耐久响应，不发布半份正式BodyDesign或
Binding；compiler／validator均不在检查中落盘或改输入。

来源与责任分别判定：源主体误判回Stage2；无依据保存限制回Preserve；目标承载
或披露选择错误回Design；合法引用被错误渲染回代码。已有`MaterialSource`及
归因函数继续复用，但只有已证明的控制选择才能决定owner。多义、缺证据或未知
规则停止；聚合报告保留全部独立问题，先处理造成输入失效的上游问题，不在旧
输入上继续购买Design修订。上游修复后按新输入重建报告，不能消费旧诊断坐标。

Design的局部修订接入已有patch接受机制。故障证据路径、允许替换范围与只读上下文
分离；schema决定可完成结构修复的对象，owner声明语义自洽所需边界。额外键的值
无论是非空／空数组、对象、标量或null，都必须有删除键的权限；完整父对象替换通过
省略该键完成删除。Design场内额外键统一授权整场，模型决定是否保留并调整原有语义，
程序不搬字段。修改目标条目、锁绑定、释放位置时包含必要共同父项，替换不重叠。

共享接受循环冻结同一计划中的基底摘要、目标路径、子schema与问题覆盖；依赖切片、
返修请求、回答校验及合并均消费该计划。整场目标的材料闭合全部场内拍，未经授权的
邻场不因此进入编辑范围；未涉及条目保持原内容。发请求前验证计划具备必要结构编辑
能力，无法规划时报告工程错误停止。只有路由闭集登记的原因可整份修订。合并后重跑
完整预检，禁止用局部通过替代整体验收，也不无声退化为每轮重写整书。
Design依赖切片统一`stage4-design-patch-slice-v4`，`wire_protocol_version`保留候选的
原始v9／v10／v11；派生切片升版不改变正式artifact／wire，也不重置预算或历史轮。
沿用原修订上限、问题身份与循环停止；增大重试次数不是本批修复手段。

### E．版本、恢复和验收

事实锁wire和正式绑定形状发生变化时显式升版，同批修改Brief投影、Design接受、
Binding、执行计划、Writer／Hook材料、恢复入口、契约守卫及真实消费者。版本号
在字段定稿实施时统一分配，不提前为尚未实现的协议改现役NODES／schema版本。
对这本rc15根不改历史版本号、原响应或账；能否免费复用由真实请求身份及新接受
条件证明，无法无损重建的映射不得由迁移层猜出。任何必要兼容层在REQUIREMENTS
登记唯一落点、范围与删除条件；原根继续作为冻结失败证据。

恢复与实现分开验收：先完成确定性工程修复和三轮已购响应的离线回放，再通过
正式owner修订入口纠正ch03及相关知情／Preserve限制；按实际依赖重编Stage2，
重新选择义务和设计，不预设62项或某个ob序号在修订后恒定。原owner修订记录、
响应、费用及已用轮数保留，人工恢复不能登记成原运行全自动成功。新wire需要新
语义输出时明确列明不可免费复用的原因，不隐式换根、换模型、增预算或授权外发。

| 验收面 | 必须留证 | 不接受的替代结论 |
|---|---|---|
| 机械工程 | RC-36～48的正反例、完整预检运行／未运行分母、发布与重启消费、无输入改写和重复购买 | 只证明函数存在、schema可解析或Stage4不再停止 |
| 关键语义 | RC-30～35／37的源文对照、支持／矛盾／未确定分项、正反案例、漏选与误拦记录 | fake transport返回正确答案就宣称语义核验有效 |
| 最终成书 | 目标相关章节及交付正文的主体、关系、知情与功能保全，现役六维验收 | 源观察／阶段产物通过就宣称成书质量通过 |
| 调用与成本 | 新增核验、内容修订、网络重试、缓存采用分别统计；prompt／completion／reasoning及未知值 | 去掉失败轮、将reasoning重复相加、预先承诺每书0.5美元 |

离线标定先冻结带证据与时点的预期标签，再与候选判断比较，不能用待测模型自己的
回答当标准。开发集包含本书已确认反例及其相反案例；启用前须另留未参与提示词调试
的代表性样本，按事实类别分别报告高影响项漏选、错误支持、错误否决、未确定和正确
支持。RC-30／31／34／37的已裁决正反例不得有错误支持或错误否决；证据确实不足
的案例必须保持未确定。该小样本全对只是最低条件，不等于已证明泛化：留出样本分母
不足、标签分歧未裁决或未知项被当通过时，不启用阻断。正式启用验收须登记所用模型、
提示词／协议身份、各类实测结果及适用范围；任何一项缺失仍保持unver。

对照必须绑定相同源文、配置、授权、质量口径与明确代码基线；历史修复回放只
证明已购反例能否收敛，不能冒充新书首答通过率或收费A/B。先量出语义核验增加
的调用与局部修订节省的token，再报告净变化；未实际购买时成本只记未测。

## CS-H：接受版本只发一次、首答通过、窄返修（RC-50～65）

本节是2026-10-05用户批准的CS-H执行计划的实施规格，依据对`csf4_restore_20261005`
（《夫君的报恩》，rc16）的审阅、一份独立审阅意见、当日四项裁决及对第一稿的六条二轮审阅
（逐条处理见DECISIONS同日行）。批次、进度与授权只登记于REQUIREMENTS；本节规定目标行为，
**未落地的批次不能冒充现役实现**，场景绑定测试后才可记通过。真实运行须另行授权（付费与
正文外发分别授权、独立新输出根）。

### 已复验的问题

本根28次实付（输入434,004／输出501,336，账内复算）里，返修与返工占输出token的52%
（259,419／501,336），且仍靠人工收尾。根因是结构性的：

- **接受版本被整份作废**：Design第2轮已购回答对本根最终Brief做完整预检**全绿**（`tools/replay_candidates.py`
  只读重放复现），系统却因上游Preserve改了几条义务措辞而整份作废，又买两轮Design加一轮Preserve
  （约12.4万输入／15万输出，占全书输出30%），随后单元额度耗尽停书。
- **称谓被当成语言模型返修题**：义务里的人名泄漏无条件派Preserve删名字；被提及未出场的人物没有显示面；
  成书的正文章包里称谓投影发生0次；Design提示词只在事实锁一条里顺带提到状态值用已释放称谓，对事件、
  场景说明、全书风格、声线没有写明规则，只有校验在拒。
- **机械工作交给了模型**：associate的same_state_dimension 90对里89对在编译器里是空操作
  （同对象同键本就是同一状态地址）。
- **返修不窄**：Preserve换两个词用了54,390输入／19,428输出；Design两次返修都是整份重写，反馈321行实为一个根因。
  Preserve没有条目定位器（RC-47的局部修订只覆盖Design与Hook），CS-F5报告所称「Preserve条目返修已落地」
  不成立，那两轮是整份重写。
- **记录不真**：被拒或被回派的候选记成`accepted=true`（`run_body_design`的validate在回派分支返回空列表以结束循环）；
  已交付的书`accepted_body=false`；逐轮报告工具在本根崩溃（H0已修：分母改为登记账里的单元）。

同一本书在rc15与本根两次独立运行里，associate都用满3轮、Design都没在额度内过闸。

### 指标与约束

| 指标 | 含义 | 读数来源 |
|---|---|---|
| M1 版本数 | 每个节点正式产物的接受版本数，目标1；每个接受版本原子发布一次，跨节点修订产生新版本、旧版可追溯 | CommitBundle |
| M2 首答通过 | 首内容轮候选通过owner完整验收的单元占比 | AcceptanceRecord（记录修正后） |
| M3 返修宽度 | 修订轮的输入、可见输出、推理token各自相对首答的比例；无关条目字节不变 | ledger＋候选差异 |
| M4 自动完成 | 无人工补丁、无unverified采用、无停止 | 修订记录＋workflow_status |
| M5 返工占比 | 返修与返工占输出token的比例 | ledger |

约束：不增加工作流节点；不提高任何重试或额度上限；不加LLM语义审阅节点或判官；不做付费A/B；不调effort；
不引入任意目标称谓创作；语义仍按`unver`披露。工程验证全部用fake transport与只读重放，零付费；
重放的实际读数分别回答“已购反例能否收敛”与“新书首答通过率”，二者不可互相冒充（见末尾验证口径表）。

### 当日四项裁决

① Design自有文本里的未释放人名走统一投影（修订CS-F2④：不再一律由判据拒收后整份重写）；
② 表达机制陈述在Stage2源头不点名（提示词规则，**不设拒收闸**，渲染期确定性兜底；与裁决选项文字里的
“接受时机械校验”不同、效果相同——默认保留恒安全）；
③ 共享事实锁由compiler派生支撑引用（修订上文B节「锁位不得隐式成为支撑拍」）；
④ 正文姓名拆分只加规则与局部修句，派生称谓另立项。
两份审阅一致、直接进计划的：已购候选沿用、Preserve条目返修、associate机械部分交编译器、接受记录分层、不提高额度。

### A．版本、候选与沿用（RC-50～56）

**发布语义（RC-56）。** 每个接受版本以一个CommitBundle原子发布一次；跨节点修订产生新版本，旧版本在封存账里
可追溯；交付清单引用的各输入版本必须彼此一致。M1数的是接受版本，不是“全程只许发布一次”——上游被修订后
下游随之重做是新版本，不是重复发布。

**候选集合（RC-52）。** 权威仍是`novel/work_units.py`的工作单元账与响应blob；候选登记是可由它们重建的索引，
登记该单元全部已购候选（含补丁链合并结果），不被后来的失败候选覆盖。每条记：wire原字节摘要、生成它的请求身份、
采用引用链、生成时所依据的上游版本摘要、最近一次预检摘要。候选的读取与预检读数由`tools/replay_candidates.py`
只读重放先行验证。上游版本摘要（谱系）授予**本次接受循环验收过的全部候选**——无论最终被接受、被拒还是被上游
回派（被回派的候选正是上游修好后最值得沿用的，不能等到发布成功才登记）；账里更早、没有谱系的候选
（H1b之前、崩溃窗口）如实留空，不补造，也不被覆盖。

**节点重入的判定（免费步骤全部先行）。**

1. 兼容性：wire协议字面量相同、能按当前schema构造、章数与篇幅约束未变。
2. 上游差异：对“候选生成时的上游版本”与“当前上游版本”的修订对照wire做结构化差异（复用
   `novel/stages/revisions.py::wire_diff`），条目按（kind, anchors）对齐，分为未变、变化、新增、删除。
   **位置参与判定**：义务编号（`ob01…`）由下标派生，候选里的承载标注引用的是位置，所以重排、或前面有增删
   而换了编号的条目，内容逐字相等也是变化；同键重复的条目按出现序加后缀对齐，不抛错。
3. 择优：所有兼容候选按当前输入完整编译与预检；全绿者优先，其次剩余根因最少者，同分取最近（RC-52）。
4. 动作（RC-50／51）：无差异，或差异**只有**被证明等价的变化条目，预检全绿即采用、零调用（新增、删除条目
   一律不零调用放行：编号漂移、新义务无人承载）；变化条目不能证明等价，
   只复核引用这些条目的承载拍，一轮条目返修，允许原值不变；删除条目的承载拍进入复核目标；新增条目走可选目标
   的条目返修（模型只在给定的拍闭集里选承载位）；预检另有问题并入同一轮目标。没有复核目标时：候选全绿＝
   无拍受影响，免费沿用；候选自身预检未过＝普通接受循环（同请求身份免费重放已购回答，换了身份才新购），
   **不从该候选起步再买一轮**——重入那样做会每次重启都重置修订额度。
5. 整份新购只限闭集原因并留痕（RC-53）：没有候选；协议不兼容；章数或篇幅约束变化；候选无法按当前schema构造；
   上游是全新回答（与候选生成时的版本之间没有修订谱系）。

**称谓规范形。** 把文本里每个唯一归属某人物的登记称谓逐次换成该人物的记号，保留每次提及，再归一标点
与空白。规范形相等只说明登记称谓与标点空白的差异，不能证明完整故事语义等价。纯函数，复用现有字面扫描。
相邻同人称谓可能是重复呼唤，不再折叠为一个记号；同位语增删若没有独立机械证据，也不能靠规范形免费证明
等价，走受影响承载拍的既有窄复核。同次数的登记称谓替换仍可在完整预检通过后零调用沿用，不整份重购。
差异摘要只能定位变化、不能判断等价：本根7条变化义务里只有3条能机械证明仅是换称谓，另4条是模型改写——
不能证明的走受影响拍的一轮窄复核，既不零调用放行，也不整份新购。等价判定针对**整条目**：位置不变，自由文本
字段（`statement`、`satisfaction`）规范形相等，其余字段（`must_preserve`、`requires`序号、字段集）逐字相等
（`anchors`比集合）；只比`statement`会把验收条件、前置义务、必保性的变化也当成“只差称谓”。

**采用凭据。** 修订记录新增kind`carried_forward`（OwnerRevisionRecord升V2，新增必填`applies_to_request_sha256`；
V1读兼容登记在REQUIREMENTS兼容层表并写明删除条件）；成本链`ledger-row`指向原attempt，本根只记一行零token的
cached行；重启按当前请求身份免费重放。人工wire采用与仓外换算采用仍是各自的档位，不并入沿用。沿用只是
没有更高优先级采用时跨请求身份的快路径：同身份（缓存重放）、已登记owner patch、显式重采与已登记采样选择
一律让位于普通接受循环。

**记录真实性（RC-54）。** `AcceptanceRecord.accepted`固定为“通过owner完整验收、可发布”；API是否返回看`parse`；
是否被采用只从CommitBundle派生；沿用、历史采用、人工补丁、重采各有独立修订记录kind。上游回派不再以空错误列表
冒充通过：材料问题归上游时该attempt记`accepted=false`并带材料问题后上抛。文本节点（正文、楔子）的机械闸错误同样
进入接受记录。单节点路径跑完交付后`accepted_body=true`，`workflow_status`的终态与交付报告同源。

**中断恢复（RC-55）。** 三个窗口各自用现有证据继续，无重复购买、无重复发布、不沿用失效的接受记录：回答已存证
而候选登记未更新；补丁已验证而未发布；上游已修订而下游未复验。

### B．称谓：显示面、投影与必保揭名（RC-60～62）

**显示面（一个解析函数，正文、Hook、Design原文闸共用）。**

1. 第一人称叙述的聚焦人物：角色词「我（叙述者）」。
2. 其余人物：该位置之前已释放称谓里按**释放顺序**的第一个。Design提示词写明约定：首个释放的称谓就是叙述中
   指称该人物的称谓，人物一被提及就应释放一个纯称谓。
3. 尚无释放：`canonical`若是纯称谓就用它，否则取源文最早登记的纯称谓。
4. 都没有：无显示面。Hook的concealed人物不走第3步。

**已落地口径（H3，2026-10-06）。** 章首已释放前缀与回落纯称谓分表携带（`ChapterIR.display_prefix`／
`display_fallback`）：回落面同表会让章内后续释放被章首回落面抢占首位，违背第 2 条「该位置之前已释放称谓
里按释放顺序的第一个」；分表后 renderer 依第 2 条先取已释放面、无释放才回落（渲染期不读 Characters，D2）。

**位置。** 拍行按拍前缀，含本拍释放：本拍正式介绍之后同拍可用，拍前不可用。场级说明行取到场末，但不参与
揭名判定。书级行取章首。释放前缀对全体人物有效并保留释放顺序；H3前曾只收本场参与者且按字典序排，
导致丈夫从第3章起在妻子视角的包里被叫作“哥”，此旧口径已退出。**Design原文闸不按位置**（2026-10-07用户裁定，见下）：
本段的拍／场／章首取位只用于写手章包的显示面与必保揭名的拍位闸。

**投影。** 内部材料用姓名指认人物时按唯一绑定逐次投影成显示面；同句自然混用不同登记称谓不拒收，
保留各次提及及其中的行动、对白。只有原文独立邻接的显示面才折叠同位语（“表弟裴书远”→“表弟”），
不能因前次投影生成相邻显示面，或另一登记称谓内部含相同显示面字样，而吞掉重复呼唤。canonical字段与来源文本保持原样，投影只改送达字节并留痕；
compiler不替Design提前释放姓名，正式必保揭名、唯一归属与concealed边界仍受原闸保护。

**Design原文闸按章判（裁决①的落地，2026-10-07用户裁定，取代H3的按位置口径）。** 写手拿到整章章包，正文最终闸
（`body_checks`）也只按章判已释放称谓，章内谁先谁后不检查；投影绑定表（`ChapterIR.withheld_bindings`）同样是章级。
H3的原文闸却按位置（章首／场末／拍前缀）判「已释放」：章内后释放的人名既不在绑定表里（不投影），在较早位置又算
未释放（判泄漏），Design被卡得比它指导的正文还严——csf4第3章章目的与拍1事件提到拍2才介绍的裴书远、沈知言，
就让已购Design第2轮（H1b／H2零调用沿用的依据）从全绿变成3条泄漏。现改为**删粒度而非加规则**：已释放集与绑定表共用
`chapter_surface_releases`（与正文章包同一函数，闸与绑定表由构造一致），显示面取章末。保留：本章及之前都没释放过
的人名出现在送写手的材料里仍是泄漏（真正的提前披露）；必保揭名按拍位判（保情节骨架）。不动：写手章包按拍取显示面。
csf4只读重放：第2轮3→0（重新全绿，可零调用采用），第4轮6→1（余下为必保揭名）。

**必保揭名（硬闸，可窄修；RC-61）。**

- 识别：一条认知变化同时满足三项才算——所属命题被identity或reveal类必保义务引用；该命题的源证据句段正是某带名
  称谓在源文首次出现的句段；命题文本含该称谓。三项都来自已有类型化数据（义务类别、Blueprint的basis句段、
  Characters的`source_refs`），不用文本共现作判定；同人多个已登记称谓的普通混用不另造揭名要求。当时本书核查命中“表弟的名字是裴书远”“裴书远的同窗的名字是沈知言”
  两条，该样本无误报（单看义务类别不够准：两条真揭名命题同时挂在identity与event_core义务下，identity义务也挂着非
  揭名命题）。
- compiler把结果作为`requires_released`写在Brief的认知变化条目上（不暴露句段号，保持源盲）。
- Design：该know所在拍必须已释放所列称谓，同拍释放合法；违例带路径、窄修。这类称谓不投影。
- 正文：承载该认知的章，正文必须出现该称谓字面；违例走段落级修句。已落地面：章机械校验经唯一构造点
  `chapter_prose_validator`（`novel/stages/stage5/body_checks.py`）绑定本章材料——写章循环、付费边界接受记录
  （RC-54）、wire显式采用与补丁预检、复核捕获同一把尺（此前采用／补丁面漏带`required_literals`与
  `scene_markers`，缺字面的wire能被采用、随后在写章循环被拒）。段落修句轮的回答是行替换JSON：请求用专用
  system提示（`BODY_PARAGRAPH_REPAIR_SYSTEM`），付费边界按**确定性合并后的稿**判定接受（成功的修句记
  `accepted=true`），解析失败如实留证并把原有缺字面问题带入整份重写轮。
- 漏检（三项未同时满足的揭名）退化为普通投影，不比现状差。其余姓名全书未被用到属于合法的设计选择，只出读数。

**投影后的残余归属。**

| 残余 | 责任方与修法 |
|---|---|
| 已绑定称谓，但该人物在此处没有显示面 | Design：补一行释放或挪承载位 |
| 必保揭名的know落在释放之前 | Design：挪know拍位或释放 |
| Design自有字段里的裸姓、未登记别名 | Design：改该字段 |
| 义务文本里的裸姓、未登记别名 | Preserve；正常情况下已被Preserve自闸前置拦下 |
| 世界约束、来源缺失或多义 | 停止（现行规则） |

**能本地确定转换的不回购（RC-62）。** 协议枚举词（认知模式、时态、可见性）按既有措辞表在渲染期替换；
人物slug换成显示面；表达陈述里的姓名按“叙述者角色词、显示面、中性泛称『其他人物』”的次序渲染；
都留痕。表达陈述保留Stage2源头提示词规则、**不设拒收闸**；英文状态值是Design自由生成，系统枚举早已有措辞表，
状态值用提示词规则加读数、不设闸。只有确实需要模型改的（裸姓、未登记别名）才设闸。最终请求文本不含
anchors／quotes，Hook材料里的姓名在渲染期已投影（本根17次），二者保留为对最终请求文本的验收断言。

同拍多条义务各说一遍同一事实来自Preserve的切分，措辞不同，机械上只能去掉逐字相同的行，不按相似度删。

### C．共享事实锁（RC-63）

一项事实定位一次，多条义务引用它。锁的承载拍属于其中一条共享义务的real集合即成立；其余共享义务由compiler
记入`supporting_beat_refs`。上文B节“只校验先后”**仅针对这些派生出的引用**（承载拍不晚于它们的接受拍）。
已落地口径（H3）：先后在Design接受面即带wire路径判掉（`fact_lock_carrier_errors` 的 `accept_refs`，码
`fact_lock_outside_obligation`，开放承载位与相关real拍两种修法），compile面`compile_support_early`只作兜底
——派生引用在compile面没有编辑位置，单靠兜底会把原本可窄修的问题变成无路径的整份退回。
原有承载检查全部保留：句柄在Brief登记、一锁一承载、承载状态存在且在该拍唯一、时态锁须同modality的set
（计划不替代已发生）、值未知锁须`op=unknown`、未回答锁不承载、锁不标在知情行。新增一项：锁的主体须包含于承载
状态的主体（此前可以把丈夫的锁标到别人的状态上而通过）。键与值是否同义仍是`unver`。

### D．窄返修协议（RC-57～59、64）

| 环节 | 要求 |
|---|---|
| 覆盖 | 全部JSON节点都有条目定位器；跨节点回派与节点内返修同一协议，基底是owner已发布版本的修订对照wire。**已落地口径（H4，2026-10-06）**：定位器齐面——chapters（`chapter_patch_locate`，CS-patch）、Design／HookDesign（`locate_design_patches`）、Preserve（`locate_preserve_patches`）、associate（`associate_patch_locate`，配对条目）、book／names／naming（`book_patch_locate`／`names_patch_locate`／`naming_patch_locate`，与各自 compile 同源视图，不新增判据）；覆盖增删、构造期复检与协议字面量等**替换不了**的问题仍走整份出口，不静默漏项。**请求路径不重叠**：同一条目的父、子错误折叠到父条目（`novel/stages/common.py::group_located_errors`，book／names／naming 共用；chapters 的同名折叠在 `chapter_patch_locate`）；associate 的多句柄位置（同章／因果／续场的两端）只命中**同时含全部句柄**的那一行，未知句柄按所在字段定位，协议校验错误按 `loc` 的条目下标直达 |
| 目标 | 固定路径；或**可选目标**：责任位置须由模型决定时（必保义务漏标、新增义务、需选释放位置），给出候选拍闭集，回答的路径必须落在闭集内（RC-58） |
| 输入 | 协议规则＋失败条目＋依赖切片＋合法取值。故障证据、替换范围与只读上下文分离，目标子schema与实际生成契约同源；切片闭合完整替换单元（整场须遍历其全部拍）的必要事实、状态与前缀，不带整份原请求，也不带前序材料全文 |
| 输出与应用 | 复核目标与错误定位目标合成后，`novel/llm/paid_requests.py::normalize_patch_targets` 在依赖切片前再次收口祖孙／重复路径：只折叠已有授权父项，合并全部问题，任一必选则父项必选。冻结基底摘要、全部目标及目标子schema为同一计划，用于依赖切片、请求、回答校验与合并。只返回目标路径的完整替换，省略非法键可删除该键；范围外改动拒绝；合并后跑完整验收，无关条目字节不变 |
| 反馈 | 按根因聚合成任务并附受影响位置 |
| 无法定位 | 视为判据缺路径的工程缺陷：停并报规则码，不进整份重生；契约测试保证每个现役规则码或可定位、或有可选目标、或登记在整份闭集（RC-59）。**H2 裁定（第四态）**：Guard M1–M8 与 compile 依赖码落在确定性免费节点（`str06.stage4.compile`）的复跑面上，没有 Design wire 编辑位置——登记第四种处置 `stop`（按所在面正式路由回派或停机报码），不把它们塞进整份闭集冒充可修复；路由表与判据一一对应，`tests/test_str06_patch_routes.py` 双向核对码集合并静态验证每个 locate 码的发射点带 wire 路径。裁定全文见 `docs/DECISIONS.md` CS-H H2 行 |
| 结构可修复性（2026-10-17） | 逐项保留Pydantic错误类型、故障位置与所属schema，聚合展示不得丢弃这些证据；额外键替换完整父对象、缺字段替换能补齐字段的对象、数组约束替换完整数组、联合分支标签不作JSON键。Design场内额外键按owner政策升整场，程序不自动移动语义。发请求前核对目标合法且允许必要编辑；计划无效以`repair_plan_invalid`停止，零返修外发。坏稿值类型不参与权限选择 |
| 整份出口（闭集） | JSON：回答无法解码或整体形状失败、协议字面量不符。文本：篇幅带、空回答、包装。局部称谓、单条义务、编译缺陷不得进入 |
| 无进展 | 同一输入下同一根因仍在即无进展，候选字节变化不算进展；回派与重入不重置任何额度 |
| 计量 | 输入、可见输出、推理token分列 |

Preserve的条目返修（RC-64）沿用同一协议：定位器按`obligations[i]`，基底取已发布版本的修订对照wire（不再以
`indent=2`展开上一版全文），切片为该义务anchors的源原子与证据窗口；Preserve自闸判据——义务文本里的裸姓与
未登记别名——与归因共用同一把尺，带条目路径，使这类问题在Preserve自己的接受循环里就被逐条定位。
已落地口径（RC-64，2026-10-06精修）：闸面＝裸姓（含复姓整字）；认领面＝家族构词（「陆家」一类集体指称，**不拒**
——同家族人物释放后是正文合法字面，Preserve阶段不知释放时点）与**唯一归属、可投影**的带名面；多义带名面
（同一登记称谓属于两个人物）永不投影、不作认领面——其中的裸姓成分如实报出（`preserve_name_literal`）。

### E．associate维度句柄（RC-65）

编译器里状态槽位的身份是（作用对象，键），`same_state_dimension`的唯一作用是把近义键改名成规范键，所以同对象
同键的配对没有效果。改为由编译器按（作用对象，键）生成维度句柄，模型只为键不同但同维的维度配对；规范键传播到
整类；本章已自开自闭的close不进可配对闭集。显式判断仍由模型作出，不违反D8。

**已落地（H4，2026-10-06，`stage2-associate-v3` 随 rc17）**：维度句柄 ``dim{nn}``＝同地址全部写入的聚合
（首次写入序编号，canonical＝类内首次写入最早者的键，按地址传播到两类全部写入）；维度配对不做同章拒绝
（维度跨章聚合，同章近义键正是要模型判断的同一性）；可配对 close 闭集＝`pairable_closes`（章内已自开自闭
的 close 不在其中，wire 引用即精确 issue）；配对条目定位器 `associate_patch_locate`（句柄反查条目路径，
需新增条目或歧义退整份）。csf4 根只读重放：已接受轮 90 对配对＝89 对同键空操作（不再需要）＋1 对近义键。

### 验证口径与残留风险

| 层 | 能证明 | 不能证明 |
|---|---|---|
| fake transport的stage测试 | 控制流、补丁范围、中断恢复、记录真实性 | 模型行为 |
| 旧根的免费历史重放（`tools/replay_candidates.py`） | 已购候选在新规则下是否收敛；已知反例被拦还是被放 | 新书首答率、真实修复成本 |
| 两本独立新根的真实运行（另行授权） | 这两本能否自动完成，M1至M5的实际读数 | 普适性；两本只是初步证据 |

- **新判据可能带来新的首答失败。** 本计划新增的硬判据有四项：Preserve文本的裸姓与未登记别名、必保揭名的know拍位、
  承载章正文必现字面、锁的主体包含。它们都带路径并与窄修同批落地；净效果只能由新书实测回答，不从重放外推。
- 必保揭名的识别靠三项信号合取，在本书上准确，其他书可能漏检；漏检时退化为普通投影。
- 无释放时的称谓回落按登记次序，不懂视角：单视角书成立，多视角书要按视角选称谓，需要“谁这样称呼谁”的数据，
  Stage2目前没有采集。
- 窄输入下模型的修复质量没有实测；额度内不收敛就停并报告，不回退整份。
- 候选沿用保证结构对齐与机械预检；不能证明等价的变化靠一轮窄复核，语义仍是`unver`。
- 无法定位即停，在判据清点完成前可能多停一次书；用规则码覆盖测试把它变成开发期红灯。

## 2026-10-10 现役材料与生成边界

本节同步现役 prompt、确定性投影与机械闸，不新增生产语义判官。Preserve 保存策略
v10／wire v5、正文 renderer v12／写手协议 v11、正文 mechanical validator v3 分别
承担材料选择、送达与包装识别；版本属于各自身份，不互相替代。

- Preserve 必要触发、答话、限制、权衡与选择由 statement、satisfaction 和 requires
  所指义务的联合材料交接；已经明确的前提不重复抄写。不同发生时点的必要前提分别
  承载，可改写动作进可选 details，源 anchors 不能代替 source-blind 下游看得懂的语义。
- Design 用已有事件、realization_brief 与 entry 组织目标演法；新增沟通、决定、信息
  获得及后文依赖归 Design。写手可呈现自然移步、短转场与即时动作，但须延续既定
  位置、感知通道与事件先后；补写钟点不得让出发早于触发决定，回溯须明示。
- 同人多个唯一登记称谓逐次投影，普通共现不判揭名；每次提及与中间行动、对白保留。
  写手投影的原文独立显示面同位语折叠与候选规范形的等价比较是不同职责，前者不能
  授权后者压掉提及次数。已释放字面也不代表任意人物都可以这样称呼他人。
- Hook 整拍禁揭仍与选用该拍冲突。具体事实地址的禁揭允许同拍其余材料，但每条
  选中该拍的材料都须 withheld 隐藏该完整地址，或用 presentation 替代整拍投影；
  其他地址的遮罩不顶替，起笔事实、阅读期待与其他条目仍各自复验。
- 正文首行只有符合章标题边界才因标题字样拒收；“尾声未尽”等自然句子可用。
  实际章号、标题、Markdown 标题与围栏仍属于包装失败。

真实消费者反例由下表定位，是否执行及通过另按本批测试记录报告，不由文档签绿：

| 边界 | 验证入口 |
|---|---|
| 联合交接与目标桥梁到达写手，未知事实不补值 | `tests/test_str06_quality_materials.py::test_trigger_in_statement_reaches_the_writer_without_repeating_it_in_satisfaction`、`test_necessary_cause_decision_and_transition_reach_the_actual_writer_request`、`test_bridge_design_cannot_fill_an_unknown_fact_during_the_actual_acceptance_loop` |
| 混称与重复提及保持，Preserve 不为自然混称返修 | `tests/test_str06_name_projection.py::test_adjacent_name_mentions_are_not_folded_as_generated_appositions`、`tests/test_str06_preserve_gate.py::test_natural_mixed_names_reach_the_writer_packet_without_preserve_repair` |
| 提及次数变化到真实沿用消费者，不再机械证明免费等价 | `tests/test_str06_candidates.py::test_mention_count_changes_reach_real_design_reuse_consumer`（含同次数别称替换的零调用对照） |
| Hook 指定地址遮罩与独立泄漏闸 | `tests/test_str06_hook.py::test_same_beat_forbidden_fact_mask_reaches_hook_writer`、`test_canonical_hook_recheck_rejects_removed_fact_mask`、`test_same_beat_fact_mask_does_not_authorize_opening_or_answer` |
| 普通首句与真实包装区分 | `tests/test_str06_body.py::test_title_words_in_a_natural_opening_are_not_wrappers`、`test_actual_heading_wrappers_are_still_rejected` |

必要语义送达和机械通过不证明文学质量。因果自洽、人物转变、动作承接、时间措辞与
实际称呼关系仍须联合阅读验收；首答格式通过与首答完整可用分别报告，返修和免费
采用不得回写成历史首答成功。

## 数据流与职责

1. compiler／renderer在确定性投影时保留可重建的字段来源；来源绑定正式产物、
   字段引用和目标消费位置。诊断只作派生证明，不成为第二份故事真源。
2. validator返回结构化问题（code、稳定位置、命中字面、来源和释放依据），只读。
   现有字符串错误由该结果投影，禁止下游再正则解析展示文字来做业务决定。
3. 纯归因函数输出可证明的责任或明确未决；统一路由表决定revise_self、
   revise_upstream或stop，orchestrator只执行登记动作。
4. 具备完整正式输入的检查在owner接受前执行；最终compile对实际Writer／Hook
   材料再次验证。两处复用同一规则，不能靠前置局部通过跳过最终消费验证。
5. owner修订发布后按实际消费身份级联；原发现节点成功复验才resolved。
   无关章包不重购，unknown、INVALID、过期、合法空值保持区分。

先使用内部结构化派生值；若正式artifact或持久runtime协议形状必须改变，同批
显式升版并登记免费重建／旧版本拒识，不向领域schema塞日志或供应商usage。
不新建通用事件溯源框架、全局失效DAG或另一套预算账。

## 收敛与旧根恢复

问题身份由规则码和稳定正式位置组成，不含展示文案、时间戳、run id。
同任务严格缩小的问题集可以在原额度内继续；相同集合、历史循环按现役规则停止。
patch与整份修订都占内容轮；跨owner仍受原累计上界保护，不因重启或改名退还额度。

旧根曾被错误二分路由消耗的费用、原issue和内容轮必须保存。新归因先免费重验；
错误旧issue的退出须走显式、身份绑定且可审计的恢复操作，不能简单删文件、改owner
或把rounds_total归零。新owner能否执行仍由其原有许可、额度和当前输入证明决定。
若现役入口不能表达该更正，本批须补恢复契约与反例，不能用手工改账代替。
冻结生成代码不追改；工程新epoch和原冻结质量实验明确分开，人工恢复不得改记自动成功。

## 用量与成本

统一纯解析器处理原usage，供当前付费入口、仍现役的client和缺账恢复共用。
合法整数0保留；缺失或null保持未知；top-level和completion_tokens_details中的
reasoning均可读取，两处非空冲突、非法类型、负数或大于completion时该读数未知并
报告诊断。保留原响应和usage，不因计量诊断重新购买或丢失可用回答。
reasoning不另加到completion，只有两者均已知且一致时派生回答token。

ledger仍是成本权威；原账已有行不因新解析规则被覆盖。历史补证只读且绑定attempt
及原响应摘要，报告区分账内值与补证值。缺账补尾幂等，免费采用单列，失败与重试
纳入真实成本。供应商实际扣款、按执行时费率估计、统一费率对照分别表达。

逐轮报告按工作单元的内容轮ordinal及其attempt顺序关联AcceptanceRecord，不按时间戳或随机ID排序。
免费复检可能更新同轮接受记录，所以当前首内容轮接受率不是历史首过率；对照实验须在每臂结束时
冻结当时记录，之后的免费恢复另留快照。网络重试、内容修订和免费采用分别统计。

### 请求配置与权限

用户已裁决：模型、thinking与effort是请求数据，付费和正文外发是独立权限。
Step的加法契约新增可空`request_profile: LegacyLlmProfile`，复用唯一配置schema，不含密钥。
历史Step字段缺失读作None（未知），不补默认值；此兼容边界与删除条件登记在REQUIREMENTS。
新配置的付费Step及其免费followcheck必须携带相同配置，CLI JSON与Step字段全等；父运行配置
不一致即在请求前拒绝。免费Step仍禁止executor_backend、`--yes`和`--allow-egress`。
连接信息仍在安全配置边界读取，不能通过请求配置覆盖API密钥、URL或代理。

`tools/effort_ab.py`只构造单节点配置与正式CLI调用，默认免费preview，不是完整实验执行器。
只有真实请求与ledger均匹配冻结配置，才能证明配置生效；修改NODES或UI显示不能作为证据。

## 实验与结束条件

输入瘦身先证明必要字段完整送达、派生可重建，再衡量实际输入token。思考配置
在同一节点冻结正式输入和其余变量，逐臂记录请求身份；不承诺任何节点默认降档。
工程测试使用fake transport。新付费实验先核对原授权的书目、模型、外发对象、
输出根、预算与配置范围；不把旧冻结三书授权解释为无限新增对照或追加额度。

整书阶段通过正式产品入口执行到Stage7，检验最终文件、manifest、费用账和重启读回。
保留原三书分母和失败轮，Golden oracle只在既定评测时机解封，生成修复不读取。
六维评价对应BA-04；旧九维阅读观察作为补充证据，不产生另一套成功判据。
只有完整且质量达标的可比成书支持整书降费；当前Stage4费用只是阶段基线。
