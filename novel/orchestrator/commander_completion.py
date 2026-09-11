#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Commander 顶层退出后的确定性完成度分类。

外层进程 ``exit 0`` 只说明 provider CLI 正常结束；execution admission 已经
drain 也只说明没有在途子执行。两者都不能回答“Commander 是否在一个仍可安全
继续的动作前主动停了”。这里在 fresh delivery 得到 ``stopped`` 后重读一次
``operator next``：人工/恢复 blocker 是合法停点；``ready`` 或“主链已完整但
delivery 仍 stopped”则是可机器证明的提前退出。

本模块不恢复 session、不启动第二个 Commander，也不写任何账。是否记入 ledger /
session audit 由 ``orchestrator.agent`` 在同一次 ended 收尾里决定。
"""
from __future__ import annotations

from dataclasses import dataclass

from contracts.schema.orchestration import AgentDeliveryOutcome
from novel.orchestrator.operator import next_action


EARLY_EXIT_ERROR_KIND = 'commander_early_exit'
NEXT_ACTION_UNAVAILABLE_ERROR_KIND = 'commander_next_action_unavailable'


@dataclass(frozen=True)
class CommanderCompletionReview:
    outcome: AgentDeliveryOutcome
    error_kind: str | None
    #: 只陈述确定性 ``operator next`` 是否仍有 ready action；是否精确续会由
    #: RunSupervisor 决定，本模块仍不启动模型。
    continue_ready: bool = False


def ready_action_remains(base: str, sid: str) -> bool:
    """只回答 canonical ``operator next`` 是否还有 ready action。"""
    current = next_action(base, sid)
    if current.state != 'ready':
        return False
    if current.action is None:
        raise RuntimeError('operator next 自报 ready 却没有 action')
    return True


def review_commander_stop(
    base: str,
    sid: str,
    outcome: AgentDeliveryOutcome,
    *,
    process_exit_code: int,
) -> CommanderCompletionReview:
    """分类一次正常退出后的 ``stopped``，但绝不自动增加模型 turn。"""
    if process_exit_code != 0 or outcome.state != 'stopped':
        return CommanderCompletionReview(
            outcome=outcome, error_kind=None, continue_ready=False)

    try:
        current = next_action(base, sid)
    except Exception as exc:  # noqa: BLE001 —— 无法分类不能猜成合法 blocker
        return CommanderCompletionReview(
            outcome=AgentDeliveryOutcome(
                state='unknown',
                checked_at=outcome.checked_at,
                reason=(
                    'fresh delivery 为 stopped，但结束检查无法读取确定性 '
                    'operator next；拒绝猜成合法人工停点：'
                    f'{type(exc).__name__}: {exc}。原终验：{outcome.reason}'
                ),
            ),
            error_kind=NEXT_ACTION_UNAVAILABLE_ERROR_KIND,
            continue_ready=False,
        )

    if current.state == 'blocked':
        # blocked 只证明当前没有可直接执行的 ready action；是否由受托审阅者继续
        # 裁决属于模型/人工工作，不能由零模型结束检查擅自重启一轮。
        return CommanderCompletionReview(
            outcome=outcome, error_kind=None, continue_ready=False)

    if current.state == 'ready':
        action = current.action
        if action is None:  # 严格 schema 已守；防 model_construct/测试假对象绕过
            return CommanderCompletionReview(
                outcome=AgentDeliveryOutcome(
                    state='unknown', checked_at=outcome.checked_at,
                    reason=(
                        'operator next 自报 ready 却没有 action；无法证明该停点安全。'
                        f'原终验：{outcome.reason}'
                    ),
                ),
                error_kind=NEXT_ACTION_UNAVAILABLE_ERROR_KIND,
                continue_ready=False,
            )
        flags = ' '.join(action.flags)
        command = f'{action.node} {flags}'.rstrip()
        reason = (
            'Commander 顶层进程以 exit 0 结束，但 execution drain 后的 '
            f'operator next 仍有 ready 动作 `{command}` '
            f'(action_sha256={action.action_sha256})；'
            '该事实允许 Supervisor 对同一 provider session 做精确续会。'
            f'原终验：{outcome.reason}'
        )
    else:
        # pipeline_complete 只代表 node 链没有下一项；delivery 仍 stopped 说明
        # 当前成书、审批或交付阻塞条件仍有未闭合项。
        reason = (
            'Commander 顶层进程以 exit 0 结束；operator next 已是 '
            'pipeline_complete，但 fresh delivery 仍为 stopped，说明成书/审批证据'
            '尚未闭合；pipeline 已无 ready action，Supervisor 不猜测新动作。'
            f'原终验：{outcome.reason}'
        )
    return CommanderCompletionReview(
        outcome=outcome.model_copy(update={'reason': reason}),
        error_kind=EARLY_EXIT_ERROR_KIND,
        continue_ready=current.state == 'ready',
    )


__all__ = [
    'EARLY_EXIT_ERROR_KIND', 'NEXT_ACTION_UNAVAILABLE_ERROR_KIND',
    'CommanderCompletionReview', 'ready_action_remains',
    'review_commander_stop',
]
