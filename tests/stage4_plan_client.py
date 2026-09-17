#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Stage4 plan 测试用 LlmClient 适配层（C3 直连路径）。"""
from __future__ import annotations

import json
from abc import ABC, abstractmethod
from dataclasses import dataclass
from types import SimpleNamespace
from typing import Any, Callable

from novel.llm.cache import ResponseCache
from novel.llm.client import LlmClient
from novel.llm.stage4_runs import plan_chapter_purpose
from novel.llm.transport import ChatRequest, ChatResponse, FakeTransport, TransportError
from novel.run import RunContext

from tests.stage4_plan_client_types import (
    AgentCommandError, AgentDriver, AgentProbe, AgentRequest, AgentTurn, AgentUsage,
)

MODEL = 'test-stage4-plan-model'


def _chapter_from_user(user: str) -> int:
    pack = json.loads(user)
    candidate = pack.get('candidate') or {}
    ordinal = candidate.get('output_ordinal')
    if ordinal is None:
        raise ValueError(f'binding user 缺 output_ordinal：{user[:120]!r}')
    return int(ordinal)


def _response_from_driver(driver: AgentDriver, req: ChatRequest) -> ChatResponse:
    agent_req = AgentRequest(system=req.system, prompt=req.user)
    try:
        turn = driver.start(None, agent_req)
    except AgentCommandError as exc:
        raise TransportError(
            str(exc), retryable=exc.retryable,
            status=exc.status, kind='rate_limit' if exc.status == 429 else 'transport',
        ) from exc
    return ChatResponse(
        content=turn.content,
        finish_reason='stop',
        usage={
            'prompt_tokens': turn.usage.prompt_tokens,
            'completion_tokens': turn.usage.completion_tokens,
        },
        model=turn.model or MODEL,
    )


class PlanTestPool(LlmClient):
    """LlmClient：用 AgentDriver 工厂驱动直连 plan 路径。"""

    plan_chapter_pool = True

    def __init__(
            self,
            ctx: RunContext,
            *,
            driver_factory: Callable[[str, int], AgentDriver] | None = None,
            max_turns: int = 4,
            concurrency: int = 1,
    ) -> None:
        self.max_turns = max_turns
        self.concurrency = concurrency
        self._driver_factory = driver_factory
        self._drivers: dict[int, AgentDriver] = {}
        self._clients: dict[int, AgentDriver] = {}
        self.turns_used = 0
        self.provider_session_id = None
        self.driver_failed = False
        self.requests: list[SimpleNamespace] = []

        def responder(req: ChatRequest, _index: int) -> ChatResponse:
            if self._driver_factory is None:
                raise AssertionError('机械书不应发起 wire')
            chapter = _chapter_from_user(req.user)
            driver = self._drivers.get(chapter)
            if driver is None:
                driver = self._driver_factory(plan_chapter_purpose(chapter), chapter)
                self._drivers[chapter] = driver
                self._clients[chapter] = driver
            self.turns_used += 1
            self.requests.append(SimpleNamespace(
                system=req.system, prompt=req.user, user=req.user))
            return _response_from_driver(driver, req)

        transport = FakeTransport(responder=responder)
        super().__init__(
            ctx,
            transport,
            cache=ResponseCache(),
            model=MODEL,
            enable_model_fallback=False,
            max_retries=0,
            concurrency=concurrency,
            warmup=False,
            budget_checker=lambda *_args, **_kwargs: None,
        )

    def finish(
            self, *, process_state: str = 'ok', delivery_state: str = 'delivered',
            error_kind: str | None = None,
    ) -> None:
        del process_state, delivery_state, error_kind

    def close(self) -> None:
        for driver in self._drivers.values():
            driver.close()


Stage4PlanSessionPool = PlanTestPool
