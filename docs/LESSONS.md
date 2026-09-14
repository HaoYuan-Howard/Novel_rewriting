# 专题经验索引

工程默认入口是根与目标目录的 AGENTS；本文件只供按编号或问题检索，不再作为每次任务必读集。
编号 1～37 保留，兼容代码注释中的现有引用。详细事故机制与原始例子在 Git 的
`1fbb2fd5482b966b4ec36f6b379b777028cf2f03:docs/LESSONS.md`，不将已修复过程反复加入上下文。

| 专题 | 局部上下文 | 相关编号 |
|---|---|---|
| 规则、版本与证据 | `contracts/AGENTS.md` | 1～12、16、22～23 |
| API 与页面 | `novel/AGENTS.md`、`web/AGENTS.md` | 6、13～15、19、27、30 |
| 测试可信度 | `tests/AGENTS.md` | 2～3、21、24、31 |
| 付费、模型和恢复 | `novel/AGENTS.md` | 17～18、25～29、32～37 |
| 文档生命周期 | `AGENTS.md::文档职责` | 12、20 |


## 1. 约束只有落到可执行守卫上才算存在

每条“必须”都要回答谁产出、谁检查、失败如何显式呈现；机器不能可靠判断的语义明确归人或 `unver`，
不能用无判别力的绿灯替代未知。落点：`contracts/rules.py`、`contracts/judges/`。

## 2. 绿灯必须携带扫描分母，并且至少见过一次红灯

“命中 0”可能来自空输入或漏接线。报告要给扫描量/freshness，HARD 要有最小反例走真实判据入口。
落点：`tests/hard_cases.py`、`tests/test_hard_counterexamples.py`。

## 3. 守卫的扫描面也需要守卫

参数表、正则、fixture 或 skip 面失效会让整批测试假绿。分母从现役注册表派生，并对非空、数量和
正反方向设自检。落点：`tests/test_coverage.py`、`tests/test_docs_references.py`。

## 4. “不知道”“没运行”“缺失”“空结果”和“没问题”必须分开建模

这些状态不得压成 `false`、`null` 或空数组；unknown、stale、unreadable、合法空值和 PASS 要在 schema、
API、作业与界面逐值保留。落点：`contracts/schema/report.py`、`novel/orchestrator/state.py`。

## 5. 样本、证据基线和现役契约是三种东西

样本只证明曾出现，历史证据只解释裁决，schema 才定义当前边界。三者分目录、分入口，禁止用新实现
输出覆盖证据后再与自己比较。落点：`docs/BACKEND_CONTRACT.md` §八、`docs/schema/`。

## 6. 原型是意图证据，不是生产实现的权威

冲突按“当前产品裁决 → 后端契约与真实 API → 原型”消解，并写明有意偏离；照搬原型代码不等于忠于
设计。落点：`web/AGENTS.md`、`contracts/schema/api.py`。

## 7. 单一落点必须覆盖最终消费结果，而不只是模板源码

枚举、阈值和词表只定义一次仍不够；测试应钉最终 prompt、API 形状或消费者结构，防止插值、适配器和
调用参数形成第二套语义。落点：`contracts/invariants.py` 与各 stage 的消费回归。

## 8. 删字段前要审计语义消费，不只搜索字段名

整字典迭代、模型复制、别名和派生函数都可能间接消费字段。删除前确认可否重算、历史如何读取、消费方
需要字段还是语义，以及失败成本由谁承担。落点：`contracts/schema/__init__.py`、`tests/corpus.py`。

## 9. 加落盘闸门时，必须同批提供零成本恢复路

付费产出被 HARD 拦下时必须可经 `--revalidate`、人工编辑或等价路径恢复；不能把 `.INVALID` 变成永久锁。
落点：`novel/store.py` 与各 stage 的提升路径。

## 10. 生成、归一化、人工写回和提升必须共享同一把尺子

输入来源可以不同，schema、归一、判据、归档和 provenance 入口必须相同；人工稿不得形成比 producer
更宽的暗门。落点：`novel/store.py`、`novel/prov.py`。

## 11. 来源账要能复现实质输入，而不只是写一个版本字符串

provenance 至少要追到代码/脏树、输入、prompt、模型参数、修复动作与调用/缓存账，并从报告可见。
落点：`contracts/schema/common.py`、`novel/run.py`、`novel/llm/ledger.py`。

## 12. Markdown 的形状和引用同样是接口

被代码解析的标题、表格列、编号和记号都是语法；引用使用稳定编号或 `仓内路径::符号`，改名和搬家必须
让测试发现悬空。落点：`contracts/requirements.py`、`tests/test_docs_references.py`。

## 13. 前端只呈现判定，不补后端没有给出的结论

费用、写盘、新鲜度、严重度和通过状态不在前端重算；服务端没给结论时显示原始读数或 unknown。
正式 typed 决定已落盘后，API 检查单必须消费同一 current 证明，不能因 CLI 未经过 HTTP 的附加
盖章而另造待审批状态；只读投影不补签。独立验收也要检查公开投影的一致性，不能覆盖后隐藏分歧。
落点：`web/src/api/`、`web/src/constants/`、`contracts/schema/api.py`。

## 14. 类型同型只能证明适配器对齐，不能证明真实链路可用

联调必须覆盖页面动作 → API → HTTP 路由 → 服务 → 最终产物，并检查刷新/重启后的状态；TypeScript
接口和 mock 通过不能证明真实 URL、状态码与落盘。落点：`web/src/api/endpoints.ts`、`novel/api/routes/`。

## 15. 审校界面要保留证据关系，而不是只给汇总数字

对照任务要保留“哪段对应哪段、哪里未对齐、问题如何回源”；状态同时用文字和视觉编码表达。
落点：`web/AGENTS.md` 与 `web/src/components/compare/`。

## 16. 没有 ground truth 时，多一条语义判据可能更危险

先标定命中、漏检和跨书分布，再决定 hard/info/metric/unver/人工；无法建立可靠真值时，诚实暴露边界
优于强行设阈值。落点：`contracts/rules.py` 与语料回归。

## 17. 模型实验要先登记决策规则，再看结果

先冻结样本、提示、质量门和淘汰条件，再运行 A/B；生成质量、评审能力、token、价格和墙钟分别报告，
样本量与未达成项不省略。落点：`docs/DECISIONS.md`。

## 18. 付费与并发必须由可执行计划约束

服务端给出完整 argv 后再确认；批量按书隔离，首个失败停止同书后续扣费，done/failed/not_run 和缓存
命中分开记账。落点：`novel/orchestrator/nodes.py`、`novel/orchestrator/queue.py`。

## 19. 功能完成要验证真实消费者与最终系统状态

函数存在、schema 可构造和单测通过都不是用户路径完成。验证真实 CLI/服务/浏览器、最终产物、归档、
来源账、失败恢复与重启状态。自动运行绕过普通 Job 写入口时，也必须接通同一书级读模型
刷新链；缓存变 stale 不等于会自动重取，进度更新不等于 check/审批已刷新。用不重挂页面的
实际轮询变化验证消费者，并将稳定快照与时间噪声分开，避免失效循环。证据落点：`docs/DECISIONS.md`。

## 20. 运行规程要经无上下文盲驱，文档也要有生命周期

让只拿入口的执行者盲驱可暴露脑内步骤；活动待办、现役契约、操作手册、历史证据和经验必须分工，
完成项从活动口吻移出。落点：`AGENTS.md::文档职责`、`docs/REQUIREMENTS.md`、`docs/OPERATIONS.md`。

## 21. 守卫也会假红，而假红的措辞会把人指向错的地方

先排查三形：测试重拼了生产实参；断言了一道产品不存在或 severity 相反的闸；契约转向时漏改同一 R 的
其他守卫/fixture。守卫调用生产单一落点，按 sev 写断言，改向时枚举全部消费者，并用真实 CLI/盘面定性。

## 22. 同一份数据的两种形态是一处静默失效点，而两侧的判据会一起变绿

只保留产物形态和最终下发形态，中间层原样搬运；兼容读取分开命名。跨一步丢失要用横跨该步的计数/
身份判据抓，分母为零落 `unver`。落点：Stage4 beat/context 组装与对应 coverage 判据。

## 23. 版本号是“破坏形状的计数器”，不是兼容层的入场券

能由 producer 重建就不造迁移；破坏形状时升版并登记重建者、成本和授权。参数化版本守卫使用 concrete
路径，且 concretizer 自己必须有前提守卫。落点：`contracts/schema/__init__.py`、
`tests/test_artifact_versions.py`。

## 24. 用替身测“组装”时，“送达”就没有人测了

fake 只能看见边界一侧；跨进程/协议处要补零成本真实探针，既断言 argv/载荷形态，也断言对端收到完整
字节和盘上结果。落点：`tests/test_agent.py` 及最终 delivery 检查。

## 25. 模型响应契约是可切换策略，不应与模型名永久绑定

模型能力、价格和响应行为是三根轴；契约由显式配置和有日期的裁决选择，不静默 fallback。切默认先过
质量非劣，再比 token/价格/墙钟；退役时关闭所有运行入口但保留历史证据。

## 26. 外层进程成功与内层交付成功必须是两条状态轴

exit 0 只说明外层正常结束；结束后独立从当前盘面核代码身份、强闸、交付物和终审包。观察器失败时
交付为 unknown，不改写进程事实。落点：`novel/orchestrator/delivery.py`。

## 27. “是否还等人”与“是否挡交付”是正交维度

人工项至少分 `attention`（pending/reviewed_retained）与 `delivery_effect`（blocking/disclosure）；已审决定
必须有 typed actor/reason/subject 证据。落点：`contracts/schema/api.py::InboxItem`、`novel/orchestrator/inbox_projection.py`。

## 28. 硬闸要落在所有调用方必经的最低边界，并与恢复材料同批交付

前端按钮、操作说明或组合命令不能独占硬闸；闸放在 CLI/API/编排最终汇合处，并保留只读 workpack、人工
编辑和 0 成本复检。外部写入统一吃注册表版本化 artifact 形状。

## 29. 会话边界也是证据契约，必须在首个动作前物化

首动作前冻结 `started_at` 与执行身份；账本先按 sid 再按 run 切片。结束时当前值不能冒充起始值。
代码漂移开启新 epoch，parent run 的 profiles 与累计授权保持；旧身份只用于来源复验，不能重签成当前许可。

## 30. 旁路决定账必须在每个读出口叠加，不能只在重新生成时烘焙

`wontfix`/resolution 不重写原报告时，check、inbox、API 都要在最低读边界叠加最新决定；测试覆盖“报告 →
决定 → 立即读取”，不能靠重跑 producer 才生效。
闭合消费者也要尊重报告已有的 actionable 投影：基线降级的线索只作披露，不重新强制改稿或拒绝整包；
同规则的 actionable 项仍逐项裁决，缺身份仍拒绝，披露不能冒充 PASS 或已销账。

## 31. 测试临时目录要有固定身份，创建者退出前必须把 ACL 还给普通用户

pytest 固定复用仓内短根 `.tr` / `.test_cache`，长路径也只复用一个仓外短根；遗留标准库临时调用由
会话 fixture 收进本次 basetemp。正常退出只恢复本轮实际触碰根的 ACL，不递归扫描陈旧 runtime；强杀后
一次性管理员接管并删除遗留目录，不以不断换目录名回避。操作见 `docs/OPERATIONS.md`。

## 32. 顶层进程退出不等于子执行闭合，下一动作也不能靠执行者记忆

下一动作必须由生产 planner 在冻结 snapshot 上给出，执行前绑定 exact token/claim；父进程关闭新 claim
后仍等 active claim 与 execution 闭合，超时只报警不释放锁。故障只走 hash-bound reconcile。
当 typed 动作按契约必须跨 producer run（Stage4 repair/replan）时，这也是 execution epoch 边界：
Supervisor 先封存旧 epoch，以 exact action 身份只滚一次 fresh epoch，再重读 planner/CAS 后执行。
不能在同一 epoch 下另造未登记的 child run，也不能删除 producer 的 same-run 防腐守卫来迁就编排。

## 33. 模型轮次上限要数真实调用；最后一次返工必须把修复与自审绑成一个原子动作

内容额度在真实调用缝记账，免费恢复不扣轮，resume 不重置。typed 动态操作给完整分支 schema 和跨字段约束；compact wire 由可信代码组装，禁止重复键或把空模板当实际提交。已购回答先 durable，再验证、闭席与提交；修后存活的 FAIL/UNVER 仍需显式复判。相关工程边界见 `novel/AGENTS.md`，具体机制按调用链读取模块头。

## 34. 两相 journal 的 producer 已结束时，same-run 恢复会与 foreign guard 互锁

prepared 不证明旧 producer 仍可续写。跨 ended producer 补尾走独立 deterministic action，锁 source run、原始 journal、sealed target 和 live target，只接受完整可证的两态；首写前闭合账链，最后才 mark committed。来源冲突或第三态保持 unknown。

## 35. 阅读信号没有行动权，不能经自由理由洗成付费修订

INFO、metric、UNVER 和 binding concern 是阅读线索，不自带付费行动权。typed 返工必须独立指出具体事实/场景/目标损害并绑定当前证据；不足时保留 stop。review contract 变化应使旧决定失效，完整 provenance 重绑不能偷换语义决定。

## 36. 批量人审回答必须先耐崩溃，再开始逐项写

一席回答覆盖当前批次，先耐崩溃保存原回答和 typed manifest，再幂等逐项提交。恢复验证 exact 已落前缀与完整待写后缀，每次写前复核 frontier；先绑定 turn/response，再 action、闭席。冲突保留 durable 记录，不能删掉后重买。

## 37. 会让授权主体失效的付费动作，必须先把意图正式化

会使原 workpack stale 的付费动作，先由免费 producer 落正式 request，再以 authority receipt 接管授权，之后才退休临时记录。独立 epoch/permit 在 wire 前写 journal；每轮 retry intent 先于调用。checkpoint/prepared 只按 exact request 免费恢复，合法后继用 WAL/receipt 证明；批量每章独立身份，未知最新结果不得重发。
