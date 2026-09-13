#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""issues 协议提交的 HTTP→CLI 桥：服务只暂存 ``_service`` 请求。

两套账事务会写正式产物（global_review、审阅事务账）并补审批接受；按跨模块
边界，这些写口只属于 CLI 子进程。HTTP 侧把公开请求（``review_token、actor、
issues``）暂存到 ``_service/edits/<sid>``，再同步执行一次
``stage2.global --review-issues``，并把事务账的最新可追溯摘要回给界面。
"""
from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path
from typing import Any

from contracts.schema.stage2_issue_review import (
    Stage2IssueReviewTransactionLedger,
)
from novel import paths, prov
from novel.errors import TerminalState
from novel.store import ArtifactStore

REPO_ROOT = Path(__file__).resolve().parents[2]

#: exit 0 = 全部安全并已接受；exit 1 = 已落两套账但仍有未闭合问题。
_LANDED_EXIT_CODES = (0, 1)


def _readonly_store(base: str | os.PathLike[str], sid: str) -> tuple[Any, ArtifactStore]:
    from novel.orchestrator import state

    novel_dir = paths.resolve_novel_dir(base, sid)
    ctx = state.readonly_ctx(novel_dir, 'stage2.global', prov.code_version())
    return ctx, ArtifactStore(ctx)


def apply_issue_submission(base: str | os.PathLike[str], sid: str,
                           submission_path: str | os.PathLike[str]) -> dict[str, Any]:
    """同步跑一次 issues 审阅事务；返回 exit 码与最新事务摘要。"""
    argv = [
        sys.executable, '-B', '-m', 'novel.cli', 'run', 'stage2.global',
        paths.check_sid(sid), '--base', str(Path(base).resolve()),
        '--review-issues', str(Path(submission_path).resolve()),
    ]
    completed = subprocess.run(
        argv, cwd=str(REPO_ROOT), stdin=subprocess.DEVNULL,
        stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True,
        encoding='utf-8', errors='replace', check=False, shell=False, timeout=600)
    if completed.returncode not in _LANDED_EXIT_CODES:
        detail = completed.stderr.strip().splitlines()[-1:] or ['无 stderr']
        raise TerminalState(
            f'issues 审阅事务失败（exit {completed.returncode}）：{detail[0]}')
    _ctx, store = _readonly_store(base, sid)
    return {
        'exit_code': completed.returncode,
        'node_accepted': completed.returncode == 0,
        'transaction': _latest_transaction(store),
    }


def latest_scope_review_summary(base: str | os.PathLike[str], sid: str) -> dict[str, Any] | None:
    """审阅事务账的最新一行；没有 issues 协议历史时保持 None。"""
    _ctx, store = _readonly_store(base, sid)
    return _latest_transaction(store)


def _latest_transaction(store) -> dict[str, Any] | None:
    from pydantic import ValidationError
    from novel.stages.stage2 import core_review as core_review_io
    try:
        ledger = core_review_io._read_transaction_ledger(store.ctx)
    except (ValidationError, OSError):
        return None
    if not ledger.transactions:
        return None
    transaction = ledger.transactions[-1]
    return {
        'protocol': transaction.protocol,
        'decision_basis': transaction.decision_basis,
        'coverage_scope_id': transaction.coverage_scope_id,
        'coverage_total': len(transaction.coverage),
        'derived_pass_count': sum(
            1 for entry in transaction.coverage if entry.basis == 'derived_pass'),
        'issue_count': len(transaction.issues),
        'node_accepted': transaction.node_accepted,
        'decided_at': transaction.decided_at,
        'transaction_sha256': transaction.transaction_sha256,
    }
