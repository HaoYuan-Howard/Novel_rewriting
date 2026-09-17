"""新工作流的运行账；它不是小说产物，也不替代成本或审批证据。"""
from __future__ import annotations

from typing import Any, Literal

from pydantic import Field, model_validator

from contracts.schema.base import FrozenModel
from contracts.schema.common import Sha256


class PipelineArtifactVersionsV4(FrozenModel):
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
    artifact_versions: PipelineArtifactVersionsV4 = Field(default_factory=PipelineArtifactVersionsV4)
    task_id: Sha256
    created_at: int


class PipelineArtifactVersionsV5(FrozenModel):
    """Gate B 冻结快照；不能随新 producer 漂移。"""
    characters: Literal[2] = 2
    global_grounding: Literal[7] = 7
    analyze_receipt: Literal[1] = 1
    chapter_blueprints: Literal[10] = 10
    knowledge_links: Literal[3] = 3
    blind_candidate: Literal[5] = 5
    blind_chapter_edit: Literal[3] = 3
    evidence_binding: Literal[5] = 5
    plan_chapters: Literal[6] = 6
    plan_edit: Literal[6] = 6
    writer_context: Literal[9] = 9


class WorkflowTransitionRefV1(FrozenModel):
    transition_id: Sha256
    source_manifest_sha256: Sha256


class WorkflowManifestV5(FrozenModel):
    schema_version: Literal[5] = 5
    contract_version: Literal['stage2-actor-v1'] = 'stage2-actor-v1'
    pipeline_contract_epoch: Literal['joint-analysis-actor-presentations-v1'] = 'joint-analysis-actor-presentations-v1'
    artifact_versions: PipelineArtifactVersionsV5 = Field(default_factory=PipelineArtifactVersionsV5)
    task_id: Sha256
    created_at: int
    transition_ref: WorkflowTransitionRefV1 | None = None


class PipelineArtifactVersions(PipelineArtifactVersionsV5):
    """Gate C 当前快照；准备、文案和改名分别保有自己的来源身份。"""
    stage6_prepare: Literal[1] = 1
    stage6_prepare_edit: Literal[1] = 1
    stage6_prepare_review: Literal[1] = 1
    stage6_prepare_decision: Literal[1] = 1
    hook: Literal[4] = 4
    hook_semantic_review: Literal[3] = 3
    name_map: Literal[3] = 3


class WorkflowManifestV6(FrozenModel):
    schema_version: Literal[6] = 6
    contract_version: Literal['stage6-prepare-v1'] = 'stage6-prepare-v1'
    pipeline_contract_epoch: Literal['independent-hook-preparation-v1'] = 'independent-hook-preparation-v1'
    artifact_versions: PipelineArtifactVersions = Field(default_factory=PipelineArtifactVersions)
    task_id: Sha256
    created_at: int
    transition_ref: WorkflowTransitionRefV1 | None = None


class PipelineArtifactVersionsV7(FrozenModel):
    """Gate D 当前快照：Stage2 语义审阅与 KnowledgeLinks 退役后的产物面。

    ``knowledge_links`` 不再是成员；Stage4 A/B/Context 与绑定闭包随知识消费
    退役各升一版。人物、Global 与 Blueprint 形状未变，不额外升版。
    旧 manifest 的 knowledge_links 记录只随 V4/V5/V6 类解码，不迁移。
    """

    characters: Literal[2] = 2
    global_grounding: Literal[7] = 7
    analyze_receipt: Literal[1] = 1
    chapter_blueprints: Literal[10] = 10
    blind_candidate: Literal[6] = 6
    blind_chapter_edit: Literal[3] = 3
    evidence_binding: Literal[6] = 6
    plan_chapters: Literal[7] = 7
    plan_edit: Literal[7] = 7
    writer_context: Literal[10] = 10
    stage6_prepare: Literal[1] = 1
    stage6_prepare_edit: Literal[1] = 1
    stage6_prepare_review: Literal[1] = 1
    stage6_prepare_decision: Literal[1] = 1
    hook: Literal[4] = 4
    hook_semantic_review: Literal[3] = 3
    name_map: Literal[3] = 3


class WorkflowManifestV7(FrozenModel):
    schema_version: Literal[7] = 7
    contract_version: Literal['stage2-mechanical-admission-v1'] = 'stage2-mechanical-admission-v1'
    pipeline_contract_epoch: Literal['stage2-mechanical-admission-v1'] = 'stage2-mechanical-admission-v1'
    artifact_versions: PipelineArtifactVersionsV7 = Field(default_factory=PipelineArtifactVersionsV7)
    task_id: Sha256
    created_at: int
    transition_ref: WorkflowTransitionRefV1 | None = None


class PipelineArtifactVersionsV8(FrozenModel):
    """STR-03-S4：删除 plan_chapters；Design/Grounding 升版。"""

    characters: Literal[2] = 2
    global_grounding: Literal[7] = 7
    analyze_receipt: Literal[1] = 1
    chapter_blueprints: Literal[10] = 10
    blind_candidate: Literal[7] = 7
    blind_chapter_edit: Literal[3] = 3
    evidence_binding: Literal[7] = 7
    plan_edit: Literal[7] = 7
    writer_context: Literal[10] = 10
    stage6_prepare: Literal[1] = 1
    stage6_prepare_edit: Literal[1] = 1
    stage6_prepare_review: Literal[1] = 1
    stage6_prepare_decision: Literal[1] = 1
    hook: Literal[4] = 4
    hook_semantic_review: Literal[3] = 3
    name_map: Literal[3] = 3


class WorkflowManifestV8(FrozenModel):
    schema_version: Literal[8] = 8
    contract_version: Literal['stage4-presentation-ir-v1'] = 'stage4-presentation-ir-v1'
    pipeline_contract_epoch: Literal['stage4-presentation-ir-v1'] = 'stage4-presentation-ir-v1'
    artifact_versions: PipelineArtifactVersionsV8 = Field(default_factory=PipelineArtifactVersionsV8)
    task_id: Sha256
    created_at: int
    transition_ref: WorkflowTransitionRefV1 | None = None


class WorkflowTransitionBookProofV1(FrozenModel):
    characters_sha256: Sha256
    global_sha256: Sha256
    review_sha256: Sha256


class WorkflowTransitionProofV1(FrozenModel):
    """来源代码的只读核验结果；不把旧 gate bool 当作审批采用权。"""
    logs: dict[str, Sha256 | None]
    owner_ids: tuple[str, ...]
    counts: dict[str, int]
    accepted_books: dict[str, WorkflowTransitionBookProofV1]


class WorkflowTransitionBookProofV2(WorkflowTransitionBookProofV1):
    closure_sha256: Sha256


class WorkflowTransitionProofV2(WorkflowTransitionProofV1):
    """V5 来源额外证明 Stage5 闭合；旧 Core proof 不获得此资格。"""
    accepted_books: dict[str, WorkflowTransitionBookProofV2]


class WorkflowTransitionProjectionV1(FrozenModel):
    """唯一允许免费重编译的派生投影；Core、来源和批准记录不迁移。"""
    relative_path: str
    source_json: str | None
    source_sha256: Sha256 | None
    target_json: str
    target_sha256: Sha256
    characters_sha256: Sha256
    main_text_sha256: Sha256

    @model_validator(mode='after')
    def _source_presence(self) -> WorkflowTransitionProjectionV1:
        if (self.source_json is None) != (self.source_sha256 is None):
            raise ValueError('投影原文件的存在性与原字节摘要必须一致')
        return self


class WorkflowTransitionPreparedV1(FrozenModel):
    schema_version: Literal[1] = 1
    transition_id: Sha256
    source_manifest_json: str
    source_manifest_sha256: Sha256
    target_manifest_json: str
    target_manifest_sha256: Sha256
    source_code_directory: str
    source_code_version: str = Field(pattern=r'^[0-9a-f]{40}$')
    target_code_version: str = Field(pattern=r'^[0-9a-f]{40}$')
    preserved_files: dict[str, Sha256]
    projections: dict[str, WorkflowTransitionProjectionV1]
    source_proof: WorkflowTransitionProofV1
    service_ports: tuple[int, ...] = Field(min_length=1)
    stopped_pids: tuple[int, ...]
    actor: str = Field(min_length=1)
    reason: str = Field(min_length=1)
    prepared_at: int = Field(ge=0)

    @model_validator(mode='after')
    def _offline_controls(self) -> WorkflowTransitionPreparedV1:
        if (not self.actor.strip() or not self.reason.strip()
                or len(set(self.service_ports)) != len(self.service_ports)
                or any(not 1 <= port <= 65535 for port in self.service_ports)
                or len(set(self.stopped_pids)) != len(self.stopped_pids)
                or any(pid <= 0 for pid in self.stopped_pids)):
            raise ValueError('迁移须提供合法唯一端口/PID，以及明确 actor/reason')
        return self


class WorkflowTransitionPreparedV2(WorkflowTransitionPreparedV1):
    """V5→V6 不重编译任何已验收产物，保留 V1 的独立历史恢复。"""
    schema_version: Literal[2] = 2
    source_proof: WorkflowTransitionProofV2

    @model_validator(mode='after')
    def _no_projection_rewrite(self) -> WorkflowTransitionPreparedV2:
        if self.projections:
            raise ValueError('V5→V6 不允许改写原人物投影或其他产物')
        return self


class WorkflowTransitionPendingV1(FrozenModel):
    schema_version: Literal[1] = 1
    transition_id: Sha256
    prepared_sha256: Sha256


class WorkflowTransitionCompletedV1(FrozenModel):
    schema_version: Literal[1] = 1
    transition_id: Sha256
    prepared_sha256: Sha256
    source_manifest_sha256: Sha256
    target_manifest_sha256: Sha256
    completed_at: int = Field(ge=0)


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


class BookBudgetGrantV1(FrozenModel):
    """整书付费总闸的显式人工追加；自动恢复路径不得调用。"""

    actor: str = Field(min_length=1, max_length=120)
    reason: str = Field(min_length=1, max_length=2000)
    additional: int = Field(ge=1, le=64, strict=True)
    created_at: int = Field(ge=0)
    before_used: int = Field(ge=0)

    @model_validator(mode='after')
    def _reason_is_real(self) -> 'BookBudgetGrantV1':
        if not self.reason.strip() or not self.actor.strip():
            raise ValueError('整书预算追加必须有 actor 与理由')
        return self


class BookContentBudgetV1(FrozenModel):
    """整书级付费内容调用总闸的持久账（跨全部工作单元合账）。

    ``used`` 只在 ``WorkUnitBudget.reserve`` 成功时 +1：网络层重试共享同一
    reservation、免费恢复与 prepared 补尾不计数（重试层级不变式，契约 §七）。
    账随书目录持久，重启/恢复/重建 job 不重置；坏账关闭式拒绝新调用。
    """

    schema_version: Literal[1] = 1
    task_id: Sha256
    sid: str = Field(min_length=1)
    ceiling: int = Field(ge=1)
    used: int = Field(default=0, ge=0)
    grants: tuple[BookBudgetGrantV1, ...] = ()

    @property
    def remaining(self) -> int:
        return self.ceiling + sum(g.additional for g in self.grants) - self.used
