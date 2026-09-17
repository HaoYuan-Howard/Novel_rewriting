#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Executor driver / model / effort 组合能力的唯一 registry。

这里同时维护静态组合能力和本机 CLI 可用性。探针只查 PATH 并执行
``<cli> --version``，不传 prompt、不读小说、不发模型请求；``ready`` 因而不表示
账号或模型 entitlement 已验证。
"""
from __future__ import annotations

import shutil
import subprocess
from dataclasses import dataclass
from typing import Callable

from contracts.invariants import (
    AGENT_EXECUTOR_NODES, AgentAvailability, AgentDriver,
    AgentModelEvidence, AgentReasoningEffort, NODES,
)
from contracts.schema.api import (
    AgentCapabilitiesResponse, AgentDriverCapability, AgentModelCapability,
    LegacyModelCapability, LegacyProviderCapability, LegacyThinkingCapability,
)
from contracts.schema.orchestration import (
    AgentExecutorBackend, AgentProfile, ExecutorBackend, LegacyExecutorBackend,
    PipelineAgentProfiles,
)
from novel.errors import UnsupportedRunMode
from novel.llm.providers import provider_specs, validate_legacy_profile
from novel.orchestrator.cli_probe import (
    CliEntryResolutionError, resolve_cli_entry,
)


_PROBE_TIMEOUT_SECONDS = 3.0
_STANDARD_MODE = 'standard'
_AUTH_FAILURE_WORDS = (
    'not logged in', 'login required', 'please login', 'please log in',
    'unauthenticated', 'unauthorized', 'authentication required',
)


@dataclass(frozen=True)
class _ModelSpec:
    id: str
    label: str
    reasoning_efforts: tuple[AgentReasoningEffort, ...]
    execution_modes: tuple[str, ...] = (_STANDARD_MODE,)
    # CLI 的上下文窗口后缀不是另一个模型；仅登记明确等价的回显，不做前缀猜测。
    reported_ids: tuple[str, ...] = ()


@dataclass(frozen=True)
class _DriverSpec:
    id: AgentDriver
    provider: str
    label: str
    executable: str
    models: tuple[_ModelSpec, ...]
    # 只有公开结果协议确实不提供 actual model、且本驱动用受测的单模型
    # 无 fallback argv 强制请求时才登记。None 表示缺 actual 必须关闭式拒绝。
    unreported_model_evidence: AgentModelEvidence | None = None


@dataclass(frozen=True)
class _ProbeResult:
    availability: AgentAvailability
    reason: str
    version: str | None = None


# 完整稳定 id 与人读 label 分开。这里声明的是产品当前真正允许提交的组合；
# CLI 将来新增别名不会自动进入运行面，也不会静默改变旧审计记录的模型身份。
_DRIVERS: tuple[_DriverSpec, ...] = (
    _DriverSpec(
        id='claude-code-glm',
        provider='bigmodel',
        label='Claude Code · 智谱 GLM',
        executable='claude',
        models=(
            _ModelSpec(
                id='glm-5.3-flash[1M]', label='GLM 5.3 Flash · 1M',
                reasoning_efforts=('low', 'high', 'max'),
                reported_ids=('glm-5.3-flash', 'glm-5.3-flash[1m]'),
            ),
        ),
    ),
    _DriverSpec(
        id='claude-code',
        provider='anthropic',
        label='Claude Code',
        executable='claude',
        models=(
            _ModelSpec(
                id='claude-opus-5', label='Claude Opus 5',
                reasoning_efforts=('low', 'medium', 'high', 'xhigh', 'max'),
            ),
            _ModelSpec(
                id='claude-sonnet-5', label='Claude Sonnet 5',
                reasoning_efforts=('low', 'medium', 'high', 'xhigh', 'max'),
            ),
        ),
    ),
    _DriverSpec(
        id='codex',
        provider='openai',
        label='Codex',
        executable='codex',
        models=(
            _ModelSpec(
                id='gpt-5.6-sol', label='GPT-5.6 Sol',
                reasoning_efforts=('none', 'low', 'medium', 'high', 'xhigh', 'max'),
            ),
            _ModelSpec(
                id='gpt-5.6-terra', label='GPT-5.6 Terra',
                reasoning_efforts=('none', 'low', 'medium', 'high', 'xhigh', 'max'),
            ),
        ),
        unreported_model_evidence='driver-enforced',
    ),
)

_DRIVER_BY_ID = {driver.id: driver for driver in _DRIVERS}


class AgentProfileRefused(ValueError):
    """profile 的组合不在 registry 中；不得兜底成另一个模型或档位。"""


def provider_for_driver(driver: AgentDriver) -> str:
    """从唯一 capability registry 返回规范 provider id；不探针、不发 wire。"""
    spec = _DRIVER_BY_ID.get(driver)
    if spec is None:
        raise AgentProfileRefused(f'未知 agent driver {driver!r}')
    return spec.provider


def reported_model_matches(profile: AgentProfile, actual_model: object) -> bool:
    """实际回显只接受原 id 或登记过的上下文后缀等价名；不改写审计值。"""
    spec = _DRIVER_BY_ID.get(profile.driver)
    if spec is None or not isinstance(actual_model, str):
        return False
    model = next((row for row in spec.models if row.id == profile.model), None)
    return model is not None and actual_model in (model.id, *model.reported_ids)


def unreported_model_evidence_for_driver(
        driver: AgentDriver) -> AgentModelEvidence | None:
    """返回 driver 在 actual model 未回显时登记的关闭式证据策略。"""
    spec = _DRIVER_BY_ID.get(driver)
    if spec is None:
        raise AgentProfileRefused(f'未知 agent driver {driver!r}')
    return spec.unreported_model_evidence


def _first_line(stdout: str | None, stderr: str | None) -> str | None:
    for stream in (stdout or '', stderr or ''):
        for line in stream.splitlines():
            if line.strip():
                return line.strip()[:300]
    return None


def _probe_driver(
        spec: _DriverSpec,
        *,
        which: Callable[[str], str | None] | None = None,
        runner: Callable[..., subprocess.CompletedProcess[str]] | None = None,
) -> _ProbeResult:
    which = shutil.which if which is None else which
    runner = subprocess.run if runner is None else runner
    executable = which(spec.executable)
    if executable is None:
        return _ProbeResult(
            availability='missing',
            reason=f'PATH 中未找到 {spec.executable} CLI；没有发模型请求',
        )

    try:
        executable = resolve_cli_entry(spec.executable, executable)
    except CliEntryResolutionError as exc:
        return _ProbeResult(
            availability='unknown',
            reason=f'{spec.label} CLI 入口不可安全执行（{exc}）；没有发模型请求',
        )
    argv = [executable, '--version']
    try:
        completed = runner(
            argv,
            capture_output=True,
            text=True,
            encoding='utf-8',
            errors='replace',
            timeout=_PROBE_TIMEOUT_SECONDS,
            check=False,
        )
    except subprocess.TimeoutExpired:
        return _ProbeResult(
            availability='unknown',
            reason=f'{spec.label} --version 超时；没有发模型请求',
        )
    except OSError as exc:
        return _ProbeResult(
            availability='unknown',
            reason=f'{spec.label} CLI 无法启动（{type(exc).__name__}）；没有发模型请求',
        )

    line = _first_line(completed.stdout, completed.stderr)
    if completed.returncode == 0 and line:
        return _ProbeResult(
            availability='ready', version=line,
            reason='CLI 可执行且版本探针成功；未发模型请求，未验证账号或模型权限',
        )
    if completed.returncode == 0:
        return _ProbeResult(
            availability='unknown',
            reason=f'{spec.label} --version 成功但没有版本文本；没有发模型请求',
        )

    lowered = (line or '').casefold()
    if any(word in lowered for word in _AUTH_FAILURE_WORDS):
        return _ProbeResult(
            availability='unauthenticated',
            reason=f'{spec.label} 版本探针明确报告未登录；没有发模型请求',
            version=None,
        )
    return _ProbeResult(
        availability='unknown',
        reason=f'{spec.label} --version 退出码 {completed.returncode}；没有发模型请求',
    )


def get_agent_capabilities(
        *,
        which: Callable[[str], str | None] | None = None,
        runner: Callable[..., subprocess.CompletedProcess[str]] | None = None,
) -> AgentCapabilitiesResponse:
    """返回静态组合矩阵与当次本机探针结果；不缓存、不发模型请求。"""
    drivers: list[AgentDriverCapability] = []
    for spec in _DRIVERS:
        probed = _probe_driver(spec, which=which, runner=runner)
        drivers.append(AgentDriverCapability(
            id=spec.id,
            provider=spec.provider,
            label=spec.label,
            availability=probed.availability,
            availability_reason=probed.reason,
            version=probed.version,
            models=[
                AgentModelCapability(
                    id=model.id,
                    label=model.label,
                    reasoning_efforts=list(model.reasoning_efforts),
                    execution_modes=list(model.execution_modes),
                )
                for model in spec.models
            ],
        ))
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
        drivers=drivers,
        legacy_providers=legacy_providers,
        executor_nodes=list(AGENT_EXECUTOR_NODES),
        legacy_model_pins={
            node: NODES[node].model
            for node in AGENT_EXECUTOR_NODES
            if NODES[node].model
        },
    )


def validate_profile(profile: AgentProfile) -> AgentProfile:
    """按 driver + model + effort + mode 关闭式校验 executor profile。"""
    driver = _DRIVER_BY_ID.get(profile.driver)
    if driver is None:
        raise AgentProfileRefused(f'未知 agent driver {profile.driver!r}')

    model = next((item for item in driver.models if item.id == profile.model), None)
    if model is None:
        raise AgentProfileRefused(
            f'driver={profile.driver!r} 不支持 model={profile.model!r}；'
            f'可选 {[item.id for item in driver.models]}')
    if profile.reasoning_effort not in model.reasoning_efforts:
        raise AgentProfileRefused(
            f'model={profile.model!r} 不支持 reasoning_effort='
            f'{profile.reasoning_effort!r}；可选 {model.reasoning_efforts}')

    mode = _STANDARD_MODE if profile.execution_mode is None else profile.execution_mode
    if mode not in model.execution_modes:
        raise AgentProfileRefused(
            f'model={profile.model!r} 不支持 execution_mode={mode!r}；'
            f'可选 {model.execution_modes}')
    return AgentProfile(
        driver=profile.driver,
        model=profile.model,
        reasoning_effort=profile.reasoning_effort,
        execution_mode=mode,
    )


def validate_pipeline_profiles(
        profiles: PipelineAgentProfiles) -> PipelineAgentProfiles:
    """一次校验默认 backend 与全部节点覆盖（schema v3，executor-only）。"""
    overrides = {
        node: validate_executor_backend(backend, node=node)
        for node, backend in profiles.executor_overrides.items()
    }
    default_nodes = [
        node for node in AGENT_EXECUTOR_NODES
        if node not in overrides
    ]
    default_backend = profiles.executor_default
    for node in default_nodes:
        default_backend = validate_executor_backend(default_backend, node=node)
    return PipelineAgentProfiles(
        executor_default=default_backend,
        executor_overrides=overrides,
        concurrency=profiles.concurrency,
    )


def validate_executor_backend(
    backend: ExecutorBackend, *, node: str | None = None,
) -> ExecutorBackend:
    """按显式 discriminator 校验 executor；两类 driver 不共享隐式 fallback。"""
    if isinstance(backend, AgentExecutorBackend):
        return AgentExecutorBackend(profile=validate_profile(backend.profile))
    if isinstance(backend, LegacyExecutorBackend):
        try:
            profile = validate_legacy_profile(backend.profile, node=node)
        except UnsupportedRunMode as exc:
            raise AgentProfileRefused(str(exc)) from exc
        return LegacyExecutorBackend(profile=profile)
    raise AgentProfileRefused(
        f'未知 executor backend 类型 {type(backend).__name__}')


__all__ = [
    'AgentProfileRefused',
    'get_agent_capabilities',
    'provider_for_driver',
    'reported_model_matches',
    'unreported_model_evidence_for_driver',
    'validate_executor_backend',
    'validate_pipeline_profiles',
    'validate_profile',
]
