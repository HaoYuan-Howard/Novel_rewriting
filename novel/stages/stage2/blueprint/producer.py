#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Blueprint 生产臂：章请求、候选、compile 提交尾、renormalize。

由 scripts/split_stage2_extract.py 从历史 ``extract.py`` 按符号归属拆出。
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
from novel.stages.stage2.knowledge import grounding as grounding_io
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
from novel.stages.stage2.shared.character_refs import (
    blueprint_inventory_from_projection,
    materialize_blueprint_character_projection,
)
from novel.stages.stage2.knowledge.contract import GlobalEvidenceContractSpec
from novel.stages.stage2.shared.evidence_candidates import (
    GlobalEvidenceCandidateTable,
)
from novel.stages.stage2.knowledge.normalize import (
    EvidenceLocatorCorrection, GlobalNormalizationAccounting, normalize_global,
)
from novel.store import ArtifactStore

from novel.stages.stage2.shared.materialize import (  # noqa: F401
    BLUEPRINT_CHARACTER_REL, BLUEPRINT_REL, GLOBAL_EDIT, GLOBAL_EDIT_COMMIT,
    GLOBAL_EDIT_FAILURE, GLOBAL_EDIT_INPUT_COMMIT, GLOBAL_REL, GLOBAL_REVIEW_REL,
    MAIN_TEXT, _Both, _GLOBAL_INVALID, _INVALID,
    _accepted_adoptions, _accounting_of, _canonical_sha256, _hard_hits,
    _judge_candidate, _note_adopted, _note_restored_adoptions,
    _older_version_error, _read_characters, _read_main_text, _read_raw,
    _report, _retire_invalid_for_matching_formal, _write_formal, _write_invalid,
)

from novel.stages.stage2.knowledge.producer import (  # noqa: F401
    GlobalCandidateContext,
    GlobalCandidateFinding,
    _GlobalResponseSource,
    _active_global_spec,
    _note_evidence_locator_corrections,
    _require_direct_stage2_entry,
    assert_global_main_text_current,
    build_global_request,
)

BLUEPRINT_MAX_TOKENS = 256_000

class _BlueprintRequestSource(_GlobalResponseSource, Protocol):
    """blueprint 扇出真正消费的客户端窄面。

    这里只消费直连 ``LlmClient`` 已有的逐章 checkpoint / adoption 桥，不持有
    driver，也不另造第二套恢复状态。
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
class _BlueprintGuard:
    """global 重跑期间当前正式 blueprint 的冻结快照。"""

    state: str                         # absent | current | older:<found>:<current>
    raw_sha256: str | None = None
    raw: dict[str, Any] | None = field(default=None, repr=False)

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
    from novel.stages.stage2.blueprint.prompts import build_plot_user
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
    from novel.stages.stage2.blueprint.contract import active_contract
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
        from novel.stages.stage2.blueprint.repair import build_prompt as revision_prompt
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
        from novel.errors import AgentDeliveryUnavailable
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
    from novel.stages.stage2.blueprint.repair import run
    return run(ctx, client, **kwargs)

def request_blueprints(ctx: RunContext, client: _BlueprintRequestSource, *,
                       chapters: tuple[int, ...] = (),
                       checkpoints: bool = True) -> dict[str, Any]:
    """全书首次请求：各章空 entry_states 并行购买，Continuity 在 compile 期缝合。

    不支持跳章首次抽取；定向修订必须走现有蓝图的编辑/返修入口。
    正式 producer 始终写 checkpoint，checkpoint 记录实际下发请求身份。
    Blueprint 生成前的机械准入由统一 ``novel.orchestrator.mechanical_admission`` 在调度入口判定。
    """
    _require_direct_stage2_entry(ctx, client, node='stage2.blueprint')
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
        from novel.stages.stage2.blueprint.inputs import input_digests
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
    from novel.stages.stage2.blueprint.sequence import execute
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
    from novel.stages.stage2.blueprint.contract import contract_for

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
    """直连批次的唯一终态入口。"""
    evaluation = evaluate_blueprint_candidate(ctx, client, batch)
    return finalize_blueprint_candidate(ctx, client, batch, evaluation)

def run_blueprint(
    ctx: RunContext,
    client: LlmClient,
    *,
    chapters: tuple[int, ...] = (),
    review_token: str | None = None,
) -> CheckReport:
    """Stage2 Blueprint 现役唯一生产入口：LlmClient 逐章直连。"""
    _require_direct_stage2_entry(ctx, client, node='stage2.blueprint')
    from novel.stages.stage2.blueprint.repair import finish_commit
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
        from novel.stages.stage2.characters.rebind import prepare_if_changed

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
    from novel.stages.stage2.blueprint.recovery import renormalize
    return renormalize(ctx, store, main_text)

