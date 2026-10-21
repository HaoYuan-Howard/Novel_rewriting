# 后端真实响应样例（现役数据源）

本目录是**当前后端接口响应的 fixture**（FE-06 的「有版本含义的目录」）：形状与
`/api/**` 现行契约一致，由 `web/src/api/mock.ts` 独家接入，屏幕与组件不直接读 JSON。
`../mocks/` 那 7 份是更早的冻结字节证据，只服务目录/概览/Stage0 解码入口。

STR-06 切换（F8c）后旧链读屏抓包全部退役：`reading_ch3.json`、`global.json`、
`stage5_ch3.json`、`review_ch3.json`、`inbox.json`、`stage7.json`、
`stage7_ch3.json` 与 `check_stage{2,4,5,7}.json` 随旧 Stage2/4/5/7 链整层删除，
恢复来源为 Git 历史；新链的只读监控面由 `str06_candidate.json` 承担。

## 现役样例

- `enums.json`：由 `tools/regen_web_enums_fixture.py` 调用当前
  `novel/api/enums.py::payload()` 生成；`tests/test_api_enums.py` 核对完整对象。
  含 `retired_stages`（`retired_nodes` 名册已随 STR-06 关闭批的存量解码面删除）。
- `stage1.json`：`GET /api/novels/:sid/stage1` 的 `Stage1View` 响应
  （连续分区、原文章号诊断与 `anomaly_count`）。
- `agent_capabilities.json`：按 `novel/orchestrator/executor_capabilities.py::get_agent_capabilities()`
  当前输出重录（并发值域随 `contracts/invariants.py` 走）；`tests/test_agent_capabilities.py`
  核对完整对象。离线驱动能力示例，不证明真实账户或模型可用。
- `str06_candidate.json`：`tests/story_facilities.py::make_pipeline` 使用 `FakeTransport`
  执行当前 15 节点后，通过真实 HTTP 路由录制的合成候选响应。SourceStory、TargetStory、
  章包、正文回执和最终 TXT 来自同一个封存根；产品域、ID、hash、来源、时间与 revision
  均保留实际生成值。只读面覆盖候选清单、run、source-story、target-story、chapters、
  hook、delivery 和 book；下载字节与盘上成书及 manifest 摘要核对。
  同根 `/api/novels/cand/run` 与 `/run/metrics` 响应也原样录制，假后端仅在 `sid=cand`
  时提供该运行投影和累计统计；未录制的单次运行范围仍明确报告无样本。
  `_fixture` 明示合成材料与零真实付费调用；账上请求/用量是离线剧本的边界记录，
  不代表真实服务调用、首答通过率或小说阅读质量。

## 纪律

所有模拟数据只经 `web/src/api/mock.ts` 接入；mock 与真实模式走同一条代码路径，
不得用旧事实检查或旧审批模拟新链通过。历史样例的 provenance 只记录生产时的配置，
前端不拿它计算当前预算；费用、用量与恢复结果始终消费当前服务账。
形状变化时先改生产与契约，再重新导出；不手工补造审批、身份或来源。
