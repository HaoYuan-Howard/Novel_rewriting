# 历史界面样例与当前辅助数据

本目录的原始响应来自 2026-08-17 的第 3 章样例；后续结构示例只供离线界面开发，
不代表 `plot-blueprint-v1` 的抽取、审阅或真实模型验收。

当前完整剧情链的阅读、阅读工作包、分场、结构审阅、写作上下文和进度样例统一位于
`../plot_v1/`，由正式合成测试链及真实 HTTP 投影生成。两套书的主体、审批和正文不能拼接。

## 运行入口

所有模拟数据只经 `web/src/api/mock.ts` 接入。历史小说的完整剧情阅读、Stage4 分场及
Stage2 检查明确返回能力不可用；不使用旧事实检查或旧审批模拟新链通过。
未消费的旧 `stage4_ch3.json` 和 Stage3 `style.json` 已退役，恢复来源为 Git 历史。

以下辅助样例仍用于现役界面的独立行为测试：

- `global.json`：有据主体的结构示例，覆盖 public/via_secret 关系、character/group 知识主体，
  以及原文回取成功和 `source=null` 两态。机械检查不证明语义已审。
- `stage5_ch3.json`、`review_ch3.json`：当前 HTTP 协议的 `legacy_incompatible` 拒绝态，
  不携带旧双臂正文，不冒充已执行的新链。
- `stage1.json`、`stage7.json`、`stage7_ch3.json`、`inbox.json` 与检查报告：
  已注明来源的历史屏幕输入，仅供交互测试；缺少的新判据不能补签成通过。
- `enums.json`：由 `tools/regen_web_enums_fixture.py` 调用当前 `novel/api/enums.py::payload()`
  生成；`tests/test_api_enums.py` 核对完整对象。
- `agent_capabilities.json`：离线驱动能力示例，不证明真实账户或模型可用。

## 保留证据，退出运行消费

`reading_ch3.json` 只保存 Global 证据所需的历史原文快照；`check_stage2.json` 只用于验证旧
结构不能作为当前蓝图接受依据。两份文件均排除在 eager bundle 外，不是现役页面响应。
`tests/test_schema_api.py` 保留七类 Global 证据的原文位置反例和旧阅读响应拒绝测试。

`web/src/mocks/` 的 20 份文件是更早的冻结字节证据，由 `web/scripts/mock-baseline.json`
和 seams 复验，不能覆盖。仅目录/概览/Stage0 输入仍由历史样例解码入口读取，其余冻结正文、
旧事实与审阅材料均不进入运行包。

历史样例的 provenance 只记录生产时的配置，前端不拿它计算当前预算；费用、用量与恢复结果
始终消费当前服务账。Stage6 整层已随 STR-06 切换退役：`stage6.json` 与 `check_stage6.json`
删除，恢复来源为 Git 历史。新链使用干净数据根，不从这些样例迁移事实或继承审批。
