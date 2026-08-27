#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""``GET /api/agent/capabilities``：agent 选择器的动态、只读能力面。"""
from __future__ import annotations

from fastapi import APIRouter, Response

from contracts.schema.api import AgentCapabilitiesResponse
from novel.agent_capabilities import get_agent_capabilities

router = APIRouter(prefix='/agent')


@router.get('/capabilities', response_model=AgentCapabilitiesResponse)
def agent_capabilities(response: Response) -> AgentCapabilitiesResponse:
    """探测本机 CLI/版本并返回服务端 registry；不缓存，也不发模型请求。"""
    response.headers['Cache-Control'] = 'no-store'
    return get_agent_capabilities()
