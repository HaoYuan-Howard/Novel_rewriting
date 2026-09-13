#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Stage2CoreReview issues 协议的关闭式运输层（``novel-stage2-issue-review-v1``）。

业务回答只有 ``issues``；目标 ref、证据 ref 与候选 patch 的主体 ID 都是本轮
prepared 包冻结的闭集，由可信代码注入 wrapper/token/actor 身份。此层只做运输
归一，不产生审阅判断，也不代替 CLI 事务对 prepared 与当前字节身份的复验。
`stop`/`uncertain` 走同一 wire 的顶层 kind 判别，避免模型为安全出口再拼一份
完整 wrapper。
"""
from __future__ import annotations

import copy
from functools import reduce
from operator import or_
from typing import Annotated, Any, Literal

from pydantic import Field, ValidationError, create_model

from contracts.schema.base import StrictModel
from contracts.schema.common import Sha256
from contracts.schema.stage2_issue_review import (
    ISSUE_REVIEW_PROTOCOL, ISSUE_TYPES, REPAIR_HINTS,
    Stage2IssueReviewSubmission,
)
from novel.orchestrator import queue

WIRE_SCHEMA = ISSUE_REVIEW_PROTOCOL

_REVIEW_ISSUES_FLAGS = ['--review-issues', '@reviewer-input']


class Stage2IssueWireError(RuntimeError):
    """issues 协议回答的 typed 拒绝；进入现役有界纠错/持久拒因路径。"""


def _review_issues_option(task: object) -> dict[str, Any] | None:
    if getattr(task, 'waiting_on', None) != queue.WAITING_ON_STAGE2_REVIEW:
        return None
    matches = []
    for option in getattr(task, 'submission_options', ()) or ():
        exact = option.get('exact_request') if isinstance(option, dict) else None
        action = exact.get('action') if isinstance(exact, dict) else None
        if (isinstance(action, dict) and action.get('kind') == 'node'
                and action.get('node') == 'stage2.global'
                and action.get('flags') == _REVIEW_ISSUES_FLAGS):
            matches.append(option)
    if len(matches) != 1:
        return None
    return matches[0]


def matches(task: object) -> bool:
    return _review_issues_option(task) is not None


def _literal(values: list[str]) -> Any:
    if not values:
        raise Stage2IssueWireError('不能为空闭集构造 Literal')
    return Literal.__getitem__(tuple(values))


def _model(name: str, **fields: Any) -> type[StrictModel]:
    return create_model(name, __base__=StrictModel, **fields)


_IDENTITY_FIELDS = {
    'SecretPatch': 'secret_id', 'WorldRulePatch': 'rule_id',
    'RelationPatch': 'relation_id',
}


def _candidate_patch_type(binding: dict[str, Any], name: str) -> Any:
    """候选 patch 的窄能力 schema；主体 ID 全部来自 prepared 冻结目录。

    只接受明确字段/时点/证据订正（patch changes）与明确的误抽删除；
    人物侧只接受既有主体的整行订正（replace），不含 add/delete/合并。
    """
    from contracts.schema.characters import CharacterReplace
    from contracts.schema.stage2 import (
        GlobalCollectionDelete, RelationPatch, SecretPatch, SettingPatch,
        WorldRulePatch,
    )

    def patch_rows(patch_model: type, key: str) -> Any:
        ids = list(binding.get(_IDENTITY_FIELDS[patch_model.__name__] + 's') or [])
        if not ids:
            return (list[str], Field(max_length=0))
        identity = _IDENTITY_FIELDS[patch_model.__name__]
        specialized = create_model(
            f'{name}{key}Patch', __base__=patch_model,
            **{identity: (_literal(ids), ...)})
        return (list[specialized], Field(default_factory=list))

    collections = binding.get('collections') or {}
    delete_branches = []
    for collection, catalog in sorted(collections.items()):
        ids = list((catalog or {}).get('subject_ids') or [])
        if not ids:
            continue
        delete_branches.append(create_model(
            f'{name}{str(collection).title()}Delete', __base__=GlobalCollectionDelete,
            subject_id=(_literal(ids), ...)))
    if delete_branches:
        delete_union = reduce(or_, delete_branches)
        deletes_field = (list[delete_union], Field(default_factory=list))
    else:
        deletes_field = (list[str], Field(max_length=0))

    global_fields: dict[str, Any] = {
        'relations': patch_rows(RelationPatch, 'Relation'),
        'world_rules': patch_rows(WorldRulePatch, 'WorldRule'),
        'arc_secrets': patch_rows(SecretPatch, 'Secret'),
        'setting': (SettingPatch | None, Field(default=None)),
        'collection_deletes': deletes_field,
    }
    global_patch = _model(name + 'Global', **global_fields)
    char_ids = list(binding.get('character_ids') or [])
    if char_ids:
        characters_replaces = create_model(
            name + 'CharacterReplace', __base__=CharacterReplace,
            char_id=(_literal(char_ids), ...))
        characters = _model(
            name + 'Characters',
            replaces=(list[characters_replaces], Field(min_length=1))) | None
    else:
        characters = type(None)
    return _model(
        name + 'Candidate',
        owner=(Literal['stage2.characters', 'stage2.global'], ...),
        characters=(characters, ...),
        global_edit=(global_patch | None, Field(alias='global')))


def _issue_model(allowed: dict[str, Any], name: str) -> Any:
    item_refs = list(allowed.get('issue.target.refs.item') or [])
    scope_refs = list(allowed.get('issue.target.refs.scope') or [])
    if not item_refs and not scope_refs:
        raise Stage2IssueWireError('prepared 范围目标闭集为空')
    target_branches = []
    if item_refs:
        target_branches.append(_model(
            name + 'ItemTarget',
            kind=(Literal['existing_item'], ...),
            ref=(_literal(item_refs), ...)))
    if scope_refs:
        target_branches.append(_model(
            name + 'ScopeTarget',
            kind=(Literal['scope_obligation'], ...),
            ref=(_literal(scope_refs), ...)))
    target_type: Any = (target_branches[0] if len(target_branches) == 1
                        else Annotated[reduce(or_, target_branches),
                                       Field(discriminator='kind')])
    evidence_refs = list(allowed.get('issue.evidence_refs') or [])
    evidence_type: Any = ((list[_literal(evidence_refs)], Field(default_factory=list))
                          if evidence_refs else (list[str], Field(max_length=0)))
    candidate_type: Any = _candidate_patch_type(
        allowed.get('candidate_patch.binding') or {}, name) | None
    return _model(
        name + 'Issue',
        target=(target_type, ...),
        state=(Literal['FAIL', 'UNVER'], ...),
        issue_type=(Literal[ISSUE_TYPES], ...),
        problem=(str, Field(min_length=1)),
        evidence_refs=evidence_type,
        repair_hint=(Literal[REPAIR_HINTS], ...),
        candidate_patch=(candidate_type, ...))


def response_model(task: object) -> type[StrictModel]:
    option = _review_issues_option(task)
    if option is None:
        raise Stage2IssueWireError('当前 task 没有唯一 Stage2 issues 审阅选项')
    allowed = option.get('allowed_values') or {}
    issue = _issue_model(allowed, 'Stage2Wire')
    issues_envelope = _model(
        'Stage2IssuesAnswer', kind=(Literal['issues'], ...),
        issues=(list[issue], ...))
    stop_envelope = _model(
        'Stage2StopAnswer', kind=(Literal['stop', 'uncertain'], ...),
        reason=(str, Field(min_length=1)))
    answer = Annotated[reduce(or_, [issues_envelope, stop_envelope]),
                       Field(discriminator='kind')]
    return _model(
        'Stage2IssueWireEnvelope',
        schema_=(Literal[WIRE_SCHEMA], Field(alias='schema')),
        coverage_scope_id=(Sha256, ...),
        answer=(answer, ...))


def response_schema(task: object) -> dict:
    """公共 strict 归一后缩短局部定义名；字段、枚举与分支完全保留。"""
    from novel.agents.session_client import agent_json_schema
    from novel.agents import validate_agent_output_schema
    schema = agent_json_schema(response_model(task))
    names = {key: f'd{index}' for index, key in enumerate(schema.get('$defs', {}))}

    def compact(value):
        if isinstance(value, list):
            return [compact(row) for row in value]
        if not isinstance(value, dict):
            return value
        result = {}
        for key, item in value.items():
            if key in {'title', 'description'}:
                continue
            if key == '$ref':
                prefix = '#/$defs/'
                if not item.startswith(prefix) or item[len(prefix):] not in names:
                    raise Stage2IssueWireError('issues wire schema 含未绑定引用')
                result[key] = prefix + names[item[len(prefix):]]
            elif key in {'properties', '$defs'}:
                result[key] = {(names[name] if key == '$defs' else name): compact(row)
                               for name, row in item.items()}
            else:
                result[key] = compact(item)
        return result
    result = compact(schema)
    validate_agent_output_schema(result)
    return result


def _token_and_actor(option: dict[str, Any]) -> tuple[str, str]:
    action = option['exact_request']['action']
    payload = action.get('input_payload')
    if not isinstance(payload, dict):
        raise Stage2IssueWireError('issues 选项缺 typed input_payload')
    token = payload.get('review_token')
    actor = payload.get('actor')
    if not isinstance(token, str) or not token or not isinstance(actor, str) or not actor:
        raise Stage2IssueWireError('issues 选项缺可信 token/actor')
    return token, actor


def assemble(task: object, response: Any) -> dict[str, Any]:
    option = _review_issues_option(task)
    if option is None:
        raise Stage2IssueWireError('当前 task 没有唯一 Stage2 issues 审阅选项')
    model = response_model(task)
    try:
        parsed = (model.model_validate_json(response) if isinstance(response, str)
                  else model.model_validate(response))
    except ValidationError as exc:
        kinds = sorted({row['type'] for row in exc.errors(include_input=False)})
        raise Stage2IssueWireError(
            f'Stage2 issues wire typed 拒绝：{kinds}') from exc
    compact = parsed.model_dump(mode='json', by_alias=True)
    allowed = option.get('allowed_values') or {}
    if compact['coverage_scope_id'] != allowed.get('coverage_scope_id'):
        raise Stage2IssueWireError('Stage2 issues wire coverage_scope_id 已 stale')
    answer = compact['answer']
    if answer['kind'] in ('stop', 'uncertain'):
        from novel.orchestrator.reviewer_tasks import _request_wrapper
        # ReviewerTask 自带 surface_seal/waiting_on；stop wrapper 只需这两格。
        return _request_wrapper(
            task, task.waiting_on,
            {'kind': answer['kind'], 'reason': answer['reason']})
    token, actor = _token_and_actor(option)
    submission = Stage2IssueReviewSubmission.model_validate({
        'review_token': token, 'actor': actor, 'issues': answer['issues'],
    })
    exact = copy.deepcopy(option['exact_request'])
    exact['action']['input_payload'] = submission.model_dump(
        mode='json', by_alias=True)
    return exact


def prompt_for(task: object, base_prompt: str) -> str:
    addendum = prompt_addendum(task)
    if not addendum:
        return base_prompt
    return f'{base_prompt}\n\n{addendum}'


def output_schema_for(task: object) -> dict[str, Any] | None:
    """只向 issues 协议任务下发原生 schema；其余（含订正席）不在此接管。"""
    if not matches(task):
        return None
    return response_schema(task)


def prompt_addendum(task: object) -> str:
    option = _review_issues_option(task)
    if option is None:
        return ''
    allowed = option.get('allowed_values') or {}
    return f'''
本轮原生输出 schema 为 {WIRE_SCHEMA}，覆盖上文完整 action 的运输示例。
只输出 schema、coverage_scope_id、answer 三个字段；coverage_scope_id 必须为 {allowed.get('coverage_scope_id')}。
review_token、actor 由可信代码注入，不能自行生成或输出。
answer.kind=issues 时 answer.issues 是本轮全部问题；没有问题时输出空数组 []，
这是对当前 prepared 范围的显式派生通过，不是“没看”。answer.kind=stop/uncertain 时
只填 reason，说明缺什么材料或为什么必须停止；不得用 stop 表达“有问题但懒得写”。
target 只能引用本包 targets 提供的义务：具体主体用 kind=existing_item；
缺失称谓、身份一致性、已删除问题等集合/元/历史义务用 kind=scope_obligation。
不得伪造不存在的业务 ID；证据 evidence_refs 只能引用本包 evidence 闭集的 ev-N。
issue_type 与 repair_hint 只是分类与建议，不决定 owner，也不授予修复权限。
顺序：先依据通用判据、原文与当前产物主动找错，再核对本包后部的历史问题，
最后仅为明确的小错附 candidate_patch。是否容易修复不得影响问题是否报告。
candidate_patch 只接受明确字段/时点/证据订正与明确的误抽删除；人物新建/删除/合并、
称谓改归属等复杂修复不要给 patch，如实报告问题并给 repair_hint。
FAIL 表示确认当前产物错误；材料不足保持 UNVER，不得猜 FAIL。
'''.strip()
