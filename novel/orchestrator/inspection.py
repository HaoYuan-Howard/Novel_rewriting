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
from typing import Any, Mapping

from contracts.artifacts import ARTIFACT_PATHS
from contracts.invariants import STAGES
from contracts.schema import artifact_key
from contracts.schema.orchestration import NovelOverview
from contracts.schema.report import CheckReport
from novel import paths, prov
from novel.errors import ArtifactUnreadable, TerminalState, UpstreamMissing
from novel.llm.ledger import ledger_path, read_jsonl_rows
from novel.orchestrator import approvals, beacon
from novel.orchestrator.nodes import check_node
from novel.orchestrator.state import overview_at, readonly_ctx
from novel.stages import check_only


_EXPECTED_UNAVAILABLE = (UpstreamMissing, ArtifactUnreadable, TerminalState)

# 这些 schema-valid JSON 是一次性 typed request，不是 planner 的当前业务面。
# 只有 exact node/flag 消费它时，execution permit 才按原始字节显式绑定；若在这里
# 也全局扫描，一份尚未使用的人工草稿会让所有无关节点 token 一起过期。
_EXTERNAL_REQUEST_ARTIFACTS = frozenset({
    ARTIFACT_PATHS['stage2.global_edit'],
    ARTIFACT_PATHS['stage2.blueprint_relation_edit'],
    ARTIFACT_PATHS['stage4.blind_chapter_edit'],
    ARTIFACT_PATHS['stage4.plan_edit'],
    ARTIFACT_PATHS['stage6.hook_edit'],
    ARTIFACT_PATHS['stage7.name_map_edit'],
})


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
    base: str | os.PathLike[str], sid: str, *, code: str | None = None,
) -> OperatorReadSnapshot:
    """冻结一次 code/overview/七层复检；调用结束即丢弃。

    ``code`` 只供已在动作前后各做一次 epoch identity CAS 的 Supervisor；
    普通 CLI/API 不得传入，仍由本函数现场读取仓库身份。
    """
    base_path = Path(base).resolve()
    novel_dir = paths.resolve_novel_dir(base_path, sid).resolve()
    code = code or prov.code_version()
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


def _active_artifact_paths(novel_dir: Path) -> tuple[Path, ...]:
    """只枚举当前注册产物，主动剪掉归档、scratch 与历史执行树。

    动态逐章/逐轮路径不能从模板预先展开，所以仍需查看七个 stage 根；但
    ``os.walk`` 在进入 ``_rounds`` / ``_revalidation`` / agent scratch 之前就
    剪枝，不会像旧 ``novel_dir.rglob('*')`` 一样遍历整本历史。JSON 是否现役
    复用 schema 路由；两个非 JSON 产物则复用路径目录的正式字面。
    """
    plain = {
        ARTIFACT_PATHS['stage0.clean_text'],
        paths.book_txt_rel(novel_dir),
    }
    observed: list[Path] = []
    for stage in STAGES:
        root = novel_dir / stage
        if not root.is_dir():
            continue
        for directory, dirnames, filenames in os.walk(root):
            # 所有下划线目录都是服务/归档/中间态，不属于 active artifact
            # registry；在这里剪枝而不是遍历后再过滤，历史规模不进入热路。
            dirnames[:] = [name for name in dirnames if not name.startswith('_')]
            parent = Path(directory)
            for name in filenames:
                if name.startswith('.'):
                    continue
                candidate = parent / name
                try:
                    rel = candidate.relative_to(novel_dir).as_posix()
                except ValueError:  # pragma: no cover —— os.walk 根保证在书内
                    continue
                registered = artifact_key(rel)
                if rel in plain or (
                    registered is not None
                    and registered not in _EXTERNAL_REQUEST_ARTIFACTS
                ):
                    observed.append(candidate)
    return tuple(sorted(set(observed), key=lambda item: str(item).casefold()))


def _read_stable_bytes(path: Path, *, what: str) -> bytes:
    """读取一次真实字节，并拒绝读的前后身份发生变化。"""
    try:
        before = path.stat()
        payload = path.read_bytes()
        after = path.stat()
    except OSError as exc:
        raise ArtifactUnreadable(f'{what}变化或不可读：{path}: {exc!r}') from exc
    coordinate = lambda stat: (stat.st_dev, stat.st_ino, stat.st_size, stat.st_mtime_ns)
    if coordinate(before) != coordinate(after) or len(payload) != after.st_size:
        raise ArtifactUnreadable(f'{what}读取期间发生变化：{path}')
    return payload


def _update_entry(digest: Any, base: Path, path: Path) -> None:
    """把 active 路径与真实字节同时绑定进 seal。"""
    try:
        name = path.relative_to(base).as_posix()
    except ValueError:
        name = os.fspath(path)
    payload = _read_stable_bytes(path, what='active surface')
    digest.update(name.encode('utf-8'))
    digest.update(b'\0')
    digest.update(str(len(payload)).encode('ascii'))
    digest.update(b'\0')
    digest.update(hashlib.sha256(payload).digest())
    digest.update(b'\n')


def read_surface_seal(
    base: str | os.PathLike[str], sid: str, *,
    code: str | None = None,
    include_final_evidence_service: bool = False,
    excluded_review: str | os.PathLike[str] | None = None,
) -> str:
    """当前坐标：代码 + active artifacts + 审批 head，可选终审事实。

    终审 Markdown 由 finalizer 自己做字节 CAS；``_rounds``、临时文件、旧 run
    与 scratch 不是当前执行输入，不进入 seal。外部 typed request 还会由
    execution permit 按 exact flag 另绑原始字节。
    """
    base_path = Path(base).resolve()
    novel_dir = paths.resolve_novel_dir(base_path, sid).resolve()
    # 参数保留给 finalizer/旧调用兼容；review 本来就不属于注册活动产物。
    del excluded_review
    digest = hashlib.sha256()
    digest.update((code if code is not None else prov.code_version()).encode('utf-8'))
    digest.update(b'\nactive-artifacts-v2\n')
    observed = _active_artifact_paths(novel_dir)
    for path in observed:
        _update_entry(digest, base_path, path)

    approval_head = approvals.approval_head_bytes(base_path, sid)
    if approval_head is not None:
        digest.update(b'approval-head\0')
        digest.update(approval_head)
    else:
        digest.update(b'approval-head\0missing')
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
