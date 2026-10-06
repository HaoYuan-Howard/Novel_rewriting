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
| CS-F4 本书正式恢复 | [x] | 已完成（2026-10-05，路线 A＋全链授权）：正式根 `outputs/csf4_restore_20261005` 交付 `stage7/final/夫君的报恩_rewriting.txt`（11/11 章接受、delivery_identity `01595264…`）；免费段（stage0/1 重放＋book/8章/names/ch04·06·08 采用）零 token；实付 28 行＝输入 434,004／输出 501,336（reasoning 415,287 不重复计）。补丁与证据：`outputs/csf4_fuhun_restore_20261005/`（义务审计、wire-dir、patch note 归档）、scratch 验证根 `outputs/csf4_patch_probe_20261005`。人工恢复如实登记：ch03 补丁 wire 与 Design 已购 wire（额度耗尽后零成本采用＋know 拍位补丁）为 unverified 类采用，preserve 裸姓 16 处走本根正式补丁链（active/preserve.json）；preserve 两次名面回派由模型修订轮闭合。**不计为全自动成功**；成书对比评估归 CS-F5 |
| CS-F5 成书与成本对照 | [x] | 已完成（2026-10-05，免费工程阅读零付费）：对 `csf4_restore_20261005` 成书（`16d26c4b…`）按金标逐锚点核验——CS-F 四修复面全部落地（ch03 主体、ob44 当场识破、裸姓清零、Hook 投影留痕）；六维逐维证据报告（不设分数）与自动程度（无人改稿但有人辅助完成）分别交付；成本同口径（ledger）：核验新增＝机械闸重跑 3 行 13,126／17,228＋语义核验 0 行（闸未启用），返修节省＝旧协议本书 12 被拒轮 80,162／360,953 纯损失消除＋新协议 3 章首答通过＋本根 4 行 0 token 缓存复用，Design 段无净节省如实登记（rc15 三轮与本根四轮均未获接受 wire，人工路线 A 闭合），不宣称整书降费。**核验发现 1 项核心缺陷**：GS-01b 负控命中（童年坠河与芸娘救命融合为同一事件，连带丢失财貌救人动机与婚前互生情愫）＋6 项轻量缺陷（藏身计数×2、衣带/裤绳指称、雨夜站位、「这才确准」矛盾、受孕时点自认），均静默进入下游（机械闸无判据、语义闸未启用），已登记 R7，是否对本书走 owner patch 修订待用户裁决。报告与账目：`outputs/csf5_review_20261005/` |

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

**CS-F4 本书正式恢复**（《夫君的报恩》，11 章）：已完成（2026-10-05，用户裁决路线 A＋全链授权到交付），
明细见 CS-F 表行与提交说明；一项机制债转入「工程余项」（换算链 wire 的正式 owner patch 绑定档缺口）。

### CS-H 接受版本只发一次、首答通过、窄返修（RC-50～65）

规格见 [repair_convergence.md](schema/repair_convergence.md)“CS-H”一节与[返修场景](specs/repair_convergence.feature) RC-50 起
（2026-10-05 用户批准的执行计划；四项裁决与二轮审阅的处理见 DECISIONS 同日行）。起因：`csf4_restore_20261005`（rc16）28 次实付里
返修与返工占输出 token 的 52%（259,419／501,336），且仍靠人工收尾。目标：每个节点正式产物的接受版本数为 1、首答通过、返修只发窄输入
（指标 M1～M5 见规格）。约束不变：不增加节点、不提高任何重试或额度上限、不加 LLM 审阅或判官、不做付费 A/B、不调 effort、不引入任意目标
称谓创作；工程验证全部用 fake transport 与只读重放，零付费。每批独立提交在当前分支，不 push。

| 批 | 状态 | 余项与完成条件 |
|---|---|---|
| H0 登记与只读装置 | [x] | 已完成（2026-10-05，无行为变化）：本表、规格 CS-H 一节、RC-50～65 场景、DECISIONS 同日行（含对 CS-F5「Preserve 条目返修已落地」的更正）；`tools/repair_rounds.py` 分母改为 `work_units.json` 登记的单元（csf4 根上因只有锁文件的空壳目录崩溃，已修）；只读重放 `tools/replay_candidates.py`（design 已接入，Preserve 随 H3、associate 随 H4 接入）。重放在 csf4 根上复现：Design 第 2 轮对最终 Brief 完整预检全绿，第 1／3 轮称谓错误 11／320 条，第 4 轮 2 条写手材料泄漏归 Design |
| H1 候选集合、免费沿用与真实记录 | [x] | 已完成（2026-10-06，fake transport 全绿、零付费）：**H1a**（2026-10-06，记录真实性：判据④⑤）上游回派以类型化信号 `UpstreamOwned` 取代空错误列表、被回派／被机械闸拒的候选与文本首稿记 `accepted=false`、单节点路径交付后状态与整链同源（`delivered`／`accepted_body=true`）、回派问题按材料身份去重。**H1b**（本批）：`novel/stages/candidates.py` 从工作单元账与响应 blob 重建全部已购候选（含补丁链合并）并写登记 `runtime/candidates/<owner>.json`（可重建索引，非第二本账）；上游差异（复用 `wire_diff`，条目按（kind, anchors）对齐分未变/变化/新增/删除）与称谓规范形（复用 `claim_spans`）为纯函数；`run_body_design` 在 `ctx.ask` 之前做免费判定（兼容→差异→完整预检→择优：全绿优先、根因最少、同分取最近）并落地四档动作——无差异或变化条目规范形相等且预检全绿→**零调用采用**（成本链 ledger-row 指向原 attempt，本根只记一行零 token cached 行），变化不能证明等价→一轮条目返修其承载拍，删除条目→承载拍进复核目标，新增条目→可选目标闭集（`PatchTarget.optional`）；整份新购只限闭集五因并留痕；`OwnerRevisionRecord` 升 V2（新增必填 `applies_to_request_sha256` 与 kind `carried_forward`，V1 读兼容登记于下方兼容层表）；三个中断恢复窗口（回答已存证而登记未更新／补丁已验证而未发布／上游已修订而下游未复验）用现有证据继续，无重复购买、无重复发布、不沿用失效接受记录；只读复检不走沿用凭证。完成判据（fake transport stage 测试，走真实入口）：①请求身份变而条目未变→零调用采用旧候选、成本链指向原 attempt；②变化条目称谓规范形相等→同样零调用；③较早的合格候选不被较晚的失败候选挡住；④被拒或被回派的候选 `accepted=false` 且带错误，正文首轮被机械闸拒同样如此；⑤单节点路径交付后 `accepted_body=true`；⑥三个中断恢复无重复购买与发布 |
| H2 Design／HookDesign 完整窄修通道 | [x] | 已完成（2026-10-06，fake transport 全绿＋csf4 根免费重放，零付费）：**③**规则码→修复路由表 `contracts/judges/str06.py::PATCH_ROUTES`（76 码，四态 locate／optional／whole_answer／stop；Guard M1–M8 与 compile 依赖码无 Design 编辑位置登记 stop，裁定见 DECISIONS）＋`tests/test_str06_patch_routes.py` 双向核对；判据全部铸规则码＋稳定位置（guard／compile／grounding／hook／preflight）。**①**带基底起步（`PatchBaseline`：窄复核从已购候选整份 wire 起步，attempt 引用经 `baseline_ref` 展开进 provenance）与可选目标（`PATCH_ROUTES` optional 码——必保义务漏标 `design_carrier_missing`、series 互斥 `series_collapsed`——由 `locate_design_patches` 给全部拍闭集，回答只覆盖选中子集、至少一个、闭集外拒绝；compile 面 `compile_must_unbound` 回声与覆盖闸同根因，按义务重叠在 inspection 去重）；**②**窄输入（`NarrowPatchRequest`：协议规则＋失败条目＋依赖切片＋合法取值，不带整份原请求与前序材料；Design 切片 `novel/stages/stage4/slim.py::design_patch_slice`、Hook 切片 `novel/stages/stage4/hook.py::hook_patch_slice`；可选行不内联当前值——切片已携带，避免整份 wire 双写）；**④**反馈按根因聚合（`novel/stages/stage4/preflight.py::DesignInspection.feedback`：同码多处＝一个任务＋受影响位置；任务身份＝无进展判定尺，候选字节变化不算进展）；**⑥**沿用判定后半（`narrow_review` 动作从已购候选 wire 带基底起步，一轮取代整份重生）；**⑦**HookDesign 同法接入（窄上下文＋与只读预览同一把尺的组合检查 `hook_combination_errors`）。完成判据验证：①上游改义务语义→一轮条目返修且无关拍字节不变（H1b 用例续跑＋子集采纳用例）；②新增义务→闭集选承载位（H1b 用例）＋可选子集采纳、未选拍字节不变（新用例）；③必保义务漏标→一轮可选目标返修（stage＋产品入口双用例，`obligation_unbound` 不再整份重写）；④同一根因两轮仍在即停、字节变化不算进展（新用例）；⑤重放实测——csf4 根单拍修复（事实锁错拍，3 拍）窄请求 28,910 字符（协议规则 20,151＋切片 4,578＋合法值 1,781），输出只回选中拍替换，对照本根整份修订轮输入 35,434／46,624、输出 29,102／66,403 tokens；⑥重放实测——Design 第 2 轮对最终 Brief 完整预检全绿（`reuse=green`，输入身份 match）⇒ 零调用采用即取代当时两轮整份重生（R3＋R4 计 70,087 输入／130,741 输出 tokens）加一轮 Preserve；其生成时上游（preserve R1）对最终 wire 差异 14 条（6 条称谓规范形相等／8 条模型改写→窄复核），R3／R4 的上游（preserve R2）差异 1 条且等价——计划文案「7 条变化、3 等价、4 改写」为起草时估计，以盘面实测为准（更正记 DECISIONS） |
| H3 称谓、必保揭名、锁与写手材料 | [ ] | 显示面解析函数、释放前缀保序且覆盖全体人物、投影的同位语折叠与无损性守卫；Design 原文闸先投影再查与归因表；必保揭名（派生 `requires_released`、Design 闸、章包与正文检查）；正文段落级修句与写手规则；Preserve 自闸与条目返修；确定性转换；表达不点名的源头规则；共享锁派生与主体包含；Design／Hook 提示词规则；写手包清理；契约与版本随形状定稿统一分配。每个新判据与该 owner 的窄修同批。完成判据：①新反例与绿对照（被提及未出场人物投影、同位语折叠、无损性守卫、concealed 不回落、裸姓不投影、无显示面归 Design、第一人称角色词）；②必保揭名四反例（know 早于释放被拒并可窄修／同拍释放通过／全书未释放被拒／承载章正文缺该字面被拒并走段落修句）；③共享锁四类反例＋共享引用自动派生绿例；④Preserve 修一条义务时请求输入输出只含该条及其依赖（对照本根 54,390／19,428），无关义务字节不变；⑤重放 csf4 根：Design 第 1／3 轮首答称谓错误（11／320 条）只剩不可投影残余且全部带路径、共享锁错误为 0、Preserve 首答 16 处裸姓在自闸被逐条定位；⑥最终请求文本断言：写手与楔子请求不含来源引文、内部句柄、协议枚举词与未释放姓名，场的 `avoid` 到达正文请求、材料所引场的 `avoid` 到达楔子请求 |
| H4 其余节点的机械化与定位器 | [ ] | associate v3（维度句柄、提示词、可配对 close 闭集、定位器；编译器按地址传播规范键；R-2.12 改写）；book、names、naming 的条目定位器（不新增判据）；`tools/repair_rounds.py` 三类 token 分列。完成判据：associate 在同键状态很多的样例上输出不随句柄数增长；章内已闭合的 close 不可被配对；重放显示 csf4 根 89 对同键配对不再需要 |
| H5 验证与收口 | [ ] | 每批：先复现目标反例，再跑受影响的定向测试，契约形状稳定后跑 BACKEND_CONTRACT §9.5 五文件，视图或 API 形状有变补前端 test 与 typecheck；四批合并后按最终改动集合跑一次默认非 corpus 回归（本机 `-n 4` 会被系统因内存终止，改串行分批并留 JUnit）；免费重放 `csf4_restore_20261005` 与 `cs_golden_20261004_rc15` 出前后对照表；真实运行**须另行授权**（付费与正文外发分别授权、独立新输出根）：《夫君的报恩》与《三人成行》各一本，可并入 CS-G 的 R10d 重复轮，记录完整 M1～M5（人工干预与失败轮全部计入）并带三项质量回归：姓名披露、事实状态（计划与实际）、D1 |

契约候选身份：H3、H4 含形状与协议变化（Brief、IR、Binding、Hook 上下文、正文 validator、`stage2-associate-v3`），默认首次真实运行前集中升一次 rc17
（若分两次合入则各自升版并重钉 `tests/test_str06_f7e_freeze.py` 冻结钉）；旧根含 `csf4_restore_20261005` 按契约表漂移只读。

**待用户裁决**（均不阻塞 H0～H4 的免费工程；第 1、2 项涉及付费与正文外发，未获授权不发起任何付费调用）：

1. 真实运行的授权范围（两本书、输出根、额度），以及是否并入 CS-G 的 R10d 重复轮。未授权前不发起任何付费调用。
2. 《夫君的报恩》成书里本计划不处理的两项：sc11 计划与实际不一致（事件写“直到沈知言搬出陆家”而状态仍是 planned，可用现有
   Design owner patch 补一条 actual 状态）与 D1 救命／坠河融合（可用现有 `avoid` 写一句“救命经过未交代，不得认定发生在幼年坠河时”）。
   两项都需重购受影响的章与楔子，属付费动作。
3. rc17 集中升一次（默认，建议）还是随 H3、H4 分两次升；H3／H4 形状定稿时再请裁决。
4. 两处超出审阅意见的补强——承载章正文必须出现必保姓名的检查、事实锁的主体包含检查——按计划保留；不保留则这两处退为读数。

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

## 待删除的兼容层与删除条件

过渡例外必须在此登记删除条件；新增兼容层先取得用户裁决。

| 对象 | 有效义务与删除条件 |
|---|---|
| rc12／rc13 五类产物 adapter | `novel/store.py::_upgrade_rc12_payload` 只迁移白名单无损信息；旧 Brief 缺认知模式免费重放 Preserve，旧 Design 缺 confirms 走显式 owner patch。存量全部重建或退役后删除此 adapter 及 Str06Repository.read_graph_hook 的旧组件迁移，禁止无限新增版本 |
| OwnerIssue v1／v2 读兼容 | v3 显式 dispatch_pending、指纹历史与更正记录；只经 `contracts/schema/workflow.py::owner_issue_record_of_payload` 集中解码；所有存量 v1／v2 根只读归档／不再续跑后删除。旧错误路由账的更正按 RC-15 显式恢复，不重置轮数 |
| OwnerRevisionRecord V1 读兼容 | V2 增必填 `applies_to_request_sha256` 与 kind `carried_forward`（CS-H H1b，RC-54/56：免费沿用的采用凭证）；只经 `contracts/schema/workflow.py::owner_revision_record_of_payload` 集中解码（v1 的应用身份即其 `baseline_request_sha256`——V1 只可能是 owner_patch／resample）。现存根盘上不再存在 `schema_version=1` 的修订记录（只读归档／不再续跑）后删除 V1 读兼容；未知版本显式拒绝，不向领域 schema 塞默认值 |
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
