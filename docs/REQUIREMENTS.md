# STR-06 已关闭 · 活动账（余 R10d／R7 待办）

本文件仍是唯一活动计划，登记**未完成批次**、长期义务与删除条件。目标架构与字段
边界见 [str06.md](schema/str06.md)，现役行为以 [BACKEND_CONTRACT.md](BACKEND_CONTRACT.md)
及 `contracts/` 为准，开发遵守根及所属目录 [AGENTS.md](../AGENTS.md)。

**2026-10-03 重置（关闭后）**：STR-06 于本日关闭——B6-B（R10c）成书经 agent 侧
六维／十案例验收全 pass 后用户指示执行「STR-06 关闭与退役尾项」，三件尾项一次闭合
（提交 `b3038a41`）。已完成批次的执行细节压缩为索引，完整记录按
`git log -- docs/REQUIREMENTS.md` 读取；裁决缘由在 [DECISIONS.md](DECISIONS.md)，
复用教训在 [LESSONS.md](LESSONS.md)。R10d 的执行指南仍见 [PLAN.md](../PLAN.md)
（R10-0～R10c 与关闭批已完成，余 R10d 段有效）。

## 关闭结论摘要

- **交付**：《夫君的报恩》全书（r10b 根，零 owner patch），
  `stage7/final/夫君的报恩_rewriting.txt`，final_text_sha256 `09d80fb1…`、
  delivery_identity `71c54085…`；工程核验全绿（章序连续、改名零残留、manifest
  匹配、重启读回 fresh）。
- **阅读验收**：agent 侧双书通读＋Golden 六维与 GS-01…10 逐案全 pass、7 项压缩
  诚实披露（承载功能存活，结构不构成复刻）；结论绑定交付 hash。两根合计
  76 笔实付＋120 缓存（r10b ≈¥14.33 峰时），自主完成度＝**无人改稿、有人辅助完成**
  （干预仅代码修复＋额度追加＋余额充值，逐项登记）。
- **关闭批**：Agent 终验改接 delivery manifest（通过仅 ready_with_disclosures）、
  存量解码面删除（RETIRED_NODES／reviewer legacy／workflow_transition，拒识改
  「非现役节点」通则，产物层与盘面探测保持闭合）、退役台账清账（「不得回来」
  守卫迁 tests/test_retired_surface_scan.py）。裁决见 DECISIONS 2026-10-03 行。

## 当前状态与下一步

| 活面 | 状态 | 依赖与放行 |
|---|---|---|
| R10d＝B6c 普适性 | [ ] 未开始，另行授权 | 《三人成行》全链＋《夫君的报恩》重复生成；前置与冻结判据见下节 |
| R7 质量与输入优化 | [ ] 未实施 | 由 R10c 通读问题与后续阅读证据驱动，见「后续优化次序」 |
| 契约瘦身与返修收敛（CS） | [x] 批 0 完成（2026-10-03），批 1–4 见「契约瘦身批次」 | 样本与证据：str06_r10b 全产物审计＋ledger 复核（用户裁决采纳的落地顺序）；每批验收同时看成本指标（每节点整份生成次数／completion／首过率） |
| 风格画像 | 开放项 | R10c 成书若复现 b6a 四条风格批评（一句一段／环境密度／幽默通道／POV 漂移）即以该批评为动因立项，须用户裁决落点 |
| 工程余项 | 维持登记 | 直连强制重采（同身份缓存命中致重问无效）、REDO 级联自动重放（R-14.4／E-1 产品裁决项）不在主线扩散 |

### R10d 前置与冻结判据（从 R10-0／R10c 提炼，原文按 Git 历史）

- **Golden Set v3.0 双书已冻结**（2026-10-01 用户提供）：`Golden_Set_v3.0_{夫君的报恩,三人成行}`
  md＋yaml 双格式；机械核验全绿（源文件 sha256 逐字节匹配、216＋139 个锚点引文
  逐字命中源文且行号精确）。**污染规则**：夫君为开发可见判据，三人成行保持隔离
  评测资格（R10d 依赖此规则，隔离根不得提前消费其判据）。
- 开工先更新 `tests/golden.py::CRITERIA` 为六维并冻结 manifest／baseline／成本；
  人工阅读为主，模型辅助另授权；只改评价过程时重评现有成书，不自动重生成。
- **对照缺失如实登记**：不做新旧 renderer 付费对照（现无可复用完整无 patch
  现版本成书）；版本结论只报绝对达标，不宣称相对改善。每书两份新稿＝R10c r10b
  首稿＋R10d 独立空根重复生成（同候选版本固定源文/代码/协议/模型/预算/停止规则，
  不采用上一轮已购回答），不扩大样本、不择优替换失败轮。
- r10b 首稿与《夫君的报恩》F5e 对照只使用历史读数（原始根已清理）；《三人成行》
  对照 Workbench Golden Set oracle v3.0。
- NameMap v5 跨书赋值、第二本表达力及第三人称串人称问题须有对应证据（本书
  第一人称不能代签）；未评或证据不足保持未验证。



## 已完成批次索引

完整执行记录按 `git log -- docs/REQUIREMENTS.md` 与各批提交读取；下表只留结论。

| 批次 | 结论 |
|---|---|
| R0–R6／R5b／语义冻结闸／R8／R8b | 字段送达、关联、两层 IR、有界修订回派、发布 seal、fake 产品链、Design 形状 Schema 化——全部交付（见「质量重设计批次」表） |
| B0–B5／收尾批 | 契约五层运行记录、五 owner 双真源、章包并发恢复、HTTP→CLI→交付→重启 fake 全链（见「职责解耦批次」表） |
| R9a／R9b／B6a | 确定性止损与修订入口、rc11 集中升版、3446 项终轮全绿与付费基线冻结 |
| R9c 首段（已停止） | 37 笔 ¥3.97 到 Stage4 compile 的 canary；2026-09-30 用户裁决停止，根只读留证；核读问题由 R10 修 |
| R10-0 | 登记／冻结证据／真实消费占比／送达守卫自检／C1–C11 反例集／Golden Set v3.0 冻结／基线读数 |
| R10a | a1 引文证据与时态→a2 Preserve 锚定→a3 冲突回派三分支→a4 Brief 锁定＋Design v5→a5 位置校验（免费，fake） |
| R10b | WriterIR 投影收窄、写手协议 v7、v6/v7 免费形状对照（免费，fake） |
| 收口 | §9.5 五文件、fake 产品链、旧根关闭式拒识、默认非 corpus 回归；rc12 冻结付费基线 |
| R10c＝B6-B | c1 语义段（两根：旧根级联螺旋止步→裁决甲硬化批换 r10b 根）＋c2 成文段；全书交付与阅读验收（见关闭结论摘要）；执行细节与两次裁决（甲乙）按 Git |
| STR-06 关闭批 | 2026-10-03 三件尾项闭合，提交 `b3038a41`；三轮回归迭代 3523 项收口 |


**人工阅读验收协议（R4 冻结；B6-A／B6-B 共用，全文唯一登记面）**

九维不打分，沿用冻结的问题：

1. 情节与因果是否完整；
2. 揭示时点是否正确；
3. 章间衔接是否自然；
4. 人物声音是否稳定；
5. 写手是否把 IR 写成了说明书；
6. 信息有没有重复交代；
7. 场景是否通过行动与对话展开；
8. Hook 是否有吸引力而不提前兑现；
9. 整体节奏与流畅度是否优于基线。

记录“章与段位置→维度→归因→处理”；归因仍为 Stage2 抽取／Preserve／Design／Bind／
编译投影／renderer／写手协议／写手发挥；处理为 canary 收敛或 R7。
R9c 核读在同一记录里补 owner、期望、实际和证据，不另立第二份维度表。
正文、阅读记录与补丁详细差异存运行根，不在仓库摘录。未评或证据不足保持未验证，
回执与 Guard 不代签语义或文学质量。

用户通读结论绑定确切成书版本／hash；正文改变时重验受影响结论，字节未变可沿用结论并
复核身份。工程问题修到通过；文学意见登记为 R7 输入。B6a＋B6-B 同时有效后按下方条件
关闭 STR-06，之后再进行 R7 与 B6c，不以已合入 main 代替验收。

## 后续优化次序

| 顺序 | 优化对象 | 证据与边界 |
|---|---|---|
| R7 质量优先 | 漏保／过保、源结构泄漏、因果、承接、声线、导演说明 | 按 B6 章／拍反例定位所属 owner；先证明章包语义充分，再改 prompt，不让 Writer 补缺失规划 |
| R7 输入精度 | 无关声线、重复约束／状态、冗长协议 | 统计实际 renderer 字节与真实 token，依显式依赖裁剪；不截断或概括必要事实，收窄正式书级材料同批改契约 |
| R7 付费效率 | 局部失败、复用、重试、并发与过度失效 | 先核请求身份、采用命中、429／unknown、活跃时间与成本分母，再调槽；并发不进语义身份，保留 D6 上限与截断即停 |
| B6c 质量回归 | 两本短篇六维 Golden | 先更新退役 rubric 引用并冻结 manifest／baseline／成本；人工为主，模型辅助另授权；只改评价过程时重评现有成书，不自动重生成 |


## 契约瘦身与返修收敛批次（CS；2026-10-03 起）

证据基线：`str06_r10b_20261002/夫君的报恩` 全产物审计（D1–D6 契约缺陷在产物上
机械复现）＋ `_service/ledger.jsonl` 逐调用复核。关键读数：主运行
（`1790925174`）规划链 completion ≈1.0M，其中 `str06.stage2.chapters` 占 65%
（650K，11 章 20 次全新生成）；design 当天 11 次整份重生成（多为跨 owner 回派
与授权重跑，节点内修订轮 ≤2 由 R3/R8b 闸住）；规划:写作 ≈ 30:1。机制现状已核实：
无进展检测（`validation_no_progress`）、sev/repair 分级、重试 prompt 可变内容
后置均已存在，缺口只在节点内无进展的「子集/交替」判定与逐轮失败记账。

| 批 | 范围 | 状态与验证 |
|---|---|---|
| CS-0 收敛止血 | ① D2 引文标点归一：比对用 `norm()` 尺、唯一命中回取段原文切片落盘（`novel/core/text.py::content_equivalent_slice`＋`resolver.canonical_quote`＋book/chapters compile 副本归一，validator 仍只读）。② D6 渲染修复：kp 句柄以 `Brief.propositions` 命题 statement 送达（`novel/core/text.py::kp_statement`；`project_context` 回填 IR 拍增量/知情边界/拍级读取/背景知情/未确认事实，`hook_context` 同尺渲染禁区与起笔事实；悬空 kp fail fast；renderer 升 `str06-render-v8`）。③ `ask_json` 无进展升级：本轮问题集是此前任一轮子集（含原样重现/只缩不消/A→B→A）即停 | [x] 完成（2026-10-03）。新测试 `tests/test_str06_quote_canon.py`（12 例）、`tests/test_str06_kp_statement.py`；受影响面 str06/llm/layering 全绿；非 corpus 全量回归见批尾记录。**不做**：重试 prompt 拼接顺序（已核实可变内容本就后置，未命中主因是跨小时/跨 run 缓存过期与上一版全文追加）；hard/soft 分级（sev/repair/degradable 机制已存在，str06 判据 repair 全 NONE，不再另造） |
| CS-1 可收敛缺陷 | ① D1 配对合并只认归一化后完全相同的 statement（`_BookCompile::validate_pair_statements`：不一致按 item 未决拦下回派 associate，不模糊合并、不替模型选边）；wire 重引用的 `statement=None` 回填类 canonical（r10b k41「learns「None」」不再进蓝图）。句柄章节命名空间（`ch{k}.{ref}`）与全局正式 ID 在 r10b 后的现行代码已存在，补齐回归测试。② D3 新增锚点**最大**源拍位置单调检查（event/reveal 义务允许并列，hook 不经 binding 天然豁免；`compile_execution_plan` 内，与既有跨场逆序闸并存）。只动代码与校验器，不动 LLM 契约 | [x] 完成（2026-10-03）。实测：r10b 全产物过新检查零倒置（n=19），按最小位置排则误报 ob17/ob16 一处（用户裁决取 max 的依据，本仓复算证实）；r10 运行复现 D1（2 处 ref 冲突含 null statement）与 D3（4 处违序）。str06 全量 852 例绿。**前置缺口如实登记**：跨书复跑不可行——仓内仅本书三次运行（r9_canary 无正式产物），新跑书需付费授权，《三人成行》受 R10d 污染规则隔离；跨运行复现已做，跨书验证顺延至自然多书产出。**D5 裁决归 CS-3**：两种写法的映射已单点在 `assemble_body_design`（C6 判据＋`test_c6_reader_confirmation_without_known_lock_rejected` 反例），「去 confirms」动 TargetDelta schema 且 D15 pending 投影依赖 confirms——纯 compile 归一化＝重写 pending/guard 机器而 wire 成本不变，与 design 一次改齐（CS-3）才有效 |
| CS-patch 条目级返修 | 机制：`ask_json` 增 `patch` 定位协议（owner 声明，返回裸元组不引入 stage→llm 依赖）；patch 轮＝**原请求逐字前缀**＋失败条目清单（缓存友好、模型保有源文上下文），输出仅 `{"patches":[{path,value}]}`，合并版过同一套 owner 校验；接受闸严格（全覆盖/无多余/无重复/坏路径整体拒绝）；采纳返回 `PatchedAnswer`（基底＋patch 多引用经 `_as_adoption_refs` 展开进 provenance/CommitBundle，B1 精确 attempt 聚合不变）；patch 修不动→无进展判定**提前到合并结果**（同题集不再买整份轮）；不可定位/形状失败→R3 整份修订原路径不变。接入：`str06.stage2.chapters`（65% 大头）——章观察闸重构为结构化视图 `_chapter_issues`（errors 是其无路径投影，判定与定位同一把尺）＋`chapter_patch_locate` 协议；book/design/preserve 后续按需接入（各自定位视图同批）。聚合面：`tools/repair_rounds.py`（只读）——每节点 fresh/completion、按语义单元首过率、失败码×轮次矩阵 | [x] 完成（2026-10-03）。新增 `tests/test_str06_item_patch.py`（12 例）；FaultTransport 补条目返修剧本（fault_once＝干净条目替换即自愈；持续故障＝原样重抄→无进展即停）；str06 全量 862 例绿。**记账更正**：逐轮失败集**已经**在 `AcceptanceRecordV1` 按 attempt 落盘（CS-0 所记缺口不成立于记录层，缺的是聚合面，本批补上）；r10b 实测读数——全书 31 个语义单元首过率 80.6%，associate 的「悬念 close 已章内自开自闭」码第 1/3 轮重现（契约缺陷信号，归 CS-2 收口），design 全天 10 次接受分项含跨 run 重放 |
| CS-2 观测/保产瘦身 | characters/observation/preserve 死字段删除＋派生回填＋短别名（D6）；LLM 面先变，落盘经 `expand()` 保持 rc12 形状（adapter 集中登记，删除条件＝下游消费迁移完成）；先瘦 `str06.stage2.chapters`（65% 大头）再 preserve | [ ] |
| CS-3 design 一次改齐 | design 字段删除＋`real:[…]` 标注＋bind 退役（semantic_binding 改 compile 生成）合成一次契约变更；world 布尔键引入稳定规则 id（`women_cannot_stand_alone` 类 value="true" 无机械 statement 映射，同批解决）；design prompt 只改一次、golden 只跑一次 | [ ] |
| CS-4 其余执行面 | 思考模式按节点开关（naming 关；hook/observation 先 A/B）；缓存前缀稳定化 | [ ] |

验收纪律：CS-2/CS-3 后跑 golden set 与六维阅读验收；每批同时报告每节点整份生成
次数、completion 与首过率（ledger 为权威账；`tools/repair_rounds.py` 是只读聚合面）。
记账更正（CS-patch 批核实）：逐轮失败集已随 `AcceptanceRecordV1` 按 attempt 落盘，
无需另建持久化。


## 恢复、替代与删除条件

| 对象 | 有效义务与删除条件 |
|---|---|
| 旧 canary 与已购证据 | R9a 只读核验；保留原字节、回答与 ledger。R9c 新根独立运行，仅同身份且证据可核验的回答采用；协议／实际输入变化即拒用，不隐式迁移 |
| 可信 owner 修订 | 本根耐久证据＋基线／当前产物摘要闭合后才级联；最近确定性复验成功才 resolved；无记录改动仍人工处理，不新增 book／chapters／names 的 operator_redo |
| 章集合恢复 | 从有效 committed set 与当前章包身份只补缺章；输入未变购买继续采用，扫描全书不等于付费重跑全书；换 NameMap 只影响交付，Hook 窄改不使无关正文失效 |
| Stage1 hook_copy | e00 章前证据仍分流至 Stage2 book 与第 1 章观察，与 main_text 构成组合源身份；只改引子拒复用旧购买，不恢复废弃中间产物 |
| 篇幅与预算 | 正式 MainText 派生总预算、源章数固定目标章数；篇幅带／Hook 目标有正式配置来源；R9a 统一计数，配置变化进入实际消费身份 |
| 存量解码面 | **已兑现（2026-10-03 关闭批）**：RETIRED_NODES／RETIRED_APPROVAL_BINDINGS 名册、reviewer legacy 五模块、workflow_transition 及 jobs/queue 历史投影删除；CLI/队列/工作单元拒识改「非现役节点」通则，产物层 `RETIRED_ARTIFACT_PATHS` 与盘面探测保持 legacy_incompatible 闭合；有效独有反例已迁（test_cli／test_work_units／test_agent_run_range 等），「不得回来」守卫迁 `tests/test_retired_surface_scan.py` |
| Agent 终验过渡桩 | **已兑现（2026-10-03 关闭批）**：novel/orchestrator/delivery.py::assess 改接候选根 delivery manifest（定位＋manifest＋成书 sha256 三重核对，通过仅 ready_with_disclosures）；旧坐标过渡桩 read_deliverable_book 删除；独有反例迁 tests/test_agent.py（无候选根/无 manifest/篡改/缺 SHA 四类 unknown＋通过例） |
| 退役台账 | **已兑现（2026-10-03 关闭批）**：retirement.py 台账与两个对账测试删除；表内遗留未执行的死文件（schema 层 stage6 两文件、novel/quality）同批删除，三个 stage 文档已由新链内容接管故保留；「旧面不得回来」单向守卫由 tests/test_retired_surface_scan.py 承接 |

历史响应先耐久再校验；pending／unknown 不自动重购。旧根的原始回答、checkpoint、
接受正文与 ledger 不因 derived 重算而删。修订基线与稳定 ID、无关 canonical 内容
必须可核验；派生物只重算受影响范围，不以保护已购结果为由忽略真源写入保护。
`--check-only` 表示零新购复验，可执行恢复写入，不称纯只读。

### 候选根运行记录映射与删除条件（B0 登记，B1 执行）

本表记录 B1 已完成的迁移：候选根 request、admission、validation、adopt 逐项映射到
SemanticRequest／ContentRevision／ProviderAttempt／AcceptanceRecord／CommitBundle，
契约锚点见 str06.md。该次切换改变了旧请求身份；R9 的历史采用仍按实际请求身份核验，
不据此推断所有缓存均可用或均失效，也不迁移旧根字节。

| 候选根记录（迁移前） | 内容 | 映射目标 | 处置与删除条件（B1） |
|---|---|---|---|
| `runtime/request_<身份>.json` | 冻结请求＋原响应＋usage，按请求身份寻址（已购恢复证据） | SemanticRequest（业务任务、输入投影与语义协议）＋ProviderAttempt（冻结配置、准确请求、原响应、成本；**原响应按 attempt 存储并被 attempt 引用，空回答不再占据请求身份**） | 新链改由 attempt 引用的耐久响应存储后停止写旧形状；「请求与响应一体、按请求身份整存整取」的旧布局退役；历史文件继续作为 F7a 历史采用的只读证据源，不删除 |
| `runtime/validation_<身份>.json` | 单请求的解析/编译/校验结果 | AcceptanceRecord（解析、救回、引用解析、编译、机械验证分项结果；语义恒 `unver`） | AcceptanceRecord 落地后停止逐请求写 validation 文件；旧文件随旧根只读保留 |
| `runtime/admission.json`（含 `commander_admissions.jsonl` 命名的历史账） | 整书调用准入平行计数（只计成功应答、不计 unknown） | 删除，不映射——与「unknown 不释放额度」不符；额度唯一由工作单元内容轮＋整书总闸（外发前按 attempt 计数、unknown 也计入）＋ledger 承担 | B1 额度账切换并验证「重启不重置、unknown 占额」后删除写路与读取；文件随旧根只读保留 |
| `runtime/adopt_<owner>.json` | 历史采用与 owner patch 记录（cost_link 三档、patch note 基线身份） | ContentRevision（首次生成或修订；owner patch 是无外发的修订，绑定基线请求身份与修订说明）＋CommitBundle 的采用证据（采用状态从 seal 派生） | CommitBundle 落地后停止写独立 adopt 文件；F-4 的 allow_patch 建档语义迁入修订记录；旧文件随旧根只读保留 |
| `runtime/body_attempts_chNN.json`、`runtime/hook_attempts.json` | 同输入有限重写的候选尝试与选择 | ContentRevision（内容轮额度沿用）＋ProviderAttempt（每轮至多 `contracts/invariants.py::WORK_UNIT_MAX_DELIVERY_ATTEMPTS`＝4 次） | B4 回执改绑时随章回执迁移到新记录；旧文件随旧根只读保留 |
| ——（无旧对应物） | —— | CommitBundle：输入清单、采用证据、输出摘要与 seal | B1 新增；正式提交记录，无删除条件 |

本表保留 B1 迁移语义供现役文档引用，不重新启用旧布局或双写。已清理的历史根不可恢复；
仍存在的文件保持只读。R8 的 work-unit 耐久证据绑定不补造 runtime/request_*，
R9 修订／采样记录沿正式证据链版本化，不成为新的业务真源。

## 成本与授权

R9a／R9b／B6a 开发与回归全部 fake transport、零真实模型调用；
R9c／B6-B 开跑前按实际章数、当前配置、可采用回答、重试规则与剩余额度会话报价，
付费与正文外发分别授权。重置文档、登记“已裁决”与免费回放不扩张任何付费授权。

完整无修订新链基础调用为 2N＋9（11 章约 31 次），只是工作量参照，不是费用或新购
上限；内容修订、provider attempt、网络失败、unknown 与历史采用分别记账。
同根重启／continuation 不重置额度，付费显式 `--yes`，增加额度走原正式入口，
截断 `finish_reason=length` 仍停在 item，不自动重试或降 effort 掩盖设计问题（D6）。

成本唯一权威为 <base>/_service/ledger.jsonl；费用未知保持未知，reasoning 不重复计入
completion。R9b 改变语义层与消费输入，Stage2/4 旧版产物不得整套采用；即使
chapters／names 的 wire 字面量未改，也必须检查其实际输入身份，不能承诺缓存命中。
身份未变、来源完整的请求经 --adopt-root 复用，不复制缓存。

### 各付费节点 token 预期量级（B0 登记；D6）

以下为迁移期历史量级，不是 R9 报价。原始历史根已清理，按历史登记读数；DeepSeek 未提供
独立 reasoning 时保持 null。R9c 回填各 owner 首次成功率、真实 token、失败／重试及成本，
尤其补 associate 的首次实测和新表达／章包输入规模；显著超量先回查协议／材料。

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

## 本轮之后

R7 是 B6 文学问题驱动的一轮新契约／流水线优化，尚未完成；持续登记漏保／过保、
under-spec、状态过建模、checklist prose、导演 brief 夹带未登记事实或通用形容词、
章内揭示与逐场兑现的机械不可证边界、过度失效、下游语义材料不足及 Golden 成本。
转场重开、跨章重复、无名细节冲突、声线漂移依 B6 阅读证据推进，不无限扩张免费重构。

B6c／Golden（D10）：固定 Workbench 的《夫君的报恩》《三人成行》；
前者为开发用书，独立性有限须披露。先更新 `tests/golden.py::CRITERIA` 的退役 R-9 引用，
冻结六维 Core Preservation、Structural Originality、Causal Continuity、
Character Continuity、Natural Progression、Local Prose Coherence，人工阅读为主，
模型辅助另授权。源书在显式 NOVEL_DATA／NOVEL_CORPUS 下核实后冻结；
输出由 NOVEL_OUTPUT 指向独立根，不覆盖语料。

冻结源版本、输入 manifest、baseline 成书、rubric/version、评价方式、辅助评审
prompt／provider／model／参数、输出根与成本；不缩减冻结分母。只变评价时重评双方现有
成书，不自动重生成。《夫君的报恩》F5e 对照仅使用历史读数（原始根已清理）；
《三人成行》对照 Workbench Golden Set oracle（现役 v3.0，2026-10-01 用户提供
并冻结；旧 v2.1 只读留证），具体可用证据开工核验。
源／新书／baseline 证据位置分别记；Structural Originality 按书／场景与语义承载比较，
不要求同号章对应。阅读时序判据随目标语义版本化，保留事实、因果、披露与改名覆盖。

NameMap v5 跨书赋值、第二本表达力及第三人称串人称问题须有对应证据；
本书第一人称不能代签。未评或证据不足保持未验证。Golden 不代替独立
current-schema corpus 验收，也不作为 STR-06 迁移关闭前的重复生成义务。

## 质量重设计批次

已完成交付仅作恢复索引，完整记录按本文件 Git 历史及
[DECISIONS.md](DECISIONS.md) 查询；R9c 已停止（2026-09-30 裁决），语义核读由 R10c 承接完成；R10 批次见下方索引。

| 批次 | 状态与已交付职责 |
|---|---|
| R0 | [x] 字段送达登记、变异守卫与 fake 故障注入底座 |
| R1 | [x] associate-v2、typed 关联与唯一句柄登记表；AUD-01／02 转绿 |
| R2 | [x] Design-v2、两层 IR、线程／承接／背景送达、renderer-v5；AUD-03／04 转绿 |
| R3／R4 | [x] 有界 owner 修订与回派、逐章集合恢复；九维人工阅读协议唯一登记 |
| R6 | [x] 输出组 publish／seal、验过才发布、未封存拒正式消费；AUD-05 转绿；总槽初始化竞态修复 |
| R5／R5b | [x] fake 真实产品链回归、研发选章／暂停／续跑与留证；免费工程基线不代替 B6 验收 |
| 语义冻结闸 | [x] D8 初始冻结及独立协议钉；R8b 按 D8 修订仅改 design-v3 |
| R8 交付记录 | [x] 回派身份与级联、Design-v3 typed 一次校验、人物首现闸；3423 项终轮全绿。随后分支同步 3409 项含一项 appendlog 间歇红，分别登记，不互相覆盖 |
| R8b 留待 | [x] 已随 R9b 完成 Design prompt 的模型 JSON Schema 生成与动态闭集，不再单开收尾批 |
| R9a／R9b | [x] 确定性止损、耐久修订与集中升版已实现；工程证据按 B6a 登记，语义核读随 R10c 完成 |
| R7 | [ ] 未实施；等待 B6 阅读输入，不属于已完成 R0–R6 |

AUD-01～05 的现役反例继续保留；全部转绿不证明 R9 新发现的材料泄漏或成书语义已通过。
## 职责解耦批次（2026-09-27 起；B0–B6）

| 批次 | 状态与恢复索引 |
|---|---|
| B0／B1 | [x] 契约计划与五层运行记录、响应耐久、额度／重试／冻结配置；token 量级与旧记录映射见上文 |
| B2 | [x] Stage2 book／chapters／names／associate／compile 五 owner、双真源唯一发布 |
| B3／B4 | [x] Stage4 章闭包与 Stage5/7 章包、并发、章回执／集合恢复、确定性交付 |
| B5 | [x] HTTP→CLI→交付→重启 fake 全链；`novel/stages/stage2/source.py` 整包删除，独有反例迁五 owner，不复活旧联合抽取 |
| 收尾批 | [x] 失败形状与逐章留证的 API／前端消费收口，已按 D11 提前合入 main |
| B6 | [x] B6-B＝R10c 已交付并通过阅读验收（2026-10-03 关闭）；B6c＝R10d 独立质量轮另行授权 |
## 必要证据与标定知识

- 现役测试入口：`tests/test_str06_contracts.py`、`tests/test_str06_invariants.py`、
  `tests/test_str06_fold_guard.py`、`tests/test_str06_source.py`、
  `tests/test_str06_target.py`、`tests/test_str06_execution_ir.py`、
  `tests/test_str06_repairs.py`、`tests/test_str06_workflow.py`、
  `tests/test_str06_body.py`、`tests/test_str06_hook.py`、
  `tests/test_str06_views.py`、`tests/test_str06_product_entry.py`。
  范围由实际消费者与 `tests/suite_catalog.json` 决定，不凭文件名宣称全链通过。
- 冻结面由 `tests/test_str06_f7e_freeze.py` 保护；R9b 按裁决同批重钉，
  不为通过旧测试恢复退役架构。
- 旧迁移／标定／F5e／F9／v13 根已清理，正文与原始输出不可恢复；结论按历史登记与
  `docs/DECISIONS.md` 读取。R9c／r10 系运行根（含关闭交付根 r10b）为现役证据面，
  与这些已清理历史根分开登记。
  证据不存在或身份不符报告未查／不可用，不补猜或重购来掩盖。
- 默认并发 4 在新协议下的表现与 429／重试由 R9c 报告；报告恒等基线
  IDENTITY_UPSTREAM 的书级／逐章口径差异仍属 R7 登记，不在 R9 偷改评价语义。
## 历史批次索引

- F4R：既有红账与 fixture／导入登记清偿；Stage0 v3 回归、Stage1 输入联调已完成。
- F5e：历史成功新购 23 次，成书 11461 字，总偏差 +1.63%、逐章带内、改名零残留、
  同根重放零新购；约 111 万 prompt＋23 万 completion token，仅作历史读数，
  开发性阅读不代替用户验收。
- F7／F8：正式接线、旧链退役；F8c 解散 `contracts/schema/str06/`、
  `novel/stages/str06/` 与 `tools/str06_workflow.py`，正式实现分流至现役包；
  F8d 默认非 corpus 回归 3034 项零失败。旧书只读拒识为 legacy_incompatible。
- F9 未通过，D1 以职责解耦批取代；B6 沿用真实产品入口、精确失败归属与工程／用户验收
  分开记录。历史预登记的保真、因果、引子分流、披露／改名反例不撤销，
  纳入 R9c 对应问题及 B6 全书阅读。
- A0–A6 的工程整改已吸收为 R0–R6；AUD-01～05 已转绿，R7 未完成。
  旧 PLAN.md（质量重设计轮）已删除；现行 PLAN.md 是 R10 静态指南，R10-0～R10c
  与关闭批已完成，余 R10d 段有效。
## 批次推进记录规则

开工核对代码状态、正式输入、请求身份、许可、剩余额度及消费者；交付记录包含精确
node／item、artifact／版本、最终盘面、恢复入口及测试层级／分母。免费与真实付费验证
分列；测试通过、函数存在或历史语义结论不直接勾选质量验收。

每批完成后只更新本文件的当前状态、该批交付证据与下一步；长历史进 Git，
复用教训进 LESSONS，裁决缘由进 DECISIONS。遇到新产品语义、公开契约或架构取舍，
先核实既有裁决再提交用户决定；文档重置本身不实施 R9、不开始付费运行。
