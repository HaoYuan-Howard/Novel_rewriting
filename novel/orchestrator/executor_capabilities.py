#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""直接 LLM executor 组合能力的唯一 registry（C3：无 Agent CLI）。

``GET /api/agent/capabilities`` 路径保留，只发布 legacy providers 面。
wire ``kind=legacy`` 在解析层物化为 ``DirectLlmExecutor``。
"""
from __future__ import annotations

from dataclasses import dataclass

from contracts.invariants import LLM_EXECUTOR_NODES, NODES
from contracts.schema.api import (
    AgentCapabilitiesResponse,
    LegacyModelCapability, LegacyProviderCapability, LegacyThinkingCapability,
)
from contracts.schema.orchestration import (
    ExecutorBackend, HistoricalAgentBackend, LegacyExecutorBackend,
    LegacyLlmProfile, PipelineAgentProfiles,
)
from novel.errors import UnsupportedRunMode
from novel.llm.providers import provider_specs, validate_legacy_profile
from novel.orchestrator import retired_formats


class AgentProfileRefused(ValueError):
    """profile 的组合不在 registry 中；不得兜底成另一个模型或档位。

    类型名暂留（settings / API 错误映射仍引用）；语义已是任意 executor 配置拒收。
    """


@dataclass(frozen=True)
class DirectLlmExecutor:
    """运行时唯一 executor；wire ``kind=legacy`` 解析为本对象。"""

    profile: LegacyLlmProfile


def get_agent_capabilities() -> AgentCapabilitiesResponse:
    """返回 legacy provider 组合矩阵；不探针 CLI、不发模型请求。"""
    legacy_providers = [
        LegacyProviderCapability(
            id=provider.id,
            label=provider.label,
            models=[
                LegacyModelCapability(
                    id=model.id,
                    label=model.label,
                    thinking_modes=[
                        LegacyThinkingCapability(
                            id=mode.id,
                            label=mode.label,
                            reasoning_efforts=list(mode.reasoning_efforts),
                        )
                        for mode in model.thinking_modes
                    ],
                    executor_nodes=list(model.executor_nodes),
                )
                for model in provider.models
            ],
        )
        for provider in provider_specs()
    ]
    return AgentCapabilitiesResponse(
        legacy_providers=legacy_providers,
        executor_nodes=list(LLM_EXECUTOR_NODES),
        legacy_model_pins={
            node: NODES[node].model
            for node in LLM_EXECUTOR_NODES
            if NODES[node].model
        },
    )


def validate_pipeline_profiles(
        profiles: PipelineAgentProfiles) -> PipelineAgentProfiles:
    """一次校验默认 backend 与全部节点覆盖（schema v4，仅 legacy）。"""
    retired_formats.refuse_agent_profiles('pipeline profiles', profiles)
    overrides = {
        node: validate_executor_backend(backend, node=node)
        for node, backend in profiles.executor_overrides.items()
    }
    default_nodes = [
        node for node in LLM_EXECUTOR_NODES
        if node not in overrides
    ]
    default_backend = profiles.executor_default
    for node in default_nodes:
        default_backend = validate_executor_backend(default_backend, node=node)
    return PipelineAgentProfiles(
        schema_version=4,
        executor_default=default_backend,
        executor_overrides=overrides,
        concurrency=profiles.concurrency,
    )


def validate_executor_backend(
    backend: ExecutorBackend | HistoricalAgentBackend,
    *,
    node: str | None = None,
) -> LegacyExecutorBackend:
    """按显式 discriminator 校验；agent wire 关闭式拒绝，不共享隐式 fallback。"""
    retired_formats.refuse_agent_backend('executor backend', backend)
    if isinstance(backend, LegacyExecutorBackend):
        try:
            profile = validate_legacy_profile(backend.profile, node=node)
        except UnsupportedRunMode as exc:
            raise AgentProfileRefused(str(exc)) from exc
        return LegacyExecutorBackend(profile=profile)
    raise AgentProfileRefused(
        f'未知 executor backend 类型 {type(backend).__name__}')


def resolve_direct_llm_executor(
    backend: ExecutorBackend, *, node: str | None = None,
) -> DirectLlmExecutor:
    """wire legacy → 运行时 ``DirectLlmExecutor``。"""
    validated = validate_executor_backend(backend, node=node)
    return DirectLlmExecutor(profile=validated.profile)


__all__ = [
    'AgentProfileRefused',
    'DirectLlmExecutor',
    'get_agent_capabilities',
    'resolve_direct_llm_executor',
    'validate_executor_backend',
    'validate_pipeline_profiles',
]
