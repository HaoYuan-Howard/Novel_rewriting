#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
agent_capabilities.py —— agent driver / model / effort 组合能力的唯一 registry。

【为什么不放进 ``/api/enums``】这里既有静态组合能力，也有每台机器都不同的 CLI
可用性。把后者混进永久缓存的平面枚举，前端会把「昨天没装」当成今天仍没装；把
model 与 effort 拆成两张枚举，又会允许浏览器拼出 registry 从未支持过的笛卡尔积。

【探针边界】只查 PATH 并执行 ``<cli> --version``，不传 prompt、不读小说、不给账号
发任何模型请求。``ready`` 因此只表示 CLI 可执行且版本探针成功，不替用户声称登录态
或某个模型 entitlement 已验证。
"""
from __future__ import annotations

import shutil
import subprocess
from dataclasses import dataclass
from typing import Callable

from contracts.invariants import (
    AGENT_EXECUTOR_NODES, AgentAvailability, AgentDriver,
    AgentModelEvidence, AgentReasoningEffort, AgentRole,
)
from contracts.schema.api import (
    AgentCapabilitiesResponse, AgentDriverCapability, AgentModelCapability,
    LegacyModelCapability, LegacyProviderCapability, LegacyThinkingCapability,
)
from contracts.schema.orchestration import (
    AgentExecutorBackend, AgentProfile, ExecutorBackend, LegacyExecutorBackend,
    PipelineAgentProfiles,
)
from novel.agents.base import AgentConfigurationError, resolve_cli_entry
from novel.errors import UnsupportedRunMode
from novel.llm.providers import provider_specs, validate_legacy_profile


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


@dataclass(frozen=True)
class _DriverSpec:
    id: AgentDriver
    provider: str
    label: str
    executable: str
    roles: tuple[AgentRole, ...]
    models: tuple[_ModelSpec, ...]
    # 只有公开结果协议确实不提供 actual model、且本驱动用受测的单模型
    # 无 fallback argv 强制请求时才登记。None 表示缺 actual 必须关闭式拒绝。
    unreported_model_evidence: AgentModelEvidence | None = None


@dataclass(frozen=True)
class _ProbeResult:
    availability: AgentAvailability
    reason: str
    version: str | None = None


#: 完整稳定 id 与人读 label 分开。这里声明的是产品当前真正允许提交的组合；
#: CLI 将来新增别名不会自动进入运行面，也不会静默改变旧审计记录的模型身份。
_DRIVERS: tuple[_DriverSpec, ...] = (
    _DriverSpec(
        id='claude-code',
        provider='anthropic',
        label='Claude Code',
        executable='claude',
        roles=('commander', 'executor'),
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
        roles=('commander', 'executor'),
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
        # Codex ``exec --json`` 当前只回 thread/turn/usage，不回实际模型；
        # ``CodexDriver._argv`` 同时显式传唯一 ``--model``、strict config，且
        # 没有 model chain/fallback。actual 仍在 session/ledger 中保持 unknown。
        unreported_model_evidence='driver-enforced',
    ),
)

_DRIVER_BY_ID = {driver.id: driver for driver in _DRIVERS}


class AgentProfileRefused(ValueError):
    """profile 的组合不在 registry 中；不得兜底成另一个模型或档位。"""


def provider_for_driver(driver: AgentDriver) -> str:
    """从唯一 capability registry 返回规范 provider id；不探针、不发 wire。"""
    spec = _DRIVER_BY_ID.get(driver)
    if spec is None:  # Pydantic 正常入口已会挡住；保留给内部关闭式调用。
        raise AgentProfileRefused(f'未知 agent driver {driver!r}')
    return spec.provider


def unreported_model_evidence_for_driver(
        driver: AgentDriver) -> AgentModelEvidence | None:
    """返回 driver 在 actual model 未回显时登记的关闭式证据策略。

    这是运行、恢复与提交验证共用的唯一判定口；调用方不得按模型名前缀或
    provider 猜测。未登记 driver 或未登记策略都不能接受 unknown actual。
    """
    spec = _DRIVER_BY_ID.get(driver)
    if spec is None:
        raise AgentProfileRefused(f'未知 agent driver {driver!r}')
    return spec.unreported_model_evidence


def _first_line(stdout: str | None, stderr: str | None) -> str | None:
    for stream in (stdout or '', stderr or ''):
        for line in stream.splitlines():
            if line.strip():
                # CLI 版本行是短文本；截断异常长输出，避免把诊断页整个送进 API。
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
        # capability 只能把 runtime 真能直接拉起的入口发布为 ready。
        # 不再单独经 cmd.exe 宽放版本探针，否则无法解析的 shim
        # 会在页面可选，却在真正 start 前必然失败。
        executable = resolve_cli_entry(spec.executable, executable)
    except AgentConfigurationError as exc:
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
    """返回静态组合矩阵与**当次**本机探针结果；不缓存、不发模型请求。"""
    drivers: list[AgentDriverCapability] = []
    for spec in _DRIVERS:
        probed = _probe_driver(spec, which=which, runner=runner)
        drivers.append(AgentDriverCapability(
            id=spec.id,
            provider=spec.provider,
            label=spec.label,
            roles=list(spec.roles),
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
    )


def validate_profile(profile: AgentProfile, *, role: AgentRole) -> AgentProfile:
    """按 role + driver + model + effort + mode 关闭式校验一份 profile。

    返回**已物化**的新对象；任何未知组合都响亮拒绝，绝不映射到
    相近档位。``execution_mode=None`` 按当前 ``standard`` 校验后显式写回，
    使 argv/provenance 快照不依赖未来 registry 对 None 的解释。
    """
    driver = _DRIVER_BY_ID.get(profile.driver)
    if driver is None:  # Pydantic 正常入口已会挡住；保留给 model_construct/内部调用。
        raise AgentProfileRefused(f'未知 agent driver {profile.driver!r}')
    if role not in driver.roles:
        raise AgentProfileRefused(
            f'driver={profile.driver!r} 不支持 role={role!r}；可选 {driver.roles}')

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
    """一次校验 commander、默认 backend 与全部节点覆盖。

    默认 backend 必须逐个通过所有未覆盖节点；否则一份 Flash 默认会在
    Stage4/Stage6 的 Pro 钉死节点直到真跑才失败。设置保存和 preview 就应把这类
    半份配置拦住，并明确要求节点覆盖。
    """
    commander = validate_profile(profiles.commander, role='commander')
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
        commander=commander,
        executor_default=default_backend,
        executor_overrides=overrides,
    )


def validate_executor_backend(
    backend: ExecutorBackend, *, node: str | None = None,
) -> ExecutorBackend:
    """按显式 discriminator 校验 executor；两类 driver 不共享隐式 fallback。"""
    if isinstance(backend, AgentExecutorBackend):
        return AgentExecutorBackend(
            profile=validate_profile(backend.profile, role='executor'))
    if isinstance(backend, LegacyExecutorBackend):
        try:
            profile = validate_legacy_profile(backend.profile, node=node)
        except UnsupportedRunMode as exc:
            raise AgentProfileRefused(str(exc)) from exc
        return LegacyExecutorBackend(profile=profile)
    raise AgentProfileRefused(
        f'未知 executor backend 类型 {type(backend).__name__}')
