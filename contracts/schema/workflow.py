"""新工作流的运行账；它不是小说产物，也不替代成本或审批证据。"""
from __future__ import annotations

from typing import Any, Literal

from pydantic import Field, model_validator

from contracts.schema.base import FrozenModel
from contracts.schema.common import Sha256


class PipelineArtifactVersions(FrozenModel):
    """初始化时的版本快照；各 Gate 的新产物仍由现役 ArtifactSpec 严格验证。"""
    characters: Literal[2] = 2
    global_grounding: Literal[7] = 7
    analyze_receipt: Literal[1] = 1
    chapter_blueprints: Literal[9] = 9
    knowledge_links: Literal[2] = 2
    blind_candidate: Literal[4] = 4
    blind_chapter_edit: Literal[2] = 2
    evidence_binding: Literal[5] = 5
    plan_chapters: Literal[5] = 5
    plan_edit: Literal[5] = 5
    writer_context: Literal[8] = 8


class WorkflowManifestV4(FrozenModel):
    schema_version: Literal[4] = 4
    contract_version: Literal['stage2-core-v2'] = 'stage2-core-v2'
    pipeline_contract_epoch: Literal['joint-analysis-independent-artifacts-v1'] = 'joint-analysis-independent-artifacts-v1'
    artifact_versions: PipelineArtifactVersions = Field(default_factory=PipelineArtifactVersions)
    task_id: Sha256
    created_at: int


class WorkUnitAttempt(FrozenModel):
    attempt_id: str = Field(min_length=1)
    outcome: Literal['started', 'ok', 'error', 'failed', 'unknown', 'not_started', 'interrupted']
    provider_session_id: str | None = None
    request_fingerprint: Sha256 | None = None


class WorkUnitContentTurn(FrozenModel):
    turn_id: str = Field(min_length=1)
    ordinal: int = Field(ge=1)
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


class BudgetGrant(FrozenModel):
    """同一业务单元的不可变人工追加；不含付费或正文外发授权。"""

    intent_id: str = Field(pattern=r'^[a-zA-Z0-9_-]{16,80}$')
    preview_token: Sha256
    additional: int = Field(default=1, ge=1, le=4, strict=True)
    actor: Literal['human:frontend'] = 'human:frontend'
    reason: str = Field(min_length=1, max_length=2000)
    created_at: int = Field(ge=0)
    before_ledger_sha256: Sha256
    surface_sha256: Sha256
    authorized_after_ordinal: int = Field(ge=4)
    owner_run_ids: tuple[str, ...] = Field(min_length=1)

    @model_validator(mode='after')
    def _reason_and_owners(self) -> BudgetGrant:
        if not self.reason.strip() or any(not value for value in self.owner_run_ids):
            raise ValueError('人工追加必须有理由和精确 owner')
        if tuple(sorted(set(self.owner_run_ids))) != self.owner_run_ids:
            raise ValueError('追加授权的 owner 必须排序且唯一')
        return self


class WorkUnitProcessing(FrozenModel):
    """一次实际处理的来源；免费处理只记证据，绝不伪造内容轮次。"""

    processing_id: Sha256
    run_id: str = Field(min_length=1)
    input_sha256: Sha256
    kind: Literal['pending', 'model', 'mechanical', 'history_adopted', 'cached']
    turn_id: str | None = None
    evidence_sha256: Sha256 | None = None

    @model_validator(mode='after')
    def _evidence(self) -> WorkUnitProcessing:
        if self.kind == 'model':
            if not self.turn_id or self.evidence_sha256 is not None:
                raise ValueError('模型处理必须引用内容轮次')
        elif self.turn_id is not None or self.evidence_sha256 is None:
            raise ValueError('免费处理必须有独立来源证据，不能冒充模型轮次')
        return self


class WorkUnitLedgerV2(FrozenModel):
    schema_version: Literal[2] = 2
    task_id: Sha256
    unit_id: Sha256
    sid: str = Field(min_length=1)
    node: str = Field(min_length=1)
    role: Literal['executor', 'commander']
    scope: str = Field(min_length=1, max_length=160)
    base_content_limit: Literal[4] = 4
    grants: tuple[BudgetGrant, ...] = ()
    turns: tuple[WorkUnitContentTurn, ...] = ()
    processing: tuple[WorkUnitProcessing, ...] = ()
    committed_through: int = Field(default=0, ge=0)
    committed_artifact: str | None = None
    committed_fingerprint: str | None = None
    # 整组候选可能全部来自缓存；提交证明不能为此伪造内容轮次。
    group_producer_sha256: Sha256 | None = None
    group_producer_completed: bool = False

    @model_validator(mode='after')
    def _closed_turns(self) -> WorkUnitLedgerV2:
        limit = self.base_content_limit
        intents: set[str] = set()
        for grant in self.grants:
            if grant.intent_id in intents or grant.authorized_after_ordinal != limit:
                raise ValueError('追加授权必须唯一，且只能在已有额度耗尽后累计')
            if grant.authorized_after_ordinal > len(self.turns):
                raise ValueError('人工追加不得越过实际已用轮次')
            intents.add(grant.intent_id)
            limit += grant.additional
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
        if len({row.processing_id for row in self.processing}) != len(self.processing):
            raise ValueError('处理来源回执身份重复')
        turns = {turn.turn_id: turn for turn in self.turns}
        for row in self.processing:
            if row.kind == 'model':
                turn = turns.get(row.turn_id)
                if (turn is None or turn.run_id != row.run_id
                        or turn.request_sha256 != row.input_sha256):
                    raise ValueError('处理来源回执不属于所声明的内容轮次')
        attempts = [a.attempt_id for t in self.turns for a in t.attempts]
        if len(set(attempts)) != len(attempts):
            raise ValueError('网络尝试不能属于多个内容轮')
        for turn in self.turns:
            if turn.state in ('delivered', 'adopted') and not turn.response_sha256:
                raise ValueError('已交付状态必须有完整响应证据')
        return self

    @property
    def content_limit(self) -> int:
        return self.base_content_limit + sum(grant.additional for grant in self.grants)


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
