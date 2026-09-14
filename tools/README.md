# tools/ 与 scripts/ 职责登记

本目录混居三类脚本，按下方三栏归类；新脚本落位前先选栏。测试引用、文档引用见
`tests/suite_catalog.json` 与 `docs/`。付费工具一律显式 `--yes`，正文语料显式
`NOVEL_DATA` / `NOVEL_CORPUS` / `NOVEL_OUTPUT`。

## 在役运维（可按文档直接执行）

| 工具 | 用途 | 引用 |
|---|---|---|
| `archive_output_root.py` | 离线整根归档（preview/apply） | `docs/OPERATIONS.md`、`tests/test_output_root_archive.py` |
| `backup_snapshot.py` | 可验证代码/私有快照的创建与恢复 | `docs/OPERATIONS.md`、`tests/test_backup_snapshot.py` |
| `verify_agent_e2e.py` | Agent 整书验收核验（注册表与 ledger 现算） | `docs/BACKEND_CONTRACT.md`、`docs/OPERATIONS.md` |
| `observe_provider_limits.py` | 有界付费 provider 429/退避探针 | `docs/BACKEND_CONTRACT.md`、`tests/test_provider_probe.py` |
| `regen_web_enums_fixture.py` | 重生成 `web/src/fixtures/backend_v1/enums.json`（`--check` 对账） | `tests/test_api_enums.py` 对账守卫 |
| `scripts/start.cmd` / `start.ps1` / `restart.cmd` | 双击控制台启动/重启 | `tests/test_api_ui.py` |

## 手工再生成与开发辅助（改动对应契约后手动运行）

| 工具 | 用途 | 引用 |
|---|---|---|
| `scripts/export_plot_types.py` | 生成 `web/src/types/plot.generated.ts`（`--check`） | 生成文件头、`web/src/fixtures/plot_v1/README.md` |
| `scripts/export_backend_types.py` | 生成 `web/src/types/backend.generated.ts`（`--check`） | `tests/test_api_enums.py` 对账守卫 |
| `scripts/export_plot_fixture.py` / `export_stage2_core_fixture.py` | 生成 `web/src/fixtures/plot_v1/` 夹具 | `web/src/fixtures/plot_v1/README.md` |
| `current_corpus_paid_preview.py` | 冻结当前语料付费范围预览（只读） | `tests/test_current_corpus_paid_preview.py` |
| `calibrate_style_corpus.py` | 0 成本 Stage5 风格基线复算 | `tests/test_style_calibration.py` |
| `book_overlap.py` | 成书与源文逐章 20 字重叠读数 | `tests/test_book_overlap.py` |
| `derive_schema.py` | 从盘上产物反推观察 schema（观察 ≠ 契约） | `docs/schema/_README.md` |
| `probe_nonbody_word.py` | NONBODY_KINDS 增词前的门槛探针 | `docs/BACKEND_CONTRACT.md` |
| `validate_style_transfer.py` | 离线不含正文的风格迁移校验 | `docs/schema/style_transfer_validation.md` |

## 冻结实验与一次性（只读历史，不接新任务）

| 工具 | 状态 |
|---|---|
| `stage6_style_ab.py`（70KB）/ `stage6_style_ab_review.py`（77KB） | 2026-08-24 冻结的 Stage6 vs Stage3 定性风格 A/B 复现；stage3 旧 prompt 封存于此，不回生产。裁决见 `docs/DECISIONS.md` 2026-08-24 |
| `archive_stage3_v1_books.py` | 已完成的 Stage3 v1 两书归档一次性工具（preview 默认，`--execute` 门控） |
| `effort_ab.py` | FC-13 逐节点 effort 实验一次性 runner |
| `node_role_table.py` | 一次性节点角色表生成 |
