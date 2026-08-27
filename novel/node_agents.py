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
from typing import Any, Generic, TypeVar

from pydantic import BaseModel

from contracts.schema.orchestration import AgentProfile
from contracts.schema.report import CheckReport
from contracts.schema.stage2 import (
    ChapterBlueprints, GlobalGrounding, GlobalGroundingResponse,
)
from contracts.schema.stage4 import BlindCandidate, EvidenceBinding
from contracts.schema.stage5 import ChapterResponse
from contracts.schema.stage6 import Hook, HookSemanticReview
from contracts.schema.stage7 import NameMap
from novel import agent_session_audit, paths, prov
from novel.agents import AgentDriver, AgentProbe
from novel.agents.factory import create_driver
from novel.agents.session_client import SESSION_PROTOCOL_VERSION, SessionAgentClient
from novel.config import MAX_CONCURRENCY
from novel.errors import GateBlocked, ProvenanceRefused
from novel.llm import stage5_runs
from novel.llm.client import LlmFailed
from novel.observability import RequestProgress
from novel.run import RunContext
from novel.stages.stage2 import extract
from novel.stages.stage2.character_refs import blueprint_character_prompt_inventory
from novel.stages.stage2.contract import active_contract, contract_for
from novel.stages.stage2.paragraphs import paragraph_spans
from novel.stages.stage2.references import hinge_candidates_by_chapter
from novel.stages.stage4 import plan
from novel.stages.stage5 import write
from novel.stages.stage6 import hook, semantic
from novel.stages.stage7 import rename
from novel.store import ArtifactStore


A = TypeVar('A', bound=BaseModel)


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

    def run(self) -> NodeAgentResult[GlobalGrounding]:
        last_rejected: extract.GlobalCandidateEvaluation | None = None
        try:
            prepared = extract.prepare_global_candidate(self.ctx)
            while True:
                try:
                    response = self.client.ask_json(
                        prepared.system,
                        prepared.user,
                        model_cls=GlobalGroundingResponse,
                        purpose=self.ctx.node,
                        max_tokens=extract.GLOBAL_MAX_TOKENS,
                    )
                except LlmFailed:
                    # 若本 session 至少产出过一份 schema 合格但业务 HARD 的候选，
                    # 后续 parse/schema 轮耗尽时仍把那份最后可恢复候选终态化一次。
                    # driver/subject/code 失败不走这条路，不能拿旧候选掩盖进程失败。
                    if (last_rejected is not None
                            and not self.client.driver_failed
                            and not self.client.can_resume):
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

                evaluation = extract.evaluate_global_candidate(
                    self.ctx, self.client, prepared, response)
                if not evaluation.passed:
                    last_rejected = evaluation
                    if self.client.queue_code_validation_feedback(
                            evaluation.feedback_rows(), purpose=self.ctx.node):
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
        concurrency: int = MAX_CONCURRENCY,
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
        self.concurrency = min(MAX_CONCURRENCY, concurrency)
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
        concurrency: int = MAX_CONCURRENCY,
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
        characters = ArtifactStore(self.ctx).read(
            GlobalGrounding, extract.GLOBAL_REL,
        ).model_dump(by_alias=True)['entities']['characters']
        inventory = blueprint_character_prompt_inventory(characters, main_text)
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


class Stage4DesignAgent(NodeExecutorAgent[BlindCandidate]):
    """Stage4A 盲设计：独立 session，候选与正式提交仍由代码边界掌管。"""

    node = 'stage4.design'

    def run(self) -> NodeAgentResult[BlindCandidate]:
        try:
            artifact = plan.run_design(self.ctx, self.client)
            if not isinstance(artifact, BlindCandidate):
                raise ProvenanceRefused(
                    'stage4.design executor 返回了非 BlindCandidate 正式产物')
            self.client.finish(
                process_state='ok', delivery_state='delivered')
            return NodeAgentResult(
                artifact=artifact, turns=self.client.turns_used,
                passed=True)
        except Exception as exc:
            self._finish_exception(exc)
            raise
        finally:
            self.client.close()


class Stage4PlanAgent(NodeExecutorAgent[BaseModel]):
    """Stage4B 证据绑定/计划编译：typed infeasible 是 landed-red，不洗绿。"""

    node = 'stage4.plan'

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

    def finish_chapter_session(self, chapter: int, *, outcome: str) -> None:
        """一章正式终验后只结束本章 session；兄弟章仍可继续运行。"""
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
                    end_code_version=prov.code_version(),
                    only_if_open=True,
                )
            if client is not None:
                client.finish(
                    process_state=states[0],
                    delivery_state=states[1],
                    error_kind=states[2],
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
    stage5_driver_factory: Stage5DriverFactory | None = None,
    semantic_driver_factory: SemanticDriverFactory | None = None,
    chapters: str | None = None,
    revise: bool = False,
    max_turns: int = 4,
) -> NodeExecutorAgent[BaseModel]:
    factory = EXECUTOR_FACTORIES.get(ctx.node)
    if factory is None:
        raise ValueError(
            f'{ctx.node} 尚未按迁移顺序接入 executor agent；拒绝静默回退 DeepSeek')
    if ctx.node == 'stage2.blueprint':
        if driver is not None:
            raise ValueError(
                'stage2.blueprint 不能让多个章节共享一个 driver；'
                '测试注入请使用 driver_factory')
        if semantic_driver_factory is not None:
            raise ValueError('stage2.blueprint 不接受 semantic_driver_factory')
        if stage5_driver_factory is not None:
            raise ValueError('stage2.blueprint 不接受 stage5_driver_factory')
        if chapters is not None or revise:
            raise ValueError('stage2.blueprint 不接受 Stage5 chapters/revise 参数')
        return factory(
            ctx, driver_factory=driver_factory, max_turns=max_turns)
    if ctx.node == 'stage5.write':
        if driver is not None:
            raise ValueError(
                'stage5.write 不能让多个章节共享一个 driver；'
                '测试注入请使用 stage5_driver_factory')
        if driver_factory is not None or semantic_driver_factory is not None:
            raise ValueError(
                'stage5.write 只接受 stage5_driver_factory，不接受其他 pool factory')
        return factory(
            ctx,
            driver_factory=stage5_driver_factory,
            max_turns=max_turns,
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
        if chapters is not None or revise:
            raise ValueError('stage6.semantic 不接受 Stage5 chapters/revise 参数')
        return factory(
            ctx, driver_factory=semantic_driver_factory, max_turns=max_turns)
    if semantic_driver_factory is not None:
        raise ValueError(f'{ctx.node} 不接受 semantic_driver_factory')
    if driver_factory is not None:
        raise ValueError(f'{ctx.node} 不接受逐章 driver_factory')
    if stage5_driver_factory is not None:
        raise ValueError(f'{ctx.node} 不接受 stage5_driver_factory')
    if chapters is not None or revise:
        raise ValueError(f'{ctx.node} 不接受 Stage5 chapters/revise 参数')
    return factory(ctx, driver=driver, max_turns=max_turns)


__all__ = [
    'BlueprintDriverFactory', 'BlueprintSessionPool', 'EXECUTOR_FACTORIES',
    'NodeAgentResult', 'NodeExecutorAgent', 'Stage2BlueprintAgent',
    'Stage2GlobalAgent', 'Stage4DesignAgent', 'Stage4PlanAgent',
    'Stage5DriverFactory', 'Stage5SessionPool',
    'Stage5WriteAgent', 'SemanticDriverFactory', 'SemanticSessionPool',
    'Stage6HookAgent',
    'Stage6SemanticAgent', 'Stage7RenameAgent', 'build_node_agent',
]
