"""冻结基线工作单元账的只读统计模型。

形状来源：24e4cf42c76309adaf1f4ee4df0dca3cdbea4d37 的
contracts/schema/workflow.py。仅供 workflow_metrics 归属首轮、返修和网络尝试；
不登记现役 artifact，不迁移文件，不向 producer、恢复或额度授权提供输入。
未来历史格式须另行核实，不能用忽略字段或修改版本号将未知账洗成已知。
"""
from __future__ import annotations

from typing import Literal

from pydantic import Field, model_validator

from contracts.schema.base import FrozenModel
from contracts.schema.common import Sha256


class HistoricalWorkUnitAttempt(FrozenModel):
    attempt_id: str = Field(min_length=1)
    outcome: Literal['started', 'ok', 'error', 'failed', 'unknown', 'not_started', 'interrupted']
    provider_session_id: str | None = None
    request_fingerprint: Sha256 | None = None


class HistoricalWorkUnitTurn(FrozenModel):
    turn_id: str = Field(min_length=1)
    ordinal: int = Field(ge=1, le=4)
    request_sha256: Sha256
    request_payload_sha256: Sha256 | None = None
    run_id: str = Field(min_length=1)
    session_run_id: str = ''
    state: Literal['reserved', 'delivered', 'failed', 'unknown', 'adopted'] = 'reserved'
    reason_code: str = ''
    response_sha256: Sha256 | None = None
    recovery_sha256: Sha256 | None = None
    producer_sha256: Sha256 | None = None
    feedback_sha256: Sha256 | None = None
    attempts: tuple[HistoricalWorkUnitAttempt, ...] = ()
    created_at: int


class HistoricalWorkUnitLedgerV1(FrozenModel):
    schema_version: Literal[1] = 1
    task_id: Sha256
    unit_id: Sha256
    sid: str = Field(min_length=1)
    node: str = Field(min_length=1)
    role: Literal['executor', 'commander']
    scope: str = Field(min_length=1, max_length=160)
    content_limit: Literal[4] = 4
    turns: tuple[HistoricalWorkUnitTurn, ...] = ()
    committed_through: int = Field(default=0, ge=0, le=4)
    committed_artifact: str | None = None
    committed_fingerprint: str | None = None
    group_producer_sha256: Sha256 | None = None
    group_producer_completed: bool = False

    @model_validator(mode='after')
    def _closed_turns(self) -> HistoricalWorkUnitLedgerV1:
        if len(self.turns) > self.content_limit:
            raise ValueError('历史账超过冻结额度')
        if self.committed_through > len(self.turns):
            raise ValueError('历史提交进度越过登记内容请求')
        if self.committed_through and not (
                self.committed_artifact and self.committed_fingerprint):
            raise ValueError('历史提交缺少产物及身份')
        if self.group_producer_completed and self.group_producer_sha256 is None:
            raise ValueError('历史整组完成缺少原提交证明')
        if [turn.ordinal for turn in self.turns] != list(range(1, len(self.turns) + 1)):
            raise ValueError('历史内容轮次不连续')
        if len({turn.turn_id for turn in self.turns}) != len(self.turns):
            raise ValueError('历史内容请求身份重复')
        attempts = [attempt.attempt_id for turn in self.turns for attempt in turn.attempts]
        if len(set(attempts)) != len(attempts):
            raise ValueError('历史网络尝试跨内容轮重复')
        for turn in self.turns:
            if turn.state in ('delivered', 'adopted') and not turn.response_sha256:
                raise ValueError('历史已交付状态缺少响应证据')
        return self
