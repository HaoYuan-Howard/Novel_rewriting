#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""operator execution permit：一次 next 铸 ready token，blocked 恢复显式铸造。

``operator next`` 在同一冻结 snapshot 内为 ready 原样动作铸造 opaque execution
token；blocked action 只描述 blocker，恢复命令的 exact node + 全部 flags 仍须显式
交到本模块。ready 只接受 next 原样动作；blocked 只接受本文件策略表登记的恢复组合。
许可还绑定代码、读面、贵贱、写盘效应，以及已登记外部输入文件的原始字节摘要。

本模块不写服务账、不启动进程、不发请求。付费恢复只调用现役审批层的只读授权口，
并且必须已经出现在 ``PROFILED_LLM_RUNS``；未完成 executor 迁移的 paid flag
即使 CLI 认识，也在这里关闭式拒绝。
"""
from __future__ import annotations

from novel import runtime_layout
import hashlib
import json
import os
from collections.abc import Mapping
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Final, Literal

from contracts.artifacts import (
    STAGE2_GLOBAL_EDIT, STAGE5_REVIEWER_REWRITE_REQUEST,
)
from contracts.invariants import NODES
from contracts.schema.api import (
    ExecutionPermit, ExecutionPermitInput, OperatorNext, OperatorNextDecision,
)
from novel import paths, prov
from novel.errors import (
    ArtifactUnreadable, TerminalState, UnsupportedRunMode, UpstreamMissing,
)
from novel.orchestrator import approvals, queue
from novel.orchestrator.inspection import read_surface_seal
from novel.orchestrator.nodes import (
    PROFILED_LLM_RUNS, REPO_ROOT, flag_tokens, flag_value,
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
_STAGE2_EDIT_COMMIT = queue.WAITING_ON_STAGE2_EDIT_COMMIT
_STAGE2_EDIT_REVALIDATE = queue.WAITING_ON_STAGE2_EDIT_REVALIDATE
_INVALID_REVALIDATE = queue.WAITING_ON_INVALID_REVALIDATE
# Stage4 四道人工闸 blocker 已退役（2026-09-15 全自动裁决）；只剩
# repair/replan prepared 尾窗的免费恢复面仍登记专属 waiter。
_STAGE4_DESIGN_REPAIR_COMMIT = queue.WAITING_ON_STAGE4_DESIGN_REPAIR_COMMIT
_STAGE5_REVIEW = queue.WAITING_ON_REVIEW
_STAGE5_CLOSURE = queue.WAITING_ON_STAGE5_CLOSURE
_STAGE5_REVIEWER_REWRITE = queue.WAITING_ON_STAGE5_REVIEWER_REWRITE
_PREPARED_DELIVERY = queue.WAITING_ON_PREPARED_DELIVERY

_W0 = frozenset({_INBOX, _RESUME})
# stage2 恢复入口只认统一机械准入 blockers；节点写权仍按知识生产节点登记。
_W2 = frozenset({_INBOX, _RESUME, _INVALID_REVALIDATE})
_W4 = frozenset({_INBOX, _RESUME, _INVALID_REVALIDATE})
_W5 = frozenset({_INBOX, _RESUME, _STAGE5_REVIEW, _STAGE5_CLOSURE, _INVALID_REVALIDATE})
_W7 = frozenset({_INBOX, _RESUME, _INVALID_REVALIDATE})


#: 首批恢复闭集。这里登记的是**全部 flag token**，不是只登记主操作；
#: 例如 revalidate 没有 actor/reason 就不是同一行。表外组合即使节点 argparse
#: 暂时能解析，也不能拿到 Commander 执行许可。
RECOVERY_RUN_POLICIES: Final[tuple[RecoveryRunPolicy, ...]] = (
    RecoveryRunPolicy('stage0.remove', _t('--check-only'), _W0),
    RecoveryRunPolicy(
        'stage1.partition', _t('--check-only'),
        frozenset({_INBOX, _RESUME}),
    ),

    RecoveryRunPolicy('stage2.global', _t('--renormalize'), _W2),
    RecoveryRunPolicy('stage2.global', _t('--recover-analyze'), _W2),
    RecoveryRunPolicy('stage2.analyze', _t('--recover-analyze-run'), _W2),
    RecoveryRunPolicy(
        'stage2.global', _t('--revalidate', '--actor', '--reason'),
        _W2 | frozenset({_STAGE2_EDIT_REVALIDATE})),
    # reviewed-edit 只保留 WAL 补尾这一条恢复路；审阅轮的 apply-edit/
    # review-decisions 等语义动作已随 Stage2 审阅退役。
    RecoveryRunPolicy(
        'stage2.global', _t('--apply-edit'),
        frozenset({_STAGE2_EDIT_COMMIT})),
    RecoveryRunPolicy('stage2.blueprint', _t('--check-only'), _W2),
    RecoveryRunPolicy('stage2.blueprint', _t('--renormalize'), _W2),
    RecoveryRunPolicy(
        'stage2.blueprint', _t('--revalidate', '--actor', '--reason'), _W2),
    RecoveryRunPolicy(
        'stage2.blueprint', _t('--emit-edit-template'), _W2 | _W4),
    RecoveryRunPolicy(
        'stage2.blueprint', _t('--validate-edit'), _W2 | _W4),
    RecoveryRunPolicy(
        'stage2.blueprint', _t('--apply-edit'), _W2 | _W4),

    RecoveryRunPolicy(
        'stage4.design',
        _t('--recover-repair-aggregate', '--source-journal-sha256',
           '--source-commit-sha256'),
        frozenset({_STAGE4_DESIGN_REPAIR_COMMIT}),
    ),
    RecoveryRunPolicy('stage4.plan', _t('--reaggregate'), _W4),
    RecoveryRunPolicy('stage4.design', _t('--reaggregate'), _W4),
    RecoveryRunPolicy('stage4.design', _t('--recover-responses'), _W4),
    RecoveryRunPolicy(
        'stage4.plan', _t('--recover-prepared', '--subject-sha256'),
        frozenset({_PREPARED_DELIVERY})),
    RecoveryRunPolicy(
        'stage4.design', _t('--recover-prepared', '--subject-sha256'),
        frozenset({_PREPARED_DELIVERY})),
    RecoveryRunPolicy('stage4.plan', _t('--check-only'), _W4),
    RecoveryRunPolicy('stage4.plan', _t('--names-only'), _W4),
    RecoveryRunPolicy('stage4.plan', _t('--identity'), _W4),
    RecoveryRunPolicy(
        'stage4.plan', _t('--revalidate', '--actor', '--reason'), _W4),
    RecoveryRunPolicy('stage4.plan', _t('--apply-edit'), _W4),
    RecoveryRunPolicy('stage4.plan', _t('--apply-binding'), _W4),

    RecoveryRunPolicy('stage5.write', _t('--recheck'), _W5),
    RecoveryRunPolicy('stage5.write', _t('--recheck', '--chapters'), _W5),
    RecoveryRunPolicy(
        'stage5.write', _t('--revalidate', '--actor', '--reason'), _W5),
    RecoveryRunPolicy(
        'stage5.write',
        _t('--revalidate', '--chapters', '--actor', '--reason'), _W5),
    RecoveryRunPolicy('stage5.write', _t('--preview-edit'), _W5),
    RecoveryRunPolicy(
        'stage5.write',
        _t('--apply-edit', '--actor', '--reason',
           '--current-chapter-sha256', '--preview-subject-sha256',
           '--edit-confirmation'),
        _W5,
    ),
    RecoveryRunPolicy(
        'stage5.write', _t('--emit-prepared-recovery-workpack'), _W5),
    RecoveryRunPolicy(
        'stage5.write',
        _t('--recover-prepared', '--actor', '--reason', '--subject-sha256'),
        _W5,
    ),
    RecoveryRunPolicy('stage5.review', _t(), _W5),
    RecoveryRunPolicy('stage5.review', _t('--check-only'), _W5),
    RecoveryRunPolicy('stage5.review', _t('--wontfix'), _W5),
    # ⚠ `--retain-pretrim(-batch)` / `--request-reviewer-rewrite` 恢复策略已随
    #   批次 B3 trim 三选一退役删除；stage5.review 无参重算即机械闭合补算路，
    #   它在 _STAGE5_CLOSURE blocker 下同样被本表放行（_W5 含闭合闸）。
    RecoveryRunPolicy(
        'stage5.write',
        _t('--recover-reviewer-rewrite-prepared',
           '--reviewer-rewrite-request-sha256'),
        frozenset({_STAGE5_REVIEWER_REWRITE})),
    RecoveryRunPolicy(
        'stage5.write',
        _t('--recover-reviewer-rewrite-prepared',
           '--reviewer-rewrite-request-sha256',
           '--reviewer-rewrite-recovery-target-run-id',
           '--reviewer-rewrite-recovery-journal-sha256'),
        frozenset({_STAGE5_REVIEWER_REWRITE})),
    RecoveryRunPolicy(
        'stage5.write',
        _t('--retire-reviewer-rewrite-request',
           '--reviewer-rewrite-request-sha256'),
        frozenset({_STAGE5_REVIEWER_REWRITE})),

    RecoveryRunPolicy(
        'stage7.rename', _t('--recover-prepared', '--subject-sha256'),
        frozenset({_PREPARED_DELIVERY})),

    RecoveryRunPolicy('stage7.rename', _t('--check-only'), _W7),
    RecoveryRunPolicy('stage7.rename', _t('--identity'), _W7),
    RecoveryRunPolicy('stage7.rename', _t('--recheck'), _W7),
    RecoveryRunPolicy(
        'stage7.rename', _t('--revalidate', '--actor', '--reason'), _W7),
    RecoveryRunPolicy('stage7.rename', _t('--apply-edit'), _W7),
    RecoveryRunPolicy('stage7.rename', _t('--emit-edit-template'), _W7),
    RecoveryRunPolicy('stage7.rename', _t('--validate-edit'), _W7),
    RecoveryRunPolicy('stage7.rename', _t('--adopt-residual'), _W7),
    RecoveryRunPolicy('stage7.rename', _t('--rebind-review'), _W7),

    # 付费恢复只开放 executor 已完整迁移且另有 typed 授权的两族；
    # stage4.design 的人工 typed 恢复动作已随四道闸退役。
    RecoveryRunPolicy('stage5.write', _t('--revise'), _W5, paid=True),
    RecoveryRunPolicy(
        'stage5.write', _t('--revise', '--chapters'), _W5, paid=True),
    RecoveryRunPolicy(
        'stage5.write', _t('--reviewer-rewrite'),
        frozenset({_STAGE5_REVIEWER_REWRITE}), paid=True),
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


ExternalPathScope = Literal[
    'repo', 'book', 'book_or_service_edit', 'stage4_repair_journal']

#: 会把**文件字节**送进 producer 的 valued flag。actor/reason/hash/run_id 等纯值
#: 不在这里；漏登记一个文件 flag 会让许可只绑定路径字符串而没绑定真正输入。
EXTERNAL_INPUT_POLICIES: Final[
    dict[tuple[str, str], ExternalPathScope]
] = {
    ('stage2.global', '--apply-edit'): 'book_or_service_edit',
    ('stage2.blueprint', '--validate-edit'): 'book_or_service_edit',
    ('stage2.blueprint', '--apply-edit'): 'book_or_service_edit',
    # Stage4A producer 用 ``paths.edit_rel`` + ArtifactStore 读 typed request，
    # 因此现役真实路径只能在书目录内。未接 HTTP upload 前不能
    # 在 permit 层谎称 `_service/edits` 可达，否则 token 会放行一条
    # producer 必然拒绝的伪恢复路。
    ('stage4.design', '--recover-repair-aggregate'): 'stage4_repair_journal',
    ('stage4.plan', '--apply-edit'): 'book',
    ('stage4.plan', '--apply-binding'): 'book',
    ('stage5.write', '--preview-edit'): 'book_or_service_edit',
    ('stage5.write', '--apply-edit'): 'book_or_service_edit',
    # （stage5.review 的 trim manifest / reviewer-input 两条外部输入随批次 B3
    #   trim 三选一退役删除。）
    ('stage5.write', '--reviewer-rewrite'): 'book',
    ('stage7.rename', '--apply-edit'): 'book',
    ('stage7.rename', '--validate-edit'): 'book',
}


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
    if scope == 'stage4_repair_journal':
        from novel.llm import stage4_runs
        from novel.run_identity import check_run_id
        run_id = check_run_id(raw)
        candidates = tuple(
            root / run_id / 'design'
            / stage4_runs.DESIGN_REPAIR_AGGREGATE_COMMIT_FILE
            for root in runtime_layout.stage_run_roots(novel_dir, 'stage4'))
        return next(
            (target.resolve() for target in candidates if target.is_file()),
            candidates[0].resolve(),
        )
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


def _is_stage2_edit_commit_replay(
    *, waiting_on: str | None, node: str, flags: tuple[str, ...],
) -> bool:
    """唯一不把 canonical edit 当外部字节输入的 WAL 恢复动作。

    首次 service edit 可能在 input WAL 已落稳、book canonical 尚未生成时硬杀。
    此时 action/surface seal 已绑定 typed WAL，而 ``global_edit.json`` 正是恢复动作
    要补出的 target，不能反过来要求它在签 permit 前存在。predicate 同时用于签发
    与 claim 后复验，避免两侧对同一输入面的理解漂移。
    """
    return (
        waiting_on == _STAGE2_EDIT_COMMIT
        and node == 'stage2.global'
        and flags == ('--apply-edit', STAGE2_GLOBAL_EDIT)
    )


def _external_inputs(
    current: OperatorNextDecision, node: str, flags: tuple[str, ...],
) -> list[ExecutionPermitInput]:
    waiting_on = None if current.action is None else current.action.waiting_on
    if _is_stage2_edit_commit_replay(
            waiting_on=waiting_on, node=node, flags=flags):
        return []
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
        journal: Mapping[str, Any] | None = None
        if node == 'stage4.design' and flag == '--recover-repair-aggregate':
            # repair aggregate 是 Stage4 内部 journal，不是 artifact；permit 层
            # 只绑 raw bytes，不在这里复制它的版本/字段/self-hash 契约。
            # 先走 journal owner 的现役 envelope 读路，随后再读同一路径
            # 铸 raw SHA；两次之间若有漂移，只有返回的 exact raw bytes
            # 同时通过下面的 SHA 锁才能签发，claim 后还会再读一次。
            from novel.llm import stage4_runs
            journal = stage4_runs.validate_design_repair_aggregate_journal_envelope(
                target, run_id=raw, sid=current.sid)
        try:
            payload = target.read_bytes()
        except OSError as exc:
            raise ArtifactUnreadable(
                f'读不了 {node} {flag} 外部输入 {target}: {exc!r}') from exc
        digest = hashlib.sha256(payload).hexdigest()
        if journal is not None:
            expected_raw = flag_value(flags, '--source-journal-sha256')
            expected_commit = flag_value(flags, '--source-commit-sha256')
            if (digest != expected_raw
                    or journal.get('commit_sha256') != expected_commit):
                raise ArtifactUnreadable(
                    'Stage4 repair recovery 的 journal raw/commit SHA 与 flags 不等')
        out.append(ExecutionPermitInput(
            flag=flag, argument=raw, resolved_path=os.fspath(target),
            bytes=len(payload), sha256=digest,
        ))
    if (
        node == 'stage5.write'
        and bool({
            '--recover-reviewer-rewrite-prepared',
            '--retire-reviewer-rewrite-request',
        } & set(flag_tokens(flags)))
    ):
        # recovery 的 argv 携 source run 与 request self-hash，真正被恢复器
        # 消费的 canonical request 原始字节仍须进入 permit CAS。
        target = paths.artifact_path(
            paths.resolve_novel_dir(current.base, current.sid),
            STAGE5_REVIEWER_REWRITE_REQUEST).resolve()
        if not target.is_file():
            raise UpstreamMissing(
                f'Stage5 Reviewer rewrite canonical request 不在盘上：{target}')
        try:
            payload = target.read_bytes()
        except OSError as exc:
            raise ArtifactUnreadable(
                f'读不了 Stage5 Reviewer rewrite canonical request：{exc!r}') from exc
        out.append(ExecutionPermitInput(
            flag='--reviewer-rewrite-request-sha256',
            argument=flag_value(
                flags, '--reviewer-rewrite-request-sha256') or '',
            resolved_path=os.fspath(target),
            bytes=len(payload),
            sha256=hashlib.sha256(payload).hexdigest(),
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
    if waiting_on == queue.WAITING_ON_STAGE6_PREPARE_COMMIT:
        # Stage6 prepare 接受闸已随 Stage6 整层退役（F8b）：该等待面只会
        # 出现在旧书历史账里，新链不再发出，也没有可恢复的 producer。
        raise UnsupportedRunMode(
            'stage6.prepare WAL 已随 Stage6 整层退役，不再提供恢复许可')
    if (waiting_on == _STAGE2_EDIT_COMMIT
            and (node != 'stage2.global'
                 or flags != ('--apply-edit', STAGE2_GLOBAL_EDIT))):
        raise UnsupportedRunMode(
            'reviewed-edit prepared commit 只允许 exact canonical '
            f'`stage2.global --apply-edit {STAGE2_GLOBAL_EDIT}`')
    if (waiting_on == _STAGE2_EDIT_REVALIDATE
            and (node != 'stage2.global'
                 or flags != tuple(current.action.flags))):
        raise UnsupportedRunMode(
            'Stage2 revalidate 补尾只允许逐字重放 operator 冻结的 '
            'exact actor/reason')
    if (waiting_on == _STAGE4_DESIGN_REPAIR_COMMIT
            and (node != 'stage4.design'
                 or flags != tuple(current.action.flags))):
        raise UnsupportedRunMode(
            'Stage4 repair aggregate 补尾只允许逐字重放 operator 冻结的 '
            'source run/journal raw/commit SHA')
    if waiting_on == _PREPARED_DELIVERY:
        if node != current.action.node or flags != tuple(current.action.flags):
            raise UnsupportedRunMode('交付完成组只能执行 operator 冻结的原 run/proof SHA')
        recovery = queue.prepared_delivery_recovery_item(
            current.base, current.sid, code=current.code_version)
        if (recovery is None or recovery.waiting_on != _PREPARED_DELIVERY
                or recovery.node != node or tuple(recovery.flags) != flags):
            raise UnsupportedRunMode('Stage6/7 原完成组证明已变化；拒绝签发旧恢复动作')
    if waiting_on == _STAGE5_REVIEWER_REWRITE:
        if node != 'stage5.write' or flags != tuple(current.action.flags):
            raise UnsupportedRunMode(
                'Stage5 Reviewer rewrite 只允许逐字执行 operator 按 current '
                'execution journal 冻结的 paid/recovery argv')
        from novel.stages.stage5 import reviewer_rewrite
        from novel.orchestrator import state

        novel_dir = paths.resolve_novel_dir(current.base, current.sid)
        ctx = state.readonly_ctx(
            novel_dir, 'stage5.write', current.code_version)
        execution = reviewer_rewrite.reviewer_rewrite_execution_state(ctx)
        if execution is None:
            raise UpstreamMissing('Stage5 Reviewer rewrite canonical request 已缺失')
        if execution.state == 'ready_paid':
            expected: tuple[str, ...] | None = (
                '--reviewer-rewrite', execution.request_rel)
        elif execution.state == 'recoverable_checkpoint':
            expected = (
                '--recover-reviewer-rewrite-prepared',
                execution.source_run_id or '',
                '--reviewer-rewrite-request-sha256',
                execution.request_sha256,
            )
        elif execution.state == 'recoverable_prepared':
            if (execution.recovery_target_run_id is None
                    or execution.recovery_journal_sha256 is None):
                raise ArtifactUnreadable(
                    'Stage5 Reviewer rewrite prepared recovery 缺 target WAL identity')
            expected = (
                '--recover-reviewer-rewrite-prepared',
                execution.source_run_id or '',
                '--reviewer-rewrite-request-sha256',
                execution.request_sha256,
                '--reviewer-rewrite-recovery-target-run-id',
                execution.recovery_target_run_id,
                '--reviewer-rewrite-recovery-journal-sha256',
                execution.recovery_journal_sha256,
            )
        elif execution.state == 'expired_unstarted':
            expected = (
                '--retire-reviewer-rewrite-request',
                execution.request_rel,
                '--reviewer-rewrite-request-sha256',
                execution.request_sha256,
            )
        else:
            expected = None
        if expected is None or flags != expected:
            raise UnsupportedRunMode(
                'Stage5 Reviewer rewrite execution state 已变化；'
                '不得沿用旧 paid/recovery argv')

    # 恢复不能越过 blocker 去动更晚层。显式例外是产品链上的真实恢复：
    # Stage5 Review currentness 由后一个 review 节点重算。
    rank = {name: index for index, name in enumerate(NODES)}
    later_exception = (
        waiting_on == _STAGE5_REVIEW and node == 'stage5.review'
    )
    if not later_exception and rank[node] > rank[current.action.node]:
        raise UnsupportedRunMode(
            f'{node} 位于当前 blocker {current.action.node} 之后；恢复不得越闸写下游')

    paid = is_paid(node, flags)
    if paid != row.paid:
        raise UnsupportedRunMode(
            f'execution recovery policy 贵贱漂移：登记 paid={row.paid}，当前判定={paid}')
    if paid:
        profiled = PROFILED_LLM_RUNS.get(node, ())
        if row.tokens not in profiled:
            raise UnsupportedRunMode(
                f'{node} {sorted(row.tokens)} 尚未完成 executor profile 迁移，'
                '付费恢复关闭式拒绝')
        _require_paid_recovery_authorized(current, node, flags)
    return row


def _require_paid_recovery_authorized(
    current: OperatorNextDecision, node: str, flags: tuple[str, ...],
) -> None:
    tokens = set(flag_tokens(flags))
    if node == 'stage5.write' and '--revise' in tokens:
        chapters = queue.chapters_from_flags(flags)
        approvals.authorize_writing(
            current.base, current.sid, chapters, requires_fresh_review=True)
        # ``--chapters`` 是过滤器，不是让调用者任意点章的授权。可付费自救章集
        # 必须由 producer 的四道筛子（actionable / UNIQUE / 未达轮次上限 /
        # current Review）现算；permit 不复制判据，也不把“计划内”误当“可 revise”。
        from novel.orchestrator import state
        from novel.stages.stage5 import write
        from novel.store import ArtifactStore
        novel_dir = paths.resolve_novel_dir(current.base, current.sid)
        store = ArtifactStore(state.readonly_ctx(
            novel_dir, 'stage5.write', current.code_version))
        picked, _skipped, _review_fingerprint = write.revise_targets(store)
        available = set(picked)
        if not available:
            raise UnsupportedRunMode(
                '当前 fresh Stage5 Review 没有 actionable/UNIQUE/未达轮次上限的'
                '可付费自救章；不得签发空 revise permit')
        if chapters is not None:
            requested = set(chapters)
            outside = sorted(requested - available)
            if not requested or outside:
                raise UnsupportedRunMode(
                    f'--revise --chapters 必须是当前 picked 章的非空子集；'
                    f'可选={sorted(available)}，越界={outside}')
        return
    if node == 'stage5.write' and '--reviewer-rewrite' in tokens:
        request_rel = flag_value(flags, '--reviewer-rewrite')
        if request_rel != STAGE5_REVIEWER_REWRITE_REQUEST:
            raise UnsupportedRunMode(
                'Stage5 Reviewer rewrite paid 入口只接受 current canonical request')
        from novel.orchestrator import state
        from novel.stages.stage5 import reviewer_rewrite

        novel_dir = paths.resolve_novel_dir(current.base, current.sid)
        ctx = state.readonly_ctx(
            novel_dir, 'stage5.write', current.code_version)
        probe = reviewer_rewrite.current_reviewer_rewrite_request(
            ctx, request_rel)
        if probe is None or probe.execution.state != 'ready_paid':
            raise UnsupportedRunMode(
                'Stage5 Reviewer rewrite request 当前不是可付费首发态')
        approvals.authorize_writing(
            current.base, current.sid,
            list(reviewer_rewrite.ready_reviewer_rewrite_targets(ctx, request_rel)),
            reviewer_rewrite_request_rel=request_rel)
        return
    raise UnsupportedRunMode(
        f'{node} {sorted(tokens)} 不是已登记的 typed paid recovery')


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
    if _is_stage2_edit_commit_replay(
            waiting_on=permit.waiting_on,
            node=permit.node,
            flags=exact_flags):
        return []
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
    if (
        permit.node == 'stage5.write'
        and bool({
            '--recover-reviewer-rewrite-prepared',
            '--retire-reviewer-rewrite-request',
        } & set(flag_tokens(exact_flags)))
    ):
        target = paths.artifact_path(
            paths.resolve_novel_dir(permit.base, permit.sid),
            STAGE5_REVIEWER_REWRITE_REQUEST).resolve()
        if not target.is_file():
            raise ArtifactUnreadable(
                f'execution permit 的 Reviewer rewrite request 已缺失：{target}')
        try:
            before = target.stat()
            payload = target.read_bytes()
            after = target.stat()
        except OSError as exc:
            raise ArtifactUnreadable(
                f'execution permit 的 Reviewer rewrite request 不可读：{exc!r}') from exc
        coordinate = lambda stat: (
            stat.st_dev, stat.st_ino, stat.st_size, stat.st_mtime_ns)
        if coordinate(before) != coordinate(after) or len(payload) != after.st_size:
            raise ArtifactUnreadable(
                'execution permit 的 Reviewer rewrite request 读取期间变化')
        out.append(ExecutionPermitInput(
            flag='--reviewer-rewrite-request-sha256',
            argument=flag_value(
                exact_flags, '--reviewer-rewrite-request-sha256') or '',
            resolved_path=os.fspath(target),
            bytes=len(payload),
            sha256=hashlib.sha256(payload).hexdigest(),
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
