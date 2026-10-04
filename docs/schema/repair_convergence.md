# 返修归因、计量与成书验收规格

本规格对应 2026-10-04 用户确认的推进顺序。行为场景使用中文 Gherkin；
架构边界写在本文，进度只登记于 [REQUIREMENTS.md](../REQUIREMENTS.md)。
本批以根 I-02／I-16／I-20／I-35／I-43、后端 R-5.16～R-5.20 与现役额度为基础。
这是目标规格；未完成的场景不能冒充当前实现。契约实现时同步后端契约和代码注册。

## 行为规格与验收映射

Gherkin 使用 [Cucumber 官方语法](https://cucumber.io/docs/gherkin/reference/)。
`.feature` 是行为说明，不引入第二个生产规则引擎；场景由现有 pytest 和 golden
入口验证，只有绑定具体测试或实测证据后才能记为通过。场景编号稳定，测试名可演进。

| 规格 | 场景 | 实现与验证边界 |
|---|---|---|
| [返修闭环](../specs/repair_convergence.feature) | RC-01～08 | 正式材料来源、源目标坐标、纯归因、统一路由、Design接受与最终材料验证 |
| 同上 | RC-09～15 | 问题身份、有限修订、耦合patch、消费级联、免费恢复与旧根处理 |
| 同上 | RC-17 | names v2 同一性留证；`tests/test_str06_name_resolution.py`：真实Stage2发布、Preserve消费、未决零下游购买、局部归一与旧版本拒识 |
| 同上 | RC-16 | 真实CLI／调度／交付／重启的零付费stage与产品入口测试 |
| [计量与对照](../specs/cost_accounting.feature) | CU-01～06 | provider usage解析、新购和缺账恢复、缓存去重、只读历史补证 |
| 同上 | CU-07～10 | 必要输入送达、精确请求身份、分节点配置对照、可比成本报告 |
| 同上 | CU-11～12 | `tests/test_str06_cli_dispatch.py` 配置／权限与篡改反例；`tests/test_str06_product_entry.py` 实际请求、免费复检及HTTP到真实子进程全链 |
| [整书验收](../specs/book_acceptance.feature) | BA-01～06 | 工程冻结、三书分母、人工干预披露、六维质量、完整成本 |

当前实现：材料来源在 `novel/core/material_trace.py`，归因在 `novel/stages/stage4/repair.py`，
共享预检在 `novel/stages/stage4/preflight.py`，旧账更正在 `novel/stages/issue_recovery.py`。
计量解析和补证分别在 `novel/llm/usage.py` 与 `novel/llm/usage_audit.py`；对应
`tests/test_llm.py`、`tests/test_str06_f7a_wiring.py`、`tests/test_repair_rounds.py`。
新名称观察和Characters信封为v2，旧版须重建names后经compile重建，不能补空声明冒充核验。

既有测试入口：`tests/test_str06_a5_positions.py`、`tests/test_str06_r3_selfheal.py`、
`tests/test_str06_item_patch.py`、`tests/test_str06_product_entry.py` 与
`tests/test_str06_f7a_wiring.py`。扩展反例必须执行真正判据和消费者，不以字符串
存在、函数存在或规格语法正确代替行为证明。新增模块同步 suite_catalog。

## 责任规则

归因回答两个问题：文本从哪里产生；哪个正式选择使它出现在当前消费位置。
“包含该字面的材料”不自动等于“应改写该文本”。源章与目标章永不按同号相认。

| 证明 | 修复责任与边界 |
|---|---|
| 原文存在未登记的姓名异写 | Stage2 names 留原字面、原句与同一性理由，same 指向已登记人物称谓；unresolved 停止；compiler 只在证明的句段范围归一语义文本，下游不私改命题 |
| 源称谓、源观察或证据确实错误 | 按正式来源回 Stage2 book／章观察；不根据目标章倒推源首现 |
| Preserve自写摘要含不适用字面 | Preserve只能改自身wire字段，保留anchors和语义义务 |
| 正确源命题被放在不适用目标拍位 | Design统一处理know拍位与目标称谓释放；compiler不替其选择 |
| Design自写文本或默认keep表达冲突 | Design文本／expression_overrides与释放选择；不能修改继承源文来掩盖 |
| Hook自有材料选择或释放冲突 | Hook Design；正文Design字段不可被Hook暗改 |
| 正式设计正确、确定性投影有误 | code缺陷停止，修compiler／renderer；不消耗语义返修 |
| 来源缺失、多义、摘要过期或码未登记 | 未决停止，给出具体缺口；不默认派Preserve或Design |

修复必须重新通过保产、模式授权、引用、披露和实际写手材料的原判据。
不删除义务、不把known改成suspected、不自动提前释放名称来“修绿”。
语义节点可在已有契约允许的选择空间内生成合法新设计，compiler不作语义推断。

## 数据流与职责

1. compiler／renderer在确定性投影时保留可重建的字段来源；来源绑定正式产物、
   字段引用和目标消费位置。诊断只作派生证明，不成为第二份故事真源。
2. validator返回结构化问题（code、稳定位置、命中字面、来源和释放依据），只读。
   现有字符串错误由该结果投影，禁止下游再正则解析展示文字来做业务决定。
3. 纯归因函数输出可证明的责任或明确未决；统一路由表决定revise_self、
   revise_upstream或stop，orchestrator只执行登记动作。
4. 具备完整正式输入的检查在owner接受前执行；最终compile对实际Writer／Hook
   材料再次验证。两处复用同一规则，不能靠前置局部通过跳过最终消费验证。
5. owner修订发布后按实际消费身份级联；原发现节点成功复验才resolved。
   无关章包不重购，unknown、INVALID、过期、合法空值保持区分。

先使用内部结构化派生值；若正式artifact或持久runtime协议形状必须改变，同批
显式升版并登记免费重建／旧版本拒识，不向领域schema塞日志或供应商usage。
不新建通用事件溯源框架、全局失效DAG或另一套预算账。

## 收敛与旧根恢复

问题身份由规则码和稳定正式位置组成，不含展示文案、时间戳、run id。
同任务严格缩小的问题集可以在原额度内继续；相同集合、历史循环按现役规则停止。
patch与整份修订都占内容轮；跨owner仍受原累计上界保护，不因重启或改名退还额度。

旧根曾被错误二分路由消耗的费用、原issue和内容轮必须保存。新归因先免费重验；
错误旧issue的退出须走显式、身份绑定且可审计的恢复操作，不能简单删文件、改owner
或把rounds_total归零。新owner能否执行仍由其原有许可、额度和当前输入证明决定。
若现役入口不能表达该更正，本批须补恢复契约与反例，不能用手工改账代替。
冻结生成代码不追改；工程新epoch和原冻结质量实验明确分开，人工恢复不得改记自动成功。

## 用量与成本

统一纯解析器处理原usage，供当前付费入口、仍现役的client和缺账恢复共用。
合法整数0保留；缺失或null保持未知；top-level和completion_tokens_details中的
reasoning均可读取，两处非空冲突、非法类型、负数或大于completion时该读数未知并
报告诊断。保留原响应和usage，不因计量诊断重新购买或丢失可用回答。
reasoning不另加到completion，只有两者均已知且一致时派生回答token。

ledger仍是成本权威；原账已有行不因新解析规则被覆盖。历史补证只读且绑定attempt
及原响应摘要，报告区分账内值与补证值。缺账补尾幂等，免费采用单列，失败与重试
纳入真实成本。供应商实际扣款、按执行时费率估计、统一费率对照分别表达。

逐轮报告按工作单元的内容轮ordinal及其attempt顺序关联AcceptanceRecord，不按时间戳或随机ID排序。
免费复检可能更新同轮接受记录，所以当前首内容轮接受率不是历史首过率；对照实验须在每臂结束时
冻结当时记录，之后的免费恢复另留快照。网络重试、内容修订和免费采用分别统计。

### 请求配置与权限

用户已裁决：模型、thinking与effort是请求数据，付费和正文外发是独立权限。
Step的加法契约新增可空`request_profile: LegacyLlmProfile`，复用唯一配置schema，不含密钥。
历史Step字段缺失读作None（未知），不补默认值；此兼容边界与删除条件登记在REQUIREMENTS。
新配置的付费Step及其免费followcheck必须携带相同配置，CLI JSON与Step字段全等；父运行配置
不一致即在请求前拒绝。免费Step仍禁止executor_backend、`--yes`和`--allow-egress`。
连接信息仍在安全配置边界读取，不能通过请求配置覆盖API密钥、URL或代理。

`tools/effort_ab.py`只构造单节点配置与正式CLI调用，默认免费preview，不是完整实验执行器。
只有真实请求与ledger均匹配冻结配置，才能证明配置生效；修改NODES或UI显示不能作为证据。

## 实验与结束条件

输入瘦身先证明必要字段完整送达、派生可重建，再衡量实际输入token。思考配置
在同一节点冻结正式输入和其余变量，逐臂记录请求身份；不承诺任何节点默认降档。
工程测试使用fake transport。新付费实验先核对原授权的书目、模型、外发对象、
输出根、预算与配置范围；不把旧冻结三书授权解释为无限新增对照或追加额度。

整书阶段通过正式产品入口执行到Stage7，检验最终文件、manifest、费用账和重启读回。
保留原三书分母和失败轮，Golden oracle只在既定评测时机解封，生成修复不读取。
六维评价对应BA-04；旧九维阅读观察作为补充证据，不产生另一套成功判据。
只有完整且质量达标的可比成书支持整书降费；当前Stage4费用只是阶段基线。
