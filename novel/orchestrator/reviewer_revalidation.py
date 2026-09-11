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
from typing import Any

from pydantic import TypeAdapter, ValidationError

from contracts.schema.common import Sha256
from contracts.schema.stage2_review import Stage2ReviewCoverage
from novel.orchestrator import queue


WAITING_ON = frozenset({
    queue.WAITING_ON_STAGE2_BLUEPRINT_REVIEW, 'stage6.approve', 'stage7.approve',
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
        surface = ('blueprint' if waiting_on == queue.WAITING_ON_STAGE2_BLUEPRINT_REVIEW
                   else waiting_on.removesuffix('.approve'))
        material = materials[surface]
        identity = {
            'kind': 'review-material-business-v1', 'waiting_on': waiting_on,
            'review_identity_sha256': _SHA.validate_python(material['review_identity_sha256']),
            'reviewer_contract_sha256': hashlib.sha256(contract.encode('utf-8')).hexdigest(),
        }
        if waiting_on == queue.WAITING_ON_STAGE2_BLUEPRINT_REVIEW:
            coverage = material['review_coverage']
            if 'scope' in coverage:
                full = Stage2ReviewCoverage.model_validate(coverage)
                scope_sha = full.scope.scope_sha256
                pending = full.pending_obligation_ids
                prior = [row.model_dump(mode='json') for row in full.prior_problems]
            else:
                scope_sha = _SHA.validate_python(coverage['scope_sha256'])
                pending = [row['obligation_id'] for row in coverage['pending']]
                prior = coverage['prior_problems']
            identity.update({
                'subject_sha256': _SHA.validate_python(material['subject_sha256']),
                'main_text_sha256': _SHA.validate_python(
                    material['binding']['main_text_artifact_sha256']),
                'scope_sha256': scope_sha,
                'pending_obligation_ids': pending, 'prior_problems': prior,
            })
        return identity
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
    if action.get('kind') == 'approval' and action.get('surface') == 'stage2_blueprint_review':
        return ('payload', 'review_token')
    if action.get('kind') == 'node' and action.get('node') == 'stage2.blueprint':
        return ('input_payload', 'expected_cross_review_token')
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
    if result.get('kind') not in {'stop', 'uncertain'}:
        _set(result, _token_path(result), '<current-review-token>')
    return result


def _reading(envelope: Mapping[str, Any]) -> dict[str, Any]:
    """仅抹去登记的瞬时坐标；正文、finding、范围、schema 和其余字段全部比较。"""
    result = copy.deepcopy(dict(envelope))
    try:
        result.pop('surface_seal')
        result.pop('action_sha256')
        waiting_on = result['waiting_on']
        surface = ('blueprint' if waiting_on == queue.WAITING_ON_STAGE2_BLUEPRINT_REVIEW
                   else waiting_on.removesuffix('.approve'))
        material = result['materials'][surface]
        for key in ('validation_seal_sha256', 'review_token',
                    'binding_sha256' if surface == 'blueprint' else 'workpack_sha256'):
            material.pop(key)
        if surface == 'blueprint':
            material['material_projection'].pop('full_workpack_sha256')
        for option in result['submission_options']:
            exact = option['exact_request']
            exact.pop('surface_seal')
            exact['action'] = _logical_action(exact['action'])
    except (KeyError, TypeError) as exc:
        raise RevalidationRefused('工作包缺免费重验所需的精确绑定') from exc
    return result


@dataclass(frozen=True)
class ReviewRevalidation:
    source: dict[str, Any]
    target: dict[str, Any]

    def rebind(self, document: Mapping[str, Any]) -> dict[str, Any]:
        """先认旧包再换当前凭证；模型的决定、理由与编辑值逐字段原样保留。"""
        result = copy.deepcopy(dict(document))
        if self.source['waiting_on'] == queue.WAITING_ON_STAGE2_BLUEPRINT_REVIEW:
            from novel.orchestrator import stage2_blueprint_wire
            try:
                result = stage2_blueprint_wire.assemble(result)
            except ValueError as exc:
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
    if not same_schema and task.waiting_on == queue.WAITING_ON_STAGE2_BLUEPRINT_REVIEW:
        from novel.orchestrator.stage2_blueprint_wire import allows_schema_publication_revalidation
        same_schema = allows_schema_publication_revalidation(request, schema_sha256)
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
