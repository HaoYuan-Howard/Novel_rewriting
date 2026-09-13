"""已购审阅的免费重验：业务材料与临时提交凭证分别比较。

这里只消费已由工作单元核实的原请求/回答；不读盘、不调用模型、不产生决定。
旧回答必须先命中原包，随后才可换当前提交凭证，并由当前 typed loader 和 producer
再次验证。原请求、原回答和首次 typed 证明不重写，后继决定仍引用原内容轮。
"""
from __future__ import annotations

import copy
import hashlib
import json
from collections.abc import Mapping
from dataclasses import dataclass
from types import SimpleNamespace
from typing import Any

from pydantic import TypeAdapter, ValidationError

from contracts.schema.common import Sha256
from novel.orchestrator import queue


WAITING_ON = frozenset({
    'stage6.approve', 'stage7.approve', queue.WAITING_ON_STAGE6_PREPARE,
})
_OBJECT = TypeAdapter(dict[str, Any])
_SHA = TypeAdapter(Sha256)


class RevalidationRefused(ValueError):
    """恢复证据不完整或旧回答未绑定原工作包；不能当作新材料重购。"""


def _sha(value: Any) -> str:
    return hashlib.sha256(json.dumps(
        value, ensure_ascii=False, sort_keys=True, separators=(',', ':'),
        allow_nan=False).encode('utf-8')).hexdigest()


def business_identity(
    materials: Mapping[str, Any], waiting_on: str, contract: str,
) -> dict[str, Any]:
    """完整生产包和模型实际阅读投影共同使用的长期审阅身份。"""
    try:
        if waiting_on == queue.WAITING_ON_STAGE6_PREPARE:
            # 延续已购 Prepare 的原业务身份；token 绑定候选、正文/Hook 和原审阅。
            # 这里只允许换运输 wrapper，不把 Prepare token 当可更新凭证。
            token = materials['prepare']['state']['review_token']
            if not isinstance(token, str) or not token.startswith('s6p1_'):
                raise RevalidationRefused('Prepare 材料缺原审阅凭证')
            _SHA.validate_python(token.removeprefix('s6p1_'))
            return {
                'kind': 'review-token-business-v1', 'waiting_on': waiting_on,
                'review_token': token,
                'reviewer_contract_sha256': hashlib.sha256(contract.encode('utf-8')).hexdigest(),
            }
        material = materials[waiting_on.removesuffix('.approve')]
        return {
            'kind': 'review-material-business-v1', 'waiting_on': waiting_on,
            'review_identity_sha256': _SHA.validate_python(material['review_identity_sha256']),
            'reviewer_contract_sha256': hashlib.sha256(contract.encode('utf-8')).hexdigest(),
        }
    except (KeyError, TypeError, ValidationError) as exc:
        raise RevalidationRefused('审阅材料缺完整业务身份或义务') from exc


def _frozen(prompt: Any) -> tuple[str, dict[str, Any], str]:
    if not isinstance(prompt, str):
        raise RevalidationRefused('原审阅请求缺冻结 prompt')
    start, end = '<immutable-workpack>\n', '\n</immutable-workpack>'
    contract_start, contract_end = '<reviewer-contract>\n', '\n</reviewer-contract>'
    if (prompt.count(start) != 1 or prompt.count(end) != 1
            or prompt.count(contract_start) != 1 or prompt.count(contract_end) != 1):
        raise RevalidationRefused('原审阅请求缺唯一冻结工作包或裁决契约')
    prefix, rest = prompt.split(start, 1)
    body, _suffix = rest.split(end, 1)
    try:
        envelope = _OBJECT.validate_json(body)
        contract = prefix.split(contract_start, 1)[1].split(contract_end, 1)[0]
    except (ValidationError, IndexError) as exc:
        raise RevalidationRefused('冻结工作包或裁决契约不可读') from exc
    return prefix, envelope, contract


def request_business_sha256(request: Mapping[str, Any]) -> str:
    _prefix, envelope, contract = _frozen(request.get('prompt'))
    waiting_on = envelope.get('waiting_on')
    if waiting_on not in WAITING_ON or request.get('waiting_on') != waiting_on:
        raise RevalidationRefused('原工作包不属于登记的免费重验入口')
    return _sha(business_identity(envelope.get('materials', {}), waiting_on, contract))


def _token_path(action: Mapping[str, Any]) -> tuple[str | int, ...]:
    if action.get('kind') == 'node' and action.get('node') == 'stage6.prepare':
        return ('input_payload', 'review_token')
    if action.get('kind') == 'node' and action.get('node') in {'stage6.approve', 'stage7.approve'}:
        flags = action.get('flags')
        if isinstance(flags, (list, tuple)) and flags.count('--review-token') == 1:
            return ('flags', flags.index('--review-token') + 1)
    raise RevalidationRefused('动作没有登记的临时提交凭证')


def _at(value: Any, path: tuple[str | int, ...]) -> Any:
    try:
        for key in path:
            value = value[key]
        return value
    except (KeyError, TypeError, IndexError) as exc:
        raise RevalidationRefused('动作缺登记的提交凭证字段') from exc


def _set(value: Any, path: tuple[str | int, ...], replacement: Any) -> None:
    _at(value, path[:-1])[path[-1]] = replacement


def _logical_action(action: Mapping[str, Any]) -> dict[str, Any]:
    result = copy.deepcopy(dict(action))
    if isinstance(result.get('flags'), tuple):
        result['flags'] = list(result['flags'])
    if (result.get('kind') not in {'stop', 'uncertain'}
            and result.get('node') != 'stage6.prepare'):
        _set(result, _token_path(result), '<current-review-token>')
    return result


def _reading(envelope: Mapping[str, Any]) -> dict[str, Any]:
    """仅抹去登记的瞬时坐标；正文、finding、范围、schema 和其余字段全部比较。"""
    result = copy.deepcopy(dict(envelope))
    try:
        result.pop('surface_seal')
        result.pop('action_sha256')
        waiting_on = result['waiting_on']
        if waiting_on == queue.WAITING_ON_STAGE6_PREPARE:
            # 全部实际阅读材料、决定域、来源和 token 逐字不变；只去当前盘面 CAS。
            for option in result['submission_options']:
                option['exact_request'].pop('surface_seal')
            return result
        material = result['materials'][waiting_on.removesuffix('.approve')]
        for key in ('validation_seal_sha256', 'review_token', 'workpack_sha256'):
            material.pop(key)
        for option in result['submission_options']:
            exact = option['exact_request']
            exact.pop('surface_seal')
            exact['action'] = _logical_action(exact['action'])
    except (KeyError, TypeError) as exc:
        raise RevalidationRefused('工作包缺免费重验所需的精确绑定') from exc
    return result


def _prepare_schema_bound(request: Mapping[str, Any], source: dict[str, Any],
                          task: Any, schema_sha256: str | None) -> bool:
    """动态 schema 可随 wrapper 变，但原 schema 必须闭合原 options，不能追认。"""
    from novel.commanders.base import _prepare_output_schema
    from novel.orchestrator import stage6_prepare_wire

    original = SimpleNamespace(submission_options=source['submission_options'])
    try:
        old_schema, _, old_sha = _prepare_output_schema(
            stage6_prepare_wire.output_schema_for(original))
        recorded_schema, _, recorded_sha = _prepare_output_schema(request.get('output_schema'))
        _, _, current_sha = _prepare_output_schema(stage6_prepare_wire.output_schema_for(task))
    except (ValueError, RuntimeError) as exc:
        raise RevalidationRefused('Prepare 原 schema/options 证明不可读') from exc
    if (recorded_schema != old_schema or recorded_sha != old_sha
            or request.get('schema_sha256') != old_sha):
        raise RevalidationRefused('Prepare 原 schema 未绑定原工作包闭集')
    return current_sha == schema_sha256


@dataclass(frozen=True)
class ReviewRevalidation:
    source: dict[str, Any]
    target: dict[str, Any]

    def rebind(self, document: Mapping[str, Any]) -> dict[str, Any]:
        """先认旧包再换当前凭证；模型的决定、理由与编辑值逐字段原样保留。"""
        result = copy.deepcopy(dict(document))
        if self.source['waiting_on'] == queue.WAITING_ON_STAGE6_PREPARE:
            from novel.orchestrator import stage6_prepare_wire
            original = SimpleNamespace(submission_options=self.source['submission_options'])
            try:
                # 先用原 native 闭集认回答，再交当前 loader；当前 wire 一律不放宽。
                result = stage6_prepare_wire.assemble(original, result)
            except stage6_prepare_wire.PrepareReviewerWireError as exc:
                raise RevalidationRefused(str(exc)) from exc
        if (set(result) != {'schema', 'surface_seal', 'waiting_on', 'action'}
                or result['surface_seal'] != self.source['surface_seal']
                or result['waiting_on'] != self.source['waiting_on']):
            raise RevalidationRefused('已购回答未绑定原工作包的 surface/blocker')
        action = result['action']
        if not isinstance(action, dict):
            raise RevalidationRefused('已购回答缺 typed action')
        selectors = ('kind', 'node', 'surface')
        options = [index for index, row in enumerate(self.source['submission_options'])
                   if all(row['exact_request']['action'].get(key) == action.get(key)
                          for key in selectors)]
        if len(options) != 1:
            raise RevalidationRefused('已购回答未唯一命中原工作包动作')
        index = options[0]
        old = self.source['submission_options'][index]['exact_request']
        new = self.target['submission_options'][index]['exact_request']
        if result['schema'] != old['schema']:
            raise RevalidationRefused('已购回答 schema 与原请求不同')
        if action.get('kind') not in {'stop', 'uncertain'}:
            path = _token_path(action)
            if _at(action, path) != _at(old['action'], path):
                raise RevalidationRefused('已购回答没有回交原工作包的精确审阅凭证')
            _set(action, path, _at(new['action'], path))
        result['surface_seal'] = self.target['surface_seal']
        return result


def prepare(
    request: Mapping[str, Any], task: Any, *, schema_sha256: str | None,
) -> ReviewRevalidation | None:
    if task.waiting_on not in WAITING_ON:
        return None
    if request.get('waiting_on') != task.waiting_on:
        return None
    old_prefix, old, _old_contract = _frozen(request.get('prompt'))
    new_prefix, new, _new_contract = _frozen(task.prompt)
    same_schema = request.get('schema_sha256') == schema_sha256
    if task.waiting_on == queue.WAITING_ON_STAGE6_PREPARE:
        same_schema = _prepare_schema_bound(request, old, task, schema_sha256)
    if (not same_schema or old_prefix != new_prefix
            or _reading(old) != _reading(new)):
        return None
    if request_business_sha256(request) != task.business_sha256:
        raise RevalidationRefused('同材料重验的当前业务身份不一致')
    return ReviewRevalidation(old, new)


def proof_matches(
    request: Mapping[str, Any], proof: Mapping[str, Any], *,
    business_sha256: str, waiting_on: str, action: Mapping[str, Any],
    provider_response_sha256: str,
) -> bool:
    """同一原回答的后继记录可更换临时 token；首次 typed 证明保持不可变。"""
    if waiting_on not in WAITING_ON:
        return False
    if (proof.get('waiting_on') != waiting_on
            or proof.get('provider_response_sha256') != provider_response_sha256
            or proof.get('business_sha256') not in {request.get('business_sha256'), business_sha256}
            or request_business_sha256(request) != business_sha256):
        return False
    original = proof.get('action')
    if not isinstance(original, dict) or _sha(original) != proof.get('action_sha256'):
        return False
    return _logical_action(original) == _logical_action(action)
