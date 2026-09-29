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
| `book_overlap.py` | 成书与源文逐章 20 字重叠读数（回映射走新链 compile 替换表） | `tests/test_book_overlap.py` |
| `derive_schema.py` | 从盘上产物反推观察 schema（新链坐标；观察 ≠ 契约） | `docs/schema/_README.md` |
| `probe_nonbody_word.py` | NONBODY_KINDS 增词前的门槛探针 | `docs/BACKEND_CONTRACT.md` |
| `canary_chapters.py` | B6-A canary 选章（只读 sealed WriterBookIR；逐章计分＋三章＋下一条命令） | `tests/test_canary_chapters.py`、`docs/schema/str06.md` |

### 已退役的剧情链导出工具（2026-09-26 清理删除）

scripts/export_plot_types.py、export_plot_fixture.py 与 export_stage2_core_fixture.py
（已删路径按 DECISIONS 纪律纯文本书写，不假装现役落点）
连同其产物（剧情链 TypeScript 类型与 `plot_v1` 夹具）已随旧 Stage2/4/5/7 链整层退役
（STR-06 切换 F8c）从盘上删除，恢复来源为 Git 历史。新链的只读监控夹具是
`web/src/fixtures/backend_v1/str06_candidate.json`（合成候选根，fake transport 产物）。

## 冻结实验与一次性（只读历史，不接新任务）

| 工具 | 状态 |
|---|---|
| `archive_stage3_v1_books.py` | 已完成的 Stage3 v1 两书归档一次性工具（preview 默认，`--execute` 门控） |
| `effort_ab.py` | FC-13 逐节点 effort 实验一次性 runner |
| `node_role_table.py` | 一次性节点角色表生成 |

## F8c 清理记录

以下工具随 STR-06 旧链退役删除（S-05；旧 path 退役后不得继续扩展）：
`stage6_style_ab.py` / `stage6_style_ab_review.py`（Stage6 整层已退役，2026-08-24
冻结实验的复现面随之关闭）、`characters_v3_m0_pilot.py`（旧 Stage2 characters
v3 生产链已退役）。另删 `verify_agent_e2e.py` 登记行——该工具已不在盘上。
stage2_context_probe.py（批 0 Stage2 上下文/架构探针）随质量改进批次二 ·
批 3 取消而删除：原登记的删除条件是「批 3 的 ch03–ch05 单臂重复采样完成」，批 3
取消后该采样不再发生，工具及其契约测试同批删除；探针产物与证据留在
outputs/str06_stage2_probe_20260926/ 只读保留，裁决见 `docs/DECISIONS.md` 批 0 一行。
