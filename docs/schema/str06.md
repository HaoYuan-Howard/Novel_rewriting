# STR-06 终态架构 RC 契约（Stage2–Stage7，Stage6 退役）

本文件登记当前 RC4 实装与 Stage2–Stage7 扩容目标：四类 truth、Hook、按章正文、
交付改名、路径与消费边界、阅读验收及 F8 切换／F9 合入条件。下文明确区分已实现契约和
待实施目标；Stage6 整层退役是目标，不表示现役链已经切换。
批次进度、必要证据索引与历史快照入口见 `docs/REQUIREMENTS.md` STR-06；架构裁决见
`docs/DECISIONS.md` 2026-09-23／2026-09-24。

**RC 边界**：代码落点为 `contracts/schema/` 子包。字段级 schema 保持 RC，
经反例与真书验证后定版。本子包**不进** `ARTIFACT_SPECS`、不注册
`NODES`、不进判据注册表；现役链按旧契约验收。F7 通过显式候选契约接入正式设施，
F8 在分支内切换并退役旧链，F9 书1 经正式入口成书、工程核验与用户人工验收通过后
一次合入 main。Golden 排在 STR-06 关闭后的迭代优化轮之后，不属于本轮。
当前代码版式身份为 `str06-rc4`（F5，2026-09-24）：Design 增导演说明与章节目的、
双层 WriterIR、按章成文与章级回执、`stage4/ir`、`stage5/body`、
`stage5/acceptance/body` 路径家族已实装，F5e 隔离入口真书重跑与开发性阅读已完成；
这不表示 F7 正式接线、F8 切换或 F9 用户验收已经完成。
Hook（`hook.py`）、NameMap v5、名称结构等未改变形状的契约**不**为统一标签
机械升版，`contract_version` 仍为 `str06-rc3`。后续破坏形状须显式修订 RC 身份，
不能靠文档更新默许旧载荷。RC1/RC2/RC3 目标产物不能直接加载：旧 RC3 Design 缺
`realization_brief`／`purpose`，明确拒读——不用空字符串兜底，也不由 compiler
补写迁移；旧设计、正文、回执、付费响应及 F6 证据只读保留，只能逐 owner 显式
审阅 wire 后免费重编译（旧 wire 不得拷贝成伪造缓存命中）。

## 当前 RC 实装、修订与免费恢复

2026-09-23 从 F2b/F3/F4 真书结果反推修订。架构仍为 Source → Preserve → Brief →
Design → Bind → Compile/Context；字段变化不接入现役注册表，正式切换仍归 F8。

| 边界 | RC2 可执行要求 |
|---|---|
| Brief | `requirements: BriefRequirement[]` 无损保留所有 kind、must、statement、satisfaction；确定性不透明 ID 排序，依赖闭合；无源坐标或源数组顺序 |
| Binding | 携带采用的完整 `obligations`；每条单一 `beat_ref` 最终验收拍，可有严格较早的 `supporting_beat_refs`；unbound 必保阻止编译，但义务不删除 |
| Design | 必填 `initial_state` 与每场 `state_reads`；`TargetDelta` 显式 domain/key/subjects/holder/op/value/scope/knowledge_mode；称谓由 initial_surfaces 和逐拍 surface_introductions 释放 |
| 状态 | character 一个对象，relation 两个有序对象，knowledge 有真实 holder 与 known/suspected/heard 模式；set/unset/unknown/未建立分离；scene 覆盖退出后恢复 book 底值 |
| Compile | 只消费 D/G，初始态来自 D，义务来自 G；检查完整分母、真实拍、前置与支撑拍序、DAG；实际状态读取亦生成依赖边；集合排序固定 |
| Context | IR 携带完整义务与满足条件；WriterBrief 携带实质要求、POV、avoid、逐拍提示、局部 entry、明确前场支撑；不累计复制整书历史义务 |
| 消费身份 | 绑定本场 D/G 内容、计划、状态、称谓、预算、完整义务语义和支撑拍；全书 checkpoint hash 与 attempt 不进入 slice；无关场内容变化不扩大失效 |
| Source | 章末快照由 chapter_input_snapshot 严格转为下一章输入；所有证据/人物/recipient 引用闭合；相邻因果保留，坏边报错；validate 不改变跨章注册表 |

`novel/stages/chain.py` 是隔离链的真实入口。`--source-json` 接收显式的连续源章数组
`[{chapter_ordinal,text}]`；不读取隐藏旧 Stage1 路径。`NOVEL_OUTPUT` 必填，`--run-dir`
必须位于其中且续跑固定使用同一根。`--wire-dir` 免费采用逐 owner 的 `book_wire.json`、
`chNN_wire.json`、`preserve_wire.json`、`design_wire.json`、`bind_wire.json`，每份记录字节摘要
及校验结果。`--repair-manifest` 核对修订前后文件 SHA，变化则拒绝采用；原文件不被覆盖。

缺同请求身份结果时，只有 `--yes --allow-source-export` 同时存在才新购一次。
`novel/llm/paid_requests.py` 经现有 transport 直连，先保存原响应与用量，再解析、校验；
形状错误免费恢复，不进行整份 JSON 付费修复。pending/unknown 保持阻断，不自动重购。
请求身份含 provider、model、完整请求与执行参数；原响应采用与新调用均记入唯一
`<NOVEL_OUTPUT>/_service/ledger.jsonl`，缓存命中单列，缺失 usage 保持未知。

`runtime/workflow_status.json` 显式区分 running/failed/source_compiled/planned_validated；旧文件保留用于恢复，
失败不会继承上次绿灯。`planned-only` 重放只证明编译与消费链，不签发 accepted receipt，
不代表成文质量验收。`runtime/request_*.json` 是已购证据，禁止当普通派生缓存清理。
RC2 的人工语义修订是显式外部输入，不是 compiler 自然语言猜测规则；F5e 的开发性阅读
不替代 F9 用户验收或后续 Golden 的质量结论。

## 当前 RC 模块与路径（RC4）

| 模块 | 承载 | 关键不变量（构造期表达） |
|---|---|---|
| `policy.py` | `PreservationPolicy`（版本化策略）、`PreserveKind` 闭集 | 类别集变化必须升版；执行身份不进 Policy |
| `grounding.py` | `SourceObligation` / `PreserveCheckpoint` / `ReconstructionBrief` / `IdentityProjection` / `OutputConstraints` / `SemanticBinding` | 必保义务必有非空 `satisfaction`；checkpoint 无 Design/Bind 字段；Brief 无源章/源拍/叙述顺序/证据定位字段；Binding 是完整 snapshot，entries 与 unbound 不交集 |
| `design.py` | `BodyDesign`／`PresentationGraph(BodyDesign)+hook`（RC4：`SceneDriver`/`TargetBeat`/`TargetDelta`/`SceneDelivery`/`ChapterAssignment`） | source-blind（无 source_ref 族字段且 forbid extra）；章号连续 1..N、场景全覆盖、每章非空；hook 必填且 graph 内引用闭合；每场 `realization_brief` 与每章 `purpose` 必填非空（缺省拒读） |
| `hook.py` | `HookDesign`／`HookContext`／`HookReceipt`／`HookConstraints`（F6，形状未变） | 起笔事实只引用已建立的目标事实；first 恰一人且非 narrator；材料唯一呈现语义口是 presentation；回执 semantic 恒 unverified |
| `naming.py` | `NameMapV5`／`NameAssignment`／`TargetName`（F6） | name 与 keep_reason 恰好一个；full 由 compiler 拼接；char_ref 唯一 |
| `source.py` | Characters v4（`CharactersCore` + `name_structure` RC 段）／Blueprints v12（F6 扩） | core view 与 RC2 同形（Preserve sha 不变）；名称结构 surface 恰好覆盖、模板渲染逐字相等、纯称谓不含姓名字面、同姓亲属共享 family_id |
| `execution.py` | `ExecutionPlan`（establishment/constraint/dependency/reinforcement）与 `ExecutionSlice` | 必保只落 establishment/constraint；`Reinforcement` 无 obligation_id（0..N 不稀释必保）；依赖边禁自指 |
| `context.py` | `WriterContext` 双视图（`VerificationIR` + `WriterBrief`，RC4 职责重划） | IR 带 `beats` 逐拍定位与 `prohibitions`（从 delivery.avoid 投影）；Brief 只承载呈现（`realization_brief` 原样投影＋driver 摘要＋POV/voice/hints），事实清单不进 Brief；两视图 scene_id 与 `execution_slice_sha256` 同源；attempt/审计字段不进静态 IR |
| `book_ir.py` | `WriterBookIR`＝`BookBrief`＋`ChapterIR[]`（F5） | BookBrief 是全书约束与确定性导航（不调 LLM 总结、不取 POV 众数、不补造统一文风；禁止项只从已有明确限制及释放位置投影）；ChapterIR 携带章目的、确定性章预算与场景双视图；章号连续、导航与章 IR 一致 |
| `receipt.py` | `ChapterReceipt`（章级回执；场景回执与 Fidelity 随取消的生产正文 Judge 退役） | 绑定章序、全书 IR、实际前文、改名前正文、实际请求身份、协议/renderer/校验版本；`semantic` 恒 unverified；只有篇幅偏差可在额度耗尽后 `length_disclosed` 披露放行 |
| `consumption.py` | 六个消费投影族（world_view／character_exposure／chapter_map／body_access／hook_material／rename_materials） | body_access 按章级有效回执判定；rename_materials 写明 Naming 只读投影与 Apply 唯一应用者的权限分界 |
| `families.py` | 三级路径家族登记（16 个家族；F5 增 stage4/ir、stage5/body、stage5/acceptance/body，删 stage5/scenes、stage5/acceptance/fidelity） | 来源账键=完整相对路径（不塌两级） |
| `dependency.py` | 新链依赖方向登记 ＋ per-stage 真实模块 owner 映射与规则 | 目标表自洽；per-stage 规则由 AST 对真实文件执行（test_str06_hook） |
| `retirement.py` | F6d 现役 Stage6 链退役登记（纯数据，106 项） | 双向对账：登记项必须真实存在、现役面必须全部在册；随 RC 包 F8 删除 |

对应引擎（F8c 起住正式 per-stage 包，F5 新增）：`novel/stages/stage4/book_ir.py`
（章预算 largest-remainder 确定性分配＋全书 IR 唯一编译）、
`novel/stages/common.py`（确定性 renderer＋版本化 Writer 协议）、
`novel/stages/stage5/body_checks.py`（章级机械校验＋专名禁用面推导）、
`novel/stages/stage5/body.py`（按章 Writer、有限重写与确定性候选选择）。
跨 stage 共用的确定性原语（字面扫描、机械判定、prompt 组装、场景投影、阅读界面）
同住 `novel/stages/common.py`；薄编排与设施装配住 `novel/stages/chain.py`。

跨产物不变量（不由 schema 自身保证，归 F4 机械 Guard）：Binding 的 obligations
分母必须与采用的 Preserve checkpoint 一致；ExecutionPlan 的依赖图无环；
entry_state/knowledge 的 `established_by` 均早于本场起点（试点 M2/M3 的迁移）。

当前三级路径按登记家族准入（`contracts/schema/families.py`，16 个家族）；
家族外多级路径仍拒，两级一般规则不变。实现在 `novel/paths.py`：
`check_rel_family`（准入）与 `family_input_key`（来源账键=完整路径）。
家族路径的 INVALID 孪生名与 `_rounds/` 归档布局已登记；接通正式 Store 的版本、
provenance、freshness 与归档事务归 F7a，不能把路径可用当作设施接入完成。
正文（`stage5/body/`）、章回执与付费 checkpoint 不是可随意删除的缓存；kind 登记是
删除政策的输入，F8 收口前不实现任何按 kind 的清理。

原 F0–F4 的 Prepare 采用 `SourceHookEvidence + TargetOpening|null`，两套坐标分离，
源 `through_chapter` 不控制目标材料；没有 accepted body 时目标开篇显式未就绪。
此记录保留其历史证据与 Q-13 纪律；Prepare 消费链已由 F6 的 Hook 正式选择替代，
`PrepareMaterials`/`SourceHookEvidence`/`TargetOpening` 在 RC3 删除。per-stage 依赖规则
（hook_writer／hook_context／hook_design／naming／assembly／name_structure）已由
AST 测试对真实模块执行（`tests/test_str06_hook.py`），不是纸面检查。

## 正式设施接入与历史采用（候选契约与设施接线已落；历史采用待 F7a 第三步）

候选节点、产物 schema／版本及 workflow／manifest／execution epoch 身份已前置定义
（`contracts/invariants.py::NODES` 与 `contracts/artifacts.py::ARTIFACT_PATHS`，F7a 第一步）：节点表含 paid、lifecycle、
flags、produces、免费复检与恢复入口（compile／assemble 以 `executor_content_limit=0`
结构性禁付）；产物表含家族、schema 版本与唯一执行 writer（模型自带
`contract_version` 的表值逐字对账）；epoch 身份支持首开、复用、code_drift／
contract_drift／repair 滚动，假漂移拒绝，滚动不重置累计额度。候选契约作为显式依赖
传入运行上下文、Store、许可和预算设施（`novel/llm/paid_requests.py` 与
`Str06Repository(root, context)`）；生产入口继续使用现役注册表。候选身份不借旧节点
获得许可，不绕过版本校验，不修改全局注册表以启动隔离链。F8 激活同一套已验收定义为
唯一现役契约，临时 adapter 随接管完成退役。

Repository 已接家族路径、完整相对路径来源账键、provenance／freshness、INVALID／_rounds
和原子提交（F7a 第二步）：provenance 复用 `contracts.schema.common` 同一套模型，
与正式 ctx 同构同闸；freshness 由 `prov.freshness` 复算，明确 stale 拒绝、无
provenance 保持 unknown；断言与视图走不记账的 `inspect` 读口。付费边界已接唯一
ledger、工作单元与书级额度：新购首 wire 前经 `WorkUnitBudget.reserve` 取内容轮，
响应同步进工作单元账，ledger 行带真实候选节点 id 与 sid；缓存先于新调用授权，
pending／unknown 不自动重购或释放额度；未采纳轮在下一次 reserve 前关闭且拒绝证据
保留。F5e 原根只读保存，在独立候选输出根初始化新 workflow（`runtime/
candidate_workflow.json`：源输入身份变化与契约表漂移关闭式拒绝）；通过登记的显式
历史采用核对原请求、响应、来源摘要与原成本关联。同请求身份的合法结果可零新购采用，
身份不匹配、证据损坏或付费状态未知时明确阻断。owner patch 保留原基线及修订来源；
历史采用不复制缓存、不续旧 session、不重复计原实付。原证据字节保持不变，同输入交付
文本字节一致；正式封装和 provenance 单独验证，不要求新旧元数据文件逐字相同。历史
采用通路已由 F7a 第三步实现（`--adopt-root` 登记源根，身份／响应／成本三方核对，
零新购、不复制缓存、不续旧 session），候选根 `outputs/str06_f7a_candidate_20260924`
与 F5e 交付逐字一致；仍不得据本节原地升级 F5e 根。

owner runner 拆分已落地（F7b）：`novel/stages/chain.py` 按候选节点表编排
Stage2 source／names、Stage4 preserve／body_design／hook_design／bind／compile、
Stage5 body／hook、Stage7 naming／assemble；每个 runner 的输入从盘面读——源 core 与
BodyDesign 是正式派生产物（`stage2/compiled/book_extraction.json`、
`stage4/design/body_design.json`），不再只活在内存；`novel/stages/chain.py` 是薄驱动，
复检用 `--check-only`／`--node`，不新造 check 节点。候选产物落盘保序（同正式
`_render`）：盘上字符典序即 wire 序，prompt 与已购请求身份按字节复算。
Stage2 输入已改读正式 Stage1 章数组（F7c 第一步）：`--book-sid` 经正式契约读
`stage1/main_text.json`（MainText，阅读序密集校验），源输入身份是该产物盘上字节
SHA-256 并写入 workflow 记录；目标章数等于源章数由模型保证，书级正文篇幅从源章总
字数向上取整确定性派生（`derive_total_length_budget`），Hook 长度和章篇幅带的正式
来源是 `STR06_RUNTIME_CONFIG`（进契约身份，与付费调用额度分开），输入或配置变化后
旧根的采用结论按设计拒绝复用。Stage1 hook_copy 新链无消费者，随旧链于 F8 退役。
恢复职责（Binding INVALID＋typed witness、owner 停止、同指纹熔断、binding-only 精确
补尾、public_state_ref 三反例、analyze receipt／draft repair 审计、name_distinctiveness
消费接管）属 F7c 余项。子批次、验收、红账及删除条件只在 REQUIREMENTS 维护。

## 扩容目标：四类 truth 与职责（隔离实装，正式接线待 F7/F8）

| Truth | 唯一职责 | 目标产物 |
|---|---|---|
| Characters | 源人物身份、初始状态、稳定约束及源名称结构 | `stage2/characters/characters.json`（v4） |
| Blueprint | 源事件、变化、揭示、因果及书级初始事实／约束 | `stage2/blueprints/chapter_blueprints.json`（v12） |
| Target Design | 目标初始语义、正文结构与归章、Body/Hook 呈现选择 | `stage4/design/presentation_graph.json`（v1） |
| Semantic Binding | Preservation 决定、source lineage、完整保留约束及目标承载 | `stage4/grounding/semantic_binding.json`（v1） |

Stage2 回答原故事是什么；Stage4 负责目标故事设计、Binding、确定性编译和 WriterIR；
Stage5 将 WriterIR 实现为语言；Stage7 完成交付命名、表层替换和组装。Stage6 整层退役。
`R = Compile(D,G)`，`T₀ = Init(R)`；compiler 不重新读取 SourceTruth 补猜目标状态，
Binding 不维护第二份初始态。Identity 等必要材料只经明确公共投影消费。

输出保留 N 个非空目标章与连续章号；Design 决定场景归章，源情节允许跨章重构。
保留全书篇幅边界，compiler 按 Design 的章／场权重分配目标字数预算，不对照同号
源章长度；字数预算与付费调用预算分开。

语义窄修复由对应 owner 提交绑定基线身份的最小 patch，保留无关 canonical 内容和
稳定 ID；受影响 derived slice 确定性重算。正文从首受影响场景重新投影与核验，
实际输入未变的已购结果及有效接受前缀继续采用，不把全书扫描范围当付费重跑范围。

正文结构先完成，Hook Design 再消费去源拓扑的 target-story view、Identity 与 style。
两次语义请求的结果由同一 Target Design publisher 校验并发布完整 graph；不让两个
producer 轮流改 canonical，不用空 Hook 默认值冒充完成。Bind 只给目标故事分配保留
义务，Hook 不增加义务分母，也不作为正文漏保的替代承载。

## F5 的导演说明、双层 WriterIR 与按章成文（RC4 已实装，F5e 真书重跑完成）

**导演说明由 Design 直接产生。**`TargetScene.realization_brief`（本场怎样演出来：
场景重心、人物如何理解局面、展开与压缩、关键转折及收束点、邻场衔接）与
`ChapterAssignment.purpose`（本章承担的核心变化，概括章内已设计的变化，不单独
增加事件或结果）是必填、非空的自然语言字段。职责分工保持：`driver` 承载欲望/
阻力/转折；`event/target_delta/surface_introductions` 承载故事事实、变化及许可
位置；`realization_brief` 只承载呈现方向；`delivery_hint` 只补拍级局部表达；
`delivery` 承载 POV、voice、avoid。brief 中涉及关键行动、持久认知变化、秘密、
承诺或因果条件的内容必须同时落在相应故事字段——不能经自由文本绕开正式状态与
披露设计。compiler 只查形状、引用及可机械证明的一致性，不用关键词规则声称
发现自然语言冲突，也不新增语义 Judge。

**场景 WriterIR 保留双视图，职责重划。**`VerificationIR` 回答「什么必须成立」：
起笔状态、人物知情、本场行动和义务、满足条件、逐拍结果（`BeatIR` 的
event/delta/释放位置定位）、信息释放位置及明确禁止事项（从 `delivery.avoid`
投影）；它表示应满足的条件，不表示正文已经通过语义验证。`WriterBrief` 回答
「怎样呈现」：canonical `realization_brief` 原样投影＋driver 摘要＋POV、voice
与拍级 delivery hints；旧 Brief 中重复的事件、义务、状态清单移回事实视图，
避免两份内容分别解释同一语义。两视图来自同一份 Design/Binding，使用相同场景
身份与 slice 身份。

**全书静态 IR。**`WriterBookIR = BookBrief + ChapterIR[]`，由 compiler 在任何
正文购买前从（BodyDesign, ExecutionPlan, SemanticBinding）确定性编译并唯一落盘
`stage4/ir/writer_book_ir.json`。`BookBrief` 汇集已有约束与按阅读序的导航（章
目的、场景一行摘要、称谓释放位置表）；不调用 LLM 总结、不取场景 POV 众数、
不补造统一文风；额外禁止项只从已有明确限制及释放位置投影，不从未来剧情猜政策。
`ChapterIR` 携带章目的、章预算（按 Design 场景权重 largest-remainder 整数分配，
总和恰等于书级目标，不对照同号源章长度）与本章场景双视图。Stage4 只生成静态
写作 IR；Stage5 从有效章回执读取前文形成派生的 `ChapterWriterPacket`；Stage4
不反向读取 Stage5 正文。

**模型阅读界面是确定性 renderer。**内部严格 IR 与模型实际阅读文本分别定义；
renderer 只排序、分组、引用解析和格式化，不压缩语义、不改写 brief、不新增
模型调用。正文请求按六层组织：【整书写作约定与自由度】（版本化 Writer 协议：
entry 是起笔已成立状态、effects 是结束需成立结果、义务必须实际兑现、约束内
创造自由、局部细节不得产生新的关键因果/知情/后续依赖）→【全书规划参考：作者
知道，不等于人物或读者已知】→【完整已提交前文】→【本章目的】→【本章各场景：
导演说明＋事实约束】→【本章交付要求】。完整 `WriterBookIR` 仍进入请求；当前章
材料集中展示，其余章完整场景 IR 标为规划参考，同一内容不重复展开。全部实质
约束（义务与满足条件、知情模式、否定条件、释放位置、逐拍结果）可追溯进入请求；
source lineage、源章拓扑和审计字段不进 Writer 阅读面。renderer／协议与
prompt 各自版本化并进入请求身份；首版不自动缩短上下文——完整输入超限由精确
tokenizer＋容量闸阻断，不静默摘要、截断或换模型。

**按章生成、机械控制与候选选择。**正文严格按章串行：每次读取完整 IR 和全部
有效前文，一次输出当前整章自然正文；章号由 compiler 添加，不强制输出场景片段、
ID 或覆盖声明，不建设厂商会话状态。章篇幅带默认 `[0.6, 1.3] × 预算`（CLI 可
覆盖），全书 ±10% 偏差读数无条件报告；计数排除空白、程序章号和 Hook。每章
首次生成加最多两次重写，重写使用相同 IR、相同前文、上一稿与 typed 机械错误。
硬检查（非空、编码、包装、明确未释放专名、响应截断）失败有额度则重写、不能
披露放行；只有篇幅偏差可在额度耗尽后按「相对目标篇幅偏差最小优先、平局取
较晚者」提交并披露；没有合法候选则停止后续生成。整章自由正文不提供可靠的
场景边界，机械检查只能覆盖章级明确字面禁用等事项，不能证明章内揭示位置或
逐场义务兑现——这些限制写入交付披露与阅读验收。

**章级提交与恢复。**提交顺序：原响应耐久保存 → 确定性编译与检查 → 选定候选
→ 原子写正文 → 原子写章回执（提交完成）→ 下一章。章级回执绑定章序、全书 IR、
实际前文、改名前正文、实际请求身份、协议及校验版本，记录机械结果和篇幅披露；
拒绝候选单独留证；pending/unknown 属于执行状态。无有效回执的文本不能作为前文
或交付输入。保存旧提交历史，恢复从连续有效章前缀开始；已保存响应优先免费补
提交；progress/report 由证据重建，不成为第二份业务权威。付费边界上缓存采用
先于授权拒绝；新调用再核对付费、正文外发许可及显式 `--book-call-ceiling`，
额度在锁内按准入凭据累计，pending/unknown 不释放额度，缓存采用不耗新调用额度；
成本唯一权威仍是 ledger。完整上下文的失效规则：Writer 可见的全书 IR 改变
（包括未来场景 brief）→ 全部正文重新核验、不沿用旧请求身份；第 k 章正文改变
→ 保留有效前缀、后续章因输入变化失效；只改 Hook 或 NameMap → 正文保持有效；
validator 改变而请求未变 → 免费重验已有响应。

## Hook 的正式选择与确定性投影（RC3 已实装，F6a/F6b；brief 要求 F5 强化）

Hook 是同一目标故事的另一种呈现。设计负责材料及顺序、起笔、时间跳转、披露和收束；
Writer 负责句法、措辞、对白、局部动作、节奏、意象和语气。局部细节不得改变事实或
成为未经 Design 承接的后续情节依据，不重选材料、改序或突破披露许可。

由 Hook Design 保存必要的正式选择（RC3 落点 `contracts/schema/hook.py`）：
`pov{person,focalizers}`、`opening_facts[{ref,address,audience}]`（只引用 init 或
目标拍上真实建立的 StateAddress，按 world/pov/reader 分获准面）、
`materials[{beat_ref,withheld,presentation?}]`（呈现序，允许乱序冷开）、`concealed`、
`surface_releases`、`forbidden_reveals[{ref,address?}]`、两份 brief。具体字段经 F6
反例定版。compiler 只解析引用、投影与格式化，不解释自然语言 brief 补出缺失选择。
Body/Hook 仍由一份 graph 承载（`PresentationGraph(BodyDesign)+hook` 必填，
`publish_graph` 唯一发布），无额外 Hook truth 文件；Bind/Compile/Context/Guard
类型收窄到 `BodyDesign`，从类型上保证 Hook 不影响正文。机械校验臂
（`novel/stages/stage5/hook.py`）钉住：引用闭合、withheld ⊆ 该拍 delta、
world/pov 起笔事实早于首个材料、reader 不晚于材料视界且不在禁止集、起笔事实不得是
材料拍 delta、first 恰一人且非 narrator、释放称谓必须是已声明面且不属隐藏人物、
隐藏人物材料必须 presentation 且不含其字面。

HookContext（`stage4/context/hook/hook.json`，Derived）包含 POV、按 audience 分组
的起笔事实、按设计序排列的材料投影（presentation，或 event＋未 withheld 的 set
delta）、禁止揭示、两份 brief、长度边界、可用称谓与 `withheld_literals`（未释放的
专名字面，由名称结构 ∩ 全部人物专名/带名 surface 推导，不写死人名）。起笔事实不是
镜头描述，也不自动等于全书 T₀；冷开时区分世界已成立、POV 已知、读者获准知道。
正文 reveal 位置和 Binding 不能单独决定 Hook 披露边界，单个 beat 含须隐藏侧面时由
Design 显式 withheld 或 presentation。POV 不按正文多数票猜测，不强制第一人称；
compiler 从正式设计投影。Hook Writer 的机械 validator 查非空、长度、编码、包装行与
withheld 字面泄漏（先认领已释放称谓再扫）；回执 `semantic` 恒为 unverified；
超长同 Context 有限重写（再 2 次，attempt 进请求身份）。

免费反例（`tests/test_str06_hook.py`）覆盖：只用 canonical 重建同一 Context 字节
一致、悬空引用、禁止源 Hook/源坐标/义务字段/state0 自由文本混入、冷开乱序但事实及
披露自洽、起笔事实时序越界、隐藏身份在称谓/presentation/正文字面三层被挡、
第三人称多视角、first 配多人或 narrator 拒、单拍部分披露、POV 取自设计而非正文
众数、无关拍改动不扩大 HookContext 失效、只改 Hook 时正文 plan 全等、机械通过不产生
语义已验字段。原型已删除单书人名、全局缓存与人称词频假设，未直接复制试点脚本。

## Stage5 Hook 成文的接受边界（RC3 已实装；Body 面已由 F5 按章接管）

Hook 只有一个输出与机械回执（`stage5/hook/hook.txt`＋`stage5/hook/receipt.json`，
绑定 hook_context_sha256 与改名前 prose_sha256）；非空、长度、编码和 wrapper/Markdown
等格式检查不证明未剧透、遵守材料顺序、POV 正确或吸引人。机械通过不得升级为语义已验
（回执 `semantic` 恒为 unverified）；未验证质量按现有披露机制保留。超长在同一 Context
下有限重写（重写请求=同一 Context＋上一稿机械错误，身份确定、可免费恢复，最多再
2 次），属于 realization failure；上游设计错误回 Stage4，付费状态未知仍阻断，不用
质量披露绕过。隔离入口 `--through stage5` 以「正文按章提交＋hook_accepted_mechanical」
为终态（`--body-chapter-limit` 可限定章前缀；部分提交显式记 `body_partial`）。

Hook `design_brief` 的写作要求在 F5 强化为逐项覆盖：入口（从什么状态或画面进入）、
哪些材料快速带过、冲突如何升级、重点展开哪里、给读者留下什么问题、在哪里切断；
`delivery_brief` 保留口吻职责。两者仍为字符串，不增加一套导演字段；
Hook renderer 同样采用分层阅读界面，材料与事实边界保持现有正式投影。

## NameMap v5 与接受／交付文本分离（RC3 已实装，F6a/F6b）

NameMap 是交付变换契约，不是第五类 semantic truth。Naming 只消费 Characters 的稳定
身份与源名称结构、目标命名风格投影，不读整本源书、Body/Hook prose 或 Binding 内部。
投影通常不落盘；provider 请求证据仍按已购恢复契约保存。源姓名构成、称谓归属及独立
化名身份归 Stage2，目标名字归 Stage7；不在 NameMap 复制旧 canonical、aliases 或角色事实。
这里登记源名称结构的 owner 要求，不声称当前 Characters v4 已具备全部字段；需要补齐
正式形状时显式修订 RC 并提供恢复边界，不让 Naming 或 compiler 代替源语义抽取。

v5 的基础面为 `entries[{char_ref, name{family?,given?,full?}|None, aliases{},
keep_reason?}]`（RC3 落点 `contracts/schema/naming.py`；`full` 由 compiler
拼接，wire 不携带；name 与 keep_reason 恰好一个）。按源名称结构给出必要名称位：
源结构登记了姓必须给新姓、登记了名必须给新名，反之不得给；只改化名位的人物允许
name 全空＋显式 alias 赋值。`absence=unchanged` 不能掩盖模型漏答：compile 以明确
应改名集合核对完整覆盖（漏项/多余项/未知 char_ref 报错），合法保留必须带理由。
共享姓氏和姓氏称谓按已知归属处理（surface_forms 模板逐人渲染），不能全局替换单个
姓字；同姓不同人或同一表层多归属导致同一源字面对应不同替换值时，必须显式报错并列出
owner，不能按替换顺序选 owner；共享同姓本身不是冲突。同 family_id 新姓必须一致、
不同源姓不得合并同姓、新姓必须与本人旧姓不同、新字面与他人旧字面冲突拒绝。
独立化名不由新姓或新全名机械猜出：由 Naming 对源身份中已登记的 alias_id 显式赋值；
不能用未授权的 `extra_subs` 或 residual 语义猜测补齐。

`compile_surface_replacements(Characters,NameMap)` 是纯函数，不新增 replacement-table
产物。已明确的姓氏构词由 surface_forms 模板渲染确定性展开（如 `姓{family}的`→
姓柳的；家族级 forms 如 `{family}家` 按同 family_id 新姓一致展开）；替换值包含另一
键（链式替换风险）报错。ApplyNames（`literal_scan.apply_replacements`）单遍长名
优先，避免连环替换。残留检查保留 `claimed/kept` 的有效语义：只查应替换的字面，
跳过认领区间与合法保留字面——目标名含旧字面、kept 含映射键时不误报；不把所有源名
字串都列禁词，不调用 LLM 猜测归属。diff proof 用骨架法机械证明差异恰为替换表。
源身份/名称结构缺失回 Stage2，目标名称缺失或冲突回 Naming，机械实现错误回 compiler。
Assembly（`novel/stages/stage7/assembly.py`）body_access 按 accepted/rejected/missing/
unknown 四态判定；无 accepted body 时显式 not_ready（F6 只落
`runtime/delivery/hook_renamed.txt` 与 runtime 组装报告，`stage7/final/` 不落产物）；
交付身份由（body hash, hook prose hash, 替换表, kept）派生，与 Stage5/Hook 回执分离，
换 NameMap 只变交付身份。

Stage5 receipt 绑定改名前正文；Stage7 用有效 NameMap、机械接受的 Hook 与 accepted
body 替换组装，另行绑定交付身份。同一输入字节、映射和组装契约产生相同交付字节；
换 NameMap 不使 Stage5 回执失效。接受状态、输入身份或 Body 未就绪时不得交付成功。

## 目标产物与代码职责（Body/Hook/Naming/Assembly 隔离链已实装）

| 身份 | 目标产物或证据 |
|---|---|
| Canonical Semantic Truth | 上述 Characters、Blueprint、Target Design、Semantic Binding |
| Derived | ReconstructionBrief、ExecutionPlan、WriterBookIR（含场景双视图）、HookContext、可重建 progress/cache |
| Accepted Prose | 有效章级回执绑定的章正文、单篇 Hook |
| Acceptance / Audit | Guard 报告、章级/Hook receipt；证明的范围与状态明确，不当作语义真源或普通缓存 |
| Delivery Transformation Contract | NameMap v5 |
| Final Deliverable | 最终重写成书（`stage7/final/`：TXT 先落盘、manifest 最后提交） |
| 已购恢复证据 | Preserve checkpoint、request/response 及采用账；与 Derived cache 分开保留 |

以下是目标输出根内的布局示意；正文按章成文的路径家族（`stage4/ir`、`stage5/body`、
`stage5/acceptance/body`）已随 F5 登记。正文与已购证据不可按“非 semantic truth”批量清理。

```text
stage2/characters/characters.json
stage2/blueprints/chapter_blueprints.json
stage2/compiled/、stage2/acceptance/
stage4/grounding/{reconstruction_brief,semantic_binding}.json
stage4/design/presentation_graph.json
stage4/compiled/execution_plan.json
stage4/ir/writer_book_ir.json
stage4/context/hook/hook.json
stage4/acceptance/guard/
stage5/body/ch{nn}.txt
stage5/acceptance/body/ch{nn}.json
stage5/hook/hook.txt、stage5/hook/receipt.json
stage7/naming/name_map.json
stage7/final/<书名>_rewriting.txt、stage7/final/delivery_manifest.json
runtime/progress/、runtime/body_attempts_ch{nn}.json、runtime/admission.json
```

正式代码继续按 owner 分包，schema 唯一在 contracts/schema 下。⚠ F8c（2026-09-25）：
RC 迁移包已拆进正式 per-stage 包并删除，下表从「目标代码职责」翻成**现役落点**。
不要求预建空目录或把每个函数做成独立抽象层。

| owner | 现役落点 |
|---|---|
| Stage2 | `novel/stages/stage2/source.py`（source 语义抽取／Blueprint 确定性编译／acceptance）、`novel/stages/stage2/names.py`（源名称结构） |
| Stage4 | `novel/stages/stage4/grounding.py`（preserve/bind）、`novel/stages/stage4/design.py`（body design 含导演说明与章节目的）、`novel/stages/stage5/hook.py`（hook design 与唯一 graph publisher）、`novel/stages/stage4/compile.py`、`novel/stages/stage4/book_ir.py`（WriterBookIR）、`novel/stages/stage5/hook_context.py`、`novel/stages/stage4/guard.py` |
| Stage5 | `novel/stages/stage5/body.py`（按章 body writer：机械检查/有限重写/候选选择）、`novel/stages/stage5/body_checks.py`、`novel/stages/stage5/hook_writer.py`（hook writer/validator） |
| Stage7 | `novel/stages/stage7/naming.py`（naming assign 与机械 apply）、`novel/stages/stage7/assembly.py`（final assemble：章回执链核对、TXT 先落盘、manifest 最后提交）；无故事 resolver 或语义 review 层 |
| 跨 stage 共用 | `novel/stages/common.py`（字面扫描、跨产物机械判定、prompt 原语、场景双视图投影、模型阅读界面 renderer） |
| 薄编排与装配 | `novel/stages/chain.py`（NODE_ORDER／NODE_RUNNERS／run_node／run_chain＋Stage1 输入、workflow/epoch、付费边界装配）；生产入口 `novel.cli run <node> <sid>` |
| 跨层设施 | 沿用 repository/store、paths、runtime 和 orchestrator；只抽取真实公共职责 |

依赖单向：Design 禁 Grounding 实现/Blueprint internals/checkpoint 裸读；Grounding 禁
Design 实现；Compile/Context 禁 LLM 和草稿；Stage5 只消费 WriterIR/acceptance；
Hook Writer 禁 Body prose、源 Hook 和源小说。Stage7 只消费正式契约与已接受文本，
不得反向修改 truth 或依赖 Stage2/4/5 internals；validator 不修输入，repository 不决策。

## 目标消费投影（RC3 登记已落 `consumption.py`，六族）

| 投影族 | 正式来源与消费面 |
|---|---|
| world_view | Design/Binding 的目标有效事实与约束，不以源时点冒充目标时点 |
| character_exposure | Characters 身份与 Design 显式称谓/披露选择；源与目标坐标分开 |
| chapter_map | Design/compile 的 scene→target chapter 映射及目标章集分母 |
| body_access | 有效 receipt 与 accepted body；未就绪显式返回，不再依赖 Prepare |
| hook_material | 正式 Hook Design 选择与 compiled target 的最小投影，不读 Body prose/Source Hook |
| rename_materials | Naming 用身份/源名称结构/风格投影；Apply/Assembly 用 Characters、NameMap 与接受文本，两种权限分开 |

源首次提及、身份揭示和姓名可用仍是源事实；目标许可由 Design 的明确选择派生，
不能换算源章号或用 Binding lineage 猜缺失语义。F3/F4 原消费干跑的证据范围保留，
新增投影与真实消费者闭合另验；不能把原五族可得误记为新 Hook/Naming 已通过。

## 本轮终点：书1 人工验收（F9，迁移口径）

F9 由正式产品入口产出《夫君的报恩》，agent 完成工程核验后交用户通读。错章、漏章、
改名残留、交付不一致、恢复失败等工程缺陷修到通过；文学质量意见逐条登记为下一轮
迭代优化的输入，不阻塞 STR-06 迁移关闭。工程绿、planned-only 干跑和机械回执都不
替代成文质量，语义未评保持未验证。F5e 为 agent 开发性阅读，不是用户验收或 Golden。

阅读结论绑定实际交付的 manifest／成书 hash。F9 修复后成书变化，重新取得用户对该版
的结论；字节未变可沿用已有阅读结论并核对身份关联。若修复改动生产代码、契约或测试
基础设施，须按最终提交重跑工程验收；无此类修改可复用 F8d 证据。最终工程证据和用户
验收同时有效后一次合入 main，不用旧候选的绿灯覆盖后续修改。

F6 开发性阅读检查 Hook 的冲突、信息升级、悬念及不提前兑现后文；F5e 已先读连续三章
再通读全书。F9 沿用这些阅读项，合读 Hook、正文开头及所选材料对应场景，检查重复、剧透、转场和
叙述声音，再检查整书人物动机、因果推进、场景节奏、情感与结局回收。并具体检查：
是否机械复述状态、每场重新开场、用旁白宣布 effects、压掉关键行动、对白和反应
缺乏推进，以及导演 brief 指导下的展开与压缩是否有效。Hook 与正文开头、取材场景
合读，检查重复、剧透、语气和悬念兑现；F9 属本轮交付验收，不新增产品逐章审批或
自动 taste Judge。新增质量读项沿用六维 rubric 和实际文本证据，
不把“像小说”降成义务逐条复述，也不为结构原创指标强迫搬移情节。

按目标语义位置评价事实、因果、披露与改名质量；Structural Originality 以书/场景及
承载关系比较，不要求同号章对应。F1/F4 证明能力接通，F9 如实报告书1 的真实生成是否
使用跨章自由度及证据；未使用则披露“真实生成中的跨章质量未获验证”。第二本表达力、
长篇／多线容量、前文增长及 NameMap v5 跨书赋值仍未验证，移交后续 Golden。

## 后续 Golden（本轮之后）

STR-06 关闭后先进行一轮新契约与流水线迭代优化，再执行 Golden，承接原 F3b 义务。
六维仍为 Core Preservation、Structural Originality、Causal Continuity、Character
Continuity、Natural Progression、Local Prose Coherence。人工阅读为主，模型评分辅助；
至少两本代表性书，其中至少一本长篇或多线结构。冻结输入与源版本、baseline 成书、
rubric/version、评价方式、模型辅助评审的 prompt／provider／model／参数、输出根与
成本信封，已有冻结 manifest 分母不临时缩减；评价方式变化只重评双方已有成书，不因此
重跑生成。源书、新书与 baseline 分别定位证据，未评或证据不足保持未验证。
Golden 不代替独立的 current-schema corpus 验收；范围外事项见 REQUIREMENTS，
不恢复独立 STR-05 活动入口，也不把多书验证重新放入 F7–F9。

## F8 契约切换清单（准备项）

F8 在分支内切换时同步闭合，候选可提前准备但不得提前现役化；F9 验收后才一次合入 main：

1. `ARTIFACT_SPECS` 翻转：characters v4、blueprints v12、graph/binding/plan、
   WriterBookIR（BookBrief/ChapterIR）、正文章文件/章级回执/Hook 回执、NameMap v5、
   交付及 runtime 家族；
   NameMap v4→v5 与旧 Stage2/4/5 形状断裂记入版本账本，旧书独立根重建。
2. 公开契约改写：现役 I-40 与 `R-5.2` 在本次文档修订及 F7 期间不改；目标 N 章连续非空及跨章重构的
   规则在 F8 同批切换，不能只改措辞却仍保留旧判据；
   R-2.*（Characters v4）、R-3.*（Blueprints v12）、R-5/R-6（Stage4 新链）、
   R-7/R-8（Stage5 新链，含章级提交与篇幅披露）、R-9/R-10 中消费旧产物的条款同步，
   并核对 `R-5.11b`、`R-14.1` 及 R-11～R-14 的受影响条款；根、novel、web 等指南中
   的章号恒等、Stage6 三候选／改选等旧产品边界同批更新。
3. `NODES` 与编排：Stage2 抽取、Stage4 Preserve/Body Design/Hook Design/Bind、
   Stage5 按章 Body Write 与 Hook Write、Stage7 Naming/Assemble 及免费检查/恢复
   面闭合（生产正文 Judge 已取消，不注册判官节点）；
   节点/profile/permit/消费身份双向对账。`--check-only` 沿用免费检查职责，
   不因本文件增加占位 check 节点。
4. 判据注册表与 `docs/schema/` 现役文档：试点 M1–M6 机械 Guard 按新坐标正式化
   （场景级写前守卫保留）；`tests/test_artifact_versions.py` shape-break 账本同批答
   「谁重建」。
5. Stage6 整层及 prepare/hook/approve 节点、三候选/改选/rebuild/历史审批写接口、
   产物家族和 approval bindings 登记 retired；旧 Stage2/4/5/7 替代路径同批删除。
   旧书 `legacy_incompatible` 只读解码，不用旧批准或默认值迁移到新链。
6. API/Views/前端消费新投影，删除 Stage6Screen 与旧操作面，重生 generated types；
   RC 包、Str06Repository、PaidRequestSession 和隔离 harness 接入正式设施后分流删除。
   先迁移仍有效的职责反例，不以删旧测试缩验收分母。
7. F7e 的旧 Stage2/4/5/7 补充退役登记与既有 Stage6 登记共同执行，覆盖编排、消费投影、
   判据、API/UI、schema 文档及有效测试反例；grandfather 清零。本文内容分流到正式
   schema 文档，STR-06 关闭时本文移出现役文档，历史留在 Git，引用同步更新。
8. F8d 完成契约 §9.5 五文件、默认非 corpus 回归、前端 test/typecheck/seams/build 及真实
   产品入口的 fake transport 全链、免费恢复和旧书拒读验收。最终现役零失败、零错误、
   无未说明跳过或收集排除；既有红账按修复或职责接管后的退役证据逐项清偿，不能只要求
   “新增失败为零”。冻结代码提交、测试证据与候选身份；F9 修复后的复验及用户验收按上节
   绑定最终交付，一次合入 main。付费与正文外发分别授权；Golden 不属于本轮关闭条件。

当前文档修订不修改 AGENTS、BACKEND_CONTRACT、注册表或可执行代码；以上接线、切换
与验收准备项均待实施，进度唯一以 REQUIREMENTS 为准。
