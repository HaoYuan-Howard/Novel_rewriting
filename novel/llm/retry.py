#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""单次内容请求的网络重试决策；业务预算、落盘和会话由调用层负责。"""
from __future__ import annotations

import math
from dataclasses import dataclass
from typing import Final

from contracts.invariants import WORK_UNIT_MAX_DELIVERY_ATTEMPTS

RESOURCE_INTERRUPTED: Final = 'insufficient_system_resource'


@dataclass(frozen=True)
class TransportRetryPolicy:
    """生产请求与独立供应商探针共用的有界退避，不授予内容调用权限。"""

    max_retries: int
    backoff_base: float

    def __post_init__(self) -> None:
        if (isinstance(self.max_retries, bool) or not isinstance(self.max_retries, int)
                or not 0 <= self.max_retries < WORK_UNIT_MAX_DELIVERY_ATTEMPTS):
            raise ValueError('网络重试参数必须在 0～3 之间，包含首次最多 4 次尝试')
        if (isinstance(self.backoff_base, bool)
                or not isinstance(self.backoff_base, (int, float))
                or not math.isfinite(self.backoff_base) or self.backoff_base < 0):
            raise ValueError('网络退避基数必须是有限的非负秒数')

    @property
    def max_attempts(self) -> int:
        return self.max_retries + 1

    def delay(self, *, attempt: int, retryable: bool) -> float | None:
        """attempt 包含首发且从 1 开始；None 明确表示不再重试，0 仍是一次重试。"""
        if (isinstance(attempt, bool) or not isinstance(attempt, int)
                or not 1 <= attempt <= self.max_attempts):
            raise ValueError('网络尝试序号超出当前请求上限')
        if not retryable or attempt == self.max_attempts:
            return None
        return self.backoff_base * (2 ** (attempt - 1))
