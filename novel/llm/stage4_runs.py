# -*- coding: utf-8 -*-
"""Stage4 design/plan 对通用 single-session agent run core 的业务适配。

``stage4.plan`` 仍是一个独立 single-session run；``stage4.design`` v2 则是
strategy + ch1..N 各一条 ``SingleSessionRunSpec``，单元通过后就落
append-only checkpoint，最后由本模块的 aggregate journal 冻结 N+1 份
adoption 与整书候选。通用 profile/prompt/schema/stdout/ledger/session-audit
闭环仍由 ``novel.llm.agent_runs`` 唯一实现。

typed infeasible 只有 binding；bound bundle 只含 plan，context 由独立节点组装；同书任一
foreign ``prepared`` 都阻止另一 run/node 写正式路径。
"""
from __future__ import annotations

import copy
import json
import os
import threading
from collections.abc import Callable, Iterable, Mapping, Sequence
from contextlib import contextmanager
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, TypeVar

from pydantic import BaseModel

from contracts.artifacts import (
    STAGE4_BLIND_CANDIDATE, STAGE4_EVIDENCE_BINDING,
    STAGE4_PLAN_CHAPTERS,
)
from contracts.invariants import STAGE4_DESIGN_MAX_CONCURRENCY
from contracts.schema import Usage
from contracts.schema.stage4 import BlindCandidate
from novel import paths, prov
from novel.agents.session_client import SAME_RUN_RESUMABLE_AGENT_FAILURES
from novel.appendlog import process_append_lock
from novel.errors import ArtifactUnreadable, ProvenanceRefused
from novel.llm import agent_runs
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

_DESIGN_AGGREGATE_VERSION = 1
_DESIGN_AGGREGATE_KIND = 'stage4-design-aggregate/v1'
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
_DESIGN_REPAIR_AGGREGATE_VERSION = 2
_DESIGN_REPAIR_AGGREGATE_KIND = 'stage4-design-targeted-repair/v2'
_DESIGN_REPAIR_AGGREGATE_LEGACY = (1, 'stage4-design-targeted-repair/v1')
_DESIGN_REPAIR_AGGREGATE_FIELDS = {
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
_LEGACY_STANDARD_NULL_PROVENANCE_FIELDS = (
    'parent_candidate_sha256',
    'repair_decision_sha256',
    'unit_lineage',
)
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
class DesignRepairCapability:
    """当前正式 A 能否走 split-checkpoint targeted repair 的只读结论。"""

    repairable: bool
    reason_code: str
    parent_run_id: str | None
    chapter_repair_rounds: tuple[tuple[int, int], ...]


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
        legacy_run_path=lambda ctx: paths.legacy_stage4_design_unit_run_path(
            ctx.novel_dir, ctx.run_id, chapter),
        legacy_checkpoint_dir=lambda ctx: paths.legacy_stage4_design_unit_checkpoint_dir(
            ctx.novel_dir, ctx.run_id, chapter),
        incompatible_session_nodes=('stage4.plan',),
    )


#: Stage4B 章级单元（批次 4）复用 plan 节点的恢复策略；章与章之间
#: per-purpose 隔离，任何两章不共享 provider session。
PLAN_CHAPTER_RECOVERY_POLICY = RECOVERY_POLICIES['stage4.plan']
PLAN_CHAPTER_REPLACEMENT_PROTOCOL = 'stage4-plan-chapter-replacement/v1'
PLAN_DELIVERY_RECOVERY_FILE = 'delivery_recovery.json'
PLAN_HISTORICAL_ADOPTION_FILE = 'historical-checkpoint-adoption.json'

_PLAN_DELIVERY_RECOVERY_VERSION = 1
_PLAN_HISTORICAL_ADOPTION_VERSION = 1
_PLAN_HISTORICAL_ADOPTION_KIND = 'stage4-plan-historical-adoption/v1'
_PLAN_HISTORICAL_ADOPTION_FIELDS = {
    'version', 'kind', 'state', 'target_run_id', 'sid', 'node', 'code',
    'candidate_subject_sha256', 'request_set_sha256', 'source_run_id',
    'source_chapters', 'sources', 'created_at', 'manifest_sha256',
}
_PLAN_HISTORICAL_SOURCE_FIELDS = {
    'chapter', 'source_code', 'source_run_identity_sha256',
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

    manifest_file_sha256: str
    source_run_id: str
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
        legacy_run_path=lambda ctx: paths.legacy_stage4_plan_chapter_run_path(
            ctx.novel_dir, ctx.run_id, chapter),
        legacy_checkpoint_dir=lambda ctx: paths.legacy_stage4_plan_chapter_checkpoint_dir(
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
    for root in paths.stage_run_read_roots(ctx.novel_dir, 'stage4'):
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
    for root in paths.stage_run_read_roots(ctx.novel_dir, 'stage4'):
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
    source_run_id = record.get('source_run_id')
    chapters = record.get('source_chapters')
    rows = record.get('sources')
    if (not isinstance(source_run_id, str)
            or check_run_id(source_run_id) == check_run_id(ctx.run_id)
            or not isinstance(chapters, list) or chapters != sorted(chapters)
            or not chapters or len(chapters) != len(set(chapters))
            or any(chapter not in request_by for chapter in chapters)
            or not isinstance(rows, list) or len(rows) != len(chapters)):
        raise ProvenanceRefused(
            f'{target} 的 source run/chapters 已不适用于当前恢复面')
    restored: list[tuple[int, agent_runs.RestoredHistoricalRun[Any]]] = []
    for chapter, row in zip(chapters, rows, strict=True):
        if (not isinstance(row, dict)
                or set(row) != _PLAN_HISTORICAL_SOURCE_FIELDS
                or row.get('chapter') != chapter):
            raise ArtifactUnreadable(f'{target} 的 source row 形状漂移')
        historical = _restore_plan_historical_chapter(
            ctx, source_run_id=source_run_id,
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
        source_run_id=source_run_id,
        restored=tuple(restored),
    )


def restore_or_select_plan_historical_adoption(
        ctx: Any, *, requests: Sequence[PlanChapterRequest],
        candidate_subject_sha256: str,
        eligible_chapters: Iterable[int],
        validators: Mapping[
            int, Callable[[BaseModel, ChatResponse], str | None] | None],
) -> PlanHistoricalAdoption | None:
    """恢复 target 已选来源；首次则只采用唯一覆盖最多的合法 partial run。

    旧 run 只读，并用自己的 code/profile/ledger/session 复验；响应还必须通过
    当前请求和当前业务 validator。来源并列时不猜，直接放弃复用并走新 wire。
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

    candidates: list[
        tuple[str, tuple[tuple[int, agent_runs.RestoredHistoricalRun[Any]], ...]]
    ] = []
    for source_run_id in _plan_source_run_ids(ctx):
        source_chapters = [
            chapter for chapter in eligible
            if _source_has_plan_checkpoint(ctx, source_run_id, chapter)]
        if not source_chapters:
            continue
        # 没有任何 current checkpoint 时，不能全量采用历史单元：普通 commit
        # 必须仍锚在 target run 的一条真实 checkpoint/adoption 链上。
        if (len(eligible) == len(normalized)
                and len(source_chapters) == len(normalized)):
            continue
        restored: list[tuple[int, agent_runs.RestoredHistoricalRun[Any]]] = []
        try:
            for chapter in source_chapters:
                restored.append((chapter, _restore_plan_historical_chapter(
                    ctx, source_run_id=source_run_id,
                    request=request_by[chapter],
                    validator=validators.get(chapter))))
        except (ArtifactUnreadable, ProvenanceRefused, ValueError):
            continue
        candidates.append((source_run_id, tuple(restored)))
    if not candidates:
        return None
    best_size = max(len(rows) for _source, rows in candidates)
    best = [(source, rows) for source, rows in candidates
            if len(rows) == best_size]
    if len(best) != 1:
        ctx.observe(
            'stage4_plan_historical_adoption_ambiguous',
            candidate_count=len(best), checkpoint_count=best_size)
        return None
    source_run_id, restored = best[0]
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
        'source_run_id': source_run_id,
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
        source_run_id=source_run_id,
        chapters=[chapter for chapter, _row in restored])
    return PlanHistoricalAdoption(
        manifest_file_sha256=agent_runs.canonical_sha256(target.read_bytes()),
        source_run_id=source_run_id,
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


def design_aggregate_usage(
        ctx: Any, units: Sequence[DesignUnitRequest],
) -> Usage:
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
    identities: list[tuple[str, Mapping[str, object]]] = []
    seen_attempts: set[str] = set()
    accepted_attempts: set[str] = set()
    provider_sessions: set[str] = set()
    for unit in normalized_units:
        purpose = design_unit_purpose(unit.chapter)
        restored = restore_design_unit_checkpoint(ctx, unit)
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
            identities.append((purpose, identity))
        if accepted_attempt not in linked_attempts:
            raise ProvenanceRefused(
                f'{purpose}: accepted attempt 未被 attempt_links 联结')
    if len(accepted_attempts) != len(normalized_units):
        raise ProvenanceRefused('Stage4 design usage 缺 N+1 accepted attempts')
    usage = ctx.ledger.usage_for_complete_purposes(
        [design_unit_purpose(unit.chapter) for unit in normalized_units],
        sid=ctx.sid, node='stage4.design', required_identities=identities)
    if usage is None:
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
        run_path=lambda ctx: paths.stage4_run_path(
            ctx.novel_dir, ctx.run_id, node),
        checkpoint_dir=lambda ctx: paths.stage4_checkpoint_dir(
            ctx.novel_dir, ctx.run_id, node),
        legacy_run_path=lambda ctx: paths.legacy_stage4_run_path(
            ctx.novel_dir, ctx.run_id, node),
        legacy_checkpoint_dir=lambda ctx: paths.legacy_stage4_checkpoint_dir(
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
    """把已验真的 journal 候选投影到当前 schema 身份。

    aggregate 的 ``primary_payload_sha256`` 永远核原始 journal 字节对应的
    JSON；这里不迁盘、不改写旧 record。唯一兼容面是旧 standard journal
    缺少 repair lineage 的三个 ``null``：先显式补这三键，再 strict validate。
    Pydantic dump 必须与这份只补白名单键的 payload 完全相等，不能借 coercion
    或其他字段默认值把真实漂移折叠成当前候选。
    """
    if aggregate_kind not in (None, 'standard', 'repair', 'human'):
        raise ValueError(f'未知 design aggregate kind：{aggregate_kind!r}')
    candidate = copy.deepcopy(dict(payload))
    provenance = candidate.get('_provenance')
    if not isinstance(provenance, dict):
        raise ArtifactUnreadable(f'{label} primary_payload 缺 typed _provenance')
    if aggregate_kind is not None:
        expected_architectures = (
            {'book_strategy_chapters_v1'} if aggregate_kind == 'standard'
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
    if aggregate_kind == 'standard':
        for field in _LEGACY_STANDARD_NULL_PROVENANCE_FIELDS:
            provenance.setdefault(field, None)
    if aggregate_kind == 'repair':
        lineage = provenance.get('unit_lineage')
        if isinstance(lineage, list):
            for row in lineage:
                if isinstance(row, dict):
                    row.setdefault('origin', 'agent')
                    row.setdefault('source_ref', None)
    try:
        normalized = BlindCandidate.model_validate(
            candidate, strict=True).model_dump(by_alias=True, mode='json')
    except (TypeError, ValueError) as exc:
        raise ArtifactUnreadable(
            f'{label} primary_payload 不是当前 BlindCandidate：{exc}') from exc
    if normalized != candidate:
        raise ArtifactUnreadable(
            f'{label} primary_payload 需要白名单之外的默认值补全或类型归一')
    return normalized, agent_runs.canonical_sha256(normalized)


def _legacy_design_candidate_sha256(payload: Mapping[str, Any]) -> str:
    """v1 repair lineage 在 origin/source_ref 加法前使用的业务身份。"""
    legacy = copy.deepcopy(dict(payload))
    provenance = legacy.get('_provenance')
    if isinstance(provenance, dict):
        lineage = provenance.get('unit_lineage')
        if isinstance(lineage, list):
            for row in lineage:
                if isinstance(row, dict):
                    row.pop('origin', None)
                    row.pop('source_ref', None)
    return agent_runs.canonical_sha256(legacy)


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


def _stage4_history_files(ctx: Any, pattern: str) -> tuple[Path, ...]:
    """按相对路径去重扫描 service + legacy，当前空间优先。"""
    found: dict[str, Path] = {}
    for root in paths.stage_run_read_roots(ctx.novel_dir, 'stage4'):
        if not root.is_dir():
            continue
        for target in root.glob(pattern):
            key = target.relative_to(root).as_posix()
            prior = found.get(key)
            if prior is not None:
                continue
            found[key] = target
    return tuple(found[key] for key in sorted(found))


def _design_history_candidates(
        ctx: Any, run_id: str, filename: str,
) -> tuple[Path, ...]:
    checked = check_run_id(run_id)
    return tuple(
        root / checked / 'design' / filename
        for root in paths.stage_run_read_roots(ctx.novel_dir, 'stage4'))


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
    expected_checkpoint = {
        'run_id': check_run_id(ctx.run_id), 'sid': ctx.sid,
        'node': 'stage4.design', 'code': ctx.code,
        'chapter': chapter, 'purpose': row.get('purpose'),
        'run_sha256': row.get('run_sha256'),
        'revision': revision,
        'checkpoint_sha256': row.get('checkpoint_sha256'),
        'attempt_id': row.get('attempt_id'),
        'request_fingerprint': identity.get('request_fingerprint'),
        'model': identity.get('model'),
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
    if set(record) != _DESIGN_AGGREGATE_FIELDS:
        raise ArtifactUnreadable(f'{target} aggregate commit 字段漂移')
    if record.get('commit_sha256') != _design_aggregate_hash(record):
        raise ArtifactUnreadable(f'{target} aggregate commit SHA 不闭合')
    expected = {
        'version': _DESIGN_AGGREGATE_VERSION,
        'kind': _DESIGN_AGGREGATE_KIND,
        'run_id': check_run_id(ctx.run_id), 'sid': ctx.sid,
        'node': 'stage4.design', 'code': ctx.code,
        'primary_artifact': STAGE4_BLIND_CANDIDATE,
    }
    if {key: record.get(key) for key in expected} != expected:
        raise ProvenanceRefused(f'{target} 不属于当前 design run/sid/code')
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
    if (not isinstance(rows, list) or len(rows) < 2
            or not isinstance(attempts, list)):
        raise ArtifactUnreadable(f'{target} 缺 N+1 aggregate adoptions')
    seen_keys: set[str] = set()
    seen_purposes: set[str] = set()
    seen_attempts: set[str] = set()
    seen_sessions: set[str] = set()
    chapters: list[int] = []
    strategy_count = 0
    for row in rows:
        if not isinstance(row, dict) or set(row) != _DESIGN_ADOPTION_FIELDS:
            raise ArtifactUnreadable(f'{target} adoption 字段漂移')
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
    return record


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
    record = agent_runs.commit_state(ctx, spec or _commit_spec(ctx.node))
    return record if record is not None and record.get('state') == 'committed' else None


def _validate_foreign_design_aggregate_envelope(
        target: Path, *, run_id: str, sid: str,
) -> dict[str, Any]:
    """跨 run 扫描只验不依赖当前 profile/ledger 的聚合提交外壳。"""
    record = _read_object(target, label='Stage4 foreign design aggregate')
    if (set(record) != _DESIGN_AGGREGATE_FIELDS
            or record.get('commit_sha256') != _design_aggregate_hash(record)
            or record.get('version') != _DESIGN_AGGREGATE_VERSION
            or record.get('kind') != _DESIGN_AGGREGATE_KIND
            or record.get('run_id') != check_run_id(run_id)
            or record.get('sid') != sid
            or record.get('node') != 'stage4.design'
            or record.get('state') not in ('prepared', 'committed')):
        raise ArtifactUnreadable(
            f'Stage4 foreign design aggregate {target} 身份/哈希不闭合')
    return record


def _validate_foreign_design_repair_aggregate_envelope(
        target: Path, *, run_id: str, sid: str) -> dict[str, Any]:
    record = _read_object(target, label='Stage4 foreign design repair aggregate')
    version_kind = (record.get('version'), record.get('kind'))
    if (set(record) != _DESIGN_REPAIR_AGGREGATE_FIELDS
            or record.get('commit_sha256')
            != _design_repair_aggregate_hash(record)
            or version_kind not in {
                (_DESIGN_REPAIR_AGGREGATE_VERSION,
                 _DESIGN_REPAIR_AGGREGATE_KIND),
                _DESIGN_REPAIR_AGGREGATE_LEGACY,
            }
            or record.get('run_id') != check_run_id(run_id)
            or record.get('sid') != sid
            or record.get('node') != 'stage4.design'
            or record.get('state') not in ('prepared', 'committed')):
        raise ArtifactUnreadable(
            f'Stage4 foreign design repair aggregate {target} 身份/哈希不闭合')
    return record


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


def _load_design_source(
        ctx: Any, row: Mapping[str, Any], *, default_run_id: str,
        default_source_kind: str, _verify_inline_owner: bool = True,
) -> DesignUnitSource:
    """从 aggregate row 指向的原 run/checkpoint 验证并取回响应。"""
    fields = set(row)
    if fields not in (
        _DESIGN_ADOPTION_FIELDS,
        _DESIGN_REPAIR_SOURCE_FIELDS_V1,
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
    if origin not in ('agent', 'human', 'candidate_snapshot'):
        raise ArtifactUnreadable('Stage4 design source origin 非法')
    if origin != 'agent':
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
    if source_kind == 'human' or (
        fields == _DESIGN_REPAIR_SOURCE_FIELDS
        and (row.get('source_ref') is not None
             or row.get('response_payload') is not None)
    ):
        raise ProvenanceRefused(
            'Stage4 agent source 不得夹带 human/inline 身份')
    run_path = next((path / agent_runs.RUN_FILE for path in (
        paths.stage4_design_unit_run_path(
            ctx.novel_dir, source_run_id, chapter),
        paths.legacy_stage4_design_unit_run_path(
            ctx.novel_dir, source_run_id, chapter),
    ) if (path / agent_runs.RUN_FILE).is_file()),
        paths.stage4_design_unit_run_path(
            ctx.novel_dir, source_run_id, chapter) / agent_runs.RUN_FILE)
    checkpoint_revision = row.get('checkpoint_revision')
    if (not isinstance(checkpoint_revision, int)
            or isinstance(checkpoint_revision, bool) or checkpoint_revision < 1):
        raise ArtifactUnreadable('Stage4 design source checkpoint revision 非法')
    checkpoint_path = next((path / f'rev-{checkpoint_revision:04d}.json'
        for path in (
            paths.stage4_design_unit_checkpoint_dir(
                ctx.novel_dir, source_run_id, chapter),
            paths.legacy_stage4_design_unit_checkpoint_dir(
                ctx.novel_dir, source_run_id, chapter),
        ) if (path / f'rev-{checkpoint_revision:04d}.json').is_file()),
        paths.stage4_design_unit_checkpoint_dir(
            ctx.novel_dir, source_run_id, chapter)
        / f'rev-{checkpoint_revision:04d}.json')
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
        origin='agent',
        source_ref=None,
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


def load_committed_design_sources(
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
    return kind, target, record


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
                and expected_primary_sha not in {
                    normalized_primary_sha,
                    _legacy_design_candidate_sha256(_normalized_primary),
                }):
            raise ProvenanceRefused(
                f'{target} 不是子 repair journal 绑定的 parent candidate')
        if kind == 'standard':
            break
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
    ``whole_book_v1``/无 aggregate 候选不猜测，只能 strategy replan。
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
        # whole_book/未记 architecture 的正式候选在产品上就没有 split
        # aggregate/checkpoint lineage。先准确分类，不能让与 lineage 无关的
        # Python strict 容器差异把它误报成 checkpoint 损坏。
        if raw_provenance.get('architecture') in (None, 'whole_book_v1'):
            return unavailable('legacy_or_untracked_candidate')
        candidate, full_sha = _normalized_design_candidate_identity(
            raw_candidate, label='当前正式 blind candidate')
        from novel.stages.stage4.blind import candidate_subject_sha256
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


def refuse_foreign_commit_reservations(ctx: Any) -> None:
    """同书任一 foreign prepared 必须先补完，不能跨节点覆盖正式路径。"""
    _require_ownership(ctx)
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
            with process_append_lock(target):
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
            ctx, f'*/design/{DESIGN_AGGREGATE_COMMIT_FILE}'):
        run_id = target.parent.parent.name
        if run_id == ctx.run_id or target.resolve() in current:
            continue
        try:
            with process_append_lock(target):
                record = _validate_foreign_design_aggregate_envelope(
                    target, run_id=run_id, sid=ctx.sid)
        except OSError as exc:
            raise ProvenanceRefused(
                f'Stage4 foreign design aggregate {target} 不可验证：{exc}') from exc
        if record['state'] == 'prepared':
            raise ProvenanceRefused(
                f'Stage4 run={run_id}/stage4.design 仍持有 prepared '
                'aggregate 写盘尾窗；必须先用原 run 补完')
    current_repair = _design_repair_aggregate_commit_path(ctx).resolve()
    for target in _stage4_history_files(
            ctx, f'*/design/{DESIGN_REPAIR_AGGREGATE_COMMIT_FILE}'):
        run_id = target.parent.parent.name
        if run_id == ctx.run_id or target.resolve() == current_repair:
            continue
        try:
            with process_append_lock(target):
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
            with process_append_lock(target):
                record = _validate_foreign_design_human_evolution_envelope(
                    target, run_id=run_id, sid=ctx.sid)
        except OSError as exc:
            raise ProvenanceRefused(
                f'Stage4 foreign human evolution 不可验证：{exc}') from exc
        if record['state'] == 'prepared':
            raise ProvenanceRefused(
                f'Stage4 run={run_id}/stage4.design 仍持有 prepared '
                'human evolution 写盘尾窗；必须先补完')


def _design_adoption_row(
        ctx: Any, unit: DesignUnitRequest,
) -> dict[str, Any]:
    restored = restore_design_unit_checkpoint(ctx, unit)
    if restored is None:
        raise ProvenanceRefused(
            f'{design_unit_purpose(unit.chapter)}: aggregate commit 前缺 '
            'accepted checkpoint')
    attempt_id = str(restored.acceptance['attempt_id'])
    identity = ctx.ledger.request_identity_for_attempt(attempt_id)
    if (set(identity) != _LEDGER_IDENTITY_FIELDS
            or identity.get('attempt_id') != attempt_id
            or identity.get('request_fingerprint')
            != restored.acceptance.get('request_fingerprint')
            or identity.get('model') != restored.acceptance.get('model')
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
        paths.legacy_stage4_run_path(
            ctx.novel_dir, ctx.run_id, 'stage4.design'),
    )
    if any((root / agent_runs.RUN_FILE).is_file()
           or (root / agent_runs.COMMIT_FILE).is_file()
           for root in single_session_roots):
        raise ProvenanceRefused(
            f'{single_session_roots} 已绑定旧 single-session design run/commit；'
            '同一 run 不能再建 aggregate 第二权威源')

    rows = [_design_adoption_row(ctx, unit) for unit in normalized_units]
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
    # 在写任何 journal 前先证明两份快照可 canonical JSON 化。
    primary_sha = agent_runs.canonical_sha256(primary)
    finalization_sha = agent_runs.canonical_sha256(finalization)
    payload: dict[str, Any] = {
        'version': _DESIGN_AGGREGATE_VERSION,
        'kind': _DESIGN_AGGREGATE_KIND,
        'state': 'prepared', 'prepared_at': _now(), 'committed_at': None,
        'run_id': check_run_id(ctx.run_id), 'sid': ctx.sid,
        'node': 'stage4.design', 'code': ctx.code,
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
            if source.origin != 'agent' else None
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


def candidate_snapshot_design_sources(
        ctx: Any, candidate_payload: Mapping[str, Any], *,
        parent_candidate_sha256: str,
) -> tuple[DesignUnitSource, ...]:
    """无 checkpoint 的旧候选只做诚实 deterministic snapshot，不冒充模型。"""
    if not agent_runs.is_sha256(parent_candidate_sha256):
        raise ProvenanceRefused('candidate snapshot parent SHA 非法')
    candidate = BlindCandidate.model_validate(
        copy.deepcopy(dict(candidate_payload)), strict=True)
    strategy = {
        'reconstruction_strategy': candidate.reconstruction_strategy,
        'chapter_weights': [{
            'chapter_ordinal': row.output_ordinal,
            'weight': row.weight,
        } for row in candidate.output_chapters],
    }
    responses: list[tuple[int | None, dict[str, Any]]] = [(None, strategy)]
    for chapter in candidate.output_chapters:
        response = chapter.model_dump(mode='json')
        for scene in response['scenes']:
            scene.pop('candidate_scene_id')
        responses.append((chapter.output_ordinal, response))
    sources: list[DesignUnitSource] = []
    for chapter, response in responses:
        response_sha = agent_runs.canonical_sha256(response)
        source_ref = agent_runs.canonical_sha256({
            'kind': 'stage4-design-candidate-snapshot-source-v1',
            'parent_candidate_sha256': parent_candidate_sha256,
            'chapter': chapter,
            'response_payload_sha256': response_sha,
        })
        sources.append(DesignUnitSource(
            chapter=chapter,
            source_run_id=check_run_id(ctx.run_id),
            purpose=design_unit_purpose(chapter),
            source_kind='inherited',
            response_payload=response,
            response_payload_sha256=response_sha,
            origin='candidate_snapshot',
            source_ref=source_ref,
        ))
    return tuple(sources)


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
    source_fields = (
        _DESIGN_REPAIR_SOURCE_FIELDS
        if record.get('version') == _DESIGN_REPAIR_AGGREGATE_VERSION
        else _DESIGN_REPAIR_SOURCE_FIELDS_V1
    )
    for row in rows:
        if not isinstance(row, dict) or set(row) != source_fields:
            raise ArtifactUnreadable(f'{target} repair source 字段漂移')
        source = _load_design_source(
            ctx, row, default_run_id=str(row.get('source_run_id') or ''),
            default_source_kind=str(row.get('source_kind') or ''))
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
                or (expected_kind == 'repair'
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
    } for source in sources if (
        record.get('version') != _DESIGN_REPAIR_AGGREGATE_VERSION
        or source.origin == 'agent'
    )]
    if record.get('attempts') != attempts:
        raise ArtifactUnreadable(f'{target} repair attempts/source lineage 不等')
    if (record.get('touched_paths') != [STAGE4_BLIND_CANDIDATE]
            or not isinstance(record.get('baselines'), dict)
            or set(record['baselines']) != {STAGE4_BLIND_CANDIDATE}):
        raise ArtifactUnreadable(f'{target} repair touched/baseline 不闭合')
    landed = record.get('landed_payload_sha256s')
    post = record.get('post_file_sha256s')
    if record['state'] == 'prepared':
        if (record.get('committed_at') is not None
                or landed is not None or post is not None):
            raise ArtifactUnreadable(f'{target} repair prepared 尾窗字段不闭合')
    elif (not isinstance(record.get('committed_at'), str)
          or not isinstance(landed, dict)
          or not agent_runs.is_sha256(landed.get(STAGE4_BLIND_CANDIDATE))
          or not isinstance(post, dict)
          or not agent_runs.is_sha256(post.get(STAGE4_BLIND_CANDIDATE))):
        raise ArtifactUnreadable(f'{target} repair committed 字段不闭合')
    return dict(record)


def _validate_foreign_committed_design_repair_aggregate(
        ctx: Any, target: Path, *, run_id: str, sid: str,
) -> dict[str, Any]:
    """foreign repair 不只验 envelope；轮次必须来自完整可追溯 lineage。"""
    record = _validate_foreign_design_repair_aggregate_envelope(
        target, run_id=run_id, sid=sid)
    if record.get('state') != 'committed':
        raise ProvenanceRefused(f'{target} foreign repair aggregate 尚未 committed')
    return _validate_design_repair_aggregate_contents(
        ctx, target, record, run_id=run_id)


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


def prepare_design_repair_aggregate_commit(
        ctx: Any, *, parent_run_id: str, parent_candidate_sha256: str,
        repair_decision_sha256: str, target_chapters: Sequence[int],
        sources: Sequence[DesignUnitSource], primary_payload: Mapping[str, Any],
        finalization_payload: Mapping[str, Any],
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
                or (expected == 'repair' and source.source_run_id != ctx.run_id)):
            raise ProvenanceRefused(
                f'design repair ch{chapter} source_kind/source_run 不符')
    primary = copy.deepcopy(dict(primary_payload))
    finalization = copy.deepcopy(dict(finalization_payload))
    rows = [_design_source_row(source) for source in ordered]
    attempts = [{
        'source_run_id': source.source_run_id,
        'purpose': source.purpose,
        'attempt_id': source.attempt_id,
    } for source in ordered if source.origin == 'agent']
    payload: dict[str, Any] = {
        'version': _DESIGN_REPAIR_AGGREGATE_VERSION,
        'kind': _DESIGN_REPAIR_AGGREGATE_KIND,
        'state': 'prepared', 'prepared_at': _now(), 'committed_at': None,
        'run_id': check_run_id(ctx.run_id), 'sid': ctx.sid,
        'node': 'stage4.design', 'code': ctx.code,
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
        'touched_paths': [STAGE4_BLIND_CANDIDATE],
        'baselines': {STAGE4_BLIND_CANDIDATE:
                      agent_runs.file_sha256(ctx, STAGE4_BLIND_CANDIDATE)},
        'attempts': attempts,
        'sources': rows,
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


def mark_design_repair_aggregate_committed(
        ctx: Any, *, primary_payload: Mapping[str, Any],
        finalization_payload: Mapping[str, Any],
) -> Path:
    _require_ownership(ctx)
    target = _design_repair_aggregate_commit_path(ctx)
    with _DESIGN_AGGREGATE_LOCK, process_append_lock(target):
        current = _validate_design_repair_aggregate_state(ctx, target)
        if (current['primary_payload_sha256']
                != agent_runs.canonical_sha256(dict(primary_payload))
                or current['finalization_sha256']
                != agent_runs.canonical_sha256(dict(finalization_payload))):
            raise ProvenanceRefused('repair aggregate committed 候选/finalization 漂移')
        post = agent_runs.file_sha256(ctx, STAGE4_BLIND_CANDIDATE)
        if post is None:
            raise ProvenanceRefused('repair aggregate committed 前候选未落盘')
        landed = {STAGE4_BLIND_CANDIDATE:
                  agent_runs.canonical_sha256(dict(primary_payload))}
        post_rows = {STAGE4_BLIND_CANDIDATE: post}
        expected = {
            (row['purpose'], row['attempt_id'], row['chapter'])
            for row in current['sources'] if row['source_kind'] == 'repair'
        }
        relevant = [row for row in _read_adoption_events(ctx)
                    if (row.get('kind') == 'artifact_adoption'
                        and row.get('run_id') == ctx.run_id
                        and row.get('sid') == ctx.sid
                        and row.get('node') == 'stage4.design'
                        and row.get('artifact') == STAGE4_BLIND_CANDIDATE
                        and row.get('artifact_fingerprint')
                        == current['primary_payload_fingerprint'])]
        actual = {(row.get('purpose'), row.get('attempt_id'), row.get('chapter'))
                  for row in relevant}
        if len(relevant) != len(expected) or actual != expected:
            raise ProvenanceRefused(
                'repair aggregate 提交前必须恰有每个目标章一条 adoption')
        if current['state'] == 'committed':
            if (current.get('landed_payload_sha256s') != landed
                    or current.get('post_file_sha256s') != post_rows):
                raise ProvenanceRefused('repair aggregate 已 committed 结果漂移')
            return target
        updated = dict(current)
        updated.update({
            'state': 'committed', 'committed_at': _now(),
            'landed_payload_sha256s': landed,
            'post_file_sha256s': post_rows,
        })
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
    if set(by_key) != {design_unit_key(unit.chapter) for unit in normalized_units}:
        raise ProvenanceRefused('design aggregate 的 unit 集合与当前请求漂移')
    restored_rows: list[tuple[str, int | None, str]] = []
    for unit in normalized_units:
        restored = restore_design_unit_checkpoint(ctx, unit)
        if restored is None:
            raise ProvenanceRefused(
                f'{design_unit_purpose(unit.chapter)}: aggregate 所指 checkpoint 缺失')
        row = by_key[design_unit_key(unit.chapter)]
        attempt_id = str(row['attempt_id'])
        identity = row['request_identity']
        if (restored.acceptance.get('attempt_id') != attempt_id
                or restored.revision != row.get('checkpoint_revision')
                or restored.checkpoint_sha256 != row.get('checkpoint_sha256')
                or restored.acceptance.get('provider_session_id')
                != row.get('provider_session_id')
                or ctx.ledger.request_identity_for_attempt(attempt_id)
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
) -> None:
    expected = {
        (str(row['purpose']), str(row['attempt_id']), row.get('chapter'))
        for row in record['adoptions']
    }
    relevant = [
        row for row in _read_adoption_events(ctx)
        if (row.get('kind') == 'artifact_adoption'
            and row.get('run_id') == ctx.run_id
            and row.get('sid') == ctx.sid
            and row.get('node') == 'stage4.design'
            and row.get('artifact') == STAGE4_BLIND_CANDIDATE
            and row.get('artifact_fingerprint')
            == record['primary_payload_fingerprint'])
    ]
    actual = {
        (str(row.get('purpose')), str(row.get('attempt_id')), row.get('chapter'))
        for row in relevant
    }
    if len(relevant) != len(expected) or actual != expected:
        raise ProvenanceRefused(
            'Stage4 design committed 前必须恰有 strategy + ch1..N '
            f'各一条当前主产物 adoption；期望 {len(expected)}，'
            f'实得 {len(relevant)}')


def mark_design_aggregate_committed(
        ctx: Any, *, primary_payload: Mapping[str, Any],
        finalization_payload: Mapping[str, Any],
        landed_payloads: Mapping[str, Mapping[str, Any]],
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
    return agent_runs.target_state(
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
    return agent_runs.mark_committed(
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
    agent_runs.verify_committed_delivery(
        ctx, spec or _commit_spec(ctx.node), record,
        primary_payload=primary_payload,
        finalization_payload=finalization_payload,
        landed_payloads=landed_payloads)


__all__ = [
    'DESIGN_AGGREGATE_COMMIT_FILE', 'DESIGN_REPAIR_AGGREGATE_COMMIT_FILE',
    'DESIGN_HUMAN_EVOLUTION_COMMIT_FILE',
    'DESIGN_MAX_CONCURRENCY', 'DESIGN_UNIT_RECOVERY_POLICY',
    'DesignRepairCapability', 'DesignUnitRequest', 'DesignUnitSource',
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
    'design_aggregate_usage', 'design_repair_usage', 'design_replan_usage',
    'design_repair_aggregate_state', 'design_repair_aggregate_target_state',
    'design_unit_purpose', 'design_unit_run_spec',
    'candidate_snapshot_design_sources', 'current_design_unit_source',
    'load_committed_design_sources',
    'design_human_evolution_state', 'design_human_evolution_target_state',
    'inspect_design_repair_capability', 'resolve_committed_design_sources',
    'ensure_design_unit_run', 'ensure_run', 'mark_committed',
    'mark_design_aggregate_committed', 'prepare_commit',
    'prepare_design_aggregate_commit', 'prepare_design_human_evolution_commit',
    'prepare_design_repair_aggregate_commit', 'prepared', 'recovery_policy',
    'refuse_foreign_commit_reservations', 'restore_checkpoint',
    'read_plan_delivery_recovery',
    'require_plan_historical_adoption_file',
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
]
