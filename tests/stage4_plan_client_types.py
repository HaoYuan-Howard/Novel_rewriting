#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Stage4 plan 测试用 AgentDriver 协议（不依赖 novel.agents factory）。"""
from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass
from typing import Any


@dataclass(frozen=True)
class AgentRequest:
    system: str | None
    prompt: str


@dataclass(frozen=True)
class AgentUsage:
    prompt_tokens: int = 0
    completion_tokens: int = 0


@dataclass(frozen=True)
class AgentTurn:
    session_id: str
    content: str
    model: str | None
    actual_reasoning_effort: str | None
    usage: AgentUsage
    turn_count: int


@dataclass(frozen=True)
class AgentProbe:
    driver: str
    provider: str
    executable: str
    available: bool
    version: str


class AgentCommandError(RuntimeError):
    def __init__(
            self, message: str, *, returncode: int = 1,
            usage: AgentUsage | None = None, error_kind: str = 'unknown',
            status: int | None = None, retryable: bool = False,
    ) -> None:
        super().__init__(message)
        self.returncode = returncode
        self.usage = usage or AgentUsage()
        self.error_kind = error_kind
        self.status = status
        self.retryable = retryable


class AgentDriver(ABC):
    driver: str
    provider: str

    @abstractmethod
    def probe(self) -> AgentProbe: ...

    @abstractmethod
    def start(self, profile: Any, request: AgentRequest) -> AgentTurn: ...

    @abstractmethod
    def resume(self, profile: Any, session_id: str, request: AgentRequest) -> AgentTurn: ...

    @abstractmethod
    def cancel(self, session_id: str | None = None) -> bool: ...

    @abstractmethod
    def close(self) -> None: ...
