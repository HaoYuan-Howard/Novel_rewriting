#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Stage2 全局默认裁决与逐项例外的关闭式运输层。

只投影可信 scope 模板，不生成任何审阅判断。scope 的完整身份、逐项 subject SHA
和 wrapper 均从同一 exact request 注入；仍由 Reviewer loader 与 CLI producer
复核所有身份、证据和业务条件。只接现役全局审阅，不保留已退役的预审入口。
"""
from __future__ import annotations

import copy
import hashlib
import json
from typing import Any, Literal

from pydantic import Field, ValidationError, create_model

from contracts.schema.base import StrictModel
from contracts.schema.common import Sha256
from contracts.schema.evaluation import EvidenceRef, GlobalReviewDecisionSet
from novel.orchestrator import queue


WIRE_SCHEMA = 'novel-stage2-scope-review-delta-v2'


def exact_request(task: object) -> dict[str, Any] | None:
    """只接管唯一全局 review 选项，不劫持多选或其他 Commander。"""
    options = getattr(task, 'submission_options', ())
    if (getattr(task, 'waiting_on', None) != queue.WAITING_ON_STAGE2_REVIEW
            or len(options) != 1):
        return None
    exact = options[0].get('exact_request', {})
    action = exact.get('action', {})
    if (action.get('kind') != 'node'
            or action.get('node') != 'stage2.global'
            or action.get('flags') != ['--review-decisions', '@reviewer-input']):
        return None
    payload = action.get('input_payload')
    if not isinstance(payload, dict) or not isinstance(payload.get('blocking_scope'), dict):
        return None
    GlobalReviewDecisionSet.model_validate(payload)
    return copy.deepcopy(exact)


def _payload(exact: dict[str, Any]) -> dict[str, Any]:
    return exact['action']['input_payload']


def _sha(exact: dict[str, Any]) -> str:
    return hashlib.sha256(json.dumps(
        exact, ensure_ascii=False, sort_keys=True,
        separators=(',', ':'), allow_nan=False).encode('utf-8')).hexdigest()


class ScopeJudgment(StrictModel):
    state: Literal['PASS', 'UNVER']
    reason: str = Field(min_length=1)
    evidence: list[EvidenceRef]


def response_model(task: object) -> type[StrictModel]:
    exact = exact_request(task)
    if exact is None:
        raise ValueError('当前 task 没有唯一完整 Stage2 scope')
    rows = _payload(exact)['decisions']
    ids = tuple(row['item_id'] for row in rows)
    if len(ids) != len(set(ids)):
        raise ValueError('Stage2 scope 模板 item 身份重复')
    if ids:
        decision = create_model(
            'Stage2ScopeException', __base__=StrictModel,
            item_id=(Literal[ids], ...),
            state=(Literal['PASS', 'FAIL', 'UNVER'], ...),
            reason=(str, Field(min_length=1)),
            evidence=(list[EvidenceRef], ...),
        )
        decision_field = (list[decision], ...)
    else:
        # 历史/测试的空 scope 仍能表达默认裁决，但不能发明任何例外身份。
        decision_field = (list[str], Field(max_length=0))
    return create_model(
        'Stage2ScopeDelta', __base__=StrictModel,
        schema_=(Literal[WIRE_SCHEMA], Field(alias='schema')),
        expected_exact_request_sha256=(Sha256, ...),
        blocking_scope=(ScopeJudgment, ...),
        decisions=decision_field,
    )


def assemble(task: object, response: Any) -> dict[str, Any]:
    exact = exact_request(task)
    if exact is None:
        raise ValueError('当前 task 没有唯一完整 Stage2 scope')
    model = response_model(task)
    try:
        parsed = (model.model_validate_json(response) if isinstance(response, str)
                  else model.model_validate(response))
    except ValidationError as exc:
        # 不回显可能夹带正文的模型值或动态 item enum。
        kinds = sorted({row['type'] for row in exc.errors(include_input=False)})
        raise ValueError(f'Stage2 scope compact typed 拒绝：{kinds}') from exc
    compact = parsed.model_dump(mode='json', by_alias=True)
    if compact['expected_exact_request_sha256'] != _sha(exact):
        raise ValueError('Stage2 scope compact exact-request SHA 已 stale')
    payload = _payload(exact)
    templates = {row['item_id']: row for row in payload['decisions']}
    payload['blocking_scope'].update(compact['blocking_scope'])
    payload['decisions'] = [
        {**copy.deepcopy(templates[row['item_id']]), **row}
        for row in compact['decisions']
    ]
    # 不排序、不去重、不删除 FAIL：重复/乱序由同一 loader 拒绝。
    return exact


def prompt_addendum(task: object) -> str:
    exact = exact_request(task)
    if exact is None:
        return ''
    return f'''
本轮原生输出 schema 为 {WIRE_SCHEMA}，替代上文完整 JSON 的输出运输形状。
只返回 schema、expected_exact_request_sha256、blocking_scope、decisions 四个字段。
expected_exact_request_sha256 必须是 {_sha(exact)}。
blocking_scope 只填写 state/reason/evidence：它是完整 blocking 面的显式默认裁决；
decisions 只填写与默认值不同或需要逐项说明的例外，沿模板顺序提交
item_id/state/reason/evidence。没有例外时显式 []。模板 decisions 是允许选择的
item/hash 闭集，不是必须复制的答案列表。FAIL/UNVER 例外必须保留，不能为了 scope
通过而省略。scope 的默认 PASS 不覆盖例外 FAIL/UNVER。
actor、wrapper、所有 scope 身份、逐项 expected_subject_sha256 由可信组装器注入，
不要在运输 JSON 中输出这些字段。逐项正文阅读与判据保持上文契约。
evidence 只能使用真实 locator；引用整个主体时 start/end 同时为 null；引用区间时
end 必须严格大于 start，不能用 0/0 代替未知区间。无可登记证据时显式 []。
'''
