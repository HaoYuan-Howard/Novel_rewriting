# STR-06 终态架构 RC 契约（Stage2–Stage7，Stage6 退役）

本文件登记当前 RC3 实装与 Stage2–Stage7 扩容目标：四类 truth、Hook、交付改名、
路径与消费边界、阅读验收及 F8 切换清单。下文明确区分已实现契约和待实施目标；
Stage6 整层退役是目标，不表示现役链已经切换。
批次进度、必要证据索引与历史快照入口见 `docs/REQUIREMENTS.md` STR-06；架构裁决见
`docs/DECISIONS.md` 2026-09-23。

**RC 边界**：代码落点为 `contracts/schema/str06/` 子包。字段级 schema 保持 RC，
经反例与真书验证后定版。本子包**不进** `ARTIFACT_SPECS`、不注册
`NODES`、不进判据注册表；现役链按旧契约验收，正式替代一次合入（F8）。
当前代码版式身份为 `str06-rc3`（F6，2026-09-24）：Hook、NameMap v5、名称结构与
stage5/hook、stage7/naming、stage7/final 路径家族已实装（F6c 真书付费冒烟除外）。
后续破坏形状须显式修订 RC 身份，不能靠文档更新默许旧载荷。RC1/RC2 目标产物不能直接
加载；只能逐 owner 显式审阅 wire 后免费重编译（RC2 wire 在 RC3 代码下 body 路径
免费重编译与 replay 逐字一致，checkpoint sha 不变——见 REQUIREMENTS F6 证据行）。

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

`tools/str06_workflow.py` 是隔离链的真实入口。`--source-json` 接收显式的连续源章数组
`[{chapter_ordinal,text}]`；不读取隐藏旧 Stage1 路径。`NOVEL_OUTPUT` 必填，`--run-dir`
必须位于其中且续跑固定使用同一根。`--wire-dir` 免费采用逐 owner 的 `book_wire.json`、
`chNN_wire.json`、`preserve_wire.json`、`design_wire.json`、`bind_wire.json`，每份记录字节摘要
及校验结果。`--repair-manifest` 核对修订前后文件 SHA，变化则拒绝采用；原文件不被覆盖。

缺同请求身份结果时，只有 `--yes --allow-source-export` 同时存在才新购一次。
`novel/llm/str06_requests.py` 经现有 transport 直连，先保存原响应与用量，再解析、校验；
形状错误免费恢复，不进行整份 JSON 付费修复。pending/unknown 保持阻断，不自动重购。
请求身份含 provider、model、完整请求与执行参数；原响应采用与新调用均记入唯一
`<NOVEL_OUTPUT>/_service/ledger.jsonl`，缓存命中单列，缺失 usage 保持未知。

`runtime/workflow_status.json` 显式区分 running/failed/source_compiled/planned_validated；旧文件保留用于恢复，
失败不会继承上次绿灯。`planned-only` 重放只证明编译与消费链，不签发 accepted receipt，
不代表成文质量验收。`runtime/request_*.json` 是已购证据，禁止当普通派生缓存清理。
RC2 的人工语义修订是显式外部输入，不是 compiler 自然语言猜测规则；质量仍需 F5/F8 验收。

## 当前 RC 模块与路径（RC3）

| 模块 | 承载 | 关键不变量（构造期表达） |
|---|---|---|
| `policy.py` | `PreservationPolicy`（版本化策略）、`PreserveKind` 闭集 | 类别集变化必须升版；执行身份不进 Policy |
| `grounding.py` | `SourceObligation` / `PreserveCheckpoint` / `ReconstructionBrief` / `IdentityProjection` / `OutputConstraints` / `SemanticBinding` | 必保义务必有非空 `satisfaction`；checkpoint 无 Design/Bind 字段；Brief 无源章/源拍/叙述顺序/证据定位字段；Binding 是完整 snapshot，entries 与 unbound 不交集 |
| `design.py` | `BodyDesign`／`PresentationGraph(BodyDesign)+hook` v1（`SceneDriver`/`TargetBeat`/`TargetDelta`/`SceneDelivery`/`ChapterAssignment`） | source-blind（无 source_ref 族字段且 forbid extra）；章号连续 1..N、场景全覆盖、每章非空；hook 必填且 graph 内引用闭合 |
| `hook.py` | `HookDesign`／`HookContext`／`HookReceipt`／`HookConstraints`（F6） | 起笔事实只引用已建立的目标事实；first 恰一人且非 narrator；材料唯一呈现语义口是 presentation；回执 semantic 恒 unverified |
| `naming.py` | `NameMapV5`／`NameAssignment`／`TargetName`（F6） | name 与 keep_reason 恰好一个；full 由 compiler 拼接；char_ref 唯一 |
| `source.py` | Characters v4（`CharactersCore` + `name_structure` RC 段）／Blueprints v12（F6 扩） | core view 与 RC2 同形（Preserve sha 不变）；名称结构 surface 恰好覆盖、模板渲染逐字相等、纯称谓不含姓名字面、同姓亲属共享 family_id |
| `execution.py` | `ExecutionPlan`（establishment/constraint/dependency/reinforcement）与 `ExecutionSlice` | 必保只落 establishment/constraint；`Reinforcement` 无 obligation_id（0..N 不稀释必保）；依赖边禁自指 |
| `context.py` | `WriterContext` 双视图（`VerificationIR` + `WriterBrief`） | 两视图 scene_id 与 `execution_slice_sha256` 同源；IR 带 `continuity_fact_view`（跨场回指误报的根因修复输入） |
| `receipt.py` | `SceneReceipt`（最小七字段）与 `FidelityVerdict` | 无 plan hash/时间戳等无关 metadata 字段；`prose_sha256` 绑定改名前正文；`failure_origin ∈ {writer, upstream_ir, unverifiable}`；无修复字段 |
| `consumption.py` | 六个消费投影族（world_view／character_exposure／chapter_map／body_access／hook_material／rename_materials；F6 删 Prepare 族） | body_access 不再含 stage6.prepare；rename_materials 写明 Naming 只读投影与 Apply 唯一应用者的权限分界 |
| `families.py` | 三级路径家族登记（15 个家族；F6 增 stage5/hook、stage7/naming、stage7/final） | 来源账键=完整相对路径（不塌两级） |
| `dependency.py` | 新链依赖方向登记 ＋ F6 迁移包真实模块 owner 映射与规则 | 目标表自洽；迁移包规则由 AST 对真实文件执行（test_str06_hook） |
| `retirement.py` | F6d 现役 Stage6 链退役登记（纯数据，106 项） | 双向对账：登记项必须真实存在、现役面必须全部在册；随 RC 包 F8 删除 |

跨产物不变量（不由 schema 自身保证，归 F4 机械 Guard）：Binding 的 obligations
分母必须与采用的 Preserve checkpoint 一致；ExecutionPlan 的依赖图无环；
entry_state/knowledge 的 `established_by` 均早于本场起点（试点 M2/M3 的迁移）。

当前三级路径按登记家族准入（`contracts/schema/str06/families.py`，15 个家族）；
家族外多级路径仍拒，两级一般规则不变。实现在 `novel/paths.py`：
`check_rel_family`（准入）与 `family_input_key`（来源账键=完整路径）。
INVALID 孪生名与 `_rounds/` 归档沿用现役机制。两级特例
`stage5/rewritten_body.txt` 走现役两级准入。正文（`stage5/scenes/`）与付费
checkpoint 不是可随意删除的缓存；kind 登记是删除政策的输入，F8 收口前不实现
任何按 kind 的清理。

原 F0–F4 的 Prepare 采用 `SourceHookEvidence + TargetOpening|null`，两套坐标分离，
源 `through_chapter` 不控制目标材料；没有 accepted body 时目标开篇显式未就绪。
此记录保留其历史证据与 Q-13 纪律；Prepare 消费链已由 F6 的 Hook 正式选择替代，
`PrepareMaterials`/`SourceHookEvidence`/`TargetOpening` 在 RC3 删除。迁移包依赖规则
（hook_writer／hook_context／hook_design／naming／assembly／name_structure）已由
AST 测试对真实模块执行（`tests/test_str06_hook.py`），不是纸面检查。

## 扩容目标：四类 truth 与职责（待实施）

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

## Hook 的正式选择与确定性投影（RC3 已实装，F6a/F6b）

Hook 是同一目标故事的另一种呈现。设计负责材料及顺序、起笔、时间跳转、披露和收束；
Writer 负责句法、措辞、对白、局部动作、节奏、意象和语气。局部细节不得改变事实或
成为未经 Design 承接的后续情节依据，不重选材料、改序或突破披露许可。

由 Hook Design 保存必要的正式选择（RC3 落点 `contracts/schema/str06/hook.py`）：
`pov{person,focalizers}`、`opening_facts[{ref,address,audience}]`（只引用 init 或
目标拍上真实建立的 StateAddress，按 world/pov/reader 分获准面）、
`materials[{beat_ref,withheld,presentation?}]`（呈现序，允许乱序冷开）、`concealed`、
`surface_releases`、`forbidden_reveals[{ref,address?}]`、两份 brief。具体字段经 F6
反例定版。compiler 只解析引用、投影与格式化，不解释自然语言 brief 补出缺失选择。
Body/Hook 仍由一份 graph 承载（`PresentationGraph(BodyDesign)+hook` 必填，
`publish_graph` 唯一发布），无额外 Hook truth 文件；Bind/Compile/Context/Guard
类型收窄到 `BodyDesign`，从类型上保证 Hook 不影响正文。机械校验臂
（`novel/stages/str06/hook_design.py`）钉住：引用闭合、withheld ⊆ 该拍 delta、
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

## Stage5 接受边界与正文衔接（Hook 面已实装，Body 面归 F5）

Body 严格串行 `Project → Guard → Write → Judge → Commit → Apply planned_delta`；
恢复只重放连续有效 accepted prefix。Body Writer 消费有限相邻已接受正文 tail，窗口规则
和实际消费字节须进入本场 Context/请求及 slice 身份；从已接受正文重建 tail，不从正文反抽 semantic
delta，也不绑定未消费的整书文本。材料缺失、unknown、拒绝和接受状态分开。

Writer 可在既有 scene driver／voice／delivery hint 内实现不改变因果、知情和持久
状态的迟疑、潜台词、情绪与局部动作；新增前史、秘密、长期动机或后文依赖的事实
须由 Design 明确承接。不从正文反推状态，不增加情绪状态机。

Hook 只有一个输出与机械回执（`stage5/hook/hook.txt`＋`stage5/hook/receipt.json`，
绑定 hook_context_sha256 与改名前 prose_sha256）；非空、长度、编码和 wrapper/Markdown
等格式检查不证明未剧透、遵守材料顺序、POV 正确或吸引人。机械通过不得升级为语义已验
（回执 `semantic` 恒为 unverified）；未验证质量按现有披露机制保留。超长在同一 Context
下有限重写（重写请求=同一 Context＋上一稿机械错误，身份确定、可免费恢复，最多再
2 次），属于 realization failure；上游设计错误回 Stage4，付费状态未知仍阻断，不用
质量披露绕过。隔离入口 `--through stage5` 到 hook_accepted_mechanical/hook_rejected
显式状态为止。

## NameMap v5 与接受／交付文本分离（RC3 已实装，F6a/F6b）

NameMap 是交付变换契约，不是第五类 semantic truth。Naming 只消费 Characters 的稳定
身份与源名称结构、目标命名风格投影，不读整本源书、Body/Hook prose 或 Binding 内部。
投影通常不落盘；provider 请求证据仍按已购恢复契约保存。源姓名构成、称谓归属及独立
化名身份归 Stage2，目标名字归 Stage7；不在 NameMap 复制旧 canonical、aliases 或角色事实。
这里登记源名称结构的 owner 要求，不声称当前 Characters v4 已具备全部字段；需要补齐
正式形状时显式修订 RC 并提供恢复边界，不让 Naming 或 compiler 代替源语义抽取。

v5 的基础面为 `entries[{char_ref, name{family?,given?,full?}|None, aliases{},
keep_reason?}]`（RC3 落点 `contracts/schema/str06/naming.py`；`full` 由 compiler
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
Assembly（`novel/stages/str06/assembly.py`）body_access 按 accepted/rejected/missing/
unknown 四态判定；无 accepted body 时显式 not_ready（F6 只落
`runtime/delivery/hook_renamed.txt` 与 runtime 组装报告，`stage7/final/` 不落产物）；
交付身份由（body hash, hook prose hash, 替换表, kept）派生，与 Stage5/Hook 回执分离，
换 NameMap 只变交付身份。

Stage5 receipt 绑定改名前正文；Stage7 用有效 NameMap、机械接受的 Hook 与 accepted
body 替换组装，另行绑定交付身份。同一输入字节、映射和组装契约产生相同交付字节；
换 NameMap 不使 Stage5 回执失效。接受状态、输入身份或 Body 未就绪时不得交付成功。

## 目标产物与代码职责（Hook/Naming/Assembly 已实装，Body 成文归 F5）

| 身份 | 目标产物或证据 |
|---|---|
| Canonical Semantic Truth | 上述 Characters、Blueprint、Target Design、Semantic Binding |
| Derived | ReconstructionBrief、ExecutionPlan、Body/Hook WriterIR、可重建 continuity/tail/progress/cache |
| Accepted Prose | 有效接受回执绑定的 scene 正文、assembled body、单篇 Hook |
| Acceptance / Audit | Guard 报告、Body/Hook receipt；证明的范围与状态明确，不当作语义真源或普通缓存 |
| Delivery Transformation Contract | NameMap v5 |
| Final Deliverable | 最终重写成书 |
| 已购恢复证据 | Preserve checkpoint、request/response 及采用账；与 Derived cache 分开保留 |

以下是目标输出根内的布局示意，不是本次新增目录；具体路径家族、receipt 身份和归档
机制在对应批次一起登记。正文与已购证据不可按“非 semantic truth”批量清理。

```text
stage2/characters/characters.json
stage2/blueprints/chapter_blueprints.json
stage2/compiled/、stage2/acceptance/
stage4/grounding/{reconstruction_brief,semantic_binding}.json
stage4/design/presentation_graph.json
stage4/compiled/execution_plan.json
stage4/context/body/scene_{nnn}.json、stage4/context/hook/hook.json
stage4/acceptance/guard/
stage5/body/scenes/scene_{nnn}.txt、stage5/body/rewritten_body.txt
stage5/acceptance/body/scene_{nnn}.json
stage5/hook/hook.txt、stage5/hook/receipt.json
stage7/naming/name_map.json
stage7/final/<书名>_rewriting.txt
runtime/continuity/、runtime/progress/、runtime/tail/、runtime/cache/
```

正式代码继续按 owner 分包，schema 唯一在 contracts/schema 下；RC 迁移包 F8 分流后
删除。下表是职责落点，不要求预建空目录或把每个函数做成独立抽象层。

| owner | 目标代码职责 |
|---|---|
| Stage2 | characters、blueprints 的语义抽取／确定性编译与 acceptance |
| Stage4 | grounding 的 preserve/bind；design 的 body/hook；单一 graph publisher；compile；body/hook context；机械 guard |
| Stage5 | body writer/judge/assemble；hook writer/validator；必要的公共 receipt 处理 |
| Stage7 | naming assign、机械 apply、final assemble；无故事 resolver 或语义 review 层 |
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

## 阅读验收与 Golden（目标保持）

六维仍为 Core Preservation、Structural Originality、Causal Continuity、Character
Continuity、Natural Progression、Local Prose Coherence。人工阅读为主，模型评分辅助；
至少两本代表性书，其中至少一本长篇或多线结构，冻结输入、baseline、rubric、评价方式
与成本信封。工程绿、planned-only 干跑和机械回执都不替代成文质量，未评保持未验证。

F6 阅读检查 Hook 的冲突、信息升级、悬念及不提前兑现后文；F5 原批次先读连续两三场
再继续全书，接通后合读 Hook、正文开头及所选材料对应场景，检查重复、剧透、转场和
叙述声音，再检查整书人物动机、因果推进、场景节奏、情感与结局回收。
不增加新的逐场审批或自动 taste Judge。新增质量读项沿用六维 rubric 和实际文本证据，
不把“像小说”降成义务逐条复述，也不为结构原创指标强迫搬移情节。

按目标语义位置评价事实、因果、披露与改名质量；Structural Originality 以书/场景及
承载关系比较，不要求同号章对应。F1/F4 证明能力接通，F8 报告真实生成是否使用跨章
自由度及证据；未使用则披露“真实生成中的跨章质量未获验证”。Golden 不代替独立的
current-schema corpus 验收；范围外事项见 REQUIREMENTS，不再保留独立 STR-05 活动入口。

## F8 契约切换清单（准备项）

F8 一次合入时同步闭合，候选可提前准备但不得提前现役化：

1. `ARTIFACT_SPECS` 翻转：characters v4、blueprints v12、graph/binding/plan、
   Body/Hook WriterIR、正文/Hook/receipts、NameMap v5、交付及 runtime 家族；
   NameMap v4→v5 与旧 Stage2/4/5 形状断裂记入版本账本，旧书独立根重建。
2. 公开契约改写：现役 I-40 与 `R-5.2` 本轮不改；目标 N 章连续非空及跨章重构的
   规则在 F8 同批切换，不能只改措辞却仍保留旧判据；
   R-2.*（Characters v4）、R-3.*（Blueprints v12）、R-5/R-6（Stage4 新链）、
   R-7/R-8（Stage5 新链）、R-9/R-10 中消费旧产物的条款同步。
3. `NODES` 与编排：Stage2 抽取、Stage4 Preserve/Body Design/Hook Design/Bind、
   Stage5 Body Write/Judge 与 Hook Write、Stage7 Naming/Assemble 及免费检查/恢复
   面闭合；节点/profile/permit/消费身份双向对账。`--check-only` 沿用免费检查职责，
   不因本文件增加占位 check 节点。
4. 判据注册表与 `docs/schema/` 现役文档：试点 M1–M6 机械 Guard、Judge 判定
   按新坐标正式化；`tests/test_artifact_versions.py` shape-break 账本同批答
   「谁重建」。
5. Stage6 整层及 prepare/hook/approve 节点、三候选/改选/rebuild/历史审批写接口、
   产物家族和 approval bindings 登记 retired；旧 Stage2/4/5/7 替代路径同批删除。
   旧书 `legacy_incompatible` 只读解码，不用旧批准或默认值迁移到新链。
6. API/Views/前端消费新投影，删除 Stage6Screen 与旧操作面，重生 generated types；
   RC 包、Str06Repository、PaidRequestSession 和隔离 harness 接入正式设施后分流删除。
   先迁移仍有效的职责反例，不以删旧测试缩验收分母。
7. 契约 §9.5 五文件、默认非 corpus 回归、前端 test/typecheck/seams/build、Golden
   均按最终改动验收后一次合入；付费与正文外发独立授权。当前三份计划文档修订不改
   AGENTS、BACKEND_CONTRACT、注册表或可执行代码。
