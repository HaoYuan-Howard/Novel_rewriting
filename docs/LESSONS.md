# 工程原则索引

本文只保留可迁移的工程原则和现役落点，不保存施工日志。历史事故、实验读数和产品裁决见
`docs/DECISIONS.md`；活动事项见 `docs/REQUIREMENTS.md`。编号 1～37 是稳定引用，代码和测试可按
“LESSONS 第 N 条”指向这里。

## 按任务读取

工程任务不再全文通读。先按改动面选条目，跨面时取并集：

| 改动面 | 必读条目 |
|---|---|
| 判据、测试、fixture、覆盖率 | 1～4、7、12、21、23、24 |
| artifact、schema、路径、provenance | 4、5、8～12、22、23、28～30 |
| LLM、语义评审、实验、成本 | 1、2、16～18、25、33、35～37 |
| 编排、队列、恢复、AgentRun | 4、18、19、24、26～34、36、37 |
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

供应商错误的分类必须先消费完整错误格，再限制对外诊断；先截断再分类会把长错误尾部的网络故障或
限额信息洗成未知。诊断可保留内容长度与 SHA，不回显私密错误正文；只识别当前终态的已知失败形态，
不能从模型回答或之前的重连过程事件推断可重试。当前结构化失败优先于 CLI stderr：
明确但未知或空的失败不能混入启动探针的 timeout 等警告，偶然获得热重试资格；缺少结构化失败
时才使用 stderr。Commander 与 Executor 共用供应商的终态取证和分类，已识别的容量不足沿既有
瞬态策略处理，不自动换模型。传输失败的计量留账，半成品回答不进入恢复 lineage。
协议中的 error item 也不自动等于模型工具调用：供应商传输切换通知须按已知闭合形状与后续成功终态
识别，未知形态仍拒绝。无工具审阅守卫同时覆盖 item 的 started/updated/completed，不能只查最终事件。

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
同一语义身份最多一次若是生成协议的不变量，应优先把身份提升为 object key，而不是要求模型在
list 里自行维持唯一，再靠交付后的 validator 拒绝；后者虽然保护正式数据，却已经消耗不可返还的
模型席位。Stage2 compact collection 因此使用由 immutable template 动态铸造的固定键 object，
canonical artifact 仍可在可信组装后保持原 list 形状。文本 JSON 入口还要拒绝重复 object key，
不能让解析器的 last-wins 悄悄改变模型实际交付。

schema 只能关掉结构岔路，不能代替依赖正式基线的候选验证。付费 typed 回答先 durable 并闭合原席，
再在 canonical 子进程前复用 producer 的同一纯候选构造器检查 post-edit registry、外键与章号；业务
拒绝可安全记回原 response digest，读盘/身份/审计异常则保留 durable 动作并停为 unknown。这样既不
复制业务 linter，也不会在预检崩溃窗丢掉已经付费取得的回答。
修后证明的完整性同样按主体身份而不是人的逻辑
推断：只改成员字段而未改 ID 集时，集合完整性 hash 可能不变；凡仍以同一 identity 存活的旧 FAIL/UNVER
都须显式复判，最后仍由内存 apply→normalize→reenumerate 后的生产安全闸判存活，外层不复制判据。

当完整 typed request 大部分是 immutable binding 时，不应让模型为改几格而重抄整份长 JSON。provider
wire 可以只承载最小 delta，但 Supervisor 必须从同一 exact request 可信组装完整 wrapper 与
**稀疏** canonical 业务 edit，再走既有 loader 与 producer。compact 的空数组/null 是“本次不提交”，
不得把 exact request 里的 no-op 骨架带进业务 edit；已提交行必须是模板的无重复有序子序列。
不可编辑字段、已提交行的乐观锁 SHA 和主体 SHA 一律注入，不靠模型复述。transport
原始 digest 用来识别模型是否重复回答，assembled content digest 用来寻址真正保存的候选，两者用途不同。
默认裁决加逐项例外的协议尤其不能把空例外模板误当成不可增行的空闭集；必须从同一
workpack 绑定允许的例外身份，并验证完整 producer→模板→wire→loader 消费链能保留 FAIL。

闭卷修订反馈要区分“未下发原文”和“写手已见、却漏写的具体材料”。只给规则名会让有界修订
变成反复猜测；明确的逐字义务可以把当前 finding 与不可变 given 做原串包含校验，只回指已下发的
缺失片段。不要直接放开整份 finding，也不要用归一或近似匹配扩大暴露面；具体片段只到模型 stdin，
持久审计继续只留规则、数量与身份。测试同时守住可修性、私有原文不外泄和恢复后的同一消费链。

付费 Reviewer 回答一旦通过 typed loader，就必须在 canonical scratch 晋升之前按内容寻址持久化；
Stage2 node 也不例外。pause/stop/重启后可按同一 business + workpack binding 零模型重放，只有
canonical action 真正 landed 后才由书内 typed 产物接管恢复；不能在两者之间的崩溃窗丢掉已付费决定。

原生 structured-output schema 也是 session 身份，不只是 CLI 参数：先冻结 canonical bytes/SHA，exact
resume 必须复用，并把同一 SHA 贯穿 started/attempt/turn/ended 与在线指针。只能接收 schema 文件路径的
driver 把文件限制在 runner 生命周期，残留只按同字节普通文件回收；只能内联 schema 的 driver 则必须在
wire 前预检 Windows argv。临时运输文件不能因为落在 scratch 就冒充 artifact 或 checkpoint。

并行预取必须先在同一冻结 snapshot 上证明自己读的是将来会被消费的业务分支，再去构造昂贵工作包。
“下一个节点已 ready”并不自动等于“预审也有资格”：只有冻结审阅项非空且全 pending 才能预审；若
planner 已登记另一条可直接消费的 INVALID 投影路径，就应在读取 formal 或旧下游产物前跳过预审，
直接执行已铸 token。否则并行优化会把不存在的 formal 变成假红，或花一席审错历史分支。

动态枚举要按 operation 的语义方向收窄，不能把一个闭集套在所有字段上：delete/split 的输入主体只可
来自冻结 current collection，add 的新主体则必须保持开放；direct patch 主体也不能为了躲避跨写口冲突
而从输入 enum 中删掉，冲突仍交 canonical validator 拒绝。同一回答涉及多 collection 时，producer 应先
在修前全集上聚合所有 operation diagnostics，再应用任一 operation，避免有限订正预算被逐组首错耗尽。
业务拒因跨进程传播时只发布代码定义的可信
collection/category/count 与完整诊断摘要 SHA，不回显任意 ID、正文、loc 或 repr。最后，ledger 的
`purpose` 只是“这次调用为何发生”的审计标签，不是业务基线；merge/drop 必须同时由 planner 的 current
waiting_on 与 durable failed edit 证明，不能看到 `purpose=correction` 就把初始 sparse 空值改读成“保留旧失败”。

## 34. 两相 journal 的 producer 已结束时，same-run 恢复会与 foreign guard 互锁

prepared journal 只证明“目标与提交意图已经封存”，不证明创建它的 producer 还能合法续写。若 source
execution epoch 已结束，普通重跑会被 foreign reservation 拒绝，而 stale action token 又无法回到原
same-run finalizer；这时恢复必须是高于 approval、inbox 与付费 planner 的唯一 deterministic action。

跨 ended-producer 补尾不采用新模型响应，也不续旧 provider。action 同时锁 source run、journal 原始
字节 SHA、自哈希 commit SHA 与 live target；只接受 baseline 或 sealed target 两态，多份 journal、物理
双副本、其他 prepared、第三态与不完整 source 生命周期一律关闭。新 run 必须从已验 checkpoint/session/
attempt/ledger 用当前 compiler 重建**完整 typed payload 与 provenance**，不能只比较业务正文或相信 journal
自己的 hash。

所有可能失败的账面条件都要在首个 artifact 写之前做 0-or-exact preclaim：source adoption、source-stable
canonical relation、实际 recovery run/parent attempt、JSONL LF 尾与全局身份唯一性。baseline 才写 sealed
target，target 只复验；adoption/audit 幂等补齐后，最后一步才把 source journal 标成 committed。这样在
候选、adoption、audit 或 mark 后再杀，下一枚 recovery run 仍能精确接管，而不会借旧 committer 的审计、
重发模型 wire，或把删失的账洗成已经提交。

## 35. 阅读信号没有行动权，不能经自由理由洗成付费修订

人工 coverage 可以独立发现程序未标定的语义缺陷，但这不等于所有呈现给 Reviewer 的行都能授权动作。
INFO、metric、UNVER、binding concern 与 `allowed_actions=[]` 只帮助定位；若 Reviewer 只把它们的文字抄进
自由 reason，下一层又无条件要求生成 typed repair，就会把只读证据“洗”成真实付费权并形成返工循环。

完整闭合需要三层同时守住：prompt 明说读数不是答案；审批 subject 把 evidence-authority contract 纳入
身份，升级后旧票只留历史；同一业务 subject 的决定只允许 exact typed replay，不能靠同 token 改票逃生。
若只是完整 provenance proof 漂移，允许保留旧 history 后把同一 payload 重绑到新 proof，但绝不改决定。
后继 resolution 只有在能独立指出具体 scene/fact/goal 损害时才形成 typed 约束；否则提供只停机、不写审批、
不签 token、不执行模型的 typed stop。这样既保留人工发现新问题的权力，也不让“材料不足”自动变成
request_replan/request_repair。

## 36. 批量人审回答必须先耐崩溃，再开始逐项写

同一个人工 blocker 里有多项同类决定时，逐项开席并不更安全：每落一项都会改变 workpack，下一项就会
获得一个新业务身份和新付费席位；而先把整批交给 Commander、再逐项落盘，又会产生“人已经答完、席位
已经扣账，但 batch 只写一半”的新尾窗。完整设计必须把**人审原子**与**产物原子**分开：一席提交覆盖
完整 current 集合的 typed manifest，Supervisor 在席位成功闭账和 canonical 执行前先把原 provider 回答
及 manifest 内容寻址地 durable；producer 再在自己的书锁内按项幂等提交。

稳定业务不能绑定会被本批写入逐项改变的 workpack/token，而应绑定批次开始时不会自变的证据：动作模式、
上游 Review、原始全量有序 item 身份与审阅契约。断点续接也不能只看“某些项已经存在”：它必须证明盘面
恰为原 manifest 的连续 exact 已落前缀加 current unresolved 完整后缀，前缀的 actor/reason/双 SHA 与原决定
相同，后缀不漏、不夹、不重排；首写前以及每一笔后继未落项写入前，都要在同一所有权内复采完整
frontier 与当前项，避免首项落盘后的盘外漂移或 foreign 决定被旧 facts 覆盖。证据冲突时保留 durable
record 并停止，不能把它删掉后再买一席。

席位账的顺序同样属于提交协议：先把 provider response digest 绑定到具体 turn，再保存 action record，
最后 finish 席位。恢复必须在原 parent 已停止的所有权窗口内，把 seat attempt、logical session、成本账、
模型/代码/协议与 response digest 逐项闭合；原 turn 留在 started/open 时只能据这些证据幂等补尾。更晚的
typed/canonical 拒因可以压过旧 action，但必须由 seat ordinal 和同样强的 session/cost/response 证据证明，
不能靠秒级时间戳或文件名猜“最新”。若决定只写旁路处置而不改正文，上游 Review 仍可保持 fresh；只重建
消费该处置面的下游 workpack，避免把零成本处置误写成一次新的审阅或生成。

## 37. 会让授权主体失效的付费动作，必须先把意图正式化

一次人审回答若会触发付费写入，而写入第一份 canonical 产物后又会让原 Review/workpack 自然 stale，
不能把临时 response、service input 或 `ReviewerActionRecord` 直接当长期执行权。它们只证明“这一席答过”，
不提供 queue-visible 的 pending/consumed 状态；宿主若在首个模型响应、checkpoint、prepared commit 或
canonical 写回任一窗口死亡，planner 会离开原 blocker，剩余动作既可能丢失，也可能重新付费。

完整路由要先用 0 成本 producer 把人审意图落成 current、内容寻址的正式 request；typed readback 后还要
把临时 action record 原始字节和 seat/session/cost 三账摘要封成 immutable authority receipt，receipt 成为
commit point 后才退休 active record。否则“request 已在、唯一授权刚删”仍是一个无法恢复的硬杀窗口。
frontier 的正文 SHA 与 canonical JSON 完整文件 SHA 也是两个不同域：前者证明 trim/audit 指向哪段正文，
后者承担目标文件 CAS；同一字段复用两种语义会让正路永远拒绝，或让历史恢复对错文件。
内容寻址原文件的 SHA 必须从原字节复验，不能从今天的 typed model 重序列化再算。新增可选默认字段
或 serializer 排版变化不改变历史文件；typed 校验、原字节内容地址及 receipt/三账关联应分别完整检查。

queue 再让该 request 的 pending/unknown/recoverable/terminal 状态优先于已经 stale 的
上游审阅和普通新席。真正付费的 producer 必须在独立 fresh epoch 取得一次性 permit，并在首个 driver/
probe/wire 前写 request execution journal；同一 provider session 的后续业务 turn 也必须先追加 retry intent，
不能让旧 checkpoint 在“下一轮已 wire、但新 checkpoint 未落”的窗口里冒充最新可恢复稿。request 专属
跨进程 lease 要覆盖 paid preclaim 至 terminal，也要覆盖 FREE recovery/retirement，direct CLI 不能成为并发
旁路。有 wire而无可验**最新** checkpoint 就是 unknown，不能重发；全根确证零执行的 stopped owner 才可
写 reclaim 证据后重开。

checkpoint/prepared 只能按 exact request 做零 wire恢复，formal/INVALID/rejected 都要留下 terminal consumption。
但 terminal 文件后来进入 history 不能自行表示“问题解决”：recheck/revalidate/apply-edit 需先写
request-specific successor WAL、保存 terminal 原字节，既有 0-wire 动作落稳后再提交 exact successor receipt。
这也覆盖“产物已换、receipt 未写”的硬杀窗，并防止手工移动文件冒充合法后继。
这把“人决定什么”与“模型是否已执行到哪里”拆成两份可恢复事实，也阻止手写 JSON、旧 stop 或孤儿 CLI
借相同外形取得付费权限。


2026-09-07 前端实跑中，Commander 识别出两章独立重复，却被“恰有一章”误导为必须 stop。
用户要求按写作并发 4 同批改写、统一 Review；R-7.31 因而改为批信封绑定有序目标子集，
每章仍有独立 execution/checkpoint/恢复身份，未选章保持 unresolved，不暗中代签 retain。
并发不代表两个章节可以共同依赖未完成的改稿：必须跨章联动才能成立或超出关闭式指令时仍 stop。

批量故障测试必须跨过真 session 和正式提交。仅验证 option/request 构造会漏掉：producer 写
`rev-0001.json` 而 reader 不接受补零编号；恢复 policy 未进 SessionAgentClient 登记；主线程 lease
未在 worker 取得；提交前先移除旧 audit 导致自身 currentness 校验失败；prepared/committed 的合法
摘要变化被误认成漂移；正式文件已落盘却在 sweep 中再次强求旧目标 SHA。用双章 barrier、第二章
提交失败、committed 后硬杀模拟与零模型恢复断言覆盖这些消费链，不能靠删闸或重买模型解决。
