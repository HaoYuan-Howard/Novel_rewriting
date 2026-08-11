#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
errors.py —— 显式终态的**唯一**词汇表。

【为什么不用 `sys.exit`】`SystemExit` 是 `BaseException`，它**穿透 `except Exception`**。
原型每个节点都拿 `sys.exit` 当终态，于是任何想「跑一批书、一本崩了继续下一本」的
调用方都拦不住它——只能把节点跑成子进程。那是原型被迫走子进程的五条理由之一
（`R-11.3` 的理由改写见 `docs/REQUIREMENTS.md`：这一条已被本模块 + `novel/run.py` 消灭，
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


class NovelError(Exception):
    """本运行时抛出的一切的基类。**不直接抛它**，见模块 docstring 末尾那条。"""


# ─────────────────────────────────────────────────────────────────────────────
# 一、显式终态 —— 预期内的「这一步不该继续」
# ─────────────────────────────────────────────────────────────────────────────

class TerminalState(NovelError):
    """显式终态（勿静默）。**不是 bug**，是这条流程按设计走不下去了。

    ⚠ 它与「静默降级」是一对反义词：本项目实测过的最贵一次损失，
    是一条闸门缺料时**照常打绿灯**（13 格判据打 13 个 `[OK]`，其中 6 条根本没跑）。
    ⇒ 走不下去就说出来，别产一份看起来正常的东西。
    """


class UpstreamMissing(TerminalState):
    """要读的上游产物不在盘上。

    ⚠ 与 `ArtifactUnreadable` **必须分开**：「上游还没跑」是流程问题（去跑它），
    「上游读不出来」是产物坏了（去看它）。混成一条，报告会把「先跑 stage2」
    说成「stage2 的产物损坏了」。
    """


class ArtifactUnreadable(TerminalState):
    """盘上那份在，但读不出来：不是合法 JSON、或不合当前 schema。

    ⚠ **不许兜底成「当它不存在」**。半截 JSON 被读成「上游还没产出」而继续等，
    会把一次**成功的付费跑**说成失败——原型并发落盘时踩过，修法是原子写（`store.write`）。
    """


class GateBlocked(TerminalState):
    """落盘闸门拦下了这一份：**没有覆盖已有产物**，问题稿已存 `.INVALID`。

    ⚠ 它携带 `invalid_path` 是**刻意的**：这条终态的全部价值在于
    「稿子没有丢，也没有再花钱」——报告必须说得出那份稿在哪，
    否则人会以为要重跑（那是一次真金白银）。0 成本提升路见 `store.promote_invalid`。
    """

    def __init__(self, message: str, *, invalid_path: str) -> None:
        super().__init__(message)
        self.invalid_path = invalid_path


class ApprovalRequired(TerminalState):
    """人工闸门没过（`APPROVAL_STATE` 的 `not_run` / `pending`）。

    ⚠ **两个未批状态都要拦**（`R-0.3b`）：写成 `if approved == 'pending'` 会漏掉
    `not_run`——那正是把三态压成 `bool` 之后必然长出来的那种漏。

    ⚠ 队列里的付费项没拿到一次性预先确认（`R-13.3`）也走这一条：
    「排进队列了」不该变成静默扣费。
    """


class UnsupportedRunMode(TerminalState):
    """这个节点没有这条跑法：未知节点 / 它不接受这个 flag / 带值 flag 没给值。

    ⚠ **归 `TerminalState` 不归 `RefusedByStructure`**，因为最常见的产地是**人**
    （在终端敲了一条这个节点没有的跑法），而不是代码自相矛盾。
    ⇒ 报错要说人话，并把这个节点**真正接受的那几个**列出来。

    ⚠ 拒收发生在 `build_argv` 里，而**展示与执行共用它**（`R-11.8`）——
    于是「界面上摆出来的那条命令」不可能是一条拼错的命令。
    """


class RunModeNotImplemented(TerminalState):
    """这条跑法登记在案（`NodeSpec.flags`），而本仓还没有接它的线。

    ⚠ 与 `NodeNotPorted` 分开是有用的：「整层还没移植」与「这一层在、这条跑法还没接」
    对人的下一步是不同的指示。合成一条，报错就只剩「跑不了」三个字。
    ⚠ 与 `UnsupportedRunMode` 更要分开：那一条说的是**这个节点没有这条跑法**
    （敲错了），这一条说的是**有，但还没实现**。前者该改命令，后者该改代码。
    """


class NodeNotPorted(TerminalState):
    """这个节点在取值面里，但本仓还没有它的实现（`contracts.rules.PORTED_STAGES`）。

    ⚠ **取值面要全、能不能跑是另一件事**，两者不许合并：
    `NODES` 缺一个节点，`/api/enums` 就少一个取值、队列就排不出那一步、
    正向覆盖率就少查一节；而「今天跑不动」只该在**真去跑它**的那一刻说出来。
    ⇒ 缺实现是一句人话，不是一段栈。
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
    """字节形态产物（`clean_text.txt` / `draft.md` / `book.txt`）不合字节纪律。

    ⚠ 盘上三份字节形态产物**全是 LF-only**，而 Windows 上 `open(p, 'w')` 默认吐 CRLF。
    归一是**内容决策**，属产地；写盘口只负责保证**平台不掺东西**，
    并在拿到 `\\r` 时当场说出来——静默归一等于替产地做了一次它自己没记账的改写。
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
    `stage5/deep/ch01.json` 记进 `inputs` 会塌成一个**反查不到文件**的键，
    于是那条边**永远显示对得上**——`一个假的匹配比没有记录更坏`。
    成稿走 `store.read_judged`（存完整相对路径），见 `_common.md` §3.2。
    """
