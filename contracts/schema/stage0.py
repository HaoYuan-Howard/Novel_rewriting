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
from typing import Literal

from pydantic import Field, model_validator

from contracts.invariants import (
    APPROVAL_STATE, NONBODY_KINDS, REMOVED_ORIGIN, SOURCE_ENCODINGS, STAGE0_WARNINGS,
)
from contracts.schema.base import Sha1, StrictModel

ApprovalState = Literal[APPROVAL_STATE]


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


class Stage0EditPlan(StrictModel):
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

    # ── 审阅状态 ────────────────────────────────────────────────────────────

    #: **三态**（2026-08-11 用户裁决）。`R-0.3b` 是 `HARD`：
    #: **不等于 `'approved'` ⇒ stage1 拒绝启动**——`not_run` 与 `pending` 都要拦。
    #:
    #: ⚠ 按本层的设计，这道闸门是**盲区①（番外用纯数字章节标记写）全链唯一的防线**，
    #: 而 stage2 是第一个花钱的层 ⇒ 闸门必须在它之前。
    approved: ApprovalState

    #: ISO 时间戳。⚠ **必填但可为 `null`**：未批准时是 `null`，
    #: 「键不在」不是合法形态（那会把三态又压回两态）。
    approved_at: str | None

    #: 第几轮。人每编辑一次 +1，上一轮进 `_rounds/`（`R-0.5`）。
    round: int = Field(ge=1)

    #: 本轮有没有人工编辑。
    #: ⚠ 它**不等于** `any(r.origin == 'human' for r in removed)`——
    #: 人还可以**直接改正文**（`R-0.4`），那一类改动在 `removed[]` 里没有任何痕迹。
    edited_by_human: bool

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
        return self
