#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""operator execution permit：一次 next 铸 ready token，blocked 恢复显式铸造。

``operator next`` 在同一冻结 snapshot 内为 ready 原样动作铸造 opaque execution
token；blocked action 只描述 blocker，恢复命令的 exact node + 全部 flags 仍须显式
交到本模块。ready 只接受 next 原样动作；blocked 只接受本文件策略表登记的恢复组合。
许可还绑定代码、读面、贵贱、写盘效应，以及已登记外部输入文件的原始字节摘要。

本模块不写服务账、不启动进程、不发请求。现役恢复表只登记免费 ``--check-only``
复检（F8b-3）：旧链恢复旗标族（--revalidate/--apply-edit/--revise/--recover-*）
与 Stage4/5/6/7 人工闸的专属恢复分支随旧链整层退役，其 blocker 只能由旧链
planner 发出、生产入口已拒识；付费节点的恢复是候选 runner 自身续跑（F7c），
不经本表发布付费恢复动作。
"""
from __future__ import annotations

import hashlib
import json
import os
from collections.abc import Mapping
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Final, Literal

from contracts.invariants import NODES
from contracts.schema.api import (
    ExecutionPermit, ExecutionPermitInput, OperatorNext, OperatorNextDecision,
)
from novel import paths, prov
from novel.errors import (
    ArtifactUnreadable, TerminalState, UnsupportedRunMode, UpstreamMissing,
)
from novel.orchestrator import queue
from novel.orchestrator.inspection import read_surface_seal
from novel.orchestrator.nodes import (
    REPO_ROOT, flag_tokens, flag_value,
    is_paid, validate_run, write_effect,
)


PERMIT_JSON_ENV = 'NOVEL_OPERATOR_EXECUTION_PERMIT_JSON'
STEP_INDEX_ENV = 'NOVEL_OPERATOR_EXECUTION_STEP_INDEX'


@dataclass(frozen=True)
class RecoveryRunPolicy:
    """一个已完成迁移的 exact token 组合及其可消费 blocker。"""

    node: str
    tokens: frozenset[str]
    waiting_on: frozenset[str]
    paid: bool = False


def _t(*tokens: str) -> frozenset[str]:
    return frozenset(tokens)


_INBOX = queue.WAITING_ON_INBOX
_RESUME = queue.WAITING_ON_RESUME_DECISION
#: 新链节点的通用 blocker 面。
_STR06 = frozenset({_INBOX, _RESUME})

_W0 = frozenset({_INBOX, _RESUME})


#: 首批恢复闭集。这里登记的是**全部 flag token**，不是只登记主操作；
#: 例如 revalidate 没有 actor/reason 就不是同一行。表外组合即使节点 argparse
#: 暂时能解析，也不能拿到 Commander 执行许可。
RECOVERY_RUN_POLICIES: Final[tuple[RecoveryRunPolicy, ...]] = (
    # ⚠ F8b-3（S-05 deprecation）：旧链恢复旗标族（--revalidate/--apply-edit/
    # --revise/--recover-* 等）随旧链整层退役——它们的 blocker 只能由旧链
    # planner 发出，生产入口已拒识。新链的免费复检是节点级 ``--check-only``
    # （blocked 恢复与零成本重验同一形状）；付费节点的恢复是候选 runner 自身
    # 续跑（F7c 精确补尾），不经 permit 表发布付费恢复动作。
    RecoveryRunPolicy('stage0.remove', _t('--check-only'), _W0),
    RecoveryRunPolicy(
        'stage1.partition', _t('--check-only'),
        frozenset({_INBOX, _RESUME}),
    ),
    RecoveryRunPolicy('str06.stage2.book', _t('--check-only'), _STR06),
    RecoveryRunPolicy('str06.stage2.chapters', _t('--check-only'), _STR06),
    RecoveryRunPolicy('str06.stage2.names', _t('--check-only'), _STR06),
    RecoveryRunPolicy('str06.stage2.associate', _t('--check-only'), _STR06),
    RecoveryRunPolicy('str06.stage4.preserve', _t('--check-only'), _STR06),
    RecoveryRunPolicy('str06.stage4.body_design', _t('--check-only'), _STR06),
    RecoveryRunPolicy('str06.stage4.bind', _t('--check-only'), _STR06),
    RecoveryRunPolicy('str06.stage4.hook_design', _t('--check-only'), _STR06),
    RecoveryRunPolicy('str06.stage5.hook', _t('--check-only'), _STR06),
    RecoveryRunPolicy('str06.stage5.body', _t('--check-only'), _STR06),
    RecoveryRunPolicy('str06.stage7.naming', _t('--check-only'), _STR06),
)


def _policy_index() -> dict[tuple[str, frozenset[str]], RecoveryRunPolicy]:
    out: dict[tuple[str, frozenset[str]], RecoveryRunPolicy] = {}
    for row in RECOVERY_RUN_POLICIES:
        key = (row.node, row.tokens)
        if key in out:  # pragma: no cover —— import 时就应失败
            raise RuntimeError(f'重复 execution recovery policy：{key}')
        out[key] = row
    return out


RECOVERY_RUN_POLICY_INDEX: Final = _policy_index()


ExternalPathScope = Literal['repo', 'book', 'book_or_service_edit']

#: 会把**文件字节**送进 producer 的 valued flag。actor/reason/hash/run_id 等纯值
#: 不在这里；漏登记一个文件 flag 会让许可只绑定路径字符串而没绑定真正输入。
#: 会把**文件字节**送进 producer 的 valued flag。actor/reason/hash/run_id 等纯值
#: 不在这里；漏登记一个文件 flag 会让许可只绑定路径字符串而没绑定真正输入。
#: ⚠ F8b-3：旧链编辑/恢复族（--apply-edit/--preview-edit/--recover-*）随旧链
#: 退役，现役节点旗标面只剩 --check-only（纯免费复检，无外部文件输入）⇒ 空表。
EXTERNAL_INPUT_POLICIES: Final[
    dict[tuple[str, str], ExternalPathScope]
] = {}


def _resolve_external_path(
    *, current: OperatorNextDecision, node: str, flag: str, raw: str,
) -> Path:
    return _resolve_external_path_at(
        base=current.base, sid=current.sid, node=node, flag=flag, raw=raw)


def _resolve_external_path_at(
    *, base: str | os.PathLike[str], sid: str, node: str, flag: str, raw: str,
) -> Path:
    """按 permit 自身坐标解析外部输入，不需要重建 OperatorNext。"""
    base_path = Path(base).resolve()
    novel_dir = paths.resolve_novel_dir(base_path, sid).resolve()
    scope = EXTERNAL_INPUT_POLICIES[(node, flag)]
    candidate = Path(raw)
    if scope == 'repo':
        return (candidate if candidate.is_absolute() else REPO_ROOT / candidate).resolve()
    if scope == 'book':
        rel = paths.edit_rel(novel_dir, raw, default_rel=f'permit/{flag[2:]}')
        return (novel_dir / rel).resolve()
    if scope != 'book_or_service_edit':  # pragma: no cover —— Literal + closed table
        raise AssertionError(f'未知 external input scope：{scope}')
    target = (candidate if candidate.is_absolute() else novel_dir / candidate).resolve()
    allowed = (paths.service_dir(base_path) / 'edits' / sid).resolve()
    # 相对路径也可能用 ``..`` 逃出书目录；必须在 resolve 后统一核边界，
    # 不能把“不是 absolute”误当成“必在当前书”。
    if not any(_is_within(target, root) for root in (novel_dir, allowed)):
        raise UnsupportedRunMode(
            f'{flag} 外部输入必须在当前书或 {allowed} 内：{target}')
    return target


def _is_within(path: Path, root: Path) -> bool:
    try:
        path.relative_to(root)
    except ValueError:
        return False
    return True


def _external_inputs(
    current: OperatorNextDecision, node: str, flags: tuple[str, ...],
) -> list[ExecutionPermitInput]:
    out: list[ExecutionPermitInput] = []
    for flag in flag_tokens(flags):
        if (node, flag) not in EXTERNAL_INPUT_POLICIES:
            continue
        raw = flag_value(flags, flag)
        if raw is None:  # validate_run 正常会先拒；这里保留关闭式错误面
            raise UnsupportedRunMode(f'{node} {flag} 缺外部输入路径')
        target = _resolve_external_path(
            current=current, node=node, flag=flag, raw=raw)
        if not target.is_file():
            raise UpstreamMissing(f'{node} {flag} 外部输入不在盘上：{target}')
        try:
            payload = target.read_bytes()
        except OSError as exc:
            raise ArtifactUnreadable(
                f'读不了 {node} {flag} 外部输入 {target}: {exc!r}') from exc
        out.append(ExecutionPermitInput(
            flag=flag, argument=raw, resolved_path=os.fspath(target),
            bytes=len(payload), sha256=hashlib.sha256(payload).hexdigest(),
        ))
    return out


def _require_recovery_policy(
    current: OperatorNextDecision, node: str, flags: tuple[str, ...],
) -> RecoveryRunPolicy:
    if current.action is None or current.state != 'blocked':
        raise UnsupportedRunMode('recovery permit 只在 operator next=blocked 时存在')
    tokens = flag_tokens(flags)
    if len(tokens) != len(set(tokens)):
        raise UnsupportedRunMode('execution permit 不接受重复 flag token')
    row = RECOVERY_RUN_POLICY_INDEX.get((node, frozenset(tokens)))
    if row is None:
        raise UnsupportedRunMode(
            f'{node} 的 flag 组合 {list(tokens)} 未登记为 blocked recovery；'
            '不得复用 blocker action hash 或退回默认付费重跑')
    waiting_on = current.action.waiting_on
    if waiting_on not in row.waiting_on:
        raise UnsupportedRunMode(
            f'{node} {list(tokens)} 不能处理当前 blocker {waiting_on!r}')

    # 恢复不能越过 blocker 去动更晚层。恢复表只登记免费 --check-only 复检，
    # 其 rank 不大于当前 blocker 节点；越闸一律关闭式拒绝。
    rank = {name: index for index, name in enumerate(NODES)}
    if rank[node] > rank[current.action.node]:
        raise UnsupportedRunMode(
            f'{node} 位于当前 blocker {current.action.node} 之后；恢复不得越闸写下游')

    paid = is_paid(node, flags)
    if paid != row.paid:
        raise UnsupportedRunMode(
            f'execution recovery policy 贵贱漂移：登记 paid={row.paid}，当前判定={paid}')
    if paid:
        # F8b-3：恢复表不再登记任何付费行——付费节点的恢复是候选 runner
        # 自身续跑（F7c），这里到达即表与贵贱判定分家，关闭式拒绝。
        raise UnsupportedRunMode(
            f'{node} {sorted(flag_tokens(flags))} 不是登记的免费恢复组合')
    return row


def _permit_from_decision(
    current: OperatorNextDecision,
    node: str,
    flags: tuple[str, ...] | list[str],
    *,
    recheck_code: bool,
    recheck_surface: bool,
) -> ExecutionPermit:
    """从冻结 decision 铸 permit；是否重读 surface 由调用边界决定。"""
    exact_flags = tuple(flags)
    validate_run(node, exact_flags)
    tokens = flag_tokens(exact_flags)
    if len(tokens) != len(set(tokens)):
        raise UnsupportedRunMode('execution permit 不接受重复 flag token')

    if current.state == 'ready':
        if current.action is None:  # schema 已守；防 model_construct 绕过
            raise UnsupportedRunMode('ready next 缺 action')
        if node != current.action.node or list(exact_flags) != current.action.flags:
            raise UnsupportedRunMode(
                'ready permit 只接受当前 operator next 的 exact node/flags；'
                '盘面或参数变化后请重新取 next')
        kind = 'ready'
        action_sha256 = current.action.action_sha256
    elif current.state == 'blocked':
        _require_recovery_policy(current, node, exact_flags)
        kind = 'recovery'
        action_sha256 = None
    else:
        raise UnsupportedRunMode(
            'pipeline_complete 没有 node execution permit；先按 delivery 终验定位问题')

    if recheck_code and prov.code_version() != current.code_version:
        raise ArtifactUnreadable('execution permit 期间代码版本发生变化；请重新取 next')
    inputs = _external_inputs(current, node, exact_flags)
    if recheck_surface and read_surface_seal(
        current.base, current.sid, code=current.code_version,
    ) != current.surface_seal:
        raise ArtifactUnreadable(
            'execution permit 读取 flags/外部输入期间本书盘面变化；请重新取 next')

    payload = {
        'base': current.base,
        'sid': current.sid,
        'code_version': current.code_version,
        'surface_seal': current.surface_seal,
        'next_state': current.state,
        'waiting_on': (
            None if current.action is None else current.action.waiting_on),
        'kind': kind,
        'node': node,
        'flags': list(exact_flags),
        'paid': is_paid(node, exact_flags),
        'writes': write_effect(node, exact_flags),
        'operator_action_sha256': action_sha256,
        'external_inputs': [item.model_dump(mode='json') for item in inputs],
    }
    # 延迟导入避免本模块与 operator.next 的调用方向形成环。
    from novel.orchestrator.operator import canonical_sha256
    return ExecutionPermit(
        **payload, permit_sha256=canonical_sha256(payload))


def ready_execution_token_from_next(
    current: OperatorNextDecision,
) -> ExecutionPermit:
    """给 ``operator next`` 同快照铸 ready token，不再读取 active surface。

    调用者必须在本函数读取 exact external inputs 之后，对最初 snapshot 的
    ``surface_seal`` 做那一次规划期 CAS。code 直接绑定该 snapshot，不再运行昂贵的
    第二次仓库指纹；execute 会在 claim 后逐字重验 code 与 active surface。这样
    整个 ready 分发只有一次完整 snapshot、一次规划期 surface CAS。
    """
    if current.state != 'ready' or current.action is None:
        raise UnsupportedRunMode('只有 ready next 能在同快照内铸 execution token')
    return _permit_from_decision(
        current, current.action.node, current.action.flags,
        recheck_code=False, recheck_surface=False,
    )


def permit_from_next(
    current: OperatorNextDecision,
    node: str,
    flags: tuple[str, ...] | list[str] = (),
) -> ExecutionPermit:
    """显式铸 exact permit；主要用于 blocked recovery 与诊断兼容。"""
    return _permit_from_decision(
        current, node, flags, recheck_code=True, recheck_surface=True)


def verify_permit_sha256(permit: ExecutionPermit) -> None:
    """重算 canonical permit hash；环境 JSON 不能靠自报摘要取得执行权。"""
    payload = permit.model_dump(mode='json')
    claimed = payload.pop('permit_sha256')
    from novel.orchestrator.operator import canonical_sha256
    expected = canonical_sha256(payload)
    if claimed != expected:
        raise TerminalState(
            'execution permit envelope 的 canonical SHA-256 与载荷不一致')


def parse_execution_permit(
    raw: ExecutionPermit | str | bytes | bytearray | Mapping[str, Any],
) -> ExecutionPermit:
    """纯函数：解析完整 permit 并复算 canonical SHA-256。

    文件、环境 envelope 与 HTTP body 都走这一把尺子。此函数不读代码、不看
    盘面、不运行 operator planner；因此 preview 可以只渲染 token 冻结的步骤。
    """
    try:
        if isinstance(raw, ExecutionPermit):
            permit = raw
        elif isinstance(raw, Mapping):
            permit = ExecutionPermit.model_validate(dict(raw))
        elif isinstance(raw, (str, bytes, bytearray)):
            permit = ExecutionPermit.model_validate_json(raw)
        else:
            raise TypeError(f'不支持的 execution permit 载荷：{type(raw).__name__}')
    except (TypeError, ValueError, json.JSONDecodeError) as exc:
        raise TerminalState('execution permit JSON/shape 无效') from exc
    verify_permit_sha256(permit)
    return permit


def load_execution_permit_file(path: str | os.PathLike[str]) -> ExecutionPermit:
    """读取完整 token，或从原样 ``OperatorNext`` wrapper 提取 ready token。

    调用方只保存和转交整份 JSON，不需要搬运任何 SHA。两种形态最终都进入
    ``parse_execution_permit`` 这一把 canonical hash 尺子。
    """
    source = Path(path).resolve()
    try:
        raw = source.read_bytes()
    except OSError as exc:
        raise TerminalState(f'读不了 execution permit 文件：{source}: {exc!r}') from exc
    try:
        current = OperatorNext.model_validate_json(raw)
    except (UnicodeDecodeError, ValueError):
        current = None
    if current is not None:
        if current.execution_token is None:  # schema 已守；关闭 model_construct 绕过
            raise TerminalState('operator next wrapper 没有 ready execution token')
        return parse_execution_permit(current.execution_token)
    return parse_execution_permit(raw)


def _current_external_inputs(permit: ExecutionPermit) -> list[ExecutionPermitInput]:
    """按登记表重读 exact flags 指向的当前原始字节。"""
    exact_flags = tuple(permit.flags)
    out: list[ExecutionPermitInput] = []
    for flag in flag_tokens(exact_flags):
        if (permit.node, flag) not in EXTERNAL_INPUT_POLICIES:
            continue
        raw = flag_value(exact_flags, flag)
        if raw is None:
            raise TerminalState(f'{permit.node} {flag} 缺外部输入路径')
        target = _resolve_external_path_at(
            base=permit.base, sid=permit.sid, node=permit.node,
            flag=flag, raw=raw)
        if not target.is_file():
            raise ArtifactUnreadable(
                f'execution permit 的外部输入已缺失：{permit.node} {flag} {target}')
        try:
            before = target.stat()
            payload = target.read_bytes()
            after = target.stat()
        except OSError as exc:
            raise ArtifactUnreadable(
                f'execution permit 的外部输入不可读：{target}: {exc!r}') from exc
        coordinate = lambda stat: (
            stat.st_dev, stat.st_ino, stat.st_size, stat.st_mtime_ns)
        if coordinate(before) != coordinate(after) or len(payload) != after.st_size:
            raise ArtifactUnreadable(
                f'execution permit 的外部输入读取期间变化：{target}')
        out.append(ExecutionPermitInput(
            flag=flag, argument=raw, resolved_path=os.fspath(target),
            bytes=len(payload), sha256=hashlib.sha256(payload).hexdigest(),
        ))
    return out


def revalidate_active_execution_surface(
    permit: ExecutionPermit, *,
    base: str | os.PathLike[str] | None = None,
    sid: str | None = None,
    node: str | None = None,
    flags: tuple[str, ...] | list[str] | None = None,
) -> None:
    """claim 后、首个副作用前做一次轻量 CAS，不运行七层 planner。

    校验范围只有 token 自身、caller 坐标、当前代码、active surface 与登记
    external inputs。它刻意不调用 ``operator next`` / queue / check-only；旧的
    SHA-only runner 可以继续走 ``operator.permit`` 慢路，新 token runner 应调用本口。
    """
    permit = parse_execution_permit(permit)
    expected_base = Path(permit.base).resolve()
    if base is not None and Path(base).resolve() != expected_base:
        raise TerminalState('execution permit.base 与 runner --base 不一致')
    if sid is not None and sid != permit.sid:
        raise TerminalState('execution permit.sid 与 runner --sid 不一致')
    if node is not None and node != permit.node:
        raise TerminalState('execution permit.node 与 runner node 不一致')
    if flags is not None and list(flags) != permit.flags:
        raise TerminalState('execution permit.flags 与 runner exact flags 不一致')

    exact_flags = tuple(permit.flags)
    validate_run(permit.node, exact_flags)
    if permit.paid != is_paid(permit.node, exact_flags):
        raise TerminalState('execution permit 的 paid 判定与当前节点注册表不一致')
    if permit.writes != write_effect(permit.node, exact_flags):
        raise TerminalState('execution permit 的 writes 判定与当前节点注册表不一致')

    current_code = prov.code_version()
    if current_code != permit.code_version:
        raise ArtifactUnreadable(
            'execution permit 的 execution epoch 代码已变化；请打开新 epoch 复检')
    current_inputs = _current_external_inputs(permit)
    if current_inputs != permit.external_inputs:
        raise ArtifactUnreadable(
            'execution permit 的登记外部输入路径或原始字节已变化；请重新铸 token')
    if read_surface_seal(
        expected_base, permit.sid, code=permit.code_version,
    ) != permit.surface_seal:
        raise ArtifactUnreadable(
            'execution permit 的 active artifact/approval head 已变化；请重新铸 token')


def env_with_permit(
    permit: ExecutionPermit,
    step_index: int,
    env: Mapping[str, str] | None = None,
) -> dict[str, str]:
    """给一个 Commander child 注入完整 permit 与零基 Step 索引。"""
    if type(step_index) is not int or step_index < 0:
        raise ValueError('execution permit step_index 必须是非负整数')
    verify_permit_sha256(permit)
    child = dict(os.environ if env is None else env)
    child[PERMIT_JSON_ENV] = permit.model_dump_json()
    child[STEP_INDEX_ENV] = str(step_index)
    return child


def load_permit_envelope(
    env: Mapping[str, str] | None = None,
) -> tuple[ExecutionPermit, int] | None:
    """关闭式读取 child envelope；半份、坏 JSON、非 canonical index 均拒绝。"""
    source = os.environ if env is None else env
    raw_permit = source.get(PERMIT_JSON_ENV)
    raw_index = source.get(STEP_INDEX_ENV)
    if raw_permit is None and raw_index is None:
        return None
    if not raw_permit or raw_index is None:
        raise TerminalState(
            'execution permit envelope 必须同时携带 permit JSON 与 step_index')
    permit = parse_execution_permit(raw_permit)
    if not raw_index.isascii() or not raw_index.isdecimal():
        raise TerminalState('execution permit step_index 必须是 canonical 非负整数')
    step_index = int(raw_index)
    if str(step_index) != raw_index:
        raise TerminalState('execution permit step_index 不得带前导零')
    return permit, step_index


__all__ = [
    'EXTERNAL_INPUT_POLICIES', 'PERMIT_JSON_ENV', 'RECOVERY_RUN_POLICIES',
    'RECOVERY_RUN_POLICY_INDEX', 'STEP_INDEX_ENV', 'env_with_permit',
    'load_execution_permit_file', 'load_permit_envelope',
    'parse_execution_permit', 'permit_from_next',
    'ready_execution_token_from_next', 'revalidate_active_execution_surface',
    'verify_permit_sha256',
]
