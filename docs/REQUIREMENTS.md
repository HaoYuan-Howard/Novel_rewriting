# 活动账：返修归因、成本与整书验收

本文件是唯一活动计划，只保留当前状态、未完成项、长期义务、删除条件和证据索引。
行为依据为 [BACKEND_CONTRACT.md](BACKEND_CONTRACT.md) 与 contracts；目标架构见
[str06.md](schema/str06.md)，本轮规格见 [repair_convergence.md](schema/repair_convergence.md)。

**2026-10-04 重置**：用户确认按“规格定稿 → 归因与返修闭环＋计量修复 → 工程验收 →
输入瘦身与思考配置对照 → 整书自动运行及质量验收”推进。重置不清账、不删输出，
不改变冻结实验身份、分母、额度或既有授权。重置前完整记录见提交 `e84a169b` 中本文件；
更早执行史按 `git log -- docs/REQUIREMENTS.md`，裁决见 [DECISIONS.md](DECISIONS.md)。

**2026-10-04 关联同义判据对齐**：用户裁决措辞不同的同义表述也算同一。
撤除 CS-1 跨章 statement 全等闸，保留 associate 的同义提示词；正式引用统一
采用最早建立的代表文本，同章重抄仍核对本章原文，引用时序与证据闸不变。
协议及产物形状不变，语义未审继续披露。最小 compiler 反例先复现拒收；修复后
契约§9.5五文件402项与受影响Stage2／Stage4消费、免费恢复及来源账324项定向通过
（`.test_cache/associate-synonym-contract.xml`／`associate-synonym-regression.xml`；
4项目标烟测包含在后者中，不重复计数）。《夫君的报恩》已购关联回答经正式
`--check-only`采用并免费编译，11章、未决0，新增购买0，整书额度仍7/512；
留证`outputs/cs_review/associate_synonym_free_recheck.json`。随后沿用户已授权的同书、
同配置续跑（run `1791116266-e0289104`，代码身份 `414b6203…-dirty.c77e77bed629acdd`）：
Preserve通过（62项义务）；Design首答＋两轮修订仍有两处未释放专名，正式
`validation_rounds_exhausted`停止，未到Stage7交付。源书对照另发现ch03把夫君
模仿表弟称谓的场景误记为芸娘与表弟已发生关系，Preserve ob18又锁为必保；
须先修正源观察及其派生产物，不能靠修掉Design字面泄露就验收通过。
本次追加4次实际调用，整书额度11/512，原响应与成本留证完整；两个admission
已drained，claim／open beacon均0。结果记录`outputs/cs_review/associate_synonym_alignment_and_acceptance.json`。
成书六维保持未验，不把Stage2机械通过算作语义通过；不扩大到另外两书，不重置任何额度。

**上一CS检查点交接（2026-10-04）**：该检查点按当时指令只完成修复、定向回归并commit，不再重跑全量。
在付费前停止；用户先处理其余流水线潜在问题，之后再进行真实整书验收和A/B。
本提交是工程检查点，不是最终冻结发布；现有实验草案没有付费或外发授权。

## 当前状态与下一步

2026-10-04 Stage4停点收敛批（CS-F）：按用户审阅收缩为现有职责内的完整修复，
最终方案见`docs/schema/repair_convergence.md`“Stage4停点解决方案”，中文Gherkin
见`docs/specs/repair_convergence.feature` RC-30～49。已复验三轮Design为299→8→2条
错误；内存消除末轮两处名称冲突后，真实写手材料预检仍有14处Preserve来源问题。
ch03主体误判及ob44无依据认知限制分别归源观察和Preserve，不用改目标稿掩盖。
事实锁改为引用已有目标事实；人物呈现复用现有登记投影；不新建通用验收框架、
不增加工作流节点、不顺带引入任意目标称谓创作功能、不提高重试上限。
用户明确选择**先离线标定，再启用自动语义闸**；结构修复和完整预检先行，生产
语义继续按现役unver披露，未完成标定／启用验收前不自动增加付费语义核验。
本批方案不扩大真实付费／外发授权；旧响应、费用、轮数和失败根保留。

| 子批 | 状态 | 完成条件 |
|---|---|---|
| CS-F0 最终方案与规格 | [x] RC-30～49已落文，引用及文档入口检查通过 | 规则、正反例、职责、消费者、恢复和分项验收边界完整；文档通过不算行为通过 |
| CS-F1 完整候选预检 | [~] RC-44～45、RC-47已实现并验证（含模型真回`patches`的stage用例）；读取／确认闭合等未转路径的错误类仍整份修订，Hook设计不接局部修订 | 能构造的候选聚合独立问题，结构缺失明确未运行；真实body_design／恢复消费者及局部修订定位闭合 |
| CS-F2 事实引用与名称呈现 | [x] 事实锁引用（`stage4-design-v8`，rc16）与Writer称谓投影（IR rc16／renderer v10）已实现并验证；**Hook材料尚未投影**；破坏协议已显式升版，旧rc15根拒收 | 删除重复kf状态，正式绑定及结构／语义结果分开；呈现层按已证明人物绑定投影，Writer／Hook／Stage7消费闭合，破坏协议显式升版 |
| CS-F3 关键语义离线标定 | [ ] 用户已选择先标定 | 高影响主体／知情及目标事实对应含正反例；支持、矛盾、未确定及漏选／误拦分别报告；通过后才进入启用验收 |
| CS-F4 本书正式恢复 | [ ] 工程稳定后执行 | 正式owner修订源观察及无依据限制，审计62项义务重复／过保，按实际依赖重建，历史与人工干预留证；版本及付费权限先核对 |
| CS-F5 成书与成本对照 | [ ] 不以Stage4继续运行代替完成 | 相关目标章节与最终正文核验，六维与自动程度分别报告，核验新增成本和返修节省量同口径比较 |

CS-F1首个子批（2026-10-04，基线HEAD `414b6203`＋本工作树未提交变更）：

- `novel/stages/stage4/preflight.py::inspect_design_candidate`统一生产接受与免费Design
  预览；可构造但不通过的Design继续派生Binding及实际Writer材料，正式发布仍失败关闭。
  Design、承载标注、Binding、执行计划、命题引用、Writer材料、Guard和Hook各有检查状态；
  依赖失败标not_run，悬空命题不再进入IR投影抛裸异常。无正式Design／Binding／Hook的
  停书也能免费预览，已有Hook则与修订候选重新核对。
- 上游材料错误优先回派，Design旧输入上的问题留在完整报告，不提前消耗Design回派轮。
  全报告在停止上下文中存一次；不复制进每条issue及后继prompt。新增正反例检查实际
  工作流状态、双产物发布、失败不发布、未改输入、免费重放与已有Hook失效。
- 20项首轮、571项消费者与契约、1项目录登记检查全部通过，合计592项定向用例，
  0失败／0跳过。JUnit分别为`.test_cache/cs_f_preflight/initial.xml`、
  `.test_cache/cs_f_preflight/consumers.xml`、`.test_cache/cs_f_preflight/catalog.xml`。
  首轮文件为`tests/test_str06_candidate_preflight.py`及`tests/test_str06_design_slim.py`；
  消费者文件覆盖material_repair、r3_selfheal、item_patch、a4_design_locks、a5_positions、
  hook、r10b_writer_packet、cli_dispatch、layering及BACKEND_CONTRACT §9.5五文件；目录检查
  为`tests/test_test_selection.py::test_catalog_covers_every_module_and_every_override_is_a_real_case`。
  公共命令为`D:/Miniconda/envs/novel_refactor/python.exe -B -m pytest <上述文件或节点> -n 0 -x
  --basetemp=E:/_tr_preflight -o cache_dir=.test_cache/cs_f_preflight --junitxml=<对应报告>`。
  固定根E:/_tr被另一批回归占用，本轮独立根与独立缓存经用户明确授权；初次失败的目录占用
  与测试接线修正不计为通过。未做全仓终验，测试均使用fake transport，零真实付费。
- 本书三份未修改的已购响应按当前纯预检回放，直接错误仍为299／8／2条，材料出现位置
  为500／14／17处；末轮17处中14处归Preserve、3处归Design。不能把位置数当独立根因数。
  记录在`outputs/cs_review/cs_f1_candidate_preflight_replay.json`，包含原响应／输入摘要及
  代码摘要；原书正式产物、响应与费用未改。此证据不代表源主体、事实语义、成书质量或净
  成本已通过。剩余事实引用与呈现投影、局部patch、离线标定及本书正式恢复按上表继续。

CS-F1第二子批与CS-F2（2026-10-04，基线HEAD `414b6203`＋本工作树未提交变更；零真实付费、零外发）：

- **事实引用（RC-36／38）**：Design wire升`stage4-design-v8`，在已选状态上写`state[].lock`引用事实锁，kf不再是状态键；
  `FactLockBinding`（锁＋承载拍＋状态地址，不复制value）随body_design同组发布进`SemanticBinding.fact_bindings`。
  时态锁须标在同modality的set、值未知锁须标在op=unknown、原文未回答锁不承载状态、一锁一承载、悬空即拒；
  未兑现的必保锁带规则码与承载该义务的拍的wire路径，并用Design看得见的`obNN`指代义务。**只证结构**：承载状态是否在语义上满足锁
  （如把暂缓和离绑到赏花）属CS-F3，未运行保持unver，不得宣称已核验。
- **称谓投影（RC-40～43）**：`ChapterIR.withheld_bindings`（本章尚未释放、且已证明唯一绑定人物的完整登记称谓，附包含它的更长称谓）让写手材料
  正向行（含禁区行）把该字面换成本场已释放称谓；短姓、关系称呼、多义字面、未登记表达（如源文没有的「陆郎」）与源引文永不投影；人物本场没有已释放称谓时
  不投影、泄漏如实保留并按原归因回派；留痕`name_projections`（候选预检报告、guard report、packet_metrics），原字段与来源文本不改。Design自有文本的名称冲突
  仍由Design接受判据拦下，compiler不自动提前释放全名；Stage7无需改动。**Hook材料尚未投影**：它的来源归因依赖原字段文本，需单独设计。
- **局部修订（RC-47）**：`novel/stages/stage4/design_paths.py::locate_design_patches`按判定处携带的wire路径取最小完整父项——叶子标量升到所在对象，同拍两处以上待修合并为整拍，
  替换不重叠，场景自身标量原地；合并后重跑**完整**候选预检，无进展／循环／累计上限沿用现役规则。已带路径的问题：wire形状、展开、事实锁、expression_refs、人物／称谓闭集、
  Design自有文本名称泄漏；其余（全局real覆盖闸、读取／确认闭合、上游派生材料、越界路径）任一出现即整份修订。
- **版本后果（需知）**：`STR06_RC_VERSION`升rc16，`semantic_binding`与`writer_book_ir`升rc16，冻结身份`FROZEN_CONTRACT_IDENTITY`更新为`4e643fb1…`。候选契约身份变化，
  `cs_golden_20261004_rc15`下三本旧根按契约表漂移关闭式拒收（`init_or_resume_candidate_workflow`），保留为冻结失败证据，**不能原地续跑**；续跑须新根，
  已购Stage2／Preserve响应能否经历史采用按请求身份免费复用，属CS-F4核对项（本批未改任何Stage2／Preserve提示词），Design因wire与提示词换版必须重购。
- **真数据对照（零模型调用）**：`outputs/cs_review/cs_f2_fact_lock_replay.json`。《夫君的报恩》第三份已购Design回答含8条以kf为键的重复状态行（对应5个不同的锁，多个义务共用同一锁）；
  删掉这些行并换v8字面量后，Design自有泄漏3→0、写手材料泄漏17→14，余下14处全是Preserve编造的源文没有的短姓称呼（陆×13、裴×1），按设计不可投影，须Preserve的owner修订（CS-F4）；
  另有8条「事实锁未兑现」是预期结果。这是模拟，不证明模型会正确标注lock，也不评语义。输入摘要前后一致，原根产物、响应与费用未改。
- **测试与迁移**：新增`test_str06_fact_lock_stage.py`、`test_str06_name_projection.py`、`test_str06_design_patch.py`，并扩充`test_str06_a4_design_locks.py`（CS-F2一节）、`test_str06_design_slim.py`、`test_str06_contracts.py`；
  旧用例按新职责迁移而非放宽：v5中间形状用例（kp知情锁／形状）改用不含事实锁兑现分母的Brief变体，事实锁语义全部迁到真实v8入口；`str06_faults.knowledge_release`原靠「源命题里的完整登记称谓」制造泄漏，
  该情形现被确定性投影消解，改为会真实触发Design修订的Design自有文本冲突（自愈、产品入口、语义交付三处共用，覆盖不变）；`material_repair`夹具默认改为夫君第1章无可用称谓，另有投影正例；
  修订提示改为条目返修后，产品入口用例按`【条目返修】`断言（整份覆盖闸仍断言`【修订要求】`）。版本冻结与渲染器版本钉同步为rc16／v10。
- **验证**（命令与根见下；全部使用fake transport，固定根`E:/_tr`当时被另一会话的全量回归占用，本轮用独立根`E:/_tr_f2`与独立缓存`.test_cache/cs_f2`）：
  按本批改动集合选测回归**3748项**（147个模块，`--changed`选择19个改动文件，`-m "not corpus"`，`-n 4`；`regression_full.xml`、选择报告`regression_selection.json`）：
  通过3745／失败3／跳过0。三项失败都是本批收尾记账缺口（`docs/schema/str06.md`符号引用用了短路径、`test_str06_r2_engine`仍钉渲染器v9、
  冻结身份哈希未随`writer_book_ir`升rc16同步），已修复；**修复后没有重跑全部3748项**，而是重跑受影响的468项（Design提示词与消息、渲染器版本、冻结身份、
  产品入口Design故障用例等22个测试目标，`post_fix.xml`）：通过468／失败0／跳过0；契约§9.5五文件402项通过／失败0／跳过0（`s95_final.xml`）。
  回归命令：`D:/Miniconda/envs/novel_refactor/python.exe -B -m pytest -n 4 --dist loadgroup -m "not corpus" --basetemp=E:/_tr_f2 -o cache_dir=.test_cache/cs_f2 --changed <19个改动文件>`。
  另做两个不进仓库的对抗式探针（随机wire／错误／称谓文本）：定位器4000例（683例可定位，无重叠、覆盖完整、确定、不改输入）与投影6000例（可逆、确定、不拆更长称谓），均通过。
- **未完成与限制**：未花真实付费；v8提示词与`lock`闭集在真实模型上的表现**未验证**（CS-F3／F4用真实调用时才会知道模型是否稳定标注）；未做离线语义标定；本书未恢复；
  Hook材料投影、读取／确认闭合的路径化、局部修订接入Hook设计均未做。

2026-10-04证据寻址批（CS-A，RC-26～29，rc15，用户批准）：cs_golden三书全部停在
`str06.stage2.chapters`，根因是证据切分只在「。」后切、段号标签悬在上一段下方，模型
系统性认错段号（61份被拒回答52份只因段号；返修轮约占Stage2输出token四成）。按
“规格与反例→实现→免费验证→付费补跑”推进：证据段改为一个自然段、一段一行；book／
chapters wire的basis只给引文，段号由Resolver定位；src数组元素可为区间；patch轮整份
回答按整份验收。携带段号的观察换字面量（book v4／chapters v4／names v3），三类观察、
Characters、Blueprint升rc15，旧根按契约表漂移拒读。旧根已购观察的复用由仓外一次性
脚本按字符位置换算后经现役`--wire-dir`采用入口进新根，不进产品代码。10项反例先红后绿
（`.test_cache/evidence-addressing-red.xml`／`-green.xml`）；按共享接受循环、契约版本与
共享提示词的影响跑默认非corpus回归：3721项中3720通过，余1项为读面测试钉的旧版本号
（Characters rc12→rc15，本批有意变更），改后该模块重跑通过（`ea-full-2.xml`、`ea-views.xml`）。
新根`outputs/cs_golden_20261004_rc15`：stage0/1与旧根逐字节一致；三书book观察与43章换算
采用（0付费）；授权补跑7章共8次实付（6章首答通过，三人成行ch17一轮整拍返修后通过），
输出15.3万token；旧账同7章19次实付、42.5万token且6章失败。三书章观察节点完成、失败
清单为空。names／associate从未在旧根运行，stage2编译完成还需另行授权。本批未运行
golden set或成书验收；补跑运行的代码身份为未提交工作树（R-11.23），采用模式下来源账的
configured model记为unknown（实付行的模型见账本）。

2026-10-04职责边界追加批：按“复核基线→RC-18～25职责规格→真实入口反例→
完整调用链修复→工程/成书分别验收”推进。Design完整义务投影已存在，保留回归；
已实现章观察回派、关联问题身份、Hook语义送达、修订权限、顺序/时点指令及
六条摄入深依赖清除。用户已选择共用纯逻辑下沉独立底层模块。工程验收完成：
默认非corpus3700项通过；章观察首轮close规则及超过99章的定位收口后466项定向通过。
本追加批尚未运行真实付费或成书验收，旧批次通过数不作为本批证据。
规则、场景、反例、实现与消费者的对应见`docs/schema/repair_convergence.md` RC-18～25表。
本批横跨共享接受循环、摄入原语、Repository及恢复消费者，在定向闭合后执行一次默认非corpus
回归，按共享基础设施影响与本批工程收口门槛执行；不是重跑上一CS检查点的验收。

2026-10-04审阅追加修复（基线`3e7215d7`）：章内先close后open左移至同源章观察
判据，已存错误按chapter scope回派；重复操作按同类出现序号区分诊断身份，
整份／patch接受循环及跨owner指纹识别问题2→1→0。源观察、正式schema和API形状不变，
章观察prompt同步顺序要求，旧重复close误派更正仍保持专用证明边界。
新增8项反例先红；修复后含新增反例、Stage2发布/接受、修订恢复及§9.5五文件的
601项定向通过（见下方留证）。用户随后授权提交、合入main并push，按根指南合入门槛
补跑最终代码集合的默认非corpus回归：3709通过，前端120项测试及typecheck/seams/build通过。
这是工程合入验证，未运行golden set或真实付费成书验收。

2026-10-04 CS-T冻结清单：生成代码基线仍是`497d0a38f22a549b34698b768bbd606b98161502`，
不追改`14312181`。总槽上限、占槽读取和节点并发配置还有未提交改动。按 R-11.23，
带未提交改动的工作树可以启动付费跑书：代码身份记为`<commit>`或
`<commit>-dirty.<digest>`，unknown 仍拒绝；运行中再改工作仓按 `code_drift` 滚 epoch。
控制配置：DeepSeek、`deepseek-flash`、thinking on、effort high。
`str06.stage2.chapters` 与 `str06.stage5.body` 的节点并发各 32，整书总槽 32，
供应商总槽 200。11 章同时在飞的请求是 min(32, 11, 32, 200)=11。串行节点仍是一次一调。
这三项都是 1～200 的整数，清单里的 32 可以再改。唯一实验变量是
`str06.stage2.book`、`str06.stage2.chapters`、`str06.stage4.preserve`、
`str06.stage4.body_design`四节点的 high／low；思考保持开启，其余节点不降档。
停止规则沿用现役常量：整书内容调用512、每内容轮最多4次、owner修订2轮、
跨节点回派每书12次、输出上限131072；截断、无进展、unknown和额度耗尽都停止，
不自动切档、换模型或追加额度。开发书为《夫君的报恩》11章，制备停在
`str06.stage5.hook`之前，无修订预期17次；加上四节点对照臂共45次，这是预期不是上限。
三书章标记为17／22／11，后置整书无修订预期124次。三本源sha256已复读，与原账中
《三人成行》`58ebf088…`一致。四个目标节点的正式输入尚未产生，不能提前锁请求摘要。
原授权只覆盖`outputs/rc14_acceptance`、代码`14312181`、全节点high和总槽4／8，
已用54／512。新根、新代码、总槽32／200和low臂不在其内。未创建实验目录，
未发付费或外发请求。清单在`outputs/cs_review/experiment_plan.draft.json`。

| 顺序／批次 | 状态 | 完成条件与下一动作 |
|---|---|---|
| CS-S 规格定稿 | [x] 中文Gherkin与职责规格定稿；RC-17按用户裁决增补 | RC／CU／BA场景成为本轮验收输入；实现映射随代码补齐 |
| CS-R 归因与返修闭环 | [x] 工程路径闭合；真实书待新冻结运行验收 | 正式来源路由、同判据前移、有界收敛、精确级联与旧账显式更正；RC-17名称同一性按用户裁决执行 |
| CS-M 计量修复 | [x] 新购、恢复、缓存与只读补证通过工程验收 | 原账54次思考已只读补证；旧账不回填、不改变估费总输出 |
| CS-E 工程验收 | [x] 前一子批3655例通过；本检查点按用户要求定向收口 | 本轮225＋6项定向通过，前端全项通过；最终冻结与真实验收等其余问题修复后再进行 |
| CS-A 证据寻址 | [~] RC-26～29实现、反例与默认非corpus回归通过；三书章观察补跑完成（7章8次实付） | 下一步names／associate／compile（需付费授权）；完整首答通过率等下一本新书实测 |
| CS-I 输入瘦身 | [x] 必要输入投影与真实消费者定向通过 | 名称证据去重，Design完整投影；Preserve补齐原先遗漏的语义后体积增加，不能记作降费成功 |
| CS-T 分节点思考对照 | [~] 冻结清单已钉；章观察与正文节点并发各 32，整书总槽 32，供应商总槽 200，并加入 stage2.book 的 high／low | 节点正式输入等制备通过后再锁；新根、新代码、总槽和 low 臂须另获付费与外发授权 |
| CS-G＝R10d／B6c 整书验收 | [ ] 按用户要求后置，未发起新付费 | 六维工具已对齐；等其余问题修复后冻结三书并真实执行，人工与自动分别报告 |
| R7 质量优化 | [ ] 持续登记，按实际阅读反例启动 | 漏保／过保、因果、承接、声线、导演说明、重复与输入不足；本轮已覆盖的输入／成本项不重复立项 |

[返修场景](specs/repair_convergence.feature)、[计量场景](specs/cost_accounting.feature)、
[整书场景](specs/book_acceptance.feature) 是目标行为说明；文件存在不表示已通过。
已有无进展检查、AcceptanceRecord逐轮失败证据和patch机制继续复用，不另建账。

## 当前真实运行基线

- 本职责追加批开发基线为`2ac0f83e`加当前worktree改动；上一CS批起点为`e84a169b`。
  真实三书实验冻结代码仍为`14312181`，不能追改其身份。
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
| CS-0／CS-1／CS-patch | 引文确定性归一、命题渲染、条目patch与失败聚合已完成；后续修正为“问题位置严格减少可继续，重复／循环停止”，源锚点单调硬闸已撤除；关联全等闸由2026-10-04同义命题裁决撤除 |
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
| rc14及以前的旧段号观察 | 证据段号按「。」切分寻址，与rc15自然段寻址不兼容；候选契约表漂移即拒读，不建仓内迁移。cs_golden三书的复用只经仓外一次性脚本按字符位置换算、现役`--wire-dir`采用入口逐条全量复验，原付费attempt与账本行记入脚本清单；脚本不入库，无仓内删除对象 |
| names／Characters旧信封 | names v2与两类信封v2显式拒识旧版；须新协议names重建后compile重建Characters，不推断空resolutions。原冻结根只读，授权新epoch再重建；无隐式adapter |
| 历史Step缺请求配置 | Step的CS-T加法契约集中在`contracts/schema/orchestration.py::Step`读取缺字段为None；展示为未知，不从默认配置推断旧请求。新执行和followcheck必须有同源配置。旧JobStart只读归档不再消费后删除缺字段兼容 |
| Golden评价v1 | v2显式拒识旧manifest／review／snapshot／result；按冻结六维重新收集、重评现有成书，不自动迁移旧结论、不要求为评价升级重购 |
| 候选身份冻结 | CS-R补齐信封版本与operator_redo；names／Characters信封v2、Hook有限owner修订改变候选身份。新身份由`tests/test_str06_f7e_freeze.py`冻结，旧根不追改；旧身份按不兼容只读拒识 |
| 字符串判据身份 | `novel/core/validation.py::validation_identity`集中解码仍现役字符串判据；材料、章内悬念冲突与关联问题已用结构化身份。剩余判据全部提供稳定身份后删除字符串前缀兼容，不在各节点扩散解析 |
| 旧associate误派更正 | `novel/stages/issue_recovery.py::_legacy_loop_correction`只从完整正式章观察重建历史重复close错误，证明旧问题全部覆盖后才显式superseded；不解析文案猜owner、不退费、不重置轮数。旧字符串association_undecided账全部只读归档且不再恢复后删除该兼容分支 |
| 六条摄入深依赖 | RC-25已删除原Stage实现与测试白名单；分章、摄入审计、Stage0放行判据分别唯一位于`novel/core/partition.py`、`ingest_audit.py`、`eligibility.py`，不留转导出adapter |
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
此前CS检查点按当时指令只做定向回归后提交；本职责追加批的工程回归单列如下，golden与工程分别记录。
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
- 职责边界RC-18～25：`.test_cache/role-boundaries-regression.xml`，默认非corpus
  `-n 4 --dist loadgroup -m "not corpus" --basetemp=E:/_tr`，3700通过、0失败、0跳过，
  779.70秒；含真实服务上传→CLI子进程→交付与重启读回。4条为同一外部Starlette/httpx
  弃用提示，无新增依赖变更。全程fake transport、零真实付费，不代签成书质量。
  先行定向证据：`role-boundaries-consumers.xml` 780通过/5未选中（含§9.5五文件），
  `role-boundaries-recovery.xml` 204通过，`role-boundaries-final-directed.xml` 29通过，
  `role-boundaries-closure-final.xml` 153通过；各集合有交集，不相加为验收分母。
  本批未改变前端与HTTP响应形状；API摄入消费者已纳入工程回归，未重跑前端构建。
  全回归后的章观察提示语同步由`role-boundaries-prompt-closure.xml` 215通过验证；
  随后发现同一回派消费链的`ch100`产物定位失败，补入RC-19，红例为
  `role-boundaries-scope-red.xml`。该局部补充按精确恢复消费者与契约定向闭合，
  不把3700例记录冒称包含后加的反例。
  最终收口：`.test_cache/role-boundaries-final-closure.xml`，466通过、0失败、0跳过，
  55.08秒；覆盖RC-18～25模块、回派自愈、条目patch及契约§9.5五文件，`git diff --check`通过。
  章内重复close的两处发现入口均验证真实章owner修订、无关章免费采用、最终compile发布与重启读取；
  旧associate误派更正经真实CLI预览／提交，保留原账及耗尽轮数。真实成书质量保持未验收。
- RC-18～20审阅追加（`3e7215d7`＋工作树修复）：
  `.test_cache/role-boundaries-followup-red.xml`新增8项失败；首次修复验证为7通过／1失败，
  后者为测试末尾未重建过期的associate，已改为沿正式names→associate→compile恢复路径。
  `.test_cache/role-boundaries-followup-directed.xml`601通过、0失败、0跳过、82.51秒，
  命令为`-n 0 -x`＋`test_workflow_role_boundaries`、`test_str06_r3_selfheal`、
  `test_str06_item_patch`、`test_str06_chapter_slim`、Stage2 owners/protocol/publish/source、
  `test_str06_invariants`及契约§9.5五文件，统一`--basetemp=E:/_tr -o cache_dir=.test_cache`。
  同拍／跨拍倒序、合法跨章close、整份／patch的部分修复与免费重放、两个发现入口的
  章owner修订和无关章采用、正式回派账的指纹与累计轮数、compile发布和重启读回均已覆盖。
  全程fake transport、零真实付费；本轮未运行golden set或默认全仓回归。
- 合入main前最终验证（修复提交`f538a480`）：
  `.test_cache/role-boundaries-main-regression.xml`，默认非corpus回归
  `-n 4 --dist loadgroup -m "not corpus" --basetemp=E:/_tr -o cache_dir=.test_cache`，
  3709通过、0失败、0跳过，820.24秒。相对本地main的待合入范围含此前分支提交，
  因此按最终代码集合验收，不把局部601项定向通过代替合入闸门。
  前端`npm run test`为25文件／120用例通过；`npm run typecheck`、`npm run seams`、
  `npm run build`均通过。seams核对100文件、7份冻结mock，构建147模块。
  前端共享node_modules的Vite临时目录在沙箱内曾报EPERM，已在沙箱外按正式命令通过。
  全程fake transport、零真实LLM调用；这些工程证据不代签golden set成书质量。
