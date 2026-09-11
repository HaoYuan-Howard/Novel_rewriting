"""Blueprint 审阅的 provider schema 与确定性组装；正式动作/产物形状不变。

整轮 actor/reason 只填写一次，关联提交由运行代码引用同一值；ruling 将接受与
各 owner 的返工字段分成闭集，禁止“接受同时派修”的矛盾动作。稀疏补丁的缺席字段不能
被公共 strict schema 强制补成 null：producer 依赖 model_fields_set 区分缺席与
显式改值。因此只为这两个小型补丁展开原契约允许的字段组合（16 + 8 种），
每种组合内部全部 required，保留原稀疏 JSON 的语义，不另造宽松载体或猜测改值。
"""
from __future__ import annotations

import hashlib
import copy
from collections.abc import Mapping
from functools import reduce
from itertools import combinations
from operator import or_
from typing import Annotated, Any, Literal

from pydantic import Field, ValidationError, create_model

from contracts.schema.api import Stage2NodeReviewRequest
from contracts.schema.base import StrictModel
from contracts.schema.common import Sha256
from contracts.schema.knowledge_links import KnowledgeLinkSubmission
from contracts.schema.stage2 import (
    BlueprintBeatPatch, BlueprintFactPatch, BlueprintRelationChapterEdit,
    BlueprintRelationEdit,
)
from novel.agents.session_client import agent_json_schema
from novel.orchestrator import queue


WIRE_SCHEMA = 'novel-stage2-blueprint-reviewer-v1'
_FULL_SCHEMA_SHA256 = '36ed2f78221cf9d9d8b2b7ff1a6544672de900d85ac2575382e580587b44abfd'


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
    common = {key: (info.rebuild_annotation(), ...)
              for key, info in Stage2NodeReviewRequest.model_fields.items()
              if key not in {'node', 'decision', 'owner', 'chapter_ordinals', 'knowledge_links'}}
    links = create_model(
        'BlueprintKnowledgeDecisions', __base__=StrictModel,
        **{key: (info.rebuild_annotation(), ...)
           for key, info in KnowledgeLinkSubmission.model_fields.items() if key not in {'actor', 'reason'}})
    non_rework = create_model(
        'BlueprintNonReworkRuling', __base__=StrictModel,
        decision=(Literal['accept', 'uncertain', 'stop'], ...))
    book_rework = create_model(
        'BlueprintBookReworkRuling', __base__=StrictModel,
        decision=(Literal['rework'], ...), owner=(Literal['stage2.characters', 'stage2.global'], ...))
    chapter_rework = create_model(
        'BlueprintChapterReworkRuling', __base__=StrictModel,
        decision=(Literal['rework'], ...), owner=(Literal['stage2.blueprint'], ...),
        chapter_ordinals=(list[Annotated[int, Field(ge=1)]], Field(min_length=1)))
    payload = create_model(
        'BlueprintReviewPayload', __base__=StrictModel, **common,
        node=(Literal['stage2.blueprint'], ...), knowledge_links=(links, ...),
        ruling=(non_rework | book_rework | chapter_rework, ...))
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
        schema_=(Literal[WIRE_SCHEMA], Field(..., alias='schema')),
        surface_seal=(Sha256, ...),
        waiting_on=(Literal[queue.WAITING_ON_STAGE2_BLUEPRINT_REVIEW], ...),
        action=(Annotated[approval | patch, Field(discriminator='kind')], ...))


def response_schema() -> dict[str, Any]:
    return agent_json_schema(response_model())


def prompt_addendum() -> str:
    return f'''【Blueprint 的唯一运输格式】
最终只输出 {WIRE_SCHEMA} JSON；其余 wrapper、node/patch、绑定与材料沿用 immutable workpack。
approval.payload 用 ruling 替代 decision/owner/chapter_ordinals：
接受、不确定或停止只交 {{"decision":"accept|uncertain|stop"}}；
按书返工交 {{"decision":"rework","owner":"stage2.characters|stage2.global"}}；
Blueprint 定向返工交 {{"decision":"rework","owner":"stage2.blueprint","chapter_ordinals":[1]}}。
竖线表示互斥取值，不是要输出的文字。返工章号须来自当前正文、升序且唯一。
approval.payload.knowledge_links 只交 scope_sha256 和 decisions，不重复 actor/reason；
Supervisor 从本轮 payload.actor/reason 确定性注入，不能分别写两份理由。
关联 decisions 必须采用原生 schema 的 resolution 分支。所有实质决定由你填写，
Supervisor 不推测接受、返工范围或事实关联，组装后仍经现役 typed/producer 全部检查。'''


def assemble(document: dict[str, Any]) -> dict[str, Any]:
    from novel.orchestrator.reviewer_tasks import REQUEST_SCHEMA

    if document.get('schema') == REQUEST_SCHEMA:
        # 已购的完整动作保持逐字段原样；尤其不能替旧回答清除矛盾的返工参数。
        return copy.deepcopy(document)
    try:
        result = response_model().model_validate(document).model_dump(
            mode='json', by_alias=True, exclude_unset=True)
    except ValidationError as exc:
        from novel.orchestrator.stage2_reviewer_wire import _validation_locations
        raise ValueError('Blueprint 响应不符合当前闭集：'
                         + _validation_locations(exc, model=response_model())) from exc
    result['schema'] = REQUEST_SCHEMA
    if result['action']['kind'] == 'approval':
        payload = result['action']['payload']
        ruling = payload.pop('ruling')
        payload.update(decision=ruling['decision'], owner=ruling.get('owner'),
                       chapter_ordinals=ruling.get('chapter_ordinals', []))
        payload['knowledge_links'].update(actor=payload['actor'], reason=payload['reason'])
    return result


def allows_schema_publication_revalidation(
    request: Mapping[str, Any], schema_sha256: str | None,
) -> bool:
    """登记旧完整动作到当前运输格式的免费重验，不改写旧回答业务值。

    仅接受旧 observation 协议或已发布完整 schema 的固定摘要；其他已下发
    schema 不兼容。旧完整动作仍原样进入现役 loader，不续原供应商会话。
    """
    from novel.commanders import ClaudeCodeCommanderDriver, CodexCommanderDriver
    from novel.commanders.base import _prepare_output_schema

    if 'output_schema' not in request:
        return False
    if request['output_schema'] is not None:
        return (request.get('schema_sha256') == _FULL_SCHEMA_SHA256
                and _prepare_output_schema(request['output_schema'])[2] == _FULL_SCHEMA_SHA256
                and schema_sha256 == _prepare_output_schema(response_schema())[2])
    protocols = (ClaudeCodeCommanderDriver.protocol, CodexCommanderDriver.protocol)
    if request.get('schema_sha256') not in {
            hashlib.sha256(f'commander-observation:{protocol}'.encode('utf-8')).hexdigest()
            for protocol in protocols}:
        return False
    return schema_sha256 == _prepare_output_schema(response_schema())[2]
