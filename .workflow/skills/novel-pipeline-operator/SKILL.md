---
name: novel-pipeline-operator
description: 作为受托终审代理，使用本仓 novel.cli 将单本中文小说从原始 txt 或既有 sid 可靠地推进到 stage7 按书名命名的 rewriting.txt 成书，并把中间人工闸门、修复裁决、校验读数、授权边界和待审余额汇总为该书目录下的书名_review.md。用于跑书、断点续跑、流水线修复、agent 代批以及“用户只审最终成稿”的任务；调用本 skill 跑书即默认授予付费授权与正文外发授权（只有用户明确收回才停在首个外部调用前），生成产物只能通过正式 CLI 节点和修复入口改变。
---

# Novel Pipeline Operator

把用户界面收敛为两份最终交付：成书与终审包。自行处理流水线内可委托的人工闸门，保留每次裁决的证据，不把中间正常审批重新抛给用户。

## 接口契约

接受以下输入；从仓库与盘上状态能确定的值自行解析，不重复询问。

| 输入 | 要求 |
|---|---|
| 书 | 原始 `.txt` 路径，或可在 `base` 内解析的既有 `sid` |
| `base` | **含各书目录的那一层**，默认仓根 `outputs`；新跑与续跑都用同一个根，隔离靠每本书自己的 `<平台_书名_sid>/` 目录，不靠另建输出根；始终传绝对路径 |
| 付费授权（`authorized`） | 只控制是否可产生外部调用费用。**交互式会话里默认 `granted`**——用户让你用本 skill 跑书，这一项就已经在了，不要在开跑前索要它 |
| 正文外发授权（`egress_authorized`） | 独立控制小说正文及派生上下文能否发送给外部模型。**交互式会话里同样默认 `granted`**，同样不索要 |
| 可选约束 | 用户指定的模型实验、定向章节范围、停止条件或审阅偏好 |

把“使用本 skill 端到端跑书”视为一次**完整**委托：`stage0.approve`、Stage2 逐项复核、Stage4A 的全书策略 + 逐章 typed 设计审阅、Stage4B 编译后的最终逐章 typed 结构裁决、独立 Stage5 writing 授权、最终 Review 后的 Stage5 typed 闭合回执、`stage6.approve`、`stage7.approve` 的代批，加上两项授权。**这些事都不要回头问用户**——本 skill 的产品承诺是“用户只审最终成稿”。Stage2 代批仍必须逐项写入 typed PASS/FAIL/UNVER；“agent 看过”不能替代文件里的决定，更不能让 FAIL/UNVER 越过硬闸。两次 Stage4 审阅、Stage5 写作授权与写后闭合回执的主体及作用都不同，任何一次也不能由另一张票、机械绿灯或付费授权代替。用户在本次对话里明确收回某一项时遵从用户范围，并在终审包写明被收回的是哪一项。

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
2. 首个动作前从仓根运行 `.workflow/skills/novel-pipeline-operator/scripts/session_preflight.py --base <BASE> --sid <SID>`；新书改用 `--source <TXT>`。把输出的 `started_at` 固化为 `<STARTED_AT>`、`code_version` 固化为 `<START_CODE>`，后续命令全程逐字复用，不能在收尾时现取一个新值冒充起点。预检后立即运行 `.workflow/skills/novel-pipeline-operator/scripts/operator_phase.py session-start --base <BASE> --sid <SID> --start-code-version <START_CODE> --started-at <STARTED_AT> --source skill`，把返回的 `session_id` 固化为 `<OPERATOR_SESSION>`。若这份可观测性账写不成，记录并披露遥测缺口，但不据此跳过、代替或放宽任何产品闸门。
3. 新书在 sid 尚未落盘前只预览并执行现有 `stage0.remove --source <TXT>`；取得 sid 后，与既有书一样，**先**运行 `<PY> -X utf8 -u -m novel.cli operator next <SID> --base <BASE> --json`。它以一次冻结 snapshot 返回 `state/overview/inbox/action`；只消费返回的 `action.node`、`action.flags`、`why`、`waiting_on`、`paid`、`writes` 与 `action_sha256`，不从 Stage 表、目录存在性或聊天记忆自行推演下一节点。`state=ready` 时把原样 node/flags 交给 `operator permit`；`state=blocked` 时只尝试当前 `waiting_on` 对应、由生产策略登记的审批或恢复，涉及 node 的动作同样先取 permit；只有 `state=pipeline_complete` 时 `action=null`，进入收尾。blocked action 的 hash 只描述 blocker，绝不是恢复执行权。
4. 在首个写盘或付费动作前创建终审包草稿，随每次授权判断、闸门裁决、修复与异常更新；不要等到最后凭记忆补写。
5. 对 exact node/flags，先从仓根运行 `<PY> -X utf8 -u -m novel.cli operator permit <SID> <node> --base <BASE> --json -- [flags]`，再用返回的 `permit_sha256` 调用 `.workflow/skills/novel-pipeline-operator/scripts/run_step.py --base <BASE> --sid <SID> --expect-code-version <START_CODE> --operator-permit-sha256 <PERMIT_SHA256> <node> -- [flags]` 预览生产 `build_steps`；真跑采用同一条命令并把 `--execute` 放在 node 前。permit 是纯读且绑定当前代码、surface、next 状态、exact flags、贵贱/写盘效应以及已登记外部输入原始字节；runner 在 preview 与 execute 都重新铸造并逐字核 hash，任何漂移都拒绝旧命令。需要机器复核 preview 时加 `--evidence-json <PATH>`：它原子记录完整 permit 与同一份 Step 的 argv/付费/写盘信息，preview 与 execute 复用同一路径时内容不同会拒绝覆盖。execute 无论是否传该路径，都会把本次真实 Step canonical evidence 的完整 SHA-256 同时写进 start/end 信标 note。headless Commander 下，runner 还会重读 started contract、取得覆盖整个 `build_steps` 的 active claim，并给 root 与自动 `--check-only` 子步骤注入同一 permit envelope 及各自 `step_index`；root 在首个副作用前重铸 current permit，root 落盘后的 follow-check 只核 envelope、claim 与 canonical index/argv，不拿预期已变化的 surface 误杀追检。已有 sid 的**所有 node actions**（包括纯读诊断与 blocked recovery）都必须走这条 runner+permit 主路。新书摄入的 `stage0.remove --source` 在 sid 落盘前省略 runner 的 `--sid` 与 permit。不要凭文档手拼真实 argv，也不要手加运行期 agent profile 参数；Agent driver 与 legacy 的 probe、恢复和禁止降级边界只读[执行模式](references/execution-modes.md)。Stage5 全书写作节点不带选臂参数；定向维护只用 `--chapters`。
6. 只有不属于 node run 的纯读 façade（`state` / `inbox` / `operator` / `preview`）、typed approval façade 与终态明确点名的 `response-repair` 可不经 runner；在 Commander 下，后两类写入口会从 launcher 提供的父 run 契约自行取得并释放 generic admission claim，不能变成孤儿写手。它们也在动作前确认当前代码等于 `<START_CODE>`。node 的 start/end 信标只由 runner 在原 active claim 内生成，不手工补 `mark`；非 node 动作的执行轨迹使用第 10 步的 `operator_phase.py`，`preview` 不打点。
7. 一次只运行一个写盘节点。用退出码判断进程结果，用最新 check badge 与 finding 判断质量结果。
8. 每次节点、typed approval、人工修订或恢复动作结束后，立即重新调用 `operator next --json`；下一次 node 动作再重新取得 exact `operator permit`，不得沿用动作前的 next、action hash 或 permit。命中闸门时按代批契约裁决；Stage4A/B 两次裁决都先完整读取当前 hash-bound 的
   `approval workpack design|structure`。Stage6/7 定稿同样先分别读取 `approval workpack stage6|stage7`：
   Stage6 包内完整展开 A/B 各自 verdict、reason、evidence、ranking；Stage7 包内完整汇总人物、别名、
   kept、实际替换命中、残留上下文、替换计数和完整主体。若包的 `disposition=already_current`，当前
   typed 决定已精确绑定相同代码、规则与产物主体，跳过重复提交；否则只把**刚读完的同一份包**里的
   `subject_sha256` 与 `workpack_sha256` 一起提交给对应 approve。工作包只缩小阅读范围，不自动 accept，
   INFO/metric/UNVER 不制造付费动作。Stage5 最终 Review 与所有 edit/trim/wontfix 收口后，逐项读取
   current closure workpack，再提交 `approval template/apply stage5` 的 typed 回执；不得代签或拿旧 s5
   继续下游。命中失败时按修复状态机收敛；每次决定同时记录证据、被否决方案和复验结果。
9. `stage7.assemble` 后执行独立成书对账，再调用一次 `operator next --json` 并用组合 `state --with-inbox` 刷新活动终态。先补完终审包中的人工叙述并清除其余占位符，再从仓根运行 `.workflow/skills/novel-pipeline-operator/scripts/finalize_review.py --base <BASE> --sid <SID> --started-at <STARTED_AT> --start-code-version <START_CODE>`。这是 canonical 收尾：只 collect 一次，在内存中 render → validate，验证通过才原子提交终审包；提交后丢弃旧快照，并以 `<START_CODE>` 调用全新读作用域的 `delivery.assess`。旧 `collect_final_evidence.py`、`sync_review_evidence.py`、`validate_review.py` 与 `verify_delivery.py` 保留为诊断/兼容入口，不再串成主路。
10. 用 `operator_phase.py` 对 `inspect / preview / execute / review / approve / finalize` 的实际窗口逐段记录：每段先 `phase-start`，固化返回的 `phase_execution_id`，结束后用 `phase-end` 记录真实 `completed / failed / cancelled / unknown`、退出码与不含正文的错误类别；不得记录 prompt、正文、reason、argv、路径或密钥。`finalize_review.py` 是一条进程内含 fresh assess 的命令，外部只记一个 `finalize` 窗口；它返回的 `phases.delivery` 证明逻辑终验独立，但不要事后把一个墙钟拆成伪 `verify`。只有另行执行诊断 `verify_delivery.py` 或 launcher 能在调用前后真实打点时才用 `verify`。本 skill 的 preflight 发生在 telemetry session 建立之前，保留为起点后的 unattributed 墙钟，不事后补一条近零时长的伪 `preflight` phase；能在预检前启动遥测的 launcher 才使用那一格。最后 `session-end` 并运行 `summary --base <BASE> --sid <SID> --started-at <STARTED_AT> --session-id <OPERATOR_SESSION>`。summary 只拆墙钟归属；Commander 宿主模型 token 与成本不可见时必须保持 `unavailable_host_boundary`，不得估算。只有 finalizer 的 fresh delivery 结果为 `ready*` 才可交付，然后只把成书与终审包交给用户。

## 不可突破的边界

- 不修改仓库代码、历史证据目录、LLM 缓存、账本或 `_rounds/` 来“修好”某本书。
- 不直接编辑正式生成产物或成书。改动一律经终审包与 [修复状态机](references/recovery.md) 明列的**受托编辑面**——那张表上的入口（含整章正文、开篇文案与 `_llm_rejected` 中终态点名的 `candidate.json`）就是留给本 skill 用的，不是留给用户的；让 CLI 完成校验、归档和正式落盘。
- **本次会话内**不并行运行两本书，也不在同一本书上并行两个写盘节点。会串的是证据、闸门裁决与终审包，它们都长在单个会话里。⚠ 这**不是**「系统不支持多书并行」：跨书并行是产品明确要求的能力，做法是每本书一个 agent 会话（批量入口与队列 lane 各自代跑一本），互斥只落在各自 `sid`。
- 会话开始后不换代码版本；任何写盘/付费步骤前后都必须仍等于 `<START_CODE>`。临时编辑只有恢复到原指纹才能续跑；稳定落到新指纹时以 `stopped` 结束本会话，重新预检后才是另一条 run。
- 不覆盖 launcher started 事件冻结的 commander / executor 模型、思考强度、execution mode 或逐节点 override；直接交互式 legacy 路也不改项目默认，除非用户明确要求一次独立实验。
- 不把 `unver`、`skipped`、分母为零或落选候选的状态改写成 `ok`。绿灯必须带分母和 freshness。
- 不估算或换算金额。只报告账本中的调用次数、缓存命中、token 与耗时事实。
- 两项授权默认都在，但收回是单向生效的：用户明确收回其中一项时，不因另一项仍在而推定它。被收回的那一项落在下一步路径上时，仍运行零成本的 `state`、`inbox` 与 `preview` 以形成恢复证据，但不得执行 preview 中的首个外部模型步骤。⚠ **「没有人特意重申授权」不是缺失**，别拿它当停机理由。
- 不为追求“全绿”改写无关内容。只处理阻塞所选定稿、成书完整性或用户终审判断的事项。
- **只有 HARD 或当前 hash-bound 的 Stage4A typed `request_repair / request_replan` 能触发付费修订。**
  后者必须来自真实逐章阅读并带关闭式约束，不得把默认拒绝模板原样提交来制造付费理由。
  `info`、`metric` 与 `unver` 本身一律只披露：`unver` 是判据
  诚实地说自己这次够不着（现役例子是 Stage4 `state_closure` 的章内非线性边界），
  看到它去重跑 `stage4.design` 就是拿一次整书付费换一份本来就在如实说话的报告。
  判据表的 `sev` 是唯一权威，见[修复状态机](references/recovery.md)。

## 完成定义

只有同时满足以下条件才可标记 `ready`：

- `state` 显示主链完成且所有消费中的正式产物 fresh/match；已解释的诊断噪声单列披露。
- Stage2 global 与 blueprint 均 current、唯一联合 check 无 HARD，且全局复核面 current、
  **blocking 面逐项 PASS**；这是 `ready` 与 `ready_with_disclosures` 的共同硬前提。
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
