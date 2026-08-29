#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""人工动作的 append-only 审计账。

``<base>/_service/human_actions.jsonl`` 以 prepared → committed 两相记录人工动作身份
与主体摘要，不写正文；公开读视图只返回 committed。Stage5 人工改稿的 actor/reason
不能只活在一次性上传文件或作业 argv：
两者都会消失，而 canonical Chapter 的来源账也刻意不冒充认证身份。

写入复用 :mod:`novel.appendlog` 的跨进程追加口；读侧只容忍被 kill 截断的最后一行。
下一次写入会在同一账锁内先把该尾行逐字隔离到内容寻址 receipt，再截回完整前缀并补相；
带换行的完整坏行与任何中间坏行仍关闭式失败。
"""
from __future__ import annotations

import hashlib
import json
import os
import tempfile
import time
from pathlib import Path
from typing import Literal

from pydantic import Field, model_validator

from contracts.invariants import STAGE5_EDIT_CONFIRMATIONS
from contracts.schema.base import FrozenModel
from novel.appendlog import append_line_under_process_lock, append_transaction
from novel.errors import ProvenanceRefused
from novel.paths import service_dir


HUMAN_ACTIONS_FILENAME = 'human_actions.jsonl'
HUMAN_ACTIONS_WIRE_VERSION = 1
HUMAN_ACTIONS_TAIL_RECOVERY_DIRNAME = 'human_actions_tail_recovery'


class HumanEditAction(FrozenModel):
    """Stage5 人工改稿两相事件；正文只以 SHA-256 出现。"""

    wire_version: Literal[1] = HUMAN_ACTIONS_WIRE_VERSION
    request_id: str = Field(pattern=r'^[0-9a-f]{64}$')
    action_id: str = Field(pattern=r'^[0-9a-f]{64}$')
    action: Literal['stage5_edit'] = 'stage5_edit'
    phase: Literal['prepared', 'committed']
    sid: str = Field(min_length=1)
    output_ordinal: int = Field(ge=1)
    actor: str = Field(min_length=1)
    reason: str = Field(min_length=1)
    confirmation: Literal[STAGE5_EDIT_CONFIRMATIONS]
    current_chapter_sha256: str = Field(pattern=r'^[0-9a-f]{64}$')
    preview_subject_sha256: str = Field(pattern=r'^[0-9a-f]{64}$')
    result_chapter_sha256: str = Field(pattern=r'^[0-9a-f]{64}$')
    introduced_hard_rules: list[str]
    destination_rel: str = Field(pattern=r'^stage5/ch\d+\.json$')
    run_id: str = Field(min_length=1)
    code: str = Field(min_length=1)
    ts: int = Field(ge=0)

    @model_validator(mode='after')
    def _credentials_and_rules_are_canonical(self) -> 'HumanEditAction':
        if not self.actor.strip() or not self.reason.strip():
            raise ValueError('human action actor/reason 不得只有空白')
        if self.introduced_hard_rules != sorted(set(self.introduced_hard_rules)):
            raise ValueError('introduced_hard_rules 必须升序、去重')
        if self.confirmation == 'formal' and self.introduced_hard_rules:
            raise ValueError('formal human action 不得记录 introduced HARD')
        if self.confirmation == 'working_draft' and not self.introduced_hard_rules:
            raise ValueError('working_draft human action 必须记录 introduced HARD')
        return self


def actions_path(base: str | os.PathLike[str]) -> Path:
    return service_dir(base) / HUMAN_ACTIONS_FILENAME


def tail_recovery_dir(base: str | os.PathLike[str]) -> Path:
    """未落稳尾行的不可变隔离证据目录；正文不会进入这里。"""
    return service_dir(base) / HUMAN_ACTIONS_TAIL_RECOVERY_DIRNAME


def request_id_of(
    *,
    sid: str,
    output_ordinal: int,
    actor: str,
    reason: str,
    confirmation: str,
    current_chapter_sha256: str,
    preview_subject_sha256: str,
) -> str:
    """一次 preview-bound 意图的稳定身份；不取提交相与结果格。"""
    identity = {
        'wire_version': HUMAN_ACTIONS_WIRE_VERSION,
        'action': 'stage5_edit',
        'sid': sid,
        'output_ordinal': output_ordinal,
        'actor': actor,
        'reason': reason,
        'confirmation': confirmation,
        'current_chapter_sha256': current_chapter_sha256,
        'preview_subject_sha256': preview_subject_sha256,
    }
    return hashlib.sha256(json.dumps(
        identity, ensure_ascii=False, sort_keys=True,
        separators=(',', ':')).encode('utf-8')).hexdigest()


def action_id_of(*, request_id: str, result_chapter_sha256: str) -> str:
    """精确 landed artifact 的 content-addressed 动作身份。"""
    return hashlib.sha256(json.dumps(
        {
            'request_id': request_id,
            'result_chapter_sha256': result_chapter_sha256,
        },
        sort_keys=True,
        separators=(',', ':'),
    ).encode('utf-8')).hexdigest()


def _event(
    *,
    phase: str,
    sid: str,
    output_ordinal: int,
    actor: str,
    reason: str,
    confirmation: str,
    current_chapter_sha256: str,
    preview_subject_sha256: str,
    result_chapter_sha256: str,
    introduced_hard_rules: list[str],
    destination_rel: str,
    run_id: str,
    code: str,
) -> HumanEditAction:
    request_id = request_id_of(
        sid=sid,
        output_ordinal=output_ordinal,
        actor=actor,
        reason=reason,
        confirmation=confirmation,
        current_chapter_sha256=current_chapter_sha256,
        preview_subject_sha256=preview_subject_sha256,
    )
    action_id = action_id_of(
        request_id=request_id,
        result_chapter_sha256=result_chapter_sha256,
    )
    event = HumanEditAction(
        request_id=request_id,
        action_id=action_id,
        phase=phase,
        sid=sid,
        output_ordinal=output_ordinal,
        actor=actor,
        reason=reason,
        confirmation=confirmation,
        current_chapter_sha256=current_chapter_sha256,
        preview_subject_sha256=preview_subject_sha256,
        result_chapter_sha256=result_chapter_sha256,
        introduced_hard_rules=sorted(set(introduced_hard_rules)),
        destination_rel=destination_rel,
        run_id=run_id,
        code=code,
        ts=int(time.time()),
    )
    return event


def _same_action(left: HumanEditAction, right: HumanEditAction) -> bool:
    # kill 后的补账会由新 run 完成；run_id/code 是“哪次尝试写了这一相”，
    # 不参与同一 preview-bound 动作及其结果的一致性。
    excluded = {'phase', 'ts', 'run_id', 'code'}
    return left.model_dump(exclude=excluded) == right.model_dump(exclude=excluded)


def _validate_phase_order(
    events: list[HumanEditAction],
) -> dict[str, tuple[HumanEditAction, HumanEditAction | None]]:
    """关闭两相账的顺序、唯一性与同形性；合法 orphan prepared 会被保留。"""
    states: dict[str, tuple[HumanEditAction, HumanEditAction | None]] = {}
    for event in events:
        state = states.get(event.action_id)
        if event.phase == 'prepared':
            if state is not None:
                raise ProvenanceRefused(
                    f'human action {event.action_id} 出现重复/倒序 prepared')
            states[event.action_id] = (event, None)
            continue
        if state is None:
            raise ProvenanceRefused(
                f'human action {event.action_id} 的 committed 早于/缺少 prepared')
        prepared, committed = state
        if committed is not None:
            raise ProvenanceRefused(
                f'human action {event.action_id} 出现重复 committed')
        if not _same_action(prepared, event):
            raise ProvenanceRefused(
                f'human action {event.action_id} 的 prepared/committed 主体不一致')
        states[event.action_id] = (prepared, event)
    return states


def _append_phase(
    base: str | os.PathLike[str],
    *,
    phase: Literal['prepared', 'committed'],
    sid: str,
    output_ordinal: int,
    actor: str,
    reason: str,
    confirmation: str,
    current_chapter_sha256: str,
    preview_subject_sha256: str,
    result_chapter_sha256: str,
    introduced_hard_rules: list[str],
    destination_rel: str,
    run_id: str,
    code: str,
) -> HumanEditAction:
    event = _event(
        phase=phase,
        sid=sid,
        output_ordinal=output_ordinal,
        actor=actor,
        reason=reason,
        confirmation=confirmation,
        current_chapter_sha256=current_chapter_sha256,
        preview_subject_sha256=preview_subject_sha256,
        result_chapter_sha256=result_chapter_sha256,
        introduced_hard_rules=introduced_hard_rules,
        destination_rel=destination_rel,
        run_id=run_id,
        code=code,
    )
    path = actions_path(base)
    line = json.dumps(event.model_dump(mode='json'), ensure_ascii=False, sort_keys=True)
    with append_transaction(path):
        _repair_truncated_tail_under_lock(base, path)
        existing = _read_log(base, tolerate_truncated_tail=False)
        _validate_phase_order(existing)
        committed_other = [
            row for row in existing
            if row.request_id == event.request_id
            and row.phase == 'committed'
            and row.action_id != event.action_id
        ]
        if committed_other:
            raise ProvenanceRefused(
                f'human request {event.request_id} 已 committed 为另一份结果；'
                '同一 preview 意图不得产生第二份正式结果')
        same_id = [row for row in existing if row.action_id == event.action_id]
        if any(not _same_action(row, event) for row in same_id):
            raise ProvenanceRefused(
                f'human action {event.action_id} 已存在但落盘结果身份不同；'
                '拒绝追加冲突审计')
        matched = [row for row in same_id if row.phase == phase]
        if matched:
            return matched[-1]
        if phase == 'committed' and not any(
                row.phase == 'prepared' for row in same_id):
            raise ProvenanceRefused(
                f'human action {event.action_id} 没有 prepared 相，'
                '拒绝凭空追加 committed')
        append_line_under_process_lock(path, line)
    return event


def prepare_edit(base: str | os.PathLike[str], **kwargs) -> HumanEditAction:
    return _append_phase(base, phase='prepared', **kwargs)


def commit_edit(base: str | os.PathLike[str], **kwargs) -> HumanEditAction:
    return _append_phase(base, phase='committed', **kwargs)


def append_edit(base: str | os.PathLike[str], **kwargs) -> HumanEditAction:
    """兼容的一步调用：内部仍严格落 prepared → committed 两行。"""
    prepare_edit(base, **kwargs)
    return commit_edit(base, **kwargs)


def _tail_recovery_receipt(raw_prefix: bytes, tail: bytes) -> bytes:
    """把被截断字节逐字留证；内容寻址使 kill 后重试仍可幂等。"""
    import base64

    payload = {
        'wire_version': 1,
        'action': 'quarantine_truncated_human_action_tail',
        'log': HUMAN_ACTIONS_FILENAME,
        'prefix_sha256': hashlib.sha256(raw_prefix).hexdigest(),
        'tail_sha256': hashlib.sha256(tail).hexdigest(),
        'tail_bytes': len(tail),
        'tail_base64': base64.b64encode(tail).decode('ascii'),
    }
    return (json.dumps(
        payload, ensure_ascii=False, sort_keys=True, separators=(',', ':'),
    ) + '\n').encode('utf-8')


def _write_tail_recovery_receipt(
    base: str | os.PathLike[str], raw_prefix: bytes, tail: bytes,
) -> Path:
    """同卷原子保存坏尾；主账截断前必须先有逐字可恢复证据。"""
    directory = tail_recovery_dir(base)
    directory.mkdir(parents=True, exist_ok=True)
    payload = _tail_recovery_receipt(raw_prefix, tail)
    digest = hashlib.sha256(payload).hexdigest()
    target = directory / f'{digest}.json'
    if target.is_file():
        if target.read_bytes() != payload:
            raise ProvenanceRefused(
                f'human action 坏尾隔离证据 {target} 已存在但内容冲突')
        return target

    fd, temp_name = tempfile.mkstemp(
        prefix=f'.{digest}.', suffix='.tmp', dir=directory)
    try:
        with os.fdopen(fd, 'wb') as handle:
            handle.write(payload)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temp_name, target)
    except BaseException:
        try:
            os.unlink(temp_name)
        except FileNotFoundError:
            pass
        raise
    return target


def _repair_truncated_tail_under_lock(
    base: str | os.PathLike[str], path: Path,
) -> Path | None:
    """只在 human_actions append 事务内隔离并截掉确证的未换行尾。"""
    if not path.is_file():
        return None
    raw = path.read_bytes()
    if not raw or raw.endswith(b'\n'):
        return None
    prefix_end = raw.rfind(b'\n') + 1
    prefix, tail = raw[:prefix_end], raw[prefix_end:]
    receipt = _write_tail_recovery_receipt(base, prefix, tail)
    # append_transaction 锁住同一目标，仍复核字节，避免未来误把本函数移出锁后
    # 静默截掉别的写手刚追加的完整事件。
    with path.open('r+b') as handle:
        current = handle.read()
        if current != raw:
            raise ProvenanceRefused(
                f'{path} 在坏尾隔离与截断之间变化；拒绝修复混合快照')
        handle.seek(prefix_end)
        handle.truncate()
        handle.flush()
        os.fsync(handle.fileno())
    return receipt


def _read_log(
    base: str | os.PathLike[str],
    *,
    tolerate_truncated_tail: bool,
) -> list[HumanEditAction]:
    path = actions_path(base)
    if not path.is_file():
        return []
    raw = path.read_bytes()
    # 读视图可把 kill 留下的最后半行当作“尚未落稳”；写侧不能在它后面继续
    # append，否则半行会被推到中间、连同新事件一起永久不可读。
    if raw and not raw.endswith(b'\n') and not tolerate_truncated_tail:
        raise ProvenanceRefused(
            f'{path} 尾行没有完整换行，疑似截断；拒绝在坏尾后继续追加')
    tail_truncated = bool(raw and not raw.endswith(b'\n'))
    lines = raw.decode('utf-8', errors='replace').splitlines()
    out: list[HumanEditAction] = []
    for index, line in enumerate(lines):
        if not line.strip():
            continue
        if tolerate_truncated_tail and tail_truncated and index == len(lines) - 1:
            # append_line 把 JSON 与结尾换行作为同一 payload；缺换行就证明这一条
            # 没有完整写完。即使截断点碰巧落在合法 JSON 的 `}` 后，也不能把它
            # 冒充已落稳事件。
            break
        try:
            out.append(HumanEditAction.model_validate(json.loads(line)))
        except Exception as exc:  # noqa: BLE001 - typed carrier closes malformed logs
            raise ProvenanceRefused(
                f'{path} 第 {index + 1} 行不是合法 human action；拒绝继续追加') from exc
    return out


def read_events(base: str | os.PathLike[str]) -> list[HumanEditAction]:
    """读取已落稳动作；prepared 孤儿不进入公开审计视图。"""
    log = _read_log(base, tolerate_truncated_tail=True)
    _validate_phase_order(log)
    return [event for event in log if event.phase == 'committed']


def action_state(
    base: str | os.PathLike[str], action_id: str,
) -> tuple[HumanEditAction | None, HumanEditAction | None]:
    """返回一项请求的 prepared/committed；用于 kill 后补账，不猜正文。"""
    events = [
        row for row in _read_log(base, tolerate_truncated_tail=True)
        if row.action_id == action_id
    ]
    states = _validate_phase_order(events)
    state = states.get(action_id)
    return state if state is not None else (None, None)


def request_state(
    base: str | os.PathLike[str],
    request_id: str,
    *,
    result_chapter_sha256: str,
) -> tuple[HumanEditAction | None, HumanEditAction | None]:
    """按请求意图 + 当前 landed 文件摘要找可补账的精确两相动作。"""
    action_id = action_id_of(
        request_id=request_id,
        result_chapter_sha256=result_chapter_sha256,
    )
    return action_state(base, action_id)


__all__ = [
    'HUMAN_ACTIONS_FILENAME', 'HUMAN_ACTIONS_TAIL_RECOVERY_DIRNAME',
    'HUMAN_ACTIONS_WIRE_VERSION',
    'HumanEditAction', 'action_id_of', 'action_state', 'actions_path',
    'append_edit', 'commit_edit', 'prepare_edit', 'read_events',
    'request_id_of', 'request_state', 'tail_recovery_dir',
]
