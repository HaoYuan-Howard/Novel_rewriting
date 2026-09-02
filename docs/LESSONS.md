# 工程经验与踩坑

本文只保留能够迁移到下一次重构的经验，不承担进度记录或需求排期。条目按编号维护，
每条都同时回答“结论是什么、失败会怎样、本仓用什么落地”。

## 1. 约束只有落到可执行守卫上才算存在

写在 prompt、设计稿、注释或评审结论里的“必须”没有天然遵守率。每条约束都应能回答：
谁产出、谁检查、失败如何显式呈现。机器无法可靠判断的语义，要明确归为人工判断或
不可验证；不能用一盏没有判别力的绿灯代替未知。

落点：`contracts/rules.py`、`contracts/judges/`、`contracts/schema/report.py`。

## 2. 绿灯必须携带扫描分母，并且至少见过一次红灯

空输入、漏接线和真正的零问题都能产出“命中 0 条”。报告必须给出扫描量、覆盖范围或
`unver` 原因；HARD 判据还要有最小反例走真实判据入口，证明它确实会红。只验证正常样本，
验证的是当前数据，不是判据能力。

落点：`tests/hard_cases.py`、`tests/test_hard_counterexamples.py`、
`tests/test_ingest_fixtures.py`。

## 3. 守卫的扫描面也需要守卫

参数表变空、正则失配、fixture 只在某台机器存在、模块缺失被 skip，都会让测试“全绿”但
实际什么也没查。扫描面应从现役注册表或闭合枚举派生，并对数量、非空性和正反方向设
自检；守卫覆盖面缩小时必须当场失败。

落点：`tests/test_coverage.py`、`tests/test_docs_references.py`、
`tests/test_layering.py`。

## 4. “不知道”“没运行”“缺失”“空结果”和“没问题”必须分开建模

缺上游、产物损坏、服务重启后的 running、判据够不到输入、合法空数组和真正通过不是同一
状态。不要用 `false`、`null` 或空数组把它们压成一个值；状态应进入 schema，并由 API、作业
记录和界面逐值保留。未知要倒向谨慎，而不是自动转成安全。

落点：`contracts/schema/report.py`、`contracts/schema/orchestration.py`、
`novel/orchestrator/state.py`。

## 5. 样本、证据基线和现役契约是三种东西

少量样本只能证明“曾经出现过这种形状”，不能证明字段必填、枚举闭合或当前实现仍如此。
交接包的 JSON 是历史证据，现役 fixture 用于回归当前实现，schema 才定义取值边界。三者要
分目录、分消费入口；不能用新实现的输出覆盖证据，再让实现与自己比较。

落点：`docs/BACKEND_CONTRACT.md` §八、`contracts/schema/`、`web/src/fixtures/`、
`web/src/mocks/`。

## 6. 原型是意图证据，不是生产实现的权威

高保真原型能证明信息层级、操作语义和视觉规则，却可能带着旧阶段名、前端自算命令、假路由
或冻结数据。落地时要按“当前产品裁决 → 后端契约与真实 API → 原型证据”的顺序消解冲突，
并把有意偏离写清楚；照搬原型代码并不等于忠于设计。

落点：`web/DELIVERY.md`（产品与界面规格的现役落点）、`contracts/schema/api.py`。

## 7. 单一落点必须覆盖最终消费结果，而不只是模板源码

枚举、规则、阈值和词表应只定义一次，但“只定义一次”仍不够。模板插值可能漏项，调用点可能
覆盖参数，适配器可能重新解释字段。测试要钉最终发出的 prompt、最终返回的 API 形状或最终
渲染所消费的结构，而不是只比较源模板。

落点：`contracts/invariants.py`、`novel/llm/prompts.py`、各 stage 的 prompt 回归测试。

## 8. 删字段前要审计语义消费，不只搜索字段名

字段可能通过整份字典迭代、模型复制、别名、派生函数或人工流程间接消费；零字面命中不等于
零消费。删除前要确认它是否可零成本重算、消费方需要的是字段还是其语义、历史产物如何读取，
以及失败成本由谁承担。仍有语义消费就不能以“清理”为名删除。

落点：`contracts/schema/__init__.py`、`contracts/invariants.py`、`tests/corpus.py`。

## 9. 加落盘闸门时，必须同批提供零成本恢复路

付费产出若被 HARD 拦下，只留下 `.INVALID` 而没有 `--revalidate` 或人工编辑入口，就会把一次
有效调用永久锁死。恢复路必须按当前判据重算派生结论，通过后再提升；不能直接把旧结论换个
文件名，也不能覆盖仍可用的正式稿。

落点：`novel/store.py` 以及 stage2、stage4、stage6、stage7 的提升路径。机械测量现已折叠进
Stage5/6 的即时消费，不写 artifact，因此不属于本条的现役提升面。

## 10. 生成、归一化、人工写回和提升必须共享同一把尺子

人工稿若绕过 schema、归一、判据、归档或 provenance，就形成一条比生成路径更宽松的暗门。
正确形态是：输入来源不同，判定与落盘口相同；修复失败保留问题稿，修复成功归档上一轮，并
如实记录 human/free/paid 与局部改写。

落点：`novel/store.py`、`novel/prov.py`、`docs/schema/_common.md`。

## 11. 来源账要能复现实质输入，而不只是写一个版本字符串

同一短 SHA 下的脏树、变过的 prompt、人工补丁和缓存命中都可能产出不同结果。provenance 至少
要让人追到代码、输入指纹、prompt 指纹、模型参数、修复动作和调用/缓存记账；否则“同版本”
只是看起来可复现。账写进产物后还必须在报告中有出口。

落点：`contracts/schema/common.py`、`novel/run.py`、`novel/llm/ledger.py`。

## 12. Markdown 的形状和引用同样是接口

当代码解析 Markdown 时，标题、表格列、编号和关键字就是语法；随手改成更自然的散文可能让
覆盖率静默归零。文档引用则应使用稳定编号或“仓内路径 + 符号”，不用行号；改名、拆号和搬家
必须让测试能发现悬空引用。

落点：`contracts/requirements.py`、`tests/test_coverage.py`、
`tests/test_docs_references.py`。

## 13. 前端只呈现判定，不补后端没有给出的结论

费用、写盘、新鲜度、严重度、通过状态和业务阈值一旦在前端重算，就出现第二个判官。服务端
没给结论时，界面应显示原始读数或 unknown；历史 mock 的兼容推导只能留在适配边界，不能进入
屏幕组件。

落点：`web/src/api/`、`web/src/constants/`、`contracts/schema/api.py`。

## 14. 类型同型只能证明适配器对齐，不能证明真实链路可用

TypeScript 可以约束 mock 与 real 实现满足同一接口，却不能证明 URL 已挂载、状态码语义正确、
写操作真正落盘或刷新后状态仍在。联调要建立“页面动作 → API 方法 → HTTP 路由 → 服务实现 →
最终产物”的闭环，并在关闭 mock 后逐项走真实请求。

落点：`web/src/types/api.ts`、`web/src/api/endpoints.ts`、`novel/api/routes/`。

## 15. 审校界面要保留证据关系，而不是只给汇总数字

对照任务的核心是“哪段对应哪段、哪里没有对齐、问题如何回到源材料”。主视图应并排呈现，
未对齐块要显式标注；状态同时用颜色、形状和文字表达。只给总分、tab 切换或一串绿色数量，
会把最需要人工判断的上下文关系抹掉。

落点：`web/DELIVERY.md` §3（路由与屏幕职责）与 §6（UI 硬规则）、
`web/src/components/compare/`。

## 16. 没有 ground truth 时，多一条语义判据可能更危险

语义探针在少量样本上命中“真问题”，不代表它稳定判断的是名称所说的概念。先标定命中、漏检
和跨书分布，再决定它应是 hard、info、metric、unver 还是人工裁决。无法建立可靠真值时，
诚实暴露边界比为了“更安全”强行设阈值更可靠。

落点：`contracts/rules.py`、各 stage 的 measure/checks 分层与语料回归。

## 17. 模型实验要先登记决策规则，再看结果

生成质量、审判能力、速度、价格和思考模式是不同维度。A/B 前先固定样本、提示、验收指标和
淘汰条件；生成模型与评审模型分开评价，质量门不过时不讨论便宜。否则观察到结果后再改规则，
很容易把一次偶然好看解释成模型胜出。

落点：`docs/BACKEND_CONTRACT.md` §十与语义审判的独立产物。

## 18. 付费与并发必须由可执行计划约束

花钱动作要由服务端给出完整 argv，在执行前一次性确认；未知成本不能伪造估价。批量任务应按
书隔离互斥，首个失败即停止后续扣费，并区分 done、failed、not_run。缓存命中也要单独记账，
不能把“没再次花钱”写成“没有执行过”。

落点：`novel/orchestrator/nodes.py`、`novel/orchestrator/jobs.py`、
`novel/orchestrator/queue.py`。

## 19. 功能完成要验证真实消费者与最终系统状态

函数存在、schema 能构造、单测通过都不等于用户路径完成。至少要验证真实书目录、真实服务、
真实浏览器或真实 CLI 消费了新能力，并检查最终产物、归档、来源账和重启后的状态。尤其要覆盖
失败、恢复和人工编辑路径，而不只走一次 happy path。

落点：`docs/BACKEND_CONTRACT.md` §十与真实产物账。

## 20. 运行规程要经无上下文盲驱，文档也要有生命周期

作者写的规程会隐含脑内知识；让只拿到文档入口的执行者从头跑一遍，才能暴露稿外步骤。但盲驱
反馈仍需对照文件和产物复核。活动待办、冻结契约、设计规格、历史证据和经验文档要分工明确：
完成项从活动清单移除，历史留给版本记录，避免旧状态继续以现役口吻指导开发。

落点：`docs/REQUIREMENTS.md`、`docs/BACKEND_CONTRACT.md`。

## 21. 守卫也会假红，而假红的措辞会把人指向错的地方

第 3 条说的是守卫假绿。反方向同样贵，而且更容易被当成真问题去改产物：**一条红说「产物有
问题」，实际是守卫自己漂了。** 实测过的两种形状：

- **守卫重拼了生产的实参**（判据的第二个落点）：判据加了参数，生产调用都跟着改了，测试台里
  那份没改 ⇒ 恒红，而真跑的 `--check-only` 全 PASS。同形的还有：拿裸常量比渲染后的 prompt、
  拿组装形态比落盘形态、拿裸子串查 JSON 载荷。
- **守卫断言了一道产品明说不存在的闸**：判据表里是 `info`（照样落盘、由下游在成品上判），
  测试却断言「一次都没越线」⇒ 它在产品**正常工作时**变红。这类红会诱人去重跑付费产物，
  而该改的是断言。⚠ 这一形有更严的一档：断言与一条 **HARD 方向相反**时，两者**永远不可能同绿**。
- **契约转向时，同一条 R 的守卫被改剩了一条**：`R-5.5` 把「输出写作单元的分组与顺序」交给 LLM
  之后，stage4 那条「重算分组必须逐组等于盘上」的语料对账就与 `R-5.6` 的 `structure_changed`
  方向相反（那条 HARD **要求**源单元 ≥4 的章不得仍按源序展开），恒红且红在产品**正常工作时**
  ——实测盘上三本 55/55 章全部命中。改判据的那次提交改了同文件里的其他测试，漏了它；
  同一次转向还漏了 `tests/test_stage4.py::_synthetic_response`（它按段序现编 `plan_beats`，
  即假定「写作单元数 == 源内容单元数」，而那正是被废除的恒等）。
  ⇒ 改契约方向时，把该条 R 的**全部**守卫（含 corpus 对账与测试台助手）一次列全再改。
  ⚠ 2026-08-19 把 `structure_changed` 的口径**转回来**（「必须移位」→「三根轴至少动一根」）时，
  那份「全部」是：`novel/stages/stage4/checks.py` · `contracts/judges/stage4.py` ·
  `contracts/schema/stage4.py` 的字段注释 · `BACKEND_CONTRACT.md` 三处（R-5.6 条文 / R-5.11b 的
  适用面 / §五覆盖率表的条数）· `docs/schema/stage4.md` 两处（条数 + 三行 sev 分类）·
  `tests/hard_cases.py` 的反例 · `tests/test_coverage.py::_R_SECTION_SIZES` ·
  `tests/test_stage4.py` 四处写死的条数——**一次列全了，没有第二轮补丁**。
  ⚠⚠ 顺带一条**新的**形状：那次转回来时，第一版设计把「标签方向与源序对不上」判成红，
  在盘上 26 个接缝里**误红 5 处**——因为 `transition` 说的是**故事时间**，而源叙述位置只在
  `present` 层内才是它的代理（那本书 39/106 个 beat 是 `recall`）。⇒ **新判据上线前，
  先把它在现役产物上跑一遍并逐条解释每一处命中**；解释不了的命中，多半是判据的可判定域没画对。

三条处置：守卫要**调用生产那一个落点**而不是重拼一份；断言要**照判据表的 `sev`** 写，
`info` 的正确守法是「越线必出声」而不是「不许越线」；给一条测试加**语料前提**之前，
先问它把「过期/缺失」当**输入**还是当**障碍**——当输入的那一条（它就是在测「非当前的上游要被拒」）
加了前提就是洗绿，那会让它永远跳过、永远不再红，而它看起来和「查过没事」一模一样。
前提只许是「产物相对上游是哪一版」，且必须由生产落点现算（`tests/corpus.py::requires_current`），
不许是任何判据结论、badge 或命中数。
分不清「语料过期」与「守卫漂了」时，**拿真 CLI 在语料副本上跑一次**就能定性；
一批红横跨多个文件时，**先按「文件 × 真实异常类型」取一份可复算的基线再动手**——
实测过一次代价：静态读代码把 `tests/test_wontfix_semantic.py` 那 11 条判成「沙箱缺料」，
而真实异常是 `UpstreamStale`（语料链条过期），两者的修法完全不同。

落点：`novel/store.py::_project_current`、`tests/corpus.py::requires_current`、
`tests/test_stage5_measure.py`。

## 22. 同一份数据的两种形态是一处静默失效点，而两侧的判据会一起变绿

一份数据在相邻两层各有一种形态（产物形态 / 中间形态）时，读取函数往往用一个「两种都试」的
分支兼容过去。**那条分支没有注释解释它为什么存在，就会在下一次「清理遗留兼容」时被删掉**
——尤其当中间形态的键名恰好和某个已废弃的旧契约同名，它看起来就是一段该删的兼容代码。

删掉之后不会抛错：读取函数**静默返回空值**，下游拿到空集合照常运行。真正致命的是随之而来的
第二重失效——**上下游的判据分母同时归零，于是它们一起报绿**：上游那条查产物（数据还在），
下游那条查函数（代码还在），中间这一步的丢失**不在任何一条的扫描面里**。本仓实测：闭卷写手
拿原文的唯一通道整整断掉一批书，五本共 898 条保真材料下发 0 条，三条 HARD 判据全绿，
唯一出声的那条度量还把根因说成了「这本书一条要害台词都没抽到」，把人指向了错的层。

三条处置：

- **不造第三种形态**。形态只留「产物形态」与「最终下发形态」，中间层原样搬运。
  需要兼容两种时，两个读取函数**分开命名**（同名不同义是漂移温床，参见 `plan_beat_id`
  的 `p` 前缀），不要靠一个函数里的 `or` 分支。
- **跨越一步的丢失，只能由横跨那一步的判据抓住**。上游查产物、下游查函数，都够不着中间。
  这类判据的形状是**比两边的条数**（不比文字——确定性改名会让文字合法地变化）。
- **绿灯必须带分母，分母为零落 `unver` 不落 `pass`**（第 2、4 条的合体）。
  「查过、干净」与「没东西可查」在报告上必须长得不一样。

落点：`novel/stages/stage4/beats.py::beat_index` 与 `::_hinge_text`（两段注释写明了为什么
不能加回那条分支）、判据 `contracts/judges/stage4.py::ctx_hinge_delivered`、
缝的测试 `tests/test_stage4_beats.py`（必须走 `beat_index` 而不是自己拼 hinge）。

## 23. 版本号是「破坏形状的计数器」，不是兼容层的入场券

一张 `migrations` 表看起来是在保护存量产物，实际保护的是**没人重建过的**旧产物。
本仓实测：8 个家族登记了 v2→v3 迁移，而删除那天盘上**一份低版本文件都没有**；
盘上唯二的 v2 文件恰好属于**刻意不登记迁移**的那一族 ⇒ 整条梯子零消费者，
却让「只接受注册表声明的当前版本」这句写在入口文档里的话整整假了一段时间
——实现走的是 `while version < current: migrate`，低版本有登记就放行。

判准是**谁来重建**：答得出「从 producer 重跑」就不该有迁移；答不出，那才是真兼容层，
而它必须在同一批里写下重建的成本与授权。破坏形状的正确做法是 `current_version` +1
**并同批回答那个问题**，不是加一条迁移把旧字节读成新形状——那不是迁移，是改写历史
（一份记着 `automated=true` 的旧 PASS 被「顺手丢掉三格」读成普通 UNVER，报告上看不出来）。

⚠ 同一次清理里撞出的另一个形状：**用模板路径写的守卫会静默变成空验证**。
`ARTIFACT_SPECS` 的键是 `stage5/{mode}/ch{nn}.json` 这样的模板，而路由把 `{n}` 编译成
`\d+` ⇒ 模板自己匹配不上任何路由 ⇒ 走「未登记路径原样返回」分支，一个异常都不抛。
拿它写的「拒读」用例会全程绿着，却一次闸都没查到。⇒ 参数化守卫的**占位符替换函数
自己也要有一条守卫**：它错了的后果不是红，是整批用例一起失去判别力。

落点：`contracts/schema/__init__.py::decode_artifact` 与模块头【版本】、
`tests/test_artifact_versions.py`（`_concrete` 与它的前提守卫、注册表全部产物 × 8 种非法标记）。

## 24. 用替身测「组装」时，「送达」就没有人测了

替身（fake spawn / 假客户端 / 内存桩）总是切在一处**进程或协议边界**上，而它接收的是内存
对象。于是边界**那一侧**的一切被测得很细，边界**本身**——对象怎么变成字节、字节怎么被对端
解析——整个落在观察面之外。危险在于这份细致会读作覆盖率：`tests/test_agent.py` 把启动 argv
的形态钉得很死（白名单形状、`//` 锚、`Write(` 规则不许出现、两项授权逐字进 prompt），
唯独没有人问过**这条 argv 能不能把 prompt 送到**，因为 fake spawn 拿到的是 Python list。

本仓实测：`build_argv` 经 `cmd /c` 拉 npm 的 `.cmd` shim，而 `cmd` 在参数的第一个换行处截断
⇒ 多行提示词只送达 **62/942 字**，agent 收到的只有首行「你在仓库 X 工作」，任务、sid、
两项授权声明、终审包落点全丢。**五本书同时如此，而五本都 exit 0。**

三重放大，每一重都比上一重贵：

- **失败方向是"做了别的事"而不是"没做"**。没收到任务的 agent 不会崩——它会礼貌地汇报盘面、
  请示下一步，然后正常退出。⇒ `exit 0` 在这里的真实含义只是「进程正常退出」。
- **静默沿着状态机一路变绿**：`exit_code == 0` → 事件流折成 `state='ok'` → 进度屏显示成功。
  盘上却一本书没动。这正是第 4 条要防的压缩，只是这次发生在**编排层**而不是判据层。
- **守卫把缺陷钉成了契约**：那句 `assert argv[:2] == ['cmd', '/c']` 不只是没抓到，它让
  **任何人修好这个 bug 测试都会红**。比没有守卫更坏——第 21 条说守卫会假红，这里是假红
  长在了一条真修复上。

三条处置：

- **替身的缝在哪，就在那条缝上补一条真的跨过去的用例**。跨进程的缝就真起一个进程——起
  探针不起真服务，成本仍是零（`test_launch_argv_delivers_the_whole_prompt` 起的是 python
  回显脚本，不是 `claude`）。
- **断言"形态"的守卫要配一条断言"送达"的守卫**：前者查你拼对了没有，后者查拼对的东西到没到。
  只有前者时，`R-11.8` 那种「展示与执行同构」只证明了两边**拼**得一样，没证明它**能用**。
- **替身模块的 docstring 要写明它看不见什么，而不只是不测什么**。原文写的是「不在这里测的：
  真拉起 `claude` —— 那要模型配额与真钱」，那是**成本边界**；被默认连带的「list → 命令行
  那一层也没测」是**能力边界**。两者写成一句，读的人会以为后者也已经权衡过。

⇒ 更普遍的推论：凡是把外部 agent / CLI 当黑盒调用的地方，「它到底做了没有」必须有一条
**盘上判据**，不能只看退出码。本仓是三样一起看：目标产物、`_service/ledger.jsonl`、
以及那份终审包在不在。

落点：`novel/orchestrator/agent.py::_claude_entry`（注释写明为什么绝不退回 `cmd /c`）、
`tests/test_agent.py::test_launch_argv_delivers_the_whole_prompt` 与
`::test_launch_argv_never_goes_through_a_shell`、`test_agent.py` 模块头的盲区说明。

## 25. 模型响应契约是可切换策略，不应与模型名永久绑定

同一份正式产物可以有多套**语义等价、成本曲线不同**的响应契约：一套把任务一次交给
能力足够的模型，调用少、速度快；另一套把困难拆成多相，用更多调用与 token 换稳定性。
当前某个模型必须靠后者过质量门，不等于前者已经过时。模型能力、供应价格和服务行为是
三根独立的轴：**模型能力增强**才可能让便宜契约追平质量；**单价下降**只改变成本权重，
不会自动提高质量；空响应率、缓存与并发变化又会单独改变实际墙钟和重采成本。

本仓的实测形状是 Stage2：同书同输入下，Flash 用一相 `extraction-ir-v1.1` 只有
91 拍 / 145 条保真材料；两相 `extraction-ir-v2` 提到 126 / 169，接近 Pro 一相的
130 / 168，但 Flash v2 的 completion 约为 Flash v1.1 的 1.99 倍。当时因此把
v1.1 保留为显式快速路径，没有因为 v2 翻默认就当场删除。2026-08-24 的后续产品
裁决又明确退役 v1.1，取值面收为 v2 / v3.1。这两次决定不矛盾：默认策略的翻转
不该**顺手**消灭可比较的备选，但产品所有者可以在明确承担失去快速路径的代价后
作出独立的退役决定；退役时历史证据保留，运行入口必须彻底闭合。

五条处置：

- **默认保留语义等价的 Pareto 契约，但只留一个显式默认。** 注册表保存可选取值，默认值单独落一格；
  旧契约不是隐式 fallback，也不按异常静默降级。正式产物记录实际 architecture，定向修复按
  基线产物选契约，避免一份书里混用两种响应语义。若后续有明确退役裁决，就让历史证据留档，
  同时从注册、配置、修复、工具与夹具一次性撤下运行入口。
- **不写死“模型名 → 契约”。** 同一个公开模型名背后的能力可能升级，硬编码会让旧裁决永久化；
  若供应商在同名下静默升级，反方向的自动能力探测又会让行为不可复现。选择应由显式配置和
  有日期的 A/B 裁决，或明确标注证据缺口的产品所有者裁决驱动；provenance 必须能还原当时用了哪一套。
- **切默认先过质量非劣，再比较钱与速度。** 冻结正文、全书底座、模型参数与 effort，拿同一模型
  分别跑两套契约；先看 HARD、信息覆盖、引用闭合、地点/秘密与下游成稿，再看调用、token、缓存
  和墙钟。至少在多本代表性小说上重复成立，才能把“这一本追平”升级成默认裁决。
- **Owner override 是产品裁决，不是实验 verdict。** 产品所有者可以明确承担未验证风险，取消尚未
  发出首个 wire 的 A/B，并把一条契约收成 sole active；此时实验状态必须记
  `CANCELLED_BEFORE_WIRE`，历史读数与未达成项原样保留，质量、token、wall、货币成本分别写
  `unver` / 未验证。不得把「决定采用」改写成 `GO`、质量非劣或降本提速已证；被退役入口要从
  注册、配置、恢复、工具与夹具完整关闭，不能留下静默 fallback。
- **价格变化只重算经济账，不重写质量结论。** 若能力不变、只是 token 变便宜，多相契约反而更
  容易承受；只有新模型在一相契约上也通过同一质量门，快速路径才重新成为优势。每次翻默认都
  保留旧契约作显式回退，除非另有一次明确的退役裁决；样本边界与未达成项始终写进证据账。

落点：`contracts/invariants.py::BLUEPRINT_ARCHITECTURES` 与 `::BLUEPRINT_ARCHITECTURE`、
`novel/stages/stage2/contract.py::CONTRACTS`、`novel/config.py::DEFAULT_STAGE2_BLUEPRINT_CONTRACT`；
付费 A/B 与后续退役裁决见 `docs/stage2_contract_experiment.md` §十四、§十九、§二十。

## 26. 外层进程成功与内层交付成功必须是两条状态轴

一个 agent、队列 worker 或脚本 exit 0，只能证明那个外层进程正常结束；它不能证明目标产物出现、
事实闸已开、终审包存在，更不能证明运行期间代码没有变化。把两者压成一个 `ok`，失败方向往往不是
“崩了”，而是“什么也没做却显示成功”，第 24 条的真实事故正是这种形状。

处置是让进程状态照退出码折叠，同时在结束后从**当前盘面**独立终验：启动/结束代码指纹一致、
强闸通过、交付物能按正式下载口重读、终审包存在、待办没有阻塞项。终验自己崩溃时仍要保留 ended
事件，并把交付状态记为 `unknown`，不能因观察器失败改写已经发生的进程事实。

落点：`contracts/schema/orchestration.py::AgentDeliveryOutcome`、
`novel/orchestrator/delivery.py::assess`、`novel/orchestrator/agent.py::fold`。

## 27. “是否还等人”与“是否挡交付”是正交维度

人工项不会因为已经裁决就从历史上消失：有些决定必须作为审计披露长期保留；反过来，有些尚待人工
阅读的语义项也不一定能据未经标定的模型结论阻断成书。只给一列 `pending`，会把“已看过但留账”误报
成未处理；只给一列 `blocking`，又会把“还需关注但不具自动否决权”藏掉。

因此人工清单至少要物化两轴：`attention = pending / reviewed_retained` 与
`delivery_effect = blocking / disclosure`。`reviewed_retained` 必须能举出 typed 决策（actor、reason、
主体 hash、状态与时间）；没有决策证据就继续写 pending，不能因为 agent 声称“看过了”而代造记录。

落点：`contracts/schema/api.py::InboxItem`、`novel/views/inbox.py::inbox_view`、
`novel/orchestrator/delivery.py::assess`。

## 28. 硬闸要落在所有调用方必经的最低边界，并与恢复材料同批交付

把 Stage2 事实复核写进某个前端按钮、某份 skill 或某个组合命令，只约束那个调用方；手敲 CLI、API
作业和下一版编排器都可能绕开。硬闸必须落在所有真实执行最终汇合的运行边界，并在组装与下载口再守
一次，已有旧文件不能充当通行证。同时不能把零成本复检、人工编辑和决策提交一起挡住，否则闸一关，
人连开闸所需的证据都拿不到。

与硬闸同批提供两类恢复面：只读 workpack 只缩小阅读范围、不自动裁决；模板、书内文件和 API 一次性
上传统一吃注册表版本化的同一 artifact 形状。路径不同不应制造第二份 schema，否则“前端能提交、CLI
读不回”的漂移只会在真实跑书时出现。

落点：`novel/cli/main.py::_cmd_run`、`novel/stages/stage2/global_review.py::require_approved`、
`::review_workpack`、`novel/store.py::ArtifactStore.read_external`、
`novel/stages/stage7/assemble.py`。

## 29. 会话边界也是证据契约，必须在首个动作前物化

同一个 sid 可以被续跑、重跑和复验多次；只按 sid 汇总 append-only 账本或信标，会把历史调用与
退出序列混进本轮。代码指纹若等到收尾才读取，起点与终点天然相等，即使中途换过代码也能伪装成
“同版完成”。这两种错误都不崩溃，只会生成一份数字齐全、却描述错对象的终审证据。

因此在首个动作前一次性固化 `started_at` 与 `start_code_version`，每个写盘/付费步骤前后核对后者；
账本和信标先按 sid，再按起始时间切出本次会话，同 sid 累计值另列且不相加。验收器必须要求调用方
交出起始指纹，不能用结束时当前值作默认。临时编辑只有恢复到原指纹才允许续跑；稳定落到新指纹
就是另一条 run。

落点：`.workflow/skills/novel-pipeline-operator/scripts/session_preflight.py::capture`、
`.workflow/skills/novel-pipeline-operator/scripts/run_step.py::_code_is_current`、
`.workflow/skills/novel-pipeline-operator/scripts/collect_final_evidence.py::collect`、
`novel/orchestrator/delivery.py::assess`。

## 30. 旁路决定账必须在每个读出口叠加，不能只在重新生成时烘焙

`wontfix`、人工 resolution 这类独立决定账通常刻意不重写原报告。若 producer 生成报告时读过一次账，
但 `check-only`、inbox 或 API 只读旧报告本体，那么“决定已落盘”与“系统仍显示 pending”会同时成立；
用户必须额外重跑一次无关的 producer 才能让决定生效。append-only 账是真的，读出口却活在旧快照里。

修复原则是在所有当前视图必经的最低读边界，以稳定身份键把最新旁路账叠加到报告投影；不删除原结论，
只更新 `actionable` 与审计标记。回归必须覆盖“生成报告 → 落决定账 → 立即 check/inbox”，不能只测
“再生成一份报告后能看见”，后者正好绕过了缺陷。

落点：`novel/stages/stage5/review.py::_report_of_review`、
`novel/wontfix.py::applied`、`tests/test_stage5.py::test_check_only_overlays_a_wontfix_written_after_the_latest_review`。

## 31. 测试临时目录要有固定身份，创建者退出前必须把 ACL 还给普通用户

Windows agent 沙箱可能给新目录写入只对沙箱 SID 有效的显式 ACL。目录创建者还活着时能够修复；
进程一旦退出，普通用户连枚举都做不到，只能 UAC `takeown`。用新的 `--basetemp` 名字绕过去会把
一次权限故障变成无限累积，还会让 Git/代码快照在扫描未忽略目录时失败。

因此 pytest 只复用 `pyproject.toml` 的 `.test_runtime` / `.test_cache`；路径预算不足时也只固定复用
一个仓外短根，不按批次或时间戳另起名字。正常退出由
`tests/conftest.py::_restore_windows_test_acls` 在创建者仍有权限时恢复继承和当前登录用户权限；
只有进程被强杀、收尾钩子未执行时，才需要管理员一次性接管并删除遗留目录。回归既要钉辅助函数，
也要真跑两次同一路径后从普通进程读取 ACL 和执行代码快照——“函数拼出了 icacls argv”不能证明
退出时序正确。

## 32. 顶层进程退出不等于子执行闭合，下一动作也不能靠执行者记忆

整书 Commander 同时有两处看似“已经决定”的静默缝。第一处是进程：外层 agent 或 shell exit 0 时，
它启动的节点子进程可能仍在后台写盘；此时立即验盘、记 ended 并释放书锁，会把半成品判成交付完成，
还允许下一条作业与迟到写入并发。第二处是调度：stage 顺序表只说明产物层，不能表达 queue 的恢复 flags、
人工闸与当前 `.INVALID`；让模型凭文档或聊天记忆挑下一节点，会在盘面变化后继续执行一条已经过期的命令。

处置要把两种“决定”都变成机器可复核的身份：下一动作从生产 queue planner 与同一次 snapshot 纯读产生，
显式区分 ready、blocked 与 pipeline complete；但 next 的 action hash 只描述“现在为何停/为何可跑”，
尤其 blocked 时不能充当恢复授权。真正执行前再把 exact node/flags 交给 production policy 铸 execution
permit，把 base、sid、代码、读面、贵贱/写盘效果与外部输入字节一起冻结；runner 与 child 都重算，root
写盘后的 canonical follow-check 则复用同一 permit/claim，只核自己的 step index 与 argv，不能被预期发生的
surface 变化误杀。

子执行不能只靠 beacon 扫描——“准备启动但尚未写 start”仍是一条空窗；父 run 要先取得由 OS
byte-lock 证明存活的 per-parent owner lease，再持久打开 admission
gate，每条 node 执行在任何副作用前与 close 共用同一事务口原子 claim，完整结束后 release。审批、
response-repair、终审与 telemetry 等非 node writer 也必须持 generic claim；已有 node claim 的 start/end
mark 复用它，不能在父 gate close 后为合法 end 信标另开一枚必然失败的新 claim。顶层 invocation 一返回
就 close 新 claim，再同时等 active claim 与唯一 execution id 的 start/end 两面归零；报警阈值不能拿来
释放书锁，越线后仍要等，最后也只能 unknown。gate 是 durable 账：服务重启后 unresolved 身份仍要阻断
同书 agent/job/trash/API writer，queue startup 也应整 lane 跳过，不能因内存线程消失就假定旧子进程也消失；显式 reconcile 必须绑定 admission
与完整 beacon 生命周期的 current subject，之后任何迟到事件都让旧 seal 失效并重新阻断，non-LF 坏尾也要
先原样隔离再 hash-bound 修复，不能猜最后一条事件；reconcile/坏尾 apply 全程还要持 owner-stopped
证明，force 也不能覆盖 live owner，残留锁文件不作存活事实。launcher 能关闭后台能力时同时关闭，作为纵深防线。
正常 exit 0 后还要重读 fresh delivery 与 operator next：仍有 ready 动作就是可审计的 early exit，不应暗中
追加一次新的模型 turn。操作规程的身份也必须覆盖入口、references、scripts 与 assets 整树，不能只哈希
一份会继续加载别处指令的 `SKILL.md`。

落点：`novel/orchestrator/operator.py::next_action` / `::permit`、
`.workflow/skills/novel-pipeline-operator/scripts/run_step.py::main`、
`novel/skill_contract.py::package_sha256`、`novel/orchestrator/admission.py`、
`novel/orchestrator/write_admission.py::operation_scope`、
`novel/orchestrator/agent.py::_wait_for_execution_closure`。

## 33. 模型轮次上限要数真实调用；最后一次返工必须把修复与自审绑成一个原子动作

“最多两轮”若只写进 prompt，确定性状态机仍可能把 typed loader 纠错、修后 pending 复审或新 surface
当成下一次合法 reviewer turn，结果就是同一业务主体连续三四次读整包。真正的预算边界必须在唯一模型
调用缝计数：只数实际 Commander invocation，0-wire review/edit 不占轮次；pause/resume 沿用同一
provider session 与计数，达到上限后返回稳定终态，任何纠错分支都不能旁路第三次调用。

若最后一次调用既要修复又要关闭人审，不能先落 edit、再期待一次复审。应让同一 typed 动作携带
hash-bound 修后自审：绑定修前 source 与完整阅读章集合，producer 在内存应用、归一和重新枚举后，只把
证明展开到**该 edit 新打开的 blocking 项**。未改变的旧 FAIL/UNVER 必须继承原红态，安全隔离也只能走
登记的完整事实组；任一 blocking 未闭合就整组拒绝写盘并停止。这样“两轮”不会以放宽审核为代价，
也不会让模型自述越过代码判据。

落点：`novel/orchestrator/agent.py::_start_planned`、
`novel/orchestrator/reviewer_tasks.py::_reviewed_global_edit_template`、
`novel/stages/stage2/extract.py::_review_after_global_edit`。

最后一次 typed edit 若被完整 producer 判据拦下，拒绝事实还必须成为**可重建的持久状态**，不能只放在
进程内 callback 或日志里。尤其当该 edit 同时改变了下游 freshness 时，普通 planner 很容易先看见 stale
下游并要求重做，把上游精确拒绝原因淹没。正确顺序是从 formal edit、current INVALID、revalidation
baseline 与原 review 重建窄 correction blocker；先消耗仅余的一次模型预算，再重新规划下游。服务/代码
身份变化可以禁止复用旧 provider session，但不能重置业务轮次预算。

补充落点：`novel/orchestrator/state.py::stage2_reviewed_edit_correction_needed`、
`novel/orchestrator/queue.py::WAITING_ON_STAGE2_EDIT_CORRECTION`、
`novel/stages/stage2/extract.py::global_edit_correction_workpack`。
