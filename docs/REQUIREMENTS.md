# STR-06 · Stage2–Stage7 终态架构迁移（Stage6 退役）

本文件是唯一活动计划，负责当前进度、下一批任务、验收、恢复与删除条件。
目标职责、真源和产物接口以 [str06.md](schema/str06.md) 为准；现役行为、根不变量与
局部指南仍按 [AGENTS.md](../AGENTS.md) 读取，历史理由见 [DECISIONS.md](DECISIONS.md)。
开工先核对工作区、实际代码和已有证据，按下列顺序推进下一未完成批次，不另造活动计划。

**2026-09-24 重置**：STR-01～STR-05 退出活动面；仍有效的旧义务已合入本计划。
完整原字节保存在 [重置前快照](archive/REQUIREMENTS_20260923_before_str06_reset.md.snapshot)，
SHA-256：`c7c7c9b2ac08de5f59e2db0556c77f491ce52b872d66b4f9caba92a86d64bccc`。
快照只供历史检索，不作为另一份活动计划；旧编号只作来源索引，不能据归档宣称任务完成。

## 目标与范围

Stage2 负责源语义；Stage4 负责目标故事、Body/Hook 呈现设计与确定性 WriterIR；Stage5
负责正文串行实现和单篇 Hook；Stage7 负责 Naming、机械 ApplyNames 与 Assembly。
四类故事语义真源保持 Characters／Blueprint／TargetDesign／SemanticBinding；NameMap
是交付变换契约，接受正文、回执与已购证据独立保护。Stage6 整层退役，不重排阶段号。

职责、权威源、单向依赖、窄修复和失败归属已冻结；字段级 schema 保持 RC。Hook 的必要
选择必须进入正式 Design 并可重建 Context；源名称结构归 Characters，目标名字归
NameMap。完整边界及目标产物布局只在 str06.md 维护，本文件不复制第二份字段契约。

**本轮终点（2026-09-24 调整）**：F7 新链接线 → F8 切换与旧链彻底退役 → F9
《夫君的报恩》经正式产品入口成书并由用户人工验收，随后一次合入 main，关闭 STR-06。
验收采用迁移口径：错章、漏章、改名残留、交付不一致、恢复失败等工程缺陷必须修到通过；
文学质量意见逐条登记为下一轮迭代优化的输入，不阻塞本轮关闭。F5e 的 agent 开发性阅读
不代替 F9 用户验收，机械全绿也不改写语义未评状态。

F0–F7 在隔离 worktree／branch 和独立输出根推进；现役链按旧契约验收。F7 的候选定义
只经显式依赖接入设施，生产入口继续使用现役注册表。F8 在分支内同批切换 I-40、`R-5.2`
等公开规则、新节点与产物注册、API/UI、正式指南；此前不提前退役 Stage6。F8 可分步提交，
合入 main 的时点为 F9 工程证据与用户验收同时有效后。
旧书保持 `legacy_incompatible`，独立根重建，不手补旧产物、不继承旧批准、不清 ledger。

F0–F7 子批次按实际修改验证隔离链、恢复与受影响消费者；契约形状稳定后补 §9.5 五文件。
定向证据闭合即可交付子批次；不因提交、改计划或登记新测试重复全仓。默认非 corpus 回归归 F8
最终候选验收；中途仅在命中根指南的完整回归门槛时提前执行，并记录具体原因。F9 若再改
生产代码、契约或测试基础设施，须按最终提交重新完成工程回归；无此类修改可复用 F8d 证据。
`tests/suite_catalog.json` 的 `change_scopes.str06` 登记隔离链选测范围，与实际导入消费者取并集；
F8 迁移包分流删除时同步删除此登记，并让正式 Stage 与其消费者承接测试范围。

本轮范围外：

- Stage0/1 未吸收的整层重构延期；Stage0 v3 回归收尾归 F4R，Stage1 输入边界联调归 F7。
- current-schema corpus 基线及其全量验收保持独立；两本 Golden 不等于“全量语料绿”。
- F9 文学质量问题驱动的迭代优化轮，在 STR-06 关闭后另行立项。
- 第二本验证与 Golden 排在迭代优化轮之后；长篇／多线容量及真实跨章重构质量的证据缺口
  随切换结论披露，不因本轮迁移验收通过而视为已验证。

### 撤销与移出

F3b 第二本 artifact-only smoke 撤销，不再作为本轮待办或付费点。第二本表达力、IR 容量
与前文增长、长篇／多线结构、NameMap v5 在非书1上的真实赋值等义务，连同两本 Golden
移交后续 Golden 轮；不重新加回 F7–F9。Golden 的样本、六维评价、冻结 manifest 与成本
要求保留，见“本轮之后”。F8 的免费反例仍须验证合法跨章布局可以编译、接受和交付。

## 当前状态与下一步

**下一批：F7c（输入、篇幅与恢复职责，免费）。F7a、F7b 已于 2026-09-24 完成
（零模型调用）。其后顺序：F7c–F7e → F8 → F9。**
F4R、F6、F5 已完成各自登记范围；F7–F9 均待实施，本次文档修订不开始迁移实现。
当前隔离链版式为 `str06-rc4`（F5 起 Design/WriterIR/章级回执新形状；Hook、NameMap
等未变形状契约仍为 rc3 标签）；下表是既有证据摘要，不是本次重新运行或整书质量结论。

| 已有阶段 | 状态与证据边界 |
|---|---|
| F0／F1／F2a | RC 契约、路径／依赖守卫、确定性 Fold／Guard、局部身份与窄修复反例已实现；F0 contract 25 项通过，详细记录见快照 |
| F2b | 《夫君的报恩》Source Semantics 冒烟：16 次付费、58.9k prompt＋164.1k completion；源 v4/v12 重载、引用及 fold 检查；当时质量披露保留在快照 |
| F3 | 单书 Preserve／Design／Bind 冒烟：5 次付费、68.7k＋104.1k tokens；69 义务、28 场、99 拍、11 章；换目录导致额外 2 次购买的教训保留 |
| F4 | 单书 RC1 planned-only 干跑：28 场、零模型调用、计划连续性可重放；不是 accepted body 或阅读验收；第二本未验证，现移交后续 Golden |
| RC2 | Source／Brief／Design／Binding／Compile／Context 的真书审阅修复与隔离入口已落；11 章、69 义务、28 场，零新增模型调用；定向 96/96、契约与路径 484/484；仅改 ob55 满足条件时仅 s27 消费身份变化 |
| F4R | 红账三项清偿（2026-09-24，零模型调用）：uploads 死引用删除后 test_store 恢复收集 161 项（HEAD 上是 collection error）；深导入 grandfather 补账 11 条（分母 77→88，stale 核对无消失边）；`_SHAPE_BREAKS` 补三条 v3 账。定向组合 485/485 绿（upload_autostart／layering／artifact_versions／stage0_revisions／stage01／stage2_characters）。test_store 87/161 绿，74 失败是修复收集后显形的既存夹具脱节（非本批引入），见 F4R 条目遗留登记 |
| F6 | Hook 链与 Stage7 终态契约＋引擎＋免费反例（2026-09-24，F6c 另付 4 次）：RC3（Characters 增 name_structure、graph 必填 hook、投影族六族、家族 +stage5/hook、stage7/naming、stage7/final）；Hook 唯一 publisher、确定性 HookContext、hook_writer 机械验＋同 Context 有限重写、NameMap v5／ApplyNames／Assembly、Stage2 names 窄调用与名称投影、迁移包依赖规则 AST 真实执行、F6d 退役登记 106 项双向对账。定向 str06＋layering 171/171、契约 §9.5 五文件 426/426；RC2 wire 在 RC3 代码下重编译：28 场 WriterContext（去版本/身份）与 replay 盘面逐字一致、checkpoint sha 不变。全量非 corpus 回归 4902 通过、420 失败/错误与 HEAD 基线逐项一致（零新增失败，全部为分支既有账，含 test_store 74 与两个既有收集错误文件，已忽略不计入）。F6c 付费冒烟已完成（见批次勾选） |
| F5a–F5d | 按章成文链免费面（2026-09-24，零模型调用）：RC4（Design 增 `realization_brief`/`purpose` 必填、双视图职责重划＋`BeatIR` 逐拍定位、`WriterBookIR`、`ChapterReceipt` 章级回执、家族 +stage4/ir、stage5/body、stage5/acceptance/body，删场景正文旧家族；生产正文 Judge 与场景回执/Fidelity 退役）；章预算 largest-remainder 确定性分配；确定性 renderer＋版本化 Writer 协议（六层阅读面、lineage 不进阅读面、完整 IR＋完整前文进请求、超限由精确 tokenizer 容量闸阻断）；`body_checks` 章级机械验（硬失败不能披露放行，只有篇幅带可披露）；按章 Writer 同输入有限重写＋确定性候选选择（偏差最小、平局取较晚）；工具层缓存先于授权、`--book-call-ceiling` 锁内准入、`--check-only`、`--body-chapter-limit`；Stage7 章回执链核对、TXT 先落盘 manifest 最后提交、全书 ±10% 偏差读数。str06 定向 156/156、契约 §9.5 五文件＋layering＋选测＋版本账全绿。真实成文与开发性阅读已由 F5e 完成，见下行 |
| F5e | 真书重跑与按章成书（2026-09-24 完成，用户授权付费）：候选 run 固定 `outputs/str06_f5e_body_20260924/run`。11 章全部提交（23 场 69 义务 Guard 零错误）、交付就绪：成书 11461 字（全书偏差 +1.63%，逐章 414–1730 字全部带内、零篇幅披露）、改名 11 处零残留、manifest 与文件 hash 一致；同根重放零新购、交付字节一致。成本实付：成功新购 23 次（design×2 含 1 次 high 档截断浪费、bind、hook_design、hook_writer、正文 16 次＝11 基础＋5 次专名释放重写；naming/names/preserve/book/chNN 经核验输入一致免费采用）＋1 次 TransportError 空行；合计约 111 万 prompt＋23 万 completion tokens。工程修正见提交 67077990（`--reasoning-effort`、T₀ 非 set 等价归一、场内自建非 entry、pov/weights/mode 归一、body_chNN owner）。**阅读验收（agent 开发性，非用户验收／非 Golden）**：楔子与 11 章通读——六维无硬伤；无机械复述状态、无逐场重开场、义务实际演出（坦白/下跪/称谓纪律/床底与房梁反转）、导演 brief 指定的展开压缩有效、楔子无提前兑现且结尾「嫂嫂，开门」与楔子悬念呼应；专名释放纪律在正文侧 5 次重写全部闭环。**限制披露**：章级机械检查不证明章内揭示位置与逐场义务兑现；正文 Judge 已取消，语义未评如实保留；单书短篇样本，长篇/多线的 IR 容量与前文增长（实测章 prompt 53.9k→60.9k tokens 线性增长）仍未验证，现移交后续 Golden；F9 用户验收尚待完成 |

RC2 修复覆盖此前“第二轮再优化”的暂缓安排，不能重新把已修问题记作待办。当前正式
编译输入是 D/G；不从 Preserve checkpoint 裸读补语义。`tools/str06_workflow.py` 取代
旧 `_tmp_str06_*` 执行 harness，旧脚本只作参考。RC1 形状不自动迁入 RC2。

仍未验证：正式入口与完整消费链、既存红账清偿后的工程回归、F9 用户对正式交付的验收。
第二本表达力与 IR 容量增长（书1 实测章级 prompt 53.9k→60.9k tokens 线性增长，未触
容量闸）、长篇／多线、NameMap v5 在非书1上的真实赋值移交后续 Golden。F6c 的 Hook
机械通过不证明不剧透、遵守材料顺序、POV 正确或吸引人；F5e 成书中的同源 Hook 连读
属于开发性阅读，不能当作用户验收或一般化质量结论。原 Prepare
消费干跑和 Hook／Stage7 付费原型只能证明当时范围；F6c 冒烟的 RC3 design 产物在 RC4
下拒读，不作为 F5e 输入。正文或章级回执不存在时，Assembly 明确未就绪，不制造成功状态。

## 批次交付与验收

F5a–F5d 已闭合按章成文的契约、引擎与免费验证，F5e 已完成隔离入口真书成文与开发性
阅读。下列 F4R／F6／F5 勾选保留原证据范围；F7–F9 待实施，F9 付费与正文外发分别授权。

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
    不提前兑现已按 str06.md 阅读项核读，F5e 已完成成书开发性阅读，用户验收归 F9，跨书质量结论归后续 Golden。真书证据
    修复一处：names wire 允许家族对象内与键一致的冗余 family_id（归一，不一致报错）。
  - [x] F6d 退役准备：`contracts/schema/str06/retirement.py` 登记 106 项（节点与编排、
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
  - [x] F5c 真实入口与恢复：`tools/str06_workflow.py` Stage4 腿完整发布 graph 与
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
    `contracts/schema/str06/candidates.py` 前置定义候选节点表（11 个节点：stage2
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
    `novel/llm/str06_run_context.py`（llm 层，迁移包纪律不动）。
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
    `novel/stages/str06/runner.py` 按候选节点表拆出 11 个 owner runner（stage2
    source／names；stage4 preserve／body_design／hook_design／bind／compile——
    Guard＋WriterBookIR＋HookContext；stage5 body／hook；stage7 naming／assemble），
    `NODE_ORDER` 即执行序，`run_node` 单节点可重跑；`build_evidence` 等 harness 逻辑
    归 stage2 source owner；`tools/str06_workflow.py` 由 587 行退为 271 行薄驱动
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
  - [ ] **F7c · 输入、篇幅与恢复职责**：Stage2 改读正式 Stage1 章数组，去掉生产路径对
    `--source-json` 的依赖；明确 Stage1 hook_copy 的新消费者或 F8 退役边界。书级正文
    篇幅目标按“保留全书篇幅边界”从源 MainText 确定性派生，目标章数等于源章数；Hook
    长度及章篇幅带有正式配置来源，与付费调用额度分开。迁移三条 Stage4 skip 的有效
    职责：Binding INVALID＋typed witness、owner 停止、同指纹熔断；覆盖 binding-only
    崩溃精确补尾和 public_state_ref 的身份隔离／跨章携入／状态解除三反例。审计 analyze
    receipt／draft repair，防重复付费、重复实体或漏补提交；名称结构接管 Stage5/7、
    consumption 与 core.naming 的 name_distinctiveness 消费。
    **验收与盘面**：正式 Stage1 输入到新链可达；输入及配置进入请求／消费身份，变化不
    误用旧结果；恢复反例通过且保留精确 owner，不把 F7a 同输入重放证明扩称为新输入可采用。
  - [ ] **F7d · API／Views／前端候选面**：纯函数 views 覆盖 Characters v4、Blueprint v12、
    graph／binding（含 Hook、导演说明、章目的）、WriterBookIR 导航、章正文与章回执
    （机械范围、语义未评、篇幅披露）、Hook 与回执、NameMap v5、成书下载与 manifest、
    Run 新节点图和 ledger 成本。前端只读监控页、types 和 fixtures 同批完成。
    **验收与盘面**：候选 API 与页面消费一致，相关测试、typecheck 和 seams 通过；现役路由
    及 Stage6 页保留至 F8，候选读面不伪装为已切换产品。
  - [ ] **F7e · 退役登记、红账对账与候选冻结**：沿用 F6d 的 Stage6 登记，为旧 Stage2／4／
    5／7、编排分支、consumption、core、quality、report 判据、views、API、前端、schema
    文档及测试建立补充登记，覆盖 88 条 grandfather 依赖。逐项写明接管者、有效反例、
    验收证据和删除条件，做双向对账。既有 420 项失败／错误、test_store 74 项及被忽略的
    两个收集错误文件按原 JUnit／收集记录核对，标明重叠、所属职责、处置批次与最终证据，
    不直接相加、不将缺失记录算作已清偿。复验 F7a 定义的 manifest／epoch／版本组合。
    **验收与盘面**：候选完整消费链、恢复和现役隔离均有证据，退役及红账登记无未归属项，
    冻结 F8 候选清单；此时不宣称旧链已退役或完整回归已通过。
- [ ] **F8 · 分支内一次切换与旧链彻底退役**（免费；可分步提交，main 在 F9 后合入一次）：
  - [ ] **F8a · 契约与公开规则翻转**：激活 F7 已验收的 ARTIFACT_SPECS、NODES／STAGES／
    RETIRED_*、判据注册表（M1–M6 按新坐标正式化）及 shape-break 账，NameMap v4→v5。
    BACKEND_CONTRACT 的 R-2～R-10 按新链改写；I-40 与 `R-5.2` 改为 N 章连续非空、允许
    跨章重构，同步 `R-5.11b`、`R-14.1` 及 R-11～R-14 中受影响条款。完成度核对当前输入、
    freshness 与有效回执，progress 不作独立业务权威。str06.md 的现役内容分流至正式
    schema 文档，stage6.md 退役；根、novel、web 等指南同批更新。
  - [ ] **F8b · 编排与服务翻转**：CLI runner、queue／state／operator／Agent supervisor／
    execution_permit／admission／delivery／upload_autostart 改走新节点，API／Views／前端
    路由同步激活；按登记删除 Stage6 分支。旧书只读识别为 `legacy_incompatible`，独立根
    重建，不继承旧批准或接受记录。
  - [ ] **F8c · 分流、删除与证据保护**：迁移包分流到正式 stage2／4／5／7 及 contracts
    schema 包，按两张登记删除已被接管的旧实现、views、routes、前端页、fixtures 和测试。
    先迁有效职责与独有反例，不缩验收分母；删除 harness、临时 adapter、`change_scopes.str06`
    和已接管的 `_tmp_*` 代码，grandfather 清零。已购响应、checkpoint、采用记录、接受正文
    及质量基线保留或归档；F5e 原证据保持只读。STR-06 关闭时 str06.md 移出现役文档，
    历史保留于 Git，引用同步迁至正式文档。
  - [ ] **F8d · 最终工程验收**：契约 §9.5 五文件、默认非 corpus 回归及前端
    test／typecheck／seams／build 全部完成。最终现役测试必须**零失败、零错误、无未说明
    跳过或收集排除**；不以“新增失败为零”替代合入门槛。F7e 的每项既有红账须修复，或在
    职责与有效反例接管后按登记退役；test_store 的有效职责全部通过。经真实产品入口
    （上传 → autostart → 编排 → stage7）用 fake transport 跑通全链，并验证零调用恢复、
    `--check-only`、旧书拒读与最终交付。冻结代码提交、测试证据和候选身份，供 F9 使用。
- [ ] **F9 · 书1 正式入口成书与用户人工验收**（本轮终点；付费与正文外发分别授权）：
  - 在独立 `NOVEL_OUTPUT` 根经前端上传《夫君的报恩》，自动跑到 stage7；按实际节点、
    配置和可采用结果重新报价。基础约 30 次、正文额外至多 22 次、Hook 额外至多 2 次的
    口径见“成本与授权”；同请求身份已购结果只经 F7a 登记的历史采用通路复用。
  - agent 先核验章连续非空、回执链、改名零残留、manifest／hash、零新购重放、Stage 页
    与盘面一致及 ledger 成本，再请用户通读。工程缺陷修到通过，文学质量意见逐条进入
    后续迭代输入；语义未评和跨章／长篇等未验证范围随交付披露。
  - 若 F9 修改生产代码、契约或测试基础设施，按最终提交重新完成 F8d 的工程验收；无此类
    修改则复用 F8d 证据。人工验收绑定实际交付的 manifest／成书 hash；成书变化后重新取得
    用户对该版本的结论，字节未变可沿用已有阅读结论并复核身份关联。最终工程证据和用户
    验收同时有效后，一次合入 main，清账关闭 STR-06。

## 恢复、替代与删除条件

以下是从旧待办接管的有效义务及临时 adapter 的唯一活动登记；相关历史编号不是独立主线。
删除必须先验证真实消费者、故障恢复与新入口，再清旧路径、测试专用 fixture 和引用。

| 对象／旧来源 | 关闭条件与验收归属 |
|---|---|
| analyze 联合首抽及 receipt／draft repair（原 STR-03-S2-CH） | F7c 迁移恢复不重复付费或造实体的反例；F7e 登记旧消费者，F8b 新书默认新链、F8c 核对旧消费者清零后删除。现役默认 v3 不是新链验收证据 |
| 内存 `PlanChapters` | F5 的 WriterIR 在隔离链接管；F7b/F7d 闭合 runner 与消费面，保留机械守卫职责，F8c 删除类与消费者。正式 plan_chapters 产物早已退役，只保留拒读登记 |
| 旧 Blueprint `public_state_ref` | F7c 由新 IR／公共投影接管公开身份与表面角色职责，迁移身份隔离、跨章携入、状态解除三类反例；F8c 随旧链删除，不能凭样例全 null 去掉职责 |
| `name_distinctiveness` | F6 的名称结构能力由 F7c 接通 Stage5 的 Book.names/name_stems、consumption 的 stage5_characters/stage7_characters、core.naming 的 name_stems/attested_surname；F8c 核对旧消费者清零后删除，不复活首字猜姓 |
| `test_mechanical_aggregate_crash_replays_exact_tail_without_wire` | F7c 迁为 binding-only 崩溃恢复反例，验证精确补尾且不重购；F8c 清除旧 skip 与替代后的旧夹具 |
| `test_a_owned_failure_stops_first_round_without_more_wire`、`test_same_fingerprint_twice_terminates_early` | F7c 迁入 Binding INVALID＋typed witness 路径，保留 owner 停止和相同指纹熔断职责；F8c 删除旧 skip，不能直接丢弃反例 |
| F4R 登记的既存跨 Stage 深导入 | 2026-09-24 补账 11 条，grandfather 总分母 77→88（stage4→blueprint evidence／source_contexts／narration、plot_design→shared.character_refs、views/stage4→stage4.plan）；F7e 逐边对账，F8c 随旧链删除清零；正式新链不得新增这些依赖 |
| `Str06Repository`／`PaidRequestSession`／`tools.str06_workflow` | F7a 第一、二步已完成：候选契约前置、repository 接正式 Store 机制、付费边界接工作单元／整书总闸／唯一 ledger；第三步历史采用待实施；F7b 拆 runner／薄驱动；F8c 分流并删除临时 adapter，不保留两条现役主路径 |
| `novel/llm/str06_run_context.py`（候选运行上下文与 workflow 记录） | F7a-2 落地：provenance 铸造、采用台账、epoch 生命周期；F8a 激活候选定义后职责并回正式 `RunContext`，本模块删除 |
| `novel/work_units.py::node_lifecycle_spec` 的候选表回退 | F7a-2 落地：候选节点复用正式工作单元语义、不建第二本预算账（I-17）；F8c 候选身份进入正式 `NODES` 后删除回退，未登记节点一律 `WorkUnitRefused` |
| `novel/llm/str06_requests.py::STR06_OWNER_UNITS` owner→节点映射 | F7a-2 过渡期归属表（owner 名是 runner 词汇）；F7b 拆 owner runner 后各 owner 自持身份，本表随之删除 |
| str06 产物模型的 `_provenance` 别名字段 | F7a-2 落地：`exclude=True` 保 prompt 字节不变；F8a 激活时随正式 schema 落定（未声明版本的 Blueprints v12 与自由形态记录的版本声明一并补齐） |
| `contracts/schema/str06/`、`novel/stages/str06/` | F7a 定义候选版本与身份，F7b–F7d 闭合真实消费，F7e 复验组合；F8a 激活注册，F8c 分流至正式包后删除迁移包 |
| `_tmp_*` harness 与旧链实现 | F7e 登记有效职责与反例，F8c 接管完成后删除代码；付费响应、checkpoint、采用证据、接受正文及质量基线保留或归档 |
| 现役 Stage6 链整层（prepare/hook/approve、产物家族、批准绑定、API/Views/前端、判据、纯 Stage6 测试） | 已登记于 `contracts/schema/str06/retirement.py::STR06_F6D_RETIREMENT`（106 项，含接管者），`tests/test_str06_retirement.py` 双向对账；F7e 复验、F8a–F8c 按表退役，先迁有效独有反例 |
| 旧 Stage2／4／5／7 及跨层消费面 | F7e 建补充退役登记，与 Stage6 登记共同覆盖编排、consumption、core、quality、report、API/UI、schema 文档和测试；F8c 按接管证据删除，F8d 验证真实消费者 |
| Stage1 `hook_copy` | F7c 明确新消费者或退役边界，覆盖 partition/check、节点产物、审批、source_identity 与 UI 的有效职责；F8 同批接线或退役，不留无消费者产物作为新链准入条件 |
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

写作验收：Writer 在导演说明边界内落实可信的欲望、阻力、选择与后果；完整已提交
前文辅助语言、动作与情绪衔接，不能从正文推导新语义状态。F5e 已完成先读连续三章、
再通读全书及合读 Hook／开头／取材场景的开发性检查；F9 用户通读正式入口的交付版本，
问题按工程缺陷与文学质量意见记录。重复、剧透、虚假悬念及语气错配等阅读证据保留；
机械篇幅披露与未执行的语义质量评估分别报告，不互相替代。HookReceipt／章级回执
只证明机械范围，语义未评如实披露；正文或检查证据缺失、未知不得据 Q-13 伪造交付成功。

切换与关闭条件：四类 truth、唯一 writer、依赖与完整消费链闭合；Preserve／Bind 隔离；
窄修复和实际消费身份生效；恢复可重建 accepted prefix；交付可重放，旧链退役登记和红账
全部闭合。F8d 工程验收加 F9 用户验收共同构成本轮终点。F9 修复后按最终提交复验工程
基线，人工结论绑定实际交付版本；有效后一次合入 main。跨章重构自由度、长篇容量与
第二本表达力在切换结论中披露“未验证，归后续 Golden 轮”，不以迁移验收冒充质量普适性。

## 成本与授权

F7／F8 免费实现与验证，F9 新购及正文外发分别授权。书1 当前 11 章的基础调用估算为
约 30 次：Stage2 13（book＋11 章＋names）、Stage4 4（Preserve／Body Design／Hook Design／
Bind）、正文 11、Hook Writer 1、Naming 1；正文额外重写每章至多 2 次，合计至多 22 次，
Hook 额外至多 2 次。修复、网络重试及未知成本分列，30 次不是承诺的新购次数或费用上限。
F5e 成功新购 23 次、约 111 万 prompt＋23 万 completion tokens 仅作历史参照，不能据此
推断本轮免费采用量；开跑前按最终节点、配置、授权及可核验的同请求历史结果重新报价。
未知历史成本不记为零，reasoning 不重复计入 completion。

F7a 候选根与 F9 正式根均通过显式历史采用保留原请求／响应／成本关联；根之间的采用
不是 session／checkpoint 续接，F5e 原根不写入新 workflow。目标根续跑复用本轮身份与
累计工作单元／书级额度，重启、换 job 不重置额度。付费显式 `--yes`，正文外发分别授权，
`--check-only` 零新购。第二本与 Golden 本轮不报价、不新增授权。

## 本轮之后

以下只保留范围和验收要求，STR-06 关闭后另行立项，不是并行活动计划。先进行一轮新契约
与流水线迭代优化，输入为 F9 文学质量问题清单及下列持续登记项；之后再做 Golden，承接
原 F3b 的第二本表达力、长篇／多线、IR／前文容量和 NameMap v5 跨书赋值验证。

Golden 冻结：输入集、源版本、baseline 成书、rubric/version、评价方式、模型辅助评审的
prompt／provider／model／参数、输出根和成本信封；沿用人工阅读验收，模型评分只作辅助；
评价过程变化时重评双方现有成书，不因此重跑生成。样本至少两本代表性书、其中至少一本
长篇或多线结构；不得临时缩减已有冻结 manifest 分母；书目在显式 `NOVEL_DATA/
NOVEL_CORPUS` 下核实后冻结（本轮未据源正文确认代表性）。Structural Originality 以
书／场景及语义承载关系比较，不要求同号章对应；源书、新书、baseline 的证据位置分别
记录；「遵守源书阅读时序」判据随目标语义版本化改写，保留事实、因果、披露和改名质量
覆盖。六维评价：Core Preservation、Structural Originality、Causal Continuity、
Character Continuity、Natural Progression、Local Prose Coherence；未评或证据不足保持
未验证。Golden 不代替独立的 current-schema corpus 验收。

持续登记：漏保／过保、源结构泄漏、under-spec、状态过建模、checklist prose、
导演 brief 携带未登记事实或退化为通用形容词、章级机械检查的边界（章内揭示
位置与逐场义务兑现不可证）、过度失效、下游语义材料不足、请求容量、Golden 成本。

## 必要证据与标定知识

以下是后续批次的索引；重置只核对文档与快照，未重新读取语料或验收这些输出。
目录身份、完整 SHA 清单、原调用记录及长篇标定说明见重置前快照的“参考件清单”与
“参考知识摘要”。证据不存在或身份不符时报告未查／不可用，不补猜、也不重新付费掩盖。

| 材料／入口 | 后续用途与限制 |
|---|---|
| `outputs/str06_repair_20260923/` | 当前 RC2 单书修订证据：前后 SHA manifest、独立 wires、owner 采用记录、28 场 Context、JUnit／replay；优先核对这一版 |
| `outputs/str06_f2b_smoke_20260923_162444/` | Source Truth 历史基线；两类当时质量披露与 RC2 修复覆盖关系见快照 |
| `outputs/str06_f3_smoke_20260923_171800/`、`outputs/str06_f4_dryrun_20260923_172702/` | RC1 Target／IR 历史基线，不能绕过 RC2 形状与实际输入身份验证 |
| `outputs/str06_f3_smoke_20260923_165735/` | 首跑 Binding 场景成环反例；续跑换目录导致额外 2 次购买，禁止照搬 |
| `outputs/review_stage4_c/` 的 taxonomy.md、labeled_assertions.json、judge_slim_report.json | F5 的 11 条 labeled 断言、误报／漏报、判据边界与原始报告；完整文件 SHA 在快照 |
| `outputs/vnext_stage4_r1/`、`outputs/vnext_stage4_r2/`、`outputs/vnext_stage4_bbase/`、`outputs/vnext_probe_b2/` | 旧成文、冻结对照及窄 patch 案例，F8c 核对有效职责与证据接管后归档；均非当前 canonical |
| `outputs/str06_f5e_body_20260924/run` | F5e 已完成的 RC4 隔离成书与恢复证据，原根只读；F7a 在独立候选根显式历史采用并核对原字节，F9 仍需正式入口与用户验收 |
| `_tmp_vnext_guard.py`、`_tmp_vnext_judge_slim.py`、`_tmp_vnext_writer.py`、`_tmp_calib_under_spec.py` | 标定及反例资产，按新 contract 改写，不恢复旧架构、不作为现役执行入口 |
| `_tmp_hook_v3.py`、`_tmp_stage7_v3.py` | F6 原型参考；先去单书假设，再证明真源／消费／恢复，不能直接转正 |
| `tests/test_str06_contracts.py`、`tests/test_str06_invariants.py`、`tests/test_str06_fold_guard.py` | 已落的新链契约、跨产物、章级回执绑定、窄失效、交付与 Fold／Guard 反例入口 |
| `tests/test_str06_source.py`、`tests/test_str06_target.py`、`tests/test_str06_execution_ir.py`、`tests/test_str06_repairs.py`、`tests/test_str06_workflow.py` | Source／Target／IR／修复与隔离入口回归，选测按真实影响，不凭文件名宣布全链通过 |
| `tests/test_str06_body.py` | F5 按章成文链免费反例入口：章预算分配、双视图 IR 同源、renderer 分层与 lineage 剥离、机械检查全分支、重写与候选选择、回执链核对、fake transport 全链到交付＋零新购恢复＋IR 失效边界 |

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
本次终点调整只修订计划与说明文档，不代表开始 F7a、不扩展付费授权、不修改现役可执行契约。
