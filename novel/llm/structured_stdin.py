#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""结构化 agent stdin 渲染：checkpoint / journal 身份哈希的唯一字节源。

与 CLI driver 包分离；生产 journal 与恢复守卫只依赖本模块，不经 novel.agents。
"""
from __future__ import annotations

from typing import Literal

from novel.errors import AgentConfigurationError

PLAIN_DELIVERY = 'plain'
CLAUDE_STRUCTURED_DELIVERY_V1 = 'claude-structured-output/v1'
AgentDeliveryProtocol = Literal[
    'plain',
    'claude-structured-output/v1',
]

CLAUDE_STRUCTURED_DELIVERY_FOOTER_V1 = """\
--- 结构化交付协议 v1（claude-structured-output/v1）---
本轮已提供 JSON Schema。最终答案必须且只能通过 CLI 提供的结构化输出工具提交一次：
工具参数本身就是符合 schema 的完整响应对象；不要先在普通回答中打印 JSON，不要套
`response`、`result` 或 `structured_output` 包装键，也不要在结构化提交之后重复输出。
除 CLI 内建的 `StructuredOutput` 外，不得调用任何其他工具。"""

def agent_delivery_protocol(*, driver: str,
                            has_output_schema: bool) -> AgentDeliveryProtocol:
    """把 driver/schema 组合映射到唯一 stdin 交付协议。"""
    if driver in ('claude-code', 'claude-code-glm') and has_output_schema:
        return CLAUDE_STRUCTURED_DELIVERY_V1
    return PLAIN_DELIVERY


def render_agent_stdin(
    system: str | None,
    prompt: str,
    *,
    delivery_protocol: AgentDeliveryProtocol = PLAIN_DELIVERY,
) -> str:
    """渲染真正送进 CLI stdin 的唯一字节源（编码前）。"""
    if system is None:
        rendered = prompt
    else:
        rendered = f'{system}\n\n--- 本轮任务 ---\n\n{prompt}'
    if delivery_protocol == PLAIN_DELIVERY:
        return rendered
    if delivery_protocol == CLAUDE_STRUCTURED_DELIVERY_V1:
        return f'{rendered}\n\n{CLAUDE_STRUCTURED_DELIVERY_FOOTER_V1}'
    raise AgentConfigurationError(
        f'未知 agent delivery_protocol={delivery_protocol!r}')


def render_agent_stdin_bytes(
    system: str | None,
    prompt: str,
    *,
    delivery_protocol: AgentDeliveryProtocol = PLAIN_DELIVERY,
) -> bytes:
    """把最终 stdin 渲染为生产子进程真正接收的 UTF-8 字节。

    这是送达、prompt SHA、request fingerprint 与 checkpoint 的共同
    落点；生产 runner 不得再把它放回文本模式，否则 Windows 会把
    LF 静默转为 CRLF，导致账上哈希与 provider 所见字节分家。
    """
    return render_agent_stdin(
        system, prompt, delivery_protocol=delivery_protocol).encode('utf-8')


def render_structured_agent_stdin(
        system: str, prompt: str, *, driver: str) -> str:
    """checkpoint/恢复守卫复算一次结构化请求的真实首轮 stdin。"""
    return render_agent_stdin(
        system,
        prompt,
        delivery_protocol=agent_delivery_protocol(
            driver=driver, has_output_schema=True),
    )


def render_structured_agent_stdin_bytes(
        system: str, prompt: str, *, driver: str) -> bytes:
    """checkpoint/恢复守卫复算结构化请求的真实 UTF-8 stdin。"""
    return render_agent_stdin_bytes(
        system,
        prompt,
        delivery_protocol=agent_delivery_protocol(
            driver=driver, has_output_schema=True),
    )


__all__ = [
    'PLAIN_DELIVERY', 'CLAUDE_STRUCTURED_DELIVERY_V1',
    'CLAUDE_STRUCTURED_DELIVERY_FOOTER_V1', 'AgentDeliveryProtocol',
    'agent_delivery_protocol', 'render_agent_stdin', 'render_agent_stdin_bytes',
    'render_structured_agent_stdin', 'render_structured_agent_stdin_bytes',
]
