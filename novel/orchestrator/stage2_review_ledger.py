"""旧 Stage2 独立席位账的只读审计解码器。

保留原始调用、拒因和旧归档事实，供冻结历史说明使用。当前执行、API 与 CLI
不消费本模块；没有预留、补账、退款、归档重开或任何写入入口。新工作流只使用
work_units 的稳定工作单元累计账，旧输出根由整根切换流程归档。
"""
from __future__ import annotations

import hashlib
import os
from pathlib import Path
from typing import Any

from pydantic import TypeAdapter

from contracts.schema.orchestration import (
    Stage2ReviewLedgerArchive, Stage2ReviewLedgerReconciliation, Stage2ReviewLedgerTurn,
)
from novel import paths
from novel.errors import PathRefused, TerminalState

_RAW_LEDGER = TypeAdapter(dict[str, Any])
_ARCHIVABLE_SCHEMA_VERSIONS = (1, 2)


class Stage2ReviewBudgetRefused(TerminalState):
    """仅供历史读取：原始账无法可信解码，不是当前预算判定。"""


def ledger_path(
    base: str | os.PathLike[str], sid: str,
    *, source_sha256: str, main_text_sha256: str,
) -> Path:
    checked_sid = paths.check_sid(sid)
    for label, value in (
            ('source_sha256', source_sha256),
            ('main_text_sha256', main_text_sha256)):
        if (len(value) != 64
                or any(ch not in '0123456789abcdef' for ch in value)):
            raise ValueError(f'{label} 不是 64 位小写 SHA-256')
    # Windows 的锁文件还会追加 ``.lock``；直接把两个 64 位 SHA 拼进文件名会让
    # 深书目录越过传统 MAX_PATH。文件名使用二者的内容地址，账内仍保留完整双 SHA
    # 并在读取后逐字核对，因此没有缩短业务 identity。
    identity_sha256 = hashlib.sha256(
        f'{source_sha256}:{main_text_sha256}'.encode('ascii')).hexdigest()
    target = (
        paths.service_runs_dir(base) / checked_sid / 'stage2'
        / 'global_review_ledgers'
        / f'{identity_sha256}.json'
    )
    service_root = paths.service_dir(base).absolute()
    if service_root.is_symlink():
        raise PathRefused('_service 根不能是符号链接')
    resolved_root = service_root.resolve()
    resolved = target.absolute().resolve()
    try:
        resolved.relative_to(resolved_root)
    except ValueError as exc:
        raise PathRefused('Stage2 review ledger 越出 _service') from exc
    return resolved


def _raw_ledger(path: Path) -> dict[str, Any] | None:
    if not path.exists():
        return None
    if path.is_symlink() or not path.is_file():
        raise Stage2ReviewBudgetRefused(
            f'Stage2 review ledger 不是普通文件：{path}')
    try:
        raw = _RAW_LEDGER.validate_json(path.read_bytes())
    except Exception as exc:  # noqa: BLE001 —— 坏账不能静默重置预算
        raise Stage2ReviewBudgetRefused(
            f'Stage2 review ledger 不是合法 JSON object：{path}') from exc
    return raw


def read_any_version(path: Path) -> tuple[dict[str, Any], Stage2ReviewLedgerArchive]:
    """只供归档：把 v1/v2 账读成一份 typed archive 记录，不产生可预留的新账。

    v1 turn 行的 ordinal/purpose 都落在 v2 的取值面内，可以逐字复读；
    ``max_calls`` / ``state`` 只作为原始事实保留在 archive 元数据里。
    """
    raw = _raw_ledger(path)
    if raw is None:
        raise TerminalState(f'Stage2 review ledger 不在盘上：{path}')
    version = raw.get('schema_version')
    if version not in _ARCHIVABLE_SCHEMA_VERSIONS:
        raise TerminalState(
            f'Stage2 review ledger schema_version={version!r} 不在可归档面内')
    try:
        turns = [
            Stage2ReviewLedgerTurn.model_validate(row)
            for row in raw.get('turns', [])]
        reconciliations = [
            Stage2ReviewLedgerReconciliation.model_validate(row)
            for row in raw.get('reconciliations', [])]
        max_calls = int(raw['max_calls'])
        state = str(raw['state'])
    except Exception as exc:  # noqa: BLE001
        raise TerminalState(
            f'Stage2 review ledger 无法按可归档形状读取：{path}') from exc
    archive = Stage2ReviewLedgerArchive(
        actor='pending', reason='pending', archived_at=0,
        original_schema_version=int(version),
        original_max_calls=max_calls,
        original_state=state,  # type: ignore[arg-type]
        original_sha256=hashlib.sha256(path.read_bytes()).hexdigest(),
        turns=turns, reconciliations=reconciliations,
    )
    return raw, archive


def _ledger_root(base: str | os.PathLike[str], sid: str) -> Path:
    checked_sid = paths.check_sid(sid)
    root = (
        paths.service_runs_dir(base) / checked_sid / 'stage2'
        / 'global_review_ledgers')
    service_root = paths.service_dir(base).absolute()
    if service_root.is_symlink():
        raise PathRefused('_service 根不能是符号链接')
    try:
        root.absolute().resolve().relative_to(service_root.resolve())
    except ValueError as exc:
        raise PathRefused('Stage2 review ledger 目录越出 _service') from exc
    return root


def list_ledgers(base: str | os.PathLike[str], sid: str) -> list[dict[str, Any]]:
    """只读列出一本书全部席位账（任意可归档版本）的无正文摘要。"""
    root = _ledger_root(base, sid)
    if not root.is_dir():
        return []
    return [
        _inspect_path(sid, candidate)
        for candidate in sorted(root.glob('*.json'))
        if candidate.is_file() and not candidate.is_symlink()
    ]


def _inspect_path(sid: str, path: Path) -> dict[str, Any]:
    raw, archive = read_any_version(path)
    return {
        'sid': sid,
        'read_only': True,
        'retired': True,
        'ledger_path': str(path),
        'source_sha256': raw.get('source_sha256'),
        'main_text_sha256': raw.get('main_text_sha256'),
        'schema_version': raw.get('schema_version'),
        'max_calls': archive.original_max_calls,
        'state': archive.original_state,
        'turns': [{
            'ordinal': row.ordinal, 'purpose': row.purpose,
            'outcome': row.outcome, 'exit_code': row.exit_code,
            'parent_run_id': row.parent_run_id,
            'session_run_id': row.session_run_id,
            'attempts': len(row.attempts),
            'rejections': len(row.rejections),
            'latest_rejection': (
                row.rejections[-1].model_dump(mode='json')
                if row.rejections else None),
        } for row in archive.turns],
        'reconciliations': len(archive.reconciliations),
        'archived_ledgers': (
            len(raw.get('archived_ledgers', []))
            if raw.get('schema_version') == 2 else 0),
        'original_sha256': archive.original_sha256,
    }


def inspect_ledger(
    base: str | os.PathLike[str], sid: str, *,
    source_sha256: str, main_text_sha256: str,
) -> dict[str, Any]:
    """只读展示一份席位账（任意可归档版本）的无正文摘要。"""
    return _inspect_path(sid, ledger_path(
        base, sid, source_sha256=source_sha256,
        main_text_sha256=main_text_sha256))
