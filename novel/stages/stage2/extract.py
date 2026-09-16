#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""`stage2.global` 与 `stage2.blueprint` 的当前生成、归一和判定路径。

两类产物都以 ``checks.check()`` 为唯一判定入口。global 模型响应只引用关闭的
段落候选 ID，由代码铸成共享证据锚点；人工编辑与当前 ``.INVALID`` 恢复
仍可使用 locator，但不属于 provider wire。
"""
from __future__ import annotations

from contracts.artifacts import (
    STAGE1_MAIN_TEXT, STAGE2_BLUEPRINT_CHARACTER_PROJECTION,
    STAGE2_CHAPTER_BLUEPRINTS, STAGE2_GLOBAL_EDIT, STAGE2_GLOBAL_EDIT_COMMIT,
    STAGE2_GLOBAL_EDIT_FAILURE, STAGE2_GLOBAL_EDIT_INPUT_COMMIT,
    STAGE2_GLOBAL_GROUNDING, STAGE2_GLOBAL_REVIEW,
)

import copy
import hashlib
import json
import re
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable, Protocol

from pydantic import BaseModel, ValidationError

from contracts.invariants import (
    BLUEPRINT_ARCHITECTURE, STAGE2_LEGACY_REVALIDATE_ACTOR,
    STAGE2_LEGACY_REVALIDATE_REASON,
)
from contracts.rules import REGISTRY
from contracts.schema import ArtifactVersionError, encode_artifact
from contracts.schema.base import StrictModel
from contracts.schema.report import CheckReport
from contracts.schema.evaluation import GlobalReview
from contracts.schema.stage1 import MainText
from contracts.schema.characters import CharacterRegistry
from contracts.schema.knowledge import (
    KnowledgeGrounding,
    KnowledgeAgentTurnResponse as GlobalCandidateAgentTurnResponse,
    KnowledgeFullTurn as GlobalCandidateFullTurn,
    KnowledgeResponse as GlobalGroundingCandidateResponse,
)
from contracts.schema.stage2 import (
    BlueprintCharacterProjection, BlueprintProvenance, BlueprintScopeViolation,
    GlobalCollectionAdd, GlobalCollectionDelete,
    GlobalCollectionSplit,
    ChapterBlueprints,
    GlobalEdit, GlobalEditCommit, GlobalEditFailure, GlobalEditInputCommit,
    GlobalGrounding,
    GlobalProvenance,
)
from novel import paths, prov
from novel.stages.stage2 import grounding as grounding_io
from novel.config import MAX_CONCURRENCY
from novel.errors import (
    ArtifactUnreadable, BlockingFindingsUnresolved, GateBlocked, PathRefused, ProvenanceRefused,
    ResponseRepairBatchRequired, ResponseRepairRequired, TerminalState,
    UpstreamMissing,
)
from novel.llm.cache import CachePolicy
from novel.llm.client import LlmClient
from novel.llm.ledger import combine_usage
from novel.llm.prompts import required_top_level, with_json_shape
from novel.observability import ChapterProgressTracker
from novel.report import build
from novel.report.findings import Outcome
from novel.run import RunContext
from novel.stages.stage2 import checks
from novel.stages.stage2.character_refs import (
    blueprint_inventory_from_projection,
    materialize_blueprint_character_projection,
)
from novel.stages.stage2.global_contract import GlobalEvidenceContractSpec
from novel.stages.stage2.global_evidence_candidates import (
    GlobalEvidenceCandidateTable,
)
from novel.stages.stage2.global_normalize import (
    EvidenceLocatorCorrection, GlobalNormalizationAccounting, normalize_global,
)
from novel.store import ArtifactStore

GLOBAL_REL = STAGE2_GLOBAL_GROUNDING
BLUEPRINT_REL = STAGE2_CHAPTER_BLUEPRINTS
BLUEPRINT_CHARACTER_REL = STAGE2_BLUEPRINT_CHARACTER_PROJECTION
MAIN_TEXT = STAGE1_MAIN_TEXT
# Stage2 语义审阅已退役：该路径只供存量 reviewed-edit journal 的恢复读面；
# 新 clean base 不再产生 global_review.json（已在 RETIRED_ARTIFACT_PATHS）。
GLOBAL_REVIEW_REL = STAGE2_GLOBAL_REVIEW

#: `--apply-edit` 的缺省决策集路径。形状是契约（`ARTIFACTS` 里登记着 `GlobalEdit`）。
GLOBAL_EDIT = STAGE2_GLOBAL_EDIT
#: canonical reviewed edit 已落、但 formal Global/review 尚未提交的 typed 恢复事实。
#: 它不是 HARD 候选，故绝不派生/复用 ``GLOBAL_REL.INVALID``。
GLOBAL_EDIT_FAILURE = STAGE2_GLOBAL_EDIT_FAILURE
#: canonical sparse edit + pending/superseded failure 的 action-target WAL。
GLOBAL_EDIT_INPUT_COMMIT = STAGE2_GLOBAL_EDIT_INPUT_COMMIT
#: GlobalEdit formal bundle 的 write-ahead journal；不属于 Commander failure。
GLOBAL_EDIT_COMMIT = STAGE2_GLOBAL_EDIT_COMMIT

#: 被闸门拦下的那一份。**从 `REL` 派生，不手写第二个字符串。**
_INVALID = paths.invalid_of(BLUEPRINT_REL)
_GLOBAL_INVALID = paths.invalid_of(GLOBAL_REL)

GLOBAL_MAX_TOKENS = 96_000
BLUEPRINT_MAX_TOKENS = 256_000


class _GlobalResponseSource(Protocol):
    """全局 producer 真正依赖的最小来源面；旧 LlmClient 与 session client 同形。"""

    model: str

    def accepted_attempt(self, purpose: str) -> str | None: ...

    def accepted_models(self, *purposes: str) -> tuple[str, ...]: ...

    def accepted_checkpoint(self, purpose: str) -> dict[str, Any]: ...

    def restore_checkpoint_acceptance(
            self, purpose: str, record: Any, **kwargs: Any,
    ) -> None: ...


class _BlueprintRequestSource(_GlobalResponseSource, Protocol):
    """blueprint 扇出真正消费的客户端窄面。

    agent 路径应由节点层提供一个按 ``(phase, chapter)`` 路由的聚合器；聚合器背后
    每格必须是独立 ``SessionAgentClient``。这里不持有 driver，也不另造 checkpoint
    或 adoption 状态，只继续消费既有 ``stage2_runs`` 所要求的桥接方法。
    """

    concurrency: int

    def ask_json(self, system: str, user: str, **kwargs: Any) -> Any: ...

    def json_request_policy(
            self, phases: Any, *, recovery_policy: str,
    ) -> dict[str, Any]: ...

    def accepted_checkpoint(self, purpose: str) -> dict[str, Any]: ...

    def restore_checkpoint_acceptance(
            self, purpose: str, record: Any, **kwargs: Any,
    ) -> None: ...


@dataclass(frozen=True)
class GlobalCandidateFinding:
    """一条可交回同一 agent session 的代码结论；它不是落盘报告。"""

    detail: str
    evidence: tuple[str, ...]

    def to_dict(self) -> dict[str, object]:
        return {'detail': self.detail, 'evidence': list(self.evidence)}


@dataclass(frozen=True)
class GlobalCandidateFeedback:
    """一条 HARD 拒因的稳定业务摘要。

    完整摘要只用于下一轮 provider prompt；成本/会话审计只应另取 rule 与计数，
    不能把 ``detail`` 或 ``evidence`` 复制进 ``_service``。
    """

    rule: str
    item: str
    requirement: str
    findings: tuple[GlobalCandidateFinding, ...]

    def to_dict(self) -> dict[str, object]:
        return {
            'rule': self.rule,
            'item': self.item,
            'requirement': self.requirement,
            'findings': [finding.to_dict() for finding in self.findings],
        }


@dataclass(frozen=True)
class _BlueprintGuard:
    """global 重跑期间当前正式 blueprint 的冻结快照。"""

    state: str                         # absent | current | older:<found>:<current>
    raw_sha256: str | None = None
    raw: dict[str, Any] | None = field(default=None, repr=False)


@dataclass(frozen=True)
class _GlobalDownstreamGuard:
    """现有 blueprint 差分闸所需的全部冻结事实。"""

    blueprint: _BlueprintGuard
    baseline_state: str                # unused | current | absent | older:<found>:<current>
    baseline_sha256: str | None = None
    baseline_hard_hits: frozenset[str] | None = None


@dataclass(frozen=True)
class GlobalCandidateContext:
    """一次 global 候选循环的冻结输入。

    agent 只需要公开的 ``system`` / ``user``。正文、旧 global 与 blueprint 快照留在
    producer 内存里；调用方拿不到任何正式落盘路径，也无须自己重拼差分判据。
    """

    system: str
    user: str
    contract_name: str
    input_sha256: str
    main_text_sha256: str
    evidence_candidates_sha256: str
    characters_reference_sha256: str
    _characters: CharacterRegistry = field(repr=False)
    _main_text: tuple[dict[str, Any], ...] = field(repr=False)
    _downstream: _GlobalDownstreamGuard = field(repr=False)
    _contract: GlobalEvidenceContractSpec = field(repr=False)
    _evidence_candidates: GlobalEvidenceCandidateTable = field(repr=False)
    _global_id_map: dict[str, str] = field(default_factory=dict, repr=False)
    _global_previous_ids: frozenset[str] = field(default_factory=frozenset, repr=False)
    _global_retired_ids: frozenset[str] = field(default_factory=frozenset, repr=False)

    @property
    def response_model(self) -> type[BaseModel]:
        return self._contract.response_model

    @property
    def agent_response_model(self) -> type[BaseModel]:
        return self._contract.agent_response_model

@dataclass(frozen=True)
class RestoredGlobalCandidate:
    """通用 checkpoint 复验后交回 executor 的完整 Global 候选。"""

    response: GlobalGroundingCandidateResponse
    acceptance: dict[str, Any]
    revision: int
    turn_kind: str


@dataclass(frozen=True)
class GlobalCandidateEvaluation:
    """一份尚未提交的强类型候选及代码 HARD 结论。

    ``candidate`` 带 ``settle=False`` 的 provisional provenance，只供同 schema 判定；
    它绝不能直接交给 ``ArtifactStore``。终态必须走 :func:`finalize_global_candidate`，
    由那里一次结算完整 usage、重判并选择 formal 或 ``.INVALID``。
    """

    candidate: GlobalGrounding
    hard_hits: tuple[str, ...]
    feedback: tuple[GlobalCandidateFeedback, ...]
    source_attempt_id: str | None
    requested_model: str
    adopted_models: tuple[str, ...]
    response_sha256: str
    _response: GlobalGroundingCandidateResponse = field(repr=False)
    _evidence_locator_failures: tuple[tuple[str, int, str, str], ...] = field(
        repr=False)
    _evidence_locator_corrections: tuple[EvidenceLocatorCorrection, ...] = field(
        repr=False)

    @property
    def passed(self) -> bool:
        return not self.hard_hits

    def feedback_rows(self) -> list[dict[str, object]]:
        """交给 agent resume 的 JSON-safe 行；不含 artifact 路径或来源账。"""
        return [row.to_dict() for row in self.feedback]


@dataclass(frozen=True)
class BlueprintCandidateFeedback:
    """整书代码 HARD 对逐章 executor 的稳定反馈投影。"""

    rule: str
    item: str
    requirement: str
    chapters: tuple[int, ...]
    findings: tuple[GlobalCandidateFinding, ...]

    def to_dict(self) -> dict[str, object]:
        return {
            'rule': self.rule,
            'item': self.item,
            'requirement': self.requirement,
            'chapters': list(self.chapters),
            'findings': [finding.to_dict() for finding in self.findings],
        }


@dataclass(frozen=True)
class BlueprintCandidateEvaluation:
    """尚未提交的整书 blueprint 候选与唯一业务判据结论。

    每章 schema/dynamic validator 已在独立请求 client 内完成；这里再按正式 compile
    结果跑联合 HARD。``candidate`` 的 provenance 使用 ``settle=False``，只能用于
    判定与反馈，终态必须走 :func:`finalize_blueprint_candidate`。
    """

    candidate: ChapterBlueprints
    hard_hits: tuple[str, ...]
    feedback: tuple[BlueprintCandidateFeedback, ...]
    purposes: tuple[tuple[str, int | None], ...]
    accepted_attempts: tuple[tuple[str, str | None], ...]
    requested_model: str
    adopted_models: tuple[str, ...]
    error_count: int
    _batch_sha256: str = field(repr=False)
    _main_text_sha256: str = field(repr=False)
    _character_projection_sha256: str = field(repr=False)

    @property
    def passed(self) -> bool:
        return not self.hard_hits

    def feedback_rows(self, chapter: int | None = None) \
            -> list[dict[str, object]]:
        """返回可交回 executor 的 JSON-safe 行；书级结论发给每个目标 session。"""
        return [
            row.to_dict() for row in self.feedback
            if chapter is None or not row.chapters or chapter in row.chapters
        ]


# ─────────────────────────────────────────────────────────────────────────────
# 一、上游与判定 —— **唯一落点**
# ─────────────────────────────────────────────────────────────────────────────

class _Both:
    """两份 stage2 产物 + 正文。**装载与判定分开**，几条路共用这一份。

    ⚠ 三份都**允许缺**，缺了不抛：`upstream_missing` 那一条 HARD 判据就是为此存在的，
    而 `checks.check()` 会让依赖那一份的判据落 `unver`。
    **在这里抛掉，报告就永远说不出「缺了哪一份」**——那正是原型
    「什么都没查却打 PASS」的反面错误。
    """

    def __init__(self, store: ArtifactStore) -> None:
        self.global_g = _read_raw(store, GlobalGrounding, GLOBAL_REL)
        self.blueprints = _read_raw(store, ChapterBlueprints, BLUEPRINT_REL)
        self.main_text = _read_main_text(store)
        self.characters = _read_characters(store)

    def judge(self) -> list[Outcome]:
        """跑全部注册判据。**唯一判定落点**——落盘闸门与复检共用它。"""
        return checks.check(global_g=self.global_g, blueprints=self.blueprints,
                            main_text=self.main_text, characters=self.characters)


def _read_raw(store: ArtifactStore, model: type, rel: str) -> dict[str, Any] | None:
    """读一份产物的**原始 dict**（顺带过一遍载体）。

    ⚠⚠ **返回 dict 不返回模型**：判据吃 dict（`checks.py` 文件头那条），
    一旦跑在已过 Pydantic 的对象上，「必填齐全」那一类在结构上构造不出会红的样本。
    ⚠ 载体拒收时**照常抛**：那份产物存在的意义就是提醒人这条路坏了，
    不许静默降级成一份「PASS」。
    """
    invalid = paths.invalid_of(rel)
    # 有问题稿说明它是更新的一次尝试；复检必须看最新问题稿，不能被仍在盘上的旧正式稿遮住。
    target = invalid if store.exists(invalid) else rel
    if not store.exists(target):
        return None
    _art, raw = (grounding_io.read_own(store, target) if model is GlobalGrounding
                 else store.read_own(model, target))
    return raw


def _older_version_error(exc: ArtifactUnreadable) -> ArtifactVersionError | None:
    """只识别“盘上确实是低版本”这一种不可读；其余坏载体继续关闭式失败。"""
    cause = exc.__cause__
    if isinstance(cause, ArtifactVersionError) and cause.is_older_than_current:
        return cause
    return None


def _read_characters(store: ArtifactStore) -> dict[str, Any] | None:
    from contracts.artifacts import STAGE2_CHARACTERS
    if not store.exists(STAGE2_CHARACTERS):
        return None
    return grounding_io.read_registry(store, own=True).model_dump(mode='json', by_alias=True)


def _read_main_text(store: ArtifactStore) -> list[dict[str, Any]] | None:
    if not store.exists(MAIN_TEXT):
        return None
    art = store.read(MainText, MAIN_TEXT)
    if store.ctx.node == 'stage2.global':
        # 生成、免费修订和复验共同保留实际输入边；遗漏会使未变的源文案
        # 审阅身份变化，并把局部订正错误扩大成 Blueprint 全量复审。
        registry = grounding_io.read_registry(store)
        hard = _hard_hits(checks.check_characters(
            registry.model_dump(mode='json', by_alias=True), art.model_dump(mode='json')))
        if hard:
            raise BlockingFindingsUnresolved(
                '人物正式登记仍有 HARD；先由 stage2.characters 修复，不购买知识重试',
                rules=hard, hint='stage2.characters --revalidate --actor system:invalid-revalidate --reason <audit>（0 LLM）')
    return [s.model_dump(by_alias=True) for s in art.root]


def _require_blueprint_character_projection(
    store: ArtifactStore,
) -> tuple[BlueprintCharacterProjection, dict[str, Any]]:
    """Blueprint 的唯一称名输入；缺/坏/stale 都关闭并指向 0-LLM 恢复。"""
    recovery = (
        'stage2.characters --revalidate --actor system:invalid-revalidate --reason <audit>（0 LLM）'
        '从当前人物登记与 MainText 重建该投影')
    projection_exists = store.exists(BLUEPRINT_CHARACTER_REL)
    try:
        freshness = store.freshness(BLUEPRINT_CHARACTER_REL)
    except UpstreamMissing:
        raise UpstreamMissing(
            f'{BLUEPRINT_CHARACTER_REL} 不在盘上；{recovery}') from None
    except ArtifactUnreadable as exc:
        raise ProvenanceRefused(
            f'{BLUEPRINT_CHARACTER_REL} 载体/版本信封不可读；{recovery}'
        ) from exc
    if freshness.reason == 'global_naming_blocked':
        raise ProvenanceRefused(
            '最新人物登记仍有会改变 Blueprint 称名闭集的 HARD；'
            '先走 stage2.characters typed edit / '
            '`--revalidate --actor system:invalid-revalidate --reason <audit>`（0 LLM）。'
            '在称名面收口前不得启动付费 Blueprint')
    if not projection_exists:
        raise UpstreamMissing(
            f'{BLUEPRINT_CHARACTER_REL} 不在盘上；{recovery}')
    if freshness.state != 'fresh':
        raise ProvenanceRefused(
            f'{BLUEPRINT_CHARACTER_REL} 无法证明绑定当前最新人物登记 + '
            f'MainText（{freshness.state}/{freshness.reason}）；{recovery}')
    try:
        artifact = store.read(
            BlueprintCharacterProjection, BLUEPRINT_CHARACTER_REL)
    except ArtifactUnreadable as exc:
        raise ProvenanceRefused(
            f'{BLUEPRINT_CHARACTER_REL} 载体不可读；{recovery}') from exc
    return artifact, artifact.model_dump(by_alias=True, mode='json')


#: 两份产物的记账键 → 报告的记账节。⚠ **只放产物里真有的键**：
#: 键不在 ⇒ 报告打「未记录」，那与「记了 0 次」不是一回事（`Q-2` 的三态）。
_GLOBAL_KEYS = ('evidence_locator_failures',)


def _accounting_of(both: _Both) -> dict[str, Any]:
    """Global 的编译失败账；Blueprint 使用关闭式响应验证与 durable 恢复账。"""
    source = (both.global_g or {}).get('_provenance') or {}
    return {key: source[key] for key in _GLOBAL_KEYS if key in source}



def _report(ctx: RunContext, store: ArtifactStore, both: _Both,
            outcomes: list[Outcome], *, judged_rel: str | None = None,
            invalid_rel: str = _INVALID) -> CheckReport:
    """⚠ **新鲜度判的是「这份报告在说哪个文件」**，而那不总是正式产物：
    第一次付费跑就被闸门拦下时，盘上**只有 `.INVALID`**（正式稿本来还不存在），
    此时拿正式路径去判新鲜度会抛「不在盘上」——于是 0 成本的提升路
    **在最需要它的那一刻**跑不动。（这条 bug 是 stage7 上撞出来的，本层从一开始照着写。）
    """
    judged = judged_rel or (
        _INVALID if store.exists(_INVALID) else BLUEPRINT_REL)
    source = both.global_g if ctx.node == 'stage2.global' else both.blueprints
    prov = (source or {}).get('_provenance') or {}
    # global 是本层的上游半步，按正常顺序执行时 blueprint 必然还不存在。
    # 落盘闸门已经用同一作用域忽略了 upstream_missing；报告若仍把它算 HARD，
    # 就会出现“正式 global 已写成功但命令退出失败”的自相矛盾。
    reported = [
        Outcome(
            rule='upstream_missing',
            unver_why=(
                'stage2.global 是本层的上游半步：本次所需的 main_text 与 global '
                '都已就绪；blueprint 要到下一节点才会生成，因此此刻不把“整层两份产物齐全”'
                '误判成 global 落盘失败'
            ),
            scanned='stage1/main_text.json + stage2/global_grounding.json',
        )
        if ctx.node == 'stage2.global' and outcome.rule == 'upstream_missing'
        else outcome
        for outcome in outcomes
    ]
    return build(sid=ctx.sid, node=ctx.node, freshness=store.freshness(judged),
                 outcomes=reported, accounting=_accounting_of(both),
                 invalid=store.exists(invalid_rel),
                 revalidated=prov.get('revalidated'), origin=prov.get('origin'))


# ─────────────────────────────────────────────────────────────────────────────
# 二、0 成本的那几条
# ─────────────────────────────────────────────────────────────────────────────

def run_check_only(ctx: RunContext) -> CheckReport:
    """`--check-only`：**读产物不重算**，两份一起读。

    ⚠ 正因为不重算，报告第一节报的新鲜度就是全部的来源交代：
    产物过期时它报出来的一切都是**旧结论**。
    ⚠ **一份都没有时照常出报告**：`upstream_missing` 会红，其余落 `unver`。
    抛掉的话「什么都没查」这件事就只剩一段栈，而它本该是报告上最显眼的一行。
    """
    store = ArtifactStore(ctx)
    both = _Both(store)
    if both.global_g is None and both.blueprints is None:
        raise UpstreamMissing(
            f'{GLOBAL_REL} 与 {BLUEPRINT_REL} **两份都不在盘上** —— '
            f'先跑 stage2。⚠ 与「缺其中一份」分开抛：缺一份时照常出报告'
            f'（`upstream_missing` 红、其余落 `unver`），而两份都没有时'
            f'连「这本书跑到哪一步了」都无从谈起')
    return _report(ctx, store, both, both.judge())


@dataclass(frozen=True)
class _GlobalRevalidationEvaluation:
    """Global INVALID 按 revalidate 的唯一恢复/归一/判定尺子得到的纯结果。"""

    main_text: list[dict[str, Any]]
    rebuilt: GlobalGrounding
    accounting: Any
    outcomes: tuple[Outcome, ...]
    hard_hits: tuple[str, ...]


def _prepare_global_revalidation_evaluation(
    ctx: RunContext, store: ArtifactStore, *, actor: str, reason: str,
) -> _GlobalRevalidationEvaluation:
    """纯计算 ``run_revalidate`` 候选；probe 与执行不得各造一把 HARD 尺子。"""
    main_text = _read_main_text(store)
    if main_text is None:
        raise UpstreamMissing(f'{MAIN_TEXT} 不在盘上——无法按当前正文重判问题稿')
    if not store.exists(_GLOBAL_INVALID):
        raise UpstreamMissing(
            f'{_GLOBAL_INVALID} 不在盘上——没有被拦下的全局问题稿要提升')
    baseline = grounding_io.revalidation_baseline(store, actor=actor, reason=reason)
    artifact, raw = grounding_io.read_own(store, _GLOBAL_INVALID)
    data = copy.deepcopy(raw)
    data.pop('_provenance', None)
    data, accounting = normalize_global(data, main_text)
    rebuilt = GlobalGrounding.model_validate({
        **data,
        '_provenance': GlobalProvenance.model_validate({
            **artifact.provenance.model_dump(), **accounting,
        }),
    })
    candidate = rebuilt.model_dump(by_alias=True)
    outcomes = tuple(_judge_candidate(candidate, None, main_text, characters=_read_characters(store)))
    hard_hits = tuple(_hard_hits(
        list(outcomes), ignore={'upstream_missing'}))
    return _GlobalRevalidationEvaluation(
        main_text=main_text,
        rebuilt=rebuilt,
        accounting=accounting,
        outcomes=outcomes,
        hard_hits=hard_hits,
    )


def run_revalidate(
    ctx: RunContext, *, actor: str, reason: str,
) -> tuple[bool, CheckReport]:
    """按当前判据重判问题稿；合格即从 `.INVALID` 零成本提升。

    落盘闸门由本层自己加，因此提升路也是闸门闭环的一部分。提升只继承原来源账，
    不构造新 prompt/model/usage；唯一变化由 `ArtifactStore.promote_invalid` 记为
    `revalidated=true`。
    """
    store = ArtifactStore(ctx)
    main_text = _read_main_text(store)
    if main_text is None:
        raise UpstreamMissing(f'{MAIN_TEXT} 不在盘上——无法按当前正文重判问题稿')

    if ctx.node == 'stage2.global':
        from novel.llm import stage2_global_runs

        # INVALID→formal 同样会改 journal 登记的四个 touched path；必须与
        # agent prepared 尾窗共用同书所有权，否则一次零成本提升就会让原 run
        # 永久落入 third-party drift，既补不了尾也开不了新 producer。
        with stage2_global_runs.mutation_ownership(ctx):
            resumed = _resume_global_revalidation_commit(
                ctx, store, actor=actor, reason=reason)
            if resumed is not None:
                return resumed
            evaluated = _prepare_global_revalidation_evaluation(
                ctx, store, actor=actor, reason=reason)
            rebuilt = evaluated.rebuilt
            accounting = evaluated.accounting
            outcomes = list(evaluated.outcomes)
            identity = _global_revalidation_commit_identity(ctx, store)
            if evaluated.hard_hits:
                if identity is not None:
                    invalid, invalid_sha256 = grounding_io.read_own_with_sha256(store, _GLOBAL_INVALID, require_current=True)
                    candidate = invalid.model_dump(mode='json', by_alias=True)
                    candidate.pop('_provenance', None)
                    _write_global_edit_failure(
                        store,
                        failure_kind='ordinary_hard_gate',
                        failed_edit_sha256=identity.edit_sha256,
                        formal_sha256=identity.formal_sha256,
                        expected_source_sha256=identity.expected_source_sha256,
                        global_review_workpack_sha256=(
                            identity.global_review_workpack_sha256),
                        candidate_business_sha256=_canonical_sha256(candidate),
                        invalid_sha256=invalid_sha256,
                        hard_hits=list(evaluated.hard_hits),
                        reason_code='revalidation_hard_gate',
                        reason_summary=(
                            'Global INVALID 按当前 revalidate 尺子仍被 HARD 拦截；rules='
                            + ','.join(evaluated.hard_hits))[:512],
                    )
                both = _Both(store)
                return False, _report(
                    ctx, store, both, outcomes,
                    judged_rel=_GLOBAL_INVALID, invalid_rel=_GLOBAL_INVALID)
            promoted = grounding_io.prepared_invalid_promotion(
                store, actor=actor, reason=reason,
                artifact_override=rebuilt)
            journal = _prepare_reviewed_edit_commit(
                store,
                artifact=promoted,
                review=_retired_review_surface(promoted),
                main_text=evaluated.main_text,
                canonical_edit_sha256=(
                    None if identity is None else identity.edit_sha256),
                formal_sha256=(
                    store.full_sha256_or_none(GLOBAL_REL)
                    if identity is None else identity.formal_sha256),
                expected_source_sha256=(
                    None if identity is None
                    else identity.expected_source_sha256),
                global_review_workpack_sha256=(
                    None if identity is None
                    else identity.global_review_workpack_sha256),
                submitted_transport_identity='book:' + _GLOBAL_INVALID,
                action_kind='revalidate',
                revalidation_failure_sha256=(
                    None if identity is None else identity.failure_sha256),
            )
            _reconcile_reviewed_edit_commit(store, journal)
            _note_evidence_locator_corrections(
                ctx, store, accounting.evidence_locator_corrections,
                rel=GLOBAL_REL)
            both = _Both(store)
            return True, _report(
                ctx, store, both, both.judge(),
                judged_rel=GLOBAL_REL, invalid_rel=_GLOBAL_INVALID)

    if ctx.node != 'stage2.blueprint':
        raise TerminalState(f'{ctx.node} 不是 stage2 的可提升节点')
    from novel.stages.stage2.plot_recovery import revalidate
    return revalidate(ctx, store, main_text, actor=actor, reason=reason)


def build_prompts(
    ctx: RunContext, *, chapters: tuple[int, ...] = (),
    character_projection: BlueprintCharacterProjection | None = None,
    main_text: list[dict[str, Any]] | None = None,
    global_projections: dict[str, list[dict[str, Any]]] | None = None,
) -> list[tuple[int | None, str, str]]:
    """返回本次会发出的真实请求；blueprint 始终是一章一条。"""
    store = ArtifactStore(ctx)
    mt = main_text if main_text is not None else _read_main_text(store)
    if mt is None:
        raise UpstreamMissing(f'{MAIN_TEXT} 不在盘上——先跑 stage1')
    if ctx.node == 'stage2.global':
        spec = _active_global_spec()
        system, user, _candidates = build_global_request(
            ctx.title, mt, spec,
            character_reference=grounding_io.knowledge_reference(grounding_io.read_registry(store)))
        return [(None, system, user)]

    ordinals = tuple(int(s.get('chapter_ordinal') or 0) for s in mt)
    targets = tuple(chapters) or ordinals
    missing = sorted(set(targets) - set(ordinals))
    if missing:
        raise TerminalState(f'--chapters 不在正文阅读序里：{missing}')
    projection = character_projection
    if projection is None:
        projection, _projection_raw = \
            _require_blueprint_character_projection(store)
    character_inventory = blueprint_inventory_from_projection(projection)
    spec = _active_spec()
    from novel.consumption import plot_global_projection
    from novel.stages.stage2.plot_prompts import build_plot_user
    if global_projections is None:
        knowledge = store.read(KnowledgeGrounding, GLOBAL_REL).model_dump(
            mode='json', by_alias=True)
        global_projections = {str(o): plot_global_projection(knowledge, o) for o in targets}
    sources = {int(row['chapter_ordinal']): str(row['text']) for row in mt}
    visible = character_inventory['visible_character_refs']
    return [(o, spec.system, build_plot_user(
        ctx.title, sources[o], o, character_refs=visible.get(o, {}),
        character_bindings=character_inventory['character_bindings'].get(o, {}),
        global_projection=global_projections[str(o)])) for o in targets]


def _active_spec():
    """本次运行选用的 blueprint 契约（整跑用；修复按基线架构另选）。"""
    from novel.stages.stage2.contract import active_contract
    return active_contract()


def _active_global_spec() -> GlobalEvidenceContractSpec:
    """本次运行选用的 global evidence wire 契约。"""
    from novel.stages.stage2.global_contract import active_contract
    return active_contract()


def build_prompt(ctx: RunContext, *, chapters: tuple[int, ...] = (),
                 review_token: str | None = None) -> tuple[str, str]:
    """`--dry-run`：把这次要发出的第一条 ``(system, user)`` 原样交出来。

    ⚠ 它与付费路**共用同一个构造入口**（`R-11.8`）——否则「预览里看到的」与
    「真发出去的」是两件事，而那种分家没有任何东西会报错。
    global 的 `user` 带全书原文；blueprint 带当前章全文、必要共享主体与称名投影。
    后续章节的持续状态在已验证前章之后编译进真实请求。
    预览与付费路共用同一个构造入口，因此这里看到的边界就是真实模型可见边界。
    """
    if ctx.node == 'stage2.blueprint' and (chapters or review_token is not None):
        from novel.stages.stage2.plot_repair import build_prompt as revision_prompt
        return revision_prompt(ctx, chapters=chapters, review_token=review_token)
    _ordinal, system, user = build_prompts(ctx, chapters=chapters)[0]
    return system, user


def _chapter_index(main_text: list[dict[str, Any]]) -> str:
    """章节索引；一切对章的引用只用阅读序章号。"""
    return '\n'.join(
        f'阅读序第{s.get("chapter_ordinal")}章 {len(s.get("text") or "")} 字'
        for s in main_text
    )


def _observe_indistinguishable_refs(
    ctx: RunContext, dropped: dict[int, list[list[str]]],
) -> None:
    """报告同名字面候选；保留实体，交由剧情 owner 按原文消歧。"""
    if not dropped:
        return
    ctx.observe(
        'character_refs_indistinguishable',
        chapters=sorted(dropped),
        groups={str(ordinal): groups for ordinal, groups in sorted(dropped.items())},
        ambiguous_ids=sum(len(cids) for groups in dropped.values()
                        for cids in groups),
    )


def blueprint_context(title: str, main_text: list[dict[str, Any]],
                      architecture: str = BLUEPRINT_ARCHITECTURE) -> dict[str, Any]:
    """保留统一 producer 接口，实际材料身份归 provenance，不记录未下发的窗口。"""
    return {
        'architecture': architecture,
        'source_format': 'numbered-paragraphs-v1',
        'global_projection_contract': 'plot-global-scope-v2',
    }


def build_global_user(
    title: str,
    main_text: list[dict[str, Any]],
    *,
    evidence_candidates: GlobalEvidenceCandidateTable,
    character_reference: dict[str, dict[str, Any]],
) -> str:
    """全书 user；段标记与 lookup 来自同一张关闭候选表。"""
    body = evidence_candidates.render()
    return '\n\n'.join([
        f'《{title}》',
        '── 冻结人物引用（只读）──\n' + json.dumps(
            {f'c{number}': character_reference[cid] for number, cid in enumerate(sorted(character_reference), 1)},
            ensure_ascii=False, sort_keys=True, separators=(',', ':')),
        '── 章节索引（一切对章的引用用阅读序章号）──\n' + _chapter_index(main_text),
        '── 全书证据候选（不可变原文；行首 ID 不属于正文）──\n' + body,
    ])


def build_global_request(
    title: str,
    main_text: list[dict[str, Any]],
    spec: GlobalEvidenceContractSpec,
    *,
    character_reference: dict[str, dict[str, Any]],
    agent_envelope: bool = False,
) -> tuple[str, str, GlobalEvidenceCandidateTable]:
    """从同一份 MainText 构造 system/user/lookup 三元组。"""
    candidates = GlobalEvidenceCandidateTable.build(main_text)
    return (
        build_global_agent_system(spec) if agent_envelope else spec.system,
        build_global_user(
            title, main_text, evidence_candidates=candidates,
            character_reference=character_reference),
        candidates,
    )


def build_global_agent_system(
    spec: GlobalEvidenceContractSpec | None = None,
) -> str:
    """原生 schema 承担结构；只下发必要知识说明，不再重复完整示例和静态窄修信封。"""
    spec = spec or _active_global_spec()
    return (spec.instruction + '\n\n响应固定为 payload.kind=full_candidate，'
            'payload.candidate 提交本次完整必要知识；只使用声明的结构化出口。')


# ─────────────────────────────────────────────────────────────────────────────
# 三、生成、归一、闸门与落盘
# ─────────────────────────────────────────────────────────────────────────────

def _hard_hits(outcomes: list[Outcome], *, ignore: set[str] | None = None) -> list[str]:
    """返回真正命中的 hard；unver/skipped 不是数据违约。"""
    ignored = ignore or set()
    return sorted(
        outcome.rule for outcome in outcomes
        if outcome.rule not in ignored
        and outcome.unver_why is None
        and outcome.findings
        and REGISTRY.get(outcome.rule).sev == 'hard'
    )


def _scope_split(
    raw: list[dict[str, Any]], ordinal: int, batch: int
) -> tuple[dict[str, Any] | None, str | None]:
    """一章调用只接纳一个目标章；其余响应留结构化范围账。"""
    produced = [bp.get('chapter_ordinal') for bp in raw]
    matching = [bp for bp in raw if bp.get('chapter_ordinal') == ordinal]
    violation = None
    if produced != [ordinal]:
        violation = BlueprintScopeViolation(
            batch=batch,
            requested=[ordinal],
            produced=produced,
            discarded_chapters=[o for o in produced if o != ordinal],
            duplicate_target=max(0, len(matching) - 1),
        ).model_dump_json()
    return (matching[0] if matching else None), violation


def _judge_candidate(
    global_g: dict[str, Any] | None,
    blueprints: dict[str, Any] | None,
    main_text: list[dict[str, Any]],
    *, characters: dict[str, Any] | None = None,
) -> list[Outcome]:
    return checks.check(
        global_g=global_g, blueprints=blueprints, main_text=main_text, characters=characters)


def _canonical_sha256(value: object) -> str:
    """候选/冻结 guard 的全宽身份；10 位 provenance 指纹不承担提交等值证明。"""
    payload = json.dumps(
        value, ensure_ascii=False, sort_keys=True,
        separators=(',', ':'), allow_nan=False,
    ).encode('utf-8')
    return hashlib.sha256(payload).hexdigest()


def _retired_review_surface(artifact: GlobalGrounding) -> GlobalReview:
    """Stage2 语义审阅退役后的 journal 形状兼容格。

    提交 journal 的 schema 仍要求 ``global_review``/``review_sha256`` 两格；
    本函数只填充这一格：恒为空项集，``source_sha256`` 绑定候选业务身份。
    它**不再写任何审阅文件**，也不表达任何语义接受；语义未审由报告
    ``unver`` 行与交付披露表达。
    """
    payload = artifact.model_dump(mode='json', by_alias=True)
    payload.pop('_provenance', None)
    return GlobalReview(source_sha256=_canonical_sha256(payload), items=[])


def _retired_review_sha256(review: GlobalReview) -> str:
    """journal 内嵌审阅格的稳定身份；不再对应任何盘上文件。"""
    return _canonical_sha256(review.model_dump(mode='json', by_alias=True))


def _refuse_reviewed_edit(edit: GlobalEdit) -> None:
    """带 ``post_edit_review`` 的编辑只由已退役的审阅线生产；提交一律关闭。"""
    if edit.post_edit_review is not None:
        raise TerminalState(
            'Stage2 语义审阅已退役：不再接受带 post_edit_review 的 GlobalEdit；'
            '请去掉该格，按普通 typed 编辑重新提交')


def global_response_sha256(
    response: GlobalGroundingCandidateResponse,
) -> str:
    """hash-bound patch 的主体：未归一、无 provenance 的强类型 raw response。"""
    return _canonical_sha256(response.model_dump(by_alias=True, mode='json'))


def _blueprint_hard_hits(outcomes: list[Outcome]) -> list[str]:
    """Blueprint 自身落盘只拦它所有的 R-3 业务违约。

    联合报告仍然保留全部 Global + Blueprint outcomes；这里只分流
    producer landing gate。否则一份 schema-valid global 因 role/证据/时间线
    HARD 落在 `.INVALID` 时，它已能完整提供称名投影，却会让正式
    Blueprint 被另一个 producer 的问题连坐拒写。Stage4 前的联合闸仍用
    :func:`_hard_hits` 检查全集。
    """
    return sorted(
        outcome.rule for outcome in outcomes
        if outcome.rule != 'upstream_missing'
        and outcome.unver_why is None
        and outcome.findings
        and REGISTRY.get(outcome.rule).sev == 'hard'
        and REGISTRY.get(outcome.rule).requirement.startswith('R-3.')
    )


def global_full_candidate_feedback_rows(
    evaluation: GlobalCandidateEvaluation,
) -> list[dict[str, object]]:
    """非局部 HARD 明确要求信封的完整候选分支。"""
    rows = evaluation.feedback_rows()
    contract = {
        'kind': 'full_candidate',
        'instruction': '返回完整 full_candidate。',
    }
    for row in rows:
        row['repair_contract'] = dict(contract)
    return rows


def _compile_global_response(
    prepared: GlobalCandidateContext,
    response: GlobalGroundingCandidateResponse,
) -> dict[str, Any]:
    """把 wire response 收到既有正式前形；candidate ID 绝不穿过此边界。"""
    if not isinstance(response, GlobalGroundingCandidateResponse):
        raise ValueError('stage2.global 只接受 必要知识候选响应')
    compiled = grounding_io.compile_response(
        response, prepared._evidence_candidates, prepared._characters, id_map=prepared._global_id_map,
        reserved_ids=prepared._global_retired_ids)
    return grounding_io.compiled_view_payload(compiled, prepared._characters)


def global_response_validator(
    prepared: GlobalCandidateContext,
) -> Callable[[BaseModel, Any], str | None]:
    """动态闭集成员闸；未知 ID 在 accepted/落盘之前回到同次响应修复。"""
    def validate(response: BaseModel, _wire: Any) -> str | None:
        try:
            _compile_global_response(prepared, response)  # type: ignore[arg-type]
        except (TypeError, ValueError) as exc:
            return str(exc)
        return None

    return validate


def global_full_candidate_validator(
    prepared: GlobalCandidateContext,
) -> Callable[[BaseModel, Any], str | None]:
    """agent 首轮/完整重写轮的分支 + candidate membership 联合闸。"""
    def validate(response: BaseModel, _wire: Any) -> str | None:
        if not isinstance(response, GlobalCandidateAgentTurnResponse):
            return '本次只接受 必要知识 agent 信封'
        if not isinstance(response.payload, GlobalCandidateFullTurn):
            return '本轮只接受 full_candidate'
        candidate = response.payload.candidate
        try:
            _compile_global_response(prepared, candidate)
        except (TypeError, ValueError) as exc:
            return str(exc)
        return None

    return validate


def ensure_global_agent_run(
        ctx: RunContext, prepared: GlobalCandidateContext,
) -> None:
    """首个 wire 前冻结 Global subject/prompt/schema/profile/code。"""
    from novel.llm import stage2_global_runs

    assert_global_main_text_current(ctx, prepared)
    # foreign prepared 代表另一 run 已写正式 bundle、只差 adoption/committed 尾窗。
    # 必须在任何 provider turn 前拒绝新 run，不能先花钱再到 finalize 才发现所有权冲突。
    with stage2_global_runs.commit_ownership(ctx.novel_dir):
        from novel.stages.stage2.analyze_recovery import refuse_pending_recovery
        refuse_pending_recovery(ctx.novel_dir)
        stage2_global_runs.refuse_foreign_commit_reservations(ctx)
    stage2_global_runs.ensure_run(
        ctx,
        input_sha256=prepared.input_sha256,
        system=prepared.system,
        user=prepared.user,
        response_model=prepared.agent_response_model,
        max_tokens=GLOBAL_MAX_TOKENS,
    )


def _global_checkpoint_state(
        prepared: GlobalCandidateContext, response: GlobalGroundingCandidateResponse,
        *, turn_kind: str,
) -> dict[str, object]:
    return {
        'kind': 'stage2-knowledge-candidate-v3', 'turn_kind': turn_kind,
        'main_text_sha256': prepared.main_text_sha256,
        'evidence_candidates_sha256': prepared.evidence_candidates_sha256,
        'candidate': response.model_dump(by_alias=True, mode='json'),
        'candidate_sha256': global_response_sha256(response), 'custody': None,
    }


def write_global_agent_checkpoint(
        ctx: RunContext, source: _GlobalResponseSource,
        prepared: GlobalCandidateContext, turn: BaseModel,
        response: GlobalGroundingCandidateResponse,
) -> None:
    """accepted stdout 到手先写不可变知识 checkpoint，再做业务提交。"""
    from novel.llm import stage2_global_runs
    assert_global_main_text_current(ctx, prepared)
    if (not isinstance(turn, GlobalCandidateAgentTurnResponse)
            or not isinstance(turn.payload, GlobalCandidateFullTurn)
            or turn.payload.candidate != response):
        raise ProvenanceRefused('知识 checkpoint 必须与本次 full_candidate 回答完全一致')
    checkpoint = source.accepted_checkpoint(ctx.node)
    turn_index = checkpoint.get('accepted_turn_index')
    if not isinstance(turn_index, int) or isinstance(turn_index, bool) or turn_index < 1:
        raise ProvenanceRefused('知识 accepted checkpoint 缺正整数 turn index')
    stage2_global_runs.write_checkpoint(
        ctx, input_sha256=prepared.input_sha256, system=prepared.system, user=prepared.user,
        response_model=prepared.agent_response_model, max_tokens=GLOBAL_MAX_TOKENS,
        response_payload=turn.model_dump(by_alias=True, mode='json'),
        acceptance=checkpoint, revision_key=f'candidate:{turn_index}',
        state_payload=_global_checkpoint_state(prepared, response, turn_kind='full_candidate'))


def _global_validator_custody_chain(
        ctx: RunContext, prepared: GlobalCandidateContext,
) -> str:
    return _canonical_sha256({
        'kind': 'stage2-knowledge-validator-custody-v2',
        'run_id': ctx.run_id,
        'sid': ctx.sid,
        'node': ctx.node,
        'code': ctx.code,
        'input_sha256': prepared.input_sha256,
        'main_text_sha256': prepared.main_text_sha256,
        'evidence_candidates_sha256': prepared.evidence_candidates_sha256,
        'system_sha256': _canonical_sha256(prepared.system),
        'user_sha256': _canonical_sha256(prepared.user),
        'response_model': prepared.agent_response_model.__name__,
    })


def _assert_global_custody_durable_state(ctx: RunContext, prepared: GlobalCandidateContext) -> None:
    """最后一份动态拒稿只能承接同一知识契约下的 durable 状态。"""
    from novel.llm import stage2_global_runs
    durable = stage2_global_runs.restore_checkpoint(
        ctx, input_sha256=prepared.input_sha256, system=prepared.system, user=prepared.user,
        response_model=prepared.agent_response_model, max_tokens=GLOBAL_MAX_TOKENS)
    if durable is not None:
        _validate_global_checkpoint_state(prepared, durable.state_payload)


def custody_global_validator_rejection(
        ctx: RunContext, source: Any, prepared: GlobalCandidateContext,
) -> str | None:
    """封存固定知识信封的最后一份 typed 动态拒稿，不购买第二份回答。"""
    from novel.llm import response_repair

    rejected = source.last_json_rejection(ctx.node)
    if rejected is None:
        return None
    _assert_global_custody_durable_state(ctx, prepared)
    assert_global_main_text_current(ctx, prepared)
    attempt_id = rejected.get('attempt_id')
    content = rejected.get('content')
    typed_payload = rejected.get('payload')
    if (not isinstance(attempt_id, str) or not isinstance(content, str)
            or not isinstance(typed_payload, dict)):
        raise ProvenanceRefused('Global validator rejection custody 缺 typed stdout 身份')
    acceptance = source.accepted_checkpoint(ctx.node)
    if acceptance.get('attempt_id') != attempt_id:
        raise ProvenanceRefused('Global validator rejection 与 accepted checkpoint 漂移')
    chain = _global_validator_custody_chain(ctx, prepared)
    why = str(rejected.get('why') or 'candidate membership validator rejected')
    detail = rejected.get('detail')
    safe_detail = [dict(row) for row in detail] if isinstance(detail, list) else []
    failure = response_repair.failure_signature(
        request_fingerprint=str(acceptance.get('request_fingerprint') or ''),
        model=str(rejected.get('model') or source.model),
        reasoning_effort=str(ctx.executor_profile.reasoning_effort),
        thinking=False,
        request_mode=str(acceptance.get('request_mode') or 'original'),
        finish_reason=None,
        gate='validator',
        reject_detail=safe_detail,
        why=why,
    )
    rel, error = response_repair.keep_rejection(
        ctx.novel_dir,
        chain_id=chain,
        attempt_id=attempt_id,
        original=content,
        candidate=typed_payload,
        payload={
            'sid': ctx.sid,
            'node': ctx.node,
            'purpose': ctx.node,
            'run_id': ctx.run_id,
            'code': ctx.code,
            'request_fingerprint': acceptance.get('request_fingerprint'),
            'attempt_ids': [
                row.get('attempt_id')
                for row in acceptance.get('attempt_links', [])
                if isinstance(row, dict)],
            'model': rejected.get('model') or source.model,
            'model_index': 0,
            'rung_index': 0,
            'sample': acceptance.get('accepted_turn_index'),
            'source': 'wire',
            'request_mode': acceptance.get('request_mode'),
            'recovery_from_attempts': acceptance.get('recovery_from_attempts'),
            'would_resample': False,
            'failure_signature': failure,
            'next_recovery_action': 'human_typed_replacement',
            'gate': 'validator',
            'why': why[:4000],
            'reject_detail': safe_detail,
            'repair_eligible': True,
            'eligibility_reason': (
                'Stage2 Global 已有完整 typed envelope；人工 replacement 必须保持'
                '完整 full_candidate，并按当前 schema、关闭候选表与业务判据重验'),
            'response_model': prepared.agent_response_model.__name__,
            'required_top_level': required_top_level(
                prepared.agent_response_model),
            'response_schema': prepared.agent_response_model.model_json_schema(
                by_alias=True),
            'chapter': None,
            'candidate_subject_sha256': prepared.input_sha256,
            'main_text_sha256': prepared.main_text_sha256,
            'evidence_candidates_sha256': prepared.evidence_candidates_sha256,
            'expected_turn_kind': 'full_candidate',
            'acceptance': acceptance,
            'typed_payload': typed_payload,
            'response': {
                'content': content,
                'reasoning': None,
                'finish_reason': None,
                'usage': None,
                'model': rejected.get('model') or source.model,
                'sha256': hashlib.sha256(content.encode('utf-8')).hexdigest(),
            },
        },
    )
    ctx.ledger.note_response_repair(
        sid=ctx.sid, node=ctx.node, purpose=ctx.node,
        attempt_id=attempt_id,
        action='offered',
        decision='pending' if rel is not None else 'skipped',
        gate='validator',
        dump=rel,
        dump_error=error,
        model=str(rejected.get('model') or source.model),
        rung_index=0,
        sample=acceptance.get('accepted_turn_index'),
        chapter=None,
    )
    if rel is None:
        raise ProvenanceRefused(
            f'Global validator 拒稿已保留在内存但 custody 落盘失败：{error}')
    return rel


def _global_custody_expectation(rejection: dict[str, Any]) -> None:
    if (rejection.get('expected_turn_kind') != 'full_candidate'
            or any(key in rejection for key in (
                'base_candidate', 'base_candidate_sha256', 'accepted_role_patch_count'))):
        raise ProvenanceRefused('知识 custody 不是当前唯一 full_candidate 契约')


def _note_global_custody_terminal_outcome(
        ctx: RunContext, record: Any,
) -> None:
    """从 durable outcome 幂等补齐 Global custody 的无正文终态审计。

    outcome 比 ``llm_audit.jsonl`` 先落盘；进程若死在两者之间，下一次同 run
    必须从不可变 resolution/outcome 重建同一 ``repair_event_id``，不能让审计
    永久停在 offered/pending。这里重新读盘，不信调用者手里的旧 ``RepairRecord``
    快照；只登记身份、决定、闸与候选哈希，不复制 replacement、判词或正文。
    """
    from novel.llm import response_repair

    durable = response_repair.find_attempt(ctx.novel_dir, record.attempt_id)
    resolution = durable.resolution
    outcome = durable.outcome
    if not isinstance(resolution, dict) or not isinstance(outcome, dict):
        raise ProvenanceRefused(
            'Global custody terminal audit 缺 durable resolution/outcome')
    decision = outcome.get('decision')
    resolution_action = resolution.get('action')
    if decision == 'declined':
        if resolution_action != 'decline':
            raise ProvenanceRefused(
                'Global custody declined outcome 与 resolution action 漂移')
        audit_action = 'declined'
        candidate_sha256 = None
    elif decision in ('accepted', 'rejected'):
        if resolution_action not in ('submit', 'deterministic'):
            raise ProvenanceRefused(
                'Global custody accepted/rejected outcome 缺 typed replacement')
        audit_action = (
            'deterministic' if resolution_action == 'deterministic' else 'agent')
        candidate_sha256 = resolution.get('candidate_payload_sha256')
        if not isinstance(candidate_sha256, str):
            raise ProvenanceRefused(
                'Global custody typed replacement 缺 candidate payload SHA')
    else:
        raise ProvenanceRefused(
            f'Global custody terminal outcome decision 非法：{decision!r}')
    gate = outcome.get('gate')
    if ((decision == 'accepted' and gate is not None)
            or (decision != 'accepted'
                and (not isinstance(gate, str) or not gate))):
        raise ProvenanceRefused(
            'Global custody terminal outcome 的 decision/gate 不闭合')
    chain_id = durable.rejection.get('chain_id')
    if not isinstance(chain_id, str):
        raise ProvenanceRefused('Global custody rejection 缺 chain identity')
    ctx.ledger.note_response_repair(
        sid=ctx.sid, node=ctx.node, purpose=ctx.node,
        attempt_id=durable.attempt_id,
        action=audit_action,
        decision=str(decision),
        actor=str(resolution.get('actor') or ''),
        gate=gate,
        candidate_sha256=candidate_sha256,
        dump=paths.rejected_response_rel(chain_id, durable.attempt_id),
        model=str(durable.rejection.get('model') or ''),
        rung_index=durable.rejection.get('rung_index'),
        sample=durable.rejection.get('sample'),
        chapter=durable.rejection.get('chapter'),
    )


def adopt_global_validator_resolution_checkpoint(
        ctx: RunContext,
        prepared: GlobalCandidateContext,
) -> None:
    """把 sealed typed replacement 收成普通 checkpoint；全程 0 wire。"""
    from novel.llm import response_repair, stage2_global_runs

    chain = _global_validator_custody_chain(ctx, prepared)
    records = response_repair.records_for_chain(ctx.novel_dir, chain)
    if not records:
        return
    if len(records) != 1:
        raise ProvenanceRefused(
            f'Global validator custody chain 应恰有一份拒稿，实得 {len(records)}')
    record = records[0]
    rejection = record.rejection
    if (rejection.get('run_id') != ctx.run_id
            or rejection.get('code') != ctx.code
            or rejection.get('candidate_subject_sha256') != prepared.input_sha256
            or rejection.get('main_text_sha256') != prepared.main_text_sha256
            or rejection.get('evidence_candidates_sha256')
            != prepared.evidence_candidates_sha256
            or rejection.get('response_model')
            != prepared.agent_response_model.__name__):
        raise ProvenanceRefused('Global validator custody 与当前 run/subject/schema 漂移')
    resolution = record.resolution
    if resolution is None:
        raise TerminalState(
            f'Global validator 拒稿 {record.attempt_id} 已进入 hash-bound custody；'
            '请编辑 candidate.json 并运行 response-repair submit，随后以同 run 恢复（0 wire）')
    if (record.outcome is not None
            and record.outcome.get('decision') in (
                'accepted', 'rejected', 'declined')):
        _note_global_custody_terminal_outcome(ctx, record)
        if record.outcome.get('decision') in ('rejected', 'declined'):
            raise TerminalState(
                f'Global validator 拒稿 {record.attempt_id} 已有不可变 '
                f'{record.outcome.get("decision")} outcome；不再发 wire')
    action = resolution.get('action')
    if action == 'decline':
        response_repair.write_outcome(
            record, decision='declined', gate='validator',
            detail=None, why='人工明确放弃 typed replacement')
        _note_global_custody_terminal_outcome(ctx, record)
        raise TerminalState(
            f'Global validator 拒稿 {record.attempt_id} 已被人工放弃；不再发 wire')
    if action not in ('submit', 'deterministic'):
        raise ProvenanceRefused('Global validator custody resolution action 不合法')
    acceptance = rejection.get('acceptance')
    original_payload = rejection.get('typed_payload')
    if not isinstance(acceptance, dict) or not isinstance(original_payload, dict):
        raise ProvenanceRefused('Global custody 缺原 accepted stdout 联结')
    links = acceptance.get('attempt_links')
    if not isinstance(links, list) or not links:
        raise ProvenanceRefused('Global custody acceptance 缺 attempt links')
    attempt_ids = [
        str(row.get('attempt_id')) for row in links if isinstance(row, dict)]
    fingerprints = [
        str(row.get('request_fingerprint'))
        for row in links if isinstance(row, dict)]
    if len(attempt_ids) != len(links) or len(fingerprints) != len(links):
        raise ProvenanceRefused('Global custody attempt links 形状不完整')
    ctx.ledger.claim_prior_attempts(
        attempt_ids,
        sid=ctx.sid,
        node=ctx.node,
        purpose=ctx.node,
        request_fingerprints=fingerprints,
    )
    assert_global_main_text_current(ctx, prepared)
    _global_custody_expectation(rejection)
    replacement_raw = resolution.get('candidate')
    try:
        replacement_turn = prepared.agent_response_model.model_validate(
            replacement_raw)
    except ValidationError as exc:
        response_repair.write_outcome(
            record, decision='rejected', gate='schema',
            detail=None, why=str(exc))
        _note_global_custody_terminal_outcome(ctx, record)
        raise TerminalState(
            f'Global typed replacement 不通过当前 schema：{exc}') from exc
    why = global_full_candidate_validator(prepared)(replacement_turn, None)
    response = replacement_turn.payload.candidate
    if why is not None:
        response_repair.write_outcome(
            record, decision='rejected', gate='validator',
            detail=None, why=why)
        _note_global_custody_terminal_outcome(ctx, record)
        raise TerminalState(
            f'知识 typed replacement 不通过当前 validator：{why}')
    state = _global_checkpoint_state(
        prepared, response, turn_kind='custody_replacement')
    state['custody'] = {
        'chain_id': chain,
        'attempt_id': record.attempt_id,
        'resolution_sha256': resolution.get('resolution_sha256'),
        'candidate_payload_sha256': resolution.get(
            'candidate_payload_sha256'),
        'actor': resolution.get('actor'),
        'reason': resolution.get('reason'),
    }
    turn_index = acceptance.get('accepted_turn_index')
    stage2_global_runs.write_checkpoint(
        ctx,
        input_sha256=prepared.input_sha256,
        system=prepared.system,
        user=prepared.user,
        response_model=prepared.agent_response_model,
        max_tokens=GLOBAL_MAX_TOKENS,
        response_payload=original_payload,
        acceptance=acceptance,
        revision_key=f'custody:{turn_index}',
        state_payload=state,
    )
    response_repair.write_outcome(
        record, decision='accepted', gate=None, detail=None, why=None)
    _note_global_custody_terminal_outcome(ctx, record)


def _validate_global_checkpoint_state(
        prepared: GlobalCandidateContext, state: dict[str, Any],
) -> GlobalGroundingCandidateResponse:
    keys = {'kind', 'turn_kind', 'main_text_sha256', 'evidence_candidates_sha256',
            'candidate', 'candidate_sha256', 'custody'}
    if (set(state) != keys or state.get('kind') != 'stage2-knowledge-candidate-v3'
            or state.get('turn_kind') not in ('full_candidate', 'custody_replacement')
            or state.get('main_text_sha256') != prepared.main_text_sha256
            or state.get('evidence_candidates_sha256') != prepared.evidence_candidates_sha256):
        raise ProvenanceRefused('知识 checkpoint 的版本、输入或关闭字段集合漂移')
    response = GlobalGroundingCandidateResponse.model_validate(state['candidate'])
    if global_response_sha256(response) != state['candidate_sha256']:
        raise ProvenanceRefused('知识 checkpoint 的回答 SHA 漂移')
    try:
        _compile_global_response(prepared, response)
    except (TypeError, ValueError) as exc:
        raise ProvenanceRefused(f'知识 checkpoint 不通过当前成员校验：{exc}') from exc
    return response


def restore_global_agent_checkpoint(
        ctx: RunContext, source: _GlobalResponseSource, prepared: GlobalCandidateContext,
) -> RestoredGlobalCandidate | None:
    """恢复同一已购知识回答，候选证据和 custody 从当前封存材料精确复验。"""
    from novel.llm import response_repair, stage2_global_runs
    assert_global_main_text_current(ctx, prepared)
    restored = stage2_global_runs.restore_checkpoint(
        ctx, input_sha256=prepared.input_sha256, system=prepared.system, user=prepared.user,
        response_model=prepared.agent_response_model, max_tokens=GLOBAL_MAX_TOKENS)
    if restored is None:
        return None
    source.restore_checkpoint_acceptance(
        ctx.node, restored.acceptance, system=prepared.system, user=prepared.user,
        max_tokens=GLOBAL_MAX_TOKENS, recovery_policy=stage2_global_runs.RECOVERY_POLICY)
    state = restored.state_payload
    response = _validate_global_checkpoint_state(prepared, state)
    turn_kind = state['turn_kind']
    if turn_kind == 'full_candidate':
        if state['custody'] is not None or restored.value.payload.candidate != response:
            raise ProvenanceRefused('知识 full checkpoint 与已接受原回答不一致')
    else:
        custody = state['custody']
        if not isinstance(custody, dict) or custody.get('attempt_id') != restored.acceptance.get('attempt_id'):
            raise ProvenanceRefused('知识 custody checkpoint 的原回答身份漂移')
        record = response_repair.find_attempt(ctx.novel_dir, custody['attempt_id'])
        _global_custody_expectation(record.rejection)
        resolution = record.resolution
        if (record.rejection.get('chain_id') != _global_validator_custody_chain(ctx, prepared)
                or record.rejection.get('chain_id') != custody.get('chain_id')
                or not isinstance(resolution, dict)
                or resolution.get('action') not in ('submit', 'deterministic')
                or resolution.get('resolution_sha256') != custody.get('resolution_sha256')
                or resolution.get('candidate_payload_sha256') != custody.get('candidate_payload_sha256')):
            raise ProvenanceRefused('知识 custody checkpoint 与 sealed resolution 漂移')
        replacement = prepared.agent_response_model.model_validate(resolution.get('candidate'))
        why = global_full_candidate_validator(prepared)(replacement, None)
        if why is not None or replacement.payload.candidate != response:
            raise ProvenanceRefused('知识 custody checkpoint 未命中当前有效 replacement')
    assert_global_main_text_current(ctx, prepared)
    return RestoredGlobalCandidate(response=response, acceptance=dict(restored.acceptance),
                                   revision=restored.revision, turn_kind=turn_kind)


def _read_blueprint_guard(
        ctx: RunContext, store: ArtifactStore, *, observe: bool,
) -> _BlueprintGuard:
    """冻结现有**正式** blueprint；INVALID 不得反向约束 global producer。"""
    if not store.exists(BLUEPRINT_REL):
        return _BlueprintGuard(state='absent')
    try:
        _old_bp, raw = store.read_own(ChapterBlueprints, BLUEPRINT_REL)
    except ArtifactUnreadable as exc:
        # 只放真正的旧注册版本；坏 marker、未来版本和坏 envelope 继续关闭式失败。
        cause = _older_version_error(exc)
        if cause is None:
            raise
        if observe:
            ctx.observe(
                'optional_downstream_guard_skipped', artifact=BLUEPRINT_REL,
                reason='artifact_version_mismatch',
                found_version=cause.found, current_version=cause.expected,
            )
        return _BlueprintGuard(
            state=f'older:{cause.found!r}:{cause.expected!r}')
    frozen = copy.deepcopy(raw)
    return _BlueprintGuard(
        state='current', raw_sha256=_canonical_sha256(frozen), raw=frozen)


def _read_global_downstream_guard(
        ctx: RunContext, store: ArtifactStore,
        main_text: list[dict[str, Any]], *, observe: bool,
) -> _GlobalDownstreamGuard:
    """冻结现有 blueprint 差分语义；读取只作前置校验，不进入新 global inputs。"""
    blueprint = _read_blueprint_guard(ctx, store, observe=observe)
    if blueprint.state != 'current':
        return _GlobalDownstreamGuard(
            blueprint=blueprint, baseline_state='unused')
    assert blueprint.raw is not None
    try:
        old_global = _read_raw(store, GlobalGrounding, GLOBAL_REL)
    except ArtifactUnreadable as exc:
        # 与旧路径一致：只有低版本 global 可失去 before 差分；其余载体错误关闭式失败。
        version = _older_version_error(exc)
        if version is None:
            raise
        if observe:
            ctx.observe(
                'optional_downstream_baseline_unavailable', artifact=GLOBAL_REL,
                found_version=version.found, current_version=version.expected,
            )
        return _GlobalDownstreamGuard(
            blueprint=blueprint,
            baseline_state=f'older:{version.found!r}:{version.expected!r}',
            baseline_hard_hits=None,
        )

    before = frozenset(_hard_hits(
        _judge_candidate(old_global, blueprint.raw, main_text, characters=_read_characters(store))))
    return _GlobalDownstreamGuard(
        blueprint=blueprint,
        baseline_state='absent' if old_global is None else 'current',
        baseline_sha256=(
            None if old_global is None else _canonical_sha256(old_global)),
        baseline_hard_hits=before,
    )


def prepare_global_candidate(
    ctx: RunContext, *, agent_envelope: bool = False,
    review_token: str | None = None,
) -> GlobalCandidateContext:
    """冻结一次 global 候选循环的 prompt、正文与下游差分基线。

    本函数不调用模型、不铸 provenance、不写盘。blueprint/旧 global 的差分守卫用
    ``validation_reads`` 隔离；独立修订使用冻结原稿作为局部句柄基线下发，
    并将当前 typed 请求绑定进候选身份，不新增反向依赖。
    """
    if ctx.node != 'stage2.global':
        raise ValueError(
            f'prepare_global_candidate 只接受 stage2.global，实得 {ctx.node!r}')
    from novel.llm import stage2_global_runs

    # 所有 provider 入口共用的首个 wire 前所有权闸。agent 同 run 的 prepared
    # 必须允许幂等补尾；legacy LlmClient 不会消费 agent journal，因此连同 run
    # prepared/committed 也必须拒绝，不能绕过 journal 直接覆盖正式字节。
    with stage2_global_runs.commit_ownership(ctx.novel_dir):
        stage2_global_runs.refuse_foreign_commit_reservations(ctx)
        if not agent_envelope:
            stage2_global_runs.refuse_current_agent_state_for_legacy(ctx)
    store = ArtifactStore(ctx)
    main_text_sha256 = store.full_sha256_or_none(MAIN_TEXT)
    if main_text_sha256 is None:
        raise UpstreamMissing(f'{MAIN_TEXT} 不在盘上——无法准备 Global 候选')
    main_text = _read_main_text(store)
    assert main_text is not None
    if store.full_sha256_or_none(MAIN_TEXT) != main_text_sha256:
        raise ProvenanceRefused(
            f'{MAIN_TEXT} 在 Global prompt 读取期间发生漂移；拒绝冻结混合主体')
    registry = grounding_io.read_registry(store)
    reference = grounding_io.knowledge_reference(registry)
    reference_sha256 = grounding_io.knowledge_reference_sha256(registry)
    spec = _active_global_spec()
    system, user, evidence_candidates = build_global_request(
        ctx.title, main_text, spec,
        character_reference=reference, agent_envelope=agent_envelope)
    global_id_map: dict[str, str] = {}
    previous = None
    with ctx.validation_reads():
        from novel.llm import agent_runs
        run_spec = stage2_global_runs._run_spec()
        if ctx.executor_profile is not None and (run_spec.run_path(ctx) / agent_runs.RUN_FILE).exists():
            frozen = agent_runs.initial_artifact_sha256s(ctx, run_spec)
            source_rel = _GLOBAL_INVALID if frozen[_GLOBAL_INVALID] is not None else GLOBAL_REL
            if frozen[source_rel] is not None:
                previous = store.read_own_by_sha256(KnowledgeGrounding, source_rel, frozen[source_rel])
        elif store.exists(_GLOBAL_INVALID) or store.exists(GLOBAL_REL):
            source_rel = _GLOBAL_INVALID if store.exists(_GLOBAL_INVALID) else GLOBAL_REL
            previous = store.read_own(KnowledgeGrounding, source_rel, require_current=True)[0]
    if previous is not None:
        draft, global_id_map = grounding_io.draft_projection(previous, evidence_candidates, registry=registry)
        user += '\n\n' + json.dumps({'kind': 'current_global_baseline',
            'candidate': draft.model_dump(mode='json'),
            'instruction': '保留未变主体的局部句柄；删除显式省略，新主体使用未占用的新句柄。'},
            ensure_ascii=False, sort_keys=True)
    if review_token is not None:
        # 恢复重放保留 token 形参；Stage2 语义审阅退役后没有登记返工面可读，
        # token 只作为本次请求/候选身份的一部分进入摘要，不改变材料。
        user += '\n\n' + json.dumps({
            'kind': 'commander_rework_request',
            'review_token': review_token,
            'instruction': ('按明确反馈修订本节点候选，保留未受影响的事实；'
                            '证据仍只返回本次候选表 ID，由代码校验与提交。'),
        }, ensure_ascii=False, sort_keys=True)
    with ctx.validation_reads():
        downstream = _read_global_downstream_guard(
            ctx, store, main_text, observe=True)
    candidate_sha256 = evidence_candidates.sha256()
    input_sha256 = _canonical_sha256({
        'node': 'stage2.global',
        'contract_name': spec.name,
        'main_text_file_sha256': main_text_sha256,
        'evidence_candidates_sha256': candidate_sha256,
        'characters_reference_sha256': reference_sha256,
        'frozen_global_id_map': global_id_map,
        'retired_global_ids': [] if previous is None else previous.provenance.retired_global_ids,
        'request_user_sha256': _canonical_sha256(user),
        **({'review_token': review_token} if review_token is not None else {}),
    })
    return GlobalCandidateContext(
        system=system,
        user=user,
        contract_name=spec.name,
        input_sha256=input_sha256,
        main_text_sha256=main_text_sha256,
        evidence_candidates_sha256=candidate_sha256,
        characters_reference_sha256=reference_sha256,
        _characters=registry.model_copy(deep=True),
        _main_text=tuple(copy.deepcopy(main_text)),
        _downstream=downstream,
        _contract=spec,
        _evidence_candidates=evidence_candidates,
        _global_id_map=global_id_map,
        _global_previous_ids=frozenset() if previous is None else frozenset(previous.identity_keys()),
        _global_retired_ids=frozenset() if previous is None else frozenset(previous.provenance.retired_global_ids),
    )


def assert_global_main_text_current(
        ctx: RunContext, prepared: GlobalCandidateContext,
) -> None:
    """逐尾窗重读盘上 MainText 主体 SHA，不拿冻结内存正文冒充 freshness。"""
    registry = grounding_io.read_registry(ArtifactStore(ctx), own=True)
    if grounding_io.knowledge_reference_sha256(registry) != prepared.characters_reference_sha256:
        raise ProvenanceRefused('stage2.global 实际人物引用在 executor 期间漂移；拒绝旧候选提交')
    current = ArtifactStore(ctx).full_sha256_or_none(MAIN_TEXT)
    if current != prepared.main_text_sha256:
        raise ProvenanceRefused(
            f'stage2.global MainText 主体在 executor 期间漂移：'
            f'prepared={prepared.main_text_sha256} current={current}；'
            '拒绝恢复、反馈或提交旧主体候选')


def _assert_global_guard_current(
        ctx: RunContext, prepared: GlobalCandidateContext,
) -> None:
    """每次反馈/终态前证明 agent 仍在回答同一份盘面。"""
    assert_global_main_text_current(ctx, prepared)
    store = ArtifactStore(ctx)
    main_text = copy.deepcopy(list(prepared._main_text))
    with ctx.validation_reads():
        current = _read_global_downstream_guard(
            ctx, store, main_text, observe=False)
    if current != prepared._downstream:
        raise ProvenanceRefused(
            'stage2.global executor session 期间 blueprint/global 差分基线发生漂移；'
            '拒绝把旧 session 的候选接到新盘面，须以新 session 重跑')


def _assert_blueprint_guard_current(
        ctx: RunContext, prepared: GlobalCandidateContext,
) -> None:
    """正式 global 已替换后，只复核仍应保持不变的 blueprint 那一半。"""
    assert_global_main_text_current(ctx, prepared)
    store = ArtifactStore(ctx)
    with ctx.validation_reads():
        current = _read_blueprint_guard(ctx, store, observe=False)
    if current != prepared._downstream.blueprint:
        raise ProvenanceRefused(
            'stage2.global 正式提交期间 blueprint guard 漂移；'
            '不能把本次 readback 声明为 delivered')


def _global_candidate_outcomes(
        candidate: dict[str, Any], prepared: GlobalCandidateContext,
) -> tuple[list[Outcome], tuple[str, ...]]:
    """复用现有判据并严格保留 blueprint 的 before/after 差分口径。"""
    main_text = copy.deepcopy(list(prepared._main_text))
    outcomes = _judge_candidate(candidate, None, main_text, characters=prepared._characters.model_dump(mode='json', by_alias=True))
    hits = set(_hard_hits(outcomes, ignore={'upstream_missing'}))
    guard = prepared._downstream
    if guard.blueprint.state == 'current':
        assert guard.blueprint.raw is not None
        after = _judge_candidate(candidate, guard.blueprint.raw, main_text, characters=prepared._characters.model_dump(mode='json', by_alias=True))
        outcomes.extend(after)
        after_hits = set(_hard_hits(after))
        if guard.baseline_hard_hits is None:
            # 旧 global schema 无法构造可靠 before；与原实现一致，保守拦下全部 after HARD。
            hits.update(after_hits)
        else:
            hits.update(after_hits - set(guard.baseline_hard_hits))
    return outcomes, tuple(sorted(hits))


def _global_feedback(
        outcomes: list[Outcome], hits: tuple[str, ...],
) -> tuple[GlobalCandidateFeedback, ...]:
    """从命中的那份 Outcome 直接投影；不另跑一套 report 判定。"""
    rows: list[GlobalCandidateFeedback] = []
    for rule_id in hits:
        rule = REGISTRY.get(rule_id)
        findings: list[GlobalCandidateFinding] = []
        seen: set[tuple[str, tuple[str, ...]]] = set()
        for outcome in outcomes:
            if outcome.rule != rule_id:
                continue
            for finding in outcome.findings:
                identity = (finding.detail, tuple(finding.evidence))
                if identity in seen:
                    continue
                seen.add(identity)
                findings.append(GlobalCandidateFinding(
                    detail=finding.detail,
                    evidence=tuple(finding.evidence),
                ))
        rows.append(GlobalCandidateFeedback(
            rule=rule_id,
            item=rule.item,
            requirement=rule.requirement,
            findings=tuple(findings),
        ))
    return tuple(rows)


def _materialize_global_evaluation(
        ctx: RunContext,
        prepared: GlobalCandidateContext,
        response: GlobalGroundingCandidateResponse,
        *, requested_model: str,
        adopted_models: tuple[str, ...],
        source_attempt_id: str | None,
        settle: bool,
) -> GlobalCandidateEvaluation:
    """归一、铸候选来源账并判定；``settle=False`` 时严格无终态副作用。"""
    main_text = copy.deepcopy(list(prepared._main_text))
    data, accounting = normalize_global(
        _compile_global_response(prepared, response), main_text)
    from contracts.schema.knowledge import KnowledgeCore
    current_ids = KnowledgeCore.model_validate(data).identity_keys()
    provenance = ctx.llm_provenance(
        GlobalProvenance,
        prompts={prepared._contract.sys_name: prepared.system},
        model=requested_model,
        adopted_models=adopted_models,
        settle=settle,
        retired_global_ids=sorted(prepared._global_retired_ids | (prepared._global_previous_ids - current_ids)),
        **accounting,
    )
    artifact = GlobalGrounding.model_validate({
        **data, '_provenance': provenance,
    })
    candidate = artifact.model_dump(by_alias=True)
    outcomes, hits = _global_candidate_outcomes(candidate, prepared)
    return GlobalCandidateEvaluation(
        candidate=artifact,
        hard_hits=hits,
        feedback=_global_feedback(outcomes, hits),
        source_attempt_id=source_attempt_id,
        requested_model=requested_model,
        adopted_models=tuple(sorted(set(adopted_models))),
        response_sha256=global_response_sha256(response),
        _response=response.model_copy(deep=True),
        _evidence_locator_failures=tuple(
            tuple(row) for row in accounting['evidence_locator_failures']),
        _evidence_locator_corrections=(
            accounting.evidence_locator_corrections),
    )


def evaluate_global_candidate(
        ctx: RunContext,
        source: _GlobalResponseSource,
        prepared: GlobalCandidateContext,
        response: GlobalGroundingCandidateResponse,
) -> GlobalCandidateEvaluation:
    """代码评估一轮 agent 候选；不写 formal/INVALID、不归档、不结算 usage。"""
    _assert_global_guard_current(ctx, prepared)
    return _materialize_global_evaluation(
        ctx,
        prepared,
        response,
        requested_model=source.model,
        adopted_models=source.accepted_models(ctx.node),
        source_attempt_id=source.accepted_attempt(ctx.node),
        settle=False,
    )


def _write_formal(
    store: ArtifactStore, rel: str, artifact: Any,
) -> None:
    """提交正式产物；审阅已退役，失败/提交 sidecar 与 formal 同一写回集合。"""
    if rel == GLOBAL_REL:
        from novel.stages.stage2.analyze_recovery import refuse_pending_recovery
        refuse_pending_recovery(store.ctx.novel_dir)
    if rel == GLOBAL_REL and store.exists(GLOBAL_EDIT_INPUT_COMMIT):
        raise ProvenanceRefused(
            'GlobalEdit canonical WAL 尚未收敛；其他 Global 写口不得覆盖')
    if rel == GLOBAL_REL and store.exists(GLOBAL_EDIT_COMMIT):
        pending_commit = store.read_own(
            GlobalEditCommit, GLOBAL_EDIT_COMMIT)[0]
        if pending_commit.state == 'prepared':
            raise ProvenanceRefused(
                'reviewed-edit prepared commit 尚未收敛；其他 Global 写口不得覆盖')
    companions = (
        (rel, GLOBAL_EDIT_FAILURE, GLOBAL_EDIT_COMMIT)
        if rel == GLOBAL_REL else (rel,)
    )
    with store.history_transaction(*companions):
        store.write_round(rel, grounding_io.for_write(store, artifact)
                          if rel == GLOBAL_REL else artifact)
        if rel == GLOBAL_REL:
            # reviewed edit 的 failure/pending sidecar 只描述“formal bundle 尚未
            # 提交”。formal 落稳后才在同一回滚域退休；
            # 任一伴产物写失败都会连 sidecar 一起恢复到调用前。
            if store.exists(GLOBAL_EDIT_FAILURE):
                store.archive_round(GLOBAL_EDIT_FAILURE)
            if store.exists(GLOBAL_EDIT_COMMIT):
                store.archive_round(GLOBAL_EDIT_COMMIT)


def _retire_invalid_for_matching_formal(
        store: ArtifactStore, rel: str, artifact: Any,
) -> None:
    """提交尾窗只退休问题稿，不把逐字相同的正式稿再记成一个历史轮次。"""
    invalid_rel = paths.invalid_of(rel)
    if not store.exists(invalid_rel):
        return
    with store.history_transaction(rel):
        current, _raw = (grounding_io.read_own(store, rel, require_current=True)
                         if rel == GLOBAL_REL else store.read_own(
                             type(artifact), rel, require_current=True))
        if current.model_dump(by_alias=True) != artifact.model_dump(by_alias=True):
            raise ProvenanceRefused(
                f'{rel} 在提交尾窗已漂移；拒绝只退休 {invalid_rel}')
        store.retire_invalid(rel)


def _write_invalid(
    store: ArtifactStore, rel: str, artifact: Any, *,
    owned_global_edit_sha256: str | None = None,
) -> str:
    """保存失败候选并保留前稿；付费草稿不能被其他owner覆盖。"""
    if rel == GLOBAL_REL:
        from novel.stages.stage2.analyze_recovery import refuse_pending_recovery
        refuse_pending_recovery(store.ctx.novel_dir)
    if rel != GLOBAL_REL:
        return str(store.write_invalid(rel, artifact, gates_skipped=[]))
    if store.exists(GLOBAL_EDIT_INPUT_COMMIT):
        input_journal = store.read_own(
            GlobalEditInputCommit, GLOBAL_EDIT_INPUT_COMMIT)[0]
        if (owned_global_edit_sha256 is None
                or input_journal.target_edit_sha256
                != owned_global_edit_sha256
                or store.full_sha256_or_none(GLOBAL_EDIT)
                != owned_global_edit_sha256):
            raise ProvenanceRefused(
                'GlobalEdit canonical WAL 尚未收敛；其他 Global INVALID 写口不得覆盖')
    if store.exists(GLOBAL_EDIT_COMMIT):
        pending_commit = store.read_own(
            GlobalEditCommit, GLOBAL_EDIT_COMMIT)[0]
        if pending_commit.state == 'prepared':
            raise ProvenanceRefused(
                'reviewed-edit prepared commit 尚未收敛；其他 Global INVALID 写口不得覆盖')
    # 人物与称名投影由独立 producer 拥有；知识问题稿不写、不采用它们。
    with store.history_transaction(
            _GLOBAL_INVALID, GLOBAL_EDIT_COMMIT):
        path = store.write_invalid(rel, grounding_io.for_write(store, artifact), gates_skipped=[])
        if store.exists(GLOBAL_EDIT_COMMIT):
            store.archive_round(GLOBAL_EDIT_COMMIT)
    return str(path)


def _equal_global_invalid_payload(
        expected: GlobalGrounding, landed: GlobalGrounding,
) -> bool:
    """忽略 ``write_invalid`` 唯一负责补入的两格 provenance 后比较候选。"""
    left = expected.model_dump(by_alias=True, mode='json')
    right = landed.model_dump(by_alias=True, mode='json')
    left_prov = left.get('_provenance')
    right_prov = right.get('_provenance')
    if isinstance(left_prov, dict) and isinstance(right_prov, dict):
        left_prov['gates_skipped'] = right_prov.get('gates_skipped')
        left_prov['invalid_baseline_sha256'] = right_prov.get(
            'invalid_baseline_sha256')
    return left == right


def _write_or_reuse_global_invalid(
        ctx: RunContext,
        prepared: GlobalCandidateContext,
        artifact: GlobalGrounding,
) -> str:
    """Agent 同一 accepted checkpoint 的 blocked 终态最多产生一个问题轮次。

    legacy 路径没有可证明为同一轮的 durable checkpoint；每次 ``--resample``
    都是新的付费请求，必须交给 ``write_invalid`` 归档上一轮问题产物。
    """
    store = ArtifactStore(ctx)
    if ctx.executor_profile is not None and store.exists(_GLOBAL_INVALID):
        with ctx.validation_reads():
            existing, _ = grounding_io.read_own(store, _GLOBAL_INVALID, require_current=True)
        if _equal_global_invalid_payload(artifact, existing):
            assert_global_main_text_current(ctx, prepared)
            return str(store.path(_GLOBAL_INVALID))
    return _write_invalid(store, GLOBAL_REL, artifact)


def _note_evidence_locator_corrections(
    ctx: RunContext,
    store: ArtifactStore,
    corrections: tuple[EvidenceLocatorCorrection, ...],
    *,
    rel: str,
) -> None:
    """在候选字节落地后记录无正文的跨章唯一回取账。"""
    if not corrections:
        return
    artifact_sha256 = store.full_sha256_or_none(rel)
    if artifact_sha256 is None:
        raise ProvenanceRefused(
            f'{rel} 还未落地，拒绝把 evidence locator 纠正记成已采用')
    for correction in corrections:
        ctx.ledger.note_evidence_locator_rewrite(
            sid=ctx.sid,
            node=ctx.node,
            purpose=ctx.node,
            artifact=rel,
            artifact_sha256=artifact_sha256,
            claim_path=correction.claim_path,
            original_chapter_ordinal=correction.original_chapter,
            resolved_chapter_ordinal=correction.resolved_chapter,
            match_mode=correction.recovery,
        )


def _note_adopted(ctx: RunContext, client: _GlobalResponseSource, *, rel: str,
                  candidate: dict[str, Any],
                  purposes: dict[str, int | None]) -> None:
    """正式产物落盘后，逐 purpose 写 `artifact_adoption` 审计行（`llm_audit.jsonl`）。

    「最终采纳档位」由 adoption 行 → `attempt_id` → 成本账行反查，不再从
    「同 purpose 最后一条 ok 行」猜。本 run 没有采纳新响应的 purpose
    （定向重生成合并进来的未动章、0 成本路）没有登记项 ⇒ 无行，缺行即「沿用旧稿」。
    """
    fp = prov.projection_fingerprint(candidate)
    for purpose, chapter in purposes.items():
        attempt_id = client.accepted_attempt(purpose)
        if attempt_id is None:
            continue
        ctx.ledger.note_adoption(
            sid=ctx.sid, node=ctx.node, purpose=purpose, attempt_id=attempt_id,
            artifact=rel, artifact_fingerprint=fp, chapter=chapter)


def _accepted_adoptions(
        client: _GlobalResponseSource, purposes: dict[str, int | None],
) -> tuple[tuple[str, int | None, str], ...]:
    """把 client 的最终采纳映射冻结成 commit journal 可跨进程恢复的形状。"""
    return tuple(
        (purpose, chapter, attempt_id)
        for purpose, chapter in purposes.items()
        if (attempt_id := client.accepted_attempt(purpose)) is not None
    )


def _note_restored_adoptions(
        ctx: RunContext, *, rel: str, candidate: dict[str, Any],
        adoptions: tuple[tuple[str, int | None, str], ...],
) -> None:
    """提交尾窗崩溃后按 journal 补 adoption；稳定 adoption_id 保证不重复追加。"""
    fp = prov.projection_fingerprint(candidate)
    for purpose, chapter, attempt_id in adoptions:
        ctx.ledger.note_adoption(
            sid=ctx.sid, node=ctx.node, purpose=purpose, attempt_id=attempt_id,
            artifact=rel, artifact_fingerprint=fp, chapter=chapter)


def verify_global_delivery(
        ctx: RunContext,
        prepared: GlobalCandidateContext,
        expected: GlobalGrounding,
) -> GlobalGrounding:
    """正式提交后的独立 readback 终验；机械可用之外不表达语义接受。"""
    assert_global_main_text_current(ctx, prepared)
    store = ArtifactStore(ctx)
    _assert_blueprint_guard_current(ctx, prepared)
    if store.exists(_GLOBAL_INVALID):
        raise ProvenanceRefused(
            f'stage2.global 正式提交后仍有 live {_GLOBAL_INVALID}；'
            '不能把一对互相矛盾的 live 文件声明为 delivered')

    with ctx.validation_reads():
        landed, landed_raw = grounding_io.read_own(store, GLOBAL_REL, require_current=True)
        global_freshness = store.freshness(GLOBAL_REL)

    expected_raw = expected.model_dump(by_alias=True, mode='json')
    landed_dump = landed.model_dump(by_alias=True, mode='json')
    if (_canonical_sha256(expected_raw) != _canonical_sha256(landed_dump)
            or expected_raw != landed_dump):
        raise ProvenanceRefused(
            'stage2.global 正式 readback 与本次终态候选不等值；'
            '拒绝只凭写盘调用未抛错声明 delivered')
    if global_freshness.state != 'fresh':
        raise ProvenanceRefused(
            f'stage2.global 正式 readback freshness='
            f'{global_freshness.state}/{global_freshness.reason}，拒绝 delivered')

    _outcomes, hits = _global_candidate_outcomes(landed_raw, prepared)
    if hits:
        raise ProvenanceRefused(
            f'stage2.global 正式 readback 终验仍命中 HARD {list(hits)}；'
            '不能把 exit 0 冒充 delivered')
    assert_global_main_text_current(ctx, prepared)
    return landed


def _global_finalization_payload(
        prepared: GlobalCandidateContext,
        terminal: GlobalCandidateEvaluation,
) -> dict[str, object]:
    return {
        'artifact': GLOBAL_REL,
        'input_sha256': prepared.input_sha256,
        'main_text_sha256': prepared.main_text_sha256,
        'evidence_candidates_sha256': prepared.evidence_candidates_sha256,
        'contract_name': prepared.contract_name,
        'response_sha256': terminal.response_sha256,
        'hard_hits': list(terminal.hard_hits),
        'evidence_locator_corrections': [
            {
                'claim_path': correction.claim_path,
                'original_chapter': correction.original_chapter,
                'resolved_chapter': correction.resolved_chapter,
                'recovery': correction.recovery,
            }
            for correction in terminal._evidence_locator_corrections
        ],
    }


def _global_bundle_readback(
        ctx: RunContext,
) -> dict[str, dict[str, Any]]:
    store = ArtifactStore(ctx)
    with ctx.validation_reads():
        global_g, _ = grounding_io.read_own(store, GLOBAL_REL, require_current=True)
    return {
        GLOBAL_REL: grounding_io.payload(store, global_g),
    }


def _finalize_agent_global_formal(
        ctx: RunContext,
        prepared: GlobalCandidateContext,
        terminal: GlobalCandidateEvaluation,
) -> GlobalGrounding:
    """封存知识与采用账；语义未审随报告 ``unver`` 披露，不再刷新审阅面。

    人物未被知识请求消费的字段不再触发任何审阅刷新，也不改写已购买知识的
    来源身份。模型提交只拥有知识与 INVALID。
    """
    from novel.llm import stage2_global_runs

    artifact = terminal.candidate
    store = ArtifactStore(ctx)
    payload = grounding_io.payload(store, artifact)
    finalization = _global_finalization_payload(prepared, terminal)
    if terminal.source_attempt_id is None:
        raise ProvenanceRefused('stage2.global 正式 agent 候选缺 accepted attempt')
    with stage2_global_runs.commit_ownership(ctx.novel_dir):
        stage2_global_runs.refuse_foreign_commit_reservations(ctx)
        journal = stage2_global_runs.committed(ctx) or stage2_global_runs.prepared(ctx)
        if journal is None:
            _assert_global_guard_current(ctx, prepared)
            journal = stage2_global_runs.prepare_commit(
                ctx, primary_payload=payload, finalization_payload=finalization,
                touched_paths=(GLOBAL_REL, _GLOBAL_INVALID),
                attempt_id=terminal.source_attempt_id)
        else:
            if not stage2_global_runs.verify_commit_finalization(journal, finalization):
                raise ProvenanceRefused('知识提交 journal 与当前回答/输入身份漂移')
            if not stage2_global_runs.verify_commit_primary(journal, payload):
                # 只有已经精确命中本 journal 的正式文件可以保留原来源账；
                # 缺失或第三方修改都不能借业务相似性冒领这份回答。
                owned, _ = store.read_own(KnowledgeGrounding, GLOBAL_REL, require_current=True)
                owned_payload = owned.model_dump(mode='json', by_alias=True)
                if (not stage2_global_runs.verify_commit_primary(journal, owned_payload)
                        or {k: v for k, v in owned_payload.items() if k != '_provenance'}
                        != {k: v for k, v in payload.items() if k != '_provenance'}):
                    raise ProvenanceRefused('知识提交 journal 与正式文件或当前候选业务内容漂移')
                # 当前请求与旧文件均已通过 actual-input freshness；完整业务值
                # 相等并不能替代来源守卫，故这里仍要求非输入来源账逐字段相等。
                old_prov = dict(owned_payload['_provenance'])
                new_prov = dict(payload['_provenance'])
                old_prov.pop('inputs', None)
                new_prov.pop('inputs', None)
                if old_prov != new_prov:
                    raise ProvenanceRefused('知识恢复的非输入来源账变化，拒绝混用两次产出')
                payload = owned_payload
                artifact = grounding_io.compose(grounding_io.read_registry(store, own=True), owned)

        assert_global_main_text_current(ctx, prepared)
        expected = grounding_io.for_write(store, artifact)
        state = stage2_global_runs.target_state(
            ctx, journal, GLOBAL_REL,
            expected_file_sha256=store.prospective_sha256(GLOBAL_REL, expected))
        invalid_state = stage2_global_runs.target_state(
            ctx, journal, _GLOBAL_INVALID, expected_absent=True)
        if state == 'baseline':
            if any(store.exists(rel) for rel in (GLOBAL_EDIT_INPUT_COMMIT, GLOBAL_EDIT_FAILURE)):
                raise ProvenanceRefused('人工知识订正尚未收敛，不能由模型提交覆盖')
            if store.exists(GLOBAL_EDIT_COMMIT):
                edit_commit = store.read_own(GlobalEditCommit, GLOBAL_EDIT_COMMIT)[0]
                if edit_commit.state == 'prepared':
                    raise ProvenanceRefused('人工知识 prepared 尚未收敛，不能由模型提交覆盖')
            with store.history_transaction(GLOBAL_REL, GLOBAL_EDIT_COMMIT):
                store.write_round(GLOBAL_REL, expected)
                if store.exists(GLOBAL_EDIT_COMMIT):
                    store.archive_round(GLOBAL_EDIT_COMMIT)
        elif invalid_state == 'baseline':
            _retire_invalid_for_matching_formal(store, GLOBAL_REL, artifact)
        assert_global_main_text_current(ctx, prepared)
        landed_payloads = _global_bundle_readback(ctx)
        if landed_payloads[GLOBAL_REL] != payload or store.exists(_GLOBAL_INVALID):
            raise ProvenanceRefused('知识 readback 未精确闭合当前提交目标')
        _note_evidence_locator_corrections(
            ctx, store, terminal._evidence_locator_corrections, rel=GLOBAL_REL)
        purpose, attempt_id = stage2_global_runs.restore_commit_adoption(ctx, journal)
        if purpose != ctx.node or attempt_id != terminal.source_attempt_id:
            raise ProvenanceRefused('知识 adoption 与 accepted attempt 不一致')
        _note_restored_adoptions(
            ctx, rel=GLOBAL_REL, candidate=payload,
            adoptions=((purpose, None, attempt_id),))
        assert_global_main_text_current(ctx, prepared)
        stage2_global_runs.mark_committed(
            ctx, primary_payload=payload, finalization_payload=finalization,
            landed_payloads=landed_payloads)
        committed = stage2_global_runs.committed(ctx)
        if committed is None:
            raise ProvenanceRefused('知识 mark_committed 后 journal 缺失')
        stage2_global_runs.verify_committed_delivery(
            ctx, committed, primary_payload=payload,
            finalization_payload=finalization, landed_payloads=landed_payloads)
        return verify_global_delivery(ctx, prepared, artifact)


def finalize_global_candidate(
        ctx: RunContext,
        source: _GlobalResponseSource,
        prepared: GlobalCandidateContext,
        evaluation: GlobalCandidateEvaluation,
) -> GlobalGrounding:
    """把 agent 循环选定的最后一份候选提交一次；中间候选不得调用本函数。

    通过候选走 global + review 的既有事务、readback 与 adoption；失败候选只在这里
    写一次 ``.INVALID`` 并抛现有 ``GateBlocked``，因而 ``--revalidate`` /
    ``--resample`` 的恢复语义保持不变。
    """
    assert_global_main_text_current(ctx, prepared)
    current_code = prov.code_version()
    if current_code != ctx.code:
        raise ProvenanceRefused(
            f'stage2.global executor session 期间代码版本发生漂移：'
            f'start={ctx.code} current={current_code}；'
            '拒绝用旧代码启动的候选结算 usage 或写入正式产物')
    if source.model != evaluation.requested_model:
        raise ProvenanceRefused(
            'stage2.global 候选评估后 requested model 漂移；拒绝跨配置提交')
    if source.accepted_attempt(ctx.node) != evaluation.source_attempt_id:
        raise ProvenanceRefused(
            'stage2.global 候选评估后 accepted attempt 已变化；'
            '只能提交最后一次被评估的响应')
    if source.accepted_models(ctx.node) != evaluation.adopted_models:
        raise ProvenanceRefused(
            'stage2.global 候选评估后 adopted model 集合漂移；拒绝猜测来源')
    if global_response_sha256(evaluation._response) != evaluation.response_sha256:
        raise ProvenanceRefused(
            'stage2.global 候选评估后 raw candidate 已变异；'
            '拒绝用旧 SHA 的评估结论提交新内容')

    _assert_global_guard_current(ctx, prepared)
    terminal = _materialize_global_evaluation(
        ctx,
        prepared,
        evaluation._response,
        requested_model=evaluation.requested_model,
        adopted_models=evaluation.adopted_models,
        source_attempt_id=evaluation.source_attempt_id,
        settle=True,
    )
    if terminal.hard_hits != evaluation.hard_hits:
        raise ProvenanceRefused(
            'stage2.global 同一候选的 provisional/final HARD 结论不一致；'
            '拒绝在判据非确定时写盘')
    if (terminal._evidence_locator_failures
            != evaluation._evidence_locator_failures
            or terminal._evidence_locator_corrections
            != evaluation._evidence_locator_corrections):
        raise ProvenanceRefused(
            'stage2.global 同一候选的 provisional/final evidence 定位账不一致；'
            '拒绝在确定性归一漂移时写盘')

    store = ArtifactStore(ctx)
    artifact = terminal.candidate
    candidate = artifact.model_dump(by_alias=True)
    if terminal.hard_hits:
        from novel.llm import stage2_global_runs

        # HARD 候选的 INVALID/projection 仍属于 Global bundle。provider 返回后
        # 另一 run 可能才落下 prepared；这里必须与 formal 写口共用同书所有权，
        # 否则旁路问题稿会让原 journal 的 target_state 变成第三方漂移。
        with stage2_global_runs.mutation_ownership(
                ctx, legacy_wire=ctx.executor_profile is None):
            _assert_global_guard_current(ctx, prepared)
            assert_global_main_text_current(ctx, prepared)
            path = _write_or_reuse_global_invalid(
                ctx, prepared, artifact)
            assert_global_main_text_current(ctx, prepared)
            _note_evidence_locator_corrections(
                ctx, store, terminal._evidence_locator_corrections,
                rel=_GLOBAL_INVALID)
        raise GateBlocked(
            f'stage2.global 落盘闸门命中 {list(terminal.hard_hits)}。问题稿已存 '
            f'{_GLOBAL_INVALID}，正式产物没有被覆盖。改好后跑 '
            '`stage2.global --revalidate --actor system:invalid-revalidate --reason <audit>`'
            '（0 LLM、同轮重判并留不可变审计）。',
            invalid_path=path,
        )

    if ctx.executor_profile is not None:
        return _finalize_agent_global_formal(
            ctx, prepared, terminal)

    from novel.llm import stage2_global_runs

    # legacy direct 路没有 commit journal；写前必须在同一所有权临界区二次检查，
    # 关闭“准备完成后另一 run 才落 prepared”的 TOCTOU。foreign committed 不阻断，
    # 但本 run 若已有 agent journal，direct 路没有资格接管。
    with stage2_global_runs.commit_ownership(ctx.novel_dir):
        stage2_global_runs.refuse_foreign_commit_reservations(ctx)
        stage2_global_runs.refuse_current_agent_state_for_legacy(ctx)
        _assert_global_guard_current(ctx, prepared)
        assert_global_main_text_current(ctx, prepared)
        _write_formal(store, GLOBAL_REL, artifact)
        _note_evidence_locator_corrections(
            ctx, store, terminal._evidence_locator_corrections,
            rel=GLOBAL_REL)
        landed = verify_global_delivery(ctx, prepared, artifact)
        assert_global_main_text_current(ctx, prepared)
        _note_adopted(
            ctx, source, rel=GLOBAL_REL, candidate=candidate,
            purposes={ctx.node: None})
        return landed


def run_global(
    ctx: RunContext, client: LlmClient, *, review_token: str | None = None,
) -> GlobalGrounding:
    """旧 LlmClient 单轮入口；agent 多轮必须显式用 prepare/evaluate/finalize 三段。"""
    prepared = prepare_global_candidate(ctx, review_token=review_token)
    response = client.ask_json(
        prepared.system,
        prepared.user,
        model_cls=prepared.response_model,
        purpose=ctx.node,
        max_tokens=GLOBAL_MAX_TOKENS,
        validator=global_response_validator(prepared),
    )
    evaluation = evaluate_global_candidate(
        ctx, client, prepared, response)
    return finalize_global_candidate(
        ctx, client, prepared, evaluation)


def run_resample(ctx: RunContext, client: LlmClient) -> GlobalGrounding:
    """为被闸门拦下的整份 global 响应显式重新采样。

    普通重跑必须继续命中同参数缓存；这条入口则只在已有 `.INVALID` 时
    把缓存口径切到 `refresh-write`。写盘与归档仍完全复用 `run_global`：
    新稿再次失败时旧问题稿先进入 `_rounds/`，新稿通过时旧正式稿与
    问题稿都进入 `_rounds/`，请求本身失败时盘面一个字节不动。
    """
    store = ArtifactStore(ctx)
    if not store.exists(_GLOBAL_INVALID):
        raise UpstreamMissing(
            f'{ctx.node} --resample 只重采样被闸门拦下的问题稿；'
            f'盘上没有 {_GLOBAL_INVALID}，尚未发起请求。'
            '首次生成请运行 stage2.global 默认路径')
    ctx.cache = CachePolicy.refresh()
    return run_global(ctx, client)


def _global_source(
    store: ArtifactStore,
    *,
    replaced_edit_rel: str | None = None,
) -> tuple[GlobalGrounding, dict[str, Any], str]:
    """修复优先吃最新问题稿；没有问题稿才吃正式稿。

    ``--apply-edit`` 的下一轮会改写上一轮 provenance 里那份决策集；只允许这条明确
    被替换的输入边变化，正文等其余来源仍必须 current；本参数不授权跳过人物或其他实际依赖。
    """
    rel = _GLOBAL_INVALID if store.exists(_GLOBAL_INVALID) else GLOBAL_REL
    if not store.exists(rel):
        raise UpstreamMissing(
            f'{GLOBAL_REL} 与 {_GLOBAL_INVALID} 都不在盘上——先跑 stage2.global')
    artifact, raw = grounding_io.read_own(store, rel, require_current=True, replaced_input_keys=() if replaced_edit_rel is None else (replaced_edit_rel,))
    return artifact, raw, rel


def _run_chapter_requests(
    ctx: RunContext,
    client: _BlueprintRequestSource,
    requests: list[tuple[int | None, str, str]],
    *,
    model_cls: type,
    purpose_of: Callable[[int], str] | None = None,
    validator_of: Callable[[int], Callable[[Any, Any], str | None] | None]
    | None = None,
    max_tokens: int = BLUEPRINT_MAX_TOKENS,
    prefer_content_over_reasoning_salvage: bool = False,
    phase: str = 'blueprint',
    architecture: str = BLUEPRINT_ARCHITECTURE,
    checkpoints: bool = False,
    restore_checkpoints: bool | None = None,
    progress_tracker: ChapterProgressTracker | None = None,
) -> tuple[list[dict[str, Any]], list[str], list[Exception]]:
    """并发等待独立章节；一个失败不丢掉已经付费成功的其他章。

    `model_cls` 按契约 spec 传入——响应边界与 system 必须同一套，
    各选一半的失败形态是「prompt 是新的、校验还是旧的」而不会报错。
    """
    results: dict[int, dict[str, Any]] = {}
    violations: list[str] = []
    errors: list[Exception] = []
    request_failures: dict[int, Exception] = {}
    should_restore = checkpoints if restore_checkpoints is None else restore_checkpoints

    concrete = [(int(o), s, u) for o, s, u in requests if o is not None]
    if not concrete:
        return [], violations, errors
    if checkpoints:
        # delivery_recovery 只描述“上一只子进程已完整收尾后的当前等待态”。
        # 新一轮一旦开始便先撤销旧标记；本轮若仍只有可恢复交付失败，会在所有
        # future drain 完成后重新铸一份。这样早期 schema/代码异常不能借旧标记
        # 被 Supervisor 错认成同一场网络波动。
        from novel.llm import stage2_runs
        stage2_runs.clear_delivery_recovery(ctx)
    tracker = progress_tracker or ChapterProgressTracker(ctx.observe, [o for o, _s, _u in concrete])
    restored_material: dict[
        int, tuple[list[dict[str, Any]], dict[str, Any]],
    ] = {}

    # 先在调用线程把所有**现存** checkpoint 的身份、当前 schema、dynamic validator、
    # scope 与 ChatRequest 配置走完。任一份损坏时不能先把另一个缺章/恢复章提交到线程池；
    # 否则“最后会报错”仍可能已经多烧了一次 wire。
    if should_restore:
        from novel.llm import stage2_runs
        preflight_failures: dict[int, Exception] = {}
        for batch, (ordinal, system, user) in enumerate(concrete, 1):
            purpose = (purpose_of(ordinal) if purpose_of
                       else f'{ctx.node}:ch{ordinal}')
            validator = validator_of(ordinal) if validator_of else None
            try:
                restored = stage2_runs.restore_checkpoint(
                    ctx, phase=phase, chapter=ordinal, purpose=purpose,
                    architecture=architecture, response_model=model_cls,
                    system=system, user=user)
                if restored is None:
                    continue
                try:
                    response = model_cls.model_validate(
                        restored['response_payload'])
                except ValidationError as exc:
                    raise ArtifactUnreadable(
                        f'{phase}/ch{ordinal} checkpoint 不再通过当前 '
                        f'{model_cls.__name__}：{exc}') from exc
                why = validator(response, None) if validator is not None else None
                if why is not None:
                    raise ArtifactUnreadable(
                        f'{phase}/ch{ordinal} checkpoint 不再通过当前 dynamic validator：'
                        f'{why}')
                raw = response.model_dump(by_alias=True)['blueprints']
                kept, violation = _scope_split(raw, ordinal, batch)
                if kept is None:
                    raise ArtifactUnreadable(
                        f'{phase}/ch{ordinal} checkpoint 当前 scope 已无目标章')
                if restored.get('scope_violation') != violation:
                    raise ProvenanceRefused(
                        f'{phase}/ch{ordinal} checkpoint 的 scope 结果与当前切分不同')
                client.restore_checkpoint_acceptance(
                    purpose, restored, system=system, user=user,
                    max_tokens=max_tokens,
                    recovery_policy='stage2-blueprint-v1')
                restored_material[ordinal] = (raw, restored)
            except Exception as exc:
                preflight_failures[ordinal] = exc
        if preflight_failures:
            if checkpoints:
                from novel.llm import stage2_runs
                stage2_runs.clear_delivery_recovery(ctx)
            errors.extend(
                preflight_failures[ordinal]
                for ordinal, _system, _user in concrete
                if ordinal in preflight_failures)
            for ordinal, _system, _user in concrete:
                if ordinal in preflight_failures:
                    exc = preflight_failures[ordinal]
                    tracker.finish(
                        ordinal, outcome='failed',
                        detail=f'{type(exc).__name__}: {str(exc).splitlines()[0]}')
                else:
                    tracker.finish(
                        ordinal, outcome='blocked',
                        detail='同批 checkpoint preflight 未通过，未提交任何付费请求')
            return [], [], errors

    def one(ordinal: int, system: str, user: str) \
            -> tuple[list[dict[str, Any]], dict[str, Any] | None, bool]:
        purpose = (purpose_of(ordinal) if purpose_of
                   else f'{ctx.node}:ch{ordinal}')
        validator = validator_of(ordinal) if validator_of else None
        if ordinal in restored_material:
            raw, restored = restored_material[ordinal]
            ctx.observe(
                'checkpoint_restored', phase=phase, chapter=ordinal,
                attempt_id=restored.get('attempt_id'),
                model=restored.get('model'), wire_calls=0)
            return raw, restored, True
        response = client.ask_json(
            system, user,
            model_cls=model_cls,
            purpose=purpose,
            max_tokens=max_tokens,
            validator=validator,
            progress=tracker.request(ordinal),
            prefer_content_over_reasoning_salvage=(
                prefer_content_over_reasoning_salvage),
            recovery_policy='stage2-blueprint-v1',
        )
        payload = response.model_dump(by_alias=True)
        material = ({
            'response_payload': payload,
            'acceptance': client.accepted_checkpoint(purpose),
            'purpose': purpose,
            'system': system,
            'user': user,
        } if checkpoints else None)
        return payload['blueprints'], material, False

    with ThreadPoolExecutor(
        max_workers=min(MAX_CONCURRENCY, client.concurrency, max(1, len(concrete)))
    ) as pool:
        futures = {
            pool.submit(one, ordinal, system, user): (i, ordinal)
            for i, (ordinal, system, user) in enumerate(concrete, 1)
        }
        for future in as_completed(futures):
            batch, ordinal = futures[future]
            try:
                raw, checkpoint_material, restored = future.result()
                kept, violation = _scope_split(raw, ordinal, batch)
                if kept is not None and checkpoints and not restored:
                    assert checkpoint_material is not None
                    from novel.llm import stage2_runs
                    stage2_runs.write_checkpoint(
                        ctx, phase=phase, chapter=ordinal,
                        purpose=checkpoint_material['purpose'],
                        architecture=architecture,
                        response_model=model_cls,
                        system=checkpoint_material['system'],
                        user=checkpoint_material['user'],
                        response_payload=checkpoint_material['response_payload'],
                        acceptance=checkpoint_material['acceptance'],
                        scope_violation=violation,
                    )
                    checkpointed = getattr(client, 'mark_checkpointed', None)
                    if callable(checkpointed):
                        checkpointed(checkpoint_material['acceptance']['attempt_id'])
            except Exception as exc:  # 已成功的章仍必须保留。
                errors.append(exc)
                request_failures[ordinal] = exc
                tracker.finish(
                    ordinal, outcome='failed',
                    detail=f'{type(exc).__name__}: {str(exc).splitlines()[0]}')
                continue
            if violation is not None:
                violations.append(violation)
            if kept is not None:
                results[ordinal] = kept
                tracker.finish(
                    ordinal, outcome='ok',
                    detail='响应含目标章之外的内容，越界部分已丢弃' if violation else None)
            else:
                tracker.finish(
                    ordinal, outcome='blocked', detail='响应没有请求的目标章')
    # future 完成顺序不承载语义。若把它原样写进 provenance，同一批已验证响应在
    # checkpoint resume 时仅因线程调度不同就会改变 artifact fingerprint，进而把
    # “formal 已落、committed 未落”的幂等收尾误判成新一轮。
    violations.sort(key=lambda row: (
        BlueprintScopeViolation.model_validate_json(row).batch, row))
    if checkpoints:
        from novel.agents.session_client import AgentDeliveryUnavailable
        from novel.llm import stage2_runs
        concrete_chapters = [ordinal for ordinal, _system, _user in concrete]
        recoverable = (
            bool(request_failures)
            and set(results).union(request_failures) == set(concrete_chapters)
            and all(
                isinstance(exc, AgentDeliveryUnavailable)
                and exc.same_run_resumable
                for exc in request_failures.values()
            )
        )
        if recoverable:
            stage2_runs.write_delivery_recovery(
                ctx,
                phase=phase,
                chapters=concrete_chapters,
                completed_chapters=results,
                failures=[{
                    'chapter': ordinal,
                    'error_kind': exc.error_kind,
                    'status': exc.status,
                    'attempts': exc.attempts,
                } for ordinal, exc in request_failures.items()
                if isinstance(exc, AgentDeliveryUnavailable)],
            )
        else:
            # 上次等待态不能让本次无关的 schema/业务失败被 Supervisor 错认成
            # 同一场网络抖动；真实 delivery attempts 已经留在 ledger。
            stage2_runs.clear_delivery_recovery(ctx)
    return [results[o] for o, _s, _u in concrete if o in results], violations, errors


def _raise_response_repair(
        ctx: RunContext, errors: list[Exception], *, architecture: str,
) -> None:
    """并发章全部收尾后，一次打包全部 pending；检查点损坏则关闭式停止。"""
    # 成功章已经在并发收尾时逐章 checkpoint；此处先抛任何非人工 pending。
    # 否则“一个 checkpoint 已坏 + 另一章可修”会先铸死唯一 manifest，诱导人
    # 做一轮注定无法 resume 的 workpack，并把真正的来源损坏藏在后面。
    for error in errors:
        if not isinstance(error, ResponseRepairRequired):
            raise error
    pending = [error for error in errors
               if isinstance(error, ResponseRepairRequired)]
    if pending:
        from novel.llm import stage2_runs
        manifest = stage2_runs.create_repair_manifest(
            ctx, pending, architecture=architecture)
        attempts = tuple(sorted(error.attempt_id for error in pending))
        base = ctx.novel_dir.parent
        hint = (
            'python -X utf8 -u -m novel.cli response-repair '
            f'{ctx.sid} --base "{base}" --batch "{manifest}" '
            '--decisions "<decisions.json>" '
            '--actor agent:novel-pipeline-operator')
        raise ResponseRepairBatchRequired(
            f'{ctx.node}: 同一并发波次有 {len(attempts)} 份已付费响应等待修复；'
            f'已稳定打包为唯一 workpack：{manifest}。'
            '先一次性补齐全部 submit|decline 决定，再用同一 run_id resume。',
            run_id=ctx.run_id, manifest_path=str(manifest),
            attempts=attempts, hint=hint)


def _keep_scope_violation(row: str, targets: set[int]) -> bool:
    try:
        requested = set(BlueprintScopeViolation.model_validate_json(row).requested)
    except (TypeError, ValueError, ValidationError):
        return True
    return requested.isdisjoint(targets)




def _full_commit_payload(
        *, character_projection: dict[str, Any],
        main_text: list[dict[str, Any]],
        candidate: dict[str, Any],
        live_before: dict[str, str | None] | None = None,
) -> dict[str, Any]:
    payload: dict[str, Any] = {
        'operation': 'stage2.blueprint full',
        'character_projection': character_projection,
        'main_text': main_text,
        'artifact': candidate,
    }
    if live_before is not None:
        payload['live_before'] = live_before
    return payload


def _blueprint_live_state(store: ArtifactStore) -> dict[str, str | None]:
    """提交前 formal/INVALID 的全宽字节身份。

    新 journal 不只要证明“要写什么”，还要证明重启后看见的
    live 文件仍是 prepare 时那一份。否则一个第三方人工改动会被
    恢复路当成“formal 还没写”而覆盖。
    """
    state: dict[str, str | None] = {}
    for rel in (BLUEPRINT_REL, _INVALID):
        path = store.path(rel)
        if not path.is_file():
            state[rel] = None
            continue
        try:
            state[rel] = hashlib.sha256(path.read_bytes()).hexdigest()
        except OSError as exc:
            raise ProvenanceRefused(
                f'无法冻结提交前 {rel} 字节：{exc}') from exc
    return state


def _require_blueprint_code_current(ctx: RunContext, *, moment: str) -> None:
    current = prov.code_version()
    if current != ctx.code:
        raise ProvenanceRefused(
            f'stage2.blueprint {moment}时代码版本漂移：'
            f'start={ctx.code} current={current}；拒绝提交')


def _finish_full_commit_tail(
        ctx: RunContext, *, store: ArtifactStore,
        main_text: list[dict[str, Any]],
        character_projection: dict[str, Any],
) -> CheckReport | None:
    """full run 在 prepare 之后崩溃时，用封存候选零 wire 收尾。"""
    from novel.llm import stage2_runs

    journal = stage2_runs.commit_state(ctx)
    if journal is None:
        return None
    if journal.get('artifact') != BLUEPRINT_REL:
        raise ProvenanceRefused(
            f'run={ctx.run_id} 的 commit journal 指向 '
            f'{journal.get("artifact")!r}，不是 {BLUEPRINT_REL}')

    live_artifact: ChapterBlueprints | None = None
    formal_matches = False
    if store.exists(BLUEPRINT_REL):
        # full producer 的正式稿是待替换的 live baseline，不是本次候选的上游。
        # 它本来就可能因为 MainText/称名投影变化而 stale；这里仍走 typed/root
        # 校验，但不能用 require_current 把“重建 stale 产物”自身封死。journal 的
        # candidate、live_before 与当前输入会在下方分别做精确复核。
        live_artifact, _raw = store.read_own(
            ChapterBlueprints, BLUEPRINT_REL)
        live_payload = live_artifact.model_dump(by_alias=True, mode='json')
        formal_matches = stage2_runs.verify_commit_artifact(journal, live_payload)
        if not formal_matches:
            live_artifact = None

    stored_finalization = journal.get('finalization_payload')
    if live_artifact is None:
        if journal.get('state') == 'committed':
            raise ProvenanceRefused(
                f'run={ctx.run_id} 已 committed，但正式 blueprint 缺失或与 journal 不一致')
        if not isinstance(stored_finalization, dict):
            # 历史 hash-only journal 在 formal 尚未落时没有可恢复字节。
            # 保留旧 LlmClient/checkpoint 重编译路，但不在这里猜候选。
            return None
        candidate, stored_finalization = stage2_runs.restore_commit_payloads(
            ctx, journal)
        live_artifact = ChapterBlueprints.model_validate(candidate)
        live_before = stored_finalization.get('live_before')
        if not isinstance(live_before, dict):
            raise ProvenanceRefused(
                f'run={ctx.run_id} 的 prepared journal 缺 live_before；'
                '不能证明恢复写盘不会覆盖第三方改动')
        if _blueprint_live_state(store) != live_before:
            raise ProvenanceRefused(
                f'run={ctx.run_id} prepare 后 formal/INVALID 已漂移；'
                '拒绝用封存候选覆盖')
    candidate = live_artifact.model_dump(by_alias=True, mode='json')
    if isinstance(stored_finalization, dict):
        live_before = stored_finalization.get('live_before')
        if formal_matches and store.exists(_INVALID) and isinstance(live_before, dict):
            invalid_artifact, _invalid_raw = store.read_own(
                ChapterBlueprints, _INVALID, require_current=True)
            invalid_payload = invalid_artifact.model_dump(
                by_alias=True, mode='json')
            invalid_business = {
                key: value for key, value in invalid_payload.items()
                if key != '_provenance'
            }
            candidate_business = {
                key: value for key, value in candidate.items()
                if key != '_provenance'
            }
            invalid_matches_candidate = (
                invalid_business == candidate_business
                and _canonical_sha256(invalid_business)
                == _canonical_sha256(candidate_business)
            )
            current_invalid = _blueprint_live_state(store).get(_INVALID)
            if (not invalid_matches_candidate
                    and current_invalid != live_before.get(_INVALID)):
                raise ProvenanceRefused(
                    f'run={ctx.run_id} 的 formal 已落，但 live {_INVALID} '
                    '不是 prepare 时那一份；拒绝归档第三方改动')
        expected_finalization = _full_commit_payload(
            character_projection=character_projection,
            main_text=main_text, candidate=candidate,
            live_before=(live_before if isinstance(live_before, dict) else None),
        )
    else:
        # 兼容旧 hash-only journal：只有 formal 已精确命中时才会走到这里。
        expected_finalization = _full_commit_payload(
            character_projection=character_projection,
            main_text=main_text, candidate=candidate)
    if not stage2_runs.verify_commit_finalization(journal, expected_finalization):
        raise ProvenanceRefused(
            f'run={ctx.run_id} 的 full 提交尾窗输入或候选已漂移')
    if hits := _blueprint_hard_hits(
            _judge_candidate(None, candidate, main_text)):
        raise ProvenanceRefused(
            f'run={ctx.run_id} 封存的 blueprint 不再通过自身 HARD：{hits}')

    _require_blueprint_code_current(ctx, moment='commit tail 写盘前')
    ctx.require_observed_roots_current()
    if not formal_matches:
        _write_formal(store, BLUEPRINT_REL, live_artifact)
    elif store.exists(_INVALID):
        _retire_invalid_for_matching_formal(
            store, BLUEPRINT_REL, live_artifact)
    report = verify_blueprint_delivery(ctx, live_artifact)
    restored = stage2_runs.restore_commit_adoptions(ctx, journal)
    _note_restored_adoptions(
        ctx, rel=BLUEPRINT_REL, candidate=candidate, adoptions=restored)
    _require_blueprint_code_current(ctx, moment='commit tail 落哨兵前')
    ctx.require_observed_roots_current()
    stage2_runs.mark_committed(ctx)
    return report




def _run_blueprint_repair(ctx, client, **kwargs) -> CheckReport:
    from novel.stages.stage2.plot_repair import run
    return run(ctx, client, **kwargs)


def request_blueprints(ctx: RunContext, client: _BlueprintRequestSource, *,
                       chapters: tuple[int, ...] = (),
                       checkpoints: bool = True) -> dict[str, Any]:
    """按阅读序发出全书首次请求，后章消费已经验证的持续状态。

    不支持跳章首次抽取；定向修订必须走现有蓝图的编辑/返修入口。
    正式 producer 始终写 checkpoint，checkpoint 记录实际下发状态后的请求身份。
    Blueprint 生成前的机械准入由统一 ``novel.orchestrator.mechanical_admission`` 在调度入口判定。
    """
    store = ArtifactStore(ctx)
    main_text = _read_main_text(store)
    assert main_text is not None
    if chapters and tuple(chapters) != tuple(row['chapter_ordinal'] for row in main_text):
        raise ProvenanceRefused('首次抽取必须覆盖全部源章；只修指定章请使用正式定向修订入口')
    character_projection, character_projection_raw = \
        _require_blueprint_character_projection(store)
    character_inventory = blueprint_inventory_from_projection(
        character_projection)
    if checkpoints:
        required_bridge = (
            'json_request_policy',
            'accepted_checkpoint',
            'restore_checkpoint_acceptance',
        )
        missing_bridge = [
            name for name in required_bridge
            if not callable(getattr(client, name, None))
        ]
        if missing_bridge:
            raise ProvenanceRefused(
                'stage2.blueprint 正式请求缺现有 stage2_runs checkpoint bridge '
                f'{missing_bridge}；已在首个 wire 前停止，不能跳过恢复账继续花费')
    spec = _active_spec()
    from novel.consumption import plot_global_projection
    knowledge = store.read(KnowledgeGrounding, GLOBAL_REL).model_dump(
        mode='json', by_alias=True)
    global_projections = {
        str(row['chapter_ordinal']): plot_global_projection(knowledge, row['chapter_ordinal'])
        for row in main_text}
    # prompt / validator / compiler 必须消费同一份冻结投影。此前 build_prompts
    # 会独立再读一次 current，Global 称名在两次读取间变化时会把一个 batch
    # 内部先拼成 P0 validator + P1 prompt，wire 之后又按 P2 编译。
    requests = build_prompts(
        ctx, chapters=chapters,
        character_projection=character_projection,
        main_text=main_text, global_projections=global_projections)
    policy_phases = [('blueprint', spec.response_model, BLUEPRINT_MAX_TOKENS,
                      spec.prefers_content_over_reasoning_salvage)]
    if checkpoints:
        from novel.llm import stage2_runs
        from novel.stages.stage2.plot_inputs import input_digests
        stage2_runs.ensure_run(
            ctx, architecture=spec.architecture, topology=spec.topology,
            requests=requests,
            request_policy=client.json_request_policy(
                policy_phases, recovery_policy='stage2-blueprint-v1'),
            input_projections=input_digests(ctx.title, main_text,
                character_inventory=character_inventory, global_projections=global_projections),
            refresh=ctx.cache.name == 'refresh-write')
    # checkpoint 不是 `.llm_cache`：普通 cache 即使在内部测试里 disabled，
    # 同 run 的已验证章节仍应恢复。只有明确要求新采样的 refresh-write 绕过旧 checkpoint。
    use_checkpoints = checkpoints and ctx.cache.name != 'refresh-write'
    from novel.stages.stage2.plot_sequence import execute
    raw, violations, errors, actual_requests = execute(ctx, client, requests,
        main_text=main_text, character_inventory=character_inventory,
        global_projections=global_projections, checkpoints=checkpoints,
        restore_checkpoints=use_checkpoints)
    _raise_response_repair(ctx, errors, architecture=spec.architecture)
    return {
        'main_text': main_text, 'character_projection': character_projection_raw,
        'global_projections': global_projections, 'requests': actual_requests,
        'raw_blueprints': raw, 'scope_violations': violations, 'errors': errors,
        'contract': spec.architecture,
    }


def _blueprint_batch_sha256(batch: dict[str, Any]) -> str:
    """冻结候选批次；异常只取稳定类型/文字，不把对象身份当成业务输入。"""
    return _canonical_sha256({
        'main_text': batch.get('main_text'),
        'character_projection': batch.get('character_projection'),
        'global_projections': batch.get('global_projections'),
        'requests': batch.get('requests'),
        'raw_blueprints': batch.get('raw_blueprints'),
        'scope_violations': batch.get('scope_violations'),
        'errors': [
            {'type': f'{type(exc).__module__}.{type(exc).__qualname__}',
             'message': str(exc)}
            for exc in (batch.get('errors') or [])
        ],
        'contract': batch.get('contract'),
    })


def _blueprint_feedback(
        outcomes: list[Outcome], hits: tuple[str, ...],
) -> tuple[BlueprintCandidateFeedback, ...]:
    """从唯一 Outcome 投影可路由章节；推不出章号的书级问题保留空 chapters。"""
    rows: list[BlueprintCandidateFeedback] = []
    for rule_id in hits:
        rule = REGISTRY.get(rule_id)
        chapters: set[int] = set()
        findings: list[GlobalCandidateFinding] = []
        seen: set[tuple[str, tuple[str, ...]]] = set()
        for outcome in outcomes:
            if outcome.rule != rule_id:
                continue
            for finding in outcome.findings:
                if finding.output_ordinal is not None:
                    chapters.add(int(finding.output_ordinal))
                raw_chapters = finding.fix_args.get('--chapters')
                if raw_chapters:
                    chapters.update(
                        int(value) for value in raw_chapters.split(',')
                        if value.strip().isdigit() and int(value) > 0)
                identity = (finding.detail, tuple(finding.evidence))
                if identity in seen:
                    continue
                seen.add(identity)
                findings.append(GlobalCandidateFinding(
                    detail=finding.detail,
                    evidence=tuple(finding.evidence),
                ))
        rows.append(BlueprintCandidateFeedback(
            rule=rule_id,
            item=rule.item,
            requirement=rule.requirement,
            chapters=tuple(sorted(chapters)),
            findings=tuple(findings),
        ))
    return tuple(rows)


def _materialize_blueprint_evaluation(
        ctx: RunContext,
        source: _GlobalResponseSource,
        batch: dict[str, Any],
        *, settle: bool,
) -> tuple[
    ChapterBlueprints, list[Outcome], tuple[str, ...],
    tuple[tuple[str, int | None], ...], list[dict[str, Any]], dict[str, Any],
]:
    """同一批响应的唯一 compile/provenance/HARD 口。"""
    from novel.stages.stage2.contract import contract_for

    store = ArtifactStore(ctx)
    main_text = copy.deepcopy(batch['main_text'])
    current_main_text = _read_main_text(store)
    if current_main_text != main_text:
        raise ProvenanceRefused(
            'stage2.blueprint 候选批次绑定的 main_text 与当前盘面不同；'
            '拒绝把旧章节响应编进新正文')
    frozen_projection_raw = batch.get('character_projection')
    try:
        frozen_projection = BlueprintCharacterProjection.model_validate(
            frozen_projection_raw)
    except ValidationError as exc:
        raise ProvenanceRefused(
            'stage2.blueprint 候选批次缺 request-time 强类型称名投影；'
            '拒绝按当前盘面替旧响应补猜输入') from exc
    _current_projection, current_projection_raw = \
        _require_blueprint_character_projection(store)
    if current_projection_raw != frozen_projection_raw:
        raise ProvenanceRefused(
            'stage2.blueprint 发出章节请求后称名投影发生漂移；'
            '拒绝把旧 prompt 响应按新名册编译或记账')
    character_inventory = blueprint_inventory_from_projection(
        frozen_projection)
    requests = batch['requests']
    raw_blueprints = batch['raw_blueprints']
    scope_violations = batch['scope_violations']
    errors = batch['errors']
    spec = contract_for(batch.get('contract') or _active_spec().architecture)
    from novel.consumption import plot_global_projection
    current_knowledge = store.read(KnowledgeGrounding, GLOBAL_REL).model_dump(
        mode='json', by_alias=True)
    frozen_globals = batch.get('global_projections')
    current_globals = {str(row['chapter_ordinal']): plot_global_projection(
        current_knowledge, row['chapter_ordinal']) for row in main_text}
    if frozen_globals != current_globals:
        raise ProvenanceRefused('本轮 Blueprint 的共享主体投影已漂移，拒绝编译旧响应')
    if not raw_blueprints:
        if errors:
            raise errors[0]
        raise TerminalState('stage2.blueprint 没有任何一章返回可接纳的响应')

    data, accounting = spec.compile_fn(
        {'blueprints': raw_blueprints},
        main_text,
        character_inventory=character_inventory,
        global_projections=frozen_globals,
    )
    system = requests[0][1]
    prompt_keys = {spec.sys_name: system}
    ordinals = [int(section.get('chapter_ordinal') or 0)
                for section in main_text]
    produced = sorted({
        int(blueprint.get('chapter_ordinal') or 0)
        for blueprint in raw_blueprints
    })
    purposes: dict[str, int | None] = {f'{ctx.node}:ch{o}': o for o in produced}
    missing_attempts = [
        purpose for purpose in purposes
        if source.accepted_attempt(purpose) is None
    ]
    if missing_attempts:
        raise ProvenanceRefused(
            f'stage2.blueprint 已有编译候选，但这些章节/相位没有 accepted attempt：'
            f'{missing_attempts}；拒绝让部分 adoption 冒充整书来源')
    purpose_rows = tuple(purposes.items())
    provenance = ctx.llm_provenance(
        BlueprintProvenance,
        prompts=prompt_keys,
        model=source.model,
        adopted_models=source.accepted_models(*purposes),
        scope_violations=scope_violations,
        context=blueprint_context(ctx.title, main_text,
                                  architecture=spec.architecture),
        settle=settle,
        **accounting,
    )
    artifact = ChapterBlueprints.model_validate({
        **data, '_provenance': provenance,
    })
    candidate = artifact.model_dump(by_alias=True)
    outcomes = _judge_candidate(None, candidate, main_text)
    return (
        artifact, outcomes, tuple(_blueprint_hard_hits(outcomes)), purpose_rows,
        main_text, frozen_projection_raw,
    )


def evaluate_blueprint_candidate(
        ctx: RunContext,
        source: _GlobalResponseSource,
        batch: dict[str, Any],
) -> BlueprintCandidateEvaluation:
    """编译并评估一批逐章响应；不写 formal/INVALID、不归档、不结算 usage。"""
    if ctx.node != 'stage2.blueprint':
        raise ValueError(
            f'evaluate_blueprint_candidate 只接受 stage2.blueprint，实得 {ctx.node!r}')
    artifact, outcomes, hits, purposes, main_text, character_projection = \
        _materialize_blueprint_evaluation(
            ctx, source, batch, settle=False)
    return BlueprintCandidateEvaluation(
        candidate=artifact,
        hard_hits=hits,
        feedback=_blueprint_feedback(outcomes, hits),
        purposes=purposes,
        accepted_attempts=tuple(
            (purpose, source.accepted_attempt(purpose))
            for purpose, _chapter in purposes
        ),
        requested_model=source.model,
        adopted_models=tuple(sorted(set(source.accepted_models(
            *(purpose for purpose, _chapter in purposes))))),
        error_count=len(batch.get('errors') or []),
        _batch_sha256=_blueprint_batch_sha256(batch),
        _main_text_sha256=_canonical_sha256(main_text),
        _character_projection_sha256=_canonical_sha256(character_projection),
    )


def verify_blueprint_delivery(
        ctx: RunContext,
        expected: ChapterBlueprints,
) -> CheckReport:
    """正式 blueprint 的独立 readback 终验；报告仍由现有唯一判据生成。"""
    _require_blueprint_code_current(ctx, moment='readback 终验开始')
    ctx.require_observed_roots_current()
    store = ArtifactStore(ctx)
    if store.exists(_INVALID):
        raise ProvenanceRefused(
            f'stage2.blueprint 正式提交后仍有 live {_INVALID}；拒绝 delivered')
    with ctx.validation_reads():
        landed, _landed_raw = store.read_own(
            ChapterBlueprints, BLUEPRINT_REL, require_current=True)
        freshness = store.freshness(BLUEPRINT_REL)
        _projection, _projection_raw = \
            _require_blueprint_character_projection(store)
        both = _Both(store)
        outcomes = both.judge()
    expected_raw = expected.model_dump(by_alias=True, mode='json')
    landed_raw = landed.model_dump(by_alias=True, mode='json')
    if (_canonical_sha256(expected_raw) != _canonical_sha256(landed_raw)
            or expected_raw != landed_raw):
        raise ProvenanceRefused(
            'stage2.blueprint 正式 readback 与本次终态候选不等值；拒绝 delivered')
    if freshness.state != 'fresh':
        raise ProvenanceRefused(
            f'stage2.blueprint 正式 readback freshness='
            f'{freshness.state}/{freshness.reason}；拒绝 delivered')
    if hits := _blueprint_hard_hits(outcomes):
        raise ProvenanceRefused(
            f'stage2.blueprint 正式 readback 仍命中自身 HARD {hits}；'
            '拒绝 delivered')
    # 不把入口的代码快照当成整个提交窗的锁：readback/判据
    # 跑完后再核一次，中途漂移就不得声称 delivered。
    _require_blueprint_code_current(ctx, moment='readback 终验结束')
    ctx.require_observed_roots_current()
    return _report(ctx, store, both, outcomes)


def finalize_blueprint_candidate(
        ctx: RunContext,
        source: _GlobalResponseSource,
        batch: dict[str, Any],
        evaluation: BlueprintCandidateEvaluation,
) -> CheckReport:
    """终态只提交一次；通过沿用现有 checkpoint→commit journal→adoption 恢复链。"""
    if ctx.node != 'stage2.blueprint':
        raise ValueError(
            f'finalize_blueprint_candidate 只接受 stage2.blueprint，实得 {ctx.node!r}')
    _require_blueprint_code_current(ctx, moment='executor sessions 终态结算')
    if _blueprint_batch_sha256(batch) != evaluation._batch_sha256:
        raise ProvenanceRefused(
            'stage2.blueprint 候选评估后的 batch 内容发生漂移；拒绝提交')
    if source.model != evaluation.requested_model:
        raise ProvenanceRefused(
            'stage2.blueprint 候选评估后 requested model 漂移；拒绝提交')
    current_attempts = tuple(
        (purpose, source.accepted_attempt(purpose))
        for purpose, _chapter in evaluation.purposes
    )
    if current_attempts != evaluation.accepted_attempts:
        raise ProvenanceRefused(
            'stage2.blueprint 候选评估后 accepted attempts 已变化；'
            '只能提交最后一次完整评估过的章节集合')
    current_models = tuple(sorted(set(source.accepted_models(
        *(purpose for purpose, _chapter in evaluation.purposes)))))
    if current_models != evaluation.adopted_models:
        raise ProvenanceRefused(
            'stage2.blueprint 候选评估后 adopted models 漂移；拒绝猜测来源')

    with ctx.validation_reads():
        store = ArtifactStore(ctx)
        current_main = _read_main_text(store)
        _projection, current_projection = \
            _require_blueprint_character_projection(store)
    if (_canonical_sha256(current_main) != evaluation._main_text_sha256
            or _canonical_sha256(current_projection)
            != evaluation._character_projection_sha256):
        raise ProvenanceRefused(
            'stage2.blueprint 候选评估后 main_text/称名投影输入漂移；'
            '必须新建每章 executor sessions')

    artifact, outcomes, hits, purposes, main_text, character_projection = \
        _materialize_blueprint_evaluation(
            ctx, source, batch, settle=True)
    if purposes != evaluation.purposes or hits != evaluation.hard_hits:
        raise ProvenanceRefused(
            'stage2.blueprint 同一批候选的 provisional/final 目标或 HARD 结论不一致；'
            '拒绝在判据非确定时写盘')

    store = ArtifactStore(ctx)
    candidate = artifact.model_dump(by_alias=True)
    purpose_map = dict(purposes)
    errors = batch.get('errors') or []
    if hits:
        path = store.write_invalid(
            BLUEPRINT_REL, artifact, gates_skipped=[])
        suffix = (
            f'；另有 {len(errors)} 个章节调用失败，已成功返回的付费结果仍保留'
            if errors else '')
        raise GateBlocked(
            f'stage2.blueprint 落盘闸门命中 {list(hits)}{suffix}。问题稿已存 '
            f'{_INVALID}，正式产物没有被覆盖。改好后跑 '
            '`stage2.blueprint --revalidate --actor system:invalid-revalidate --reason <audit>`'
            '（0 LLM、同轮重判并留不可变审计）。',
            invalid_path=str(path),
        )

    # 完整重跑也可能是在一份被拦下的 `.INVALID` 之后发生。成功稿必须像定向修复一样
    # 归档旧正式稿与问题稿；否则 `_Both` / `--check-only` 会继续优先读取旧问题稿，
    # 出现“正式稿已写成功、自动复检却仍判上一轮失败”的幽灵红灯。
    from novel.llm import stage2_runs
    artifact_fp = prov.projection_fingerprint(candidate)
    prior_commit = stage2_runs.committed(ctx)
    existing_matches = False
    invalid_present = store.exists(_INVALID)
    if store.exists(BLUEPRINT_REL):
        # 这是 producer 要覆盖的旧输出，不是它要消费的上游。完整重跑正是
        # stale/consumed_changed 的正式恢复路；要求旧输出 current 会让全部章节
        # 已验并付费后才自相矛盾地拒绝写回。read_own 仍保留 schema、版本、
        # 根身份与字节读取校验，后续 live_before/observed roots 防止并发覆盖。
        existing, _existing_raw = store.read_own(
            ChapterBlueprints, BLUEPRINT_REL)
        existing_matches = existing.model_dump(by_alias=True) == candidate
        # 比的是同一 typed payload。raw 还带 store 注入的 `_schema_version` 信封，
        # candidate 在写盘前没有那一列；拿两者直接哈希会让逐字段完全相同的正式稿
        # 永远判成不同。read_own 已先严格核验信封与载体版本。
    if prior_commit is not None:
        if (prior_commit.get('artifact') != BLUEPRINT_REL
                or prior_commit.get('artifact_fingerprint') != artifact_fp
                or not stage2_runs.verify_commit_artifact(prior_commit, candidate)
                or not existing_matches or invalid_present):
            raise ProvenanceRefused(
                f'run={ctx.run_id} 已有 committed 哨兵，但正式 blueprint 与本次重编译'
                '结果不一致或仍残留 .INVALID；拒绝覆盖或新增归档')
    else:
        # prepare journal 必须先于正式替换：若进程停在 formal 已落、adoption/哨兵
        # 未落的尾窗，下一次同 run 才有一枚不靠当前 live baseline 猜测的候选 SHA。
        live_before = _blueprint_live_state(store)
        stage2_runs.prepare_commit(
            ctx,
            artifact=BLUEPRINT_REL,
            artifact_fingerprint=artifact_fp,
            artifact_payload=candidate,
            finalization_payload=_full_commit_payload(
                character_projection=character_projection,
                main_text=main_text,
                candidate=candidate, live_before=live_before),
            adoptions=_accepted_adoptions(source, purpose_map),
        )
        _require_blueprint_code_current(ctx, moment='正式写盘前')
        ctx.require_observed_roots_current()
        if not existing_matches:
            _write_formal(store, BLUEPRINT_REL, artifact)
        elif invalid_present:
            _retire_invalid_for_matching_formal(
                store, BLUEPRINT_REL, artifact)
    report = verify_blueprint_delivery(ctx, artifact)
    _require_blueprint_code_current(ctx, moment='adoption 前')
    ctx.require_observed_roots_current()
    _note_adopted(ctx, source, rel=BLUEPRINT_REL, candidate=candidate,
                  purposes=purpose_map)
    _require_blueprint_code_current(ctx, moment='committed 哨兵前')
    ctx.require_observed_roots_current()
    stage2_runs.mark_committed(ctx)
    return report


def finish_blueprints(
    ctx: RunContext,
    client: _GlobalResponseSource,
    batch: dict[str, Any],
) -> CheckReport:
    """旧单轮终态入口；agent 循环显式调用 evaluate/finalize 两段。"""
    evaluation = evaluate_blueprint_candidate(ctx, client, batch)
    return finalize_blueprint_candidate(ctx, client, batch, evaluation)


def run_blueprint(
    ctx: RunContext,
    client: LlmClient,
    *,
    chapters: tuple[int, ...] = (),
    review_token: str | None = None,
) -> CheckReport:
    from novel.stages.stage2.plot_repair import finish_commit
    recovered = finish_commit(ctx, chapters=chapters, review_token=review_token)
    if recovered is not None:
        return recovered
    # review_token 形参为恢复重放保留；语义审阅退役后没有登记返工面可读，
    # 定向修订只按显式 --chapters 目标章执行。
    if chapters and review_token is None:
        # 普通整跑/另一轮刷新留下的 run 状态必须在读取任何 producer 输入、更不能
        # 在首个 wire 前拒绝；只有本轮自己登记且已封存唯一 batch resolution 的
        # refresh run 可以继续到详细 subject/request 复核并恢复本轮 checkpoint。
        from novel.llm import stage2_runs
        stage2_runs.preflight_refresh(
            ctx, operation='stage2.blueprint --chapters')
    store = ArtifactStore(ctx)
    main_text = _read_main_text(store)
    assert main_text is not None
    _projection, character_projection = \
        _require_blueprint_character_projection(store)
    if chapters:
        return _run_blueprint_repair(
            ctx,
            client,
            chapters=chapters,
            store=store,
            main_text=main_text,
            character_projection=character_projection,
            review_token=review_token,
        )
    finalized = _finish_full_commit_tail(
        ctx, store=store, main_text=main_text,
        character_projection=character_projection)
    if finalized is not None:
        return finalized
    if store.exists(BLUEPRINT_REL) or store.exists(_INVALID):
        raise TerminalState(
            '已有完整剧情蓝图；不能用首次抽取覆盖主体身份和编辑历史。'
            '真实错漏使用 --chapters <章号> 或 --emit-edit-template/--apply-edit；'
            '已购请求按原 run-id 恢复，代码派生变化使用免费 --revalidate。')
    return finish_blueprints(ctx, client, request_blueprints(ctx, client))


def run_renormalize(ctx: RunContext) -> CheckReport:
    """`--renormalize`：按当前契约重跑确定性归一并把账写回。

    归一改的是模型产物中的代码派生字段，因此保留原 `prompts/model/usage`，
    只换成本次实际读取的 `inputs`，并以 `partial_rewrite=--renormalize` 留痕。
    输入必须已经符合当前 schema；本入口不承担旧产物升级。
    """
    store = ArtifactStore(ctx)
    main_text = _read_main_text(store)
    if main_text is None:
        raise UpstreamMissing(f'{MAIN_TEXT} 不在盘上——无法重跑确定性归一')

    if ctx.node == 'stage2.global':
        from novel.llm import stage2_global_runs
        from novel.stages.stage2.character_rebind import prepare_if_changed

        with stage2_global_runs.mutation_ownership(ctx):
            source_rel = (
                _GLOBAL_INVALID if store.exists(_GLOBAL_INVALID) else GLOBAL_REL)
            if not store.exists(source_rel):
                raise UpstreamMissing(
                    f'{GLOBAL_REL} 与 {_GLOBAL_INVALID} 都不在盘上——'
                    '没有可重新归一的全局底座')
            rebound = prepare_if_changed(store, source_rel)
            if rebound is None:
                previous, raw = grounding_io.read_own(store, source_rel)
            else:
                previous = rebound.previous
                raw = rebound.view.model_dump(mode='json', by_alias=True)
            data = copy.deepcopy(raw)
            data.pop('_provenance', None)
            data, accounting = normalize_global(data, main_text)
            rewritten = ctx.rewrite_provenance(
                previous.provenance, by='--renormalize')
            provenance = GlobalProvenance.model_validate({
                **rewritten.model_dump(),
                **accounting,
                'gates_skipped': None,
                'core_edit': None,
            })
            artifact = GlobalGrounding.model_validate({
                **data, '_provenance': provenance,
            })
            candidate = artifact.model_dump(by_alias=True)
            outcomes = _judge_candidate(candidate, None, main_text, characters=_read_characters(store))
            hits = _hard_hits(outcomes, ignore={'upstream_missing'})
            if rebound is not None:
                rebound.assert_current(store)
            if hits:
                _write_invalid(store, GLOBAL_REL, artifact)
                _note_evidence_locator_corrections(
                    ctx, store, accounting.evidence_locator_corrections,
                    rel=_GLOBAL_INVALID)
                both = _Both(store)
                return _report(
                    ctx, store, both, outcomes,
                    judged_rel=_GLOBAL_INVALID, invalid_rel=_GLOBAL_INVALID)
            _write_formal(store, GLOBAL_REL, artifact)
            _note_evidence_locator_corrections(
                ctx, store, accounting.evidence_locator_corrections,
                rel=GLOBAL_REL)
            both = _Both(store)
            return _report(
                ctx, store, both, both.judge(),
                judged_rel=GLOBAL_REL, invalid_rel=_GLOBAL_INVALID)

    if ctx.node != 'stage2.blueprint':
        raise TerminalState(f'{ctx.node} 不是 stage2 的可重新归一节点')
    from novel.stages.stage2.plot_recovery import renormalize
    return renormalize(ctx, store, main_text)


# ─────────────────────────────────────────────────────────────────────────────
# 四、`--apply-edit`：Stage2 typed facts 的 0 成本人工订正
# ─────────────────────────────────────────────────────────────────────────────

def _manual_locator(
    evidence: dict[str, Any], chapters: dict[int, str], *, subject: str,
) -> dict[str, Any]:
    """把正式 SourceSpan 转回 GlobalEdit 唯一接受的短 locator。"""
    chapter = evidence.get('chapter_ordinal')
    source = evidence.get('source')
    if type(chapter) is not int or not isinstance(source, dict):
        raise TerminalState(
            f'{subject} 没有可回取的正式 evidence；不能生成可执行 GlobalEdit 模板')
    text = chapters.get(chapter)
    literal = source.get('text')
    start = source.get('start')
    end = source.get('end')
    if (not isinstance(text, str) or not isinstance(literal, str)
            or type(start) is not int or type(end) is not int
            or text[start:end] != literal):
        raise TerminalState(f'{subject} 的正式 evidence 坐标与当前 MainText 不闭合')
    if len(literal) <= 160 and text.count(literal) == 1:
        return {'chapter_ordinal': chapter, 'locator': literal}
    # 人工 locator 最长 160 字。优先取含原 span 的上下文窗口；只有章内唯一
    # 命中才交给 Commander，绝不靠“第一个命中”猜证据坐标。
    lo_min = max(0, end - 160)
    lo_max = min(start, max(0, len(text) - 1))
    starts = list(range(lo_min, lo_max + 1, 8))
    if lo_max not in starts:
        starts.append(lo_max)
    for lo in starts:
        candidate = text[lo:min(len(text), lo + 160)]
        if candidate and text.count(candidate) == 1:
            return {'chapter_ordinal': chapter, 'locator': candidate}
    raise TerminalState(
        f'{subject} 找不到 ≤160 字且章内唯一的 locator；拒绝生成会落 INVALID 的模板')


def global_edit_template(ctx: RunContext) -> GlobalEdit:
    """从最新问题稿给 typed no-op 模板；正式稿不再有审阅面派生的模板。"""
    store = ArtifactStore(ctx)
    artifact, raw, source_rel = _global_source(store)
    if source_rel == _GLOBAL_INVALID:
        # 问题稿保留全部可编辑主体，提交仍需真实变化与相同 HARD 校验；
        # 模板读取不创建任何接受证明。
        groups = {'setting'}
        for name, prefix, key in (('relations', 'relation', 'relation_id'),
                                  ('world_rules', 'world_rule', 'rule_id'),
                                  ('arc_secrets', 'secret', 'secret_id')):
            groups.update(prefix + ':' + row[key] for row in raw[name])
            if not raw[name]:
                groups.add('set:' + name)
    else:
        raise TerminalState(
            'Stage2 语义审阅已退役：正式稿不再有审阅面派生的编辑模板；'
            '请直接提交完整 GlobalEdit，或先在问题稿上生成模板')
    edit: dict[str, Any] = {}
    deltas = []
    for name, prefix, key in (('relations', 'relation', 'relation_id'),
                              ('world_rules', 'world_rule', 'rule_id'),
                              ('arc_secrets', 'secret', 'secret_id')):
        if 'set:' + name in groups:
            deltas.append({'collection': name,
                           'expected_collection_sha256': _canonical_sha256(raw[name]), 'operations': []})
        rows = [copy.deepcopy(row) for row in raw[name] if prefix + ':' + row[key] in groups]
        if rows:
            edit[name] = rows
    if 'setting' in groups:
        edit['setting'] = copy.deepcopy(raw['setting'])
    if deltas:
        edit['collection_edits'] = deltas
    if not edit:
        raise TerminalState('当前没有需要 GlobalEdit 的长期知识组；人物问题须派修 stage2.characters')
    return GlobalEdit.model_validate(edit)


def global_edit_template_json(ctx: RunContext) -> str:
    payload = global_edit_template(ctx).model_dump(
        mode='json', by_alias=True, exclude_unset=True)
    return json.dumps(
        encode_artifact(GLOBAL_EDIT, payload),
        ensure_ascii=False, indent=2, sort_keys=True)


def _build_global_edit_failure(
    store: ArtifactStore,
    *,
    failure_kind: str,
    failed_edit_sha256: str,
    formal_sha256: str,
    expected_source_sha256: str | None,
    global_review_workpack_sha256: str | None,
    reason_code: str,
    reason_summary: str,
    candidate_business_sha256: str | None = None,
    invalid_sha256: str | None = None,
    hard_hits: list[str] | None = None,
) -> GlobalEditFailure:
    """构造 reviewed-edit 恢复事实；内容摘要不得携正文/Pydantic input。"""
    return GlobalEditFailure.model_validate({
        'failure_kind': failure_kind,
        'failed_edit_sha256': failed_edit_sha256,
        'formal_sha256': formal_sha256,
        'expected_source_sha256': expected_source_sha256,
        'global_review_workpack_sha256': global_review_workpack_sha256,
        'candidate_business_sha256': candidate_business_sha256,
        'invalid_sha256': invalid_sha256,
        'hard_hits': list(hard_hits or ()),
        'reason_code': reason_code,
        'reason_summary': reason_summary[:512],
        'code_version': store.ctx.code,
    })


def _write_global_edit_failure(
    store: ArtifactStore,
    **kwargs: Any,
) -> GlobalEditFailure:
    """原子替换 reviewed-edit 恢复事实。"""
    failure = _build_global_edit_failure(store, **kwargs)
    store.write_round(GLOBAL_EDIT_FAILURE, failure)
    return failure


def _global_edit_failure_business_sha256(
    failure: GlobalEditFailure,
) -> str:
    """失败的稳定业务身份；raw sidecar SHA 只承担盘面 CAS。

    ``code_version``、自由摘要及 INVALID 的 provenance/raw SHA 都是审计或
    运输字节，重写它们不得凭空返还一个 Commander 席位。
    """
    return _canonical_sha256({
        'failure_kind': failure.failure_kind,
        'failed_edit_sha256': failure.failed_edit_sha256,
        'formal_sha256': failure.formal_sha256,
        'expected_source_sha256': failure.expected_source_sha256,
        'global_review_workpack_sha256': (
            failure.global_review_workpack_sha256),
        'candidate_business_sha256': failure.candidate_business_sha256,
        'reason_code': failure.reason_code,
        'hard_hits': sorted(failure.hard_hits),
    })


def _validated_ordinary_failure_inputs(
    store: ArtifactStore, failure: GlobalEditFailure, *,
    allow_human_invalid: bool = False,
) -> tuple[GlobalEdit, str]:
    """复验普通人工 edit 的 terminal sidecar 与仍在盘上的 exact 结果。"""
    if failure.failure_kind not in {
            'ordinary_hard_gate', 'ordinary_candidate_validation'}:
        raise TerminalState('当前 failure 不是普通 GlobalEdit terminal 事实')
    edit, edit_sha256 = store.read_own_with_sha256(GlobalEdit, GLOBAL_EDIT)
    if edit_sha256 != failure.failed_edit_sha256 or edit.post_edit_review is not None:
        raise TerminalState(
            '普通 GlobalEdit failure 与当前 canonical edit 不匹配')
    if failure.failure_kind == 'ordinary_hard_gate':
        invalid, invalid_sha256 = grounding_io.read_own_with_sha256(store, _GLOBAL_INVALID, require_current=True)
        candidate = invalid.model_dump(mode='json', by_alias=True)
        candidate.pop('_provenance', None)
        if (invalid_sha256 != failure.invalid_sha256
                or _canonical_sha256(candidate)
                != failure.candidate_business_sha256):
            audit = store.revalidation_audit(
                GLOBAL_REL, grounding_io.KnowledgeGrounding,
                actor='system:stage2-terminal-probe',
                reason='只读识别普通 GlobalEdit INVALID 是否由人编辑',
            )
            if not (allow_human_invalid and audit.edited_by_human):
                raise TerminalState(
                    '普通 GlobalEdit HARD failure 与 live INVALID 漂移')
    return edit, edit_sha256


@dataclass(frozen=True)
class _GlobalRevalidationCommitIdentity:
    """Global INVALID 提升时可由 formal journal 接管的 canonical edit 身份。"""

    edit: GlobalEdit
    edit_sha256: str
    reviewed_edit: bool
    formal_sha256: str
    expected_source_sha256: str | None
    global_review_workpack_sha256: str | None
    failure: GlobalEditFailure | None
    failure_sha256: str | None


def _global_revalidation_commit_identity(
    ctx: RunContext, store: ArtifactStore,
) -> _GlobalRevalidationCommitIdentity | None:
    """绑定 edit/formal/workpack；允许 live INVALID 被人编辑后形成新候选。"""
    if not store.exists(GLOBAL_EDIT):
        if store.exists(GLOBAL_EDIT_FAILURE):
            raise TerminalState('GlobalEdit failure 在场但 canonical edit 已缺失')
        return None
    edit, edit_sha256 = store.read_own_with_sha256(GlobalEdit, GLOBAL_EDIT)
    failure: GlobalEditFailure | None = None
    failure_sha256: str | None = None
    if store.exists(GLOBAL_EDIT_FAILURE):
        failure, failure_sha256 = store.read_own_with_sha256(
            GlobalEditFailure, GLOBAL_EDIT_FAILURE)
        if failure.failure_kind == 'hard_gate':
            # reviewed（Commander/Supervisor）失败随 Stage2 语义审阅一并退役：
            # 它的恢复身份绑定已退役的审阅面文件，不再自动接管。
            raise TerminalState(
                'reviewed GlobalEdit failure 的恢复已随 Stage2 语义审阅退役；'
                '请人工修改问题稿后重跑 --revalidate，或提交普通 typed edit')
        if failure.failure_kind == 'ordinary_hard_gate':
            if (failure.failed_edit_sha256 != edit_sha256
                    or edit.post_edit_review is not None):
                raise TerminalState('ordinary revalidate failure/edit 身份漂移')
            return _GlobalRevalidationCommitIdentity(
                edit=edit,
                edit_sha256=edit_sha256,
                reviewed_edit=False,
                formal_sha256=failure.formal_sha256,
                expected_source_sha256=None,
                global_review_workpack_sha256=None,
                failure=failure,
                failure_sha256=failure_sha256,
            )
        raise TerminalState(
            f'{failure.failure_kind} failure 没有 live INVALID revalidate 语义')

    if edit.post_edit_review is not None:
        raise TerminalState(
            'reviewed Global INVALID 缺 typed 失败回执；不得从旧 INVALID 推断恢复身份')
    return None


def _global_edit_input_path(ctx: RunContext, edit_path: str) -> Path:
    """解析并约束 edit 运输路径；文件可暂缺，供 durable replay 对账。"""
    supplied = Path(edit_path) if edit_path else Path(GLOBAL_EDIT)
    if supplied.is_absolute():
        candidate = supplied.resolve()
        book = Path(ctx.novel_dir).resolve()
        service = (paths.service_dir(book.parent) / 'edits' / ctx.sid).resolve()
        if not (candidate.is_relative_to(book) or candidate.is_relative_to(service)):
            raise PathRefused(
                f'GlobalEdit 必须在当前书或 {service} 内：{candidate}')
    else:
        rel = paths.edit_rel(ctx.novel_dir, str(supplied), GLOBAL_EDIT)
        candidate = (Path(ctx.novel_dir) / rel).resolve()
    return candidate


def _global_edit_transport_identity(ctx: RunContext, candidate: Path) -> str:
    """把绝对运输路径收成 book/service 内稳定且不泄漏宿主根的身份。"""
    target = candidate.resolve()
    book = Path(ctx.novel_dir).resolve()
    service = (paths.service_dir(book.parent) / 'edits' / ctx.sid).resolve()
    if target.is_relative_to(book):
        return 'book:' + target.relative_to(book).as_posix()
    if target.is_relative_to(service):
        return 'service:' + target.relative_to(service).as_posix()
    raise PathRefused(f'GlobalEdit 运输路径不属于当前书/SID：{target}')


def _read_global_edit_input(
    ctx: RunContext, store: ArtifactStore, edit_path: str,
) -> tuple[GlobalEdit, Path]:
    """同一 schema 接受书内文件与当前 SID 的 service edit。"""
    candidate = _global_edit_input_path(ctx, edit_path)
    if not candidate.is_file():
        raise TerminalState(
            f'{candidate} 不在盘上。`--apply-edit` 需要当前版本 GlobalEdit')
    return store.read_external(GlobalEdit, GLOBAL_EDIT, candidate), candidate


def _prepare_global_edit_input_commit(
    store: ArtifactStore, *, submitted_edit: GlobalEdit,
    submitted_transport_identity: str, formal_sha256: str,
    expected_source_sha256: str | None,
    global_review_workpack_sha256: str | None,
) -> GlobalEditInputCommit:
    """在改 canonical edit/failure 前落下两目标 WAL。"""
    if store.exists(GLOBAL_EDIT_INPUT_COMMIT):
        raise ProvenanceRefused('上一份 GlobalEdit input commit 尚未补完')
    if store.exists(GLOBAL_EDIT_COMMIT):
        prior_formal = store.read_own(
            GlobalEditCommit, GLOBAL_EDIT_COMMIT)[0]
        if prior_formal.state == 'prepared':
            raise ProvenanceRefused(
                'formal prepared commit 尚未补完；不能准备新 canonical edit')

    reviewed = submitted_edit.post_edit_review is not None
    target_edit_sha256 = store.prospective_sparse_sha256(
        GLOBAL_EDIT, submitted_edit)
    target_failure: GlobalEditFailure | None = None
    target_failure_sha256: str | None = None
    if reviewed:
        if expected_source_sha256 is None or global_review_workpack_sha256 is None:
            raise ProvenanceRefused(
                'reviewed canonical WAL 缺 source/workpack 身份')
        target_failure = _build_global_edit_failure(
            store,
            failure_kind='canonical_pending',
            failed_edit_sha256=target_edit_sha256,
            formal_sha256=formal_sha256,
            expected_source_sha256=expected_source_sha256,
            global_review_workpack_sha256=global_review_workpack_sha256,
            reason_code='canonical_input_persisted',
            reason_summary=(
                'canonical reviewed GlobalEdit 已持久化，formal bundle 尚未提交'),
        )
        target_failure_sha256 = store.prospective_sha256(
            GLOBAL_EDIT_FAILURE, target_failure)

    journal = GlobalEditInputCommit.model_validate({
        'submitted_transport_identity': submitted_transport_identity,
        'reviewed_edit': reviewed,
        'formal_sha256': formal_sha256,
        'expected_source_sha256': expected_source_sha256,
        'global_review_workpack_sha256': global_review_workpack_sha256,
        'baseline_edit_sha256': store.full_sha256_or_none(GLOBAL_EDIT),
        'baseline_failure_sha256': store.full_sha256_or_none(
            GLOBAL_EDIT_FAILURE),
        'baseline_formal_commit_sha256': store.full_sha256_or_none(
            GLOBAL_EDIT_COMMIT),
        'target_edit': submitted_edit.model_dump(
            mode='json', by_alias=True, exclude_unset=True),
        'target_edit_sha256': target_edit_sha256,
        'target_failure': target_failure,
        'target_failure_sha256': target_failure_sha256,
        'code_version': store.ctx.code,
    })
    # journal 路径此刻必须缺失；正式 write 是同目录 os.replace，返回前没有
    # GLOBAL_EDIT/failure mutation，进程硬杀只会得到“无 journal”或完整 journal。
    store.write(GLOBAL_EDIT_INPUT_COMMIT, journal)
    return journal


def _global_edit_input_target(journal: GlobalEditInputCommit) -> GlobalEdit:
    """把落盘 WAL 的 sparse object 统一解回生产 GlobalEdit。"""
    return GlobalEdit.model_validate(journal.target_edit)


def _validate_global_edit_input_commit(
    store: ArtifactStore, journal: GlobalEditInputCommit,
) -> None:
    target_edit = _global_edit_input_target(journal)
    if (store.prospective_sparse_sha256(
            GLOBAL_EDIT, target_edit)
            != journal.target_edit_sha256):
        raise ProvenanceRefused(
            'GlobalEdit input WAL 的 target edit payload/SHA 不互验')
    failure = journal.target_failure
    if failure is None:
        if journal.target_failure_sha256 is not None:
            raise ProvenanceRefused(
                '普通 GlobalEdit WAL 声称不存在 failure 却带 target SHA')
        return
    if (store.prospective_sha256(GLOBAL_EDIT_FAILURE, failure)
            != journal.target_failure_sha256
            or failure.failed_edit_sha256 != journal.target_edit_sha256
            or failure.formal_sha256 != journal.formal_sha256
            or failure.expected_source_sha256 != journal.expected_source_sha256
            or failure.global_review_workpack_sha256
            != journal.global_review_workpack_sha256):
        raise ProvenanceRefused(
            'GlobalEdit input WAL 的 pending failure 与 edit/formal/workpack 漂移')


def _reconcile_global_edit_input_commit(
    store: ArtifactStore, journal: GlobalEditInputCommit,
) -> tuple[GlobalEdit, str]:
    """canonical edit/failure/旧 formal commit 按 baseline/target 三面收敛。

    journal 此处故意不退休：reviewed 由 classified sidecar 或 formal commit 接管，
    普通 edit 由 formal commit 接管。否则 target-only 硬杀会留下看似 current 的旧
    formal，planner 既找不到动作，也可能静默继续下游。
    """
    _validate_global_edit_input_commit(store, journal)
    edit_state = _reviewed_edit_commit_target_state(
        store, GLOBAL_EDIT,
        baseline_sha256=journal.baseline_edit_sha256,
        target_sha256=journal.target_edit_sha256)
    failure_state = _reviewed_edit_commit_target_state(
        store, GLOBAL_EDIT_FAILURE,
        baseline_sha256=journal.baseline_failure_sha256,
        target_sha256=journal.target_failure_sha256)
    formal_commit_state = _reviewed_edit_commit_target_state(
        store, GLOBAL_EDIT_COMMIT,
        baseline_sha256=journal.baseline_formal_commit_sha256,
        target_sha256=None)

    if edit_state == 'baseline':
        store.write_sparse_input(
            GLOBAL_EDIT, _global_edit_input_target(journal))
    if failure_state == 'baseline':
        if journal.target_failure is None:
            if store.exists(GLOBAL_EDIT_FAILURE):
                store.archive_round(GLOBAL_EDIT_FAILURE)
        else:
            store.write_round(GLOBAL_EDIT_FAILURE, journal.target_failure)
    if formal_commit_state == 'baseline' and store.exists(GLOBAL_EDIT_COMMIT):
        current_formal = store.read_own(
            GlobalEditCommit, GLOBAL_EDIT_COMMIT)[0]
        if current_formal.state != 'committed':
            raise ProvenanceRefused(
                'canonical WAL 只能 supersede 已完成的 formal commit')
        store.archive_round(GLOBAL_EDIT_COMMIT)

    if (store.full_sha256_or_none(GLOBAL_EDIT)
            != journal.target_edit_sha256
            or store.full_sha256_or_none(GLOBAL_EDIT_FAILURE)
            != journal.target_failure_sha256
            or store.exists(GLOBAL_EDIT_COMMIT)):
        raise ProvenanceRefused('GlobalEdit input WAL target readback 未闭合')
    current = store.read_own(
        GlobalEditInputCommit, GLOBAL_EDIT_INPUT_COMMIT)[0]
    if current != journal:
        raise ProvenanceRefused('GlobalEdit input WAL 在 handoff 前已漂移')
    canonical, canonical_sha256 = store.read_with_sha256(
        GlobalEdit, GLOBAL_EDIT)
    return canonical, canonical_sha256


def _resume_global_edit_input_commit(
    ctx: RunContext, store: ArtifactStore, *, requested_path: Path,
) -> tuple[GlobalEdit, str] | None:
    if not store.exists(GLOBAL_EDIT_INPUT_COMMIT):
        return None
    journal = store.read_own(
        GlobalEditInputCommit, GLOBAL_EDIT_INPUT_COMMIT)[0]
    requested_identity = _global_edit_transport_identity(ctx, requested_path)
    canonical_identity = 'book:' + GLOBAL_EDIT
    if requested_path.is_file() and requested_identity != canonical_identity:
        requested = store.read_external(
            GlobalEdit, GLOBAL_EDIT, requested_path)
        if requested != _global_edit_input_target(journal):
            raise ProvenanceRefused(
                'GlobalEdit input WAL 的 service 重放 payload 不等于 target edit')
    elif (not requested_path.is_file()
          and requested_identity not in {
              journal.submitted_transport_identity, canonical_identity}):
        raise TerminalState(f'{requested_path} 不在盘上')
    current_failure_sha256 = store.full_sha256_or_none(GLOBAL_EDIT_FAILURE)
    if (not journal.reviewed_edit
            and current_failure_sha256 not in {
                journal.baseline_failure_sha256,
                journal.target_failure_sha256,
            }):
        # 普通 edit 的 terminal sidecar 已落、input WAL 尚未来得及退休。不能在
        # live INVALID 上重施同一 patch（会变成 no-op），而应先复验 exact terminal
        # 事实、关闭 WAL，再原样返回这次动作的 landed/exit2 语义。
        failure = store.read_own(
            GlobalEditFailure, GLOBAL_EDIT_FAILURE)[0]
        if (failure.failed_edit_sha256 != journal.target_edit_sha256
                or failure.formal_sha256 != journal.formal_sha256):
            raise ProvenanceRefused(
                '普通 GlobalEdit input WAL 遇到不属于本动作的 terminal failure')
        _validated_ordinary_failure_inputs(store, failure)
        store.archive_round(GLOBAL_EDIT_INPUT_COMMIT)
        if failure.failure_kind == 'ordinary_hard_gate':
            raise GateBlocked(
                '普通 GlobalEdit 的 HARD terminal 已落稳；input WAL 已幂等关闭，'
                '请提交新的 typed edit 或人工修改 INVALID 后 revalidate',
                invalid_path=str(store.path(_GLOBAL_INVALID)))
        raise TerminalState(
            '普通 GlobalEdit 候选不符合当前 GlobalGrounding schema；'
            'terminal failure 已落稳，请提交新的 typed edit')
    if (journal.reviewed_edit
            and current_failure_sha256 not in {
                journal.baseline_failure_sha256,
                journal.target_failure_sha256,
            }):
        # classified sidecar 已落、WAL 尚未来得及退休的硬杀盘面。它必须仍是
        # 同一 edit/formal/source/workpack 的确定失败；完整复验后退休旧 WAL，
        # 本次 deterministic replay 再走一遍纯计算并返回原失败 exit 语义。
        failure = store.read_own(
            GlobalEditFailure, GLOBAL_EDIT_FAILURE)[0]
        if (failure.failure_kind == 'canonical_pending'
                or failure.failed_edit_sha256 != journal.target_edit_sha256
                or failure.formal_sha256 != journal.formal_sha256
                or failure.expected_source_sha256
                != journal.expected_source_sha256
                or failure.global_review_workpack_sha256
                != journal.global_review_workpack_sha256):
            raise ProvenanceRefused(
                'GlobalEdit input WAL 遇到不属于本动作的 failure 第三态')
        _validated_failure_inputs(ctx, store, failure)
        store.archive_round(GLOBAL_EDIT_INPUT_COMMIT)
        return store.read_with_sha256(GlobalEdit, GLOBAL_EDIT)
    return _reconcile_global_edit_input_commit(store, journal)


def _retire_global_edit_input_commit_after_failure(
    store: ArtifactStore, *, canonical_edit_sha256: str,
) -> None:
    """classified sidecar 已落稳后退休同动作 input WAL。"""
    if not store.exists(GLOBAL_EDIT_INPUT_COMMIT):
        return
    journal = store.read_own(
        GlobalEditInputCommit, GLOBAL_EDIT_INPUT_COMMIT)[0]
    failure = store.read_own(
        GlobalEditFailure, GLOBAL_EDIT_FAILURE)[0]
    if (not journal.reviewed_edit
            or journal.target_edit_sha256 != canonical_edit_sha256
            or failure.failure_kind == 'canonical_pending'
            or failure.failed_edit_sha256 != canonical_edit_sha256
            or failure.formal_sha256 != journal.formal_sha256
            or failure.expected_source_sha256 != journal.expected_source_sha256
            or failure.global_review_workpack_sha256
            != journal.global_review_workpack_sha256):
        raise ProvenanceRefused(
            'classified failure 不能接管不匹配的 GlobalEdit input WAL')
    store.archive_round(GLOBAL_EDIT_INPUT_COMMIT)


def _retire_nonreviewed_global_edit_input_commit(
    store: ArtifactStore, *, canonical_edit_sha256: str,
) -> None:
    """普通 edit 已得到显式 terminal/landed 结论后关闭 action WAL。"""
    if not store.exists(GLOBAL_EDIT_INPUT_COMMIT):
        return
    journal = store.read_own(
        GlobalEditInputCommit, GLOBAL_EDIT_INPUT_COMMIT)[0]
    if (journal.reviewed_edit
            or journal.target_edit_sha256 != canonical_edit_sha256
            or store.full_sha256_or_none(GLOBAL_EDIT) != canonical_edit_sha256):
        raise ProvenanceRefused(
            '普通 GlobalEdit 结论不能退休不匹配的 input WAL')
    failure = store.read_own(
        GlobalEditFailure, GLOBAL_EDIT_FAILURE)[0]
    _validated_ordinary_failure_inputs(store, failure)
    if (failure.failed_edit_sha256 != canonical_edit_sha256
            or failure.formal_sha256 != journal.formal_sha256):
        raise ProvenanceRefused(
            '普通 GlobalEdit terminal sidecar 不能接管不匹配的 input WAL')
    store.archive_round(GLOBAL_EDIT_INPUT_COMMIT)


def _reviewed_edit_commit_target_state(
    store: ArtifactStore, rel: str, *,
    baseline_sha256: str | None,
    target_sha256: str | None,
) -> str:
    """每个 live path 只接受 prepared 时的 baseline 或 journal target。"""
    current = store.full_sha256_or_none(rel)
    if current == target_sha256:
        return 'target'
    if current == baseline_sha256:
        return 'baseline'
    raise ProvenanceRefused(
        f'reviewed-edit prepared commit 的 {rel} 已处于第三方状态：'
        f'current={current} baseline={baseline_sha256} target={target_sha256}')


def _validate_reviewed_edit_commit(
    store: ArtifactStore, journal: GlobalEditCommit,
) -> tuple[GlobalEdit | None, list[dict[str, Any]]]:
    """验证 journal 身份、staging payload 与正式 renderer 的逐字承诺。"""
    edit: GlobalEdit | None = None
    if journal.failed_edit_sha256 is not None:
        edit, edit_sha256 = store.read_own_with_sha256(GlobalEdit, GLOBAL_EDIT)
        if edit_sha256 != journal.failed_edit_sha256:
            raise ProvenanceRefused(
                'GlobalEdit commit journal 与 canonical global_edit.json 漂移')
        attestation = edit.post_edit_review
        if journal.reviewed_edit:
            if attestation is None:
                raise ProvenanceRefused(
                    'reviewed-edit commit journal 绑定的 canonical edit 已无 '
                    'attestation')
            if (attestation.expected_source_sha256
                    != journal.expected_source_sha256
                    or (attestation.expected_workpack_sha256 is not None
                        and attestation.expected_workpack_sha256
                        != journal.global_review_workpack_sha256)):
                raise ProvenanceRefused(
                    'reviewed-edit commit journal 与 edit 的 source/workpack '
                    '身份漂移')
        elif attestation is not None:
            raise ProvenanceRefused(
                '普通 GlobalEdit commit journal 绑定的 canonical edit 意外带 '
                'attestation')
    elif journal.reviewed_edit or journal.action_kind != 'revalidate':
        raise ProvenanceRefused(
            '只有 generic Global INVALID revalidate commit 可以没有 edit 身份')
    if (journal.action_kind == 'revalidate'
            and store.exists(GLOBAL_EDIT_INPUT_COMMIT)):
        raise ProvenanceRefused(
            'GlobalEdit input WAL 尚未交接；拒绝并发准备/恢复 revalidate bundle')
    if store.full_sha256_or_none(MAIN_TEXT) != journal.main_text_sha256:
        raise ProvenanceRefused(
            'reviewed-edit commit prepared 后 MainText 已变化；拒绝重放旧投影')
    main_text = _read_main_text(store)
    if main_text is None:  # pragma: no cover —— 上方 raw SHA 已要求存在
        raise UpstreamMissing(f'{MAIN_TEXT} 不在盘上')
    if journal.action_kind == 'revalidate':
        audit = journal.target.global_grounding.provenance.revalidation_audit
        if audit is None:
            raise ProvenanceRefused(
                'revalidate commit 的 target 未携 typed revalidation audit')

    target = journal.target
    # 审阅已退役：journal 内嵌的 review/review_sha256 只是历史形状兼容格，
    # 不再对应任何盘上文件，也不参与 SHA 互验；只有 Global 主体仍逐字承诺。
    expected_hashes = {
        GLOBAL_REL: store.prospective_sha256(
            GLOBAL_REL, grounding_io.for_write(store, target.global_grounding)),
    }
    recorded_hashes = {
        GLOBAL_REL: target.global_sha256,
    }
    if expected_hashes != recorded_hashes:
        raise ProvenanceRefused(
            'reviewed-edit commit staging payload 与 prospective SHA 不互验')
    return edit, main_text


def _reviewed_edit_commit_readback(
    store: ArtifactStore, journal: GlobalEditCommit,
) -> GlobalGrounding:
    """全部路径与 target 精确闭合后才允许把 prepared 标成 committed。"""
    target = journal.target
    expected = {
        GLOBAL_REL: target.global_sha256,
    }
    got = {rel: store.full_sha256_or_none(rel) for rel in expected}
    if got != expected:
        raise ProvenanceRefused(
            f'reviewed-edit commit readback 未命中完整 target：{got}')
    if store.exists(_GLOBAL_INVALID) or store.exists(GLOBAL_EDIT_FAILURE):
        raise ProvenanceRefused(
            'reviewed-edit commit target readback 后仍有 live INVALID/failure sidecar')
    landed = grounding_io.read_own(store, GLOBAL_REL, require_current=True)[0]
    return landed


def _reconcile_reviewed_edit_commit(
    store: ArtifactStore, journal: GlobalEditCommit,
) -> GlobalGrounding:
    """按 prepared baseline/target 二态逐件收敛；支持硬杀后的任意写点。"""
    _validate_reviewed_edit_commit(store, journal)
    target = journal.target
    baseline = journal.baseline
    states = {
        GLOBAL_REL: _reviewed_edit_commit_target_state(
            store, GLOBAL_REL, baseline_sha256=baseline.global_sha256,
            target_sha256=target.global_sha256),
        _GLOBAL_INVALID: _reviewed_edit_commit_target_state(
            store, _GLOBAL_INVALID, baseline_sha256=baseline.invalid_sha256,
            target_sha256=None),
        GLOBAL_EDIT_FAILURE: _reviewed_edit_commit_target_state(
            store, GLOBAL_EDIT_FAILURE,
            baseline_sha256=baseline.failure_sha256,
            target_sha256=None),
    }

    if states[GLOBAL_REL] == 'baseline':
        store.write_round(GLOBAL_REL, grounding_io.for_write(store, target.global_grounding))
    if store.exists(_GLOBAL_INVALID):
        if store.full_sha256_or_none(_GLOBAL_INVALID) != baseline.invalid_sha256:
            raise ProvenanceRefused(
                'reviewed-edit commit 退休 INVALID 前其 exact SHA 已漂移')
        _retire_invalid_for_matching_formal(
            store, GLOBAL_REL, target.global_grounding)
    if store.exists(GLOBAL_EDIT_FAILURE):
        if (store.full_sha256_or_none(GLOBAL_EDIT_FAILURE)
                != baseline.failure_sha256):
            raise ProvenanceRefused(
                'reviewed-edit commit 退休 failure sidecar 前其 exact SHA 已漂移')
        store.archive_round(GLOBAL_EDIT_FAILURE)

    landed = _reviewed_edit_commit_readback(store, journal)
    current = store.read_own(GlobalEditCommit, GLOBAL_EDIT_COMMIT)[0]
    if current != journal:
        raise ProvenanceRefused(
            'reviewed-edit commit mark committed 前 journal 已漂移')
    if journal.state == 'prepared':
        store.write_round(
            GLOBAL_EDIT_COMMIT,
            journal.model_copy(update={'state': 'committed'}))
    if (journal.action_kind == 'apply_edit'
            and store.exists(GLOBAL_EDIT_INPUT_COMMIT)):
        input_journal = store.read_own(
            GlobalEditInputCommit, GLOBAL_EDIT_INPUT_COMMIT)[0]
        if (input_journal.target_edit_sha256 != journal.failed_edit_sha256
                or input_journal.reviewed_edit != journal.reviewed_edit
                or input_journal.formal_sha256 != journal.formal_sha256):
            raise ProvenanceRefused(
                'formal commit 不能退休不匹配的 GlobalEdit input WAL')
        store.archive_round(GLOBAL_EDIT_INPUT_COMMIT)
    return landed


def submitted_global_patch_is_current(
    base: str, sid: str, payload: dict[str, Any],
) -> bool:
    """确认原 Commander typed 回答仍拥有当前免费提交尾窗，不修改产物。

    正式 Global 换版后原工作包必然过期；只有请求的 sparse edit、首写前
    journal 及全部 live baseline/target 同时互验，才能重放原回答。
    """
    from contracts.schema import decode_artifact
    from novel.orchestrator.state import readonly_ctx

    edit = GlobalEdit.model_validate(decode_artifact(GLOBAL_EDIT, payload).payload)
    if edit.post_edit_review is None:
        return False
    store = ArtifactStore(readonly_ctx(
        paths.resolve_novel_dir(base, sid), 'stage2.global', prov.code_version()))
    if not store.exists(GLOBAL_EDIT_COMMIT):
        return False
    journal = store.read_own(GlobalEditCommit, GLOBAL_EDIT_COMMIT)[0]
    if (journal.action_kind != 'apply_edit' or not journal.reviewed_edit
            or journal.failed_edit_sha256
            != store.prospective_sparse_sha256(GLOBAL_EDIT, edit)):
        return False
    if store.exists(GLOBAL_EDIT_INPUT_COMMIT):
        input_journal = store.read_own(
            GlobalEditInputCommit, GLOBAL_EDIT_INPUT_COMMIT)[0]
        if (store.full_sha256_or_none(GLOBAL_EDIT_COMMIT)
                == input_journal.baseline_formal_commit_sha256):
            return False
        if (input_journal.target_edit_sha256 != journal.failed_edit_sha256
                or input_journal.reviewed_edit != journal.reviewed_edit
                or input_journal.formal_sha256 != journal.formal_sha256):
            raise ProvenanceRefused(
                'Commander 原回答的 GlobalEdit journal 与当前 input WAL 不匹配')
    _validate_reviewed_edit_commit(store, journal)
    if journal.state == 'committed':
        _reviewed_edit_commit_readback(store, journal)
        return True
    baseline, target = journal.baseline, journal.target
    for rel, before, after in (
        (GLOBAL_REL, baseline.global_sha256, target.global_sha256),
        (GLOBAL_REVIEW_REL, baseline.review_sha256, target.review_sha256),
        (_GLOBAL_INVALID, baseline.invalid_sha256, None),
        (GLOBAL_EDIT_FAILURE, baseline.failure_sha256, None),
    ):
        _reviewed_edit_commit_target_state(
            store, rel, baseline_sha256=before, target_sha256=after)
    return True


def _resume_reviewed_edit_commit(
    ctx: RunContext, store: ArtifactStore, *, requested_path: Path,
) -> tuple[GlobalGrounding, CheckReport] | None:
    """在读取 partial formal/review 之前消费 prepared，或证明 committed 重放。"""
    if not store.exists(GLOBAL_EDIT_COMMIT):
        return None
    journal = store.read_own(GlobalEditCommit, GLOBAL_EDIT_COMMIT)[0]
    if journal.action_kind != 'apply_edit':
        if journal.state == 'prepared':
            raise ProvenanceRefused(
                'legacy revalidate prepared commit 尚未补完；拒绝以 apply-edit 接管')
        return None
    if store.exists(GLOBAL_EDIT_INPUT_COMMIT):
        input_journal = store.read_own(
            GlobalEditInputCommit, GLOBAL_EDIT_INPUT_COMMIT)[0]
        formal_journal_sha256 = store.full_sha256_or_none(GLOBAL_EDIT_COMMIT)
        if (formal_journal_sha256
                == input_journal.baseline_formal_commit_sha256):
            # 新 action 的 WAL 以这份 committed journal 为 baseline；先让
            # input reconcile canonicalize 新 edit 并退休旧 journal，不能把 J0/E0
            # 误当成当前 action 成功而形成永久循环。
            return None
        if (journal.failed_edit_sha256 != input_journal.target_edit_sha256
                or journal.reviewed_edit != input_journal.reviewed_edit
                or journal.formal_sha256 != input_journal.formal_sha256):
            raise ProvenanceRefused(
                'GlobalEdit formal journal 既不是 input WAL baseline，'
                '也不是匹配的 prepared successor')
    requested_identity = _global_edit_transport_identity(ctx, requested_path)
    canonical_identity = 'book:' + GLOBAL_EDIT
    same_transport = requested_identity in {
        journal.submitted_transport_identity, canonical_identity}
    canonical_sha256 = store.full_sha256_or_none(GLOBAL_EDIT)
    if (journal.state == 'committed'
            and canonical_sha256 != journal.failed_edit_sha256):
        # 新 action 已经 canonical 化、prepared journal 尚未替换成功时，旧
        # committed 记录只说明上一轮完成；不得拿它验证/吞掉这一轮。
        return None
    if requested_path.is_file():
        requested = store.read_external(
            GlobalEdit, GLOBAL_EDIT, requested_path)
        canonical = store.read_own(GlobalEdit, GLOBAL_EDIT)[0]
        if requested != canonical:
            if journal.state == 'prepared':
                raise ProvenanceRefused(
                    'prepared commit 的重放输入不等于 canonical edit')
            return None
        # uploads 每次会生成新 UUID；只要新 service 载体 typed 语义逐字段等于
        # journal 绑定的 exact canonical edit，就仍是同一动作，不要求随机路径相同。
        same_transport = True
    elif requested_identity != journal.submitted_transport_identity:
        # 只有 journal 记住的精确一次性运输路径允许在清理后缺失；book canonical
        # 路径若缺则下方 typed CAS 必然关闭。
        raise TerminalState(f'{requested_path} 不在盘上')

    if journal.state == 'prepared' and not same_transport:
        raise ProvenanceRefused(
            '另一个 reviewed-edit prepared commit 尚未补完；拒绝接管新 edit')
    if not same_transport:
        return None

    landed = _reconcile_reviewed_edit_commit(store, journal)
    both = _Both(store)
    return landed, _report(
        ctx, store, both, both.judge(),
        judged_rel=GLOBAL_REL, invalid_rel=_GLOBAL_INVALID)


def _resume_global_revalidation_commit(
    ctx: RunContext, store: ArtifactStore, *, actor: str, reason: str,
) -> tuple[bool, CheckReport] | None:
    """消费 legacy revalidate 的 prepared/committed formal bundle。"""
    if not store.exists(GLOBAL_EDIT_COMMIT):
        return None
    journal = store.read_own(GlobalEditCommit, GLOBAL_EDIT_COMMIT)[0]
    if journal.action_kind != 'revalidate':
        if journal.state == 'prepared':
            raise ProvenanceRefused(
                'apply-edit prepared commit 尚未补完；拒绝以 revalidate 接管')
        return None
    audit = journal.target.global_grounding.provenance.revalidation_audit
    if audit is None or audit.actor != actor or audit.reason != reason:
        raise ProvenanceRefused(
            'revalidate prepared commit 只能由原 actor/reason 精确补尾')
    landed = _reconcile_reviewed_edit_commit(store, journal)
    both = _Both(store)
    return True, _report(
        ctx, store, both, both.judge(),
        judged_rel=GLOBAL_REL, invalid_rel=_GLOBAL_INVALID)


def _prepare_reviewed_edit_commit(
    store: ArtifactStore, *, artifact: GlobalGrounding,
    review: GlobalReview, main_text: list[dict[str, Any]],
    canonical_edit_sha256: str | None, formal_sha256: str | None,
    expected_source_sha256: str | None,
    global_review_workpack_sha256: str | None,
    submitted_transport_identity: str,
    action_kind: str = 'apply_edit',
    revalidation_failure_sha256: str | None = None,
) -> GlobalEditCommit:
    """在首个 live companion mutation 前冻结完整、可逐字重放的目标。"""
    reviewed_edit = expected_source_sha256 is not None
    failure_sha256: str | None = None
    if action_kind == 'revalidate':
        if store.exists(GLOBAL_EDIT_INPUT_COMMIT):
            raise ProvenanceRefused(
                'GlobalEdit input WAL 尚未交接；拒绝并发准备 revalidate bundle')
        if reviewed_edit and global_review_workpack_sha256 is None:
            raise ProvenanceRefused(
                'reviewed revalidate commit 缺 workpack 身份')
        if (not reviewed_edit
                and global_review_workpack_sha256 is not None):
            raise ProvenanceRefused(
                'ordinary revalidate commit 不得伪造 reviewed workpack 身份')
        if not store.exists(_GLOBAL_INVALID):
            raise ProvenanceRefused(
                'legacy revalidate commit 准备前 live INVALID 已缺失')
        failure_sha256 = store.full_sha256_or_none(GLOBAL_EDIT_FAILURE)
        if failure_sha256 != revalidation_failure_sha256:
            raise ProvenanceRefused(
                'legacy revalidate commit 的 failure baseline 已漂移')
        if (not reviewed_edit and failure_sha256 is not None
                and canonical_edit_sha256 is None):
            raise ProvenanceRefused(
                'ordinary terminal revalidate commit 必须绑定 canonical edit')
    elif reviewed_edit:
        if global_review_workpack_sha256 is None:
            raise ProvenanceRefused('reviewed-edit commit 缺 workpack 身份')
        failure, failure_sha256 = store.read_own_with_sha256(
            GlobalEditFailure, GLOBAL_EDIT_FAILURE)
        if (failure.failure_kind != 'canonical_pending'
                or failure.failed_edit_sha256 != canonical_edit_sha256
                or failure.formal_sha256 != formal_sha256
                or failure.expected_source_sha256 != expected_source_sha256
                or failure.global_review_workpack_sha256
                != global_review_workpack_sha256):
            raise ProvenanceRefused(
                'reviewed-edit commit 只能从 exact canonical_pending sidecar 准备')
    elif (global_review_workpack_sha256 is not None
          or store.exists(GLOBAL_EDIT_FAILURE)):
        raise ProvenanceRefused(
            '普通 GlobalEdit commit 不得接管 reviewed failure sidecar')
    target = {
        'global_grounding': artifact.model_dump(mode='json', by_alias=True),
        'global_review': review,
        'global_sha256': store.prospective_sha256(GLOBAL_REL, grounding_io.for_write(store, artifact)),
        # 审阅文件已退役：这一格只是 journal 的历史形状兼容格，填内嵌空壳的
        # 稳定身份，不再对应任何盘上文件，也不参与恢复时的 SHA 互验。
        'review_sha256': _retired_review_sha256(review),
    }
    main_text_sha256 = store.full_sha256_or_none(MAIN_TEXT)
    if main_text_sha256 is None:  # pragma: no cover —— caller 已读到 MainText
        raise UpstreamMissing(f'{MAIN_TEXT} 不在盘上')
    journal = GlobalEditCommit.model_validate({
        'state': 'prepared',
        'action_kind': action_kind,
        'reviewed_edit': reviewed_edit,
        'submitted_transport_identity': submitted_transport_identity,
        'failed_edit_sha256': canonical_edit_sha256,
        'formal_sha256': formal_sha256,
        'expected_source_sha256': expected_source_sha256,
        'global_review_workpack_sha256': global_review_workpack_sha256,
        'main_text_sha256': main_text_sha256,
        'baseline': {
            'global_sha256': store.full_sha256_or_none(GLOBAL_REL),
            'review_sha256': store.full_sha256_or_none(GLOBAL_REVIEW_REL),
            'invalid_sha256': store.full_sha256_or_none(_GLOBAL_INVALID),
            'failure_sha256': failure_sha256,
        },
        'target': target,
        'code_version': store.ctx.code,
    })
    # 唯一 write-ahead 点：它返回前，知识/review/INVALID 一个字节都没动。
    store.write_round(GLOBAL_EDIT_COMMIT, journal)
    return journal


_SAFE_GLOBAL_EDIT_REJECTION_CATEGORIES = frozenset({
    'candidate_application',
    'candidate_noop',
    'candidate_normalization',
    'candidate_shape',
    'collection_add_subject_exists',
    'collection_delete_subject_missing',
    'collection_expected_sha_stale',
    'collection_split_output_subject_exists',
    'collection_split_subject_missing',
    'dangling_character_reference',
    'dangling_relation_endpoint',
    'dangling_secret_reference',
    'direct_collection_subject_conflict',
    'invalid_chapter_reference',
    'invalid_fate_evidence_pair',
    'invalid_relation_window',
    'invalid_opening_scope',
    'missing_patch_index',
    'missing_patch_subject',
})
_SAFE_GLOBAL_EDIT_COLLECTIONS = frozenset({
    'relations', 'arc_secrets', 'world_rules',
})


class GlobalEditCandidateRejected(TerminalState):
    """纯内存 GlobalEdit 候选被业务边界拒绝，且对外原因不含 payload。

    ``diagnostics`` 只留在当前异常对象内供本地调试；跨进程日志、席位账与
    correction prompt 只能消费 ``safe_summary``。摘要只发布代码定义的类别、
    数量、可信 collection 名与完整诊断的 canonical SHA，绝不回显模型提供的
    ID、正文、extra key 或 Pydantic ``loc``。collection 维度不能只压成一个
    ``candidate_application:1``：否则下一席不知道该替换哪组 operations。
    """

    def __init__(self, diagnostics: list[dict[str, Any]]) -> None:
        if not diagnostics:
            raise ValueError('GlobalEdit candidate rejection 缺诊断')
        frozen = sorted(
            (copy.deepcopy(row) for row in diagnostics),
            key=lambda row: json.dumps(
                row, ensure_ascii=False, sort_keys=True,
                separators=(',', ':'), allow_nan=False),
        )
        counts: dict[str, int] = {}
        collection_counts: dict[tuple[str, str], int] = {}
        for row in frozen:
            category = row.get('category')
            if category not in _SAFE_GLOBAL_EDIT_REJECTION_CATEGORIES:
                raise ValueError('GlobalEdit candidate rejection 缺稳定 category')
            assert isinstance(category, str)
            counts[category] = counts.get(category, 0) + 1
            collection = row.get('collection')
            if collection is not None:
                if collection not in _SAFE_GLOBAL_EDIT_COLLECTIONS:
                    raise ValueError(
                        'GlobalEdit candidate rejection 含非可信 collection')
                assert isinstance(collection, str)
                key = (collection, category)
                collection_counts[key] = collection_counts.get(key, 0) + 1
        self.diagnostics = tuple(frozen)
        self.diagnostic_sha256 = _canonical_sha256(frozen)
        categories = ','.join(
            f'{category}:{counts[category]}' for category in sorted(counts))
        collection_breakdown = '|'.join(
            f'collection={collection},category={category},count={count}'
            for (collection, category), count in sorted(collection_counts.items())
        )
        self.safe_summary = (
            'Stage2 GlobalEdit 候选被 producer 业务边界拒绝；'
            f'categories={categories}；'
            f'diagnostic_sha256={self.diagnostic_sha256}'
            + (f'；collection_breakdown={collection_breakdown}'
               if collection_breakdown else ''))
        super().__init__(self.safe_summary)


@dataclass(frozen=True)
class _GlobalEditCandidate:
    """同供 preflight 与 canonical producer 消费的无写盘候选。"""

    data: dict[str, Any]
    accounting: GlobalNormalizationAccounting
    changed: list[str]


def _apply_secret_patches(data: dict[str, Any], edit: GlobalEdit,
                           ordinals: set[int], *,
                           characters: dict[str, Any] | None = None) -> list[str]:
    """纯内存应用四类 typed 编辑；集合前置身份先聚合拒因，再原子更新输入。"""
    candidate = copy.deepcopy(data)
    changed: list[str] = []
    keys = {'relations': 'relation_id', 'world_rules': 'rule_id', 'arc_secrets': 'secret_id'}
    diagnostics = []
    for delta in edit.collection_edits:
        name, key = delta.collection, keys[delta.collection]
        current = candidate[name]
        actual = _canonical_sha256(current)
        if actual != delta.expected_collection_sha256:
            diagnostics.append({'category': 'collection_expected_sha_stale', 'collection': name,
                                'actual_collection_sha256': actual,
                                'expected_collection_sha256': delta.expected_collection_sha256})
        existing = {row[key] for row in current}
        for index, operation in enumerate(delta.operations):
            if isinstance(operation, (GlobalCollectionDelete, GlobalCollectionSplit)):
                if operation.subject_id not in existing:
                    diagnostics.append({'category': 'collection_' + operation.op + '_subject_missing',
                                        'collection': name, 'operation_index': index,
                                        'subject_id': operation.subject_id})
            outputs = ([operation.item] if isinstance(operation, GlobalCollectionAdd)
                       else operation.items if isinstance(operation, GlobalCollectionSplit) else [])
            for item in outputs:
                identity = getattr(item, key)
                if identity in existing:
                    diagnostics.append({'category': ('collection_add_subject_exists' if isinstance(operation, GlobalCollectionAdd)
                                                     else 'collection_split_output_subject_exists'),
                                        'collection': name, 'operation_index': index,
                                        'subject_id': identity})
    for name, key in keys.items():
        existing = {row[key] for row in candidate[name]}
        for delta in edit.collection_edits:
            if delta.collection != name:
                continue
            for operation in delta.operations:
                if isinstance(operation, (GlobalCollectionDelete, GlobalCollectionSplit)):
                    existing.discard(operation.subject_id)
                additions = ([operation.item] if isinstance(operation, GlobalCollectionAdd)
                             else operation.items if isinstance(operation, GlobalCollectionSplit) else [])
                existing.update(getattr(row, key) for row in additions)
        for patch in getattr(edit, name):
            removed = {operation.subject_id for delta in edit.collection_edits
                       if delta.collection == name for operation in delta.operations
                       if isinstance(operation, (GlobalCollectionDelete, GlobalCollectionSplit))}
            if getattr(patch, key) in removed:
                diagnostics.append({'category': 'direct_collection_subject_conflict', 'collection': name,
                                    'subject_id': getattr(patch, key)})
                continue
            if getattr(patch, key) not in existing:
                diagnostics.append({'category': 'missing_patch_subject', 'collection': name,
                                    'subject_id': getattr(patch, key)})
    if characters is not None:
        # 引用有效性属于编辑输入预检，不能等 canonical WAL 已写后才报 HARD。
        # 联合 Core 批次传预览人物登记，允许同一决定先新增人物再引用该身份。
        registered = set(characters.get('characters') or {})
        endpoints = {row['relation_id']: copy.deepcopy(row) for row in candidate['relations']}
        if edit.relations_replace is not None:
            endpoints = {row.relation_id: row.model_dump(mode='json') for row in edit.relations_replace}
        for delta in edit.collection_edits:
            if delta.collection != 'relations':
                continue
            for operation in delta.operations:
                if isinstance(operation, (GlobalCollectionDelete, GlobalCollectionSplit)):
                    endpoints.pop(operation.subject_id, None)
                outputs = ([operation.item] if isinstance(operation, GlobalCollectionAdd)
                           else operation.items if isinstance(operation, GlobalCollectionSplit) else [])
                for row in outputs:
                    endpoints[row.relation_id] = row.model_dump(mode='json')
        for patch in edit.relations:
            if patch.relation_id in endpoints:
                endpoints[patch.relation_id].update(patch.model_dump(mode='json', exclude_unset=True))
        for identity, relation in endpoints.items():
            for field in ('subject_id', 'object_id'):
                if relation[field] not in registered:
                    diagnostics.append({'category': 'dangling_relation_endpoint', 'collection': 'relations',
                                        'relation_id': identity, 'field': field, 'target_id': relation[field]})
    if diagnostics:
        raise GlobalEditCandidateRejected(diagnostics)
    for delta in edit.collection_edits:
        name, key = delta.collection, keys[delta.collection]
        for operation in delta.operations:
            if isinstance(operation, GlobalCollectionAdd):
                candidate[name].append(operation.item.model_dump(mode='json', by_alias=True))
            else:
                position = next(index for index, row in enumerate(candidate[name])
                                if row[key] == operation.subject_id)
                replacement = ([row.model_dump(mode='json', by_alias=True) for row in operation.items]
                               if isinstance(operation, GlobalCollectionSplit) else [])
                candidate[name][position:position + 1] = replacement
            changed.append(name + '.' + operation.op)
    for name, key in keys.items():
        replacement = getattr(edit, name + '_replace')
        if replacement is not None:
            candidate[name] = [row.model_dump(mode='json', by_alias=True) for row in replacement]
            changed.append(name)
        else:
            by_id = {row[key]: row for row in candidate[name]}
            for patch in getattr(edit, name):
                value = patch.model_dump(mode='json', by_alias=True, exclude_unset=True)
                identity = value.pop(key)
                by_id[identity].update(value)
                changed.extend(f'{name}.{identity}.{field}' for field in value)
    if edit.setting is not None:
        value = edit.setting.model_dump(mode='json', by_alias=True, exclude_unset=True)
        candidate['setting'].update(value)
        changed.extend('setting.' + key for key in value)
    # 章号不能静默裁剪；这里返回可一次修复的聚合诊断。
    def chapters(value: Any, path: str = ''):
        if isinstance(value, dict):
            for key, child in value.items():
                where = f'{path}.{key}'.strip('.')
                if key in ('effective_from', 'effective_until', 'revealed_from', 'chapter_ordinal'):
                    if child is not None and child not in ordinals:
                        diagnostics.append({'category': 'invalid_chapter_reference', 'path': where})
                elif key == 'setup_chapters':
                    if not set(child) <= ordinals:
                        diagnostics.append({'category': 'invalid_chapter_reference', 'path': where})
                elif key != '_provenance':
                    chapters(child, where)
        elif isinstance(value, list):
            for index, child in enumerate(value):
                chapters(child, f'{path}.{index}')
    chapters(candidate)
    if diagnostics:
        raise GlobalEditCandidateRejected(diagnostics)
    data.clear()
    data.update(candidate)
    return changed


def _build_global_edit_candidate(
    previous: GlobalGrounding,
    raw: dict[str, Any],
    edit: GlobalEdit,
    main_text: list[dict[str, Any]],
    *, characters: dict[str, Any] | None = None,
) -> _GlobalEditCandidate:
    """构造并验证完整业务候选；纯内存且是 apply/preflight 的唯一判据。"""
    previous_business = copy.deepcopy(raw)
    previous_business.pop('_provenance', None)
    data = copy.deepcopy(previous_business)
    ordinals = {
        int(section.get('chapter_ordinal') or 0) for section in main_text
    }
    try:
        changed = _apply_secret_patches(data, edit, ordinals, characters=characters)
    except GlobalEditCandidateRejected:
        raise
    except TerminalState as exc:
        raise GlobalEditCandidateRejected([{
            'category': 'candidate_application',
            'error_type': type(exc).__name__,
            'detail': str(exc),
        }]) from exc
    try:
        data, accounting = normalize_global(data, main_text)
    except (TerminalState, ValueError) as exc:
        # normalize 是纯内存 producer 判据。它明确给出的 TerminalState 属于
        # 候选业务拒绝；OSError/结构性异常等其他失败仍原样上抛为 unknown。
        raise GlobalEditCandidateRejected([{
            'category': 'candidate_normalization',
            'error_type': type(exc).__name__,
            'detail': str(exc),
        }]) from exc
    if data == previous_business:
        raise GlobalEditCandidateRejected([{
            'category': 'candidate_noop',
            'detail': (
                'GlobalEdit 应用并归一后没有改变任何业务字节；拒绝把原样模板'
                '写成一轮“已修复”并重新触发 reviewer'),
        }])

    from contracts.schema.knowledge import KnowledgeCore
    # 这里只借前稿 provenance 验证完整业务形状，不声称新动作已经 canonical；
    # 真正 inputs/by 仍只由 run_apply_edit 在 WAL 之后重写。
    provisional_provenance = previous.provenance.model_dump(
        mode='json', by_alias=True)
    provisional_provenance.update(accounting)
    provisional_provenance['gates_skipped'] = None
    provisional_provenance['core_edit'] = None
    try:
        current_ids = KnowledgeCore.model_validate(data).identity_keys()
        accounting['retired_global_ids'] = sorted(
            set(previous.provenance.retired_global_ids) | (previous.identity_keys() - current_ids))
        provisional_provenance.update(accounting)
        GlobalGrounding.model_validate({
            **data, '_provenance': provisional_provenance})
    except ValidationError as exc:
        # loc/msg 可能含模型提供的动态 dict key 或正文，只进入异常内部诊断
        # 的 canonical SHA；safe_summary 不直接发布其中任何一个字节。
        diagnostics = [{
            'category': 'candidate_shape',
            'type': str(error.get('type') or 'unknown'),
            'loc': [str(part) for part in error.get('loc', ())],
            'message': str(error.get('msg') or ''),
        } for error in exc.errors(
            include_url=False, include_context=False, include_input=False)]
        raise GlobalEditCandidateRejected(diagnostics or [{
            'category': 'candidate_shape',
            'type': 'unknown',
            'loc': [],
            'message': type(exc).__name__,
        }]) from exc
    return _GlobalEditCandidate(
        data=data, accounting=accounting, changed=changed)


def preflight_global_edit(ctx: RunContext, edit: GlobalEdit) -> None:
    """在任何 canonical/正式写盘前，以 producer 唯一候选构建器只读预检。"""
    if ctx.node != 'stage2.global':
        raise TerminalState(f'{ctx.node} 不能预检 Stage2 GlobalEdit')
    store = ArtifactStore(ctx)
    main_text = _read_main_text(store)
    if main_text is None:
        raise UpstreamMissing(f'{MAIN_TEXT} 不在盘上——无法预检 GlobalEdit')
    if store.exists(GLOBAL_EDIT_COMMIT):
        live_commit = store.read_own(
            GlobalEditCommit, GLOBAL_EDIT_COMMIT)[0]
        if live_commit.state == 'prepared':
            raise ProvenanceRefused(
                'reviewed-edit prepared commit 尚未补完；拒绝预检新的 apply-edit')
    _refuse_reviewed_edit(edit)
    previous, raw, _source_rel = _global_source(
        store, replaced_edit_rel=GLOBAL_EDIT)
    _build_global_edit_candidate(previous, raw, edit, main_text, characters=_read_characters(store))


class _PostEditReviewRejected(TerminalState):
    """带稳定 code 的修后复核终态；sidecar 不保存正文/Pydantic input。"""

    def __init__(self, code: str, message: str, *, safe_summary: str | None = None) -> None:
        super().__init__(message)
        self.failure_code = code
        self.safe_summary = (safe_summary or message)[:512]


def _review_after_global_edit(
    artifact: GlobalGrounding,
    previous: GlobalReview | None,
    edit: GlobalEdit,
    chapter_ordinals: list[int],
    *,
    expected_workpack_sha256: str | None = None,
    characters: dict[str, Any] | None = None,
) -> GlobalReview | None:
    """原子合并本轮 edit、新打开项自检与仍存活旧项的 hash-bound 复判。

    旧 v3 attestation 仍按“只证明新 pending”读取。Supervisor 新模板额外绑定
    完整 workpack 与修前 unresolved identity；只有精确存活的旧项可由 decisions
    复判。改过的主体会换 hash，必须走新 pending 自检，不能套用旧决定。
    """
    attestation = edit.post_edit_review
    if attestation is None:
        return None
    if previous is None or previous.source_sha256 != attestation.expected_source_sha256:
        raise _PostEditReviewRejected(
            'source_changed',
            'post_edit_review 绑定的 Stage2 source 已变化；拒绝把旧返工证明应用到新主体')
    if chapter_ordinals != attestation.reviewed_chapter_ordinals:
        raise _PostEditReviewRejected(
            'chapter_scope_changed',
            'post_edit_review 没有覆盖当前完整正文章集合；'
            f'expected={chapter_ordinals} got={attestation.reviewed_chapter_ordinals}',
            safe_summary='post_edit_review 未绑定当前完整正文章集合')

    extended = attestation.expected_workpack_sha256 is not None
    if extended:
        if expected_workpack_sha256 != attestation.expected_workpack_sha256:
            raise _PostEditReviewRejected(
                'workpack_changed',
                'post_edit_review 绑定的完整 workpack 已变化；拒绝应用旧阅读结论')
        expected_scope = [
            (item.item_id, item.subject_sha256)
            for item in previous.items
            if review_effect_of(item.kind) == 'blocking'
            and item.state != 'PASS'
            and quarantined_group(item) is None
        ]
        supplied_scope = [
            (row.item_id, row.expected_subject_sha256)
            for row in attestation.review_scope
        ]
        if supplied_scope != expected_scope:
            raise _PostEditReviewRejected(
                'review_scope_mismatch',
                'post_edit_review.review_scope 未精确覆盖修前全部未闭合 blocking 项')

    payload = artifact.model_dump(mode='json', by_alias=True)
    updated = build_review(payload, previous, characters=characters)
    previous_identities = {
        (item.item_id, item.kind, item.subject_path, item.subject_sha256)
        for item in previous.items
    }
    pending = [
        item for item in updated.items
        if review_effect_of(item.kind) == 'blocking'
        and item.state == 'pending'
        and (item.item_id, item.kind, item.subject_path, item.subject_sha256)
        not in previous_identities
    ]
    quarantine = {
        row.group_key: row for row in attestation.quarantine_groups
    }
    pending_groups = {review_group_key(item.subject_path) for item in pending}
    unknown_groups = sorted(set(quarantine) - pending_groups)
    if unknown_groups:
        raise _PostEditReviewRejected(
            'unknown_quarantine_group',
            'post_edit_review 请求隔离的组不属于本次 edit 新打开的 blocking 面：'
            f'{unknown_groups}',
            safe_summary='post_edit_review 含不属于本次新打开 blocking 面的隔离组')

    if extended:
        identities = {
            (item.item_id, item.subject_sha256): item for item in updated.items
        }
        for decision in attestation.decisions:
            identity = (decision.item_id, decision.expected_subject_sha256)
            if identity not in identities:
                raise _PostEditReviewRejected(
                    'stale_decision_target',
                    'post_edit_review 决定指向已被本 edit 改写或删除的旧主体：'
                    f'{decision.item_id}',
                    safe_summary='post_edit_review 决定指向已改写或删除的旧主体')
            updated = decide_global_review(
                updated,
                item_id=decision.item_id,
                expected_subject_sha256=decision.expected_subject_sha256,
                state=decision.state,
                actor=attestation.actor,
                reason=decision.reason,
                evidence=[row.model_dump(mode='json') for row in decision.evidence],
                generation_disposition=decision.generation_disposition,
            )

    for item in pending:
        group = review_group_key(item.subject_path)
        disposition = None
        state = attestation.default_state
        reason = attestation.reason
        if group in quarantine:
            state = 'UNVER'
            reason = quarantine[group].reason
            disposition = GlobalReviewGenerationDisposition(
                action='quarantine_group', reason=reason)
        updated = decide_global_review(
            updated,
            item_id=item.item_id,
            expected_subject_sha256=item.subject_sha256,
            state=state,
            actor=attestation.actor,
            reason=reason,
            evidence=[],
            generation_disposition=disposition,
        )

    safety = generation_safety(payload, updated, characters=characters)
    if not safety.safe:
        raise _PostEditReviewRejected(
            'blocking_remaining',
            'reviewed GlobalEdit 没有消解全部 blocking 项；拒绝落盘。'
            f'remaining={list(safety.blocking_item_ids)}',
            safe_summary=(
                'reviewed GlobalEdit 仍有未消解 blocking 项；'
                f'count={len(safety.blocking_item_ids)}'))
    return updated


def run_apply_edit(ctx: RunContext, edit_path: str = '') -> tuple[GlobalGrounding, CheckReport]:
    """`--apply-edit`：吃 typed 人工订正，走与生成稿**同一道**归一、判定与闸门。0 成本。

    普通 typed 编辑以最新 INVALID（若有）为底座，允许首抽尚无正式 Global/Review。
    带 post_edit_review 的 Commander 编辑仍须绑定正式底座与已有审阅，不能把无审阅的
    问题稿当作已审来源。全部候选先走统一证据编译与 HARD；通过后以 WAL 一起提交
    Global 和审阅。普通编辑只生成或刷新 pending 义务，不授予语义接受。

    provenance 保留原 model/prompts/adopted_models 和共享首抽身份，更新实际输入并标明
    partial_rewrite。人物由独立 owner 持有；下游仅由实际消费投影判断失效。
    正式与 INVALID 都缺失时显式终止，这条修订路不购买或猜测新底座。
    """
    store = ArtifactStore(ctx)
    if ctx.node != 'stage2.global':
        raise TerminalState(
            f'{ctx.node} 没有 Stage2 全局人工订正入口——`--apply-edit` 挂在 `stage2.global` 上'
            f'（逐章蓝图的人工路是 `--chapters` 定向重生成）')

    from novel.llm import stage2_global_runs

    requested_path = _global_edit_input_path(ctx, edit_path)
    # prepared recovery 必须先于 formal/review 的任何联合读取；partial bundle 正是
    # 这条路要修的盘面，若先走常规 apply 会把可恢复中断误判成 stale/no-op。
    resumed_input: tuple[GlobalEdit, str] | None = None
    with stage2_global_runs.mutation_ownership(ctx):
        # formal journal 是 input WAL 的后继 handoff；两者都在的硬杀盘面必须
        # 先消费后继，否则 input WAL 会把合法 prepared journal 看成第三态。
        resumed = _resume_reviewed_edit_commit(
            ctx, store, requested_path=requested_path)
        if resumed is not None:
            return resumed
        resumed_input = _resume_global_edit_input_commit(
            ctx, store, requested_path=requested_path)

    if resumed_input is None:
        submitted_edit, supplied_path = _read_global_edit_input(
            ctx, store, edit_path)
        # 新提交的 reviewed edit 只由已退役审阅线生产；普通 typed 编辑在
        # 任何 WAL/正式写盘前显式关闭（旧盘 WAL 恢复仍走上面的 resume）。
        _refuse_reviewed_edit(submitted_edit)
    else:
        submitted_edit, _resumed_sha256 = resumed_input
        supplied_path = requested_path

    main_text = _read_main_text(store)
    if main_text is None:
        raise UpstreamMissing(f'{MAIN_TEXT} 不在盘上——人工订正同样要重走归一与判据')
    chapter_ordinals = [
        int(section.get('chapter_ordinal') or 0) for section in main_text
    ]
    with stage2_global_runs.mutation_ownership(ctx):
        if store.exists(GLOBAL_EDIT_COMMIT):
            live_commit = store.read_own(
                GlobalEditCommit, GLOBAL_EDIT_COMMIT)[0]
            if live_commit.state == 'prepared':
                raise ProvenanceRefused(
                    'reviewed-edit prepared commit 尚未补完；拒绝进入新的 apply-edit')
        previous_review = None
        reviewed_formal_sha256: str | None = None
        # reviewed edit 已在入口被 ``_refuse_reviewed_edit`` 关闭；这里只剩
        # 普通 typed 编辑路径，从正式底座取同一次读取的字节与 SHA。
        previous, raw, source_rel = _global_source(
            store, replaced_edit_rel=GLOBAL_EDIT)
        if store.exists(GLOBAL_REVIEW_REL):
            previous_review = store.read_own(GlobalReview, GLOBAL_REVIEW_REL)[0]
        reviewed_formal_sha256 = store.full_sha256_or_none(source_rel)
        if reviewed_formal_sha256 is None:  # pragma: no cover —— source 已读
            raise UpstreamMissing(f'{source_rel} 不在盘上')
        reviewed_workpack_sha256: str | None = None
        prepared_candidate = _build_global_edit_candidate(
            previous, raw, submitted_edit, main_text, characters=_read_characters(store))
        data = prepared_candidate.data
        accounting = prepared_candidate.accounting
        changed = prepared_candidate.changed

        # service edit 只是一次性运输载体。CLI 在确认它确有业务变化后，才把同一
        # typed payload 写进书内唯一输入落点；新 Global 的 provenance 因而永远
        # 绑定可长期复算的 ``stage2/global_edit.json``，不会指向稍后删除的 UUID。
        canonical_path = store.path(GLOBAL_EDIT).resolve()
        reviewed_edit = submitted_edit.post_edit_review is not None
        assert reviewed_formal_sha256 is not None
        if resumed_input is None:
            input_journal = _prepare_global_edit_input_commit(
                store,
                submitted_edit=submitted_edit,
                submitted_transport_identity=_global_edit_transport_identity(
                    ctx, supplied_path),
                formal_sha256=reviewed_formal_sha256,
                expected_source_sha256=(
                    submitted_edit.post_edit_review.expected_source_sha256
                    if reviewed_edit else None),
                global_review_workpack_sha256=(
                    reviewed_workpack_sha256 if reviewed_edit else None),
            )
            canonical_edit, canonical_edit_sha256 = (
                _reconcile_global_edit_input_commit(store, input_journal))
        else:
            canonical_edit, canonical_edit_sha256 = store.read_with_sha256(
                GlobalEdit, GLOBAL_EDIT)
        if canonical_edit != submitted_edit:  # pragma: no cover —— store round-trip 已守
            raise ProvenanceRefused('Stage2 canonical GlobalEdit 写回后字节语义漂移')

        rewritten = ctx.rewrite_provenance(
            previous.provenance, by='--apply-edit')
        provenance = GlobalProvenance.model_validate({
            **rewritten.model_dump(),
            **accounting,
            'gates_skipped': None,
                'core_edit': None,
        })
        try:
            artifact = GlobalGrounding.model_validate({
                **data, '_provenance': provenance})
        except ValidationError as exc:
            failure = TerminalState(
                '人工 GlobalEdit 应用后的完整候选不符合当前 KnowledgeGrounding v7；'
                '字段位置可从异常链诊断，恢复事实不会持久化 input_value/正文')
            if reviewed_edit:
                locations = sorted({
                    '.'.join(str(part) for part in error.get('loc', ()))
                    for error in exc.errors(include_input=False)
                })
                _write_global_edit_failure(
                    store,
                    failure_kind='candidate_validation',
                    failed_edit_sha256=canonical_edit_sha256,
                    formal_sha256=reviewed_formal_sha256,
                    expected_source_sha256=(
                        submitted_edit.post_edit_review.expected_source_sha256),
                    global_review_workpack_sha256=reviewed_workpack_sha256,
                    candidate_business_sha256=_canonical_sha256(data),
                    reason_code='candidate_schema_invalid',
                    reason_summary=(
                        'GlobalEdit 候选不符合 KnowledgeGrounding v7；fields='
                        + ','.join(locations[:12]))[:512],
                )
                _retire_global_edit_input_commit_after_failure(
                    store, canonical_edit_sha256=canonical_edit_sha256)
            else:
                _write_global_edit_failure(
                    store,
                    failure_kind='ordinary_candidate_validation',
                    failed_edit_sha256=canonical_edit_sha256,
                    formal_sha256=reviewed_formal_sha256,
                    expected_source_sha256=None,
                    global_review_workpack_sha256=None,
                    candidate_business_sha256=_canonical_sha256(data),
                    reason_code='candidate_schema_invalid',
                    reason_summary=(
                        '普通 GlobalEdit 候选不符合 KnowledgeGrounding v7'),
                )
                _retire_nonreviewed_global_edit_input_commit(
                    store, canonical_edit_sha256=canonical_edit_sha256)
            raise failure from exc

        candidate = artifact.model_dump(by_alias=True)
        outcomes = _judge_candidate(candidate, None, main_text, characters=_read_characters(store))
        hits = _hard_hits(outcomes, ignore={'upstream_missing'})
        if hits:
            failure_rels = (
                _GLOBAL_INVALID, GLOBAL_EDIT_FAILURE)
            with store.history_transaction(*failure_rels):
                path = _write_invalid(
                    store, GLOBAL_REL, artifact,
                    owned_global_edit_sha256=canonical_edit_sha256)
                if reviewed_edit:
                    invalid_sha256 = store.full_sha256_or_none(_GLOBAL_INVALID)
                    if invalid_sha256 is None:  # pragma: no cover —— 写口后置条件
                        raise ProvenanceRefused(
                            'reviewed GlobalEdit HARD 已返回路径却没有 live INVALID')
                    _write_global_edit_failure(
                        store,
                        failure_kind='hard_gate',
                        failed_edit_sha256=canonical_edit_sha256,
                        formal_sha256=reviewed_formal_sha256,
                        expected_source_sha256=(
                            submitted_edit.post_edit_review.expected_source_sha256),
                        global_review_workpack_sha256=reviewed_workpack_sha256,
                        candidate_business_sha256=_canonical_sha256({
                            key: value for key, value in candidate.items()
                            if key != '_provenance'
                        }),
                        invalid_sha256=invalid_sha256,
                        hard_hits=hits,
                        reason_code='canonical_hard_gate',
                        reason_summary=(
                            'reviewed GlobalEdit 被 canonical HARD 拦截；rules='
                            + ','.join(hits))[:512],
                    )
                else:
                    invalid_sha256 = store.full_sha256_or_none(_GLOBAL_INVALID)
                    if invalid_sha256 is None:  # pragma: no cover —— 写口后置条件
                        raise ProvenanceRefused(
                            '普通 GlobalEdit HARD 已返回路径却没有 live INVALID')
                    _write_global_edit_failure(
                        store,
                        failure_kind='ordinary_hard_gate',
                        failed_edit_sha256=canonical_edit_sha256,
                        formal_sha256=reviewed_formal_sha256,
                        expected_source_sha256=None,
                        global_review_workpack_sha256=None,
                        candidate_business_sha256=_canonical_sha256({
                            key: value for key, value in candidate.items()
                            if key != '_provenance'
                        }),
                        invalid_sha256=invalid_sha256,
                        hard_hits=hits,
                        reason_code='canonical_hard_gate',
                        reason_summary=(
                            '普通 GlobalEdit 被 canonical HARD 拦截；rules='
                            + ','.join(hits))[:512],
                    )
            if reviewed_edit:
                _retire_global_edit_input_commit_after_failure(
                    store, canonical_edit_sha256=canonical_edit_sha256)
            else:
                _retire_nonreviewed_global_edit_input_commit(
                    store, canonical_edit_sha256=canonical_edit_sha256)
            _note_evidence_locator_corrections(
                ctx, store, accounting.evidence_locator_corrections,
                rel=_GLOBAL_INVALID)
            raise GateBlocked(
                f'人工 Stage2 订正被落盘闸门拦下：{hits}（改动 {changed}）。'
                f'问题稿在 {path}，**正式全书底座没有被覆盖**；'
                f'改完编辑文件重跑 `--apply-edit`，'
                f'或直接改 `.INVALID` 后跑 '
                f'`{ctx.node} --revalidate --actor system:invalid-revalidate --reason <audit>`'
                f'（0 LLM、同轮重判并留不可变审计）',
                invalid_path=path)

        try:
            reviewed = _review_after_global_edit(
                artifact, previous_review, submitted_edit, chapter_ordinals,
                expected_workpack_sha256=reviewed_workpack_sha256, characters=_read_characters(store))
        except TerminalState as exc:
            if reviewed_edit:
                if isinstance(exc, _PostEditReviewRejected):
                    reason_code = exc.failure_code
                    reason_summary = exc.safe_summary
                else:  # helper 的显式终态也不能把 canonical edit 丢掉。
                    reason_code = 'post_edit_review_rejected'
                    reason_summary = (
                        'post_edit_review 在 formal bundle 提交前被关闭式拒绝')
                _write_global_edit_failure(
                    store,
                    failure_kind='post_edit_review',
                    failed_edit_sha256=canonical_edit_sha256,
                    formal_sha256=reviewed_formal_sha256,
                    expected_source_sha256=(
                        submitted_edit.post_edit_review.expected_source_sha256),
                    global_review_workpack_sha256=reviewed_workpack_sha256,
                    candidate_business_sha256=_canonical_sha256({
                        key: value for key, value in candidate.items()
                        if key != '_provenance'
                    }),
                    reason_code=reason_code,
                    reason_summary=reason_summary,
                )
                _retire_global_edit_input_commit_after_failure(
                    store, canonical_edit_sha256=canonical_edit_sha256)
            raise
        assert reviewed_formal_sha256 is not None
        # Stage2 语义审阅退役：journal 的 global_review/review_sha256 两格是
        # 空壳兼容格（恒空项集，绑定候选业务身份），不再表达任何语义接受。
        attestation = submitted_edit.post_edit_review
        target_review = _retired_review_surface(artifact)
        journal = _prepare_reviewed_edit_commit(
            store,
            artifact=artifact,
            review=target_review,
            main_text=main_text,
            canonical_edit_sha256=canonical_edit_sha256,
            formal_sha256=reviewed_formal_sha256,
            expected_source_sha256=(
                None if attestation is None
                else attestation.expected_source_sha256),
            global_review_workpack_sha256=reviewed_workpack_sha256,
            submitted_transport_identity=_global_edit_transport_identity(
                ctx, supplied_path),
        )
        artifact = _reconcile_reviewed_edit_commit(store, journal)
        _note_evidence_locator_corrections(
            ctx, store, accounting.evidence_locator_corrections,
            rel=GLOBAL_REL)
        both = _Both(store)
        return artifact, _report(
            ctx, store, both, both.judge(),
            judged_rel=GLOBAL_REL, invalid_rel=_GLOBAL_INVALID)


def preview_edit(ctx: RunContext, edit: GlobalEdit) -> dict[str, Any]:
    """Global typed 编辑的独立只读预检；与 apply 共用同一候选判据。

    不归档、不写 INVALID、不记费用、不领 owner；返回的摘要是**预览**，
    不是预授权——apply 会以同一判据现场重判，期间基线变化时以重判为准。
    """
    if ctx.node != 'stage2.global':
        raise TerminalState(
            f'{ctx.node} 没有 Stage2 全局编辑预检入口——预检挂在 `stage2.global` 上')
    _refuse_reviewed_edit(edit)
    store = ArtifactStore(ctx)
    main_text = _read_main_text(store)
    if main_text is None:
        raise UpstreamMissing(f'{MAIN_TEXT} 不在盘上——预检与 apply 使用同一底座')
    previous, raw, _source_rel = _global_source(
        store, replaced_edit_rel=GLOBAL_EDIT)
    prepared = _build_global_edit_candidate(
        previous, raw, edit, main_text, characters=_read_characters(store))
    candidate = GlobalGrounding.model_validate({
        **prepared.data, '_provenance': previous.provenance})
    outcomes = _judge_candidate(
        candidate.model_dump(by_alias=True), None, main_text,
        characters=_read_characters(store))
    return {
        'result_subject_sha256': _canonical_sha256(prepared.data),
        'changed': list(prepared.changed),
        'hard_hits': _hard_hits(outcomes, ignore={'upstream_missing'}),
    }


def _characters_business_sha256(registry: CharacterRegistry) -> str:
    raw = registry.model_dump(mode='json', by_alias=True)
    raw.pop('_provenance')
    return _canonical_sha256(raw)


def _require_same_core_main(store: ArtifactStore, previous: KnowledgeGrounding) -> list[dict[str, Any]]:
    """免费批次只允许人物依赖重绑，不能顺带追认已改变的正文。"""
    main = store.read(MainText, MAIN_TEXT).model_dump(mode='json', by_alias=True)
    old, current = previous.provenance.inputs.get(MAIN_TEXT), store.ctx.edges.get(MAIN_TEXT)
    if old is None or current is None or old.consumed is None or old.consumed != current.consumed:
        raise ProvenanceRefused('Global core 修订缺少同一 MainText 的实际输入证明')
    return main


def preflight_core_edit(
    ctx: RunContext, edit: GlobalEdit | None, *, registry: CharacterRegistry,
    expected_global_sha256: str,
):
    """跨 owner 批次先冻结业务目标；未来人物未落盘时不伪造其 InputEdge。"""
    from contracts.schema.knowledge import KnowledgeCore
    if ctx.node != 'stage2.global':
        raise ProvenanceRefused('Global core 预检只能由所属 producer 执行')
    store = ArtifactStore(ctx)
    previous, sha = store.read_own_with_sha256(KnowledgeGrounding, GLOBAL_REL)
    if sha != expected_global_sha256:
        raise ProvenanceRefused('Global core 预检基线已改变')
    main = _require_same_core_main(store, previous)
    raw = previous.model_dump(mode='json', by_alias=True)
    if edit is None:
        data = {key: value for key, value in raw.items() if key != '_provenance'}
    else:
        if edit.post_edit_review is not None:
            raise ProvenanceRefused('跨 owner 批次不把旧 GlobalEdit attestation 当作新主体接受')
        data = _build_global_edit_candidate(
            previous, raw, edit, main, characters=registry.model_dump(mode='json', by_alias=True)).data
    data['characters_reference_sha256'] = grounding_io.knowledge_reference_sha256(registry)
    core = KnowledgeCore.model_validate(data)
    outcomes = checks.check_global(core.model_dump(mode='json'), main,
                                    characters=registry.model_dump(mode='json', by_alias=True))
    hits = _hard_hits(outcomes)
    if hits:
        raise GlobalEditCandidateRejected([{'category': 'core_hard_gate', 'rule': rule} for rule in hits])
    if store.full_sha256_or_none(GLOBAL_REL) != expected_global_sha256:
        raise ProvenanceRefused('Global core 预检期间基线漂移')
    return core


def commit_core_edit(
    ctx: RunContext, business_candidate, *, expected_global_sha256: str,
    expected_characters_business_sha256: str, actor: str, reason: str, request_sha256: str,
) -> GlobalGrounding:
    """外层批次 WAL 的 Global 落点：CAS 或同请求精确幂等，真实输入落稳后才生成来源账。"""
    from contracts.schema.knowledge import GlobalCoreEditReceipt, KnowledgeCore
    from novel.llm import stage2_global_runs
    core = KnowledgeCore.model_validate(business_candidate.model_dump(mode='json')
        if isinstance(business_candidate, BaseModel) else business_candidate)
    receipt = GlobalCoreEditReceipt(actor=actor, reason=reason, source_sha256=expected_global_sha256,
        request_sha256=request_sha256, characters_business_sha256=expected_characters_business_sha256)
    store = ArtifactStore(ctx)
    with stage2_global_runs.mutation_ownership(ctx):
        registry = grounding_io.read_registry(store)
        if _characters_business_sha256(registry) != expected_characters_business_sha256:
            raise ProvenanceRefused('Global core 提交的人物业务目标与批次冻结候选不同')
        if core.characters_reference_sha256 != grounding_io.knowledge_reference_sha256(registry):
            raise ProvenanceRefused('Global core 目标没有绑定当前真实人物投影')
        previous, current_sha = store.read_own_with_sha256(KnowledgeGrounding, GLOBAL_REL)
        main = _require_same_core_main(store, previous)
        from contracts.artifacts import STAGE2_CHARACTERS
        frozen = {rel: store.full_sha256_or_none(rel) for rel in (MAIN_TEXT, STAGE2_CHARACTERS, GLOBAL_REL)}
        hits = _hard_hits(checks.check_global(core.model_dump(mode='json'), main,
            characters=registry.model_dump(mode='json', by_alias=True)))
        if hits:
            raise ProvenanceRefused(f'Global core 冻结候选复验仍有 HARD：{hits}')
        if current_sha != expected_global_sha256:
            previous_core = KnowledgeCore.model_validate({key: value for key, value in
                previous.model_dump(mode='json', by_alias=True).items() if key != '_provenance'})
            if previous.provenance.core_edit != receipt or previous_core != core:
                raise ProvenanceRefused('Global core 既非原基线，也不是同请求已提交目标')
            return previous
        provenance = ctx.rewrite_provenance(previous.provenance, by='--apply-edit')
        provenance = provenance.model_copy(update={'gates_skipped': None, 'core_edit': receipt,
            'evidence_locator_failures': [], 'retired_global_ids': sorted(
                set(previous.provenance.retired_global_ids) | (previous.identity_keys() - core.identity_keys()))})
        target = GlobalGrounding.model_validate({**core.model_dump(mode='json'), '_provenance': provenance})
        if frozen != {rel: store.full_sha256_or_none(rel) for rel in frozen}:
            raise ProvenanceRefused('Global core 提交前真实输入漂移')
        _write_formal(store, GLOBAL_REL, target)
        landed = store.read_own(KnowledgeGrounding, GLOBAL_REL, require_current=True)[0]
        if landed != target:
            raise ProvenanceRefused('Global core 正式写回不等于批次目标')
        return landed
