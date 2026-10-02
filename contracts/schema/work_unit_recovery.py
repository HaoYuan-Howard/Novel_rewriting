"""工作单元传输失败的免费工程收口；不退款、不清成本、不替代语义审阅。

Commander 收口只服务存量书；2026-10-02（R10c）增补 executor 变体——现役直连
executor 轮的确定性 wire 拒收（不可重试 ``TransportError``，如 4xx 拒付）经同一
入口收口。拒收种类闭集登记在本模块（`EXECUTOR_WIRE_REFUSAL_KINDS`），
orchestrator 只消费不另定义（I-11）。
"""
from __future__ import annotations

from typing import Any, Literal, Union
from pydantic import Field, model_validator

from contracts.schema.base import FrozenModel
from contracts.schema.common import Sha256
from contracts.schema.run_metrics import UsageSummary
from contracts.schema.workflow import WorkUnitContentTurn

#: executor 收口可接受的拒收种类闭集（`novel/llm/transport.py::_error_kind` 对
#: 不可重试 4xx 的确定性拒收）。read_timeout／未知异常不在此列：那类结果真未知，
#: 保持 unknown 交恢复入口对账。
EXECUTOR_WIRE_REFUSAL_KINDS: frozenset[str] = frozenset({'http_error'})


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


class ExecutorWireRefusalReceipt(FrozenModel):
    """executor 轮确定性 wire 拒收的收口证明（R10c 2026-10-02）。

    与 Commander 收口同构：绑定收口前轮次、工程请求与证据；不退款、不生成
    语义决定、不清除未知用量。差别只在收口前提——被服务端明确拒收（种类在
    `EXECUTOR_WIRE_REFUSAL_KINDS`），attempt 按旧 wire 记 unknown、新 wire 记
    failed，两者都无回答。
    """
    kind: Literal['executor-wire-refusal-reconciliation-v1'] = (
        'executor-wire-refusal-reconciliation-v1')
    sid: str
    unit_id: Sha256
    before: WorkUnitContentTurn
    request: CommanderFailureRecoveryRequest
    evidence: dict[str, Any]

    @model_validator(mode='after')
    def _refused_request(self) -> ExecutorWireRefusalReceipt:
        if (self.before.state != 'unknown'
                or self.before.reason_code not in EXECUTOR_WIRE_REFUSAL_KINDS
                or any((self.before.response_sha256, self.before.recovery_sha256,
                        self.before.producer_sha256, self.before.feedback_sha256))
                or not self.before.attempts
                or any(row.outcome not in ('unknown', 'failed')
                       for row in self.before.attempts)):
            raise ValueError('只可收口没有回答且拒收证据在闭集的 executor wire 拒收')
        return self


#: 两种收口证明的联合；消费方按 ledger.role 与 payload kind 各自选择。
WorkUnitFailureReceipt = Union[CommanderFailureRecoveryReceipt, ExecutorWireRefusalReceipt]
