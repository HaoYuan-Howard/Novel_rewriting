#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""`stage2.global` 与 `stage2.blueprint` 的当前生成、归一和判定路径。

两类产物都以 ``checks.check()`` 为唯一判定入口。global 模型响应只引用关闭的
段落候选 ID，由代码直接铸成 ``SourceSpan``；人工编辑与存量 ``.INVALID`` 恢复
仍可使用 locator，但不属于 provider wire。
"""
from __future__ import annotations

from contracts.artifacts import (
    STAGE1_MAIN_TEXT, STAGE2_BLUEPRINT_CHARACTER_PROJECTION,
    STAGE2_CHAPTER_BLUEPRINTS, STAGE2_GLOBAL_EDIT, STAGE2_GLOBAL_GROUNDING,
    STAGE2_GLOBAL_REVIEW,
)

import copy
import hashlib
import json
import re
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import dataclass, field
from typing import Any, Callable, Protocol

from pydantic import BaseModel, ValidationError

from contracts.invariants import BLUEPRINT_ARCHITECTURE
from contracts.rules import REGISTRY
from contracts.schema import ArtifactVersionError
from contracts.schema.base import StrictModel
from contracts.schema.report import CheckReport
from contracts.schema.evaluation import GlobalReview
from contracts.schema.stage1 import MainText
from contracts.schema.stage2 import (
    BlueprintCharacterProjection, BlueprintProvenance, BlueprintScopeViolation,
    ChapterBlueprints,
    GlobalCandidateAgentTurnResponse, GlobalCandidateFullTurn,
    GlobalEdit,
    GlobalGrounding, GlobalGroundingCandidateResponse,
    GlobalProvenance, GlobalRoleFatePatchTurn,
    RoleSplitResponse,
)
from novel import paths, prov
from novel.config import MAX_CONCURRENCY
from novel.errors import (
    ArtifactUnreadable, GateBlocked, ProvenanceRefused,
    ResponseRepairBatchRequired, ResponseRepairRequired, TerminalState,
    UpstreamMissing,
)
from novel.llm.cache import CachePolicy
from novel.llm.client import LlmClient
from novel.llm.ledger import combine_usage
from novel.llm.prompts import required_top_level, with_json_shape
from novel.observability import ChapterProgressTracker
from novel.quality.global_review import build_review, require_current as require_current_review
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
    GlobalEvidenceCandidateTable, compile_candidate_evidence,
)
from novel.stages.stage2 import global_role_fate_patch
from novel.stages.stage2.normalize import (
    EvidenceLocatorCorrection,
    normalize_blueprints, normalize_global,
    restore_failed_global_evidence_locators,
)
from novel.stages.stage2.paragraphs import paragraph_spans, render_numbered
from novel.stages.stage2.references import (
    hinge_candidates_by_chapter, prompt_candidates,
)
from novel.store import ArtifactStore

GLOBAL_REL = STAGE2_GLOBAL_GROUNDING
GLOBAL_REVIEW_REL = STAGE2_GLOBAL_REVIEW
BLUEPRINT_REL = STAGE2_CHAPTER_BLUEPRINTS
BLUEPRINT_CHARACTER_REL = STAGE2_BLUEPRINT_CHARACTER_PROJECTION
MAIN_TEXT = STAGE1_MAIN_TEXT

#: `--apply-edit` 的缺省决策集路径。形状是契约（`ARTIFACTS` 里登记着 `GlobalEdit`）。
GLOBAL_EDIT = STAGE2_GLOBAL_EDIT

#: 被闸门拦下的那一份。**从 `REL` 派生，不手写第二个字符串。**
_INVALID = paths.invalid_of(BLUEPRINT_REL)
_GLOBAL_INVALID = paths.invalid_of(GLOBAL_REL)

GLOBAL_MAX_TOKENS = 96_000
BLUEPRINT_MAX_TOKENS = 256_000
BOUNDARY_MAX_TOKENS = 64_000
ROLE_MAX_TOKENS = 32_000
#: 三个 v2 上下文预算（2026-08-14 用 167 本语料 2612 章实测标定，脚本不入仓）：
#: 4000 字邻章尾窗对 99.5% 的章是整章（章长 P50=795 / P90=1422 / P99=2695；
#: 收到 2000 只剩 97.2%，不值得省）；1600/240 尾摘只够 ~7 个更早章、86.8% 的书
#: 盖不全更早章——**这是设计而非缺口**，更早章的名字连续性由已现称名表接住；
#: 600 字称名表预算对三本真书末章实测 46–195 字，约 3 倍余量。
PAST_NEIGHBOR_CHARS_MAX = 4_000
CONTINUITY_CHARS_MAX = 1_600
PAST_NAMES_CHARS_MAX = 600


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
    _main_text: tuple[dict[str, Any], ...] = field(repr=False)
    _downstream: _GlobalDownstreamGuard = field(repr=False)
    _contract: GlobalEvidenceContractSpec = field(repr=False)
    _evidence_candidates: GlobalEvidenceCandidateTable = field(repr=False)

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
    accepted_role_patch_count: int


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

    def judge(self) -> list[Outcome]:
        """跑全部注册判据。**唯一判定落点**——落盘闸门与复检共用它。"""
        return checks.check(global_g=self.global_g, blueprints=self.blueprints,
                            main_text=self.main_text)


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
    _art, raw = store.read_own(model, target)
    return raw


def _older_version_error(exc: ArtifactUnreadable) -> ArtifactVersionError | None:
    """只识别“盘上确实是低版本”这一种不可读；其余坏载体继续关闭式失败。"""
    cause = exc.__cause__
    if isinstance(cause, ArtifactVersionError) and cause.is_older_than_current:
        return cause
    return None


def _read_main_text(store: ArtifactStore) -> list[dict[str, Any]] | None:
    if not store.exists(MAIN_TEXT):
        return None
    art = store.read(MainText, MAIN_TEXT)
    return [s.model_dump(by_alias=True) for s in art.root]


def _write_blueprint_character_projection(
    store: ArtifactStore,
    grounding: GlobalGrounding,
    *,
    main_text: list[dict[str, Any]] | None = None,
) -> BlueprintCharacterProjection:
    """Global 终态写回时，用真实 prompt inventory 的唯一纯函数物化投影。

    投影未变时不重写字节；这不是小优化，而是 role/fate 等非消费
    修订不让 Blueprint stale 的物理保证。载体损坏也可在这条 0-LLM
    生产者路上重建，不需要重采 global。
    """
    sections = main_text if main_text is not None else _read_main_text(store)
    if sections is None:
        raise UpstreamMissing(
            f'{MAIN_TEXT} 不在盘上——无法物化 Blueprint 称名投影')
    characters = grounding.model_dump(
        by_alias=True, mode='json')['entities']['characters']
    expected = materialize_blueprint_character_projection(characters, sections)
    if store.exists(BLUEPRINT_CHARACTER_REL):
        try:
            current, _raw = store.read_own(
                BlueprintCharacterProjection,
                BLUEPRINT_CHARACTER_REL,
            )
        except ArtifactUnreadable:
            current = None
        if (current is not None
                and current.model_dump(by_alias=True, mode='json')
                == expected.model_dump(by_alias=True, mode='json')):
            return current
    store.write_round(BLUEPRINT_CHARACTER_REL, expected)
    return expected


def _require_blueprint_character_projection(
    store: ArtifactStore,
) -> tuple[BlueprintCharacterProjection, dict[str, Any]]:
    """Blueprint 的唯一称名输入；缺/坏/stale 都关闭并指向 0-LLM 恢复。"""
    recovery = (
        'stage2.global --renormalize（0 LLM）会从当前 formal/INVALID global '
        '与 MainText 重建该投影')
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
            '最新 Global 候选仍有会改变 Blueprint 称名闭集的 HARD；'
            '先走 stage2.global typed edit / '
            '`--revalidate --actor <id> --reason <why>`（0 LLM）。'
            '在称名面收口前不得启动付费 Blueprint')
    if not projection_exists:
        raise UpstreamMissing(
            f'{BLUEPRINT_CHARACTER_REL} 不在盘上；{recovery}')
    if freshness.state != 'fresh':
        raise ProvenanceRefused(
            f'{BLUEPRINT_CHARACTER_REL} 无法证明绑定当前最新 global + '
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
_GLOBAL_KEYS = ('setup_chapters_pruned', 'evidence_locator_failures')
_BLUEPRINT_KEYS = ('verbatim_failures', 'fact_atom_failures',
                   'hinge_ambiguous_pending')


def _accounting_of(both: _Both) -> dict[str, Any]:
    """⚠ **本层的记账横跨两份产物**，而报告只有一节 —— 这是全链唯一一处。
    理由与「判据表只有一张」同源：两个节点是**同一层**，把账拆成两节，
    「这一层落盘前改写了多少」就要读两屏才拼得出来。
    """
    got: dict[str, Any] = {}
    for src, keys in (((both.global_g or {}).get('_provenance') or {}, _GLOBAL_KEYS),
                      ((both.blueprints or {}).get('_provenance') or {}, _BLUEPRINT_KEYS)):
        got.update({k: src[k] for k in keys if k in src})
    return got


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


def _checkpoint_fact_atom_phrases(
        ctx: RunContext, *, artifact: ChapterBlueprints,
        raw: dict[str, Any], main_text: list[dict[str, Any]],
        character_projection: dict[str, Any],
        failures: list[tuple[str, str, str]],
) -> dict[str, str]:
    """从与当前问题章逐字段闭合的已验证 checkpoint 恢复原始证据短语。

    INVALID 只保留 ``claim``，而 claim 是模型转述，绝不能反猜 locator。这里先用
    immutable provenance 限定 code/架构/system/model/usage，再让 stage2_runs 核
    accepted attempt 与 ledger；最后按当前 main/称名投影重编译。只有编译章除失败事实
    的 ``source/source_beat_ref`` 外逐字段相等、且同章恰有一个响应 payload 时才交给
    normalizer。checkpoint 不闭合只是没有恢复材料，原 HARD 仍原样保留。
    """
    from novel.llm import stage2_runs
    from novel.stages.stage2.contract import contract_for

    provenance = artifact.provenance
    try:
        spec = contract_for(provenance.context.architecture)
    except KeyError:
        return {}
    if provenance.prompts.get(spec.sys_name) != prov.fingerprint(spec.system):
        return {}
    if provenance.usage is None:
        return {}

    chapter_of_fact: dict[str, int] = {}
    current_by_chapter: dict[int, dict[str, Any]] = {}
    atoms: dict[str, dict[str, Any]] = {}
    for blueprint in (raw.get('blueprints') or []):
        if not isinstance(blueprint, dict):
            continue
        ordinal = int(blueprint.get('chapter_ordinal') or 0)
        current_by_chapter[ordinal] = blueprint
        for atom in (blueprint.get('fact_atoms') or []):
            if not isinstance(atom, dict):
                continue
            fact_id = str(atom.get('fact_id') or '')
            if fact_id in atoms:
                return {}
            atoms[fact_id] = atom
            chapter_of_fact[fact_id] = ordinal

    targets: dict[int, dict[str, str]] = {}
    for row in failures:
        if len(row) != 3:
            return {}
        fact_id, original_ref, _cause = row
        fact_id = str(fact_id)
        atom = atoms.get(fact_id)
        ordinal = chapter_of_fact.get(fact_id)
        # live INVALID 若已由人改过这格，尊重人工候选，不拿旧 checkpoint 覆盖。
        if (atom is None or ordinal is None or atom.get('source') is not None
                or str(atom.get('source_beat_ref') or '') != str(original_ref)):
            continue
        targets.setdefault(ordinal, {})[fact_id] = str(original_ref)
    if not targets:
        return {}

    checkpoints = stage2_runs.verified_blueprint_recovery_checkpoints(
        ctx,
        architecture=spec.architecture,
        response_model=spec.response_model,
        system=spec.system,
        artifact_code=provenance.code,
        accepted_models=provenance.adopted_model_ids(),
        artifact_usage=provenance.usage.model_dump(mode='json'),
        chapters=targets,
    )
    character_inventory = blueprint_inventory_from_projection(
        character_projection)
    candidates: dict[int, dict[str, dict[str, str]]] = {}

    def masked(
            blueprint: dict[str, Any], wanted: set[str],
    ) -> tuple[dict[str, Any], set[str]]:
        value = copy.deepcopy(blueprint)
        seen: set[str] = set()
        for atom in (value.get('fact_atoms') or []):
            if not isinstance(atom, dict):
                continue
            fact_id = str(atom.get('fact_id') or '')
            if fact_id not in wanted:
                continue
            atom['source'] = None
            atom['source_beat_ref'] = '__checkpoint_rehome_target__'
            seen.add(fact_id)
        return value, seen

    for checkpoint in checkpoints:
        wanted_map = targets.get(checkpoint.chapter)
        current = current_by_chapter.get(checkpoint.chapter)
        if not wanted_map or current is None:
            continue
        response_rows = checkpoint.response_payload.get('blueprints')
        if not isinstance(response_rows, list):
            continue
        selected = [
            copy.deepcopy(row) for row in response_rows
            if isinstance(row, dict)
            and int(row.get('chapter_ordinal') or 0) == checkpoint.chapter
        ]
        if len(selected) != 1:
            continue
        try:
            compiled, accounting = spec.compile_fn(
                {'blueprints': selected}, main_text,
                character_inventory=character_inventory)
        except (TypeError, ValueError, ValidationError):
            continue
        compiled_rows = compiled.get('blueprints')
        if not isinstance(compiled_rows, list) or len(compiled_rows) != 1:
            continue
        candidate_chapter = compiled_rows[0]
        if not isinstance(candidate_chapter, dict):
            continue
        failed_ids = {
            str(row[0]) for row in accounting.get('fact_atom_failures', [])
            if isinstance(row, (list, tuple)) and len(row) == 3
        }
        wanted = set(wanted_map)
        if failed_ids & wanted:
            continue

        current_masked, current_seen = masked(current, wanted)
        candidate_masked, candidate_seen = masked(candidate_chapter, wanted)
        if (current_seen != wanted or candidate_seen != wanted
                or current_masked != candidate_masked):
            continue

        landed = {
            str(atom.get('fact_id') or ''): atom
            for atom in (candidate_chapter.get('fact_atoms') or [])
            if isinstance(atom, dict)
        }
        # 本入口只恢复“声明拍外、唯一落入另一拍”的旧失败；同拍补 source 属另一类
        # typed 修订，不能借这条自动路扩大权限。
        if any(
                not isinstance((landed.get(fact_id) or {}).get('source'), dict)
                or str((landed.get(fact_id) or {}).get('source_beat_ref') or '')
                == original_ref
                for fact_id, original_ref in wanted_map.items()):
            continue

        phrases: dict[str, str] = {}
        sequence = 0
        for beat in (selected[0].get('beats') or []):
            if not isinstance(beat, dict):
                continue
            for atom in (beat.get('fact_atoms') or []):
                if not isinstance(atom, dict):
                    continue
                sequence += 1
                fact_id = f'f{checkpoint.chapter:02d}_{sequence:03d}'
                if fact_id in wanted:
                    phrase = atom.get('evidence_phrase')
                    if isinstance(phrase, str) and phrase.strip():
                        phrases[fact_id] = phrase
        if set(phrases) != wanted:
            continue
        candidates.setdefault(checkpoint.chapter, {})[
            checkpoint.response_payload_sha256] = phrases

    recovered: dict[str, str] = {}
    for ordinal, wanted_map in targets.items():
        by_payload = candidates.get(ordinal, {})
        if len(by_payload) != 1:
            continue
        phrases = next(iter(by_payload.values()))
        if set(phrases) == set(wanted_map):
            recovered.update(phrases)
    return recovered


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
            if not store.exists(_GLOBAL_INVALID):
                raise UpstreamMissing(
                    f'{_GLOBAL_INVALID} 不在盘上——没有被拦下的全局问题稿要提升')
            baseline = store.revalidation_baseline(
                GLOBAL_REL, GlobalGrounding, actor=actor, reason=reason)
            artifact, raw = store.read_own(GlobalGrounding, _GLOBAL_INVALID)
            data = copy.deepcopy(raw)
            data.pop('_provenance', None)
            restore_failed_global_evidence_locators(
                data, baseline.provenance.evidence_locator_failures)
            data, accounting = normalize_global(data, main_text)
            rebuilt = GlobalGrounding.model_validate({
                **data,
                '_provenance': GlobalProvenance.model_validate({
                    **artifact.provenance.model_dump(), **accounting,
                }),
            })
            candidate = rebuilt.model_dump(by_alias=True)
            outcomes = _judge_candidate(candidate, None, main_text)
            if _hard_hits(outcomes, ignore={'upstream_missing'}):
                both = _Both(store)
                return False, _report(
                    ctx, store, both, outcomes,
                    judged_rel=_GLOBAL_INVALID, invalid_rel=_GLOBAL_INVALID)
            with store.history_transaction(
                    GLOBAL_REL, GLOBAL_REVIEW_REL, BLUEPRINT_CHARACTER_REL):
                store.promote_invalid(
                    GLOBAL_REL, GlobalGrounding, actor=actor, reason=reason,
                    artifact_override=rebuilt)
                promoted = store.read(GlobalGrounding, GLOBAL_REL)
                _refresh_global_review(store, promoted)
                _write_blueprint_character_projection(
                    store, promoted, main_text=main_text)
            _note_evidence_locator_corrections(
                ctx, store, accounting.evidence_locator_corrections,
                rel=GLOBAL_REL)
            both = _Both(store)
            return True, _report(
                ctx, store, both, both.judge(),
                judged_rel=GLOBAL_REL, invalid_rel=_GLOBAL_INVALID)

    if ctx.node != 'stage2.blueprint':
        raise TerminalState(f'{ctx.node} 不是 stage2 的可提升节点')
    if not store.exists(_INVALID):
        raise UpstreamMissing(
            f'{_INVALID} 不在盘上——没有被拦下的逐章问题稿要提升')
    _projection, character_projection = \
        _require_blueprint_character_projection(store)
    invalid_freshness = store.freshness(_INVALID)
    if (invalid_freshness.state == 'unknown'
            and invalid_freshness.reason == 'legacy_blueprint_inputs'):
        raise TerminalState(
            'stage2/chapter_blueprints.INVALID.json 仍记录旧 '
            '`{MainText, Global}` 来源边；`--revalidate` 只能继承原生成来源账，'
            '不能把旧账伪造成当前消费。请改跑 '
            '`stage2.blueprint --renormalize`（0 LLM）刷新真实来源边')
    baseline = store.revalidation_baseline(
        BLUEPRINT_REL, ChapterBlueprints, actor=actor, reason=reason)
    artifact, raw = store.read_own(ChapterBlueprints, _INVALID)
    phrases = _checkpoint_fact_atom_phrases(
        ctx,
        artifact=baseline,
        raw=raw,
        main_text=main_text,
        character_projection=character_projection,
        failures=list(baseline.provenance.fact_atom_failures),
    )
    data = copy.deepcopy(raw)
    data.pop('_provenance', None)
    data, accounting = normalize_blueprints(
        data,
        main_text,
        previous_fact_atom_failures=list(
            baseline.provenance.fact_atom_failures),
        evidence_phrases=phrases,
    )
    rebuilt = ChapterBlueprints.model_validate({
        **data,
        '_provenance': BlueprintProvenance.model_validate({
            **baseline.provenance.model_dump(), **accounting,
        }),
    })
    candidate = rebuilt.model_dump(by_alias=True)
    outcomes = _judge_candidate(None, candidate, main_text)
    if _blueprint_hard_hits(outcomes):
        both = _Both(store)
        return False, _report(ctx, store, both, outcomes, judged_rel=_INVALID)
    store.promote_invalid(
        BLUEPRINT_REL, ChapterBlueprints, actor=actor, reason=reason,
        artifact_override=rebuilt)
    both = _Both(store)
    return True, _report(ctx, store, both, both.judge())


def build_prompts(
    ctx: RunContext, *, chapters: tuple[int, ...] = (),
    character_projection: BlueprintCharacterProjection | None = None,
    main_text: list[dict[str, Any]] | None = None,
) -> list[tuple[int | None, str, str]]:
    """返回本次会发出的真实请求；blueprint 始终是一章一条。"""
    from novel.stages.stage2 import prompts

    store = ArtifactStore(ctx)
    mt = main_text if main_text is not None else _read_main_text(store)
    if mt is None:
        raise UpstreamMissing(f'{MAIN_TEXT} 不在盘上——先跑 stage1')
    if ctx.node == 'stage2.global':
        spec = _active_global_spec()
        system, user, _candidates = build_global_request(
            ctx.title, mt, spec)
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
    past_names = character_inventory['past_attested_surfaces']
    # 契约 dispatch：system 是 spec 里**已拼好结构提示**的完整字节。v2 的首相
    # 不需要关闭式细节表；scaffolded single 必须在唯一请求里同时拿到人物与候选。
    spec = _active_spec()
    # 两段式契约真实发出的第一条是边界预传；dry-run 与付费路共用这里，
    # 所以预览不能拿尚未冻结边界的细节 system 冒充第一条调用。
    system = (spec.boundary_system
              if spec.topology == 'boundary_then_detail'
              else spec.system)
    if system is None:
        raise RuntimeError(f'{spec.architecture} 的调用拓扑没有登记 system')
    visible: dict[int, dict[str, list[str]]] = {}
    candidates: dict[int, list[dict[str, Any]]] = {}
    if spec.topology == 'scaffolded_single':
        visible = character_inventory['visible_character_refs']
    if spec.topology == 'scaffolded_single' and spec.uses_hinge_candidates:
        candidates = hinge_candidates_by_chapter(mt)
    return [
        (o, system, build_blueprint_user(
            ctx.title,
            mt,
            (o,),
            past_names=past_names.get(o, []),
            character_refs=(visible.get(o, {})
                            if spec.topology == 'scaffolded_single' else None),
            hinge_candidates=(candidates.get(o, [])
                              if spec.topology == 'scaffolded_single'
                              and spec.uses_hinge_candidates else None),
        ))
        for o in targets
    ]


def _active_spec():
    """本次运行选用的 blueprint 契约（整跑用；修复按基线架构另选）。"""
    from novel.stages.stage2.contract import active_contract
    return active_contract()


def _active_global_spec() -> GlobalEvidenceContractSpec:
    """本次运行选用的 global evidence wire 契约。"""
    from novel.stages.stage2.global_contract import active_contract
    return active_contract()


def build_prompt(ctx: RunContext, *, chapters: tuple[int, ...] = ()) -> tuple[str, str]:
    """`--dry-run`：把这次要发出的第一条 ``(system, user)`` 原样交出来。

    ⚠ 它与付费路**共用同一个构造入口**（`R-11.8`）——否则「预览里看到的」与
    「真发出去的」是两件事，而那种分家没有任何东西会报错。
    ⚠ global 的 `user` 带全书原文；blueprint 只带稳定索引、当前章全文、有界过去窗口
    与已现称名表（后者只从注册的称名投影读取）。
    预览与付费路共用同一个构造入口，因此这里看到的边界就是真实模型可见边界。
    """
    _ordinal, system, user = build_prompts(ctx, chapters=chapters)[0]
    return system, user


def _chapter_index(main_text: list[dict[str, Any]]) -> str:
    """章节索引；一切对章的引用只用阅读序章号。"""
    return '\n'.join(
        f'阅读序第{s.get("chapter_ordinal")}章 {len(s.get("text") or "")} 字'
        for s in main_text
    )


def _blueprint_stable_prefix(title: str, main_text: list[dict[str, Any]]) -> str:
    """逐章那一相的**稳定前缀**：跨章不变，指纹进 `_provenance.context.stable_prefix`。

    ⚠ 这里调 `_chapter_index`，而那个函数**与 global 那一相共用**——
    要给正文加段号请改 `build_blueprint_user` 里的当前章块，别动它
    （改它 = global 侧的 user 字节也变 = 那一相的供应商前缀缓存一起冷启）。
    """
    return '\n\n'.join([
        f'《{title}》',
        '── 章节索引（一切对章的引用用阅读序章号）──\n' + _chapter_index(main_text),
        '── 上下文边界 ──\n当前章正文完整提供、且逐段编号（行首 `<数字>` 是段号，'
        '不是原文的一部分）；只提供过去窗口，未来章正文不可见。'
        '过去窗口是节选、没有编号，不可引用其段号。',
    ])


def _bounded_past(main_text: list[dict[str, Any]], ordinal: int) -> tuple[str, str]:
    """返回相邻章原文尾窗与更早章节的滚动连续性尾摘；两者都只看过去。"""
    prior = [s for s in main_text if int(s.get('chapter_ordinal') or 0) < ordinal]
    neighbor = prior[-1:]
    older = prior[:-1]
    neighbor_text = '\n\n'.join(
        '【第{}章末邻接窗口】\n{}'.format(
            s.get('chapter_ordinal'),
            str(s.get('text') or '')[-PAST_NEIGHBOR_CHARS_MAX:])
        for s in neighbor)
    remaining = CONTINUITY_CHARS_MAX
    rows: list[str] = []
    for section in reversed(older):
        separator_chars = 1 if rows else 0
        label = '第{}章末：'.format(section.get('chapter_ordinal'))
        available = remaining - separator_chars - len(label)
        if available <= 0:
            break
        text = str(section.get('text') or '')
        take = min(240, available, len(text))
        if take <= 0:
            continue
        row = label + text[-take:]
        rows.append(row)
        remaining -= separator_chars + len(row)
    return neighbor_text, '\n'.join(reversed(rows))


def _past_names_block(names: list[str]) -> str:
    """已现称名清单 → user 里的一个块；超预算按首现先后保留、砍尾。"""
    rows: list[str] = []
    used = 0
    for name in names:
        cost = len(name) + (1 if rows else 0)
        if used + cost > PAST_NAMES_CHARS_MAX:
            break
        rows.append(name)
        used += cost
    if not rows:
        return '── 此前章节已出现的称名 ──\n无（此前正文未出现名册称名）'
    return ('── 此前章节已出现的称名（代码派生，仅过去）──\n'
            '以下称名在此前章节的正文里出现过。判断「到目前为止的正文里出现过没有」时'
            '可据此认定；既不在此清单、也不在已给出正文里出现过的名字，仍然一个都不许用。\n'
            + '、'.join(rows))


def _observe_indistinguishable_refs(
    ctx: RunContext, dropped: dict[int, list[list[str]]],
) -> None:
    """把「本章有几组人物在称呼上分不开、因此没有下发」如实报出来。

    ⚠ 剔除本身在 `visible_character_refs_with_drops` 里（候选集只能有一个落点）；
    这里只负责让它**不是静默的**——少标几个在场者不会有任何判据变红，
    而「为什么这一章少标了」必须答得出来。
    """
    if not dropped:
        return
    ctx.observe(
        'character_refs_indistinguishable',
        chapters=sorted(dropped),
        groups={str(ordinal): groups for ordinal, groups in sorted(dropped.items())},
        dropped_ids=sum(len(cids) for groups in dropped.values()
                        for cids in groups),
    )


def _character_refs_block(refs: dict[str, list[str]]) -> str:
    """关闭式人物候选集；每个字面都已由代码证明在截止本章正文出现过。"""
    if not refs:
        return ('── 本章可选人物引用（代码派生、关闭式）──\n'
                '无。`present_char_ids` 只能交空数组。')
    return (
        '── 本章可选人物引用（代码派生、关闭式）──\n'
        '每个键是允许返回的 char_id，值是截止本章正文已经出现过的原文字面。'
        '只能从这些键中选择，不许写姓名、代词或清单外编号。\n'
        + json.dumps(refs, ensure_ascii=False, separators=(',', ':'))
    )


def _hinge_candidates_block(rows: list[dict[str, Any]]) -> str:
    """v3.1 的关闭式定位候选；空表不等于 hinge 必须为空，仍可交 phrase。"""
    return (
        '── 本章 hinge 原文候选（代码派生、按需选择）──\n'
        '候选恰好覆盖材料时只交 candidate_id；没有合适候选时交原样 phrase。'
        '不要为了使用候选而把无关字面标成 hinge。\n'
        + json.dumps(prompt_candidates(rows), ensure_ascii=False,
                     separators=(',', ':'))
    )


def _frozen_boundaries_block(ends: list[int],
                             events: list[str] | None = None) -> str:
    rows = [{'end_para': end,
             **({'event_draft': events[index]}
                if events is not None and index < len(events) else {})}
            for index, end in enumerate(ends)]
    return (
        '── 已冻结的事件边界（代码校验）──\n'
        '下面的 end_para 必须按顺序逐项原样返回；beats 数量也必须完全相同。\n'
        '若带 event_draft，它只是边界相留下的语义草图；请按原文忠实补全或修正 event，'
        '但不得借此增删边界。\n'
        + json.dumps(rows, ensure_ascii=False, separators=(',', ':'))
    )


def blueprint_context(title: str, main_text: list[dict[str, Any]],
                      architecture: str = BLUEPRINT_ARCHITECTURE) -> dict[str, Any]:
    """可落盘、可比较的上下文策略身份。

    ⚠ `architecture` 的默认值从 `contracts/invariants.py::BLUEPRINT_ARCHITECTURE` 取，
    判据 `context_scope` 读的是同一张取值面——**两边各写一份字面**是升版时最容易漏的
    那一处，漏了就得到「产物明明是新架构、判据说不符合约定」的假红。
    多契约并存期由调用方传入本次选用的架构（整跑=配置选的，修复=基线产物的）；
    其余预算字段**刻意不随契约变**（A/B 变量只有响应契约一维）。
    """
    return {
        'architecture': architecture,
        'stable_prefix': prov.fingerprint(_blueprint_stable_prefix(title, main_text)),
        'past_neighbor_chars_max': PAST_NEIGHBOR_CHARS_MAX,
        'continuity_chars_max': CONTINUITY_CHARS_MAX,
        'past_names_chars_max': PAST_NAMES_CHARS_MAX,
    }


def build_global_user(
    title: str,
    main_text: list[dict[str, Any]],
    *,
    evidence_candidates: GlobalEvidenceCandidateTable,
) -> str:
    """全书 user；段标记与 lookup 来自同一张关闭候选表。"""
    body = evidence_candidates.render()
    return '\n\n'.join([
        f'《{title}》',
        '── 章节索引（一切对章的引用用阅读序章号）──\n' + _chapter_index(main_text),
        '── 全书证据候选（不可变原文；行首 ID 不属于正文）──\n' + body,
    ])


def build_global_request(
    title: str,
    main_text: list[dict[str, Any]],
    spec: GlobalEvidenceContractSpec,
    *,
    agent_envelope: bool = False,
) -> tuple[str, str, GlobalEvidenceCandidateTable]:
    """从同一份 MainText 构造 system/user/lookup 三元组。"""
    candidates = GlobalEvidenceCandidateTable.build(main_text)
    return (
        build_global_agent_system(spec) if agent_envelope else spec.system,
        build_global_user(
            title, main_text, evidence_candidates=candidates),
        candidates,
    )


def build_global_agent_system(
    spec: GlobalEvidenceContractSpec | None = None,
) -> str:
    """为 agent 固定唯一 candidate wire 的全量/role-fate 窄修信封。"""
    spec = spec or _active_global_spec()

    full = {
        'payload': {
            'kind': 'full_candidate',
            'candidate': spec.hint,
        },
    }
    role_fate_patch = {'payload': {
        'kind': 'role_fate_patch',
        'candidate_sha256': '代码反馈给出的64位小写sha256',
        'evidence_table_sha256': '代码反馈给出的候选表64位小写sha256',
        'changes': [{
            'char_id': '只能复制代码反馈 allowed_char_ids 中的一项',
            'role': '只含静态身份与位置',
            'role_evidence': {'evidence_candidate_id': 'e000001'},
            'fate': '搬入的完整经历与结局事实',
            'fate_evidence': {'evidence_candidate_id': 'e000002'},
        }],
    }}
    return (
        spec.instruction
        + '\n\n最终只沿请求声明的唯一结构化出口提交一份'
          '符合响应 JSON Schema 的完整对象，不要解释、不要 markdown。'
          '第一次回答必须使用 full_candidate。只有代码反馈明确给出 '
          'repair_contract.kind=role_fate_patch 时，才使用 role_fate_patch；'
          '它必须逐字回显 candidate_sha256 与 evidence_table_sha256，'
          '人物集合必须与 allowed_char_ids 精确相等，四字段原子提交。'
          'evidence_candidate_id 格式/成员失败会在 accepted 前拒绝，并保持'
          '当前轮 repair contract；未进入 role_fate_patch 前的其他 HARD 才使用'
          'full_candidate。一旦进入 role_fate_patch 就不得退回完整候选。'
        + '\n\n完整候选形状：\n'
        + json.dumps(full, ensure_ascii=False, separators=(',', ':'))
        + '\n\n代码指定 role/fate 窄修时的形状：\n'
        + json.dumps(
            role_fate_patch, ensure_ascii=False, separators=(',', ':'))
    )


def build_blueprint_user(title: str, main_text: list[dict[str, Any]],
                         targets: tuple[int, ...], *,
                         past_names: list[str],
                         repair_findings: list[dict[str, str]] | None = None,
                         repair_baseline: dict[str, Any] | None = None,
                         character_refs: dict[str, list[str]] | None = None,
                         hinge_candidates: list[dict[str, Any]] | None = None,
                         frozen_boundaries: list[int] | None = None,
                         boundary_events: list[str] | None = None) -> str:
    """逐章那一相的 user。

    ⚠⚠ **这里从不下发完整人物表**：旧契约在 `char_id` 反转之后不引用全局编号；
    v2 若显式传 `character_refs`，也只有「截止本章正文已逐字出现过的字面 → 不透明
    char_id」关闭式子集。这不只是省输入——**完整人物表本身带着未来**
    （`char_001.address` 里那个名字可能要到 ch4 才在原文首现，而这份表被喂给了抽 ch1 的那次调用）
    ⇒ 只收窄正文修不掉 `event_early_name`，「收窄上下文」与「去掉 global 依赖」
    是同一个根因的两条腿。

    `past_names` 与 v2 的 `character_refs` 都必须来自
    `blueprint_character_prompt_inventory`。两份清单里的每个
    字面都经过正文实证过滤：``past_names`` 只认第 1..N-1 章，当前章的
    ``character_refs`` 则认截至第 N 章的累计正文。未来性被代码剥掉了，上面那条否决
    理由对它不成立。v2 窗口化之后模型看不全前文，「到目前为止出现过没有」这条判据
    由这两份清单代为作证——不下发它们，窗外首现的名字只能保守漏报。
    `past_names` 是必填参数而非默认 `None`：默认省略会让某条调用路静默丢掉这个块，
    而整跑与定向重生成的 user 必须同构（context 身份只有一个）。
    """
    scope = '、'.join(f'阅读序第{o}章' for o in targets)
    ordinal = max(targets)
    current = next(
        str(section.get('text') or '') for section in main_text
        if int(section.get('chapter_ordinal') or 0) == ordinal)
    neighbor, continuity = _bounded_past(main_text, ordinal)
    parts = [
        _blueprint_stable_prefix(title, main_text),
        ('── 滚动连续性状态（仅过去、摘要窗口）──\n' + continuity)
        if continuity else '── 滚动连续性状态 ──\n无（当前为早期章节）',
        ('── 相邻原文窗口（仅过去）──\n' + neighbor)
        if neighbor else '── 相邻原文窗口 ──\n无（当前为第一章）',
        _past_names_block(past_names),
        f'── 当前章全文（不可裁剪，逐段编号）──\n【第{ordinal}章】\n'
        + render_numbered(current),
        '── 本次任务范围 ──\n只产出以下章的 blueprint'
        '（按此顺序，范围外的章一律不要产出）：\n' + scope,
    ]
    if character_refs is not None:
        parts.append(_character_refs_block(character_refs))
    if hinge_candidates is not None:
        parts.append(_hinge_candidates_block(hinge_candidates))
    if frozen_boundaries is not None:
        parts.append(_frozen_boundaries_block(
            frozen_boundaries, boundary_events))
    if repair_baseline:
        parts.append(
            '── 上一版结构基线 ──\n'
            '这是局部修订基线。除失败证据直接要求改动之处，不得删除或合并已有事件拍，'
            '不得减少 hinges / fact_atoms；仍须交回一份完整章节结构。正文与证据优先。\n'
            + json.dumps(repair_baseline, ensure_ascii=False, separators=(',', ':')))
    if repair_findings:
        evidence = '\n'.join(
            f'- [{item["item"]}] {item["detail"]}' for item in repair_findings)
        parts.append(
            '── 上一版必须修正的问题 ──\n'
            '这些是确定性校验命中的问题。只修本次任务章；不要靠机械放宽 span 遮住漏抓。\n'
            + evidence)
    return '\n\n'.join(parts)


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
) -> list[Outcome]:
    return checks.check(
        global_g=global_g, blueprints=blueprints, main_text=main_text)


def _canonical_sha256(value: object) -> str:
    """候选/冻结 guard 的全宽身份；10 位 provenance 指纹不承担提交等值证明。"""
    payload = json.dumps(
        value, ensure_ascii=False, sort_keys=True,
        separators=(',', ':'), allow_nan=False,
    ).encode('utf-8')
    return hashlib.sha256(payload).hexdigest()


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


def global_role_fate_patch_scope(
        prepared: GlobalCandidateContext,
        evaluation: GlobalCandidateEvaluation,
) -> global_role_fate_patch.RoleFatePatchScope | None:
    """只为纯 ``role_leaks_fate`` 建立 typed allowlist。"""
    response = evaluation._response
    table = prepared._evidence_candidates
    actual_sha = global_response_sha256(response)
    if actual_sha != evaluation.response_sha256:
        raise ProvenanceRefused(
            'stage2.global 评估后 raw candidate 已变异；'
            '拒绝用旧 SHA 构造 role/fate patch allowlist')
    scope = global_role_fate_patch.scope_for(
        response,
        hard_hits=evaluation.hard_hits,
        candidate_sha256=actual_sha,
        evidence_table=table,
    )
    if (scope is not None
            and prepared.evidence_candidates_sha256
            != scope.evidence_table_sha256):
        raise ProvenanceRefused(
            'stage2.global 冻结候选表身份与 role/fate patch scope 漂移')
    return scope


def global_role_fate_patch_feedback_rows(
        evaluation: GlobalCandidateEvaluation,
        scope: global_role_fate_patch.RoleFatePatchScope,
) -> list[dict[str, object]]:
    """给 provider 的失败事实与双 SHA 修订契约；服务账不复制正文。"""
    rows = evaluation.feedback_rows()
    matched = [row for row in rows if row.get('rule') == 'role_leaks_fate']
    if len(rows) != 1 or len(matched) != 1:
        raise ValueError(
            'role/fate patch 必须恰好对应唯一 role_leaks_fate 反馈')
    matched[0]['repair_contract'] = scope.to_dict()
    return rows


def apply_global_role_fate_patch(
        prepared: GlobalCandidateContext,
        evaluation: GlobalCandidateEvaluation,
        patch: GlobalRoleFatePatchTurn,
) -> global_role_fate_patch.AppliedRoleFatePatch:
    """关闭式合并 role/fate patch；结果仍须走完整 evaluate/finalize。"""
    response = evaluation._response
    table = prepared._evidence_candidates
    scope = global_role_fate_patch_scope(prepared, evaluation)
    if scope is None:
        raise ValueError(
            '当前候选不是纯 role_leaks_fate，禁止 role/fate patch')
    return global_role_fate_patch.apply_patch(
        response, patch, scope, table,
        actual_candidate_sha256=global_response_sha256(response),
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
        raise ValueError('stage2.global 只接受 paragraph-candidate-v1 响应')
    return compile_candidate_evidence(
        response.model_dump(by_alias=True, mode='json'),
        prepared._evidence_candidates,
    )


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
            return '本次只接受 paragraph-candidate-v1 agent 信封'
        if not isinstance(response.payload, GlobalCandidateFullTurn):
            return '本轮只接受 full_candidate'
        candidate = response.payload.candidate
        try:
            _compile_global_response(prepared, candidate)
        except (TypeError, ValueError) as exc:
            return str(exc)
        return None

    return validate


def global_role_fate_patch_validator(
    prepared: GlobalCandidateContext,
    evaluation: GlobalCandidateEvaluation,
) -> Callable[[GlobalCandidateAgentTurnResponse, Any], str | None]:
    """role/fate 修复轮只接受绑定当前候选/候选表的窄 patch。

    响应 schema 是 full/patch union，只负责让同一 provider session 的输出信封
    保持稳定；真正的状态机必须在这里拒绝 full re-entry。错误 SHA、人物集合或
    evidence ID 也都在 accepted/checkpoint/audit 之前关闭式返回。
    """
    def validate(
        response: GlobalCandidateAgentTurnResponse, _wire: Any,
    ) -> str | None:
        if not isinstance(response.payload, GlobalRoleFatePatchTurn):
            return '本轮 repair_contract 只接受 role_fate_patch，不接受完整候选'
        try:
            apply_global_role_fate_patch(
                prepared, evaluation, response.payload)
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
        prepared: GlobalCandidateContext,
        response: GlobalGroundingCandidateResponse,
        *,
        turn_kind: str,
        base_response: GlobalGroundingCandidateResponse | None,
        accepted_role_patch_count: int,
) -> dict[str, object]:
    return {
        'kind': 'stage2-global-candidate-v1',
        'turn_kind': turn_kind,
        'accepted_role_patch_count': accepted_role_patch_count,
        'main_text_sha256': prepared.main_text_sha256,
        'evidence_candidates_sha256': prepared.evidence_candidates_sha256,
        'candidate': response.model_dump(by_alias=True, mode='json'),
        'candidate_sha256': global_response_sha256(response),
        'base_candidate': (
            None if base_response is None else
            base_response.model_dump(by_alias=True, mode='json')),
        'base_candidate_sha256': (
            None if base_response is None else
            global_response_sha256(base_response)),
        'custody': None,
    }


def write_global_agent_checkpoint(
        ctx: RunContext,
        source: _GlobalResponseSource,
        prepared: GlobalCandidateContext,
        turn: BaseModel,
        response: GlobalGroundingCandidateResponse,
        *,
        base_response: GlobalGroundingCandidateResponse | None = None,
        accepted_role_patch_count: int = 0,
) -> None:
    """accepted stdout 到手后立刻写 append-only checkpoint。"""
    from novel.llm import stage2_global_runs

    assert_global_main_text_current(ctx, prepared)
    payload = getattr(turn, 'payload', None)
    if isinstance(payload, GlobalCandidateFullTurn):
        turn_kind = 'full_candidate'
        if base_response is not None:
            raise ProvenanceRefused('Global full_candidate checkpoint 不得携带 patch base')
        if accepted_role_patch_count != 0:
            raise ProvenanceRefused(
                'Global full_candidate checkpoint 不得继承 role/fate patch 计数')
    elif isinstance(payload, GlobalRoleFatePatchTurn):
        turn_kind = 'role_fate_patch'
        if not isinstance(base_response, GlobalGroundingCandidateResponse):
            raise ProvenanceRefused(
                'Global role_fate_patch checkpoint 缺 candidate-only typed base')
        if accepted_role_patch_count not in (1, 2):
            raise ProvenanceRefused(
                'Global role_fate_patch checkpoint 的 accepted 计数只能是 1 或 2')
    else:
        raise ProvenanceRefused(
            f'Global checkpoint 尚未登记 turn 类型 {type(payload).__name__}')
    checkpoint = source.accepted_checkpoint(ctx.node)
    turn_index = checkpoint.get('accepted_turn_index')
    if not isinstance(turn_index, int) or isinstance(turn_index, bool) or turn_index < 1:
        raise ProvenanceRefused('Global accepted checkpoint 缺正整数 turn index')
    stage2_global_runs.write_checkpoint(
        ctx,
        input_sha256=prepared.input_sha256,
        system=prepared.system,
        user=prepared.user,
        response_model=prepared.agent_response_model,
        max_tokens=GLOBAL_MAX_TOKENS,
        response_payload=turn.model_dump(by_alias=True, mode='json'),
        acceptance=checkpoint,
        revision_key=f'candidate:{turn_index}',
        state_payload=_global_checkpoint_state(
            prepared, response, turn_kind=turn_kind,
            base_response=base_response,
            accepted_role_patch_count=accepted_role_patch_count),
    )


def _global_validator_custody_chain(
        ctx: RunContext, prepared: GlobalCandidateContext,
) -> str:
    return _canonical_sha256({
        'kind': 'stage2-global-validator-custody-v1',
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


def _assert_global_custody_durable_state(
        ctx: RunContext,
        prepared: GlobalCandidateContext,
        *,
        expected_turn_kind: str,
        base_response: GlobalGroundingCandidateResponse | None,
        accepted_role_patch_count: int,
) -> None:
    """把调用者的内存 patch 状态与 latest durable checkpoint 对证。

    dynamic-validator rejection 会覆盖 client 的内存 accepted attempt，却不会写业务
    checkpoint。custody 因此只能把“被拒 turn 之前”的 base/count 绑定到最新 durable
    candidate；若裸信调用者传入的 ``count``，真实第二次 role patch 可被伪装成第一次，
    恢复口就会错误放宽出第三次窄修。
    """
    from novel.llm import stage2_global_runs

    durable = stage2_global_runs.restore_checkpoint(
        ctx,
        input_sha256=prepared.input_sha256,
        system=prepared.system,
        user=prepared.user,
        response_model=prepared.agent_response_model,
        max_tokens=GLOBAL_MAX_TOKENS,
    )
    if expected_turn_kind == 'full_candidate':
        # 首轮 full 的 dynamic rejection 合法地发生在首个 checkpoint 之前；若已有
        # durable candidate，它也只能属于尚未进入 role patch 的 count=0 状态。
        if durable is not None:
            state_count = durable.state_payload.get(
                'accepted_role_patch_count')
            if state_count != 0:
                raise ProvenanceRefused(
                    'Global full custody 与 durable role/fate sticky 状态冲突')
        return
    if durable is None or base_response is None:
        raise ProvenanceRefused(
            f'Global {expected_turn_kind} custody 缺 latest durable base checkpoint')
    state = durable.state_payload
    base_payload = base_response.model_dump(by_alias=True, mode='json')
    base_sha = global_response_sha256(base_response)
    if (state.get('candidate') != base_payload
            or state.get('candidate_sha256') != base_sha
            or state.get('accepted_role_patch_count')
            != accepted_role_patch_count):
        raise ProvenanceRefused(
            f'Global {expected_turn_kind} custody 的 base/count 与 latest durable '
            'checkpoint 不一致')


def custody_global_validator_rejection(
        ctx: RunContext,
        source: Any,
        prepared: GlobalCandidateContext,
        *,
        expected_turn_kind: str = 'full_candidate',
        base_response: GlobalGroundingCandidateResponse | None = None,
        accepted_role_patch_count: int = 0,
) -> str | None:
    """按当轮状态封存最后一份 schema-valid / dynamic-validator 拒稿。

    ``accepted_role_patch_count`` 是这份被拒 turn **之前**已经 accepted 的窄修数；
    因此 role/fate replacement 成功后只能从 0→1 或 1→2。旧 full 调用省略
    三个关键字时仍保持原语义。
    """
    from novel.llm import response_repair

    rejected = source.last_json_rejection(ctx.node)
    if rejected is None:
        return None
    if expected_turn_kind == 'full_candidate':
        if base_response is not None or accepted_role_patch_count != 0:
            raise ProvenanceRefused(
                'Global full_candidate custody 不得携带 base/role patch 计数')
    elif expected_turn_kind == 'evidence_patch':
        raise ProvenanceRefused(
            'Global locator evidence_patch custody 已退役；不能再保管旧 wire 拒稿')
    elif expected_turn_kind == 'role_fate_patch':
        if (not isinstance(base_response, GlobalGroundingCandidateResponse)
                or accepted_role_patch_count not in (0, 1)):
            raise ProvenanceRefused(
                'Global role_fate_patch custody 必须绑定 candidate base，'
                '且写前 accepted 计数只能是 0 或 1')
    else:
        raise ProvenanceRefused(
            f'Global custody 未登记 expected_turn_kind={expected_turn_kind!r}')
    base_payload = (
        None if base_response is None else
        base_response.model_dump(by_alias=True, mode='json'))
    base_sha256 = (
        None if base_response is None else
        global_response_sha256(base_response))
    _assert_global_custody_durable_state(
        ctx,
        prepared,
        expected_turn_kind=expected_turn_kind,
        base_response=base_response,
        accepted_role_patch_count=accepted_role_patch_count,
    )
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
                f'当轮 expected_turn_kind={expected_turn_kind}，并按当前 schema、'
                '冻结 base、candidate table 与业务判据重放同一 applicator'),
            'response_model': prepared.agent_response_model.__name__,
            'required_top_level': required_top_level(
                prepared.agent_response_model),
            'response_schema': prepared.agent_response_model.model_json_schema(
                by_alias=True),
            'chapter': None,
            'candidate_subject_sha256': prepared.input_sha256,
            'main_text_sha256': prepared.main_text_sha256,
            'evidence_candidates_sha256': prepared.evidence_candidates_sha256,
            'expected_turn_kind': expected_turn_kind,
            'base_candidate': base_payload,
            'base_candidate_sha256': base_sha256,
            'accepted_role_patch_count': accepted_role_patch_count,
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


def _global_custody_expectation(
        rejection: dict[str, Any],
) -> tuple[
    str,
    GlobalGroundingCandidateResponse | None,
    int,
]:
    """读取新状态绑定；三格全缺仅兼容既有 full-candidate custody。"""
    state_keys = {
        'expected_turn_kind', 'base_candidate',
        'base_candidate_sha256', 'accepted_role_patch_count',
    }
    present = state_keys & set(rejection)
    if not present:
        return 'full_candidate', None, 0
    if present != state_keys:
        raise ProvenanceRefused(
            'Global custody rejection 的 expected/base/count 状态只写了一半')
    expected = rejection.get('expected_turn_kind')
    base_raw = rejection.get('base_candidate')
    base_sha = rejection.get('base_candidate_sha256')
    count = rejection.get('accepted_role_patch_count')
    if not isinstance(count, int) or isinstance(count, bool):
        raise ProvenanceRefused('Global custody accepted_role_patch_count 不是整数')
    if expected == 'full_candidate':
        if base_raw is not None or base_sha is not None or count != 0:
            raise ProvenanceRefused(
                'Global full custody rejection 意外携带 base/patch 计数')
        return expected, None, count
    if expected == 'evidence_patch':
        raise ProvenanceRefused(
            'Global locator evidence_patch custody 已退役；旧拒稿不能在当前 '
            'paragraph-candidate-v1 wire 上恢复')
    if expected == 'role_fate_patch':
        if not isinstance(base_raw, dict) or count not in (0, 1):
            raise ProvenanceRefused(
                'Global role/fate custody rejection 缺 candidate base 或计数非法')
        base = GlobalGroundingCandidateResponse.model_validate(base_raw)
    else:
        raise ProvenanceRefused(
            f'Global custody rejection 含未知 expected_turn_kind={expected!r}')
    if global_response_sha256(base) != base_sha:
        raise ProvenanceRefused('Global custody rejection 的 base SHA 不闭合')
    return expected, base, count


def _global_custody_base_evaluation(
        ctx: RunContext,
        prepared: GlobalCandidateContext,
        base: GlobalGroundingCandidateResponse,
        acceptance: dict[str, Any],
) -> GlobalCandidateEvaluation:
    """用 sealed acceptance 的来源事实重建 patch dynamic validator 所需 base。"""
    links = acceptance.get('attempt_links')
    if not isinstance(links, list) or not links:
        raise ProvenanceRefused('Global custody acceptance 缺 attempt links')
    models = tuple(sorted({
        str(row.get('model')) for row in links
        if isinstance(row, dict) and isinstance(row.get('model'), str)
    }))
    if not models:
        raise ProvenanceRefused('Global custody acceptance 缺 adopted model 集合')
    _assert_global_guard_current(ctx, prepared)
    return _materialize_global_evaluation(
        ctx,
        prepared,
        base,
        requested_model=str(acceptance.get('model') or ''),
        adopted_models=models,
        source_attempt_id=str(acceptance.get('attempt_id') or ''),
        settle=False,
    )


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
    expected_turn_kind, base, prior_role_patch_count = \
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
    payload = getattr(replacement_turn, 'payload', None)
    why: str | None
    if expected_turn_kind == 'full_candidate':
        if not isinstance(payload, GlobalCandidateFullTurn):
            why = '当轮 expected_turn_kind=full_candidate，只接受完整 full_candidate'
        else:
            why = global_full_candidate_validator(prepared)(
                replacement_turn, None)
        if why is None:
            assert isinstance(payload, GlobalCandidateFullTurn)
            response = payload.candidate
        result_role_patch_count = 0
    else:
        assert expected_turn_kind == 'role_fate_patch'
        if not isinstance(payload, GlobalRoleFatePatchTurn):
            why = '当轮 expected_turn_kind=role_fate_patch，禁止借 custody 回 full'
        else:
            assert isinstance(base, GlobalGroundingCandidateResponse)
            base_evaluation = _global_custody_base_evaluation(
                ctx, prepared, base, acceptance)
            why = global_role_fate_patch_validator(
                prepared, base_evaluation)(replacement_turn, None)
        if why is None:
            assert isinstance(payload, GlobalRoleFatePatchTurn)
            response = apply_global_role_fate_patch(
                prepared, base_evaluation, payload).response
        result_role_patch_count = prior_role_patch_count + 1
        if result_role_patch_count not in (1, 2):
            raise ProvenanceRefused(
                'Global role/fate custody accepted 计数未严格递增到 1/2')
    if why is not None:
        response_repair.write_outcome(
            record, decision='rejected', gate='validator',
            detail=None, why=why)
        _note_global_custody_terminal_outcome(ctx, record)
        raise TerminalState(
            f'Global typed replacement 不通过当前 {expected_turn_kind} validator：{why}')
    state = _global_checkpoint_state(
        prepared, response, turn_kind='custody_replacement',
        base_response=base,
        accepted_role_patch_count=result_role_patch_count)
    state['custody'] = {
        'chain_id': chain,
        'attempt_id': record.attempt_id,
        'expected_turn_kind': expected_turn_kind,
        'accepted_role_patch_count_before': prior_role_patch_count,
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


def restore_global_agent_checkpoint(
        ctx: RunContext,
        source: _GlobalResponseSource,
        prepared: GlobalCandidateContext,
) -> RestoredGlobalCandidate | None:
    """同 run 零 wire 恢复 latest accepted 候选，并重放 patch applicator。"""
    from novel.llm import stage2_global_runs

    assert_global_main_text_current(ctx, prepared)
    restored = stage2_global_runs.restore_checkpoint(
        ctx,
        input_sha256=prepared.input_sha256,
        system=prepared.system,
        user=prepared.user,
        response_model=prepared.agent_response_model,
        max_tokens=GLOBAL_MAX_TOKENS,
    )
    if restored is None:
        return None
    source.restore_checkpoint_acceptance(
        ctx.node,
        restored.acceptance,
        system=prepared.system,
        user=prepared.user,
        max_tokens=GLOBAL_MAX_TOKENS,
        recovery_policy=stage2_global_runs.RECOVERY_POLICY,
    )
    assert_global_main_text_current(ctx, prepared)
    state = restored.state_payload
    expected_keys = {
        'kind', 'turn_kind', 'accepted_role_patch_count', 'main_text_sha256',
        'evidence_candidates_sha256', 'candidate', 'candidate_sha256',
        'base_candidate', 'base_candidate_sha256', 'custody',
    }
    if set(state) != expected_keys or state.get('kind') != 'stage2-global-candidate-v1':
        raise ProvenanceRefused('Global checkpoint state 形状/版本不合法')
    if (state.get('main_text_sha256') != prepared.main_text_sha256
            or state.get('evidence_candidates_sha256')
            != prepared.evidence_candidates_sha256):
        raise ProvenanceRefused(
            'Global checkpoint MainText/candidate table subject 与当前准备态漂移')

    turn = restored.value
    payload = getattr(turn, 'payload', None)
    turn_kind = state.get('turn_kind')
    accepted_role_patch_count = state.get('accepted_role_patch_count')
    if (not isinstance(accepted_role_patch_count, int)
            or isinstance(accepted_role_patch_count, bool)
            or not 0 <= accepted_role_patch_count <= 2):
        raise ProvenanceRefused(
            'Global checkpoint accepted_role_patch_count 不在 0..2 闭集')
    if turn_kind == 'full_candidate':
        if not isinstance(payload, GlobalCandidateFullTurn):
            raise ProvenanceRefused('Global checkpoint full_candidate 信封分支漂移')
        if state.get('base_candidate') is not None or state.get('base_candidate_sha256') is not None:
            raise ProvenanceRefused('Global checkpoint full_candidate 意外携带 patch base')
        if accepted_role_patch_count != 0:
            raise ProvenanceRefused(
                'Global full_candidate checkpoint 意外携带 role/fate patch 计数')
        response = payload.candidate
    elif turn_kind == 'evidence_patch':
        raise ProvenanceRefused(
            'Global locator evidence_patch checkpoint 已退役；旧 checkpoint '
            '不能在当前 paragraph-candidate-v1 wire 上恢复')
    elif turn_kind == 'role_fate_patch':
        if not isinstance(payload, GlobalRoleFatePatchTurn):
            raise ProvenanceRefused(
                'Global checkpoint role_fate_patch 信封分支漂移')
        if accepted_role_patch_count not in (1, 2):
            raise ProvenanceRefused(
                'Global role_fate_patch checkpoint 缺 accepted 计数')
        base_raw = state.get('base_candidate')
        if not isinstance(base_raw, dict):
            raise ProvenanceRefused(
                'Global checkpoint role_fate_patch 缺 candidate-only typed base')
        base = GlobalGroundingCandidateResponse.model_validate(base_raw)
        if global_response_sha256(base) != state.get('base_candidate_sha256'):
            raise ProvenanceRefused(
                'Global checkpoint role/fate patch base SHA 漂移')
        base_evaluation = evaluate_global_candidate(
            ctx, source, prepared, base)
        try:
            applied = apply_global_role_fate_patch(
                prepared, base_evaluation, payload)
        except (TypeError, ValueError) as exc:
            raise ProvenanceRefused(
                f'Global checkpoint role/fate patch 当前代码重放失败：{exc}') from exc
        response = applied.response
        attempt_id = str(restored.acceptance.get('attempt_id') or '')
        ctx.ledger.note_role_fate_candidate_patch(
            sid=ctx.sid, node=ctx.node, purpose=ctx.node,
            attempt_id=attempt_id,
            base_candidate_sha256=base_evaluation.response_sha256,
            result_candidate_sha256=global_response_sha256(response),
            evidence_table_sha256=payload.evidence_table_sha256,
            changed_field_count=applied.changed_field_count,
        )
    elif turn_kind == 'custody_replacement':
        from novel.llm import response_repair

        custody = state.get('custody')
        candidate_raw = state.get('candidate')
        if not isinstance(custody, dict) or not isinstance(candidate_raw, dict):
            raise ProvenanceRefused('Global custody checkpoint 状态不完整')
        attempt_id = custody.get('attempt_id')
        if attempt_id != restored.acceptance.get('attempt_id'):
            raise ProvenanceRefused('Global custody attempt 与 acceptance 漂移')
        record = response_repair.find_attempt(ctx.novel_dir, str(attempt_id))
        resolution = record.resolution
        if (record.rejection.get('chain_id') != custody.get('chain_id')
                or not isinstance(resolution, dict)
                or resolution.get('action') not in ('submit', 'deterministic')
                or resolution.get('resolution_sha256')
                != custody.get('resolution_sha256')
                or resolution.get('candidate_payload_sha256')
                != custody.get('candidate_payload_sha256')):
            raise ProvenanceRefused(
                'Global custody checkpoint 与 sealed response-repair resolution 漂移')
        expected_turn_kind, base, prior_role_patch_count = \
            _global_custody_expectation(record.rejection)
        custody_expected = custody.get(
            'expected_turn_kind', 'full_candidate')
        custody_prior = custody.get(
            'accepted_role_patch_count_before', 0)
        if (custody_expected != expected_turn_kind
                or custody_prior != prior_role_patch_count):
            raise ProvenanceRefused(
                'Global custody checkpoint 的 expected turn/count 与 rejection 漂移')
        if base is None:
            if (state.get('base_candidate') is not None
                    or state.get('base_candidate_sha256') is not None):
                raise ProvenanceRefused(
                    'Global full custody checkpoint 意外携带 base')
        elif (state.get('base_candidate')
              != base.model_dump(by_alias=True, mode='json')
              or state.get('base_candidate_sha256')
              != global_response_sha256(base)):
            raise ProvenanceRefused(
                'Global custody checkpoint 的 base candidate/SHA 与 rejection 漂移')
        repaired_turn = prepared.agent_response_model.model_validate(
            resolution.get('candidate'))
        repaired_payload = getattr(repaired_turn, 'payload', None)
        if expected_turn_kind == 'full_candidate':
            if (not isinstance(repaired_payload, GlobalCandidateFullTurn)
                    or accepted_role_patch_count != 0):
                raise ProvenanceRefused(
                    'Global full custody checkpoint 分支/计数漂移')
            why = global_full_candidate_validator(prepared)(
                repaired_turn, None)
            response = repaired_payload.candidate
        else:
            if (expected_turn_kind != 'role_fate_patch'
                    or not isinstance(repaired_payload, GlobalRoleFatePatchTurn)
                    or not isinstance(base, GlobalGroundingCandidateResponse)
                    or accepted_role_patch_count != prior_role_patch_count + 1
                    or accepted_role_patch_count not in (1, 2)):
                raise ProvenanceRefused(
                    'Global role/fate custody checkpoint 分支/base/计数未递增到 1/2')
            base_evaluation = evaluate_global_candidate(
                ctx, source, prepared, base)
            why = global_role_fate_patch_validator(
                prepared, base_evaluation)(repaired_turn, None)
            if why is None:
                applied = apply_global_role_fate_patch(
                    prepared, base_evaluation, repaired_payload)
                response = applied.response
                ctx.ledger.note_role_fate_candidate_patch(
                    sid=ctx.sid, node=ctx.node, purpose=ctx.node,
                    attempt_id=str(attempt_id),
                    base_candidate_sha256=base_evaluation.response_sha256,
                    result_candidate_sha256=global_response_sha256(response),
                    evidence_table_sha256=(
                        repaired_payload.evidence_table_sha256),
                    changed_field_count=applied.changed_field_count,
                )
        if why is not None:
            raise ProvenanceRefused(
                f'Global custody replacement 不再通过当前 '
                f'{expected_turn_kind} validator：{why}')
        if response.model_dump(by_alias=True, mode='json') != candidate_raw:
            raise ProvenanceRefused(
                'Global custody checkpoint candidate 与 sealed replacement 漂移')
    else:
        raise ProvenanceRefused(
            f'Global checkpoint 含未登记 turn_kind={turn_kind!r}')

    candidate_raw = state.get('candidate')
    if (not isinstance(candidate_raw, dict)
            or response.model_dump(by_alias=True, mode='json') != candidate_raw
            or global_response_sha256(response) != state.get('candidate_sha256')):
        raise ProvenanceRefused('Global checkpoint 合并候选与当前 schema/applicator 漂移')
    # full_candidate 的 unknown-ID 等动态成员闸也必须用当前候选表重验。
    try:
        _compile_global_response(prepared, response)
    except (TypeError, ValueError) as exc:
        raise ProvenanceRefused(
            f'Global checkpoint 候选不再通过当前 membership validator：{exc}') from exc
    assert_global_main_text_current(ctx, prepared)
    return RestoredGlobalCandidate(
        response=response,
        acceptance=dict(restored.acceptance),
        revision=restored.revision,
        turn_kind=str(turn_kind),
        accepted_role_patch_count=accepted_role_patch_count,
    )


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
        _judge_candidate(old_global, blueprint.raw, main_text)))
    return _GlobalDownstreamGuard(
        blueprint=blueprint,
        baseline_state='absent' if old_global is None else 'current',
        baseline_sha256=(
            None if old_global is None else _canonical_sha256(old_global)),
        baseline_hard_hits=before,
    )


def prepare_global_candidate(
    ctx: RunContext, *, agent_envelope: bool = False,
) -> GlobalCandidateContext:
    """冻结一次 global 候选循环的 prompt、正文与下游差分基线。

    本函数不调用模型、不铸 provenance、不写盘。blueprint/旧 global 只决定能否提交，
    不参与候选内容，因此用 ``validation_reads`` 隔离，避免它们冒充新 global 的输入边。
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
    spec = _active_global_spec()
    system, user, evidence_candidates = build_global_request(
        ctx.title, main_text, spec, agent_envelope=agent_envelope)
    with ctx.validation_reads():
        downstream = _read_global_downstream_guard(
            ctx, store, main_text, observe=True)
    candidate_sha256 = evidence_candidates.sha256()
    input_sha256 = _canonical_sha256({
        'node': 'stage2.global',
        'contract_name': spec.name,
        'main_text_file_sha256': main_text_sha256,
        'evidence_candidates_sha256': candidate_sha256,
    })
    return GlobalCandidateContext(
        system=system,
        user=user,
        contract_name=spec.name,
        input_sha256=input_sha256,
        main_text_sha256=main_text_sha256,
        evidence_candidates_sha256=candidate_sha256,
        _main_text=tuple(copy.deepcopy(main_text)),
        _downstream=downstream,
        _contract=spec,
        _evidence_candidates=evidence_candidates,
    )


def assert_global_main_text_current(
        ctx: RunContext, prepared: GlobalCandidateContext,
) -> None:
    """逐尾窗重读盘上 MainText 主体 SHA，不拿冻结内存正文冒充 freshness。"""
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
    outcomes = _judge_candidate(candidate, None, main_text)
    hits = set(_hard_hits(outcomes, ignore={'upstream_missing'}))
    guard = prepared._downstream
    if guard.blueprint.state == 'current':
        assert guard.blueprint.raw is not None
        after = _judge_candidate(candidate, guard.blueprint.raw, main_text)
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
    provenance = ctx.llm_provenance(
        GlobalProvenance,
        prompts={prepared._contract.sys_name: prepared.system},
        model=requested_model,
        adopted_models=adopted_models,
        settle=settle,
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


def _refresh_global_review(store: ArtifactStore, artifact: GlobalGrounding) -> None:
    previous = (
        store.read_own(GlobalReview, GLOBAL_REVIEW_REL)[0]
        if store.exists(GLOBAL_REVIEW_REL) else None
    )
    review = build_review(artifact.model_dump(by_alias=True), previous)
    store.write_round(GLOBAL_REVIEW_REL, review)


def _write_formal(store: ArtifactStore, rel: str, artifact: Any) -> None:
    """Commit a new round; global grounding and its review are indivisible."""
    companions = (
        (rel, GLOBAL_REVIEW_REL, BLUEPRINT_CHARACTER_REL)
        if rel == GLOBAL_REL else (rel,)
    )
    with store.history_transaction(*companions):
        store.write_round(rel, artifact)
        if rel == GLOBAL_REL:
            _refresh_global_review(store, artifact)
            _write_blueprint_character_projection(store, artifact)


def _retire_invalid_for_matching_formal(
        store: ArtifactStore, rel: str, artifact: Any,
) -> None:
    """提交尾窗只退休问题稿，不把逐字相同的正式稿再记成一个历史轮次。"""
    invalid_rel = paths.invalid_of(rel)
    if not store.exists(invalid_rel):
        return
    with store.history_transaction(rel):
        current, _raw = store.read_own(
            type(artifact), rel, require_current=True)
        if current.model_dump(by_alias=True) != artifact.model_dump(by_alias=True):
            raise ProvenanceRefused(
                f'{rel} 在提交尾窗已漂移；拒绝只退休 {invalid_rel}')
        store.retire_invalid(rel)


def _write_invalid(
    store: ArtifactStore, rel: str, artifact: Any
) -> str:
    """Store a failed round; the store atomically preserves its predecessor."""
    if rel != GLOBAL_REL:
        return str(store.write_invalid(rel, artifact, gates_skipped=[]))
    sections = _read_main_text(store)
    if sections is None:
        raise UpstreamMissing(
            f'{MAIN_TEXT} 不在盘上——无法判定 Blueprint 称名投影是否可发布')
    grounding_payload = artifact.model_dump(by_alias=True, mode='json')
    naming_blockers = checks.blueprint_projection_blockers(
        global_g=grounding_payload,
        main_text=sections,
    )
    if naming_blockers:
        # 问题稿本身必须稳定落盘，但称名闭集尚不可信时不发布一份必然
        # ``unknown`` 的正式伴生产物。freshness 会直接从 latest INVALID
        # 报 ``global_naming_blocked``，修好后再由 0-LLM 路径物化投影。
        return str(store.write_invalid(rel, artifact, gates_skipped=[]))

    # schema-valid Global 被非称名 HARD 拦在 INVALID 时，已经足够确定
    # Blueprint 真正消费的称名闭集。两者必须同进同退，否则崩在中间会让
    # 旧投影为新名册继续背书。
    with store.history_transaction(_GLOBAL_INVALID, BLUEPRINT_CHARACTER_REL):
        path = store.write_invalid(rel, artifact, gates_skipped=[])
        _write_blueprint_character_projection(
            store, artifact, main_text=sections)
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
            existing, _ = store.read_own(
                GlobalGrounding, _GLOBAL_INVALID, require_current=True)
        if _equal_global_invalid_payload(artifact, existing):
            sections = _read_main_text(store)
            if sections is None:
                raise UpstreamMissing(
                    f'{MAIN_TEXT} 不在盘上——无法复验 Global INVALID companion')
            blockers = checks.blueprint_projection_blockers(
                global_g=artifact.model_dump(by_alias=True, mode='json'),
                main_text=sections,
            )
            if not blockers:
                _write_blueprint_character_projection(
                    store, artifact, main_text=sections)
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
    """正式提交后的独立 readback 终验；通过不等于 global review 已获人工 PASS。"""
    assert_global_main_text_current(ctx, prepared)
    store = ArtifactStore(ctx)
    _assert_blueprint_guard_current(ctx, prepared)
    if store.exists(_GLOBAL_INVALID):
        raise ProvenanceRefused(
            f'stage2.global 正式提交后仍有 live {_GLOBAL_INVALID}；'
            '不能把一对互相矛盾的 live 文件声明为 delivered')

    with ctx.validation_reads():
        landed, landed_raw = store.read_own(
            GlobalGrounding, GLOBAL_REL, require_current=True)
        review, _review_raw = store.read_own(
            GlobalReview, GLOBAL_REVIEW_REL, require_current=True)
        projection, _projection_raw = store.read_own(
            BlueprintCharacterProjection,
            BLUEPRINT_CHARACTER_REL,
            require_current=True,
        )
        global_freshness = store.freshness(GLOBAL_REL)
        review_freshness = store.freshness(GLOBAL_REVIEW_REL)

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
    if review_freshness.state != 'fresh':
        raise ProvenanceRefused(
            f'{GLOBAL_REVIEW_REL} readback freshness='
            f'{review_freshness.state}/{review_freshness.reason}，拒绝 delivered')
    require_current_review(landed_raw, review)

    expected_projection = materialize_blueprint_character_projection(
        expected_raw['entities']['characters'],
        copy.deepcopy(list(prepared._main_text)),
    )
    if (projection.model_dump(by_alias=True, mode='json')
            != expected_projection.model_dump(by_alias=True, mode='json')):
        raise ProvenanceRefused(
            f'{BLUEPRINT_CHARACTER_REL} readback 与本次 Global/MainText 投影不等值')

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


def _expected_global_companions(
        store: ArtifactStore,
        prepared: GlobalCandidateContext,
        artifact: GlobalGrounding,
) -> tuple[GlobalReview, BlueprintCharacterProjection]:
    previous = (
        store.read_own(GlobalReview, GLOBAL_REVIEW_REL)[0]
        if store.exists(GLOBAL_REVIEW_REL) else None)
    payload = artifact.model_dump(by_alias=True, mode='json')
    review = build_review(payload, previous)
    projection = materialize_blueprint_character_projection(
        payload['entities']['characters'],
        copy.deepcopy(list(prepared._main_text)),
    )
    return review, projection


def _global_bundle_readback(
        ctx: RunContext,
) -> dict[str, dict[str, Any]]:
    store = ArtifactStore(ctx)
    with ctx.validation_reads():
        global_g, _ = store.read_own(
            GlobalGrounding, GLOBAL_REL, require_current=True)
        review, _ = store.read_own(
            GlobalReview, GLOBAL_REVIEW_REL, require_current=True)
        projection, _ = store.read_own(
            BlueprintCharacterProjection,
            BLUEPRINT_CHARACTER_REL,
            require_current=True)
    return {
        GLOBAL_REL: global_g.model_dump(by_alias=True, mode='json'),
        GLOBAL_REVIEW_REL: review.model_dump(by_alias=True, mode='json'),
        BLUEPRINT_CHARACTER_REL: projection.model_dump(
            by_alias=True, mode='json'),
    }


def _finalize_agent_global_formal(
        ctx: RunContext,
        prepared: GlobalCandidateContext,
        terminal: GlobalCandidateEvaluation,
) -> GlobalGrounding:
    """Global bundle 的 prepared→write→readback→adoption→committed 尾窗。"""
    from novel.llm import stage2_global_runs

    artifact = terminal.candidate
    payload = artifact.model_dump(by_alias=True, mode='json')
    finalization = _global_finalization_payload(prepared, terminal)
    if terminal.source_attempt_id is None:
        raise ProvenanceRefused('stage2.global 正式 agent 候选缺 accepted attempt')
    store = ArtifactStore(ctx)
    touched = (
        GLOBAL_REL, GLOBAL_REVIEW_REL,
        BLUEPRINT_CHARACTER_REL, _GLOBAL_INVALID,
    )
    with stage2_global_runs.commit_ownership(ctx.novel_dir):
        stage2_global_runs.refuse_foreign_commit_reservations(ctx)
        journal = (
            stage2_global_runs.committed(ctx)
            or stage2_global_runs.prepared(ctx))
        if journal is None:
            _assert_global_guard_current(ctx, prepared)
            assert_global_main_text_current(ctx, prepared)
            journal = stage2_global_runs.prepare_commit(
                ctx,
                primary_payload=payload,
                finalization_payload=finalization,
                touched_paths=touched,
                attempt_id=terminal.source_attempt_id,
            )
        elif (not stage2_global_runs.verify_commit_primary(journal, payload)
              or not stage2_global_runs.verify_commit_finalization(
                  journal, finalization)):
            raise ProvenanceRefused(
                'stage2.global commit journal 与 terminal Global/subject 漂移')

        assert_global_main_text_current(ctx, prepared)
        expected_review, expected_projection = _expected_global_companions(
            store, prepared, artifact)
        expected = {
            GLOBAL_REL: artifact,
            GLOBAL_REVIEW_REL: expected_review,
            BLUEPRINT_CHARACTER_REL: expected_projection,
        }
        states = {
            rel: stage2_global_runs.target_state(
                ctx, journal, rel,
                expected_file_sha256=hashlib.sha256(
                    store._render(rel, value)).hexdigest())
            for rel, value in expected.items()
        }
        states[_GLOBAL_INVALID] = stage2_global_runs.target_state(
            ctx, journal, _GLOBAL_INVALID, expected_absent=True)

        if states[GLOBAL_REL] == 'baseline':
            assert_global_main_text_current(ctx, prepared)
            _write_formal(store, GLOBAL_REL, artifact)
        else:
            # 崩在 multi-file 写盘中段时只补缺失 companion；正式 Global 已精确
            # 等于 journal 主体就不得再造一轮伪历史。
            with store.history_transaction(
                    GLOBAL_REVIEW_REL, BLUEPRINT_CHARACTER_REL):
                if states[GLOBAL_REVIEW_REL] == 'baseline':
                    store.write_round(GLOBAL_REVIEW_REL, expected_review)
                if states[BLUEPRINT_CHARACTER_REL] == 'baseline':
                    _write_blueprint_character_projection(
                        store, artifact,
                        main_text=copy.deepcopy(list(prepared._main_text)))
            if states[_GLOBAL_INVALID] == 'baseline':
                _retire_invalid_for_matching_formal(
                    store, GLOBAL_REL, artifact)

        assert_global_main_text_current(ctx, prepared)
        landed = verify_global_delivery(ctx, prepared, artifact)
        _note_evidence_locator_corrections(
            ctx, store, terminal._evidence_locator_corrections,
            rel=GLOBAL_REL)
        assert_global_main_text_current(ctx, prepared)
        purpose, attempt_id = stage2_global_runs.restore_commit_adoption(
            ctx, journal)
        if purpose != ctx.node or attempt_id != terminal.source_attempt_id:
            raise ProvenanceRefused(
                'stage2.global commit adoption 不等于 terminal accepted attempt')
        landed_payloads = _global_bundle_readback(ctx)
        _note_restored_adoptions(
            ctx,
            rel=GLOBAL_REL,
            candidate=landed_payloads[GLOBAL_REL],
            adoptions=((purpose, None, attempt_id),),
        )
        assert_global_main_text_current(ctx, prepared)
        stage2_global_runs.mark_committed(
            ctx,
            primary_payload=landed_payloads[GLOBAL_REL],
            finalization_payload=finalization,
            landed_payloads=landed_payloads,
        )
        assert_global_main_text_current(ctx, prepared)
        committed = stage2_global_runs.committed(ctx)
        if committed is None:
            raise ProvenanceRefused(
                'stage2.global mark_committed 后没有 committed journal')
        stage2_global_runs.verify_committed_delivery(
            ctx,
            committed,
            primary_payload=landed_payloads[GLOBAL_REL],
            finalization_payload=finalization,
            landed_payloads=landed_payloads,
        )
        assert_global_main_text_current(ctx, prepared)
        return landed


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
            '`stage2.global --revalidate --actor <id> --reason <why>`'
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


def run_global(ctx: RunContext, client: LlmClient) -> GlobalGrounding:
    """旧 LlmClient 单轮入口；agent 多轮必须显式用 prepare/evaluate/finalize 三段。"""
    prepared = prepare_global_candidate(ctx)
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
    被替换的输入边变化，正文等其余来源仍必须 current。付费 ``--purge-role`` 不传
    本参数，继续按完整 freshness 关闭式拒绝。
    """
    rel = _GLOBAL_INVALID if store.exists(_GLOBAL_INVALID) else GLOBAL_REL
    if not store.exists(rel):
        raise UpstreamMissing(
            f'{GLOBAL_REL} 与 {_GLOBAL_INVALID} 都不在盘上——先跑 stage2.global')
    artifact, raw = store.read_own(
        GlobalGrounding,
        rel,
        require_current=True,
        replaced_input_keys=(() if replaced_edit_rel is None else (replaced_edit_rel,)),
    )
    return artifact, raw, rel


def _role_split_user(global_raw: dict[str, Any]) -> str:
    characters = ((global_raw.get('entities') or {}).get('characters') or {})
    secrets = []
    for secret in ((global_raw.get('canon') or {}).get('secrets') or []):
        secrets.append({
            '第几章揭示': secret.get('first_reveal_chapter'),
            '秘密': secret.get('fact'),
        })
    payload = {
        cid: {
            'canonical': character.get('canonical'),
            'role': character.get('role'),
            'fate': character.get('fate'),
        }
        for cid, character in characters.items()
    }
    compact = lambda value: json.dumps(
        value, ensure_ascii=False, separators=(',', ':'))
    return (
        '── 秘密清单（每条注明第几章才对读者揭示）──\n'
        + compact(secrets)
        + '\n\n── 人物表（role/fate 可能划错了，按判据重划）──\n'
        + compact(payload)
    )


def run_purge_role(ctx: RunContext, client: LlmClient) -> GlobalGrounding:
    """付费语义判官：只在原 char_id 上双向重划 role/fate。"""
    from novel.stages.stage2 import prompts
    from novel.llm import stage2_global_runs

    # 这是 legacy LlmClient 旁路：既不会消费 agent journal，也不能等花完钱
    # 才发现另一 run 持有 prepared。正式写前还会在同一 guard 下二次复核。
    stage2_global_runs.refuse_legacy_mutation_before_wire(ctx)

    store = ArtifactStore(ctx)
    # 把实际发给 provider 的 source rel + 完整文件 SHA 在同一所有权窗口冻结。
    # 只看 foreign prepared 不够：长 wire 期间另一 run 可能已经完整 committed，
    # 此时 journal 应当放行，但旧响应没有资格覆盖那份新底座。
    with stage2_global_runs.mutation_ownership(ctx, legacy_wire=True):
        old_artifact, old_raw, source_rel = _global_source(store)
        source_sha256 = store.full_sha256_or_none(source_rel)
        if source_sha256 is None:
            raise ProvenanceRefused(
                f'stage2.global --purge-role 无法冻结 {source_rel} 完整字节 SHA')
    main_text = _read_main_text(store)
    if main_text is None:
        raise UpstreamMissing(f'{MAIN_TEXT} 不在盘上')
    if old_artifact.provenance.model != client.model:
        raise TerminalState(
            '局部重划不能把不同主模型配置的结果混成一份来源账；'
            f'原稿 model={old_artifact.provenance.model!r}，本次={client.model!r}。'
            '为避免来源账说假话，尚未调用 LLM')

    system = with_json_shape(prompts.ROLE_SPLIT_SYS, prompts.ROLE_SPLIT_HINT)
    response = client.ask_json(
        system,
        _role_split_user(old_raw),
        model_cls=RoleSplitResponse,
        purpose=f'{ctx.node}:purge-role',
        max_tokens=ROLE_MAX_TOKENS,
    )
    returned = response.model_dump(by_alias=True)['characters']
    characters = ((old_raw.get('entities') or {}).get('characters') or {})
    if set(returned) != set(characters):
        missing = sorted(set(characters) - set(returned))
        extra = sorted(set(returned) - set(characters))
        raise TerminalState(
            f'role/fate 判官没有原样交回 char_id 集合：缺 {missing}，多 {extra}。'
            '响应已进缓存与成本账，但没有覆盖 artifact')

    data = copy.deepcopy(old_raw)
    data.pop('_provenance', None)
    out_characters = ((data.get('entities') or {}).get('characters') or {})
    fallback_chapter = min(
        (int(row.get('chapter_ordinal') or 0) for row in main_text), default=1)
    for cid, split in returned.items():
        character = out_characters[cid]
        role = split['role'].strip()
        fate = split['fate']
        if role != character.get('role'):
            old = character.get('role_evidence') or {}
            character['role_evidence'] = {
                'chapter_ordinal': old.get('chapter_ordinal') or fallback_chapter,
                'source': None,
            }
        character['role'] = role
        if fate is None:
            character['fate_evidence'] = None
        elif fate != character.get('fate'):
            old = character.get('fate_evidence') or character.get('role_evidence') or {}
            character['fate_evidence'] = {
                'chapter_ordinal': old.get('chapter_ordinal') or fallback_chapter,
                'source': None,
            }
        character['fate'] = fate

    data, accounting = normalize_global(data, main_text)

    old_prov = old_artifact.provenance
    delta = ctx.llm_provenance(
        GlobalProvenance,
        prompts={'ROLE_SPLIT_SYS': system},
        model=client.model,
        adopted_models=client.accepted_models(f'{ctx.node}:purge-role'),
        **accounting,
    )
    provenance = GlobalProvenance.model_validate({
        **old_prov.model_dump(),
        **accounting,
        'code': ctx.code,
        'inputs': delta.inputs,
        'prompts': {**old_prov.prompts, **delta.prompts},
        'adopted_models': sorted(set(old_prov.adopted_model_ids())
                                 | set(delta.adopted_model_ids())),
        'usage': combine_usage(old_prov.usage, delta.usage),
        'gates_skipped': None,
    })
    artifact = GlobalGrounding.model_validate({
        **data, '_provenance': provenance,
    })
    candidate = artifact.model_dump(by_alias=True)
    hits = _hard_hits(
        _judge_candidate(candidate, None, main_text),
        ignore={'upstream_missing'},
    )
    with stage2_global_runs.mutation_ownership(ctx, legacy_wire=True):
        current_source_rel = (
            _GLOBAL_INVALID if store.exists(_GLOBAL_INVALID) else GLOBAL_REL)
        current_source_sha256 = store.full_sha256_or_none(current_source_rel)
        if (current_source_rel != source_rel
                or current_source_sha256 != source_sha256):
            raise ProvenanceRefused(
                'stage2.global --purge-role provider 等待期间 Global source '
                f'底座漂移：start={source_rel}@{source_sha256}，'
                f'current={current_source_rel}@{current_source_sha256}；'
                '响应与成本账保留，但拒绝覆盖更新后的 Global bundle')
        if hits:
            path = _write_invalid(store, GLOBAL_REL, artifact)
            _note_evidence_locator_corrections(
                ctx, store, accounting.evidence_locator_corrections,
                rel=_GLOBAL_INVALID)
            raise GateBlocked(
                f'role/fate 重划后仍命中 {hits}。问题稿保留在 {_GLOBAL_INVALID}。'
                '改好后跑 `stage2.global --revalidate --actor <id> --reason <why>`'
                '（0 LLM、同轮重判并留不可变审计）。',
                invalid_path=path,
            )
        _write_formal(store, GLOBAL_REL, artifact)
        _note_evidence_locator_corrections(
            ctx, store, accounting.evidence_locator_corrections,
            rel=GLOBAL_REL)
        _note_adopted(ctx, client, rel=GLOBAL_REL, candidate=candidate,
                      purposes={f'{ctx.node}:purge-role': None})
        return artifact


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
) -> tuple[list[dict[str, Any]], list[str], list[Exception]]:
    """并发等待独立章节；一个失败不丢掉已经付费成功的其他章。

    `model_cls` 按契约 spec 传入——响应边界与 system 必须同一套，
    各选一半的失败形态是「prompt 是新的、校验还是旧的」而不会报错。
    """
    results: dict[int, dict[str, Any]] = {}
    violations: list[str] = []
    errors: list[Exception] = []
    should_restore = checkpoints if restore_checkpoints is None else restore_checkpoints

    concrete = [(int(o), s, u) for o, s, u in requests if o is not None]
    if not concrete:
        return [], violations, errors
    tracker = ChapterProgressTracker(ctx.observe, [o for o, _s, _u in concrete])
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
            except Exception as exc:  # 已成功的章仍必须保留。
                errors.append(exc)
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


def _boundary_validator(ordinal: int, paragraph_count: int) \
        -> Callable[[Any, Any], str | None]:
    """边界动态闸：召回完整，末尾只放行生产编译器能确定性恢复的范围。"""
    from novel.stages.stage2.compile_v2 import (
        TAIL_NUDGE_MAX_PARAS, tail_end_is_recoverable,
    )

    def validate(obj: Any, _resp: Any) -> str | None:
        matches = [bp for bp in obj.blueprints
                   if int(bp.chapter_ordinal) == ordinal]
        if len(matches) != 1:
            return f'ch{ordinal} 必须恰好返回一份，实际 {len(matches)} 份'
        blueprint = matches[0]
        micro_ends = [int(beat.end_para) for beat in blueprint.micro_events]
        beat_ends = [int(beat.end_para) for beat in blueprint.beats]
        for label, ends in (('micro_events', micro_ends), ('beats', beat_ends)):
            if any(left >= right for left, right in zip(ends, ends[1:])):
                return f'ch{ordinal} {label}.end_para 不是严格递增：{ends}'
            if any(end < 1 or end > paragraph_count for end in ends):
                return (f'ch{ordinal} {label}.end_para 越界 '
                        f'1..{paragraph_count}：{ends}')
            if not ends or not tail_end_is_recoverable(
                    ends[-1], paragraph_count):
                return (f'ch{ordinal} {label} 末项须止于最后一段 '
                        f'{paragraph_count}，或落在编译器尾部外扩帽 '
                        f'{TAIL_NUDGE_MAX_PARAS} 段内；实际 {ends}')
        outside = [end for end in beat_ends if end not in set(micro_ends)]
        if outside:
            return (f'ch{ordinal} beats 边界必须来自 micro_events，'
                    f'清单外止段：{outside}')
        return None
    return validate


def _fact_contract_error(ordinal: int, blueprint: Any, allowed: set[str]) -> str | None:
    """响应侧临时 atom_key 的闭包、义务与因果 DAG 动态闸。"""
    atoms = [atom for beat in blueprint.beats for atom in beat.fact_atoms]
    keys = [str(atom.atom_key) for atom in atoms]
    if len(keys) != len(set(keys)):
        return f'ch{ordinal} fact atom_key 重复：{keys}'
    known = set(keys)
    invalid_chars = sorted({
        str(cid) for atom in atoms for cid in atom.participant_char_ids
        if str(cid) not in allowed
    })
    if invalid_chars:
        return f'ch{ordinal} fact atom 参与者含清单外 char_id：{invalid_chars}'
    refs = [str(ref) for edge in blueprint.causal_edges
            for ref in (edge.before, edge.after)]
    refs += [str(ref) for ref in blueprint.entry_state_refs]
    refs += [str(ref) for ref in blueprint.ending_state_refs]
    missing = sorted(set(refs) - known)
    if missing:
        return f'ch{ordinal} 章级语义引用不存在：{missing}'
    obligation = {str(atom.atom_key): str(atom.obligation) for atom in atoms}
    if any(obligation[str(ref)] != 'context' for ref in blueprint.entry_state_refs):
        return f'ch{ordinal} entry_state_refs 只能引用 context 原子'
    if any(obligation[str(ref)] != 'land' for ref in blueprint.ending_state_refs):
        return f'ch{ordinal} ending_state_refs 只能引用 land 原子'
    graph: dict[str, list[str]] = {key: [] for key in keys}
    indegree = {key: 0 for key in keys}
    for edge in blueprint.causal_edges:
        before, after = str(edge.before), str(edge.after)
        if before == after:
            return f'ch{ordinal} causal_edges 存在自环：{before}'
        graph[before].append(after)
        indegree[after] += 1
    queue = [key for key, degree in indegree.items() if degree == 0]
    seen = 0
    while queue:
        key = queue.pop()
        seen += 1
        for after in graph[key]:
            indegree[after] -= 1
            if indegree[after] == 0:
                queue.append(after)
    if seen != len(keys):
        return f'ch{ordinal} causal_edges 不是 DAG'
    return None


def _detail_validator(ordinal: int, frozen: list[int], allowed: set[str]) \
        -> Callable[[Any, Any], str | None]:
    """细节传不能暗改边界，也不能从关闭式可见人物集合外造编号。"""
    def validate(obj: Any, _resp: Any) -> str | None:
        matches = [bp for bp in obj.blueprints
                   if int(bp.chapter_ordinal) == ordinal]
        if len(matches) != 1:
            return f'ch{ordinal} 必须恰好返回一份，实际 {len(matches)} 份'
        got = [int(beat.end_para) for beat in matches[0].beats]
        if got != frozen:
            return f'ch{ordinal} 冻结边界被改动：要 {frozen}，得 {got}'
        invalid = sorted({str(cid)
                          for beat in matches[0].beats
                          for cid in beat.present_char_ids
                          if str(cid) not in allowed})
        if invalid:
            return f'ch{ordinal} 在场者含清单外 char_id：{invalid}'
        return _fact_contract_error(ordinal, matches[0], allowed)
    return validate


def _scaffolded_validator(
    ordinal: int,
    paragraph_count: int,
    allowed: set[str],
    *,
    candidate_rows: list[dict[str, Any]] | None = None,
    narration_refs: bool = False,
) -> Callable[[Any, Any], str | None]:
    """单调用脚手架的动态闸：边界召回、关闭人物与候选所属拍一次验完。"""
    boundary = _boundary_validator(ordinal, paragraph_count)
    candidates = {
        str(row['candidate_id']): row for row in (candidate_rows or [])
    }

    def validate(obj: Any, resp: Any) -> str | None:
        boundary_error = boundary(obj, resp)
        if boundary_error is not None:
            return boundary_error
        blueprint = next(bp for bp in obj.blueprints
                         if int(bp.chapter_ordinal) == ordinal)
        invalid = sorted({
            str(cid)
            for beat in blueprint.beats
            for cid in beat.present_char_ids
            if str(cid) not in allowed
        })
        if invalid:
            return f'ch{ordinal} 在场者含清单外 char_id：{invalid}'
        fact_error = _fact_contract_error(ordinal, blueprint, allowed)
        if fact_error is not None:
            return fact_error
        if narration_refs:
            invalid_narration = sorted({
                str(cid)
                for narration in blueprint.narration
                for cid in (narration.narrator_char_id,
                            narration.focalizer_char_id)
                if cid is not None and str(cid) not in allowed
            })
            if invalid_narration:
                return (f'ch{ordinal} 叙述身份含清单外 char_id：'
                        f'{invalid_narration}')

        start_para = 1
        for beat in blueprint.beats:
            for hinge in beat.hinges:
                candidate_id = getattr(hinge, 'candidate_id', None)
                if candidate_id is None:
                    continue
                row = candidates.get(str(candidate_id))
                if row is None:
                    return (f'ch{ordinal} hinge 含清单外 candidate_id：'
                            f'{candidate_id}')
                para = int(row['para'])
                if not start_para <= para <= int(beat.end_para):
                    return (f'ch{ordinal} hinge 候选 {candidate_id} 位于第 {para} 段，'
                            f'不在所属拍 {start_para}..{beat.end_para}')
            start_para = int(beat.end_para) + 1
        return None

    return validate


def _repair_baseline(blueprint: dict[str, Any],
                     main_text: list[dict[str, Any]],
                     *, projection: Any) -> dict[str, Any]:
    """最终产物 → 模型响应形状；代码派生字段不反喂给模型。

    `projection` 必须按契约显式传入——基线形状必须与本次请求的响应
    契约同构，否则模型收到的「上一版」长得跟它要交的东西不是一回事。
    ⚠ 要章正文才反投影得出定位（段号）——**取的必须是这条蓝图自己那一章**，
    拿错章反算出来的段号一样是整数、一样不会报错。
    """
    ordinal = int(blueprint.get('chapter_ordinal') or 0)
    text = next((str(section.get('text') or '') for section in main_text
                 if int(section.get('chapter_ordinal') or 0) == ordinal), '')
    return projection(blueprint, text)


def _mentions_chapter(value: str, ordinal: int) -> bool:
    return bool(
        re.search(rf'\b[bfh]{ordinal:02d}_', value)
        or re.search(rf'\bch0*{ordinal}(?!\d)', value)
    )


def _repair_findings(
    outcomes: list[Outcome], ordinal: int
) -> list[dict[str, str]]:
    """只把目标章自己的 HARD 证据下发，绝不把未来章失败带进较早章节。"""
    rows: list[dict[str, str]] = []
    for outcome in outcomes:
        if REGISTRY.get(outcome.rule).sev != 'hard':
            continue
        evidence = sorted({
            str(value)
            for item in outcome.findings
            for value in item.evidence
            if _mentions_chapter(str(value), ordinal)
        })
        if not evidence:
            continue
        rows.append({
            'rule': outcome.rule,
            'item': REGISTRY.get(outcome.rule).item,
            'detail': '确定性校验证据：'
                      + json.dumps(evidence, ensure_ascii=False, separators=(',', ':')),
        })
    if not rows:
        rows.append({
            'rule': 'manual_regeneration',
            'item': '人工指定重生成',
            'detail': f'用户明确要求重新抽取阅读序第 {ordinal} 章；正文与结构基线优先。',
        })
    return rows


def _record_is_targeted(row: Any, targets: set[int]) -> bool:
    if not isinstance(row, (list, tuple)) or not row:
        return False
    match = re.match(r'[bfh](\d{2,})_', str(row[0]))
    return bool(match and int(match.group(1)) in targets)


def _keep_scope_violation(row: str, targets: set[int]) -> bool:
    try:
        requested = set(BlueprintScopeViolation.model_validate_json(row).requested)
    except (TypeError, ValueError, ValidationError):
        return True
    return requested.isdisjoint(targets)


def _refresh_commit_payload(
        *, chapters: tuple[int, ...], character_projection: dict[str, Any],
        main_text: list[dict[str, Any]], candidate: dict[str, Any],
        live_before: dict[str, str | None] | None = None,
) -> dict[str, Any]:
    """提交尾窗只重验当前输入与最终候选；原 baseline/prompt 身份仍由 run/checkpoint 守。"""
    payload: dict[str, Any] = {
        'operation': 'stage2.blueprint --chapters',
        'chapters': list(chapters),
        'character_projection': character_projection,
        'main_text': main_text,
        'artifact': candidate,
    }
    if live_before is not None:
        payload['live_before'] = live_before
    return payload


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
        live_artifact, _raw = store.read_own(
            ChapterBlueprints, BLUEPRINT_REL, require_current=True)
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


def _finish_refresh_commit_tail(
        ctx: RunContext, *, chapters: tuple[int, ...], store: ArtifactStore,
        main_text: list[dict[str, Any]],
        character_projection: dict[str, Any],
) -> CheckReport | None:
    """formal 已落而 committed 未落时，零 wire 补 adoption/哨兵并返回联合报告。"""
    from novel.llm import stage2_runs

    _require_blueprint_code_current(ctx, moment='定向 commit tail 开始')
    ctx.require_observed_roots_current()
    journal = stage2_runs.commit_state(ctx)
    if journal is None:
        return None
    if journal.get('artifact') != BLUEPRINT_REL:
        raise ProvenanceRefused(
            f'run={ctx.run_id} 的 commit journal 指向 {journal.get("artifact")!r}，'
            f'不是 {BLUEPRINT_REL}')
    if not store.exists(BLUEPRINT_REL):
        if journal.get('state') == 'committed':
            raise ProvenanceRefused(
                f'run={ctx.run_id} 已 committed，但正式 blueprint 已缺失')
        return None

    formal, _formal_raw = store.read_own(
        ChapterBlueprints, BLUEPRINT_REL, require_current=True)
    candidate = formal.model_dump(by_alias=True)
    if not stage2_runs.verify_commit_artifact(journal, candidate):
        if journal.get('state') == 'committed':
            raise ProvenanceRefused(
                f'run={ctx.run_id} 已 committed，但正式 blueprint 与 journal 不一致')
        # prepared 写在正式替换之前：此时 live 仍是原 baseline，继续走 checkpoint
        # 重编译即可。若 live 是第三方改写，后面的 run subject/request 绑定会关闭式拒绝。
        return None
    stored_finalization = journal.get('finalization_payload')
    stored_live_before = (
        stored_finalization.get('live_before')
        if isinstance(stored_finalization, dict) else None
    )
    finalization = _refresh_commit_payload(
        chapters=chapters, character_projection=character_projection,
        main_text=main_text, candidate=candidate,
        live_before=(stored_live_before
                     if isinstance(stored_live_before, dict) else None))
    if not stage2_runs.verify_commit_finalization(journal, finalization):
        raise ProvenanceRefused(
            f'run={ctx.run_id} 的提交尾窗输入/称名投影/目标章已经漂移；'
            '拒绝补猜 committed')
    hits = _blueprint_hard_hits(_judge_candidate(None, candidate, main_text))
    if hits:
        raise ProvenanceRefused(
            f'run={ctx.run_id} 的正式 blueprint 不再通过自身 HARD：{hits}')
    if store.exists(_INVALID):
        if journal.get('state') == 'committed':
            raise ProvenanceRefused(
                f'run={ctx.run_id} 已 committed，但仍残留 {_INVALID}')
        # 正式候选已经逐字命中 journal，只归档 INVALID；若再走 write_round，
        # 会把同一正式稿凭空多记成一轮，破坏 commit 尾窗幂等。
        _retire_invalid_for_matching_formal(store, BLUEPRINT_REL, formal)
    restored = stage2_runs.restore_commit_adoptions(ctx, journal)
    _note_restored_adoptions(
        ctx, rel=BLUEPRINT_REL, candidate=candidate, adoptions=restored)
    _require_blueprint_code_current(ctx, moment='定向 commit tail 哨兵前')
    ctx.require_observed_roots_current()
    stage2_runs.mark_committed(ctx)
    return verify_blueprint_delivery(ctx, formal)


def _run_blueprint_repair(
    ctx: RunContext,
    client: LlmClient,
    *,
    chapters: tuple[int, ...],
    store: ArtifactStore,
    main_text: list[dict[str, Any]],
    character_projection: dict[str, Any],
) -> CheckReport:
    """只重生成目标章，合并未动章节并记录完整 repair 账。

    ``--chapters`` 是主动刷新，所以绝不读取别的 logical run 留下的旧
    checkpoint；但本轮并发波次已经付费通过的章仍写进自己的 refresh run，供同一
    ``run_id`` 的唯一 batch resume 恢复。两相契约也共用这一份 manifest 人工额度。
    """
    if ctx.cache.name != 'refresh-write':
        raise TerminalState(
            'stage2.blueprint --chapters 必须使用 refresh-write：'
            '定向重生成不得读回原请求缓存')
    finalized = _finish_refresh_commit_tail(
        ctx, chapters=chapters, store=store,
        main_text=main_text, character_projection=character_projection)
    if finalized is not None:
        return finalized
    source_rel = _INVALID if store.exists(_INVALID) else BLUEPRINT_REL
    if not store.exists(source_rel):
        raise UpstreamMissing(
            f'{BLUEPRINT_REL} 与 {_INVALID} 都不在盘上——定向重生成没有结构基线')
    previous, previous_raw = store.read_own(
        ChapterBlueprints, source_rel, require_current=True)
    if previous.provenance.model != client.model:
        raise TerminalState(
            '定向重生成不能把不同主模型配置的结果混成一份来源账；'
            f'原稿 model={previous.provenance.model!r}，本次={client.model!r}。'
            '为避免来源账说假话，尚未调用 LLM')

    from novel.stages.stage2.contract import contract_for

    # ⚠ 修复按**基线产物的 architecture** 选契约——修复必须与基线同构，配置说了不算。
    baseline_arch = previous.provenance.context.architecture
    spec = contract_for(baseline_arch)
    if _active_spec().architecture != baseline_arch:
        raise TerminalState(
            f'配置选的契约是 {_active_spec().architecture!r}，而基线产物是 '
            f'{baseline_arch!r}——定向重生成必须与基线同构。要换契约先整跑 '
            'stage2.blueprint（整体切换架构）。尚未调用 LLM')
    system = spec.system
    old_system = previous.provenance.prompts.get(spec.sys_name)
    if old_system != prov.fingerprint(system):
        raise TerminalState(
            f'定向重生成的 {spec.sys_name} 与基线产物不同，而现有 provenance '
            '没有表达“部分章节使用另一版 system”的槽位；尚未调用 LLM')
    if spec.topology == 'boundary_then_detail':
        if spec.boundary_system is None:
            raise RuntimeError(f'{spec.architecture} 两段拓扑没有登记 boundary_system')
        old_boundary = previous.provenance.prompts.get(
            str(spec.boundary_sys_name or ''))
        if old_boundary != prov.fingerprint(spec.boundary_system):
            raise TerminalState(
                f'定向重生成的 {spec.boundary_sys_name} 与基线产物不同，而现有 '
                'provenance 没有表达“部分章节使用另一版边界 system”的槽位；'
                '尚未调用 LLM')

    from novel.llm import stage2_runs

    policy_phases = (
        [('repair-boundaries', spec.boundary_response_model,
          BOUNDARY_MAX_TOKENS, False),
         ('repair', spec.response_model, BLUEPRINT_MAX_TOKENS,
          spec.prefers_content_over_reasoning_salvage)]
        if spec.topology == 'boundary_then_detail' else
        [('repair', spec.response_model, BLUEPRINT_MAX_TOKENS,
          spec.prefers_content_over_reasoning_salvage)]
    )
    # 定向刷新最终会把未改章节与目标章重新合并；只哈目标 prompt 抓不到未改基线
    # 在暂停期间被人改写。把完整 baseline/称名投影/main_text 与目标集压成一枚主体指纹，
    # resume 时任何输入漂移都会在首个 wire 前关闭式停止。
    refresh_subject = prov.projection_fingerprint({
        'operation': 'stage2.blueprint --chapters',
        'chapters': list(chapters),
        'baseline': previous_raw,
        'character_projection': character_projection,
        'main_text': main_text,
    })
    refresh_run_ready = False

    def ensure_refresh_run(
            initial_requests: list[tuple[int | None, str, str]],
    ) -> None:
        nonlocal refresh_run_ready
        if refresh_run_ready:
            return
        stage2_runs.ensure_run(
            ctx,
            architecture=spec.architecture,
            topology=spec.topology,
            requests=initial_requests,
            request_policy=client.json_request_policy(
                policy_phases, recovery_policy='stage2-blueprint-v1'),
            refresh=True,
            operation='chapters-refresh',
            subject_fingerprint=refresh_subject,
            resume_flags=(
                '--chapters', ','.join(str(chapter) for chapter in chapters)),
        )
        refresh_run_ready = True

    expected_context = blueprint_context(ctx.title, main_text,
                                         architecture=baseline_arch)
    if previous.provenance.context.model_dump() != expected_context:
        raise TerminalState(
            '定向重生成不能把 cumulative-past 与 bounded-fidelity 两种上下文'
            '混进同一份 provenance；请先整跑 stage2.blueprint。尚未调用 LLM')

    old_by = {
        int(blueprint.get('chapter_ordinal') or 0): blueprint
        for blueprint in (previous_raw.get('blueprints') or [])
    }
    missing_baselines = set(chapters) - set(old_by)

    before_outcomes = _judge_candidate(None, previous_raw, main_text)
    prompt_rows: dict[int, tuple[str, list[dict[str, str]]]] = {}
    findings_of: dict[int, list[dict[str, str]]] = {}
    baselines: dict[int, dict[str, Any]] = {}
    frozen: dict[int, list[int]] = {}
    boundary_events: dict[int, list[str]] = {}
    boundary_requests: list[tuple[int | None, str, str]] = []
    visible: dict[int, dict[str, list[str]]] = {}
    candidate_rows: dict[int, list[dict[str, Any]]] = {}
    character_inventory = blueprint_inventory_from_projection(
        character_projection)
    visible = character_inventory['visible_character_refs']
    past_names = character_inventory['past_attested_surfaces']
    if spec.uses_hinge_candidates:
        candidate_rows = hinge_candidates_by_chapter(main_text)
    for ordinal in chapters:
        backfill = ordinal in missing_baselines
        findings = ([{
            'rule': 'chapter_coverage',
            'item': f'missing:{ordinal}',
            'detail': (f'首轮问题稿缺少阅读序第 {ordinal} 章；'
                       '按首轮同形请求补回，不伪造上一版结构基线。'),
        }] if backfill else _repair_findings(before_outcomes, ordinal))
        findings_of[ordinal] = findings
        if not backfill:
            baselines[ordinal] = _repair_baseline(
                old_by[ordinal], main_text, projection=spec.projection_fn)
        if spec.topology == 'boundary_then_detail':
            baseline_beats = (
                [] if backfill else (baselines[ordinal].get('beats') or []))
            # ⚠⚠ **`end_para` 可以是 `None`。** `canonical_ir_projection` 对定位不到的
            #   span（`span_unlocatable` 报的正是这一条）如实交 `None`，不兜底——
            #   而这里原来直接 `int(beat['end_para'])`：`TypeError` ⇒ exit 70，
            #   且发生在**任何 LLM 调用之前**，人拿到的是一个崩溃而不是判定。
            #   语义上它本来就该重切边界：连区间都对不上原文，冻结它没有意义。
            unlocatable = [index for index, beat in enumerate(baseline_beats)
                           if beat.get('end_para') is None]
            needs_resegment = backfill or bool(unlocatable) or bool(
                {row['rule'] for row in findings}
                & {'beats_coarse', 'beats_collapsed'})
            if unlocatable:
                ctx.observe(
                    'repair_baseline_boundary_unlocatable',
                    chapter_ordinal=ordinal, beats=unlocatable,
                    total_beats=len(baseline_beats))
            if needs_resegment:
                boundary_requests.append((
                    ordinal,
                    spec.boundary_system,
                    build_blueprint_user(
                        ctx.title, main_text, (ordinal,),
                        past_names=past_names.get(ordinal, []),
                        repair_findings=(None if backfill else findings),
                    ),
                ))
            else:
                frozen[ordinal] = [int(beat['end_para'])
                                   for beat in baseline_beats]
                boundary_events[ordinal] = [
                    '；'.join(str(atom.get('claim') or '')
                              for atom in (beat.get('fact_atoms') or []))
                    for beat in baseline_beats]

    boundary_scope: list[str] = []
    boundary_errors: list[Exception] = []
    if boundary_requests:
        if spec.boundary_response_model is None:
            raise RuntimeError(
                f'{spec.architecture} 登记了 boundary_system 却没有响应模型')
        paragraph_count = {
            int(section.get('chapter_ordinal') or 0):
            len(paragraph_spans(str(section.get('text') or '')))
            for section in main_text
        }
        ensure_refresh_run(boundary_requests)
        returned_boundaries, boundary_scope, boundary_errors = \
            _run_chapter_requests(
                ctx,
                client,
                boundary_requests,
                model_cls=spec.boundary_response_model,
                purpose_of=lambda ordinal:
                f'{ctx.node}:repair-boundaries:ch{ordinal}',
                validator_of=lambda ordinal: _boundary_validator(
                    ordinal, paragraph_count[ordinal]),
                max_tokens=BOUNDARY_MAX_TOKENS,
                phase='repair-boundaries', architecture=spec.architecture,
                checkpoints=True, restore_checkpoints=True,
            )
        _raise_response_repair(
            ctx, boundary_errors, architecture=spec.architecture)
        for blueprint in returned_boundaries:
            ordinal = int(blueprint.get('chapter_ordinal') or 0)
            frozen[ordinal] = [int(beat['end_para'])
                               for beat in (blueprint.get('beats') or [])]
            boundary_events[ordinal] = [str(beat.get('event') or '')
                                        for beat in (blueprint.get('beats') or [])]

    requests: list[tuple[int | None, str, str]] = []
    for ordinal in chapters:
        if spec.topology == 'boundary_then_detail' and ordinal not in frozen:
            continue
        user = build_blueprint_user(
            ctx.title, main_text, (ordinal,),
            past_names=past_names.get(ordinal, []),
            repair_findings=(None if ordinal in missing_baselines
                             else findings_of[ordinal]),
            repair_baseline=baselines.get(ordinal),
            character_refs=visible.get(ordinal, {}),
            hinge_candidates=(candidate_rows.get(ordinal, [])
                              if spec.uses_hinge_candidates else None),
            frozen_boundaries=(frozen[ordinal]
                               if spec.topology == 'boundary_then_detail' else None),
            boundary_events=(boundary_events.get(ordinal)
                             if spec.topology == 'boundary_then_detail' else None),
        )
        prompt_rows[ordinal] = (user, findings_of[ordinal])
        requests.append((ordinal, system, user))

    paragraph_count = {
        int(section.get('chapter_ordinal') or 0):
        len(paragraph_spans(str(section.get('text') or '')))
        for section in main_text
    }
    validator_of = None
    if spec.topology == 'boundary_then_detail':
        validator_of = lambda ordinal: _detail_validator(
            ordinal, frozen[ordinal], set(visible.get(ordinal, {})))
    elif spec.topology == 'scaffolded_single':
        validator_of = lambda ordinal: _scaffolded_validator(
            ordinal,
            paragraph_count[ordinal],
            set(visible.get(ordinal, {})),
            candidate_rows=candidate_rows.get(ordinal, []),
            narration_refs=True,
        )
    # 没有边界重切请求时，detail 是本轮第一个真实请求面；它必须先铸 run
    # identity。已有边界相时 helper 是幂等 no-op，继续沿用同一身份。
    ensure_refresh_run(requests)
    returned, detail_scope, detail_errors = _run_chapter_requests(
        ctx,
        client,
        requests,
        model_cls=spec.response_model,
        validator_of=validator_of,
        prefer_content_over_reasoning_salvage=(
            spec.prefers_content_over_reasoning_salvage),
        phase='repair', architecture=spec.architecture,
        checkpoints=True, restore_checkpoints=True,
    )
    _raise_response_repair(
        ctx, detail_errors, architecture=spec.architecture)
    new_scope = boundary_scope + detail_scope
    errors = boundary_errors + detail_errors
    if not returned:
        if errors:
            raise errors[0]
        raise TerminalState('定向重生成没有任何目标章返回可接纳的响应')

    target_data, target_accounting = spec.compile_fn(
        {'blueprints': returned},
        main_text,
        character_inventory=character_inventory,
    )
    normalized_by = {
        int(blueprint.get('chapter_ordinal') or 0): blueprint
        for blueprint in target_data['blueprints']
    }
    ordinals = [
        int(section.get('chapter_ordinal') or 0) for section in main_text
    ]
    merged_by = copy.deepcopy(old_by)
    merged_by.update(normalized_by)
    merged = [merged_by[ordinal] for ordinal in ordinals if ordinal in merged_by]

    targets = set(chapters)
    old_prov = previous.provenance
    old_failures = [
        list(row) for row in old_prov.verbatim_failures
        if not _record_is_targeted(row, targets)
    ]
    old_fact_failures = [
        list(row) for row in old_prov.fact_atom_failures
        if not _record_is_targeted(row, targets)
    ]
    failures = old_failures + list(target_accounting['verbatim_failures'])
    fact_failures = old_fact_failures + list(
        target_accounting.get('fact_atom_failures') or [])

    repaired_ordinals = sorted(normalized_by)
    for ordinal in repaired_ordinals:
        accepted_model = client.accepted_model(f'{ctx.node}:ch{ordinal}')
        if accepted_model is None:
            raise ProvenanceRefused(
                f'第 {ordinal} 章已经返回蓝图，但没有 accepted model；'
                '拒绝按主模型配置估算')

    old_scope = [
        row for row in old_prov.scope_violations
        if _keep_scope_violation(row, targets)
    ]
    pending_rows = [
        list(row) for row in old_prov.hinge_ambiguous_pending
        if not _record_is_targeted(row, targets)
    ] + [list(row) for row
         in (target_accounting.get('hinge_ambiguous_pending') or [])]
    repair_purposes: dict[str, int | None] = {
        f'{ctx.node}:ch{o}': o for o in repaired_ordinals}
    resegmented = {int(ordinal) for ordinal, _system, _user
                   in boundary_requests if ordinal is not None}
    repair_purposes.update({
        f'{ctx.node}:repair-boundaries:ch{o}': o
        for o in repaired_ordinals if o in resegmented
    })

    def current_artifact(*, settle: bool = True) -> ChapterBlueprints:
        """把这一刻的 `merged` 铸成产物。"""
        keys = {spec.sys_name: system}
        if spec.boundary_sys_name and spec.boundary_system:
            keys[spec.boundary_sys_name] = spec.boundary_system
        delta = ctx.llm_provenance(
            BlueprintProvenance,
            prompts=keys,
            model=client.model,
            adopted_models=client.accepted_models(*repair_purposes),
            scope_violations=old_scope + new_scope,
            context=old_prov.context.model_dump(),
            verbatim_failures=failures,
            fact_atom_failures=fact_failures,
            hinge_ambiguous_pending=pending_rows,
            settle=settle,
        )
        provenance = BlueprintProvenance.model_validate({
            **delta.model_dump(),
            'adopted_models': sorted(set(old_prov.adopted_model_ids())
                                     | set(delta.adopted_model_ids())),
            'usage': combine_usage(old_prov.usage, delta.usage),
            'partial_rewrite': old_prov.partial_rewrite,
        })
        return ChapterBlueprints.model_validate({
            'blueprints': merged,
            '_provenance': provenance,
        })

    not_returned = sorted(targets - set(repaired_ordinals))

    def block(artifact: ChapterBlueprints, hits: list[str]) -> None:
        path = _write_invalid(store, BLUEPRINT_REL, artifact)
        extra = []
        if errors:
            extra.append(f'{len(errors)} 个章节调用失败')
        if not_returned:
            extra.append(f'未返回目标章 {not_returned}')
        suffix = f'；{"；".join(extra)}' if extra else ''
        raise GateBlocked(
            f'stage2.blueprint 定向重生成后仍命中 {hits}{suffix}。'
            f'问题稿已存 {_INVALID}，正式产物没有被覆盖。改好后跑 '
            '`stage2.blueprint --revalidate --actor <id> --reason <why>`'
            '（0 LLM、同轮重判并留不可变审计）。',
            invalid_path=path,
        )

    artifact = current_artifact()
    candidate = artifact.model_dump(by_alias=True)
    outcomes = _judge_candidate(None, candidate, main_text)
    hits = _blueprint_hard_hits(outcomes)
    if hits or errors or not_returned:
        block(artifact, hits)

    adoptions = _accepted_adoptions(client, repair_purposes)
    live_before = _blueprint_live_state(store)
    stage2_runs.prepare_commit(
        ctx,
        artifact=BLUEPRINT_REL,
        artifact_fingerprint=prov.projection_fingerprint(candidate),
        artifact_payload=candidate,
        finalization_payload=_refresh_commit_payload(
            chapters=chapters, character_projection=character_projection,
            main_text=main_text, candidate=candidate,
            live_before=live_before),
        adoptions=adoptions,
    )
    _require_blueprint_code_current(ctx, moment='定向正式写盘前')
    ctx.require_observed_roots_current()
    _write_formal(store, BLUEPRINT_REL, artifact)
    report = verify_blueprint_delivery(ctx, artifact)
    _require_blueprint_code_current(ctx, moment='定向 adoption 前')
    ctx.require_observed_roots_current()
    _note_adopted(ctx, client, rel=BLUEPRINT_REL, candidate=candidate,
                  purposes=repair_purposes)
    _require_blueprint_code_current(ctx, moment='定向 committed 哨兵前')
    ctx.require_observed_roots_current()
    stage2_runs.mark_committed(ctx)
    return report


def request_blueprints(ctx: RunContext, client: _BlueprintRequestSource, *,
                       chapters: tuple[int, ...] = (),
                       checkpoints: bool = True) -> dict[str, Any]:
    """发出**首轮形状**的逐章请求。`chapters` 为空 = 整跑（生产唯一用法）。

    ⚠ `chapters` 只收窄「发哪几章」，一个字节都不改每章的 `(system, user)`
    ——blueprint 的 user 只依赖 (书名, main_text, 目标章号, 名册)，与「本次跑几章」
    无关。⇒ 子集跑出来的响应会原样命中整跑的缓存。契约实验的高难包冒烟
    （`tools/smoke_stage2_ir1.py`）靠的就是这条性质，由
    `tests/test_stage2_smoke_ir1.py` 钉住；**它不是给生产多开一条「只跑几章」的路**
    （那条路是 `--chapters` 定向重生成，形状完全不同：带基线与 findings）。

    ``checkpoints=False`` 只供这个明确承诺“书目录零改动”的付费诊断工具使用；
    正式 producer 保持默认开启。诊断响应仍按普通 cache/ledger/audit 留账，但不创建
    一个之后没有正式 commit/resume 消费者的孤立 `_stage2_runs`。
    """
    store = ArtifactStore(ctx)
    main_text = _read_main_text(store)
    assert main_text is not None
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
    # prompt / validator / compiler 必须消费同一份冻结投影。此前 build_prompts
    # 会独立再读一次 current，Global 称名在两次读取间变化时会把一个 batch
    # 内部先拼成 P0 validator + P1 prompt，wire 之后又按 P2 编译。
    requests = build_prompts(
        ctx, chapters=chapters,
        character_projection=character_projection,
        main_text=main_text)
    if (spec.topology == 'boundary_then_detail'
            and (spec.boundary_system is None
                 or spec.boundary_response_model is None)):
        raise RuntimeError(f'{spec.architecture} 登记了 boundary topology 却没有响应模型')
    policy_phases = (
        [('boundaries', spec.boundary_response_model, BOUNDARY_MAX_TOKENS, False),
         ('detail', spec.response_model, BLUEPRINT_MAX_TOKENS, False)]
        if spec.topology == 'boundary_then_detail' else
        [('blueprint', spec.response_model, BLUEPRINT_MAX_TOKENS,
          spec.prefers_content_over_reasoning_salvage)]
    )
    if checkpoints:
        from novel.llm import stage2_runs
        stage2_runs.ensure_run(
            ctx, architecture=spec.architecture, topology=spec.topology,
            requests=requests,
            request_policy=client.json_request_policy(
                policy_phases, recovery_policy='stage2-blueprint-v1'),
            refresh=ctx.cache.name == 'refresh-write')
    # checkpoint 不是 `.llm_cache`：普通 cache 即使在内部测试里 disabled，
    # 同 run 的已验证章节仍应恢复。只有明确要求新采样的 refresh-write 绕过旧 checkpoint。
    use_checkpoints = checkpoints and ctx.cache.name != 'refresh-write'
    if spec.topology != 'boundary_then_detail':
        validator_of = None
        if spec.topology == 'scaffolded_single':
            visible = character_inventory['visible_character_refs']
            candidate_rows = (hinge_candidates_by_chapter(main_text)
                              if spec.uses_hinge_candidates else {})
            paragraph_count = {
                int(section.get('chapter_ordinal') or 0):
                len(paragraph_spans(str(section.get('text') or '')))
                for section in main_text
            }
            validator_of = lambda ordinal: _scaffolded_validator(
                ordinal,
                paragraph_count[ordinal],
                set(visible.get(ordinal, {})),
                candidate_rows=candidate_rows.get(ordinal, []),
                narration_refs=True,
            )
        raw, violations, errors = _run_chapter_requests(
            ctx, client, requests, model_cls=spec.response_model,
            validator_of=validator_of,
            prefer_content_over_reasoning_salvage=(
                spec.prefers_content_over_reasoning_salvage),
            phase='blueprint', architecture=spec.architecture,
            checkpoints=checkpoints, restore_checkpoints=use_checkpoints)
        _raise_response_repair(ctx, errors, architecture=spec.architecture)
        return {
            'main_text': main_text,
            'character_projection': character_projection_raw,
            'requests': requests,
            'raw_blueprints': raw,
            'scope_violations': violations,
            'errors': errors,
            'contract': spec.architecture,
        }

    assert spec.boundary_system is not None and spec.boundary_response_model is not None
    paragraph_count = {
        int(section.get('chapter_ordinal') or 0):
        len(paragraph_spans(str(section.get('text') or '')))
        for section in main_text
    }
    boundary_raw, boundary_violations, boundary_errors = _run_chapter_requests(
        ctx,
        client,
        requests,
        model_cls=spec.boundary_response_model,
        purpose_of=lambda ordinal: f'{ctx.node}:boundaries:ch{ordinal}',
        validator_of=lambda ordinal: _boundary_validator(
            ordinal, paragraph_count[ordinal]),
        max_tokens=BOUNDARY_MAX_TOKENS,
        phase='boundaries', architecture=spec.architecture,
        checkpoints=checkpoints, restore_checkpoints=use_checkpoints,
    )
    _raise_response_repair(
        ctx, boundary_errors, architecture=spec.architecture)
    frozen = {
        int(blueprint.get('chapter_ordinal') or 0):
        [int(beat['end_para']) for beat in (blueprint.get('beats') or [])]
        for blueprint in boundary_raw
    }
    boundary_events = {
        int(blueprint.get('chapter_ordinal') or 0):
        [str(beat.get('event') or '') for beat in (blueprint.get('beats') or [])]
        for blueprint in boundary_raw
    }
    visible = character_inventory['visible_character_refs']
    past_names = character_inventory['past_attested_surfaces']
    detail_requests = [
        (ordinal, spec.system, build_blueprint_user(
            ctx.title,
            main_text,
            (ordinal,),
            past_names=past_names.get(ordinal, []),
            character_refs=visible.get(ordinal, {}),
            frozen_boundaries=frozen[ordinal],
            boundary_events=boundary_events.get(ordinal),
        ))
        for ordinal, _system, _user in requests
        if ordinal is not None and ordinal in frozen
    ]
    detail_raw, detail_violations, detail_errors = _run_chapter_requests(
        ctx,
        client,
        detail_requests,
        model_cls=spec.response_model,
        validator_of=lambda ordinal: _detail_validator(
            ordinal, frozen[ordinal], set(visible.get(ordinal, {}))),
        phase='detail', architecture=spec.architecture,
        checkpoints=checkpoints, restore_checkpoints=use_checkpoints,
    )
    _raise_response_repair(ctx, detail_errors, architecture=spec.architecture)
    return {
        'main_text': main_text,
        'character_projection': character_projection_raw,
        'requests': detail_requests,
        'boundary_requests': requests,
        'frozen_boundaries': frozen,
        'boundary_events': boundary_events,
        'raw_blueprints': detail_raw,
        'scope_violations': boundary_violations + detail_violations,
        'errors': boundary_errors + detail_errors,
        'contract': spec.architecture,
    }


def _blueprint_batch_sha256(batch: dict[str, Any]) -> str:
    """冻结候选批次；异常只取稳定类型/文字，不把对象身份当成业务输入。"""
    return _canonical_sha256({
        'main_text': batch.get('main_text'),
        'character_projection': batch.get('character_projection'),
        'requests': batch.get('requests'),
        'boundary_requests': batch.get('boundary_requests'),
        'frozen_boundaries': batch.get('frozen_boundaries'),
        'boundary_events': batch.get('boundary_events'),
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
    if not raw_blueprints:
        if errors:
            raise errors[0]
        raise TerminalState('stage2.blueprint 没有任何一章返回可接纳的响应')

    data, accounting = spec.compile_fn(
        {'blueprints': raw_blueprints},
        main_text,
        character_inventory=character_inventory,
    )
    system = requests[0][1]
    prompt_keys = {spec.sys_name: system}
    boundary_requests = batch.get('boundary_requests') or []
    if spec.boundary_sys_name and boundary_requests:
        prompt_keys[spec.boundary_sys_name] = boundary_requests[0][1]
    ordinals = [int(section.get('chapter_ordinal') or 0)
                for section in main_text]
    produced = sorted({
        int(blueprint.get('chapter_ordinal') or 0)
        for blueprint in raw_blueprints
    })
    purposes: dict[str, int | None] = {f'{ctx.node}:ch{o}': o for o in produced}
    if spec.topology == 'boundary_then_detail':
        purposes.update({f'{ctx.node}:boundaries:ch{o}': o for o in produced})
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
            '`stage2.blueprint --revalidate --actor <id> --reason <why>`'
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
        existing, _existing_raw = store.read_own(
            ChapterBlueprints, BLUEPRINT_REL, require_current=True)
        existing_matches = existing.model_dump(by_alias=True) == candidate
        # 比的是同一 typed payload。raw 还带 store 注入的 `_schema_version` 信封，
        # candidate 在写盘前没有那一列；拿两者直接哈希会让逐字段完全相同的正式稿
        # 永远判成不同。`require_current=True` 已先严格核验信封与载体版本。
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
) -> CheckReport:
    if chapters:
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
        )
    finalized = _finish_full_commit_tail(
        ctx, store=store, main_text=main_text,
        character_projection=character_projection)
    if finalized is not None:
        return finalized
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

        with stage2_global_runs.mutation_ownership(ctx):
            source_rel = (
                _GLOBAL_INVALID if store.exists(_GLOBAL_INVALID) else GLOBAL_REL)
            if not store.exists(source_rel):
                raise UpstreamMissing(
                    f'{GLOBAL_REL} 与 {_GLOBAL_INVALID} 都不在盘上——'
                    '没有可重新归一的全局底座')
            previous, raw = store.read_own(GlobalGrounding, source_rel)
            data = copy.deepcopy(raw)
            data.pop('_provenance', None)
            data, accounting = normalize_global(data, main_text)
            rewritten = ctx.rewrite_provenance(
                previous.provenance, by='--renormalize')
            provenance = GlobalProvenance.model_validate({
                **rewritten.model_dump(),
                **accounting,
                'gates_skipped': None,
            })
            artifact = GlobalGrounding.model_validate({
                **data, '_provenance': provenance,
            })
            candidate = artifact.model_dump(by_alias=True)
            outcomes = _judge_candidate(candidate, None, main_text)
            hits = _hard_hits(outcomes, ignore={'upstream_missing'})
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
    source_rel = _INVALID if store.exists(_INVALID) else BLUEPRINT_REL
    if not store.exists(source_rel):
        raise UpstreamMissing(
            f'{BLUEPRINT_REL} 与 {_INVALID} 都不在盘上——没有可重新归一的逐章蓝图')
    _projection, character_projection = \
        _require_blueprint_character_projection(store)
    previous, raw = store.read_own(ChapterBlueprints, source_rel)
    previous_provenance = previous.provenance
    phrases = _checkpoint_fact_atom_phrases(
        ctx,
        artifact=previous,
        raw=raw,
        main_text=main_text,
        character_projection=character_projection,
        failures=list(previous_provenance.fact_atom_failures),
    )
    data = copy.deepcopy(raw)
    data.pop('_provenance', None)
    data, accounting = normalize_blueprints(
        data,
        main_text,
        previous_fact_atom_failures=list(
            previous_provenance.fact_atom_failures),
        evidence_phrases=phrases,
    )
    rewritten = ctx.rewrite_provenance(
        previous_provenance, by='--renormalize')
    provenance = BlueprintProvenance.model_validate({
        **rewritten.model_dump(),
        **accounting,
        'gates_skipped': None,
    })
    artifact = ChapterBlueprints.model_validate({
        **data, '_provenance': provenance,
    })
    candidate = artifact.model_dump(by_alias=True)
    outcomes = _judge_candidate(None, candidate, main_text)
    if _blueprint_hard_hits(outcomes):
        _write_invalid(store, BLUEPRINT_REL, artifact)
        both = _Both(store)
        return _report(ctx, store, both, outcomes, judged_rel=_INVALID)
    _write_formal(store, BLUEPRINT_REL, artifact)
    both = _Both(store)
    return _report(ctx, store, both, both.judge(), judged_rel=BLUEPRINT_REL)


# ─────────────────────────────────────────────────────────────────────────────
# 四、`--apply-edit`：Stage2 typed facts 的 0 成本人工订正
# ─────────────────────────────────────────────────────────────────────────────

def _apply_secret_patches(data: dict[str, Any], edit: GlobalEdit,
                          ordinals: set[int]) -> list[str]:
    """把 typed 人工决定应用到内存候选，并在任何写盘前关闭式校验引用/章号。

    历史函数名为兼容测试与仓外调用保留；当前编辑面已覆盖全部会被 2b/2c HARD
    拦住的自由事实。修改 claim 必须同交 locator，归一随后仍走 ``normalize_global``。
    """
    from novel.core.evidence import iter_global_evidence_slots

    # 校验失败时连调用者手里的内存对象也不改：完整 replacement 若先落进 ``data``
    # 再发现悬空引用，虽然不会写盘，后续同轮代码仍可能误读半应用候选。
    candidate = copy.deepcopy(data)
    entities = candidate.get('entities') or {}
    canon = candidate.get('canon') or {}
    arc = candidate.get('arc') or {}
    characters = entities.get('characters') or {}
    secret_by_id = {str(row.get('secret_id') or ''): row
                    for row in (canon.get('secrets') or [])}
    rule_by_id = {str(row.get('rule_id') or ''): row
                  for row in (canon.get('world_rules') or [])}
    relation_by_id = {str(row.get('relation_id') or ''): row
                      for row in (entities.get('relations') or [])}
    tracks = arc.get('arc_tracks') or []

    unknown = {
        'secret': sorted(p.secret_id for p in edit.secrets
                         if p.secret_id not in secret_by_id),
        'world rule': sorted(p.rule_id for p in edit.world_rules
                             if p.rule_id not in rule_by_id),
        'relation': sorted(p.relation_id for p in edit.relations
                           if p.relation_id not in relation_by_id),
        'character': sorted(p.char_id for p in [
            *edit.characters, *edit.addresses, *edit.addresses_replace]
                            if p.char_id not in characters),
    }
    unknown = {kind: values for kind, values in unknown.items() if values}
    if unknown:
        existing = {
            'secret': sorted(secret_by_id),
            'world rule': sorted(rule_by_id),
            'relation': sorted(relation_by_id),
            'character': sorted(characters),
        }
        raise TerminalState(
            f'编辑文件指向盘上不存在的 subject：{unknown}；盘上现有：{existing}')
    bad_track = sorted(p.track_index for p in edit.arc_tracks
                       if p.track_index >= len(tracks))
    bad_address = sorted((p.char_id, p.address_index) for p in edit.addresses
                         if p.address_index >= len(
                             (characters.get(p.char_id) or {}).get('address') or []))
    if bad_track or bad_address:
        raise TerminalState(
            f'编辑文件指向不存在的下标：arc_tracks={bad_track}, address={bad_address}')

    def dump(value: Any) -> Any:
        if isinstance(value, StrictModel):
            return value.model_dump(by_alias=True)
        if isinstance(value, list):
            return [dump(item) for item in value]
        return copy.deepcopy(value)

    changed: list[str] = []

    def apply_fields(target: dict[str, Any], patch: StrictModel,
                     identity: str, *, skip: set[str]) -> None:
        for field in patch.model_fields_set - skip:
            target[field] = dump(getattr(patch, field))
            changed.append(f'{identity}.{field}')

    if edit.secrets_replace is not None:
        canon['secrets'] = [row.model_dump(by_alias=True) for row in edit.secrets_replace]
        changed.append('canon.secrets')
    else:
        for patch in edit.secrets:
            apply_fields(secret_by_id[patch.secret_id], patch, patch.secret_id,
                         skip={'secret_id'})

    if edit.world_rules_replace is not None:
        canon['world_rules'] = [row.model_dump(by_alias=True)
                                for row in edit.world_rules_replace]
        changed.append('canon.world_rules')
    else:
        for patch in edit.world_rules:
            apply_fields(rule_by_id[patch.rule_id], patch, patch.rule_id,
                         skip={'rule_id'})

    if edit.relations_replace is not None:
        entities['relations'] = [row.model_dump(by_alias=True)
                                 for row in edit.relations_replace]
        changed.append('entities.relations')
    else:
        for patch in edit.relations:
            apply_fields(relation_by_id[patch.relation_id], patch, patch.relation_id,
                         skip={'relation_id'})

    for patch in edit.characters:
        target = characters[patch.char_id]
        apply_fields(target, patch, patch.char_id, skip={'char_id'})
        if 'fate' in patch.model_fields_set and patch.fate is None:
            target['fate_evidence'] = None
    if edit.setting is not None:
        dumped = edit.setting.model_dump(by_alias=True, exclude_unset=True)
        canon.setdefault('setting', {}).update(dumped)
        changed.extend(f'canon.setting.{key}' for key in dumped)
    if edit.conflict_stages_replace is not None:
        replacement = [row.model_dump(by_alias=True)
                       for row in edit.conflict_stages_replace]
        measured = checks.measure_conflict_stage_coverage(replacement, ordinals)
        if measured.evidence:
            raise TerminalState(
                'conflict_stages_replace 没有让正文章恰好各出现一次：'
                f'缺章={list(measured.missing)}，越界={list(measured.extra)}，'
                f'重复={[(chapter, list(labels)) for chapter, labels in measured.duplicate]}；'
                f'盘上章集合={sorted(ordinals)}')
        arc['conflict_stages'] = replacement
        changed.append('arc.conflict_stages')
    if edit.arc_tracks_replace is not None:
        arc['arc_tracks'] = [row.model_dump(by_alias=True)
                             for row in edit.arc_tracks_replace]
        changed.append('arc.arc_tracks')
    else:
        for patch in edit.arc_tracks:
            apply_fields(tracks[patch.track_index], patch,
                         f'arc.arc_tracks.{patch.track_index}', skip={'track_index'})
    for replacement in edit.addresses_replace:
        characters[replacement.char_id]['address'] = [
            row.model_dump(by_alias=True) for row in replacement.address]
        changed.append(f'{replacement.char_id}.address')
    for patch in edit.addresses:
        characters[patch.char_id]['address'][patch.address_index]['context'] = patch.context
        changed.append(f'{patch.char_id}.address.{patch.address_index}.context')

    # 校验**应用后的完整候选**，这样“删了 secret 却留下 via_secret 引用”也会在写盘前出声。
    bad_refs: list[str] = []
    bad_chapters: list[str] = []

    def chapter(path: str, value: Any) -> None:
        if type(value) is not int or value not in ordinals:
            bad_chapters.append(f'{path}={value!r}')

    resulting_secrets = canon.get('secrets') or []
    secret_ids = {row.get('secret_id') for row in resulting_secrets
                  if isinstance(row, dict)}
    for relation in (entities.get('relations') or []):
        rid = relation.get('relation_id')
        for key in ('subject_id', 'object_id'):
            if relation.get(key) not in characters:
                bad_refs.append(f'{rid}.{key}={relation.get(key)!r}')
        start = relation.get('valid_from_chapter')
        chapter(f'{rid}.valid_from_chapter', start)
        through = relation.get('valid_through_chapter')
        if through is not None:
            chapter(f'{rid}.valid_through_chapter', through)
        if type(start) is int and type(through) is int and through < start:
            bad_refs.append(f'{rid}.window={start}>{through}')
        visibility = relation.get('visibility') or {}
        if visibility.get('kind') == 'public':
            chapter(f'{rid}.visibility.from_chapter', visibility.get('from_chapter'))
        elif visibility.get('kind') == 'via_secret':
            if visibility.get('secret_id') not in secret_ids:
                bad_refs.append(
                    f'{rid}.visibility.secret_id={visibility.get("secret_id")!r}')

    for secret in resulting_secrets:
        sid = secret.get('secret_id')
        setup = secret.get('setup') or {}
        for value in (setup.get('chapters') or []):
            chapter(f'{sid}.setup.chapters', value)
        chapter(f'{sid}.first_reveal_chapter', secret.get('first_reveal_chapter'))
        for event in (secret.get('knowledge_events') or []):
            chapter(f'{sid}.knowledge_events.known_from_chapter',
                    event.get('known_from_chapter'))
            subject = event.get('subject') or {}
            if (subject.get('kind') == 'character'
                    and subject.get('char_id') not in characters):
                bad_refs.append(
                    f'{sid}.knowledge_events.char_id={subject.get("char_id")!r}')
    for rule in (canon.get('world_rules') or []):
        for version in (rule.get('versions') or []):
            chapter(f'{rule.get("rule_id")}.versions.effective_from_chapter',
                    version.get('effective_from_chapter'))
    for cid, character in characters.items():
        if ((character.get('fate') is None)
                != (character.get('fate_evidence') is None)):
            bad_refs.append(f'{cid}.fate/fate_evidence pairing')
        for index, address in enumerate(character.get('address') or []):
            chapter(f'{cid}.address.{index}.from_chapter', address.get('from_chapter'))
            by = address.get('by')
            if (isinstance(by, str) and re.fullmatch(r'char_\d+', by)
                    and by not in characters):
                bad_refs.append(f'{cid}.address.{index}.by={by!r}')
    for path, owner, field in iter_global_evidence_slots(candidate):
        evidence = owner.get(field) or {}
        chapter(f'{path}.{field}.chapter_ordinal', evidence.get('chapter_ordinal'))

    if bad_refs or bad_chapters:
        raise TerminalState(
            f'编辑后的候选含无效引用/章号：refs={sorted(bad_refs)}, '
            f'chapters={sorted(bad_chapters)}；盘上章集合={sorted(ordinals)}')
    data.clear()
    data.update(candidate)
    return changed


def run_apply_edit(ctx: RunContext, edit_path: str = '') -> tuple[GlobalGrounding, CheckReport]:
    """`--apply-edit`：吃 typed 人工订正，走与生成稿**同一道**归一、判定与闸门。0 成本。

    【它补的是什么】既能逐格订正已有 secret/world-rule/枚举，也能完整替换 secret、
    world-rule、冲突阶段和单个人物的 address 集合，从而表达新增、删除、复合时间线拆分
    与全书冲突分段重画；所有字段仍由同一严格 schema 收口，来源账明确记录这是人工
    partial rewrite。

    四条纪律（与 stage4/6/7 的现役人工路同款）：
      · **同一归一落点**（`normalize_global`）——人改完的埋点章照样过 `_prune_setups`，
        人工稿与生成稿不许有两套归一；
      · **同一判定落点**（`_judge_candidate`）——没有免检写口；
        HARD ⇒ `.INVALID` **不覆盖正式全书底座**；
      · **轮次归档**——旧轮进 `_rounds/`，覆盖前有处可回；
      · **来源账不伪造**——`rewrite_provenance(prev, by='--apply-edit')`：
        继承 `prompts`/`model`/`adopted_models` 一个字不动，
        换成本次真读到的 `inputs`，
        标 `partial_rewrite`（这一份此后是混版的：未编辑字段出自当初那次付费产出，
        被改的 typed facts 是**现在**由人给的）。

    ⚠ **下游 stale 是由构造保证的，不额外做一件事**：这一份的字节变了 ⇒
    `stage2/chapter_blueprints.json` / stage4 及更下游记的 `inputs[].file` 全部失配；
    按字段投影算的 `consumed` 也跟着失配（其中 `R-6.3` 的逐章剧透闸门直接消费
    secrets；规则与 address 也各有下游消费投影）。

    ⚠ **无前稿（正式与 `.INVALID` 皆无）⇒ 显式终态**：这是修订路，不是生成路。
    """
    store = ArtifactStore(ctx)
    if ctx.node != 'stage2.global':
        raise TerminalState(
            f'{ctx.node} 没有 Stage2 全局人工订正入口——`--apply-edit` 挂在 `stage2.global` 上'
            f'（逐章蓝图的人工路是 `--chapters` 定向重生成）')

    rel = paths.edit_rel(ctx.novel_dir, edit_path, GLOBAL_EDIT)
    if not store.exists(rel):
        raise TerminalState(
            f'{rel} 不在盘上。⚠ `--apply-edit` 吃的是**人写的决策集**，'
            f'把它放到这本书的 `stage2/` 下（缺省文件名 `global_edit.json`）'
            f'——它的形状是契约的一部分（`GlobalEdit`）')
    edit = store.read(GlobalEdit, rel)

    main_text = _read_main_text(store)
    if main_text is None:
        raise UpstreamMissing(f'{MAIN_TEXT} 不在盘上——人工订正同样要重走归一与判据')
    ordinals = {int(section.get('chapter_ordinal') or 0) for section in main_text}

    from novel.llm import stage2_global_runs

    with stage2_global_runs.mutation_ownership(ctx):
        previous, raw, _source_rel = _global_source(
            store, replaced_edit_rel=rel)
        data = copy.deepcopy(raw)
        data.pop('_provenance', None)
        changed = _apply_secret_patches(data, edit, ordinals)
        data, accounting = normalize_global(data, main_text)

        rewritten = ctx.rewrite_provenance(
            previous.provenance, by='--apply-edit')
        provenance = GlobalProvenance.model_validate({
            **rewritten.model_dump(),
            **accounting,
            'gates_skipped': None,
        })
        try:
            artifact = GlobalGrounding.model_validate({
                **data, '_provenance': provenance})
        except ValidationError as exc:
            raise TerminalState(
                '人工 GlobalEdit 应用后的完整候选不符合当前 '
                f'GlobalGrounding v4：{exc}') from exc

        candidate = artifact.model_dump(by_alias=True)
        outcomes = _judge_candidate(candidate, None, main_text)
        hits = _hard_hits(outcomes, ignore={'upstream_missing'})
        if hits:
            path = _write_invalid(store, GLOBAL_REL, artifact)
            _note_evidence_locator_corrections(
                ctx, store, accounting.evidence_locator_corrections,
                rel=_GLOBAL_INVALID)
            raise GateBlocked(
                f'人工 Stage2 订正被落盘闸门拦下：{hits}（改动 {changed}）。'
                f'问题稿在 {path}，**正式全书底座没有被覆盖**；'
                f'改完编辑文件重跑 `--apply-edit`，'
                f'或直接改 `.INVALID` 后跑 '
                f'`{ctx.node} --revalidate --actor <id> --reason <why>`'
                f'（0 LLM、同轮重判并留不可变审计）',
                invalid_path=path)

        _write_formal(store, GLOBAL_REL, artifact)
        _note_evidence_locator_corrections(
            ctx, store, accounting.evidence_locator_corrections,
            rel=GLOBAL_REL)
        both = _Both(store)
        return artifact, _report(
            ctx, store, both, both.judge(),
            judged_rel=GLOBAL_REL, invalid_rel=_GLOBAL_INVALID)
