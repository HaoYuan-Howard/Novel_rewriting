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

历史中间产物 `outputs/skill_e2e_schema_v1_20260816` 不是 current-schema 验收证据，
不能为最终 manifest 提供通过数字。

与最终基线相连、但尚未取得实证的付费项：

- **FC-08**：四项密度基线已完整下发给写手，仍需按冻结的两书 42 章范围复跑，证明漂移命中逐书
  不增加、合计严格下降且 short-run 偏差不恶化。
- **FC-13**：逐节点 effort 覆盖位与受限 A/B 工具已落地；`stage3.style` / `stage7.rename` 在付费
  实测满足质量、UNVER/INFO、fallback/wire/error 和 completion 门之前继续保持默认 high。

验收：所有被测产物由现役写盘路径生成；完整套件的通过/跳过/失败数、每条 skip 理由和上述付费
实验结论一并记录，未完成项不因代码位或预览工具存在而销账。

### TODO-24 · 长期积累不可确定语义真值并校准自动审判

本项是**长期活动项**，不阻塞当前由人或 agent 辅助执行、复核、修订和重跑的工作流。
fail-closed 基础已落地：缺真值、缺证据、judge 分歧或 profile 不匹配保持 `UNVER`/人工待审，
不汇总成自动 `PASS`（实现与私有数据边界见 `docs/schema/evaluation.md`）。尚未完成的是从真实
生产分布积累候选、冻结 ground truth 并证明 judge 达到自动放行门槛。

- [ ] 在日常生产使用中持续保留可复算的候选材料：来源与阶段产物哈希、题材、能力、family、模型、
  prompt/schema/decoder 身份、自动与人工/agent 结论、证据理由及修复前后关系。候选产物不是
  ground truth；正文、盲包、标签和仲裁工作文件留在私有目录，仓库只接收无正文 manifest/profile。
- [ ] 定期运行零付费供给审计，既收集 `FAIL` / `UNVER` / 自动结论被推翻的难例，也随机抽取正常例；
  同一来源的原样、扰动和修复前后样本归入同一 family，避免训练/提示材料与 held-out 泄漏。现役
  八类能力及供给硬门以 `docs/schema/evaluation.md` 为准；未纳入真值的其他语义能力继续 human-only。
- [ ] 供给满足预登记硬门后再冻结 held-out：manifest 只对**实际登记了 case 的能力**
  要求至少 100 个独立 family、正反例与跨题材覆盖均达标；尚无 case 的能力不在 manifest
  中伪造样本。随后完成两人独立标注、第三方仲裁和三轮 A/B 校准。`EvaluatorProfile`
  仍恒覆盖八类能力；manifest 未登记的能力必须 `enabled=false`、`n=0` 且全部指标为 0。
  只有固定准确率/一致性门槛全部通过，才为对应能力启用自动 `PASS/FAIL`；
  dataset、模型、prompt、schema 或 decoder 漂移即重新失效。

长期验收：最终 manifest 的样本量、标签平衡、题材覆盖、独立 family、标注一致度和三轮评测均满足
冻结契约；报告分别呈现确定性检查、语义评测与人工结论。计划轻重、施受关系、潜台词、视角等尚无
正式真值的判断，不得因单次 LLM judge 或词法命中为零而宣称 hard pass。

### TODO-27 · 前端残留的显示阈值与第四态推导收归服务端下发

`web/DELIVERY.md` §6 自查表中 U-08 / U-11 的两处 ⚠️：前端仍在重算业务判定，违反
「前端只呈现判定，不补后端没给的结论」。

- [ ] 产物新鲜度第四态由服务端直接下发 `freshness: 'missing'`，前端删除
  `web/src/constants/vocabulary.ts::freshnessOf` 里的 `!exists` 推导。
- [ ] 拍级「预算/源文」出档阈值（<0.9 / >1.1）与锚点覆盖率警示阈值（0.6）由服务端下发，
  前端删除这两个显示阈值（章级 `in_band` 已有服务端下发的先例）。

验收：前端代码不再含这两处阈值字面量与 `freshnessOf` 推导；真实模式下三处显示与服务端下发值
一致；`npm run typecheck` 与 `npm run seams` 通过。

## 条目生命周期

1. 开工前补足可复现输入、预期最终状态和受影响的守卫。
2. 改动同时覆盖真实消费者、失败路径、文档与测试，不以“能构造 schema”为完成。
3. 验收通过即删除条目；不要在活动清单里保留已完成施工史。
