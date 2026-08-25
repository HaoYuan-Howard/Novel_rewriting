#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
stage0.py —— 对应 `docs/schema/stage0.md`。非正文剔除 + 人工审阅闸门，**不调 LLM**。

⚠ 另一份产物 `stage0/clean_text.txt` **不是 JSON**，它的 schema 是字节形态
（utf-8 无 BOM · 恒 `\\n` · 只删段不改字）。字节形态的守卫在写盘口那一层
（`novel/store.py::write_text`），不在这里。

【本层唯一没有 `_provenance` 的理由，写在明处】
`docs/schema/stage0.md` 的产物表里**没有 `_provenance` 这一项**，本模块因此也没有。
它不是漏了：本层是全链的源头，`inputs` 恒空；原文身份由链根 `Stage0Source`
表达，producer 代码身份逐 revision 记录。

── 2026-08-22 阶段 C：**一个可变格 + 一条不可变链** ─────────────────────────

【之前是什么样】本层曾是一个小型版本控制系统：`machine_snapshot.json`（机器那一轮的
完整快照）· `merge_candidate.json`（base/ours/theirs 三份全文 + 码点级 diff3 的 typed
冲突）· `_audit/edit_N.json`（SHA-256 串联的决定账，同时充当跨文件提交的 WAL）·
`_rounds/`（正式稿逐轮归档）。四套机制守的是**同一件事**：

  > `clean_text.txt` 与 `removal_report.json` 必须同进同出，而文件系统只保证单文件原子。

于是有了 WAL、有了「唯一可恢复条目」、有了 `_completed_merge` 这类崩溃重放状态机；
而 diff3 只合并了 `clean_text` 一个字段，**`removed[]` 账本怎么合并没有答案**
（外部评审 P0-3：接受 `proposed_clean_text` 之后正式 `removed[]` 该写什么，
四种选择全都不正确）——因为三方合并的聚合根根本不是单独的正文，而是
`{原文身份, 正文, 剔除账本, 告警}` 这一整团。

【现在是什么样】聚合根 = `Stage0Revision`，**整团一起不可变**：

  · `stage0/revisions.json` 是本层**唯一可变的那一格**：不可变 revision 链 +
    `current` 指针 + 待裁决候选 + 审批状态，一次原子单文件写就是一次提交
    ⇒ **跨文件提交这件事本身消失了**，WAL 与崩溃重放状态机随之无事可守；
  · `clean_text.txt` 是 `current` 的唯一物化投影，任何时刻都可按
    `revisions.json` 幂等重建；派生报告不落盘。
  · 机器重跑不再自动三方合并：它只是链上又一条 revision。与当前稿不同就挂成
    **候选**，由人三选一（保留当前 / 换成候选 / 换成候选后再编辑）。
    ⇒ diff3、冲突 schema、决议 schema、base/ours/theirs 三份全文副本一并出局。
  · 审计链折叠进 revision：每条 revision 记 `parent_revision_id` + 起源 + 自报
    actor/reason，`revision_id` 由**前像**（含 parent）派生 ⇒ **链本身就是哈希链**，
    改一个字就对不上，且不再需要与报告顶层的 `audit_seq` / `audit_head_sha256`
    两格跨文件对账（那两格随之出局）。
"""
from __future__ import annotations

from typing import Annotated, Literal

from pydantic import Field, StringConstraints, model_validator

from contracts.invariants import (
    APPROVAL_STATE, NONBODY_KINDS, REMOVED_ORIGIN,
)
from contracts.schema.base import Sha1, StrictModel
from contracts.schema.common import HumanApproval

#: 闸门两态的取值面投影。判别联合的两个 `status` 字面量必须与它对上
#: （`tests/test_invariants.py`）。
ApprovalState = Literal[APPROVAL_STATE]
Sha256 = Annotated[str, StringConstraints(pattern=r'^[0-9a-f]{64}$')]

#: 一条 revision 是**怎么来的**。三支，且各自的可问责字段不同（见 `Stage0Revision`）：
#:   · `machine`     —— 机器剔除算出来的，没有人参与 ⇒ 没有 actor / reason；
#:   · `human_edit`  —— 人改了正文或改了圈选（`R-0.4`），**正文含人工痕迹**；
#:   · `human_adopt` —— 人把一条既有 revision（候选）采纳为当前稿。
#:     ⚠ 它的**内容**逐字等于被采纳那条，人工痕迹按被采纳那条算；
#:     它单独成一条 revision 的理由是「谁在何时以什么理由换掉了正文」必须留痕
#:     ——审计链折叠进来之后，这就是它唯一的落点。
Stage0RevisionOrigin = Literal['machine', 'human_edit', 'human_adopt']


# ── 原文住在哪儿：**两种来源，处置相反** ────────────────────────────────────
#
# 【2026-08-22 之前是什么样】一格 `original_path: str`（绝对路径），读取端拿到它之后
# 靠一套「先试记录的路径、断了就猜猜看是不是 `_service/uploads/<sha256>/<name>` 那种
# 形状」的**隐式重锚**逻辑去找回原文。问题不在那套逻辑本身（它是对的），
# 在于**它必须去猜**：同一个字符串既可能是外部语料的路径、也可能是本服务自己
# 内容寻址目录下的路径，而这两者被删/被移的含义完全相反——
#   · 外部语料没了 = **世界真的变了**，`ghost` 是正确答案；
#   · 界面上传的书「没了」= 多半只是 Output 根搬了家，文件一个字节没变，
#     报 ghost 是**假阴性**（实测发生过：《夫君与鲛人孰》）。
# ⇒ 把「它是哪一种」从**猜**变成**产地当时就写下来**。读取端不再有回落分支：
#   external_file 就按绝对路径找，找不到就是真没了；
#   managed_upload 就按内容地址在当前 base 下解析，且仍然自证 `sha256(raw) == 目录名`。
class ExternalFileRef(StrictModel):
    """外部语料：一条书目录之外的绝对路径。

    被删被移就是世界真的变了，读取端不许猜另一份同名字文件。
    """

    kind: Literal['external_file']
    absolute_path: str = Field(min_length=1)


class ManagedUploadRef(StrictModel):
    """界面上传的书：住在 `<base>/_service/uploads/<blob_sha256>/<filename>`。

    ⚠ **它是内容寻址的**，所以跟着 base 走：Output 根搬家只会让当年那条绝对路径失效，
    文件还在、字节没变。⇒ 这一支不记绝对路径，只记「内容是什么 + 叫什么名字」，
    此刻在哪由当前 base 现算（`novel/paths.py::uploads_dir`）。
    """

    kind: Literal['managed_upload']

    #: 原始字节的 sha256，**同时就是它所在的目录名**（产地唯一：
    #: `novel/orchestrator/uploads.py::save_upload`）。读取端拿到候选必须复算相等
    #: 才采信——这是「回落永远不可能把另一份文件冒充成原文」的全部依据。
    blob_sha256: Sha256

    filename: str = Field(min_length=1)


#: 原文此刻该去哪儿找。⚠ 读取端的唯一入口是
#: `novel/core/source.py::resolve_recorded_source`，别在调用方各自展开这个联合
#: ——那会变成四份会各自漂的实现。
SourceRef = Annotated[
    ExternalFileRef | ManagedUploadRef, Field(discriminator='kind')]


class RemovedSegment(StrictModel):
    """已验证的剔除事实；命令中的用户意图使用 ``RequestedRemoval``。

    只保存不可再生的决定与最小定位证据。行号、长度和文本预览从原文与 span 现算。
    """

    kind: Literal[NONBODY_KINDS]

    #: 标记行原文（strip 后）。它是唯一保留的可读诊断；其余定位事实都由 span 现算。
    #:
    #: ⚠ **它与 `core`（剥壳后）是两个量纲，别混着用**：所有阈值判的都是 `core`
    #: ——剥掉前导 `【[(（`/数字、剥掉尾部标点之后剩下的部分。
    #: `NONBODY_HEAD_MAX_CP`(20) 与告警线 `NONBODY_MARKER_WARN_CP`(17) 判的都是 `core`。
    marker_line: str = Field(min_length=1)

    #: 码点半开区间，**坐标系是原始 txt**，不是 `clean_text`。
    #:
    #: ⚠ 审阅屏要在**原文**上画高亮，而 `clean_text` 里那段已经没了。
    #: 混用坐标系是这类设计最常见的 bug，而且它**不会报错**——
    #: 只会把高亮画到错误的位置上。
    span: tuple[int, int]

    origin: Literal[REMOVED_ORIGIN]

    @model_validator(mode='after')
    def _span_is_a_half_open_interval(self) -> RemovedSegment:
        lo, hi = self.span
        if not 0 <= lo < hi:
            raise ValueError(f'span={self.span} 不是合法的半开区间（要求 0 <= 起 < 止）')
        return self


class RequestedRemoval(StrictModel):
    """人工编辑命令中的剔除意图；事实字段由执行端重读原文后落定。"""

    kind: Literal[NONBODY_KINDS]
    span: tuple[int, int]

    @model_validator(mode='after')
    def _span_is_a_half_open_interval(self) -> RequestedRemoval:
        lo, hi = self.span
        if not 0 <= lo < hi:
            raise ValueError(f'span={self.span} 不是合法的半开区间（要求 0 <= 起 < 止）')
        return self


# ── 可疑项：按 current 内容现算的结构化证据，不落 revision ───────────────────


class EarlyCutWarning(StrictModel):
    """正文占比过低。与 `R-0.9` 的 FAIL 是同一件事的两个出口：FAIL 阻断、本条解释原因。"""

    kind: Literal['early_cut']
    body_code_points: int = Field(ge=0)
    total_code_points: int = Field(ge=1)


class DropRatioWarning(StrictModel):
    """剔除段占全文比例过高。与 `early_cut` 几乎同义，独立起作用的窗口很窄。"""

    kind: Literal['drop_ratio']
    removed_code_points: int = Field(ge=1)
    total_code_points: int = Field(ge=1)


class LongMarkerWarning(StrictModel):
    """某条剔除记录的标记 core 超过实测真标记上限。⚠ 判的是 `core` 不是 `marker_line`。"""

    kind: Literal['long_marker']
    span_start: int = Field(ge=0)
    marker_core_code_points: int = Field(ge=1)


#: 一条可疑项。⚠ 它是**判别联合**，不能直接实例化——产地必须选一个具体类，
#: 这正是「每一类的证据字段各不相同」的意思。取值面与 `STAGE0_WARNINGS` 的对账在
#: `tests/test_invariants.py`，那条测试是这三个 `Literal` 不变成第二落点的全部保证。
Stage0Warning = Annotated[
    EarlyCutWarning | DropRatioWarning | LongMarkerWarning,
    Field(discriminator='kind'),
]


# ── 审阅闸门：**两个分支，不是四个可空字段** ──────────────────────────────
#
# 【2026-08-22 之前是什么样】顶层平铺四格：
#   `approved` + `approved_at` + `approval_origin` + `approved_by`
# 其中后三格全部可空，靠一个 20 行的 `model_validator` 维持它们之间的互斥关系：
# 「approved 才该有时间」「approved 必须说得出来源」「human 必须给 approved_by」
# 「auto_zero_hit 不得伪造 approved_by」「未批准不得保留 origin/by」。
# ⇒ 五条规则守的其实是**一件事**：这里有两种状态，而它们的字段集不一样。
#   用四个可空字段表达两个分支，就是把类型系统能做的事挪进了 validator，
#   而 validator 只在构造那一刻起作用——`model_copy(update=...)` 绕得过去。
#
# ⇒ 收成判别联合之后，「pending 却带着批准时间」这种形态**在类型上就构造不出来**，
#   而不是构造得出来但会被拦下。
class PendingApproval(StrictModel):
    """还没人批。

    ⚠ **这个分支一个字段都没有，这正是重点**：「没批」说不出批准时间、说不出来源、
    说不出批准人。上一版用三个 `None` 表达这件事，于是「未批准却带着 approved_by」
    是一个需要被拦下的形态；现在它压根写不出来。
    """

    status: Literal['pending']


class ApprovedApproval(StrictModel):
    """已经批过；批准只有人工入口，因此 ``by`` 与 ``reason`` 恒必填。"""

    status: Literal['approved']

    #: ISO 时间戳。批过就一定说得出是什么时候批的。
    at: str = Field(min_length=1)

    #: 自报身份，不宣称是认证身份。
    by: str = Field(min_length=1)

    #: 批准理由。⚠ 2026-08-22 阶段 C 从审计条目搬进来：审计链折叠进 revision 之后，
    #: 「批准」是唯一一件**不改变正文**因而不产生新 revision 的人工决定 ⇒ 它的
    #: reason 没有别的落点。此前 `stage0.approve` 强制要 `--reason`，却只把它写进
    #: `_audit/edit_N.json`；删掉那条链而不搬这一格，理由就丢了。
    reason: str = Field(min_length=1)

    @model_validator(mode='after')
    def _actor_is_not_blank(self) -> ApprovedApproval:
        # ⚠ `min_length=1` 拦不住全空白串，而「批准人叫『   』」与没有批准人等价。
        if not self.by.strip():
            raise ValueError('人工批准必须给 by（自报身份）')
        if not self.reason.strip():
            raise ValueError('人工批准必须给 reason')
        return self


#: 一次 stage0 审阅闸门的状态。取值面与 `APPROVAL_STATE` 的对账在
#: `tests/test_invariants.py`（同 warning 判别联合：那两个 `Literal` 与元组之间
#: 没有任何语言级联系，对账是唯一的连接）。
Stage0Approval = Annotated[
    PendingApproval | ApprovedApproval, Field(discriminator='status')]


# ── 聚合根：一条源身份 + 多条正文 revision ───────────────────────────────────


class Stage0Source(StrictModel):
    """整条 revision 链共同的原文前提。

    原文没有随正文版本变化的语义；把这三格塞进每条 revision，只会重复数据并迫使
    validator 事后检查「全链同源」。提升到根之后，换原文在结构上就是另一条链。
    """

    source_ref: SourceRef
    normalized_source_sha1: Sha1
    total_code_points: int = Field(ge=1)


class Stage0RevisionContent(StrictModel):
    """一条 revision 的可变内容：正文 + 剔除账本。

    ⚠⚠ **它整团一起变，不许只换其中一格**——这正是外部评审 P0-3 的结论：
    此前的三方合并只对 `clean_text` 做 diff3，于是「接受之后 `removed[]` 该写什么」
    在契约里答不出来（用 ours 的 span 可能指向旧原文、用 theirs 会丢人工圈选、
    取并集坐标系不一致、从最终文本反推则因为允许任意人工改字而不唯一）。
    ⇒ 聚合根不是 `clean_text`，是这一整团；换就整团换。
    """

    #: 这一版的正文本身。⚠ 它**在产物里**，不是指向 `clean_text.txt` 的引用——
    #: `clean_text.txt` 反过来是它的物化投影。历史版本的正文没有第二处可存：
    #: 剔除是破坏性的，而 `_rounds/` 那套逐轮归档正是本批要删掉的东西。
    clean_text: str

    removed: list[RemovedSegment]


class Stage0Revision(StrictModel):
    """链上不可变的一版。**`revision_id` 由前像派生 ⇒ 这条链就是哈希链。**

    ⚠ 前像**不含** `at` 与 `code`（同已下线的 `snapshot_digest` 的先例：时间与代码
    版本是审计元数据，机器输入/输出不变时身份必须稳定）。这不是省事：机器重跑算出
    同一份内容、挂在同一个 parent 上，就必须得到**同一个 id** —— 否则「这个候选我
    已经否过一次」（`Stage0Revisions.dismissed`）永远认不出来，被否掉的候选每跑一次
    机器就复活一次。前像的逐字定义与 golden vector 在
    `novel/stages/stage0/revisions.py::revision_digest` 与 `tests/test_stage0_revisions.py`。
    """

    revision_id: Sha256

    #: 链上的父。恒有且只有一条 `None`（首次摄入那条）。
    parent_revision_id: Sha256 | None

    origin: Stage0RevisionOrigin

    #: ⚠ **`machine` 那一支恒 `None`，两格一起**：没有人参与的事没有可问责的人，
    #: 也没有理由可写。措辞（「机器剔除」之类）是展示层的事，不落盘
    #: ——同 warning 把 `message` 赶出产物的那条纪律。
    actor: str | None
    reason: str | None

    at: str = Field(min_length=1)
    code: str = Field(min_length=1)

    content: Stage0RevisionContent

    @model_validator(mode='after')
    def _credentials_match_origin(self) -> Stage0Revision:
        if self.origin == 'machine':
            if self.actor is not None or self.reason is not None:
                raise ValueError('machine 起源不得伪造 actor / reason')
            return self
        if self.actor is None or not self.actor.strip():
            raise ValueError(f'{self.origin} 必须给自报 actor')
        if self.reason is None or not self.reason.strip():
            raise ValueError(f'{self.origin} 必须给 reason')
        return self


class Stage0DismissedCandidate(StrictModel):
    """人看过并**否掉**的一个机器候选。

    ⚠ 它不进链：否掉候选不改变正文，链只记内容血缘。但它必须留痕，否则
    `revision_id` 的稳定派生（同内容同 parent ⇒ 同 id）会让机器每重跑一次
    就把同一个已被否掉的候选重新挂回待裁决位。
    """

    revision_id: Sha256
    at: str = Field(min_length=1)
    by: str = Field(min_length=1)
    reason: str = Field(min_length=1)


class Stage0Current(StrictModel):
    """当前生效的那一版 + 它的闸门状态。

    ⚠ **审批与 revision 绑在同一个对象里，不是两个平级字段**：换稿必须重新批准，
    而「换了稿却把上一版的批准留着」是这一层最贵的错（stage2 是第一个花钱的层）。
    绑在一起之后，那种形态**在类型上就写不出来**——换 `revision_id` 就得同时给出
    这一版的 `approval`，而唯一会把它写成 `approved` 的入口是 `stage0.approve`。
    """

    revision_id: Sha256
    approval: Stage0Approval


class Stage0Revisions(StrictModel):
    """`stage0/revisions.json`。**本层唯一可变的那一格。**

    一次原子单文件写 = 一次提交。`clean_text.txt` 是它的唯一物化投影，可在任何时刻由
    `novel/stages/stage0/revisions.py::materialize` 幂等重建。
    """

    source: Stage0Source

    #: 全部历史版本，**按拓扑序**（parent 必须在自己之前出现）。
    revisions: list[Stage0Revision] = Field(min_length=1)

    current: Stage0Current

    #: 机器重跑算出的、与当前稿不同的那一版。⚠ 它**已经在 `revisions` 里**——
    #: 候选不是另一种东西，只是一条还没被指向的 revision。三选一：保留当前 /
    #: 采纳它（`human_adopt`）/ 采纳后再编辑（采纳完照常走 `stage0.edit`）。
    candidate_revision_id: Sha256 | None

    dismissed: list[Stage0DismissedCandidate] = Field(default_factory=list)

    @model_validator(mode='after')
    def _chain_is_well_formed(self) -> Stage0Revisions:
        ids = [item.revision_id for item in self.revisions]
        if len(ids) != len(set(ids)):
            raise ValueError('revision_id 不得重复')
        seen: set[str] = set()
        roots = 0
        for item in self.revisions:
            if item.parent_revision_id is None:
                roots += 1
            elif item.parent_revision_id not in seen:
                raise ValueError(
                    f'{item.revision_id[:12]}… 的 parent 不在它之前出现，链不是拓扑序')
            seen.add(item.revision_id)
        if roots != 1:
            raise ValueError(f'链必须恰好有一个根（parent=null），实际 {roots} 条')
        known = set(ids)
        if self.current.revision_id not in known:
            raise ValueError('current.revision_id 不在 revisions 里')
        if self.candidate_revision_id is not None:
            if self.candidate_revision_id not in known:
                raise ValueError('candidate_revision_id 不在 revisions 里')
            if self.candidate_revision_id == self.current.revision_id:
                raise ValueError('当前稿不能同时是待裁决候选')
            by_id = {item.revision_id: item for item in self.revisions}
            if by_id[self.candidate_revision_id].origin != 'machine':
                raise ValueError('只有机器起源的 revision 才会成为候选')
        dismissed_ids = [item.revision_id for item in self.dismissed]
        if len(dismissed_ids) != len(set(dismissed_ids)):
            raise ValueError('同一个候选不得否两次')
        for revision_id in dismissed_ids:
            if revision_id not in known:
                raise ValueError('dismissed 指向不存在的 revision')
            if revision_id == self.candidate_revision_id:
                raise ValueError('已否掉的候选不能同时挂在待裁决位')
        return self


# ── 命令 DTO：**用户意图**，与产物在类型上分家（TODO-36） ──────────────────


class Stage0EditPlan(HumanApproval):
    """审校台交给 ``stage0.edit`` 子进程的一次编辑计划。

    它不是产物，不进 ``ARTIFACTS``；HTTP 进程只准备人的决定，真正落一条新 revision
    的仍然是 CLI 子进程。``expected_revision_id`` 是乐观锁，防两个标签页互相覆盖。

    ⚠ ``removed`` 是 ``RequestedRemoval``（只有 span + kind）而不是产物的
    ``RemovedSegment``：命令说不出「事实」。见 `RequestedRemoval` 的说明。
    """

    #: 乐观锁。⚠ 2026-08-22 阶段 C 从 `expected_round` + `expected_clean_sha1`
    #: 两格换成这一格：revision id 由内容与 parent 一起派生，它**同时**钉住了
    #: 「基于哪一版」与「那一版长什么样」——两格能表达的它一格就表达完了，
    #: 而两格之间还可能互相矛盾。
    expected_revision_id: Sha256

    removed: list[RequestedRemoval]

    @model_validator(mode='after')
    def _payload_is_self_consistent(self) -> Stage0EditPlan:
        spans = [segment.span for segment in self.removed]
        if spans != sorted(spans):
            raise ValueError('removed 必须按原文 span 顺序排列')
        for index, segment in enumerate(self.removed):
            lo, hi = segment.span
            if index and self.removed[index - 1].span[1] > lo:
                raise ValueError(f'removed[{index - 1}] 与 removed[{index}] 重叠')
        return self


class Stage0CandidatePlan(HumanApproval):
    """审校台对一个机器候选的裁决（``adopt`` / ``dismiss``）。

    ⚠ 三选一里的「采纳后再编辑」**不是第三个动作**，是 ``adopt`` 之后照常走
    ``stage0.edit``：多一个复合动作就多一条要各自崩溃恢复的路，而这两步各自
    都已经是一次原子提交。
    """

    action: Literal['adopt', 'dismiss']
    candidate_revision_id: Sha256

    #: 乐观锁：页面看到的当前稿。候选是相对**某一版当前稿**提出的，
    #: 当前稿换了人还照旧点「采纳」，采纳的就不是他看过的那件事。
    expected_revision_id: Sha256


class Stage0Snapshot(StrictModel):
    """当前 Stage0 状态的运行时投影；不落盘、不进 artifact registry。"""

    original_filename: str = Field(min_length=1)

    source_ref: SourceRef
    code: str = Field(min_length=1)
    normalized_source_sha1: Sha1
    clean_sha1: Sha1
    removed: list[RemovedSegment]
    total_code_points: int = Field(ge=1)
    body_code_points: int = Field(ge=0)
    warnings: list[Stage0Warning]
    revision_id: Sha256
    revision_origin: Stage0RevisionOrigin
    approval: Stage0Approval
