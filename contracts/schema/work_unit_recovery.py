"""Commander 传输失败的免费工程收口；不退款、不清成本、不替代语义审阅。"""
from __future__ import annotations

from typing import Any, Literal
from pydantic import Field, model_validator

from contracts.schema.base import FrozenModel
from contracts.schema.common import Sha256
from contracts.schema.run_metrics import UsageSummary
from contracts.schema.workflow import WorkUnitContentTurn


class CommanderFailureRecoveryRequest(FrozenModel):
    preview_token: Sha256
    actor: str = Field(min_length=1, max_length=200)
    reason: str = Field(min_length=1, max_length=2000)

    @model_validator(mode='after')
    def _text(self) -> CommanderFailureRecoveryRequest:
        if not self.actor.strip() or not self.reason.strip():
            raise ValueError('工程恢复必须有执行者与明确理由')
        return self


class CommanderFailureRecoveryPreview(FrozenModel):
    sid: str
    unit_id: Sha256
    turn_id: str
    available: bool
    reason: str
    preview_token: Sha256 | None = None
    before: WorkUnitContentTurn | None = None
    usage: UsageSummary | None = None


class CommanderFailureRecoveryReceipt(FrozenModel):
    kind: Literal['commander-failure-reconciliation-v1'] = 'commander-failure-reconciliation-v1'
    sid: str
    unit_id: Sha256
    before: WorkUnitContentTurn
    request: CommanderFailureRecoveryRequest
    evidence: dict[str, Any]

    @model_validator(mode='after')
    def _failed_request(self) -> CommanderFailureRecoveryReceipt:
        if (self.before.state != 'unknown'
                or self.before.reason_code != 'commander_transport_failed'
                or any((self.before.response_sha256, self.before.recovery_sha256,
                        self.before.producer_sha256, self.before.feedback_sha256))
                or not self.before.attempts
                or any(row.outcome != 'error' for row in self.before.attempts)):
            raise ValueError('只可收口没有回答且网络尝试全部结束的 Commander 传输失败')
        return self
