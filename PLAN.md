# R10 · 关键语义保真与 WriterIR 精简 —— 最终执行指南

本指南合并两份计划（R10 执行计划草案、接口与恢复规则计划）并裁决冲突，是 R10 批次的唯一执行依据；
批次、迁移与删除条件仍统一登记于 `docs/REQUIREMENTS.md`，历史缘由进 `docs/DECISIONS.md`（D8 修订三）。
两份原计划被本指南取代。静态代码基线 `7ceffac0`（工作区干净）。

## 一、目标与已定边界

让 AI 主导的流水线稳定自动产出高质量小说：保留现有 14 节点、四类语义真源与 owner 边界，
**不加任何生产语义审阅节点、不新增独立 LLM 调用**；改造重点是源证据可达、关键语义少转述、
跨阶段信息不丢失、写手材料不打架。最终用一本**全程无人工内容 patch** 的新链成书同时检验
保真、阅读质量、自主完成度与成本。

用户已裁决（2026-09-30）：停止 R9c，不申请 Design 追加轮；R9c 与 b6a 两根只读留证；
R10c 的无人工 patch 全书即 B6-B 交付候选，工程核验＋用户通读有效后关闭 STR-06；
新旧 renderer 不做付费对照，只做免费形状读数；独立关键案例由 agent 起草、用户确认后冻结。

**不做**：逐节点语义 reviewer、逐条原子核对、双跑 Preserve、全书状态模拟器、通用时序系统、
关键词/锚点数硬闸、写手回读原文裁决上游、生产文学评分闸。R10-0～R10b 与收口全部
fake transport、零付费；只有 R10c/R10d 付费，付费与正文外发分别授权（I-43）。
分支 `codex/workflow-role-boundaries` 按子批检查点提交（中文 Conventional Commit）。
所有新增校验并入各 owner 现有「一次校验、合并报告」（R8b 形态），同一 owner 同一版本
一轮修完再放行下游。

| 节点 | 继续负责 | 本轮收紧（落点） |
|---|---|---|
| Stage2 | 理解源文，产出人物、事件、状态变化及依据 | 关键判断带逐字引文与限定、事件时态显式；不为填字段补答案（a1） |
| Preserve | 选择必保、成立条件、阶段 | 义务锚定源原子、类型由 compiler 拷贝；只能报证据冲突，不私建事实版本（a2/a3） |
| Design | 目标叙事、场景与呈现 | 锁定的知情/时态/未知不得经状态或说明改写；书级表达不举后段具体（a4） |
| Bind | 义务→承载位置 | 不改义务正文（已成立）；阶段分别承载，缺口默认回 Design（a5） |
| Resolver/Compiler/Renderer | 定位、确定性编译与投影 | 不判断自然语言支持关系、不合并「看起来同义」；只做引用、拷贝、去重（全程） |
| Stage5 | 按章包实现文字 | 不重设计因果与知情；在写手自由范围内实现表达任务（R10b） |

## 二、接口与恢复规则（合并裁决）

### 1. 证据表示（SourceTruth 内，不加新事实权威）

- Stage2 wire 新增 EvidenceQuote{segment, quote}（R10a a1 落地）：quote 是逐字精确引用，resolver 判定
  「quote 逐字存在于所引句段内」且句段属于该拍 `source_refs`；不归一化、不模糊匹配。
- compiler 在 Blueprint **v15** 派生证据视图：被引用句段全文为证据窗口正文；**扩展上下文**
  ＝同章前后各一句；重叠窗口机械合并。精确支持引用与扩展上下文分别保留——**邻句被送达
  不代表邻句自动成为该命题的证据**；跨窗口限定由 Stage2 显式补引。
- Preserve 消费紧凑的 SourceTruth 投影及其证据（a2 新增的 project_preserve_view 纯函数），不另读
  Stage1 全文、不新增摘要调用。
- 沿用已有命题、持有者、认知模式与状态地址，只补关键区别：人物判断与事实、计划／预计／
  条件行为与已发生（`modality ∈ {actual, planned, expected, conditional}`）、源文未说明与
  目标暂未揭示。抽取未决不能自动变成 `unknown_in_source`。

### 2. Preserve 冲突与 owner 恢复（三分支）

Preserve 显式区分正常结果／源证据冲突分支；冲突结果不发布 Brief，按条目 lineage 回派到
负责该源解释的 Stage2 语义 owner（chapters 逐章或 book），SourceTruth 仍由 compile 唯一发布。

| Stage2 处理结果 | 后续动作 | 发布边界 |
|---|---|---|
| **修订** | 发布新版本 SourceTruth；按实际改变的消费材料恢复 Preserve（材料变化＝新请求身份）；沿用原有内容额度 | 旧 Preserve 回答不能直接采用为新源版本的结果 |
| **保留解释** | 处理结果、解释、引用及适用版本作为**同一 Preserve 任务** revise_self 的恢复输入；必须获得新的可发布结果 | compiler 不删除异议、改写义务或宣布语义通过；revise 后仍坚持同一实质分歧 → **停止，不二次回派** |
| **未决**（额度耗尽/无进展） | 保持阻塞、保存证据，按既有停止规则结束本轮 | 不自动标成源文未知，不移入背景后继续 |

身份与状态规则：

- 回执绑定冲突身份（所涉原子句柄＋分歧描述）、报告回答、处理前后 SourceTruth 版本与
  owner 结果；处理状态与后续请求身份耐久保存。重启读到已处理旧冲突：恢复原处理进度、
  采用仍适用的已保存回答，**不重复派发、不重复购买、不判定分歧再次发生**。
- 进入 Preserve 输入的回执内容属于实际消费材料、参与请求身份；时间戳等纯审计字段不进模型输入。
- 新问题仍受现有 owner/item 额度与停止规则约束；处理完成、版本改变或进程重启均不重置额度。
- 误报闭合：对「已维持」原子再次报同一冲突被 revise_self 拒收（源解释归 Stage2 owner）；
  沿用 `OWNER_REVISION_MAX_ROUNDS` 与无进展指纹，不另设额度。

### 3. 状态身份、阶段与顺序

- **状态身份**：确实跨章消费的当前状态，首次由相应语义 owner 明确槽位身份，后续沿该身份
  更新；候选对象、当前计划内容等进入**值**，不通过更换 key 表示状态变化。源侧沿用 associate
  显式同维度关联；目标侧由 Design 建立并复用正式槽位；compiler 只按显式关联归一，不按近义
  文本合并。各章抽取与正文生成保持独立。
- **阶段适用范围**：离散兑现时点的事件／揭示义务分别登记独立阶段（`series`）；稳定身份、
  持续约束和全书表达机制沿用其作用域，**不为满足阶段规则新增确认拍或故事事件**。机械检查
  只保证已登记阶段的引用与映射完整；源文独立阶段是否全部识别归冻结案例与成书评价。
- **顺序三分**：故事发生顺序（如先监测后确诊）是 `requires` 因果前提语义，不机械映射到目标
  场景阅读序；信息披露顺序（先疑点后确认）由 kp/kf 锁位置 Guard 验证；原文讲述顺序不自动
  成为保留要求（合法：先叙述确诊、再回顾此前监测）。compiler 只查显式关系、引用与结构，
  不从自由文本推断时序。
- Bind 保持「引用 → 位置 → 缺口」职责；完整义务由 compiler 复制、不由 Bind 重写；已有要求
  缺目标承载回 Design，不删改要求消除缺口。
- 允许空 `target_delta`，不要求每拍新增读者确认；同一命题、持有者及有效阶段的模式不得
  未经授权升级，后续明确新证据可以支持确认。必保因果与表达功能保留自然语言条件；
  类型传递不替代首次源理解正确性的评价。

### 4. 版本与回答复用

- 全链契约升 `str06-rc12`；改变形状或判据的协议分别升版：book `stage2-book-v3`、chapters
  `stage2-chapters-v2`（names 仍 `stage2-observation-v1`、associate 仍 v2，只因输入投影变而
  换身份）、preserve `stage4-preserve-v2`/policy v5、design `stage4-design-v5`；bind/hook-design
  字面不变；Blueprint v15、Brief/BodyDesign/PresentationGraph/SemanticBinding/WriterBookIR 对应
  rc；Renderer 与写手协议 v6→v7。旧产物不补默认值取得新协议资格；旧根只读、按版本关闭式
  拒识（记「协议不兼容」，不计语义检出）；旧 renderer 仅用于离线对照。

| 变化 | 处理 |
|---|---|
| 实际模型输入未变，已购回答耐久保存且仍适用 | 复用回答；重启不重复购买 |
| 验收条件变化，模型输入未变 | 免费机械复验；失败按既有恢复规则处理 |
| 实际写作材料变化 | 按请求身份与修订规则判断是否新回答 |
| 仅审计或展示信息变化 | 不触发付费重写 |

只承诺**未受实际消费材料或验收条件变化影响的章节不重购**；上游修订可能影响多章，变化后
重新汇集当前有效正文和回执、更新 Stage7 交付判定。

### 5. 对照与矩阵（裁决）

- 用户已裁决不做新旧 renderer 付费对照。R10-0 核验（2026-10-01）：现盘面
  **不存在任何可复用的现版本成书**——b6a 运行根（20260929b／20260930）已被
  清理、无盘面可读，仅余 REQUIREMENTS 历史记录（三章＋Hook 成文读数、41 笔
  付费账、两次 owner patch 事实）；R9c 未成文。故实验对照取：R9c 语义层基线
  读数（冻结证据清单）、b6a 历史记录（只作背景参照，不能宣称完成逐段对读）、
  同一合成绿基座上 v6/v7 renderer 的免费无损投影对照（共同支持的离线夹具，
  不新增生产迁移层、不拿不同 Design 产物比 renderer）。
- 版本结论只报**绝对达标**（Golden 六维＋冻结关键案例＋九维），不宣称相对旧
  版的付费对照改善；对照缺失如实登记。普适性由 R10d 第二本书＋重复生成披露，
  不外推。
- 每书目标矩阵：一份对照（上述免费基线）＋两份独立新稿（R10c 首稿＋R10d
  重复生成）；不扩大样本、不择优替换失败轮。

## 三、批次与顺序

```text
R10-0 基线/真实消费/反例集/关键案例（免费）
 → R10a 关键语义交接（a1 Stage2 → a2 Preserve → a3 证据冲突回派 → a4 Brief 锁定＋Design v5 → a5 Bind/compile 位置校验）
 → R10b WriterIR 投影与写手协议 v7（夹具先行）
 → 收口：§9.5 五文件＋受影响 stage＋fake 产品链＋默认非 corpus 回归＋前端四件 → 冻结付费基线
 → R10c 付费无人工 patch 全书（c1 语义层段 → 免费核读 → c2 成文段 → 工程核验＋用户通读）→ STR-06 关闭
 → R10d 第二本（三人成行）＋重复生成（另行授权，含 golden CRITERIA 更新）
```

### R10-0 · 基线、真实消费与反例集（免费）

1. **登记**：REQUIREMENTS 重置当前状态（R9c 停止、批次表改 R10、B6-B 并入 R10c、B6c 并入
   R10d、R7 余项由 R10c 阅读问题驱动）；DECISIONS 增 D8 修订三；不写旧运行根。
2. **冻结证据**：R9c 根（`outputs/str06_r9_canary_20260930/`）全文件摘要清单，
   逐项标注产物所属 checkpoint（正式 Design/Bind 属首轮、checkpoint/brief 属
   补丁后、`runtime/review/` 是未采用人工草案）；b6a 根经核验已从盘面清除
   （2026-10-01），其三章＋Hook 成文与 41 笔付费账只余 REQUIREMENTS 历史记录，
   作背景参照、不能宣称完成逐段对读；给出对照可复用性判定（§二.5）。
3. **真实消费核对**：`tools/str06_review.py::request_preview`/`views` ＋
   `novel/llm/budget.py::count_input_tokens`，逐 owner 捕获实际请求，分解规则／材料／JSON
   冗余占比；区分「落盘重复」与「付费重新生成」。计数与结构登记 REQUIREMENTS；含正文的
   请求明细不落仓库、不写旧根。
4. **送达守卫修补**：`tests/test_str06_semantic_delivery.py` 增自检——凡登记写手消费者的
   字段必须有变异用例且改变**渲染字节**（`render_chapter_packet`）；`dramatic_intent` 现状
   如实改登记（登记写手可见但从不渲染），R10b 删除该字段。
5. **反例回归集**：新增 `tests/str06_semantic_cases.py` 构造器（合成最小样例，付费回答与
   正文不做 fixture），每例写明源类型、期望 typed 结果、由哪个子批转红/绿：C1 眼神猜测→
   他人确知＋读者确认；C2 预计时长→已发生；C3 两时点合并；C4 引文错位；C5 多次意外现身
   合并；C6 线索拍强加读者确认；C7 背景历史回声；C8 全书表达举后段具体（仅称谓部分机械
   可查）；C9 原文未知被补答；C10 生命周期残留（只读数）；C11 救人动机遗漏（机械不可查，
   归关键案例）。**合法正例**：跨章证据支持同一次揭示；先怀疑后新证据确认；「她原以为如此，
   后来终于证实」不被关键词拦；第三人称对白「我」；先叙述确诊、再回顾此前监测（顺序三分）；
   空状态变化拍。
6. **独立关键案例**：agent 依 `Test_data/Workbench/Golden_Set_v2.1_Template_STR06.md` 起草
   `Golden_Set_v2.1_夫君的报恩.md`（P/T/SIG/LIM/GS，稀疏锚点）；种子＝REQUIREMENTS「B6
   沿用的保真与交付反例」＋R9c `reading_review.json` 问题＋C1–C11＋b6a 六维验收发现；
   评价依据不能只取自待评估的 Preserve 条目。**冻结时序（2026-10-01 用户裁决）**：起草
   不阻塞任何批次（与 R10-0.3/R10a 并行）；用户不预先确认——c1 段它仅作 agent 免费核读
   的工作判据，用户只看核读结论决定是否进 c2；与全书通读一并确认，确认即冻结为本版
   验收判据（R10d 与 STR-06 关闭以该确认为准）。
7. **基线读数**：R9c ledger 逐节点 token（Preserve/Design/associate 轮次；
   b6a 账面只剩 REQUIREMENTS 历史记录，无盘面可重算）、R9c 草案
   章包分区条目与重复率，作为 R10c 对照与 R10b 免费形状对照的 v6 基准。

### R10a · 关键语义交接（免费开发）

**a1 Stage2：引文证据、事件时态、命题纯度**
- `contracts/schema/source.py`：新增 EvidenceQuote{segment, quote}；`Effect`/`Cognition`/
  `InitialStateAtom`/`WorldConstraint` 必填 `basis`（观察与 canonical 共用同一模型，I-11）；
  `Effect` 必填 `modality` 闭集。Blueprint v15 派生证据窗口（§二.1）。
- Resolver（`novel/stages/stage2/resolver.py`）：复用 `check_closed_set`/逐字查找，新增引文
  判定；失败复用 `missing_literal`/`unknown_segment` → `revise_self`。
- Prompt（`novel/stages/common.py`）：引文逐字、含决定判断的限定（以为/觉得/像是/预计/约/
  几天后），不得只摘人名或动作词；modality 规则；命题 statement 写命题本身、认识限定只进
  mode。状态槽位身份规则（§二.3）同步进 prompt 与规则。
- 读数进 `stage2/acceptance/fold.json`（度量不是门）：引文长度分布、引文等于称谓字面、
  命题含限定词比例、modality 计数。

**a2 Preserve：证据视图、原子锚定、阶段身份（preserve-v2，policy v5）**
- 新纯函数 project_preserve_view（R10a a2 落地于 `novel/stages/stage4/grounding.py`）：按章/拍确定性渲染
  event、证据窗口（精确引用＋扩展上下文）、带句柄的原子（`b06_21.e1`/`.c1`/`.l1`、`s0_*`、
  `wc_*`）及类型与引文，取代 JSON 倾倒；不摘要、不改写；实测 token。
- wire：义务 `{kind, statement, must_preserve, satisfaction, anchors:[句柄], requires:[序号]}`；
  顶层 `series:[{members:[序号≥2], mechanism: ex_id|null}]`、`evidence_conflicts:
  [{anchor, segment, note}]`。`lineage` 退出 wire，由 compiler 从锚点派生（I-15/I-21）。
- `contracts/schema/grounding.py`：`SourceObligation` 增 compiler 类型化拷贝的 `anchors`
  （持有者、mode、命题、效应目标/值/modality/可见性、引文）；`PreserveCheckpoint` 增 `series`。
- `PRESERVE_RULES`（policy v5）：每条义务锚定其依赖的原子；揭示义务只在源有读者确认时要求
  确认，线索/怀疑阶段合法；复合义务拆阶段并用 series 登记同一机制的多次发生；原文未知保持
  未知；必保与展开重点是两回事（不以压条数为目标）。
- `assemble_preserve` 校验：锚点闭集可解析；series 成员闭集、互斥、mechanism 属源表达；
  冲突引用的锚点与句段在闭集且句段属该拍证据。

**a3 证据冲突的有限回派（§二.2 三分支的工程落点）**
- `contracts/schema/workflow.py`：新码 `source_evidence_conflict` → `revise_upstream`
  `str06.stage2.chapters`（item=`chapter:N`，owner=`chNN`）或 `str06.stage2.book`；
  `contracts/invariants.py::NODES` 为两节点登记 `operator_redo='owner_revision'`；
  `novel/stages/chain.py::_REVISION_TARGET_OWNERS`/`OWNER_PRODUCT_RELS`、
  `novel/orchestrator/state.py::resume_verdict` 支持逐章 owner。
- `run_preserve`：冲突非空 → 回答照常耐久、checkpoint 不发布，`discover_owner_issues` 后抛
  `OwnerRevisionRequired`；章观察修订 prompt 带冲突说明与原句（复用 `revision_prompt_for_owner`）。
- 冲突回执按 §二.2 持久化（冲突身份、owner 结果、前后 SourceTruth 版本）；Stage2 重编译后
  Preserve 重跑 prompt 附确定性「冲突处理结果」（对比所涉原子前后：已修订／Stage2 维持原解释）；
  对「已维持」原子再报同一冲突 → revise_self 拒收，耗尽即停止不二次回派。

**a4 Brief 锁定项与 Design v5（design-v5，Brief rc12）**
- `project_brief` 由锚点确定性派生**锁定项**，Brief 仍源盲（无引文、句段、拍号）：
  命题登记 `kp_<hash>`（仿 `obligation_core_id` 铸盲句柄）＋知情锁 `{持有者|reader, kp, mode}`，
  模式映射唯一登记（learns/confirms→known、suspects→suspected、hears_claim→heard、
  believes→believes；asserts 转「某人公开声称」事实锁）；时态锁 `kf_<hash>`（modality≠actual
  及 op=unknown 的效应）；原文未回答锁（compiler 从 canonical loops 判定）；requirement 带
  盲 series 标记与 mechanism。
- Design wire v5（`contracts/schema/design.py`）：非知情/非读者 set 必填 `modality`；拍增
  `expression_refs:[ex_id]`（正向表达任务落具体拍，书级表达只写机制）；**删除
  `dramatic_intent`**；知情与读者确认涉及登记命题时 `key` 必须用 `kp_*`，时态锁用 `kf_*`；
  槽位身份规则——目标状态沿正式槽位更新，不换 key 表状态变化。
- `DESIGN_RULES`：读者确认只用于已授权的揭示，线索/怀疑拍可以没有状态变化；事件不照抄
  义务；书级风格/表达/声线不举后段具体情节；知情状态是约束不是「目睹记录」；当前计划用
  同键替换；原文未回答的问题不得作答。
- `assemble_body_design` 新增接受校验（`revise_self`，同节点≤2 轮）：kp 键知情持有者须有锁、
  模式属该持有者被授权的模式集合（known 只能来自 known 锁）；必保义务的知情/时态锁须被对应
  键兑现；kf 键 modality 与锁一致；原文未说明的 kf 键不得 set。

**a5 Bind 与 Stage4 compile：阶段与位置**
- Bind wire 形状不变；`project_binding_space` 带出 series 与锁摘要；`BIND_RULES`：同一 series
  成员绑定不同拍；必保缺口默认归 design。
- `assemble_binding`：series 成员同拍 → `series_collapsed`（`revise_self`）。`run_bind` 把
  接受闸接进 `ask` 同节点修订轮（≤2），耗尽才写 `.INVALID` 孪生并停（R-5.5 同批改写）。
- Stage4 compile 新 Guard（进 `novel/stages/stage4/guard.py::GUARD_CHECKS`）：kp 键知情升到
  某模式的位置不得早于授权该模式的义务承载位置（信息披露顺序）；读者确认不得早于对应锁的
  承载位置；违例 → 新码 `epistemic_lock_order` → `revise_upstream:str06.stage4.body_design`
  （带位置上下文）。故事发生顺序不做机械 Guard（§二.3 顺序三分）。

### R10b · WriterIR 投影与写手协议 v7（免费开发，夹具先行）

1. **先冻结完整章包夹具，再实现 renderer**：夹具含实际发给 Writer 的完整输入（非单分区），
   展示：因果与公开说辞/真实判断并存保留；既成事实作为约束送达、不变成本章重复介绍任务；
   言行反差保留为表达目标、不强制写成解释性总结句；本章入口状态、知情边界、必要承接、
   退出位置与统一表达约束齐全。
2. IR（`writer_book_ir` rc12，`contracts/schema/context.py`/`book_ir.py`）：义务带承载拍与
   支撑拍；`BeatIR` 增承载于此的锁（知情边界、时态、原文未知）与 `expression_refs` 展开的
   表达任务；删 `dramatic_intent`；Machine IR 完整保留供审计。
3. 背景投影（改 `novel/stages/common.py::participant_background`）：只投**书级初态立足点＋
   后文仍被消费的当前状态**（消费者＝声明读取、读者确认目标、Hook 起笔/隐藏/禁揭地址、锁键）；
   unset/unknown 不作背景；只写一次且无消费者的拍状态不再向后投影（留在其拍「必须写出」
   与 Machine IR）；局部条件停止向后投影≠宣布故事中已解除。
4. Renderer v7（`build_writer_packet`/`render_chapter_packet`）：必须写出按「场→拍」组织
   （事件＋承载于此的义务满足条件＋拍末结果＋锁）；义务 statement 仅在与事件逐字相同时省略
   （按同一条目去重，不按相似度删）；书级不可违背每章一次；称谓许可只在变化处列；明确禁止、
   开放线索、禁区按章去重；背景章首一次、场内只补新入场人物；原文未回答锁从承载位置起进
   禁区；时态措辞「计划/预计，尚未发生」；kp/kf 句柄绝不进阅读面（构造期守卫＋
   `material_identities` 增该模式）。条目可拆分控制，但正文不要求逐条对应句子/段落/独立场景。
5. 写手协议 v7（`WRITER_PROTOCOL`/`BODY_WRITER_SYSTEM`）：三类——必须写出／必须遵守不必
   复述／写手自由；场景是控制单元，不要求每场独立铺背景、收束、总结；反差通过话语与内心
   反应呈现，不加总结句；怀疑不写成确知、计划不写成已发生、原文未交代的不补答案。章包保持
   自足，不把跨章连续性重新交给 Writer。
6. Hook：沿用独立素材授权；Hook Design 地址引用随 Design 键闭集（含 kp/kf）；HookContext
   按 value 送达、锁键不进阅读面。
7. `contracts/schema/consumption.py::STR06_FIELD_DELIVERY` 同批更新；`packet_metrics` 增
   离线 token、章内重复行比例、被剔除历史回声数、锁数（audit_only）。
8. 免费形状读数：同一合成绿基座上 v6（R10-0 记录）与 v7 分区条目、重复率、token 对照；
   R9c 草案章包作旧形状参照。投影守卫全绿、读数合理即收口，不为调试视图次要问题延后成书。

### 收口 · 工程验收与付费基线冻结（免费）

- 版本同批登记（§二.4 全表）；`tests/test_str06_f7e_freeze.py` 重钉契约身份与
  `FROZEN_SEMANTIC_PROTOCOLS`。
- 判据登记：BACKEND_CONTRACT R-2/R-5/R-6 按节续号（引文定位、modality 闭集、锚点闭合、
  series、证据冲突路由与三分支恢复、知情/时态锁、锁位置 Guard、背景投影规则、回答复用四
  条件），§五计数，`contracts/rules.py` 注册，`tests/hard_cases.py` 每条 HARD 红例＋绿对照。
- 文档：`docs/schema/str06.md`（R10 节）、`docs/schema/stage2.md`/`stage4.md`、
  `novel/stages/stage2/AGENTS.md` 若 S2 局部不变量变化。
- 验证：§9.5 五文件 → 受影响 stage（Stage2/4/5/7、R3 回派、章闭包）→
  `tests/test_str06_product_entry.py` 真实子进程 fake 全链到交付与重启读回 → 旧根关闭式拒识
  （不计语义检出）→ 默认非 corpus 回归（冻结付费基线门槛）→ 枚举/API 形状变（新 issue 码进
  `backend.generated.ts`）补前端 test/typecheck/seams/build。

### R10c · 付费无人工 patch 全书（B6-B，分段授权）

- 新根 `outputs/str06_r10_<日期>/`，经真实产品入口（HTTP 上传→AgentRun→CLI 子进程）；
  `NOVEL_DATA`/`NOVEL_CORPUS` 指 Workbench，`NOVEL_OUTPUT` 为新根；默认 flash/high/并发 4。
  开跑前报价清单（代码/协议/配置身份、基础调用 2N+9、额度）落 `runtime/review/`；付费与
  正文外发分别授权。版本、预算、停止条件先冻结再开跑。
- **禁止 owner 内容 patch**。允许的干预只有：确定性缺陷修代码后免费重放、显式带理由重采、
  追加额度——每次记入自主完成度，不冒充自动通过。
- c1 语义层段：Stage2→Stage4 compile，暂停在 `str06.stage5.hook` 前；免费核读
  （`tools/str06_review.py views|obligations`，判据＝关键案例工作清单，未冻结）；
  核读结论交用户决定是否进 c2；出现重复、高损失错误模式则回 R10a/b 修并开新根，
  不在本根打补丁。
- c2 成文段（另行授权）：Hook＋全部 11 章＋Naming＋Assemble。
- 工程核验：章连续非空不增删并拆重排、回执与 seal、改名零残留、manifest/hash 与下载字节、
  `--check-only` 零新购重放、服务重启读回、ledger。
- 阅读验收：agent 按九维协议＋关键案例逐条归因（owner/位置/期望/实际/证据）→ 用户通读，
  通读时一并确认案例清单（确认即冻结为本版验收判据），结论绑定交付 hash；有效后执行
  「STR-06 关闭与退役尾项」。

### R10d · 普适性（与 B6c 合并，另行授权）

- 先更新 `tests/golden.py::CRITERIA` 为六维并冻结 manifest；《三人成行》新根全链（对照其
  v2.1 oracle）＋至少一次《夫君的报恩》重复生成（独立空输出根、同候选版本固定源文/代码/
  协议/模型/预算/停止规则、不采用上一轮已购回答），区分「对反例书定制」与「普适改善」；
  模型 A/B 只在此后按瓶颈层单独做。

## 四、硬闸／诊断／语义评价

| 类别 | 条目 |
|---|---|
| 确定性硬闸（进有界修订） | 引文不在所引句段；句段越界；锚点/series/冲突引用不闭合；series 成员同拍；kp 键持有者无锁或模式越权；必保锁未兑现；kf modality 不一致；原文未说明被 set；知情升级/读者确认早于授权位置；kp/kf 句柄进阅读面 |
| 诊断读数（不阻断） | 引文过短/等于称谓；命题含限定词；Design 自由键知情与读者确认数；只写一次无消费者状态数；跨章义务数、每义务锚点数、必保比例；章包 token 与重复率；证据冲突次数；Design 新增状态键数/槽位复用率 |
| 语义评价（不宣称机械保证） | 原文是否支持某动机；反差/幽默是否保住；正文是否新增改变因果的情节；表达是否好看——归冻结关键案例与通读 |

## 五、验证与验收

### 测试证明方式（每项测试标明）

| 证明方式 | 可以证明 | 不能据此证明 |
|---|---|---|
| 确定性 unit／contract／stage 测试 | 引用存在、版本一致、已登记阶段映射闭合、模式继承、材料送达及真实消费者行为 | 初始源解释正确、阶段抽取完整、自然语言无矛盾 |
| fake transport 回派测试 | 收到冲突后能正确归因、修订、恢复、复用及停止 | Preserve 面对真实错误会主动报告冲突 |
| 真实生成与全文评价 | 本次样本是否产生错误、是否发现并修复、成书是否合格 | 对任意小说均有相同表现 |

定向测试覆盖（正反例）：证据（精确引用与邻句区分、跨窗口双引用、窗口合并、源版本不匹配、
定位失败）；语义继承（怀疑与确认、听到说辞与相信、计划与已发生、留白与隐藏答案、空状态变化、
合法后续确认）；身份与状态（候选对象换值不换槽、历史记录保留、局部条件停止投影、仍被消费
的状态不误删）；阶段与绑定（多次独立发生分别承载、跨章证据支持同一阶段、稳定身份无需确认拍、
漏绑与 Design 缺口回派）；顺序（Policy 允许时先叙述确诊再回顾监测合法、把开始监测写成已确诊
是保真反例）；异常恢复（修订源版本、保留解释改变恢复身份、未决停止、旧记录重启不重派、新回答
复现分歧停止、崩溃恢复位置）；WriterIR 与交付（完整请求夹具、独有因果不丢、既成事实不强制
复述、无阶段剧透、材料变化影响范围、验收条件变化免费复验、Stage7 用当前有效结果）。

执行遵守 `tests/AGENTS.md`：`novel_refactor` 环境、绕开沙箱、复用 `.tr`/`E:/_tr`、
`--junitxml` 留档、不并行共享 basetemp；子批先定向，§9.5 五文件在形状稳定后，收口一次默认
非 corpus 回归＋前端四件；默认 fake transport，付费生成与 Golden 单列。

### 成书评价与结果分类

- 沿用 Golden 六维发布标准与九维阅读协议，不新增评分体系；每份新稿独立评价关键源例与全文，
  结论绑定最终文件 hash；未读部分或未验证结论保持 `unver`。
- 反例结果至少三分类记录：错误未再出现／错误被系统发现并修好／错误仍静默进入下游。停止、
  未完成、未评价另列，不计入「错误已消除」。SourceTruth 错标 known 后下游正确继承＝工程
  传递通过、源语义仍可能失败，分开表述。
- 自主完成分类：**原定规则内自主完成**（预算/修订/停止规则预先冻结；无人工内容 patch、基于
  质量的人工重采、临时加额度或影响内容选择的人工决定）／**无人改稿、有人辅助完成**／
  **未完成**。纯运维重启单独标记，不省略。
- 两个最终结论分开：**评测完成**（规定运行的结果、失败、干预、评价与成本记录完整；系统性
  错误导致剩余运行未启动时标记中止与未执行单元）与**版本通过本轮样本验收**（每书两份新稿
  分别达标；不能挑最好的一份、不能用辅助重采覆盖原轮失败）。对照缺失不宣称相对改善（§二.5）。
- 防作弊分母：同时报义务总数与必保比例、未知锁数量、停止次数；不以删义务、标未知或中止
  运行换通过率。

### 成本

以 `_service/ledger.jsonl` 为权威，逐轮记录付费调用、缓存、自动修订、失败尝试、耗时与人工
干预；未知不作零、reasoning 不重复计入。每份可接受成书成本按该版本评测总费用分摊（含失败
尝试与自动修订）；旧版对照与一次性研发费另列；合格成书数为零时报告总投入和零成功。
参考量级：R9c 到 Stage4 的 37 次实付约 ¥3.97（估算），全书预计数倍，以开跑前实际报价为准。

## 六、风险与开放点

- 严格逐字引文可能抬高 Stage2 修订轮与停止率：R10c 报首轮接受率；系统性失败改 prompt 的
  原句展示方式，不放宽匹配。
- Design 输出增加（modality、kp/kf、expression_refs）对 128K 上限的影响：删 `dramatic_intent`
  对冲，R10c 报 completion；截断按 D6 停在 item。
- 类型化校验只覆盖「已链接」项：Design 用自由键重复表达同一命题仍需阅读发现（诊断读数提示）。
- 证据冲突回派新增一条付费例外路径：有界、可观测；误报由「Stage2 维持原解释即不得再报」闭合。
- 单本开发书过拟合：由 R10d 第二本与重复生成披露，不外推。
- **风格画像（开放项，本批不动）**：b6a 已证实四条风格批评（一句一段、环境密度、幽默通道、
  POV 漂移）根因在语义链 source-blind 且无风格抽取字段；两份计划均未纳入。登记为 R10c 通读
  后按需启动的候选批——若 R10c 成书风格批评复现，即以该批评为动因立项（落点候选：style_profile
  抽取进 preserve/design，或 withheld 源文样本进 writer；均需用户裁决）。R10b 协议 v7 只吸收
  与材料组织直接相关的项。
- 工程余项：b6a 复盘的直连强制重采（同身份缓存命中致重问无效）、REDO 级联自动重放（R-14.4/
  E-1 产品裁决项）维持登记，不在 R10 主线扩散；R10c 按「允许干预」分类如实记录命中情况。
