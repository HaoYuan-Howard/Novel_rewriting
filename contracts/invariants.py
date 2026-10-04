#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
invariants.py —— 跨层取值域的【唯一落点】。

【为什么必须只有一份】一个枚举 / 词表 / 区间同时出现在 prompt 文字和代码里时，
prompt 那一份必须是**插值来的**。手抄一份不会报错，它只是慢慢漂：
原型实测过两处——写法词表的 prompt 版写着「象征/强化感官」而真词表是「意象/感官」、
另 8 词一个没提；篇幅带的 prompt 版漏了整整一档。**模型按 A 算、校验按 B 判，谁也不会出声。**

【谁从这里取】
  · prompt —— 用 `string.Template` 插值（**缺值必须抛异常**；渲染成空串等于静默产出一个
    带窟窿的 prompt，与「显式终态」正相反）
  · 校验器 —— 直接 import
  · HTTP API 的 `/api/enums` —— 整份下发
  · 前端 —— 从 `/api/enums` 运行时取，**不许硬编码**

【本模块不许 import 任何业务模块】它只依赖同为叶子的 `contracts.artifacts` 路径目录。
"""
from __future__ import annotations

from typing import Final, Literal, NamedTuple

import contracts.artifacts as artifact_paths

# ─────────────────────────────────────────────────────────────────────────────
# 一、状态三态 / 严重度四级 —— 「不知道」与「没问题」必须可区分（需求 Q-2）
# ─────────────────────────────────────────────────────────────────────────────

#: 产物新鲜度。**`unknown` 不是 `fresh` 的一种**——它是「这份产物没有来源记录，无从判断」。
#: 画成同一个颜色就等于替人断言了一件没人验证过的事。
#: 实测代价：某次 23 章成稿与任何一版上游都配不上（匹配 1/23），配对关系永久丢失，而界面全绿。
FRESHNESS: Final = ('fresh', 'stale', 'unknown')


#: ArtifactState 给界面直接消费的四态。`missing` 不能再由前端拿 `!exists` 推导。
ARTIFACT_DISPLAY: Final = ('missing', 'fresh', 'stale', 'unknown')

#: 文件可用性与来源新鲜度是两轴；这一轴负责 `.INVALID` / 读不回 / 缺席。
ARTIFACT_CONDITION: Final = ('missing', 'ready', 'invalid', 'unreadable')

#: 一本书顶部那句服务端结论。顺序优先级由 `novel.orchestrator.state` 唯一计算。
OVERVIEW_VERDICT: Final = (
    'broken', 'legacy_incompatible', 'stale', 'pending', 'unknown', 'complete',
)

#: 单节点续跑判定。``redo`` / ``unknown`` / ``legacy_incompatible`` 都必须
#: 留给人决定，不能被下一步编排压回 ``run`` 或 ``skip``。
RESUME_VERDICTS: Final = (
    'run', 'skip', 'redo', 'unknown', 'legacy_incompatible',
)

#: operator 的外层下一动作状态。``pipeline_complete`` 只说明 ``NODES`` 已无
#: 待跑节点，不冒充终审包已经完成或整书已达到 delivery ready。
OPERATOR_NEXT_STATES: Final = ('ready', 'blocked', 'pipeline_complete')

#: 运行页对确定性 operator 下一动作的只读投影。书目录尚未形成时保留
#: ``unknown``，不能把“还判不了”冒充从 stage0 开始；其余三态逐字复用
#: ``OPERATOR_NEXT_STATES``。
RUN_RESUME_FRONTIER_STATES: Final = (*OPERATOR_NEXT_STATES, 'unknown')

#: 下一动作真正由谁承接。``code`` 是 0 成本确定性节点，``human`` 是需要人工
#: 在 Stage 页面裁决的审批/恢复边界（Commander 受托审阅已退役，2026-09-15
#: 用户裁决：审批一律人工，自动跑书停在闸前等待过闸后续跑）；前端只呈现，
#: 不从 paid/waiting_on 猜。
RUN_NEXT_HANDLERS: Final = (
    'code', 'executor', 'human', 'none', 'unknown',
)

#: operator 执行许可的两种来源。``ready`` 只许可当前 ``operator next``
#: 精确给出的 node/flags；``recovery`` 只许可生产策略表登记的阻塞恢复动作。
#: 两者必须分开：blocked action 的 hash 只描述“为什么停”，不能冒充恢复命令授权。
EXECUTION_PERMIT_KINDS: Final = ('ready', 'recovery')


#: 一条来源边**为什么**得到那个结论。**用 id 不用措辞**（同 `STAGE0_WARNINGS` 的理由）：
#: 它要进校验报告的第一节（`Q-4`：来源在前判定在后），而报告不在逐字节验收范围内——
#: 拿字符串前缀认原因会随一次措辞改动静默断掉。算法唯一落点 `novel/prov.py::freshness`。
#:
#: ⚠ **`ghost` 是这里最要紧的一个**，它是 `Q-4` 亲自点过的一个 bug：
#: 原型的比对函数对「取不到指纹」的条目**静默跳过**，于是一条指向已删文件的边
#: 既不报错、也不报「不知道」，只是安静地当作一条正常的上游边。
#: 盘上真有一条（`成年人不做选择` 那本的 `stage4/writer_context.json` 挂着
#: `stage4/plan_chapters.INVALID.json`，而那个文件已经不在了——0 成本提升路读完就删了它）。
#: ⇒ **取不到指纹 = 那份上游已经不在盘上，要报出来，并按名字说是哪一条。**
#:
#: ⚠ `consumed_undeclared` 与 `match` 的差别是本项目最容易被顺手抹平的一条：
#: 「本节点没声明自己读了哪些字段」**不是**「我读的东西没变」。**建槽位，不填槽位**——
#: 七个现役层逐步补投影，在补上之前那条边诚实地是 `unknown`。
FRESHNESS_REASON: Final = (
    'no_provenance',        # 这份产物根本没有来源账 ⇒ unknown（**永不 fresh**）
    'no_inputs',            # 有来源账，但 inputs 缺失或为空 ⇒ unknown
    'ghost',                # 边指向的文件不在盘上 ⇒ unknown ＋ 点名报出
    'file_changed',         # 整份文件的指纹对不上 ⇒ stale
    'consumed_changed',     # 本节点真正读到的那部分变了 ⇒ stale
    'binding_input_contract_obsolete',  # 已证明的旧章级 B 缺输入映射契约 ⇒ stale
    'binding_input_contract_unproven',  # 章级 B 输入契约/来源形状未知 ⇒ unknown
    'consumed_undeclared',  # 边上 consumed=null ⇒ 该边 unknown
    'consumed_unresolved',  # 声明过投影，但这次算不出来（没登记投影函数）⇒ 该边 unknown
    # 只有 store 亲见 Blueprint 原始 inputs 精确等于 {MainText, Global} 才铸造；
    # 与通用 consumed_unresolved 分开，防止未来/额外来源边误走零调用迁移。
    'legacy_blueprint_inputs',
    'global_naming_blocked',  # latest Global 的称名 HARD 使 Blueprint 投影暂不可消费 ⇒ unknown
    'match',                # 取到且相等 ⇒ fresh
)

#: 校验报告的徽章。**`amber` = 通过了，但有 N 项查不了**（`passed and unver_count > 0`）。
#: 它不是绿色。判定放服务端算，免得「PASS + 未验证」在某个分支上被顺手画成绿的。
BADGE: Final = ('pass', 'amber', 'fail')

#: 结论严重度。**`metric` 是度量不是问题**——实测一本书 61 条结论里 41 条是 metric，
#: 画成警告色整屏都是红黄，真正的 `info` 就淹了。
#: **`metric` 与 `info` 必须分开**：度量无条件产出，不许挂在「没问题」那一支上——
#: 把度量挂在「没问题」分支上，等于规定「一出问题就不许知道现状」。
SEVERITY: Final = ('hard', 'info', 'metric', 'unver')

#: 判据的实现类型。它与严重度正交：`hard` 可以由机械代码或语义模型给出，
#: `info` 也一样。前端据此把 0 成本机械验收与付费语义审判分开展示。
CHECK_KIND: Final = ('mechanical', 'semantic')

#: 判据命中后的处置责任。它与严重度同样正交，且由判据的现有契约派生：
#: `llm` = 有唯一、受约束的反哺路径；`deterministic` = 有机器可执行的 fix；
#: `human` = 多解、查不了或没有安全自动路径；`none` = 纯度量。
REMEDIATION: Final = ('llm', 'human', 'deterministic', 'none')

#: 校验报告里**一条注册判据**的处境（`docs/schema/_report.md` §一）。
#: 报告按**判据**编排而不是按**结论**编排，这四个态是那次翻转的全部落点。
#:
#: ⚠ **`skipped` 是这里最要紧的一个。** 它是「注册了，而实现这次没有被调用」——
#: 旧形状（`oks` 是一串**没有 rule id 的散文字符串**）根本表达不出它，
#: 于是「跑过没命中」与「压根没跑」在 API 响应里同形。
#: 实测代价：stage7 的 `rn_unregistered` 在判据表里注册着、**全仓 0 行实现**，
#: 而它是 `info` ⇒ **每本每次都打绿灯**，还挂在「本节点唯一能发现白名单不够的东西」那句注释下面。
#:
#: ⚠ `unver` 与 `skipped` 方向相反，别合并：前者是判据**诚实地说这次够不着**
#: （没名表 / 名表过滤后为空 / 盲区 100%），是判据的边界；
#: 后者是**系统自己的漏洞**——一条注册在案的判据这一次没有任何代码去执行它。
STATUS: Final = ('ok', 'hit', 'unver', 'skipped')

#: 作业状态。`landed_red` = 命令以 exit 1 结束：产物已落盘，但报告有阻断红行；
#: 它与「命令没跑成」的 `failed` 处置相反，不可再合并。
#: **服务重启后原本 `running` 的一律改判 `unknown`**，不是 `ok`：
#: 重启前它是死是活我们真的不知道。
JOB_STATE: Final = ('running', 'ok', 'landed_red', 'failed', 'unknown')

#: RunSupervisor 管理的整本书运行状态。它与单节点 ``JOB_STATE`` 正交：
#: ``paused`` 是可恢复的稳定态，``pending`` 是已建契约但尚未打开 execution
#: epoch；``stopped`` 是服务端同时拿到 exit 2 与显式 stopped 终验后的安全停点，
#: 不与真正崩溃的 ``failed`` 混写。服务重启后无法证明进程/锁状态时仍必须
#: 投影成 ``unknown``。
AGENT_RUN_STATE: Final = (
    'pending', 'running', 'paused', 'ok', 'landed_red', 'stopped', 'failed',
    'unknown',
)

#: 前端只呈现服务端明确开放的控制，不根据相似状态自行猜测。v1 running
#: 没有 Supervisor，因此只能 stop；v2 pending/running 才有 pause，paused 才有 resume。
AGENT_RUN_CONTROLS: Final = ('pause', 'resume', 'stop')

#: 上传响应里的自动开跑结局（R-11.28）。API ``UploadResult.autostart`` 与
#: 前端类型共用这一份；不得在 schema 里再抄字面量。
UPLOAD_AUTOSTART_OUTCOMES: Final = (
    'started', 'skipped_stage0_failed', 'replay')

#: parent ended 所保留退出码的来源；外部 CLI 的同值数字不继承小说产物语义。
#: 旧事件缺来源时保持未知，不从理由或诊断文字补推。
AGENT_RUN_EXIT_ORIGINS: Final = ('novel_cli', 'commander', 'supervisor')

#: 服务空间 retention preview 返回的候选类别。它描述的是服务端已经判定出的
#: 清理对象，不是浏览器可自行选择或重算的维护策略；schema 只引用这一处取值面。
RETENTION_CANDIDATE_KINDS: Final = (
    'log', 'operator_telemetry', 'trash', 'checkpoint',
)

#: v2 parent run 的 append-only 事件域。一个 execution epoch 可以包含多次
#: 身份完全相同的 Commander 精确续会；代码或 skill 漂移只滚动 epoch，不创建新 parent run。
#: ``continue`` 还承载 Stage4 typed repair/replan 的显式 producer 边界：parent 不变，
#: 但修订不得与修前 aggregate 共用 session_run_id。
AGENT_RUN_EVENT_V2_KINDS: Final = (
    'started', 'epoch_opened', 'paused', 'resumed',
    'epoch_closed', 'ended', 'reconciled',
)
EXECUTION_EPOCH_REASONS: Final = (
    'initial', 'continue', 'manual_resume', 'code_change',
)
EXECUTION_EPOCH_DRAIN_STATES: Final = ('drained', 'unknown')

#: agent 外层进程结束后对**交付盘面**做的独立判定。它不能复用 `JOB_STATE`：
#: `exit 0` 只证明 headless agent 自己正常退出，不证明 Stage2 已逐项 PASS、终审包
#: 已存在或成书仍可下载。`unknown` 专门承接旧事件与终验自身无法完成的情况。
AGENT_DELIVERY_OUTCOME: Final = (
    'ready', 'ready_with_disclosures', 'stopped', 'failed', 'unknown',
)

#: 单个 commander / executor logical session 的终态。它与整书外层
#: ``AGENT_DELIVERY_OUTCOME`` 不是一张表：前者判断一次受限会话是否把本节点正式
#: 产物交付成功，后者判断整本书是否 ready。进程状态与交付状态继续正交记录。
AGENT_SESSION_PROCESS_STATE: Final = ('ok', 'failed', 'stopped', 'unknown')
AGENT_SESSION_DELIVERY_STATE: Final = (
    'delivered', 'blocked', 'failed', 'unknown',
)

#: Stage5 正式提交链的固定阶段枚举（session audit wire v2 的 ``first_failure_phase``
#: 取值面）。提交诊断必须能说出失败发生在哪一段；只留下笼统的
#: ``artifact_verification`` 而不给阶段，是 P0-1 之前那次 1/17 落盘事故里
#: 「谁也说不清停在哪一步」的根因，不得复发。``session_end`` 属于收尾段：
#: 它失败时 ended 事件本身就写不出去，列进枚举是为取值面闭合。
STAGE5_COMMIT_PHASES: Final = (
    'prepare_commit', 'formal_write', 'typed_readback',
    'adoption', 'mark_committed', 'committed_verify', 'session_end',
)

#: Stage5 闭合闸（``require_stage5_closed``）在 CLI 运行边界只拦 stage6/stage7
#: 的**写效果**跑法。这些 flag 组合是 0 成本只读 / 人工恢复路：闸关闭时仍可
#: 运行，否则人连开闸需要的证据都拿不到（硬闸与恢复面必须同批交付）。
#: 一条 stage6/stage7 命令的 flag token 全部落在本表内 ⇒ 免闸；否则必须先有
#: 当前 typed closure 回执。取值面以各 NodeSpec.flags 为准，由测试交叉钉住。
#: Stage5 闭合回执的取值域（P0-2）；公开 schema 用 ``Literal[...]`` 引用，
#: 字面量只许活在这一张表里。
STAGE5_MUST_APPEAR_RULINGS: Final = ('confirmed_present', 'accepted_exception')
STAGE5_CLOSURE_GROUPS: Final = ('fact_landing', 'unver')
STAGE5_CLOSURE_STATES: Final = (
    'missing', 'review_required', 'blocked', 'ready', 'closed', 'expired',
)

#: Stage5 顶部 Reviewer 可交给写手的**唯一**局部改写指令闭集。
#:
#: 这些 code 只描述已经在 current 正文中逐字举证的压缩手段；执行端另行固定
#: “压回预算、保留 plan slots / 事实 / 时序 / 必须逐字内容”等不可编辑约束。
#: Commander 的 actor/reason 与证据只作审计和定位，绝不能被拼成自由 prompt。
STAGE5_REVIEWER_REWRITE_DIRECTIVE_CODES: Final = (
    'remove_repetition',
    'compress_exposition',
    'repair_wording',
    'smooth_transition',
)


#: 常用 executor 配置（``PipelineAgentProfiles``）在盘上的三态；与整书代跑
#: ``AgentRun`` 无关。唯一发布口仍是 ``GET /api/agent/capabilities``（路径保留，
#: C3 起只回 legacy providers 面）与 ``/settings/agent-profiles``。
AgentProfileSettingsState = Literal['unset', 'configured', 'unreadable']

#: 付费外发网络偏好（``/settings/network``，2026-09-29 c252e8b9）的两态闭集：
#: ``direct`` 是产品默认（本地代理会切断长请求），``proxy`` 须带 http(s) 地址。
#: 存储（`novel/orchestrator/network_settings.py`）与 API 视图共用本别名，
#: 不在 api.py 里抄第二份字面量。
NetworkEgressMode = Literal['direct', 'proxy']

#: accepted checkpoint 采用模型身份的两种证据来源。``provider-reported``
#: 表示 provider 明确回显且与请求精确相等；``driver-enforced`` 保留给历史
#: Agent CLI 账只读解码（C3 起新写入不再产生该证据）。
AgentModelEvidence = Literal['provider-reported', 'driver-enforced']

#: 现役唯一 executor wire kind。C3 硬切后本地 Agent CLI（``kind=agent``）已退役；
#: 新写入只认直接 LLM API。稳定 provider id；provider → model / effort / thinking
#: 的组合能力由动态 capability registry 发布，不能把平面枚举交给浏览器任意做
#: 笛卡尔积。新增 GLM 等 provider 时必须同时补 driver、预算闸、传输层与 capability。
ExecutorBackendKind = Literal['legacy']
LegacyLlmProvider = Literal['deepseek']

#: 作业记录是一串**事件**，`JOB_STATE` 是它们折叠出来的**视图**（`_orchestration.md` §2.1）。
#:
#: 【为什么是事件流而不是一份被改写的 JSON】原型每次状态变化都 `os.replace` 整份
#: `<job_id>.json`，并且**为此写了一段重试**——`PermissionError: [WinError 5]`，
#: 因为服务一边写一边被 SSE 轮询读，**实测在测试里必现**。
#: 改成 append-only 之后，理由与成本账是同一条：**永不需要读-改-写**
#: ⇒ 并发安全、被 kill 不损坏、可 grep。
#:
#: ⚠ `reconciled` 是**追加一条**，不是回去改 `started` 那一行——
#: 回去改就毁掉了 append-only 的全部意义，而那正是「被 kill 不损坏」的来源。
JOB_EVENT_KINDS: Final = ('started', 'step', 'ended', 'reconciled')

#: 「待人工」清单的八类。`repairable` 是已经有 0 成本确定性修复路由的红行；
#: `human_only` 才是只能人工裁决的红行。
#: **通用 check 报告里的 `unver` 仍不进这张清单**——那是判据的边界，
#: 不是待办；只有 TODO-24 全书评测中可以通过证据与人工决策消解的
#: `pending` / `UNVER` 才以 `verification` 入列。
#: ⚠ `approval` 是**按设计就该人做的一步**，不是故障，但它确实该在这张清单上。
INBOX_KIND: Final = (
    'invalid', 'rejected', 'stalled', 'repairable', 'human_only',
    'verification', 'upstream', 'approval',
)

#: 结论的根因**第一嫌疑**层。`corpus` = 原文自身如此，没有可修复的流水线阶段。
#: 当前产物层均可拥有本层结论；这也是 `Finding.owner` 的完整取值面。
OWNER: Final = (
    'stage0', 'stage1', 'stage2', 'stage4', 'stage5', 'stage6', 'stage7', 'corpus',
)

#: `docs/BACKEND_CONTRACT.md` 的「谁在查」那一列的**取值面**。
#:
#: 【为什么它必须闭合】那一列的全部价值是「让『我们真的知道』与『我们只是写着』读起来不一样」。
#: 解析器读不懂一个记号时**不会报错**，它只是把那一行读成「什么也没声明」——
#: 而那正是这一列存在的理由要防的事。实测：本文档一度有 **10 行**是解析器读不懂的。
#:
#: 【为什么是八个而不是三个】前四个是「有一条注册判据在管它」，后四个都不是判据，
#: 但每一个都是一种**真实存在、且互不相同**的守卫形态。写成 `HARD` 是说假话，
#: 写成 `无守卫` 也是说假话——两种假话方向相反，危害相同。
#:   · `HARD` / `INFO`     判据在查，阻断 / 提示
#:   · `GATE`（闸门）       落盘闸门：不合格就不落盘
#:   · `METRIC`（度量）     **无条件产出的读数，不是结论**。它必须有一条注册判据（sev=metric），
#:                         但它永远不「命中」——写成 `INFO` 会让度量与告警重新合流（判据 21）
#:   · `CARRIER`（载体）    **类型系统在守**：`Literal` 收走了取值面，构造期就拦住了。
#:                         ⚠ 准入：那一格必须点名是哪个字段
#:   · `STRUCTURAL`（结构） **代码结构使然，要违反它必须先改代码**（例：「一次调用产三候选」
#:                         ——它就是「`ask_json` 只被调了一次」这件事）。
#:                         ⚠ 准入见 `contracts/requirements.py`：那一格**必须写出代码落点**，
#:                         否则它只是「无守卫」的化妆品
#:   · `DELEGATED`（见另一条）职责已转授给别的编号（拆层时留下的）。它自己不查任何东西，
#:                         但**「转授了」与「没人查」必须分得开**
#:   · `N_A`（不适用）      这一条按定义无从「查」（例：一份视图清单）
#:
#: ⚠ 后四个**不许有判据指着它们**——有判据就说明它其实是判据在守，那一格标错了
#: （`tests/test_coverage.py::test_no_rule_claims_a_non_judge_requirement` 钉着）。
WHO_CHECKS: Final = ('HARD', 'INFO', 'GATE', 'METRIC',
                     'CARRIER', 'STRUCTURAL', 'DELEGATED', 'N_A')

#: 一条**判据**可以声称自己对应哪个取值。比 `WHO_CHECKS` 少四个：
#: 判据存在本身就否定了「载体在守 / 结构在守 / 转授 / 不适用」。
#: `HUMAN` 只出现在这一侧——它是「有人给这一步签过字吗」那一类 `unver` 判据
#: （判据在**报告人做没做**，不是在替人做判断）。
WHO_CHECKS_RULE: Final = ('HARD', 'INFO', 'GATE', 'METRIC', 'HUMAN')


# ─────────────────────────────────────────────────────────────────────────────
# 二、分章（stage1）
# ─────────────────────────────────────────────────────────────────────────────

#: 非正文标记的四类。判据已按 161 本语料标定：命中 27 本 / 34 条标记 / **0 假阳**，
#: 切点全落在 75.9%~99.3%。
#: ⚠ `后记`/`尾声` **必须全等匹配，不能用包含**：语料里有「节目的尾声」（5 字）、
#: 「孩子出生后记在你的名下」——它们**比真标记还短**，长度上限救不了，全等才救得了。
#:
#: **这张表是开放的**——遇到新写法（「外传」「特别篇」「小剧场」…）就加一个。
#: 但加之前必须过 `tools/probe_nonbody_word.py` 那道闸（`R-1.7` 的加词流程）：
#: 剔除是破坏性的，一个假阳会静默吃掉半本书，而**词表增长是单向的、错了很难发现**。
NONBODY_KINDS: Final = ('番外', '后记', '尾声', '作者有话说')

# ⚠ 这里曾有一张 `NONBODY_KINDS_ADDED` 加词登记表，配一条「原型冻结那份 + 本表 ==
#   现役那份」的逐值比对。常量随 `08240cf`（删除 legacy stage 契约兼容层）一并删除，
#   而那 10 行说明**独自活到了 2026-08-22**，一直指着一个不存在的常量说「加词时同时
#   往这里追加一个词」。原型仓与 163 本语料已于 2026-08-16 永久删除 ⇒ 比对的另一半
#   （原型那份）不复存在，两张表的机制随之整体作废，不是「还没来得及补」。
#   ⇒ 现在往 `NONBODY_KINDS` 加词只有一道闸：`tools/probe_nonbody_word.py`（见上）。

#: 完结标记的闭合词表。**它与 `NONBODY_KINDS` 必须无交集**——
#: 完结标记与非正文标记走的是两条完全不同的路（前者只定切点、不进 `dropped`；
#: 后者进 `dropped` 并记账），同一个词落进两张表，产物会同时按两种口径记它。
#: ⚠ 实测踩过：候选词「正文完」在语料里有 1 处全等命中（`(正文完。)`），
#: 看着像个非正文标记，其实它**已经是完结标记了**。加词前必须查这张表。
END_MARKER_WORDS: Final = ('全文完', '正文完', '完')

#: 从标记行剥出 `core` 的两条规则。**只有一份**——
#: 判据、加词闸门、将来的 stage1 实现都从这里取。
#: 手抄一份不会报错，它只会慢慢漂，而漂掉之后「长度上限」判的就不是同一个东西了。
NONBODY_LEAD_STRIP: Final = r'^[【\[(（]*(?:\d{1,3}[.、．:：]?[ 　]?)?'
NONBODY_TRAIL_STRIP: Final = r'[】\])）。:：\s]*$'

#: 一本书的最少正文章数。**少于它入显式终态**（`R-1.13`，2026-08-10 裁决）：
#: 只有一章的「书」不接受重构。全语料 163 本里正好 10 本是这样（10670~28223 字，
#: 原文里确实只有一个 `1` 标记、之后再没有数字行——真·未分章，不是漏切），全部拒掉。
#: ⚠ 不带字数阈值。原型那条是「≤1 章且 >3000 字才告警」，而语料里最短的书也有 8088 字，
#: 阈值从来没起过作用；留着它只会让判据看起来像标定过，实际没有。
MIN_CHAPTERS: Final = 2

#: 正文占比的下限。低于它判 FAIL —— **红在花钱之前**。
#: 实测区间 0.7595~1.0，离这条线最近的一本还差 0.1595。
#: ⚠ **它属于 stage0（`R-0.9`），不属于 stage1。** 2026-08-10 加了 stage0 之后，
#: stage1 不再剔除任何内容，切点只由完结标记决定，`body_ratio` 恒 ≈1.0——
#: 这条判据留在 stage1 就是**一盏保证会亮的绿灯**：报告照常输出，只是不再携带信息。
BODY_RATIO_MIN: Final = 0.60

#: 丢弃占比的上限。超过它出 warning（不阻断）。实测最大 0.2405。
#: ⚠ **它与 `BODY_RATIO_MIN` 几乎是同一条判据**：`body + dropped ≈ total`，
#: 所以 `dropped > 0.35` 基本等价于 `body < 0.65`。它真正独立起作用的窗口只有
#: `body_ratio ∈ [0.60, 0.65)` 这 5 个百分点——别把两条都响读成「两个独立证据」。
DROP_RATIO_MAX: Final = 0.35

#: 同一个章号重复到这个次数就不是普通重章，是某个固定标记被误判成了章号。
#: 实测命中 1 本（`小三争夺战`：25 章，`chapter_no=1` 出现 17 次）。
NOISY_DUPLICATE_MIN: Final = 3

#: 非正文标记的长度上限（**按码点，不按字节**）。这两个数是假阳的唯一防线：
#: 实测真标记「番外」开头分支最长 17 码点、中/尾分支最长 6，而含「番外」的正文句最短 77。
#: ⚠「番外」一个词就是 6 字节 —— 拿字节数当字数会把上限放大三倍。
#:
#: ⚠⚠ **`NONBODY_HEAD_MAX_CP` 于 2026-08-10 由 30 收到 20**，理由是实跑出来的一段带状结构：
#:   `core ≤ 17` 真标记区（实测全在这）· **`18~20` 切了但不出声** · `21~30` 切了且出声 · `>30` 不切。
#:   中间那三档是**静默假阳区**——一句 18 码点、以「番外」开头的正文句
#:   （实例：`番外的那天她再也没有对任何人提起过了`）会把其后内容全部吃掉，一声不响。
#:   收到 20 之后：`>20` 一律当正文，不切。**宁可漏切也不误切**——
#:   漏切是「番外留在正文里」，在章表上看得见；误切是「正文被删掉」，看不见。
#:   全语料 163 本行为不变（实测真标记最长 17）。
NONBODY_HEAD_MAX_CP: Final = 20
NONBODY_MID_MAX_CP: Final = 12

#: 标记行长到该出声的线。**等于实测真标记上限**——超过 17 就已经不像标记了。
#: 与 `NONBODY_HEAD_MAX_CP` 一起把静默区压成 0：`18~20` 切但告警，`>20` 根本不切。
#: ⚠ 这两个数必须一起看。只改一个都会留下洞：只降告警线 → `21~30` 照样误切；
#: 只收上限 → `18~20` 照样静默。
NONBODY_MARKER_WARN_CP: Final = 17

#: **剔除**的可疑项分类，属于 stage0。**用 id 不用措辞**：它们要进 S-13 校验报告当
#: `info` 级结论，而报告不在逐字节验收范围内（措辞可改）——拿字符串前缀认告警会随时断。
#: ⚠ `early_cut` 与 `R-0.9` 的 FAIL 是同一件事的两个出口：FAIL 阻断、告警解释原因。
#:   它**不是**「FAIL 之外还有一条独立证据」。
#: ⚠ `early_cut` 与 `drop_ratio` 几乎同义（`body + removed ≈ total`），
#:   后者独立起作用的窗口只有 `body_ratio ∈ [0.60, 0.65)`。两条都留是因为严重度不同。
STAGE0_WARNINGS: Final = ('early_cut', 'drop_ratio', 'long_marker')

#: 人工审阅闸门的状态。**两态，不是 `bool`**（2026-08-21 裁决，见 `docs/schema/stage0.md` §2.4）。
#:
#: 【历史】原型曾按「没跑过 · 跑了没批 · 批了」定成三态。其中 `not_run` 是**死值**：
#: 一份合法的 `Stage0Revisions` 一旦存在，机器摄入必然已经跑过，
#: 「没跑过」在产物内不可诚实表达——全仓也从未有过写入方（preflight 存量扫描 0 实例，
#: 2026-08-21）。「没跑 / 崩了 / 阻断」是**执行状态**，由「产物不存在 + JobState」表达。
#: 【为什么不能是 `bool`】「跑了没批」与「批了」必须分开，与 `FRESHNESS` / `JOB_STATE` /
#: `BADGE` 同构：本项目每一次把「不知道」并进「没问题」都付过学费。
#: ⚠ `R-0.3b`（`HARD`）判的是**不满足执行资格 ⇒ stage1 拒绝启动**，判它的唯一落点是
#: `novel/core/eligibility.py::is_stage0_executable`，消费方不许复述条件。
#: ⚠ `ApprovedApproval` 曾有第三格 `origin: 'auto_zero_hit' | 'human'`，
#: 2026-08-22 与 `not_run` 同一判据删除：产地 2026-08-21 已停产，而真正带着它的
#: 存量产物是旧版且本仓不迁移 ⇒ 那些产物连 decode 都过不去，
#: 该取值在盘上**结构不可达**（实测报 `schema v1 不是当前 v3`）。
#: ⚠ 2026-08-22 起 `approved` 那四个平铺字段收成一格判别联合（`Stage0Approval`），
#: 本元组仍是 `status` 那一格的取值面；两者的对账在 `tests/test_invariants.py`。
APPROVAL_STATE: Final = ('pending', 'approved')


#: `stage2.blueprint` 那一次调用的**模型可见边界身份**，落在
#: `_provenance.context.architecture`（`contracts/schema/stage2.py::BlueprintContext`）。
#:
#: 【为什么它是取值域而不是一句注释】它同时被三处消费：产地写它、`BlueprintContext` 的
#: `Literal` 拿它当取值面、判据 `context_scope` 拿它比对。此前产地与判据**各写一份
#: `'bounded-fidelity-v2'` 字面**，改一处忘一处就得到一条「产物明明是新架构、判据说不符合
#: 约定」的假红（`docs/LESSONS.md` 第 21 条那一族）。
#:
#: ⚠⚠ **收窄或改名取值就是破坏产物形状**：`Literal` 收窄之后存量蓝图经 `store.read_own`
#: 当场拒读。按仓规（`CLAUDE.md`）必须同批把 `ARTIFACT_SPECS` 里
#: `STAGE2_CHAPTER_BLUEPRINTS` 的 `current_version` +1，并回答「存量产物由谁重建」。
#: · `v2` → `v3`（2026-08-19）：锚点从「模型逐字转写原文片段」改成「下发带编号的段落、
#:   模型只交段号」。**旧产物语义已坏**（锚点可能不是原文字节）⇒ 单值收窄 + 升版重建，
#:   `.INVALID` 与 `_rounds/` 一律作废，不手补字段冒充当前产地。
#: · `v3` → 并入 `extraction-ir-v1`（2026-08-20，Stage2_improvement 改造）：**放宽成双值，
#:   不升版**。与上一次不同，ir1 的产物**语义不变**——beat 区间仍是段号查表出的精确区间
#:   （ir1 的分割式覆盖是 v3「允许中段空隙」的子集情形），hinge/char_ids/narration
#:   逐字段等价，下游消费面一个不动 ⇒ 旧字节不拒读、两种架构可并存于 A/B 的不同
#:   base 之间。同一书目录同一时刻仍只有一种架构（整跑重建时整体切换）。
#:   响应契约的选择口在 `novel/config.py::stage2_blueprint_contract`
#:   （env `NOVEL_STAGE2_BLUEPRINT_CONTRACT`）。
#: · `extraction-ir-v1` → 并入 `extraction-ir-v1.1`（2026-08-21）：**再放宽一格，
#:   同样不升版**。ir1.1 与 ir1 的差别**只有 blueprint system 里 place 那一段措辞**
#:   （响应模型 / 编译器 / 投影函数逐个共用，见 `novel/stages/stage2/blueprint/contract.py`）
#:   ⇒ 产物形状与语义都不变，两臂可并存于 A/B 的不同 base 之间。
#:   起因是 ir1 的地点填充率相对 v3 掉了一半以上，而 `docs/stage2_contract_experiment.md`（已删，见 Git 历史）
#:   §十一 实测**两个模型上都掉**（Pro −60% / Flash −28%）⇒ 那是契约的独立责任，
#:   不是模型效应。为什么改、以及「加压会不会反被规避」那条风险，写在
#:   `novel/stages/stage2/prompts.py::_BLUEPRINT_TPL_V1_1` 上方。
#:   ⚠ 该实验臂未翻过默认；随后由 v2 的付费验收统一接替，见下一项。
#: · `extraction-ir-v2`（2026-08-23）：同样只放宽取值面、不升 artifact schema。
#:   它把模型响应拆成「止段边界预传 → 冻结边界上的事实细节」两相，并把
#:   `present_names` 的开放生成改成截止本章可见 `char_id` 的关闭式选择；编译后仍落
#:   同一个 `ChapterBlueprints` 形状。2026-08-23 同书付费验收后翻默认：Flash 从
#:   91→126 拍（Pro 130）、145→169 hinge（Pro 168），在场者代词 9→0、名册外
#:   9→0；0 HARD，唯一错误由 ch17 定向重生成一次收敛。完整证据见
#:   `docs/stage2_contract_experiment.md`（已删，见 Git 历史） §十四。
#: · `extraction-ir-v2.1`（2026-08-23）：v2 的相级 effort 单变量实验臂。双书付费
#:   配对因 hinge 召回与成本门失败，默认未翻；完整否决证据见实验手册 §十八。
#: · `extraction-ir-v3 / v3.1`（2026-08-23 登记时）：两条**尚未翻默认的实验臂**，只
#:   放宽 provenance 取值面、不升 artifact schema。v3 把 v2 的“召回边界 → 细节”
#:   合进同一次响应，保留强制 micro_events 脚手架，以减少每章第二次 high 推理；
#:   v3.1 在 v3 上再把 narration 收成可见 char_id，并让 hinge 优先选择代码枚举的
#:   原文候选（覆盖不到仍可交 phrase，避免为省 token 静默漏事实）。两臂最终都编译
#:   成既有 `ChapterBlueprints`；没有付费 A/B 证据前默认继续是 v2。
#: · 2026-08-23 收口：只保留 `extraction-ir-v1.1`、`extraction-ir-v2`、
#:   `extraction-ir-v3.1`。`bounded-fidelity-v3`、v1、v2.1、v3 从公开取值面撤下；
#:   Literal 收窄会拒读带退役 architecture 的存量字节，因此 chapter blueprints
#:   schema v2 → v3，全部存量由 `stage2.blueprint` 付费整跑重建，不做迁移。
#: · 2026-08-24 再收窄：`extraction-ir-v1.1` 退役，当时只留 v2 / v3.1。
#:   `BlueprintContext.architecture` 从三值收成两值，因此 chapter blueprints
#:   schema v3 → v4；存量 v3 由 `stage2.blueprint` 付费整跑重建，不迁移。
#: · 2026-09-04 增加 v3.2：正式 ChapterBlueprints 字段语义不变，只压缩模型响应
#:   wire——召回 cue 改为止段数组，事实 evidence 改选当前章段落候选；代码在任何
#:   checkpoint/业务写盘前核成员与所属拍，再回取既有 SourceSpan。它是兼容扩张，
#:   旧 v2/v3.1 正式字节仍成立，不升 artifact 版本。
BLUEPRINT_ARCHITECTURES: Final = (
    'plot-blueprint-v1',)

#: 当前默认架构。`BLUEPRINT_ARCHITECTURES` 是取值面，这一格是默认值——
#: 翻默认只动这一格（`novel/config.py::DEFAULT_STAGE2_BLUEPRINT_CONTRACT` 从它派生），
#: 盘上存量产物可能来自任一已登记架构，而定向修复是按「基线产物的 architecture」
#: 选契约的；因此取值面收窄必须升 artifact version 并整跑重建，不能只改这一格。
#:
#: 【2026-08-21 翻默认：v3 → extraction-ir-v1】依据是同书同上游的单变量对照
#: （`docs/stage2_contract_experiment.md`（已删，见 Git 历史） §六~§十，无情道 23 章）：
#:   · **当前默认组合 Flash×v3 落不了盘**——3 条 `hinge_drift` 被闸门拦
#:     （本书；另有 2026-08-19 三本书实测同样在 stage2 被拦，n=4）；
#:     Flash×ir1 是 135/135 全定位、0 HARD 直接落盘。去掉段号这一维，
#:     模型就不会把段号写错——它交短语，位置由代码算。
#:   · stage4 首过率升（v3 首轮三档全废重跑；ir1 一次调用 + 0 LLM 提升）。
#:   · 人工语义审查通过，留「切得比 Pro 粗」一条已知代价。
#:     ⚠ **补完 2×2 之后（D 臂 Pro×ir1）这条的归因改了**：拍数是**交互效应**——
#:     契约在 Flash 上对拍数几乎无影响（93→95，+2%），在 Pro 上却压粗 20%
#:     （147→117）。翻默认的结论不受影响（生产默认就是 Flash，那一行 +2%），
#:     但**不许把它说成「契约不影响切分粒度」**：换回 Pro 生成时它会显影。
#:     地点则是契约的独立责任，两个模型上都掉（Pro −60% / Flash −28%）。
#:   ⚠ 未达成项如实记：总付费 token 比 B 臂**高 5%**（原定不高于 B）。赢的是
#:     「成本/被接受的书」（B 那 149k 没落盘），不是「成本/次调用」。
#:   ⚠ n=1（7C 三本配对未跑）。回退就是把这一格改回去，不涉及任何产物迁移。
#:
#: 【2026-08-23 翻默认：extraction-ir-v1 → extraction-ir-v2】依据见上方 v2 登记项与
#: `docs/stage2_contract_experiment.md`（已删，见 Git 历史） §十四。它是**质量换成本**：本书 Flash 真调用
#: 25→43、completion 136,431→271,462；地点 66.7% 仍没到预登记 81% 底线，n=1。
#: 采纳的理由是生产默认模型就是 Flash，而拍/hinge 缺口分别约补回 90%/96%、在场者
#: 四类解析失败归零。默认回退仍只改这一格，但契约取值面的增删另按上方升版纪律走。
#:
#: 【2026-08-24 翻默认：extraction-ir-v2 → extraction-ir-v3.1】用户依据同模型、同上游、
#: 独立空缓存的两书三臂对照作出产品裁决，完整证据见实验手册 §十九。v3.1 两书匿名盲审
#: 均第一（26.0/30、25.0/30），最终正式产物 2/2；v2 为 20.0/30、24.4/30，正式产物
#: 1/2。v3.1 以每章一相替代 v2 两相，两书累计 wire 61 vs 97、付费命令墙钟 958s vs
#: 1,073s，但 prompt+completion 高 11.0%。
#: v3.2 的默认切换只基于“响应中删去编译前即丢弃的重复字段、引用改由代码回取”
#: 这一结构等价性，不冒充真实模型的质量或速度实证。下一次 clean run 仍须核首次
#: 落盘、生产 HARD、人工事实抽查与 P50/P95；v2/v3.1 保留为显式回退。
BLUEPRINT_ARCHITECTURE: Final = 'plot-blueprint-v1'
assert BLUEPRINT_ARCHITECTURE in BLUEPRINT_ARCHITECTURES



#: 原始 txt 的编码候选，**按尝试顺序**。取值面只能从代码来：
#: 全语料 163 本**只出现过 `utf-8`，另四个零样本**——样本再多也补不出没发生过的分支。
SOURCE_ENCODINGS: Final = ('utf-8-sig', 'utf-32', 'utf-16', 'utf-8', 'gb18030')

#: 一条剔除记录是谁圈的。**不许省**（`R-0.8`）：重跑 stage0 时 `machine` 可以重算覆盖，
#: `human` **不许静默覆盖**——没有这个字段，重跑一次就把人的工作悄悄抹掉了。
REMOVED_ORIGIN: Final = ('machine', 'human')

#: stage0 / stage1 连续原文视图里的分区类型。它们是 HTTP 契约的一部分，仍然只在
#: 这里落一次取值面；前端按 `kind` 选渲染，不从 label 文案里猜。
STAGE0_SEGMENT_KINDS: Final = ('keep', 'remove', 'end_marker')
STAGE1_SEGMENT_KINDS: Final = ('hook', 'chapter', 'end_marker', 'hole')

#: Stage1 对照页里，逐章**原始章号**相对上一章的异常分类。
#: 【为什么要有它】纯数字番外与正常数字章节在机器结构上没有区别——零 removed、
#: 零 warning、`clean == source`——只看「原文 + 删除高亮 + warnings」的 Stage0 审批页
#: 对它是盲的。现役流程在 Stage0 批准后免费落 Stage1，再由独立的 s1 人工闸门检查
#: 章号序列与逐章正文；异常必须在这个正式对照页显式呈现。
#: 三类互斥，判定优先级即本元组顺序：`reset` > `gap` > `repeat`。
#: ⚠ 这是**诊断不是判据**：命中不拦任何东西，也不进任何产物，只在审批页上标出来给人看。
#: 番外若接着正文往下编号（50 → 51），三类一条都不命中——那种形态机器看不出来，
#: 只能靠同屏的逐章首行预览让人读出来。**别把它宣传成番外检测器。**
#:
#: 原文章号不落盘，由 ``scan_text`` 当前复扫；``ChapterMarker.chapter_no`` 恒由
#: ``CHAPTER_RE`` 匹配出的数字行解析得到，因此没有 ``non_numeric`` 分支。
CHAPTER_LABEL_ANOMALY: Final = ('reset', 'gap', 'repeat')


#: 默认仍为四并发；6/8 只在显式配置中启用，并继续受整书及供应商总槽限制。
PIPELINE_DEFAULT_CONCURRENCY: Final = 4
PIPELINE_BOOK_CONCURRENCY: Final = 4
PIPELINE_PROVIDER_CONCURRENCY: Final = 8
PIPELINE_TOTAL_CONCURRENCY_MIN: Final = 1
PIPELINE_TOTAL_CONCURRENCY_MAX: Final = 32
PIPELINE_CONCURRENCY_TRIALS: Final = (4, 6, 8)
#: 并发覆盖只支持**新链逐章节点**（B1）：键必须已在 `NODES` 登记。现役注册表
#: 两个逐章 owner：`str06.stage2.chapters`（章观察，B2 五节点重建时登记）与
#: `str06.stage5.body`（正文逐章 owner，scope=chapter:N）。旧链键
#: `stage2.blueprint`／`stage4.design`／`stage4.plan`／`stage5.write`
#: 已随旧链退役删除（str06.md「额度、并发与配置」）。并发度不进语义身份。
PIPELINE_PARALLEL_NODES: Final = (
    'str06.stage2.chapters', 'str06.stage5.body')

# ⚠ 2026-10-04 清理批：本节（旧链 Stage4 计划与篇幅的六张响应前缀表、
#   两级档位与对齐断言）已随旧链 stage4/plot schema 删除；档位标定史见
#   Git 历史与本文件此前版本。现役篇幅带见 `DRAFT_BAND` / `BOOK_BAND`
#   与 stage4/acceptance 的 guard 报告。

# ─────────────────────────────────────────────────────────────────────────────
# 六、写作（stage5）
# ─────────────────────────────────────────────────────────────────────────────

#: Stage5 v1 的历史目录名。只用来识别不兼容盘面和读取不可变旧记录；
#: 它们不是现役写作臂，不得重新暴露到 CLI/API。
LEGACY_STAGE5_MODES: Final = ('deep', 'shallow')


#: 成稿字数 / 本章预算必须落在这个带里。它就是执行带，**没有第二层宽限**。
#: ⚠⚠ **它与 `BOOK_BAND` 不再是同一个数**（2026-08-17 用户裁决）。两条带答的是两个问题：
#:   · 本带答「单章允许偏离预算多少」——写手对齐的是逐拍下发的 `budget_chars`，
#:     而一章的节奏本来就会让某几章胖一点、某几章瘦一点；
#:   · `BOOK_BAND` 答「全书总量允许偏离源书多少」——那是产品承诺，必须紧。
#: 逐章放宽而全书收紧是**自洽**的：逐章的正负偏差在书级相互抵消，全书带兜住总量。
#: ⚠ 定在 0.80~1.20 的依据是实测分布而不是拍脑袋：盘上 31 章的两端极值为
#: **0.814 / 1.206**（见 `contracts/schema/stage5.py` 文件级注释④），本带把它们
#: 恰好包进去 ⇒ 正常写作波动不再触发收缩臂与 `length_over` / `length_under`。
#: ⚠ 收窄回 ±10% 之前先看那两个极值：那不是"写坏了"，是这一层的自然方差。
DRAFT_BAND: Final = (0.80, 1.20)

#: 全书成稿字数 / 全书源字数。
#: ⚠ 原型这里写死 `0.81~1.21`，注释自述推导自「逐章带**配 ±10% 宽限**」——
#: 而那个宽限已随 stage4 裁决拿掉，这条书级带于是建立在一个不存在的前提上。
#: 实测三本 0.949 / 1.022 / 0.980，收紧到本带**代价为 0**。
#: ⚠ **本带刻意不跟随 `DRAFT_BAND` 放宽**：逐章放宽是给单章节奏留余量，
#: 而全书总量是对用户的承诺。两个数不同不是漂移，是上面那条裁决的直接结果。
#:
#: ⚠⚠ **它有两个消费方，而两边的口径不是同一个数**（2026-08-22 起）：
#:   · stage5 `book_length`（INFO）：**全书成稿字数 / 全书源字数**（`len(整章文本)` 那一支）；
#:   · stage4 `plan_book_band`（HARD，`R-5.3c`）：**全书预算合计 / 全书 span 合计**。
#: 两个分母差着「span 覆盖不到的那 2%~3% 正文」（见 `novel/stages/stage4/measure.py`
#: 文件头那张三口径表），**不许拿来做相等断言**——产地已经为这件事把一份合格的计划
#: 判成过 `.INVALID`。共用同一个带是有意的：它们答的是同一个问题（总量偏离源书多少），
#: 只是一个在钱花之前问、一个在钱花之后问。
BOOK_BAND: Final = (0.90, 1.10)

#: canonical 闭卷成稿与源章的 20 字窗口重合度上界
#: （已扣除契约要求逐字保留的部分）。它是提示级信号，不是自动放行门。
SOURCE_OVERLAP_MAX: Final = 0.10

#: 连续多少轮未消 → 判「根因不在写手」，反哺主动跳过。
STOP_REWRITE_AFTER: Final = 2


# ─────────────────────────────────────────────────────────────────────────────
# 七、成书形态与人名残留（stage6 三候选面已随 STR-06 切换退役；旧「即时机械
# 测量」节的 STYLE_MEASUREMENT_CONTRACT 已随投影构建族删除，2026-10-04 清理批）
# ─────────────────────────────────────────────────────────────────────────────

# ⚠⚠ F8a：本节原有的 HOOK_APPROACHES / HOOK_RETIRED_APPROACHES / HOOK_RECIPE /
#   HOOK_CURRENT_APPROACHES / HOOK_SLOTS / HOOK_APPROACH_NOTES 与 slot_of 已整体
#   删除（F6d 退役登记，STR-06 切换）：Stage6 三候选开篇文案面不复存在，Hook 的
#   必要选择进 Stage4 正式 Design（PresentationGraph.hook），机械验由
#   stage5 hook owner 的回执守护。旧盘 stage6/hook.json 只读拒识，历史上下文见
#   `docs/DECISIONS.md` 与 Git 历史。
# ⚠ 2026-10-04 清理批：本节的收尾形态（ENDING/SEP）、人名残留探针
#   （RESIDUAL_* / MIN_SURFACE / DERIVED_ALIAS_SUFFIXES）、旧链成书分隔
#   （BOOK_CHAPTER_SEP＝裸数字行）与恒等基线（IDENTITY_*）常量随旧链
#   consumer（core/bands.py、stage7/subs.py、旧 schema/stage7.py）删除；
#   现役成书章标记是 `assembly.py::_chapter_marker` 的「第N章」行，残留由
#   `naming.py::check_residual` 机械验硬闸守卫。

#: 一条待办此刻是否还等人动作。`reviewed_retained` 表示 typed 决策已存在，
#: 记录因审计/披露而保留；它与“是否阻塞交付”是正交轴。
INBOX_ATTENTION: Final = ('pending', 'reviewed_retained')
INBOX_DELIVERY_EFFECT: Final = ('blocking', 'disclosure')

#: 成书的段间分隔（空行）；`tools/book_overlap.py` 按它切块。旧链的裸数字
#: 章分隔（BOOK_CHAPTER_SEP）已随旧链成书格式删除，现役章标记见
#: `novel/stages/stage7/assembly.py::_chapter_marker`（「第N章」行）。
BOOK_BLOCK_SEP: Final = '\n\n'


# ─────────────────────────────────────────────────────────────────────────────
# 八、0 成本入口的命名体系
# ─────────────────────────────────────────────────────────────────────────────

#: 每一层确定性的部分都必须能 0 成本重算，否则修自己的 bug 要付费（需求 Q-3）。
#: **命名体系本身是资产**：同一个语义在不同节点上必须叫同一个名字。
#:
#: 新链节点的节点级旗标只有 `--check-only`（免费复检：
#: 本地已购行／登记的历史采用与确定性重放，零新购）；Stage0/1 保留 `--dry-run`。
#: 旧链的 typed edit／recovery／revalidate 一族随旧节点整体退役（F7e 登记）——
#: 新链的语义修复走 owner patch（基线身份核验），机械复检走 `--check-only`，
#: 人工编辑面随人工闸退役不再存在。**表不全 ⇒ 系统在对用户说假话**（`Q-3`）：
#: `is_paid` 拿本表当白名单、未知一律按付费算，所以这里只剩真实存在的两个。
FREE_ENTRIES: Final = {
    '--check-only': '按当前契约免费复检：只允许缓存命中与登记的历史采用，'
                    '零新购、纯读不写盘；报 FAIL 退出码非零',
    '--dry-run': '只打印将要发出去的东西，不调用、不写盘',
}

#: 挂在**付费**动作上的 flag。F8a 后为空：新链没有「把免费节点变付费」的旗标，
#: 付费性由节点默认路径（`spec.paid`）与 `--check-only` 的免费覆盖共同决定。
PAID_FLAGS: Final = {}

#: 重算时**只对本次新引入的错误入终态** —— 旧违约照报但不挡路。
#: 否则一条不相干的旧问题会把重算这条路永久堵死。
REVALIDATE_ONLY_NEW_ERRORS: Final = True


#: 部分改写旗标（旧链 trim／recheck 面）随 Stage5 人工闸退役删除；新链的精确
#: 重做范围由章回执链与 IR 身份机械推导，不靠旗标声明。
PARTIAL_REWRITE_FLAGS: Final = ()

#: 带值 flag：值说的是「这个值是什么」。F8a 后收录新链工具驱动
#: （`novel/stages/chain.py`）的入口级取值旗标——它们进 argv，因此
#: `flag_tokens` 必须知道值不是旗标；节点级旗标仍只有 `--check-only`。
REQUIRED_PLACEHOLDER_PREFIX: Final = '待填·'

VALUED_FLAGS: Final = {
    # ⚠ F8A：本表收敛为**节点级**带值旗标。工具驱动（`novel/stages/chain.py`）的
    # 入口级取值旗标（--node／--through／--run-dir／--wire-dir／--adopt-root／
    # --book-sid／--budget／--reasoning-effort…）由工具自己的 argparse 治理——
    # 它们不进节点 argv（编排经 `novel.cli run <node> <sid>` 派发），登记在这里
    # 只会让 `test_every_registered_flag_is_reachable` 失去意义。
    '--source': 'stage0.remove 的原始小说路径（首次摄入；相对路径相对 cwd）',
}

#: flag → 这一次会不会写盘、写的是什么。**默认按 `NodeSpec.produces` 推导**，
#: 本表只收「与 produces 不同」或「纯读」的例外。
#:
#: F8a 后只剩两条纯读旗标：`--check-only`（免费复检）与 `--dry-run`。
#: 旧链的免费但写盘一族（revalidate／apply-edit／recover-prepared…）随旧节点
#: 整体退役；新链的免费恢复入口（saved_response／historical_adoption／
#: owner_patch／committed_set_recovery／deterministic_replay）全部经同一付费
#: 边界落盘，写效应即对应节点的 `produces`，无需逐旗标覆盖。
WRITE_EFFECTS: Final = {
    '--dry-run': '',
    '--check-only': '',
}

#: 节点维度的写盘效果覆盖。F8a 后为空：新链没有「同一旗标在不同节点不同形」
#: 的例外——每个节点的全部跑法（默认付费／--check-only 复检）写效应都等于
#: 该节点自己的 `produces`。表保留是因为结构仍被 `would_write` 消费。
NODE_WRITE_EFFECTS: Final = {}


# ─────────────────────────────────────────────────────────────────────────────
# 九、阶段与节点 —— 命名的唯一落点（2026-08-10 裁决）
# ─────────────────────────────────────────────────────────────────────────────

# 【病根：两个正交的概念被压成了一个轴】
# **`stage`** 是产物目录 + schema 层 + 需求节的归属；**`node`** 是可跑的作业单元。
# 两者不是 1:1——stage2 有两个节点、stage5 有写作与验收两相、stage4 的上下文组装是
# 0 成本节点、stage0 有三个人参与的动作。
#
# 节点 id 使用稳定的 `<stage>.<动词>`；stage 是产物层轴，node 是可执行动作轴。
# label、需求节、完成度和写入面都从本表派生，避免在多个消费者中手写平行清单。


#: AgentRun 单步 operator 可登记的自动 REDO 策略（取值面，闭集）。
#: ⚠ F8c 收敛：`stale_chapters`（旧 `stage5.write --chapters` 的章行导出）与
#: `review`（Stage5 Review 轮次面）两个策略随旧链退役删除，其使用者（旧节点键）
#: 已整体移入 `RETIRED_NODES`。R3（2026-09-28）为跨节点回派新增 `owner_revision`
#: ——只允许由**盘上 open 的 typed issue 记录**（`runtime/issues/<owner>.json`）
#: 证明的修订重跑；它与 `canonical` 一样只经默认 flags 重跑本节点，不猜参数。
#: 多留一个没人登记的名字，就是给「猜一组 flags」留一道门（判据 32：没人用的
#: 取值与没人查的约束同形）。将来确有两种合法重放参数时，先有消费者再加名字，
#: 不为预留扩大闭集。
OPERATOR_REDO_STRATEGIES: Final = (
    'canonical',          # 默认 flags 会完整重建本节点当前正式输出
    'owner_revision',     # R3：下游 typed issue 回派的 owner 修订重跑（open 记录证明）
)


WORKFLOW_ROLE_CONTRACT: Final = 'pipeline-cost-latency-v2'
WORK_UNIT_EXECUTOR_MAX_CONTENT_CALLS: Final = 4
WORK_UNIT_MAX_DELIVERY_ATTEMPTS: Final = 4
WORK_UNIT_GRANT_ADDITIONS: Final = (1, 2, 3, 4)
#: R3 自愈（2026-09-28 裁决 1）：同一 owner 的**自动付费修订轮**上限。
#: 「每单元最多 2 轮，同一问题集重复即停」——ask_json 的同节点修订内容轮与
#: 跨节点回派（`runtime/issues/<owner>.json` 的 `rounds_total`）共用本上限；
#: 耗尽即停给人（owner patch／人工重跑），不隐式扩大授权。
OWNER_REVISION_MAX_ROUNDS: Final = 2
#: 跨节点回派的**每根累计上界**（R10c 硬化，2026-10-02）：单 owner 轮上限
#: （上面那行）防不住「多 owner 互相打断对方收敛」的级联身份 churn——任一
#: owner 修订改变下游请求身份→重购→额度尽→grant→另一 owner 再修→再 churn
#: （R10c c1 旧根实证螺旋）。本闸对**一本书一个输出根**的跨节点回派总量
#: 计数（`runtime/cascade_rewinds.json` 持久，重启不重置）；超界按 item 停给
#: 人，不隐式继续付费。量级依据：健康链全程回派个位数；12 拦在灾难之前。
CASCADE_REWIND_LIMIT: Final = 12
#: 整书级付费内容调用总闸（2026-09-15 全自动裁决）。
#: 工作单元额度按 `(node, scope)` 各自记账，防不住跨单元的总量失控；本闸在
#: `reserve` 时对整本书的**全部**内容调用合账设绝对上限。量级依据：正常完成
#: 一本书每单元 1–2 次调用（≤ 约 100 次），全部单元同时耗尽 4×额度在 40 章
#: 书上可达约 700——那已是灾难性质量事件，512 拦在它之前。持久于
#: `_service/runs/<sid>/book_budget.json`，重启/恢复/重建 job 不重置；
#: 追加只能显式人工（`novel/work_units.py::grant_book_budget`），
#: 自动恢复路径不得追加。
BOOK_CONTENT_CALL_CEILING_DEFAULT: Final = 512
#: 全部付费节点的统一输出上限（2026-09-27 D6）：截断（finish_reason=length）是
#: 节点设计缺陷——停在该 item、记录 reasoning 与正文 token，不自动重试、不加预算；
#: 根因从协议瘦身与量级表（`docs/REQUIREMENTS.md` B0）解决，不以降 effort 代替。
#: 取代现役 65536（旧调用因 16k thinking 截断的历史教训由上限与量级表吸收）。
LLM_MAX_OUTPUT_TOKENS: Final = 131072
#: 确定性重建白名单（2026-09-15 全自动裁决；F8a 随节点表翻为新链键）：改选等
#: 后置决定触发的自动重建只允许进入这些节点（全部 `paid=False`、确定性）加交付终验
#: （`novel/orchestrator/delivery.py`）。任何自动重建路径不得触发付费节点——
#: 不是「顺便补齐上游」的整书 start。测试钉扎白名单节点的付费与确定性。
DETERMINISTIC_REBUILD_NODES: Final = frozenset({'str06.stage7.assemble'})


class NodeLifecycleSpec(NamedTuple):
    """节点职责登记；运行状态和剩余预算必须读取持久工作单元账。

    ``approval_bindings`` 把审批 blocker 映射到 (属主节点, scope)：它登记
    「这道闸属于哪个节点的工作面」，供历史 reviewer 工作单元只读解码使用。
    Stage4 四道闸、Stage5 写作授权/闭合回执、stage6.prepare 接受闸与
    stage7.approve 已随 2026-09-15 全自动裁决（批次 B2/B3/B4）整体退役——
    现役 ``NODES`` 只保留 stage6.approve 的人工改选面（不阻塞下游）。
    """

    unit_kind: str
    input_workpack: str
    generation_mode: str
    review_mode: str
    review_scope: str
    repair_actions: tuple[str, ...]
    repair_owners: tuple[str, ...]
    session_scope: str
    recovery_entries: tuple[str, ...]
    executor_content_limit: int = WORK_UNIT_EXECUTOR_MAX_CONTENT_CALLS
    approval_bindings: tuple[tuple[str, str], ...] = ()
    approval_projection: tuple[str, ...] = ()


class NodeSpec(NamedTuple):
    """一个可跑的作业单元。

    ⚠ `paid` 说的是**默认路径**花不花钱。真正的贵贱由 `(node, flags)` 二元组决定，
    唯一落点在 `novel/orchestrator/nodes.py::is_paid`——而**贵贱只有一处算**（`R-11.7`）。
    `FREE_ENTRIES` 里的 flag 一律让它变 0 成本；反向只有 `paid_flags` 这几个。
    """

    stage: str                          # 产物层。STAGES 之一
    section: str                        # 需求节。本节点的判据都该指向这一节里的 R 行
    paid: bool                          # 默认路径调不调 LLM
    label: str                          # 人读的一句话。**前端从这里取，不许手写第二份**
    paid_flags: tuple[str, ...] = ()    # 能把一个 0 成本节点变成付费的 flag
    flags: tuple[str, ...] = ()         # ★ 本节点接受哪些**跑法** flag，见下
    required_flags: tuple[str, ...] = ()  # ★ 少一个就拼不出可执行 argv 的 flag，见下
    produces: tuple[str, ...] = ()      # ★ 默认路径往盘上写哪几份产物，见下
    # 可选首档；未做 A/B 的节点必须保持 None（即 high）。
    # ⚠ 取值面是 `low` / `high` / `max` / `None`：`medium` 与 `xhigh` 虽然 API 收，但被
    #   服务端静默映射回 `high` ⇒ 允许它们就是允许一个「写着降了档、实际一档没降」的配置，
    #   而账本那一列会如实记下 `medium`，事后分辨不出来。`max` 是**升档**（2026-08-21
    #   为 FC-13 质量实验启用，官方 effort 前缀已从 `4744479^` 原样取回）：它由
    #   `rungs_from_effort` 显式选中；异常不再自动切换 effort。
    #   判定落点是 `novel/llm/client.py::rungs_from_effort`，不靠本注释。
    effort: str | None = None
    # 本节点钉死的模型 id；'' = 跟随全局默认（`novel/config.py::DEFAULT_MODEL`，
    # 可被 `DEEPSEEK_MODEL` 覆盖——A/B 换臂的显式口）。
    # ⚠ **只有「产品裁决过、不许被环境变量静默翻转」的节点才许填非空**。
    #   2026-09-16 用户裁决：全链 DeepSeek Flash，取消 stage4 Pro 钉死；
    #   现役付费节点 ``model=''``，跟随全局默认（``DEFAULT_MODEL`` / ``DEEPSEEK_MODEL``）。
    model: str = ''
    # AgentRun 的 operator 每执行一个动作就重新采完整快照，因此只有它可以在
    # ``redo`` 已被证明来自 declared consumed projection 变化时，按这里登记的
    # 唯一策略铸下一张 execution token。普通 durable queue 不消费这格，仍停给人；
    # 未登记 = 没有唯一、安全的重放 argv，绝不能从 ``paid`` 或空 flags 猜。
    operator_redo: str | None = None
    lifecycle: NodeLifecycleSpec | None = None
    # 联合首抽回执只在采用该计算路径时产生，不是每本书必备的运行知识。
    stage_required: bool = True


#: 全部作业单元。⚠ 顺序即处理链顺序，队列与断点续跑都按它排。
#:
#: Stage5 v2 只有一条 canonical 闭卷写作路径。旧 deep/shallow 值只能出现在
#: 不可变作业历史的私有解码边界；现役节点形状无选臂字段，同书作业共用一把锁。
#:
#: ⚠ **没有 `check` 节点。** 复检一律是 `--check-only` flag。原型两头都有
#: （`check_partition` 是独立脚本、`n3_style --check-only` 是 flag），正是 `Q-3` 点名的
#: 「三个节点行为不一致」。
#:
#: 【`flags` 那一列的取值从哪儿来】移植节点取真实抓包
#: （`docs/BACKEND_CONTRACT.md` §八冻结的 `enums.json`，36 条 `actions`）与原型脚本 `argparse`；
#: 新写节点取已定契约与真正接线的 CLI 消费方。没有证据的节点填 `()`——
#: **空元组是诚实的**，而随手编几个是抢跑（判据 32）。
#: ⇒ F7 已把 stage0 查清：`remove` 接 `--source/--dry-run/--check-only`，
#:   `edit` 接 `--apply-edit`；`approve` 是整份报告盖章，没有额外参数。
#: ⇒ `stage4.context` / `stage7.assemble` 空：它们**本身**就是那个动作，
#:   `stage4.context` / `stage7.assemble` 本身就是动作，因此没有同义 flag。
#: ⇒ 三层 `*.approve` 的结论不一样：`stage6.approve` 确实有一个
#:   候选序号 ⇒ F1 补成 `('--pick',)`；`stage7.approve` 已退役（移入
#:   RETIRED_NODES）：机械校验通过后由 rename producer auto seal。
#:   `stage0.approve` 同样已退役。
#:
#: 未登记的 flag 会由命令构造器直接拒收，节点表只描述当前真实入口。
#: 【`required_flags` 那一列：少一个就拼不出可执行 argv 的 flag】它是 `flags` 的**子集**，
#: 补它是因为「必填」此前**只活在节点函数体里**（`stage0.approve` 少 revision 乐观锁或 `--actor/--reason`
#: 就 `TerminalState`），而 `NodeSpec` 表达不出来 ⇒ `build_argv` 只能拼出一条缺参数的命令，
#: `preview` 于是照着摊开一条**注定 exit 2** 的命令，而 skill 的工作流契约明写
#: 「逐字执行 preview 给出的全部步骤」——两边对不上（2026-08-16 复盘 §六.1）。
#: ⇒ 拒收挪到 `validate_run`（展示与执行共用的那一处），`preview` 把缺的那几个渲染成
#: `REQUIRED_PLACEHOLDER_PREFIX` 占位符并标注；占位符**本身也会被 `validate_run` 拒**，
#: 所以「照抄不改直接跑」拿到的是一句人话，不是一条记着 `待填·actor` 的假账。
#: ⚠ 只登记**无条件**必填的。跟着另一个 flag 才必填的那些在 `CONDITIONAL_REQUIRED_FLAGS`。
#: 【`produces` 那一列：默认路径往盘上写哪几份】它是「完成度按**产物存在性**判定」
#: （`R-14.1`）的输入，也是 `would_write` 默认为真的依据——
#: **每个节点的默认路径都产一份产物，那是「节点」这个词的定义**。
#: ⚠ 值里带 `{}` 的是**逐份不定**的（stage5 逐章 / 验收轮次），存在性不能靠一次 `is_file()`
#: ——原型正因如此把 stage5 整个排除在它那张完成度表之外，代价是
#: 「有没有陈旧产物」那个徽章**不覆盖成稿**。这里不排除它，改成逐章数。
#: ⚠ 同一份产物可以被两个节点写（`stage6.hook` 产候选、`stage6.approve` 落定稿），
#: 这不是重复登记：**一层要哪几份**取的是本层各节点的并集。
#: 全部作业单元。⚠ 顺序即处理链顺序，队列与断点续跑都按它排。
#:
#: 【F8a 翻转：候选链成为唯一现役链】本表由 `contracts/schema/workflow.py`
#: 的候选节点表**原样激活**（F7e 冻结基线，`tests/test_str06_f7e_freeze.py` 钉住）：
#: `str06.` 前缀节点取代旧 Stage2/4/5/6/7 全部节点键；B2 起现役面为 14 个节点。 旧键整体移入
#: `RETIRED_NODES` 只供 append-only 作业历史解码，不 preview、不调度、不建新队列。
#: Stage6 整层退役（`STAGES` 不再含 `stage6`，`RETIRED_STAGES` 登记）。
#: 【B2 五节点重建（2026-09-27，D9）】`str06.stage2.source` 退役，Stage2 扩为
#: book／chapters／names／associate／compile 五 owner，公开节点 11→14；冻结
#: 基线随 B2 重新钉定（`tests/test_str06_f7e_freeze.py`）。
#:
#: 【为什么 `str06.` 前缀原样保留】候选身份在 F7a–F7e 已完成付费对账、 epoch 与
#: ledger 记账；切换时改命名空间等于让全部已购证据的身份悬空。前缀同时从结构上
#: 与 Stage0/1 的 `stageN.<name>` 键保持可分（旧链解码不混新链调度）。
#:
#: 【`flags` 那一列】新链每个付费节点只接受 `--check-only`（免费复检：缓存／wire
#: 采用与确定性重放，零新购）；两个确定性节点（compile／assemble）本身免费，
#: `flags=()` 是终态。入口级旗标（`--through`／`--node`／`--wire-dir`／
#: `--adopt-root`／`--body-chapters` 等）属工具驱动不属于节点身份
#: （候选节点表 `flags` 列的同一纪律）。
#:
#: 【`produces` 那一列】值与候选产物表逐字相等（对账在
#: `tests/test_str06_candidates.py`）。三级路径按 `contracts/artifacts.py`
#: 的家族键登记；`{nn}`／`{title}` 是逐章／逐书名占位符。
NODES: Final[dict[str, NodeSpec]] = {
    # Stage0 只有一个 JSON 权威和一个正文投影；运行时报告不落盘。
    # ⚠ `stage0.approve` / `stage0.edit` 已随 2026-09-15 全自动裁决退役
    #   （移入 RETIRED_NODES）：机械校验通过即由 producer 写入 auto 批准
    #   （`system:stage0-auto`）；剔除的语义正确性（番外盲区）随交付披露。
    'stage0.remove':    NodeSpec('stage0', 'R-0',  False, 'stage0 非正文剔除',
                                  lifecycle=NodeLifecycleSpec('book', '原始文本', 'deterministic', 'none', '机械校验通过即自动接受', ('recompute',), ('stage0.remove',), 'none', ('--check-only',), 0),
                                  flags=('--source', '--dry-run', '--check-only'),
                                  produces=(artifact_paths.STAGE0_REVISIONS,
                                            artifact_paths.STAGE0_CLEAN_TEXT)),
    'stage1.partition': NodeSpec('stage1', 'R-1',  False, 'stage1 分章',
                                 lifecycle=NodeLifecycleSpec('book', 'stage0.remove 的机械通过稿 clean_text', 'deterministic', 'none', '机械覆盖与章号校验', ('recompute', 'stop'), ('stage1.partition',), 'none', ('--check-only',), 0),
                                 flags=('--dry-run', '--check-only'),
                                 produces=(artifact_paths.STAGE1_MAIN_TEXT,
                                           artifact_paths.STAGE1_HOOK_COPY),
                                 operator_redo='canonical'),
    # ── STR-06 新链（F8a 激活；owner runner 见 novel/stages/chain.py）──
    # B2（2026-09-27 职责解耦 D3/D8/D9）：Stage2 按语义单元与作用域拆五节点，
    # `str06.stage2.source`（book＋chNN 联合抽取）退役。观察与关联协议见
    # `contracts/schema/observation.py`；compile 是 Characters＋Blueprint 双真源
    # 的唯一 writer（`stage2/compiled/book_extraction.json` 派生输入退役）。
    # 恢复入口沿用候选生命周期：saved_response（本地已购行）、historical_adoption
    # （F7a 登记的跨根历史采用）、owner_patch（基线身份核验后的语义修复）、
    # committed_set_recovery（有效提交集合恢复：只补缺章，崩溃窗口按请求身份
    # 免费重放）、same_context_rewrite（同 Context 有限重写）、deterministic_replay
    # （确定性节点免费重算）。
    'str06.stage2.book': NodeSpec(
        'stage2', 'R-2', True, 'Stage2 书级观察（人物候选、称谓及证据、初态、世界约束）',
        flags=('--check-only',),
        operator_redo='owner_revision',
        produces=('stage2/observations/book.json',),
        lifecycle=NodeLifecycleSpec(
            'book',
            '正式 Stage1 MainText 章数组（阅读序密集）＋hook_copy 章前文字（e00 证据段）',
            'executor', 'none', '机械校验自证；语义未审以 unver 披露',
            ('stop',), ('str06.stage2.book',), 'executor:book',
            ('saved_response', 'historical_adoption', 'owner_patch'), 4)),
    'str06.stage2.chapters': NodeSpec(
        'stage2', 'R-2', True, 'Stage2 逐章观察（事件与三类转移；互不依赖，D3）',
        flags=('--check-only',),
        operator_redo='owner_revision',
        produces=('stage2/observations/ch{nn}.json',),
        lifecycle=NodeLifecycleSpec(
            'chapter',
            '书级人物表（book 观察）、本章句段与第 1..k-1 章原文、抽取协议',
            'executor', 'none',
            '机械校验自证；章观察互不依赖，缺失单元恢复不重购已存结果（D3）；'
            '逐章失败留证 runtime/chapter_observation_failures.json'
            '（「最近一次完成执行」形状，与正文留证同形共用）',
            ('stop',), ('str06.stage2.chapters',), 'executor:chapter',
            ('saved_response', 'historical_adoption', 'owner_patch'), 4)),
    'str06.stage2.names': NodeSpec(
        'stage2', 'R-3', True, 'Stage2 源名称结构观察（家族、名、化名、称谓构词）',
        flags=('--check-only',),
        operator_redo='canonical',
        produces=('stage2/observations/names.json',),
        lifecycle=NodeLifecycleSpec(
            'book', '书级人物表（book 观察）、完整 MainText 与 hook_copy 章前文字',
            'executor', 'none',
            '机械校验自证；名称同一性留证，歧义停止；姓氏构词探针只报告',
            ('stop',), ('str06.stage2.names',), 'executor:book',
            ('saved_response', 'historical_adoption', 'owner_patch'), 4)),
    'str06.stage2.associate': NodeSpec(
        'stage2', 'R-2', True,
        'Stage2 跨章关联（同一命题、同维状态、悬念配对、跨章因果与续场，D8）',
        flags=('--check-only',),
        operator_redo='owner_revision',
        produces=('stage2/observations/associate.json',),
        lifecycle=NodeLifecycleSpec(
            'book', 'book 与 chapters 观察经 names 已证明同一性归一后的句柄及最小文本（compiler 投影）',
            'executor', 'none',
            '机械校验自证；只选已给句柄，未决项保持未决、不默认合并或默认不同（D8）',
            ('stop',), ('str06.stage2.associate',), 'executor:book',
            ('saved_response', 'historical_adoption', 'owner_patch'), 4)),
    'str06.stage2.compile': NodeSpec(
        'stage2', 'R-2', False,
        'Stage2 确定性编译：解析证据、生成 ID、执行关联，发布 Characters＋Blueprint（D9）',
        produces=('stage2/characters/characters.json',
                  'stage2/blueprints/chapter_blueprints.json',
                  'stage2/acceptance/fold.json'),
        operator_redo='canonical',
        lifecycle=NodeLifecycleSpec(
            'book', 'book／chapters／names／associate 四份观察',
            'deterministic', 'none',
            '未决项拒绝发布并按 item 报告（D8）；双真源唯一 writer（D9）',
            ('recompute', 'stop'), ('str06.stage2.compile',), 'none',
            ('deterministic_replay',), 0)),
    'str06.stage4.preserve': NodeSpec(
        'stage4', 'R-5', True, 'Stage4 Preserve（保存策略义务）与 ReconstructionBrief 投影',
        flags=('--check-only',),
        operator_redo='owner_revision',
        produces=('runtime/preserve_checkpoint.json',
                  'stage4/grounding/reconstruction_brief.json'),
        lifecycle=NodeLifecycleSpec(
            'book', '源 core 与 Blueprint v13', 'executor', 'none',
            '机械校验通过即自动接受',
            ('stop',), ('str06.stage4.preserve',), 'executor:book',
            ('saved_response', 'historical_adoption', 'owner_patch'), 4)),
    'str06.stage4.body_design': NodeSpec(
        'stage4', 'R-5', True,
        'Stage4 目标故事与导演说明设计（BodyDesign＋real 承载标注派生 SemanticBinding）',
        flags=('--check-only',),
        operator_redo='owner_revision',
        produces=('stage4/design/body_design.json',
                  'stage4/grounding/semantic_binding.json'),
        lifecycle=NodeLifecycleSpec(
            'book', 'Preserve checkpoint、身份投影与 Characters v4', 'executor', 'none',
            '机械校验通过即自动执行；CS-3 bind 退役——承载位由 wire 的 real '
            '标注派生，双产物同组发布（semantic_binding 唯一 writer）',
            ('stop',), ('str06.stage4.body_design',), 'executor:book',
            ('saved_response', 'historical_adoption', 'owner_patch'), 4)),
    'str06.stage4.hook_design': NodeSpec(
        'stage4', 'R-5', True, 'Stage4 Hook 设计并经 publish_graph 唯一发布 PresentationGraph',
        flags=('--check-only',),
        operator_redo='owner_revision',
        produces=('stage4/design/presentation_graph.json',),
        lifecycle=NodeLifecycleSpec(
            'book', 'BodyDesign、Characters v4 与身份投影', 'executor', 'none',
            '机械校验通过即自动执行；graph 唯一 publisher',
            ('stop',), ('str06.stage4.hook_design',), 'executor:book',
            ('saved_response', 'historical_adoption', 'owner_patch'), 4)),
    'str06.stage4.compile': NodeSpec(
        'stage4', 'R-6', False, 'Stage4 确定性编译：ExecutionPlan／Guard／WriterBookIR／HookContext',
        produces=('stage4/compiled/execution_plan.json',
                  'stage4/acceptance/guard/compile.json',
                  'stage4/acceptance/guard/report.json',
                  'stage4/acceptance/packet_metrics.json',
                  'stage4/context/hook/hook.json',
                  'stage4/ir/writer_book_ir.json'),
        operator_redo='canonical',
        lifecycle=NodeLifecycleSpec(
            'book', 'PresentationGraph、SemanticBinding 与 Characters v4',
            'deterministic', 'none', 'Guard M1–M8 写前机械校验；零错误才放行',
            ('recompute', 'stop'), ('str06.stage4.compile',), 'none',
            ('deterministic_replay',), 0)),
    'str06.stage5.hook': NodeSpec(
        'stage5', 'R-8', True, 'Stage5 Hook 成文（同 Context 有限重写＋机械验）',
        flags=('--check-only',),
        operator_redo='canonical',
        produces=('stage5/hook/hook.txt', 'stage5/hook/receipt.json'),
        lifecycle=NodeLifecycleSpec(
            'book', 'HookContext（正式输入确定性重建）', 'executor', 'none',
            '单产＋机械验；重写上限内无合法候选即停止，不披露放行',
            ('stop',), ('str06.stage5.hook',), 'executor:book',
            ('same_context_rewrite', 'saved_response', 'historical_adoption',
             'owner_patch'), 4)),
    'str06.stage5.body': NodeSpec(
        'stage5', 'R-7', True, 'Stage5 按章正文成文（机械检查＋有限重写＋章级回执）',
        flags=('--check-only',),
        produces=('stage5/body/ch{nn}.txt', 'stage5/acceptance/body/ch{nn}.json'),
        operator_redo='canonical',
        lifecycle=NodeLifecycleSpec(
            'chapter', '章包（WriterBookIR 逐章确定性投影；各章独立，唯一输入面）',
            'executor', 'none',
            '章级机械验（硬失败不披露放行，只有篇幅带可披露）；原子写正文→章回执；'
            '无合法候选停该章（各章独立，其余章照常；逐章失败留证 '
            'runtime/body_failures.json——「最近一次完成执行」形状：'
            'requested/attempted/chapters/recorded_at，与章观察同形共用）',
            ('stop',), ('str06.stage5.body',), 'executor:chapter',
            ('committed_set_recovery', 'saved_response', 'historical_adoption',
             'owner_patch'), 4)),
    'str06.stage7.naming': NodeSpec(
        'stage7', 'R-10', True, 'Stage7 Naming（NameMap v5 赋值；Apply 归 assembly）',
        flags=('--check-only',),
        operator_redo='canonical',
        produces=('stage7/naming/name_map.json',),
        lifecycle=NodeLifecycleSpec(
            'book', 'Characters v4 名称结构与 Blueprint v13', 'executor', 'none',
            '机械校验通过即 auto 定稿；ApplyNames 是替换唯一应用者',
            ('stop',), ('str06.stage7.naming',), 'executor:book',
            ('saved_response', 'historical_adoption', 'owner_patch'), 4)),
    'str06.stage7.assemble': NodeSpec(
        'stage7', 'R-10', False, 'Stage7 改名替换与成书装配（TXT 先落盘、manifest 最后提交）',
        produces=('stage7/final/{title}_rewriting.txt',
                  'stage7/final/delivery_manifest.json'),
        operator_redo='canonical',
        lifecycle=NodeLifecycleSpec(
            'book', 'NameMap v5、章回执链、Hook 回执与正文', 'deterministic', 'none',
            '章回执链核对＋单遍替换＋残留检查；交付可确定性重放',
            ('recompute', 'stop'), ('str06.stage7.assemble',), 'none',
            ('deterministic_replay',), 0)),
}

# 所有节点必须登记真实职责；不能给新增节点隐式发放模型或修改权限。
assert all(spec.lifecycle is not None for spec in NODES.values())
assert all(owner in NODES for spec in NODES.values()
           for owner in spec.lifecycle.repair_owners)


def approval_work_unit(waiting_on: str) -> tuple[str, str] | None:
    """审批 blocker 到 (属主节点, scope) 的唯一投影；未知 blocker 不归属任何节点。

    历史上这是 Commander 审阅工作单元的预算属主投影；Commander 退役
    （2026-09-15）后它继续回答「这道闸属于谁的工作面」。STR-06 关闭批删除
    存量 reviewer 账解码面（RETIRED_APPROVAL_BINDINGS 历史只读表随批删除）；
    投影只看现役 ``NODES`` 的 approval_bindings，历史 blocker 一律无归属。
    """
    found = [(node, scope) for node, spec in NODES.items()
             for blocker, scope in spec.lifecycle.approval_bindings
             if blocker == waiting_on]
    if len(found) > 1:
        raise ValueError(f'审批 blocker 重复登记：{waiting_on}')
    return found[0] if found else None


def approval_scope_allowed(node: str, scope: str) -> bool:
    """审批 scope 只由现役节点登记派生；未登记的 scope 不属于任何审批面。

    ⚠ STR-06 关闭批：退役节点键与历史 scope 放行表（RETIRED_APPROVAL_BINDINGS）
    已随存量解码面删除——未知节点与未知 scope 一律 False（关闭式，fail closed）。
    """
    spec = NODES.get(node)
    if spec is None:
        return False
    return scope in {value for _, value in spec.lifecycle.approval_bindings}

#: 允许配置直接 LLM executor 的现役节点。这里只列**真正调用生成/审判模型**的
#: 付费节点；``str06.stage4.compile``／``str06.stage7.assemble`` 由代码执行
#: （`executor_content_limit=0` 结构性禁付），不能在 API 上挂一份看似生效、
#: 实际无人消费的 executor override。
#:
#: 本表显式列值而不从 ``NODES`` 现算，配套闭合测试守 ``spec.paid`` 双向一致：
#: 新增付费节点却没接 executor、或代码节点误标 paid，两种漂移都必须响亮地红。
LLM_EXECUTOR_NODES: Final = (
    'str06.stage2.book', 'str06.stage2.chapters', 'str06.stage2.names',
    'str06.stage2.associate',
    'str06.stage4.preserve', 'str06.stage4.body_design',
    'str06.stage4.hook_design',
    'str06.stage5.body', 'str06.stage5.hook',
    'str06.stage7.naming',
)
AgentAvailability = Literal['ready', 'missing', 'unknown', 'unauthenticated']

#: `(节点, 触发 flag)` → 这条跑法**额外**必填的 flag。`NodeSpec.required_flags` 的补集：
#: 那一列管无条件必填，这张表管「跟着另一条跑法才必填」。
#:
#: ⚠ 两张表分开而不是合并成一个可调用的谓词：必填这件事要能被 `preview`
#: **0 成本地摊开**（渲染占位符），谓词做不到——它只能回答是/否，说不出缺哪几个。
#:
#: ⇒ F8a 翻转后本表为空：新链节点的全部旗标只有 `--check-only`（无条件
#: 出现在 `flags` 列，且不改变免费性以外的身份），旧链的 typed edit／recovery
#: 条件必填面随旧节点整体退役。表保留是因为**结构仍被消费**（`required_flags`
#: 查它、`nodes.missing_required` 查它）；空表是诚实的现状，不是漏登记。
CONDITIONAL_REQUIRED_FLAGS: Final[dict[tuple[str, str], tuple[str, ...]]] = {}

# ⚠⚠ **`--revise` 挂哪个节点：D 期登记的那处分歧已在 F4 裁掉**（2026-08-12，用户裁决）。
# D 期把它记在 `stage5.review` 上（读法：返工由验收报告驱动），而原型挂在
# **`n5_write`/`n6_write`**（读法：返工由写手执行）。两种读法都说得通，
# 但它们指向**两段不同的代码**，当时没有任何实现可以裁定谁对，所以原样登记着。
#
# ⇒ 裁给 **`stage5.write`**，判准是本表自己那一列：`--revise` 产的是
# **新一轮 canonical 成稿**（`stage5/chNN.json`，带 `round` +1 与 `revision_of`），
# 不是新一份验收报告。挂在 `stage5.review` 上会让一个 `paid=False` 的节点
# 产出另一份产物，而那与它自己的 `produces` 说的不是一回事——
# **完成度、断点续跑、`would_write` 三处都从 `produces` 派生**，它们会一起说假话。
# 返工前置另由统一 `review_status` 保证：非 fresh Review 不得驱动付费 `--revise`。
#
# 顺带：原型 `n5_write --revalidate` 自述 0 成本，而它的 `FREE_FLAGS` 白名单里**没有它**
# ⇒ 现役界面把一个不花钱的动作画成橙色并弹二次确认。本表已把它补进 `stage5.write.flags`。

#: 现役产物层；编号保留 Stage3 空洞，不重排 Stage4～7。
#: ⚠ F8a：`stage6` 整层退役（三候选改选面与 prepare 链随 STR-06 切换删除），
#: 移入 `RETIRED_STAGES`；旧书 `stage6/` 目录只读拒识为 `legacy_incompatible`。
STAGES: Final = ('stage0', 'stage1', 'stage2', 'stage4', 'stage5', 'stage7')

#: ⚠ STR-06 关闭批（2026-10-03）：`RETIRED_NODES` 历史节点名册已随存量解码面
#: 删除——jobs/queues 历史账的旧节点行不再可整流解码（关闭式拒读，话术
#: `legacy_incompatible`）；旧根的正式状态判定不依赖节点名册（产物层
#: `RETIRED_ARTIFACT_PATHS` 与盘面 `legacy_stage5_paths` 探测独立闭合）。
#: 未知节点一律不进 NODES/STAGES：不能 preview、调度或创建队列。
RETIRED_STAGES: Final = ('stage3', 'stage6')

#: `stage` → 它的需求节。**从 `NODES` 派生，不许手抄。**
#: ⚠ 一个 stage 可以有多节（stage2 → R-2+R-3 · stage4 → R-5+R-6 · stage5 → R-7+R-8），
#: 所以值是元组不是标量。正向覆盖率要取并集。
STAGE_SECTIONS: Final[dict[str, tuple[str, ...]]] = {
    s: tuple(dict.fromkeys(n.section for n in NODES.values() if n.stage == s))
    for s in STAGES
}


# ─────────────────────────────────────────────────────────────────────────────
# 进度信标（agent 代跑的可观察轨迹）
# ─────────────────────────────────────────────────────────────────────────────

#: 跑书进度的节点序列。**直接从 `NODES` 派生，不维护第二张模板。**
#:
#: 旧实现手抄了 14 步，漏掉 `stage0.edit` / `stage2.blueprint` /
#: `stage4.context`；真实 UI 与队列跑到这些节点时，进度屏因此仍显示「还没跑到」。
#: `NODES` 的声明顺序已经是队列与断点续跑的处理链顺序，进度屏只消费同一顺序；
#: 修复回路仍按节点归位，不因 flags/mode 另造一步。
PIPELINE_STEPS: Final = tuple(NODES)

#: 信标事件的两态。**`start` 不带退出码、`end` 必带**——由
#: `contracts/schema/orchestration.py::ProgressEvent` 的后置校验钉住。
PROGRESS_PHASES: Final = ('start', 'end')

#: 一步的五种观察态。**它是观察不是判定**（`R-13.6`）：`ok` 只说明「那条命令
#: exit 0」，而 exit 0 ≠ 合格（`F-087`）——「这层过没过」的唯一判官仍是
#: check 报告的 badge。`landed_red` 保留 exit 1 的「已落盘但判定不通过」，
#: 不许与 exit 2 / 3 / 70 的「没跑成」压成 `failed`。
BEACON_STATES: Final = (
    'pending', 'running', 'ok', 'landed_red', 'stopped', 'failed',
)

#: Commander/operator 的本地阶段遥测。它与 ``PIPELINE_STEPS`` 正交：这里没有
#: node id，人工审阅/终审同步也不因此冒充流水线节点或模型调用。
OPERATOR_PHASE_EVENT_KINDS: Final = (
    'session_started', 'phase_started', 'phase_ended', 'session_ended')
OPERATOR_PHASES: Final = (
    'preflight', 'inspect', 'preview', 'execute', 'review', 'approve',
    'finalize', 'verify')
OPERATOR_TELEMETRY_SOURCES: Final = ('interactive', 'headless', 'skill', 'service')
OPERATOR_TERMINAL_STATES: Final = ('completed', 'failed', 'cancelled', 'unknown')
OPERATOR_DURATION_SOURCES: Final = ('monotonic', 'wall_clock')

#: agent 代跑事件的三态。与 `JOB_EVENT_KINDS` 分开：这边没有 `step`
#: （agent 进程内部干什么不由我们记——那是信标的事），只有生死与改判。
AGENT_RUN_EVENT_KINDS: Final = ('started', 'ended', 'reconciled')

#: Commander 顶层进程与它准许启动的 ``run_step --execute`` 之间的 admission
#: 事件。它不是节点进度，也不是 agent 生死：``closed`` 只关掉**新 claim**，
#: close 前已经取得的 claim 仍须以 ``released`` 精确闭合。
COMMANDER_ADMISSION_EVENT_KINDS: Final = (
    'opened', 'claimed', 'released', 'closed', 'drained', 'reconciled')

#: append-only JSONL 被 kill 留下 non-LF 尾时，恢复记录不写回坏账；每个主体在
#: 独立目录留下 immutable 两相记录，先授权、后确认截断已经 fsync。虽沿用最早
#: admission 纵切留下的名字，beacon 也共用这一个闭集，不能另长第三种恢复相位。
COMMANDER_ADMISSION_TAIL_REPAIR_KINDS: Final = ('authorized', 'applied')

#: 批次 C：live `.INVALID` 的 0-wire ``--revalidate`` 由队列/Supervisor 自动排入，
#: 不再等人填 actor/reason。固定审计身份避免伪装成人类决定；人类 Stage 页与
#: enums.actions 不再发布该用户入口（内部 RecoveryRunPolicy / CLI 仍可复用）。
SYSTEM_INVALID_REVALIDATE_ACTOR: Final = 'system:invalid-revalidate'
SYSTEM_INVALID_REVALIDATE_REASON: Final = (
    'live INVALID passes current canonical HARD rules; auto 0-wire revalidate')
#: Commander 顶层子进程的首事件/空闲超时、同席尝试上限与重试退避已随角色
#: 退役（2026-09-15）删除：这些是驱动层重试参数，executor 会话各自持有
#: 独立的超时与尝试策略（见 ``novel/agents``），不再有全局 Commander 席位。#: TODO-25 的唯一机器契约。它描述的是显示投影，不是访问控制：服务端响应仍含完整数据。
#: 后端发布器、前端 adapter、Screen 与 mock 都消费这一个结构；隐藏路径和承诺不再散落成文案。
SPOILER_DISCLOSURE: Final = {
    'mode': 'display_only',
    'security_boundary': False,
    'server_sends_full_data': True,
    'hidden_paths': (
        'arc_secrets',
    ),
    'known_leakage_surfaces': (
        'characters[].canonical', 'characters[].aliases', 'relations[].effective_from',
        'world_rules[].versions[].desc', 'network_response_and_devtools',
    ),
    'consumers': (
        'web/src/api/spoilers.ts', 'web/src/screens/global/GlobalScreen.tsx',
        'web/src/api/mock.ts',
    ),
    'notice': (
        '这是显示辅助，不是安全边界；服务端仍下发完整数据。关闭时只隐藏长线秘密，'
        '人物登记、身份映射、关系时点、规则正文与网络响应仍可能剧透。'
    ),
}


#: 一次调用当时的缓存口径，记进付费产物 `_provenance`（如 stage2 的 `repairs[]`）。
#: ⚠ **定向重生成必须配 `refresh-write`**（`R-3.11`）：它的 prompt 与整跑时那一批**逐字相同**，
#: 缓存必然命中，于是一个字都没重新生成而它照样打印「已重生成」。
#: ⚠⚠ **`disabled` 没有 CLI 入口，这是有意的，别当死取值删掉。** 它与那批「装置删了只剩
#: 取值」的枚举**不同型**：装置还在（`ResponseCache` 的逐读写开关），缺的只是一个 flag，
#: 而它是冻结需求 `R-12.4`「关缓存是同时关读写」的**唯一表达**——删了那条需求就没有守卫。
#: 也不要顺手补一个 `--no-cache`：`--refresh` 严格更优（重采样且仍留账），
#: 多一个入口只会让人误跑一次「花了钱什么都没留下」的重跑。
CACHE_POLICY: Final = ('default', 'refresh-write', 'disabled')
