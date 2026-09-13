# 完整剧情链的界面材料

`flow.json` 由 `scripts/export_plot_fixture.py` 执行证据自洽的三章合成书后导出。
它包含正式生产与 HTTP 投影返回的阅读、阅读工作包、分场、结构审阅、上下文和进度材料；
模型边界使用 FakeTransport，写作仍须经过完整接受与授权。Stage2 语义审阅已退役，
统一机械准入只证明产物形状与来源闭合，语义未审随交付披露。

`review.json` 是同次导出的 Stage6 Prepare/Hook 与 Stage7 终态工作包。它和 `flow.json` 均只经
`web/src/api/mock.ts` 进入消费测试；屏幕不得直接读取 JSON 或把它与历史样例书拼接。

这些材料验证前端对当前契约的消费能力，不证明真实模型的剧情质量、调用成本或全书验收。
普通分场的语义不在机械检查范围内，不能把证据覆盖或证据重叠当作语义结论。

形状变化时先更新生产与测试链，再重新导出；不手工补造审批、身份或来源。
运行导出脚本需使用 `novel_refactor` 环境及独立短临时根，不能与使用同一临时根的 pytest 并行。

`core.json` 由 `scripts/export_stage2_core_fixture.py` 从免费联合首抽、独立正式产物及真实
Global/Characters 只读投影导出。它是独立合成书，包含 Characters v2 与 Global v7，
以及同一次响应的共享来源。当前未接受状态原样保留，不与历史小说或 `flow.json` 的
审批拼接。临时书在 worktree 内清理；它不证明真实模型质量或实际费用。
