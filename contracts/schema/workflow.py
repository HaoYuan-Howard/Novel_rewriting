"""新工作流的运行账；它不是小说产物，也不替代成本或审批证据。"""
from __future__ import annotations

from collections.abc import Callable, Mapping
from typing import Any, Literal

from pydantic import Field, model_validator

from contracts.invariants import OWNER_REVISION_MAX_ROUNDS
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


class PipelineArtifactVersionsV9(FrozenModel):
    """STR-03-S2-BP：ChapterBlueprints v11 无损结构压缩。"""

    characters: Literal[3] = 3
    global_grounding: Literal[7] = 7
    analyze_receipt: Literal[1] = 1
    chapter_blueprints: Literal[11] = 11
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


class WorkflowManifestV9(FrozenModel):
    schema_version: Literal[9] = 9
    contract_version: Literal['stage4-presentation-ir-v1'] = 'stage4-presentation-ir-v1'
    pipeline_contract_epoch: Literal['stage4-presentation-ir-v1'] = 'stage4-presentation-ir-v1'
    artifact_versions: PipelineArtifactVersionsV9 = Field(default_factory=PipelineArtifactVersionsV9)
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
    """ProviderAttempt 层：一次实际外发（每内容轮至多
    `contracts/invariants.py::WORK_UNIT_MAX_DELIVERY_ATTEMPTS` 次）。

    ``response_sha256`` 是**按 attempt 耐久存储的原响应**的引用（工作单元目录内
    同名 blob）：原响应不再挂在请求身份名下——空回答以 ``failed`` attempt 留证，
    不占据请求身份（D5）；``outcome='ok'`` 只授予非空回答。
    """

    attempt_id: str = Field(min_length=1)
    outcome: Literal['started', 'ok', 'error', 'failed', 'unknown', 'not_started', 'interrupted']
    provider_session_id: str | None = None
    request_fingerprint: Sha256 | None = None
    response_sha256: Sha256 | None = None


class WorkUnitContentTurn(FrozenModel):
    turn_id: str = Field(min_length=1)
    ordinal: int = Field(ge=1)
    request_sha256: Sha256
    request_payload_sha256: Sha256 | None = None
    run_id: str = Field(min_length=1)
    session_run_id: str = ''
    #: R8a-5（2026-09-29 canary 实证）：reserve 时的执行身份三格。agent 流经
    #: ``NOVEL_COMMANDER_*`` 成对注入；``agent_run_id`` 与 ``run_id``（producer
    #: run，即成本行 ``run_id``）在 agent 流同值，显式双写供归属证明交叉核对；
    #: 空串＝手敲直连轮（无 AgentRun 外层）。``candidate_session_run_id`` 是候选
    #: 根 workflow epoch 的 session uuid——成本行 ``parent_run_id`` 的真身，此前
    #: 被误当 AgentRun id 导致追加额度证明永不闭合。
    agent_run_id: str = ''
    agent_epoch_id: str = ''
    candidate_session_run_id: str = ''
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


# ─────────────────────────────────────────────────────────────────────────────
# B1 执行设施：AcceptanceRecord 与 CommitBundle（运行记录五层的其余两层）
#
# 【五层与工作单元账的对齐】SemanticRequest＝工作单元 (node, scope)＋请求身份
# （``WorkUnitContentTurn.request_sha256``＋请求工作包 blob）；ContentRevision＝
# ``WorkUnitContentTurn``（内容轮额度沿用）；ProviderAttempt＝``WorkUnitAttempt``
# （原响应按 attempt 存储并被 attempt 引用）。下面两个模型补齐其余两层，取代候选根
# 的逐请求 ``validation_*.json`` 与独立 ``adopt_*.json`` 写路
# （映射表见 `docs/REQUIREMENTS.md`「候选根运行记录映射与删除条件」）。
# ─────────────────────────────────────────────────────────────────────────────

class SamplingSelectionV1(FrozenModel):
    """人工选择的采样身份；普通恢复复用此选择，不另分配序号。"""
    schema_version: Literal[1] = 1
    owner: str = Field(min_length=1)
    baseline_request_sha256: Sha256
    sequence: int = Field(ge=1)
    reason: str = Field(min_length=1)
    nonce: Sha256
    created_at: int = Field(ge=0)

    @model_validator(mode='after')
    def _real_reason(self):
        if not self.reason.strip():
            raise ValueError('显式重采必须有真实理由')
        return self


class OwnerRevisionRecordV1(FrozenModel):
    """R9 人工补丁／显式重采的耐久证据；staged 不授权任何恢复级联。"""

    schema_version: Literal[1] = 1
    kind: Literal['owner_patch', 'resample']
    owner: str = Field(min_length=1)
    node: str = Field(min_length=1)
    baseline_request_sha256: Sha256
    baseline_response_sha256: Sha256
    baseline_source_root: str = Field(min_length=1)
    candidate_sha256: Sha256
    evidence_rel: str = Field(pattern=r'^runtime/revisions/evidence/[0-9a-f]{64}\.json$')
    diff_sha256: Sha256
    reason: str = Field(min_length=1)
    sequence: int = Field(ge=0)
    product_rel: str = Field(min_length=1)
    before_product_sha256: Sha256 | None
    product_sha256: Sha256 | None = None
    status: Literal['staged', 'open', 'resolved']
    created_at: int = Field(ge=0)

    @model_validator(mode='after')
    def _closed_shape(self):
        if not self.reason.strip():
            raise ValueError('修订必须有真实理由')
        if self.status != 'staged' and self.product_sha256 is None:
            raise ValueError('已采用修订必须绑定正式产物摘要')
        if self.evidence_rel != f'runtime/revisions/evidence/{self.candidate_sha256}.json':
            raise ValueError('原字节证据路径必须与候选摘要一致')
        if (self.kind == 'resample') != (self.sequence > 0):
            raise ValueError('仅显式重采使用正采样序号')
        return self


class AcceptanceRecordV1(FrozenModel):
    """AcceptanceRecord 层：一次已交付响应（按 attempt）的接受分项记录。

    解析、救回（D5）、引用解析、编译与机械验证的分项结果落在这里；语义判定
    **恒 ``unver``**（I-42：未经标定的语义保持未验证，不冒充通过）。取代候选根
    逐请求的 ``runtime/validation_<身份>.json``。
    """

    schema_version: Literal[1] = 1
    node: str = Field(min_length=1)
    scope: str = Field(min_length=1)
    owner: str = Field(min_length=1)
    #: 历史／wire 采用没有工作单元内容轮（turn_id 为空）；新购必填于运行时。
    turn_id: str = ''
    attempt_id: str = Field(min_length=1)
    request_sha256: Sha256
    mode: Literal['json', 'text']
    parse: Literal['ok', 'failed', 'empty']
    #: D5 空回答救回：``reasoning``＝空回答从 reasoning 整段救回；文本调用恒 none。
    salvage: Literal['none', 'reasoning'] = 'none'
    errors: tuple[str, ...] = ()
    accepted: bool
    semantic: Literal['unver'] = 'unver'
    created_at: int = Field(ge=0)


class CommitInputRef(FrozenModel):
    """输入清单的一条边：键与盘上字节指纹（与 `InputEdge.file` 同一 10 位指纹）。"""

    key: str = Field(min_length=1)
    fingerprint: str = Field(pattern=r'^[0-9a-f]{10}$')


class CommitAdoptionRef(FrozenModel):
    """CommitBundle 里的采用证据：指向被采用响应的精确 ProviderAttempt。"""

    owner: str = Field(min_length=1)
    node: str = Field(min_length=1)
    request_sha256: str = Field(default='', min_length=0)
    unit_id: str = Field(default='', min_length=0)
    turn_id: str = Field(default='', min_length=0)
    attempt_id: str = Field(default='', min_length=0)
    response_sha256: str = Field(default='', min_length=0)
    model: str = Field(min_length=1)
    #: 历史采用／owner patch 的证据边界（cost_link 三档、patch 基线身份与理由）。
    historical: dict[str, Any] | None = None
    patch: dict[str, Any] | None = None


class CommitOutputRef(FrozenModel):
    key: str = Field(min_length=1)
    sha256: Sha256


class CommitBundleV1(FrozenModel):
    """CommitBundle 层：一次正式提交的记录（输入清单、采用证据、输出摘要与 seal）。

    ``seal`` 是除 seal 外全部字段的确定性摘要：**采用状态从 seal 派生**——一条
    内容轮被某份已封存 bundle 的 ``adoptions`` 引用即视为已采纳，不再依赖进程内
    窗口或独立 adopt 文件（映射表见 REQUIREMENTS）。落盘于候选根
    ``runtime/commit/``，随根自定位；seal 不符即记录损坏，关闭式拒绝。
    """

    schema_version: Literal[1] = 1
    node: str = Field(min_length=1)
    artifact: str = Field(min_length=1)
    artifact_sha256: Sha256
    inputs: tuple[CommitInputRef, ...] = ()
    adoptions: tuple[CommitAdoptionRef, ...] = ()
    outputs: tuple[CommitOutputRef, ...] = ()
    run_id: str = Field(min_length=1)
    created_at: int = Field(ge=0)
    seal: Sha256


def commit_bundle_seal(payload: dict[str, Any]) -> str:
    """CommitBundle 的 seal：除 ``seal`` 外全部字段的确定性 SHA-256（纯函数）。"""
    import hashlib
    import json

    material = {key: value for key, value in payload.items() if key != 'seal'}
    return hashlib.sha256(json.dumps(material, ensure_ascii=False, sort_keys=True,
                                     separators=(',', ':'), allow_nan=False)
                          .encode('utf-8')).hexdigest()


# ─────────────────────────────────────────────────────────────────────────────
# 附：STR-06 候选链的运行期配置与 workflow／epoch 身份记录
# （F8c 分流自 `contracts/schema/str06/candidates.py`；原样搬迁，字段未动）
#
# 【为什么住在 workflow.py】这两族记录描述的是「一次候选根运行的身份窗口」，
# 与上面的 WorkUnit／BookBudget 同属运行账家族，都不是小说产物。节点表与产物表
# 已在 F8a 激活进 `contracts/invariants.py::NODES` 与 `contracts/artifacts.py`，
# 本段只保留**运行期配置**与**身份记录模型**——不为切换保留第二张节点/产物表
# （S-04）。
#
# 【与付费调用额度的分界】配置进 prompt 与 IR，从而进请求身份；额度
# （`--book-call-ceiling`、工作单元／整书总闸）只闸新购次数，不进语义身份。
# CLI 旗标可覆盖配置，但覆盖值同样经 prompt／IR 进入身份——配置变化不误旧结果。
# ─────────────────────────────────────────────────────────────────────────────

from typing import Final, NamedTuple


class CandidateRuntimeConfig(NamedTuple):
    """候选链运行期配置的**正式来源**（F7c）：Hook 长度、章篇幅带与书级篇幅
    取整粒度。与付费调用额度（`--book-call-ceiling`、工作单元／整书总闸）是两件
    事：配置进 prompt 与 IR，从而进请求身份；额度只闸新购次数，不进语义身份。

    CLI 旗标可覆盖，但覆盖值同样经 prompt／IR 进入身份——配置变化不误用旧结果。
    """

    hook_target_chars: int = 280
    hook_hard_max_chars: int = 360
    length_band_low: float = 0.6
    length_band_high: float = 1.3
    #: 书级正文篇幅从源 MainText 章节总字数向上取整的粒度（保留全书篇幅边界）。
    length_budget_rounding: int = 1000


#: 现役候选运行期配置（版本随 RC 表一起进契约身份）。
STR06_RUNTIME_CONFIG: Final = CandidateRuntimeConfig()

#: 本族契约的 RC 版式身份（与 `STR06_RC_LABELS` 同源；ledger code 与 slice 业务身份
#: 引用本常量，避免各处手写字符串漂移）。rc6（2026-09-27 B2 五节点重建）：Stage2
#: 按语义单元与作用域拆五节点（D9），观察与关联协议落地
#（`contracts/schema/observation.py`，stage2-observation-v1）；联合抽取派生输入退役。
#: rc7（2026-09-27 B3 前半）：Stage4 四协议独立版化（preserve/design/bind/
#: hook_design，见下方 `STAGE4_*_PROTOCOL`），Design wire 瘦身——场景/拍 ID、
#: 复合引用、权重归一与整数预算改由 compiler 生成（I-21）；TargetDesign 增结构化
#: 跨章字段；读者确认必填事实地址（D4）。
#: rc8（2026-09-27 B4）：章包写手（D2）——WriterBookIR 增章级验证材料并升
#: `str06-rc8`；章回执改绑章包摘要（回执升 `str06-rc5`），不再绑定全书 IR
#: 与前文链。
#: rc9（2026-09-28 R1 关联无损）：associate 观察协议独立升版
#: `stage2-associate-v2`（typed 作用对象显式序列化＋状态句柄位置编码），
#: 见 `contracts/schema/observation.py`；三类观察协议不变。
#: rc10（2026-09-28 R2 语义送达）：Design wire 升 `stage4-design-v2`（拍上线程
#: `threads`、书级 `world_rules`、章级 `entry`；四个静态跨章列表删除，AUD-04）；
#: WriterBookIR／WriterContext 升 `str06-rc10`（拍级读取值挂拍、线程投影面、
#: continuity 分区、world_rules 进书级层）；章回执身份拆分（回执升 `str06-rc6`，
#: 写手视图／验证输入／Machine 审计三把摘要）；renderer 与写手协议升 v5。
STR06_RC_VERSION: Final = 'str06-rc11'

#: 已知 RC 标签面（版本规则见 `contracts/artifacts.py::STR06_ARTIFACT_RC_VERSIONS`）。
STR06_RC_LABELS: Final = ('str06-rc2', 'str06-rc3', 'str06-rc4', 'str06-rc5', 'str06-rc6',
                          'str06-rc7', 'str06-rc8', 'str06-rc9', 'str06-rc10', 'str06-rc11')

#: Stage4 四个付费语义节点的独立 wire 协议（B3 前半；各节点独立演进，升版互不
#: 牵连）。wire 顶层 `protocol_version` 与对应常量逐字对账，漏带或错带即拒
#: （I-13：本仓无隐式通用迁移）。模型只表达选择、结构与相对篇幅重点；场景/拍
#: ID（`sc{序:02d}`／`b{序:02d}`，按列表序派发）、复合引用（`场景#拍`）、
#: 权重归一与整数预算全部由 compiler 生成（I-21）。
#: design v2（R2）：beats 增 `threads`（本地标签，compiler 派发 `th{nn}`）、顶层增
#: `world_rules`、chapters 增 `entry`（第 1 章可省——compiler 填固定开篇，其余章
#: 必填）；standing_goals／ongoing_actions／open_questions／promises 四键关闭式
#: 拒读（AUD-04 无消费者，裁决 3 改独立线程结构）。
#: preserve v2（R10a a2，2026-10-01）：义务以 anchors 引用投影句柄（b拍.e/c/l、
#: s0_*、wc_*），lineage 退出 wire 由 compiler 派生（I-15/I-21）；顶层增
#: series（同一机制的多次发生，members≥2、mechanism 取源表达 ex_id）与
#: evidence_conflicts（源证据冲突报告，不发布——回派归 a3）。输入投影从
#: Blueprint JSON 倾倒改为 project_preserve_view 确定性渲染（证据窗口＋逐字
#: 引文）。policy v5 同批（义务锚定、series、冲突报告规则）。
STAGE4_PRESERVE_PROTOCOL: Final = 'stage4-preserve-v2'
STAGE4_PRESERVE_POLICY_VERSION: Final = 5
STAGE4_DESIGN_PROTOCOL: Final = 'stage4-design-v4'
STAGE4_BIND_PROTOCOL: Final = 'stage4-bind-v1'
STAGE4_HOOK_DESIGN_PROTOCOL: Final = 'stage4-hook-design-v1'

#: epoch 滚动理由面。`initial` 只允许首开；漂移类要求身份确实变化；`repair`
#: 是 typed 修复的显式 producer 边界（对齐正式 ExecutionEpochReason 的 continue）。
EPOCH_REASONS: Final = ('initial', 'code_drift', 'contract_drift', 'repair')

#: 候选节点身份的**激活补齐格**（F8c）：`candidate_contract_identity()` 的输入
#: 里有两格没有随 F8a 激活进 `contracts/invariants.py::NODES`——
#: ① 免费节点（compile／assemble）的 `session_scope`：冻结表按 executor 面登记
#:    `executor:book`，而 NODES 登记 `'none'`（它们确实不跑 executor，调度面
#:    说的是真话）；候选身份要的是冻结基线那一格。
#: ② `str06.stage7.assemble` 的 `owner`：语义 owner 是 `stage7.assembly`，节点
#:    id 却是 `str06.stage7.assemble`（命名历史，F7a 定版）。
#: 其余 owner 一律由节点 id 去掉 `str06.` 前缀派生（10/11 如此），不在这里抄
#: 第二份节点表（S-04）。
#: ⚠ 本表存在的唯一理由是**逐字复算 F7e 冻结身份**：盘上
#: `runtime/candidate_workflow.json` 记的正是那个 SHA-256，算法一漂移，全部既有
#: 候选根都会被 `check_candidate_workflow` 判「契约表已漂移」而拒绝消费已购
#: 证据——那是行为变化，不是搬家。对账在 `tests/test_str06_f7e_freeze.py`。
STR06_NODE_IDENTITY_EXTRAS: Final[dict[str, dict[str, str]]] = {
    'str06.stage2.compile': {'session_scope': 'executor:book'},
    'str06.stage4.compile': {'session_scope': 'executor:book'},
    'str06.stage7.assemble': {'session_scope': 'executor:book',
                              'owner': 'stage7.assembly'},
}


def candidate_node_owner(node_id: str) -> str:
    """候选节点的语义 owner：节点 id 去掉 `str06.` 前缀，例外按 EXTRAS 登记。

    其余 `str06.` 节点由前缀派生；`str06.stage7.assemble` 的语义 owner 是
    `stage7.assembly`（命名历史），单独登记。消费方（views 监控面、契约身份）
    共用本函数，不各自拆字符串。
    """
    extra = STR06_NODE_IDENTITY_EXTRAS.get(node_id, {})
    return extra.get('owner', node_id[len('str06.'):])


class CandidateEpoch(NamedTuple):
    """候选 workflow 的一段身份稳定的执行窗口。

    epoch_id 由（契约表身份、code_version、ordinal、理由、task_id、
    session_run_id、前一 epoch_id）确定性派生；ordinal 单调递增，链条可验证。
    """

    epoch_id: str
    ordinal: int
    reason: str
    contract_version: str
    code_version: str
    task_id: str
    session_run_id: str
    created_at: int


class CandidateWorkflow(NamedTuple):
    """候选根的 workflow 身份记录（落盘 `runtime/candidate_workflow.json`）。

    绑定 epoch、契约表身份与源输入身份；不含路径（记录随根自定位）。
    `source_identity` 在 F7c 改读正式 Stage1 章数组前是源 JSON 的完整字节
    SHA-256，之后是 Stage1 输入的身份摘要——消费形状变化必须换身份，
    不能沿用旧根的采用结论。
    """

    epoch: CandidateEpoch
    contract_identity: str
    source_identity: str
    created_at: int


# ─────────────────────────────────────────────────────────────────────────────
# R3 自愈（2026-09-28 裁决 1）：OwnerIssue 与机械问题的修订路由
#
# 【只修 typed、可定位的机械问题】路由范围限定 schema、闭集、锚点、引用闭合、
# 关联未决与必保义务未承载；文学质量永不进入自动修复（语义保持 unver）。
# 【两个闭轴】code 轴：未知 code 一律停（不猜路由）；owner 轴：
# `must_keep_unbound` 的 `unbound.owner` 只认 `UNBOUND_OWNER_ROUTES` 登记的
# 两个值（与 BIND_RULES 的 `design|grounding` 闭集同一取值面），未映射即停
# （E-8：wire 上 `owner` 是自由 str，闭合责任落在路由表，不放给 schema 宽容）。
# ─────────────────────────────────────────────────────────────────────────────

class OwnerIssueV1(FrozenModel):
    """一条 typed、可定位的机械问题（R3 自愈面的唯一 issue 形状）。

    起点复用 `novel/stages/stage2/resolver.py::ResolverIssue` 的三格
    （code／message／context），增补 ``item`` 作为修订定位（章序、义务 id 或
    句柄）。``code`` 的取值面与路由在下方两张表里闭合，未知 code 由路由停。
    """

    code: str = Field(min_length=1)
    item: str = ''
    message: str = Field(min_length=1)
    context: dict[str, Any] = Field(default_factory=dict)


class OwnerIssueFileV1(FrozenModel):
    """`runtime/issues/<owner>.json`（v1，R8a 之前写下）：读侧兼容形状。

    v1 与 v2 的差别只有 ``against_sha256``（v2 增）。盘上 v1 记录经
    :func:`owner_issue_record_of_payload` 集中迁移升到 v2，读写两侧不再
    各自处理两种形状；本模型只作迁移源，不用于新写盘。
    """

    schema_version: Literal[1] = 1
    #: 被要求修订的 owner（runner 词汇：associate／design／preserve）。
    owner: str = Field(min_length=1)
    #: 修订目标节点 id（路由表 `revise_upstream:<节点>` 的落点）。
    node: str = Field(min_length=1)
    #: 发现问题的下游节点 id（回派由它发起、复验由它清账）。
    raised_by: str = Field(min_length=1)
    status: Literal['open', 'resolved']
    rounds_total: int = Field(ge=0, le=OWNER_REVISION_MAX_ROUNDS)
    fingerprint: str = Field(min_length=1)
    issues: tuple[OwnerIssueV1, ...] = Field(min_length=1)
    updated_at: int = Field(ge=0)


class OwnerIssueFileV2(FrozenModel):
    """`runtime/issues/<owner>.json`（v2，R8a 起唯一写盘形状）：回派账增
    ``against_sha256``——发现节点判定时 owner 正式产物
    （`OWNER_PRODUCT_RELS`）的字节摘要。

    调度门控只认这份摘要：owner 当前产物摘要仍等于 ``against_sha256`` 时，
    `resume_verdict` 才判修订 REDO；已变（owner 修订写回或人工 patch）就不
    再回头重买 owner，等下游按新鲜度复验（compile 通过置 resolved，再发现按
    现有轮次规则计数或停）。其余格子语义同 v1（轮数终身、不复位；同指纹
    无进展即停）。
    """

    schema_version: Literal[2] = 2
    owner: str = Field(min_length=1)
    node: str = Field(min_length=1)
    raised_by: str = Field(min_length=1)
    status: Literal['open', 'resolved']
    rounds_total: int = Field(ge=0, le=OWNER_REVISION_MAX_ROUNDS)
    fingerprint: str = Field(min_length=1)
    against_sha256: Sha256
    #: R8a：同指纹重现（修订无进展）置 True——调度门控据此停给人，不再自动
    #: 重买 owner（复验会刷新 against，光看轮数会在「1/2＋产物未变」上打转）。
    no_progress: bool = False
    issues: tuple[OwnerIssueV1, ...] = Field(min_length=1)
    updated_at: int = Field(ge=0)


def owner_issues_rel(owner: str) -> str:
    """跨节点回派账的唯一落点（runtime 家族，非版本化书产物）。"""
    return f'runtime/issues/{owner}.json'


#: 回派 owner → 其正式产物 rel（``against_sha256`` 的对照物；owner 轴取值面
#: 与 `UNBOUND_OWNER_ROUTES` 的映射目标一致）。未登记 owner 在 digest 计算口
#: 显式拒绝——闭集责任在本表，不放给调用方各自猜。
OWNER_PRODUCT_RELS: Final[dict[str, str]] = {
    'associate': 'stage2/observations/associate.json',
    'design': 'stage4/design/body_design.json',
    'preserve': 'runtime/preserve_checkpoint.json',
}


def owner_issue_record_of_payload(
        payload: Mapping[str, Any],
        product_sha256_of: Callable[[str], str],
) -> OwnerIssueFileV2:
    """回派账的统一读口：v2 原样、v1 集中迁移（S-03），一律升到 v2 形状。

    v1 的 ``against_sha256`` 由 ``product_sha256_of(owner)`` 给出 owner 正式
    产物的**当前**摘要——v1 语义下回派以未捕获异常（exit 70）告终、owner 从
    未有机会修订，「当前」即「判定时」。迁移是集中兼容层，删除条件：付费与
    开发根盘上不再存在 `schema_version=1` 的回派账（canary 20260929b 根只读
    归档即满足）。未知版本号显式拒绝（I-34）。
    """
    version = payload.get('schema_version')
    if version == 2:
        return OwnerIssueFileV2.model_validate(dict(payload))
    if version == 1:
        legacy = OwnerIssueFileV1.model_validate(dict(payload))
        return OwnerIssueFileV2(
            owner=legacy.owner, node=legacy.node, raised_by=legacy.raised_by,
            status=legacy.status, rounds_total=legacy.rounds_total,
            fingerprint=legacy.fingerprint,
            against_sha256=product_sha256_of(legacy.owner),
            issues=legacy.issues, updated_at=legacy.updated_at)
    raise ValueError(
        f'回派账 schema_version 未知：{version!r}（取值面 1|2；'
        '未知形状不得静默当作任何一种语义继续）')


#: issue code 闭集：前四码逐字复用 Resolver 的精确码（R3 起点），后三码是
#: 自愈面新增的归并码。扩码必须同批更新路由表与守卫测试（未知 code 一律停）。
OWNER_ISSUE_CODES: Final = (
    'unknown_segment',     # 引用了闭集之外的句段 id（Resolver）
    'missing_literal',     # 逐字锚在所引句段内不存在（Resolver）
    'ambiguous_literal',   # 逐字锚出现多次、无法唯一定位（Resolver）
    'anchor_conflict',     # 逐字锚命中分散在多个句段（Resolver）
    'schema_violation',    # wire 形状／协议字面量／重复键（同节点）
    'association_undecided',   # 关联未决（D8）：回派 associate
    'must_keep_unbound',   # 必保义务未承载：按 unbound.owner 回 body_design/preserve
)

#: code → `revise_self`｜`revise_upstream:<节点>`｜（未登记即 stop）。
#: `revise_upstream:unbound_owner` 是占位：目标节点按 issue 携带的
#: `unbound.owner` 经 `UNBOUND_OWNER_ROUTES` 解析，未映射即停（owner 轴闭合）。
OWNER_ISSUE_ROUTES: Final[dict[str, str]] = {
    'unknown_segment': 'revise_self',
    'missing_literal': 'revise_self',
    'ambiguous_literal': 'revise_self',
    'anchor_conflict': 'revise_self',
    'schema_violation': 'revise_self',
    'association_undecided': 'revise_upstream:str06.stage2.associate',
    'must_keep_unbound': 'revise_upstream:unbound_owner',
}

#: `UnboundEntry.owner` → 回派目标节点（取值面与 BIND_RULES 的 design|grounding
#: 闭集一致；wire 字段保持 str，闭合责任在本表，未映射即停）。
UNBOUND_OWNER_ROUTES: Final[dict[str, str]] = {
    'design': 'str06.stage4.body_design',
    'grounding': 'str06.stage4.preserve',
}


def route_owner_issue(code: str, *, unbound_owner: str = '') -> tuple[str, str]:
    """一条 issue 的修订路由：``(action, target)``。

    ``action`` ∈ ``{'revise_self', 'revise_upstream', 'stop'}``；``target`` 是
    目标节点 id（revise_self 时为空串）或停止原因（stop 时）。未知 code、
    未映射的 ``unbound_owner`` 一律 stop——机械问题路由本身不猜（E-8）。
    """
    route = OWNER_ISSUE_ROUTES.get(code)
    if route is None:
        return 'stop', f'未知问题码 {code!r}（闭集={OWNER_ISSUE_CODES}；一律停）'
    if route == 'revise_self':
        return 'revise_self', ''
    target = route.partition(':')[2]
    if target == 'unbound_owner':
        node = UNBOUND_OWNER_ROUTES.get(unbound_owner)
        if node is None:
            return ('stop',
                    f'unbound.owner {unbound_owner!r} 未映射（闭集='
                    f'{tuple(UNBOUND_OWNER_ROUTES)}；一律停）')
        return 'revise_upstream', node
    return 'revise_upstream', target
