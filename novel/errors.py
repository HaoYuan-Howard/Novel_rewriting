#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
errors.py —— 显式终态的**唯一**词汇表。

【为什么不用 `sys.exit`】`SystemExit` 是 `BaseException`，它**穿透 `except Exception`**。
原型每个节点都拿 `sys.exit` 当终态，于是任何想「跑一批书、一本崩了继续下一本」的
调用方都拦不住它——只能把节点跑成子进程。那是原型被迫走子进程的五条理由之一
（`R-11.3` 的理由改写见 `docs/BACKEND_CONTRACT.md`：这一条已被本模块 + `novel/run.py` 消灭，
而**走子进程这条规则仍然留着**，靠另外三条独立成立的理由）。
⇒ 运行时一律抛这里的异常；**只有 `novel/cli/` 那一层**可以把它翻译成退出码
（`tests/test_layering.py::test_the_runtime_never_uses_sys_exit` 钉着这条）。

【两族，分界线是「谁的错」】
  · `TerminalState` —— **数据/流程**说这一步不该继续（上游没跑、闸门拦下、人没批）。
    它是**预期内**的终态，报告里要说人话，退出码非零，但它不是 bug。
  · `RefusedByStructure` —— **代码**自相矛盾（来源账里 prompts 非空而 inputs 是空的、
    0 成本节点想记一个 model、字节形态产物里混进了 `\\r`）。
    它是 bug，栈要留全。⚠ **它绝不允许被降级成一句警告**——
    原型那三处写盘口正是 `print` 一句就照写，于是假账全部落了盘。

⚠ **不设「默认异常」**。每一处终态都要选一个具体的类；选不出来说明那个失败还没想清楚，
而 `raise NovelError('...')` 会让调用方永远只能靠读字符串来分辨发生了什么
（`Q-6`：措辞是会改的，措辞不是契约）。
"""
from __future__ import annotations

from collections.abc import Sequence


class NovelError(Exception):
    """本运行时抛出的一切的基类。**不直接抛它**，见模块 docstring 末尾那条。

    【`message` 与 `hint` 是两件事，落在两个字段上】
      · `message` —— **发生了什么**（一句人话）。
      · `hint`    —— **下一条命令**（人照着敲就能解开这条终态的那一句）。

    ⚠ 此前两者是拼在一个字符串里的，于是**「有没有出路」这件事不可判定**：
    调用方拿到的是一段中文，只能靠读它去猜里面有没有藏一条命令。CLI 那一侧看不出
    差别（都打在一行上），HTTP 这一侧就露馅了——`ErrorBody` 早就有 `hint` 这一格、
    前端也一路解析进 `ApiError.hint`，而**后端只有 CSRF 那一处在填**，
    于是界面永远只能把整段话糊在正文里。

    ⚠ `hint=None` 是**有含义的**，不是「忘了写」：它说的是「说不出下一条命令」。
    `ErrorBody.hint` 的原话就是这条——**说不出来就别编一句**。
    一条编出来的「试试重跑」比没有更坏：人会照着做，然后再花一次钱。

    ⚠ 它不动「不设默认异常」那条：`hint` 是**载荷**，类才是身份。
    别拿「反正 hint 说得清」当借口去抛一个笼统的类（`Q-6`）。
    """

    def __init__(self, message: str, *, hint: str | None = None) -> None:
        super().__init__(message)
        self.hint = hint


# ─────────────────────────────────────────────────────────────────────────────
# 一、显式终态 —— 预期内的「这一步不该继续」
# ─────────────────────────────────────────────────────────────────────────────

class TerminalState(NovelError):
    """显式终态（勿静默）。**不是 bug**，是这条流程按设计走不下去了。

    ⚠ 它与「静默降级」是一对反义词：本项目实测过的最贵一次损失，
    是一条闸门缺料时**照常打绿灯**（13 格判据打 13 个 `[OK]`，其中 6 条根本没跑）。
    ⇒ 走不下去就说出来，别产一份看起来正常的东西。
    """



class ParagraphPlanInfeasible(TerminalState):
    """逐 slot 段落计划在任何 wire 之前已不可能完成。

    Stage5 的段落计划按「每个场景至少一段」分配章节目标段数；当场景
    （slot）数本身就超过合法段数上限时，把任务发出去只会买到必然的
    `paragraph_budget_missed` 拒稿。⇒ 在构造下发面时显式终态，而不是
    花一轮钱确认一个代码早就知道的死局。
    """

class ResponseRepairRequired(TerminalState):
    """一份已付费结构化响应可受托修复；原节点停在下一采之前。

    它不是 `LlmFailed`：采样链尚未耗尽，也不是 `GateBlocked`：没有正式产物或
    `.INVALID`。固定候选面提交/放弃后，重跑**同一原节点**才能继续状态机。
    """

    def __init__(self, message: str, *, attempt_id: str, candidate_path: str,
                 original_path: str, hint: str) -> None:
        super().__init__(message, hint=hint)
        self.attempt_id = attempt_id
        self.candidate_path = candidate_path
        self.original_path = original_path


class ResponseRepairBatchRequired(TerminalState):
    """同一 Stage2 并发波次已收齐为一份不可变 workpack，等待一次人工决定。"""

    def __init__(self, message: str, *, run_id: str, manifest_path: str,
                 attempts: tuple[str, ...], hint: str) -> None:
        super().__init__(message, hint=hint)
        self.run_id = run_id
        self.manifest_path = manifest_path
        self.attempts = attempts


class BookTrashFailed(TerminalState):
    """项目无法安全移入回收站；正式书目录保持原位或仍可从回收站恢复。"""


class ApprovalFrozen(TerminalState):
    """这本书正在被移入回收站，审批写入不许再落盘。

    ⚠ 与 `JobConflict` 同一族「流程说这一步现在不该发生」：删除与审批是两个人
    （或两个标签页）同时点的按钮，不是 bug。落点在
    `novel/orchestrator/approvals.py::frozen_for_trash`，那里写着它防的是哪条竞态。
    """


class ContextBudgetRefused(TerminalState):
    """LLM 请求无法在已登记的供应商上下文预算内安全执行。

    这道终态发生在缓存、账本和网络之前。它既覆盖确定超限，也覆盖无法可靠计算
    （未知模型、tokenizer 缺失或固定资源校验失败）：后一类如果猜一个近似值放行，
    就会把配置错误变成一次可能付费的试探请求。
    """

    requirement = 'R-12.6'

    def __init__(self, message: str, *, model: str,
                 input_tokens: int | None = None,
                 max_tokens: int | None = None,
                 context_limit: int | None = None,
                 max_output_tokens: int | None = None,
                 tokenizer_revision: str | None = None,
                 hint: str | None = None) -> None:
        super().__init__(message, hint=hint)
        self.model = model
        self.input_tokens = input_tokens
        self.max_tokens = max_tokens
        self.context_limit = context_limit
        self.max_output_tokens = max_output_tokens
        self.tokenizer_revision = tokenizer_revision


class UpstreamMissing(TerminalState):
    """要读的上游产物不在盘上。

    ⚠ 与 `ArtifactUnreadable` **必须分开**：「上游还没跑」是流程问题（去跑它），
    「上游读不出来」是产物坏了（去看它）。混成一条，报告会把「先跑 stage2」
    说成「stage2 的产物损坏了」。
    """


class UpstreamStale(TerminalState):
    """要读的上游产物在盘上，但它已经不是当前根上那一份了。

    ⚠⚠ **它此前是 `ProvenanceRefused`，也就是「代码自相矛盾」的退出码 3。** 那是错的：
    这条拒绝**自带恢复命令**（「请先从 <producer> 重建当前根上的产物」），
    而 `novel/cli/exit_codes.py` 对两个码的定义是「2 = 数据/流程说这一步不该继续，
    不是 bug」「3 = 代码自相矛盾，是 bug，栈要留全」。一条人照着做就能解开的前置条件
    落进 3，等于每次都在说「这是我们的 bug」，而编排层与 agent 执行者会据此停机。

    ⚠ 与 `UpstreamMissing` 分开：「上游还没跑」（去跑它）与「上游跑过但已过期」
    （去**重跑**它，且下游缓存会失效）是两条不同的恢复路，报告不能混。
    ⚠ 与 `ProvenanceRefused` 分开：那一条留给来源账本身自相矛盾的情形
    （同一把刻度结算两次、缺全链根、根 sha1 分叉），那些确实是 bug。
    ⚠ 与 `UpstreamUnproven` 分开：**它只在 freshness 确定为 `stale` 时抛**。
    `unknown` 是「证明不了」，不是「变了」——见那个类。
    """


class UpstreamUnproven(TerminalState):
    """要读的上游产物在盘上，但来源账**证明不了**它出自当前根（freshness = `unknown`）。

    ⚠⚠ **与 `UpstreamStale` 分开的理由是「别把不知道说成知道」。** `stale` 是一句
    可复算的断言——`FreshnessReport.edges` 说得出**哪条边**变了；`unknown` 只是
    「这份产物没有足够来源事实」。把 `unknown` 也抛成 `UpstreamStale`，报告就会替一件
    算不出来的事编一句「它已经变了」，而那正是本仓五态建模要防的那种压缩。
    ⇒ 恢复指令也真的不同：`stale` 指得出重建哪一条（`--recheck` 常常就够），
    `unknown` 指不出，只能整份从 producer 重建（那可能是一次付费）。

    ⚠ **生产节点不抛它**：`novel/store.py::ArtifactStore._refuse_stale_upstream` 对
    `unknown` 明确放行（旧产物不能伪装成 fresh，但也不能凭空断言内容已变）。
    抛它的是**比那道闸更严**的下游闸——`stage7` 的交付闸要的是「证明得了当前」，
    `unknown` 在那里必须拦（`R-10.14`：txt 是交付物，里面没有地方写风险抬头横幅）。
    这一档严格性差异**必须看得见**；两处用同一个类，就把它藏进措辞里了。
    """


class ArtifactUnreadable(TerminalState):
    """盘上那份在，但读不出来：不是合法 JSON、或不合当前 schema。

    ⚠ **不许兜底成「当它不存在」**。半截 JSON 被读成「上游还没产出」而继续等，
    会把一次**成功的付费跑**说成失败——原型并发落盘时踩过，修法是原子写（`store.write`）。
    """


class GateBlocked(TerminalState):
    """落盘闸门拦下了那一份：**没有覆盖已有产物**，问题稿已存 `.INVALID`。

    ⚠ 它携带 `invalid_path` 是**刻意的**：这条终态的全部价值在于
    「稿子没有丢，也没有再花钱」——报告必须说得出那份稿在哪，
    否则人会以为要重跑（那是一次真金白银）。0 成本提升路见 `store.promote_invalid`。

    ⚠ **它是一个「状态」而不只是一个「事件」**：抛它的既可以是刚拦下的那个产地，
    也可以是**下游**——盘上还躺着一份没裁决的 `.INVALID`，于是定稿 / 组装 / 批准
    都不该开工。两种情形的恢复路逐字相同（`--revalidate` 提升或 `--apply-edit` 改），
    ⇒ **不给它分子类**：没有任何消费方会在两者之间分叉，而为了区分「谁抛的」分子类，
    正是 `PathRefused` 那一格论证过的错解法。

    ⚠ `invalid_path` 指的是**那份被拦下的稿在哪**：一份时是文件，
    一批时（stage5 逐章）是装着它们的那个目录。它不保证是单个文件路径。

    ⚠ 「有 `.INVALID` 挡着」与「正式稿本身带着没消的 hard」是两回事，
    后者盘上没有 `.INVALID` 可指 ⇒ 走 `BlockingFindingsUnresolved`。
    """

    def __init__(self, message: str, *, invalid_path: str,
                 hint: str | None = None) -> None:
        super().__init__(message, hint=hint)
        self.invalid_path = invalid_path


class BlockingFindingsUnresolved(TerminalState):
    """盘上那份是**正式稿**，但它带着还没消的阻断级（`hard`）判定。

    ⚠⚠ **与 `GateBlocked` 分开，因为盘上没有 `.INVALID` 可指。** `GateBlocked` 的全部
    价值在那个 `invalid_path`（「稿子没丢，也没有再花钱」）；这一条的正式稿好好地在
    live 路径上，被拦住的是**下一步**（定稿 / 组装 / 交付）。硬拿 `GateBlocked` 抛它，
    要么编一个不存在的路径，要么漏掉那个必填参数——后者的实测下场是 `TypeError`，
    退出码 **70**「崩了，结论未知」，而它本来是一条退出码 2 的预期内终态。

    ⚠ `rules` **必填且 typed**：那份 rule key 列表就是 `novel/report/builder.py::
    remediation_of` 的路由输入与待办清单的条目。写成 `f'还有 {hard} 条'` 等于让消费方
    去 parse 一个 list 的 repr——`Q-6`：措辞是会改的，措辞不是契约。

    ⚠ 恢复路是**逐条修判据命中**（`--apply-edit` 改一行 / `--recheck` 按当前判据重判），
    不是提升（那是 `GateBlocked`）、不是盖章（那是 `ApprovalRequired`）、
    也不是重建上游（那是 `UpstreamStale` / `UpstreamUnproven`）。
    """

    def __init__(self, message: str, *, rules: Sequence[str],
                 hint: str | None = None) -> None:
        super().__init__(message, hint=hint)
        self.rules = tuple(rules)


class ApprovalRequired(TerminalState):
    """人工闸门没过（`APPROVAL_STATE` 的 `pending`）。

    ⚠ 执行资格的**唯一落点**是
    `novel/stages/stage0/eligibility.py::is_stage0_executable`，消费方不许复述条件
    （2026-08-22 删掉 `auto_zero_hit` 之后它恰好等于 `approval.status == 'approved'`，
    但收口的理由是「同一条 R 只有一个守卫」，不是条件复杂）。
    「没跑过」不是产物状态：产物不存在本身就是那个事实（2026-08-21 起 `not_run` 已删）。

    ⚠ 队列里的付费项没拿到一次性预先确认（`R-13.3`）也走这一条：
    「排进队列了」不该变成静默扣费。
    """


    requirement = 'R-0.3b'


class DeliveryIncomplete(TerminalState):
    """这本书还没到可交付状态：计划有而成稿无 / 开篇文案未定稿。

    ⚠⚠ **一次报全，不是逐条失败。** `R-10.14` 的原话是「txt 是交付物，里面没有地方写
    风险抬头横幅」——正因为成书里写不下警告，这道闸必须在拒绝的那一刻把差的东西
    一次说清；拆成 first-match 之后，人补完缺章才发现文案还没定稿，
    **一道 0 成本的闸要走两个来回**。
    ⇒ 两项各自 typed，消费方按字段路由（去跑 stage5 /
    `stage6.approve --pick N`），而**不是**去 parse 那段人话（`Q-6`）。

    ⚠ **为什么是一个类而不是两个**：这两条不是两种失败，是同一件失败的两种量法
    （同 `UpstreamStale` 覆盖 freshness / 消费指纹漂移 / 规模账不符）。差别在于
    **哪一项要补**，那是数据不是类型。⇒ 拆开能给的机器可读性，字段一样给得出。
    ⚠ 它与本函数前面那串 first-match 的闸不同**是有理由的**：那串是有序依赖
    （没有批准就无从比对批准对象），这两条同时成立且互不依赖。

    ⚠ 与 `UpstreamMissing` 分开：那一条说「上游那份产物不在盘上」（去跑那一层），
    这一条的上游都在，是**内容还没齐**。
    ⚠ `invalids`（被闸门拦下的废稿）**不在这里**：`R-10.14b` 明说它是「排除但要报计数」，
    那是报告的一行，不是拒绝交付的理由。
    """

    requirement = 'R-10.14'

    def __init__(self, message: str, *, missing: Sequence[int], hook_unapproved: bool,
                 hint: str | None = None) -> None:
        super().__init__(message, hint=hint)
        self.missing = tuple(missing)
        self.hook_unapproved = hook_unapproved


class IngestTerminal(TerminalState):
    '''stage0/stage1 的数据终态；子类用 requirement 给机器稳定分类。'''

    requirement: str


class SourceDecodeFailed(IngestTerminal):
    '''原始小说无法按契约候选无损解码、含 NUL，或正文为空。'''

    requirement = 'R-0.11'


class SourcePathRejected(IngestTerminal):
    '''源文件名推导出的书目录不能被同一把尺子无损解析回来。'''

    requirement = 'R-0.13'


class SourceConflict(IngestTerminal):
    '''同一目标目录已有另一份正文，未获显式覆盖许可。'''

    requirement = 'R-0.14'


class HumanEditProtected(IngestTerminal):
    '''stage0 重跑会覆盖人工编辑，未显式强制时拒绝。'''

    requirement = 'R-0.8'


class Stage0BodyRejected(IngestTerminal):
    '''清理后保留正文不足六成，不能把结果递给下游。'''

    requirement = 'R-0.9'


class PartitionInputMismatch(IngestTerminal):
    '''stage1 真正读到的 clean_text 与 stage0 批准的指纹不一致。'''

    requirement = 'R-1.3'


class PartitionChapterMismatch(IngestTerminal):
    '''分章标记与可写入章表无法保持一一对应。'''

    requirement = 'R-1.5'


class ResidualNonbody(IngestTerminal):
    '''已批准的 clean_text 里仍能识别出非正文标记。'''

    requirement = 'R-1.7'


class HookCopyMissing(IngestTerminal):
    '''第一个正文章标记之前没有非空开篇文案段。'''

    requirement = 'R-1.12'


class TooFewChapters(IngestTerminal):
    '''正文章数少于契约下限。'''

    requirement = 'R-1.13'


class UnsupportedRunMode(TerminalState):
    """这个节点没有这条跑法：未知节点 / 它不接受这个 flag / 带值 flag 没给值。

    ⚠ **归 `TerminalState` 不归 `RefusedByStructure`**，因为最常见的产地是**人**
    （在终端敲了一条这个节点没有的跑法），而不是代码自相矛盾。
    ⇒ 报错要说人话，并把这个节点**真正接受的那几个**列出来。

    ⚠ 拒收发生在 `build_argv` 里，而**展示与执行共用它**（`R-11.8`）——
    于是「界面上摆出来的那条命令」不可能是一条拼错的命令。
    """


class QueueConflict(TerminalState):
    """A persistent queue operation conflicts with the item's current state.

    Most commonly this means attempting to cancel an item whose subprocess has
    already started.  A running LLM call cannot be represented as a lossless
    cancellation, so the queue refuses instead of rewriting history.
    """


# ─────────────────────────────────────────────────────────────────────────────
# 二、结构性拒绝 —— 代码自相矛盾，是 bug
# ─────────────────────────────────────────────────────────────────────────────

class RefusedByStructure(NovelError):
    """写盘口/读盘口拒绝了一次自相矛盾的操作。**这是 bug，不是数据问题。**"""


class ProvenanceRefused(RefusedByStructure):
    """来源账自相矛盾，拒绝落盘。

    ⚠⚠ **原型这里是 `print` 一句警告然后照写**，于是：
    `chapter_blueprints` 漏记 global · `style_grounding` 与 `fidelity_reference`
    的 `inputs` 干脆是空的。一份自称能证明来源的记录悄悄漏掉一个来源，
    **比没有这个记录更坏**——下游会拿它当证据。
    ⇒ 改成拒绝。两道闸的定义见 `novel/store.py::ArtifactStore._refuse_bad_provenance`。
    """


class AccountingKeyUnregistered(RefusedByStructure):
    """产地记了一笔账，而 `novel/report/accounting.py::ACCOUNTING` 里没有这个键。

    ⚠⚠ **它是「记了一笔谁也读不到的账」**：那个数已经写进 `_provenance`，人却永远看不见。
    原型正是这个下场——13 个记账键里 6 个 0 出口（stage2 的 8 个有 5 个、stage3 的 3 个
    **一个都没有**）。`Q-1` 的那句话是「**记账不是终点，报出来才是**」。
    ⇒ 加一个记账键的成本必须是「同时给它一个出口」，否则加不进去。

    ⚠ **为什么归到 `RefusedByStructure` 而不是 `TerminalState`**：`_common.md` §九
    那张表写的是「显式终态」，而那是**口语义**（＝别静默）。按本模块开头那条分界线
    ——「谁的错」——记账与声明表是**同一批代码**的两处，对不上就是代码自相矛盾，
    不是数据说走不下去了。⇒ 是 bug，栈要留全。
    这一处细化登记在 `docs/schema/_report.md` §六。
    """


class ByteFormRefused(RefusedByStructure):
    """字节形态产物（`clean_text.txt` / 成书）不合字节纪律。

    ⚠ 盘上三份字节形态产物**全是 LF-only**，而 Windows 上 `open(p, 'w')` 默认吐 CRLF。
    归一是**内容决策**，属产地；写盘口只负责保证**平台不掺东西**，
    并在拿到 `\\r` 时当场说出来——静默归一等于替产地做了一次它自己没记账的改写。
    """


class ValueDomainRefused(RefusedByStructure):
    """`/api/enums` 的发布器认不出一个取值面的形状，或它一个桶都没落进去。

    ⚠⚠ **它存在的全部意义是不兜底。** 发布器如果对认不出的东西
    `json.dumps` 一下就发出去，`NodeSpec` 这种 `NamedTuple` 会被静默写成一个
    **位置数组**——前端拿到 `["stage3","R-4",false,"stage3 机械基线",...]`，
    字段名全没了，而没有任何一层会出声。那是判据 26 说的
    「给违约数据发的静默通行证」，只是这次发的是一整份取值面。

    ⚠ 另一半：**新加一个常量却没决定发不发**也走这一条。方向是有意的——
    手写「发布清单」会**静默地烂**（新常量前端拿不到 ⇒ 前端自己硬编码一份，
    而那正是 `invariants` 存在的理由要防的事）；手写「不发登记表」会**响亮地烂**
    （新常量落不进任何桶 ⇒ 当场抛）。两种烂的成本相同，方向相反。
    """


class PathRefused(RefusedByStructure):
    """路径这一族的结构性拒绝，三种：

      ① **产物相对路径**不合法（绝对路径 / 越出书目录 / 层数不对）；
      ② **书 id** 不合法（空 / 带分隔符 / `..` / 带盘符）——见 `paths.check_sid`。
         id 是目录名的一整段，不合法的 id 拼在 `<Output>` 后面就是一个任意目录读写入口，
         而它**不会因为等一会儿就变合法**（所以它不是 `UpstreamMissing`）；
      ③ **一个 id 匹配到多个书目录**，或归档位已被占用——两者都是「这里有两份，
         而只有人能决定留哪个」，静默挑一个正是本项目要消灭的那种沉默。

    ⚠ 层数那一条不是洁癖：来源账的键是**两级**的（`_common.md` §3.1），
    多于两级的产物路径若记进 `inputs`，会塌成一个**反查不到文件**的键，
    于是那条边**永远显示对得上**——`一个假的匹配比没有记录更坏`。
    成稿走 `store.read_judged`（存完整相对路径），见 `_common.md` §3.2。
    """
