# 以小说质量为中心的 STR-06 流水线重设计计划

## Context

**优先级**（用户明确）：高质量重写 ＞ 稳定自动成书 ＞ 工程优雅。

**审计结论**：骨架是对的，但"最后一公里"断了——
- 付费语义没有送达写手：拍级读取值漏包（AUD-03）；四类跨章字段没有消费者（AUD-04）；
  世界约束没有通道（`ReconstructionBrief.book_constraints` 恒空）；章包没有「承接」。
- Stage2 关联投影丢了作用对象，状态句柄会碰撞（AUD-01/02）。
- 系统不会自愈：v13 验证轮 5 次首响应里，ch04 那次有 4 类错误被闸拦
  （见 `docs/DECISIONS.md` 2026-09-26「v13 验证轮」行）；F9 停在 body_design 之前。
- 发布权威名不副实：读取路径不看 seal（AUD-05）。
- 新链从没接过真实模型。

**新增的核心心智模型**："语义送达 Writer"不等于"Writer 拿到了最好写的上下文"。

- Stage2/4 负责把故事想清楚。
- Compiler 负责把依赖算完整：Machine IR 以完整为先。
- WriterPacket 像一位好编辑：把本章真正需要的东西，按正确的优先级交给写手，以注意力为先。

如果背景、保护项、待写事项在写手面前同权，就会写出"事实全对、处处交代、悬念复读、像在完成检查表"的 AI 文。

**关键时机**：盘上最新付费记录是 2026-09-26 的 `str06-rc4`（旧协议），新协议没有任何已购产物。所以：

- 语义层协议（Stage2 观察与关联、Preserve、Design、Bind、Hook Design、canonical schema）在 B6-A 之前一次改完并冻结。
- 写作层（renderer、WriterPacket 呈现、写手协议）允许在真实 canary 之后再收敛——fake transport 永远验证不了"好不好写"。

**与 REQUIREMENTS 提案的关系（2026-09-28 吸收登记）**：`docs/REQUIREMENTS.md`「目标导向整改与优化执行方案」的 A0–A6 提案已吸收改写为本计划的批次登记，不另立第二份活动计划；该节保留为细则素材（AUD 反例、发布协议细节、验证矩阵与执行配方），不再是并行提案，冲突处以本计划「已裁决」为准。对应关系：A0→R0（AUD-01/02 随 R1、AUD-03/04 随 R2、AUD-05 随 R6 转绿）；A1→R1；A2／A3→R2；A4 发布与恢复→R6，其中 A4a「现役记录受限采用」不立项——新协议无已购产物（见「关键时机」），B6-A 实购重建，旧盘面只读保留；A5→R4＋R5；A6→R5；B6→B6-A／B6-B；「后续优化」表→R7。未单列残留维持现役：Binding 纯 edge 不做（见「保留不动」的义务快照）；同址多次改写后的读者确认版本规则不变，R2 落地时若需 owner 显式引用历史版本，先经用户裁决。14 节点是默认执行拓扑而非不变量（裁决 4）。本计划的全部裁决由 R4 批写入 `docs/DECISIONS.md`，各批交付记录按根指南登记进 `docs/REQUIREMENTS.md`。

## 已裁决（2026-09-28）

1. 允许有上限的自动付费修订，含跨节点回派。每单元最多 2 轮，同一问题集重复即停；**只处理 typed、可定位的机械问题，文学质量永不进入自动修复**。
2. **统一发布权威（R6）放在 B6 之前**，让 B6 真正代表"最终产品链第一次接真实模型"。这一条取代本会话问答中"放在 B6 之后"的回答。
3. 跨章字段改用**独立线程结构**。
4. 冻结的是四类语义真源与 owner 边界。**14 节点只是默认执行拓扑，不是不变量**：落实 owner 边界、写作质量或故障恢复需要时可以调整。
5. B6 拆成两步：**B6-A 语义与写作 canary**，再 **B6-B 全书**。
6. 采用 **Machine IR 与 WriterPacket 两层**，由 `delivery_mode` 决定每项内容以什么身份、什么显著度送达写手。
7. 章间承接由 TargetDesign 声明一个很薄的 `entry` 字段（衔接方式），编译器不猜。
8. 研发期设冻结的人工阅读维度与归因记录；生产环境不设 LLM 评审，也不设文学评分闸。

## 设计原则（按优先级）

1. **WriterPacket 按注意力组织**：Machine IR 保持完整（ID、地址、来源、依赖边、Guard 材料）；WriterPacket 是编辑写的章纲，不出现工程词汇。
   - 背景是约束，不是写作任务。
   - 保护悬念不等于提醒悬念。
2. **无死语义，且送达方式正确**：每个语义字段都要登记消费者、影响范围和 `delivery_mode`；用变异测试验证"送到了哪一节、以什么身份出现"。
3. **依赖由编译器推导**：在 Design 声明的读取之外，按参与者机械补齐入场状态，但补齐部分以"背景"身份呈现。
4. **跨章语义都是拍上的转移**：状态走现有叠加折叠，线程走独立 ThreadFold，不留静态列表。
5. **有上限的自愈只修机械问题**。
6. **分层冻结**：语义层在 B6-A 前冻结，写作层经 canary 收敛后再冻结。
7. **先量后优**：运行指标之外加 WriterPacket 指标，先采数、不设阈值。

**保留不动**：D1–D11；四类故事语义真源（Characters／Blueprint／TargetDesign／SemanticBinding，`docs/REQUIREMENTS.md` 术语锚点；**不是** R2.4 删除的四个静态跨章字段）与 owner 边界；Design 源盲；章写手与章观察各自独立；Binding 的义务快照（只修文档声明）；不设生产语义 Judge。

## 执行顺序

| 批次 | 目标 | 付费 | 改请求身份 |
|---|---|---|---|
| R0 | 语义送达与送达方式守卫，故障注入底座 | 否 | 否 |
| R1 | Stage2 关联无损（associate 协议 v2） | 否 | 是 |
| R2 | Stage4 语义送达＋两层 IR（Design v2、IR rc9、renderer／写手协议 v5） | 否 | 是 |
| R3 | 自愈：typed issue＋有上限修订（只修机械问题） | 否（运行期会付费） | 否 |
| R4 | 漂移收口、文档与阅读验收协议登记 | 否 | 否 |
| R6 | 统一发布权威（seal），验过才发布 | 否 | 否 |
| R5 | 最终零成本产品回归（首次接真实模型前的基线） | 否 | 否 |
| R5b | 付费路径遗留项收口（选章参数、逐章留证、Guard 判据登记、HTTP→CLI→HTTP 端到端验收） | 否 | 否 |
| 语义冻结闸 | 从合入后 main tip 列 Stage2/4 canonical 与协议版本，裁决 D8 后冻结（B6-A 之前）。**2026-09-29 完成**：用户裁决 D8 按现状冻结、`world_constraints` 不拆；冻结清单与机械拦截（`tests/test_str06_f7e_freeze.py` 语义协议钉）见 REQUIREMENTS「语义冻结闸」条 | 否 | — |
| B6-A | 语义层实购＋全部章包＋Hook 与 3 章 canary；写作层收敛 | 是（单独授权） | 仅写作层 |
| B6-B | 全书成文、交付与用户通读 | 是（单独授权） | — |
| R7 | 按归因问题与指标优化，golden | 视项目 | 视项目 |

每批都"先写反例（红）→ 修 → 绿"，反例与修复同批提交。

拓扑基线：14 节点（`contracts/invariants.py::NODES`）是**默认执行拓扑，不是不变量**（裁决 4）。为落实 owner 边界、写作质量或故障恢复需要调整节点表时，同批闭合 `NODES`、契约、测试与文档；四类语义真源与 owner 边界不在调整面内。

### R0 语义送达与送达方式守卫
在 `contracts/schema/consumption.py` 现有六族投影之上增加**字段级送达登记**。每一项登记：

- 字段路径；
- 消费者：写手章包分区、HookContext、Naming、验证器，或仅审计；
- 受影响章的范围规则；
- `delivery_mode`，可以随投影规则逐场决定（例如同一条线程，有本场操作就是"必须写出"，否则是"延续"或"禁区"）。

顺带把族登记里已退役的节点名改成现役名。

`delivery_mode` 与现有内容的对应：

| delivery_mode | 写手怎么用 | 装什么 |
|---|---|---|
| `must_realize` | 在正文中实际写出来 | 本章拍的事件与目标效应；本场义务的满足条件；本场的线程操作 |
| `must_respect` | 不得违背，不必提及 | 本场依赖的事实（入场与拍级读取）；知情边界；称谓许可；世界规则；场景 avoid |
| `continuity` | 自然延续，不复述 | 承接（上一章末场＋本章 `entry`）；与参与者相关、但本场没有操作的开放线程 |
| `creative_guidance` | 创作方向 | 导演说明、场景驱动、书级风格、人物声线、拍级表达提示、章目的 |
| `background` | 需要时才用 | 编译器按参与者补齐的入场状态 |
| `guard_only` | 绝不主动提示、不提前兑现 | 未到时点的读者确认；开放期间受保护的问题线程 |
| `audit_only` | 写手看不到 | ID、地址、来源（`established_by`）、依赖边、Guard 材料、禁用字面（只给验证器） |

新测试模块 `test_str06_semantic_delivery.py`（入 tests/ 目录，随 R0 创建并登记目录表），四条检查：

1. canonical 字段未登记即红。
2. 变异一个字段后，只有登记范围内的章，其写手视图摘要发生变化。
3. 变异值只出现在它 `delivery_mode` 对应的分区里。
4. 写手视图里不出现任何 `audit_only` 内容，例如 `sc01#b02` 式 ID、`established_by`、字段名。

另外准备故障注入 fake：脚本化的模型式错误（证据 id 越界、未知 slug、漏配对、必保义务未承载、wire 多键），供 R3 与产品链测试复用，沿用 `tests/test_str06_product_entry.py` 的 sitecustomize 注入路径。

### R1 Stage2 关联无损（只升 associate 协议）
- `novel/stages/stage2/associate.py`：
  - typed 目标显式序列化：world 写成显式类型，关系保留方向；
  - 状态句柄改为位置编码 `ch{nn}.b{拍}.e{效应序}.{key}`，book 初态用独立命名空间。
- 抽出唯一的关联登记表，prompt、闭集核对和 `novel/stages/stage2/compile.py::_StateClasses` 共用；构建时重复句柄即拒。
- `contracts/schema/observation.py`：associate 改用独立版本 `stage2-associate-v2`，其余三类观察的请求逐字不变。
- 完成：
  - AUD-01/02 转绿；
  - 同对象跨拍、不同对象同键、反向关系、同拍多效应、未决配对都有正反例；
  - 句柄数量守恒。

### R2 Stage4 语义送达与两层 IR

1. **两层 IR**
   - Machine ChapterIR（`contracts/schema/book_ir.py`、`context.py`）保持完整，照旧落 `stage4/ir/writer_book_ir.json`，供审计和 Stage7 核对。
   - 新增写手侧 `WriterPacket` 模型：按 `delivery_mode` 分节，全部是自然语言条目，不带内部 ID 和字段名，由确定性 renderer 从 Machine IR 加送达登记生成。
   - `novel/stages/common.py::render_chapter_packet` 改为渲染 WriterPacket，不再把 VerificationIR 以 JSON 倾倒进 prompt。
   - **身份拆分**：
     - 章回执分别绑定写手视图摘要（含 renderer 与写手协议版本）和验证输入摘要（禁用字面、称谓许可、篇幅带）；
     - Machine 摘要只作审计，只改 Machine 层字段不会触发重购；
     - `novel/stages/stage7/assembly.py::verify_chapter_receipts` 同步。
2. **统一"已解析读取"（AUD-03）**
   - `scene_read_views` 对入场读取和拍级读取都产出解析结果；拍级读取的值挂到对应拍（`contracts/schema/context.py`），以 `must_respect` 呈现"本场依赖的事实"。
   - Guard（`novel/stages/stage4/guard.py`）新增 M7：每条声明读取都已解析并进入 IR。
   - `novel/stages/stage4/compile.py` 的依赖边改由实际解析来源生成，删除 `last_writer` 扫描。
3. **编译器补齐的基线**
   - `project_context` 按参与者机械补入入场状态：人物域、参与者之间的关系域、持有者为参与者的知情域；
   - 补入内容以 `background` 呈现，写手协议明写"背景事实是约束，不是写作任务；除非场景自然需要，不复述"。
4. **跨章线程（独立线程结构，替换四个死列表）**
   - **模型**：`TargetBeat` 新增与源侧 `loops` 对称的 `threads`：
     - `open` 带 kind∈{goal, action, question, promise}、主体和文本（文本只写一次，question 不写答案）；
     - `advance` 带进展说明；
     - `close` 带按 kind 限定的结局闭集和简短收束。
   - 编译器派发 `th{nn}` 并校验生命周期；删除 `BodyDesign` 的四个静态列表，`novel/stages/stage4/design.py` 同步。
   - **折叠**：`novel/stages/stage4/fold.py` 新增独立的 ThreadFold，共用 `guard.scene_order`。
   - **呈现分三种身份**：

     | 情形 | 身份 | 写手看到的意思 |
     |---|---|---|
     | 本场有该线程操作 | `must_realize` | 这一场推进或收束它 |
     | 开放中、与参与者相关、本场无操作 | `continuity` | 仍影响人物选择，不必点明 |
     | 开放中的问题线程 | `guard_only` | 不得提前回答，也不主动提醒读者 |

     全场景保护不等于每场提醒。
   - Guard M8：生命周期合法，应投影的线程已进入 IR。
5. **世界规则**：`novel/stages/stage4/grounding.py::project_brief` 把 Blueprint 的 world_constraints 机械填入 `book_constraints`；`BodyDesign` 新增书级 `world_rules`（目标侧，由 Design 决定沿用或改写），以 `must_respect` 进每章，"作为世界边界，不要讲解"。
6. **章缝合契约**
   - `ChapterAssignment` 新增很薄的 `entry`（第 1 章固定为开篇）：
     - `transition` ∈ {immediate_continuation, time_skip, location_cut, pov_shift, new_scene}；
     - 可选 `carryover`：延续到本章的动作或张力；
     - `recap` ∈ {do_not_recap, brief_orientation}。
   - `novel/stages/stage4/book_ir.py` 把 `entry` 与上一章末场的机械投影（驱动转折、末拍事件、收尾时所在与在场人物）合成 `continuity` 分区，例如"上一章收在……；本章以时间跳跃开场，不复述上章结尾"。
7. **写手协议 v5**：围绕六个分区重写——必须写出／不可违背／承接／导演说明／背景（不必交代）／禁区（不提前兑现、不主动提示）。
   - Hook 的渲染（`novel/stages/stage4/hook_context.py` 与 `HOOK_WRITER_SYSTEM`）同样分层：材料是必须写出；禁止揭示与受保护线程是禁区；起笔事实是延续或背景。
   - `forbidden_reveals` 可以引用线程的 close。
8. **WriterPacket 指标**（派生读数，不是门）：compile 写 `stage4/acceptance/packet_metrics.json`，逐章记录：
   - 写手视图字数，以及各 `delivery_mode` 的条目数；
   - 基线事实数、活跃与受保护线程数、世界规则数；
   - 相邻章在延续与背景两节中的重复比例；
   - 目标篇幅与"必须写出"的密度。
   - 在送达登记里标为 `audit_only`。
9. **升版**：`STAGE4_DESIGN_PROTOCOL` v2（线程、`world_rules`、`entry`）、IR rc10（rc9 已被 R1 的 associate 观察形状占用）、回执、`RENDERER_VERSION` v5、`WRITER_PROTOCOL_VERSION` v5。
- 完成：
  - AUD-03/04、世界规则、承接的反例转绿；R0 守卫全绿（含分区与身份检查）；
  - 写手视图无内部 ID；
  - 线程逐类覆盖开启前、开启拍、跨章携入、推进、关闭拍、关闭后、结尾仍开放、错误锚点；
  - fake 全链绿。

### R3 自愈（只修 typed、可定位的机械问题）
- `contracts/schema/workflow.py` 新增 `OwnerIssue` 与路由表：code → `revise_self`｜`revise_upstream:<节点>`｜`stop`，未知 code 一律停。起点复用 `novel/stages/stage2/resolver.py::ResolverIssue`。
- 范围只限 schema、闭集、锚点、引用闭合、关联未决、必保义务未承载这类机械问题；文学质量不进入。
- **左移校验**：
  - 章观察的人物闭集在 `chapters.py` 验收时就查；
  - associate 验收时做一次 compile 干跑，未决项作为它自身的问题。
- **同节点修订**：`novel/llm/paid_requests.py::ask_json` 在"非空但不过闸"时发修订内容轮（原请求＋上一版＋typed 问题），最多 2 轮，同一问题集重复即停；形状照 `novel/stages/stage5/body.py::write_chapter` 的重写循环。Runtime 的 `send` 仍不收业务回调。
- **跨节点回派**：
  - 回派规则：
    - `run_compile` 的关联未决 → associate；
    - `run_bind` 或 `run_compile_stage4` 的必保义务未承载 → 按 unbound.owner 回 body_design 或 preserve。
  - 下游写 `runtime/issues/<owner>.json`；`novel/orchestrator/state.py::resume_verdict` 把上游判为修订重跑。
  - `contracts/invariants.py` 登记 `owner_revision`。
- 全部计入整书总闸与 ledger；监控页展示修订轮与停止原因（`novel/views/str06.py`、`contracts/schema/api.py`、`web/src/types/str06.ts`）。
- 完成：故障注入 fake 全链靠修订跑完；无进展时按 item 停下；崩溃后恢复免费。

### R4 漂移收口、文档与阅读验收协议
- `run_chapters` 复用 `run_body` 的 `pipeline_model_slot` 与线程池并发，单章失败隔离。
- `str06.stage5.body` 的生命周期改为章包输入与集合恢复语义（先查 `committed_set_recovery` 的调用方）；`state.py` 里提示已退役 `--revalidate` 的文案改正。
- 文档：
  - `docs/schema/str06.md`（两层 IR、送达方式、线程、承接、自愈、发布）；
  - `docs/BACKEND_CONTRACT.md` 的 R-5.14；
  - PreserveCheckpoint 的接口声明如实登记；
  - `novel/stages/stage2/AGENTS.md` 删除 `source.py`；
  - `docs/DECISIONS.md` 登记本计划全部裁决，其中 14 节点写明是默认拓扑。
- 在 REQUIREMENTS 的 B6 节登记**人工阅读验收协议**（见下节），供 B6-A 与 B6-B 共用。

### R6 统一发布权威（B6 之前）
- `novel/store.py::Str06Repository` 按输出组发布：先写组内产物，再写 seal；免费产物同样封存。
- `read_model` 对正式输入校验 seal 与摘要。
- `run_compile_stage4` 等改为验过才发布，失败结果只进诊断。
- 新协议没有已购资产，所以不需要任何补封或迁移层；崩溃窗口靠 prepare 与耐久响应免费补尾。
- 完成：AUD-05 转绿，删掉 seal 后正式消费被拒，免费恢复后重新交付。

### R5 最终零成本产品回归
- 默认非 corpus 完整回归、契约 §9.5 五文件、前端四件；
- 含 R3 故障注入与 R6 发布的 fake 全链产品路径：HTTP 上传 → Supervisor → CLI 子进程 → 交付 → 重启读回。

### R5b 付费路径遗留项（B6-A 前置，2026-09-29 已交付）
挡在付费路径上的遗留项一次改完，再跑一次工程回归后合入 main，然后过语义冻结闸、报价：
1. **W1 选章参数**：`ChainContext.body_chapters`（None＝全书；非空 tuple＝恰好这些章；`()`／乱序／重复／越界在付费调用前拒绝）与 CLI `--body-chapters`；只作调用元数据（`workflow_status.json` 审计字段），不进身份。选章工具 `tools/canary_chapters.py`（只读 sealed WriterBookIR，机械三章规则）。
2. **W2 逐章失败留证**：「最近一次完成执行」语义（`requested/attempted/chapters/recorded_at`，正文与章观察同形共用），缺失＝该节点没有完成执行。
3. **W3 契约登记漂移**：Guard 表驱动（`GUARD_CHECKS`）＋M7/M8 判据登记（R-6.14/R-6.15）＋防漂移 invariant。
4. **W4 端到端验收**：AgentRun 停在正文前 → CLI canary → 续跑只补剩余正文 → 交付（fake transport，零付费）。
5. **W5 登记**：裁决 D1–D7 入 `docs/DECISIONS.md`，交付记录入 `docs/REQUIREMENTS.md`。

### B6-A 语义与写作 canary（付费，单独报价授权）
1. 冻结语义层协议后，为《夫君的报恩》实购 Stage2 与 Stage4，免费编译出全部章包与 packet 指标。
2. 写 Hook 与 3 个按规则机械选出的章（R5b 已落地：选章用 `--body-chapters`，章由 `tools/canary_chapters.py` 从 sealed WriterBookIR 机械选出）：
   - 开篇章；
   - 线程操作与跨章读取最多的中段章；
   - 读者确认与线程收束最多的后段章。
3. 按冻结维度阅读：checklist 感、状态被硬解释、承接是否自然、线程复读、人物声音、场景是否真正演出来、揭示时点。维度与记录方式以「人工阅读验收协议」节为准（该协议由 R4 批正式登记进 `docs/REQUIREMENTS.md` 的 B6 节，B6-A 与 B6-B 共用；此处清单是 canary 侧重点，不另立第二份维度表）。
4. 由 agent 先读并逐条归因，用户确认。
5. 问题在 renderer、WriterPacket 呈现、写手协议或分区优先级 → 只重跑 canary 写手，Stage2/4 不动。
6. 问题在语义层 → 报给用户裁决：改冻结协议意味着重购 Stage2/4。
7. 用户认可 canary 后冻结写作层。

### B6-B 全书（付费，单独授权）
- 补写其余章：写手视图没变的 canary 章直接复用；再跑 Naming 与组装。
- 工程核验：章连续非空、回执、改名零残留、交付字节、零新购重放、重启读回、ledger。
- 交用户通读，按冻结维度记录归因问题（「人工阅读验收协议」节，R4 批正式登记）；汇总运行指标与 packet 指标。

### R7 数据驱动优化
- 归因问题落到对应 owner 的 prompt、投影或协议，包括 v13 遗留的 F-1/F-2/F-3。
- 成本与时延按实测优化：Design 接近 128K 或过闸率低时才考虑拆分；effort 调整须有 A/B 证据。
- STR-06 关闭后跑 D10 两本 golden，先修 `tests/golden.py` 的退役 rubric 引用。

## 执行提示（2026-09-28 计划复核登记）

以下九条是本计划定稿复核发现的歧义、漏项与二义读法，登记为各批开工前的消歧清单；引用行号已在登记时逐一核验。它们**不是新裁决**：

- 对应批次开工先消歧：能由现役契约、`docs/REQUIREMENTS.md` 的定义或本计划「已裁决」唯一确定的，按该语义执行，并把解读登记进该批交付记录；
- 两种读法产物不同且无法从现役契约裁定的（尤其 E-1），先上报用户裁决，不得静默择一；
- 消歧结论与本计划文字冲突时，同批回写本节与对应小节，不留口头结论。

**E-1（high）· R3「自愈」跨节点回派：修订重跑缺执行许可落点。**
「`resume_verdict` 把上游判为修订重跑」没有写进哪张许可表。现行契约：普通队列对 redo 关闭式停止（`novel/orchestrator/state.py:695-698`）；redo 仅 AgentRun 单步 operator 经 `operator_redo_flags` 执行，且要求节点登记 `operator_redo` 策略＋每份 stale 输出的权威汇总 reason 均为 `consumed_changed`（`novel/orchestrator/state.py:860-905`）；`OPERATOR_REDO_STRATEGIES` 只有 `('canonical',)`（`contracts/invariants.py:1657-1659`）；回派目标 `str06.stage2.associate`／`str06.stage4.preserve`／`str06.stage4.body_design` 均未登记 `operator_redo`（`contracts/invariants.py:1871-1912`），且回派场景上游产物新鲜、不满足 `consumed_changed`。「登记 `owner_revision`」进哪张表、是否动 `EXECUTION_PERMIT_KINDS`（现为 `('ready', 'recovery')`，`contracts/invariants.py:77`）的 recovery 许可，均未写。两种读法——只改判定、把修订停给人；或新增自动执行通道——产物不同，直接影响 R3 完成标准「故障注入 fake 全链靠修订跑完」。

**E-2（medium）·「设计原则」保留不动清单 vs R2.4：两个「四类」未消歧。**
「保留不动：四类语义真源」与「四类跨章字段」（R2.4 要删除的 `BodyDesign.standing_goals`／`ongoing_actions`／`open_questions`／`promises`，`contracts/schema/design.py:245-248`）字面易混。仓内术语锚点：`docs/REQUIREMENTS.md`「目标与范围」定义四类故事语义真源＝Characters／Blueprint／TargetDesign／SemanticBinding（复核时另有人读成「Stage2 四份语义观察」，也与跨章字段不同）。执行者若把「四类语义真源」读成跨章字段清单，则与裁决 3／R2.4「删除四个静态列表」直接矛盾——删字段与保字段两种做法产物相反。R2 开工先按锚点消歧，并把「保留不动」句改写成点名定义。

**E-3（medium）· R2.7：`forbidden_reveals` 引用线程 close 缺 wire 落点。**
  **R2 引擎批已消歧（2026-09-28，读法 b）**：compiler 把未到时点的线程 close 机械
  并入禁区（`novel/stages/stage4/hook_context.py` 的 `_protected_thread_entries`：材料视界内开启且主体
  与楔子人物相关——视界外收束进 resolution 禁区、全书未收的 question 进「不得
  给出答案」禁区；视界后才开启的线不进）。不动 `ForbiddenReveal` wire、不升
  `STAGE4_HOOK_DESIGN_PROTOCOL`（R2.9 升版清单本就不含它）；并入项直接追加进
  `HookContext.forbidden_reveals`（已是渲染字符串面，无形状变化）。
`ForbiddenReveal.ref` 是 `OpeningRef`（'init' 或 场景#拍，`contracts/schema/hook.py:75-79`）；`PresentationGraph` 对 forbidden_reveals 也只校验拍引用（`contracts/schema/design.py:370-372`）。要「引用线程的 close」须扩 wire，同步升 `STAGE4_HOOK_DESIGN_PROTOCOL` 并改 `contracts/schema/hook.py`——但 R2.9 升版清单不含该协议、「关键文件」契约清单不含 hook.py。另一读法是 compiler 把未到时点的线程 close 机械并入禁区（不动 wire）。两种做法产物不同，R2 开工先定读法并补齐对应同步清单。

**E-4（medium）· R2.4「呈现分三种身份」表：线程 `delivery_mode` 归类二义。**
行 2「开放中、与参与者相关、本场无操作→continuity」与行 3「开放中的问题线程→guard_only」交集非空（问题线程也有主体）；R0 也只说「否则是延续或禁区」未给分支条件。`delivery_mode` 是单值，归入哪区使渲染分区、packet 指标（活跃／受保护线程计数）与 R0 检查 3 的分区断言都不同。R2 须补可机械执行的分支条件（示例：先判本场操作，再判保护类，其余才进 continuity），并与 R0 登记表同批闭合。
  **R2 契约批已闭合（2026-09-28）**：分支条件落在 `contracts/schema/consumption.py`（`open_threads[].text` 的 mode_note：先判本场操作→must_realize，再判保护类（question）→guard_only，其余→continuity；按参与者相关性过滤，不相关不投影）与 `novel/stages/stage4/guard.py::expected_thread_faces`（机械执行面，M8 对账基准）。

**E-5（low）· R1：状态句柄改形状的同步清单不完整。**
句柄改 `ch{nn}.b{拍}.e{效应序}.{key}` 时，除 `associate.py` 投影与闭集核对外，`_state_rank` 的 `ch(\d+)\.k(\d+)` 正则与 book 前缀序解析（`novel/stages/stage2/compile.py:100-108`）及同文件 338 行（book 句柄 `book.k{index}.{key}` 构造）、475 行（章拍句柄 `ch{chapter}.k{occurrence}.{key}` 构造）都要同批改。「唯一关联登记表」共用清单只点名 prompt、闭集核对与 `_StateClasses`；漏改 `_state_rank` 时不报错，同维合并的 canonical key（类内最早确立者排序）静默退化为 fallback rank。

**E-6（low）· R2.8：新盘上产物未登记 produces 与候选对账。**
`stage4/acceptance/packet_metrics.json` 未列入 `str06.stage4.compile` 的 produces（`contracts/invariants.py:1932-1943`），也未进候选产物表对账（`tests/test_str06_candidates.py`）。不登记则完成度判定与「已登记产物写盘须附来源账」（`novel/store.py:2648-2667`）不覆盖该文件，违反同批闭合纪律；本计划未列这两处同步，R2 须补。

**E-7（low）· R2.1：回执摘要拆分的消费者清单漏 `chain.py`。**
`run_body` 的恢复集合 `_packet_digests`／`_committed_set`（`novel/stages/chain.py:697-698`）与 `run_assemble` 的逐章核对输入（`novel/stages/chain.py:777`）同样按章包摘要判定待补章集。R2.1 只点名 `novel/stages/stage7/assembly.py::verify_chapter_receipts` 同步；漏改则恢复与组装按旧口径判「待补集合」。

**E-8（low）· R3 回派规则：unbound.owner 值域未闭合。**
「按 unbound.owner 回 body_design 或 preserve」的 owner 取值面没有闭合：`UnboundEntry.owner` 是自由 str（`contracts/schema/grounding.py:171-176`），`design|grounding` 闭集只活在 BIND_RULES prompt（`novel/stages/common.py:575-576`）——wire 上 `owner='body_design'` 等变体照样过 schema。路由表须先收紧 schema，或对未映射 owner 显式停；本计划只定义了 code 轴「未知一律停」，owner 轴未定义。

**E-9（low）· B6-A 步骤 2：「扩展 `body_chapter_limit` 与 CLI」二义。——R5b 已消歧**
消歧结论（D2，2026-09-29）：`body_chapter_limit`（前缀语义）退役，改为 `ChainContext.body_chapters`（None＝全书；非空 tuple＝恰好这些章，`()`／乱序／重复／越界在付费调用前拒绝）＋CLI `--body-chapters`，只作调用元数据不进身份；`_committed_set` 按目标集合迭代、`body_complete` 仍按全书判断。恢复判定因此按集合而非前缀：已完成 1、2、4、6 章时只补 3、5 的语义不变。

## 人工阅读验收协议（R4 登记，B6-A／B6-B 共用）
- **维度**：
  1. 情节与因果是否完整；
  2. 揭示时点是否正确；
  3. 章间衔接是否自然；
  4. 人物声音是否稳定；
  5. 写手是否把 IR 写成了说明书；
  6. 信息有没有重复交代；
  7. 场景是否通过行动与对话展开；
  8. Hook 是否有吸引力而不提前兑现；
  9. 整体节奏与流畅度是否优于基线。
- **记录方式**：不打分。每条问题记"章与段位置 → 维度 → 归因 → 处理"。
  - 归因取值：Stage2 抽取／Preserve／Design／Bind／编译投影／renderer／写手协议／写手发挥。
  - 处理取值：canary 收敛或 R7。
- 阅读记录存运行根，不进仓库，也不在仓库里摘录正文；仓库只写汇总。
- 语义保持 unver，机械回执不代签质量。

## 关键文件
- Stage2：`novel/stages/stage2/associate.py`、`compile.py`、`chapters.py`；`contracts/schema/observation.py`
- Stage4：
  - 契约：`contracts/schema/design.py`、`context.py`、`book_ir.py`、`receipt.py`、`workflow.py`
  - 投影与 renderer：`novel/stages/common.py`（`scene_read_views`、`project_context`、`project_chapter_packet`、`render_chapter_packet`、`DESIGN_RULES`、`WRITER_PROTOCOL`、`HOOK_WRITER_SYSTEM`）
  - 引擎：`novel/stages/stage4/design.py`、`compile.py`、`fold.py`、`book_ir.py`、`guard.py`、`grounding.py`、`hook_context.py`
- Stage5/7：`novel/stages/stage5/body.py`；`novel/stages/stage7/assembly.py::verify_chapter_receipts`
- 自愈：`novel/llm/paid_requests.py::ask_json`、`novel/stages/chain.py`、`novel/orchestrator/state.py`、`contracts/invariants.py`
- 发布：`novel/store.py::Str06Repository`
- 登记与守卫：`contracts/schema/consumption.py`、`tests/suite_catalog.json`

## 复用的现有实现
- 折叠与阅读序：`novel/stages/common.py::StateOverlay`、`novel/stages/stage4/fold.py::FoldedState`、`novel/stages/stage4/guard.py::scene_order`
- 线程生命周期的形状：`contracts/schema/source.py::LoopAction`，以及 `novel/stages/stage2/compile.py` 的 loop 编号与重复 close 校验
- 章包投影与摘要：`project_chapter_packet`、`packet_sha256`（变异守卫的比较面）
- 并发槽：`novel/model_slots.py::pipeline_model_slot` 与 `run_body` 的线程池写法
- 有限重写：`novel/stages/stage5/body.py::write_chapter`
- 精确 issue：`novel/stages/stage2/resolver.py::ResolverIssue`
- 封存：`contracts/schema/workflow.py::CommitBundleV1`、`novel/work_units.py::sealed_commit_bundles`

## 验证
- **每批定向**（在沙箱外跑，看退出码与 JUnit）：
  `& 'D:\Miniconda\envs\novel_refactor\python.exe' -B -m pytest -q -n 0 -x <本批测试> --basetemp=E:/_tr -o cache_dir=.test_cache --junitxml=.test_cache/<批>.xml`
- **R0/R2**：必须走真实 compiler 与 renderer，检查最终写手视图的字节——分区、身份、有无内部 ID——不能只断言 helper。
- **R3**：故障注入 fake 全链，覆盖修订成功、无进展停止、崩溃恢复。
- **R5**：`-n 4 --dist loadgroup -m "not corpus"` 完整回归，加契约五文件，加 `web` 下 test／typecheck／seams／build。
- **B6-A／B6-B**：报价与授权分别进行；逐节点记录 token（含 reasoning）、耗时、成本、首响应过闸率、修订轮数与 packet 指标；按阅读协议归因。工程结果与文学评价分开报告。
