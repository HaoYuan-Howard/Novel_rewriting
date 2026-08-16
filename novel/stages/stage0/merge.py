#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Stage0 的码点级三方合并与不可覆盖审计链。全程零 LLM。"""
from __future__ import annotations

import hashlib
import json
import re
from dataclasses import dataclass
from datetime import datetime, timezone
from difflib import SequenceMatcher
from typing import Iterable, Mapping

from contracts.artifacts import STAGE0_AUDIT_ENTRY
from contracts.schema.stage0 import (
    MachineRemovalSnapshot,
    Stage0AuditEntry,
    Stage0ConflictResolution,
    Stage0MergeCandidate,
    Stage0MergeConflict,
)
from novel import prov
from novel.errors import ArtifactUnreadable, HumanEditProtected
from novel.run import RunContext
from novel.store import ArtifactStore


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _digest(value: object) -> str:
    raw = json.dumps(
        value, ensure_ascii=False, sort_keys=True, separators=(',', ':'),
    ).encode('utf-8')
    return hashlib.sha256(raw).hexdigest()


def snapshot_digest(snapshot: MachineRemovalSnapshot | Mapping[str, object]) -> str:
    data = snapshot.model_dump(mode='json') if isinstance(snapshot, MachineRemovalSnapshot) \
        else dict(snapshot)
    data.pop('snapshot_sha256', None)
    # 时间与代码是审计元数据；机器输入/输出不变时快照身份必须稳定。
    data.pop('created_at', None)
    data.pop('code', None)
    return _digest(data)


def audit_digest(entry: Stage0AuditEntry | Mapping[str, object]) -> str:
    data = entry.model_dump(mode='json') if isinstance(entry, Stage0AuditEntry) else dict(entry)
    data.pop('entry_sha256', None)
    return _digest(data)


def candidate_digest(candidate: Stage0MergeCandidate | Mapping[str, object]) -> str:
    data = candidate.model_dump(mode='json') if isinstance(candidate, Stage0MergeCandidate) \
        else dict(candidate)
    data.pop('candidate_id', None)
    data.pop('created_at', None)
    return _digest(data)


@dataclass(frozen=True)
class _Change:
    side: str
    start: int
    end: int
    text: str


def _changes(base: str, side: str, name: str) -> list[_Change]:
    out: list[_Change] = []
    matcher = SequenceMatcher(a=base, b=side, autojunk=False)
    for tag, i1, i2, j1, j2 in matcher.get_opcodes():
        if tag != 'equal':
            out.append(_Change(name, i1, i2, side[j1:j2]))
    return out


def _cross_conflict(left: _Change, right: _Change) -> bool:
    if left.side == right.side:
        return False
    if (left.start, left.end, left.text) == (right.start, right.end, right.text):
        return False
    if left.start == left.end and right.start == right.end:
        return left.start == right.start
    if left.start == left.end:
        return right.start <= left.start <= right.end
    if right.start == right.end:
        return left.start <= right.start <= left.end
    return max(left.start, right.start) < min(left.end, right.end)


def _apply_region(base: str, start: int, end: int, changes: Iterable[_Change]) -> str:
    cursor = start
    parts: list[str] = []
    for change in sorted(changes, key=lambda c: (c.start, c.end)):
        parts.append(base[cursor:change.start])
        parts.append(change.text)
        cursor = change.end
    parts.append(base[cursor:end])
    return ''.join(parts)


def three_way_merge(
    base: str,
    ours: str,
    theirs: str,
    resolutions: Mapping[str, Stage0ConflictResolution] | None = None,
) -> tuple[str, list[Stage0MergeConflict]]:
    """合并不相交/相同变更；重叠变更返回 typed conflict，正文绝不写标记。"""
    all_changes = _changes(base, ours, 'ours') + _changes(base, theirs, 'theirs')
    links: dict[int, set[int]] = {i: set() for i in range(len(all_changes))}
    for i, left in enumerate(all_changes):
        for j in range(i + 1, len(all_changes)):
            if _cross_conflict(left, all_changes[j]):
                links[i].add(j)
                links[j].add(i)

    conflict_nodes = {i for i, peers in links.items() if peers}
    components: list[set[int]] = []
    unseen = set(conflict_nodes)
    while unseen:
        seed = min(unseen)
        component: set[int] = set()
        stack = [seed]
        while stack:
            item = stack.pop()
            if item in component:
                continue
            component.add(item)
            stack.extend(links[item] - component)
        unseen -= component
        components.append(component)

    replacements: list[tuple[int, int, str]] = []
    conflicts: list[Stage0MergeConflict] = []
    for number, component in enumerate(
        sorted(components, key=lambda c: min(all_changes[i].start for i in c)), start=1,
    ):
        changes = [all_changes[i] for i in component]
        lo = min(change.start for change in changes)
        hi = max(change.end for change in changes)
        ours_text = _apply_region(base, lo, hi, (c for c in changes if c.side == 'ours'))
        theirs_text = _apply_region(base, lo, hi, (c for c in changes if c.side == 'theirs'))
        conflict = Stage0MergeConflict(
            id=f'conflict_{number:03d}',
            base_span=(lo, hi),
            base_text=base[lo:hi],
            ours_text=ours_text,
            theirs_text=theirs_text,
        )
        conflicts.append(conflict)
        decision = (resolutions or {}).get(conflict.id)
        if decision is None or decision.choice == 'ours':
            chosen = ours_text
        elif decision.choice == 'theirs':
            chosen = theirs_text
        else:
            chosen = decision.custom_text or ''
        replacements.append((lo, hi, chosen))

    # 非冲突改动两边并集；完全相同的改动去重。
    unique: set[tuple[int, int, str]] = set()
    for index, change in enumerate(all_changes):
        if index not in conflict_nodes:
            unique.add((change.start, change.end, change.text))
    replacements.extend(unique)
    replacements.sort(key=lambda item: (item[0], item[1]))

    cursor = 0
    parts: list[str] = []
    for start, end, text in replacements:
        if start < cursor:
            raise ArtifactUnreadable('diff3 内部生成了重叠 replacement，拒绝猜测顺序')
        parts.append(base[cursor:start])
        parts.append(text)
        cursor = end
    parts.append(base[cursor:])
    return ''.join(parts), conflicts


def make_candidate(
    *,
    expected_round: int,
    expected_source_sha1: str,
    expected_clean_sha1: str,
    base_snapshot: MachineRemovalSnapshot,
    theirs_snapshot: MachineRemovalSnapshot,
    base_text: str,
    ours_text: str,
    theirs_source_sha1: str,
    theirs_text: str,
) -> Stage0MergeCandidate:
    proposed, conflicts = three_way_merge(base_text, ours_text, theirs_text)
    payload = {
        'expected_round': expected_round,
        'expected_source_sha1': expected_source_sha1,
        'expected_clean_sha1': expected_clean_sha1,
        'base_snapshot_sha256': base_snapshot.snapshot_sha256,
        'theirs_snapshot': theirs_snapshot.model_dump(mode='json'),
        'base_clean_sha1': prov.content_sha1(base_text.encode('utf-8')),
        'ours_clean_sha1': prov.content_sha1(ours_text.encode('utf-8')),
        'theirs_source_sha1': theirs_source_sha1,
        'theirs_clean_sha1': prov.content_sha1(theirs_text.encode('utf-8')),
        'base_text': base_text,
        'ours_text': ours_text,
        'theirs_text': theirs_text,
        'proposed_clean_text': proposed,
        'conflicts': [item.model_dump(mode='json') for item in conflicts],
    }
    return Stage0MergeCandidate(candidate_id=_digest(payload), created_at=_now(), **payload)


def resolved_text(
    candidate: Stage0MergeCandidate,
    resolutions: list[Stage0ConflictResolution],
) -> str:
    by_id = {item.conflict_id: item for item in resolutions}
    if len(by_id) != len(resolutions):
        raise HumanEditProtected('同一个 merge conflict 不得处理两次')
    expected = {item.id for item in candidate.conflicts}
    if set(by_id) != expected:
        missing = sorted(expected - set(by_id))
        extra = sorted(set(by_id) - expected)
        raise HumanEditProtected(
            f'所有冲突必须恰好处理一次；missing={missing}, extra={extra}')
    text, conflicts = three_way_merge(
        candidate.base_text, candidate.ours_text, candidate.theirs_text, by_id,
    )
    if [item.model_dump(mode='json') for item in conflicts] != [
        item.model_dump(mode='json') for item in candidate.conflicts
    ]:
        raise HumanEditProtected('候选的 diff3 冲突集已漂移，拒绝套用旧决定')
    return text


def append_audit(
    ctx: RunContext,
    *,
    seq: int,
    previous_sha256: str | None,
    action: str,
    actor: str | None,
    reason: str,
    before_source_sha1: str | None,
    before_clean_sha1: str | None,
    after_source_sha1: str,
    after_clean_sha1: str,
    candidate_id: str | None = None,
    at: str | None = None,
) -> Stage0AuditEntry:
    stable = {
        'seq': seq,
        'previous_sha256': previous_sha256,
        'action': action,
        'actor': actor,
        'reason': reason,
        'before_source_sha1': before_source_sha1,
        'before_clean_sha1': before_clean_sha1,
        'after_source_sha1': after_source_sha1,
        'after_clean_sha1': after_clean_sha1,
        'candidate_id': candidate_id,
    }
    store = ArtifactStore(ctx)
    rel = STAGE0_AUDIT_ENTRY.format(n=seq)
    if store.exists(rel):
        existing, _ = store.read_own(Stage0AuditEntry, rel)
        existing_stable = existing.model_dump(mode='json')
        existing_stable.pop('entry_sha256')
        existing_stable.pop('at')
        if existing_stable == stable:
            return existing
        raise HumanEditProtected(f'审计序号 {seq} 已存在且内容不同；不可覆盖')
    payload = {**stable, 'at': at or _now()}
    entry = Stage0AuditEntry(entry_sha256=_digest(payload), **payload)
    store.write(rel, entry)
    return entry


_AUDIT_FILENAME = re.compile(r'^edit_(\d+)\.json$')


def _audit_numbers(store: ArtifactStore) -> list[int]:
    """Enumerate the durable chain shape, including entries beyond the report head."""
    directory = store.path('stage0/_audit')
    if not directory.exists():
        return []
    if not directory.is_dir():
        raise ArtifactUnreadable('stage0/_audit 不是目录，审计链不可读')
    numbers: list[int] = []
    for path in directory.iterdir():
        if not path.name.startswith('edit_'):
            continue
        match = _AUDIT_FILENAME.fullmatch(path.name)
        if match is None:
            raise ArtifactUnreadable(f'{path.name}: 非法 Stage0 审计条目文件名')
        number = int(match.group(1))
        if path.name != f'edit_{number}.json':
            raise ArtifactUnreadable(f'{path.name}: Stage0 审计序号文件名不是规范形态')
        numbers.append(number)
    if len(numbers) != len(set(numbers)):
        raise ArtifactUnreadable('Stage0 审计链有重复序号文件')
    return sorted(numbers)


def _verify_audit_prefix(
    ctx: RunContext, *, seq: int, head: str | None,
) -> None:
    """Verify the report-owned prefix without silently blessing later entries."""
    previous: str | None = None
    store = ArtifactStore(ctx)
    for number in range(1, seq + 1):
        rel = STAGE0_AUDIT_ENTRY.format(n=number)
        entry, _ = store.read_own(Stage0AuditEntry, rel)
        if entry.seq != number or entry.previous_sha256 != previous:
            raise ArtifactUnreadable(f'{rel}: 审计链序号/前驱不连续')
        if audit_digest(entry) != entry.entry_sha256:
            raise ArtifactUnreadable(f'{rel}: entry_sha256 校验失败，审计记录被改动')
        previous = entry.entry_sha256
    if previous != head:
        raise ArtifactUnreadable(
            f'审计链头不一致：报告={head!r}，重算={previous!r}')


def pending_merge_audit(
    ctx: RunContext, *, seq: int, head: str | None,
) -> Stage0AuditEntry | None:
    """Return the sole recoverable WAL entry immediately after a report head.

    A merge writes its immutable audit entry before the cross-file formal
    commit.  That one exact ``seq + 1`` entry is therefore a durable intent
    record.  Any other chain shape is corruption, not a license to skip rows.
    """
    store = ArtifactStore(ctx)
    _verify_audit_prefix(ctx, seq=seq, head=head)
    expected = list(range(1, seq + 1))
    numbers = _audit_numbers(store)
    if numbers == expected:
        return None
    if numbers != [*expected, seq + 1]:
        raise ArtifactUnreadable(
            f'Stage0 审计链在报告 seq={seq} 之后不是唯一可恢复条目：{numbers}')
    rel = STAGE0_AUDIT_ENTRY.format(n=seq + 1)
    entry, _ = store.read_own(Stage0AuditEntry, rel)
    if entry.seq != seq + 1 or entry.previous_sha256 != head:
        raise ArtifactUnreadable(f'{rel}: 待恢复审计条目没有紧接当前报告链头')
    if audit_digest(entry) != entry.entry_sha256:
        raise ArtifactUnreadable(f'{rel}: entry_sha256 校验失败，审计记录被改动')
    return entry


def verify_audit_chain(ctx: RunContext, *, seq: int, head: str | None) -> None:
    """Verify both hashes and the exact set of entries owned by the report."""
    store = ArtifactStore(ctx)
    numbers = _audit_numbers(store)
    expected = list(range(1, seq + 1))
    if numbers != expected:
        raise ArtifactUnreadable(
            f'Stage0 审计链与报告 seq={seq} 不闭合：盘上={numbers}，预期={expected}；'
            '额外条目可能是一次中断的 merge，必须沿同一决定重放恢复')
    _verify_audit_prefix(ctx, seq=seq, head=head)
