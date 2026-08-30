#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""一次调用内的 operator 只读快照。

这里复用完成度与各层 ``check_only`` 的生产入口，只负责让 ``state``、
``inbox`` 和终审事实收集在同一次调用里看到同一个代码指纹、同一轮报告。
快照只活在调用栈内：不落盘、不做模块级缓存，也没有跨写盘刷新接口。
"""
from __future__ import annotations

import hashlib
import json
import os
from dataclasses import dataclass
from pathlib import Path
from types import MappingProxyType
from typing import Mapping

from contracts.invariants import STAGES
from contracts.schema.orchestration import NovelOverview
from contracts.schema.report import CheckReport
from novel import paths, prov
from novel.errors import ArtifactUnreadable, TerminalState, UpstreamMissing
from novel.llm.ledger import ledger_path, read_jsonl_rows
from novel.orchestrator import beacon
from novel.orchestrator.nodes import check_node
from novel.orchestrator.state import overview_at, readonly_ctx
from novel.stages import check_only


_EXPECTED_UNAVAILABLE = (UpstreamMissing, ArtifactUnreadable, TerminalState)


@dataclass(frozen=True)
class CheckObservation:
    """一层复检的结果；失败不能在采集时被洗成“没有报告”。"""

    report: CheckReport | None = None
    error: Exception | None = None

    def for_view(self) -> CheckReport | None:
        """保持既有 state/inbox 语义：预期不可读显示未知，代码 bug 继续抛。"""
        if self.error is None:
            return self.report
        if isinstance(self.error, _EXPECTED_UNAVAILABLE):
            return None
        raise self.error


@dataclass(frozen=True)
class OperatorReadSnapshot:
    """只属于一次命令/请求的已物化读面。"""

    base: Path
    sid: str
    novel_dir: Path
    code: str
    surface_seal: str
    overview: NovelOverview
    checks: Mapping[str, CheckObservation]

    def check(self, stage: str) -> CheckObservation:
        return self.checks[stage]


def capture_operator_snapshot(
    base: str | os.PathLike[str], sid: str,
) -> OperatorReadSnapshot:
    """冻结一次 code/overview/七层复检；调用结束即丢弃。"""
    base_path = Path(base).resolve()
    novel_dir = paths.resolve_novel_dir(base_path, sid).resolve()
    code = prov.code_version()
    before = read_surface_seal(base_path, sid, code=code)
    overview = overview_at(novel_dir, code)
    checks: dict[str, CheckObservation] = {}
    for stage in STAGES:
        try:
            report = check_only(stage)(
                readonly_ctx(novel_dir, check_node(stage), code))
        except Exception as exc:  # noqa: BLE001 —— 由消费者按原有策略解释
            checks[stage] = CheckObservation(error=exc)
        else:
            checks[stage] = CheckObservation(report=report)
    after = read_surface_seal(base_path, sid, code=code)
    if after != before:
        raise ArtifactUnreadable(
            'operator 只读快照采集期间本书代码/产物/审批盘面发生变化；'
            '拒绝返回混合时点的 state/check，请重试')
    return OperatorReadSnapshot(
        base=base_path,
        sid=sid,
        novel_dir=novel_dir,
        code=code,
        surface_seal=after,
        overview=overview,
        checks=MappingProxyType(checks),
    )


def _service_facts(
    base: Path, sid: str,
) -> dict[str, object]:
    """只投影当前终审收集器消费的本书服务事实；别书追加不假漂移。"""
    ledger = [
        row for row in read_jsonl_rows(ledger_path(base))
        if row.get('sid') == sid
    ]
    progress = [
        event.model_dump(mode='json')
        for event in beacon.read_events(base)
        if event.sid == sid
    ]
    return {
        'ledger': ledger,
        'progress': progress,
    }


def read_surface_seal(
    base: str | os.PathLike[str], sid: str, *,
    code: str | None = None,
    include_final_evidence_service: bool = False,
    excluded_review: str | os.PathLike[str] | None = None,
) -> str:
    """当前坐标：代码 + 本书文件清单/审批，可选终审服务事实。

    正式产物都经临时文件 + ``os.replace`` 提交，因此路径、大小、mtime_ns
    足以在正常写入口下发现调用期间漂移。终审包本身由 finalizer 单独做字节
    compare-and-swap，这里刻意排除它、同文件互斥锁与唯一临时文件。
    """
    base_path = Path(base).resolve()
    novel_dir = paths.resolve_novel_dir(base_path, sid).resolve()
    review = (
        Path(excluded_review).resolve()
        if excluded_review is not None
        else paths.review_package_path(novel_dir).resolve()
    )
    review_lock = review.with_name(review.name + '.lock')
    legacy_temporary = review.with_name(review.name + '.operator-sync.tmp')
    temporary_prefix = f'.{review.name}.operator-sync.'
    digest = hashlib.sha256()
    digest.update((code if code is not None else prov.code_version()).encode('utf-8'))

    observed = [
        path for path in novel_dir.rglob('*')
        if path.is_file()
        and path not in (review, review_lock, legacy_temporary)
        and not (
            path.parent == review.parent
            and path.name.startswith(temporary_prefix)
            and path.name.endswith('.tmp')
        )
    ]
    approval = base_path / '_service' / 'approvals' / f'{sid}.json'
    if approval.is_file():
        observed.append(approval)
    for path in sorted(observed, key=lambda item: str(item).casefold()):
        try:
            stat = path.stat()
        except OSError as exc:
            raise ArtifactUnreadable(
                f'只读快照封印期间文件变化：{path}: {exc!r}') from exc
        try:
            name = path.relative_to(base_path).as_posix()
        except ValueError:
            name = str(path)
        digest.update(name.encode('utf-8'))
        digest.update(b'\0')
        digest.update(str(stat.st_size).encode('ascii'))
        digest.update(b'\0')
        digest.update(str(stat.st_mtime_ns).encode('ascii'))
        digest.update(b'\n')
    if include_final_evidence_service:
        try:
            service = _service_facts(base_path, sid)
        except (OSError, UnicodeDecodeError, ValueError) as exc:
            raise ArtifactUnreadable(
                f'终审封印期间服务事实不可读：{type(exc).__name__}: {exc}') from exc
        digest.update(json.dumps(
            service, ensure_ascii=False, sort_keys=True, separators=(',', ':'),
        ).encode('utf-8'))
    return digest.hexdigest()
