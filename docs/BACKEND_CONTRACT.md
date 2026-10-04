# 小说重构系统 · 现役后端契约

本文件保存 R/Q/B、守卫归属与修复路由。它由 `contracts/requirements.py` 解析，主要供
`tests/test_coverage.py`、`tests/test_docs_references.py` 检查需求与实现的关联；生产判据由
`contracts/rules.py`、`contracts/judges/` 及各层校验器执行，模型请求不全文加载本文件。

维护时先按节点或稳定编号定位相关节，不通读全表。待办见 `docs/REQUIREMENTS.md`，当前形状见
`docs/schema/`，关键裁决及证据限制见 `docs/DECISIONS.md`。这些关联检查不能证明模型的语义质量。

本文件对应现役契约版式 `str06-rc16`（`contracts/schema/workflow.py::STR06_RC_VERSION`；
Design 模型面 wire `stage4-design-v8`，CS-F2 事实锁引用与称谓投影）。R9（2026-09-30，
D8 修订二）当时把消费面定为 rc11：book-v2 的源表达经 Blueprint v14
与源盲 Brief 到 Design v4；正文 narration 约束场景 POV，表达 keep／change 的最终陈述
进写手导演说明，改变理由仅审计。承接锚按 transition 明确定位较早事件；紧接必须是
上章末拍，倒叙必须早于本章。本人释放面包含已登记家族构词，正文／Hook 共用禁用推导，
Stage7 实际替换。计数统一为非空白字符；验证前移、材料终检与免费修订／重采恢复
共用生产函数。修订级联只认精确产物摘要与封存输入边，不扩大既有源观察 owner 的自动
重派策略；已落地 exit 1 的相同动作无盘面进展时停止。R9 之后 R10／CS-3／RC-26～29／
CS-F2 各批沿革与现役字段、错误投影见 `docs/schema/str06.md` 相应各节，
付费、正文外发与成书语义核读仍分别满足 R-12 与 B 边界。

## 阅读与修改接口

- R 是功能要求；Q 是跨层工程约束；B 明示尚无可靠机械守卫或按设计交人的边界。
- 注册 Rule 只能指向现存 R，Q 可解释理由。改编号需查全部引用，不能放宽规则正则来跳过分母。
- R 标题、三列表格、Q 标题、B 行首及“谁在查”记号是解析接口，具体语法与验证见 §九。
- 结构/载体与 HARD 等判据职责分行登记；需要拆条时顺延字母后缀，不复用旧编号。
- 新规则同时告知 producer，并在真实判据入口设反例；只写 prompt 不是已守住，只登记 Rule 也不是质量已验。

| 谁在查的记号 | 含义与要求 |
|---|---|
| HARD / INFO / 闸门 / 度量 | 有注册 Rule；度量只报读数，不作放行或返工结论 |
| 载体 | 类型/字段构造约束，必须点名载体 |
| 结构 | 代码结构约束，必须点名可解析的路径与符号 |
| 不适用 | 按定义不形成机械检查义务 |
| 见某个 R 编号 | 职责转授，目标必须存在，记号只在格首生效 |
| 人 | 按设计交 typed 人工裁决 |
| 待建 | 尚无实现，完成后更新归属；活动工作仍只维护在 REQUIREMENTS |
| 无守卫 → B 编号 | 明示盲区，不能把未校验当通过 |

记号取值唯一见 `contracts/invariants.py::WHO_CHECKS`。解析器按字面识别，“谁在查”的散文不要
无意带入 HARD、INFO、闸门、度量、载体、结构或不适用，避免误声明额外职责。

证据不足保留 UNVER；历史 Stage6 双盲 judge 与 typed 人审闭合已随 Stage6 整层退役，
现役机械验不产生自动语义 PASS/FAIL，语义未审以 `unver` 披露。
历史语义 evaluator profile 和 Stage5 自动收缩臂已经退役，不能通过整理文档恢复这些入口。

## 一、命名体系与章的唯一键

系统单人、本机使用，将中文小说处理为保留情节骨架的重写新书。界面与人工验收范围见
`web/AGENTS.md`，工程运维见 `docs/OPERATIONS.md`。

产品为全自动单模式（2026-09-15 全自动裁决，批次 B0–D）：上传即授权开跑，确定性
orchestrator 推进，semantic unit 生成；Stage 页只读监控。`HUMAN_NODES` 为空；命中
`approval:*` waiting_on 视为编排 bug（fail-closed）。Stage6 整层已随 STR-06 切换退役
（三候选改选面不复存在；Hook 的必要选择进 Stage4 正式 Design）。受托 Commander 审阅
角色已退役（见 `docs/DECISIONS.md`）。开发维护 agent 不作为流水线角色。执行与恢复归
运行代码，最终交付不生成或依赖 Markdown 报告，状态与证据在前端查看。

- `chapter_ordinal` 是阅读序整数；输出章号恒等于源章号，不合并、不删除、不拆分、不重排
  **章**；允许跨章重构——场景可跨源章重组（`R-5.2`，STR-06 翻转后 `I-40` 的同款表述）。
- stage 表示产物/schema/报告层，node 表示作业，二者不是一一对应。节点唯一登记在
  `contracts/invariants.py::NODES`（F8a 起 `str06.` 前缀新链节点；B2 五节点重建后
  为 13 个——CS-3 bind 退役后），label 和 `STAGE_SECTIONS` 从注册表派生，不手写第二套映射。
- 作业 id 为 `<stage>.<动词>`（新链为 `str06.<stage>.<owner>`）；复检是 `--check-only`
  flag，不造 check 节点，Stage3 无现役产物或节点。
- Stage5 各章独立成文（D2 章包写手，经 `novel/model_slots.py` 共享书级与供应商
  总槽并发，默认 4），唯一正文落 `stage5/body/chNN.txt`（章级回执
  `stage5/acceptance/body/chNN.json`）；恢复按有效提交集合补缺，不重购已有章。
- <a id="enums-superset"></a>`/api/enums` 从当前 invariants 生成；前端 fixture 逐项对账。
  反例见 `tests/test_api_enums.py::test_the_web_enums_fixture_is_the_same_payload`。

不能从 stage 编号拼 R 编号：Stage2/4/5 各有多节点或多相职责。
`tests/test_coverage.py::test_stage_sections_is_not_the_naive_stage_number` 保留该反例。
旧输出只可与冻结旧代码配对，不原地迁移、选写作臂或触发付费重建。

## 二、功能需求 R-0 ~ R-14

### R-0 · 非正文剔除（stage0）

免费识别非正文并落盘；机械校验（含正文占比闸）通过即由 producer 写入 auto 批准（`system:stage0-auto`，2026-09-15 全自动裁决），进入 Stage1。剔除的语义正确性（番外盲区，`R-0.7`）没有确定性防线，随交付披露（Q-13）。`stage0.approve` / `stage0.edit` 节点已退役（非现役节点，关闭式拒识）。

| # | 要求 | 谁在查 |
|---|---|---|
| R-0.1 | 自动剔除用的是 `R-1.7` 的同一套判据与同一张 `NONBODY_KINDS`——**只有一份** | `HARD` |
| R-0.2 | 剔除逐段只记不可再生的决定与最小定位证据：类型、标记行、原文码点区间、来源。行号、字数与预览从原文和 span 现算；**番外体不进产物目录** | `HARD` |
| R-0.3a | **【v3 收窄】** 剔除语义正确性仍无人可机械证明（见 `R-0.7` / Q-13）。闸门不再是人工 `approved`，而是 transform 的 **mechanical admission**（`status=passed`）。公开检查单从当前 transform、执行资格与 freshness 只读投影。API 仍可投影 `revision_id`（= `transform_id`）供兼容读面 | `结构`（`novel/core/eligibility.py::is_stage0_executable`）+ `B-20` 披露面 |
| R-0.3b | **未获 mechanical admission 的书，stage1 必须拒绝启动**；执行资格的唯一落点是 `novel/core/eligibility.py::is_stage0_executable`，消费方一律 import、**不许复述条件**。v3 首跑即写入 admission；缺 transform / 损坏盘面须重跑 `stage0.remove` | `HARD` |
| R-0.4 | 【已退役 2026-09-15】人工编辑剔除范围随 `stage0.edit` 退役；判据漏掉的那类（语义误剔/漏剔）转为 `R-0.7` 的交付披露，不再有人工修正入口 | `不适用`（退役条款） |
| R-0.5 | transform 自证（判据 `s0_revision_chain` 复算 `transform_id`），`clean_text.txt` 是当前 transform 的唯一物化投影。无多修订祖先链 | `HARD` |
| R-0.6 | 必须能核对**两个内容身份**：`Stage0Source.normalized_source_sha1`（归一化原文）；`clean_sha1` 从 transform 与 live 投影现算。Stage1 的真正输入身份是后者 | `HARD` |
| R-0.7 | 剔除的**正确性**——「这一段到底是不是番外」。2026-09-15 起人工确认入口退役，本维转为 Q-13 内容质量类披露随交付输出 | `人`→`披露`（`s0_correctness`；Q-13） |
| R-0.8 | 重跑 stage0（v3）：同原文且算法结论相同 ⇒ 盘面不变；结论变化 ⇒ 整份替换 transform 并保持 mechanical admission。原文变化关闭式拒绝（`R-0.14`）。v3 无人工稿覆盖分支 | `HARD` |
| R-0.9 | 剔除后正文占比 < 0.60 判失败——**红在花钱之前** | `HARD` |
| R-0.10 | 可疑但不算错的按 current 内容现算为显式 `warnings[]` 视图（`STAGE0_WARNINGS` 三类），**不落 revision、不静默、不自动修正、不硬报错** | `INFO` |
| R-0.11 | 解码：BOM 优先探测，无 BOM 时试 `utf-8` → `gb18030`。**解码失败入显式终态，绝不用替换字符** | `HARD` |
| R-0.12 | 归一化**只做两件事**：去 BOM、换行统一为 `\n`。**不做 NFC/NFKC、不 strip 行尾** | `HARD` |
| R-0.13 | 目录寻址只有一把尺子：文件名结尾有 `_<数字>` 就用它当 id，否则用书名本身当键；写盘前断言目录名能被同一把尺子解析回来且是不动点 | `HARD` |
| R-0.14 | 同目录不同正文 → 显式终态（除非显式强制覆盖） | `HARD` |

原始文本与人工清理稿必须有不同身份，重跑不得覆盖人工修改或替另一版补批准。
纯数字章节下的番外没有可靠结构信号，必须保留完整原文、删除范围及 typed stop，不能以摘要代审。
载体与版本见 `docs/schema/stage0.md`；源文漂移、审批竞态和中断重放反例见
`tests/test_stage0_revisions.py`、`tests/test_stage01.py`。

### R-1 · 摄入与分章（stage1）

免费消费 Stage0 已批准文本，生成章表与开篇文案；发现非正文残留时拒绝，不在本层再次删除。

| # | 要求 | 谁在查 |
|---|---|---|
| R-1.1 | 解码由 `R-0.11` 负责；本层只读取 UTF-8、换行已归一的 `stage0/clean_text.txt`。 | 见 `R-0.11` |
| R-1.2 | 文本归一化由 `R-0.12` 负责，Stage1 不重复处理。 | 见 `R-0.12` |
| R-1.3 | 当前 `clean_text` 的全宽 sha1 必须等于 Stage0 当前批准 revision 的 `clean_sha1`；对不上即阻断。Stage1 不再透传源身份或落盘第二份指纹 | `HARD` |
| R-1.4 | 原文章号的断号 / 重号 / 倒退由当前 `clean_text` 复扫，只在 Stage1 对照视图与校验报告里显示；不复制进 `main_text` | `HARD` |
| R-1.5 | 复扫得到的章标记数必须等于 `main_text` 长度，且每一章 `text` 必须与当前 clean 切片逐字相等 | `HARD` |
| R-1.6 | 剔除后的正文占比由 `R-0.9` 检查；Stage1 不重复剔除或设置占比闸。 | 见 `R-0.9` |
| R-1.7 | **降级成断言**：在 stage0 已批准的文本里若还认出非正文标记 → **显式终态**（说明 stage0 漏了）。<br>**本层一个字都不切。** 判据与词表仍与 `R-0.1` 同源——只有一份 | `HARD` |
| R-1.8 | 剔除正确性由 `R-0.7` 的人工审阅负责。 | 见 `R-0.7` |
| R-1.9 | 同一原文章号重复达 `NOISY_DUPLICATE_MIN` 时产生 `s1_warnings` 的 `INFO` finding；证据从当前 clean 复扫，**不落盘、不静默、不自动修正、不阻断** | `INFO` |
| R-1.10 | 原始文本的目录寻址由 `R-0.13` 负责。 | 见 `R-0.13` |
| R-1.11 | 同目录不同原始正文的冲突由 `R-0.14` 负责。 | 见 `R-0.14` |
| R-1.12 | **认不出开篇文案段 → 显式终态**：不写任何产物、后续 stage 一步都不跑。文案段不是可选的 | `HARD` |
| R-1.13 | **正文章数 < 2 → 显式终态**：只有一章的「书」不接受重构。**不带字数阈值** | `HARD` |
| R-1.14 | 切点与第一个非正文标记之间那一段，**要么为空，要么只有一个完结标记行**——正文里不许漏进第三种东西 | `HARD` |
| R-1.15 | **源文本覆盖不变量**：`[0, total_code_points)` 必须完整分解为 hook + 逐章标记行 + 章正文 + tail——无未记账码点、无重复覆盖；每条标记行须通过章标记文法解析，并与同一次 clean 复扫得到的标号相等。判据走覆盖游走 + 文法解析，**不与分章产物自证** | `HARD` |

非正文匹配只在 `novel/core/ingest.py::nonbody_kind` 与 `contracts/invariants.py` 定义。
修改词表先用 `tools/probe_nonbody_word.py` 查正文误命中，再补 `tests/fixtures_ingest.py` 的红例和绿对照；
不得与完结标记词表重叠。`后记`、`尾声` 的包含匹配会误切“节目的尾声”“孩子出生后记在你的名下”，
长度上限不能补救；阈值按 Unicode 码点计算，标记前的章号行要正确归属。
纯数字番外仍需人工兜底；章号倒退不能直接当作番外。覆盖游走必须独立核对原文，不能与分章结果自证。
精确字段见 `docs/schema/stage1.md`，边界与阈值带反例见 `tests/test_ingest_fixtures.py`。

### R-2 · 源语义观察与编译（stage2 · B2 五节点）

**B2（2026-09-27 职责解耦 D3/D8/D9）**：Stage2 按语义单元与作用域拆五个节点——
`str06.stage2.book`（书级观察：人物候选、称谓及证据、初态、世界约束）、
`str06.stage2.chapters`（逐章观察，互不依赖可并行）、`str06.stage2.names`（源名称
结构观察）、`str06.stage2.associate`（跨章关联）与免费 `str06.stage2.compile`
（解析证据、生成 ID、执行关联，发布 Characters＋Blueprint 双真源；唯一 writer）。
`str06.stage2.source`（book＋chNN 联合抽取）退役；派生输入
`stage2/compiled/book_extraction.json` 与 `stage2/compiled/input_snapshots.json`
退役，Stage4 改读 canonical。观察与关联协议显式版本化
（`contracts/schema/observation.py`；I-13，无运行时转换层，旧根只读，新协议下
新根全量重购）：book／chapters／names 三类观察共用 `stage2-observation-v1`；
associate 独立版本 `stage2-associate-v2`（R1，2026-09-28——typed 作用对象显式
序列化、状态句柄位置编码 `ch{nn}.b{拍}.e{效应序}.{key}`／`book.e{条目序}.{key}`，
三类观察请求逐字不变）。模型输出面去机械化（I-20/I-21）：拍号、
状态/约束编号、命题/loop/场景正式 ID、章序、offset、hash 一律由 compiler 生成；
人物 slug 仍由模型给出（可读语义句柄）。证据定位归 Resolver
（`novel/stages/stage2/resolver.py`：句段 id 闭集选择、逐字锚唯一定位、basis 引文
所在段定位与证据清单渲染）。**证据段＝源文一个非空自然段**（RC-26，2026-10-04，
rc15）：段文本是去掉首尾空白的原文一行，请求里一段一行、段号与文字同行
（`novel/stages/chain.py::build_evidence` 唯一切分、`novel/stages/stage2/resolver.py::render_evidence_block`
唯一渲染，段文本含换行即拒）；旧的「只在句号后切」会把对白收尾「」」切到下一段、
把问号叹号收尾的多个自然段并成一段，模型系统性认错段号（三书停书根因）。
源章原文只进观察节点自己的 prompt；下游一律消费编译产物（source-blind），
不从源正文推语义。章序身份来自正式 Stage1 `MainText`（阅读序 1..N 密集）；
**输入身份＝`main_text.json`＋`hook_copy.json` 两份盘上字节的组合摘要**（`R-2.9`）
——章前文字（源引子）切成 `e00_*` 证据段进书级观察与第 1 章观察，按信息职责分流
（开场前已成立的状态/关系/知情 → 初态；实际发生的开场行动 → 第 1 章拍；
与正文重复的事件以正文为准；预告/概括不进 canonical；判断不了弃权），
不对整篇引子打标签。`source_fold` 把 core 与 Blueprint 折成事件表供机械对账
（含章前文字的两个只读读数）。
语义未审以 unver 行常驻披露，不产生审批待办。字段见 `docs/schema/stage2.md`。

| # | 要求 | 谁在查 |
|---|---|---|
| R-2.1 | book 级观察编译闭合：`BookObservation` 无未知键、无悬空引用、人物闭集自洽；证据闭集、`R-2.10` 锚点与 `R-2.8` canonical 闭合在 book owner 引擎当场聚合报错，不落半份产物。canonical `CharactersCore` 构造归 compile（`str06.stage2.compile`，B2 第三步已接线）。 | `HARD`（`novel/stages/stage2/book.py::compile_book_observation`） |
| R-2.2 | 源章数组阅读序 1..N 密集且非空（`MainText` 模型保证）；目标章数恒等于源章数（同 `R-5.2`）。生产路径读正式 Stage1 产物，`--source-json` 只是开发路径。 | `HARD`（`novel/stages/chain.py::run_chain` + `contracts/schema/stage1.py::MainText`） |
| R-2.3 | 章观察与编译闭合（B2）：章观察（`ChapterObservation`）只含有序事件＋三类转移＋场景边界＋时间模式＋证据，**只含章内本地句柄**——没有前章快照、命题/loop 登记表、beat 表与因果边（D3）；章观察互不依赖，前章编译或关联失败不改变后章请求，缺失单元恢复不重购已存结果。participants／认知 holder 闭集、命题与 loop 引用闭合（本章登记表）与场景分段生成归 compile；拍号按事件列表序派发。 | `HARD`（`contracts/schema/observation.py::ChapterObservation`＋`novel/stages/stage2/chapters.py::compile_chapter_observation`＋`novel/stages/stage2/compile.py`（B2 第三步已接线） |
| R-2.4 | `source_fold` 事件与 Blueprint canonical 一致（落 `stage2/acceptance/fold.json`，由 `str06.stage2.compile` 随双真源同批写出）；fold 是机械对账面，不产生新语义。章前文字（e00）的两个分流读数——开场拍对初态同键 set 同值、初态已知者又被开场拍告知（按**持有者与命题**核对，QP-07 修复）——只报告、不阻断。 | `HARD`（`novel/stages/stage2/compile.py::source_fold`） |
| R-2.5 | `CharactersCore` 与 `BlueprintsArtifactV13` 的盘上形状由 `ARTIFACT_SPECS` 登记（这两个模型不声明 `contract_version`，按 F8 义务在注册表登记形状）；不往模型加非 exclude 字段——那会改 `model_dump` 字节、从而改 prompt 与已购请求身份。 | `载体`（`contracts/schema/__init__.py::ARTIFACT_SPECS`） |
| R-2.6 | 已购请求身份按字节复算：候选产物落盘用保序序列化（不 `sort_keys`），prompt 与 adoption 身份逐字相等；排序归一会静默改掉 F5e 已购请求的身份。 | `结构`（`novel/store.py::Str06Repository` 的保序序列化） |
| R-2.7 | fold 事件计数与章分布无条件报出（度量，不是门）；章前文字读数同列上报。 | `度量`（`novel/stages/stage2/compile.py::source_fold`） |
| R-2.8 | **称谓闭合（wire 闸）**：`canonical` 必须逐字等于该人物 `surfaces` 中的某一项。canonical 是内部主键显示名，又作为身份名交给 Design／Hook；未登记字面在替换表里找不到对应新名。不通过走 owner patch，不静默改写。 | `HARD`（`novel/stages/stage2/book.py::compile_book_observation`） |
| R-2.9 | **组合源身份**：`source_identity` 与 `root_sha1` 由 `main_text.json`＋`hook_copy.json` 两份字节的组合摘要派生（长度前缀，防拼接歧义）。只改章前文字也换源身份 ⇒ 候选根复校关闭式拒绝，旧根不得沿用旧购买；续跑用新独立根。开发路径 `--source-json` 必须显式给出引子或显式声明「无引子」。 | `HARD`（`novel/stages/chain.py::load_stage1_chapters`＋`novel/prov.py::combined_content_sha256`＋`novel/llm/paid_requests.py::init_or_resume_candidate_workflow`） |
| R-2.10 | **称谓锚点字面（wire 闸）**：每个 surface 的 `from_chapter` 由 compiler 按证据锚点派生（B2 起 wire 不带窗口坐标）：锚点段＝所引证据段中逐字包含该称呼的最早一段，章前文字（e00）锚点映射到第 1 章；所引段没有一个逐字包含该称呼即拒（QP-04 根因：章前称谓被标成第 0 章）。锚点派生唯一实现在 Stage2 Resolver。 | `HARD`（`novel/stages/stage2/resolver.py::EvidenceResolver.surface_chapter`） |
| R-2.11 | **悬念生命周期**：`loops` 独立成数组，op 只有 open／close；同一 loop 只能 open 一次（已 open 或已 close 的 id 不得再 open），close 必填非空 resolution，问题文本只出现在 open 处、之后不可改写（v12 把 loop 挂成 effect 时模型反复用 set 改写问题文本，F9 与首轮付费验收实证）。章内本地close不得早于本地open（拍序及同拍条目序）；仅有close的观察仍允许跨章配对。 | `HARD`（`contracts/schema/source.py::LoopAction`＋`novel/stages/stage2/chapters.py` 本章内open／close均不得重复且本地开合有序，同一判据用于接受、关联输入预检与最终编译；旧盘错误按chapter scope回派，跨章配对选择归associate；RC-18～20追加反例见`tests/test_workflow_role_boundaries.py`） |
| R-2.12 | **命题引用闭合（B2 版）**：命题与 loop 的引用用**章内本地句柄**（wire `ref`，不进 canonical）；新建（首次出现）必带 statement，引用不带文本；同章重抄核对本章首次登记原文。**同一拍内后建的命题可被本拍其它转移引用**（D5 证实拍模式）；同章跨拍引用须已登记；**章间不引用**（D3 去登记表）——跨章同一性（同一命题、同维状态、悬念开合、跨章因果）由 `str06.stage2.associate` 只选已给句柄建立，配不上即未决，compile 拒绝发布并按 item 报告（D8）。**措辞不同的同义表述也算同一**：语义判断归 associate，compiler 不以文本全等否决有效配对，也不按文本相似度自行配对；类内正式引用统一采用阅读序最早建立的 statement，各章观察原文保留。合并不移动事实成立、角色知情或读者揭示时点；无文本类、悬空引用及未来引用仍拒绝。正式 ID（kNN／loopNN）由 compiler 生成。 | `HARD`（`contracts/schema/observation.py`＋`novel/stages/stage2/compile.py`；2026-10-04 用户裁决撤除跨章文本全等闸，语义正确性按 R-3.3 保持未评披露） |
| R-2.13 | **事实可见性**：每个 effect 必填 `disclosure`（on_page／concealed）；concealed 必须声明 known_by（非空）并带命题锚——叙述确立但读者此刻不可见的事实，由后续 cognition 经同一命题记录揭示；没有可见性，漏记的揭示无法机械发现（QP-05：ch04 床下现身缺确认 reveal，三份样本全缺）。 | `HARD`（`contracts/schema/source.py::Disclosure`／`Effect`） |
| R-2.14 | **登记键闭集与作用域绑定**：`scene_location`（scene 作用域，当前所在）与 `residence`（book 作用域，长期居所）为登记键，身体状态各自一键（不塞进位置值）；表内键作用域由 compiler 按表绑定（I-15，模型声明不符即归一化，canonical 恒带表内作用域）；`whereabouts`／`hiding_place`／`location` 已退役，出现即拒并指向分轴后的键。 | `HARD`（`contracts/schema/source.py::REGISTERED_STATE_KEYS`／`RETIRED_STATE_KEYS`＋`novel/stages/stage2/chapters.py` 归一与拒识） |
| R-2.15 | **未揭示 concealed 只读报告**：concealed 效应到书末仍无读者确认（reader 持有者的 learns／confirms）时，按命题列出（statement／建立拍／known_by）落 fold.json；只报告、不阻断。 | `度量`（`novel/stages/stage2/compile.py::source_fold`） |
| R-2.16 | **空转移拍比例**：每章三类转移全空的拍所占比例按章报出（fold.json `chapters`）；这类拍合法——完整性由该读数配合离线核读证明，不加声明字段（D7／D17）。 | `度量`（`novel/stages/stage2/compile.py::source_fold`） |
| R-2.17 | **知情 fold 读数**：源 fold 只消费 canonical transitions（认知层按「命题＋持有者＋模式」折叠；D3 起不再派生逐章输入快照）；未揭示 concealed、空转移拍比例等读数保留——度量，不是门；读者确认时点由 on_page 效应或 reader 的 learns／confirms 落账。fold 唯一实现随 B2 第三步迁入 compile owner。 | `度量`（`novel/stages/stage2/compile.py::source_fold`） |
| R-2.18 | **basis 引文定位（R10a a1，C4；RC-27 起段号由代码定位）**：canonical 的每条 basis 是 EvidenceQuote{segment, quote}——quote 存在于该段文本内，且该段属于条目自身的 `source_refs`（Effect／Cognition 在所属拍；书级条目在自身）。book／chapters 的 LLM 输出面（`stage2-book-v5`／`stage2-chapters-v5`）**只给引文**，所在段由 Resolver 在条目自身证据范围内定位（逐字命中优先，其次仅标点／空白宽度不同的唯一命中并回取原文切片；多段都含时取范围内第一段，basis 是证据存在性不是唯一定位）：范围外命中 → `quote_outside_refs`（整拍返修，修正可能是扩范围也可能是换引文）；全无命中 → `quote_not_found`（只返修该转移）。canonical 直通（wire 领养）照原规则逐字复验，缺段号按协议拒收、不代补；names 的 basis 段号仍由模型选（它决定局部归一作用的出现处）。失败码 `quote_outside_refs`／`quote_not_found`／`quote_not_in_segment`／`basis_segment_outside_refs`／`unknown_segment` → `revise_self`。Blueprint v15 的证据窗口（被引段全文＋同章前后各一段、重叠机械合并）由 compiler 派生——邻段被送达不代表邻段自动成为证据，跨窗口限定须 Stage2 显式补引。 | `HARD`（`novel/stages/stage2/resolver.py::locate_quote`/`verify_quote`/`verify_bases`＋`contracts/schema/source.py::EvidenceQuote`；反例 `tests/test_stage2_evidence_addressing.py`、`tests/test_str06_a1_semantics.py`、`tests/hard_cases.py`） |
| R-2.19 | **事件时态闭集（R10a a1，C2 源侧）**：`Effect.modality` 必填且取值 `actual/planned/expected/conditional`——计划／预计／条件行为与已发生是不同事实形态，抽取不得把限定词当已发生写入效应；命题 statement 写命题本身，认识限定（以为/觉得/像是/预计/约）只进 cognition 模式或 modality，不混入命题文本。源文未说明不得自动填成 `actual`。 | `HARD`（`contracts/schema/source.py::Effect.modality`＋`novel/stages/common.py` 观察规则；反例 `tests/test_str06_a1_semantics.py`、`tests/hard_cases.py`） |

源语义（归因、因果、揭示）的语义正确性不经机械检查证明，随 Stage2 语义未审披露；
`--check-only` 只允许缓存命中与登记的历史采用，零新购。

### R-3 · 源名称结构（stage2 · str06.stage2.names）

`str06.stage2.names` 是对源 core 的窄调用：编译名称结构（`NameStructure`：人物闭集、
家族、称谓面）落观察 `stage2/observations/names.json`（B2 起，D9）；Characters v4
（`stage2/characters/characters.json`）由 `str06.stage2.compile` 合并名称结构后统一发布。
姓氏构词探针（`surname_construction_probe`）只报告、不修复。专名面唯一：Stage5/7 与消费投影全部走
名称投影（`name_projection`），旧 `name_distinctiveness` 消费者已随旧链退役。

| # | 要求 | 谁在查 |
|---|---|---|
| R-3.1 | Characters v4 名称结构编译闭合：人物闭集、家族与称谓面合法；names v2 对源姓名异写提供原字面、same／unresolved、人物与已登记称谓、原句证据和理由。compiler 仅在声明的证据局部归一语义文本，保留原文及引文；未决或冲突在关联购买与双产物发布前停止，下游不得私改命题；编译错误 fail-fast。 | `HARD`（`novel/stages/stage2/names.py::compile_name_structure`） |
| R-3.2 | 应改名集合覆盖源名称结构：名称位一致、无漏标、纯称谓不进应改名集；`merge_name_structure` 保持稳定 ID。 | `HARD`（`novel/stages/stage2/names.py::merge_name_structure`） |
| R-3.3 | 源语义（归因、因果、揭示）未经语义审阅，保持未评披露；不产生审批待办。 | `INFO`（`source_semantics_unver` 明示 `UNVER`）；逐章与全书抽取的语义盲区见 `B-07`／`B-08`，设定块剧透见 `B-16` |
| R-3.4 | 姓氏构词探针只读报告（同姓亲属、复姓、独立化名的构词读数），不自动替换、不首字猜姓。 | `结构`（`novel/stages/stage2/names.py::surname_construction_probe`） |

### R-4 · 即时机械测量（无 Stage3 产物）

Stage5/6 从已读取的 Stage1 文本即时计算机械读数，不生成 Stage3 产物。

| # | 要求 | 谁在查 |
|---|---|---|
| R-4.1 | **形态数字由代码算，不是 LLM 产**：正文机械读数由当前代码现场计算；没有可漂移的机械缓存。旧链风格投影构建族（`measure_body`／`body_style_metrics`／`hook_style_metrics`）已随 2026-10-04 清理批删除 | `结构`（`novel/stages/stage5/body_checks.py::count_body_chars` + `novel/stages/stage2/expression.py::expression_readings`） |
| R-4.2 | 正文与开篇文案段**分开测、互不污染**：现役只测完整正文；「Stage6 只测原作文案」的一半已随 STR-06 退役 | `结构`（`novel/stages/stage5/body_checks.py::count_body_chars`） |
| R-4.3 | **没有现役 Stage3 节点或产物**：`stage3.style` 与 `stage3/style_grounding.json` 只登记为退役身份，不能由 CLI、队列、API 或 producer 重新创建 | `结构`（`contracts/artifacts.py::RETIRED_ARTIFACT_PATHS`（产物层关闭式拒识；节点名册已随 STR-06 关闭批删除，非现役节点一律拒）） |
| R-4.3b | **【Stage5 侧】只消费正文机械精确投影**：即时结果只保留 `avg_sentence_chars`、`avg_paragraph_chars`、`paragraph_sep_chars`、对白比值/引号体、章均字数、逐章平均句长 min/max；对白适用性由 `dialogue_ratio is not None` 推出，不存重复布尔值。`shape_block` 只把这些值序列化为句子/段落/对白/章篇幅/章间差异 5 个正文档位。旧链 Stage5 review 的 `load_book` 消费面随旧链退役；新链 Stage5 侧只由 `str06` 正文写手消费章级机械投影（非空白码点计数与长度带），不重建第二套读数缓存 | `结构`（`novel/stages/stage5/body_checks.py::count_body_chars`） |
| R-4.4 | **【已退役】Stage6 只消费原作文案机械五叶**——Stage6 层随 STR-06 切换退役，`hook_style_metrics` 已随 2026-10-04 清理批删除；新链 Hook 只携带 `HookConstraints`（target/hard_max）长度边界，不消费原作文案机械投影 | `结构`（`contracts/schema/hook.py::HookConstraints`） |
| R-4.5 | 测量模型只是**进程内 strict 载体**，不带 `_schema_version`、`_provenance`、prompt、模型或 usage，也不进入 artifact registry | `载体`（`contracts/schema/measurement.py`） |
| R-4.5b | 旧 Stage3 v2 的解码器及专用 consumed 投影已退役；旧来源边不能通过当前 Store 获得现役有效性，历史字节只随旧代码与旧根配对读取 | `载体`（`contracts/schema/__init__.py::ARTIFACT_SPECS` + `novel/store.py::_projection_payload`） |
| R-4.6 | 开篇文案段是**必填输入**；无文案段已由 `R-1.12` 挡在 Stage1（其 Stage6 机械消费边已随 STR-06 退役，本层不造 nullable hook 或空兜底） | 见 `R-1.12` |
| R-4.7 | 当前机械测量不独立记 provenance；Stage5 的来源账直接记录真正读取的 Stage1 主体（Stage6 来源边已随 STR-06 退役） | `结构`（`novel/consumption.py::stage2_main_text` / `contracts/schema/hook.py::HookContext`） |
| R-4.8 | 测量算法口径是独立版本轴；只改算法、不改输入 JSON 形状时，下游也必须自然 stale。旧 `STYLE_MEASUREMENT_CONTRACT` 常量已随 2026-10-04 清理批删除，现役版本轴由章级 validator 版本承载 | `结构`（`novel/stages/stage5/body_checks.py::BODY_VALIDATOR_VERSION`） |
| R-4.9 | 当前测量的真实上游只有 Stage1 正文（原作文案消费边已随 STR-06 退役）；测量本身不等待、也不消费 Stage2 | `结构`（`novel/stages/stage5/body_checks.py::count_body_chars` / `contracts/schema/hook.py::HookConstraints`） |
| R-4.10 | 测量契约必须进入 Stage5/6 对相应 Stage1 文本的 consumed 投影，使算法改动与输入文本改动走同一 freshness 机制 | `结构`（`novel/consumption.py::stage2_main_text` / `contracts/schema/hook.py::HookContext`） |
| R-4.11 | 不再注册 Stage3 规则或 check 端点；即时读数只由 Stage5/6 的真实消费者继续进入 prompt、归一化或机械检查 | `结构`（`contracts/rules.py` + `novel/core/measure.py`） |
| R-4.12 | 不再生产 Stage3 `.INVALID`、轮次稿或修复命令；Stage1 变更后下游现场重新计算 | `结构`（`contracts/artifacts.py::ARTIFACT_PATHS` + `contracts/invariants.py::NODES`） |
| R-4.13 | 旧 Stage3 字节不迁移、不重建；Stage5/6 freshness 只使用现役节点的登记投影。旧边未能解析时保持 unknown，不为其重签来源或审批 | `结构`（`novel/store.py::freshness_of_inputs` + `novel/consumption.py::projection_for`） |
| R-4.14 | `--measure-only`、Stage3 `--check-only` 与默认生产入口全部退役；诊断由 Stage5/6 的 0 成本检查展示实际消费后的读数 | `结构`（`contracts/invariants.py::NODES`（非现役节点一律拒） + `novel/orchestrator/nodes.py::build_argv`） |
| R-4.15 | s3 闸、Stage3 API 与页面全部撤下。当前新 clean base 的盘上审批为 v9、公开 API 为 v9（各自独立形状）；当前审批只解码 v9，v1～v8 及未知版本均拒绝；旧审批解码、升版和旧 Stage3 proof 迁移分支已删除，Stage2 已读/接受记录不再属于审批契约。backfill 只为当前工作流补录可证明的检查单，不迁移旧 base、旧 token 或补签历史审批。普通 GET 与事件观测只读；新写入口必须验证当前 workflow manifest。backfill 不从“产物齐备”制造审批，也不得把旧 Structure subject 重签为 current s4。Stage4A 设计审阅只授权 B，不打开 s4；Stage5 写作证明绑定当前 Stage1 正文、Stage4 计划、经最终结构批准后单独生成的当前 WriterContext 和测量契约；s4 仍只由 B 编译后的独立 typed 计划结构裁决打开。**F8b-3：旧链人工闸（authorize_plan/authorize_context/begin_writing 与 backfill 写入口）随旧链整层退役；2026-10-04 裁决后存量 v9 账的只读解码与文件锁模块一并删除（旧根一律 legacy_incompatible 拒识）**，新链交付由确定性 orchestrator 推进，不再签发人工 gate | `结构`（`novel/orchestrator/trash.py::approval_exclusion_lock`（历史审批文件的互斥与归档） + `novel/orchestrator/inspection.py::_legacy_approval_bytes`（原始字节 seal） + `novel/work_units.py::check_workflow_write`；2026-10-04 裁决后存量账不再解码，`approvals.py` 与 `ApprovalDoc` 已删除） |

现役读数在真实消费点即时计算，不序列化完整测量对象；旧链下发叶子声明面
（`body_style_metrics`／`hook_style_metrics`）已随 2026-10-04 清理批删除。
修改读数口径同时核对 producer、prompt 与校验消费；Hook 契约见
`docs/schema/stage4.md`／`stage5.md` 的 Hook 节。

### R-5 · 目标故事与 Hook 设计（stage4 · preserve／body_design／hook_design）

Stage4 的三个付费节点按固定序推进：`preserve`（保存策略义务）→ `body_design`
（目标故事与导演说明，同节点双产物发布 SemanticBinding——CS-3 bind 退役，义务
承载位由 design wire 的 real 标注派生）→ `hook_design`（Hook 必要选择
并经 `publish_graph` 唯一发布 PresentationGraph），之后由免费 `str06.stage4.compile`
确定性编译（`R-6`）。Design 是 source-blind 的：实际输入
只有 Preserve 投影（`project_brief`）、身份投影与 Characters v4，源叙述拓扑不进 prompt。
章数恒等于源章数；跨章重构合法——场景可跨源章重组，输出章仍与源章一一同号。

| # | 要求 | 谁在查 |
|---|---|---|
| R-5.1 | BodyDesign 编译闭合：场景、拍、章任务与约束合法（未知键、悬空引用、非法 POV 当场拒）；**称谓面闭合**——`initial_surfaces` 与逐拍 `surface_introductions` 只能释放 Characters 登记过的 surface（`registered_surfaces` 由 `novel/stages/chain.py::run_body_design` 传入），未登记字面在生产端尽早失败。 | `HARD`（`novel/stages/stage4/design.py::assemble_body_design`） |
| R-5.2 | **章唯一键（I-40 同款翻转）**：目标章数等于源章数、章序 1..N 连续非空、输出章号恒等于源章号；不合并、不删除、不拆分、不重排**章**，但允许跨章重构（场景可跨源章重组，`from_source_ordinals` 如实记录多源归属）。 | `HARD`（`novel/stages/stage4/design.py::assemble_body_design` + `contracts/schema/grounding.py::OutputConstraints`） |
| R-5.3 | 章预算按场景权重 largest-remainder 整数分配，总和恰等于书级目标；书级目标从源章总字数向上取整确定性派生（配置取整粒度进契约身份），付费调用额度不参与本计算。 | `HARD`（`novel/stages/stage4/book_ir.py::allocate_chapter_budgets` + `novel/stages/chain.py::derive_total_length_budget`） |
| R-5.4 | Preserve checkpoint 编译闭合：策略义务完整、来源可溯；ReconstructionBrief 是确定性投影，不新增语义。 | `HARD`（`novel/stages/stage4/grounding.py::assemble_preserve`） |
| R-5.5 | 承载闸接进 body_design 的 ask 同节点修订轮（CS-3 bind 退役改写）：机械校验（real 覆盖、标注拍闭集、series 成员接受拍不同、分母一致）在修订轮内重问（≤`OWNER_REVISION_MAX_ROUNDS`，同问题集重复即停）；修订轮耗尽／无进展 fail-fast 停在 owner；同指纹不重购（原响应已耐久，重放走缓存）。**局部修订（CS-F1，RC-46/47）**：Design 的接受问题在判定处带规则码、稳定位置与可编辑 v8 wire 路径（`ValidationIssue.context['wire_paths']`，不解析文案）；全部可定位时只把**最小完整父项**发回条目返修（叶子标量升到所在对象，同一拍内两处及以上待修合并为整拍，替换不重叠，场景自身标量原地替换），合并后重跑**完整**候选预检才采纳、未涉及条目保持原内容，无进展／循环／累计上限沿用现役规则；存在任一无法定位的问题（全局覆盖闸、上游派生材料、越界路径）整份修订，不静默漏项。 | `HARD`（`novel/stages/stage4/grounding.py::derive_semantic_binding` + `novel/stages/chain.py::run_body_design` + `novel/stages/stage4/design_paths.py::locate_design_patches`；局部修订反例 `tests/test_str06_design_patch.py`）；修复被驳回后没有下一条路，见 `B-18` |
| R-5.6 | Hook 设计机械校验通过（必要选择落在正式 Design、材料倒叙冷开有序、禁止揭示与 withheld 字面合法）；PresentationGraph 唯一 publisher 是 hook_design 节点。 | `HARD`（`novel/stages/stage4/hook.py::assemble_hook_design`／`publish_graph`） |
| R-5.7 | 目标故事与导演说明的语义质量未经语义审阅，保持未评披露。 | `INFO`（`design_semantics_unver` 明示 `UNVER`）；自由文本发明与提前泄底见 `B-06`，身份描述泄露见 `B-14` |
| R-5.8 | 章预算相对场景权重的分布读数无条件报出（度量，不是门）。 | `度量`（`novel/stages/stage4/book_ir.py::allocate_chapter_budgets`）；场景轻重与披露顺序的审美无 ground truth，见 `B-17`／`B-02` |
| R-5.9 | `BodyDesign`／`PresentationGraph` 的盘上形状由 `ARTIFACT_SPECS` 登记；模型自带 `contract_version` 字面量与候选产物表逐字对账。 | `载体`（`contracts/schema/design.py::BodyDesign`） |
| R-5.10 | Design 实际输入无源叙述拓扑：`project_brief` 只投影义务与身份，源章拓扑/原文不进 prompt。 | `结构`（`novel/stages/stage4/grounding.py::project_brief`）；prompt 侧隔离守卫见 `B-05` |
| R-5.11b | 当前组合的产物形状由候选产物表与 `ARTIFACT_SPECS` 登记（25 份书产物：家族＋schema 版本＋唯一 writer；模型自带 `contract_version` 的表值逐字对账），候选根的 workflow 身份记录绑定契约表身份与源输入身份；表漂移即拒绝复用旧根的采用结论。 | `载体`（`contracts/invariants.py::NODES`＋`contracts/artifacts.py::STR06_ARTIFACT_RC_VERSIONS` + `contracts/schema/__init__.py::ARTIFACT_SPECS`）+ `tests/test_str06_candidates.py` 对账 |
| R-5.12 | **Design 知情词汇含 believes（D14）**：knowledge 域的 `knowledge_mode` 取值域为 known/suspected/heard/believes；believes＝持有者以为为真、不代表世界为真——错误看法以结构形式进入写手入场知情，不得写成 known 让写手当世界事实演出。Preserve 策略 v3 同批登记（源侧说辞/行为反差、concealed 揭示时点、iterative 作用、loop 问题与 resolution）。 | `HARD`（`contracts/schema/state.py::StateChange`＋`novel/stages/common.py::DESIGN_RULES`；反例 `tests/test_str06_execution_ir.py::test_believes_knowledge_enters_writer_view_without_becoming_world_fact`） |
| R-5.13 | **Stage4 各语义节点独立 wire 协议与 compiler 生成派生面（B3 前半，rc7；R2 起 Design 升 v2，R8b 按 D8 修订升 v3，模型面现役瘦 wire `stage4-design-v8`、展开回 canonical 面 `stage4-design-v5`）**：Preserve／Design／Hook Design 各用独立 wire 协议（Bind 的 stage4-bind-v1 随 CS-3 bind 退役一并删除），顶层 `protocol_version` 逐字对账，漏带/错带即拒；模型只表达选择、结构与相对篇幅重点——义务编号（Preserve 列表序派发＋requires 序号重写）、场景/拍 ID（`sc{序:02d}`／`b{序:02d}`）、归章切片、线程编号（`th{nn}`，R2 按 open 先后派发）、权重归一（相对重点正数数组→和为 1）与整数预算全部由 compiler 生成，wire 白名单外的 ID/编号/归一权重键即拒（I-21/Q-1）。Design 的输入投影只含义务清单与锁摘要（`R-5.18`），不回显其它节点全量。 | `结构`（`novel/stages/stage4/design.py::assemble_body_design`＋`novel/stages/stage4/grounding.py`＋`contracts/schema/workflow.py::STAGE4_PRESERVE_PROTOCOL` 等三常量＋`contracts/schema/design.py::STAGE4_DESIGN_WIRE_PROTOCOL`；反例 `tests/test_str06_stage4_protocol.py`、`tests/test_str06_target.py`、`tests/hard_cases.py`） |
| R-5.14 | **TargetDesign 结构化跨章依赖（B3 前半；R2/rc10 改独立线程结构）**：整书风格（`book_style` 必填）、书级世界边界（`world_rules`）与稳定人物声线（`character_voices`）是 BodyDesign 的正式书级字段，随 BookBrief 进每章请求；跨章语义走**拍上的线程操作**（`TargetBeat.threads`：open 带 kind 闭集＋主体＋文本、advance 带进展、close 带按 kind 限定的结局闭集＋简短收束，生命周期闭合由 schema 构造期校验，主体人物闭集由 assemble 校验），章间承接由 `ChapterAssignment.entry` 必填声明（第 1 章固定开篇，编译器不猜）。rc7 的四个静态跨章列表（standing_goals／ongoing_actions／open_questions／promises）已删除（AUD-04 无消费者），wire 旧键关闭式拒读。 | `结构`（`contracts/schema/design.py::BodyDesign`＋`novel/stages/stage4/book_ir.py::compile_writer_book_ir`；反例 `tests/test_str06_stage4_protocol.py`） |
| R-5.15 | **Preserve v2 锚定与 series 闭合（R10a a2）**：每条义务以 `anchors` 引用源证据投影的句柄闭集（`b拍.e|c|l`／`s0_*`／`wc_*`，每义务 ≥1），`lineage` 由 compiler 从锚点派生（wire 不输出，I-15/I-21）；义务锚点类型化拷贝源原子内容（持有者、mode、命题、效应目标/值/modality/可见性、逐字引文）。顶层 `series`（members≥2、闭集互斥、mechanism 属源表达 ex_id 或 null）登记同一机制的离散兑现时点——不为满足阶段规则合并；`evidence_conflicts` 的锚点与句段在闭集且句段属该拍证据。 | `HARD`（`novel/stages/stage4/grounding.py::assemble_preserve`＋`contracts/schema/grounding.py::ObligationAnchor`/`ObligationSeries`；反例 `tests/test_str06_a2_preserve.py`、`tests/hard_cases.py`） |
| R-5.16 | **源证据冲突的有限回派与三分支恢复（R10a a3）**：Preserve 报告 `evidence_conflicts` 时回答照常耐久、checkpoint 不发布，按锚点句柄回派 Stage2 语义 owner（拍句柄 `bNN_MM.*`→逐章 owner chNN／item=chapter:N；书级 `s0_*`/`wc_*`→book；不可解析即停，机械路由不猜）。三分支：**修订**→发布新 SourceTruth、按材料变化恢复 Preserve（旧回答不直接采用）；**保留解释**→处理结果与引用作为同一 Preserve 任务 revise_self 的恢复输入，revise 后仍坚持同一实质分歧即停止不二次回派；**未决**→保持阻塞按停止规则结束，不自动标成源文未知。同指纹重现走无进展停；处理完成不重置额度。 | `HARD`（`contracts/schema/workflow.py::route_owner_issue`＋`novel/stages/chain.py::_backfill_source_evidence_conflicts`／`run_preserve`；反例 `tests/test_str06_a3_conflicts.py`、`tests/hard_cases.py`） |
| R-5.17 | **Brief 锁定项与 Design v5 锁规则（R10a a4）**：Brief 由 compiler 从义务 anchors 与 canonical loops 确定性派生锁定项——kp 命题登记（盲句柄）、知情锁 {持有者\|reader, kp, mode}（唯一映射 learns/confirms→known、suspects→suspected、hears_claim→heard、believes→believes；asserts 转公开声称标记不铸锁）、kf 事实锁（时态 modality≠actual、值未知 op=unknown、原文未回答＝悬念 open 未 close）。Design wire 接受校验（revise_self）：kp 键持有者须有锁且模式被授权（known 只能来自 known 锁，C1）；读者揭示统一走 knowledge(holder=reader)——与人物知情同一把授权尺（C6，rc14 保留统一知情域，读者 known 的 set 必填 confirms 目标事实地址）；**事实锁（kf）不再是状态键，不再另写一条复述事实的状态（CS-F2，rc16，Design wire `stage4-design-v8`）**：Design 在**已选的目标状态**上标注 `lock`（句柄闭集来自 Brief、不含原文未回答锁，请求按 Brief 注入）；时态锁须标在同 modality 的 set 上（C2，计划不得写成已发生，后文合法转 actual 不被旧锁永久禁止）、值未知锁须标在 op=unknown 上（C9，不得补值）、原文未回答锁是禁区、不承载状态、不要求造事实状态；一个锁至多一条承载状态，承载拍内该地址必须恰好对应一条状态（同址多条即歧义，Design反馈定位全部条目，Binding派生及正式产物免费重编译同验；后续拍合法更新仍可通过）；kf 作状态键、lock 标在 kp 知情行上一律拒。锁到状态的映射随 body_design 同次发布的 SemanticBinding 承载（`fact_bindings`：锁句柄＋承载拍＋状态地址，**不复制目标 value**，BodyDesign 仍是目标事实的唯一持有者）。这只证结构：承载状态的内容在语义上是否满足锁需标定的语义核验，未运行即保持 unver（I-42）。必保义务的逐次 knowledge_changes 与事实锁须完整兑现，v7 know 短引用单独声明各次认知拍位；real 仍声明最终验收/支撑位。Brief 仍源盲（无引文、句段、拍号）。 | `HARD`（`novel/stages/stage4/grounding.py::project_brief`/`_derive_brief_locks`＋`novel/stages/stage4/design.py::_check_lock_acceptance`／`_check_fact_marks`＋`novel/stages/stage4/slim.py::design_fact_marks`＋`contracts/schema/grounding.py::FactLockBinding`；反例 `tests/test_str06_a4_design_locks.py`（CS-F2 一节）、`tests/test_str06_fact_lock_stage.py`、`tests/hard_cases.py`） |
| R-5.18 | **series 分别承载（R10a a5；CS-3 随 bind 迁移）**：design 义务清单带出 series（正式 ID＋mechanism）与按义务渲染的锁摘要（Brief 锁面）；同一 series 的成员接受拍不同——两成员接受拍相同即 `series_collapsed`（body_design 同节点修订，C3/C5 的承载面）；复合义务前半用支撑拍（非末次标注拍）表达，不合并成员；必保漏标由 real 覆盖闸拦、无标注的非必保义务进 unbound（义务不删）。 | `HARD`（`novel/stages/stage4/grounding.py::derive_semantic_binding`＋`design_obligation_index`；反例 `tests/test_str06_a5_positions.py`、`tests/hard_cases.py`） |
| R-5.19 | **锁位置 Guard：信息披露顺序（R10a a5）**：kp 键知情升到某模式的位置、读者确认的位置不得早于**授权该模式的义务承载位置**（授权位＝必保义务 anchors 授权该 (holder, kp, mode) 的全部承载点最早者——支撑拍是承载的一部分，怀疑渐进到证实合法，P2）；initial_state 无披露时点不进判据；自由键不查（模式合法性归 R-5.17）；故事发生顺序不做机械 Guard（顺序三分，P5 合法倒叙不拦）。违例按 `epistemic_lock_order` 回派 `str06.stage4.body_design`（位置上下文＋源盲 core_id；披露顺序是 Design 的决定，承载位由 body_design 的 real 标注派生——CS-3 bind 退役后无独立承载节点）。 | `HARD`（`novel/stages/stage4/guard.py::lock_order_violations`＋`novel/stages/chain.py::_backfill_epistemic_lock_order`；反例 `tests/test_str06_a5_positions.py`、`tests/hard_cases.py`） |
| R-5.20 | **已购回答复用四条件（R10a §二.4）**：实际模型输入未变且已购回答仍适用→复用（重启不重复购买）；验收条件变化、模型输入未变→免费机械复验，失败按既有恢复规则；实际写作材料变化→按请求身份与修订规则判断是否新回答（材料变化＝新请求身份，含冲突处理结果附注）；仅审计或展示信息变化→不触发付费重写（时间戳等纯审计字段不进模型输入）。未受材料或验收条件影响的章节不重购；上游修订影响的章重新汇集正文与回执。 | `HARD`（`novel/llm/paid_requests.py` 请求身份与接受循环＋`novel/stages/chain.py::revision_prompt_for_owner`；反例 `tests/test_str06_a3_conflicts.py`、`tests/hard_cases.py`） |

材料名称冲突的归因须使用正式字段来源、目标消费拍位和释放依据（RC-01～08）；
不按源目标同号章二分路由。正确源命题的 know 拍位归 Design，Preserve 只修改自身
statement／satisfaction，默认 keep 由 Design expression_overrides 处理；来源未证明、
投影不一致或多 owner 歧义停止。Design 接受前与最终 compile 共用实际材料判据。
完整候选预检（RC-44～45）由 `novel/stages/stage4/preflight.py::inspect_design_candidate`
供生产接受与Design免费修订预览共用。可构造的候选即使有Design局部错误，仍继续
派生Binding、执行计划和实际Writer材料，聚合可独立执行的错误；依赖构造失败的检查
记录`not_run`及原因，Hook尚未生成时同样披露未运行。已有Hook的恢复预览及正式免费
重编译先复用Hook接受判据（称谓授权、隐藏呈现、材料与禁揭引用），再检查上下文与材料。
报告及回派不授予正式发布资格，
BodyDesign与Binding须在正文候选全部适用机械检查通过后同组发布。已证明的上游材料
错误先回派，全报告随停止上下文留证，不在错误上游输入上继续购买Design自修。
上述检查不证明自由事实的语义对应；新语义闸仍须先完成离线标定及启用验收。
关联诊断与材料诊断在判据源头生成规则码、稳定位置和身份，修订控制不得重新解析文案（RC-18～21）。问题身份不依赖展示位置或措辞；相同集合、循环和累计额度使返修停止，崩溃后尚未派出的
已登记轮可恢复，不能因此多占一轮。运行记录用 OwnerIssue v3，旧版本集中解码；错误
旧归因仅经 `issue-correction` 身份绑定的免费预览／显式更正变为 superseded，保存原
记录、已耗轮数与更正理由，不删除历史、不重置额度（RC-09～16）。

### R-6 · 确定性编译（stage4 · str06.stage4.compile · 0 成本）

`str06.stage4.compile` 结构性禁付（`executor_content_limit=0`）：ExecutionPlan、Guard
报告、WriterBookIR 与 HookContext 全部由确定性代码从正式输入产出。写前机械 Guard
M1–M8 在本节点聚合执行（判据码 → 检查函数的登记表
`novel/stages/stage4/guard.py::GUARD_CHECKS`，与 `contracts/judges/str06.py` 的
`guard_m{n}_*` 规则集合逐码对账），零错误才放行下游。

| # | 要求 | 谁在查 |
|---|---|---|
| R-6.1 | ExecutionPlan 编译闭合：四类执行语义与依赖边合法。 | `HARD`（`novel/stages/stage4/compile.py::compile_execution_plan`） |
| R-6.2 | WriterBookIR 双视图同源：VerificationIR 事实不遗漏、WriterBrief 只投影呈现；同一编译输入两次产出字节一致。两层 IR（R2）：Machine 层 `ChapterIR`（WriterContext：VerificationIR＋WriterBrief，审计与 Stage7 核对面）→ 写手层 `WriterPacket`（六分区渲染——must_realize／must_respect／continuity／creative_guidance／background／guard_only，全部自然语言、无内部 ID 与字段名），后者由确定性 renderer 从 Machine 层生成、从不落盘（章包是派生视图，I-16），其字节进章回执的写手视图摘要。**称谓投影（CS-F2，RC-40/41，renderer `str06-render-v10`）**：Machine 层 `ChapterIR.withheld_bindings` 记录本章尚未释放、且已证明唯一绑定某人物的**完整登记称谓**（登记 surface、含姓名部件、全书只属一人（归属统计含纯称谓登记）、长度≥2、非姓／家族构词；附包含它的更长称谓防拆分），renderer 在正向材料行（含禁区行）里把这类字面换成本场已释放称谓——确定性、可观测（`name_projections` 覆盖六分区的实际投影，含禁区；禁区仍不参与正向字面扫描，留痕不含行文本）、原字段与来源文本不改、不反写事实；短姓、关系称呼、多义字面、未登记表达（如源文没有的「陆郎」）与源引文一律不投影，仍按字面检查回派原 owner；人物本场没有已释放称谓时不投影、泄漏如实保留。Design 自有文本的名称冲突仍由 Design 接受判据报告（compiler 不自动提前释放全名，RC-43）；Hook 材料暂不投影。 | `HARD`（`novel/stages/stage4/book_ir.py::compile_writer_book_ir`＋`novel/stages/common.py::project_chapter_packet`/`render_chapter_packet`/`project_withheld_names`＋`novel/core/name_materials.py::withheld_name_bindings`；反例 `tests/test_str06_body.py`、`tests/test_str06_r2_contracts.py`、`tests/test_str06_name_projection.py`） |
| R-6.3 | HookContext 仅凭正式输入可确定性重建（PresentationGraph＋名称投影＋Hook 配置）；同输入字节一致，不依赖旧 session 或内存态。 | `HARD`（`novel/stages/stage4/hook_context.py::compile_hook_context`） |
| R-6.4 | **M1 义务覆盖**：本场 owner 的义务完整进入 IR，内容逐条一致。 | `HARD`（`novel/stages/stage4/guard.py::check_obligation_coverage`） |
| R-6.5 | **M2 提前揭示**：`entry_knowledge` 的 `established_by` 早于本场起点。 | `HARD`（`novel/stages/stage4/guard.py::check_entry_timing`） |
| R-6.6 | **M3 提前效应**：`entry_state` 的 `established_by` 早于本场起点。 | `HARD`（`novel/stages/stage4/guard.py::check_entry_timing`） |
| R-6.7 | **M4 承载位置**：义务绑定指向真实场景/拍；事实锁绑定的承载拍与状态地址须恰好对应一条目标状态，不存在或歧义均拒绝（跨产物引用闭合，免费重编译同验）。 | `HARD`（`novel/stages/stage4/guard.py::check_binding_beat_refs`；反例 `tests/test_str06_a4_design_locks.py`、`tests/test_str06_fact_lock_stage.py`） |
| R-6.8 | **M5 称谓释放**：IR 释放集与 Design 显式释放集一致（同一称谓释放引擎）。 | `HARD`（`novel/stages/stage4/guard.py::run_mechanical_guard`） |
| R-6.9 | **M6 合法人物**：释放面人物已由 Design 声明参与或引入。 | `HARD`（`novel/stages/stage4/guard.py::check_cast_legality`） |
| R-6.10 | Guard 零错误不证明小说质量；本节点确定性重跑是 0 成本修复路（`deterministic_replay`）。 | `结构`（`novel/stages/stage4/guard.py::run_mechanical_guard`） |
| R-6.11 | **尚未向读者确认的事实（D15，rc14）**：VerificationIR 的 pending_reader_confirmations 从 knowledge(holder=reader, mode=known) 增量确定性派生，带 confirms 对应的**目标事实地址**与命题内容。按完整事实/知情地址投影进入实际建立或读取该事实的场景和确认场，命题 kp 句柄与自由事实键可不同；本场确认注明拍位，后场确认标为本章内不得确认。确认必须不早于实际事实建立，未知、已解除或计划不算成立。确认前允许线索、误导与猜测；不新增 LLM 节点。 | `结构`（`novel/stages/common.py::pending_reader_confirmations`＋`_scene_view`；反例 `tests/test_str06_execution_ir.py`、`tests/test_str06_stage4_protocol.py`、`tests/test_str06_workflow.py`） |
| R-6.12 | **章预算的最大余数传导（B3 前半）**：章预算保持全书 largest-remainder 整数分配、总和恰等于书级目标；场景相对篇幅（权重）变化按整数预算变化传导（可能波及多章），受影响章＝整数预算变化的章（确定性纯函数）。 | `结构`（`novel/stages/stage4/book_ir.py::chapters_with_budget_change`；反例 `tests/test_str06_stage4_protocol.py::test_budget_change_propagates_by_integer_allocation`） |
| R-6.13 | **章闭包：按拍读取与 T₀ 语义（B3 后半）**——状态读取定位到入场或具体拍（`StateRead.at`，wire 给 1-based 拍序由 compiler 解析），按「先读旧值、后写新值」解析：入场读取解析本场写入前的旧值（本场会写该地址不再是删除入场读取的理由），拍读取在该拍之前的建立上解析（先写后读）；T₀ 保留 unknown（显式值未知）、unset（显式解除）、set（含合法空值）与未建立的区别，不以 op 过滤初始项。compiler 按阅读序折叠逐场证明**已声明依赖的引用闭合**——读取在定位点未建立、作用域已失效或解析到 unknown 即 fail fast（`str06.stage4.compile` 免费重验）；语义依赖是否遗漏归质量验收，机械检查不冒充证明。「值未知」（unknown）与「人物不知道」（知情缺席或 believes）在 Design 词汇分开。 | `HARD`（`novel/stages/common.py::scene_read_views`＋`novel/stages/stage4/compile.py::init_establishments`＋`contracts/schema/state.py::StateRead`；反例 `tests/test_str06_chapter_closure.py`） |
| R-6.14 | **M7 声明读取已解析进 IR（R2，AUD-03）**：Scene 的每条 `StateRead` 都必须到达 IR——入场读取（`at is None`）的解析值进 `entry_state`／`entry_knowledge`，拍级读取（`at`＝场内拍）的解析值挂到对应拍的 `BeatIR.reads`（先写后读）。解析本身（未建立／unknown／定位闭合）由章闭包在 compile 期 fail fast（R-6.13）；M7 钉的是「解析结果确实到达了写手输入」——值漏包即红。 | `HARD`（`novel/stages/stage4/guard.py::check_read_projection`；反例 `tests/test_str06_r2_contracts.py`、`tests/hard_cases.py`） |
| R-6.15 | **M8 线程生命周期与投影（R2.4）**：线程 open 一次、操作不早于 open、close 后无操作、结局落在 kind 闭集内（canonical schema 构造期与 Guard 对读回的图复核同一判据，两处同源）；本场线程操作（`thread_ops`，must_realize 数据面）与入场时开放且与参与者相关的线程（`open_threads`，continuity／guard_only 数据面）必须按 Design 投影进 IR——身份漏包或过度提醒都红。 | `HARD`（`novel/stages/stage4/guard.py::check_thread_lifecycle`＋`check_thread_projection`；反例 `tests/test_str06_r2_contracts.py`、`tests/hard_cases.py`） |
| R-6.16 | **背景投影规则（R10b，C7/C10）**：写手面背景（WriterPacket 的 background 分区）只投**书级初态立足点＋建立之后仍被消费的当前状态**——消费者闭集＝后文声明读取、读者确认目标（confirms 的事实地址）与知情锁键、Hook 起笔/隐藏/禁揭地址（书末消费）、锁承载（拍上 kp_* 键的知情承载，以及 Binding `fact_bindings` 登记的事实锁承载状态——CS-F2 起 kf 不再是状态键）；unset/unknown 不作背景（局部条件停止向后投影≠宣布故事中已解除——它们留在 Machine IR 与拍级「必须写出」）；建立拍自身不构成后文消费（消费者位置必须晚于建立场）。只写不读的事实状态无消费者，留在其拍「必须写出」与 Machine IR，不再向后投影（背景历史回声的机械面）。**补齐面不受收窄影响**：`participant_background` 的全量参与者基线仍是依赖边生成（read_closure）的输入——收窄只作用于写手面投影，两个消费者、两种身份。渲染侧背景章内同一**条目**（不带场标的条目内容）只列一次。 | `HARD`（`novel/stages/common.py::narrow_background`（投影）＋`background_narrowing_violations`（判据对账面，与 `consumption_closure` 同源）＋`novel/stages/stage4/book_ir.py::compile_writer_book_ir`；红例 `tests/hard_cases.py`、绿对照 `tests/test_str06_r10b_writer_packet.py`） |

### R-7 · 按章正文（stage5 · str06.stage5.body；B4 章包写手）

`str06.stage5.body` 各章独立成文（D2 章包）：Writer 与 Validator 只收当前章包
（`ChapterWriterPacket`＝PacketBrief＋ChapterIR＋RealizationContract；称谓许可、
禁用字面等验证材料在 compile 时算好随章包携带，运行时从完整 `WriterBookIR`
确定性投影、不落重复章包文件），不读前文正文、Design、Characters 或其他章 IR。
每章一次首次生成＋同输入有限重写（attempt 进请求身份），章级机械验全过才算合法
候选；确定性候选选择（硬检查全过优先，耗尽后按相对目标偏差最小、平局取较晚）；
截断（finish_reason=length）即停该章，不重写、不进候选（D6）。各章经
`novel/model_slots.py` 共享书级与供应商总槽并发执行（默认
`contracts/invariants.py::PIPELINE_DEFAULT_CONCURRENCY`；并发度不进语义身份）。
提交顺序＝原响应耐久 → 检查 → 候选 → 原子写正文 → 原子写章回执。
恢复按有效提交集合：已完成 1、2、4、6 章时只补 3、5；局部失败保留其他合法结果，
授权、账务或取消故障关闭相应准入；章回执绑定当前章包摘要与正文字节。

| # | 要求 | 谁在查 |
|---|---|---|
| R-7.1 | 每章正文完整：非空、编码合法、无章节标题/编号/任务术语这类叙述外壳。 | `HARD`（`novel/stages/stage5/body_checks.py::validate_chapter_prose`）；canonical 闭卷是唯一写作路径，旧开卷路径已闭环为 `legacy_incompatible`，见 `B-03`／`B-13` |
| R-7.2 | 未释放专名不得出现在正文：重写耗尽后无合法候选即停止该章，不披露放行。 | `HARD`（禁用字面 compile 时算好：`novel/stages/stage4/book_ir.py::chapter_withheld_literals`；检查：`novel/stages/stage5/body_checks.py::validate_chapter_prose`） |
| R-7.3 | 章篇幅落在配置篇幅带内；超限只有篇幅带可披露（`length_disclosed`），硬失败不披露。 | `HARD`（`novel/stages/stage5/body_checks.py::validate_chapter_prose`）；超限后只有篇幅带可披露，不恢复自动收缩臂，见 `B-11` |
| R-7.4 | 章回执绑定章序、章包摘要、请求身份、验证版本与改名前正文摘要（D2，rc5）；不再绑定全书 IR 与前文链，各章独立有效。崩溃窗口（响应已存/正文已写回执未写）免费重放闭合。 | `HARD`（`novel/stages/common.py::check_chapter_receipt`） |
| R-7.5 | `ChapterReceipt` 的盘上形状由 `ARTIFACT_SPECS` 登记；回执只证明机械范围。 | `载体`（`contracts/schema/receipt.py::ChapterReceipt`） |
| R-7.6 | 正文文学质量未经语义评审（生产正文 Judge 已随 STR-06 取消），保持未评披露；章级机械检查不证明章内揭示位置与逐场义务兑现。 | `INFO`（`body_semantics_unver` 明示 `UNVER`，进交付披露）；发明越界见 `B-01`，「必须成立不必写在纸面上」见 `B-04`，潜台词与声音见 `B-10`／`B-09`，无表面探针的事实见 `B-23`，改写式泄底历史闭环见 `B-15` |
| R-7.7 | 逐章实际篇幅与预算偏差无条件报出（度量，不是门）。 | `度量`（`novel/stages/stage5/body_checks.py::validate_chapter_prose`） |

### R-8 · Hook 成文（stage5 · str06.stage5.hook）

`str06.stage5.hook` 单产＋机械验＋同 Context 有限重写（首次＋再 2 次，attempt 进请求
身份）；重写上限内无合法候选即停止并留 attempts 记录，不披露放行。Hook 只消费
HookContext（正式输入确定性重建），不读源 hook 文案——Stage1 `hook_copy`（章前文字）的
消费者是 `str06.stage2.book`／`str06.stage2.chapters` 的书级与第 1 章观察（`R-2` 导语／
`R-2.9`），Hook 写手仍不读源引子。

| # | 要求 | 谁在查 |
|---|---|---|
| R-8.1 | Hook 机械验通过：POV 与 Design 一致、起笔事实时序合法、禁止集零兑现、withheld 字面不出现。 | `HARD`（`novel/stages/stage5/hook_writer.py::validate_hook_prose`）；不剧透与「只引用正文真实句子」没有确定性防线，见 `B-12` |
| R-8.2 | Hook 回执绑定 HookContext 身份、正文 hash 与请求身份；拒绝稿不落正式位。 | `HARD`（`novel/stages/stage5/hook_writer.py::validate_hook_prose` + `novel/stages/chain.py::run_hook`） |
| R-8.3 | Hook 成文不扩大正文失效面：只改 Hook 不使无关正文接受失效（窄修改边界）。 | `结构`（`novel/stages/stage4/hook_context.py::compile_hook_context`） |
| R-8.4 | `HookReceipt` 的盘上形状由 `ARTIFACT_SPECS` 登记；机械通过不证明不剧透、材料顺序遵守与吸引力。 | `载体`（`contracts/schema/hook.py::HookReceipt`） |

### R-9 · 开篇文案（stage6 · 已退役）

**Stage6 整层退役（STR-06 切换）**：三候选生产、独立判官与人工改选面（`--pick`）不复存在。
Hook 的必要选择进入 Stage4 正式 Design（`R-5.6`），经 HookContext 确定性投影
（`R-6.3`）由 Stage5 单产成文（`R-8.1`）。旧书 `stage6/` 目录只读拒识为
`legacy_incompatible`；历史字节随 Git 保留，不再迁移或手补。

| # | 要求 | 谁在查 |
|---|---|---|
| R-9.1 | 【已退役】stage6.prepare／hook／approve 三节点与其产物家族、批准绑定、API/UI 面随 Stage6 整层退役；`contracts/artifacts.py::RETIRED_ARTIFACT_PATHS` 登记旧路径拒读。 | `不适用`（退役条款，无可执行机械义务） |
| R-9.2 | 【已退役】stage6.semantic 双盲判官随 2026-09-15 全自动裁决退役；依赖它的三条判据（hook_semantic_contradiction／hook_candidate_contradiction／hook_quality_rank）不再注册。 | `不适用`（退役条款，无可执行机械义务） |

### R-10 · 命名与交付（stage7 · naming／assemble）

`str06.stage7.naming` 只做 NameMap v5 赋值（读名称结构与 Blueprint 的只读投影）；
`str06.stage7.assemble` 是替换与装配的唯一应用者：章回执链核对 → ApplyNames 单遍替换
→ 残留检查 → TXT 先落盘 → manifest 最后提交。Stage7 不改写 Stage5 任何字节；
交付可确定性重放，同输入同字节。

| # | 要求 | 谁在查 |
|---|---|---|
| R-10.1 | NameMap v5 编译闭合：名称位一致、无姓氏冲突、保留项合法（带理由）。 | `HARD`（`novel/stages/stage7/naming.py::compile_name_map`） |
| R-10.2 | ApplyNames 单遍替换后旧名零残留（遵守 claimed/kept；目标名含旧字面不误报）。 | `HARD`（`novel/stages/stage7/naming.py::check_residual`）；残留 0 条不等于人名都换干净（替换覆盖率验不了），见 `B-21` |
| R-10.3 | 章回执集合核对（D2，rc8；B4 后半补五态；rc6 身份拆分）：逐章回执状态、当前编译出的写手视图摘要（`writer_view_sha256`，材料含 renderer 与写手协议版本）、验证输入摘要（`validation_sha256`：禁用字面、称谓许可、篇幅带）与正文 hash 绑定；Machine 层摘要（`machine_view_sha256`）仅审计不参与核对——只改 Machine 层字段不触发重购。有效／缺失／拒绝／未知／失效（绑定不成立的接受态回执）五态分开按 item 报告（I-42），不静默跳过；集合齐备才装配就绪（not_ready 显式，不落新交付），某章失效不截断其他章有效性。 | `HARD`（`novel/stages/stage7/assembly.py::verify_chapter_receipts`／`chapter_access`，共用 `_binding_problems`） |
| R-10.4 | TXT 先落盘、manifest 最后提交；manifest 与文件 hash 逐字一致，消费者核对 manifest 才算就绪。 | `HARD`（`novel/stages/stage7/assembly.py::build_delivery_manifest`） |
| R-10.5 | 同输入交付字节一致（确定性重放）；重放只允许缓存与登记的历史采用，零新购。 | `HARD`（`novel/stages/stage7/assembly.py::assemble_delivery`） |
| R-10.6 | 新名的阅读质量未经语义评审，保持未评披露。 | `INFO`（`naming_quality_unver` 明示 `UNVER`，进交付披露）；新名质量没有确定性判据，见 `B-22` |
| R-10.7 | 全书相对书级目标的偏差无条件报出（±10% 读数，度量不是门）；逐章篇幅披露与未评语义分开报告。 | `度量`（`novel/stages/stage7/assembly.py::build_delivery_manifest`） |
| R-10.8 | `NameMapV5` 与交付 manifest 的盘上形状由 `ARTIFACT_SPECS`／唯一 writer 契约治理；NameMap 是交付变换契约，接受正文、回执与已购证据独立保护。 | `载体`（`contracts/schema/naming.py::NameMapV5`） |
| R-10.9 | Stage7 不回写 Stage5：投影不落交付、Apply 不改映射（权限分界）；换 NameMap 只影响交付。 | `结构`（`novel/stages/stage7/naming.py::compile_surface_replacements`） |
| R-10.10 | **章标记与分章身份**：成书按阅读序逐章加「第N章」一行（章号由系统添加，写手协议据此约定不写章号），引子放最前、不加标题；交付身份由有序的 `(chapter_ordinal, prose_sha256)`、Hook 成文 hash、改名映射与保留字面、`ASSEMBLY_FORMAT_VERSION` 派生——拼接相同但分章不同 ⇒ 身份不同。章回执绑定不含章号的改名前正文摘要（rc5）。 | `HARD`（`novel/stages/stage7/assembly.py::assemble_delivery`／`delivery_identity`） |

### R-11 · 人工审阅、模型权限与确定性调度

界面、CLI 与受托审阅消费同一后端证明；字段形状见 `docs/schema/_api.md`、`docs/schema/_orchestration.md` 和 `docs/schema/work_units.md`。

| # | 要求 | 谁在查 |
|---|---|---|
| R-11.1 | 只绑 `127.0.0.1`。四条各自独立成立的理由：会在本机执行进程 · 那些进程花真钱 · 会往盘上写文件 · 传输的是不外流的版权正文 | `HARD` |
| R-11.2 | localhost 也要防 CSRF：变更类路由要求自定义头 + 校验来源。**一个花钱的端点开在本机上，浏览器里任何页面都够得着它** | `HARD` |
| R-11.3 | **服务进程绝不落【产物】盘**，产物变更一律走 CLI 子进程；它只写 `_service/` 中的上传、一次性编辑计划、作业事件/日志与人工审批状态。上述都不是小说产物。⚠ 这一条经过带标注的收窄，但没有放宽产物写口，见下方那个框 | `HARD`（测试钉死） |
| R-11.4 | **界面不重算任何已经在校验器/闸门里的判据**，只呈现或链接。造第二个判官就是造第二个落点 | `HARD` |
| R-11.5 | **七**个核心对照视图：**原文↔切分（`R-1.8` 的唯一闸门）** · 原文↔骨架 · 计划↔写作上下文（逐场景）· 成稿↔材料（含原文↔重构稿按锚点切块）· 结论↔可编辑成稿 · 三候选文案 · 新旧人名表 | `不适用`（这是一份**清单**，不是一条可判定的约束；每个视图各自的纪律在 `R-11.9`/`R-11.13` 等条里） |
| R-11.6 | 产物新鲜度**三态**（新 / 陈旧 / **不知道**）；校验徽章**三态**（通过 / 通过但有查不了的 / 失败） | `HARD` |
| R-11.7 | 付费与 0 成本动作在界面上分色，且**贵贱只有一处算**（服务端），前端一行判贵贱的代码都没有 | `HARD` |
| R-11.8 | 付费入口一律经二次确认，并把**将要执行的那条命令**摊开显示（展示与执行共用同一个构造函数） | `HARD` |
| R-11.9 | 原文↔重构稿的分界线由**必须逐字写入的原话**定：同一串字节在两侧各定位一次，**两边都锚到才切**。没有锚的拍并进相邻块，**不给它编一条分界线**，并常驻显示锚点覆盖率 | `HARD` |
| R-11.10 | 同一本书同时只跑一个作业（第二个请求 409）；Stage5 的全书默认与 `--chapters` 定向写作共用这一把书级键，只在作业内部按章并发。不同 `sid` 的后台作业互不阻塞 | `HARD` |
| R-11.11 | 作业记录 = 这次跑用的是哪条命令、哪版代码。**服务重启后 `运行中` 一律改判 `不知道`**，不是「成功」；已冻结的 `executor_backend` 快照槽若为 `null` / 缺失，只能解释为未记录，不能按今天的默认配置倒推历史身份。旧 `executor_profile` 只在读边界无损迁移为 `kind=agent`，新序列化不得继续发旧字段 | `HARD` |
| R-11.12 | **不做作业取消**——调用在途时钱已经花了，kill 掉省不回来 | `不适用`（一条**负空间**声明：它要求的是「不建某个功能」，没有可判定的对象） |
| R-11.13 | 剧透开关只关四个字段，**已知旁路仍在**，界面上常驻这句标注 | `结构`（`contracts/invariants.py::SPOILER_DISCLOSURE`，随 `/api/enums` 整份下发；由 `tests/test_api_discipline.py::test_the_spoiler_disclosure_boundary_is_published` 查） |
| R-11.14 | executor 选择只有一个公开事实源：`GET /api/agent/capabilities`（路径保留）。C3 硬切后现役唯一 wire 是 `ExecutorBackend = {kind:'legacy', profile:LegacyLlmProfile}`；本地 Agent CLI（`kind=agent` / `AgentProfile`）已从契约与执行面删除，历史账经 `HistoricalAgentBackend` + `retired_formats` 只读拒绝。Legacy 由 `novel.llm.providers` 关闭 provider/model/thinking/effort/node。`PipelineAgentProfiles.schema_version=4`；加载 v1–v3 丢弃 commander 与 agent 条目。前端不得硬编码选项；`default_profiles=null` 只表示尚无产品预置。用户常用配置经 settings CAS，三态 unset/configured/unreadable | `结构`（`contracts/schema/orchestration.py::ExecutorBackend` + `novel/llm/providers.py` + `novel/orchestrator/executor_capabilities.py` + `novel/orchestrator/agent_settings.py`；`tests/test_agent_capabilities.py`、`tests/test_retired_formats.py`、`tests/test_cli.py` 查） |
| R-11.15 | 显式 Executor profile 必须从 preview/start 的同一计划冻结到 Step、JobStart、argv 与 RunContext，并到达该节点 DirectLlmExecutor。可执行 node/flags 组合以 `novel/orchestrator/nodes.py::PROFILED_LLM_RUNS` 为准，未登记组合在 wire/dispatch 前拒绝 | `结构`（`novel/orchestrator/nodes.py::resolve_executor_backend` + `novel/orchestrator/jobs.py::plan_start` + `novel/cli/main.py`；`tests/test_orchestrator_nodes.py`、`tests/test_cli.py` 查） |
| R-11.16 | 直接 job 的 legacy executor backend 与并发策略只按现役 capability 闭集选择并冻结到 JobStart / Step / argv / RunContext；请求配置独立于权限，以 Step.request_profile 与 CLI --request-profile 贯穿实际请求、请求身份及免费复检。免费 check 沿用配置，但没有 executor backend、付费或外发许可；缺已购回答即停，禁止退回默认 high。checkpoint 与历史采用必须闭合 profile/请求/输入/回答/session/attempt/ledger | `结构`（`novel/orchestrator/nodes.py::resolve_request_profile` + `novel/cli/main.py::_request_profile_of` + `novel/stages/chain.py::execute`；`tests/test_str06_cli_dispatch.py`、`tests/test_str06_product_entry.py` 查） |
| R-11.17 | 正文外发与付费分别授权。`PreviewRequest.egress_authorized` 默认 false；resolved legacy profile 与 bool 必须同源冻结到 JobStart/Step/argv，只有 true 才加入 `--allow-egress`。C3 起不再有 Agent CLI dual-session 路径 | `结构`（`novel/orchestrator/nodes.py::resolve_executor_egress` + `novel/cli/main.py::_dispatch`；jobs/API/CLI 回归查） |
| R-11.18 | 直接 Executor 组合统一登记在 `PROFILED_LLM_RUNS`；producer 使用固定 typed response，由 DirectLlmExecutor 提交，schema/HARD 由代码判断。Stage4/5 prepared 与 historical adoption 纪律不变（旧链 `stage4_runs` 的 repair aggregate/prepared 面随旧链退役；新链同族边界是 `PaidRequestSession` 的 wire 采用与跨根历史采用，身份逐字相等才零新购复用） | `结构`（`novel/orchestrator/nodes.py::PROFILED_LLM_RUNS` + `novel/llm/run_journal.py` + `novel/llm/paid_requests.py::PaidRequestSession`；stage 恢复与 jobs/API/CLI 回归查） |
| R-11.19 | `/api/agent/capabilities` 是 legacy provider/model/effort/thinking 的唯一选项源（C3 不再发布 Agent CLI drivers）。新运行表单按 configured→产品默认→空草稿初始化；unreadable 关闭预览/启动。`RunContractV2` 冻结授权与 profiles；code/protocol 归 ExecutionEpoch。自动跑书由确定性 RunSupervisor 推进 | `结构`（`novel/orchestrator/agent.py` + `novel/orchestrator/executor_capabilities.py`；RunContractV2/epoch/授权回归查） |
| R-11.20 | **确定性调度是唯一推进者；`NODES.lifecycle` 是全部现役节点职责的唯一登记。** Executor 只生成候选并在本工作单元内自修；typed 接受、订正、定向返工、不确定或停止只由现役 typed 面提交（历史受托审阅与各 Stage 人工闸已随 2026-09-15 全自动裁决及 STR-06 切换整层退役；其存量解码面已随 STR-06 关闭批删除，历史账关闭式拒读）。producer 统一验证、归一、归档、记录来源与正式写盘。ready 在唯一冻结 snapshot 上携 opaque execution token，execute 取得 admission claim 后只做一次活动 surface CAS；blocked 登记确定性 recovery 优先直接交确定性 Executor。推进要求当前有效产物与所有必需前置同时成立，文件存在、已读或 exit 0 均不能替代。HUMAN_NODES 为空。每步完成重新读盘规划，恢复先重放已保存候选、决定和提交 journal；unknown、非本节点 owner、预算耗尽或无进展停止。预算、会话、审批与执行权分离：typed 决定不直接授予 paid permit，付费与外发分别校验。每个 root/follow-check 仍继承同一 permit envelope、独立 step index 与原子 claim；调度者持有 parent owner lease 并 drain 全部 claims/beacons 后才释放书锁。工作单元额度、显式追加与原配置续跑的完整边界见 `docs/schema/work_units.md`。 | `结构`（`novel/orchestrator/operator.py::next_action` / `::permit` + `novel/orchestrator/agent_supervisor.py` + `novel/orchestrator/run_step.py` + `novel/orchestrator/admission.py` + `novel/orchestrator/write_admission.py::operation_scope` + `novel/orchestrator/work_unit_grants.py` + `novel/orchestrator/agent_continuation.py` + `novel/orchestrator/agent.py`）；单 snapshot execution-token、claim 后单 CAS、现役节点工作单元登记、原配置续跑及持续会话、原回答与成本/会话尾账零模型恢复、typed 决定幂等提交和退休、drain、reconcile 回归查 |
| R-11.21 | RunSupervisor 是现有 orchestrator 内的**确定性状态机**，不得调用 LLM、不得形成第三个模型角色。Agent 状态独立取 `pending / running / paused / ok / landed_red / failed / unknown`；parent ended 保留原始退出码，并只由 producer 明确记录 `exit_origin=novel_cli / commander / supervisor`（唯一闭集 `AGENT_RUN_EXIT_ORIGINS`，缺失时省略字段，不改 wire_version；`commander` 仅历史行解码，新 run 不再产生该来源）。外部 CLI 的 raw 退出码、parent 状态与盘面 delivery 三轴不得互推；只有 `1 + novel_cli` 投影 `landed_red`，缺来源的 1 保守投影 `failed`，不得凭 reason/note 散文推断来源，原 note 保留且仅真正 landed_red 可补已落盘说明；append-only 生命周期至少含 `started / epoch_opened / paused / resumed / epoch_closed / ended / reconciled`。公开 pause/resume/stop API 统一返回 202，只确认请求已受理，不能把 HTTP 返回冒充 lifecycle 终态：pause 先停止接纳新节点，后台等待当前 claim/beacon 安全闭合后才写 paused，不取消正常执行中的付费节点；代码与执行契约身份未变时 resume 在后台复用当前 epoch；漂移时同一 parent run 打开新 epoch、免费复检并新建 provider session，不续用任何 reviewer session。stop 在后台取消该 run 所有 Executor 进程树、drain 后才写 ended，之后不可 resume。旧链的系统暂停分支（delivery_recovery）与 Stage4 repair/replan 的 epoch 滚动已随旧链退役，只服务存量 run 解码。服务重启后无法证明进程状态必须保持 unknown，不得洗成 paused 或自动恢复；人工 `reconciled` 只能复用 admission（文件沿用历史命名 `commander_admissions`，executor 现役）的 owner-stopped 证明、完整 beacon 主体与 hash-bound CAS，seal 成功后才向同一个 AgentRun v2 事件流投影，崩在两账之间可按同 subject 幂等补尾，不得另造一套恢复状态 | `结构`（`novel/orchestrator/agent_supervisor.py` + `novel/orchestrator/agent.py::reconcile_run` / `::fold` + agent pause/resume/stop API + append-only event fold）；202 异步受理、零 LLM 调度、并发 resume、同/跨 epoch session、stop 永久、崩溃 unknown 与 reconciled 单一 CAS 回归查 |
| R-11.22 | 历史审阅工作包的 `submission_options`（Reviewer workpack 语义）是不可变执行 binding；node/approval 的 actor、review token、范围、hash 与闭集动作均由当前包唯一派生。Stage2 不设审阅席位或内容纠错预算：语义结论随语义未审披露，旧 Stage2 席位账只保留只读审计解码，不参与新工作单元。Stage4 A/B、Stage5 closure/disposition、Stage6 Prepare 与定稿审阅等 scope 已随 STR-06 切换整层退役，其存量解码面已随 STR-06 关闭批删除（席位账冻结拒读）。Stage0 发现误剔漏剔仍可 typed stop。机械节点不新建模型审阅；候选 SHA、token、run/epoch 或 session 改变不能重置预算，相同错误无进展提前停止，未知执行不得删账后重买 | `结构`（`tests/test_retired_surface_scan.py` 与 `tests/test_approval_retirement.py`（审批解码面与历史席位账不得回来；`approvals.py` 已按 2026-10-04 裁决删除））；binding、typed stop、业务 identity、持久拒因/预算、重启与 epoch 漂移回归查 |
| R-11.23 | 生产 AgentRun 的代码身份必须**可复验**：`preview`、execution epoch 与产物 provenance 如实记录 `<commit>` 或 `<commit>-dirty.<digest>`（未提交改动逐字节进身份，不清洗），unknown 仍在 probe、spawn、模型 wire 和生命周期写入前拒绝。**带未提交改动的工作树可以启动付费跑书**（2026-09-26 用户裁决）：脏身份随 epoch 与 provenance 落账，运行中编辑工作仓由 `_epoch_identity_current` 检出并按 `code_drift` 滚 epoch、记录新身份与原因，不静默续接旧 epoch；已购结果复用仍按请求身份逐字核对，不把跨版本采用冒充同身份。该限制不改变普通开发 job/CLI 的既有边界 | `结构`（`novel/orchestrator/agent.py::_require_reproducible_code_version` + `novel/orchestrator/agent.py::_epoch_identity_current`）+ preview 可读/dirty 可启动与 unknown 早拒、epoch 滚定回归 |
| R-11.23b | 单本受管运行的可选 pause_before_node 以登记节点为排他上界；preview、start、AgentRunStart 和 RunContractV2 保持同一最终值。Supervisor 在任何当前动作或审批闸前按工作流顺序关闭目标节点及后续节点，返修看实际 owner；blocked 中的消费者节点不替代 waiting_on 登记的人工审批闸 owner，Blueprint 等待 Global 不阻止上界前的 Global 审阅；联合首抽覆盖两侧物化归属，未知关闭式停止。到界先关闭 epoch 并排空、释放 owner，结果为 stopped/范围边界，不伪造 pipeline_complete 或整书交付。continuation 省略字段继承原边界，显式新值或 null 才改变范围；保留原 profiles 和累计工作单元额度。旧记录无边界时不向原 hash 补 null。 | `结构`（`contracts/schema/orchestration.py::RunContractV2` + `novel/orchestrator/agent.py::plan` + `novel/orchestrator/agent_supervisor.py::range_boundary_reason` + `novel/orchestrator/agent_continuation.py::inspect`）+ Supervisor 零派发、HTTP 持久读回、续跑省略继承/显式扩大反例 |
| R-11.24 | 运行页必须把三种事实分开呈现：节点 beacon 给“当前正在执行什么”，operator 同快照给“下一节点/下一处理者”，AgentRun lifecycle 给“当前或最后 execution epoch、rollover 原因与累计换 epoch 次数”。空 beacon 不得把下一节点伪装成正在执行，epoch rollover 也不得藏在日志里；三者都只呈现后端事实，不由前端推断新鲜度或调度 | `结构`（`web/src/screens/run/RunScreen.tsx::ResumeFrontier` / `::AgentRunBlock`）+ 前端回归 |
| R-11.25 | 受托 Commander 审阅已退役；本条的持久化语义继续约束存量书历史决定与 Stage5 reviewer rewrite 执行链的解码。历史/Reviewer 决定先持久保存原始回答，再通过同源 typed loader，把可执行决定按内容寻址写入 `_service/edits/<sid>/reviewer-action-<sha256>.json`，之后才取得当前 permit 与调用 canonical producer。新决定必须携 `work_unit_id / work_unit_turn_id`，并以工作单元原响应、business、waiting_on、typed action SHA、原物理 session 与 provider response digest 联结；恢复窗口的 run/epoch 包装变化不改变真实调用所属 parent。工作包、请求、原始响应和 typed 决定分别落盘；缺失或损坏保持 unknown，不能重新发放内容预算。`NODES.lifecycle.review_actions` 登记 accept/编辑/rework/uncertain/stop 的节点实际子集，`approval_projection` 只投影同一 operator 快照的有效审阅原状态。已保存响应优先免费重验、补提交；首次 typed 证明不可改写；后继 action 仍引用原内容轮且决定、理由、编辑值相同，先保存并核验后继再退休旧记录，中断可补尾。格式/内容拒绝需先保存反馈再关闭重放，同一错误无进展提前停止。canonical exit 0 后标记 adopted；exit 1 表示已落盘但未通过，exit 2 表示明确拒绝，exit 70 表示未知，不能互相代替。rewrite request 的永久 authority 保留原 action 字节、request raw/self SHA、原内容轮、唯一成功 provider attempt、对应 logical-session turn 与成本账；跨 parent 补提交必须持真实内容调用 owner-stopped lease 直到 seal 完成。历史旧席账和协议恢复只供冻结旧契约的审计与原证明复读，不参与新工作单元运行或预算重置 | `结构`（`novel/llm/ledger.py::reconcile_commander_call` / `novel/llm/session_audit.py::reconcile_commander_event`（尾账复验原语，现役）；`ReviewerActionRecord` 与存量审批账解码面已随 2026-10-04 清理批删除，旧根一律 legacy_incompatible 拒识）；原回答/typed决定/提交分离、跨parent、无模型补提交、未知保留与预算不重置反例（历史解码函数已随 STR-06 关闭批删除）|
| R-11.26 | **C3：Agent CLI JSONL 子进程调用层（原 `novel/llm/providers.py`）已删除。** 现役 executor 走直接 LLM API transport。历史 Agent CLI 账上的 `error_kind` / session 行仍须可读（`novel/llm/ledger.py` 存量注记） | `结构`（`novel/llm/providers.py` + `novel/llm/ledger.py`；legacy transport 与 `tests/test_retired_formats.py` 查） |
| R-11.27 | **确定性重建白名单**（2026-09-15 全自动裁决）：后置人工决定（如 Stage6 文案改选）触发的自动重建，只允许进入 `contracts/invariants.py::DETERMINISTIC_REBUILD_NODES` 登记的节点（全部 `paid=False` 且 `generation_mode='deterministic'`）加只读交付终验；任何自动重建路径不得触发付费节点，也不得借道整书 start「顺便补齐上游」。白名单钉扎测试双向守卫：混入付费节点即红，白名单清空即红（分层被整体绕开同样不可接受） | `结构`（`contracts/invariants.py::DETERMINISTIC_REBUILD_NODES` + `tests/test_invariants.py::test_deterministic_rebuild_whitelist_is_free_and_deterministic`）+ 白名单收缩/清空反例 |
| R-11.28 | **上传幂等自动开跑**（2026-09-16 批次 D）：产品路径上传即授权。`POST /api/upload` 在 `authorized=true` 时必须携客户端幂等键（Header `X-Novel-Idempotency-Key` 或 query `idempotency_key`），并在任何 stage0/agent 作业前把意图 append 到 base 级 `_service/upload_autostart_intents.jsonl`；同键重放返回同一 sid / stage0 job / run，不得重复 start。内容寻址去重仍由 `uploads.save_upload`（同字节同文件名 → 同 sid；同字节不同文件名 → 不同 sid）。stage0 失败只记 `stage0_failed` 并返回 `autostart=skipped_stage0_failed`，**绝不开付费 agent run**。服务启动 drain 仅对「requested 且 stage0 已成功、尚未 `run_started`」的意图补开一次 `agent.start`（冻结授权与 profiles）；书锁冲突保持 pending。新 `RunContractV2` 必须冻结 `source_sha256`、`rules_fingerprint`（全流水线判据指纹）与 `book_budget_ceiling`；历史行缺省 `None` 并在序列化省略；原配置续跑校验冻结字段不得被今日默认静默覆盖。`authorized=false` 保留仅 stage0 的合成/兼容路径，不铸付费意图 | `结构`（`contracts/schema/orchestration.py::UploadAutostartEventV1` / `::RunContractV2` + `novel/orchestrator/upload_autostart.py` + `novel/api/routes/ingest.py` + `novel/orchestrator/agent.py` / `agent_continuation.py`）+ `tests/test_upload_autostart.py` |

以下只保留跨层顺序与拒绝边界，字段清单不在此重复：

- admission 与 outer AgentRun 分开；owner lease、beacon 与 reconcile 不能猜关联。reconciled 保留 unknown，
  不伪造 ended、退出码或 drain；完整证明与 CAS 见 `tests/test_commander_admission.py`、`tests/test_agent_reconcile.py`。
- 原回答和 typed 决定先 durable，执行权另取 current permit。Stage5 rewrite 先物化 canonical request，再封存
  原 action 与 authority receipt；receipt 是提交点，最后退休 active action。仅有 archive 或 receipt、坏账、
  跨 parent 无真实 owner-stopped 均拒绝，见 `tests/test_agent.py` 与 `tests/test_commander_receipt_recovery.py`（历史证据链测试已随关闭批退役）。
- rewrite execution 在 driver/wire 前写 request journal；只按 latest intent 的 exact checkpoint 恢复。
  terminal 后的免费重验/编辑先写 successor prepared，合法后继或明确 consumption 前不替换请求。
  Stage5 partial batch replay 的 exit 2 不得删掉唯一 action 后重买；先保留证据并停止。
- Stage5 trim 的 retain/rewrite 共用原 Review、完整有序 manifest 与 contract 的业务身份；逐项写回导致 token
  自变不重置内容额度。Stage4 replan 的 typed stop 不产生审批或执行权，原拒绝保持（受托审阅模式已退役；legacy 拒识与解码面删除见 `tests/test_approval_retirement.py`）。
- 确定性 prepared 补尾优先于新审阅，且只消费唯一来源及其 exact CAS；同时存在互斥 prepared 必须拒绝。
  历史 journal 仅作为被选中恢复动作的 external input，不把历史全树加入活动身份。
- Stage4 紧凑 wire 只压缩运输，不能减少合法 submission option。actor、范围、token 与 SHA 由同一 immutable
  request 可信注入，完整候选仍经现役 loader/producer；重复 JSON 键、越权 ID 和重叠操作必须拒绝。
  纠错空项的保留/撤销语义只由正式 failed edit 绑定开启，不能从 ledger 的 correction 标签推断。
- schema 字节绑定同一 provider session；已保存 canonical 决定的重放不重新经过 compact wire。登记协议恢复
  只按原日志和完整身份复验，不改变模型判断、不返还预算、不把冻结旧席账用于新工作单元。
  该历史解码边界（reviewer_legacy 及其复验链）已随 STR-06 关闭批删除；旧席账只读冻结。
- 内容轮预留在 Popen 成功之后、stdin 发送之前；本地预检或 Popen 失败不扣内容额度，
  已预留后的崩溃或未知仍计入。当前追加只用不可变工作单元授权，不退款或重开旧账。
- 模型回显必须与冻结 profile 一致；无回显仅在已登记 driver-enforced 且 start/resume 钉死型号、留有 CLI 版本时
  采用 requested identity，actual_model 与 ledger 未知项不倒填。所有路径共用
  `novel/llm/model_identity.py`，反例见 `tests/test_agent_model_identity.py`。
- 费用与会话按真实 parent/producer/session 关系归属，不按时间猜。外发授权逐项点名实际可达的 provider、
  driver、model、effort 和模式，不授权被 override 遮蔽的默认项，凭据不进入材料或日志。
- 服务使用 CLI 子进程隔离 CPU 与崩溃，预览和执行共用 argv；服务只写 `_service/`，事件只追加。
  出站 prompt 和输出产物是两道检查面，不能用产物扫描代替材料边界。反例见 `tests/test_layering.py`、`tests/test_api_discipline.py`。

### R-12 · 编排与作业执行

按冻结命令执行、记录真实成本与显式终态；响应形状和运行身份必须在继续消费前验证。

| # | 要求 | 谁在查 |
|---|---|---|
| R-12.1 | **编排只表达真实生产依赖与接受边界。** 满足 cold-start 证明的新书先跑 `stage2.characters` 再跑 `stage2.global`（analyze **不再**为新书联合写 Characters）；Blueprint 由统一机械准入放行后免费汇合进入 Stage4。已有任一侧产物、历史回答、checkpoint 或 prepared 时先恢复或修订对应 owner，不能仅凭正式文件缺失重新首抽。存量 analyze receipt / draft repair 路径仅服务历史解码，删除条件见 STR-03-S2-CH。独立 Global 修订请求消费必要人物 canonical_surface/surfaces 投影（无 role）。Blueprint 在 driver/cache/wire 前要求统一机械准入通过：当前人物、Global、MainText 与称名投影可读、当前有效、无未恢复 INVALID 且所需机械检查全部通过；语义结论随 Stage2 语义未审披露，不再要求审阅接受。编排按准入阻断原因把恢复指向对应 owner 的编辑/复验入口。人物实际知识投影改变时，旧链由 stage2 characters 的 rebind 准备（prepare_if_changed；该模块随旧链退役，只服务存量解码）证明其余输入未变、来源完整且外键闭合后免费重绑；原模型/成本保留，unknown 不追认。人物投影缺失或损坏、人物正式登记 current 时只排 `stage2.characters --revalidate` 免费重建；live INVALID 保持 unknown 并交同 owner 编辑/复验，不因缺投影默认付费重采。Stage4 首项要求统一机械准入通过（`stage4_ready`）且 Blueprint 联合 HARD 通过。Stage2 唯一显式 `--check-only` 仍是 Blueprint，机械测量由 Stage5/6 在真实消费点即时执行，不造队列节点。**F8b-3：上述旧链 Stage2/4 编排规则随旧链整层退役**（旧节点键随 STR-06 关闭批的存量解码面删除，统一机械准入名册收空壳拒判）；新链准入由完成度与续跑判定承担：登记缺失 ⇒ 普通付费 todo，live `.INVALID` ⇒ 收件箱阻断，未证明 ⇒ `unknown` 留人，免费复检统一走节点级 `--check-only` | `结构`（`contracts/invariants.py::NODES` + `tests/test_retired_surface_scan.py`（旧编排面不得回来）（旧准入名册 mechanical_admission.py 已随 2026-10-04 清理批删除）+ `novel/orchestrator/queue.py` + `novel/orchestrator/state.py`）+ CLI/API 零 wire 早拒与联合复检回归 |
| R-12.1b | 节点要诚实报出「这条规则本次没有跑，不是跑过没事」，且**流程里必须钉死一次 0 成本复检**。⚠ 别拿「这次补跑是绿的」当结论——那是运气，不是保证 | `HARD` |
| R-12.1c | legacy LLM transport 的 Stage2 逐章抽取仍按章全量并发、硬上限 32；生产 Agent 路径改用独立逐章 session pool，实际取 `min(4, NOVEL_CONCURRENCY, 待跑章数)`。Blueprint pool 在统一机械准入通过后启动，准入未过不产生任何 Blueprint wire。Stage4A 是另一份节点级私有 session pool：策略请求串行先行，策略 checkpoint 后的逐章设计实际取 `min(4, NOVEL_CONCURRENCY, 待跑章数)`；配置可以把单书池下调但不能放大到 4 以上，也不复用/改写其他节点的产品上限。该 4 上限只描述已退役的旧链 session pool。任何并发完成顺序都不得串错逐章 usage/provenance/checkpoint（**F8b-3：旧链 `stage2_runs`/`stage4_runs` 的 pool 实现随旧链退役**；新链 `str06.stage2.chapters` 与 `str06.stage5.body` 各自按本次冻结的节点并发执行，并经 `novel/model_slots.py` 共享书级与供应商总槽（B4，D2）。节点并发、整书总槽、供应商总槽都是 `PIPELINE_TOTAL_CONCURRENCY_MIN`～`MAX` 的整数，由 `PipelineConcurrency` 配置；没写覆盖的节点保持运行默认。实际在飞数取节点并发、两个总槽和待处理项数的最小值。`novel/config.py::concurrency_for` 是每节点并发的唯一读点。**B1：并发覆盖只登记这两个逐章节点**（`PIPELINE_PARALLEL_NODES`），并发度不进语义身份） | `结构`（`contracts/invariants.py::PIPELINE_PARALLEL_NODES` + `novel/model_slots.py` + `novel/llm/run_journal.py` + `novel/config.py::concurrency_for` + `novel/cli/main.py::_dispatch` + `novel/llm/ledger.py::chapter_usage`）+ 共享槽峰值/槽位归还/配置 1 串行/逐章账回归 |
| R-12.1d | 供应商限流、账号 entitlement 与产品/节点并发闸必须分开陈述：legacy Stage2/5 transport 项目硬上限是 32，Stage2/Stage4 的 Agent 逐章 session pool 上限分别是 4/4；DeepSeek V4 Pro 当前官方账号级上限是 500。Agent pool 的 4 是当前 Claude executor 的**本进程、单书节点安全边界**，依据同一 executor 在 Stage2 真实整书 13 路首波出现快速 403 拒绝后的保守收敛，不冒充 Claude 账号级通用上限。HTTP 403 单码也不能推出五小时额度或账号总限：必须再区分明确用量窗口、登录过期、永久权限与“`Request not allowed` + 已确认 0 token”的瞬时拒绝。它只约束对应单书节点池；多书 agent 运行的总并发仍由独立的 `novel/orchestrator/agent.py::MAX_CONCURRENT` 管，不得把 4 写成账号总闸。正常产品路径不为制造 429/403 越过自身硬闸。真实观测必须显式确认付费、禁缓存、限制请求/重试/token，并保留逐次 ledger 与不含 prompt/响应正文的报告；429/瞬时 403 的重试/退避由可重复合成故障守卫 | `结构`（`tools/observe_provider_limits.py` + `novel/llm/providers.py` + `novel/model_slots.py` + `novel/llm/run_journal.py`）+ 2026-08-16 DeepSeek 真实 32 并发观测 + Stage2 Claude 13 路首波拒绝实证 + 合成 429/403/退避回归；DeepSeek 供应商口径见 [官方限速说明](https://api-docs.deepseek.com/zh-cn/quick_start/rate_limit) |
| R-12.2 | **结构化调用点必须声明全部必需顶层键**；parse / require / schema / validator 任一闸失败都驱逐坏缓存，修复稿始终重走 `json.loads → require → model_validate → validator`。禁止异常驱动地降低 effort / thinking，也不改供应商或 schema mode；Flash→Pro 保持同 effort / thinking / max_tokens。Stage2 blueprint 使用按失败类型登记的恢复策略：reasoning 中完整 JSON 一旦通过全部闸立即采纳并铸 checkpoint，不再为正常 content 多发一采；BOM / 容器尾逗号只做窄本地修正并全量复验；有可忠实恢复材料的 `answer_lost` / parse / require / schema 首次进入本 run 唯一 batch，修复拒绝或未通过后最多一次 Flash 定向修复、再最多一次 Pro constraint rebuild；dynamic validator 不交给通用 JSON 修复，只走一次 Stage2 定向恢复与一次 Pro rebuild；无有效材料的 `answer_lost` 不重复相同 Flash 原请求，直接 Pro rebuild；`finish_reason=length` 不做同配置重采，进入下一种已登记动作，仍失败即终态；可确定性消化的 blueprint schema/validator 失败（如历史 XOR 双写文案、章内 presentation 句柄）不触发同档付费重采，由 compile / response-repair 收口。传输交付 attempt 与内容/语义修订 turn 必须分账：Agent 对 429、408/425/5xx、已识别连接/timeout、明确模型容量不足，以及“403 `Request not allowed` 且 token 明确全为 0”执行首发加最多三次的 2/4/8 秒有界退避；四次逐项进入 ledger，但没有可采纳回答的 attempt 不进入 session turn/checkpoint `attempt_links`。五小时用量窗口与登录过期不秒级热重试而进入可恢复等待，其他 403 按权限错误立即终态；legacy transport 的不可重试 4xx、配置错误、预算拒绝仍立即终态。失败签名统一绑定 request fingerprint、模型/effort/thinking、request mode、finish reason、gate 与归一 reject detail；同一 `run_id` 内相同签名不得重复相同动作，只能升级或结束。其他结构化调用继续使用各自已登记的通用恢复链；失败候选一律视作不可信数据，动态恢复 prompt 仍按完整候选/诊断摘要/最小诊断逐份预检并记录 `request_mode` + `recovery_from_attempts`。来源账的 adopted model 只认 accepted attempt → ledger 精确联结；拿不到联结就拒绝落盘 | `HARD`（`novel/llm/client.py::ask_json` + `novel/llm/providers.py` + `novel/llm/session_audit.py` + `novel/llm/response_repair.py::failure_signature` + `novel/llm/paid_requests.py::PaidRequestSession`；旧链 `stage2_runs` 的 blueprint 定向恢复面随旧链退役，新链失败类型路由由 PaidRequestSession 的 turn/attempt 账承担）+ reasoning 单 wire/失败类型路由/交付退避/动作去重/模型来源账回归 |
| R-12.2a | 所有 Agent executor 的响应 schema 必须在公共构造口归一并通过 Claude/Codex 都可接收的严格子集，再由同一份 canonical 字节计算 identity、schema SHA 并交给具体 driver；禁止 driver 私自删改。根必须是 object 且不得为联合，每层 object 必须 `additionalProperties=false` 并把全部 `properties` 无重复列入 `required`；`discriminator` 只作分支注解予以删除，`oneOf` 等价归一为 `anyOf`，单值 `const` 等价归一为单元素 `enum`，`default` 注解删除且对应键仍必须在线上显式返回，其余子集外关键字在拉起 CLI 前关闭式失败。业务动态键映射不得发布开放 object：必须设计成带显式 key/value 的行数组，Pydantic 收到后确定性还原为既有内部 dict，重复键拒绝；正式 artifact 与新 checkpoint 的内部 payload 形状不变。旧 checkpoint 不迁移、不改字节；因 schema SHA 是恢复身份，协议变化后必须关闭式拒绝旧 restore 并由新 run 重建。任何 provider 的 `invalid_json_schema` 都归类为不可热重试的 `agent_schema`，不得洗成普通 `agent_command` 或连发四次 | `结构`（`novel/llm/json_schema_util.py`（schema 归一唯一口；旧 keyed-rows 载体 `agent_wire.py` 已随旧 stage7 wire 删除））+ 全部现役响应模型词表/object 闭合/动态映射 roundtrip/Claude 与 Codex 共闸/错误分类回归 |
| R-12.2b | 下游节点在开始逐条报症状之前，先判一次「上游这份东西是不是整个空的」 | `HARD` |
| R-12.2c | Claude agent 的结构化请求保留 provider schema gate（`--json-schema`），最终 stdin 必须且只能附加一次版本化 delivery instruction：只经内建 `StructuredOutput` 交最终对象，不先打印普通 JSON、不套 `response/result/structured_output` wrapper、不调用其他工具。有 output schema 时成功信封缺 `structured_output` 必须关闭式失败；只有 schema-less 请求可读取字符串 `result`。prompt SHA、request fingerprint 与 checkpoint/恢复守卫必须共用 `AgentRequest.stdin_bytes()` 的真实 UTF-8 字节，生产子进程必须以 binary stdin 原样收取，不得另拼一份或经文本换行转写；Claude 内部 `num_turns` 只作读数，不得被误当成「单次提交」闸门 | `结构`（`novel/llm/structured_stdin.py` + `novel/llm/providers.py` + session/checkpoint 共用渲染落点）+ footer 唯一/错误 wrapper/缺 structured_output/schema-less fallback/raw-stdin/hash 对账回归 |
| R-12.3 | 子进程环境三件套（UTF-8 相关）缺一不可：缺了它，任何打印 `✓` 的节点进管道就非零退出——而一次**成功的付费跑**会被报成失败 | `HARD`（回归测试） |
| R-12.4 | 普通缓存仍有三条语义：关缓存是**同时关读写**；参数不变的重跑可以零 wire；坏响应不得留在可命中的内容缓存。验证后 checkpoint 是第四种、明确独立的恢复状态：命中不记 cache hit，不产生新 wire，保留原 accepted attempt/model/成本联结并发出 `checkpoint_restored`；只允许同 `run_id`、同代码/契约/架构/输入/request/SHA 与闭合 ledger 恢复。Stage2 按章 checkpoint；Stage4A 先铸策略 checkpoint，再为每个已验章节铸独立 checkpoint，策略或任一章失败不得抹掉其他已验 unit，同 run 只补缺失/失败单元。Stage4 checkpoint 不是正式 artifact：N+1 单元齐全后仍须确定性聚合、整书 schema/HARD 与 commit journal 原子提交，之前不得出现部分 `blind_candidate.json`。主动 `--resample` / `--chapters` 首次执行不得命中旧 checkpoint；Stage4A 不开放公开 `--chapters`，恢复只由同一公开 node/run 自动发现 unit checkpoint。canonical `RunContext.run_id` 在同一 producer identity 内覆盖一次首轮或 repair/replan epoch 的请求 → 响应修复 → resume → 聚合提交，并进入 ledger、LLM audit、repair、checkpoint 与 adoption；AgentRun 跨代码 epoch，或在 Stage4 typed repair/replan 动作边界滚动 fresh epoch 时，均按 R-11.19/R-5.26 换新 producer identity，只由显式 `parent_run_id` 维持父子关系。`agent_progress.jsonl` 是外层 execution beacon，继续使用 parent run。job/queue/agent/chain/attempt id 保持各自含义只建父子关系。手敲 CLI 首次自动生成并打印，resume 必须显式 `--run-id` 复用；历史 JSONL 无 run_id 仍可读但只标 legacy/ungrouped，不回填、不按时间窗口猜。本字段不进正式 artifact provenance、缓存键或贵贱判断 | `HARD`（`novel/run_identity.py` + `novel/run.py::RunContext` + `novel/llm/paid_requests.py::PaidRequestSession` + `novel/llm/paid_requests.py`；旧链 `stage2_runs`/`stage4_runs` 的 checkpoint 面随旧链退役，新链候选生命周期用 committed_set_recovery／deterministic_replay 承担同族边界 + ledger/audit/progress/adoption 联结回归） |
| R-12.4b | `R-12.4` 的“只允许同 run”约束专指会恢复当前 logical/provider session 的普通 checkpoint hit。新链的显式**跨根历史采用**（`--adopt-root`，F7a 落地：同请求身份零新购复用，身份逐字相等、响应字节完整、源根 ledger 有唯一真实付费行）与旧链 `R-11.18` 的 Stage4B bridge 是同族边界，不是 session/checkpoint restore：source 始终只读，target 不复制 checkpoint、不续旧 provider，会以 immutable manifest、source-stable lineage、当前 validator 与分 run ledger 单独证明；表外节点、策略 unit、普通首轮和 replan 仍不得借此跨 run。旧链 prepared recovery（A 的 repair/replan aggregate 与 B 的章级提交） 是另一种跨 ended-producer **提交补尾**（随旧链退役；新链的提交补尾是候选生命周期的 committed_set_recovery／deterministic_replay）：它只重验并提交 source journal 已 sealed 的 aggregate，不采用新响应、不复制 checkpoint、不续 provider，因此不放宽 replan 禁止 historical response adoption 的边界 | `结构`（`novel/llm/run_journal.py::restore_historical_run` + `novel/llm/paid_requests.py::PaidRequestSession` 的 historical adoption 与 wire 采用；旧链 `stage4_runs` 的 repair aggregate recovery 随旧链退役）+ ordinary restore/cross-run adoption/commit-tail 正反例回归 |
| R-12.5 | **发出去的 prompt 里不许出现内部结构名**（拍号 `bNN_NN` / 人物 id `char_001` / 规则 id `wr_01` / 内部枚举名 / 节点名）。每层只说自己的语言：prompt 只讲任务，不提节点名、不提消费方、不提归属 | **无守卫 → `B-05`** |
| R-12.6 | **每一份最终逻辑请求先过对应的精确上下文预算，再碰其缓存、外部请求、ledger 或正常产物。** Stage4 各付费请求与每章正文请求分别是登记在架构中的最终请求，必须逐份预检；所有统一 `LlmClient` 调用同样按本次最终 system、user 与官方消息 framing 精确计 input tokens（新链 `str06.stage2.*`／`str06.stage4.*`／`str06.stage5.body` 的每次付费调用都过同一道预检）。原始 Flash 请求及同 prompt 的 Pro 兜底必须在该逻辑调用首个副作用前同批预检，任一项拒绝即该逻辑调用 0 副作用。R-12.2 的定向恢复 prompt 依赖尚未发生的响应，不能在首发前伪造“最终内容”：它在每次后继采样前按完整候选 → 诊断摘要 → 最小诊断逐份精确预检，只允许读取/发送第一份通过者；全拒时退回已经通过首轮预检的原请求，不能在已经付费后新增一个预算终态。`deepseek-v4-pro` / `deepseek-flash` 的保守有效上下文上限固定为 1,000,000，单次输出上限 384,000，且要求 `input_tokens + max_tokens <= 1,000,000`。tokenizer 固定为 DeepSeek V4 官方 revision `0e1a0e5e52aea73055f50fef6f2423db370265b6`，资源 SHA-256 `8f9f37ca37fdc4f5fd36d5cf4d3b0e8392edb4e894fd10cc0d70b4957c8633cf`；未知模型、依赖/资源不可用或 hash 不符全部关闭式拒绝，不用字符比例猜测。Stage4A 的策略 + N 单章是**语义契约预先登记的聚合拓扑**，不是预算失败后的动态分块；任何一份已登记请求仍不得截断或临时再拆，预算拒绝本身不触发模型兜底。错误必须给出模型、tokenizer 版本、输入、声明输出、合计、两项上限及缩减正文的行动建议 | `结构`（`novel/llm/budget.py` + `novel/llm/client.py::_preflight` + 新链付费请求预检）+ 首发零副作用/边界/新链各节点回归测试 |
| R-12.7 | 全链身份必须显式拆为四类：`review_subject_sha256` 覆盖人实际看到的内容、证据与 finding，`review_contract_version` 只表示影响审阅语义的显式契约，`validation_seal` 表示当前代码/规则下的免费机械复检，`execution_epoch` 表示本轮 code/profiles/admission/provider 上下文。人工决定的适用性只由前两类决定；代码变化先令 effective 投影暂停，新 validation seal 通过且 subject/contract 未变时 raw typed decision 自动复活。审批盘必须永久保存 raw 决定，前置闸失效只产生只读 effective 投影，禁止把级联 false 覆盖回 raw proof；任何人工修改从 raw record 出发。Prepare 使用独立主体与接受记录，不复用 gate.s6；Stage5 闭合是生成准入而非 Prepare 模型业务输入，MainText/HookCopy 的实际材料变化重开相应准备审阅，证据与审计变化不扩大为无关重买。Stage6/7 公开 gate 必须在既有前置链闭合后从 producer 的 current typed 定稿投影，不能以附加 API 检查单是否盖章作为第二次批准；GET 不补账、不重签，缺失、过期或校验失败不得投影 true。整书验收仍独立核对 producer 真相，并检查公开 gate 与它一致，不能用一面覆盖另一面的矛盾。所有现役人审写入口——Stage2 Blueprint/关系编辑、Stage4 design/structure/完整章 edit、Writing、Stage5 closure、Stage6 Prepare 与 Stage6/7——对外均只收一枚 opaque review token 加 actor/reason/typed ruling；execution permit 同样只暴露 opaque execution token。完整 subject/contract/validation/workpack SHA 仍在内部审计并由服务端重建，调用方不得建立第二套 public hash builder | `结构`（`novel/orchestrator/inspection.py::capture_operator_snapshot` + `_legacy_approval_bytes`（原始字节 seal）、`contracts/schema/orchestration.py::ExecutionEpoch`；2026-10-04 裁决后 `approvals.py` 解码面已删除，旧根一律 legacy_incompatible 拒识）；前置失效/复活、可见 finding 精确过期、代码漂移不重签和全 token 表面的 TOCTOU 回归查 |

合法 JSON 不等于合法响应：只返回策略却没有章表的空壳不能进入缓存并在下游产生一串派生症状。
响应与缓存反例见 `tests/test_llm_response_repair.py`。供应商诊断从仓根用
`conda run -n novel_refactor python -m tools.observe_provider_limits` 进入，操作参数见 `docs/OPERATIONS.md`。

### R-13 · 批量队列

排入多本书，自动推进到授权范围；需要裁决时明确停止并指出在等谁。

| # | 要求 | 谁在查 |
|---|---|---|
| R-13.1 | 队列以「书 × 目标阶段」为单位，可排入、可取消**尚未开始**的项 | **`结构`**（`novel/orchestrator/queue.py::create` 冻结 `QueueStart` 并追加 `queues.jsonl`；`novel/orchestrator/queue.py::cancel` 只接收 `pending/blocked`，运行中明确拒绝）+ API/CLI 行为回归 |
| R-13.2 | **一本书同时只跑一个作业**这条不变，Stage5 也没有书内互斥例外；队列只决定顺序，不放宽互斥 | **`结构`**（互斥键唯一落点 `novel/orchestrator/jobs.py::active_key`；队列不碰它） |
| R-13.3 | **花钱的项必须一次性预先确认**，不能因为「排进队列了」就变成静默扣费。确认时摊开的仍是**将要执行的那些命令**。⚠ **2026-08-29 用户裁定修订（TODO-45 批次 3）**：durable queue/job 父记录下**身份完全闭合的同一 run**，其原付费授权可跨重启自动续跑——前提同时满足：durable start 中 `paid_confirmed=true` 且正文外发授权仍有效；run/代码/argv/输入/candidate/审批/profile/模型/effort 完全未漂移；仍在原 max-turn/token cap 内；provider session/checkpoint 身份可精确对证（checkpoint 恢复的既有关闭式校验）；不切 provider、不 fallback、不新造命令。自动续跑 = 新 `job_id`（execution）+ `recovered_from_job_id` + 原 `run_id`。任一字段缺失、legacy（原 started 无 run 身份）、漂移或可能串错 session 时停止并落持久 restart-confirmation 提醒，不自动猜；**从未启动过的付费项与手工 CLI 不自动复活**，仍需 CLI `queue-resume --yes` / API `{"confirmed":true}`。恢复会重新逐字展示创建时冻结的剩余付费命令；调用底层 runner 本身不构成授权 | **`结构`**（`novel/orchestrator/queue.py::confirm` / `_confirm_resume` / `::_auto_resume_block_reason` / `resume_incomplete`；`novel/orchestrator/jobs.py::plan_start` 的 run_id/recovered_from_job_id 与 `::_run_steps` 的 env 注入）+ **`人`**（创建与每次不可自动续跑的付费恢复各完成当轮确认，见 `B-19`） |
| R-13.4 | 任一项未绿 → **同书停在那里**，不跳过、不继续往下扣费；另一书继续。exit 1 单列为 `landed_red`：正式产物已经落盘，但本次 typed 结论不通过（包括报告阻断红行与 Stage4B `infeasible`），不能误报成命令没跑成或没有落盘的 `failed`。它与 `failed` 一样会停住该书 lane，并把后续项记 `not_run` 等人处理；四类计数不得合并 | **`结构`**（`novel/exit_codes.py::landed` + `novel/orchestrator/queue.py::_run_lane`；`QueueLaneCounts` 分列 done / landed_red / failed / not_run）+ 两书一红一成与一败一成回归 |
| R-13.5 | 需要人裁决的闸门会挡住队列并明确说出在等谁。**F8a 起新链没有任何人工闸**：13 个 `str06.` 节点（B2 五节点重建 D9；CS-3 bind 退役）全自动推进，机械验拦下的稿进 `.INVALID` 孪生留证后 fail-fast 停在 owner，恢复入口是 `--check-only`（缓存／登记的历史采用）与候选生命周期登记的 saved_response／historical_adoption／owner_patch／committed_set_recovery／same_context_rewrite／deterministic_replay——没有审阅 blocker，也没有 `gate.*` 待办。旧链的 stage2 机械准入、stage6.prepare 自动接受、三候选定稿与人名表定稿面随旧链退役；存量书的审批账只读解码，新书不产生 typed 决定。普通 durable 多步队列遇到 `redo/unknown` 仍必须形成无命令的 `resume:manual-decision`，不能把冻结快照上的「要重来/判不了」伪装成 runnable；AgentRun 单步 operator 的 exact REDO 例外照旧（节点在 `NodeSpec.operator_redo` 登记唯一策略、汇总 reason 全是 declared projection 的 `consumed_changed`、无 unreadable／unknown／INVALID），每落一步重新规划并铸同快照 execution token。 | **`结构`**（`novel/orchestrator/queue.py::plan_book_from_overview` + `novel/orchestrator/state.py::operator_redo_flags` + `novel/orchestrator/operator.py::_next_from_snapshot` + 新链无审批闸回归） |
| R-13.6 | 队列本身**不产生新的判定**，也不放宽任何落盘闸门 | **`结构`**（`tests/test_layering.py::test_the_queue_cannot_reach_the_judgement_layer`：`queue.py` 够不到 `novel.report`，连「顺手判一下」都做不到） |
| R-13.7 | 不同小说的作业可以并行执行；互斥范围只落在各自 `sid`。每个后台作业独立启动 CLI 子进程，一本书失败或阻塞不占用另一本书的键 | `结构`（`novel/orchestrator/queue.py::_execute` 每书一个线程，调用 `novel/orchestrator/jobs.py::start`；互斥仍唯一落在 `novel/orchestrator/jobs.py::active_key`）+ barrier 证明两本书同时进入子进程的回归 |
| R-13.8 | 正常主路的权威时间线由确定性 RunSupervisor、agent lifecycle events、admission claim、node beacon 与付费 ledger **自动形成**；Executor 不得在 prompt 或正常主路中手工调用 telemetry，历史 Commander 账只读。`_service/operator_phases/<session_id>.jsonl` 只保留 legacy/诊断兼容：它与上述权威账正交，不进入 `NODES`、不冒充模型调用、不成为交付闸；缺失、未闭合或无法联结时完整 phase 墙钟分解必须是 unknown，不能据此阻断运行或洗成 0。若显式诊断使用该账，phase 仍闭合为 preflight/inspect/preview/execute/review/approve/finalize/verify，区间并集不重复计时，余量为 unattributed；prompt、正文、reason、argv、路径、密钥、模型 token/价格不得落账，宿主 usage 越界时声明 `unavailable_host_boundary`，不拿墙钟估成本 | `结构`（`novel/orchestrator/agent_supervisor.py` + agent event/admission/beacon/ledger fold + `novel/orchestrator/operator_telemetry.py::summarize` 的 legacy 诊断）+ 无手工埋点主路、缺账 unknown、隐私与 layering 回归 |

持久队列恢复先核 jobs 与正式产物；已完成且 fresh 不重跑。需要继续时复算计划并与冻结命令逐项比较，
相同且免费才可自动续，相同但付费需显式 queue-resume，变化则重新预览。
不得因恢复扩张章节或模型范围；执行前的机械授权（计划/context 章集/fresh Review）仍在同书互斥内复验。
持久队列与进程内 Agent 待跑列表的边界见 `docs/schema/_orchestration.md`，反例见 `tests/test_orchestrator_queue.py`。

### R-14 · 断点续跑与中断恢复

按当前身份恢复已完成成果；候选、决定或正式提交已合法落盘时不重复购买，缺证据不能承诺免费重建。

| # | 要求 | 谁在查 |
|---|---|---|
| R-14.1 | 已完成的阶段按**产物存在性 + 来源新鲜度**判定，不靠一份单独的进度文件——进度文件会与盘上的实际产物分家 | **`结构`**（`novel/orchestrator/state.py::overview`；配一条 AST 守卫 `test_completeness_never_peeks_at_the_job_records`：那份文件的**代码里**够不到 `_service`） |
| R-14.2 | 逐章类阶段（写作）的续跑粒度是**章**：已落盘且通过当前 schema/version/freshness 的章不重跑；文件名发现本身不得冒充完成 | **`结构`**（`novel/orchestrator/state.py::chapters_done` 只发现 canonical 并排除 `.INVALID.json`；`artifact_state` + `stage_state` 逐章关闭式校验后才允许 `done/skip`） |
| R-14.3 | **被打断的跑可以几乎免费续**——已完成的调用命中缓存。这要求缓存键稳定，且**参数不变的「重跑」什么都没跑**这条语义被显式说出来 | **`结构`**（`novel/llm/cache.py::cache_key` 稳定键 + `novel/llm/ledger.py` 的 `calls_made` / `calls_cached` **两个计数器**） |
| R-14.4 | 续跑前必须**重新判定新鲜度**：上游在中断期间变过，就不是「续」而是「重来」，要继续保留 `redo` 原结论；判不了就是 `unknown`，也不能折成“待继续”。普通队列对 `redo/unknown` 均不得构造或自动执行命令。AgentRun 因为每步后重新采完整快照，可在注册 producer 的每份 stale 输出都汇总为 `consumed_changed` 时导出唯一 canonical flags：书级 producer 用默认完整重建，Stage5 写作必须导出 stale/missing 的精确 `--chapters`；Stage5 机械 Review 则只在专用 currentness 已明确 stale 时零成本新增一轮。同一 producer 的缺失输出可由 canonical 动作补齐；producer 有输出汇总为 `file_changed`、存在 unreadable、盘上既有却 `unknown` 的输出或 INVALID、策略未登记或 flags 不唯一，仍关闭式停在 `resume:manual-decision` | **`结构`**（`novel/orchestrator/state.py::resume_verdict` 五态保持原值 + `::operator_redo_flags` 的封闭策略；`novel/orchestrator/queue.py::plan_book_from_overview` 默认关闭、仅 operator opt-in；Stage2 与 Stage5 精确重放回归） |
| R-14.5 | 中断留下的半成品（写到一半的章 / 空壳响应）**入显式终态并踢出缓存**，不许被当成「已完成」续过去 | `结构`（`novel/llm/client.py` 空壳响应 evict；`ArtifactStore.write` 临时文件 + 原子替换，所以被杀时不会留下半份 `chNN.json`；stage5 续跑只认通过契约与落盘闸门的正式章，fixture 覆盖“已有章跳过、缺章补跑”） |
| R-14.6 | 逐项登记进程中断后的不完整盘面、免费补尾条件和不能自动恢复的边界；单文件原子替换不能代替跨文件事务或证明执行 owner 已停止。 | **`结构`**（`docs/schema/_orchestration.md` §八逐项记录中断盘面与恢复条件） |
| R-14.7 | state/inbox 使用调用栈内一次性冻结快照，同一代码、overview 与七层 check；不做跨写或全局缓存。active surface seal 只覆盖当前注册产物和审批 head，外部输入由 exact permit 单独绑定；历史、scratch、非注册文档及别书追加不制造假漂移。交付终验在独立读作用域检查当前成书、审批、Stage2 统一机械准入与 blocking 待办，准入的语义未审披露随交付常驻说明；并在前后复验 surface 与代码，漂移拒绝；不生成、读取或要求终审 MD。费用/session 统计是独立只读投影，不作为质量放行条件 | `结构`（`novel/orchestrator/inspection.py::capture_operator_snapshot` / `::read_surface_seal` + `novel/orchestrator/inbox_projection.py::inbox_from_snapshot` + `novel/orchestrator/delivery.py::assess`）+ active-surface、历史噪声、call-counter、漂移与统一下载回归 |

保留的跨层边界：

- 只有选中的唯一 prepared journal 进入恢复 permit 的 external-input CAS，claim 后再验原字节，
  不扩大为扫描所有历史 run；非关联追加不能制造假漂移。
- run、scratch、trash 与 retention 的路径、字段及逐项恢复见 `docs/schema/_orchestration.md`。
  清理默认关闭，按 preview/apply 的同一计划逐项 CAS；prepared/永久 receipt 先于移动或删除，
  不以“目标不见了”猜成功。账本、admission、beacon、事件与恢复证明保留。
- trash restore 与整根发布不同：前者精确恢复 book/approval/service run 的唯一权威副本；后者停止 admission、
  排空并证明全部 owner 停止，离线按整根 manifest 归档，逐项复验后启用独立空根。
  双份、缺失或漂移保持原盘并拒绝；回滚配对旧代码和旧根，不删除围栏或重签旧产物。
- 原子替换单个文件不等于跨文件事务。中断矩阵、内存队列限制与补尾条件见 `docs/schema/_orchestration.md` §八；
  具体操作以 `docs/OPERATIONS.md` 为准，生命周期反例见 `tests/test_service_lifecycle.py`。

## 三、工程约束 Q-1 ~ Q-13

跨阶段成立。它们不是「最佳实践」，每一条都对应一次已发生的损失。

### Q-1 · 不静默

截断、缺章、解码失败和异常进入显式终态；可疑但不构成错误的内容独立告警，不悄悄接受或改写。
落盘前改变已有内容须保存改写账，并在报告中常驻显示计数、非空展开；初始化不是改写，不造空账。
结构化响应拒绝额外字段，避免拼错字段名后值被静默丢弃。反例与 schema 守卫见
`tests/test_rules_registry.py`、`tests/test_artifact_versions.py`，具体来源字段见 `docs/schema/_common.md`。

### Q-2 · 「不知道」与「没问题」必须可区分

unknown、缺失、未运行、合法空值、失败和通过分别保留，不能互相折叠；有未验证项时不得展示无条件全绿。
缺指纹不是匹配，源文件不见不能跳过那条依赖。关键反例是旧成稿已无法与上游配对却仍显示通过；
当前来源与部分可读投影见 `tests/test_prov.py`、`tests/test_api_discipline.py`。

### Q-3 · 每一层确定性的部分都必须能 0 成本重算

确定性部分必须有免费重算/恢复入口，唯一登记在 `contracts/invariants.py::FREE_ENTRIES`；同语义使用同 flag。
漏登记的免费动作会被当作付费，必须校验实际分支而不是相信名称。新硬闸同批提供零成本恢复。
重算按登记策略阻断新引入错误，旧违规仍报告，不让不相关旧问题永久堵住修复；反例见 §9.3 与恢复测试。

### Q-4 · 每份产物必须记住自己是从哪份上游来的

产物必须能追溯实际输入、prompt、模型与采用来源、代码及参数，先报来源再报判定，读上游同时记账。
请求、失效、历史采用与恢复共用实际依赖投影；原始文件完整性与语义依赖身份分开，不能丢证据或把不消费字段
的变化扩大为整书失效。来源缺失保持 unknown，反例见 `tests/test_prov.py`、`tests/test_str06_f7a_wiring.py`
（`test_workflow_refuses_source_identity_change`：新链源输入字节身份变化的拒例）。
只修改原钩子的已批准 Stage0 revision，必须沿同书祖先链及 Stage1 原切分证明正文和 MainText
原始字节不变，才能保留 Stage2–5 原来源及仍满足完整身份的审批；稳定盲句柄和审阅依据不得
因此换 seed。未知、正文或切分变动、未批准、坏链、Hook 依赖仍走原根守卫。Prepare 则由
完整 typed 重编绑定当前两材料及历史生成证明，重新接受；不把旧模型来源重挂成新模型调用。
证明与拒绝反例见 `novel/source_identity.py` 与 Prepare HTTP 回归；新链源输入身份由 `tests/test_str06_f7a_wiring.py` 承接。
字段形状见 `docs/schema/_common.md` 及各 producer schema。

### Q-5 · 先校验后落盘

阻断候选不覆盖有效正式产物，问题稿保留来源并进入 `.INVALID`。历史免费用户写面
`--revalidate --actor --reason` 与 typed `--apply-edit` 人工轮次已随 2026-09-15 全自动裁决退役
（内部 0-wire revalidate 仅由队列以固定审计身份自动排入，无用户入口）；新链机械验拦下的稿
在 `.INVALID` 孪生留证后 fail-fast，恢复走 owner 修订与节点级 `--check-only` 免费复检，
保留原生成来源，不把人工字节伪装成新模型结果。反例是判据修正后被迫重购另一份稿，或提升
丢失原来源；恢复载体见 `docs/schema/_common.md`。

### Q-6 · 单一落点

枚举、词表、区间、判定和修复路由各有唯一落点，prompt 必须引用或插值，不手抄子集。
模板缺值必须失败，不渲染成空串。反例是模型材料使用一份词表、校验器使用另一份，两端都不报错；
注册与分层守卫见 `tests/test_rules_registry.py`、`tests/test_layering.py`。

### Q-7 · 中间层只存决策与引用，内容由确定性代码搬运

中间层只存决定与引用，正文和上游事实由代码按引用搬运；让模型转抄会增加事实漂移与遗漏。
保留反例：把人物关系改成另一种身份、把多条具体事件压成合并散文。实际送达和覆盖反例见
`tests/test_hard_counterexamples.py`、`tests/test_str06_body.py`（WriterBookIR 规范投影与称谓
字面按释放计划送达）、`tests/test_str06_target.py`（source-blind 投影）。

### Q-8 · 当前模型与读取边界都只保持单形

契约、prompt、CLI、判据与报告只消费当前形状，不提供隐式字段别名、双形 union 或旧命令转发。
版本及路径唯一见 `contracts/schema/__init__.py::ARTIFACT_SPECS`，精确载体见 `docs/schema/_README.md`。
本仓没有通用迁移机制：缺版本、旧版、未来版与非法版本明确拒读，并指向重建 producer；破坏形状时升版，
同批明确重建与授权。坏一份只降级其可读区域，`.INVALID` 和轮次证据同样校验版本。
反例见 `tests/test_artifact_versions.py`，旧冻结证据不获现役写入权。

### Q-9 · 判据要报出自己的基线，且探针的名字要等标定完再起

词表判据报告基线与扫描分母；未经标定的名字或阈值不当作语义真值。
告警与度量分别输出，出错时也保留读数。反例是“揭示词”几乎章章命中，或“幻觉探针”实际抓到另一类问题；
相同判据在不同书上的判别力需分别检验，不能外推。

### Q-10 · 钱

付费和正文外发分别取得授权，继续在已授权范围内执行；可离线验证的先离线验证。
费用分类共用 node/flags 的唯一判定，免费用白名单，未知不声称免费，也不编造预计费用。
终审成本只认 `<base>/_service/ledger.jsonl`；实付与缓存命中分列，局部 provenance、日志与报告不能重复加总，
reasoning 是 completion 的子口径，不再累加。统一解析顶层与 completion_tokens_details.reasoning_tokens，保留合法零，缺失／null／冲突／非法值保持未知并诊断；reasoning 大于 completion 也拒绝该子口径，不触发模型重购。当前购买、缺账恢复与 client 共用 `novel/llm/usage.py`；历史只读补证由 `novel/llm/usage_audit.py` 绑定原请求、attempt 与响应摘要，分别报告账内和补证值，不覆盖原账。失败和重试计入成本，缓存采用单列。

**额度与重试层级**（2026-09-15 全自动裁决补全）：付费内容调用有两层持久额度——每工作单元
`WORK_UNIT_EXECUTOR_MAX_CONTENT_CALLS`（含首次生成），以及**整书级总闸**
`BOOK_CONTENT_CALL_CEILING_DEFAULT`（跨全部工作单元合账，持久于
`_service/runs/<sid>/book_budget.json`，仅 `WorkUnitBudget.reserve` 计数）。重启、恢复、
重建 job 不重置任一层；追加只能显式人工（单元 `work_unit_grants`、整书
`novel/work_units.py::grant_book_budget`），自动恢复路径不得追加。重试不叠加放大：
网络层重试共享同一 reservation 不耗新额度，语义轮走内容额度，queue/supervisor
从不扩张已确认的付费命令面；反例见 `tests/test_book_budget.py`、`tests/test_work_units.py`。

### Q-11 · 缓存

关闭缓存同时关闭读写；缓存命中与真实调用分别记账；已合法保存的回答优先免费恢复。
响应必须通过当前结构验证，空壳不能作为成功候选缓存；反例是重复读同一个坏响应却被当成重试调用。
响应修复与缓存边界见 `tests/test_llm_response_repair.py`、`tests/test_llm.py`。

### Q-12 · 守卫本身要有守卫

守卫随其检查的契约同步更新，实际经过红例与绿对照，并校验扫描分母。改字段追完整下游消费与恢复，
不能只改校验器。保留两类反例：旧守卫恒通过或全量误报；改名后消费链静默生成空材料。
`tests/test_coverage.py`、`tests/test_hard_counterexamples.py` 与 `tests/test_ingest_fixtures.py` 分别守
双向关联、真实判据分支和摄入边界；测试通过不等于未被覆盖的语义也正确。

### Q-13 · 披露边界（未知分层）

**仅内容质量层面的不可验证项允许进入交付披露**（未执行语义评估、可降级目标偏差见 §七分级）。
产物完整性、契约有效性、输入新鲜度、机械检查执行状态与付费副作用状态的 unknown **不得通过披露
绕过**：进入对应恢复路径或终态失败。机械检查器异常退出（exit 3/70/未登记码）保持 fail-closed，
不得折成「通过但有披露」。

唯一落点：判据注册表 `Rule.disclosable_unver`（注册期校验只收 `sev=unver`；集合双向钉扎在
`tests/test_rules_registry.py::test_disclosure_and_degradable_sets_are_pinned`；旧机械准入
名册的派生表已随名册删除），表外任何 `unver`/`skipped` 一律按不可判阻断。
`ready_with_disclosures` 只装质量类披露。

## 四、无守卫清单 B-01 ~ B-23

> **这是新骨架关卡层的施工清单。**
> 下表每一条都问过「哪一段代码在查它」，答案都是「没有」。
> 处置只有三种：**补一条判据** / **把这个自由度整个收走** / **明知不查但要写明**。
>
> ⚠ 补 prompt 之前先确认那条要求**本身是对的**——已有一例是要求错了（`B-12` 那条打架的）。
> ⚠ 收走自由度之后**别把它记成质量改进**：去掉的是一个无守卫、高方差的自由变量。
> **没有守卫 ≠ 已经出事。**

### 4.1 最要紧的五条

| # | 约束 | 现状 | 建议处置 |
|---|---|---|---|
| **B-01** ★ | **发明越界七项里的五项**：新事件 · 新地点 · 新道具 · 新因果 · 新时间跳跃 | **零校验。** 只有「新数字」有探针、「新人物」有部分探针（召回有缺口：用本书没出现过的姓造人、不带称谓后缀的光名字都抓不到）。地名后缀是普通名词、无硬左锚，实测 82 条候选几乎全假 | **已裁决（2026-08-10）：不补，直到有 ground truth。** 三条理由都有实测：① gate1 在抽象层做语义判定时**护栏自己成了发明源**，已整节退役；② **唯一 `full` 的那一项自己就零判别力**——`invented_number` 在 3 本里有 2 本被书级恒等闸门判死（头牌 2/8 vs 源章 2/8；三岁 1/9 vs 源章 **2/9**）；③ 修复回环**实测会诱发**自造内容而当轮判定一条都指不出它。<br>⇒ 七项的身份是**给人核对的料**（同 `name_surface_survey`），不是关卡。**「探针报 0 条 ≠ 约束生效」必须写在报告里** |
| **B-02** | **旧事件拍的落笔顺序** | **现役暴露面已关闭。** 旧 event/beat 写作骨架已经退役；Stage4A 决定新场景披露顺序，Stage5 响应按计划 slot 确定性归位（旧链 stage5/write.py 的 `_ordered_segments` 归位面随旧链退役；新链由 `novel/stages/stage5/body.py` 按章计划成文），不能临场另排一套。至于 A 选出的顺序是否更好没有 ground truth，留在 `B-17` 的结构读数 + 人工裁决面 | 不恢复“源拍位置单调”判据；那会把已去序的规划重新锁回源顺序。执行顺序由 Design 的呈现序权威关闭（`R-5.1`），审美优劣按 `R-5.7` 的 unver 披露交人 |
| **B-03** | **旧开卷路径的重合度没有阈值** | **历史闭环记录，现役暴露面为空。** 旧盘面逐章重算：旧闭卷 0.0~2.4%，旧开卷 0.9%~96.8%，不存在可标定的开卷一刀切线 | **2026-08-24 以收走能力关闭，不是以发明阈值关闭。** 现役只有 canonical 闭卷 `stage5/chNN.json`，上界为 `SOURCE_OVERLAP_MAX = 10%`（`INFO`）；旧目录只读保留并报 `legacy_incompatible`，CLI 没有可恢复旧能力的入口 |
| **B-04** | **「必须成立、不必写在纸面上」那一组** | 机械层没有，且对应判据明确标为「查不了」；历史 Stage5 书级语义节点已退役，不再是现役覆盖 | 机械层继续诚实标注 `unver`；没有 ground truth 时不为了消掉盲区恢复一个自动语义门 |
| **B-05** | **Stage4A prompt 不许泄漏源序或正式内部 id** | **本缺口已按 A 相边界关闭。** `build_blind_projection` 是唯一构造口，正式外发面只含去序 opaque alias、事实语义、因果与状态；生产集成测试直接检查真发 prompt 不含 fact/source beat/stage/material/人物正式 id、原文或源区间。B 相在结构冻结后才有意接收 candidate scene id 与关闭式证据目录，不能把它反推成 A 的输入 | 维持 `R-5.5` 的 prompt 侧隔离守卫；不要把“B 为绑定证据而看正式引用”误报成泄漏，也不要把其它节点的 typed id 需要偷换成 Stage4A 的源序暴露 |

### 4.2 其余（按节点分组，逐条已核实「谁在查 = 没有」）

| # | 节点 | 无守卫的约束 |
|---|---|---|
| **B-06** | 计划 | A/B 的 strategy、goal intent、brief、purpose/reason 与 infeasible reason 等自由文本仍可能发明人物、事件或设定，也可能提前泄底。typed fact/stage/material/participant 引用能守引用闭合，却不能证明这些自由文本的语义忠实 |
| **B-07** | 逐章抽取 | 不为首尾相接拉伸区间 · 主谓宾必须明确不许省略施受关系 · 别把「靠什么成立」摘要掉 · 硬数字必须原样进事件文本 · 原文自相矛盾时如实记录矛盾 · 不许照抄例子里的名字 · 摘要不照抄原文措辞 |
| **B-08** | 全书抽取 | 结构性规则是否完整解释运作机制、秘密是否符合收录标准、冲突描述是否可定位，以及人物区分度、规则类型、称谓场合和叙述人称的语义选择，仍需人工核验。schema 的闭合取值域不证明内容正确；限知／全知与语法人称是不同维度，当前没有独立限知／全知载体。弧线字段已无现役 producer 或 consumer，不再列为待补守卫。 |
| **B-09** | 叙述视点与退役文风 | Stage3 v1 的“可迁移、逐章可见、可执行规则、素材分区”等定性质量面已随字段和消费方整体退役，**不再是待补守卫**。仍存在的缺口只有限知/全知与语法人称可能混淆（随 Stage2 语义未审披露，见 `R-3.3`）；它没有现役独立载体，不得以此为由静默复活 Stage3 定性 schema |
| **B-10** | canonical 写作 | 不把潜台词说破·换叙述人时声音也要换，两者没有确定性真值。历史开卷路径的表层修饰/整段照抄要求已随能力退役，不是现役条款。见 `R-7.6` | 人称与声音没有确定性真值，剩余语义盲区继续明示，不冒充已守住 |
| **B-11** | 修剪 | **自动收缩臂仍关闭（2026-08-18 整臂下线；处置已随批次 B3 机械自动化）**：现役没有由 `length_over` 自行生成、比较或采用短候选的路径——机械改写是显式 typed request 驱动的完整章重写，不是候选采用。超上限章没有 identity 探针（历史 `TrimAudit` 载体已随旧链 evaluation schema 删除，全仓无产出方）；新链由章级机械验的篇幅带承接（预算内同输入有限重写，耗尽后只有篇幅带可披露，见 `R-7.3`），不再有 trim 人工三选一 |
| **B-12** | 文案 / 改名 | 不说破任何答案（字面探针**自陈判别力为零，别当门用**）· 只引用正文真实出现过的句子〔标「查不了」〕· 不歪曲人物关系〔标「查不了」〕· 新名贴合时代与性别气质 · 同辈共用字、兄弟姐妹只差一字等**跨名字**结构关系。<br>⚠ 单个旧称呼内部的字面一致性已由名称结构与单遍替换确定性守住（`R-10.2`）；本 B 只保留无法靠字面不变量判定的语感与跨名关系。 |
| **B-13** | 旧开卷写作 | **历史缺口已随能力关闭。** 旧 user 消息曾发「现场/回忆/转述」标签，system prompt 没有解释；实验补强没有稳定净收益 | 现役只有一份 canonical 闭卷 system prompt，旧路径为 `legacy_incompatible`，差异面结构性消失 |

### 4.3 已量出来、但没有下文的

| # | 条目 | 状态 |
|---|---|---|
| **B-14** | 身份描述里的结局 / 未揭示秘密 | **没有确定性防线。** 词表命中 0/17 而人读真违规 6/17（另一本 2/40）。处置不能是加词、也不能按章收窄（会掏空写作节点对角色的认知）。结局字段本身是对的，病在身份描述把它又讲了一遍，而两者的最长公共子串只对 1/3 强，**做不成可靠探测器** |
| **B-15** | 改写式泄底 | **历史闭环记录。** 曾以 secret-blind claims → secret alignment 两相付费审判接线；2026-08-17 根据实跑证据转授到 Stage2 后见之明与交付对账，旧 Stage5 节点、产物和判据全部下线 | 现役处置见 `R-3.3`（源语义未审）／`R-7.6`（正文语义未评）；不得把历史实验描述成当前 Stage5 能力 |
| **B-16** | 设定块的剧透闸门 | 字面子串包含，**命中 0/8，包括一条已知真泄漏**。真根因在产地：设定块里装着情节，修法是收紧抽取判据——那要重跑（付费） |
| **B-17** | 计划的场景轻重与顺序 | 场景数量、章/场 `weight`、预算分布与披露顺序哪种更好没有 ground truth。现役 `scene_budget_distribution`、逐字占比与结构度量只常驻报读数，不自动升成审美阈值；收口依赖 hash-bound typed 人工结构裁决 |
| **B-18** | 定向修复被驳回之后 | **没有下一条路。** 回归护栏会正确地驳回一次「顺手少了一条 hinge」的重生成并保留旧稿，但这一层**没有**写作层那套「换一次采样再试一次」⇒ 一次付费调用白花、那一章仍是红的。**同一个问题形状在写作层有解、在抽取层没有** |

### 4.4 按设计就没有判据的（`人` 是正确答案，但必须写明）

| # | 条目 | 说明 |
|---|---|---|
| **B-19** | Stage5 全书写作授权 | **【已退役 2026-09-15 全自动裁决（批次 B3）】** 是否付费不再逐书逐层由人授权：上传即授权本书全部付费节点（run 启动授权），整批付费确认由 R-13.3 的 `--yes`/`confirmed` 面一次性摊开。存量 `writing/writing_proof` 账只读保留 |
| **B-20** | 非正文剔除的正确性 | **确定性验不了**——「这一段到底是不是番外」没有结构不变量可查，而正文体已按契约丢弃、盘上不留副本。判据已按 161 本语料标定（27 本命中 / 0 假阳），但那是「在这批语料上没抓错」，不是「对任何输入都不会抓错」。每本必须打印一条「剔除的正确性：本层验不了」 |
| **B-21** | 改名的替换覆盖率 | **验不了。** 只能替换人物表里有的称呼，人物表漏掉的角色这一层**结构上够不着**——实测漏过一个（靠残留探针捡回来）。别把「残留 0 条」读成「人名都换干净了」。⚠ 2026-09-26 收窄：**Design 放行面已确定性闭合**（`R-5.1`：放行字面必须落在 Characters 登记面内，源侧 canonical 也必须是已登记 surface，`R-2.8`）⇒ 「获准输出的称谓没换干净」这一子类已有机械闸；本 B 只保留人物表本身漏收角色那一段 |
| **B-22** | 新名的质量 | 时代感与雅俗没有确定性判据。**现役闸门就是「人定稿」那一步**，这一点不会变。同理开篇文案的定稿 |
| **B-23** | 提不出表面探针的事实 | 新链诚实地把这类事实语义留在未评披露（`R-7.6`），确定性层没有第二把语义尺子证明它是否落地。这不是“查过没事”。同场的 verbatim / keep_value / must_appear 只能守各自材料维度，不能替代事实语义；代词化又已实测会让专名探针假阳。⇒ 先用真书盲读标定，不凭手感开自动阈值 |

---

## 五、反向覆盖率

每条注册 Rule 必须指向现存 R；每条声明 HARD / INFO / 闸门 / 度量的 R 必须有对应 Rule。
具体规则清单与修复归属只读 `contracts/rules.py::REGISTRY`，不在此手抄逐条映射。
下表仅保留现有文档计数守卫所需的逐层声明，不能用条数相等替代逐条覆盖。

| 守卫所在 | 需求范围 |
|---|---|
| 非正文剔除（`stage0`，**13 条现役**） | R-0 |
| 分章（`stage1`，**9 条现役**） | R-1 |
| 源语义与名称结构（`stage2`，**20 条现役**） | R-2、R-3 |
| 目标故事与确定性编译（`stage4`，**29 条现役**） | R-5、R-6（含 Guard M1–M8） |
| 按章正文与 Hook（`stage5`，**8 条现役**） | R-7、R-8 |
| 命名与交付（`stage7`，**8 条现役**） | R-10 |

`tests/test_coverage.py` 校验双向关联、逐节精确条目数、逐层计数及无遗漏；structural/carrier 的
实现归属仍由对应 R 行点名，不能因没有注册 Rule 就称它没有任何校验。人审和真正盲区分别明示，B 不冒充测试。
Stage6 整层退役后不再出现在本表（该层没有任何注册判据）；R-9 节保留退役条款与历史编号。

## 六、产品与界面规格

产品边界、人工验收任务、路由与 UI 规则唯一见 `web/AGENTS.md`，操作入口见 `docs/OPERATIONS.md`。
本节保留稳定引用位置，不另存一份界面手册。

## 七、修复机制与成本上界

校验结论按 `remediation` 分四类；路由是策略，不在 CLI、界面或 agent 中临时猜：

| remediation | 谁修 | 准入与路由 | 自动成本上界 |
|---|---|---|---|
| deterministic | 代码 | 运行 Rule.fix 指向的同层免费节点与 flag，注册期验证路由真实存在 | 一次免费命令 |
| llm | stage5 写手 | 只接受 `repair == UNIQUE` 且 `unresolved_rounds < 2`；directive 从 Rule 唯一派生 | 每章每判据最多两轮付费 |
| human | 人 | 真人工路由为 human_only / upstream / approval；`verification` 批次 C 起转披露（不挡交付）。通过 typed apply-edit、pick 或 wontfix 收口（Stage4 的 hash-bound design resolution、Stage5 的 trim 三选一与闭合回执、analyze-draft/response-repair/INVALID revalidate 用户写面已随 2026-09-15 全自动裁决退役；内部 0-wire revalidate 由队列以 `system:invalid-revalidate` 自动排入） | 无自动费用 |
| none | 无 | metric 只报告读数 | 无 |

处理顺序：首轮生成 → 机械检查 → deterministic 先修 → UNIQUE 才可付费修 → 连续两轮未消转人 →
全规则回归。unver 与 skipped 不属于四种修复，
也不得洗成通过。

**交付硬约束 vs 可降级目标偏差**（2026-09-15 全自动裁决；分级落在 `Rule.degradable`，注册期校验
`degradable=True` 要求 `sev=hard` 且有修复路，集合钉扎同 Q-13）：

- 未标记的 HARD 是**交付硬约束**（缺章、schema 无效、指纹不一致、替换正确性、覆盖游走等）：
  不满足即阻断交付，预算内修复，耗尽即终态失败。
- 标记 `degradable=True` 的是**从一开始就定义为可降级的目标偏差**（现役仅 `paragraph_budget_missed`）：
  生成期照常按 HARD 驱动预算内修复；预算耗尽后按预定义规则降为交付披露，不是四次失败之后把
  HARD 临时改 WARN。降级事实随交付披露常驻（Q-13），不静默、不作零。

唯一落点：路由算法 `novel/report/builder.py::remediation_of`；付费筛选随旧链 stage5/write.py 的
revise_targets 退役——新链付费修复路由由候选 runner 的 realization failure 重写上限承担
（`novel/stages/stage5/body_checks.py::MAX_BODY_REWRITES`）；轮次上限
`contracts/invariants.py::STOP_REWRITE_AFTER`；八类清单 `novel/orchestrator/inbox_projection.py::inbox_view`。

现役逐条修复归属从注册表读取；密度、比例和档位等多解目标不因此获得付费自救权。

## 八、冻结证据基线

冻结样本只承担明确的证据与反例职责，不代替当前 schema、正式产物或质量验收。

### 8.1 唯一保留位置与守卫

历史交接包和 design brief 曾各保存一份相同 JSON，`web/src/mocks/` 又保存第三份。清理后只保留
`web/src/mocks/` 的 7 份原始字节。

⚠ **字节真值不在本文档。** 文件名、字节数与 SHA-256 逐份登记在
`web/scripts/mock-baseline.json`，由 `web/scripts/check-seams.mjs` 读取，逐份验证文件清单恰好
一致、字节数与哈希均不变。人读的散文与机读的数据不许同存于一张 markdown 表：
它既不能自由排版（改一下对齐就可能解析不到），坏掉时又可能表现为一个恰好正确的份数。
本节只负责**来历与纪律**，不是任何脚本的输入。

要换基线必须有明确产品裁决，并同时更新 `web/src/mocks/` 的样本字节、
`web/scripts/mock-baseline.json` 与本节说明。

### 8.2 样本读法与不可推导项

这些是历史抓包，不是当前 schema：旧 job kind、旧 check kind、stage0_extras 和部分旧字段只证明
“曾经出现过”，不得重新接入 real mode。当前类型以 `contracts/schema/` 与真实 API 为准。

保留的关键反例：`jobs.json` 是合法空数组；stage2 secrets 可以为空；stage5 一章可以只有一条臂；
锚点覆盖率可低至 0.375 且未对齐块必须明示；已完成书的旧 check 可以是 fail；review diff_rows 可以
为空；stage7 允许 renamed=false 并要求理由。空值和红灯都不能被 mock 适配器“美化”。

`web/src/fixtures/backend_v1/` 是当前后端形状的版本化 fixture，与本节证据基线职责不同；前者可随
新版本新增目录，后者未经裁决不得改字节。

## 九、文档与测试的直接耦合

### 9.1 可执行 Markdown 语法

`contracts/requirements.py` 直接解析本文件：R 节必须以 `### R-N` 开头，R 条目必须是至少三列的
`| R-N.m | 要求 | 谁在查 |`，Q 定义必须以 `### Q-N` 开头，B 定义必须是
`| **B-NN** | ... |`。编号重复、表格缺列或完全解析不到条目会失败。

现役自检：R 按节核对精确条目数，Q 恰好 13、B 恰好 23 且连续。每个 B 必须被某条 R 引用；每个声明
HARD / INFO / 闸门 / 度量的 R 必须被注册 Rule 覆盖；每条 Rule 必须反向指向现存 R。结构行的
可解析落点使用 `` `仓内路径.py::symbol` ``，具体分母由 `tests/test_coverage.py` 维护，不能为文档清理缩小检查范围。

### 9.2 文档引用与扫描自检

`tests/test_docs_references.py::_git_paths` 自动发现 Git 跟踪与未忽略新增的 Markdown，按用途区分工程说明、
临时计划、fixture 来源和许可；产物、缓存与第三方依赖不进入项目文档面。工程说明必须从根 AGENTS 的引用图可达。
R/Q/B 引用、仓内路径、Markdown 标题锚点与 `路径::符号` 必须有效，移动或删除时同步修复实际消费者。
逐份覆盖与扫描防空由明确的正例、坏路径、删标题、缺符号及新增孤立 MD 反例守护，不用总命中数代替覆盖。

### 9.3 判据注册与反例资产

- Rule id 全局唯一；severity、owner、check_kind、who_checks、repair bucket 都是闭合枚举。
- metric 与 METRIC 双向一致；unver 不进自动修复；词表规则必须有 baseline。
- repair 非 NONE 只属于 stage5 且必须有 directive；NONE 时 directive 必须为空。
- fix 指向同层真实节点与真实 flag，费用可由唯一入口推导。
- semantic 判据面随 Stage6 整层退役：新链注册表全部为 mechanical（M1–M8、章级机械验、Hook 机械验、命名残留与交付链），语义质量一律走 unver 披露，不以历史节点补占位规则。
- stage4/5/7 每条 HARD 在 `tests/hard_cases.py` 有真实会红的反例；stage0/1 的 warning、编码、kind、
  新增 HARD 与阈值带由 `tests/test_ingest_fixtures.py` 双向覆盖。
- ⚠ **一条 HARD 有多个判定分支时，「红过一次」不等于每一支都红过。** Stage4 现役定向反例覆盖
  placement 缺失、重复、越章引用，分场节点必要端点缺失及完整背景提前放开；绑定另核对父节点证据
  和舞台闭集。`tests/test_str06_target.py` 直接走正式 Design/Assembly 编译器，保留状态、知识和转场红绿对照。
  已登记故事约束只拒绝包含严格先后或因果边的回路，不从披露顺序推断世界因果，也不拒绝纯非严格
  回应、观察回路。靶子自检在 `tests/test_hard_counterexamples.py`；语义完整始终另经审阅判断。

### 9.4 架构与前端 seam

`tests/test_layering.py` 守 contracts/runtime/web 分层、store 独占 JSON 读写、API/view 不直写产物、
服务不能花钱、view/queue 不产生判定、API 不造第二个 argv builder、完成度不偷看作业记录。
`tests/test_api_discipline.py` 守 loopback、无 CORS、状态域、unknown、唯一 paid 来源、部分遮蔽说明与
同书 409。`web/scripts/check-seams.mjs` 守唯一 fetch 缝、mock/fixture 消费边界、颜色令牌，
以及 `web/scripts/mock-baseline.json` 登记的 7 份冻结字节（来历见 §8.1）。

### 9.5 最小验证命令

```powershell
& 'D:\Miniconda\envs\novel_refactor\python.exe' -B -m pytest -q `
  tests/test_docs_references.py tests/test_coverage.py tests/test_rules_registry.py `
  tests/test_hard_counterexamples.py tests/test_ingest_fixtures.py
```

同一业务判据、schema、节点或 API 契约批次形状稳定后先跑以上五文件，再补对应 stage/API
与恢复消费者测试。测试频率、前端检查范围和完整回归门槛统一由 `AGENTS.md::测试选择与批次验收`
规定；局部修复、单个 stage 重构交付与验收续跑不自动触发全仓回归，达到根指南门槛再运行
默认非 corpus 回归；前端命令见 `web/AGENTS.md`，current-schema corpus 与 golden set 成书质量分别验收。
测试通过只说明已登记守卫没有发现问题，仍需检查真实消费者和最终盘面。

## 历史决策与验证证据

仍影响当前选择的裁决理由、实验边界与证据限制见 `docs/DECISIONS.md`。本文件只保留现役、可执行的契约；
历史记录不得覆盖这里的 R/Q/B 与当前 schema。
