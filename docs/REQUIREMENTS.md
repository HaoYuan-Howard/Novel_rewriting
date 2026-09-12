# 活动待办

本文件是项目唯一的活动待办清单，只保留尚未完成、仍值得推进且有明确验收条件的事项。
已完成能力、撤回方案与实验过程由 Git 和 `docs/DECISIONS.md` 回答；产品规格、
冻结契约与证据基线见 `docs/BACKEND_CONTRACT.md`，可迁移经验见 `docs/LESSONS.md`。

**编号不重排。** 已删除的编号保持空缺，避免历史提交中的引用改指另一件事。

⚠ 默认 pytest 刻意排除 corpus。只有 TODO-21 完成，并按 `tests/README.md` 使用
`--require-corpus` 对精确匹配的无正文 manifest 与盘面完成验收后，才可以称“全量语料绿”；
此前的通过结果只能称“非 corpus 回归”。

## P0 · 当前工程实施

### TODO-50 · Stage6/7 人工前端免费编辑范围待裁决

开发 harness、操作 skill 退役、运行身份、无 Markdown 交付及前端统计已闭合，规则归所属指南与契约。
尚未确定的扩展是 Stage6 文案编辑、Stage7 人物表编辑：`docs/schema/_api.md` 五j 与
`novel/api/routes/edits.py` 仍明确限定为 CLI。已向用户建议撤回旧限制，尚未收到该范围的裁决。
若纳入，服务只暂存同书 typed 请求，由 CLI 按当前主体校验、归档和写盘；编辑后重新审批，
验证页面编辑、旧批准失效、重新批准、下载及重启读回。此扩展不阻塞现有自动跑书验收。

### TODO-49 · 小说流水线 token 与耗时优化

现役设计见 BACKEND_CONTRACT 与 schema，性能配对协议见 `docs/OPERATIONS.md`；首轮、局部返工和故障恢复分开验收，最终一次整根切换。
工程基线由 `24e4cf4` 推进至当前 worktree；已实现行为见当前契约，历史验证只见 DECISIONS/Git。

**本轮批准的契约重构**：`plot-blueprint-v1` 使用完整 beat 正文、稀疏状态/约束、统一证据和
KnowledgeLinks；Blueprint V9 按章及跨章审阅，Stage4 以 placement/delivery 在本章分场，
普通分场不重开 Stage2。保留章级 B、两次 Stage4 审阅和章级写作。详细责任边界见
`docs/schema/plot_blueprint.md`。贯通、依赖失效、身份/问题历史和免费恢复须同批闭合。

- [ ] 清理 V8 正式消费与编辑路径，迁移共享测试夹具并保留拒绝、漂移、恢复反例。
- [ ] 至少两章的完整生成/章审/跨章/KL/A/设计审阅/B/结构审阅/Context/写作与交付贯通。
- [ ] 验证普通分场不重开 Blueprint、必要端点准确绑定、实际依赖精确失效及问题不会被删除或回滚消除。
- [ ] 验证轻量进度、完整节点与分场对照页面，HTTP/CLI、刷新和服务重启结果一致。
- [ ] 固定新代码与干净数据根，完成第 1/5/6/11 章重点语义及原书 11 章成稿验收，
      记录请求体、用量、耗时、调用/返修与 ledger 成本；工程合成测试不代替真实模型验收。


已接入人物/知识/Blueprint 三 producer、两 Commander、Stage4 实际依赖与历史采用、义务继承、
知识关联及披露边界、Stage5 紧凑整章任务书、不可变人工追加额度、原 profiles 续跑和指标 v3。
旧版兼容通道已按授权退役。这些工程能力仍须完成以下验收，不能视为性能目标已经通过：

- [ ] 完成《夫君的报恩》单书真实消费者链：Blueprint Commander → Stage2 免费收口 → Stage4～7 → 下载 → 服务重启读取。
- [ ] 真书核 Stage4 局部/多来源/全采用、无待审免费提交及回答/决定/prepared/正式写回中断恢复；19 章只改第 17 章时无关章零新增请求/审阅，第 18 章实际边界变化正确失效。
- [ ] Plan 不变但 Blueprint 证据改变、候选从唯一变多义时，旧决定/绑定失效；修改或删除问题仍完整复审，来源损坏保持 unknown。
- [ ] 真书核人物/知识三 owner 派修、规则变化不重抽无关 Blueprint、场景重排披露边界、对白语义与必要字面例外。
- [ ] 冻结三书样例、两臂干净代码/源 hash/配置/输出及人工质量收据，按 OPERATIONS 完成九组配对；首轮、返工、恢复分开。
- [ ] 逐节点完成 4 对 6/8 并发实验，验证每书/供应商总限制、失败、重试与 token 不退化。
- [ ] 完成追加额度、原配置续跑及重启累计账的真实链路；无进展或 unknown 不因追加而解除。
- [ ] 与 TODO-21/28/31 同批闭合 current-schema corpus、真实 API fixture 和产物形状验收。
- [ ] 最终冻结批次完成根 AGENTS 规定的回归及前端门槛；备份 main 后按已授权方案用验收通过的 worktree 替换。
- [ ] 停止 admission、排空并证明 owner 停止，通过 preview/apply 整根归档旧输出；新生产使用独立空根，回滚配对旧代码与旧根。

**当前授权与交付范围（2026-09-11）**：继续隔离根中的《夫君的报恩》，并验收
《夫君不行，但我有孕啦》。用户因 Codex 连接超时改用已配置的 Claude CLI / 智谱 GLM 5.3 Flash，
两书的 Executor、Commander 均明确设为 `max`，能力仅发布 `low/high/max`。
原 OpenAI 配置、失败尝试及已购成果作为原始证据保留；换模型走显式新运行，累计预算不重置。
两书原 OpenAI 外发与付费及本次指定智谱路线的必要调用沿用会话授权，不重复询问。
先冻结已验收工程代码并提交，
旧 owner 排空后走正式续跑；实际停点、额度和用量只读对应 `_service` 权威记录。
两书通过真实交付验收后，备份 main 并用本 worktree 的已验收代码替换。速度、token 与稳定性
保留失败和恢复分母；不同书目、供应商及 effort 的观测比较不冒充固定条件的模型配对实验，
未执行的 Terra 请求不能记作 Terra 实测结果。
上述两书交付不代表本条其他 corpus、九组配对或局部恢复实验已完成，也不自动归档既有输出根。

**2026-09-12 执行边界更新**：用户明确“先不跑真实的全书验收”。本轮先完成工程实现、
合成书贯通与页面验证；真实全书模型质量、性能和成本验收保持待办，正式切换继续等待验收条件。

TODO-45～48 的有效恢复/质量反例并入本条；旧书恢复和“五轮三十分钟”不再是活动目标。
失败、追加额度调用和未知保留分母；未完成或质量失败不能因便宜计作节约。

## P1 · 质量闭环与诊断可信度

### TODO-21 · 建立 current-schema 现役语料基线

**缺口**：corpus 闸、manifest schema 与关闭式验收器已经存在，但仓内仍没有一份由当前
producer 从来源完整生成、且与盘面精确匹配的无正文 manifest。因此现有非 corpus 回归不能证明
真实全链、版本、来源账与 skip 面闭合。

**工作**：

- 通过前端自动跑书，在隔离输出与独立空缓存中，对
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
Pydantic 响应模型直接校验。Stage2、Stage4、Stage5 与 Stage7 的形状变化已经让部分 fixture
无法通过对应的 `contracts/schema` 模型，而前端 seam 只检查“谁能读取”，检查不到载荷漂移。

**工作**：

- TODO-21 产出一本文本链完整、current-schema 的书后，从真实 HTTP 端点重抓 backend_v1 fixture；
  合成示例只保留无法安全抓取且确有专门判别价值的分支，并明确标注来源。
- 建立闭合登记表：每个 JSON 必须映射到对应后端响应模型，或登记显式例外及独立守卫；登记表键集
  必须等于目录 JSON 文件集，新增文件不得静默漏验。
- `enums.json` 继续由既有 `payload()` 对账；`style.json` 的 legacy adapter 例外必须显式登记；
  其余映射使用 `StrictModel(extra='forbid')` 双向抓缺字段和退役字段。

**验收**：全部 fixture 均被登记表覆盖；普通 fixture 通过现役响应模型；两个例外各有独立守卫；
故意删登记项、增加字段或删除必填字段时测试都会失败。

### TODO-30 · 盲规划、结构读数与独立人工批准闭环

**定位**：这是独立质量研究，不阻塞稳定工作流交付。Stage4 已有去序 A/B、可复算结构读数与
hash-bound 人工结构门；尚缺 current-schema 真书对照来回答“重构是否可辨认且不伤事实安全”。
没有 ground truth 时，不能用非 corpus 测试或自动阈值替代人工盲评。

**工作**：在不覆盖正式产物的实验面冻结同一份 current Stage2 主体，比较旧规划与当前盲规划；
B 只绑定证据，不静默改变 A 的场景顺序、事实分组、首尾锚点或 retain/reconstruct。两臂、书名、
版本和生成顺序遮蔽后逐章盲评，至少区分：顺叙本来合理、入口/分组/披露确有重建、只改措辞、
为追指标强行重排。标签必须绑定章节与证据，并与现役读数逐项对照。

**验收**：事实、因果、人物状态和保真材料仍闭合；每章明确记 retain/reconstruct；人工能指出具体
入口、组合或披露差异；每项 metric 最终得到“继续只报不挡 / 候选阈值需产品裁决 / 当前不可判”之一，
证据写入 `docs/DECISIONS.md`。`R-5.25` 工作包只缩小阅读范围，不制造盲评标签。

### TODO-31 · 下一次新 base 同批收紧 Stage4→5 盘上契约

**当前状态**：最小主体的前一批基线已通过默认非 corpus 回归；本轮 V9 重构仍须按最终改动集合重新验收，
旧结果不作为本轮通过证明。WriterContext v8 保留章级唯一剧情目录和逐场许可，不重复保存同号来源、
源字数和预算副本；API 从当前场景与 Stage1 现场派生。
逐章人物名单被写手、Stage6/7 实际读取，因此保留。Chapter v3、Review v3、Rejected v2、
TrimAudit/TrimResolution v3 均只保存承重主体、必要历史基线或精确绑定证据。
这些 shape break 与 TODO-49 同批发布；旧书只留在整根归档，不原地迁移。

**验收**：相关 shape break 均登记 current version 与 `_SHAPE_BREAKS`；不迁移、不手补旧产物；
Stage5 写作、recheck/revalidate、Review、trim、s5 审批及 Stage6/7 只读最小主体；删字段前后 prompt、
机械 findings、正文拼接和审批语义不变。关系若没有真实 WriterContext consumer，不复制到展示字段。

## 条目生命周期

1. 开工前补足可复现输入、预期最终状态、成本与受影响守卫。
2. 改动覆盖真实消费者、失败与恢复路径、文档和测试，不以类型或占位入口存在为完成。
3. 验收通过或经证据裁定不再值得推进时，立即从本文件删除；历史只回写 Git 与
   `docs/DECISIONS.md`。
