# tools/ 与 scripts/ 职责登记

本目录混居三类脚本，按下方三栏归类；新脚本落位前先选栏。测试引用、文档引用见
`tests/suite_catalog.json` 与 `docs/`。付费工具一律显式 `--yes`，正文语料显式
`NOVEL_DATA` / `NOVEL_CORPUS` / `NOVEL_OUTPUT`。

## 在役运维（可按文档直接执行）

| 工具 | 用途 | 引用 |
|---|---|---|
| `archive_output_root.py` | 离线整根归档（preview/apply） | `docs/OPERATIONS.md`、`tests/test_output_root_archive.py` |
| `backup_snapshot.py` | 可验证代码/私有快照的创建与恢复 | `docs/OPERATIONS.md`、`tests/test_backup_snapshot.py` |
| `observe_provider_limits.py` | 有界付费 provider 429/退避探针 | `docs/BACKEND_CONTRACT.md`、`tests/test_provider_probe.py` |
| `regen_web_enums_fixture.py` | 重生成 `web/src/fixtures/backend_v1/enums.json`（`--check` 对账） | `tests/test_api_enums.py` 对账守卫 |
| `scripts/start.cmd` / `start.ps1` / `restart.cmd` | 双击控制台启动/重启 | `tests/test_api_ui.py` |

## 手工再生成与开发辅助（改动对应契约后手动运行）

| 工具 | 用途 | 引用 |
|---|---|---|
| `scripts/export_backend_types.py` | 生成 `web/src/types/backend.generated.ts`（`--check`） | `tests/test_api_enums.py` 对账守卫 |
| `calibrate_style_corpus.py` | 0 成本新链 Stage5 正文风格基线复算（`stage5/body/chNN.txt` vs 源文） | `tests/test_style_calibration.py` |
| `derive_schema.py` | 从盘上产物反推观察 schema（新链坐标；观察 ≠ 契约） | `docs/schema/_README.md` |
| `probe_nonbody_word.py` | NONBODY_KINDS 增词前的门槛探针 | `docs/BACKEND_CONTRACT.md` |
| `ab_compare.py` | A/B 两位匿名评审对照评分 runner | `tests/test_ab_compare.py` |
| `story_metrics.py` | 分列首响应直接可用、首个完整候选接受、材料响应、窄修与实际成本读数 | `tests/test_story_metrics.py` |
| `story_facts.py` | 冻结源证据、隔离开发/保留样例，人工标注并比较实际 SourceStory；零付费 | `tests/test_story_facts.py`、执行规格 §5.3 |
| `repair_rounds.py` | 修订轮回派账/收敛读数 | `tests/test_repair_rounds.py`、`docs/OPERATIONS.md` |

### 离线事实标注与比较

`story_facts.py` 是开发评测工具，不进入生产节点、judge 或自动接受链。
准备材料只需正式 Stage0/1，可以在抽取前独立标注；比较读取实际封存的
`SourceStory`，不读取 prompt 或 reasoning 代替正式结果，不调用模型。

显式设置 `NOVEL_DATA` 或 `NOVEL_CORPUS` 指向原始源书，`NOVEL_OUTPUT` 指向
当前正式产物根。两个源变量同时设置必须一致。评测记录经 `--dataset-dir`
指定到第三个独立目录，不能包含或位于源根、正式输出根内。源文件由
`--source-relative` 在源根内定位，并与 Stage0 原始字节 SHA-256 核对。
以下命令在仓根运行，使用 `novel_refactor` 环境。

```powershell
python -m tools.story_facts --dataset-dir E:/novel_evaluation/facts prepare --sample baby_dev --split dev --sid 123 --source-relative 样书_123.txt --evidence e001_00001 e001_00003 e002_00001
python -m tools.story_facts --dataset-dir E:/novel_evaluation/facts compare --sample baby_dev
```

`prepare` 冻结 Stage1 源身份、内容摘要、段号、章/段序和原文，生成
`dev/baby_dev/source.json` 与空的 `annotation.json`。只引用显式给出的段，
需要先通读带地址的全书证据时，用 `--all-evidence` 明确冻结全部段，不能与 `--evidence` 混用。
没有自动从待测候选制造金标。`dataset.json` 只存分集元数据和摘要，不复制原文；
同一书的 SID、原文或内容身份不能跨 `dev`/`heldout`，改样例名或改证据范围也不行。
重复准备不覆盖人工标注。比较某一开发样例不会加载其他保留样例的证据文件。

人工编辑 `annotation.json`：全部九个维度保留 `coverage`，分别为实体绑定、事件、
关键事实、因果、认识、读者披露、时间、线程和具体状态。未标注保持 `pending`；
认为不适用须写 `not_applicable`、原文段号和理由。每条 `expectations` 写明
`category`、`statement`、`presence: present|absent`、`evidence_refs` 和 `checks`。
自然语言预期由人判断；可选的 `checks` 只比较人工对齐条目的具体字段，例如：

```json
{"path":["world_status"],"op":"equals","value":"refuted"}
```

`contains`/`lacks` 用于数组；字典值按所列字段逐项匹配，可检查同一实体含有
“婴儿”和晚揭真名两种表面形式。它们是结构检查，不会判断两个自然语言陈述是否同义。

`compare` 在 `comparison.json` 中列出证据范围内的实际候选路径、原始字段、
未评价项和分母，另建绑定候选/标注摘要的 `review_<摘要>.json`。人工在 review
中对齐实际路径，逐项填 `supported/missing/incorrect/unver`、评审者及依据；
无依据新增、主体错误、错误强断言记入 `extras`，也须候选路径与源段。然后免费复算：

```powershell
python -m tools.story_facts --dataset-dir E:/novel_evaluation/facts compare --sample baby_dev --review E:/novel_evaluation/facts/dev/baby_dev/review_实际摘要.json
```

源字节、证据、标注或候选变化后，旧 review 不再有效。报告分别保留结构差异与
人工语义结论；结构相等不会生成语义通过。人工标注召回只覆盖该 scope 的正向金标，
未评价或没有分母时为 `null`；未对齐候选不自动计为无依据新增。它不证明整书召回、
首答可用率或文学质量，需与 `story_metrics.py` 的过程读数及 golden 的成书评价分开报告。
候选完全漏掉某冻结段时，该段仍留在金标分母；不会要求候选补造证据表才能比较，
也不会自动把无候选判成缺失，须人工填 `missing` 后才计入召回结果。

### 已退役的剧情链导出工具（2026-09-26 清理删除）

scripts/export_plot_types.py、export_plot_fixture.py 与 export_stage2_core_fixture.py
（已删路径按 DECISIONS 纪律纯文本书写，不假装现役落点）
连同其产物（剧情链 TypeScript 类型与 `plot_v1` 夹具）已随旧 Stage2/4/5/7 链整层退役
（STR-06 切换 F8c）从盘上删除，恢复来源为 Git 历史。新链的只读监控夹具是
`web/src/fixtures/backend_v1/str06_candidate.json`（合成候选根，fake transport 产物）。

## 冻结实验与一次性（只读历史，不接新任务）

| 工具 | 状态 |
|---|---|
| `effort_ab.py` | FC-13 逐节点 effort 实验一次性 runner |

## F8c 清理记录

以下工具随 STR-06 旧链退役删除（S-05；旧 path 退役后不得继续扩展）：
`stage6_style_ab.py` / `stage6_style_ab_review.py`（Stage6 整层已退役，2026-08-24
冻结实验的复现面随之关闭）、`characters_v3_m0_pilot.py`（旧 Stage2 characters
v3 生产链已退役）。另删 `verify_agent_e2e.py` 登记行——该工具已不在盘上。
stage2_context_probe.py（批 0 Stage2 上下文/架构探针）随质量改进批次二 ·
批 3 取消而删除：原登记的删除条件是「批 3 的 ch03–ch05 单臂重复采样完成」，批 3
取消后该采样不再发生，工具及其契约测试同批删除；探针产物与证据留在
outputs/str06_stage2_probe_20260926/（该只读根后经运维清理已不在当前盘面；裁决见
`docs/DECISIONS.md` 批 0 一行，证据缺失按「未查/不可用」报告，不补猜）。
