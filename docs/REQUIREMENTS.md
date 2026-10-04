# 活动账：返修归因、成本与整书验收

本文件是唯一活动计划，只保留当前状态、未完成项、长期义务、删除条件和证据索引。
行为依据为 [BACKEND_CONTRACT.md](BACKEND_CONTRACT.md) 与 contracts；目标架构见
[str06.md](schema/str06.md)，本轮规格见 [repair_convergence.md](schema/repair_convergence.md)。

**2026-10-04 重置**：用户确认按“规格定稿 → 归因与返修闭环＋计量修复 → 工程验收 →
输入瘦身与思考配置对照 → 整书自动运行及质量验收”推进。重置不清账、不删输出，
不改变冻结实验身份、分母、额度或既有授权。重置前完整记录见提交 `e84a169b` 中本文件；
更早执行史按 `git log -- docs/REQUIREMENTS.md`，裁决见 [DECISIONS.md](DECISIONS.md)。

**当前交接（2026-10-04用户追加指令）**：本次只完成修复、定向回归并commit，不再重跑全量。
在付费前停止；用户先处理其余流水线潜在问题，之后再进行真实整书验收和A/B。
本提交是工程检查点，不是最终冻结发布；现有实验草案没有付费或外发授权。

## 当前状态与下一步

| 顺序／批次 | 状态 | 完成条件与下一动作 |
|---|---|---|
| CS-S 规格定稿 | [x] 中文Gherkin与职责规格定稿；RC-17按用户裁决增补 | RC／CU／BA场景成为本轮验收输入；实现映射随代码补齐 |
| CS-R 归因与返修闭环 | [x] 工程路径闭合；真实书待新冻结运行验收 | 正式来源路由、同判据前移、有界收敛、精确级联与旧账显式更正；RC-17名称同一性按用户裁决执行 |
| CS-M 计量修复 | [x] 新购、恢复、缓存与只读补证通过工程验收 | 原账54次思考已只读补证；旧账不回填、不改变估费总输出 |
| CS-E 工程验收 | [x] 前一子批3655例通过；本检查点按用户要求定向收口 | 本轮225＋6项定向通过，前端全项通过；最终冻结与真实验收等其余问题修复后再进行 |
| CS-I 输入瘦身 | [x] 必要输入投影与真实消费者定向通过 | 名称证据去重，Design完整投影；Preserve补齐原先遗漏的语义后体积增加，不能记作降费成功 |
| CS-T 分节点思考对照 | [~] 配置全链已接通；真实对照按用户要求后置 | 等其余流水线问题修复后冻结相同输入、配置和停止规则，再核对新根授权 |
| CS-G＝R10d／B6c 整书验收 | [ ] 按用户要求后置，未发起新付费 | 六维工具已对齐；等其余问题修复后冻结三书并真实执行，人工与自动分别报告 |
| R7 质量优化 | [ ] 持续登记，按实际阅读反例启动 | 漏保／过保、因果、承接、声线、导演说明、重复与输入不足；本轮已覆盖的输入／成本项不重复立项 |

[返修场景](specs/repair_convergence.feature)、[计量场景](specs/cost_accounting.feature)、
[整书场景](specs/book_acceptance.feature) 是目标行为说明；文件存在不表示已通过。
已有无进展检查、AcceptanceRecord逐轮失败证据和patch机制继续复用，不另建账。

## 当前真实运行基线

- 开发分支基线 `e84a169b`；真实三书实验冻结代码仍为 `14312181`，不能追改其身份。
- 首书《三人成行》停在Stage4：源第2章命题 `b02_02.c1` 合法带全名，Design安排
  在目标第1章而称谓到目标第4章释放。Preserve自写statement没有该字面，却被
  旧二分路由误派并耗尽2/2轮。新规格归因须检查正式来源与目标选择，不改短句掩盖。
- 截至本轮开工，原账54次实际请求、168条缓存采用；prompt 725082、completion
  1162700，思考嵌套usage尚未进入现役付费账。人工只读补证不是账内已修复值。
  不在活动账固定随时变动的供应商价格；token、估费与账户扣款分别报告。
- 章观察首轮6/17通过，有界修订后15/17；第3、15章人工免费证据修正后17/17。
  Preserve已购响应曾免费投影回wire，Design曾人工覆盖默认keep表达，book修订后
  names经正式CLI恢复、17章免费重放。这些均保留为人工干预，不能改记全自动成功。
- 首书运行已结束，另两书尚未开始，正文与最终交付未完成；《三人成行》oracle未解封。
  本轮三书已有付费及全文外发至DeepSeek的单独授权，范围仍受原manifest、原根、配置
  与额度约束；新增配置对照、换根或追加额度须核对其是否仍在授权内。

## 已完成批次索引

| 批次／证据 | 已完成内容与限制 |
|---|---|
| STR-06关闭，`b3038a41` | R0～R6／R8／B0～B5及R10a～c落地；Stage6与旧链退役；终验接delivery manifest；存量解码面及退役台账删除。现役13节点，bind已退役 |
| R10c／B6-B成书 | r10b《夫君的报恩》交付hash `09d80fb1…`、delivery `71c54085…`；六维／GS-01～10 agent侧阅读通过。无人改稿、有人辅助完成；不代签R10d或本轮 |
| CS-0／CS-1／CS-patch | 引文确定性归一、命题渲染、关联全等闸、条目patch与失败聚合已完成；后续修正为“问题位置严格减少可继续，重复／循环停止”，源锚点单调硬闸已撤除 |
| CS-2已完成部分 | chapter瘦wire `stage2-chapters-v3`、确定性展开与条目定位；Preserve输入／wire和book面仍有余项，不把整个CS-2标完成 |
| CS-3已完成部分 | Design v7、real＋know分离、bind退役、body_design双产物、world规则id、rc14认知与confirms闭合；剩余Brief输入投影与真实费用／质量证据 |
| `8221ef2f`／`14312181` | rc14工程冻结：3581例默认非corpus通过；前端120例及typecheck／seams／build通过。三书免费源身份／分章预检通过，124次无修订基础调用；不证明成书质量 |
| `e84a169b` | 证据patch耦合范围、Windows长路径已购恢复、Preserve wire投影与先验型、names已证明级联准入、逐章freshness、keep表达诊断。定向集合见该提交原活动账；尚未做本隔离批最终全量回归 |

## 未完成项与决策边界

- **CS-2后续**：book观察仍同时使用全文排版与证据地址，不能未经语义充分性证明删除；
  `basis.quote`逐字要求不因省token取消。Preserve必要事实补全和Design Brief投影已闭合。
- **CS-4／CS-T**：节点thinking／effort对照与稳定输入前缀；不预设所有节点降低思考。
- **CS-G**：六维与现役Hook语义已对齐，评价工具v2区分工程／自动程度／质量／费用，
  定向反例通过；真实三书未执行。第三本书若无oracle，依据源书与同一六维评价，不伪造oracle。
- **R10d重复轮**：原计划含《夫君的报恩》独立重复生成／《三人成行》绝对验收；
  原三书manifest与该长期重复义务分别核对，不把一次三书运行记成重复轮已完成。
- **R7**：风格画像仅在一句一段、环境密度、幽默通道、POV漂移等既有批评复现且
  有阅读证据时提出具体方案；under-spec、过度失效、状态过建模、checklist prose、
  章内披露及表达力未验证继续登记。第一人称书不能代签第三人称稳定性。
- **工程余项**：同身份强制重采、一般REDO级联政策仍属既有产品裁决边界；本批只闭合
  已证明的消费链，不扩普通队列权限。并发默认4，先看真实429／unknown再调。

## 恢复、替代与删除条件

| 对象 | 有效义务与删除条件 |
|---|---|
| rc12／rc13五类产物adapter | `novel/store.py::_upgrade_rc12_payload`只迁移白名单无损信息；旧Brief缺认知模式免费重放Preserve，旧Design缺confirms走显式owner patch。存量全部重建或退役后删除此adapter及Str06Repository.read_graph_hook旧组件迁移，禁止无限新增版本 |
| OwnerIssue v1／v2读兼容 | v3显式dispatch_pending、指纹历史与更正记录；只经owner_issue_record_of_payload集中解码；所有存量v1／v2根只读归档／不再续跑后删除。旧错误路由账的更正按RC-15显式恢复，不重置轮数 |
| names／Characters旧信封 | names v2与两类信封v2显式拒识旧版；须新协议names重建后compile重建Characters，不推断空resolutions。原冻结根只读，授权新epoch再重建；无隐式adapter |
| 历史Step缺请求配置 | Step的CS-T加法契约集中在`contracts/schema/orchestration.py::Step`读取缺字段为None；展示为未知，不从默认配置推断旧请求。新执行和followcheck必须有同源配置。旧JobStart只读归档不再消费后删除缺字段兼容 |
| Golden评价v1 | v2显式拒识旧manifest／review／snapshot／result；按冻结六维重新收集、重评现有成书，不自动迁移旧结论、不要求为评价升级重购 |
| 候选身份冻结 | CS-R补齐信封版本与operator_redo；names／Characters信封v2、Hook有限owner修订改变候选身份。新身份由`tests/test_str06_f7e_freeze.py`冻结，旧根不追改；旧身份按不兼容只读拒识 |
| 字符串判据身份 | `novel/core/validation.py::validation_identity`集中解码仍现役字符串判据；材料问题已用结构化身份。剩余判据全部提供稳定身份后删除字符串前缀兼容，不在各节点扩散解析 |
| 可信owner修订 | 耐久修订证明＋基线／当前摘要闭合才准入级联；发现节点复验成功才resolved。names登记的准入只认已证明消费变化；unknown、不可读、INVALID不放行 |
| 已购证据与旧根 | 原始响应、checkpoint、正文、ledger不可当派生缓存删除。请求身份与接受条件相符才采用；`--check-only`零新购但可执行恢复写入，不称纯只读 |
| 章集合与交付恢复 | committed set及当前章包证明只补缺章；NameMap改动只重交付，Hook窄改不使无关正文失效；Stage1 e00仍参与源身份 |
| 已退役链 | 拒识legacy_incompatible，RETIRED_ARTIFACT_PATHS和盘面探测维持闭合；守卫`tests/test_retired_surface_scan.py`，不复活历史解码面 |

### 候选根运行记录映射与删除条件

B1迁移已完成，保留以下恢复索引：旧request一体记录由SemanticRequest＋ProviderAttempt
取代，validation由AcceptanceRecord取代，adopt由ContentRevision＋CommitBundle取代，
body／hook attempts由ContentRevision＋ProviderAttempt取代。旧admission平行计数已退出，
额度由现役工作单元、整书总闸和ledger承担，unknown也占额。旧文件仅留原根只读证据；
不恢复旧写路、不双写、不补造旧runtime/request文件。完整映射见重置前`e84a169b`本文件。

## R10d前置与冻结判据

- Workbench现有三本源书以显式NOVEL_DATA／NOVEL_CORPUS核验；NOVEL_OUTPUT是独立输出根。
  Golden Set v3.0《夫君的报恩》《三人成行》双格式已冻结，源sha与216＋139锚点曾核验。
  夫君为开发可见用书，三人成行oracle保持隔离，不能提前进入生成／修复材料。
- 冻结源版本、代码、模型、协议、预算、停止规则、评价方式与输出根；不得缩三书分母、
  择优替换失败轮或将恢复后的产物冒充独立新生成。旧冻结运行与新候选分别报告。
- 不做缺乏完整基线的新旧renderer付费对照；历史F5e原根已清理，只可引用历史读数。
  本轮分节点思考实验另冻结输入与配置；没有可比完整成书时不宣称整书相对改善。
- 评价以阅读为主，模型辅助另授权。只改评价过程时重评现有成书，不自动重生成。
  每个结论绑定精确成书hash，改文后重验受影响结论；工程通过不代签质量。
- 六维为Core Preservation、Structural Originality、Causal Continuity、Character
  Continuity、Natural Progression、Local Prose Coherence；Structural Originality按场景
  与语义承载比较，不要求源目标同号章对应。NameMap跨书与第二本表达力须独立证据。

### 人工阅读验收协议

九项观察继续用作六维证据：情节因果、揭示时点、章间衔接、人物声线、说明书式正文、
重复交代、行动对话展开、Hook吸引力与披露边界、整体节奏流畅。记录“章段→维度→
归因→处理→证据”，归因用现役Stage2／Preserve／Design／compiler／renderer／写手协议／
写手发挥；bind已退役。正文及细读记录留运行根，不在仓库摘录。未读保持unver。

## 成本与授权

成本权威为<base>/_service/ledger.jsonl。reasoning是completion子口径；未知不算零，
缓存采用单列，未采用回答、内容修订、网络重试与失败全部纳入成本。基础调用2N＋8
只表示13节点链的无修订工作量，不是费用上限。重启与continuation不重置额度；付费
显式`--yes`，外发单独核对。截断仍停item，不自动重试或降effort。

### 各付费节点token预期量级

旧B0／F5e／F9量级与逐次记录见`e84a169b`本文件历史表，仅作诊断参照，非现役报价。
已退役bind不再作为预算项。新量级由本轮CU场景下的账与补证逐节点重建；异常先回查
输入和协议。当前JSON字符下降只证明wire瘦身，未证明整书费用下降。

## 必要证据与批次记录

测试入口与消费者由`tests/suite_catalog.json`登记；定向通过不称全量回归。
本次按用户追加指令只做定向回归后提交；后续真实验收前的验证范围另行确定，golden与工程分别记录。
历史已清理根不可恢复；证据缺失报告未查／不可用，不补猜。
每批只更新本文件的当前状态、交付证据和下一步；长历史进Git，复用教训进LESSONS，
裁决缘由进DECISIONS。文档重置不清除历史失败或长期义务。

- CS-R／M工程验收：`.test_cache/cs-engineering-accepted.xml`，3655通过、0失败、
  0跳过，749.531秒；默认非corpus、4 workers。前端120例以及typecheck／seams／build通过。
  该证据属于前一子批；输入与配置后续改动不冒用此全量证据。
- CS-M原账只读审计：`outputs/cs_review/usage_audit.json`，54实际请求、168缓存采用，
  补证思考1036079；原ledger字节未变。sid必须是ledger的完整书目录键，查无项拒绝假零。
- CS-I定向：`.test_cache/cs-input-directed.xml` 114通过；离线同材料输入块测量见
  `outputs/cs_review/input_sizes.json`：names 25708→17141文本token（−33.3%），
  Design 18020→13485（−25.2%），Preserve 44818→48874（+9.1%，补齐语义）。
  不含完整prompt框架与schema，不是真实请求或费用对照；复测脚本与前后摘要同目录留存。
- CS-T配置贯穿：`.test_cache/cs-profile-process.xml` 275通过，包含HTTP上传→真实子进程→
  Stage7及重启读回，名称节点low／其余high均核对实际ledger；免费复检零新购。前端120例、
  typecheck／seams／build通过。旧仅改NODES的对照工具已改为正式请求配置入口。
- CS-G工具与返修统计定向：`.test_cache/cs-golden-directed.xml` 48通过，覆盖六维v2、
  人工与未知自动证据、配置错误、缺账／重复账、回派更正及正式内容轮排序。
  真实付费对照与阅读验收未开始；当前按用户指令只定向收口，后续验证范围另行确定。
- 本次已执行的回归留证：`.test_cache/cs-final-regression.xml`，3678例中3663通过、15失败、
  0跳过。失败为10例Step替身缺请求配置、3例测试变量误用、2例旧派生ID断言；已定位修正，
  不把该轮记为全绿。用户随后要求只定向修复验证，不再重跑全量。
- 收口定向：`.test_cache/cs-final-repair-directed.xml` 225通过，覆盖runner许可／证据v5、
  CLI与只读审阅配置、付费边界配置冻结、统计、文档和依赖分层。
  `.test_cache/cs-product-repair-directed.xml` 6通过，覆盖5个原失败产品入口用例及显式请求配置的
  真实CLI／账／免费重放。均使用fake transport、零真实付费，未重跑全量。
