# 小说重构系统 · 现役后端契约

本文件保存 R/Q/B、守卫归属与修复路由。它由 `contracts/requirements.py` 解析，主要供
`tests/test_coverage.py`、`tests/test_docs_references.py` 检查需求与实现的关联；生产判据由
`contracts/rules.py`、`contracts/judges/` 及各层校验器执行，模型请求不全文加载本文件。

维护时先按节点或稳定编号定位相关节，不通读全表。待办见 `docs/REQUIREMENTS.md`，当前形状见
`docs/schema/`，关键裁决及证据限制见 `docs/DECISIONS.md`。这些关联检查不能证明模型的语义质量。

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

证据不足保留 UNVER；Stage6 双盲 judge 不自动给出最终 PASS/FAIL，闭合仍由 typed 人审决定。
历史语义 evaluator profile 和 Stage5 自动收缩臂已经退役，不能通过整理文档恢复这些入口。

## 一、命名体系与章的唯一键

系统单人、本机使用，将中文小说处理为保留情节骨架的重写新书。界面与人工验收范围见
`web/AGENTS.md`，工程运维见 `docs/OPERATIONS.md`。

产品只有两种跑书模式：前端 Agent 自动模式由确定性 RunSupervisor 推进，Executor 生成，审批一律
人工——自动跑书在登记的人工审批闸停止（exit 2 `stopped`，`next_handler='human'`），用户在现有人工
Stage 页面过闸后用原配置续跑，每本书约 8–11 个人工触点；受托 Commander 审阅角色已于 2026-09-15
退役（见 `docs/DECISIONS.md`）。人工模式由用户在各 Stage 页面选择生成、编辑、
审阅与下一动作，仍受同一后端前置、授权、freshness 和提交校验约束。开发维护 agent 不作为
第三个流水线角色。执行与恢复归运行代码，最终交付不生成或依赖 Markdown 报告，状态与证据在前端查看。

- `chapter_ordinal` 是阅读序整数；输出章号恒等于源章号，不合并、不删除、不拆分、不重排。
- stage 表示产物/schema/报告层，node 表示作业，二者不是一一对应。节点唯一登记在
  `contracts/invariants.py::NODES`，label 和 `STAGE_SECTIONS` 从注册表派生，不手写第二套映射。
- 作业 id 为 `<stage>.<动词>`；复检是 `--check-only` flag，不造 check 节点，Stage3 无现役产物或节点。
- Stage5 只有 canonical 闭卷路径，唯一正文落 `stage5/chNN.json`；全书补缺不重购已有章，定向改写用
  `--chapters`，所有写入共享书级互斥。并发限制以当前注册表和执行策略为准。
- <a id="enums-superset"></a>`/api/enums` 从当前 invariants 生成；前端 fixture 逐项对账。
  反例见 `tests/test_api_enums.py::test_the_web_enums_fixture_is_the_same_payload`。

不能从 stage 编号拼 R 编号：Stage2/4/5 各有多节点或多相职责。
`tests/test_coverage.py::test_stage_sections_is_not_the_naive_stage_number` 保留该反例。
旧输出只可与冻结旧代码配对，不原地迁移、选写作臂或触发付费重建。

## 二、功能需求 R-0 ~ R-14

### R-0 · 非正文剔除（stage0）

免费识别非正文并落盘；机械校验（含正文占比闸）通过即由 producer 写入 auto 批准（`system:stage0-auto`，2026-09-15 全自动裁决），进入 Stage1。剔除的语义正确性（番外盲区，`R-0.7`）没有确定性防线，随交付披露（Q-13）。`stage0.approve` / `stage0.edit` 节点已退役（RETIRED_NODES）。

| # | 要求 | 谁在查 |
|---|---|---|
| R-0.1 | 自动剔除用的是 `R-1.7` 的同一套判据与同一张 `NONBODY_KINDS`——**只有一份** | `HARD` |
| R-0.2 | 剔除逐段只记不可再生的决定与最小定位证据：类型、标记行、原文码点区间、来源。行号、字数与预览从原文和 span 现算；**番外体不进产物目录** | `HARD` |
| R-0.3a | **人工批准这个动作本身**——「这一段到底是不是番外」没有结构不变量可查，只能人看。**批准的唯一来源是人**：HTTP 与受托 Agent/CLI 均经 `stage0.approve` 的同一 typed producer；公开检查单从当前 revision 链、执行资格与 freshness 只读投影，不要求额外服务盖章。批准请求必须带本次实际审阅的完整 `expected_revision_id`；工作包 A 与提交之间若经 edit/adopt 变成 B，乐观锁关闭式拒绝，不能把 A 的人眼结论盖到 B。审批页显示完整原文、删除高亮与运行时 warnings；Agent Reviewer 的 immutable workpack 也必须逐字携带完整 current clean_text 与全部剔除段，首尾/词表/章号摘要只是导航，不能代替全文反查纯数字章号下的语义漏剔。首个章号前的非空片段是 `R-1.12` 要求下游保留的开篇文案：叙事性概述、提前呈现后文情节/对白或与正文形成重复钩子均不得仅凭“不是第一章正文”判为漏剔；只把书名/作者署名、平台网址、站内导航、更新订阅/票赏推广、作者站务元话语等不属于作品叙事文案的站务或元数据残留视为问题。发现误删/漏剔时必须有不写审批、不执行节点的 typed stop 出口，不得被唯一 approve option 逼成违心通过。正式分章对照在免费 Stage1 落盘后进行 | **`人`** + revision 乐观锁，见 `B-20` |
| R-0.3b | **未获批准的书，stage1 必须拒绝启动**；执行资格的唯一落点是 `novel/stages/stage0/eligibility.py::is_stage0_executable`，消费方一律 import、**不许复述条件**。2026-09-15 起批准由机械校验通过自动写入；存量 pending 书重跑 stage0.remove 收口 | `HARD` |
| R-0.4 | 【已退役 2026-09-15】人工编辑剔除范围随 `stage0.edit` 退役；判据漏掉的那类（语义误剔/漏剔）转为 `R-0.7` 的交付披露，不再有人工修正入口 | `不适用`（退役条款） |
| R-0.5 | revision 链逐条自证（判据 `s0_revision_chain` 逐条复算 `revision_id`），`clean_text.txt` 是 `current` 的唯一物化投影。历史 `origin=human` 稿保留只读；现役机器稿重算按 `R-0.8` 处置 | `HARD` |
| R-0.6 | 必须能核对**两个内容身份**：链根保存 `normalized_source_sha1`（原始 txt 归一化文本，不是文件字节 digest）；`clean_sha1` 从 current `clean_text` 与 live 投影现算。Stage1 的真正输入身份是后者 | `HARD` |
| R-0.7 | 剔除的**正确性**——「这一段到底是不是番外」。2026-09-15 起人工确认入口退役，本维转为 Q-13 内容质量类披露随交付输出 | `人`→`披露`（`s0_correctness`；Q-13） |
| R-0.8 | 重跑 stage0 的覆盖规则（2026-09-15 全自动裁决）：当前稿是机器稿（无论 pending 还是已批准）⇒ 直接替换为新算法结论并保持 auto 批准——剔除是确定性算法的输出；当前稿含人工内容（`origin=human` 存量）⇒ 关闭式 TerminalState 拒绝，指引显式废弃书目录重跑（`R-0.14`），不静默覆盖；原文变化连替换都不谈。历史「人否过的候选不复活」检查保留 | `HARD` |
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

### R-2 · 全书人物与必要知识（stage2）

首次由 `stage2.analyze` 一次语义分析产生两份草稿，Characters 与 Global 分别编译并保持独立的产物、证据、编辑和恢复生命周期；后续修订由所属 producer 处理。Blueprint 生成前由统一机械准入（`novel/orchestrator/mechanical_admission.py::blueprint_generation_ready`）检查人物、Global、原文与称名投影可读、当前且机械检查全过；Stage2 语义审阅（Global 逐项审阅、Blueprint 章审与跨章复核）已整体退役，机械检查不能证明的语义维度以报告 unver 行常驻披露，不产生审批待办。字段与材料范围见 `docs/schema/stage2.md`。

| # | 要求 | 谁在查 |
|---|---|---|
| R-2.1 | canonical 是登记身份，必须在本实体去重 aliases 中恰好出现一次；真实重名允许跨实体共享字面，以稳定 char_id 与独立归属证据区分，不自造消歧词，也不把登记名当作写作许可。归属与消歧的语义正确性不因机械闭合而获证，随 Stage2 语义未审披露。 | `结构`（`contracts/schema/characters.py::RegisteredCharacter`） |
| R-2.2 | 人物 first_chapter 须有该章首次出场锚点；每个称谓须由所属人物的证据支持，机械检查按规范化字面核验包含关系，正式锚点仍须逐字 SHA 与坐标闭合。代码不以字面命中代替语义归属，不模糊合并异写；归属语义随 Stage2 语义未审披露。 | `HARD`（`novel/stages/stage2/character_checks.py::check_characters`）+ `novel/stages/stage2/core_evidence.py::validate_evidence_anchors` |
| R-2.3 | 称谓整条不得等于已登记的裸代词；判断使用 BARE_PRONOUNS 闭集，不能把所有含代词的有据称谓一概拒绝。人物登记不承担对白施受或局部称呼许可。 | `HARD`（`novel/stages/stage2/character_checks.py::check_characters` + `contracts/invariants.py::BARE_PRONOUNS`） |
| R-2.4 | 称谓绑定只保存 surface、identity_revealed_from、evidence_refs；不存逐次 by/context、施受关系、知情矩阵或场景称呼表，也不再有同章披露节点或身份映射许可。完整 Beat/State 承担各自消费职责。 | `结构`（`contracts/schema/characters.py::CharacterNameBinding` / `RegisteredCharacter`） |
| R-2.5 | first_chapter 与 identity_revealed_from 必须是当前 MainText 阅读序章号。后者只表示称谓与实体的身份映射何时向读者揭示，不能解释为称谓字面何时可出现；证据位置、字面首现和身份揭示各自独立，揭示时点的语义恰当性保持未审披露。 | `HARD`（`novel/stages/stage2/character_checks.py::check_characters`） |
| R-2.5b | 每个人物至少有一个有据称谓，每实体每个不同 surface 只登记一次；人物集合完整性不由机械检查证明，匿名关键个体不能漏、无名背景群体不伪造实体的语义维度随 Stage2 语义未审披露。不再报告逐次 address 自称或出现次数读数。 | `结构`（`contracts/schema/characters.py::RegisteredCharacter`） |
| R-2.6 | role 只描述首次公开身份：非空、不等于 canonical、不含已登记结局词或模棱语。role/canonical 的机械命中仍为 HARD；零命中不能证明未泄底，`role_fate_unverifiable` 无条件记录该语义维度为 UNVER（登记于统一准入的语义未审披露），身份支持性和后文经历混入同属未审维度。模棱语按完整短语匹配，确定判断“不可能”不因子串“可能”误报。 | `HARD` + `INFO`（`novel/stages/stage2/character_checks.py::check_characters`；INFO 记录 UNVER，不产生审批待办） |
| R-2.6b | 匿名关键人物使用原文有据的亲属称谓、排行或头衔登记；同名不是强制改名的理由。首次及新增人物稳定 ID 由代码分配，修订保留身份，不按数组位置重编号。 | `结构`（`novel/stages/stage2/characters.py::compile_response` + `contracts/schema/characters.py::CharactersEdit`） |
| R-2.7 | Characters 与 Global core 不保存 fate/character_states；后续经历、结局与动态状态留在剧情状态，长线隐藏真相按 arc_secret 准入。不得为接住结局而恢复角色结局字段，role 泄漏继续由 R-2.6 的机械检查与语义未审披露守卫。 | `结构`（`contracts/schema/characters.py::CharacterRegistry` + `contracts/schema/knowledge.py::KnowledgeCore`） |
| R-2.8 | 关系属于跨章长期知识，两端引用独立人物登记。`effective_from` 与 `effective_until` 描述世界内成立区间，末章包含在内，持续至书末为 null；`revealed_from` 独立描述读者首次获知。全部章号合法，起止有序。 | `HARD`（relation_dangling/global_core_contract） |
| R-2.9 | 长线秘密以 `setup_chapters` 与 `revealed_from` 分开表达铺垫和读者揭示；无铺垫明确填 []。铺垫不得晚于揭示，同章不同情节单元的先后属语义维度，随 Stage2 语义未审披露，Stage2 不虚构分场或坐标作机械拒绝。归一不得裁剪、排序改写或清空模型的铺垫意图。 | 载体（`contracts/schema/knowledge.py::GlobalArcSecret`） |
| R-2.9b | 合法空秘密集合和无铺垫秘密都保留原样；数量不证明覆盖齐全。漏掉的长线真相及错误提升的局部状态不由机械检查证明，随 Stage2 语义未审披露（`secrets_absent` 常驻 UNVER），不产生审批待办。 | `INFO`（secrets_absent 明示 `UNVER`） |
| R-2.9c | 人物及群体的知情、信息来源和传递时序不进入 Global core，也不再设同章披露节点或身份映射许可；世界内知情、事实成立与读者揭示三者不得互相替代。Stage4 以章节总纲、场景安排和呈现指令设计章内发展。 | 载体（`contracts/schema/knowledge.py::KnowledgeCore` 字段排除） |
| R-2.9d | 秘密揭示的语义只能由人与正文核验；不能用是否出现“其实/原来”等高频词证明或拒绝“演而不说”的合法揭示。原 GlobalReview 载体已随语义审阅退役，只随冻结旧代码解码。 | `不适用`（Stage2 语义未审，最终交付披露该限制） |
| R-2.10 | 人名区分度、规则类型、称谓场合与叙述人称分别表达各自语义，不得混轴。叙述人称只表示我／你／他；限知／全知当前没有独立载体，不得声称由下游自动守住。取值域以当前 schema 为准。 | **无守卫 → `B-08` / `B-09`** |
| R-2.11 | Global 仅收 setting/world_rules/relations/arc_secrets。提升门槛是删去后会破坏跨章节事实、因果或披露一致性；局部行动、临时状态、单场对话与普通信息传递留在完整剧情载体。不以固定条数或篇幅截断合格事实，提升边界与完整性的语义核验保持未审披露。 | 载体（`contracts/schema/knowledge.py::KnowledgeCore`）+ prompt |
| R-2.11b | 世界规则具有 kind 与版本，每版用稳定 version_id 精确标识规则正文、证据、世界内成立区间及读者揭示章。同一规则版本按生效时间排列且含末章区间不重叠；desc 写清条件、运行及后果，人物个案不提升为规则。 | 见 `R-2.19` / `R-2.20`；概括准确性保持语义未审披露 |
| R-2.12 | 身份描述里不许藏结局或未揭示的秘密 | **无守卫 → `B-14`** |
| R-2.13 | 0 LLM typed 编辑保持所属 producer 独立。GlobalEdit v6 只改 setting/world_rules/relations/arc_secrets；CharactersEdit 独占人物增删与称名绑定。Global 已有成员按稳定ID稀疏patch，新增/删除/拆分走带修前集合SHA的 collection_edits add/delete/split，也保留整组 *_replace。重叠写口、集合漂移、悬空ID/精确版本、非法章号和失配证据在内存候选阶段拒绝，不半应用。人工证据可引用当前 evidence_id 或交章内唯一locator；事实变更必须同时交证据。模板默认no-op，原样应用不得写成“已修复”。修订保留canonical输入WAL、Global完整提交WAL及INVALID/失败恢复；编辑后由免费机械复验重判，不再产生审阅待办。 | `结构`（`novel/stages/stage2/extract.py::_build_global_edit_candidate`、`novel/stages/stage2/extract.py::run_apply_edit`）+ 恢复反例 |
| R-2.14 | Global 不消费 HookCopy，不保存源文案事实或开篇范围；源文案与文案创作归既有 Stage6。不能为填充新core加入 source_blurb 空壳，也不能用文案断言替正文向长期事实注入无据内容。 | 载体（`contracts/schema/knowledge.py::KnowledgeGrounding`）+ producer 真实输入与 schema 边界 |
| R-2.15 | 【已退役 2026-09】Stage2 GlobalReview 模板、工作包、决定集、义务继承与免费刷新随语义审阅整体退役；`stage2/global_review.json` 与 `stage2/global_review_decisions.json` 进 `contracts/artifacts.py::RETIRED_ARTIFACT_PATHS`，旧字节只配对冻结旧代码读取，不迁移、不继承审批。Stage2 下游前置由统一机械准入与语义未审披露承接（见 `R-2.16`）。 | `不适用`（退役条款，无可执行机械义务） |
| R-2.16 | 新 clean base 上 `stage2.blueprint` 的串行前置由统一机械准入唯一判定：当前 Characters、Global、MainText 与称名投影必须可读、当前有效、无未恢复 INVALID，且所需机械检查全部通过；机械检查缺失、未运行或不可判仍阻断，不依据 CheckReport.passed。登记于语义未审披露的 unver 行只披露不阻断。`blueprint_character_projection.json` 只由当前 CharacterRegistry 与 MainText 确定性物化，由 `stage2.characters` 发布与免费复验恢复，只投影称名所需字段。人物称名 inventory 改变后现有 Blueprint 依生产 consumed projection 失效，未消费字段变化不扩大重建范围。恢复按实际 owner 先走人物或知识 typed edit/revalidate，再重新过准入；不能绕过准入先生成 Blueprint。 | `结构`（`novel/orchestrator/mechanical_admission.py::blueprint_generation_ready` + `novel/stages/stage2/character_refs.py::materialize_blueprint_character_projection` + `novel/store.py::_blueprint_character_projection_freshness` + `novel/orchestrator/queue.py::plan_book_from_overview` + state/queue/CLI/API/Stage7 消费边）+ formal/INVALID 物化、schema-invalid/legacy unknown/额外键形/上游漂移反例、0-LLM 恢复、freshness/投影/API/下载回归 |
| R-2.17 | 【已退役 2026-09】原统一审阅覆盖矩阵（人物集合完整性、身份及首次出场、称谓归属、role、name_distinctiveness 与 Global 逐项覆盖）随语义审阅退役，不再构成生成前置或义务分母。这些语义维度按登记的 unver 行披露：`role_fate_unverifiable`、`secrets_absent`、`plot_semantics`；表外语义维度随最终交付说明 Stage2 语义未审限制。 | `不适用`（退役条款，无可执行机械义务） |
| R-2.18 | 每条 arc_secret 只表达一个原子长线真相。fact 与 fact_ref 必须恰好选一支：独立fact必须举证；已由关系或规则版本表达的真相只用 fact_ref 精确引用 relation_id 或 rule_id+version_id，evidence_refs=[]并复用权威事实的证据，revealed_from必须相同。删改目标必须同时修订引用者；不得维护重复事实正文。 | 见 `R-2.20`（global_core_contract）；精确引用反例同时覆盖本条 |
| R-2.19 | 关系及规则版本的世界内成立窗口与读者首次揭示独立。effective_until包含末章；读者晚知不改变事实实际成立时间。规则更替版本区间不能重叠；机械检查不要求揭示晚于生效或早于失效。消费端须区分当前有效状态与已经结束的历史事实：当前状态受成立窗口限制，历史事实在揭示后仍可陈述，但不得伪装为当前状态。精细逐章消费由 Gate B 验收，不再使用 via_secret 或人名启发式制造第二套闸。 | `HARD`（global_temporal_contract；仅成立窗口）；揭示时点语义保持未审披露 |
| R-2.20 | Characters v2 与 KnowledgeGrounding v7 各自保存 evidence_anchors 共享表，锚点为章号、左闭右开坐标及原文片段SHA，主体只留evidence_refs。模型只返回局部实体句柄与同轮关闭候选表中的e000001式证据ID，代码编译稳定正式ID及精确引用；相同锚点在一份artifact只保存一次。未建立的setting值为null且refs=[]；有值必须有据。正式锚点漂移、悬空ref、原文摘要变化不得被归一移动或默认值掩盖。人工locator必须在声明章内唯一回取，失败写前拒绝并保留原编辑；已购草稿保存于checkpoint/custody，形状拒收不丢响应、不靠重买恢复。Global模型与人工恢复继续校验冻结MainText、候选表、实际人物投影、session/ledger和完整prepared提交集合；新版本不假兼容旧字段，旧epoch须配旧代码排空或归档。运行时事实投影剥离共享证据，证据修订重开机械复验而不无条件扩大写作freshness。 | `HARD`（global_core_contract/global_evidence_unlocated）+ core_evidence纯编译/恢复反例 |
| R-2.21 | Global 全部成立起止、读者揭示、铺垫与共享证据章号必须落在当前MainText阅读序章集合，生成、免费复验和人工编辑同判。未知/越界不是“以后再生效”，也不能被归一裁成合法空值。 | `HARD`（canon_chapter_refs_dangling）+ 当前MainText对齐 |
| R-2.22 | 【已退役 2026-09】原 GlobalReview kind 的 blocking/disclosure 分级、generation-safe 面与 quarantine_or_fix 处置随语义审阅退役，不再构成生成或交付前置；`GLOBAL_REVIEW_*` 常量只服务历史字节识别。成书与交付的 Stage2 前置改为统一机械准入（`R-2.16`、`R-10.14`），语义未审随交付披露。 | `不适用`（退役条款，无可执行机械义务） |

保留两类反例：身份描述换个说法仍可能泄露后文，词表零命中不证明无泄漏；非法铺垫与失配正式锚点必须保留问题事实，不能以归一裁剪制造通过。对应 `B-14`、`R-2.9`、`R-2.20`。
结构化调用必须校验全部必需顶层键，不以某个下游判据恰好兜底代替响应契约。

Stage2 语义审阅（Global 逐项审阅、Blueprint 章审与跨章复核、问题义务与审批继承）已整体退役：
机械检查只证明形状、引用与证据闭合，不证明人物完整、主张获原文支持或剧情抽取正确。
登记在 `novel/orchestrator/mechanical_admission.py::SEMANTIC_UNREVIEWED_RULES` 的 `role_fate_unverifiable`、`secrets_absent`
与 `plot_semantics` 以 unver 行常驻披露且不阻断准入；表外语义维度随最终交付披露 Stage2 语义未审
限制，不产生审批待办。统一准入由 CLI、调度、最低读取边界和交付检查共用，任何一方不复述第二套条件。
载体和消费约定见 `docs/schema/stage2.md`；源文案与恢复反例见现役 stage2 测试面
（原 source-blurb 与 Commander 审阅载体专项测试已随语义审阅与角色退役删除）。

### R-3 · 逐章 Blueprint 抽取（stage2）

逐章抽取事实、因果、舞台及保真材料；来源区间由代码回取，模型不转抄原文。精确形状见 `docs/schema/stage2.md`。

| # | 要求 | 谁在查 |
|---|---|---|
| R-3.1 | 完整剧情节点、持续状态、稀疏约束与共享证据由编译器分配稳定正式 ID；模型只交章内临时 key。普通修订可以保留 ID，语义拆合分配新 ID，旧 ID 不复用并保存 tombstone/替代映射；ID 不证明语义或接受仍有效。 | `结构`（`novel/stages/stage2/plot_compile.py` / `::plot_edit.py`） |
| R-3.2 | EvidenceAnchor 绑定当前源章 SHA、程序编号的段落范围及零起点半开字符区间；所有引用可解析，原文按需从源产物回取一次。多个内容可以共享段落，不要求节点证据连续分割全文。 | `HARD`（`plot_evidence`） |
| R-3.2b | 响应只选择段落范围，编译器去重物化章内 EvidenceAnchor。source_contexts 引用证据并保存有据的时层、地点和实际在场人物；坐标不能推导语义，提及不等于在场。 | `结构`（`novel/stages/stage2/plot_compile.py::compile_chapter`） |
| R-3.3 | 写手接收完整剧情正文与必要执行条件；原文连续证据段只供审阅和 B 绑定，不作为写作输入。逐字材料仅由有据 literal 条件派生，普通对白意义归 beat.content。 | `结构`（`novel/stages/stage4/plot_context.py` + `novel/stages/stage5/plot_prompts.py`） |
| R-3.3b | 必要 literal/value/object 约束必须从当前蓝图经计划、Context 到写手材料完整传递；用真实消费输出与同源编译结果对证，不能只证明上游字段存在。 | `结构`（`ctx_plot_exact` + `tests/test_plot_writer.py`） |
| R-3.4 | 字面条件必须明确作用于哪一完整节点及场景端点；需要分段呈现时仍保留原条件与父节点身份，不能把完整原文段伪装成逐字点。 | `结构`（`contracts/schema/plot.py` + `novel/stages/stage4/plot_placements.py`） |
| R-3.5 | beat.content 是节点唯一完整剧情正文，包含必要动机、施受关系、反应和结果；辅助状态与约束不得藏着正文遗漏的关键剧情。证据均由代码回取，不能据引用闭合自动签语义接受；剧情语义随 `R-3.13` 的 `plot_semantics` 披露。 | `结构`（`novel/stages/stage2/plot_prompts.py::BLUEPRINT_SYSTEM`） |
| R-3.6 | State modality 与 Constraint 类型是关闭式取值；未知类型拒收，不兜底成逐字写作要求或世界真相。同章披露节点与知识关联载体已退役，不再新建第二套主体。 | `载体`（`contracts/schema/plot.py`） |
| R-3.6b | State/Constraint 可以合法为空；不设每章最少辅助项、节点数量或字数配额。数量仅作结构读数，不能为达标制造状态或约束项。 | `不适用`（数量只在 `R-3.13` 的 `plot_shape` 无条件报出） |
| R-3.6c | 保留有据的现场、回忆、转述等时层，不按叙述语气推断时层，也不靠全书现场计数替代语义检查。 | `不适用`（时层与归因语义随 `plot_semantics` 披露，不产生审批待办） |
| R-3.7 | 参与人、在场者、持有者、知情受众、叙述者、观察者和关系端点使用 ActorRef，内部 char_id 与可选 presentation_ref 分开。ActorBinding 只保存登记称谓、本章证据和可选公开 State；同一 Beat 的同一 char_id 可以有两个不同呈现，不按人物 ID 去重。 | `载体`（`contracts/schema/plot.py`） |
| R-3.7b | ActorRef 按其使用位置所属章解析；多呈现必须明确绑定，缺失或归属歧义保留候选并交 owner 免费订正，不取首项或回退 canonical。唯一绑定的省略引用与显式引用解析为同一呈现；晚章新增绑定不改变旧章已解析身份。source_context 在场者仍与参与者、叙述者独立。 | `HARD`（`present_ids_dangling`）+ `novel/stages/stage2/plot_compile.py::validate_actors` / `novel/stages/stage4/actors.py::ActorCatalog` |
| R-3.7c | 在场者不确定与合法无人分别表达；仅被提及、回忆、相信或转述的人物不自动视为当前在场。连续性与真实性的语义随 `plot_semantics` 披露。 | `不适用`（Stage2 语义未审，不产生审批待办） |
| R-3.7d | 晚出现的名字、后台身份和结局不能被无据提前写入正文。人物称名仍守本章可见闭集；自由文本改写式后见之明由 Stage4 设计、结构审阅与最终人工验收核验。 | **`人`**（另见 `B-16`） |
| R-3.8 | 章首、章尾及中间关键剧情均须完整抽取；证据范围覆盖全文不能证明语义完整，也不要求为了首末坐标覆盖生成空洞节点。剧情完整性随 `R-3.13` 的 `plot_semantics` 披露。 | `不适用`（Stage2 语义未审，不产生审批待办） |
| R-3.8b | 抽取 prompt 明确完整因果节点与重要事件、时间、揭示、独立依赖的拆分规则；多个动作或形容词、潜在换场方式不单独构成拆分理由。 | `结构`（`novel/stages/stage2/plot_prompts.py::BLUEPRINT_SYSTEM`） |
| R-3.9 | 不设置字数/节点比例或节点上限；过粗丢失重要边界、过细重复语义由原文样例对照与最终人工验收判断，不能据数量自动付费返工。 | `不适用`（数量只在 `R-3.13` 的 `plot_shape` 无条件报出） |
| R-3.10 | 完整原因→动作→即时反应→结果优先同一 beat；原文重要揭示或独立依赖必须明确建模。普通姿态、修饰和措辞可以改编，关键动机、对象、结果及认识性质必须保留。 | `不适用`（语义标定缺口见 `B-07`；剧情语义随 `plot_semantics` 披露） |
| R-3.11 | 主动定向重生成须使用 refresh-write 与新逻辑 run；定向修订绑定当前章与实际依赖。恢复先核验同一 run 的已购响应，不能因 token、epoch 或缓存策略变化重复购买。 | `结构`（`novel/stages/stage2/plot_repair.py` + `novel/llm/stage2_runs.py`） |
| R-3.12 | 真实修订失败保留问题、来源和已购响应；提供同一 typed 编辑、格式恢复、免费复验及定向生成入口。不能通过删问题或重置预算绕过失败。 | `结构`（`novel/stages/stage2/plot_edit.py` / `plot_repair.py`；历史缺口 `B-18`） |
| R-3.13 | 每章有非空 chapter_function 与完整 beats；State 只携持续且后续需要的内容，Constraint 只保留必要显式条件。状态性质与传播权限分开，Global/Beat/State/Constraint 各有唯一职责。主体、引用、状态闭合可机械核验，剧情、归因、因果、揭示与持续状态语义保持 unver，随 Stage2 语义未审披露，不产生审批待办。 | `HARD`（`plot_contract` / `plot_state_continuity` / `plot_global_refs`）+ `度量`（`plot_shape`）+ `INFO`（`plot_semantics`） |
| R-3.14 | 多个叙述声音分别登记身份和证据；不自动压成同一现场，也不以单一人称的表层检测证明全部声音正确。 | `不适用`（叙述声音归因随 `plot_semantics` 披露，不产生审批待办） |
| R-3.14b | 叙述者**自称的字面**必须照原文用字（「我」/「咱」/「本宫」/「朕」），第三人称交空数组合法。凡是契约要求逐字照抄的就该有一条确定性检查盯着，而不是只在 prompt 里叮嘱——实测 262 个照抄锚点 30 个对不上，**prompt 劝说压不住** | `INFO` |
| R-3.15 | EvidenceAnchor 只定位证据；content 与字面条件必须语义完整。不得通过截断正文或字符切片制造另一份可见剧情正文。 | `结构`（`novel/stages/stage4/plot_context.py::build_writer_context`） |
| R-3.16 | 正式蓝图与源章一一对应、不缺多重乱：Blueprint 使用 chapter:N 与 cross_chapter 独立范围，章集齐备、章序一致、无第二个章键由机械判据守。原章审与跨章复核已退役：剧情、归因、因果与揭示语义随 `R-3.13` 的 `plot_semantics` 披露，不生成逐节点或逐 delivery 正向回执，也不产生审批待办。 | `HARD`（章集判据：upstream_missing / section_id_leak / chapter_coverage / chapter_order） |
| R-3.16b | 唯一现役抽取架构为 plot-blueprint-v1。每章一次逻辑请求：本章原文一次、当前可见人物、必要 Global 投影及前章需要携带的稀疏状态；未来正文不下发。初次抽取按携带状态依赖依序推进，在既有并发上限内执行，独立 session 与验证后 checkpoint 保留；这不承诺同书各章并行。请求、恢复、编译共用显式契约及实际消费输入。 | `HARD`（`scope_violation`）+ `度量`（`context_scope`） |
| R-3.17 | 转场点由地点字面推导、**不单存字段**，且它的读数要带着自己的偏差一起报——地点是自由文本、粒度不稳（街上/街边、公寓/公寓卫生间门口），按它相等推导出来的转场数**系统性高估**。⚠ 它作为**下发给写手的舞台信息**是对的，不改；但凡引用这个数的地方都要带上这一句 | **`人`**（转场与舞台审美交最终人工验收；无独立地点字面转场计数判据） |
| R-3.18 | 段落、证据、引用或 schema 失败在当前响应恢复账与问题稿中明确保留，不能从 claim 猜证据。已购响应须绑定原 run/request/attempt/ledger、当前输入与编译契约才可免费采用；缺失、损坏、来源未知不得当缓存未命中重买。 | `结构`（`novel/stages/stage2/plot_recovery.py` / `plot_repair.py` + `novel/llm/stage2_runs.py`） |
| R-3.18b | EvidenceAnchor 保存代码解析的段落及字符位置，不内嵌 source.text。编辑/审计记录保存修订历史和 tombstone，不在每次模型工作包重复嵌入历史正文。 | `载体`（`contracts/schema/plot.py` / `plot_edit.py`） |
| R-3.19 | blueprint 的模型请求、内容恢复与 Flash→Pro 兜底都以**单章**隔离；一章失败不得取消其他已起跑章。每章只有依次通过 JSON parse、必需键、当前 Pydantic、dynamic validator 与章节 scope 切分后，才原子写入同一 `run_id` 下的 `_service/runs/<sid>/stage2/<run_id>/blueprint/checkpoints/<phase>-chNNNN.json`；旧书内 `_stage2_runs/` 已退役；即使为空或来源损坏，也不得复制、采用或视为 cache miss 后重新付费。普通 content 与完整通过的 reasoning salvage 均保存 accepted channel、原始 attempt/model/request 指纹、原文/payload SHA、contributing attempts、recovery mode 与 `salvaged`。该目录是内部 v1 sidecar，不是正式 artifact 或 `.llm_cache`，不参与完成度、schema 版本、缓存命中或正式 provenance。resume 必须显式复用原 `run_id`：恢复时先核对代码/契约/架构/输入/request/SHA 与 ledger 联结，再重新跑当前 schema、validator、scope、整书 compile 与全书联合 HARD；任何损坏、跨 run 或漂移都关闭式停止，不能退化成 cache miss 后重新付费。主动 `--chapters` 刷新首次进入必须使用全新 run，不得命中先前逻辑运行的 checkpoint；只有本次 refresh run 已生成唯一 manifest、并完整封存 batch resolution 后，显式同 `run_id` 的唯一人工 resume 才可恢复**本轮新采**的成功章，不能把普通重复执行冒充 resume。并发波次全部结束后先保存所有成功章，再把全部 `ResponseRepairRequired` 稳定排序为唯一 immutable manifest；decisions 必须完整一一覆盖 attempt 集合并逐项给 `submit|decline` + 非空理由，候选及 SHA 经一次性全量校验后只原子写一份 batch resolution，batch attempt 禁止再走单项 submit。每个逻辑 run 最多一次人工 workpack、一次人工 resume；恢复后再失败只能进入已登记自动动作或终态；V10 每章只保留一份完整剧情响应，不再存在事实分相抽取。正式 `chapter_blueprints.json` 仍必须覆盖完整章集；全书 artifact 已写而提交哨兵未写时按 artifact 指纹幂等收尾，不新增 `_rounds` 或重复 adoption | `结构`（`novel/stages/stage2/extract.py::_run_chapter_requests` / `_raise_response_repair` + `novel/llm/stage2_runs.py`）+ 检查点零 wire/损坏漂移/八章单 workpack/单 resolution/单 resume/幂等提交回归 |
| R-3.20 | entry_state_refs 只指本章章首或已由前章携出的有效状态；carry_out_refs 只携章末仍成立且后续需要的状态，允许合法空值。解除、取代显式绑定节点；继承状态在后章结束只写结束记录，不回写旧章正文。modality 表示 world/belief/claim/unresolved，不授予人物知情或读者许可。 | `结构`（`novel/stages/stage2/plot_compile.py::validate_entry_states` + `plot_state_continuity`） |
| R-3.21 | 必要跨节点 cause/story_before/response_to/observes/same_scene 与 literal/value/object 使用关闭式端点，不从相邻节点补边或把内部因果再抄一遍。分场在 Stage4 绑定已有事件场景，不新建第二套主体。错抽、缺少重要揭示或独立主体由 Stage2 所有的编辑/定向修订入口改蓝图，普通分场不触发修订。旧根配对旧代码可恢复，新链干净根重建，不迁移旧事实或审批。 | `结构`（`contracts/schema/plot.py` + `novel/stages/stage2/plot_edit.py` + typed 编辑与定向修订） |
| R-3.21b | 关闭式约束保留刺激—回应、观察者与被观察对象、必须同场及故事先后。所有端点映射先闭合；分场使用显式绑定，未知语义及合法倒叙不能按披露数组顺序自动判错。 | `结构`（`novel/stages/stage4/plot_placements.py::compile_placements`；机械闭合统一见 R-5.6） |

连续原文段不进入闭卷写手材料；普通对白保留关系与语义，剧情必要的字面例外独立核验。
定向重生成仍可能无变化或引入新错，必须重验完整受影响章，不能将“重跑过”当作“已修好”。

### R-4 · 即时机械测量（无 Stage3 产物）

Stage5/6 从已读取的 Stage1 文本即时计算机械读数，不生成 Stage3 产物。

| # | 要求 | 谁在查 |
|---|---|---|
| R-4.1 | **形态数字由代码算，不是 LLM 产**：正文与文案的字符、段句长度、对白与引号体等读数都由当前代码现场计算；没有可漂移的机械缓存 | `结构`（`novel/core/measure.py::measure_body` / `novel/core/measure.py::measure`） |
| R-4.2 | 正文与开篇文案段**分开测、互不污染**：Stage5 只测完整正文，Stage6 只测原作文案 | `结构`（`novel/stages/stage5/review.py::load_book` / `novel/stages/stage6/hook.py::_Upstreams`） |
| R-4.3 | **没有现役 Stage3 节点或产物**：`stage3.style` 与 `stage3/style_grounding.json` 只登记为退役身份，不能由 CLI、队列、API 或 producer 重新创建 | `结构`（`contracts/invariants.py::RETIRED_NODES` + `contracts/artifacts.py::RETIRED_ARTIFACT_PATHS`） |
| R-4.3b | **【Stage5 侧】只消费正文机械精确投影**：即时结果只保留 `avg_sentence_chars`、`avg_paragraph_chars`、`paragraph_sep_chars`、对白比值/引号体、章均字数、逐章平均句长 min/max；对白适用性由 `dialogue_ratio is not None` 推出，不存重复布尔值。`shape_block` 只把这些值序列化为句子/段落/对白/章篇幅/章间差异 5 个正文档位 | `结构`（`novel/core/measure.py::body_style_metrics` + `novel/core/bands.py::body_bands` + `novel/stages/stage5/review.py::load_book`） |
| R-4.4 | **Stage6 只消费原作文案机械五叶**：`chars`、平均句长、平均段长、引号体、对白比例；生成与机械复检共用同一次即时投影，不序列化完整测量对象 | `结构`（`novel/core/measure.py::hook_style_metrics` + `novel/stages/stage6/prompts.py::build_material`） |
| R-4.5 | 测量模型只是**进程内 strict 载体**，不带 `_schema_version`、`_provenance`、prompt、模型或 usage，也不进入 artifact registry | `载体`（`contracts/schema/measurement.py`） |
| R-4.5b | 旧 Stage3 v2 的解码器及专用 consumed 投影已退役；旧来源边不能通过当前 Store 获得现役有效性，历史字节只随旧代码与旧根配对读取 | `载体`（`contracts/schema/__init__.py::ARTIFACT_SPECS` + `novel/store.py::_projection_payload`） |
| R-4.6 | 原作文案是**必填机械输入**；无文案段已由 `R-1.12` 挡在 Stage1，本层不造 nullable hook 或空兜底 | 见 `R-1.12` |
| R-4.7 | 当前机械测量不独立记 provenance；Stage5/6 各自的来源账直接记录真正读取的 Stage1 主体 | `结构`（`novel/consumption.py::stage5_main_text` / `novel/consumption.py::stage6_hook_copy`） |
| R-4.8 | 测量算法口径是独立版本轴；只改算法、不改 Stage1 JSON 形状时，下游也必须自然 stale | `结构`（`contracts/invariants.py::STYLE_MEASUREMENT_CONTRACT`） |
| R-4.9 | 当前测量的真实上游只有 Stage1 正文或 Stage1 原作文案；测量本身不等待、也不消费 Stage2 | `结构`（`novel/stages/stage5/review.py::load_book` / `novel/stages/stage6/hook.py::_Upstreams`） |
| R-4.10 | 测量契约必须进入 Stage5/6 对相应 Stage1 文本的 consumed 投影，使算法改动与输入文本改动走同一 freshness 机制 | `结构`（`novel/consumption.py::stage5_main_text` / `novel/consumption.py::stage6_hook_copy`） |
| R-4.11 | 不再注册 Stage3 规则或 check 端点；即时读数只由 Stage5/6 的真实消费者继续进入 prompt、归一化或机械检查 | `结构`（`contracts/rules.py` + `novel/core/measure.py`） |
| R-4.12 | 不再生产 Stage3 `.INVALID`、轮次稿或修复命令；Stage1 变更后下游现场重新计算 | `结构`（`contracts/artifacts.py::ARTIFACT_PATHS` + `contracts/invariants.py::NODES`） |
| R-4.13 | 旧 Stage3 字节不迁移、不重建；Stage5/6 freshness 只使用现役节点的登记投影。旧边未能解析时保持 unknown，不为其重签来源或审批 | `结构`（`novel/store.py::freshness_of_inputs` + `novel/consumption.py::projection_for`） |
| R-4.14 | `--measure-only`、Stage3 `--check-only` 与默认生产入口全部退役；诊断由 Stage5/6 的 0 成本检查展示实际消费后的读数 | `结构`（`contracts/invariants.py::RETIRED_NODES` + `novel/orchestrator/nodes.py::build_argv`） |
| R-4.15 | s3 闸、Stage3 API 与页面全部撤下。当前新 clean base 的盘上审批为 v9、公开 API 为 v9（各自独立形状）；当前审批只解码 v9，v1～v8 及未知版本均拒绝；旧审批解码、升版和旧 Stage3 proof 迁移分支已删除，Stage2 已读/接受记录不再属于审批契约。backfill 只为当前工作流补录可证明的检查单，不迁移旧 base、旧 token 或补签历史审批。普通 GET 与事件观测只读；新写入口必须验证当前 workflow manifest。backfill 不从“产物齐备”制造审批，也不得把旧 Structure subject 重签为 current s4。Stage4A 设计审阅只授权 B，不打开 s4；Stage5 写作证明绑定当前 Stage1 正文、Stage4 计划、经最终结构批准后单独生成的当前 WriterContext 和测量契约；s4 仍只由 B 编译后的独立 typed 计划结构裁决打开 | `结构`（`novel/orchestrator/approvals.py::backfill_from_disk` / `::authorize_plan` / `::authorize_context` / `::begin_writing` + `contracts/schema/api.py::ApprovalDoc` + `novel/work_units.py::check_workflow_write`） |

实际下发叶子由 `novel/core/measure.py::body_style_metrics` 与
`novel/core/measure.py::hook_style_metrics` 各自声明；不能把测量对象的全部字段自动发送给模型。
修改读数投影同时核对 producer、prompt 与校验消费；字段说明见 `docs/schema/stage5.md`、`docs/schema/stage6.md`。

### R-5 · 盲场景设计与证据绑定（stage4）

A 脱离源顺序设计，B 给冻结候选绑定证据，代码编译正式计划；只存决定与引用，事实内容由代码搬运。

| # | 要求 | 谁在查 |
|---|---|---|
| R-5.1 | 输出章号 1..N 连续 | `HARD` |
| R-5.2 | **一章对一章**：源章列表长度恒为 1 · 源章序非递减 · 源章全覆盖 · 无源章被两个输出章共用 · **源章号 == 输出章号**（前四条的合取式，做成会报错的检查而不是一句注释） | `HARD` ×5 |
| R-5.3 | 篇幅方式恒为「保持」 | `载体`（`contracts/schema/stage4.py::PlanChapter` 不提供 `op` 选择，预算从当前上游分配） |
| R-5.3b | 逐章预算之和落在源字数的 **±20%**（`PLAN_BUDGET_BAND`），**无豁免**（原型在带之外又乘了一次 ±10%，那第二层已裁掉）。⚠ 2026-08-22 从 ±10% 放宽：这是把「哪一章厚、哪一章薄」这个自由度还给 stage4，治的是「成稿只改了措辞」；**放宽的对价是同批的 `R-5.3c`**——不配它，全书总量从此无人守。⚠⚠ **2026-08-22（批 4）起它是代码的后置条件**：模型给章级档位（`CHAPTER_WEIGHTS`），整数由 `novel/stages/stage4/measure.py::allocate_budgets` 归一化 + 钳带算出 ⇒ 生成路上这条判据恒绿。**判据不撤**：`--apply-edit` 的人工稿与 `--revalidate` 的存量产物不经过那个函数，在那两条路上它仍然是真守卫 | `HARD` |
| R-5.3c | **全书**预算之和落在全书 span 合计的 `BOOK_BAND`（0.90~1.10）内。逐章带一放宽，「章章 ±10% ⇒ 全书必然 ±10%」这条蕴含就断了，而总量是对用户的产品承诺。⚠ `unver` 臂比 `R-5.3b` 严：**缺一章就不判**（少一章分子分母同时缺一块，得出的倍率既不能说合格也不能说不合格）。⚠ finding 必须打出**逐章倍率明细**，否则本层那条 0 成本恢复路名存实亡。⚠⚠ **2026-08-22（批 4）起同样是代码的后置条件**（同 `R-5.3b`）：全书总量恒等于全书 span 合计（带的中心 1.0），逐章档位只决定它**怎么分**。定向修复那一路把本章总量钉死在基线合计上，因此也顶不出带外——而从前它可以 | `HARD` |
| R-5.4 | Stage4A 的整书策略 `reconstruction_strategy`、逐章 `chapter_rewrite_brief` 与场景 `purpose` 只说**写什么与怎样组场**，不许用风格套话代替结构决策。**事实词义不是写法指令**：与该字段可引用的 typed fact claim 连续对齐、且 payload 不短于 8 个字母/数字的成对中文引文内的命中不计（`purpose` 只认本场 land/context claim，`chapter_rewrite_brief` 只认本章场景实际引用的 claim，整书策略 session 看不到 fact claim ⇒ `reconstruction_strategy` 无豁免）；无引号、短引号、跨场跨章与同字段第二次无据出现一律照红。A 策略不进入正式计划，Stage2 冻结的 `chapter_function` 只读、不由 Stage4 改写 | `HARD`（`style_words`，A 相与正式复检共用 `novel/stages/stage4/checks.py::style_word_findings`） |
| R-5.5 | 普通 Stage4A 的策略/章响应分别选择精确历史来源，允许多来源及全部采用；同请求不同有效回答关闭式拒绝。选择逐单元持久化，聚合 v2 每行绑定 source run，全部有效来源可走 `--reaggregate` 免费提交。Stage4 历史请求先经通用 run envelope 校验，再由同一请求构造器比较已物化 profile、输入、schema、prompt 和 token ceiling；不同请求可跳过，来源损坏不得吞掉再申请新模型调用。命中后仍完整核 checkpoint、session、ledger 与当前业务 validator。章级请求与恢复共用实际依赖：A 策略只绑定公开章概况及 typed 反馈；A 章绑定策略、自身 weight、公开真相投影与 local→opaque→formal 编译映射，策略 epoch 不携整书 truth 摘要（整书策略审阅主体仍保留完整证据）。B 章绑定同一 `_ChapterBindingIssue` 下发材料、完整歧义候选、局部引用尺及只读邻章边界。整书候选与输入摘要属于提交身份，不污染无关章请求；完整映射、上游与聚合 HARD 仍须复验。Stage4 正式生产是两个**独立公开付费节点/授权**，不是两个逻辑请求：`stage4.design` 仍只有一条公开 CLI dispatch、一次预览/`--yes` 授权，内部固定为 **1 条整书策略请求 + N 条逐章私有 session**；策略验证并 checkpoint 后才扇出逐章请求，逐章按本次冻结的节点、每书和供应商总限制执行（默认 4 并发），每个 unit 独占 provider session、purpose、ledger attempt 与验证后 checkpoint。A 通过机械闸并原子落盘后，必须先提交独立、hash-bound 的正式 `design_review`：全书策略决定为 `accept / request_replan`，逐章决定为 `accept / request_repair`；它只授权下一次 B，绝不打开 s4。只有同一宏观策略 epoch、自身 weight、局部真相与证据均相同的旧 `accept` 才能预填复用，并仍须对当前整书主体原子提交一次；宏观策略或审阅契约变化重开全部受影响义务；单章事实、证据或 alias 的实际依赖变化只使该章过期。整书策略决定绑定与策略 producer 相同的宏观输入投影，完整 review basis 另留审计；同章同主体最近拒绝遮住更早接受，不能跳过拒绝回捞旧票。`request_repair` 必须带关闭式修复码及章内 scene/opaque-alias 证据，CLI 以服务端给出的单一 opaque `--review-token` 现场展开当前决定，只重跑被拒章并复用其余 checkpoint；策略被拒才允许在同一 token 动作上附加 `--replan` 整书重做，裸刷新或把自然语言理由直接发给模型均拒绝。`stage4.plan` 仍是一条独立逻辑调用，只把已审阅接受的完整剧情候选绑定证据并确定性编译为计划；A 完成或审批完成都不得静默连跑 B，批量队列必须重新 plan/preview/confirm 才能得到 B 的付费 argv。A 的策略 prompt 只见同号章 `chapter_function` 与确定性计数，不见 节点/人物 alias；单章 prompt 只见冻结策略、本章去序真相，以短小、稳定、局部且不含源序含义的 opaque alias 引用事实与人物；两类 prompt 均不得出现原文、SourceSpan、`source_beat_ref`、有源序的 beat/stage 标识或材料。传输、内容重采与定向修复仍可能产生额外 wire，必须逐条进 ledger | `结构`（`contracts/invariants.py::NODES` 的 `stage4.design` / `stage4.plan` + `novel/stages/stage4/prompts.py` + `novel/llm/stage4_runs.py` + `novel/orchestrator/approvals.py::approve_design` / `::resolve_design_action` / `::authorize_plan` + `novel/orchestrator/queue.py::plan_book`） |
| R-5.6 | BeatPlacement 是节点到场景的唯一可编辑映射，每节点一份 placement，默认一次 delivery，同节点同场最多一次；场景节点列表从中派生。多场呈现联合完成父节点，不创建子事实。已登记状态、跨节点约束与目标端点必须落到确定场景：单场可派生，分场禁止首次/末次兜底。全部主要呈现完成前不能通过 context_beat_refs 复用完整父正文。普通分场不改上游蓝图；引用/范围/端点检查不能证明联合语义完整。 | `HARD`（`placement_ref` / `goal_landing`） |
| R-5.7 | Stage4A 策略响应只产全书 `reconstruction_strategy` 与完整章权重表；每章响应由请求身份注入章号与冻结权重，只产本章 `retain/reconstruct` 决定、三个 local-alias goal、brief、BeatPlacement、必要 anchor_bindings 与按新披露序排列的非空候选 `scenes[]`，不回显章号、权重或全书策略。代码把 local alias 映回正式稳定 alias，注入 `output_ordinal` / `weight` / `candidate_scene_id` 并按章确定性聚合；只有 N+1 个 unit 全部验证并 checkpoint、聚合后整书 schema/HARD 全通过，才原子写入完整 `blind_candidate.json`，中断、缺章或失败不得写部分正式候选。goal 不存展示 rationale；候选场没有 material/stage refs，不能冒充最终计划 | `HARD`（`empty_chapter` / `budget_int`）+ strict schema（`contracts/schema/stage4.py::BlindBookStrategyResponse` / `::BlindChapterDesignResponse` / `::BlindCandidate`） |
| R-5.8 | 章级 B 消费完整本章已接受设计、必要节点/人物映射、相关状态/约束/舞台及证据闭包，只绑定 delivery 的证据与舞台，不修改场序、placement、指令、目标、端点、权重或设计语义。多个 delivery 可以共享段级证据，不为去重伪造细证据。唯一选择代码完成，真歧义才购买章级请求；concerns 供结构审阅，不自行授权重规划。人工当前 typed binding 仍可零调用提交。 | `结构`（`novel/stages/stage4/plot_binding.py` + `novel/stages/stage4/plan.py`）；自由文本带入源序的风险仍见 `B-06` |
| R-5.8b | B 的实际请求、预览和 checkpoint 身份共用同一章工作包与输入契约，完整别名映射须闭合；不发送不可解引用、其他章完整正文或冗余全书产物。模型回答只处理本章真实歧义，不能把场级局部请求冒充章级审阅。 | `结构`（`novel/stages/stage4/plan.py::_chapter_binding_issue` / `::_chapter_input_sha` + `STAGE4_BINDING_INPUT_CONTRACT`） |
| R-5.9 | 场景详略由章级与场级 `weight` 决定，整数预算由代码分配；“多少场算过多、何种详略最好、哪一种顺序更好”没有 ground truth，不设自动审美阈值 | **`人`**；预算硬门见 `R-5.3b/c`，分布见 `R-5.9b`；旧盲区见 `B-17` |
| R-5.9b | 场景预算与原文舞台 span 的分布、章内轻重必须常驻报出；读数不自动升级为质量结论 | `度量`（`scene_budget_distribution`） |
| R-5.10 | A 免费全部采用明确使用 `BlindCandidateCompiledProvenance(origin=compiled, architecture=book_strategy_adopted_v1)`，只绑定来源行摘要，不现造本次模型身份或复制历史用量。普通 A 混合采用时原模型费用只保留在原 ledger，新产物只认当前 run 完整用量，聚合 adoption 明确关联各 source run/attempt。A 与 B 各只在自己的中间产物记录来源账与 usage；A 的正式 provenance 聚合本 run 下策略/逐章 N+1 个稳定 purpose 为得到最终获采纳 units 发生的完整成本与实际模型集合，不伪装成一次供应商调用：除 accepted checkpoint 的 turn/recovery links 外，同 purpose 先前无 checkpoint 的失败 session 成本也必须计入，且部分恢复、全恢复、定向修复与 aggregate prepared 尾窗重启都要重建出逐字相同的 usage。逐 unit 的 accepted attempt、provider session、request identity 与 usage 仍由 ledger/session audit/checkpoint 精确联结；定向修复另绑定审批决定 SHA、被替换单元与其基线 unit SHA，其余单元只引用既有 checkpoint，不复制冒充新调用。B 的章级路同样不得伪装成一次供应商响应：`architecture=chapter_baseline_v1` 时，机械章记 baseline、歧义章记实际 accepted attempt/model/request 与 replacement lineage；全机械书模型集合及 usage 为空。旧整书模型来源已退役，缺失或不明的章来源保持 unknown；`--apply-binding` 明确记 human provenance，不伪造模型、prompt 或 usage。最终 `plan_chapters.json` 是代码编译产物，只记 `FreeProvenance(origin=compiled)`，同时绑定 A/B 完整 SHA-256 与实际 consumed inputs，**不得复制 B 的模型/usage 冒充计划自身调用**。人工整份替换记 `origin=human`，不得继续冒充旧 A/B 编译结果。问题稿与 0 成本提升稿同样带来源，ghost input 必须显式报出 | `结构`（`BlindCandidateProvenance` / `EvidenceBindingProvenance` / `PlanProvenance` + `novel/llm/stage4_runs.py::design_aggregate_usage` / `::plan_chapter_*` + ledger/session checkpoint + `novel/prov.py::FreshnessReport`） |
| R-5.10b | **写作上下文照的必须是盘上这份计划**：两处各存一份 goals，手改计划而不重建上下文就会分家。四态分开报——对得上 / 照的是另一版 / 没记这条边 / 上下文不在场 | `HARD` |
| R-5.10c | 校验器要为**两份产物各报一行来源**：报告第一节只覆盖被复检的那一份，另一份的来源结论要另有一行常驻（空也要报） | `度量` |
| R-5.10d | `origin=compiled` 的计划在 freshness、Stage4 全判据复检和正式写盘后置条件中，都必须把 provenance 保存的两枚完整 SHA-256 与盘上**当前 A/B 版本化文件的原始字节**逐一比较；缺失或不符即关闭式阻断，不得因 `artifact_payload` / `inputs[].consumed` 的业务投影仍相同而放行。`origin=human` 不声明 A/B 编译身份，跳过这两条 exact 边但仍走普通 provenance freshness | `HARD`（`phase_artifact_link` + `novel/stages/stage4/phase_binding.py::verify_plan_phase_artifacts`） |
| R-5.10e | A/B 完整字节失配不等于一律需要人工：若同一条普通来源边已证明**声明消费投影**变化，freshness 汇总必须保留 `consumed_changed`，让登记 producer 按 canonical 动作重建；只有消费投影仍相同、而未消费字节或格式单独变化时才是 `file_changed`，继续关闭式停在人工决定。两类都仍由 `R-5.10d` 的 exact SHA 闸阻断，不得把“可确定重建”误报为“仍然 fresh” | `结构`（`novel/store.py::_stage4_plan_phase_freshness`）+ 业务投影变化/仅空白变化正反例回归 |
| R-5.10f | Stage4B 输入契约只由 `inspect_binding_input_contract` 解释。仅已发行 `origin=llm`、`architecture=chapter_baseline_v1`、完整逐章来源与精确旧 system 指纹共同证明的缺回执，才是 `stale/binding_input_contract_obsolete`；未知架构、回执或来源形状保持 `unknown/binding_input_contract_unproven`。普通上游 freshness 原因优先保留，compiled Plan 在 exact A/B 边闭合后继承当前 B 的输入契约结论，不伪造消费投影变化。已知过时 B 在当前 A 已正式接受、现有 paid/egress/epoch 授权仍成立且产物可读无 INVALID 时，由原 `stage4.plan` producer 经 canonical queue 重建；未知来源不取得此特许。0 成本 `--apply-binding` 仍可提交同一 typed 业务形状，不能手补历史回执或用 `--revalidate` 冒充重新下发模型输入 。旧整书、缺省架构或不完整章来源不得解码成当前绑定，也不得取得自动重采权限。 | `结构`（`novel/stages/stage4/binding_input_contract.py::inspect_binding_input_contract` + `novel/store.py::_stage4_binding_input_freshness` / `::_stage4_plan_phase_freshness` + `novel/orchestrator/state.py::operator_redo_flags`）；Store/Operator/API 恢复正反例回归 |
| R-5.11 | 编译参数、fact atom 数、源舞台数与结构算法版本均可由当前代码和上游现算，不进入计划或 provenance；结构读数必须现场计算，算法读数变化不得改写计划字节 | `载体`（strict schema + `novel/stages/stage4/measure.py::structure_metrics_of`） |
| R-5.11b | 当前组合为 ChapterBlueprints v10、PlotEdit v2、BlindCandidate v6、BlindChapterEdit v3、EvidenceBinding v6、PlanChapters/PlanEdit v7、WriterContext v10；这些 Actor 契约登记在 workflow manifest v7（`stage2-mechanical-admission-v1`）的 `artifact_versions`，KnowledgeLinks 不再是成员。人物 v2、Global v7 与 Blueprint v10 形状未变不升版；旧业务载体关闭式拒读，不手补产物或复用旧审批。V6 及更早 base 不再迁移，旧输出与成本账原样保留，由冻结旧代码读取；仅登记的 V4→V5、V5→V6 离线过渡仍按冻结来源代码和 CAS 目标显式推进，保留独立 Core、任务、profiles 与累计额度。人物投影只由既有 Characters/MainText 免费重编，任一混合写入窗口按 prepared 目标恢复，第三方字节拒绝覆盖。Gate B→C 保留已闭合 Stage4–5 的全部产物与审批，不重编人物投影，仅拒绝受影响 Stage6/7 残留；离线过渡统一由 R-11.20 的入口处理。Stage5 正文格式不机械升版，请求、检查和恢复绑定新 Context/计划。 | `载体`（`ARTIFACT_SPECS` + `contracts/schema/workflow.py`）+ `novel/orchestrator/workflow_transition.py` / CAS 恢复反例 |
| R-5.12 | 每个完整节点必须有唯一 placement 且覆盖本章全部节点；少数节点可在多个同章场景分段表现，delivery 不成为上游可引用的新剧情主体。联合遗漏、重复和含义改变交既有设计与结构审阅。 | `结构`（`novel/stages/stage4/plot_placements.py::compile_placements`）+ **`人`** |
| R-5.13 | 一场的预算不许被必须逐字写入的原话挤满：低于 2 倍只提示，不自动改场或改事实 | `INFO`（`verbatim_starved`） |
| R-5.14 | 原话占场景预算的分布要无条件报出；阈值没有独立质量真值，读数不作放行门 | `度量`（`verbatim_share`） |
| R-5.15 | 结构变化由同一个逐章函数复算并保留分子、分母和 `unver`。完整节点的源证据区间严格可排序时，比较全部 delivery 的目标位置，逆序与交错分别计数；可比较对为零时比例为 null。节点不是原文场景，证据相邻或重叠不证明同场，因此没有源场景模型时分区、边界和非连续重组保持 `unver`；不能继续使用旧 land-fact pair 分母。映射不闭合时相关读数全部未判 | `度量`（`partition_change` / `disclosure_order` / `noncontiguous_regroup`，唯一数学落点 `novel/stages/stage4/measure.py::structure_metrics_of`） |
| R-5.16 | 跨节点 cause/story_before 表示故事因果与先后，场序表示披露顺序；合法倒叙不自动判错。分场节点使用明确端点，不使用父节点首次/末次出现推断。联合呈现完整、错误归因、因果或认识性质改变及隐含提前披露均需语义审阅。 | `INFO`（`causal_disclosure` / `presentation_semantics`） |
| R-5.17 | 场景 `transition` 必须是关闭式可执行标签，首场必须 `start`；回溯/返回的审美是否合适无 ground truth | `HARD`（`scene_transition`）+ 人工盲读 |
| R-5.18 | 首尾报告首/末目标场景是否包含原文边界节点，显式目标锚点同时供审阅。父节点多场呈现不等于每一场都已披露完整内容，不能由证据覆盖证明具体迁移或质量。已登记因果约束不是全部不可移动语义的全集，重构自由度与无源场景依据的分组指标保持 `unver`，由 Stage4 结构审阅与最终人工验收判断；不以场景数或字面低重合冒充重构 | `度量`（`anchor_migration` / `structure_eligibility`） |
| R-5.19 | 每章必须显式记 `structure_decision`：`retain` 的声明变化为空；`reconstruct` 必须从 `entry_anchor / ending_anchor / fact_grouping / disclosure_order / noncontiguous_regroup` 中声明至少两类。计划只存 mode/reason/declared；`computed_change_kinds` 每次从当前 blueprint 与实际场景现场计算，声明已实现、未实现或出现未声明变化常驻对证，但不回写计划，也**不把“至少两项实际变化”或整书距离写成未经标定的自动美学门**。事实/状态/因果闭合仍由 HARD 守 | `度量`（`structure_alignment` / `semantic_closure_reading`） |
| R-5.20 | Stage4 保留两次相互独立、主体不同的人审，均核对父节点及全部 delivery 的联合语义。第一次是 `R-5.5` 的 A 设计审阅，只决定候选是否可交 B；第二次发生在 B 已绑定并编译计划、机械检查通过之后：服务端给出包含计划业务面、现场结构对证读数及 B `binding_concerns` 的完整结构 workpack 与 opaque token，人只回交 token、actor、总理由以及**恰好覆盖当前全部章**的 `accept / request_replan` 与逐章理由；完整 subject/计划字节/证据 SHA 由服务端重建并持久审计。结构 subject 与 proof 必须包含当前 Blueprint 的完整节点及必要证据；逐章复用绑定本章 Blueprint，有全局 concern 时保守扩大依赖。Plan 字节不变而蓝图证据改变，也必须使旧决定失效；API 展示、工作包、审批与重启读取共用该主体。coverage 是独立人审动作槽，可以发现工作包未预登记的真实语义问题；但 `request_replan` 必须指出具体 scene/beat/goal 的结构损害，INFO、metric、UNVER、`binding_concern`、声明—实算差异及 `allowed_actions=[]` 的行只有披露/定位权，不能单独制造付费动作。全部 accept 才打开 s4；任一 request_replan 只保存决定并保持 s4 关闭，绝不自动启动或付费重规划。其后 operator 发布独立的 Stage4A resolution blocker，人工在 Stage 页面交叉读取 B 的逐章拒绝证据与当前 A 工作包并二选一：若能独立确认具体结构损害，提交新的 current typed A `request_repair / request_replan`；若现有 B 拒绝仅由只读材料支撑且没有具体损害，提交 hash-bound typed stop，安全结束本轮且不写 A/B、不改 gate、不签 token、不调用 Executor。不得重复审或用同一 subject/token 改写 B、以 all-accept 绕过拒绝、把 B 自由文本直接当作 Executor 输入，或为关闭 blocker 发明 A 约束；对上游蓝图的具体疑点直接走 Stage2 蓝图 owner 的编辑或定向修订入口（typed `--apply-edit` / `--chapters`），不再转交独立审阅席位，也不触发额外付费；修订后受影响下游按统一机械准入与 freshness 失效。只有新 A 决定真实落盘后，确定性 Supervisor 才按 opaque token 精确恢复 `stage4.design`。同一 current A/B 业务 subject 的决定一经落盘只允许 exact typed replay；proof 也未变时纯幂等不写，只有完整 artifact provenance 字节变化但业务 subject 与外部 typed payload 逐字段相同时，才可保留旧 history 并追加一条绑定当前 proof 的同语义记录，不能用旧 token 改票。A 的 proof rebound 不改 B 或 s4；B 的 rebound 只按原逐章决定重签 s4/proof。唯一不同 payload 的后续写入，是 current A 已 accept 且 current B 为 request_replan 时另交一份非 all-accept A resolution。`STRUCTURE_REVIEW_CONTRACT` 与 `DESIGN_REVIEW_CONTRACT` 均进入 subject，契约升级使旧决定保留在 history 但自然 expired。通用 `/gate/s4` 必须拒绝；决定同时绑定结构 subject 与当前计划完整字节，业务计划、上游结构证据或 concern 变化后即时 expired；纯 provenance proof 漂移只开放上述 exact rebound。计划的生成、`--revalidate`、`--apply-edit`、`--apply-binding` 均不得顺带生成 Context；只有当前最终结构决定全部接受后，`stage4.context` 才能单独、0 成本落盘。Stage5 的任何付费写作在首个 LLM 调用前必须同时验证当前 s4 结构批准、fresh Context 和独立 writing 授权；0 成本 check/人工编辑/阅读面不得被挡 | **`人`** + `结构`（`contracts/schema/api.py::DesignReviewRequest` / `::StructureApprovalRequest` + `novel/orchestrator/approvals.py::approve_design` / `::approve_structure` / `::authorize_context` + `novel/cli/main.py::_cmd_run`）；没有自动审美阈值 |
| R-5.20b | 有效章级 B 输入回执对应的显式契约身份，同时进入整书结构 subject、逐章决定复用 hash、immutable snapshot、实际可见 workpack 与 Stage4 view；非章模型路径省略该输入回执身份，仍必须绑定现役结构审阅契约及完整 Blueprint 证据。由缺映射旧输入转为完整新输入后，即使 B 业务绑定恰好相同，也必须重新提交当前正式结构审阅，旧 accept/reject 不自动复用或改写。只有可证明实际下发的新输入契约能形成这条身份变化，任意代码、prompt、run 或 provenance 漂移都不能借此重开相同业务裁决 | `结构`（`novel/stages/stage4/measure.py::structure_review_subject_sha256` + `novel/orchestrator/approvals.py::_structure_chapter_subject_sha256` / `::_structure_workpack_snapshot` + `novel/views/stage4.py::stage4_view`）；同结果重建、旧决定保留、未知回执关闭回归 |
| R-5.21 | Stage4 的运行时失败必须携带**闭合式 typed witness**：`novel/llm/typed_failures.py::TypedFailure` 九字段冻结（candidate_sha256 / code / rule / chapter_ordinal / scene_refs / refs / repair_owner / retryable / failure_fingerprint）。发射点 → `repair_owner` 的路由表唯一落在 `contracts/invariants.py::STAGE4_FAILURE_OWNERS`，未登记的发射点关闭式归 `code` owner。`repair_owner ∈ {stage4.design, stage2.blueprint, code}` 的失败在 B 首轮立即停止（不排队反馈、不消耗四轮预算）；B owner 回同一 session；同一 fingerprint 集**连续两轮**未闭合提前终止该 unit；`failure_fingerprint` 是语义身份（不含 candidate SHA），换候选重演的同一违约指纹相等。判据在已知章号的发射点必须给 finding 填 `output_ordinal`（章级判据按章拆 finding），typed witness 只从发射点结构化产出，**禁止从 detail 散文反推章节**。三份出口各取所需：完整反馈只回同一 provider；`_service/llm_audit.jsonl` 的 reject_detail 只追加无正文的 `code/chapter_ordinal/fingerprint` 读数；完整 witness 与被拒响应进现有 `_llm_rejected/<chain>/<attempt>/` 恢复面（`repair_eligible=false`，收口走 `--revalidate` / decision-bound repair / hash-bound replacement，不建第二份审计文件） | `结构`（`contracts/invariants.py::stage4_failure_owner` + `novel/llm/typed_failures.py` + `novel/stages/stage4/checks.py::check(witness=)` + `novel/stages/stage4/plan.py::run_plan` 的关闭式路由与 `::_keep_business_witness`）；`tests/test_stage4_typed_failures.py` 正反例回归查 |
| R-5.22 | Stage4A 候选在授权 B 之前必须经过唯一 **A→formal 零调用预检** （`novel/stages/stage4/plan.py::preflight_binding_candidate`）：从当前 A 与 Stage2 关闭目录构造存在性绑定候选；唯一证据由代码派生，歧义项在存在性验证中使用允许候选集合，复用正式 merge/plan ref validator/WriterContext 组装与同一联合 HARD，返回绑定 candidate/blueprint/review-contract SHA 的 typed pass/blocked 结论，**不落任何新 artifact**。该候选只验证编译与机械约束是否可行，不作为正式 B 绑定，不声称通过语义审阅；失败 owner 由 `R-5.21` 闭合表分类，机械目录缺失的 witness 按当前归属表转交，语义担忧不得伪装为不可行证明。调用点四处：A 审阅 workpack生成、approve 闸（当前契约的 blocked 或 missing 预检拒绝任何 accept；repair/replan 不受闸）、`authorize_plan`（复用现场 subjects 的预检投影）、`prepare_plan_candidate`（任何 driver/probe/provider 副作用前重跑）。旧版不提供预检兼容或 raw 决定生效例外。元数据归一：`computed_change_kinds` 是权威代码读数，`mode/declared_change_kinds` 保留为设计意图只进 `structure_alignment` 度量（metric_only，无 fix 路由、不得生成付费 action）；泛化 `structure_delta` 退役为 `DESIGN_REPAIR_CODES_RETIRED`（历史可解码、新决定 approve 闸拒绝），新修复决定只接受带 scene/alias 的具体修复码；`binding_constraint_unsat` 必须有机械 witness（预检不可行证明或 fresh B infeasible 终态）支撑；`DESIGN_REVIEW_CONTRACT` 使用当前登记版本，旧契约接受不得免费重签；同一语义主体仅验证 seal 变化优先免费复检；v3 split-capability 历史仍可严格解码，但不取得 current authority | `结构`（`novel/stages/stage4/plan.py::preflight_binding_candidate` / `::_preflight_blocked_text` + `novel/orchestrator/approvals.py::_design_preflight_summary` / `::_require_design_preflight_clear` / `::_require_design_codes_active` + `contracts/invariants.py::DESIGN_REVIEW_CONTRACT` / `::LEGACY_V3_DESIGN_REVIEW_CONTRACT` / `::DESIGN_REPAIR_CODES_RETIRED`）；`tests/test_stage4_preflight.py` 与 `tests/test_design_review.py` 正反例回归查 |
| R-5.23 | Stage4B 内部拆为「机械基线 → 歧义章私有 session → 确定性聚合」，公开仍只有一个 `stage4.plan` 节点、一次预览和一次授权，不开放模型 `--chapters`。唯一机械基线落点 `novel/stages/stage4/plot_binding.py::build_mechanical_baseline`（纯函数）：按冻结 placement 为每个 delivery 编译父节点证据和有据舞台候选。唯一结果由代码确定；多证据分场 `delivery_evidence` 与不同舞台签名 `source_context_selection` 才生成内容寻址 ambiguity id。多个 delivery 可共用同一段证据，不能为消除重叠伪造子证据。机械章 **0 wire**（绑定由冻结输入确定性重算，不落章级 checkpoint——其价值是免重付模型调用，确定性重算零成本）；歧义章各占一条 single-session run/checkpoint 链（`stage4_runs.plan_chapter_run_spec`，`_service/runs/<sid>/stage4/<run>/plan/chapters/chNNNN/`；旧书内 `_stage4_runs/` 已退役，当前恢复与历史采用只消费 service 根），真实 worker 池默认 `STAGE4_PLAN_MAX_CONCURRENCY=4` 并发，仅显式实验配置允许 6/8，仍受每书及供应商总槽位限制，任何两章不共享 driver/logical/provider session；章 prompt 包含完整本章已接受设计、主体映射及所引用状态、约束、知识、舞台与证据闭包；必要外章证据只按真实依赖回取。原文不重复嵌入对象。B 不改 A 的顺序、呈现、目标和端点；模型只返回 `ChapterBindingResponse`（歧义逐项闭合 + 非阻断 concerns，无章号/hash/完整绑定面）。每个 worker 内按原 wire validator 完成 parse/require/schema/本章闭合，通过即 checkpoint；后章失败不得抹掉已验章，恢复只补未闭合 unit。全章闭合后按章号确定性聚合并过同一 compiler 与整书联合 HARD；typed 失败按 `R-5.21` 路由，B owner 且带章号只回该章 session；重问通过后必须同步重写 checkpoint、accepted attempt/model/request、unit lineage 与 aggregate anchor，不能让正式稿指向旧响应；同一 fingerprint 两轮早停。时间层冲突模型无法给出可执行选择时不采用任意 fallback，保留他章 checkpoint 转人工 typed 替换。该 replacement 必须绑定原 run/request/code/subject 与被拒 candidate SHA，消费一次性封存 resolution，复用同一 `ChapterBindingResponse` validator、compiler 与全书 HARD，0 wire 且不得伪造模型 provenance；整份 `--apply-binding` 仍是另一条明确人工入口。聚合拥有独立 deterministic commit 身份，完整绑定机械输入、历史采用与新生成各章来源；全部采用和混合来源均允许免费提交，不要求本轮至少一个模型 checkpoint，不得伪铸 provider acceptance。当前来源账只接受显式 `architecture=chapter_baseline_v1` 与完整覆盖章集的 `unit_lineage`（kind 只取 baseline/chapter_session + attempt；人工 replacement 以同一 chapter_session 行的 resolution/candidate/audit 字段留证，不伪造第三种模型 unit）；全机械书落 `origin=compiled` 免费账（0 模型声明）；只读取当前 `EvidenceBinding` v4；旧整书单发 checkpoint 恢复入口退役，当前 B 只通过逐章池与确定性聚合生成。整书 run 元数据仅用于冻结配置和聚合身份，不能据此创建旧模型工作单元 | `结构`（`baseline.py` + `novel/llm/stage4_runs.py::plan_chapter_run_spec` + `novel/orchestrator/node_agents.py::Stage4PlanSessionPool` + `novel/stages/stage4/plan.py::_run_plan_with_chapters` / `::_finalize_mechanical_plan` / unit replacement）；`tests/test_stage4_baseline.py`、`::test_stage4_plan_sessions.py`、`::test_stage4_plan_chapters.py` 正反例回归查 |
| R-5.24 | Stage4B 机械基线发现同一候选场的 source facts 对应多个不同 `time_layer/place/present` 签名时，必须为每个 `stage_signature_conflict` 常驻报出章、场、事实与全部候选签名；最终 binding 选中一个候选也不能让其从报告消失。该读数没有自动优劣阈值，只作为结构人审的完整披露面，不阻断编译、不生成付费修复动作 | `INFO`（`novel/stages/stage4/checks.py::check`） |
| R-5.25 | Stage4 两次人审各有一份只读、异常优先的人审工作包 v1；公开面固定为 `GET /api/novels/:sid/approvals/design/workpack`、`GET /api/novels/:sid/approvals/structure/workpack` 与 0 成本 CLI `approval workpack design|structure`。工作包必须绑定当前 candidate/完整审阅 subject 与精确章全集；服务端复用生产 A→formal preflight、Stage4 check/metric（含 `R-5.24` 的完整 INFO）、机械 baseline、binding concerns 及审批历史投影，不在 façade 重拼判据，不预填决定、不自动 accept、不启动模型或作业。六组以 `blocking → semantic_attention → disclosure → metric_only → phase_deferred → coverage` 固定全集顺序出线，空组也保留并给扫描分母；coverage 恰覆盖全章、changed 优先且各段章号升序，changed 与 exact historical accept reuse 互斥并穷尽。条目携闭集 `sev/status/effect/allowed_actions`：actions 只可为 `accept / request_repair / request_replan`；INFO、metric、UNVER、skipped 及非 blocking/coverage effect 恒无动作，不能借工作包制造付费返工理由。Design 面突出 preflight witness、首尾锚点、因果/时间与 repair 章；Structure 面完整投影 B concerns、非线性 UNVER、所有 stage signature conflicts 和结构读数。Context 才能判的 `R-6` 规则只进 `phase_deferred(stage4.context)`，不进入结构 subject。Inbox 同步发布 Stage4 A/B 审批余额；已被 current hash-bound 结构决定实际覆盖的 disclosure 保留为 `reviewed_retained` 并附 typed decision 证据，不洗成 pending，也不从审计面消失。Stage4/6/7 工作包 disposition 均为 `review_required / already_current`；旧 frozen-v6 工作包与恢复入口已退役，版本不匹配明确拒绝。盘上 raw typed 决定不因前置闸暂时失效而删除；免费复检后 subject/contract 未变可恢复 `already_current`，不同 actor/reason/typed 决定不得自动合并。Design 与 Structure POST 均只收各自 workpack 的 opaque `review_token`、actor/reason 与 typed rulings；candidate/aggregate/chapter/contract/validation 等完整 SHA 由服务端在同书锁内重建并写审计，调用方不再搬运。Stage4A 完整章 edit 的独立单 token 见 `R-5.26`。每个 coverage 行携当前确定性 `reading`：B 包含本章完整蓝图、源文及实际依赖闭包、正式 placement/全部 deliveries/明确端点、结构读数与 binding concerns；A 包含无源序的完整 PlannerTruthView 与设计骨架。正文只在唯一目录出现，场景和问题以引用定位；顶层仅保存一次 `legend` 与全书必要信息；`metric_only` 只保留一行摘要且 `refs=[]`，完整读数以逐章 reading 承载。reading 不进 subject/token/proof，纯增渲染不使既有决定过期；人工对待审章只读 reading，不需要也不得读取盘上产物。普通 A/B 模型传输使用同一冻结全包的 `stage4-review-obligations-v1` 投影，只下发待审章、全局必要上下文及继承摘要；完整包仍供审计。typed ruling 的 `source_decision_sha256` 明确绑定同义务、相同理由与有效原接受；原 actor/理由沿不可变历史链保留，最新否决遮住更早接受，来源损坏保持 unknown。loader 固定继承行，服务在审批锁内核验“新决定 ∪ 有效继承＝当前完整义务”；没有逐章证据快照的旧票不得用于局部继承。待审为空由 operator 发布所属 A/B 节点的 `--close-review`，零模型闭合，不创建审阅席位；A 汇合记 `reviewer_kind=inherited`。恢复或重放只补当前 proof 和审批尾部，不能新造接受 | `结构`（`novel/stages/stage4/reading.py::structure_chapter_reading` / `::design_chapter_reading` + `contracts/schema/api.py::ApprovalWorkpack` + `novel/orchestrator/approvals.py::design_workpack` / `::structure_workpack` + `novel/api/routes/approvals.py` + `novel/cli/main.py::_cmd_approval` + `novel/orchestrator/inbox_projection.py::inbox_view`）；API/CLI/schema/投影/Inbox 正反例与前端 seams 查 |
| R-5.25b | RunSupervisor 为 Stage4 审批组包时必须在**一个 Python 进程、同一份 operator overview/code 与一次调用栈只读 memo**内完成，首尾以 active surface seal 做 CAS；不得为 design workpack、template、structure workpack 与 approval show 串行启动多棵 CLI 并各自重扫仓库/书面。B→A 回审给人的阅读面固定为“完整当前 A + 当前 B typed 决定 + 仅 request_replan 章/全局项的 B delta”；完整 B workpack 仍以 canonical SHA-256 绑定业务身份和审计。人工回交的策略/章/约束集合先作规范排序再进 typed schema，重复项仍必须拒绝，不能用排序顺手去重或改写引用顺序。B→A 回审 delta 中被点名章保留完整 coverage `reading`；A 工作包的 reading 由内存中的 subject 直接渲染，B 工作包的 reading 与 plan/binding 同一次快照读出当前蓝图，不另开读路 | `结构`（`novel/orchestrator/approvals.py::design_workpack` / `::structure_workpack` / `::read_projection`）+ 单进程/双 seal/delta/重复拒绝回归 |
| R-5.26 | Stage4A 返修的 current 契约固定为 `stage4-design-review-v5`；v5 的策略审阅绑定与 producer 相同的宏观输入投影，局部事实变化只重开受影响章的义务。v3/v4 仅作历史严格解码、不再取得 current authority。每轮 Executor 必须同时收到本轮具体 finding 与此前全部尚未关闭的 typed constraints，关系约束与 Stage2 的 `story_before / response_to / same_scene / observes` 同构；自由文本 reason 只进审计，不能混入 prompt。每份 hash-bound repair/replan 动作都是 producer 边界：RunSupervisor 在执行 `recovery:stage4-design-action` 前按 exact action 身份只滚一次 `reason=continue` 的 fresh execution epoch，重读 operator next/CAS 后才启动 Executor，不重复调用 Reviewer；修前 aggregate producer 与修订 producer 不得相同。同一工作单元跨 repair epoch 共用基础四次模型内容额度，另可经 R-11.20 显式人工追加；耗尽后不得借新 epoch 返还预算，可以停止或以 `BlindChapterEditRequest` v1 对当前候选中的一章做完整响应替换，绝不自动升级为全书 replan。人工计划必须先 preview 后 apply，两步共用同一 compiler、schema、动态 validator 与全书 HARD；public request 只含 opaque `s4er1_…` review token、actor/reason、目标章号与完整章响应，服务端由 token 现场重建并核对 current candidate、aggregate/chapter subject、design decision、strategy epoch/chapter strategy 与 constraint set。只改目标章，非目标章业务字节恒等，provenance 明记 `origin=human`，不伪造 prompt/model/usage，也不新增正式展示 artifact。真正策略驳回才允许 replan；新策略请求必须携累计策略/章节失败和旧 accepted 章约束，只有 effective strategy/input digest 改变的章节才可重发 wire | `结构`（`contracts/schema/api.py::DesignRepairConstraint` + `contracts/schema/stage4.py::BlindChapterEditRequest` + `novel/orchestrator/agent_supervisor.py` + `novel/stages/stage4/plan.py` / `novel/stages/stage4/design_evolution.py` 的 accumulated constraints、repair epoch、preview/apply 与 effective-input checkpoint 复用）+ 新 producer epoch 重读 exact action、第二轮继承首轮约束、零 wire 人工替换、非目标章字节恒等、真策略 replan 反例回归 |
| R-5.26d | Stage4A 未审模板不得向真实模型输入预填 repair/replan/accept 判断；合法示例与真实历史决定必须按来源分开。每次初审、候选改变后的复审与 B→A resolution 都携本 strategy epoch 的逐章有效约束目录及来源/集合摘要。Reviewer 通过 typed `constraint_operations` 对每个 active SHA 明确 keep、withdraw 或 supersede，代码只验证引用、当前集合与完整覆盖并折叠结果；自由 reason 不进入 Executor，不得自动推测反向含义。旧 `scene_goal` 保持必须覆盖，新 `scene_goal_exclude` 只禁止把所指事实断言成指定场景的当前目标或已发生事件，不删除事实或移动落点。执行只消费正式决定折叠后的非空有效集合；旧 history、proof、committed rounds 与席位预算保持，不以协议变化清空旧义务或重开预算。历史 current repair 继续按原授权执行，候选改变后由正式复审追加更正；同主体改票限制保持 | `结构`（`contracts/schema/api.py::DesignConstraintOperations` + `novel/orchestrator/approvals.py` 的历史折叠与审批边界 + `novel/llm/stage4_runs.py` / `novel/stages/stage4/design_evolution.py` 的真实模型输入 + `novel/stages/stage4/plan.py` 的有效约束消费）；模板污染/方向相反/逐条更正/存量重放与预算身份回归 |
| R-5.26b | Stage4A typed targeted repair 在执行前必先进入 fresh producer execution epoch；代码 漂移同样不得把旧 provider session 当 live resume。新 producer run 可在首个新 wire 前采用另一个 ended run 的**只读已验章响应**。仅限本次 repair 目标章，不含策略 unit、普通首轮或 strategy replan；source 与 target 的 resolved profile、完整请求、输入、schema、token ceiling、恢复协议必须逐字段相等，source checkpoint/session/attempt/ledger 与当前动态 validator/业务状态必须全部闭合。只在恰有一个历史 run 提供唯一最大合法章集时写 immutable `design/historical-checkpoint-adoption.json`；零个或并列来源都不猜。采用不复制 revision、不注册或续接旧 provider session；正式 `DesignUnitLineage.origin=historical_checkpoint` 同时绑定 source attempt 与 manifest SHA，aggregate usage 按 source/target run 分段精确合并，manifest/source/target 任一字节漂移都关闭式拒绝 | `结构`（`novel/llm/stage4_runs.py::restore_or_select_design_historical_adoption` / `::design_repair_sources_usage` + `novel/stages/stage4/plan.py::_run_design_repair`）+ 同 parent 动作边界、跨 code 零 wire、profile 漂移拒绝、无 revision 复制与账面联结回归 |
| R-5.26c | Stage4A repair/replan aggregate 不得把 Python 进程或原 producer 存活当作提交原子性。任一正式候选变更前先在 source run 写自哈希 `design-repair-aggregate` v3 prepared journal，冻结 source/producer-parent/code、repair|replan scope、parent/decision/targets、完整 unit source、候选 baseline/target、整份 typed provenance 与 finalization。source execution epoch 结束后只能由新 recovery run 执行 FREE/0-wire `stage4.design --recover-repair-aggregate <source_run_id> --source-journal-sha256 <raw_sha> --source-commit-sha256 <prepared_self_sha>`：不解析已 stale review token，不构造 profile/client/session/checkpoint/wire，不恢复旧 provider。operator 在 approval/inbox/付费 planner 前只接受全书物理根下唯一可验 prepared；service/legacy 双副本、多份或任一其他 Stage4 prepared 都关闭。Stage2 与 Stage4 两类 prepared 同时存在也是不可能双所有权，不得按顺序猜 winner。恢复在任一候选写前必须证明 source Agent epoch 已 `drained` 关闭且 parent ended（direct CLI 只允许 producer-parent=source），并持有 owner-stopped lease；逐格复验 source checkpoint/session/attempt/ledger/code/request，用当前 compiler 从响应重建候选，并要求重建后的 usage、model、prompts、inputs、root/truth、unit lineage 与 adoption provenance 连同业务体逐字等于 sealed typed payload，再过当前 truth/input/schema/HARD。live candidate 只允许 journal baseline 或 target；第三态拒绝。候选写前先对 source adoption、source-stable canonical recovery relation 与全局唯一的 per-target run/parent attempt relation 作 0-or-exact preclaim；帐尾非 LF、多行或漂移都不得先改候选。baseline 才写 sealed target，target 只验证；幂等补 adoption/recovery audit 后最后原位将 source journal 提为 committed。再杀于 prepare/候选/adoption/audit/mark 任一窗口都必须由下一新 run 零 wire 收敛；已 committed 的下游读取仍逐字要求 adoption、canonical relation 与 target attempt audit，不得删账后继续消费 | `结构`（`novel/llm/stage4_runs.py::inspect_prepared_design_repair_aggregate_recovery` / `::preclaim_design_repair_aggregate_recovery` / `::mark_design_repair_aggregate_committed` + `novel/stages/stage4/plan.py::run_design_repair_aggregate_recovery` + `novel/orchestrator/operator.py` / `execution_permit.py` + `novel/cli/main.py::_run_stage4`）+ repair/replan×baseline/target、物理双副本/多 prepared/第三态、source ended/ledger/session、完整 provenance 重建漂移、无 LF、adoption/audit 唯一与各 hard-kill 写点回归 |

字段、章级输入、采用及提交协议见 `docs/schema/stage4.md`。无关章节不因整书摘要变化而重购，
实际证据或邻章边界变化则必须失效；全部采用、混合来源及 prepared 补尾不能依赖本轮模型 commit anchor。
反例见 `tests/test_stage4_plan_chapters.py`、`tests/test_stage4_design_adoption.py` 与 §9.3 的多分支 HARD 反例。

### R-6 · 写作上下文的确定性组装（stage4 · 0 成本）

写作材料由代码按已批准计划及其引用组装，精确字段见 `docs/schema/stage4.md`。

| # | 要求 | 谁在查 |
|---|---|---|
| R-6.1 | WriterContext v10 保存一份章级剧情目录，beat.content 只出现一次；场景通过 placement 引用节点及呈现指令，附舞台、必要条件与端点。代码从当前计划及蓝图同源编译，禁止中间模型转抄正文或维护另一套可编辑场景节点映射。 | `HARD`（`ctx_plot_exact`） |
| R-6.1a | Context 是最终计划审批后的独立 0 成本产物：`stage4.context` 必须先核当前完整结构决定全章 accept，B 编译与所有计划修复入口都不得提前或顺带落它 | `结构`（`novel/orchestrator/approvals.py::authorize_context` + `novel/cli/main.py::_run_stage4`） |
| R-6.2 | Writer 只接收有据表面身份与独立 opaque actor_ref；不下发反向 char_id 映射、共同实体分组或可关联身份的原关系 ID。canonical 不自动成为称呼，隐蔽呈现只使用获准公开 State 的 role。多个呈现同场保持两个 token；称谓与呈现许可只由当前 Characters 登记闭集与称名投影派生，前章 Context 同样重新投影，不能借历史上下文反查未揭示身份。Stage5 私有全书姓名检测从独立 Characters 与 MainText 重建，不把该后台名表交写手。 | `HARD`（`ctx_name_attested` 核验下发称谓有据）+ 编译隔离（`novel/stages/stage4/actors.py` / `plot_context.py` + `novel/stages/stage5/review.py`）+ 最终 Writer 请求反例 |
| R-6.3 | 世界成立、人物信念/声称及读者揭示的区分只由 Global 四类长期知识与章级揭示字段（`revealed_from` 等）表达，作为未审参考进入消费投影；同章披露节点、逐场知识许可与人物知情事件已退役。PlannerTruthView 见设计所需真相；WriterVisibleView 提供本章可下发内容与逐场状态许可。 | `结构`（`novel/stages/stage4/plot_context.py` / `plot_placements.py`） |
| R-6.4 | 每项 delivery 带有据舞台且时层、地点、在场者保留未知与合法空值差别；状态许可与明确端点、完成位置一致。舞台可执行性及自由文本是否遵守许可仍交语义审阅。 | `HARD`（`ctx_permission_exact`）+ **`人`** |
| R-6.5 | 时代背景与世界观随当前 Global 投影按必要依赖下发；缺失、过期、未决投影拒绝消费，不回退字面匹配或缺省首章。不再有独立的知识关联准入。 | `结构`（`novel/stages/stage4/plot_context.py::build_writer_context`） |
| R-6.6 | 身份、状态、关系、规则及设定按当前必要依赖和知识许可投影；effective_from/effective_until 与 revealed_from 独立，事实结束后仍可作为历史事实披露。关系端点只来自 BP/Plan 的实际 relation use，不从默认 char_id 猜呈现，也不为挂引用伪造 State；规则引用锁定具体版本。同义 secret.fact_ref 只展开唯一明确目标。禁止字符串裁剪秘密或新增平行 visible_content；自由文本提前揭示由设计、结构与成稿核验。 | `结构`（`novel/stages/stage4/plot_context.py` / `actors.py`） |
| R-6.7 | 场景披露的语义质量仍需审阅：机械投影只证明使用了当前 Global 投影与状态许可，不能判断改写式设定是否泄底，也不能证明成稿遵守了许可 | **无守卫 → `B-16`** |
| R-6.8 | Stage5 可发明不改变状态的动作、走位、即时反应、对白措辞、感官/心理/调度与非持久细节；不得新增因果、决定、结果、关系、知识、往事、秘密、规则或会被后文引用的人/地/物/事。确定性层只覆盖新数字与新称谓探针，其余仍无语义真值 | 探针见 `R-7.5`；其余 **无守卫 → `B-01`** ★ |
| R-6.9 | Context 不重复完整剧情正文、全书策略或源文坐标；章级目录与逐场呈现共同构成唯一写作材料。Global 投影的表达许可不在 State 再存一份时间线，约束亦不保存第二份剧情正文。 | `载体`（`contracts/schema/plot_context.py`） |
| R-6.10 | **能 0 成本现算的不落进这份上下文**：书名 / 书 id / 拍密度 / 全书名称检测基线 / 未揭示秘密 id 集都从当前上游现场重建，存第二份只会制造漂移 | `载体`（同上 + `novel/stages/stage5/review.py::load_book`） |

组装器存在不证明材料已送达，`R-6.1`～`R-6.4` 保留实际输出的后置检查。
字面子串未命中不能当作语义未泄露；已知真泄漏也可能零命中，盲区继续由 `B-16` 明示。

### R-7 · 逐章写作（stage5 · canonical 闭卷）

首写、普通修订与 Reviewer rewrite 共用紧凑材料投影，仍输出整章。

| # | 要求 | 谁在查 |
|---|---|---|
| R-7.1 | 全书只有一条 canonical **闭卷**写作路：写手收到写作上下文与保真材料，源章原文只供确定性校验、绝不进入 prompt；每章正式产物唯一落 `stage5/chNN.json`，正文由 producer-owned `segments` 按计划 slot 顺序确定性拼出 | `结构`（`novel/stages/stage5/prompts.py::build_material` / `write_system` + `novel/stages/stage5/write.py::_rel` / `_ordered_segments`） |
| R-7.2 | 人只批准「按当前 Stage1 正文与 Stage4 计划启动全书闭卷写作」，不再逐章选择写作臂。授权落 `_service/approvals/<sid>.json` 的 `writing/writing_proof`，不是小说产物、不进 provenance，且不能替代 `R-5.20` 的当前 typed s4 结构批准；任何 Stage5 付费写作在首个 LLM 调用前必须两证齐全。默认按全书计划补齐所有缺章，已有章不重写，`--chapters` 才明确指定要重写的章 | `结构`（`novel/orchestrator/approvals.py::begin_writing` / `authorize_writing` + Stage5 付费最低边界 + `novel/stages/stage5/write.py::_chapters_of`）+ **`人`**（是否授权本轮付费写作，见 `B-19`） |
| R-7.2b | 目标章在一个并发池内逐章起跑，硬上限 32；一章失败不取消其他已经起跑的章，结果仍按阅读序落盘和报告。`ResponseRepairRequired` 是该章停在下一采之前，不是全批网络故障：不得取消尚待起跑的其他章，同批成功章先落各自 canonical，再把修复终态交给外层 agent。40 章时同时在途峰值只能是 32；不再生成全书 Markdown 投影 | `结构`（`novel/stages/stage5/write.py::_run_group_parallel`）+ 并发、失败隔离、修复暂停与顺序回归测试 |
| R-7.3 | 普通对白允许改换措辞，保留说话关系、语义、话语作用和 父节点的关键剧情；语义等价保持 dialogue_semantics_unver，交受托或人工核验，不因普通对白原句缺失触发付费返修。逐字 HARD 仅校验有剧情依据的密码、口令、谜面谜底与识别符号，连标点、大小写和数字也按原字节保留。Agent 缺失反馈必须精确回指当前已下发字面例外，只有原串完整包含于不可变 given 的缺失片段可进入 required_materials；持久审计只留规则与数量，不能反馈未下发原文 | `人` + `闸门`（`novel/stages/stage5/checks.py::fidelity_findings` + `novel/stages/stage5/write.py::_chapter_feedback` + `novel/agents/session_client.py::SessionAgentClient.queue_code_validation_feedback`）；原话回写、已下发精确反馈、闭卷边界与审计隔离回归查 |
| R-7.3b | 字面例外被提示语切开或改动一两个字时可单列诊断，但不能因此放行不同的密码、口令或识别符号；必须同时保留 verbatim_missing HARD。普通对白由语义契约约束，不进入原句比较 | `闸门` + `INFO` |
| R-7.4 | 必须保住的硬数字，**按数值比不按字面比**：下发面「30岁」、成稿「三十岁」= 满足 | `闸门` |
| R-7.5 | 发明越界的**两个探针**：新数字（下发面以外的「数字+单位」逐个列出）· 新人物（只抓「本书已有的名字用字 + 称谓后缀」造出来的人，召回有缺口） | `INFO` |
| R-7.5b | 发明边界按**是否产生契约外持久状态变化**判，不按“原文没写的动作一律禁止”判。新因果/决定/结果/关系/知识/往事/秘密/规则及可被后文引用的新人物地点道具事件，确定性层一个可靠探针都没有 | **无守卫 → `B-01`** ★ |
| R-7.6 | 成稿与源章的重合度用 20 字窗口逐章计算，**扣掉契约要求逐字保留的部分**；canonical 闭卷上界为 `SOURCE_OVERLAP_MAX`（10%）。它用于暴露原文意外进入下发面、缓存或落盘链的异常，不是文风质量线 | `INFO`（`source_overlap_high`） |
| R-7.6b | **历史关闭说明**：旧开卷路径逐章重合跨度 0.9%~96.8%，没有可标定的一刀切阈值；该无守卫面没有靠发明阈值“修好”，而是随双路径执行能力整体退役。旧盘面仍按原字节保留但为 `legacy_incompatible`，不能作为现役章稿读取 | `结构`（`novel/stages/stage5/measure.py::legacy_stage5_paths` / `canonical_chapter_paths`；历史缺口见 `B-03`） |
| R-7.6c | 现役章稿不携带写作路径标记，故不再产历史路径分报；重合度由 `overlap_report` 逐章无条件报出，且只有 `source_overlap_high` 这一条提示级结论 | `结构`（`novel/stages/stage5/checks.py::fidelity_findings`） |
| R-7.7 | 机械层归一（标点 / 引号体 / 分段）由代码做，**归一改了多少进校验报告一行**（不落产物）；**引号配对异常留着报、不静默修** | `INFO`（`quote_unbalanced`） |
| R-7.8 | 篇幅：成稿字数落在**章预算 `DRAFT_BAND`（±20%）**、全书落在 **`BOOK_BAND`（±10%）**，**两个数不同且都没有第二层宽限**（逐章放宽给单章节奏留余量，全书总量是产品承诺，逐章正负偏差在书级抵消）；超限**由阻断降为提示** | `INFO` |
| R-7.8b | 源章 / 成稿的**直接引语数与比值逐章无条件报出**；现役没有书级 `dialogue_report`，不得把逐章 producer 的读数宣传成另一条不存在的聚合 | `度量`（`dialogue_report`） |
| R-7.9 | 按给定的叙述视角写，但只在确定性可判域内给结论。**人称轴**：`narration_person_report` 逐章常驻报告声明人称、锚点、自称命中与未判原因；只有「单 narration + 全章边界 + first/third + 非空锚」进入弱信号 `narration_person_drift`，且只作人工线索。**章尾轴**：`end_hook_surface_report` 只盘点姓名/数字原子落在末 slot、其他 slot 或整章未见；代词化、换说法、同名与撞值都让字面位置无法推出语义交付，所以每个有稿章节保留 `end_hook_unver`，绝不据字面探针指挥付费改稿 | `度量`（`narration_person_report` / `end_hook_surface_report`）+ `INFO`（`narration_person_drift`）+ `unver`（`end_hook_unver`）。<br>⚠ 章尾末 slot 由 Stage4 新 `scenes[-1]` 给出；两轴都没有 ground truth，故不设 HARD |
| R-7.9b | 不把潜台词说破、换叙述人时声音也要换，仍没有确定性真值；历史开卷路径的表层修饰与整段照抄约束已随该能力退役 | **无守卫 → `B-10`** |
| R-7.10 | Stage4 新场景的 slot 顺序就是落笔顺序；**节点目录数组没有预设写作顺序含义**，写手按 purpose/turn/呈现指令 自由调度，不做“事件倒序”机械判定 | `结构`（`contracts/schema/stage5.py::ChapterResponse` 闭合 slot；`novel/stages/stage5/prompts.py::build_material` 渲染场景任务）；旧盲区 `B-02` 已随源序事件骨架退休 |
| R-7.11 | 「必须成立、不必写在纸面上」那一组，按定义查不了文面 | **`人`**，见 `B-04` |
| R-7.12 | 篇幅超上限时不许由模型**自动**裁剪：`length_over` 只会零调用保留 pretrim、落 current `TrimAudit` 并转人工，不能自行生成、挑选或采用较短候选。付费生成、重采、人工整章写回、Reviewer 明确授权的单章 LLM 重写、`--revalidate` 与 `--recheck` 的 canonical 提交共用同一个 trim 对账口；新 `.INVALID` / rejected 未替换旧 canonical 时不得改动旧稿的 audit/resolution，正文未变且人工决定仍匹配时必须保留 | `结构`（`novel/stages/stage5/write.py` 的 canonical 提交与 trim 同步函数） |
| R-7.13 | 超上限章的最终处置权属于人：可以保留 pretrim、提交人工完整章，或让受托 Reviewer 对**有序非空章子集**提交关闭式 LLM 改写请求。同一 fresh Review 下仅有多章 trim 待销账时，一席 Reviewer 必须在三类互斥出口中择一：覆盖全部 current unresolved 项的 typed retain full-batch、逐章独立指令的 typed rewrite batch request、或 typed stop；Supervisor 不得逐章代选、替人把自由理由变成 prompt，或为同一证据分别购买 retain/rewrite 席位 | `人`：`novel/stages/stage5/trim_resolution.py::retain_pretrim` / `::retain_pretrim_batch` 写 hash-bound resolution（0 次调用），`stage5.write --apply-edit` 落人工新轮，或按 `R-7.31` 先落正式 Reviewer rewrite request 再执行唯一 paid writer。typed stop 不落 resolution/request。⚠ `TrimResolution.action` 仍只有 `retain_pretrim`；系统不会替人采用一个预先生成的较短候选（`B-11` 仍关闭） |
| R-7.14 | **历史关闭说明**：旧开卷 system prompt 对「现场 / 回忆 / 转述」解释不足；实验补强在三本真实小说中没有稳定净收益，随后整条开卷能力退役。现役单一路径使用一份闭卷 system prompt，因此该差异面已结构性消失 | `结构`（`novel/stages/stage5/prompts.py::write_system`；历史缺口见 `B-13`） |
| R-7.15 | 成稿必须是**完整的一章正文**：不是空的或残的（内容字 < 50 早退，其余判定一条都不跑）· 结尾是完整句 · 正文里不含章节标题 / 编号 / 任务术语这类叙述外壳 | `HARD` |
| R-7.16 | 必须**用下发的那张称谓表**称呼人物：本章相关人物一个名字都没出现是漏写，不是简洁。⚠ 它与 `R-7.5` 是一对反向条款——那一条只管「不许编新的」，没管「要用给的」 | `INFO` |
| R-7.17 | 【必须在场】的物件要在成稿里**留下痕迹**（怎么描写随写手）——四组保真材料里的第三组，另两组是 `R-7.3` / `R-7.4`，第四组是 `R-7.11` | `INFO` |
| R-7.18 | **不许提前点破本章还不该揭示的事**（字面探针那一半）。⚠ 报绿灯时**同一行必须打盲区计数**：实测 21 条秘密里 19 条的字面探针连它自己的揭示章都命中不了，一本书盲区 100% 时要落「查不了」而不是沉默 | `HARD` |
| R-7.18b | **书级改写式泄底的守卫已于 2026-08-17 转授**（用户裁决）。原形态是 `stage5.semantic` 的两相付费语义审判（`semantic_secret_leak`），节点与判据一并下线。裁决依据是实跑证据：本项目的重构**不跨章重组**（实测 18/18 章 `from_source_ordinals == [自身]`），书级泄底的主风险面在计划形态上几乎不存在；而该节点占单本实付调用 54%、输出 token 55%，同一本书上 18 章里 5 章命题抽取返回 uncertain、8 条 secret 全部判不动 | 见 `R-3.7d` |
| R-7.18c | **语义审判的覆盖闭合条款随 R-7.18b 一并转授**。原形态是 `semantic_secret_coverage`：覆盖不确定时只能 `UNVER`、不得洗成通过。⚠ 该条款的**精神**仍然有效并落在别处：stage2 的后见之明判据是确定性的（不存在"模型没把握"这一态），交付前由成书逐字对账第 8 项把上游 info 逐条投影到成书章序复核 | 见 `R-3.7d` |
| R-7.19 | **历史关闭说明**：旧开卷路径曾以 `dialogue_halved` 阻断「计划无净压缩却把直接引语腰斩」；该判据只在已退役路径上有前件，现役注册表与执行路径一并删除，不留一条永远零分母的假绿灯 | `结构`（现役注册表 `contracts/judges/stage5.py`；历史风险见 `B-03`） |
| R-7.20 | 直接引语的**条数 / 引号对白体 / 对白占比**要贴着源章与本书——canonical 闭卷写作在历史六本书上都显示把台词压成转述的方向性效应，因此当前只以逐章 `dialogue_report` 给分母、三条 INFO 留线索，不自动补台词 | `INFO` |
| R-7.21 | 成稿要贴着机械基线：`R-4.3b` 的正文精确投影与 Stage5 从源文现算的句段形态档位 · 段均字数 · 提示语排版惯例 · 明喻密度 ·「心里」密度 · 破折号密度。这里不消费 `R-4` 的定性画像 | `INFO` |
| R-7.21b | 破折号密度与连续短段成串**无条件报出读数**——它们是上一条的基线，只在漂了的时候才说得出来就没有基线可言 | `度量` |
| R-7.22 | 每个 `required_fact` 都要在所属新场景留下落点；只用专名/数字作确定性表面探针，**提不出探针的事实显式 UNVER**，不许算作通过；不再检查 fact 数组的源文先后 | `INFO`（`fact_landing`）+ `unver`（`fact_landing_unver`） |
| R-7.22b | 完整剧情节点的姓名、数值探针以及已登记字面、数值和必须出现约束，回查**源章原文**；未找到时交上游内容或名字投影核查，账记上游不记写手。词面命中不证明剧情完整或归因正确；沿用诊断 ID 不代表消费旧事实表 | `INFO`（`fact_atom_unattested` / `material_anchor_unattested`，`owner=stage2`） |
| R-7.22c | **守卫自陈排除掉的样本，要说清它们此后没有被判过**：`noatom` fact 被计数、被报出，但语义落点没有可靠第二把自动尺子 | **无守卫 → `B-23`** |
| R-7.23 | 自造的数字在**跨章之间**不许打架——同一件事在两章里写成两个数，两处都不是原作给的，选哪个都行**但必须一致** | `INFO` |
| R-7.24 | `stage5.write --apply-edit` 是 **0 LLM 调用的新人工修订轮**，不是一个局部文本补丁。输入必须是文件名指向的单章完整 `ChapterResponse`，`segments` 覆盖该章全部 producer-owned slot 且每个恰好一次；缺 slot、多 slot或只交一段差异均关闭式拒绝。`--recheck` 只刷新 v2 派生字段，不归一或改写 segments、任务快照与写作 provenance；`--revalidate` 按 `.INVALID` 保存的原始 segments 重判，提升不能借机修改正文 | `结构`（`ChapterResponse` + `novel/stages/stage5/write.py::run_apply_edit` / `run_recheck` / `run_revalidate`）+ 回归测试 |
| R-7.25 | 写手收到的自然段数量目标必须与落盘判据共用同一公式：逐章篇幅带除以本书 `avg_paragraph_chars + paragraph_sep_chars`，下限至少 3、上限至少 4。`paragraph_count_report` 无条件报实际值与目标；越界由 `paragraph_budget_missed` **HARD** 拦成 `.INVALID`，不得由 `--apply-edit` 正式化。⚠ 本条是 §七登记的**可降级目标偏差**（`degradable=True`）：生成期照常 HARD 驱动预算内修复，预算耗尽后按预定义规则降为交付披露，不是事后改 WARN | `HARD`（`paragraph_budget_missed`）+ `度量`（`paragraph_count_report`）；两者共用 `novel/stages/stage5/measure.py::target_paragraphs` 与 `novel/stages/stage5/checks.py::_paragraph_budget` |
| R-7.26 | fresh 只代表 Review 结论仍绑定当前主体，不代表当前主体可以正式下游消费。s5 审批、Stage6、Stage7 与最终交付必须再要求 accepted Review：无活动 `.INVALID`、计划章均有正式稿、当前 Review 无 HARD | `结构`（唯一出口 `novel/stages/stage5/review.py::require_accepted_review`；`novel/orchestrator/approvals.py::_stage5_subject_rels`、`novel/stages/stage6/hook.py::_Upstreams`、`novel/stages/stage7/rename.py::_Upstreams` 共用）+ 回归测试 |
| R-7.27 | foreign `prepared` 是旧 run 已完成有效 initial-write 响应、却未可靠提交 canonical 的尾窗，不是第二条写作臂。默认付费写作必须在 client、driver factory、session、probe、cache 与 wire 前关闭式拒绝其目标章；唯一恢复路是同一个新 target run 先 `--emit-prepared-recovery-workpack <SOURCE_RUN_ID>`、再以工作包完整 SHA-256、actor/reason 执行 `--recover-prepared`。两入口均为 FREE，禁止 executor profile/egress、`--chapters`、`--revise` 与 response repair，恢复期间 target run 的持久 ledger/session 及当前 run 计数必须共同证明零 wire、零 cache、零 session | `结构`（`contracts/invariants.py::FREE_ENTRIES` / `VALUED_FLAGS` + `novel/stages/stage5/write.py` 的付费最低边界与 recovery producer）+ `tests/test_stage5_prepared_recovery.py` |
| R-7.27b | apply 不是“checkpoint 存在即采用”：必须重验 current Stage4 typed 最终结构批准、current WriterContext、独立 writing proof、source 章仍在当前计划，以及当前代码下的输入、prompt、schema、业务 validator、HARD 与 live baseline。正常 Stage5 Review 因尾窗未闭合而 stale 时不反向堵死这条专用恢复路；恢复会使 Review stale，全部收口后仍须单独重跑 `stage5.review` | `结构`（`novel/orchestrator/approvals.py::authorize_prepared_recovery` + `novel/stages/stage5/write.py::run_prepared_recovery`）+ 审批与 recovery 回归测试 |
| R-7.27c | source run 的 identity、commit、checkpoint 均同时核 semantic self-hash 与原文件 SHA-256，并与历史 ledger/session/request/attempt/response 逐项闭合；恢复不得修改 source 目录下任何 sidecar。`source_adoption_id` 只由稳定 source 身份与章组成、不含 target run：同一 source/章已由一个 target 正式采用后，另一个 target 不能重放；source 任一 raw/self 漂移必须重新阻断 | `结构`（`novel/llm/stage5_runs.py` 的 source sidecar 验证与稳定 adoption identity + `novel/llm/ledger.py` 的 append-only adoption audit）+ raw/self、只读与跨 run replay 回归测试 |
| R-7.27d | target 首次 apply 先原子冻结 hash-bound 整书 workpack/action，再逐章按 `prepared journal → canonical 共用提交口 → typed readback → 完整 adoption audit → committed` 收口；同 target 崩溃重跑只能补齐原声明。commit 当下须精确核 live post hashes；其后合法 rewrite/recheck/retain-pretrim 不得让旧 foreign source 复活，须由 current typed adoption 或 `stage5/_rounds/` 中与 journal 精确一致的 recovered payload/adoption 提供持久 witness，并继续核 source raw/self、journal self 与除时间外的完整 audit 行。任一 lineage 缺失或漂移均关闭式拒绝 | `结构`（`novel/llm/stage5_runs.py` 的 manifest/journal/replacement 判定 + `novel/store.py::write_round` 的历史归档 + `novel/llm/ledger.py::require_prepared_recovery_lineage`）+ 崩溃、合法后继与篡改回归测试 |
| R-7.27e | prepared recovery 的 subject 必须保留 `target_run_id` 绑定；因此通用 HTTP 作业面**不提供**这两个入口：`POST /api/jobs/preview` 与 `POST /api/jobs` 都必须在 argv、审批与 job 构造前拒绝 `stage5.write --emit-prepared-recovery-workpack` / `--recover-prepared`。HTTP 每个 job 都生成新 run_id，不能安全复用 target；操作员只能用 operator 的 CLI runner 显式给 emit/apply 同一个 `--run-id` | `结构`（`novel/api/routes/jobs.py::_refuse_prepared_recovery_api`）+ `tests/test_api_http.py::test_http_jobs_refuse_cli_only_prepared_recovery` |
| R-7.28 | **逐 slot 段落计划只有一个算法落点**：章节目标由章预算和本书段落成本计算并 clamp 进 `R-7.25` 的合法区间；每个非空场景至少 1 段，余量按场景预算 largest-remainder 分配，slot 顺序是唯一平局规则，总和必须精确等于章目标。场景数已使目标不可行时在首个 wire 前关闭式拒绝；prompt、修订反馈与常驻 `paragraph_slot_report` 必须消费同一计划。`reflow_shadow` 只允许改空白、不改变非空白字符 SHA，不跨对白合并且只在句末拆分；它只把可行性和 HARD 前后差写观察流，不写产物、不改变候选或 Review 结论，TODO-21 三书标定前不得默认采纳 | `结构`（`novel/stages/stage5/measure.py::paragraph_slot_plan` / `::reflow_shadow` + `novel/stages/stage5/prompts.py::build_material` + `novel/stages/stage5/checks.py::_paragraph_budget` + `novel/stages/stage5/write.py::_record_reflow_shadow`）+ `tests/test_stage5_paragraph_plan.py` |
| R-7.29 | **人工改稿是 preview→双 hash 提交，不是盲写覆盖**：只读 preview 与正式 apply 共用归一、完整 slot 校验和当前 `_judge`，零模型调用且 preview 不写盘；响应同时给归一稿、完整 finding、当前/新增/消除 HARD、当前 canonical 完整文件 SHA-256 与 preview subject SHA-256。apply 必填 actor/reason、两枚 SHA 与 `formal｜working_draft`：同书总锁→逐章锁的线程+跨进程所有权覆盖同一纯评估口重算、preview subject 匹配、提交前 current 文件 SHA 复核以及整条写回，任一漂移都在归档前拒绝；`formal` 不得新增 HARD，`working_draft` 可明示落一般新增 HARD 为新的 canonical human 轮但不得绕过 `paragraph_budget_missed`，旧轮照常归档，Review/closure/下游保持 stale/阻断，**不写 `.INVALID`**。`human_actions.jsonl` 以不含正文的 `prepared → canonical/trim 落盘+读回+report → committed` 两相收口，request 绑 preview 意图、action 再绑预期结果 SHA；普通异常补偿产物并保留 prepared 证据，kill 留下精确 landed 结果时新 run 只补后相而不造新轮，公开审计只认 committed。未换行尾必须在 append 全局锁内先把原始字节与前缀摘要内容寻址地隔离到 `_service/human_actions_tail_recovery/`，再截尾补相；中间或已换行完整坏行仍关闭式失败。逐次 apply 不自动跑 Review；人可连续预览/改多章，只在全部编辑与 trim 收口后显式跑一次最终全书 `stage5.review` | `结构`（`contracts/schema/api.py::DraftEditPreview` / `::DraftEditRequest` + `novel/stages/stage5/write.py::prepare_edit_preview` / `::run_apply_edit` + `novel/human_actions.py::HumanEditAction` + `novel/api/routes/edits.py::preview_draft_edit` / `::edit_draft`）+ CLI/API/hash/审计账回归测试 |
| R-7.30 | **正式提交事务与 same-run sweep 是一次 run 内的确定性收口**：阶段闭集为 `prepare_commit → formal_write → typed_readback → adoption → mark_committed → committed_verify → session_end`；首轮逐章提交失败先保留阶段/摘要并让其余候选完成，随后只对失败章做恰好一次有界 sweep，复用同一 checkpoint、候选、章所有权与 usage 认领，只重放提交链，禁止新 driver/probe/wire/cache/session。提交和 batch 记账分别以 resume/once 幂等续接，session 只结束一次并记 delivery attempts、same-run recovery 与首个失败阶段；所有 preflight/readback/trim 核验读必须隔离在 `RunContext.validation_reads()`，提交前后来源边新增、消失或改值均关闭式拒绝 | `结构`（`contracts/invariants.py::STAGE5_COMMIT_PHASES` + `novel/stages/stage5/write.py::_commit_phase` / `::_run_group_parallel` / `::_assert_commit_edges_stable`）+ `tests/test_stage5_commit_sweep.py` |
| R-7.31 | Reviewer 改写须先以正式批请求和永久 authority receipt 固化授权，再由独立 fresh epoch 消费；手写请求或孤儿 CLI 不获得执行权。只对有序非空章子集逐章给关闭式指令，未选章不代签；跨章联动超出当前指令时停止。授权原字节、typed 主体和 seat/session/cost 来源共同复验，不重序列化历史凭据。每章独立 lease、checkpoint 和执行 journal，旧结果优先零 wire 补尾；未知调用不重购，未解决意图不覆盖，整批落稿后统一免费 Review。字段、九态、提交/恢复/后继事务与版本边界唯一见 `docs/schema/stage5.md::Reviewer 改写授权与执行`。 | `结构`（`contracts/schema/stage5.py::ReviewerRewriteBatchRequest` / `::ReviewerRewriteExecutionJournal` / `::ReviewerRewriteRecoveryJournal` / `::ReviewerRewriteDisposition` + `contracts/schema/orchestration.py::ReviewerRewriteAuthorityReceipt` + `novel/orchestrator/reviewer_action_evidence.py` + `novel/stages/stage5/reviewer_rewrite.py::record_reviewer_rewrite_request` / `::reviewer_rewrite_execution_state` + `novel/stages/stage5/write.py::run_reviewer_rewrite` / `::run_reviewer_rewrite_prepared_recovery` + `novel/llm/stage5_runs.py::reviewer_rewrite_execution_ownership` + `novel/orchestrator/queue.py` / `execution_permit.py` / `agent.py`）+ request/authority/九态 journal/多轮 intent/并发 claim/全硬杀窗/合法后继/零重复 wire 回归测试 |

A 接受、B 最终结构接受和 writing proof 是独立授权，已有文件或付费账不能反向补造审批。
盘上与 HTTP 审批版本分别以 `docs/schema/stage5.md` 为准，旧输出只配对冻结代码，不原地迁移或重签。
`R-7.6` 的重合上界是材料泄漏异常探针，不是文风评分；对白读数逐章常驻，历史比例不能作为固定缩放系数。

材料入口为 `novel/stages/stage5/prompts.py::build_material`。局部文字表只无损去重正文，目标、因果、场景、
转折和状态引用仍逐处保留，编号不代表同一事件；人物、披露状态和字面保真材料不参加文字替换。
字节没有减少时保留原表达，校验与反馈仍用同一真实 given。反例见 `tests/test_stage5_write_agent_boundary.py`。

### R-8 · 全书验收与修复（stage5 第二相）

判成品并保持人工与机械结论分工；只有 current Review 可驱动后续修订、销账和审批。

| # | 要求 | 谁在查 |
|---|---|---|
| R-8.1 | 严重度四级：`阻断 / 提示 / 度量 / 查不了`。**「度量」与「提示」必须分开**——读数无条件产出，不许挂在「没问题」那一支上 | `结构`（`contracts/rules.py::_validate` 第 ② 条：`sev` 与 `who_checks` 两侧必须成对，写歪一侧当场拒绝注册） |
| R-8.1b | 篇幅 · 与源章的重合度 · 全书篇幅总账这几个读数**逐章 + 书级无条件报出** | `度量` |
| R-8.2 | 每条结论带**根因第一嫌疑层**；「可修复」**蕴含根因在写作层**——根因不在写手身上的条目重写一百遍也修不好 | `载体`（`contracts/schema/common.py::Finding` 的两条后置条件：`owner is None` ⇔ 它不是结论 · `actionable` 蕴含 `owner == 'stage5'`） |
| R-8.3 | 收敛键 = `(章, 判据, 证据键)`。只按判据去重会把「换了个位置」读成「已修复」 | `载体`（`contracts/schema/common.py::Finding` 的 `evidence_key` 由 `evidence` 现算并断言，对不上当场炸） |
| R-8.3b | 收敛账**无条件报出**：与上一轮相比消掉了什么、还剩什么 | `度量` |
| R-8.4 | 成稿指纹没变时**不许前进未消轮次计数**——0 成本重跑一次验收不是「写手又试了一次」 | `结构`（`novel/stages/stage5/review.py::_converge` 只在**正文**指纹 `draft_text_sha1` 变了时才 +1，没变一律记 `same_draft`；`converge` 由「稿子变没变」决定、不由计数值决定） |
| R-8.5 | 连续 2 轮未消 → 判「根因不在写手」，修复主动跳过 | `结构`（`novel/stages/stage5/write.py::revise_targets` 的第四道筛子按 `contracts/invariants.py::STOP_REWRITE_AFTER` 筛掉，并把跳过的两类**单独报出来**——「不进修复」不等于「不管」） |
| R-8.6 | **付费自救的第二道准入**：可修复之外还要「满足路径唯一」——目标字串被上游钉死，或动作就是「删掉指出来的那个东西」，写手编不出第三种答案。密度 / 比率 / 档位类**一律不进**，因为最省力的满足路径是删内容或编内容 | `结构`（`contracts/rules.py::_validate` ③④：准入是**一个字段的三个取值**，「进且只进一张桶」于是不可能分不干净；写歪一个值当场拒绝注册） |
| R-8.7 | 「这条我不修」要**理由必填**，且豁免只挂在它真正豁免的那一条结论上 | `载体`（`contracts/schema/common.py::WontFix` 的 `why` 最小长度 + 键↔条目的收敛键一致性断言。⚠ 模型已从 `stage5.py` 搬到 `common.py`：语义豁免闭环把它变成跨层机制，形状必须只有一份） |
| R-8.7b | 不给**不存在的**结论发豁免（该层**当前结论池**里没有那条 ⇒ 显式终态）；豁免**不删结论**，只是不再进修复、不再占待办，并**单独计数** | `结构`（`novel/wontfix.py::mark` 先在结论池里反查，查不到 / 同一 (章,判据) 有多条而没写出证据键 ⇒ 显式终态；落进 `Finding.wontfix` 由 `novel/wontfix.py::applied` **只降 `actionable` 不删结论**） |
| R-8.7c | **持久豁免要跨层共用同一个闭环**。身份 = `(层, 章\|book, 判据, 证据键)`，层由 `<stage>/wontfix.json` 路径承载；产物版本只进审计账 `at`、不参与匹配，失效由当前证据键保证。现役 Stage5 结论池只有 0 成本机械验收；语义结论只在 Stage6 文案层进同一闭环 | `结构`（`novel/wontfix.py` 是全链唯一机制落点；结论池分别由 `novel/stages/stage5/review.py::mark_wontfix` 与 `novel/stages/stage6/hook.py::run_wontfix` 交出） |
| R-8.8 | 待办清单聚合八类，**只读盘、不重判**：`invalid` / `rejected` / `stalled` / `human_only` / `repairable` / `verification` / `upstream` / `approval`。`repairable` 仅表示本层有结构化修复路，不再冒充「只能人修」；`verification` 只收有明确人工消解路的 UNVER。固有盲区仍不制造永远清不空的待办 | `结构`（`novel/orchestrator/inbox_projection.py::inbox_view` 只读正式产物、审批、语义决定与 durable trim audit/resolution；`INBOX_KIND` 八键始终全出，不调用模型） |
| R-8.9 | 词表类判据**必须报出它的基线命中率** | `结构`（`contracts/rules.py::_validate` 第 ⑤ 条：`wordlist` 不填 `baseline` 当场拒绝注册） |
| R-8.9b | **探针自己的基线读数**要无条件报出：未揭示秘密在全书成稿里的出现分布 · 全书自造称谓与撞名的名字面。⚠ 后者原来只在**命中时**才产出 ⇒「全书 0 个自造称谓」这个读数永远说不出来，而「查过、干净」与「根本没在查」在报告上长得一模一样 | `度量` |
| R-8.10 | **历史关闭说明**：Stage5 曾有独立付费的两相书级语义审判；它已连节点、产物与判据一并下线，现役 Stage5 只产 0 成本机械 Review，不得宣称仍有一层书级语义守卫 | `结构`（`contracts/invariants.py::NODES`；转授与历史依据见 `R-7.18b` / `R-7.18c` 与 `B-15`） |
| R-8.11 | 当前 Stage4 计划里的每一章都要有且只有一份 canonical `stage5/chNN.json`；缺章单独报 `draft_missing`，旧目录则进 `legacy_incompatible`，不把两种状态合成一个比例 | `INFO`（`draft_missing`） |
| R-8.12 | 逐章 finding 必须携带 `output_ordinal`；书级 finding 显式为 `null`。Review、CheckReport、CLI/API 与 inbox 不得丢掉这个定位键 | `载体`（`Finding.output_ordinal`）+ 端到端回归 |
| R-8.13 | 当章有一份当前 schema 可读、登记相对路径解析的章主体指向当前 canonical 稿、且正文 SHA-256 精确匹配的 `TrimAudit` 时，`length_over` 不再单独产 finding，而把 before/目标及超限事实吸收到同一条 trim verification detail。⚠ **折叠不看销账状态**：已 `retain_pretrim` 销账的 audit 照样折叠——销账关掉的是待办，不是「这一稿确实超限」这个事实；销账后再产一条 `length_over` 等于把人刚关掉的那个决定重新打开（判据侧取 `current_trim_audits`，inbox 待核验侧取 `current_unresolved_audits`，**两个集合故意不是一个**）。audit 缺失/拒读、正式稿缺失/不可读/过期、路径非规范、canonical 章号对不上或正文 hash 已变时，独立篇幅 finding 仍照常保留。⚠ 没有「已采用」这一档：自动收缩候选与采用路径已于 2026-08-18 下线 | `结构`（`novel/stages/stage5/review.py::_merge_length_over_into_trim`；当前审计集合由 `novel/stages/stage5/trim_resolution.py::current_trim_audits` 给出）+ trim resolution 回归 |
| R-8.14 | `band_drift` 的逐章事实保留在 `Review.chapters`；对外 `CheckReport`/CLI/API/inbox 聚合为一条书级 finding，detail 给命中章数/章号，evidence 可展开逐章明细，且 wontfix/收敛信息不得静默丢失 | `结构`（`novel/stages/stage5/review.py::_aggregate_band_drift`）+ CheckReport/inbox 回归 |
| R-8.15 | `stage5.review --retain-pretrim` 必须显式提交非空 `--actor` / `--reason`，并用当前章主体 hash 锁定 resolution；`--retain-pretrim-batch` 同样要求非空 actor/顶层 reason，且每项有非空 effective reason 并绑定 current audit/chapter 双 SHA，不允许默认空凭据或由 Supervisor 代填决定 | `结构`（`novel/stages/stage5/trim_resolution.py::retain_pretrim` / `::retain_pretrim_batch`）+ CLI/ArtifactStore 后置条件回归 |
| R-8.16 | 字面 secret timeline 以 Stage2 `first_reveal_chapter` 为权威读者时点；Stage4/5/6 必须共用 `novel/core/facts.py::first_reveal_chapter`，不得自造兼容字段或第二套判定 | `结构`（`novel/stages/stage5/checks.py::fidelity_findings`）+ 时线回归 |
| R-8.17 | **Stage5 只有一份强制、hash-bound 的整书闭合回执；同一 `approval:stage5-closure` 顶部卡按 current closure 状态发布互斥动作。** 只读 workpack 必须绑定当前规则指纹、fresh Review 的完整文件 SHA、全部计划内 canonical 章的完整文件 SHA，以及 current trim audit/resolution 与 wontfix 处置面的稳定完整摘要；构包前后必须重验同一 Review 文件并对全章、活动 `.INVALID`、trim/wontfix 处置面做完整双采样，任一中途漂移都不得返回新旧混合包。全部 Stage5 canonical/INVALID/recheck/revalidate/Review/wontfix/trim writer 与 closure submit 必须共用同一个位于 `<base>/_service/` 的线程+跨进程书锁，逐章提交再按序取章锁；closure 二次构包至 approval/gate 原子写回之间不得有 writer 尾窗，API 不得因取锁写书目录。只有 Stage5 完全未开始且无历史时可为 `missing`；已有稿件/历史时 Review 缺失、过期或不可证明 current，状态只能是 `review_required`，不发布 current workpack/decision 但保留 history。fresh Review 下若仍有缺章、活动 `.INVALID`、当前 HARD 或未销账 trim，workpack 必须优先为 `blocked`；其中**仅有 unresolved trim** 时，同一卡只发布互斥的人工出口：覆盖全部待销账章的 typed retain full-batch、有序非空章子集的 typed LLM rewrite request、或 typed stop，均由人在 Stage 页面提交，绝不给 closure approval，也不由队列自动 retain/rewrite；缺章、活动 `.INVALID` 或 HARD 与 trim 并存时在开席前 stopped/2 并指向各自恢复面。只有 accepted 面才可进入 `ready/closed/expired` 并只发布 closure receipt。actionable 的章级 `must_appear_missing` 只接受逐项 `confirmed_present` 或理由非空的 `accepted_exception`；恒等基线已降为不可执行的同类线索保留原 finding 与披露，不重新要求裁决、不使整包失败，也不改写为 PASS 或已销账；缺章身份的书级 actionable 同类条目仍关闭式拒绝。fact landing 与其余 UNVER 只按完整 finding-set digest 整组确认，未登记的 actionable 规则关闭式失败；历史预填只复用 finding SHA 与 chapter SHA 都未变的逐项裁决。workpack 的 `unresolved_trim_items[]` 必须精确投影每章 audit SHA + 正文 SHA；retain batch 只能原样复制完整有序全集并逐项给非空理由，rewrite request 也复制同一完整 frontier，并以 targets 选择有序非空章子集和 `R-7.31` 的关闭式 directive/evidence。trim Reviewer 的稳定 business 统一绑定 `action_mode=trim_disposition`、同一 Review 原始 SHA、原始全量有序 audit/chapter 双 SHA 与 reviewer contract，刻意不绑定会随处置落盘变化的 closure token/workpack；retain/rewrite 不能拆成两个席位，closure receipt 仍以 `action_mode=closure_receipt` 绑定 opaque token。retain producer 在书锁内全量预检并于首写前复采；中断后只有同一 manifest 的连续 exact 已落前缀与 current unresolved 完整后缀可零模型重放，前缀决定须同 actor/reason/双 SHA，后缀不得漏、夹、重排，坏 audit inventory、路径/章号或任一身份漂移都保留 durable 证据并停止。rewrite 回答先按 `R-7.31` 零成本落正式 request，之后其 pending/unknown/prepared/terminal 执行态在 stale Review、closure 与新 Reviewer 前收口。纯 `retain_pretrim` 不改正文或 Review 主体，批次完成后沿用同一 fresh Review，只以 current 处置摘要重建 closure workpack/token；rewrite 正式改稿后必须先免费重跑 Review。`gate.s5` 只能由 current typed closure 打开，匿名 `approve_gate('s5')` 拒绝；Stage6/7 写效果 CLI 与最终 assemble 共用 `require_stage5_closed` 硬闸，0 成本检查、改稿、trim、request 与回执恢复入口不得被反向封死 | `结构`（`novel/stages/stage5/closure.py::closure_workpack` / `::closure_view` / `::require_stage5_closed` + `novel/stages/stage5/trim_resolution.py::retain_pretrim_batch` + `novel/stages/stage5/reviewer_rewrite.py` + `novel/orchestrator/approvals.py::approve_stage5_closure` + `novel/api/routes/approvals.py` / `novel/api/routes/edits.py`）+ Stage5 closure/trim/rewrite、durable replay 与 CLI 闸回归 |

freshness 唯一入口是 `novel/stages/stage5/review.py::review_status`，实际依赖及字段见 `docs/schema/stage5.md`。
批量写回每一项前都要在同书锁内重验完整 manifest、已落前缀与未落后缀，不能用批次开始时的缓存覆盖后来变化。
逐章 identity_hit 与书级 identity_dead 是不同结论，上游锚点问题不得借此归罪当前判据。

保留反例：为补对白比例而编造新台词；揭示词表在几乎每章都命中；秘密换措辞后连揭示章也零命中。
因此密度、比例等多解目标不获付费自救权，判别力必须按“判据 × 本书”检验。
批量 rewrite、恢复与义务覆盖见 `tests/test_stage5_reviewer_rewrite_batch.py`、`tests/test_reviewer_work_unit_evidence.py`。

### R-9 · 开篇文案（stage6）

一次产生三个候选，人工定稿；故事材料提供选择空间与真实性边界，不用于逐条翻译全书秘密。

| # | 要求 | 谁在查 |
|---|---|---|
| R-9.1 | 三候选是**一次**调用产出，不是三次（成本口径） | `结构`（`novel/stages/stage6/hook.py::run_hook` 是全层唯一一处 `client.ask_json`） |
| R-9.2 | 文案准备由独立 `stage6.prepare` E/C 负责，正式输入只有 MainText 与 HookCopy，各在模型材料中完整提供一次。保存正文支持、原钩子独立支持、未决事实及连续开篇提案；代码编译段落定位并校验，语义接受独立于最终 `gate.s6`。Stage5 闭合后方可首购 Prepare，当前 Prepare 未接受不得生成 Hook；开篇缺失、unresolved 或非法终点交 typed 裁决，不从比例或第 1 章缺省猜测。源材料变化重开对应准备审阅，免费编辑绑定当前材料和审阅 token；原付费回答、决定与提交 WAL 优先恢复，不重新购买。材料范围不等于知识披露上界，仍保留全书事实供文案审阅 | `HARD`（`prepare_evidence_valid`）+ `UNVER/人`（`prepare_semantic_review`）；来源与恢复落实于（`novel/stages/stage6/prepare.py` + `novel/stages/stage6/prepare_compile.py` + `novel/stages/stage6/measure.py::lead_upto`） |
| R-9.3 | 机械层归一（标点 / 引号体 / **段间分隔**）由代码做**并记账**；段间分隔另有后置条件校验 | `HARD`（后置条件）+ 记账进报告 |
| R-9.3b | 引号配对异常**留着报，不静默修** | `INFO`（`hook_quote_unbalanced`） |
| R-9.4 | 收尾形态参照原作文案段的四分类 | `INFO` |
| R-9.5 | 引语的条数与最长一条字数**无条件报出**、与原作并排；**不给阈值** | `度量`（`hook_quote_shape`） |
| R-9.6 | **定稿绑定当前字节；默认自动、人工改选随时可做且不阻塞下游**（2026-09-15 全自动裁决）。默认路 `stage6.approve --auto`：完成全部必需机械检查后按**固定槽位序**取第一条无 HARD 候选（不做任何质量优选——诊断条数不是候选质量），`origin='auto'`、`basis='default_policy'`；三条均不可用即拒绝，预算内修复或终态失败，不默认放行；当前候选集合上已有有效用户选择时**不覆盖**。人工改选 `--pick N --actor --reason --review-token`：`origin='human'`、`basis='human_selection'`——**选择只证明选择**，不证明人工验证过全文；选中当前同一条候选仍是有效选择：内容不变、来源 auto→human、披露更新（「开篇文案未经人工选择」移除，「本书未执行语义质量审阅」保留，Q-13）。判官随 `stage6.semantic` 整节点退役，`human_verification` / `semantic_review_seq` 随之消失 | **`人`**（`hook_not_approved`）；批准边界使用 `HookApprovalResolution`，并由 `novel/stages/stage6/semantic.py::require_human_resolution` 在归档/写盘前校验乐观锁与字节绑定 |
| R-9.7 | **剧透不是违规，也不是质量目标**：只判断它是否增强期待；不会因为说出后期信息而扣分或阻断 | `不适用`（产品质量口径的负空间声明） |
| R-9.8 | 文案允许重写台词、压缩时间、倒叙与合成场面；只有改变人物身份、关系、关键因果、故事结果或新增改变理解的设定才算矛盾。语义审判已退役，这一维不再有自动判据，属本书未执行语义质量审阅的披露面（Q-13） | `不适用`（负空间声明；披露见 `SEMANTIC_UNREVIEWED_NOTE`） |
| R-9.9 | **完整故事真相面下发**：独立 Characters 身份、Global 四类长期约束、已接受 Prepare 主张与开篇范围，以及全书逐章事件地图均提供给生成器和质量审判；人物表不再从 Global 取，源文案准备不回写 Global。`hook_supported` 是源钩子独立事实，不因正文未重复而判编造或要求正文补写；正文有反证须明确裁决。模型自主选择文案披露多少 | `结构`（`novel/stages/stage6/prompts.py::build_material` + `novel/stages/stage6/hook.py::_Upstreams`） |
| R-9.10 | **原作文案作为创作金标准下发**：学其信息次序、升级节拍、反差、对白密度与最后一击；Stage6 从同一份 Stage1 文案即时计算字符数、平均句/段长、引号体、对白比例五叶机械参照，定性包不得进入生成或语义复核 | `结构`（`novel/core/measure.py::hook_style_metrics` + `novel/stages/stage6/prompts.py::build_material`） |
| R-9.11 | 这是一次**重写**：不复用**原作文案段**的句子 · 不整段誊抄成品正文 | `HARD` + `INFO` |
| R-9.12 | 文案里的**人物称谓**与**数字+单位**都要在下发面里有据 | `HARD` + `INFO` |
| R-9.13 | 文案是**完整的一段**：不是空的、以完整句子收束 | `HARD` ×2 |
| R-9.14 | 篇幅 / 段落形态 / 引号体贴合**本书自己的文案段**（不是正文） | `INFO` ×3 |
| R-9.15 | 本层三条读数**无条件报出**：下发范围 / 各候选字数 / 原作文案段字数；文案与秘密原文的逐字重合条数（按读者时点分已揭示 / 未揭示）。⚠ 它们**不是门**——秘密允许说破（`R-9.9`），这一条要的只是「数字必须出现在报告里」 | `度量`（`hook_report` / `hook_secret_literal`） |
| R-9.16 | 开篇连续正文有缺稿时必须常驻出声；secret 缺失或没有揭示时点不再限制文案，因为生成器已有完整事实与故事地图 | `INFO`（`hook_body_missing`） |
| R-9.17 | 判的必须是**照盘上这份正文写的那一版**文案：逐份成稿记 `judged[].consumed`（字段投影）判过期，整文件 `sha1` 只作审计；**没有 `consumed` ⇒ 说不清，不许算作没过期** | `INFO`（`hook_body_stale`） |
| R-9.18 | 原作文案式的重写台词与合成场面是合法体裁手法，不再要求台词逐字存在于正文，也不为这件事保留一条永恒的 `unver` 假检查 | `不适用`（负空间声明；边界由 `R-9.8` 定义） |
| R-9.18b | **A/B 双盲语义审判已整节点退役**（2026-09-15 全自动裁决）：原 R-9.19 实质矛盾结论、原 R-9.20 逐候选矛盾提示与原 R-9.21 吸引力排序随 `stage6.semantic` 一并删除，判据（`hook_semantic_contradiction` / `hook_candidate_contradiction` / `hook_quality_rank`）、schema 家族与 `double_blind_decision` 不再存在；旧书 `semantic_N.json` 只读保留、现役不读取。矛盾维与质量排序进入 Q-13 披露面 | `结构`（`contracts/invariants.py::RETIRED_NODES` + `novel/stages/stage6/semantic.py` 模块头）+ 历史只读解码 |
| R-9.22 | 初始三候选机械判定后，命中 HARD 的候选按槽位顺序定向 refresh-write；请求只能回该槽原 approach，并见完整事实面、整组候选及具体 HARD 证据。只有 HARD 数严格下降且不与其他候选重复时才采纳；仍有 HARD 时继续同槽修订，失败/wrong approach/重复/无净减则保留最近已验真候选并提前停止该路径、完整记账。整书初始生成、格式纠错和所有槽位修订共享工作单元基础四次内容额度，显式人工追加依 R-11.20；缓存命中不购买内容轮；网络尝试另行有界计费。换槽、候选变化、会话或进程重启不重发预算。Hook v4 持久记录同槽连续唯一 attempt 1..4；Agent 完整替换协议不启用局部重试 | `结构`（修订与取舍在 `novel/stages/stage6/hook.py::request_hook_candidates`，预算为 `novel/work_units.py::WorkUnitBudget`，审计载体为 `contracts/schema/stage6.py::HookCandidateRetry` / `::HookProvenance`）+ 零模型端到端恢复回归 |
| R-9.23 | **呈现结构距离无条件报出、不给阈值**：候选与原作文案段的多档窗口重合率、最长逐字重合字数、末行逐字重合，与 `R-9.11` 那道 20 字闸的读数并排展示。⚠ 它**不是第二道复用闸**——`R-9.11` 尾注明许学结构、节拍与最后一击的功能，结构贴近本身不违约；这条读数要回答的是「这条候选离原作有多近」，供人裁决策略去留与定稿取舍。⚠ 设阈值须先有多轮采样的跑间方差，同 `R-9.5` 的纪律 | `度量`（`hook_source_distance`） |
| R-9.24 | **候选身份是位置，策略是可重复的属性**：一次调用按 `HOOK_RECIPE` 逐槽产出，响应校验必须按列表位置逐项等于这份配方，而不是只比计数、更不是「每种策略各一次」——同一种策略可以占两槽，模型却不能把 c1/c2 对调后再让位置身份替错误顺序背书。同一种策略占两槽时，prompt **现算**出「这两条必须锚在不同事件上」那句硬要求（配方改回全不重复时它自己消失，不会留下一句不再成立的话）。⚠ `source_architecture` 已于 2026-08-23 退场：不再下发、响应里出现即违约，但**仍留在 `Candidate.approach` 的取值面上**——v2 对照臂里仍有这个历史取值，删掉会让退场证据拒读 | `结构`（`contracts/invariants.py::HOOK_RECIPE` / `HOOK_RETIRED_APPROACHES` 是唯一落点，import 期断言守住配方⊆取值面、配方∩退场=∅、配方长度=槽位数）+ `HookResponse` 的逐槽配方校验器 |
| R-9.25 | Stage6 定稿必须先读取只读、异常优先、hash-bound 的公开 v4 workpack：公开面为 `GET /api/novels/:sid/approvals/stage6/workpack` 与 CLI `approval workpack stage6`。同一包完整覆盖三候选正文、机械异常与度量（判官证据已随 `R-9.18b` 退役移除），以及 `materials` 内的已接受独立 Prepare 主张及其材料身份、原钩子与完整 canonical 重写正文（各只一份）；文案独立事实与正文分别核验，同一断言不重复计数。现役审阅契约为 `stage6-final-review-v5`，材料变化进入长期 review identity；旧接受须由当前 workpack 重新裁决，不重抽无关上游。工作包在内部区分 review subject/contract 与当前 validation seal；不自动 pick、不把合并 UNVER 洗成 PASS。调用方只提交刚读包给出的 opaque `review_token=s6r4_…` 与 actor/reason/pick，不能手搬 subject/workpack/code/rules 多枚 SHA；公开包只输出 opaque seal，不输出 `code_version` / `rules_fingerprint` 原料。服务端在同书锁内重建同一包、核 token 与 current validation seal，任一漂移关闭式拒绝；旧 v2/v3 包与 `s6r2_`/`s6r3_` token 只读，不接受新批准。raw typed 批准只由 review subject/contract 判断适用；代码或规则变化先投影为 `suspended`，免费复检通过且人实际看到的内容/finding 未变时自动复活，不要求重签。approval/current 字段不进入 review identity，批准本身不自废；只有同一 raw 决定 effective current 时 `disposition=already_current`，重复提交幂等跳过，不同声明不自动合并 | `结构`（`contracts/schema/api.py::Stage6ApprovalWorkpack` + `novel/stages/stage6/hook.py::approval_workpack` + `novel/views/stage6.py::stage6_approval_workpack` + `novel/api/routes/approvals.py::get_stage6_workpack` + `novel/cli/main.py::_cmd_approval`）+ hash-bound `人` + opaque-token、raw/effective、自动复活正反例回归 |
| R-9.26 | Stage6 hook prompt 的无损瘦身与前缀缓存只开放为显式实验臂：默认 `pretty-v1` 的实际请求字节必须不变；`compact-v1` 只删 JSON 语法空白，不排序键、不改字符串/数组/字段；layout 必须进 Hook provenance。判官双 session、错峰实验与 semantic decoder fingerprint 已随 `stage6.semantic` 退役删除。静态前缀 observation 只证明真实 driver stdin 的公共字节，不保证 provider 命中；命中/成本唯一认分角色 ledger usage。任何默认切换、字段投影、模型或 effort 变化都须先过干净缓存的多书质量回归，不以单书墙钟替代质量与稳定性证据 | `结构`（`novel/stages/stage6/prompts.py::dump_prompt_json` / `::prompt_json_layout` + `contracts/schema/stage6.py::HookParams`）+ 默认字节与 ledger 回归 |

候选配方、输入字段、双盲审判和定稿见 `docs/schema/stage6.md`。允许合成场面或新台词，
不允许改变人物关系、关键因果与故事结果；编造不存在的血迹或入狱结果是实质矛盾反例。
原作文案的长句复用约束同时作用于模型与人工写回；历史 A/B 只解释裁决，不证明已退役字段的独立收益。

### R-10 · 人名替换与成书（stage7）

模型只产生改名决定，代码替换并组书，人工定稿；关系、身份、职位与称谓体系保持。

| # | 要求 | 谁在查 |
|---|---|---|
| R-10.1 | **改名只能待在链尾**：全链底座的判据是「人名必须在原文里有据」，把新名写回上游会被静默撤销。本节点**只读上游、只产自己的产物、绝不回写** | `结构`（`novel/stages/stage7/rename.py::run_rename` 与 `novel/stages/stage7/assemble.py::run_assemble` 是全层仅有的两个写盘点，两处都只落 `stage7/`；写口本身也只有 `novel/store.py::ArtifactStore` 一个） |
| R-10.2 | 替换是**单次 alternation 扫描**，`A→B`+`B→C` 的级联在结构上不可表示 | `结构`（`novel/stages/stage7/subs.py::apply_subs` 的单次 `pat.finditer`，游标不回头） |
| R-10.2b | 上一条的**后置条件**：替换跑完，映射表里**任何一条键都不许在替换目标里留下**——残留一条键就说明那次扫描漏掉了一段字节 | `HARD`（`rn_residual_key`） |
| R-10.3 | **三道防线之①**（中文没有词边界，这是本节点全部的风险；另两道是下面的 `R-10.3c` / `R-10.3d`）：保留项进 alternation 当**恒等条目** | `结构`（`novel/stages/stage7/subs.py::sub_table` 把保留项与替换项合成**同一张表**、按长度倒排，短项抢不走长项的字节） |
| R-10.3c | **三道防线之②**：要替换的称呼都**不短于两个字**。单字面在中文里没有左右锚，换掉它就是在正文里随机改字 | `HARD`（`rn_surface_short`） |
| R-10.3d | **三道防线之③**：被替换的称呼**没有同时挂在多个角色身上**，多主一律否决——否则换的是别人的称呼 | `HARD`（`rn_shared_surface`） |
| R-10.3b | 但**有一类书里多主是情节本身**（顶名 / 替身 / 女扮男装）：人在补充映射里明写的按人的裁决执行，另落一条提示 **照样报出它还碰到了谁**。`R-10.3d` 那条否决防的是**静默**误伤，不是「多主一律不能换」 | `INFO`（`rn_shared_surface_human`） |
| R-10.4 | 姓氏族群只从**原文有据的姓**生成：完整 `canonical` 精确等于语言层关系/官职/位号标记时先排除（`皇贵夫` / `皇太女` 不姓「皇」），只以标记结尾的带姓称谓仍保留（`王夫人→王`、`李将军→李`）；其余候选按「字面同姓且 ≥2 人」聚类。关系边那条已删：一本书的兄弟在关系里**根本没有边**，而谓词词表会把夫妻同姓并成一族。确定性族群不能由模型自己的 `renamed` 决定退出，表中原姓一个不能漏、组内必须共用新姓 | `HARD`（`rn_surname_split`；族群生产唯一落点 `novel/core/naming.py::attested_surname` / `::family_groups`，精确排除与正向召回由生产回归查） |
| R-10.5 | 亲属词与姓氏同形（`孙`/`公`/`子`/`女`/`母`）时，用**本书的姓氏表**判，不硬编码字表——否则会让原作的姓氏活着进入「原创」成品 | `HARD` |
| R-10.6 | 残留探针的取值面 = 「原文有据的姓」**∪** 「姓氏映射的键」。少了后者有盲区：正典名是自造标签的姓、与亲属字同形的姓都会被过滤判掉 | `结构`（`novel/stages/stage7/rename.py::_build` 里 `stems` 那一行并集，喂给 `novel/stages/stage7/subs.py::residual_scan`） |
| R-10.6b | ⚠ 真姓被保守过滤漏掉时，**不为召回放宽下发面**，由探针把 `surname_map` 键并回来；反方向的完整纯称谓/位号是假姓，必须在 `attested_surname` 以共享语义标记的整串相等精确排除。同一份数据在下发面该严、在检测器该宽，两处的正确方向相反 | `结构`（`novel/core/naming.py::attested_surname` 只被 `novel/core/naming.py::family_groups` 调用，`stems` 另走一条路） |
| R-10.6c | 探针的**结论**要常驻报出：逐个姓字的覆盖率与残留条数，**残留 0 条时也要出声**——「扫过、一条都没捡到」与「根本没扫」不许在报告上同形 | `INFO`（`rn_residual_stem`） |
| R-10.7 | 恒等基线：占位符双射往返，验替换器与组装器**逐字节守恒**。⚠ **它的结论必须落进产物**——「跑过且逐字节无损」与「从没跑过」不许在盘上同形（`Q-2`；原型只 `print`，产物里没有任何一个键记得它跑过没有） | `INFO`（`rn_identity_baseline`；**没跑过时报「没跑过」**，不是不报这一行） |
| R-10.7b | 恒等基线**验不了**三样：正向对真名安不安全（占位符天然无歧义，真名不是）· 漏网覆盖率 · 新名好不好。**这三样必须与基线的结论印在一起**，否则下一个人会把它跑绿了读成「替换是安全的」 | **`人`**（`rn_identity_blind`），见 `B-21` / `B-22` |
| R-10.8 | 成书 = `文案 \n\n 1 \n\n 第1章正文 \n\n 2 \n\n …`，LF、无「第N章」、无任何审阅元信息 | `结构`（`novel/stages/stage7/assemble.py::assemble_txt` 是唯一的组装路径，章分隔与换行取自 `contracts/invariants.py`，是常量不是参数） |
| R-10.9 | 成书是纯文本、带不了来源账，所以账记在它的 sha1 + 「判了哪几份稿」上 | `HARD` |
| R-10.10 | **替换覆盖率验不了**——只能替换人物表里有的称呼，**人物表漏掉的角色这一层结构上够不着** | **无守卫 → `B-21`** |
| R-10.11 | **新名好不好、读起来串不串味没有确定性判据** | **`人`**，见 `B-22` |
| R-10.12 | 新名贴合时代与性别气质 · 保住名字之间的结构关系（排行字、兄弟姐妹只差一字） | **无守卫 → `B-12`** |
| R-10.12b | **改名不得改变旧称呼自带的字面语义标记**：中文数字/阿拉伯数字、排行、亲属关系、头衔官阶必须在新形中原样保留；例如「阿九→阿七」必须阻断，「阿九→玄九」可通过 | `HARD`（`rn_semantic_marker_changed`） |
| R-10.13 | **映射表自身的六条后置条件**（＝下给模型那六条规则的验收面）：① 每个角色都交出决定与理由，`char_id` 与实际下发人物恰好一次对应；`renamed=true` 时 visible canonical 或至少一条该角色实际收到的 alias 必须真实变化（描述 canonical 恒等而带姓 alias 真改合法，全恒等的账面假改名不合法）；`renamed=false` 时 canonical/alias 必须全恒等 ② 每个角色的 `aliases[].old` 与该角色实际下发 alias 精确一一对应（不漏、不多、不重复、不串角色），下发的每一个称呼都有明确去向 ③ **新名全书唯一** ④ 一个旧称呼只映射到一个新名 ⑤ **新名不得已经出现在替换目标里**（成稿+**全部候选文案**+书名——免费改选裁决：对候选全集校验后，候选间改选才无需重做替换），只出现在原作正文里的另报一条提示 ⑥ 称谓/亲属后缀在新形里原样保留（职位与关系不变） | ①②④⑥ `HARD`<br>③ `HARD` + `INFO`，见下方那条<br>⑤ `HARD` + `INFO` |
| R-10.13b | **人手补的映射不受 ③ 的否决，但必须报出它撞上了谁**——同 `R-10.3b` 的形状：这条否决防的是**静默**撞名。⚠ 实测有一次撞名**是对的**（原作对同一个人有两种写法），而硬拦它的唯一出路是撤销一个对的决定 | `INFO` |
| R-10.13c | 人手补的映射之间不许**首尾重叠**（`a` 的后缀 == `b` 的前缀 ⇒ 单次扫描会切出半截残串） | `HARD` |
| R-10.13d | 被**保留**的称呼里不许夹着已经改掉的名字（否则成品里半改不改：「阿禾」成了「阿舒」而「阿禾姊姊」原样留着） | `INFO` |
| R-10.14 | **成书的前置条件**：Stage2 统一机械准入必须通过（CLI、调度与交付检查共用 `novel/orchestrator/mechanical_admission.py` 的同一结论）；人物表必须已定稿且当前 fresh；Stage5 缺章或 `legacy_incompatible` / 各章不出自同一套当前契约 / 正文消费指纹漂移 / **候选集投影漂移**（`hook_consumed.candidates_sha1`——重新生成或改字才触发；候选间改选不算）/ 文案未定稿或其 resolution 不再绑定当前字节 —— 一律拒写 txt。人物表编辑会清空旧批准与 `book_txt` 账并把旧 txt 移出 live 下载路径；下载端点复用同一道 Stage2 准入 + approved/current/fresh 闸，不以“文件存在”冒充可交付。文案定稿闸接受两种可审计基础：`default_policy`（固定槽位序自动定稿）与 `human_selection`（人工改选），resolution 都必须仍绑着当前进成书的那份字节（改一个字即失效）。⚠ 判官语义闸已随 `stage6.semantic` 退役删除；Stage2 语义未审随交付披露该限制，不构成关闭条件；改选后成书在 assemble 免费重放前保持上一版本并明示「新选择生成中」。txt 是交付物，里面没有地方写风险抬头横幅 | `HARD` ×2 + `闸门`（含 `novel/stages/stage6/semantic.py::require_approved_clear` 与 Stage7 delivery gate） |
| R-10.14b | 被拦下的废稿**排除但要报计数**——「排除了 N 份」与「一份都没有」必须分得开 | `INFO` |
| R-10.15 | **人工编辑面按 `char_id` 浅合并**，不要求人重抄整份决策集；不在补丁里的角色**结构上改不到**，且**不许改由人物表派生的旧名** | `结构`（`novel/stages/stage7/measure.py::merge_mapping_edit` 只认白名单 `_CHARACTER_PATCH_KEYS`，`old_canonical` 不在其中，写了就显式终态） |
| R-10.16 | **书名里的人名跟着换，其余不动**（不重起书名）——它走的是同一份替换表，是**确定性应用**不是第二次决策 | **`人`**（`rn_title_unver`：现语料上是**恒等变换**，三本书名都不含人名 ⇒ 这一条**从来没有过失败样本**，只能常驻报出换了几处、不能当绿灯读；换得对不对由人定稿时看），见 `B-22` |
| R-10.17 | **三条书级读数常驻输出**（不是告警，也不许挂在「没问题」那一支上）：替换总账（几条映射、换了几处、篇幅变化）· 新姓字在原文里另有多少次非人名用法 · **成书的写法密度**（正文／文案／原作三列）。⚠ **文案那一列全链只有这里量**——逐章判定够不到它，因为文案不属于任何一章 | `度量` ×3（`rn_report` / `rn_stem_noise` / `rn_style_report`） |
| R-10.18 | **「替换覆盖率查不了」这件事本身要常驻列在报告里**，不是不出现——省略掉，读报告的人会默认它守住了。代码侧只负责把「够不着」写在脸上，把关的是 `R-10.11` 那一下 | **`人`**（`rn_coverage_unver`），见 `B-21` |
| R-10.19 | `stage7.rename --emit-edit-template` 必须 stdout-only 输出 registry 绑定的 `NameMapEdit` v1 骨架，不手抄版本。`stage7.approve` 必须显式提交 actor/reason 与当前公开 v3 workpack 给出的 opaque `review_token=s7r3_…`；调用方不得手算或搬运当前决策面、workpack、代码、规则的多枚 SHA，公开包也不得输出 `code_version` / `rules_fingerprint` 原料。服务端在同书锁内重建 `surname_map + characters + extra_subs` 决策面、review contract 与 validation seal 后核 token；旧 v2 包与 `s7r2_` token 只读，不得批准新决策。服务 s7 proof 保存内部 review identity 与完整 raw typed 审批凭据，并持续复核 effective current；派生 residual/book/book_txt 字节不进 review identity，故 assemble 写交付账不自废批准，真正决策、可见 finding、review contract 或凭据变化仍失效。改名 prompt 实际下发人物 `role` 与 `setting.时代背景`，所以 grounding 消费投影必须包含 role 和真实下发的 era：typed role/era 修订会让旧 name map stale；未下发的 fate/secret/arc/世界观不得反向制造本节点 stale。`--identity` / `--recheck` 在覆盖 provenance 前必须先核旧消费输入：stale/unknown 或决策主体变化令 effective approval suspended，只有 proven fresh 且主体不变才可复活；两条路都清 `book_txt` 并原子退休旧交付物，不能用零调用重写 provenance 洗白旧审批 | `结构`（模板、决策面与免费重写锁在 `novel/stages/stage7/rename.py`，服务 proof 在 `novel/orchestrator/approvals.py`，交付闸在 `novel/stages/stage7/assemble.py`，消费投影在 `novel/consumption.py::stage7_grounding`）+ hash-bound `人` + opaque-token、role/era/identity/recheck/assemble/freshness 回归 |
| R-10.20 | **来源账那几条边的结论要在复检那一步就报出来**，不能只在组装那一步算。`_provenance.inputs`（含 `stage2/global_grounding.json`）一直记着 `file`+`consumed` 双指纹，比对算法也一直在，但此前**只有 `stage7.assemble` 调用它** —— 而人是在 `stage7.approve` 拍板的，approve 在 assemble **之前** ⇒ 一个人可以给「照旧人物表算出来的映射」签字，直到组装才被拒。⚠ 复检这一条是 `INFO` 不是 `HARD`：hard 会连 `--recheck` / `--adopt-residual` 这些 0 成本恢复路一起拦死，而 assemble 那道真闸原样不动。⚠ 三态分开：`stale`（可复算的断言）与 `unknown`（来源账证明不了）不许合成一句 `!= fresh` | `INFO`（`rn_upstream_stale`）+ `闸门`（`novel/stages/stage7/assemble.py` 原有那道）；判定共用 `novel/store.py::ArtifactStore.freshness_of_inputs`，**不另起第二把尺子** |
| R-10.22 | **免费改选承诺**（2026-09-15 全自动裁决）：正文、NameMap、候选集合和组装规则均未变化时，Stage6 三候选之间切换（含 auto→human 的同候选来源更新）的**模型调用数为零**。达成方式是三件同批事实：① rename 对 stage6 的消费投影绑定**候选集合**（`hook_consumed.candidates_sha1`），碰撞校验与新名命中计数的目标面 = 成稿 ∪ 全部候选 ∪ 书名（`R-10.13`⑤），改选不动两者；② 改选后 NameMap 仍 fresh，只有 `stage7.assemble`（白名单确定性节点）与交付终验需要重跑；③ 组装恒从未改名正文与原始候选**全量重放**，c1→c2→c1 往返逐字节无漂移。改选选择本身（`--pick`）仍要求 actor/reason/review-token 与字节绑定 | `结构`（`novel/stages/stage7/measure.py::hook_consumed_of` + `novel/consumption.py::stage7_hook` + `novel/stages/stage7/rename.py::_Upstreams.pre_target` + `novel/stages/stage7/assemble.py::assemble_txt`）+ `tests/test_stage7_hook_reselection.py`（投影不变性、drift、往返逐字节）+ `novel/orchestrator/rebuild.py`（outbox 意图账、busy 排队、启动 reconcile、`GET /api/novels/:sid/stage6/rebuild` 状态缝）+ 强断言：重建路径内触碰模型调用预留入口即失败（`tests/test_stage6_rebuild.py`） |
| R-10.21 | Stage7 人物定稿必须先读取只读、异常优先、hash-bound workpack：公开面为 `GET /api/novels/:sid/approvals/stage7/workpack` 与 CLI `approval workpack stage7`。同一包完整携人物/别名/补充映射、kept 明细、逐旧字面的真实单次扫描命中、完整 residual 上下文与 baseline、替换计数及书级/identity findings；内部保存 review subject/contract 与 validation seal，不让人从分散文件猜。公开提交只接受同一包的 opaque `review_token`、actor/reason，服务端现场重建并关闭式防漂移。approval 与 assemble 派生账不进入 review identity，批准/组装本身不自废。代码、规则或机械来源漂移只令 raw 决定的 effective 投影 `suspended`；免费复检后 subject/contract 未变则 `already_current` 幂等复活，人物决策、可见 finding 或 review contract 变化才回到 `review_required` | `结构`（`contracts/schema/api.py::Stage7ApprovalWorkpack` + `novel/stages/stage7/rename.py::approval_workpack` + `novel/views/stage7.py::stage7_approval_workpack` + `novel/api/routes/approvals.py::get_stage7_workpack` + `novel/cli/main.py::_cmd_approval`）+ hash-bound `人` + API/CLI/页面 opaque-token 与 raw/effective 正反例回归 |

字段与恢复见 `docs/schema/stage7.md`。定向修订必须限制可改范围；漏收一个人和同一人的异写需要不同补法。
残留零命中不证明更名完整：门第、职务与错字可能漏出称谓表，覆盖率分母也可能被主要漏网形反向压低。
这些反例继续约束 `R-10.10`、`R-10.15` 与 `B-21`，不以语料旧数字代替当前验收。

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
| R-11.14 | executor 选择只有一个公开事实源：`GET /api/agent/capabilities`。Executor 模式必须是判别联合 `ExecutorBackend = {kind:'agent', profile:AgentProfile} \| {kind:'legacy', profile:LegacyLlmProfile}`，不得再以 profile 是否存在猜模式；Agent 端由 `AgentDriver` 抽象及 capability registry 关闭 driver/model/effort/mode，Legacy 端由 `LegacyLlmDriver` 抽象及 provider registry 关闭 provider/model/thinking/effort/node，任一失败都不得降级到另一 variant。Claude CLI 的 Anthropic 与智谱路线分别登记为 `claude-code` / `claude-code-glm`，供应商为 `anthropic` / `bigmodel`；智谱路线必须固定并核对接收端，禁止换 base URL 后仍冒充 Anthropic。GLM 5.3 Flash 只发布 low/high/max；上下文后缀仅按登记的等价名验证，原始实际模型与未知 effort 不改写。当前 Legacy 只发布 DeepSeek；以后接入 GLM 必须新增完整 driver/spec/client，不能换 base URL 后复用 DeepSeek 的模型回退、tokenizer、预算或 thinking 语义。`PipelineAgentProfiles.schema_version=3`（2026-09-15 用户裁决：删除 Commander 角色）没有 commander 字段，只含 `executor_default + executor_overrides + concurrency`；v1 裸 AgentProfile 只读迁移为 Agent variant，v1/v2 行里的 `commander` 字段在读边界直接丢弃且原始 append-only 字节不动，需要原 commander 配置比对的历史证明按关闭式拒绝，新写入只发 v3。一次选择内 executor 只允许一种模式（2026-09-14 用户裁决）：`executor_default` 与全部 `executor_overrides` 必须同 `kind`，整跑要么纯 agent、要么纯 legacy，混用两种模式在构造期关闭式拒绝，同模式内的逐节点模型/effort/thinking 差异不受影响。单向豁免（2026-09-15 用户裁决：legacy 整书跑通）：默认 `legacy` 时仅 `contracts/invariants.py::AGENT_ONLY_EXECUTOR_NODES`（producer 只实现 agent node-executor/v1 acceptance、legacy 无执行器的付费节点）允许 agent 覆盖，legacy driver 的 capability 不得宣称这些节点；默认 `agent` 时无任何豁免。该规则是准入规则而非重放判据：append-only 代跑账里存有裁决前合法写入的混用选择，历史行只经 `AgentRunEvent.replay_validate` 读回，准入校验不重审判历史；新写入（preview、启动、设置保存、CLI）仍走 `model_validate` 全额校验。前端不得硬编码选项、拼笛卡尔积或取数组第一项猜默认；`default_profiles=null` 只表示尚无经 TODO-45 整书验收的**产品预置配置**，不是 Agent/Legacy 开关。用户常用配置是另一份 `_service/agent_profile_settings.json` 服务状态，只经 `GET/POST /api/settings/agent-profiles` 读写并由同一 registry 关闭式校验；读取保留 `unset / configured / unreadable` 三态，写入携 revision 做 CAS，坏文件或读取失败不得静默回退产品默认。设置不得保存 API key、CLI 登录态、base URL、代理、付费或正文外发授权；只初始化未来运行草稿，不倒推已启动、排队或历史 run。Agent 可用性保持 `ready / missing / unknown / unauthenticated` 四态，本机探针不发模型请求；Legacy 能力表同样不读取密钥、不联网。R-11.15～R-11.18 中历史 Phase 所称 `executor profile` 只指今天的 Agent variant，不得据此覆盖本条现役联合 | `结构`（`contracts/schema/orchestration.py::ExecutorBackend` + `novel/agents/base.py::AgentDriver` + `novel/llm/providers.py::LegacyLlmDriver` + `novel/agents/capabilities.py` + `novel/orchestrator/agent_settings.py` + capability/settings API 路由；`tests/test_agent_capabilities.py`、`tests/test_agent_settings.py`、`tests/test_orchestrator_nodes.py`、`tests/test_cli.py`、`tests/test_schema_api.py`、`tests/test_agent.py`（历史混用账重放反例）与前端 settings/dialog 回归查） |
| R-11.15 | 显式 Executor profile 必须从 preview/start 的同一计划冻结到 Step、JobStart、argv 与 RunContext，并到达该节点独立 Executor，不能只收配置不生效。可执行 node/flags 组合以 `novel/orchestrator/nodes.py::EXECUTOR_PROFILED_RUNS` 为准，未登记组合在 wire/dispatch 前拒绝，显式 profile 不回退 DeepSeek。配置的真实消费须由 jobs/CLI/节点链路验证；没有真实模型请求时不得宣传质量、速度或可用性实证。 | `结构`（`novel/orchestrator/nodes.py::resolve_executor_profile` + `novel/orchestrator/jobs.py::plan_start` + `novel/cli/main.py::_run_stage2` + `novel/orchestrator/node_agents.py::Stage2GlobalAgent`；`tests/test_orchestrator_nodes.py`、`tests/test_orchestrator_jobs.py`、`tests/test_api_http.py` 与 `tests/test_cli.py` 查） |
| R-11.16 | 直接 job 的 executor backend、完整 resolved profile 与并发策略只按现役 capability 闭集选择，并冻结到 JobStart、producer Step/argv 与 RunContext；自动追加的免费 check Step 不携模型 profile。各章按 node/phase/purpose 隔离 driver 与 session，默认四并发，显式实验可选 6/8，且受每书与供应商总槽位限制。checkpoint、恢复与历史采用必须闭合 profile、请求、输入、回答、session、attempt 和 ledger；未完成或来源未知不能重置预算或自动重购。原配置续跑加载冻结 profiles，能力不再支持时明确停止，不回退默认模型。代码负责 schema、动态约束、整书 HARD、提交与 readback；模型自述不放行，免费补尾不创建模型调用。 | `结构`（`novel/orchestrator/nodes.py::EXECUTOR_PROFILED_NODES` + `novel/agents/unit_session_pool.py::UnitSessionPool` + `novel/orchestrator/node_agents.py::BlueprintSessionPool` / `::Stage2BlueprintAgent` + `novel/llm/stage2_runs.py::write_checkpoint` / `::restore_checkpoint` / `::read_delivery_recovery` + `novel/stages/stage2/extract.py::finalize_blueprint_candidate`；`tests/test_stage2_blueprint_agent.py`、`tests/test_stage2_blueprint_agent_boundary.py`、`tests/test_stage2_recovery_runs.py`、jobs/API/CLI 回归查） |
| R-11.17 | Stage6 双盲语义调用的正文外发与付费分别授权。 `PreviewRequest.egress_authorized` 默认 false；resolved profile 与这枚 bool 必须从 preview/start 同源冻结到 `JobStart`、付费 `Step` 与 argv，只有 true 才加入不属于 `NodeSpec.flags` 的运行控制参数 `--allow-egress`。false 预览可以完整摊开不可执行命令，POST start 与手敲 CLI 则必须在 job、probe、driver 与 wire 之前以 `ApprovalRequired` 拒绝；无 profile 时 egress 快照归一为 null，显式 true 反而关闭式拒绝，不能从 `--yes` 或 profile 推导正文外发授权。执行时 A/B 各自取得不同 driver 对象、logical session、临时 cwd 与 provider session id，同一双盲池的 driver/provider 元数据必须闭合，缺失角色并发请求；每角色验证后 checkpoint 不可变，任一新 wire 前先预检两边，完整恢复零 driver/probe/start/wire，单边恢复只为缺失角色新建 session，绝不伪造历史 provider resume。代码负责匿名响应校验、合并、序号与提交所有权，并在同一临界区按 `prepared journal → 正式 artifact write → typed readback → A/B 两条 adoption → committed` 收口，最后再跑当前输入、代码与交付终验；显式 profile 在通用 dispatch 先于任何旧 `_llm_client`，绝不回退 DeepSeek。 | `结构`（`novel/orchestrator/nodes.py::resolve_executor_egress` + `novel/orchestrator/jobs.py::_require_executor_egress` + `novel/cli/main.py::_dispatch` + `novel/agents/unit_session_pool.py::UnitSessionPool` + 已删除的判官 A/B 双 session 桥 / `::Stage6SemanticAgent` + 已删除的 stage6_runs 判官提交账 / `::mark_committed` + `novel/stages/stage6/semantic.py::require_approved_clear`；已退役的判官 agent 测试（随节点删除）、已退役的判官 agent 边界测试（随节点删除）、已退役的判官 run 账测试（随节点删除） 与 jobs/API/CLI 回归查） |
| R-11.18 | 直接 Executor 的可执行组合统一登记在 `novel/orchestrator/nodes.py::EXECUTOR_PROFILED_RUNS`，未登记组合在 wire/dispatch 前拒绝。producer 都使用固定 typed response，agent 只提交候选，schema 与业务 HARD 由代码判断；其中 Stage4A 不再提交一份整书 response，而由一个节点内私有 pool 依次接收 1 份策略 response 与 N 份单章 response，每份验证后独立 checkpoint，代码再聚合成唯一整书候选。Stage4B 只对歧义章作有界惰性 admission；首个登记的可恢复交付失败立即关闭新 admission、drain 已在途章并留下严格绑定当前 run/code/请求全集的 `delivery_recovery.json`，同 run 恢复只补缺章。已经 ended 的旧 Stage4B run 不得伪装 live resume；新 run 只能在重新复验旧 run/profile/request/schema/checkpoint/session/ledger、当前输入与动态 validator 后，以 immutable `historical-checkpoint-adoption.json` v2 按章采用多来源或完整历史集合，不复制 checkpoint、不续旧 provider session；相同请求的冲突回答关闭式拒绝，相同回答按稳定来源摘要确定，来源损坏保持 unknown。正式逐章 lineage 保留 source-stable 身份，成本保留在 source/target ledger。章级 B 统一使用独立 `aggregate-committed.json`，不要求本轮模型 commit anchor；全机械、全采用及混合来源可由 `--reaggregate` 免费提交。跨 ended-producer 的 prepared 尾窗由 `stage4.plan --recover-prepared <source> --subject-sha256 <proof>` 在审批前免费收口；持 owner-stopped lease 与书级锁，以原 checkpoint/ledger/当前 compiler 重建完整 typed provenance 并执行 baseline/expected CAS，实际新 committer 单独入审计，原 journal 最后 committed。安全的结构化规则元数据只回到对应 logical session。凡登记了正式 primary 的交付都在同书提交所有权内按 `prepared journal → artifact write / retire → typed readback → current input/code/HARD/freshness 终验 → 精确 accepted-attempt adoption → committed` 收口；Stage4 typed infeasible EvidenceBinding 仍是正式 primary 但保持 landed-red，没有正式 primary 的 INVALID / rejected / 耗尽终态不得伪造 adoption/committed，既有 0 成本 revalidate / 恢复路继续保留。恢复必须复核 profile、subject、prompt/schema、session audit 与 ledger，历史 provider session 只作 adoption 身份，不能伪装 live resume；显式 profile 的 dispatch 永远先于 legacy client 构造，绝不静默回退 DeepSeek。 | `结构`（`novel/orchestrator/nodes.py::EXECUTOR_PROFILED_RUNS` + `novel/agents/unit_session_pool.py::UnitSessionPool` + `novel/orchestrator/node_agents.py::EXECUTOR_FACTORIES` + `novel/llm/agent_runs.py::prepare_commit` / `::mark_committed` + `novel/llm/stage4_runs.py::read_plan_delivery_recovery` / `::restore_or_select_plan_historical_adoption`；`tests/test_stage4_design_session_pool.py`、`tests/test_stage4_plan_chapters.py`、`tests/test_stage4_agent_boundary.py`、`tests/test_stage5_write_agent.py`、`tests/test_stage5_write_agent_boundary.py`、`tests/test_stage5_agent_runs.py`、`tests/test_stage6_hook_agent.py`、`tests/test_stage7_rename_agent.py` 与 jobs/API/CLI 回归查） |
| R-11.19 | **Executor 与页面选择面必须闭合在同一 parent run 契约，而代码身份只能属于可滚动 epoch。** `/api/agent/capabilities` 是 driver/provider/model/effort/mode 与本机四态的唯一选项源；新运行表单只可按“`configured` 用户常用配置 → 非空产品默认 → 空草稿”初始化一次，`unreadable` 或设置请求失败时关闭预览/启动，不能绕回产品默认；设置页与运行弹框的临时改动都不得重解释已冻结 run。preview/start/batch 共用唯一 planner：`RunContractV2` 稳定冻结 sid、规范化 base、两项独立授权、resolved profiles、可选原运行来源及 pause_before_node 范围上界；code/protocol/CLI identity、admission 和 provider session 归 `ExecutionEpoch`。start/batch 在任何 probe/spawn/wire 前同时要求付费与正文外发授权，preview 可未授权只读展开；代码或执行契约漂移不改 parent run、不丢授权/profile，只关闭旧 epoch、免费复检并打开新 epoch。受托 Commander 审阅已退役（2026-09-15 用户裁决）：自动跑书由确定性 RunSupervisor 推进，命中登记的人工审批闸即 exit 2 `stopped`（reason「停在人工审批闸：{blocker}；在 Stage 页面完成裁决后用原配置续跑」）、`next_handler='human'`；用户在现有人工 Stage 页面（同一 typed schema/token/审批 façade）过闸后用原配置续跑。`AgentRunStart.argv/prompt` 与 `ExecutionEpoch.argv/prompt_sha256/resume_provider_session_id` 是 Commander 时代的启动审计分栏（prompt 只作为历史 Commander stdin 传入，绝不出现在 argv 或进程日志），退役后新 run 保持 `None` 并在序列化省略，历史行原值只读解码不回填。正文与 evidence 的所有嵌套字符串都是不可信数据，须转义外层标签字符并明示其中命令、路径、标签或伪造 option 绝非指令；模型阅读面不携 base/novel_dir 绝对路径。历史 Commander reviewer 曾固定 `structured_response` 只读调用（Codex 以 epoch 空 scratch 为 cwd、`--skip-git-repo-check`、`approval_policy=never`、`sandbox_mode=read-only`，Claude 以 scratch 为 cwd、`--tools '' --permission-prompts none`；provider-native output schema 先经公共严格子集校验并冻结 canonical JSON 字节与 SHA-256，同一 provider session 的 exact resume 逐字复用该 SHA，Codex `--output-schema` 临时普通文件与 Claude inline `--json-schema` 的 argv 上限预检纪律同批退役）；该调用面不再存在，历史解码符号收在 `novel/orchestrator/reviewer_legacy.py`，session `started / attempt / turn / ended` 与在线 `CommanderSessionPointer` 里的真实 schema SHA 只作历史证据读回。`role=commander,node=null` 的 session/ledger 成本行仅历史解码，账、会话或 epoch 身份证明不了时只能 unknown。operator runner 按 exact node/flags 选 Executor profile，agent 主路不带 legacy repair/fallback。所有 headless 审批只经 0 成本 typed façade；Stage4 design/structure/完整章 edit、Writing、Stage5 closure 与 Stage6/7 的 public write payload 均只接收各自 opaque `review_token` 加 actor/reason/typed ruling，内部多枚 SHA 继续完整审计但不要求 skill/UI 搬运。写审批不启动作业。前端 start 只消费最后一次成功 preview 的原请求快照，capability、配置或授权漂移关闭式拒绝。模型对 scratch 没有写面；服务进程只在 `_service` 内承担上述验证后运输，正式产物仍只经确定性入口与 CLI 子进程 | `结构`（`novel/orchestrator/agent.py::plan` / `::start` + `novel/orchestrator/execution_context.py::load_from_environment` + `novel/agents/session_audit.py` + `novel/llm/ledger.py::append_commander_call` + `novel/orchestrator/run_step.py` + `novel/cli/main.py::_cmd_approval` + `web/src/components/agent/AgentProfilesForm.tsx` + `web/src/screens/settings/SettingsScreen.tsx`）；RunContractV2/epoch/profile/授权/UI/会话/历史 schema SHA 与 argv 早拒回归查 |
| R-11.20 | **确定性 RunSupervisor 是唯一调度者；NODES.lifecycle 是全部现役节点职责的唯一登记。** Executor 只生成候选并在本工作单元内自修，typed 接受、订正、定向返工、不确定或停止只由人在 Stage 页面提交；producer 统一验证、归一、归档、记录来源与正式写盘。ready 在唯一冻结 snapshot 上携 opaque execution token，execute 取得 admission claim 后只做一次活动 surface CAS；blocked 的审批闸一律交人工（受托 Commander reviewer 已退役），登记 deterministic recovery 优先直接交确定性 Executor。推进要求当前有效产物与所有必需前置同时成立，文件存在、已读或 exit 0 均不能替代。Stage2 首次以 Analyze E 联合生成独立 Characters 与 Global；Blueprint 生成前由统一机械准入（`novel/orchestrator/mechanical_admission.py::blueprint_generation_ready`）核当前人物、Global、MainText、称名投影及完整 Blueprint 可读、当前且机械检查全过，准入未过即 blocked 并给出阻断原因；语义结论随 Stage2 语义未审披露，不设审阅席位，修订仅回对应人物或知识 owner，不重新购买联合首抽。Stage4A/B 分别连续审阅各自工作单元，返修后的版本变化不强制换审阅者；两相仍独立。Stage5 闭合后运行独立 Prepare E → Prepare C 接受，再生成 Hook 三候选，Stage6 A/B judge 仍独立互盲，最终 gate.s6 定稿另行闭合。每步完成重新读盘规划，恢复先重放已保存候选、决定和提交 journal；unknown、非本节点 owner、预算耗尽或无进展停止。预算、会话、审批与执行权分离：typed 决定不直接授予 paid permit，付费与外发分别校验。每个 root/follow-check 仍继承同一 permit envelope、独立 step index 与原子 claim；Supervisor 持有 parent owner lease 并 drain 全部 claims/beacons 后才释放书锁。普通写口仅接受 WorkflowManifestV7（stage2-mechanical-admission-v1），独立空根可初始化；V6 及更早 base 不再迁移，旧输出与成本账由冻结旧代码读取；仅登记的 V4→V5、V5→V6 允许在来源冻结代码、原 owner 排空与真实离线证明下显式免费 CAS 推进，保留原 task_id/created_at/profiles/账，prepared/completed 闭合后才开放当前写口。旧版本不自动迁移，已闭合旧回执重放不重开事务。工作单元 ledger v2 只追加不可变人工 BudgetGrant：默认一次、单次一至四次；同书 writer、审批与工作单元锁以及原 owner 停止 lease 内复验主体/代码/来源/成本，相同 intent_id 的相同请求幂等，漂移、未知、未闭合或已有回答/prepared 可免费恢复时拒绝。额度、费用和原轮次累计，增额不解除无进展或语义停点，不执行模型。原配置续跑只能复用原完整 profiles 与来源 hash，在启动锁内复验，重新明确两项授权；缺配置或模型不可用不得回退 settings。 | `结构`（`novel/orchestrator/operator.py::next_action` / `::permit` + `novel/orchestrator/agent_supervisor.py` + `novel/orchestrator/reviewer_legacy.py`（历史决定/席位账解码） + `novel/orchestrator/run_step.py` + `novel/orchestrator/admission.py` + `novel/orchestrator/write_admission.py::operation_scope` + `novel/orchestrator/work_unit_grants.py` + `novel/orchestrator/agent_continuation.py` + `tools/verify_agent_e2e.py::collect`）；单 snapshot execution-token、claim 后单 CAS、现役节点工作单元登记、Blueprint 准入前零调用、E/C 基础四次与显式累计追加、原配置续跑及持续会话、原回答与成本/会话尾账零模型恢复、typed 决定幂等提交和退休、drain、reconcile 与 agent E2E 回归查 |
| R-11.21 | RunSupervisor 是现有 orchestrator 内的**确定性状态机**，不得调用 LLM、不得形成第三个模型角色。Agent 状态独立取 `pending / running / paused / ok / landed_red / failed / unknown`；parent ended 保留原始退出码，并只由 producer 明确记录 `exit_origin=novel_cli / commander / supervisor`（唯一闭集 `AGENT_RUN_EXIT_ORIGINS`，缺失时省略字段，不改 wire_version；`commander` 仅历史行解码，新 run 不再产生该来源）。外部 CLI 的 raw 退出码、parent 状态与盘面 delivery 三轴不得互推；只有 `1 + novel_cli` 投影 `landed_red`，Supervisor、历史 Commander 或旧账缺来源的 1 保守投影 `failed`，不得凭 reason/note 散文推断来源，原 note 保留且仅真正 landed_red 可补已落盘说明；append-only 生命周期至少含 `started / epoch_opened / paused / resumed / epoch_closed / ended / reconciled`。公开 pause/resume/stop API 统一返回 202，只确认请求已受理，不能把 HTTP 返回冒充 lifecycle 终态：pause 先停止接纳新节点，后台等待当前 claim/beacon 安全闭合后才写 paused，不取消正常执行中的付费节点；代码与执行契约 身份未变时 resume 在后台复用当前 epoch；人工审批闸过闸后以原配置续跑，不续用任何 reviewer session；漂移时同一 parent run 打开新 epoch、免费复检并新建 provider session。Stage4 typed repair/replan 是另一条有意的 producer epoch 边界：Supervisor 对 exact `recovery:stage4-design-action + action_sha256` 在本 parent 内只滚一次 `reason=continue`，封存旧 provider、重读 current operator next/CAS 后才交 Executor，Stage2 deterministic recovery 不借此滚动；stop 在后台取消该 run 所有 Executor 进程树、drain 后才写 ended，之后不可 resume。另有一条关闭式的系统暂停：`stage2.blueprint` 或 `stage4.plan` 以 exit 2 结束，且对应 producer 在当前 epoch 留下严格校验、只包含可恢复 Agent 交付失败的 `delivery_recovery.json` 时，Supervisor 保持 parent/epoch 与书锁、追加带缺章摘要的 paused，并由既有 `resume` 控件继续；普通 exit 2、凭据缺失/漂移/损坏仍结束或 unknown，不能借此复活。Stage4B 的 ended 历史 run 采用属于后续新 run 的 producer 恢复，不把已结束 parent/epoch 复活成 paused。内部阻塞 helper 只用于诊断/测试。服务重启后无法证明进程状态必须保持 unknown，不得洗成 paused 或自动恢复；人工 `reconciled` 只能复用 admission（文件沿用历史命名 `commander_admissions`，executor 现役）的 owner-stopped 证明、完整 beacon 主体与 hash-bound CAS，seal 成功后才向同一个 AgentRun v2 事件流投影，崩在两账之间可按同 subject 幂等补尾，不得另造一套恢复状态 | `结构`（`novel/orchestrator/agent_supervisor.py` + `novel/orchestrator/agent.py::_delivery_pause_note` / `::reconcile_run` + agent pause/resume/stop API + append-only event fold）；202 异步受理、Stage2/Stage4B 系统暂停、Stage4 action producer rollover、零 LLM 调度、并发 resume、同/跨 epoch session、stop 永久、崩溃 unknown 与 reconciled 单一 CAS 回归查 |
| R-11.22 | 审批工作包的 `submission_options`（历史 Reviewer workpack 语义）是不可变执行 binding；node/approval 的 actor、review token、范围、hash 与闭集动作均由当前包唯一派生。Stage2 不再设审阅席位或内容纠错预算：其语义结论随 Stage2 语义未审披露，旧 Stage2 席位账只保留只读审计解码，不参与新工作单元。Stage4 A/B、Stage5 closure/disposition、Stage6 Prepare 与 Stage6/7 定稿保留各自审阅 scope 与 typed 权限；Prepare C 仅见完整 MainText、HookCopy 与准备主体，正文及钩子各一次，不消费 Global 或改写正文；独立 native wire 绑定完整 options 与 exact request，模型只给语义决定和可选 PrepareDraft，来源、actor、token 由可信代码注入后再交原 typed loader；机械节点不新建模型审阅。候选 SHA、token、run/epoch 或 session 改变不能重置预算；同单元持续会话可续用，恢复以持久工作包与已保存回答为准，旧 provider 不可用时只重建必要模型上下文。相同错误无进展提前停止，未知执行不得删账后重买。Stage0 发现误剔漏剔可 typed stop；Stage5 trim 的 retain/rewrite 互斥，rewrite 允许有序非空 targets 并逐章下达关闭式 directive，先落正式 request 再独立执行。 | `结构`（`novel/orchestrator/reviewer_legacy.py`（历史 binding/席位账/纠错 prompt 解码） + `novel/orchestrator/stage2_review_ledger.py` + `novel/work_units.py::validate_reviewer_record` + `novel/orchestrator/approvals.py`）；binding、typed stop、业务 identity、fresh session、持久拒因/预算、精确失效、无效 request、exit 2、重启与 epoch 漂移回归查 |
| R-11.23 | 生产 AgentRun 只能从**干净、可复验的冻结 commit**启动或打开新 execution epoch：`preview` 可如实展示 `-dirty.<digest>` 或 unknown 代码身份，但 `start`、批量 `enqueue` 与因 code/identity 漂移触发的 epoch rollover 必须在 probe、spawn、模型 wire 和生命周期写入前拒绝 dirty/unknown。运行中编辑工作仓不是可接受的“热更新”，应在独立 clean worktree 完成跑书；该限制不改变普通开发 job/CLI 的既有边界 | `结构`（`novel/orchestrator/agent.py::_require_reproducible_code_version`）+ preview 可读/start 与新 epoch 早拒回归 |
| R-11.23b | 单本受管运行的可选 pause_before_node 以登记节点为排他上界；preview、start、AgentRunStart 和 RunContractV2 保持同一最终值。Supervisor 在任何当前动作或审批闸前按工作流顺序关闭目标节点及后续节点，返修看实际 owner；blocked 中的消费者节点不替代 waiting_on 登记的人工审批闸 owner，Blueprint 等待 Global 不阻止上界前的 Global 审阅；联合首抽覆盖两侧物化归属，未知关闭式停止。到界先关闭 epoch 并排空、释放 owner，结果为 stopped/范围边界，不伪造 pipeline_complete 或整书交付。continuation 省略字段继承原边界，显式新值或 null 才改变范围；保留原 profiles 和累计工作单元额度。旧记录无边界时不向原 hash 补 null。 | `结构`（`contracts/schema/orchestration.py::RunContractV2` + `novel/orchestrator/agent.py::plan` + `novel/orchestrator/agent_supervisor.py::range_boundary_reason` + `novel/orchestrator/agent_continuation.py::inspect`）+ Supervisor 零派发、HTTP 持久读回、续跑省略继承/显式扩大反例 |
| R-11.24 | 运行页必须把三种事实分开呈现：节点 beacon 给“当前正在执行什么”，operator 同快照给“下一节点/下一处理者”，AgentRun lifecycle 给“当前或最后 execution epoch、rollover 原因与累计换 epoch 次数”。空 beacon 不得把下一节点伪装成正在执行，epoch rollover 也不得藏在日志里；三者都只呈现后端事实，不由前端推断新鲜度或调度 | `结构`（`web/src/screens/run/RunScreen.tsx::ResumeFrontier` / `::AgentRunBlock`）+ 前端回归 |
| R-11.25 | 受托 Commander 审阅已退役；本条的持久化语义继续约束存量书的历史决定与 Stage5 reviewer rewrite 执行链。历史/Reviewer 决定先持久保存原始回答，再通过同源 typed loader，把可执行决定按内容寻址写入 `_service/edits/<sid>/reviewer-action-<sha256>.json`，之后才取得当前 permit 与调用 canonical producer；现役人工审批只经 typed façade，不产生模型决定记录。新决定必须携 `work_unit_id / work_unit_turn_id`，并以工作单元原响应、business、waiting_on、typed action SHA、原物理 session 与 provider response digest 联结；恢复窗口的 run/epoch 包装变化不改变真实调用所属 parent。工作包、请求、原始响应和 typed 决定分别落盘；缺失或损坏保持 unknown，不能重新发放内容预算。`NODES.lifecycle.review_actions` 登记 accept/编辑/rework/uncertain/stop 的节点实际子集，`approval_projection` 只投影同一 operator 快照的有效审阅原状态。`ReviewerStopAction` 的 stop/uncertain 均只持久保存本次响应与明确原因，不铸可执行 action 或 permit，也不当作格式错误继续纠错。已保存响应优先免费重验、补提交；Blueprint 与 Stage6/7 的 business 绑定审阅主体、裁决契约与当前义务，代码版本及 validation seal 只守执行和提交竞态，不因代码修复重购。原请求、原回答、原 owner/session/cost 闭合后，只有实际阅读材料、裁决说明、输出 schema 及可选动作未变，才可更新登记的临时提交凭证并用当前 loader/producer 免费重验。首次 typed 证明不可改写；后继 action 仍引用原内容轮且决定、理由、编辑值相同，先保存并核验后继再退休旧记录，中断可补尾。格式/内容拒绝需先保存反馈再关闭重放，同一错误无进展提前停止。canonical exit 0 后标记 adopted；exit 1 表示已落盘但未通过，exit 2 表示明确拒绝，exit 70 表示未知，不能互相代替。Stage6 Prepare 原回答、独立决定与免费编辑也先耐久，再按本 owner 冻结基线 CAS 发布；任何 paid/free 提交窗口优先走 --recover-prepared/--recover-edit，必须由完成 journal 与当前目标证明原 C 已采用，不能凭 actor 或 exit 文案猜测。Stage5 retain 整批与有序非空 rewrite targets 互斥，其 review/contract、audit/章 SHA 和关闭式 directive 仍精确绑定当前工作包。rewrite request 的永久 authority 保留原 action 字节、request raw/self SHA、原内容轮、唯一成功 provider attempt、对应 logical-session turn 与成本账；新工作单元封存只绑定该次不可变响应证据，后续连续审阅或 delivered→adopted 不得破坏已有 seal，不要求整个 logical session 提前结束。跨 parent 补提交必须持真实内容调用 owner-stopped lease 直到 seal 完成，不能用新的 record 包装冒充原 owner。历史旧席账和协议恢复只供冻结旧契约的审计与原证明复读，不参与新工作单元运行或预算重置。 | `结构`（`novel/work_units.py::validate_reviewer_record` / `WorkUnitBudget` + `novel/orchestrator/reviewer_legacy.py`（历史 request 文档/席位账/协议恢复解码） + `novel/orchestrator/reviewer_action_evidence.py::validate_stage5_action_record_ledger` / `::_closed_evidence_hashes` / `::seal_reviewer_rewrite_authority` + `novel/orchestrator/uploads.py::load_reviewer_actions`）；原回答/typed决定/提交分离、跨parent、无模型补提交、未知保留、连续审阅历史seal与预算不重置反例 |
| R-11.26 | **agent CLI 共用一层可恢复 JSONL 子进程调用（`novel/agents/jsonl_runner.py`），网络/瞬态故障在同一内容请求内续接，不扣新内容轮。** 公共 runner 只定义一次进程组创建、三线程泵、按 owner 的 cancel、幂等 close 与 Popen 后 launch 回调；stdout 逐行流式解析、协议事件即时回调、首事件在线 session 指针、三档超时与同席 exact-resume/relaunch 是历史 Commander 审阅链的会话语义——首事件 `session_id` 到达即写 `CommanderSessionPointer`、`COMMANDER_FIRST_EVENT_TIMEOUT_SECONDS` / `COMMANDER_IDLE_TIMEOUT_SECONDS` / `COMMANDER_MAX_ATTEMPTS_PER_SEAT` / `COMMANDER_RETRY_BACKOFF_SECONDS` 常量族已随角色删除，账上指针与超时记录只读解码。现役 executor 协议不做流式解析，只保留总超时并把公共异常映射回本层 typed 错误。非 0 退出与超时都经 executor 侧同一分类器（`classify_agent_command_failure`）得到 `failure_kind / retryable / wire_evidence`：可重试且已捕获 provider session 的失败在同席 **resume**（Claude `--resume`、Codex `exec resume`，续接提示不重发工作包），0-wire 启动失败在同席 **relaunch**，每席按登记上限执行真实进程尝试并退避；authentication / usage_limit / permission / 协议错 / model drift / provider session 被他人占用一律不重试。Codex/Claude 已提供的当前结构化失败是分类权威；未知或空的明确失败仍是未知，不拼入无关 stderr 警告，也不借模型回答或此前已恢复的错误推导重试。只有缺少结构化失败时才使用 CLI stderr。已实测的完整模型容量不足拒绝归既有 `provider_transient`，沿用同席上限与退避，不换模型、不返还席位。每次 attempt 都逐次落成本账（`attempt_id` 唯一）、`agent_sessions.jsonl` 的 `attempt` 事件（席内序号 `delivery_attempts`）与对应工作单元内容轮的 `attempts[]`；已取得 provider session 的失败尝试再记一条带 `error_kind` 的 provider turn，同一 logical session 的 `turn_index` 严格递增。内容轮预留、`reserved`、launch ticket 只属于第一次尝试；宿主崩溃留下的 `reserved` 仍按 R-11.22 不返还 | `结构`（`novel/agents/jsonl_runner.py::JsonlProcessRunner` + `novel/agents/base.py` 默认可取消 runner 与 `classify_agent_command_failure` + `novel/agents/session_client.py` + `novel/agents/session_audit.py::fold` + `novel/work_units.py::WorkUnitBudget.record_attempt` / `::WorkUnitBudget.save_response`）；owner 取消、分类与部分观测、同席 resume/relaunch/上限/不可重试、attempt/response 逐次落账与历史指针文件回归查 |
| R-11.27 | **确定性重建白名单**（2026-09-15 全自动裁决）：后置人工决定（如 Stage6 文案改选）触发的自动重建，只允许进入 `contracts/invariants.py::DETERMINISTIC_REBUILD_NODES` 登记的节点（全部 `paid=False` 且 `generation_mode='deterministic'`）加只读交付终验；任何自动重建路径不得触发付费节点，也不得借道整书 start「顺便补齐上游」。白名单钉扎测试双向守卫：混入付费节点即红，白名单清空即红（分层被整体绕开同样不可接受） | `结构`（`contracts/invariants.py::DETERMINISTIC_REBUILD_NODES` + `tests/test_invariants.py::test_deterministic_rebuild_whitelist_is_free_and_deterministic`）+ 白名单收缩/清空反例 |

以下只保留跨层顺序与拒绝边界，字段清单不在此重复：

- admission 与 outer AgentRun 分开；owner lease、beacon 与 reconcile 不能猜关联。reconciled 保留 unknown，
  不伪造 ended、退出码或 drain；完整证明与 CAS 见 `tests/test_commander_admission.py`、`tests/test_agent_reconcile.py`。
- 原回答和 typed 决定先 durable，执行权另取 current permit。Stage5 rewrite 先物化 canonical request，再封存
  原 action 与 authority receipt；receipt 是提交点，最后退休 active action。仅有 archive 或 receipt、坏账、
  跨 parent 无真实 owner-stopped 均拒绝，见 `tests/test_reviewer_work_unit_evidence.py`、`tests/test_agent.py`。
- rewrite execution 在 driver/wire 前写 request journal；只按 latest intent 的 exact checkpoint 恢复。
  terminal 后的免费重验/编辑先写 successor prepared，合法后继或明确 consumption 前不替换请求。
  Stage5 partial batch replay 的 exit 2 不得删掉唯一 action 后重买；先保留证据并停止。
- Stage5 trim 的 retain/rewrite 共用原 Review、完整有序 manifest 与 contract 的业务身份；逐项写回导致 token
  自变不重置内容额度。Stage4 replan 的 typed stop 不产生审批或执行权，原拒绝保持，见 `tests/test_reviewer_approval_landed.py`。
- 确定性 prepared 补尾优先于新审阅，且只消费唯一来源及其 exact CAS；同时存在互斥 prepared 必须拒绝。
  历史 journal 仅作为被选中恢复动作的 external input，不把历史全树加入活动身份。
- Stage4 紧凑 wire 只压缩运输，不能减少合法 submission option。actor、范围、token 与 SHA 由同一 immutable
  request 可信注入，完整候选仍经现役 loader/producer；重复 JSON 键、越权 ID 和重叠操作必须拒绝。
  纠错空项的保留/撤销语义只由正式 failed edit 绑定开启，不能从 ledger 的 correction 标签推断。
- schema 字节绑定同一 provider session；已保存 canonical 决定的重放不重新经过 compact wire。登记协议恢复
  只按原日志和完整身份复验，不改变模型判断、不返还预算、不把冻结旧席账用于新工作单元。
  历史解码边界见 `novel/orchestrator/reviewer_legacy.py`（其复验链由 `tests/test_reviewer_work_unit_evidence.py` 等历史证据测试覆盖）。
- 内容轮预留在 Popen 成功之后、stdin 发送之前；本地预检或 Popen 失败不扣内容额度，
  已预留后的崩溃或未知仍计入。当前追加只用不可变工作单元授权，不退款或重开旧账。
- 模型回显必须与冻结 profile 一致；无回显仅在已登记 driver-enforced 且 start/resume 钉死型号、留有 CLI 版本时
  采用 requested identity，actual_model 与 ledger 未知项不倒填。所有路径共用
  `novel/agents/model_identity.py`，反例见 `tests/test_agent_model_identity.py`。
- 费用与会话按真实 parent/producer/session 关系归属，不按时间猜。外发授权逐项点名实际可达的 provider、
  driver、model、effort 和模式，不授权被 override 遮蔽的默认项，凭据不进入材料或日志。
- 服务使用 CLI 子进程隔离 CPU 与崩溃，预览和执行共用 argv；服务只写 `_service/`，事件只追加。
  出站 prompt 和输出产物是两道检查面，不能用产物扫描代替材料边界。反例见 `tests/test_layering.py`、`tests/test_api_discipline.py`。

### R-12 · 编排与作业执行

按冻结命令执行、记录真实成本与显式终态；响应形状和运行身份必须在继续消费前验证。

| # | 要求 | 谁在查 |
|---|---|---|
| R-12.1 | **编排只表达真实生产依赖与接受边界。** 满足 cold-start 证明的新书先由一次 Analyze Executor 联合生成两份草稿，各自编译为独立人物与知识产物；Blueprint 由统一机械准入放行后免费汇合进入 Stage4。已有任一侧产物、历史回答、checkpoint 或 prepared 时先恢复或修订对应 owner，不能仅凭正式文件缺失重新首抽。独立 Global 修订请求消费必要人物 canonical/surfaces/role 投影；联合首抽来源如实绑定完整联合请求，不把它假称窄投影调用，也不把整个响应挂为后续可变人物依赖。Blueprint 在 driver/cache/wire 前要求统一机械准入通过：当前人物、Global、MainText 与称名投影可读、当前有效、无未恢复 INVALID 且所需机械检查全部通过；语义结论随 Stage2 语义未审披露，不再要求审阅接受。编排按准入阻断原因把恢复指向对应 owner 的编辑/复验入口。人物实际知识投影改变时，只由 `novel/stages/stage2/character_rebind.py::prepare_if_changed` 证明其余输入未变、来源完整且外键闭合后排 `stage2.global --renormalize` 免费重绑；原模型/成本保留，unknown 不追认。人物投影缺失或损坏、人物正式登记 current 时只排 `stage2.characters --revalidate` 免费重建；live INVALID 保持 unknown 并交同 owner 编辑/复验，不因缺投影默认付费重采。Stage4 首项要求统一机械准入通过（`stage4_ready`）且 Blueprint 联合 HARD 通过。Stage2 唯一显式 `--check-only` 仍是 Blueprint，机械测量由 Stage5/6 在真实消费点即时执行，不造队列节点。 | `结构`（`contracts/invariants.py::NODES` + `novel/orchestrator/mechanical_admission.py::blueprint_generation_ready` / `::stage4_ready` + `novel/orchestrator/queue.py::plan_book_from_overview` + `novel/stages/stage2/analyze.py::cold_start_reason` + `novel/stages/stage2/grounding.py::read`）+ CLI/API 零 wire 早拒与联合复检回归 |
| R-12.1b | 节点要诚实报出「这条规则本次没有跑，不是跑过没事」，且**流程里必须钉死一次 0 成本复检**。⚠ 别拿「这次补跑是绿的」当结论——那是运气，不是保证 | `HARD` |
| R-12.1c | legacy LLM transport 的 Stage2 逐章抽取仍按章全量并发、硬上限 32；生产 Agent 路径改用独立逐章 session pool，实际取 `min(4, NOVEL_CONCURRENCY, 待跑章数)`。Blueprint pool 在统一机械准入通过后启动，准入未过不产生任何 Blueprint wire。Stage4A 是另一份节点级私有 session pool：策略请求串行先行，策略 checkpoint 后的逐章设计实际取 `min(4, NOVEL_CONCURRENCY, 待跑章数)`；配置可以把单书池下调但不能放大到 4 以上，也不复用/改写其他节点的产品上限。任何并发完成顺序都不得串错逐章 usage/provenance/checkpoint | `结构`（`contracts/invariants.py::STAGE2_BLUEPRINT_MAX_CONCURRENCY` / `::STAGE4_DESIGN_MAX_CONCURRENCY` + `novel/model_slots.py` + `novel/agents/unit_session_pool.py::UnitSessionPool` + `novel/orchestrator/node_agents.py::BlueprintSessionPool` + `novel/cli/main.py::_dispatch` + `novel/llm/stage4_runs.py::DESIGN_MAX_CONCURRENCY` + `novel/llm/ledger.py::chapter_usage`）+ 共享槽峰值/槽位归还/配置 1 串行/逐章账回归 |
| R-12.1d | 供应商限流、账号 entitlement 与产品/节点并发闸必须分开陈述：legacy Stage2/5 transport 项目硬上限是 32，Stage2/Stage4 的 Agent 逐章 session pool 上限分别是 4/4；DeepSeek V4 Pro 当前官方账号级上限是 500。Agent pool 的 4 是当前 Claude executor 的**本进程、单书节点安全边界**，依据同一 executor 在 Stage2 真实整书 13 路首波出现快速 403 拒绝后的保守收敛，不冒充 Claude 账号级通用上限。HTTP 403 单码也不能推出五小时额度或账号总限：必须再区分明确用量窗口、登录过期、永久权限与“`Request not allowed` + 已确认 0 token”的瞬时拒绝。它只约束对应单书节点池；多书 agent 运行的总并发仍由独立的 `novel/orchestrator/agent.py::MAX_CONCURRENT` 管，不得把 4 写成账号总闸。正常产品路径不为制造 429/403 越过自身硬闸。真实观测必须显式确认付费、禁缓存、限制请求/重试/token，并保留逐次 ledger 与不含 prompt/响应正文的报告；429/瞬时 403 的重试/退避由可重复合成故障守卫 | `结构`（`tools/observe_provider_limits.py` + `novel/agents/base.py::classify_agent_command_failure` + `novel/model_slots.py` + `novel/agents/unit_session_pool.py::UnitSessionPool`）+ 2026-08-16 DeepSeek 真实 32 并发观测 + Stage2 Claude 13 路首波拒绝实证 + 合成 429/403/退避回归；DeepSeek 供应商口径见 [官方限速说明](https://api-docs.deepseek.com/zh-cn/quick_start/rate_limit) |
| R-12.2 | **结构化调用点必须声明全部必需顶层键**；parse / require / schema / validator 任一闸失败都驱逐坏缓存，修复稿始终重走 `json.loads → require → model_validate → validator`。禁止异常驱动地降低 effort / thinking，也不改供应商或 schema mode；Flash→Pro 保持同 effort / thinking / max_tokens。Stage2 blueprint 使用按失败类型登记的恢复策略：reasoning 中完整 JSON 一旦通过全部闸立即采纳并铸 checkpoint，不再为正常 content 多发一采；BOM / 容器尾逗号只做窄本地修正并全量复验；有可忠实恢复材料的 `answer_lost` / parse / require / schema 首次进入本 run 唯一 batch，修复拒绝或未通过后最多一次 Flash 定向修复、再最多一次 Pro constraint rebuild；dynamic validator 不交给通用 JSON 修复，只走一次 Stage2 定向恢复与一次 Pro rebuild；无有效材料的 `answer_lost` 不重复相同 Flash 原请求，直接 Pro rebuild；`finish_reason=length` 不做同配置重采，进入下一种已登记动作，仍失败即终态。传输交付 attempt 与内容/语义修订 turn 必须分账：Agent 对 429、408/425/5xx、已识别连接/timeout、明确模型容量不足，以及“403 `Request not allowed` 且 token 明确全为 0”执行首发加最多三次的 2/4/8 秒有界退避；四次逐项进入 ledger，但没有可采纳回答的 attempt 不进入 session turn/checkpoint `attempt_links`。五小时用量窗口与登录过期不秒级热重试而进入可恢复等待，其他 403 按权限错误立即终态；legacy transport 的不可重试 4xx、配置错误、预算拒绝仍立即终态。失败签名统一绑定 request fingerprint、模型/effort/thinking、request mode、finish reason、gate 与归一 reject detail；同一 `run_id` 内相同签名不得重复相同动作，只能升级或结束。其他结构化调用继续使用各自已登记的通用恢复链；失败候选一律视作不可信数据，动态恢复 prompt 仍按完整候选/诊断摘要/最小诊断逐份预检并记录 `request_mode` + `recovery_from_attempts`。来源账的 adopted model 只认 accepted attempt → ledger 精确联结；拿不到联结就拒绝落盘 | `HARD`（`novel/llm/client.py::ask_json` + `novel/agents/base.py::classify_agent_command_failure` + `novel/agents/session_client.py::_invoke` + `novel/llm/response_repair.py::failure_signature` + `novel/llm/stage2_runs.py`）+ reasoning 单 wire/失败类型路由/交付退避/动作去重/模型来源账回归 |
| R-12.2a | 所有 Agent executor 的响应 schema 必须在公共构造口归一并通过 Claude/Codex 都可接收的严格子集，再由同一份 canonical 字节计算 identity、schema SHA 并交给具体 driver；禁止 driver 私自删改。根必须是 object 且不得为联合，每层 object 必须 `additionalProperties=false` 并把全部 `properties` 无重复列入 `required`；`discriminator` 只作分支注解予以删除，`oneOf` 等价归一为 `anyOf`，单值 `const` 等价归一为单元素 `enum`，`default` 注解删除且对应键仍必须在线上显式返回，其余子集外关键字在拉起 CLI 前关闭式失败。业务动态键映射不得发布开放 object：必须设计成带显式 key/value 的行数组，Pydantic 收到后确定性还原为既有内部 dict，重复键拒绝；正式 artifact 与新 checkpoint 的内部 payload 形状不变。旧 checkpoint 不迁移、不改字节；因 schema SHA 是恢复身份，协议变化后必须关闭式拒绝旧 restore 并由新 run 重建。任何 provider 的 `invalid_json_schema` 都归类为不可热重试的 `agent_schema`，不得洗成普通 `agent_command` 或连发四次 | `结构`（`novel/agents/session_client.py::agent_json_schema` + `novel/agents/base.py::validate_agent_output_schema` + `contracts/schema/agent_wire.py::keyed_rows_to_mapping`）+ 全部现役响应模型词表/object 闭合/动态映射 roundtrip/Claude 与 Codex 共闸/错误分类回归 |
| R-12.2b | 下游节点在开始逐条报症状之前，先判一次「上游这份东西是不是整个空的」 | `HARD` |
| R-12.2c | Claude agent 的结构化请求保留 provider schema gate（`--json-schema`），最终 stdin 必须且只能附加一次版本化 delivery instruction：只经内建 `StructuredOutput` 交最终对象，不先打印普通 JSON、不套 `response/result/structured_output` wrapper、不调用其他工具。有 output schema 时成功信封缺 `structured_output` 必须关闭式失败；只有 schema-less 请求可读取字符串 `result`。prompt SHA、request fingerprint 与 checkpoint/恢复守卫必须共用 `AgentRequest.stdin_bytes()` 的真实 UTF-8 字节，生产子进程必须以 binary stdin 原样收取，不得另拼一份或经文本换行转写；Claude 内部 `num_turns` 只作读数，不得被误当成「单次提交」闸门 | `结构`（`novel/agents/base.py::AgentRequest.stdin_bytes` + `novel/agents/claude_code.py` + session/checkpoint 共用渲染落点）+ footer 唯一/错误 wrapper/缺 structured_output/schema-less fallback/raw-stdin/hash 对账回归 |
| R-12.3 | 子进程环境三件套（UTF-8 相关）缺一不可：缺了它，任何打印 `✓` 的节点进管道就非零退出——而一次**成功的付费跑**会被报成失败 | `HARD`（回归测试） |
| R-12.4 | 普通缓存仍有三条语义：关缓存是**同时关读写**；参数不变的重跑可以零 wire；坏响应不得留在可命中的内容缓存。验证后 checkpoint 是第四种、明确独立的恢复状态：命中不记 cache hit，不产生新 wire，保留原 accepted attempt/model/成本联结并发出 `checkpoint_restored`；只允许同 `run_id`、同代码/契约/架构/输入/request/SHA 与闭合 ledger 恢复。Stage2 按章 checkpoint；Stage4A 先铸策略 checkpoint，再为每个已验章节铸独立 checkpoint，策略或任一章失败不得抹掉其他已验 unit，同 run 只补缺失/失败单元。Stage4 checkpoint 不是正式 artifact：N+1 单元齐全后仍须确定性聚合、整书 schema/HARD 与 commit journal 原子提交，之前不得出现部分 `blind_candidate.json`。主动 `--resample` / `--chapters` 首次执行不得命中旧 checkpoint；Stage4A 不开放公开 `--chapters`，恢复只由同一公开 node/run 自动发现 unit checkpoint。canonical `RunContext.run_id` 在同一 producer identity 内覆盖一次首轮或 repair/replan epoch 的请求 → 响应修复 → resume → 聚合提交，并进入 ledger、LLM audit、repair、checkpoint 与 adoption；AgentRun 跨代码 epoch，或在 Stage4 typed repair/replan 动作边界滚动 fresh epoch 时，均按 R-11.19/R-5.26 换新 producer identity，只由显式 `parent_run_id` 维持父子关系。`agent_progress.jsonl` 是外层 execution beacon，继续使用 parent run。job/queue/agent/chain/attempt id 保持各自含义只建父子关系。手敲 CLI 首次自动生成并打印，resume 必须显式 `--run-id` 复用；历史 JSONL 无 run_id 仍可读但只标 legacy/ungrouped，不回填、不按时间窗口猜。本字段不进正式 artifact provenance、缓存键或贵贱判断 | `HARD`（`novel/run_identity.py` + `novel/run.py::RunContext` + `novel/llm/stage2_runs.py` + `novel/llm/stage4_runs.py` + ledger/audit/progress/adoption 联结回归） |
| R-12.4b | `R-12.4` 的“只允许同 run”约束专指会恢复当前 logical/provider session 的普通 checkpoint hit。`R-5.26b` 的 Stage4A targeted-repair bridge 与 `R-11.18` 的 Stage4B bridge 是显式的**跨 run 响应采用**，不是 session/checkpoint restore：source 始终只读，target 不复制 checkpoint、不续旧 provider，会以 immutable manifest、source-stable lineage、当前 validator 与分 run ledger 单独证明；表外节点、策略 unit、普通首轮和 replan 仍不得借此跨 run。`R-5.26c` 的 A 和 `R-11.18` 的 B prepared recovery 是另一种跨 ended-producer **提交补尾**：它只重验并提交 source journal 已 sealed 的 aggregate，不采用新响应、不复制 checkpoint、不续 provider，因此不放宽 replan 禁止 historical response adoption 的边界 | `结构`（`novel/llm/agent_runs.py::restore_historical_run` + `novel/llm/stage4_runs.py` 的 historical adoption 与 repair aggregate recovery）+ ordinary restore/cross-run adoption/commit-tail 正反例回归 |
| R-12.5 | **发出去的 prompt 里不许出现内部结构名**（拍号 `bNN_NN` / 人物 id `char_001` / 规则 id `wr_01` / 内部枚举名 / 节点名）。每层只说自己的语言：prompt 只讲任务，不提节点名、不提消费方、不提归属 | **无守卫 → `B-05`** |
| R-12.6 | **每一份最终逻辑请求先过对应的精确上下文预算，再碰其缓存、外部请求、ledger 或正常产物。** Stage4A 的策略请求与每章请求分别是登记在架构中的最终请求，必须逐份预检；`stage2.global`、`stage4.plan` 与所有统一 `LlmClient` 调用同样按本次最终 system、user 与官方消息 framing 精确计 input tokens。原始 Flash 请求及同 prompt 的 Pro 兜底必须在该逻辑调用首个副作用前同批预检，任一项拒绝即该逻辑调用 0 副作用。R-12.2 的定向恢复 prompt 依赖尚未发生的响应，不能在首发前伪造“最终内容”：它在每次后继采样前按完整候选 → 诊断摘要 → 最小诊断逐份精确预检，只允许读取/发送第一份通过者；全拒时退回已经通过首轮预检的原请求，不能在已经付费后新增一个预算终态。`deepseek-v4-pro` / `deepseek-flash` 的保守有效上下文上限固定为 1,000,000，单次输出上限 384,000，且要求 `input_tokens + max_tokens <= 1,000,000`。tokenizer 固定为 DeepSeek V4 官方 revision `0e1a0e5e52aea73055f50fef6f2423db370265b6`，资源 SHA-256 `8f9f37ca37fdc4f5fd36d5cf4d3b0e8392edb4e894fd10cc0d70b4957c8633cf`；未知模型、依赖/资源不可用或 hash 不符全部关闭式拒绝，不用字符比例猜测。Stage4A 的策略 + N 单章是**语义契约预先登记的聚合拓扑**，不是预算失败后的动态分块；任何一份已登记请求仍不得截断或临时再拆，预算拒绝本身不触发模型兜底。错误必须给出模型、tokenizer 版本、输入、声明输出、合计、两项上限及缩减正文的行动建议 | `结构`（`novel/llm/budget.py` + `novel/llm/client.py::_preflight` + Stage4 agent request 预检）+ 首发零副作用/动态恢复预算降级/边界/Stage2/Stage4/CLI 回归测试 |
| R-12.7 | 全链身份必须显式拆为四类：`review_subject_sha256` 覆盖人实际看到的内容、证据与 finding，`review_contract_version` 只表示影响审阅语义的显式契约，`validation_seal` 表示当前代码/规则下的免费机械复检，`execution_epoch` 表示本轮 code/profiles/admission/provider 上下文。人工决定的适用性只由前两类决定；代码变化先令 effective 投影暂停，新 validation seal 通过且 subject/contract 未变时 raw typed decision 自动复活。审批盘必须永久保存 raw 决定，前置闸失效只产生只读 effective 投影，禁止把级联 false 覆盖回 raw proof；任何人工修改从 raw record 出发。Prepare 使用独立主体与接受记录，不复用 gate.s6；Stage5 闭合是生成准入而非 Prepare 模型业务输入，MainText/HookCopy 的实际材料变化重开相应准备审阅，证据与审计变化不扩大为无关重买。Stage6/7 公开 gate 必须在既有前置链闭合后从 producer 的 current typed 定稿投影，不能以附加 API 检查单是否盖章作为第二次批准；GET 不补账、不重签，缺失、过期或校验失败不得投影 true。整书验收仍独立核对 producer 真相，并检查公开 gate 与它一致，不能用一面覆盖另一面的矛盾。所有现役人审写入口——Stage2 Blueprint/关系编辑、Stage4 design/structure/完整章 edit、Writing、Stage5 closure、Stage6 Prepare 与 Stage6/7——对外均只收一枚 opaque review token 加 actor/reason/typed ruling；execution permit 同样只暴露 opaque execution token。完整 subject/contract/validation/workpack SHA 仍在内部审计并由服务端重建，调用方不得建立第二套 public hash builder | `结构`（`novel/orchestrator/approvals.py::_read_state` / `::_effective_record`、`novel/orchestrator/inspection.py::capture_operator_snapshot`、`contracts/schema/orchestration.py::ExecutionEpoch`）；前置失效/复活、可见 finding 精确过期、代码漂移不重签和全 token 表面的 TOCTOU 回归查 |

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
| R-13.5 | 需要人裁决的闸门会挡住队列并明确说出在等谁。Stage2 的顺序由统一机械准入表达：`stage2.blueprint` 在准入未过时保持 blocked，blocker 携带 `novel/orchestrator/mechanical_admission.py` 给出的阻断原因（必要产物缺失、INVALID、过期或机械检查不可判）；没有审阅 blocker，也没有 `gate.s2`。准入结论由 CLI、调度与交付检查共用，队列不复述第二套 Stage2 条件。Prepare 待审、INVALID 或材料已变时停在独立 approval:stage6-prepare，未接受不得购买 Hook；已保存 Prepare 提交先发布精确免费恢复动作，人工页、inbox、operator 和执行许可共用该动作。其余 stage0 批准、Stage5 全书写作授权、文案定稿、人名表定稿与待人工非空照常阻塞；没有 current `writing/writing_proof` 不拿只读默认值猜授权。Stage6/7 定稿也不得只看盘上 `approved != null`：队列必须复用各节点的 production `approval_is_current`，要求声明仍绑定当前业务主体、裁决契约与实际审阅材料；代码与校验凭证变化先免费重验，不单独使旧决定失效。仍无效的旧声明必须先形成对应 `stage6.approve` / `stage7.approve` blocker，使 exact recovery permit 可达，不能先暴露一个下游 ready 动作、再由下游拒绝旧批准而形成路由死锁。普通 durable 多步队列遇到 `redo/unknown` 时必须形成无命令的 `resume:manual-decision`，不能把冻结快照上的“要重来/判不了”伪装成 runnable；唯一例外是 AgentRun 单步 operator：有正式产物的 producer 仅当每份 stale 输出的权威汇总 reason 都是 declared projection 的 `consumed_changed`、节点在 `NodeSpec.operator_redo` 登记唯一策略、且没有 unreadable、盘上既有却 `unknown` 的输出或 INVALID 时，才可导出 canonical exact flags；同一 producer 的缺失输出可以由该 canonical 动作一并补齐。另允许专用 currentness 已明确为 stale 的 Stage5 Review 走登记的零成本编号重算。两类都须铸同快照 execution token，每落一步重新规划；producer 汇总为 `file_changed` 与任何盘上既有却 `unknown` 的输出永不走该例外 | **`结构`**（`novel/orchestrator/queue.py::plan_book` / `_human_gate_satisfied` + `novel/orchestrator/mechanical_admission.py::blueprint_generation_ready` / `::stage4_ready` + `novel/orchestrator/state.py::operator_redo_flags` + `novel/orchestrator/operator.py::_next_from_snapshot` + `novel/stages/stage6/hook.py::approval_is_current` + `novel/stages/stage7/rename.py::approval_is_current` + 普通队列关闭、AgentRun Stage2/Stage5 exact REDO、Stage4 阻塞与 Stage6-7 current-workpack 回归） |
| R-13.6 | 队列本身**不产生新的判定**，也不放宽任何落盘闸门 | **`结构`**（`tests/test_layering.py::test_the_queue_cannot_reach_the_judgement_layer`：`queue.py` 够不到 `novel.report`，连「顺手判一下」都做不到） |
| R-13.7 | 不同小说的作业可以并行执行；互斥范围只落在各自 `sid`。每个后台作业独立启动 CLI 子进程，一本书失败或阻塞不占用另一本书的键 | `结构`（`novel/orchestrator/queue.py::_execute` 每书一个线程，调用 `novel/orchestrator/jobs.py::start`；互斥仍唯一落在 `novel/orchestrator/jobs.py::active_key`）+ barrier 证明两本书同时进入子进程的回归 |
| R-13.8 | 正常主路的权威时间线由确定性 RunSupervisor、agent lifecycle events、admission claim、node beacon 与付费 ledger **自动形成**；Executor 不得在 prompt 或正常主路中手工调用 telemetry，历史 Commander 账只读。`_service/operator_phases/<session_id>.jsonl` 只保留 legacy/诊断兼容：它与上述权威账正交，不进入 `NODES`、不冒充模型调用、不成为交付闸；缺失、未闭合或无法联结时完整 phase 墙钟分解必须是 unknown，不能据此阻断运行或洗成 0。若显式诊断使用该账，phase 仍闭合为 preflight/inspect/preview/execute/review/approve/finalize/verify，区间并集不重复计时，余量为 unattributed；prompt、正文、reason、argv、路径、密钥、模型 token/价格不得落账，宿主 usage 越界时声明 `unavailable_host_boundary`，不拿墙钟估成本 | `结构`（`novel/orchestrator/agent_supervisor.py` + agent event/admission/beacon/ledger fold + `novel/orchestrator/operator_telemetry.py::summarize` 的 legacy 诊断）+ 无手工埋点主路、缺账 unknown、隐私与 layering 回归 |

持久队列恢复先核 jobs 与正式产物；已完成且 fresh 不重跑。需要继续时复算计划并与冻结命令逐项比较，
相同且免费才可自动续，相同但付费需显式 queue-resume，变化则重新预览。
不得因恢复扩张章节或模型范围，执行前仍在同书互斥内复验写作授权。
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
| R-14.7 | state/inbox 使用调用栈内一次性冻结快照，同一代码、overview 与七层 check；不做跨写或全局缓存。active surface seal 只覆盖当前注册产物和审批 head，外部输入由 exact permit 单独绑定；历史、scratch、非注册文档及别书追加不制造假漂移。交付终验在独立读作用域检查当前成书、审批、Stage2 统一机械准入与 blocking 待办，准入的语义未审披露随交付常驻说明；并在前后复验 surface 与代码，漂移拒绝；不生成、读取或要求终审 MD。费用/session 统计是独立只读投影，不作为质量放行条件 | `结构`（`novel/orchestrator/inspection.py::capture_operator_snapshot` / `::read_surface_seal` + `novel/orchestrator/inbox_projection.py::inbox_from_snapshot` + `novel/orchestrator/mechanical_admission.py::stage4_ready` + `novel/orchestrator/delivery.py::assess`）+ active-surface、历史噪声、call-counter、漂移与统一下载回归 |

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
的变化扩大为整书失效。来源缺失保持 unknown，反例见 `tests/test_prov.py`、`tests/test_stage4_design_adoption.py`。
只修改原钩子的已批准 Stage0 revision，必须沿同书祖先链及 Stage1 原切分证明正文和 MainText
原始字节不变，才能保留 Stage2–5 原来源及仍满足完整身份的审批；稳定盲句柄和审阅依据不得
因此换 seed。未知、正文或切分变动、未批准、坏链、Hook 依赖仍走原根守卫。Prepare 则由
完整 typed 重编绑定当前两材料及历史生成证明，重新接受；不把旧模型来源重挂成新模型调用。
证明与拒绝反例见 `novel/source_identity.py`、`tests/test_source_identity.py` 和 Prepare HTTP 回归。
字段形状见 `docs/schema/_common.md` 及各 producer schema。

### Q-5 · 先校验后落盘

阻断候选不覆盖有效正式产物，问题稿保留来源并进入 `.INVALID`。免费 `--revalidate --actor --reason`
按当前判据重算、记录原始与当前身份及改动审计，合格才提升；保留原生成来源，不把人工字节伪装成新模型结果。
typed `--apply-edit` 是新人工轮次，旧主体决定按依赖失效，不能用同一个布尔标记混同两条路径。
反例是判据修正后被迫重购另一份稿，或提升丢失原来源；恢复载体见 `docs/schema/_common.md`。

### Q-6 · 单一落点

枚举、词表、区间、判定和修复路由各有唯一落点，prompt 必须引用或插值，不手抄子集。
模板缺值必须失败，不渲染成空串。反例是模型材料使用一份词表、校验器使用另一份，两端都不报错；
注册与分层守卫见 `tests/test_rules_registry.py`、`tests/test_layering.py`。

### Q-7 · 中间层只存决策与引用，内容由确定性代码搬运

中间层只存决定与引用，正文和上游事实由代码按引用搬运；让模型转抄会增加事实漂移与遗漏。
保留反例：把人物关系改成另一种身份、把多条具体事件压成合并散文。实际送达和覆盖反例见
`tests/test_hard_counterexamples.py`、`tests/test_stage5_write_agent_boundary.py`。

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
reasoning 已含在 completion 中时不再累加。

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
`tests/test_rules_registry.py::test_disclosure_and_degradable_sets_are_pinned`），机械准入
`novel/orchestrator/mechanical_admission.py::SEMANTIC_UNREVIEWED_RULES` 从注册表派生，
表外任何 `unver`/`skipped` 一律按不可判阻断。`ready_with_disclosures` 只装质量类披露。

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
| **B-02** | **旧事件拍的落笔顺序** | **现役暴露面已关闭。** 旧 event/beat 写作骨架已经退役；Stage4A 决定新场景披露顺序，Stage5 响应由 `novel/stages/stage5/write.py::_ordered_segments` 按计划 slot 确定性归位，不能临场另排一套。至于 A 选出的顺序是否更好没有 ground truth，留在 `B-17` 的结构读数 + 人工裁决面 | 不恢复“源拍位置单调”判据；那会把已去序的规划重新锁回源顺序。执行顺序由 `R-7.1` 的结构口关闭，审美优劣按 `R-5.20` 交人 |
| **B-03** | **旧开卷路径的重合度没有阈值** | **历史闭环记录，现役暴露面为空。** 旧盘面逐章重算：旧闭卷 0.0~2.4%，旧开卷 0.9%~96.8%，不存在可标定的开卷一刀切线 | **2026-08-24 以收走能力关闭，不是以发明阈值关闭。** 现役只有 canonical 闭卷 `stage5/chNN.json`，上界为 `SOURCE_OVERLAP_MAX = 10%`（`INFO`）；旧目录只读保留并报 `legacy_incompatible`，CLI 没有可恢复旧能力的入口 |
| **B-04** | **「必须成立、不必写在纸面上」那一组** | 机械层没有，且对应判据明确标为「查不了」；历史 Stage5 书级语义节点已退役，不再是现役覆盖 | 机械层继续诚实标注 `unver`；没有 ground truth 时不为了消掉盲区恢复一个自动语义门 |
| **B-05** | **Stage4A prompt 不许泄漏源序或正式内部 id** | **本缺口已按 A 相边界关闭。** `build_blind_projection` 是唯一构造口，正式外发面只含去序 opaque alias、事实语义、因果与状态；生产集成测试直接检查真发 prompt 不含 fact/source beat/stage/material/人物正式 id、原文或源区间。B 相在结构冻结后才有意接收 candidate scene id 与关闭式证据目录，不能把它反推成 A 的输入 | 维持 `R-5.5` 的 prompt 侧隔离守卫；不要把“B 为绑定证据而看正式引用”误报成泄漏，也不要把其它节点的 typed id 需要偷换成 Stage4A 的源序暴露 |

### 4.2 其余（按节点分组，逐条已核实「谁在查 = 没有」）

| # | 节点 | 无守卫的约束 |
|---|---|---|
| **B-06** | 计划 | A/B 的 strategy、goal intent、brief、purpose/reason 与 infeasible reason 等自由文本仍可能发明人物、事件或设定，也可能提前泄底。typed fact/stage/material/participant 引用能守引用闭合，却不能证明这些自由文本的语义忠实 |
| **B-07** | 逐章抽取 | 不为首尾相接拉伸区间 · 主谓宾必须明确不许省略施受关系 · 别把「靠什么成立」摘要掉 · 硬数字必须原样进事件文本 · 原文自相矛盾时如实记录矛盾 · 不许照抄例子里的名字 · 摘要不照抄原文措辞 |
| **B-08** | 全书抽取 | 结构性规则是否完整解释运作机制、秘密是否符合收录标准、冲突描述是否可定位，以及人物区分度、规则类型、称谓场合和叙述人称的语义选择，仍需人工核验。schema 的闭合取值域不证明内容正确；限知／全知与语法人称是不同维度，当前没有独立限知／全知载体。弧线字段已无现役 producer 或 consumer，不再列为待补守卫。 |
| **B-09** | 叙述视点与退役文风 | Stage3 v1 的“可迁移、逐章可见、可执行规则、素材分区”等定性质量面已随字段和消费方整体退役，**不再是待补守卫**。仍存在的缺口只有限知/全知与语法人称可能混淆（见 `R-2.10`）；它没有现役独立载体，不得以此为由静默复活 Stage3 定性 schema |
| **B-10** | canonical 写作 | 不把潜台词说破·换叙述人时声音也要换，两者没有确定性真值。历史开卷路径的表层修饰/整段照抄要求已随能力退役，不是现役条款。见 `R-7.9b` | 人称的狭可判域已由 `R-7.9` 常驻报数；剩余语义盲区继续明示，不冒充已守住 |
| **B-11** | 修剪 | **自动收缩臂仍关闭（2026-08-18 整臂下线）**：曾经的关闭方式是「同槽结构化候选 + 确定性 claims 对账 + 校准 A/B 双盲同 PASS 才采用」，而那道校准闸从未在任何宿主上打开过。现役没有由 `length_over` 自行生成、比较或采用短候选的路径：超上限章先零调用落 identity `TrimAudit` 转人工；人可 `--retain-pretrim` 销账、`--apply-edit` 提交完整章，或由受托 Reviewer 按 `R-7.31` 对有序非空章子集逐章给出关闭式指令、先落正式 request 后调用 Writer 重写完整章。最后一种是显式人审授权的写作动作，不是自动候选采用，也没有证明其语义质量可由机器裁定。 |
| **B-12** | 文案 / 改名 | 不说破任何答案（字面探针**自陈判别力为零，别当门用**）· 只引用正文真实出现过的句子〔标「查不了」〕· 不歪曲人物关系〔标「查不了」〕· 新名贴合时代与性别气质 · 同辈共用字、兄弟姐妹只差一字等**跨名字**结构关系。<br>⚠ 单个旧称呼内部的数字、排行、亲属、头衔标记已由 `R-10.12b` 确定性守住；本 B 只保留无法靠字面不变量判定的语感与跨名关系。 |
| **B-13** | 旧开卷写作 | **历史缺口已随能力关闭。** 旧 user 消息曾发「现场/回忆/转述」标签，system prompt 没有解释；实验补强没有稳定净收益 | 现役只有一份 canonical 闭卷 system prompt，旧路径为 `legacy_incompatible`，差异面结构性消失 |

### 4.3 已量出来、但没有下文的

| # | 条目 | 状态 |
|---|---|---|
| **B-14** | 身份描述里的结局 / 未揭示秘密 | **没有确定性防线。** 词表命中 0/17 而人读真违规 6/17（另一本 2/40）。处置不能是加词、也不能按章收窄（会掏空写作节点对角色的认知）。结局字段本身是对的，病在身份描述把它又讲了一遍，而两者的最长公共子串只对 1/3 强，**做不成可靠探测器** |
| **B-15** | 改写式泄底 | **历史闭环记录。** 曾以 secret-blind claims → secret alignment 两相付费审判接线；2026-08-17 根据实跑证据转授到 Stage2 后见之明与交付对账，旧 Stage5 节点、产物和判据全部下线 | 现役处置见 `R-3.7d` / `R-7.18b` / `R-7.18c`；不得把历史实验描述成当前 Stage5 能力 |
| **B-16** | 设定块的剧透闸门 | 字面子串包含，**命中 0/8，包括一条已知真泄漏**。真根因在产地：设定块里装着情节，修法是收紧抽取判据——那要重跑（付费） |
| **B-17** | 计划的场景轻重与顺序 | 场景数量、章/场 `weight`、预算分布与披露顺序哪种更好没有 ground truth。现役 `scene_budget_distribution`、逐字占比与结构度量只常驻报读数，不自动升成审美阈值；收口依赖 hash-bound typed 人工结构裁决 |
| **B-18** | 定向修复被驳回之后 | **没有下一条路。** 回归护栏会正确地驳回一次「顺手少了一条 hinge」的重生成并保留旧稿，但这一层**没有**写作层那套「换一次采样再试一次」⇒ 一次付费调用白花、那一章仍是红的。**同一个问题形状在写作层有解、在抽取层没有** |

### 4.4 按设计就没有判据的（`人` 是正确答案，但必须写明）

| # | 条目 | 说明 |
|---|---|---|
| **B-19** | Stage5 全书写作授权 | 是否按当前 Stage1 正文与 Stage4 计划启动一轮付费写作是人的决策，无自动判据可言。决定只落 `_service/approvals/` 的 `writing/writing_proof`，不进入小说产物；系统能验主体当前、测量契约当前、目标章在计划内，不能替人授权支出 |
| **B-20** | 非正文剔除的正确性 | **确定性验不了**——「这一段到底是不是番外」没有结构不变量可查，而正文体已按契约丢弃、盘上不留副本。判据已按 161 本语料标定（27 本命中 / 0 假阳），但那是「在这批语料上没抓错」，不是「对任何输入都不会抓错」。每本必须打印一条「剔除的正确性：本层验不了」 |
| **B-21** | 改名的替换覆盖率 | **验不了。** 只能替换人物表里有的称呼，人物表漏掉的角色这一层**结构上够不着**——实测漏过一个（靠残留探针捡回来）。别把「残留 0 条」读成「人名都换干净了」 |
| **B-22** | 新名的质量 | 时代感与雅俗没有确定性判据。**现役闸门就是「人定稿」那一步**，这一点不会变。同理开篇文案的定稿 |
| **B-23** | 提不出表面探针的事实 | `R-7.22` 会诚实地把无专名/数字的 fact claim 记为 `fact_landing_unver`，但确定性层随后没有第二把语义尺子证明它是否落地。这不是“查过没事”。同场的 verbatim / keep_value / must_appear 只能守各自材料维度，不能替代事实语义；代词化又已实测会让专名探针假阳。⇒ 先用真书盲读标定，不凭手感开自动阈值 |

---

## 五、反向覆盖率

每条注册 Rule 必须指向现存 R；每条声明 HARD / INFO / 闸门 / 度量的 R 必须有对应 Rule。
具体规则清单与修复归属只读 `contracts/rules.py::REGISTRY`，不在此手抄逐条映射。
下表仅保留现有文档计数守卫所需的逐层声明，不能用条数相等替代逐条覆盖。

| 守卫所在 | 需求范围 |
|---|---|
| 非正文剔除（`stage0`，**13 条现役**） | R-0 |
| 分章（`stage1`，**9 条现役**） | R-1 |
| 人物与 Blueprint（`stage2`，**29 条现役**） | R-2、R-3 |
| 设计、绑定与上下文（`stage4`，**29 条现役**） | R-5、R-6，含 `R-3.3b`、`R-3.21b` 的跨层交付 |
| 写作与验收（`stage5`，**52 条现役**） | R-7、R-8 |
| 文案（`stage6`，**22 条现役**） | R-9 |
| 更名与成书（`stage7`，**35 条现役**） | R-10 |

`tests/test_coverage.py` 校验双向关联、逐节精确条目数、逐层计数及无遗漏；structural/carrier 的
实现归属仍由对应 R 行点名，不能因没有注册 Rule 就称它没有任何校验。人审和真正盲区分别明示，B 不冒充测试。

## 六、产品与界面规格

产品边界、人工验收任务、路由与 UI 规则唯一见 `web/AGENTS.md`，操作入口见 `docs/OPERATIONS.md`。
本节保留稳定引用位置，不另存一份界面手册。

## 七、修复机制与成本上界

校验结论按 `remediation` 分四类；路由是策略，不在 CLI、界面或 agent 中临时猜：

| remediation | 谁修 | 准入与路由 | 自动成本上界 |
|---|---|---|---|
| deterministic | 代码 | 运行 Rule.fix 指向的同层免费节点与 flag，注册期验证路由真实存在 | 一次免费命令 |
| llm | stage5 写手 | 只接受 `repair == UNIQUE` 且 `unresolved_rounds < 2`；directive 从 Rule 唯一派生 | 每章每判据最多两轮付费 |
| human | 人 | 真人工路由为 human_only / upstream / approval / verification；通过 typed apply-edit、pick、hash-bound resolution 或 wontfix 收口 | 无自动费用 |
| none | 无 | metric 只报告读数 | 无 |

处理顺序：首轮生成 → 机械检查 → deterministic 先修 → UNIQUE 才可付费修 → 连续两轮未消转人 →
全规则回归 → Stage5 Review 只在 `review_status == fresh` 时驱动后续；Stage6 才有独立语义审判。unver 与 skipped 不属于四种修复，
也不得洗成通过。

**交付硬约束 vs 可降级目标偏差**（2026-09-15 全自动裁决；分级落在 `Rule.degradable`，注册期校验
`degradable=True` 要求 `sev=hard` 且有修复路，集合钉扎同 Q-13）：

- 未标记的 HARD 是**交付硬约束**（缺章、schema 无效、指纹不一致、替换正确性、覆盖游走等）：
  不满足即阻断交付，预算内修复，耗尽即终态失败。
- 标记 `degradable=True` 的是**从一开始就定义为可降级的目标偏差**（现役仅 `paragraph_budget_missed`）：
  生成期照常按 HARD 驱动预算内修复；预算耗尽后按预定义规则降为交付披露，不是四次失败之后把
  HARD 临时改 WARN。降级事实随交付披露常驻（Q-13），不静默、不作零。

唯一落点：路由算法 `novel/report/builder.py::remediation_of`；付费筛选
`novel/stages/stage5/write.py::revise_targets`；轮次上限
`contracts/invariants.py::STOP_REWRITE_AFTER`；八类清单 `novel/orchestrator/inbox_projection.py::inbox_view`。

现役逐条修复归属从注册表读取；密度、比例和档位等多解目标不因此获得付费自救权。

## 八、冻结证据基线

冻结样本只承担明确的证据与反例职责，不代替当前 schema、正式产物或质量验收。

### 8.1 唯一保留位置与守卫

历史交接包和 design brief 曾各保存一份相同 JSON，`web/src/mocks/` 又保存第三份。清理后只保留
`web/src/mocks/` 的 20 份原始字节。

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

现役自检：R 按节核对精确条目数，Q 恰好 12、B 恰好 23 且连续。每个 B 必须被某条 R 引用；每个声明
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
- semantic 判据只在 stage6，且必须非空；不以历史节点补占位规则。
- stage4/6/7 每条 HARD 在 `tests/hard_cases.py` 有真实会红的反例；stage0/1 的 warning、编码、kind、
  新增 HARD 与阈值带由 `tests/test_ingest_fixtures.py` 双向覆盖。
- ⚠ **一条 HARD 有多个判定分支时，「红过一次」不等于每一支都红过。** Stage4 现役定向反例覆盖
  placement 缺失、重复、越章引用，分场节点必要端点缺失及完整背景提前放开；绑定另核对父节点证据
  和舞台闭集。`tests/test_stage4_beats.py` 直接走正式呈现编译器，保留状态、知识和转场红绿对照。
  已登记故事约束只拒绝包含严格先后或因果边的回路，不从披露顺序推断世界因果，也不拒绝纯非严格
  回应、观察回路。靶子自检在 `tests/test_hard_counterexamples.py`；语义完整始终另经审阅判断。

### 9.4 架构与前端 seam

`tests/test_layering.py` 守 contracts/runtime/web 分层、store 独占 JSON 读写、API/view 不直写产物、
服务不能花钱、view/queue 不产生判定、API 不造第二个 argv builder、完成度不偷看作业记录。
`tests/test_api_discipline.py` 守 loopback、无 CORS、状态域、unknown、唯一 paid 来源、部分遮蔽说明与
同书 409。`web/scripts/check-seams.mjs` 守唯一 fetch 缝、mock/fixture 消费边界、颜色令牌，
以及 `web/scripts/mock-baseline.json` 登记的 20 份冻结字节（来历见 §8.1）。

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
