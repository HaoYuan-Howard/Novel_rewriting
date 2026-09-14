# 活动待办

本文件是项目唯一的活动待办清单，只保留尚未完成、仍值得推进且有明确验收条件的事项。
已完成能力、撤回方案与实验过程由 Git 和 `docs/DECISIONS.md` 回答；产品规格、
冻结契约与证据基线见 `docs/BACKEND_CONTRACT.md`，可迁移经验见 `docs/LESSONS.md`。

**编号不重排。** 已删除的编号保持空缺，避免历史提交中的引用改指另一件事。

⚠ 默认 pytest 刻意排除 corpus。只有 TODO-21 完成，并按 `tests/README.md` 使用
`--require-corpus` 对精确匹配的无正文 manifest 与盘面完成验收后，才可以称“全量语料绿”；
此前的通过结果只能称“非 corpus 回归”。

## P0 · 当前工程实施

### TODO-51 · Stage2 联合分析、独立产物与分阶段验收

按用户 2026-09-12 最终计划实施；本条的新授权覆盖 TODO-49 中暂缓本书重构后验收的旧停点。
正式契约分别在 `docs/schema/characters.md`、`docs/schema/stage2.md`；人物登记与 Global 保持
各自证据、版本、编辑、失效及恢复。原 `docs/schema/stage2_review.md` 已随 Stage2 语义审阅
退役删除（2026-09），Stage2 下游前置统一由机械准入承接。

**2026-09-13 验收范围更新**：用户将本轮真书验收收在 `stage2.blueprint`；完成原文核对、
免费订正与统一机械准入核验后停止，不再推进 Stage4～7 真书或付费调用。Stage2 语义审阅
（Global 逐项审阅、章审与跨章复核）已同批退役，语义未审以报告 unver 行与交付披露承接。
Gate A～C 的代码仍须完整闭合，Stage4～7 以工程消费者、恢复、API/UI 与回归验证；
不据此声称真书成稿或交付质量通过。

**2026-09-14 工程基线范围更新**：用户收窄本批验收——先用一本小说跑通 stage0～stage2，
Stage4 及以后的链路逐步重构。本批测试收口以 stage0～stage2 定向选择加被波及共享面
（store/cli/api/permit/grants/transition）为准，完整非 corpus 回归全绿门槛顺延到
Stage4～7 链路重构批次。V5→V6 显式迁移用例（`tests/test_workflow_transition.py` 的
`setup_stage5` 组）依赖完整 Stage2→4→5 生产链构造合法 v5 迁移来源；现役 v7 链不再产出
退役 review 文件与 `stage2_reviews` 历史审批键，来源证明无法通过，用例已在
`frozen_stage5` 按来源证明守卫带原因跳过，链路迁移批次闭合后自动恢复。

- [x] Gate A：一次 Analyze Executor、两个独立编译器、Characters v2 / Global v7、统一
      Global Commander；闭合 CLI/API、待审、共享首抽统计、typed 编辑及已购回答/半边失败恢复。
      已在 `f5a95aa` 完成指定小说原文核验及免费 typed 恢复，接受署名 `codex`。
      C1 误接受、C2～C4 格式拒绝原样保留；不声明模型正常路径自动通过。
      冻结证据为 `E:\NovelAcceptance_Stage2CoreV2\reports\gate-a-f5a95aa.json`。
- [ ] Gate B：Blueprint / Stage4–5 接动态状态和 ActorRef/ActorBinding；按 BP v10 /
      BlindCandidate v6 / EvidenceBinding v6 / WriterContext v10，直接检查工程 Writer
      请求中的未来身份、关系与别名映射；KnowledgeLinks 与同章揭示载体已随语义审阅退役，
      不再检查；真书只闭合 Blueprint 机械准入与原文核对，语义未审随交付披露。
- [ ] Gate C：Stage6 Prepare 独立 E/C 与免费编辑恢复，Stage7 改名、API/UI、下载及重启读回。
      验证 Prepare 五载体 v1、Hook v4、HookSemanticReview v3、NameMap v3，以及显式
      V5→V6 离线过渡保留 Stage4–5 产物、审批、profiles 和累计用量。
      最终新工作流基线统一执行非 corpus 回归与前端 test/typecheck/seams/build，并附页面截图。

Gate A 使用 `E:\NovelAcceptance_Stage2CoreV2`：source 为指定小说的字节一致副本，output
为独立产物根；`NOVEL_DATA` / `NOVEL_CORPUS` 指 source，`NOVEL_OUTPUT` 指 output。
已核验源 SHA-256 为 `c717b0e567cf7aff7ac35caf3c665be00341a39b246e205327f8686ec75a234c`。
Executor / Commander 固定 `claude-code-glm / glm-5.3-flash[1M] / high`，已获付费与全文外发授权；
每个 E/C 单元基础四次，不自动追加、重置或切模型。跨 Gate 冻结新代码、排空旧 owner、开新
执行 epoch，保留任务身份、原 profiles、累计费用和可证明复用产物。

首轮与接受后的 core 字符压缩、实际草稿候选去留、语义主体干预、修复影响、调用/耗时从
不可变回答与唯一 ledger attempt 取证。证据/审计另列，未知保持 unver；无同配置旧基线不报
相对性能改善。单书 Gate 语义验收、工程回归与项目 golden set 分开结论。

### TODO-50 · Stage6/7 人工前端免费编辑范围待裁决

开发 harness、操作 skill 退役、运行身份、无 Markdown 交付及前端统计已闭合，规则归所属指南与契约。
尚未确定的扩展是 Stage6 文案编辑、Stage7 人物表编辑：`docs/schema/_api.md` 五j 与
`novel/api/routes/edits.py` 仍明确限定为 CLI。已向用户建议撤回旧限制，尚未收到该范围的裁决。
若纳入，服务只暂存同书 typed 请求，由 CLI 按当前主体校验、归档和写盘；编辑后重新审批，
验证页面编辑、旧批准失效、重新批准、下载及重启读回。此扩展不阻塞现有自动跑书验收。

### TODO-49 · 小说流水线 token 与耗时优化

现役设计见 BACKEND_CONTRACT 与 schema，性能配对协议见 `docs/OPERATIONS.md`；首轮、局部返工和故障恢复分开验收，最终一次整根切换。
现役完整剧情与分场边界见 `docs/schema/plot_blueprint.md`；工程验证记录保留在 Git。

本轮执行边界以 TODO-51 的 2026-09-13 更新为准；以下真书 Stage4～7、成稿与性能实验保留为
未验事项，不属于当前继续执行的授权范围，也不因工程回归通过而勾销。

- [ ] 待 `stage2.global` 与 `stage2.characters` 重构完成后，固定新代码与干净数据根，
      完成第 1/5/6/11 章重点语义及原书 11 章成稿验收，
      记录请求体、用量、耗时、调用/返修与 ledger 成本；工程合成测试不代替真实模型验收。


已接入 Analyze 联合首抽、独立人物/知识修订与 Blueprint producer、统一机械准入、Stage4 实际依赖与历史采用、
Stage5 紧凑整章任务书、不可变人工追加额度与原 profiles 续跑。
原 Stage2 两类 Commander、义务继承与知识关联及披露边界已随语义审阅退役（2026-09）；
旧 `tools/workflow_metrics.py`、`tools/workflow_pairs.py` 与 `tools/stage2_acceptance.py`
读数/配对工具同批退役，其读数主体是已退役的 Stage2 Commander 盘面（见 `docs/OPERATIONS.md`）。
旧版兼容通道已按授权退役。这些工程能力仍须完成以下验收，不能视为性能目标已经通过：

- [ ] 完成《夫君的报恩》单书真实消费者链：Stage2 统一机械准入 → Stage4～7 → 下载 → 服务重启读取。
- [ ] 真书核 Stage4 局部/多来源/全采用、无待审免费提交及回答/决定/prepared/正式写回中断恢复；19 章只改第 17 章时无关章零新增请求/审阅，第 18 章实际边界变化正确失效。
- [ ] Plan 不变但 Blueprint 证据改变、候选从唯一变多义时，旧决定/绑定失效；上游修订后按统一机械准入与 freshness 正确失效。
- [ ] 真书核人物/知识/蓝图各 owner 的编辑与复验、规则变化不重抽无关 Blueprint、场景重排披露边界、对白语义与必要字面例外。
- [ ] 冻结三书样例、两臂干净代码/源 hash/配置/输出及人工质量收据，完成九组配对；首轮、返工、恢复分开。旧读数/配对工具已随 Stage2 语义审阅退役，先在 v7 节点面重建只读读数工具再执行。
- [ ] 逐节点完成 4 对 6/8 并发实验，验证每书/供应商总限制、失败、重试与 token 不退化；依赖上项重建的读数工具。
- [ ] 完成追加额度、原配置续跑及重启累计账的真实链路；无进展或 unknown 不因追加而解除。
- [ ] 与 TODO-21/28/31 同批闭合 current-schema corpus、真实 API fixture 和产物形状验收。
- [ ] 最终冻结批次完成根 AGENTS 规定的回归及前端门槛；备份 main 后按已授权方案用验收通过的 worktree 替换。

**历史授权范围（2026-09-11，当前暂停执行）**：继续隔离根中的《夫君的报恩》，并验收
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

**2026-09-12 执行边界更新**：本轮只收尾 Blueprint 工程、合成书贯通与页面验证。
真实模型质量、性能和成本验收待后续 `stage2.global` 与 `stage2.characters` 重构完成后再安排。
用户取消旧数据归档，`E:\NovelAcceptance` 由用户自行处置；不再执行归档、恢复或数据删除。
新契约真实运行仍使用干净输出根，不沿用旧审批；正式切换继续等待验收条件。

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

**缺口**：完整剧情链的 `web/src/fixtures/plot_v1/flow.json` 已由合成贯通与真实 HTTP 投影生成，
并由响应模型闭合校验；`backend_v1` 保留的辅助样例和历史拒绝态仍缺完整登记表。
历史 Stage2 原文和检查仅作证据反例，不进入运行包。前端 seam 检查读取边界与退役字段，
不能代替载荷的 Pydantic 校验。

**工作**：

- TODO-21 产出一本文本链完整、current-schema 的书后，从真实 HTTP 端点重抓 backend_v1 fixture；
  合成示例只保留无法安全抓取且确有专门判别价值的分支，并明确标注来源。
- 建立闭合登记表：每个 JSON 必须映射到对应后端响应模型，或登记显式例外及独立守卫；登记表键集
  必须等于目录 JSON 文件集，新增文件不得静默漏验。
- `enums.json` 继续由既有 `payload()` 对账；无消费者的 Stage3 `style.json` 与旧 Stage4 示例
  已退役，不再保留 legacy adapter 例外。保留的历史证据须有独立守卫；其余映射使用
  `StrictModel(extra='forbid')` 双向抓缺字段和退役字段。

**验收**：全部 fixture 均被登记表覆盖；普通 fixture 通过现役响应模型；历史证据例外各有独立守卫；
故意删登记项、增加字段或删除必填字段时测试都会失败。

### TODO-30 · 盲规划、结构读数与独立人工批准闭环

**定位**：这是独立质量研究，不阻塞稳定工作流交付。Stage4 已有去序 A/B、可复算结构读数与
hash-bound 人工结构门；尚缺 current-schema 真书对照来回答“重构是否可辨认且不伤事实安全”。
没有 ground truth 时，不能用非 corpus 测试或自动阈值替代人工盲评。

研究入口须使用当前完整剧情及 placement 契约。旧 BPv5/Planv2 双臂实验与固定 Flash 配置的
JSON mode 探针不属于现役接口，其工具及专属旧协议测试已退役；历史代码可从 `bbc3a71` 恢复。
这不取消下面的独立质量研究，也不代替正式管线的别名隔离、来源闭合或付费恢复反例。

**工作**：在不覆盖正式产物的实验面冻结同一份 current Stage2 主体，比较旧规划与当前盲规划；
B 只绑定证据，不静默改变 A 的场景顺序、事实分组、首尾锚点或 retain/reconstruct。两臂、书名、
版本和生成顺序遮蔽后逐章盲评，至少区分：顺叙本来合理、入口/分组/披露确有重建、只改措辞、
为追指标强行重排。标签必须绑定章节与证据，并与现役读数逐项对照。

**验收**：事实、因果、人物状态和保真材料仍闭合；每章明确记 retain/reconstruct；人工能指出具体
入口、组合或披露差异；每项 metric 最终得到“继续只报不挡 / 候选阈值需产品裁决 / 当前不可判”之一，
证据写入 `docs/DECISIONS.md`。`R-5.25` 工作包只缩小阅读范围，不制造盲评标签。

### TODO-31 · 下一次新 base 同批收紧 Stage4→5 盘上契约

**当前状态**：最小主体的前一批基线已通过默认非 corpus 回归；本轮 WriterContext v10 重构仍须按最终改动集合重新验收，
旧结果不作为本轮通过证明。WriterContext v10 保留章级唯一剧情目录、opaque 人物呈现与逐场状态许可，不重复保存同号来源、
源字数和预算副本；API 从当前场景与 Stage1 现场派生。
逐章人物名单被写手、Stage6/7 实际读取，因此保留。Chapter v3、Review v3、Rejected v2、
TrimAudit/TrimResolution v3 均只保存承重主体、必要历史基线或精确绑定证据。
这些 shape break 与 TODO-49 / TODO-51 同批验收；不原地迁移或补签旧业务产物。新根使用当前
WorkflowManifestV7；V6 及更早旧根不再迁移，只配对冻结旧代码读取，已登记的 V4→V5、V5→V6
离线过渡按冻结来源代码显式执行。

**验收**：相关 shape break 均登记 current version 与 `_SHAPE_BREAKS`；不迁移、不手补旧产物；
Stage5 写作、recheck/revalidate、Review、trim、s5 审批及 Stage6/7 只读最小主体；删字段前后 prompt、
机械 findings、正文拼接和审批语义不变。关系若没有真实 WriterContext consumer，不复制到展示字段。

## P2 · 架构债

### TODO-52 · BACKEND_CONTRACT 的机器可读契约源

`docs/BACKEND_CONTRACT.md` 被 `contracts/requirements.py` 在运行时解析（文档缺失即
raise；R/Q/B 三列表格与「谁在查」记号就是解析接口）。这意味着文档排版语法是运行时
依赖：一次看似无害的表格改写可能改变生产行为且无守卫提示。目标形态是机器可读契约源
→ 运行时 → 生成文档（当前方向相反）。迁移涉及全部 R/Q/B 条目与
`tests/test_coverage.py` 双向对账，属高影响面改造，不与功能批次混跑；开工前维持现状，
改动表格语法须先改 §9.1「可执行 Markdown 语法」。

### TODO-53 · 跨 stage 深路径 import grandfather 消化

2026-09 架构迁移冻结了 71 条对 `novel/stages/stageN/<内部模块>` 的深路径 import
（`tests/test_layering.py::_CROSS_STAGE_DEEP_GRANDFATHER`，含 views、orchestrator、
store 惰性、llm、api、stages 互跨；守卫只减不增、双向断言）。消化一条 = 在 stageN
包级导出该符号、消费方改走 `from novel.stages.stageN import X`、从清单删除并在提交
说明给理由；验收条件是清单清零且各 stage 包级公开面有模块头说明。与 TODO-51 的
Stage2 重构不同批，避免同一文件对撞。

## 条目生命周期

1. 开工前补足可复现输入、预期最终状态、成本与受影响守卫。
2. 改动覆盖真实消费者、失败与恢复路径、文档和测试，不以类型或占位入口存在为完成。
3. 验收通过或经证据裁定不再值得推进时，立即从本文件删除；历史只回写 Git 与
   `docs/DECISIONS.md`。
