# STR-06 · R9 修订闭环、语义契约升版与 B6 分步验收（Stage6 退役）

本文件是唯一活动计划，登记进度、推进顺序、验收、恢复与删除条件。目标架构与字段
边界见 [str06.md](schema/str06.md)，现役行为以 [BACKEND_CONTRACT.md](BACKEND_CONTRACT.md)
及 `contracts/` 为准，开发遵守根及所属目录 [AGENTS.md](../AGENTS.md)。

**2026-09-30 重置**：吸收用户提供的仓外计划
`e-practice-group-novel-rewriting-outputs-glistening-clarke.md`，将 R9 插在 B6-A 二轮之前。
本次只重置活动账，R9 尚未实施；现役协议与冻结记录不因计划落盘而改变。
不恢复 PLAN.md 为第二份活动计划。已完成批次压缩为历史索引，完整交付记录按
`git log -- docs/REQUIREMENTS.md` 读取；旧计划里的运行命令与提交步骤不作为交付记录。

## 目标与范围

保留现有职责：Stage2 源语义，Stage4 目标故事、Body/Hook 呈现设计及确定性 WriterIR，
Stage5 逐章正文与单篇 Hook，Stage7 Naming、ApplyNames 与 Assembly。四类语义真源仍为
Characters／Blueprint／TargetDesign／SemanticBinding；NameMap、接受正文、回执与已购
证据按各自身份独立保护。节点表唯一见 `contracts/invariants.py::NODES`；
R9 扩展现有 book 调用，保持 stage0/1 ＋ 14 个 str06 节点，不新增表达抽取节点。
产品继续全自动单模式，Stage 页只读监控；R9c 核读、暂停与选章属于研发验收会话，
不增加生产人工闸、语义评审节点或文学评分闸。

本轮解决：校验过晚、停止空转、正向材料泄漏、修订后恢复不闭合，以及源表达、义务
边界和目标承接缺失。LLM 作语义决定；compiler 不补猜语义，validator 不修改输入；
新增硬闸同批提供免费重验、人工编辑或等价恢复入口（I-42）。

**关闭条件沿用 D1／D11**：新链产出《夫君的报恩》，工程验收与用户通读验收同时有效后
关闭 STR-06。main 已提前合入，不等于 B6 完成。工程缺陷修到通过；文学意见登记给 R7，
不阻塞迁移关闭。R9 明确要求的动机、行为主体、因果、时序、披露与叙述人称不得以
“文学优化留后”跳过；人工核读与机械结果分别报告，运行时语义状态仍为 `unver`。

产品范围仍为中文约 2 万字的短篇，永不做长篇／多线；不新增书长预检，沿用逐请求容量闸
（D7）。Stage0/1 未吸收的整层重构、独立 current-schema corpus 验收不并入 R9。
F3b 第二本 artifact-only smoke 已撤销；第二本表达力与 NameMap v5 跨书赋值随 B6c
Golden 验证，不重新加回迁移关闭条件。

## 当前状态与下一步

**已完成**：B0–B5 与收尾批；R0–R6、R5b、语义冻结闸及 R8。**未完成**：R9、
B6-A 二轮、B6-B、R7 与 B6c；R7 等待 B6 阅读问题输入。

- 现役语义冻结面仍是 book／chapters／names 的 `stage2-observation-v1`、
  `stage2-associate-v2`、Stage4 的 preserve-v1／design-v3／bind-v1／hook-design-v1，
  canonical 总版 `str06-rc10`；以 `tests/test_str06_f7e_freeze.py` 为机械依据。
- 提供的计划记录：20260930 canary 已完成 Stage2/4，生成 Hook 及第 1、5、10 章；
  首现人物误绑与 Design 读取定位曾靠手工 owner patch 过关，暴露 ID／自由文本
  不一致、修订证据不耐久及自动续跑缺口。本次未逐文件复核运行根，R9a 开工先核验。
- 20260929b 与 20260930 canary 只读保留作证据；旧根不得原地升版、手补新字段、
  清 ledger 或复制旧批准。R9c 使用独立新根，按实际请求身份核验历史采用。
- 最近已登记的分支同步回归：3409 项中 3408 通过、1 失败；唯一失败为 appendlog
  并发锁初始化 PermissionError，独立模块复跑 5 项全过。间歇性问题尚未根治，
  随 B6a 核查；不得将该轮写成全绿。前端 120 项及 typecheck／seams／build 已过。

**下一步从 R9a 开工；所有免费前置闭合后才报价 R9c。** 推进顺序如下：

| 顺序 | 批次与状态 | 依赖、交付与放行条件 |
|---|---|---|
| 1 | [ ] R9a 确定性止损与修订入口（免费） | 先最小反例与只读盘点，再统一计数、前移校验、停止防空转、泄漏终检，最后接耐久修订／重采／审阅工具；定向与产品链证据闭合 |
| 2 | [ ] R9b 语义契约一次升版（免费开发） | 接 R9a 的校验与恢复设施；集中完成表达、义务、承接、称谓释放、写作层及全消费链；吸收 R8b 的 JSON Schema 生成待办 |
| 3 | [ ] B6a／R9 工程收口（免费） | R9a＋R9b 最终形状稳定后，契约五文件、真实 fake 产品链、默认非 corpus 回归及适用前端验证；冻结最终代码／协议／配置 |
| 4 | [ ] R9c＝B6-A 二轮（分段付费） | 报价与授权后，先语义层到 compile 并核读，再 Hook＋第 1 章，再按实际承载位置选问题章；每段通过才进入下一段 |
| 5 | [ ] B6-B 全书与用户验收（付费） | 沿用 R9c 同根及有效购买／章回执，只补剩余正文与交付；工程核查及九维通读，用户结论绑定确切成书版本 |
| 6 | [ ] STR-06 关闭与退役尾项 | B6a＋B6-B 同时有效；按删除条件清理存量解码面，若改可执行面按最终集合补验，完成关闭记录 |
| 7 | [ ] R7 数据驱动优化 | 根据 B6 阅读问题及后续优化表另行立项；质量、输入精度、过度失效和付费效率按实证推进 |
| 8 | [ ] B6c 两本短篇 Golden | R7 后先更新六维评价、冻结样本与 baseline，再执行两本质量评价；第三人称问题另有对应书或免费反例证据 |

### 节省时间的执行约定

1. **按共用路径合批**：计数／校验／错误形状先做；称谓推导与实际视图检查只维护
   一套；patch、resample 与审阅工具共用差异／验证／修订级联，不各造一条恢复路径。
2. **先安排反例，再补实现**：复用最小合成样例，运行根离线回放只作补充证据，
   不将付费回答或正文变成普通测试 fixture；不为复现已知问题再次购买。
3. **可同步准备，不交叉改同一真源**：appendlog 定位、测试影响盘点、Golden 评价资料
   可与免费实现同步准备；涉及共同 schema、common、chain、冻结记录的改动按依赖落地。
   不同时启动共享 basetemp／cache 的 pytest。
4. **集中升版与冻结**：R9b 一批闭合 wire、canonical、prompt、消费者、恢复与守卫；
   R8b 的 Schema 生成一起做，避免新 canary 后再改形状说明。R9a 可交付检查点，
   不单为检查点、提交或测试登记重复完整回归。
5. **每个子批定向，最终集合统一回归**：契约形状稳定后跑 §9.5 五文件；
   合入 main／冻结付费基线前命中根指南门槛，按最终集合做一次默认非 corpus 回归。
   此后若再改生产代码、可执行契约或测试基础设施，按新最终集合重新补齐验收，
   不以“一次”免除新变更的义务。
6. **付费按问题覆盖扩展**：先核语义，再验证最小写作样本；选章按新 Design 中的实际
   承载位置，不固定照抄旧 1／5／10 或 3／5／7／9。通过的请求与章稿继续采用，
   不为凑一次完整运行整书重新购买；编排器不自动生成 resample。

## R9a · 确定性止损与修订入口（免费）

本批 fake transport、零真实付费，不改语义层协议字面量。涉及修订／采样记录的新形状
须显式版本化；冻结表的变化与 R9b 最终集合同批核对，不暗增 operator_redo 策略。

**开工顺序**：先核验旧 canary 的请求、产物与 ledger 身份并冻结只读证据，建立七类
反例；再完成 1～3 的基础校验和停止语义，接 4 的材料终检，最后做 5～7 的修订闭环。

1. **统一字数口径**
   - 非空白字符计数唯一实现放在 `novel/core/text.py`；
     `novel/stages/stage5/body_checks.py` 的正文计数委托它。
   - 总预算派生、Hook 长度检查／attempts／报告、packet_metrics、Stage7 assembly
     全部使用该实现；Hook prompt 注明口径。
   - 预算只含章正文，不含系统添加的章号标题与 Hook；Hook 另有目标，使用同一函数
     计数。保留最大余数分配，边界同步 `docs/schema/str06.md`。
   - 本书 10000→9000 是计划提供的口径测量，开工免费复核；预算进入 Design
     实际请求身份，与 R9b 后的重购一起处理。
2. **校验前移并共用**
   - 从 `novel/stages/stage4/compile.py` 抽出章闭包检查，
     Design 接受与 execution plan 编译共用 fold／scene_read_views；
     未建立或 unknown 状态的消费在 Design 接受阶段交既有 ≤2 轮修订处理。
   - 首现检查改为“人物最早锚点章＋单章拍”输入；章观察接受回调用同一
     character_core_view 派生锚点，修订需同时重写人物 ID 与事件文本。
     Stage2 compile 保留同一道兜底，不维护第二套规则。
3. **停止语义与防空转**
   - ask_json 的无进展／轮次耗尽改为显式 ValidationStop（ExecutionError）：
     code 为 `validation_no_progress`／`validation_rounds_exhausted`，
     item 为单元 scope，context 保留轮数及按位置聚合的问题。
   - MechanicalDefectStop、OwnerRevisionStop 同样携带 stage／node／item／context，
     由 _record_failure 写 workflow_status；API 与监控页消费唯一错误投影（I-36）。
   - CLI 按类型将 ValidationStop 映射为 exit 1；真正程序错误保持 exit 70，
     不用统一 except ValueError 掩盖。
   - 先用产品链复现 compile 停止是否被原样重派；若 landed exit 1 后重规划仍是
     同一 action_sha256 且盘面坐标未变，按无进展停给人，引用 workflow_status 的
     code／node／item。已有正确防护则只补反例。
4. **实际章包与 Hook 终检**
   - 一套检查检查正向分区的未释放专名字面及内部 ID／slug；负向禁区、明确禁止、
     Hook 禁止揭示不纳入正向检查。复用 claim_spans／uncovered_occurrences，
     先认领已释放全称，避免单字禁用误伤。
   - renderer 删除 char_id 兜底；声线只送本章有释放面的人物，承接里的在场人物
     无可用显示面时不列出。不得用猜测的称谓补齐。
   - Hook withheld 与章共用推导，包含家族构词；R9b 再统一实现合法释放闭集。
   - Design 自有文本按自身释放表逐章检查，Hook Design 按 Hook 可用称谓检查，
     问题交所属 owner 修订；compile 对实际渲染的全部章视图和 Hook 视图终检，
     命中即 typed 停止，不购买写手。validator 只报告，不改语义文本。
   - R9a 单独落地时旧盘面因家族构词被终检拒绝属于预期；须等 R9b 闭合集后再放行，
     不关闭闸或单独花钱“试是否还能通过”。
5. **owner patch 预检、采用与恢复**
   - 预检只读：核验当前 owner 请求身份的已购基线（默认本根），输出 ID／自由文本
     两列差异；执行 owner 校验与下游确定性干跑；列出被替换人物仍在自由文本中的
     残留指称位置，只报告、不代改。
   - 采用经 repository 将补丁原字节按摘要耐久保存到候选根；修订记录绑定基线身份、
     差异摘要、意图、product_rel／product_sha256、open／resolved 状态，
     不继续指向根外临时文件。
   - 与 R8a 回派共用修订级联：仅 open 记录与当前 owner 产物摘要一致时，
     下游 consumed_changed／file_changed 可作为可信级联；已登记 canonical／
     owner_revision 策略重放，免费编译与同请求缓存自动走。
   - 新购买仍受原许可与额度限制，不自动追加额度；最近的确定性复验节点
     （Stage2 compile／Stage4 compile／Stage7 assemble）成功后清为 resolved。
   - 无记录改动、摘要不符、修订后再次改动照旧停给人。book／chapters／names
     没有 operator_redo 策略，修改它们仍需显式手工逐节点重放，本批不扩张登记。
6. **显式定向重采**
   - 研发手动入口 `--resample <owner> --reason …`；基线请求身份之上绑定采样序号，
     普通重跑仍命中原缓存，编排器永不自行发起。
   - 原回答保留，新样本计入工作单元与整书总闸；记录基线、序号及理由，
     基线身份改变时旧序号不沿用。下游复用第 5 条级联及其策略限制。
7. **只读审阅工具**
   - 拟新增 `str06_review.py`（放在现有 `tools/`）：views、obligations、
     patch-preview 三个子命令，不形成另一套领域模型或业务判定。
   - views 调用实际请求相同的渲染函数并附终检；obligations 并列义务、承载拍、
     brief、表达提示及未入选／跨章材料；patch-preview 复用第 5 条。
   - 文本、原始证据与详细差异只写运行根，不进仓库。

**验收**：七类最小红例（计数不一、读取差一拍、人物首现、验证耗尽 exit 70、
exit 1 原样重派、称谓／slug 泄漏、patch 后 manual-decision）及绿对照同批。
绿对照包括全称内单字、负向指令、合法 patch 级联与无记录改动仍停。
补基线漂移、采样额度／身份、补丁证据耐久与重启恢复。
真实子进程产品链验证已登记 owner patch 后 AgentRun 续跑到交付、二次代跑零新购；
未登记策略的源观察 owner 保留显式手工路径，不以测试伪装为自动闭合。
旧 canary 离线回放分别核对读取定位、首现与 11 章／Hook 泄漏分布，独立报告。

## R9b · 语义契约一次升版（免费开发）

**计划给出的裁决约束（2026-09-30，D8 修订二）**：书级观察及 Stage4 规则／wire
集中升版；扩展现有 book 调用产出源表达；默认保留表达机制，每项显式 keep／change，
change 带理由。本书验收目标为第一人称、对外守礼与对内直白的反差。
R9b 开工将该裁决来源登记到 `docs/DECISIONS.md`，同批重钉冻结记录；
本次文档重置不代表运行时协议已经升版，也不代表付费／正文外发授权。

1. **源表达送达**
   - BookObservation 增 expression：叙述人称、叙述者、视角权限、主导幽默／反差、
     重要内外反差，逐项带 source_refs。book 观察协议独立升版，chapters／names
     协议不动，associate 不扩张职责。
   - Stage2 compiler 产 source_expression 并派发 ex_nn、解析证据；确定性段长、
     句长、对白占比只作读数，不作质量闸。
   - ReconstructionBrief 投入表达陈述与读数，不送证据原文或源章结构，
     Design 继续源盲。
   - Design v4 的 narration（person／focalizers）复用 HookPov 形状；
     expression_decisions 对每个 ex 恰好一条 keep／change，change 带理由及目标说法。
   - 场景 POV 服从 narration：first 为唯一叙述者，third 为 focalizers 或 narrator。
     BookBrief／IR 正式送达；人称进“不可违背”，明确对白自称不改变叙述人称，
     表达机制进“导演说明”。
2. **义务传递与 Preserve policy v4**
   - satisfaction 写正文必须成立的边界，包括不足以兑现及禁止改法；
     救人动机等反例在核读中检查 statement→satisfaction→Design→正文是否保持。
   - 不同时点的决定保留条件与先后；跨场复合义务拆为可在单处兑现的阶段义务，
     用 requires 表明因果／信息前提，不把单纯先后写成因果。
   - 选择后回看未入选铺垫、重复与兑现是否承担必要条件；身份义务不捆绑
     首次出现时点不同的称谓。
3. **Design 规则与统一形状源**
   - 默认保留源表达，改变显式说明；brief 指定信息、反差与结束效果，
     微动作只在承担关键事实／伏笔时写，不转述改变义务因果。
   - 正向材料只使用对应位置已释放称谓；释放闭集扩为本人登记面与家族构词，
     家族称呼随成员合法释放，Stage7 依 NameMap 展开并覆盖实际消费测试。
   - 义务／设计冲突由 Design 修；写手“事实优先”只作兜底，核读②拒绝把
     规划缺口继续交写手解决。
   - **吸收 R8b 留待项**：Design prompt 形状段从模型 JSON Schema 生成；
     typed 校验与下发形状同源，完整分支约束与动态闭集同批对齐，
     不再独立维护手写字段形状段。
4. **可定位承接**
   - ChapterEntry 增较早事件拍的 anchor，transition 增 flashback。
     compiler 查引用存在、早于本章；immediate_continuation 的锚为上一章末拍。
     按锚渲染措辞；自由文本是否遵循交核读。
   - anchor 是可选定位材料，衔接规则由显式 transition 驱动；
     缺失值不得暗示另一种业务模式（I-14），各分支必需条件同批登记、关闭式校验。
5. **Hook 设计与写作层 v6**
   - Hook brief 明确入口知识、跳时定位及末尾悬念；POV 使用 person／focalizers，
     不强制与正文相同，其合理性及事实／披露一致性交核读。
   - 写手可补措辞、对白、即时反应与局部动作；不得新增改变动机、行为主体、
     关系、时序、知情条件或后续前提的事实。用“删去后是否改变为什么发生、
     谁做了、前史是什么的理解”帮助判定。
   - IR 给本章人物可用显示名，renderer 彻底消除 slug 兜底；新增人称约束，
     同步协议／renderer v6 与 Hook 实际材料。
6. **完整升版与闭合**
   - book 协议、Blueprint／Brief／BodyDesign／IR 的 artifact 版、design-v4、
     STR06_RC_VERSION、Preserve policy、renderer／写手 v6 同批登记；
     具体产物版号按最终改动面分配，不预填历史语义默认值。
   - 更新字段送达表 `contracts/schema/consumption.py`、冻结钉及实际契约身份，
     producer／compiler／consumer／恢复／API/UI／测试／文档一次闭合。
   - 同步 `docs/schema/str06.md`、`docs/BACKEND_CONTRACT.md` 的字段消费与判据；
     旧回答、产物、ledger 只读保留，旧版缺表达或新字段时拒识，不静默迁移。
     协议未变不等于请求身份未变，历史采用按实际投影逐项判断。

**验收**：wire→canonical，新增字段全送达且变异只影响登记范围／分区；人称与 POV、
表达决定覆盖、家族释放、承接锚红例与绿对照。第三人称对白的“我”和合法倒叙须通过。
契约形状稳定后跑 §9.5 五文件，fake 全链到最终交付、旧版拒读及可用证据免费恢复；
既有职责反例与分母保留，不因升版重新耦合退役实现。

## B6a · R9 工程收口与付费基线冻结（免费）

R9a＋R9b 稳定后，按最终改动集合收口，而不是每个子项完成即全仓复跑。

- unit／contract 与受影响 stage、调度恢复、API/前端消费闭合；选测目录与实际导入
  消费者取并集。§9.5 五文件为 test_docs_references、test_coverage、
  test_rules_registry、test_hard_counterexamples、test_ingest_fixtures。
- 真实 HTTP 上传→Supervisor→run_step／CLI 子进程→交付→重启读回，
  覆盖 typed 停止、无进展、防空转、可信修订级联、零新购重放与发布证据缺失拒读。
- 合入／冻结最终付费基线前跑默认非 corpus 回归；核查 appendlog 间歇性问题，
  若再现则保留该轮失败并定位修复，不删反例、不放宽断言、不以单点绿代签完整回归。
  API 形状／前端 fixture／页面受影响时补 test／typecheck／seams／build。
- 冻结代码 commit、dirty tree、协议、配置、模型参数、输入身份及新输出根；
  测试与源码临时文件不在 canary 中途增删，避免 code_change 导致 epoch 空滚。
  新的可执行改动出现后旧基线证据不冒充最终基线。
- 后端使用 novel_refactor，pytest 沙箱外执行，固定复用 .tr／.test_cache 或
  E:/_tr，不并行共享；具体命令以 `tests/AGENTS.md` 与
  `docs/OPERATIONS.md` 为准。工程回归与 Golden 分别报告。

## B6 执行与后续优化次序

### R9c · B6-A 二轮（分段付费）

1. **报价与输入冻结**：新独立输出根，经真实产品入口运行；历史根只通过
   `--adopt-root` 提供身份一致且证据可核验的回答。免费盘点后报价，付费与正文外发
   分别授权，manifest 写运行根；次数与额度不得作为已确认费用。
   语料只经 NOVEL_DATA／NOVEL_CORPUS 指向 Workbench，NOVEL_OUTPUT 为独立根。
2. **核读①Preserve、②Design**：先跑到 stage4 compile，
   `pause_before_node str06.stage5.hook`；核对义务边界、阶段承载、表达决定、
   动机／主体／时序／知情／披露、承接及实际材料泄漏。
   未通过回所属 owner 修订，按 R9a 可信级联免费重编译／缓存重放后复核；
   新购买依原授权与额度，不自动增加额度。通过才放行写手。
3. **核读③Hook＋第 1 章**：研发选章入口 `--body-chapters 1`，
   核对救人动机、创作边界、第一人称与内外反差、Hook 入口和悬念。
   称谓／slug 引起的首稿拒绝应为零；发现材料泄漏先修材料，不让写手重复付费兜底。
4. **按问题承载扩展选章**：建立“问题→修复位置→验收文本”表，以新 Design 的
   实际章／拍位置覆盖画像心动与反差、宝典作用链、最后再试一次的条件与顺序、
   表白到房梁现场的时间／在场关系、ob10 的全部阶段。
   旧章号只作定位线索；取覆盖这些问题所需的最小完整章集合，保留已通过样本。
5. **报告与冻结写作层**：每条问题记录 owner、位置、期望、实际及证据，全部存运行根。
   分别报告机械约束、语义核读（通过／未通过／未完成）、
   产物身份与依赖（当前／失效）；canary 收敛后冻结写作层。
   成本分缓存命中行、成功非缓存尝试、连接失败尝试，另保留 unknown／reasoning／
   429／重试与全部失败分母；费用以 ledger 为权威，未知不作零。
   本书为第一人称，不用它证明第三人称串人称已修复；后者由免费反例与后续对应书验证。

### B6-B · 全书与用户验收

R9c 通过后沿用同一根与有效章提交，解除研发暂停／选章边界，用真实全书入口只补剩余章、
Naming 与 Assembly；续跑为 continuation 新 run，沿用累计工作单元和整书额度。

开发 agent 先核验章连续非空、不增删／合并／拆分／重排，回执与 seal、改名零残留、
manifest/hash 与下载字节、Stage 页／最终盘面、零新购重放、服务重启读回及 ledger，
再通读全书与 Hook，按协议提交当前版本与问题清单。源书与可核验基线作对照；
F5e 原始根已清理，只能使用历史读数，不能宣称完成不存在正文的逐段对读。

**人工阅读验收协议（R4 冻结；B6-A／B6-B 共用，全文唯一登记面）**

九维不打分，沿用冻结的问题：

1. 情节与因果是否完整；
2. 揭示时点是否正确；
3. 章间衔接是否自然；
4. 人物声音是否稳定；
5. 写手是否把 IR 写成了说明书；
6. 信息有没有重复交代；
7. 场景是否通过行动与对话展开；
8. Hook 是否有吸引力而不提前兑现；
9. 整体节奏与流畅度是否优于基线。

记录“章与段位置→维度→归因→处理”；归因仍为 Stage2 抽取／Preserve／Design／Bind／
编译投影／renderer／写手协议／写手发挥；处理为 canary 收敛或 R7。
R9c 核读在同一记录里补 owner、期望、实际和证据，不另立第二份维度表。
正文、阅读记录与补丁详细差异存运行根，不在仓库摘录。未评或证据不足保持未验证，
回执与 Guard 不代签语义或文学质量。

用户通读结论绑定确切成书版本／hash；正文改变时重验受影响结论，字节未变可沿用结论并
复核身份。工程问题修到通过；文学意见登记为 R7 输入。B6a＋B6-B 同时有效后按下方条件
关闭 STR-06，之后再进行 R7 与 B6c，不以已合入 main 代替验收。

### B6 沿用的保真与交付反例

F9 的 H1–H4 仍有效：Preserve 保留作用而非整场转写；Design 章目的与分场一致且
不改写人物；Hook presentation 不漂移；Stage2 补抽取缺口并正确分流引子。
下列原有阅读清单继续与 R9c 的新增问题共同核查，不因活动账重置缩减验收分母。
源章定位不变，目标承载按新 Design 查询，不要求源／目标同号章对应。

- **Stage2**：女主自我标榜按说辞记录且反差两面齐备；离开不外推成送离住处；
  不同次藏身用不同 knowledge_key；源第 11 章快照无并存双人选键、无尚未决定
  残留，孕期状态已解除。
- **引子分流**：本书第 1 章有 e00 开场拍且不重复正文，初态不含由开场拍首次产生
  的知情；《三人成行》两臂基本等价随 B6c 检验；合成样本概括段零引用。
- **Preserve**：口头守礼与内心清醒反差、失言到自曝到怀疑的连接、第二夜误会链、
  首夜床下确认作为 reveal；离家半月与摊牌受阻不挂伪因果；不整场转写，
  must_preserve 有真实 false，主线无漏保。
- **Design**：11 章 purpose 与 scene_ids 一致；人物与声线不被同质化；床下确认拍
  有 reader delta 且此前线索仍在；称谓仅用登记的合法释放面。
- **Hook 与交付**：presentation 对照引用拍；新婚夜、死结与剪子、反对换人失言、
  结尾四处成立；床下藏身在确认拍前不作已确认叙述；不擅添改变正式计划时间线、
  身份或前史的新设定；Hook 与正文一致，新名全书一致、混合名零残留、章号齐全。

各 owner 首次输出成功率仅作单列读数，保留全部失败／修订分母；v13 的零成本对照
按 DECISIONS 历史登记核验，缺原始证据时不代签质量。

### 后续优化次序

| 顺序 | 优化对象 | 证据与边界 |
|---|---|---|
| R7 质量优先 | 漏保／过保、源结构泄漏、因果、承接、声线、导演说明 | 按 B6 章／拍反例定位所属 owner；先证明章包语义充分，再改 prompt，不让 Writer 补缺失规划 |
| R7 输入精度 | 无关声线、重复约束／状态、冗长协议 | 统计实际 renderer 字节与真实 token，依显式依赖裁剪；不截断或概括必要事实，收窄正式书级材料同批改契约 |
| R7 付费效率 | 局部失败、复用、重试、并发与过度失效 | 先核请求身份、采用命中、429／unknown、活跃时间与成本分母，再调槽；并发不进语义身份，保留 D6 上限与截断即停 |
| B6c 质量回归 | 两本短篇六维 Golden | 先更新退役 rubric 引用并冻结 manifest／baseline／成本；人工为主，模型辅助另授权；只改评价过程时重评现有成书，不自动重生成 |

## 恢复、替代与删除条件

| 对象 | 有效义务与删除条件 |
|---|---|
| 旧 canary 与已购证据 | R9a 只读核验；保留原字节、回答与 ledger。R9c 新根独立运行，仅同身份且证据可核验的回答采用；协议／实际输入变化即拒用，不隐式迁移 |
| 可信 owner 修订 | 本根耐久证据＋基线／当前产物摘要闭合后才级联；最近确定性复验成功才 resolved；无记录改动仍人工处理，不新增 book／chapters／names 的 operator_redo |
| 章集合恢复 | 从有效 committed set 与当前章包身份只补缺章；输入未变购买继续采用，扫描全书不等于付费重跑全书；换 NameMap 只影响交付，Hook 窄改不使无关正文失效 |
| Stage1 hook_copy | e00 章前证据仍分流至 Stage2 book 与第 1 章观察，与 main_text 构成组合源身份；只改引子拒复用旧购买，不恢复废弃中间产物 |
| 篇幅与预算 | 正式 MainText 派生总预算、源章数固定目标章数；篇幅带／Hook 目标有正式配置来源；R9a 统一计数，配置变化进入实际消费身份 |
| 存量解码面 | RETIRED_NODES／reviewer legacy／workflow_transition 在 STR-06 关闭时删除；先迁有效独有反例，再删解码面，不能破坏现役恢复或历史拒识 |
| 退役台账 | `contracts/schema/retirement.py` 保留至关闭，现役拒识与独有反例先接管；按家族与 `tests/test_str06_f7e_retirement.py` 双向对账 |

历史响应先耐久再校验；pending／unknown 不自动重购。旧根的原始回答、checkpoint、
接受正文与 ledger 不因 derived 重算而删。修订基线与稳定 ID、无关 canonical 内容
必须可核验；派生物只重算受影响范围，不以保护已购结果为由忽略真源写入保护。
`--check-only` 表示零新购复验，可执行恢复写入，不称纯只读。

### 候选根运行记录映射与删除条件（B0 登记，B1 执行）

本表记录 B1 已完成的迁移：候选根 request、admission、validation、adopt 逐项映射到
SemanticRequest／ContentRevision／ProviderAttempt／AcceptanceRecord／CommitBundle，
契约锚点见 str06.md。该次切换改变了旧请求身份；R9 的历史采用仍按实际请求身份核验，
不据此推断所有缓存均可用或均失效，也不迁移旧根字节。

| 候选根记录（迁移前） | 内容 | 映射目标 | 处置与删除条件（B1） |
|---|---|---|---|
| `runtime/request_<身份>.json` | 冻结请求＋原响应＋usage，按请求身份寻址（已购恢复证据） | SemanticRequest（业务任务、输入投影与语义协议）＋ProviderAttempt（冻结配置、准确请求、原响应、成本；**原响应按 attempt 存储并被 attempt 引用，空回答不再占据请求身份**） | 新链改由 attempt 引用的耐久响应存储后停止写旧形状；「请求与响应一体、按请求身份整存整取」的旧布局退役；历史文件继续作为 F7a 历史采用的只读证据源，不删除 |
| `runtime/validation_<身份>.json` | 单请求的解析/编译/校验结果 | AcceptanceRecord（解析、救回、引用解析、编译、机械验证分项结果；语义恒 `unver`） | AcceptanceRecord 落地后停止逐请求写 validation 文件；旧文件随旧根只读保留 |
| `runtime/admission.json`（含 `commander_admissions.jsonl` 命名的历史账） | 整书调用准入平行计数（只计成功应答、不计 unknown） | 删除，不映射——与「unknown 不释放额度」不符；额度唯一由工作单元内容轮＋整书总闸（外发前按 attempt 计数、unknown 也计入）＋ledger 承担 | B1 额度账切换并验证「重启不重置、unknown 占额」后删除写路与读取；文件随旧根只读保留 |
| `runtime/adopt_<owner>.json` | 历史采用与 owner patch 记录（cost_link 三档、patch note 基线身份） | ContentRevision（首次生成或修订；owner patch 是无外发的修订，绑定基线请求身份与修订说明）＋CommitBundle 的采用证据（采用状态从 seal 派生） | CommitBundle 落地后停止写独立 adopt 文件；F-4 的 allow_patch 建档语义迁入修订记录；旧文件随旧根只读保留 |
| `runtime/body_attempts_chNN.json`、`runtime/hook_attempts.json` | 同输入有限重写的候选尝试与选择 | ContentRevision（内容轮额度沿用）＋ProviderAttempt（每轮至多 `contracts/invariants.py::WORK_UNIT_MAX_DELIVERY_ATTEMPTS`＝4 次） | B4 回执改绑时随章回执迁移到新记录；旧文件随旧根只读保留 |
| ——（无旧对应物） | —— | CommitBundle：输入清单、采用证据、输出摘要与 seal | B1 新增；正式提交记录，无删除条件 |

本表保留 B1 迁移语义供现役文档引用，不重新启用旧布局或双写。已清理的历史根不可恢复；
仍存在的文件保持只读。R8 的 work-unit 耐久证据绑定不补造 runtime/request_*，
R9 修订／采样记录沿正式证据链版本化，不成为新的业务真源。

## 全链验收与关闭条件

免费工程条件：四类 truth、唯一 writer、单向依赖、实际语义送达与正式消费链闭合；
Preserve／Bind 隔离；typed 停止及有限修订不空转；可信 patch／采样身份与恢复可重建；
未发布、证据缺失或未知不能伪装 ready；请求身份未变的有效购买可免费采用。

付费／阅读条件：R9c 核读问题已收敛、B6-B 全书交付与用户通读有效，工程缺陷清零，
文学意见完整登记。Guard／回执只证明机械范围，运行时语义保持 unver；
B6a 工程通过、canary 开发性阅读与用户验收三者不互相代替。

关闭时完成存量解码与退役尾项及适用回归。若关闭前新增生产代码、契约或测试基础设施
变化，B6a 按新最终集合复验；main 提前合入仅改变合入时点（D11）。
第二本表达力、NameMap v5 跨书赋值与质量普适性在 B6c 单独报告，长篇无待验项。

## 成本与授权

R9a／R9b／B6a 开发与回归全部 fake transport、零真实模型调用；
R9c／B6-B 开跑前按实际章数、当前配置、可采用回答、重试规则与剩余额度会话报价，
付费与正文外发分别授权。重置文档、登记“已裁决”与免费回放不扩张任何付费授权。

完整无修订新链基础调用为 2N＋9（11 章约 31 次），只是工作量参照，不是费用或新购
上限；内容修订、provider attempt、网络失败、unknown 与历史采用分别记账。
同根重启／continuation 不重置额度，付费显式 `--yes`，增加额度走原正式入口，
截断 `finish_reason=length` 仍停在 item，不自动重试或降 effort 掩盖设计问题（D6）。

成本唯一权威为 <base>/_service/ledger.jsonl；费用未知保持未知，reasoning 不重复计入
completion。R9b 改变语义层与消费输入，Stage2/4 旧版产物不得整套采用；即使
chapters／names 的 wire 字面量未改，也必须检查其实际输入身份，不能承诺缓存命中。
身份未变、来源完整的请求经 --adopt-root 复用，不复制缓存。

### 各付费节点 token 预期量级（B0 登记；D6）

以下为迁移期历史量级，不是 R9 报价。原始历史根已清理，按历史登记读数；DeepSeek 未提供
独立 reasoning 时保持 null。R9c 回填各 owner 首次成功率、真实 token、失败／重试及成本，
尤其补 associate 的首次实测和新表达／章包输入规模；显著超量先回查协议／材料。

| 付费节点 | prompt 量级 | completion 量级 | 说明 |
|---|---|---|---|
| `str06.stage2.book` | 1.3万–1.6万 | 0.5万–1.5万 | v13/F9 实测；含 e00 引子证据段 |
| `str06.stage2.chapters`（每章） | 3千–9千 | 0.8万–4.5万 | completion 高值主要是 reasoning（v13 ch05 42515 为高 effort 对话章）；D3 去登记表后预期趋低 |
| `str06.stage2.names` | ~9千 | 3千–2万 | F9 8920/17532、F6c 8.6k/2.6k（effort 差异） |
| `str06.stage2.associate` | ≤1万（初始闸，无实测） | ≤1万（初始闸，无实测） | 新节点：输入为句柄与最小文本，应显著小于 book；B6 真实读数回填后修订本表 |
| `str06.stage4.preserve` | ~1.9万 | ~5.5万 | F9 实测 18569/54928；接近旧 65536 上限，128K（D6）后仍按此量级预期，超出即回查协议 |
| `str06.stage4.body_design` | ~1.2万 | ~6万 | F5e 11952/61040；整书一次调用、最可能触上限的节点，协议瘦身首要对象 |
| `str06.stage4.bind` | ~3.6万 | ~3.2万 | F5e 36150/32146 |
| `str06.stage4.hook_design` | 1.7万–2.6万 | 1.9万–3.2万 | F5e/F6c 两点实测 |
| `str06.stage5.body`（每章） | 旧形态 5.4万–6.1万；新章包预期 ≤2万（B4 以 renderer 字节数免费实测钉住） | 1千–7千 | F5e body_ch01–ch11；completion 含少量 reasoning，正文 1–2k 字 |
| `str06.stage5.hook` | ~1.6千 | 4千–7千 | F5e 1571/4195、F6c 1.3k/6.5k |
| `str06.stage7.naming` | ~1千 | ~2千 | F6c 0.8k/1.9k |
| `str06.stage2.compile`、`str06.stage4.compile`、`str06.stage7.assemble` | 0 | 0 | 结构性禁付（`executor_content_limit=0`），无 token |

## 本轮之后

R7 是 B6 文学问题驱动的一轮新契约／流水线优化，尚未完成；持续登记漏保／过保、
under-spec、状态过建模、checklist prose、导演 brief 夹带未登记事实或通用形容词、
章内揭示与逐场兑现的机械不可证边界、过度失效、下游语义材料不足及 Golden 成本。
转场重开、跨章重复、无名细节冲突、声线漂移依 B6 阅读证据推进，不无限扩张免费重构。

B6c／Golden（D10）：固定 Workbench 的《夫君的报恩》《三人成行》；
前者为开发用书，独立性有限须披露。先更新 `tests/golden.py::CRITERIA` 的退役 R-9 引用，
冻结六维 Core Preservation、Structural Originality、Causal Continuity、
Character Continuity、Natural Progression、Local Prose Coherence，人工阅读为主，
模型辅助另授权。源书在显式 NOVEL_DATA／NOVEL_CORPUS 下核实后冻结；
输出由 NOVEL_OUTPUT 指向独立根，不覆盖语料。

冻结源版本、输入 manifest、baseline 成书、rubric/version、评价方式、辅助评审
prompt／provider／model／参数、输出根与成本；不缩减冻结分母。只变评价时重评双方现有
成书，不自动重生成。《夫君的报恩》F5e 对照仅使用历史读数（原始根已清理）；
《三人成行》对照 Workbench Golden Set v2.1 oracle，具体可用证据开工核验。
源／新书／baseline 证据位置分别记；Structural Originality 按书／场景与语义承载比较，
不要求同号章对应。阅读时序判据随目标语义版本化，保留事实、因果、披露与改名覆盖。

NameMap v5 跨书赋值、第二本表达力及第三人称串人称问题须有对应证据；
本书第一人称不能代签。未评或证据不足保持未验证。Golden 不代替独立
current-schema corpus 验收，也不作为 STR-06 迁移关闭前的重复生成义务。

## 质量重设计批次

已完成交付仅作恢复索引，完整记录按本文件 Git 历史及
[DECISIONS.md](DECISIONS.md) 查询；R9 的待实施要求见上文，不勾成已交付。

| 批次 | 状态与已交付职责 |
|---|---|
| R0 | [x] 字段送达登记、变异守卫与 fake 故障注入底座 |
| R1 | [x] associate-v2、typed 关联与唯一句柄登记表；AUD-01／02 转绿 |
| R2 | [x] Design-v2、两层 IR、线程／承接／背景送达、renderer-v5；AUD-03／04 转绿 |
| R3／R4 | [x] 有界 owner 修订与回派、逐章集合恢复；九维人工阅读协议唯一登记 |
| R6 | [x] 输出组 publish／seal、验过才发布、未封存拒正式消费；AUD-05 转绿；总槽初始化竞态修复 |
| R5／R5b | [x] fake 真实产品链回归、研发选章／暂停／续跑与留证；免费工程基线不代替 B6 验收 |
| 语义冻结闸 | [x] D8 初始冻结及独立协议钉；R8b 按 D8 修订仅改 design-v3 |
| R8 交付记录 | [x] 回派身份与级联、Design-v3 typed 一次校验、人物首现闸；3423 项终轮全绿。随后分支同步 3409 项含一项 appendlog 间歇红，分别登记，不互相覆盖 |
| R8b 留待 | [ ] Design prompt 由模型 JSON Schema 生成，已并入 R9b，不再单开收尾批 |
| R7 | [ ] 未实施；等待 B6 阅读输入，不属于已完成 R0–R6 |

AUD-01～05 的现役反例继续保留；全部转绿不证明 R9 新发现的材料泄漏或成书语义已通过。

## 职责解耦批次（2026-09-27 起；B0–B6）

| 批次 | 状态与恢复索引 |
|---|---|
| B0／B1 | [x] 契约计划与五层运行记录、响应耐久、额度／重试／冻结配置；token 量级与旧记录映射见上文 |
| B2 | [x] Stage2 book／chapters／names／associate／compile 五 owner、双真源唯一发布 |
| B3／B4 | [x] Stage4 章闭包与 Stage5/7 章包、并发、章回执／集合恢复、确定性交付 |
| B5 | [x] HTTP→CLI→交付→重启 fake 全链；`novel/stages/stage2/source.py` 整包删除，独有反例迁五 owner，不复活旧联合抽取 |
| 收尾批 | [x] 失败形状与逐章留证的 API／前端消费收口，已按 D11 提前合入 main |
| B6 | [ ] B6a 最终工程基线、R9c／B6-A 二轮与 B6-B 用户验收未闭合；B6c 独立质量轮 |

## 必要证据与标定知识

- 现役测试入口：`tests/test_str06_contracts.py`、`tests/test_str06_invariants.py`、
  `tests/test_str06_fold_guard.py`、`tests/test_str06_source.py`、
  `tests/test_str06_target.py`、`tests/test_str06_execution_ir.py`、
  `tests/test_str06_repairs.py`、`tests/test_str06_workflow.py`、
  `tests/test_str06_body.py`、`tests/test_str06_hook.py`、
  `tests/test_str06_views.py`、`tests/test_str06_product_entry.py`。
  范围由实际消费者与 `tests/suite_catalog.json` 决定，不凭文件名宣称全链通过。
- 冻结面由 `tests/test_str06_f7e_freeze.py` 保护；R9b 按裁决同批重钉，
  不为通过旧测试恢复退役架构。
- 旧迁移／标定／F5e／F9／v13 根已清理，正文与原始输出不可恢复；结论按历史登记与
  `docs/DECISIONS.md` 读取。当前两次 canary 与这些已清理历史根分开登记。
  证据不存在或身份不符报告未查／不可用，不补猜或重购来掩盖。
- 默认并发 4 在新协议下的表现与 429／重试由 R9c 报告；报告恒等基线
  IDENTITY_UPSTREAM 的书级／逐章口径差异仍属 R7 登记，不在 R9 偷改评价语义。

## 历史批次索引

- F4R：既有红账与 fixture／导入登记清偿；Stage0 v3 回归、Stage1 输入联调已完成。
- F5e：历史成功新购 23 次，成书 11461 字，总偏差 +1.63%、逐章带内、改名零残留、
  同根重放零新购；约 111 万 prompt＋23 万 completion token，仅作历史读数，
  开发性阅读不代替用户验收。
- F7／F8：正式接线、旧链退役；F8c 解散 `contracts/schema/str06/`、
  `novel/stages/str06/` 与 `tools/str06_workflow.py`，正式实现分流至现役包；
  F8d 默认非 corpus 回归 3034 项零失败。旧书只读拒识为 legacy_incompatible。
- F9 未通过，D1 以职责解耦批取代；B6 沿用真实产品入口、精确失败归属与工程／用户验收
  分开记录。历史预登记的保真、因果、引子分流、披露／改名反例不撤销，
  纳入 R9c 对应问题及 B6 全书阅读。
- A0–A6 的工程整改已吸收为 R0–R6；AUD-01～05 已转绿，R7 未完成。
  PLAN.md 已删除，不从历史重新建立并行活动计划。

## 批次推进记录规则

开工核对代码状态、正式输入、请求身份、许可、剩余额度及消费者；交付记录包含精确
node／item、artifact／版本、最终盘面、恢复入口及测试层级／分母。免费与真实付费验证
分列；测试通过、函数存在或历史语义结论不直接勾选质量验收。

每批完成后只更新本文件的当前状态、该批交付证据与下一步；长历史进 Git，
复用教训进 LESSONS，裁决缘由进 DECISIONS。遇到新产品语义、公开契约或架构取舍，
先核实既有裁决再提交用户决定；文档重置本身不实施 R9、不开始付费运行。
