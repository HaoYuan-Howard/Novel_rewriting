"""``GET /api/agent/capabilities``：executor 选择面（C3：仅 legacy providers）。"""
from __future__ import annotations

from fastapi import APIRouter, Response

from contracts.schema.api import AgentCapabilitiesResponse
from novel.orchestrator.executor_capabilities import get_agent_capabilities

router = APIRouter(prefix='/agent')


@router.get('/capabilities', response_model=AgentCapabilitiesResponse)
def agent_capabilities(response: Response) -> AgentCapabilitiesResponse:
    """返回 legacy provider 组合矩阵；不探针 CLI，不发模型请求。路径名保留。"""
    response.headers['Cache-Control'] = 'no-store'
    return get_agent_capabilities()
