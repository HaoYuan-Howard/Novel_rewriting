# 第二阶段（前后端打通）验收冒烟记录

日期：2026-08-14
范围：批 7 验收销项——两本全链书 12 屏冒烟、免费端到端、失败路径覆盖。
方式：真实服务（`python -m novel.api`）+ HTTP 全链核对；前端三道门（typecheck / seams / build）。
说明：本阶段无浏览器自动化工具，12 屏以「每屏真实数据缝对应的 API」逐屏核对代替人肉浏览器走屏；界面渲染层的最终目视确认仍需人在浏览器里过一遍。

## 1. 两本全链书 12 屏冒烟（FE-04）

语料：`outputs/e2e_20260813/`（gitignored）取两本——`夫君为爱绝育，但我子孙满堂_7645085972736135960`、`皇帝亲自替我宫斗_7626951433820066622`。
脚本：`tools/smoke_screens.sh`（只发 GET，零写盘），每本书 27 项断言全部通过（SMOKE PASS ×2）：

- 12 屏数据缝：`/novels`、`/novels/:sid`、stage0 report/segments、stage1 segments/chapters/hook_copy、`reading/1`（长文本）、`stage2/global`、`stage3/style`、`stage4/1`、`stage5/1`、`review/1`、`stage6`、`stage7`、`stage7/1`、`inbox`、`approvals` 全部 200。
- 九个 `check/:kind`（stage0–7）全部 200 且 `rules[]` 出线。
- **amber 覆盖**：两本书各 8 个 check 中 5 个 amber（stage0/2/5/6/7），hit/unver 计数非零——校验面板的命中行与「够不着」行均有真实样本。
- **stale 覆盖**：批内对两本书执行了 `stage2.blueprint --migrate`（旧锚点制品迁移到现契约，0 成本），迁移后 `stage4/1` 的 `plan_state`/`ctx_state` 与 `stage5/1` 的 `ctx_state` 如实变 `stale`——新鲜度传导被真实触发并观察到。
- **unknown 覆盖**：旧产物的 freshness 大量为 `unknown`（`no_provenance` / `consumed_undeclared`）；服务重启后 `/api/jobs` 从账本完整恢复历史作业（state ok/failed 如实），语料库与已跑层屏幕不受影响。
- **null / 空数组**：`reading/1` 在蓝图读不回时 `blueprint=false` 且 `blueprint_state.readable=false`（200 不装死）；inbox 计数六键全出（含 0）。

### 冒烟中发现并处理的两件事

1. **语料契约漂移（已修）**：两本书的 `stage2/chapter_blueprints.json` 是旧锚点形态（`starts_with/ends_with` 定位子），当前契约定稿为 `SourceSpan`（`text/start/end/...`），导致 `check/stage2`、`check/stage4` 409 `ArtifactUnreadable`。用契约自带的迁移路 `stage2.blueprint --migrate`（机械重锚，0 成本）把两本书迁移到现契约后全部 200。`reading` 屏在迁移前即正确降级（`blueprint=false`），证明失败路径的界面语义诚实。
2. **全链跑完的书在语料库消失（已修，本批内）**：`StageState` 旧断言「无产物清单 ⇒ done 只能是 None」拒收了 stage5 `done=True`，`/api/novels` 逐本 try 把它静默跳过。修复放宽载体断言（完成度证据在计划+逐章成稿，守在产地 `planned_chapters`），并加回归锚 `tests/test_api_screens.py::test_a_fully_written_book_survives_the_overview`。

## 2. 免费端到端

临时语料 `outputs/smoke_free/`（gitignored），真实服务全程 HTTP：

1. `POST /api/upload?filename=` 上传 `Test_data/前任赘夫逃跑后_2022040243025528636.txt`（32K）→ stage0.remove 作业 ok。
2. `POST /novels/:sid/stage0/approve` → ok；`approvals/gate/s0` 盖章。
3. `POST /api/jobs`（`stage1.partition`）→ ok，分出 19 章；stage1 三屏 200。
4. **付费屏诚实态**：`stage2/global`、`stage4/1`、`review/1`、`stage6`、`stage7` 全部 404 `UpstreamMissing`（界面渲染「这一步还没跑」）；`stage5/1` 200 且 `ctx_exists=false`（不发假材料）；`check/stage2|5|7` 404。所涉节点（stage0.remove/approve、stage1.partition）均 `paid=False`，全程零花费。

失败路径顺带覆盖：误传 percent-encoded sid 启动作业 → HTTP 202 收单、CLI 内失败以 `state='failed'` 出线且日志可读（作业失败不是 HTTP 错误，符合 BE-03 纪律）。

## 3. 付费全链（单列，未做）

两本书上传 → stage2~7 → book.txt 的付费全链按 VAL-01 纪律需用户逐次确认，**本批未执行**；FE 条目销项不以它为前提（计划原文）。`docs/REQUIREMENTS.md` 保留 VAL-01 / VAL-03 待付。

## 4. 测试与门禁

- `pytest -q` 全量：除 `tests/test_stage01.py` 6 项**既有红**外全绿（该红在干净 HEAD 上复现：`Test_data/` 后加的 FQ_ 三本与测试钉死的样本集不符，守卫本身要求人工复核样本变更，不在本批范围）。
- 前端三道门全过：`npm run typecheck`、`npm run seams`（63 文件、mocks 20 份逐字节一致）、`npm run build`。
