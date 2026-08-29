# 代批与终审契约

## 目录

- [委托边界](#委托边界)
- [通用裁决格式](#通用裁决格式)
- [stage0 代批](#stage0-代批)
- [Stage2 逐项复核](#stage2-逐项复核)
- [Stage4 结构代批](#stage4-结构代批)
- [Stage5 写作授权](#stage5-写作授权)
- [stage6 代批](#stage6-代批)
- [stage7 代批](#stage7-代批)
- [成书逐字对账](#成书逐字对账)
- [终审包](#终审包)

## 委托边界

把下列标准人工闸门作为受托 agent 的工作，不在每个闸门重新询问用户。代批权限只允许：

- 依据当前产物、报告、源文和既定判据批准或否决；
- 使用已登记的受托编辑面修复候选、**整章正文**、开篇文案或映射——那些入口就是留给本 skill 的，
  不必因为它们叫「人工输入」就回头找用户（[修复状态机](recovery.md)「允许写入面」开头那一节说明了为什么）；
- 修复 `ResponseRepairRequired` 点名的隔离 `candidate.json`，再经 `response-repair` 提交并让原调用点
  以完整 JSON/schema/validator 闸复验；这项权限只补忠实可恢复的结构，不能发明事实或绕过内容判据；
- 在终审包中完整披露无法确定、已裁不修和所选方案的风险。

代批权限不允许：

- 在用户已明确收回某一项授权后仍推定它（两项授权默认都在，不必索要，见 [接口契约](../SKILL.md#接口契约)）；
- 改变公开 API、判据严重度、产品语义或模型策略；
- 删除 finding、伪造 freshness、编辑正式产物绕过闸门；
- 把没有证据的审美偏好写成客观通过。

如果争议属于产品语义而非单书裁决，安全停机并写出决策点；不要为了无人值守静默发明新规则。

## 通用裁决格式

每次代批立即在终审包草稿中记录：

```text
闸门：<node>
决定：<approve / pick N / reject-and-repair / stop>
检查对象：<产物路径 + sha1/完整主体 SHA-256/round/label>
机械证据：<badge、HARD 命中数/分母、关键 info>
语义证据：<结论、分母、freshness；不适用时写不适用>
被否决项：<方案 + 具体缺陷>
不可验证项：<unver/human_only + agent 的人工审阅结论>
后续验证：<命令 + 退出码 + 最新产物标识>
```

不要只写“看起来没问题”或“全绿”。所有结论都要能回到文件、候选 label、rule id 或逐字扫描结果。

## stage0 代批

在运行 `stage0.approve` 前，先从仓根生成只读工作包：

```text
<PY> -X utf8 .claude/skills/novel-pipeline-operator/scripts/stage0_review_workpack.py --base <BASE> --sid <SID>
```

脚本复用生产 Stage0 check、审校分区与 `scan_text`，不直读产物、不审批、不写盘；
`automated_decision` 恒为空。它另有一份固定版本的**审阅型**平台/广告模式，只用来缩小
人工阅读面，不是第二套剔除判据。模式命中必须逐条读上下文，零命中仍须核首尾；章号、顺序与
连续性属于 Stage1，Stage0 工作包不输出也不要求审批这些读数。
完成阅读后只使用工作包 `approval_flags` 给出的 `--expected-revision-id <revision_id>` 提交；
读包到提交之间发生 edit/adopt 时旧 revision 会被拒，必须重新生成并审阅工作包，不能去掉乐观锁重试。
工作包默认完整输出每个剔除段及其前后保留边界；只有执行者显式限制预览长度时，截断项才需要
回审校视图补读全文，不能凭头尾摘要批准。
若当前轮来自人工编辑、而生产分区只能给出全 keep 的 current clean，脚本会以
`human_edit_partition_unproven` 关闭式停止：这个形状分不出“确实零剔除”和“人工直接删改正文”，
必须回完整 source 与 clean_text 做全文差异审阅，不能套用下面的零剔除快捷口径。
批准理由字段只是带“待确认”的草稿，完成下列阅读后才能删去待确认措辞并提交：

1. 逐段判断被剔除内容是否确为广告、平台尾巴、元数据或非正文；任何情节、人物行动、对白、章节正文被剔除都先修复。
2. 核对总码点、保留码点、剔除段数与保留率；异常低的保留率必须解释，不能只看 HARD。
3. 检查正文开头、结尾和剔除边界，避免截断句子或把正文前言误删。
4. 确认除待批准项外无 HARD，所有 warning 均已人工判断。
5. 需要修改时只走 `stage0.edit` 的正式编辑入口，复检后再批准。

记录每个被删段的语义类别；零剔除也要记录分母和边界抽查结果。

**零剔除时这份职责不是消失，是掉转方向。** 分母为零 ⇒ 没有"被误删的段"可判 ⇒
唯一还能出错的方向是**漏剔**，所以改做反向抽查，并把它当作本次代批的实际证据：

1. 只读工作包按登记版本扫描平台尾巴/广告模式，并记录**扫了多少类、各命中几处**；
   不再为每本书临时拼词表，也不把这些审阅模式搬进生产剔除规则；
2. 每一个命中都要逐条读上下文再定性——现代题材里社交软件、人群、群聊这类词是正文自然用词，
   凭词表命中就判广告会把正文当垃圾删掉；
3. 抽查首尾边界：首行是否为正文首句（而非标题/元数据残留），尾行是否为完结标记或正常收束，
   有无被截断的句子；
4. 顺带核一次结构分母（总行数/非空行、独占数字行的章号集合、第一个章号之前是否为文案区）。

零命中要写成"扫了 N 个模式、0 命中"，不能写成"没问题"——前者有分母，后者没有。

## Stage2 逐项复核

Blueprint 不等 Global 正式稿：最新 Global 候选通过当前 schema 并物化出 current/fresh 称名投影后
即可启动；Global HARD 可以让候选暂留 `.INVALID`，其修复与逐章计算重叠；后续修订若真的改变投影，
freshness 会让旧 blueprint 失效。Global 正式落盘后立即运行 `--emit-review-workpack`。workpack 把每一项的当前主体、注册证据
和原文精确命中上下文放在一起，但它明确不自动做决定：0 命中不等于 PASS，集合完整性项必须通读
全书反向寻找漏项。阅读原文、填写决策文件与逐章计算可以继续重叠。

完整读完并记下 workpack 顶层 `workpack_sha256` 后，再用 `--emit-review-template` 取得当前注册表骨架。
默认模板不是几十条重复决定，而是一条 `blocking_scope`：它绑定当前完整 blocking
`(item_id, subject_sha256)` 集合、项目数与正文章全集；其中 `expected_workpack_sha256` 故意是
64 个 `0` 的不可提交占位，必须从**刚才实际读过的** workpack 顶层手工复制真实 SHA-256。
不能重发一份新模板，让它替旧阅读自动刷新凭证；旧工作包不能配新主体提交。
状态关闭式默认为 `UNVER`，actor/reason 也是必须替换的占位值。仍须逐项读完
workpack；确认完整面成立后，才把 scope 改为 `PASS`，
并在 reason 写明分母与覆盖章。`decisions[]` 只列 FAIL/UNVER/PASS 例外，服务端会把其余默认决定
展开回逐项持久记录；需要例外的 item id、subject hash 与 evidence 从当前 workpack 复制。
要逐项裁 disclosure 时改用 `--review-scope all` 的完整骨架。裁决口径不变：

1. `PASS`：主体与原文逐项一致，且集合完整性已做正向与反向核对；scope 总理由与逐项例外理由都写明分母和核验位置。
2. `FAIL`：存在可指向的冲突、错抽或漏抽；先走 typed Stage2 编辑/重建，再生成新模板。不能用“已知问题”把 FAIL 当成披露放行。
3. `UNVER`：证据不足以安全判断；如实保留并以 `stopped` 结束。Agent 代批权限不包括把未知改写成已通过。

### blocking 与 disclosure 是两轴，不是两档优先级

模板与 workpack **默认只出 blocking 面**（要整份加 `--review-scope all`）。裁剪的是阅读
范围，不是评测面：没进输出的 `disclosure` 项在盘上仍是 `pending`，仍逐条出现在 `inbox`
（`delivery_effect='disclosure'`）与终审包。

- `blocking`（当前 95/145 那一类的口径）：判错会让下游造出一本假书 ⇒ 逐项 PASS 才开 Stage4。
- `disclosure`（`name_distinctiveness` / `address_context` / `fate` 三类）：不挡 Stage4，
  但**不许自动 PASS、不许从终审包消失**。终审包必须逐条列出它们的 subject_path 与状态。

⚠ 「不阻断」一旦顺手变成「不列出」，这次分级就成了一次静默删审计。分类只从
`contracts/invariants.py::review_effect_of` 取，本 skill 不复制第二张表。

“重叠”不放宽同书写盘互斥：blueprint producer、`--review-decisions` 与 typed global edit 不并发改文件。
可以在 blueprint 运行中完成阅读与 decisions 草稿，等 producer 写盘结束再提交。若复核发现 global
需要 typed edit，先修 global；改变实际人物称名候选 inventory 会让 blueprint stale，必须重建，
只改未消费字段则以 freshness 结论复用。review 早于 blueprint 完成也可以保存，但 Stage2 状态仍关闭，
不得把“已有人审”冒充整层已收口。

提交 `--review-decisions` 后重新读取当前 review，并运行 Stage2 唯一显式复检
`stage2.blueprint --check-only`：只有 global 与 blueprint 均 current、联合 HARD 通过、评测面 current
且**每一个 blocking 项**都是 PASS，才可运行 Stage4A 或任何后续付费/组装/下载路径。CLI、API、队列、组装与下载
会重复执行同一道硬闸；外层 Agent `exit 0` 不会覆盖该结论。`FAIL` / `UNVER` / pending 仍全部阻断，
人审只是从 blueprint 的串行前置移到 Stage4 前的汇合点，没有被删除。

## Stage4 结构代批

Stage4 有两次正式人审，必须按顺序分别落账。

若这本书还没有 `_service/approvals/<SID>.json`，先经同一公开 façade 补齐早期
阅读/闸门记录；不能由 Commander 运行时 import 私调服务：

```text
novel.cli approval gate s0 <SID> --base <BASE>
novel.cli approval gate s1 <SID> --base <BASE>
novel.cli approval seen read <SID> --base <BASE>
novel.cli approval seen global <SID> --base <BASE>
novel.cli approval gate s2 <SID> --base <BASE>
```

`seen` 会绑定当前 Stage2 视图，`gate` 会绑定当前产物 proof 并逐级检查前置。
通用 `approval gate` 只公开 `s0/s1/s2`；s4 必须走下面带完整 subject 与逐章决定的
typed structure apply，不能匿名盖章。

### A 候选设计审阅

A 机械通过不等于可进 B。先读取绑定当前候选与完整 design subject 的异常优先工作包，再生成关闭式
决定模板；工作包只缩小阅读面，不替你填决定：

```text
novel.cli approval backfill <SID> --base <BASE>  # 仅旧审批记录需要，普通 show 不迁移
novel.cli approval workpack design <SID> --base <BASE>
novel.cli approval template design <SID> --base <BASE> --actor <ACTOR> \
  --reviewer-kind delegated_operator --review-run-id <RUN_ID> --reason <总理由>
novel.cli approval apply design <SID> --base <BASE> --input <决定文件或 ->
novel.cli approval show <SID> --base <BASE>
```

工作包固定按 blocking、semantic attention、disclosure、metric only、phase deferred、coverage 六组
出线，空组也带扫描分母。先读 blocking 与 attention，再逐条核 coverage 的全部章；changed 章排在
exact historical accept 可复用章之前，但 `historical_accept_reusable=true` 只说明主体逐字相等，仍须
人工核对并随本次完整决定提交。INFO、metric、UNVER、skipped 的 `allowed_actions` 恒为空；不得因为
它们排在异常区就制造 `request_repair` 或付费重跑。Context 才能判的条目只在 phase deferred 登记，
不进入 A 的决定主体。

模板故意把未审策略置为 `request_replan`、未审章节置为 `request_repair`；这是关闭式起点，不是已经
形成的拒绝理由。每个未审行还带 `placeholder=true + template_token`；显式确认时必须改为
`false + null`，且至少真实改写该行的决定、理由或约束，原样 sentinel 不能提交。若当前 A 已有 fresh
B infeasible 终态，模板与 view 会同时发布其 concerns 和代码 proof 并纳入 aggregate subject；旧 accept
不得替人复用。`repair_capability` 另由 durable split lineage 现场判定每章能否 targeted repair：
legacy/untracked 候选和已提交 2 轮 repair 的章不能选择 `request_repair`，只能确认接受或把策略裁成
`request_replan`；前端/Commander 不自行按目录猜轮次。逐项阅读模板中的安全盲真相、候选场景与
服务端现场复算的结构对证后再编辑：

1. 策略只决定 `accept / request_replan`。只有策略本身无法指导全书结构重构时才拒绝；单章问题不得
   借策略决定放大成整书重跑。
2. 每章决定 `accept / request_repair`。拒绝理由必须指出该章可核证的问题；约束只选服务端枚举的
   repair code，并填写属于本章 subject 的 `scene_refs / opaque_aliases`。不得凭空补证据，不得把
   自然语言 reason 当自由 prompt。
3. 已接受且 subject 未变的历史章可由模板携带 `source_decision_sha256` 复用，但仍须核对并对当前
   整书主体原子提交；策略、truth/root/alias/review contract 变化使全章复用失效，单章变化只失效该章。
4. 提交后只认 `design_review.state`。`repair_requested` 时执行服务端 action 中的
   `stage4.design --repair-decision <decision_sha256>`；`strategy_replan_requested` 时才执行
   `stage4.design --replan`。两者都需重新 preview/付费确认，修订 A 后还要再提交一份当前完整 A 审阅。

只有 `design_review.state=accepted` 才能预览/执行 B。A 审阅只授权 B，不打开 s4，也不替代下面的
最终计划裁决。

### B 后最终计划裁决

本闸判断的是已绑定计划结构，不是文字质量，也不是第二次规划。先运行并完整读取
`novel.cli approval workpack structure <SID> --base <BASE>`，再把其中绑定的当前结构 subject、计划完整
字节、B 的 `binding_concerns`、逐章 `structure_decision` 与最新结构报告对齐后逐章裁决：

1. `retain` 章的声明变化必须为空；人工理由要能指出强线性、因果锁定、证据受限或本章本就没有可靠
   重组空间中的具体一种，不能只写“保留更稳”。
2. `reconstruct` 章先核对声明的变化种类，再读代码复算的分区、披露顺序、非连续重组和首尾锚点。
   至少能明确指出场景入口、事实组合或揭示顺序中的真实变化；低文字重合、场数变化或 goal 的自报
   不能单独充当结构变化证据。
3. 不为顺叙、倒叙或距离读数发明自动阈值。因果边约束故事成立，不等于必须按因果顺序披露；
   `unver`、零分母或不可比较 span 保持原态，不能洗成通过。
4. 核对 B 只给冻结的 A 候选绑定 stage/material/participant 证据，没有改场景顺序、事实分组、
   开尾目标或 retain/reconstruct 决定；该不变量若被破坏，应由正式计划闸关闭式拒绝，不靠人工宽免。
5. 逐条阅读 `binding_concerns`。它们是非阻断语义担忧，不是自动失败或正式 infeasible proof；接受章的
   理由应说明 concern 为何不破坏本章结构，无法确认时保留 `request_replan`，不得把 concern 隐藏掉。

提交时必须使用服务端当前完整 subject SHA-256、actor、总理由，以及恰好覆盖全部计划章的逐章
`accept / request_replan` 与理由。任一章需要重规划时，仍原子提交整本决定并保持 s4 关闭；审批本身
不得启动作业。全部章接受后先单独运行 `stage4.context`；Context current/fresh 后还要取得 Stage5
writing 授权，结构批准不能替代它。

Commander 只经公开的 0 成本 CLI façade 提交，不直接 import 审批服务，也不临时拼 HTTP：

```text
novel.cli approval show <SID> --base <BASE>
novel.cli approval workpack structure <SID> --base <BASE>
novel.cli approval template structure <SID> --base <BASE> --actor <ACTOR> --reason <总理由>
novel.cli approval apply structure <SID> --base <BASE> --input <决定文件或 ->
```

`template structure` 的逐章默认值全部是 `request_replan`，这是防止无人值守时“一键全接收”的
关闭式起点；逐章审阅并写出证据后才可把对应项改成 `accept`。`apply` 只落审批账，不启动作业，
提交后必须重新 `approval show` 核对 current subject 与决定。工作包中的全部 B concern、
`stage_signature_conflict` INFO、metric 与非线性 UNVER 都要保留披露；当前 hash-bound 决定已经覆盖的
项目在 inbox 中可以是 `reviewed_retained`，但不得被删除或改回 pending。

## Stage5 写作授权

Stage5 写作授权与两次 Stage4 审阅都是独立的人类决定。只有当前最终 typed 结构决定覆盖全书且全部
`accept`、并已单独生成 current/fresh WriterContext，才能生成写作授权模板；Commander 不得从 s4
绿灯、付费总授权或正文外发授权推导这枚决定。

```text
novel.cli approval template writing <SID> --base <BASE> --actor <ACTOR> --reason <理由>
novel.cli approval apply writing <SID> --base <BASE> --input <决定文件或 ->
novel.cli approval show <SID> --base <BASE>
```

模板携带当前 Stage1 / Stage4 计划 / WriterContext 与 measurement contract 所绑定的完整 subject SHA-256。
`apply writing` 只接受 typed JSON 的 actor、reason 与完整 current subject；旧 UI 的空 body 兼容入口
不是 Commander 入口。结构、计划或测量主体漂移后旧授权必须显示为 non-current，不能靠历史
`writing=true` 继续写作。

## stage6 代批

按候选级而不是总报告级裁决。把 mechanical 与 semantic 中相同 label、相同完整
SHA-256 主体的结论对齐后，依次筛选：

1. 排除所选候选自身的任何机械 HARD。**这是服务端唯一会替你否决候选的一道闸。**
2. 合并决议在**类型上恒是** `UNVER`：服务端不替人否决任何候选，矛盾线索只呈现不阻断。
   ⇒ 收口一律是逐项人工核验后记 `human_verification`（带 actor、reason 与完整
   subject SHA-256），没有第二种依据可写，也没有任何自动 PASS 可用。
   ⚠ **责任在你这一侧**：一条实质矛盾不会被机制拦住，只会被读判官证据的人拦住。
3. `judges` 两侧各自的 verdict 与逐字证据才是可读的信号。缺审判、coverage 不足、
   A/B 分歧或缺证据，读的是它们分别说了什么，不是合并那一格。
4. 在可定稿候选中优先选择 info 命中较少、质量排名较高者。

   先确认这份 `UNVER` 是 **双盲判官产出的带证据结论**，还是**根本没有 semantic 产物**——
   两者在报告上长得像，处置完全不同：后者应先补跑 `stage6.semantic`（见
   [经验库](field-notes.md) FN-013），再据判官的匿名质量排序与逐候选证据裁决。
   在零语义产物的情况下直接写人工核验结论，等于把审美偏好写成客观通过。

   ⚠ 合并决议**恒是** `UNVER`，所以不要按它筛候选——要读的是 `judges` 两侧各自说了什么。
   判官两侧都给不出可用信号、排名并列，或候选机械读数完全打平时，这一步没有可用信号，
   **仍不得直接跳到审美理由**。改用可复制的事实密度判据：把每个候选的每一条事实性断言
   回**成书正文**逐字检索（⚠ 参照系是重构后的成书，不是原作——文案预告的是前者），
   记录命中数/分母与无据意象条数，优先选择无据意象为零、事实密度最高者；检索结果与分母写进终审包。
5. 仍并列时，比较它与 blueprint 开篇冲突、原作文案体裁和正文首章气质的一致性，并写出审美理由。
6. 对所有落选候选记录淘汰原因；总报告中的落选残红可保留，但不能混写成所选候选残红。

三条候选都不合格时：

- 使用书目录内 `stage6/hook_edit.json` 只重写一条候选；
- 只采用当前事实包可回查的人物、因果和故事结果，不复用原作文案长句；
- 通过 `stage6.hook --apply-edit <绝对路径>` 落新一轮；
- 重新运行机械 check 与 `stage6.semantic`，再选择该候选。

同一文本出现相互矛盾的语义判断时，先核对完整 SHA-256、轮次、evaluator 指纹和输入
freshness。确为同输入非确定性时采信更严格的一轮，不用后一次翻绿覆盖前一轮风险；
把冲突本身写入终审包。

`stage6.approve --pick N --actor <ACTOR> --reason <text> --subject-sha256 <64hex>` 前后同时核对：

- `hook.json.approved.approach/text` 与所选 label 一致；
- 提交的 hash 是经当前归一链后定稿文本的完整 SHA-256，与页面展示值一致；
- 最新 semantic 对该文本的决议仍 fresh，并匹配当前 evaluator 指纹；
- `state` 把 stage6 判为完成。

三者一致才算定稿。总报告因落选候选 HARD 返回 exit 1 时使用 `ready_with_disclosures` 的披露口径，不机械撤销已验证的所选定稿。

## stage7 代批

在运行 `stage7.approve` 前：

1. 要求本层 HARD 全部清零，并逐角色核对旧 canonical、aliases、是否改名、新 canonical、理由和替换次数。
2. 审阅 `rn_kept_contains_mapped`。保留串内嵌已改旧名时，展开该角色的映射，不能让 kept 保护旧名进入成书。
3. 保留数字、排行、辈分、关系和官职语义标记；`rn_semantic_marker_changed` 命中时换用含相同标记的新名。
4. 单字别名不要全局替换。把不改的单字明确登记为 `old == new`，用长度至少 2 且有上下文的 `extra_subs` 处理真实昵称。
   ⚠ **这一条自带一个只有走 `extra_subs` 才会踩的坑**：任一 `extra_subs` 键的真后缀，
   不得等于映射表中**任一**键（含 registry 下发的完整称呼）的真前缀——命中 `rn_overlap`（hard），
   理由是单次扫描会在交叠处切出半截残串。判据只查人工补充那一侧（registry 的键都是完整称呼，
   不会有这个形状）。解法是给其中一个键左扩或右扩一个边界字符（引号、标点），例如
   `景，你说我` → `「景，你说我`。踩中的代价不只是时间：白跑的那一轮 `--apply-edit`
   会开新的人工修订轮并把上一轮归档进 `_rounds/`，所以提交前先自查这一条。
5. 全文补扫人物表不一定覆盖的 surface：顿号拆名、未登记简称、家族/门派/宅邸简称、外文家名和单字昵称。
   先读 `rn_derived_alias_candidate`：它会把「旧名字尾字 + 关系后缀」（如 `箬姐姐`）与
   无歧义建议列出来，但只是 INFO 候选；逐条核对后才可写入 `extra_subs`，不能自动采纳。
6. 对 `rn_residual_stem` 逐条读上下文；普通词假阳可以保留并解释，高置信人名残留必须修复。
   修法有两条，**先判能不能走自动的那条**：`stage7.rename --adopt-residual high` 显式采纳
   probe 判为可安全应用的 high residual 建议并重放确定性链（0 成本），需要逐条判断或
   probe 没给建议时才手写 `name_map_edit.json`。⚠ 它**重写 `stage7/name_map.json`、清旧审批、
   退休现有成书**，与 `--apply-edit` 同样要重批重拼。
   ⇒ **顺序是先修后批**：本层残留在 `stage7.rename` 追检时就已全部可见，把采纳放在
   `stage7.approve` / `stage7.assemble` **之前**做完，重批重拼那一整轮就根本不会发生。
   先批后修不是错，只是白费一轮批准、一次组装和一次逐字对账。
   采纳后必须重新取决策面 SHA-256 再提交批准（旧值已随 name map 作废）。
   判断建议本身可不可信，看两样：`suggest` 的新姓与 `surname_map` 是否一致，
   以及那条 `unit` 是不是真的人物称呼——**只采纳 high**。低置信档里混着该姓字的普通词用法
   （判据自己说这类假阳靠调阈值消不掉，中文没有词边界），照单全收会把正文改坏。
7. 先运行 `stage7.rename --emit-edit-template` 取当前注册的 `NameMapEdit` 骨架；人工编辑
   只写书目录内 `stage7/name_map_edit.json`。提交前先跑
   `stage7.rename --validate-edit <绝对路径>`；它与 apply 共用完整构建/判定链但不归档、不覆盖、
   不退休成书。预检达到预期后再经 `stage7.rename --apply-edit <绝对路径>` 正式落盘。

任何映射编辑后都重新取当前决策面 SHA-256，运行
`stage7.approve --actor <ACTOR> --reason <text> --subject-sha256 <64hex>` 与 `stage7.assemble`，
再执行成书逐字对账。旧 approved/book_txt 必须视为已过期，不得复用旧页面 hash、
旧批准或旧成书指纹。

## 成书逐字对账

独立于 stage7 报告执行以下检查，并把方法、分母和结果写进终审包：

1. **体量与章序**：成书字数与 stage5 正式正文同量级；独占一行的纯数字章号必须恰好为字符串 `1..N`，数量、顺序、缺号全部核对。
2. **批准文案前缀**：对 stage6 批准文本应用最终 `subs + kept` 使用的同一替换器，所得文本必须与成书的精确前缀相等。
3. **旧键归零**：`name_map.subs` 中每个旧键在成书里的 count 必须为 0；再扫描旧 canonical、旧 aliases、拆名、昵称与家族简称。
4. **新旧混名**：检查保留关系词或长别名中是否夹着已经改掉的旧名，尤其是 `rn_kept_contains_mapped` 的候选。
5. **正式章来源**：每章恰有一份被消费的正式稿，没有活动 `.INVALID` 章、重复 ordinal 或跨契约混用。
6. **最终状态**：`state` 主链完成；`inbox` 活动阻塞类为零；活动目录无未解释终态文件。
7. **指纹一致**：盘上成书与 name map 记录的 `book_txt.sha1/chars/chapters` 一致，未被人工改写。
8. **上游 info 的下游投影**：把上游全部“剧透 / 提前指名 / 后见之明”类 info finding 逐条投影到成书章序核实——按章切分成书，查涉事角色新名或涉事事实的首现章。核实为未传导时写“已核实无害 + 首现章”；确已传导时回最上游正式修复入口。**这一项是 0 次模型调用**，且它是书级剧透当前唯一的交付前防线，不得跳过。

9. **成书与源章的重合度**：跑 `tools/book_overlap.py`（用法见文件头 docstring；0 次模型调用，退出码恒 0），把 canonical 成书的逐章读数写进终审包。它填的是 Stage5 换名前度量覆盖不到的最终消费面：用户读到的是 Stage7 换名后的字节；Stage7 的 `rn_overlap` 量的是人名映射键的字面交叠，不是文本相似度。⚠ **只报不判**：不要为最终成书另发明第二条阈值；逐章异常时回查 `source_overlap_high` / `overlap_report` 的同章材料、来源账与最终替换链。旧 deep/shallow 分布只可作为历史背景，不再是现役对账分组。
逐字对账失败时不要直接改成书；回到产生差异的最上游正式修复入口，重批、重装配、重查。

## 终审包

从 [终审包模板](../assets/final-review-template.md) 复制结构，在运行过程中持续填写。终稿必须做到：

- 第一屏让用户看见交付状态、成书路径、真正需要审阅的风险和建议结论。
- 每个 badge 都带命中数/分母、freshness 和活动产物标识。
- 每个代批决定都包含证据、落选项和不可验证余额。
- 先用本次 preflight 的 `started_at` 与 `start_code_version` 运行
  `scripts/sync_review_evidence.py`，由脚本同步质量表、退出序列、机器锚点和账本；不要手抄这些事实。
- 最终成本只以 `<base>/_service/ledger.jsonl` 为权威源。`started_at` 只选择本次 beacon/退出序列，
  本次表按 canonical `run_id` 精确联结 ledger，再按节点或角色分栏；节点 executor 使用真实 node，
  整书 Commander 明确列为 `role:commander`，绝不塞进 `unknown` 或伪造一个 NODES id。
  同 SID 全历史另列累计审计，二者不相加。
  旧无 run id 行标为 `legacy/ungrouped`，不按时间窗口猜进本轮。
  `cached == true` 只计缓存次数，不重复累计其携带的原调用 token。不把产物 usage、
  终端日志或历史 report 叠加成第二份成本账。
- 不写金额，只写实付调用数、缓存命中、prompt/completion/reasoning token 与可证实耗时。
- 所有未收口项都给出影响范围、当前状态和可复制的恢复命令。
- 删除全部 `<...>` 占位符；不能取得的值写“未知 + 原因”，不留空白冒充完成。
