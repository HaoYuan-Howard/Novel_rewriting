# 后端测试开发与执行

本目录按单元 / contract、stage、回归、golden set 四层职责维护测试。改测试先确认它能抓住目标问题，
不能靠删现役职责反例、放宽判据或缩验收分母提速；确认退役或等价重复的测试按 README 的删除规则清理。
测试选择与整批门槛由根 `AGENTS.md::测试选择与批次验收` 定义；
各层执行方式、golden set 与 corpus 的边界见 `tests/README.md`，
完整命令、Windows ACL 与故障清理见 `docs/OPERATIONS.md::pytest 与 corpus`。

## 执行前

- 后端使用 conda 环境 `novel_refactor`。agent 跑 pytest 必须绕开文件系统沙箱，避免 Windows 目录 ACL 残留。
- 复用仓根 `.tr` / `.test_cache`；路径过长固定复用 `E:/_tr`，不按任务或时间新建根。
  不并行启动共享同一 basetemp/cache 的 pytest；正常退出仅恢复本轮触碰根，强杀后的清理按 OPERATIONS。
- 单点用 `-n 0 -x`，失败回放用 `-n 0 --lf --lfnf=none -x`；两者只可报告“定向测试”。
  默认非 corpus 回归为 `-n 4 --dist loadgroup -m "not corpus"`；corpus 硬验收显式 `-n 0 --require-corpus`。
- 工程分层通过 `--test-layer unit_contract|stage|regression`；`--test-stage` / `--affected` 选择阶段依赖，
  `--changed` 选择文件及消费者，具体命令见 README。golden set 只经 `python -m tests.golden` 显式执行。

## 测试结构与隔离

- 单元 / contract 使用最小确定性样例验证约定；stage 测试必须走被测阶段的真实 producer/consumer，
  检查正式产物、拒绝与恢复，不能用 fake 直接返回被测阶段的成功结论。
- 回归复用既有用例；局部改动先按影响选择，单个 stage 重构验证该 stage 及受影响 stage，不默认全仓。
  golden set 使用根指南指定的 Workbench 源书，只在项目最终质量验收时运行；普通测试不依赖该目录。
- 新增模块同步 `tests/suite_catalog.json`；混合文件逐用例分类，单例 stage_scope 只能收窄模块范围。
  文件名、注释中的“真实”不能作为 stage 分类依据：被测 producer、判据、Store 和消费者必须实际执行。
- 删除测试先核实现役契约、生产调用、CLI/API、动态注册和运维消费者；旧格式拒绝、免费恢复与独有故障反例
  仍归现役职责。等价重复须比较输入、故障点、断言和消费者；删除同步目录表、专用 fixture、串行登记与引用。
  只为历史复现保留的测试是否退出维护属于范围决定，现役依据不明确时提交具体案例由用户裁决。
- `tests/conftest.py` 管 fixture、代码身份与调度。普通测试按文件成组，线程、跨进程或取消生命周期模块
  登记到 `_SERIAL_TEST_MODULES`；不拆散模块 fixture 或移除 serial 登记换速度。
- 新测试用 `tmp_path` / `tmp_path_factory`，不回到系统 `%TEMP%`。上游准备复用只能保存不可变基线，
  每例还原到自己的空目录；不共享可变 Store、账或硬链接，不复用被测步骤的结论。
- 普通测试使用确定性 code_version；真实身份场景声明 `real_code_version`，corpus 读取真实 Git。
  禁止为测试速度缓存生产 `novel.prov.code_version()`，长驻服务必须看到当前代码身份。
- 纯判据与 prompt 测试用 `tests/factories.py` 等确定性构造器，不随机读取本地小说。直接构造 artifact
  用 `contracts.schema.encode_artifact()`；涉及生产或恢复的断言仍走真实 producer/consumer。
- HARD 反例在真实判据入口有红例、绿对照与分母自检；假红先核对同一 R 下旧 fixture、已退役要求和重复参数。
  fake 只证明一侧组装，进程协议、取消、恢复与交付需补零成本真实消费者，已购结果验证免费恢复。

## 报告范围

记录层级、覆盖范围、命令、代码基线、通过/失败/skip 与 JUnit 分母；汇总行缺失时核对 JUnit 或完整进度，不猜结果。
current-schema manifest、ledger 与精确 skip 面未闭合，不能称“全量语料绿”。corpus 规程由
`tests/README.md` 与 `docs/schema/corpus.md` 维护，不另建验收清单副本。
stage / corpus 通过不能代替成书质量评价；golden set 还须报告逐书质量证据与人工验收结论，未评保持未验证。
