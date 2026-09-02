#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""当前 output base 的服务设置；只写 ``_service``，不改小说产物。"""
from __future__ import annotations

from fastapi import APIRouter, Depends, Response

from contracts.schema.api import (
    AgentProfileSettingsUpdate,
    AgentProfileSettingsView,
)
from novel.api.deps import get_base
from novel.orchestrator import agent_settings


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
