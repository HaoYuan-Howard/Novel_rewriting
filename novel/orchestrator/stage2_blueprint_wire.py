"""Blueprint 完整审阅动作的 provider schema；不改变现役响应或产物形状。

接受与补丁两个出口都复用正式 typed 契约。稀疏 fact/beat patch 的缺席字段不能
被公共 strict schema 强制补成 null：producer 依赖 model_fields_set 区分缺席与
显式改值。因此只为这两个小型补丁展开原契约允许的字段组合（16 + 8 种），
每种组合内部全部 required，保留原稀疏 JSON 的语义，不另造宽松载体或猜测改值。
"""
from __future__ import annotations

import hashlib
from collections.abc import Mapping
from functools import reduce
from itertools import combinations
from operator import or_
from typing import Annotated, Any, Literal

from pydantic import Field, create_model

from contracts.schema.api import Stage2NodeReviewRequest
from contracts.schema.base import StrictModel
from contracts.schema.common import Sha256
from contracts.schema.stage2 import (
    BlueprintBeatPatch, BlueprintFactPatch, BlueprintRelationChapterEdit,
    BlueprintRelationEdit,
)
from novel.agents.session_client import agent_json_schema
from novel.orchestrator import queue


def _sparse_patch(model: type[StrictModel], identity: str) -> Any:
    fields = [name for name in model.model_fields if name != identity]
    variants = []
    for count in range(len(fields) + 1):
        for selected in combinations(fields, count):
            keys = (identity, *selected)
            variants.append(create_model(
                f'{model.__name__}Fields{len(variants)}', __base__=StrictModel,
                **{key: (model.model_fields[key].rebuild_annotation(), ...)
                   for key in keys}))
    return reduce(or_, variants)


def response_model() -> type[StrictModel]:
    # 此模块由 wire 分派及恢复入口按需调用，避免 reviewer_tasks 导入时形成环。
    from novel.orchestrator.reviewer_tasks import REQUEST_SCHEMA

    payload = create_model(
        'BlueprintReviewPayload', __base__=Stage2NodeReviewRequest,
        node=(Literal['stage2.blueprint'], ...))
    approval = create_model(
        'BlueprintApprovalAction', __base__=StrictModel,
        kind=(Literal['approval'], ...), surface=(Literal['stage2_blueprint_review'], ...),
        payload=(payload, ...))
    chapter = create_model(
        'BlueprintChapterPatchWire', __base__=BlueprintRelationChapterEdit,
        fact_patches=(list[_sparse_patch(BlueprintFactPatch, 'fact_id')], ...),
        beat_patches=(list[_sparse_patch(BlueprintBeatPatch, 'beat_id')], ...))
    edit = create_model(
        'BlueprintEditWire', __base__=BlueprintRelationEdit,
        chapters=(list[chapter], Field(min_length=1)))
    patch = create_model(
        'BlueprintPatchAction', __base__=StrictModel,
        kind=(Literal['node'], ...), node=(Literal['stage2.blueprint'], ...),
        flags=(list[Literal['--apply-edit', '@reviewer-input']], Field(min_length=2, max_length=2)),
        input_payload=(edit, ...))
    return create_model(
        'BlueprintReviewerAction', __base__=StrictModel,
        schema_=(Literal[REQUEST_SCHEMA], Field(..., alias='schema')),
        surface_seal=(Sha256, ...),
        waiting_on=(Literal[queue.WAITING_ON_STAGE2_BLUEPRINT_REVIEW], ...),
        action=(Annotated[approval | patch, Field(discriminator='kind')], ...))


def response_schema() -> dict[str, Any]:
    return agent_json_schema(response_model())


def allows_schema_publication_revalidation(
    request: Mapping[str, Any], schema_sha256: str | None,
) -> bool:
    """仅补发原有完整动作的 schema；旧无约束回答仍须过当前 typed 校验。

    旧请求只有 CLI observation 协议摘要，并不代表另一版业务 JSON。登记这一次
    从未下发到完整下发的转换，不兼容其他已下发 schema，也不续原供应商会话。
    """
    from novel.commanders import ClaudeCodeCommanderDriver, CodexCommanderDriver
    from novel.commanders.base import _prepare_output_schema

    if 'output_schema' not in request or request['output_schema'] is not None:
        return False
    protocols = (ClaudeCodeCommanderDriver.protocol, CodexCommanderDriver.protocol)
    if request.get('schema_sha256') not in {
            hashlib.sha256(f'commander-observation:{protocol}'.encode('utf-8')).hexdigest()
            for protocol in protocols}:
        return False
    return schema_sha256 == _prepare_output_schema(response_schema())[2]
