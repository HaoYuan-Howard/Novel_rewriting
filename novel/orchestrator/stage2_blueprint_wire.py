"""范围审阅的关闭式运输契约；只省去代码可注入的重复身份和理由。

完整语义仍由审阅者输出五维结论与实际问题。运输组装不推测接受、不拆分剧情，
旧逐事实响应不自动升级为新语义审批。
"""
from __future__ import annotations

import copy
from collections.abc import Mapping
from typing import Annotated, Any, Literal

from pydantic import Field, ValidationError, create_model

from contracts.schema.api import Stage2NodeReviewRequest
from contracts.schema.base import StrictModel
from contracts.schema.common import Sha256
from contracts.schema.knowledge_links import KnowledgeLinkDecision
from contracts.schema.plot_review import ReviewScope, PlotReviewResponse
from novel.agents.session_client import agent_json_schema
from novel.orchestrator import queue

WIRE_SCHEMA = 'novel-stage2-blueprint-reviewer-v2'


def response_model() -> type[StrictModel]:
    links = create_model('BlueprintKnowledgeDecisionsV2', __base__=StrictModel,
        scope_sha256=(Sha256, ...), decisions=(list[KnowledgeLinkDecision], ...))
    non_rework = create_model('BlueprintNonReworkRulingV2', __base__=StrictModel,
        decision=(Literal['accept', 'uncertain', 'stop'], ...))
    book_rework = create_model('BlueprintBookReworkRulingV2', __base__=StrictModel,
        decision=(Literal['rework'], ...),
        owner=(Literal['stage2.characters', 'stage2.global'], ...))
    chapter_rework = create_model('BlueprintChapterReworkRulingV2', __base__=StrictModel,
        decision=(Literal['rework'], ...), owner=(Literal['stage2.blueprint'], ...),
        chapter_ordinals=(list[Annotated[int, Field(ge=1)]], Field(min_length=1)))
    payload = create_model('BlueprintReviewPayloadV2', __base__=StrictModel,
        node=(Literal['stage2.blueprint'], ...), review_token=(str, ...),
        scope=(ReviewScope, ...), actor=(str, ...), plot_review=(PlotReviewResponse, ...),
        knowledge_links=(links, ...),
        ruling=(non_rework | book_rework | chapter_rework, ...))
    action = create_model('BlueprintApprovalActionV2', __base__=StrictModel,
        kind=(Literal['approval'], ...), surface=(Literal['stage2_blueprint_review'], ...),
        payload=(payload, ...))
    return create_model('BlueprintReviewerActionV2', __base__=StrictModel,
        schema_=(Literal[WIRE_SCHEMA], Field(..., alias='schema')),
        surface_seal=(Sha256, ...),
        waiting_on=(Literal[queue.WAITING_ON_STAGE2_BLUEPRINT_REVIEW], ...),
        action=(action, ...))


def response_schema() -> dict[str, Any]:
    return agent_json_schema(response_model())


def prompt_addendum() -> str:
    return f'''【Blueprint 范围审阅运输格式】
最终只输出 {WIRE_SCHEMA} JSON。scope、token、actor 沿用 exact_request。
payload.ruling 替代外层 decision/owner/chapter_ordinals：
接受、不确定、停止分别只交对应 decision；返修必须有 owner，Blueprint 返修还须列章。
plot_review 是完整五维裁决，reason 只在这里填写一次。
knowledge_links 只交 scope_sha256 和 decisions。代码将 plot_review.reason 注入本轮
reason 与 knowledge_links.reason，将 actor 注入关联；不会自动补任何语义结论。
原子事实补丁、逐节点或逐 delivery 正向通过回执都不属于本契约。'''


def assemble(document: dict[str, Any]) -> dict[str, Any]:
    from novel.orchestrator.reviewer_tasks import REQUEST_SCHEMA
    if document.get('schema') == REQUEST_SCHEMA:
        result = copy.deepcopy(document)
        if result.get('action', {}).get('surface') == 'stage2_blueprint_review':
            Stage2NodeReviewRequest.model_validate(result['action']['payload'])
        return result
    try:
        result = response_model().model_validate(document).model_dump(mode='json', by_alias=True)
        payload = result['action']['payload']
        ruling = payload.pop('ruling')
        payload.update(decision=ruling['decision'], owner=ruling.get('owner'),
                       chapter_ordinals=ruling.get('chapter_ordinals', []),
                       reason=payload['plot_review']['reason'], review=None)
        payload['knowledge_links'].update(actor=payload['actor'], reason=payload['reason'])
        Stage2NodeReviewRequest.model_validate(payload)
    except ValidationError as exc:
        from novel.orchestrator.stage2_reviewer_wire import _validation_locations
        raise ValueError('Blueprint 范围响应不符合当前闭集：'
                         + _validation_locations(exc, model=response_model())) from exc
    result['schema'] = REQUEST_SCHEMA
    return result


def allows_schema_publication_revalidation(
    request: Mapping[str, Any], schema_sha256: str | None,
) -> bool:
    """旧事实数据库响应不具备五维语义，不以运输升级免费签成新接受。"""
    return False
