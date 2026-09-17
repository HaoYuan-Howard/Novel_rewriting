# -*- coding: utf-8 -*-
"""Legacy LLM accepted model 的供应商中立证据判定。

checkpoint 的 ``model`` 表示本次可采用的模型身份：由 provider actual 精确回显
证明。C3 起 Agent CLI ``driver-enforced`` 路径已退役；没有回显就关闭式拒绝。
恢复和提交会用同一判定重新核 session + ledger。
"""
from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass

from contracts.invariants import AgentModelEvidence
from contracts.schema.orchestration import LegacyLlmProfile
from novel.llm.providers import validate_legacy_profile
from novel.errors import AgentModelIdentityRefused


@dataclass(frozen=True)
class ResolvedAgentModelIdentity:
    """一份可采用模型身份及其没有被伪造的 actual 证据。"""

    model: str
    evidence: AgentModelEvidence
    actual_model: str | None


def _resolved_model(profile: LegacyLlmProfile | Mapping[str, object]) -> str:
    try:
        materialized = (
            profile
            if isinstance(profile, LegacyLlmProfile)
            else LegacyLlmProfile.model_validate(dict(profile))
        )
        resolved = validate_legacy_profile(materialized)
    except (TypeError, ValueError) as exc:
        raise AgentModelIdentityRefused(
            'resolved profile 缺 provider/model，不能判定 accepted model',
            loc='profile', kind='invalid_resolved_profile') from exc
    return resolved.model


def resolve_agent_model_identity(
        profile: LegacyLlmProfile | Mapping[str, object],
        actual_model: object,
        *,
        cli_version: object | None = None,
        require_driver_version: bool = False,
) -> ResolvedAgentModelIdentity:
    """由 provider actual 解析可采用模型身份；无回显关闭式拒绝。"""
    del cli_version, require_driver_version
    requested_model = _resolved_model(profile)
    if actual_model is not None:
        if not isinstance(actual_model, str) or not actual_model.strip():
            raise AgentModelIdentityRefused(
                'provider actual model 不是非空字符串',
                loc='actual_model', kind='invalid_provider_metadata')
        if actual_model != requested_model:
            raise AgentModelIdentityRefused(
                f'provider actual model={actual_model!r} 与 requested '
                f'model={requested_model!r}（resolved profile）不一致',
                loc='actual_model', kind='provider_metadata_mismatch')
        return ResolvedAgentModelIdentity(
            model=requested_model,
            evidence='provider-reported',
            actual_model=actual_model,
        )
    raise AgentModelIdentityRefused(
        'legacy provider 未回显 actual model，不能签发 accepted model',
        loc='actual_model', kind='missing_actual_model')


def resolve_ledger_model_identity(
        profile: LegacyLlmProfile | Mapping[str, object],
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
        profile: LegacyLlmProfile | Mapping[str, object],
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
