# 维护协议

## 目标

把本 skill 当作有接口、实现模块、知识库、模板和测试的软件维护。Git 负责回答“何时改过”；skill 文件只保留当前有效行为。

## 文件职责

| 文件 | 负责 | 不负责 |
|---|---|---|
| `SKILL.md` | 触发说明、输入/输出、稳定主循环、权限边界、完成定义 | 节点细节、事故史、宿主版本、日期日志 |
| `workflow.md` | 当前 CLI 工作流和状态解释 | 个案修复、审美裁决 |
| `delegated-review.md` | 闸门政策、候选选择、成书对账、终审包 | CLI 注册表 |
| `recovery.md` | 稳定修复状态机和正式人工写入面 | 未验证经验 |
| `runtime-profiles.md` | 宿主差异：授权来源、等待、停止、观察 | executor 选择、transport 诊断、流水线质量判据 |
| `execution-modes.md` | Agent driver / legacy LLM transport 的选择、预检、恢复与禁止降级 | 交互/headless 权限来源、stage 顺序 |
| `field-notes.md` | 经验证或明确标态的经验；索引兼入口（超出整读预算后条目分片进 `field-notes/`） | 稳定公共接口、流水账 |
| `assets/final-review-template.md` | 用户最终审阅界面 | 运行逻辑 |
| `scripts/session_preflight.py` | 只读固化一次 run 的起始时间、代码指纹、sid 与路径 | 推进节点或替代授权检查 |
| `scripts/operator_phase.py` | 调用生产遥测唯一落点，追加不含正文的 operator session/phase 墙钟事件并生成归属摘要 | 估算 Commander 宿主模型 token/成本、冒充流水线 node 或成为交付闸 |
| `scripts/stage0_review_workpack.py` | 只读复用生产 check/审校分区/`scan_text`，固定 Stage0 反向审阅模式并输出紧凑工作包 | 自动审批、自动剔除或复制生产删除判据 |
| `scripts/run_step.py` | 重铸 exact execution permit，调用生产 `build_steps`，以同一 permit/claim envelope + `step_index` 覆盖 root/follow，并给每一步串 start/end 信标 | 重造 argv、判定质量或吞掉节点退出码 |
| `scripts/finalize_review.py` | canonical 收尾：collect 恰一次、内存 render/validate、验证通过后原子提交，再以全新读作用域调用生产交付终验 | 在写盘后复用旧快照、吞掉校验失败或用结束时当前指纹替代起始事实 |
| `scripts/collect_final_evidence.py` | finalizer 复用的只读事实收集器；也保留为独立诊断入口 | 新造质量判据、写盘或替代逐项人工审阅 |
| `scripts/sync_review_evidence.py` | finalizer 复用的稳定机器事实块 renderer/原子提交器；也保留为兼容入口 | 改写 agent 的语义判断、代批理由或产物 |
| `scripts/validate_review.py` | finalizer 复用的终审实例对账器；也保留为独立诊断入口 | 判断小说内容好坏 |
| `scripts/verify_delivery.py` | 保留的独立兼容入口；用起始代码指纹调用生产交付终验 | canonical finalizer 之外再串一次重复 collect，或用结束时当前指纹替代起始事实 |
| `scripts/validate_skill.py` | 静态对账：结构、链接、节点顺序、经验格式，以及「文档声称的行为 vs 代码」 | 代替真实跑书；需要 pydantic 模型实例才能验的事 |
| `tests/test_skill_operator.py`（仓内） | 把校验器接进默认 pytest 套件；扫描面下界；用真 `render()` 验推荐读法 | 结构与文案本身 |

所有 reference 必须由 `SKILL.md` 直接链接，保持一层渐进披露。不要增加 README、CHANGELOG、quick reference 或同义副本。

## 事实来源

遇到冲突时先辨别问题类型：

- 产品语义与用户体验：用户裁决和现行产品契约优先；改变它们前先取得决定。
- 可执行能力：当前代码、schema、CLI preview 和测试优先；文档声称存在但代码不可达时标为缺口。
- 单次行为：原始产物、报告、日志、缓存键和复现步骤优先；不能用回忆替代。
- 经验推断：永远低于上述事实，必须带状态与验证方法。

不要把现有代码自动当成正确，也不要把旧 E2E 文档自动当成当前事实。发现冲突时先复现，再决定修代码、修文档或请求产品裁决。

## 变更分类

按变更影响更新最小完整集合：

| 变更 | 必改 | 同步检查 |
|---|---|---|
| 输入、授权、代批范围、终态、交付物 | `SKILL.md` | headless prompt、API/UI 文案、测试；语义变化先裁决 |
| 节点顺序、默认主跑参数、follow-check | `workflow.md` | `PIPELINE_STEPS`、`NODES`、CLI tests、校验脚本 |
| 闸门判据或候选选择 | `delegated-review.md` | report/schema、终审模板、真实失败样本 |
| 修复入口或写入权限 | `recovery.md` | `NODES.flags`、终态文案、schema、真实消费路径 |
| 新宿主 | `runtime-profiles.md` | launcher 权限、等待/停止机制、日志与恢复测试 |
| 新 executor/transport 或模式切换规则 | `execution-modes.md` | capability、CLI 控制参数、session/ledger 与禁止降级测试 |
| 新实战现象 | `field-notes.md` | 先复现和标态，不立即改稳定契约 |
| 用户终审需求 | 模板 + `delegated-review.md` | 真实终审包是否一屏可读、事实是否可追溯 |

外部消费者不要引用 `§3`、`§10` 之类易漂移的章节号。引用 skill 名和资源职责，或直接要求读取 `SKILL.md` 的“按需加载资源”。

## 经验生命周期

新增经验时按以下顺序：

1. **捕获**：保留原始 finding、产物 sha1、命令、退出码和最小复现；不要先写结论。
2. **核证**：对照当前代码、测试和源文，区分 contract、实现缺口、宿主行为与偶发模型输出。
3. **泛化**：删除书名、日期、金额和一次性措辞，改写为“触发—判断—处置—验证”。
4. **标态**：只使用 `contract-confirmed`、`observed`、`open-gap`。
5. **去重**：先检索节点、rule id 和错误片段；补充既有条目优先于新增近义条目。
6. **晋升**：只有代码/测试已钉住且会影响所有运行的规则，才从经验库晋升到 workflow/recovery/review。
7. **退役**：实现修复并验证后删除过时条目，或把仍有价值的机制重写为 current contract；不要保留“已修复”墓碑。
   ⚠ **FN 编号不重排、不复用、退役即留空号**：`novel/`、`contracts/`、`tests/` 的注释在按号
   引用这些条目。复用一个号不会报错，它只会让读的人查到另一件事，然后按自己的猜测把文档读通。
   删除那一侧由“仓内引用必须存在”的断言兜住；**复用那一侧没有守卫，只能靠这条纪律**。

每条经验固定包含：

```text
### FN-NNN · 中性标题
- 状态：contract-confirmed | observed | open-gap
- 触发：可检索的节点/rule/错误
- 判断：如何区分相似根因
- 处置：正式入口与成本边界
- 验证：怎样证明收口
- 依据：代码、测试或原始证据类型
- 复核：**仅 `open-gap` 必填**——一条 0 成本命令，跑一次就能判定这个缺口还在不在
```

`复核` 那一行存在的理由：缺口会被别人修掉，而经验不会自己退役。**本 skill 独立存在，不挂
任何仓内待办编号**（那种编号会随另一份文档的增删漂走，而它漂了这里不会出声），所以缺口的
时效性只能由条目自带的、当场可跑的命令承担。写成反引号里的命令，校验器会验它在不在。

`依据` 尽量写成 `仓内路径::符号`——校验器会验它落不落地。散文依据不报错，但它查不出来：
FN-006 就是这么烂掉的，它点名的那个字段整个消失了而条目仍然“格式合法”，
照它推断会在正文未变时白付一次语义审判。

## 经验库的增长

**索引是条目的投影，不是第二份清单。** 五列的用途各不相同，缺一列检索就退化：

| 列 | 它回答什么 | 谁保证它不漂 |
|---|---|---|
| ID | 跨仓稳定引用键（生产代码在按号引用） | 编号纪律 + “仓内引用必须存在”断言 |
| 标题 | 人扫描 | **逐字**投影自 `###` 行，校验器对账 ⇒ 结构上不可能漂 |
| 状态 | **命中后要不要打开**：手里已有 finding 时 `observed` 可先跳过 | 与条目 `- 状态：` 对账 |
| 触发键 | rg 的实际命中面 | 每个 token 必须在条目正文里 |
| 处置入口 | **最省的一格**：很多时候看完索引就不用打开条目 | 每个 flag 必须在条目 `- 处置：` 里 |

**整读预算不是一个写死的数字**，是「单份经验文件不得比每跑必读集还贵」：文件看起来不大时
执行者会直接整读，那样“按需检索”就名存实亡。校验器按 `SKILL.md` + `workflow.md` +
`delegated-review.md` + `runtime-profiles.md` + `execution-modes.md` 的当前体量算这条界，
用掉七成时提示，超了报错。宿主与执行模式仍只读各自匹配小节；这里用整文件体量是保守上界，
不是要求把另一模式也加载进上下文。

**超了就分片，不要为了缩体积删经验**：

```text
references/field-notes.md            ← 只留「使用方式 + 索引」，仍是唯一被 SKILL.md 链接的入口
references/field-notes/stage2.md     ← 按 stage/节点分轴（失败现场最先拿到的检索键）
references/field-notes/cross-cutting.md
```

- 分片必须**由索引直接链接**，且**不得**被 `SKILL.md` 直接链接：渐进披露仍是
  SKILL → 索引 → 分片的严格树，不开旁路。校验器两个方向都查。
- **二层渐进披露只对经验库开放**，其余 reference 保持一层——不要拿它当先例给 workflow 也开子目录。
- `cross-cutting.md` 的准入写死，免得变垃圾抽屉：**处置入口落在哪一层就归哪一层；
  跨三层以上或不涉及任何节点，才进 cross-cutting。**
- 检索命令不变（`rg` 本来就递归整个 `references/`），命中后只读一个分片而不是整库。

**晋升没有机械判据。** 试过一版（`contract-confirmed` 且依据已点名代码符号），它命中了
绝大多数条目——那不是信号是噪音，而总在喊的提示会把人训练成忽略它。晋升的真判据是
「会不会影响所有运行」，属产品判断；它的复核时机是下面的前向测试，不是校验器。

## 校验

每次修改后从仓根运行（`<PY>` 必须是项目解释器——校验器要 import `write_effect`，
那条链上有 pydantic；换个解释器会报 `FAIL validator crashed`，不会假绿）：

```text
<PY> -X utf8 .workflow/skills/novel-pipeline-operator/scripts/validate_skill.py
<PY> -m pytest tests/test_skill_operator.py
<PY> <SKILL_CREATOR>/scripts/quick_validate.py .workflow/skills/novel-pipeline-operator
```

第二条是这套守卫真正生效的地方：校验器**接在默认 pytest 套件里**，skill 漂了会让回归变红。
它此前只靠本节这一句话要求人手跑，于是十三处残留在它全绿的情况下活了下来。

**结构断言**（校验器）：frontmatter 只有 `name` 与 `description`；`SKILL.md` 少于 500 行；
所有本地链接存在、所有 reference 均从 `SKILL.md` 直接可达；workflow 的节点顺序与
`PIPELINE_STEPS` 完全一致；经验 ID 唯一且六个字段齐全；终审模板的必需章节存在；
headless 消费提示词不依赖已删除的章节编号；`.agents` / `.claude` 两份宿主发现 shim
逐字节相同、只指向 `.workflow` neutral canonical，且都不复制 `references/` / `scripts/` /
`assets/` 或主循环；runtime 只含宿主路由，execution modes 同时覆盖两种 executor 且明确禁止自动降级。

**行为断言**（测的是「文档声称的行为与代码是否一致」，结构检查测不到；真值一律从生产落点
反查，校验器里不出现第二份清单）：

| 断言 | 真值取自 | 它抓的那种漏法 |
|---|---|---|
| `INBOX_KIND` 每一类在 `recovery.md` 与 `SKILL.md` 完成定义都有交代 | `INBOX_KIND` | 少写一类，余额被静默漏报 |
| 文档写出的每个双横线 flag 都在注册表或 CLI 选项里 | `NODES` / 三张 flag 表 / argparse | flag 改名而文档没跟上 |
| 挂在节点上的每个 `PAID_FLAGS` 至少被提过一次 | 同上 | 漏一条现役付费路径 ⇒ 每本书白丢那一层 |
| **会改动盘上产物的 0 成本入口**至少被提过一次 | `write_effect(node, [flag])` | 漏 0 成本销账口 ⇒ 本可销账的留成余额；漏会写盘的诊断 flag ⇒ 以为“只是看看”而毁掉已批准的产物 |
| 「允许写入面」表与 `NODES[node].flags` **双向**对账 | `NODES` + `FREE_ENTRIES` + `write_effect` | 散文里“本层没有 X”不可判定，转成“表必须覆盖 X”才抓得住 |
| 判据 `fix` 指名的路由必须被提过 | `contracts.rules.REGISTRY` 的 `Rule.fix` | finding 当场指着一条 skill 里没有的路 |
| 写出的 `novel.cli <子命令>` 必须注册在案（只做正向） | argparse `add_parser` | 反向会逼着把 queue 系列塞进主循环，那是假红 |
| `operator next --json` 必须先于 runner，且每次变更后重新调用 | SKILL/workflow 的稳定循环 | 模型从 Stage 表猜下一节点，确定性调度器形同虚设 |
| 推荐过滤正则接住每一种非 `ok` 状态，且**不**接住 `[OK]` | `render.py::_STATUS_MARK` | 过滤吞掉 `[--]` / 来源行 / `⚠` 就不再是“只改变读法”；反向那半防的是把正则写成 `.*` 换绿灯 |
| 终审模板的“N 类计数”跟着 `INBOX_KIND` 走 | `INBOX_KIND` | `assets/` 长期不在行为断言面内，上一次十二处对账就漏了模板 |
| `依据` 里的 `路径::符号` 必须落地 | `ast` 解析目标文件 | 经验的价值等于它的依据能不能回查 |
| 经验索引的四列都是条目的投影（ID 顺序、标题逐字、状态、触发键、处置入口） | 条目本身 | 索引与条目各说各话，检索得到却打不开 |
| 分片必须由索引直接链接、且不被 `SKILL.md` 直接链接 | 目录结构 | 分片“存在但无人检查”比不分片更坏 |
| 单份经验文件不得超过每跑必读集的体量 | 必读集当前体量（**算出来的，不是写死的数字**） | 整读比检索便宜时，“按需检索”名存实亡 |
| 仓内 `novel/`+`contracts/`+`tests/` 按号引用的 `FN-NNN` 必须存在 | `rglob` 全仓 | 退役留空号 ⇒ 引用悬空 ⇒ 当场红（复用编号抓不住） |
| `open-gap` 条目必须带 `- 复核：`，且那一行给出反引号里的命令 | 条目本身 | 缺口已修而经验还挂着——skill 不挂仓内待办编号，时效只能由自带的复核命令承担 |
| 正文（剥掉代码块与行内 code 之后）无日期、版本号、带数字的“实测/实跑”、KB/% 读数 | — | 日期戳与一次性读数会过期，而它们从来没被查过 |
| `workflow.md`「结果判定」的退出码集合等于 `novel/cli/exit_codes.py` | `exit_codes.py` | 未登记的码被当成未知崩溃 |

`tests/test_skill_operator.py` 另做两件校验器做不到的事：给每个扫描面设**下界**
（正则失配、glob 变空、`NODES` 被改空时，“零个都没查”会伪装成“全部一致”），
以及用真报告（`novel.report.build` + `render`，不手写样例）验证推荐过滤确实留下了每一条判定行。

**这套守卫测不到的**（写在这里，免得下一个维护者把全绿读成正确）：

- **同一个 flag 在不同层行为不同**——`--identity` 在 `stage7.rename` 重写 name map、
  在 `stage6.hook` 一个字节都不写；`--apply-edit` 的路径语义分两组。只能留在经验库靠实跑复现。
- **秒数与次数类的一次性数字没有不假红的正则**：“每 60 秒汇报一次进度”是合法指令，
  “约 150 秒”是过期测量，两者形态相同。写之前自己问一句：它明年还对吗？
- flag 被“提过”**不等于**用法写对了、写在了对的段落。
- `依据` 的符号还在**不等于**语义没变（FN-006 就是这么烂掉的：它点名的字段整个消失了，
  而条目仍然“格式合法”）。
- FN 编号**复用**抓不住，只能靠“不重排、不复用、退役留空号”的纪律；删除那一侧才有守卫。
- **校验器全绿不等于跑得通一本书**——它不代替真实跑书。

随后运行与修改面相称的项目测试。只改文档仍需运行 skill 校验；改节点、flag、prompt 或 schema 时必须运行对应单测与真实 `preview`。

## 前向测试

重大改版后至少做三种无泄漏验证：

1. 给一个全新执行者 `SKILL.md` 路径、sid/base 和明确授权，确认它先调用 `operator next --json`，
   再消费返回的 action，而不是仅凭 skill 说出或猜出下一节点。
2. 分别给出 headless + Agent driver、交互式 + legacy transport 两组上下文；前者只读取 headless/Agent
   小节且绝不运行 DeepSeek doctor，后者只读取交互/legacy 小节且不伪造 started profile。
3. 给一个带 `.INVALID`、落选候选 HARD 或 stage7 二次编辑的原始状态，让其完成修复决策和终审包片段；
   修复写盘后必须重新取 next，不能沿用旧 action SHA。

不要告诉验证者预期答案或旧缺陷。对照其实际读取的资源、命令、停机边界和产物；“验证者说文档没写”也要回查文件，不能直接堆入重复说明。
