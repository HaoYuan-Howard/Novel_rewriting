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
Pydantic 响应模型直接校验。Stage2 v5、Stage4 v2、Stage5 与 Stage7 的形状变化已经让部分 fixture
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

### TODO-30 · 标定新场景重构读数的可解释边界

**缺口**：Stage4 v2 已把旧源序写作骨架替换为 fact atom → scene 编译，并常驻报告原相邻事实
保留率、非相邻事实重组、场景边界变化、入口重建、事实再分布与语义闭合。闭合已有 HARD；其余
读数只说明“改了多少”，没有 ground truth，不能据此判断“改得好不好”或发明阈值。

**工作**：

- 与 TODO-21 共用同一批两书 current-schema 产物，保存 Stage2 v5、Stage4 v2 与 Stage5 canonical
  全书路径的逐章读数，不另付一批只为重复生成相同产物。
- 对版本信息做遮蔽后人工盲读，至少区分：顺叙本来合理、入口确有重建、只改措辞、为追指标强行
  重排；标签必须绑定具体章节与证据，不能只给全书印象分。
- 逐项判断读数能否稳定区分上述标签。没有稳定分界、或会误伤合法顺叙反例的维度继续保持 metric；
  只有出现可复算分界时，才另起产品语义变更讨论阈值。

**验收**：产物、逐章读数与盲读标签可复算；每项 metric 都得到“继续只报不挡 / 候选阈值需产品
裁决 / 当前不可判”之一的明确结论，并记入 `docs/BACKEND_CONTRACT.md` §十。若最终改变 R 条目的
判定语义，须同批更新冻结契约、反例与真实消费路径，不能在本条里静默改线。

## 条目生命周期

1. 开工前补足可复现输入、预期最终状态、成本与受影响守卫。
2. 改动覆盖真实消费者、失败与恢复路径、文档和测试，不以类型或占位入口存在为完成。
3. 验收通过或经证据裁定不再值得推进时，立即从本文件删除；历史只回写 Git 与
   `docs/BACKEND_CONTRACT.md` §十。
