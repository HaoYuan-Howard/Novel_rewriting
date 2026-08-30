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

from typing import Final, Literal, Mapping, NamedTuple

import contracts.artifacts as artifact_paths

# ─────────────────────────────────────────────────────────────────────────────
# 一、状态三态 / 严重度四级 —— 「不知道」与「没问题」必须可区分（需求 Q-2）
# ─────────────────────────────────────────────────────────────────────────────

#: 产物新鲜度。**`unknown` 不是 `fresh` 的一种**——它是「这份产物没有来源记录，无从判断」。
#: 画成同一个颜色就等于替人断言了一件没人验证过的事。
#: 实测代价：某次 23 章成稿与任何一版上游都配不上（匹配 1/23），配对关系永久丢失，而界面全绿。
FRESHNESS: Final = ('fresh', 'stale', 'unknown')

#: Stage5 Review 相对当前 RULES、canonical 章集与正文的完整状态。
STAGE5_REVIEW_STATE: Final = (
    'fresh', 'stale', 'unknown', 'missing', 'legacy_incompatible',
)

#: ArtifactState 给界面直接消费的四态。`missing` 不能再由前端拿 `!exists` 推导。
ARTIFACT_DISPLAY: Final = ('missing', 'fresh', 'stale', 'unknown')

#: 文件可用性与来源新鲜度是两轴；这一轴负责 `.INVALID` / 读不回 / 缺席。
ARTIFACT_CONDITION: Final = ('missing', 'ready', 'invalid', 'unreadable')

#: 一本书顶部那句服务端结论。顺序优先级由 `novel.orchestrator.state` 唯一计算。
OVERVIEW_VERDICT: Final = (
    'broken', 'legacy_incompatible', 'stale', 'pending', 'unknown', 'complete',
)

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
    'consumed_undeclared',  # 边上 consumed=null ⇒ 该边 unknown
    'consumed_unresolved',  # 声明过投影，但这次算不出来（没登记投影函数）⇒ 该边 unknown
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

STAGE5_CLOSED_EXEMPT_FLAGS: Final = (
    '--dry-run', '--check-only', '--identity', '--recheck',
    '--revalidate', '--apply-edit', '--wontfix',
    '--emit-edit-template', '--adopt-residual', '--validate-edit',
)

#: agent 运行配置的四个基础取值域。它们刻意做成类型别名而不是新的
#: ``AGENT_*`` 自动发布常量：浏览器需要的是「driver → model → effort / mode」的
#: **组合能力矩阵**，不是四张可以任意笛卡尔积的平面枚举。唯一发布口是动态的
#: ``GET /api/agent/capabilities``；那里还会附上本机 CLI 的可用性与版本。
#:
#: ``unknown`` 只用于探针无法下结论；``missing`` 是 PATH 中没有 CLI；
#: ``unauthenticated`` 留给能够在不发模型请求的前提下明确识别登录态的 driver。
AgentDriver = Literal['claude-code', 'codex']
AgentRole = Literal['commander', 'executor']
AgentAvailability = Literal['ready', 'missing', 'unknown', 'unauthenticated']

#: ``ultra`` 不是 reasoning effort。它若将来开放，必须作为执行模式由具体
#: model capability 显式列出；不能塞进 effort 后再由某个 driver 静默翻译。
AgentReasoningEffort = Literal['none', 'low', 'medium', 'high', 'xhigh', 'max']

#: execution mode 故意不做平面 Literal：它是否可用是 driver/model 组合能力。
#: profile 在载体层保留字符串，registry 当前只声明 ``standard``；未来只有在真实
#: CLI 映射有受测实现之后，才会把 ``ultra`` 加进对应 model capability。
AgentExecutionMode = str

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
#: `novel/stages/stage0/eligibility.py::is_stage0_executable`，消费方不许复述条件。
#: ⚠ `ApprovedApproval` 曾有第三格 `origin: 'auto_zero_hit' | 'human'`，
#: 2026-08-22 与 `not_run` 同一判据删除：产地 2026-08-21 已停产，而真正带着它的
#: 存量产物是旧版且本仓不迁移 ⇒ 那些产物连 decode 都过不去，
#: 该取值在盘上**结构不可达**（实测报 `schema v1 不是当前 v3`）。
#: ⚠ 2026-08-22 起 `approved` 那四个平铺字段收成一格判别联合（`Stage0Approval`），
#: 本元组仍是 `status` 那一格的取值面；两者的对账在 `tests/test_invariants.py`。
APPROVAL_STATE: Final = ('pending', 'approved')

# Human workflow checkpoints kept under Output/_service. These are service
# state, not Stage0 current approval fields.
APPROVAL_GATES: Final = ('s0', 's1', 's2', 's4', 's5', 's6', 's7')
APPROVAL_SEEN: Final = ('read', 'global')

#: Stage4 v4 的章级结构决定。`retain` 是一次显式设计决定，不是“模型忘了改”；
#: `reconstruct` 只声明要改变哪些可复算结构维度，实际变化仍由代码从最终计划重算。
STRUCTURE_MODES: Final = ('retain', 'reconstruct')
STRUCTURE_CHANGE_KINDS: Final = (
    'entry_anchor', 'ending_anchor', 'fact_grouping',
    'disclosure_order', 'noncontiguous_regroup',
)
STRUCTURE_METRIC_CONTRACT: Final = 'atomic-structure-v2'
BLIND_ALIAS_ALGORITHM: Final = 'hmac-sha256-v1'
#: Stage4A 的逐章私有 session 并发上限。配置可以把它调低，但不能调高；
#: 这是该节点的供应商稳定性边界，不与全局 ``MAX_CONCURRENCY`` 混成一把尺子。
STAGE4_DESIGN_MAX_CONCURRENCY: Final = 4
#: Stage4B 章级 session 的并发上限（批次 4）：与 A 的逐章池同一供应商稳定性
#: 边界，配置可以调低不能调高。
STAGE4_PLAN_MAX_CONCURRENCY: Final = 4
STRUCTURE_APPROVAL_DECISIONS: Final = ('accept', 'request_replan')
STRUCTURE_APPROVAL_STATES: Final = (
    'unavailable', 'missing', 'accepted', 'replan_requested', 'expired',
)

#: Stage4A 的正式设计审阅与 B 后最终计划审批是两张不同的票。前者只决定
#: 当前盲候选能否进入付费 B，绝不打开 ``gate.s4``。决定与修复码都必须是
#: 闭合集：自由文本理由只留审计，不能未经代码解释直接塞回模型 prompt。
DESIGN_STRATEGY_DECISIONS: Final = ('accept', 'request_replan')
DESIGN_CHAPTER_DECISIONS: Final = ('accept', 'request_repair')
DESIGN_REVIEWER_KINDS: Final = ('human', 'delegated_operator')
#: 2026-08-29 升 v2（TODO-45 批次 2）：审阅主体新增 A→formal 零调用预检
#: 投影，v1 决定按 subject 相等性自然过期；重新审阅 0 成本，历史保留可解码。
DESIGN_REVIEW_CONTRACT: Final = 'stage4-design-review-v2'
#: `R-5.22` 预检结论的两态。pass = 构造性绑定过整书联合 HARD；
#: blocked = 存在 stop-owner 失败或代码铸造的不可行 witness。
DESIGN_PREFLIGHT_STATUSES: Final = ('pass', 'blocked')
#: Commander 审阅工作包（TODO-45 批次 5）的固定分组顺序——异常优先：
#: blocking 永远第一，全章 compact coverage 永远最后。`phase_deferred`
#: 只登记「Context 落盘后才可判」的规则，不进入结构 subject。
APPROVAL_WORKPACK_GROUPS: Final = (
    'blocking', 'semantic_attention', 'disclosure',
    'metric_only', 'phase_deferred', 'coverage',
)
#: 工作包只有 A / B 两个审阅面；它们都是只读视图，不是作业入口。
APPROVAL_WORKPACK_KINDS: Final = ('design', 'structure')
#: 审批工作包相对当前 typed 决定的处置态。``already_current`` 只表示
#: **同一份工作包摘要**已有当前批准，供调用方跳过重复人工整读；它不把不同
#: actor/reason 的人工声明折叠成同一件事。
APPROVAL_WORKPACK_DISPOSITIONS: Final = ('review_required', 'already_current')
#: Stage6/7 紧凑、hash-bound 终审工作包的两种主体。
FINAL_APPROVAL_WORKPACK_KINDS: Final = ('stage6', 'stage7')
#: 工作包条目只能公布现役 typed 审批决定面；它不会把动作翻译成
#: CLI argv，更不会因为读了一次工作包就启动模型。
APPROVAL_WORKPACK_ACTIONS: Final = (
    'accept', 'request_repair', 'request_replan',
)
#: 工作包条目的交付效果（服务端下发；前端只呈现，不自算）。
APPROVAL_WORKPACK_EFFECTS: Final = (
    'block_b', 'block_context', 'attention', 'disclose', 'read_only',
    'phase_deferred', 'coverage',
)
#: 分组与 effect 的关系也是契约，不许 builder / 前端各猜一份。
#: blocking 唯一的两个 effect 对应 A 挡 B、B 挡 Context；其余组均是单值。
APPROVAL_WORKPACK_GROUP_EFFECTS: Final[Mapping[str, tuple[str, ...]]] = {
    'blocking': ('block_b', 'block_context'),
    'semantic_attention': ('attention',),
    'disclosure': ('disclose',),
    'metric_only': ('read_only',),
    'phase_deferred': ('phase_deferred',),
    'coverage': ('coverage',),
}
DESIGN_REPAIR_CODES: Final = (
    'entry_anchor',
    'ending_anchor',
    'fact_grouping',
    'disclosure_order',
    'noncontiguous_regroup',
    'scene_goal',
    'binding_constraint_unsat',
)
#: 退役但仍可**解码**的修复码。`structure_delta` 是泛化重排指令：arity (0,0)
#: 意味着它可以不带任何 scene/alias 证据提交，等于允许人用一句「重排一下」
#: 制造不可执行的返修。历史决定保留可读（Literal 与 arity 表都仍收它），
#: 新决定在 approve 闸关闭式拒绝，具体语义修复码都带 scene/alias 下限。
DESIGN_REPAIR_CODES_RETIRED: Final = ('structure_delta',)
#: ``code -> (最少 scene_refs, 最少 opaque_aliases)``。审批 schema 与返修 prompt
#: 必须共用这一张表；否则一边接受的“具体反馈”会在另一边变成空指令。
DESIGN_REPAIR_CONSTRAINT_ARITY: Final = {
    'structure_delta': (0, 0),
    'entry_anchor': (0, 1),
    'ending_anchor': (0, 1),
    'fact_grouping': (0, 2),
    'disclosure_order': (0, 2),
    'noncontiguous_regroup': (0, 2),
    'scene_goal': (1, 1),
    'binding_constraint_unsat': (1, 0),
}
#: Stage4B 模型可以报告的语义担忧。它们只作非阻断证据，不得被解释成
#: 编译器不可满足证明；schema、prompt 与 HTTP 枚举共用这一份取值面。
BINDING_CONCERN_CODES: Final = (
    'causal_conflict',
    'stage_conflict',
    'material_conflict',
    'future_leak',
    'insufficient_evidence',
)
#: Stage4B 关闭约束编译器目前能够机械铸造的不可行证明码。顺序与
#: ``novel.stages.stage4.blind::prove_binding_infeasible`` 的两类 witness 对齐；
#: 模型无权直接返回这些正式证明。
BINDING_INFEASIBILITY_CODES: Final = (
    'missing_chapter_catalog',
    'empty_stage_allowlist',
)
DESIGN_REVIEW_STATES: Final = (
    'unavailable', 'missing', 'repair_requested',
    'strategy_replan_requested', 'accepted', 'expired',
)

# ── Stage4 运行时失败 witness 的关闭式路由（TODO-45 批次 1）────────────────
#: typed failure 的 ``repair_owner`` 完整取值面。前两个是现役节点 id；
#: ``stage2.blueprint`` 是上游锚点/目录问题；``human`` 只形成 nonblocking
#: concern；``code`` 是判据/编译器自身漂移；``infra`` 走传输退避、不计内容重采。
FAILURE_REPAIR_OWNERS: Final = (
    'stage4.design', 'stage4.plan', 'stage2.blueprint', 'human', 'code', 'infra',
)
#: 这些 owner 的失败**不是 B 在自己的 session 里能修掉的**：B 首轮见到立即
#: 停止并诚实落盘，不把四轮预算烧在注定失败的重试上。
FAILURE_STOP_OWNERS: Final = ('stage4.design', 'stage2.blueprint', 'code')

#: Stage4 失败发射点 ``code`` → 修复 owner。**闭合表**：表外的 code 一律
#: ``code`` owner（停下来给人看），绝不默认成可重试。
#:
#: 分域依据（B 只绑定舞台与保真材料；场景设计、预算、目标、状态都是 A 的）：
#: · A 的设计域（``stage4.design``）：章集/序号/空章、land 事实落场、因果闭合、
#:   状态承接、首尾目标、预算、写法词、slot/transition、ctx 事实投影与人名。
#: · B 的绑定域（``stage4.plan``）：舞台引用、材料恰好落一次、材料送达、
#:   舞台投影，以及 sha 回显 / infeasible 证明 / 绑定编译拒绝这些 B 自己的响应错。
#: · 上游（``stage2.blueprint``）：人名证据缺位。
#: · 代码（``code``）：来源边与 A/B 字节绑定是 provenance 机制自身；
#:   ctx 事实投影的两侧都是代码组装，命中即代码漂移。
STAGE4_FAILURE_OWNERS: Final[Mapping[str, str]] = {
    # wire / 编译层（plan.py 的 validator 发射）
    'candidate_sha256_echo': 'stage4.plan',
    'infeasible_unproven': 'stage4.plan',
    'binding_compile_refused': 'stage4.plan',
    # A 单元自身的发射点（在 A 的 session 里可原地重试）
    'strategy_chapter_set': 'stage4.design',
    'strategy_style_words': 'stage4.design',
    'blind_compile_refused': 'stage4.design',
    'blind_chapter_problem': 'stage4.design',
    # 联合 HARD（checks.check 发射）
    'one_to_one': 'stage4.design',
    'ordinal_seq': 'stage4.design',
    'empty_chapter': 'stage4.design',
    'fact_ref.land': 'stage4.design',
    'fact_ref.context': 'stage4.design',
    'fact_ref.state': 'stage4.design',
    'fact_ref.turn': 'stage4.design',
    'fact_ref.stage': 'stage4.plan',
    'fact_ref.material': 'stage4.plan',
    'fact_coverage': 'stage4.design',
    'causal_closure': 'stage4.design',
    'state_closure': 'stage4.design',
    'material_coverage': 'stage4.plan',
    'scene_transition': 'stage4.design',
    'goal_landing': 'stage4.design',
    'budget_int': 'stage4.design',
    'budget_band': 'stage4.design',
    'plan_book_band': 'stage4.design',
    'style_words': 'stage4.design',
    'phase_artifact_link': 'code',
    'context_link': 'code',
    'ctx_facts_exact': 'code',
    'ctx_name_attested': 'stage2.blueprint',
    'ctx_stage_explicit': 'stage4.plan',
    'ctx_hinge_delivered': 'stage4.plan',
    # B 不可行证明的两类 witness（批次 2 预检复用同一分类）：关闭目录缺章/
    # 空舞台allowlist 都是 Stage2 锚点/目录问题，不是 A 的场景设计错。
    'missing_chapter_catalog': 'stage2.blueprint',
    'empty_stage_allowlist': 'stage2.blueprint',
}


def stage4_failure_owner(code: str) -> str:
    """失败发射点 → 修复 owner 的唯一读法；未登记的 code 关闭式归 ``code``。"""
    owner = STAGE4_FAILURE_OWNERS.get(str(code))
    if owner is None:
        return 'code'
    assert owner in FAILURE_REPAIR_OWNERS
    return owner


def stage4_failure_retryable(code: str, *, surfacing: str) -> bool:
    """失败只有由能修它的那一方自己发现时才值得原地重试。

    ``surfacing`` 是发现失败的节点 id。``infra`` 例外：传输层失败对任何节点
    都走退避重试，不计内容重采。
    """
    owner = stage4_failure_owner(code)
    return owner == surfacing or owner == 'infra'

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


# ─────────────────────────────────────────────────────────────────────────────
# 三、事件拍与保真材料（stage2 → stage4 → stage5）
# ─────────────────────────────────────────────────────────────────────────────

#: 一拍的叙述层。**一拍内恒定**（分组函数的 HARD 后置条件）——
#: 跨层并拍会把只在回忆里出场的人放进现世街头。
TIME_LAYERS: Final = ('present', 'recall', 'hearsay')

#: 三个叙述层给人看的那一面：**短名**（下发面逐拍渲染用）与**释义**（system prompt 展开用）。
#: ⚠ **两处都要，而且必须来自同一张表**：写作节点的 user 消息里逐拍打的是短名
#: （`现场·某地·在场：…`），system prompt 里展开的是「〔现场〕= 当下正在发生」。
#: 从前 system 那一份是**手抄**的，改措辞时它不会跟着改、也不会报错（判据 25）。
#: ⚠ 键必须与 `TIME_LAYERS` 逐值相同——下面那条 `assert` 是它的落点。
LAYER_CN: Final = {'present': '现场', 'recall': '回忆', 'hearsay': '转述'}
LAYER_GLOSS: Final = {'present': '当下正在发生',
                      'recall': '视角人自己想起的往事',
                      'hearsay': '别人讲给她听的事'}
assert tuple(LAYER_CN) == tuple(LAYER_GLOSS) == TIME_LAYERS, \
    '叙述层的短名/释义与 `TIME_LAYERS` 分家了——渲染顺序会跟着漂，而没有东西会出声'

#: 从原文抽出来的保真「点」。
#: **以「点」不以「段」**：离散原子无法被模仿成文风，只能被放进去或不放进去；
#: 连续文本段一律不下发——写作节点必然沿着它的句序和节奏走，那正是重写要避免的。
HINGE_KINDS: Final = ('硬数字', '要害台词', '物件', '因果前提')

#: Stage2 原子事实的关闭式语义分类。它回答「这条章内真相改变了什么」，不回答
#: 「Stage5 应怎样写」；场景归组、先后与篇幅属于 Stage4 的职责。
FACT_ATOM_KINDS: Final = (
    'event', 'information', 'character_state', 'relationship', 'reader_knowledge',
)

#: `land` 必须由且只由一个新场景落地；`context` 是场景进入时已经成立的事实，
#: 可以被多个场景读取但不算本章新增剧情。两者不能靠 kind 推断，否则「既有关系」与
#: 「关系在本章改变」会再次被压成一个状态。
FACT_ATOM_OBLIGATIONS: Final = ('land', 'context')

#: 每类保真点各守各的维度。
#: ⚠ **这张表不许用 `dict.get(kind, 默认值)` 取**——把写歪的类型兜底成
#: 「原样写入，一个字都不能改」，会让一段叙述文变成逐字要求，而全链没有一层出声。
#: 认不出的类型：产地一侧入显式终态，消费侧丢弃 + 告警。
HINGE_GROUPS: Final = {
    '要害台词': 'verbatim',      # 逐字，允许被【一处】提示语切开（中文小说的标准写法）
    '硬数字': 'keep_value',      # 比【数值】不比字面：下发「30岁」、成稿「三十岁」= 满足
    '物件': 'must_appear',       # 必须出现
    '因果前提': 'must_hold',     # 必须成立，不必写在纸面上 —— 按定义查不了文面，恒 unver
}

#: 四组保真材料**给写手看的标签**，拆成「标签名 + 限定语」两截。
#:
#: 【为什么拆两截】下发面逐条渲染的是**合起来的全串**（`· 原样写入（一个字都不能改）：…`），
#: 而 system prompt 里逐条展开时用的是**短名**（`- 【原样写入】：…`）。两种写法都要，
#: 且必须来自同一个源——从前 system 那一份是手抄的，改标签措辞时它不会跟着改、也不会报错。
#:
#: ⚠ **把四种都当成「必须逐字出现」是最常见的错法**：`must_hold` 要的只是命题成立，
#: 当台词写就会逼出一段背景交代。四条限定语正是在防这件事，**一个字都不许省**。
HINGE_LABEL_PARTS: Final = {
    'verbatim': ('原样写入', '一个字都不能改'),
    'keep_value': ('数值不得改动', '措辞随你'),
    'must_appear': ('必须在场并起作用', '怎么描写随你'),
    'must_hold': ('必须成立，但【不必写在纸面上】', '别为它硬塞背景交代'),
}
assert set(HINGE_LABEL_PARTS) == set(HINGE_GROUPS.values()), \
    '保真标签的四个键与 `HINGE_GROUPS` 分家了'

#: 合起来的全串（下发面逐条用）。**由上表派生，不另存一份。**
HINGE_LABEL: Final = {k: f'{name}（{qual}）' for k, (name, qual) in HINGE_LABEL_PARTS.items()}


def hinge_heading(kind: str) -> str:
    """标签名当**小节标题**用时的写法：去掉名字内部的强调号。

    `must_hold` 的标签名自带一对【】（「必须成立，但【不必写在纸面上】」），
    而小节标题本身又要套一层【】——不去掉就渲染成
    「【必须成立，但【不必写在纸面上】】」。另三个不含【】，本函数对它们是恒等的。
    """
    return HINGE_LABEL_PARTS[kind][0].replace('【', '').replace('】', '')


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
#:   （响应模型 / 编译器 / 投影函数逐个共用，见 `novel/stages/stage2/contract.py`）
#:   ⇒ 产物形状与语义都不变，两臂可并存于 A/B 的不同 base 之间。
#:   起因是 ir1 的地点填充率相对 v3 掉了一半以上，而 `docs/stage2_contract_experiment.md`
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
#:   `docs/stage2_contract_experiment.md` §十四。
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
#: · 2026-08-24 再收窄：`extraction-ir-v1.1` 退役，现役只留 v2 / v3.1。
#:   `BlueprintContext.architecture` 从三值收成两值，因此 chapter blueprints
#:   schema v3 → v4；存量 v3 由 `stage2.blueprint` 付费整跑重建，不迁移。
BLUEPRINT_ARCHITECTURES: Final = ('extraction-ir-v2', 'extraction-ir-v3.1')

#: 当前默认架构。`BLUEPRINT_ARCHITECTURES` 是取值面，这一格是默认值——
#: 翻默认只动这一格（`novel/config.py::DEFAULT_STAGE2_BLUEPRINT_CONTRACT` 从它派生），
#: 盘上存量产物可能来自任一已登记架构，而定向修复是按「基线产物的 architecture」
#: 选契约的；因此取值面收窄必须升 artifact version 并整跑重建，不能只改这一格。
#:
#: 【2026-08-21 翻默认：v3 → extraction-ir-v1】依据是同书同上游的单变量对照
#: （`docs/stage2_contract_experiment.md` §六~§十，无情道 23 章）：
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
#: `docs/stage2_contract_experiment.md` §十四。它是**质量换成本**：本书 Flash 真调用
#: 25→43、completion 136,431→271,462；地点 66.7% 仍没到预登记 81% 底线，n=1。
#: 采纳的理由是生产默认模型就是 Flash，而拍/hinge 缺口分别约补回 90%/96%、在场者
#: 四类解析失败归零。默认回退仍只改这一格，但契约取值面的增删另按上方升版纪律走。
#:
#: 【2026-08-24 翻默认：extraction-ir-v2 → extraction-ir-v3.1】用户依据同模型、同上游、
#: 独立空缓存的两书三臂对照作出产品裁决，完整证据见实验手册 §十九。v3.1 两书匿名盲审
#: 均第一（26.0/30、25.0/30），最终正式产物 2/2；v2 为 20.0/30、24.4/30，正式产物
#: 1/2。v3.1 以每章一相替代 v2 两相，两书累计 wire 61 vs 97、付费命令墙钟 958s vs
#: 1,073s，但 prompt+completion 高 11.0%。
#: ⚠ 这次没有完成预登记的第三本外部 holdout；第二书 v3.1 与 v2 还共同出现 ch6 实体
#: 串线，机械 HARD 没抓到。因此这是带明确风险账的产品默认选择，不冒充全语料质量门通过。
#: v2 继续留在取值面供显式回退；v1.1 已于 2026-08-24 退役。
BLUEPRINT_ARCHITECTURE: Final = 'extraction-ir-v3.1'
assert BLUEPRINT_ARCHITECTURE in BLUEPRINT_ARCHITECTURES

#: ``stage2.global`` 的模型证据响应契约。两臂最终都编译成同一份
#: ``GlobalGrounding``（正式证据仍是 ``SourceSpan``），因此这里只是 wire strategy，
#: 不是 artifact architecture，也不进入盘上 schema 版本。
#:
#: ``paragraph-candidate-v1`` 仍是 TODO-45 P2a 的冷缓存 A/B 臂：在固定语料证明
#: 人审质量不回退、且总 wall/token/成本改善之前，默认必须继续是 locator。
GLOBAL_EVIDENCE_CONTRACTS: Final = (
    'locator-v1',
    'paragraph-candidate-v1',
)
GLOBAL_EVIDENCE_CONTRACT: Final = 'locator-v1'
assert GLOBAL_EVIDENCE_CONTRACT in GLOBAL_EVIDENCE_CONTRACTS

#: Stage2 Blueprint 只读工作包当前绑定的是正式稿还是 live 问题稿。
#: 只有 formal 工作包能提交 ``seen.read``；invalid 包只辅助修复。
BLUEPRINT_REVIEW_SUBJECT_KINDS: Final = ('formal', 'invalid')

#: `_provenance.verbatim_failures` 里 `位置 == 'span'` 那一族的根因取值面。
#: 判据 `verbatim_failed` 只按根因**计数**，不按根因分流，所以这一张表存在的意义是
#: 「产地只许说这几句话」——措辞漂了，报告上同一种病会分裂成两行。
SPAN_FAILURE_CAUSES: Final = (
    '段号不是整数',
    '段号越界',
    '段号区间倒序',
    '起段没有越过上一拍的止段（区间重叠或顺序倒置）',
    '本章没有可编号的段落',
    'span 里既没有段号也不是正式区间',
    '已落盘的精确区间与原文对不上',
    # ── `extraction-ir-v1` 专属（beat 只交 `end_para`，起点由代码推导）──
    # ⚠ compiler 对非法响应逐项发这些措辞、不抛裸异常：抛异常是「进程崩了，
    #   结论未知」，而这几件事都是**数据的病**，该走 `.INVALID` + 修复路由。
    '止段没有越过上一拍的止段（区间倒序或同段重复）',
    '末拍没有到达本章最后一段',
)

#: `位置 == 'hinge'` 那一族的根因 → **该由哪条判据收**。
#:
#: 【为什么是一张表而不是几个 `if`】判据侧此前按**中文子串**猜（`'空档' in cause` /
#: `'本章原文' in cause`）——那是产地与判据之间一条**没有任何东西在守的隐式契约**：
#: 改一句措辞，`hinge_anchor_void` 就静默降级成 `hinge_drift`，报告照样绿。
#: ⇒ 改成按**相等**查这一张表，分家立刻是 `KeyError` 级别的显性失败，
#: 并配一条「产地只许发这几种根因」的守卫（`tests/test_stage2_generation.py`）。
#:
#: ⚠ 四分流里**没有 `hinge_cascade`**：那一条由「本拍的 span 自己没定位成功」推出，
#: 不经这张表（判据侧先看 `beat_id` 在不在已定位集合里）。
#: ⚠ 也没有 `hinge_cross_chapter`：引了别章原文的那一档由判据侧现场回扫全书判定，
#: 产地手里只有本章正文，说不出这句话。
HINGE_FAILURE_ROUTE: Final = {
    '短语不在本章原文里': 'hinge_anchor_void',
    '段号越界，本章没有那一段': 'hinge_anchor_void',
    '段号落在没有被任何 beat 覆盖的空档': 'hinge_in_hole',
    '短语不在它指定的那一段里，而出现在别处': 'hinge_drift',
    '已落盘的 source 与它所属 beat 的区间对不上': 'hinge_drift',
    # ── `extraction-ir-v1` 专属（hinge 只交 `kind + phrase`，无段号）──
    # ⚠ 「多处命中」是新契约信息不足的**预期产物**，不是编的：交接实测 189 条
    #   hinge 反投影后 187 条拍内唯一、2 条双命中（约 1.06%）。它的修法是
    #   **候选选择**（代码枚举命中位，模型只挑 candidate_id），与 anchor_void
    #   （短语是编的，要重给）完全不同 ⇒ 单立一条判据，不并进既有四分流。
    '短语在本拍区间内多处命中': 'hinge_ambiguous',
    '短语不在它所属拍的区间里，而出现在别处': 'hinge_drift',
}

#: hinge 根因 → **修复走哪一级**。与 `HINGE_FAILURE_ROUTE`（根因 → 判据）是
#: **两个消费方的两张表**：那张归判据分流，这张归修复阶梯——混成一张，
#: 改判据措辞就会牵动修复行为。键域必须与 `HINGE_FAILURE_ROUTE` 相同（assert 在下）。
#:
#: 取值面（`extraction-ir-v1` 修复阶梯，`Stage2_improvement.md` §五）：
#:   · `field_patch`    —— 字段级局部修复（候选选择 / phrase 重给），不整章重付；
#:   · `chapter_regen`  —— 整章定向重生成（`stage2.blueprint --chapters`），最后手段。
#: ⚠ 首版刻意只有两级：`beat_patch` / `boundary_patch` 等更细的层级要等
#:   六章高难包付费冒烟的真实 finding 分布出来再立（数据驱动，不预建框架）。
#: 【7A 实测（2026-08-21，无情道 6 章 40 hinge）】drift 3 例、末拍不到底 1 例，
#:   四例同一个根因：**模型的 `end_para` 比它自己 `event` 里写到的内容早收 1–2 段**
#:   （证据逐条在 `outputs/_ab/smoke_extraction_ir_v1_20260821.md`）。
#:   ⇒ 「drift 一律整章重生成」在真数据上是**过度反应**：四例全部可由确定性边界
#:   外扩修好、0 次付费调用。改路由前要先有裁决（见实验手册 §六），本表未动。
REPAIR_ROUTE: Final = {
    '短语不在本章原文里': 'field_patch',              # 编的短语 → 局部重给 phrase
    '段号越界，本章没有那一段': 'chapter_regen',
    '段号落在没有被任何 beat 覆盖的空档': 'chapter_regen',
    '短语不在它指定的那一段里，而出现在别处': 'chapter_regen',
    '已落盘的 source 与它所属 beat 的区间对不上': 'chapter_regen',
    '短语在本拍区间内多处命中': 'field_patch',        # 候选选择，模型只挑 candidate_id
    # ⚠ ir1 的 drift 可能是 phrase 错、也可能是 end_para 边界切错——首版一律整章兜底，
    #   不许用改锚点遮住错误分段；boundary patch 等 7A 读数后再立（5B）。
    '短语不在它所属拍的区间里，而出现在别处': 'chapter_regen',
}
assert set(REPAIR_ROUTE) == set(HINGE_FAILURE_ROUTE), \
    '修复路由与判据分流的键域分家了——新根因必须同时回答「归哪条判据」与「怎么修」'
assert set(REPAIR_ROUTE.values()) <= {'field_patch', 'chapter_regen'}, \
    '修复层级出现了未定义的取值——新层级要先在这里立取值面'

#: `extraction-ir-v1` 响应侧的叙述层枚举 → 产物侧取值。**前缀化只在响应侧**：
#: 产物侧 `TIME_LAYERS` 一个不动（下游 + 盘上数据的硬约束），compiler 是唯一映射落点。
#: 【为什么前缀化】闭合枚举之间的串字段（交接实测 `delivery.summary` 串进
#: `narrative_role`）本质是取值面互相混淆；互不为子串的带前缀值让串字段在
#: schema 闸就死掉。⚠ 刻意没有 `T_UNRESOLVED`：产物侧 `time_layer` 是必填三值、
#: 无落点，允许它等于每个 UNRESOLVED 必然逼出一轮修复调用，直接顶高
#: `paid_calls / accepted_calls`。
RESPONSE_TIME_LAYERS: Final = {
    'T_PRESENT': 'present',
    'T_RECALL': 'recall',
    'T_HEARSAY': 'hearsay',
}
assert tuple(RESPONSE_TIME_LAYERS.values()) == TIME_LAYERS, \
    '响应侧叙述层映射与 `TIME_LAYERS` 分家了——compiler 会把新值映到不存在的产物值'
assert len(set(RESPONSE_TIME_LAYERS)) == len(set(RESPONSE_TIME_LAYERS.values())), \
    '响应侧叙述层映射不是双射'

#: 逐字原话允许被一处提示语切开时，插入部分的内容字数上限。
#: 形态必须在原文字节上长成「收引号 + ≤N 内容字 + 开引号」；
#: 改词、插动作描写、只写半句照旧判缺失。
SPEECH_TAG_MAX: Final = 12

#: 原文↔成稿对齐的锚覆盖率低于此值时只提示「证据不足」，不判内容不合格。
#: 阈值与结论都由服务端下发；前端只画 coverage 原始读数和 state。
ANCHOR_COVERAGE_WARN: Final = 0.6
ANCHOR_COVERAGE_STATE: Final = ('low', 'adequate')

# ─────────────────────────────────────────────────────────────────────────────
# 四、人物与称谓（stage2）
# ─────────────────────────────────────────────────────────────────────────────

#: 「谁在这么叫」。**指称群体一律记 `public`**——「苏府下人」这类群体名解析不出人，
#: 而这个字段的用途是**派发键**（谁在称呼谁），一个解析不出人的串对派发毫无用处。
#: 已知代价：丢掉「哪个群体」（四本实测 2/194 条）。
ADDRESS_BY: Final = ('char_id', 'narration', 'public')

#: 称谓的场合。
ADDRESS_CONTEXT: Final = ('address', 'reference')

#: `Secret.setup` 判别联合的两臂。**`cold` 是一个正当答案，不是缺省值**——
#: v1 时代这一格是 `list[int]`，空数组同时表示「声明没有埋点」与「模型没想过」，
#: 而它是关系闸门隐瞒窗口与 secret 闸门第②臂**两道下发闸门的前件**。
SECRET_SETUP_KINDS: Final = ('cold', 'foreshadowed')

#: relation 的**读者可见**时点与世内成立时点正交：公开事实直接给起章，
#: 秘密关系引用一条 secret 的首次揭示章。两臂是闭合判别联合。
RELATION_VISIBILITY_KINDS: Final = ('public', 'via_secret')

#: secret 知情主体的两臂。具名人物用 registry 外键；无名群体本身就是答案，
#: 以自由文本 label 保留，不能为了闭合引用而逼模型编一个 char_id。
KNOWLEDGE_SUBJECT_KINDS: Final = ('character', 'group')

#: 人名区分度。**不下发给写作节点**——它只给确定性代码用（成稿人名检查 / 文风专名过滤）。
#: 写作 prompt 里从没定义过这个枚举，整份发过去只是噪声。
NAME_DISTINCTIVENESS: Final = ('high', 'medium', 'common')

#: 叙述人称。**只管人称这一个轴**（2026-08-10 裁决，`stage2.md` §2.5）。
#:
#: 【为什么取值面必须改】原型写的是 `first｜third_limited｜omniscient`——把**人称**与**视角限制**
#: 塞进了一个字段（判据 9），而且**表达不了第二人称**（语料里有一本真的第二人称小说）。
#: 「上帝视角 = 全知」是**第三人称的一个子类**，不是它的兄弟类：
#: 「第三人称限知」（文字写「她」，但只进她一个人的内心）是现代小说的主流写法。
#:
#: ⚠⚠ **另一个轴（限知/全知）刻意【不在 stage2 立字段】** —— 那不是遗漏，是裁决：
#:   Stage3 v2 已删除没有稳定下游收益的定性文风字段；不能为了替已退役字段找新住处，
#:   就在 stage2 再立一个没有验证过消费价值的 `focalization`。新字段会增加抽取成本和
#:   事实漂移面，在下游用途被证明前不恢复。
#:   **已知代价**：第三人称全知的源章可能被改写成第三人称限知（丢掉全知声音）。
#:   这是文风保真的缺口，归 `B-09`，**不假装它被覆盖了**。
#:
#: 【三个值各有全语料样本】163 本，剥掉引号内对白只数叙述文（每千字 我/你/他她）：
#:   · `second` —— `功名利禄` 4.2 / **29.2** / 14.7。开头逐字「你是山野村姑…你五岁那年就想好了」。
#:                 **全语料唯一一本**，但它是真的。
#:   · `third`  —— `FQ_皇帝亲自替我宫斗`：ch1 我 28.8（第一人称）→ **ch2~ch19 我恒 0.0**（第三人称）。
#:                 **人称在章之间会变**，这就是本字段必须【逐章存】而不是全书存一个的实测辩护。
#:   · `first`  —— 其余 160 本。
#: ⚠ **别拿代词密度当判据**：`FQ_群臣逼朕送镇国大将军去青楼` 的「我」只有 **0.1/千字**，
#:   看着像第三人称，实际是第一人称、自称是「朕」——**那正是 `pronoun_surfaces` 存在的理由**。
NARRATIVE_PERSON: Final = ('first', 'second', 'third')

#: 世界规则的类别（`canon.world_rules[].kind`）。**七个，闭合。**
#: ⚠ 盘上只见 **4** 个（`social_norm`×4 · `supernatural_rule`×3 · `legal_rule`×3 ·
#: `ability_system`×1），**另三个零样本**——取值面从代码来，不是从盘上量出来的。
WORLD_RULE_KINDS: Final = (
    'social_norm', 'institution', 'legal_rule', 'ability_system',
    'supernatural_rule', 'technology_rule', 'physical_constraint',
)

#: 裸代词。**`address[].surface` 里一个都不许出现，连在别的字上也不行**（`R-2.3`）。
#:
#: 【为什么它必须在契约层而不是 prompt 里】三个消费方：
#:   · stage2 的 `GLOBAL_SYS`（`$PRONOUNS`，下发给模型的那句禁令）；
#:   · 判据 `address_bare_pronoun`（**HARD**，盘上 1/3 本命中：三岁 `char_034='她'`）
#:     与 `address_has_pronoun`（INFO）；
#:   · 判据 `present_names_pronoun`（度量，盘上 15/176）。
#: 一份表三个消费方 ⇒ 唯一落点（判据 25）。写在 prompt 里就等于让判据另抄一份，
#: 而两份分家之后「下发的禁令」与「查的那条」会在某个字上对不上，**且不会报错**。
#:
#: 【为什么理由是「相对说话人」不是「不好看」】代词的所指随说话人变，
#: 而 `address` 记的要是一个**换谁来说都成立的固定叫法**。
#: 「我妈」只在叙述者自己嘴里成立；下游拿它当这个人的名字用，就会写出「我妈对我妈说」。
#: ⚠ 第一人称主角的自称由 `narration.pronoun_surfaces` 承载，**不进 `address`**——
#: 所以那一族判据**不能**拿本表去查（`present_names_pronoun` 是度量不是违约，
#: 正因为「代词按设计永远不在 registry 里」）。
#:
#: ⚠ **它是容器 ⇒ `/api/enums` 自动发布成 `bare_pronouns`，那是有意的**（同 `STYLE_WORDS`）。
#: 容器自动发是**静默**的，所以这个选择要写下来：前端拿它渲染一句图例
#: （「这几个字不能当称呼」），而**违规条目本身已经逐条在报告里**——渲染不产生判定。
#: ⚠ 对照 `RESIDUAL_HIGH_STEMS` 那一格：它**是容器却仍然不发**，因为
#: `confidence` 那个**分数**的全部输入就是那张表，发过去前端能自己重算一个结论。
#: 本表没有那个形状（没有从它派生的分数），所以不必登记为不发。
BARE_PRONOUNS: Final = ('我', '你', '他', '她', '它', '咱')

#: ── Stage2 全局复核项：取值面与**交付效果**分级 ────────────────────────────
#:
#: 【为什么要分级】旧口径是「145 项全部 PASS 才开 Stage4」。它把两件事压成一件：
#: 「这条事实错了会让下游造出一本假书」与「这条读起来不够好」。实测那一本 145 项里
#: **50 项属于后者**——名字够不够独特、称谓场合、结局措辞——它们既不下发写手，
#: 也另有 naming / Stage7 改名审批 / 恒等检查在守。让它们阻断，等于用一道无法自动
#: 判定的闸把整条链锁死，而真正的高影响事实混在同一堆里得不到应有的注意力。
#:
#: ⚠ **分级不是删审计。** `disclosure` 的项一条不少地留在 `global_review.json`、
#: 留在 inbox、留在终审包；它们只是不再拦 Stage4。**不许自动 PASS**。
#: ⚠ **加一个 kind 必须同时选一边**：`review_effect_of` 对未登记 kind 关闭式失败，
#: 而不是给一个「默认 disclosure」——默认放行是这张表唯一不能有的方向。
GLOBAL_REVIEW_KINDS: Final = (
    'world_rule_version', 'secret', 'reveal', 'known_by', 'knowledge_event',
    'name_distinctiveness', 'address_context', 'world_rule_kind',
    'character_set_complete', 'relation_set_complete',
    'world_rule_set_complete', 'secret_set_complete',
    'role', 'fate', 'relation', 'setting_fact', 'secret_setup',
    'conflict_partition', 'arc_analysis',
)

#: 只披露、不阻断的那三类。**它是白名单，其余一律 blocking**（见下面的函数）。
#:
#: · `name_distinctiveness`：名字够不够独特是改名质量问题，`novel/core/naming.py`
#:   与 Stage7 的人名表审批 + 恒等检查已经在守，而它判错不会造出假剧情。
#: · `address_context`：称谓用在什么场合同样受 naming 约束，且是措辞层。
#: · `fate`：**本来就不下发写手**——它是剧透面，Stage4/5 的 packet 里没有它。
#:
#: ⚠ 这三条的 P1→P2 窗口风险由「继续披露」承接：它们不得从审计面消失，
#: inbox 与终审包必须逐条列出，否则「不阻断」就变成了「没人看」。
GLOBAL_REVIEW_DISCLOSURE_KINDS: Final = (
    'name_distinctiveness', 'address_context', 'fate',
)

#: 复核项对**交付**的效果。两个取值与 `InboxDeliveryEffect` 同名同义，不另造第三套词。
GLOBAL_REVIEW_EFFECTS: Final = ('blocking', 'disclosure')


def review_effect_of(kind: str) -> str:
    """一个复核项该不该拦下游。**全仓唯一的分类落点。**

    ⚠ **未登记的 kind 当场炸，不给默认值。** 这张表的失败方向是「新加一类高影响
    事实被静默放行」，所以它必须是白名单 + 关闭式：加 kind 的人被强制选一边。
    ⚠ legacy `known_by`（只为读存量 v1 review 保留）保守按 `blocking` 读：
    读不准的历史项宁可挡住，也不替它开门。
    """
    if kind not in GLOBAL_REVIEW_KINDS:
        raise ValueError(
            f'未登记的 stage2 复核项 kind：{kind!r}。'
            f'加一类必须同时在 GLOBAL_REVIEW_KINDS 与（若只披露）'
            f'GLOBAL_REVIEW_DISCLOSURE_KINDS 里登记，没有默认值')
    return 'disclosure' if kind in GLOBAL_REVIEW_DISCLOSURE_KINDS else 'blocking'


assert set(GLOBAL_REVIEW_DISCLOSURE_KINDS) <= set(GLOBAL_REVIEW_KINDS)
assert len(set(GLOBAL_REVIEW_KINDS)) == len(GLOBAL_REVIEW_KINDS)

#: TODO-25 的唯一机器契约。它描述的是显示投影，不是访问控制：服务端响应仍含完整数据。
#: 后端发布器、前端 adapter、Screen 与 mock 都消费这一个结构；隐藏路径和承诺不再散落成文案。
SPOILER_DISCLOSURE: Final = {
    'mode': 'display_only',
    'security_boundary': False,
    'server_sends_full_data': True,
    'hidden_paths': (
        'characters[].fate', 'secrets', 'arc.climax', 'arc.ending',
    ),
    'known_leakage_surfaces': (
        'characters[].role', 'relations[].valid_from_chapter',
        'world_rules[].versions[].desc', 'network_response_and_devtools',
    ),
    'consumers': (
        'web/src/api/spoilers.ts', 'web/src/screens/global/GlobalScreen.tsx',
        'web/src/api/mock.ts',
    ),
    'notice': (
        '这是显示辅助，不是安全边界；服务端仍下发完整数据。关闭时只隐藏 fate、secrets、'
        'climax、ending 四处核心字段，role、关系时点、规则正文与网络响应仍可能剧透。'
    ),
}

#: ⚠ 这里曾有 `PARTIAL_MASK_NOTE = SPOILER_DISCLOSURE['notice']`，自陈是「旧名字的源码
#: 兼容别名」。全仓**没有任何代码消费它**（四处提及全在注释里），它唯一的存在理由是
#: `novel/api/enums.py` 要求本模块每个公开大写名都被三张桶表之一归类 ⇒ 一个只为了通过
#: 归类检查而活着的别名。⇒ 与 `enums.py` 那行 `_CONSUMED` 登记同批删除。
#: 要那句话，读 `SPOILER_DISCLOSURE['notice']`。

#: 一次调用当时的缓存口径，记进付费产物 `_provenance`（如 stage2 的 `repairs[]`）。
#: ⚠ **定向重生成必须配 `refresh-write`**（`R-3.11`）：它的 prompt 与整跑时那一批**逐字相同**，
#: 缓存必然命中，于是一个字都没重新生成而它照样打印「已重生成」。
#: ⚠⚠ **`disabled` 没有 CLI 入口，这是有意的，别当死取值删掉。** 它与那批「装置删了只剩
#: 取值」的枚举**不同型**：装置还在（`ResponseCache` 的逐读写开关），缺的只是一个 flag，
#: 而它是冻结需求 `R-12.4`「关缓存是同时关读写」的**唯一表达**——删了那条需求就没有守卫。
#: 也不要顺手补一个 `--no-cache`：`--refresh` 严格更优（重采样且仍留账），
#: 多一个入口只会让人误跑一次「花了钱什么都没留下」的重跑。
CACHE_POLICY: Final = ('default', 'refresh-write', 'disabled')


# ─────────────────────────────────────────────────────────────────────────────
# 五、计划与篇幅（stage4）
# ─────────────────────────────────────────────────────────────────────────────

#: Stage4 每章预算之和 / 源 span 字数的唯一现行区间。**无第二层宽限。**
#: `output_chapters[].op` 不属于当前契约，因此这里也不保留已删除操作的取值面。
#:
#: 历史上该区间曾与源 beat 全覆盖、源序执行共同锁死等密度重述；现有场景编译器已退休
#: 源 beat 写作骨架，但总章幅仍需受控，因此继续保留这一章级预算带。
#:
#: ⚠ **`0.80~1.20` 不是一个新发明的数**：它逐字等于 `DRAFT_BAND`，而那个数的辩护词
#: （盘上 31 章的两端极值 **0.814 / 1.206**）辩护的正是同一件事——「一章的节奏本来就会让
#: 某几章胖一点、某几章瘦一点」。本带此前比执行带还紧，等于**规划层不许计划出执行层
#: 本来就允许的波动**。
#:
#: ⚠⚠ **放宽本带的前提是同批补上书级闸**：总量是对用户的承诺，此前它是被本带
#: 「逐章 ±10% ⇒ 全书必然 ±10%」顺带兜住的，本带一松那条蕴含就断了。
#: ⇒ 新增 `plan_book_band`（HARD，`R-5.3c`）：全书各章预算之和 / 全书 span 合计 ∈ `BOOK_BAND`。
#: **逐章放宽而全书收紧**与 `DRAFT_BAND` / `BOOK_BAND` 那一对**同构**，
#: 那次裁决（2026-08-17）的理由逐字适用：逐章的正负偏差在书级相互抵消。
#: ⚠ 此前唯一的书级守卫是 stage5 的 `book_length`（INFO）——那是**钱花完才响**。
#:
#: ⚠ **带取对称是刻意的**：实测「压缩会让成稿更像原文」，所以详略应当是不对称的，
#: 但**不对称由 prompt 与 `delivery` 引导，不由一个没标定过的常量发明**（`判据 15`）。
#: 更宽的不对称带（如 0.65~1.35）要等读数显示计划真的在贴 0.80/1.20 的边界。
PLAN_BUDGET_BAND: Final = (0.80, 1.20)

#: Stage4 结构规划的三个闭合轴。它们描述的是叙事架构，不是文风：同一组事实可以被
#: 放在冷开场、升级或兑现位置，可以作为完整场景或压缩交代，并且必须说清与上一单元
#: 的时间关系。Stage5 只执行这些决定，不再自行猜测重排后的衔接。
STRUCTURAL_ROLES: Final = (
    'cold_open', 'setup', 'escalation', 'turn', 'payoff', 'bridge',
)
DELIVERY_MODES: Final = ('scene', 'summary')
TEMPORAL_LINKS: Final = ('start', 'continue', 'earlier', 'later', 'parallel', 'return')

#: 逐章三个 typed goal 的意图取值面。
#: ⚠ **它 2026-08-22 才从三处手抄收拢到这里**：`contracts/schema/stage4.py` 的
#: `PlanGoal` 与 `CtxGoal` 各写着一份行内 `Literal[...]`，`stage4/prompts.py` 里还有
#: 第三份手抄的 `establish|advance|open|close|withhold|resolve`——`$RECONSTRUCT_MIN`
#: 那条注释记过同型的下场（prompt 与判据各拿一个数）。
GOAL_INTENTS: Final = (
    'establish', 'advance', 'open', 'close', 'withhold', 'resolve',
)

#: ── `stage4.plan` 响应侧的四张前缀枚举 → 产物侧取值 ─────────────────────────
#:
#: **前缀化只在响应侧**：产物侧那四张表一个字不动（下游 + 盘上数据的硬约束），
#: `novel/stages/stage4/measure.py::decode_plan_response` 是唯一映射落点
#: ——与 `RESPONSE_TIME_LAYERS` / `PLACE_MODES` 那次（`extraction-ir-v1`）同一套手法。
#:
#: 【为什么前缀化】这四张表在响应 JSON 里**串行相邻**，而它们语义两两邻近
#: （`cold_open`↔`start`、`bridge`↔`continue`、`payoff`↔`close`、
#: `narrative_role`↔`delivery` 都有 `summary` 味、`intent` 的每一个值都像一种
#: `narrative_role`）⇒ 模型串表，而串出来的值**在另一张表里是合法的**，
#: 于是 schema 闸只能在整份响应收完之后才发现。
#:
#: ⚠⚠ **代价实测过，两次**：
#:   · 交接时 `delivery` 的 `summary` 串进 `narrative_role`；
#:   · 2026-08-22 批 2 的第二次全书实付——`intent='turn'`（那是 `narrative_role` 的值）
#:     ×6 章、`narrative_role='recall'`（那是 `time_layer` 的值，从**下发面**抄来的）。
#:     整条 `LADDER` 走到底、6 次 wire 全被内容闸拒、completion 86 150 **全白花、0 产物**。
#: ir1 那次的读数是**非法 enum 归零**，所以这里照抄的是一个已验证的修法，不是一次猜测。
#:
#: ⚠ 四个前缀字母互不相同，且**与 stage2 的 `T_` / `P_` 也不撞**：同一个模型在两个节点上
#: 见到的前缀空间不该重叠。⚠ `transition` 用 `X_` 不用 `T_` 正是为此
#: （`T_` 已经是叙述层的，而叙述层的值恰好就是这次串进 `narrative_role` 的那个）。
RESPONSE_STRUCTURAL_ROLES: Final = {
    'R_COLD_OPEN': 'cold_open', 'R_SETUP': 'setup', 'R_ESCALATION': 'escalation',
    'R_TURN': 'turn', 'R_PAYOFF': 'payoff', 'R_BRIDGE': 'bridge',
}
RESPONSE_DELIVERY_MODES: Final = {'D_SCENE': 'scene', 'D_SUMMARY': 'summary'}
RESPONSE_TEMPORAL_LINKS: Final = {
    'X_START': 'start', 'X_CONTINUE': 'continue', 'X_EARLIER': 'earlier',
    'X_LATER': 'later', 'X_PARALLEL': 'parallel', 'X_RETURN': 'return',
}
RESPONSE_GOAL_INTENTS: Final = {
    'G_ESTABLISH': 'establish', 'G_ADVANCE': 'advance', 'G_OPEN': 'open',
    'G_CLOSE': 'close', 'G_WITHHOLD': 'withhold', 'G_RESOLVE': 'resolve',
}

#: ── 篇幅的**两级档位**（2026-08-22 批 4）─────────────────────────────────────
#:
#: ⚠⚠ **模型不再给 `budget_chars` 这个整数，它给两个档位，整数由代码算。**
#: 从前模型要同时满足三件算术：逐单元正整数（`budget_int`）、每章之和落在
#: `PLAN_BUDGET_BAND`（`budget_band`）、全书之和落在 `BOOK_BAND`（`plan_book_band`）——
#: 而后两条要把五六十个整数先按章分组求和、再把二十多个章级合计再加一遍。
#: 实测代价：批 2 run1 一次全书实付倒在 `budget_band`（ch18 压到 0.442×），**整份退回**。
#: ⇒ 这三条从「模型的责任」变成 `novel/stages/stage4/measure.py::allocate_budgets`
#: 的**后置条件**，而模型只表达它真正该表达的那件事：**哪一章重、章里哪一场重**。
#:
#: 【为什么是两级而不是一级】它们是**两层不同的问题**：
#: 「哪一章重要」只有章级倍率能表达，「本章里哪里重要」只有场景级分配能表达。
#: 压成一级就等于宣布其中一层不存在。
#:
#: 【两张表的取值面刻意零重词】前缀 `CH_` / `W_` 不重叠，**十个词干也两两不同**
#: （`MAJOR/ABOVE/EVEN/BELOW/MINOR` vs `PEAK/STRONG/MID/QUICK/BRUSH`）——
#: 它们在响应 JSON 里一个在章级、一个在单元级，正是 `RESPONSE_STRUCTURAL_ROLES`
#: 那条注释说的「串行相邻且语义邻近」的形状。共用一个 `HEAVY`/`LIGHT` 词对
#: 就会让串表串出来的值在另一张表里合法，而那要等整份响应收完才发现。
RESPONSE_CHAPTER_WEIGHTS: Final = {
    'CH_MAJOR': 'major', 'CH_ABOVE': 'above', 'CH_EVEN': 'even',
    'CH_BELOW': 'below', 'CH_MINOR': 'minor',
}
CHAPTER_WEIGHTS: Final = ('major', 'above', 'even', 'below', 'minor')

RESPONSE_UNIT_WEIGHTS: Final = {
    'W_PEAK': 'peak', 'W_STRONG': 'strong', 'W_MID': 'mid',
    'W_QUICK': 'quick', 'W_BRUSH': 'brush',
}
UNIT_WEIGHTS: Final = ('peak', 'strong', 'mid', 'quick', 'brush')

#: 章级档位 → **这一章的目标倍率**（预算 ÷ 本章源字数）。
#:
#: ⚠⚠ **这张表的极差（1.18/0.84 = 1.40）故意宽过一个「怎么填都不会越界」的表**，
#: 因为它**不是**最终倍率：`allocate_budgets` 先按它排出章间轻重，再整体归一化到
#: 全书目标（= 全书源字数 × 1.0，`BOOK_BAND` 的中心），最后把冲出 `PLAN_BUDGET_BAND`
#: 的章钳回带内、把残差在还有余量的章之间摊掉。
#: ⇒ 档位表达的是**相对轻重**，绝对倍率由那三步决定。
#: 取一张极差 ≤1.20 的窄表也能免掉钳制那一步（归一化后必然落在带内），但那样
#: **批 2 三轮实付买来的章间轻重会被表本身压掉一半**——用钳制换表宽是这里的取舍。
CHAPTER_WEIGHT_RATIO: Final = {
    'major': 1.18, 'above': 1.08, 'even': 1.00, 'below': 0.92, 'minor': 0.84,
}

#: 单元档位 → **本章内的相对权重**。分配式是 `预算_i ∝ 源字数_i × 权重_i`
#: ⇒ 「预算/源」相对本章均值的倍数恰好等于 `权重_i ÷ 权重的源字数加权均值`。
#:
#: ⚠ 两个端点值沿用既有标定：重头场约为本章均值的 2~3 倍，
#: 快速带过场低于均值一半；`peak=2.6` / `brush=0.38` 是它们的取值面化。
#: ⚠ **它同时决定 `structure_reshaped`（`R-5.19`）那条读数的分辨率**：那条读数问的是
#: 「本章有没有哪一场的相对倍率冲出 `PLAN_BUDGET_BAND`」，而 2.6 与 0.38 都在带外
#: ⇒ 模型一旦真的标出重头场或一带而过的场，那条读数就会成立。这是有意的。
UNIT_WEIGHT_FACTOR: Final = {
    'peak': 2.6, 'strong': 1.7, 'mid': 1.0, 'quick': 0.6, 'brush': 0.38,
}

#: 六张响应表与它们的产物表**逐格对齐**（顺序也对齐——渲染进 prompt 的就是这个顺序）。
_PLAN_RESPONSE_ENUMS: Final = (
    (RESPONSE_STRUCTURAL_ROLES, STRUCTURAL_ROLES, 'narrative_role'),
    (RESPONSE_DELIVERY_MODES, DELIVERY_MODES, 'delivery'),
    (RESPONSE_TEMPORAL_LINKS, TEMPORAL_LINKS, 'transition'),
    (RESPONSE_GOAL_INTENTS, GOAL_INTENTS, 'intent'),
    (RESPONSE_CHAPTER_WEIGHTS, CHAPTER_WEIGHTS, 'chapter.weight'),
    (RESPONSE_UNIT_WEIGHTS, UNIT_WEIGHTS, 'segment.weight'),
)
for _resp, _art, _field in _PLAN_RESPONSE_ENUMS:
    assert tuple(_resp.values()) == tuple(_art), \
        f'{_field} 的响应侧映射与产物侧取值面分家了——decode 会映到一个不存在的产物值'
    assert len(set(_resp)) == len(set(_resp.values())), f'{_field} 的响应侧映射不是双射'

#: ⚠⚠ **这条断言才是前缀化买的那件东西**：任意两个响应侧取值互不相等、
#: **且互不为子串**——串表串出来的值必须在 schema 闸当场死掉，而不是在另一张表里合法。
#: 一起比进来的还有 `TIME_LAYERS`（它的值出现在**下发面**上，`narrative_role='recall'`
#: 就是从那里抄过来的）与 `HINGE_KINDS`。
_PLAN_RESPONSE_KEYS: Final = tuple(
    k for _resp, _a, _f in _PLAN_RESPONSE_ENUMS for k in _resp)
for _a in _PLAN_RESPONSE_KEYS:
    for _b in (*_PLAN_RESPONSE_KEYS, *TIME_LAYERS):
        assert _a == _b or _a not in _b, \
            f'响应侧取值 {_a!r} 是 {_b!r} 的子串——串表就不会在 schema 闸上死掉了'

#: 两张倍率表的键必须与它们的取值面逐格相同——少一格，`allocate_budgets` 会在一份
#: **已经付过钱**的响应上 `KeyError`，而那是最贵的一种崩法。
assert tuple(CHAPTER_WEIGHT_RATIO) == CHAPTER_WEIGHTS
assert tuple(UNIT_WEIGHT_FACTOR) == UNIT_WEIGHTS

#: 「逐字挤占」的下限倍数：一场的预算至少要是它**必须逐字写入的原话**字数的这么多倍。
#: 2.0 的含义是「原话最多占一半」——低于它，剩下的字连交代谁在说、说完什么反应都不够。
#:
#: ⚠ **它只由分布长尾支持，没有独立判断给它当基准**：旧计划实测某本 43 个带原话的单元里
#: 中位占 18%、只有 1 个越过 50%，另一本 40 个里同样 1 个。阈值取在长尾上，
#: 不会把正常场景卷进来。
#: ⚠⚠ **所以它是提示级不是阻断级**（`R-5.13` 标 `INFO`）：用一个没校准的数去挡一次
#: 付费重跑，是判据 15 反对的事。⚠ 与它配套的**度量**（原话占预算的分布）是
#: **无条件报出**的（`R-5.14`）——现状读数比越阈的那几条更值钱，正因为这个阈值没校准过。
VERBATIM_BUDGET_FACTOR: Final = 2.0

#: 计划里的三个目标（章目标 / 开头钩子 / 结尾钩子）不许含的风格套话词。
#: **这 15 个词必须整份插值进 prompt**，不许手抄一个子集——原型就是这么漂的。
STYLE_WORDS: Final = (
    '环境渲染', '渲染氛围', '氛围营造', '感官', '画面感', '白描', '意象', '诗意',
    '唯美', '笔触', '文笔', '深沉', '细腻', '张力', '质感',
)


# ─────────────────────────────────────────────────────────────────────────────
# 六、写作（stage5）
# ─────────────────────────────────────────────────────────────────────────────

#: Stage5 v1 的历史目录名。只用来识别不兼容盘面和读取不可变旧记录；
#: 它们不是现役写作臂，不得重新暴露到 CLI/API。
LEGACY_STAGE5_MODES: Final = ('deep', 'shallow')

#: 发明边界。**保持元组不拍平成一句话**：将来给 forbid 逐项补探针时，探针读的就是这份。
#: ⚠ 这份清单**是给写作节点看的，不是可查依据**。回写侧的覆盖情况见下面的 `INVENT_GUARDED`。
INVENT_ALLOW: Final = ('感官细节', '环境', '动作分解', '内心独白', '对话的语气与节奏')
INVENT_FORBID: Final = ('新事件', '新人物', '新数字', '新道具', '新因果', '新地点', '新时间跳跃')
INVENT_PRINCIPLE: Final = '凡能拿回原文核对真伪的，禁止发明；凡纯属行文表面的，必须发明。'

#: `INVENT_FORBID` 七项各自**回写侧有没有探针**。这张表就是需求 B-01 的机器可读形式。
#: ⚠ **别把「探针报 0 条」读成「约束生效了」**——「没报违规」与「根本没在查」必须能区分。
#: 「新地点」查不了是有实测原因的：地名后缀（府/家/宫/城/街/山）都是**普通名词**，
#: 没有数字那样的硬左锚，实测 23 章报 82 条候选、几乎 100% 假阳。
#:
#: ⚠⚠ **B-01 已裁决（2026-08-10）：五个 `none`/`partial` 一律【不补判据】，直到有 ground truth。**
#: 三条理由，每条都有实测：
#:   ① 本项目已为「在抽象层做语义判定」付过一次学费——gate1 的护栏**自己成了发明源**
#:      （它把原文 0 次的事收进结论，若照办写手会被逼着写一场原文没有的戏），因此整节退役；
#:   ② **唯一 `full` 的那一项自己就零判别力**：`invented_number` 在 3 本里有 2 本被
#:      书级恒等闸门判死（头牌 命中 2/8 vs 源章 2/8；三岁 1/9 vs 源章 **2/9**，源章触发得更多）；
#:   ③ 反哺回路**实测会诱发**自造内容，而当轮判定一条都指不出它。
#: ⇒ 这七项的正确身份是**给人核对的料**（同 `name_surface_survey`），不是关卡。
INVENT_GUARDED: Final = {
    '新数字': 'full',       # 数值探针。⚠ 2/3 本上被恒等闸门判死，见上
    '新人物': 'partial',    # 只抓「本书已有的名字用字 + 称谓后缀」造出来的人，召回有缺口
    '新事件': 'partial',    # 由 verbatim / keep_value / events 间接约束
    '新道具': 'none',
    '新因果': 'none',
    '新地点': 'none',
    '新时间跳跃': 'none',
}

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
# 七、即时机械测量（不是 stage / node / 产物）
# ─────────────────────────────────────────────────────────────────────────────

#: Stage5/6 共用的确定性测量算法显式契约版本。
#:
#: 它不对应 JSON 信封，只回答「这些数字按哪一版算法计算」。测量算法或口径变化
#: 时先升本值；Stage5/6 的 Stage1 consumed 投影把它并入指纹，因此既有下游会
#: 诚实地判 stale，而不是继续拿旧算法的数字冒充当前测量。
STYLE_MEASUREMENT_CONTRACT: Final = 'stage3-measurable-v1'


# ─────────────────────────────────────────────────────────────────────────────
# 七之二、开篇文案（stage6）与人名替换（stage7）
# ─────────────────────────────────────────────────────────────────────────────

#: 文案写法的**取值面**——`Candidate.approach` 那个 `Literal` 读的就是它。
#:
#: ⚠⚠ **它不是「这一次要产哪几条」**，那是 `HOOK_RECIPE`。两者曾经是同一张表
#: （表里三种、一次各产一条），而「各产一条」这个前提**本身就是被裁决掉的东西**：
#: 同一种策略要能采两条样本，取值面就不能再兼任配方。
#: ⚠ 产地**不许 `.get` 兜底**：写歪的 approach 一律显式终态（枚举上的兜底
#: 就是给违约数据发一张静默通行证）。
HOOK_APPROACHES: Final = ('source_architecture', 'payoff_preview', 'scene_punchline')

#: **已退场的写法**：不再下发、响应里出现即违约（`HookResponse` 的配方校验器拦它），
#: 但**仍留在取值面里**。
#:
#: ⚠⚠ 留着它不是心软，是因为**盘上有存量**：「寡妇门前」那份 `stage6/hook.json` 的
#: `candidates[0].approach` 就是 `source_architecture`。把它从 `Literal` 里删掉
#: 等于再来一次形状破坏 + 该书 stage6 层当场拒读，而那三条候选正是退场裁决所依据的
#: **对照臂**——为了让退场看上去干净而删掉判它退场的证据，是把账做没了。
#: ⚠ 它与 `HOOK_SEMANTIC_STATES` 删掉的那两个取值**不是同一件事**：那两个取值
#: 任何宿主都产不出来（死分支），这一个**在盘上真实存在**（历史数据）。
HOOK_RETIRED_APPROACHES: Final = ('source_architecture',)

#: **当前配方**：一次调用产 `len(HOOK_SLOTS)` 条候选，按位置逐条指定写法。
#:
#: ⚠ 它是**序列不是集合**——同一种写法可以出现多次，那正是它与取值面分家的理由。
#: `payoff_preview` ×1 + `scene_punchline` ×2：`source_architecture` 于 2026-08-23
#: 退场（用户裁决，闭卷盲选验收），空出来的那一槽用 `scene_punchline` 再采一样，
#: 而不是缩成两条——2026-08-15 冻结的 A/B 证明三候选最佳 8.75 对单份直出 6.60，
#: 赢在**创作搜索方差**，槽位数不能借着退场偷偷减掉。
#: ⚠ 改这张表会改 `prompts.HOOK_SYS` 的指纹——那是对的：
#: **一个模板 + 一份配方 = 一个 prompt**，配方变了发出去的东西就变了。
HOOK_RECIPE: Final = ('payoff_preview', 'scene_punchline', 'scene_punchline')

#: 当前配方里出现过的写法，按首次出现顺序去重。**下发面就是它**
#: （`HOOK_APPROACH_NOTES` 的键、prompt 的 JSON 形状提示都从这里来）。
HOOK_CURRENT_APPROACHES: Final = tuple(dict.fromkeys(HOOK_RECIPE))

#: **候选的身份**。它是位置，不是策略名。
#:
#: ⚠⚠ 这里曾经没有这张表，候选身份就是 `approach` 本身（`_texts_of` 拿它当标签、
#: `--apply-edit` 拿它选候选、inbox 拿它做证据键）。那在「三种策略各出一条」的前提下
#: 成立，而那个前提**本身就是要改的东西**：同一种策略采两条样本时，
#: 标签立刻重复 ⇒ 判官 aliases、`judged_texts` 键、`reviews[].label` 一起塌。
#: ⇒ 身份与策略必须分开。**这不是新概念**——`--pick N` 从一开始就是位置寻址
#: （`hook.py` 里就是 `art.candidates[pick - 1]`），本表只是把它显式化。
#:
#: ⚠ 与 `HookCandidateLine.index`（1..3）是**同一件事的两种写法**，不是两个身份：
#: `slot_of(index)` 是唯一的换算口，别在别处再写 `f'c{i}'`。
HOOK_SLOTS: Final = ('c1', 'c2', 'c3')

#: Stage6 双盲判官的完整角色面。工作包、语义产物与 executor run 都只能出现这两名；
#: API schema 不得用 ``Literal['A', 'B']`` 另抄一份取值面。
STAGE6_JUDGE_ROLES: Final = ('A', 'B')


def slot_of(index: int) -> str:
    """1 起的候选位置 → 候选身份。**唯一换算口。**

    ⚠ 越界当场炸，不兜底：兜底会让一次算错的位置安静地指到另一条候选上，
    而证据键、判官标签和人工定稿都挂在这个返回值上。
    """
    if not 1 <= index <= len(HOOK_SLOTS):
        raise ValueError(
            f'候选位置 {index} 越界；本层恒 {len(HOOK_SLOTS)} 条候选'
            f'（1~{len(HOOK_SLOTS)}），取值面是 {list(HOOK_SLOTS)}')
    return HOOK_SLOTS[index - 1]

#: 候选在最新一次质量审判中的可选状态。界面与批准路共同消费。
#:
#: ⚠⚠ 这里曾有四个取值，多的两个是 ``clear`` 与 ``contradiction``。它们只由
#: automated PASS/FAIL 产生，而自动放行门随 evaluator profile 于 2026-08-18 整批下线
#: ⇒ **两个都不可达**。留着它们最坏的一面是 ``clear``：界面会为一个永远不会出现的
#: 绿灯保留一条渲染分支，而读的人会以为「只是这次没绿」。
#: 现在只剩两个，而且它们答的是不同的问题：``unreviewed`` = 没有可用的当前审判，
#: ``uncertain`` = 判过、有证据、但本仓不替人盖章。
HOOK_SEMANTIC_STATES: Final = ('uncertain', 'unreviewed')

#: **在编写法**各自的一句说明。**键必须与 `HOOK_CURRENT_APPROACHES` 逐字相等**
#: （下面有 import 期断言）。它逐字进 `HOOK_SYS`——原型把这几句写在产地的一个 dict 里，
#: 而枚举表在这里，于是「有哪几种写法」与「每种是什么意思」分了两个落点。合成一份之后，
#: 加一种写法而忘了写说明，`fill()` 当场抛 `KeyError`（缺值不渲染成空串）。
#: ⚠ **退场的写法不再有说明，键里也不留**：说明是发给模型的东西，退场就是不再发它。
#: 想知道 `source_architecture` 当初是什么意思，去 `HOOK_RETIRED_APPROACHES` 的注释
#: 与 §十 的 A/B 记录里查——那是**史料**，不是下发面。
#: 这几种写法来自 2026-08-15 的三书 A/B：最佳实验候选对当前定稿的匿名主编分
#: 8.78 vs 4.88，且 3/3 本获胜。它们分别允许用后期兑现制造期待、以及强制落到
#: 可视可听的场面；不再按「泄底多少」切策略。
HOOK_APPROACH_NOTES: Final[dict[str, str]] = {
    'payoff_preview':  '兑现预告 —— 从全书挑最有吸引力的后期兑现或反转作预告，'
                       '制造「怎么走到这一步」的阅读欲',
    'scene_punchline': '场面重击 —— 用一个可视、可听的具体场面或对白砸出核心关系，'
                       '以人物反应、危险或笑点收尾',
}

#: 收尾形态的四类。**唯一落点**——分类器（`novel/core/bands.py::ending_shape`）、
#: 判据措辞、报告共用这一份。
#: 两边都直接从原作文案段/候选字节量形态，不经过已经退役的定性描述。
ENDING_SHAPES: Final = ('引号台词', '问句', '省略号', '陈述')

#: 段间分隔的两档。**离散性质，按字节判**（见 `novel/core/bands.py::sep_band_of_text`
#: 里那段 off-by-one 的账）。
SEP_BANDS: Final = ('空行分段', '单换行')

assert tuple(HOOK_APPROACH_NOTES) == HOOK_CURRENT_APPROACHES, \
    'HOOK_APPROACH_NOTES 的键与当前配方分家了——加一种写法就要写一句说明，退场的要一起撤'
assert len(HOOK_RECIPE) == len(HOOK_SLOTS), \
    '配方长度必须等于槽位数——一条配方对一个位置身份，少一条就有槽位没人写'
assert set(HOOK_RECIPE) <= set(HOOK_APPROACHES), '配方里出现了取值面之外的写法'
assert not (set(HOOK_RECIPE) & set(HOOK_RETIRED_APPROACHES)), \
    '退场的写法又被排进配方了——要么它没退场，要么这条配方写错了'
assert set(HOOK_RETIRED_APPROACHES) <= set(HOOK_APPROACHES), \
    '退场清单必须是取值面的子集：退场是「不再下发」，不是「从取值面消失」'

#: 残留候选的置信度。**`high` ⟺ 命中处带了一个结构信号**，今天有三个来源：
#: ① 称呼后缀（`RESIDUAL_HIGH_STEMS`）· ② 被标点拆开的完整旧名（`canonical_map` 那一支）·
#: ③ 旧姓后紧跟成串的中断号（`RESIDUAL_INTERRUPT_MARKS`）。
#: **三个都是「这段字节的形状不对」，不是「这个词看起来像名字」**——后者是词表，本层不用。
#: ⚠ 只看正式产物会把取值面定成**只有 `low`**：3 份正式产物 19 条残留全是 `low`，
#: 而 8 份 `_rounds/` 归档里有 **35 条 `high`**——`high` 的那些早被人勾选修掉了。
#: **凡是有「人修完再落盘」这条路的层，归档必须一起反推。**
#: ⚠ 覆盖率 `ratio` 与它打架时**以本字段为准**：`ratio` 有自我压低的反馈回路
#: （漏掉的形就是该姓的主用法时，分母里装的主要就是分子），实测 `沈` 40% / `孙` 60%
#: 落在「基本是普通词」那一侧而两个都是真漏网；`confidence` 实测 6/6 全真。
RESIDUAL_CONFIDENCE: Final = ('high', 'low')

#: `stage7.rename --adopt-residual` / 对应 HTTP 请求唯一允许的置信度。
#: 这是**处置动作**的闭合输入域，不等于探针本身会产出的完整
#: `RESIDUAL_CONFIDENCE`：low 仍须展示给人，但绝不能由批量采纳入口写回。
RESIDUAL_ADOPTION_CONFIDENCE: Final = ('high',)

#: 判成 `high` 的称呼后缀：**「旧姓 + 这些字」= 高置信的漏网候选**。
#: 长后缀必须排在短后缀前面（例如「公子」先于单字），扫描口统一按长度倒排，
#: 不能退回到只看紧邻一个字，否则「温公子」会被截成「温公」。
#: ⚠ **绝不自动替换**：「不允**许家**人」里就含「许家」，自动换会写出「不允苏家人」。
#: 它只影响候选的**排序与标记**，处置永远是人勾选。
RESIDUAL_HIGH_STEMS: Final = (
    '公子', '少主', '仙子', '姑娘', '道友', '前辈',
    '兄', '姐', '妹', '弟', '家', '府', '宗', '氏', '宅', '门',
)

#: 判成 `high` 的**中断号**：旧姓后面紧跟成串的这些字符 ⇒ 一句被打断的称呼。
#: 实测形状：某本 ch18 的「【每日一问，**迟……**等等，她什么时候元」——
#: 那是原作真姓被叫到一半，`extra_subs` 最后是人**读正文**才补上的
#: （`迟……→苏……`），而探针当时确实报了它，只是埋在同书 16 条 low 噪声里
#: （迟钝／桂花糕／桃花眼／落花流水）。
#:
#: ⚠⚠ **只认成串的中断号，绝不认终止标点。** 同一本书 ch8 有「高岭之**花。**」——
#: `花` 是本书的旧姓、后面跟着句号，而那是一句话说完了，不是被打断。
#: 把终止标点也算进来，这条信号立刻从「6/6 全真」掉进噪声里，
#: 而 `high` 是 `RESIDUAL_ADOPTION_CONFIDENCE` 的**可采纳面**——误判会被人一键换进成书。
#: ⇒ 判据是**成串**（`{2,}`，见 `novel/stages/stage7/subs.py::residual_scan`），
#: 单个字符一律不算。
#:
#: ⚠ **证据 n=1**（一本书一处）。按 `docs/LESSONS.md` 第 17 条，样本量写在这里，
#: 别让下一个人把它读成与称呼后缀那条（6/6）同等强度的信号。
RESIDUAL_INTERRUPT_MARKS: Final = ('…', '—')

#: 中断号成串的最短长度。`……` / `——` 在中文里本就是**成对**出现的全角形，
#: 单个 `…` 更多是排版噪声。
RESIDUAL_INTERRUPT_RUN: Final = 2

#: 残留候选报出的命中片段长度（**恒 3 字**）与上下文半径（前后各 12 字）。
#: ⚠ 上下文**只影响可读性，不是门**——探针报的是位置，人要判「这是不是一个人名」
#: 靠的就是这几个字。
RESIDUAL_HIT_CHARS: Final = 3
RESIDUAL_CONTEXT_CHARS: Final = 12

#: 能进**替换面**的称呼最短几个字。**中文没有词边界，这是一条硬边界，不是保守。**
#: 实测某本 `char_011` 唯一的称呼是**单字「姐」**，而成稿里「姐姐」有 4 处、「姐」共 18 次。
#: 单字进了替换面，「姐姐」当场被切成「新X姐」。
#: ⚠ 它与「保留项进 alternation 当恒等条目」是**两道独立的防线，缺一不可**：
#: 屏蔽只挡得住 registry 收了的长称呼（「姐姐」在表里），挡不住「大姐」「姐妹」这种没人收的词。
#: ⚠ 它管的是**替换面**，不管**屏蔽面**——`kept` 里有单字条目是对的。
MIN_SURFACE: Final = 2

#: 人物表能推导、但 registry 未必逐字收录的「名字尾字 + 关系称呼」后缀。
#: 例如人物表只登记「谢箬→苏瑶」，正文却另写「箬姐姐」；旧姓残留探针看不到
#: 这个形（它不含旧姓），完整 surface 替换也按构造够不着。
#: ⚠ 这张表只喂给 **INFO 候选探针**，绝不自动替换。中文没有词边界，自动采纳会
#: 把普通词误当昵称；短到单字的「姐/哥」也刻意不收，避免噪声压倒人工审阅面。
DERIVED_ALIAS_SUFFIXES: Final = (
    '姐姐', '哥哥', '妹妹', '弟弟', '姊姊', '兄长', '阿姐', '阿哥', '姑娘', '公子',
)

#: 一条待办此刻是否还等人动作。`reviewed_retained` 表示 typed 决策已存在，
#: 记录因审计/披露而保留；它与“是否阻塞交付”是正交轴。
INBOX_ATTENTION: Final = ('pending', 'reviewed_retained')
INBOX_DELIVERY_EFFECT: Final = ('blocking', 'disclosure')

#: 成书的两个分隔常量。**章分隔＝裸阿拉伯数字单独成行**，不写「第 N 章」。
#: ⚠ 这是**产品形态，不是可调参数**——写成开关就是又一个没有守卫的自由变量。
BOOK_CHAPTER_SEP: Final = '{}'
BOOK_BLOCK_SEP: Final = '\n\n'

#: 恒等基线的占位符模板。选 `⟦⟧`（U+27E6/27E7）是因为中文小说正文不会用这对括号，
#: 而基线跑之前会**断言它一次都没在文本里出现过**——占位符与正文撞车会让往返
#: 静默变成恒等，**那正是这条基线要排除的假绿灯**。
IDENTITY_PLACEHOLDER: Final = '⟦{}⟧'

#: 恒等基线**验不了**的三样（`R-10.7b`）。它是常量而不是现算，**但必须跟结论存在一起**
#: ——分开放，下一个人就会把绿灯读成「替换是安全的」。
IDENTITY_BLIND: Final = (
    '正向替换对【真名】安不安全——占位符天然无歧义，真名不是',
    '漏网覆盖率——人物表够不到的角色，这一层结构上够不着',
    '新名好不好——没有确定性判据，现役闸门是人定稿',
)


# ─────────────────────────────────────────────────────────────────────────────
# 八、0 成本入口的命名体系
# ─────────────────────────────────────────────────────────────────────────────

#: 每一层确定性的部分都必须能 0 成本重算，否则修自己的 bug 要付费（需求 Q-3）。
#: **命名体系本身是资产**：同一个语义在不同节点上必须叫同一个名字。
#: ⚠ 原型里这条**三个节点行为不一致**（一个有提升路、一个没有、一个把问题稿双双排除掉，
#: 代码直接叫你重付一次钱）。新骨架**必须一致**。
#:
#: 【这张表曾经不全，而它自称是唯一落点】2026-08-11 拿**真实抓包**
#: （`docs/BACKEND_CONTRACT.md` §八冻结的 `enums.json`，36 条 `actions`）对了一遍：
#: 现役后端真正当 0 成本用的 flag 有 **13 个**，本表当时只有 **9 个**，交集 8 个。
#: ⚠ 差的那几个不是笔误，**漏一个的后果是反的**：`is_paid` 拿本表当白名单，
#: 而「未知 flag 一律按付费算」（`Q-10`：不知道要倒向贵的那一边）——
#: 于是一个漏登记的 0 成本动作会在界面上被画成橙色、弹二次确认，
#: 而**它根本不花钱**。表不全 ⇒ 系统在对用户说假话。
FREE_ENTRIES: Final = {
    '--dry-run': '只打印将要发出去的东西，不调用、不写盘',
    '--check-only': '按当前判据复检存量产物，纯读不写盘；报 FAIL 退出码非零',
    '--recheck': '按当前判据重判存量产物并写回指标',
    '--revalidate': '按当前判据对 .INVALID 稿做 0 LLM 同轮重判，必填 actor/reason 并留不可变审计，合格才提升',
    '--renormalize': '按当前契约重算代码派生字段与原文定位',
    '--names-only': '只产逐章可用名册（stage4），不产计划',
    '--identity': '恒等基线：拿原作自己的东西跑整套判据，看判据有没有把合法情况排除在外',
    '--apply-edit': '把人工改过的内容落成新的一轮',
    '--preview-edit': '只读预检 Stage5 人工稿，不调用模型、不写盘',
    '--review-decisions': '应用带 actor、理由和 subject hash 乐观锁的 stage2 人工复核决策',
    # ⚠⚠ **它是 2026-08-15 补的，而漏它的后果正是本表开头那条**：
    #   `--wontfix` 原来只挂在 `stage5.review` 上，而那是个 `paid=False` 节点
    #   ⇒ 不进本表也判不出付费，注释因此写着「它是参数不是入口 ⇒ 不进 FREE_ENTRIES」。
    #   语义豁免闭环把它同时挂上了 **`stage6.hook`**（`paid=True`）之后，
    #   那句推理就不成立了：`is_paid` 拿本表当白名单、未知一律按付费算
    #   ⇒ 一次**纯记账、一个请求都不发**的动作会当场要 `--yes` 二次确认。
    #   实测就是这么发现的（真跑 CLI 被拦下）。**表不全 ⇒ 系统在对用户说假话。**
    '--wontfix': '把一条「这条我不修」记进 <stage>/wontfix.json，纯记账、不调用',
    '--retain-pretrim': ('人工确认保留当前 trim audit 对应的原稿；只写 hash-bound resolution，'
                         '0 次 LLM 调用（不是 0 人工成本）'),
    '--retain-pretrim-batch': ('按显式 manifest 一次销账多章 trim；整批预检、按章幂等写'
                               'hash-bound resolution，0 次 LLM 调用'),
    '--emit-edit-template': '向 stdout 输出带当前 schema version 的 stage7 人工编辑空壳，不写盘',
    '--validate-edit': '按 stage7 apply-edit 的同一构建与判定链预检人工编辑，不写盘',
    '--emit-review-template': ('向 stdout 输出绑定当前 stage2 全局复核项 hash/evidence 的'
                               '安全 UNVER 决策骨架，不写盘'),
    '--emit-review-workpack': ('向 stdout 输出 Stage2 Global 逐项证据包，或 Blueprint '
                                '异常优先全章包；只辅助人工核验，不自动做决定'),
    '--adopt-residual': '显式采纳可安全应用的 high residual 建议并重放 stage7 确定性链',
    # ⚠ 它**不是**通用 response-repair 的一种：那条路只收 parse/require/schema
    #   （`repair_eligible=True`）。Stage4 的 validator 拒稿明确不在通用修复面
    #   （`eligibility_reason='validator 不属于通用 JSON/形状修复面'`），在此之前
    #   它**没有任何恢复入口** ⇒ 一次有效付费的整书候选只能整本重来。
    '--adopt-rejected': ('按当前完整四道闸 0 次调用重判一份封存的 Stage4A 拒稿；'
                         '只消费隔离区里的候选与既有成本账，不发新请求'),
    '--apply-binding': ('把完整人工 EvidenceBinding 按与 Stage4B 相同的 schema、'
                        '编译和 HARD 闸写回；0 次模型调用，也允许首次 B 无正式计划时恢复'),
    '--emit-prepared-recovery-workpack': (
        '向 stdout 输出一份绑定旧 Stage5 prepared checkpoint、当前代码与当前上游的'
        '只读恢复工作包；不调用模型、不写产物'),
    '--recover-prepared': (
        '把旧 Stage5 run 中全部尚未收口的 prepared checkpoint 按当前代码重新验证并'
        '提交为 canonical；0 次模型调用，必须带 hash-bound 人工凭据'),
}

#: 挂在**付费**动作上的 flag。它们不是 0 成本入口，登记在这里免得被当成漏项。
PAID_FLAGS: Final = {
    '--revise': 'stage5 的修订式反哺——把可反哺的结论连同上一轮成稿发回写手',
    # ⚠ 原型 `FREE_FLAGS['n2_global']` 那一行的注释明写着
    #   「`--purge-role` 是一次真实调用，**不在这里**」——它是语义判官那条臂
    #   （`ROLE_SPLIT_SYS`，2008 字），`role`/`fate` 双向重划分。
    # ⚠ 它是判据 `role_leaks_fate`（盘上 2/3 本命中）**唯一的修复路**，
    #   而那条路要再花一次钱 ⇒ 界面必须把它画成付费色。
    # ⚠ 它**不进 `NodeSpec.paid_flags`**：那一列的定义是「能把一个 **0 成本节点**
    #   变成付费的 flag」，而 `stage2.global` 默认路径本来就付费 ⇒ 那会是一格
    #   永远不起作用的声明（`is_paid` 早在 `spec.paid` 那一步就判完了）。
    #   同 `stage5.write` 的 `--revise`，理由见 `NODES` 里那一格上方的注释。
    '--purge-role': 'stage2 的语义判官臂——把 role/fate 双向重划分一遍，它要再调一次模型',
    '--resample': 'stage2 全书响应被闸门拦下后，绕过同参数问题缓存重新采样整份响应',
    '--replan': ('Stage4B 返回 infeasible 或人工结构审批 request_replan 后，'
                 '仅在当前正式 Stage4A 策略审阅明确 request_replan 后，'
                 '绕过同参数缓存重新采样整书策略与各章；不会自动串行 Stage4B'),
    '--repair-decision': ('按当前正式 Stage4A 审阅决定定向返修被 request_repair 的章节；'
                          '目标与反馈由 hash-bound 决定派生，不能在命令行另选章节'),
    '--patch': 'stage2 逐章蓝图 extraction IR 族的字段级修复——逐条失败各调一次模型，'
               '量级是条不是章',
}

#: 重算时**只对本次新引入的错误入终态** —— 旧违约照报但不挡路。
#: 否则一条不相干的旧问题会把重算这条路永久堵死。
REVALIDATE_ONLY_NEW_ERRORS: Final = True

#: Stage5 人工改稿预览后的提交语义。``formal`` 只允许没有新增 HARD 的候选；
#: ``working_draft`` 允许把一般新增 HARD 明示落为新的 canonical 人工轮，随后由
#: stale Review / closure / 下游闸阻断；段落预算 HARD 在两种模式下都不得正式化。
#: 这是请求载体、CLI 与运行时共用的闭集，不能在三处各抄一份字符串。
STAGE5_EDIT_CONFIRMATIONS: Final = ('formal', 'working_draft')

#: **局部改写**一份付费产物的那几条 0 成本路。`_provenance.partial_rewrite` 的取值面。
#:
#: 【为什么是一个带值的键，不是三个布尔】这三条干的是同一件事——改掉产物的一部分，
#: 而 LLM 写的那些字段仍出自当初那次付费产出。区别只在「哪条路」，**那是一个值不是三个键**。
#: `--recheck` / `--renormalize` 通常用同一个局部改写字段记录来源
#: ——同一件事的第三个落点（`Q-3`：同一个语义必须叫同一个名字）。见 `_common.md` §5.1b。
#: ⚠ Stage5 的 `--recheck` 是明确例外：它保留 canonical 正文、冻结任务书与 writing
#: provenance，只刷新同一正文的 v2 派生字段并对账 trim。这个取值表按 flag 索引，不能
#: 表达节点维度；保留 `--recheck` 是为了其他层，不授权 Stage5 改 provenance。
#:
#: ⚠ 它**必须 ⊆ `FREE_ENTRIES`**（由 `tests/test_invariants.py` 钉着）：
#: 一条会花钱的路不叫「局部改写」，那是一次新的产出，该记一份新的来源账。
#: ⚠ **`--revalidate` 不在这里**：提升路改的是**状态**（`.INVALID` → 正式稿），
#: 内容一个字没动，它有自己的键 `revalidated`。两件事。
#:
#: ⚠ **`--identity` 只对 stage7 成立**：stage4 / stage6 的 `--identity` 只打印
#: 当前恒等读数，**不写盘**。
#: 同一个 flag 在两层行为不同，
#: 这条已经登记在 `WRITE_EFFECTS` 上方那段注释里——本表按 flag 索引，
#: 而它收的是「**这条路要是写了盘，那份产物就是混版的**」，对两层都不说假话。
#: ⚠ **定稿节点刻意不在这里**：定稿一个由代码算出来的字段都没改，
#: 它只是盖了个章。标 `partial_rewrite` 会说假话。
#: `--apply-edit` 不是在每一层都代表混版：stage5 的整章人工稿会铸一份
#: `origin=human` 的新来源账；stage7 只改 `extra_subs` / 决策 patch，模型生成的
#: `characters` 仍来自旧调用，所以必须允许它如实标成局部改写。
PARTIAL_REWRITE_FLAGS: Final = (
    '--recheck', '--renormalize', '--identity', '--apply-edit',
    '--adopt-residual',
)

#: 带值的 flag：`--apply-edit <载荷文件>`。值说的是「这个值是什么」。
#:
#: 【它与前三张表是**正交**的两个轴】那三张答的是「贵不贵」，本表答的是「带不带值」——
#: 所以 `--apply-edit` 同时出现在 `FREE_ENTRIES` 与本表里是**对的**，
#: 不许把它加进 `test_the_three_flag_tables_are_disjoint` 那条不交断言。
#:
#: ⚠ 不带值的 flag 传了值、带值的 flag 没传值，都是 argv 拼错——
#: 而拼错的那一条**会真的被执行**。所以取值面要有落点，由 `build_argv` 当场拒。
#: `preview` 给必填项渲染的占位符前缀。**它不是一个合法值**——
#: `validate_run` 见到以它开头的值就当场拒，所以「照抄 preview 不改就跑」
#: 得到的是一句「把 `待填·actor` 换成真身份」，而不是一条把占位符
#: 当自报身份写进产物的假账（人工凭据是**问责**，不是形状）。
#: ⚠ 用中点不用冒号：冒号在 Windows 上会与盘符形态撞车。
REQUIRED_PLACEHOLDER_PREFIX: Final = '待填·'

VALUED_FLAGS: Final = {
    # ⚠ 「相对**书目录**」这五个字是承重的：各节点相对路径都按当前书目录解释。
    #   stage4/6/7 的绝对路径必须仍在书内；Stage5 还要接受服务进程放在
    #   `<base>/_service/edits/` 的可信绝对上传路径（R-11.3），所以绝对路径边界
    #   由具体节点说明。此前四份实现按进程 cwd 解释而报错文案说书目录，
    #   两个口径分家（skill 现场记录 FN-016）。这一行是人唯一会读到的说明。
    '--apply-edit': ('人工改好的候选稿路径（必填；相对路径相对**书目录**；'
                     '绝对路径由节点校验，Stage5 额外接受服务上传的 _service/edits 路径）'),
    '--preview-edit': ('只读预检 Stage5 人工整章稿；与 --apply-edit 共用归一、判据与'
                       'preview subject 算法；相对路径相对书目录，不写产物'),
    '--response-repair': '结构化拒绝响应恢复模式；当前只接受 `agent`。'
                         '可修复响应先隔离并停给外层受托 agent，同闸复验失败才继续采样',
    '--workpack-sha256': ('人工批准时看到的紧凑工作包完整 SHA-256；服务端在任何'
                          '产物写入前按当前盘面重算并拒绝过期提交'),
    '--adopt-rejected': ('要重判并采用的拒绝响应 attempt_id（16 位小写十六进制）；'
                         '人只能编辑隔离区里那份固定的 candidate.json，'
                         '本命令封存 actor/reason 与新旧闸指纹后再走同一套闸'),
    '--repair-decision': ('当前 Stage4A 正式审阅决定的完整 SHA-256；'
                          '代码只返修该决定中 request_repair 的章节并复用其余已接受单元'),
    '--apply-binding': ('完整人工 Stage4 EvidenceBinding 路径（必填；相对路径相对书目录，'
                        '也接受书目录内的绝对路径）'),
    '--emit-prepared-recovery-workpack': (
        '要审计的旧 Stage5 source run_id；输出绑定当前 target run/code 的只读工作包'),
    '--recover-prepared': (
        '要零调用收口的旧 Stage5 source run_id；范围固定为其中全部 unresolved prepared'),
    '--validate-edit': '要预检的 stage7 人工编辑路径（与 --apply-edit 同路径规则，不写盘）',
    '--retain-pretrim-batch': ('批量销账 manifest 路径（必填；相对路径相对书目录，'
                               '也接受书目录内的绝对路径）；actor/reason 在 manifest 内'),
    '--apply-edit-plan': '审校台生成的 stage0 编辑计划路径（正文 + 剔除账 + 乐观锁）',
    '--review-decisions': 'stage2/global_review 的人工决策集（相对当前书目录）',
    # ⚠ 它只裁剪**只读输出面**，不裁剪评测面本身：`global_review.json` 仍枚举全部
    #   145 类主体，`current` 仍要求集合完全一致。默认 `blocking` 是为了把人的注意力
    #   放在真能挡住成书的那 95 项上；`all` 一格不少地交出整份，供审计与终审包使用。
    '--review-scope': ('只读复核输出的范围：`blocking`（默认，只出能挡 Stage4 的项）'
                       '或 `all`（整份评测面）。它不改评测面，也不改任何判定'),
    '--apply-candidate-plan': '审校台生成的 stage0 候选裁决（采纳 / 否掉整版 + 乐观锁）',
    '--actor': '人工决定的自报身份（不是认证身份）',
    '--reason': '人工决定的非空理由',
    '--expected-revision-id': ('Stage0 人工批准乐观锁定的完整 revision SHA-256；'
                               '必须取自本次实际审阅的 current/workpack'),
    '--subject-sha256': '人工决定乐观锁定的完整内容 SHA-256',
    '--current-chapter-sha256': 'Stage5 人工改稿预览时读到的当前 canonical 完整文件 SHA-256',
    '--preview-subject-sha256': 'Stage5 改稿预览绑定的归一候选、判据身份与结论 SHA-256',
    '--edit-confirmation': 'Stage5 改稿提交语义：formal 或 working_draft',
    '--source': '要摄入的原始小说 txt/md 路径；新书在目录产生前没有 sid',
    # ⚠ `--pick` 只表示 stage6 定稿候选序号；定稿动作本身是独立节点。
    # 不进 `FREE_ENTRIES`（不是独立入口），也不进 `WRITE_EFFECTS`
    # （写的就是本节点 `produces` 中的正式产物）。
    '--pick': '定稿选第几个候选（1 起）',
    # ⚠ 它与 `--pick` 在**带值**这一格同形，其余三格全不一样：
    #   · 它**进 `FREE_ENTRIES`**（2026-08-15 改），而 `--pick` 不进。
    #     原来的理由是「它是 `stage5.review` 的参数，不是 0 成本入口」——
    #     那句话在 `stage5.review`（`paid=False`）上恰好无害，
    #     而语义豁免闭环把它同时挂上 `stage6.hook`（`paid=True`）之后就变成假话：
    #     一次纯记账会被 `is_paid` 判成花钱。⇒ 判准改成「它自己花不花钱」，
    #     而不是「它挂的那个节点花不花钱」。
    #   · 它**要进 `WRITE_EFFECTS`**，而 `--pick` 不进：`--pick` 写的就是本节点
    #     `produces` 里那一份（不登记 ⇒ 落到 `produces`，正确），
    #     而 `--wontfix` 写的是**另一份不在 `produces` 里的产物**
    #     （`<stage>/wontfix.json` 是可选的，进 `produces` 会让每一本书永远差一份）。
    # ⚠ 值的形态 `<章|book>|<判据>[|<证据键>]=<理由>`：前两段是人说得出来的，
    # 证据键由代码从**该层当前的结论池**里补全（含语义结论）；
    # 同一 (章, 判据) 有多条结论时**要人写出第三段**
    # ——原型那条路把它们**一并豁免**了，而「换了个位置的同类问题」不是人做的决定。
    '--wontfix': '这一条我不修：`<章|book>|<判据>[|<证据键>]=<理由>`，理由必填',
    # ⚠⚠ **它只是 canonical 写作的定向章范围**：不给就补写全书所有缺章。
    # 它不携带深浅语义，也不是授权；花钱授权独立落在 `writing/writing_proof`。
    # ⚠ 它**不进 `FREE_ENTRIES`**（那张表登记的是「0 成本**入口**」，而它是付费入口的
    # **参数**——`is_paid` 因此照旧判付费，正确）；也**不进 `WRITE_EFFECTS`**
    # （它写的就是本节点 `produces` 里那一份，只是少写几章 ⇒ 不登记就落到 `produces`，正确）。
    # 两条都与 `--pick` 逐条同形。
    '--chapters': '只写这几个输出章（逗号分隔，如 `2,14`）；不给就补写所有缺的章',
    '--adopt-residual': '只接受 `high`；采纳无冲突、未覆盖且 unit/suggest 完整的残留建议',
}

#: ⭐ 一次跑**会往盘上写什么**。空串 = 不写盘。
#:
#: 【为什么它必须存在】「贵贱」与「写不写」是两个**正交**的维度，而原型只实现了前者：
#: 后者只活在注释里，且**一字不差地出现了 8 次**——「**`FREE_FLAGS` 只管钱不管写**」。
#: 于是界面上一个写着「0 成本」的按钮可能归档旧轮、覆盖正式产物、重拼成书 txt，
#: 而用户看到的只有「0 成本」三个字。一条信息在注释里重复四次以上，
#: 说明它是真的，只是**没有落点**。本表就是它的落点。
#:
#: 【为什么值是一句话不是 `bool`】空串 = 不写盘，与记账那边
#: 「`recorded=False` ⇒ `[????] 未记录`」是同一个纪律：**「不写」与「没人说」必须分得开**。
#:
#: 【默认是「写」，flag 只能把它变成「不写」】每个节点的默认路径都产一份产物
#: ——那是「节点」这个词的定义。所以方向与 `is_paid` 同构（`FREE_ENTRIES` 让付费变免费），
#: 而**没登记的 flag 一律按会写算**（`Q-10` 的同一条：「不知道」要倒向危险的那一边）。
#:
#: ⚠ **`--identity` 刻意不在本表里**：它在不同节点的落盘行为不同，
#: **跨节点不一致的 flag 不许进一张只按 flag 索引的表**。
#: 已经核实的节点特例放在下方 `NODE_WRITE_EFFECTS`；没登记的其余
#: 节点仍由 `write_effect` 保守判为会写。
WRITE_EFFECTS: Final = {
    # ── 只看不动的入口（`FREE_ENTRIES` 自述「不写盘」）──
    '--dry-run': '',
    '--check-only': '',
    # ⚠ **它一个字节都不写**（原型只 `print` 逐章名册），而本表**默认按会写算** ⇒
    #   不登记的后果是**方向反的**：`/api/enums` 上它会被派生成
    #   「写 `stage4/plan_chapters.json`」——**那是系统在对用户说假话**
    #   （`Q-3`）。实测确认过一次才补上这一行。
    '--names-only': '',
    '--emit-edit-template': '',
    '--validate-edit': '',
    '--emit-review-template': '',
    '--emit-review-workpack': '',
    '--emit-prepared-recovery-workpack': '',
    '--preview-edit': '',
    '--adopt-residual': '重写 stage7/name_map.json，清旧审批并退休现有成书',
    # ── 免费但**会写盘**的那些：原型那 8 条注释点名的正是它们 ──
    '--recheck': '按当前判据刷新已有成稿的派生指标，并对账 trim 状态',
    '--revalidate': '必填 actor/reason；0 LLM 同轮重判并留不可变审计，合格的 .INVALID 才提升，旧稿归档进 _rounds/',
    # ⚠ 本表按 flag 索引 ⇒ 这句话必须对**每一个**挂它的节点都成立。2026-08-22 阶段 C
    #   之后 stage0 的历史不再进 `_rounds/`（它在 revision 链上，且不可覆盖），
    #   所以这一格只说得出两边都成立的那部分：落成新的一版、上一版仍可回看。
    '--apply-edit': '把人工稿落成新的一版，上一版仍可回看（stage0 在 revision 链上，其余归档进 _rounds/）',
    '--renormalize': '按当前契约重算代码派生字段与原文定位并写回',
    # ⚠ 它写的是**另一份**产物（`<stage>/wontfix.json`），不是本节点 `produces` 里那一份
    # ⇒ 不登记的后果是**方向反的**：`/api/enums` 会把它派生成「写 `stage5/review_{n}.json`」，
    # 而它一份验收报告都不产。同 `--names-only` 那一格的病，只是方向相反。
    # ⚠ **本表按 flag 索引，所以这句话必须对每一个挂它的节点都成立**（表头那条纪律）：
    #   stage5 挂在 `stage5.review` 上、stage6 挂在 `stage6.hook` 上，两边都是
    #   「写自己那一层的 `wontfix.json`，不产本节点 `produces` 里那一份」⇒ 一句话说得下。
    '--wontfix': '把一条豁免记进 <stage>/wontfix.json（**本节点的正式产物一份都不产**）',
    '--retain-pretrim': ('写 stage5/trim_resolution_chNN.json；旧 resolution 归档，'
                         '不改正文、不采用收缩候选'),
    '--retain-pretrim-batch': ('按 manifest 逐章写 trim_resolution；已销账章幂等跳过，'
                               '不改正文、不采用收缩候选'),
    '--recover-prepared': (
        '写 stage5/chNN.json、target run recovery manifest/两相 replacement journal 与'
        'append-only adoption lineage；不改旧 source sidecar'),
    # ── stage2 那两条 0 成本但**会写盘**的路（F5-1 补）──
    # ⚠ 它们与上面 `--recheck` 那几条是同一类：**免费，但写盘**。
    #   本表存在的全部理由就是这一类（原型那句「`FREE_FLAGS` 只管钱不管写」
    #   一字不差地出现了 8 次，而它没有落点）。
    # ⚠ **`--purge-role` 刻意不在本表里**，那不是漏登记：它写的就是本节点 `produces`
    #   里那一份（`stage2/global_grounding.json`，只改 role/fate 两栏）
    #   ⇒ 不登记即落到 `produces`，**正确**。同 `--pick` 那一格的理由。
}

#: 节点维度的写盘效果覆盖。键是 `(node, flag)`，只收「同一 flag
#: 在不同节点不同形」且已核实的例外；值的口径与 `WRITE_EFFECTS`
#: 一样，空串 = 不写盘。普通情形仍由 `WRITE_EFFECTS` 和 `NodeSpec.produces` 派生。
#:
#: `stage4.plan --identity` 与 Stage6 一样只做内存诊断；Stage7 仍写正式人物表。
NODE_WRITE_EFFECTS: Final = {
    ('stage4.plan', '--identity'): '',
    ('stage6.hook', '--identity'): '',
    ('stage7.rename', '--identity'): artifact_paths.STAGE7_NAME_MAP,
}


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
    # ⚠ **只有「产品裁决过、不许被环境变量静默翻转」的节点才许填非空**：
    #   2026-08-19/20 用户裁决「除语义判官外全链 flash」，而例外必须长成形状
    #   而不是纪律——此前靠每条命令记得带 `DEEPSEEK_MODEL`，忘一次就静默烧成
    #   flash 判官，账本事后才看得出来。
    # ⚠⚠ **现有两个钉死值，而它们的理由不是同一个**，不许互相援引：
    #   · `stage6.semantic` = **判官给不出有区分度的证据**（质量死刑，2026-08-20）。
    #   · `stage4.design` = 小型整书策略 + 逐章私有 session；
    #     `stage4.plan` = 机械 baseline + 仅歧义章私有 session（最多 4 并发）。
    #     它们的模型默认是产品裁决，不得再用「两相各一次全书调用」
    #     解释；B 的新恢复粒度是歧义 unit，零歧义章零模型调用。
    #   两条实测依据都在 `docs/BACKEND_CONTRACT.md` §十。
    model: str = ''


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
#:   候选序号 ⇒ F1 补成 `('--pick',)`；`stage7.approve` 没有候选序号
#:   （一本书只有一份人物表，定稿是给整份盖章），但仍必须提交统一的
#:   `--actor/--reason` 与当前 decision-surface `--subject-sha256` 凭据。
#:   `stage0.approve` 同样给整份剔除报告盖章，没有候选参数；但它**有**人工凭据
#:   （`--actor` / `--reason`，节点函数缺一个就 `TerminalState`）⇒ 那两个是跑法 flag，
#:   而且是 `required_flags`。「没有候选参数」与「没有 flag」是两件事。
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
NODES: Final[dict[str, NodeSpec]] = {
    # Stage0 只有一个 JSON 权威和一个正文投影；运行时报告不落盘。
    'stage0.remove':    NodeSpec('stage0', 'R-0',  False, 'stage0 非正文剔除',
                                  flags=('--source', '--dry-run', '--check-only'),
                                  produces=(artifact_paths.STAGE0_REVISIONS,
                                            artifact_paths.STAGE0_CLEAN_TEXT)),
    'stage0.approve':   NodeSpec('stage0', 'R-0',  False, 'stage0 人工批准',
                                  flags=('--expected-revision-id', '--actor', '--reason'),
                                  required_flags=(
                                      '--expected-revision-id', '--actor', '--reason'),
                                   produces=(artifact_paths.STAGE0_REVISIONS,)),
    'stage0.edit':      NodeSpec('stage0', 'R-0',  False, 'stage0 人工编辑',
                                  flags=('--apply-edit', '--apply-edit-plan',
                                         '--apply-candidate-plan', '--actor', '--reason'),
                                  produces=(artifact_paths.STAGE0_REVISIONS,
                                            artifact_paths.STAGE0_CLEAN_TEXT)),
    'stage1.partition': NodeSpec('stage1', 'R-1',  False, 'stage1 分章',
                                 flags=('--dry-run', '--check-only'),
                                 produces=(artifact_paths.STAGE1_MAIN_TEXT,
                                           artifact_paths.STAGE1_HOOK_COPY)),
    # ⚠ `--apply-edit` 是 TODO-05 补的，而它补的是一个**契约内的空洞**：
    #   `reader_reveal_events` / 知情人 / secret 事实抽错时，此前唯一的路是手改产物
    #   ——没有 schema 管形状，来源账上也留不下「这是人改的」。
    #   当时除 Stage1 外的 stage0/3/4/5/6/7 都已有这条 0 成本人工路；Stage3 后来整层退役。
    'stage2.global':    NodeSpec('stage2', 'R-2',  True,  'stage2 全书级抽取',
                                 flags=('--renormalize', '--purge-role', '--resample',
                                         '--revalidate',
                                         '--apply-edit', '--review-decisions',
                                         '--emit-review-template', '--emit-review-workpack',
                                         '--review-scope',
                                         '--actor', '--reason', '--response-repair'),
                                 produces=(artifact_paths.STAGE2_GLOBAL_GROUNDING,
                                           artifact_paths.STAGE2_GLOBAL_REVIEW,
                                           artifact_paths.STAGE2_BLUEPRINT_CHARACTER_PROJECTION)),
    # ⚠⚠ **`--chapters` 是这一列里最容易漏的那一个**：§5.1 有 **19 条**判据的修法是
    #   `blueprint --chapters`（定向重生成那几章），而它此前只挂在 `stage5.write` 上。
    #   漏了它，那 19 条在 F5-4 注册的那一刻全被 `_validate` ⑧ 拒收。
    'stage2.blueprint': NodeSpec('stage2', 'R-3',  True,  'stage2 逐章抽取',
                                  flags=('--dry-run', '--check-only', '--renormalize',
                                         '--chapters', '--patch',
                                         '--emit-review-workpack',
                                         '--revalidate', '--actor', '--reason',
                                         '--response-repair'),
                                 produces=(artifact_paths.STAGE2_CHAPTER_BLUEPRINTS,)),
    # ⚠⚠ Stage4 两个付费相仍沿用 `deepseek-v4-pro`，但调用拓扑已分开：
    #   A = 一条小型整书策略 + N 条逐章私有 session，代码聚合后跑整书 HARD；
    #   B = 机械 baseline 先消除唯一解，只对真歧义章开私有 session（最多 4 并发），
    #   零歧义章 0-wire 确定性编译（不伪造模型 checkpoint）。当前 B 默认模型的证据面包含历史
    #   whole-book B 样本与现役歧义 unit，不再声称「一次重采就是整本书」。
    #   架构改变本身不静默翻默认；后续须在同一 unit 契约下比较首交率、
    #   HARD、token 与墙钟再裁决。
    'stage4.design':    NodeSpec('stage4', 'R-5',  True,  'stage4 盲场景设计',
                                  flags=('--dry-run', '--replan', '--repair-decision',
                                         '--adopt-rejected',
                                         '--actor', '--reason', '--response-repair'),
                                  produces=(artifact_paths.STAGE4_BLIND_CANDIDATE,),
                                  model='deepseek-v4-pro'),
    'stage4.plan':      NodeSpec('stage4', 'R-5',  True,  'stage4 证据绑定与计划编译',
                                  flags=('--dry-run', '--check-only',
                                         '--revalidate', '--names-only', '--identity',
                                         '--apply-edit', '--apply-binding',
                                         '--actor', '--reason',
                                         '--response-repair'),
                                  produces=(artifact_paths.STAGE4_EVIDENCE_BINDING,
                                            artifact_paths.STAGE4_PLAN_CHAPTERS),
                                  model='deepseek-v4-pro'),
    # ⚠ `flags=()` 在这一格是【终态】不是待定：本节点**本身**就是那个动作
    #   `stage4.context` 本身就是该动作，因此不再另设同义 flag。
    'stage4.context':   NodeSpec('stage4', 'R-6',  False, 'stage4 写作上下文组装',
                                 produces=(artifact_paths.STAGE4_WRITER_CONTEXT,)),
    # ⚠ `--revise` 是 F4 从 `stage5.review` **挪过来**的（用户裁决，见本表下方那段）。
    # ⚠ Stage5 的 canonical 主体只有逐章 JSON。全书 Markdown 投影已退役；
    #   完成度直接比较计划章集与 canonical 章集，避免让展示副本参与状态判断。
    # ⚠ `--revise` 挪过来之后**不再需要 `paid_flags`**：那一列的定义是
    #   「能把一个 **0 成本节点**变成付费的 flag」，而本节点的默认路径本来就付费。
    #   留着它是一格永远不起作用的声明——`is_paid` 早在 `spec.paid` 那一步就判完了。
    'stage5.write':     NodeSpec('stage5', 'R-7',  True,  'stage5 全书闭卷写作',
                                  flags=('--dry-run', '--recheck', '--revalidate',
                                         '--preview-edit', '--apply-edit', '--revise',
                                         '--chapters', '--actor', '--reason',
                                         '--subject-sha256',
                                         '--current-chapter-sha256',
                                         '--preview-subject-sha256',
                                         '--edit-confirmation',
                                         '--emit-prepared-recovery-workpack',
                                         '--recover-prepared', '--response-repair'),
                                 produces=(artifact_paths.STAGE5_CHAPTER,)),
    # ⚠ `--wontfix` 是**参数**不是 0 成本入口（见 `VALUED_FLAGS`），形态同 `--pick`。
    # ⚠⚠ `stage5/wontfix.json` **刻意不进 `produces`**，而它确实是本节点写的：
    #   这一列的消费方是 `state.required_artifacts`（「这一层跑完了盘上该有哪几份」），
    #   而豁免那一份是**可选**的——没人豁免过任何东西时它就不该存在（盘上 3/3 本都没有）。
    #   把它登记进来会让每一本书**永远差一份产物**。⇒ 它的写盘效应登记在 `WRITE_EFFECTS`。
    'stage5.review':    NodeSpec('stage5', 'R-8',  False, 'stage5 全书验收',
                                 flags=('--check-only', '--wontfix', '--retain-pretrim',
                                        '--retain-pretrim-batch',
                                        '--chapters', '--actor', '--reason'),
                                 produces=(artifact_paths.STAGE5_REVIEW,)),
    # ⚠⚠ **`stage5.semantic` 已于 2026-08-17 整节点下线**（用户裁决，见
    #   `docs/BACKEND_CONTRACT.md` §十 2026-08-17 那一行）。裁决依据是实跑证据而非偏好：
    #     · 本项目的重构**不跨章重组**（实测 18/18 章 `from_source_ordinals == [自身]`），
    #       书级剧透那条主风险面在计划形态上几乎不存在；
    #     · 剧透的可查通道在 stage2 已有确定性判据（`event_early_name` /
    #       `present_early_named` / `present_names_late`），且**成书逐字对账的
    #       「上游 info 下游投影」是 0 次调用**——实测它给出了确定结论，
    #       而书级语义判官在同一本书上 18 章里 5 章命题抽取返回 uncertain、
    #       8 条 secret 全部判不动；
    #     · 成本上它占单本实付调用的 54%、输出 token 的 55%。
    #   ⇒ 下线的是**节点**不是能力：`stage6.semantic` 保留（2 次调用、1.7% token，
    #   实测直接纠正过一次定稿）。要恢复请连同 `contracts/schema/stage5.py` 的
    #   `SemanticReview` 一族、`novel/stages/stage5/semantic.py` 与两条判据一起恢复。
    # ⚠ `--revalidate` 是 F 期移植时补的，而它是**新加那道落盘闸门的必然配套**：
    #   本层一次产三条候选，闸门拦的是「一条干净候选都没有」；不给一条 0 成本的提升路，
    #   一次付费产出就会被永久锁死。**加闸门与加提升路必须同批**。
    # ⚠ `--wontfix` 是 TODO-04 补的，形态同 `stage5.review` 那一条（**参数不是入口**）。
    #   它挂在本层的 0 成本复检节点上，理由与 stage5 一样：反查要拿到**当前**结论池，
    #   而结论池是复检算出来的。⚠⚠ 本层的两条语义判据此前**没有任何人工闭环**，
    #   而待人工清单在建议人去跑一个这一层根本没有的 flag（见 `views/inbox.py::_human_how`）。
    'stage6.hook':      NodeSpec('stage6', 'R-9',  True,  'stage6 开篇文案',
                                 flags=('--dry-run', '--check-only', '--identity',
                                        '--recheck', '--revalidate', '--apply-edit',
                                        '--wontfix', '--actor', '--reason',
                                        '--response-repair'),
                                 produces=(artifact_paths.STAGE6_HOOK,)),
    # 付费语义审判是字面探针的 companion（R-9.19/R-9.20）：
    # 不接 `--check-only`，否则一层两个复检节点、HTTP kind 路由产生歧义。
    # ⚠ 它是全链**唯一**的语义审判节点（stage5 那一个已于 2026-08-17 下线）。
    # 保留它的实测依据：2 次调用 / 1.7% 输出 token，而它的匿名质量排序在实跑中
    # 直接纠正过一次定稿——性价比与被下线的那个不在一个量级。
    # ⚠⚠ `model` 钉死 `deepseek-v4-pro`（2026-08-19/20 用户裁决，**两个钉死值中的
    #   第一个**；另一个是 `stage4.plan`，理由完全不同，见那一格）：
    #   flash 判官在有 ground truth 的样本上把唯一与正文矛盾的候选
    #   判 PASS 并排成第一名，证据区间圈成候选全文（等于没有指向）——对「结论必须
    #   绑证据」的判官是死刑。钉在本表 ⇒ `DEEPSEEK_MODEL` 换不掉它。
    'stage6.semantic':  NodeSpec('stage6', 'R-9',  True,  'stage6 文案语义审判',
                                 flags=('--dry-run', '--response-repair'),
                                 produces=(artifact_paths.STAGE6_SEMANTIC,),
                                 model='deepseek-v4-pro'),
    # ⚠ `--pick` 是**参数**不是 0 成本入口（见 `VALUED_FLAGS`）。
    #   本节点的 `flags` 从前是空的，注释写着「候选序号是它们的参数，参数名属 F 期」。
    'stage6.approve':   NodeSpec('stage6', 'R-9',  False, 'stage6 文案定稿',
                                 flags=('--pick', '--actor', '--reason',
                                        '--subject-sha256', '--workpack-sha256'),
                                 required_flags=('--pick', '--actor', '--reason',
                                                 '--subject-sha256',
                                                 '--workpack-sha256'),
                                 produces=(artifact_paths.STAGE6_HOOK,)),
    # ⚠ `--revalidate` 是 F2 补的：本层加了落盘闸门（**任何一条 hard ⇒ `.INVALID`**，
    #   不照 stage6——那一层一次产三份，拦的是「一条干净候选都没有」），
    #   而**闸门是我们自己加的，不给一条 0 成本提升路，一次付费产出就被永久锁死**。
    'stage7.rename':    NodeSpec('stage7', 'R-10', True,  'stage7 人名表',
                                  flags=('--dry-run', '--check-only', '--identity',
                                         '--recheck', '--apply-edit', '--revalidate',
                                          '--emit-edit-template', '--adopt-residual',
                                          '--validate-edit',
                                         '--actor', '--reason', '--response-repair'),
                                  produces=(artifact_paths.STAGE7_NAME_MAP,)),
    # ⚠⚠ 本层没有候选序号：一本书只有一份人物表，定稿是「给整份盖章」。
    #   但审批动作必须携带统一人工凭据，并用 decision-surface SHA-256 防旧页面误批。
    'stage7.approve':   NodeSpec('stage7', 'R-10', False, 'stage7 人名表定稿',
                                  flags=('--actor', '--reason', '--subject-sha256',
                                         '--workpack-sha256'),
                                  required_flags=('--actor', '--reason',
                                                  '--subject-sha256',
                                                  '--workpack-sha256'),
                                  produces=(artifact_paths.STAGE7_NAME_MAP,)),
    # 成书只能消费已批准的人名表；声明顺序同时是队列和进度屏的唯一顺序权威。
    'stage7.assemble':  NodeSpec('stage7', 'R-10', False, 'stage7 替换与成书',
                                 produces=(artifact_paths.STAGE7_BOOK,)),
}

#: 允许配置 executor agent 的现役节点。这里只列**真正调用生成/审判模型**的八个
#: 付费节点；``stage4.context``、人工 approve 与机械 review 仍由代码执行，不能在
#: API 上挂一份看似生效、实际无人消费的 executor override。
#:
#: 本表显式列值而不从 ``NODES`` 现算，配套闭合测试守 ``spec.paid`` 双向一致：
#: 新增付费节点却没接 agent、或代码节点误标 paid，两种漂移都必须响亮地红。
AGENT_EXECUTOR_NODES: Final = (
    'stage2.global', 'stage2.blueprint',
    'stage4.design', 'stage4.plan',
    'stage5.write',
    'stage6.hook', 'stage6.semantic',
    'stage7.rename',
)

#: Stage2 的两份付费生成可以在全局人工复核收口前完成：global 先产正式复核面，
#: blueprint 随后即可在人工阅读期间生成。闸门真正守的是它们的下游消费，不能再把
#: blueprint 本身误当成「消费了已批准 global」；否则所谓并行只剩一句 UI 文案。
_STAGE2_REVIEW_PRODUCERS: Final = frozenset({
    'stage2.global', 'stage2.blueprint',
})

#: 这两项自身不花钱，却会把未复核的 Stage2 主体投影给写手或交付物；若只用
#: ``paid`` 判闸，它们会成为绕过点。其余 0 成本 check/revalidate/apply-edit 都是
#: 开闸所需恢复路，必须保持可用。
_STAGE2_REVIEW_FREE_CONSUMERS: Final = frozenset({
    'stage4.context', 'stage7.assemble',
})


def stage2_review_required(node: str, *, paid: bool) -> bool:
    """这次真实执行是否必须先完成当前 Stage2 全局逐项复核。

    ``paid`` 必须来自运行时唯一贵贱判据 ``novel.orchestrator.nodes.is_paid``；
    contracts 层只拥有策略边界，不在这里重算 flags。未知节点仍由调用入口的
    ``validate_run`` 关闭式拒收。
    """
    if node in _STAGE2_REVIEW_PRODUCERS:
        return False
    return paid or node in _STAGE2_REVIEW_FREE_CONSUMERS

#: `(节点, 触发 flag)` → 这条跑法**额外**必填的 flag。`NodeSpec.required_flags` 的补集：
#: 那一列管无条件必填，这张表管「跟着另一条跑法才必填」。
#:
#: ⚠ 两张表分开而不是合并成一个可调用的谓词：必填这件事要能被 `preview`
#: **0 成本地摊开**（渲染占位符），谓词做不到——它只能回答是/否，说不出缺哪几个。
#:
#: ⇒ `stage0.edit` 的三条编辑路只有 `--apply-edit` 那条要人工凭据；
#:   `--apply-edit-plan` / `--apply-candidate-plan` 的凭据在计划文件里（连同乐观锁）。
#: ⇒ `stage5.review --retain-pretrim` 要 `--chapters` 指定恰好一章，并强制留下
#:   自报 `--actor` / `--reason`：它是逐章的 hash-bound 人工决定，不是匿名布尔开关。
CONDITIONAL_REQUIRED_FLAGS: Final[dict[tuple[str, str], tuple[str, ...]]] = {
    ('stage0.edit', '--apply-edit'): ('--actor', '--reason'),
    ('stage2.global', '--revalidate'): ('--actor', '--reason'),
    ('stage2.blueprint', '--revalidate'): ('--actor', '--reason'),
    ('stage4.plan', '--revalidate'): ('--actor', '--reason'),
    ('stage4.design', '--adopt-rejected'): ('--actor', '--reason'),
    ('stage5.write', '--revalidate'): ('--actor', '--reason'),
    ('stage5.write', '--apply-edit'): (
        '--actor', '--reason', '--current-chapter-sha256',
        '--preview-subject-sha256', '--edit-confirmation'),
    ('stage5.write', '--recover-prepared'): (
        '--actor', '--reason', '--subject-sha256'),
    ('stage5.review', '--retain-pretrim'): ('--chapters', '--actor', '--reason'),
    ('stage6.hook', '--revalidate'): ('--actor', '--reason'),
    ('stage7.rename', '--revalidate'): ('--actor', '--reason'),
}

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

#: 七个当前产物层；编号保留 Stage3 空洞，不重排 Stage4～7。
#: 它承重在产物目录名、schema 文档名、Pydantic 模块名、`Rule.stage` 与校验报告路由。
STAGES: Final = ('stage0', 'stage1', 'stage2', 'stage4', 'stage5', 'stage6', 'stage7')

# 只供 append-only 作业/队列历史解码。它们不进 NODES/STAGES，因此不能 preview、
# 调度或创建新队列；删除这两个名字会让既有 jobs.jsonl / queues.jsonl 整流不可读。
RETIRED_NODES: Final = ('stage3.style',)
RETIRED_STAGES: Final = ('stage3',)

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
BEACON_STATES: Final = ('pending', 'running', 'ok', 'landed_red', 'failed')

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
