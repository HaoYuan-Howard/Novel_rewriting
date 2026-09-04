---
name: novel-pipeline-operator
description: 作为受托终审代理，使用本仓 novel.cli 将单本中文小说从原始 txt 或既有 sid 可靠地推进到 stage7 按书名命名的 rewriting.txt 成书，并把中间人工闸门、修复裁决、校验读数、授权边界和待审余额汇总为该书目录下的书名_review.md。用于跑书、断点续跑、流水线修复、agent 代批以及“用户只审最终成稿”的任务；调用本 skill 跑书即默认授予付费授权与正文外发授权（只有用户明确收回才停在首个外部调用前），生成产物只能通过正式 CLI 节点和修复入口改变。
---

# Novel Pipeline Operator

把用户界面收敛为两份最终交付：成书与终审包。自行处理流水线内可委托的人工闸门，保留每次裁决的证据，不把中间正常审批重新抛给用户。

正式工作流只有两个模型角色：Executor 生成或修复指定逻辑单元，Commander/Reviewer 阅读不可变
workpack 并提交 typed 决定。RunSupervisor 是现有 orchestrator 内的**确定性状态机**，只负责锁、
admission、execution permit、节点调度、epoch、自动遥测与最终报告；它不调用模型、不参与语义判断，
也不是第三个模型角色。正式运行没有“外层 root”；交互式 root 只用于开发调试、安全暂停和修复代码，
不能在正常跑书时二次代批或套一层 Commander。

## 接口契约

接受以下输入；从仓库与盘上状态能确定的值自行解析，不重复询问。

| 输入 | 要求 |
|---|---|
| 书 | 原始 `.txt` 路径，或可在 `base` 内解析的既有 `sid` |
| `base` | **含各书目录的那一层**，默认仓根 `outputs`；新跑与续跑都用同一个根，隔离靠每本书自己的 `<平台_书名_sid>/` 目录，不靠另建输出根；始终传绝对路径 |
| 付费授权（`authorized`） | 只控制是否可产生外部调用费用。**交互式会话里默认 `granted`**——用户让你用本 skill 跑书，这一项就已经在了，不要在开跑前索要它 |
| 正文外发授权（`egress_authorized`） | 独立控制小说正文及派生上下文能否发送给外部模型。**交互式会话里同样默认 `granted`**，同样不索要 |
| 可选约束 | 用户指定的模型实验、定向章节范围、停止条件或审阅偏好 |

把“使用本 skill 端到端跑书”视为一次**完整**委托：`stage0.approve`、Stage2 逐项复核、Stage4A 的全书策略 + 逐章 typed 设计审阅、Stage4B 编译后的最终逐章 typed 结构裁决、独立 Stage5 writing 授权、最终 Review 后的 Stage5 typed 闭合回执、`stage6.approve`、`stage7.approve` 的代批，加上两项授权。**这些事都不要回头问用户**——本 skill 的产品承诺是“用户只审最终成稿”。Stage2 代批仍必须逐项写入 typed PASS/FAIL/UNVER；“agent 看过”不能替代文件里的决定。FAIL 必须经 GlobalEdit 改真实事实；UNVER 仍不得改名为 PASS，只有生产选项明确给出 `quarantine_group` 的完整可删组能从写作视图隔离并以 disclosure 继续。两次 Stage4 审阅、Stage5 写作授权与写后闭合回执的主体及作用都不同，任何一次也不能由另一张票、机械绿灯或付费授权代替。用户在本次对话里明确收回某一项时遵从用户范围，并在终审包写明被收回的是哪一项。

两项授权仍**分别取值**，只是默认值都为真：收回其中一项时，不因另一项仍在而推定它。headless 是唯一的例外，取值以 launcher prompt 原文为准（见[运行宿主](references/runtime-profiles.md)）。

代批与人工修订一律以 `<ACTOR>` = `agent:novel-pipeline-operator` 自报身份。生产只要求这个值非空，形态由本 skill 约定且只在这里约定一次：带前缀才能在账本与审计里一次捞全代批记录，裸串会和人名撞。

只产生以下用户交付接口：

- `<novel-dir>/stage7/<书名>_rewriting.txt`：最终成书，只能由 `stage7.assemble` 生成。书名就是书目录名去掉平台前缀与 `_<sid>` 尾巴的那一段；`stage7.assemble` 的成功行会打印它的绝对路径，以那条为准。
- `<novel-dir>/<书名>_review.md`：终审包，按 [终审包模板](assets/final-review-template.md) 填写。书名就是书目录名去掉平台前缀与 `_<sid>` 尾巴的那一段；落在**书目录根**，不在任何 `stageN/` 下。⚠ 不必在文件名里再带 sid——书目录名已经带了。启动 prompt 会直接给出这条绝对路径，以它为准。

使用三种终态，禁止用“命令跑完”冒充“书已完成”：

- `ready`：成书与全部完成条件满足，可直接交用户终审。
- `ready_with_disclosures`：成书可交付，但仍有不影响所选定稿的残红、不可验证项或已知实现缺口；逐项披露。
- `stopped`：安全地停在可恢复位置；终审包必须给出阻塞证据与精确恢复命令。

## 按需加载资源

开始任何跑书或续跑前，完整读取两份共享契约：

- [工作流契约](references/workflow.md)：现行命令协议、主节点顺序、成败判定与断点续跑。
- [代批与终审契约](references/delegated-review.md)：标准人工闸门的判据、成书对账与终审包要求。

然后独立确定两根轴，各只读取一个匹配小节，不加载另一模式的细节：

- [运行宿主](references/runtime-profiles.md)：交互式主会话、headless Commander、只读观察三选一；
  它只决定授权来源、进程等待与停止方式。
- [执行模式](references/execution-modes.md)：Agent driver 或 legacy LLM transport 二选一；
  它只决定付费生成节点由谁承接。headless 的选择来自 started contract，不能临场改成 legacy。

遇到非 happy path 时再读取：

- [修复状态机](references/recovery.md)：退出码、`remediation`、允许写入面与停止条件。
- [经验库](references/field-notes.md)：先用节点名、rule id、退出码或报错片段检索，只读取命中的条目；经验不能覆盖当前 finding 与代码契约。

仅在维护本 skill 时读取 [维护协议](references/maintenance.md)，并从仓根运行
`.workflow/skills/novel-pipeline-operator/scripts/validate_skill.py`。不要在普通跑书任务中加载维护说明。

## 执行主循环

1. 确认仓根、Python 解释器、书、绝对 `base`，并分别确定当前运行宿主与执行模式；**记录**两项授权取值与代批范围——默认都是 `granted`，这一步是记录事实，不是向用户索要。不要打印密钥，也不要因某一种 executor 配置缺失阻断另一种已明确选择的模式。
2. 首个动作前从仓根运行 `.workflow/skills/novel-pipeline-operator/scripts/session_preflight.py --base <BASE> --sid <SID>`；新书改用 `--source <TXT>`。把输出的 `started_at` 固化为 `<STARTED_AT>`、`code_version` 固化为当前 `<START_CODE>`。headless 正式路由由 RunSupervisor、admission、beacon 与 ledger 自动留下 parent/epoch/node 的权威生命周期和联结；它们不伪造 inspect/review 等语义阶段的精确起止，缺少显式诊断账时该分解就是 `unknown`。Commander 不手工维护 phase/telemetry。直接交互式运行把这两格当成本轮 epoch 的坐标，代码漂移时停止旧 token，按[运行宿主](references/runtime-profiles.md)打开新 epoch，不能在收尾时现取一个新值冒充旧 epoch。
3. 新书在 sid 尚未落盘前只预览并执行现有 `stage0.remove --source <TXT>`；取得 sid 后，与既有书一样，**先**运行 `<PY> -X utf8 -u -m novel.cli operator next <SID> --base <BASE> --json`，把 stdout 原样保存为 scratch 内的 `<NEXT_FILE>`。它以一次冻结 snapshot 返回 `state/overview/inbox/action/execution_token`；只消费返回的状态与 action 路由字段，不拆、不手搬 token 内部 SHA，也不从 Stage 表、目录存在性或聊天记忆自行推演下一节点。`state=ready` 时完整 token 已在同一 JSON 内，ready action 不再第二次调用 `operator permit`；`state=blocked` 时 `<NEXT_FILE>` 只描述 blocker，只尝试当前 `waiting_on` 对应、由生产策略登记的审批或恢复，涉及 node 的恢复动作才显式铸 recovery permit。`recovery:stage4-design-action` 是已经存在 typed A 决定后的确定性恢复：headless RunSupervisor 直接铸 exact permit 并交 Executor，不启动 Commander；其余 blocker 只有命中 reviewer 注册表才唤 Commander。只有 `state=pipeline_complete` 时 `action=null` 且无 token，进入收尾。blocked action 的 hash 绝不是恢复执行权。
4. 审阅、授权、修复与异常都写进各自的 typed 决定、provenance、ledger 和 Supervisor 事件；运行中不创建、
   不逐动作改写 `<书名>_review.md`。节点链闭合后才按模板一次补齐必须的人读叙述，再由 finalizer 从
   权威账批量渲染机器事实，避免 Commander 维护第二份流水账。
5. 对 ready 的 exact node/flags，preview 与 execute 直接复用上一步原样 `<NEXT_FILE>`：`.workflow/skills/novel-pipeline-operator/scripts/run_step.py --base <BASE> --sid <SID> --expect-code-version <START_CODE> --operator-permit-file <NEXT_FILE> <node> -- [flags]`；真跑只在同一命令中把 `--execute` 放到 node 前。只有 blocked recovery 才从仓根运行 `<PY> -X utf8 -u -m novel.cli operator permit <SID> <node> --base <BASE> --json -- [flags]`，把完整恢复许可原样保存为 `<PERMIT_FILE>`，并用同一 runner 参数替换 `<NEXT_FILE>`。两类文件只能落操作系统临时目录或 `_service/scratch/<sid>/<当前 session>/`，execute 结束即删除；不得再把 `tmp_next/state/permit` 留在仓根或书目录。preview 只解析冻结 token 并渲染 `build_steps`，不重跑 planner；execute 在取得 claim 后、首个副作用前只做一次 active-surface CAS。任一代码、active artifact、审批 head、登记外部输入或 exact flags 漂移都拒绝旧 token，回到 `operator next` 重新铸造。SHA-only 参数只保留诊断兼容，不能作为默认主路。需要机器复核 preview 时加 `--evidence-json <PATH>`：它原子记录完整 permit 与同一份 Step 的 argv/付费/写盘信息；该证据也只落上述 scratch 并随 session TTL 清理。headless Commander 下，同一 envelope 覆盖 root 与自动 `--check-only` 子步骤并给每个 child 注入 canonical `step_index`；root 落盘后的 follow-check 只核 envelope、claim 与 canonical index/argv。已有 sid 的**所有 node actions**（包括纯读诊断与 blocked recovery）都必须走这条 runner+完整 token 主路。新书摄入的 `stage0.remove --source` 在 sid 落盘前省略 runner 的 `--sid` 与 permit。不要凭文档手拼真实 argv，也不要手加运行期 agent profile 参数；Agent driver 与 legacy 的 probe、恢复和禁止降级边界只读[执行模式](references/execution-modes.md)。Stage5 全书写作节点不带选臂参数；定向维护只用 `--chapters`。
6. 只有不属于 node run 的纯读 façade（`state` / `inbox` / `operator` / `preview`）、typed approval façade 与终态明确点名的 `response-repair` 可不经 runner；在 Commander 下，后两类写入口会从 launcher 提供的父 run 契约自行取得并释放 generic admission claim，不能变成孤儿写手。node 的 claim/beacon、进程结果、ledger 联结和异常由权威账自动记录；语义 phase 没有证据时保持 unknown，Commander 不手工补 `mark` 或 telemetry。
7. 一次只运行一个写盘节点。用退出码判断进程结果，用最新 check badge 与 finding 判断质量结果。
8. 每次节点、typed approval、人工修订或恢复动作结束后，立即重新调用 `operator next --json` 并原样保存新的 `<NEXT_FILE>`；下一次 ready node 直接使用其中的新 token，blocked recovery 才另取 exact permit，不得沿用动作前的 next、action hash 或 token。命中闸门时按代批契约裁决；Stage4A/B 两次裁决都先完整读取当前 hash-bound 的
   `approval workpack design|structure`。Stage6/7 定稿同样先分别读取 `approval workpack stage6|stage7`：
   Stage6 包内完整展开 A/B 各自 verdict、reason、evidence、ranking；Stage7 包内完整汇总人物、别名、
   kept、实际替换命中、残留上下文、替换计数和完整主体。若包的 `disposition=already_current`，当前
   review subject 与 review contract 未变且当前 validation seal 免费复检通过，raw typed 决定仍适用，跳过重复提交；代码变化本身不撤销人审。若 **Stage4 Design** 包为 `suspended`，同样跳过重复提交，但必须明确停在 legacy-frozen 缺 relation/preflight validation authority：它不授权 B，不取 recovery permit，也不执行 repair/replan/B；该态没有 `s4rr1`、`s4er1` 或 execution token。否则只把**刚读完的同一份包**里的
   opaque `review_token` 提交给对应 approve。工作包只缩小阅读范围，不自动 accept，
   INFO/metric/判据型 UNVER 不制造付费动作；Stage2 typed review 的 FAIL/UNVER 按下述专用
   GlobalEdit/隔离闭环处理。Stage5 最终 Review 与所有 edit/trim/wontfix 收口后，逐项读取
   current closure workpack，再提交 `approval template/apply stage5` 的 typed 回执；不得代签或拿旧 s5
   继续下游。命中失败时按修复状态机收敛；每次决定同时记录证据、被否决方案和复验结果。
9. `stage7.assemble` 后执行独立成书对账，再调用一次 `operator next --json` 并用组合 `state --with-inbox` 刷新活动终态。先补完终审包中的人工叙述并清除其余占位符，再从仓根运行 `.workflow/skills/novel-pipeline-operator/scripts/finalize_review.py --base <BASE> --sid <SID> --started-at <STARTED_AT> --start-code-version <START_CODE>`。这是 canonical 收尾：只 collect 一次，在内存中 render → validate，验证通过才原子提交终审包；提交后丢弃旧快照，并以 `<START_CODE>` 调用全新读作用域的 `delivery.assess`。旧 `collect_final_evidence.py`、`sync_review_evidence.py`、`validate_review.py` 与 `verify_delivery.py` 保留为诊断/兼容入口，不再串成主路。
10. RunSupervisor/lifecycle、admission、beacon 与 ledger 自动记录可证明的 parent/epoch/node/费用事实；
    它们不把事件间隔猜成 `inspect / preview / review / approve / finalize` 窗口。Commander 不调用
    `operator_phase.py`；该薄脚本只保留给开发者做可选诊断，不得成为交付闸，也不得事后补伪窗口。
    没有完整诊断账时 phase 墙钟分解保持 `unknown`。Commander 宿主模型 token 与成本不可见时保持
    `unavailable_host_boundary`，不得估算。只有 finalizer 的 fresh delivery 结果为 `ready*` 才可交付，
    然后只把成书与终审包交给用户。

## 不可突破的边界

- 不修改仓库代码、历史证据目录、LLM 缓存、账本或 `_rounds/` 来“修好”某本书。
- 不直接编辑正式生成产物或成书。改动一律经终审包与 [修复状态机](references/recovery.md) 明列的**受托编辑面**——那张表上的入口（含整章正文、开篇文案与 `_llm_rejected` 中终态点名的 `candidate.json`）就是留给本 skill 用的，不是留给用户的；让 CLI 完成校验、归档和正式落盘。
- **本次会话内**不并行运行两本书，也不在同一本书上并行两个写盘节点。会串的是证据、闸门裁决与终审包，它们都长在单个会话里。⚠ 这**不是**「系统不支持多书并行」：跨书并行是产品明确要求的能力，做法是每本书一个 agent 会话（批量入口与队列 lane 各自代跑一本），互斥只落在各自 `sid`。
- 一个 execution epoch 内不换代码版本；任何写盘/付费步骤前后都必须仍等于该 epoch 的 `<START_CODE>`。
  代码稳定变化时暂停接纳新节点，关闭旧 epoch，并在**同一 parent run** 内打开新 epoch、免费机械复检、
  创建新的 Commander provider session；不得把旧 provider context 或旧 execution token 带进新 epoch。
  内容与 review contract 未变的 raw 人工决定在复检通过后恢复有效，不因代码 SHA 单独变化要求重签。
- 不覆盖 launcher started 事件冻结的 commander / executor 模型、思考强度、execution mode 或逐节点 override；直接交互式 legacy 路也不改项目默认，除非用户明确要求一次独立实验。
- 不把 `unver`、`skipped`、分母为零或落选候选的状态改写成 `ok`。绿灯必须带分母和 freshness。
- 不估算或换算金额。只报告账本中的调用次数、缓存命中、token 与耗时事实。
- 两项授权默认都在，但收回是单向生效的：用户明确收回其中一项时，不因另一项仍在而推定它。被收回的那一项落在下一步路径上时，仍运行零成本的 `state`、`inbox` 与 `preview` 以形成恢复证据，但不得执行 preview 中的首个外部模型步骤。⚠ **「没有人特意重申授权」不是缺失**，别拿它当停机理由。
- 不为追求“全绿”改写无关内容。只处理阻塞所选定稿、成书完整性或用户终审判断的事项。
- **只有 HARD 或当前 hash-bound 的 Stage4A typed `request_repair / request_replan` 能触发付费修订。**
  后者必须来自真实逐章阅读并带关闭式约束，不得把默认拒绝模板原样提交来制造付费理由。
  `info`、`metric` 与判据严重度 `unver` 本身一律只披露：`unver` 是判据
  诚实地说自己这次够不着（现役例子是 Stage4 `state_closure` 的章内非线性边界），
  看到它去重跑 `stage4.design` 就是拿一次整书付费换一份本来就在如实说话的报告。
  Stage2 `GlobalReviewItem.state=UNVER` 是另一套 typed 人审状态：只有
  `generation_impact=quarantine_or_fix` 的完整 secret/relation/world-rule 组可显式 quarantine；
  `must_fix` 只有证据支持 PASS 或能指出真实错误时才用 0-wire GlobalEdit，材料仍不足就保持 UNVER
  并停止，不臆造编辑。GlobalEdit 字段 patch 只能改模板已有 subject；集合完整性的新增/删除/拆分
  必须填写 hash-bound `collection_edits[].operations`。Stage2 每个 Global/MainText 主体的预算是 1 次 Executor + 四席
  Commander（一席完整审阅、至多两席订正、一席只读独立终审）；每席都开新 logical/provider session，
  同席内因网络中断的 resume 不占新席；订正席仅发送未闭合或依赖重开的 subject cards、精确拒因与
  surviving decisions，终审席读全量 blocking 行、整书正文与 Blueprint 工作包，只能 confirm 或 reopen。
  0-wire typed 动作不计席位。若 reviewed edit 被
  canonical HARD 拦成当前 `.INVALID`，必须先走持久 `recovery:stage2-global-reviewed-edit`；预算槽在
  模型启动前落 `started`，不得先切 stale Blueprint，也不得因 pause/resume 或重启重置。订正席用尽仍未
  闭合时停止并保留具体恢复事实；blocking 面安全后只有第四席终审能收口 `gate.s2`。
  判据表的 `sev` 是唯一权威，见[修复状态机](references/recovery.md)。

## 完成定义

只有同时满足以下条件才可标记 `ready`：

- `state` 显示主链完成且所有消费中的正式产物 fresh/match；已解释的诊断噪声单列披露。
- Stage2 global 与 blueprint 均 current、唯一联合 check 无 HARD，且全局复核面 current、
  **blocking 面 generation-safe**：逐项 PASS，或仅余带 hash-bound `quarantine_group` 的
  secret/relation/world-rule UNVER。全 PASS 才可 `ready`；存在隔离项只能
  `ready_with_disclosures`，且写作只消费确定性净化视图。
  人审可与 blueprint 计算重叠，但不能缺席。`disclosure` 的三类不挡门，
  却必须在终审包里逐条披露——「不阻断」不等于「不列出」。
- `inbox.delivery_counts.blocking == 0`；`attention_counts` 区分“尚待处理”与“已裁决但记录保留”，不得拿总数冒充失败数。披露项逐类记录。
- `inbox.counts` 的八类余额必须逐类披露：`invalid`、`rejected`、`repairable`、`human_only`、`verification`、`upstream`、`stalled`、`approval`；类别总数不直接等于阻塞数，阻塞性只读 `delivery_effect`。
- 活动目录不存在未解释的 `.INVALID`、`.rejected` 或 `.stalled`；历史 `_rounds/` 不计入活动余额。
- 全部代批均有可追溯依据；Stage4A 当前设计审阅与 Stage4B 最终结构裁决均逐章接受；Stage5
  `closure.state=closed` 且 `gate.s5` 绑定当前 closure 决定；所选 stage6 候选和 stage7 人名表均通过各自定稿条件。
- 成书通过 [代批与终审契约](references/delegated-review.md) 的逐字对账，终审包中的统计与盘上事实一致。
- 终审包的退出序列按 `<STARTED_AT>` 切出本次会话，成本账再按这些 beacon 的 `run_id + node`
  精确联结；旧无 run id 行只作 legacy/ungrouped 累计审计，不能靠时间猜进本轮；起止代码指纹逐字一致。

任何一项未满足时只能使用 `ready_with_disclosures` 或 `stopped`，并说明用户审阅时真正需要关注什么。
