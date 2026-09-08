"""新工作流的运行账；它不是小说产物，也不替代成本或审批证据。"""
from __future__ import annotations

from typing import Any, Literal

from pydantic import Field, model_validator

from contracts.schema.base import FrozenModel
from contracts.schema.common import Sha256


class WorkflowManifestV1(FrozenModel):
    schema_version: Literal[1] = 1
    contract_version: Literal['workflow-role-boundaries-v1'] = 'workflow-role-boundaries-v1'
    task_id: Sha256
    created_at: int


class WorkUnitAttempt(FrozenModel):
    attempt_id: str = Field(min_length=1)
    outcome: Literal['started', 'ok', 'error', 'failed', 'unknown', 'not_started', 'interrupted']
    provider_session_id: str | None = None
    request_fingerprint: Sha256 | None = None


class WorkUnitContentTurn(FrozenModel):
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
    attempts: tuple[WorkUnitAttempt, ...] = ()
    created_at: int


class WorkUnitLedgerV1(FrozenModel):
    schema_version: Literal[1] = 1
    task_id: Sha256
    unit_id: Sha256
    sid: str = Field(min_length=1)
    node: str = Field(min_length=1)
    role: Literal['executor', 'commander']
    scope: str = Field(min_length=1, max_length=160)
    content_limit: Literal[4] = 4
    turns: tuple[WorkUnitContentTurn, ...] = ()
    committed_through: int = Field(default=0, ge=0, le=4)
    committed_artifact: str | None = None
    committed_fingerprint: str | None = None
    # 整组候选可能全部来自缓存；提交证明不能为此伪造内容轮次。
    group_producer_sha256: Sha256 | None = None
    group_producer_completed: bool = False

    @model_validator(mode='after')
    def _closed_turns(self) -> WorkUnitLedgerV1:
        if len(self.turns) > self.content_limit:
            raise ValueError('工作单元内容预算超过上限')
        if self.committed_through > len(self.turns):
            raise ValueError('正式提交进度不能越过已登记的内容请求')
        if self.committed_through and not (
                self.committed_artifact and self.committed_fingerprint):
            raise ValueError('正式提交进度必须绑定实际产物及其身份')
        if self.group_producer_completed and self.group_producer_sha256 is None:
            raise ValueError('整组完成状态必须绑定原提交证明')
        if [t.ordinal for t in self.turns] != list(range(1, len(self.turns) + 1)):
            raise ValueError('内容轮次必须连续且从一开始')
        if len({t.turn_id for t in self.turns}) != len(self.turns):
            raise ValueError('内容请求身份重复')
        attempts = [a.attempt_id for t in self.turns for a in t.attempts]
        if len(set(attempts)) != len(attempts):
            raise ValueError('网络尝试不能属于多个内容轮')
        for turn in self.turns:
            if turn.state in ('delivered', 'adopted') and not turn.response_sha256:
                raise ValueError('已交付状态必须有完整响应证据')
        return self


class WorkUnitBlobV1(FrozenModel):
    schema_version: Literal[1] = 1
    unit_id: Sha256
    turn_id: str
    payload: dict[str, Any]


class WorkUnitGroupBlobV1(FrozenModel):
    schema_version: Literal[1] = 1
    unit_id: Sha256
    payload: dict[str, Any]


class WorkUnitRegistryV1(FrozenModel):
    schema_version: Literal[1] = 1
    task_id: Sha256
    sid: str = Field(min_length=1)
    unit_ids: tuple[Sha256, ...] = ()
