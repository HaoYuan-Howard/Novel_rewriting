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
  产物必须由现役 producer 按 `ARTIFACT_SPECS.current_version` 写出。**现役全表恒 v1，且没有
  迁移机制**（2026-08-18 归零，理由见 `contracts/schema/__init__.py` 模块头【版本】）——
  这一条因此比过去简单：盘上任何一个不是 v1 的标记都说明那份产物不是这一跑产的。
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

## 条目生命周期

1. 开工前补足可复现输入、预期最终状态和受影响的守卫。
2. 改动同时覆盖真实消费者、失败路径、文档与测试，不以“能构造 schema”为完成。
3. 验收通过即删除条目；不要在活动清单里保留已完成施工史。
