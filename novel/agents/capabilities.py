#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""兼容旧导入；现役能力 registry 位于 orchestrator 中性边界。"""
from novel.orchestrator.executor_capabilities import (
    AgentProfileRefused,
    get_agent_capabilities,
    provider_for_driver,
    reported_model_matches,
    unreported_model_evidence_for_driver,
    validate_executor_backend,
    validate_pipeline_profiles,
    validate_profile,
)

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
