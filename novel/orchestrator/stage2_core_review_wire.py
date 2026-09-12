"""Global Commander 多选项运输：模型选择语义动作，代码注入 immutable option 身份。

首次及正常增量同时开放逐项审阅与节点裁决。单 scope、单 reviewed-edit 的既有
wire 保持不变；多选项里的 reviewed-edit 复用它们的精确组装器。Core 免费编辑
使用显式 field/value 变化列表，使 provider required-all 不会把 absent 变成 null。
此层不采纳历史失败回答，也不代替 load_action / owner 的原子预检与恢复守卫。
"""
from __future__ import annotations

import copy
import hashlib
import json
from functools import reduce
from operator import or_
from types import SimpleNamespace
from typing import Annotated, Any, Literal, get_args, get_origin

from pydantic import Field, ValidationError, create_model

from contracts.schema.api import Stage2NodeReviewRequest
from contracts.schema.base import StrictModel
from contracts.schema.characters import CharacterAdd, CharacterDelete, CharacterReplace
from contracts.schema.common import Sha256
from contracts.schema.evaluation import EvidenceRef, GlobalReviewDecisionSet
from contracts.schema.stage2 import GlobalEdit, SettingPatch
from novel.orchestrator import queue, stage2_scope_wire

WIRE_SCHEMA = 'novel-stage2-core-review-choice-v1'


def _kind(option: dict) -> str | None:
    action = option.get('exact_request', {}).get('action', {})
    if action.get('kind') == 'approval' and action.get('surface') == 'stage2_global_review':
        return 'approval'
    if action.get('kind') == 'node' and action.get('node') == 'stage2.global':
        if action.get('flags') == ['--review-decisions', '@reviewer-input']:
            return 'review'
        if action.get('flags') == ['--apply-edit', '@reviewer-input']:
            return 'edit'
    return None


def matches(task: object) -> bool:
    options = getattr(task, 'submission_options', ())
    if (getattr(task, 'waiting_on', None) not in queue.STAGE2_REVIEWER_WAITING_ON
            or not options or any(not isinstance(row, dict) or _kind(row) is None for row in options)):
        return False
    if len(options) == 1 and (_kind(options[0]) == 'edit'
            or stage2_scope_wire.exact_request(task) is not None):
        return False
    return True


def options_sha256(task: object) -> str:
    return hashlib.sha256(json.dumps(task.submission_options, ensure_ascii=False,
        sort_keys=True, separators=(',', ':'), allow_nan=False).encode('utf-8')).hexdigest()


def _single(task, option):
    return SimpleNamespace(waiting_on=task.waiting_on, submission_options=(option,))


def _literal(values):
    if not values or len(values) != len(set(values)):
        raise ValueError('Stage2 native 引用目录为空或含重复身份')
    return Literal.__getitem__(tuple(values))


def _list_type(models):
    return list[reduce(or_, models)] if models else list[str]


def _list_field(models):
    return (_list_type(models), ...) if models else (list[str], Field(max_length=0))


def _model(name, **fields):
    return create_model(name, __base__=StrictModel, **fields)


def _review_model(option: dict, name: str):
    payload = option['exact_request']['action']['input_payload']
    GlobalReviewDecisionSet.model_validate(payload)
    rows = payload['decisions']
    if len({row['item_id'] for row in rows}) != len(rows):
        raise ValueError('Stage2 review 可信目录重复 item')
    groups = {}
    for row in rows:
        groups.setdefault(bool(row.get('generation_disposition')), []).append(row['item_id'])
    models = [_model(name + 'Judgment' + str(index),
        item_id=(_literal(ids), ...),
        state=(Literal['UNVER'] if disposition else Literal['PASS', 'FAIL', 'UNVER'], ...),
        reason=(str, Field(min_length=1)), evidence=(list[EvidenceRef], ...),
        disposition_reason=(str if disposition else type(None), Field(min_length=1) if disposition else ...))
        for index, (disposition, ids) in enumerate(groups.items())]
    return _model(name,
        blocking_scope=(stage2_scope_wire.ScopeJudgment if payload.get('blocking_scope') is not None else type(None), ...),
        decisions=_list_field(models))


def _without_none(annotation):
    if get_origin(annotation) is Annotated:
        inner, *metadata = get_args(annotation)
        return Annotated.__class_getitem__((_without_none(inner), *metadata))
    args = get_args(annotation)
    if type(None) in args:
        values = [value for value in args if value is not type(None)]
        return reduce(or_, values)
    return annotation


def _changes(model, name: str, *, identity: str | None = None):
    nullable = {'effective_until'} if model.__name__ == 'RelationPatch' else (
        {'fact', 'fact_ref'} if model.__name__ == 'SecretPatch' else (
            {'era', 'social_context'} if model is SettingPatch else set()))
    branches = []
    for key, info in model.model_fields.items():
        if key == identity:
            continue
        annotation = info.rebuild_annotation()
        if key not in nullable:
            annotation = _without_none(annotation)
        branches.append(_model(name + key.title().replace('_', ''),
            field=(Literal[key], ...), value=(annotation, ...)))
    return list[Annotated[reduce(or_, branches), Field(discriminator='field')]]


def _global_model(binding: dict, name: str):
    from novel.orchestrator import stage2_reviewer_wire as legacy
    fields = {}
    collection_fields = {}
    for key, (patch_model, identities) in legacy._PATCH_MODELS.items():
        identity = identities[0]
        catalog = binding['collections'][key]
        ids = catalog['subject_ids']
        models = ([_model(name + key.title(), **{
            identity: (_literal(ids), ...),
            'changes': (_changes(patch_model, name + key, identity=identity), Field(min_length=1)),
        })] if ids else [])
        fields[key] = _list_field(models)
        collection_fields[key] = (list[legacy._collection_operation_type(key, tuple(ids), name)], ...)
    fields['setting'] = (_changes(SettingPatch, name + 'Setting'), ...)
    fields.update({key: (annotation, ...) for key, annotation in legacy._REPLACEMENT_WIRE_TYPES.items()})
    fields['collection_edits'] = (_model(name + 'Collections', **collection_fields), ...)
    return _model(name, **fields)


def _core_model(binding: dict, name: str):
    ids = binding['character_ids']
    operations = [CharacterAdd]
    if ids:
        for model in (CharacterReplace, CharacterDelete):
            operations.append(create_model(name + model.__name__, __base__=model,
                char_id=(_literal(ids), ...)))
    character = _model(name + 'Characters',
        operations=(list[Annotated[reduce(or_, operations), Field(discriminator='kind')]], Field(min_length=1)))
    return _model(name, characters=(character | None, ...),
        global_edit=(_global_model(binding, name + 'Global') | None, Field(alias='global')))


def _approval_model(option: dict, name: str):
    allowed = option['allowed_values']
    ids = allowed['review.obligation_ids']
    exceptions = ([_model(name + 'Exception', obligation_id=(_literal(ids), ...),
        decision=(Literal['rework', 'uncertain', 'stop'], ...),
        reason=(str, Field(min_length=1)))] if ids else [])
    review = _model(name + 'Coverage', default_decision=(Literal['accept', 'uncertain', 'stop'], ...),
        exceptions=_list_field(exceptions))
    template = option['exact_request']['action']['payload']
    core = type(None)
    if template.get('core_edits') is not None:
        core = _core_model(allowed['core_edits.native_binding'], name + 'Core') | None
    return _model(name, decision=(_literal(allowed['decision']), ...),
        reason=(str, Field(min_length=1)), owner=(Literal['stage2.characters', 'stage2.global'] | None, ...),
        review=(review, ...), core_edits=(core, ...))


def response_model(task: object):
    if not matches(task):
        raise ValueError('当前 task 不属于 Global 多选项 native wire')
    from novel.orchestrator import stage2_reviewer_wire as legacy
    options = task.submission_options
    _literal([row['id'] for row in options])
    choices = []
    for index, option in enumerate(options):
        name = 'Stage2CoreChoice' + str(index)
        kind = _kind(option)
        if kind == 'review':
            payload = _review_model(option, name + 'Review')
        elif kind == 'approval':
            payload = _approval_model(option, name + 'Approval')
        else:
            payload = legacy.response_model(_single(task, option)).model_fields['payload'].annotation
        choices.append(_model(name, option_id=(Literal[option['id']], ...), payload=(payload, ...)))
    choice = choices[0] if len(choices) == 1 else Annotated[reduce(or_, choices), Field(discriminator='option_id')]
    return _model('Stage2CoreChoiceEnvelope', schema_=(Literal[WIRE_SCHEMA], Field(alias='schema')),
        expected_options_sha256=(Sha256, ...), choice=(choice, ...))


def response_schema(task: object) -> dict:
    """公共 strict 归一后仅缩短局部定义名，避免 Windows argv 承担泛型长名称。

    字段、枚举、required 和分支完全保留；不展开 $ref，也不合并不同判据。最后
    再经公共预检，短名只是本份 schema 的局部导航，不能成为持久业务身份。
    """
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
                    raise ValueError('Core native schema 含未绑定的局部定义引用')
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


def _change_values(rows: list[dict]) -> dict:
    result = {}
    for row in rows:
        if row['field'] in result:
            raise ValueError('Stage2 Core 同一字段不得重复修改')
        result[row['field']] = row['value']
    return result


def _global_edit(delta: dict, binding: dict) -> dict:
    from novel.orchestrator import stage2_reviewer_wire as legacy
    result = {}
    for key, (_, identities) in legacy._PATCH_MODELS.items():
        if delta[key]:
            identity = identities[0]
            result[key] = [{identity: row[identity], **_change_values(row['changes'])} for row in delta[key]]
    if delta['setting']:
        result['setting'] = _change_values(delta['setting'])
    for key in legacy._REPLACEMENT_WIRE_TYPES:
        if delta[key] is not None:
            result[key] = delta[key]
    collections = [{'collection': key,
        'expected_collection_sha256': binding['collections'][key]['expected_collection_sha256'],
        'operations': operations} for key, operations in delta['collection_edits'].items() if operations]
    if collections:
        result['collection_edits'] = collections
    # 只序列化显式提交字段；不让 Patch 缺席的默认值获得清空权。
    return GlobalEdit.model_validate(result).model_dump(mode='json', by_alias=True, exclude_unset=True)


def assemble(task: object, response: Any) -> dict:
    from novel.orchestrator import stage2_reviewer_wire as legacy
    model = response_model(task)
    try:
        compact = (model.model_validate_json(response) if isinstance(response, str)
                   else model.model_validate(response)).model_dump(mode='json', by_alias=True)
        if compact['expected_options_sha256'] != options_sha256(task):
            raise ValueError('Stage2 Core native options SHA 已 stale')
        option = next(row for row in task.submission_options if row['id'] == compact['choice']['option_id'])
        delta = compact['choice']['payload']
        kind = _kind(option)
        if kind == 'edit':
            single = _single(task, option)
            return legacy.assemble(single, {'schema': legacy.WIRE_SCHEMA, 'option_id': option['id'],
                'expected_exact_request_sha256': legacy.wire_binding(single).expected_exact_request_sha256,
                'payload': delta})
        exact = copy.deepcopy(option['exact_request'])
        action = exact['action']
        if kind == 'review':
            payload = action['input_payload']
            templates = {row['item_id']: row for row in payload['decisions']}
            selected = []
            for row in delta['decisions']:
                item = copy.deepcopy(templates[row['item_id']])
                item.update({key: row[key] for key in ('state', 'reason', 'evidence')})
                if item.get('generation_disposition') is not None:
                    item['generation_disposition']['reason'] = row['disposition_reason']
                selected.append(item)
            payload['decisions'] = selected
            if payload.get('blocking_scope') is not None:
                payload['blocking_scope'].update(delta['blocking_scope'])
            GlobalReviewDecisionSet.model_validate(payload)
        else:
            payload = action['payload']
            payload.update({key: delta[key] for key in ('decision', 'reason', 'owner')})
            payload['review'].update(delta['review'])
            if delta['core_edits'] is None:
                payload['core_edits'] = None
            else:
                frozen = payload['core_edits']
                edits = delta['core_edits']
                character = edits['characters']
                if character is not None:
                    character = {'source_sha256': frozen['expected_characters_sha256'], **character}
                global_edit = edits['global']
                if global_edit is not None:
                    global_edit = _global_edit(global_edit, option['allowed_values']['core_edits.native_binding'])
                payload['core_edits'] = {**frozen, 'characters': character, 'global': global_edit}
            Stage2NodeReviewRequest.model_validate(payload)
        return exact
    except ValidationError as exc:
        raise ValueError('Stage2 Core native typed 拒绝：' + legacy._validation_locations(exc, model=model)) from exc


def prompt_addendum(task: object) -> str:
    return f'''
本轮原生输出 schema 为 {WIRE_SCHEMA}，覆盖上文完整 action 的运输示例。
只输出 schema、expected_options_sha256、choice；expected_options_sha256 必须为 {options_sha256(task)}。
choice={{option_id,payload}} 从本包所有 submission_options 中选择恰好一项；不要输出 id 或完整 wrapper。
actor、来源/主体/集合 SHA、review_token、scope 与固定章范围均由可信代码注入，不能自行生成。
逐项 review 没有 blocking_scope 时只能填 null，并按模板顺序完整提交所有 decisions；有 scope 时
blocking_scope 是显式默认裁决，decisions 是有序例外。FAIL/UNVER 不得省略；不得把 scope=null 修成伪 scope。
review 的 disposition_reason 只在已开放隔离的行填写，其余为 null。所有审阅判断仍须来自完整材料。
approval 的 review 只填 default_decision/exceptions，义务只能选当前 enum；不能靠删问题删掉义务。
core_edits=null 表示没有免费编辑；非空时只填写有变化的 owner。人物 operations 保留正式 replace/delete
和新增 character_ref=c1 等；同批 Global 关系可引用新增 cN，代码分配并冻结正式 ID，禁止猜 ID。
Core Global patch 每行用 changes=[{{field,value}}]；setting 直接为变化列表。[] 表示不修改，
显式 value:null 才表示清空可空字段。秘密事实更改须同时给 fact、fact_ref、evidence_refs，仍严格 XOR。
*_replace=null 表示不提交替换，[] 表示完整清空该集合；collection_edits 每个固定键 [] 表示不修改。
双 owner 编辑使用 decision=rework、owner=null，并在 review.exceptions 保留相应 rework 义务。
node apply-edit 的 payload 沿用当前 reviewed-edit 模板 delta；未提交行不改，修后复核不能丢掉旧 FAIL/UNVER。
以上只减少机械抄写；最终仍通过现役 load_action、正式 schema 与原 owner 预检，不自动接受新义务。
'''.strip()
