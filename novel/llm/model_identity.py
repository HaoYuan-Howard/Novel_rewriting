# -*- coding: utf-8 -*-
"""Agent accepted model 的供应商中立证据判定。

``AgentTurn.model``、session ``actual_model`` 与 ledger ``model`` 始终只表示
CLI/provider 真正回显的 actual；没有回显就分别保留 ``None`` / ``unknown``。
checkpoint 的 ``model`` 表示本次可采用的模型身份：要么由 actual 精确回显证明，
要么由 capability registry 中显式登记的单模型、无 fallback driver 契约证明。

这层不能读取 CLI 私有 rollout，也不能把 requested 倒填进 actual。恢复和提交会
用同一判定重新核 session + ledger，因此只改 checkpoint 自签名不能换掉证据来源。
"""
from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass

from contracts.invariants import AgentDriver, AgentModelEvidence
from contracts.schema.orchestration import AgentProfile
from novel.orchestrator.executor_capabilities import (
    reported_model_matches, unreported_model_evidence_for_driver, validate_profile,
)
from novel.errors import AgentModelIdentityRefused


@dataclass(frozen=True)
class ResolvedAgentModelIdentity:
    """一份可采用模型身份及其没有被伪造的 actual 证据。"""

    model: str
    evidence: AgentModelEvidence
    actual_model: str | None


def _profile_fields(
        profile: AgentProfile | Mapping[str, object],
) -> tuple[AgentDriver, str]:
    try:
        materialized = (
            profile
            if isinstance(profile, AgentProfile)
            else AgentProfile.model_validate(dict(profile))
        )
        resolved = validate_profile(materialized)
    except (TypeError, ValueError) as exc:
        raise AgentModelIdentityRefused(
            'resolved profile 缺 driver/model，不能判定 accepted model',
            loc='profile', kind='invalid_resolved_profile') from exc
    return resolved.driver, resolved.model


def resolve_agent_model_identity(
        profile: AgentProfile | Mapping[str, object],
        actual_model: object,
        *,
        cli_version: object | None = None,
        require_driver_version: bool = False,
) -> ResolvedAgentModelIdentity:
    """由 provider actual 或登记过的 driver 强制请求解析可采用模型身份。"""
    driver, requested_model = _profile_fields(profile)
    if actual_model is not None:
        if not isinstance(actual_model, str) or not actual_model.strip():
            raise AgentModelIdentityRefused(
                'provider actual model 不是非空字符串',
                loc='actual_model', kind='invalid_provider_metadata')
        materialized = profile if isinstance(profile, AgentProfile) else AgentProfile.model_validate(profile)
        if not reported_model_matches(materialized, actual_model):
            raise AgentModelIdentityRefused(
                f'provider actual model={actual_model!r} 与 requested '
                f'model={requested_model!r}（resolved profile）不一致',
                loc='actual_model', kind='provider_metadata_mismatch')
        return ResolvedAgentModelIdentity(
            model=requested_model,
            evidence='provider-reported',
            actual_model=actual_model,
        )

    evidence = unreported_model_evidence_for_driver(driver)
    if evidence != 'driver-enforced':
        raise AgentModelIdentityRefused(
            f'driver={driver!r} 未回显 actual model，且未登记单模型无 fallback 证据',
            loc='actual_model', kind='missing_actual_model')
    if require_driver_version and (
            not isinstance(cli_version, str) or not cli_version.strip()):
        raise AgentModelIdentityRefused(
            f'driver={driver!r} 未回显 actual model，且缺 CLI 版本证据',
            loc='cli_version', kind='missing_cli_version')
    return ResolvedAgentModelIdentity(
        model=requested_model,
        evidence=evidence,
        actual_model=None,
    )


def resolve_ledger_model_identity(
        profile: AgentProfile | Mapping[str, object],
        ledger_model: object,
        *,
        cli_version: object | None = None,
        require_driver_version: bool = False,
) -> ResolvedAgentModelIdentity:
    """读取 CostLedger 的 actual-model 口径；只有精确 ``unknown`` 表示未知。"""
    actual = None if ledger_model == 'unknown' else ledger_model
    return resolve_agent_model_identity(
        profile, actual,
        cli_version=cli_version,
        require_driver_version=require_driver_version,
    )


def validate_checkpoint_model_identity(
        profile: AgentProfile | Mapping[str, object],
        *,
        checkpoint_model: object,
        actual_model: object,
        cli_version: object | None = None,
        actual_is_ledger: bool = False,
        require_driver_version: bool = False,
) -> ResolvedAgentModelIdentity:
    """证明 checkpoint adopted model 与独立 session/ledger actual 证据一致。"""
    resolver = (
        resolve_ledger_model_identity
        if actual_is_ledger else resolve_agent_model_identity
    )
    resolved = resolver(
        profile, actual_model,
        cli_version=cli_version,
        require_driver_version=require_driver_version,
    )
    if checkpoint_model != resolved.model:
        raise AgentModelIdentityRefused(
            f'checkpoint model={checkpoint_model!r} 与证据支持的 '
            f'model={resolved.model!r} 不一致',
            loc='model', kind='checkpoint_model_mismatch')
    return resolved


__all__ = [
    'AgentModelIdentityRefused',
    'ResolvedAgentModelIdentity',
    'resolve_agent_model_identity',
    'resolve_ledger_model_identity',
    'validate_checkpoint_model_identity',
]
