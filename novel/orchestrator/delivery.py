#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Agent 进程结束后的交付终验。

这不是另一个流水线节点，也不读取 agent 的自然语言日志。它只从当前注册产物、
Stage2 typed 决策、待办视图、终审包和可下载成书重新得出结论。
"""
from __future__ import annotations

import time
from pathlib import Path

from contracts.schema.orchestration import AgentDeliveryOutcome
from novel import paths, prov
from novel.errors import ArtifactUnreadable, TerminalState
from novel.orchestrator.state import readonly_ctx
from novel.stages.stage2 import global_review
from novel.stages.stage7 import assemble
from novel.views.inbox import inbox_view


def _outcome(state: str, reason: str) -> AgentDeliveryOutcome:
    return AgentDeliveryOutcome(state=state, reason=reason, checked_at=int(time.time()))


def assess(base: str | Path, sid: str, *, start_code_version: str | None,
           ) -> AgentDeliveryOutcome:
    """只读终验；任何无法证明的部分倒向 stopped/unknown，绝不倒成 ready。"""
    if start_code_version is None:
        return _outcome('unknown', 'started 事件没有代码指纹，无法证明本次跑使用同一版代码')
    current_code = prov.code_version()
    if current_code != start_code_version:
        return _outcome(
            'stopped',
            f'运行期间代码指纹变化：start={start_code_version}，end={current_code}')

    try:
        novel_dir = paths.resolve_novel_dir(base, sid)
        s2ctx = readonly_ctx(novel_dir, 'stage2.global', current_code)
        current, approved, counts = global_review.approval_status(s2ctx)
        if not approved:
            return _outcome(
                'stopped',
                f'Stage2 复核闸未打开：current={current}，counts={counts}')

        book_ctx = readonly_ctx(novel_dir, 'stage7.assemble', current_code)
        assemble.read_current_book(book_ctx)
        review_path = paths.review_package_path(novel_dir)
        if not review_path.is_file():
            return _outcome('stopped', f'终审包不存在：{review_path}')

        inbox = inbox_view(base, sid)
        blocking = [item for item in inbox.items if item.delivery_effect == 'blocking']
        if blocking:
            sample = '；'.join(f'{item.stage}/{item.kind}/{item.rule}'
                              for item in blocking[:5])
            return _outcome(
                'stopped', f'仍有 {len(blocking)} 条交付阻塞待办：{sample}')
        if inbox.items:
            return _outcome(
                'ready_with_disclosures',
                f'成书与强闸均通过；仍保留 {len(inbox.items)} 条非阻塞人工披露')
        return _outcome('ready', 'Stage2 全 PASS、成书当前可下载、终审包存在且无待办余额')
    except (ArtifactUnreadable, TerminalState, OSError, ValueError) as exc:
        return _outcome('stopped', f'交付条件未满足：{type(exc).__name__}: {exc}')
    except Exception as exc:  # noqa: BLE001 —— 终验自己失败不能冒充产品失败或成功
        return _outcome('unknown', f'交付终验无法完成：{type(exc).__name__}: {exc}')
