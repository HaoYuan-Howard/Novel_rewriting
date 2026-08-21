#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
stage0.py —— 对应 `docs/schema/stage0.md`。非正文剔除 + 人工审阅闸门，**不调 LLM**。

F7 已用临时目录走通产物形态、审阅状态机与编辑回写，并用 `Test_data` 的 8 本真实小说
跑过摄入与下游分章。原仓 `Output/` 仍没有历史 stage0 产物；这份新层的证据边界
记录在 `docs/schema/stage0.md`，不把本仓生成的临时样本冒充成原型历史样本。

⚠ 另一份产物 `stage0/clean_text.txt` **不是 JSON**，它的 schema 是字节形态
（utf-8 无 BOM · 恒 `\\n` · 只删段不改字）。字节形态的守卫在写盘口那一层
（`novel/store.py::write_text`，B 期），不在这里。

【本层唯一没有 `_provenance` 的理由，写在明处】
`docs/schema/stage0.md` 的三份产物表里**没有 `_provenance` 这一项**，本模块因此也没有。
它不是漏了：本层是全链的**源头**，`inputs` 恒空，而 `normalized_source_sha1` / `clean_sha1` /
`source_encoding` / `text_norm` 已经把「这份产物照什么产的」答完了。
`code` 不能塞进一份不存在的 `_provenance`，因此作为报告顶层字段记录；
它仍由全链同一个 `RunContext.code` / `prov.code_version()` 口径产生。
"""
from __future__ import annotations

import hashlib
from typing import Annotated, Literal

from pydantic import Field, StringConstraints, model_validator

from contracts.invariants import (
    APPROVAL_STATE, NONBODY_KINDS, REMOVED_ORIGIN, SOURCE_ENCODINGS,
)
from contracts.schema.base import Sha1, StrictModel
from contracts.schema.common import HumanApproval

#: 闸门两态的取值面投影。判别联合的两个 `status` 字面量必须与它对上
#: （`tests/test_invariants.py`）。
ApprovalState = Literal[APPROVAL_STATE]
Sha256 = Annotated[str, StringConstraints(pattern=r'^[0-9a-f]{64}$')]
#: `auto_zero_hit` 是**历史只读取值**（2026-08-21 停产）：旧版零命中自动批留下的产物仍须
#: 可 decode、可展示、可审计，但它**不具执行资格**——人工重批后 `approval.origin`
#: 才回到 `human`。现役写入方只有 `human`。
ApprovalOrigin = Literal['auto_zero_hit', 'human']
#: `auto_approve` 同上：历史审计条目里存在、必须可读；现役代码不再产生。
AuditAction = Literal['auto_approve', 'approve', 'edit', 'merge_accept', 'merge_reject']


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
#
# ⚠ `original_filename` 仍在报告顶层，只作展示；locator 的唯一权威是这一格。


class ExternalFileRef(StrictModel):
    """外部语料：一条书目录之外的绝对路径。

    ⚠ **被删被移就是世界真的变了**，读取端不许「救」它——多认一种形状就是把一条
    真信号洗成好消息。补偿是 `RemovedSegment` 里的 head/tail 残片。
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
    """删掉的一段。**`removed[]` 是唯一能核对「切对了没有」的东西**——
    番外体在产物目录里不留任何副本，剔除是破坏性的，一次假阳会静默吃掉半本书。

    ⇒ 这是本份产物存在的**第一理由**：它记的是一段已经从 `clean_text` 里消失的文本，
    删掉就是真的丢信息，不像其它字段那样能 0 成本现算回来。
    """

    kind: Literal[NONBODY_KINDS]

    #: 标记行原文（strip 后）。全语料实测长度 2~18。
    #:
    #: ⚠ **它与 `core`（剥壳后）是两个量纲，别混着用**：所有阈值判的都是 `core`
    #: ——剥掉前导 `【[(（`/数字、剥掉尾部标点之后剩下的部分。
    #: `NONBODY_HEAD_MAX_CP`(20) 与告警线 `NONBODY_MARKER_WARN_CP`(17) 判的都是 `core`。
    marker_line: str = Field(min_length=1)

    #: 1 起。全语料实测 541 ~ 2143。
    line_no: int = Field(ge=1)

    #: 码点半开区间，**坐标系是原始 txt**，不是 `clean_text`。
    #:
    #: ⚠ 审阅屏要在**原文**上画高亮，而 `clean_text` 里那段已经没了。
    #: 混用坐标系是这类设计最常见的 bug，而且它**不会报错**——
    #: 只会把高亮画到错误的位置上。
    span: tuple[int, int]

    #: 94 ~ 3290（全语料）。
    code_points: int = Field(ge=1)

    #: 恒 80 / 恒 40 码点。**被删掉那段文本的唯一残片**，尽管人不会去读 JSON。
    #:
    #: `span` 只有在原文还在原地、且没被换过时才能取回内容，而 `source_ref`
    #: 那一条恰恰说了「文件被移走时是**不知道**」。
    #: 34 条 × 120 码点——留着的成本可以忽略，丢了就没有第二次机会。
    head_preview: str
    tail_preview: str

    origin: Literal[REMOVED_ORIGIN]

    @model_validator(mode='after')
    def _span_is_a_half_open_interval(self) -> RemovedSegment:
        lo, hi = self.span
        if not 0 <= lo < hi:
            raise ValueError(f'span={self.span} 不是合法的半开区间（要求 0 <= 起 < 止）')
        return self


# ── 可疑项：**结构化证据**，不带措辞 ────────────────────────────────────────
#
# 【为什么 2026-08-22 把 `message` 从产物里拿掉】
# 上一版是 `{kind, message}`，措辞逐字落盘。三个后果：
#   ① 措辞是**展示层**的东西，却被写进了长期持久化的产物 —— 改一次文案，
#      盘上同一件事就有了两种写法，而它们都自称是当时的事实；
#   ② 判据只能拿 `kind` 复算（`s0_warnings` 此前正是只比 kind），
#      于是**数值错了不会红**：一条 `line_no` 记错的 `long_marker` 照样绿；
#   ③ 界面想换个说法（加个百分比、改个语气）就得重跑产地。
# ⇒ 产物只记**这条告警是怎么算出来的那几个数**，措辞由 `novel/core/warnings.py`
#   现渲染。⚠ **阈值不进产物**：`BODY_RATIO_MIN` 之类的唯一落点是
#   `contracts/invariants.py`，写进产物就是第二个落点，而两个落点必然分家。
#   渲染时现取即可 —— 阈值改了，旧产物的措辞跟着变**才是对的**：
#   它说的是「按今天的判准，这份数据长这样」。
#
# ⚠ **认告警一律用 `kind`，不许用措辞前缀**——这条纪律没变，只是现在连措辞都不落盘了。


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
    line_no: int = Field(ge=1)
    marker_core_code_points: int = Field(ge=1)


#: 一条可疑项。⚠ 它是**判别联合**，不能直接实例化——产地必须选一个具体类，
#: 这正是「每一类的证据字段各不相同」的意思。取值面与 `STAGE0_WARNINGS` 的对账在
#: `tests/test_invariants.py`，那条测试是这三个 `Literal` 不变成第二落点的全部保证。
Stage0Warning = Annotated[
    EarlyCutWarning | DropRatioWarning | LongMarkerWarning,
    Field(discriminator='kind'),
]


class Stage0EditPlan(HumanApproval):
    '''审校台交给 ``stage0.edit`` 子进程的一次编辑计划。

    它不是产物，不进 ``ARTIFACTS``；HTTP 进程只准备人的决定，真正归档并改写
    ``stage0`` 两份产物的仍然是 CLI 子进程。三个 ``expected_*`` 字段是乐观锁，
    防两个标签页拿旧页面互相覆盖。
    '''

    expected_round: int = Field(ge=1)
    expected_normalized_source_sha1: Sha1
    expected_clean_sha1: Sha1
    expected_total_code_points: int = Field(ge=1)
    clean_text: str
    clean_sha1: Sha1
    removed: list[RemovedSegment]

    @model_validator(mode='after')
    def _payload_is_self_consistent(self) -> Stage0EditPlan:
        if '\r' in self.clean_text:
            raise ValueError('clean_text 必须是 LF-only，编辑计划不做静默换行归一')
        actual = hashlib.sha1(self.clean_text.encode('utf-8')).hexdigest()
        if self.clean_sha1 != actual:
            raise ValueError(
                f'clean_sha1={self.clean_sha1} 与计划正文实际 {actual} 对不上')
        spans = [segment.span for segment in self.removed]
        if spans != sorted(spans):
            raise ValueError('removed 必须按原文 span 顺序排列')
        for index, segment in enumerate(self.removed):
            lo, hi = segment.span
            if hi > self.expected_total_code_points:
                raise ValueError(
                    f'removed[{index}].span={segment.span} 越过原文长度 '
                    f'{self.expected_total_code_points}')
            if segment.code_points != hi - lo:
                raise ValueError(
                    f'removed[{index}].code_points={segment.code_points}，但 span 长 {hi - lo}')
            if index and self.removed[index - 1].span[1] > lo:
                raise ValueError(f'removed[{index - 1}] 与 removed[{index}] 重叠')
        return self


class Stage0MergeConflict(StrictModel):
    """一次码点级 diff3 中不能安全自动选择的重叠改动。"""

    id: str = Field(min_length=1)
    base_span: tuple[int, int]
    base_text: str
    ours_text: str
    theirs_text: str

    @model_validator(mode='after')
    def _base_span_is_ordered(self) -> Stage0MergeConflict:
        lo, hi = self.base_span
        if not 0 <= lo <= hi:
            raise ValueError('base_span 必须是允许空插入点的半开区间')
        if len(self.base_text) != hi - lo:
            raise ValueError('base_text 长度必须等于 base_span 长度')
        return self


class Stage0ConflictResolution(StrictModel):
    conflict_id: str = Field(min_length=1)
    choice: Literal['ours', 'theirs', 'custom']
    custom_text: str | None = None

    @model_validator(mode='after')
    def _custom_is_explicit(self) -> Stage0ConflictResolution:
        if (self.choice == 'custom') != (self.custom_text is not None):
            raise ValueError('choice=custom 时且仅此时必须给 custom_text')
        return self


class Stage0MergeDecisionPlan(HumanApproval):
    action: Literal['accept', 'reject']
    candidate_id: Sha256
    expected_round: int = Field(ge=1)
    expected_normalized_source_sha1: Sha1
    expected_clean_sha1: Sha1
    resolutions: list[Stage0ConflictResolution] = Field(default_factory=list)

    @model_validator(mode='after')
    def _reject_has_no_fake_resolutions(self) -> Stage0MergeDecisionPlan:
        if self.action == 'reject' and self.resolutions:
            raise ValueError('reject 不接受 resolutions')
        return self


class MachineRemovalSnapshot(StrictModel):
    """一轮机器剔除的完整、可复算快照；它从不冒充正式人工稿。"""

    created_at: str = Field(min_length=1)
    code: str = Field(min_length=1)
    original_filename: str = Field(min_length=1)
    source_ref: SourceRef
    normalized_source_sha1: Sha1
    clean_sha1: Sha1
    source_encoding: Literal[SOURCE_ENCODINGS]
    text_norm: str = Field(min_length=1)
    total_code_points: int = Field(ge=1)
    body_code_points: int = Field(ge=0)
    clean_text: str
    removed: list[RemovedSegment]
    warnings: list[Stage0Warning]
    snapshot_sha256: Sha256

    @model_validator(mode='after')
    def _lengths_and_hash_agree(self) -> MachineRemovalSnapshot:
        if self.body_code_points != len(self.clean_text):
            raise ValueError('body_code_points 必须等于机器 clean_text 码点长度')
        actual = hashlib.sha1(self.clean_text.encode('utf-8')).hexdigest()
        if actual != self.clean_sha1:
            raise ValueError('clean_sha1 与机器 clean_text 不一致')
        return self


class Stage0MergeCandidate(StrictModel):
    """机器重跑与正式人工稿的三方合并候选；接受前不得改正式稿。"""

    candidate_id: Sha256
    created_at: str = Field(min_length=1)
    expected_round: int = Field(ge=1)
    expected_normalized_source_sha1: Sha1
    expected_clean_sha1: Sha1
    base_snapshot_sha256: Sha256
    theirs_snapshot: MachineRemovalSnapshot
    base_clean_sha1: Sha1
    ours_clean_sha1: Sha1
    theirs_normalized_source_sha1: Sha1
    theirs_clean_sha1: Sha1
    base_text: str
    ours_text: str
    theirs_text: str
    proposed_clean_text: str
    conflicts: list[Stage0MergeConflict]

    @model_validator(mode='after')
    def _conflicts_are_unique(self) -> Stage0MergeCandidate:
        ids = [item.id for item in self.conflicts]
        if len(ids) != len(set(ids)):
            raise ValueError('merge conflict id 不得重复')
        checks = (
            ('base_clean_sha1', self.base_clean_sha1, self.base_text),
            ('ours_clean_sha1', self.ours_clean_sha1, self.ours_text),
            ('theirs_clean_sha1', self.theirs_clean_sha1, self.theirs_text),
        )
        for name, expected, text in checks:
            actual = hashlib.sha1(text.encode('utf-8')).hexdigest()
            if actual != expected:
                raise ValueError(f'{name} 与对应文本不一致')
        if self.theirs_snapshot.snapshot_sha256 == self.base_snapshot_sha256:
            raise ValueError('机器快照没有变化时不该生成 merge candidate')
        if self.theirs_snapshot.normalized_source_sha1 != self.theirs_normalized_source_sha1:
            raise ValueError('theirs_snapshot 与 theirs_normalized_source_sha1 不一致')
        return self


class Stage0AuditEntry(StrictModel):
    """不可覆盖、哈希串联的一次 Stage0 人工或自动决定。"""

    seq: int = Field(ge=1)
    previous_sha256: Sha256 | None
    entry_sha256: Sha256
    action: AuditAction
    actor: str | None
    reason: str = Field(min_length=1)
    at: str = Field(min_length=1)
    before_normalized_source_sha1: Sha1 | None
    before_clean_sha1: Sha1 | None
    after_normalized_source_sha1: Sha1
    after_clean_sha1: Sha1
    candidate_id: Sha256 | None = None

    @model_validator(mode='after')
    def _actor_matches_action(self) -> Stage0AuditEntry:
        if self.action == 'auto_approve':
            if self.actor is not None:
                raise ValueError('auto_approve 不得伪造 actor')
        elif self.actor is None or not self.actor.strip():
            raise ValueError('人工决定必须给自报 actor')
        return self



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
    """已经批过。三格**全部必填**（`by` 的可空只属于历史自动批那一支）。"""

    status: Literal['approved']

    #: 批准来源。`auto_zero_hit` 是**历史只读取值**（2026-08-21 停产）：
    #: 旧产物读得、展示得、审计得，**跑不得**——执行资格的唯一落点是
    #: `novel/stages/stage0/eligibility.py::is_stage0_executable`，它要求 `human`。
    origin: ApprovalOrigin

    #: ISO 时间戳。批过就一定说得出是什么时候批的。
    at: str = Field(min_length=1)

    #: 自报身份，不宣称是认证身份。
    #: ⚠ **只有 `auto_zero_hit` 那一支才可为 `null`**——没有人批的东西没有批准人。
    by: str | None

    @model_validator(mode='after')
    def _actor_matches_origin(self) -> ApprovedApproval:
        if self.origin == 'human':
            if self.by is None or not self.by.strip():
                raise ValueError('人工批准必须给 by（自报身份）')
        elif self.by is not None:
            raise ValueError('auto_zero_hit 自动批准不得伪造 by')
        return self


#: 一次 stage0 审阅闸门的状态。取值面与 `APPROVAL_STATE` 的对账在
#: `tests/test_invariants.py`（同 warning 判别联合：那两个 `Literal` 与元组之间
#: 没有任何语言级联系，对账是唯一的连接）。
Stage0Approval = Annotated[
    PendingApproval | ApprovedApproval, Field(discriminator='status')]


class RemovalReport(StrictModel):
    """`stage0/removal_report.json`。删了哪些段 + 审阅状态 + 两个指纹。"""

    original_filename: str = Field(min_length=1)

    #: 原文此刻该去哪儿找（2026-08-22 从绝对路径字符串换成判别联合，见上）。
    #: ⚠ **文件被移走时是「不知道」，不是「不匹配」**——S-03 原文↔切分对照屏的
    #: 三种降级必须分得开。
    source_ref: SourceRef

    #: 本轮写报告所用的代码版本。stage0 没有 `_provenance`，所以这一格落在顶层。
    code: str = Field(min_length=1)

    # ── 两个指纹 = `R-0.6` 的全部内容 ────────────────────────────────────────
    #
    # 只记一个，**「有人换过原始小说」和「有人改过剔除结果」就分不开了**，
    # 而这两件事的处置完全相反：前者要从头重跑，后者**是人有意为之、必须保住**。
    #
    # ⚠ 两个都是 40 位**内容身份**（不是 10 位来源指纹）：它们回答的是
    # 「有人换过原文吗」，人可能拿 `sha1sum` 手工复核，截断买不到任何东西。

    #: **原始 txt 归一化之后那段文本**的 sha1。变了 ⇒ 全链重跑（`R-0.14` + `R-1.3`）。
    #:
    #: ⚠ **它不是原始文件字节的 digest**，这正是 2026-08-22 从 `source_sha1` 改名的理由：
    #: 旧名字读起来像「这个文件的哈希」，而算的是**解码 + 去 BOM + CRLF→LF 之后**的
    #: 那个串（`text_norm` 恒 `v1:bom+crlf` 说的就是这道工序）。⇒ 同一本书存成 UTF-8
    #: 和存成 GB18030，字节完全不同而这一格**相同**——那是对的，也正是它该有的语义，
    #: 但拿旧名字去核 `sha1sum 原文.txt` 的人会以为对不上是出了问题。
    normalized_source_sha1: Sha1

    #: **`clean_text.txt`** 的 sha1。变了而 `normalized_source_sha1` 没变 ⇒ 人编辑过，**保住**。
    #: 这才是 stage1 记的那个输入指纹（`partition_report.input_sha1`）。
    #:
    #: ⚠ **它的差异不可分解**：人那一趟能改字，于是「删了番外」与「人改了字」
    #: 混在同一个指纹里。⇒ 审阅屏算 diff **只能拿 `_rounds/` 里的上一版比**，
    #: 不能拿 `source ↔ clean` 比；`edited_by_human` 因此是唯一能一眼看出
    #: 「这本书被人动过」的标志位，不许省。
    clean_sha1: Sha1

    #: ⚠ 全语料 163 本**只出现过 `utf-8`，另四个零样本**——
    #: 取值面只能从代码来，**样本再多也补不出没发生过的分支**。
    source_encoding: Literal[SOURCE_ENCODINGS]

    #: 恒 `v1:bom+crlf`。归一化口径的版本号，改口径要改这个串。
    text_norm: str = Field(min_length=1)

    #: 全语料 27/163 本非空，单本最多 4 条；共 34 条真标记、**0 假阳**。
    removed: list[RemovedSegment]

    #: 原始 txt 归一化后的长度。全语料 8088 ~ 41224。
    total_code_points: int = Field(ge=1)

    #: 剔除后剩下的长度 = `len(clean_text)`。
    #:
    #: ⚠ `body_ratio` 与 `removed_code_points` **都不落盘**——前者恒等于
    #: `body_code_points / total_code_points`、后者恒等于 `sum(removed[].code_points)`，
    #: 校验器现算。`R-0.9` 的 `< 0.60` 判 FAIL 不受影响。
    body_code_points: int = Field(ge=0)

    #: 三类**全语料实测全部 0 命中**，现在各有一份合成样本。
    #: 合成样本证明的是「这条分支被执行过」，**不是「在真实语料上验证过」**。
    warnings: list[Stage0Warning]

    #: 机器输入与机器输出的稳定身份。正式 clean 可被人工编辑，因此不能拿 clean_sha1
    #: 冒充机器结果；三方合并必须显式区分两者。
    machine_normalized_source_sha1: Sha1
    machine_clean_sha1: Sha1
    machine_snapshot_sha256: Sha256

    # ── 审阅状态 ────────────────────────────────────────────────────────────

    #: **两个分支**（2026-08-22 从四个平铺可空字段收成判别联合，见上）。
    #: `R-0.3b` 是 `HARD`：**不满足执行资格 ⇒ stage1 拒绝启动**。
    #:
    #: ⚠ 执行资格比「`status == 'approved'`」更严：**还必须 `origin == 'human'`**
    #: （`novel/stages/stage0/eligibility.py::is_stage0_executable`，唯一落点）。
    #: 历史自动批产物读得、展示得，**跑不得**。
    #:
    #: ⚠ 按本层的设计，这道闸门是**盲区①（番外用纯数字章节标记写）全链唯一的防线**，
    #: 而 stage2 是第一个花钱的层 ⇒ 闸门必须在它之前、且必须有人真的看过。
    approval: Stage0Approval

    #: 第几轮。人每编辑一次 +1，上一轮进 `_rounds/`（`R-0.5`）。
    round: int = Field(ge=1)

    #: 本轮有没有人工编辑。
    #: ⚠ 它**不等于** `any(r.origin == 'human' for r in removed)`——
    #: 人还可以**直接改正文**（`R-0.4`），那一类改动在 `removed[]` 里没有任何痕迹。
    edited_by_human: bool

    #: 当前报告对应的审计链位置；0/null 表示尚无决定记录。
    audit_seq: int = Field(ge=0)
    audit_head_sha256: Sha256 | None

    @model_validator(mode='after')
    def _audit_head_and_seq_agree(self) -> RemovalReport:
        """审计链位置的两格必须同进同出。

        ⚠ 这里曾另有 20 行在维持 `approved` / `approved_at` / `approval_origin` /
        `approved_by` 四个平铺字段之间的互斥。那五条规则守的是同一件事——
        **有两种状态、字段集不一样**——现在由判别联合在类型上表达，
        「pending 却带着批准时间」从「构造得出来但会被拦下」变成「压根写不出来」。
        """
        if (self.audit_seq == 0) != (self.audit_head_sha256 is None):
            raise ValueError('audit_seq=0 与 audit_head_sha256=null 必须同时成立')
        return self
