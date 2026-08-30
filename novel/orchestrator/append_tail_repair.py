#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""append-only JSONL 的 non-LF 坏尾恢复尺子。

坏尾不是 JSON：它可能正截在 UTF-8 字符、转义或对象中间，任何解析或补全都是猜。
本模块只按最后一个 LF 切出 committed prefix 与原始 tail，以双 SHA 形成主体；
apply 先原样隔离 tail，再写 immutable ``authorized``，fsync 截断，最后写
``applied``。admission 与 beacon 必须共用这套字节级语义，不能各自“差不多”。
"""
from __future__ import annotations

import hashlib
import json
import os
import tempfile
import time
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from novel.appendlog import append_transaction
from novel.errors import TerminalState


@dataclass(frozen=True)
class TailRepairSpec:
    protocol: str
    log_filename: str
    filename_field: str
    label: str
    repair_dir: Path
    record_type: type[Any]


@dataclass(frozen=True)
class TailRepairSubject:
    subject_sha256: str
    committed_size: int
    committed_sha256: str
    tail_size: int
    tail_sha256: str
    quarantine_path: Path


def sha256_bytes(payload: bytes) -> str:
    return hashlib.sha256(payload).hexdigest()


def require_appendable(path: Path, *, label: str) -> None:
    """调用方持有目标事务锁时，拒绝把新行接到未提交尾之后。"""
    if not path.is_file() or path.stat().st_size == 0:
        return
    with open(path, 'rb') as handle:
        handle.seek(-1, os.SEEK_END)
        if handle.read(1) != b'\n':
            raise ValueError(
                f'{label} 留有未以 LF 提交的尾行；拒绝继续拼接。'
                '必须保留原始字节并走 typed/hash-bound 恢复，不能静默截断、'
                '补 JSON 或续写')


def subject_from_hashes(
    spec: TailRepairSpec, *, committed_size: int,
    committed_sha256: str, tail_size: int, tail_sha256: str,
) -> TailRepairSubject:
    payload = {
        'protocol': spec.protocol,
        spec.filename_field: spec.log_filename,
        'committed_size': committed_size,
        'committed_sha256': committed_sha256,
        'tail_size': tail_size,
        'tail_sha256': tail_sha256,
    }
    rendered = json.dumps(
        payload, ensure_ascii=False, sort_keys=True,
        separators=(',', ':')).encode('utf-8')
    subject_sha256 = sha256_bytes(rendered)
    return TailRepairSubject(
        subject_sha256=subject_sha256,
        committed_size=committed_size,
        committed_sha256=committed_sha256,
        tail_size=tail_size,
        tail_sha256=tail_sha256,
        quarantine_path=spec.repair_dir / f'{subject_sha256}.tail.bin',
    )


def tail_material(
    spec: TailRepairSpec, raw: bytes,
) -> tuple[TailRepairSubject, bytes, bytes]:
    if not raw or raw.endswith(b'\n'):
        raise TerminalState(f'{spec.label} 当前没有 non-LF 坏尾可恢复')
    boundary = raw.rfind(b'\n') + 1
    committed = raw[:boundary]
    tail = raw[boundary:]
    subject = subject_from_hashes(
        spec, committed_size=len(committed),
        committed_sha256=sha256_bytes(committed), tail_size=len(tail),
        tail_sha256=sha256_bytes(tail))
    return subject, committed, tail


def repair_record_path(
    spec: TailRepairSpec, subject_sha256: str, kind: str,
) -> Path:
    return spec.repair_dir / f'{subject_sha256}.{kind}.json'


def read_records(spec: TailRepairSpec) -> tuple[Any, ...]:
    if not spec.repair_dir.is_dir():
        return ()
    records = [
        spec.record_type.model_validate_json(path.read_bytes())
        for path in sorted(spec.repair_dir.glob('*.json'))
    ]
    return tuple(sorted(
        records,
        key=lambda item: (
            item.subject_sha256, 0 if item.kind == 'authorized' else 1)))


def fsync_directory(path: Path) -> None:
    """尽力刷目录项；Windows 不支持目录 fd 时由文件 fsync 兜底。"""
    try:
        descriptor = os.open(path, os.O_RDONLY)
    except OSError:
        return
    try:
        os.fsync(descriptor)
    finally:
        os.close(descriptor)


def write_immutable(
    path: Path, payload: bytes, *, label: str,
) -> None:
    """同内容重试幂等；既有证据任何字节漂移都关闭式拒绝。"""
    path.parent.mkdir(parents=True, exist_ok=True)
    if path.exists():
        if path.read_bytes() != payload:
            raise TerminalState(f'immutable {label} 恢复证据已漂移：{path}')
        return
    descriptor, temporary = tempfile.mkstemp(
        prefix=f'.{path.name}.', suffix='.tmp', dir=path.parent)
    temporary_path = Path(temporary)
    try:
        with os.fdopen(descriptor, 'wb') as handle:
            handle.write(payload)
            handle.flush()
            os.fsync(handle.fileno())
        if path.exists():
            if path.read_bytes() != payload:
                raise TerminalState(f'immutable {label} 恢复证据已漂移：{path}')
        else:
            os.replace(temporary_path, path)
            fsync_directory(path.parent)
    finally:
        try:
            temporary_path.unlink()
        except FileNotFoundError:
            pass


def record_matches_subject(record: Any, subject: TailRepairSubject) -> bool:
    return (
        record.subject_sha256 == subject.subject_sha256
        and record.committed_size == subject.committed_size
        and record.committed_sha256 == subject.committed_sha256
        and record.tail_size == subject.tail_size
        and record.tail_sha256 == subject.tail_sha256
        and record.quarantine_name == subject.quarantine_path.name
    )


def write_record(
    spec: TailRepairSpec, subject: TailRepairSubject, *,
    kind: str, actor: str, reason: str,
) -> Any:
    path = repair_record_path(spec, subject.subject_sha256, kind)
    if path.exists():
        existing = spec.record_type.model_validate_json(path.read_bytes())
        if existing.kind != kind or not record_matches_subject(existing, subject):
            raise TerminalState(f'{spec.label} tail repair {kind} 记录主体漂移')
        if existing.actor != actor or existing.reason != reason:
            raise TerminalState(
                f'{spec.label} tail repair {kind} 两相 actor/reason 漂移；'
                '被 kill 后只能由同一 typed identity 重试')
        return existing
    record = spec.record_type(
        kind=kind, ts=int(time.time()), actor=actor, reason=reason,
        subject_sha256=subject.subject_sha256,
        committed_size=subject.committed_size,
        committed_sha256=subject.committed_sha256,
        tail_size=subject.tail_size, tail_sha256=subject.tail_sha256,
        quarantine_name=subject.quarantine_path.name)
    payload = json.dumps(
        record.model_dump(mode='json'), ensure_ascii=False, sort_keys=True,
        separators=(',', ':')).encode('utf-8') + b'\n'
    write_immutable(path, payload, label=spec.label)
    return record


def pending_subject(
    spec: TailRepairSpec, raw: bytes,
) -> TailRepairSubject | None:
    records = read_records(spec)
    applied = {
        record.subject_sha256 for record in records if record.kind == 'applied'}
    candidates: list[TailRepairSubject] = []
    for record in records:
        if record.kind != 'authorized' or record.subject_sha256 in applied:
            continue
        subject = subject_from_hashes(
            spec, committed_size=record.committed_size,
            committed_sha256=record.committed_sha256,
            tail_size=record.tail_size, tail_sha256=record.tail_sha256)
        if not record_matches_subject(record, subject):
            raise TerminalState(
                f'{spec.label} tail repair authorized 记录主体漂移')
        quarantine = subject.quarantine_path.read_bytes() \
            if subject.quarantine_path.is_file() else None
        if (len(raw) == subject.committed_size
                and sha256_bytes(raw) == subject.committed_sha256
                and quarantine is not None
                and len(quarantine) == subject.tail_size
                and sha256_bytes(quarantine) == subject.tail_sha256):
            candidates.append(subject)
    if len(candidates) > 1:
        raise TerminalState(
            f'{spec.label} 存在多个匹配当前 committed prefix 的未完成 tail repair')
    return candidates[0] if candidates else None


def repair_subject(path: Path, spec: TailRepairSpec) -> TailRepairSubject:
    """只读形成坏尾模板；也可续接 truncate 后未写 applied 的事务。"""
    with append_transaction(path):
        raw = path.read_bytes() if path.is_file() else b''
        if raw and not raw.endswith(b'\n'):
            return tail_material(spec, raw)[0]
        pending = pending_subject(spec, raw)
        if pending is not None:
            return pending
        raise TerminalState(f'{spec.label} 当前没有 non-LF 坏尾可恢复')


RecordWriter = Callable[..., Any]


def repair_tail(
    path: Path, spec: TailRepairSpec, *, actor: str, reason: str,
    subject_sha256: str, record_writer: RecordWriter | None = None,
) -> TailRepairSubject:
    """原尾 immutable 隔离 → authorized → truncate+fsync → applied。"""
    actor = actor.strip()
    reason = reason.strip()
    if not actor or not reason:
        raise TerminalState(f'{spec.label} tail repair 的 actor/reason 不得为空')
    writer = record_writer or (
        lambda subject, **kwargs: write_record(spec, subject, **kwargs))
    with append_transaction(path):
        raw = path.read_bytes() if path.is_file() else b''
        has_tail = bool(raw and not raw.endswith(b'\n'))
        if has_tail:
            subject, committed, tail = tail_material(spec, raw)
        else:
            subject = pending_subject(spec, raw)
            if subject is None:
                raise TerminalState(
                    f'{spec.label} 当前没有可恢复坏尾或未完成事务')
            committed = raw
            tail = subject.quarantine_path.read_bytes()
        if subject.subject_sha256 != subject_sha256:
            raise TerminalState(
                f'{spec.label} tail repair subject SHA 已变化；'
                f'current={subject.subject_sha256}')

        write_immutable(subject.quarantine_path, tail, label=spec.label)
        writer(subject, kind='authorized', actor=actor, reason=reason)
        if has_tail:
            with open(path, 'r+b') as handle:
                handle.truncate(subject.committed_size)
                handle.flush()
                os.fsync(handle.fileno())
            if path.read_bytes() != committed:
                raise TerminalState(
                    f'{spec.label} tail repair 截断后 committed prefix 漂移')
        writer(subject, kind='applied', actor=actor, reason=reason)
        return subject


__all__ = [
    'TailRepairSpec', 'TailRepairSubject', 'fsync_directory',
    'pending_subject', 'read_records', 'record_matches_subject',
    'repair_subject', 'repair_tail', 'require_appendable', 'sha256_bytes',
    'subject_from_hashes', 'tail_material', 'write_immutable', 'write_record',
]
