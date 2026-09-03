# 活动待办

本文件是项目唯一的活动待办清单，只保留尚未完成、仍值得推进且有明确验收条件的事项。
已完成能力、撤回方案与实验过程由 Git 和 `docs/DECISIONS.md` 回答；产品规格、
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

**缺口**：Stage5 已从当前 Stage1/Stage4 任务书现场派生来源章号、源字数和章预算，但 Chapter、
Review 与 trim 仍保留可由正文、findings 或绑定主体重算的展示副本。旧书为保留付费正文和人工决定
不原地迁移；shape break 与 TODO-45 的 clean v7 base 同批切换。

**工作**：只在 TODO-45 的新 clean base 上由 producer 完整生成：WriterContext v5 删除重复人物/章级
来源字段；Chapter v3 删除可重算读数；Review v3 收成当前主体、findings 与必要基线；Rejected v2
改存 segments；TrimAudit/TrimResolution v3 删除可由正文、路径或对方主体确定的重复字段。

**验收**：五次 shape break 均登记 current version 与 `_SHAPE_BREAKS`；不迁移、不手补旧产物；
Stage5 写作、recheck/revalidate、Review、trim、s5 审批及 Stage6/7 只读最小主体；删字段前后 prompt、
机械 findings、正文拼接和审批语义不变。关系若没有真实 WriterContext consumer，不复制到展示字段。

### TODO-45 · 稳定工作流的两本书恢复、clean v7 与现实运行验收

TODO-32 的剩余真实 agent-only 验收已并入本条，旧编号删除。已经落地的 RunSupervisor、四类身份、
raw/effective 审批、opaque token、Stage2 v7 关系和 Stage4 v3 repair/edit/replan 不再作为活动叙述重复；
冻结行为见 `R-3.16`、`R-3.21`、`R-5.26`、`R-11.19`～`R-11.21`、`R-12.7`、`R-13.8`、
`R-14.7`，工程证据见 `docs/DECISIONS.md`。本条只追踪尚未完成的实书恢复、运行验收与
目录生命周期。

**批次一：第一本书零模型最小恢复。** 对 SID `2067848088279168820` 在 current code 下重建
validation seal。只要 review subject/contract 未变，就从盘上 raw typed decisions 恢复 effective 投影，
不重新签字、不改成书字节；刷新一次性终审包并达到真实 delivery 终态。若内容或可见 finding 变化，
必须诚实停在 review_required，不能为满足验收自动代批。不得重建 Stage2 v7 或重新生成正文。
冻结 v6 的旧 A 决定若因 review contract 升版过期，仍不得复活；但现存 EvidenceBinding、Plan、
WriterContext 已由 frozen exact proof 逐字对证且当前 s4/structure 已接受时，A 的一次性 B 生产授权不再
反向阻断这些既有下游的消费。任一 proof、Plan/Context 或最终结构决定缺失仍关闭式停止。

**批次二：第二本书 Stage4 定向恢复。** 对 SID `2062246499690591305` 用 ch4/ch6/ch8 已知的错指、
纠正和动作—台词关系作回归。旧书保持 Stage2 v6，不自动迁移 v7；修复轮必须携本轮 finding 与全部
未关闭历史 typed constraints。单章两轮耗尽后只能开同策略 repair epoch 或走完整章 preview/apply；
只有显式全局策略驳回才 replan。验收只改受影响章，Stage0～Stage4A 其余有效单元不重跑，未变
checkpoint 的重复 wire 为 0。若 frozen-v6 当前候选经真实复核为全章 accept，raw typed 决定必须
如实落账而 effective 投影保持 `suspended`：不伪造 `request_repair` 换 edit 票，不重复唤 Commander，
不发布 Structure approval、`s4rr1`、`s4er1`、付费 repair/replan/B 或 execution token；需要新 B 时只在
clean v7 base 继续。只有真实 `request_repair` 才保留该章零 wire 完整章 edit。current Stage4B
`request_replan` 必须先进入不同的 Stage4A resolution blocker，由 Commander 结合 B 证据形成新的 typed
A 修订决定；不得重审同一 B。决定落盘后只由确定性 Supervisor 交 exact action 给 Executor，不再重复
调用 Commander；运行页须把该真实前沿和下一处理角色直接展示，而不是用空 beacon 营造从 Stage0 重跑。

**批次三：一次 clean v7 agent-only 全链。** 新建 clean base，用冻结提交、固定 profile、冷缓存和
完整 lifecycle/admission/beacon/ledger/session/unit 记录，从 producer 跑 Stage0～Stage7。legacy
`operator_phases` 若缺失只报 unknown，不作为验收 blocker。Stage2 v7 的 review 关系只进入 Commander
workpack，只有 typed 确认的 hard 关系以 opaque alias 进入 Stage4A；`same_scene` 不因同 source beat 自动
生成，Executor 不见 SourceSpan/source beat/完整源顺序。正常运行只有确定性 RunSupervisor 调度
Commander/Executor，外层 root 介入为 0；同一 parent run 必须跨 pause/resume 和至少一次 execution
epoch rollover 完成：同 epoch 精确续 provider session，新 epoch 新建 session。`kind=legacy` backend 显式禁用且
不作 fallback；付费与正文外发在首 wire 前分别授权。该 run 同时承担 TODO-32 的剩余真机验收；
`default_profiles` 在此验收前保持 null。

**批次四：现实生命周期验收。** `_service/runs`、scratch、两相 retention、v3 trash、
round/revalidation 精确去重、restore hard-kill reconcile、维护 API/UI 与 AgentRun `reconciled` 的工程面
已经完成，证据移入 `docs/DECISIONS.md`，不再作为活动施工项。当前只剩在现实运行中演练
TTL preview/apply、部分删除与进程崩溃补尾、trash restore，以及一个 parent run 跨 pause/resume 与多个
epoch 交付；服务崩溃、坏尾、迟到 child 与并发 resume 必须继续关闭式处理。

**验证纪律**：

- 小修只跑直接受影响单测、对应守卫和至少一个反例；不为每个小修重复全仓。
- 契约变更跑 `docs/BACKEND_CONTRACT.md` §9.5 五文件及受影响 stage/API/恢复测试；前端受影响才跑
  seams、typecheck 和相关测试。
- 每个连贯批次合并前只跑一次完整非 corpus 后端；前端跑 test/build/seams/typecheck；skill 变化跑
  skill validator。Release 才运行 current-schema corpus manifest、`--require-corpus`、固定 profile/cache、
  单一 clean execution identity 和一次 clean agent-only Stage7。
- 必测：代码变化只滚 epoch/validation seal；raw 下游决定不被前置失效擦除；内容/关系/可见 finding
  只使受影响范围过期；每节点最多一次完整 operator snapshot 和一次 execute CAS；pause/stop API 只以
  202 异步受理，后台 drain 后才分别落 paused/ended，stop 永久；第二轮 repair 继承第一轮未关闭约束；人工完整章替换零 wire且非目标章字节恒等；真策略
  replan 有累计反馈并只重跑 effective input 变化章；Stage4B 首个用量窗口失败停止新章 admission、同 run
  只补 pending，ended 历史 run 只经严格且唯一的只读 checkpoint adoption 接续；INFO/metric/UNVER
  不产生付费动作；Stage4B `request_replan` 不重审同一 B，新的 typed A 决定只触发一次 exact Executor
  recovery，运行页的跳过前缀/续跑点/下一角色必须全部来自 operator 同一快照。

**性能验收（相同模型、effort、并发与冷缓存）**：

- 第二本 15 章 Stage4 付费调用从 85 降到不超过 25，墙钟 40～60 分钟；非模型控制面累计少于
  10 分钟，控制/审阅不再占总墙钟 80%。
- Stage4 整书 replan 为 0，除非显式全局策略驳回；已成功未变单元重复 wire 为 0。
- 首次逻辑单元落盘率 ≥95%，已授权公开节点首次正式落盘率 100%，基础设施失败率 ≤1%，正常运行
  外层 root 介入为 0。
- 现有两本多代码身份 run 只作问题回归，不作 clean 性能基线。累计至少 5 次 clean run 后，才把
  ≤20 章 P50 120 分钟、P95 180 分钟升级为发布硬门。

## 条目生命周期

1. 开工前补足可复现输入、预期最终状态、成本与受影响守卫。
2. 改动覆盖真实消费者、失败与恢复路径、文档和测试，不以类型或占位入口存在为完成。
3. 验收通过或经证据裁定不再值得推进时，立即从本文件删除；历史只回写 Git 与
   `docs/DECISIONS.md`。
