#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""approvals —— 存量审批账的只读解码与文件锁设施（F8c 收口后只剩这两件事）。

【F8c 发生了什么】人工闸整层退役（2026-09-15 全自动裁决 → STR-06 新链）：
新链没有审批面——`HUMAN_NODES` 为空、11 个 `str06.` 节点没有任何人工 gate，
上传即授权开跑。因此本模块原来那套「gate／design／structure／writing／closure
五类 typed 决定 + 当前主体 proof 复验 + 自动落账」全部随旧链删除；**新书不会
再产生审批记录，也没有任何入口能写一份**。

【为什么模块还留着】两件仍然真实存在的事：
  1. **存量书的审批账只读解码**：旧输出根上的 `_service/approvals/<sid>.json`
     是历史证据。`legacy_incompatible` 的旧书只读识别（`state`／`inspection`／
     `workflow_transition` 的离线迁移）要能把它解回记录形状——所以记录模型
     （`_ApprovalRecordV8` 一族）与 `approval_head_bytes` 的稳定序列化原样保留，
     旧字节怎么写的就怎么解，不迁移、不补写、不假装新链也有这份账。
  2. **这本书删到一半时冻结审批写入的锁**：`frozen_for_trash` 与
     `approval_exclusion_lock` 防的是「书已移进回收站、审批文件又被写出来」
     的竞态（`novel/errors.py` 引着这条理由）。锁文件不是审批事实。

⚠ 删除的不只是函数，还有**写入口**：`get`／`approve_gate`／`backfill_from_disk`／
`reset`／`authorize_*`／`record_stage5_auto_closure`／`stage5_closure_view` 与
Stage4 约束冻结面一并删除；对应路由（`novel/api/routes/approvals.py`）、
CLI 子命令与前端审批屏同批退役（`contracts/schema/retirement.py` 登记）。
"""
from __future__ import annotations

import hashlib
import json
import os
import threading
from collections.abc import Iterator
from contextlib import contextmanager
from contextvars import ContextVar
from pathlib import Path
from typing import Literal

from pydantic import Field, ValidationError, model_validator

from contracts.invariants import APPROVAL_GATES
from contracts.schema.api import (
    DesignBindingInfeasibility,
    DesignReviewDecision,
    DesignReviewRepairCapability,
    DesignReviewRequest,
    Stage5ClosureDecision,
    Stage5FindingGroupAck,
    Stage5MustAppearDecision,
    StructureChapterDecision,
)
from contracts.schema.base import StrictModel
from contracts.schema.orchestration import NovelOverview
from novel import paths, prov
from novel.errors import ArtifactUnreadable, HumanEditProtected, NovelError

APPROVALS_DIRNAME = 'approvals'
_LOCK = threading.RLock()
_READ_CODE: ContextVar[str | None] = ContextVar(
    'approval_read_code', default=None)
_READ_OVERVIEW: ContextVar[NovelOverview | None] = ContextVar(
    'approval_read_overview', default=None)

#: 正在被移入回收站的书。**`_LOCK` 保护它**，与审批写入同一把锁——
#: 这样「冻结」与「写一份审批」之间没有缝，两者要么先后、要么其中一个明确 409。
#: ⚠ 只在内存，与 `jobs.JobRegistry` 同一条理由（`R-14.6`）：写下来就要有陈旧清理
#:   策略，而**一份会说谎的冻结名单比没有名单更坏**。删除是短操作（拉一个 CLI 子进程
#:   移目录），进程死了这一格跟着消失，恢复由 `trash.sweep_incomplete` 从盘上重建。
_FROZEN_FOR_TRASH: set[str] = set()


@contextmanager
def frozen_for_trash(sid: str) -> Iterator[None]:
    """删除这本书的整段时间里冻结它的审批写入。**回收站的进程内那一半。**

    ⚠⚠ 这一格补的是一条真实的两半竞态：书目录与审批文件由 CLI 子进程移动，
    而**审批写入发生在服务父进程**。父进程只占了书级作业锁（`jobs.active_key`），
    审批那条路一把都不占 ⇒ 子进程刚把 `<sid>.json` 移进回收站，父进程的
    审批写入就能把它**重新写出来**，留下一本「书已删除、审批还活着」的孤儿；
    反过来若子进程随后失败回滚，回滚条件 `not approval.exists()` 已被那次重写破坏，
    归档的那一份就永远留在半成品回收站里。
    ⇒ 冻结**只按 sid**，不持有 `_LOCK` 本身：删一本书不该把别的书的审批全挡住。
    """
    with _LOCK:
        if sid in _FROZEN_FOR_TRASH:
            raise HumanEditProtected(f'{sid} 已经有一个删除在进行中')
        _FROZEN_FOR_TRASH.add(sid)
    try:
        yield
    finally:
        with _LOCK:
            _FROZEN_FOR_TRASH.discard(sid)


def _refuse_if_frozen(sid: str) -> None:
    with _LOCK:
        if sid in _FROZEN_FOR_TRASH:
            raise HumanEditProtected(
                f'{sid} 正在被移入回收站；此时的审批写入会造出孤儿审批文件')


# ─────────────────────────────────────────────────────────────────────────────
# 一、存量审批账的记录形状（只读解码；新链不写这份账）
# ─────────────────────────────────────────────────────────────────────────────

class _ApprovalProof(StrictModel):
    """Exact current subject of one persisted human/service decision.

    The service record is deliberately not an artifact and therefore does not
    participate in pipeline provenance.  It still needs the same two anchors:
    the current clean-text root and the exact bytes a person accepted.  The
    decision digest additionally binds the exact human decision scope.
    """

    root_clean_sha1: str = Field(pattern=r'^[0-9a-f]{40}$')
    artifacts: dict[str, str] = Field(min_length=1)
    decision_sha256: str = Field(pattern=r'^[0-9a-f]{64}$')

    @model_validator(mode='after')
    def _artifact_digests_are_full_sha256(self) -> _ApprovalProof:
        if any(
            not rel or len(digest) != 64
            or any(char not in '0123456789abcdef' for char in digest)
            for rel, digest in self.artifacts.items()
        ):
            raise ValueError('approval artifact subjects must be non-empty full SHA-256 values')
        return self


class _StoredStructureDecision(StrictModel):
    """一项服务审批历史：公开 typed 决定 + 当时主体的内部 proof。

    ``chapter_subject_sha256`` 是从 v6 起加入的私有 additive 快照：它让
    工作包只复用业务面仍精确相同的历史章 accept；当前 v7 记录继续承载。
    缺少快照的旧记录只能走整书 subject + 完整 proof 仍精确相同的保守
    fallback。该字段不进入公开 ``ApprovalDoc`` v6。
    """

    actor: str = Field(min_length=1)
    reason: str = Field(min_length=1)
    subject_sha256: str = Field(pattern=r'^[0-9a-f]{64}$')
    chapters: list[StructureChapterDecision] = Field(min_length=1)
    decided_at: str = Field(min_length=1)
    proof: _ApprovalProof
    chapter_subject_sha256: dict[int, str] = Field(default_factory=dict)

    @model_validator(mode='after')
    def _chapter_subject_snapshot_is_complete_when_present(
        self,
    ) -> _StoredStructureDecision:
        if not self.chapter_subject_sha256:
            return self
        expected = {row.output_ordinal for row in self.chapters}
        if set(self.chapter_subject_sha256) != expected:
            raise ValueError('structure 逐章 subject 快照必须精确覆盖决定章集')
        if any(
            len(value) != 64
            or any(char not in '0123456789abcdef' for char in value)
            for value in self.chapter_subject_sha256.values()
        ):
            raise ValueError('structure 逐章 subject 快照必须是完整小写 SHA-256')
        return self


class _StoredWritingDecision(StrictModel):
    """一项 Stage5 写作授权历史：typed 决定 + 当时完整主体 proof。"""

    actor: str = Field(min_length=1)
    reason: str = Field(min_length=1)
    subject_sha256: str = Field(pattern=r'^[0-9a-f]{64}$')
    decided_at: str = Field(min_length=1)
    proof: _ApprovalProof


class _StoredDesignDecision(DesignReviewDecision):
    """一次 Stage4A 盲审历史：完整 typed 决定 + 当时候选的精确字节 proof。"""

    proof: _ApprovalProof


class _StoredDesignDecisionSlim(StrictModel):
    """新写入的 Stage4A 决定：只存 typed facts 与内部身份，不复制 workpack。"""

    record_kind: Literal['slim_v1'] = 'slim_v1'
    request: DesignReviewRequest
    candidate_sha256: str = Field(pattern=r'^[0-9a-f]{64}$')
    aggregate_subject_sha256: str = Field(pattern=r'^[0-9a-f]{64}$')
    strategy_subject_sha256: str = Field(pattern=r'^[0-9a-f]{64}$')
    strategy_epoch_sha256: str = Field(pattern=r'^[0-9a-f]{64}$')
    chapter_subject_sha256: dict[int, str]
    repair_capability: DesignReviewRepairCapability
    binding_infeasibility: DesignBindingInfeasibility | None = None
    decision_sha256: str = Field(pattern=r'^[0-9a-f]{64}$')
    decided_at: str = Field(min_length=1)
    proof: _ApprovalProof

    @model_validator(mode='after')
    def _chapter_subjects_cover_request(self) -> _StoredDesignDecisionSlim:
        expected = {row.output_ordinal for row in self.request.chapters}
        if set(self.chapter_subject_sha256) != expected:
            raise ValueError('slim design 章 subject 必须精确覆盖 typed 决定')
        return self


_StoredDesignHistory = _StoredDesignDecision | _StoredDesignDecisionSlim


class _StoredClosureDecision(StrictModel):
    """一次 Stage5 闭合回执：完整 typed 决定 + 提交时盘面的精确字节 proof。"""

    actor: str = Field(min_length=1)
    reason: str = Field(min_length=1)
    workpack_sha256: str = Field(pattern=r'^[0-9a-f]{64}$')
    review_sha256: str = Field(pattern=r'^[0-9a-f]{64}$')
    must_appear: list[Stage5MustAppearDecision] = Field(default_factory=list)
    group_acks: list[Stage5FindingGroupAck] = Field(default_factory=list)
    decision_sha256: str = Field(pattern=r'^[0-9a-f]{64}$')
    decided_at: str = Field(min_length=1)
    proof: _ApprovalProof


class _ApprovalRecordV9(StrictModel):
    """唯一现役盘上审批；当前主体与不可变历史仍完整校验，不继承旧版本读取器。"""

    v: Literal[9] = 9
    sid: str = Field(min_length=1)
    gate: dict[str, bool]
    writing: bool = False
    gate_proofs: dict[str, _ApprovalProof | None]
    writing_proof: _ApprovalProof | None = None
    structure_history: list[_StoredStructureDecision] = Field(default_factory=list)
    structure_decision: _StoredStructureDecision | None = None
    writing_history: list[_StoredWritingDecision] = Field(default_factory=list)
    writing_decision: _StoredWritingDecision | None = None
    #: 完整与 slim 历史仍保留原证据；历史能否生效由当前审阅契约另行核验。
    design_history: list[_StoredDesignHistory] = Field(default_factory=list)
    design_decision: _StoredDesignHistory | None = None
    closure_history: list[_StoredClosureDecision] = Field(default_factory=list)
    closure_decision: _StoredClosureDecision | None = None

    @model_validator(mode='after')
    def _record_is_complete_and_proved(self) -> _ApprovalRecordV9:
        if set(self.gate) != set(APPROVAL_GATES):
            raise ValueError('gate must contain exactly ' + ','.join(APPROVAL_GATES))
        if set(self.gate_proofs) != set(APPROVAL_GATES):
            raise ValueError('gate_proofs must contain exactly ' + ','.join(APPROVAL_GATES))
        for key, approved in self.gate.items():
            if approved != (self.gate_proofs[key] is not None):
                raise ValueError(f'gate {key} and its current-subject proof must agree')
        if self.writing != (self.writing_proof is not None):
            raise ValueError('writing and writing_proof must agree')
        if (self.structure_decision is None) != (not self.structure_history):
            raise ValueError('structure_decision 与非空 structure_history 必须同进同出')
        if (self.structure_decision is not None
                and self.structure_decision != self.structure_history[-1]):
            raise ValueError('structure_decision 必须是 structure_history 最后一项')
        if (self.writing_decision is None) != (not self.writing_history):
            raise ValueError('writing_decision 与非空 writing_history 必须同进同出')
        if (self.writing_decision is not None
                and self.writing_decision != self.writing_history[-1]):
            raise ValueError('writing_decision 必须是 writing_history 最后一项')
        if self.gate['s4']:
            decision = self.structure_decision
            if decision is None or any(
                row.decision != 'accept' for row in decision.chapters
            ):
                raise ValueError('s4 gate 只能由逐章全 accept 的 typed 结构裁决打开')
            if self.gate_proofs['s4'] != decision.proof:
                raise ValueError('s4 gate proof 必须等于当前 typed 结构裁决 proof')
        return self

    @model_validator(mode='after')
    def _design_history_has_one_current_tail(self) -> _ApprovalRecordV9:
        if (self.design_decision is None) != (not self.design_history):
            raise ValueError('design_decision 与非空 design_history 必须同进同出')
        if (
            self.design_decision is not None
            and self.design_decision != self.design_history[-1]
        ):
            raise ValueError('design_decision 必须是 design_history 最后一项')
        return self

    @model_validator(mode='after')
    def _closure_history_has_one_current_tail(self) -> _ApprovalRecordV9:
        if (self.closure_decision is None) != (not self.closure_history):
            raise ValueError('closure_decision 与非空 closure_history 必须同进同出')
        if (
            self.closure_decision is not None
            and self.closure_decision != self.closure_history[-1]
        ):
            raise ValueError('closure_decision 必须是 closure_history 最后一项')
        if self.gate['s5'] and self.closure_decision is None:
            raise ValueError('s5 gate 只能由当前 typed closure 回执打开')
        return self


def _path(base: str | os.PathLike[str], sid: str) -> Path:
    return Path(os.fspath(base)) / '_service' / APPROVALS_DIRNAME / f'{sid}.json'


def _lock_path(base: str | os.PathLike[str], sid: str) -> Path:
    """每本书审批 RMW 的跨进程锁；锁文件不是审批事实，长期保留无害。"""
    checked = paths.check_sid(sid)
    return (
        Path(os.fspath(base)).resolve()
        / '_service' / APPROVALS_DIRNAME / '.locks' / f'{checked}.lock'
    )


@contextmanager
def approval_exclusion_lock(
    base: str | os.PathLike[str], sid: str,
) -> Iterator[None]:
    """串行化一份审批文件的完整 read-modify-write 与回收站移动。

    ``threading.RLock`` 只管当前服务进程；Commander 从 CLI apply，HTTP 从服务
    路由 apply，两者可能是两个进程。这里把进程内锁和 OS 文件锁叠在同一个公开
    边界上，trash 也占同一把锁，避免审批更新丢失或书删完后审批复活。
    """
    lock_path = _lock_path(base, sid)
    lock_path.parent.mkdir(parents=True, exist_ok=True)
    with _LOCK:
        with lock_path.open('a+b') as handle:
            handle.seek(0, os.SEEK_END)
            if handle.tell() == 0:
                handle.write(b'\0')
                handle.flush()
            handle.seek(0)
            if os.name == 'nt':
                import msvcrt

                msvcrt.locking(handle.fileno(), msvcrt.LK_LOCK, 1)
                try:
                    yield
                finally:
                    handle.seek(0)
                    msvcrt.locking(handle.fileno(), msvcrt.LK_UNLCK, 1)
            else:
                import fcntl

                fcntl.flock(handle.fileno(), fcntl.LOCK_EX)
                try:
                    yield
                finally:
                    fcntl.flock(handle.fileno(), fcntl.LOCK_UN)


@contextmanager
def approval_file_lock(
    base: str | os.PathLike[str], sid: str,
) -> Iterator[None]:
    """审批相关写准入先于互斥副作用；删除只使用 exclusion lock。"""
    _require_approval_workflow(base)
    with approval_exclusion_lock(base, sid):
        yield


def _require_approval_workflow(base: str | os.PathLike[str]) -> None:
    """审批设施只消费已初始化工作流身份；错误请求不能创建任务或重发预算。"""
    from novel.work_units import WorkUnitRefused, workflow_manifest
    if workflow_manifest(base) is None:
        raise WorkUnitRefused(
            '审批设施要求已有当前工作流 manifest；旧 base 请用冻结版本恢复，'
            '新任务先经 Stage0 入口创建 clean 工作流')


@contextmanager
def _read_projection(
    *, overview: NovelOverview | None, code: str | None,
) -> Iterator[None]:
    """把同一次 operator 快照限制在当前只读投影调用栈内。"""
    if overview is None and code is None:
        overview, code = _READ_OVERVIEW.get(), _READ_CODE.get()
    overview_token = _READ_OVERVIEW.set(overview)
    code_token = _READ_CODE.set(code)
    try:
        yield
    finally:
        _READ_CODE.reset(code_token)
        _READ_OVERVIEW.reset(overview_token)


@contextmanager
def read_projection(
    *, overview: NovelOverview, code: str,
) -> Iterator[None]:
    """让同一次只读编排复用已经冻结的 overview / code 坐标。

    这是 invocation-scoped 的投影，不是跨请求缓存。operator 已经完成一次
    code/surface/七层 snapshot 后，queue、inbox 与审批 currentness 都必须在
    这一个上下文里派生；否则每个 gate 会各自重跑整仓 ``code_version``，既慢，
    也可能把同一次 next 拼成多个代码时点。
    """
    _validate_read_projection(overview.sid, overview=overview, code=code)
    with _read_projection(overview=overview, code=code):
        yield


def _projection_code() -> str:
    """有 invocation-scoped 坐标就复用；普通 API 调用仍现场取指纹。"""
    return _READ_CODE.get() or prov.code_version()


def projection_code() -> str:
    """返回当前只读 invocation 的代码坐标；普通调用仍现场读取。"""
    return _projection_code()


def _validate_read_projection(
    sid: str, *, overview: NovelOverview | None, code: str | None,
) -> None:
    """只接受完整且属于本书的 invocation-scoped 只读坐标。"""
    if overview is None and code is None:
        current = _READ_OVERVIEW.get()
        if current is not None and current.sid != sid:
            raise ValueError('审批只读投影属于另一书，不能共享快照')
    if (overview is None) != (code is None):
        raise ValueError('审批只读投影的 overview/code 必须成对提供')
    if overview is not None and overview.sid != sid:
        raise ValueError(
            f'审批只读投影属于 sid={overview.sid!r}，不是 {sid!r}')


def _fresh_record(sid: str) -> _ApprovalRecordV9:
    """没有盘上账时的空账：全 gate 关闭、无任何 proof。

    新链不写这份账；空账只服务于「盘上从没有过审批文件」这一事实的诚实读数
    （`has_record` 为 False 的伴随形状），不推断任何授权状态。
    """
    return _ApprovalRecordV9(
        sid=sid,
        gate={key: False for key in APPROVAL_GATES},
        gate_proofs={key: None for key in APPROVAL_GATES},
    )


def _read_raw(
    base: str | os.PathLike[str], sid: str, *, require_existing: bool = False,
) -> _ApprovalRecordV9:
    """按盘上原字节解码存量审批账；版本不符或损坏一律明确拒绝（不迁移）。"""
    paths.resolve_novel_dir(base, sid)
    p = _path(base, sid)
    if not p.is_file():
        if require_existing:
            raise HumanEditProtected('正式审批在已提交回执复验期间消失，不能推断未提交')
        return _fresh_record(sid)
    try:
        payload = json.loads(p.read_text(encoding='utf-8'))
        if (not isinstance(payload, dict) or type(payload.get('v')) is not int
                or payload['v'] != 9):
            raise ValueError('盘上审批必须显式声明当前整数版本 v=9')
        record = _ApprovalRecordV9.model_validate(payload)
    except (OSError, UnicodeDecodeError, ValueError) as exc:
        raise HumanEditProtected(
            f'审批记录损坏或版本不是当前 v9（旧版本与未来版本均不迁移）；'
            f'旧代码与旧输出根配对读取：{exc}') from exc
    if record.sid != sid:
        raise HumanEditProtected(
            f'approval state belongs to {record.sid!r}, not requested novel {sid!r}')
    return record


def approval_head_bytes(
    base: str | os.PathLike[str], sid: str,
) -> bytes | None:
    """稳定解析审批盘并只序列化 current head；历史增长不进入 surface seal。

    JSON 形状、旧 v7 full/slim union 与 SID 归属都只在 approvals 边界解释；
    inspection 只消费这份 canonical bytes，不再拥有第二个裸 JSON 解析器。
    """
    paths.resolve_novel_dir(base, sid)
    p = _path(base, sid)
    try:
        before = p.stat()
    except FileNotFoundError:
        return None
    except OSError as exc:
        raise ArtifactUnreadable(f'审批当前 head 不可读：{p}: {exc!r}') from exc
    try:
        raw = p.read_bytes()
        after = p.stat()
    except OSError as exc:
        raise ArtifactUnreadable(f'审批当前 head 读取期间变化：{p}: {exc!r}') from exc
    coordinate = lambda stat: (
        stat.st_dev, stat.st_ino, stat.st_size, stat.st_mtime_ns)
    if coordinate(before) != coordinate(after) or len(raw) != after.st_size:
        raise ArtifactUnreadable(f'审批当前 head 读取期间发生变化：{p}')
    try:
        # ⚠ 与 `_read_raw` 同一道版本闸：盘上审批必须**显式声明当前整数版本
        #   v=9**。只靠 pydantic 默认值会把「缺 `v` 的旧字节」和「`v: 9.0`
        #   浮点伪整数」静默解成合法 v9——那正是「旧版本不迁移」要拒的两格，
        #   而 surface seal 会把这次误认扩散成一次「已验证」的坐标。
        payload = json.loads(raw)
        if (not isinstance(payload, dict) or type(payload.get('v')) is not int
                or payload['v'] != 9):
            raise ValueError('盘上审批必须显式声明当前整数版本 v=9')
        record = _ApprovalRecordV9.model_validate(payload)
    except (ValidationError, ValueError) as exc:
        raise ArtifactUnreadable(
            f'审批当前 head 不是合法 v9 记录：{p}: {exc}') from exc
    if record.sid != sid:
        raise ArtifactUnreadable(
            f'审批当前 head 属于 {record.sid!r}，不是 {sid!r}')
    head = record.model_dump(
        mode='json',
        exclude={
            'design_history', 'structure_history',
            'writing_history', 'closure_history',
        },
    )
    return json.dumps(
        head, ensure_ascii=False, sort_keys=True, separators=(',', ':'),
        allow_nan=False,
    ).encode('utf-8')


def has_record(base: str | os.PathLike[str], sid: str) -> bool:
    """是否有 durable 审批记录文件（含存量书历史账）。

    新链没有审批面；本谓词只剩「盘上有没有这份历史账」这一个诚实读数，
    不推断任何当前授权状态。
    """
    with _LOCK:
        try:
            return _path(base, sid).is_file()
        except (NovelError, OSError):
            return False
