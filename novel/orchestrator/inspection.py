#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""一次调用内的 operator 只读快照。

这里复用完成度与各层 ``check_only`` 的生产入口，只负责让 ``state``、
``inbox`` 和交付校验在同一次调用里看到同一个代码指纹、同一轮报告。
快照只活在调用栈内：不落盘、不做模块级缓存，也没有跨写盘刷新接口。
"""
from __future__ import annotations

import hashlib
import os
from contextlib import contextmanager
from dataclasses import dataclass
from pathlib import Path
from types import MappingProxyType
from typing import Any, Iterator, Mapping

from contracts.artifacts import ARTIFACT_PATHS, RETIRED_ARTIFACT_PATHS
from contracts.invariants import STAGES
from contracts.schema import artifact_key
from contracts.schema.orchestration import NovelOverview
from contracts.schema.report import CheckReport
from novel import paths, prov
from novel.errors import ArtifactUnreadable, TerminalState, UpstreamMissing
from novel.orchestrator import approvals
from novel.orchestrator.nodes import check_node
from novel.orchestrator.state import overview_at, readonly_ctx
from novel.stages import check_only, checkable_stages
from novel.store import readonly_snapshot_memo


_EXPECTED_UNAVAILABLE = (UpstreamMissing, ArtifactUnreadable, TerminalState)


@contextmanager
def approval_read_snapshot(
    base: str | os.PathLike[str], sid: str,
) -> Iterator[tuple[NovelOverview, str]]:
    """审批 GET 在一个盘面内复用代码与消费投影，不构建 operator 动作。

    审批页与进度页会同时读取本书。若每个内部闸门重算代码和依赖，
    审批读锁会长期占用并拖住进度；此处只在本次请求内共享，结束复验。
    """
    novel_dir = paths.resolve_novel_dir(base, sid).resolve()
    with readonly_snapshot_memo(novel_dir):
        code = prov.code_version()
        before = read_surface_seal(base, sid, code=code)
        overview = overview_at(novel_dir, code)
        with approvals.read_projection(overview=overview, code=code):
            yield overview, code
        if read_surface_seal(base, sid, code=code) != before:
            raise ArtifactUnreadable('审批读取期间盘面发生变化；请重新读取当前状态')

# 这些 schema-valid JSON 是一次性 typed request，不是 planner 的当前业务面。
# 只有 exact node/flag 消费它时，execution permit 才按原始字节显式绑定；若在这里
# 也全局扫描，一份尚未使用的人工草稿会让所有无关节点 token 一起过期。
# ⚠ F8A：新链没有 typed edit 请求面（人工改稿闸随旧链退役），旧链的六个
#   `*_edit.json` 路径键已移入 `RETIRED_ARTIFACT_PATHS`；本表现在为空集，
#   结构保留是因为巡检仍按「显式登记的 externally-bound 请求」口径扫描。
_EXTERNAL_REQUEST_ARTIFACTS = frozenset({
    rel for rel in RETIRED_ARTIFACT_PATHS.values() if rel.endswith('_edit.json')
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
        """本层的复检观测；**没采集的层不是绿灯，也不是 KeyError**。

        F8c：快照只采集有层级复检器的层（stage0/1）；新链 Stage2/4/5/7 的
        复检是节点级 `--check-only`，不在这个快照里。`inbox_from_snapshot` 与
        CLI `state` 都按 `STAGES` 逐层读（`.INVALID` 投影与 wontfix 账覆盖
        全部层）⇒ 对这些层返回**未采集**观测：`report=None`、`error=None`，
        `for_view()` 给 None，界面显示「校验未知」（I-42：未知≠通过）。
        不在 `STAGES` 里的层名是拼写错误，仍当场 KeyError——「打错层名」
        与「这一层没有层级复检器」必须分得开。
        """
        observation = self.checks.get(stage)
        if observation is not None:
            return observation
        if stage not in STAGES:
            raise KeyError(stage)
        return CheckObservation()


def capture_operator_snapshot(
    base: str | os.PathLike[str], sid: str, *, code: str | None = None,
) -> OperatorReadSnapshot:
    """冻结一次 code/overview/各层复检；调用结束即丢弃。

    ``code`` 只供已在动作前后各做一次 epoch identity CAS 的 Supervisor；
    普通 CLI/API 不得传入，仍由本函数现场读取仓库身份。
    """
    base_path = Path(base).resolve()
    novel_dir = paths.resolve_novel_dir(base_path, sid).resolve()
    code = code or prov.code_version()
    before = read_surface_seal(base_path, sid, code=code)
    overview = overview_at(novel_dir, code)
    checks: dict[str, CheckObservation] = {}
    # 复检中的下游审批检查也属于本次读域；否则每个下游消费者
    # 会再次读取 Git，给同一进度响应混入多个代码时点。
    with approvals.read_projection(overview=overview, code=code):
        # F8c：只采集有层级复检器的层（stage0/1）。Stage2/4/5/7 的复检是
        # 节点级 `--check-only`，不在这个快照里——`check(stage)` 对它们返回
        # 未采集，`for_view()` 给 None，界面显示「校验未知」而不是绿灯。
        for stage in checkable_stages():
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
) -> str:
    """当前坐标：代码 + active artifacts + 审批 head。

    ``_rounds``、非注册文档、临时文件、旧 run
    与 scratch 不是当前执行输入，不进入 seal。外部 typed request 还会由
    execution permit 按 exact flag 另绑原始字节。
    """
    base_path = Path(base).resolve()
    novel_dir = paths.resolve_novel_dir(base_path, sid).resolve()
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
    # ⚠ F8c：旧链 `stage2.analyze` 首跑恢复面（draft_recovery source_paths 与
    #   pending initial run 树）随 Stage2 旧链整层退役——其节点与恢复旗标不在现役
    #   `NODES`，新链 `str06.stage2.*` 的输入面由正式 Stage1 产物与已购凭证承载，
    #   不再有「分析首跑未落盘」这一中间盘面需要进 seal。
    return digest.hexdigest()
