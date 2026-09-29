#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""当前 output base 的服务设置；只写 ``_service``，不改小说产物。"""
from __future__ import annotations

from fastapi import APIRouter, Depends, Response

from contracts.schema.api import (
    AgentProfileSettingsUpdate,
    AgentProfileSettingsView,
    NetworkSettingsUpdate,
    NetworkSettingsView,
)
from novel.api.deps import get_base
from novel.orchestrator import agent_settings, network_settings


router = APIRouter(prefix='/settings')


@router.get('/agent-profiles', response_model=AgentProfileSettingsView)
def get_agent_profiles(
    response: Response,
    base: str = Depends(get_base),
) -> AgentProfileSettingsView:
    response.headers['Cache-Control'] = 'no-store'
    return agent_settings.read(base)


@router.post('/agent-profiles', response_model=AgentProfileSettingsView)
def save_agent_profiles(
    request: AgentProfileSettingsUpdate,
    base: str = Depends(get_base),
) -> AgentProfileSettingsView:
    return agent_settings.update(
        base,
        profiles=request.profiles,
        expected_revision=request.expected_revision,
    )


@router.get('/network', response_model=NetworkSettingsView)
def get_network(
    response: Response,
    base: str = Depends(get_base),
) -> NetworkSettingsView:
    response.headers['Cache-Control'] = 'no-store'
    return network_settings.read(base)


@router.post('/network', response_model=NetworkSettingsView)
def save_network(
    request: NetworkSettingsUpdate,
    base: str = Depends(get_base),
) -> NetworkSettingsView:
    return network_settings.update(
        base,
        network=request.network,
        expected_revision=request.expected_revision,
    )
