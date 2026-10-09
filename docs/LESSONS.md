# 专题经验索引

工程默认入口是根与目标目录的 AGENTS；本文件只供按编号或问题检索，不再作为每次任务必读集。
编号 1～41 保留，兼容代码注释中的现有引用。详细事故机制与原始例子在 Git 的
`1fbb2fd5482b966b4ec36f6b379b777028cf2f03:docs/LESSONS.md`，不将已修复过程反复加入上下文。

| 专题 | 局部上下文 | 相关编号 |
|---|---|---|
| 规则、版本与证据 | `contracts/AGENTS.md` | 1～12、16、22～23 |
| API 与页面 | `novel/AGENTS.md`、`web/AGENTS.md` | 6、13～15、19、27、30 |
| 测试可信度 | `tests/AGENTS.md` | 2～3、21、24、31 |
| 付费、模型和恢复 | `novel/AGENTS.md` | 17～18、25～29、32～41 |
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

付费产出被 HARD 拦下时必须可经免费复检、owner 修订、人工编辑或等价路径恢复；不能把 `.INVALID` 变成永久锁。
落点：`novel/store.py`（`.INVALID` 孪生写入与退休）与各 stage 的恢复入口（节点级 `--check-only`、owner 修订）；
旧链的 `--revalidate` 提升路径已删除（2026-10-05）。

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
落点：`web/AGENTS.md`（U-15／U-16）。旧链读屏专用的对照组件目录已随 STR-06 切换
（F8c）删除，规则本身不变：将来再做对照屏，按这两条重建，不拿旧实现复活。

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

## 38. 引入「同一 graph 的第二 producer」时，先给类型层收窄，再谈校验

Hook 与正文同住一份 PresentationGraph，若 publisher 只有一个而消费类型不收窄，下游仍可能把
hook 字段读进正文链路。F6 的做法：拆 `BodyDesign`（Bind/Compile/Context/Guard 只收这个类型），
`PresentationGraph(BodyDesign)+hook` 必填、`publish_graph` 唯一发布——「Hook 不影响正文」从类型
上成立，而不是靠校验提醒。同类场景（两个语义请求产一份 canonical）先拆视图类型再谈守卫。

## 39. 文本产物必须有文本落盘口，JSON write 会把字符串变成带引号的 JSON

`Str06Repository.write` 对 str 入参做 JSON 编码，落盘成 `"柳婉望着空房。"`——内容「存在」且
read 回来相等，直到有人按正文读它。新增 `write_text/read_text`（同一套原子写＋回读核对）后，
正文/成文类产物一律走文本口；测试要断言 `read_text` 的原始字节，不能只断言 `read` 的往返。

## 40. 退役清单要双向对账：登记项必须真实存在，现役面必须全部在册

只扫现役面会漏登记（新加文件不在清单），只验登记会指错对象（符号改名后清单静默失效）。
F6d 的登记表每条 target 用四种可核对格式（文件/符号/dict 键/字面子串），测试同时做两个方向；
现役面扫描（NODES 键、stage6 文件、纯 Stage6 测试、前端目录、路由装饰器）发现清单外对象即红。
没有对账的退役清单会在 F8 当天才发现删不动。

## 41. 让模型引用的标识必须对应它看得见的单位；验收器算得出的标识不要让模型抄

2026-10-04 cs_golden 三书停书：证据段只在「。」后切，段号标签悬在上一段文字下方，模型按自然段
自行数号，引用恒偏大一到几十段；验收器已算出引文所在段，却仍要求模型付费改段号，返修在两条
互相牵制的判据间来回。修法是让段＝自然段、一段一行，并把可由引文推导的段号交给 Resolver
（RC-26/27）。只改反馈措辞（「该引文命中于句段 X」）或扩大返修范围都是补丁。另：reasoning 模型
一次条目返修的输出 token 接近首答（实测 17.8K 对 23.6K），降本靠首答通过率，不靠缩小返修条目。
落点：`novel/stages/chain.py::build_evidence`、`novel/stages/stage2/resolver.py::basis_quote`。
（2026-10-07 收口：RC-27 只把段号交给代码，引文仍由模型逐字抄——flash/low 两轮漏抄「在冬日」
触发 validation_no_progress 实证这一半仍是模型负担。用户裁决「模型只选段、代码切原文」：wire 的
basis 只给段 id，引文＝段全文由 compiler 派生，转写失败类错误构造上消失——本条教训的完整版。）

## 42. 标定 LLM 前先自证装置的量程与渲染；判官与被测执行器不得同源

CS-F3 语义核验标定（2026-10-05，45 次真实调用，已封口）的三层教训：① 装置自身是最大误差源——
首轮 max_tokens 4000 使 15/15 次把额度耗尽在 reasoning、content 全空（reasoning 模型先小样本探档
再批量花钱）；锚点窗口 ±2 拒掉窗外同章合法引用、材料行号前缀进入逐字比对造成 wire_error 假阳，
末轮 11/70 案未测得＝13% 分母损失。归因要把「装置量程／渲染缺陷」与「模型格式纪律」分开报，
否则既冤枉模型又虚化正确率（正确 51 的分母是 59，不是 70）。② 判官与被测抽取器同模型＝同源背书：
抽取期读错的主体关系（ch03 误派）核验期照单背书，错误支持恰是历史事故模式——标定判官必须与
抽取器不同源，否则测不出真错。③ 机械诚实的装置救了结论：标签先冻结、逐字闸如实拒、报告恒
unver、「已裁决反例不得错误支持」硬规则，拦下了一个 51/59 正确却背书事故模式的判官——装置的
保守纪律在判官不达标时给出的是「不启用」，这正是它的职责。落点（已删，Git 提交 2d17a898）：
tests/semantic_calibration.py、tests/csf3_golden.py；裁决与重开条件见 DECISIONS 2026-10-05 行。

## 43. 关联失败先核对上游问题粒度；结构通过不证明悬念已完整抽取

同一主题下的复合 open、具体行动 open 和普通事件 close 混用，会让关联模型在重复占用
close、未配对和 unresolved 之间往返；扩大关联重试不能补回章观察漏掉的答案。抽取按
一个具体问题建悬念，明确接受安排与执行成败的分别收束；关联材料带两端拍事件，明确
pair 一对一及 distinct 只否决候选对。结构守卫继续拒发，人工核读确认的上游语义错误
通过原请求身份的 chapters owner patch 恢复，不由 compiler 猜测、删记录或强配。
比较历史成功盘必须同时检查未收束 open：旧版本通过可能只是没有抽到冲突项。
落点：`novel/stages/common.py::CHAPTER_PROMPT`、
`novel/stages/stage2/associate.py::project_identity_candidates` 与正式免费修订入口。

## 44. 已购回答的多 owner 问题须先完整登记；修订主体不是兄弟任务的上游

Preserve 同答报告两章冲突时，在遍历中直接调用必抛异常的登记口，会只记第一章，
另一章的问题仍留在已购回答却不进入恢复范围。按 owner 合并全部条目，捕获可回派
结论后再抛最早节点；重放复用 pending 记录，不重复起轮或计级联次数。
多章同时修订又暴露共享来源窗口串扰：读取各自上一版会把兄弟章旧版串入输入边，
新稿刚发布便被判 stale。上一版是同 owner 的修订主体，经 check_model 验封/新鲜度，
其字节随修订请求留证，不登记成上游。验证必须覆盖「同答多章 → 各章修订 →
重编译 → Preserve 复验发布」，并检查两章 provenance，无兄弟章或下游依赖。
姓名自闸也须在首答协议中说明，不能只在付费后的拒绝反馈里揭示合法称呼规则。
落点：`novel/stages/chain.py::_backfill_source_evidence_conflicts`／`run_chapters`、
`novel/stages/common.py::PRESERVE_RULES`；反例 `tests/test_str06_a3_conflicts.py`。

## 45. 事件来源与锁分开；恢复必须验证失败书和清账后的真实路径

beat.event 有真实行动而三类转移全空，并不意味着源观察失败。若保存协议只准引用
转移，模型只能借邻拍原子、漏掉行动或要求补造转移；增加 effort 和修订轮次不能
补上这个契约缺口。独立事件句柄只复制事件与正式证据，事件/细节可以合法入选，
状态与知情锁仍消费各自原子，邻句窗口不能变成支持新事实的逐字引文。

免费补丁预检先验证本 owner 候选，再报告尚未运行的下游，不能要求失败书已有
checkpoint/Design。升版修订的已购基线与当前应用请求分别留证；完整验收之后
只允许这对身份重放，任意新输入仍需显式裁决。回派清账不是新源材料，不能让
处理说明消失、改变已验请求并造成重购；返回同一基线请求也不能用原稿覆盖补丁。
验证须包含真实章请求范围、前置关联候选、Preserve 发布、无临时 wire 重放和
事件材料送达写手的完整链。工程通过与人工修订不等于模型首答或成书质量通过。
落点：`novel/stages/stage4/grounding.py`、`novel/stages/review.py`、
`novel/stages/revisions.py`；反例 `tests/test_str06_quality_materials.py`、`tests/test_str06_r9.py`。
