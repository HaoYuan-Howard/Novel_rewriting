# backend_v1 —— 新后端真实响应的 fixture（FE-06）

抓取方式：`tests/sandbox.py` 把原型语料按当前契约迁移到临时目录，
起 `create_app` 后逐端点 GET（脚本：会话 scratchpad `capture_fixtures.py`）。

- 书：`我把夫君捧成头牌后，全家悔疯了_7615069877019300633`（与 `src/mocks/` 的样本书同一本，sid=7615069877019300633）
- 抓取日期：2026-08-17
- 章级样本仍取第 3 章（与统一契约 §八的冻结基线对齐）

⚠ `src/mocks/` 那 20 份是**冻结的证据基线**（与 design_handoff 逐字节一致，
check-seams 钉着），本目录才是 mock 现役消费的数据源；两边不许互相覆盖。

| fixture | 端点 |
|---|---|
| `check_stage2.json` | `/api/novels/7615069877019300633/check/stage2` |
| `check_stage3.json` | `/api/novels/7615069877019300633/check/stage3` |
| `check_stage4.json` | `/api/novels/7615069877019300633/check/stage4` |
| `check_stage5.json` | `/api/novels/7615069877019300633/check/stage5` |
| `check_stage6.json` | `/api/novels/7615069877019300633/check/stage6` |
| `check_stage7.json` | `/api/novels/7615069877019300633/check/stage7` |
| `enums.json` | `/api/enums` |
| `global.json` | `/api/novels/7615069877019300633/stage2/global` |
| `inbox.json` | `/api/novels/7615069877019300633/inbox` |
| `reading_ch3.json` | `/api/novels/7615069877019300633/reading/3` |
| `review_ch3.json` | `/api/novels/7615069877019300633/review/3` |
| `stage4_ch3.json` | `/api/novels/7615069877019300633/stage4/3` |
| `stage5_ch3.json` | `/api/novels/7615069877019300633/stage5/3` |
| `stage6.json` | `/api/novels/7615069877019300633/stage6` |
| `stage7.json` | `/api/novels/7615069877019300633/stage7` |
| `stage7_ch3.json` | `/api/novels/7615069877019300633/stage7/3` |
| `style.json` | `/api/novels/7615069877019300633/stage3/style` |
