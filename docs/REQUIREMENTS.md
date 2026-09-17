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
- [x] 旧 `plot_*` / `contract` / `run_state` / `paragraphs` / `character_refs` 顶层路径为薄 shim；见下方 adapter 表。
- [ ] 补最小 contract / unit 反例，证明旧耦合无法回流（随 shim 删除批闭合）。

**验收**：blueprint 新 path 为权威；shim 有删除条件；样板可被 analyze/characters/global 复用。

### STR-03 · 按 Stage 逐节点绞杀

下列为默认顺序，用户可改序；每完成一节点在此勾选并写删除条件指针。

| 节点族 | 状态 | 权威 path | adapter / 删除条件 |
|---|---|---|---|
| stage0 | 未开始 | — | — |
| stage1 | 未开始 | — | — |
| stage2（analyze / characters / global / blueprint） | blueprint + analyze + characters + knowledge 权威；extract facade 待拆 | `blueprint/`、`analyze/`、`characters/`、`knowledge/`、`shared/` | 见 adapter 表 |
| stage4（design / plan / context） | 未开始 | — | — |
| stage5（write / review） | 未开始 | — | — |
| stage6（prepare / hook / approve） | 未开始 | — | — |
| stage7（rename / assemble） | 未开始 | — | — |
| orchestrator / repository / LLM 传输共享面 | 随消费节点收敛 | — | — |

**验收（单节点）**：I-01～I-38 与 S-01～S-05 对该节点可检查；下游只经正式 artifact。
**验收（全书）**：上表节点族全部 authoritative；adapter 清单清零或仅余有日期的只读解码。

### STR-04 · 契约与文档随权威 path 收口

- [ ] 每个切换权威的节点同步 `docs/schema/` 与必要的 BACKEND_CONTRACT 条目。
- [ ] 退役路径进入 retired 登记；禁止为旧测试恢复废弃耦合（I-52）。
- [ ] 子目录 `AGENTS.md`（contracts / novel / tests / web）按需要补局部细则，不复制根不变量全文。

### STR-05 · 工程基线与质量验收（权威链闭合后）

- [ ] 默认非 corpus 回归 + 前端 test/typecheck/seams/build（若波及 UI）。
- [ ] 重建 current-schema corpus 基线（原 TODO-21 意图），再谈「全量语料绿」。
- [ ] 真书 / golden set 质量验收单独授权、单独结论；工程绿 ≠ 成书质量通过。

## Compatibility adapter 登记

| adapter 路径 | 服务的旧消费者 | 删除条件 | 目标删除批次 |
|---|---|---|---|
| `novel/stages/stage2/plot_*.py`、`contract.py`、`run_state.py`（薄 re-export → `blueprint/`） | 过渡期测试与未改完的深 import | 消费者清零；`test_layering` 不再允许旧路径；定向 plot/blueprint 测试改权威 import | stage2 批次 5（拆 extract 同批） |
| `novel/stages/stage2/paragraphs.py`、`character_refs.py`（薄 re-export → `shared/`） | 同上 | 同上 | stage2 批次 5 |
| `novel/stages/stage2/analyze_recovery.py`、`analyze_draft_recovery.py`（薄 re-export → `analyze/`） | 过渡期 CLI/测试 | 消费者清零后删除 | stage2 批次 5 |
| `novel/stages/stage2/character_checks.py`、`character_rebind.py`（薄 re-export → `characters/`） | 过渡期 | 消费者清零后删除 | stage2 批次 5 |
| `novel/stages/stage2/grounding.py`、`global_*.py`、`knowledge_prompts.py`、`source_blurb.py`（→ `knowledge/`） | 过渡期 | 消费者清零后删除 | stage2 批次 5 |
| `novel/stages/stage2/core_evidence.py`、`normalize.py`、`global_evidence_candidates.py`（→ `shared/`） | 过渡期 | 消费者清零后删除 | stage2 批次 5 |

## 条目生命周期

1. 开工前写清：输入、预期权威 path、成本/授权、受影响守卫与删除条件。
2. 改动覆盖真实消费者、失败与恢复、文档和测试；不以占位入口存在为完成。
3. 验收通过或裁定不再推进时，从本文件删除勾选项正文；历史只回写 Git 与 DECISIONS。
