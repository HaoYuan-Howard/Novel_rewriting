# -*- coding: utf-8 -*-
"""Stage4 design/plan 对通用 single-session agent run core 的业务适配。

``stage4.plan`` 仍是一个独立 single-session run；``stage4.design`` v2 则是
strategy + ch1..N 各一条 ``SingleSessionRunSpec``，单元通过后就落
append-only checkpoint，最后由本模块的 aggregate journal 冻结 N+1 份
adoption 与整书候选。通用 profile/prompt/schema/stdout/ledger/session-audit
闭环仍由 ``novel.llm.run_journal`` 唯一实现。

typed infeasible 只有 binding；bound bundle 只含 plan，context 由独立节点组装；同书任一
foreign ``prepared`` 都阻止另一 run/node 写正式路径。
"""
from __future__ import annotations

from novel import runtime_layout
import copy
import json
import os
import threading
from collections.abc import Callable, Iterable, Mapping, Sequence
from contextlib import contextmanager, nullcontext
from contextvars import ContextVar
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, TypeVar

from pydantic import BaseModel, ValidationError

from contracts.artifacts import (
    STAGE4_BLIND_CANDIDATE, STAGE4_EVIDENCE_BINDING,
    STAGE4_PLAN_CHAPTERS,
)
from contracts.invariants import STAGE4_DESIGN_MAX_CONCURRENCY
from contracts.schema import Usage, decode_artifact
from contracts.schema.api import DesignRepairScope
from contracts.schema.orchestration import AgentProfile, AgentRunEvent
from contracts.schema.stage4 import (
    BlindBookStrategyResponse, BlindCandidate, BlindChapterDesignResponse,
)
from novel import paths, prov
from novel.orchestrator.executor_capabilities import validate_profile
from novel.llm import session_audit as agent_session_audit
from novel.llm.model_identity import validate_checkpoint_model_identity
from novel.llm.session_binding import (
    SAME_RUN_RESUMABLE_AGENT_FAILURES, SESSION_PROTOCOL_VERSION,
    validate_agent_session_request_binding,
)
from novel.appendlog import process_append_lock
from novel.errors import ArtifactUnreadable, ProvenanceRefused
from novel.llm import run_journal as agent_runs, aggregate_runs
from novel.llm import ledger as ledger_module
from novel.llm.json_text import normalize_json_text
from novel.llm.ledger import CostLedger, combine_usage
from novel.llm.transport import ChatResponse
from novel.run_identity import check_run_id


M = TypeVar('M', bound=BaseModel)

RECOVERY_POLICIES = {
    'stage4.design': 'stage4-design-v1',
    'stage4.plan': 'stage4-plan-v1',
}

# design v2 不再是一个全书 session；策略和各章 purpose 各自恢复。
# 该 discriminator 由后续 producer 接入 SessionAgentClient 时一并登记，
# 本模块先用它冻结 run.json，避免新旧架构被当成同一恢复链。
DESIGN_UNIT_RECOVERY_POLICY = 'stage4-design-v2'
DESIGN_MAX_CONCURRENCY = STAGE4_DESIGN_MAX_CONCURRENCY
DESIGN_AGGREGATE_COMMIT_FILE = 'aggregate-committed.json'
DESIGN_REPAIR_AGGREGATE_COMMIT_FILE = 'repair-aggregate-committed.json'
DESIGN_HUMAN_EVOLUTION_COMMIT_FILE = 'human-evolution-committed.json'
DESIGN_HISTORICAL_ADOPTION_FILE = 'historical-checkpoint-adoption.json'

_DESIGN_AGGREGATE_VERSION = 2
_DESIGN_AGGREGATE_KIND = 'stage4-design-aggregate/v2'
_DESIGN_AGGREGATE_FIELDS = {
    'version', 'kind', 'state', 'prepared_at', 'committed_at',
    'run_id', 'sid', 'node', 'code', 'primary_artifact',
    'primary_payload_sha256', 'primary_payload_fingerprint',
    'primary_payload', 'finalization_sha256', 'finalization_payload',
    'touched_paths', 'baselines', 'attempts', 'adoptions',
    'landed_payload_sha256s', 'post_file_sha256s', 'commit_sha256',
}
_DESIGN_ADOPTION_FIELDS = {
    'unit_key', 'purpose', 'chapter', 'attempt_id',
    'run_sha256', 'checkpoint_revision', 'checkpoint_sha256',
    'checkpoint_file_sha256', 'provider_session_id', 'request_identity',
}
_DESIGN_AGGREGATE_FIELDS_V2 = _DESIGN_AGGREGATE_FIELDS | {
    'producer_parent_run_id', 'primary_file_text', 'primary_file_sha256', 'recovery',
}
_DESIGN_ADOPTION_FIELDS_V2 = _DESIGN_ADOPTION_FIELDS | {'source_run_id'}
_DESIGN_REPAIR_AGGREGATE_VERSION = 3
_DESIGN_REPAIR_AGGREGATE_KIND = 'stage4-design-targeted-repair/v3'
_DESIGN_REPAIR_AGGREGATE_FIELDS_V2 = {
    'version', 'kind', 'state', 'prepared_at', 'committed_at',
    'run_id', 'sid', 'node', 'code', 'parent_run_id',
    'parent_candidate_sha256', 'repair_decision_sha256', 'target_chapters',
    'primary_artifact', 'primary_payload_sha256',
    'primary_payload_fingerprint', 'primary_payload',
    'finalization_sha256', 'finalization_payload', 'touched_paths',
    'baselines', 'attempts', 'sources', 'landed_payload_sha256s',
    'post_file_sha256s', 'commit_sha256',
}
_DESIGN_REPAIR_SOURCE_FIELDS_V1 = {
    'unit_key', 'chapter', 'source_run_id', 'purpose', 'attempt_id',
    'source_kind', 'run_sha256', 'checkpoint_revision',
    'checkpoint_sha256', 'checkpoint_file_sha256',
    'response_payload_sha256', 'model', 'accepted_turn_index',
}
_DESIGN_REPAIR_SOURCE_FIELDS = _DESIGN_REPAIR_SOURCE_FIELDS_V1 | {
    'origin', 'source_ref', 'response_payload',
}
_DESIGN_HUMAN_EVOLUTION_VERSION = 1
_DESIGN_HUMAN_EVOLUTION_KIND = 'stage4-design-human-evolution/v1'
_DESIGN_HUMAN_EVOLUTION_FIELDS = {
    'version', 'kind', 'state', 'prepared_at', 'committed_at',
    'run_id', 'sid', 'node', 'code', 'parent_run_id',
    'parent_candidate_sha256', 'review_token', 'review_decision_sha256',
    'output_ordinal', 'chapter_subject_sha256', 'strategy_epoch_sha256',
    'chapter_strategy_sha256', 'constraint_set_sha256',
    'edit_request_sha256', 'human_edit_sha256',
    'primary_artifact', 'primary_payload_sha256',
    'primary_payload_fingerprint', 'primary_payload', 'sources',
    'touched_paths', 'baselines', 'landed_payload_sha256s',
    'post_file_sha256s', 'commit_sha256',
}
_DESIGN_REPAIR_AGGREGATE_FIELDS = (
    _DESIGN_REPAIR_AGGREGATE_FIELDS_V2
    | {'producer_parent_run_id', 'repair_scope', 'recovery'})
_DESIGN_REPAIR_RECOVERY_FIELDS = {
    'stage4_repair_recovery_id', 'stage4_repair_recovery_attempt_id',
    'target_run_id', 'target_parent_run_id',
    'source_journal_sha256', 'source_commit_sha256',
    'target_file_sha256',
}
_DESIGN_TARGETED_REPAIR_FINALIZATION_FIELDS = {
    'delivery_kind', 'architecture', 'input_sha256', 'truth_sha256',
    'parent_candidate_file_sha256', 'parent_candidate_payload_sha256',
    'parent_aggregate_subject_sha256', 'parent_run_id',
    'repair_decision_sha256', 'target_chapters', 'target_subjects',
    'source_lineage_sha256', 'candidate_sha256',
}
_DESIGN_REPLAN_FINALIZATION_FIELDS = {
    'delivery_kind', 'architecture', 'input_sha256',
    'strategy_input_sha256', 'parent_run_id',
    'parent_candidate_file_sha256', 'parent_candidate_payload_sha256',
    'repair_decision_sha256', 'feedback_sha256', 'target_chapters',
    'candidate_sha256',
}
_DESIGN_HISTORICAL_ADOPTION_VERSION = 1
_DESIGN_HISTORICAL_ADOPTION_KIND = 'stage4-design-historical-adoption/v1'
_DESIGN_HISTORICAL_ADOPTION_FIELDS = {
    'version', 'kind', 'state', 'target_run_id', 'sid', 'node', 'code',
    'repair_input_sha256', 'request_set_sha256', 'source_run_id',
    'source_chapters', 'sources', 'created_at', 'manifest_sha256',
}
_DESIGN_HISTORICAL_SOURCE_FIELDS = {
    'chapter', 'source_code', 'source_run_identity_sha256',
    'source_run_file_sha256', 'source_checkpoint_revision',
    'source_checkpoint_sha256', 'source_checkpoint_file_sha256',
    'source_attempt_id', 'source_request_fingerprint',
    'source_response_sha256', 'source_model',
    'target_run_identity_sha256', 'target_run_file_sha256',
}
_LEDGER_IDENTITY_FIELDS = {
    'attempt_id', 'request_fingerprint', 'model', 'request_model',
    'reasoning_effort', 'thinking', 'json_mode', 'max_tokens',
    'response_sha256',
}

_PLAN_INVALID = paths.invalid_of(STAGE4_PLAN_CHAPTERS)
_OWNERSHIP_REGISTRY_LOCK = threading.Lock()
_OWNERSHIP_THREAD_LOCKS: dict[str, threading.RLock] = {}
_OWNERSHIP_LOCAL = threading.local()
_DESIGN_AGGREGATE_LOCK = threading.RLock()


@dataclass(frozen=True)
class DesignUnitRequest:
    """一份 strategy/单章请求的完整恢复身份。

    聚合提交不从当前默认配置反推 prompt/schema；调用方把当前
    确定性构建出的请求传入，本模块逐项对照 immutable run/checkpoint。
    """

    chapter: int | None
    input_sha256: str
    system: str
    user: str
    response_model: type[BaseModel]
    max_tokens: int


@dataclass(frozen=True)
class DesignUnitSource:
    """已提交 aggregate 所指的一份不可变 unit checkpoint。"""

    chapter: int | None
    source_run_id: str
    purpose: str
    source_kind: str
    response_payload: dict[str, Any]
    response_payload_sha256: str
    origin: str = 'agent'
    source_ref: str | None = None
    attempt_id: str | None = None
    run_sha256: str | None = None
    checkpoint_revision: int | None = None
    checkpoint_sha256: str | None = None
    checkpoint_file_sha256: str | None = None
    model: str | None = None
    accepted_turn_index: int | None = None


@dataclass(frozen=True)
class DesignHistoricalAdoption:
    """当前 targeted repair 采用的一组只读历史章 checkpoint。"""

    manifest_file_sha256: str
    source_run_id: str
    restored: tuple[tuple[int, agent_runs.RestoredHistoricalRun[Any]], ...]

    def by_chapter(self) -> dict[int, agent_runs.RestoredHistoricalRun[Any]]:
        return dict(self.restored)


@dataclass(frozen=True)
class DesignRepairCapability:
    """当前正式 A 能否走 split-checkpoint targeted repair 的只读结论。"""

    repairable: bool
    reason_code: str
    parent_run_id: str | None
    chapter_repair_rounds: tuple[tuple[int, int], ...]


@dataclass(frozen=True)
class DesignRepairAggregateRecovery:
    """一份 foreign prepared repair/replan aggregate 的精确恢复坐标。"""

    source_run_id: str
    producer_parent_run_id: str
    journal_path: Path
    journal_sha256: str
    commit_sha256: str
    target_state: str
    candidate_file_sha256: str
    expected_file_sha256: str


@dataclass(frozen=True)
class _ValidatedRepairSource:
    """一份 source checkpoint 在 ledger/session 闭集中的定位信息。"""

    purpose: str
    chapter: int | None
    identities: tuple[dict[str, Any], ...]
    session_run_id: str
    subject_sha256: str
    code: str
    profile: dict[str, Any]


def recovery_policy(node: str) -> str:
    """返回 Stage4 节点唯一恢复策略；节点名不是自由输入。"""
    try:
        return RECOVERY_POLICIES[node]
    except KeyError:
        raise ProvenanceRefused(
            f'Stage4 agent node 必须属于 {tuple(RECOVERY_POLICIES)}，实得 {node!r}') from None


def design_unit_key(chapter: int | None) -> str:
    """strategy/单章的稳定聚合键，与路径片段完全一致。"""
    return paths.stage4_design_unit_dirname(chapter)


def design_unit_purpose(chapter: int | None) -> str:
    """每个 design unit 的 ledger/session purpose；不与旧全书 purpose 混用。"""
    return f'stage4.design:{design_unit_key(chapter)}'


def design_unit_run_spec(chapter: int | None) -> agent_runs.SingleSessionRunSpec:
    """返回 strategy/单章独立的通用 single-session run 声明。"""
    key = design_unit_key(chapter)
    purpose = design_unit_purpose(chapter)
    return agent_runs.SingleSessionRunSpec(
        label=f'Stage4 design unit {key}',
        node='stage4.design',
        session_owner=purpose,
        purpose=purpose,
        chapter=chapter,
        phase='strategy' if chapter is None else 'chapter',
        recovery_policy=DESIGN_UNIT_RECOVERY_POLICY,
        session_isolation='per-purpose',
        run_path=lambda ctx: paths.stage4_design_unit_run_path(
            ctx.novel_dir, ctx.run_id, chapter),
        checkpoint_dir=lambda ctx: paths.stage4_design_unit_checkpoint_dir(
            ctx.novel_dir, ctx.run_id, chapter),
        incompatible_session_nodes=('stage4.plan',),
    )


#: Stage4B 章级单元（批次 4）复用 plan 节点的恢复策略；章与章之间
#: per-purpose 隔离，任何两章不共享 provider session。
PLAN_CHAPTER_RECOVERY_POLICY = RECOVERY_POLICIES['stage4.plan']
PLAN_CHAPTER_REPLACEMENT_PROTOCOL = 'stage4-plan-chapter-replacement/v1'
PLAN_DELIVERY_RECOVERY_FILE = 'delivery_recovery.json'
PLAN_HISTORICAL_ADOPTION_FILE = 'historical-checkpoint-adoption.json'
PLAN_AGGREGATE_COMMIT_FILE = 'aggregate-committed.json'

_PLAN_DELIVERY_RECOVERY_VERSION = 1
_PLAN_HISTORICAL_ADOPTION_VERSION = 2
_PLAN_HISTORICAL_ADOPTION_KIND = 'stage4-plan-historical-adoption/v2'
_PLAN_HISTORICAL_ADOPTION_FIELDS = {
    'version', 'kind', 'state', 'target_run_id', 'sid', 'node', 'code',
    'candidate_subject_sha256', 'request_set_sha256',
    'source_chapters', 'sources', 'created_at', 'manifest_sha256',
}
_PLAN_HISTORICAL_SOURCE_FIELDS = {
    'chapter', 'source_run_id', 'source_code', 'source_run_identity_sha256',
    'source_run_file_sha256', 'source_checkpoint_revision',
    'source_checkpoint_sha256', 'source_attempt_id',
    'source_request_fingerprint', 'source_response_sha256', 'source_model',
}
_PLAN_DELIVERY_RECOVERY_FIELDS = {
    'version', 'state', 'run_id', 'sid', 'node', 'code',
    'candidate_subject_sha256', 'request_set_sha256', 'total_chapters',
    'completed_chapters', 'pending_chapters', 'failures',
    'deferred_chapters', 'historical_chapters',
    'historical_adoption_file_sha256', 'run_files', 'updated_at',
}
_PLAN_DELIVERY_RUN_FIELDS = {
    'chapter', 'run_identity_sha256', 'run_file_sha256',
}


@dataclass(frozen=True)
class PlanChapterRequest:
    """一份章级 B 请求的完整恢复身份（与 DesignUnitRequest 同构）。"""

    chapter: int
    input_sha256: str
    system: str
    user: str
    response_model: type[BaseModel]
    max_tokens: int


@dataclass(frozen=True)
class PlanHistoricalAdoption:
    """当前 target run 采用的一组只读历史章 checkpoint。"""

    manifest_file_sha256: str | None
    restored: tuple[tuple[int, agent_runs.RestoredHistoricalRun[Any]], ...]

    def by_chapter(self) -> dict[int, agent_runs.RestoredHistoricalRun[Any]]:
        return dict(self.restored)


def plan_chapter_purpose(chapter: int) -> str:
    """章级 B session 的 ledger/session purpose。"""
    return f'stage4.plan:{paths.stage4_plan_chapter_dirname(chapter)}'


def plan_chapter_run_spec(chapter: int) -> agent_runs.SingleSessionRunSpec:
    """章级 B session 的通用 single-session run 声明。"""
    return agent_runs.SingleSessionRunSpec(
        label=f'Stage4 plan chapter {paths.stage4_plan_chapter_dirname(chapter)}',
        node='stage4.plan',
        session_owner=plan_chapter_purpose(chapter),
        purpose=plan_chapter_purpose(chapter),
        chapter=chapter,
        phase='chapter',
        recovery_policy=PLAN_CHAPTER_RECOVERY_POLICY,
        session_isolation='per-purpose',
        run_path=lambda ctx: paths.stage4_plan_chapter_run_path(
            ctx.novel_dir, ctx.run_id, chapter),
        checkpoint_dir=lambda ctx: paths.stage4_plan_chapter_checkpoint_dir(
            ctx.novel_dir, ctx.run_id, chapter),
        incompatible_session_nodes=('stage4.design',),
    )


def ensure_plan_chapter_run(ctx: Any, request: Any) -> dict[str, Any]:
    """创建或复验一份章级 B immutable run。"""
    return agent_runs.ensure_run(
        ctx, plan_chapter_run_spec(request.chapter),
        input_sha256=request.input_sha256,
        system=request.system, user=request.user,
        response_model=request.response_model,
        max_tokens=request.max_tokens)


def plan_chapter_replacement_identity(
        ctx: Any, request: Any, *, candidate_subject_sha256: str,
) -> dict[str, Any]:
    """章级 0-wire replacement 的完整不可变身份。

    身份直接复用已校验的 run.json request/policy，而不是由恢复方重拼 prompt
    指纹；另显式绑定 A 候选主体。run/request/code/subject 任一漂移都会换链。
    """
    run = ensure_plan_chapter_run(ctx, request)
    if not agent_runs.is_sha256(candidate_subject_sha256):
        raise ProvenanceRefused('Stage4B unit replacement 缺候选主体 SHA-256')
    return {
        'protocol': PLAN_CHAPTER_REPLACEMENT_PROTOCOL,
        'run_id': run['run_id'], 'sid': run['sid'], 'node': run['node'],
        'code': run['code'], 'chapter': run['chapter'],
        'purpose': run['purpose'], 'input_sha256': run['input_sha256'],
        'candidate_subject_sha256': candidate_subject_sha256,
        'run_identity_sha256': run['identity_sha256'],
        'request_policy_sha256': run['request_policy_sha256'],
        'request': copy.deepcopy(run['request']),
    }


def plan_chapter_replacement_chain(
        ctx: Any, request: Any, *, candidate_subject_sha256: str,
) -> str:
    """同一 unit replacement 在 response-repair 中的唯一 chain id。"""
    return agent_runs.canonical_sha256(plan_chapter_replacement_identity(
        ctx, request, candidate_subject_sha256=candidate_subject_sha256))


def write_plan_chapter_checkpoint(
        ctx: Any, request: Any, *,
        response_payload: Mapping[str, Any], acceptance: Mapping[str, Any],
        state_payload: Mapping[str, Any], revision_key: str = 'terminal',
) -> Path:
    """一章歧义解析通过闭合校验后立即追加检查点。"""
    return agent_runs.write_checkpoint(
        ctx, plan_chapter_run_spec(request.chapter),
        input_sha256=request.input_sha256,
        system=request.system, user=request.user,
        response_model=request.response_model,
        max_tokens=request.max_tokens,
        response_payload=response_payload,
        acceptance=acceptance,
        revision_key=revision_key,
        state_payload=state_payload)


def restore_plan_chapter_checkpoint(
        ctx: Any, request: Any, *,
        validator: Callable[[BaseModel, ChatResponse], str | None] | None = None,
) -> agent_runs.RestoredCheckpoint[BaseModel] | None:
    """只恢复该章 latest accepted revision；其他章失败不影响它。"""
    return agent_runs.restore_checkpoint(
        ctx, plan_chapter_run_spec(request.chapter),
        input_sha256=request.input_sha256,
        system=request.system, user=request.user,
        response_model=request.response_model,
        max_tokens=request.max_tokens,
        validator=validator)


def _normalized_plan_requests(
        requests: Sequence[PlanChapterRequest],
) -> tuple[PlanChapterRequest, ...]:
    if not isinstance(requests, Sequence) or isinstance(requests, (str, bytes)):
        raise ProvenanceRefused('Stage4B 章请求必须是非空序列')
    by_chapter: dict[int, PlanChapterRequest] = {}
    for request in requests:
        if not isinstance(request, PlanChapterRequest):
            raise ProvenanceRefused(
                'Stage4B 章请求每项必须是 PlanChapterRequest')
        chapter = request.chapter
        if chapter in by_chapter:
            raise ProvenanceRefused(f'Stage4B 章请求重复 ch{chapter}')
        by_chapter[chapter] = request
    if not by_chapter:
        raise ProvenanceRefused('Stage4B 章请求不能为空')
    return tuple(by_chapter[chapter] for chapter in sorted(by_chapter))


def _plan_request_row(request: PlanChapterRequest) -> dict[str, Any]:
    return {
        'chapter': request.chapter,
        'input_sha256': request.input_sha256,
        'system_sha256': agent_runs.canonical_sha256(
            request.system.encode('utf-8')),
        'user_sha256': agent_runs.canonical_sha256(
            request.user.encode('utf-8')),
        'response_model': request.response_model.__name__,
        'response_schema_sha256': agent_runs.canonical_sha256(
            agent_runs.agent_json_schema(request.response_model)),
        'max_tokens': request.max_tokens,
    }


def _plan_request_set_sha256(
        requests: Sequence[PlanChapterRequest],
) -> str:
    return agent_runs.canonical_sha256([
        _plan_request_row(request)
        for request in _normalized_plan_requests(requests)
    ])


def _plan_root(ctx: Any, run_id: str | None = None) -> Path:
    return paths.stage4_run_path(
        ctx.novel_dir, check_run_id(run_id or ctx.run_id), 'stage4.plan')


def _plan_historical_adoption_path(ctx: Any) -> Path:
    return _plan_root(ctx) / PLAN_HISTORICAL_ADOPTION_FILE


def _plan_delivery_recovery_path(ctx: Any) -> Path:
    return _plan_root(ctx) / PLAN_DELIVERY_RECOVERY_FILE


def _plan_history_hash(payload: Mapping[str, Any]) -> str:
    return agent_runs.canonical_sha256({
        key: value for key, value in payload.items()
        if key not in ('created_at', 'manifest_sha256')
    })


def _historical_source_row(
        chapter: int, historical: agent_runs.RestoredHistoricalRun[Any],
) -> dict[str, Any]:
    acceptance = historical.checkpoint.acceptance
    return {
        'chapter': chapter,
        'source_run_id': historical.context.run_id,
        'source_code': historical.context.code,
        'source_run_identity_sha256':
            historical.run_record['identity_sha256'],
        'source_run_file_sha256': historical.run_sha256,
        'source_checkpoint_revision': historical.checkpoint.revision,
        'source_checkpoint_sha256': historical.checkpoint.checkpoint_sha256,
        'source_attempt_id': acceptance['attempt_id'],
        'source_request_fingerprint': acceptance['request_fingerprint'],
        'source_response_sha256': acceptance['ledger_response_sha256'],
        'source_model': acceptance['model'],
    }


def _restore_plan_historical_chapter(
        ctx: Any, *, source_run_id: str, request: PlanChapterRequest,
        validator: Callable[[BaseModel, ChatResponse], str | None] | None,
) -> agent_runs.RestoredHistoricalRun[Any]:
    historical = agent_runs.restore_historical_run(
        ctx, plan_chapter_run_spec(request.chapter),
        source_run_id=source_run_id,
        input_sha256=request.input_sha256,
        system=request.system,
        user=request.user,
        response_model=request.response_model,
        max_tokens=request.max_tokens,
        validator=validator,
    )
    state = historical.checkpoint.state_payload
    if (set(state) != {
            'chapter_ordinal', 'aggregate_revision', 'ambiguity_ids'}
            or state.get('chapter_ordinal') != request.chapter
            or not isinstance(state.get('aggregate_revision'), int)
            or isinstance(state.get('aggregate_revision'), bool)
            or state['aggregate_revision'] < 0
            or not isinstance(state.get('ambiguity_ids'), list)
            or any(not isinstance(value, str) or not value
                   for value in state['ambiguity_ids'])):
        raise ProvenanceRefused(
            f'Stage4B ch{request.chapter} 历史 checkpoint 不是普通已通过章单元')
    return historical


def _plan_source_run_ids(ctx: Any) -> tuple[str, ...]:
    found: set[str] = set()
    for root in runtime_layout.stage_run_roots(ctx.novel_dir, 'stage4'):
        if not root.is_dir():
            continue
        try:
            children = tuple(root.iterdir())
        except OSError as exc:
            raise ArtifactUnreadable(
                f'Stage4 历史 run 根不可读 {root}：{exc}') from exc
        for child in children:
            if not child.is_dir():
                continue
            try:
                source = check_run_id(child.name)
            except ValueError:
                continue
            if source != check_run_id(ctx.run_id):
                found.add(source)
    return tuple(sorted(found))


def _source_has_plan_checkpoint(
        ctx: Any, source_run_id: str, chapter: int,
) -> bool:
    source = check_run_id(source_run_id)
    dirname = paths.stage4_plan_chapter_dirname(chapter)
    for root in runtime_layout.stage_run_roots(ctx.novel_dir, 'stage4'):
        checkpoint_dir = root / source / 'plan' / 'chapters' / dirname / 'checkpoints'
        if checkpoint_dir.is_dir() and any(
                child.is_file() and child.suffix == '.json'
                for child in checkpoint_dir.iterdir()):
            return True
    return False


def plan_chapter_checkpoint_exists(
        ctx: Any, chapter: int, *, run_id: str | None = None,
) -> bool:
    """只判断 revision 是否存在；真正采用仍必须走完整 restore 校验。"""
    return _source_has_plan_checkpoint(
        ctx, check_run_id(run_id or ctx.run_id), chapter)


def plan_historical_source_lineage(
        chapter: int, historical: agent_runs.RestoredHistoricalRun[Any],
) -> dict[str, Any]:
    """投影进正式 provenance 的 source-stable 历史采用身份。"""
    return {
        key: value for key, value in _historical_source_row(
            chapter, historical).items()
        if key != 'chapter'
    }


def _read_plan_historical_adoption(
        ctx: Any, *, requests: Sequence[PlanChapterRequest],
        candidate_subject_sha256: str,
        eligible_chapters: Iterable[int],
        validators: Mapping[
            int, Callable[[BaseModel, ChatResponse], str | None] | None],
) -> PlanHistoricalAdoption | None:
    target = _plan_historical_adoption_path(ctx)
    if not target.is_file():
        return None
    record = _read_object(target, label='Stage4B historical adoption')
    if (set(record) != _PLAN_HISTORICAL_ADOPTION_FIELDS
            or record.get('version') != _PLAN_HISTORICAL_ADOPTION_VERSION
            or record.get('kind') != _PLAN_HISTORICAL_ADOPTION_KIND
            or record.get('state') != 'selected'
            or record.get('manifest_sha256') != _plan_history_hash(record)):
        raise ArtifactUnreadable(
            f'{target} 不是闭合的 Stage4B historical adoption manifest')
    normalized = _normalized_plan_requests(requests)
    request_by = {request.chapter: request for request in normalized}
    eligible = sorted({int(chapter) for chapter in eligible_chapters})
    expected = {
        'target_run_id': check_run_id(ctx.run_id),
        'sid': ctx.sid,
        'node': 'stage4.plan',
        'code': ctx.code,
        'candidate_subject_sha256': candidate_subject_sha256,
        'request_set_sha256': _plan_request_set_sha256(normalized),
    }
    if ({key: record.get(key) for key in expected} != expected
            or not isinstance(record.get('created_at'), str)
            or not record['created_at']):
        raise ProvenanceRefused(
            f'{target} 与当前 target run/code/subject/request set 漂移')
    chapters = record.get('source_chapters')
    rows = record.get('sources')
    if (not isinstance(chapters, list) or chapters != sorted(chapters)
            or not chapters or len(chapters) != len(set(chapters))
            or any(chapter not in request_by for chapter in chapters)
            or not isinstance(rows, list) or len(rows) != len(chapters)):
        raise ProvenanceRefused(
            f'{target} 的 source run/chapters 已不适用于当前恢复面')
    restored: list[tuple[int, agent_runs.RestoredHistoricalRun[Any]]] = []
    for chapter, row in zip(chapters, rows, strict=True):
        if (not isinstance(row, dict)
                or set(row) != _PLAN_HISTORICAL_SOURCE_FIELDS
                or row.get('chapter') != chapter
                or not isinstance(row.get('source_run_id'), str)
                or check_run_id(row['source_run_id']) == check_run_id(ctx.run_id)):
            raise ArtifactUnreadable(f'{target} 的 source row 形状漂移')
        historical = _restore_plan_historical_chapter(
            ctx, source_run_id=row['source_run_id'],
            request=request_by[chapter], validator=validators.get(chapter))
        if row != _historical_source_row(chapter, historical):
            raise ProvenanceRefused(
                f'{target} 的 ch{chapter} source run/checkpoint/ledger 漂移')
        # target run 后续可能已为某个历史章生成更新 checkpoint；manifest
        # 仍保持 immutable 并完整复验，但该章从此由 target 事实接管，不能
        # 因旧选择文件存在而覆盖新 checkpoint。
        if chapter in eligible:
            restored.append((chapter, historical))
    return PlanHistoricalAdoption(
        manifest_file_sha256=agent_runs.canonical_sha256(target.read_bytes()),
        restored=tuple(restored),
    )


def _historical_request_matches(
        ctx: Any, request: PlanChapterRequest | DesignUnitRequest, *,
        source_run_id: str, node: str,
) -> bool:
    """Stage4 跨代码采用政策：exact request/profile + 当前业务复验。

    本地依赖、prompt、schema 与已物化配置任一变化属于可解释的不适用；
    来源 envelope/checkpoint/ledger 损坏必须冒泡，禁止转成新付费许可。
    """
    specs = {'stage4.design': design_unit_run_spec, 'stage4.plan': plan_chapter_run_spec}
    if node not in specs:
        raise ProvenanceRefused('Stage4 历史采用政策未登记该节点')
    match = agent_runs.match_historical_request(
        ctx, specs[node](request.chapter), source_run_id=source_run_id,
        input_sha256=request.input_sha256, system=request.system, user=request.user,
        response_model=request.response_model, max_tokens=request.max_tokens,
        use_source_profile=getattr(ctx, 'paid', None) is False)
    if not match.matches:
        ctx.observe('stage4_historical_request_inapplicable',
                    source_run_id=source_run_id, chapter=request.chapter,
                    changed_fields=list(match.changed_fields))
    return match.matches


def restore_or_select_plan_historical_adoption(
        ctx: Any, *, requests: Sequence[PlanChapterRequest],
        candidate_subject_sha256: str,
        eligible_chapters: Iterable[int],
        validators: Mapping[
            int, Callable[[BaseModel, ChatResponse], str | None] | None],
        persist: bool = True,
) -> PlanHistoricalAdoption | None:
    """逐章选择有效来源，允许多来源与全部采用；冲突保持关闭。

    相同业务回答的多份证明可按稳定摘要确定来源；不同回答不能按时间或
    覆盖量猜选，也不能退回新 wire 掩盖冲突。首次清单落盘后保持不可变。
    """
    normalized = _normalized_plan_requests(requests)
    if not agent_runs.is_sha256(candidate_subject_sha256):
        raise ProvenanceRefused('Stage4B historical adoption 缺候选主体 SHA-256')
    request_by = {request.chapter: request for request in normalized}
    eligible = sorted({int(chapter) for chapter in eligible_chapters})
    if any(chapter not in request_by for chapter in eligible):
        raise ProvenanceRefused('Stage4B historical adoption eligible 章越界')
    existing = _read_plan_historical_adoption(
        ctx, requests=normalized,
        candidate_subject_sha256=candidate_subject_sha256,
        eligible_chapters=eligible, validators=validators)
    if existing is not None:
        return existing

    candidates: dict[int, list[agent_runs.RestoredHistoricalRun[Any]]] = {}
    for source_run_id in _plan_source_run_ids(ctx):
        for chapter in eligible:
            if not _source_has_plan_checkpoint(ctx, source_run_id, chapter):
                continue
            request = request_by[chapter]
            if not _historical_request_matches(
                    ctx, request, source_run_id=source_run_id, node='stage4.plan'):
                continue
            historical = _restore_plan_historical_chapter(
                ctx, source_run_id=source_run_id, request=request,
                validator=validators.get(chapter))
            candidates.setdefault(chapter, []).append(historical)
    if not candidates:
        return None
    chosen = []
    for chapter, options in sorted(candidates.items()):
        response_hashes = {agent_runs.canonical_sha256(
            row.checkpoint.value.model_dump(by_alias=True, mode='json')) for row in options}
        if len(response_hashes) != 1:
            raise ProvenanceRefused(
                f'Stage4B ch{chapter} 相同请求有不同历史回答；需要明确裁决来源，禁止新增调用绕过')
        chosen.append((chapter, min(options, key=lambda row: (
            row.checkpoint.checkpoint_sha256, row.run_sha256, row.context.run_id))))
    restored = tuple(chosen)
    if not persist:
        return PlanHistoricalAdoption(manifest_file_sha256=None, restored=restored)
    source_rows = [
        _historical_source_row(chapter, historical)
        for chapter, historical in restored]
    payload: dict[str, Any] = {
        'version': _PLAN_HISTORICAL_ADOPTION_VERSION,
        'kind': _PLAN_HISTORICAL_ADOPTION_KIND,
        'state': 'selected',
        'target_run_id': check_run_id(ctx.run_id),
        'sid': ctx.sid,
        'node': 'stage4.plan',
        'code': ctx.code,
        'candidate_subject_sha256': candidate_subject_sha256,
        'request_set_sha256': _plan_request_set_sha256(normalized),
        'source_chapters': [chapter for chapter, _row in restored],
        'sources': source_rows,
        'created_at': _now(),
    }
    payload['manifest_sha256'] = _plan_history_hash(payload)
    target = _plan_historical_adoption_path(ctx)
    with _DESIGN_AGGREGATE_LOCK, process_append_lock(target):
        if target.is_file():
            return _read_plan_historical_adoption(
                ctx, requests=normalized,
                candidate_subject_sha256=candidate_subject_sha256,
                eligible_chapters=eligible, validators=validators)
        _atomic_json(target, payload)
    ctx.observe(
        'stage4_plan_historical_checkpoint_adopted',
        source_run_ids=sorted({row.context.run_id for _chapter, row in restored}),
        chapters=[chapter for chapter, _row in restored])
    return PlanHistoricalAdoption(
        manifest_file_sha256=agent_runs.canonical_sha256(target.read_bytes()),
        restored=restored,
    )


def require_plan_historical_adoption_file(
        ctx: Any, expected_file_sha256: str | None,
) -> None:
    """在 provisional/final 两次铸 provenance 前复核来源选择未漂移。"""
    target = _plan_historical_adoption_path(ctx)
    actual = (
        agent_runs.canonical_sha256(target.read_bytes())
        if target.is_file() else None)
    if actual != expected_file_sha256:
        raise ProvenanceRefused(
            'Stage4B historical adoption manifest 在运行期间出现、消失或漂移')


def clear_plan_delivery_recovery(ctx: Any) -> None:
    """清掉当前 Stage4B run 的瞬态等待标记；账本/checkpoint 不动。"""
    target = _plan_delivery_recovery_path(ctx)
    with _DESIGN_AGGREGATE_LOCK, process_append_lock(target):
        agent_runs._remove_sidecar(target)


def write_plan_delivery_recovery(
        ctx: Any, *, candidate_subject_sha256: str,
        requests: Sequence[PlanChapterRequest],
        completed_chapters: Iterable[int],
        failures: Sequence[Mapping[str, Any]],
        deferred_chapters: Iterable[int],
        historical_chapters: Iterable[int],
        historical_adoption_file_sha256: str | None,
) -> dict[str, Any]:
    """冻结 Stage4B 熔断后的同 run 恢复凭据，不保存异常正文。"""
    normalized = _normalized_plan_requests(requests)
    requested = [request.chapter for request in normalized]
    request_by = {request.chapter: request for request in normalized}
    completed = sorted({int(chapter) for chapter in completed_chapters})
    deferred = sorted({int(chapter) for chapter in deferred_chapters})
    historical = sorted({int(chapter) for chapter in historical_chapters})
    if (not agent_runs.is_sha256(candidate_subject_sha256)
            or any(chapter not in request_by for chapter in completed + deferred)
            or any(chapter not in completed for chapter in historical)):
        raise ProvenanceRefused(
            'Stage4B delivery recovery 的主体或章节集合越界')
    normalized_failures: list[dict[str, Any]] = []
    for row in failures:
        chapter = row.get('chapter')
        kind = row.get('error_kind')
        status = row.get('status')
        attempts = row.get('attempts')
        if (not isinstance(chapter, int) or isinstance(chapter, bool)
                or chapter not in request_by or chapter in completed
                or chapter in deferred
                or kind not in SAME_RUN_RESUMABLE_AGENT_FAILURES
                or not isinstance(attempts, int) or isinstance(attempts, bool)
                or attempts < 1
                or (status is not None and (
                    not isinstance(status, int) or isinstance(status, bool)
                    or not 100 <= status <= 599))):
            raise ProvenanceRefused(
                f'Stage4B delivery recovery failure 身份不合法：{row!r}')
        normalized_failures.append({
            'chapter': chapter, 'error_kind': kind,
            'status': status, 'attempts': attempts,
        })
    normalized_failures.sort(key=lambda row: row['chapter'])
    failed = [row['chapter'] for row in normalized_failures]
    pending = sorted(failed + deferred)
    if (not failed or len(pending) != len(set(pending))
            or sorted(completed + pending) != sorted(requested)):
        raise ProvenanceRefused(
            'Stage4B delivery recovery 的 completed/pending 未精确覆盖请求章')

    history_target = _plan_historical_adoption_path(ctx)
    actual_history_sha = (
        agent_runs.canonical_sha256(history_target.read_bytes())
        if history_target.is_file() else None)
    if actual_history_sha != historical_adoption_file_sha256:
        raise ProvenanceRefused(
            'Stage4B delivery recovery 绑定的 historical adoption 已漂移')
    if historical and actual_history_sha is None:
        raise ProvenanceRefused(
            'Stage4B delivery recovery 的历史章节与 adoption manifest 不闭合')

    run_files: list[dict[str, Any]] = []
    for chapter in sorted(set(requested) - set(historical)):
        run = ensure_plan_chapter_run(ctx, request_by[chapter])
        run_path = (paths.stage4_plan_chapter_run_path(
            ctx.novel_dir, ctx.run_id, chapter) / agent_runs.RUN_FILE)
        run_files.append({
            'chapter': chapter,
            'run_identity_sha256': run['identity_sha256'],
            'run_file_sha256': agent_runs.canonical_sha256(
                run_path.read_bytes()),
        })
    payload: dict[str, Any] = {
        'version': _PLAN_DELIVERY_RECOVERY_VERSION,
        'state': 'waiting',
        'run_id': check_run_id(ctx.run_id),
        'sid': ctx.sid,
        'node': 'stage4.plan',
        'code': ctx.code,
        'candidate_subject_sha256': candidate_subject_sha256,
        'request_set_sha256': _plan_request_set_sha256(normalized),
        'total_chapters': len(requested),
        'completed_chapters': completed,
        'pending_chapters': pending,
        'failures': normalized_failures,
        'deferred_chapters': deferred,
        'historical_chapters': historical,
        'historical_adoption_file_sha256': actual_history_sha,
        'run_files': run_files,
        'updated_at': _now(),
    }
    target = _plan_delivery_recovery_path(ctx)
    with _DESIGN_AGGREGATE_LOCK, process_append_lock(target):
        _atomic_json(target, payload)
    return payload


def read_plan_delivery_recovery(
        novel_dir: Path, run_id: str, *, sid: str, code: str,
) -> dict[str, Any] | None:
    """严格核验 Supervisor 可据以暂停的 Stage4B 恢复凭据。"""
    source = check_run_id(run_id)
    root = paths.stage4_run_path(novel_dir, source, 'stage4.plan')
    target = root / PLAN_DELIVERY_RECOVERY_FILE
    if not target.is_file():
        return None
    record = _read_object(target, label='Stage4B delivery recovery')
    if (set(record) != _PLAN_DELIVERY_RECOVERY_FIELDS
            or record.get('version') != _PLAN_DELIVERY_RECOVERY_VERSION
            or record.get('state') != 'waiting'):
        raise ArtifactUnreadable(
            f'{target} 不是当前 Stage4B delivery recovery 形状')
    expected = {
        'run_id': source, 'sid': sid, 'node': 'stage4.plan', 'code': code}
    if {key: record.get(key) for key in expected} != expected:
        raise ProvenanceRefused(f'{target} 与当前 execution 身份不一致')
    total = record.get('total_chapters')
    completed = record.get('completed_chapters')
    pending = record.get('pending_chapters')
    failures = record.get('failures')
    deferred = record.get('deferred_chapters')
    historical = record.get('historical_chapters')
    run_files = record.get('run_files')
    history_sha = record.get('historical_adoption_file_sha256')
    if (not agent_runs.is_sha256(record.get('candidate_subject_sha256'))
            or not agent_runs.is_sha256(record.get('request_set_sha256'))
            or not isinstance(record.get('updated_at'), str)
            or not record['updated_at']
            or not isinstance(total, int) or isinstance(total, bool) or total < 1
            or any(not isinstance(rows, list) for rows in (
                completed, pending, failures, deferred, historical, run_files))
            or any(not isinstance(chapter, int) or isinstance(chapter, bool)
                   or chapter < 1
                   for chapter in completed + pending + deferred + historical)
            or completed != sorted(completed) or pending != sorted(pending)
            or deferred != sorted(deferred) or historical != sorted(historical)
            or len(completed) + len(pending) != total
            or len(set(completed + pending)) != total
            or set(completed).intersection(pending)
            or not set(deferred).issubset(pending)
            or not set(historical).issubset(completed)
            or (history_sha is not None and not agent_runs.is_sha256(history_sha))
            or (historical and history_sha is None)):
        raise ArtifactUnreadable(f'{target} 的章节计数或哈希不闭合')
    normalized_failures: list[dict[str, Any]] = []
    for row in failures:
        if not isinstance(row, dict):
            raise ArtifactUnreadable(f'{target} 的 failures 含非对象')
        chapter = row.get('chapter')
        status = row.get('status')
        attempts = row.get('attempts')
        if (set(row) != {'chapter', 'error_kind', 'status', 'attempts'}
                or chapter not in pending or chapter in deferred
                or row.get('error_kind') not in SAME_RUN_RESUMABLE_AGENT_FAILURES
                or not isinstance(attempts, int) or isinstance(attempts, bool)
                or attempts < 1
                or (status is not None and (
                    not isinstance(status, int) or isinstance(status, bool)
                    or not 100 <= status <= 599))):
            raise ArtifactUnreadable(f'{target} 的 failure 身份不合法')
        normalized_failures.append(row)
    failed = sorted(row['chapter'] for row in normalized_failures)
    if (not failed or len(failed) != len(set(failed))
            or sorted(failed + deferred) != pending):
        raise ArtifactUnreadable(
            f'{target} 的 failure/deferred 与 pending 章不闭合')

    run_chapters: list[int] = []
    for row in run_files:
        if (not isinstance(row, dict)
                or set(row) != _PLAN_DELIVERY_RUN_FIELDS
                or not isinstance(row.get('chapter'), int)
                or isinstance(row.get('chapter'), bool)
                or not agent_runs.is_sha256(row.get('run_identity_sha256'))
                or not agent_runs.is_sha256(row.get('run_file_sha256'))):
            raise ArtifactUnreadable(f'{target} 的 run_files 身份不合法')
        chapter = row['chapter']
        run_path = (paths.stage4_plan_chapter_run_path(
            novel_dir, source, chapter) / agent_runs.RUN_FILE)
        run = _read_object(run_path, label='Stage4B delivery run')
        identity = {
            key: run[key] for key in agent_runs._RUN_FIELDS
            if key not in ('identity_sha256', 'created_at')
        } if set(run) == agent_runs._RUN_FIELDS else {}
        if (run.get('run_id') != source or run.get('sid') != sid
                or run.get('node') != 'stage4.plan'
                or run.get('code') != code or run.get('chapter') != chapter
                or run.get('identity_sha256') != agent_runs.canonical_sha256(identity)
                or row['run_identity_sha256'] != run.get('identity_sha256')
                or row['run_file_sha256'] != agent_runs.canonical_sha256(
                    run_path.read_bytes())):
            raise ProvenanceRefused(
                f'{target} 绑定的 ch{chapter} run.json 已缺失或漂移')
        run_chapters.append(chapter)
    if (run_chapters != sorted(run_chapters)
            or len(run_chapters) != len(set(run_chapters))
            or sorted(run_chapters + historical)
            != sorted(completed + pending)):
        raise ArtifactUnreadable(
            f'{target} 的 current/historical run 覆盖不闭合')
    history_target = root / PLAN_HISTORICAL_ADOPTION_FILE
    if history_sha is not None:
        if (not history_target.is_file()
                or history_sha != agent_runs.canonical_sha256(
                    history_target.read_bytes())):
            raise ProvenanceRefused(
                f'{target} 绑定的 historical adoption 已缺失或漂移')
        history = _read_object(
            history_target, label='Stage4B historical adoption')
        if (history.get('target_run_id') != source
                or history.get('sid') != sid
                or history.get('node') != 'stage4.plan'
                or history.get('code') != code
                or history.get('candidate_subject_sha256')
                != record['candidate_subject_sha256']
                or history.get('request_set_sha256')
                != record['request_set_sha256']
                or not set(historical).issubset(
                    set(history.get('source_chapters') or []))):
            raise ProvenanceRefused(
                f'{target} 与 historical adoption 身份不闭合')
    elif history_target.exists():
        raise ProvenanceRefused(
            f'{target} 未绑定但盘上出现 historical adoption')
    return record


def ensure_design_unit_run(
        ctx: Any, request: DesignUnitRequest,
) -> dict[str, Any]:
    """创建或复验一份 strategy/单章 immutable run。"""
    return agent_runs.ensure_run(
        ctx, design_unit_run_spec(request.chapter),
        input_sha256=request.input_sha256,
        system=request.system, user=request.user,
        response_model=request.response_model,
        max_tokens=request.max_tokens)


def write_design_unit_checkpoint(
        ctx: Any, request: DesignUnitRequest, *,
        response_payload: Mapping[str, Any], acceptance: Mapping[str, Any],
        state_payload: Mapping[str, Any], revision_key: str = 'terminal',
) -> Path:
    """一份 unit 业务验证通过后立即追加检查点。"""
    return agent_runs.write_checkpoint(
        ctx, design_unit_run_spec(request.chapter),
        input_sha256=request.input_sha256,
        system=request.system, user=request.user,
        response_model=request.response_model,
        max_tokens=request.max_tokens,
        response_payload=response_payload,
        acceptance=acceptance,
        revision_key=revision_key,
        state_payload=state_payload)


def restore_design_unit_checkpoint(
        ctx: Any, request: DesignUnitRequest, *,
        validator: Callable[[BaseModel, ChatResponse], str | None] | None = None,
) -> agent_runs.RestoredCheckpoint[BaseModel] | None:
    """只恢复该 unit 的 latest accepted revision；其他章失败不影响它。"""
    return agent_runs.restore_checkpoint(
        ctx, design_unit_run_spec(request.chapter),
        input_sha256=request.input_sha256,
        system=request.system, user=request.user,
        response_model=request.response_model,
        max_tokens=request.max_tokens,
        validator=validator)


def _normalized_design_repair_requests(
        requests: Sequence[DesignUnitRequest],
) -> tuple[DesignUnitRequest, ...]:
    if not isinstance(requests, Sequence) or isinstance(requests, (str, bytes)):
        raise ProvenanceRefused('Stage4A 历史采用请求必须是非空序列')
    by_chapter: dict[int, DesignUnitRequest] = {}
    for request in requests:
        if not isinstance(request, DesignUnitRequest) or request.chapter is None:
            raise ProvenanceRefused('Stage4A 历史采用只接受 targeted repair 章单元')
        if request.chapter in by_chapter:
            raise ProvenanceRefused(f'Stage4A 历史采用重复 ch{request.chapter}')
        by_chapter[request.chapter] = request
    if not by_chapter:
        raise ProvenanceRefused('Stage4A 历史采用请求不能为空')
    return tuple(by_chapter[chapter] for chapter in sorted(by_chapter))


def _design_repair_request_row(request: DesignUnitRequest) -> dict[str, Any]:
    return {
        'chapter': request.chapter,
        'input_sha256': request.input_sha256,
        'system_sha256': agent_runs.canonical_sha256(
            request.system.encode('utf-8')),
        'user_sha256': agent_runs.canonical_sha256(
            request.user.encode('utf-8')),
        'response_model': request.response_model.__name__,
        'response_schema_sha256': agent_runs.canonical_sha256(
            agent_runs.agent_json_schema(request.response_model)),
        'max_tokens': request.max_tokens,
    }


def _design_repair_request_set_sha256(
        requests: Sequence[DesignUnitRequest],
) -> str:
    return agent_runs.canonical_sha256([
        _design_repair_request_row(request)
        for request in _normalized_design_repair_requests(requests)
    ])


def _design_historical_adoption_path(ctx: Any) -> Path:
    return (paths.stage4_run_path(
        ctx.novel_dir, ctx.run_id, 'stage4.design')
        / DESIGN_HISTORICAL_ADOPTION_FILE)


def _design_historical_hash(payload: Mapping[str, Any]) -> str:
    return agent_runs.canonical_sha256({
        key: value for key, value in payload.items()
        if key not in ('created_at', 'manifest_sha256')
    })


def _source_has_design_checkpoint(
        ctx: Any, source_run_id: str, chapter: int | None,
) -> bool:
    source = check_run_id(source_run_id)
    for root in runtime_layout.stage_run_roots(ctx.novel_dir, 'stage4'):
        checkpoint_dir = (
            root / source / 'design' / 'units'
            / paths.stage4_design_unit_dirname(chapter) / 'checkpoints')
        if checkpoint_dir.is_dir() and any(
                child.is_file() and child.suffix == '.json'
                for child in checkpoint_dir.iterdir()):
            return True
    return False


def design_unit_checkpoint_exists(
        ctx: Any, chapter: int | None, *, run_id: str | None = None,
) -> bool:
    """只判断 revision 是否存在；真正采用仍走完整 restore 校验。"""
    return _source_has_design_checkpoint(
        ctx, check_run_id(run_id or ctx.run_id), chapter)


def _historical_design_checkpoint_path(
        ctx: Any, source_run_id: str, chapter: int | None, revision: int,
) -> Path:
    runtime_layout.stage_run_roots(ctx.novel_dir, 'stage4')
    return (paths.stage4_design_unit_checkpoint_dir(ctx.novel_dir, source_run_id, chapter)
            / f'rev-{revision:04d}.json')


def _historical_design_run_path(ctx: Any, run_id: str, chapter: int | None) -> Path:
    runtime_layout.stage_run_roots(ctx.novel_dir, 'stage4')
    return paths.stage4_design_unit_run_path(ctx.novel_dir, run_id, chapter) / agent_runs.RUN_FILE


def _design_historical_source_row(
        ctx: Any, chapter: int,
        historical: agent_runs.RestoredHistoricalRun[Any],
) -> dict[str, Any]:
    acceptance = historical.checkpoint.acceptance
    target_run = paths.stage4_design_unit_run_path(
        ctx.novel_dir, ctx.run_id, chapter) / agent_runs.RUN_FILE
    try:
        target_bytes = target_run.read_bytes()
        checkpoint_bytes = _historical_design_checkpoint_path(
            ctx, historical.context.run_id, chapter,
            historical.checkpoint.revision).read_bytes()
    except OSError as exc:
        raise ProvenanceRefused(
            f'Stage4A historical adoption 来源/目标文件不可读：{exc}') from exc
    target = _read_object(target_run, label='Stage4A target design unit run')
    return {
        'chapter': chapter,
        'source_code': historical.context.code,
        'source_run_identity_sha256': historical.run_record['identity_sha256'],
        'source_run_file_sha256': historical.run_sha256,
        'source_checkpoint_revision': historical.checkpoint.revision,
        'source_checkpoint_sha256': historical.checkpoint.checkpoint_sha256,
        'source_checkpoint_file_sha256':
            agent_runs.canonical_sha256(checkpoint_bytes),
        'source_attempt_id': acceptance['attempt_id'],
        'source_request_fingerprint': acceptance['request_fingerprint'],
        'source_response_sha256': acceptance['ledger_response_sha256'],
        'source_model': acceptance['model'],
        'target_run_identity_sha256': target['identity_sha256'],
        'target_run_file_sha256': agent_runs.canonical_sha256(target_bytes),
    }


def _restore_design_historical_chapter(
        ctx: Any, *, source_run_id: str, request: DesignUnitRequest,
        validator: Callable[[BaseModel, ChatResponse], str | None] | None,
        state_payload: Callable[[BaseModel], Mapping[str, Any]],
) -> agent_runs.RestoredHistoricalRun[Any]:
    target = ensure_design_unit_run(ctx, request)
    historical = agent_runs.restore_historical_run(
        ctx, design_unit_run_spec(request.chapter),
        source_run_id=source_run_id,
        input_sha256=request.input_sha256,
        system=request.system, user=request.user,
        response_model=request.response_model,
        max_tokens=request.max_tokens,
        validator=validator,
    )
    # 跨 code 只复用响应，不复用配置：target/source 的 resolved profile、schema、
    # token ceiling、恢复策略和请求正文必须逐字段相等。
    comparable = (
        'sid', 'node', 'session_owner', 'chapter', 'purpose',
        'input_sha256', 'request_policy', 'request_policy_sha256', 'request',
    )
    if ({key: historical.run_record.get(key) for key in comparable}
            != {key: target.get(key) for key in comparable}):
        raise ProvenanceRefused(
            f'Stage4A ch{request.chapter} 历史 run 与 target profile/request 漂移')
    checkpoint = historical.checkpoint
    expected_state = dict(state_payload(checkpoint.value))
    turn = checkpoint.acceptance.get('accepted_turn_index')
    if (checkpoint.revision_key != 'terminal'
            or checkpoint.state_payload != expected_state
            or not isinstance(turn, int) or isinstance(turn, bool)
            or turn < 1 or turn > 2):
        raise ProvenanceRefused(
            f'Stage4A ch{request.chapter} 历史 checkpoint 不是闭合 repair 章单元')
    return historical


def _read_design_historical_adoption(
        ctx: Any, *, requests: Sequence[DesignUnitRequest],
        repair_input_sha256: str, eligible_chapters: Iterable[int],
        validators: Mapping[
            int, Callable[[BaseModel, ChatResponse], str | None] | None],
        state_payloads: Mapping[
            int, Callable[[BaseModel], Mapping[str, Any]]],
) -> DesignHistoricalAdoption | None:
    target = _design_historical_adoption_path(ctx)
    if not target.is_file():
        return None
    record = _read_object(target, label='Stage4A historical adoption')
    normalized = _normalized_design_repair_requests(requests)
    request_by = {request.chapter: request for request in normalized}
    eligible = sorted({int(chapter) for chapter in eligible_chapters})
    expected = {
        'target_run_id': check_run_id(ctx.run_id), 'sid': ctx.sid,
        'node': 'stage4.design', 'code': ctx.code,
        'repair_input_sha256': repair_input_sha256,
        'request_set_sha256': _design_repair_request_set_sha256(normalized),
    }
    if (set(record) != _DESIGN_HISTORICAL_ADOPTION_FIELDS
            or record.get('version') != _DESIGN_HISTORICAL_ADOPTION_VERSION
            or record.get('kind') != _DESIGN_HISTORICAL_ADOPTION_KIND
            or record.get('state') != 'selected'
            or record.get('manifest_sha256') != _design_historical_hash(record)):
        raise ArtifactUnreadable(f'{target} 不是闭合的 Stage4A adoption manifest')
    if ({key: record.get(key) for key in expected} != expected
            or not isinstance(record.get('created_at'), str)
            or not record['created_at']):
        raise ProvenanceRefused(f'{target} 与当前 repair target/request 漂移')
    source_run_id = record.get('source_run_id')
    chapters = record.get('source_chapters')
    rows = record.get('sources')
    if (not isinstance(source_run_id, str)
            or check_run_id(source_run_id) == check_run_id(ctx.run_id)
            or not isinstance(chapters, list) or chapters != sorted(chapters)
            or not chapters or len(chapters) != len(set(chapters))
            or any(chapter not in request_by for chapter in chapters)
            or not isinstance(rows, list) or len(rows) != len(chapters)):
        raise ProvenanceRefused(f'{target} 的 source run/chapters 不闭合')
    restored: list[tuple[int, agent_runs.RestoredHistoricalRun[Any]]] = []
    for chapter, row in zip(chapters, rows, strict=True):
        if (not isinstance(row, dict)
                or set(row) != _DESIGN_HISTORICAL_SOURCE_FIELDS
                or row.get('chapter') != chapter):
            raise ArtifactUnreadable(f'{target} 的 source row 形状漂移')
        historical = _restore_design_historical_chapter(
            ctx, source_run_id=source_run_id, request=request_by[chapter],
            validator=validators.get(chapter),
            state_payload=state_payloads[chapter])
        if row != _design_historical_source_row(ctx, chapter, historical):
            raise ProvenanceRefused(
                f'{target} 的 ch{chapter} source/target/ledger 漂移')
        if chapter in eligible:
            restored.append((chapter, historical))
    return DesignHistoricalAdoption(
        manifest_file_sha256=agent_runs.canonical_sha256(target.read_bytes()),
        source_run_id=source_run_id,
        restored=tuple(restored),
    )


def restore_or_select_design_historical_adoption(
        ctx: Any, *, requests: Sequence[DesignUnitRequest],
        repair_input_sha256: str, eligible_chapters: Iterable[int],
        validators: Mapping[
            int, Callable[[BaseModel, ChatResponse], str | None] | None],
        state_payloads: Mapping[
            int, Callable[[BaseModel], Mapping[str, Any]]],
) -> DesignHistoricalAdoption | None:
    """为 targeted repair 选择唯一最大合法旧 run；旧 provider 永不续接。"""
    normalized = _normalized_design_repair_requests(requests)
    if not agent_runs.is_sha256(repair_input_sha256):
        raise ProvenanceRefused('Stage4A historical adoption 缺 repair input SHA-256')
    request_by = {request.chapter: request for request in normalized}
    eligible = sorted({int(chapter) for chapter in eligible_chapters})
    if (any(chapter not in request_by for chapter in eligible)
            or set(validators) != set(request_by)
            or set(state_payloads) != set(request_by)):
        raise ProvenanceRefused(
            'Stage4A historical adoption 请求/validator 章集合不闭合')
    existing = _read_design_historical_adoption(
        ctx, requests=normalized, repair_input_sha256=repair_input_sha256,
        eligible_chapters=eligible, validators=validators,
        state_payloads=state_payloads)
    if existing is not None:
        return existing
    if not eligible:
        return None
    candidates: list[
        tuple[str, tuple[tuple[int, agent_runs.RestoredHistoricalRun[Any]], ...]]
    ] = []
    for source_run_id in _plan_source_run_ids(ctx):
        source_chapters = [
            chapter for chapter in eligible
            if _source_has_design_checkpoint(ctx, source_run_id, chapter)]
        if not source_chapters:
            continue
        restored: list[tuple[int, agent_runs.RestoredHistoricalRun[Any]]] = []
        for chapter in source_chapters:
            request = request_by[chapter]
            if not _historical_request_matches(
                    ctx, request, source_run_id=source_run_id, node='stage4.design'):
                continue
            restored.append((chapter, _restore_design_historical_chapter(
                ctx, source_run_id=source_run_id, request=request,
                validator=validators.get(chapter), state_payload=state_payloads[chapter])))
        if not restored:
            continue
        candidates.append((source_run_id, tuple(restored)))
    if not candidates:
        return None
    best_size = max(len(rows) for _source, rows in candidates)
    best = [(source, rows) for source, rows in candidates
            if len(rows) == best_size]
    if len(best) != 1:
        ctx.observe(
            'stage4_design_historical_adoption_ambiguous',
            candidate_count=len(best), checkpoint_count=best_size)
        return None
    source_run_id, restored = best[0]
    payload: dict[str, Any] = {
        'version': _DESIGN_HISTORICAL_ADOPTION_VERSION,
        'kind': _DESIGN_HISTORICAL_ADOPTION_KIND,
        'state': 'selected', 'target_run_id': check_run_id(ctx.run_id),
        'sid': ctx.sid, 'node': 'stage4.design', 'code': ctx.code,
        'repair_input_sha256': repair_input_sha256,
        'request_set_sha256': _design_repair_request_set_sha256(normalized),
        'source_run_id': source_run_id,
        'source_chapters': [chapter for chapter, _row in restored],
        'sources': [_design_historical_source_row(ctx, chapter, historical)
                    for chapter, historical in restored],
        'created_at': _now(),
    }
    payload['manifest_sha256'] = _design_historical_hash(payload)
    target = _design_historical_adoption_path(ctx)
    with _DESIGN_AGGREGATE_LOCK, process_append_lock(target):
        if target.is_file():
            return _read_design_historical_adoption(
                ctx, requests=normalized,
                repair_input_sha256=repair_input_sha256,
                eligible_chapters=eligible, validators=validators,
                state_payloads=state_payloads)
        _atomic_json(target, payload)
    ctx.observe(
        'stage4_design_historical_checkpoint_adopted',
        source_run_id=source_run_id,
        chapters=[chapter for chapter, _row in restored])
    return DesignHistoricalAdoption(
        manifest_file_sha256=agent_runs.canonical_sha256(target.read_bytes()),
        source_run_id=source_run_id, restored=restored)


def require_design_historical_adoption_file(
        ctx: Any, expected_file_sha256: str | None,
) -> None:
    target = _design_historical_adoption_path(ctx)
    actual = (agent_runs.canonical_sha256(target.read_bytes())
              if target.is_file() else None)
    if actual != expected_file_sha256:
        raise ProvenanceRefused(
            'Stage4A historical adoption manifest 在运行期间出现、消失或漂移')


def historical_design_unit_source(
        adoption: DesignHistoricalAdoption, chapter: int,
) -> DesignUnitSource:
    try:
        historical = adoption.by_chapter()[chapter]
    except KeyError:
        raise ProvenanceRefused(
            f'Stage4A historical adoption 未包含 ch{chapter}') from None
    checkpoint = historical.checkpoint
    acceptance = checkpoint.acceptance
    payload = checkpoint.value.model_dump(by_alias=True, mode='json')
    return DesignUnitSource(
        chapter=chapter, source_run_id=historical.context.run_id,
        purpose=design_unit_purpose(chapter), source_kind='repair',
        response_payload=payload,
        response_payload_sha256=agent_runs.canonical_sha256(payload),
        origin='historical_checkpoint',
        source_ref=adoption.manifest_file_sha256,
        attempt_id=str(acceptance['attempt_id']),
        run_sha256=historical.run_sha256,
        checkpoint_revision=checkpoint.revision,
        checkpoint_sha256=checkpoint.checkpoint_sha256,
        checkpoint_file_sha256=agent_runs.canonical_sha256(
            _historical_design_checkpoint_path(
                historical.context, historical.context.run_id,
                chapter, checkpoint.revision).read_bytes()),
        model=str(acceptance['model']),
        accepted_turn_index=int(acceptance['accepted_turn_index']),
    )


def design_aggregate_usage(
        ctx: Any, units: Sequence[DesignUnitRequest],
        *, historical: Mapping[int | None, Any] | None = None,
        preserved: Sequence[DesignUnitSource] = (),
) -> Usage | None:
    """精确聚合 strategy + ch1..N 这一 run 的全部调用。

    accepted checkpoint 的 ``attempt_links`` 是可核验的最终采纳链，
    但不是完整成本边界：某 unit 曾在没有 checkpoint 时失败，
    同 run 新进程里成功落下的 checkpoint 不会知道旧进程那些调用。

    本口先用当前 prompt/schema/profile 完整恢复每份 checkpoint，
    再把 N+1 个稳定 purpose 与封存的 links 交给
    ``CostLedger.usage_for_complete_purposes``。后者以
    ``run_id + sid + node + purpose`` 选出盘上（或 path=None 时的内存）
    全部 attempts，links 只作为必须存在且完全相等的子集锚点。
    因此结果不受当前 ``RunContext`` 的
    UsageMark、已恢复单元顺序或不相关调用影响，可直接作为
    ``BlindCandidateProvenance.usage``。
    """
    normalized_units = _normalized_design_units(units)
    historical = dict(historical or {})
    preserved_by = {row.chapter: row for row in preserved}
    if set(preserved_by) & set(historical):
        raise ProvenanceRefused('父候选保留与普通历史采用不能覆盖同一单元')
    identities: list[tuple[str, Mapping[str, object]]] = []
    seen_attempts: set[str] = set()
    accepted_attempts: set[str] = set()
    provider_sessions: set[str] = set()
    for unit in normalized_units:
        if unit.chapter in preserved_by:
            continue
        purpose = design_unit_purpose(unit.chapter)
        previous = historical.get(unit.chapter)
        if previous is None:
            restored = restore_design_unit_checkpoint(ctx, unit)
        else:
            from novel.llm import design_adoption
            checked = design_adoption.restore(ctx, unit, previous.context.run_id)
            if checked.checkpoint != previous.checkpoint or checked.run_sha256 != previous.run_sha256:
                raise ProvenanceRefused('Stage4A 历史来源在聚合期间漂移')
            restored = checked.checkpoint
        if restored is None:
            raise ProvenanceRefused(
                f'{purpose}: usage 聚合前缺 accepted checkpoint')
        acceptance = restored.acceptance
        accepted_attempt = str(acceptance.get('attempt_id') or '')
        provider_session = str(acceptance.get('provider_session_id') or '')
        links = acceptance.get('attempt_links')
        if (not _is_attempt_id(accepted_attempt)
                or accepted_attempt in accepted_attempts
                or not provider_session
                or provider_session in provider_sessions
                or not isinstance(links, list) or not links):
            raise ProvenanceRefused(
                f'{purpose}: accepted attempt/session/attempt_links 不闭合')
        accepted_attempts.add(accepted_attempt)
        provider_sessions.add(provider_session)
        linked_attempts: set[str] = set()
        for link in links:
            if not isinstance(link, Mapping):
                raise ProvenanceRefused(f'{purpose}: attempt_links 含非对象')
            identity = dict(link)
            attempt_id = identity.get('attempt_id')
            if (not _is_attempt_id(attempt_id)
                    or attempt_id in linked_attempts
                    or attempt_id in seen_attempts):
                raise ProvenanceRefused(
                    f'{purpose}: attempt_links 含重复/不合法 attempt')
            linked_attempts.add(str(attempt_id))
            seen_attempts.add(str(attempt_id))
            if previous is None:
                identities.append((purpose, identity))
        if accepted_attempt not in linked_attempts:
            raise ProvenanceRefused(
                f'{purpose}: accepted attempt 未被 attempt_links 联结')
    if len(accepted_attempts) + len(preserved_by) != len(normalized_units):
        raise ProvenanceRefused('Stage4 design usage 缺 N+1 accepted attempts')
    usage = ctx.ledger.usage_for_complete_purposes(
        [design_unit_purpose(unit.chapter) for unit in normalized_units],
        sid=ctx.sid, node='stage4.design', required_identities=identities)
    if usage is None and not historical and not preserved:
        raise ProvenanceRefused(
            'Stage4 design 已有 N+1 accepted checkpoints，但精确 usage 为空')
    return usage


def design_repair_usage(
        ctx: Any, units: Sequence[DesignUnitRequest]) -> Usage:
    """精确聚合当前 repair run 的目标章调用；不重复认领父 run 成本。"""
    if not units or any(unit.chapter is None for unit in units):
        raise ProvenanceRefused('design repair usage 只接受非空目标章 units')
    by = {unit.chapter: unit for unit in units}
    if len(by) != len(units):
        raise ProvenanceRefused('design repair usage 目标章重复')
    identities: list[tuple[str, Mapping[str, object]]] = []
    purposes: list[str] = []
    for chapter in sorted(by):
        unit = by[chapter]
        purpose = design_unit_purpose(unit.chapter)
        restored = restore_design_unit_checkpoint(ctx, unit)
        if restored is None:
            raise ProvenanceRefused(f'{purpose}: repair usage 前缺 checkpoint')
        links = restored.acceptance.get('attempt_links')
        turn = restored.acceptance.get('accepted_turn_index')
        if (not isinstance(links, list) or not links
                or not isinstance(turn, int) or isinstance(turn, bool)
                or turn != len(links) or turn > 2):
            raise ProvenanceRefused(f'{purpose}: repair content turn 必须在 1..2')
        for link in links:
            if not isinstance(link, Mapping):
                raise ProvenanceRefused(f'{purpose}: repair attempt link 非 object')
            identities.append((purpose, dict(link)))
        purposes.append(purpose)
    usage = ctx.ledger.usage_for_complete_purposes(
        purposes, sid=ctx.sid, node='stage4.design',
        required_identities=identities)
    if usage is None:
        raise ProvenanceRefused('design repair 已有 checkpoints 但 usage 为空')
    return usage


def design_repair_sources_usage(
        ctx: Any, units: Sequence[DesignUnitRequest],
        sources: Sequence[DesignUnitSource], *,
        validators: Mapping[
            int, Callable[[BaseModel, ChatResponse], str | None] | None],
        state_payloads: Mapping[
            int, Callable[[BaseModel], Mapping[str, Any]]],
) -> Usage:
    """聚合 current + historical repair checkpoints 的真实分 run 成本。"""
    normalized = _normalized_design_repair_requests(units)
    unit_by = {unit.chapter: unit for unit in normalized}
    source_by = {
        source.chapter: source for source in sources
        if source.source_kind == 'repair'
    }
    if (set(source_by) != set(unit_by)
            or set(validators) != set(unit_by)
            or set(state_payloads) != set(unit_by)):
        raise ProvenanceRefused(
            'design repair usage 的 units/sources/validators 未精确覆盖 targets')
    buckets: dict[str, dict[str, Any]] = {}
    for chapter in sorted(unit_by):
        unit = unit_by[chapter]
        source = source_by[chapter]
        if source.origin == 'agent':
            if source.source_run_id != check_run_id(ctx.run_id):
                raise ProvenanceRefused(
                    f'ch{chapter} current repair source 不属于当前 run')
            restored = restore_design_unit_checkpoint(
                ctx, unit, validator=validators[chapter])
            if restored is None:
                raise ProvenanceRefused(f'ch{chapter} current repair checkpoint 缺失')
            historical_context = ctx
        elif source.origin == 'historical_checkpoint':
            # 先复验 manifest owner，再按当前 validator/request 重放 source。
            checked = _load_design_source(
                ctx, _design_source_row(source),
                default_run_id=source.source_run_id,
                default_source_kind='repair')
            if checked != source:
                raise ProvenanceRefused(f'ch{chapter} historical source 投影漂移')
            historical = _restore_design_historical_chapter(
                ctx, source_run_id=source.source_run_id, request=unit,
                validator=validators[chapter],
                state_payload=state_payloads[chapter])
            restored = historical.checkpoint
            historical_context = historical.context
        else:
            raise ProvenanceRefused(
                f'ch{chapter} repair usage 不接受 origin={source.origin!r}')
        payload = restored.value.model_dump(by_alias=True, mode='json')
        acceptance = restored.acceptance
        if (source.attempt_id != acceptance.get('attempt_id')
                or source.checkpoint_revision != restored.revision
                or source.checkpoint_sha256 != restored.checkpoint_sha256
                or source.response_payload_sha256
                != agent_runs.canonical_sha256(payload)
                or source.model != acceptance.get('model')):
            raise ProvenanceRefused(
                f'ch{chapter} repair source 与恢复 checkpoint/ledger 漂移')
        links = acceptance.get('attempt_links')
        turn = acceptance.get('accepted_turn_index')
        if (not isinstance(links, list) or not links
                or not isinstance(turn, int) or isinstance(turn, bool)
                or turn != len(links) or turn > 2):
            raise ProvenanceRefused(f'ch{chapter} repair content turn 必须在 1..2')
        bucket = buckets.setdefault(source.source_run_id, {
            'context': historical_context, 'purposes': [], 'identities': [],
        })
        if bucket['context'].ledger.path != historical_context.ledger.path:
            raise ProvenanceRefused('同一 source run 意外指向不同成本账')
        purpose = design_unit_purpose(chapter)
        bucket['purposes'].append(purpose)
        for link in links:
            if not isinstance(link, Mapping):
                raise ProvenanceRefused(f'{purpose}: repair attempt link 非 object')
            bucket['identities'].append((purpose, dict(link)))
    parts: list[Usage | None] = []
    for run_id in sorted(buckets):
        bucket = buckets[run_id]
        parts.append(bucket['context'].ledger.usage_for_complete_purposes(
            bucket['purposes'], sid=ctx.sid, node='stage4.design',
            required_identities=bucket['identities']))
    usage = combine_usage(*parts)
    if usage is None:
        raise ProvenanceRefused('design repair 历史/当前 source usage 有未知分段')
    return usage


def design_replan_usage(
        ctx: Any, units: Sequence[DesignUnitRequest]) -> Usage:
    """精确聚合一次 typed replan 的 strategy + 受影响章调用。

    与首次 design 的 N+1 不同，replan 只拥有一条新 strategy 和 effective
    input 真变化的章；与 targeted repair 不同，它允许 strategy unit，且不把
    继承 checkpoint 的历史成本再次认领进当前 run。
    """
    if len(units) < 2 or sum(unit.chapter is None for unit in units) != 1:
        raise ProvenanceRefused(
            'design replan usage 必须含一条 strategy 与至少一章新 unit')
    keys = [unit.chapter for unit in units]
    if len(keys) != len(set(keys)):
        raise ProvenanceRefused('design replan usage unit 重复')
    identities: list[tuple[str, Mapping[str, object]]] = []
    purposes: list[str] = []
    seen_attempts: set[str] = set()
    for unit in sorted(units, key=lambda row: (-1 if row.chapter is None
                                               else row.chapter)):
        purpose = design_unit_purpose(unit.chapter)
        restored = restore_design_unit_checkpoint(ctx, unit)
        if restored is None:
            raise ProvenanceRefused(f'{purpose}: replan usage 前缺 checkpoint')
        acceptance = restored.acceptance
        links = acceptance.get('attempt_links')
        accepted = acceptance.get('attempt_id')
        turn = acceptance.get('accepted_turn_index')
        if (not isinstance(links, list) or not links
                or not _is_attempt_id(accepted)
                or not isinstance(turn, int) or isinstance(turn, bool)
                or turn != len(links)):
            raise ProvenanceRefused(
                f'{purpose}: replan acceptance/turn/attempt_links 不闭合')
        linked: set[str] = set()
        for link in links:
            if not isinstance(link, Mapping):
                raise ProvenanceRefused(f'{purpose}: replan attempt link 非 object')
            attempt_id = link.get('attempt_id')
            if (not _is_attempt_id(attempt_id)
                    or str(attempt_id) in linked
                    or str(attempt_id) in seen_attempts):
                raise ProvenanceRefused(
                    f'{purpose}: replan attempt link 重复或不合法')
            linked.add(str(attempt_id))
            seen_attempts.add(str(attempt_id))
            identities.append((purpose, dict(link)))
        if str(accepted) not in linked:
            raise ProvenanceRefused(
                f'{purpose}: replan accepted attempt 未被 links 联结')
        purposes.append(purpose)
    usage = ctx.ledger.usage_for_complete_purposes(
        purposes, sid=ctx.sid, node='stage4.design',
        required_identities=identities)
    if usage is None:
        raise ProvenanceRefused('design replan 已有 checkpoints 但 usage 为空')
    return usage


def _run_spec(node: str) -> agent_runs.SingleSessionRunSpec:
    recovery = recovery_policy(node)
    other = tuple(value for value in RECOVERY_POLICIES if value != node)
    return agent_runs.SingleSessionRunSpec(
        label=f'Stage4 {node.removeprefix("stage4.")}',
        node=node,
        session_owner=node,
        purpose=node,
        chapter=None,
        phase=node.removeprefix('stage4.'),
        recovery_policy=recovery,
        session_isolation='per-node',
        # 仅冻结整书聚合的原配置；模型请求由 strategy/chapter spec 登记。
        track_pending=False,
        run_path=lambda ctx: paths.stage4_run_path(
            ctx.novel_dir, ctx.run_id, node),
        checkpoint_dir=lambda ctx: paths.stage4_checkpoint_dir(
            ctx.novel_dir, ctx.run_id, node),
        incompatible_session_nodes=other,
    )


def plan_chapter_commit_spec(chapter: int) -> agent_runs.CommitBundleSpec:
    """批次 4：章级拓扑的 commit 账机——journal 锚定锚章 checkpoint 链。

    primary/touched 面与整书 spec 完全一致；只有 run/commit 路径落在
    ``plan/chapters/chNNNN/``。聚合响应是代码从章 session + 机械基线编译的，
    不是任何 provider 的逐字 turn，因此**不能**在整书链上伪铸 acceptance；
    journal 改为锚定真实存在的那条章 checkpoint 链（锚章 attempt）。
    """
    primary = STAGE4_EVIDENCE_BINDING
    allowed = (
        (primary,),
        (primary, _PLAN_INVALID),
        (primary, STAGE4_PLAN_CHAPTERS, _PLAN_INVALID),
    )
    return agent_runs.CommitBundleSpec(
        run=plan_chapter_run_spec(chapter),
        commit_path=lambda ctx: (
            paths.stage4_plan_chapter_run_path(
                ctx.novel_dir, ctx.run_id, chapter)
            / agent_runs.COMMIT_FILE),
        primary_artifact=primary,
        allowed_touched=allowed,
        nonformal_paths=(_PLAN_INVALID,),
    )


def _commit_spec(node: str) -> agent_runs.CommitBundleSpec:
    run = _run_spec(node)
    if node == 'stage4.design':
        primary = STAGE4_BLIND_CANDIDATE
        allowed = ((primary,),)
        nonformal: tuple[str, ...] = ()
    else:
        primary = STAGE4_EVIDENCE_BINDING
        allowed = (
            (primary,),
            (primary, _PLAN_INVALID),
            (primary, STAGE4_PLAN_CHAPTERS, _PLAN_INVALID),
        )
        nonformal = (_PLAN_INVALID,)
    return agent_runs.CommitBundleSpec(
        run=run,
        commit_path=lambda ctx: (
            paths.stage4_run_path(ctx.novel_dir, ctx.run_id, node)
            / agent_runs.COMMIT_FILE),
        primary_artifact=primary,
        allowed_touched=allowed,
        nonformal_paths=nonformal,
    )


def ensure_run(
        ctx: Any, *, input_sha256: str, system: str, user: str,
        response_model: type[BaseModel], max_tokens: int,
) -> dict[str, Any]:
    return agent_runs.ensure_run(
        ctx, _run_spec(ctx.node), input_sha256=input_sha256,
        system=system, user=user, response_model=response_model,
        max_tokens=max_tokens)


def write_checkpoint(
        ctx: Any, *, input_sha256: str, system: str, user: str,
        response_model: type[BaseModel], max_tokens: int,
        response_payload: Mapping[str, Any], acceptance: Mapping[str, Any],
        state_payload: Mapping[str, Any], revision_key: str = 'terminal',
) -> Path:
    """追加 Stage4 accepted 候选。

    design 只有终态 revision，沿用默认 ``terminal``；plan 的业务 HARD
    返工循环用 ``candidate``，每一份可物化候选都保留在同一哈希链里。
    """
    return agent_runs.write_checkpoint(
        ctx, _run_spec(ctx.node), input_sha256=input_sha256,
        system=system, user=user, response_model=response_model,
        max_tokens=max_tokens, response_payload=response_payload,
        acceptance=acceptance, revision_key=revision_key,
        state_payload=state_payload)


def restore_checkpoint(
        ctx: Any, *, input_sha256: str, system: str, user: str,
        response_model: type[M], max_tokens: int,
        validator: Callable[[M, ChatResponse], str | None] | None = None,
) -> agent_runs.RestoredCheckpoint[M] | None:
    return agent_runs.restore_checkpoint(
        ctx, _run_spec(ctx.node), input_sha256=input_sha256,
        system=system, user=user, response_model=response_model,
        max_tokens=max_tokens, validator=validator)


def _ownership_thread_lock(path: Path) -> threading.RLock:
    key = os.path.normcase(str(path.resolve()))
    with _OWNERSHIP_REGISTRY_LOCK:
        lock = _OWNERSHIP_THREAD_LOCKS.get(key)
        if lock is None:
            lock = _OWNERSHIP_THREAD_LOCKS[key] = threading.RLock()
        return lock


def _ownership_key(novel_dir: Path) -> str:
    return os.path.normcase(str(paths.stage4_commit_lock_path(novel_dir).resolve()))


@contextmanager
def commit_ownership(novel_dir: Path):
    """同一本书 design/plan 的正式写盘与恢复共用线程/进程锁。"""
    target = paths.stage4_commit_lock_path(novel_dir)
    key = _ownership_key(Path(novel_dir))
    held = getattr(_OWNERSHIP_LOCAL, 'depths', None)
    if held is None:
        held = _OWNERSHIP_LOCAL.depths = {}
    with _ownership_thread_lock(target):
        if held.get(key, 0):
            held[key] += 1
            try:
                yield
            finally:
                held[key] -= 1
            return
        with process_append_lock(target):
            held[key] = 1
            try:
                yield
            finally:
                del held[key]


def _require_ownership(ctx: Any) -> None:
    held = getattr(_OWNERSHIP_LOCAL, 'depths', {})
    if held.get(_ownership_key(Path(ctx.novel_dir)), 0) < 1:
        raise ProvenanceRefused(
            'Stage4 commit 必须在 commit_ownership(novel_dir) 临界区内完成 '
            'prepare、正式写盘、readback、adoption 与 committed')


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec='seconds')


def _atomic_json(path: Path, payload: Mapping[str, Any]) -> None:
    # 原子 JSON 写口仍只在通用 agent run core 一处；Stage4 只定义
    # aggregate 业务状态，不再复制 tempfile/fsync/replace 协议。
    agent_runs._atomic_json(path, payload)


def _read_object(path: Path, *, label: str) -> dict[str, Any]:
    try:
        raw = path.read_bytes()
        value = json.loads(raw.decode('utf-8'))
    except FileNotFoundError:
        raise ProvenanceRefused(f'{label} {path} 不在盘上') from None
    except (OSError, UnicodeError, json.JSONDecodeError) as exc:
        raise ArtifactUnreadable(f'{label} {path} 读不出来：{exc}') from exc
    if not isinstance(value, dict):
        raise ArtifactUnreadable(f'{label} {path} 不是 JSON object')
    return value


def _normalized_design_candidate_identity(
        payload: Mapping[str, Any], *, label: str,
        aggregate_kind: str | None = None,
) -> tuple[dict[str, Any], str]:
    """验证 journal 候选的当前精确身份，不补默认字段或迁移旧来源。

    primary_payload_sha256 始终核原始 journal JSON；Pydantic 输出必须与原载荷
    完全相等，不能靠类型归一或默认值掩盖缺字段。
    """
    if aggregate_kind not in (None, 'standard', 'repair', 'human'):
        raise ValueError(f'未知 design aggregate kind：{aggregate_kind!r}')
    candidate = copy.deepcopy(dict(payload))
    provenance = candidate.get('_provenance')
    if not isinstance(provenance, dict):
        raise ArtifactUnreadable(f'{label} primary_payload 缺 typed _provenance')
    if aggregate_kind is not None:
        expected_architectures = (
            {'book_strategy_chapters_v1', 'book_strategy_adopted_v1'} if aggregate_kind == 'standard'
            else {'human_chapter_edit_v1'} if aggregate_kind == 'human'
            else {
                'book_strategy_targeted_repair_v1',
                'book_strategy_replan_v1',
            }
        )
        if provenance.get('architecture') not in expected_architectures:
            raise ArtifactUnreadable(
                f'{label} primary_payload architecture 与 {aggregate_kind} '
                'journal 不符')
    try:
        normalized = BlindCandidate.model_validate(
            candidate, strict=True).model_dump(by_alias=True, mode='json')
    except (TypeError, ValueError) as exc:
        raise ArtifactUnreadable(
            f'{label} primary_payload 不是当前 BlindCandidate：{exc}') from exc
    if normalized != candidate:
        raise ArtifactUnreadable(
            f'{label} primary_payload 不允许默认值补全或类型归一')
    return normalized, agent_runs.canonical_sha256(normalized)


def _design_aggregate_commit_path(ctx: Any) -> Path:
    return (paths.stage4_run_path(
        ctx.novel_dir, ctx.run_id, 'stage4.design')
        / DESIGN_AGGREGATE_COMMIT_FILE)


def _design_repair_aggregate_commit_path(ctx: Any, run_id: str | None = None) -> Path:
    return (paths.stage4_run_path(
        ctx.novel_dir, check_run_id(run_id or ctx.run_id), 'stage4.design')
        / DESIGN_REPAIR_AGGREGATE_COMMIT_FILE)


def _design_human_evolution_commit_path(
        ctx: Any, run_id: str | None = None,
) -> Path:
    return (paths.stage4_run_path(
        ctx.novel_dir, check_run_id(run_id or ctx.run_id), 'stage4.design')
        / DESIGN_HUMAN_EVOLUTION_COMMIT_FILE)


def _stage4_history_files(
        ctx: Any, pattern: str, *, reject_shadowed: bool = False,
) -> tuple[Path, ...]:
    """只扫描当前 service 运行根；旧书内运行目录由统一布局守卫拒绝。"""
    found: dict[str, Path] = {}
    for root in runtime_layout.stage_run_roots(ctx.novel_dir, 'stage4'):
        if not root.is_dir():
            continue
        for target in root.glob(pattern):
            key = target.relative_to(root).as_posix()
            prior = found.get(key)
            if prior is not None:
                if reject_shadowed:
                    raise ProvenanceRefused(
                        'Stage4 当前/legacy run 空间同时占用相同'
                        f'逻辑路径 {key!r}：{prior} / {target}')
                continue
            found[key] = target
    return tuple(found[key] for key in sorted(found))


def _require_unshadowed_stage4_recovery_journals(ctx: Any) -> None:
    """恢复 discovery 不得借 current-root 优先级隐藏 legacy 分叉。"""
    for pattern in (
            f'*/*/{agent_runs.COMMIT_FILE}',
            f'*/plan/chapters/ch*/{agent_runs.COMMIT_FILE}',
            f'*/design/{DESIGN_AGGREGATE_COMMIT_FILE}',
            f'*/plan/{PLAN_AGGREGATE_COMMIT_FILE}',
            f'*/design/{DESIGN_REPAIR_AGGREGATE_COMMIT_FILE}',
            f'*/design/{DESIGN_HUMAN_EVOLUTION_COMMIT_FILE}'):
        _stage4_history_files(ctx, pattern, reject_shadowed=True)


def _design_history_candidates(
        ctx: Any, run_id: str, filename: str,
) -> tuple[Path, ...]:
    checked = check_run_id(run_id)
    return tuple(
        root / checked / 'design' / filename
        for root in runtime_layout.stage_run_roots(ctx.novel_dir, 'stage4'))


def _design_aggregate_hash(payload: Mapping[str, Any]) -> str:
    return agent_runs.canonical_sha256({
        key: value for key, value in payload.items()
        if key not in ('prepared_at', 'committed_at', 'commit_sha256')
    })


def _design_repair_aggregate_hash(payload: Mapping[str, Any]) -> str:
    return agent_runs.canonical_sha256({
        key: value for key, value in payload.items()
        if key not in ('prepared_at', 'committed_at', 'commit_sha256')
    })


def _design_repair_recovery_identity_payload(
        record: Mapping[str, Any], *, source_journal_sha256: str,
        source_commit_sha256: str, target_file_sha256: str,
) -> dict[str, Any]:
    """不含 target run 的 source-stable 恢复身份。"""
    values = (
        source_journal_sha256, source_commit_sha256, target_file_sha256,
        record.get('parent_candidate_sha256'),
        record.get('repair_decision_sha256'),
        record.get('primary_payload_sha256'),
        record.get('finalization_sha256'),
    )
    if not all(agent_runs.is_sha256(value) for value in values):
        raise ProvenanceRefused('Stage4 repair recovery source/candidate SHA 不闭合')
    artifact_fingerprint = record.get('primary_payload_fingerprint')
    if (not isinstance(artifact_fingerprint, str)
            or len(artifact_fingerprint) != 10
            or any(char not in '0123456789abcdef'
                   for char in artifact_fingerprint)):
        raise ProvenanceRefused('Stage4 repair recovery artifact fingerprint 非法')
    return {
        'kind': 'stage4-repair-aggregate-recovery/v1',
        'sid': record.get('sid'),
        'node': 'stage4.design',
        'source_run_id': check_run_id(str(record.get('run_id') or '')),
        'producer_parent_run_id': check_run_id(
            str(record.get('producer_parent_run_id') or '')),
        'source_journal_sha256': source_journal_sha256,
        'source_commit_sha256': source_commit_sha256,
        # 业务 parent 与 CostLedger 的 outer-Agent parent 是两条正交边；若都叫
        # parent_run_id，audit stamp 会被业务字段覆盖，实际 owner 归因就丢了。
        'source_candidate_parent_run_id': check_run_id(
            str(record.get('parent_run_id') or '')),
        'parent_candidate_sha256': record.get('parent_candidate_sha256'),
        'repair_decision_sha256': record.get('repair_decision_sha256'),
        'target_chapters': list(record.get('target_chapters') or ()),
        'primary_payload_sha256': record.get('primary_payload_sha256'),
        'finalization_sha256': record.get('finalization_sha256'),
        'target_file_sha256': target_file_sha256,
        'delivery_kind': (
            record.get('finalization_payload') or {}).get('delivery_kind'),
        'artifact': STAGE4_BLIND_CANDIDATE,
        'artifact_fingerprint': artifact_fingerprint,
    }


def _design_repair_recovery_id(
        record: Mapping[str, Any], *, source_journal_sha256: str,
        source_commit_sha256: str, target_file_sha256: str,
) -> str:
    return agent_runs.canonical_sha256(
        _design_repair_recovery_identity_payload(
            record,
            source_journal_sha256=source_journal_sha256,
            source_commit_sha256=source_commit_sha256,
            target_file_sha256=target_file_sha256,
        ))


def _design_repair_recovery_attempt_id(
        *, recovery_id: str, target_run_id: str,
        target_parent_run_id: str,
) -> str:
    if not agent_runs.is_sha256(recovery_id):
        raise ProvenanceRefused('Stage4 repair recovery_id 非法')
    return agent_runs.canonical_sha256({
        'kind': 'stage4-repair-aggregate-recovery-attempt/v1',
        'stage4_repair_recovery_id': recovery_id,
        'target_run_id': check_run_id(target_run_id),
        'target_parent_run_id': check_run_id(target_parent_run_id),
    })


def _design_human_evolution_hash(payload: Mapping[str, Any]) -> str:
    return agent_runs.canonical_sha256({
        key: value for key, value in payload.items()
        if key not in ('prepared_at', 'committed_at', 'commit_sha256')
    })


def _is_attempt_id(value: object) -> bool:
    return (isinstance(value, str) and len(value) == 16
            and all(char in '0123456789abcdef' for char in value))


def _normalized_design_units(
        units: Sequence[DesignUnitRequest],
) -> tuple[DesignUnitRequest, ...]:
    if not isinstance(units, Sequence) or isinstance(units, (str, bytes)):
        raise ProvenanceRefused('Stage4 design units 必须是非空请求序列')
    by_key: dict[str, DesignUnitRequest] = {}
    for unit in units:
        if not isinstance(unit, DesignUnitRequest):
            raise ProvenanceRefused(
                'Stage4 design units 每项必须是 DesignUnitRequest')
        key = design_unit_key(unit.chapter)
        if key in by_key:
            raise ProvenanceRefused(f'Stage4 design unit={key} 重复')
        by_key[key] = unit
    strategy = by_key.get('strategy')
    chapters = sorted(
        unit.chapter for unit in by_key.values() if unit.chapter is not None)
    if strategy is None or not chapters:
        raise ProvenanceRefused(
            'Stage4 design aggregate 必须恰有一份 strategy 与至少一章')
    expected = list(range(1, chapters[-1] + 1))
    if chapters != expected:
        raise ProvenanceRefused(
            f'Stage4 design aggregate 章集必须闭合为 1..N，实得 {chapters}')
    return (strategy, *(by_key[f'ch{chapter:04d}'] for chapter in chapters))


def _checkpoint_revision_path(ctx: Any, chapter: int | None, revision: int) -> Path:
    return (paths.stage4_design_unit_checkpoint_dir(
        ctx.novel_dir, ctx.run_id, chapter) / f'rev-{revision:04d}.json')


def _validate_design_adoption_source(
        ctx: Any, target: Path, row: Mapping[str, Any],
) -> None:
    chapter = row.get('chapter')
    source_run_id = str(row.get('source_run_id') or ctx.run_id)
    if source_run_id != ctx.run_id:
        ctx, _record, _path = agent_runs._historical_run_context(
            ctx, design_unit_run_spec(chapter), source_run_id=source_run_id,
            ledger_source_path=ledger_module.ledger_path(ctx.novel_dir.parent))
    run_path = paths.stage4_design_unit_run_path(
        ctx.novel_dir, ctx.run_id,
        chapter if isinstance(chapter, int) else None) / agent_runs.RUN_FILE
    try:
        run_bytes = run_path.read_bytes()
    except OSError as exc:
        raise ProvenanceRefused(
            f'{target} 绑定的 unit run 不可读：{run_path}: {exc}') from exc
    if row.get('run_sha256') != agent_runs.canonical_sha256(run_bytes):
        raise ProvenanceRefused(f'{target} 绑定的 unit run.json 已漂移')
    run = _read_object(run_path, label='Stage4 design unit run')
    expected_run = {
        'run_id': check_run_id(ctx.run_id), 'sid': ctx.sid,
        'node': 'stage4.design', 'code': ctx.code,
        'chapter': chapter, 'purpose': row.get('purpose'),
        'session_owner': row.get('purpose'),
    }
    if {key: run.get(key) for key in expected_run} != expected_run:
        raise ProvenanceRefused(f'{run_path} 不属于当前 unit/run/sid/code')

    revision = int(row['checkpoint_revision'])
    checkpoint_path = _checkpoint_revision_path(ctx, chapter, revision)
    try:
        checkpoint_bytes = checkpoint_path.read_bytes()
    except OSError as exc:
        raise ProvenanceRefused(
            f'{target} 绑定的 unit checkpoint 不可读：'
            f'{checkpoint_path}: {exc}') from exc
    if row.get('checkpoint_file_sha256') != agent_runs.canonical_sha256(
            checkpoint_bytes):
        raise ProvenanceRefused(f'{checkpoint_path} 已被改写')
    checkpoint = _read_object(
        checkpoint_path, label='Stage4 design unit checkpoint')
    identity = row.get('request_identity')
    if not isinstance(identity, Mapping):
        raise ArtifactUnreadable(f'{target} adoption 缺 request_identity')
    model_identity = validate_checkpoint_model_identity(
        checkpoint.get('profile'),
        checkpoint_model=checkpoint.get('model'),
        actual_model=identity.get('model'),
        actual_is_ledger=True,
    )
    expected_checkpoint = {
        'run_id': check_run_id(ctx.run_id), 'sid': ctx.sid,
        'node': 'stage4.design', 'code': ctx.code,
        'chapter': chapter, 'purpose': row.get('purpose'),
        'run_sha256': row.get('run_sha256'),
        'revision': revision,
        'checkpoint_sha256': row.get('checkpoint_sha256'),
        'attempt_id': row.get('attempt_id'),
        'request_fingerprint': identity.get('request_fingerprint'),
        'model': model_identity.model,
        'ledger_response_sha256': identity.get('response_sha256'),
        'provider_session_id': row.get('provider_session_id'),
    }
    if {key: checkpoint.get(key) for key in expected_checkpoint} != expected_checkpoint:
        raise ProvenanceRefused(
            f'{checkpoint_path} 与 aggregate adoption 身份漂移')


def _validate_design_aggregate_state(
        ctx: Any, target: Path,
) -> dict[str, Any]:
    if ctx.node != 'stage4.design':
        raise ProvenanceRefused('design aggregate journal 只属于 stage4.design')
    record = _read_object(target, label='Stage4 design aggregate commit')
    fields = _DESIGN_AGGREGATE_FIELDS_V2
    if set(record) != fields:
        raise ArtifactUnreadable(f'{target} aggregate commit 字段漂移')
    if record.get('commit_sha256') != _design_aggregate_hash(record):
        raise ArtifactUnreadable(f'{target} aggregate commit SHA 不闭合')
    expected = {
        'run_id': check_run_id(ctx.run_id), 'sid': ctx.sid,
        'node': 'stage4.design', 'code': ctx.code,
        'primary_artifact': STAGE4_BLIND_CANDIDATE,
    }
    if {key: record.get(key) for key in expected} != expected:
        raise ProvenanceRefused(f'{target} 不属于当前 design run/sid/code')
    if (record.get('version'), record.get('kind')) != (
            _DESIGN_AGGREGATE_VERSION, _DESIGN_AGGREGATE_KIND):
        raise ArtifactUnreadable(f'{target} aggregate version/kind 漂移')
    if record.get('state') not in ('prepared', 'committed'):
        raise ArtifactUnreadable(f'{target} aggregate state 非法')

    primary = record.get('primary_payload')
    finalization = record.get('finalization_payload')
    if (not isinstance(primary, dict) or not isinstance(finalization, dict)
            or record.get('primary_payload_sha256')
            != agent_runs.canonical_sha256(primary)
            or record.get('primary_payload_fingerprint')
            != prov.projection_fingerprint(primary)
            or record.get('finalization_sha256')
            != agent_runs.canonical_sha256(finalization)):
        raise ArtifactUnreadable(f'{target} 封存的候选/finalization 不闭合')
    touched = record.get('touched_paths')
    baselines = record.get('baselines')
    if (touched != [STAGE4_BLIND_CANDIDATE]
            or not isinstance(baselines, dict)
            or set(baselines) != {STAGE4_BLIND_CANDIDATE}
            or (baselines[STAGE4_BLIND_CANDIDATE] is not None
                and not agent_runs.is_sha256(
                    baselines[STAGE4_BLIND_CANDIDATE]))):
        raise ArtifactUnreadable(f'{target} touched/baseline 不闭合')

    rows = record.get('adoptions')
    attempts = record.get('attempts')
    from novel.stages.stage4.design_rebuild import preserved_sources
    preserved = preserved_sources(ctx, record)
    if (not isinstance(rows, list) or len(rows) + len(preserved) < 2
            or not isinstance(attempts, list)):
        raise ArtifactUnreadable(f'{target} 缺 N+1 aggregate adoptions')
    seen_keys: set[str] = set()
    seen_purposes: set[str] = set()
    seen_attempts: set[str] = set()
    seen_sessions: set[str] = set()
    chapters: list[int] = []
    strategy_count = 0
    for row in rows:
        fields = _DESIGN_ADOPTION_FIELDS_V2
        if not isinstance(row, dict) or set(row) != fields:
            raise ArtifactUnreadable(f'{target} adoption 字段漂移')
        check_run_id(str(row['source_run_id']))
        chapter = row.get('chapter')
        if (chapter is not None
                and (not isinstance(chapter, int) or isinstance(chapter, bool)
                     or chapter < 1)):
            raise ArtifactUnreadable(f'{target} adoption 章号非法')
        key = design_unit_key(chapter)
        purpose = design_unit_purpose(chapter)
        attempt_id = row.get('attempt_id')
        session_id = row.get('provider_session_id')
        identity = row.get('request_identity')
        revision = row.get('checkpoint_revision')
        if (row.get('unit_key') != key or row.get('purpose') != purpose
                or key in seen_keys or purpose in seen_purposes
                or not _is_attempt_id(attempt_id)
                or attempt_id in seen_attempts
                or not isinstance(session_id, str) or not session_id
                or session_id in seen_sessions
                or not isinstance(revision, int) or isinstance(revision, bool)
                or revision < 1
                or not agent_runs.is_sha256(row.get('run_sha256'))
                or not agent_runs.is_sha256(row.get('checkpoint_sha256'))
                or not agent_runs.is_sha256(row.get('checkpoint_file_sha256'))
                or not isinstance(identity, dict)
                or set(identity) != _LEDGER_IDENTITY_FIELDS
                or identity.get('attempt_id') != attempt_id):
            raise ArtifactUnreadable(f'{target} adoption 身份不闭合')
        if chapter is None:
            strategy_count += 1
        else:
            chapters.append(chapter)
        seen_keys.add(key)
        seen_purposes.add(purpose)
        seen_attempts.add(str(attempt_id))
        seen_sessions.add(session_id)
        _validate_design_adoption_source(ctx, target, row)
    for source in preserved:
        key = design_unit_key(source.chapter)
        if key in seen_keys:
            raise ProvenanceRefused('Stage4A 保留来源与新采用重复覆盖单元')
        seen_keys.add(key)
        if source.chapter is None:
            strategy_count += 1
        else:
            chapters.append(source.chapter)
    if (strategy_count != 1
            or sorted(chapters) != list(range(1, len(chapters) + 1))
            or attempts != sorted(seen_attempts)):
        raise ArtifactUnreadable(
            f'{target} strategy/1..N/attempts 集合不闭合')

    landed = record.get('landed_payload_sha256s')
    post = record.get('post_file_sha256s')
    if record['state'] == 'prepared':
        if (not isinstance(record.get('prepared_at'), str)
                or record.get('committed_at') is not None
                or landed is not None or post is not None):
            raise ArtifactUnreadable(f'{target} prepared 字段不闭合')
    elif (not isinstance(record.get('prepared_at'), str)
          or not isinstance(record.get('committed_at'), str)
          or not isinstance(landed, dict)
          or set(landed) != {STAGE4_BLIND_CANDIDATE}
          or not agent_runs.is_sha256(landed[STAGE4_BLIND_CANDIDATE])
          or not isinstance(post, dict)
          or set(post) != {STAGE4_BLIND_CANDIDATE}
          or not agent_runs.is_sha256(post[STAGE4_BLIND_CANDIDATE])):
        raise ArtifactUnreadable(f'{target} committed 字段不闭合')
    _validate_design_aggregate_snapshot(record, target)
    if record['producer_parent_run_id'] != ctx.parent_run_id:
        raise ProvenanceRefused('Stage4A 聚合的原 producer parent 身份漂移')
    aggregate_runs._verify_recovery_receipt(ctx, design_aggregate_recovery_record(record))
    return record


def design_aggregate_recovery_record(record: Mapping[str, Any]) -> dict[str, Any]:
    """共用聚合审计只消费已封存的完整来源摘要，不重造模型来源。"""
    sources = record['adoptions']
    if record['finalization_payload'].get('rebuild') is not None:
        sources = {'adoptions': sources, 'rebuild': record['finalization_payload']['rebuild']}
    return {**record, 'sources_sha256': agent_runs.canonical_sha256(sources)}


def _validate_design_aggregate_snapshot(record: Mapping[str, Any], target: Path) -> None:
    from contracts.schema.stage4 import BlindCandidate
    from novel.store import ArtifactStore
    check_run_id(str(record.get('producer_parent_run_id') or ''))
    raw = record.get('primary_file_text')
    if (not isinstance(raw, str)
            or record.get('primary_file_sha256') != agent_runs.canonical_sha256(raw.encode('utf-8'))
            or (record['state'] == 'prepared' and record.get('recovery') is not None)):
        raise ArtifactUnreadable(f'{target} 原生产者/冻结字节不闭合')
    artifact = ArtifactStore.decode_snapshot(BlindCandidate, STAGE4_BLIND_CANDIDATE, raw.encode('utf-8'))
    if artifact.model_dump(by_alias=True, mode='json') != record['primary_payload']:
        raise ArtifactUnreadable(f'{target} 冻结字节与 typed 主体不等')


def design_aggregate_state(ctx: Any) -> dict[str, Any] | None:
    """返回当前 design aggregate journal；prepared 只授权补完尾窗。"""
    target = _design_aggregate_commit_path(ctx)
    return _validate_design_aggregate_state(ctx, target) if target.is_file() else None


def design_aggregate_prepared(ctx: Any) -> dict[str, Any] | None:
    record = design_aggregate_state(ctx)
    return record if record is not None and record.get('state') == 'prepared' else None


def design_aggregate_committed(ctx: Any) -> dict[str, Any] | None:
    record = design_aggregate_state(ctx)
    return record if record is not None and record.get('state') == 'committed' else None


def prepared(ctx: Any) -> dict[str, Any] | None:
    record = agent_runs.commit_state(ctx, _commit_spec(ctx.node))
    return record if record is not None and record.get('state') == 'prepared' else None


def committed(ctx: Any, spec: 'agent_runs.CommitBundleSpec | None' = None,
             ) -> dict[str, Any] | None:
    backend = aggregate_runs if isinstance(spec, aggregate_runs.AggregateCommitSpec) else agent_runs
    record = backend.commit_state(ctx, spec or _commit_spec(ctx.node))
    return record if record is not None and record.get('state') == 'committed' else None


def _validate_foreign_design_aggregate_envelope(
        target: Path, *, run_id: str, sid: str,
) -> dict[str, Any]:
    """跨 run 扫描只验不依赖当前 profile/ledger 的聚合提交外壳。"""
    record = _read_object(target, label='Stage4 foreign design aggregate')
    fields = _DESIGN_AGGREGATE_FIELDS_V2
    if (set(record) != fields
            or record.get('commit_sha256') != _design_aggregate_hash(record)
            or (record.get('version'), record.get('kind')) != (
                _DESIGN_AGGREGATE_VERSION, _DESIGN_AGGREGATE_KIND)
            or record.get('run_id') != check_run_id(run_id)
            or record.get('sid') != sid
            or record.get('node') != 'stage4.design'
            or record.get('state') not in ('prepared', 'committed')):
        raise ArtifactUnreadable(
            f'Stage4 foreign design aggregate {target} 身份/哈希不闭合')
    _validate_design_aggregate_snapshot(record, target)
    return record


def _validate_foreign_design_repair_aggregate_envelope(
        target: Path, *, run_id: str, sid: str) -> dict[str, Any]:
    record = _read_object(target, label='Stage4 foreign design repair aggregate')
    version_kind = (record.get('version'), record.get('kind'))
    expected_fields = _DESIGN_REPAIR_AGGREGATE_FIELDS
    if (set(record) != expected_fields
            or record.get('commit_sha256')
            != _design_repair_aggregate_hash(record)
            or version_kind != (
                _DESIGN_REPAIR_AGGREGATE_VERSION,
                _DESIGN_REPAIR_AGGREGATE_KIND)
            or record.get('run_id') != check_run_id(run_id)
            or record.get('sid') != sid
            or record.get('node') != 'stage4.design'
            or record.get('state') not in ('prepared', 'committed')):
        raise ArtifactUnreadable(
            f'Stage4 foreign design repair aggregate {target} 身份/哈希不闭合')
    return record


def validate_design_repair_aggregate_journal_envelope(
        target: Path, *, run_id: str, sid: str,
) -> dict[str, Any]:
    """只读验证 repair aggregate 的内部 envelope，供编排层绑定 external input。

    这个窄口只铸字段集、版本/kind、source run/sid/state 与 journal self-hash；
    完整 candidate/source/provenance 复验仍只属于 Stage4 producer。调用方还须
    自己绑定同一次执行声明里的原始文件字节，不能用逻辑 JSON 哈希代替 raw CAS。
    """
    return _validate_foreign_design_repair_aggregate_envelope(
        Path(target), run_id=run_id, sid=sid)


def _validate_foreign_design_human_evolution_envelope(
        target: Path, *, run_id: str, sid: str,
) -> dict[str, Any]:
    record = _read_object(target, label='Stage4 human evolution journal')
    if (set(record) != _DESIGN_HUMAN_EVOLUTION_FIELDS
            or record.get('commit_sha256')
            != _design_human_evolution_hash(record)
            or record.get('version') != _DESIGN_HUMAN_EVOLUTION_VERSION
            or record.get('kind') != _DESIGN_HUMAN_EVOLUTION_KIND
            or record.get('run_id') != check_run_id(run_id)
            or record.get('sid') != sid
            or record.get('node') != 'stage4.design'
            or record.get('state') not in ('prepared', 'committed')):
        raise ArtifactUnreadable(
            f'Stage4 human evolution {target} 身份/哈希不闭合')
    return record


def _require_design_historical_source_owner(
        ctx: Any, row: Mapping[str, Any],
) -> dict[str, Any]:
    """由 manifest 把 foreign checkpoint 与当时 target profile/request 闭合。"""
    source_ref = row.get('source_ref')
    if not agent_runs.is_sha256(source_ref):
        raise ProvenanceRefused('Stage4 historical source 缺 adoption manifest SHA')
    matches: list[Path] = []
    for candidate in _stage4_history_files(
            ctx, f'*/design/{DESIGN_HISTORICAL_ADOPTION_FILE}',
            reject_shadowed=True):
        try:
            if agent_runs.canonical_sha256(candidate.read_bytes()) == source_ref:
                matches.append(candidate)
        except OSError as exc:
            raise ArtifactUnreadable(
                f'Stage4 historical adoption manifest 不可读：{candidate}: {exc}') from exc
    if len(matches) != 1:
        raise ProvenanceRefused(
            'Stage4 historical source 必须唯一指向一本书内的 adoption manifest')
    target = matches[0]
    manifest = _read_object(target, label='Stage4A historical adoption owner')
    if (set(manifest) != _DESIGN_HISTORICAL_ADOPTION_FIELDS
            or manifest.get('version') != _DESIGN_HISTORICAL_ADOPTION_VERSION
            or manifest.get('kind') != _DESIGN_HISTORICAL_ADOPTION_KIND
            or manifest.get('state') != 'selected'
            or manifest.get('sid') != ctx.sid
            or manifest.get('node') != 'stage4.design'
            or manifest.get('manifest_sha256')
            != _design_historical_hash(manifest)):
        raise ArtifactUnreadable(f'{target} historical adoption owner 不闭合')
    chapter = row.get('chapter')
    source_run_id = row.get('source_run_id')
    if (not isinstance(chapter, int) or isinstance(chapter, bool)
            or source_run_id != manifest.get('source_run_id')
            or chapter not in (manifest.get('source_chapters') or [])):
        raise ProvenanceRefused(
            'Stage4 historical source 不属于 manifest 的 source run/chapter')
    sources = manifest.get('sources')
    if not isinstance(sources, list):
        raise ArtifactUnreadable(f'{target} historical adoption sources 非数组')
    source = next((item for item in sources
                   if isinstance(item, dict) and item.get('chapter') == chapter), None)
    if source is None or set(source) != _DESIGN_HISTORICAL_SOURCE_FIELDS:
        raise ProvenanceRefused('Stage4 historical source 缺 manifest source row')
    expected_from_row = {
        'source_run_file_sha256': row.get('run_sha256'),
        'source_checkpoint_revision': row.get('checkpoint_revision'),
        'source_checkpoint_sha256': row.get('checkpoint_sha256'),
        'source_checkpoint_file_sha256': row.get('checkpoint_file_sha256'),
        'source_attempt_id': row.get('attempt_id'),
        'source_response_sha256': row.get('response_payload_sha256'),
        'source_model': row.get('model'),
    }
    if {key: source.get(key) for key in expected_from_row} != expected_from_row:
        raise ProvenanceRefused(
            'Stage4 historical aggregate row 与 adoption source row 漂移')
    target_run_id = check_run_id(str(manifest.get('target_run_id') or ''))
    target_run_path = _historical_design_run_path(ctx, target_run_id, chapter)
    source_run_path = _historical_design_run_path(
        ctx, check_run_id(str(source_run_id)), chapter)
    try:
        target_bytes = target_run_path.read_bytes()
        source_bytes = source_run_path.read_bytes()
    except OSError as exc:
        raise ProvenanceRefused(
            f'Stage4 historical adoption target/source run 不可读：{exc}') from exc
    if (source.get('target_run_file_sha256')
            != agent_runs.canonical_sha256(target_bytes)
            or source.get('source_run_file_sha256')
            != agent_runs.canonical_sha256(source_bytes)):
        raise ProvenanceRefused('Stage4 historical adoption target/source run 字节漂移')
    target_run = _read_object(target_run_path, label='Stage4A adoption target run')
    source_run = _read_object(source_run_path, label='Stage4A adoption source run')
    if (target_run.get('identity_sha256')
            != source.get('target_run_identity_sha256')
            or source_run.get('identity_sha256')
            != source.get('source_run_identity_sha256')
            or target_run.get('code') != manifest.get('code')
            or source_run.get('code') != source.get('source_code')):
        raise ProvenanceRefused('Stage4 historical adoption run identity/code 漂移')
    comparable = (
        'sid', 'node', 'session_owner', 'chapter', 'purpose',
        'input_sha256', 'request_policy', 'request_policy_sha256', 'request',
    )
    if ({key: target_run.get(key) for key in comparable}
            != {key: source_run.get(key) for key in comparable}):
        raise ProvenanceRefused(
            'Stage4 historical adoption target/source profile/request 不等')
    return manifest


def _load_design_source(
        ctx: Any, row: Mapping[str, Any], *, default_run_id: str,
        default_source_kind: str, _verify_inline_owner: bool = True,
) -> DesignUnitSource:
    """从 aggregate row 指向的原 run/checkpoint 验证并取回响应。"""
    fields = set(row)
    if fields not in (
        _DESIGN_ADOPTION_FIELDS_V2,
        _DESIGN_REPAIR_SOURCE_FIELDS,
    ):
        raise ArtifactUnreadable('Stage4 design source 字段漂移')
    chapter = row.get('chapter')
    if chapter is not None and (
            not isinstance(chapter, int) or isinstance(chapter, bool) or chapter < 1):
        raise ArtifactUnreadable('Stage4 design source chapter 非法')
    source_run_id = check_run_id(str(row.get('source_run_id') or default_run_id))
    purpose = str(row.get('purpose') or '')
    if purpose != design_unit_purpose(chapter):
        raise ArtifactUnreadable('Stage4 design source purpose/chapter 不闭合')
    source_kind = str(row.get('source_kind') or default_source_kind)
    if source_kind not in ('inherited', 'repair', 'human'):
        raise ArtifactUnreadable('Stage4 design source_kind 非法')
    origin = str(row.get('origin') or 'agent')
    if origin not in (
            'agent', 'historical_checkpoint', 'human'):
        raise ArtifactUnreadable('Stage4 design source origin 非法')
    if origin == 'human':
        response_payload = row.get('response_payload')
        response_sha = row.get('response_payload_sha256')
        source_ref = row.get('source_ref')
        forbidden = (
            row.get('attempt_id'), row.get('run_sha256'),
            row.get('checkpoint_revision'), row.get('checkpoint_sha256'),
            row.get('checkpoint_file_sha256'), row.get('model'),
            row.get('accepted_turn_index'),
        )
        if (
            fields != _DESIGN_REPAIR_SOURCE_FIELDS
            or source_kind == 'repair'
            or not isinstance(response_payload, dict)
            or response_sha != agent_runs.canonical_sha256(response_payload)
            or not agent_runs.is_sha256(source_ref)
            or any(value is not None for value in forbidden)
        ):
            raise ProvenanceRefused(
                'Stage4 deterministic design source 身份/载荷不闭合')
        if _verify_inline_owner:
            owner_path = next((path for path in _design_history_candidates(
                ctx, source_run_id, DESIGN_HUMAN_EVOLUTION_COMMIT_FILE)
                if path.is_file()), _design_human_evolution_commit_path(
                    ctx, source_run_id))
            owner = _validate_foreign_committed_design_human_evolution(
                ctx, owner_path, run_id=source_run_id, sid=ctx.sid)
            if dict(row) not in owner['sources']:
                raise ProvenanceRefused(
                    'Stage4 deterministic source 不属于其 human evolution journal')
        return DesignUnitSource(
            chapter=chapter,
            source_run_id=source_run_id,
            purpose=purpose,
            source_kind=source_kind,
            response_payload=copy.deepcopy(response_payload),
            response_payload_sha256=str(response_sha),
            origin=origin,
            source_ref=str(source_ref),
        )
    if origin == 'historical_checkpoint':
        if (fields != _DESIGN_REPAIR_SOURCE_FIELDS
                or source_kind not in ('inherited', 'repair')
                or not agent_runs.is_sha256(row.get('source_ref'))
                or row.get('response_payload') is not None):
            raise ProvenanceRefused(
                'Stage4 historical checkpoint source 身份/manifest 不闭合')
        _require_design_historical_source_owner(ctx, row)
    elif source_kind == 'human' or (
            fields == _DESIGN_REPAIR_SOURCE_FIELDS
            and (row.get('source_ref') is not None
                 or row.get('response_payload') is not None)):
        raise ProvenanceRefused(
            'Stage4 agent source 不得夹带 human/inline 身份')
    run_path = _historical_design_run_path(ctx, source_run_id, chapter)
    checkpoint_revision = row.get('checkpoint_revision')
    if (not isinstance(checkpoint_revision, int)
            or isinstance(checkpoint_revision, bool) or checkpoint_revision < 1):
        raise ArtifactUnreadable('Stage4 design source checkpoint revision 非法')
    checkpoint_path = _historical_design_checkpoint_path(
        ctx, source_run_id, chapter, checkpoint_revision)
    try:
        run_bytes = run_path.read_bytes()
        checkpoint_bytes = checkpoint_path.read_bytes()
    except OSError as exc:
        raise ProvenanceRefused(
            f'Stage4 design source run/checkpoint 不可读：{exc}') from exc
    run_sha = agent_runs.canonical_sha256(run_bytes)
    checkpoint_file_sha = agent_runs.canonical_sha256(checkpoint_bytes)
    if (row.get('run_sha256') != run_sha
            or row.get('checkpoint_file_sha256') != checkpoint_file_sha):
        raise ProvenanceRefused('Stage4 design source run/checkpoint 字节已漂移')
    run = _read_object(run_path, label='Stage4 design source run')
    checkpoint = _read_object(
        checkpoint_path, label='Stage4 design source checkpoint')
    expected_run = {
        'run_id': source_run_id, 'sid': ctx.sid, 'node': 'stage4.design',
        'chapter': chapter, 'purpose': purpose, 'session_owner': purpose,
    }
    if {key: run.get(key) for key in expected_run} != expected_run:
        raise ProvenanceRefused('Stage4 design source run 不属于当前书/unit')
    if (checkpoint.get('checkpoint_sha256') != row.get('checkpoint_sha256')
            or checkpoint.get('checkpoint_sha256')
            != agent_runs._checkpoint_hash(checkpoint)
            or checkpoint.get('run_id') != source_run_id
            or checkpoint.get('sid') != ctx.sid
            or checkpoint.get('node') != 'stage4.design'
            or checkpoint.get('chapter') != chapter
            or checkpoint.get('purpose') != purpose
            or checkpoint.get('run_sha256') != run_sha
            or checkpoint.get('revision') != checkpoint_revision):
        raise ProvenanceRefused('Stage4 design source checkpoint 身份/哈希不闭合')
    attempt_id = str(checkpoint.get('attempt_id') or '')
    model = str(checkpoint.get('model') or '')
    turn = checkpoint.get('accepted_turn_index')
    response_payload = checkpoint.get('response_payload')
    response_sha = checkpoint.get('response_payload_sha256')
    if (not _is_attempt_id(attempt_id)
            or attempt_id != row.get('attempt_id')
            or not model
            or (row.get('model') is not None and row.get('model') != model)
            or not isinstance(turn, int) or isinstance(turn, bool) or turn < 1
            or (source_kind == 'repair' and turn > 2)
            or not isinstance(response_payload, dict)
            or response_sha != agent_runs.canonical_sha256(response_payload)
            or (row.get('response_payload_sha256') is not None
                and row.get('response_payload_sha256') != response_sha)):
        raise ProvenanceRefused('Stage4 design source response/attempt/turn 不闭合')
    return DesignUnitSource(
        chapter=chapter,
        source_run_id=source_run_id,
        purpose=purpose,
        attempt_id=attempt_id,
        source_kind=source_kind,
        run_sha256=run_sha,
        checkpoint_revision=checkpoint_revision,
        checkpoint_sha256=str(checkpoint['checkpoint_sha256']),
        checkpoint_file_sha256=checkpoint_file_sha,
        response_payload=copy.deepcopy(response_payload),
        response_payload_sha256=str(response_sha),
        origin=origin,
        source_ref=(str(row.get('source_ref'))
                    if origin == 'historical_checkpoint' else None),
        model=model,
        accepted_turn_index=turn,
    )


def _validate_design_human_evolution_contents(
        ctx: Any, target: Path, record: Mapping[str, Any], *, run_id: str,
) -> dict[str, Any]:
    """复验 human edit journal；inline source 不伪造 agent checkpoint。"""
    checked = check_run_id(run_id)
    primary = record.get('primary_payload')
    rows = record.get('sources')
    ordinal = record.get('output_ordinal')
    if (
        not isinstance(primary, dict)
        or record.get('primary_payload_sha256')
        != agent_runs.canonical_sha256(primary)
        or record.get('primary_payload_fingerprint')
        != prov.projection_fingerprint(primary)
        or not isinstance(ordinal, int) or isinstance(ordinal, bool) or ordinal < 1
        or not isinstance(rows, list) or len(rows) < 2
        or not agent_runs.is_sha256(record.get('parent_candidate_sha256'))
        or not agent_runs.is_sha256(record.get('review_decision_sha256'))
        or not agent_runs.is_sha256(record.get('chapter_subject_sha256'))
        or not agent_runs.is_sha256(record.get('strategy_epoch_sha256'))
        or not agent_runs.is_sha256(record.get('chapter_strategy_sha256'))
        or not agent_runs.is_sha256(record.get('constraint_set_sha256'))
        or not agent_runs.is_sha256(record.get('edit_request_sha256'))
        or not agent_runs.is_sha256(record.get('human_edit_sha256'))
        or not isinstance(record.get('review_token'), str)
        or not str(record['review_token']).startswith('s4er1_')
    ):
        raise ArtifactUnreadable(f'{target} human evolution 身份/载荷不闭合')
    normalized, _normalized_sha = _normalized_design_candidate_identity(
        primary, label=f'human evolution {target}', aggregate_kind='human')
    provenance = normalized['_provenance']
    expected_provenance = {
        'review_token': record['review_token'],
        'review_decision_sha256': record['review_decision_sha256'],
        'output_ordinal': ordinal,
        'chapter_subject_sha256': record['chapter_subject_sha256'],
        'strategy_epoch_sha256': record['strategy_epoch_sha256'],
        'chapter_strategy_sha256': record['chapter_strategy_sha256'],
        'constraint_set_sha256': record['constraint_set_sha256'],
        'edit_request_sha256': record['edit_request_sha256'],
        'parent_candidate_sha256': record['parent_candidate_sha256'],
    }
    if any(provenance.get(key) != value
           for key, value in expected_provenance.items()):
        raise ArtifactUnreadable(
            f'{target} human provenance 与 journal 内部身份不等')
    sources: list[DesignUnitSource] = []
    for row in rows:
        if not isinstance(row, dict) or set(row) != _DESIGN_REPAIR_SOURCE_FIELDS:
            raise ArtifactUnreadable(f'{target} human source 字段漂移')
        sources.append(_load_design_source(
            ctx, row,
            default_run_id=str(row.get('source_run_id') or ''),
            default_source_kind=str(row.get('source_kind') or ''),
            _verify_inline_owner=(
                str(row.get('source_run_id') or '') != checked),
        ))
    by = {source.chapter: source for source in sources}
    chapters = sorted(value for value in by if value is not None)
    target_source = by.get(ordinal)
    if (
        len(by) != len(sources)
        or None not in by
        or chapters != list(range(1, len(chapters) + 1))
        or [source.chapter for source in sources] != [None, *chapters]
        or target_source is None
        or target_source.source_kind != 'human'
        or target_source.origin != 'human'
        or target_source.source_run_id != checked
        or target_source.source_ref != record['human_edit_sha256']
        or any(source.source_kind != 'inherited'
               for source in sources if source.chapter != ordinal)
    ):
        raise ArtifactUnreadable(f'{target} human source 闭集/目标来源不闭合')
    expected_human_edit = agent_runs.canonical_sha256({
        'kind': 'stage4-design-human-source-v1',
        'run_id': checked,
        'review_token': record['review_token'],
        'review_decision_sha256': record['review_decision_sha256'],
        'output_ordinal': ordinal,
        'edit_request_sha256': record['edit_request_sha256'],
        'response_payload_sha256': target_source.response_payload_sha256,
    })
    if record['human_edit_sha256'] != expected_human_edit:
        raise ArtifactUnreadable(f'{target} human_edit_sha256 不闭合')
    if (record.get('touched_paths') != [STAGE4_BLIND_CANDIDATE]
            or not isinstance(record.get('baselines'), dict)
            or set(record['baselines']) != {STAGE4_BLIND_CANDIDATE}):
        raise ArtifactUnreadable(f'{target} human touched/baseline 不闭合')
    landed = record.get('landed_payload_sha256s')
    post = record.get('post_file_sha256s')
    if record['state'] == 'prepared':
        if (record.get('committed_at') is not None
                or landed is not None or post is not None):
            raise ArtifactUnreadable(f'{target} human prepared 尾窗不闭合')
    elif (not isinstance(record.get('committed_at'), str)
          or not isinstance(landed, dict)
          or not agent_runs.is_sha256(landed.get(STAGE4_BLIND_CANDIDATE))
          or not isinstance(post, dict)
          or not agent_runs.is_sha256(post.get(STAGE4_BLIND_CANDIDATE))):
        raise ArtifactUnreadable(f'{target} human committed 尾窗不闭合')
    return dict(record)


def _validate_foreign_committed_design_human_evolution(
        ctx: Any, target: Path, *, run_id: str, sid: str,
) -> dict[str, Any]:
    record = _validate_foreign_design_human_evolution_envelope(
        target, run_id=run_id, sid=sid)
    if record.get('state') != 'committed':
        raise ProvenanceRefused(f'{target} human evolution 尚未 committed')
    return _validate_design_human_evolution_contents(
        ctx, target, record, run_id=run_id)


_DESIGN_SOURCE_STACK: ContextVar[tuple[str, ...]] = ContextVar('design_source_stack', default=())


def load_committed_design_sources(
        ctx: Any, *, parent_run_id: str,
        parent_candidate_sha256: str,
) -> tuple[dict[str, Any], tuple[DesignUnitSource, ...]]:
    """递归来源只在本次读取中防环，不缓存跨调用或跨代码身份的判定。"""
    key = f'{ctx.novel_dir}:{check_run_id(parent_run_id)}'
    stack = _DESIGN_SOURCE_STACK.get()
    if key in stack:
        raise ProvenanceRefused('Stage4A 已提交来源链成环')
    token = _DESIGN_SOURCE_STACK.set((*stack, key))
    try:
        return _load_committed_design_sources(
            ctx, parent_run_id=parent_run_id,
            parent_candidate_sha256=parent_candidate_sha256)
    finally:
        _DESIGN_SOURCE_STACK.reset(token)


def _load_committed_design_sources(
        ctx: Any, *, parent_run_id: str,
        parent_candidate_sha256: str,
) -> tuple[dict[str, Any], tuple[DesignUnitSource, ...]]:
    """读已 committed 的父 aggregate，不复制旧 checkpoint。"""
    source_run_id = check_run_id(parent_run_id)
    by_kind = {
        'standard': _design_history_candidates(
            ctx, source_run_id, DESIGN_AGGREGATE_COMMIT_FILE),
        'repair': _design_history_candidates(
            ctx, source_run_id, DESIGN_REPAIR_AGGREGATE_COMMIT_FILE),
        'human': _design_history_candidates(
            ctx, source_run_id, DESIGN_HUMAN_EVOLUTION_COMMIT_FILE),
    }
    existing = [
        (kind, next((path for path in candidates if path.is_file()), None))
        for kind, candidates in by_kind.items()
    ]
    existing = [(kind, path) for kind, path in existing if path is not None]
    if len(existing) != 1:
        raise ProvenanceRefused(
            f'parent run={source_run_id} 必须恰有一份 design aggregate，'
            f'实得 {len(existing)}')
    aggregate_kind, target = existing[0]
    assert target is not None
    if aggregate_kind == 'standard':
        record = _validate_foreign_design_aggregate_envelope(
            target, run_id=source_run_id, sid=ctx.sid)
        _validate_committed_design_recovery(ctx, record)
        rows = record.get('adoptions')
        default_kind = 'inherited'
    elif aggregate_kind == 'repair':
        record = _validate_foreign_committed_design_repair_aggregate(
            ctx, target, run_id=source_run_id, sid=ctx.sid)
        rows = record.get('sources')
        default_kind = 'inherited'
    else:
        record = _validate_foreign_committed_design_human_evolution(
            ctx, target, run_id=source_run_id, sid=ctx.sid)
        rows = record.get('sources')
        default_kind = 'inherited'
    primary = record.get('primary_payload')
    if (record.get('state') != 'committed'
            or not isinstance(primary, dict)
            or record.get('primary_payload_sha256')
            != agent_runs.canonical_sha256(primary)
            or not isinstance(rows, list)):
        raise ProvenanceRefused(
            'parent design aggregate 未 committed 或原始候选 hash 不闭合')
    normalized_primary, normalized_sha = _normalized_design_candidate_identity(
        primary, label=f'parent design aggregate {target}',
        aggregate_kind=aggregate_kind)
    if normalized_sha != parent_candidate_sha256:
        raise ProvenanceRefused(
            'parent design aggregate 当前 schema 候选身份与 parent 引用不符')
    sources = tuple(_load_design_source(
        ctx, row, default_run_id=source_run_id,
        default_source_kind=default_kind)
        for row in rows if isinstance(row, Mapping))
    if len(sources) != len(rows):
        raise ArtifactUnreadable('parent design aggregate sources 含非 object')
    if aggregate_kind == 'standard':
        from novel.stages.stage4.design_rebuild import preserved_sources
        sources = (*sources, *preserved_sources(ctx, record))
        sources = tuple(sorted(sources, key=lambda row: -1 if row.chapter is None else row.chapter))
    by = {source.chapter: source for source in sources}
    chapters = sorted(chapter for chapter in by if chapter is not None)
    if (len(by) != len(sources) or None not in by
            or chapters != list(range(1, len(chapters) + 1))):
        raise ProvenanceRefused('parent design aggregate source 集不闭合')
    # 进入新 repair 后，从父链继承的无论当时是 repair 还是首轮，
    # 对新 aggregate 都是 inherited；新目标章会被当前 run 替换。
    inherited = tuple(DesignUnitSource(
        **{**source.__dict__, 'source_kind': 'inherited'}) for source in sources)
    return normalized_primary, inherited


def _committed_design_aggregate_for_run(
        ctx: Any, run_id: str,
) -> tuple[str, Path, dict[str, Any]]:
    """取一个 run 唯一 committed design aggregate 并核其内部 payload hash。"""
    checked = check_run_id(run_id)
    by_kind = {
        'standard': _design_history_candidates(
            ctx, checked, DESIGN_AGGREGATE_COMMIT_FILE),
        'repair': _design_history_candidates(
            ctx, checked, DESIGN_REPAIR_AGGREGATE_COMMIT_FILE),
        'human': _design_history_candidates(
            ctx, checked, DESIGN_HUMAN_EVOLUTION_COMMIT_FILE),
    }
    existing = [
        (kind, next((path for path in candidates if path.is_file()), None))
        for kind, candidates in by_kind.items()
    ]
    existing = [(kind, path) for kind, path in existing if path is not None]
    if len(existing) != 1:
        raise ProvenanceRefused(
            f'design run={checked} 必须恰有一份 aggregate，实得 '
            f'{len(existing)}')
    kind, target = existing[0]
    assert target is not None
    record = (
        _validate_foreign_design_aggregate_envelope(
            target, run_id=checked, sid=ctx.sid)
        if kind == 'standard' else
        _validate_foreign_committed_design_repair_aggregate(
            ctx, target, run_id=checked, sid=ctx.sid)
        if kind == 'repair' else
        _validate_foreign_committed_design_human_evolution(
            ctx, target, run_id=checked, sid=ctx.sid)
    )
    primary = record.get('primary_payload')
    if (record.get('state') != 'committed'
            or not isinstance(primary, dict)
            or record.get('primary_payload_sha256')
            != agent_runs.canonical_sha256(primary)):
        raise ProvenanceRefused(
            f'design run={checked} aggregate 未 committed 或候选 hash 不闭合')
    if kind == 'standard':
        _validate_committed_design_recovery(ctx, record)
    return kind, target, record


def _validate_committed_design_recovery(ctx: Any, record: Mapping[str, Any]) -> None:
    if record.get('version') == 2 and record.get('recovery') is not None:
        # 下游 lineage 消费也必须验证实际补提交者，不能只在恢复 CLI 上检查一次。
        source = copy.copy(ctx)
        source.run_id, source.parent_run_id = record['run_id'], record['producer_parent_run_id']
        source.ledger = ledger_module.CostLedger(
            path=ledger_module.ledger_path(ctx.novel_dir.parent), run_id=record['run_id'],
            parent_run_id=record['producer_parent_run_id'])
        aggregate_runs._verify_recovery_receipt(source, design_aggregate_recovery_record(record))
        _require_design_repair_recovery_target_identity_clean(
            ctx, record['recovery']['run_id'], stage4_design_only=True)


def _repair_ancestry_rounds(
        ctx: Any, parent_run_id: str,
) -> tuple[tuple[int, int], ...]:
    """沿 parent_run + parent candidate hash 链统计每章已提交修订轮。"""
    counts: dict[int, int] = {}
    seen: set[str] = set()
    current = check_run_id(parent_run_id)
    expected_primary_sha: str | None = None
    while True:
        if current in seen:
            raise ProvenanceRefused('design repair parent_run lineage 成环')
        seen.add(current)
        kind, target, record = _committed_design_aggregate_for_run(ctx, current)
        _normalized_primary, normalized_primary_sha = \
            _normalized_design_candidate_identity(
                record['primary_payload'], label=f'design aggregate {target}',
                aggregate_kind=kind)
        if (expected_primary_sha is not None
                and expected_primary_sha != normalized_primary_sha):
            raise ProvenanceRefused(
                f'{target} 不是子 repair journal 绑定的 parent candidate')
        if kind == 'standard':
            rebuild = record.get('finalization_payload', {}).get('rebuild')
            if rebuild is None:
                break
            parent = rebuild.get('parent') if isinstance(rebuild, dict) else None
            if (not isinstance(parent, dict)
                    or not isinstance(parent.get('run_id'), str)
                    or not agent_runs.is_sha256(parent.get('candidate_sha256'))):
                raise ArtifactUnreadable(f'{target} rebuild parent run/candidate 不闭合')
            current = check_run_id(parent['run_id'])
            expected_primary_sha = parent['candidate_sha256']
            continue
        if kind == 'human':
            parent = record.get('parent_run_id')
            parent_sha = record.get('parent_candidate_sha256')
            # 旧 whole-book 候选可由当前 human journal 的 deterministic
            # snapshot sources 收编；没有可追的 parent run 时到此自然止链。
            if parent is None:
                break
            if (not isinstance(parent, str)
                    or not agent_runs.is_sha256(parent_sha)):
                raise ArtifactUnreadable(
                    f'{target} human parent run/candidate 不闭合')
            current = check_run_id(parent)
            expected_primary_sha = str(parent_sha)
            continue
        targets = record.get('target_chapters')
        if (not isinstance(targets, list) or not targets
                or targets != sorted(set(targets))
                or any(not isinstance(chapter, int) or isinstance(chapter, bool)
                       or chapter < 1 for chapter in targets)):
            raise ArtifactUnreadable(f'{target} target_chapters 不闭合')
        for chapter in targets:
            counts[chapter] = counts.get(chapter, 0) + 1
        parent = record.get('parent_run_id')
        parent_sha = record.get('parent_candidate_sha256')
        if (not isinstance(parent, str)
                or not agent_runs.is_sha256(parent_sha)):
            raise ArtifactUnreadable(f'{target} parent run/candidate 不闭合')
        current = check_run_id(parent)
        expected_primary_sha = str(parent_sha)
    return tuple(sorted(counts.items()))


def inspect_design_repair_capability(
        ctx: Any, *, candidate_payload: Mapping[str, Any],
        candidate_file_sha256: str,
) -> DesignRepairCapability:
    """只读判定当前 A 是否有唯一可执行 split lineage。

    本函数专供审批 template/approve/resolve 与 core pre-wire 共用；
    来源未知或缺 aggregate 的候选不猜测，不授予单章模型修复权限。
    """
    unavailable = lambda code: DesignRepairCapability(
        repairable=False,
        reason_code=code,
        parent_run_id=None,
        chapter_repair_rounds=(),
    )
    try:
        if (not agent_runs.is_sha256(candidate_file_sha256)
                or agent_runs.file_sha256(ctx, STAGE4_BLIND_CANDIDATE)
                != candidate_file_sha256):
            return unavailable('checkpoint_lineage_unavailable')
        raw_candidate = copy.deepcopy(dict(candidate_payload))
        raw_provenance = raw_candidate.get('_provenance')
        if not raw_candidate or not isinstance(raw_provenance, Mapping):
            return unavailable('legacy_or_untracked_candidate')
        candidate, full_sha = _normalized_design_candidate_identity(
            raw_candidate, label='当前正式 blind candidate')
        from novel.stages.stage4.plot_design import candidate_subject_sha256
        business = {
            key: value for key, value in candidate.items()
            if key != '_provenance'
        }
        business_sha = candidate_subject_sha256(business)
        matches: list[str] = []
        for filename in (
                DESIGN_AGGREGATE_COMMIT_FILE,
                DESIGN_REPAIR_AGGREGATE_COMMIT_FILE,
                DESIGN_HUMAN_EVOLUTION_COMMIT_FILE):
            for target in _stage4_history_files(ctx, f'*/design/{filename}'):
                run_id = target.parent.parent.name
                kind, canonical_target, record = \
                    _committed_design_aggregate_for_run(ctx, run_id)
                if canonical_target != target:
                    # 同 run 同时有两份 aggregate 已在 helper 中关闭式拒绝。
                    raise ProvenanceRefused(
                        f'{target} 不是 run={run_id} 唯一 aggregate')
                expected_kind = {
                    DESIGN_AGGREGATE_COMMIT_FILE: 'standard',
                    DESIGN_REPAIR_AGGREGATE_COMMIT_FILE: 'repair',
                    DESIGN_HUMAN_EVOLUTION_COMMIT_FILE: 'human',
                }[filename]
                if kind != expected_kind:
                    raise ProvenanceRefused(f'{target} aggregate kind 与路径漂移')
                primary, primary_sha = _normalized_design_candidate_identity(
                    record['primary_payload'],
                    label=f'design aggregate {target}',
                    aggregate_kind=kind,
                )
                if primary_sha != full_sha or primary != candidate:
                    continue
                primary_business = {
                    key: value for key, value in primary.items()
                    if key != '_provenance'
                }
                if candidate_subject_sha256(primary_business) != business_sha:
                    raise ArtifactUnreadable(
                        f'{target} 完整候选匹配但业务 subject 不闭合')
                matches.append(check_run_id(run_id))
        if not matches:
            return unavailable('legacy_or_untracked_candidate')
        if len(matches) != 1:
            return unavailable('ambiguous_aggregate')
        parent_run_id = matches[0]
        parent, _sources = load_committed_design_sources(
            ctx,
            parent_run_id=parent_run_id,
            parent_candidate_sha256=full_sha,
        )
        if parent != candidate:
            return unavailable('checkpoint_lineage_unavailable')
        rounds = _repair_ancestry_rounds(ctx, parent_run_id)
        return DesignRepairCapability(
            repairable=True,
            reason_code='ready',
            parent_run_id=parent_run_id,
            chapter_repair_rounds=rounds,
        )
    except (ArtifactUnreadable, ProvenanceRefused, OSError, ValueError):
        return unavailable('checkpoint_lineage_unavailable')


def resolve_committed_design_sources(
        ctx: Any, *, candidate_payload: Mapping[str, Any],
        candidate_file_sha256: str,
) -> tuple[str, dict[str, Any], tuple[DesignUnitSource, ...]]:
    """由当前正式 A 反查唯一 committed aggregate 与原 checkpoints。

    首轮 ``book_strategy_chapters_v1`` 产物没有 ``run_id`` /
    ``unit_lineage``，但 aggregate journal 已封存完整候选与 N+1 个
    checkpoint 指针。这里同时核对正式文件 SHA、完整 payload
    与去 provenance 的业务 subject；0 个或多个匹配都关闭式拒绝，
    不靠目录新旧猜一个 parent run。
    """
    candidate, full_sha = _normalized_design_candidate_identity(
        candidate_payload, label='当前正式 blind candidate')
    capability = inspect_design_repair_capability(
        ctx,
        candidate_payload=candidate,
        candidate_file_sha256=candidate_file_sha256,
    )
    if not capability.repairable or capability.parent_run_id is None:
        raise ProvenanceRefused(
            'Stage4 repair 无可执行 split checkpoint lineage：'
            f'{capability.reason_code}')
    parent_run_id = capability.parent_run_id
    parent, sources = load_committed_design_sources(
        ctx,
        parent_run_id=parent_run_id,
        parent_candidate_sha256=full_sha,
    )
    if parent != candidate:
        raise ProvenanceRefused('Stage4 repair 解析的 parent payload 读取期漂移')
    return parent_run_id, parent, sources


def refuse_foreign_commit_reservations(
        ctx: Any, *, allowed_design_repair_run_id: str | None = None,
) -> None:
    """同书任一 foreign prepared 必须先补完，不能跨节点覆盖正式路径。"""
    # 只读调度会再次在正式 producer 锁内复验，不创建查询自己的锁文件。
    if not getattr(ctx, 'readonly', False):
        _require_ownership(ctx)
    allowed_repair = (
        check_run_id(allowed_design_repair_run_id)
        if allowed_design_repair_run_id is not None else None)
    current = {_commit_spec(ctx.node).commit_path(ctx).resolve()}
    if ctx.node == 'stage4.design':
        current.add(_design_aggregate_commit_path(ctx).resolve())
        current.add(_design_repair_aggregate_commit_path(ctx).resolve())
        current.add(_design_human_evolution_commit_path(ctx).resolve())
    for target in _stage4_history_files(ctx, f'*/*/{agent_runs.COMMIT_FILE}'):
        run_id = target.parent.parent.name
        node = 'stage4.' + target.parent.name
        if (run_id == ctx.run_id and node == ctx.node) \
                or target.resolve() in current:
            continue
        if node not in RECOVERY_POLICIES:
            raise ProvenanceRefused(
                f'Stage4 foreign commit 目录不是 design/plan：{target}')
        try:
            with (nullcontext() if getattr(ctx, 'readonly', False) else process_append_lock(target)):
                record = agent_runs.validate_foreign_commit_envelope(
                    target, label='Stage4 foreign commit', run_id=run_id,
                    sid=ctx.sid, node=node)
        except OSError as exc:
            raise ProvenanceRefused(
                f'Stage4 foreign commit journal {target} 不可验证：{exc}') from exc
        if record['state'] == 'prepared':
            raise ProvenanceRefused(
                f'Stage4 run={run_id}/{node} 仍持有 prepared 正式写盘尾窗；'
                '必须先用原 run 补完，不能由新 design/plan 覆盖')
    for target in _stage4_history_files(
            ctx, f'*/plan/chapters/ch*/{agent_runs.COMMIT_FILE}'):
        run_id = target.parents[3].name
        dirname = target.parent.name
        try:
            chapter = int(dirname.removeprefix('ch'))
        except ValueError as exc:
            raise ProvenanceRefused(
                f'Stage4 plan chapter commit 目录非法：{target}') from exc
        if (dirname != paths.stage4_plan_chapter_dirname(chapter)
                or target.parent.parent.name != 'chapters'
                or target.parent.parent.parent.name != 'plan'):
            raise ProvenanceRefused(
                f'Stage4 plan chapter commit 路径非 canonical：{target}')
        if run_id == ctx.run_id and ctx.node == 'stage4.plan':
            continue
        try:
            with (nullcontext() if getattr(ctx, 'readonly', False) else process_append_lock(target)):
                record = agent_runs.validate_foreign_commit_envelope(
                    target, label='Stage4 foreign plan chapter commit',
                    run_id=run_id, sid=ctx.sid, node='stage4.plan')
        except OSError as exc:
            raise ProvenanceRefused(
                f'Stage4 foreign plan chapter commit {target} '
                f'不可验证：{exc}') from exc
        if record['state'] == 'prepared':
            raise ProvenanceRefused(
                f'Stage4 run={run_id}/stage4.plan ch{chapter} 仍持有 '
                'prepared 正式写盘尾窗；必须先用原 run 补完')
    for target in _stage4_history_files(
            ctx, f'*/design/{DESIGN_AGGREGATE_COMMIT_FILE}'):
        run_id = target.parent.parent.name
        if run_id == ctx.run_id or target.resolve() in current:
            continue
        try:
            with (nullcontext() if getattr(ctx, 'readonly', False) else process_append_lock(target)):
                record = _validate_foreign_design_aggregate_envelope(
                    target, run_id=run_id, sid=ctx.sid)
        except OSError as exc:
            raise ProvenanceRefused(
                f'Stage4 foreign design aggregate {target} 不可验证：{exc}') from exc
        if record['state'] == 'prepared':
            raise ProvenanceRefused(
                f'Stage4 run={run_id}/stage4.design 仍持有 prepared '
                'aggregate 写盘尾窗；必须先用原 run 补完')
    for target in _stage4_history_files(ctx, f'*/plan/{PLAN_AGGREGATE_COMMIT_FILE}'):
        run_id = target.parent.parent.name
        if run_id == ctx.run_id and ctx.node == 'stage4.plan':
            continue
        record = aggregate_runs.validate_envelope(
            target, run_id=run_id, sid=ctx.sid, node='stage4.plan')
        if record['state'] == 'prepared':
            raise ProvenanceRefused(
                f'Stage4 run={run_id}/stage4.plan 仍持有 prepared aggregate；必须先补尾')
    current_repair = _design_repair_aggregate_commit_path(ctx).resolve()
    for target in _stage4_history_files(
            ctx, f'*/design/{DESIGN_REPAIR_AGGREGATE_COMMIT_FILE}'):
        run_id = target.parent.parent.name
        if (run_id == ctx.run_id or run_id == allowed_repair
                or target.resolve() == current_repair):
            continue
        try:
            with (nullcontext() if getattr(ctx, 'readonly', False) else process_append_lock(target)):
                record = _validate_foreign_design_repair_aggregate_envelope(
                    target, run_id=run_id, sid=ctx.sid)
        except OSError as exc:
            raise ProvenanceRefused(
                f'Stage4 foreign design repair aggregate 不可验证：{exc}') from exc
        if record['state'] == 'prepared':
            raise ProvenanceRefused(
                f'Stage4 run={run_id}/stage4.design 仍持有 prepared '
                'repair aggregate 写盘尾窗；必须先补完')
    current_human = _design_human_evolution_commit_path(ctx).resolve()
    for target in _stage4_history_files(
            ctx, f'*/design/{DESIGN_HUMAN_EVOLUTION_COMMIT_FILE}'):
        run_id = target.parent.parent.name
        if run_id == ctx.run_id or target.resolve() == current_human:
            continue
        try:
            with (nullcontext() if getattr(ctx, 'readonly', False) else process_append_lock(target)):
                record = _validate_foreign_design_human_evolution_envelope(
                    target, run_id=run_id, sid=ctx.sid)
        except OSError as exc:
            raise ProvenanceRefused(
                f'Stage4 foreign human evolution 不可验证：{exc}') from exc
        if record['state'] == 'prepared':
            raise ProvenanceRefused(
                f'Stage4 run={run_id}/stage4.design 仍持有 prepared '
                'human evolution 写盘尾窗；必须先补完')
    # foreign prepared 只守“不能踩过活写手”；已落成的
    # recovered repair 还必须在每个正式下游入口重验 source
    # adoption + canonical + 实际 committer attempt。只匹配当前正式
    # candidate，不会因陈旧历史 repair 的 audit 已不可用就阻断。
    _require_current_design_repair_aggregate_lineage(
        ctx, allowed_prepared_run_id=allowed_repair)


def _require_current_design_repair_aggregate_lineage(
        ctx: Any, *, allowed_prepared_run_id: str | None = None,
) -> None:
    """若正式 A 是 repair/replan，重验唯一匹配的 committed lineage。

    不遍历重验所有历史 repair 的 durable audit：陈旧候选早已不是
    当前下游产地。匹配同时绑定 typed payload 与正式文件 raw SHA，
    避免只靠业务 JSON 相等把另一份落盘实例当成父产地。
    """
    if not getattr(ctx, 'readonly', False):
        _require_ownership(ctx)
    candidate_path = Path(ctx.novel_dir) / STAGE4_BLIND_CANDIDATE
    try:
        raw = candidate_path.read_bytes()
    except FileNotFoundError:
        return
    except OSError as exc:
        raise ProvenanceRefused(
            f'Stage4 当前正式 candidate 不可读：{exc}') from exc
    try:
        encoded = json.loads(raw.decode('utf-8'))
        value = decode_artifact(STAGE4_BLIND_CANDIDATE, encoded).payload
    except (UnicodeError, json.JSONDecodeError, ValueError) as exc:
        raise ArtifactUnreadable(
            f'Stage4 当前正式 candidate 不是可读 JSON：{exc}') from exc
    if not isinstance(value, dict):
        raise ArtifactUnreadable('Stage4 当前正式 candidate 不是 JSON object')
    provenance = value.get('_provenance')
    if (not isinstance(provenance, dict)
            or provenance.get('architecture') not in {
                'book_strategy_targeted_repair_v1',
                'book_strategy_replan_v1',
            }):
        return
    try:
        candidate_model = BlindCandidate.model_validate(value, strict=True)
    except (TypeError, ValueError) as exc:
        raise ArtifactUnreadable(
            f'Stage4 当前正式 repair candidate 不符合当前 schema：'
            f'{exc}') from exc
    # 盘上 provenance 会由 ``to_disk`` 保留“null / 未记录”三态；
    # journal 封存的是 typed model dump。先按 ArtifactStore 同样的模型
    # 读取还原 typed payload，再与 journal 逐字比较。
    candidate, candidate_sha = _normalized_design_candidate_identity(
        candidate_model.model_dump(by_alias=True, mode='json'),
        label='Stage4 当前正式 repair candidate',
        aggregate_kind='repair')
    candidate_file_sha = agent_runs.canonical_sha256(raw)

    matches: list[tuple[Path, str, dict[str, Any]]] = []
    for target in _stage4_history_files(
            ctx, f'*/design/{DESIGN_REPAIR_AGGREGATE_COMMIT_FILE}'):
        run_id = target.parent.parent.name
        try:
            with (nullcontext() if getattr(ctx, 'readonly', False) else process_append_lock(target)):
                record = _validate_foreign_design_repair_aggregate_envelope(
                    target, run_id=run_id, sid=ctx.sid)
        except OSError as exc:
            raise ProvenanceRefused(
                f'Stage4 repair aggregate {target} 不可验证：{exc}') from exc
        if (record.get('state') != 'committed'
                and not (record.get('state') == 'prepared'
                         and run_id == allowed_prepared_run_id)):
            continue
        primary = record.get('primary_payload')
        post = record.get('post_file_sha256s')
        if not isinstance(primary, dict):
            continue
        # v1/v2 repair journal 的 lineage 尚无 ``origin/source_ref``；正式
        # artifact 经当前 schema 读取会显式补出这两个默认键。匹配时沿用
        # 现役白名单 normalization，不能把仍合法的旧产地误判为 0 match。
        # 不匹配的陈旧历史 journal 即使 payload 已不可归一，也不应阻断
        # 当前正式候选；一旦匹配，下面的完整 validator 仍会严验原始 hash。
        try:
            normalized_primary, normalized_sha = \
                _normalized_design_candidate_identity(
                    primary, label=f'Stage4 repair aggregate {target}',
                    aggregate_kind='repair')
        except ArtifactUnreadable:
            continue
        if normalized_sha != candidate_sha or normalized_primary != candidate:
            continue
        if record.get('state') == 'prepared':
            from novel.store import ArtifactStore
            expected_file_sha = agent_runs.canonical_sha256(
                ArtifactStore(ctx)._render(  # noqa: SLF001
                    STAGE4_BLIND_CANDIDATE,
                    BlindCandidate.model_validate(
                        normalized_primary, strict=True)))
            if expected_file_sha != candidate_file_sha:
                continue
        elif (not isinstance(post, dict)
              or post.get(STAGE4_BLIND_CANDIDATE) != candidate_file_sha):
            continue
        matches.append((target, check_run_id(run_id), record))
    if len(matches) != 1:
        raise ProvenanceRefused(
            'Stage4 当前正式 repair candidate 必须唯一对应 '
            f'committed aggregate，实得 {len(matches)}')
    target, run_id, record = matches[0]
    if record.get('state') == 'prepared':
        # 只有 recovery 内部显式传入的 source run 可在“sealed
        # target 已写、journal 尚未 mark”这一条硬杀窗穿过。仍重验
        # 整份 contents，且匹配已经绑定 exact payload + raw file SHA。
        _validate_design_repair_aggregate_contents(
            ctx, target, record, run_id=run_id)
        return
    _validate_foreign_committed_design_repair_aggregate(
        ctx, target, run_id=run_id, sid=ctx.sid)


def _design_adoption_row(
        ctx: Any, unit: DesignUnitRequest,
        *, historical: Any | None = None,
) -> dict[str, Any]:
    if historical is not None:
        from novel.llm import design_adoption
        checked = design_adoption.restore(ctx, unit, historical.context.run_id)
        if checked.checkpoint != historical.checkpoint or checked.run_sha256 != historical.run_sha256:
            raise ProvenanceRefused('Stage4A 已选择的来源在提交前漂移')
        ctx = checked.context
    restored = restore_design_unit_checkpoint(ctx, unit)
    if restored is None:
        raise ProvenanceRefused(
            f'{design_unit_purpose(unit.chapter)}: aggregate commit 前缺 '
            'accepted checkpoint')
    attempt_id = str(restored.acceptance['attempt_id'])
    identity = ctx.ledger.request_identity_for_attempt(attempt_id)
    validate_checkpoint_model_identity(
        restored.acceptance.get('profile'),
        checkpoint_model=restored.acceptance.get('model'),
        actual_model=identity.get('model'),
        actual_is_ledger=True,
    )
    if (set(identity) != _LEDGER_IDENTITY_FIELDS
            or identity.get('attempt_id') != attempt_id
            or identity.get('request_fingerprint')
            != restored.acceptance.get('request_fingerprint')
            or identity.get('response_sha256')
            != restored.acceptance.get('ledger_response_sha256')):
        raise ProvenanceRefused(
            f'{design_unit_purpose(unit.chapter)}: checkpoint/ledger 请求身份不闭合')
    run_path = paths.stage4_design_unit_run_path(
        ctx.novel_dir, ctx.run_id, unit.chapter) / agent_runs.RUN_FILE
    checkpoint_path = _checkpoint_revision_path(
        ctx, unit.chapter, restored.revision)
    try:
        run_sha = agent_runs.canonical_sha256(run_path.read_bytes())
        checkpoint_file_sha = agent_runs.canonical_sha256(
            checkpoint_path.read_bytes())
    except OSError as exc:
        raise ProvenanceRefused(
            f'{design_unit_purpose(unit.chapter)}: 读不了已验证 run/checkpoint：'
            f'{exc}') from exc
    return {
        'unit_key': design_unit_key(unit.chapter),
        'source_run_id': ctx.run_id,
        'purpose': design_unit_purpose(unit.chapter),
        'chapter': unit.chapter,
        'attempt_id': attempt_id,
        'run_sha256': run_sha,
        'checkpoint_revision': restored.revision,
        'checkpoint_sha256': restored.checkpoint_sha256,
        'checkpoint_file_sha256': checkpoint_file_sha,
        'provider_session_id': restored.acceptance['provider_session_id'],
        'request_identity': identity,
    }


def prepare_design_aggregate_commit(
        ctx: Any, *, units: Sequence[DesignUnitRequest],
        primary_payload: Mapping[str, Any],
        finalization_payload: Mapping[str, Any],
        touched_paths: Sequence[str] = (STAGE4_BLIND_CANDIDATE,),
        historical: Mapping[int | None, Any] | None = None,
        preserved: Sequence[DesignUnitSource] = (),
        primary_file_text: str | None = None,
) -> dict[str, Any]:
    """冻结 strategy + ch1..N 的最新 accepted revisions 与整书候选。

    这一步必须发生在正式 artifact 写盘前；journal 内封存候选
    payload，即使进程死在 prepare→formal 尾窗，重启也能零 wire 收尾。
    """
    _require_ownership(ctx)
    if ctx.node != 'stage4.design':
        raise ProvenanceRefused('design aggregate commit 只属于 stage4.design')
    normalized_units = _normalized_design_units(units)
    touched = sorted(str(value) for value in touched_paths)
    if touched != [STAGE4_BLIND_CANDIDATE]:
        raise ProvenanceRefused(
            f'design aggregate touched_paths 必须恰为 '
            f'[{STAGE4_BLIND_CANDIDATE!r}]')
    refuse_foreign_commit_reservations(ctx)
    single_session_roots = (
        paths.stage4_run_path(
            ctx.novel_dir, ctx.run_id, 'stage4.design'),
    )
    if any((root / agent_runs.RUN_FILE).is_file()
           or (root / agent_runs.COMMIT_FILE).is_file()
           for root in single_session_roots):
        raise ProvenanceRefused(
            f'{single_session_roots} 已绑定旧 single-session design run/commit；'
            '同一 run 不能再建 aggregate 第二权威源')

    historical = dict(historical or {})
    preserved_by = {row.chapter: row for row in preserved}
    rows = [_design_adoption_row(ctx, unit, historical=historical.get(unit.chapter))
            for unit in normalized_units if unit.chapter not in preserved_by]
    attempts = sorted(str(row['attempt_id']) for row in rows)
    sessions = [str(row['provider_session_id']) for row in rows]
    if len(attempts) != len(set(attempts)):
        raise ProvenanceRefused(
            'Stage4 design N+1 units 不能采纳同一 attempt')
    if len(sessions) != len(set(sessions)):
        raise ProvenanceRefused(
            'Stage4 design strategy/各章必须使用互异 provider session')
    primary = copy.deepcopy(dict(primary_payload))
    finalization = copy.deepcopy(dict(finalization_payload))
    from novel.stages.stage4.design_rebuild import preserved_sources
    if tuple(preserved) != preserved_sources(ctx, {'primary_payload': primary, 'finalization_payload': finalization}):
        raise ProvenanceRefused('Stage4A 保留来源不等于完整父节点与实际依赖证明')
    if len(preserved_by) != len(preserved) or set(preserved_by) & set(historical):
        raise ProvenanceRefused('Stage4A 保留来源重复或与普通历史采用相交')
    # 在写任何 journal 前先证明两份快照可 canonical JSON 化。
    primary_sha = agent_runs.canonical_sha256(primary)
    finalization_sha = agent_runs.canonical_sha256(finalization)
    if primary_file_text is None:
        from contracts.schema.stage4 import BlindCandidate
        from novel.store import ArtifactStore
        primary_file_text = ArtifactStore(ctx)._render(
            STAGE4_BLIND_CANDIDATE, BlindCandidate.model_validate(primary)).decode('utf-8')
    payload: dict[str, Any] = {
        'version': _DESIGN_AGGREGATE_VERSION,
        'kind': _DESIGN_AGGREGATE_KIND,
        'state': 'prepared', 'prepared_at': _now(), 'committed_at': None,
        'run_id': check_run_id(ctx.run_id), 'sid': ctx.sid,
        'node': 'stage4.design', 'code': ctx.code,
        'producer_parent_run_id': check_run_id(ctx.parent_run_id), 'recovery': None,
        'primary_file_text': primary_file_text,
        'primary_file_sha256': agent_runs.canonical_sha256(primary_file_text.encode('utf-8')),
        'primary_artifact': STAGE4_BLIND_CANDIDATE,
        'primary_payload_sha256': primary_sha,
        'primary_payload_fingerprint': prov.projection_fingerprint(primary),
        'primary_payload': primary,
        'finalization_sha256': finalization_sha,
        'finalization_payload': finalization,
        'touched_paths': touched,
        'baselines': {
            STAGE4_BLIND_CANDIDATE:
                agent_runs.file_sha256(ctx, STAGE4_BLIND_CANDIDATE),
        },
        'attempts': attempts,
        'adoptions': rows,
        'landed_payload_sha256s': None,
        'post_file_sha256s': None,
    }
    payload['commit_sha256'] = _design_aggregate_hash(payload)
    target = _design_aggregate_commit_path(ctx)
    with _DESIGN_AGGREGATE_LOCK:
        with process_append_lock(target):
            if target.is_file():
                current = _validate_design_aggregate_state(ctx, target)
                ignored = {
                    'state', 'prepared_at', 'committed_at', 'commit_sha256',
                    'baselines', 'landed_payload_sha256s', 'post_file_sha256s',
                }
                left = {
                    key: value for key, value in current.items()
                    if key not in ignored
                }
                right = {
                    key: value for key, value in payload.items()
                    if key not in ignored
                }
                if left != right:
                    raise ProvenanceRefused(
                        f'{target} 已绑定另一份候选/unit adoption '
                        '集合；拒绝覆盖')
                return current
            _atomic_json(target, payload)
    return payload


def _design_source_row(source: DesignUnitSource) -> dict[str, Any]:
    return {
        'unit_key': design_unit_key(source.chapter),
        'chapter': source.chapter,
        'source_run_id': source.source_run_id,
        'purpose': source.purpose,
        'attempt_id': source.attempt_id,
        'source_kind': source.source_kind,
        'run_sha256': source.run_sha256,
        'checkpoint_revision': source.checkpoint_revision,
        'checkpoint_sha256': source.checkpoint_sha256,
        'checkpoint_file_sha256': source.checkpoint_file_sha256,
        'response_payload_sha256': source.response_payload_sha256,
        'model': source.model,
        'accepted_turn_index': source.accepted_turn_index,
        'origin': source.origin,
        'source_ref': source.source_ref,
        'response_payload': (
            copy.deepcopy(source.response_payload)
            if source.origin == 'human' else None
        ),
    }


def current_design_unit_source(
        ctx: Any, unit: DesignUnitRequest, *, source_kind: str = 'repair',
) -> DesignUnitSource:
    """把当前 run 的已验证 checkpoint 投影成 aggregate lineage。"""
    row = _design_adoption_row(ctx, unit)
    source = _load_design_source(
        ctx,
        row,
        default_run_id=ctx.run_id,
        default_source_kind=source_kind,
    )
    # adoption 行与 repair source 行是两套关闭字段集，不能靠拼两个键把
    # 前者冒充后者。checkpoint 经 adoption 契约完整验证后，lineage kind
    # 只是 aggregate 的派生标记，不改变模型来源或任何 run/checkpoint 身份。
    return DesignUnitSource(**{**source.__dict__, 'source_kind': source_kind})


def _validate_design_human_evolution_state(
        ctx: Any, target: Path,
) -> dict[str, Any]:
    record = _validate_foreign_design_human_evolution_envelope(
        target, run_id=ctx.run_id, sid=ctx.sid)
    if ctx.node != 'stage4.design' or record.get('code') != ctx.code:
        raise ProvenanceRefused(f'{target} 不属于当前 human edit run/code')
    return _validate_design_human_evolution_contents(
        ctx, target, record, run_id=ctx.run_id)


def design_human_evolution_state(ctx: Any) -> dict[str, Any] | None:
    target = _design_human_evolution_commit_path(ctx)
    return _validate_design_human_evolution_state(
        ctx, target) if target.is_file() else None


def prepare_design_human_evolution_commit(
        ctx: Any, *, parent_run_id: str | None,
        parent_candidate_sha256: str, review_token: str,
        review_decision_sha256: str, output_ordinal: int,
        chapter_subject_sha256: str, strategy_epoch_sha256: str,
        chapter_strategy_sha256: str, constraint_set_sha256: str,
        edit_request_sha256: str, human_response_payload: Mapping[str, Any],
        parent_sources: Sequence[DesignUnitSource],
        primary_payload: Mapping[str, Any],
) -> dict[str, Any]:
    """先封 deterministic source 与目标候选，再开放 human write_round。"""
    _require_ownership(ctx)
    checked_parent = (
        check_run_id(parent_run_id) if parent_run_id is not None else None)
    hashes = (
        parent_candidate_sha256, review_decision_sha256,
        chapter_subject_sha256, strategy_epoch_sha256,
        chapter_strategy_sha256, constraint_set_sha256,
        edit_request_sha256,
    )
    if (not all(agent_runs.is_sha256(value) for value in hashes)
            or not isinstance(review_token, str)
            or not review_token.startswith('s4er1_')
            or not isinstance(output_ordinal, int)
            or isinstance(output_ordinal, bool) or output_ordinal < 1):
        raise ProvenanceRefused('human evolution 请求身份不闭合')
    if checked_parent is not None:
        parent_kind, parent_target, parent_record = \
            _committed_design_aggregate_for_run(ctx, checked_parent)
        _parent, normalized_parent_sha = _normalized_design_candidate_identity(
            parent_record['primary_payload'],
            label=f'human evolution parent {parent_target}',
            aggregate_kind=parent_kind,
        )
        if normalized_parent_sha != parent_candidate_sha256:
            raise ProvenanceRefused(
                'human evolution parent run 与 parent candidate 不匹配')
    by = {source.chapter: source for source in parent_sources}
    chapters = sorted(value for value in by if value is not None)
    if (len(by) != len(parent_sources) or None not in by
            or chapters != list(range(1, len(chapters) + 1))
            or output_ordinal not in by):
        raise ProvenanceRefused('human evolution parent sources 必须恰覆盖 strategy + 1..N')
    inherited = {
        chapter: DesignUnitSource(**{
            **source.__dict__, 'source_kind': 'inherited',
        }) for chapter, source in by.items()
    }
    response = copy.deepcopy(dict(human_response_payload))
    response_sha = agent_runs.canonical_sha256(response)
    human_edit_sha = agent_runs.canonical_sha256({
        'kind': 'stage4-design-human-source-v1',
        'run_id': check_run_id(ctx.run_id),
        'review_token': review_token,
        'review_decision_sha256': review_decision_sha256,
        'output_ordinal': output_ordinal,
        'edit_request_sha256': edit_request_sha256,
        'response_payload_sha256': response_sha,
    })
    inherited[output_ordinal] = DesignUnitSource(
        chapter=output_ordinal,
        source_run_id=check_run_id(ctx.run_id),
        purpose=design_unit_purpose(output_ordinal),
        source_kind='human',
        response_payload=response,
        response_payload_sha256=response_sha,
        origin='human',
        source_ref=human_edit_sha,
    )
    ordered = tuple(inherited[key] for key in (None, *chapters))
    primary = copy.deepcopy(dict(primary_payload))
    # 先用当前 schema 证明 journal 封存的确实是 human candidate。
    _normalized_design_candidate_identity(
        primary, label='human evolution primary', aggregate_kind='human')
    payload: dict[str, Any] = {
        'version': _DESIGN_HUMAN_EVOLUTION_VERSION,
        'kind': _DESIGN_HUMAN_EVOLUTION_KIND,
        'state': 'prepared', 'prepared_at': _now(), 'committed_at': None,
        'run_id': check_run_id(ctx.run_id), 'sid': ctx.sid,
        'node': 'stage4.design', 'code': ctx.code,
        'parent_run_id': checked_parent,
        'parent_candidate_sha256': parent_candidate_sha256,
        'review_token': review_token,
        'review_decision_sha256': review_decision_sha256,
        'output_ordinal': output_ordinal,
        'chapter_subject_sha256': chapter_subject_sha256,
        'strategy_epoch_sha256': strategy_epoch_sha256,
        'chapter_strategy_sha256': chapter_strategy_sha256,
        'constraint_set_sha256': constraint_set_sha256,
        'edit_request_sha256': edit_request_sha256,
        'human_edit_sha256': human_edit_sha,
        'primary_artifact': STAGE4_BLIND_CANDIDATE,
        'primary_payload_sha256': agent_runs.canonical_sha256(primary),
        'primary_payload_fingerprint': prov.projection_fingerprint(primary),
        'primary_payload': primary,
        'sources': [_design_source_row(source) for source in ordered],
        'touched_paths': [STAGE4_BLIND_CANDIDATE],
        'baselines': {STAGE4_BLIND_CANDIDATE:
                      agent_runs.file_sha256(ctx, STAGE4_BLIND_CANDIDATE)},
        'landed_payload_sha256s': None,
        'post_file_sha256s': None,
    }
    payload['commit_sha256'] = _design_human_evolution_hash(payload)
    target = _design_human_evolution_commit_path(ctx)
    with _DESIGN_AGGREGATE_LOCK, process_append_lock(target):
        if target.is_file():
            current = _validate_design_human_evolution_state(ctx, target)
            ignored = {
                'state', 'prepared_at', 'committed_at', 'commit_sha256',
                'baselines', 'landed_payload_sha256s', 'post_file_sha256s',
            }
            if ({key: value for key, value in current.items() if key not in ignored}
                    != {key: value for key, value in payload.items()
                        if key not in ignored}):
                raise ProvenanceRefused(
                    'human evolution journal 已绑定另一份 edit/candidate')
            return current
        _atomic_json(target, payload)
    return payload


def design_human_evolution_target_state(
        ctx: Any, record: Mapping[str, Any], *, expected_file_sha256: str,
) -> str:
    _require_ownership(ctx)
    current = _validate_design_human_evolution_state(
        ctx, _design_human_evolution_commit_path(ctx))
    if dict(record) != current or not agent_runs.is_sha256(expected_file_sha256):
        raise ProvenanceRefused('human evolution target 身份/SHA 不闭合')
    actual = agent_runs.file_sha256(ctx, STAGE4_BLIND_CANDIDATE)
    if actual == expected_file_sha256:
        return 'expected'
    if actual == current['baselines'][STAGE4_BLIND_CANDIDATE]:
        return 'baseline'
    raise ProvenanceRefused('blind candidate 在 human prepared 尾窗发生第三方漂移')


def mark_design_human_evolution_committed(
        ctx: Any, *, primary_payload: Mapping[str, Any],
) -> Path:
    _require_ownership(ctx)
    target = _design_human_evolution_commit_path(ctx)
    with _DESIGN_AGGREGATE_LOCK, process_append_lock(target):
        current = _validate_design_human_evolution_state(ctx, target)
        primary = dict(primary_payload)
        if current['primary_payload_sha256'] != agent_runs.canonical_sha256(primary):
            raise ProvenanceRefused('human evolution committed 候选漂移')
        post = agent_runs.file_sha256(ctx, STAGE4_BLIND_CANDIDATE)
        if post is None:
            raise ProvenanceRefused('human evolution committed 前候选未落盘')
        landed = {STAGE4_BLIND_CANDIDATE:
                  agent_runs.canonical_sha256(primary)}
        post_rows = {STAGE4_BLIND_CANDIDATE: post}
        if current['state'] == 'committed':
            if (current.get('landed_payload_sha256s') != landed
                    or current.get('post_file_sha256s') != post_rows):
                raise ProvenanceRefused('human evolution 已 committed 结果漂移')
            return target
        updated = dict(current)
        updated.update({
            'state': 'committed', 'committed_at': _now(),
            'landed_payload_sha256s': landed,
            'post_file_sha256s': post_rows,
        })
        updated['commit_sha256'] = _design_human_evolution_hash(updated)
        _atomic_json(target, updated)
    return target


def _validate_design_repair_aggregate_contents(
        ctx: Any, target: Path, record: Mapping[str, Any], *, run_id: str,
) -> dict[str, Any]:
    """复验 current/foreign repair journal 共用的完整 lineage 业务面。"""
    checked_run_id = check_run_id(run_id)
    primary = record.get('primary_payload')
    finalization = record.get('finalization_payload')
    if (not isinstance(primary, dict) or not isinstance(finalization, dict)
            or record.get('primary_payload_sha256')
            != agent_runs.canonical_sha256(primary)
            or record.get('primary_payload_fingerprint')
            != prov.projection_fingerprint(primary)
            or record.get('finalization_sha256')
            != agent_runs.canonical_sha256(finalization)
            or not agent_runs.is_sha256(record.get('parent_candidate_sha256'))
            or not agent_runs.is_sha256(record.get('repair_decision_sha256'))):
        raise ArtifactUnreadable(f'{target} repair 候选/finalization/hash 不闭合')
    normalized_primary, _normalized_primary_sha = \
        _normalized_design_candidate_identity(
        primary, label=f'design repair aggregate {target}',
        aggregate_kind='repair')
    primary_provenance = normalized_primary['_provenance']
    strategy_replan = (
        primary_provenance.get('architecture') == 'book_strategy_replan_v1')
    if (primary_provenance['parent_candidate_sha256']
            != record.get('parent_candidate_sha256')
            or primary_provenance['repair_decision_sha256']
            != record.get('repair_decision_sha256')):
        raise ArtifactUnreadable(
            f'{target} repair primary provenance 与 journal parent/decision 不等')
    targets = record.get('target_chapters')
    rows = record.get('sources')
    if (not isinstance(targets, list) or not targets
            or targets != sorted(set(targets))
            or any(not isinstance(chapter, int) or isinstance(chapter, bool)
                   or chapter < 1 for chapter in targets)
            or not isinstance(rows, list) or len(rows) < 2):
        raise ArtifactUnreadable(f'{target} repair targets/sources 不闭合')
    sources: list[DesignUnitSource] = []
    source_fields = _DESIGN_REPAIR_SOURCE_FIELDS
    for row in rows:
        if not isinstance(row, dict) or set(row) != source_fields:
            raise ArtifactUnreadable(f'{target} repair source 字段漂移')
        source = _load_design_source(
            ctx, row, default_run_id=str(row.get('source_run_id') or ''),
            default_source_kind=str(row.get('source_kind') or ''))
        if _design_source_row(source) != row:
            raise ProvenanceRefused(
                f'{target} repair source inline payload 与原 checkpoint 漂移')
        sources.append(source)
    by = {source.chapter: source for source in sources}
    chapters = sorted(chapter for chapter in by if chapter is not None)
    if (len(by) != len(sources) or None not in by
            or chapters != list(range(1, len(chapters) + 1))
            or any(chapter not in by for chapter in targets)
            or [source.chapter for source in sources] != [None, *chapters]):
        raise ArtifactUnreadable(f'{target} repair source unit 集不闭合')
    for chapter, source in by.items():
        expected_kind = (
            'repair' if chapter in targets
            or (strategy_replan and chapter is None)
            else 'inherited')
        if (source.source_kind != expected_kind
                or (expected_kind == 'repair' and source.origin == 'agent'
                    and source.source_run_id != checked_run_id)):
            raise ProvenanceRefused(
                f'{target} ch{chapter} source_kind/source_run 与 targets 漂移')
    expected_unit_lineage = [{
        'chapter_ordinal': source.chapter,
        'source_run_id': source.source_run_id,
        'purpose': source.purpose,
        'attempt_id': source.attempt_id,
        'source_kind': source.source_kind,
        'origin': source.origin,
        'source_ref': source.source_ref,
    } for source in sources]
    if primary_provenance['unit_lineage'] != expected_unit_lineage:
        raise ArtifactUnreadable(
            f'{target} repair primary unit_lineage 与 journal sources 不等')
    attempts = [{
        'source_run_id': source.source_run_id,
        'purpose': source.purpose,
        'attempt_id': source.attempt_id,
    } for source in sources if source.origin in ('agent', 'historical_checkpoint')]
    if record.get('attempts') != attempts:
        raise ArtifactUnreadable(f'{target} repair attempts/source lineage 不等')
    if (record.get('touched_paths') != [STAGE4_BLIND_CANDIDATE]
            or not isinstance(record.get('baselines'), dict)
            or set(record['baselines']) != {STAGE4_BLIND_CANDIDATE}
            or not agent_runs.is_sha256(
                record['baselines'].get(STAGE4_BLIND_CANDIDATE))):
        raise ArtifactUnreadable(f'{target} repair touched/baseline 不闭合')

    if record.get('version') == _DESIGN_REPAIR_AGGREGATE_VERSION:
        architecture = primary_provenance.get('architecture')
        try:
            producer_parent = check_run_id(
                str(record.get('producer_parent_run_id') or ''))
        except ValueError as exc:
            raise ArtifactUnreadable(
                f'{target} producer_parent_run_id 非法：{exc}') from exc
        raw_scope = record.get('repair_scope')
        try:
            scope = DesignRepairScope.model_validate(raw_scope, strict=True)
        except ValidationError as exc:
            raise ArtifactUnreadable(
                f'{target} repair_scope 不符合现役 typed 契约：{exc}') from exc
        if (not isinstance(raw_scope, dict)
                or scope.model_dump(mode='json') != raw_scope
                or scope.candidate_sha256
                != record['baselines'][STAGE4_BLIND_CANDIDATE]
                or scope.decision_sha256
                != record.get('repair_decision_sha256')
                or scope.strategy_replan != strategy_replan):
            raise ProvenanceRefused(
                f'{target} repair_scope 与 baseline/decision/architecture 漂移')
        expected_fields = (
            _DESIGN_REPLAN_FINALIZATION_FIELDS
            if strategy_replan
            else _DESIGN_TARGETED_REPAIR_FINALIZATION_FIELDS)
        expected_delivery = (
            'design-strategy-replan'
            if strategy_replan else 'design-targeted-repair')
        if (set(finalization) != expected_fields
                or finalization.get('delivery_kind') != expected_delivery
                or finalization.get('architecture') != architecture
                or not agent_runs.is_sha256(finalization.get('input_sha256'))
                or finalization.get('parent_run_id')
                != check_run_id(str(record.get('parent_run_id') or ''))
                or finalization.get('parent_candidate_file_sha256')
                != record['baselines'][STAGE4_BLIND_CANDIDATE]
                or finalization.get('parent_candidate_payload_sha256')
                != record.get('parent_candidate_sha256')
                or finalization.get('repair_decision_sha256')
                != record.get('repair_decision_sha256')
                or finalization.get('target_chapters') != targets
                or finalization.get('candidate_sha256')
                != record.get('primary_payload_sha256')):
            raise ProvenanceRefused(
                f'{target} repair finalization 未闭合 parent/candidate/decision/targets')
        if strategy_replan:
            strategy_input = finalization.get('strategy_input_sha256')
            strategy_run = _historical_design_run_path(
                ctx, by[None].source_run_id, None)
            strategy_record = _read_object(
                strategy_run, label='Stage4 replan strategy source run')
            if (not agent_runs.is_sha256(strategy_input)
                    or strategy_record.get('input_sha256') != strategy_input
                    or not agent_runs.is_sha256(finalization.get('feedback_sha256'))
                    or scope.replan_feedback is None
                    or finalization.get('feedback_sha256')
                    != scope.replan_feedback.constraint_set_sha256):
                raise ProvenanceRefused(
                    f'{target} replan finalization.strategy_input 未绑定 strategy run')
        else:
            target_subjects = finalization.get('target_subjects')
            scope_subjects = [
                [row.output_ordinal, row.subject_sha256]
                for row in scope.chapters]
            if (not agent_runs.is_sha256(finalization.get('truth_sha256'))
                    or finalization.get('truth_sha256')
                    != primary_provenance.get('truth_sha256')
                    or not agent_runs.is_sha256(
                        finalization.get('parent_aggregate_subject_sha256'))
                    or finalization.get('parent_aggregate_subject_sha256')
                    != scope.aggregate_subject_sha256
                    or not isinstance(target_subjects, list)
                    or [row[0] for row in target_subjects
                        if isinstance(row, list) and len(row) == 2] != targets
                    or len(target_subjects) != len(targets)
                    or target_subjects != scope_subjects
                    or any(not isinstance(row, list) or len(row) != 2
                           or not agent_runs.is_sha256(row[1])
                           for row in target_subjects)):
                raise ProvenanceRefused(
                    f'{target} targeted finalization subject/truth 不闭合')
            lineage = [{
                'chapter': source.chapter, 'source_run': source.source_run_id,
                'purpose': source.purpose, 'attempt': source.attempt_id,
                'source_kind': source.source_kind, 'origin': source.origin,
                'source_ref': source.source_ref,
            } for source in sources]
            if finalization.get('source_lineage_sha256') \
                    != agent_runs.canonical_sha256(lineage):
                raise ProvenanceRefused(
                    f'{target} targeted finalization source lineage 不闭合')

    landed = record.get('landed_payload_sha256s')
    post = record.get('post_file_sha256s')
    recovery = record.get('recovery')
    if record['state'] == 'prepared':
        if (record.get('committed_at') is not None
                or landed is not None or post is not None
                or recovery is not None):
            raise ArtifactUnreadable(f'{target} repair prepared 尾窗字段不闭合')
    elif (not isinstance(record.get('committed_at'), str)
          or not isinstance(landed, dict)
          or not agent_runs.is_sha256(landed.get(STAGE4_BLIND_CANDIDATE))
          or not isinstance(post, dict)
          or not agent_runs.is_sha256(post.get(STAGE4_BLIND_CANDIDATE))):
        raise ArtifactUnreadable(f'{target} repair committed 字段不闭合')
    if (record.get('version') == _DESIGN_REPAIR_AGGREGATE_VERSION
            and recovery is not None):
        # source_commit_sha256 是 prepared journal 的内部 hash；committed
        # 更新必然改变 record.commit_sha256，不能拿后者覆盖前者。
        if (not isinstance(recovery, dict)
                or set(recovery) != _DESIGN_REPAIR_RECOVERY_FIELDS
                or recovery.get('target_file_sha256')
                != post.get(STAGE4_BLIND_CANDIDATE)):
            raise ArtifactUnreadable(
                f'{target} repair recovery committed lineage 不闭合')
        recovery_id = _design_repair_recovery_id(
            record,
            source_journal_sha256=str(
                recovery.get('source_journal_sha256') or ''),
            source_commit_sha256=str(
                recovery.get('source_commit_sha256') or ''),
            target_file_sha256=str(recovery.get('target_file_sha256') or ''),
        )
        target_run_id = check_run_id(str(recovery.get('target_run_id') or ''))
        target_parent_run_id = check_run_id(str(
            recovery.get('target_parent_run_id') or ''))
        if (target_run_id == checked_run_id
                or recovery.get('stage4_repair_recovery_id') != recovery_id
                or recovery.get('stage4_repair_recovery_attempt_id')
                != _design_repair_recovery_attempt_id(
                    recovery_id=recovery_id,
                    target_run_id=target_run_id,
                    target_parent_run_id=target_parent_run_id)):
            raise ProvenanceRefused(
                f'{target} repair recovery target/source lineage 不闭合')
    return dict(record)


def _validate_foreign_committed_design_repair_aggregate(
        ctx: Any, target: Path, *, run_id: str, sid: str,
) -> dict[str, Any]:
    """foreign repair 不只验 envelope；轮次必须来自完整可追溯 lineage。"""
    record = _validate_foreign_design_repair_aggregate_envelope(
        target, run_id=run_id, sid=sid)
    if record.get('state') != 'committed':
        raise ProvenanceRefused(f'{target} foreign repair aggregate 尚未 committed')
    checked = _validate_design_repair_aggregate_contents(
        ctx, target, record, run_id=run_id)
    recovery = checked.get('recovery')
    if recovery is not None:
        _require_committed_design_repair_recovery_audit(
            ctx, target=target, record=checked)
    return checked


def _validate_design_repair_aggregate_state(
        ctx: Any, target: Path) -> dict[str, Any]:
    record = _validate_foreign_design_repair_aggregate_envelope(
        target, run_id=ctx.run_id, sid=ctx.sid)
    if ctx.node != 'stage4.design' or record.get('code') != ctx.code:
        raise ProvenanceRefused(f'{target} 不属于当前 design repair run/code')
    return _validate_design_repair_aggregate_contents(
        ctx, target, record, run_id=ctx.run_id)


def design_repair_aggregate_state(ctx: Any) -> dict[str, Any] | None:
    target = _design_repair_aggregate_commit_path(ctx)
    return _validate_design_repair_aggregate_state(
        ctx, target) if target.is_file() else None


def _design_repair_recovery_target(
        ctx: Any, target: Path, *, run_id: str,
) -> tuple[DesignRepairAggregateRecovery, dict[str, Any]]:
    """在 ownership 内把一份 prepared journal 解析成唯一恢复坐标。"""
    _require_ownership(ctx)
    with process_append_lock(target):
        try:
            raw = target.read_bytes()
        except OSError as exc:
            raise ArtifactUnreadable(
                f'Stage4 repair recovery journal {target} 不可读：{exc}') from exc
        def reject_constant(value: str) -> None:
            raise ValueError(f'JSON 不接受 {value}')

        def reject_duplicate(
                pairs: list[tuple[str, object]],
        ) -> dict[str, object]:
            decoded: dict[str, object] = {}
            for key, value in pairs:
                if key in decoded:
                    raise ValueError(f'JSON object 含重复键 {key!r}')
                decoded[key] = value
            return decoded

        try:
            strict_record = json.loads(
                raw.decode('utf-8'), parse_constant=reject_constant,
                object_pairs_hook=reject_duplicate)
        except (UnicodeError, ValueError, json.JSONDecodeError) as exc:
            raise ArtifactUnreadable(
                f'Stage4 repair recovery journal {target} 非严格 JSON：{exc}') from exc
        if not isinstance(strict_record, dict):
            raise ArtifactUnreadable(
                f'Stage4 repair recovery journal {target} 不是 JSON object')
        record = _validate_foreign_design_repair_aggregate_envelope(
            target, run_id=run_id, sid=ctx.sid)
        if record != strict_record:
            raise ArtifactUnreadable(
                f'Stage4 repair recovery journal {target} 读取期间漂移')
        record = _validate_design_repair_aggregate_contents(
            ctx, target, record, run_id=run_id)
    if record['state'] != 'prepared':
        raise ProvenanceRefused(f'{target} 已不是 prepared repair aggregate')
    if record.get('version') != _DESIGN_REPAIR_AGGREGATE_VERSION:
        raise ProvenanceRefused(
            f'{target} 是旧 repair journal；没有现役 recovery lineage 槽位，'
            '不得由新 run 猜测补尾')
    if record.get('code') != ctx.code:
        raise ProvenanceRefused(
            'Stage4 prepared repair aggregate 的 source code 与当前 execution '
            'epoch 不同；不得改写 sealed provenance')
    source_run_id = check_run_id(run_id)
    if source_run_id == ctx.run_id:
        raise ProvenanceRefused(
            'Stage4 repair aggregate 跨 run 恢复必须使用新的 target run')
    producer_parent = check_run_id(
        str(record.get('producer_parent_run_id') or ''))
    # journal 独立封存 source producer→outer owner；没有这条边，删掉
    # AgentRun 账就能把 Agent epoch 误降级为 direct CLI。
    design_repair_recovery_source_parent(
        ctx, source_run_id, expected_parent_run_id=producer_parent)
    baseline = record['baselines'].get(STAGE4_BLIND_CANDIDATE)
    if not agent_runs.is_sha256(baseline):
        raise ArtifactUnreadable(
            f'{target} repair baseline 必须是既有正式候选的完整 file SHA')
    from novel.store import ArtifactStore
    artifact = BlindCandidate.model_validate(record['primary_payload'], strict=True)
    expected_file_sha = agent_runs.canonical_sha256(
        ArtifactStore(ctx)._render(STAGE4_BLIND_CANDIDATE, artifact))
    actual_file_sha = agent_runs.file_sha256(ctx, STAGE4_BLIND_CANDIDATE)
    if actual_file_sha == baseline:
        target_state = 'baseline'
    elif actual_file_sha == expected_file_sha:
        target_state = 'expected'
    else:
        raise ProvenanceRefused(
            'blind candidate 在 foreign repair prepared 尾窗中既不是基线，'
            '也不是 sealed target；拒绝覆盖第三方漂移')
    assert actual_file_sha is not None
    return DesignRepairAggregateRecovery(
        source_run_id=source_run_id,
        producer_parent_run_id=producer_parent,
        journal_path=target.resolve(),
        journal_sha256=agent_runs.canonical_sha256(raw),
        commit_sha256=str(record['commit_sha256']),
        target_state=target_state,
        candidate_file_sha256=actual_file_sha,
        expected_file_sha256=expected_file_sha,
    ), record


def _prepared_design_repair_aggregate_recovery_locked(
        ctx: Any,
) -> tuple[DesignRepairAggregateRecovery, dict[str, Any]] | None:
    """扫描全书 repair journals；任何歧义都关闭，不按时间猜 winner。"""
    _require_ownership(ctx)
    _require_unshadowed_stage4_recovery_journals(ctx)
    prepared: list[tuple[Path, str]] = []
    for target in _stage4_history_files(
            ctx, f'*/design/{DESIGN_REPAIR_AGGREGATE_COMMIT_FILE}',
            reject_shadowed=True):
        run_id = target.parent.parent.name
        with process_append_lock(target):
            record = _validate_foreign_design_repair_aggregate_envelope(
                target, run_id=run_id, sid=ctx.sid)
        if record['state'] == 'prepared':
            prepared.append((target, run_id))
    if not prepared:
        return None
    if len(prepared) != 1:
        raise ProvenanceRefused(
            'Stage4 同书存在多份 prepared repair aggregate；'
            f'不能按时间或目录名猜恢复目标：{[run_id for _path, run_id in prepared]}')
    target, run_id = prepared[0]
    found = _design_repair_recovery_target(ctx, target, run_id=run_id)
    _refuse_design_repair_recovery_foreign_prepared_locked(
        ctx, allowed_repair_journal=target)
    return found


def _refuse_design_repair_recovery_foreign_prepared_locked(
        ctx: Any, *, allowed_repair_journal: Path,
) -> None:
    """恢复专口：除 exact source repair 外，任何 Stage4 prepared 都关闭。

    普通 producer 的 foreign guard 会跳过 ``ctx.run_id`` 和当前节点的
    journal；那是为了让同 run 补自己的尾窗。跨 run recovery 恰好相反：
    target run 必须是一张干净的 FREE 执行面，不能同时持有 generic、首轮
    aggregate、另一份 repair 或 human-evolution 的写盘权。这里扫描物理
    current/legacy 空间，不给 target-current 留豁免。
    """
    _require_ownership(ctx)
    allowed = allowed_repair_journal.resolve()

    for target in _stage4_history_files(
            ctx, f'*/*/{agent_runs.COMMIT_FILE}', reject_shadowed=True):
        run_id = target.parent.parent.name
        node = 'stage4.' + target.parent.name
        if node not in RECOVERY_POLICIES:
            raise ProvenanceRefused(
                f'Stage4 recovery foreign commit 目录不是 design/plan：{target}')
        try:
            with process_append_lock(target):
                record = agent_runs.validate_foreign_commit_envelope(
                    target, label='Stage4 recovery foreign commit',
                    run_id=run_id, sid=ctx.sid, node=node)
        except OSError as exc:
            raise ProvenanceRefused(
                f'Stage4 recovery foreign commit {target} 不可验证：{exc}') from exc
        if record['state'] == 'prepared':
            raise ProvenanceRefused(
                f'Stage4 recovery 发现另一份 prepared generic commit：{target}')

    for target in _stage4_history_files(
            ctx, f'*/plan/chapters/ch*/{agent_runs.COMMIT_FILE}',
            reject_shadowed=True):
        run_id = target.parents[3].name
        dirname = target.parent.name
        try:
            chapter = int(dirname.removeprefix('ch'))
        except ValueError as exc:
            raise ProvenanceRefused(
                f'Stage4 recovery plan chapter commit 目录非法：'
                f'{target}') from exc
        if (dirname != paths.stage4_plan_chapter_dirname(chapter)
                or target.parent.parent.name != 'chapters'
                or target.parent.parent.parent.name != 'plan'):
            raise ProvenanceRefused(
                f'Stage4 recovery plan chapter commit 路径非 '
                f'canonical：{target}')
        try:
            with process_append_lock(target):
                record = agent_runs.validate_foreign_commit_envelope(
                    target, label='Stage4 recovery plan chapter commit',
                    run_id=run_id, sid=ctx.sid, node='stage4.plan')
        except OSError as exc:
            raise ProvenanceRefused(
                f'Stage4 recovery plan chapter commit {target} '
                f'不可验证：{exc}') from exc
        if record['state'] == 'prepared':
            raise ProvenanceRefused(
                f'Stage4 recovery 发现另一份 prepared plan '
                f'chapter ch{chapter}：{target}')

    for target in _stage4_history_files(
            ctx, f'*/plan/{PLAN_AGGREGATE_COMMIT_FILE}', reject_shadowed=True):
        record = aggregate_runs.validate_envelope(
            target, run_id=target.parent.parent.name, sid=ctx.sid, node='stage4.plan')
        if record['state'] == 'prepared':
            raise ProvenanceRefused(f'Stage4 recovery 发现另一份 prepared plan aggregate：{target}')

    validators = (
        (DESIGN_AGGREGATE_COMMIT_FILE,
         _validate_foreign_design_aggregate_envelope,
         'design aggregate'),
        (DESIGN_REPAIR_AGGREGATE_COMMIT_FILE,
         _validate_foreign_design_repair_aggregate_envelope,
         'repair aggregate'),
        (DESIGN_HUMAN_EVOLUTION_COMMIT_FILE,
         _validate_foreign_design_human_evolution_envelope,
         'human evolution'),
    )
    for filename, validator, label in validators:
        for target in _stage4_history_files(
                ctx, f'*/design/{filename}', reject_shadowed=True):
            run_id = target.parent.parent.name
            try:
                with process_append_lock(target):
                    record = validator(
                        target, run_id=run_id, sid=ctx.sid)
            except OSError as exc:
                raise ProvenanceRefused(
                    f'Stage4 recovery foreign {label} {target} '
                    f'不可验证：{exc}') from exc
            if record['state'] != 'prepared':
                continue
            if (filename == DESIGN_REPAIR_AGGREGATE_COMMIT_FILE
                    and target.resolve() == allowed):
                continue
            raise ProvenanceRefused(
                f'Stage4 recovery 发现另一份 prepared {label}：{target}')


def inspect_prepared_design_repair_aggregate_recovery(
        ctx: Any,
) -> DesignRepairAggregateRecovery | None:
    """给 operator 的 0-wire 发现口；返回值已绑定 journal 与 candidate CAS。"""
    # 未进入 Stage4 的书没有该层 owner；纯读前沿不能为它创建运行目录或锁。
    # 布局检查仍先拒绝 legacy 空间；已有运行根继续在原所有权锁内完整扫描。
    if not any(root.exists() for root in runtime_layout.stage_run_roots(ctx.novel_dir, 'stage4')):
        return None
    with commit_ownership(ctx.novel_dir):
        found = _prepared_design_repair_aggregate_recovery_locked(ctx)
        if found is None:
            return None
        surface, _record = found
        refuse_foreign_commit_reservations(
            ctx, allowed_design_repair_run_id=surface.source_run_id)
        return surface


def load_prepared_design_repair_aggregate_recovery(
        ctx: Any, *, source_run_id: str, journal_sha256: str,
        source_commit_sha256: str,
) -> tuple[DesignRepairAggregateRecovery, dict[str, Any]]:
    """在提交锁内逐字消费 operator 冻结的 source journal 身份。"""
    _require_ownership(ctx)
    if (not agent_runs.is_sha256(journal_sha256)
            or not agent_runs.is_sha256(source_commit_sha256)):
        raise ProvenanceRefused('Stage4 repair recovery journal/commit SHA 非法')
    found = _prepared_design_repair_aggregate_recovery_locked(ctx)
    if found is None:
        raise ProvenanceRefused('Stage4 当前没有 prepared repair aggregate 可恢复')
    surface, record = found
    if (surface.source_run_id != check_run_id(source_run_id)
            or surface.journal_sha256 != journal_sha256
            or surface.commit_sha256 != source_commit_sha256):
        raise ProvenanceRefused(
            'Stage4 repair recovery 的 source run/journal raw/commit SHA 已漂移')
    refuse_foreign_commit_reservations(
        ctx, allowed_design_repair_run_id=surface.source_run_id)
    return surface, record


def load_design_repair_aggregate_recovery_sources(
        ctx: Any, *, source_run_id: str, journal_sha256: str,
        source_commit_sha256: str,
) -> tuple[
    DesignRepairAggregateRecovery, dict[str, Any], tuple[DesignUnitSource, ...],
]:
    """返回 exact prepared 及已经逐份回读原 owner 的 final unit sources。"""
    surface, record = load_prepared_design_repair_aggregate_recovery(
        ctx, source_run_id=source_run_id, journal_sha256=journal_sha256,
        source_commit_sha256=source_commit_sha256)
    sources = tuple(_load_design_source(
        ctx, row,
        default_run_id=str(row.get('source_run_id') or ''),
        default_source_kind=str(row.get('source_kind') or ''),
    ) for row in record['sources'])
    if [_design_source_row(source) for source in sources] != record['sources']:
        raise ProvenanceRefused(
            'Stage4 repair recovery sources 与 sealed inline lineage 漂移')
    return surface, record, sources


def prepare_design_repair_aggregate_commit(
        ctx: Any, *, parent_run_id: str, parent_candidate_sha256: str,
        repair_decision_sha256: str, target_chapters: Sequence[int],
        sources: Sequence[DesignUnitSource], primary_payload: Mapping[str, Any],
        finalization_payload: Mapping[str, Any],
        repair_scope_payload: Mapping[str, Any],
) -> dict[str, Any]:
    """冻结 targeted repair 的跨 run unit lineage 与新候选。"""
    _require_ownership(ctx)
    targets = sorted(set(target_chapters))
    if (not targets or len(targets) != len(tuple(target_chapters))
            or any(not isinstance(chapter, int) or isinstance(chapter, bool)
                   or chapter < 1 for chapter in targets)):
        raise ProvenanceRefused('design repair target_chapters 必须是非空去重正整数')
    if (not agent_runs.is_sha256(parent_candidate_sha256)
            or not agent_runs.is_sha256(repair_decision_sha256)):
        raise ProvenanceRefused('design repair parent/decision 必须是完整 SHA-256')
    _parent_kind, parent_target, parent_record = \
        _committed_design_aggregate_for_run(ctx, parent_run_id)
    _parent_payload, normalized_parent_sha = \
        _normalized_design_candidate_identity(
            parent_record['primary_payload'],
            label=f'parent design aggregate {parent_target}',
            aggregate_kind=_parent_kind,
        )
    if parent_candidate_sha256 != normalized_parent_sha:
        raise ProvenanceRefused(
            'design repair parent_candidate_sha256 必须使用当前 schema '
            '归一后的候选身份')
    by = {source.chapter: source for source in sources}
    chapters = sorted(chapter for chapter in by if chapter is not None)
    if (len(by) != len(sources) or None not in by
            or chapters != list(range(1, len(chapters) + 1))
            or any(chapter not in by for chapter in targets)):
        raise ProvenanceRefused('design repair sources 必须恰覆盖 strategy + 1..N')
    ordered = tuple(by[chapter] for chapter in (None, *chapters))
    raw_provenance = dict(primary_payload).get('_provenance')
    strategy_replan = (
        isinstance(raw_provenance, Mapping)
        and raw_provenance.get('architecture') == 'book_strategy_replan_v1')
    for chapter, source in by.items():
        expected = (
            'repair' if chapter in targets
            or (strategy_replan and chapter is None)
            else 'inherited')
        if (source.source_kind != expected
                or (expected == 'repair' and source.origin == 'agent'
                    and source.source_run_id != ctx.run_id)):
            raise ProvenanceRefused(
                f'design repair ch{chapter} source_kind/source_run 不符')
    primary = copy.deepcopy(dict(primary_payload))
    finalization = copy.deepcopy(dict(finalization_payload))
    repair_scope = copy.deepcopy(dict(repair_scope_payload))
    try:
        typed_scope = DesignRepairScope.model_validate(repair_scope, strict=True)
    except ValidationError as exc:
        raise ProvenanceRefused(
            f'design repair scope 不能封存进 aggregate：{exc}') from exc
    if typed_scope.model_dump(mode='json') != repair_scope:
        raise ProvenanceRefused('design repair scope 尚未规范化，拒绝封存')
    rows = [_design_source_row(source) for source in ordered]
    attempts = [{
        'source_run_id': source.source_run_id,
        'purpose': source.purpose,
        'attempt_id': source.attempt_id,
    } for source in ordered if source.origin in (
        'agent', 'historical_checkpoint')]
    payload: dict[str, Any] = {
        'version': _DESIGN_REPAIR_AGGREGATE_VERSION,
        'kind': _DESIGN_REPAIR_AGGREGATE_KIND,
        'state': 'prepared', 'prepared_at': _now(), 'committed_at': None,
        'run_id': check_run_id(ctx.run_id), 'sid': ctx.sid,
        'node': 'stage4.design', 'code': ctx.code,
        'producer_parent_run_id': check_run_id(ctx.parent_run_id),
        'parent_run_id': check_run_id(parent_run_id),
        'parent_candidate_sha256': parent_candidate_sha256,
        'repair_decision_sha256': repair_decision_sha256,
        'target_chapters': targets,
        'primary_artifact': STAGE4_BLIND_CANDIDATE,
        'primary_payload_sha256': agent_runs.canonical_sha256(primary),
        'primary_payload_fingerprint': prov.projection_fingerprint(primary),
        'primary_payload': primary,
        'finalization_sha256': agent_runs.canonical_sha256(finalization),
        'finalization_payload': finalization,
        'repair_scope': repair_scope,
        'touched_paths': [STAGE4_BLIND_CANDIDATE],
        'baselines': {STAGE4_BLIND_CANDIDATE:
                      agent_runs.file_sha256(ctx, STAGE4_BLIND_CANDIDATE)},
        'attempts': attempts,
        'sources': rows,
        'recovery': None,
        'landed_payload_sha256s': None,
        'post_file_sha256s': None,
    }
    payload['commit_sha256'] = _design_repair_aggregate_hash(payload)
    target = _design_repair_aggregate_commit_path(ctx)
    with _DESIGN_AGGREGATE_LOCK, process_append_lock(target):
        if target.is_file():
            current = _validate_design_repair_aggregate_state(ctx, target)
            ignored = {
                'state', 'prepared_at', 'committed_at', 'commit_sha256',
                'baselines', 'landed_payload_sha256s', 'post_file_sha256s',
            }
            if ({key: value for key, value in current.items() if key not in ignored}
                    != {key: value for key, value in payload.items()
                        if key not in ignored}):
                raise ProvenanceRefused('repair aggregate 已绑定另一份候选/lineage')
            return current
        _atomic_json(target, payload)
    return payload


def restore_design_repair_aggregate_payloads(
        ctx: Any, record: Mapping[str, Any] | None = None,
) -> tuple[dict[str, Any], dict[str, Any]]:
    current = design_repair_aggregate_state(ctx)
    if current is None or (record is not None and dict(record) != current):
        raise ProvenanceRefused('design repair aggregate 缺失或读取期漂移')
    return (copy.deepcopy(current['primary_payload']),
            copy.deepcopy(current['finalization_payload']))


def design_repair_aggregate_target_state(
        ctx: Any, record: Mapping[str, Any], *, expected_file_sha256: str,
) -> str:
    _require_ownership(ctx)
    current = _validate_design_repair_aggregate_state(
        ctx, _design_repair_aggregate_commit_path(ctx))
    if dict(record) != current or not agent_runs.is_sha256(expected_file_sha256):
        raise ProvenanceRefused('design repair aggregate target 身份/SHA 不闭合')
    actual = agent_runs.file_sha256(ctx, STAGE4_BLIND_CANDIDATE)
    if actual == expected_file_sha256:
        return 'expected'
    if actual == current['baselines'][STAGE4_BLIND_CANDIDATE]:
        return 'baseline'
    raise ProvenanceRefused('blind candidate 在 repair prepared 尾窗发生第三方漂移')


def _strict_jsonl_objects(path: Path, *, label: str) -> list[dict[str, Any]]:
    return ledger_module.read_strict_jsonl_objects(path, label=label)


def _design_recovery_source_owner(
        ctx: Any, source_run_id: str, *, expected_parent_run_id: str,
) -> tuple[str | None, bool]:
    """精确核验来源 epoch，并区分已停止 owner 与同父运行内补尾。

    ``agent.read_events`` 为 UI 容忍 kill 尾；恢复写口不能借那条容忍把尚未
    durable 的 ``epoch_closed``/``ended`` 当成不存在，因此在这里复用 strict-LF
    reader。来源必须 closed/drained；活动父运行只准当前有 claim 的后继
    epoch 补尾。只读预览可以发现这一机会，本函数不会因此授予写权限。
    """
    source = check_run_id(source_run_id)
    expected_parent = check_run_id(expected_parent_run_id)
    base = Path(ctx.novel_dir).parent
    from novel.orchestrator import agent

    raw_rows = _strict_jsonl_objects(
        agent.runs_path(base), label='Stage4 recovery AgentRun 账')
    try:
        events = [AgentRunEvent.replay_validate(row) for row in raw_rows]
    except ValidationError as exc:
        raise ProvenanceRefused(
            f'Stage4 recovery AgentRun 账不符合事件 schema：{exc}') from exc
    opened = [
        (index, event) for index, event in enumerate(events)
        if event.kind == 'epoch_opened' and event.epoch is not None
        and event.epoch.session_run_id == source
    ]
    if expected_parent == source:
        if opened:
            raise ProvenanceRefused(
                f'Stage4 journal 声明 source={source!r} 为 direct CLI，'
                '但 AgentRun 账存在 epoch 映射')
        return None, False
    if not opened:
        raise ProvenanceRefused(
            f'Stage4 journal 已封存 producer_parent={expected_parent!r}，'
            f'但 strict AgentRun 账找不到 source epoch={source!r}；'
            '不得因账缺失/截断降级 direct CLI')
    if len(opened) != 1:
        raise ProvenanceRefused(
            f'Stage4 recovery source producer={source!r} 映射到 '
            f'{len(opened)} 条 epoch_opened；不得猜 parent')
    open_index, open_event = opened[0]
    assert open_event.epoch is not None
    parent = check_run_id(open_event.run_id)
    epoch_id = open_event.epoch.epoch_id
    if parent != expected_parent:
        raise ProvenanceRefused(
            f'Stage4 recovery source epoch parent={parent!r} 与 journal '
            f'producer_parent={expected_parent!r} 不等')
    parent_events = [
        (index, event) for index, event in enumerate(events)
        if event.run_id == parent]
    starts = [
        (index, event) for index, event in parent_events
        if event.kind == 'started'
    ]
    closed = [
        (index, event) for index, event in parent_events
        if event.kind == 'epoch_closed'
        and event.epoch_id == epoch_id
    ]
    ended = [
        (index, event) for index, event in parent_events
        if event.kind == 'ended'
    ]
    contract = starts[0][1].contract if len(starts) == 1 else None
    if (open_event.wire_version != 2
            or open_event.epoch.code_version != ctx.code
            or len(starts) != 1 or contract is None
            or contract.sid != ctx.sid
            or len(closed) != 1 or len(ended) > 1
            or closed[0][1].drain_state != 'drained'
            or starts[0][0] != parent_events[0][0]
            or any(event.wire_version != 2 for _index, event in parent_events)
            or not (starts[0][0] < open_index < closed[0][0])
            or (ended and closed[0][0] >= ended[0][0])):
        raise ProvenanceRefused(
            f'Stage4 recovery source producer={source!r} 已映射 Agent epoch，'
            '但 started/epoch_closed/parent ended/code/sid 终态链不闭合；'
            '不得降级成 direct CLI')

    # 来源以前必须完整排空，后继仍遵守唯一串行 epoch。ended 是永久终态，
    # 不能用同父恢复把终态之后的迟到事件洗成另一条生命。
    if ended and ended[0][0] != parent_events[-1][0]:
        raise ProvenanceRefused(
            f'Stage4 recovery parent={parent!r} 在 ended 后仍有事件')
    active_epoch: str | None = None
    seen_epochs: set[str] = set()
    paused = False
    for index, event in parent_events:
        if event.kind == 'started':
            if index != starts[0][0]:
                raise ProvenanceRefused(
                    f'Stage4 recovery parent={parent!r} 重复 started')
        elif event.kind == 'epoch_opened':
            assert event.epoch is not None
            if (paused or active_epoch is not None
                    or event.epoch.epoch_id in seen_epochs):
                raise ProvenanceRefused(
                    f'Stage4 recovery parent={parent!r} epoch 打开链重叠/重复')
            active_epoch = event.epoch.epoch_id
            seen_epochs.add(active_epoch)
        elif event.kind == 'epoch_closed':
            if (active_epoch != event.epoch_id
                    or event.drain_state != 'drained'):
                raise ProvenanceRefused(
                    f'Stage4 recovery parent={parent!r} epoch '
                    'closed 未匹配或 drain 未闭合')
            active_epoch = None
        elif event.kind == 'paused':
            if paused or active_epoch is not None:
                raise ProvenanceRefused(
                    f'Stage4 recovery parent={parent!r} paused 状态链非法')
            paused = True
        elif event.kind == 'resumed':
            if not paused or active_epoch is not None:
                raise ProvenanceRefused(
                    f'Stage4 recovery parent={parent!r} resumed 状态链非法')
            paused = False
        elif event.kind == 'ended' and active_epoch is not None:
            raise ProvenanceRefused(
                f'Stage4 recovery parent={parent!r} ended 前仍有 open epoch')
    if ended and active_epoch is not None:
        raise ProvenanceRefused(
            f'Stage4 recovery parent={parent!r} 仍有 open epoch={active_epoch!r}')
    # fold 只作一致性复核，不能代替上面的严格顺序与排空证据。
    folded = [row for row in agent.fold(events) if row.run_id == parent]
    if (len(folded) != 1 or (ended and folded[0].ended is None)
            or folded[0].active_epoch_id != active_epoch):
        raise ProvenanceRefused(
            f'Stage4 recovery parent={parent!r} fold 与严格生命周期不等')
    live_parent = not ended
    if live_parent and not ctx.readonly:
        from novel.orchestrator import execution_context
        from novel.orchestrator import admission

        current = execution_context.load_from_environment(
            base, ctx.sid)
        if (ctx.parent_run_id != parent or ctx.run_id == source
                or current is None or current.run_id != parent
                or current.epoch is None
                or current.epoch.epoch_id != active_epoch
                or current.epoch.session_run_id != ctx.run_id
                or current.epoch.code_version != ctx.code):
            raise ProvenanceRefused(
                'Stage4 活动父运行只允许其当前后继 epoch 免费补尾；拒绝外部恢复者')
        # 完整上下文核验当前 code/skill/profile，额外要求未释放的执行 claim。
        # 暂停/停止会等 claim 排空，不能争抢仍由本 parent 持有的 owner lease。
        gate = admission.readonly_snapshot(base, current.execution_id)
        if (not gate.lifecycle_linked or gate.lifecycle_run_id != parent
                or gate.sid != ctx.sid or gate.closed):
            raise ProvenanceRefused('Stage4 同父补尾缺当前开放的 admission 归属')
        admission.require_active_claim(base, current.execution_id, ctx.sid, ctx.node)
    return parent, live_parent


def design_repair_recovery_source_parent(
        ctx: Any, source_run_id: str, *, expected_parent_run_id: str,
) -> str | None:
    return _design_recovery_source_owner(
        ctx, source_run_id, expected_parent_run_id=expected_parent_run_id)[0]


@contextmanager
def require_design_repair_recovery_source_stopped(
        ctx: Any, source_run_id: str, *, producer_parent_run_id: str,
):
    """外部恢复持停止 lease；同父后继持当前 claim，来源仍须 closed/drained。"""
    source = check_run_id(source_run_id)
    if ctx.readonly:
        raise ProvenanceRefused('只读恢复预览不能进入提交事务')
    parent, live_parent = _design_recovery_source_owner(
        ctx, source, expected_parent_run_id=producer_parent_run_id)
    if live_parent:
        yield parent
        return
    from novel.orchestrator import admission

    with admission.require_owner_stopped(Path(ctx.novel_dir).parent, parent or source):
        # 取得 OS lease 后再读一次，避免探测与 lease 之间发生映射/终态漂移。
        if design_repair_recovery_source_parent(
                ctx, source,
                expected_parent_run_id=producer_parent_run_id) != parent:
            raise ProvenanceRefused(
                'Stage4 recovery source Agent epoch 在 owner lease 内发生漂移')
        yield parent


def _require_design_repair_recovery_target_identity_clean(
        ctx: Any, target_run_id: str, *, stage4_design_only: bool = False,
) -> None:
    """复核 recovery committer 没有留下被禁止的 wire/session。

    候选写入前的 target run 必须整个干净；committed 之后的永久
    消费验证只能拒绝该 run 上的 ``stage4.design`` 模型事实。
    同一 Agent epoch 后续合法的 ``stage4.plan`` 成本/会话不得让
    已恢复 lineage 自毁。
    """
    target_run = check_run_id(target_run_id)
    base = Path(ctx.novel_dir).parent
    cost_rows = _strict_jsonl_objects(
        ledger_module.ledger_path(base), label='Stage4 recovery 成本账')
    if any(
            row.get('run_id') == target_run
            and (not stage4_design_only
                 or row.get('node') == 'stage4.design')
            for row in cost_rows):
        raise ProvenanceRefused(
            'Stage4 repair recovery target 已有持久 ledger 行；不得冒充 0-wire')
    session_rows = _strict_jsonl_objects(
        agent_session_audit.audit_path(base),
        label='Stage4 recovery agent session 账')
    for index, row in enumerate(session_rows):
        try:
            event = agent_session_audit.AgentSessionEvent.from_dict(row)
        except (TypeError, ValueError) as exc:
            raise ProvenanceRefused(
                f'Stage4 recovery agent session 第 {index + 1} 行不合法：{exc}') from exc
        if (event.parent_run_id == target_run
                and (not stage4_design_only
                     or event.node == 'stage4.design')):
            raise ProvenanceRefused(
                'Stage4 repair recovery target 已有 agent session 事件；不得冒充 0-wire')


def require_design_repair_recovery_target_clean(ctx: Any) -> None:
    """证明当前 target run 是 FREE，且从未留下 wire/cache/session 事实。"""
    if ctx.node != 'stage4.design' or ctx.paid:
        raise ProvenanceRefused(
            'Stage4 repair aggregate recovery 只属于 free stage4.design run')
    if (getattr(ctx, 'executor_backend', None) is not None
            or getattr(ctx, 'executor_profile', None) is not None):
        raise ProvenanceRefused(
            'Stage4 repair aggregate recovery 禁止 executor backend/profile')
    if ctx.ledger.calls_made or ctx.ledger.calls_cached:
        raise ProvenanceRefused(
            'Stage4 repair recovery target 已有内存 wire/cache 计数')
    _require_design_repair_recovery_target_identity_clean(ctx, ctx.run_id)


def _repair_source_profile(run: Mapping[str, Any], *, purpose: str) -> AgentProfile:
    policy = run.get('request_policy')
    raw = policy.get('profile') if isinstance(policy, dict) else None
    try:
        profile = validate_profile(
            AgentProfile.model_validate(raw))
    except (ValidationError, ValueError) as exc:
        raise ProvenanceRefused(
            f'{purpose}: source run 的 executor profile 不可执行：{exc}') from exc
    if profile.model_dump(mode='json') != raw:
        raise ProvenanceRefused(f'{purpose}: source profile 尚未完整物化')
    return profile


def _repair_source_run_path(
        ctx: Any, *, source_run_id: str, chapter: int | None,
) -> Path:
    runtime_layout.stage_run_roots(ctx.novel_dir, 'stage4')
    return paths.stage4_design_unit_run_path(ctx.novel_dir, source_run_id, chapter) / agent_runs.RUN_FILE


def validate_design_repair_recovery_unit_request(
        ctx: Any, source: DesignUnitSource, request: DesignUnitRequest,
) -> None:
    """按当前 compiler 构造出的 exact context 复核 source run 请求身份。"""
    if (source.chapter != request.chapter
            or source.purpose != design_unit_purpose(request.chapter)):
        raise ProvenanceRefused(
            'Stage4 recovery source 与 current compiler unit 不同章/purpose')
    if source.origin == 'human':
        if source.source_kind == 'repair':
            raise ProvenanceRefused(
                'Stage4 recovery repair target 不得由无 run 请求的 deterministic source 提供')
        return
    _validate_design_repair_recovery_run_request(
        ctx, source_run_id=source.source_run_id, request=request)


def validate_design_repair_recovery_unit_checkpoint_state(
        ctx: Any, source: DesignUnitSource, *,
        state_payload: Mapping[str, Any],
) -> None:
    """按当前 compiler 重建的业务状态复验 source checkpoint。"""
    if source.source_kind != 'repair' or source.origin not in {
            'agent', 'historical_checkpoint'}:
        raise ProvenanceRefused(
            'Stage4 recovery 只能复验 agent/historical repair checkpoint')
    expected_state = dict(state_payload)
    expected_code = (
        ctx.code if source.origin == 'agent'
        else _repair_recovery_historical_source_code(
            ctx, _design_source_row(source)))
    run_path = _repair_source_run_path(
        ctx, source_run_id=source.source_run_id, chapter=source.chapter)
    run = _read_object(
        run_path, label=f'{source.purpose} recovery state source run')
    if run.get('code') != expected_code:
        raise ProvenanceRefused(
            f'{source.purpose}: checkpoint state source code 漂移')
    profile = _repair_source_profile(run, purpose=source.purpose)
    from novel.stages.stage4 import prompts
    response_model: type[BaseModel] = (
        BlindBookStrategyResponse
        if source.chapter is None else BlindChapterDesignResponse)
    max_tokens = (
        prompts.BLIND_BOOK_STRATEGY_MAX_TOKENS
        if source.chapter is None else prompts.BLIND_CHAPTER_DESIGN_MAX_TOKENS)
    historical = agent_runs._HistoricalContext(  # noqa: SLF001
        novel_dir=Path(ctx.novel_dir), node='stage4.design',
        code=expected_code, executor_profile=profile,
        run_id=source.source_run_id,
        ledger=CostLedger(
            path=ledger_module.ledger_path(Path(ctx.novel_dir).parent),
            run_id=source.source_run_id),
        _sid=ctx.sid,
    )
    agent_runs._validate_run_record(  # noqa: SLF001
        historical, design_unit_run_spec(source.chapter), run,
        response_model=response_model, max_tokens=max_tokens)
    try:
        run_sha = agent_runs.canonical_sha256(run_path.read_bytes())
    except OSError as exc:
        raise ProvenanceRefused(
            f'{source.purpose}: checkpoint state source run 不可读：{exc}') from exc
    latest = agent_runs._latest_checkpoint(  # noqa: SLF001
        historical, design_unit_run_spec(source.chapter), run_sha=run_sha)
    if latest is None:
        raise ProvenanceRefused(
            f'{source.purpose}: checkpoint state source 缺 latest revision')
    checkpoint_path, checkpoint = latest
    try:
        checkpoint_file_sha = agent_runs.canonical_sha256(
            checkpoint_path.read_bytes())
    except OSError as exc:
        raise ProvenanceRefused(
            f'{source.purpose}: checkpoint state revision 不可读：{exc}') from exc
    if (source.run_sha256 != run_sha
            or source.checkpoint_revision != checkpoint.get('revision')
            or source.checkpoint_sha256 != checkpoint.get('checkpoint_sha256')
            or source.checkpoint_file_sha256 != checkpoint_file_sha
            or source.response_payload_sha256
            != checkpoint.get('response_payload_sha256')
            or source.response_payload != checkpoint.get('response_payload')
            or checkpoint.get('revision_key') != 'terminal'
            or checkpoint.get('state_payload') != expected_state
            or checkpoint.get('state_payload_sha256')
            != agent_runs.canonical_sha256(expected_state)):
        raise ProvenanceRefused(
            f'{source.purpose}: source checkpoint terminal/state_payload '
            '不等于当前 compiler 派生状态')


def _validate_design_repair_recovery_run_request(
        ctx: Any, *, source_run_id: str, request: DesignUnitRequest,
        expected_code: str | None = None,
) -> str:
    """复验一份 unit run 的 current request，并返回配置主模型。"""
    run_path = _repair_source_run_path(
        ctx, source_run_id=source_run_id, chapter=request.chapter)
    run = _read_object(
        run_path,
        label=f'{design_unit_purpose(request.chapter)} recovery request source run')
    if expected_code is not None and run.get('code') != expected_code:
        raise ProvenanceRefused(
            f'{design_unit_purpose(request.chapter)}: producer run code '
            '与 sealed/current code 漂移')
    profile = _repair_source_profile(
        run, purpose=design_unit_purpose(request.chapter))
    historical = agent_runs._HistoricalContext(  # noqa: SLF001
        novel_dir=Path(ctx.novel_dir), node='stage4.design',
        code=str(run.get('code') or ''), executor_profile=profile,
        run_id=check_run_id(source_run_id),
        ledger=CostLedger(
            path=ledger_module.ledger_path(Path(ctx.novel_dir).parent),
            run_id=check_run_id(source_run_id)),
        _sid=ctx.sid,
    )
    checked = agent_runs._validate_run_record(  # noqa: SLF001
        historical, design_unit_run_spec(request.chapter), run,
        response_model=request.response_model, max_tokens=request.max_tokens)
    expected_request = agent_runs._request_identity(  # noqa: SLF001
        design_unit_run_spec(request.chapter), request.system, request.user,
        request.response_model, driver=profile.driver)
    if (checked.get('input_sha256') != request.input_sha256
            or checked.get('request') != expected_request):
        raise ProvenanceRefused(
            f'{design_unit_purpose(request.chapter)}: '
            'source run input/system/user/schema 与 '
            'current compiler context 漂移')
    return profile.model


def design_repair_recovery_producer_model(
        ctx: Any, *, source_run_id: str,
        requests: Sequence[DesignUnitRequest],
) -> str:
    """从 source producer 当时为本轮建的 exact unit runs 重建主模型。

    historical adoption 的最终 source 指向更早的 run，但原 producer 仍先为
    当前请求建立了 target run；manifest 已把两份 request/profile 钉为相等。
    因而这里一律读 source producer 的 unit run，不从 sealed provenance 反推。
    """
    source = check_run_id(source_run_id)
    normalized = tuple(requests)
    keys = [request.chapter for request in normalized]
    if not normalized or len(keys) != len(set(keys)):
        raise ProvenanceRefused(
            'Stage4 recovery producer model 请求必须非空且 unit 不重复')
    models = {
        _validate_design_repair_recovery_run_request(
            ctx, source_run_id=source, request=request,
            expected_code=ctx.code)
        for request in normalized
    }
    if len(models) != 1:
        raise ProvenanceRefused(
            f'Stage4 recovery source producer unit 主模型不唯一：{sorted(models)}')
    return next(iter(models))


def validate_design_repair_recovery_historical_input(
        ctx: Any, sources: Sequence[DesignUnitSource], *,
        repair_input_sha256: str,
) -> None:
    """historical target 还必须属于本次准确 repair input 的 manifest。"""
    if not agent_runs.is_sha256(repair_input_sha256):
        raise ProvenanceRefused(
            'Stage4 recovery historical repair_input_sha256 非法')
    for source in sources:
        if source.origin != 'historical_checkpoint':
            continue
        manifest = _require_design_historical_source_owner(
            ctx, _design_source_row(source))
        if manifest.get('repair_input_sha256') != repair_input_sha256:
            raise ProvenanceRefused(
                f'{source.purpose}: historical adoption manifest 不属于 '
                '当前重建的 repair input')


def _validate_repair_source_session(
        ctx: Any, *, source_run_id: str, chapter: int | None,
        run: Mapping[str, Any], checkpoint: Mapping[str, Any],
        profile: AgentProfile,
) -> None:
    purpose = design_unit_purpose(chapter)
    raw_rows = _strict_jsonl_objects(
        agent_session_audit.audit_path(Path(ctx.novel_dir).parent),
        label='Stage4 recovery source agent session 账')
    try:
        events = [agent_session_audit.AgentSessionEvent.from_dict(row)
                  for row in raw_rows]
        agent_session_audit.fold(events)
    except (TypeError, ValueError) as exc:
        raise ProvenanceRefused(
            f'{purpose}: source agent session 审计不可验证：{exc}') from exc
    request = run['request']
    session = validate_agent_session_request_binding(
        events, checkpoint,
        purpose=purpose,
        profile=profile.model_dump(mode='json'),
        original_prompt_sha256=str(request['initial_prompt_sha256']),
        original_schema_sha256=str(request['response_schema_sha256']),
    )
    accepted_attempt = checkpoint.get('attempt_id')
    accepted_turns = [event for event in events
                      if event.kind == 'turn'
                      and event.attempt_id == accepted_attempt]
    starts = [event for event in events
              if event.kind == 'started'
              and event.session_run_id == checkpoint.get('session_run_id')]
    if len(accepted_turns) != 1 or len(starts) != 1:
        raise ProvenanceRefused(
            f'{purpose}: accepted attempt/session started 必须各唯一一条')
    start, turn = starts[0], accepted_turns[0]
    normalized_profile = profile.model_dump(mode='json')
    start_profile = (
        None if start.profile is None else start.profile.model_dump(mode='json'))
    turn_profile = (
        None if turn.profile is None else turn.profile.model_dump(mode='json'))
    if (start.parent_run_id != source_run_id
            or start.role != 'executor' or start.node != 'stage4.design'
            or start.chapter != chapter
            or start.subject_sha256 != checkpoint.get('subject_sha256')
            or start_profile != normalized_profile
            or start.provider_session_id is not None
            or start.requested_model != profile.model
            or start.requested_effort != profile.reasoning_effort
            or start.protocol_version != SESSION_PROTOCOL_VERSION
            or start.start_code_version != run.get('code')
            or not session.turns
            or start.prompt_sha256 != session.turns[0].prompt_sha256
            or start.schema_sha256 != session.turns[0].schema_sha256):
        raise ProvenanceRefused(f'{purpose}: source started 与 run/checkpoint 漂移')
    validate_checkpoint_model_identity(
        normalized_profile,
        checkpoint_model=checkpoint.get('model'),
        actual_model=turn.actual_model,
        cli_version=turn.cli_version,
        require_driver_version=True,
    )
    if (turn.parent_run_id != source_run_id
            or turn.role != 'executor' or turn.node != 'stage4.design'
            or turn.chapter != chapter
            or turn.subject_sha256 != checkpoint.get('subject_sha256')
            or turn_profile != normalized_profile
            or turn.provider_session_id != checkpoint.get('provider_session_id')
            or turn.turn_index != checkpoint.get('accepted_turn_index')
            or turn.requested_model != profile.model
            or turn.requested_effort != profile.reasoning_effort
            or turn.protocol_version != SESSION_PROTOCOL_VERSION
            or turn.start_code_version != run.get('code')
            or turn.prompt_sha256 != checkpoint.get('accepted_prompt_sha256')
            or turn.schema_sha256 != checkpoint.get('accepted_schema_sha256')):
        raise ProvenanceRefused(f'{purpose}: source turn 与 checkpoint 漂移')
    linked = {str(row['attempt_id']) for row in checkpoint['attempt_links']}
    for event in events:
        if event.kind != 'turn' or event.attempt_id not in linked:
            continue
        event_profile = (
            None if event.profile is None
            else event.profile.model_dump(mode='json'))
        if (event.parent_run_id != source_run_id
                or event.role != 'executor'
                or event.node != 'stage4.design'
                or event.chapter != chapter
                or event.subject_sha256 != checkpoint.get('subject_sha256')
                or event_profile != normalized_profile):
            raise ProvenanceRefused(
                f'{purpose}: attempt_links 含跨 run/unit/session turn')


def _validate_repair_source_checkpoint(
        ctx: Any, row: Mapping[str, Any], *, source_ledger: CostLedger,
        cost_rows: Sequence[Mapping[str, Any]], expected_code: str,
        require_current_code: bool = True,
) -> _ValidatedRepairSource:
    """只读复验 run/checkpoint/schema/session/ledger 的完整 source identity。"""
    source = _load_design_source(
        ctx, row, default_run_id=str(row.get('source_run_id') or ''),
        default_source_kind='repair')
    chapter = source.chapter
    purpose = source.purpose
    run_path = _repair_source_run_path(
        ctx, source_run_id=source.source_run_id, chapter=chapter)
    run = _read_object(run_path, label=f'{purpose} recovery source run')
    if (run.get('code') != expected_code
            or (require_current_code and expected_code != ctx.code)):
        raise ProvenanceRefused(
            f'{purpose}: source run code 与 v3 journal/current code 漂移')
    profile = _repair_source_profile(run, purpose=purpose)
    from novel.stages.stage4 import prompts
    response_model: type[BaseModel] = (
        BlindBookStrategyResponse
        if chapter is None else BlindChapterDesignResponse)
    max_tokens = (
        prompts.BLIND_BOOK_STRATEGY_MAX_TOKENS
        if chapter is None else prompts.BLIND_CHAPTER_DESIGN_MAX_TOKENS)
    historical = agent_runs._HistoricalContext(  # noqa: SLF001
        novel_dir=Path(ctx.novel_dir), node='stage4.design',
        code=str(run.get('code') or ''), executor_profile=profile,
        run_id=source.source_run_id, ledger=source_ledger, _sid=ctx.sid)
    checked_run = agent_runs._validate_run_record(  # noqa: SLF001
        historical, design_unit_run_spec(chapter), run,
        response_model=response_model, max_tokens=max_tokens)
    run_sha = agent_runs.canonical_sha256(run_path.read_bytes())
    latest = agent_runs._latest_checkpoint(  # noqa: SLF001
        historical, design_unit_run_spec(chapter), run_sha=run_sha)
    if latest is None:
        raise ProvenanceRefused(f'{purpose}: source run 没有 checkpoint')
    checkpoint_path, checkpoint = latest
    if (source.run_sha256 != run_sha
            or source.checkpoint_revision != checkpoint.get('revision')
            or source.checkpoint_sha256 != checkpoint.get('checkpoint_sha256')
            or source.checkpoint_file_sha256
            != agent_runs.canonical_sha256(checkpoint_path.read_bytes())
            or source.attempt_id != checkpoint.get('attempt_id')
            or source.response_payload_sha256
            != checkpoint.get('response_payload_sha256')
            or source.model != checkpoint.get('model')
            or source.accepted_turn_index
            != checkpoint.get('accepted_turn_index')
            or checked_run.get('request', {}).get('response_schema_sha256')
            != checkpoint.get('accepted_schema_sha256')
            or checkpoint.get('code') != expected_code
            or checkpoint.get('revision_key') != 'terminal'):
        raise ProvenanceRefused(
            f'{purpose}: aggregate source 与 latest checkpoint/run 漂移')
    try:
        parsed = json.loads(str(checkpoint['normalized_text']))
        typed = response_model.model_validate(parsed)
    except (json.JSONDecodeError, ValidationError, ValueError) as exc:
        raise ProvenanceRefused(
            f'{purpose}: source checkpoint stdout 不再通过 schema：{exc}') from exc
    if (normalize_json_text(str(checkpoint['accepted_text']))
            != checkpoint['normalized_text']
            or parsed != checkpoint['parsed_payload']
            or typed.model_dump(by_alias=True) != checkpoint['response_payload']
            or checkpoint['response_payload'] != source.response_payload):
        raise ProvenanceRefused(
            f'{purpose}: source stdout/parsed/typed/aggregate payload 漂移')
    _validate_repair_source_session(
        ctx, source_run_id=source.source_run_id, chapter=chapter,
        run=checked_run, checkpoint=checkpoint, profile=profile)
    links = checkpoint.get('attempt_links')
    if not isinstance(links, list) or not links:
        raise ProvenanceRefused(f'{purpose}: source checkpoint 缺 attempt_links')
    identities = tuple(dict(link) for link in links)
    attempt_ids = [str(link.get('attempt_id') or '') for link in identities]
    fingerprints = [str(link.get('request_fingerprint') or '')
                    for link in identities]
    for identity in identities:
        attempt_id = str(identity.get('attempt_id') or '')
        matches = [item for item in cost_rows
                   if item.get('attempt_id') == attempt_id]
        if len(matches) != 1:
            raise ProvenanceRefused(
                f'{purpose}: attempt={attempt_id} 成本账必须全局唯一，'
                f'实得 {len(matches)}')
        item = matches[0]
        if (item.get('run_id') != source.source_run_id
                or item.get('sid') != ctx.sid
                or item.get('node') != 'stage4.design'
                or item.get('purpose') != purpose
                or item.get('code') != expected_code
                or item.get('chapter') != chapter):
            raise ProvenanceRefused(
                f'{purpose}: attempt={attempt_id} 成本账跨 source/unit')
    source_ledger.claim_prior_attempts(
        attempt_ids, sid=ctx.sid, node='stage4.design', purpose=purpose,
        request_fingerprints=fingerprints)
    for identity in identities:
        attempt_id = str(identity['attempt_id'])
        if source_ledger.request_identity_for_attempt(attempt_id) != identity:
            raise ProvenanceRefused(
                f'{purpose}: checkpoint attempt_links 与 source ledger 漂移')
    accepted_rows = [
        identity for identity in identities
        if identity.get('attempt_id') == source.attempt_id]
    if len(accepted_rows) != 1:
        raise ProvenanceRefused(
            f'{purpose}: accepted attempt 在 checkpoint attempt_links '
            f'中必须恰有一条，实得 {len(accepted_rows)}')
    accepted = accepted_rows[0]
    if (accepted.get('request_fingerprint')
            != checkpoint.get('request_fingerprint')
            or accepted.get('response_sha256')
            != checkpoint.get('ledger_response_sha256')
            or accepted.get('model') != checkpoint.get('model')):
        raise ProvenanceRefused(
            f'{purpose}: accepted request_fingerprint/response/model 不闭合')
    session_run_id = checkpoint.get('session_run_id')
    subject_sha256 = checkpoint.get('subject_sha256')
    if (not isinstance(session_run_id, str) or not session_run_id
            or not agent_runs.is_sha256(subject_sha256)):
        raise ProvenanceRefused(
            f'{purpose}: source checkpoint session/subject 身份不闭合')
    return _ValidatedRepairSource(
        purpose=purpose, chapter=chapter, identities=identities,
        session_run_id=session_run_id, subject_sha256=str(subject_sha256),
        code=expected_code, profile=profile.model_dump(mode='json'))


def _require_repair_source_run_surface_closed(
        ctx: Any, *, source_run_id: str, producer_parent_run_id: str,
        expected_code: str, cost_rows: Sequence[Mapping[str, Any]],
        validated: Sequence[_ValidatedRepairSource],
) -> None:
    """source run 的 cost/session 必须恰好属于 sealed repair units。

    checkpoint links 是受采纳请求的内核，但不是 run 闭集；若同
    source run 还混入另一 node/purpose/code/session，只验 links 会把
    那段 durable wire 事实当成不存在。all-historical producer 的合法
    ``validated`` 为空，因而也明确要求它的成本/会话集为空。
    """
    source = check_run_id(source_run_id)
    parent = check_run_id(producer_parent_run_id)
    expected_by_purpose: dict[str, _ValidatedRepairSource] = {}
    for item in validated:
        if (item.code != expected_code
                or item.purpose in expected_by_purpose):
            raise ProvenanceRefused(
                f'Stage4 recovery source run={source} '
                'unit purpose/code 不唯一')
        expected_by_purpose[item.purpose] = item

    run_cost_rows = [row for row in cost_rows if row.get('run_id') == source]
    attempt_ids: set[str] = set()
    for row in run_cost_rows:
        purpose = row.get('purpose')
        item = expected_by_purpose.get(str(purpose or ''))
        raw_parent = row.get('parent_run_id', source)
        try:
            row_parent = check_run_id(str(raw_parent or ''))
        except ValueError as exc:
            raise ProvenanceRefused(
                f'Stage4 recovery source run={source} ledger parent 非法：'
                f'{exc}') from exc
        attempt_id = row.get('attempt_id')
        if (item is None
                or row_parent != parent
                or row.get('sid') != ctx.sid
                or row.get('node') != 'stage4.design'
                or row.get('code') != expected_code
                or row.get('chapter') != item.chapter
                or not isinstance(attempt_id, str) or not attempt_id
                or attempt_id in attempt_ids):
            raise ProvenanceRefused(
                f'Stage4 recovery source run={source} 存在 sealed '
                'repair units 之外或身份漂移的成本行')
        attempt_ids.add(attempt_id)
    if bool(expected_by_purpose) != bool(run_cost_rows):
        raise ProvenanceRefused(
            f'Stage4 recovery source run={source} 成本账空/非空与 '
            'sealed repair units 不等')

    raw_session_rows = _strict_jsonl_objects(
        agent_session_audit.audit_path(Path(ctx.novel_dir).parent),
        label='Stage4 recovery source agent session 账')
    try:
        events = [agent_session_audit.AgentSessionEvent.from_dict(row)
                  for row in raw_session_rows]
        agent_session_audit.fold(events)
    except (TypeError, ValueError) as exc:
        raise ProvenanceRefused(
            'Stage4 recovery source agent session 审计不可验证：'
            f'{exc}') from exc
    relevant = [event for event in events if event.parent_run_id == source]
    expected_by_chapter = {
        item.chapter: item for item in expected_by_purpose.values()}
    if len(expected_by_chapter) != len(expected_by_purpose):
        raise ProvenanceRefused(
            f'Stage4 recovery source run={source} chapter 定位不唯一')
    for event in relevant:
        item = expected_by_chapter.get(event.chapter)
        profile = (
            None if event.profile is None
            else event.profile.model_dump(mode='json'))
        if (item is None
                or event.role != 'executor'
                or event.node != 'stage4.design'
                or event.chapter != item.chapter
                or event.subject_sha256 != item.subject_sha256
                or profile != item.profile
                or event.requested_model != item.profile.get('model')
                or event.requested_effort
                != item.profile.get('reasoning_effort')
                or event.protocol_version != SESSION_PROTOCOL_VERSION
                or event.start_code_version != expected_code
                or (event.end_code_version is not None
                    and event.end_code_version != expected_code)):
            raise ProvenanceRefused(
                f'Stage4 recovery source run={source} 存在 sealed '
                'repair sessions 之外或身份漂移的会话事件')
    folded = [record for record in agent_session_audit.fold(events)
              if record.start.parent_run_id == source]
    cost_attempts = {
        str(row['attempt_id']) for row in run_cost_rows}
    for record in folded:
        event_attempts = {
            str(event.attempt_id)
            for event in (*record.turns, *record.attempts)
            if event.attempt_id is not None}
        # provider 在真正产出 turn 前失败时，session 可以只有
        # started/ended；对应费用仍由同 run/purpose 的完整成本闭集认领。
        # 不能把这种合法失败历史误当成缺账。若已有 attempt link，则每一条
        # 仍必须落在 source ledger 内。
        if not event_attempts.issubset(cost_attempts):
            raise ProvenanceRefused(
                f'Stage4 recovery source run={source} session='
                f'{record.session_run_id} 没有逐项联到 source ledger')
    if (not {item.session_run_id for item in validated}.issubset(
            {record.session_run_id for record in folded})
            or (bool(expected_by_purpose) != bool(folded))):
        raise ProvenanceRefused(
            f'Stage4 recovery source run={source} session 闭集与 '
            'sealed repair units 不等')


def _repair_recovery_source_ledger(
        ctx: Any, record: Mapping[str, Any], *, source_run_id: str,
) -> CostLedger:
    """完整复验 source checkpoints 后构造只补 audit 的隔离 ledger。"""
    source = check_run_id(source_run_id)
    expected = [row for row in record['sources'] if (
        row['source_kind'] == 'repair'
        and row.get('origin', 'agent') == 'agent'
        and row['source_run_id'] == source)]
    ledger_path = ledger_module.ledger_path(Path(ctx.novel_dir).parent)
    cost_rows = _strict_jsonl_objects(
        ledger_path, label='Stage4 recovery source 成本账')
    source_rows = [row for row in cost_rows if row.get('run_id') == source]
    parent = check_run_id(str(record.get('producer_parent_run_id') or ''))
    mapped_parent = design_repair_recovery_source_parent(
        ctx, source, expected_parent_run_id=parent)
    if (parent == source) != (mapped_parent is None):
        raise ProvenanceRefused(
            'Stage4 recovery source direct/Agent parent 分类漂移')
    try:
        parents = {
            check_run_id(str(row.get('parent_run_id') or source))
            for row in source_rows}
    except ValueError as exc:
        raise ProvenanceRefused(
            f'Stage4 repair recovery source ledger parent 非法：{exc}') from exc
    if parents and parents != {parent}:
        raise ProvenanceRefused(
            'Stage4 repair recovery source ledger 与 Agent/direct parent '
            f'不等：expected={parent!r}, actual={sorted(parents)}')
    source_ledger = CostLedger(
        path=ledger_path, run_id=source, parent_run_id=parent)
    purposes: list[str] = []
    required: list[tuple[str, Mapping[str, object]]] = []
    validated: list[_ValidatedRepairSource] = []
    for row in expected:
        checked = _validate_repair_source_checkpoint(
            ctx, row, source_ledger=source_ledger, cost_rows=cost_rows,
            expected_code=str(record.get('code') or ''))
        validated.append(checked)
        purposes.append(checked.purpose)
        required.extend(
            (checked.purpose, identity) for identity in checked.identities)
    _require_repair_source_run_surface_closed(
        ctx, source_run_id=source, producer_parent_run_id=parent,
        expected_code=str(record.get('code') or ''), cost_rows=cost_rows,
        validated=validated)
    if purposes and source_ledger.usage_for_complete_purposes(
            purposes, sid=ctx.sid, node='stage4.design',
            required_identities=required) is None:
        raise ProvenanceRefused(
            'Stage4 repair recovery source checkpoints 有未知成本分段')
    return source_ledger


def _repair_recovery_historical_source_code(
        ctx: Any, row: Mapping[str, Any],
) -> str:
    """从 hash-bound adoption manifest 取历史 checkpoint 的代码身份。"""
    manifest = _require_design_historical_source_owner(ctx, row)
    chapter = row.get('chapter')
    candidates = [
        source for source in manifest.get('sources', [])
        if isinstance(source, dict) and source.get('chapter') == chapter]
    if len(candidates) != 1:
        raise ProvenanceRefused(
            f'Stage4 historical ch{chapter} 缺唯一 manifest source code')
    code = candidates[0].get('source_code')
    if not isinstance(code, str) or not code:
        raise ProvenanceRefused(
            f'Stage4 historical ch{chapter} manifest source code 非法')
    return code


def design_repair_recovery_usage(
        ctx: Any, record: Mapping[str, Any],
        sources: Sequence[DesignUnitSource],
) -> Usage:
    """从所有 repair checkpoints/成本账重建 sealed provenance usage。

    inherited strategy/章节属于 parent artifact，不重复认领；targeted repair
    只认本轮目标章，replan 只认 replacement strategy + affected chapters。
    historical checkpoint 仍认它真正发生的旧 run 成本，而不是把 adoption
    当作 0 次调用。
    """
    source_run = check_run_id(str(record.get('run_id') or ''))
    producer_parent = check_run_id(
        str(record.get('producer_parent_run_id') or ''))
    rows = tuple(_design_source_row(source) for source in sources
                 if source.source_kind == 'repair')
    if not rows:
        raise ProvenanceRefused(
            'Stage4 recovery sealed sources 没有 repair unit，无法重建 usage')
    ledger_path = ledger_module.ledger_path(Path(ctx.novel_dir).parent)
    cost_rows = _strict_jsonl_objects(
        ledger_path, label='Stage4 recovery provenance 成本账')
    buckets: dict[str, dict[str, Any]] = {}
    for row in rows:
        run_id = check_run_id(str(row.get('source_run_id') or ''))
        origin = row.get('origin', 'agent')
        if origin == 'agent':
            if run_id != source_run:
                raise ProvenanceRefused(
                    'Stage4 recovery agent repair source 不属于 sealed source run')
            expected_code = str(record.get('code') or '')
            parent = producer_parent
            require_current = True
        elif origin == 'historical_checkpoint':
            expected_code = _repair_recovery_historical_source_code(ctx, row)
            run_cost_rows = [item for item in cost_rows
                             if item.get('run_id') == run_id]
            try:
                parents = {
                    check_run_id(str(item.get('parent_run_id') or run_id))
                    for item in run_cost_rows}
            except ValueError as exc:
                raise ProvenanceRefused(
                    f'Stage4 historical source ledger parent 非法：{exc}') from exc
            if len(parents) != 1:
                raise ProvenanceRefused(
                    f'Stage4 historical source run={run_id} '
                    f'ledger parent 必须唯一，实得 {sorted(parents)}')
            parent = next(iter(parents))
            require_current = False
        else:
            raise ProvenanceRefused(
                f'Stage4 recovery repair source origin={origin!r} 不可认领 usage')
        bucket = buckets.setdefault(run_id, {
            'parent': parent, 'code': expected_code,
            'require_current': require_current, 'rows': [],
        })
        if (bucket['parent'] != parent or bucket['code'] != expected_code
                or bucket['require_current'] != require_current):
            raise ProvenanceRefused(
                f'Stage4 recovery source run={run_id} code/parent 分类不唯一')
        bucket['rows'].append(row)

    parts: list[Usage | None] = []
    for run_id in sorted(buckets):
        bucket = buckets[run_id]
        source_ledger = CostLedger(
            path=ledger_path, run_id=run_id,
            parent_run_id=bucket['parent'])
        purposes: list[str] = []
        required: list[tuple[str, Mapping[str, object]]] = []
        validated: list[_ValidatedRepairSource] = []
        for row in bucket['rows']:
            checked = _validate_repair_source_checkpoint(
                ctx, row, source_ledger=source_ledger,
                cost_rows=cost_rows, expected_code=bucket['code'],
                require_current_code=bucket['require_current'])
            validated.append(checked)
            purposes.append(checked.purpose)
            required.extend(
                (checked.purpose, identity) for identity in checked.identities)
        _require_repair_source_run_surface_closed(
            ctx, source_run_id=run_id,
            producer_parent_run_id=bucket['parent'],
            expected_code=bucket['code'], cost_rows=cost_rows,
            validated=validated)
        parts.append(source_ledger.usage_for_complete_purposes(
            purposes, sid=ctx.sid, node='stage4.design',
            required_identities=required))
    usage = combine_usage(*parts)
    if usage is None:
        raise ProvenanceRefused(
            'Stage4 recovery repair/replan source usage 有未知成本分段')
    return usage


def _repair_recovery_audit_rows(ctx: Any) -> list[dict[str, Any]]:
    return _strict_jsonl_objects(
        ledger_module.audit_path(Path(ctx.novel_dir).parent),
        label='Stage4 recovery LLM adoption 审计账')


def _repair_recovery_audit_row(
        surface: DesignRepairAggregateRecovery,
        record: Mapping[str, Any], *, artifact_fingerprint: str,
) -> dict[str, Any]:
    binding = _design_repair_recovery_identity_payload(
        record,
        source_journal_sha256=surface.journal_sha256,
        source_commit_sha256=surface.commit_sha256,
        target_file_sha256=surface.expected_file_sha256)
    if artifact_fingerprint != binding['artifact_fingerprint']:
        raise ProvenanceRefused(
            'Stage4 repair recovery artifact fingerprint 与 stable binding 漂移')
    recovery_id = agent_runs.canonical_sha256(binding)
    return {
        'kind': 'stage4_repair_aggregate_recovery',
        'stage4_repair_recovery_id': recovery_id,
        'binding_kind': binding.pop('kind'),
        **binding,
    }


def _repair_recovery_attempt_audit_row_for(
        canonical: Mapping[str, Any], *, target_run_id: str,
        target_parent_run_id: str,
) -> dict[str, Any]:
    target = check_run_id(target_run_id)
    target_parent = check_run_id(target_parent_run_id)
    recovery_id = str(canonical['stage4_repair_recovery_id'])
    attempt_id = _design_repair_recovery_attempt_id(
        recovery_id=recovery_id,
        target_run_id=target,
        target_parent_run_id=target_parent)
    return {
        'kind': 'stage4_repair_aggregate_recovery_attempt',
        'stage4_repair_recovery_attempt_id': attempt_id,
        'stage4_repair_recovery_id': recovery_id,
        'sid': canonical['sid'], 'node': 'stage4.design',
        'source_run_id': canonical['source_run_id'],
        'source_journal_sha256': canonical['source_journal_sha256'],
        'source_commit_sha256': canonical['source_commit_sha256'],
        'target_run_id': target,
        'target_parent_run_id': target_parent,
        'target_file_sha256': canonical['target_file_sha256'],
        'artifact': canonical['artifact'],
        'artifact_fingerprint': canonical['artifact_fingerprint'],
    }


def _repair_recovery_attempt_audit_row(
        ctx: Any, canonical: Mapping[str, Any],
) -> dict[str, Any]:
    return _repair_recovery_attempt_audit_row_for(
        canonical, target_run_id=ctx.run_id,
        target_parent_run_id=ctx.parent_run_id)


def _stamped_audit_row(ledger: CostLedger, row: Mapping[str, Any]) -> dict[str, Any]:
    return {**ledger._identity_fields(), **dict(row)}  # noqa: SLF001


def _repair_recovery_adoption_rows(
        ctx: Any, record: Mapping[str, Any], *, source_ledger: CostLedger,
        artifact_fingerprint: str,
) -> tuple[tuple[dict[str, Any], dict[str, Any]], ...]:
    """重建 source agent 的 exact unstamped/stamped adoption 行。"""
    expected: list[tuple[dict[str, Any], dict[str, Any]]] = []
    for source in record['sources']:
        if (source['source_kind'] != 'repair'
                or source.get('origin', 'agent') != 'agent'
                or source['source_run_id'] != source_ledger.run_id):
            continue
        purpose = str(source['purpose'])
        attempt_id = str(source.get('attempt_id') or '')
        chapter = source.get('chapter')
        if not attempt_id:
            raise ProvenanceRefused(
                f'{purpose}: Stage4 recovery source adoption 缺 attempt_id')
        adoption_id = ledger_module._adoption_id(  # noqa: SLF001
            run_id=source_ledger.run_id, node='stage4.design',
            purpose=purpose, attempt_id=attempt_id,
            artifact=STAGE4_BLIND_CANDIDATE,
            artifact_fingerprint=artifact_fingerprint,
            chapter=chapter)
        row = {
            'kind': 'artifact_adoption',
            'adoption_id': adoption_id,
            'sid': ctx.sid,
            'node': 'stage4.design',
            'purpose': purpose,
            'attempt_id': attempt_id,
            'artifact': STAGE4_BLIND_CANDIDATE,
            'artifact_fingerprint': artifact_fingerprint,
            'chapter': chapter,
        }
        expected.append((row, _stamped_audit_row(source_ledger, row)))
    ids = [row['adoption_id'] for row, _stamped in expected]
    if len(ids) != len(set(ids)):
        raise ProvenanceRefused(
            'Stage4 recovery source rows 重建出重复 adoption_id')
    return tuple(expected)


def _semantic_audit_row(
        row: Mapping[str, Any], expected: Mapping[str, Any], *, label: str,
) -> dict[str, Any]:
    if (set(row) != set(expected) | {'at'}
            or not isinstance(row.get('at'), str)
            or not str(row['at']).strip()):
        raise ProvenanceRefused(f'{label} audit 字段/at 不闭合')
    semantic = {key: value for key, value in row.items() if key != 'at'}
    if semantic != dict(expected):
        raise ProvenanceRefused(f'{label} audit 与 exact identity 漂移')
    return semantic


def _preclaim_repair_recovery_adoptions(
        ctx: Any, record: Mapping[str, Any], *, source_ledger: CostLedger,
        artifact_fingerprint: str, require_present: bool = False,
) -> tuple[tuple[dict[str, Any], dict[str, Any]], ...]:
    """source adoption 在全局 identity 空间必须 0-or-exact。"""
    rows = _repair_recovery_audit_rows(ctx)
    expected = _repair_recovery_adoption_rows(
        ctx, record, source_ledger=source_ledger,
        artifact_fingerprint=artifact_fingerprint)
    expected_ids = {row['adoption_id'] for row, _stamped in expected}
    for _row, stamped in expected:
        identity = stamped['adoption_id']
        matches = [row for row in rows if row.get('adoption_id') == identity]
        if len(matches) > 1 or (require_present and len(matches) != 1):
            state = '恰一' if require_present else '0-or-1'
            raise ProvenanceRefused(
                f'Stage4 recovery source adoption_id={identity} '
                f'必须{state}条，实得 {len(matches)}')
        for actual in matches:
            _semantic_audit_row(
                actual, stamped,
                label='Stage4 recovery source artifact adoption')

    # 同 source 的正式 candidate adoption 必须是 sealed sources 的
    # 完整闭集。fingerprint 是 exact 行的内容，不是先缩小全局
    # 闭集的过滤器；否则 all-historical 的空集会放过一条
    # 另一 fingerprint 的 orphan adoption。
    relevant = [
        row for row in rows
        if (row.get('kind') == 'artifact_adoption'
            and row.get('run_id') == source_ledger.run_id
            and row.get('sid') == ctx.sid
            and row.get('node') == 'stage4.design'
            and row.get('artifact') == STAGE4_BLIND_CANDIDATE)
    ]
    if any(row.get('adoption_id') not in expected_ids for row in relevant):
        raise ProvenanceRefused(
            'Stage4 recovery source candidate 存在 sealed sources 之外的 '
            'artifact adoption')
    return expected


def _expected_recovery_attempt_audit(
        row: Mapping[str, Any], canonical: Mapping[str, Any],
) -> dict[str, Any]:
    try:
        target = check_run_id(str(row.get('target_run_id') or ''))
        parent = check_run_id(str(row.get('target_parent_run_id') or ''))
    except ValueError as exc:
        raise ProvenanceRefused(
            f'Stage4 recovery attempt target identity 非法：{exc}') from exc
    attempt = _repair_recovery_attempt_audit_row_for(
        canonical, target_run_id=target, target_parent_run_id=parent)
    ledger = CostLedger(path=None, run_id=target, parent_run_id=parent)
    return _stamped_audit_row(ledger, attempt)


def _preclaim_repair_recovery_audit(
        ctx: Any, *, source_ledger: CostLedger,
        canonical: Mapping[str, Any], attempt: Mapping[str, Any],
        target_ledger: CostLedger | None = None,
) -> None:
    rows = _repair_recovery_audit_rows(ctx)
    expected_canonical = _stamped_audit_row(source_ledger, canonical)
    effective_target_ledger = target_ledger or ctx.ledger
    expected_attempt = _stamped_audit_row(effective_target_ledger, attempt)
    stable_id = canonical['stage4_repair_recovery_id']
    current_attempt_id = attempt['stage4_repair_recovery_attempt_id']

    # 先按全局 identity key 查；不能先按 source 过滤，否则伪造另一 source 的
    # orphan 会在候选写入后才被 append 口发现。
    linked = [row for row in rows
              if row.get('stage4_repair_recovery_id') == stable_id]
    if any(row.get('kind') not in {
            'stage4_repair_aggregate_recovery',
            'stage4_repair_aggregate_recovery_attempt',
    } for row in linked):
        raise ProvenanceRefused(
            'Stage4 recovery stable identity 被未知 audit kind 占用')
    canonical_by_id = [row for row in linked
                       if row.get('kind')
                       == 'stage4_repair_aggregate_recovery']
    if len(canonical_by_id) > 1:
        raise ProvenanceRefused('Stage4 canonical recovery identity 重复')
    for row in canonical_by_id:
        _semantic_audit_row(
            row, expected_canonical, label='Stage4 canonical recovery')
    for row in linked:
        if row.get('kind') != 'stage4_repair_aggregate_recovery_attempt':
            continue
        expected_old = _expected_recovery_attempt_audit(row, canonical)
        _semantic_audit_row(
            row, expected_old, label='Stage4 recovery attempt')

    current_by_id = [
        row for row in rows
        if row.get('stage4_repair_recovery_attempt_id') == current_attempt_id]
    if len(current_by_id) > 1:
        raise ProvenanceRefused('Stage4 当前 recovery attempt identity 重复')
    for row in current_by_id:
        _semantic_audit_row(
            row, expected_attempt, label='Stage4 当前 recovery attempt')

    # target run 本身也是审计唯一键的一部分。不能通过换
    # parent/source 重算另一枚 attempt_id，让同一 target run 代表
    # 两次不同的 recovery committer。
    target_rows = [
        row for row in rows
        if (row.get('kind') == 'stage4_repair_aggregate_recovery_attempt'
            and row.get('target_run_id') == attempt['target_run_id'])
    ]
    if len(target_rows) > 1:
        raise ProvenanceRefused(
            'Stage4 同一 recovery target run 存在多条 attempt relation')
    for row in target_rows:
        _semantic_audit_row(
            row, expected_attempt,
            label='Stage4 recovery target run relation')

    # 再收紧同 source 的全局 0-or-exact 族：只能有一个 stable binding；
    # R1 audit 后崩再由 R2 接手时，旧 attempt 合法保留，但必须逐条能按其
    # target run/parent 重建出完整 exact 行。
    source_rows = [row for row in rows
                   if row.get('kind') == 'stage4_repair_aggregate_recovery'
                   and row.get('source_run_id') == canonical['source_run_id']]
    if len(source_rows) > 1:
        raise ProvenanceRefused(
            'Stage4 同一 source run 有多条 canonical recovery identity')
    for row in source_rows:
        _semantic_audit_row(
            row, expected_canonical, label='Stage4 source recovery family')
    attempts = [
        row for row in rows
        if row.get('kind') == 'stage4_repair_aggregate_recovery_attempt'
        and row.get('source_run_id') == canonical['source_run_id']]
    by_id: dict[str, list[dict[str, Any]]] = {}
    for row in attempts:
        identity = str(row.get('stage4_repair_recovery_attempt_id') or '')
        by_id.setdefault(identity, []).append(row)
        expected_old = _expected_recovery_attempt_audit(row, canonical)
        _semantic_audit_row(
            row, expected_old, label='Stage4 source recovery attempt family')
    if any(len(group) != 1 for group in by_id.values()):
        raise ProvenanceRefused('Stage4 recovery attempt identity 重复')
    for identity in by_id:
        globally_same_id = [
            row for row in rows
            if row.get('stage4_repair_recovery_attempt_id') == identity]
        if len(globally_same_id) != 1:
            raise ProvenanceRefused(
                'Stage4 旧 recovery attempt_id 被另一 source/stable 行复用')


def _require_repair_recovery_audit(
        ctx: Any, *, source_ledger: CostLedger,
        canonical: Mapping[str, Any], attempt: Mapping[str, Any],
        target_ledger: CostLedger | None = None,
) -> None:
    _preclaim_repair_recovery_audit(
        ctx, source_ledger=source_ledger,
        canonical=canonical, attempt=attempt,
        target_ledger=target_ledger)
    rows = _repair_recovery_audit_rows(ctx)
    expected_canonical = _stamped_audit_row(source_ledger, canonical)
    expected_attempt = _stamped_audit_row(target_ledger or ctx.ledger, attempt)
    canonical_matches = [row for row in rows
                         if row.get('stage4_repair_recovery_id')
                         == canonical['stage4_repair_recovery_id']
                         and row.get('kind')
                         == 'stage4_repair_aggregate_recovery']
    attempt_matches = [row for row in rows
                       if row.get('stage4_repair_recovery_attempt_id')
                       == attempt['stage4_repair_recovery_attempt_id']]
    if len(canonical_matches) != 1 or len(attempt_matches) != 1:
        raise ProvenanceRefused(
            'Stage4 repair committed 前需 canonical + 当前 target attempt 各一条 exact audit')
    _semantic_audit_row(
        canonical_matches[0], expected_canonical,
        label='Stage4 committed canonical recovery')
    _semantic_audit_row(
        attempt_matches[0], expected_attempt,
        label='Stage4 committed current recovery attempt')


def _require_committed_design_repair_recovery_audit(
        ctx: Any, *, target: Path, record: Mapping[str, Any],
) -> None:
    """下游读取 recovered aggregate 时重验 canonical/actual committer 两条账。"""
    recovery = record.get('recovery')
    if not isinstance(recovery, dict):
        raise ProvenanceRefused(
            f'{target} 声称跨 run recovery 但缺 typed recovery lineage')
    source_run = check_run_id(str(record.get('run_id') or ''))
    producer_parent = check_run_id(
        str(record.get('producer_parent_run_id') or ''))
    target_run = check_run_id(str(recovery.get('target_run_id') or ''))
    target_parent = check_run_id(
        str(recovery.get('target_parent_run_id') or ''))
    _require_design_repair_recovery_target_identity_clean(
        ctx, target_run, stage4_design_only=True)
    surface = DesignRepairAggregateRecovery(
        source_run_id=source_run,
        producer_parent_run_id=producer_parent,
        journal_path=target.resolve(),
        journal_sha256=str(recovery.get('source_journal_sha256') or ''),
        commit_sha256=str(recovery.get('source_commit_sha256') or ''),
        target_state='expected',
        candidate_file_sha256=str(recovery.get('target_file_sha256') or ''),
        expected_file_sha256=str(recovery.get('target_file_sha256') or ''),
    )
    source_ledger = _repair_recovery_source_ledger(
        ctx, record, source_run_id=source_run)
    _preclaim_repair_recovery_adoptions(
        ctx, record, source_ledger=source_ledger,
        artifact_fingerprint=str(record['primary_payload_fingerprint']),
        require_present=True)
    canonical = _repair_recovery_audit_row(
        surface, record,
        artifact_fingerprint=str(record['primary_payload_fingerprint']))
    attempt = _repair_recovery_attempt_audit_row_for(
        canonical, target_run_id=target_run,
        target_parent_run_id=target_parent)
    target_ledger = CostLedger(
        path=ledger_module.ledger_path(Path(ctx.novel_dir).parent),
        run_id=target_run, parent_run_id=target_parent)
    _require_repair_recovery_audit(
        ctx, source_ledger=source_ledger, target_ledger=target_ledger,
        canonical=canonical, attempt=attempt)


def preclaim_design_repair_aggregate_recovery(
        ctx: Any, *, source_run_id: str, journal_sha256: str,
        source_commit_sha256: str,
) -> DesignRepairAggregateRecovery:
    """任何正式候选写入前完成 target clean、source 与全局 0-or-exact。"""
    _require_ownership(ctx)
    require_design_repair_recovery_target_clean(ctx)
    surface, record = load_prepared_design_repair_aggregate_recovery(
        ctx, source_run_id=source_run_id,
        journal_sha256=journal_sha256,
        source_commit_sha256=source_commit_sha256)
    source_ledger = _repair_recovery_source_ledger(
        ctx, record, source_run_id=surface.source_run_id)
    _preclaim_repair_recovery_adoptions(
        ctx, record, source_ledger=source_ledger,
        artifact_fingerprint=str(record['primary_payload_fingerprint']))
    canonical = _repair_recovery_audit_row(
        surface, record,
        artifact_fingerprint=record['primary_payload_fingerprint'])
    attempt = _repair_recovery_attempt_audit_row(ctx, canonical)
    _preclaim_repair_recovery_audit(
        ctx, source_ledger=source_ledger,
        canonical=canonical, attempt=attempt)
    return surface


def note_design_repair_aggregate_recovery(
        ctx: Any, *, source_run_id: str, journal_sha256: str,
        source_commit_sha256: str, primary_payload: Mapping[str, Any],
) -> DesignRepairAggregateRecovery:
    """幂等补 source adoption、source-stable identity 与本 target attempt。"""
    _require_ownership(ctx)
    require_design_repair_recovery_target_clean(ctx)
    surface, record = load_prepared_design_repair_aggregate_recovery(
        ctx,
        source_run_id=source_run_id,
        journal_sha256=journal_sha256,
        source_commit_sha256=source_commit_sha256,
    )
    primary = dict(primary_payload)
    if record['primary_payload_sha256'] != agent_runs.canonical_sha256(primary):
        raise ProvenanceRefused('Stage4 repair recovery adoption 的候选与 sealed payload 漂移')
    fingerprint = prov.projection_fingerprint(primary)
    source_ledger = _repair_recovery_source_ledger(
        ctx, record, source_run_id=surface.source_run_id)
    adoption_rows = _preclaim_repair_recovery_adoptions(
        ctx, record, source_ledger=source_ledger,
        artifact_fingerprint=fingerprint)
    canonical = _repair_recovery_audit_row(
        surface, record, artifact_fingerprint=fingerprint)
    attempt = _repair_recovery_attempt_audit_row(ctx, canonical)
    _preclaim_repair_recovery_audit(
        ctx, source_ledger=source_ledger,
        canonical=canonical, attempt=attempt)
    for row, _stamped in adoption_rows:
        source_ledger._append_audit_idempotent(  # noqa: SLF001
            {**row, 'at': _now()}, identity_key='adoption_id',
            require_equal=True)
    source_ledger._append_audit_idempotent(  # noqa: SLF001
        {**canonical, 'at': _now()},
        identity_key='stage4_repair_recovery_id',
        require_equal=True)
    ctx.ledger._append_audit_idempotent(  # noqa: SLF001
        {**attempt, 'at': _now()},
        identity_key='stage4_repair_recovery_attempt_id',
        require_equal=True)
    _require_repair_recovery_audit(
        ctx, source_ledger=source_ledger,
        canonical=canonical, attempt=attempt)
    _preclaim_repair_recovery_adoptions(
        ctx, record, source_ledger=source_ledger,
        artifact_fingerprint=fingerprint, require_present=True)
    return surface


def mark_design_repair_aggregate_committed(
        ctx: Any, *, primary_payload: Mapping[str, Any],
        finalization_payload: Mapping[str, Any],
        source_run_id: str | None = None,
        source_journal_sha256: str | None = None,
        source_commit_sha256: str | None = None,
) -> Path:
    _require_ownership(ctx)
    recovery_surface = None
    if source_run_id is None:
        if (source_journal_sha256 is not None
                or source_commit_sha256 is not None):
            raise ProvenanceRefused('same-run repair commit 不接受 foreign journal 身份')
        target = _design_repair_aggregate_commit_path(ctx)
        owner_run_id = ctx.run_id
    else:
        if source_journal_sha256 is None or source_commit_sha256 is None:
            raise ProvenanceRefused('foreign repair commit 必须绑定 raw/commit SHA')
        recovery_surface, _record = load_prepared_design_repair_aggregate_recovery(
            ctx,
            source_run_id=source_run_id,
            journal_sha256=source_journal_sha256,
            source_commit_sha256=source_commit_sha256,
        )
        target = recovery_surface.journal_path
        owner_run_id = recovery_surface.source_run_id
    with _DESIGN_AGGREGATE_LOCK, process_append_lock(target):
        if recovery_surface is None:
            current = _validate_design_repair_aggregate_state(ctx, target)
        else:
            current = _validate_foreign_design_repair_aggregate_envelope(
                target, run_id=owner_run_id, sid=ctx.sid)
            current = _validate_design_repair_aggregate_contents(
                ctx, target, current, run_id=owner_run_id)
        if (current['primary_payload_sha256']
                != agent_runs.canonical_sha256(dict(primary_payload))
                or current['finalization_sha256']
                != agent_runs.canonical_sha256(dict(finalization_payload))):
            raise ProvenanceRefused('repair aggregate committed 候选/finalization 漂移')
        post = agent_runs.file_sha256(ctx, STAGE4_BLIND_CANDIDATE)
        if post is None:
            raise ProvenanceRefused('repair aggregate committed 前候选未落盘')
        if (recovery_surface is not None
                and post != recovery_surface.expected_file_sha256):
            raise ProvenanceRefused(
                'repair aggregate recovery committed 前正式候选不是 sealed target')
        landed = {STAGE4_BLIND_CANDIDATE:
                  agent_runs.canonical_sha256(dict(primary_payload))}
        post_rows = {STAGE4_BLIND_CANDIDATE: post}
        recovery_payload = None
        if recovery_surface is not None:
            source_ledger = _repair_recovery_source_ledger(
                ctx, current, source_run_id=owner_run_id)
            _preclaim_repair_recovery_adoptions(
                ctx, current, source_ledger=source_ledger,
                artifact_fingerprint=current['primary_payload_fingerprint'],
                require_present=True)
            canonical = _repair_recovery_audit_row(
                recovery_surface, current,
                artifact_fingerprint=current['primary_payload_fingerprint'])
            attempt = _repair_recovery_attempt_audit_row(ctx, canonical)
            _require_repair_recovery_audit(
                ctx, source_ledger=source_ledger,
                canonical=canonical, attempt=attempt)
            recovery_payload = {
                'stage4_repair_recovery_id':
                    canonical['stage4_repair_recovery_id'],
                'stage4_repair_recovery_attempt_id':
                    attempt['stage4_repair_recovery_attempt_id'],
                'target_run_id': check_run_id(ctx.run_id),
                'target_parent_run_id': check_run_id(ctx.parent_run_id),
                'source_journal_sha256': recovery_surface.journal_sha256,
                'source_commit_sha256': recovery_surface.commit_sha256,
                'target_file_sha256': post,
            }
        if recovery_surface is None:
            expected = {
                (row['purpose'], row['attempt_id'], row['chapter'])
                for row in current['sources']
                if (row['source_kind'] == 'repair'
                    and row.get('origin', 'agent') == 'agent'
                    and row['source_run_id'] == owner_run_id)
            }
            relevant = [row for row in _read_adoption_events(ctx)
                        if (row.get('kind') == 'artifact_adoption'
                            and row.get('run_id') == owner_run_id
                            and row.get('sid') == ctx.sid
                            and row.get('node') == 'stage4.design'
                            and row.get('artifact') == STAGE4_BLIND_CANDIDATE
                            and row.get('artifact_fingerprint')
                            == current['primary_payload_fingerprint'])]
            actual = {
                (row.get('purpose'), row.get('attempt_id'), row.get('chapter'))
                for row in relevant}
            if len(relevant) != len(expected) or actual != expected:
                raise ProvenanceRefused(
                    'repair aggregate 提交前必须恰有每个当前 run '
                    '目标章一条 adoption；historical checkpoint 由 '
                    'adoption manifest 单独证明')
        if current['state'] == 'committed':
            if (current.get('landed_payload_sha256s') != landed
                    or current.get('post_file_sha256s') != post_rows
                    or current.get('recovery') != recovery_payload):
                raise ProvenanceRefused('repair aggregate 已 committed 结果漂移')
            return target
        updated = dict(current)
        updated.update({
            'state': 'committed', 'committed_at': _now(),
            'landed_payload_sha256s': landed,
            'post_file_sha256s': post_rows,
        })
        if current.get('version') == _DESIGN_REPAIR_AGGREGATE_VERSION:
            updated['recovery'] = recovery_payload
        updated['commit_sha256'] = _design_repair_aggregate_hash(updated)
        _atomic_json(target, updated)
    return target


def verify_design_aggregate_primary(
        record: Mapping[str, Any], primary_payload: Mapping[str, Any],
) -> bool:
    return record.get('primary_payload_sha256') == agent_runs.canonical_sha256(
        dict(primary_payload))


def verify_design_aggregate_finalization(
        record: Mapping[str, Any], finalization_payload: Mapping[str, Any],
) -> bool:
    return record.get('finalization_sha256') == agent_runs.canonical_sha256(
        dict(finalization_payload))


def restore_design_aggregate_payloads(
        ctx: Any, record: Mapping[str, Any] | None = None,
) -> tuple[dict[str, Any], dict[str, Any]]:
    """恢复 prepare 时封存的已验证整书候选与确定性终态。"""
    current = dict(record) if record is not None else design_aggregate_state(ctx)
    if current is None:
        raise ProvenanceRefused(
            f'run={ctx.run_id} 缺 design aggregate journal，不能恢复提交候选')
    validated = _validate_design_aggregate_state(
        ctx, _design_aggregate_commit_path(ctx))
    if current != validated:
        raise ProvenanceRefused('design aggregate journal 在读取候选期间漂移')
    return (
        copy.deepcopy(validated['primary_payload']),
        copy.deepcopy(validated['finalization_payload']),
    )


def restore_design_aggregate_adoptions(
        ctx: Any, record: Mapping[str, Any], *,
        units: Sequence[DesignUnitRequest],
) -> tuple[tuple[str, int | None, str], ...]:
    """恢复 N+1 attempts，并用当前请求逐项重验 run/checkpoint/ledger。"""
    _require_ownership(ctx)
    current = _validate_design_aggregate_state(
        ctx, _design_aggregate_commit_path(ctx))
    if dict(record) != current:
        raise ProvenanceRefused('传入的 design aggregate 不是当前盘上 journal')
    normalized_units = _normalized_design_units(units)
    by_key = {str(row['unit_key']): row for row in current['adoptions']}
    from novel.stages.stage4.design_rebuild import preserved_sources
    preserved = {design_unit_key(row.chapter) for row in preserved_sources(ctx, current)}
    if set(by_key) & preserved or set(by_key) | preserved != {design_unit_key(unit.chapter) for unit in normalized_units}:
        raise ProvenanceRefused('design aggregate 的 unit 集合与当前请求漂移')
    restored_rows: list[tuple[str, int | None, str]] = []
    for unit in normalized_units:
        if design_unit_key(unit.chapter) in preserved:
            continue
        row = by_key[design_unit_key(unit.chapter)]
        source_run_id = str(row.get('source_run_id') or ctx.run_id)
        if source_run_id == ctx.run_id:
            restored = restore_design_unit_checkpoint(ctx, unit)
            ledger = ctx.ledger
        else:
            from novel.llm import design_adoption
            source = design_adoption.restore(ctx, unit, source_run_id)
            restored, ledger = source.checkpoint, source.context.ledger
        if restored is None:
            raise ProvenanceRefused(
                f'{design_unit_purpose(unit.chapter)}: aggregate 所指 checkpoint 缺失')
        attempt_id = str(row['attempt_id'])
        identity = row['request_identity']
        if (restored.acceptance.get('attempt_id') != attempt_id
                or restored.revision != row.get('checkpoint_revision')
                or restored.checkpoint_sha256 != row.get('checkpoint_sha256')
                or restored.acceptance.get('provider_session_id')
                != row.get('provider_session_id')
                or ledger.request_identity_for_attempt(attempt_id)
                != identity):
            raise ProvenanceRefused(
                f'{design_unit_purpose(unit.chapter)}: aggregate '
                'adoption 与当前 checkpoint/ledger 漂移')
        restored_rows.append((
            design_unit_purpose(unit.chapter), unit.chapter, attempt_id))
    return tuple(restored_rows)


def design_aggregate_target_state(
        ctx: Any, record: Mapping[str, Any], rel: str, *,
        expected_file_sha256: str | None = None,
        expected_absent: bool = False,
) -> str:
    """prepared 尾窗中只允许写前基线或本 run 已验证目标。"""
    _require_ownership(ctx)
    current = _validate_design_aggregate_state(
        ctx, _design_aggregate_commit_path(ctx))
    if dict(record) != current:
        raise ProvenanceRefused('传入的 design aggregate 不是当前盘上 journal')
    if rel != STAGE4_BLIND_CANDIDATE:
        raise ProvenanceRefused(f'{rel} 不属于 design aggregate touched_paths')
    if expected_absent and expected_file_sha256 is not None:
        raise ProvenanceRefused(f'{rel} 不能同时声明 expected SHA/absent')
    if not isinstance(expected_absent, bool):
        raise ProvenanceRefused(f'{rel} expected_absent 必须是 bool')
    if (expected_file_sha256 is not None
            and not agent_runs.is_sha256(expected_file_sha256)):
        raise ProvenanceRefused(f'{rel} expected file SHA 非法')
    current_sha = agent_runs.file_sha256(ctx, rel)
    if expected_absent and current_sha is None:
        return 'expected'
    if expected_file_sha256 is not None and current_sha == expected_file_sha256:
        return 'expected'
    if current_sha == current['baselines'][rel]:
        return 'baseline'
    raise ProvenanceRefused(
        f'{rel} 在 design aggregate prepared 尾窗中既不是基线，'
        '也不是本 run 已验证目标；拒绝覆盖第三方漂移')


def _read_adoption_events(ctx: Any) -> list[dict[str, Any]]:
    if ctx.ledger.path is None:
        return [dict(row) for row in ctx.ledger.audit_events]
    target = ctx.ledger.path.with_name('llm_audit.jsonl')
    if not target.is_file():
        return []
    try:
        with process_append_lock(target):
            raw = target.read_bytes()
    except OSError as exc:
        raise ProvenanceRefused(f'读不了 Stage4 adoption 审计账：{exc}') from exc
    lines = raw.split(b'\n')
    partial_tail = bool(raw) and not raw.endswith(b'\n')
    rows: list[dict[str, Any]] = []
    for index, encoded in enumerate(lines):
        if not encoded.strip():
            continue
        try:
            value = json.loads(encoded.decode('utf-8'))
        except (UnicodeError, json.JSONDecodeError) as exc:
            if partial_tail and index == len(lines) - 1:
                break
            raise ArtifactUnreadable(
                f'LLM adoption 审计第 {index + 1} 行损坏：{exc}') from exc
        if isinstance(value, dict):
            rows.append(value)
    return rows


def _require_design_aggregate_adoptions(
        ctx: Any, record: Mapping[str, Any],
        *, require_present: bool = True,
) -> None:
    expected_rows = [ctx.ledger.artifact_adoption_payload(
        sid=ctx.sid, node='stage4.design', purpose=row['purpose'], attempt_id=row['attempt_id'],
        artifact=STAGE4_BLIND_CANDIDATE, artifact_fingerprint=record['primary_payload_fingerprint'],
        chapter=row.get('chapter')) for row in record['adoptions']]
    expected = {row['adoption_id']: row for row in expected_rows}
    relevant = [
        row for row in ledger_module.read_strict_audit_events(ctx.ledger)
        if (row.get('kind') == 'artifact_adoption'
            and row.get('run_id') == ctx.run_id
            and row.get('sid') == ctx.sid
            and row.get('node') == 'stage4.design'
            and row.get('artifact') == STAGE4_BLIND_CANDIDATE
            )
    ]
    actual = [row.get('adoption_id') for row in relevant]
    if (len(actual) != len(set(actual)) or not set(actual).issubset(expected)
            or (require_present and set(actual) != set(expected))):
        raise ProvenanceRefused(
            'Stage4 design committed 前必须恰有 strategy + ch1..N '
            f'各一条当前主产物 adoption；期望 {len(expected)}，'
            f'实得 {len(relevant)}')
    for row in relevant:
        _semantic_audit_row(row, expected[row['adoption_id']], label='Stage4A 原单元采用')


def mark_design_aggregate_committed(
        ctx: Any, *, primary_payload: Mapping[str, Any],
        finalization_payload: Mapping[str, Any],
        landed_payloads: Mapping[str, Mapping[str, Any]],
        recovery: Mapping[str, Any] | None = None,
) -> Path:
    """typed readback 与 N+1 adoptions 都闭合后原子提升为 committed。"""
    _require_ownership(ctx)
    target = _design_aggregate_commit_path(ctx)
    with _DESIGN_AGGREGATE_LOCK:
        with process_append_lock(target):
            if not target.is_file():
                raise ProvenanceRefused(f'{target} 缺 prepared aggregate journal')
            current = _validate_design_aggregate_state(ctx, target)
            if (not verify_design_aggregate_primary(current, primary_payload)
                    or not verify_design_aggregate_finalization(
                        current, finalization_payload)):
                raise ProvenanceRefused(
                    'design aggregate 正式 readback/finalization 与 prepared 漂移')
            if set(landed_payloads) != {STAGE4_BLIND_CANDIDATE}:
                raise ProvenanceRefused(
                    f'landed payloads 必须恰好覆盖 {STAGE4_BLIND_CANDIDATE}')
            landed_sha = {
                STAGE4_BLIND_CANDIDATE: agent_runs.canonical_sha256(
                    dict(landed_payloads[STAGE4_BLIND_CANDIDATE])),
            }
            post = {
                STAGE4_BLIND_CANDIDATE:
                    agent_runs.file_sha256(ctx, STAGE4_BLIND_CANDIDATE),
            }
            if post[STAGE4_BLIND_CANDIDATE] is None:
                raise ProvenanceRefused('committed 前 blind candidate 仍未落盘')
            _require_design_aggregate_adoptions(ctx, current)
            if current['state'] == 'committed':
                if (current.get('landed_payload_sha256s') != landed_sha
                        or current.get('post_file_sha256s') != post):
                    raise ProvenanceRefused(
                        'committed aggregate 与当前 typed/file readback 漂移')
                return target
            payload = {
                **current, 'state': 'committed', 'committed_at': _now(),
                'landed_payload_sha256s': landed_sha,
                'post_file_sha256s': post,
            }
            if current['version'] == 2:
                payload['recovery'] = dict(recovery) if recovery is not None else None
                aggregate_runs._verify_recovery_receipt(ctx, design_aggregate_recovery_record(payload))
            payload['commit_sha256'] = _design_aggregate_hash(payload)
            _atomic_json(target, payload)
    return target


def verify_design_aggregate_committed_delivery(
        ctx: Any, record: Mapping[str, Any], *,
        primary_payload: Mapping[str, Any],
        finalization_payload: Mapping[str, Any],
        landed_payloads: Mapping[str, Mapping[str, Any]],
) -> None:
    _require_ownership(ctx)
    current = _validate_design_aggregate_state(
        ctx, _design_aggregate_commit_path(ctx))
    if dict(record) != current or current['state'] != 'committed':
        raise ProvenanceRefused('传入的 journal 不是当前 committed aggregate')
    if (not verify_design_aggregate_primary(current, primary_payload)
            or not verify_design_aggregate_finalization(
                current, finalization_payload)):
        raise ProvenanceRefused('committed aggregate 主产物/finalization 漂移')
    if set(landed_payloads) != {STAGE4_BLIND_CANDIDATE}:
        raise ProvenanceRefused('committed aggregate typed readback 集合漂移')
    landed_sha = {
        STAGE4_BLIND_CANDIDATE: agent_runs.canonical_sha256(
            dict(landed_payloads[STAGE4_BLIND_CANDIDATE])),
    }
    post = {
        STAGE4_BLIND_CANDIDATE:
            agent_runs.file_sha256(ctx, STAGE4_BLIND_CANDIDATE),
    }
    if (landed_sha != current.get('landed_payload_sha256s')
            or post != current.get('post_file_sha256s')):
        raise ProvenanceRefused('committed aggregate 后正式 payload/file 被改写')
    _require_design_aggregate_adoptions(ctx, current)


def prepare_commit(
        ctx: Any, *, primary_artifact: str,
        primary_payload: Mapping[str, Any],
        finalization_payload: Mapping[str, Any],
        touched_paths: Sequence[str], attempt_id: str,
        spec: agent_runs.CommitBundleSpec | None = None,
) -> dict[str, Any]:
    _require_ownership(ctx)
    spec = spec or _commit_spec(ctx.node)
    if primary_artifact != spec.primary_artifact:
        raise ProvenanceRefused(
            f'{ctx.node}: primary artifact 必须是 {spec.primary_artifact}')
    refuse_foreign_commit_reservations(ctx)
    if isinstance(spec, aggregate_runs.AggregateCommitSpec):
        return aggregate_runs.prepare_commit(
            ctx, spec, primary_payload=primary_payload,
            finalization_payload=finalization_payload, touched_paths=touched_paths)
    return agent_runs.prepare_commit(
        ctx, spec, primary_payload=primary_payload,
        finalization_payload=finalization_payload,
        touched_paths=touched_paths, attempt_id=attempt_id)


def target_state(
        ctx: Any, record: Mapping[str, Any], rel: str, *,
        expected_file_sha256: str | None = None,
        expected_absent: bool = False,
        spec: agent_runs.CommitBundleSpec | None = None,
) -> str:
    _require_ownership(ctx)
    backend = aggregate_runs if isinstance(spec, aggregate_runs.AggregateCommitSpec) else agent_runs
    return backend.target_state(
        ctx, spec or _commit_spec(ctx.node), record, rel,
        expected_file_sha256=expected_file_sha256,
        expected_absent=expected_absent)


def verify_commit_primary(
        record: Mapping[str, Any], primary_payload: Mapping[str, Any],
) -> bool:
    return agent_runs.verify_commit_primary(record, primary_payload)


def verify_commit_finalization(
        record: Mapping[str, Any], finalization_payload: Mapping[str, Any],
) -> bool:
    return agent_runs.verify_commit_finalization(record, finalization_payload)


def restore_commit_adoption(
        ctx: Any, record: Mapping[str, Any],
        spec: agent_runs.CommitBundleSpec | None = None,
) -> tuple[str, str]:
    _require_ownership(ctx)
    return agent_runs.restore_commit_adoption(
        ctx, spec or _commit_spec(ctx.node), record)


def mark_committed(
        ctx: Any, *, primary_payload: Mapping[str, Any],
        finalization_payload: Mapping[str, Any],
        landed_payloads: Mapping[str, Mapping[str, Any]],
        spec: agent_runs.CommitBundleSpec | None = None,
) -> Path:
    _require_ownership(ctx)
    backend = aggregate_runs if isinstance(spec, aggregate_runs.AggregateCommitSpec) else agent_runs
    return backend.mark_committed(
        ctx, spec or _commit_spec(ctx.node), primary_payload=primary_payload,
        finalization_payload=finalization_payload,
        landed_payloads=landed_payloads)


def verify_committed_delivery(
        ctx: Any, record: Mapping[str, Any], *,
        primary_payload: Mapping[str, Any],
        finalization_payload: Mapping[str, Any],
        landed_payloads: Mapping[str, Mapping[str, Any]],
        spec: 'agent_runs.CommitBundleSpec | None' = None,
) -> None:
    _require_ownership(ctx)
    backend = aggregate_runs if isinstance(spec, aggregate_runs.AggregateCommitSpec) else agent_runs
    backend.verify_committed_delivery(
        ctx, spec or _commit_spec(ctx.node), record,
        primary_payload=primary_payload,
        finalization_payload=finalization_payload,
        landed_payloads=landed_payloads)


__all__ = [
    'DESIGN_AGGREGATE_COMMIT_FILE', 'DESIGN_REPAIR_AGGREGATE_COMMIT_FILE',
    'DESIGN_HISTORICAL_ADOPTION_FILE', 'DESIGN_HUMAN_EVOLUTION_COMMIT_FILE',
    'DESIGN_MAX_CONCURRENCY', 'DESIGN_UNIT_RECOVERY_POLICY',
    'DesignHistoricalAdoption', 'DesignRepairAggregateRecovery',
    'DesignRepairCapability',
    'DesignUnitRequest', 'DesignUnitSource',
    'PlanChapterRequest', 'PlanHistoricalAdoption',
    'PLAN_CHAPTER_REPLACEMENT_PROTOCOL', 'PLAN_DELIVERY_RECOVERY_FILE',
    'PLAN_HISTORICAL_ADOPTION_FILE',
    'clear_plan_delivery_recovery',
    'plan_chapter_commit_spec', 'plan_chapter_replacement_chain',
    'plan_chapter_replacement_identity', 'plan_chapter_checkpoint_exists',
    'plan_historical_source_lineage',
    'RECOVERY_POLICIES',
    'commit_ownership', 'committed', 'design_aggregate_committed',
    'design_aggregate_prepared', 'design_aggregate_state',
    'design_aggregate_target_state', 'design_unit_key',
    'design_aggregate_usage', 'design_repair_usage',
    'design_repair_sources_usage', 'design_replan_usage',
    'design_repair_recovery_producer_model',
    'design_repair_recovery_usage',
    'design_repair_aggregate_state', 'design_repair_aggregate_target_state',
    'design_unit_checkpoint_exists', 'design_unit_purpose',
    'design_unit_run_spec', 'historical_design_unit_source',
    'current_design_unit_source',
    'load_committed_design_sources',
    'load_design_repair_aggregate_recovery_sources',
    'load_prepared_design_repair_aggregate_recovery',
    'design_human_evolution_state', 'design_human_evolution_target_state',
    'inspect_design_repair_capability',
    'inspect_prepared_design_repair_aggregate_recovery',
    'resolve_committed_design_sources',
    'ensure_design_unit_run', 'ensure_run', 'mark_committed',
    'mark_design_aggregate_committed', 'prepare_commit',
    'prepare_design_aggregate_commit', 'prepare_design_human_evolution_commit',
    'prepare_design_repair_aggregate_commit', 'prepared', 'recovery_policy',
    'preclaim_design_repair_aggregate_recovery',
    'refuse_foreign_commit_reservations', 'restore_checkpoint',
    'validate_design_repair_aggregate_journal_envelope',
    'read_plan_delivery_recovery',
    'require_design_repair_recovery_source_stopped',
    'require_design_repair_recovery_target_clean',
    'require_design_historical_adoption_file',
    'require_plan_historical_adoption_file',
    'restore_or_select_design_historical_adoption',
    'restore_or_select_plan_historical_adoption',
    'restore_commit_adoption', 'restore_design_aggregate_adoptions',
    'restore_design_aggregate_payloads',
    'restore_design_repair_aggregate_payloads',
    'restore_design_unit_checkpoint',
    'target_state', 'verify_commit_finalization', 'verify_commit_primary',
    'verify_committed_delivery', 'verify_design_aggregate_committed_delivery',
    'verify_design_aggregate_finalization', 'verify_design_aggregate_primary',
    'write_checkpoint', 'write_design_unit_checkpoint',
    'write_plan_delivery_recovery',
    'mark_design_human_evolution_committed',
    'mark_design_repair_aggregate_committed',
    'note_design_repair_aggregate_recovery',
    'validate_design_repair_recovery_historical_input',
    'validate_design_repair_recovery_unit_checkpoint_state',
    'validate_design_repair_recovery_unit_request',
]
