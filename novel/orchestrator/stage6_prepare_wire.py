"""独立 Prepare Commander 的紧凑运输；正式决定仍交现役 loader 与 producer。

响应绑定本轮完整 options 与 exact request，代码注入来源、actor 和 token。
模型只判断支持类型、开篇范围及语义接受，不生成持久来源身份。
"""
from __future__ import annotations

import copy
import hashlib
import json
from typing import Annotated, Literal

from pydantic import Field, ValidationError, create_model

from contracts.invariants import STAGE6_PREPARE_DECISIONS
from contracts.schema.base import StrictModel
from contracts.schema.stage6_prepare import PrepareDraft, PrepareEdit
from novel.agents.session_client import agent_json_schema
from novel.orchestrator import queue


WIRE_SCHEMA = 'novel-stage6-prepare-review-v1'


class PrepareReviewerWireError(RuntimeError):
    """原 wire 不属于本轮 immutable Prepare 工作包。"""


def _sha(value) -> str:
    return hashlib.sha256(json.dumps(value, ensure_ascii=False, sort_keys=True,
        separators=(',', ':'), allow_nan=False).encode('utf-8')).hexdigest()


def uses_compact_wire(task) -> bool:
    return any(option.get('exact_request', {}).get('waiting_on') == queue.WAITING_ON_STAGE6_PREPARE
               for option in task.submission_options)


def _options(task):
    options = list(task.submission_options)
    by_kind = {}
    for option in options:
        request = option.get('exact_request', {})
        action = request.get('action', {})
        kind = action.get('kind')
        if (request.get('waiting_on') != queue.WAITING_ON_STAGE6_PREPARE
                or kind not in {'node', 'stop'} or kind in by_kind):
            raise PrepareReviewerWireError('Prepare options 不是唯一独立决定及 stop')
        if kind == 'node' and (action.get('node') != 'stage6.prepare'
                or action.get('flags') != ['--review-decisions', '@reviewer-input']):
            raise PrepareReviewerWireError('Prepare action 越出当前 owner')
        by_kind[kind] = option
    if set(by_kind) != {'node', 'stop'}:
        raise PrepareReviewerWireError('Prepare options 缺独立决定或 stop')
    return options, by_kind


def response_model(task):
    options, by_kind = _options(task)
    digest = _sha(options)
    decision = create_model('PrepareReviewDecision_' + digest[:12], __base__=StrictModel,
        kind=(Literal['decision'], ...),
        exact_request_sha256=(Literal[_sha(by_kind['node']['exact_request'])], ...),
        decision=(Literal[STAGE6_PREPARE_DECISIONS], ...),
        reason=(str, Field(min_length=1)),
        source_blurb=(PrepareDraft | None, ...))
    stop = create_model('PrepareReviewStop_' + digest[:12], __base__=StrictModel,
        kind=(Literal['stop'], ...),
        exact_request_sha256=(Literal[_sha(by_kind['stop']['exact_request'])], ...),
        reason=(str, Field(min_length=1)))
    return create_model('PrepareReviewResponse_' + digest[:12], __base__=StrictModel,
        wire_schema=(Literal[WIRE_SCHEMA], ...),
        options_sha256=(Literal[digest], ...),
        choice=(Annotated[decision | stop, Field(discriminator='kind')], ...))


def output_schema_for(task):
    return agent_json_schema(response_model(task))


def prompt_for(task, prompt: str) -> str:
    options, by_kind = _options(task)
    bindings = {kind: _sha(option['exact_request']) for kind, option in by_kind.items()}
    return (prompt + '\n\nPrepare 当前 native response：' + json.dumps({
        'wire_schema': WIRE_SCHEMA, 'options_sha256': _sha(options),
        'exact_request_sha256': bindings,
        'source_blurb': 'null 表示不编辑；有值为当前段号候选格式的完整免费订正。'
            'accept/rework/uncertain 评价提交后的候选。来源和actor不由模型填写。'}, ensure_ascii=False))


def assemble(task, response: dict) -> dict:
    options, by_kind = _options(task)
    try:
        choice = response_model(task).model_validate(response).choice
    except ValidationError as exc:
        raise PrepareReviewerWireError('Prepare 响应未满足当前完整分支与来源绑定') from exc
    kind = 'stop' if choice.kind == 'stop' else 'node'
    request = copy.deepcopy(by_kind[kind]['exact_request'])
    if not choice.reason.strip():
        raise PrepareReviewerWireError('Prepare 决定必须有实际理由')
    if kind == 'stop':
        request['action']['reason'] = choice.reason
        return request
    payload = request['action']['input_payload']
    payload.update(reason=choice.reason, decision=choice.decision, edit=None)
    if choice.source_blurb is not None:
        binding = by_kind['node'].get('prepare_input_binding')
        if not isinstance(binding, dict):
            raise PrepareReviewerWireError('Prepare option 缺完整 typed 编辑来源')
        try:
            edit = PrepareEdit.model_validate({**binding,
                'actor': payload['actor'], 'reason': choice.reason,
                'source_blurb': choice.source_blurb.model_dump(mode='json')})
        except ValidationError as exc:
            raise PrepareReviewerWireError('Prepare 编辑来源模板不合法') from exc
        if edit.source_sha256 != payload['source_sha256']:
            raise PrepareReviewerWireError('Prepare 编辑来源与决定不同')
        payload['edit'] = edit.model_dump(mode='json')
    return request
