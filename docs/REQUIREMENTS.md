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

F0–F7 在隔离 worktree／branch 和独立输出根推进；现役链按旧契约验收。F8 同批切换
I-40、`R-5.2` 等公开规则、新节点与产物注册、API/UI、正式指南；此前不提前退役 Stage6。
旧书保持 `legacy_incompatible`，独立根重建，不手补旧产物、不继承旧批准、不清 ledger。

F0–F7 子批次按实际修改验证隔离链、恢复与受影响消费者；契约形状稳定后补 §9.5 五文件。
定向证据闭合即可交付子批次；不因提交、改计划或登记新测试重复全仓。默认非 corpus 回归归 F8
最终候选验收；中途仅在命中根指南的完整回归门槛时提前执行，并记录具体原因。
`tests/suite_catalog.json` 的 `change_scopes.str06` 登记隔离链选测范围，与实际导入消费者取并集；
F8 迁移包分流删除时同步删除此登记，并让正式 Stage 与其消费者承接测试范围。

本轮范围外仅两项：

- Stage0/1 未吸收的整层重构延期；Stage0 v3 回归收尾归 F4R，Stage1 输入边界联调归 F7。
- current-schema corpus 基线及其全量验收保持独立；两本 Golden 不等于“全量语料绿”。

## 当前状态与下一步

**下一批：F5e（付费，待授权）。其后顺序：F3b → F7 → F8（F4R、F6、F5a–F5d 已全部完成）。**
当前隔离链版式为 `str06-rc4`（F5 起 Design/WriterIR/章级回执新形状；Hook、NameMap
等未变形状契约仍为 rc3 标签）；下表是既有证据摘要，不是本次重新运行或整书质量结论。

| 已有阶段 | 状态与证据边界 |
|---|---|
| F0／F1／F2a | RC 契约、路径／依赖守卫、确定性 Fold／Guard、局部身份与窄修复反例已实现；F0 contract 25 项通过，详细记录见快照 |
| F2b | 《夫君的报恩》Source Semantics 冒烟：16 次付费、58.9k prompt＋164.1k completion；源 v4/v12 重载、引用及 fold 检查；当时质量披露保留在快照 |
| F3 | 单书 Preserve／Design／Bind 冒烟：5 次付费、68.7k＋104.1k tokens；69 义务、28 场、99 拍、11 章；换目录导致额外 2 次购买的教训保留 |
| F4 | 单书 RC1 planned-only 干跑：28 场、零模型调用、计划连续性可重放；不是 accepted body 或阅读验收；第二本仍待 F3b |
| RC2 | Source／Brief／Design／Binding／Compile／Context 的真书审阅修复与隔离入口已落；11 章、69 义务、28 场，零新增模型调用；定向 96/96、契约与路径 484/484；仅改 ob55 满足条件时仅 s27 消费身份变化 |
| F4R | 红账三项清偿（2026-09-24，零模型调用）：uploads 死引用删除后 test_store 恢复收集 161 项（HEAD 上是 collection error）；深导入 grandfather 补账 11 条（分母 77→88，stale 核对无消失边）；`_SHAPE_BREAKS` 补三条 v3 账。定向组合 485/485 绿（upload_autostart／layering／artifact_versions／stage0_revisions／stage01／stage2_characters）。test_store 87/161 绿，74 失败是修复收集后显形的既存夹具脱节（非本批引入），见 F4R 条目遗留登记 |
| F6 | Hook 链与 Stage7 终态契约＋引擎＋免费反例（2026-09-24，F6c 另付 4 次）：RC3（Characters 增 name_structure、graph 必填 hook、投影族六族、家族 +stage5/hook、stage7/naming、stage7/final）；Hook 唯一 publisher、确定性 HookContext、hook_writer 机械验＋同 Context 有限重写、NameMap v5／ApplyNames／Assembly、Stage2 names 窄调用与名称投影、迁移包依赖规则 AST 真实执行、F6d 退役登记 106 项双向对账。定向 str06＋layering 171/171、契约 §9.5 五文件 426/426；RC2 wire 在 RC3 代码下重编译：28 场 WriterContext（去版本/身份）与 replay 盘面逐字一致、checkpoint sha 不变。全量非 corpus 回归 4902 通过、420 失败/错误与 HEAD 基线逐项一致（零新增失败，全部为分支既有账，含 test_store 74 与两个既有收集错误文件，已忽略不计入）。F6c 付费冒烟已完成（见批次勾选） |
| F5a–F5d | 按章成文链免费面（2026-09-24，零模型调用）：RC4（Design 增 `realization_brief`/`purpose` 必填、双视图职责重划＋`BeatIR` 逐拍定位、`WriterBookIR`、`ChapterReceipt` 章级回执、家族 +stage4/ir、stage5/body、stage5/acceptance/body，删场景正文旧家族；生产正文 Judge 与场景回执/Fidelity 退役）；章预算 largest-remainder 确定性分配；确定性 renderer＋版本化 Writer 协议（六层阅读面、lineage 不进阅读面、完整 IR＋完整前文进请求、超限由精确 tokenizer 容量闸阻断）；`body_checks` 章级机械验（硬失败不能披露放行，只有篇幅带可披露）；按章 Writer 同输入有限重写＋确定性候选选择（偏差最小、平局取较晚）；工具层缓存先于授权、`--book-call-ceiling` 锁内准入、`--check-only`、`--body-chapter-limit`；Stage7 章回执链核对、TXT 先落盘 manifest 最后提交、全书 ±10% 偏差读数。str06 定向 156/156、契约 §9.5 五文件＋layering＋选测＋版本账全绿。真实成文与阅读验收归 F5e |

RC2 修复覆盖此前“第二轮再优化”的暂缓安排，不能重新把已修问题记作待办。当前正式
编译输入是 D/G；不从 Preserve checkpoint 裸读补语义。`tools/str06_workflow.py` 取代
旧 `_tmp_str06_*` 执行 harness，旧脚本只作参考。RC1 形状不自动迁入 RC2。

仍未验证：完整工程回归、test_store 既存夹具脱节的修复、第二本表达力、真实新购 token 对比、
F5e 的 RC4 Stage4 真实设计与按章成书（导演说明/章节目的的真实语义质量、正文成文质量、
机械通过不证明章内揭示位置与逐场义务兑现）、F6c 的 Hook 真实设计与成文（机械通过不
证明不剧透、遵守材料顺序、POV 正确或吸引人）、NameMap v5 真实赋值及整书交付，及
Golden。原 Prepare 消费干跑、F6c 冒烟和 Hook／Stage7 付费原型只能证明当时范围；
F6c 冒烟的 RC3 design 产物在 RC4 下拒读，不作为 F5e 输入。正文或章级回执不存在时，
Assembly 明确未就绪，不制造成功状态。

## 批次交付与验收

F5a–F5d 已闭合按章成文的契约、引擎与免费验证（正文接受与交付待 F5e 真书验证）；
F3b 补第二本表达力与干跑，F8 仍须两本成书 Golden。下列勾选项除已完成的
F4R／F6／F5a–F5d 外都是待实施，付费点分别授权。

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
    不提前兑现已按 str06.md 阅读项核读，忠实性与开篇质量的整书结论归 F5/F8。真书证据
    修复一处：names wire 允许家族对象内与键一致的冗余 family_id（归一，不一致报错）。
  - [x] F6d 退役准备：`contracts/schema/str06/retirement.py` 登记 106 项（节点与编排、
    产物与版本账、批准绑定 s6/s6r4_/HookApprovalResolution、API/Views/前端 Stage6 面、
    判据、14 个纯 Stage6 测试文件、Stage7 现役对 stage6/hook.json 的依赖、已死残留
    stage6.semantic 与 _api.md 幽灵路由等），`test_str06_retirement.py` 双向对账：
    登记项必须真实存在、现役面（NODES/ARTIFACT_PATHS stage6 键、stage6 引擎与 schema
    文件、纯 Stage6 测试、前端 Stage6 页、/stage6 路由）必须全部在册。已退役判官不算
    新增成果。
- [ ] **F5 · 按章成文与完整交付（F5a–F5d 已于 2026-09-24 完成并提交到隔离分支，
  零模型调用；F5e 待单独授权）**：
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
  - [ ] F5e 重新执行 Stage4 与按章成书（付费，独立授权）：现有样书重跑 Stage4
    Design（故事设计与导演说明同一次请求形成，不给旧设计定点补 brief）；建立
    RC4 验收候选 run，后续生成与恢复固定该目录。基础新调用 ≈15 次（Body Design、
    Binding、Hook Design、Hook Writer 各 1＋正文 11 章；Source/Preserve/Naming
    能否免费采用先核对实际输入），正文最多另有 22 次机械重写，Hook 重写与其它
    失败另列。分步报价与授权：① 新 Stage4（检查故事设计、场景导演说明与章节
    目的，特别关注 brief 是否新增未登记事实、是否只剩通用形容词）；② Hook 与
    前三章（按实际 renderer 输入重新计量容量和费用，生成后连读）；③ 剩余正文
    与交付（核验完整盘面及零新购重放）。阅读沿用六维 rubric 并具体检查机械
    复述、每场重新开场、旁白宣布 effects、压掉关键行动、对白推进与 brief 指导
    下的展开压缩；合读 Hook＋开头＋取材场景。
- [ ] **F3b · 第二本 artifact-only smoke**：第二本 Stage2 全链＋F3 链约 15+ 次付费，
  独立授权；补 F4 第二本干跑并验证表达力多样性。选书须服务 F8 的长篇或多线覆盖，
  具体书目经显式 corpus 路径核实；artifact-only 不算第二本成书验收。
- [ ] **F7 · 工作流与公开面（免费实现）**：
  - 预建 Stage2 抽取、Stage4 Preserve／Body Design／Hook Design／Bind、Compile／Context、
    Stage5 Body／Hook、Stage7 Naming／Assembly 节点面；付费声明与 profiled executor
    双向对账，复检统一 `--check-only`，不新造 check 节点，正式注册留给 F8。
  - 接通串行与场景恢复、窄失效、付费许可、书级 budget、消费投影和机械准入；
    Str06Repository 接正式 ArtifactStore 的家族路径／provenance／freshness，
    PaidRequestSession 对齐 Q-10／Q-11 和 checkpoint 未知态，准备 adapter 分流删除。
  - 审计 analyze 历史 receipt／draft repair 的重复付费、重复实体与提交补尾；新链接管
    恢复职责后核对消费者清零，再签发 F8 删除条件。闭合 Stage5/7、consumption 与
    core.naming 的名称结构消费，不能只因 Stage7 替代就删除 name_distinctiveness。
  - API／Views／前端预建新 Characters／Blueprint、graph／binding、正文／Hook／回执、
    NameMap／下载与 Run 节点图；Stage6 页面及改选等旧面 F8 退役。闭合 manifest／epoch／
    版本组合、Stage1 引子与显式章数组边界、Golden 工具，冻结 F8 候选。
- [ ] **F8 · 验收与一次切换**：
  - 同批翻转 ARTIFACT_SPECS、节点／阶段／retired 注册、判据及 shape-break 账，
    NameMap v4→v5；旧书 legacy_incompatible 独立根重建，不迁移旧接受或批准。
  - I-40 与 `R-5.2` 同步改为 N 章连续非空、允许源情节跨章重构的目标语义；同步
    `R-5.11b`、R-9/R-10、`R-14.1` 及其它受影响条款。完成度仍核对当前输入、
    freshness 和有效回执，progress 不作独立业务权威。
  - 新链分流至 stage2／4／5／7 正式包后删除迁移包／adapter／临时入口，删除已被接管
    的旧实现、API/UI、fixtures 与测试；先迁移仍有效职责和独有反例，不缩验收分母。
    清理仅限临时代码和可重建派生物，已购响应、checkpoint、采用证据及接受正文保留或归档。
  - 契约 §9.5 五文件、默认非 corpus 回归、前端 test／typecheck／seams／build，随后
    单独报价授权执行两本 Golden；通过后一次合入、更新正式指南并清账关闭 STR-06。

## 恢复、替代与删除条件

以下是从旧待办接管的有效义务及临时 adapter 的唯一活动登记；相关历史编号不是独立主线。
删除必须先验证真实消费者、故障恢复与新入口，再清旧路径、测试专用 fixture 和引用。

| 对象／旧来源 | 关闭条件与验收归属 |
|---|---|
| analyze 联合首抽及 receipt／draft repair（原 STR-03-S2-CH） | F7 证明新书默认新 authoritative 链、旧消费者清零、恢复不重复付费或造实体；F8 勾选条件后删除。现役默认 v3 不是新链验收证据 |
| 内存 `PlanChapters` | F5 的 WriterIR 全量接管，Stage5 不再消费旧 plan 形；保留机械守卫职责，F8 删除类与消费者。正式 plan_chapters 产物早已退役，只保留拒读登记 |
| 旧 Blueprint `public_state_ref` | F5/F7 由新 IR／公共投影接管公开身份与表面角色职责；迁移身份隔离、跨章携入、状态解除三类反例后，F8 随旧链删除，不能凭样例全 null 直接去掉职责 |
| `name_distinctiveness` | F6/F7 新名称结构接管有效职责；Stage5 的 Book.names/name_stems、consumption 的 stage5_characters/stage7_characters、core.naming 的 name_stems/attested_surname 消费全部清零，F8 再删；不复活首字猜姓 |
| `test_mechanical_aggregate_crash_replays_exact_tail_without_wire` | F5 迁为 binding-only 崩溃恢复反例，验证精确补尾且不重购；F8 清除旧 skip 与替代后的旧夹具 |
| `test_a_owned_failure_stops_first_round_without_more_wire`、`test_same_fingerprint_twice_terminates_early` | F5 迁入 Binding INVALID＋typed witness 路径，保留 owner 停止和相同指纹熔断职责；F8 删除 skip，不能直接丢弃反例 |
| F4R 登记的既存跨 Stage 深导入 | 2026-09-24 补账 11 条，grandfather 总分母 77→88（stage4→blueprint evidence／source_contexts／narration、plot_design→shared.character_refs、views/stage4→stage4.plan）；保留准确分母和对应消费者；正式新链不得新增这些依赖，F8 随旧链整层删除时同步清零 |
| `Str06Repository`／`PaidRequestSession`／`tools.str06_workflow` | F7 接正式 Store、执行许可、成本及恢复边界；F8 分流／删除，不注册两条现役主路径 |
| `contracts/schema/str06/`、`novel/stages/str06/` | F7 目标形状与真实消费闭合；F8 分流至正式 Stage 包，版本／manifest／epoch／注册表同步后删除迁移包 |
| `_tmp_*` harness 与旧链实现 | 有效职责与反例已迁移后删除代码；付费响应、checkpoint、采用证据、接受正文及质量基线保留或归档，不能当缓存清掉 |
| 现役 Stage6 链整层（prepare/hook/approve、产物家族、批准绑定、API/Views/前端、判据、纯 Stage6 测试） | 唯一登记在 `contracts/schema/str06/retirement.py::STR06_F6D_RETIREMENT`（106 项，含接管者），`tests/test_str06_retirement.py` 双向对账守分母；F8 一次合入按表执行，先迁仍有效的独有反例，不缩分母 |

历史响应先耐久再校验；同请求身份优先免费恢复，pending／unknown 不自动重购。
语义修复核对 owner patch 的基线身份，验证无关 canonical 内容及稳定 ID 保持；
derived 仅重算受影响 slice，不能以保护已购结果为由忽略真源写入保护。
接受回执绑定改名前正文和本场实际消费身份；从连续有效 accepted prefix 重放 planned_delta，
有限 prose tail 也由接受正文重建。输入未变的已购结果继续采用；扫描全书不等于付费重跑全书。
换 NameMap 只影响交付，Hook 窄改不使无关正文接受失效。

## 全链验收、Golden 与授权

必须验证（F8 汇总）：Preserve 恢复不重购；无半份 Binding；unbound 不删义务；Design
实际输入无源叙述拓扑；合法跨章布局可编译、写作、接受并交付且章数/非空约束成立；局部
修改只使真实受影响输入失效，前缀及无关已购结果保留；旧 PASS 配新正文／Context／slice
拒绝；崩溃后可重建 accepted prefix；Guard 机械反例写前阻断；Judge 错误正确归属、未知
不伪装通过；源章／目标章同号但语义不同的样例不能错误读取开篇或推导姓名许可。
扩容后补验：HookContext 仅凭正式输入可重建，冷开／隐藏身份／多视角有明确设计来源，
只改 Hook 不使无关正文失效；命名结构、应改名覆盖、同姓／复姓／化名及合法保留有正反例；
Stage7 改名不回写 Stage5，交付可确定性重放、残留判断遵守 claimed/kept；Stage6 退役
实际到达节点、API/UI 和旧盘面拒读。旧 Prepare 坐标分离证据留在历史记录，不冒充新链验收。

写作验收：Writer 在导演说明边界内落实可信的欲望、阻力、选择与后果；完整已提交
前文辅助语言、动作与情绪衔接，不能从正文推导新语义状态。F5e 先读连续三章，
完成全书后合读 Hook、开头和取材场景，检查重复、剧透、虚假悬念及语气错配；
机械篇幅披露与未执行的语义质量评估分别报告，不互相替代。HookReceipt／章级回执
只证明机械范围，语义未评如实披露；正文或检查证据缺失、未知不得据 Q-13 伪造交付成功。

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

Cutover 条件：四类 truth、唯一 writer、依赖与完整消费链闭合；Preserve／Bind 隔离；
窄修复和场景身份真实生效；恢复可重建 accepted prefix；交付可重放；工程回归及冻结过程
下的逐书 Golden 验收通过。未实际验证的跨章质量范围必须随验收结论披露。

成本与风险：基础调用估算 = Stage2 实际抽取 + Preserve 1 + Body Design 1 +
Hook Design 1 + Bind 1 + 目标章数（按章正文）+ Hook Writer 1 + Naming 1；正文
机械重写每章至多 2 次另列；修复、重试及未知成本分列，不能用书1的章数估算任意长篇。
F6c（约 3–5，已完成 4）、F5e（RC4 书1基础 ≈15＋正文重写至多 22）、F3b（约 15+）
及 F8 Golden 分别授权，Golden 按最终节点与样本报价；运行级新调用另受显式
`--book-call-ceiling` 约束，续跑固定同一 run 目录。
F4 与早期消费投影干跑为零模型调用。付费显式 `--yes`，正文外发分别授权，
`--check-only` 零新购。
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
| `outputs/vnext_stage4_r1/`、`outputs/vnext_stage4_r2/`、`outputs/vnext_stage4_bbase/`、`outputs/vnext_probe_b2/` | 旧成文、冻结对照及窄 patch 案例，保留至 F5 职责接管；均非当前 canonical |
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
本次重置只做文档整理，不代表开始 F4R、不扩展付费授权、不修改现役可执行契约。
