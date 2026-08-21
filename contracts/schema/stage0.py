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
它不是漏了：本层是全链的**源头**，`inputs` 恒空，而 `source_sha1` / `clean_sha1` /
`source_encoding` / `text_norm` 已经把「这份产物照什么产的」答完了。
`code` 不能塞进一份不存在的 `_provenance`，因此作为报告顶层字段记录；
它仍由全链同一个 `RunContext.code` / `prov.code_version()` 口径产生。
"""
from __future__ import annotations

import hashlib
from typing import Annotated, Literal

from pydantic import Field, StringConstraints, model_validator

from contracts.invariants import (
    APPROVAL_STATE, NONBODY_KINDS, REMOVED_ORIGIN, SOURCE_ENCODINGS, STAGE0_WARNINGS,
)
from contracts.schema.base import Sha1, StrictModel
from contracts.schema.common import HumanApproval

ApprovalState = Literal[APPROVAL_STATE]
Sha256 = Annotated[str, StringConstraints(pattern=r'^[0-9a-f]{64}$')]
#: `auto_zero_hit` 是**历史只读取值**（2026-08-21 停产）：旧版零命中自动批留下的产物仍须
#: 可 decode、可展示、可审计，但它**不具执行资格**——人工重批后 `approval_origin`
#: 才回到 `human`。现役写入方只有 `human`。
ApprovalOrigin = Literal['auto_zero_hit', 'human']
#: `auto_approve` 同上：历史审计条目里存在、必须可读；现役代码不再产生。
AuditAction = Literal['auto_approve', 'approve', 'edit', 'merge_accept', 'merge_reject']


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
    #: `span` 只有在原文还在原地、且没被换过时才能取回内容，而 `original_path`
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


class Stage0EditPlan(HumanApproval):
    '''审校台交给 ``stage0.edit`` 子进程的一次编辑计划。

    它不是产物，不进 ``ARTIFACTS``；HTTP 进程只准备人的决定，真正归档并改写
    ``stage0`` 两份产物的仍然是 CLI 子进程。三个 ``expected_*`` 字段是乐观锁，
    防两个标签页拿旧页面互相覆盖。
    '''

    expected_round: int = Field(ge=1)
    expected_source_sha1: Sha1
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
    expected_source_sha1: Sha1
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
    original_path: str = Field(min_length=1)
    source_sha1: Sha1
    clean_sha1: Sha1
    source_encoding: Literal[SOURCE_ENCODINGS]
    text_norm: str = Field(min_length=1)
    total_code_points: int = Field(ge=1)
    body_code_points: int = Field(ge=0)
    clean_text: str
    removed: list[RemovedSegment]
    warnings: list['Stage0Warning']
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
    expected_source_sha1: Sha1
    expected_clean_sha1: Sha1
    base_snapshot_sha256: Sha256
    theirs_snapshot: MachineRemovalSnapshot
    base_clean_sha1: Sha1
    ours_clean_sha1: Sha1
    theirs_source_sha1: Sha1
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
        if self.theirs_snapshot.source_sha1 != self.theirs_source_sha1:
            raise ValueError('theirs_snapshot 与 theirs_source_sha1 不一致')
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
    before_source_sha1: Sha1 | None
    before_clean_sha1: Sha1 | None
    after_source_sha1: Sha1
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


class Stage0Warning(StrictModel):
    """一条可疑项。**认告警一律用 `kind`，不许用措辞前缀**——
    措辞随时可改，而拿字符串开头去认告警的代码会在第一次改文案时**静默**失效。
    """

    kind: Literal[STAGE0_WARNINGS]

    #: 人读的一句话。它进 S-13 校验报告当 `info` 级结论。
    message: str = Field(min_length=1)


class RemovalReport(StrictModel):
    """`stage0/removal_report.json`。删了哪些段 + 审阅状态 + 两个指纹。"""

    original_filename: str = Field(min_length=1)

    #: 绝对路径。⚠ **文件被移走时是「不知道」，不是「不匹配」**——
    #: S-03 原文↔切分对照屏的三种降级必须分得开。
    original_path: str = Field(min_length=1)

    #: 本轮写报告所用的代码版本。stage0 没有 `_provenance`，所以这一格落在顶层。
    code: str = Field(min_length=1)

    # ── 两个指纹 = `R-0.6` 的全部内容 ────────────────────────────────────────
    #
    # 只记一个，**「有人换过原始小说」和「有人改过剔除结果」就分不开了**，
    # 而这两件事的处置完全相反：前者要从头重跑，后者**是人有意为之、必须保住**。
    #
    # ⚠ 两个都是 40 位**内容身份**（不是 10 位来源指纹）：它们回答的是
    # 「有人换过原文吗」，人可能拿 `sha1sum` 手工复核，截断买不到任何东西。

    #: **原始 txt** 归一化后的 sha1。变了 ⇒ 全链重跑（`R-0.14` + `R-1.3`）。
    source_sha1: Sha1

    #: **`clean_text.txt`** 的 sha1。变了而 `source_sha1` 没变 ⇒ 人编辑过，**保住**。
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
    machine_source_sha1: Sha1
    machine_clean_sha1: Sha1
    machine_snapshot_sha256: Sha256

    # ── 审阅状态 ────────────────────────────────────────────────────────────

    #: **两态**（2026-08-21 裁决；三态里的 `not_run` 是从未落过盘的死值，已删）。
    #: `R-0.3b` 是 `HARD`：**不满足执行资格 ⇒ stage1 拒绝启动**。
    #:
    #: ⚠ 执行资格比「等于 `'approved'`」更严：**还必须 `approval_origin == 'human'`**
    #: （`novel/stages/stage0/eligibility.py::is_stage0_executable`，唯一落点）。
    #: 历史自动批产物读得、展示得，**跑不得**。
    #:
    #: ⚠ 按本层的设计，这道闸门是**盲区①（番外用纯数字章节标记写）全链唯一的防线**，
    #: 而 stage2 是第一个花钱的层 ⇒ 闸门必须在它之前、且必须有人真的看过。
    approved: ApprovalState

    #: ISO 时间戳。⚠ **必填但可为 `null`**：未批准时是 `null`，
    #: 「键不在」不是合法形态（那会把两态压成一态）。
    approved_at: str | None

    #: 批准来源。未批准时为 null。`auto_zero_hit` 是**历史只读取值**（已停产）：
    #: 零命中书不再自动获得批准——那恰好是纯数字番外盲区绕过人工防线的路。
    #: 现役唯一来源是 `human`。
    approval_origin: ApprovalOrigin | None

    #: 自报身份，不宣称是认证身份。未批准时为 null；人工批必有值。
    approved_by: str | None

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
    def _approval_state_and_timestamp_agree(self) -> RemovalReport:
        """`approved_at` 有值 ⟺ 真的批过。

        它挡的是三态被写歪的那一半：一份 `approved='pending'` 却带着批准时间的产物，
        与一份 `approved='approved'` 却说不出什么时候批的产物，
        在界面上都会被读成「批过了」。
        """
        if (self.approved == 'approved') != (self.approved_at is not None):
            raise ValueError(
                f'approved={self.approved!r} 与 approved_at={self.approved_at!r} 对不上：'
                f'只有 approved 才该有批准时间，而 approved 必须说得出是什么时候批的'
            )
        if self.approved == 'approved':
            if self.approval_origin is None:
                raise ValueError('approved 报告必须声明 approval_origin')
            if self.approval_origin == 'human':
                if self.approved_by is None or not self.approved_by.strip():
                    raise ValueError('人工批准必须给 approved_by')
            elif self.approved_by is not None:
                raise ValueError('auto_zero_hit 自动批准不得伪造 approved_by')
        elif self.approval_origin is not None or self.approved_by is not None:
            raise ValueError('未批准报告不得保留 approval_origin/approved_by')
        if (self.audit_seq == 0) != (self.audit_head_sha256 is None):
            raise ValueError('audit_seq=0 与 audit_head_sha256=null 必须同时成立')
        return self
