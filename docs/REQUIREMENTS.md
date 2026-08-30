# 活动待办

本文件是项目唯一的活动待办清单，只保留尚未完成、仍值得推进且有明确验收条件的事项。
已完成能力、撤回方案与实验过程由 Git 和 `docs/BACKEND_CONTRACT.md` §十回答；产品规格、
冻结契约与证据基线见 `docs/BACKEND_CONTRACT.md`，可迁移经验见 `docs/LESSONS.md`。

**编号不重排。** 已删除的编号保持空缺，避免历史提交中的引用改指另一件事。

⚠ 默认 pytest 刻意排除 corpus。只有 TODO-21 完成，并按 `tests/README.md` 使用
`--require-corpus` 对精确匹配的无正文 manifest 与盘面完成验收后，才可以称“全量语料绿”；
此前的通过结果只能称“非 corpus 回归”。

## P1 · 质量闭环与诊断可信度

### TODO-21 · 建立 current-schema 现役语料基线

**缺口**：corpus 闸、manifest schema 与关闭式验收器已经存在，但仓内仍没有一份由当前
producer 从来源完整生成、且与盘面精确匹配的无正文 manifest。因此现有非 corpus 回归不能证明
真实全链、版本、来源账与 skip 面闭合。

**工作**：

- 按 `.claude/skills/novel-pipeline-operator/SKILL.md`，在隔离输出与独立空缓存中，对
  `CURRENT_CORPUS_BOOKS` 从来源运行 Stage0～Stage7；人工闸与 typed review 必须留下真实决定，
  不批量盖章，不给旧产物补字段或版本冒充当前 producer。
- 生成并提交无正文 manifest。逐书记录 sid、source hash、章节数、预期产物及当前版本、代码身份、
  冻结 dispatch、ledger 聚合与精确 skip allowlist；成本只取 `<base>/_service/ledger.jsonl`。
- 对同一盘面运行 `--require-corpus` 的 `corpus or not corpus` 完整套件。

**验收**：书目与产物集合完整；每份产物版本等于 `ARTIFACT_SPECS.current_version`；代码身份唯一；
dispatch、ledger 与 manifest 闭合；`tests/corpus.py::requires_current` 不产生过渡态 skip；没有未登记
skip、缺书、缺产物或版本漂移。命令与失败口径只引用 `tests/README.md`，不在本文件复制第二份。

### TODO-28 · backend_v1 fixture 与真实 API 响应契约闭环

**缺口**：`web/src/fixtures/backend_v1/*.json` 是 mock 的现役数据源，但当前只有少数文件被
Pydantic 响应模型直接校验。Stage2 v6、Stage4 v4、Stage5 与 Stage7 的形状变化已经让部分 fixture
无法通过对应的 `contracts/schema` 模型，而前端 seam 只检查“谁能读取”，检查不到载荷漂移。

**工作**：

- TODO-21 产出一本文本链完整、current-schema 的书后，从真实 HTTP 端点重抓 backend_v1 fixture；
  合成示例只保留无法安全抓取且确有专门判别价值的分支，并明确标注来源。
- 建立一张闭合登记表：每个 JSON 必须映射到对应的后端响应模型，或登记为显式例外及其独立守卫。
  登记表键集必须等于目录 JSON 文件集，新增文件不得静默漏验。
- `enums.json` 继续由既有 `payload()` 对账守卫负责；`style.json` 的 legacy adapter 例外必须显式
  登记，不能靠未映射实现豁免。其余映射使用 `StrictModel(extra='forbid')` 双向抓缺字段和退役字段。

**验收**：全部 fixture 均由登记表覆盖；每个普通 fixture 通过对应现役响应模型；两个例外各有
独立守卫；故意删一个登记项、增加一个字段或删除一个必填字段时，测试都会失败。

### TODO-30 · 盲规划、结构读数与独立人工批准闭环

**缺口**：Stage4 已完成 fact atom → scene、隔离 A/B、可复算结构读数、hash-bound 人工结构门和
v4 减法契约；尚缺的是用 current-schema 真实书证明「A 的去序隔离确实带来可辨认且不伤事实安全的
结构重建」。结构质量没有 ground truth，不能用非 corpus 测试或未经标定的自动阈值代替人工盲评。

**工作（按顺序完成，不跨步宣称收口）**：

- 冻结同一份 Stage2 v6，在不覆盖正式产物的实验面做旧规划与当前盲规划 A/B。A 只见去序、无源位置信息
  的事实真相图和 opaque alias；B 才见证据层，只能绑定舞台与保真材料，不能静默改变 A 的
  场景顺序、事实分组、首尾锚点或 retain/reconstruct 决定；人物到场只从绑定舞台推导。
- 把两臂、书名、版本和生成顺序遮蔽后做逐章人工盲评，至少区分：顺叙本来合理、入口/分组/披露确有
  重建、只改措辞、为追指标强行重排。标签必须绑定章节与证据，并与现役可复算指标逐项对照。
- 最后与 TODO-21 共用两书 current-schema 全链验收，保存 Stage2 冻结主体、A/B 中间证据、Stage4 v4、
  结构报告与人工决定、Stage5 canonical 的逐章读数；不得另拿旧 prompt 或旧 schema 产物充数。

**验收**：两书事实、因果、人物状态、材料闭合与低文字重合仍成立；每章明确记
`retain/reconstruct`。⚠ 2026-08-29（TODO-45 批次 2，`R-5.22`）口径修订：
`computed_change_kinds` 是权威代码读数，`mode/declared_change_kinds` 只是模型设计意图，
`structure_alignment` 固定为 metric_only 披露（无 fix 路由、不得生成付费 action）——
「声明与复算对齐」不再是质量验收判据，只作盲评对照的读数；整书结构向量、逐章指标、
A/B 原始候选、盲评标签与结构批准均可从盘上 current-schema 主体复算。人工抽样能指出入口、事实
组合或披露顺序的具体差异，而非只说句子不同。每项 metric 最终得到“继续只报不挡 / 候选阈值需
产品裁决 / 当前不可判”之一的结论并记入 `docs/BACKEND_CONTRACT.md` §十。`R-5.25` 的 design /
structure 工作包已把 current subject、异常、全章 coverage 与复用身份投影成可达阅读面，但它只缩小
人工阅读范围，不制造盲评标签或质量结论；在完成两书付费实证前，任何测试通过都只能叫非 corpus
回归，不能删除本条。

### TODO-31 · 下一次新 base 同批收紧 Stage4→5 盘上契约

**缺口**：Stage5 已改为从当前 Stage1 / Stage4 任务书现场派生来源章号、源字数与章预算，
Chapter/Review/trim 中也存在可由正文、findings 或绑定主体重算的展示副本。为保留现有付费正文与
人工 trim 决定，本批只完成运行时单一落点和一致性断言，盘上破坏形状延期；不能把延期误解成这些
字段仍有独立语义。

**依赖调整（TODO-45）**：本条的五次 shape break 不再单独安排一次新 base，
统一并入 TODO-45 的 P2 批次一起切换，避免为同一批减法付两次重建费用。本条继续
独立登记验收口径，不因并批销账。

**工作**：只在下一次创建并从 producer 完整生成的新 base 上（即 TODO-45 P2 的那一次），同批切换：

- WriterContext v5 删除 `ChapterCharacter.char_id` 与章级 `from_source_ordinals/source_chars/budget_chars`；
- Chapter v3 删除 `from_source_ordinals/chars/overlap_excl_verbatim/warns`，运行时由正文和当前任务书重算；
- Review v3 收成当前主体绑定、findings 与确有判定用途的必要基线，不保留展示计数或重复正文面；
- Rejected v2 改存 `segments`，不再存无法保持 slot 身份的整章 `text`；
- TrimAudit / TrimResolution v3 删除可由绑定正文、路径或对方主体确定的重复展示与身份字段。

**验收**：五次 shape break 均登记 current version 与 `tests/test_artifact_versions.py::_SHAPE_BREAKS`；
新 base 只由现役 producer 重建，不迁移、不手补旧产物；Stage5 写作、recheck/revalidate、Review、
trim 决定、s5 审批与 Stage6/7 消费均只读最小主体；证明删字段前后 prompt、机械 findings、正文拼接
与审批对象语义不变。旧 base 保持关闭式拒读且不被自动改写，TODO-21 的 manifest 只接受完成切换后的
current-schema 盘面。

### TODO-32 · 将现役付费生成迁移到逐节点可恢复 agent

**剩余缺口**：Phase 1～6 的工程迁移已经接通——八个付费生成节点均有 Claude Code / Codex
逐节点可恢复 agent 路径，整书 Commander、typed 代批与前端双角色/逐节点配置也已贯穿真实
preview/start/argv/session/audit 链。但这些结论仍只来自 fake driver、本机零模型 CLI 检查与非
corpus 回归：尚未用 current-schema 真实整书跑到 Stage7，也没有据此冻结 agent-only 服务端默认。
因此 `default_profiles` 仍为 null，未显式 profile 的 legacy LLM 路仍保留；现在删除或自动改写它会把
未经真实验收的工程可达性冒充生产迁移完成。

Commander/Executor 的零成本工程收口已补齐以下读面，但**不改变上述真实付费验收缺口**：Stage4/6/7
审批工作包以代码、规则、产物主体和完整 workpack SHA 绑定，完全相同的 current typed 决定可幂等跳过；
Stage6/7 页面必须真实展示工作包证据后才允许提交双 hash。`state --with-inbox` 在单次调用内复用冻结
snapshot，canonical 终审只 collect 一次、内存 sync/validate 通过后原子提交，再由 fresh
`delivery.assess` 独立关闭。operator phase 遥测只拆 attributed/unattributed 墙钟；Commander 宿主
模型 usage 仍越不过进程边界，不得由墙钟估 token 或成本。

本批又完成五项**零模型接线**：`operator next` 从 queue 的同一规划落点纯读给出三态下一动作；
`operator permit` 把 ready exact action 或生产登记的 blocked recovery 铸成独立许可，绑定 current 读面、
node/flags、贵贱/写盘效应及外部输入原始字节，blocked action hash 本身不再冒充执行权；headless
`run_step.py` 在 preview/execute 都重铸 permit，claim 覆盖 root 与 canonical follow-check，子 CLI 在任何
producer/LLM 前复核 active claim、permit envelope、step index 与逐字 argv。一份 canonical skill 以 Agent
driver / legacy LLM 两个 execution profile 复用，Commander 冻结并复核入口、references、scripts、assets
的整树字节 hash；Commander spawn 前先取得 per-parent OS owner lease，再打开 durable admission gate，node execute 与 approval、response-repair、
终审、operator telemetry 等非 node 写入口都在副作用前 claim。顶层返回后 close 并持续等待 active claim
与 execution beacon 两面归零，300 秒只报警/cancel、仍持书锁与并发槽，越线 run 最终只能 unknown；
Claude launcher 同时禁用后台任务，服务重启后 unresolved gate 继续阻断同书 agent/job/trash/API 写手并
让 queue startup 整 lane 跳过；孤儿及 admission/beacon 坏尾只有 hash-bound reconcile 恢复，live owner
即使 force 也不可越过，迟到事件会使旧 reconcile seal 失效。正常 exit 0 后若 fresh
delivery 仍 stopped 且 next ready，记 `commander_early_exit`，本批不自动追加模型 turn。新增
`tools/verify_agent_e2e.py` 把 current 产物、HARD/UNVER、审批、账本、session、adoption、operator phase
与 fresh delivery 关闭式联结。以上只证明调度、身份与验收器的工程路径，不是一次真实 agent-only 成书。

Stage6 另有默认关闭的 `compact-v1` JSON 与独立 session B-worker 微错峰实验臂；默认请求仍保持
`pretty-v1 + 0ms`。compact 目前只证明字节语义等价与工程可运行，静态公共前缀也不保证 provider
cache hit。是否切默认、是否采用错峰，以及账本 prompt/cache/墙钟的真实收益，必须放进下面同一批
干净代码、干净缓存、多书付费质量回归；不能拿本书单次提速直接定案。

**工作（严格按顺序完成）**：

- 已建立供应商中立 executor / Commander driver、能力探针、精确 profile、追加式 session 审计与
  start/resume/stop 协议；配置、actual identity、CLI 或审计不闭合均关闭式失败或降 unknown。
- 已按顺序接通 `stage2.global`、`stage2.blueprint`、`stage6.semantic` 与其余五个付费节点；每个
  node/chapter/judge 独占 session，候选、代码反馈、checkpoint、正式提交、adoption 与终验闭合。
- 已接通 API/前端 capability 驱动的 Commander、默认 executor、逐节点 override、精确模型与
  reasoning effort；preview/start/queue 冻结同一快照，两项授权继续独立，标准代批只走 typed CLI façade。
- 已接通 hash-bound Stage6/7 审批 workpack、current 决定幂等跳过、invocation-scoped state/inbox
  snapshot、collect-once finalizer 与 operator phase 遥测；这些都是 0 模型工程能力，不替代付费质量验收。
- 已接通 queue 同源的确定性 `operator next`、独立 exact execution permit、canonical skill 整树 hash、
  node/generic admission claim、close + execution beacon 双面 drain、hash-bound reconcile 与
  `tools/verify_agent_e2e.py` 的 agent-only 整书验收器；它们没有产生模型响应。
- 用 current-schema 真实整书跑到 Stage7 并核对正式产物、双账、会话审计、审批与终审包后，才把
  agent 设为 Commander 主路的默认执行路径。DeepSeek 长期保留为独立、显式且不可自动触发的
  legacy LLM 路径，不作为 agent 的静默 fallback；后续另行把现役 LLM 客户端抽成供应商中立基类，
  再让 DeepSeek 作为一个具体实现接入，这项后续重构不阻断当前 agent 纵切验收。

**依赖调整（TODO-45）**：TODO-45 P3 的两臂都跑 agent-only、legacy 路禁用，因此候选臂
那次真实整书**可以**充当本条要的付费验收——前提是它跑在干净 main 的提交上
（`experiment_valid=true`，即代码身份唯一且不带 `-dirty`）。脏工作树跑出来的整书不算数。

**验收**：两个 driver 的零成本 wire / resume / 取消测试通过；八节点 registry 闭合且没有 vendor×node
子类笛卡尔积；中间拒稿不写正式产物、不伪造终态，最终提交可由 ledger attempt、session turn 与
artifact adoption 逐项联结；Stage2 逐章 session 和 Stage6 双判官互相不可见；所有未迁移组合在
spawn 前拒绝且显式 profile 永不 fallback；前端选择经页面 → API → Job/Commander → 子 CLI →
driver 的真实链路生效。非 corpus 回归与真实付费整书验收分开报告；没有显式授权时不发模型请求。

### TODO-45 · Stage2 事实分级、Stage4 分单元恢复与上下文瘦身

**冻结决策（先读这一段，再看分批）**：

- Stage2 只让高影响事实阻断下游；低影响项目保留审计与披露，不自动 PASS、不消失。
- Stage4 保留一个公开 `stage4.design` 节点/授权、一个独立 `stage4.plan` 节点/授权和整书结构审批；
  A 内部固定为 1 条整书策略 + N 条逐章私有 session；B 内部固定为机械基线 + 真歧义章私有
  session，两相各自最多 4 并发，**不开放公开的 `--chapters` 定向重规划**。
- A 的正式盲审与最终 Plan 结构审批是两张独立票：只有 current A 的策略与全部章节均 accept
  才授权 B；B 编译成功后仍须对 current Plan 逐章全 accept，才可生成 Context。A 的逐章驳回
  只通过绑定 current decision SHA-256 的定向 repair 执行，策略驳回才允许整书 replan。
- 模型只提出候选；代码确定性分级，人确认高影响事实后才能成为 binding。
- Stage4 局部修复只走 agent session；legacy DeepSeek 保持无历史单发路径，
  且**不作为 agent 的静默 fallback**。
- 审计证据、provenance、拒稿与历史轮次继续完整保存；优化目标是减少模型输入、
  人审负担与重复业务语义，不是删审计。

**P0（不升 artifact version）**：

- 已交付只读读数器 `tools/workflow_metrics.py`：来源逐格唯一（产物体积/重复率取书内
  `stage*/` 顶层 JSON、token/缓存取 ledger、CLI 取 `agent_progress.jsonl`、闸与恢复取
  `llm_audit.jsonl`、结构只调生产 `structure_metrics_of`），输出不含正文/人名/书名/
  绝对语料路径，`--json` 拒绝写进 base。
- 已精确复算 v8 基线并标记 `experiment_valid=false`（7 个 `-dirty` 代码身份）。
- 按行为逐条移植已证实的假拒绝修复，**不移植** worktree 的 Claude transport、
  旧 `run_design/run_plan` 与旧 response contract：`declared_change_kinds` 去重并按冻结顺序
  canonicalize；`bound + evidence_refs=[]` 收成 `null`；只有 `continue` 强制相邻状态承接、
  可证明的 `return` 查锚点、其余非线性边界产 UNVER；**章首 entry 与章尾 ending 继续 HARD**；
  因果端点可通过 context fact 入场、多次出现取首次可见场；`STYLE_WORDS` 只拦真正的写法指令。
- 跑书 skill 写明：INFO、metric、UNVER 只披露，不触发付费重试。
- **P0 已完成并按 `docs/BACKEND_CONTRACT.md` §十 2026-08-28 行留证**：v8 那 5 份真拒稿
  0 成本重放，4 份通过、1 份仍红且红在真漏落事实。

**P2a 进行中**：已把两类「让模型复述代码已知信息」的假拒绝收窄——同一时间线的状态
承接改由编译器派生（`novel/stages/stage4/checks.py::derive_effective_states`），
context 引用与章尾闭合改判「读者此刻知不知道 / 本章有没有让它成立」。Stage4A 的
生产拓扑与提交边界也已改成「1 条小型整书策略 + N 条逐章私有 session」：策略与每章分别
验证后 checkpoint，逐章池由配置下调且绝不超过 4，并在 N+1 个 unit 全闭合后确定性聚合、
重跑整书 schema/HARD、通过 commit journal 原子提交现有 `BlindCandidate`。公开节点、授权次数、
`BlindCandidate` v2 形状不变。随后稳定性修复把 `EvidenceBinding` 升至 v3：模型只能提交
非阻断 `binding_concerns`，正式 `infeasible` 必须由代码在同一关闭约束集铸造 witness；旧 v2 B
不迁移，须在 current A 全 accept 后重跑 B。
**P2a 已完成六项并按 `docs/BACKEND_CONTRACT.md` §十留证**：旧 whole-book validator 拒稿的
0-wire adoption（`stage4.design --adopt-rejected`，f138896；不冒充 split-run unit 恢复）；typed failure witness /
关闭式路由（`R-5.21`，批次 1：A/Stage2/code owner 首轮即停、同 fingerprint 两轮早停、
审计无正文、witness 入 `_llm_rejected`）；A→formal 零调用预检与审阅元数据归一
（`R-5.22`，批次 2：构造性绑定 + 同一 HARD 的四处预检调用点、blocked 拒 accept 与
wire 前拒绝、`structure_alignment` metric_only、`structure_delta` 退役、
`DESIGN_REVIEW_CONTRACT` v2）；B 的机械基线与章级私有 session（`R-5.23`，批次 4：
唯一可解绑定代码确定、机械章 0 wire、歧义章私有 session 真实最多 4 并发、worker 内验证后即时
checkpoint、聚合重问同步更新 lineage/anchor、hash-bound unit replacement 与同一 compiler/HARD、
来源账 architecture/unit_lineage additive——公开节点与授权次数不变）；两次审批的异常优先工作包
（`R-5.25`，批次 5：固定六组、当前 subject、全量 INFO/metric/UNVER/阶段延期披露、changed-first
coverage、历史 exact accept 标识、服务端 action/effect 与 Inbox `reviewed_retained`，API/CLI/UI 均只读）；
Stage2 Blueprint 的异常优先全书只读工作包（`R-3.16`：绑定 current Blueprint/MainText/完整 check/
代码/规则，全章保留、机械字段隐藏、语义字段不裁；页面展示与提交只用同一完整 binding，不再因
打开一章自动写整书 `seen.read`；持久 proof 随完整工作包变化失效，CLI/mock/backfill 均不能代造，
不新增 approval/gate/artifact）。
**剩下的 P2a 项**（按对稳定性的贡献排序）：

1. `evidence_candidate_id` 的**工程实验臂已落地、默认未切**：候选表由 `paragraph_spans` 同源渲染与
   lookup，模型响应使用独立 candidate-only 类型，未知 ID 在 acceptance/checkpoint/写盘前关闭，
   人工 GlobalEdit 仍用 locator，正式 GlobalGrounding 仍落 SourceSpan。剩余工作是另批冷缓存 A/B：
   对同一固定语料配对比较首轮落盘率、
   `global_evidence_unlocated`、总 wall/token 与人审质量；质量无回退且成本/耗时改善前不切默认。
   本项需要单独的正文外发与付费授权，不复用主分支跑书目录或缓存。

**P2（按稳定性优先重排；2026-08-28 用户裁定）**

P2 原本是一个「候选事实分级 + 整书修复 + 上下文瘦身」的大批次。按「稳定跑通优先」
重排为两段，**先做与稳定性直接相关的那一段**：

- **P2a（先做，不破坏正式盘上形状）**：A/B 的分 unit 生产拓扑、验证后即时 checkpoint、最多
  4 并发、确定性聚合、整书 schema/HARD、aggregate commit journal 与 B 的 hash-bound unit
  replacement 已落地，把「一处错就整书归零」收成「保留全部已验单元」。本段剩余工作是抽出唯一
  `gate_payload` 校验函数供 LlmClient / agent intake / response-repair / adoption / repair 共用，并继续把
  A 与其他 split-run 的 validator 拒稿 unit 收敛到统一 0-wire adoption/replacement。拒稿仓统一为
  `_llm_rejected/<chain>/<attempt>/`；正式 A 候选仍走 decision-bound 逐章 repair，旧整书
  `--adopt-rejected` 不冒充 unit 恢复路。目标不变：**一次有效付费产出不许被判据或形状问题永久锁死**。
- **P2b（后做，需要新 base 与一次付费重建）**：blueprint fact 增代码计算的
`derived_tier`（core/support/incidental）与 `tier_reasons/depends_on`，模型的
`producer_obligation` 只留审计；Blueprint Review 分 frontier/coverage 与 claim 两层，
 source beat 必须闭合为 `covered_by` 或显式无状态变化；Stage4A 沿用已冻结的策略 + N 单章
 session/checkpoint/整书聚合契约；抽出唯一 `gate_payload` 校验函数供
 LlmClient / agent intake / response-repair / adoption / repair 共用；拒稿仓统一为
 `_llm_rejected/<chain>/<attempt>/`，补齐分单元 0-wire adoption 与 typed replacement；
WriterContext v5 改共享表 + refs；Stage6 拆 `generation_packet` /
`mechanical_guard_basis` / `semantic_verification_packet` 三面，发明判据的允许集只来自
generation packet。TODO-31 的 Stage4→5 减法并入本批。

**P3（全链稳定性验收，需用户在付费前显式批准预算）**

⚠ **2026-08-28 用户裁定：P3 不做盲审，也不做双臂偏好实验。** 当务之急是
「给定一本小说，agent driver 下 opus5 + xhigh 能稳定跑通 stage0～stage7」，
核心指标是**每个逻辑单元首次交付 + 一次节点授权完成正式落盘 + 少量人审**。原计划里的两臂对冲、
42/42 章双臂 panel、
臂遮蔽与结构偏好统计整体撤下；控制臂 `e15aefbf4856aba83a44abd1a607bfdc48a20b87`
只作为已有 v8 读数的对照**从账本上读**，不为它另花一次钱。

- 执行面：单臂、agent executor、`max_turns=4`、legacy 路禁用；profile 在首个付费调用前
  由用户批准并写入 manifest，能力不可用就停止、不 fallback。
- 验收执行面已经有 0 模型纯读入口 `tools/verify_agent_e2e.py`；它只能消费一条真实 Commander run 的
  `run_id` 并关闭式联结产物、闸门与三本账，不能在尚未开跑时预造 PASS。当前尚无满足本节条件的
  current-schema Stage7 run，因此本工具没有 P3 通过结果，`default_profiles` 继续为 null。
- 稳定性读数（全部由 `tools/workflow_metrics.py` 从账本与审计现算，不换算金额）：
  逐逻辑单元**首次交付率**、逐公开节点**首次授权运行正式落盘率**、wire 次数与重采次数、
  内容拒绝次数、`.INVALID` 次数、
  infra 失败（timeout/连接）单列不与内容失败混算、人审项数与人工介入次数。
- 目标（是目标不是 HARD）：Stage4 整节点归零重跑为 0，已验策略/章节不因其他单元失败而重发；
  INFO / metric / UNVER 引发的付费调用为 0；内容拒绝 ≤ 1 次/书；全链 0 次因判据假红导致的整书重出。
- 达到用户批准的 token 或货币 cap 即停并如实判 inconclusive，不挑便宜结果。

**验收**：P0/P1/P2 各批跑冻结契约 §9.5、对应 stage/API 测试与完整非 corpus 回归；
P2 的每次 shape break 登记 `tests/test_artifact_versions.py::_SHAPE_BREAKS`；
P3 自动化只验「书到 Stage7、产物版本全 current、HARD 全闭合且 UNVER 不计绿、
`inbox.delivery_counts.blocking == 0`、账本 / session 审计 / artifact adoption / 审批
可逐项联结、`experiment_valid=true`（代码身份唯一且不带 `-dirty`）」；
稳定性读数只报原始次数与分母，不预设自动 go/no-go。
阶段验收后把冻结行为写入对应 R 条与 `docs/BACKEND_CONTRACT.md` §十，并从本条删除已完成部分。

## 条目生命周期

1. 开工前补足可复现输入、预期最终状态、成本与受影响守卫。
2. 改动覆盖真实消费者、失败与恢复路径、文档和测试，不以类型或占位入口存在为完成。
3. 验收通过或经证据裁定不再值得推进时，立即从本文件删除；历史只回写 Git 与
   `docs/BACKEND_CONTRACT.md` §十。
