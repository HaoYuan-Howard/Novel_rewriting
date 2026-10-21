"""新工作流的运行账；它不是小说产物，也不替代成本或审批证据。"""
from __future__ import annotations

from collections.abc import Mapping
from typing import Annotated, Any, Literal

from pydantic import Field, model_validator

from contracts.invariants import OWNER_REVISION_MAX_ROUNDS
from contracts.schema.base import FrozenModel
from contracts.schema.common import Sha256
from contracts.schema.source_story import STORY_VERSION, StoryVersion


class WorkflowManifest(FrozenModel):
    """当前输出根身份；不解码旧流水线或提供同根迁移。"""
    schema_version: Literal[1]
    contract_version: StoryVersion
    pipeline_contract_epoch: StoryVersion
    task_id: Sha256
    created_at: int


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


class LlmNodeVersionReset(FrozenModel):
    """2026-10-11 裁决：全部 LLM 节点升版恢复基础额度；版本基线也必须落账。

    additional=0 仍保存版本，防止先免费处理、后付费时重复恢复。同一版本
    不重复追加；当前账首次登记时显式建立节点版本基线。
    """

    upgrade_id: Sha256
    versions: dict[str, Annotated[int, Field(ge=1, strict=True)]] = Field(min_length=1)
    reason: str = Field(min_length=1, max_length=2000)
    created_at: int = Field(ge=0)
    before_ledger_sha256: Sha256
    refunded_after_ordinal: int = Field(ge=0)
    additional: int = Field(ge=0, le=4, strict=True)

    @model_validator(mode='after')
    def _versions(self) -> LlmNodeVersionReset:
        if any(not key or type(value) is not int or value < 1
               for key, value in self.versions.items()):
            raise ValueError('节点版本分量必须有名称和显式正整数')
        return self


class WorkUnitProcessing(FrozenModel):
    """一次实际处理的来源；免费处理只记证据，绝不伪造内容轮次。"""

    processing_id: Sha256
    run_id: str = Field(min_length=1)
    input_sha256: Sha256
    kind: Literal['pending', 'model', 'mechanical', 'cached']
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


class WorkUnitLedgerV3(FrozenModel):
    """唯一现役预算账；旧版账拒识，不迁移。"""
    schema_version: Literal[3]
    task_id: Sha256
    unit_id: Sha256
    sid: str = Field(min_length=1)
    node: str = Field(min_length=1)
    role: Literal['executor', 'commander']
    scope: str = Field(min_length=1, max_length=160)
    base_content_limit: Literal[4] = 4
    grants: tuple[BudgetGrant, ...] = ()
    version_resets: tuple[LlmNodeVersionReset, ...] = ()
    turns: tuple[WorkUnitContentTurn, ...] = ()
    processing: tuple[WorkUnitProcessing, ...] = ()
    committed_through: int = Field(default=0, ge=0)
    committed_artifact: str | None = None
    committed_fingerprint: str | None = None
    # 整组候选可能全部来自缓存；提交证明不能为此伪造内容轮次。
    group_producer_sha256: Sha256 | None = None
    group_producer_completed: bool = False

    @model_validator(mode='after')
    def _closed_turns(self) -> WorkUnitLedgerV3:
        # 人工追加与升级返还按登记时间合并校验：追加只能在「当前额度（含此前
        # 返还）已耗尽」时登记——先返还后追加的单元，追加的 authorized_after_
        # ordinal 对合并序下的运行额度成立，而不是对「只数追加」的假额度。
        limit = self.base_content_limit
        intents: set[str] = set()
        refund_ids: set[str] = set()
        events = self._budget_events()
        for _at, _kind_order, _index, row in events:
            if isinstance(row, BudgetGrant):
                if row.intent_id in intents or row.authorized_after_ordinal != limit:
                    raise ValueError('追加授权必须唯一，且只能在已有额度耗尽后累计')
                if row.authorized_after_ordinal > len(self.turns):
                    raise ValueError('人工追加不得越过实际已用轮次')
                intents.add(row.intent_id)
                limit += row.additional
            else:
                if row.upgrade_id in refund_ids:
                    raise ValueError('同一升级对同一工作单元的轮次返还必须唯一')
                if row.refunded_after_ordinal > len(self.turns):
                    raise ValueError('轮次返还不得越过实际已用轮次')
                if not row.reason.strip():
                    raise ValueError('轮次返还必须登记理由')
                if row.additional != max(
                        0, self.base_content_limit - (limit - row.refunded_after_ordinal)):
                    raise ValueError('节点升版只能恢复基础可用额度，不能额外发放轮次')
                refund_ids.add(row.upgrade_id)
                limit += row.additional
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
        return (self.base_content_limit
                + sum(grant.additional for grant in self.grants)
                + sum(reset.additional for reset in self.version_resets))


    def _budget_events(self):
        return sorted(
            [*((row.created_at, 0, index, row) for index, row in enumerate(self.grants)),
             *((row.created_at, 1, index, row) for index, row in enumerate(self.version_resets))],
            key=_budget_event_order)


    @model_validator(mode='after')
    def _version_chain(self) -> WorkUnitLedgerV3:
        previous: dict[str, int] = {}
        ordinal = 0
        for row in self.version_resets:
            if (row.refunded_after_ordinal < ordinal
                    or not set(previous) <= set(row.versions)
                    or any(row.versions[key] < value for key, value in previous.items())
                    or row.versions == previous):
                raise ValueError('节点版本重置必须按单调升版顺序登记，不能重复或降版')
            previous, ordinal = row.versions, row.refunded_after_ordinal
        return self


def work_unit_ledger_of_payload(payload: dict) -> WorkUnitLedgerV3:
    """仅接受当前账格式；旧账原字节留存，不由运行时转换。"""
    return WorkUnitLedgerV3.model_validate(payload)


def _budget_event_order(event):
    _at, kind, index, row = event
    ordinal = (row.authorized_after_ordinal if isinstance(row, BudgetGrant)
               else row.refunded_after_ordinal)
    # 同轮序的授权先于首次版本基线；同类保留 append 顺序，不依赖墙钟精度。
    return ordinal, kind, index


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
    turn_id: str = Field(min_length=1)
    attempt_id: str = Field(min_length=1)
    request_sha256: Sha256
    mode: Literal['json', 'text']
    response_kind: Literal['result', 'material', 'patch', 'format_failure', 'upstream_issue']
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
    request_sha256: Sha256
    unit_id: str = Field(min_length=1)
    turn_id: str = Field(min_length=1)
    attempt_id: str = Field(min_length=1)
    response_sha256: Sha256
    model: str = Field(min_length=1)


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
    contract_version: StoryVersion
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
    """候选链运行期配置的**正式来源**（F7c）：章篇幅带与书级篇幅
    取整粒度。与付费调用额度（`--book-call-ceiling`、工作单元／整书总闸）是两件
    事：配置进 prompt 与 IR，从而进请求身份；额度只闸新购次数，不进语义身份。

    CLI 旗标可覆盖，但覆盖值同样经 prompt／IR 进入身份——配置变化不误用旧结果。
    """

    length_band_low: float = 0.6
    length_band_high: float = 1.3
    #: 书级正文篇幅从源 MainText 章节总字数向上取整的粒度（保留全书篇幅边界）。
    length_budget_rounding: int = 1000


#: 现役候选运行期配置（版本随 RC 表一起进契约身份）。
STR06_RUNTIME_CONFIG: Final = CandidateRuntimeConfig()

#: 当前三层契约身份；破坏性变更拒绝旧根，产物在新根重新生成。
STR06_RC_VERSION: Final = STORY_VERSION
STR06_RC_LABELS: Final = (STORY_VERSION,)

#: epoch 滚动理由面。`initial` 只允许首开；漂移类要求身份确实变化；`repair`
#: 是 typed 修复的显式 producer 边界（对齐正式 ExecutionEpochReason 的 continue）。
EPOCH_REASONS: Final = ('initial', 'code_drift', 'contract_drift', 'repair')

def candidate_node_owner(node_id: str) -> str:
    """现役节点的语义 owner 与唯一节点登记同名。"""
    if not node_id.startswith('str06.'):
        raise ValueError('候选节点必须使用现役 str06 命名空间')
    return node_id[len('str06.'):]


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
    source_identity绑定原始输入SHA256；source_root_sha1供来源边复校；
    source_content_sha256绑定规范化的冻结源全文。三者均必填，无旧根迁移。
    """

    epoch: CandidateEpoch
    contract_identity: str
    source_identity: str
    source_root_sha1: str
    source_content_sha256: str
    created_at: int


# 当前三层回派：语义归属显式声明，机械验证不猜控制流；旧根不解码。

class OwnerIssueV1(FrozenModel):
    """当前typed问题条目；context携带明确path与证据，文件版本由V3封闭。"""

    code: str = Field(min_length=1)
    item: str = ''
    message: str = Field(min_length=1)
    context: dict[str, Any] = Field(default_factory=dict)


class OwnerIssueCorrection(FrozenModel):
    """显式归因更正保存当前原账；不能凭机械通过推断语义问题已消失。"""
    original: 'OwnerIssueFileV3'
    evidence_sha256: Sha256
    reason: str = Field(min_length=1)
    target_owners: tuple[str, ...] = Field(min_length=1)
    created_at: int = Field(ge=0)


class OwnerIssueFileV3(FrozenModel):
    """当前三层唯一回派账；不解码或升级任何旧版本。"""
    contract_version: StoryVersion
    schema_version: Literal[3] = 3
    owner: str = Field(min_length=1)
    node: str = Field(min_length=1)
    raised_by: str = Field(min_length=1)
    status: Literal['open', 'resolved', 'superseded']
    rounds_total: int = Field(ge=0, le=OWNER_REVISION_MAX_ROUNDS)
    fingerprint: str = Field(min_length=1)
    against_sha256: Sha256
    no_progress: bool = False
    issues: tuple[OwnerIssueV1, ...] = Field(min_length=1)
    updated_at: int = Field(ge=0)
    dispatch_pending: bool
    fingerprint_history: tuple[str, ...] = ()
    corrections: tuple[OwnerIssueCorrection, ...] = ()

    @model_validator(mode='after')
    def valid_routing(self):
        if owner_issue_node(self.owner) != self.node:
            raise ValueError('回派owner与正式产物writer不一致')
        from contracts.invariants import NODES
        if self.raised_by not in NODES:
            raise ValueError('发现节点必须属于当前节点表')
        if self.status == 'superseded' and not self.corrections:
            raise ValueError('superseded必须具有身份绑定的显式归因更正证据')
        if any(c.original.owner != self.owner for c in self.corrections):
            raise ValueError('更正证据必须属于同一owner')
        if self.dispatch_pending and (self.status != 'open' or self.no_progress):
            raise ValueError('只有未停止的open回派允许待执行')
        return self


def owner_issues_rel(owner: str) -> str:
    """跨节点回派账的唯一落点；拒绝把任意字符串当路径。"""
    owner_issue_node(owner)
    return f'runtime/issues/{owner}.json'


from contracts.story_paths import (
    ASSOCIATION, BOOK_OBSERVATION, NAME_OBSERVATION, NAME_MAP, PRESERVATION,
    TARGET_MODEL, HOOK_PLAN, HOOK, CHAPTER_OBSERVATION, CHAPTER_PLAN, BODY, chapter_rel,
)

OWNER_PRODUCT_RELS: Final[dict[str, str]] = {
    'book': BOOK_OBSERVATION, 'associate': ASSOCIATION, 'names': NAME_OBSERVATION,
    'naming': NAME_MAP, 'preserve': PRESERVATION, 'design': TARGET_MODEL,
    'hook_design': HOOK_PLAN, 'hook_writer': HOOK,
}

_OWNER_NODES: Final[dict[str, str]] = {
    'book': 'str06.stage2.book', 'associate': 'str06.stage2.associate',
    'names': 'str06.stage2.names', 'naming': 'str06.stage4.naming',
    'preserve': 'str06.stage4.preserve', 'design': 'str06.stage4.body_design',
    'hook_design': 'str06.stage4.hook_design', 'hook_writer': 'str06.stage5.hook',
}


def _chapter_owner(owner: str):
    import re
    found = re.fullmatch(r'(ch|plan_ch|body_ch)([0-9]{2,})', owner)
    if found is None:
        return None
    ordinal = int(found[2])
    if ordinal < 1 or found[2] != f'{ordinal:02d}':
        return None
    return found[1], ordinal


def chapter_owner_product_rel(owner: str) -> str | None:
    found = _chapter_owner(owner)
    if found is None:
        return None
    prefix, ordinal = found
    return chapter_rel({'ch': CHAPTER_OBSERVATION, 'plan_ch': CHAPTER_PLAN,
                        'body_ch': BODY}[prefix], ordinal)


def owner_issue_node(owner: str) -> str:
    if owner in _OWNER_NODES:
        return _OWNER_NODES[owner]
    found = _chapter_owner(owner)
    if found is not None:
        return {'ch': 'str06.stage2.chapters', 'plan_ch': 'str06.stage4.chapters',
                'body_ch': 'str06.stage5.body'}[found[0]]
    raise ValueError(f'owner {owner!r}不属于当前正式产物闭集')


def owner_issue_record_of_payload(payload: Mapping[str, Any]) -> OwnerIssueFileV3:
    """只读显式story-v3/V3；缺失版本与旧账均拒绝，不补当前摘要冒充旧证据。"""
    if payload.get('contract_version') != STORY_VERSION or payload.get('schema_version') != 3:
        raise ValueError('回派账只支持显式story-v3/schema_version=3；旧根须独立重建')
    return OwnerIssueFileV3.model_validate(dict(payload))


OWNER_ISSUE_CODES: Final = (
    'schema_violation', 'source_missing', 'source_contradiction', 'source_unsupported',
    'source_observation_issue', 'source_name_structure_missing', 'needs_design',
    'target_cross_boundary_change',
)
OWNER_ISSUE_ROUTES: Final[dict[str, str]] = {
    'schema_violation': 'revise_self',
    'source_missing': 'revise_upstream:str06.stage2.chapters',
    'source_contradiction': 'revise_upstream:str06.stage2.chapters',
    'source_unsupported': 'revise_upstream:str06.stage2.chapters',
    'source_observation_issue': 'revise_upstream:str06.stage2.chapters',
    'source_name_structure_missing': 'revise_upstream:str06.stage2.names',
    'needs_design': 'revise_upstream:explicit_owner',
    'target_cross_boundary_change': 'revise_upstream:explicit_owner',
}


def route_owner_issue(code: str, *, owner: str = '') -> tuple[str, str]:
    """只路由已登记问题；需上下文时必须给正式owner，禁止解析自然语言猜归属。"""
    route = OWNER_ISSUE_ROUTES.get(code)
    if route is None:
        return 'stop', f'未知问题码{code!r}，禁止猜测回派'
    action, _, target = route.partition(':')
    if target == 'explicit_owner':
        try:
            target = owner_issue_node(owner)
        except ValueError as exc:
            return 'stop', str(exc)
    return action, target


OwnerIssueFileV3.model_rebuild()
OwnerIssueCorrection.model_rebuild()
