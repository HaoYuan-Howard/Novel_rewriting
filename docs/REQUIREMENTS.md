# To-do list

本文件是项目唯一的活动待办清单。已完成事项不在这里保留；历史原因由 Git 和
`docs/BACKEND_CONTRACT.md` §十 回答。产品规格、冻结契约与证据基线见 `docs/BACKEND_CONTRACT.md`。

**编号不重排。** TODO-01 ～ TODO-26 中已完成的已删除；留下的号空着——Git 提交信息按号引用，
重排会让那些引用指向别的条目。

⚠ **默认 pytest 刻意只跑非 corpus 套件。** 最终硬验收必须显式使用
`--require-corpus`，同时提供隔离的 `NOVEL_OUTPUT` 和与该盘面精确一致的无正文 manifest
（字段契约见 `docs/schema/corpus.md`）。当前 manifest 尚未生成（TODO-21），因此任何通过数字
只能称为“非 corpus 回归”，不能称为“全量绿”；也不得引用任何历史“0 failed”数字充当当前证据。

优先级含义：P0 影响正确性、真实花费或恢复能力；P1 影响质量闭环、诊断可信度或兼容性；
P2 是明确可延期的工程化与体验增强。勾选前必须满足条目中的验收条件，而不是只增加类型、按钮
或占位函数。

## P1 · 质量闭环、诊断与兼容

### TODO-21 · 建立 current-schema 现役语料基线

- [ ] 在隔离输出与独立空缓存中，用最终代码从来源重新生成两本书的 Stage0～Stage7 产物；所有
  产物必须由现役 producer 按 `ARTIFACT_SPECS.current_version` 写出。当前 registry 同时包含
  v1/v2/v3 家族：Stage2 global/blueprints、Stage3 style、Stage4 plan、Stage5 chapter/review、
  Stage6 hook 与 Stage7 map 已为 v3，其余以注册表为准。
  不给旧 JSON 手补 `_schema_version`、`root_clean_sha1` 或其他字段冒充当前产地。
- [ ] 生成并提交无正文 manifest：逐书记录 sid、source hash、章节数、预期产物与版本、代码身份、
  冻结命令、ledger 聚合及精确 skip allowlist。终审成本以 `<base>/_service/ledger.jsonl` 为
  唯一权威源，不与产物 usage 或日志混加。
- [ ] 对同一盘面运行 `--require-corpus` 的完整 `corpus or not corpus` 套件；任何书目/产物减少、
  版本漂移、ledger 不闭合或新增 skip 都必须失败。
- [ ] 语料闸可用性前置（**已完成大部分，剩余见下**）：`-m corpus` 批要在**当前**盘面上给出
  可解释读数——2026-08-18 由 251 红降到 16 红 / 82 跳过 / 800 通过。剩余失败逐条对应真实
  产物或代码问题，**不得以「已知问题」记账**。当前剩余一类：沙箱里
  `novel/orchestrator/approvals.py::backfill_from_disk` 只回填得出 `s0/s1/s3`。
  〔原第二类（stage7 交付拒绝抛抽象基类 `TerminalState`，而 `status_of` 刻意不给它
  状态码 ⇒ 经 HTTP 变 500 而不是 409）已于 2026-08-18 收口：`assemble.py` 现在
  **每一处 raise 都是具体类**，三个新类 `UpstreamUnproven` /
  `BlockingFindingsUnresolved` / `DeliveryIncomplete` 均已登记进
  `novel/api/errors.py::STATUS`。〕

⚠ `tests/corpus.py::requires_current` 产的跳过**不进本 manifest 的 skip allowlist**：
`contracts/schema/corpus.py::CorpusSkipExpectation` 会拒收它们，而**那是对的**——本条要求的
验收盘面是从来源整批重生成、`code_identity` 唯一的，那样的盘面上这些前提恒成立、跳过恒不发生。
⇒ 它们是**过渡态**；验收跑里出现任何一条，就说明那份盘面没有真的整批重生成。

历史中间产物 `outputs/skill_e2e_schema_v1_20260816` 不是 current-schema 验收证据，
不能为最终 manifest 提供通过数字。

与最终基线相连、但尚未取得实证的付费项：

- **FC-08**：四项密度基线已完整下发给写手，仍需按冻结的两书 42 章范围复跑，证明漂移命中逐书
  不增加、合计严格下降且 short-run 偏差不恶化。
- **FC-13**：逐节点 effort 覆盖位与受限 A/B 工具已落地；`stage3.style` / `stage7.rename` 在付费
  实测满足质量、UNVER/INFO、fallback/wire/error 和 completion 门之前继续保持默认 high。

验收：所有被测产物由现役写盘路径生成；完整套件的通过/跳过/失败数、每条 skip 理由和上述付费
实验结论一并记录，未完成项不因代码位或预览工具存在而销账。

## 条目生命周期

1. 开工前补足可复现输入、预期最终状态和受影响的守卫。
2. 改动同时覆盖真实消费者、失败路径、文档与测试，不以“能构造 schema”为完成。
3. 验收通过即删除条目；不要在活动清单里保留已完成施工史。
