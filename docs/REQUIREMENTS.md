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
| stage2（analyze / characters / global / blueprint） | **权威闭合**；Blueprint 契约 **STR-03-S2-BP v11** | `blueprint/`、`analyze/`、`characters/`、`knowledge/`、`shared/`；双臂生产在 `*/producer.py`；`extract.py` 仅薄转发 | 顶层 shim 已清零；V11 见 STR-03-S2-BP |
| stage4（design / plan / context） | **权威闭合（STR-03-S4）** | 见 STR-03-S4；`plan_chapters` 已退役 | `RETIRED_ARTIFACT_PATHS['stage4.plan_chapters']` |
| stage5（write / review） | 未开始 | — | — |
| stage6（prepare / hook / approve） | 未开始 | — | — |
| stage7（rename / assemble） | 未开始 | — | — |
| orchestrator / repository / LLM 传输共享面 | 随消费节点收敛 | — | — |

**验收（单节点）**：I-01～I-38 与 S-01～S-05 对该节点可检查；下游只经正式 artifact。
**验收（全书）**：上表节点族全部 authoritative；adapter 清单清零或仅余有日期的只读解码。

#### STR-03-S0 · Stage0 单次机械 transform（v3）

目标：去掉 revision 链 / pending / 候选 / 人工编辑；权威一次 `Stage0Transform`，
闸门为 mechanical `admission`；正文可从原文 − removed 重建。

- [x] schema v3 + ARTIFACT_SPECS 升版拒读 v2
- [x] `revisions.py`：make_transform / commit / ensure_materialized
- [x] producer / eligibility / checks / CLI / views / source_identity 对齐
- [ ] 定向回归闭合后勾选本行完成；存量书一律重建

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

**验收**：展开(压缩(完整上下文)) 等价；Stage4 A 不因统一入口泄源序；v10 拒读；性能分列报告（模型 token / 正式字节 / 下游输入 / 重试 / 耗时）；states 质量观察单列。

### STR-04 · 契约与文档随权威 path 收口

- [x] Stage2：`docs/schema/stage2.md`、`docs/BACKEND_CONTRACT.md`、相关注释改权威 owner path（批次 6）。
- [ ] 其余 Stage 切换权威时同步 `docs/schema/` 与必要的 BACKEND_CONTRACT 条目。
- [ ] 退役路径进入 retired 登记；禁止为旧测试恢复废弃耦合（I-52）。
- [x] Stage2 局部 `AGENTS.md` 已补；其余子目录按需要补，不复制根不变量全文。

### STR-05 · 工程基线与质量验收（权威链闭合后）

- [ ] 默认非 corpus 回归 + 前端 test/typecheck/seams/build（若波及 UI）。
- [ ] 重建 current-schema corpus 基线（原 TODO-21 意图），再谈「全量语料绿」。
- [ ] 真书 / golden set 质量验收单独授权、单独结论；工程绿 ≠ 成书质量通过。

## Compatibility adapter 登记

Stage2 顶层 shim 已在批次 5 清零。后续节点的 adapter 在此追加。

| adapter 路径 | 服务的旧消费者 | 删除条件 | 目标删除批次 |
|---|---|---|---|
| `RETIRED_ARTIFACT_PATHS['stage4.plan_chapters']` | 仅拒读旧盘面 | 已退役；无新写 | STR-03-S4 已闭合 |
| 内存 `PlanChapters` 编译视图 | stage4.plan HARD / Context 组装 | Writer IR 全量切换且 Stage5 不再经 plan 形时删除类 | 后续 Stage5 绞杀 |
| 机械书 aggregate 崩溃恢复用例 | `test_mechanical_aggregate_crash_*`（已 skip） | 改写为 binding-only 崩溃恢复后删除 skip | 后续 Stage4 恢复专项 |
| typed HARD 停止/指纹熔断用例 | `test_a_owned_failure_*` / `test_same_fingerprint_*`（已 skip） | 与 binding INVALID + witness 路径重对齐后删 skip | 后续 Stage4 typed failure 专项 |

## 条目生命周期

1. 开工前写清：输入、预期权威 path、成本/授权、受影响守卫与删除条件。
2. 改动覆盖真实消费者、失败与恢复、文档和测试；不以占位入口存在为完成。
3. 验收通过或裁定不再推进时，从本文件删除勾选项正文；历史只回写 Git 与 DECISIONS。
