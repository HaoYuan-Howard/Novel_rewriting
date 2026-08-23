# To-do list

本文件是项目唯一的活动待办清单。已完成事项不在这里保留；历史原因由 Git 和
`docs/BACKEND_CONTRACT.md` §十 回答。产品规格、冻结契约与证据基线见 `docs/BACKEND_CONTRACT.md`。

**编号不重排。** TODO-01 ～ TODO-27 中已完成的已删除；留下的号空着——Git 提交信息按号引用，
重排会让那些引用指向别的条目。

⚠ **默认 pytest 刻意只跑非 corpus 套件。** 最终硬验收必须显式使用
`--require-corpus`，同时提供隔离的 `NOVEL_OUTPUT` 和与该盘面精确一致的无正文 manifest
（字段契约见 `docs/schema/corpus.md`）。当前 manifest 尚未生成（TODO-21），因此任何通过数字
只能称为“非 corpus 回归”，不能称为“全量绿”；也不得引用任何历史“0 failed”数字充当当前证据。

优先级含义：P0 影响正确性、真实花费或恢复能力；P1 影响质量闭环、诊断可信度或兼容性；
P2 是明确可延期的工程化与体验增强。勾选前必须满足条目中的验收条件，而不是只增加类型、按钮
或占位函数。

## P1 · 质量闭环、诊断与兼容

### TODO-21 · 建立 current-schema 现役语料基线

剩下的工作**只有一次付费跑**：闸机制（`tests/corpus.py` + `tests/conftest.py` 的
`--require-corpus` + `contracts/schema/corpus.py` + `tests/corpus_manifest.py`）已完整落地并
互相咬合，缺的是一份合格盘面。源语料两本都在（`Test_data/`，`NOVEL_DATA` 指路）。

- [ ] 在隔离输出与独立空缓存中，用最终代码从来源重新生成两本书的 Stage0～Stage7 产物；所有
  产物必须由现役 producer 按 `ARTIFACT_SPECS.current_version` 写出。**本仓没有迁移机制**
  （2026-08-18 归零，理由见 `contracts/schema/__init__.py` 模块头【版本】）——
  这一条因此比过去简单：盘上任何一个与注册表当前版本对不上的标记，都说明那份产物不是这一跑产的。
  ⚠ 版本号**逐份从注册表现算**，别写死 `1`：`stage2/chapter_blueprints.json` 已于 2026-08-19 升到 v2。
  不给旧 JSON 手补 `_schema_version`、`root_clean_sha1` 或其他字段冒充当前产地。
- [ ] 生成并提交无正文 manifest：逐书记录 sid、source hash、章节数、预期产物与版本、代码身份、
  冻结命令、ledger 聚合及精确 skip allowlist。终审成本以 `<base>/_service/ledger.jsonl` 为
  唯一权威源，不与产物 usage 或日志混加。
  ⚠ 冻结 dispatch 是 **16 条**（2 书 × 8 付费节点，`CURRENT_CORPUS_BOOKS ×
  CURRENT_CORPUS_PAID_NODES` 现算）。`stage6.semantic` 随 `--advisory` 删除后只带
  `--base --yes`，只剩 `stage5.write` 还带 `--deep 1,2,3 --shallow-rest`。
- [ ] 对同一盘面运行 `--require-corpus` 的完整 `corpus or not corpus` 套件；任何书目/产物减少、
  版本漂移、ledger 不闭合或新增 skip 都必须失败。

⚠⚠ **本条不登记任何滚动读数。** 本文件开头那条 ⚠ 已经禁止拿非验收盘面的数字当证据，
那么在同一份文件里维护一个「还剩几条红」的分数本身就是违规——它会被读成进度。
真实读数只能由上面那一跑给出。
（历史读数已作废：`-m corpus` 曾在 2026-08-18 记过 251→16 红，而当天晚些时候的三个
schema 破坏让盘上三本书的 stage5/stage6 层整体拒读 ⇒ 那份盘面连读数都产不出来
（实测：177 份 JSON 里 46 份拒读，`test_wontfix_semantic` / `test_api_edits` 的用例
在 `sandbox.Sandbox()` 搬文件时就整批 ERROR）。这不是回归，正是「它不能充当验收盘面」
的直接证明——**那三本书已于同日整批删除**，本条要的盘面只能从来源重新生成。）

既往两处澄清，避免下一个人再当成待办：

- `novel/orchestrator/approvals.py::backfill_from_disk` 曾被记成「只回填得出 `s0/s1/s3`」。
  根因是沙箱缺人工决策集，已由补齐沙箱解决；卡点随后移到更后面的闸。
- `spawns['s1']` 三本皆空**是产品正确行为**，不是缺陷：`approvals.py::_spawns` 按
  `state.resume_verdict` 过滤，一份 fresh 的 stage2 不该被再提议一次付费重跑。
  语义由非语料用例 `tests/test_approval_spawns.py` 双向钉住。

⚠ `tests/corpus.py::requires_current` 产的跳过**不进本 manifest 的 skip allowlist**：
`contracts/schema/corpus.py::CorpusSkipExpectation` 会拒收它们，而**那是对的**——本条要求的
验收盘面是从来源整批重生成、`code_identity` 唯一的，那样的盘面上这些前提恒成立、跳过恒不发生。
⇒ 它们是**过渡态**；验收跑里出现任何一条，就说明那份盘面没有真的整批重生成。

历史中间产物 `outputs/skill_e2e_schema_v1_20260816` 不是 current-schema 验收证据，
不能为最终 manifest 提供通过数字。

与最终基线相连、但尚未取得实证的付费项：

- **FC-08**：四项密度基线已完整下发给写手（`novel/stages/stage5/prompts.py::density_guidance`，
  反漂移守卫 `tests/test_stage5_prompts.py`），仍需按冻结的两书 42 章范围复跑，证明漂移命中
  逐书不增加、合计严格下降且 short-run 偏差不恶化。
- **FC-13**：逐节点 effort 覆盖位已落地（`contracts/invariants.py::NodeSpec` 的 `effort` 一格；
  未做 A/B 的节点保持 `None` 即 high，现在**每个节点都是 `None`**）。
  ⚠ **受限 A/B 工具已随 `860e1b1` 连同三份测试一并删除**（`tools.restricted_ab`、
  `tools.restricted_fc08`、`tools.paid_experiment_preview`——**点号模块名不是笔误**：
  反引号里的仓内路径受 `tests/test_docs_references.py` 保护，指着已删文件会红），
  而那次的 commit message 一个字没提这件事——本条目是这个事实唯一还活着的记录点。要做这次实验必须先重建一个
  「0 成本预览 → 显式 `--execute --yes`」的 runner，并按 `docs/LESSONS.md` §17 先登记决策规则
  再看结果。⇒ runner 已于 2026-08-19 重建为 `tools/effort_ab.py`（**进程内**覆盖
  `NodeSpec.effort`，仓里那一格恒 `None` ⇒ 实验臂与交付跑物理上不可能混）。
  在付费实测满足质量、UNVER/INFO、fallback/wire/error 和 completion 门之前，
  `stage3.style` / `stage7.rename` 继续保持默认 high（实测结果见下，改默认值仍待用户裁决）。
  ⚠⚠ **这一条是全链最大的降本杠杆，而它此前没有量。** 2026-08-18 实测（`.llm_cache/` 里
  1639 份带 `usage` 的真实响应，0 成本可复算）：completion 13,031,022 token 里 reasoning 占
  **11,457,627（87.9%）**，净成品输出只有 1,573,395；写作类调用（n=720）reasoning 中位 9109 而
  成品中位 1213。**输出侧是成本主体、reasoning 一项约占八成，输入侧优化的天花板只有约 7%**
  ⇒ 缓存/裁材料那一侧无论怎么做都换不来量级收益，只有 effort 降档能。候选优先序按收益：
  `stage5.write` 的 **shallow 臂**（它做的是照原文改写而不是闭卷创作，却与 deep 臂同付 high）
  → `stage7.rename` → `stage3.style`。⚠ 判官（`stage6.semantic`）与 `stage2.global` **不进候选**：
  2026-08-15 的 A/B 已裁决判官永不换廉价配置、关思考在 `stage2.global` 三轮全灭
  （见 `docs/BACKEND_CONTRACT.md` §十）。⚠ 那次 A/B 测的是**模型**与 **thinking 开关**两个维度，
  `reasoning_effort` 是**第三个维度、从未测过**——不要拿那次结论替本条销账。
  ⚠⚠ **实验设计要按真实档位来，不是三档扫描。** 本模型的档位是 **low / high / max** 三个
  （2026-08-18 用户依官方文档确认）：`medium` 与 `xhigh` 虽然 API 收，但被服务端**静默映射回
  `high`** ⇒ 拿它们当中间档会得到一份「配置写着降了、模型按 high 跑」的脏数据，而账本
  `reasoning_effort` 那一列会如实写着 `medium`，事后分辨不出来。三处闸已关闭式拒绝它们
  （`novel/llm/client.py::rungs_from_effort` / `novel/llm/budget.py` / `NodeSpec.effort` 的取值面）。
  ⇒ **降本方向只有一个对比点：high vs low。** `max` 是**升档**（多 79 token 官方 effort 前缀、
  且前缀明令写出完整推敲过程 ⇒ reasoning 更多），它属于**质量上界**实验而不是降本实验，
  且启用前要先按 `novel/llm/budget.py` 的记录把官方前缀字节取回来。

#### FC-13 决策规则（2026-08-18 登记，**先于任何 effort 实测结果**）

按 `docs/LESSONS.md` 第 17 条，规则在看到结果之前固定；事后修改规则即本次实验作废、重跑。

**样本**：`四人成行_2055965741988443882`（11 章，`Test_data/` 七本中最小）。
n=11 ⇒ 任何结论只能写成「**这本书上没观察到质量塌陷**」，不得写成「low 等同 high」。

**设计：单节点换臂（OFAT），不是全链齐变。**
全链齐变得不到逐节点结论——流水线是串联的，`stage5` 消费 stage2/3/4 的产物，
全 low 那一臂里成稿变差分不清是本节点降档还是上游输入已劣化，**两个变量绑在一起动**。
正确形状：先全 high 跑通一遍当基线（它同时是交付品），再对每个候选**复制一份 base、
只把该节点重跑成 low、上游产物逐字节相同**，用 `tools/ab_compare.py --arm` 双 base 对账。

**候选与顺序**：`stage7.rename` → `stage3.style`。
排除 `stage6.semantic` 与 `stage2.global`（2026-08-15 已裁决，见 `docs/BACKEND_CONTRACT.md` §十）。

⚠ **收益最大的候选 `stage5.write` shallow 臂今天够不着，它是本条真正的施工前置。**
`NodeSpec.effort` 是**逐节点**的，而 deep/shallow 是同一个节点 `stage5.write` 的两个 mode
（`novel/cli/main.py` 里 `deep_ctx` 与 `shallow_ctx` 都传同一个 `ctx.node`）
⇒ `NODES[ctx.node].effort` 对两臂返回同一个值，**现有旋钮挂不上「只给 shallow 降档」**。
要做这一项必须先加挂载点，那是独立改动，不要在实验里临时改成全臂降档冒充。

**成本口径：只看输出侧（`completion` / `reasoning`），不得用 prompt token 判胜负。**
理由是实测发现 **`reasoning_effort` 会切分供应商前缀缓存**：同一本书、同一段 system 字节、
同一次跑里，`stage2.blueprint` 全部 `high` 行 hit≈4352，而降级阶梯打到 `low` 的那一条
hit=0 / miss=5613 ⇒ **low 臂天然冷启**，prompt 侧对比是伪信号（且输出侧本就是 87.9% 所在）。
记账只以 `<base>/_service/ledger.jsonl` 按 sid + node + `outcome == "ok"` + `cached == false`
过滤，不与产物 `usage` 或日志混加。

**质量门（先于成本比较；任一项劣于基线即判该节点 low 不可用，不再讨论便宜）**：

| # | 指标 | 通过条件 |
|---|---|---|
| 1 | 该节点 check 的 HARD 命中数 / 分母 | ≤ 基线，且分母不变 |
| 2 | `unver` + `info` 条数 | ≤ 基线 |
| 3 | 降级阶梯触发次数、wire error 数 | ≤ 基线 |
| 4 | `finish_reason != stop`（截断）计数 | ≤ 基线 |
| 5 | 产物 schema 校验、章集合覆盖 | 全通过且覆盖面逐字不变 |

⚠ **生成质量与审判能力分开评价**：`stage6.semantic` 恒产带证据 `UNVER`、本仓无自动放行门
⇒ 它**不能**给生成侧质量背书。上表五项都是可复算读数，不含任何审美判断；
若五项全平而只剩「读起来哪个更好」，按第 17 条那是 n=1 盲读偏好，**记为无结论**，
不作为降档依据（2026-08-15 已踩过一次：盲读偏好与硬闸结论相反）。

#### FC-13 实测结果（2026-08-19，`四人成行_2055965741988443882`，n=11 章）

**方法**：每个候选节点复制一份 base 到 `outputs/_ab/<node>_<arm>/`（`_` 开头 ⇒ 不会被
`novel/paths.py` 当成书目录），上游产物逐字节相同，只把该节点重跑成 low。
**high 参照臂从 `.llm_cache/` 复现**（账本 `cached=true`、0 成本、与基线账本逐字段相同）——
除 `stage4.plan` 外全部命中，因此这次的 high 侧几乎没有花钱。
⚠ 复现的前提是 argv 与基线**逐字相同**：`--chapters` 会把 `cache_policy` 变成
`refresh-write`（只写不读），`stage5.write --mode X`（不带 `--chapters`）会把**全部**缺章
写进那一条臂——两条都实测踩过，各误发了一批真调用。整书入口是
`stage5.write --deep 1,2,3 --shallow-rest`。

**成本（输出侧，按本条既定口径；prompt 侧因 effort 切分前缀缓存不可比）**

| 节点 | 调用 | completion high→low | reasoning high→low | 净正文 high→low |
|---|---|---|---|---|
| stage2.blueprint | 11 | 194,490 → 132,694（−31.8%） | 171,162 → 112,874（−34.1%） | 23,328 → 19,820（−15.0%） |
| stage5.write | 11 | 113,525 → 86,726（−23.6%） | 104,237 → 78,026（−25.1%） | 9,288 → 8,700（−6.3%） |
| stage3.style | 12 | 34,232 → 20,751（−39.4%） | 28,678 → 15,968（−44.3%） | 5,554 → 4,783（−13.9%） |
| stage7.rename | 1 | 13,691 → 7,466（−45.5%） | 11,817 → 5,744（−51.4%） | 1,874 → 1,722（−8.1%） |
| stage6.hook | 1 | 5,144 → 3,959（−23.0%） | 4,687 → 3,559（−24.1%） | 457 → 400（−12.5%） |

**质量门（五门判据见上表；任一项劣于基线即判 low 不可用）**

| 节点 | 门①HARD | 门②unver+info | 门④截断 | 门⑤章集合 | 结论 |
|---|---|---|---|---|---|
| stage3.style | 0 → 0 | 0 → 0（两臂 `PASS`，ok 9 逐项相同） | 无 | n/a | **五门全过** |
| stage6.hook | 0 → 0 | 6 unver → 6 unver（逐条相同） | 无 | n/a | **五门全过**（但机械面 6 条 unver，语义面无 ground truth） |
| stage7.rename | 1 → **0** | 8 → 8 | 无 | n/a | **未见劣化**（n=1，不得读成「low 更好」） |
| stage2.blueprint | 1 → **3** | 2 → 5 | 无 | 11/11 | **否决** |
| stage5.write | 0 → **1** | 5 → 8 | **ch1 截断** | **11 → 10** | **否决** |

- `stage2.blueprint`：high 首轮只命中 `present_names_invented`（ok 67·命中 2·unver 2）；
  low 首轮命中 `hinge_anchor_void` / `span_unlocatable`（86/87 定位成功）/ `verbatim_failed`
  （4 条失败记录）三条 HARD（ok 64·命中 5·unver 2）。**三条全属「锚不回原文」一族。**
  另实测 ch7 一次 `reasoning_tokens` 只有 33（28s，净正文 1,949）⇒ low 档会偶发几乎不思考。
- `stage5.write`：low 的 ch1（deep 臂）`draft_truncated`（结尾无终止标点）落 `.INVALID`，
  章集合掉到 10/11；`stage5.review` 的 info 命中 4→6（新增 `invented_number` 与
  `quotes_in_unquoted_corpus`）、unver 1→2；`para_len_drift` 最坏章从 5.57× 恶化到 11.10×。
- `stage4.plan`：**这一节点测不了**。两条臂都只发生 1 次调用、且都是同一份
  `cached=true, reasoning_effort=low` 的响应——基线那次 high 响应被 `ask_json` 的内容闸
  拒绝后 `novel/llm/cache.py::ResponseCache.evict` 已把它从盘上删掉，缓存里**没有 high 那一档**。
  ⇒ 这同时确证了降档由**内容闸**判而不是传输层（账本两行 `outcome=ok` / `attempt=1` 不矛盾：
  阶梯是与传输重试并列的另一条循环）。要 A/B 必须付费重取一次 high。
  ⚠ 顺带的结论：**交付那本书的 stage4 计划本来就是 low 产的**，除一处 `goal_landing`
  越界（0 成本人工改一行）外通过全部判据。

**结论**：降档伤的是「回到原文取证」那一类能力（verbatim / span / 锚点 / 不截断），
不是文风密度类判据。而**降本杠杆最大的两个节点恰恰全在被伤的那一类里**：
`stage2.blueprint`（占本书生成 completion 的 45.5%）与 `stage5.write`（26.6%）合计 72.1% 一律否决；
可采纳面只有 `stage3.style`（8.0%）+ `stage6.hook`（1.2%）+ 单样本未见劣化的 `stage7.rename`（3.2%），
**全采纳的输出侧节省上界约 4.8%**（按各自实测降幅折算）。
⇒ 「用 effort 降档换量级收益」这条路在本书上被证伪；量级收益要另找方向。

#### FC-13/A · `stage2.blueprint` 锚点改段落索引（2026-08-19 收官：**门⑥未达阈值，经用户裁决采纳**）

**方向**：既然降不了「想多少」，就降「需要想什么」。把定位方式从「模型逐字转写原文片段」
改成**「下发带编号的段落、模型交索引」**，由代码把索引回取成 `SourceSpan`。
被砍掉的是每章约 **38 次**「原样照抄、必须能在本章原文一字不差找到」的转写核对
（约 9.6 条 beat × 2 锚点 + 约 9.5 条 hinge × 2 锚点）。产物 `SourceSpan` / `Beat` /
`Hinge` 的形状一个字不动——逐字锚点本来就只活在「响应 → 产物」那一道归一边界上。

**可行性前提（同日实测，见下方存档 §⑩）**：本书 458 个非空段、均段 23.4 字
（内容字 21.3）、p99=66、max=87，基线 106 拍 ⇒ 平均一拍约 4.3 段，段号粒度明显细于拍粒度。
⚠ **这是本书特性**，长段叙述体的书要另行验证，结论只能写成「这本书上成立」。

**契约破坏（已按仓规同批回答）**：`BlueprintContext.architecture` 收窄成只认
`bounded-fidelity-v3`，`ARTIFACT_SPECS[STAGE2_CHAPTER_BLUEPRINTS].current_version` **1 → 2**
（登记在 `tests/test_artifact_versions.py` 的破坏账里，那张表与注册表互为守卫）。
⇒ **存量蓝图由 `stage2.blueprint` 整跑重建（付费）**，连同 `.INVALID` 与 `_rounds/` 一律作废，
**不手补字段冒充当前产地**。落代码后、重跑前，盘上存量书的 stage2 落「读不回来」，
`state` / `inbox` 实测 exit 0 并如实降级（不崩）。

**本批同时删掉的死路**（都是被这次改动作废的，不是顺手清理）：
`novel/core/spans.py` 的 `walk_spans` / `_find_or_recover` / `_find_exact_raw` /
`extend_punct` / `HINGE_LCS_MIN`（模型不再转写原文 ⇒ 「在原文里搜锚点」零生产消费方，
反向守卫见 `tests/test_core.py`）；判据 `anchor_not_verbatim` 与它断言的
`BlueprintProvenance.verbatim_recovered`（LCS 救回结构性不存在 ⇒ 一盏永不亮的绿灯）；
`SourceSpan.recovery` 的 `lcs` 一档。stage2 判据因此 **71 → 70 条**（38 hard 不变，info 15 → 14）。

**同批修掉的一条隐式契约**：hinge 失败根因此前由产地写中文、判据按**子串**猜
（`'空档' in cause`）——改一句措辞就能让 `hinge_anchor_void` 静默降级成 `hinge_drift`
而报告照样绿。现在根因是闭合取值面、判据按相等查
`contracts/invariants.py::HINGE_FAILURE_ROUTE`，并配一条 AST 守卫断言
「产地只发表里那几句」（`tests/test_stage2_generation.py`）。

**决策规则（登记于付费实测之前，`docs/LESSONS.md` §17；事后改规则即本次实验作废、重跑）**
沿用 FC-13 那五道门，外加本次专有的两条。任一项劣于基线即判 A 不可用。

| # | 指标 | 通过条件 | 基线值 |
|---|---|---|---|
| ① | 该节点 check 的 HARD 命中数 / 分母 | ≤ 基线且分母不变 | 1（`present_names_invented`）· ok67/hit2/unv2 |
| ② | `unver` + `info` 条数 | ≤ 基线 | unver 2 |
| ③ | 降级阶梯触发次数、wire error 数 | ≤ 基线 | 0 / 0 |
| ④ | `finish_reason != stop`（截断）计数 | ≤ 基线 | 0 |
| ⑤ | 产物 schema 校验、章集合覆盖 | 全通过且覆盖面逐字不变 | 11/11 章 |
| ⑥ | 有 event 的拍数 · 在场者称呼数 | **≥ 106** · **≥ 237** | 106（约 9.6/章）· 237 |
| ⑦ | 段级覆盖：被任一 beat 覆盖 ≥50% 的段数 · 零覆盖段 | **= 458** · **= 0** | 458/458 · 0 |

⚠ **门⑦替掉了「原文覆盖 ≥98%」**：区间对齐到段边界后字数覆盖率会因对齐而升，
那条门变得几乎恒真、**不再是证据**。段级读数保留判别力——已否决的 low 臂在它上面是
454/458、4 段零覆盖，而字数覆盖率只从 98% 动到 97%。
⚠ **两处判别力下降，验收时必须如实报、不得写成战果**：
`SourceSpan.recovery` 的取值分布不可比（`lcs` 归零、`normalized` 只可能出现在 hinge）；
`edge_hole` 变钝（章首漏一段 ≈23 字 < `HOLE_MIN_CHARS=30` ⇒ 要漏 ≥2 段才出声）。
把 `edge_hole` 升级成「首拍 `start_para` 必须是 1」是把 HARD 变严、会与基线不可比，
**故意留到验收之后**（见下方待办）。

**基线存档**：`outputs/_ab/baseline_blueprint_v2.md`（三臂矩阵 + 全 71 条 scanned +
段级覆盖 + 段落粒度 + 复算脚本）。⚠ `outputs/` 不入 git，且 **architecture 收窄之后
基线臂经 `store` 再也读不回来**（判据吃 raw dict，仍可在进程内跑）⇒ 上表那几个数字
就是它在仓内的唯一副本。

#### FC-13/A 实测结果（2026-08-19，11 章付费实跑）

**方法**：从交付书复制一份 base，只删 stage2 的蓝图产物，`preview` 取 argv 后前台整跑
（`--chapters` 被 `BLUEPRINT_SYS` 指纹闸挡住 ⇒ 结构上没有小样探路这条路）。
质量对照用**同一套 70 条判据**分别跑两份产物（`checks.check` 吃 raw dict，不经 `store`），
因此基线侧与新臂侧是逐条可比的。

**成本（输出侧，口径同 FC-13）**

| | 基线 high（v2） | 段落索引（v3） | 变化 |
|---|---|---|---|
| 真调用 | 11 | 11 | — |
| completion | 194,490 | **146,974** | **−24.4%** |
| reasoning | 171,162 | **124,883** | **−27.0%** |
| 净输出（completion − reasoning） | 23,328 | 22,091 | −5.3% |
| reasoning 占比 | 88.0% | 85.0% | −3.0pp |
| prompt | 73,246 | 74,235 | +1.4%（段号标记） |
| 墙钟 | 657.5s | 490.1s | −25.5% |

⚠ **供应商前缀缓存命中率 65.4% → 10.3%，那是一次性冷启不是回归**：`BLUEPRINT_SYS`
字节变了，供应商侧前缀要重新热起来（交接稿 §5 已预告）。下一跑会回升，**不要把它记成成本项**。

**七道门**

| # | 指标 | 基线 | v3 | 判定 |
|---|---|---|---|---|
| ① | HARD 命中 / 分母 | 1（`present_names_invented`）/ 237 | **0** / 221 | 命中数 ✅ · **分母缩了** |
| ② | `unver` + `info` 命中 | unver 2 · info 1（`present_continuity`） | unver 2 · info 1（同一条） | ✅ 逐条相同 |
| ③ | 降级阶梯 / wire error | 0 / 0 | 0 / 0（11 行全 `high`、`attempt=1`） | ✅ |
| ④ | `finish_reason != stop` | 0 | 0（11/11 `stop`） | ✅ |
| ⑤ | schema 校验 · 章集合 | 11/11 | 11/11，正式稿落盘无 `.INVALID` | ✅ |
| ⑥ | 有 event 的拍数 · 在场者称呼数 | 106 · 237 | 106 · **221** | 拍数 ✅ · **称呼 ❌（−6.8%）** |
| ⑦ | 段级覆盖 · 零覆盖段 | 458/458 · 0 | 458/458 · 0 | ✅ |

⇒ **门⑥的后半没过。按本条既定规则（任一项劣于基线即判不可用），本次验收判未通过。**
规则登记在实测之前，**不得事后修改**（`docs/LESSONS.md` §17）。

**门⑥失败的形态，与 low 档退化不是同一种**（这一段是给裁决用的事实，不是辩解）：

- 拍数 **106 = 106**，且切分更均匀：中位 **109 → 92 字/拍**、最粗 **234 → 175**
  （low 臂当初是 106 → 87 拍）。
- hinge **105 → 134（+27.6%）**，保真材料反而多了。
- 少掉的 14 个称呼实例里有 1 个是**基线自造的**（`官员们`，b08_10——那正是基线唯一那条
  HARD）；v3 的 221 个**全部在原文里找得到**，`present_names_invented` 从命中变成 ok。
- 但确有真少认：`容妃` / `平阳侯夫人` / `皇帝` / `世子` / `意娘` / `谢姑娘` 六种字面
  在 v3 里一次都没出现（另有 `官员们→官员`、`那名女子→那女子`、`姑娘们→席上的姑娘们`
  三组只是同一个人的字面变体）。
- 可操作的修法方向已经在报告里：`present_continuity`（info）报了 **21 处**
  「相邻拍同地同层、本拍被点名却未列」。

**裁决（2026-08-19，用户）：判验收通过、采纳本实现。** 理由：认为 237 → 221 这段差值
主要来自模型随机性。

⚠⚠ **这是一次「裁决」，不是「门⑥通过了」——两者在报告上必须分得开。**
门⑥的阈值**没有被改小**（改小就是事后改规则，按 `docs/LESSONS.md` §17 本次实验即作废）；
上表里那一行仍然如实写着 `221 ❌`。
⚠ 「随机性」这个归因**本次没有量过**：n=1、只跑了一臂一次，方差不在证据里。
要坐实它需要同一 prompt 字节下再取一个独立样本（改 `--refresh` 或换书），那是另一次付费。
⇒ 下一本书跑完后回头看这个数：若仍落在 220 上下而不是回到 237，那就不是随机性。

- [x] 用户裁决采纳（见上）。原「回滚 / 另开条目」二选一到此关闭。
- [ ] **下一本书跑完时复核在场者称呼数**——这是上面那条归因唯一便宜的证伪机会，
      别让它随本条目一起消失。

- [x] 付费验收跑已做（读数见上）。⚠ 那一跑的臂已随收尾删除；`.llm_cache` 里那 11 份响应
      还在，所以对**同一本书**重跑 `stage2.blueprint` 是 0 成本的（实测 11 行全 `cached`）。
- [x] 收尾已做：`outputs/_ab/` 的 12 个 effort 臂、2 个 `_probe_*`、验收臂本身，以及
      交付书 `_rounds/` 里的两份 v1 蓝图，全部删除（19M → 104K）。
      **只留两份读数存档**：`outputs/_ab/baseline_blueprint_v2.md` 与
      `outputs/_ab/fc13_matrix.md`——它们是本条与 FC-13 全部数字的原始出处。
- [ ] 在改造后的节点上重跑一次 effort 换臂（`tools/effort_ab.py --set
      stage2.blueprint=low`）。A 拿掉的正是 low 档第一个塌的那类能力，若改造后 low 也能
      过门，等于**整个节点降档**，收益远大于「拆成两次调用」那个方案，且不多一次调用、
      不多一道缝。**先量，再决定要不要做那个。**
- [ ] 把 `edge_hole` 从「≥30 字未覆盖区间」升级成段号精确判据。

- [ ] 改 `NodeSpec.effort` 默认值需用户裁决（`tests/test_invariants.py` 现钉着
      `stage3.style` / `stage7.rename` 恒 `None`，改默认要同批改那两行断言）。
- [ ] **降档路径没有任何可观测**：`novel/llm/client.py::LlmClient.ask_json` 三道内容闸失败时
      只做 `cache.evict` + 降一档，既不产 observe 事件也不写日志，`errors` 只在**所有档全灭**时
      才随 `LlmFailed` 出现 ⇒ 事后无法回答「为什么降的档」。补一条带 `why` 的 observe 事件是
      本条继续做下去的前置（否则每次 A/B 都可能混进一次说不清的降档）。
- [ ] `stage5.write` 的 shallow 单臂降档仍够不着（挂载点问题，见上）。本次实测的是**整节点**
      降档，按上表已否决；**不要拿它替 shallow 臂那一项销账**。

验收：所有被测产物由现役写盘路径生成；完整套件的通过/跳过/失败数、每条 skip 理由和上述付费
实验结论一并记录，未完成项不因代码位或预览工具存在而销账。

### TODO-28 · 前端 backend_v1 fixture 与 API 模型之间没有任何对账

`web/src/api/mock.ts` 把 `web/src/fixtures/backend_v1/*.json` 当**现役数据源**用
（那段注释自己写着「现役读的是这一批 —— 与真后端同一形状」），但**没有任何东西在核这句话**：
`web/scripts/check-seams.mjs` 只管「谁可以读它」，不管它长什么样。于是后端每破坏一次形状，
mock 模式就与真后端多分家一点，而三条缝检查全绿。

⚠ 2026-08-19 又多分家一处（**不要手补**，那正是本条禁的动作）：
`check_stage2.json` 里还留着已裁撤的 `anchor_not_verbatim` 那一行、`reading_ch3.json` 里还留着
已删的 `verbatim_recovered` 键与旧的 `BLUEPRINT_SYS` 指纹。它们随
`bounded-fidelity-v3` 一起过期，修法只有「从一本走完全链的书重抓」。

立条目时实测：15 份可映射 fixture 里 **13 份**已通不过当前 `contracts/schema` 模型
（六份 `check_stage*.json` 都带已删的顶层 `retired`；`stage4_ch3.json` 缺 31 项、
`stage7*.json` 各多 6 项；`stage6.json` 连 `candidates[].approach` 的取值面都变了）。
⚠ 这个数字是**问题规模**不是进度分数——销账方式是删掉本条目，不是把它改小。
另两份刻意排除、不在对账面内：`enums.json`（`/api/enums` 刻意无响应模型）与
`style.json`（mock 用 `LegacyStyle` 读、经 `currentMockStyle()` 转成现役形状，**故意**的旧形状）。

- [ ] 从一本走完 stage0～stage7 的书重抓整套 backend_v1 fixture。
- [ ] 同批补一条「fixture ↔ `contracts/schema` 模型」的对账守卫。`StrictModel` 的
  `extra='forbid'` 会自动抓住已删字段，所以真正要留心的是**映射表本身的完整性**：
  必须断言「表的键集 == 目录里的 json 文件集」，否则新增 fixture 会静默漏登记，
  这条守卫就退化成只守老文件（同一形状的漏网见 `docs/LESSONS.md` 第 21 条）。
  两份排除项要在表里显式登记为排除并写明理由，不能靠「忘了写」实现豁免。
- [ ] 顺序约束：**只能在新书首跑之后做**。重抓需要盘上有一本完整的书，而现在产物已全批清空
  ⇒ 现在就加守卫等于制造一片必须靠 xfail/skip 压住的红，那是本仓明令禁止的洗绿形态。

### TODO-29 · 供应商前缀缓存：命中率尚无付费证据，另两项对齐未做

2026-08-18 落了两处**不改变模型看到的内容**的缓存对齐（`novel/stages/stage6/semantic.py::judge_request`
把双盲角色后缀移到 user 末尾；`novel/llm/client.py::LlmClient._warmup_gate` 首发独占）。
形状已由非 corpus 套件钉住，**但供应商侧到底多命中了多少，一个真实数字都还没有**。

- [ ] 在**一本新书**上取证，按 `tools/ab_compare.py` 的 `prompt_cache_hit_rate` /
      `prompt_cache_hit_tokens` 出对比矩阵（该工具已具备全部能力，不要新造）。
      ⚠ **不能拿旧书重跑充数**：首发闸不改 prompt 字节 ⇒ 本地 `.llm_cache/` 键完全不变 ⇒
      重跑会 0 次真上线，那份「全绿」证明不了任何事。
      验收数字要与 FC-13 那条实测的 34.3% 基线对齐着报，别只报改后的绝对值。

### TODO-30 · 三条新结构判据只有读数、没有标定过的正常区间

2026-08-19 换掉 `structure_changed` 的口径（「必须移位」→「三根轴至少动一根」）并补了
`R-5.16`~`R-5.18`。三条判据的**测量**都是确定性的，但**多少算过量一个都没有标定过**，
所以现在一律只报读数、不设阈值（`Q-9`：探针的名字要等标定完再起）。

⚠⚠ **立条目时的读数不能当基线用。** 它们全部取自 `四人成行` 那一份计划，而那份计划有两重
污染：① 它是**旧 prompt 逼出来的分布**——旧口径下够大的章不移位就不许落盘；
② 它是对着**旧版 stage2** 产的（当前 `chapter_blueprints.json` 已是 schema v2，
`from_beats` 里 16/106 个 `beat_id` 在当前蓝图中已不存在）。当时读数：
`cold_open` **11/11 章** · `transition` = `earlier 18 / start 11 / return 6 / later 1 / continue 1`
· 方向可判率 **13/26** 个接缝（判不动：`recall→recall` 6 · 上游漂移 3 · `recall→present` 3 ·
`hearsay→present` 1）· `flashback_closed` 11 章用过 `earlier`，6 章闭合 / **2 章命中** / 3 章判不动。

- [ ] `cold_open` 占比该不该有上界？上界是多少？⚠ 只能在**换 prompt 之后重跑的书**上取分布，
      旧读数量的是被旧口径逼出来的形状。
- [ ] `transition_direction`（`R-5.16`）的可判率正常区间是多少？它是承重读数——
      模型把 `recall` 单元排到段尾就能让接缝不可判，可判率掉到 0 意味着有人在逃逸。
- [ ] `flashback_closed`（`R-5.17`）的命中率正常区间？标定之后再决定它该不该从 `INFO` 升 `HARD`：
      **测量是确定的，「回溯必须收口」这条规范不是**——小说可以在闪回里结束一章。
- [ ] 顺序约束：**只能在新书首跑（含 stage4 付费重跑）之后做**，与 `TODO-21` 同一批盘面。

验收：三个区间各有一句写得出来的结论（含「维持只报不挡」这种结论），并写进
`docs/BACKEND_CONTRACT.md` 对应 R 条目的裁决注。

⚠⚠ **本条原先登记的那条「按 `chapter_index` 排序看命中从第 2 章起跳升」的取证法已被证伪，
两处都错**（2026-08-18 在一本刚跑完全链的 19 章书上实测）：

1. **`chapter_index` 不是章号，是批内序号。** 同一份 ledger 里 `purpose=stage2.blueprint:ch8`
   的 `chapter_index` 是 `1`、`:ch9` 是 `2`。真章号只在 `purpose` 里
   （`novel/llm/ledger.py::CostLedger.record` 的 `chapter_index` 是扇出批内计数）。按它排序
   得到的「章序」是假的。
2. **更要命的是那个形状本身不存在。** 实测 `stage2.blueprint` 的**首发那一条就已经
   hit≈4352**（`BLUEPRINT_SYS` 4073 token，供应商按 64 token 分块 ⇒ 68×64），
   `stage5.write` 首发即 hit=1920（`write_system` 1833–1905 ⇒ 30×64）。原因是
   **system prompt 与书无关、跨书跨跑复用** ⇒ 供应商侧前缀在这次跑开始前就是热的。
   ⇒ 「从第 2 章起跳升」永远观察不到，而**看不到跳升不等于闸没生效**。

⇒ 要隔离首发闸的增量贡献，需要一段**从未发出过的前缀**（改动 system prompt 字节后的首跑，
或一个全新节点），否则拿到的只是「目标状态」而不是「闸的贡献」。在补到那样的样本之前，
本条的诚实结论是**不可测**，不要用一份满命中的账本给它销账。

两项**同期评估过但没做**的（记在这里是为了后来者不必重新推一遍，不是承诺要做）：

- **stage5 user 块重排**（把跨章不变的块提到 user 最前）：预期约 3–4%。没做的理由是它会把
  「本章任务」从第 2 块挪到靠后，**有位置效应**，超出「零语义风险」这一档，必须一次真跑证明
  没写坏成稿。⚠ 另需先判断 `novel/stages/stage5/prompts.py::style_block` 到底变不变——
  有 `chapter_styles` 时它本就是逐章投影，那部分挪上去不会带来任何命中。
- **shallow 臂裁材料**：预期约 1%。没做的理由是 `novel/stages/stage5/prompts.py::Material` 的
  `given` **同时是 `invented_number` / `invented_name` 两条判据的参照集**
  （`novel/stages/stage5/checks.py`）。直接裁下发块会让原本有依据的数字和人名被判成「自造」——
  省下占比约 9% 的 input，换来判据误报。要做必须**先把「判据参照集」与「真正发出去的 blocks」
  拆成两件事**，那是独立改动。

⚠ 两项加起来也只有个位数百分比：输入侧优化的天花板约 7%（FC-13）。要量级收益只有 effort 降档。

### TODO-31 · 生成侧确定性化的 Stage7 余项

- [ ] Stage7 家族/宅邸称呼从 `surname_map` 确定性派生 `X家` / `X府`；
      失败样本是旧 Flash 实验臂里 35 条高置信残留（`孙家/孙府/宋家/宋府`）。

⚠⚠ **`TODO-38` / `TODO-39` 这两个号在 2026-08-22 被两条分支各分配过一次，含义不同。**
两边的 commit 信息都已经写死，改号只会让活着的条目与创建它的 commit 对不上 ⇒ 只标注：

| 号 | `stage01-improvement` 那一份（**已销账，不在本清单里**） | 本清单里活着的这一份 |
|---|---|---|
| `TODO-38` | 前端 enums 夹具与 `payload()` 分家（`3d775cf` 修好） | 内容闸拒因不落账（下面这条） |
| `TODO-39` | 两条守卫把宿主环境当成被测对象（`492a413`/`9d9270c` 修好） | `structure_changed` 换项裁决（下面那条） |

⇒ 读 `docs/BACKEND_CONTRACT.md` §十 时注意：2026-08-22 那行「顺带销 TODO-38/39」说的是
**左边那一列**，与本清单里这两条无关。

### TODO-38 · 内容闸拒了，但账上不写「拒在哪个字段」

`_service/llm_audit.jsonl` 的 `gate_decision` 记 `gate`（`schema` / `parse` / `validator` /
`answer_lost` / `require` / `empty_text`；`answer_lost` 是 2026-08-22 从 `parse` 里劈出来的，
见本文件 TODO-40 与 `docs/BACKEND_CONTRACT.md` 同日那一行）、
`reasoning_effort`、`sample`、`rung_index`、`resample`——**唯独不记那一拒的内容**。
于是每一次「这一批改动有没有让 schema 拒变少」都只能靠「这一次过没过」来回答，
而那是个 n=1 的信号。

2026-08-22 实测坐实了它的代价：stage4 的响应侧四个枚举加前缀之前，一次全书规划
**6 次 wire 全被内容闸拒**（`intent='turn'`、`narrative_role='recall'`，串表）；
加前缀之后同一本书的收官轮仍有 1 次 `schema` 拒、同档重采后通过。
⇒ **「前缀化把串表清零了」这句话证不出来**：账上只有 `gate: schema` 四个字，
拒在哪个字段、是不是同一种错，一个字都没有。ir1 那次能报出「非法 enum 0」，
靠的是当时人工盯着控制台，不是这条账。

- [x] **已落（2026-08-22）**：`gate_decision` 新增 `reject_detail`
      = `[{loc, type, count}, …]`。pydantic 的 `ValidationError.errors()` 只取
      `loc` + `type`（**`input` 不取，`msg` 也不取**——`literal_error` 的 msg 里就带着
      `input_value=...`），按 `(字段路径, 错误类型)` 去重计数，**下标压成 `[]`**
      （`beats[].role` 而不是 `beats.0.role`，否则答不了「是不是同一种错」）。
      `require` 那一支记缺的顶层键；`parse` 那一支记 `error`/`pos`/`doc_chars`/`truncated`。
      ⚠⚠ **`truncated` 取自 `finish_reason` 而不是从 `pos` 推**：
      `Unterminated string starting at` 的 `pos` 指向那个字符串的**起点**——实测
      `{"voice": {"a": 1}, "surface_hab` 是 `pos=20` / `len=32`，最典型的截断会被
      任何「pos 贴不贴末尾」的启发式判成「中间坏的」，然后被推去做同档重采
      （而截断是唯一不该重采的那一类）。这条由测试钉住。
      ⚠ 施工的直接动因是 2026-08-22 现场咬的那一口：一次整跑被超时杀掉，
      stdout 连同 `why` 一起没了，账上只剩 `gate: schema` 四个字，
      两次拒**再也查不出拒在哪个字段**。
- [ ] 有了它才谈得上一条读数：**「按字段分的内容闸拒分布」**，
      而那正是判断「下一个该给哪个字段加前缀 / 换形状」的唯一依据。
- [ ] ⚠ 不要顺手把整段响应写进账：`llm_audit.jsonl` 与产物同一棵目录树，
      而正文外发边界（`R-12.x`）管的是同一批字节。

### TODO-39 · `structure_changed` 该不该把恒真的 `axis_group` 换成 `axis_reshape`

`structure_changed`（`R-5.6`）的判定式有两项：`axis_group`（分组序列 ≠ 恒等基线）
与 `axis_delivery`（存在 `summary` 单元）。**前者在生产落点上恒真**——
`stage4.plan --check-only` 实测 **13/13 个可重构章全靠它过关、`axis_entry` 0/13**，
批 2 的 run3 又是 13/13。⇒ 这条 HARD 实际只在判「有没有合并过」，
而「成稿只改了措辞」那条链上真正没人判的那一维是**详略**。

2026-08-22（批 3）加了候选第四轴的读数出口 `structure_reshaped`（`R-5.19`）：
`axis_reshape` = 本章有拍的 `预算/源` 相对本章均值冲出 `PLAN_BUDGET_BAND`。
**判定式一行未动**，两条守卫测试把这条线钉住
（`tests/test_stage4.py::test_the_reconstruction_gate_still_judges_grouping_not_pacing`）。

**第一份读数已经有了**（2026-08-22，`--check-only` 在批 2 run3 那份计划上，**0 成本**）：

| | `axis_group`（现役） | `axis_reshape`（候选） |
|---|--:|--:|
| 可重构章命中 | **13/13** | **7/13** |
| 换成它之后 `structure_changed` 会红 | — | **6 章**（3/6/11/13/19/22） |
| 现役口径实际红 | **0 章** | — |

⇒ **「换项只是换了个恒真项」这个担忧被证伪**：7/13 有真实判别力。
（担忧的来源是「40/57 拍落在 0.9~1.1 之外」——那是相对 **1.0** 量的，
不是相对本章均值 `r̄`，两个数本来就不能互相代入。）
⚠⚠ **但换项不是等价替换**：那 6 章一个 `summary` 都没有 ⇒ `axis_delivery` 救不了它们，
换项之后每一章都要付一次定向重规划。**这是一次产品语义收紧，不是清理。**

- [x] **新分布已经拿到了**（2026-08-22 批 3 整跑验收，`pro@high`，1 次实付 / exit 0）：

      | | `axis_group`（现役） | `axis_reshape`（候选） |
      |---|--:|--:|
      | 可重构章命中 | **13/13** | **10/13**（旧 7/13） |
      | 换成它之后会红 | — | **3 章**（`[10, 11, 22]`；旧 6 章 `[3,6,11,13,19,22]`） |
      | 判不动 | — | **0** |
      | 现役口径实际红 | **0 章** | — |

      ⇒ **换项的代价降了一半**（6 章 → 3 章要付定向重规划），裁决条件比上一版好。
      ⚠⚠ **但不要把它归因给 `spine`**：这一版同时换了臂（`flash → pro`）、改了 prompt
      （`spine` 变引用 + 骨架带编号），三个自变量一起动。

- [ ] ⚠⚠ **批 4 把这条裁决的输入整个换掉了，拍板前必须重新取一份读数。**
      2026-08-22 批 4 起 `budget_chars` 由 `measure.allocate_budgets` 从**单元级档位**算出：
      `W_PEAK` 的相对倍率是 2.6、`W_BRUSH` 是 0.38，**两个都在 `PLAN_BUDGET_BAND` 之外**
      ⇒ 模型只要在一章里用过一次非 `W_MID` 的档位，那一章的 `axis_reshape` 就成立。
      0 成本探针（真书 23 章，合成响应把五个档位轮着填，`--check-only` 同一段判据代码）：
      **`axis_reshape` 13/13**，换项反事实红 **0 章**。
      ⚠ 那是**上界不是预测**：轮着填不是任何一个模型的真实分布，真分布要等一次整跑。
      ⚠⚠ 但方向已经清楚：**「`axis_reshape` 有判别力」这个结论是在「模型逐拍手写整数」
      那个世界里量出来的**，而那个世界已经没有了。今天它更像第二个恒真项——
      换项要解决的问题（现役那一项恒真、判别力为零）会被原样搬过去。
      ⇒ 拍板前拿批 4 那次整跑的分布重算这张表；若仍是 13/13，本条应当**结案不做**，
      而「详略这一维没人判」要另找出口（例如把 `axis_reshape` 的阈值挂在**档位的方差**上，
      而不是挂在一个由代码算出来、必然越带的比值上）。

- [x] **批 4 整跑的分布已经拿到，而它是决定性的**（2026-08-22，`pro@high`，1 次实付 / exit 0）：

      | | `axis_group`（现役） | `axis_reshape`（候选） |
      |---|--:|--:|
      | 可重构章命中 | **13/13** | **13/13**（批 3 是 10/13、批 2 是 7/13） |
      | 换成它之后会红 | — | **0 章**（批 3 是 3 章、批 2 是 6 章） |
      | 现役口径实际红 | **0 章** | — |

      ⇒ **换项已经是一次 no-op**：两项都恒真、红的章集合都是空集。
      换项要解决的问题（现役那一项恒真、判别力为零）**被原样搬到了候选项上**。
      根因不是巧合：批 4 起 `budget_chars` 由档位算出，而 `W_PEAK`(2.6) 与 `W_BRUSH`(0.38)
      **都在 `PLAN_BUDGET_BAND` 之外** ⇒ 模型只要在一章里用过一次非 `W_MID`，
      `axis_reshape` 就成立；这一跑 13/23 章用了非 `even` 的章级档、55 个单元里 39 个非 `mid`。
      ⇒ **建议结案不做**（读数齐了，等你拍板）。
      ⚠ 「详略这一维没人判」这个问题**仍然没有出口**，而且比从前更难：它现在是一个
      由代码算出来的比值，量它等于量代码自己。真要判，得判**模型给的档位分布**
      （例如「一章里所有单元都同档」= 没做过章内详略决策），那是一条新判据不是换项。
      ⚠ 同一跑回答了那个新问题——**「`spine` 让模型更愿意给主干拍加预算」= 没有**：
      挑第一件的单元每件事平均 76 字、挑后面的 77 字，几乎相等 ⇒ 主次与预算是两个
      各自独立的决策。所以 `axis_reshape` 那 3 章的降幅**不是** `spine` 的传导。
- [ ] ⚠⚠ **裁决前先看一条同跑长出来的反向读数**：章级倍率极差 0.357 → **0.209**、
      `≤0.85 / ≥1.15` 的章 1 / 3 → **0 / 0**、`flat_chapter` 命中 14/22 → **16/19 章**
      ⇒ `pro` 这一版**比 flash 那版更平**。`axis_reshape` 命中率却升了（7/13 → 10/13），
      两者不矛盾（前者按绝对字数、后者按 `预算/源` 相对本章均值），但它们合起来说明
      **这条候选轴对「模型换了一版」很敏感** ⇒ 换项之前至少要在第二本书上再读一轮，
      否则钉的是这一本、这一版模型的分布。
- [ ] ⚠ `axis_group` 的恒真是**当前 prompt 下的稳定行为，不是结构性的**：
      批 2 的 run1（那份被 3 条 HARD 退回的坏计划）上它只有 4/13。
      裁决时要把这个 n=1 的反例一起写进去，否则下一次 prompt 一改，
      换项的前提可能已经不成立了。
- [ ] 换项若成立，两条反向守卫**一条都不许改成迁就新判定式**：
      `--identity` 必须**仍然**在 `structure_changed` 上红、
      `test_keeping_the_source_order_is_not_a_violation_when_something_else_changed`
      必须**仍然**绿——这一对是「顺叙不违规」那次学费的验收断言。
      ⚠ 也不许写成三个松散的 `or`：`R-5.6` 的换口径记录反对的正是那个形状。

验收：一句写得出来的结论（含「维持现状」这种结论），写进 `R-5.6` 的换口径记录，
并把 `R-5.19` 的 note 与 `structure_reshaped` 的注释同批改口。

### TODO-40 · flash@high 的空正文：是模型边界，还是 `json_object` 的交互？

2026-08-22 的复盘把根因钉到了**模型那一侧**：同一份调用代码、同一批参数，
`flash@high` 60/418（14%）返回「正文 0 token + `finish_reason=stop`」，
而 `pro@high` 0/249、`flash@low` 0/70。供应商 `usage` 自称
`reasoning_tokens == completion_tokens`，且空响应的总 token ÷ 同 purpose 正文中位数
**中位 1.06**（`stage4.plan` 的 5 次是 9 208~11 241，成功正文 9 258~11 665）
⇒ 模型不是「想很久然后放弃」，是**整份答案写进了思考通道**（`</think>` 边界没吐出来）。
逐项对照官方文档也全部合法：`reasoning_effort` 取值、`extra_body.thinking`、
思考模式下的 JSON 模式、`max_tokens=96000 ≤ 384K`、思考模式下没传 `temperature`。

**唯一还没排除的「调用方式」是 `response_format={'type':'json_object'}`**——
它是官方自己打了「偶尔返回空 content，正在优化」标签的那个开关，
且 vLLM 上 DeepSeek 思考模式 + JSON response_format 有**同形状**的公开 bug
（JSON 落进 reasoning、content 为空；成因是 JSON grammar 在推理边界判定之前就生效）。
官方服务端内部看不见，但两种机制都在对方那一侧。

⚠⚠ **本段下面那条关于 `spine` 的推断已被同日实测推翻，保留原文只是为了让撤回看得见。**
原文：「批 2 高档 1/5 空 → 批 3 4/4 空，Fisher 单侧 p≈0.04 ⇒ 那 14 行大概率抬高了概率」。
**实测：同一份批 3 prompt 当天重采是 3/7（43%），4/4 没复现** ⇒ 那个基线不稳，
`spine` 是不是调节因子**现有数据答不了**。见本条目下方「结果」。
（仍然成立的只有一句：同一失败模式在 08-19、08-20 就出现过 ⇒ **先于 `spine` 存在**。）

**预登记的判据（花钱之前写在这里）：**

- [ ] 0 改动对照：同一份批 3 prompt 在 `flash@high` 上 `json_mode=False` 跑 **8 次**，
      独立 `NOVEL_CACHE_DIR` + 强制真发（否则阶梯横扫缓存会让实验静默不发生，
      见 `reference-ab-arm-cache-replay`）。flash 单次约 $0.03，全程 < $0.5。
- [ ] **空响应归零 ⇒ 去掉 `response_format` 就是修法**（届时要另解决「怎么保证是 JSON」，
      本仓 `_strip_fences` 已经在扛围栏，`ask_json` 的三道闸也还在）。
- [ ] **仍约 30% ⇒ 是模型边界**，只剩两条路：`stage4.plan` 固定 `pro`
      （与 2026-08-19 的 FC-13 A/B 结论同向），或加大同档采样
      （`MAX_SAMPLES_PER_RUNG=2` 在空响应率 0.33~0.5 时双空概率 11~25%，
      随后掉进 `low`——而 `low` 在这个节点的已知劣化（猜 event 编号）才是真正吃掉产物的那步）。
- [ ] ⚠ 读结论**必须看留证**（`<书目录>/_answer_lost/`，2026-08-22 落地）：
      「思考通道里装的是不是整份 JSON」在此之前只能拿 token 体积推断，
      现在有全文可读 ⇒ 同一笔钱能拿到直接证据，别再只数「空/不空」。

**结果（2026-08-22 07:43–08:32 实付，11 次 wire，`tools/json_mode_control.py`）**

同场次 A/B。同一本书（无情道 / `outputs/_b2`）、同一节点、`flash` / `high` / `thinking` /
`max_tokens=96000`，两臂的请求指纹**只差 `json_mode` 一位**：
`on` = `c61c671c559fa4a852ef1f89fc126dde`（**与批 3 那 4 次全空的请求逐字相同**）、
`off` = `0f5529c170b4245672ea989bdfa60a10`。原始报告
`outputs/_b2/_service/json_mode_control_20260822.json`，wire 全部记在同目录 `ledger.jsonl`。

| 臂 | 空正文 | 解析得动 JSON | 合 `PlanResponse` | completion 合计 | 每份可用计划 |
|---|--:|--:|--:|--:|--:|
| `json_mode=off` n=4 | **0/4** | 4/4 | 3/4 | 201 083 | 67 028 |
| `json_mode=on` n=7 | **3/7** | 4/7 | 2/7 | 136 427 | 68 214 |

Fisher 单侧 p≈0.21 ⇒ **方向对，但没到显著**。三条已经站得住的：

1. ✅ **机制坐实，不再是推断。** 3 份留证里，推理通道那段**整体就是一份合法 JSON**：
   23 章齐全、括号配平、**一个字推敲过程都没混进去**，其中 1 份（`3e31815f…`）
   **完全合 `PlanResponse` 契约**。⇒「答案写完了，只是走错了通道」是直接观测。
   在留证落地之前这个问题只能拿 token 体积猜（当时中位 1.06×）。
2. ⚠⚠ **批 3「4/4 全空」没复现**——今天同一份 prompt 是 **3/7（43%）**。
   ⇒ 「`spine` 把空响应率抬到 100%、Fisher p≈0.04」那句话**建立在一个不稳的基线上，撤回**。
   `spine` 是不是调节因子，现有数据答不了；要答得先把两臂各做到 n≥8。
3. ⚠ **`json_object` 并不买到 schema 合法。** 它只保证语法是 JSON：on 臂只有 2/7 合契约，
   而 off 臂 4/4 都解析得动（靠 `_strip_fences`）、3/4 合契约。两臂**在同一种 enum 越界上
   翻车**（`G_TURN` / `R_SCENE` / 多出 `rationale_note`）——那是 TODO-38 与批 4 前缀化的地盘，
   不是本条的。⇒ 去掉 `response_format` 在「还拿不拿得到 JSON」这一维上**没有代价**。

**「输出太长所以答案流丢了」——这条因果被账本否掉**（2026-08-22，0 成本重算，
4 份 ledger 共 880 行，只取 `outcome=='ok' and not cached`）：

| 节点 | flash@high n | 空正文 | 率 | 单次正文 token 上限 |
|---|--:|--:|--:|--:|
| `stage5.write`（**已逐章**） | 66 | 30 | **45.5%** | 1 335 |
| `stage4.plan`（全书一次） | 30 | 13 | 43.3% | 12 024 |
| `stage2.blueprint`（**已逐章**） | 109 | 25 | 22.9% | 5 610 |
| `stage3.style` | 215 | 20 | 9.3% | 1 310 |

**最短的那个节点率最高**，按 `max_tokens` 分桶也不单调（16k 桶 23.2% / 64k 桶 5.1% /
96k 桶 37.1%），`low` 档在每个节点上都是 0 次。⇒ 这是 `flash @ high/think` 的**档位行为**，
不是输出长度的函数。⚠⚠ **这条读数买到的是一个不该做的改动**：它是「把 `stage2.blueprint` /
`stage4.plan` 的产物拆成逐章文件」那份方案的动机来源，而拆开**买不到**空正文率下降
（`stage2.blueprint` 本来就是逐章调用，只是落盘时聚合）。用户 2026-08-22 知情后
**裁决不拆、保持现状** ⇒ 该方案作废，这段读数留在这里，免得下一次有人拿同一个动机重提。

**未决，以及本次新长出来的：**

- [ ] 要判到 p<0.05，两臂各约需 8 个样本（现 off=4 / on=7）。⚠ 但先看这条读数再决定值不值：
      **每份可用计划的 completion 两臂几乎相等**（67 028 vs 68 214）⇒ 换 `json_mode`
      **省不了钱，只换失败形态**（on 臂失败成空响应，off 臂失败成 schema 拒）。
      而 schema 拒是**同档重采就能救**的那一类，空响应也是——两边都不掉档。
- [x] **产品语义裁决已下：`answer_lost` 开「推理通道整体过得了同一套闸就采纳」这条路**
      （2026-08-22 用户裁决，`novel/llm/client.py::SALVAGE_ANSWER_FROM_REASONING`）。
      动因就是 `3e31815f…`：一份**完全合契约、可用**的计划被当成「这一档不行」丢掉、还付了钱。
      ⚠ **落地比预登记的判据更严**：不是「`json.loads` + `model_validate` 双过」，
      而是**同一套四道闸全过**（`json.loads` → `require` → `model_validate` → 调用点
      `validator`），且与正常路径共用同一个 `gate_payload`——两条路各写一遍判定，
      就是给捞出来的那份开了一个自己的契约。
      ⚠ 风险面（「那段可能是中途稿」）**没有靠 n 解决，靠的是闸**：中途稿过不了 `require`
      与 `model_validate`；`finish_reason='length'` 那一半按边界③根本不捞。
      ⚠ 账上分得出来：`gate_decision` 与 `answer_lost` 两种审计行都带 `salvaged`，
      留证照写不误 ⇒ 「这本书有几章的产物不是从 `content` 来的」事后答得了。
      ⚠ **认下来的代价**：捞回来的那一份仍进不了缓存（`ResponseCache.put` 拒写空正文）
      ⇒ 这条路省的是**本次的重采**，不是下次重跑的钱。
- [ ] ⚠ **只量过、也只开了 `ask_json` 这一侧。** `ask_text`（`stage5.write` 的正文）空响应时，
      推理通道里是不是同样的完整成品，一次都没量过——那一侧「半成品」的老说法**可能仍然成立**
      （正文不像 JSON 有明确的收尾边界），而且**它没有等价的客观闸**：JSON 那边「解析得动 +
      合契约」是可判的证据，自然语言那边「这段是答案还是推敲」判不出来。
      ⇒ 结构上就不给 `ask_text` 开这条路。别拿本条的结论去覆盖它。
- [ ] ⚠ **紧迫性已经降了一档**：2026-08-22 同日 `stage4.plan` 钉死 pro（pro 全表
      `answer_lost` 0/249）⇒ 这条路现在的受益者是仍跑 flash 的 stage2/3/5，
      而它们逐章重采一次只要约 5.7k output。**这条路的真实收益要等下一次 flash 整跑才有读数。**

验收：`novel/llm/client.py` 模块 docstring ① 与 `novel/llm/transport.py::ChatResponse`
的成因两句已同批改口（2026-08-22）；采纳路径同日落地并由
`tests/test_llm.py` 四条钉住（捞成功不重采 · 四道闸全过才算 · 截断与 `ask_text` 都不捞 ·
两种审计行都带 `salvaged` 且捞回来的不进缓存）。`ask_text` 那一侧未开，条目保留。

### TODO-41 · Stage2 `extraction-ir-v3.1` 默认候选的双书验收

2026-08-23 已按 `docs/stage2_contract_experiment.md` §十五的预注册矩阵完成三臂付费实测，
默认继续 `extraction-ir-v2`。v3 reasoning 相对同场 v2 下降 69.6%，但 hinge 只有 v2 的
85.7%，并新增 6 个在场者歧义与 2 个名册外引用，一级质量门失败；v3.1 全部模型调用完成后
在编译正式产物时因空 `SourceSpan.normalized` 崩溃，产物未落盘，reasoning 还比 v3 高 43.0%。
原始 wire、adoption、产物 hash 与全矩阵已经写回实验手册，不再缺付费证据。

同日按用户指定只跑了修复后的 `v3.1-fixed` 单臂诊断：候选生成器已在截断前剔除
`content_only == ''` 的纯标点项，真书 243 项候选全部能构造正式 `SourceSpan`；定向、冻结五文件
与全量非 corpus 回归 exit 0，真实主跑与独立 `--check-only` 也均 exit 0。正式产物为 134 beat /
175 hinge / 0 HARD，证明 compiler 结构漏洞已修好；但人物歧义仍为 6，reasoning 163,987 又比
旧 v3 高 45.5%，所以默认仍保持 `extraction-ir-v2`。这次跨了代码冻结且没有重跑 v2/v3，
只能作诊断，不能冒充新的严格矩阵。完整证据见 `docs/stage2_contract_experiment.md` §十五。

随后复核已改变问题边界，详见 `docs/stage2_contract_experiment.md` §十六：6 项人物歧义全是
判据把 v3/v3.1 误走成开放字面引用造成的假红，按响应契约取 `present_char_ids` 后为 **0/285**；
但对候选/phrase 密集的 5 章做盲序抽样，覆盖 63/175 条 hinge，确认 175 里确有普通对白、
非因果数字以及整句/子串重复，不能再把“更多”直接当成“更好”。冷揭示差距也是真问题：全书抽取
把第一章已明示给读者的主角身份登记成秘密，却漏了第 16 章“黎玄从收徒当天就已知情”的后置揭示。

零成本修复已完成：判据从 `CONTRACTS` 的关闭引用能力派生；数字候选不再从“第二天/下一秒”
截出“二天/一秒”；v3.1 prompt 恢复 hinge 类别、反批量选择与反嵌套规则；全书秘密 prompt 明确
区分世界内角色不知与读者尚未知。定向 118 项非 corpus 回归 exit 0。**这不改变盘上
`ChapterBlueprints` artifact schema v2**；待翻的是响应契约默认值，不是 artifact 版本。
冻结契约五文件 325 项、完整非 corpus 回归、前端 seams/typecheck/build 亦均 exit 0；前端验证
只读复用了同锁文件主 worktree 的依赖，未安装或改写依赖。

双书真实付费验收已完成，完整账本、hash、盲审与恢复过程见 §十六。两份最终稿都能达到正式 schema、
fresh/match、0 HARD、span 100% 和关闭人物引用四项全 0；无情道 global 也恢复了 ch16 的真实冷揭示。
但预注册默认门失败，`BLUEPRINT_ARCHITECTURE` 必须继续是 `extraction-ir-v2`：

- 无情道新稿 130 beat / 133 hinge，密集章已消除数字后缀与嵌套，稀疏章却漏掉 ch4 的笑点落点和
  ch12 的关系证据；reasoning/completion 还分别比 Pro v1.1 高 5.0%/5.9%，没有保住既有优势；
- 偶像公寓首轮 ch1/ch7 被 `beats_coarse` 拦截，需两章付费重生成和摘要重做才落正式稿；正常
  19 调拓扑实际用了 23 调，不能称跨书首轮稳定。

失败后已完成下一候选的零成本施工：同相脚手架补回对话转折切拍规则，v3.1 hinge 明确“反批量不等于
少交”，保留关系证据、后文回收原话和笑点最后一击。随后用户授权继续付费诊断；同一正文、同一
global、同一 `5ce162d` 代码上的 `v3.1 + Pro` 已完成，详见 §十七。它找回 ch4/ch12/ch13/ch18/ch22
多项 Flash 真漏项，并把人物引用四项收为 0；但最终 152 beat / 134 hinge，ch22 又出现切分偏细风险，
completion 也比 Pro v1.1 高 4.7%，所以结论是“架构有上限、Flash 仍需约束”，不是“v3.1 已胜”。

第一版结构化教师蒸馏已真实烟测并**否决**：v3.1 micro-event 增加 `function` / `fidelity` 闭集后，
无情道 ch4/ch12/ch13 的真漏项均补回，ch22 从 3 beat 修到与 Pro v1.1 相同的 7 beat；偶像 ch1/ch7
也不再粗切。但 13 个目标章用了 17 调，4 章因自报义务重采；completion/reasoning 为
306,190/270,735，比旧冻结同批章高 76.3%/78.0%。偶像 ch1、ch13 又分别升到 21、26 条 hinge，
普通对白被批量抬成保真点。质量召回成立不等于默认候选通过，详见 §十七。

该响应字段与 validator 已撤回，system/hint 恢复到 e268 的 6,577/538 字。当前只保留两项不增加模型
推理任务的代码优化：候选按引语/数字/因果分层并保留各族首尾；章节 HARD 在摘要调用前用同一生产判据
预检，使失败恢复由 `N+1+k+1` 收为 `N+k+1`。盘上 `ChapterBlueprints` 仍为 v2：

- [x] 先用无情道 ch4/ch12/ch13/ch22、偶像 ch1/ch7 做 6 章核心烟测；必须补回笑点、关系证据与
  揭示回收，ch22 保持约 5–9 beat，ch1/ch7 不粗切，同时不得出现普通对白 hinge 系统性反弹；
- [x] 核心通过后再补无情道 ch16/ch18/ch19/ch21 与偶像 ch10/ch13/ch16 的密集章烟测；同批章
  completion/reasoning 不得比旧冻结读数异常反弹，且首轮重采率不能再接近 4/13；
- [ ] 烟测通过后才用独立空缓存整跑两本开发书；两书都须首轮通过 §十六质量/成本门，失败仍保留
  `BLUEPRINT_ARCHITECTURE = 'extraction-ir-v2'`；
- [ ] 开发书冻结后才启封第三本结构不同的小说，固定同 global 做 Pro v1.1 / Flash v2 /
  Flash v3.1 三臂外部验证；不得看第三书结果继续调参后仍称 holdout；
- [ ] 只有开发书和外部验证共同支持，才把 `contracts/invariants.py::BLUEPRINT_ARCHITECTURE`
  切到 `extraction-ir-v3.1`；继续保留 `extraction-ir-v2` 用于显式回退和存量产物重检。

2026-08-23 第二轮核心烟测已完成但**没有通过第一项**。撤回结构化自报义务后的最小候选在 6 章
全部首轮成功，合计 6 调、prompt 42,485、completion 77,443、reasoning 67,920；无情道
ch4/ch12/ch13 的笑点、关系证据与揭示回收均找回，ch22 为 5 beat / 8 hinge、关系链完整。
但偶像 ch1 为 11 beat / 20 hinge，普通介绍、追问、楼梯接话、正式介绍与一般时长仍被系统性抬成
hinge；ch7 的 7 beat / 12 hinge 可接受。故核心门仍失败，不能扩大到密集章整包或两书整跑。

随后两种不增加章级 prompt 的低思考后处理也已用真实 API **否决**：①对既有 20 条做 keep/drop
只删 2 条训斥，普通噪声大多保留；②冻结 beat 后从未预选候选独立重选，偶像 ch1/ch7 各选 16 条，
还把“四个”当硬数字；无情道对照又漏掉 ch4“抽出隐藏款了”、ch12 关系原话与 ch13 戏骨仙揭示。
两者虽分别只用 2,703/179 与 6,807/2,066 prompt/completion、reasoning 都为 0，但精度/召回不合格，
不接生产。下一候选只收紧**候选曝光预算**（对话密集章不再给满 32 条机械候选，仍保留 phrase 兜底
和章节位置分层），先做零成本候选覆盖投影再复跑核心章；不再润色已否决的选择器 prompt。

候选上限 32→20 的真实核心与密集章烟测随后通过，详见 §十七。无情道 8 章保住笑点、关系原话、
身份揭示与 ch22 五步关系链；偶像 ch1 从 20 hinge 收到 15，预算因果与拒绝链未漏，剩余约 3–4 条
普通/重复项但已不是系统性抬升，ch7/ch10/ch16 也通过人工逐段核对。13 章按**实际产生被采纳响应**的
账本归并，计入 ch18 一次 `answer_lost` 重采以及 ch13 对救回稿的正常正文对照后，为 15 调、
prompt 110,031、completion 160,153、reasoning 138,435；相对旧冻结同 13 章分别少 3 调、低
18.0%/7.8%/9.0%，也没有结构化候选 4/13 的高成本重采。

其中偶像 ch13 揭示了一个与 prompt 无关的质量缝：首个 `content` 为空、从 `reasoning` 救回的合法
JSON 为 15 beat / 12 hinge、15/15 地点 unknown；同一请求正常正文重采为 10 / 14、10/10 地点明确，
切拍也更连贯。故只对 `extraction-ir-v3.1` 把“是否接纳 reasoning salvage”登记为响应契约能力并关闭，
由既有客户端同档重采；其他契约及全局 salvage 不变。零成本组合回归 211 项通过。下一步仍是独立
空缓存双书整跑，不因烟测通过提前翻默认；第三本 holdout 继续封存。

## 条目生命周期

1. 开工前补足可复现输入、预期最终状态和受影响的守卫。
2. 改动同时覆盖真实消费者、失败路径、文档与测试，不以“能构造 schema”为完成。
3. 验收通过即删除条目；不要在活动清单里保留已完成施工史。
