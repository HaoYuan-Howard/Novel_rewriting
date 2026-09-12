"""stage2.blueprint 当前响应契约。旧事实链留在冻结旧代码，不进入 V10。"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Callable, Literal, Mapping

from pydantic import BaseModel

from contracts.invariants import BLUEPRINT_ARCHITECTURES
from contracts.schema.plot import PlotBlueprintResponse
from novel.stages.stage2.plot_compile import compile_plot_response, response_projection
from novel.stages.stage2.plot_prompts import response_system


@dataclass(frozen=True)
class ContractSpec:
    architecture: str
    system: str
    sys_name: str
    response_model: type[BaseModel]
    schema_mode: Literal['provider_json_schema', 'tool_call', 'prompt_plus_local_validation']
    compile_fn: Callable[..., tuple[dict[str, Any], dict[str, Any]]]
    projection_fn: Callable[[dict[str, Any], str], dict[str, Any]]
    topology: Literal['single']
    prefers_content_over_reasoning_salvage: bool = False


CONTRACTS = {
    'plot-blueprint-v1': ContractSpec(
        architecture='plot-blueprint-v1', system=response_system(),
        sys_name='BLUEPRINT_PLOT_SYS', response_model=PlotBlueprintResponse,
        schema_mode='prompt_plus_local_validation',
        compile_fn=compile_plot_response, projection_fn=response_projection,
        topology='single'),
}
assert set(CONTRACTS) == set(BLUEPRINT_ARCHITECTURES)


def active_contract(env: Mapping[str, str] | None = None) -> ContractSpec:
    from novel.config import load_config
    return CONTRACTS[load_config(env=env).stage2_blueprint_contract]


def contract_for(architecture: str) -> ContractSpec:
    if architecture not in CONTRACTS:
        raise KeyError(f'当前不支持 Blueprint 架构 {architecture!r}，旧数据请使用冻结旧代码')
    return CONTRACTS[architecture]
