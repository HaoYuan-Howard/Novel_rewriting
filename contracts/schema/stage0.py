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
`docs/schema/stage0.md` 的产物表里**没有 `_provenance` 这一项**，本模块因此也没有。
它不是漏了：本层是全链的**源头**，`inputs` 恒空，而 `normalized_source_sha1` / `clean_sha1` /
`source_encoding` / `text_norm` 已经把「这份产物照什么产的」答完了。
`code` 不能塞进一份不存在的 `_provenance`，因此作为报告顶层字段记录；
它仍由全链同一个 `RunContext.code` / `prov.code_version()` 口径产生。

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
  · `clean_text.txt` 与 `removal_report.json` 退成 `current` 的**物化投影**：
    它们的路径与字节形态一个字不动（stage1~7 的来源账、`root_clean_sha1`、
    freshness 全部照旧），但「它们是不是最新的」不再需要跨文件事务保证——
    任何时刻按 `revisions.json` 重新物化一次即可，那是一个幂等纯函数。
  · 机器重跑不再自动三方合并：它只是链上又一条 revision。与当前稿不同就挂成
    **候选**，由人三选一（保留当前 / 换成候选 / 换成候选后再编辑）。
    ⇒ diff3、冲突 schema、决议 schema、base/ours/theirs 三份全文副本一并出局。
  · 审计链折叠进 revision：每条 revision 记 `parent_revision_id` + 起源 + 自报
    actor/reason，`revision_id` 由**前像**（含 parent）派生 ⇒ **链本身就是哈希链**，
    改一个字就对不上，且不再需要与报告顶层的 `audit_seq` / `audit_head_sha256`
    两格跨文件对账（那两格随之出局）。
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

    ⚠ **它是【已验证事实】，不是【用户意图】**（2026-08-22 阶段 C / TODO-36）：
    `origin` / `line_no` / `marker_line` / 两条预览由服务端在唯一落点复算落定。
    人从审校台交上来的那份长成 `RequestedRemoval`——只有 `span` 与 `kind`。
    两者共用一个模型时，客户端可以提交任意 `origin='machine'` 与任意预览，
    而**类型上看不出这是意图还是事实**。
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


class RequestedRemoval(StrictModel):
    """人**想**删掉的一段：只有意图，没有事实（2026-08-22 阶段 C / TODO-36）。

    【为什么要与 `RemovedSegment` 分家】审校台交上来的编辑计划此前直接复用产物模型，
    于是客户端可以提交 `origin='machine'`、可以伪造 `line_no`、可以写任意预览。
    2026-08-21 已在服务端把这些字段一律重新落定（伪造无效），但**类型上**命令与产物
    仍是同一个东西 —— 「哪些格是人说了算、哪些格只能由服务端算」全靠一段注释维持。

    ⇒ 命令 DTO 只留人真正说得出口的两格：**删哪一段**（`span`，坐标系是原文）与
    **它是哪一类**（`kind`）。其余全部由 `novel/stages/stage0/removal.py` 的唯一落点
    按 `s0_removal_ledger` 的同一口径复算 —— 那才是「已验证事实」。
    """

    kind: Literal[NONBODY_KINDS]
    span: tuple[int, int]

    @model_validator(mode='after')
    def _span_is_a_half_open_interval(self) -> RequestedRemoval:
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
    """已经批过。**批准只有人一种来源，因此 `by` / `reason` 恒必填。**

    ⚠ 这里曾有第三格 `origin: 'auto_zero_hit' | 'human'`，2026-08-22 删除。
    删除依据是**载体保证 ⇒ 结构不可达**，与 `APPROVAL_STATE.not_run` /
    `CHAPTER_LABEL_ANOMALY.non_numeric` 同一把尺子：`auto_zero_hit` 的产地
    （零命中自动批）2026-08-21 停产，此后唯一写入方 `run_approve` 恒写 `human`；
    而**真正带着它的存量产物是 v1**，阶段 B/C 两次形状破坏把本产物推到 v3 之后，
    `decode_artifact` 不迁移低版本 ⇒ 它们连读都读不进来（实测：盘上那份
    `auto_zero_hit` 报告报 `schema v1 不是当前 v3`）。留着它，`by` / `reason`
    就得为一个谁都造不出来的分支保持可空。
    """

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


# ── 聚合根：一条 revision ────────────────────────────────────────────────────


class Stage0RevisionContent(StrictModel):
    """一条 revision 的**全部内容**：原文身份 + 正文 + 剔除账本 + 告警。

    ⚠⚠ **它整团一起变，不许只换其中一格**——这正是外部评审 P0-3 的结论：
    此前的三方合并只对 `clean_text` 做 diff3，于是「接受之后 `removed[]` 该写什么」
    在契约里答不出来（用 ours 的 span 可能指向旧原文、用 theirs 会丢人工圈选、
    取并集坐标系不一致、从最终文本反推则因为允许任意人工改字而不唯一）。
    ⇒ 聚合根不是 `clean_text`，是这一整团；换就整团换。
    """

    #: 只作展示（书目录寻址走 `s0_dir_roundtrip`）；locator 的权威是 `source_ref`。
    original_filename: str = Field(min_length=1)
    source_ref: SourceRef

    #: **原始 txt 归一化之后那段文本**的 sha1。⚠ 不是原始文件字节的 digest。
    #: 一条链上的每条 revision 这一格必须相同——换原文按 `R-0.14` 是另一本书的摄入。
    normalized_source_sha1: Sha1
    source_encoding: Literal[SOURCE_ENCODINGS]
    text_norm: str = Field(min_length=1)
    total_code_points: int = Field(ge=1)

    #: 这一版的正文本身。⚠ 它**在产物里**，不是指向 `clean_text.txt` 的引用——
    #: `clean_text.txt` 反过来是它的物化投影。历史版本的正文没有第二处可存：
    #: 剔除是破坏性的，而 `_rounds/` 那套逐轮归档正是本批要删掉的东西。
    clean_text: str
    clean_sha1: Sha1
    body_code_points: int = Field(ge=0)

    removed: list[RemovedSegment]

    #: ⚠ 落盘而不是现算，是**有意的**：`s0_warnings` 拿 `warnings_for()` 复算并逐条
    #: 比证据，那条判据的全部判别力来自「盘上这份是产地当时算的」。物化时现算
    #: 会把它变成恒等式（同 `detected_chapter_marker_count` 那一刀的教训）。
    warnings: list[Stage0Warning]

    @model_validator(mode='after')
    def _text_and_hash_agree(self) -> Stage0RevisionContent:
        if self.body_code_points != len(self.clean_text):
            raise ValueError('body_code_points 必须等于 clean_text 码点长度')
        actual = hashlib.sha1(self.clean_text.encode('utf-8')).hexdigest()
        if actual != self.clean_sha1:
            raise ValueError('clean_sha1 与 clean_text 不一致')
        return self


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

    一次原子单文件写 = 一次提交。`clean_text.txt` 与 `removal_report.json` 是它的
    物化投影，可在任何时刻由 `novel/stages/stage0/revisions.py::materialize` 幂等重建
    ⇒ 「两份产物同进同出」不再需要 WAL、不再需要崩溃重放状态机。
    """

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
        sources = {item.content.normalized_source_sha1 for item in self.revisions}
        if len(sources) != 1:
            raise ValueError(
                '同一条链上的 revision 必须出自同一份原文（换原文按 R-0.14 是另一本书）')
        return self


# ── 命令 DTO：**用户意图**，与产物在类型上分家（TODO-36） ──────────────────


class Stage0EditPlan(HumanApproval):
    """审校台交给 ``stage0.edit`` 子进程的一次编辑计划。

    它不是产物，不进 ``ARTIFACTS``；HTTP 进程只准备人的决定，真正落一条新 revision
    的仍然是 CLI 子进程。三个 ``expected_*`` 是乐观锁，防两个标签页拿旧页面互相覆盖。

    ⚠ ``removed`` 是 ``RequestedRemoval``（只有 span + kind）而不是产物的
    ``RemovedSegment``：命令说不出「事实」。见 `RequestedRemoval` 的说明。
    """

    #: 乐观锁。⚠ 2026-08-22 阶段 C 从 `expected_round` + `expected_clean_sha1`
    #: 两格换成这一格：revision id 由内容与 parent 一起派生，它**同时**钉住了
    #: 「基于哪一版」与「那一版长什么样」——两格能表达的它一格就表达完了，
    #: 而两格之间还可能互相矛盾。
    expected_revision_id: Sha256

    #: 原文身份仍要单独钉：它不是某一版的属性而是整条链的前提，
    #: 且服务端要拿它去核盘上那份原文有没有被换过（`R-0.14`）。
    expected_normalized_source_sha1: Sha1
    expected_total_code_points: int = Field(ge=1)

    clean_text: str
    clean_sha1: Sha1
    removed: list[RequestedRemoval]

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


class RemovalReport(StrictModel):
    """`stage0/removal_report.json`。**`stage0/revisions.json` 中 `current` 的物化投影。**

    ⚠⚠ **它是投影不是权威**（2026-08-22 阶段 C）：内容逐字来自 `current` 指向的那条
    revision，`approval` 逐字来自 `current.approval`。它仍然是 stage1~7 眼里那份
    承重产物——路径、字节形态、来源账的键、`root_clean_sha1` 一个字没动——
    但「它与 `clean_text.txt` 是不是同一版」不再靠跨文件事务保证：
    任何时候按 `revisions.json` 重新物化一次即可，那是幂等纯函数。
    """

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
    #: 混在同一个指纹里。⇒ 审阅屏算 diff **只能拿链上的上一条 revision 比**，
    #: 不能拿 `source ↔ clean` 比。
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

    # ── 身份：这份投影照的是哪一版 ──────────────────────────────────────────
    #
    # ⚠ 2026-08-22 阶段 C 一次换掉六格：`round: int`（轮次编号）、三格 `machine_*`
    #   （机器输入/输出/快照身份，是三方合并用来分辨「人动过没有」的）、
    #   两格 `audit_*`（审计链头，是跨文件提交的对账位）。
    #   它们守的事全部由 revision 链本身承担：
    #     · 「第几版」→ 链上位置，且 `revision_id` 可复算 ⇒ 比递增整数更难伪造；
    #     · 「人动过没有」→ `revision_origin`，不必再拿两个哈希相减去推断；
    #     · 「审计链头对不对」→ `parent_revision_id` 逐条复算，不再需要第二份文件。

    #: 当前这一版的内容身份。**同时是编辑/裁决路的乐观锁**（`Stage0EditPlan`）。
    revision_id: Sha256

    #: 这一版是怎么来的。⚠ 它替代了旧的 `edited_by_human`：那一格是
    #: `origin == 'human_edit'` 的同义反复，而它说不出「人采纳了机器新结果」
    #: 与「人自己改的」之间的差别——两者的正文含义完全相反。
    revision_origin: Stage0RevisionOrigin

    #: **两个分支**（2026-08-22 从四个平铺可空字段收成判别联合，见上）。
    #: `R-0.3b` 是 `HARD`：**不满足执行资格 ⇒ stage1 拒绝启动**。
    #:
    #: ⚠ 判这件事的唯一落点是
    #: `novel/stages/stage0/eligibility.py::is_stage0_executable`——消费方一律 import，
    #: **不许复述条件**（含前端：响应里的 `executable` 是那个谓词的投影）。
    #: 2026-08-22 删掉 `auto_zero_hit` 之后它恰好等于 `status == 'approved'`，
    #: 但收口的理由不是条件复杂，是「同一条 R 只有一个守卫」。
    #:
    #: ⚠ 按本层的设计，这道闸门是**盲区①（番外用纯数字章节标记写）全链唯一的防线**，
    #: 而 stage2 是第一个花钱的层 ⇒ 闸门必须在它之前、且必须有人真的看过。
    approval: Stage0Approval
