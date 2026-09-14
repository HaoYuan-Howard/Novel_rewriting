#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Legacy LLM API executor 的抽象基类、唯一 registry 与工厂。

这里的 ``legacy`` 只表示“由本进程直接调用 LLM API”，不表示无契约的旧代码。
每个 provider 必须独立给出：可选模型/档位、节点适用面、默认配置解析，以及真实
client 构造。这样以后接入 GLM 时只能新增一个完整 driver，不能只换 base URL 后继续
误用 DeepSeek 的 Flash→Pro 恢复链、thinking wire 或固定 tokenizer 预算闸。

密钥、代理、超时、重试与缓存目录不进 ``LegacyLlmProfile``；它们是 provider 的
安全运行配置。profile 只保存会改变模型行为、必须进入历史审计的稳定选择。
"""
from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass
from typing import Final

from contracts.invariants import AGENT_EXECUTOR_NODES, LegacyLlmProvider, NODES
from contracts.schema.orchestration import LegacyExecutorBackend, LegacyLlmProfile
from novel.errors import UnsupportedRunMode


@dataclass(frozen=True)
class LegacyThinkingSpec:
    id: str
    label: str
    reasoning_efforts: tuple[str, ...]


@dataclass(frozen=True)
class LegacyModelSpec:
    id: str
    label: str
    thinking_modes: tuple[LegacyThinkingSpec, ...]
    executor_nodes: tuple[str, ...]


@dataclass(frozen=True)
class LegacyProviderSpec:
    id: LegacyLlmProvider
    label: str
    models: tuple[LegacyModelSpec, ...]


class LegacyLlmDriver(ABC):
    """一个直接 LLM API provider 的完整扩展缝。"""

    @property
    @abstractmethod
    def spec(self) -> LegacyProviderSpec:
        """返回可公开给前端的关闭式能力矩阵；不得探测或发模型请求。"""

    @abstractmethod
    def validate_profile(
        self, profile: LegacyLlmProfile, *, node: str | None = None,
    ) -> LegacyLlmProfile:
        """关闭式校验并返回可冻结的精确快照。"""

    @abstractmethod
    def default_profile(self, *, node: str) -> LegacyLlmProfile:
        """把 provider 的安全运行配置解析成一个节点的显式行为快照。"""

    @abstractmethod
    def create_client(self, ctx, profile: LegacyLlmProfile):
        """构造真实 LLM client；只允许在付费节点即将执行时调用。"""


_DEEPSEEK_THINKING: Final = (
    LegacyThinkingSpec(
        id='on', label='开启思考', reasoning_efforts=('low', 'high', 'max')),
    LegacyThinkingSpec(
        id='off', label='关闭思考', reasoning_efforts=('low',)),
)
_PINNED_PRO_NODES: Final = tuple(
    node for node in AGENT_EXECUTOR_NODES
    if NODES[node].model == 'deepseek-v4-pro'
)


class DeepSeekLegacyDriver(LegacyLlmDriver):
    """DeepSeek 直接 API driver（V4.1 Flash + V4 Pro）。"""

    _SPEC: Final = LegacyProviderSpec(
        id='deepseek',
        label='DeepSeek API',
        models=(
            LegacyModelSpec(
                id='deepseek-flash',
                label='DeepSeek V4.1 Flash',
                thinking_modes=_DEEPSEEK_THINKING,
                executor_nodes=tuple(
                    node for node in AGENT_EXECUTOR_NODES
                    if node not in _PINNED_PRO_NODES),
            ),
            LegacyModelSpec(
                id='deepseek-v4-pro',
                label='DeepSeek V4 Pro',
                thinking_modes=_DEEPSEEK_THINKING,
                executor_nodes=tuple(AGENT_EXECUTOR_NODES),
            ),
        ),
    )

    @property
    def spec(self) -> LegacyProviderSpec:
        return self._SPEC

    def validate_profile(
        self, profile: LegacyLlmProfile, *, node: str | None = None,
    ) -> LegacyLlmProfile:
        if profile.provider != self.spec.id:
            raise UnsupportedRunMode(
                f'DeepSeek driver 不能消费 provider={profile.provider!r}')
        model = next(
            (candidate for candidate in self.spec.models
             if candidate.id == profile.model),
            None,
        )
        if model is None:
            raise UnsupportedRunMode(
                f'legacy provider={self.spec.id!r} 不支持 model={profile.model!r}；'
                f'可选 {[item.id for item in self.spec.models]}')
        thinking = next(
            (candidate for candidate in model.thinking_modes
             if candidate.id == profile.thinking_mode),
            None,
        )
        if thinking is None:
            raise UnsupportedRunMode(
                f'model={profile.model!r} 不支持 thinking_mode='
                f'{profile.thinking_mode!r}；可选 '
                f'{[item.id for item in model.thinking_modes]}')
        if profile.reasoning_effort not in thinking.reasoning_efforts:
            raise UnsupportedRunMode(
                f'model={profile.model!r} / thinking={profile.thinking_mode!r} '
                f'不支持 reasoning_effort={profile.reasoning_effort!r}；'
                f'可选 {list(thinking.reasoning_efforts)}')
        if node is not None and node not in model.executor_nodes:
            pinned = NODES.get(node).model if node in NODES else ''
            reason = (
                f'；该节点产品契约钉死 {pinned}' if pinned else '')
            raise UnsupportedRunMode(
                f'legacy model={profile.model!r} 不支持 executor 节点 {node!r}'
                f'{reason}；请给该节点配置显式覆盖')
        return LegacyLlmProfile.model_validate(profile.model_dump(mode='json'))

    def default_profile(self, *, node: str) -> LegacyLlmProfile:
        from novel.config import load_config

        cfg = load_config()
        thinking = cfg.thinking_mode
        effort = 'low' if thinking == 'off' else (NODES[node].effort or 'high')
        profile = LegacyLlmProfile(
            provider='deepseek',
            model=NODES[node].model or cfg.model,
            reasoning_effort=effort,
            thinking_mode=thinking,
        )
        return self.validate_profile(profile, node=node)

    def create_client(self, ctx, profile: LegacyLlmProfile):
        checked = self.validate_profile(profile, node=ctx.node)
        from novel.config import load_config
        from novel.llm.cache import ResponseCache
        from novel.llm.client import LlmClient
        from novel.llm.transport import DeepSeekTransport

        cfg = load_config()
        return LlmClient(
            ctx,
            DeepSeekTransport(cfg),
            cache=ResponseCache(cfg.cache_dir),
            model=checked.model,
            thinking_mode=checked.thinking_mode,
            effort=checked.reasoning_effort,
            max_retries=cfg.max_retries,
            backoff_base=cfg.backoff_base,
            concurrency=cfg.concurrency_for(ctx.node),
            concurrency_policy=cfg.concurrency_policy,
        )


_DRIVERS: Final[tuple[LegacyLlmDriver, ...]] = (DeepSeekLegacyDriver(),)
_DRIVER_BY_ID: Final = {driver.spec.id: driver for driver in _DRIVERS}


def provider_specs() -> tuple[LegacyProviderSpec, ...]:
    """动态 API 的静态 legacy 能力源；不读密钥、不发请求。"""
    return tuple(driver.spec for driver in _DRIVERS)


def driver_for(provider: LegacyLlmProvider) -> LegacyLlmDriver:
    try:
        return _DRIVER_BY_ID[provider]
    except KeyError:
        raise UnsupportedRunMode(
            f'未知 legacy LLM provider {provider!r}；已登记 {list(_DRIVER_BY_ID)}') from None


def validate_legacy_profile(
    profile: LegacyLlmProfile, *, node: str | None = None,
) -> LegacyLlmProfile:
    return driver_for(profile.provider).validate_profile(profile, node=node)


def default_legacy_backend(*, node: str) -> LegacyExecutorBackend:
    return LegacyExecutorBackend(
        profile=driver_for('deepseek').default_profile(node=node))


def create_legacy_client(ctx, profile: LegacyLlmProfile):
    return driver_for(profile.provider).create_client(ctx, profile)


__all__ = [
    'DeepSeekLegacyDriver', 'LegacyLlmDriver', 'LegacyModelSpec',
    'LegacyProviderSpec', 'LegacyThinkingSpec', 'create_legacy_client',
    'default_legacy_backend', 'driver_for', 'provider_specs',
    'validate_legacy_profile',
]
