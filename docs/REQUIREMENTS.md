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
`retain/reconstruct`，`reconstruct` 章的声明与代码复算变化可逐项对证；整书结构向量、逐章指标、
A/B 原始候选、盲评标签与结构批准均可从盘上 current-schema 主体复算。人工抽样能指出入口、事实
组合或披露顺序的具体差异，而非只说句子不同。每项 metric 最终得到“继续只报不挡 / 候选阈值需
产品裁决 / 当前不可判”之一的结论并记入 `docs/BACKEND_CONTRACT.md` §十；在完成两书付费实证前，
任何测试通过都只能叫非 corpus 回归，不能删除本条。

### TODO-31 · 下一次新 base 同批收紧 Stage4→5 盘上契约

**缺口**：Stage5 已改为从当前 Stage1 / Stage4 任务书现场派生来源章号、源字数与章预算，
Chapter/Review/trim 中也存在可由正文、findings 或绑定主体重算的展示副本。为保留现有付费正文与
人工 trim 决定，本批只完成运行时单一落点和一致性断言，盘上破坏形状延期；不能把延期误解成这些
字段仍有独立语义。

**工作**：只在下一次创建并从 producer 完整生成的新 base 上，同批切换：

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

## 条目生命周期

1. 开工前补足可复现输入、预期最终状态、成本与受影响守卫。
2. 改动覆盖真实消费者、失败与恢复路径、文档和测试，不以类型或占位入口存在为完成。
3. 验收通过或经证据裁定不再值得推进时，立即从本文件删除；历史只回写 Git 与
   `docs/BACKEND_CONTRACT.md` §十。
