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

from typing import Final, NamedTuple

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
OVERVIEW_VERDICT: Final = ('broken', 'stale', 'pending', 'unknown', 'complete')

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
#: 八层逐步补投影，在补上之前那条边诚实地是 `unknown`。
FRESHNESS_REASON: Final = (
    'no_provenance',        # 这份产物根本没有来源账 ⇒ unknown（**永不 fresh**）
    'no_inputs',            # 有来源账，但 inputs 缺失或为空 ⇒ unknown
    'ghost',                # 边指向的文件不在盘上 ⇒ unknown ＋ 点名报出
    'file_changed',         # 整份文件的指纹对不上 ⇒ stale
    'consumed_changed',     # 本节点真正读到的那部分变了 ⇒ stale
    'consumed_undeclared',  # 边上 consumed=null ⇒ 该边 unknown
    'consumed_unresolved',  # 声明过投影，但这次算不出来（没登记投影函数）⇒ 该边 unknown
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
#: Stage 0~7 均可拥有本层结论；这也是 `Finding.owner` 的完整取值面。
OWNER: Final = (
    'stage0', 'stage1', 'stage2', 'stage3', 'stage4', 'stage5', 'stage6', 'stage7', 'corpus',
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
#: 一份合法的 `RemovalReport` 一旦存在，机器摄入必然已经跑过（它自己就带着机器指纹），
#: 「没跑过」在产物内不可诚实表达——全仓也从未有过写入方（preflight 存量扫描 0 实例，
#: 2026-08-21）。「没跑 / 崩了 / 阻断」是**执行状态**，由「产物不存在 + JobState」表达。
#: 【为什么不能是 `bool`】「跑了没批」与「批了」必须分开，与 `FRESHNESS` / `JOB_STATE` /
#: `BADGE` 同构：本项目每一次把「不知道」并进「没问题」都付过学费。
#: ⚠ `R-0.3b`（`HARD`）判的是**不满足执行资格 ⇒ stage1 拒绝启动**。现役执行资格
#: 是 `approval.status == 'approved'` **且** `approval.origin == 'human'`
#: （`novel/stages/stage0/eligibility.py::is_stage0_executable`，唯一落点）：
#: `origin == 'auto_zero_hit'` 是历史自动批取值，**只读、不具执行资格**，
#: 人工重批（`stage0.approve`）后恢复。
#: ⚠ 2026-08-22 起 `approved` 那四个平铺字段收成一格判别联合（`Stage0Approval`），
#: 本元组仍是 `status` 那一格的取值面；两者的对账在 `tests/test_invariants.py`。
APPROVAL_STATE: Final = ('pending', 'approved')

# Human workflow checkpoints kept under Output/_service. These are service
# state, not artifact approval fields such as stage0 RemovalReport.approved.
APPROVAL_GATES: Final = ('s0', 's1', 's2', 's3', 's4', 's5', 's6', 's7')
APPROVAL_SEEN: Final = ('read', 'global')

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

#: **分章**的可疑项分类，属于 stage1。原型有 6 类，两轮裁决后只剩 1 类：
#:   · `仅识别到 ≤1 章却很长`  → 升级成显式终态 `R-1.13`
#:   · `完结标记之后还有内容`  → 被 `R-1.14` 的 HARD 判据接管
#:   · `early_cut` / `drop_ratio` / `long_marker` → 随剔除一起搬去 stage0
#: ⚠ **只剩一条不等于这一层变安全了**——是别的三条换了位置、两条换了严重度。
STAGE1_WARNINGS: Final = ('noisy_duplicate',)

#: 批准前分章预览里，逐章**原始章号**相对上一章的异常分类。
#: 【为什么要有它】纯数字番外与正常数字章节在机器结构上没有区别——零 removed、
#: 零 warning、`clean == source`——只看「原文 + 删除高亮 + warnings」的审批页对它是盲的。
#: 人工闸门要真的兜住这个盲区，人在点批准之前必须**看得见**章号序列断在哪里。
#: 三类互斥，判定优先级即本元组顺序：`reset` > `gap` > `repeat`。
#: ⚠ 这是**诊断不是判据**：命中不拦任何东西，也不进任何产物，只在审批页上标出来给人看。
#: 番外若接着正文往下编号（50 → 51），三类一条都不命中——那种形态机器看不出来，
#: 只能靠同屏的逐章首行预览让人读出来。**别把它宣传成番外检测器。**
#:
#: ⚠ 这里曾有第四类 `non_numeric`（原始章号解析不出数字）。2026-08-22 随
#: `MainTextSection.source_chapter_no` 从 `str` 改型成 `int` 一并删除：**载体自己
#: 保证了它是数字**，这一档从此结构上不可达，留着就是第二个 `not_run`——一个从来
#: 不会被写出来的取值，让取值面看起来比实际宽。产地那一路也从没产生过它：
#: `ChapterMarker.chapter_no` 恒由 `CHAPTER_RE` 匹配出的数字行解析得到。
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
#:   ⚠ 新架构**未翻默认**：默认仍是 ir1，ir1.1 经 env 显式选择，验收在跑书之后。
BLUEPRINT_ARCHITECTURES: Final = (
    'bounded-fidelity-v3', 'extraction-ir-v1', 'extraction-ir-v1.1')

#: 当前默认架构。`BLUEPRINT_ARCHITECTURES` 是取值面，这一格是默认值——
#: 翻默认只动这一格（`novel/config.py::DEFAULT_STAGE2_BLUEPRINT_CONTRACT` 从它派生），
#: **取值面不动**：盘上存量产物全是 v3，收窄取值面它们当场读不回来，
#: 而定向修复是按「基线产物的 architecture」选契约的。
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
BLUEPRINT_ARCHITECTURE: Final = 'extraction-ir-v1'
assert BLUEPRINT_ARCHITECTURE in BLUEPRINT_ARCHITECTURES

#: `SourceSpan.recovery` 的取值面：这个区间是怎么对上原文的。
#:
#: · `exact`      —— 字节逐字命中。beat 的区间**恒是它**（段号查表算出来的，
#:                   模型根本没写过那段字节）；hinge 抄对时也是它。
#: · `normalized` —— 只在**内容字符空间**命中：模型把半角标点写成全角、或把换行压掉了。
#:                   `bounded-fidelity-v3` 起只可能出自 `hinge.phrase`。
#:
#: ⚠⚠ **这里曾经还有第三档 `lcs`**（按最长公共子串把被改掉一两个字的锚点救回来），
#: 随 `bounded-fidelity-v3` 一并下线：beat 不再有可改的锚点，而 hinge 那一侧的 LCS 兜底
#: 本来就是禁的（实测「传家玉镯」→「家传玉镯」救回来的是**语义已经变了**的片段）。
#: ⇒ 它在新架构下**结构性不可产生**，留在取值面里就是一个永远读不到的分支。
SPAN_RECOVERY: Final = ('exact', 'normalized')

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

#: `extraction-ir-v1` 响应侧 place 的三态（判别联合的 discriminator 取值面）。
#:   · `P_EXPLICIT` —— 原文明写（或延续中可辨认），必带 `name`；
#:   · `P_INHERIT`  —— 延续**上一相邻拍**的地点（仅同 `time_layer` 且相邻才许，
#:                     代码做继承；链头 INHERIT 落 `place_inherit_without_base` 账）；
#:   · `P_UNKNOWN`  —— 确实无从判断 ⇒ 产物侧 `place=null`。
#: 不确定性是**受约束的状态**，不是自由文本（`Stage2_improvement.md` §三.2）。
PLACE_MODES: Final = ('P_EXPLICIT', 'P_INHERIT', 'P_UNKNOWN')

#: 逐字原话允许被一处提示语切开时，插入部分的内容字数上限。
#: 形态必须在原文字节上长成「收引号 + ≤N 内容字 + 开引号」；
#: 改词、插动作描写、只写半句照旧判缺失。
SPEECH_TAG_MAX: Final = 12

#: 碎拍吸收阈值：只有 < 这个字数的拍才会被并进同层的、较小的那一侧邻居；够大的拍一条不动。
#: ⚠ **只有分布支持，没有独立判断校准**。它来自「写作节点一拍的自然产出 80~150 字」的观测，
#: 不是拿成稿质量标定出来的。「80 比 60 或 100 更好」**没有证据**。
BEAT_MIN_CHARS: Final = 80

#: 写作节点一拍**自然产出**区间的上界。下界就是 `BEAT_MIN_CHARS`（同一个数不是巧合——
#: 拍就是按它切的，切完自然不会低于它）。**这一对整份插值进规划 prompt**，指导它怎么分预算。
#:
#: ⚠ **它与 `BEAT_MIN_CHARS` 同源、同样只有观测没有校准**（`_README.md` 那条纪律要求
#: 每个数都答一句「量出来的还是拍的」）：两个数都来自「写手一拍产 80~150 字」这一次观测，
#: 而**没有任何一次实验拿成稿质量去标定过 150**。原型里它是 `BEAT_NATURAL = (BEAT_MIN_CHARS, 150)`
#: 里的一个**行内字面量**，`invariants` 从前没有它。
#: ⚠ **别把它与 `BEAT_ALERT_MAX` 合成一个数**：见下。
BEAT_NATURAL_MAX: Final = 150

#: 「离谱到该有人看一眼」的报警线。**与 `BEAT_MIN_CHARS` / `BEAT_NATURAL_MAX` 是故意的两对数**：
#: 前者指导分预算，后者是报警。合并成一个会同时坏掉两件事——
#: 拿 50/300 去指导分配会让规划器把拍摊平，拿 80/150 去报警会让每一章都红。
#: ⚠ **两对都必须从这里插值**：一对进 prompt、一对进判据，各手抄一份的下场是
#: 模型按 A 算、校验按 B 判，而谁也不会出声。
BEAT_ALERT_MIN: Final = 50
BEAT_ALERT_MAX: Final = 300

#: 原文↔成稿对齐的锚覆盖率低于此值时只提示「证据不足」，不判内容不合格。
#: 阈值与结论都由服务端下发；前端只画 coverage 原始读数和 state。
ANCHOR_COVERAGE_WARN: Final = 0.6
ANCHOR_COVERAGE_STATE: Final = ('low', 'adequate')

#: 拍的**分组算法版本**。与 `BEAT_MIN_CHARS` 一起进 `plan_chapters._provenance.params`。
#:
#: 【它存在的全部理由】不记算法版本与阈值，「这份计划的拍是照哪一套切的」在改过阈值之后
#: 就**无从判断**——而 `structure_changed` / `structure_units` 两条 HARD 是拿**当前**的
#: `group_beats` 去量的。⚠ 原型记了这个字段，**而全仓没有一个读者**：两条判据每一次都在
#: **拿新尺子量旧布**，那个字段本来就是为了让人发现这件事才存在的。
#: ⇒ `R-5.11` / `R-5.11b`：一行常驻报出来，且两条依赖分组的判据**先比它再判**。
GROUPING_VERSION: Final = 'absorb-small-v1'

#: 一段原文没被任何拍覆盖时，多长才值得报出来。
UNCOVERED_MIN_CHARS: Final = 30


# ─────────────────────────────────────────────────────────────────────────────
# 四、人物与称谓（stage2）
# ─────────────────────────────────────────────────────────────────────────────

#: 「谁在这么叫」。**指称群体一律记 `public`**——「苏府下人」这类群体名解析不出人，
#: 而这个字段的用途是**派发键**（谁在称呼谁），一个解析不出人的串对派发毫无用处。
#: 已知代价：丢掉「哪个群体」（四本实测 2/194 条）。
ADDRESS_BY: Final = ('char_id', 'narration', 'public')

#: 称谓的场合。
ADDRESS_CONTEXT: Final = ('address', 'reference')

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
#:   stage3 的 `narrative_distance` / `interiority` **已经在记它**，且写得比二值枚举好得多
#:   （实测产物逐字：「紧贴主角内心，以第一人称即时吐露所有真实想法，读者全程共享其腹黑计算」）。
#:   再立一个 `focalization` 就是同一语义的第二落点，而两个落点必然漂移（判据 7）；
#:   叠上判据 6「加字段不是免费的，新字段会从已有字段身上取」，收益不抵成本。
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
#: ⚠ 与 `arc_tracks[].change_type` 恰好相反：那一个是**开放**枚举
#: （`stage2.md` §1.6 明写「末尾的 `…` 是语义的一部分」），所以它**不许**做成 `Literal`。
WORLD_RULE_KINDS: Final = (
    'social_norm', 'institution', 'legal_rule', 'ability_system',
    'supernatural_rule', 'technology_rule', 'physical_constraint',
)

#: `arc_tracks[].change_type` 的取值面。⚠⚠ **开放枚举，末尾那个 `…` 是语义的一部分**
#: （`stage2.md` §1.6 明写）⇒ 它**不许**做成 `Literal`，也不许拆成元组再 `join`
#: ——拆开就把「还可以有别的」这件事丢了，而那正是它与 `WORLD_RULE_KINDS`（七个，闭合）
#: 相反的地方。整串下发给模型，本仓不校验它。
#:
#: 【为什么它是字符串不是元组】这一格答的是「发给模型的那句话长什么样」。
#: 做成元组的唯一用途是拿去 `'|'.join(...)`，而那样就得在末尾**手工补一个 `…`**
#: ——那是第二个落点，而且补漏了不会有任何东西出声。
CHANGE_TYPES_ENUM: Final = 'stable|development|revelation|deterioration|reversal|…'

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

#: 一次调用当时的缓存口径，记进 `_provenance`（stage2 的 `repairs[]` · stage3 的 `attempts[]`）。
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
PLAN_BUDGET_BAND: Final = (0.90, 1.10)

#: Stage4 结构规划的三个闭合轴。它们描述的是叙事架构，不是文风：同一组事实可以被
#: 放在冷开场、升级或兑现位置，可以作为完整场景或压缩交代，并且必须说清与上一单元
#: 的时间关系。Stage5 只执行这些决定，不再自行猜测重排后的衔接。
STRUCTURAL_ROLES: Final = (
    'cold_open', 'setup', 'escalation', 'turn', 'payoff', 'bridge',
)
DELIVERY_MODES: Final = ('scene', 'summary')
TEMPORAL_LINKS: Final = ('start', 'continue', 'earlier', 'later', 'parallel', 'return')

#: 「这一章大到该做一次结构决策」的源内容单元数下限（`R-5.6` 的 `structure_changed`）。
#: ⚠⚠ **它同时是 prompt 与判据的唯一落点。** 从前 prompt 里那个 `4` 是**手抄的**，
#: 判据侧 `checks.py` 另有一份行内字面量——两份数字各自漂的下场，`Q-6` 已经写过一遍。
#:
#: ⚠ **它约束的不是「必须重排」。** 2026-08-19 换口径之前，这条 HARD 判的是
#: 「源单元 ≥4 的章不得仍按源序展开」，即**保持源序 = 阻断落盘**；于是模型对每一个够大的章
#: 都必须移位，哪怕移位没有叙事理由。实测代价：交付那本书 11/11 章都是 `cold_open` 倒叙起手、
#: 全书 37 拍里向前推进的 `continue` **只出现 1 次**，读者永远在往回走。
#: 换口径后它判的是「与恒等基线（`novel/stages/stage4/measure.py::identity_plan` 的结构切片）
#: 逐格相同」——**顺叙不再违规**，没做过任何结构决策才违规。
PLAN_RECONSTRUCT_MIN_UNITS: Final = 4

#: 「逐字挤占」的下限倍数：一拍的预算至少要是它**必须逐字写入的原话**字数的这么多倍。
#: 2.0 的含义是「原话最多占一半」——低于它，剩下的字连交代谁在说、说完什么反应都不够。
#:
#: ⚠ **它只由分布长尾支持，没有独立判断给它当基准**：实测某本 43 个带原话的拍里
#: 中位占 18%、只有 1 个越过 50%，另一本 40 个里同样 1 个。阈值取在长尾上，
#: 不会把正常的拍卷进来。
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

#: 两条写作臂。差别只有 (system prompt, 给不给源章原文)。
#: **闭卷会系统性把对白比压到 ~0.7×**（三本书 15 闭卷章 / 16 开卷章，同章配对，方向一致：
#: 0.74/0.67/0.68 vs 1.00/1.01/0.94）。效应量远大于本项目量过的任何跑间方差——
#: 这不是叮嘱不够，是这条臂本身的性质。
DEPTH_MODES: Final = ('deep', 'shallow')

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
BOOK_BAND: Final = (0.90, 1.10)

#: 闭卷臂成稿与源章的 20 字窗口重合度上界（已扣掉契约要求逐字保留的部分）。
#: **它守的是【臂的身份】，不是文风质量** —— 这一点决定了阈值怎么选：
#:   · 闭卷实测逐章 0.0%~**2.4%**（11 章，中位 0.1%）；
#:   · 开卷实测逐章 0.9%~**96.8%**（20 章，中位 49.8%），书级均值 36~78%。
#: 10% 落在两者中间且离两边都远：正常闭卷章不可能碰到（4× 余量），
#: 而「臂跑错了 / `with_source_text` 被翻转 / 开卷稿落进 deep 目录」会立刻越线。
#: ⚠ **它是提示级不是阻断级**：越线的唯一真修法是重写一整章（付费），
#: 而本层已经为同一个理由把篇幅硬门降过一次级（判据 15）。
#: ⚠ **开卷臂不定线**（B-03 的另一半）：0.9%~96.8% 的跨度说明那不是一个可以一刀切的量，
#: 它只报分布。别把这条常量套到 shallow 上。
#: ⚠ **这个数只有 11 章的标定，是「拍的」不是量的**（判据 18/32）：
#: deep 章数上到 30+ 之后要重看一次分布再决定动不动它。
DEEP_OVERLAP_MAX: Final = 0.10

#: 连续多少轮未消 → 判「根因不在写手」，反哺主动跳过。
STOP_REWRITE_AFTER: Final = 2


# ─────────────────────────────────────────────────────────────────────────────
# 七、文风（stage3）
# ─────────────────────────────────────────────────────────────────────────────

#: 声音内核八维，全部自然语言描述。
VOICE_KEYS: Final = (
    'narrative_distance', 'register_tone', 'interiority', 'sentence_rhythm',
    'dialogue_presentation', 'description_density', 'chapter_open_close', 'mode_variation',
)

#: 开篇文案段的写法七项。
HOOK_KEYS: Final = (
    'person', 'open_mode', 'info_hiding', 'twist', 'end_question', 'tone', 'relation_to_body',
)

#: 文风画像的尝试类别（`_provenance.attempts[].kind`）。
#: `surface_habits_targeted_repair` = 只把 `surface_habits` 那一个列表发回去改写，
#: 其余字段一个字不动——**全链唯一有定向返工通道的字段**。
STAGE3_ATTEMPT_KINDS: Final = (
    'surface_habits_targeted_repair',
    # 逐源章抽取，再只用抽象结果做一次书级汇总。
    'chapter_local', 'chapter_aggregate',
)


# ─────────────────────────────────────────────────────────────────────────────
# 七之二、开篇文案（stage6）与人名替换（stage7）
# ─────────────────────────────────────────────────────────────────────────────

#: 三种文案写法，一次调用各产一条候选。**这是唯一落点**——
#: prompt 里那一份由 `fill()`（`string.Template`）插值，缺值抛异常而不是渲染成空串。
#: ⚠ 产地**不许 `.get` 兜底**：写歪的 approach 一律显式终态（枚举上的兜底
#: 就是给违约数据发一张静默通行证）。
#: ⚠ 改这张表会改 `prompts.HOOK_SYS` 的指纹——那是对的：
#: **一个模板 + 一份枚举表 = 一个 prompt**，表变了发出去的东西就变了。
HOOK_APPROACHES: Final = ('source_architecture', 'payoff_preview', 'scene_punchline')

#: 定稿那一份的 `approach` 比候选多一个值。
#: 它答的是「这份人工稿是从哪一条候选改出来的」——**没有前身时才是 `human_edit`**。
#: ⚠ 先 `stage6.approve --pick 2` 再 `--apply-edit`，`approach` 会**留着**被选候选的策略，
#: 而文本已经是人手写的。「它是不是人写的」由 `origin` 回答，不由这里回答。
HOOK_APPROVED_APPROACHES: Final = HOOK_APPROACHES + ('human_edit',)

#: 候选在最新一次质量审判中的可选状态。界面与批准路共同消费。
#:
#: ⚠⚠ 这里曾有四个取值，多的两个是 ``clear`` 与 ``contradiction``。它们只由
#: automated PASS/FAIL 产生，而自动放行门随 evaluator profile 于 2026-08-18 整批下线
#: ⇒ **两个都不可达**。留着它们最坏的一面是 ``clear``：界面会为一个永远不会出现的
#: 绿灯保留一条渲染分支，而读的人会以为「只是这次没绿」。
#: 现在只剩两个，而且它们答的是不同的问题：``unreviewed`` = 没有可用的当前审判，
#: ``uncertain`` = 判过、有证据、但本仓不替人盖章。
HOOK_SEMANTIC_STATES: Final = ('uncertain', 'unreviewed')

#: 三种写法各自的一句说明。**键必须与 `HOOK_APPROACHES` 逐字相等**（下面有 import 期断言）。
#: 它逐字进 `HOOK_SYS`——原型把这三句写在产地的一个 dict 里，而**枚举表在这里**，
#: 于是「有哪三种写法」与「每种是什么意思」分了两个落点。合成一份之后，
#: 加一种写法而忘了写说明，`fill()` 当场抛 `KeyError`（缺值不渲染成空串）。
#: 这三种写法来自 2026-08-15 的三书 A/B：最佳实验候选对当前定稿的匿名主编分
#: 8.78 vs 4.88，且 3/3 本获胜。它们分别守住原作文案已经验证有效的结构、允许用
#: 后期兑现制造期待、以及强制落到可视可听的场面；不再按「泄底多少」切策略。
HOOK_APPROACH_NOTES: Final[dict[str, str]] = {
    'source_architecture': '原作文案结构 —— 保住原文的信息次序、升级节拍、反差与最后一击，'
                           '换成重写正文的声线和全新措辞',
    'payoff_preview':      '兑现预告 —— 从全书挑最有吸引力的后期兑现或反转作预告，'
                           '制造「怎么走到这一步」的阅读欲',
    'scene_punchline':     '场面重击 —— 用一个可视、可听的具体场面或对白砸出核心关系，'
                           '以人物反应、危险或笑点收尾',
}

#: 收尾形态的四类。**唯一落点**——分类器（`novel/core/bands.py::ending_shape`）、
#: 判据措辞、报告共用这一份。
#: ⚠ 它是 `hook_style.end_question`（一条**自由文本**描述）唯一可确定性检测的投影：
#: 不去解析那段自由文本，而是从原作文案段量出同一个量，两边比**形态**。
ENDING_SHAPES: Final = ('引号台词', '问句', '省略号', '陈述')

#: 段间分隔的两档。**离散性质，按字节判**（见 `novel/core/bands.py::sep_band_of_text`
#: 里那段 off-by-one 的账）。
SEP_BANDS: Final = ('空行分段', '单换行')

assert tuple(HOOK_APPROACH_NOTES) == HOOK_APPROACHES, \
    'HOOK_APPROACH_NOTES 的键与 HOOK_APPROACHES 分家了——加一种写法就要写一句说明'

#: 残留候选的置信度。**`high` ⟺ 命中处第二个字 ∈ `家府氏宅门`**（结构信号）。
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
    '--measure-only': '只跑确定性实测并报出来，**不写盘**（与 `--remeasure` 是两件事）',
    '--remeasure': '重算确定性实测值写回，其余字段不动',
    '--renormalize': '按当前契约重算代码派生字段与原文定位',
    '--names-only': '只产逐章可用名册（stage4），不产计划',
    '--identity': '恒等基线：拿原作自己的东西跑整套判据，看判据有没有把合法情况排除在外',
    '--apply-edit': '把人工改过的内容落成新的一轮',
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
    '--emit-edit-template': '向 stdout 输出带当前 schema version 的 stage7 人工编辑空壳，不写盘',
    '--adopt-residual': '显式采纳可安全应用的 high residual 建议并重放 stage7 确定性链',
}

#: 挂在**付费**动作上的 flag。它们不是 0 成本入口，登记在这里免得被当成漏项。
PAID_FLAGS: Final = {
    '--full-extract': 'stage2 全局底座、逐章蓝图与 stage3 文风画像并发首跑',
    '--shallow-rest': 'stage5 把未选为深度重构的其余章节交给浅层写手',
    '--repair-surface-habits': 'stage3 表层习惯的定向返工——它要再调一次模型',
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
    '--patch': 'stage2 逐章蓝图的字段级修复（extraction-ir-v1）——逐条失败各调一次模型，'
               '量级是条不是章',
}

#: 重算时**只对本次新引入的错误入终态** —— 旧违约照报但不挡路。
#: 否则一条不相干的旧问题会把重算这条路永久堵死。
REVALIDATE_ONLY_NEW_ERRORS: Final = True

#: **局部改写**一份付费产物的那几条 0 成本路。`_provenance.partial_rewrite` 的取值面。
#:
#: 【为什么是一个带值的键，不是三个布尔】这三条干的是同一件事——改掉产物的一部分，
#: 而 LLM 写的那些字段仍出自当初那次付费产出。区别只在「哪条路」，**那是一个值不是三个键**。
#: `--recheck` / `--renormalize` 都用同一个局部改写字段记录来源
#: ——同一件事的第三个落点（`Q-3`：同一个语义必须叫同一个名字）。见 `_common.md` §5.1b。
#:
#: ⚠ 它**必须 ⊆ `FREE_ENTRIES`**（由 `tests/test_invariants.py` 钉着）：
#: 一条会花钱的路不叫「局部改写」，那是一次新的产出，该记一份新的来源账。
#: ⚠ **`--revalidate` 不在这里**：提升路改的是**状态**（`.INVALID` → 正式稿），
#: 内容一个字没动，它有自己的键 `revalidated`。两件事。
#:
#: ⚠ **`--identity` 是 F2 加进来的第四条，而它与 `--remeasure` 逐条同形**：
#: 0 成本 · 写盘 · 改的是产物里**由代码算出来的那一部分**（stage7 的 `identity` 键 /
#: stage3 的 `measurable`）· LLM 写的字段一个字不动。
#: ⚠⚠ 但它**只对 stage7 成立**：stage6 的 `--identity` 只打印当前恒等读数、**不写盘**。
#: 同一个 flag 在两层行为不同，
#: 这条已经登记在 `WRITE_EFFECTS` 上方那段注释里——本表按 flag 索引，
#: 而它收的是「**这条路要是写了盘，那份产物就是混版的**」，对两层都不说假话。
#: ⚠ **定稿节点刻意不在这里**：定稿一个由代码算出来的字段都没改，
#: 它只是盖了个章。标 `partial_rewrite` 会说假话。
#: `--apply-edit` 不是在每一层都代表混版：stage5 的整章人工稿会铸一份
#: `origin=human` 的新来源账；stage7 只改 `extra_subs` / 决策 patch，模型生成的
#: `characters` 仍来自旧调用，所以必须允许它如实标成局部改写。
PARTIAL_REWRITE_FLAGS: Final = (
    '--remeasure', '--recheck', '--renormalize', '--identity', '--apply-edit',
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
    '--deep': '深度重构章节（逗号分隔；与 --shallow-rest 成对使用）',
    # ⚠ 「相对**书目录**」这五个字是承重的：stage3/4/6/7 走
    #   `novel/paths.py::edit_rel`，相对路径按当前书目录解释、绝对路径照收，
    #   两者都必须落在书目录内。此前四份实现按进程 cwd 解释而报错文案说书目录，
    #   两个口径分家（skill 现场记录 FN-016）。这一行是人唯一会读到的说明。
    '--apply-edit': '人工改好的候选稿路径（必填；相对路径相对**书目录**，'
                    '如 stage7/name_map_edit.json，也接受书目录内的绝对路径）',
    '--apply-edit-plan': '审校台生成的 stage0 编辑计划路径（正文 + 剔除账 + 乐观锁）',
    '--review-decisions': 'stage2/global_review 的人工决策集（相对当前书目录）',
    '--apply-candidate-plan': '审校台生成的 stage0 候选裁决（采纳 / 否掉整版 + 乐观锁）',
    '--actor': '人工决定的自报身份（不是认证身份）',
    '--reason': '人工决定的非空理由',
    '--subject-sha256': '人工决定乐观锁定的完整内容 SHA-256',
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
    # ⚠⚠ **它是「逐章重构深度」这个人的决策的唯一载体**（`R-7.2` / `stage5.md` §九-3）：
    # 那件事**不落任何产物**，只活在这个实参里。没有它，`stage5.write --mode deep`
    # 只能把缺的章全部写成闭卷，两条臂没法按章分工，而 `R-7.2` 那一行就成了假话。
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
    '--full-extract': '并发写 stage2/global_grounding.json、stage2/chapter_blueprints.json 与 stage3/style_grounding.json',
    '--shallow-rest': '按人工深度选章并发写 stage5/deep 与 stage5/shallow 的逐章成稿',
    # ── 只看不动的四条（`FREE_ENTRIES` 自述「不写盘」）──
    '--dry-run': '',
    '--check-only': '',
    '--measure-only': '',
    # ⚠ **它一个字节都不写**（原型只 `print` 逐章名册），而本表**默认按会写算** ⇒
    #   不登记的后果是**方向反的**：`/api/enums` 上它会被派生成
    #   「写 `stage4/plan_chapters.json`」——**那是系统在对用户说假话**
    #   （`Q-3`）。实测确认过一次才补上这一行。
    '--names-only': '',
    '--emit-edit-template': '',
    '--adopt-residual': '重写 stage7/name_map.json，清旧审批并退休现有成书',
    # ── 免费但**会写盘**的那些：原型那 8 条注释点名的正是它们 ──
    '--remeasure': '重算的 measurable 写回正式产物（其余字段不动）',
    '--recheck': '刷新已有成稿的指标，并重拼 draft.md',
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
    # ── stage2 那两条 0 成本但**会写盘**的路（F5-1 补）──
    # ⚠ 它们与上面 `--remeasure` / `--recheck` 那几条是同一类：**免费，但写盘**。
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
#: `stage4.plan --identity` 不覆盖正式计划，它把恒等基线单独落在
#: `writer_context.identity.json`。此前因为只有 flag 维度，API 会误报
#: `stage4/plan_chapters.json`；方向虽保守，文件名却会误导人的覆盖决策。
NODE_WRITE_EFFECTS: Final = {
    ('stage4.plan', '--identity'): artifact_paths.STAGE4_WRITER_CONTEXT_IDENTITY,
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
    modes: tuple[str, ...] = ()         # 有参数臂的节点，取值面在别处（DEPTH_MODES）
    paid_flags: tuple[str, ...] = ()    # 能把一个 0 成本节点变成付费的 flag
    flags: tuple[str, ...] = ()         # ★ 本节点接受哪些**跑法** flag，见下
    required_flags: tuple[str, ...] = ()  # ★ 少一个就拼不出可执行 argv 的 flag，见下
    produces: tuple[str, ...] = ()      # ★ 默认路径往盘上写哪几份产物，见下
    # 可选首档；未做 A/B 的节点必须保持 None（即 high）。
    # ⚠ 取值面是 `low` / `high` / `max` / `None`：`medium` 与 `xhigh` 虽然 API 收，但被
    #   服务端静默映射回 `high` ⇒ 允许它们就是允许一个「写着降了档、实际一档没降」的配置，
    #   而账本那一列会如实记下 `medium`，事后分辨不出来。`max` 是**升档**（2026-08-21
    #   为 FC-13 质量实验启用，官方 effort 前缀已从 `4744479^` 原样取回）：它走
    #   `rungs_from_effort` 的覆盖梯，不进降级梯 `LADDER`。
    #   判定落点是 `novel/llm/client.py::rungs_from_effort`，不靠本注释。
    effort: str | None = None
    # 本节点钉死的模型 id；'' = 跟随全局默认（`novel/config.py::DEFAULT_MODEL`，
    # 可被 `DEEPSEEK_MODEL` 覆盖——A/B 换臂的显式口）。
    # ⚠ **只有「产品裁决过、不许被环境变量静默翻转」的节点才许填非空**：
    #   2026-08-19/20 用户裁决「除语义判官外全链 flash」，而判官例外必须长成形状
    #   而不是纪律——此前靠每条命令记得带 `DEEPSEEK_MODEL`，忘一次就静默烧成
    #   flash 判官，账本事后才看得出来。实测依据见 `docs/BACKEND_CONTRACT.md`
    #   §十 2026-08-20 那一行（flash 判官把唯一与正文矛盾的候选排成第一名）。
    model: str = ''


#: 全部作业单元。⚠ 顺序即处理链顺序，队列与断点续跑都按它排。
#:
#: ⚠ **写作两条臂是【参数】不是两个节点**：`stage5.write --mode deep|shallow`。
#: 依据是 stage5 自述「两臂只差 (system prompt, 给不给源章原文)，产物形状逐字节相同，
#: 判据表只有一张」，且 `R-7` 一节同时管两臂。两个 node id 指同一段代码就是第二个落点。
#: ⇒ `R-11.10`「一本书同时只跑一个作业，唯一例外是写作两条臂」的互斥键因此是
#: `(sid, node, mode)`——例外从「两个 kind」变成「同一个节点的两个 mode 值」，语义更准。
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
#: 补它是因为「必填」此前**只活在节点函数体里**（`stage0.approve` 少了 `--actor/--reason`
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
    # ⚠ 2026-08-22 阶段 C：三个节点都先写 `stage0/revisions.json`（唯一可变格、
    #   一次原子写就是提交），再把 `current` 物化成另外两份。三份都登记 ——
    #   `produces` 回答的是「跑完这一步盘上该有哪几份」，物化投影同样在其中。
    'stage0.remove':    NodeSpec('stage0', 'R-0',  False, 'stage0 非正文剔除',
                                 flags=('--source', '--dry-run', '--check-only'),
                                 produces=(artifact_paths.STAGE0_REVISIONS,
                                           artifact_paths.STAGE0_REMOVAL_REPORT,
                                           artifact_paths.STAGE0_CLEAN_TEXT)),
    'stage0.approve':   NodeSpec('stage0', 'R-0',  False, 'stage0 人工批准',
                                  flags=('--actor', '--reason'),
                                  required_flags=('--actor', '--reason'),
                                  produces=(artifact_paths.STAGE0_REVISIONS,
                                            artifact_paths.STAGE0_REMOVAL_REPORT)),
    'stage0.edit':      NodeSpec('stage0', 'R-0',  False, 'stage0 人工编辑',
                                  flags=('--apply-edit', '--apply-edit-plan',
                                         '--apply-candidate-plan', '--actor', '--reason'),
                                 produces=(artifact_paths.STAGE0_REVISIONS,
                                           artifact_paths.STAGE0_REMOVAL_REPORT,
                                           artifact_paths.STAGE0_CLEAN_TEXT)),
    'stage1.partition': NodeSpec('stage1', 'R-1',  False, 'stage1 分章',
                                 flags=('--dry-run', '--check-only'),
                                 produces=(artifact_paths.STAGE1_MAIN_TEXT,
                                           artifact_paths.STAGE1_HOOK_COPY,
                                           artifact_paths.STAGE1_PARTITION_REPORT)),
    # ⚠ `--apply-edit` 是 TODO-05 补的，而它补的是一个**契约内的空洞**：
    #   `reader_reveal_events` / 知情人 / secret 事实抽错时，此前唯一的路是手改产物
    #   ——没有 schema 管形状，来源账上也留不下「这是人改的」。
    #   八层里 stage0/3/4/5/6/7 六层都已有这条 0 成本人工路，本层是最后一个缺口。
    'stage2.global':    NodeSpec('stage2', 'R-2',  True,  'stage2 全书级抽取',
                                 flags=('--renormalize', '--purge-role', '--resample',
                                         '--revalidate', '--full-extract',
                                         '--apply-edit', '--review-decisions',
                                         '--actor', '--reason'),
                                 produces=(artifact_paths.STAGE2_GLOBAL_GROUNDING,
                                           artifact_paths.STAGE2_GLOBAL_REVIEW)),
    # ⚠⚠ **`--chapters` 是这一列里最容易漏的那一个**：§5.1 有 **19 条**判据的修法是
    #   `blueprint --chapters`（定向重生成那几章），而它此前只挂在 `stage5.write` 上。
    #   漏了它，那 19 条在 F5-4 注册的那一刻全被 `_validate` ⑧ 拒收。
    'stage2.blueprint': NodeSpec('stage2', 'R-3',  True,  'stage2 逐章抽取',
                                 flags=('--dry-run', '--check-only', '--renormalize',
                                        '--chapters', '--patch',
                                        '--revalidate', '--actor', '--reason'),
                                 produces=(artifact_paths.STAGE2_CHAPTER_BLUEPRINTS,)),
    'stage3.style':     NodeSpec('stage3', 'R-4',  True,  'stage3 文风画像',
                                  flags=('--dry-run', '--check-only', '--measure-only',
                                         '--remeasure', '--revalidate', '--apply-edit',
                                         '--repair-surface-habits', '--actor', '--reason'),
                                 produces=(artifact_paths.STAGE3_STYLE_GROUNDING,)),
    'stage4.plan':      NodeSpec('stage4', 'R-5',  True,  'stage4 重构计划',
                                 flags=('--dry-run', '--check-only',
                                        '--revalidate', '--names-only', '--identity',
                                        '--apply-edit', '--chapters', '--actor', '--reason'),
                                 produces=(artifact_paths.STAGE4_PLAN_CHAPTERS,)),
    # ⚠ `flags=()` 在这一格是【终态】不是待定：本节点**本身**就是那个动作
    #   `stage4.context` 本身就是该动作，因此不再另设同义 flag。
    'stage4.context':   NodeSpec('stage4', 'R-6',  False, 'stage4 写作上下文组装',
                                 produces=(artifact_paths.STAGE4_WRITER_CONTEXT,)),
    # ⚠ `--revise` 是 F4 从 `stage5.review` **挪过来**的（用户裁决，见本表下方那段）。
    # ⚠⚠ **两条臂合成的那份 `stage5/draft.md` 刻意不在这一列里**，而它确实是本节点写的
    #   （`assemble_book`，每跑都拼）。F4 试过补上，当场被两条测试抓住：
    #   这一列的消费方是 `state.required_artifacts`（「这一层**跑完了**盘上该有哪几份」），
    #   而那份拼稿**在只写了一章时也存在**（缺章会写成 md 里的一条横幅，不是不落盘）
    #   ⇒ 拿它当完成度信号是**假阳**：`resume_verdict` 会对一个写了一半的付费层判「跳过」。
    #   本层的完成度只有一条路——`planned_chapters` 对 `chapters_done` 逐章数（`R-14.2`），
    #   读不回计划时是 `unknown` 而不是 `False`。
    #   ⇒ 它的写盘效应由 `stage5/{mode}/ch{nn}.json` 那一条带着（同一次跑、同一个动作）。
    # ⚠ `--revise` 挪过来之后**不再需要 `paid_flags`**：那一列的定义是
    #   「能把一个 **0 成本节点**变成付费的 flag」，而本节点的默认路径本来就付费。
    #   留着它是一格永远不起作用的声明——`is_paid` 早在 `spec.paid` 那一步就判完了。
    'stage5.write':     NodeSpec('stage5', 'R-7',  True,  'stage5 逐章写作',
                                  modes=('deep', 'shallow'),
                                  flags=('--dry-run', '--recheck', '--revalidate',
                                         '--apply-edit', '--revise', '--chapters',
                                         '--deep', '--shallow-rest', '--actor', '--reason'),
                                 produces=(artifact_paths.STAGE5_CHAPTER,
                                           artifact_paths.STAGE5_MODE_DRAFT)),
    # ⚠ `--wontfix` 是**参数**不是 0 成本入口（见 `VALUED_FLAGS`），形态同 `--pick`。
    # ⚠⚠ `stage5/wontfix.json` **刻意不进 `produces`**，而它确实是本节点写的：
    #   这一列的消费方是 `state.required_artifacts`（「这一层跑完了盘上该有哪几份」），
    #   而豁免那一份是**可选**的——没人豁免过任何东西时它就不该存在（盘上 3/3 本都没有）。
    #   把它登记进来会让每一本书**永远差一份产物**。⇒ 它的写盘效应登记在 `WRITE_EFFECTS`。
    'stage5.review':    NodeSpec('stage5', 'R-8',  False, 'stage5 全书验收',
                                 flags=('--check-only', '--wontfix', '--retain-pretrim',
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
                                        '--wontfix', '--actor', '--reason'),
                                 produces=(artifact_paths.STAGE6_HOOK,)),
    # 付费语义审判是字面探针的 companion（R-9.19/R-9.20）：
    # 不接 `--check-only`，否则一层两个复检节点、HTTP kind 路由产生歧义。
    # ⚠ 它是全链**唯一**的语义审判节点（stage5 那一个已于 2026-08-17 下线）。
    # 保留它的实测依据：2 次调用 / 1.7% 输出 token，而它的匿名质量排序在实跑中
    # 直接纠正过一次定稿——性价比与被下线的那个不在一个量级。
    # ⚠⚠ `model` 钉死 `deepseek-v4-pro`（2026-08-19/20 用户裁决，全链 flash 的
    #   **唯一例外**）：flash 判官在有 ground truth 的样本上把唯一与正文矛盾的候选
    #   判 PASS 并排成第一名，证据区间圈成候选全文（等于没有指向）——对「结论必须
    #   绑证据」的判官是死刑。钉在本表 ⇒ `DEEPSEEK_MODEL` 换不掉它。
    'stage6.semantic':  NodeSpec('stage6', 'R-9',  True,  'stage6 文案语义审判',
                                 flags=('--dry-run',),
                                 produces=(artifact_paths.STAGE6_SEMANTIC,),
                                 model='deepseek-v4-pro'),
    # ⚠ `--pick` 是**参数**不是 0 成本入口（见 `VALUED_FLAGS`）。
    #   本节点的 `flags` 从前是空的，注释写着「候选序号是它们的参数，参数名属 F 期」。
    'stage6.approve':   NodeSpec('stage6', 'R-9',  False, 'stage6 文案定稿',
                                 flags=('--pick', '--actor', '--reason',
                                        '--subject-sha256'),
                                 required_flags=('--pick', '--actor', '--reason',
                                                 '--subject-sha256'),
                                 produces=(artifact_paths.STAGE6_HOOK,)),
    # ⚠ `--revalidate` 是 F2 补的：本层加了落盘闸门（**任何一条 hard ⇒ `.INVALID`**，
    #   照 stage3 不照 stage6——那一层一次产三份，拦的是「一条干净候选都没有」），
    #   而**闸门是我们自己加的，不给一条 0 成本提升路，一次付费产出就被永久锁死**。
    'stage7.rename':    NodeSpec('stage7', 'R-10', True,  'stage7 人名表',
                                  flags=('--dry-run', '--check-only', '--identity',
                                         '--recheck', '--apply-edit', '--revalidate',
                                         '--emit-edit-template', '--adopt-residual',
                                         '--actor', '--reason'),
                                  produces=(artifact_paths.STAGE7_NAME_MAP,)),
    # ⚠⚠ 本层没有候选序号：一本书只有一份人物表，定稿是「给整份盖章」。
    #   但审批动作必须携带统一人工凭据，并用 decision-surface SHA-256 防旧页面误批。
    'stage7.approve':   NodeSpec('stage7', 'R-10', False, 'stage7 人名表定稿',
                                  flags=('--actor', '--reason', '--subject-sha256'),
                                  required_flags=('--actor', '--reason',
                                                  '--subject-sha256'),
                                  produces=(artifact_paths.STAGE7_NAME_MAP,)),
    # 成书只能消费已批准的人名表；声明顺序同时是队列和进度屏的唯一顺序权威。
    'stage7.assemble':  NodeSpec('stage7', 'R-10', False, 'stage7 替换与成书',
                                 produces=(artifact_paths.STAGE7_BOOK,)),
}

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
    ('stage3.style', '--revalidate'): ('--actor', '--reason'),
    ('stage4.plan', '--revalidate'): ('--actor', '--reason'),
    ('stage5.write', '--revalidate'): ('--actor', '--reason'),
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
# **新一轮成稿**（`stage5/{mode}/chNN.json`，带 `round` +1 与 `revision_of`），
# 不是新一份验收报告。挂在 `stage5.review` 上会让一个 `paid=False` 的节点
# 产出另一份产物，而那与它自己的 `produces` 说的不是一回事——
# **完成度、断点续跑、`would_write` 三处都从 `produces` 派生**，它们会一起说假话。
# 顺带：`--revise` 要按臂分岔（闭卷/开卷的 SYS 不同），而 `stage5.review` 没有 `modes`。
#
# 顺带：原型 `n5_write --revalidate` 自述 0 成本，而它的 `FREE_FLAGS` 白名单里**没有它**
# ⇒ 现役界面把一个不花钱的动作画成橙色并弹二次确认。本表已把它补进 `stage5.write.flags`。

#: 八个当前产物层。**永不用作作业 id**——那一轴归 `NODES`。
#: 它承重在产物目录名、schema 文档名、Pydantic 模块名、`Rule.stage` 与校验报告路由。
STAGES: Final = tuple(f'stage{i}' for i in range(8))

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
#: 旧实现手抄了 14 步，漏掉 `stage0.edit` / `stage2.blueprint` / `stage3.style` /
#: `stage4.context`；真实 UI 与队列跑到这些节点时，进度屏因此仍显示「还没跑到」。
#: `NODES` 的声明顺序已经是队列与断点续跑的处理链顺序，进度屏只消费同一顺序；
#: 修复回路仍按节点归位，不因 flags/mode 另造一步。
PIPELINE_STEPS: Final = tuple(NODES)

#: 信标事件的两态。**`start` 不带退出码、`end` 必带**——由
#: `contracts/schema/orchestration.py::ProgressEvent` 的后置校验钉住。
PROGRESS_PHASES: Final = ('start', 'end')

#: 一步的四种观察态。**它是观察不是判定**（`R-13.6`）：`ok` 只说明「那条命令
#: exit 0」，而 exit 0 ≠ 合格（`F-087`）——「这层过没过」的唯一判官仍是
#: check 报告的 badge。消费方（路由/界面）不许把信标渲染成判定。
BEACON_STATES: Final = ('pending', 'running', 'ok', 'failed')

#: agent 代跑事件的三态。与 `JOB_EVENT_KINDS` 分开：这边没有 `step`
#: （agent 进程内部干什么不由我们记——那是信标的事），只有生死与改判。
AGENT_RUN_EVENT_KINDS: Final = ('started', 'ended', 'reconciled')
