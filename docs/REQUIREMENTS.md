# 活动待办

本文件是项目唯一的活动待办清单，只保留尚未完成、仍值得推进且有明确验收条件的事项。
已完成能力、撤回方案与实验过程由 Git 和 `docs/DECISIONS.md` 回答；产品规格、
冻结契约与证据基线见 `docs/BACKEND_CONTRACT.md`，可迁移经验见 `docs/LESSONS.md`。

**编号不重排。** 已删除的编号保持空缺，避免历史提交中的引用改指另一件事。

⚠ 默认 pytest 刻意排除 corpus。只有 TODO-21 完成，并按 `tests/README.md` 使用
`--require-corpus` 对精确匹配的无正文 manifest 与盘面完成验收后，才可以称“全量语料绿”；
此前的通过结果只能称“非 corpus 回归”。

## P0 · 成本与耗时优化实施

### TODO-49 · 小说流水线 token 与耗时优化

实施依据为 `PLAN.md` 与 `docs/PIPELINE_COST_LATENCY_PLAN.md`，工程基线 `24e4cf4`。首轮和返工分别验收，分批开发、一次切换；旧根整根归档，不迁移旧产物。活动状态仅在本条维护。

- [ ] P0：对齐目标与当前能力，补齐 Stage4/5 E/C、内容轮/网络尝试、采用、恢复和墙钟观测，冻结质量样例与配对标准。
- [ ] P1：先闭合结构审批的 Blueprint 依赖，再贯通 A/B 精确局部、多来源及全采用、免费整书提交和恢复。
- [ ] P2：义务决定继承、待审材料压缩、无待审免费收口；移除非 Stage2 指令中的 Global 编辑规则。
- [ ] P3：人物注册、必要知识、arc 删除、源文案范围、三 owner 派修、独立知识—事实关联及披露消费。
- [ ] P4：Blueprint 与材料 fact 归属、对白语义化、Stage5 整章边界内的紧凑首写/修订/Reviewer rewrite。
- [ ] 独立恢复：同单元人工追加、停止证明、幂等/CAS、原完整 profiles 续跑，不作为所有性能批次前置。
- [ ] P5：默认四并发、逐节点 6/8 对照与总并发限制、真实链路、current-schema corpus、归档切换。

已实现、尚待真实语料与发布验收的工程增量：

流水线实现的默认非 corpus 回归共 6431 项，6429 通过、2 项因缺 Stage2 审阅语料跳过，
无失败或错误，进程退出 0（`retirement-final-noncorpus-v3.xml`）；不代表 current-schema corpus 已验收。
指标 v3 与审阅指令已对齐：Stage2 总耗时和用量纳入人物节点及两位 Commander，
旧五轮验收工具仅保留历史报告读取，现役入口统一九组配对；Commander 的三 owner 派修、
实际依赖决定继承和人工累计额度同步到运行时工作包与操作 skill，删除提前生成 Blueprint 的旧指引。
该增量的契约五文件 331 项及指标、实际工作包、额度、API/skill 定向验证 273 项均已通过。
默认回归后仅追加统计工具对冻结基线 v1 工作单元账的只读解析，保留未知与生产拒绝边界；
该补丁与契约五文件、指标配对及工作单元守卫共 425 项定向测试通过
（`retirement-metrics-history-targeted.xml`），不混称已包含在前述默认回归中。
这些工程结果不覆盖或替代各条明确保留的真书、corpus、付费实验与切换缺口。

- 旧 frozen-v6 产物读取、Stage4 审阅/编辑与 Stage7 组装/下载兼容入口已按用户明确授权退役；当前产物版本不匹配保持拒绝。旧 Stage2 预审、额外终审、独立额度账的写入/退款/归档重开入口亦已移除，Blueprint Commander 的进度分母改为自身义务并删除重复 Global 工作包读取。旧书内运行目录的搬运与回退、Approval v2～v6 的自动迁移、Stage3 的现役消费投影和旧整书 B checkpoint 恢复入口亦已退役；当前 service 根的有效回答与提交尾窗继续免费恢复。退役入口在 API/CLI/重启读取上的定向拒绝测试已通过，恢复守卫已纳入默认回归，正式旧根尚未归档。

- P0 已有实际处理/待执行/历史采用/机械处理/缓存、当前审阅待办与决定继承的后端工作单元投影；异常保持 unknown，累计额度独立呈现。真实 HTTP 重启读取及运行页续跑操作已过定向测试。已有阶段与节点 E/C、历史采用及恢复读数，以及绑定报告、冻结配置和人工质量收据的九组配对工具；具体口径见 `docs/PERFORMANCE_EXPERIMENTS.md`。真实三书质量样例、配置与收据尚未冻结，尚无性能结论。
- P1/P2 已接入 Blueprint 审批依赖、A/B 逐章采用与聚合、A 原回答和 prepared 恢复、精确审阅义务继承与免费收口。同一存活父任务内，已关闭并排空的来源只准当前完整上下文和活动 claim 的后继免费补尾；回答与 prepared 两类中断和拒绝反例已过定向验证，扩大后的契约与 Stage4 恢复测试通过。未聚合运行的局部依赖变化、来源损坏与后继恢复已补定向反例；真书链路仍待验收。
- P3 已删除 arc，Global 正式读取源文案、持久化证据支持或未知状态，并通过 typed 编辑及审阅确定开篇范围；Stage6 不再缺省第 1 章。独立人物节点已接入载体、两种 executor backend、工作单元、CLI、checkpoint/prepared 与免费编辑/重验证；Global 已切到只存必要知识的 v6，输入绑定静态人物实际引用；静态写口从 GlobalEdit v5 移除，称名投影由人物发布。七个下游读取入口、知识免费编辑及保存回答/checkpoint/正式提交中断恢复已过定向验证。三 owner 的 typed 决策与派修选项已接入，两位 Commander 不变；人物 API/UI、知识关联与场景披露已接入，现役 fixture 已随契约调整；真书消费链仍待闭合。
- P3 的知识关联已通过 Blueprint Commander typed 决定先保存、免费 producer 再物化；场景级披露、人物/群体知情、世界规则及关系时点已进入 WriterContext v7 和前端。人物订正后的免费引用重绑已闭合来源校验、状态 unknown、旧知识归档及变化项复审。上传→源文审批/分章→人物与知识 producer→人物 HTTP 订正→免费重绑→Blueprint Commander 决定→关联落盘中断→真实 CLI 补尾→重启读取的 88 项消费链定向检查已通过（`stage2-closure-projection-consumers.xml`）；包含落盘前后中断和代码变化拒绝盖章。三 owner 派修的实际 HTTP operator/preview、producer、完成回执和重启读取也已全部通过（`stage2-real-owner-rework-fixed.xml`，3 项），Blueprint 只请求被派修章。测试仅替换模型传输，合成决定不作为质量结论；独立 corpus 仍待闭合。
- P5 整根离线归档已新增专用 preview/apply、旧代码停止证明、整树清单 CAS、根外 prepared/committed 和新旧根消费围栏。合成盘面验证了移动与提交中断、真实进程退出后的同一事务补尾、未知记录及停止裁决保留、迟到事件反证、目录不可读拒绝和 API/CLI 围栏。契约五文件与该批路由、归档及消费检查共 465 项通过（`retirement-final-contract-consumers.xml`）。尚未执行真实旧根归档或新根生产，逐书 trash 结果不替代整根发布验收。
- P3 已退役旧 Global role/fate 窄修 schema、编译与写审计入口；其累计轮次、回答托管和中断恢复保障由现役人物/知识完整候选测试接续。人物 INVALID 的失败定位已贯通 HTTP、真实免费 CLI 和重启读取；未知人物 ID 不再归一成 public。普通 Global 复验保留裁剪失败依据，显式归一路由不变；缺 typed 失败回执的 reviewed INVALID 在 workpack、恢复身份和只读调度器中均保持未知，不再推断付费纠正授权。退役增量的定向验证已通过，真实质量与性能验收仍待完成。
- P4 的 WriterContext v7、Chapter v3、Review v3、Rejected v2、TrimAudit/TrimResolution v3 已接入最小主体与运行时读数。checkpoint 保存完整候选 segments；Review 全书篇幅分母仍取已写章的真实源长度；trim Reviewer 正文只下发一次，精确证据校验上下文保留在后端。恢复、审批、HTTP 与重启读取已过定向测试，独立 corpus 验收仍待完成。
- P4 已新增 Blueprint v8 材料事实归属、普通对白语义任务与剧情字面例外，B 请求/Plan 守卫共用事实候选；Context 对材料引用执行披露检查并保留对白人物依赖。首写、修订、Reviewer rewrite 共用紧凑材料，普通对白不再走逐字返修。定向消费链已通过，真实性能与质量验收仍待完成。
- 退役收尾已删除 Stage2 无调用者的旧 Global/role-fate 提示，人物节点正式提示改为引用唯一称名枚举。Stage5 将上一章实际下发的目标、末场转折和章末状态纳入同一文字表，未下发证据不进入材料；角色引用、字面例外与原任务书不变。根切换标记或计划版本未知时，即使摘要相互匹配，CLI/服务也拒绝消费。契约五文件及上述写作、人物、拒稿、归档恢复消费链共 619 项定向测试通过（`retirement-final-v2-targeted-fixed.xml`），现役消费链已纳入默认回归；旧版拒稿 fixture 原字节保留为拒绝反例，当前版由现役候选载体单独合成，不作为真实质量证据。
- 人工追加额度及原完整 profiles 续跑已接入锁内复验、API 与界面；真实运行到重启读取的最终消费链仍待验收。
- Stage2 逐义务回执已接入新决定/历史接受的完整覆盖、删除问题复审、来源损坏 unknown、模型待审材料投影、API/UI 与两个节点的免费收口。定向测试已验证实际 Blueprint 局部编辑、HTTP 重启、CLI 零模型恢复和三 owner 派修；真书验收仍待完成。

性能沿原三书各三对冻结基线/候选。Stage4 token/墙钟目标 −30%/−20%；Stage2 token −20% 且墙钟不恶化；Blueprint token/墙钟各 −15%；Stage5 首轮输入 −20%、阶段 E+C 总 token/墙钟各 −15%、独立返工总 token −20%。失败、人工追加和未知留在分母；未完成/质量失败不计节约；正文篇幅、事实/因果/状态/披露/对白质量不退化。

TODO-45 的旧书恢复和 TODO-47 的五轮三十分钟目标退出本次活动目标；它们仍有价值的恢复、真实消费链与质量反例并入本条。TODO-46/48 的真机成本和恢复验收并入本条，不重复购入旧席位实验。TODO-21/28 的 corpus 与真实 fixture 继续独立闭合，TODO-30 仍为独立研究。真实模型尚未执行，不把工程改动记为性能验收通过。

## P1 · 质量闭环与诊断可信度

### TODO-21 · 建立 current-schema 现役语料基线

**缺口**：corpus 闸、manifest schema 与关闭式验收器已经存在，但仓内仍没有一份由当前
producer 从来源完整生成、且与盘面精确匹配的无正文 manifest。因此现有非 corpus 回归不能证明
真实全链、版本、来源账与 skip 面闭合。

**工作**：

- 按 `.workflow/skills/novel-pipeline-operator/SKILL.md`，在隔离输出与独立空缓存中，对
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

**当前状态**：最小主体与现役消费者已实现，默认非 corpus 回归已通过，尚待独立 corpus 和新根切换。
WriterContext v7 删除章级同号来源、源字数和预算副本，API 从当前场景与 Stage1 现场派生；
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
