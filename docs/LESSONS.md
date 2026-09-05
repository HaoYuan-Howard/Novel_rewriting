# 工程原则索引

本文只保留可迁移的工程原则和现役落点，不保存施工日志。历史事故、实验读数和产品裁决见
`docs/DECISIONS.md`；活动事项见 `docs/REQUIREMENTS.md`。编号 1～33 是稳定引用，代码和测试可按
“LESSONS 第 N 条”指向这里。

## 按任务读取

工程任务不再全文通读。先按改动面选条目，跨面时取并集：

| 改动面 | 必读条目 |
|---|---|
| 判据、测试、fixture、覆盖率 | 1～4、7、12、21、23、24 |
| artifact、schema、路径、provenance | 4、5、8～12、22、23、28～30 |
| LLM、语义评审、实验、成本 | 1、2、16～18、25、33 |
| 编排、队列、恢复、AgentRun | 4、18、19、24、26～33 |
| API、前端、审校界面 | 4、6、13～15、19 |
| 纯跑书与流水线代批 | 不读本文；遵循 `.workflow/skills/novel-pipeline-operator/SKILL.md` |

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
设计。落点：`web/DELIVERY.md`、`contracts/schema/api.py`。

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
落点：`web/src/api/`、`web/src/constants/`、`contracts/schema/api.py`。

## 14. 类型同型只能证明适配器对齐，不能证明真实链路可用

联调必须覆盖页面动作 → API → HTTP 路由 → 服务 → 最终产物，并检查刷新/重启后的状态；TypeScript
接口和 mock 通过不能证明真实 URL、状态码与落盘。落点：`web/src/api/endpoints.ts`、`novel/api/routes/`。

## 15. 审校界面要保留证据关系，而不是只给汇总数字

对照任务要保留“哪段对应哪段、哪里未对齐、问题如何回源”；状态同时用文字和视觉编码表达。
落点：`web/DELIVERY.md` 与 `web/src/components/compare/`。

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
来源账、失败恢复与重启状态。证据落点：`docs/DECISIONS.md`。

## 20. 运行规程要经无上下文盲驱，文档也要有生命周期

让只拿入口的执行者盲驱可暴露脑内步骤；活动待办、现役契约、操作手册、历史证据和经验必须分工，
完成项从活动口吻移出。落点：`README.md`、`docs/REQUIREMENTS.md`、`docs/OPERATIONS.md`。

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
必须有 typed actor/reason/subject 证据。落点：`contracts/schema/api.py::InboxItem`、`novel/views/inbox.py`。

## 28. 硬闸要落在所有调用方必经的最低边界，并与恢复材料同批交付

前端按钮、skill 或组合命令不能独占硬闸；闸放在 CLI/API/编排最终汇合处，并保留只读 workpack、人工
编辑和 0 成本复检。外部写入统一吃注册表版本化 artifact 形状。

## 29. 会话边界也是证据契约，必须在首个动作前物化

首动作前冻结 `started_at` 与代码/skill 身份；账本先按 sid 再按 run 切片。结束时当前值不能冒充起始值，
跨代码身份是新 epoch/run，而不是同一次续跑。

## 30. 旁路决定账必须在每个读出口叠加，不能只在重新生成时烘焙

`wontfix`/resolution 不重写原报告时，check、inbox、API 都要在最低读边界叠加最新决定；测试覆盖“报告 →
决定 → 立即读取”，不能靠重跑 producer 才生效。

## 31. 测试临时目录要有固定身份，创建者退出前必须把 ACL 还给普通用户

pytest 固定复用仓内短根 `.tr` / `.test_cache`，长路径也只复用一个仓外短根；遗留标准库临时调用由
会话 fixture 收进本次 basetemp。正常退出只恢复本轮实际触碰根的 ACL，不递归扫描陈旧 runtime；强杀后
一次性管理员接管并删除遗留目录，不以不断换目录名回避。操作见 `docs/OPERATIONS.md`。

## 32. 顶层进程退出不等于子执行闭合，下一动作也不能靠执行者记忆

下一动作必须由生产 planner 在冻结 snapshot 上给出，执行前绑定 exact token/claim；父进程关闭新 claim
后仍等 active claim 与 execution 闭合，超时只报警不释放锁。故障只走 hash-bound reconcile。

## 33. 模型轮次上限要数真实调用；最后一次返工必须把修复与自审绑成一个原子动作

预算在唯一模型调用缝计数，0-wire 修复不占轮次，resume 不重置业务预算。最后一轮若同时修复和收口，
typed 动作要绑定修前主体、修后自审与完整 producer 校验；任何 blocking 未闭合则整组拒写并持久化拒因。

动态新增的 typed 数组行不能只给字段名或自然语言提示：完整必填键集、空值的显式写法与允许值必须随
submission option 下发，最终仍由同一 schema/loader 复验；否则模型可能完成整轮语义工作，却因漏一个
`evidence: []` 之类的结构字段浪费不可返还的真实席位。

判别联合还要把**当前分支的局部 schema**下发：若父 model 才知道 collection→member 类型，直接给模型
一个大联合，它很容易臆造惯用的 `value` 字段。应从现役 Pydantic operation/member model 现场专门化，
明示每个 op 的闭合键集，不加宽 loader 兼容错误形状。单行 schema 看不见的父 model 跨操作不变量
也必须明示下发：例如 split 输出不复用输入主体，输入/输出各自唯一且不相交。对这类
静态生产拒因可用严格白名单 reason code 指导纠错，不得回显可能含正文/ID 的任意 validator 消息。
修后证明的完整性同样按主体身份而不是人的逻辑
推断：只改成员字段而未改 ID 集时，集合完整性 hash 可能不变；凡仍以同一 identity 存活的旧 FAIL/UNVER
都须显式复判，最后仍由内存 apply→normalize→reenumerate 后的生产安全闸判存活，外层不复制判据。

当完整 typed request 大部分是 immutable binding 时，不应让模型为改几格而重抄整份长 JSON。provider
wire 可以只承载最小 delta，但 Supervisor 必须从同一 exact request 可信组装完整 wrapper 与
**稀疏** canonical 业务 edit，再走既有 loader 与 producer。compact 的空数组/null 是“本次不提交”，
不得把 exact request 里的 no-op 骨架带进业务 edit；已提交行必须是模板的无重复有序子序列。
不可编辑字段、已提交行的乐观锁 SHA 和主体 SHA 一律注入，不靠模型复述。transport
原始 digest 用来识别模型是否重复回答，assembled content digest 用来寻址真正保存的候选，两者用途不同。

付费 Reviewer 回答一旦通过 typed loader，就必须在 canonical scratch 晋升之前按内容寻址持久化；
Stage2 node 也不例外。pause/stop/重启后可按同一 business + workpack binding 零模型重放，只有
canonical action 真正 landed 后才由书内 typed 产物接管恢复；不能在两者之间的崩溃窗丢掉已付费决定。

原生 structured-output schema 也是 session 身份，不只是 CLI 参数：先冻结 canonical bytes/SHA，exact
resume 必须复用，并把同一 SHA 贯穿 started/attempt/turn/ended 与在线指针。只能接收 schema 文件路径的
driver 把文件限制在 runner 生命周期，残留只按同字节普通文件回收；只能内联 schema 的 driver 则必须在
wire 前预检 Windows argv。临时运输文件不能因为落在 scratch 就冒充 artifact 或 checkpoint。
