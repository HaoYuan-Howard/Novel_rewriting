# 活动待办

本文件是项目唯一的活动待办清单：绞杀式重构的进度、迁移批次、compatibility adapter
与删除条件。已完成能力与历史 TODO 由 Git 与 `docs/DECISIONS.md` 回答；产品规格与
可执行守卫见 `docs/BACKEND_CONTRACT.md`；架构硬闸见根 `AGENTS.md` 的 I-\* / S-\*。

**2026-09-17 重置**：本文件已清空旧 TODO-21～54 活动账，改为 stage0～stage7 干净绞杀
重构 backlog。旧条目全文可在 Git 历史中按路径检索；重置裁决见 `docs/DECISIONS.md`。
新编号从 STR-01 起；编号不重排，删除后保持空缺。

⚠ 默认 pytest 刻意排除 corpus。在 current-schema 语料基线重建并经
`--require-corpus` 验收前，通过结果只能称「非 corpus 回归」，不能称「全量语料绿」。

## 重构目标

对当前流水线做绞杀式重构：项目结构、目录名、文件名、函数名与角色术语一并收束到根
`AGENTS.md` 的目标词典（orchestrator / compiler / validator / repository / semantic unit）。
逐节点推进；每个节点成为 authoritative 后，旧 path 只读解码并进入删除条件，禁止继续扩展。

产品边界在重构期间默认保持：全自动单模式、Stage 只读监控、Stage6 三候选改选、章号恒等、
ledger 成本权威。改变产品语义前须用户裁决，并同步 BACKEND_CONTRACT。

## 迁移纪律（每条节点通用验收）

完成一个节点的绞杀，至少同时闭合：

1. 新路径只依赖新 contract；不 import legacy 实现内部模块（S-01～S-02）。
2. 若需读旧盘面，兼容逻辑只在登记过的 adapter 中；本条列出删除条件（S-03）。
3. 无双写真相源；权威 path 切换后旧 path 停止加功能（S-04～S-05）。
4. 契约、compiler/validator、consumer、恢复、API/UI、测试与文档同批闭合。
5. 定向 unit/contract + 该 stage 测试通过；波及共享面时补受影响回归。

## P0 · 绞杀批次

### STR-01 · 根不变量与活动账复位

- [x] 根 `AGENTS.md` 写入 I-\* / S-\* 与目标角色词典。
- [x] 本文件重置为绞杀 backlog；旧 TODO 退出活动账。
- [x] 在 `docs/DECISIONS.md` 登记重置与术语统一裁决。

**验收**：agent 开工只从本文件取未完成项；不变量冲突时优先根 AGENTS + BACKEND_CONTRACT。

### STR-02 · 术语与包边界样板（Stage2 blueprint）

样板节点：`stage2.blueprint`（局部不变量见 `novel/stages/stage2/AGENTS.md` S2-1～S2-8）。

- [x] 目录 / 模块命名对齐目标词典：权威包 `novel/stages/stage2/blueprint/` + `shared/`。
- [x] 公开面与内部实现分离；生产深 import 改权威 path；跨 Stage 深路径 import 不得新增。
- [x] 旧 `plot_*` / `contract` / `run_state` / `paragraphs` / `character_refs` 顶层 shim 已删除。
- [x] 最小反例：`tests/test_layering.py::test_stage2_owner_shims_cannot_return`。

**验收**：blueprint 新 path 为权威；样板已被 analyze/characters/knowledge 复用。

### STR-03 · 按 Stage 逐节点绞杀

下列为默认顺序，用户可改序；每完成一节点在此勾选并写删除条件指针。

| 节点族 | 状态 | 权威 path | adapter / 删除条件 |
|---|---|---|---|
| stage0 | **进行中（STR-03-S0）** | `novel/stages/stage0/`；权威 `Stage0Transform` v3（路径仍 `stage0/revisions.json`） | 见 STR-03-S0；v2 链/approval 拒读，无 adapter 迁移 |
| stage1 | 未开始 | — | — |
| stage2（analyze / characters / global / blueprint） | **权威闭合**；Blueprint **STR-03-S2-BP v11**；Characters **STR-03-S2-CH v3（M1–M3 消费闭合；analyze 保留）** | 同上；Characters v3 见 STR-03-S2-CH | analyze 联合首抽退役条件见 S2-CH；恢复双付费未审计前不得删除 |
| stage4（design / plan / context） | **权威闭合（STR-03-S4）** | 见 STR-03-S4；`plan_chapters` 已退役 | `RETIRED_ARTIFACT_PATHS['stage4.plan_chapters']` |
| stage5（write / review） | **整链由 STR-06-F5 接管**；旧链删除归 STR-06-F8 | 见 STR-06 | — |
| stage6（prepare / hook / approve） | 未开始；消费适配由 STR-06-F6 承接，本体其余绞杀独立 | — | — |
| stage7（rename / assemble） | 未开始；消费适配由 STR-06-F6 承接，本体其余绞杀独立 | — | — |
| orchestrator / repository / LLM 传输共享面 | 随消费节点收敛 | — | — |

**验收（单节点）**：I-01～I-38 与 S-01～S-05 对该节点可检查；下游只经正式 artifact。
**验收（全书）**：上表节点族全部 authoritative；adapter 清单清零或仅余有日期的只读解码。

#### STR-03-S0 · Stage0 单次机械 transform（v3）

目标：去掉 revision 链 / pending / 候选 / 人工编辑；权威一次 `Stage0Transform`，
闸门为 mechanical `admission`；正文可从原文 − removed 重建。

- [x] schema v3 + ARTIFACT_SPECS 升版拒读 v2
- [x] `revisions.py`：make_transform / commit / ensure_materialized
- [x] producer / eligibility / checks / CLI / views / source_identity 对齐
- [ ] 定向回归闭合后勾选本行完成；存量书一律重建。
      漏账（2026-09-23 F0 对账发现）：`tests/test_store.py` 仍 import 已删除的
      `Stage0CandidatePlan`，整文件 collection error——Stage0 v3 切换未同步该
      测试；随本行闭合一并修复

**验收**：同输入幂等；算法变则整份替换；手改 clean 关闭式拒绝；下游只经 transform + clean 投影。

#### STR-03-S4 · Stage4 契约瘦身（Design → Grounding → Context）

目标版本（一次对外切换批次共用；内部拆分不单独升版）：

| 产物 | 目标 | 备注 |
|---|---|---|
| Design 正式文件 | `presentation_graph` / BlindCandidate **v7**（路径可仍为 `blind_candidate.json` 或登记为 `presentation_graph.json`） | 有序 `steps[]`、compiler 发 `delivery_ref`、稀疏 `permission_transitions`、`delivery_focus`；落盘无 alias 字典 |
| Grounding | EvidenceBinding **v7** | 键为 `delivery_ref`；失败三分 ambiguous / infeasible / input_insufficient |
| Context | WriterContext **v10**（上游已切 Design+Grounding） | scene-centric Writer IR 类型在 `stage4_ir` / `compile_ir` 就绪；Stage5 全量切 IR 随 stage5 绞杀 |
| plan_chapters | **退役** | 不进 ARTIFACT_SPECS；职责拆回 Design weight / 预算纯函数 / Context provenance |

锁定边界见会话计划；Blueprint **本批（S4）冻结**不改 schema（已由 STR-03-S2-BP 另开 Blueprint V11）。公开 node id 不变。

- [x] Batch 0：本条登记 + `Stage4SourceView` ingress + `docs/schema/stage4.md` 底线
- [x] Batch 1：目标契约模型 + 反例 fixtures
- [x] Batch 2：Design 发号 / Grounding / Context 纯编译
- [x] Batch 3：一次切换正式链与 Stage5 reader（禁止新 IR→旧章库适配层）
- [x] Batch 4：死链清理 + 定向验证；勾选上表 stage4 为权威闭合

**验收**：上游只决定一次；Stage5 不猜顺序/身份/许可；缺输入 fail-closed；同输入重编译业务 payload 稳定。

#### STR-03-S2-BP · Blueprint V11 无损结构压缩

定位：**无损结构压缩 + 有明确边界的 states 抽取收紧**；不是尽量删字段。
单独开闸（STR-03-S4 已闭合；本批解除其对 Blueprint schema 的冻结）。
**不含** Stage5 全量切 Writer IR。

目标产物：`chapter_blueprints` **10 → 11**（破坏形状；拒读 v10；无通用迁移）。
版本钉与 schema/producer/consumer/恢复 **同一对外切换**；Batch 0 只登记、不单独发布未闭合 pin。

| 项目 | 裁决 |
|---|---|
| 统一 evidence 解析与按引用切片 | **做**（第一优先） |
| anchors 物理迁书级 | 可选次要（存储职责）；不当主要 token 收益 |
| `source_contexts` 稀疏编码 | **做**；保留列表名与基数；不改名为 scene |
| narration 默认值上提 | **做**；保留拍级 `beat_refs`；书级 default 不引用章级 `ap*` |
| `public_state_ref` | **本批默认保留**；删除须职责接管 + 身份隔离/跨章携入/状态解除三反例 |
| states 抽取收紧 | **做**；边界反例 + `plot_shape` 读数；禁止 HARD 配额；25–35 仅样本观察 |
| `story_before` | 保留 kind；本批仅去**完全重复登记**；不做邻接折叠 |
| `same_scene` / `relation_uses` / ActorRef·bindings | 保留（后两者第二阶段） |

恢复二分：

- **v11 内免费修复**：`revalidate` / `renormalize` 重算坐标或确定性展开视图；不改语义与来源身份。
- **v10 → v11 重建**：拒读后由 `stage2.blueprint` **付费整跑**；不默认承诺 revalidate 升级。

- [x] Batch 0：本条 + DECISIONS 登记（不 bump ARTIFACT_SPECS）
- [x] Batch 1：统一 evidence resolve/切片；切齐 Stage2/4 消费者
- [x] Batch 2：`source_contexts` 稀疏 + narration_default
- [x] Batch 3：states 反例/读数；story_before 去重；public_state_ref 保留
- [x] Batch 4：展开差分测试 + shape-break/stale + §9.5；一次翻转 v11

- [ ] 漏账（2026-09-23 F0 对账发现）：blueprint 子包重组后 stage4 对
      `novel.stages.stage2.blueprint.*` 的 10 条深导入边未进
      `tests/test_layering.py::test_cross_stage_imports_stay_on_the_package_surface`
      的 grandfather 清单（`novel/views/stage4.py` 对 `stage4.plan` 一条同理），
      对账测试红；要么消化入包级公开导出，要么随 STR-06-F8 旧链删除消失，补登记者答清走向

**验收**：展开(压缩(完整上下文)) 等价；Stage4 A 不因统一入口泄源序；v10 拒读；性能分列报告（模型 token / 正式字节 / 下游输入 / 重试 / 耗时）；states 质量观察单列。

#### STR-03-S2-CH · Characters v3（身份解析 → 消费闭合）

目标：先验证身份解析召回，再闭合 Global / Blueprint / Stage4～7 消费链。
**不**与 Blueprint v11 / Stage0 改动回退绑定；正式切换前必须通过隔离小样本（M0）。

链路：`MainText → 章 Extract（新 surface）→ 确定性字面回填 → 书 Resolver → Compiler → Characters v3`。

| 里程碑 | 内容 | 杀停 / 准入 |
|---|---|---|
| **M0** | 草稿 schema + 回填/编译 + 合成反例；隔离输出根真机试跑（另授权） | 女儿仍漏 / 裴知远未绑定 / 关系称谓回填差 / 成本失控 → 不升 ARTIFACT_SPECS |
| **M1** | Characters / Edit / 投影 v3 挂载；v2 拒读；R-2.4 翻转入契约 | 仍靠自由文本 role 做 claim⇔evidence → 回退 |
| **M2** | Blueprint `mention_uses` 分档义务；断 Stage4 `characters.role` 回退 | 仍能「ActorRef 合法但漏人」→ 不切默认 |
| **M3** | Stage5～7 / API/UI；新书默认 v3；analyze 退役条件闭合 | 恢复仍可能重复付费/实体 → 不关旧 path |

纳入谓词（不用次数）：专名/稳定称谓个体、情节参与者、必要前史因果、跨章 continuity 实体；排除无名群体与一次性背景集合。大儒与女儿均纳入。

三投影互不替代：`first_mention_chapter` / `identity_revealed_from` / 姓名类 `name_usable_from`。
删除 `role` 与旧 `first_chapter`；`name_distinctiveness` 待 Stage7 替代后再删。
`stage2.analyze` 联合首抽**不**与 M1 同批硬切。

- [x] Batch 0：本条 + DECISIONS 方向；`contracts/schema/characters_v3.py` 草稿（**不** bump ARTIFACT_SPECS）
- [x] M0 确定性：`candidates` 字面回填 + `compile_v3` + 合成反例测试
- [x] M0 真机：冻结 11 章样书、独立输出根、基线 Flash、成本信封（`tools/characters_v3_m0_pilot.py --yes`；产物 `outputs/m0_ch_v3_20260919_181339/…/characters_v3_pilot/`；通过条件全绿；2 wire≈55k tokens）
- [x] M1：正式 schema / checks / producer / 恢复；更新 BACKEND_CONTRACT R-2.*；Stage4 停止硬依赖 `role`/`first_chapter`/`aliases`；判据退役 `role_*` → `character_semantics_unverifiable`
- [x] M2：Blueprint `mention_actor_coverage`（R-3.7e）+ 切断 Stage4/shared/consumption 的 v2 双读
- [x] M3：Stage5～7 / views / naming / knowledge prompt / UI 只认 v3；新书默认 WorkflowManifestV9（`characters:3`）；**不**删除 analyze
- [ ] analyze 删除条件：恢复双付费未审计（旧 receipt/draft repair 消费者清零前保持未勾）。审计与替代工作由 STR-06 承接，删除归 STR-06-F8；不得提前勾选
- [ ] M1 漏账（2026-09-23 F0 对账发现）：characters / characters_edit /
      blueprint_character_projection 三条升到 v3 但未登记
      `tests/test_artifact_versions.py` 的 `_SHAPE_BREAKS` 账本（「由谁重建」未答），
      对账测试红；补账后恢复

**验收（M0）**：合成反例绿；真机另列。小样本通过 ≠ golden set。
**删除条件（analyze）**：新书默认 v3 + 旧 receipt/draft repair 消费者清零 + 恢复双付费审计通过 + REQUIREMENTS 勾选后删除联合首抽 path。当前 M3 已默认 v3，但恢复双付费未审计，删除条件保持未勾。

### STR-04 · 契约与文档随权威 path 收口

- [x] Stage2：`docs/schema/stage2.md`、`docs/BACKEND_CONTRACT.md`、相关注释改权威 owner path（批次 6）。
- [ ] 其余 Stage 切换权威时同步 `docs/schema/` 与必要的 BACKEND_CONTRACT 条目。
      STR-06 涉及部分指向 STR-06-F7/F8（版本收口与正式指南），其他范围保留。
- [ ] 退役路径进入 retired 登记；禁止为旧测试恢复废弃耦合（I-52）。
      STR-06 旧链退役登记与 retired 更新归 STR-06-F8。
- [x] Stage2 局部 `AGENTS.md` 已补；其余子目录按需要补，不复制根不变量全文。

### STR-05 · 工程基线与质量验收（权威链闭合后）

- [ ] 默认非 corpus 回归 + 前端 test/typecheck/seams/build（若波及 UI）。
      STR-06 迁移的整批验收唯一指向 STR-06-F8，仍需独立付费授权。
- [ ] 重建 current-schema corpus 基线（原 TODO-21 意图），再谈「全量语料绿」。
      保持独立事项；STR-06 的两本 Golden 不代替 corpus 验收。
- [ ] 真书 / golden set 质量验收单独授权、单独结论；工程绿 ≠ 成书质量通过。

### STR-06 · Stage2–Stage5 终态架构迁移（F0–F8，迁移进行中）

来源：Stage2 vnext 契约三轮真实验证（《夫君的报恩》单书、约 36 次真实付费）+
用户终态裁决「停止抽象、固定 contracts」。新链**完全替代现役 stage2–stage5**；
试点 vnext 契约形状（tb.source_refs、机械 grounding full/bridge、execution_table、
resolved_plan）**降为参考实现，不作冻结目标**——按旧形状迁移会引发第二轮 shape break。

- **Architecture 冻结**职责、权威源、依赖方向、窄修复和失败归属；**字段级 schema 保持
  RC**，经反例与真书验证后定版。
- F0–F7 在隔离 migration worktree／branch 和独立输出根内推进；现役链继续按旧契约验收；
  I-40、`R-5.2` 及相关公开契约的正式替代，归属 **F8 切换提交并一次合入生效**。候选内容
  可以提前准备，但主仓不得出现两套现役定义。
- 旧书保持 `legacy_incompatible`，在独立输出根按新工作流重建；不手补旧产物、不继承旧
  批准、不清除旧 ledger。
- 旧链切换前冻结新增依赖。旧测试先迁移仍有效的职责和独有反例，再删除，不按行数清理。

#### 终态模型（冻结）

四类 semantic truth，每类唯一职责：

| Truth | 唯一职责 |
|---|---|
| Characters | 源人物身份、人物初始状态、稳定约束 |
| Blueprint | 源事件、变化、揭示、因果及书级初始事实／约束 |
| Target Design | 目标初始语义选择、场景、目标拍、戏剧驱动、目标章归属 |
| Semantic Binding | Preservation 决定、source lineage、保留约束和目标承载 |

Source initial facts 来自 Characters 与 Blueprint；Target initial choices 归 Design；
Binding 约束这些选择，不维护第二份目标初始状态；`T₀ = Init(Compile(D,G))` 确定性派生。

链路与关键规则：

```text
SourceTruth → Grounding.Preserve → durable Preserve checkpoint
  → deterministic ReconstructionBrief → Target Design → Grounding.Bind
  → 完整 SemanticBinding canonical publish → Compile → Context / Mechanical Guard
  → Writer / Fidelity Judge → Accepted Prose
```

- Preserve 与 Bind 是同一 Grounding owner 内的独立请求。Preserve 只消费 SourceTruth、
  版本化 Policy 和执行配置，不接收 Design 或 Bind 失败结果。Obligation 是有 source
  lineage 的语义投影，可综合多个源主体；必保要求必须有实质满足条件，不被 reinforcement
  的 `0..N` 稀释。Preserve 结果保存为校验过、hash-bound、可恢复的 paid checkpoint；
  Grounding 确定性发布 Derived ReconstructionBrief；Design 不直接读 checkpoint 内部。
- Bind 完成后一次发布完整 `semantic_binding.json`，obligations 必须与采用的 Preserve
  结果一致；不发布半份 canonical Binding。unbound 保留原义务并返回正确 owner；Bind
  不得删义务迁就 Design。Design 修订不自动重购 Preserve。
- Design 只消费 ReconstructionBrief、IdentityProjection、OutputConstraints；Brief 保留
  核心语义和必要因果，屏蔽源章分组、源拍序列、原叙述顺序及证据定位。稳定 `char_id`
  合法；Target Design 不保存 source beat、evidence 或 binding refs。
- 输出保持 N 个非空目标章、连续章号 `1..N`；源情节允许跨章重构。Scene 归章由 Design
  决定，Compiler 只验证章数、归属、顺序和完整性。保留全书篇幅边界，按 Design 的章／场
  权重分配目标预算，不对照同号源章长度。Effect 必须 mechanically applicable；保留
  人物／关系、人物知情、世界事实、读者揭示等必要区别，不迁旧 PlotState ontology。
  前史／倒叙不得按叙述顺序误改当前世界状态；unknown、解除、未运行、合法空值分别表达。
  ExecutionPlan 编译为 establishment、constraint、dependency、reinforcement；语义等价
  归上游判断，Compiler 不解释自然语言补猜事实。
- WriterContext 是单份 Derived artifact，含 VerificationIR 与 WriterBrief，两者从同一
  执行输入确定性投影。Guard 仅机械验证；Judge 验证正文实现，保留
  `writer / upstream_ir / unverifiable` 归因，不编辑 truth。**Detection ≠ Ownership**：
  下游发现的问题回到需要改变 canonical truth 或 deterministic transform 的 owner。
- 同书严格串行，不做投机写作：`Project → Guard → Write → Judge → Commit acceptance →
  Apply planned delta → 下一场`。只有有效接受后才应用 `E.scene.planned_delta`；禁止从
  正文反抽 semantic delta；Writer 局部呈现细节要被后续情节依赖，必须由 Design 明确承接。
- 接受回执最小接口：`scene_id / attempt_id / prose_sha256 / writer_context_sha256 /
  execution_slice_sha256 / judge_contract_version / status`。`prose_sha256` 绑定替换
  人名前的正文；Context／slice 摘要绑定本场实际消费的规范化业务内容及必要契约身份；
  全书 plan hash、审计时间戳和无关 metadata 不作场景有效性硬匹配。入口、freshness、
  历史采用与 receipt 共用登记消费投影；恢复从**连续有效 accepted prefix** 重放 planned
  deltas；删除 runtime 后必须可重建相同前缀与 prose tail。
- 不建设 Merkle DAG、event sourcing、全局失效 DAG 或事务协调系统。

**窄修复**（一等不变量）：语义修复必须采用最小、可证明正确的 patch scope；无关已购
结果和有效 accepted prefix 不得因实现方便而全量重跑。Canonical：owner 提交有基线身份
的窄 patch，保留无关内容和稳定 ID。Derived：受影响 slice 失效并确定性重算。Prose：从
首受影响场景重新投影、核验；输入未变的结果继续采用。必要全局校验可扫描全书，但不把
扫描范围当付费重跑范围。只修第 9 场且前 8 场输入未变则前缀继续有效；实际改变前场约束
时按真实影响处理。

#### 产物与依赖

保留按 owner 分包的 novel/stages/stageN/ 结构；正式 schema 唯一位于 `contracts/schema/`，
局部 contract.py 不复制领域模型。路径机制按登记家族支持多级路径（三级子目录），并覆盖
INVALID、归档、文本和来源账；正文与付费 checkpoint 不作可随意删除的缓存。

| 家族 | 身份 |
|---|---|
| `stage2/characters/characters.json`（目标 v4）、`stage2/blueprints/chapter_blueprints.json`（目标 v12） | Canonical |
| `stage2/compiled/`（evidence index；可选 source state cache）、`stage2/acceptance/` | Derived / 审计 |
| 既有 checkpoint 存储 | 已购语义结果，不是下游读取接口 |
| `stage4/grounding/reconstruction_brief.json` | Derived |
| `stage4/design/presentation_graph.json`（目标 v1） | Canonical |
| `stage4/grounding/semantic_binding.json`（目标 v1） | 完整 Canonical snapshot |
| `stage4/compiled/execution_plan.json` | Derived |
| `stage4/context/scene_{nnn}.json` | 双视图 Derived |
| `stage4/acceptance/guard/scene_{nnn}.json` | 机械报告 |
| `stage5/scenes/scene_{nnn}.txt`、`stage5/acceptance/fidelity/scene_{nnn}.json` | 候选正文 / 判定与接受回执 |
| `stage5/rewritten_body.txt` | Stage5 最终正文，Assemble 唯一 producer |
| `runtime/` | continuity、tail、progress、cache 派生状态 |

依赖方向（AST layering 覆盖绝对和相对导入并保留分母自检；validator 输入不变和 truth
writer 边界补行为测试；F6 验收清零本批 Stage6/7 相关深导入）：

| 模块 | 允许 | 禁止 |
|---|---|---|
| contracts | 契约层内部合法依赖 | import novel |
| Stage2 | 公共契约与底层设施 | import Stage4／5 |
| Design | Brief／Identity／OutputConstraints 契约 | Grounding 实现、Blueprint internals、checkpoint 裸读 |
| Grounding | SourceTruth 公共投影、TargetDesign 契约 | Design 实现、直接修改 Design |
| Compile／Context／Continuity | 正式契约、登记投影、必要 runtime 输入 | LLM、语义补猜、producer 草稿 |
| Stage5 | WriterContext／acceptance 契约 | Stage2 internals、Design／Grounding 实现 |
| Stage6／7 | 正式契约、登记消费投影、accepted body 读取接口 | Stage2／4／5 internals |
| Guard／Judge | 验证输入和报告出口 | 写 canonical semantic truth |
| shared／repository | 通用原语及持久化边界 | 反向依赖具体 Stage 业务 |

**Stage6/7 消费投影在 F0 提前登记 RC**：Era／setting／world rules／relations／secrets
的新归属及目标有效视图；Characters 身份与称谓及目标实际建立／揭示／使用位置；
Scene→target chapter 映射与目标章集分母；accepted body 完整读取与目标开篇读取；名称
替换所需实体、关系、时代背景、候选文案及有效正文投影。源首次提及、身份揭示、姓名可用
时点仍是源事实；目标时点由 Design／Binding／ExecutionPlan 的明确语义位置派生，不能
直接换算源章号，也不能用 Binding lineage 猜缺失语义。F3 免费检查已有产物能否提供消费
投影所需信息；F4 用真实 Compile 结果干跑 planned projections；尚无 accepted body 或
Prepare 目标开篇时明确未就绪，不制造成功状态；F6 再验证真实完整消费链及付费语义质量。

**Prepare 坐标分离**（本轮已确认的必要接口适配）：目标开篇范围仍由现有 Stage6 Prepare
owner 选择；保留源钩子事实及其源正文／源钩子证据；增加基于 accepted target body 的
目标开篇范围与目标定位；两种坐标分别表达，源 `through_chapter` 不直接控制目标材料；
必要时显式升版现有 Prepare 契约，不新增节点或故事 truth；源文支持不自动等于目标文案
可用，Prepare／Hook 按各自材料和职责核对，不反写目标故事事实。

**接受正文与交付正文分离**：Stage5 receipt 绑定改名前正文；Stage7 根据有效 name_map、
选定 Hook 和 accepted body 做确定性替换与组装，另行绑定交付身份。同一 accepted body、
同一选定 Hook、同一有效替换／保留映射和组装契约，重放应得到相同交付字节；更换 name_map
不使 Stage5 接受回执失效，只影响 Stage7 交付结果。残留检查沿用 `claimed/kept` 语义：
拒绝未认领、应替换的残留；合法保留称谓及被认领区间内的字面不误报；不得用「所有源名
字串都不能出现」替代现役判据。

#### 迁移批次

| 批次 | 实施及验收 |
|---|---|
| **F0 · 架构与 RC** | 建立最小 schema、Policy、Brief、场景消费身份、路径与依赖契约；登记 Stage6/7 投影、Prepare 坐标分离及 Golden rubric 迁移；准备 F8 契约切换清单 |
| **F1 · 反例** | Preserve 隔离、必保满足、source-blind、typed effects、四类执行语义、双视图、局部身份和窄修复；补交付确定性、claimed/kept、改名前接受字节与交付字节分离反例 |
| **F2 · Source Semantics** | Characters／Blueprints 抽取编译、Fold／query、owner 窄 patch 与已购恢复；定向 unit／contract、stage 和真书冒烟 |
| **F3 · Target Semantics** | Preserve checkpoint→Brief→Design→Bind→完整发布；目标归章／权重／修复；第二本 artifact-only smoke，并免费检查 Stage6/7 消费信息闭合 |
| **F4 · Execution IR** | Compile、Init／Apply、局部投影、双视图、机械 Guard 和预算；两本 `--no-prose` 加消费者投影干跑，只模拟 planned continuity |
| **F5 · Realization** | Writer／Judge、场景回执、串行推进、恢复、历史采用、Assemble；迁入试点 labeled 反例，两本代表性成文冒烟；核对参考知识是否完整接管 |
| **F6 · 下游闭合** | Stage6 Prepare／Hook 和 Stage7 Rename／Assemble 消费适配，必要契约升版；相关深导入清零；真书验证 Hook 三候选、目标开篇、改名质量、免费改选及交付重放 |
| **F7 · 工作流与公开面** | 编排阶段依赖、窄修复与串行；API／Views／Frontend；RC 收口，版本、manifest、epoch 组合闭合；完成目标契约候选和 Golden 工具适配 |
| **F8 · 验收与切换** | 删除已替代旧链、更新 retired 和正式指南；完成 I-40／`R-5.2` 等切换提交；契约 §9.5、默认非 corpus 回归和前端完整验证→Golden→一次合入切换 |

批次进度：

- [x] **F0 · 架构与 RC**（2026-09-23，隔离分支内完成）：RC 契约子包
  `contracts/schema/str06/`（policy / grounding / design / execution / context /
  receipt / consumption / families / dependency；不进 ARTIFACT_SPECS 与 NODES，
  反向守卫钉在 tests/test_str06_contracts.py）；`novel/paths.py` 新增
  `check_rel_family` 与 `family_input_key`（登记家族内三级准入、来源账键用完整
  路径；两级一般规则不变）；pyproject packages 同步；RC 契约文档与 F8 切换清单
  见 [str06.md](schema/str06.md)；定向 contract 测试 25 项通过。
- [x] **F1 · 反例**（2026-09-23）：迁移期工作包 `novel/stages/str06/`（F8 分流后
  删除；包内纪律由 AST 守卫钉住：只准依赖 contracts / novel.errors / 本包 /
  标准库 / pydantic）。`checks.py` 跨产物机械判定器：Binding 分母一致（丢义务/
  混入/身份不符三红一绿）、plan↔graph 完整性、receipt 三 hash 绑定（旧 PASS 配
  新 Context/slice 拒绝）、窄修复两段计算（`directly_affected` 付费重写判定 +
  `entry_sensitive_closure` 重投影闭包；扫描范围≠付费重跑范围）。`delivery.py`
  确定性交付组装与 claimed/kept 残留判定（重放字节稳定、长名优先即契约、kept
  不误报）。反例套件 `tests/test_str06_invariants.py`：Preserve 请求输入面隔离、
  双视图 continuity_fact_view、Brief 因果保留无源拓扑、scene 作用域 effect、
  跨章合并/拆分两 fixture 证明契约能力接通（能力接通≠生成质量，后者归 F8）、
  同号异义不混淆、name_map 更换不触碰 Stage5 回执。
- [x] **F2a · 确定性引擎前置（免费部分，2026-09-23）**：`fold.py` 确定性状态折叠
  （T₀=Init、establishment 按场景序应用、scene 作用域边界过期、established_by
  溯源、accepted prefix 重放重建；Guard/Judge/Context 共用同一引擎，无第二套
  Fold）；`guard.py` 写前机械 Guard RC 版——M1 义务覆盖、M2 提前揭示、M3 提前
  效应、M4 承载位置真实（新链无 bridge 拍，试点职责转译）、M6 在场合法性；
  M5 surface 时机依赖 surface 窗口引擎留 F4。试点 ce1–ce6 反例逐条迁移到新
  坐标（tests/test_str06_fold_guard.py），validator 输入不变有行为测试。
- [x] **F2b · Source Semantics 完成（2026-09-23，真书冒烟经用户授权）**：
  `contracts/schema/str06/source.py` Characters v4／Blueprints v12 字段级 RC
  （试点 B 档形状类型化：slug/beat/state/wc id 规则、surface 窗口、闭集、
  open 必填、relation 禁自环、章号连续）；`source_compile.py` 确定性 compile
  机械臂（book wire 分流两份 truth、章级跨产物闭合、knowledge_key 命题复用
  一致、双 owner 检测、相邻因果边机械归一删除、跨章方向校验）与
  `source_fold`／`initial_snapshot`（单一折叠实现，供 F3 Preserve 与逐章
  prompt 共用）；合成反例 10 项。真书冒烟（《夫君的报恩》，16 次真实付费、
  58.9k prompt + 164.1k completion，`outputs/str06_f2b_smoke_20260923_162444/`）：
  两份 canonical 从盘面重载过自身 schema；participants/causal 引用零悬空；
  knowledge_key 零命题漂移；fold 重放一致；surface until 全 null（纪律遵守）。
  体量与 B 档同量级（beats 83/78、effects 85/67、reveals 26/30）。
  **质量披露（不静默接受；2026-09-23 用户裁决：延后到全部接线完成、旧架构
  退役后的第二轮迭代统一优化，F3 前不修）**：①loop 开闭纪律退化——13 open
  仅 2 close（B 档 4/3），终章后 11 条悬念线未收束，loop 粒度过细（场内疑问
  被升格全书悬念线）；属 prompt 标定问题非 schema 缺陷，收紧方向=只收跨多章
  未决主线。②fold 事件 1 条：第 10 章 stable `wealth` 被改写，待人工对照
  源书复核（合理剧情或漏保信号）。
- [x] **F3 · Target Semantics 完成（2026-09-23，付费延续授权）**：
  `novel/stages/str06/grounding.py`（assemble_preserve：义务 id 唯一／requires
  闭合无环／lineage 引用真实 beat+char；checkpoint 内容 hash 稳定可重建；
  project_brief：event_core 义务→cores、去源坐标投影、IdentityProjection 从
  v4 公共投影；assemble_binding：beat_ref 真实拍＋分母一致复用 F1 checks）；
  `design_compile.py`（Design wire→PresentationGraph v1：beat 唯一、participants
  引用 char_id、delta 域闭集、归章闭合；**wire 键白名单**——多余键（含
  source_ref 类混入）在 compile 入口报错，Q-1 不静默）；`SourceObligation`
  RC 增 `requires`（义务间必要因果，禁自指）。合成反例 7 项。
  冒烟（**修订后基线 run `outputs/str06_f3_smoke_20260923_171800/`**，5 次付费、
  68.7k+104.1k；首个 run 165735 的 Binding 因场景依赖成环被 F4 compile 拒绝
  ——Bind prompt 未传义务 requires，承载分布违反拓扑；修订 = BIND_PROMPT 增
  依赖序硬约束后重跑。**成本披露：重跑建了新目录致 Preserve/Design 未命中
  断点缓存、多付 2 次调用；教训=续跑必须指向同一 run 目录**）：obligations 69
  （必保为主；brief cores 30）；Brief 零源坐标泄漏（regex 实检）；Design 28 场
  99 拍 11 章（场／章 2–4）；Binding 69 entries 全承载、0 unbound；
  checkpoint 从 wire 确定性重建、分母与身份一致；M4 全真实。首个 run 的
  跨章自由度证据（18 条义务跨 2–4 源章）仍有效（义务形状两 run 同源同分布）。
  Stage6/7 消费信息免费检查：`consumption_projection_check.md`——
  world_view/character_exposure/chapter_map/rename_materials 信息可得，
  body_access 及其依赖项**显式未就绪**（F5 产物），不制造成功状态。
- [x] **F4 · Execution IR 单书初版干跑（2026-09-23，RC1，问题由下述 RC2 修复）**：
  `compile.py`（四类执行语义：拍 delta→establishment+净终值 planned_delta、
  必保非事件义务→constraint、requires×承载→跨场 dependency（同场不建、
  场景级成环编译期拒绝）、reinforcement RC 空；`init_establishments`：
  v4 initial_state+v12 world_constraints→T₀ init 锚，knowledge 型映射
  `holder::key`）；`project.py`（双视图投影唯一产地：IR 含义务/entry 视图/
  M5 surface 引擎（出场即释放、canonical 需 identity 承载）/
  continuity_fact_view；brief 含任务/驱动/拍纲/约束/权重预算/口吻；
  `execution_slice_sha256` 与 context/receipt 三处同源）。
  `SceneExecutionPlan` RC 修正：establishments 允许同 (domain,key) 拍级覆盖
  （F0 唯一性校验误拒合法时序语义，删除）。
  **Guard M6 修正（干跑抓到真 bug）**：在场判定从 fold 状态键改为与 M5
  引擎同源的 graph 参与序（`appeared_chars`）——状态键≠在场集合，混用产生
  20 个假阳性。合成反例 4 项 + fold_guard 更新。
  干跑（`outputs/str06_f4_dryrun_20260923_172702/`，零调用）：28 场 M1–M4/M6
  零问题；planned continuity 重放一致；T₀ 21 条、终态 130 条；slice 身份
  28/28 唯一；预算按权重分配；消费者投影 chapter_map 干跑输出、目标开篇
  **显式未就绪**。
- [x] **F2–F4 · 真书审阅修复 RC2（2026-09-23）**：本次用户要求立即修复，覆盖上文
  “第二轮再优化”的暂缓安排。RC1 完成记录只表示当时工程冒烟，不表示语义质量验收。
  - Brief 改为全义务 requirements（含 satisfaction），去源坐标并消除数组源顺序；Binding
    发布完整义务。D/G 是 Compile 与 Context 的唯一正式输入，不再读 Preserve checkpoint。
  - Design 显式承担目标初始态、状态对象、知情主体/模式、作用域、实际读取与称谓释放；
    Fold 只重放 accepted prefix 的 planned_delta，scene 覆盖不污染长期底值。
  - 单一最终验收拍加较早支撑拍解决复合义务跨场，拒绝前置逆序与同场拍序错误；Context
    只投影实质要求、局部状态及明确支撑，消费摘要覆盖 POV/avoid/hint/satisfaction 等实际内容。
  - Source 校验补齐证据、effect 人物与 recipient 引用；最新章末快照严格串接；保留相邻实质
    因果，坏边不吞；修复 diamond DAG 假环、完整 beat_ref 误拒和 set 遍历摘要不稳定。
  - 新隔离入口 `tools/str06_workflow.py` 取代三个 `_tmp_str06_*` 执行 harness（历史文件仅作
    参考，不能继续执行）。语义 prompts 迁入包内；沿用直连 transport，原响应先持久化，
    请求身份缓存复用，机械失败免费恢复，无自动整份付费修复。成本仍唯一记 service ledger。
  - 本书修订与证据：`outputs/str06_repair_20260923/`（不提交），原书全文对齐、修订前后
    SHA manifest、独立 wires、逐 owner 采用记录与全部 28 场 Context；11 章、69 义务分母
    保持，Guard 零问题，source fold 零告警。本轮没有新增模型调用。
  - 临时 adapter：`Str06Repository` / `PaidRequestSession` / `tools.str06_workflow` 只服务隔离
    RC 链，F8 接入正式 Store、节点调度与执行许可后分流/删除；不注册第二条现役路径。
    RC1 缺失语义不自动迁移；显式 wire 审阅与 hash-bound manifest 是免费恢复入口。
  - 验证记录：RC2 定向 96/96、契约与路径 484/484 通过；真书修改 ob55 满足条件只使
    s27 消费身份变化，其余 27 场保持。JUnit、replay 与验收报告位于上述输出根。
    边界检查剩一项旧主链深层 import 失败，Store 测试因旧 Stage0 缺失符号阻断收集；
    涉及文件与 HEAD 一致，未算全仓通过。第二本、真实新购 token 对比与 Stage5 成文质量
    保持未验证，不据零错误机械报告推进 F5/F8 验收。
- [ ] **F3b · 第二本 artifact-only smoke**：第二本书 Stage2 全链抽取＋F3 链
  （付费约 15+ 次），回答 schema 表达力多样性；待用户单独授权后执行。
  F4 的「两本干跑」中第二本同样依赖此授权。
- [ ] F5–F8 未完成。

F6 不承担无关 Stage6/7 本体重构。跨章能力验证：F1/F4 用明确跨章布局的反例证明能力
接通；F8 报告 Design 是否实际使用跨章自由度及对应证据，未使用时明确记录「真实生成中
的跨章质量未获验证」，不强迫模型为指标搬移情节。

#### 待办精确归属

| 原条目 | 处理 |
|---|---|
| STR-03 Stage0／Stage1 | 原样保留，不吸收 |
| Stage2 analyze 删除条件 | 审计与替代工作由 STR-06 承接，删除归 F8；不得提前勾选 |
| Stage4 遗留 adapter／恢复反例 | 分别迁入 F3–F5，删除归 F8 |
| Stage5 整体绞杀 | 指向 STR-06-F5，旧链删除归 F8 |
| Stage6／7 | 消费及必要接口适配由 F6 承接；节点本体的其余绞杀保持独立 |
| STR-04 文档与 retired | STR-06 涉及部分指向 F7/F8，其他范围保留 |
| STR-05 工程回归与 Golden | 本次迁移的验收唯一指向 F8，仍需独立付费授权 |
| STR-05 current-schema corpus 基线 | 保持独立事项；两本 Golden 不代替 corpus 验收 |

#### 参考件清单及迁移判定（参考非契约）

以下材料均为**参考非契约**；不恢复旧 source-ref Design、统一 ExactlyOne、semantic
Guard 或旧 adapter。报告中的调用估算与模型配置保留证据范围，不直接提升为全局政策。
原始基线至少保留至 F5 验收及相关反例迁移闭合，本次不清理这些文件。目录身份用清单
哈希（`find <dir> -type f | sort` 逐行「相对路径 字节数」的 SHA-256，取前 16 位）。

Downloads（用户本机 `~/Downloads`，不进仓）：

| 参考件 | SHA-256 | 用途 / 已知限制 | 判定 / 批次 |
|---|---|---|---|
| stage2_contracts_vnext.schema.json | `f67f77cfa66961c0840cd3710127986b1e56cf7ce410bad0f9cbed37866bc4f` | vnext Stage2 提案（characters surfaces 时间窗、blueprints effects/causal_edges + 确定性 fold）；形状≠终态 v4/v12 | 改写 → F2 |
| presentation_graph.schema.json | `b3ff6a48ecd80a0e37cf3abe4ab887ac3aa642c3b8ad38fbe7c3d85e82314c4` | vnext Design 草案；含 tb.source_refs / split 禁用等旧形状，终态 Design source-blind | 退役（参考）→ F3 |
| evidence_binding.schema.json | `be47c311fb90731791a60923b0e17e6d634c1716ac6d7f9f5da9dd731456606` | vnext Grounding 草案；终态改 Preserve+Bind 两职，形状不同 | 退役（参考）→ F3 |
| writer_context_v1.schema.json | `0464b9538c1838a67812d1536c070e45ae2cf7bf722c669d2e42e58ca3bdf18` | 章粒度投影草案 | 退役（参考）→ F4 |
| writer_context_v2.schema.json | `cde8ed0bf8d559faff805d87f2473bd6bd21260f3516a38c23d2aa9d479bd77` | 场景粒度单视图草案；终态改双视图 | 改写 → F4 |
| writer_context_projection_spec.json | `3412a00829b95bccc83f8092e6bf886bb3805608ddfa9d0aec6e41b03c5b31c` | 8 步确定性投影说明 | 改写 → F4 |
| stage4_contract_manifest.json | `f7b214e39cf1856171f8d97786ae78b49a577c3bd41bdc3cde6a84879fc75af` | 试点契约清单（stage4-v3）；非冻结 manifest | 退役（参考）→ F0 |

工作区 harness（未跟踪 `_tmp_*`，保留至对应批次迁移闭合）：

| 参考件 | SHA-256 | 用途 / 已知限制 | 判定 / 批次 |
|---|---|---|---|
| _tmp_vnext_run.py | `7b88931cfe5d38616b7590508aabda507effd0a796ab2dc40c03c8f8ba426be2` | M0 首轮 Stage2 vnext 重抽；断点续跑防重复付费 | 改写 → F2 |
| _tmp_vnext_check.py | `86630c70972e3b276093c6e42917030c1c2686300ab7240bb8d3dfd5515f664a` | 契约公理机械检查器（C1–C9，与 Judge C1–C3 同名不同物） | 改写 → F2 |
| _tmp_stage2_patch.py | `d24ced8c83bb12508b03a835c5e0a26c70c0033b25a4ed2985167df7173b1441` | Stage2 定点修订 patch receipt（窄修复案例载体；receipt 非第二 Source Truth） | 改写 → F2 |
| _tmp_vnext_stage4.py | `90cf6194eae366ffc25bb9a82687bbd26eb0c9b1201f443f3326cb77a7c1f8f9` | Stage4 全链 harness，含 fold 引擎与 established_by 溯源 | 改写 → F3/F4 |
| _tmp_vnext_stage4_check.py | `e910ee8af4f43360a25d548e98833fa57d8cc408428af0d388b649732c9d2d3b` | fixtures 回归（G1 越界/G2 首现颠倒/G3 丢内核/SPLIT/COV/R1） | 改写 → F1/F3 |
| _tmp_vnext_guard.py | `9d32872566ac360d586f67436f14dec630a5a17ed554e63ae8e82186f5c6fd2b` | 机械 Guard M1–M6 + 语义三查 + r1 批量标定 + ce1–ce6 反例构造 | 改写 → F4 |
| _tmp_vnext_judge.py | `954c9d363b2d41b0b2a79494975fe50e02d4ea36bede1737daa54c281b80ec81` | Judge 首版（含跨场回指误报史） | 参考 → F5 |
| _tmp_vnext_judge_slim.py | `856ecb82d1e861fcaebaaf734c63aae9fe180b3950b8f53170e6abf036f1d78a` | slim Judge + C1–C3 + labeled 断言 | 改写 → F5 |
| _tmp_vnext_writer.py | `f603361b72dd0c78974a67219be98586e16907210fddf3a2dea7dc5af8be202c` | 投影修复 + 成文 harness（口吻/禁补造规则、--prior-events） | 改写 → F4/F5 |
| _tmp_calib_under_spec.py | `e602d5df1f175582cc11f2d20febcbfd8e7db2f1a183786588c6fc5b622a8186` | under_spec 定向合成探针（union 真不足用例） | 改写 → F1/F4 |
| _tmp_str06_f2b_smoke.py | `0526526554ddf0767fd0737a031575e55e84186e74cf31434876d689898274af` | F2b 真书冒烟 harness（断点续跑；复用试点 prompt/证据切段；授权付费） | 改写 → F3 复用同一 SourceTruth |
| _tmp_str06_f3_smoke.py | `06b83df858970cb6c62aeefdad42c4f7a56ff1d1aeb1a01e7b055ea16bdbdbd9` | F3 冒烟 harness（Preserve/Design/Bind 付费 + 依赖序硬约束修订版 + source-blind 实检） | 改写 → F4/F5 |
| _tmp_str06_f4_dryrun.py | `77e8e69161131a2dd7e6bc63a52cbded4f5816025ff95527257bd9d854d6c4a8` | F4 --no-prose 干跑（零调用：Compile/投影/Guard/连续性/消费投影） | 改写 → F5 |
| _tmp_str06_consume_check.py | `12b5357b9ae36e12731b1924b795a7cd9a45a308409ed2381317cfbd7740661f` | Stage6/7 消费投影信息闭合免费检查 | 改写 → F4 干跑 |
| _tmp_vnext_probe.py | `f4fbd26b6ddb51f83b86c4c9b0e9bc35530496b5dea1ccf2a1e2f8fb41056ac4` | 现役盘面形状一次性探查 | 退役（一次性） |

（`_tmp_prep_m1_run.py` 属 Characters v3 M1 冒烟准备，与 STR-06 无关，不登记。）

outputs 冻结基线（目录身份为清单哈希）：

| 参考件 | 清单哈希 / 文件数 | 用途 / 已知限制 | 判定 / 批次 |
|---|---|---|---|
| outputs/vnext_stage4_r1 | `ba79870da0140e92` / 43 | v2 形状全链 r1（12 场 79 拍；正文 b 档语境） | 参考 → F3 |
| outputs/vnext_stage4_r2 | `4ee702aaa91b7a5d` / 76 | v3 前身 r2（60 拍、义务全量唯一 owner、span 预算） | 参考 → F3/F5 |
| outputs/vnext_stage4_bbase | `746428d9dae517b0` / 15 | b 档确定性重投冻结对照 | 保留至 F5 |
| outputs/vnext_probe_20260920_191609 | `5a642b9bf9849284` / 41 | 首轮 vnext probe + WriterContext v1 全书成文 | 参考 → F2/F4 |
| outputs/vnext_probe_b | `b073d5e608f0878f` / 35 | B 档契约二轮重抽 | 参考 → F2 |
| outputs/vnext_probe_b2 | `1fe3bf083401d3cc` / 4 | b2 定点修订档（三处 patch） | 参考 → F2（窄修复案例） |
| outputs/review_stage4_c | `370bde60701dddf8` / 3 | 第三轮验收报告目录（下三件） | 知识来源 → F1/F5 |
| outputs/str06_f2b_smoke_20260923_162444 | `b9947b7b74f7b972` / 15 | F2b 真书冒烟（v4/v12 canonical + wire 缓存 + report；loop 纪律与 stable 改写两披露） | F3 的 SourceTruth 基线 |
| outputs/str06_f3_smoke_20260923_171800 | `00643d55abc21f2a` / 7 | F3 修订后基线（依赖序硬约束 Binding；F4 干跑输入） | F4 的 Target Semantics 基线 |
| outputs/str06_f3_smoke_20260923_165735 | `ee538f40232b883d` / 7 | F3 首跑（Binding 场景成环，被 F4 compile 拒绝；保留为依赖序反例证据） | 反例 → F1 职责 |
| outputs/str06_f4_dryrun_20260923_172702 | `3f9fa94c471a82cb` / 6 | F4 干跑（execution_plan/context 样例/Guard 报告/终态快照） | F5 的执行输入基线 |
| ├ taxonomy.md | `017f6c42940e69d9f4c41927bb0326197ecda9638d6a5b11c3de212bad0e0e52` | 六列错误分类学 + 判据边界 + 成本台账 | 知识摘要母本 |
| ├ labeled_assertions.json | `a4e0463f42fe1ad221095c1322b8c6bc3d26befee67d0638e2f647e04e348915` | 11 条 labeled 回归断言（正/负例） | 迁入 F5 |
| └ judge_slim_report.json | `3d1d91988099ee0ec2b4f6ca64f2f1408b95cc590118da1e6a9b1228ad299256` | slim judge 判定原始报告 | 证据 |

#### 参考知识摘要（逐项标注判定；避免重新购买同样的标定知识）

**Guard 机械 M1–M6（写前、免费）——保留，迁 F4 机械 Guard**（字段名随新契约改写）：

- M1 义务覆盖：本场 owner 的义务全部进入写作输入的 target_beats；
- M2 提前揭示：entry_knowledge 某 holder 的 established_by 位置不得 ≥ 本场起点；
- M3 提前效应：entry_state 键的 established_by 位置不得 ≥ 本场起点（或前缀折叠中不存在）；
- M4 bridge 非法承载：bridge 拍不得携带任何义务；
- M5 surface 时机：cast.available_surfaces 必须等于引擎释放集；
- M6 cast 合法性：cast ⊆ 累计合法在场人物，且人人有已释放 surface。
- 标定事实：两代 run 各 12 场机械全绿；6 个伪造反例（提前揭示/提前效应/义务缺失/
  bridge 承载/提前 surface/非法 cast）全部各报一次；ce1–ce6 反例构造器随 harness 保留。

**Judge 精度规则 C1–C3（写后）——保留，迁 F5 Fidelity Judge**（类级表述，不含实例）：

- C1 时间精确性：义务给定的相对时间以义务为准；同句多类别分别核对 + 全量枚举；
- C2 一般→个体推定禁令：一般性/背景性陈述不得推定为个体事实；
- C3 POV 知情起点：POV 人物何时知道/醒来/察觉，以义务与 entry_knowledge 为准。
- 标定事实：首轮无规则正例 3/6，加规则后 6/6；labeled 回归 11/11（正例全抓、负例零误报）。

**语义 Guard 三查（conflicts / injection / under_spec）——改写（RC 证据）**：健康盘面
9 条发现全 low confidence、6 场零发现、0 high ⇒ **不固化 always-on**；接异常路径
（stage2 patch 后重投验证、design 变更验证、跨书新 fixture）。under_spec 的 union
（brief+源事件+context）可恢复性判据是对的；union 真不足的合成探针已验证可触发
（high confidence）。已知判据边界：「brief 级压缩误导」（试探丢宾语）在 union 层可
恢复、写前不该报，错误发生在写手服从压缩 brief——若要写前拦截需另设 brief↔
source_event 一致性检查（design_context_conflict 子类），**不放宽 under_spec 判据**
（列入 F1 反例候选）。

**已知误报／漏报清单——保留为反例资产，迁 F1/F5**：

- 漏报：试探丢宾语（写前 union 可恢复；写后由人工发现，归属 design）。
- 类命中非实例命中：河岸救人由 `design_free_text_injection` 类命中（引句落在姊妹注入）。
- 误报根因与修复：judge 缺前场义务导致跨场回指误报（四条合法回指全中招）→ 根因修复
  是输入加 continuity_fact_view，不是放宽判据；r1 s07 两条新增 deviated 经复核为旧版
  漏报的真实压缩损失，登记为改进而非退化。
- 枚举不稳定：同一场景多违规时单轮可能只报部分，需「列出全部违规」显式指令；labeled
  集仍需多轮稳定性抽验。
- 两臂互补：义务臂与 facts 臂任一命中即算（当天→次日由义务臂 deviated 抓、告老由
  facts 臂抓）；slim r2 九场结果来自 C 首轮 prompt（未含 C1–C3），负例回归未复验
  （约需 9 次付费，未跑）。

**局部修复案例（origin → earliest observable layer → repair owner → minimal
invalidation）——保留，窄修复不变量的实证**：借出方翻转（Stage2 b07_01 主客体错误）
经 patch p02 修复后，request hash 差集实测 = {s07,s08,s09,s10}（断言 ⊇{s08}），只付费
重写 s08（guard+rewrite+judge 共 3 次调用），s07/s09/s10 输入未变继续采用；重判
14/15 fulfilled。三载同窗 patch 后重抽写手自行写对（0 次额外修复调用）。计划级
Continuity 使措辞级改写不向后雪崩；「向你借」人称转换歧义最终以 Stage2 patch 消除——
错误按归属处理，不做全量重跑。

**截断配置经验——保留（调用工程规约）**：新组件付费调用一律 `max_tokens=65536` +
宽容 JSON 提取（剥代码围栏）。本轮 3 次可避免浪费：guard 首跑 16k 被 thinking 吃满
截断 ×2、未剥围栏解析失败 ×1。成本台账单列于 taxonomy.md（计划 28–29、实际 ≈36）。

**旧原型形状知识——退役（防第二轮 shape break）**：tb.source_refs canonical 放置、
scene.source_placement 单真相、split 禁用论证、execution_table 义务唯一 owner、
resolved_plan、机械 grounding full/bridge 由 design 意图生成——这些属原型 v2/v3 形状，
终态以 source-blind Design + Preserve/Bind + execution_plan + 语义四类执行语义取代；
仅其反例与论证过程保留参考价值。WriterContext v1 章粒度 8 步投影、v2 场景粒度单视图
由双视图（VerificationIR + WriterBrief）取代。`_tmp_vnext_check.py` 的契约公理 C1–C9
（与 Judge C1–C3 同名不同物，迁移时防混淆）改写进 F2 正式 checks。

#### 验收、Golden 与停止点

必须验证（F8 汇总）：Preserve 恢复不重购；无半份 Binding；unbound 不删义务；Design
实际输入无源叙述拓扑；合法跨章布局可编译、写作、接受并交付且章数/非空约束成立；局部
修改只使真实受影响输入失效，前缀及无关已购结果保留；旧 PASS 配新正文／Context／slice
拒绝；崩溃后可重建 accepted prefix；Guard 机械反例写前阻断；Judge 错误正确归属、未知
不伪装通过；源章／目标章同号但语义不同的样例不能错误读取开篇或推导姓名许可；Prepare
源证据与目标开篇分别有效；Hook／Rename 在新材料下过真实语义验收；Stage7 改名不回写
Stage5、交付可确定性重放、残留判断遵守 claimed/kept。

Golden 冻结：输入集、源版本、baseline 成书、rubric/version、评价方式、模型辅助评审的
prompt／provider／model／参数、输出根和成本信封；沿用人工阅读验收，模型评分只作辅助；
评价过程变化时重评双方现有成书，不因此重跑生成。样本至少两本代表性书、其中至少一本
长篇或多线结构；不得临时缩减已有冻结 manifest 分母；书目在显式 `NOVEL_DATA/
NOVEL_CORPUS` 下核实后冻结（本轮未据源正文确认代表性）。Structural Originality 以
书／场景及语义承载关系比较，不要求同号章对应；源书、新书、baseline 的证据位置分别
记录；「遵守源书阅读时序」判据随目标语义版本化改写，保留事实、因果、披露和改名质量
覆盖。六维评价：Core Preservation、Structural Originality、Causal Continuity、
Character Continuity、Natural Progression、Local Prose Coherence；未评或证据不足保持
未验证。Golden 不代替 STR-05 的 current-schema corpus 验收。

Cutover 条件：四类 truth、唯一 writer、依赖与完整消费链闭合；Preserve／Bind 隔离；
窄修复和场景身份真实生效；恢复可重建 accepted prefix；交付可重放；工程回归及冻结过程
下的逐书 Golden 验收通过。未实际验证的跨章质量范围必须随验收结论披露。

成本与风险：基础调用估算 = Stage2 实际抽取 + Preserve 1 + Design 1 + Bind 1 +
2 × 目标场景数 + Stage6/7 实际调用；修复、重写、重判、重试及未知成本分列；F4 与早期
消费投影干跑为零模型调用。真实付费验证独立授权、显式 `--yes`、正文外发分别授权。
持续登记：漏保／过保、源结构泄漏、under-spec、状态过建模、checklist prose、Judge
误判、过度失效、下游语义材料不足、请求容量、Golden 成本。

**文档落盘批次（2026-09-23）只运行了 `git diff --check`、`tests/test_docs_references.py`、
`tests/test_coverage.py` 后停止。同日用户指令按 F0–F8 批次推进；F0 已完成（见批次进度），
后续批次到付费节点（F2 真书冒烟起）须独立授权、显式 `--yes`，正文外发分别授权。**

## Compatibility adapter 登记


Stage2 顶层 shim 已在批次 5 清零。后续节点的 adapter 在此追加。

| adapter 路径 | 服务的旧消费者 | 删除条件 | 目标删除批次 |
|---|---|---|---|
| `RETIRED_ARTIFACT_PATHS['stage4.plan_chapters']` | 仅拒读旧盘面 | 已退役；无新写 | STR-03-S4 已闭合 |
| 内存 `PlanChapters` 编译视图 | stage4.plan HARD / Context 组装 | Writer IR 全量切换且 Stage5 不再经 plan 形时删除类 | STR-06-F5（旧链删除归 F8） |
| 机械书 aggregate 崩溃恢复用例 | `test_mechanical_aggregate_crash_*`（已 skip） | 迁入 STR-06（F3–F5 反例迁移），改写为 binding-only 崩溃恢复后删除 skip | STR-06-F8 |
| typed HARD 停止/指纹熔断用例 | `test_a_owned_failure_*` / `test_same_fingerprint_*`（已 skip） | 迁入 STR-06（F3–F5 反例迁移），与 binding INVALID + witness 路径重对齐后删 skip | STR-06-F8 |

## 条目生命周期

1. 开工前写清：输入、预期权威 path、成本/授权、受影响守卫与删除条件。
2. 改动覆盖真实消费者、失败与恢复、文档和测试；不以占位入口存在为完成。
3. 验收通过或裁定不再推进时，从本文件删除勾选项正文；历史只回写 Git 与 DECISIONS。
