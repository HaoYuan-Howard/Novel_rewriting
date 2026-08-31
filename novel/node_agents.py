#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""逐节点 executor agent：独立实例、独立 session、代码提交与终验。

厂商差异只在 ``AgentDriver``；这里按业务节点派生。这样每个节点确有自己的实例、
输入投影、反馈循环与 delivery verifier，同时避免 node×vendor 子类笛卡尔积。
"""
from __future__ import annotations

import copy
import hashlib
import json
import threading
from abc import ABC, abstractmethod
from collections.abc import Callable, Mapping, Sequence
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import dataclass
from typing import Any, Generic, Literal, TypeVar

from pydantic import BaseModel

from contracts.invariants import (
    STAGE2_BLUEPRINT_MAX_CONCURRENCY,
    STAGE4_DESIGN_MAX_CONCURRENCY, STAGE4_PLAN_MAX_CONCURRENCY,
)
from contracts.schema.orchestration import AgentProfile
from contracts.schema.report import CheckReport
from contracts.schema.stage2 import (
    ChapterBlueprints, GlobalCandidateAgentTurnResponse,
    GlobalCandidateFullTurn, GlobalGrounding, GlobalRoleFatePatchTurn,
)
from contracts.schema.stage4 import (
    BindingConcern, BlindCandidate, ChapterBindingResponse, EvidenceBinding,
)
from contracts.schema.stage5 import ChapterResponse
from contracts.schema.stage6 import Hook, HookSemanticReview
from contracts.schema.stage7 import NameMap
from novel import agent_session_audit, paths, prov
from novel.agents import AgentDriver, AgentProbe
from novel.agents.factory import create_driver
from novel.agents.session_client import SESSION_PROTOCOL_VERSION, SessionAgentClient
from novel.config import MAX_CONCURRENCY
from novel.errors import GateBlocked, ProvenanceRefused
from novel.llm import stage4_runs, stage5_runs
from novel.llm.client import LlmFailed
from novel.observability import RequestProgress
from novel.run import RunContext
from novel.stages.stage2 import extract
from novel.stages.stage2.character_refs import blueprint_inventory_from_projection
from novel.stages.stage2.contract import active_contract, contract_for
from novel.stages.stage2.paragraphs import paragraph_spans
from novel.stages.stage2.references import hinge_candidates_by_chapter
from novel.stages.stage4 import plan
from novel.stages.stage5 import write
from novel.stages.stage6 import hook, semantic
from novel.stages.stage7 import rename
from novel.store import ArtifactStore


A = TypeVar('A', bound=BaseModel)
GlobalPatchKind = Literal['role_fate_patch']


@dataclass(frozen=True)
class NodeAgentResult(Generic[A]):
    artifact: A
    turns: int
    #: ``False`` 表示已落正式负结论/报告红灯，应由 CLI 返回 landed-red=1。
    #: `.INVALID` 耗尽不从这里返回，而由 producer 的 GateBlocked 保持 exit=2。
    passed: bool
    #: 可选机械/证据报告。artifact 与报告不是同一个轴，不能为了 CLI 显示而
    #: 丢掉真正的正式产物（Stage2 blueprint / Stage6 semantic 都是这种形状）。
    report: CheckReport | None = None

    def __post_init__(self) -> None:
        if (not isinstance(self.turns, int) or isinstance(self.turns, bool)
                or self.turns < 0):
            raise ValueError('NodeAgentResult.turns 必须是非负整数')
        if type(self.passed) is not bool:
            raise ValueError('NodeAgentResult.passed 必须显式给 bool')
        if self.report is not None and self.passed != self.report.passed:
            raise ValueError(
                'NodeAgentResult.passed 必须与 report.passed 精确一致')
        if isinstance(self.artifact, CheckReport) and self.report is None:
            raise ValueError(
                'CheckReport artifact 必须同时放进 report 显式传播给 CLI')


class NodeExecutorAgent(ABC, Generic[A]):
    """一个节点 executor 的抽象基类；一次实例只拥有一个 logical session。"""

    node: str

    def __init__(
        self,
        ctx: RunContext,
        *,
        driver: AgentDriver | None = None,
        max_turns: int = 4,
    ) -> None:
        if ctx.node != self.node:
            raise ValueError(f'{type(self).__name__} 只接受 {self.node}，实得 {ctx.node}')
        if ctx.executor_profile is None:
            raise ValueError(f'{self.node} executor 缺 resolved profile')
        self.ctx = ctx
        self.driver = driver or create_driver(
            ctx.executor_profile, role='executor')
        self.client = SessionAgentClient(
            ctx, ctx.executor_profile, self.driver,
            parent_run_id=ctx.run_id, max_turns=max_turns)

    @abstractmethod
    def run(self) -> NodeAgentResult[A]:
        raise NotImplementedError

    def _finish_exception(self, exc: Exception) -> None:
        """单 session 节点统一折叠进程状态与交付状态；不从异常文本猜成功。"""
        if isinstance(exc, GateBlocked):
            self.client.finish(
                process_state='ok', delivery_state='blocked',
                error_kind='artifact_validation')
        elif isinstance(exc, ProvenanceRefused):
            self.client.finish(
                process_state='ok', delivery_state='unknown',
                error_kind='artifact_verification')
        elif isinstance(exc, LlmFailed):
            self.client.finish(
                process_state=('failed' if self.client.driver_failed else 'ok'),
                delivery_state=(
                    'unknown' if self.client.driver_failed else 'failed'),
                error_kind=(
                    'agent_driver' if self.client.driver_failed
                    else 'response_validation'),
            )
        else:
            self.client.finish(
                process_state=('failed' if self.client.driver_failed else 'ok'),
                delivery_state=(
                    'unknown' if self.client.driver_failed else 'failed'),
                error_kind=(
                    'agent_driver' if self.client.driver_failed
                    else 'node_executor'),
            )


class Stage2GlobalAgent(NodeExecutorAgent[GlobalGrounding]):
    """``stage2.global`` 的候选—代码校验—resume—正式提交纵切。"""

    node = 'stage2.global'

    def _finish_restored(
            self, acceptance: Mapping[str, object], *,
            delivery_state: str, error_kind: str | None = None,
    ) -> None:
        agent_session_audit.finish_restored_session(
            self.ctx.novel_dir.parent,
            acceptance,
            process_state='ok',
            delivery_state=delivery_state,
            error_kind=error_kind,
            end_code_version=prov.code_version(),
            only_if_open=True,
        )

    def _finalize_rejected(
        self,
        prepared: extract.GlobalCandidateContext,
        evaluation: extract.GlobalCandidateEvaluation,
    ) -> None:
        """终态化最后一份业务坏候选，并保证 session 两条状态轴闭合。"""
        try:
            extract.finalize_global_candidate(
                self.ctx, self.client, prepared, evaluation)
        except GateBlocked:
            self.client.finish(
                process_state='ok', delivery_state='blocked',
                error_kind='artifact_validation')
            raise
        except ProvenanceRefused:
            self.client.finish(
                process_state='ok', delivery_state='unknown',
                error_kind='artifact_verification')
            raise
        except Exception:
            self.client.finish(
                process_state=('failed' if self.client.driver_failed else 'ok'),
                delivery_state=('unknown' if self.client.driver_failed else 'failed'),
                error_kind=('agent_driver' if self.client.driver_failed
                            else 'node_executor'),
            )
            raise
        raise AssertionError('HARD 候选 finalize 必须抛 GateBlocked')

    @staticmethod
    def _repair_transition(
        prepared: extract.GlobalCandidateContext,
        evaluation: extract.GlobalCandidateEvaluation,
        *,
        role_patch_started: bool,
        accepted_role_patch_count: int,
    ) -> tuple[
        list[dict[str, object]],
        extract.GlobalCandidateEvaluation | None,
        GlobalPatchKind | None,
        bool,
    ]:
        """把一份业务 HARD 候选收成唯一下一状态。

        fresh 路与 checkpoint 恢复路必须共用这一只 reducer；否则进程恰好死在
        accepted checkpoint 后，会因为走了另一套分支而跳过首个窄修、额外开放
        第三次 patch，或把 mixed HARD 重新扩大成整书 full candidate。

        返回 ``(feedback, next_base, next_kind, terminal_role_failure)``。
        """
        if role_patch_started:
            role_scope = extract.global_role_fate_patch_scope(
                prepared, evaluation)
            if role_scope is not None and accepted_role_patch_count < 2:
                return (
                    extract.global_role_fate_patch_feedback_rows(
                        evaluation, role_scope),
                    evaluation,
                    'role_fate_patch',
                    False,
                )
            # role patch 一旦开始即 sticky。第二份 accepted patch 仍红，或 patch
            # 引入 mixed/其它 HARD，都必须原样终态化；通用剩余轮次不能重新扩大
            # 改写面。终态仍返回完整反馈，供 rejected gate 留无正文计数。
            return evaluation.feedback_rows(), None, None, True
        role_scope = extract.global_role_fate_patch_scope(
            prepared, evaluation)
        if role_scope is not None:
            return (
                extract.global_role_fate_patch_feedback_rows(
                    evaluation, role_scope),
                evaluation,
                'role_fate_patch',
                False,
            )
        return (
            extract.global_full_candidate_feedback_rows(evaluation),
            None,
            None,
            False,
        )

    def run(self) -> NodeAgentResult[GlobalGrounding]:
        last_rejected: extract.GlobalCandidateEvaluation | None = None
        patch_base: extract.GlobalCandidateEvaluation | None = None
        patch_kind: GlobalPatchKind | None = None
        role_patch_started = False
        accepted_role_patch_count = 0
        try:
            prepared = extract.prepare_global_candidate(
                self.ctx, agent_envelope=True)
            extract.ensure_global_agent_run(self.ctx, prepared)
            extract.adopt_global_validator_resolution_checkpoint(
                self.ctx, prepared)
            restored = extract.restore_global_agent_checkpoint(
                self.ctx, self.client, prepared)
            if restored is not None:
                accepted_role_patch_count = restored.accepted_role_patch_count
                role_patch_started = accepted_role_patch_count > 0
                evaluation = extract.evaluate_global_candidate(
                    self.ctx, self.client, prepared, restored.response)
                if not evaluation.passed:
                    last_rejected = evaluation
                    (feedback, patch_base, patch_kind,
                     terminal_role_failure) = self._repair_transition(
                        prepared,
                        evaluation,
                        role_patch_started=role_patch_started,
                        accepted_role_patch_count=accepted_role_patch_count,
                    )
                    extract.assert_global_main_text_current(self.ctx, prepared)
                    if not self.client.queue_restored_code_validation_feedback(
                            feedback,
                            purpose=self.ctx.node,
                            allow_resume=not terminal_role_failure):
                        self._finalize_rejected(prepared, evaluation)
                else:
                    try:
                        artifact = extract.finalize_global_candidate(
                            self.ctx, self.client, prepared, evaluation)
                    except GateBlocked:
                        self._finish_restored(
                            restored.acceptance,
                            delivery_state='blocked',
                            error_kind='artifact_validation')
                        raise
                    except Exception:
                        self._finish_restored(
                            restored.acceptance,
                            delivery_state='unknown',
                            error_kind='artifact_verification')
                        raise
                    self._finish_restored(
                        restored.acceptance, delivery_state='delivered')
                    return NodeAgentResult(
                        artifact=artifact, turns=self.client.turns_used,
                        passed=True)
            while True:
                try:
                    if patch_kind is None:
                        turn_validator = \
                            extract.global_full_candidate_validator(prepared)
                    elif patch_base is None:
                        raise ProvenanceRefused(
                            f'stage2.global {patch_kind} 缺状态机 base candidate')
                    elif patch_kind == 'role_fate_patch':
                        turn_validator = extract.global_role_fate_patch_validator(
                            prepared, patch_base)
                    else:
                        raise ProvenanceRefused(
                            f'stage2.global 未登记 patch_kind={patch_kind!r}')
                    turn = self.client.ask_json(
                        prepared.system,
                        prepared.user,
                        model_cls=prepared.agent_response_model,
                        purpose=self.ctx.node,
                        max_tokens=extract.GLOBAL_MAX_TOKENS,
                        validator=turn_validator,
                        recovery_policy='stage2-global-v1',
                        retain_validator_rejection=True,
                    )
                except LlmFailed:
                    # 若本 session 至少产出过一份 schema 合格但业务 HARD 的候选，
                    # 后续 parse/schema 轮耗尽时仍把那份最后可恢复候选终态化一次。
                    # driver/subject/code 失败不走这条路，不能拿旧候选掩盖进程失败。
                    if (not self.client.driver_failed
                            and not self.client.can_resume):
                        expected_turn_kind = (
                            'full_candidate' if patch_kind is None else
                            patch_kind)
                        custody = extract.custody_global_validator_rejection(
                            self.ctx,
                            self.client,
                            prepared,
                            expected_turn_kind=expected_turn_kind,
                            base_response=(
                                None if patch_base is None else
                                patch_base._response),
                            accepted_role_patch_count=accepted_role_patch_count,
                        )
                        # typed validator rejection 是最后一份已经付费的 stdout，
                        # 优先进入 hash-bound custody；只有根本没有 typed 稿（例如
                        # parse/schema 耗尽）时，才终态化上一份业务 HARD 候选。
                        if custody is None and last_rejected is not None:
                            self._finalize_rejected(prepared, last_rejected)
                    self.client.finish(
                        process_state=(
                            'failed' if self.client.driver_failed else 'ok'),
                        delivery_state='failed',
                        error_kind=(
                            'agent_driver' if self.client.driver_failed
                            else 'response_validation'),
                    )
                    raise

                if patch_kind is None:
                    if (not isinstance(turn, GlobalCandidateAgentTurnResponse)
                            or not isinstance(
                                turn.payload, GlobalCandidateFullTurn)):
                        raise ProvenanceRefused(
                            'stage2.global full turn '
                            '通过 dynamic validator 后分支漂移')
                    response = turn.payload.candidate
                    base_evaluation = None
                    patch_audit: object | None = None
                elif patch_kind == 'role_fate_patch':
                    if (not isinstance(turn, GlobalCandidateAgentTurnResponse)
                            or not isinstance(
                                turn.payload, GlobalRoleFatePatchTurn)):
                        raise ProvenanceRefused(
                            'stage2.global role/fate patch turn 通过 dynamic '
                            'validator 后分支漂移')
                    if patch_base is None:
                        raise ProvenanceRefused(
                            'stage2.global role/fate patch 缺状态机 base candidate')
                    base_evaluation = patch_base
                    applied = extract.apply_global_role_fate_patch(
                        prepared, base_evaluation, turn.payload)
                    response = applied.response
                    accepted_role_patch_count += 1
                    if accepted_role_patch_count > 2:
                        raise ProvenanceRefused(
                            'stage2.global role/fate patch accepted 次数超过 2')
                    role_patch_started = True
                    patch_audit = applied
                else:
                    raise ProvenanceRefused(
                        f'stage2.global 未登记 patch_kind={patch_kind!r}')
                extract.write_global_agent_checkpoint(
                    self.ctx,
                    self.client,
                    prepared,
                    turn,
                    response,
                    base_response=(
                        None if base_evaluation is None else
                        base_evaluation._response),
                    accepted_role_patch_count=accepted_role_patch_count,
                )
                # checkpoint 先于审计：若进程死在两者之间，restore 会按同一事件
                # identity 幂等补尾；反过来不能让审计声称一份尚不可恢复的 patch。
                if patch_audit is not None:
                    attempt_id = self.client.accepted_attempt(self.ctx.node)
                    if attempt_id is None or base_evaluation is None:
                        raise ProvenanceRefused(
                            'stage2.global patch 已 checkpoint 但缺 accepted/base 身份')
                    if not hasattr(patch_audit, 'changed_field_count'):
                        raise ProvenanceRefused(
                            'stage2.global role/fate patch 审计载荷漂移')
                    self.ctx.ledger.note_role_fate_candidate_patch(
                        sid=self.ctx.sid,
                        node=self.ctx.node,
                        purpose=self.ctx.node,
                        attempt_id=attempt_id,
                        base_candidate_sha256=base_evaluation.response_sha256,
                        result_candidate_sha256=extract.global_response_sha256(
                            response),
                        evidence_table_sha256=turn.payload.evidence_table_sha256,
                        changed_field_count=patch_audit.changed_field_count,
                    )
                patch_base = None
                patch_kind = None

                evaluation = extract.evaluate_global_candidate(
                    self.ctx, self.client, prepared, response)
                if not evaluation.passed:
                    last_rejected = evaluation
                    (feedback, next_patch_base, next_patch_kind,
                     terminal_role_failure) = self._repair_transition(
                        prepared,
                        evaluation,
                        role_patch_started=role_patch_started,
                        accepted_role_patch_count=accepted_role_patch_count,
                    )
                    extract.assert_global_main_text_current(
                        self.ctx, prepared)
                    if self.client.queue_code_validation_feedback(
                            feedback, purpose=self.ctx.node,
                            allow_resume=not terminal_role_failure):
                        patch_base = next_patch_base
                        patch_kind = next_patch_kind
                        continue
                    # 只有循环预算耗尽才在这里写一次 INVALID；中间轮始终只在内存。
                    self._finalize_rejected(prepared, evaluation)

                artifact = extract.finalize_global_candidate(
                    self.ctx, self.client, prepared, evaluation)
                self.client.finish(
                    process_state='ok', delivery_state='delivered')
                return NodeAgentResult(
                    artifact=artifact, turns=self.client.turns_used,
                    passed=True)
        except GateBlocked:
            self.client.finish(
                process_state='ok', delivery_state='blocked',
                error_kind='artifact_validation')
            raise
        except ProvenanceRefused:
            self.client.finish(
                process_state='ok', delivery_state='unknown',
                error_kind='artifact_verification')
            raise
        except Exception:
            self.client.finish(
                process_state=('failed' if self.client.driver_failed else 'ok'),
                delivery_state=('unknown' if self.client.driver_failed else 'failed'),
                error_kind=('agent_driver' if self.client.driver_failed
                            else 'node_executor'),
            )
            raise
        finally:
            self.client.close()


BlueprintDriverFactory = Callable[[str, str, int], AgentDriver]


@dataclass(frozen=True)
class _BlueprintRevisionSpec:
    phase: str
    purpose: str
    chapter: int
    system: str
    user: str
    model_cls: type[BaseModel]
    max_tokens: int
    validator: Callable[[Any, Any], str | None] | None
    progress: RequestProgress


class BlueprintSessionPool:
    """把 producer 的共享 client 面路由到逐 ``(phase,purpose,chapter)`` session。

    pool 只聚合调用与 adoption 索引；checkpoint 字节和恢复验证仍全部交给
    ``stage2_runs`` 既有桥。clean checkpoint 只保存进程内 adoption 索引；
    需要修订时才把累计 turn/attempt 恢复到一条全新的 logical/provider session，
    不落第二份恢复账，也绝不拿旧 provider id 伪造 resume。
    """

    def __init__(
        self,
        ctx: RunContext,
        *,
        driver_factory: BlueprintDriverFactory | None = None,
        max_turns: int = 4,
        concurrency: int = STAGE2_BLUEPRINT_MAX_CONCURRENCY,
    ) -> None:
        if ctx.node != 'stage2.blueprint':
            raise ValueError(
                f'BlueprintSessionPool 只接受 stage2.blueprint，实得 {ctx.node!r}')
        if ctx.executor_profile is None:
            raise ValueError('stage2.blueprint executor 缺 resolved profile')
        if (not isinstance(max_turns, int) or isinstance(max_turns, bool)
                or max_turns < 1):
            raise ValueError('BlueprintSessionPool.max_turns 必须 >= 1')
        if (not isinstance(concurrency, int) or isinstance(concurrency, bool)
                or concurrency < 1):
            raise ValueError('BlueprintSessionPool.concurrency 必须 >= 1')
        self.ctx = ctx
        self.profile: AgentProfile = ctx.executor_profile
        self.model = self.profile.model
        self.max_turns = max_turns
        self.concurrency = min(STAGE2_BLUEPRINT_MAX_CONCURRENCY, concurrency)
        self._contract = active_contract()
        self._driver_factory = driver_factory or (
            lambda _phase, _purpose, _chapter: create_driver(
                self.profile, role='executor'))
        self._clients: dict[
            tuple[str, str, int], SessionAgentClient,
        ] = {}
        self._purpose_keys: dict[str, tuple[str, str, int]] = {}
        self._subjects: dict[str, tuple[str, str]] = {}
        self._restored: set[str] = set()
        self._restored_records: dict[str, dict[str, Any]] = {}
        self._probe: AgentProbe | None = None
        self._driver_owners: dict[int, tuple[str, str, int]] = {}
        self._provider_session_owners: dict[
            tuple[str, str], tuple[tuple[str, str, int], str],
        ] = {}
        self._failed_session_keys: set[tuple[str, str, int]] = set()
        self._closed = False
        self._lock = threading.RLock()

    @property
    def turns_used(self) -> int:
        with self._lock:
            total = 0
            for purpose, key in self._purpose_keys.items():
                client = self._clients.get(key)
                if client is not None:
                    total += client.turns_used
                    continue
                record = self._restored_records.get(purpose)
                turn = None if record is None else record.get(
                    'accepted_turn_index')
                if isinstance(turn, int) and not isinstance(turn, bool):
                    total += turn
            return total

    @property
    def driver_failed(self) -> bool:
        with self._lock:
            return any(client.driver_failed for client in self._clients.values())

    @property
    def isolation_failed(self) -> bool:
        with self._lock:
            return bool(self._failed_session_keys)

    @property
    def session_count(self) -> int:
        with self._lock:
            return len(self._clients)

    def _phase_of(self, purpose: str) -> str:
        if ':boundaries:' in purpose:
            return 'boundaries'
        if self._contract.topology == 'boundary_then_detail':
            return 'detail'
        return 'blueprint'

    def _new_client(
            self, phase: str, purpose: str, chapter: int, *,
            probe_driver: bool = True,
    ) -> SessionAgentClient:
        key = (phase, purpose, chapter)
        driver = self._driver_factory(phase, purpose, chapter)
        driver_id = id(driver)
        prior_owner = self._driver_owners.get(driver_id)
        if prior_owner is not None and prior_owner != key:
            self._failed_session_keys.update((prior_owner, key))
            raise ProvenanceRefused(
                f'{purpose}: driver factory 把同一对象同时给了 '
                f'{prior_owner} 与 {key}；已在首个 model start 前拒绝')
        self._driver_owners[driver_id] = key
        try:
            if not probe_driver:
                if (driver.driver != self.profile.driver
                        or (self._probe is not None
                            and (driver.driver != self._probe.driver
                                 or driver.provider != self._probe.provider))):
                    self._failed_session_keys.add(key)
                    raise ProvenanceRefused(
                        f'{purpose}: checkpoint 恢复用 driver 与 profile/pool 漂移')
            elif self._probe is None:
                probe = driver.probe()
                if (driver.driver != self.profile.driver
                        or probe.driver != self.profile.driver
                        or probe.provider != driver.provider
                        or not probe.available):
                    self._failed_session_keys.add(key)
                    raise ProvenanceRefused(
                        f'{purpose}: driver/probe/profile 身份不一致：'
                        f'profile={self.profile.driver!r}, '
                        f'driver={driver.driver!r}, '
                        f'probe={probe.driver!r}/{probe.provider!r}')
                self._probe = probe
            elif (driver.driver != self._probe.driver
                  or driver.provider != self._probe.provider):
                self._failed_session_keys.add(key)
                raise ProvenanceRefused(
                    f'{purpose}: driver factory 在同一 pool 内漂移了厂商身份')
            return SessionAgentClient(
                self.ctx,
                self.profile,
                driver,
                chapter=chapter,
                parent_run_id=self.ctx.run_id,
                max_turns=self.max_turns,
                preflight_probe=self._probe,
            )
        except Exception:
            self._driver_owners.pop(driver_id, None)
            driver.close()
            raise

    def _client_for(
            self, phase: str, purpose: str, chapter: int,
    ) -> SessionAgentClient:
        key = (phase, purpose, chapter)
        with self._lock:
            existing_key = self._purpose_keys.get(purpose)
            if existing_key is not None and existing_key != key:
                raise ProvenanceRefused(
                    f'{purpose}: 已绑定 session key={existing_key}，'
                    f'不能改绑为 {key}')
            client = self._clients.get(key)
            if client is None:
                if purpose in self._restored:
                    raise ProvenanceRefused(
                        f'{purpose}: checkpoint 只能在联合 HARD 修订口'
                        '以新 session 承接，不能伪造原 session resume')
                client = self._new_client(phase, purpose, chapter)
                self._clients[key] = client
                self._purpose_keys[purpose] = key
            return client

    def prepare_initial_sessions(self) -> None:
        """在首个 model start 前构造本批所有缺失 checkpoint 的 driver。

        这一遍只用 producer 的 ``build_prompts`` 取章号，不读取或解释
        checkpoint 内容；恢复身份仍只能由 ``stage2_runs`` 校验。
        目录中有待验 checkpoint 时跳过 driver，因而全量恢复是
        零 factory/零 probe/零 start。
        """
        requests = extract.build_prompts(self.ctx)
        chapters = tuple(
            int(chapter) for chapter, _system, _user in requests
            if chapter is not None
        )
        if self._contract.topology == 'boundary_then_detail':
            keys = tuple(
                (phase, (
                    f'{self.ctx.node}:boundaries:ch{chapter}'
                    if phase == 'boundaries'
                    else f'{self.ctx.node}:ch{chapter}'
                ), chapter)
                for phase in ('boundaries', 'detail')
                for chapter in chapters
            )
        else:
            keys = tuple(
                ('blueprint', f'{self.ctx.node}:ch{chapter}', chapter)
                for chapter in chapters
            )
        may_restore = self.ctx.cache.name != 'refresh-write'
        with self._lock:
            for phase, purpose, chapter in keys:
                checkpoint = paths.stage2_blueprint_checkpoint_path(
                    self.ctx.novel_dir, self.ctx.run_id, phase, chapter)
                if may_restore and checkpoint.exists():
                    continue
                key = (phase, purpose, chapter)
                if key in self._clients:
                    continue
                client = self._new_client(*key)
                self._clients[key] = client
                self._purpose_keys[purpose] = key

    def _client_of_purpose(self, purpose: str) -> SessionAgentClient | None:
        with self._lock:
            key = self._purpose_keys.get(purpose)
            return None if key is None else self._clients.get(key)

    def _register_provider_owner(
            self, key: tuple[str, str, int], logical_session_id: str,
            provider_session_id: object,
    ) -> None:
        """provider session 也必须与 phase/purpose/chapter 一对一。"""
        if not isinstance(provider_session_id, str) or not provider_session_id:
            return
        if not isinstance(logical_session_id, str) or not logical_session_id:
            raise ProvenanceRefused(
                f'{key[1]}: provider session 缺 logical session owner')
        session_key = (self.profile.driver, provider_session_id)
        owner = (key, logical_session_id)
        with self._lock:
            prior_owner = self._provider_session_owners.get(session_key)
            if prior_owner is not None and prior_owner != owner:
                self._failed_session_keys.update((prior_owner[0], key))
                raise ProvenanceRefused(
                    f'{key[1]}: provider session {provider_session_id!r} '
                    f'已属于 {prior_owner[0]}/logical={prior_owner[1]!r}；'
                    '拒绝跨章/跨相位共享或 crash 恢复偷接旧 session')
            self._provider_session_owners[session_key] = owner

    @staticmethod
    def _scope_validator(
        chapter: int,
        batch_index: int,
        validator: Callable[[Any, Any], str | None] | None,
    ) -> Callable[[Any, Any], str | None]:
        """agent 路径把章节 scope 也放进同 session validator，避免采纳不可编译响应。"""
        def validate(obj: Any, response: Any) -> str | None:
            if validator is not None:
                why = validator(obj, response)
                if why is not None:
                    return why
            raw = obj.model_dump(by_alias=True).get('blueprints') or []
            kept, violation = extract._scope_split(raw, chapter, batch_index)
            if kept is None:
                return f'ch{chapter} 响应没有且仅有目标章'
            if violation is not None:
                return f'ch{chapter} 响应混入目标章之外的内容'
            return None
        return validate

    def ask_json(self, system: str, user: str, **kwargs: Any) -> Any:
        model_cls = kwargs.pop('model_cls', None)
        if (not isinstance(model_cls, type)
                or not issubclass(model_cls, BaseModel)):
            raise ProvenanceRefused(
                'stage2.blueprint 请求缺显式 Pydantic model_cls')
        progress = kwargs.pop('progress', None)
        if not isinstance(progress, RequestProgress):
            raise ProvenanceRefused(
                'stage2.blueprint 每次请求必须带可验证的 chapter progress')
        purpose = str(kwargs.get('purpose') or '')
        if not purpose:
            raise ProvenanceRefused('stage2.blueprint 请求缺 purpose')
        prefer_content = kwargs.pop(
            'prefer_content_over_reasoning_salvage', False)
        recovery_policy = kwargs.pop('recovery_policy', None)
        if prefer_content:
            raise ProvenanceRefused(
                f'{purpose}: agent session 不支持伪造 legacy reasoning salvage 策略')
        if recovery_policy != 'stage2-blueprint-v1':
            raise ProvenanceRefused(
                f'{purpose}: 未登记的 blueprint recovery policy '
                f'{recovery_policy!r}')
        validator = kwargs.pop('validator', None)
        kwargs['validator'] = self._scope_validator(
            progress.chapter, progress.index, validator)
        kwargs['progress'] = progress
        phase = self._phase_of(purpose)
        client = self._client_for(phase, purpose, progress.chapter)
        with self._lock:
            subject = self._subjects.get(purpose)
            if subject is not None and subject != (system, user):
                raise ProvenanceRefused(
                    f'{purpose}: 同一章节 session 的原始 system/user 漂移')
            self._subjects[purpose] = (system, user)
        key = (phase, purpose, progress.chapter)
        try:
            return client.ask_json(
                system, user, model_cls=model_cls, **kwargs)
        finally:
            self._register_provider_owner(
                key, client.session_run_id,
                client.observed_provider_session_id)

    def json_request_policy(
            self, phases: Any, *, recovery_policy: str,
    ) -> dict[str, Any]:
        """首个 session/driver 创建前绑定 resolved agent 请求策略。"""
        return SessionAgentClient.build_json_request_policy(
            self.profile, phases, recovery_policy=recovery_policy)

    def accepted_checkpoint(self, purpose: str) -> dict[str, Any]:
        client = self._client_of_purpose(purpose)
        if client is None:
            raise ProvenanceRefused(f'{purpose}: 尚无章节 session，不能铸 checkpoint')
        return client.accepted_checkpoint(purpose)

    def restore_checkpoint_acceptance(
            self, purpose: str, record: Mapping[str, Any], **kwargs: Any,
    ) -> None:
        chapter = record.get('chapter')
        if not isinstance(chapter, int) or isinstance(chapter, bool) or chapter < 1:
            raise ProvenanceRefused(f'{purpose}: checkpoint 缺合法 chapter')
        phase = str(record.get('phase') or self._phase_of(purpose))
        if phase != self._phase_of(purpose):
            raise ProvenanceRefused(
                f'{purpose}: checkpoint phase={phase!r} 与当前契约不一致')
        system = kwargs.get('system')
        user = kwargs.get('user')
        max_tokens = kwargs.get('max_tokens')
        recovery_policy = kwargs.get('recovery_policy')
        if set(kwargs) != {
                'system', 'user', 'max_tokens', 'recovery_policy'}:
            raise ProvenanceRefused(
                f'{purpose}: checkpoint restore 含未登记参数 '
                f'{sorted(set(kwargs) - {"system", "user", "max_tokens", "recovery_policy"})}')
        if (not isinstance(system, str) or not isinstance(user, str)
                or not isinstance(max_tokens, int)
                or isinstance(max_tokens, bool) or max_tokens < 1
                or recovery_policy != 'stage2-blueprint-v1'):
            raise ProvenanceRefused(
                f'{purpose}: checkpoint restore 缺当前 subject/ceiling/protocol')
        binding = record.get('accepted_request')
        if (not isinstance(binding, Mapping)
                or binding.get('requested_output_token_ceiling') != max_tokens
                or record.get('profile') != self.profile.model_dump(mode='json')):
            raise ProvenanceRefused(
                f'{purpose}: checkpoint acceptance 与当前 profile/ceiling 不一致')
        key = (phase, purpose, chapter)
        logical_id = record.get('session_run_id')
        if not isinstance(logical_id, str) or not logical_id:
            raise ProvenanceRefused(
                f'{purpose}: checkpoint 缺 logical session owner')
        with self._lock:
            existing_key = self._purpose_keys.get(purpose)
            if existing_key is not None and existing_key != key:
                raise ProvenanceRefused(
                    f'{purpose}: checkpoint 从 {existing_key} 漂移到 {key}')
            if key in self._clients:
                raise ProvenanceRefused(
                    f'{purpose}: live session 已存在，不能再恢复 checkpoint')
            # ``stage2_runs.restore_checkpoint`` 在进入此口前已完成
            # stdout/schema/ledger/profile/subject 全链校验与 prior-attempt claim。
            # pool 只保留 adoption 索引，不为盘上 provider id 伪造
            # 一个本进程可 resume 的 driver/client。
            self._purpose_keys[purpose] = key
            self._subjects[purpose] = (system, user)
            self._restored.add(purpose)
            self._restored_records[purpose] = dict(record)
        # 先保存 core 已验证 acceptance，再登记 provider owner；若这里发现跨章
        # 污染，finish_all 仍能给这条旧 logical session 补失败 ended。
        self._register_provider_owner(
            key, logical_id, record.get('provider_session_id'))

    def accepted_attempt(self, purpose: str) -> str | None:
        client = self._client_of_purpose(purpose)
        if client is not None:
            attempt = client.accepted_attempt(purpose)
            if attempt is not None:
                return attempt
        with self._lock:
            restored = self._restored_records.get(purpose)
            if restored is None:
                return None
            value = restored.get('attempt_id')
            return value if isinstance(value, str) and value else None

    def accepted_models(self, *purposes: str) -> tuple[str, ...]:
        selected: Sequence[str]
        with self._lock:
            selected = purposes or tuple(sorted(self._purpose_keys))
        models: set[str] = set()
        for purpose in selected:
            client = self._client_of_purpose(purpose)
            if (client is not None
                    and client.accepted_attempt(purpose) is not None):
                models.update(client.accepted_models(purpose))
                continue
            with self._lock:
                restored = self._restored_records.get(purpose)
                model = None if restored is None else restored.get('model')
            if isinstance(model, str) and model not in ('', 'unknown'):
                models.add(model)
        return tuple(sorted(models))

    def can_revise(self, purpose: str) -> bool:
        client = self._client_of_purpose(purpose)
        with self._lock:
            if purpose in self._restored:
                return True
        return bool(client is not None and client.can_resume)

    def purpose_driver_failed(self, purpose: str) -> bool:
        client = self._client_of_purpose(purpose)
        return bool(client is not None and client.driver_failed)

    def _start_from_restored(
            self, spec: _BlueprintRevisionSpec,
    ) -> tuple[SessionAgentClient, dict[str, Any]]:
        """恢复累计预算/attempt 后开新 provider session；绝不 resume 旧 id。"""
        key = (spec.phase, spec.purpose, spec.chapter)
        with self._lock:
            record = self._restored_records.get(spec.purpose)
            subject = self._subjects.get(spec.purpose)
            if record is None or subject != (spec.system, spec.user):
                raise ProvenanceRefused(
                    f'{spec.purpose}: checkpoint 恢复记录或原始 subject 漂移')
            turn = record.get('accepted_turn_index')
            if not isinstance(turn, int) or isinstance(turn, bool) or turn < 1:
                raise ProvenanceRefused(
                    f'{spec.purpose}: checkpoint 缺累计 turn budget')
            client = self._new_client(
                *key, probe_driver=turn < self.max_turns)
            self._clients[key] = client
        try:
            client.restore_checkpoint_acceptance(
                spec.purpose,
                record,
                system=spec.system,
                user=spec.user,
                max_tokens=spec.max_tokens,
                recovery_policy='stage2-blueprint-v1',
            )
        except Exception:
            with self._lock:
                self._clients.pop(key, None)
            client.close()
            raise
        return client, record

    def revise(
            self,
            spec: _BlueprintRevisionSpec,
            feedback: Sequence[Mapping[str, object]],
    ) -> dict[str, Any] | None:
        """把无正文安全反馈交回 live session；返回新的目标章 raw blueprint。"""
        if not feedback:
            return None
        client = self._client_of_purpose(spec.purpose)
        with self._lock:
            restored = spec.purpose in self._restored
            subject = self._subjects.get(spec.purpose, (spec.system, spec.user))
        if client is None and not restored:
            raise ProvenanceRefused(
                f'{spec.purpose}: 没有初始/恢复 session 可承接业务反馈')
        if subject != (spec.system, spec.user):
            raise ProvenanceRefused(
                f'{spec.purpose}: 联合 HARD 修订的原始 system/user 已漂移')
        key = (spec.phase, spec.purpose, spec.chapter)
        if restored:
            client, _record = self._start_from_restored(spec)
            try:
                queued = client.queue_restored_code_validation_feedback(
                    feedback, purpose=spec.purpose)
            except Exception:
                with self._lock:
                    self._clients.pop(key, None)
                client.close()
                raise
            if not queued:
                with self._lock:
                    self._clients.pop(key, None)
                client.close()
                return None
            subject_user = spec.user
        else:
            assert client is not None
            if not client.can_resume:
                return None
            if not client.queue_code_validation_feedback(
                    feedback, purpose=spec.purpose):
                return None
            subject_user = subject[1]

        try:
            response = client.ask_json(
                spec.system,
                subject_user,
                model_cls=spec.model_cls,
                purpose=spec.purpose,
                max_tokens=spec.max_tokens,
                validator=self._scope_validator(
                    spec.chapter, spec.progress.index, spec.validator),
                progress=spec.progress,
                recovery_policy='stage2-blueprint-v1',
            )
        finally:
            self._register_provider_owner(
                key, client.session_run_id,
                client.observed_provider_session_id)
        with self._lock:
            # 只在新 session 真正接纳了修订结果后退休旧 adoption。
            # 若 schema/driver 失败，旧候选仍能被终态化为 INVALID。
            self._restored.discard(spec.purpose)
            self._restored_records.pop(spec.purpose, None)
        raw = response.model_dump(by_alias=True).get('blueprints') or []
        kept, violation = extract._scope_split(
            raw, spec.chapter, spec.progress.index)
        if kept is None or violation is not None:
            raise ProvenanceRefused(
                f'{spec.purpose}: SessionAgentClient 已接受未通过 scope validator 的响应')
        return kept

    def finish_all(
            self, *, process_state: str, delivery_state: str,
            error_kind: str | None,
    ) -> None:
        with self._lock:
            clients = tuple(self._clients.items())
            isolation_failed = set(self._failed_session_keys)
            restored = tuple(
                (purpose, dict(record))
                for purpose, record in self._restored_records.items()
            )
        for key, client in clients:
            if process_state == 'failed':
                failed_here = client.driver_failed or key in isolation_failed
                client.finish(
                    process_state=('failed' if failed_here else 'ok'),
                    delivery_state=delivery_state,
                    error_kind=(
                        'session_isolation' if client.provider_session_reused
                        else 'agent_driver' if client.driver_failed
                        else 'session_isolation' if key in isolation_failed
                        else 'peer_session_failed'),
                )
            else:
                client.finish(
                    process_state=process_state,
                    delivery_state=delivery_state,
                    error_kind=error_kind,
                )
        # checkpoint 的旧 logical session 没有本进程 client。producer 终验后用
        # durable acceptance 指回；dirty 旧会话若已由 queue 收尾，公共桥只复核
        # 身份并保留原历史，不拿本次观察改写它。
        for purpose, record in restored:
            with self._lock:
                key = self._purpose_keys.get(purpose)
            if key is None:
                raise ProvenanceRefused(
                    f'{purpose}: restored session 缺 phase/purpose/chapter owner')
            if process_state == 'failed':
                failed_here = key in isolation_failed
                states = (
                    'failed' if failed_here else 'ok',
                    delivery_state,
                    ('session_isolation' if failed_here
                     else 'peer_session_failed'),
                )
            else:
                states = (process_state, delivery_state, error_kind)
            agent_session_audit.finish_restored_session(
                self.ctx.novel_dir.parent,
                record,
                process_state=states[0],
                delivery_state=states[1],
                error_kind=states[2],
                end_code_version=prov.code_version(),
                only_if_open=True,
            )

    def close(self) -> None:
        with self._lock:
            if self._closed:
                return
            clients = tuple(self._clients.values())
            self._closed = True
        for client in clients:
            client.close()


class Stage2BlueprintAgent(NodeExecutorAgent[ChapterBlueprints]):
    """逐章私有 session 并发产候选，整书只由 producer 校验和正式提交。"""

    node = 'stage2.blueprint'

    def __init__(
        self,
        ctx: RunContext,
        *,
        driver_factory: BlueprintDriverFactory | None = None,
        max_turns: int = 4,
        concurrency: int = STAGE2_BLUEPRINT_MAX_CONCURRENCY,
    ) -> None:
        if ctx.node != self.node:
            raise ValueError(f'{type(self).__name__} 只接受 {self.node}，实得 {ctx.node}')
        if ctx.executor_profile is None:
            raise ValueError(f'{self.node} executor 缺 resolved profile')
        self.ctx = ctx
        self.pool = BlueprintSessionPool(
            ctx,
            driver_factory=driver_factory,
            max_turns=max_turns,
            concurrency=concurrency,
        )

    @staticmethod
    def _safe_feedback(
            evaluation: extract.BlueprintCandidateEvaluation,
    ) -> dict[int, list[dict[str, object]]]:
        """只保留规则元数据与计数；detail/evidence/future text 永不进入 session。"""
        detail_targets = tuple(sorted({
            int(chapter)
            for purpose, chapter in evaluation.purposes
            if chapter is not None and ':boundaries:' not in purpose
        }))
        routed: dict[int, list[dict[str, object]]] = {}
        for row in evaluation.feedback:
            # 没有定位章的书级 HARD 仍可安全修订：只将静态
            # rule/item/requirement/count 广播给 detail 目标，不广播 finding。
            chapters = row.chapters or detail_targets
            for chapter in chapters:
                if chapter not in detail_targets:
                    continue
                routed.setdefault(chapter, []).append({
                    'rule': row.rule,
                    'item': row.item,
                    'requirement': row.requirement,
                    'count': len(row.findings),
                    'chapter': chapter,
                })
        return routed

    def _revision_specs(
            self, batch: dict[str, Any], chapters: Sequence[int],
    ) -> dict[int, _BlueprintRevisionSpec]:
        spec = contract_for(str(batch['contract']))
        main_text = batch['main_text']
        ordinals = [
            int(section.get('chapter_ordinal') or 0)
            for section in main_text
        ]
        index_of = {ordinal: index for index, ordinal in enumerate(ordinals, 1)}
        request_of = {
            int(ordinal): (system, user)
            for ordinal, system, user in batch['requests']
            if ordinal is not None
        }
        frozen_projection = batch.get('character_projection')
        _projection, current_projection = \
            extract._require_blueprint_character_projection(
                ArtifactStore(self.ctx))
        if current_projection != frozen_projection:
            raise ProvenanceRefused(
                'stage2.blueprint agent revision 前称名投影已偏离 request batch；'
                '拒绝让旧 session 按新名册继续修订')
        inventory = blueprint_inventory_from_projection(frozen_projection)
        visible = inventory['visible_character_refs']
        paragraph_count = {
            int(section.get('chapter_ordinal') or 0):
            len(paragraph_spans(str(section.get('text') or '')))
            for section in main_text
        }
        candidates = (
            hinge_candidates_by_chapter(main_text)
            if spec.uses_hinge_candidates else {}
        )
        out: dict[int, _BlueprintRevisionSpec] = {}
        for chapter in sorted(set(int(value) for value in chapters)):
            if chapter not in request_of or chapter not in index_of:
                continue
            system, user = request_of[chapter]
            if spec.topology == 'boundary_then_detail':
                phase = 'detail'
                frozen = batch.get('frozen_boundaries') or {}
                validator = extract._detail_validator(
                    chapter,
                    list(frozen[chapter]),
                    set(visible.get(chapter, {})),
                )
            elif spec.topology == 'scaffolded_single':
                phase = 'blueprint'
                validator = extract._scaffolded_validator(
                    chapter,
                    paragraph_count[chapter],
                    set(visible.get(chapter, {})),
                    candidate_rows=candidates.get(chapter, []),
                    narration_refs=True,
                )
            else:
                phase = 'blueprint'
                validator = None
            out[chapter] = _BlueprintRevisionSpec(
                phase=phase,
                purpose=f'{self.ctx.node}:ch{chapter}',
                chapter=chapter,
                system=system,
                user=user,
                model_cls=spec.response_model,
                max_tokens=extract.BLUEPRINT_MAX_TOKENS,
                validator=validator,
                progress=RequestProgress(
                    chapter=chapter,
                    index=index_of[chapter],
                    total=len(ordinals),
                ),
            )
        return out

    @staticmethod
    def _replace_chapters(
            batch: dict[str, Any], replacements: Mapping[int, dict[str, Any]],
    ) -> dict[str, Any]:
        updated = {**batch}
        by_chapter = {
            int(row.get('chapter_ordinal') or 0): copy.deepcopy(row)
            for row in batch['raw_blueprints']
        }
        by_chapter.update({
            int(chapter): copy.deepcopy(row)
            for chapter, row in replacements.items()
        })
        order = [
            int(section.get('chapter_ordinal') or 0)
            for section in batch['main_text']
        ]
        updated['raw_blueprints'] = [
            by_chapter[chapter] for chapter in order if chapter in by_chapter
        ]
        # agent 的 scope 已进 SessionAgentClient validator；默认单相契约中，目标章
        # 修订成功即可退休那一章旧的 scope 结论。两相的 boundary 结论不在 detail
        # 修订所有权内，保守保留。
        if str(batch.get('contract')) != 'extraction-ir-v2':
            targets = set(replacements)
            # scope 载体的解析只有 producer 那一个落点；executor 不另造一把
            # JSON 尺子。坏载体也沿用 producer 的关闭式「保留」语义。
            updated['scope_violations'] = [
                raw for raw in batch.get('scope_violations') or []
                if extract._keep_scope_violation(raw, targets)
            ]
        return updated

    def _revise_wave(
            self,
            batch: dict[str, Any],
            evaluation: extract.BlueprintCandidateEvaluation,
    ) -> tuple[dict[str, Any], bool]:
        routed = self._safe_feedback(evaluation)
        specs = self._revision_specs(batch, tuple(routed))
        work = {
            chapter: spec for chapter, spec in specs.items()
            if routed.get(chapter) and self.pool.can_revise(spec.purpose)
        }
        if not work:
            return batch, False
        replacements: dict[int, dict[str, Any]] = {}
        failures: list[tuple[str, Exception]] = []
        with ThreadPoolExecutor(
            max_workers=min(self.pool.concurrency, max(1, len(work)))
        ) as executor:
            futures = {
                executor.submit(
                    self.pool.revise, spec, routed[chapter]): spec.purpose
                for chapter, spec in work.items()
            }
            for future in as_completed(futures):
                purpose = futures[future]
                try:
                    replacement = future.result()
                except Exception as exc:
                    failures.append((purpose, exc))
                    continue
                if replacement is not None:
                    chapter = next(
                        spec.chapter for spec in work.values()
                        if spec.purpose == purpose)
                    replacements[chapter] = replacement
        for purpose, exc in failures:
            if self.pool.purpose_driver_failed(purpose) \
                    or not isinstance(exc, LlmFailed):
                raise exc
        if not replacements:
            return batch, False
        return self._replace_chapters(batch, replacements), True

    def run(self) -> NodeAgentResult[ChapterBlueprints]:
        try:
            self.pool.prepare_initial_sessions()
            batch = extract.request_blueprints(self.ctx, self.pool)
            evaluation = extract.evaluate_blueprint_candidate(
                self.ctx, self.pool, batch)
            while not evaluation.passed:
                batch, progressed = self._revise_wave(batch, evaluation)
                if not progressed:
                    # 整书/不可路由 HARD 或所有目标 session 已耗尽：只在这里终态化一次。
                    extract.finalize_blueprint_candidate(
                        self.ctx, self.pool, batch, evaluation)
                    raise AssertionError('HARD blueprint finalize 必须抛 GateBlocked')
                evaluation = extract.evaluate_blueprint_candidate(
                    self.ctx, self.pool, batch)

            report = extract.finalize_blueprint_candidate(
                self.ctx, self.pool, batch, evaluation)
            artifact = ArtifactStore(self.ctx).read(
                ChapterBlueprints, extract.BLUEPRINT_REL)
            self.pool.finish_all(
                process_state='ok', delivery_state='delivered', error_kind=None)
            return NodeAgentResult(
                artifact=artifact, turns=self.pool.turns_used,
                passed=report.passed, report=report)
        except GateBlocked:
            self.pool.finish_all(
                process_state='ok', delivery_state='blocked',
                error_kind='artifact_validation')
            raise
        except ProvenanceRefused:
            if self.pool.isolation_failed:
                self.pool.finish_all(
                    process_state='failed', delivery_state='unknown',
                    error_kind='session_isolation')
            else:
                self.pool.finish_all(
                    process_state='ok', delivery_state='unknown',
                    error_kind='artifact_verification')
            raise
        except Exception:
            self.pool.finish_all(
                process_state=(
                    'failed' if self.pool.driver_failed else 'ok'),
                delivery_state=(
                    'unknown' if self.pool.driver_failed else 'failed'),
                error_kind=(
                    'agent_driver' if self.pool.driver_failed else 'node_executor'),
            )
            raise
        finally:
            self.pool.close()


Stage4DesignDriverFactory = Callable[[str, int | None], AgentDriver]


class Stage4DesignSessionPool:
    """Stage4A 的 strategy + 逐章私有 session 路由与恢复桥。

    producer 先串行复验所有现存 checkpoint，再把本批缺失 unit 一次性交给
    :meth:`prepare_initial_sessions`。pool 在任一 wire 前构造完这一批全部
    driver/client/cwd；完整恢复只登记 durable acceptance，保持零 factory、
    零 probe、零 model start。strategy 与每章的 driver、logical session、
    provider session 都必须一对一，不能以“同一节点”为由共享上下文。
    """

    def __init__(
        self,
        ctx: RunContext,
        *,
        driver_factory: Stage4DesignDriverFactory | None = None,
        max_turns: int = 4,
        concurrency: int = STAGE4_DESIGN_MAX_CONCURRENCY,
    ) -> None:
        if ctx.node != 'stage4.design':
            raise ValueError(
                f'Stage4DesignSessionPool 只接受 stage4.design，实得 {ctx.node!r}')
        if ctx.executor_profile is None:
            raise ValueError('stage4.design executor 缺 resolved profile')
        if (not isinstance(max_turns, int) or isinstance(max_turns, bool)
                or max_turns < 1):
            raise ValueError('Stage4DesignSessionPool.max_turns 必须 >= 1')
        if (not isinstance(concurrency, int) or isinstance(concurrency, bool)
                or concurrency < 1):
            raise ValueError('Stage4DesignSessionPool.concurrency 必须 >= 1')
        self.ctx = ctx
        self.profile: AgentProfile = ctx.executor_profile
        self.model = self.profile.model
        self.max_turns = max_turns
        self.concurrency = min(STAGE4_DESIGN_MAX_CONCURRENCY, concurrency)
        self._driver_factory = driver_factory or (
            lambda _purpose, _chapter: create_driver(
                self.profile, role='executor'))
        self._clients: dict[str, SessionAgentClient] = {}
        self._restored: dict[str, dict[str, Any]] = {}
        self._subjects: dict[str, tuple[str, str]] = {}
        self._known_units: dict[str, int | None] = {}
        self._prepared_units: set[str] = set()
        self._wired_purposes: set[str] = set()
        self._progress: dict[str, RequestProgress] = {}
        self._probe: AgentProbe | None = None
        self._driver_owners: dict[int, str] = {}
        self._provider_session_owners: dict[
            tuple[str, str], tuple[str, str],
        ] = {}
        self._isolation_failed_units: set[str] = set()
        self._driver_setup_failed_units: set[str] = set()
        self._closed = False
        self._lock = threading.RLock()

    @staticmethod
    def _unit(purpose: str, chapter: object) -> tuple[str, int | None]:
        if chapter is not None and (
                not isinstance(chapter, int) or isinstance(chapter, bool)
                or chapter < 1):
            raise ProvenanceRefused(
                f'stage4.design unit 章号必须为 None 或正整数，实得 {chapter!r}')
        normalized = chapter if isinstance(chapter, int) else None
        expected = stage4_runs.design_unit_purpose(normalized)
        if purpose != expected:
            raise ProvenanceRefused(
                f'stage4.design unit purpose/chapter 不闭合：'
                f'expected={expected!r}, actual={purpose!r}/{chapter!r}')
        return purpose, normalized

    @staticmethod
    def _unit_sort(row: tuple[str, int | None]) -> tuple[int, int]:
        _purpose, chapter = row
        return (0, 0) if chapter is None else (1, chapter)

    @property
    def turns_used(self) -> int:
        with self._lock:
            live = sum(client.turns_used for client in self._clients.values())
            restored = sum(
                int(record['accepted_turn_index'])
                for purpose, record in self._restored.items()
                if purpose not in self._clients
                and isinstance(record.get('accepted_turn_index'), int)
                and not isinstance(record.get('accepted_turn_index'), bool)
            )
        return live + restored

    @property
    def driver_failed(self) -> bool:
        with self._lock:
            return (bool(self._driver_setup_failed_units)
                    or any(client.driver_failed
                           for client in self._clients.values()))

    @property
    def isolation_failed(self) -> bool:
        with self._lock:
            return bool(self._isolation_failed_units)

    @property
    def session_count(self) -> int:
        with self._lock:
            return len(self._clients)

    def _new_client(
        self, purpose: str, chapter: int | None,
    ) -> SessionAgentClient:
        try:
            driver = self._driver_factory(purpose, chapter)
        except BaseException:
            self._driver_setup_failed_units.add(purpose)
            raise
        driver_id = id(driver)
        prior_owner = self._driver_owners.get(driver_id)
        if prior_owner is not None and prior_owner != purpose:
            # 这是已有 unit 正在持有的同一对象，不能在这里 close 它。
            self._isolation_failed_units.update((prior_owner, purpose))
            raise ProvenanceRefused(
                'stage4.design driver factory 把同一对象同时给了 '
                f'{prior_owner}/{purpose}；已在首个 model start 前拒绝')
        self._driver_owners[driver_id] = purpose
        try:
            if self._probe is None:
                probe = driver.probe()
                if not probe.available:
                    self._driver_setup_failed_units.add(purpose)
                    raise ProvenanceRefused(
                        f'{purpose}: executor driver 本地探针不可用：'
                        f'{probe.detail or "unknown"}')
                if (driver.driver != self.profile.driver
                        or probe.driver != self.profile.driver
                        or probe.provider != driver.provider):
                    self._isolation_failed_units.add(purpose)
                    raise ProvenanceRefused(
                        f'{purpose}: driver/probe/profile 身份不闭合：'
                        f'profile={self.profile.driver!r}, '
                        f'driver={driver.driver!r}, '
                        f'probe={probe.driver!r}/{probe.provider!r}')
                self._probe = probe
            elif (driver.driver != self._probe.driver
                  or driver.provider != self._probe.provider):
                self._isolation_failed_units.add(purpose)
                raise ProvenanceRefused(
                    f'{purpose}: driver factory 在同一 Stage4A pool 内漂移厂商身份')
            return SessionAgentClient(
                self.ctx,
                self.profile,
                driver,
                chapter=chapter,
                parent_run_id=self.ctx.run_id,
                max_turns=self.max_turns,
                preflight_probe=self._probe,
            )
        except BaseException:
            if purpose not in self._isolation_failed_units:
                self._driver_setup_failed_units.add(purpose)
            if self._driver_owners.get(driver_id) == purpose:
                self._driver_owners.pop(driver_id, None)
            try:
                driver.close()
            except Exception:
                pass
            raise

    def _rebuild_progress(self) -> None:
        chapters = sorted(
            chapter for chapter in self._known_units.values()
            if chapter is not None)
        if len(chapters) != len(set(chapters)):
            raise ProvenanceRefused('stage4.design unit 章号重复')
        total = len(chapters)
        self._progress = {
            stage4_runs.design_unit_purpose(chapter): RequestProgress(
                chapter=chapter, index=index, total=total)
            for index, chapter in enumerate(chapters, 1)
        }

    def prepare_initial_sessions(
        self, units: Sequence[tuple[str, int | None]],
    ) -> None:
        """为本批全部缺失 unit 预建私有 client，恢复 unit 一律跳过。"""
        if not isinstance(units, Sequence) or isinstance(
                units, (str, bytes, bytearray)):
            raise ProvenanceRefused(
                'stage4.design prepare_initial_sessions 必须接 unit 序列')
        normalized: list[tuple[str, int | None]] = []
        seen: set[str] = set()
        for raw in units:
            if (not isinstance(raw, Sequence)
                    or isinstance(raw, (str, bytes, bytearray))
                    or len(raw) != 2):
                raise ProvenanceRefused(
                    'stage4.design unit 必须是 (purpose, chapter) 二元组')
            purpose, chapter = self._unit(str(raw[0]), raw[1])
            if purpose in seen:
                raise ProvenanceRefused(
                    f'stage4.design 本批 unit={purpose} 重复')
            seen.add(purpose)
            normalized.append((purpose, chapter))

        created: list[str] = []
        primary: BaseException | None = None
        cleanup_errors: list[BaseException] = []
        with self._lock:
            if self._closed:
                raise ProvenanceRefused('stage4.design pool 已关闭')
            new_chapters = {
                chapter for purpose, chapter in normalized
                if purpose not in self._known_units and chapter is not None
            }
            if new_chapters and any(
                    self._known_units[purpose] is not None
                    for purpose in self._wired_purposes):
                raise ProvenanceRefused(
                    'stage4.design 逐章 wire 启动后不能再扩充本批 unit；'
                    '必须在首个章节 model start 前一次性预建全部缺失章')
            for purpose, chapter in normalized:
                existing = self._known_units.get(purpose, chapter)
                if purpose in self._known_units and existing != chapter:
                    raise ProvenanceRefused(
                        f'{purpose}: 已绑定 chapter={existing!r}，不能改绑 {chapter!r}')
                self._known_units[purpose] = chapter
            self._rebuild_progress()
            try:
                for purpose, chapter in normalized:
                    if purpose in self._restored or purpose in self._clients:
                        self._prepared_units.add(purpose)
                        continue
                    client = self._new_client(purpose, chapter)
                    self._clients[purpose] = client
                    self._prepared_units.add(purpose)
                    created.append(purpose)
            except BaseException as exc:
                primary = exc
                # 只撤销本批刚造出的 client；上一批已 accepted 的 strategy
                # 仍由 finish_all 以真实失败原因收口。
                for purpose in reversed(created):
                    client = self._clients.pop(purpose, None)
                    self._prepared_units.discard(purpose)
                    if client is None:
                        continue
                    self._driver_owners.pop(id(client.driver), None)
                    try:
                        client.close()
                    except BaseException as cleanup:
                        cleanup_errors.append(cleanup)
        if primary is not None:
            if cleanup_errors:
                raise BaseExceptionGroup(
                    'stage4.design 预建 session 与回收同时失败',
                    [primary, *cleanup_errors])
            raise primary

    def _client_for(self, purpose: str) -> tuple[SessionAgentClient, int | None]:
        with self._lock:
            if purpose not in self._prepared_units:
                raise ProvenanceRefused(
                    f'{purpose}: 必须先随本批全部缺失 unit 预建，才能启动 wire')
            if purpose in self._restored:
                raise ProvenanceRefused(
                    f'{purpose}: 已从 durable checkpoint 恢复，不能重复付费')
            client = self._clients.get(purpose)
            if client is None:
                raise ProvenanceRefused(f'{purpose}: 缺预建的私有 client')
            return client, self._known_units[purpose]

    def _register_provider_owner(
        self, purpose: str, client: SessionAgentClient,
    ) -> None:
        provider_session_id = client.observed_provider_session_id
        if not isinstance(provider_session_id, str) or not provider_session_id:
            return
        key = (self.profile.driver, provider_session_id)
        owner = (purpose, client.session_run_id)
        with self._lock:
            prior = self._provider_session_owners.get(key)
            if prior is not None and prior != owner:
                self._isolation_failed_units.update((prior[0], purpose))
                raise ProvenanceRefused(
                    f'stage4.design provider session {provider_session_id!r} '
                    f'被 {prior[0]}/{purpose} 共享；拒绝伪造 unit 隔离')
            self._provider_session_owners[key] = owner

    def ask_json(self, system: str, user: str, **kwargs: Any) -> Any:
        model_cls = kwargs.pop('model_cls', None)
        if (not isinstance(model_cls, type)
                or not issubclass(model_cls, BaseModel)):
            raise ProvenanceRefused(
                'stage4.design 请求缺显式 Pydantic model_cls')
        purpose = str(kwargs.get('purpose') or '')
        if kwargs.get('recovery_policy') != \
                stage4_runs.DESIGN_UNIT_RECOVERY_POLICY:
            raise ProvenanceRefused(
                f'{purpose}: Stage4A recovery policy 漂移：'
                f'{kwargs.get("recovery_policy")!r}')
        client, chapter = self._client_for(purpose)
        self._unit(purpose, chapter)
        with self._lock:
            subject = self._subjects.get(purpose)
            if subject is not None and subject != (system, user):
                raise ProvenanceRefused(
                    f'{purpose}: 同一 unit 的原始 system/user 漂移')
            self._subjects[purpose] = (system, user)
            supplied_progress = kwargs.pop('progress', None)
            if chapter is None:
                if supplied_progress is not None:
                    raise ProvenanceRefused(
                        'stage4.design strategy 请求不能伪造章节 progress')
            else:
                expected_progress = self._progress.get(purpose)
                if expected_progress is None:
                    raise ProvenanceRefused(
                        f'{purpose}: 缺整批章节 progress 身份')
                if (supplied_progress is not None
                        and supplied_progress != expected_progress):
                    raise ProvenanceRefused(
                        f'{purpose}: progress 与整批章序漂移')
                kwargs['progress'] = expected_progress
            self._wired_purposes.add(purpose)

        primary: BaseException | None = None
        result: Any = None
        try:
            result = client.ask_json(
                system, user, model_cls=model_cls, **kwargs)
        except BaseException as exc:
            primary = exc
        isolation: BaseException | None = None
        try:
            self._register_provider_owner(purpose, client)
        except BaseException as exc:
            isolation = exc
        if primary is not None and isolation is not None:
            raise BaseExceptionGroup(
                f'{purpose}: wire 与 provider 隔离检查同时失败',
                [primary, isolation])
        if isolation is not None:
            raise isolation
        if primary is not None:
            raise primary
        return result

    @staticmethod
    def _identity_sha256(value: Mapping[str, Any]) -> str:
        return hashlib.sha256(json.dumps(
            dict(value), ensure_ascii=False, sort_keys=True,
            separators=(',', ':'), allow_nan=False,
        ).encode('utf-8')).hexdigest()

    def accepted_checkpoint(self, purpose: str) -> dict[str, Any]:
        with self._lock:
            chapter = self._known_units.get(purpose, object())
            restored = self._restored.get(purpose)
            client = self._clients.get(purpose)
        if chapter is not None and not isinstance(chapter, int):
            raise ProvenanceRefused(f'{purpose}: 未登记 Stage4A unit')
        self._unit(purpose, chapter)
        if restored is not None:
            return copy.deepcopy(restored)
        if client is None:
            raise ProvenanceRefused(f'{purpose}: 尚无 live/restored acceptance')
        return client.accepted_checkpoint(purpose)

    def restore_checkpoint_acceptance(
        self, purpose: str, record: Mapping[str, Any], *,
        system: str | None = None, user: str | None = None,
        max_tokens: int | None = None, recovery_policy: str | None = None,
        **unsupported: object,
    ) -> None:
        """恢复 N+1 unit 的 acceptance/adoption，不复活历史 provider 会话。"""
        if unsupported:
            raise ProvenanceRefused(
                f'{purpose}: checkpoint 恢复含未实现参数 {sorted(unsupported)}')
        if system is None or user is None or max_tokens is None:
            raise ProvenanceRefused(
                f'{purpose}: checkpoint 恢复缺 system/user/requested ceiling')
        if recovery_policy != stage4_runs.DESIGN_UNIT_RECOVERY_POLICY:
            raise ProvenanceRefused(
                f'{purpose}: checkpoint recovery policy 漂移')
        profile = record.get('profile')
        binding = record.get('accepted_request')
        if (record.get('acceptance_protocol') != SESSION_PROTOCOL_VERSION
                or profile != self.profile.model_dump(mode='json')
                or not isinstance(binding, dict)):
            raise ProvenanceRefused(
                f'{purpose}: checkpoint protocol/profile/request 漂移')
        chapter = binding.get('chapter')
        purpose, chapter = self._unit(purpose, chapter)
        schema_sha = binding.get('schema_sha256')
        if (not isinstance(schema_sha, str) or len(schema_sha) != 64
                or any(char not in '0123456789abcdef' for char in schema_sha)):
            raise ProvenanceRefused(
                f'{purpose}: checkpoint 缺合法 response schema SHA-256')
        expected = {
            'node': self.ctx.node,
            'chapter': chapter,
            'purpose': purpose,
            'system_sha256': hashlib.sha256(system.encode('utf-8')).hexdigest(),
            'user_sha256': hashlib.sha256(user.encode('utf-8')).hexdigest(),
            'schema_sha256': schema_sha,
            'requested_model': self.profile.model,
            'requested_effort': self.profile.reasoning_effort,
            'requested_output_token_ceiling': max_tokens,
            'protocol_version': SESSION_PROTOCOL_VERSION,
        }
        if binding != expected:
            raise ProvenanceRefused(
                f'{purpose}: checkpoint accepted_request 与当前 unit 请求漂移')
        if record.get('subject_sha256') != self._identity_sha256(expected):
            raise ProvenanceRefused(
                f'{purpose}: checkpoint logical subject SHA-256 不闭合')
        provider_session_id = record.get('provider_session_id')
        logical_session_id = record.get('session_run_id')
        if (not isinstance(provider_session_id, str) or not provider_session_id
                or not isinstance(logical_session_id, str)
                or not logical_session_id):
            raise ProvenanceRefused(
                f'{purpose}: checkpoint 缺 provider/logical session 身份')
        turn_index = record.get('accepted_turn_index')
        links = record.get('attempt_links')
        if (not isinstance(turn_index, int) or isinstance(turn_index, bool)
                or turn_index < 1 or turn_index > self.max_turns
                or not isinstance(links, list) or len(links) != turn_index):
            raise ProvenanceRefused(
                f'{purpose}: checkpoint turn/attempt links 与当前预算不闭合')
        linked: dict[str, dict[str, Any]] = {}
        for link in links:
            if not isinstance(link, dict):
                raise ProvenanceRefused(
                    f'{purpose}: checkpoint attempt link 不是对象')
            attempt = link.get('attempt_id')
            if not isinstance(attempt, str) or not attempt or attempt in linked:
                raise ProvenanceRefused(
                    f'{purpose}: checkpoint attempt links 不唯一')
            identity = self.ctx.ledger.request_identity_for_attempt(attempt)
            if dict(link) != identity:
                raise ProvenanceRefused(
                    f'{purpose}: checkpoint attempt={attempt} 与成本账漂移')
            linked[attempt] = identity
        attempt_id = record.get('attempt_id')
        identity = linked.get(str(attempt_id))
        if (identity is None
                or record.get('request_fingerprint')
                != identity.get('request_fingerprint')
                or record.get('model') != identity.get('model')
                or record.get('ledger_response_sha256')
                != identity.get('response_sha256')
                or identity.get('model') != self.profile.model
                or identity.get('model') in (None, '', 'unknown')):
            raise ProvenanceRefused(
                f'{purpose}: checkpoint accepted attempt 与成本账/profile 漂移')

        key = (self.profile.driver, provider_session_id)
        owner = (purpose, logical_session_id)
        normalized = copy.deepcopy(dict(record))
        with self._lock:
            if self._closed:
                raise ProvenanceRefused('stage4.design pool 已关闭')
            if purpose in self._clients:
                raise ProvenanceRefused(
                    f'{purpose}: 已预建 live client，不能再恢复 checkpoint')
            previous = self._restored.get(purpose)
            if previous is not None and previous != normalized:
                raise ProvenanceRefused(
                    f'{purpose}: 被恢复为两份不同 acceptance')
            prior = self._provider_session_owners.get(key)
            if prior is not None and prior != owner:
                self._isolation_failed_units.update((prior[0], purpose))
                # 两条历史 acceptance 都是真实发生过的付费事实。即便共享
                # provider id 使本次恢复关闭式失败，也要保留第二条记录，
                # 让 finish_all 能把两条开放 logical session 都准确收成
                # session_isolation，而不是只审计先到的一条。
                self._known_units[purpose] = chapter
                self._prepared_units.add(purpose)
                self._restored[purpose] = normalized
                self._subjects[purpose] = (system, user)
                self._rebuild_progress()
                raise ProvenanceRefused(
                    f'stage4.design provider session {provider_session_id!r} '
                    f'被 {prior[0]}/{purpose} 的 checkpoint 共享')
            existing_chapter = self._known_units.get(purpose, chapter)
            if purpose in self._known_units and existing_chapter != chapter:
                raise ProvenanceRefused(
                    f'{purpose}: checkpoint chapter 与既有 unit 漂移')
            self._provider_session_owners[key] = owner
            self._known_units[purpose] = chapter
            self._prepared_units.add(purpose)
            self._restored[purpose] = normalized
            self._subjects[purpose] = (system, user)
            self._rebuild_progress()

    def accepted_attempt(self, purpose: str) -> str | None:
        with self._lock:
            chapter = self._known_units.get(purpose, object())
            restored = self._restored.get(purpose)
            client = self._clients.get(purpose)
        if chapter is not None and not isinstance(chapter, int):
            raise ProvenanceRefused(f'{purpose}: 未登记 Stage4A unit')
        self._unit(purpose, chapter)
        if restored is not None:
            attempt = restored.get('attempt_id')
            return attempt if isinstance(attempt, str) and attempt else None
        return None if client is None else client.accepted_attempt(purpose)

    def accepted_models(self, *purposes: str) -> tuple[str, ...]:
        with self._lock:
            selected = tuple(purposes) or tuple(
                purpose for purpose, _chapter in sorted(
                    self._known_units.items(), key=self._unit_sort))
        models: set[str] = set()
        for purpose in selected:
            attempt_id = self.accepted_attempt(purpose)
            if attempt_id is None:
                continue
            model = self.ctx.ledger.model_for_attempt(attempt_id)
            if isinstance(model, str) and model:
                models.add(model)
        return tuple(sorted(models))

    @staticmethod
    def _failure_contains(
        failure: BaseException | None, cls: type[BaseException],
    ) -> bool:
        if failure is None:
            return False
        if isinstance(failure, cls):
            return True
        if isinstance(failure, BaseExceptionGroup):
            return any(
                Stage4DesignSessionPool._failure_contains(child, cls)
                for child in failure.exceptions)
        return False

    def finish_all(
        self, *, delivered: bool, failure: BaseException | None = None,
    ) -> None:
        """整书终态后一次收口所有 unit；失败时保留已过章 checkpoint。"""
        with self._lock:
            units = tuple(sorted(
                self._known_units.items(), key=self._unit_sort))
            clients = dict(self._clients)
            restored = copy.deepcopy(self._restored)
            isolation_failed = set(self._isolation_failed_units)
            driver_setup_failed = set(self._driver_setup_failed_units)
            accepted = {
                purpose for purpose, client in clients.items()
                if client.accepted_attempt(purpose) is not None
            } | set(restored)
        errors: list[BaseException] = []
        if delivered and set(purpose for purpose, _chapter in units) != accepted:
            errors.append(ProvenanceRefused(
                'stage4.design 正式交付时 N+1 unit acceptance 未闭合'))
        for purpose, _chapter in units:
            client = clients.get(purpose)
            record = restored.get(purpose)
            try:
                if delivered and purpose in accepted:
                    state = ('ok', 'delivered', None)
                elif (purpose in isolation_failed
                      or (client is not None
                          and client.provider_session_reused)):
                    state = ('failed', 'unknown', 'session_isolation')
                elif client is not None and client.driver_failed:
                    state = ('failed', 'unknown', 'agent_driver')
                elif purpose in driver_setup_failed:
                    state = ('failed', 'unknown', 'agent_driver')
                elif purpose in accepted:
                    # 兄弟 unit/聚合任一失败时，已经付费并通过本 unit 闸的
                    # 候选仍是可恢复进度，不得写成 failed 或 delivered。
                    state = ('ok', 'blocked', 'peer_result_missing')
                elif self._failure_contains(failure, LlmFailed):
                    state = ('ok', 'failed', 'response_validation')
                elif self._failure_contains(failure, GateBlocked):
                    state = ('ok', 'blocked', 'artifact_validation')
                elif self._failure_contains(failure, ProvenanceRefused):
                    state = ('ok', 'unknown', 'artifact_verification')
                else:
                    state = ('ok', 'unknown', 'node_executor')
                process_state, delivery_state, error_kind = state
                if client is not None:
                    client.finish(
                        process_state=process_state,
                        delivery_state=delivery_state,
                        error_kind=error_kind,
                    )
                elif record is not None:
                    agent_session_audit.finish_restored_session(
                        self.ctx.novel_dir.parent,
                        record,
                        process_state=process_state,
                        delivery_state=delivery_state,
                        error_kind=error_kind,
                        end_code_version=prov.code_version(),
                        only_if_open=True,
                    )
            except BaseException as exc:
                errors.append(exc)
        if errors:
            raise BaseExceptionGroup(
                'stage4.design 逐 unit ended 审计写入失败', errors)

    def close(self) -> None:
        with self._lock:
            if self._closed:
                return
            clients = tuple(self._clients.values())
            self._closed = True
        errors: list[BaseException] = []
        for client in clients:
            try:
                client.close()
            except BaseException as exc:
                errors.append(exc)
        if errors:
            raise BaseExceptionGroup(
                'stage4.design 逐 unit driver/cwd 关闭失败', errors)


class Stage4DesignAgent(NodeExecutorAgent[BlindCandidate]):
    """Stage4A 多 session 执行器；代码聚合与正式提交仍由 producer 独占。"""

    node = 'stage4.design'

    def __init__(
        self,
        ctx: RunContext,
        *,
        driver_factory: Stage4DesignDriverFactory | None = None,
        max_turns: int = 4,
        concurrency: int = STAGE4_DESIGN_MAX_CONCURRENCY,
        repair_decision_sha256: str | None = None,
        repair_scope: plan.DesignRepairScope | None = None,
    ) -> None:
        if ctx.node != self.node:
            raise ValueError(f'{type(self).__name__} 只接受 {self.node}，实得 {ctx.node}')
        if ctx.executor_profile is None:
            raise ValueError(f'{self.node} executor 缺 resolved profile')
        repair_mode = (
            repair_scope is not None and not repair_scope.strategy_replan)
        self.ctx = ctx
        self.repair_decision_sha256 = repair_decision_sha256
        self.repair_scope = repair_scope
        self.pool = Stage4DesignSessionPool(
            ctx,
            driver_factory=driver_factory,
            # targeted repair 的 content budget 是产品契约，不受通用默认值放大。
            max_turns=2 if repair_mode else max_turns,
            concurrency=concurrency,
        )

    @staticmethod
    def _raise_group(
        primary: BaseException | None, extras: Sequence[BaseException],
    ) -> None:
        if primary is None and not extras:
            return
        if primary is not None and not extras:
            raise primary
        raise BaseExceptionGroup(
            'stage4.design 执行/会话收口发生多项错误',
            ([primary] if primary is not None else []) + list(extras),
        )

    def run(self) -> NodeAgentResult[BlindCandidate]:
        result: NodeAgentResult[BlindCandidate] | None = None
        primary: BaseException | None = None
        delivered = False
        try:
            artifact = plan.run_design(
                self.ctx,
                self.pool,
                repair_decision_sha256=self.repair_decision_sha256,
                repair_scope=self.repair_scope,
            )
            if not isinstance(artifact, BlindCandidate):
                raise ProvenanceRefused(
                    'stage4.design executor 返回了非 BlindCandidate 正式产物')
            delivered = True
            result = NodeAgentResult(
                artifact=artifact,
                turns=self.pool.turns_used,
                passed=True,
            )
        except BaseException as exc:
            primary = exc

        extras: list[BaseException] = []
        try:
            self.pool.finish_all(delivered=delivered, failure=primary)
        except BaseException as exc:
            extras.append(exc)
        try:
            self.pool.close()
        except BaseException as exc:
            extras.append(exc)
        if primary is not None or extras:
            self._raise_group(primary, extras)
        assert result is not None
        return result


class Stage4PlanAgent(NodeExecutorAgent[BaseModel]):
    """Stage4B 证据绑定/计划编译：typed infeasible 是 landed-red，不洗绿。

    批次 4 起 agent 主路是**章级池**（`Stage4PlanSessionPool`）：机械章 0
    wire、歧义章私有 session；`run_plan` 按 ``prepare_chapters`` 识别池并走
    章级拓扑。legacy 整书单发 client 仅在显式传入时保留。
    """

    node = 'stage4.plan'

    def __init__(self, ctx: RunContext, *, driver: AgentDriver | None = None,
                 max_turns: int = 4) -> None:
        if ctx.node != self.node:
            raise ValueError(
                f'{type(self).__name__} 只接受 {self.node}，实得 {ctx.node}')
        if ctx.executor_profile is None:
            raise ValueError('stage4.plan executor 缺 resolved profile')
        self.ctx = ctx
        driver_factory: Stage4DesignDriverFactory | None = None
        if driver is not None:
            # 显式传入的单 driver 只能服务一个章 session：任何两章共享同一
            # provider session 都是隔离破口，第二取用关闭式拒绝。
            state = {'used': False}

            def _single_use_factory(_purpose: str, _chapter: int | None):
                if state['used']:
                    raise ProvenanceRefused(
                        'stage4.plan 章级池不接受跨章共享同一 driver/session')
                state['used'] = True
                return driver

            driver_factory = _single_use_factory
        self.client = Stage4PlanSessionPool(
            ctx, driver_factory=driver_factory, max_turns=max_turns)

    def run(self) -> NodeAgentResult[BaseModel]:
        try:
            artifact = plan.run_plan(self.ctx, self.client)
            if isinstance(artifact, EvidenceBinding):
                if artifact.status != 'infeasible':
                    raise ProvenanceRefused(
                        'stage4.plan executor 返回了未编译的 bound EvidenceBinding')
                passed = False
            elif isinstance(artifact, CheckReport):
                passed = artifact.passed
            else:
                raise ProvenanceRefused(
                    'stage4.plan executor 返回了未知终态载体 '
                    f'{type(artifact).__name__}')
            self.client.finish(
                process_state='ok', delivery_state='delivered')
            return NodeAgentResult(
                artifact=artifact, turns=self.client.turns_used, passed=passed,
                report=(artifact if isinstance(artifact, CheckReport) else None))
        except Exception as exc:
            self._finish_exception(exc)
            raise
        finally:
            self.client.close()



class Stage4PlanSessionPool:
    """Stage4B 歧义章的私有 session 池（TODO-45 批次 4）。

    机械基线把唯一可解的绑定由代码确定；只有带 ambiguity 的章才需要模型。
    每章一条 single-session run/checkpoint 链（`stage4_runs.plan_chapter_run_spec`），
    最多 `STAGE4_PLAN_MAX_CONCURRENCY` 章；任何两章不共享 driver、logical
    session 或 provider session。机械章（零歧义）由代码确定性重算，**不进入
    本池**——0 wire、0 checkpoint（checkpoint 的价值是免重付模型调用，
    确定性重算本身零成本）。
    """

    def __init__(
        self,
        ctx: RunContext,
        *,
        driver_factory: Stage4DesignDriverFactory | None = None,
        max_turns: int = 4,
        concurrency: int = STAGE4_PLAN_MAX_CONCURRENCY,
    ) -> None:
        if ctx.node != 'stage4.plan':
            raise ValueError(
                f'Stage4PlanSessionPool 只接受 stage4.plan，实得 {ctx.node!r}')
        if ctx.executor_profile is None:
            raise ValueError('stage4.plan executor 缺 resolved profile')
        if (not isinstance(max_turns, int) or isinstance(max_turns, bool)
                or not 1 <= max_turns <= 4):
            raise ValueError('Stage4PlanSessionPool.max_turns 必须在 1..4')
        if (not isinstance(concurrency, int) or isinstance(concurrency, bool)
                or concurrency < 1):
            raise ValueError('Stage4PlanSessionPool.concurrency 必须 >= 1')
        self.ctx = ctx
        self.profile: AgentProfile = ctx.executor_profile
        self.model = self.profile.model
        self.max_turns = max_turns
        self.concurrency = min(STAGE4_PLAN_MAX_CONCURRENCY, concurrency)
        self._driver_factory = driver_factory or (
            lambda _purpose, _chapter: create_driver(
                self.profile, role='executor'))
        self._clients: dict[int, SessionAgentClient] = {}
        # factory 返回的是 provider session 的所有者，不是可跨章复用的无状态
        # transport。用对象身份守住最早的共享破口；provider_session_id 的跨对象
        # 碰撞仍由 agent_session_audit 在首轮真实响应处关闭式拒绝。
        self._driver_owners: dict[int, int] = {}
        self._probe: AgentProbe | None = None
        self._progress: dict[int, RequestProgress] = {}
        self._closed = False
        self._lock = threading.RLock()

    def _client(self, chapter: int) -> SessionAgentClient:
        with self._lock:
            client = self._clients.get(chapter)
            if client is not None:
                return client
            driver = self._driver_factory(
                stage4_runs.plan_chapter_purpose(chapter), chapter)
            owner = self._driver_owners.get(id(driver))
            if owner is not None and owner != chapter:
                raise ProvenanceRefused(
                    'stage4.plan 章级池不接受跨章共享同一 driver/session：'
                    f'ch{owner} 与 ch{chapter}')
            self._driver_owners[id(driver)] = chapter
            if self._probe is None:
                probe = driver.probe()
                if not probe.available:
                    raise ProvenanceRefused(
                        f'{stage4_runs.plan_chapter_purpose(chapter)}: '
                        'executor driver 本地探针不可用：'
                        f'{probe.detail or "unknown"}')
                if (driver.driver != self.profile.driver
                        or probe.driver != self.profile.driver
                        or probe.provider != driver.provider):
                    raise ProvenanceRefused(
                        'stage4.plan 章 session driver/probe/profile 身份不闭合')
                self._probe = probe
            elif (driver.driver != self._probe.driver
                  or driver.provider != self._probe.provider):
                raise ProvenanceRefused(
                    'stage4.plan 章 session 在同一 pool 内漂移厂商身份')
            client = SessionAgentClient(
                self.ctx, self.profile, driver, chapter=chapter,
                parent_run_id=self.ctx.run_id, max_turns=self.max_turns,
                preflight_probe=self._probe)
            self._clients[chapter] = client
            return client

    def prepare_chapters(self, chapters: Sequence[int]) -> None:
        """为本批歧义章预建私有 client 与 RequestProgress。

        client 必须在任何 wire 之前全部建好：这样 factory 错把一个有状态 driver
        发给两章时，会在花钱前失败，而不是并发跑到一半才发现 session 串线。
        """
        raw = [int(chapter) for chapter in chapters]
        normalized = sorted(set(raw))
        if len(normalized) != len(raw):
            raise ProvenanceRefused('stage4.plan 章 session 章号重复')
        if not normalized:
            raise ProvenanceRefused('stage4.plan 章批次不能为空')
        total = len(normalized)
        self._progress = {
            chapter: RequestProgress(chapter=chapter, index=index, total=total)
            for index, chapter in enumerate(normalized, 1)
        }
        for chapter in normalized:
            self._client(chapter)

    def restore_chapter(
            self, chapter: int, request: Any, *,
            validator: Callable[[Any, Any], str | None] | None = None,
    ) -> Any | None:
        """恢复该章 latest accepted checkpoint；缺/漂移返回 None。"""
        restored = stage4_runs.restore_plan_chapter_checkpoint(
            self.ctx, request, validator=validator)
        if restored is None:
            return None
        client = self._client(chapter)
        client.restore_checkpoint_acceptance(
            stage4_runs.plan_chapter_purpose(chapter),
            restored.acceptance,
            system=request.system, user=request.user,
            max_tokens=request.max_tokens,
            recovery_policy=stage4_runs.plan_chapter_run_spec(
                chapter).recovery_policy,
        )
        return restored

    def ask_chapter(
            self, chapter: int, request: Any, *,
            validator: Callable[[Any, Any], str | None] | None = None,
    ) -> ChapterBindingResponse:
        """一章的 wire：ask → 返回 typed 解析；checkpoint 由调用方在业务
        闭合校验通过后写（`write_chapter_checkpoint`）。"""
        progress = self._progress.get(chapter)
        if progress is None:
            raise ProvenanceRefused(
                f'{stage4_runs.plan_chapter_purpose(chapter)}: '
                '章 session 必须先经 prepare_chapters 建立批次进度')
        client = self._client(chapter)
        return client.ask_json(
            request.system, request.user,
            model_cls=ChapterBindingResponse,
            purpose=stage4_runs.plan_chapter_purpose(chapter),
            max_tokens=request.max_tokens,
            validator=validator,
            retain_validator_rejection=True,
            progress=progress,
            recovery_policy=stage4_runs.plan_chapter_run_spec(
                chapter).recovery_policy,
        )

    def write_chapter_checkpoint(
            self, chapter: int, request: Any, *,
            response_payload: Mapping[str, Any],
            state_payload: Mapping[str, Any],
            revision_key: str = 'terminal',
    ) -> None:
        client = self._client(chapter)
        stage4_runs.write_plan_chapter_checkpoint(
            self.ctx, request,
            response_payload=response_payload,
            acceptance=client.accepted_checkpoint(
                stage4_runs.plan_chapter_purpose(chapter)),
            state_payload=state_payload, revision_key=revision_key)

    def queue_chapter_feedback(self, chapter: int, rows: Sequence[Mapping[str, object]]) -> bool:
        """业务 HARD 反馈回**该章自己的** session；其他章不受影响。"""
        return self._client(chapter).queue_code_validation_feedback(
            rows, purpose=stage4_runs.plan_chapter_purpose(chapter))

    def accepted_chapter(self, chapter: int) -> tuple[str, tuple[str, ...]]:
        client = self._client(chapter)
        purpose = stage4_runs.plan_chapter_purpose(chapter)
        return client.accepted_attempt(purpose), tuple(
            sorted(set(client.accepted_models(purpose))))

    def chapter_acceptance(self, chapter: int) -> dict[str, Any]:
        """章级 accepted stdout 的完整不可变身份，供 hash-bound 恢复留证。"""
        return self._client(chapter).accepted_checkpoint(
            stage4_runs.plan_chapter_purpose(chapter))

    def chapter_rejection(self, chapter: int) -> dict[str, Any] | None:
        """只暴露最后一轮 schema-valid dynamic-validator 拒稿。"""
        return self._client(chapter).last_json_rejection(
            stage4_runs.plan_chapter_purpose(chapter))

    @property
    def provider_session_id(self) -> str | None:
        """只投影“本进程是否有 live session”；聚合从不冒充单一 session。"""
        with self._lock:
            live = sorted(
                str(client.provider_session_id)
                for client in self._clients.values()
                if client.provider_session_id is not None)
        return None if not live else '|'.join(live)

    @property
    def turns_used(self) -> int:
        with self._lock:
            return sum(client.turns_used for client in self._clients.values())

    @property
    def driver_failed(self) -> bool:
        with self._lock:
            return any(client.driver_failed
                       for client in self._clients.values())

    def finish(self, *, process_state: str, delivery_state: str,
               error_kind: str | None = None) -> None:
        """与 SessionAgentClient.finish 同形；executor 收口统一走这里。"""
        self.finish_all(process_state=process_state,
                        delivery_state=delivery_state,
                        error_kind=error_kind)

    def finish_all(self, *, process_state: str, delivery_state: str,
                   error_kind: str | None = None) -> None:
        with self._lock:
            clients = list(self._clients.values())
        for client in clients:
            client.finish(process_state=process_state,
                          delivery_state=delivery_state,
                          error_kind=error_kind)

    def close(self) -> None:
        with self._lock:
            clients = list(self._clients.values())
            self._closed = True
        for client in clients:
            client.close()



Stage5DriverFactory = Callable[[int], AgentDriver]


class Stage5SessionPool:
    """``stage5.write`` 的逐章 session 路由、恢复与 ended 审计收口。

    每章只绑定一个原始 subject；live 返工复用本章 provider session，crash 后的
    坏 checkpoint 则恢复累计 turn/attempt，再用一条新的 logical/provider session
    继续。pool 不解释 checkpoint 字节，完整 acceptance 校验仍只在
    ``stage5_runs -> agent_runs`` 与 ``SessionAgentClient`` 两个通用落点完成。
    """

    _OUTCOMES = frozenset({
        'delivered', 'blocked', 'response_failed',
        'artifact_failed', 'executor_failed',
    })

    def __init__(
        self,
        ctx: RunContext,
        *,
        driver_factory: Stage5DriverFactory | None = None,
        max_turns: int = 4,
        concurrency: int = MAX_CONCURRENCY,
    ) -> None:
        if ctx.node != 'stage5.write':
            raise ValueError(
                f'Stage5SessionPool 只接受 stage5.write，实得 {ctx.node!r}')
        if ctx.executor_profile is None:
            raise ValueError('stage5.write executor 缺 resolved profile')
        if (not isinstance(max_turns, int) or isinstance(max_turns, bool)
                or max_turns < 1):
            raise ValueError('Stage5SessionPool.max_turns 必须 >= 1')
        if (not isinstance(concurrency, int) or isinstance(concurrency, bool)
                or concurrency < 1):
            raise ValueError('Stage5SessionPool.concurrency 必须 >= 1')
        self.ctx = ctx
        self.profile: AgentProfile = ctx.executor_profile
        self.model = self.profile.model
        self.max_turns = max_turns
        self.concurrency = min(MAX_CONCURRENCY, concurrency)
        self._driver_factory = driver_factory or (
            lambda _chapter: create_driver(self.profile, role='executor'))
        self._clients: dict[int, SessionAgentClient] = {}
        self._subjects: dict[int, tuple[str, str, int]] = {}
        self._restored: dict[int, dict[str, Any]] = {}
        self._prepared_chapters: tuple[int, ...] | None = None
        self._checkpoint_chapters: set[int] = set()
        self._probe: AgentProbe | None = None
        self._driver_owners: dict[int, int] = {}
        # value 同时带 chapter 与 logical session；同章 crash 恢复也不能偷偷
        # 重新接回旧 provider id。
        self._provider_session_owners: dict[
            tuple[str, str], tuple[int, str],
        ] = {}
        self._failed_chapters: set[int] = set()
        self._terminal: dict[int, tuple[str, str, str | None]] = {}
        self._closed = False
        self._lock = threading.RLock()

    @staticmethod
    def _chapter(chapter: object) -> int:
        if (not isinstance(chapter, int) or isinstance(chapter, bool)
                or chapter < 1):
            raise ProvenanceRefused(
                f'stage5.write session 章号必须是正整数，实得 {chapter!r}')
        return chapter

    @classmethod
    def _chapter_of_purpose(cls, purpose: str) -> int:
        prefix = 'stage5.write·ch'
        if not isinstance(purpose, str) or not purpose.startswith(prefix):
            raise ProvenanceRefused(
                f'stage5.write 逐章 purpose 非法：{purpose!r}')
        raw = purpose.removeprefix(prefix)
        if not raw.isdigit():
            raise ProvenanceRefused(
                f'stage5.write 逐章 purpose 缺合法章号：{purpose!r}')
        chapter = cls._chapter(int(raw))
        if purpose != stage5_runs.purpose_of(chapter):
            raise ProvenanceRefused(
                f'stage5.write 逐章 purpose 不是 canonical 形状：{purpose!r}')
        return chapter

    @property
    def turns_used(self) -> int:
        """返回本 run 各章 logical request 的累计 turns；恢复轮次不重置。"""
        with self._lock:
            chapters = set(self._restored) | set(self._clients)
            total = 0
            for chapter in chapters:
                client = self._clients.get(chapter)
                if client is not None:
                    total += client.turns_used
                    continue
                turn = self._restored[chapter].get('accepted_turn_index')
                if isinstance(turn, int) and not isinstance(turn, bool):
                    total += turn
            return total

    @property
    def driver_failed(self) -> bool:
        with self._lock:
            return any(client.driver_failed for client in self._clients.values())

    @property
    def isolation_failed(self) -> bool:
        with self._lock:
            return bool(self._failed_chapters)

    @property
    def session_count(self) -> int:
        with self._lock:
            return len(self._clients)

    def _new_client(
        self, chapter: int, *, probe_driver: bool = True,
    ) -> SessionAgentClient:
        """构造一章独占的 client；调用方必须持 ``_lock``。"""
        driver = self._driver_factory(chapter)
        driver_id = id(driver)
        prior = self._driver_owners.get(driver_id)
        if prior is not None and prior != chapter:
            self._failed_chapters.update((prior, chapter))
            raise ProvenanceRefused(
                f'stage5.write driver factory 把同一对象同时给了第 '
                f'{prior}/{chapter} 章；拒绝跨章伪隔离')
        self._driver_owners[driver_id] = chapter
        try:
            if not probe_driver:
                if driver.driver != self.profile.driver:
                    self._failed_chapters.add(chapter)
                    raise ProvenanceRefused(
                        f'Stage5 第 {chapter} 章恢复用 driver 与 profile 漂移')
            elif self._probe is None:
                probe = driver.probe()
                if (driver.driver != self.profile.driver
                        or probe.driver != self.profile.driver
                        or probe.provider != driver.provider
                        or not probe.available):
                    self._failed_chapters.add(chapter)
                    raise ProvenanceRefused(
                        f'Stage5 第 {chapter} 章 driver/probe/profile 身份或'
                        f'可用性不闭合：profile={self.profile.driver!r}, '
                        f'driver={driver.driver!r}, '
                        f'probe={probe.driver!r}/{probe.provider!r}/'
                        f'{probe.available!r}')
                self._probe = probe
            elif (driver.driver != self._probe.driver
                  or driver.provider != self._probe.provider):
                self._failed_chapters.add(chapter)
                raise ProvenanceRefused(
                    f'Stage5 第 {chapter} 章 driver factory 在同批内漂移厂商身份')
            return SessionAgentClient(
                self.ctx,
                self.profile,
                driver,
                chapter=chapter,
                parent_run_id=self.ctx.run_id,
                max_turns=self.max_turns,
                preflight_probe=self._probe,
            )
        except Exception:
            self._driver_owners.pop(driver_id, None)
            driver.close()
            raise

    @staticmethod
    def _checkpoint_may_exist(ctx: RunContext, chapter: int) -> bool:
        """陌生/坏 JSON 也视作待恢复，防恢复失败后静默重新付费。"""
        directory = paths.stage5_chapter_checkpoint_dir(
            ctx.novel_dir, ctx.run_id, chapter)
        return directory.is_dir() and any(directory.glob('*.json'))

    def prepare_initial_sessions(self, chapters: Sequence[int]) -> None:
        """在任意 wire 前预建所有无 checkpoint 章节并只做一次 CLI probe。"""
        normalized = tuple(self._chapter(chapter) for chapter in chapters)
        if len(set(normalized)) != len(normalized):
            raise ProvenanceRefused(
                f'stage5.write 本批章号重复：{normalized!r}')
        with self._lock:
            if self._closed:
                raise ProvenanceRefused('stage5.write session pool 已关闭')
            if self._prepared_chapters is not None:
                if self._prepared_chapters != normalized:
                    raise ProvenanceRefused(
                        'stage5.write 已冻结本批章集，不能在首个 wire 后改写')
                return
            self._prepared_chapters = normalized
            try:
                for chapter in normalized:
                    if self._checkpoint_may_exist(self.ctx, chapter):
                        self._checkpoint_chapters.add(chapter)
                        continue
                    self._clients[chapter] = self._new_client(chapter)
            except Exception:
                clients = tuple(self._clients.values())
                self._clients.clear()
                self._prepared_chapters = None
                self._checkpoint_chapters.clear()
                for client in clients:
                    try:
                        client.close()
                    except Exception:
                        pass
                raise

    def _require_prepared_chapter(self, chapter: int) -> None:
        prepared = self._prepared_chapters
        if prepared is None:
            raise ProvenanceRefused(
                'stage5.write 必须先预建本批 session，才能启动任意 wire')
        if chapter not in prepared:
            raise ProvenanceRefused(
                f'Stage5 第 {chapter} 章不属于本批冻结章集 {prepared!r}')
        if chapter in self._terminal:
            raise ProvenanceRefused(
                f'Stage5 第 {chapter} 章 session 已终态，不能再调用模型')

    def _client_for(self, chapter: int) -> SessionAgentClient:
        with self._lock:
            self._require_prepared_chapter(chapter)
            client = self._clients.get(chapter)
            if client is None:
                if chapter in self._restored:
                    raise ProvenanceRefused(
                        f'Stage5 第 {chapter} 章 checkpoint 必须先排入恢复反馈，'
                        '不能伪造旧 provider resume')
                raise ProvenanceRefused(
                    f'Stage5 第 {chapter} 章启动时发现 checkpoint，'
                    '恢复验证失败后不得临时造 driver 重新付费')
            return client

    def _register_provider_owner(
        self,
        chapter: int,
        logical_session_id: str,
        provider_session_id: object,
    ) -> None:
        if not isinstance(provider_session_id, str) or not provider_session_id:
            return
        key = (self.profile.driver, provider_session_id)
        owner = (chapter, logical_session_id)
        with self._lock:
            prior = self._provider_session_owners.get(key)
            if prior is not None and prior != owner:
                self._failed_chapters.update((prior[0], chapter))
                raise ProvenanceRefused(
                    f'Stage5 provider session {provider_session_id!r} 同时属于 '
                    f'第 {prior[0]}/{chapter} 章或两个 logical sessions；'
                    '拒绝跨章共享/恢复时偷接旧 session')
            self._provider_session_owners[key] = owner

    def ask_json(self, system: str, user: str, **kwargs: Any) -> Any:
        model_cls = kwargs.pop('model_cls', None)
        if model_cls is not ChapterResponse:
            raise ProvenanceRefused(
                'stage5.write 请求的 model_cls 必须精确为 ChapterResponse')
        purpose = str(kwargs.get('purpose') or '')
        chapter = self._chapter_of_purpose(purpose)
        progress = kwargs.get('progress')
        if not isinstance(progress, RequestProgress) or progress.chapter != chapter:
            raise ProvenanceRefused(
                f'{purpose}: 每次请求必须带本章独享的 RequestProgress')
        if kwargs.get('max_tokens') != write.MAX_TOKENS:
            raise ProvenanceRefused(f'{purpose}: requested ceiling 与 Stage5 契约漂移')
        if kwargs.get('recovery_policy') != stage5_runs.RECOVERY_POLICY:
            raise ProvenanceRefused(f'{purpose}: recovery policy 漂移')
        client = self._client_for(chapter)
        with self._lock:
            subject = self._subjects.get(chapter)
            current = (system, user, write.MAX_TOKENS)
            if subject is not None and subject != current:
                raise ProvenanceRefused(
                    f'{purpose}: 同一章 session 的原始 system/user 漂移')
            self._subjects[chapter] = current
        try:
            result = client.ask_json(
                system, user, model_cls=model_cls, **kwargs)
        except Exception:
            if client.driver_failed:
                with self._lock:
                    self._failed_chapters.add(chapter)
            raise
        finally:
            self._register_provider_owner(
                chapter, client.session_run_id,
                client.observed_provider_session_id)
        with self._lock:
            # 新响应已经铸成当前 client acceptance；只有此时才能退休旧索引。
            self._restored.pop(chapter, None)
        return result

    def accepted_checkpoint(self, purpose: str) -> dict[str, Any]:
        chapter = self._chapter_of_purpose(purpose)
        with self._lock:
            client = self._clients.get(chapter)
            restored = self._restored.get(chapter)
        if client is not None and client.accepted_attempt(purpose) is not None:
            return client.accepted_checkpoint(purpose)
        if restored is not None:
            return copy.deepcopy(restored)
        raise ProvenanceRefused(f'{purpose}: 尚无 live/restored acceptance')

    def restore_checkpoint_acceptance(
        self,
        purpose: str,
        record: Mapping[str, Any],
        *,
        system: str | None = None,
        user: str | None = None,
        max_tokens: int | None = None,
        recovery_policy: str | None = None,
        **unsupported: object,
    ) -> None:
        """保存 core 已验证的历史 acceptance；这里不再复制 20-key validator。"""
        chapter = self._chapter_of_purpose(purpose)
        if unsupported:
            raise ProvenanceRefused(
                f'{purpose}: checkpoint 恢复含未登记参数 {sorted(unsupported)}')
        if (not isinstance(system, str) or not isinstance(user, str)
                or max_tokens != write.MAX_TOKENS
                or recovery_policy != stage5_runs.RECOVERY_POLICY):
            raise ProvenanceRefused(
                f'{purpose}: checkpoint 恢复缺当前 subject/ceiling/policy')
        binding = record.get('accepted_request')
        if (record.get('acceptance_protocol') != SESSION_PROTOCOL_VERSION
                or record.get('profile') != self.profile.model_dump(mode='json')
                or not isinstance(binding, Mapping)
                or binding.get('node') != self.ctx.node
                or binding.get('chapter') != chapter
                or binding.get('purpose') != purpose
                or binding.get('requested_output_token_ceiling') != max_tokens
                or binding.get('protocol_version') != SESSION_PROTOCOL_VERSION):
            raise ProvenanceRefused(
                f'{purpose}: checkpoint 路由/profile/request binding 漂移')
        provider_id = record.get('provider_session_id')
        logical_id = record.get('session_run_id')
        turn = record.get('accepted_turn_index')
        if (not isinstance(provider_id, str) or not provider_id
                or not isinstance(logical_id, str) or not logical_id
                or not isinstance(turn, int) or isinstance(turn, bool)
                or turn < 1 or turn > self.max_turns):
            raise ProvenanceRefused(
                f'{purpose}: checkpoint 缺 provider/logical session 或累计轮次身份')
        normalized = copy.deepcopy(dict(record))
        with self._lock:
            self._require_prepared_chapter(chapter)
            if chapter in self._clients:
                raise ProvenanceRefused(
                    f'{purpose}: 已为此章预建 live client，不能再恢复 checkpoint')
            prior = self._restored.get(chapter)
            if prior is not None and prior != normalized:
                raise ProvenanceRefused(
                    f'{purpose}: 同一章被恢复为两份不同 acceptance')
            subject = (system, user, max_tokens)
            old_subject = self._subjects.get(chapter)
            if old_subject is not None and old_subject != subject:
                raise ProvenanceRefused(f'{purpose}: 恢复 subject 发生漂移')
            self._subjects[chapter] = subject
            self._restored[chapter] = normalized
        # 即使这里发现另一章复用了 provider id，也保留刚通过 core 验证的
        # acceptance，使批次收口能给两条历史 logical session 都补失败 ended。
        self._register_provider_owner(chapter, logical_id, provider_id)

    def accepted_attempt(self, purpose: str) -> str | None:
        chapter = self._chapter_of_purpose(purpose)
        with self._lock:
            client = self._clients.get(chapter)
            restored = self._restored.get(chapter)
        if client is not None:
            value = client.accepted_attempt(purpose)
            if value is not None:
                return value
        value = None if restored is None else restored.get('attempt_id')
        return value if isinstance(value, str) and value else None

    def accepted_model(self, purpose: str) -> str | None:
        chapter = self._chapter_of_purpose(purpose)
        with self._lock:
            client = self._clients.get(chapter)
            restored = self._restored.get(chapter)
        if client is not None and client.accepted_attempt(purpose) is not None:
            return client.accepted_model(purpose)
        value = None if restored is None else restored.get('model')
        return value if isinstance(value, str) and value else None

    def accepted_models(self, *purposes: str) -> tuple[str, ...]:
        with self._lock:
            selected = purposes or tuple(
                stage5_runs.purpose_of(chapter)
                for chapter in (self._prepared_chapters or ()))
        return tuple(sorted({
            model for purpose in selected
            if (model := self.accepted_model(purpose)) is not None
        }))

    def accepted_request_fingerprint(self, purpose: str) -> str | None:
        record = self.accepted_checkpoint(purpose)
        value = record.get('request_fingerprint')
        return value if isinstance(value, str) and value else None

    def queue_code_validation_feedback(
        self,
        rows: Sequence[Mapping[str, object]],
        *,
        purpose: str | None = None,
    ) -> bool:
        if purpose is None:
            raise ProvenanceRefused('stage5.write 代码反馈必须显式带逐章 purpose')
        chapter = self._chapter_of_purpose(purpose)
        client = self._client_for(chapter)
        return client.queue_code_validation_feedback(rows, purpose=purpose)

    def queue_restored_code_validation_feedback(
        self,
        rows: Sequence[Mapping[str, object]],
        *,
        purpose: str | None = None,
    ) -> bool:
        """坏 checkpoint 在还有总预算时才创建新 client，并排入完整安全反馈。"""
        if purpose is None:
            raise ProvenanceRefused('Stage5 恢复反馈必须显式带逐章 purpose')
        chapter = self._chapter_of_purpose(purpose)
        with self._lock:
            self._require_prepared_chapter(chapter)
            record = self._restored.get(chapter)
            subject = self._subjects.get(chapter)
            if record is None or subject is None:
                raise ProvenanceRefused(
                    f'{purpose}: 没有 core 已验证 checkpoint 可继续')
            turn = record.get('accepted_turn_index')
            if (not isinstance(turn, int) or isinstance(turn, bool)):
                raise ProvenanceRefused(f'{purpose}: checkpoint 累计轮次非法')
            if chapter in self._clients:
                raise ProvenanceRefused(
                    f'{purpose}: checkpoint 恢复 client 已存在，不能重复排队')
            client = self._new_client(
                chapter, probe_driver=turn < self.max_turns)
            self._clients[chapter] = client
            system, user, max_tokens = subject
            try:
                client.restore_checkpoint_acceptance(
                    purpose,
                    record,
                    system=system,
                    user=user,
                    max_tokens=max_tokens,
                    recovery_policy=stage5_runs.RECOVERY_POLICY,
                )
                queued = client.queue_restored_code_validation_feedback(
                    rows, purpose=purpose)
            except Exception:
                self._clients.pop(chapter, None)
                try:
                    client.close()
                finally:
                    raise
            if not queued:
                self._clients.pop(chapter, None)
                client.close()
            return queued

    def purpose_can_resume(self, purpose: str) -> bool:
        chapter = self._chapter_of_purpose(purpose)
        with self._lock:
            client = self._clients.get(chapter)
        return bool(client is not None and client.can_resume)

    def purpose_driver_failed(self, purpose: str) -> bool:
        chapter = self._chapter_of_purpose(purpose)
        with self._lock:
            client = self._clients.get(chapter)
        return bool(client is not None and client.driver_failed)

    def _states_for(
        self, chapter: int, outcome: str,
    ) -> tuple[str, str, str | None]:
        if outcome not in self._OUTCOMES:
            raise ValueError(f'Stage5 session outcome 未登记：{outcome!r}')
        client = self._clients.get(chapter)
        if chapter in self._failed_chapters:
            if client is not None and client.provider_session_reused:
                return 'failed', 'unknown', 'session_isolation'
            if client is not None and client.driver_failed:
                return 'failed', 'unknown', 'agent_driver'
            return 'failed', 'unknown', 'session_isolation'
        if client is not None and client.driver_failed:
            return 'failed', 'unknown', 'agent_driver'
        return {
            'delivered': ('ok', 'delivered', None),
            'blocked': ('ok', 'blocked', 'artifact_validation'),
            'response_failed': ('ok', 'failed', 'response_validation'),
            'artifact_failed': ('ok', 'unknown', 'artifact_verification'),
            'executor_failed': ('ok', 'failed', 'node_executor'),
        }[outcome]

    def finish_chapter_session(
        self, chapter: int, *, outcome: str,
        delivery_attempts: int | None = None,
        same_run_recovered: bool | None = None,
        first_failure_phase: str | None = None,
        first_failure_detail: str | None = None,
    ) -> None:
        """一章正式终验后只结束本章 session；兄弟章仍可继续运行。

        提交诊断四元组只在确实发生过正式提交尝试时由调用方给出
        （``write.py`` 的提交链）；None = 未记录，不能倒推。
        """
        ordinal = self._chapter(chapter)
        with self._lock:
            prepared = self._prepared_chapters
            if prepared is None or ordinal not in prepared:
                raise ProvenanceRefused(
                    f'Stage5 第 {ordinal} 章不属于已冻结的本批章集')
            states = self._states_for(ordinal, outcome)
            prior = self._terminal.get(ordinal)
            if prior is not None:
                if prior != states:
                    raise ProvenanceRefused(
                        f'Stage5 第 {ordinal} 章 session 已以 {prior} 终态，'
                        f'不能改写为 {states}')
                return
            client = self._clients.get(ordinal)
            restored = self._restored.get(ordinal)
            if restored is not None:
                # clean checkpoint 的正式/INVALID/拒稿 delivery 已在 producer 中
                # 完成；这里才用当前代码版本闭合旧 session。dirty checkpoint 已
                # 由公共 queue 收口，``only_if_open`` 会复核 acceptance 后保留历史。
                agent_session_audit.finish_restored_session(
                    self.ctx.novel_dir.parent,
                    restored,
                    process_state=states[0],
                    delivery_state=states[1],
                    error_kind=states[2],
                    delivery_attempts=delivery_attempts,
                    same_run_recovered=same_run_recovered,
                    first_failure_phase=first_failure_phase,
                    first_failure_detail=first_failure_detail,
                    end_code_version=prov.code_version(),
                    only_if_open=True,
                )
            if client is not None:
                client.finish(
                    process_state=states[0],
                    delivery_state=states[1],
                    error_kind=states[2],
                    delivery_attempts=delivery_attempts,
                    same_run_recovered=same_run_recovered,
                    first_failure_phase=first_failure_phase,
                    first_failure_detail=first_failure_detail,
                )
            self._terminal[ordinal] = states

    def finish_all(self, *, failure: BaseException | None) -> None:
        """异常尾窗补齐尚未归类的 live session；成功时漏通知是关闭式失败。"""
        with self._lock:
            chapters = tuple(self._prepared_chapters or ())
            missing = [chapter for chapter in chapters if chapter not in self._terminal]
        errors: list[BaseException] = []
        for chapter in missing:
            outcome = (
                'artifact_failed' if isinstance(failure, ProvenanceRefused)
                else 'response_failed' if isinstance(failure, LlmFailed)
                else 'executor_failed')
            try:
                self.finish_chapter_session(chapter, outcome=outcome)
            except BaseException as exc:
                errors.append(exc)
        if failure is None and missing:
            errors.append(ProvenanceRefused(
                f'stage5.write 成功返回前有章节未报告 delivery：{missing}'))
        if errors:
            raise BaseExceptionGroup(
                'stage5.write 逐章 ended 审计收口失败', errors)

    def close(self) -> None:
        with self._lock:
            if self._closed:
                return
            clients = tuple(self._clients.values())
            self._closed = True
        errors: list[BaseException] = []
        for client in clients:
            try:
                client.close()
            except BaseException as exc:
                errors.append(exc)
        if errors:
            raise BaseExceptionGroup(
                'stage5.write 逐章 driver/cwd 关闭失败', errors)


class Stage5WriteAgent(NodeExecutorAgent[CheckReport]):
    """默认、定向章节与 revise 共用同一个逐章 executor 入口。"""

    node = 'stage5.write'

    def __init__(
        self,
        ctx: RunContext,
        *,
        driver_factory: Stage5DriverFactory | None = None,
        max_turns: int = 4,
        concurrency: int = MAX_CONCURRENCY,
        chapters: str | None = None,
        revise: bool = False,
    ) -> None:
        if ctx.node != self.node:
            raise ValueError(
                f'{type(self).__name__} 只接受 {self.node}，实得 {ctx.node}')
        if ctx.executor_profile is None:
            raise ValueError(f'{self.node} executor 缺 resolved profile')
        if chapters is not None and not isinstance(chapters, str):
            raise ValueError('stage5.write chapters 必须是字符串或 None')
        if not isinstance(revise, bool):
            raise ValueError('stage5.write revise 必须是 bool')
        self.ctx = ctx
        self.chapters = chapters
        self.revise = revise
        self.pool = Stage5SessionPool(
            ctx,
            driver_factory=driver_factory,
            max_turns=max_turns,
            concurrency=concurrency,
        )

    @staticmethod
    def _raise_group(
        primary: BaseException | None, extras: Sequence[BaseException],
    ) -> None:
        if primary is None and not extras:
            return
        if primary is not None and not extras:
            raise primary
        errors = ([primary] if primary is not None else []) + list(extras)
        raise BaseExceptionGroup(
            'stage5.write 执行/逐章会话收口发生多项错误', errors)

    def run(self) -> NodeAgentResult[CheckReport]:
        result: NodeAgentResult[CheckReport] | None = None
        primary: BaseException | None = None
        try:
            report = (
                write.run_revise(self.ctx, self.pool, self.chapters)
                if self.revise
                else write.run_write(self.ctx, self.pool, self.chapters)
            )
            result = NodeAgentResult(
                artifact=report, turns=self.pool.turns_used,
                passed=report.passed, report=report)
        except BaseException as exc:
            primary = exc

        extras: list[BaseException] = []
        try:
            self.pool.finish_all(failure=primary)
        except BaseException as exc:
            extras.append(exc)
        try:
            self.pool.close()
        except BaseException as exc:
            extras.append(exc)
        if primary is not None or extras:
            self._raise_group(primary, extras)
        assert result is not None
        return result


SemanticDriverFactory = Callable[[str], AgentDriver]


class Stage6HookAgent(NodeExecutorAgent[Hook]):
    """单 session 完整 replacement 循环；代码判定并提交正式 Hook。"""

    node = 'stage6.hook'

    def run(self) -> NodeAgentResult[Hook]:
        try:
            prepared = hook.prepare_hook_candidate(self.ctx)
            batch = hook.request_hook_candidates(
                self.ctx, self.client, prepared)
            evaluation = hook.evaluate_hook_candidate(
                self.ctx, self.client, prepared, batch)
            artifact = hook.finalize_hook_candidate(
                self.ctx, self.client, prepared, evaluation)
            self.client.finish(
                process_state='ok', delivery_state='delivered')
            return NodeAgentResult(
                artifact=artifact, turns=self.client.turns_used,
                passed=True)
        except GateBlocked:
            self.client.finish(
                process_state='ok', delivery_state='blocked',
                error_kind='artifact_validation')
            raise
        except ProvenanceRefused:
            self.client.finish(
                process_state='ok', delivery_state='unknown',
                error_kind='artifact_verification')
            raise
        except LlmFailed:
            self.client.finish(
                process_state=(
                    'failed' if self.client.driver_failed else 'ok'),
                delivery_state=(
                    'unknown' if self.client.driver_failed else 'failed'),
                error_kind=(
                    'agent_driver' if self.client.driver_failed
                    else 'response_validation'),
            )
            raise
        except Exception:
            self.client.finish(
                process_state=(
                    'failed' if self.client.driver_failed else 'ok'),
                delivery_state=(
                    'unknown' if self.client.driver_failed else 'failed'),
                error_kind=(
                    'agent_driver' if self.client.driver_failed
                    else 'node_executor'),
            )
            raise
        finally:
            self.client.close()


class SemanticSessionPool:
    """``stage6.semantic`` 的 A/B 双 session 路由与恢复桥。

    判官 checkpoint 是已经结束的一次历史付费事实，不是可 resume 的 live
    provider session。pool 因此只恢复 adoption 索引；只有缺 checkpoint 的
    role 才构造新的 driver/client/cwd。两个缺失 role 必须先全部构造并通过
    同一份只读 probe，producer 才能启动任意一次 wire。
    """

    _ROLES = ('A', 'B')

    def __init__(
        self,
        ctx: RunContext,
        *,
        driver_factory: SemanticDriverFactory | None = None,
        max_turns: int = 4,
    ) -> None:
        if ctx.node != 'stage6.semantic':
            raise ValueError(
                f'SemanticSessionPool 只接受 stage6.semantic，实得 {ctx.node!r}')
        if ctx.executor_profile is None:
            raise ValueError('stage6.semantic executor 缺 resolved profile')
        self.ctx = ctx
        self.profile: AgentProfile = ctx.executor_profile
        self.model = self.profile.model
        self.max_turns = max_turns
        self._driver_factory = driver_factory or (
            lambda _role: create_driver(self.profile, role='executor'))
        self._clients: dict[str, SessionAgentClient] = {}
        self._restored: dict[str, dict[str, Any]] = {}
        self._subjects: dict[str, tuple[str, str]] = {}
        self._probe: AgentProbe | None = None
        self._driver_owners: dict[int, str] = {}
        self._provider_session_owners: dict[tuple[str, str], str] = {}
        self._failed_roles: set[str] = set()
        self._prepared = False
        self._closed = False
        self._lock = threading.RLock()

    @staticmethod
    def _role_of_purpose(purpose: str) -> str:
        prefix = 'stage6.semantic.'
        if not purpose.startswith(prefix):
            raise ProvenanceRefused(
                f'stage6.semantic judge purpose 非法：{purpose!r}')
        role = purpose.removeprefix(prefix)
        if role not in SemanticSessionPool._ROLES:
            raise ProvenanceRefused(
                f'stage6.semantic judge purpose 必须精确属于 '
                f'{tuple(prefix + value for value in SemanticSessionPool._ROLES)}')
        return role

    @staticmethod
    def _purpose(role: str) -> str:
        if role not in SemanticSessionPool._ROLES:
            raise ProvenanceRefused(f'stage6.semantic 未登记 judge role={role!r}')
        return f'stage6.semantic.{role}'

    @property
    def turns_used(self) -> int:
        with self._lock:
            return sum(client.turns_used for client in self._clients.values())

    @property
    def driver_failed(self) -> bool:
        with self._lock:
            return any(client.driver_failed for client in self._clients.values())

    @property
    def isolation_failed(self) -> bool:
        with self._lock:
            return bool(self._failed_roles)

    @property
    def session_count(self) -> int:
        with self._lock:
            return len(self._clients)

    def _new_client(self, role: str) -> SessionAgentClient:
        driver = self._driver_factory(role)
        driver_id = id(driver)
        prior = self._driver_owners.get(driver_id)
        if prior is not None and prior != role:
            self._failed_roles.update((prior, role))
            raise ProvenanceRefused(
                f'stage6.semantic driver factory 把同一对象同时给了 '
                f'judge {prior}/{role}；已在首个 model start 前拒绝')
        self._driver_owners[driver_id] = role
        try:
            if self._probe is None:
                probe = driver.probe()
                if (driver.driver != self.profile.driver
                        or probe.driver != self.profile.driver
                        or probe.provider != driver.provider
                        or not probe.available):
                    raise ProvenanceRefused(
                        f'judge {role}: driver/probe/profile 身份或可用性不闭合：'
                        f'profile={self.profile.driver!r}, '
                        f'driver={driver.driver!r}, '
                        f'probe={probe.driver!r}/{probe.provider!r}/'
                        f'{probe.available!r}')
                self._probe = probe
            elif (driver.driver != self._probe.driver
                  or driver.provider != self._probe.provider):
                raise ProvenanceRefused(
                    f'judge {role}: driver factory 在同一双盲池内漂移了厂商身份')
            return SessionAgentClient(
                self.ctx,
                self.profile,
                driver,
                parent_run_id=self.ctx.run_id,
                max_turns=self.max_turns,
                preflight_probe=self._probe,
            )
        except Exception:
            self._driver_owners.pop(driver_id, None)
            driver.close()
            raise

    def prepare_initial_sessions(self) -> None:
        """只为缺 checkpoint 的判官预建 client；完整恢复不碰 factory/probe。"""
        with self._lock:
            if self._prepared:
                return
            try:
                for role in self._ROLES:
                    checkpoint = paths.stage6_semantic_checkpoint_path(
                        self.ctx.novel_dir, self.ctx.run_id, role)
                    if checkpoint.is_file():
                        continue
                    self._clients[role] = self._new_client(role)
            except Exception:
                # 预创建阶段尚未有 model start；仍要关掉已经创建的第一名判官。
                clients = tuple(self._clients.values())
                self._clients.clear()
                for client in clients:
                    try:
                        client.close()
                    except Exception:
                        pass
                raise
            self._prepared = True

    def _client_for(self, role: str) -> SessionAgentClient:
        with self._lock:
            if not self._prepared:
                raise ProvenanceRefused(
                    'stage6.semantic 必须先预建全部缺失判官，才能启动任意 wire')
            client = self._clients.get(role)
            if client is None:
                raise ProvenanceRefused(
                    f'judge {role}: 启动时已有 checkpoint，不能在恢复失败后'
                    '临时造 driver 重新付费')
            return client

    def _register_provider_owner(
        self, role: str, provider_session_id: object,
    ) -> None:
        if not isinstance(provider_session_id, str) or not provider_session_id:
            return
        key = (self.profile.driver, provider_session_id)
        with self._lock:
            prior = self._provider_session_owners.get(key)
            if prior is not None and prior != role:
                self._failed_roles.update((prior, role))
                raise ProvenanceRefused(
                    f'stage6.semantic provider session {provider_session_id!r} '
                    f'被 judge {prior}/{role} 共享；拒绝双盲伪隔离')
            self._provider_session_owners[key] = role

    def ask_json(self, system: str, user: str, **kwargs: Any) -> Any:
        model_cls = kwargs.pop('model_cls', None)
        if (not isinstance(model_cls, type)
                or not issubclass(model_cls, BaseModel)):
            raise ProvenanceRefused(
                'stage6.semantic 请求缺显式 Pydantic model_cls')
        purpose = str(kwargs.get('purpose') or '')
        role = self._role_of_purpose(purpose)
        recovery_policy = kwargs.get('recovery_policy')
        if recovery_policy != 'stage6-semantic-v1':
            raise ProvenanceRefused(
                f'{purpose}: 未登记 semantic recovery policy '
                f'{recovery_policy!r}')
        client = self._client_for(role)
        with self._lock:
            subject = self._subjects.get(role)
            if subject is not None and subject != (system, user):
                raise ProvenanceRefused(
                    f'{purpose}: 同一判官 session 的原始 system/user 漂移')
            self._subjects[role] = (system, user)
        try:
            return client.ask_json(
                system, user, model_cls=model_cls, **kwargs)
        except Exception:
            if client.driver_failed:
                with self._lock:
                    self._failed_roles.add(role)
            raise
        finally:
            self._register_provider_owner(
                role, client.observed_provider_session_id)

    def accepted_checkpoint(self, purpose: str) -> dict[str, Any]:
        role = self._role_of_purpose(purpose)
        with self._lock:
            restored = self._restored.get(role)
            client = self._clients.get(role)
        if restored is not None:
            return copy.deepcopy(restored)
        if client is None:
            raise ProvenanceRefused(f'{purpose}: 尚无 live/restored acceptance')
        return client.accepted_checkpoint(purpose)

    def restore_checkpoint_acceptance(
        self, purpose: str, record: Mapping[str, Any], *,
        system: str | None = None, user: str | None = None,
        max_tokens: int | None = None, recovery_policy: str | None = None,
        **unsupported: object,
    ) -> None:
        """恢复已验证 acceptance 的 adoption 索引，不伪造 live provider session。"""
        role = self._role_of_purpose(purpose)
        if unsupported:
            raise ProvenanceRefused(
                f'{purpose}: checkpoint 恢复含未实现参数 {sorted(unsupported)}')
        if system is None or user is None or max_tokens is None:
            raise ProvenanceRefused(
                f'{purpose}: checkpoint 恢复缺 system/user/requested ceiling')
        if recovery_policy != 'stage6-semantic-v1':
            raise ProvenanceRefused(
                f'{purpose}: checkpoint recovery policy 漂移')
        profile = record.get('profile')
        binding = record.get('accepted_request')
        if (record.get('acceptance_protocol') != SESSION_PROTOCOL_VERSION
                or profile != self.profile.model_dump(mode='json')
                or not isinstance(binding, dict)):
            raise ProvenanceRefused(
                f'{purpose}: checkpoint profile/request binding 漂移')
        schema_sha = binding.get('schema_sha256')
        if (not isinstance(schema_sha, str) or len(schema_sha) != 64
                or any(ch not in '0123456789abcdef' for ch in schema_sha)):
            raise ProvenanceRefused(
                f'{purpose}: checkpoint 缺合法 response schema SHA-256')
        expected = {
            'node': self.ctx.node,
            'chapter': None,
            'purpose': purpose,
            'system_sha256': hashlib.sha256(system.encode('utf-8')).hexdigest(),
            'user_sha256': hashlib.sha256(user.encode('utf-8')).hexdigest(),
            'schema_sha256': schema_sha,
            'requested_model': self.profile.model,
            'requested_effort': self.profile.reasoning_effort,
            'requested_output_token_ceiling': max_tokens,
            'protocol_version': SESSION_PROTOCOL_VERSION,
        }
        # 完整 prompt/schema/subject/ledger 联结已由 stage6_runs.restore_checkpoint
        # 在调用本桥之前重验；这里再守 pool 能独立知道的 exact request 面。
        if binding != expected:
            raise ProvenanceRefused(
                f'{purpose}: checkpoint accepted_request 与当前判官请求漂移')
        subject_sha256 = hashlib.sha256(json.dumps(
            expected, ensure_ascii=False, sort_keys=True,
            separators=(',', ':'), allow_nan=False,
        ).encode('utf-8')).hexdigest()
        if record.get('subject_sha256') != subject_sha256:
            raise ProvenanceRefused(
                f'{purpose}: checkpoint logical subject SHA-256 不闭合')
        if (not isinstance(record.get('provider_session_id'), str)
                or not record.get('provider_session_id')
                or not isinstance(record.get('session_run_id'), str)
                or not record.get('session_run_id')):
            raise ProvenanceRefused(
                f'{purpose}: checkpoint 缺 provider/logical session 身份')
        prior_turns = record.get('accepted_turn_index')
        prior_links = record.get('attempt_links')
        if (not isinstance(prior_turns, int) or isinstance(prior_turns, bool)
                or prior_turns < 1 or prior_turns > self.max_turns
                or not isinstance(prior_links, list)
                or len(prior_links) != prior_turns):
            raise ProvenanceRefused(
                f'{purpose}: checkpoint turn/attempt links 与当前预算不闭合')
        attempt_id = str(record.get('attempt_id') or '')
        identity = self.ctx.ledger.request_identity_for_attempt(attempt_id)
        if (record.get('request_fingerprint') != identity.get('request_fingerprint')
                or record.get('model') != identity.get('model')
                or record.get('ledger_response_sha256')
                != identity.get('response_sha256')):
            raise ProvenanceRefused(
                f'{purpose}: checkpoint accepted attempt 与成本账漂移')
        if identity.get('model') in (None, '', 'unknown'):
            raise ProvenanceRefused(
                f'{purpose}: checkpoint 缺实际 accepted model')
        provider_session_id = record.get('provider_session_id')
        self._register_provider_owner(role, provider_session_id)
        with self._lock:
            if role in self._clients:
                raise ProvenanceRefused(
                    f'{purpose}: 已为此 role 预建 live client，不能再恢复 checkpoint')
            prior = self._restored.get(role)
            normalized = copy.deepcopy(dict(record))
            if prior is not None and prior != normalized:
                raise ProvenanceRefused(
                    f'{purpose}: 同一 role 被恢复为两份不同 acceptance')
            self._restored[role] = normalized
            self._subjects[role] = (system, user)

    def accepted_attempt(self, purpose: str) -> str | None:
        role = self._role_of_purpose(purpose)
        with self._lock:
            restored = self._restored.get(role)
            client = self._clients.get(role)
        if restored is not None:
            value = restored.get('attempt_id')
            return value if isinstance(value, str) and value else None
        return None if client is None else client.accepted_attempt(purpose)

    def accepted_models(self, *purposes: str) -> tuple[str, ...]:
        selected = purposes or tuple(self._purpose(role) for role in self._ROLES)
        models: set[str] = set()
        for purpose in selected:
            attempt_id = self.accepted_attempt(purpose)
            if attempt_id is None:
                continue
            model = self.ctx.ledger.model_for_attempt(attempt_id)
            if isinstance(model, str) and model:
                models.add(model)
        return tuple(sorted(models))

    def _finish_rows(
        self, *, delivered: bool, failure: BaseException | None,
    ) -> tuple[Exception, ...]:
        with self._lock:
            clients = dict(self._clients)
            restored = copy.deepcopy(self._restored)
            failed_roles = set(self._failed_roles)
            accepted_roles = {
                role for role, client in clients.items()
                if client.accepted_attempt(self._purpose(role)) is not None
            } | set(restored)
        errors: list[Exception] = []
        for role in self._ROLES:
            client = clients.get(role)
            acceptance = restored.get(role)
            try:
                if delivered:
                    state = ('ok', 'delivered', None)
                elif (role in failed_roles
                      or (client is not None and client.driver_failed)):
                    state = (
                        'failed', 'unknown',
                        ('session_isolation' if role in failed_roles
                         and (client is None
                              or not client.driver_failed
                              or client.provider_session_reused)
                         else 'agent_driver'),
                    )
                elif len(accepted_roles) == 1 and role in accepted_roles:
                    state = ('ok', 'blocked', 'peer_result_missing')
                elif role not in accepted_roles:
                    state = (
                        'ok',
                        ('failed' if isinstance(failure, LlmFailed)
                         else 'unknown'),
                        ('response_validation' if isinstance(failure, LlmFailed)
                         else 'node_executor'),
                    )
                else:
                    state = (
                        'ok', 'unknown',
                        ('artifact_verification'
                         if isinstance(failure, ProvenanceRefused)
                         else 'node_executor'),
                    )
                process_state, delivery_state, error_kind = state
                if client is not None:
                    client.finish(
                        process_state=process_state,
                        delivery_state=delivery_state,
                        error_kind=error_kind,
                    )
                elif acceptance is not None:
                    # 旧 run 可能已在单边失败时收成 blocked；公共桥先完整
                    # 复验 acceptance/turn，再只为仍开放的 crash 尾窗补 ended。
                    agent_session_audit.finish_restored_session(
                        self.ctx.novel_dir.parent,
                        acceptance,
                        process_state=process_state,
                        delivery_state=delivery_state,
                        error_kind=error_kind,
                        end_code_version=prov.code_version(),
                        only_if_open=True,
                    )
            except Exception as exc:
                errors.append(exc)
        return tuple(errors)

    def finish_all(
        self, *, delivered: bool, failure: BaseException | None = None,
    ) -> None:
        errors = self._finish_rows(delivered=delivered, failure=failure)
        if errors:
            raise ExceptionGroup(
                'stage6.semantic 逐判官 ended 审计写入失败', list(errors))

    def close(self) -> None:
        with self._lock:
            if self._closed:
                return
            clients = tuple(self._clients.values())
            self._closed = True
        errors: list[Exception] = []
        for client in clients:
            try:
                client.close()
            except Exception as exc:
                errors.append(exc)
        if errors:
            raise ExceptionGroup(
                'stage6.semantic 逐判官 driver/cwd 关闭失败', errors)


class Stage6SemanticAgent(NodeExecutorAgent[HookSemanticReview]):
    """双盲判官各用一条独立 session；代码合并、校验并正式落盘。"""

    node = 'stage6.semantic'

    def __init__(
        self,
        ctx: RunContext,
        *,
        driver_factory: SemanticDriverFactory | None = None,
        max_turns: int = 4,
    ) -> None:
        if ctx.node != self.node:
            raise ValueError(f'{type(self).__name__} 只接受 {self.node}，实得 {ctx.node}')
        if ctx.executor_profile is None:
            raise ValueError(f'{self.node} executor 缺 resolved profile')
        self.ctx = ctx
        self.pool = SemanticSessionPool(
            ctx, driver_factory=driver_factory, max_turns=max_turns)

    @staticmethod
    def _raise_group(
        primary: BaseException | None, extras: Sequence[BaseException],
    ) -> None:
        if primary is None and not extras:
            return
        if primary is not None and not extras:
            raise primary
        errors = ([primary] if primary is not None else []) + list(extras)
        raise BaseExceptionGroup(
            'stage6.semantic 执行/会话收口发生多项错误', errors)

    def run(self) -> NodeAgentResult[HookSemanticReview]:
        result: NodeAgentResult[HookSemanticReview] | None = None
        primary: BaseException | None = None
        delivered = False
        try:
            prepared = semantic.prepare_semantic_candidate(self.ctx)
            self.pool.prepare_initial_sessions()
            responses = semantic.request_semantic_candidates(
                self.ctx, self.pool, prepared)
            evaluation = semantic.evaluate_semantic_candidate(
                self.ctx, self.pool, prepared, responses)
            artifact, report = semantic.finalize_semantic_candidate(
                self.ctx, self.pool, prepared, evaluation)
            delivered = True
            result = NodeAgentResult(
                artifact=artifact, turns=self.pool.turns_used,
                passed=report.passed, report=report)
        except BaseException as exc:
            primary = exc

        extras: list[BaseException] = []
        try:
            self.pool.finish_all(delivered=delivered, failure=primary)
        except BaseException as exc:
            extras.append(exc)
        try:
            self.pool.close()
        except BaseException as exc:
            extras.append(exc)
        if primary is not None or extras:
            self._raise_group(primary, extras)
        assert result is not None
        return result


class Stage7RenameAgent(NodeExecutorAgent[NameMap]):
    """单 session 完整人物表 replacement；代码 HARD 与提交尾窗均由 producer 掌管。"""

    node = 'stage7.rename'

    def run(self) -> NodeAgentResult[NameMap]:
        try:
            prepared = rename.prepare_rename_candidate(self.ctx)
            batch = rename.request_rename_candidate(
                self.ctx, self.client, prepared)
            evaluation = rename.evaluate_rename_candidate(
                self.ctx, self.client, prepared, batch)
            artifact = rename.finalize_rename_candidate(
                self.ctx, self.client, prepared, evaluation)
            self.client.finish(
                process_state='ok', delivery_state='delivered')
            return NodeAgentResult(
                artifact=artifact, turns=self.client.turns_used,
                passed=True)
        except GateBlocked:
            self.client.finish(
                process_state='ok', delivery_state='blocked',
                error_kind='artifact_validation')
            raise
        except ProvenanceRefused:
            self.client.finish(
                process_state='ok', delivery_state='unknown',
                error_kind='artifact_verification')
            raise
        except LlmFailed:
            self.client.finish(
                process_state=(
                    'failed' if self.client.driver_failed else 'ok'),
                delivery_state=(
                    'unknown' if self.client.driver_failed else 'failed'),
                error_kind=(
                    'agent_driver' if self.client.driver_failed
                    else 'response_validation'),
            )
            raise
        except Exception:
            self.client.finish(
                process_state=(
                    'failed' if self.client.driver_failed else 'ok'),
                delivery_state=(
                    'unknown' if self.client.driver_failed else 'failed'),
                error_kind=(
                    'agent_driver' if self.client.driver_failed
                    else 'node_executor'),
            )
            raise
        finally:
            self.client.close()


EXECUTOR_FACTORIES: dict[str, Callable[..., NodeExecutorAgent[BaseModel]]] = {
    'stage2.global': Stage2GlobalAgent,
    'stage2.blueprint': Stage2BlueprintAgent,
    'stage4.design': Stage4DesignAgent,
    'stage4.plan': Stage4PlanAgent,
    'stage5.write': Stage5WriteAgent,
    'stage6.hook': Stage6HookAgent,
    'stage6.semantic': Stage6SemanticAgent,
    'stage7.rename': Stage7RenameAgent,
}


def build_node_agent(
    ctx: RunContext, *, driver: AgentDriver | None = None,
    driver_factory: BlueprintDriverFactory | None = None,
    stage4_design_driver_factory: Stage4DesignDriverFactory | None = None,
    stage5_driver_factory: Stage5DriverFactory | None = None,
    semantic_driver_factory: SemanticDriverFactory | None = None,
    chapters: str | None = None,
    revise: bool = False,
    repair_decision_sha256: str | None = None,
    repair_scope: plan.DesignRepairScope | None = None,
    max_turns: int = 4,
    concurrency: int = MAX_CONCURRENCY,
) -> NodeExecutorAgent[BaseModel]:
    factory = EXECUTOR_FACTORIES.get(ctx.node)
    if factory is None:
        raise ValueError(
            f'{ctx.node} 尚未按迁移顺序接入 executor agent；拒绝静默回退 DeepSeek')
    if (ctx.node != 'stage4.design'
            and (repair_decision_sha256 is not None
                 or repair_scope is not None)):
        raise ValueError(f'{ctx.node} 不接受 Stage4 repair 参数')
    if ctx.node == 'stage2.blueprint':
        if driver is not None:
            raise ValueError(
                'stage2.blueprint 不能让多个章节共享一个 driver；'
                '测试注入请使用 driver_factory')
        if semantic_driver_factory is not None:
            raise ValueError('stage2.blueprint 不接受 semantic_driver_factory')
        if stage4_design_driver_factory is not None:
            raise ValueError(
                'stage2.blueprint 不接受 stage4_design_driver_factory')
        if stage5_driver_factory is not None:
            raise ValueError('stage2.blueprint 不接受 stage5_driver_factory')
        if chapters is not None or revise:
            raise ValueError('stage2.blueprint 不接受 Stage5 chapters/revise 参数')
        return factory(
            ctx, driver_factory=driver_factory, max_turns=max_turns,
            concurrency=concurrency)
    if ctx.node == 'stage4.design':
        if driver is not None:
            raise ValueError(
                'stage4.design 的 strategy/各章不能共享一个 driver；'
                '测试注入请使用 stage4_design_driver_factory')
        if (driver_factory is not None or stage5_driver_factory is not None
                or semantic_driver_factory is not None):
            raise ValueError(
                'stage4.design 只接受 stage4_design_driver_factory，'
                '不接受其他 pool factory')
        if chapters is not None or revise:
            raise ValueError('stage4.design 不接受 Stage5 chapters/revise 参数')
        return factory(
            ctx,
            driver_factory=stage4_design_driver_factory,
            max_turns=max_turns,
            concurrency=concurrency,
            repair_decision_sha256=repair_decision_sha256,
            repair_scope=repair_scope,
        )
    if ctx.node == 'stage5.write':
        if driver is not None:
            raise ValueError(
                'stage5.write 不能让多个章节共享一个 driver；'
                '测试注入请使用 stage5_driver_factory')
        if (driver_factory is not None or semantic_driver_factory is not None
                or stage4_design_driver_factory is not None):
            raise ValueError(
                'stage5.write 只接受 stage5_driver_factory，不接受其他 pool factory')
        return factory(
            ctx,
            driver_factory=stage5_driver_factory,
            max_turns=max_turns,
            concurrency=concurrency,
            chapters=chapters,
            revise=revise,
        )
    if ctx.node == 'stage6.semantic':
        if driver is not None:
            raise ValueError(
                'stage6.semantic 不能让 A/B 共用一个 driver；'
                '测试注入请使用 semantic_driver_factory')
        if driver_factory is not None:
            raise ValueError(
                'stage6.semantic 不接受逐章 driver_factory；'
                '请使用 semantic_driver_factory')
        if stage5_driver_factory is not None:
            raise ValueError('stage6.semantic 不接受 stage5_driver_factory')
        if stage4_design_driver_factory is not None:
            raise ValueError(
                'stage6.semantic 不接受 stage4_design_driver_factory')
        if chapters is not None or revise:
            raise ValueError('stage6.semantic 不接受 Stage5 chapters/revise 参数')
        return factory(
            ctx, driver_factory=semantic_driver_factory, max_turns=max_turns)
    if semantic_driver_factory is not None:
        raise ValueError(f'{ctx.node} 不接受 semantic_driver_factory')
    if driver_factory is not None:
        raise ValueError(f'{ctx.node} 不接受逐章 driver_factory')
    if stage4_design_driver_factory is not None:
        raise ValueError(
            f'{ctx.node} 不接受 stage4_design_driver_factory')
    if stage5_driver_factory is not None:
        raise ValueError(f'{ctx.node} 不接受 stage5_driver_factory')
    if chapters is not None or revise:
        raise ValueError(f'{ctx.node} 不接受 Stage5 chapters/revise 参数')
    return factory(ctx, driver=driver, max_turns=max_turns)


__all__ = [
    'BlueprintDriverFactory', 'BlueprintSessionPool', 'EXECUTOR_FACTORIES',
    'NodeAgentResult', 'NodeExecutorAgent', 'Stage2BlueprintAgent',
    'Stage2GlobalAgent', 'Stage4DesignAgent', 'Stage4DesignDriverFactory',
    'Stage4DesignSessionPool', 'Stage4PlanAgent',
    'Stage4PlanSessionPool',
    'Stage5DriverFactory', 'Stage5SessionPool',
    'Stage5WriteAgent', 'SemanticDriverFactory', 'SemanticSessionPool',
    'Stage6HookAgent',
    'Stage6SemanticAgent', 'Stage7RenameAgent', 'build_node_agent',
]
