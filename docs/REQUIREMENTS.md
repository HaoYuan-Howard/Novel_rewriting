# STR-06 · Stage2–Stage7 终态架构迁移（Stage6 退役）

本文件是唯一活动计划，负责当前进度、下一批任务、验收、恢复与删除条件。
目标职责、真源和产物接口以 [str06.md](schema/str06.md) 为准；现役行为、根不变量与
局部指南仍按 [AGENTS.md](../AGENTS.md) 读取，历史理由见 [DECISIONS.md](DECISIONS.md)。
开工先核对工作区、实际代码和已有证据，按下列顺序推进下一未完成批次，不另造活动计划。

**2026-09-24 重置**：STR-01～STR-05 退出活动面；仍有效的旧义务已合入本计划。
完整原字节保存在 [重置前快照](archive/REQUIREMENTS_20260923_before_str06_reset.md.snapshot)，
SHA-256：`58132800359d5ef6b98d39aad57c98657253cd37373b902400d7d587903a1bdc`
（2026-09-26 复核值：对 Git 提交 blob 的原字节计算。此前登记的 `c7c7c9b2…` 与 blob
字节（LF 行尾，git 规范化后）和工作树字节（CRLF 行尾，autocrlf 检出后）都不相等，
从未核验通过，按“身份不符就如实更正”处置；内容完整性以单提交单 blob 为准）。
快照只供历史检索，不作为另一份活动计划；旧编号只作来源索引，不能据归档宣称任务完成。

## 目标与范围

Stage2 负责源语义；Stage4 负责目标故事、Body/Hook 呈现设计与确定性 WriterIR；Stage5
负责正文逐章独立并发实现和单篇 Hook；Stage7 负责 Naming、机械 ApplyNames 与 Assembly。
四类故事语义真源保持 Characters／Blueprint／TargetDesign／SemanticBinding；NameMap
是交付变换契约，接受正文、回执与已购证据独立保护。Stage6 整层退役，不重排阶段号。

职责、权威源、单向依赖、窄修复和失败归属已冻结；字段级 schema 保持 RC。Hook 的必要
选择必须进入正式 Design 并可重建 Context；源名称结构归 Characters，目标名字归
NameMap。完整边界及目标产物布局只在 str06.md 维护，本文件不复制第二份字段契约。

**本轮终点（2026-09-27 再调整）**：2026-09-27 用户裁决「流水线职责解耦计划」（D1–D10，
见 [DECISIONS.md](DECISIONS.md)）取代 F9 成为收尾路径：新链产出《夫君的报恩》由用户
通读，工程验收与用户验收同时有效后一次合入 main、关闭 STR-06（B6b）；F5e 成书
（盘上只读、零成本）作对照基线。产品范围固定为短篇小说（中文约 2 万字），永不做长篇；
不新增书长预检，沿用现有逐请求容量闸（D7）。验收采用迁移口径：错章、漏章、改名残留、
交付不一致、恢复失败等工程缺陷必须修到通过；文学质量意见逐条登记为下一轮迭代优化的
输入，不阻塞本轮关闭。F5e 的 agent 开发性阅读不代替用户验收，机械全绿也不改写语义
未评状态。

B0–B5 全部 fake transport、零模型调用；付费与正文外发只在 B6 按实际报价分别授权。
目标架构（Stage2 五节点、章包三件套、执行设施、提交顺序、精确失效）唯一登记在
[str06.md](schema/str06.md)；本文件维护批次进度、验收与删除条件，批次表见下方
「职责解耦批次（B0–B6）」。F7/F8 的隔离 worktree、候选接人与分支切换均已按原口径
完成（见批次勾选）；F8a 起现役注册面即新链 11 节点，**B2 前半（2026-09-27）已扩为
14 个**（Stage2 五节点，`str06.stage2.source` 退役；stage0/1 另计）。

旧书保持 `legacy_incompatible`，独立根重建，不手补旧产物、不继承旧批准、不清 ledger。
新协议改变全部请求身份，历史采用（F7a 通路）在新链上不会命中；旧输出根与已购证据
只读保留。

定向证据闭合即可交付子批次；不因提交、改计划或登记新测试重复全仓。默认非 corpus 回归
归 B5 末按最终改动集合执行；中途仅在命中根指南的完整回归门槛时提前执行，并记录具体原因。
B6 若再改生产代码、契约或测试基础设施，须按最终提交重新完成工程回归。
`tests/suite_catalog.json` 的 `change_scopes.str06` 登记选测范围，与实际导入消费者取并集；
B5 删除旧路径（S-05）时随删除条件一并清理此登记。

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
F8 的免费反例仍须验证合法跨章布局可以编译、接受和交付。

## 当前状态与下一步

**当前状态（2026-09-28）：B1–B5 主干与收尾已实现，D11 已提前合入 main；B6 尚未完成。**
同日按理想设计审计确认：Stage2 关联投影丢失对象与句柄碰撞、Stage4 拍级读取漏入章包、
四类跨章字段缺消费者。既有批次的测试成绩保留其原范围，不再据此宣称依赖闭包已完整兑现。
用户本次要求提供具体执行文档；下方「目标导向整改与优化执行方案」是本文件内的后续提案，
不是第二份活动计划，也不代表已经实施、采纳新的架构裁决或取得付费／正文外发授权。
2026-09-28 该提案已吸收改写为 `PLAN.md` 的批次登记（唯一活动计划）：执行顺序
R0→R1→R2→R3→R4→R6→R5→B6-A→B6-B→R7，B6 拆两步、R6 前置于 R5，14 节点为默认
执行拓扑而非不变量（对应 `PLAN.md` 已裁决第 4 条）。本节保留为细则素材与验收口径，
不再作为并行提案维护；冲突处以 `PLAN.md`「已裁决」为准，裁决由 R4 批写入
`docs/DECISIONS.md`，各批交付记录按「批次交付与验收」纪律登记进本文件。

**此前 B0–B2 实施记录：**
2026-09-27 用户裁决职责解耦计划（D1–D10）取代 F9 与质量改进批次二的剩余项，成为唯一
活动计划：批次表 B0–B6 见「职责解耦批次」；B0 登记与 B1 执行设施（前半 2026-09-27
提交 5f74bd78、后半同日完成：截断即停 D6、`max_tokens` 131072、ExecutionProfile run
启动冻结与 effort 面 low/high/max、并发登记改 `str06.stage5.body`）均已闭合，故障注入
全绿。**B2 前半（2026-09-27）已落地**：五节点注册面一次性改齐（`NODES` 14 节点、
`LLM_EXECUTOR_NODES`、`PIPELINE_PARALLEL_NODES` 增 `str06.stage2.chapters`、观察产物
四件与 RC6、候选契约身份重钉 `a0f5fb5e…`）、观察与关联协议
（`contracts/schema/observation.py`，`stage2-observation-v1`，I-13 显式版本化、
模型输出面去机械化）、Resolver（`novel/stages/stage2/resolver.py`，句段闭集选择＋
逐字锚唯一定位＋R-2.10 称谓锚点，`compile_book` 委托同一实现）与公开规则改版
（`R-2.3`／`R-2.10`／`R-2.12`／`R-2.17`）。**B2 第二步（2026-09-27）已落地**：
book／chapters 观察 owner 引擎（`novel/stages/stage2/book.py`／`chapters.py`）与
`chain.py` 接线（`run_source` 退役、`run_names` 重挂、`_owner_unit` 改挂新节点、
并发观察腿经真实付费边界验证零重购）。**B2 第三步（2026-09-27）已落地，B2 收口**：
`str06.stage2.associate`（`novel/stages/stage2/associate.py`：句柄投影、只选已给
句柄闭集、五类配对的机械闸）与 `str06.stage2.compile`（`novel/stages/stage2/compile.py`：
解析证据、生成拍号/命题/悬念/场景/初态/约束编号、执行关联（并查集合并同义命题与
同维状态键）、未决项拒绝发布并按 item 报告；源 fold 唯一实现随迁本模块，`R-2.17`
只消费 canonical transitions）两 owner 落地，`run_associate`／`run_compile` 入列；
`stage2/compiled/book_extraction.json` 写路全部退役，Stage4 三腿改读 canonical
（人物 core 由 Characters 投影）；顺带修复两处上游缺陷——chapters 节点逐章产物
不在完成度三账（非模板/`{n}` 轮次/stage5 章账）任何一账 ⇒ Supervisor 对已成功节点
重复派发的死循环（`novel/orchestrator/state.py` 增 `stage2_observations_done` 账与
verdict 分支，F8d 同形缺陷复现），以及监控面逐章扫描写死 `.txt` 看不见 `ch{nn}.json`
（`novel/views/str06.py` 后缀随模板解析）。验收证据见 B2 行。
此前状态：F4R、F6、F5a–F5e、F7a–F7e、F8a–F8d 均已完成——F8c（迁移包分流＋旧链整层
删除）把 `contracts/schema/str06/`、`novel/stages/str06/`、`novel/llm/str06_*`、
`tools/str06_workflow.py` 全部解散分流进正式包；F8a 激活基线契约身份 `04b6fb22…`、
质量改进批次二批 2 后 Design 三产物换 rc5 时为 `7fb5ecfa…`（历史基线，Git 可查；
B2 起由 `tests/test_str06_f7e_freeze.py` 重钉，表内任何变化须先改冻结记录）；F8d 默认非
corpus 回归 3034 项零失败、零错误、零未说明跳过。F9 首轮付费验收未通过，按用户
指示停在 `str06.stage4.body_design` 之前；质量改进批次（免费）与批次二批 0–2 已
完成、批 3 已取消；v13 验证轮实购 book＋ch01–ch06（7 次成功＋1 次 TransportError
unknown），ch01–ch04 的工程验证与离线语义验收已登记。B6b 前不再按旧链补跑。
下表是既有证据摘要，不是本次重新运行或整书质量结论。

| 已有阶段 | 状态与证据边界 |
|---|---|
| F0／F1／F2a | RC 契约、路径／依赖守卫、确定性 Fold／Guard、局部身份与窄修复反例已实现；F0 contract 25 项通过，详细记录见快照 |
| F2b | 《夫君的报恩》Source Semantics 冒烟：16 次付费、58.9k prompt＋164.1k completion；源 v4/v12 重载、引用及 fold 检查；当时质量披露保留在快照 |
| F3 | 单书 Preserve／Design／Bind 冒烟：5 次付费、68.7k＋104.1k tokens；69 义务、28 场、99 拍、11 章；换目录导致额外 2 次购买的教训保留 |
| F4 | 单书 RC1 planned-only 干跑：28 场、零模型调用、计划连续性可重放；不是 accepted body 或阅读验收；~~第二本未验证，现移交后续 Golden~~（旧待验口径已被 2026-09-27 D7／D10 取代：第二本固定为 Workbench 短篇《三人成行》、排在 B6，不再是待验项） |
| RC2 | Source／Brief／Design／Binding／Compile／Context 的真书审阅修复与隔离入口已落；11 章、69 义务、28 场，零新增模型调用；定向 96/96、契约与路径 484/484；仅改 ob55 满足条件时仅 s27 消费身份变化 |
| F4R | 红账三项清偿（2026-09-24，零模型调用）：uploads 死引用删除后 test_store 恢复收集 161 项（HEAD 上是 collection error）；深导入 grandfather 补账 11 条（分母 77→88，stale 核对无消失边）；`_SHAPE_BREAKS` 补三条 v3 账。定向组合 485/485 绿（upload_autostart／layering／artifact_versions／stage0_revisions／stage01／stage2_characters）。test_store 87/161 绿，74 失败是修复收集后显形的既存夹具脱节（非本批引入），见 F4R 条目遗留登记 |
| F6 | Hook 链与 Stage7 终态契约＋引擎＋免费反例（2026-09-24，F6c 另付 4 次）：RC3（Characters 增 name_structure、graph 必填 hook、投影族六族、家族 +stage5/hook、stage7/naming、stage7/final）；Hook 唯一 publisher、确定性 HookContext、hook_writer 机械验＋同 Context 有限重写、NameMap v5／ApplyNames／Assembly、Stage2 names 窄调用与名称投影、迁移包依赖规则 AST 真实执行、F6d 退役登记 106 项双向对账。定向 str06＋layering 171/171、契约 §9.5 五文件 426/426；RC2 wire 在 RC3 代码下重编译：28 场 WriterContext（去版本/身份）与 replay 盘面逐字一致、checkpoint sha 不变。全量非 corpus 回归 4902 通过、420 失败/错误与 HEAD 基线逐项一致（零新增失败，全部为分支既有账，含 test_store 74 与两个既有收集错误文件，已忽略不计入）。F6c 付费冒烟已完成（见批次勾选） |
| F5a–F5d | 按章成文链免费面（2026-09-24，零模型调用）：RC4（Design 增 `realization_brief`/`purpose` 必填、双视图职责重划＋`BeatIR` 逐拍定位、`WriterBookIR`、`ChapterReceipt` 章级回执、家族 +stage4/ir、stage5/body、stage5/acceptance/body，删场景正文旧家族；生产正文 Judge 与场景回执/Fidelity 退役）；章预算 largest-remainder 确定性分配；确定性 renderer＋版本化 Writer 协议（六层阅读面、lineage 不进阅读面、完整 IR＋完整前文进请求、超限由精确 tokenizer 容量闸阻断）；`body_checks` 章级机械验（硬失败不能披露放行，只有篇幅带可披露）；按章 Writer 同输入有限重写＋确定性候选选择（偏差最小、平局取较晚）；工具层缓存先于授权、`--book-call-ceiling` 锁内准入、`--check-only`、`--body-chapter-limit`；Stage7 章回执链核对、TXT 先落盘 manifest 最后提交、全书 ±10% 偏差读数。str06 定向 156/156、契约 §9.5 五文件＋layering＋选测＋版本账全绿。真实成文与开发性阅读已由 F5e 完成，见下行 |
| F5e | 真书重跑与按章成书（2026-09-24 完成，用户授权付费）：候选 run 固定 `outputs/str06_f5e_body_20260924/run`。11 章全部提交（23 场 69 义务 Guard 零错误）、交付就绪：成书 11461 字（全书偏差 +1.63%，逐章 414–1730 字全部带内、零篇幅披露）、改名 11 处零残留、manifest 与文件 hash 一致；同根重放零新购、交付字节一致。成本实付：成功新购 23 次（design×2 含 1 次 high 档截断浪费、bind、hook_design、hook_writer、正文 16 次＝11 基础＋5 次专名释放重写；naming/names/preserve/book/chNN 经核验输入一致免费采用）＋1 次 TransportError 空行；合计约 111 万 prompt＋23 万 completion tokens。工程修正见提交 67077990（`--reasoning-effort`、T₀ 非 set 等价归一、场内自建非 entry、pov/weights/mode 归一、body_chNN owner）。**阅读验收（agent 开发性，非用户验收／非 Golden）**：楔子与 11 章通读——六维无硬伤；无机械复述状态、无逐场重开场、义务实际演出（坦白/下跪/称谓纪律/床底与房梁反转）、导演 brief 指定的展开压缩有效、楔子无提前兑现且结尾「嫂嫂，开门」与楔子悬念呼应；专名释放纪律在正文侧 5 次重写全部闭环。**限制披露**：章级机械检查不证明章内揭示位置与逐场义务兑现；正文 Judge 已取消，语义未评如实保留；单书短篇样本；~~长篇/多线的 IR 容量与前文增长（实测章 prompt 53.9k→60.9k tokens 线性增长）仍未验证，现移交后续 Golden~~——该待验义务已按 2026-09-27 D7 删除（产品范围固定短篇、永不做长篇；53.9k→60.9k 属旧章包形态读数，D2 改章包后随构造消失，新链读数在 B4／B6 重报）；用户验收由 B6b 承接（F9 口径，D1），尚待完成 |

RC2 修复覆盖此前“第二轮再优化”的暂缓安排，不能重新把已修问题记作待办。当前正式
编译输入是 D/G；不从 Preserve checkpoint 裸读补语义。`novel/stages/chain.py` 取代
旧 `_tmp_str06_*` 执行 harness，旧脚本只作参考。RC1 形状不自动迁入 RC2。

仍未验证：B6 工程复验、用户对正式交付的
验收（B6b）。B5 已闭合上传/API 接入面、失败类别与恢复状态展示及旧路径删除
（fake 全链由 `tests/test_str06_product_entry.py` 两条链路驱动：进程内逐节点与
真实 `run_step`/`novel.cli` 子进程；前端接入及收尾见 B5 与收尾批记录）。旧链读数中的「章级 prompt 53.9k→60.9k 线性增长」属旧章包形态（完整
IR＋完整前文）；D2 改章包后此问题随构造消失，新链读数在 B4（renderer 字节，免费）与
B6（真实用量）重新报出。F6c 的 Hook 机械通过不证明不剧透、遵守材料顺序、POV 正确或
吸引人；F5e 成书中的同源 Hook 连读属于开发性阅读，不能当作用户验收或一般化质量结论。
原 Prepare 消费干跑和 Hook／Stage7 付费原型只能证明当时范围；F6c 冒烟的 RC3 design
产物在 RC4 下拒读，不作为 F5e 输入。正文或章级回执不存在时，Assembly 明确未就绪，
不制造成功状态。

## 目标导向整改与优化执行方案（2026-09-28 提案）

### 目的、边界与实施顺序

本轮优先保证：源事实能正确表达、目标设计能完整送达 Writer、已购结果能可靠恢复、
最终成书与当前接受集合一致。架构纯度、prompt 长度和吞吐优化都以这些目标为约束。
保留短篇范围、四类语义真源、14 节点主链、章数与阅读序不变、独立章写手、只读监控页，
不恢复 Stage6、前文正文依赖、生产语义 Judge 或人工逐 Stage 过闸。

推荐路线是：先修已复现的信息损失，再完成跨章语义的显式消费和统一发布证明，最后做
真实产品入口验收。Binding 暂保留现役冻结义务快照；不以删除快照或新增常驻评审调用
换取表面上的职责整齐。与现役契约不同的建议在下表单列，实施前取得用户采纳并写入
`docs/DECISIONS.md` 与 `docs/schema/str06.md`；本次仅交付文档，不提前修改可执行契约。

| 提案取舍 | 推荐方案与原因 | 代价／另一选择 |
|---|---|---|
| 跨章目标、行动、问题、承诺的生命周期 | 由 TargetDesign owner 显式声明建立、消费与终止／仍开放；compiler 只执行声明，沿用现有 typed transition 与地址体系，避免另外造一份 Global | 需要升版 Design 语义协议和 canonical／IR；只把当前四份列表广播给每章，无法正确处理关闭与提前披露，不推荐 |
| 同一地址多次改写后的读者确认 | 推荐确认绑定确认拍当时有效的那次事实建立；若要确认已被覆盖的历史事实，owner 显式引用该版本 | 地址本身不足以区分多个历史值；此细化须与 D4 核实并正式登记，不能由 compiler 按叙述文字猜测 |
| 写作身份与验证身份 | 请求只绑定 Writer 真正看到的材料；回执分别绑定写作材料、验证材料／版本与正文，validator-only 变化先免费重验 | 现役 packet_sha256 仍包含验证材料；细化身份需同批修改回执、采用、恢复和 Stage7 消费，不可只删 hash 字段 |
| 发布权威 | 将现有 CommitBundle 升级为多输出组的统一发布证明，免费与付费产物同规；专门业务检查继续存在 | 影响 Store、恢复、CLI/API/UI，需要故障注入与一次完整回归；保留专门规则可维持现状，但不能再声称 seal 是统一发布权威 |
| 协议升级与已购采用 | 按实际改变的 semantic unit 升版；新增受限的现役 attempt 采用入口，只复用身份与证据完全一致的旧回答 | 现有跨根采用不能直接覆盖所有现役记录；未补入口前不能承诺免费复用。另一选择是新根全部重购，损失可核验的既购资产，不推荐 |
| Binding 是否立即改为纯 edge | 本轮保留“Preserve 唯一语义 owner 的冻结义务快照＋Bind 只读映射边”；核实没有另一条可独立改写义务的正式入口 | 明确不满足理想文的纯 edge 定义。若坚持纯 edge，需将完整义务语义迁入 TargetDesign 的独立保留要求区，并同步 Preserve／Design／Bind／Guard／恢复；不得直接删义务分母。另经架构裁决立项 |

**吸收登记（2026-09-28）**：A0–A6 已按下列对应并入 `PLAN.md` 批次——A0→R0
（AUD-01/02 随 R1、AUD-03/04 随 R2、AUD-05 随 R6 转绿）；A1→R1；A2／A3→R2；
A4 发布与恢复→R6，其中 A4a「现役记录受限采用」不立项（新协议无已购产物，B6-A
实购重建，旧盘面只读保留）；A5→R4＋R5；A6→R5；B6→B6-A／B6-B；「后续优化」
表→R7。未单列残留维持现役：Binding 纯 edge 不做（保留义务快照，见 `PLAN.md`
设计原则「保留不动」）；同址多次改写后的读者确认版本规则不变，R2 落地时若需
owner 显式引用历史版本，先经用户裁决。下表 A0–A6 及各小节保留原文作细则素材，
状态与交付以 `PLAN.md` 批次为跟踪面，A0–A6 勾选框不再作为第二份活动清单维护。

下表 A0–A6 是 B5 后、B6 前的整改提案，不替换历史批次编号；所有状态初始为未实施。
生命周期、确认版本、身份契约、发布权威和新采用协议的实施依赖上表采纳。
确定性缺陷的反例与修复可独立推进，
不必等待 Binding 纯 edge 的讨论。B6 及后续 Golden 的既有授权、样本和关闭口径不因本提案改变。

| 批次 | 交付物 | 依赖与完成门槛 |
|---|---|---|
| [ ] A0 反例与边界基线 | 将审计反例固化为正式测试，登记最终消费与恢复矩阵 | 第一批；旧行为确实失败，绿对照有效；确认上表待裁决项 |
| [ ] A1 Stage2 关联无损 | typed 对象投影、无碰撞句柄、同一 registry 的关联与 canonical 编译 | A0 后可实现；完整勾选另依赖 A4a 的现役记录采用验收，合法关联真实发布并被 Stage4 消费 |
| [ ] A2 逐消费点状态闭包 | 每次显式读取的值进入对应 Writer 输入，身份随真实消费改变 | A0；跨章／本场写后读／知识域／失效边界全部闭合；可与 A1 分文件推进 |
| [ ] A3 跨章语义完整消费 | 持续目标、行动、问题、承诺的生命周期与章包投影 | A2＋生命周期方案采纳；无漏项、无提前作为已发生事实、关闭后不继续当开放项 |
| [ ] A4 发布与恢复统一 | 多输出组封存、统一正式读口、免费补尾及旧记录受限采用 | 发布与采用方案采纳；A4a 可先独立验收，发布集成等待 A1–A3 形状冻结，不要求其先全部勾选 |
| [ ] A5 产品与登记收口 | 编排、CLI、API、下载、前端、生命周期登记消费同一后端证明 | A1–A4；真实子进程 fake 全链、失败与恢复、刷新及重启读回一致 |
| [ ] A6 最终工程基线 | 默认非 corpus 回归、契约五文件、前端四件与恢复证据 | A5；按最终代码统一验收，然后进入 B6a/B6b |
| [ ] B6 真实验收 | 按最终版本和授权生成《夫君的报恩》，工程核验后用户通读 | A6；报价与授权、真实结果与质量结论分别记录，不用旧形态成书替代 |

### A0：把已知缺口变成可执行验收

审计基线为提交 `0e380364`（当时工作分支与本地 main 一致）。当日免费定向 102 项通过，
0 失败、0 错误、0 跳过，包含真实 HTTP → Supervisor → CLI 子进程 → Stage7 → 服务重启
读回；模型全部 fake。另做的内存及隔离盘面反例揭示下列缺口。102 项通过只证明原分母，
不替代新增反例；临时日志不是长期唯一证据，A0 将反例及输入保存在正式测试中。

| 编号 | 必须固化的反例 | 原行为与通过标准 |
|---|---|---|
| AUD-01 | 合法 StateTarget／EffectTarget 的人物、关系、world 输入 | 原投影将 typed 对象变成空 dict，prompt 显示 world；修复后各对象、关系方向与 key/value 原样到达关联输入 |
| AUD-02 | 初态 bob.trust；同章两拍分别写 alice.trust_level 与 bob.trust_level，关联只选择 bob | 原跨拍同名句柄覆盖，compiler 将同一关联套给两拍并误拒；修复后两个句柄独立，合法 bob 关联可发布，alice 不受影响 |
| AUD-03 | 前章建立口令，后章在具体 beat 读取，当前场不重写 | 原 compile/Guard 通过，但章包与 prompt 无值；改前章值后后章摘要不变。修复后值到达消费点且相关身份改变，无关章不变 |
| AUD-04 | 当前四类合法非空字段；新协议另补完整生命周期样例 | 原添加字段后 WriterIR 完全不变；修复后每个已声明消费有真实输入；采纳生命周期契约后，再验证建立前、建立拍、有效期和关闭后 |
| AUD-05 | fake 全链生成后，删除全部 CommitBundle，再只运行 Stage7 组装 | 原 14 份 bundle 全删仍可读且 assemble=ready；统一发布启用后正式消费必须拒绝，恢复可从原证据免费补尾再交付 |

AUD-05 属拟采纳发布契约的反例，AUD-04 的新增生命周期义务同样先完成契约裁决再启用；
当前四字段漏包仍按现役契约证明。不要把新业务规则改写成“早已要求如此”。
这些反例均须走真实 producer/compiler、packet renderer 和消费端，
不能只断言 helper 输出。A0 同时确认现役五态、章包摘要、请求身份、回执和发布证据各管什么，
避免用一个 ready 布尔值包办所有状态。

落点：`tests/test_str06_stage2_publish.py`、`tests/test_str06_chapter_closure.py`、
`tests/test_str06_stage4_protocol.py`、`tests/test_str06_body.py`、
`tests/test_str06_runtime_records.py` 与 `tests/test_str06_product_entry.py`。
新增用例按真实职责登记到 `tests/suite_catalog.json`，保留原有正反例分母。

### A1：修复源状态关联的表示边界

改动入口：`novel/stages/stage2/associate.py`、`novel/stages/stage2/compile.py`、
`contracts/schema/observation.py`、`novel/stages/chain.py`。先读
`novel/stages/stage2/AGENTS.md`，保持 compile 是 Characters／Blueprint 唯一 writer。

1. 状态目标投影接受契约声明的 typed 对象并显式序列化；未知类型、缺必要字段均精确报错。
   world 是合法显式类型，不能拿 `{}` 兜底表示“推测是 world”。人物关系保留主客体方向。
2. 按观察位置生成关联句柄，例如章、拍、该拍第几个 effect；key 是载荷，不承担唯一性。
   book 初态使用自己的命名空间。编码仅由 compiler 生成，关联模型只选择已给闭集。
3. 同一份 registry 同时服务 prompt、闭集检查和最终编译；每个句柄指向唯一原始 atom。
   compiler 直接查 registry，不在另一处重写 occurrence 计数规则。构建时发现重复键即拒绝，
   不依赖 dict 后写覆盖。检查数量守恒与往返定位；不得用“同 key”自动归并状态维度。
4. 关联配对保留同对象、方向、时间和作用域约束；同义性仍由 associate 决定。
   原样保留 missing／ambiguous／undecided，问题定位到 node、章、拍、句柄和源证据。
5. 将 associate 协议与 book／chapters／names 的协议分别管理。本次改变句柄含义和输入材料，
   associate 必须升版；没有变化的三类观察协议与 prompt 不顺带变更。同步 schema 冻结账、
   artifact 版本登记和实际消费者；canonical 若没有形状变化，不为迁移方便虚构升版。

恢复边界：旧关联回答建立在丢对象／丢条目的空间上，不做句柄字符串替换后继续采用。
已损失的语义不能由 compiler 猜回。当前 owner patch 要求原基线请求身份匹配，不能假定
它已经支持跨协议修订；可在新闭集上明确形成新的语义修订，由 A4a 登记跨版本证据与新请求
关系并重新验收，或经授权执行 associate 定向新请求。新语义修订不是旧回答缓存命中，
禁止只替换旧句柄或给旧请求重贴身份。
book／chapters／names 仅在请求身份、schema 与原响应证据完全匹配时有资格复用。
当前候选契约身份闸与现役跨根采用能力仍需 A4 补齐；未实际走通前报告“具备复用条件，
采用入口待完成”，不能报告“已免费迁移”。新根重建，不覆盖旧证据或清空 ledger。

完成条件：AUD-01/02 转绿；同对象跨拍多次赋值、不同对象同键、反向关系、同拍多 effect、
跨章同维与未决配对均有正反例。真实 Stage2 五节点发布双真源，源 fold 值正确，Stage4
直接消费 canonical；未改变的观察请求逐字一致。A1 核心实现可先验收，完整勾选等待 A4a，
期间记录“核心实现完成、迁移验收待完成”。采用验收必须从现役 B1 记录生成旧根，经新根
真实入口证明 book／逐章 chapters／names 免费采用，旧 associate 不直接采用，新关联按
新请求执行；第二次相同请求零新购。fake transport 精确核对调用和采用计数，证据损坏或
账未知时阻断，不默默回退到付费购买。

### A2：使每次显式读取真正到达 Writer

改动入口：`novel/stages/common.py` 的读取解析与 Context 投影、
`novel/stages/stage4/compile.py`、`novel/stages/stage4/fold.py`、
`novel/stages/stage4/book_ir.py`，以及 `contracts/schema/context.py`、
`contracts/schema/book_ir.py`、`contracts/schema/execution.py`、`contracts/schema/state.py`。
身份与回执调整还须同批覆盖 `contracts/schema/receipt.py`、`novel/stages/stage5/body.py`、
`novel/stages/stage7/assembly.py`、`novel/stages/chain.py` 的摘要、采用、恢复与组装检查。

1. 统一产出“已解析读取”：消费场／拍、typed 地址、该时点实际值、状态类别、认知模式、
   作用域和建立来源。Guard、Writer 投影与依赖计算使用同一结果；不再存在只验证到值、
   却因 `at` 非空将值丢弃的分支。来源细节放验证／审计视图，Writer 只看写作所需内容。
   依赖边从实际读到的建立来源生成，删除独立 `last_writer` 扫描形成的近似边；Guard 对账
   每条声明恰有一个正确时点的解析结果，并已进入对应 IR，而不只验证已有 entry 合法。
2. 入场读取按场前状态解析；拍级读取按该拍之前的状态解析，遵守先读旧值、后写新值。
   本场更早的写入应遮蔽上游旧值，当前拍／未来拍的写入不能倒流。把拍级解析值放到
   对应 BeatIR 的明确输入位置，不把同一地址多个时点的不同值压成一份 entry 值。
3. knowledge 必须带 holder 与目标侧认知模式 known／suspected／heard／believes；
   传闻、怀疑或相信不能升级为世界事实。源侧模式转换必须经显式语义声明与契约处理。
   unknown、unset、合法空值、未建立和作用域失效分别处理；保持现役合法性判据，
   不以空值默认通过，不从源小说或前章正文补猜。
4. 未来保护从章包实际引用的地址推导；事实成立、角色得知、读者确认分别保持时点。
   补足语义输入不意味着允许 Writer 提前确认秘密；保护信息与实际读取值共同到达消费者。
   同址重写的确认版本规则按上表采纳后执行，覆盖解除后重建与场景覆盖；未明确的历史
   版本引用回 owner，不把某一版本的确认自动套给该地址的所有值。
5. 章包摘要包含真实写作内容与契约版本；provider 请求身份由最终实际请求构造。
   验证输入、验证版本与正文 hash 由回执单独绑定。调试来源、时间戳、seal 等运行元数据
   不混入语义身份；仅 validator 变化走免费重验，不伪造成 Writer 输入变化而重购。
   这是待实施的身份／回执契约细化：现役 packet_sha256 仍包含验证材料，必须显式升版并
   同步全部摘要消费者，不得为让旧回执通过而删校验或把旧摘要改贴到新包。

完成条件：AUD-03 转绿，且覆盖前章读取在 entry／具体拍、本场先写后读、同地址多次读写、
跨场失效、knowledge、unknown/unset、未确认秘密。两类变形测试必须同时成立：改变真实
上游依赖后，最终 prompt 与受影响章身份相应改变；改变无关未来信息时，当前章 prompt
和身份不变。还要验证旧回执拒绝、未变章继续采用、受影响章有明确恢复入口。

测试主入口：`tests/test_str06_chapter_closure.py`、`tests/test_str06_execution_ir.py`、
`tests/test_str06_fold_guard.py`、`tests/test_str06_body.py`、`tests/test_str06_invariants.py`。
不能仅给 entry 视图补值后宣布完成，必须检查最终 renderer 输出及 Stage5 的真实请求。

### A3：把跨章目标、行动、问题与承诺接入状态机

改动入口：`contracts/schema/design.py`、`contracts/schema/execution.py`、
`contracts/schema/context.py`、`contracts/schema/book_ir.py`，以及
`novel/stages/stage4/design.py`、`novel/stages/stage4/fold.py`、
`novel/stages/stage4/compile.py`、`novel/stages/stage4/book_ir.py`、`novel/stages/common.py`。
同步检查 `novel/stages/stage4/hook.py`、`novel/stages/stage4/hook_context.py` 与
`novel/stages/stage5/hook_writer.py` 的真实消费，不能只修正文而让 Hook 仍漏掉相关承诺或泄漏答案。

现有四字段不足以免费推导完整生命周期：standing_goals 只有人物与文本；其余三个
只声明 started_at／planted_at／made_at，没有结束、回答、兑现或失效的位置，也未声明
所有消费位置。因此不能从自然语言猜完成时间，也不能把“字段存在”当作章闭包完成。

推荐契约把“条目定义”与“状态变化”分开，但仍都归 TargetDesign：条目保存一次语义内容，
变化进入显式 transition，消费通过引用声明；编译器生成正式 ID 和时间坐标。
不要另建可人工改写的 GoalsTruth／PromiseTruth。具体字段形状在采纳方案后由 canonical
schema 定义，本文不另存第二份 JSON 契约。

| 类别 | semantic owner 必须决定 | compiler 必须交付 |
|---|---|---|
| 持续目标 | 是整书稳定约束，还是会改变的在途目标；动态目标何时建立、改变／放弃，哪些场消费 | 稳定约束按正式书级范围投影；动态目标按状态与消费时点投影，不能一律广播 |
| 进行中行动 | 启动、完成／中断／结尾仍在进行，以及后章何处接续 | 当前进展与本章应发生的 transition；完成后不继续称“正在进行” |
| 开放问题 | 向哪些角色／读者打开、回答／取消／结尾保持开放及消费位置 | 谁此时知道什么、哪些仍未解决、何时允许回答；开放问题本身不泄漏答案 |
| 承诺 | 承诺主体与对象、作出、履行／违背／解除／结尾仍有效及消费位置 | 本章生效的约束与需要落实的变化；未作出前不能当已承诺，履行与违背不混同 |

“尚未解决”是合法显式状态；没有终止位置不能默认解释为某种结局。章节按实际消费时点
携带需要的条目状态、显式引用的历史／关闭结果、本章要执行的变化及保护条件；本章较晚才建立的条目须
锚定到对应拍，不能提前写入入场状态。未解析引用或互相矛盾的生命周期回到 Design owner，
不得删字段后放行。词面与因果含义由 LLM 决定，ID、排序、当前态、影响集合由 compiler 计算。

迁移要求：新增语义无法从旧 Design 确定性补齐，旧记录只读保留；允许明确 owner patch
补语义或授权后的 Design 定向修订，再免费重编译。不能用全部空数组填充新版本，不能
把缺失语义记成合法空。协议、canonical、IR／renderer 与回执的变化分别版本化，避免一次
统升所有节点 prompt。A2 的已解析读取实现复用到本批，不另写平行的时间线解释器。

完成条件：AUD-04 转绿；四类逐项覆盖开启前、开启拍、跨章携入、关闭拍、关闭后、结尾
仍开放、错误锚点、不同知情者和读者揭示。测试必须证明增加、修改、删除一个实际被消费
的条目会改变相应 Writer 输入；仅导航或无关未来条目变化不扩大失效。同步覆盖
`tests/test_str06_hook.py` 的 Hook 设计、Context、成文与恢复。使用纯人工构造的
最小两章样例即可免费验结构，效果是否自然留给 B6 阅读，不能据 schema 合法声称质量提高。

### A4：统一发布证明，并把免费恢复先做完

设施入口：`contracts/schema/workflow.py`、`novel/store.py`、`novel/work_units.py`、
`novel/llm/paid_requests.py`、`novel/paths.py`、`novel/stages/chain.py`。
推荐复用并升版 CommitBundle，不增加第二个发布数据库、独立 published 标志或第二本成本账。
Runtime 的 send 继续只返回耐久回答，不接受领域 compile／validate 回调。

| 子项 | 可独立交付的条件 |
|---|---|
| A4a 现役记录受限采用 | 先补当前 WorkUnitAttempt／blob 的跨根读面与显式修订证据；用 A1 的新协议做真实采用验收，交回 A1 勾选依据；不等待全部发布改造 |
| A4b 提交契约与恢复原语 | 明确多输出组、幂等提交身份、prepare 字节与封存验证；免费补尾先于硬闸开放 |
| A4c 正式 writer／reader 接入 | A1–A3 形状冻结后，全部产物组接同一发布口，真实读口消费同一证明，删本批旁路 |
| A4d 故障与产品验收 | 提交窗口、并发、版本变更、坏证据、重启、采用与费用全部闭合，再交 A5 |

发布完整性与业务有效性是两件事：seal 证明某组输出按某组输入完整发布，不能证明小说正确、
回执仍新鲜或交付当前可用。读取依次核对发布组、契约／引用与业务当前性；既有章包、正文
摘要、NameMap 残留和 delivery manifest 检查继续保留。Stage0/1 维持现有机制，本批统一范围
是现役 STR-06 Stage2/4/5/7 正式产物，不借机扩大整层重构。

**提交协议与恢复：**

1. 根据唯一 writer 登记输出组与依赖。Stage2 两真源同组；免费 Stage4 编译的一组派生
   输出同组；每章正文＋章回执、Hook＋Hook 回执、最终 TXT＋delivery manifest 各自同组。
   不把不同 semantic owner 强行捆成整书事务。观察材料等下游正式读取的付费中间产物也
   有发布证明；原响应和诊断文件保持执行证据身份。
2. prepare 耐久保存确切待写字节、输入版本与摘要、输出摘要、契约和显式采用引用。
   免费输出允许没有付费引用；不能继续仅在 `refs` 非空时才封存。
   同一准备记录重试沿用同一提交身份，不因 created_at 或进程重启制造重复业务版本。
3. 只在短提交窗口持锁，复验读集未变化，再写组内输出并逐字回读，最后原子写 seal。
   LLM 调用不持有提交锁。正式读口取得一致快照并使用该快照字节，防止校验后又读取一份
   已被替换的内容；并发章不能混用彼此的回执、provenance 或费用归属。
4. 无 seal、seal 损坏、组内缺文件或摘要不符都不能作为正式输入。诊断 inspect 可展示
   未发布证据，但须明确状态，不能经 API、下载或 orchestrator ready 反向放行。
   崩溃导致半组覆盖时允许停在可恢复状态，不拼接旧组与新组冒充完整发布。
5. 历史提交不可改写；当前版本选择有显式组身份与版本关系，不凭 mtime 或目录排序猜。
   没有匹配的完整发布组就报告缺口；元数据索引只能由封存记录重建，不成为第二权威。
6. seal 前崩溃先复验输入、契约、compiler 与 validator 版本；一致才用 prepare 的确切
   字节补尾，处理版本改变则按当前规则免费重编译／重验。prepare 缺失时，免费派生物
   从完整有效的正式输入重算；付费语义或正文还须有准确的原耐久回答与采用证据。
   输入变化则旧 prepare 失效，重新计算真实受影响范围；unknown attempt 先对账，
   不自动重购。不得为任意孤立文件直接“补盖章”。

特别区分“历史已采用”与“当前已发布”：旧 bundle 曾合法采用某 paid turn 的事实，不能因
新输出覆盖旧路径而撤销，更不能重置额度。当前消费才要求输出与当前发布组匹配并通过
currentness。原响应、accepted prose、历史 seal、ledger 保留，未变输入的购买资格不丢失。

**受限采用与版本切换：**

- 为本次登记的旧版本提供显式来源根读取，核对现役 SemanticRequest／ContentRevision／
  WorkUnitAttempt blob、原响应摘要和 ledger 成本关联。当前旧 request 文件采用入口不能
  直接当作这项能力已存在；测试要从现役 B1 记录生成旧根，再经真实新根入口采用。
- 原请求字节、模型／参数、来源身份与协议一致才复用回答；改变材料或要求的节点回到所属
  owner。只读旧 schema 用于迁移诊断，不让旧 canonical 直接混入新消费者。
- 将发布摘要与语义消费摘要分开：增加 seal 或补运行元数据不应改变未变的 Writer 请求。
  字段／材料／renderer 真改变时，按实际章包重新判断；验证器变化先免费重验。
- 在本文件登记受限 reader 的支持版本、调用入口、退出条件。它只服务显式迁移，完成旧根
  采用且没有在途恢复后删除；不扩成隐式通用迁移，也不改旧根和旧账。

必须先完成上述免费恢复入口，再启用新的发布硬闸（I-42）。完成条件包含：prepare 前后、
第一个输出写后、全部输出写后、seal 前后、采用记账前后的逐窗口崩溃；缺／坏 seal、错
digest、输入竞争、两章并发、旧根采用、费用归属、重启不重置、零新购补尾。AUD-05 需验证
“拒正式消费 → 免费恢复 → 重新交付”，不能只把 ready 改为 false。

### A5：让真实消费者与工程登记收口

入口：`novel/orchestrator/state.py`、`novel/orchestrator/delivery.py`、
`novel/views/str06.py`、`novel/api/routes/str06.py`、`contracts/schema/api.py`、
`contracts/invariants.py`、`web/src/types/str06.ts`、
`web/src/screens/str06/Str06MonitorScreen.tsx` 及现役 fixture。

- 编排只消费节点完成／恢复证明，错误回到精确 owner，不自动推理怎样修小说。
  核查整书 Agent 终验当前仍保留的退役 unknown 路径，使真实新链终态、交付视图与下载
  共同使用后端证明；退出码、文件存在、manifest present 都不能单独宣告可交付。
- Stage5 集合恢复和 Stage7 逐章核对接 A4 正式快照；只补需要处理的章，保留他章合法提交。
  变动 NameMap 只触发相应变换与交付，正文不回流到 Characters／Blueprint／Design。
- API 分开表达 missing、unpublished、invalid、stale、unknown 和业务接受结果，不把它们
  粗暴压成同一个失败或空列表；公开枚举的具体形状先在后端契约定义，UI 只消费结论。
  新的“未发布”是持久化状态，不擅自替换现有业务五态。同步 types、fixtures、路由与测试。
- 更新生命周期登记中“完整前文／已提交章前缀／chapter_prefix_replay”等旧定义为真实章包
  与集合恢复语义；检查调用方与公开枚举后再改名，不能只改字符串破坏恢复兼容。
  当前说明同步到 schema 文档；历史执行记录标为历史，不删除真实旧证据。
- 清理仅限本轮造成的废路径、重复投影与 adapter；不要为目录整齐重排所有 Stage 或拆一轮
  无行为收益的模块。现役纯 compiler、validator、repository 的边界用依赖测试保护。

完成条件：真实 HTTP 上传 → 自动编排 → 真实 CLI 子进程 → 发布组 → Stage7 → 下载，
均使用 fake transport；增加漏依赖、未封存、缺章、Hook 失败、NameMap 修改、服务重启场景。
分别检查进程终态、后端业务结论、字节与 UI 状态；不能继续只断言下载文件存在。
新硬闸通过现有 CLI／恢复控制面可解除，Stage 屏保持只读，不新增人工批准按钮。

### A6：验证矩阵、执行命令与批次交付

| 批次 | 定向验证入口（按真实变更补直接消费者） | 本批不得省略的证据 |
|---|---|---|
| A1 | stage2 protocol／owners／publish、source、workflow、受影响 Stage4 | 句柄唯一与数量守恒、真实合法发布、错误对象拒绝、原观察请求不变 |
| A2/A3 | chapter_closure、stage4_protocol、execution_ir、fold_guard、body、invariants | 值真实出现在 renderer、两向失效测试、四类语义全生命周期、免费恢复 |
| A4 | runtime_records、store、work_units、workflow、body、naming、views | 每个崩溃窗口、组内一致性、现役记录采用、坏证据拒绝、费用不重复 |
| A5 | product_entry、API HTTP/screens、views、CLI dispatch、orchestrator、前端四件 | 真子进程 fake 全链、终态／下载一致、刷新与重启、失效恢复 |

表中模块简称对应现役测试文件，实际执行先用选择报告核对范围；新测试模块须登记。
每个形状稳定的 schema／节点／API 批次补契约 §9.5 五文件。A1–A5 子批次使用定向证据，
不因提交或登记重复全仓。A4 涉及共享持久化；其完整回归是否提前执行按根指南评估，
影响无法用明确消费者闭合时必须提前跑，不能以等 A6 为理由排除未知影响。

A6 按最终生产代码、契约与测试基础设施集合执行一次默认非 corpus 回归；前端已改变则
执行 test/typecheck/seams/build。工程基线此后若再改，按 B6a 规则重验，不复用过期成绩。
corpus、真实成书阅读、Golden 分别报告；机械通过不证明文学质量。

Windows 使用 `novel_refactor`，agent 的 pytest 按 `tests/AGENTS.md` 在沙箱外执行；
当前 worktree 路径过长，固定复用 `E:/_tr` 与 `.test_cache`，不得并行跑共享根。
以下为执行配方，不表示本次写文档已经运行整改测试：

```powershell
# A1 定向起点；按最终影响补消费者，不使用它冒充完整验收。
& 'D:\Miniconda\envs\novel_refactor\python.exe' -B -m pytest -q -n 0 -x tests/test_str06_stage2_protocol.py tests/test_str06_stage2_owners.py tests/test_str06_stage2_publish.py tests/test_str06_source.py --basetemp=E:/_tr -o cache_dir=.test_cache --junitxml=.test_cache/str06-a1.xml

# 形状稳定后的契约五文件。
& 'D:\Miniconda\envs\novel_refactor\python.exe' -B -m pytest -q -n 0 tests/test_docs_references.py tests/test_coverage.py tests/test_rules_registry.py tests/test_hard_counterexamples.py tests/test_ingest_fixtures.py --basetemp=E:/_tr -o cache_dir=.test_cache --junitxml=.test_cache/str06-contract.xml

# A6 最终工程回归；不再加路径或缩小分母的选择条件。
& 'D:\Miniconda\envs\novel_refactor\python.exe' -B -m pytest -q -n 4 --dist loadgroup -m "not corpus" --basetemp=E:/_tr -o cache_dir=.test_cache --junitxml=.test_cache/str06-engineering.xml

# 在 web 目录依次执行。
npm run test
npm run typecheck
npm run seams
npm run build
```

每批交付必须在本节更新：状态／提交、契约版本、受影响请求与产物、反例编号、准确的
通过／失败／skip 分母、真实消费与恢复证据、剩余问题及下一批。每条 adapter 都有删除条件。
文档、代码、测试闭合后才能勾选；本轮文档完成不勾选 A0–A6。

### B6 执行与后续优化次序

1. A6 后冻结代码、协议、配置、输入和输出根。先盘点可核验的已购回答与不可采用节点，
   按实际章数、模型参数、重试规则与剩余额度给出会话报价；付费与正文外发分别授权。
   仍用 `NOVEL_DATA`／`NOVEL_CORPUS` 读取指定 Workbench，`NOVEL_OUTPUT` 独立输出，
   不覆盖源语料或旧验证根。本次编写方案没有核查这些语料与旧输出，记为“没查”。
2. 从真实产品入口生成《夫君的报恩》。失败定位 node/item：确定性缺陷免费修；语义缺口
   回所属 owner 窄修订；pending／unknown 先恢复。不得为了拿到成书临时关闭新闸或整书
   反复重购。逐调用保留 provider/model、耗时、token、reasoning、缓存与未知费用。
3. 开发 agent 在验收会话中先核查章连续非空、回执与发布组、改名零残留、交付字节、
   下载、重启恢复、ledger；再离线通读全书和 Hook，对照源书与 F5e 基线，给用户当前
   版本成书与问题记录。这是开发性验收，不给整书 Agent／Runtime 新增语义评审节点。
   阅读重点为事实／因果／知情／揭示，以及转场重开、重复交代、无名细节冲突、声线漂移。
   - **人工阅读验收协议（占位）**：维度、记录方式与归因取值的协议全文见 `PLAN.md`
     「人工阅读验收协议」节；R4 批正式登记进本节，B6-A 与 B6-B 共用。上文阅读重点是
     工程核查与开发性阅读视角的补充，协议登记后以协议维度为准，不另立第二份维度表。
4. 用户通读结论绑定确切成书版本。遵守 D1/D11：工程缺陷修复，文学意见登记为下一轮输入；
   已提前合入 main 不表示 B6 完成。机械回执仍保留语义未评，不由免费 Guard 代签质量通过。
5. STR-06 关闭后的文学优化仍依现有“本轮之后”执行，再做 D10 两本短篇 Golden；
   B6c 保留为该后续质量工作编号，不把它提前当作本提案的常驻生成节点或更改关闭条件。

后续优化按实证推进，不与 A1–A6 混为无边界重构：

| 顺序 | 优化对象 | 决策证据与约束 |
|---|---|---|
| 质量优先 | 漏保／过保、源结构泄漏、目标因果、衔接、声线和导演说明 | 从 B6 的具体章／拍反例定位 Source、Design、Binding 或 Writer；先确认章包是否已充分提供语义，再改所属 prompt；不让 Writer 代做缺失规划 |
| 输入精度 | 无关人物声线、重复约束、重复状态与冗长协议 | A2/A3 完整后统计实际 renderer 字节与真实 token；只按显式依赖裁剪。全书声线现为正式书级材料，收窄需同步契约和对照验证；不截断、概括掉必要事实 |
| 付费效率 | 局部失败重跑、相同请求复用、重试与并发 | 先看请求身份与采用命中、429/unknown、活跃时间和成本分母，再调槽位；并发度不进语义身份。保留 D6 上限与截断即停，不降 effort 掩盖设计问题 |
| 质量回归 | 两本固定短篇的六维评价 | 先升级 `tests/golden.py` 的退役 rubric 引用，再冻结样本、baseline、评价协议和成本；人工为主，模型辅助另授权；评价变化先重评现有成书，不自动重生成 |

执行方案的最终成功标准是：已声明语义完整到达实际消费者，未声明的语义不被确定性代码
猜造；无关改动不扩大付费失效；未发布或证据不全不能伪装交付；真实成书的工程结果与
文学评价分别有证据。到达这一状态后再按测量结果优化成本与速度。

## 批次交付与验收

F5a–F5d 已闭合按章成文的契约、引擎与免费验证，F5e 已完成隔离入口真书成文与开发性
阅读。下列 F4R／F6／F5／F7／F8 勾选保留原证据范围；F9 已被职责解耦计划取代（D1），
其下保留执行史与证据；活动批次自「职责解耦批次（B0–B6）」起，B0–B5 免费且全部
fake transport，B6 付费与正文外发分别授权。

- [x] **F4R · 红账清偿与 Stage0 收尾（免费，先行）**（2026-09-24 完成，零模型调用）：
  - 修复 `novel/orchestrator/uploads.py`：删除对已删除 `Stage0CandidatePlan`／
    `Stage0EditPlan` 的死 import 与零调用方的 `save_edit_plan`／`save_candidate_plan`
    （同型先例：2026-09-16 删 `save_prepare_input`），模块头改为 Stage0 v3 单次机械
    transform 语义；`test_layering` 的 `_SERVICE_MUTATORS` 同步删死探测项。test_store
    经 `save_upload` 恢复收集 161 项（HEAD 上是 collection error，0 可跑）。上传与
    Stage0 定向回归绿（upload_autostart／stage0_revisions／stage01，既有用例覆盖幂等、
    算法变则整份替换、手改 clean 拒绝、物化自愈；存量书 v2 拒读由参数化拒读覆盖，
    未新增用例）。恢复收集不等于 Stage0 阶段语义验收完成。
  - 深导入 grandfather 补账 11 条（stage4→blueprint.evidence×6、source_contexts×2、
    narration×1、plot_design→shared.character_refs×1、views/stage4→stage4.plan×1），
    分母 77→88；系 STR-02／STR-03 模块改名收编时漏同步冻结清单的既存依赖，非新增；
    stale 断言核对无已消失边；F8 随旧链整层删除时清零（登记见恢复与退役表）。
  - `_SHAPE_BREAKS` 补 characters／characters_edit／blueprint_character_projection 三条
    v3 账：连同 v2 以「两次破坏」记历史，重建 owner 与成本／恢复边界已写明（新根
    stage2.characters 付费重建、owner 免费重发编辑模板、projection 免费 revalidate）；
    旧版拒读由参数化 `test_only_the_current_version_is_read` 按注册表分母覆盖。
  - 定向回归：六文件组合 485/485 绿（独立 basetemp、`-n 0`）。本批为删死代码＋两处
    测试账本，按口径不触发默认非 corpus 回归，未伪称完成。
  - 遗留（被收集错误掩盖的既存账，非本批引入）：`test_store.py` 161 收集中 74 失败——
    49× 夹具写已退役 `stage4/plan_chapters(.INVALID).json` 被拒、17× freshness 断言
    `('stale','consumed_changed')≠('fresh','match')`、零星 Characters v3 波及
    （KeyError 'aliases'、EvidenceBin 校验）。修复须更新夹具至现役契约且不缩分母，
    归 store 与退役路径清理批次（F7 接正式 Store 时核对、F8 清账前必须归零）。
- [x] **F6 · Hook 链与 Stage7 终态**（取代旧“仅消费适配”范围；F6a/F6b/F6c/F6d
  已于 2026-09-24 全部完成并提交到隔离分支，其中 F6c 用户授权付费 4 次）：
  - [x] F6a 契约与免费反例：Hook 必要选择进正式 Design（PresentationGraph.hook 必填、
    publish_graph 唯一发布），删除 Context 后免费重建字节一致；source-hook 混入、悬空
    引用、误载必保义务、跨场冷开乱序、隐藏身份三层防线（称谓释放／presentation 字面/
    withheld_literals）、第三人称多视角、起笔事实时序与禁止集、Hook 窄改不扩大正文失效
    均有免费反例钉住。Naming 覆盖复姓（皇甫不截首字）、同姓亲属（同 family_id 新姓一致、
    不同源姓不得合并）、同姓不同人、独立化名显式赋值、纯称谓不进应改名集、漏项、
    合法保留带理由、名称位一致、单遍替换及 residual 不误报（目标名含旧字面、kept 含
    映射键）；名称结构投影接管旧 name_distinctiveness 的有效职责。契约测试 49 项新增
    （test_str06_hook／naming／retirement）。
  - [x] F6b 引擎：原型已验证行为迁移完成，无硬编码人物、正文 POV 众数、强制第一人称、
    全局缓存、首字猜姓及一人一姓假设。`literal_scan`（单遍长名优先认领区间，Stage5/7
    共用）、`name_structure`（Stage2 names 窄调用 compile＋只报告的姓氏构词探针）、
    `hook_design`（确定性校验臂＋唯一 publisher）、`hook_context`（纯投影）、
    `hook_writer`（单产＋机械验＋同 Context 有限重写 2 次，attempt 进请求身份）、
    `naming`（投影／NameMap v5 compile／纯函数替换表／ApplyNames／残留／diff proof）、
    `assembly`（body_access 四态分开、not_ready 显式、交付身份与回执分离）；删
    `delivery.py`（重放稳定/长名优先/claimed-kept 职责迁新实现）。Preserve/Brief/章节
    编译改吃 core view；Bind/Compile/Context/Guard 类型收窄 BodyDesign。隔离入口
    `--through stage2|stage4|stage5|stage7`、新 owner names／hook_design／hook_writer／
    naming、混合采用（wire 免费、缺失未授权停在 owner 并写 needs_authorization）。
  - [x] F6c 付费冒烟（2026-09-24 完成，用户授权，分步核读）：基础 4 次付费一次通过
    （names 8.6k＋hook_design 17.3k／32.4k＋hook_writer 1.3k／6.5k＋naming 0.8k／1.9k
    tokens，合计约 28.0k prompt＋43.4k completion；writer 一次过零重写，未触重写上限）。
    产物 `outputs/str06_f6_smoke_f6_smoke_12/run/`：名称结构四人四家族与人工核对一致
    （陆大哥/姓沈的构词、纯称谓防漏标）；Hook 第三人称芸娘限知、4 材料倒叙冷开
    （s06→s01→s02→s03）、forbidden 四条零兑现、295 字；NameMap 全员换姓（萧/温/顾/柳）、
    名称位与源结构一致、纯称谓保留、11 处替换零残留；Assembly 显式 not_ready
    （body_not_implemented）。同根重放零新调用、10 份关键产物字节一致。
    **质量披露**：机械通过与人工核读不等于成书质量验收；楔子的冲突、信息升级、悬念与
    不提前兑现已按 str06.md 阅读项核读，F5e 已完成成书开发性阅读，用户验收归 F9（D1 后由 B6b 承接），跨书质量结论归后续 Golden。真书证据
    修复一处：names wire 允许家族对象内与键一致的冗余 family_id（归一，不一致报错）。
  - [x] F6d 退役准备：`contracts/schema/retirement.py` 登记 106 项（节点与编排、
    产物与版本账、批准绑定 s6/s6r4_/HookApprovalResolution、API/Views/前端 Stage6 面、
    判据、14 个纯 Stage6 测试文件、Stage7 现役对 stage6/hook.json 的依赖、已死残留
    stage6.semantic 与 _api.md 幽灵路由等），`test_str06_retirement.py` 双向对账：
    登记项必须真实存在、现役面（NODES/ARTIFACT_PATHS stage6 键、stage6 引擎与 schema
    文件、纯 Stage6 测试、前端 Stage6 页、/stage6 路由）必须全部在册。已退役判官不算
    新增成果。
- [x] **F5 · 按章成文与完整交付**（F5a–F5d 免费面与 F5e 真书重跑均已完成；
  F5e 于 2026-09-24 用户授权付费执行）：
  - [x] F5a 契约与 renderer：Design 升 RC4——`TargetScene.realization_brief` 与
    `ChapterAssignment.purpose` 必填非空，旧 RC3 Design 明确拒读（不用空串兜底、
    不由 compiler 补写）；`realization_brief` 只承载呈现方向，关键行动/持久认知
    变化/秘密/承诺/因果条件必须同时落在正式故事字段。双视图职责重划：
    `VerificationIR` 补 `BeatIR` 逐拍定位与 `prohibitions`，`WriterBrief` 只投影
    呈现（事实清单不再重复进 Brief）；`WriterBookIR = BookBrief + ChapterIR[]`
    唯一落盘 `stage4/ir/writer_book_ir.json`，BookBrief 只做确定性导航与称谓释放
    位置投影（不调 LLM 总结、不取 POV 众数、不补造统一文风）；章预算按场景权重
    largest-remainder 整数分配、总和恰等于书级目标。确定性 renderer 六层阅读面
    （协议→规划参考→完整前文→章目的→本章场景→交付要求），完整 IR 与完整前文
    真实进请求，lineage/源拓扑/审计字段剥离，同输入字节一致；协议/renderer/prompt
    各自版本化进请求身份。`ChapterReceipt` 章级回执绑定章序、全书 IR、实际前文、
    改名前正文、请求身份、协议与校验版本；场景回执与 Fidelity 判定随取消的生产
    正文 Judge 退役。免费反例：canonical 缺失拒绝、brief 原样投影、双视图同源、
    逐拍事实不遗漏、知情/否定/释放位置可追溯、未来章 brief 修改使请求变化、
    前文修改使请求变化、Hook/NameMap 不扩大正文失效。
  - [x] F5b 按章 Writer 与机械控制：`body_checks` 章级机械验（非空/编码/包装/
    未释放专名/篇幅带；截断按 finish_reason 折算 typed 错误）——硬失败不能披露
    放行，只有篇幅偏差可在额度耗尽后披露；`body_writer` 同 IR 同前文＋上一稿＋
    typed 错误的有限重写（首次+再 2 次，attempt 进请求身份）、确定性候选选择
    （硬检查全过优先，耗尽后按相对目标偏差最小、平局取较晚）、无合法候选即停止
    后续章。免费反例覆盖全部分支与篇幅披露形状。
  - [x] F5c 真实入口与恢复：`novel/stages/chain.py` Stage4 腿完整发布 graph 与
    WriterIR 后结束（零正文购买），Stage5 腿按章串行成文＋Hook，Stage7 由真实
    Assembly 产生交付；`--body-chapter-limit`（前缀式）、`--check-only`（免费，
    只允许缓存/wire 采用）、`--book-call-ceiling`（锁内按准入凭据累计，
    pending/unknown 不释放额度，缓存采用不耗额度）、`--body-band-low/high`；
    付费边界修复为「缓存先于授权拒绝」（`PurchaseRequired` 停在对应 owner 写
    needs_authorization）；提交顺序=原响应耐久→检查→候选→原子写正文→原子写
    章回执；恢复从连续有效章前缀装载，回执绑定当前 IR/前文/正文字节三方核对，
    崩溃窗口（响应已存/正文已写回执未写/manifest 未提交）免费重放闭合。
    Stage7：章回执链核对（逐章 IR 身份+前文链+正文 hash）、ApplyNames 单遍替换、
    TXT 先落盘、manifest 最后提交（含逐章篇幅读数与全书 ±10% 偏差无条件报告、
    机械范围与未评语义分开披露）。
  - [x] F5d 免费验证与文档契约检查：经真实 repository＋请求边界（fake transport）
    跑到最终成书；同根恢复零新调用、交付字节一致；IR 变化（含未来章 brief）使
    正文按新请求身份重购、hook_writer/naming 缓存命中不重购；额度上限显式停止。
    str06 定向 156/156、契约 §9.5 五文件＋layering＋test_selection＋
    artifact_versions 全绿；`tests/test_str06_body.py` 已登记 suite_catalog。
    原 Stage4 三条 skip 继续归 F7 的 Binding 恢复、owner 停止和无进展停止职责
    迁移，F8 核对后删除；历史 11 条 labeled 断言保留为质量反例，不计作新 Writer
    已通过。
  - [x] F5e 重新执行 Stage4 与按章成书（2026-09-24 完成，用户授权）：候选 run
    `outputs/str06_f5e_body_20260924/run`。Source（book/chNN/names）与 Preserve、
    Naming 经输入身份逐字核验后免费采用（names/naming prompt 与 F6c 已购请求
    一致）；Design/Bind/Hook Design/Hook Writer 新购。审读门槛①②③全过：
    章节目的 11/11 实质承载核心变化；导演说明具体且关键事实同时落在拍事件
    字段；Hook 6 材料倒序冷开无提前兑现；前三章连读与全书通读见上表阅读验收。
    交付与恢复核验：manifest、hash、零残留、零新购重放全部通过。hook wire 一处
    stale withheld 按 fail-fast 纪律报错后走 owner patch（patch 注记
    `outputs/str06_f5e_body_20260924/wires/hook_design_patch_note.json`）。
- [ ] **F7 · 新链接线与免费验证**（旧链在分支上仍现役，新面不进现役注册表）：
  - [x] **F7a 第一步 · 候选契约与身份边界**（2026-09-24 完成，零模型调用）：
    `contracts/invariants.py::NODES` 与 `contracts/artifacts.py::ARTIFACT_PATHS` 前置定义候选节点表（11 个节点：stage2
    source／names、stage4 preserve／body_design／hook_design／bind／compile、
    stage5 body／hook、stage7 naming／assemble；含 paid、lifecycle、
    executor_content_limit、flags、produces、免费复检与恢复入口；compile／assemble
    以 limit=0 结构性禁付）、候选产物表（20 份书产物：家族＋schema 版本＋唯一执行
    writer；模型自带 `contract_version` 的表值逐字对账）、workflow 记录与
    execution epoch 身份（首开／复用／code_drift／contract_drift／repair 滚动规则，
    假漂移拒绝，滚动不重置累计额度）。候选 id 一律 `str06.` 前缀，结构上进不了现役
    `NODES`／`ARTIFACT_SPECS`／`ARTIFACT_PATHS`（双向对账测试钉住）。产物模型新增
    `_provenance` 别名字段（`exclude=True`，内存 dump 与 prompt 字节不变，F5e 已购
    请求身份不受影响）。测试 `tests/test_str06_candidates.py` 17 项。
  - [x] **F7a 第二步 · 正式设施接线**（2026-09-24 完成，零模型调用）：
    `Str06Repository` 接正式 Store 机制：家族路径准入与完整路径来源账键
    （`check_rel_family`／`family_input_key`）、provenance 铸造（复用
    `contracts.schema.common` 同一套模型，付费闸与正式 ctx 同构）、freshness 复算
    （`prov.freshness`，明确 stale 拒绝、无 provenance 保持 unknown 放行）、INVALID
    孪生与 `_rounds` 归档（先归档后覆写）、原子提交；未附加上下文写已登记产物关闭式
    拒绝；新增不记账的 `inspect` 读口（断言／视图与生产消费分流）。`PaidRequestSession`
    接正式设施：新购首 wire 前经 `WorkUnitBudget.reserve` 取内容轮（单元上限＋整书
    总闸同一把锁序），响应同步进工作单元账（内容寻址），ledger 行带真实候选节点 id 与
    sid、run 绑 epoch parent；未采纳的 delivered 轮在下一次 reserve 前
    `mark_rejected` 关闭（拒绝证据保留），reserved／unknown 轮不自动关闭、不自动重购；
    `novel/work_units.py` 新增公开 `reserve_book_call` 与 `node_lifecycle_spec`
    （NODES 优先、候选表回退，删除条件见退役表）。候选运行上下文落在
    `novel/llm/paid_requests.py`（llm 层，迁移包纪律不动）。
    **test_store 复核**：74 项既有失败与 HEAD 基线逐项一致（49 退役路径夹具＋17
    freshness 断言＋8 旧形状），零新增——均非本批 Store 接口相关，按计划整项进入 F7e
    登记。测试 `tests/test_str06_f7a_wiring.py` 18 项；str06 全套＋layering＋
    work_units／book_budget／execution_permit／test_selection 定向全绿。
  - [x] **F7a 第三步 · F5e 历史采用**（2026-09-24 完成，零模型调用）：候选 run
    `outputs/str06_f7a_candidate_20260924/run`。`--adopt-root`（可重复）登记显式历史
    采用源根：新购首 wire 前先查本地请求行，再逐个注册源根核对同请求身份的已购行——
    身份逐字相等、响应字节完整、原状态 answered、源根 ledger 有唯一真实付费行（同
    attempt_id＋指纹）；任一不符明确阻断，不补购。采用不复制响应字节进候选根（不冒充
    cache hit）：候选根只落采用记录与零 token 的 cached ledger 行，历史实付留在源根账
    内分列。wire 采用经 `bind_wire_adoption` 绑定身份／成本：ledger-row（三方绑定）、
    batch-documented（采用链可验、批次口径，F2b 逐请求账未持久如实标注）、unverified
    三档；hook_design 的 owner patch 经 patch note 基线身份核验后绑定（修订来源保留）。
    **验收结果**：F5e 原根树字节指纹前后一致（只读）；同输入交付文本与 F5e 逐字一致
    （`夫君的报恩_rewriting.txt` sha 81e4fcce…）；零新购（无 admission 计费，ledger
    21 行全 cached、token 合计 0）；30 份采用记录成本关联完整（15 ledger-row＋15
    batch-documented，含 1 份 patch 链）；同 flags `--check-only` 免费续跑：前缀恢复
    跳过 11 章正文、仅 stage4／hook 重放 3 行采用、零新购、交付字节不变。源 JSON 按
    F6c 已购 names 请求内嵌全文逐字重建（11 章 10093 字，sha 90ea4b16…）。
    **验收与盘面**：F5e 原证据字节不变；独立候选根的采用记录、来源及成本关联完整；
    同输入交付文本字节一致、零新购、可免费续跑；正式封装与 provenance 的变化单独核对，
    不要求新旧元数据文件逐字相同。候选节点可用，现役注册面不变，Store 有效反例通过。
  - [x] **F7b · owner runner 与薄驱动**（2026-09-24 完成，零模型调用）：
    `novel/stages/chain.py` 按候选节点表拆出 11 个 owner runner（stage2
    source／names；stage4 preserve／body_design／hook_design／bind／compile——
    Guard＋WriterBookIR＋HookContext；stage5 body／hook；stage7 naming／assemble），
    `NODE_ORDER` 即执行序，`run_node` 单节点可重跑；`build_evidence` 等 harness 逻辑
    归 stage2 source owner；`novel/stages/chain.py` 由 587 行退为 271 行薄驱动
    （装配 ChainContext、驱动节点计划、落 run 级状态）。每个 runner 的输入一律从盘面
    读（`repository.read_model`）：源 core 与 BodyDesign 落为正式派生产物
    （`stage2/compiled/book_extraction.json`、`stage4/design/body_design.json`，
    候选产物表 20→22 条），不再只活在内存。**两处实测修正**：① candidate repository
    落盘原用 `sort_keys` 归一字符典序，会把 F5e 已购请求的身份静默改掉（design 采用
    失配）——改为保序序列化（同正式 store 的 `_render`），prompt 按字节复算；
    ② 来源边窗口跨 run 继承会把上轮末尾的输入边写进下轮第一份产物（freshness 误判
    stale）——`begin_run` 清窗口，轮内继承保留。恢复扫描遇上游过期的回执视为前缀
    断裂（该章按新身份重做），与回执自身的 IR 绑定校验同一语义。
    **验收与盘面**：fake transport 下逐节点全链跑通（str06 全套＋layering＋
    work_units／book_budget／execution_permit／test_selection 共 500 项，仅
    test_store 74 项既有失败且与基线逐项一致）；`--node` 单节点重跑（assemble／names）
    与续跑通过；F7b 新候选根 `outputs/str06_f7b_candidate_20260924` `--check-only`
    零新购复现交付（21 行 ledger 全 cached、token 0、交付与 F5e 逐字一致、
    delivery_identity 相同、15 ledger-row＋15 batch-documented），F5e 原根树字节
    不变。F7a 候选根被契约表漂移守卫按设计拒绝复用（候选表新增两件派生产物）——
    旧根不原地升级，采用结论按新表在新根重建。
  - [x] **F7c 第一步 · 正式 Stage1 输入、配置来源与预算派生**（2026-09-24 完成，
    零模型调用）：`--book-sid` 生产路径经正式契约读 `<base>/<sid>/stage1/main_text.json`
    （`MainText` 章数组，阅读序密集校验），源输入身份＝该产物盘上字节 SHA-256，写入
    workflow 记录；`--source-json` 退为开发／测试路径。候选运行期配置落在
    `contracts/schema/workflow.py::STR06_RUNTIME_CONFIG`（Hook 长度、章篇幅带、
    取整粒度；进契约身份，与付费调用额度分开）。书级正文篇幅从源章总字数向上取整
    确定性派生（`derive_total_length_budget`，本书 10093 字 → 11000，与 F5e 一致），
    目标章数等于源章数由 MainText 模型保证。~~**Stage1 hook_copy 边界闭合：新链无消费者**~~
    （2026-09-26 质量改进批次改写：源引子恢复唯一消费者 `str06.stage2.source`，见
    F9 下的质量改进批次登记与退役表）。canary（`outputs/str06_f7c_candidate_20260924`，正式 Stage1 输入＋
    `--check-only`）：零新购复现交付（21 行 ledger 全 cached、token 0、交付与 F5e
    逐字一致、delivery_identity 相同、15 ledger-row＋15 batch-documented）；**输入变化
    闸实证**：改动 stage1 一章文本后重跑被 `ProvenanceRefused` 拒绝（旧根采用不覆盖
    新输入）——变化不误用旧结果。facilities 顺序纪律：workflow manifest 须在 base 干净时
    初始化（先 facilities 后书目录），候选工具在 manifest 缺失且 base 非空时按正式
    `_require_clean_base` 拒绝。
  - [x] **F7c 第二步 · 恢复职责（INVALID 留痕／owner 停止／熔断／精确补尾）**
    （2026-09-24 完成，零模型调用）：bind 的机械校验闸移到节点自身——拦下的稿进
    `stage4/grounding/semantic_binding.INVALID.json` 孪生（`gates_skipped`＝问题清单、
    wire 原样留证）再 fail-fast；run 级状态记 `failed_node`（owner 停止的精确归属），
    后续节点不再执行。同指纹熔断：校验失败不重购（原响应已耐久，重放走缓存、零 token）。
    binding-only 崩溃精确补尾：删 `stage4/compiled/execution_plan.json` 后重跑零新购、
    交付字节不变。测试 `tests/test_str06_f7a_wiring.py` 26 项（含上述四类反例）。
  - [x] **F7c 第三步 · name_distinctiveness 消费接管核验**（2026-09-24）：新链
    （`novel/stages/{stage2,stage4,stage5,stage7}/`、`novel/llm/paid_requests.py`、`novel/stages/chain.py`、
    `contracts/schema/*`）对 `name_distinctiveness` 的功能引用为 0（仅
    source.py 三处注释说明接管关系）——Stage2 names
    的名称结构（`NameStructure`／`name_projection`）是唯一专名面，Stage5/7 与
    consumption 的新链消费者全部走名称投影（F6 已落）；旧消费者（`novel/consumption.py`
    三处、`novel/core/naming.py` 首字猜姓）只服务旧链，随旧链退役归 F7e／F8c 登记。
    真书证据：候选根 naming 11 处替换零残留（F7a–F7c canary）。
  - [x] **F7c 余项 · public_state_ref 反例与 repair 审计**（2026-09-25 完成，
    零模型调用）：旧 Blueprint `public_state_ref` 三项职责由新链 IR 称谓释放
    位置表与公共投影接管，`tests/test_str06_f7c_remainder.py` 15 项免费反例：
    ① 身份隔离——participants 不等于揭示（fu_jun 全程参与 s01 但无释放面）、
    未揭示称谓逐个独立释放不提前合并（苏姑娘／苏芸娘分拍释放）、未释放专名是
    硬失败（`[withheld_literals]` 重写耗尽后无合法候选即停止，不披露放行；
    只用已释放称谓的稿同尺接受）；② 跨章携入——第 2 章引入不向前携入（第 1 章
    释放集／禁用面／场景 IR 均不含陆大哥，释放后退出禁用面，全书未引入的陆兄
    两章都禁用）、只改后章引入时前章释放集与第 1 章场景 IR 释放面不变、
    release_schedule 记最早引入章、同章后场引入不向前场携入；③ 状态解除——
    unset 以显式解除标记到达消费面（status=unset、value=None，不残留 stale 值、
    不静默消失），`value_of` 返回 None，且解除是真实输入变化（s03 消费身份随
    之窄失效）；unknown 仍阻断消费，两态不相混（I-42）。analyze receipt／draft
    repair 审计：同请求身份只购一次（二次 ask 零 token 缓存行、轮次不翻倍）、
    采用重放幂等（记录字节不变、候选根零实付行）、候选根已购行被篡改拒绝采用
    也拒绝重购、未授权停在 owner 零调用零账不建额度且授权后同根重跑 12 次新购
    与全新运行一致、付费产物漏补提交后单节点重放零新购业务内容逐字一致、同指纹
    再次拦下旧孪生先进归档再覆写且正式位不落污染稿。旧消费者退役登记归 F7e。
    **验收与盘面**：本批 15/15 通过；str06 定向套件（15 文件含 layering／
    test_selection）286/286、付费设施（book_budget／execution_permit／work_units）
    73/73 全绿，零新增失败；`tests/suite_catalog.json` 同步登记（三例 stage 层）。
  - [x] **F7d · API／Views／前端候选只读面**（2026-09-25 完成，零模型调用）：
    契约模型 `contracts/schema/api.py`（16 个视图模型：不抄字面量取值面、
    无 badge/passed/counts、契约层不背 web 栈，与现役 api.py 同构三条纪律），
    纯函数 views `novel/views/str06.py`（候选根定位以 workflow 身份记录为标记，
    读口走 inspect 不记边；present 显式分开缺席与合法空值；semantic 恒 unverified；
    ledger 实付与缓存分开、usage 缺失单列不并零），只读路由
    `novel/api/routes/str06.py`（`/api/str06/candidates/...` 12 条 GET，与现役
    `/novels` 面物理分开；未知候选根 404，成书缺失 404 不是空 200；服务层不写
    产物盘、够不到 LLM 层与判官层，layering 守卫绿）。前端：`web/src/types/str06.ts`、
    `useStr06Monitor`、`/str06/:candidate` 只读监控屏（不进左导航、无 check 面板、
    无写按钮，顶部常驻「候选链 · 未切换」标注）、mock 经
    `fixtures/backend_v1/str06_candidate.json`（fake transport 全链合成录制，
    非真实书正文）。types→endpoints→hook→页面→fixture 同批闭合。
    **验收与盘面**：`tests/test_str06_views.py` 9 项（合成候选根逐面核对、半程
    候选根 present 分开、HTTP 与 views 逐字段一致、未知/非法候选根 404、
    监控读口零写盘、契约纪律对账）全绿；前端 typecheck／seams／test 183／build
    全过；契约五文件与 test_api_http（公开接口面同步登记 12 条新端点）绿；
    `docs/schema/_api.md` 与 `web/AGENTS.md` 路由表同批更新。现役路由与 Stage6
    页保留至 F8，候选读面不伪装为已切换产品。
  - [x] **F7e · 退役登记、红账对账与候选冻结**（2026-09-25 完成，零模型调用）：
    `contracts/schema/retirement.py` 增补 `STR06_F7E_RETIREMENT`（384 项，
    与 F6d 106 项合并成 F8c 完整删除清单，两表目标不重复）：旧 Stage2/4/5/7
    四个引擎包整包登记（91 个引擎文件由对账测试机械枚举）、NODES 旧链键 10 个、
    ARTIFACT_PATHS 旧链键 30 个、consumption 投影键 13 个（stage6 两键 F6d 漏登
    在此补齐）、判据层（judges stage2/4/5/7＋quality＋hard_cases）、编排面 15 个
    改写消费者、views 5 删 3 留、API 14 个路由模块、前端旧屏 5 删 12 改写、
    schema 文档 4 删 4 改写、测试三桶与 88 条 grandfather 边逐条登记。
    测试三桶机械全覆盖 234 个 test_*.py：keep 37（基础设施/守卫/范围外，理由逐条
    登记在 `STR06_F7E_TEST_KEEP`）、rewrite 80（F8b 切新链后保留）、delete 87
    （F8c 先迁独有反例再删）、str06 候选 16、F6d 已登记 14。
    **红账对账** `STR06_F7E_RED_ACCOUNT`（7 项）：F6 全量回归 420 项（含 test_store
    74 与两个收集错误，包含关系不相加；原 JUnit 不在当前缓存，按登记口径核对，
    F8d 全新回归取证）、test_store 74 项（49 退役夹具＋17 freshness＋8 旧形状）、
    两个收集错误文件本批实测定位（test_blueprint_v11_compression.py 导入已删的
    dedupe_exact_story_before；test_stage4_agent_boundary.py 导入不存在的
    novel.agents）、test_schema_api 3 项（干净树复现实测）、88 条 grandfather 边、
    F7a 候选根漂移拒用（F7b 已闭合）。
    **候选组合复验**：F7c canary 根复制到 `outputs/str06_f7e_recheck_20260925`，
    同 flags（--check-only＋--wire-dir＋--adopt-root＋--reasoning-effort medium）
    全链重放：25 行 ledger 全 cached、实付 0、token 0、成书与 F5e 逐字一致
    （sha 81e4fcce…）、delivery_identity 696d5824… 与 canary 相同；epoch 按
    code_drift 滚到 ordinal 2（额度不重置）；F5e 原根只读未动。
    **冻结**：`tests/test_str06_f7e_freeze.py` 钉住 F8 激活基线——契约身份
    `04b6fb22f222d2118e996c9c1835e7fa5c0425bd69ea8b17e70aa7d3a9154466`、11 节点
    （9 付费＋compile/assemble 结构性禁付）、22 份书产物、运行期配置
    （Hook 280/360、篇幅带 0.6–1.3、取整 1000）；表内任何变化先改冻结记录再激活。
    **验收与盘面**：`tests/test_str06_f7e_retirement.py`（9 项双向对账）＋
    `test_str06_f7e_freeze.py`（5 项）全绿；str06 定向套件 312/312、契约五文件
    ＋test_api_http 508/508 全绿，零新增失败。此时不宣称旧链已退役或完整回归已通过。
    **整批门槛回归**（2026-09-25，按根指南对含生产代码的最终改动集合执行）：默认非
    corpus 全量回归 5448 项——5030 通过、418 失败/错误（F6 记录口径 420，通过数由
    4902 增至 5030，失败集无 str06 项、无本批新增；两个已知收集错误文件按 F6 同口径
    `--ignore`）、8 个跳过全部带说明（其中 6 个是 F7c 已迁接管、登记待 F8c 删除的旧
    skip）。中途修正一处本批引入的测试副作用（f7e 对账测试曾用 `del sys.modules`
    重导入 contracts.schema.str06，导致全量运行时同类双实例假红——已改子进程验证，
    复跑确认 str06 零失败）。
- [ ] **F8 · 分支内一次切换与旧链彻底退役**（免费；可分步提交，main 在 F9 后合入一次）：
  - [x] **F8a · 契约与公开规则翻转**（2026-09-25 完成，零模型调用）：
    - 激活面：`contracts/invariants.py::NODES` 由 14 个旧链键翻为 11 个 `str06.` 节点
      （候选表原样激活，`tests/test_str06_candidates.py` 双向对账）；`STAGES` 删 `stage6`，
      旧 14 键进 `RETIRED_NODES` 只读解码；`contracts/artifacts.py` 旧 38 个路径键移入
      `RETIRED_ARTIFACT_PATHS`（拒读），22 份候选产物以 rel 为键上位；
      `ARTIFACT_SPECS` 挂 14 份模型产物（全 v1；形状史由 `contract_version` 字面量与
      候选表 RC 标签承载，R-2.5／R-5.11b 写明）；NameMap v4→v5；`artifact_regex` 补
      `{title}`；`PARTIAL_REWRITE_FLAGS` 空表（机制退役）；`VALUED_FLAGS` 收敛为节点级。
    - 判据注册表：`contracts/judges/str06.py` 62 条（M1–M6、章级机械验、Hook 机械验、
      命名残留、交付回执链；旧五张 judges 表随旧链退役）；`novel/consumption.py` 投影键
      翻为新链 11 节点＋stage1；`_SHAPE_BREAKS` 账翻为新链口径。
    - BACKEND_CONTRACT：R-2～R-10 按新链改写（I-40 与 `R-5.2` 翻为 N 章连续非空、
      允许跨章重构；R-9 保留退役条款；R-5.11b／R-14.1 及 R-11～R-14 受影响条款同步；
      §五 反向覆盖率表按 6 层重算）；`docs/schema/stage2／4／5／7.md` 重写（新链内容＋
      判据表＋计数行），`stage6.md`／`plot_blueprint.md` 删除；根／novel／web 指南同批更新。
    - 验收：契约 §9.5 五文件（test_coverage／test_rules_registry／test_hard_counterexamples／
      test_docs_references／test_ingest_fixtures）全绿；str06 定向套件（candidates／freeze／
      retirement×2／views 外功能面）全绿；test_invariants 全绿。test_hard_counterexamples
      的 25 个新链最小会红样本逐条真跑会红（hard_cases.py 重写）。
    - 已知过渡态（F8b／F8c 处置，非本批引入）：① 编排层旧链分支（inbox_projection 的
      Stage6 workpack、execution_permit／state／nodes 的旧节点特例）仍引用已退役的
      Stage6 API 模型 ⇒ `novel.api.app` 导入链在 F8b 完成前不通；② `tests/
      test_test_selection.py` 的 str06 选测范围断言把旧链模块算进消费者闭包
      （4 项，F8a 提交时已红），随 F8c 删除旧链测试模块与 `change_scopes.str06`
      登记后闭合；③ 旧链测试文件的收集失败按 delete／rewrite 两桶归 F8b／F8c。
  - [x] **F8b · 编排与服务翻转**（2026-09-25 完成；F8b-1、F8b-2、F8b-3 均已提交）：
    CLI runner、queue／state／operator／Agent supervisor／execution_permit／admission／
    delivery／upload_autostart 改走新节点，API／Views／前端路由同步激活；按登记删除
    Stage6 分支。旧书只读识别为 `legacy_incompatible`，独立根重建，不继承旧批准或
    接受记录。
    - [x] **F8b-1 · CLI 新链可达**（2026-09-25 完成，零模型调用）：`novel.cli run
      <str06 节点> <sid>` 在旧链前置之前分流到 `_run_str06`；付费确认闸（`--yes`）
      前置到分流点；`novel/stages/chain.py` 的装配体提取为 `execute(args)`（工具
      CLI 与 orchestrator 节点执行共用同一条路径，`base` 显式传入）；argv 保持
      `novel.cli run <node> <sid> --base …` 形状（run_step 信标身份解析不变）；
      付费边界（工作单元＋整书总闸＋唯一 ledger）与 workflow／epoch 身份由
      PaidRequestSession／CandidateRunContext 闭合。`tests/test_str06_cli_dispatch.py`
      5 项（argv 形状／节点旗标面／分流点／付费闸／未知旗标拒绝）全绿。
    - [x] **F8b-2 · 编排状态与服务面收口**（2026-09-25 完成，零模型调用）：
      Stage6 服务分支按登记切除（CLI `_runners` 无 stage6 腿、queue／permit／
      inbox_projection／operator／reviewer 的 Stage6 workpack 与 prepare 恢复面删除；
      views／routes／前端 Stage6 页、`stage7` 旧视图与 fixtures 同批退役）。
      state 完成度面翻新链：章稿 `stage5/body/ch{nn}.txt`、计划章集读 BodyDesign、
      INVALID 清单与回执章号绑定改新链路径族。`check_node` 改「NODES 顺序第一座
      桥」代表语义（新链复检是节点级 `--check-only`，零桥仍报错）。`RunContext`
      对 readonly＋`RETIRED_NODES` 放行——旧链恢复／审批／完成度探测读旧盘面
      是 `legacy_incompatible` 只读识别的正读路，生产入口仍按退役拒识。
      `PROFILED_LLM_RUNS` 翻为 9 个 `str06.` 付费节点默认跑法。edits 路由族
      （review 改稿／stage2 编辑三族／stage7 残留采纳）随全自动单模式整族退役，
      文件保留空 router。F8a 遗留破损修复：inspection 的 `ARTIFACT_PATHS` 漏
      import、enums `STAGE6_*` 悬空恢复 `_WITHHELD`、stage6 链模块（hook／
      prompts／schema.stage6）按删除前值内联过渡（F8c 整包删除）。corpus 付费
      预览工具与测试按登记修订为 F8b 提前删除（F8a 已删其派生源，修复需手抄
      冻结节点表违反工具自身纪律；接管：新链付费边界由 PaidRequestSession
      工作单元／整书总闸／ledger 守卫，corpus 线重启时按当期契约重建预览面）。
      验证：契约 §9.5 五文件＋`test_invariants`＋str06 定向（candidates／freeze／
      retirement×2／cli_dispatch）全绿；inbox／api_screens／api_edits／
      artifact_versions 全绿（api_edits 重写为退役守卫、schema_api 移除收集期
      旧判据注册污染）；前端 test 171／typecheck／seams／build 全绿，backend／
      plot 生成类型同步重导。定向面余项：schema_api 3 项（F7e 红账，F8d 闭合）。
      **剩余（F8b-3 起）**：rewrite 桶其余文件的旧节点载荷改写（llm／cli／
      queue／nodes／state／api_http 等约 870 项，根因集中于旧节点字符串与旧
      fixtures，按文件分批推进，F8d 前清零）。
      **F8b-3 进行中（2026-09-25，分批提交 74faff4b→当前）**：已完成并全绿——
      keep 桶与新链守卫（str06 现役挂载守卫翻转、layering 冻结清单 88→81、
      corpus 冻结坐标翻新链＋无信封派生 JSON 封闭集合、ApprovalDoc.prepare
      删除、RETIRED_NODES 补 stage7.assemble、enums 旧动作面退役守卫、
      core v3 surfaces 夹具、retired_surface/run.py AgentProfile 清除、
      schema_api 红账 3 项闭合）；store 族（test_store 161/161，74 项红账
      闭合；store 成书桥改读交付清单、hook.txt 接确定性桥）；queue/state/
      jobs/metrics/nodes（agent 改写 190 项＋_ordered_items 退役容错＋
      章回执跨路径绑定修复＋resume_verdict INVALID 防崩＋stage5 hook 面
      并入完成度）；operator（旧 prepared 恢复 lane 按 S-05 拆除）；
      work_units 族（executor_scope 翻新链、退役节点只读解码桩）；agent 族
      （78+6+21+11+39）；llm 族（132+27+27+26）；信标/运行操作面/巡检/
      恢复反例/api_run；report（44）；交付消费缝三件＋旧链语义/审批面
      六文件＋source_identity 按登记退役（接管者逐条写入
      retirement.py::_EXECUTED_BY_F8B 注记）。
      **F8b-3 尾项**（2026-09-25 完成）：test_cli（旧 runner 内构 19 节点参数化
      改 legacy_incompatible 拒读＋旗标面退役守卫）、test_execution_permit（恢复表
      翻新链 `--check-only` 闭集）、test_api_http（发布面重钉 50 条现役端点）、
      test_workflow_transition（v4→v5 投影族 narrowed 为显式拒绝、v5→v6 主路径
      五个崩溃窗口保留）全部闭合；test_store 161 项分母保留、74 项历史红账逐条
      修复/改写（F7e 红账清偿）。
  - [x] **F8c · 分流、删除与证据保护**（2026-09-25 完成，零模型调用）：迁移包
    分流到正式 stage2／4／5／7 及 contracts schema 包——`contracts/schema/str06/*`
    分流进 `contracts/schema/{source,design,grounding,context,book_ir,hook,naming,
    receipt,execution,policy,state,families,dependency,consumption}.py`（16 个视图
    模型并入 `api.py`，节点/产物表自 F8a 起就在 `invariants.NODES`／`artifacts`
    激活、未搬第二份），`novel/stages/str06/*` 分流进 `novel/stages/stage2|stage4|
    stage5|stage7/`＋`novel/stages/common.py`＋`novel/stages/chain.py`，
    `novel/llm/str06_*` 合入 `novel/llm/paid_requests.py`（`STR06_OWNER_UNITS`
    按登记删除），`tools/str06_workflow.py` 删除（装配体进 `chain.py`）。按两张
    退役登记删除旧 Stage2/4/5/6/7 引擎包、五张旧判据表、旧 views/routes、审批
    CLI/HTTP 面、rebuild 意图账与 `RebuildEventV1`；前端旧屏（read/global/stage4/
    stage5/stage7/inbox）与旧类型/mocks/fixtures 同批退役。`change_scopes.str06`
    的 paths 重新登记到新落点（选测范围仍有真实消费者，删除条件未满足故保留并注明）。
    layering grandfather 81→6（仅剩 stage0/1 范围外边）。`tests/test_str06_product_
    entry.py` 新建：真实 HTTP 上传→autostart→agent 编排跑通 11 节点到 stage7 交付，
    零新购重放、`--check-only`、旧书 `legacy_incompatible` 拒读均有断言。已购证据
    与 F5e 原根保持只读；`contracts/schema/retirement.py` 作为历史台账保留至
    STR-06 关闭。**同批修复两处生产接缝**：直连 LLM 节点的正文外发授权与 executor
    backend 解钩（`resolve_executor_egress`／`build_argv`／`Step`／`JobStart`，否则
    编排器构造不出可执行付费新链节点的 canonical argv）；候选根完成度桥
    （Stage1 源身份匹配时 root 边即完整输入声明，空 inputs 保持 unknown）。
  - [x] **F8d · 最终工程验收**（2026-09-25 完成）：契约 §9.5 五文件、默认非
    corpus 回归及前端 test／typecheck／seams／build 全部完成。最终现役测试
    **3034 项零失败、零错误、零未说明跳过**（54 个 corpus 用例按标记剔除、未经
    真实语料执行，其载荷已全部切换到现役路由/节点）；test_store 161 项有效职责
    全部通过；F7e 红账逐项闭合（test_store 74、两个收集错误文件、schema_api 3、
    88→6 grandfather、F7a 候选根漂移）。真实产品入口全链经 fake transport 验证
    （见 F8c 的 product_entry 测试）：11 节点顺序派发、交付 manifest 最后提交、
    章回执链绑定、零新购重放字节一致、旧书拒读且 transport 零调用。冻结代码提交、
    测试证据与候选身份（`04b6fb22…` 未变）供 F9 使用。
- **F9 · 书1 正式入口成书与用户人工验收——已被 2026-09-27 职责解耦计划取代（D1），
  不再是活动批次，本项不勾选也不再执行**：成书与用户验收由 B6b 承接（F9 口径），
  工程复验由 B6a 承接；以下保留执行史、证据口径与 B6 沿用的预登记清单：
  - **2026-09-26 批次内生产变更（用户裁决）**：取消「工作树有未提交改动不能启动付费
    跑书」——`novel/orchestrator/agent.py::_require_reproducible_code_version` 去掉
    dirty 拒绝分支（unknown 仍在 probe/spawn/模型 wire/生命周期写入前拒绝），契约
    R-11.23 原位改写（R-11 条数不变），dirty 启动与 unknown 早拒的测试断言同批切换；
    缘由与代价（跑书期间编辑会滚 epoch、采用仍按请求身份核对）见 DECISIONS 2026-09-26。
    该变更触发 F9 重验义务；**用户 2026-09-26 指示本次只是局部改动，全量非 corpus
    回归推迟到合入 main 前跑**——本轮闭合证据为定向组合（test_agent／test_api_agent／
    test_prov／test_coverage／test_docs_references／test_rules_registry 共 344 项）＋
    契约 §9.5 五文件 377 项＋前端 typecheck／seams／test(115)／build，全部零失败；
    合入前须按最终提交补全量回归再合。
  - **2026-09-26 执行状态：付费跑书已启动并部分完成，按用户指示停止，验收未完成**。
    独立根 `outputs/str06_f9_product_20260926`（stage0/stage1 现行产物：11 章 9724 字、
    派生预算 10000；开头 288 字引子按 stage1 契约分离进 `hook_copy.json`
    （当时新链无消费者；2026-09-26 质量改进批次起它是 `str06.stage2.source` 的正式输入，
    ch1 回溯叙述已覆盖坦白与借种事实）。经产品 HTTP 上传（authorized＋egress_authorized，
    与前端上传按钮同形；IAB 不支持文件选择器）触发 autostart。**实付 14 次**
    （source book＋ch01–11 共 12、names 1、preserve 1），77783 prompt＋212744 completion
    tokens，全部 finish=stop、无截断无失败调用；body_design 及其后未跑。用户审阅产物后
    指示：先把各节点产物质量提高，再端到端跑书验证——**停在 `str06.stage4.body_design`
    之前**，孤儿 admission 已显式收口（证据：8765 无监听、无子进程、body_design 零产物、
    ledger 无该次调用行；run lifecycle 按 R-11.21 保持 unknown，不洗成 stopped）。
    批次内另修的生产问题：dirty 拒启动闸（用户裁决取消）与直连 LLM 节点 argv 冻结
    executor backend 旗标（F8d 遗留③，F9 实证）见上一 bullet 与 DECISIONS。
  - **已实证的合同/流水线缺口清单（质量改进批次的输入；根因大都在提示词/契约形状，
    不在模型随机性）**：① 章 wire 的 reveal recipients 出现 `char_<id>` 前缀笔误
    （ch04/ch06）——提示词只约束 participants/targets 的 id 取值面；② loop 类 effect
    漏 `key`（ch08 一处、ch10 三处）——effects 的 key 必填，loop 目标在提示词示例里
    不显著；③ names wire 的 surface_forms 与书核 surface 集「恰好相等」的校验 vs 书核
    抽取漏收（`姓沈的` 源文出现 1 次但书核未录）——核与名称结构的覆盖边界；
    ④ preserve wire 把 `must_preserve`（布尔）统一写成保全要求字符串（92/92）——提示词
    字段表未标类型；⑤ fold 记录第 6 章 loop 重复 open——loop 开闭纪律。以上均已按
    owner patch 先例机械修复（补丁 wire＋patch note 存
    `outputs/str06_f9_product_20260926/wires/`，采用记录在 run 的 `runtime/adopt_*.json`，
    原购买成本留在本根 ledger），**修复是症状级的，根因归质量改进批次**。
  - **恢复入口**：同根续跑身份未漂移（candidate workflow 钉 2a524e8b-dirty.*，
    本地已购行免费采用）；质量改进若改生产代码/契约，按 F9 规则以最终提交重验工程基线
    （全量回归＋契约五文件）后再端到端重跑；重跑建议用新独立根，不原地升级本根。
  - 在独立 `NOVEL_OUTPUT` 根经前端上传《夫君的报恩》，自动跑到 stage7；按实际节点、
    配置和可采用结果重新报价。基础约 30 次、正文额外至多 22 次、Hook 额外至多 2 次的
    口径见“成本与授权”；同请求身份已购结果只经 F7a 登记的历史采用通路复用。
  - **F7e 复验证实的采用调用配方**（F8c 已把它带进新链正式入口）：候选 run 目录按
    `novel.cli run <str06节点> <sid> --base <独立根> --check-only --reasoning-effort medium
    --wire-dir <F5e>/wires --adopt-root <F5e>/run` 重放即零新购复现交付（25 行 ledger
    全 cached、token 0、成书与 F5e 逐字一致）。三个调用凭证（`--wire-dir`／
    `--adopt-root`／`--reasoning-effort`）是 invocation 面旗标（F8c 补进 `run`
    子命令，不进 `NodeSpec.flags`）：同请求身份的已购结果经 wire 字节或源根 ledger
    行免费复用，身份不符明确阻断、不补购。两个易错点：① F5e 全部成功请求都是
    `medium` 档（唯一 `high` 档是被截断浪费的 design attempt）——档位进请求身份，
    用默认 high 会匹配到截断行或被拒；② `wires/` 只有 book／chNN／names／preserve／
    hook_design（含 patch）／naming，design／bind／hook_writer／body 走
    `--adopt-root` 的已购行——两者都要给。
  - agent 先核验章连续非空、回执链、改名零残留、manifest／hash、零新购重放、Stage 页
    与盘面一致及 ledger 成本，再请用户通读。工程缺陷修到通过，文学质量意见逐条进入
    后续迭代输入；语义未评和跨章／长篇等未验证范围随交付披露。
  - 若 F9 修改生产代码、契约或测试基础设施，按最终提交重新完成 F8d 的工程验收；无此类
    修改则复用 F8d 证据。人工验收绑定实际交付的 manifest／成书 hash；成书变化后重新取得
    用户对该版本的结论，字节未变可沿用已有阅读结论并复核身份关联。最终工程证据和用户
    验收同时有效后，一次合入 main，清账关闭 STR-06。
  - [x] **质量改进批次（2026-09-26，免费；零模型调用）**：按 F7e recheck 评估与用户指示
    （先提高各节点产物质量，再端到端跑书）实施。做法：确定性缺陷直接修；语义问题只收紧
    prompt 契约措辞与边界；**不加阶段、节点、Judge、schema 字段或义务 kind**；不让
    Preserve 读源正文。F9 缺口①–⑤已归并：①（recipients 前缀笔误）②（loop 漏 key）
    ⑤（loop 重复 open）→ B1 的字段类型与 loop 纪律；③（书核漏收 surface）→ B1 的
    surface_forms 键逐字相等＋A3 的 canonical 闸；④（must_preserve 全 true）→ B2 的
    反事实判据。三批改动：
    - **批 A（确定性接缝）**：A1 称谓释放导航按（人物, 实际释放章）分组（`book_ir._release_schedule`）；
      A2 HookContext 事实渲染保留 domain/holder/knowledge_mode（`hook_context._render_fact`）；
      A3 称谓闭合——源侧 canonical 必须是已登记 surface（`compile_book`），目标侧 Design
      放行面闭合在登记面内（`assemble_body_design` 的 `registered_surfaces`），贯穿反例证明
      放行面→替换表零残留；A4 成书按阅读序加「第N章」＋分章交付身份
      （`ASSEMBLY_FORMAT_VERSION`、`delivery_identity(chapter_digests=…)`）。
    - **批 B（语义契约，只改 prompt 文本与版本号）**：B1 Stage2 抽取纪律（自我定性记自述、
      动作不外推、knowledge_key 同命题复用、状态键不新开近义、loop 必须带 key 且不重复
      open、字段类型写清）；B2 Preserve Policy v2（七类 kind 定义同源渲染、statement 按
      kind、satisfaction 写「保住作用必须成立什么」、must_preserve 反事实判据）；
      B3 Design（章书写顺序、purpose 只概括本章 scene_ids、voice 不贴情绪标签、读者延后
      确认用 reader delta）；B4 Hook（presentation 可省略但不得改写、默认 2–4 份材料、
      局部小动作不带出新情节）；B5 写手协议 v2（世界设定精度边界＋未揭示事实不当已确认叙述）。
    - **批 C（源引子按信息职责进 Stage2）**：`build_evidence` 把 hook_copy 切成 `e00_*`
      章前证据段（只进书级调用与第 1 章）；分流规则写进 BOOK_PROMPT 与第 1 章规则；
      `source_fold` 增加两条只报告读数（开场拍重复建立初态、初态已知者又被开场拍告知）；
      `Stage1Input` 与候选根源身份改为 main_text＋hook_copy 组合摘要（`prov.combined_content_sha256`，
      store 完成度 root 边同口径）；`--source-json` 必须显式给出引子或显式声明无引子。
    - 契约与登记：新增 R-2.8／R-2.9／R-10.10，R-5.1 与 B-21 收窄，R-2 导语／R-8 导语改写；
      判据注册表 +4 条 HARD（stage2×2、stage4×1、stage7×1，stage2 进 hard_cases 扫描面），
      `docs/schema/{str06,stage1,stage2,stage4,stage7}.md` 同步；DECISIONS 2026-09-26 记四项裁决。
      **候选契约身份实测未变**（`04b6fb22…`；`_node_identity` 不含 `input_workpack` 文本），
      F7e 冻结记录无需更新——计划中「契约身份会随之变化」的预期不成立，如实登记。
    - 验证（全部免费）：str06 套件＋layering＋选测 331 项、消费面（prov/workflow/
      artifact_versions/runtime_identity/docs_references/coverage/rules_registry/
      hard_counterexamples/ingest_fixtures/orchestrator/operator/api/agent）1013 项，
      合计 1344 项零失败（JUnit 留档 `E:/_tr/quality_batch_{str06,consumers}.xml`）；
      `test_hard_counterexamples` 逐条真跑新红例；`test_str06_product_entry` 全链
      （含 e00 臂与章标记断言）通过。前端消费者同批闭合：称谓释放位置表改按
      （人物, 释放章）分组后同人物可有多条记录，`Str06MonitorScreen` 的 React key
      由 `char_id` 改为 `char_id@from_chapter`（重复 key 会让监控面丢行）；
      typecheck／seams／test(115)／build 四项全绿。
    - **代价披露**：源身份公式变化 ⇒ F9 根等既有候选根在新代码下 workflow 复校被拒
      （不沿用旧购买），端到端重跑用新独立根；章回执绑定协议版本 v2 与 policy v2 ⇒
      旧已购正文/Preserve 结论在新输入下不复用。**本批未跑任何真实 LLM**；付费重跑与
      用户验收见下一条。
  - **2026-09-26 质量改进后首轮付费验收：未通过，停在 Stage2.source/ch05**。
    基线 `d37c212a`；用户明确授权《夫君的报恩》全文及本轮产物外发至 DeepSeek 官方
    API，配置 `deepseek-flash/high`。独立根 `outputs/str06_quality_paid_20260926` 经
    正式 CLI 免费生成 Stage0/1（11 章、288 码点章前文字），实购 book＋ch01–05 共
    **6 次，29228 prompt＋98059 completion tokens**；全部 finish=stop，无 unknown
    请求。首次响应直接编译 **4/6**：book 两处 `from_chapter=0` 已免费 owner patch
    为 1 并保留原账关联；ch05 三处 recipients 的 `char_` 前缀错误复现并明确阻断。
    **语义实证**：ch03 把丈夫的行为归给表弟并生成错误 intimacy 关系，该状态已进入
    ch04 实际请求；女主自述又转成未限定为说辞的顺从状态；ch04 床下现身缺确认 reveal；
    ch05 event 已从衣柜出来但 whereabouts 仍写藏于衣柜。免费前缀复算还发现新 fold
    四条“初始已知又被告知”读数仅比较共享 e00，没有核对知情者或命题，本样本均属误报。
    H4 未达标；H1/H2/H3、其余引子样本和成书质量未验证。未继续下游付费，正式完整
    Blueprint 尚未发布，未生成 Stage4–7；未修改生产代码。原响应、修订链、原文对照与
    成本证据见该根 `付费验收报告_20260926.md` 和 `acceptance/verification.json`。
    本轮为 CLI 节点验收，未冒称 HTTP/UI 全链或用户成书验收完成；全量回归仍按前述
    用户裁决留待合入前。
  - **~~质量改进后的端到端重跑与用户验收~~——旧链口径已被 B6b/B6c 取代（D1），
    段一/段二不再按旧链执行；下列 H1–H4 验收假设与预登记阅读清单作为 B6 沿用的口径
    原样保留**（付费与正文外发分别授权；每臂独立根；stage0/1 由 CLI 从源文件免费
    生成；开跑前重新报价）：分两段——段一 `str06.stage2.source` →
    `str06.stage4.compile`（本书参照 F9 前 14 次实付口径），段二 Hook、正文 11 章、naming、
    assemble。验收假设（只判断每个 owner 是否达到预登记要求，不把改善归因到单一规则）：
    H1 Preserve 义务从「转写」转向「作用」；H2 Design 章目的与分场一致、不改写人物；
    H3 Hook presentation 不再漂移（有界实验）；H4 Stage2 消除抽取缺口且引子按 owner 正确分流。
    预登记阅读清单（判据以原文为准，F9/F7e 旧产物只作核对账）：Stage2 四条（女主的自我
    标榜记为说辞且反差两面都在；离开不外推成「送离住处」；不同次藏身用不同 knowledge_key；
    第 11 章快照无并存双人选键、无「尚未决定」残留、孕期状态已解除）；引子分流三条
    （本书 ch01 有引自 e00 的开场拍且与正文不重复、初态无由开场拍首次产生的知情；
    《三人成行》两臂基本等价；合成样本概括段零引用）；Preserve 七条（女主义务体现口头守礼
    与内心清醒的反差、失言→自曝→引起怀疑的连接、第二夜误会链、首夜床下确认时点为 reveal、
    离家半月与摊牌受阻之间不挂因果、无整场转写且 must_preserve 有真实 false、主线事件无漏保）；
    Design 四条（11 章 purpose 与 scene_ids 一致、不改写女主且 voice 不同质、床下确认拍有
    reader delta 且此前线索仍在、称谓全部落在登记面内）；Hook 一条（presentation 对照引用拍）；
    正文与交付五条（新婚夜/死结与剪子/反对换人失言/结尾四处成立、床下藏身在确认拍前未被当作
    已确认叙述、无改变时间线/身份/前史的新设定、Hook 与正文一致、新名全书一致且混合名零次、
    「第N章」齐全）。单列指标（不参与语义判定）：各付费 owner 首次输出成功率。
    原前置条件「批 2 改变请求身份故正文不复用旧结论」仍成立；批 3 已取消，Stage2 的
    新链重建与语义验证归 B2 与 B6c 对照读数。
- **质量改进批次二 · Stage2 源事实、状态生命周期与认知边界——已收口（2026-09-27），
  剩余项由职责解耦计划承接**：批 0–2 完成、批 3 取消；v13 契约（拍三类转移）是 B2
  五节点重建的起点。ch05–ch11 未购、ch05/ch06 语义核读未做、三重复未采样等旧剩余项
  不再按本批次执行——Stage2 的语义验证由 B2 的关联协议与 B6c 的 v13 对照读数承接。
    输入是首轮付费验收的
    三样本对照（F7e／F9／本轮，登记见 `docs/DECISIONS.md`）：ch03 行为主体误归、
    ch04 缺读者确认 reveal、ch05 拍末位置与 event 矛盾、loop 用 set 改写、
    recipients 的 `char_` 前缀等多版本复现问题，根因大都在契约形状与 prompt。设计
    决定 D1–D17：每拍的变化分 effects（事实）、cognition（认知）、loops（悬念）三类
    转移，各有唯一归属与生命周期；event 只是给人读的事件表述，不是连续性真值。
  - [x] **批 0 · 上下文探针与架构探针**（付费需单独授权；只改模型输入、不改契约）：
    现行 v12 契约下以 A／B／C 三臂比较第 3 章上下文形态（A 现行、B 加前文原文前缀、
    C 再加正文优先规则与快照逐条带来源），另以单次多章调用单独回答任务分解架构问题。
    工具 tools/stage2_context_probe.py：先 0 成本 preview 打印请求差异与精确
    token 数，只有 `--execute --yes` 才购买；每个样本一个独立探针根，不挂
    `--adopt-root`。预登记裁决规则已冻结（开跑前定、跑后不改）：前文上下文进入生产
    除非 B／C 编译失败率高于 A 或引入新错误类型；只有 A 失败 ≥2/4 且 B 或 C 失败
    0/3 才宣称「上下文修复了 QP-01」；架构探针须 3/3 无截断、主体正确、ch04/ch05
    两项不劣于 F7e 范式才进入生产候选讨论。
    **2026-09-26 执行结果（12 次实购，prompt 54210＋completion 194501 tokens，
    按 2026-09-26 DeepSeek 周末非高峰口径估算约 0.125 美元；本轮探针的付费与正文
    外发授权以 2026-09-26 验收轮用户对《夫君的报恩》全文＋DeepSeek 的明确授权为据）**：
    · 臂 A（现行请求，4 样本＝原响应＋3 次新购）：编译 4/4、finish 全 stop；
      **夜间行为主体误归表弟 1/4**（仅原响应 A_1），completion 11756–37216。
    · 臂 B（前文原文前缀，3 样本）：编译 3/3、主体 3/3 正确，completion 5594–10370。
    · 臂 C（B＋正文优先＋快照带来源，3 样本）：编译 3/3、主体 3/3 正确，
      completion 9292–15437。
    · 架构探针（单次 ch01–05，3 样本）：3/3 无截断、3/3 主体正确、ch04 床底现身拍
      2/3 带读者 reveal（F7e 范式为 0）；**但 2/3 未能全章编译**（arch_1 第 1 章
      双 owner 失败级联、arch_3 第 3 章因果边前向引用 b04_01），ch05 衣柜记法三样本
      不一致（1 份现身拍带 reveal、1 份 whereabouts 仍写「藏于衣柜」的 QP-06 式混值）。
    **按预登记裁决规则**：① 前文上下文进入生产（B／C 编译失败率 0/3 不高于 A 的
    0/4、无新错误类型），C 不劣于 B ⇒ 批 1C 取 C 形态；② A 失败 1/4 未达 ≥2/4 门槛，
    **如实记为「未证明上下文修复了 QP-01」**——QP-01 是概率性失败，本轮只证明新形态
    不劣于现行；③ 架构探针 2/3 不能全章编译（跨章引用完整性失败正是逐章架构按构造
    避免的问题），不进入生产候选讨论，记录后关闭。产物在
    `outputs/str06_stage2_probe_20260926/<臂>_<序号>/`（每根 runtime/ 下有原响应、
    usage 与 probe_report.json）；工具随批 0 关闭，不为后续批次扩展。
  - [x] **批 1 · 源契约、编译与投影**（免费；2026-09-26 完成，提交 e2ae770e；其后的
    R-2.12 拍内两遍解析（8011d484）与 R-2.14 登记键作用域 compiler 绑定（9729e62a）
    为同批增量修复，16 行工程验证见下方 v13 验证轮登记）：1A `contracts/schema/source.py` 按
    D1–D12 定义拍三类转移、场景边界（new／continue）与 `temporal_mode`
    （single／iterative），effect 增 `scope` 与必填 `disclosure`、op 增 `unknown`，
    登记键闭集与固定作用域（`scene_location`／`residence`／身体状态键），loops 独立
    成数组且 close 必填 resolution，Blueprint v12→v13；1B `compile_book` 的
    from_chapter 由锚点派生并加锚点字面校验，`ChapterCompiler` 生成场景 ID、绑定
    作用域、解析命题标签与引用、loop 一次性与 resolution、recipients 闭集，
    `source_fold` 重写为事实层＋认知层＋按「命题＋持有者＋模式」的知情 fold（未揭示
    concealed 只读报告、每章空转移拍比例读数），场景叠加原语上提
    `novel/stages/common.py` 供 stage4 fold 共用；1C prompt 与前文组装按 D1–D12 与
    批 0 裁决改写、证据切分细化到句并保留上下文；1D judges 新 HARD 与度量（红例＋
    绿对照）、R-2 与 schema 文档同步。
  - [x] **批 2 · 消费者闭合**（免费）：2A Preserve rules 改写且 policy_version 2→3；
    2B `project_brief` 的 stable_constraints 只取事实层；2C Design 知情词汇补
    believes；2D 写手投影新增「尚未向读者确认的事实」清单（由 compiler 从 Design
    reader delta 逐场派生）并升 renderer 与写作协议版本；2E API／UI 与前端只读展示
    按事实、认知、悬念分列；2F fake transport 全链（源 concealed＋读者揭示 → Preserve
    → Brief → Design → 写手章包）加 `test_str06_product_entry.py` 新形状 wire 到交付。
    **2026-09-26 完成（提交 c5cc88f4）**：PRESERVE_RULES v3 补写说辞/行为反差、
    concealed 揭示时点、iterative 作用与 loop 兑现四类规则；believes 进
    knowledge_mode 闭集（R-5.12，Design 三产物换 rc5、模型 contract_version 与
    候选契约身份随冻结纪律更新为 `7fb5ecfa…`）；VerificationIR 带
    pending_reader_confirmations（R-6.11，renderer v2／协议 v3，确认前列出、确认场
    标注、确认后不再列出）；身份投影反例钉住 cognition 不进 stable_constraints；
    监控屏 Blueprint 面板按事实/认知/悬念分列。验证：str06 定向 490 项、契约五文件
    393 项、API 122 项全绿，前端 typecheck/test/seams/build 全过；fake transport
    全链断言三态清单与 fold 未揭示报告为空。零模型调用。
  - [ ] **批 3 · 真实验收**（**2026-09-27 用户决定取消，不再执行**；付费需单独授权）：
    新建独立根，stage0/1 免费生成，`str06.stage2.source` 跑全书（开跑前报价并取得
    授权）；关键章 ch03–ch05 用探针工具以生产 prompt 单臂重复 3 次；离线语义核读按
    执行计划第六节分项报告（结构首次成功率、语义错误、遗漏、误报、未决项分开，不合成
    总体通过率），生产语义保持 `unver`。通过后按上述 F9 端到端计划继续段一。
    **取消范围**：ch07–ch11 不再购买、三重复不再采样、段一端到端（Preserve→Design
    →compile→正文）不再执行。已购证据面实际为 book＋ch01–ch06（取消决定前该轮又跑了
    ch05／ch06 两次，见下）；其中 ch01–ch04 的工程验证与离线语义验收（下）已交付
    登记，ch05／ch06 已购但在盘、取消前未做语义核读（ch05 completion 42515 明显
    高于均值；ch06 有一处 source_ref 修订草稿未落盘，wire 原样保留）。取消后收尾：
    F-4 修复（提交 91a04094）、探针工具及其测试按原登记删除条件删除（删除条件随取消
    触发，见 tools/README.md F8c 清理记录）、F-1/F-2/F-3 转已知读数（无付费重跑可
    验证 prompt 改动，待将来真实轮）。
    **2026-09-26 v13 验证轮（部分执行，实际停于 ch06 后）**：独立根
    `outputs/str06_v13_verify_20260926`（候选契约身份 `7fb5ecfa…`，与批 2 一致），
    stage0/1 由 CLI 免费生成；付费与外发授权以 2026-09-26 验收轮用户对《夫君的报恩》
    全文＋DeepSeek 的明确授权为据。根 ledger 记 8 条付费行：book（prompt 15804／
    completion 14268）、ch01（3778／20402）、ch02（3913／16554）、ch03 首次
    TransportError unknown（无 token）、ch03 同身份重购（4475／20369）、
    ch04（5414／22371）、ch05（6971／42515）、ch06（8274／18266）；成功 7 次合计
    prompt 48629＋completion 154745 tokens，按批 0 同口径估算约 0.10 美元；
    另有 9 条 CACHED 免费重放行（零新购）。
    · **结构现实**：book＋ch01–ch03 编译通过；ch04 响应购到但校验失败，节点停在该章
    （`workflow_status` 记四类错误）——b04_02 把 proposition 误放进 disclosure
    （契约里它在 effect 上）、b04_01/b04_04 对 book 作用域登记键 body_head_covering
    声明 scene、b04_07 cognition 引用未登记命题 p_fu_jun_hiding（前述 effect 构造
    失败未登记的级联）、因果边指向被丢弃拍。全部被闸精确拦下，按规程走 owner patch
    免费修复、不重问整份 JSON、不自动重购。
    · **契约修复依据**：ch02 b02_09 实证 D5 同拍模式（命题文本建在证实拍 cognition、
    同拍 effect 引用），旧单遍编译器会整章拒收——`_normalize` 改拍内两遍解析
    （同拍合法、跨拍悬空仍拒），提交 8011d484，R-2.12 补边界措辞。
    · **未决**：ch05–ch11 未购；ch04 owner patch 未落地；离线语义核读未做，
    生产语义保持 `unver`；ch04 两类模型错误（嵌套位置、键作用域）是否 prompt 再收紧，
    待 patch 落地后评估，不并入本提交。
    **2026-09-26 工程验证与离线语义验收（ch01–ch04 分期，免费）**：两项验证按执行
    计划第六节分项报告，**不合成总体通过率**；真实验收未完成（ch05–ch11 未购、
    ch03–ch05 三重复未采样、段一端到端未跑），批 3 勾选不开。
    · **工程验证（16 行）**：`tests/ -k "str06 or layering or context_probe"` 共
    433 项零失败零跳过（此前同选择 422 项 1 失败——探针测试未入桶，已修并提交
    6fc682c1）。逐行证据：行 1（章请求上下文）除测试（`test_arm_a_is_byte_identical_to_production_request`／`test_arm_b_only_inserts_prior_text_before_chapter_marker`／
    `test_changed_hook_copy_refuses_the_old_root_and_changes_requests`）外，补真实书
    核查——ch03 实际请求含【前文原文】（只读、截至第 2 章末、不带证据 id）、快照逐条
    带来源与正式 ID 登记（k01／loop01）、要求 7「以正文原文为准……按正文记录」在场，
    且无第 4 章及以后正文；行 2–3（场景跨章延续／同拍位置变化）
    `test_scene_segmentation_new_continue_and_cross_chapter`／
    `test_scene_scope_expires_at_new_boundary`；行 4–5（事实/认知层、知情揭示）
    `test_identity_stable_constraints_come_only_from_the_fact_layer`＋fold 对账；
    行 6（可见性）`test_fold_reports_concealed_unrevealed_at_book_end`；行 7（悬念）
    `test_loops_open_once_and_close_with_resolution`／
    `test_loop_question_text_cannot_be_rewritten_at_close`；行 8（participants）
    `test_chapter_compiler_rejects_unknown_participant_and_dual_owner`；行 9（空转读数）
    `test_fold_counts_empty_transition_beats_per_chapter`；行 10（事件粒度）iterative
    标记进 Preserve 材料由 target 测试对账；行 11（未知状态）
    `test_fold_keeps_unset_and_unknown_distinct_from_never_established`；行 12（称谓锚点）
    `test_from_chapter_is_derived_from_the_evidence_anchor`；行 13（believes）
    `test_believes_knowledge_enters_writer_view_without_becoming_world_fact`；
    行 14（写手投影）`test_pending_reader_confirmations_are_derived_per_scene`／
    `test_pending_confirmations_render_into_the_writer_packet`；行 15（版本恢复）
    `test_paid_response_durable_before_validation_and_reused`／
    `test_failed_request_is_preserved_and_never_automatically_rebought`；行 16（实际
    消费者）`test_full_chain_from_http_upload_to_stage7_delivery`＋HTTP/UI 读回
    `test_candidate_reads_over_http_match_the_views`。
    · **离线语义验收（ch01–ch04＋引子，对照源文逐拍核读；重编译免费、wire 只读）**：
    结构首次成功率：book＋ch01–ch03 首响应即编译通过（4/4）；ch04 首响应 4 类错误
    （上述）全被闸拦，owner patch（仅形状/命名 4 处修订，见该根 _patch_wires/）修复后编译
    通过（15 拍）。语义核读：① 丈夫行为未归表弟——b03_03「嫂嫂」主体为夫君、
    participants 无表弟（QP-01 未复现）；② 亲属方向正确（s0 `fu_jun→pei_shuyuan`
    cousins，事件措辞「夫君的表弟裴书远」）；③ 自述/内心/事实未互相升级——b01_05
    「处处为她想」记 believes、b04_14 未说出口的半句仅留 event；④ ch01 未补造
    「同意暂缓」——b01_10 落 `marital_decision=undecided_pending_cousin_pei_arrival`；
    ⑤ **QP-05 在真实样本兑现**——b04_02 床底藏身记成立拍＋concealed＋命题锚 k07，
    b04_07 现身拍两人 learns＋出床 on_page 效应，隐藏/得知/读者三时点分明；⑥ 悬念
    lifecycle 正确（loop01 借种主问题至 ch04 末仍 open，loop02/03 带 resolution 正常
    关闭，问题文本未改写）；⑦ 称谓表与原文核对无误（含 ch06 一次「裴知远」原文写法，
    非幻觉）；⑧ participants 无仅提名人物；⑨ 引子按 e00 分流（ch01 前三拍 e00 证据
    段，与正文不重复）。次要读数：空转移拍比例 ch01 4/10、ch02 3/9、ch03 1/11、
    ch04 8/15（ch04 偏高系对话章，逐拍核读后空拍均合法，仅 b04_06「就要外冲」的
    位置变化略可记）。
    · **本轮发现（分项，不合成通过率）**——遗漏 F-1：ch01 b01_08 夫君「有手有口定不
    叫娘子失望」是 claim，未进 cognition（应 holder=fu_jun mode=asserts 建命题），
    致 ch02 b02_09 的证实命题 k04 为新建而非引用同一命题，D5「同一命题」链断一半
    （效应链仍在）；误用 F-2：b04_13 `scene_location='去向未交代'` 用 set 描述性值，
    契约对去向未知有 unknown op；边界 F-3：b03_10 芸娘转述「夫君觉得要她亲生的好」
    记为 fu_jun 事实（第一人称转述的他意，严格可归 believes）；测试基础设施 F-5：
    探针测试未入桶（已修 6fc682c1）。**F-1/F-2/F-3 是模型语义读数：批 3 取消后没有
    付费重跑可以验证 prompt 收紧的收益，转已知读数待将来真实轮，不凭猜测改 prompt。**
    · **F-4 已修（提交 91a04094）**：同根 owner patch 首次引导不了——
    `bind_wire_adoption` 要求 `runtime/adopt_<owner>.json` 预存在（补丁 wire 的
    sha），而该记录只在 bind 成功后才写，同根自补丁死循环（ch04 补丁采用因此被拒；
    跨根历史采用不受影响）。改为 allow_patch 时记录缺失即建档（身份／成本／编译三道
    闸不变，记录存在但 sha 不符仍拒），反例
    `test_same_root_owner_patch_bootstraps_and_version_mismatch_refuses`。
    · **R-2.14 配套修复**：ch04／ch05 各 5 处把身体状态键误声明为 scene——登记键
    作用域属 I-15 可推导坐标，`ChapterCompiler._normalize` 改在 schema 校验前按表
    归一化（模型声明不符不再整章拒，canonical 恒带表内作用域）；schema 层保留兜底，
    判据红例换轨为退役键拒识，ch04 补丁的两处 scope 修订成无害空操作。契约、判据、
    hard_cases 与文档同批落地，DECISIONS 2026-09-27 一行（含代价披露：模型失误从
    「被闸拦」变「被静默归一」，语义靠 fold 读数与离线核读兜底）。
    · **明确未覆盖**：ch07–ch11 抽取（批 3 取消，不再购买）、ch05–ch06 语义核读
    （已购未读）、ch03–ch05 三重复（不采样）、段一 Preserve→Design→compile 端到端
    （不跑）、生产 Judge 已取消（语义由人工核读）。生产语义保持 `unver`；ch04 补丁
    采用链已可走通（F-4 修复后），验证根本身作为只读证据保留，补丁是否正式采用由
    用户定。

## 质量重设计批次（2026-09-28 起；R0–R7，依据 PLAN.md）

批次表与裁决见 `PLAN.md`（唯一活动计划；本节只登记交付记录）。付费与正文外发只在
B6-A／B6-B 分别授权；R 批全部 fake transport、零模型调用。

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
    语义文本中的源侧字面属 R2 renderer 目标名解析问题，非本检查对象）。当前 v4
    renderer 仍 JSON 倾倒 VerificationIR／HookContext，`sc01#b02` 式 ID、
    `established_by` 与字段名的阅读面清零以 `r2_face_pending` 登记为闭集
    （41 条），R2 WriterPacket 落地后必须清零——对应 R2 完成项「R0 守卫全绿」。
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

## 职责解耦批次（2026-09-27 起；B0–B6）

裁决 D1–D10 各行见 [DECISIONS.md](DECISIONS.md) 2026-09-27；目标架构（Stage2 五节点、
ChapterWriterPacket 三件套、执行设施与提交顺序、精确失效表）唯一登记在
[str06.md](schema/str06.md)。每批同时闭合 producer、consumer、恢复、测试与文档；
B0–B5 全部 fake transport、零模型调用，付费与正文外发只在 B6 分别授权、开跑前按
实际章数与可采用结果报价。

| 批次 | 内容 | 完成条件 |
|---|---|---|
| [x] B0 契约与文档 | 登记 2026-09-27 裁决 D1–D10（DECISIONS 各行，注明覆盖的旧行）与契约/版本/文档变更清单（节点增删与 R 条款改写归 B2–B4 执行）；候选根运行记录映射表与删除条件（见下）；各付费节点 reasoning 与正文 token 预期量级（见下）；替换 F9 与被覆盖的「本轮之后」内容；删除长篇、多线与容量增长待验项；清理质量改进批次二的残留勾选 | §9.5 五文件；文档引用检查（test_docs_references 等五文件覆盖） |
| [x] B1 执行设施 | 身份与记录对齐（SemanticRequest／ContentRevision／ProviderAttempt／AcceptanceRecord／CommitBundle，逐项映射候选根四类运行记录）、attempt 与传输重试（按是否已送达分类）、空回答救回（D5）、失败分类与恢复（str06.md 失败表）、额度（整书总闸按 attempt 计数、unknown 计入）、并发槽位（登记面本批完成；运行接入归 B4 `novel/model_slots.py` 逐章并发）、配置贯通（ExecutionProfile run 启动冻结，删执行时 `load_config()` 重读；effort 取值面与 NodeSpec 一致；`max_tokens` 131072）、provenance 显式化（删 `CandidateRunContext` 共享窗口）、CommitBundle；先写空回答回归反例（含现役 `write_chapter` 第二轮 `candidates[-1]` `IndexError` 场景）。**2026-09-27 前半已落地（fake transport，零模型调用）**：五层记录对齐工作单元账（`WorkUnitAttempt.response_sha256`、`AcceptanceRecordV1`、`CommitBundleV1`＋seal）、Runtime 耐久响应引用（`novel/llm/paid_requests.py::DurableAnswer`／`send`／`AdoptionRef`，`ExecutionError` 带 stage/node/item/phase/code/context）、传输按是否已送达分类重试（`transport._classify` 读超时＝unknown 不重试）、D5 空回答救回＋同轮重试＋空回答不占请求身份（两处现役缺陷修复：`write_chapter` IndexError、空正文 answered 落盘重跑复现）、provenance 显式引用纯函数构造＋ledger 精确 attempt 行聚合（删共享窗口与时间窗归账）、CommitBundle 封存（`runtime/commit/`，采用状态从 seal 派生）、候选根 request/validation/admission/adopt 写路退役（旧根只读保留）、整书总闸按 attempt 计数（unknown 占额、重启不重置，`--book-call-ceiling` 从同一准入账派生）；反例与完成条件证据 `tests/test_str06_runtime_records.py` 10 项＋str06/layering 定向 431 项＋契约五文件 393 项全绿。**2026-09-27 后半已落地（fake transport，零模型调用），B1 完成**：截断即停——`send` 对 `finish_reason='length'` 统一抛 `ExecutionError(code='truncated', phase='accept')`（缓存命中与历史采用同样停止，重放免费命中同一截断响应不再付费；attempt 留证、ledger 行带 finish_reason 与 reasoning/正文 token；不重试、不加预算；`write_chapter` 截断稿即停该章不重写不进候选，`R-12.2` 的截断触发重写旧处置随 D6 退役）；`max_tokens` 统一 `LLM_MAX_OUTPUT_TOKENS`＝131072（`contracts/invariants.py`）；ExecutionProfile run 启动冻结（`novel/stages/chain.py::ExecutionProfile`／`freeze_execution_profile`——`load_config()` 唯一执行期调用点是 `execute`，`assemble_paid_boundary` 删除重读通道；effort 取值面 low/high/max 与 NodeSpec 一致、CLI 移除 medium；单次运行上限从持久准入账 ceiling＋grants 派生、产品路径冻结传入付费边界、显式 `--book-call-ceiling` 优先）；并发登记改新链逐章节点（`PIPELINE_PARALLEL_NODES＝('str06.stage5.body',)`，退役键 `stage2.blueprint`/`stage4.design`/`stage4.plan`/`stage5.write` 与四个 `STAGE*_MAX_CONCURRENCY` 常量删除，`R-12.1c` 结构证据同批换轨）；停止顺序断言补强（预算竞争停止后已封存 CommitBundle 完好、不再外发）。后半证据：`tests/test_str06_runtime_records.py` 17 项、str06 契约/退役/layering/选测定向 282 项、链路与产品入口 108 项、契约五文件、付费设施与 API 114 项、CLI 与 agent 能力面 108 项全绿。**移交注记**：`str06.stage2.chapters` 未在现役注册表（B2 建节点时同步登记进 `PIPELINE_PARALLEL_NODES`）；并发槽位运行接入随 B4 逐章并发（B4 行已登记 `novel/model_slots.py`） | 故障注入全绿：空回答、截断、多 attempt、权限拒绝、未知执行、预算竞争、重启不重置、各提交窗口崩溃 |
| [ ] B2 Stage2 | 五个节点（`str06.stage2.book`／`chapters`／`names`／`associate`／`compile`，见 str06.md 节点表；`str06.stage2.source` 退役；公开节点 11→14；同步 `contracts/invariants.py::NODES`、`LLM_EXECUTOR_NODES`、`PIPELINE_PARALLEL_NODES`（B1 移交：把 `str06.stage2.chapters` 登记进并发覆盖表）、冻结记录 `tests/test_str06_f7e_freeze.py` 与候选契约身份）、观察与关联协议、Resolver、compile 双真源发布（Characters＋Blueprint 唯一 writer，`stage2/compiled/book_extraction.json` 退役）；Stage4 改读 canonical；同步根 `AGENTS.md` 与 `novel/AGENTS.md` 的节点数、`novel/stages/stage2/AGENTS.md` 的 S2-5／目录约束／现役公开面（五 owner）、`docs/BACKEND_CONTRACT.md` 节点计数行与 `web/AGENTS.md` run 屏节点数描述。**2026-09-27 前半已落地（fake transport，零模型调用）**：五节点注册面一次性改齐（`NODES` 14 节点、`LLM_EXECUTOR_NODES` 11 付费、`PIPELINE_PARALLEL_NODES` 增 `str06.stage2.chapters`、产物表 24 份——新增观察产物四件 `stage2/observations/{book,ch{nn},names,associate}.json` 并退役 `stage2/compiled/{book_extraction,input_snapshots}.json`、RC 版式升 `str06-rc6`、候选契约身份重钉 `a0f5fb5e…`）；观察与关联协议 `contracts/schema/observation.py`（`stage2-observation-v1`：book／chapters／associate／names 四观察，显式版本化 I-13；拍号/状态与约束编号/命题与 loop 正式 ID/章序不在 wire，人物 slug 留给模型；章观察只含章内本地句柄，无因果边；associate 只选已给句柄，五类配对）；Resolver `novel/stages/stage2/resolver.py`（句段 id 闭集选择、逐字锚唯一定位、missing/ambiguous/unknown/自指精确 issue，不模糊匹配不猜笔误；R-2.10 称谓锚点派生收敛为唯一实现，`compile_book._derive_from_chapter` 委托）；注册面消费表同步（`PROFILED_LLM_RUNS`、`RECOVERY_RUN_POLICIES`、`_STR06_THROUGH`、`CONSUMED_PROJECTIONS` 五节点投影、`executor_scope` 章分支、候选根初始化默认绑定改 `str06.stage2.book`）；公开规则改版 `R-2.3`（章观察互不依赖、只含章内句柄）／`R-2.10`（wire 不带窗口坐标、Resolver 落点）／`R-2.12`（章内句柄闭合、跨章同一性归 associate）／`R-2.17`（fold 只消费 canonical、无快照）；文档同步（根／novel／stage2／web AGENTS、BACKEND_CONTRACT 节点计数与 R 行、docs/schema/stage2.md 五节点协议、str06.md 实施边界）。contract 证据：`tests/test_str06_stage2_protocol.py` 20 项＋契约 §9.5 五文件 265 项＋注册表与付费设施定向（candidates/free/orchestrator_nodes/agent_capabilities/invariants/execution_permit/report/runtime_operator/llm_budget 等）全绿。**2026-09-27 第二步已落地（fake transport，零模型调用）——book／chapters 观察 owner 与接线**：`novel/stages/stage2/book.py`（`book_observation_prompt`／`compile_book_observation`：证据句段闭集、`R-2.10` 锚点可派生、`R-2.8` canonical 闭合、surface 重复、初态 known_by／目标闭合与场景作用域键拒识；`character_core_view` 人物表投影，from_chapter 走 Resolver 唯一实现，投影不含初态编号）与 `novel/stages/stage2/chapters.py`（`chapter_observation_prompt`／`chapter_prior_text`／`compile_chapter_observation`：**本章**证据闭集、退役位置键拒识与登记键作用域按表归一（R-2.14）、章内句柄纪律自含部分——同一命题 statement 冲突与同一悬念本章重复 open 拒；请求只由（本章句段，前文原文，book 观察，协议）决定，D3 互不依赖由构造签名保证）两 owner 引擎入列；`chain.py` `NODE_ORDER` 扩 14 节点序（`str06.stage2.source` runner 退役，`stage2/compiled/book_extraction.json` 写路随之停止），`run_book`／`run_chapters` 落观察产物（失败定位章 item，已存章观察保持有效），`run_names` 重挂 book 观察输入并改落 `stage2/observations/names.json`（names 引擎不变，不再写 characters.json——S2-5 compile 唯一 writer），`through='stage2'` 边界改 `str06.stage2.compile`、终态 `stage2_compiled`；`_owner_unit` 的 book→`str06.stage2.book`、chNN→`str06.stage2.chapters` 改挂（`strategy` scope 随联合抽取退役）；BOOK_PROMPT／CHAPTER_PROMPT 按新协议重写（wire 带 protocol_version、无 beat_id/因果边/快照/登记表槽；章正文标记 `【第 N 章正文】` 等路由嗅探面保持）。stage 证据：`tests/test_str06_stage2_owners.py` 17 项（含前章观察内容变化不改变后章请求、ch02 失败停在该 item 且 ch01 保持有效、缺失单元经真实付费边界＋fake transport 按请求身份免费重放零新购）＋`tests/test_str06_stage2_protocol.py` 20 项＋`test_work_units` 全绿＋`test_changed_hook_copy` 换轨五节点全绿（e00 只进 book 与 ch01、ch02 不因引子变化、组合源身份拒绝）；`tests/test_layering.py` 模块面清单同步（__init__／book／chapters／names／resolver／source）。**2026-09-27 第三步已落地（fake transport，零模型调用），B2 收口——associate／compile 与 canonical 切换**：`novel/stages/stage2/associate.py`（`project_association_space`／`associate_observation_prompt`／`compile_associate_observation`：book 与 chapters 观察投影为**已给句柄闭集及最小文本**——拍 `ch{nn}.b{序}`、命题/悬念 `ch{nn}.{本地ref}`、状态 `ch{nn}.k{occ}.{key}`／`book.k{n}.{key}`、场景 `ch{nn}.sc{序}`；wire 机械闸＝只选已给句柄（未知句柄精确拒，不猜笔误）、自指配对、同章配对非跨章、悬念句柄按 open/close 角色分闭集、跨章因果同章拒）、`novel/stages/stage2/compile.py`（`compile_source_truth`：拍号 `b{章:02d}_{序:02d}` 派发；命题 kNN／悬念 loopNN 正式 ID 按**并查集等价类**授予——同义命题配对合并、最早确立者文本为 canonical，链式配对合法；状态近义键按同维配对归一到最早确立者的 canonical key（canonical 作用域随键重绑，R-2.14）；跨章因果边落到 to 拍所在章并核对方向；续场配对否决下一章自称 new 的边界（布局核对：左端须该章末场、右端须该章首场、相邻 seam、右端不可占用）；participants／holder／target／known_by 对书级人物闭集；**未决项拒绝发布并按 item 报告**——悬空跨章引用无配对、配对类无任何 statement、时序倒转（引用晚于建立）、同维目标不一致、重复 close、close 早于 open、续场越位；源 fold 唯一实现随迁本模块（`R-2.17` 只消费 canonical transitions，`source.py` 留委托 re-export 供旧反例））两 owner 引擎与 `chain.py` 的 `run_associate`／`run_compile` runner 落地；`run_compile` 以同一结果一次成形发布 Characters＋Blueprint＋fold （来源账边记四份观察；I-25 唯一 writer、I-16），`stage2/compiled/book_extraction.json` 写路全部退役；Stage4 三腿（Preserve／BodyDesign／HookDesign）改读 canonical（人物 core 由 Characters `core_view()` 投影）。上游缺陷就地修复两处：① chapters 节点逐章产物不在完成度三账（非模板 required_artifacts／`{n}` 轮次／stage5 章账）任何一账 ⇒ operator 判定恒 run、Supervisor 对已成功节点重复派发的死循环（F8d 同形缺陷，product_entry 实测复现；`novel/orchestrator/state.py` 增 `stage2_observations_done` 账、`stage_state` 记章账、`resume_verdict` 增 chapters 分支）；② 监控面 `_chapter_files` 写死 `.txt` 看不见 `stage2/observations/ch{nn}.json`（`novel/views/str06.py` 后缀随模式解析）。换轨测试：`tests/test_str06_workflow.py` 2 例、`tests/test_str06_runtime_records.py` 购买计数 12→13（associate 新付费 owner）、`tests/test_str06_body.py`、`tests/test_str06_hook.py::_full_chain_wires`（全链 fake wires 换新协议＋associate 空配对）、`tests/test_str06_f7c_remainder.py`（丢失 characters.json 的免费补发布改挂 `str06.stage2.compile`）、`tests/test_str06_views.py`（节点数 11→14）、`tests/test_str06_product_entry.py`（ledger 节点集合与 付费节点面翻五节点）、`tests/test_layering.py` 模块面清单（＋associate／compile）；新增 `tests/test_str06_stage2_publish.py` 27 项覆盖「各批验收细节 · Stage2」逐项（同名不同人、同义命题合并、近义状态键归一、重复引文、不存在证据、错误嵌套、同拍引用、跨章因果、悬念开合、跨章续场、后文身份确认不提前改变角色知情/读者确认/初始事实、下游只凭 canonical、未决拒发按 item 报告、fold 度量读数）。**红账更新（B2 后续批次/B5 承接）**：切换期登记的全链红账全部清偿——workflow 2、runtime_records 3、f7a_wiring 2、body／hook／f7c_remainder／views／product_entry 全绿；仍红＝队列与状态语义（test_orchestrator_queue／state 23 项，source 节点的 INVALID 路由与 operator redo 语义待按五节点重写）、store 取样面（test_store 46 项随双真源发布换观察产物样例）、corpus 清单（test_corpus_manifest 12 项随 B6 线换新产物面）不变；`R-6.11` 按 DECISIONS D4 归 B3 改写 | 下方「各批验收细节 · Stage2」全部项 |
| [ ] B3 Stage4 | 协议瘦身与 compiler 生成 ID/预算、Design 新字段（跨章依赖进 TargetDesign 结构化字段）、读者确认地址（D4）、按拍读取（先读旧值后写新值）、T₀ 语义（unknown/unset/未建立与合法空值分开，F5e 垫底失败保留为回归反例）、章闭包、Hook Design 与 publish_graph／HookContext 代码归位 Stage4 包 | 下方「各批验收细节 · Stage4 章闭包」全部项。**2026-09-27 前半已落地（fake transport，零模型调用）**：四协议独立版化（`contracts/schema/workflow.py` 的 `STAGE4_PRESERVE_PROTOCOL` 等四常量＝stage4-{preserve,design,bind,hook-design}-v1；wire 顶层 `protocol_version` 逐字对账，漏带/错带即拒）；Design wire 瘦身与 compiler 派生面（`novel/stages/stage4/design.py` 重写——场景/拍 ID `sc{序:02d}`/`b{序:02d}` 按列表序派发、归章改章切片 `[{purpose, scenes:N}]`、跨章锚定 `{"scene","beat"}` 分量→`场景#拍`、`scene_weights` 相对重点数组→归一 dict、initial_state 的 scope 由 compiler 填；wire 白名单拒 ID/复合引用/归一权重键，Q-1；Preserve 义务编号由 compiler 派发＋requires 1-based 序号重写，Bind/Hook Design 的复合引用由 compiler 组成，Bind 输入改 `project_binding_space` 最小承载选择投影——不再回显 Design 全量）；TargetDesign 结构化跨章字段（`BodyDesign.book_style` 必填＋character_voices/standing_goals/ongoing_actions/open_questions/promises；锚定引用闭合 schema 构造期校验、人物闭集 assemble 校验；book_style/character_voices 投影进 BookBrief 与正文请求渲染层）；读者确认地址（D4：`TargetDelta.confirms` 必填 schema 拒、confirms≠reader、assemble 校验地址真实建立＋确认不晚于建立；`pending_reader_confirmations` 改按地址投影——只进实际消费该地址的场景，`PendingConfirmation.address` 落盘）；预算传导机械规则 `chapters_with_budget_change`；`Establishment` 构造剥 confirms 元数据（`novel/stages/stage4/compile.py` 的 establishment 构造统一剥该元数据）。RC 版式升 `str06-rc7`（Stage4 三产物 body_design/presentation_graph/writer_book_ir；freeze 重钉 `29cc19d0…`，B2 基线 `a0f5fb5e…` 入 Git 历史）；renderer v3＋写作协议 v4（确认清单带地址、新增整书风格与人物声线层）。新契约测试 `tests/test_str06_stage4_protocol.py` 15 项；既有 Stage4 面测试全部换轨（target/hook/body/workflow/execution_ir/repairs/f7c/naming/views/hard_cases/stage2_publish——其中 bind 输入投影使「导演说明变化」不再改变 bind 请求身份，test_str06_body 相应断言收窄为 4 次外发）。**2026-09-27 后半已落地（fake transport，零模型调用）**：状态读取定位到入场或具体拍——`contracts/schema/state.py::StateRead`（wire 给 1-based 拍序 `at`，`assemble_body_design._resolve_reads` 解析成场内 beat_id 并校验定位闭合），`novel/stages/common.py::scene_read_views` 按「先读旧值、后写新值」解析，是 compile 章闭包证明（`compile_execution_plan` 按阅读序折叠逐场证明已声明依赖的引用闭合，未建立/作用域失效/unknown 即 fail fast）与写手投影（`project_context`）的唯一实现，取消「本场会写该地址就删除全部入场读取」；T₀ 语义：`init_establishments` 不再按 op 过滤，unknown（显式值未知）/unset（显式解除）/set（含合法空值）/未建立分开（I-42），DESIGN_RULES 词汇分开「值未知」与「人物不知道」（后者用知情缺席或 believes 表达），F5e「unknown 初态垫底毒化 fold」失败保留为回归反例；Hook 设计与唯一 graph publisher（`novel/stages/stage4/hook.py`）与 HookContext 编译（`novel/stages/stage4/hook_context.py`）归位 Stage4 包（依赖登记 `STR06_MIGRATION_MODULE_OWNERS`/`_STAGE457`、F7e 新链落位清单、BACKEND_CONTRACT R-5.6/R-6.3/R-8.3 路径同步）；新判据 `chapter_closure_read_resolution`（R-6.13，stage4 判据 19→20，§五与 stage4.md 条数行同步）。新契约测试 `tests/test_str06_chapter_closure.py` 12 项覆盖「各批验收细节 · Stage4 章闭包」逐项（先读后写/先写后读/跨章续场/作用域失效/unknown/unset/未建立/读者确认按地址投影/精确失效三章包/权重传导）；契约 §9.5 五文件 394 项、付费设施（work_units/work_unit_grants/book_budget/execution_permit/invariants）96 项、str06 定向套件 586 项全绿（仅 test_store 6 项 B2 既有红账，HEAD 上复现）。**余项**：Hook 输入投影瘦身（hook_design 请求骨架裁剪）未做，不在本批范围清单，随后续批次；test_store 取样面 46 项红账仍归 B2 遗留登记（B5 清偿） |
| [ ] B4 Stage5/7 | 章包写手与验证（Writer/Validator 只收本章章包，D2）、并发（默认 4，经 `novel/model_slots.py`）、集合恢复（按有效提交集合补缺）、回执改绑（绑定章包摘要/请求身份/验证版本/改名前正文摘要，`R-7.4`、`R-10.3` 相应改写）、截断即停（D6）、Stage7 集合核对与按阅读序组装。**2026-09-27 前半已落地（fake transport，零模型调用），rc8**：`ChapterWriterPacket` 三件套与章级验证材料落地（`contracts/schema/book_ir.py` rc8；`compile_writer_book_ir` 必填 projection/structure，称谓许可/禁用字面 compile 时算好并随章 IR 携带，推导迁 `stage4/book_ir.py`）；章包投影/渲染 v4/摘要唯一产地 `novel/stages/common.py::project_chapter_packet`/`render_chapter_packet`/`packet_sha256`，Writer 请求不含前文正文、其他章 IR、导航与称谓总表（精确失效表可执行：某章导演说明变化只换该章章包）；章回执 rc5 改绑章包摘要+请求身份+验证版本+改名前正文摘要（`R-7.4`、`R-10.3` 已改写，`verify_chapter_receipts` 集合核对）；`run_body` 待补章经 `pipeline_model_slot` 共享书级（4）/供应商（8）总槽并发（`concurrency_for` 唯一落点，并发度不进语义身份），恢复按有效提交集合只补缺章、局部失败保留其他合法结果（`runtime/body_failures.json` 留证）、授权/账务/取消故障以原始类型关闭准入；截断即停在正文路径保持（D6）。证据：`tests/test_str06_body.py`（D2 反例、章包精确失效、并发身份不变、局部失败与集合恢复）＋str06 定向 522 项＋契约 §9.5 五文件全绿；候选契约冻结基线换轨 `d552b280…`（`test_str06_f7e_freeze.py`）。**移交注记**：新章包 prompt 量级（≤2 万）的 renderer 字节实测与 B6 读数回填未做（本批不读写语料）；`tests/test_store.py` 基线即红的 40 项旧链 store 族与 `test_names_consumes_whole_extraction_and_narrow_main_text`（消费已退役 book_extraction）移交 B5 迁移登记清理（本批已就地修绿其中 6 项：DOWNSTREAM_FAMILY 的 preserve 上游格从退役 book_extraction 换 canonical Characters）。**2026-09-28 后半已落地（fake transport，零模型调用）——Stage7 集合核对与组装**：`run_assemble` 逐章装载盘上回执与正文原样（`inspect`，不经 `_committed_set` 预过滤——预过滤只用于恢复补缺，不替 Stage7 隐瞒盘上事实），与当前编译出的章包摘要逐章核对，有效／缺失／拒绝／未知／失效（stale＝接受态回执但章包换版或正文脱钩，须重做、不是缺失）五态分开按 item 列进 `runtime/delivery/assembly_report.json`（I-42，`R-10.3` 补五态，分类与逐条问题共用 `assembly._binding_problems`）；not_ready 不落新交付、旧 manifest 原样，他章合法提交不受影响；乱序完成得到相同有序交付（第 2 章先提交的注入 transport 反例，交付字节／身份／manifest 与按序一致）；Hook 失败停在 `str06.stage5.hook` 不破坏章提交，恢复输入后全链零新购复原。证据：`tests/test_str06_body.py` 增 3 项（五态按 item 报告与免费恢复、乱序有序交付、Hook 失败恢复）＋`tests/test_str06_naming.py::test_chapter_access_separates_stale_from_missing_and_rejected`。顺带修复上游缺陷：`run_hook` 拒绝回执写路带空 refs 在付费 provenance 闸下必被 `ProvenanceRefused` 拦下、掩盖 typed 失败（现引用实际 attempt，无已应答 attempt 不伪造写盘）。**移交注记（B5）**：真实 HTTP 上传 → 自动编排 → CLI 子进程 → Repository → Stage7 交付、页面刷新与服务重启读回一致的 fake 全链属 B5 完成条件，本批未做服务／编排改造 | 下方「各批验收细节 · Stage5/7 与 Runtime」全部项 |
| [x] B5 产品与退役 | 上传、自动编排、CLI/API/UI 接入 14 节点与 attempt、失败类别、恢复状态；删除旧路径（S-05：现役 11 节点 runner 形态、候选根四类运行记录写路与 admission 平行计数等）；随删除条件清理 `tests/suite_catalog.json` 的 `change_scopes.str06` 等迁移登记。**2026-09-28 已落地（fake transport，零模型调用；API/CLI/编排与旧路径删除部分，前端归下一位工程师）**：① fake 全链（B4 移交项）——`tests/test_str06_product_entry.py::test_service_full_chain_real_subprocess_and_restart_readback`：真实 HTTP 上传→autostart→Supervisor→**真实 `run_step`／`novel.cli` 子进程**→Repository→Stage7 交付，页面刷新与**服务重启（全新 `create_app` 实例）**读回逐字节一致（fake transport 经 `sitecustomize`+PYTHONPATH 注入子进程替换 `chain.DeepSeekTransport`；`real_code_version` marker 过 run_step 真实代码身份闸；13 次付费、11 个付费节点、`--check-only` 追跑零新购、二次 `agent.start` 零派发零新购；run 面 nodes 恰为现役 str06 14 节点）。② S-05 旧路径删除——`novel/stages/stage2/source.py` 整包删除（stage2 模块面收窄至 `__init__/associate/book/chapters/compile/names/resolver`，`tests/test_layering.py` 钉住）；反例迁五 owner 引擎（`tests/hard_cases.py` stage2 九条换轨，判据 `source_book_extraction` 更名 `source_book_observation`，`docs/schema/stage2.md` 判据表同步；`tests/test_str06_source.py` 重写为五 owner 引擎的最小反例＋fold 读数＋e00 读数；`tests/test_str06_repairs.py` 换轨，D3 退役面 `chapter_input_snapshot` 反例随架构退役删除）；`contracts/schema/dependency.py` 的 `_STAGE457`/forbids 移除 `stage2.source`。③ 死代码与执行期配置重读——`_llm_client`（novel/cli/main.py）与 `novel/llm/providers.py` 的 `default_profile`/`create_client`/`create_legacy_client`/`default_legacy_backend` 删除（执行期 `load_config()` 重读路径清偿：`load_config()` 执行期唯一调用点是 `chain.execute`）；`tests/test_cli.py` 相应换轨（`test_paid_nodes_follow_the_global_default_model_without_pins` 改注册面断言、旧链 `stage2.global` 阶梯 corpus 反例随节点退役删除）。④ 旧链 runner 形态残留——`novel/orchestrator/nodes.py` 删除 `validate_run` 的旧链逐节点校验分支与 `_PRIMARY_OPERATIONS` 表（`node not in NODES` 先于一切拒绝，F8c 起不可达）；`novel/api/routes/run.py` 的 `stage2.blueprint` 恢复放行特例删除；`novel/api/routes/jobs.py` 的 prepared-recovery API 边界改为按旗标拒绝（去 `stage5.write` 旧节点限定）；`novel/llm/ledger.py` 旧链 prepared recovery 审计族（`prepared_recovery_audit_row`/`note_prepared_recovery_adoption` 等六函数）与 `novel/paths.py` 对应路径助手删除；`novel/consumption.py` 删除 stage4/5/6/7 死投影函数 26 个与 `consumed_digest`；`tools/derive_schema.py` 产物清单换观察产物面；代码注释「11 节点」残留更新为 14（`cli/main.py`/`consumption.py`/`queue.py`/`approvals.py`/`mechanical_admission.py`/`edits.py`/`invariants.py`/`workflow.py`）。⑤ 红账清偿——test_store 旧链 store 族 40 项红账清零（采样面换轨：通用事务夹具改 NameMap v5×canonical Characters 显式记账边、names 消费面用例改 `str06.stage2.names`×book 观察＋MainText 窄投影、只读 memo 用例改两级 `(book×main_text)` 对；159/159 全绿）；test_orchestrator_queue/state 136 项全绿（B2 红账已在 B3/B4 清偿，本批复核）。⑥ 迁移登记清理——`tests/suite_catalog.json` 的 `change_scopes.str06` F8c 迁移叙事移除（登记转为该链现役选测范围，路径与 tests 清单均实存）；新测试登记进 `test_str06_product_entry.py` 条目。**保留的 adapter**：`contracts/schema/corpus.py` 的 corpus 冻结坐标已随 B5 闸门清偿换新产物面（付费面 11 节点、必备产物换 B2 五节点观察面、`input_snapshots` 出无信封集合；B2 行登记的 test_corpus_manifest 12 项红账清零；B6 语料重生成批次按新坐标 build manifest）；其余存量解码面（`RETIRED_NODES`／reviewer legacy／workflow_transition）删除条件未到，随 STR-06 关闭删除。顺带修复上游缺陷：`test_store` 夹具 `_write_consumed_subject` 的 `payload=b'[]'` 与新采样面（dict 载荷）不符导致的 envelope 拒写（改 `{}` 并同步 `_book` 写两级载体）。 | 真实 HTTP 上传到 Stage7 的 fake 全链；默认非 corpus 回归＋前端 test/typecheck/seams/build（前端归下一位工程师） |
| [ ] B6 验收（付费） | B6a 按最终提交复验工程基线；B6b 新链产出《夫君的报恩》供用户通读（F9 口径）；B6c Workbench 两本 golden（见「本轮之后」与验收细节） | 下方「各批验收细节 · B6」；工程与用户验收同时有效后关闭 STR-06。**2026-09-28 D11：用户裁决 B6 前提前合入 main**（合入闸门：B5 末与收尾批后最终改动集合默认非 corpus 回归 3226 项全绿＋前端四件绿，快进合入）；B6 仍待付费与正文外发单独授权执行，B6a 基线以合入后 main 为准 |
| [x] 收尾批（2026-09-28；B5 后，终审遗留） | **S-05 尾项三项复核属实并清偿**：`novel/consumption.py` 的 `stage4_plan_blueprints`／`stage7_hook`（全仓 grep 仅命中定义行；F8c 旧链清偿后零注册位）与 `character_knowledge_reference`（曾注册旧链 `stage2.source`／`stage2.global`，B2 五节点重建后无注册位、无生产消费者；唯一引用是 `tests/test_store.py` 畸形鲁棒用例——该用例改写保留 `stage2_main_text` 现役断言，删除断死函数部分，I-52：退役路径残留测试而非现役职责反例）；陈旧注释同批更正（consumption 旧链分节头与 `stage2_hook_copy` docstring、`stage2/compile.py`「快照仍留在 source.py」、`chain.py`×2＋`store.py`×3 的「hook_copy 消费者是 Stage2.source」同句——现役消费者是 Stage2 book／names）。**两个 API 投影缺口补面（B5 前端工程师报告）**：`Str06RunView` 补失败形状直投影（`failure_kind`／`detail`；ExecutionError 另带 `error_code/stage/node/item/phase/context` 六字段，I-36）与 `runtime/body_failures.json`、`runtime/delivery/assembly_report.json`（五态按 item，I-42；缺席是 null 不冒充合法空值）；契约（`contracts/schema/api.py` 新增 5 个视图模型）、producer（`novel/views/str06.py`，纯读 inspect）、API 路由（同端点 response_model）、web（types／监控屏失败块＋章级留证＋五态报告／fixture delivered 形状）、测试（views 新增失败形状与 stale 五态反例、屏渲染失败形状）、文档（`docs/schema/_api.md` run 行、`docs/schema/str06.md` 收尾批段与 B4 后半投影注记）同批闭合 | 契约 §9.5 五文件 394 项全绿；定向：test_store＋test_layering 188 项、test_str06_views 12 项（新增 2）、test_str06_product_entry＋test_api_screens 9 项（corpus 标记按默认选择器排除）、test_api_http＋test_str06_invariants＋test_artifact_versions＋test_str06_views 81 项全绿；前端四件全绿（test 116 项／typecheck／seams 99 文件 7 基线／build）。零模型调用、不触发 golden（子批次，B5 末已跑 3224 项默认回归） |

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
- B6b（取代 F9）：经产品入口产出《夫君的报恩》；agent 先核验章连续非空、回执与集合、
  改名零残留、manifest/hash、零新购重放、Stage 页与盘面一致、ledger 成本，再请用户
  通读。工程缺陷修到通过；文学意见登记为下一轮输入，不阻塞合入。
- B6c golden：Workbench 两本短篇（《夫君的报恩》《三人成行》），按「本轮之后」六维、
  人工阅读为主；先把 `tests/golden.py::CRITERIA` 更新为六维（现为旧六项且引用已退役的
  R-9 条款）。《夫君的报恩》对照 F5e 成书，《三人成行》对照 Workbench 的 Golden Set
  v2.1 oracle。阅读项沿用 F9 预登记清单（见 F9 执行史），另加四项新链风险：转场与
  逐章重开场、跨章重复交代、跨章无名细节冲突、声线漂移。Stage2 产物与 v13 验证根
  `outputs/str06_v13_verify_20260926` 已购结果做零成本对照读数。保留全部失败与重试分母。
- 合入条件：B6a 与 B6b 同时有效；B6c 结论分别报告。机械全绿不代替语义验收。（2026-09-28 D11：main 已按用户裁决提前快进合入，本条转为 STR-06 关闭条件；B6 缺陷在 main 上修复。）

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
量级依据（只读 ledger 与采用记录，本批逐行读取核实）：v13 验证轮
`outputs/str06_v13_verify_20260926`（book 15804/14268；ch01–ch06 prompt 3778–8274、
completion 16554–42515，high effort）、F9 根 `outputs/str06_f9_product_20260926`
（names 8920/17532；preserve 18569/54928）、F7e recheck 采用记录里的 F5e 实付
（design 11952/61040、bind 36150/32146、hook_design 25656/18672、hook_writer 1571/4195、
body_ch01–ch11 prompt 54258–61050、completion 684–6563）、F6c 冒烟（names 8.6k prompt、
hook_design 17.3k/32.4k、naming 0.8k/1.9k——原始冒烟根 `outputs/str06_f6_smoke_f6_smoke_12`
已不在盘上，本批无法逐行重读；数字取自 2026-09-24 本文档 F6c 条目登记，仅与该登记
内部一致）。表值为「prompt / completion 合计（含
reasoning）」量级；DeepSeek usage 当前不单独报 reasoning（上述 ledger `reasoning_tokens`
全为 null），B1 的逐次分列按传输实际能力闭合，取不到时保持 null 不猜。

| 付费节点 | prompt 量级 | completion 量级 | 说明 |
|---|---|---|---|
| `str06.stage2.book` | 1.3万–1.6万 | 0.5万–1.5万 | v13/F9 实测；含 e00 引子证据段 |
| `str06.stage2.chapters`（每章） | 3千–9千 | 0.8万–4.5万 | completion 高值主要是 reasoning（v13 ch05 42515 为高 effort 对话章）；D3 去登记表后预期趋低 |
| `str06.stage2.names` | ~9千 | 3千–2万 | F9 8920/17532、F6c 8.6k/2.6k（effort 差异） |
| `str06.stage2.associate` | ≤1万（初始闸，无实测） | ≤1万（初始闸，无实测） | 新节点：输入为句柄与最小文本，应显著小于 book；B2 fake 闸按此校准，B6 真实读数回填后修订本表 |
| `str06.stage4.preserve` | ~1.9万 | ~5.5万 | F9 实测 18569/54928；接近旧 65536 上限，128K（D6）后仍按此量级预期，超出即回查协议 |
| `str06.stage4.body_design` | ~1.2万 | ~6万 | F5e 11952/61040；整书一次调用、最可能触上限的节点，协议瘦身（不回显输入、不写可派生字段）首要对象 |
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

## 恢复、替代与删除条件

以下是从旧待办接管的有效义务及临时 adapter 的唯一活动登记；相关历史编号不是独立主线。
删除必须先验证真实消费者、故障恢复与新入口，再清旧路径、测试专用 fixture 和引用。

| 对象／旧来源 | 关闭条件与验收归属 |
|---|---|
| 候选根四类运行记录（`runtime/request_*.json`、`runtime/validation_*.json`、`runtime/admission.json`、`runtime/adopt_*.json`）及 `runtime/body_attempts_chNN.json`／`hook_attempts.json` | 2026-09-27 已登记逐项映射到 SemanticRequest／ContentRevision／ProviderAttempt／AcceptanceRecord／CommitBundle（表见「职责解耦批次」）；B1 执行映射或删除，旧根文件只读保留作 F7a 历史采用证据；admission 平行计数（只计成功应答、不计 unknown）按删除条件拆除，不并入新账 |
|---|---|
| analyze 联合首抽及 receipt／draft repair（原 STR-03-S2-CH） | F7c 已迁反例：同请求身份只购一次、采用重放幂等零实付、已购行篡改拒绝采用与重购、未授权停 owner 零账、漏补提交免费重放、同指纹再拦旧孪生先归档（test_str06_f7c_remainder.py）；F7e 登记旧消费者，F8b 新书默认新链、F8c 核对旧消费者清零后删除。现役默认 v3 不是新链验收证据 |
| 内存 `PlanChapters` | F5 的 WriterIR 在隔离链接管；F7b/F7d 闭合 runner 与消费面，保留机械守卫职责，F8c 删除类与消费者。正式 plan_chapters 产物早已退役，只保留拒读登记 |
| 旧 Blueprint `public_state_ref` | F7c 已由新 IR／公共投影接管并迁三反例：身份隔离（participants≠揭示、未释放面独立释放不提前合并、未释放专名硬失败不披露）、跨章携入（后章／同章后场引入不向前携入、前章释放集对后章编辑稳定）、状态解除（unset 显式携带、非 stale／非静默消失／非 unknown 阻断）；F8c 随旧链删除，不能凭样例全 null 去掉职责 |
| `name_distinctiveness` | F6 的名称结构能力由 F7c 接通 Stage5 的 Book.names/name_stems、consumption 的 stage5_characters/stage7_characters、core.naming 的 name_stems/attested_surname；F8c 核对旧消费者清零后删除，不复活首字猜姓 |
| `test_mechanical_aggregate_crash_replays_exact_tail_without_wire` | F7c 迁为 binding-only 崩溃恢复反例，验证精确补尾且不重购；F8c 清除旧 skip 与替代后的旧夹具 |
| `test_a_owned_failure_stops_first_round_without_more_wire`、`test_same_fingerprint_twice_terminates_early` | F7c 迁入 Binding INVALID＋typed witness 路径，保留 owner 停止和相同指纹熔断职责；F8c 删除旧 skip，不能直接丢弃反例 |
| F4R 登记的既存跨 Stage 深导入 | 2026-09-24 补账 11 条，grandfather 总分母 77→88（stage4→blueprint evidence／source_contexts／narration、plot_design→shared.character_refs、views/stage4→stage4.plan）；F7e 逐边对账，F8c 随旧链删除清零；正式新链不得新增这些依赖 |
| `Str06Repository`／`PaidRequestSession`／`novel.stages.chain` | F7a 三步与 F7b 均已完成（候选契约前置、repository 接正式 Store 机制、付费边界接工作单元／整书总闸／唯一 ledger、`--adopt-root` 历史采用、owner runner 拆分）；F8c 已分流并删除临时 adapter，不保留两条现役主路径；付费边界的身份与记录重构归 B1（见「候选根运行记录映射与删除条件」） |
| `novel/llm/paid_requests.py`（候选运行上下文与 workflow 记录） | F7a-2 落地：provenance 铸造、采用台账、epoch 生命周期；F8a 激活候选定义后职责并回正式 `RunContext`，本模块删除 |
| `novel/work_units.py::node_lifecycle_spec` 的候选表回退 | F7a-2 落地：候选节点复用正式工作单元语义、不建第二本预算账（I-17）；F8c 候选身份进入正式 `NODES` 后删除回退，未登记节点一律 `WorkUnitRefused` |
| 迁移期 owner→节点归属表（原 `STR06_OWNER_UNITS`，随 F8c 删除） | F7a-2 过渡期归属表（owner 名是 runner 词汇）；F7b 拆 owner runner 后各 owner 自持身份，本表随之删除 |
| str06 产物模型的 `_provenance` 别名字段 | F7a-2 落地：`exclude=True` 保 prompt 字节不变；F8a 激活时随正式 schema 落定（未声明版本的 Blueprints v12 与自由形态记录的版本声明一并补齐） |
| `contracts/schema/`、`novel/stages/{stage2,stage4,stage5,stage7}/` | F7a 定义候选版本与身份，F7b–F7d 闭合真实消费，F7e 复验组合；F8a 激活注册，F8c 分流至正式包后删除迁移包 |
| `_tmp_*` harness 与旧链实现 | F7e 登记有效职责与反例，F8c 接管完成后删除代码；付费响应、checkpoint、采用证据、接受正文及质量基线保留或归档 |
| 现役 Stage6 链整层（prepare/hook/approve、产物家族、批准绑定、API/Views/前端、判据、纯 Stage6 测试） | 已登记于 `contracts/schema/retirement.py::STR06_F6D_RETIREMENT`（106 项，含接管者），`tests/test_str06_retirement.py` 双向对账；F7e 复验、F8a–F8c 按表退役，先迁有效独有反例 |
| 旧 Stage2／4／5／7 及跨层消费面 | F7e 建补充退役登记，与 Stage6 登记共同覆盖编排、consumption、core、quality、report、API/UI、schema 文档和测试；F8c 按接管证据删除，F8d 验证真实消费者 |
| Stage1 `hook_copy` | F7c 曾判定新链无消费者；**2026-09-26 质量改进批次改写**：Stage6 退役后源引子恢复唯一消费者 `str06.stage2.source`（切成 `e00_*` 章前证据段，按信息职责分流进 initial_state／第 1 章 beats，判断不了弃权），并与 main_text 共同构成组合源身份（只改引子即拒复用旧购买）。覆盖 partition/check、节点产物、审批、source_identity 与 UI 的有效职责；不留无消费者产物作为新链准入条件 |
| 书级正文篇幅、Hook 长度与章篇幅带 | F7c 从正式 MainText 派生书级篇幅、固定目标章数为源章数，Hook／篇幅带有正式配置来源；移除生产路径的 harness 默认值依赖，配置变化进入实际消费身份 |
| F5e 原输出根与已购证据 | F7a 原根只读、独立候选根显式历史采用，核对请求／响应／来源／成本及零新购同文本重放；F8c 保留或归档，F9 复用同一采用边界，不原地升级或伪造缓存命中 |
| `tests/test_store.py` 的既存 74 项失败 | 保留 161 收集、87 通过及失败分类的历史记录；F7a-2 已复核与 HEAD 基线逐项一致（49 退役路径夹具＋17 freshness 断言＋8 旧形状，均非 Store 接口相关，零新增）；F7e 登记其余接管者，F8d 有效职责全部通过；不以删除现役反例清零 |
| F6 记录的 420 项失败／错误及被忽略的两个收集错误文件 | F7e 核对原 JUnit／收集记录及与 Store 74 项的重叠，逐项绑定修复或职责接管后的退役证据；F8d 现役零失败、零错误、无未说明跳过或收集排除 |

历史响应先耐久再校验；同请求身份优先免费恢复，pending／unknown 不自动重购。
语义修复核对 owner patch 的基线身份，验证无关 canonical 内容及稳定 ID 保持；
derived 仅重算受影响 slice，不能以保护已购结果为由忽略真源写入保护。
RC4 章回执绑定改名前正文、全书 WriterIR、实际前文和请求身份；恢复核对连续有效章前缀，
planned state 仍由正式计划确定性重放，完整前文从接受正文重建。全书 IR 的 Writer 可见
变化须按新身份重验正文；第 k 章正文变化保留其前缀、后续章按实际请求重验。输入未变的
已购结果继续采用；扫描全书不等于付费重跑全书。
换 NameMap 只影响交付，Hook 窄改不使无关正文接受失效。

## 全链验收与关闭条件

必须验证（F8 汇总）：Preserve 恢复不重购；无半份 Binding；unbound 不删义务；Design
实际输入无源叙述拓扑；合法跨章布局可编译、写作、接受并交付且章数/非空约束成立；局部
修改只使真实受影响输入失效，前缀及无关已购结果保留；旧 PASS 配新正文／Context／slice
拒绝；崩溃后可重建 accepted prefix；Guard 机械反例写前阻断，失败正确归属 owner、未知
不伪装通过；生产正文 Judge 已取消，章回执不能代签语义通过。源章／目标章同号但语义
不同的样例不能错误读取开篇或推导姓名许可。
扩容后补验：HookContext 仅凭正式输入可重建，冷开／隐藏身份／多视角有明确设计来源，
只改 Hook 不使无关正文失效；命名结构、应改名覆盖、同姓／复姓／化名及合法保留有正反例；
Stage7 改名不回写 Stage5，交付可确定性重放、残留判断遵守 claimed/kept；Stage6 退役
实际到达节点、API/UI 和旧盘面拒读。旧 Prepare 坐标分离证据留在历史记录，不冒充新链验收。

写作验收：Writer 在章包（导演说明＋事实约束）边界内落实可信的欲望、阻力、选择与后果；
不读前文正文与其他章 IR（D2），衔接依赖章包内的承接与声线约束。F5e 已完成先读连续三章、
再通读全书及合读 Hook／开头／取材场景的开发性检查；B6b 用户通读正式入口的交付版本，
问题按工程缺陷与文学质量意见记录。重复、剧透、虚假悬念及语气错配等阅读证据保留；
机械篇幅披露与未执行的语义质量评估分别报告，不互相替代。HookReceipt／章级回执
只证明机械范围，语义未评如实披露；正文或检查证据缺失、未知不得据 Q-13 伪造交付成功。

切换与关闭条件：四类 truth、唯一 writer、依赖与完整消费链闭合；Preserve／Bind 隔离；
窄修复和实际消费身份生效；恢复可按有效提交集合重建；交付可重放，旧链退役登记和红账
全部闭合。B6a 工程复验加 B6b 用户验收共同构成本轮终点；人工结论绑定实际交付版本，
有效后一次合入 main。跨章重构自由度与第二本表达力在 B6c 中分别报告，不以迁移验收
冒充质量普适性；长篇永不做（D7），无相应待验项。

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

F7a 候选根与 F9 正式根均通过显式历史采用保留原请求／响应／成本关联；根之间的采用
不是 session／checkpoint 续接，F5e 原根不写入新 workflow。新协议改变全部请求身份，
历史采用在新链上不会命中，B6 全部新购。目标根续跑复用本轮身份与累计工作单元／书级
额度，重启、换 job 不重置额度。付费显式 `--yes`，正文外发分别授权，`--check-only`
表示零新购复验、可执行恢复写入，文档不得称为纯只读检查。

## 本轮之后

以下只保留范围和验收要求，STR-06 关闭后另行立项，不是并行活动计划。先进行一轮新契约
与流水线迭代优化，输入为 B6 文学质量意见清单及下列持续登记项；之后再做 Golden。

Golden（D10）：源语料固定 Workbench 两本短篇——《夫君的报恩》（开发用书，golden
独立性有限，显式披露）与《三人成行》；删除「至少一本长篇或多线结构」要求，长篇／
多线永不做（D7）。先把 `tests/golden.py::CRITERIA` 更新为六维（现为旧六项且引用已
退役的 R-9 条款）。评价沿用六维：Core Preservation、Structural Originality、Causal
Continuity、Character Continuity、Natural Progression、Local Prose Coherence；人工
阅读为主，模型评分只作辅助。冻结输入集、源版本、baseline 成书、rubric/version、
评价方式、模型辅助评审的 prompt／provider／model／参数、输出根和成本信封；评价过程
变化时重评双方现有成书，不因此重跑生成；不得临时缩减已有冻结 manifest 分母；书目在
显式 `NOVEL_DATA`／`NOVEL_CORPUS` 下核实后冻结。《夫君的报恩》对照 F5e 成书，
《三人成行》对照 Workbench 的 Golden Set v2.1 oracle；Structural Originality 以书／
场景及语义承载关系比较，不要求同号章对应；源书、新书、baseline 的证据位置分别记录；
「遵守源书阅读时序」判据随目标语义版本化改写，保留事实、因果、披露和改名质量覆盖；
未评或证据不足保持未验证。NameMap v5 跨书赋值与第二本表达力随《三人成行》检验。
Golden 不代替独立的 current-schema corpus 验收。

持续登记：漏保／过保、源结构泄漏、under-spec、状态过建模、checklist prose、
导演 brief 携带未登记事实或退化为通用形容词、章级机械检查的边界（章内揭示
位置与逐场义务兑现不可证）、过度失效、下游语义材料不足、Golden 成本；新链风险四项
（转场与逐章重开场、跨章重复交代、跨章无名细节冲突、声线漂移）归 B6 阅读项。

## 必要证据与标定知识

以下是后续批次的索引；重置只核对文档与快照，未重新读取语料或验收这些输出。
目录身份、完整 SHA 清单、原调用记录及长篇标定说明见重置前快照的“参考件清单”与
“参考知识摘要”。证据不存在或身份不符时报告未查／不可用，不补猜、也不重新付费掩盖。

| 材料／入口 | 后续用途与限制 |
|---|---|
| ~~`outputs/str06_repair_20260923/`~~ | **2026-09-26 已不在盘上**（未跟踪证据，清理临时产物时一并删除，未经 Git 保存，不可恢复）。原用途：RC2 单书修订证据（前后 SHA manifest、独立 wires、owner 采用记录、28 场 Context、JUnit／replay）。其有效职责已由现役反例承接（恢复/窄修复/回执链入口见下表 tests 行） |
| ~~`outputs/str06_f2b_smoke_20260923_162444/`~~ | **2026-09-26 已不在盘上**（未跟踪，不可恢复）。原用途：Source Truth 历史基线；当时质量披露见重置前快照 |
| ~~`outputs/str06_f3_smoke_20260923_171800/`、`outputs/str06_f4_dryrun_20260923_172702/`~~ | **2026-09-26 已不在盘上**（未跟踪，不可恢复）。原用途：RC1 Target／IR 历史基线；现行判据以 F8a 新坐标契约为准，不再核对旧形状 |
| ~~`outputs/str06_f3_smoke_20260923_165735/`~~ | **2026-09-26 已不在盘上**（未跟踪，不可恢复）。原用途：首跑 Binding 成环反例；「续跑必须同 run 目录」的教训已进 LESSONS 与候选根身份机制 |
| ~~`outputs/review_stage4_c/`~~ | **2026-09-26 已不在盘上**（未跟踪，不可恢复）。原用途：F5 的 11 条 labeled 断言与判据边界；labeled 断言已作为质量反例迁入现役测试面 |
| ~~`outputs/vnext_stage4_r1/`、`outputs/vnext_stage4_r2/`、`outputs/vnext_stage4_bbase/`、`outputs/vnext_probe_b2/`~~ | **2026-09-26 已不在盘上**（未跟踪，不可恢复）。原用途：旧成文、冻结对照及窄 patch 案例；均非当前 canonical，F8c 后无接管义务 |
| ~~`outputs/str06_f6_smoke_f6_smoke_12/`~~ | **2026-09-27 核实已不在盘上**（未跟踪，不可恢复；2026-09-26 的清理登记清单未含此根，此处补登）。原用途：F6c 付费冒烟证据（4 次实付、names／hook_design／hook_writer／naming token 读数与 Hook／NameMap 核读）。其 token 数字与质量披露只保留在本文档 F6c 条目登记（2026-09-24），此后无法逐行重读 |
| `outputs/str06_f5e_body_20260924/run` | **盘上仍在（只读）**：F5e 已完成的 RC4 隔离成书与恢复证据；F7a 起独立候选根显式历史采用并核对原字节，F9 的采用配方（`--wire-dir`/`--adopt-root`）以它为来源根 |
| `outputs/str06_f7a_candidate_20260924/`、`outputs/str06_f7b_candidate_20260924/`、`outputs/str06_f7c_candidate_20260924/`、`outputs/str06_f7e_recheck_20260925/` | **盘上仍在（只读）**：F7a–F7e 候选根与采用台账；F7e recheck 根是 F9 零新购复现配方的复验来源（25 行 ledger 全 cached、成书与 F5e 逐字一致） |
| `outputs/str06_f9_product_20260926/`、`outputs/str06_v13_verify_20260926/`、`outputs/str06_stage2_probe_20260926/`、`outputs/str06_e2e_product_20260926/` | **盘上仍在（只读）**：F9 首轮付费验收（14 次实付、停在 body_design 前、owner patch 证据与付费验收报告）、v13 验证轮（book＋ch01–ch06 逐次 token 读数、_patch_wires/ 补丁件、ch01–ch04 语义验收账）、批 0 探针产物（A/B/C 臂与架构探针、probe_report.json）、fake 全链产品入口证据；B0 token 量级表与 B6 对照读数的依据来源 |
| ~~`_tmp_vnext_guard.py`、`_tmp_vnext_judge_slim.py`、`_tmp_vnext_writer.py`、`_tmp_calib_under_spec.py`~~ | **2026-09-26 已不在盘上**（未跟踪草稿，不可恢复）。原用途：标定及反例资产；其有效结论已固化进现役判据与测试（Guard M1–M6、Judge 边界知识见下方“必须保留的知识”） |
| ~~`_tmp_hook_v3.py`、`_tmp_stage7_v3.py`~~ | **2026-09-26 已不在盘上**（未跟踪草稿，不可恢复）。原用途：F6 原型参考；原型结论已由 F6 现役实现（hook_design/hook_writer/naming/assembly）承载 |
| `tests/test_str06_contracts.py`、`tests/test_str06_invariants.py`、`tests/test_str06_fold_guard.py` | 已落的新链契约、跨产物、章级回执绑定、窄失效、交付与 Fold／Guard 反例入口 |
| `tests/test_str06_source.py`、`tests/test_str06_target.py`、`tests/test_str06_execution_ir.py`、`tests/test_str06_repairs.py`、`tests/test_str06_workflow.py` | Source／Target／IR／修复与隔离入口回归，选测按真实影响，不凭文件名宣布全链通过 |
| `tests/test_str06_body.py` | F5 按章成文链免费反例入口：章预算分配、双视图 IR 同源、renderer 分层与 lineage 剥离、机械检查全分支、重写与候选选择、回执链核对、fake transport 全链到交付＋零新购恢复＋IR 失效边界 |
| `tests/test_str06_views.py` | F7d 候选只读监控面入口：合成候选根逐面核对、半程候选根 present 分开、HTTP 与 views 逐字段一致、未知/非法候选根 404、监控读口零写盘、候选契约三条纪律对账 |
| `tests/test_str06_f7c_remainder.py` | F7c 余项反例入口：public_state_ref 接管三反例（身份隔离／跨章携入／状态解除，IR 释放位置表与公共投影）与 analyze receipt／draft repair 审计（不重复付费、不造重复实体、不漏补提交）；旧消费者退役登记归 F7e |

后续实现必须保留的知识：

- Guard M1–M6 的义务覆盖、提前揭示／效应、承载、surface 与在场职责按新坐标迁移；
  试点 bridge 规则不得原样复活，新链对应承载位置真实。Guard 零错误不证明小说质量。
- Fidelity C1–C3：相对时间精确、一般背景不得推定为个体事实、POV 知情起点明确；同句
  多违规全量枚举。旧标定为正例 3/6→6/6、labeled 11/11；新 prompt 仍须自己的验收。
- Judge 跨场回指误报通过 continuity_fact_view 补输入解决，不放宽判据；义务臂与 facts
  臂互补。slim r2 九场负例回归曾未复验，不能把旧正例分母当完成证明。
- 语义 Guard conflicts／injection／under_spec 的试点证据不支持 always-on；仅异常路径
  参考。union 可恢复的“brief 压缩误导”不属于 under_spec，不恢复源事件进入 Writer 的旧接口。
- 窄修复案例：b07_01 主客体 patch 后身份差集 s07–s10，仅付费重写 s08，其余输入未变
  继续采用；14/15 fulfilled。不能把扫描范围当重购范围，完整限制见快照。
- 旧调用曾因 16k thinking 截断两次、未剥围栏一次浪费；试点采用 max_tokens=65536 与
  围栏提取。调用参数、解析边界随当前契约核对，不能把格式错误直接升级为付费重采样。
- 旧 tb.source_refs、统一 ExactlyOne、execution_table／resolved_plan、单视图 WriterContext
  都是参考形状；以 str06.md 和当前 RC 契约为准，不为旧测试恢复废弃耦合。

## 批次推进记录规则

每批开工核对实际状态、输入身份、许可与受影响消费者；验收覆盖生成、恢复、最终盘面及
适用的 unit/contract、stage、API/UI。通过后更新本文件的状态摘要、证据位置和下一批；
未通过保留精确 node/item、失败原因及恢复入口。不得以代码存在、收集通过或机械全绿勾选质量验收。
历史长记录进入快照／Git；可复用教训按需进入 LESSONS，裁决缘由进入 DECISIONS。
B0 只修订计划与说明文档，不代表开始 B1、不扩展付费授权、不修改现役可执行契约。
