#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""逐单元 session 池的公共骨架（批 4）。

``node_agents.py`` 里五个池（Stage2 Blueprint / Stage4 Design / Stage4 Plan /
Stage5 / Stage6 Semantic）各自按 phase/purpose/chapter/role 隔离 session；它们的
业务差异（键推导、scope 校验、批次进度、逐章终态幂等、finish 状态机）是真实的，
但下面几件事此前逐字重复了五遍，且已出现分歧（Plan 池没有 provider owner 登记、
Semantic 池的 owner 少了 logical session id）：

- driver 对象只能归一个单元所有（factory 把同一有状态 driver 发给两个单元是最早
  的隔离破口，必须在首个 model start 前拒绝）；
- 同一池只探一次 CLI，之后每个 driver 都必须与 profile/首次探针的厂商身份一致；
- provider session id 与单元/logical session 一对一，crash 恢复也不能偷接旧 id；
- 关闭时持锁快照、逐个关闭并汇总异常，不因第一个失败漏掉后面的进程/cwd。

``UnitSessionPool`` 通过组合提供这些**无业务语义**的生命周期；各 adapter 保留
自己的键、失败集合与报错文案，不把 stage 分支藏进公共类。
"""
from __future__ import annotations

import threading
from collections.abc import Callable, Hashable, Mapping, MutableMapping, Sequence
from typing import Any, Generic, Protocol, TypeVar

from pydantic import BaseModel

from contracts.schema.orchestration import AgentProfile
from novel import agent_session_audit, prov
from novel.agents.base import AgentDriver, AgentProbe
from novel.agents.session_client import (
    SessionAgentClient, ValidatedCheckpointAcceptance,
    validate_checkpoint_acceptance,
)
from novel.errors import ProvenanceRefused
from novel.run import RunContext


K = TypeVar('K', bound=Hashable)
V = TypeVar('V')


def validate_pool_args(
    *, pool_name: str, ctx: RunContext, node: str,
    max_turns: int, concurrency: int, concurrency_cap: int,
    max_turns_cap: int | None = None,
) -> tuple[AgentProfile, int]:
    """五个池共用的构造前置：节点、profile、turn/并发上限。返回 (profile, 生效并发)。"""
    if ctx.node != node:
        raise ValueError(f'{pool_name} 只接受 {node}，实得 {ctx.node!r}')
    if ctx.executor_profile is None:
        raise ValueError(f'{node} executor 缺 resolved profile')
    if (not isinstance(max_turns, int) or isinstance(max_turns, bool)
            or max_turns < 1
            or (max_turns_cap is not None and max_turns > max_turns_cap)):
        bound = (
            f'在 1..{max_turns_cap}' if max_turns_cap is not None else '>= 1')
        raise ValueError(f'{pool_name}.max_turns 必须{bound}')
    if (not isinstance(concurrency, int) or isinstance(concurrency, bool)
            or concurrency < 1):
        raise ValueError(f'{pool_name}.concurrency 必须 >= 1')
    return ctx.executor_profile, min(concurrency_cap, concurrency)


def claim_driver_owner(
    owners: MutableMapping[int, K], driver: AgentDriver, key: K,
) -> K | None:
    """把 driver 对象登记给 ``key``；已属于另一单元时返回那个单元且不改登记。"""
    driver_id = id(driver)
    prior = owners.get(driver_id)
    if prior is not None and prior != key:
        return prior
    owners[driver_id] = key
    return None


def release_driver_owner(
    owners: MutableMapping[int, K], driver: AgentDriver, key: K,
) -> None:
    """只释放确实属于 ``key`` 的登记；别的单元正持有同一对象时不能替它 pop。"""
    driver_id = id(driver)
    if owners.get(driver_id) == key:
        owners.pop(driver_id, None)


def close_driver_quietly(driver: AgentDriver) -> None:
    try:
        driver.close()
    except Exception:  # noqa: BLE001 —— 构造失败路上的清理不再层层抛
        pass


def claim_provider_session_owner(
    owners: MutableMapping[tuple[str, str], V], *,
    driver_name: str, provider_session_id: object, owner: V,
) -> V | None:
    """把 (driver, provider session) 登记给 ``owner``；已属于别人时返回那个 owner。

    ``owner`` 同时带单元键与 logical session id：同单元的 crash 恢复也不能把旧
    provider id 接到新的 logical session 上。空 id 视为“供应商未回显”，不登记。
    """
    if not isinstance(provider_session_id, str) or not provider_session_id:
        return None
    key = (driver_name, provider_session_id)
    prior = owners.get(key)
    if prior is not None and prior != owner:
        return prior
    owners[key] = owner
    return None


class _ClosablePool(Protocol):
    _lock: threading.RLock
    _clients: Mapping[Any, Any]
    _closed: bool


def close_session_clients(pool: _ClosablePool, *, label: str) -> None:
    """持锁快照、置 closed、逐个 close 并汇总异常；幂等。"""
    with pool._lock:
        if pool._closed:
            return
        clients = tuple(pool._clients.values())
        pool._closed = True
    errors: list[BaseException] = []
    for client in clients:
        try:
            client.close()
        except BaseException as exc:  # noqa: BLE001 —— 先关完再报
            errors.append(exc)
    if errors:
        raise BaseExceptionGroup(f'{label} driver/cwd 关闭失败', errors)


FinishState = tuple[str, str, str | None]
DriverReuseMessage = Callable[[K, K], str]
ProviderReuseMessage = Callable[[tuple[K, str], tuple[K, str], str], str]
FinishStateFactory = Callable[
    [K, SessionAgentClient | None, Mapping[str, Any] | None], FinishState,
]
FinishKwargsFactory = Callable[[K], Mapping[str, object]]


class UnitSessionPool(Generic[K]):
    """逐单元 session 池里与 stage 业务无关的公共生命周期。

    adapter 决定单元键、prompt/scope、checkpoint 业务状态和 ended 判因；本类只
    负责 driver/provider owner、一次 probe、client 构造、公共 JSON 调用缝、
    turns 聚合以及“全部访问后再汇总错误”的 finish/close 骨架。
    """

    def __init__(
        self,
        ctx: RunContext,
        *,
        node: str,
        pool_name: str,
        max_turns: int,
        concurrency: int,
        concurrency_cap: int,
        max_turns_cap: int | None = None,
    ) -> None:
        profile, effective_concurrency = validate_pool_args(
            pool_name=pool_name,
            ctx=ctx,
            node=node,
            max_turns=max_turns,
            concurrency=concurrency,
            concurrency_cap=concurrency_cap,
            max_turns_cap=max_turns_cap,
        )
        self.ctx = ctx
        self.profile = profile
        self.model = profile.model
        self.max_turns = max_turns
        self.concurrency = effective_concurrency
        self.pool_name = pool_name
        self._clients: dict[K, SessionAgentClient] = {}
        self._probe: AgentProbe | None = None
        self._driver_owners: dict[int, K] = {}
        self._provider_session_owners: dict[
            tuple[str, str], tuple[K, str],
        ] = {}
        self._isolation_failed_keys: set[K] = set()
        self._driver_setup_failed_keys: set[K] = set()
        self._closed = False
        self._lock = threading.RLock()

    @property
    def clients(self) -> dict[K, SessionAgentClient]:
        return self._clients

    @property
    def lock(self) -> threading.RLock:
        return self._lock

    @property
    def driver_owners(self) -> dict[int, K]:
        return self._driver_owners

    @property
    def provider_session_owners(
        self,
    ) -> dict[tuple[str, str], tuple[K, str]]:
        return self._provider_session_owners

    @property
    def isolation_failed_keys(self) -> set[K]:
        return self._isolation_failed_keys

    @property
    def driver_setup_failed_keys(self) -> set[K]:
        return self._driver_setup_failed_keys

    @property
    def closed(self) -> bool:
        with self._lock:
            return self._closed

    @property
    def driver_failed(self) -> bool:
        with self._lock:
            return any(client.driver_failed for client in self._clients.values())

    @property
    def session_count(self) -> int:
        with self._lock:
            return len(self._clients)

    @property
    def isolation_failed(self) -> bool:
        with self._lock:
            return bool(self._isolation_failed_keys)

    def turns_used(
        self,
        restored: Mapping[K, Mapping[str, Any]] | None = None,
    ) -> int:
        """聚合 live turns；无 live client 的 durable acceptance 可显式补入。"""
        restored = restored or {}
        with self._lock:
            clients = dict(self._clients)
        total = sum(client.turns_used for client in clients.values())
        for key, record in restored.items():
            if key in clients:
                continue
            turn = record.get('accepted_turn_index')
            if isinstance(turn, int) and not isinstance(turn, bool):
                total += turn
        return total

    def new_client(
        self,
        key: K,
        *,
        driver_factory: Callable[[], AgentDriver],
        chapter: int | None,
        label: str,
        probe_driver: bool = True,
        driver_reuse_message: DriverReuseMessage[K] | None = None,
    ) -> SessionAgentClient:
        """构造一条私有 client，并在 model start 前封住共享/漂移 driver。"""
        with self._lock:
            if self._closed:
                raise ProvenanceRefused(f'{self.pool_name} 已关闭')
            try:
                driver = driver_factory()
            except BaseException:
                self._driver_setup_failed_keys.add(key)
                raise
            prior = claim_driver_owner(self._driver_owners, driver, key)
            if prior is not None:
                self._isolation_failed_keys.update((prior, key))
                message = (
                    driver_reuse_message(prior, key)
                    if driver_reuse_message is not None
                    else f'{label}: driver factory 把同一对象同时给了 {prior}/{key}'
                )
                # ``prior`` 的 live client 仍拥有这只对象；这里不能替它 close。
                raise ProvenanceRefused(message)
            try:
                if not probe_driver:
                    if (driver.driver != self.profile.driver
                            or (self._probe is not None
                                and (driver.driver != self._probe.driver
                                     or driver.provider != self._probe.provider))):
                        self._isolation_failed_keys.add(key)
                        raise ProvenanceRefused(
                            f'{label}: checkpoint 恢复用 driver 与 profile/pool 漂移')
                elif self._probe is None:
                    try:
                        probe = driver.probe()
                    except BaseException:
                        self._driver_setup_failed_keys.add(key)
                        raise
                    if not probe.available:
                        self._driver_setup_failed_keys.add(key)
                        raise ProvenanceRefused(
                            f'{label}: executor driver 本地探针不可用：'
                            f'{probe.detail or "unknown"}')
                    if (driver.driver != self.profile.driver
                            or probe.driver != self.profile.driver
                            or probe.provider != driver.provider):
                        self._isolation_failed_keys.add(key)
                        raise ProvenanceRefused(
                            f'{label}: driver/probe/profile 身份不闭合：'
                            f'profile={self.profile.driver!r}, '
                            f'driver={driver.driver!r}, '
                            f'probe={probe.driver!r}/{probe.provider!r}')
                    self._probe = probe
                elif (driver.driver != self._probe.driver
                      or driver.provider != self._probe.provider):
                    self._isolation_failed_keys.add(key)
                    raise ProvenanceRefused(
                        f'{label}: driver factory 在同一 pool 内漂移了厂商身份')
                return SessionAgentClient(
                    self.ctx,
                    self.profile,
                    driver,
                    chapter=chapter,
                    parent_run_id=self.ctx.run_id,
                    max_semantic_turns=self.max_turns,
                    preflight_probe=self._probe,
                )
            except BaseException:
                if (key not in self._isolation_failed_keys
                        and key not in self._driver_setup_failed_keys):
                    self._driver_setup_failed_keys.add(key)
                release_driver_owner(self._driver_owners, driver, key)
                close_driver_quietly(driver)
                raise

    def detach_client(self, key: K) -> SessionAgentClient | None:
        """从池中摘下一条 client，并释放其 driver 对象登记；不负责 close。"""
        with self._lock:
            client = self._clients.pop(key, None)
            if client is not None:
                release_driver_owner(self._driver_owners, client.driver, key)
            return client

    def register_provider_owner(
        self,
        key: K,
        *,
        logical_session_id: object,
        provider_session_id: object,
        provider_reuse_message: ProviderReuseMessage[K] | None = None,
    ) -> None:
        """把 provider id 同时绑定单元键与 logical session，拒绝跨恢复偷接。"""
        if not isinstance(provider_session_id, str) or not provider_session_id:
            return
        if not isinstance(logical_session_id, str) or not logical_session_id:
            raise ProvenanceRefused(
                f'{self.pool_name}/{key}: provider session 缺 logical session owner')
        owner = (key, logical_session_id)
        with self._lock:
            prior = claim_provider_session_owner(
                self._provider_session_owners,
                driver_name=self.profile.driver,
                provider_session_id=provider_session_id,
                owner=owner,
            )
            if prior is None:
                return
            self._isolation_failed_keys.update((prior[0], key))
        message = (
            provider_reuse_message(prior, owner, provider_session_id)
            if provider_reuse_message is not None
            else (
                f'{self.pool_name}: provider session {provider_session_id!r} '
                f'同时属于 {prior[0]}/{key} 或两个 logical sessions')
        )
        raise ProvenanceRefused(message)

    @staticmethod
    def validate_json_request(
        kwargs: dict[str, Any],
        *,
        model_error: str,
        purpose_error: str,
        expected_model_cls: type[BaseModel] | None = None,
        exact_model_cls: bool = False,
    ) -> tuple[type[BaseModel], str]:
        """取出五类 adapter 共用的 ``model_cls`` / ``purpose`` 前置身份。"""
        model_cls = kwargs.pop('model_cls', None)
        valid_model = (
            isinstance(model_cls, type)
            and issubclass(model_cls, BaseModel)
        )
        if expected_model_cls is not None:
            valid_model = valid_model and (
                model_cls is expected_model_cls
                if exact_model_cls else issubclass(model_cls, expected_model_cls)
            )
        if not valid_model:
            raise ProvenanceRefused(model_error)
        purpose = str(kwargs.get('purpose') or '')
        if not purpose:
            raise ProvenanceRefused(purpose_error)
        return model_cls, purpose

    def ask_json(
        self,
        key: K,
        client: SessionAgentClient,
        system: str,
        user: str,
        *,
        model_cls: type[BaseModel],
        kwargs: Mapping[str, Any],
        provider_reuse_message: ProviderReuseMessage[K] | None = None,
        group_wire_and_isolation_failures: bool = False,
    ) -> Any:
        """调用一轮并始终登记观测到的 provider owner；双失败不互相覆盖。"""
        primary: BaseException | None = None
        result: Any = None
        try:
            result = client.ask_json(
                system, user, model_cls=model_cls, **dict(kwargs))
        except BaseException as exc:
            primary = exc
        isolation: BaseException | None = None
        try:
            self.register_provider_owner(
                key,
                logical_session_id=client.session_run_id,
                provider_session_id=client.observed_provider_session_id,
                provider_reuse_message=provider_reuse_message,
            )
        except BaseException as exc:
            isolation = exc
        if (primary is not None and isolation is not None
                and group_wire_and_isolation_failures):
            raise BaseExceptionGroup(
                f'{self.pool_name}/{key}: wire 与 provider 隔离检查同时失败',
                [primary, isolation],
            )
        if isolation is not None:
            raise isolation
        if primary is not None:
            raise primary
        return result

    def validate_checkpoint_acceptance(
        self,
        *,
        chapter: int | None,
        purpose: str,
        record: Mapping[str, Any],
        system: str,
        user: str,
        max_tokens: int,
        recovery_policy: str,
    ) -> ValidatedCheckpointAcceptance:
        """复用 SessionAgentClient 的完整恢复守卫；不改变 adapter 业务索引。"""
        return validate_checkpoint_acceptance(
            self.ctx,
            self.profile,
            chapter=chapter,
            max_semantic_turns=self.max_turns,
            purpose=purpose,
            record=record,
            system=system,
            user=user,
            max_tokens=max_tokens,
            recovery_policy=recovery_policy,
        )

    def finish_rows(
        self,
        keys: Sequence[K],
        *,
        restored_records: Mapping[K, Mapping[str, Any]],
        state_for: FinishStateFactory[K],
        finish_kwargs_for: FinishKwargsFactory[K] | None = None,
    ) -> tuple[BaseException, ...]:
        """访问每条 live/restored session 后返回错误；adapter 决定何时汇总抛出。"""
        with self._lock:
            clients = dict(self._clients)
        errors: list[BaseException] = []
        seen: set[K] = set()
        for key in keys:
            if key in seen:
                errors.append(ProvenanceRefused(
                    f'{self.pool_name} finish keys 重复：{key!r}'))
                continue
            seen.add(key)
            client = clients.get(key)
            record = restored_records.get(key)
            if client is None and record is None:
                continue
            try:
                extra = (
                    dict(finish_kwargs_for(key))
                    if finish_kwargs_for is not None else {}
                )
            except BaseException as exc:
                errors.append(exc)
                continue
            if record is not None:
                try:
                    process_state, delivery_state, error_kind = state_for(
                        key, None, record)
                    agent_session_audit.finish_restored_session(
                        self.ctx.novel_dir.parent,
                        record,
                        process_state=process_state,
                        delivery_state=delivery_state,
                        error_kind=error_kind,
                        end_code_version=prov.code_version(),
                        only_if_open=True,
                        **extra,
                    )
                except BaseException as exc:
                    errors.append(exc)
            if client is not None:
                try:
                    process_state, delivery_state, error_kind = state_for(
                        key, client, None)
                    client.finish(
                        process_state=process_state,
                        delivery_state=delivery_state,
                        error_kind=error_kind,
                        **extra,
                    )
                except BaseException as exc:
                    errors.append(exc)
        return tuple(errors)

    def finish_all(
        self,
        keys: Sequence[K],
        *,
        restored_records: Mapping[K, Mapping[str, Any]],
        state_for: FinishStateFactory[K],
        label: str,
        finish_kwargs_for: FinishKwargsFactory[K] | None = None,
        initial_errors: Sequence[BaseException] = (),
    ) -> None:
        errors = [*initial_errors, *self.finish_rows(
            keys,
            restored_records=restored_records,
            state_for=state_for,
            finish_kwargs_for=finish_kwargs_for,
        )]
        if errors:
            raise BaseExceptionGroup(label, errors)

    def close(self, *, label: str) -> None:
        close_session_clients(self, label=label)


__all__ = [
    'UnitSessionPool',
]
