# STR-06 · R9 修订闭环、语义契约升版与 B6 分步验收（Stage6 退役）

本文件是唯一活动计划，登记进度、推进顺序、验收、恢复与删除条件。目标架构与字段
边界见 [str06.md](schema/str06.md)，现役行为以 [BACKEND_CONTRACT.md](BACKEND_CONTRACT.md)
及 `contracts/` 为准，开发遵守根及所属目录 [AGENTS.md](../AGENTS.md)。

**2026-09-30 重置**：吸收用户提供的仓外计划
`e-practice-group-novel-rewriting-outputs-glistening-clarke.md`，将 R9 插在 B6-A 二轮之前。
活动账重置已提交为 `67e454c8`。R9a、R9b 已实现；现役协议与冻结记录按 D8 修订二
集中升版，免费工程验收及实测证据见下文。用户已明确授权 R9c 首段付费，
以及向 DeepSeek 外发本书正文及派生材料；首段仍停在 Stage5 Hook 之前。
不恢复 PLAN.md 为第二份活动计划。已完成批次压缩为历史索引，完整交付记录按
`git log -- docs/REQUIREMENTS.md` 读取；旧计划里的运行命令与提交步骤不作为交付记录。

**2026-10-01 重置（R10）**：用户裁决停止 R9c、不申请 Design 追加轮，并提供两份计划
（R10 执行草案＋接口与恢复规则计划）；agent 合并裁决后形成 [PLAN.md](../PLAN.md) 作为
R10 批次唯一执行指南（D8 修订三）。本文件仍是唯一活动账；PLAN.md 只作静态指南，
进度、批次与验收状态继续登记在此。B6-B 并入 R10c、B6c 并入 R10d；R7 余项由 R10c
阅读问题驱动。R10 升版（rc12／book-v3／chapters-v2／preserve-v2＋policy 5／design-v5／
Blueprint v15／renderer＋写手 v7）落地后，R9c 与 b6a 已购产物按版本关闭式拒识，
不计作语义检出；新根全量重购。

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

**已完成工程**：B0–B5 与收尾批；R0–R6、R5b、语义冻结闸、R8、R9a、R9b、B6a；R9c 首段。
**当前**：R9c 已按用户裁决（2026-09-30）停止，不申请 Design 追加轮；R9c 与 b6a 两根
只读留证。活动批次切换为 **R10（关键语义保真与 WriterIR 精简）**，执行指南唯一登记
[PLAN.md](../PLAN.md)（D8 修订三）；R10-0 为当前子批（免费），其后 R10a → R10b → 收口
→ R10c → R10d。

- 现役语义冻结面是 book 的 `stage2-book-v2`、chapters／names 的
  `stage2-observation-v1`、`stage2-associate-v2`、Stage4 的
  preserve-v1／design-v4／bind-v1／hook-design-v1；Preserve policy v4、
  canonical 总版 `str06-rc11`、Blueprint v14、renderer／写手 v6。
  以 `tests/test_str06_f7e_freeze.py` 为机械依据；契约身份
  `08a05314a73c68d4518fc008af795078cdecf5f69888346b4f6a98d7cda497bb`。
  R10a/b 按指南 §二.4 集中升版，冻结钉同批重钉。
- 20260930 canary 已逐文件只读核验：Stage2/4、Hook 及第 1、5、10 章存在；
  366 个文件前后摘要不变。39 个成功非缓存回答的请求／响应／成本绑定闭合，
  2 个连接失败尝试另列、用量未知；首现、读取定位与实际材料诊断见「免费实施证据」。
  b6a 三章＋Hook 是唯一成文基线（含两次 owner patch），对照复用性判定见 R10-0。
- 20260929b 与 20260930 canary 及 R9c 根只读保留作证据；旧根不得原地升版、手补新字段、
  清 ledger 或复制旧批准。R10c 使用独立新根。
- 最近已登记的分支同步回归：3409 项中 3408 通过、1 失败；唯一失败为 appendlog
  并发锁初始化 PermissionError，独立模块复跑 5 项全过。间歇性问题尚未根治，
  随 B6a 核查；不得将该轮写成全绿。前端 120 项及 typecheck／seams／build 已过。

推进顺序如下（细化见 PLAN.md 第三节）：

| 顺序 | 批次与状态 | 依赖、交付与放行条件 |
|---|---|---|
| 1 | [x] R9a／R9b／B6a（历史） | 交付记录见历史索引；R9c 首段执行记录见下节 |
| 2 | [x] R10-0 基线、真实消费与反例集（免费） | 登记、两根冻结证据清单、逐 owner 请求占比、送达守卫自检、C1–C11 反例集、用户提供的 Golden Set v3.0 关键案例（已冻结）、基线读数——全部闭合（见 R10-0 执行记录） |
| 3 | [x] R10a 关键语义交接（免费开发） | a1 Stage2 引文/时态（**已完成**）→ a2 Preserve 证据视图/锚定（**已完成**）→ a3 冲突回派三分支（**已完成**）→ a4 Brief 锁定＋Design v5（**已完成**）→ a5 Bind/compile 位置校验（**已完成**）；一次校验合并报告，fake transport |
| 4 | [ ] R10b WriterIR 投影与写手协议 v7（免费） | 完整章包夹具先行；背景投影收窄；同基座 v6/v7 免费形状读数对照，不做付费对照 |
| 5 | [x] 收口 工程验收与付费基线冻结（免费） | §9.5 五文件、受影响 stage、fake 产品链（product_entry 真实子进程）、旧根关闭式拒识核验、默认非 corpus 回归全绿；前端无 API 形状变化不触发四件；rc12 全表＋F7E 重钉＋判据登记（执行记录见 R10a 节） |
| 6 | [ ] R10c＝B6-B 付费无人工 patch 全书（分段授权） | c1 语义层段＋免费核读 → c2 成文段；工程核验＋用户通读 → STR-06 关闭；禁止 owner 内容 patch |
| 7 | [ ] R10d 第二本＋重复生成（另行授权） | golden CRITERIA 六维更新与 manifest 冻结；《三人成行》全链＋至少一次重复生成，披露普适性 |

### R9c 首段执行记录（2026-09-30）

- 新运行根为 `outputs/str06_r9_canary_20260930/夫君的报恩_2059579376044396719`；
  原报价为首段 ¥3–8 的估算，不是金额硬上限。授权原话及报价摘要保存在运行根
  `runtime/review/r9c_first_segment_authorization.json`，首段暂停边界为 `str06.stage5.hook`。
- 真实 AgentRun `1790766409-7b046ffd` 已显式停止，epoch 排空且没有悬空 admission；
  原运行 32 次非缓存请求均收到回答，首轮与修订失败仍保留在成本与工作单元账。
  机械接受不等于语义通过，R9c 尚未完成，后段没有获得付费许可。
- 首轮核读发现源证据错位、救人动机缺失、将眼神猜测写成他人知情、计划归期写成
  已发生时长、诊孕时点提前，以及 Preserve 混合了不同的成立／揭示阶段。
  第一批源 patch 已经正式免费采用并重编译；Preserve 的 86 条阶段义务已免费采用，
  其中 85 条必保、1 条明确不必保，旧 Design／Bind 尚未覆盖当前分母。
- 全书 fold 继续发现待执行计划、双人选状态、搬家计划及临时身体状态残留，
  生命周期补丁已正式免费采用，当前 associate／Preserve 新输入的真实基线分别在
  原额度内购买 1 次后免费修订，两个工作单元现在均为 4/4。Stage2 已重编译，
  11 章、4 人物、5 条初态、0 undecided；末章六个原文未解答的悬念仍保持开放。
  原文未解答的藏身过程、邻居身份、父亲自称等保持未知，不为闭合快照补猜答案。
- Design 草案含 30 场景／85 拍，每条必保义务有明确承载；85 条绑定、1 条明确不必保
  保留在原 86 条分母。经正式接受器、执行计划及 WriterIR 的免费内存预览，
  11 章实际写作材料与 Hook context 终检零错误；逐章核读后补齐计划替换、疑点确认、
  误读被反证及准确揭示禁区。该预览只保存审阅投影，未发布 Design／Bind／Hook
  正式真源，不能算正式 Stage4 compile 通过。九维记录、草案与详细证据仅存运行根
  `runtime/review/reading_review.json` 及 `runtime/review/semantic_repair_draft/`；
  正文及 Hook 未运行的质量维度保持未验证，运行时语义仍为 `unver`。
- 当前账本为 37 次非缓存请求、46 次缓存采用，37 次真实请求用量均已知；
  按官方空闲单价折算约 ¥3.97，仅为用量估算，不是账户扣费或余额。追加提案只涉及
  Design 1 次内容轮（4→5），预计 ¥0.1–0.6，不自动扩至原允许的最大追加数。
  新输入身份及追加预览在运行根留证；首段后续 Bind／Hook Design 沿用原剩余额度，
  仍停在 `str06.stage5.hook` 之前，后段未获付费许可。
- canary 暴露了确定性缺陷：跨章关闭句柄重复占用被静默忽略，或跨章配对被章内
  close 抢先消费。现由 associate 接受与 Stage2 compiler 共用占用检查，冲突拒发
  双真源并走既有 associate 免费修复入口；不改协议、prompt、额度或公开 API。
  新反例先红后绿；Stage2 关联／发布 42 项与精确消费者 175 项通过，合计 217 项
  定向验证，含正式产物保持及免费恢复，不冒充新一轮默认完整回归。
  证据：`.test_cache/r9-loop-red.xml`、`r9-loop-targeted.xml`、`r9-loop-consumers.xml`。
- 第二批源 patch 在正式免费采用入口暴露了连续修订缺陷：已有采用摘要挡住了
  同一已购身份的下一份显式补丁。现仅在上一份耐久修订、当前正式产物及旧采用
  摘要均可核验时允许接续，采用前再次核验并归档旧修订；历史版本不符、证据损坏
  与未登记改写继续拒绝。87 项定向验证通过，包含真实 CLI 两次采用、删除外部
  文件后 Agent 交付及二次代跑零新购；不改模型请求、协议或预算。
  证据：`.test_cache/r9-patch-second-red.xml`、`r9-patch-second-targeted.xml`。
- 真实追加预览继续暴露了书键不一致：付费额度按隔离根目录名记账，AgentRun 与
  API 按短 SID 引用。恢复共用层现经唯一实际书目录核验同书，不另造额度账、不改
  既有轮次或成本；异书、歧义目录及同书未闭合运行仍拒绝。70 项精确消费者通过，
  覆盖 HTTP 预览／追加／重启幂等及当前／历史执行身份；24 项专门测试包含在该
  70 项中，不重复相加。真实 canary 只读追加预览已可用，尚未应用授权。
  本次为首段内确定性修复的定向验收，不是整项目终验或新一轮冻结发布；
  B6-B／关闭批按最终变更集合补齐工程验收。
  证据：`.test_cache/r9-grant-book-red.xml`、`r9-grant-book-targeted.xml`、
  `r9-grant-book-consumers.xml`。

### R10-0 执行记录（2026-10-01）

- **0.1 登记**：[PLAN.md](../PLAN.md)（合并两份计划并裁决冲突）、DECISIONS
  D8 修订三、本文件批次表重置（`7cfb73e5`）。
- **0.4 送达守卫修补**（`b1da1edb`）：`tests/test_str06_semantic_delivery.py`
  新增自检——凡登记写手/Hook 消费者的字段必须有变异用例（豁免闭集 4 项带
  原因）；绿基座探针实证 7 项登记漂移（dramatic_intent、entry_state／
  background_state 的 domain 与 holder、entry_knowledge／background_knowledge
  的 subjects 变异不改变渲染字节），登记如实改 audit_only；补 37 个正文面＋
  6 个 Hook 面变异用例。该文件 107 项与契约 §9.5 五文件、
  `tests/test_str06_r2_engine.py` 全绿。
- **0.5 反例回归集**（`b1da1edb`）：`tests/str06_semantic_cases.py` 登记
  C1–C11 反例＋P1–P6 正例（合成最小样例，不引付费回答或正文），每例标明
  期望 typed 结果与转红/绿子批；机械不可查项（C8 部分、C11）显式归冻结
  关键案例，不删登记。
- **0.2 冻结证据**：R9c 根共 473 文件／12.3MB——`_service`（ledger 83 行＋
  运行记录）、书目录 `stage0/1`（4 文件）、`stage2`（observations 14＋
  blueprints／characters／acceptance 各 1）、`stage4`（design 2＋grounding 2
  ＋acceptance 1，**无 ir／无正式 compile 通过**）、`runtime`（commit 83、
  review 103、revisions 67）。checkpoint 归属：正式 Design/Bind 属首轮，
  checkpoint/brief 属补丁后，`runtime/review/semantic_repair_draft/` 是
  未采用人工草案。**b6a 两根（20260929b／20260930）经核验已从盘面清除**
  （2026-10-01 全盘搜索无果），其三章＋Hook 成文、41 笔付费账与两次 owner
  patch 事实只余本文件历史记录；对照复用性判定＝**不可复用**（无完整无
  patch 成书、现无盘面），版本结论只报绝对达标。
- **0.7 基线读数**（R10c 对照基准）：R9c ledger 83 行＝37 笔付费＋46 缓存。
  逐节点付费 ptok／ctok：preserve 4 笔 157.8k／102.0k（≈3.9 万 prompt/轮）、
  body_design 4 笔 108.4k／208.3k（≈2.7 万 prompt、5.2 万 completion/轮）、
  chapters 19 笔 121.0k／356.5k、book 2 笔 33.2k／82.6k、associate 4 笔
  58.3k／108.4k、names 2 笔 18.0k／8.5k、bind 1 笔 9.7k／16.0k、
  hook_design 1 笔 22.7k／16.6k。草案章包（`writer_material_preview.json`）
  六分区全书条目：必须写出 197／不可违背 287／承接 44／导演说明 266／
  **背景 979**／禁区 100；背景逐章膨胀 13→245 条（ch10），相邻章背景重复率
  0.48–0.88（承接 0–0.6）——「材料重复且打架」的量化基准，R10b 背景投影
  收窄的对象读数。
- **0.3 真实消费核对**（2026-10-01，`tools/str06_review.py::request_preview`
  只读重建 R9c 根各 owner 正式请求，token 用 `count_input_tokens` 同口径
  差分分解；正文明细不落仓库，重算用 E:/_tr 一次性脚本）：捕获形态为
  当前盘面＋当前代码（与 rc11 已购请求同身份）；preserve 为修订轮形态
  （含上一版与修订指令，比首轮大）。逐 owner「user 净 token＝规则＋材料」：

  | owner | 付费/缓存 | user | 规则 | 材料（结构） |
  |---|---|---|---|---|
  | book | 2/2 | 15.7k | 1.1k | 14.6k（全书正文＋证据清单） |
  | ch01/06/11 | 19/33（全章） | 2.9k/5.7k/7.9k | 1.8k 固定 | 1.1k–6.2k（章正文，随章长） |
  | names | 2/4 | 8.6k | 0.4k | 8.2k，其中 **text＝7.5k 第二次全书正文** |
  | associate | 4/1 | 15.2k | — | 15.1k（行式句柄清单为主体） |
  | preserve | 4/1 | 46.4k | 1.2k | 45.2k，其中 **Blueprint JSON 倾倒 29.9k**＋characters 1.1k＋修订轮附加≈14k |
  | body_design | 4/3 | 17.5k | **7.2k（最大规则面）** | 10.3k（brief 10.1k） |
  | bind | 1/1 | 11.0k | 0.3k | 10.8k（义务 7.0k＝compiler 拷贝＋场景 3.7k） |
  | hook_design | 1/1 | 22.6k（＝所购 22.7k） | 0.9k | 21.7k（design 材料 21.5k） |

  结论（R10a/b 依据）：①Preserve 的整份 Blueprint JSON 倾倒（29.9k/轮）是
  a2 `project_preserve_view` 的直接替换对象；②names 与 book 各自付费重发
  全书正文（14.6k＋7.5k）——「付费重新生成 vs 落盘重复」里属前者，names
  面的必要性随 a1 证据窗口重估；③bind 的义务面是 compiler 拷贝（不花钱
  重生成，只占 prompt）；④body_design 规则 7.2k 为最大协议面，R10b 删
  dramatic_intent 与规则收敛时复核；⑤修订轮 prompt 比首轮大约 14k
  （附加上一版回答与指令），多轮修订的 token 成本随轮数线性叠加。
- **0.6 关键案例**（已闭合，2026-10-01 用户提供并冻结）：两本书
  `Test_data/Workbench/Golden_Set_v3.0_{夫君的报恩,三人成行}`（md＋yaml 双格式，
  模板 `Golden_Set_v3.0_Template_STR06.md`）。机械核验全绿：源文件 sha256
  逐字节匹配、216＋139 个锚点全部逐字命中源文且行号精确对齐；夫君侧
  8P/3T/4E/5SIG/9LIM/10 案例/32 源理解检查，三人成行 6P/3T/3E/5SIG/6LIM/
  10 案例/17 检查。v3.0 口径采纳：源理解与目标重构分离、四值判定
  （pass/fail/unver/not_applicable）、污染规则（夫君＝开发可见；三人成行
  保持隔离评测资格，R10d 依赖）。早于原时序（用户 2026-10-01 裁决「与
  通读一并确认」）由用户直接提供冻结件。**R10-0 至此全部闭合。**

### R10a a1 执行记录（2026-10-01，免费，fake transport）

- **schema**（`contracts/schema/source.py`／`observation.py`）：新增
  `EvidenceQuote{segment, quote}`（quote 逐字精确引用，不归一化不模糊匹配）；
  `Effect`／`Cognition`／`InitialStateAtom`／`WorldConstraint`／观察面
  `InitialFact`／`WorldRule` 必填 `basis`（≥1，段属条目自身 source_refs）；
  `Effect` 必填 `modality` 闭集（actual/planned/expected/conditional）。
  `BlueprintsArtifactV14→V15`：compiler 派生 `evidence` 证据窗口（被引句段
  全文＋同章前后各一段机械合并；只收被引用段，邻句送达≠邻句为证据）。
  协议字面量：book `stage2-book-v2→v3`、chapters 拆独立字面
  `stage2-chapters-v2`（names 输入投影未变保持 observation-v1，associate
  仍 v2）。RC 版式头 rc11 与全链 rc12 升版留在收口批同批登记（§二.4）。
- **resolver**（`stage2/resolver.py`）：`verify_quote`＋`verify_bases` 批量
  判定；新 issue 码 `basis_segment_outside_refs`（引文段不在条目自身
  source_refs 内——引你所引）与 `quote_not_in_segment`（引文不逐字存在于
  段内）。不要求唯一出现（basis 是证据存在性，不是定位）。
- **owner 验收**（`stage2/book.py`／`chapters.py`）：初态/约束/逐拍转移的
  basis 在 owner 闸即判（一次校验、合并报告）；chapters 验收改传完整段映射
  （含 text）使逐字判定可行（`novel/stages/chain.py::run_chapters` 同步）。
- **compile 防御复核**（`stage2/compile.py`）：发布前对全书 basis 再核一遍
  （I-34 fail fast），同时收集被引段派生证据窗口；fold 新增
  `evidence_metrics` 读数（§四诊断表，度量不是门）：引文长度分布
  （min/median/max）、引文等于称谓字面数、命题含判断限定词比例
  （以为/觉得/像是/预计/约/几天后，与 prompt 同集——P3 正例钉住限定词
  只进读数不进门）、modality 计数。
- **prompt**（`stages/common.py`）：BOOK/CHAPTER 两 prompt 增【逐字证据】
  段（引文须含决定判断的限定原文、不得只摘人名或动作词凑数；modality
  四值语义；认识限定只进 cognition 的 mode、命题写命题本身）。
- **冻结钉重钉**（`tests/test_str06_f7e_freeze.py`，守卫指引动作）：
  `FROZEN_SEMANTIC_PROTOCOLS` 更新（book v3＋新增 chapters v2 行）；
  `FROZEN_CONTRACT_IDENTITY` 重算
  `7031356e…04eae`（漂移原因＝Blueprint 模型名 V14→V15 进身份哈希）。
  旧候选根对 RC 面仍为 rc11 身份，新链盘面按新身份开跑。
- **fixture 面**：`test_str06_stage2_publish`（_basis helper／工厂补
  modality+basis／_chapter_evidence Mapping 形态）、`_owners`／`_protocol`／
  `_source`／`_workflow`／`_hook`（复用 faults 底座）／`_repairs`／
  `test_store`／`test_str06_target`／`hard_cases`（V15 evidence 必填）／
  `test_str06_r2_engine`（typed WorldConstraint 补 basis）。
- **a1 判据测试**（新 `tests/test_str06_a1_semantics.py`，消费
  `CASES_BY_BATCH['R10a-a1']`）：C4 红两面（引文转写→quote_not_in_segment；
  段越界→basis_segment_outside_refs）＋逐字命中绿；P3 绿（「原以为／后来
  证实」不被关键词拦）；C2 的 a1 面（expected 槽位可表达）；读数四件套
  计数。C2 案例登记修正：其 mechanical_face 原描述「命题含预计限定而
  actual → 拒」与 PLAN §四（限定词列诊断读数不阻断）冲突，按 PLAN 对齐为
  「a1 只给槽位与读数，转绿在 a4 kf 一致性」。
- **验证**：str06 全选测（-k "str06 or store or hard_cases"）934 项＋
  契约 §9.5 五文件 389 项＋新 a1 面 6 项全绿（basetemp E:/_tr/r10a1）。
  全量默认非 corpus 回归留收口批（子批不触发）。零付费。

### R10a a2 执行记录（2026-10-01，免费，fake transport）

- **schema**（`contracts/schema/grounding.py`）：新 `ObligationAnchor`（投影
  句柄 → compiler **类型化拷贝**：holder/mode/proposition_ref/
  proposition_statement/address/value/modality/visibility/question/quotes，
  I-15/I-21——模型只给句柄，内容不由模型复述；a4 知情/时态锁直接消费
  holder/mode/proposition_ref/modality）；`SourceObligation.anchors` 必填
  ≥1（义务必须锚定其依赖的源原子）；新 `ObligationSeries`（members≥2、
  mechanism 取源表达 ex_id 或 null）；`PreserveCheckpoint.series` 带
  成员闭集与互斥的构造期闸；`SourceLineage` 保留为 compiler 派生结果。
  协议 `stage4-preserve-v1→v2`、policy `4→5`。
- **投影**（`novel/stages/stage4/grounding.py::project_preserve_view`）：
  按书级初态→世界约束→源表达机制（ex_id 闭集）→逐章逐拍确定性渲染，
  每原子一行带句柄＋类型化内容＋逐字引文，文末证据窗口（被引句段全文，
  Blueprint V15 的窗口派生）。取代整份 Blueprint JSON 倾倒（R10-0.3 实证
  29.9k token/轮）；prompt 材料改 `PRESERVE_RULES＋视图文本＋人物表 JSON`
  （`novel/stages/chain.py::run_preserve`）。
- **assemble v2**：义务键白名单 `anchors`（闭集判定＋缺 anchors 拒）、
  `lineage` 退出 wire（compiler 从锚点派生 blueprint_refs/character_ids）；
  series 成员 1-based 序号→正式 ID、越界/少于 2/mechanism 不在 ex_id 闭集
  精确拒；evidence_conflicts 只验形状（锚点/句段闭集、句段属锚点拍证据、
  note 非空）——**冲突非空不发布、回派 Stage2 是 a3 的编排决定**，不混入
  validator（I-36）。checkpoint hash 覆盖 anchors 与 series。
- **PRESERVE_RULES v5**：输入面描述改投影视图；阶段与揭示规则（同一机制
  多次发生各独立登记＋series；揭示义务只在源有读者确认时要求确认；原文
  未说明保持未知）；冲突报告规则（不私建事实版本）。
- **送达登记**（`contracts/schema/consumption.py`）：anchors 12 个子字段
  全部 audit_only（renderer 剥离的源坐标面；a4 起部分子字段成为锁输入，
  届时按实际渲染改 mode）。
- **冻结钉**：`FROZEN_SEMANTIC_PROTOCOLS` 更新 preserve v2＋policy 5
  （契约身份哈希不变——ArtifactSpec 模型名未换）。
- **fixture 面**：`str06_facilities.v13_effect` 升级（modality＋basis 参数）
  ＋新 `anchor_stub`（手工 checkpoint 构造统一引用）；12 个文件的 typed
  `SourceObligation` 构造补 anchors；wire 面换 v2（target/hook/
  workflow/publish/hard_cases/faults 底座）。
- **a2 判据测试**（新 `tests/test_str06_a2_preserve.py`，消费
  `CASES_BY_BATCH['R10a-a2']`：C1/C3/C5/P1）：视图确定性渲染（句柄/引文/
  窗口/ex_id、I-24 纯函数）；绿路径（lineage 派生＋typed anchors＋C1 的
  mode 拷贝）；红例（缺 anchors/未知句柄/lineage 键拒收/series 三种红/
  schema 互斥与闭集兜底/冲突形状四面）；hash 绑定 anchors 与 series。
- **验证**：定向面（-k "str06 or store or hard_cases or 契约 §9.5 五文件
  ＋test_selection"）1386 项，除 test_selection 的 1 项暂态（选择器按
  工作区 dirty 集选受影响面，a2 改动未提交时把 test_store.py 算入；提交后
  复验恢复）外全绿。零付费。

### R10a a3 执行记录（2026-10-01，免费，fake transport）

- **路由面**（`contracts/schema/workflow.py`）：新 issue 码
  `source_evidence_conflict`；路由占位 `revise_upstream:conflict_anchor`——
  `route_owner_issue` 按锚点句柄解析（拍句柄 `bNN_MM.e|c|lN` →
  `str06.stage2.chapters`，书级 `s0_NN`/`wc_NN` → `str06.stage2.book`；
  不可解析即停，机械路由不猜）。`OWNER_PRODUCT_RELS` 增 book；逐章 owner
  （`chNN`）经 `chapter_owner_product_rel` 唯一拼装（owner 名即章目录名，
  不逐章登记静态表）。`contracts/invariants.py::NODES` 为 book/chapters 登记
  `operator_redo='owner_revision'`（resume_verdict 与 operator_redo_flags
  按 node 匹配 issue，机制通用无需逐 owner 特判；契约身份哈希不含该格，
  7031356e… 不变）。
- **回派入口**（`novel/stages/chain.py::run_preserve`＋
  `_backfill_source_evidence_conflicts`）：冲突非空——回答由 ask 路径照常
  耐久、checkpoint 不发布；按锚点分组回派（拍句柄→owner `chNN`／item
  `chapter:N`，书级→owner `book`）。`run_book`/`run_chapters` 的 prompt 接
  `revision_prompt_for_owner`（无 open 记录时逐字节不变，请求身份不漂移）。
- **冲突处理结果注入**（`_conflict_resolution_notes`，§二.2）：盘上有未闭合
  冲突记录时，Preserve 重跑 prompt 附确定性对比块——owner 正式产物当前
  digest vs 记录 against digest（已修订／维持原解释，纯机械不判语义）。材料
  变化 ⇒ 新请求身份：防同身份缓存命中重放上一轮带冲突的回答（e2e 记忆中
  「直连强制重采」同根问题在此面的工程解）。复验通过（本轮无冲突＋
  checkpoint 发布）按 owner 清账（`_resolve_handled_conflicts`；轮数与指纹
  留账不复位）。
- **误报闭合**：同指纹重现走 `discover_owner_issues` 无进展闸
  （OwnerRevisionStop）——「Stage2 维持原解释后 Preserve 再报同一冲突」不
  二次回派；额度耗尽停给人（owner patch／人工重跑）。
- **a3 判据测试**（新 `tests/test_str06_a3_conflicts.py` 5 项）：
  归因（拍→ch01/item、书级→book、不可解析停、issue 记录形状、checkpoint
  不发布）；fake transport 闭环（preserve 首答带冲突 → 回派 ch01 → 章观察
  修订轮 → 重编译 → preserve 复验干净 → checkpoint/brief 发布、ch01 与
  preserve 各恰两轮、清账 resolved）；粘性冲突无进展停（不二次回派、
  rounds 留 1）。`test_str06_r3_selfheal` 路由表测试补 conflict_anchor 轴。
- **验证**：定向面 1348 项＋selfheal/a3 26 项全绿。零付费。

### R10a a4 执行记录（2026-10-01，免费，fake transport）

- **Brief 锁定项**（`contracts/schema/grounding.py`＋
  `novel/stages/stage4/grounding.py::project_brief`）：新增
  BriefProposition（kp_<hash> 命题登记，asserts 认知转
  `publicly_claimed_by` 标记——公开声称不产生任何持有者知情锁）、
  BriefKnowledgeLock（{holder|reader, kp, mode}；唯一映射
  learns/confirms→known、suspects→suspected、hears_claim→heard、
  believes→believes；public 持有者不铸锁）、BriefFactLock（kf_<hash>：
  planned/expected/conditional 时态锁、unknown_value 值未知锁、
  unanswered 原文未回答锁——canonical loops open 未 close）。
  BriefRequirement 增 locks（信息面）＋realize_holders/realize_locks
  （必保义务兑现分母，非必保为空）＋series_group/series_mechanism
  （盲组 token sr_<hash>，不泄源序号）。ObligationAnchor 增 op
  （值未知锁输入）。锁句柄经 grounding._lock_id 铸（与
  obligation_core_id 同纪律；命题 ref 是 compile 派发的 k{nn}，故
  kp 句柄随 canonical 而定——fixture 不能静态预计算）。
- **Design wire v5**（`contracts/schema/design.py::DesignWireV5`，协议
  `stage4-design-v5`）：删 `dramatic_intent`（TargetBeat/DesignWireBeat/
  BeatIR 三面同删；materials 终检字段表同步）；TargetDelta 增
  `modality`（actual/planned/expected/conditional，非知情/非读者 set
  必填、其余禁带；Establishment 透传同形）；拍增
  `expression_refs:[ex_id]`（正向表达任务落具体拍；narration 书级
  机制不上拍）。
- **接受校验**（`novel/stages/stage4/design.py::assemble_body_design`，
  `brief` 传入时经 `_check_lock_acceptance`＋`_check_expression_refs`，
  生产链 chain.run_body_design 恒传）：kp 键未登记拒；knowledge kp 键
  持有者无锁或模式越权拒（授权集＝该 (holder,kp) 全部锁模式——known
  只能来自 known 锁，C1）；reader set 用 kp 键须有 reader known 锁
  （线索/怀疑不是确认，C6）；kf 时态锁只能按锁 modality set（C2）、
  unknown_value 不得补值（C9）、unanswered 不得 set 作答；必保义务
  realize 分母缺承载即拒；expression_refs 闭集外/narration 上拍拒。
  位置顺序（知情升级/确认不早于授权位）归 a5 Guard，不在本批。
- **送达登记**（`contracts/schema/consumption.py`）：dramatic_intent
  条目删除（R10-0.4 收口）；新增 anchors[].op、beats[].deltas[].
  modality、planned_delta[].modality（均 AUDIT_ONLY；R10b renderer v7
  措辞送达后改 MUST_REALIZE）与 beats[].expression_refs（audit）。
- **DESIGN_RULES v5**（`novel/stages/common.py`）：事实时态段（必填
  modality 语义）、Brief 锁定项段（kp/kf 键规则、槽位身份、reader
  确认纪律）、正向表达任务段（expression_refs）；dramatic_intent
  表述移除。冻结面：FROZEN_SEMANTIC_PROTOCOLS design 升 v5；
  FROZEN_CONTRACT_IDENTITY 不变（7031356e——模型名与 RC 表未动，
  rc12 全表升版仍留收口批）。
- **fixture 清扫**：全仓 dramatic_intent→删除（dict 键与构造 kw 两形）；
  事实 set 增补 modality='actual'（AST 双轮：dict 字面量＋
  TargetDelta 构造点；`_delta` 类 helper 参数化）；
  'stage4-design-v4'→v5；test_str06_target._design_wire 的 kp 键
  参数化（kp_known 占位；真链测试从 design prompt 内嵌 Brief 解出
  真实 kp 注入——命题正式 ref 由 compile 派发，静态不可算）；
  test_str06_workflow 两个真链 fixture 按 a4 纪律改 kp 承载（concealed
  案 reader 确认改 kp 键）。semantic_delivery 的 dramatic_intent
  变异用例随字段删除。
- **a4 判据测试**（新 `tests/test_str06_a4_design_locks.py` 16 项，
  消费 CASES_BY_BATCH['R10a-a4']，C1/C2/C6/C9 红例＋绿对照）：
  Brief 派生（kp/kf/asserts/series/兑现分母/确定性）、v5 拒读
  dramatic_intent、kp 越权与未登记、kf 时态不一致与缺 modality 形状、
  reader 无锁确认、值未知/未回答 set、必保锁兑现缺失、expression_refs
  闭集与 narration 上拍、锁模型悬空引用拒。
- **验证**：定向面（-k "str06 or store or hard_cases or
  test_docs_references or test_test_selection"）全绿。零付费。

### R10a a5 执行记录（2026-10-01，免费，fake transport）

- **Bind 承载面**：`project_binding_space` 带出 series（正式 ID＋mechanism＋提示）
  与按义务渲染的锁摘要（Brief 锁面：命题文本、持有者授权模式、kf modality/
  类别——锁注册表唯一权威仍是 a4 的 Brief，投影只渲染）；`BIND_RULES` 登记
  「series 成员绑定不同拍」「必保缺口默认归 design」。`assemble_binding` 对
  同一 series 成员落在同一承载拍报 `series_collapsed`（同节点修订面；
  unbound 成员不参与比较，必保未承载另有 must_keep_unbound）。
- **run_bind 修订轮（R-5.5 同批改写）**：接受闸从「节点自身校验、失败即
  INVALID＋ValueError」改为接进 `ask` 的同节点修订轮（≤
  OWNER_REVISION_MAX_ROUNDS、同问题集重复即停）——坏稿先重问；耗尽/无进展
  才写 `.INVALID` 孪生并停。`ValidationStop` 增设 `adoption`（被拦最后一稿
  的显式采用引用，DurableAnswer）供 INVALID 铸来源账；f7a/f7c 两个既有
  bind INVALID 测试同批改写到新行为（bind 恰两次＝原轮＋一轮修订即同指纹
  停；重跑命中缓存零新购、旧孪生归档再覆写语义保留）。
- **锁位置 Guard（epistemic_lock_order）**：`guard.py` 新增全书级
  `lock_order_violations`/`check_epistemic_lock_order`——kp 知情升级/读者
  确认的位置不得早于授权该模式的义务承载位置。授权位＝必保义务 anchors
  授权 (holder,kp,mode) 的全部承载点（最终验收拍＋supporting）最早者
  （支撑拍是承载的一部分——怀疑渐进到证实合法，P2）；initial_state 无披露
  时点不进判据；自由键不查；故事发生顺序不查（顺序三分，P5 合法倒叙不拦）。
  不进 GUARD_CHECKS 逐场表（表序冻结且 scope 单场）；`run_compile_stage4`
  在机械面干净、必保全承载之后调用，违例经 `_backfill_epistemic_lock_order`
  按 `epistemic_lock_order` 回派 `str06.stage4.body_design`（owner=design、
  item=kp 句柄、context 带升级/授权位置与源盲 core_id）。workflow 登记
  新码与单一目标路由。
- **a5 判据测试**（新 `tests/test_str06_a5_positions.py` 14 项，消费
  CASES_BY_BATCH['R10a-a5']，C3/C5/C6/P5 红例＋绿对照）：投影 series/锁
  摘要、series 同拍拒与 unbound 不参与、known 升级早于授权位、reader 确认
  早于授权位、支撑拍承载合法（P2）、合法倒叙不拦（P5）、自由键与
  initial_state 不查、回派编排（owner/item/context/回派账 open）、run_bind
  修订轮好转与粘性耗尽（INVALID 孪生 wire 原样＋gates_skipped＋正式位不写）。
  C6 的 green_in 落 a5（位置正例面；red 面仍在 a4）。
- **验证**：a5 14 例＋受影响 142 例（body/hook/r2/r3/repairs/stage4/target/
  views/a1–a4）＋防漂移/分层 76 例全绿。零付费。

### R10a 收口执行记录（2026-10-01，免费，冻结付费基线门槛）

- **rc12 全表升版**：`STR06_ARTIFACT_RC_VERSIONS` 25 条统一 `str06-rc12`
  （源理解变化波及全书链；names/associate/bind/hook-design 协议字面量不变、
  经 RC 表换身份）；`STR06_RC_VERSION`/`STR06_RC_LABELS` 同批；8 个 schema
  模型的 `contract_version` 字面量升 rc12（book_ir/context×2/design/
  grounding×2/execution/hook/naming/source 默认值/receipt）。写作层
  renderer/写手协议 v6 不在本批（R10b 升 v7）。
- **F7E 重钉**：候选契约身份重算 `7d24e572…`；冻结记录补 R10a 收口段；
  `FROZEN_SEMANTIC_PROTOCOLS` 不变（book v3/chapters v2/preserve v2+policy5/
  design v5/bind v1/hook-design v1/names v1/associate v2——a1–a4 已钉）。
- **判据登记**：BACKEND_CONTRACT 新增 R-2.18（basis 引文定位）/R-2.19
  （Effect.modality 闭集）与 R-5.15（Preserve 锚定与 series）/R-5.16（证据
  冲突回派三分支）/R-5.17（Brief 锁定项与 Design v5 锁规则）/R-5.18（Bind
  series 分别承载）/R-5.19（epistemic_lock_order 位置 Guard）/R-5.20
  （回答复用四条件）；R-5.5 原位改写（接受闸进修订轮）。§五计数 stage2
  18→20、stage4 22→28；`contracts/judges/str06.py` 注册 8 条新 HARD＋
  binding_mechanical 文本改写；`tests/hard_cases.py` 补 8 个最小会红样本
  （绿对照在 a1–a5 套件）；`tests/test_coverage.py` 节计数 R-2 20/R-5 21。
- **文档**：`docs/schema/str06.md` 增 R10 节（rc12 版式与六面契约摘要）；
  `stage2.md`/`stage4.md` 判据表补行并更新计数。
- **验证**：§9.5 五文件（docs_references/coverage/rules_registry/
  hard_counterexamples/ingest_fixtures）全绿；全部 str06 套件全绿（含
  f7a/f7c bind 新行为）；`test_str06_product_entry.py` 11 项全绿（真实子
  进程 fake 全链到交付与重启读回、旧书 legacy_incompatible 关闭式拒识）；
  默认非 corpus 回归按收口门槛执行（含生产代码与可执行契约变化的最终
  集合）。前端无 API 形状变化（issue code 为开放 string 面，
  backend.generated.ts 不含版本/issue 枚举），不触发前端四件。零付费。
- **限制**：背景投影收窄、WriterIR 锁承载、写手协议 v7 归 R10b；判据
  「背景投影规则」（PLAN 收口清单内）随 R10b 落地时再登记。

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
裁决来源已登记到 `docs/DECISIONS.md`，同批重钉冻结记录；现役协议已集中升版。
工程实施与文档登记不构成付费／正文外发授权。

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

### 免费实施证据（2026-09-30；不代签 R9c 或成书语义）

- R9a 的七类反例与绿对照、已购基线漂移、采样额度／身份、原字节耐久与重启恢复
  已落测试；预检及正式采用共用文本 wire 闭集，ledger 的回答摘要也须精确匹配。
  Hook／正文机械耗尽保持 typed 负结论，不再伪装成程序崩溃。
- R9b 的 wire→canonical→实际章包送达、人称／POV、表达覆盖、承接锚、家族称谓、
  第三人称对白与合法倒叙反例闭合；Design 形状由 JSON Schema 生成，动态闭集同源。
- 前置产品链与冻结组 27 项通过；最后的补丁／历史采用组 54 项通过；§9.5 五文件
  385 项及正文硬失败反例共 386 项通过。最终默认非 corpus 回归 **3446 项全部通过**
  （37 分 33 秒；0 fail／error／skip，4 个既有 FastAPI/httpx 弃用警告）。
  前端 120 项及 typecheck／seams／build 通过。完整回归后只更新文档验收记录，
  按文档范围补校验；没有再改生产代码、可执行契约或测试基础设施。
- 首轮完整回归为 **3440 通过／1 失败**：唯一失败是分层白名单漏登记新增
  `stage2/expression.py` compiler，已补登记，整组分层 29 项通过；随后补齐上述
  预检／证据／文本耗尽边界，因此该轮不冒充最终代码全绿。
- 旧根诊断严格区分已接受产物与原购买回答：已接受首现和 Design 读取闭包均为零错；
  ch01 原购买回答有一组晚出场人物误绑，原 Design 回答有 3 处事实建立前读取。
  第 1／5／10 章及 Hook 的保存请求与重建旧视图一致；其余章只有 IR 视图，未成文。
- 旧版 11 章正向视图泄漏数依次为 **14／18／11／10／10／10／32／17／12／11／10**，
  Hook 为 1；仅扩充合法家族称谓后为 **9／6／5／6／6／6／19／11／8／7／4**。
  后者仍使用旧 renderer，不能当作新 v6 的 canary 结果。详细文本与差异留运行根审阅目录。
- MainText 正文含空白 9724、非空白 8818 字符；统一口径后的预算为 **9000**，
  旧预算为 10000。新预算会改变实际 Design 请求身份，不承诺旧回答可采用。
- 独立根为 `E:/practice_group/Novel_rewriting/outputs/str06_r9_canary_20260930`；
  正式 Stage0/1 免费摄入 Workbench 本书，11 章、引子非空白 262 字符，ledger 0 行。
  首段到 Stage4 compile，基础语义单元 **18**，暂停在 `str06.stage5.hook` 之前；
  book 实际输入 **15811 tokens**，max_tokens 仍为 131072。报价、输入／代码／协议／
  配置身份及报告在本书 `runtime/review/r9c_quote_manifest.json`，两个授权为 false；
  原 canary 详细只读审计在同目录 `r9a_legacy_canary_audit.json`，摘要
  `f0a36b667bc13ad6b360c506c255685eaef6c84be0a68c058efad880619dd50d`。

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
| Agent 终验过渡桩 | `novel/orchestrator/delivery.py::assess` 当前只证明代码身份并返回 unknown；正式新链交付由 Stage7 manifest／候选 API 读取。STR-06 关闭前改接正式交付证明并迁移独有反例，删除旧坐标过渡桩；本轮产品链证据不宣称 Agent outcome 已 ready |
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
《三人成行》对照 Workbench Golden Set oracle（现役 v3.0，2026-10-01 用户提供
并冻结，见 R10-0 执行记录 0.6；旧 v2.1 只读留证），具体可用证据开工核验。
源／新书／baseline 证据位置分别记；Structural Originality 按书／场景与语义承载比较，
不要求同号章对应。阅读时序判据随目标语义版本化，保留事实、因果、披露与改名覆盖。

NameMap v5 跨书赋值、第二本表达力及第三人称串人称问题须有对应证据；
本书第一人称不能代签。未评或证据不足保持未验证。Golden 不代替独立
current-schema corpus 验收，也不作为 STR-06 迁移关闭前的重复生成义务。

## 质量重设计批次

已完成交付仅作恢复索引，完整记录按本文件 Git 历史及
[DECISIONS.md](DECISIONS.md) 查询；R9a／R9b 实施与免费证据见上文，R9c 首段执行中、尚未完成。

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
| R8b 留待 | [x] 已随 R9b 完成 Design prompt 的模型 JSON Schema 生成与动态闭集，不再单开收尾批 |
| R9a／R9b | [x] 确定性止损、耐久修订与集中升版已实现；最终工程证据按 B6a 登记，语义核读待 R9c |
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
| B6 | [ ] B6a 免费工程基线已通过；R9c／B6-A 二轮与 B6-B 用户验收未闭合，B6c 独立质量轮 |

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
