# 后端真实响应样例（现役数据源）

本目录是**新后端真实响应的 fixture**（FE-06 的「有版本含义的目录」）：形状与
`/api/**` 现行契约一致，由 `web/src/api/mock.ts` 独家接入，屏幕与组件不直接读 JSON。
`../mocks/` 那 7 份是更早的冻结字节证据，只服务目录/概览/Stage0 解码入口。

STR-06 切换（F8c）后旧链读屏抓包全部退役：`reading_ch3.json`、`global.json`、
`stage5_ch3.json`、`review_ch3.json`、`inbox.json`、`stage7.json`、
`stage7_ch3.json` 与 `check_stage{2,4,5,7}.json` 随旧 Stage2/4/5/7 链整层删除，
恢复来源为 Git 历史；新链的只读监控面由 `str06_candidate.json` 承担。

## 现役样例

- `enums.json`：由 `tools/regen_web_enums_fixture.py` 调用当前
  `novel/api/enums.py::payload()` 生成；`tests/test_api_enums.py` 核对完整对象。
  含 `retired_nodes`／`retired_stages`，不因旧节点入册就恢复任何旧链端点。
- `stage1.json`：`GET /api/novels/:sid/stage1` 的 `Stage1View` 响应
  （连续分区、原文章号诊断与 `anomaly_count`）。
- `agent_capabilities.json`：离线驱动能力示例，不证明真实账户或模型可用。
- `str06_candidate.json`：录制的合成候选根全链响应（fake transport 产物，非真实书
  正文）；`/api/str06/candidates/{candidate}/*` 十二条只读端点与真后端同形状。

## 纪律

所有模拟数据只经 `web/src/api/mock.ts` 接入；mock 与真实模式走同一条代码路径，
不得用旧事实检查或旧审批模拟新链通过。历史样例的 provenance 只记录生产时的配置，
前端不拿它计算当前预算；费用、用量与恢复结果始终消费当前服务账。
形状变化时先改生产与契约，再重新导出；不手工补造审批、身份或来源。
