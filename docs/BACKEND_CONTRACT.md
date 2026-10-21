# 小说重构系统 · 现役后端契约

本文件保存 R/Q/B、守卫归属与修复路由。它由 `contracts/requirements.py` 解析，主要供
`tests/test_coverage.py`、`tests/test_docs_references.py` 检查需求与实现的关联；生产判据由
`contracts/rules.py`、`contracts/judges/` 及各层校验器执行，模型请求不全文加载本文件。

维护时先按节点或稳定编号定位相关节，不通读全表。待办见 `docs/REQUIREMENTS.md`，当前形状见
`docs/schema/`，关键裁决及证据限制见 `docs/DECISIONS.md`。这些关联检查不能证明模型的语义质量。

现役故事契约为 `story-v3`：Stage2 事实理解、Stage4 目标建模、Stage5 顺序执行、Stage7 核对装配。
节点、schema、consumer、恢复与只读 API 同批维护。运行只支持当前新契约，旧根保留原始证据并新根重建，
不转换旧语义产物或沿用旧购买作为当前回答。机械验收不证明文学质量，真实首答与成本需另经授权运行验证。

## 阅读与修改接口

- R 是功能要求；Q 是跨层工程约束；B 明示尚无可靠机械守卫或按设计交人的边界。
- 注册 Rule 只能指向现存 R，Q 可解释理由。改编号需查全部引用，不能放宽规则正则来跳过分母。
- R 标题、三列表格、Q 标题、B 行首及“谁在查”记号是解析接口，具体语法与验证见 §九。
- 结构/载体与 HARD 等判据职责分行登记；需要拆条时顺延字母后缀，不复用旧编号。
- 新规则同时告知 producer，并在真实判据入口设反例；只写 prompt 不是已守住，只登记 Rule 也不是质量已验。

| 谁在查的记号 | 含义与要求 |
|---|---|
| HARD / INFO | 当前有注册 Rule，分别报告机械阻断与明确未评/提示 |
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

产品为本机全自动单模式。上传时分别授权本书付费和正文外发；确定性 orchestrator 推进，
Stage 页只读监控，`HUMAN_NODES` 为空。用户负责需求、质量验收和兜底，工程 agent 不作为流水线角色。

- chapter_ordinal 为源阅读序整数，输出仍为 1..N；不合并、删除、拆分、重排章节。
- 当前 Stage0/1 后有15个 str06 节点，唯一登记 `contracts/invariants.py::NODES`；职责见 `docs/schema/str06.md`。
- Stage2 chapters 可独立观察；Stage4 chapters 基于冻结整书模型展开；Stage5 正文顺序消费已接受前章短尾。
- 正式路径见 `contracts/story_paths.py`；产物经 repository 输出组封存，状态不由文件存在性独立决定。
- run 提供版本/revision 和后端运行结论；source-story/target-story/chapters/Hook 提供 ready/missing/invalid、data/errors。
- `--check-only` 仅免费重放当前同请求的已购回答、重编和重验；缺响应或输入漂移明确停止，不能暗中付费。
- 跨版本重建使用独立空输出根，旧账和响应原字节只作证据。没有通用 contract-upgrade 或旧候选迁移入口。

枚举来自 `/api/enums`，前端不手抄另一套节点名。操作入口见 `docs/OPERATIONS.md`。

## 二、功能需求 R-0 ~ R-14

### R-0 · 非正文剔除（stage0）

免费识别非正文并落盘；机械校验（含正文占比闸）通过即由 producer 写入 auto 批准（`system:stage0-auto`，2026-09-15 全自动裁决），进入 Stage1。剔除的语义正确性（番外盲区）没有确定性防线，随交付披露（Q-13；原 `s0_correctness` 判据已随 2026-10-08 裁决删除，见 `R-0.7`）。`stage0.approve` / `stage0.edit` 节点已退役（非现役节点，关闭式拒识）。

| # | 要求 | 谁在查 |
|---|---|---|
| R-0.1 | 自动剔除用的是 `R-1.7` 的同一套判据与同一张 `NONBODY_KINDS`——**只有一份** | `HARD` |
| R-0.2 | 剔除逐段只记不可再生的决定与最小定位证据：类型、标记行、原文码点区间、来源。行号、字数与预览从原文和 span 现算；**番外体不进产物目录** | `HARD` |
| R-0.3a | **【v3 收窄】** 剔除语义正确性仍无人可机械证明（见 `R-0.7` / Q-13）。闸门不再是人工 `approved`，而是 transform 的 **mechanical admission**（`status=passed`）。公开检查单从当前 transform、执行资格与 freshness 只读投影。API 仍可投影 `revision_id`（= `transform_id`）供兼容读面 | `结构`（`novel/core/eligibility.py::is_stage0_executable`）+ `B-20` 披露面 |
| R-0.3b | **未获 mechanical admission 的书，stage1 必须拒绝启动**；执行资格的唯一落点是 `novel/core/eligibility.py::is_stage0_executable`，消费方一律 import、**不许复述条件**。v3 首跑即写入 admission；缺 transform / 损坏盘面须重跑 `stage0.remove` | `HARD` |
| R-0.4 | 【已退役 2026-09-15】人工编辑剔除范围随 `stage0.edit` 退役；判据漏掉的那类（语义误剔/漏剔）转为 `R-0.7` 的交付披露，不再有人工修正入口 | `不适用`（退役条款） |
| R-0.5 | transform 自证（判据 `s0_revision_chain` 复算 `transform_id`），`clean_text.txt` 是当前 transform 的唯一物化投影。无多修订祖先链 | `HARD` |
| R-0.6 | 必须能核对**两个内容身份**：`Stage0Source.normalized_source_sha1`（归一化原文）；`clean_sha1` 从 transform 与 live 投影现算。Stage1 的真正输入身份是后者 | `HARD` |
| R-0.7 | 剔除的**正确性**——「这一段到底是不是番外」。2026-09-15 起人工确认入口退役，本维转为 Q-13 内容质量类披露随交付输出。**2026-10-08 裁决：`s0_correctness` 判据删除**——剔除决策由机械判据确定性处理，永 unver 的规则只让 stage0 复检恒 AMBER；披露由交付 manifest 固定文案承担 | `结构`（`novel/stages/stage7/story.py::assemble_story` 的 semantic=unver；Q-13） |
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

### R-2 · 原作事实理解（stage2）

章观察 → 后置全书整合 → 明示未决的窄关联 → 名称结构 → 免费编译。
正式事实只有 SourceStory；章内观察可保留局部未决，整书综合不得暗改原观察。
schema 见 `contracts/schema/source_story.py`，引擎见 `novel/stages/stage2/story.py`。

| # | 要求 | 谁在查 |
|---|---|---|
| R-2.1 | SourceStory 显式当前版本，事件与证据非空；缺版本、旧版、未知字段或非法枚举拒绝。 | `HARD` |
| R-2.2 | 阅读章序恒为源 1..N；章观察集合必须完整。目标分章继承本章集合。 | 见 `R-5.2` |
| R-2.3 | 章观察只理解当前章及截至本章许可补料；互不依赖前章综合结果。语义观察与正式事实分开。 | `结构`（`novel/stages/stage2/story.py::chapter_prompt`；`novel/stages/stage2/story.py::compile_chapter`） |
| R-2.4 | book 继承章事件文本与证据，只增加整合关系；章观察摘要漂移或局部漏错须带证据回原章职责。 | `结构`（`novel/stages/stage2/story.py::compile_book`；`novel/stages/stage2/story.py::book_prompt`、`novel/stages/stage2/story.py::SourceObservationIssueRequired`） |
| R-2.5 | 唯一正式 SourceStory 含实体、事件、命题、认知、披露、状态、因果、线程、规则、表达、冲突和证据。 | `载体`（`contracts/schema/source_story.py::SourceStory`） |
| R-2.6 | 正式 ID、章内顺序、摘要和路径由代码计算，语义句柄不冒充正式身份。 | `结构`（`novel/stages/stage2/story.py::compile_book`；`novel/stages/stage2/story.py::compile_chapter`、`novel/stages/stage2/story.py::compile_names`） |
| R-2.7 | SourceIndex 为源原文唯一索引；查询返回范围、分母、游标、complete/truncated/empty 及清单摘要。 | `结构`（`novel/core/source_index.py::search_literal`；`novel/core/source_index.py::read_segments`、`novel/core/source_index.py::build_source_index`） |
| R-2.8 | 实体成立与姓名披露分开。未命名婴儿保留 person 身份，不以真名首现章限制其参与。 | 见 `R-2.12` |
| R-2.9 | 当前 source_identity 与 SourceIndex 一致，证据对象逐字相等；只改 Hook 原文也换源身份。证据缺失、伪造或源漂移拒绝。 | `HARD` |
| R-2.10 | 称谓字面必须实际出现在自身证据中；证据由代码回取，不让模型重复生成引文。 | 见 `R-2.9` |
| R-2.11 | 整合线程有序 open/progress/resolve/abandon，不能重复打开、孤立收束或终止后继续。局部孤立收束可先保留观察。 | `HARD` |
| R-2.12 | 实体/事件/命题/证据引用闭合；人物认知保留 holder/mode/polarity/at/supersedes；世界真值与人物误信、读者披露正交，读者 confirmed 不得升级 refuted/undetermined 命题。 | `HARD` |
| R-2.14 | 具体状态槽按主体＋aspect＋focus 唯一；多目标不被宽分类覆盖。更新保留 set/unset/unknown、故事时态、modality、persistence；引用闭合且 actual 状态不能依赖已否定或未定命题。 | `HARD` |
| R-2.16 | current_state 只折叠当前、稳定且实际的更新；回忆、未来和局部状态不覆盖当前整书状态。 | `结构`（`novel/stages/stage2/story.py::current_state`） |
| R-2.17 | 初始认知使用 story_start；人物认知更正明确 supersedes，读者披露保留独立通道。 | 见 `R-2.12` |
| R-2.18 | 证据读取不能越出本单位材料许可；空搜索不是不存在的证明，补料不能扩大写权限。 | `结构`（`novel/core/source_index.py::read_segments`） |
| R-2.19 | 原文不明/source_conflict 与模型 unresolved 分开；associate 只裁决显式候选，无候选零调用。未决不能伪装为原作歧义发布。 | `结构`（`novel/stages/stage2/story.py::compile_source_story`；`novel/stages/stage2/story.py::associate_prompt`、`novel/stages/stage2/story.py::compile_associate`） |

### R-3 · 源名称结构（stage2）

实体 ID 已稳定后拆名称，避免把命名难题变成人物存在门槛。

| # | 要求 | 谁在查 |
|---|---|---|
| R-3.1 | 名称结构恰好覆盖所有 person，名称部件、化名、纯称谓和 surface_forms 能逐字还原原称呼。 | `HARD` |
| R-3.2 | 实体合并后重新核对名称覆盖与称谓闭集；不通过改名结果暗中新增或合并人物。 | 见 `R-3.1` |
| R-3.3 | 事实召回、主体归属、复合命题粒度、因果、时态与叙述权限未经语义验收。 | `INFO`（source_semantics_unver；B-05、B-07、B-08、B-09、B-14、B-16、B-18） |
| R-3.4 | 名称产物只存语义组成；构词模板与正式别名句柄由 compiler 确定性派生。 | `结构`（`novel/stages/stage2/story.py::compile_names`） |

### R-4 · 即时机械读数（无 stage3）

代码计算机械读数，不设独立 Stage3，也不把读数包装成文学质量。

| # | 要求 | 谁在查 |
|---|---|---|
| R-4.1 | 非空白字符由代码计算，标点计入、空白不计。 | `结构`（`novel/core/story_prose.py::nonspace_chars`） |
| R-4.2 | 正文与 Hook 各按自己的包与篇幅界限验证。 | 见 `R-8.1` |
| R-4.3 | Stage3 节点与产物退役，不能由 CLI、API、队列重新创建。 | `结构`（`contracts/invariants.py::NODES`） |
| R-4.3b | Stage5 只消费当前章包预算，不从原文实时推导另一份写作目标。 | 见 `R-7.1` |
| R-4.4 | 原作 Hook 的机械文风投影退役；新 Hook 由 Stage4 明确设计。 | 见 `R-8.3` |
| R-4.5 | 篇幅边界属于当前 ChapterPacket/HookPacket，不造独立测量产物。 | `载体`（`contracts/schema/target_story.py::ChapterPacket`） |
| R-4.5b | 旧 Stage3 字节不迁移、不采用。 | `不适用`（退役条款） |
| R-4.6 | 源开篇文案仍是 Stage1 必填输入。 | 见 `R-1.12` |
| R-4.7 | 机械字符数不独立铸模型来源或计费记录。 | `结构`（`novel/core/story_prose.py::nonspace_chars`） |
| R-4.8 | 请求身份覆盖实际 system/user 和包摘要；算法/协议改变须同步当前身份与重建策略。 | 见 `R-7.4` |
| R-4.9 | 当前正文与 Hook 篇幅均按包中 min/max 检查，不把超限改成通过。 | 见 `R-7.1` |
| R-4.10 | 机械读数不能反向覆盖包预算或正文。 | `结构`（`novel/core/story_prose.py::validate_body`） |
| R-4.11 | 没有 Stage3 规则或检查端点。 | `结构`（`contracts/invariants.py::NODES`） |
| R-4.12 | 没有 Stage3 轮次稿、INVALID 提升或修复命令。 | `不适用`（退役条款） |
| R-4.13 | 旧测量来源边不重签为当前正式输入。 | `不适用`（退役条款） |
| R-4.14 | 旧 measure-only 与 Stage3 check-only 入口退役。 | `不适用`（退役条款） |
| R-4.15 | 历史 Stage4/5 人工批准和测量绑定不构成现役授权。 | `不适用`（退役条款） |

### R-5 · 目标小说建模（stage4）

命名与保真决定消费 SourceStory；整书建模只消费目标称呼和目标保真陈述。
整书模型定义故事，逐章展开只安排其演出。唯一正式目标故事为 TargetStory。

| # | 要求 | 谁在查 |
|---|---|---|
| R-5.1 | 语义线由 trigger→choice→result、人物弧线、状态/认知变化、因果/时间边和章入/离场共同表达；模型承担这些选择。 | `载体`（`contracts/schema/target_story.py::TargetModelWire`） |
| R-5.2 | 目标章数与章序严格等于源 1..N；事件恰分配一次，逐章展开不能重排事件或偷偷引入跨章事实。 | `HARD` |
| R-5.3 | 每条 required 保真要求有目标事件承载；依赖闭合有序、因果/时间联合图无环，AdaptationMap 记录实际承载。 | `HARD` |
| R-5.4 | 必保要求有目标陈述、参与者、满足条件与前提，源引用只用于追溯。 | `载体`（`contracts/schema/target_story.py::PreservationContract`） |
| R-5.5 | 整书建模材料去掉源句、源事件 ID、源章拓扑；保真语义转成目标称呼。 | `结构`（`novel/stages/stage4/story.py::preservation_brief`） |
| R-5.6 | 模型返回语义决定和位置引用；ID、预算、初终状态折叠由 compiler 计算。 | `结构`（`novel/stages/stage4/story.py::compile_target_model`） |
| R-5.7 | 建模自由文本的忠实度、人物说服力、场景轻重与转场自然度没有机械真值。 | 无守卫 → B-01、B-02、B-06、B-17 |
| R-5.8 | 世界规则每条明确 keep/adapt/omit 和理由；required 机制不能 omit。 | `结构`（`novel/stages/stage4/story.py::compile_preserve`） |
| R-5.9 | 源冲突每条给有界一致性决定，保留受保护结果；源事实漏错走 SourceFactIssue，不能伪装为自由重设计。 | `结构`（`novel/stages/stage4/story.py::compile_preserve`） |
| R-5.10 | TargetStory 与 AdaptationMap 分开，后者是审计映射，不是第二故事真相源。 | `载体`（`contracts/schema/target_story.py::TargetStory`） |
| R-5.11b | 保真条件满足与否的语义仍需阅读评价，realizes 引用只证明声明的承载存在。 | 见 `R-5.7` |
| R-5.12 | 已声明认知读取按 holder/mode/命题与时点成立；确知不能引用 refuted 事实，误信合法且不自动升级世界真值。 | `HARD` |
| R-5.13 | 初始称呼显式覆盖目标人物；后续名称释放只改变被授权显示。 | 见 `R-6.1` |
| R-5.14 | 逐章展开有 setting/time/pov/participants/goal/opposition/turn/realization 与动作回应结果；输入被冻结整书模型。 | `载体`（`contracts/schema/target_story.py::ChapterPlan`） |
| R-5.15 | 逐章新增跨章变化必须结构化回整书模型，不由 compiler 猜补。 | 见 `R-5.2` |
| R-5.16 | Hook 明确选事件、组织、重点、停点和读者期待。 | `载体`（`contracts/schema/target_story.py::HookPlan`） |
| R-5.17 | Hook 的 allowed_knowledge/allowed_displays 是独立授权，不由未来事件自动放开。 | 见 `R-6.1` |
| R-5.18 | 源语义与目标模型问题分别回 Stage2/整书目标 owner；不能交写手临场改故事。 | `结构`（`novel/stages/chain.py::_upstream_rows`） |
| R-5.19 | TargetStory 同时核对章计划与整书事件覆盖。 | 见 `R-5.2` |
| R-5.20 | 相同正式输入与配置的确定性编译幂等；语义生成不要求逐字复现。 | `结构`（`novel/stages/stage4/story.py::compile_target_story`） |
| R-5.21 | 语义输出与正式产物分开；缺版本不自动补成当前 canonical。 | `载体`（`contracts/schema/target_story.py::TargetModelDraft`） |
| R-5.22 | 章计划只引用本章冻结事件，不另抄一份源小说作为创作答案。 | 见 `R-5.2` |
| R-5.23 | 原作证据不进入 Stage5 正文请求；目标包投影承担正式边界。 | 见 `R-6.1` |
| R-5.24 | 不以最终接受状态反推首答通过率；真实首答与费用另用授权运行与原始响应核验。 | `结构`（`novel/llm/ledger.py::CostLedger`；`novel/story_metrics.py::story_metrics`） |

### R-6 · 确定性投影与发布（stage4）

compiler 不进行新的语义判断；validator 不修改输入。

| # | 要求 | 谁在查 |
|---|---|---|
| R-6.1 | 当前章/Hook 包引用与授权闭合，names 为当前许可显示；动作、状态、知情与六分区来自冻结目标故事。Hook 不能借未来事件释放额外姓名/知情。 | `HARD` |
| R-6.2 | 正式产物须有覆盖当前字节的封存输出组；未封存、摘要或 seal 损坏均拒绝 typed 消费。免费产物同样封存。 | `HARD` |
| R-6.3 | 包的 must_realize/must_respect/continuity/creative_guidance/background/guard_only 六分区不可省略。 | `载体`（`contracts/schema/target_story.py::ChapterPacket`） |
| R-6.4 | must_realize 演出行动，must_respect 约束条件，continuity 衔接，creative_guidance 负责表达空间。 | 见 `R-6.1` |
| R-6.5 | background 只供当前写作背景；guard_only 只供抽象禁止事项，不能携带未来答案和未释放真名。材料语义泄底仍未评。 | 见 `R-6.1` |
| R-6.6 | 参与者集合与 names 恰好一致，篇幅 min≤budget≤max。 | 见 `R-6.1` |
| R-6.7 | TargetStory 的章初终机器状态沿已声明时态、模态与变化折叠，不把过去回忆覆盖当前；写作包的入场/离场提示由章语义 owner 的 WriterContext 明确给出。 | `结构`（`novel/stages/stage4/story.py::compile_target_model`） |
| R-6.8 | 当前章名面按事件时点投影，非整书最终真名表。 | 见 `R-6.1` |
| R-6.9 | 显示名缺失或未授权时拒绝，不用内部 ID/slug 当正文称呼。 | 见 `R-6.1` |
| R-6.10 | 机械零错误不证明语义完整或文学质量；免费重编不能替代缺失语义决定。 | 见 `R-7.6` |
| R-6.11 | 人物认知与读者披露保持独立；只投影明确授权知识，不从世界真相自动授予人物。 | 见 `R-5.12` |
| R-6.12 | 整章预算按最大余数分配，和等于书级目标；包预算由代码派生。 | `结构`（`novel/stages/stage4/story.py::compile_target_model`） |
| R-6.13 | 声明依赖只证明声明的读取闭合；遗漏语义依赖不能靠机械折叠发现。 | 见 `R-5.12` |
| R-6.14 | 章语义 owner 在目标故事中明确 WriterContext 的安全称呼、入场/离场、行动与六分区；compiler 只核对并投影成包，不从机器状态自然值或全局未来材料猜造写作提示。 | 见 `R-6.1` |
| R-6.15 | 源悬念理解由事实层承担，目标悬念演出由目标事件与包表达，不能复活旧 IR 线程表。 | 见 `R-2.11` |
| R-6.16 | 生产读记真实来源边，检查读不偷记内容依赖；章绑定与正式封存分别核对。 | `结构`（`novel/store.py::Str06Repository`） |

### R-7 · 顺序正文执行（stage5）

写手只按当前目标章包与已接受前章短尾写作。表达自由，故事缺口返回 needs_design。
机械通过与文学质量分开，正文既是目标文字，不存在末端改名前中间稿。

| # | 要求 | 谁在查 |
|---|---|---|
| R-7.1 | 回答是 prose/text 或 needs_design/problems；prose 必须完整 stop、非空、无 NUL 且落包中篇幅带。截断/旧协议/非法回答不签接受回执。 | `HARD` |
| R-7.2 | 按源阅读序写作。请求送当前包及前章≤800非空白字符的完整自然段短尾，保留原换行；末段超限不截半段，状态明确。 | `结构`（`novel/core/story_prose.py::build_body_request`） |
| R-7.3 | 篇幅为当前包硬约束；不按预算耗尽把超限或空稿改成披露通过。 | 见 `R-7.1` |
| R-7.4 | 回执绑定当前包/实际前章短尾/正文摘要、真实 request_id/response_id/provider/model、system/user 摘要与原生成身份；修订另记真实补丁链。hash 与 ID 由代码计算，生产调用来源由 durable 边界提供。 | `HARD` |
| R-7.5 | 章回执是 ProseReceipt，机械通过仅覆盖已声明范围。 | `载体`（`contracts/schema/story_prose.py::ProseReceipt`） |
| R-7.6 | 成稿逻辑、人物转变、场景衔接、推进自然、保真兑现与文学质量未经语义验收。 | `INFO`（body_semantics_unver；B-03、B-04、B-10、B-11、B-12、B-13、B-15、B-19、B-20、B-21、B-23） |
| R-7.7 | 只实际送出的短尾影响下一章请求身份；前章其它内容变化且短尾不变不扩大失效。 | `结构`（`novel/core/story_prose.py::prior_tail`） |
| R-7.8 | 开场窄修只授权前8非空自然段，逐句柄 keep/replace 或 needs_scope；单段替换不含换段字符，错句柄/乱序/增加第九段拒绝，未授权字节原样保留。 | `HARD` |
| R-7.9 | 补丁请求绑定旧正文/旧回执/当前包与短尾；合并后完整机械重验。前章尾变化可窄修重新衔接，但保留原生成请求和 original_prior_tail_sha256，不伪装整章重生成。 | 见 `R-7.8` |

### R-8 · Hook 执行（stage5）

Hook 只消费 Stage4 的 HookPacket，沿用 tagged prose 回答与当前来源证明。

| # | 要求 | 谁在查 |
|---|---|---|
| R-8.1 | 当前 Hook 包、请求、真实响应来源与正文摘要一致；完整结束、非空、无 NUL、篇幅带通过。机械通过不证明不剧透或吸引力。 | `HARD` |
| R-8.2 | HookReceipt 使用当前 ProseReceipt 的 hook 分支，无前章短尾。 | 见 `R-8.1` |
| R-8.3 | 只改 Hook 不改变无关正文请求身份；Hook 选材与授权由 Stage4 明确，不能由执行层扩写故事。 | `结构`（`novel/core/story_prose.py::build_hook_request`） |
| R-8.4 | HookBody/ProseReceipt 显式当前版本，拒绝稿不进入正式接受位。 | `载体`（`contracts/schema/story_prose.py::HookBody`） |

### R-9 · Stage6 退役

没有候选文案改选和双盲判官占位节点。

| # | 要求 | 谁在查 |
|---|---|---|
| R-9.1 | Stage6 节点、批准与三候选选择面退役，当前节点表不再登记。 | `不适用`（退役条款） |
| R-9.2 | 旧语义判官和 typed 决定不恢复为当前正文质量证明。 | `不适用`（退役条款） |

### R-10 · 目标命名与最终交付（stage4/stage7）

命名前移进入正式建模；交付仅核对与装配，不能末端改变故事或替换正文姓名。

| # | 要求 | 谁在查 |
|---|---|---|
| R-10.1 | 目标 NameMap 恰好覆盖源 person，rename/keep、保留理由、名称部件和家族对应闭合。 | `HARD` |
| R-10.2 | 旧末端姓名替换、提及标记和差异证明退役；目标正文原字节进入交付。 | `不适用`（退役条款） |
| R-10.3 | 按源 1..N 严格核对章包/正文/回执集合与阅读序，逐章校验当前包、已接受前章短尾、正文和补丁链，另核对 Hook。不能静默排序、缺章或跳过失效章。 | `HARD` |
| R-10.4 | 最终 TXT 与 FinalManifest 必须从当前有效正文/Hook/包免费逐字重建一致，摘要覆盖最终 UTF-8 字节；manifest 存在不单独代表可下载。 | `HARD` |
| R-10.5 | 同正式输入确定性重放字节一致，正文不 strip、不换行归一、不末端改名。 | 见 `R-10.4` |
| R-10.6 | 新名时代感、可读性与实际语境使用未经语义验收。 | `INFO`（naming_quality_unver；B-22） |
| R-10.7 | mechanical=passed 与 semantic=unver 分开，不以交付字节正确宣称小说好读。 | 见 `R-7.6` |
| R-10.8 | FinalManifest 绑定有序各章 packet/tail/body/receipt 摘要与请求、Hook 的包/正文/回执摘要及最终 TXT 摘要。 | `载体`（`contracts/schema/story_prose.py::FinalManifest`） |
| R-10.9 | 旧 NameMapV6、IR 与旧回执不转换、不采用；新根按当前契约重建。 | `不适用`（退役条款） |
| R-10.10 | 成书开头为书名与 Hook，按源阅读序逐章添加「第N章」；源章数与分章身份保留。 | 见 `R-10.3` |

### R-11 · 模型权限、服务与只读界面

服务不调用模型、不写小说产物；客户端只呈现当前后端证明。

| # | 要求 | 谁在查 |
|---|---|---|
| R-11.1 | HTTP 仅绑定 loopback，不对外监听。 | `HARD` |
| R-11.2 | 变更类 HTTP 请求验证来源和 X-Novel-UI 自定义头，防止本机跨站请求。 | `HARD` |
| R-11.3 | 服务只写 _service 管理面；小说产物由 CLI 子进程经 repository 发布。 | `HARD` |
| R-11.4 | 前端不重算已在后端的业务判据、付费判断或交付结论。 | `HARD` |
| R-11.5 | 三层只读监控展示 SourceStory、TargetStory、章包/正文/回执、Hook、状态、成本和下载，不保留旧人物/Blueprint/Design/IR读屏。 | `结构`（`web/src/screens/str06/Str06MonitorScreen.tsx::Str06MonitorScreen`） |
| R-11.6 | 新鲜度 fresh/stale/unknown、missing 与校验 passed/failed/unver 分开。 | `HARD` |
| R-11.7 | 付费与免费动作由服务端唯一登记决定，前端不推测。 | `HARD` |
| R-11.8 | 上传或启动时确认本次冻结配置、付费与正文外发授权；preview 与 start 共用同一计划。 | `HARD` |
| R-11.9 | 材料响应 ready/missing/invalid 与错误来自后端；UI 不从文件或运行退出码推导可交付。 | `结构`（`novel/views/str06.py::source_story_view`） |
| R-11.10 | 同书只能一个后台作业，重复启动409；不同书互不占同一书锁。 | `HARD` |
| R-11.11 | 作业保留冻结命令、代码和模型配置；重启后无法证明执行状态保持 unknown，不倒推历史默认模型。 | `HARD` |
| R-11.12 | 退役的逐Stage人工批准和改选不恢复为普通用户流程。 | `不适用`（退役条款） |
| R-11.13 | 部分剧透遮蔽披露常驻，不把视觉开关宣传为数据安全边界。 | `结构`（`contracts/invariants.py::SPOILER_DISCLOSURE`） |
| R-11.14 | 模型执行配置唯一来自 capability；wire kind=legacy 是现役直连配置稳定名。配置只接受当前版本，不以旧配置迁移恢复旧模型角色。 | `结构`（`novel/orchestrator/executor_capabilities.py`） |
| R-11.15 | 冻结 profile 从计划贯穿 Step/argv/请求身份，不在节点执行期偷偷重读默认模型。 | `结构`（`novel/orchestrator/nodes.py`） |
| R-11.16 | 免费 check 使用相同请求配置但没有新付费或外发许可；缺已购回答停止。 | `结构`（`novel/stages/chain.py::execute`） |
| R-11.17 | 用户常用配置经 revision CAS，unset/configured/unreadable 区分；配置不包含运行授权。 | `结构`（`novel/orchestrator/agent_settings.py`） |
| R-11.18 | Direct LLM 按独立协议交付响应；正式发布来源仅从真实 durable 回答引用构造，不提供旧跨根候选采用。 | `结构`（`novel/llm/story_requests.py::request_json`） |
| R-11.19 | 整书 Agent 只是确定性代跑编排，不形成 Commander/Reviewer 模型角色。 | `结构`（`novel/orchestrator/agent_supervisor.py`） |
| R-11.20 | 执行许可在冻结快照与同书互斥内复验；unknown 不能授予执行。 | `结构`（`novel/orchestrator/execution_permit.py`） |
| R-11.21 | Agent 生命周期与进程退出、盘面交付三轴分别表达。暂停先停止接纳并排空，恢复复验身份，停止须证明执行树停止；服务重启无法证明时 unknown。 | `结构`（`novel/orchestrator/agent_supervisor.py`） |
| R-11.22 | 工作单元、执行 epoch、provider session、attempt 及 parent run 身份分开，不按时间窗口猜联结。 | `结构`（`novel/run_identity.py`） |
| R-11.23 | 代码或执行契约漂移先按当前规则免费复检并重开执行边界，不抹历史费用，也不把旧语义换算成当前回答。 | `结构`（`novel/orchestrator/agent_continuation.py`） |
| R-11.23b | pause_before_node 是登记节点的排他上界；冻结后保留到实际执行，未到全书交付不能伪造完成。 | `结构`（`novel/orchestrator/agent_supervisor.py::range_boundary_reason`） |
| R-11.24 | 运行页只消费后端 work_units、resume_frontier、allowed_controls、费用和耗时；未知不能折零。 | `结构`（`web/src/screens/run/RunScreen.tsx::RunScreen`） |
| R-11.25 | 额度追加必须显式授权与CAS；自动恢复不重置累计额度或自动追加。 | `结构`（`novel/work_units.py`） |
| R-11.26 | 同一输出组唯一 writer，正式读取验封，前端下载与 CLI 免费终验共用当前证明。 | `结构`（`novel/store.py::Str06Repository`） |
| R-11.27 | 错误保留 stage/node/item/phase/context 和真实 owner，服务不能只返回 traceback。 | `结构`（`novel/errors.py`） |
| R-11.28 | 当前故事 UI/HTTP 只接受 current SourceStory/TargetStory/Prose 契约，旧根拒识或归档，不混入新输出根。 | `结构`（`novel/views/str06.py`） |

### R-12 · 编排、模型请求与恢复

编排只走实际生产依赖；昂贵请求在副作用前核对精确身份、预算与权限。

| # | 要求 | 谁在查 |
|---|---|---|
| R-12.1 | 先章观察、后整书综合，再目标模型与逐章展开；Stage5 顺序执行，Stage7 核对。调度不代替业务语义，节点表是唯一拓扑登记。 | `结构`（`contracts/invariants.py::NODES`） |
| R-12.1b | 未执行、跳过、缺失与通过分别报告，运行中提供当前契约的零成本复检。 | `HARD` |
| R-12.1c | Stage2 章观察与 Stage4 章展开可受冻结并发和共享槽约束；Stage5 正文按阅读序运行，不以旧独立章池并发破坏前章短尾依赖。 | `结构`（`novel/stages/chain.py::run_body`） |
| R-12.1d | 每个请求使用独立任务协议、正式材料和许可补料；不读取工程指南/技能或额外盘上小说。 | `结构`（`novel/llm/story_requests.py`） |
| R-12.2 | 响应先 durable 后解析/校验/采用；空壳、重复JSON键、截断和未知字段不能作为成功缓存。 | `HARD` |
| R-12.2a | 格式修订、语义内容修订、网络重试与免费重放分开，记录实际 attempt/请求/响应和接受决定。 | `结构`（`novel/llm/story_requests.py::request_json`） |
| R-12.2b | 可定位的修订只修改授权路径，未授权候选保持冻结；重复诊断无进展显式停止，不能无限回环。 | `结构`（`novel/llm/story_requests.py::request_json`） |
| R-12.2c | 旧 Agent CLI structured delivery 和 provider会话恢复面不恢复为当前故事执行入口。 | `不适用`（退役条款） |
| R-12.3 | CLI 子进程显式UTF-8环境，原始退出码与终态保存，非ASCII日志不能把成功执行误报失败。 | `HARD` |
| R-12.4 | 缓存读写开关一致；当前同请求 durable 回答免费重放，保留真实响应来源和费用，补提交仍重过当前校验与输出组封存。 | `HARD` |
| R-12.4b | 旧 adopt-root、跨版本候选和旧模型响应换算退役；旧根请求/响应/ledger只作原字节证据，不成为当前正式依赖。 | `不适用`（退役条款） |
| R-12.5 | 每单位只接收自身任务所需的引用；Stage5 无源原句和内部ID，材料自由文本仍有语义泄底盲区。 | 无守卫 → B-05 |
| R-12.6 | 每份实际最终system/user先过当前模型精确token预算再碰缓存/网络/ledger；未知模型、tokenizer不可用、资源校验失败关闭式拒绝。禁止字符比例猜tokens或预算失败后静默截断。 | `结构`（`novel/llm/budget.py`） |
| R-12.7 | 请求身份、机械验证、产物发布seal和执行epoch各司其职，不复用旧人工审阅token或审批证明作为当前接受。 | `结构`（`novel/llm/paid_requests.py::CandidateRunContext`） |

### R-13 · 批量队列

批量只是编排多个独立书级工作流，不形成第二套判据或授权。

| # | 要求 | 谁在查 |
|---|---|---|
| R-13.1 | 同一计划冻结书集合、范围、profiles与授权；批量上传依次接入单书流程。 | `结构`（`novel/orchestrator/queue.py`） |
| R-13.2 | 运行前逐书复验冻结计划和书级冲突，明确 running/pending/rejected。 | `结构`（`novel/orchestrator/queue.py`） |
| R-13.3 | 付费与正文外发分别授权，客户端幂等键使受理重试不重复建run。 | `结构`（`novel/orchestrator/agent.py`） |
| R-13.4 | 一本失败不回滚其它书；恢复不扩大书集、章节或模型范围。 | `结构`（`novel/orchestrator/queue.py`） |
| R-13.5 | 当前流程没有人工Stage批准和三候选改选。失败停在精确owner，免费恢复与已授权内容修订分开。 | `结构`（`novel/stages/chain.py::run_node`） |
| R-13.6 | 队列不产生新的判定，不绕过producer或repository。 | `结构`（`novel/orchestrator/queue.py`） |
| R-13.7 | 不同书可并发，互斥仅落各自sid；同书作业内仍遵守章节依赖。 | `结构`（`novel/orchestrator/jobs.py::active_key`） |
| R-13.8 | 运行时间线由生命周期、admission、beacon与付费ledger形成，缺账保持unknown，不用墙钟估成本或人工埋点冒充模型记录。 | `结构`（`novel/orchestrator/operator_telemetry.py`） |

### R-14 · 断点恢复与最终状态

恢复实际当前产物与来源证明，不因文件存在就宣称完成。

| # | 要求 | 谁在查 |
|---|---|---|
| R-14.1 | 完成度来自当前正式产物、schema、封存与新鲜度，不依赖job成功记录。 | `结构`（`novel/orchestrator/state.py::overview`） |
| R-14.2 | 章恢复核对当前包/正文/回执、实际前章短尾和补丁链；已有效章不重购，发现文件名不能冒充接受。 | `结构`（`novel/orchestrator/state.py::chapters_done`） |
| R-14.3 | 同请求合法已购回答可免费补提交；实际调用与缓存命中分列，不能许诺来源缺失时仍免费恢复。 | `结构`（`novel/llm/paid_requests.py::PaidRequestSession`） |
| R-14.4 | 继续前复验当前输入；stale/unknown/invalid/missing区分，不能把已漂移状态折成待续跑。可安全重建范围由注册策略给出，未知停住。 | `结构`（`novel/orchestrator/state.py::resume_verdict`） |
| R-14.5 | 响应已存而发布未封存时不把半组当正式输入；按当前请求重放/重验/重新封存，不能重新捏造生成来源。 | `结构`（`novel/store.py::Str06Repository`） |
| R-14.6 | 进程停止证明与文件原子替换不同。清理/回收/恢复先冻结计划、复验身份并保留账与证据，不扩大到旧根迁移。 | `结构`（`docs/schema/_orchestration.md`） |
| R-14.7 | 最终下载从当前有效产物重建核对manifest/TXT，前后复验快照与代码；费用统计不作为质量放行依据，不生成终审MD要求。 | `结构`（`novel/orchestrator/delivery.py::assess`） |

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

产物追溯实际输入、prompt、模型与采用来源、代码及参数；生产读同时记来源边。
实际内容依赖与原始文件完整性分别核对。当前同请求 durable 响应可免费重放，
跨版本或源身份漂移不能重签旧来源；原始响应、请求和账只读保留。
真实调用与补丁来源在发布前绑定，不能靠测试或默认值伪造 provider/model/response_id。
边界见 `tests/test_story_pipeline.py` 与 `tests/test_story_prose.py`。

### Q-5 · 先校验后发布

原始响应耐久留证，解析与机械验证通过后才发布正式产物；输出组封存前不是正式输入。
拒绝的候选保留其请求/响应/诊断来源，不覆盖当前有效正式产物，不靠INVALID提升或默认版本补签。
恢复只在当前契约、相同请求身份与完整来源下免费重放和重新验证，缺证据保持失败/未知。
反例见 `tests/test_story_pipeline.py` 与 `tests/test_story_prose.py`。

### Q-6 · 单一落点

枚举、词表、区间、判定和修复路由各有唯一落点，prompt 必须引用或插值，不手抄子集。
模板缺值必须失败，不渲染成空串。反例是模型材料使用一份词表、校验器使用另一份，两端都不报错；
注册与分层守卫见 `tests/test_rules_registry.py`、`tests/test_layering.py`。

### Q-7 · 机械派生交代码，语义决定交模型

源局部事件不让全书模型重复抄写，证据原文、ID、hash、排序、引用解析、预算和材料投影由代码计算。
原作理解、保真决策与目标建模属于不同语义责任，不能让 compiler 从自然语言猜补。
正式边界与反例见 `tests/test_source_story.py`、`tests/test_target_story.py`。

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
名册的派生表已随名册删除），执行与完整性的 `unknown`/`skipped` 不得当作质量披露放行；源语义未评仅说明其证据范围，不虚构语义闸。
`ready_with_disclosures` 只装质量类披露。

## 四、无守卫清单 B-01 ~ B-23

以下明确质量或语义边界，不承诺已实现自动语义检查。

| # | 边界 | 当前限制 |
|---|---|---|
| **B-01** | 写作发明越界 | 自由表达可能新增未经授权事件、因果或设定；没有可靠机械语义门。 |
| **B-02** | 演出顺序 | 目标模型定义顺序；实际成稿是否自然遵守仍须阅读核验。 |
| **B-03** | 源文重合 | 当前写手不读源原文；未标定的重合率不能当文学质量阈值。 |
| **B-04** | 隐含事实 | 材料中必须成立的条件不等于必须逐字出现，字面探针不能证明语义兑现。 |
| **B-05** | 源盲边界 | 机械投影隔离源ID/原句；自然语言是否仍泄漏源结构需语义判断。 |
| **B-06** | 建模自由文本 | 角色动机、条件与原因文本可能发明内容；引用闭合不证明忠实。 |
| **B-07** | 章事实召回 | 施受关系、数字、时间、非显式状态与被摘要掉的细节可能遗漏。 |
| **B-08** | 整书理解 | 命题粒度、人物区分、世界规则、冲突与因果归因需核读。 |
| **B-09** | 叙述权限 | 语法人称不等于限知/全知，视点遵守与声音需阅读判断。 |
| **B-10** | 潜台词与声音 | 潜台词是否过早说明、角色声音是否区分没有确定性真值。 |
| **B-11** | 篇幅与内容 | 篇幅带机械通过不证明没有注水、删关键内容或压缩人物转变。 |
| **B-12** | Hook效果 | 不剧透、期待是否成立与吸引力需要实际阅读。 |
| **B-13** | 退役开卷写作 | 旧开卷/双臂/收缩能力已退役，不以历史实验补当前证明。 |
| **B-14** | 后见之明 | 全书理解可能把后文答案回填到早期人物认知，结构闭合只能抓已声明矛盾。 |
| **B-15** | 改写式泄底 | 换句话提前说破答案难以用字面比对识别。 |
| **B-16** | 背景隐含泄底 | 背景或抽象禁止事项可能携带未来答案，需要审视实际写手材料。 |
| **B-17** | 结构审美 | 章场轻重、节奏与冲突编排没有自动审美阈值。 |
| **B-18** | 窄修有效性 | 合法局部修订可能仍没修对语义，机械保范围不证明修得好。 |
| **B-19** | 授权与验收 | 上传授权执行不等于用户已验收成书质量；没有逐Stage人工批准。 |
| **B-20** | 非正文判断 | 机械剔除标记不能证明所有番外识别正确。 |
| **B-21** | 姓名语义覆盖 | 人物漏收、称谓归错与正文姓名使用不能由命名模板证明。 |
| **B-22** | 新名质量 | 时代感、雅俗、声音与语境适配未经语义评估。 |
| **B-23** | 无字面探针事实 | 无法直接定位的事实兑现需要对照源/目标模型阅读，未评不等于通过。 |

## 五、反向覆盖率

每条注册 Rule 必须指向现存 R；每条当前声明 HARD / INFO 的 R 必须有对应 Rule。
具体规则清单与修复归属只读 `contracts/rules.py::REGISTRY`，不在此手抄逐条映射。
下表仅保留现有文档计数守卫所需的逐层声明，不能用条数相等替代逐条覆盖。

| 守卫所在 | 需求范围 |
|---|---|
| 非正文剔除（`stage0`，**12 条现役**；2026-10-08 删 `s0_correctness`） | R-0 |
| 分章（`stage1`，**9 条现役**） | R-1 |
| 源语义与名称结构（`stage2`，**7 条现役**） | R-2、R-3 |
| 目标命名、目标故事与确定性编译（`stage4`，**6 条现役**） | R-5、R-6、R-10 |
| 顺序正文与 Hook（`stage5`，**5 条现役**） | R-7、R-8 |
| 最终交付（`stage7`，**3 条现役**） | R-10 |

`tests/test_coverage.py` 校验双向关联、逐节精确条目数、逐层计数及无遗漏；structural/carrier 的
实现归属仍由对应 R 行点名，不能因没有注册 Rule 就称它没有任何校验。人审和真正盲区分别明示，B 不冒充测试。
Stage6 整层退役后不再出现在本表（该层没有任何注册判据）；R-9 节保留退役条款与历史编号。

## 六、产品与界面规格

产品边界、人工验收任务、路由与 UI 规则唯一见 `web/AGENTS.md`，操作入口见 `docs/OPERATIONS.md`。
本节保留稳定引用位置，不另存一份界面手册。

## 七、修复与成本边界

当前规则没有可降级 HARD，也不由 Rule.repair 自动授予模型调用。
schema/引用/摘要失败定位到 node/owner/item/path；validator 不改输入。
材料不足走有范围的 need_materials；格式/可定位结构失败仅在已授权内容额度内有限修订，
跨职责缺口结构化回对应 owner；写手返回 needs_design 不代表允许临场重设计。
正文开场窄修按 R-7.8 授权，超出范围返回 needs_scope，不偷偷全章重写。

当前同请求已有 durable 回答时优先免费重放，免费编译和重验不新购。
旧 owner_patch、issue-correction、INVALID 提升、contract-upgrade 和跨版本历史采用不构成现役入口。
缺来源、截断、未知执行或输入漂移明确失败/停止，不降成质量披露。
实际内容调用额度由 `novel/work_units.py` 管理，网络重试、内容修订与免费恢复分别记账；
账内真实调用/缓存/失败与未知用量分列，不捏造首答率、预算或成本承诺。

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
HARD / INFO 的 R 必须被注册 Rule 覆盖；每条 Rule 必须反向指向现存 R。结构行的
可解析落点使用 `` `仓内路径.py::symbol` ``，具体分母由 `tests/test_coverage.py` 维护，不能为文档清理缩小检查范围。

### 9.2 文档引用与扫描自检

`tests/test_docs_references.py::_git_paths` 自动发现 Git 跟踪与未忽略新增的 Markdown，按用途区分工程说明、
临时计划、fixture 来源和许可；产物、缓存与第三方依赖不进入项目文档面。工程说明必须从根 AGENTS 的引用图可达。
R/Q/B 引用、仓内路径、Markdown 标题锚点与 `路径::符号` 必须有效，移动或删除时同步修复实际消费者。
逐份覆盖与扫描防空由明确的正例、坏路径、删标题、缺符号及新增孤立 MD 反例守护，不用总命中数代替覆盖。

### 9.3 判据注册与反例资产

Rule id、severity、owner、requirement、repair 与披露集合遵守 `contracts/rules.py` 注册校验。
当前故事规则只登记已实现机械范围；source/body/naming 语义质量使用明确 unver。
`tests/test_hard_counterexamples.py` 要求18条故事 HARD 与 `tests/hard_cases.py` 一一对应，
每条真实调用生产 schema/compiler/validator/repository，合法底座不报错，最小反例命中目标结论。
不能扫描测试源码出现规则名就声称有反例。空/旧版/未知字段、证据篡改、世界真值与认知、
具体状态地址、悬念、章集合、必保承载、封存摘要、正文来源和补丁范围另有明确分支样例。
Stage0/1 摄入反例继续由 `tests/test_ingest_fixtures.py` 守护。
stage 测试验证真实消费与恢复，golden set 评价最终阅读质量，两者不能互代。

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
