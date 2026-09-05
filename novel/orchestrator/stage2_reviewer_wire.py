#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Stage2 reviewed GlobalEdit 的紧凑 Commander wire 与可信组装器。

生产动作仍是 :class:`contracts.schema.stage2.GlobalEdit`，现役
``reviewer_tasks.load_action`` 仍拥有最终 binding 与业务判定。本模块只缩短
Commander 必须返回的运输形状：

* wrapper、不可编辑字段、patch 未变行、collection SHA、review scope 与 subject
  SHA 都从 immutable ``submission_options[].exact_request`` 确定性注入；
* Commander 只返回当前模板已经开放的 patch 行、collection operations、replacement
  与修后复核判断；
* 最终仍组装成完整 ``novel-reviewer-action-v1`` document，再交现役 loader 复验。

不能用一份静态“所有字段都 Optional”的 Pydantic model：公共 agent JSON Schema 会
依 OpenAI strict 规则把所有 properties 列为 required，显式 ``null`` 随后既可能被
误当成清空，也会破坏生产 Patch 的 ``model_fields_set`` 语义。因此 schema 必须从
当前可信模板动态铸造。patch 行缺席表示保留；一旦出现，则该模板行已经开放的字段
全部显式交回。这样既保持 portable structured output，又不会让 Supervisor 猜业务值。
"""
from __future__ import annotations

import copy
import hashlib
import json
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from functools import reduce
from operator import or_
from typing import Annotated, Any, Literal, Protocol

from pydantic import Field, ValidationError, create_model

from contracts.schema.base import StrictModel
from contracts.schema.common import Sha256
from contracts.schema.evaluation import (
    EvidenceRef,
    GlobalReviewGenerationDisposition,
    GlobalReviewDecisionSet,
)
from contracts.schema.stage2 import (
    AddressContextPatch,
    ArcResponse,
    ArcTrackPatch,
    ArcTrackResponse,
    CharacterAddressReplacement,
    CharacterCollectionItem,
    ConflictStage,
    GlobalCollectionAdd,
    GlobalCollectionDelete,
    GlobalCollectionSplit,
    GlobalCharacterPatch,
    GlobalEdit,
    GlobalEditQuarantineGroup,
    RelationPatch,
    RelationResponse,
    SecretPatch,
    SecretResponse,
    SettingPatch,
    WorldRulePatch,
    WorldRuleResponse,
)
from novel.agents.session_client import agent_json_schema


WIRE_SCHEMA = 'novel-stage2-reviewer-delta-v1'


class Stage2ReviewerWireError(RuntimeError):
    """紧凑响应不能由当前 immutable option 唯一、无损地组装。"""


class _ReviewerTaskLike(Protocol):
    submission_options: Sequence[Mapping[str, Any]]


@dataclass(frozen=True)
class Stage2ReviewerWireBinding:
    """需要随 prompt 明示、也由动态 schema 固定的两枚响应身份。"""

    option_id: str
    expected_exact_request_sha256: str


class Stage2PrereviewResponse(StrictModel):
    """Stage2 无写权预审的原生 structured-output 载体。

    本模型只让 CLI 在返回前保证 JSON/typed 形状；business/workpack binding、可编辑
    字段及 sentinel 仍由 ``reviewer_tasks.load_stage2_prereview`` 复核。
    """

    schema_: Literal['novel-stage2-global-prereview-v1'] = Field(alias='schema')
    business_sha256: Sha256
    workpack_sha256: Sha256
    input_payload: GlobalReviewDecisionSet


_PATCH_MODELS: dict[str, tuple[type[StrictModel], tuple[str, ...]]] = {
    'secrets': (SecretPatch, ('secret_id',)),
    'world_rules': (WorldRulePatch, ('rule_id',)),
    'characters': (GlobalCharacterPatch, ('char_id',)),
    'relations': (RelationPatch, ('relation_id',)),
    'arc_tracks': (ArcTrackPatch, ('track_index',)),
    'addresses': (AddressContextPatch, ('char_id', 'address_index')),
    # 这是单个人物称呼列表的完整替换，char_id 仍是当前模板开放的稳定身份。
    'addresses_replace': (CharacterAddressReplacement, ('char_id',)),
}

_REPLACEMENT_WIRE_TYPES: dict[str, Any] = {
    # 生产形状是 char_id -> CharacterResponse 的映射；agent portable schema 禁止
    # 动态 object 键，因此 wire 提升为带显式 char_id 的行数组。
    'characters_replace': list[CharacterCollectionItem] | None,
    'secrets_replace': list[SecretResponse] | None,
    'world_rules_replace': list[WorldRuleResponse] | None,
    'relations_replace': list[RelationResponse] | None,
    'conflict_stages_replace': list[ConflictStage] | None,
    'arc_tracks_replace': list[ArcTrackResponse] | None,
    'arc_replace': ArcResponse | None,
}


def _collection_operation_type(label: str, member_model: type[StrictModel]) -> Any:
    """从现役 operation model 专型化成员，不在 wire 层复制键集/约束。"""
    add_model = create_model(
        f'Stage2Reviewer{label}Add',
        __base__=GlobalCollectionAdd,
        item=(member_model, copy.deepcopy(
            GlobalCollectionAdd.model_fields['item'])),
    )
    split_model = create_model(
        f'Stage2Reviewer{label}Split',
        __base__=GlobalCollectionSplit,
        items=(list[member_model], copy.deepcopy(
            GlobalCollectionSplit.model_fields['items'])),
    )
    return Annotated[
        add_model | GlobalCollectionDelete | split_model,
        Field(discriminator='op'),
    ]


_CharacterOperation = _collection_operation_type(
    'Character', CharacterCollectionItem)
_RelationOperation = _collection_operation_type('Relation', RelationResponse)
_SecretOperation = _collection_operation_type('Secret', SecretResponse)
_WorldRuleOperation = _collection_operation_type('WorldRule', WorldRuleResponse)


class _CharacterCollectionDelta(StrictModel):
    collection: Literal['characters']
    operations: list[_CharacterOperation]


class _RelationCollectionDelta(StrictModel):
    collection: Literal['relations']
    operations: list[_RelationOperation]


class _SecretCollectionDelta(StrictModel):
    collection: Literal['secrets']
    operations: list[_SecretOperation]


class _WorldRuleCollectionDelta(StrictModel):
    collection: Literal['world_rules']
    operations: list[_WorldRuleOperation]


_COLLECTION_WIRE_MODELS: dict[str, type[StrictModel]] = {
    'characters': _CharacterCollectionDelta,
    'relations': _RelationCollectionDelta,
    'secrets': _SecretCollectionDelta,
    'world_rules': _WorldRuleCollectionDelta,
}


def _canonical_sha256(value: object) -> str:
    payload = json.dumps(
        value, ensure_ascii=False, sort_keys=True,
        separators=(',', ':'), allow_nan=False,
    ).encode('utf-8')
    return hashlib.sha256(payload).hexdigest()


def _apply_edit_option(
    task: _ReviewerTaskLike,
) -> tuple[str, dict[str, Any]]:
    # compact envelope 只携一枚 option_id，不是通用多选动作联合。即使多选任务中
    # 恰好只有一条 apply-edit，也不能据此把其他合法 option 从 provider schema /
    # prompt 中静默裁掉；多选任务必须继续走现役完整 action 协议。
    options = task.submission_options
    if len(options) != 1:
        raise Stage2ReviewerWireError(
            'compact wire 只允许唯一 submission option 的 Stage2 apply-edit task')
    option = options[0]
    if not isinstance(option, Mapping):
        raise Stage2ReviewerWireError('唯一 submission option 不是 object')
    option_id = option.get('id')
    exact = option.get('exact_request')
    action = exact.get('action') if isinstance(exact, Mapping) else None
    flags = action.get('flags') if isinstance(action, Mapping) else None
    if (not isinstance(option_id, str) or not option_id
            or not isinstance(exact, dict)
            or not isinstance(action, Mapping)
            or action.get('kind') != 'node'
            or action.get('node') != 'stage2.global'
            or not isinstance(flags, list)
            or '--apply-edit' not in flags):
        raise Stage2ReviewerWireError(
            '唯一 submission option 不是 stage2.global --apply-edit')
    return option_id, exact


def wire_binding(task: _ReviewerTaskLike) -> Stage2ReviewerWireBinding:
    """返回 compact response 必须逐字回传的 option 与 exact-request 指纹。"""
    option_id, exact = _apply_edit_option(task)
    return Stage2ReviewerWireBinding(
        option_id=option_id,
        expected_exact_request_sha256=_canonical_sha256(exact),
    )


def uses_compact_wire(task: object) -> bool:
    """只按真实 option 形状判断本轮是否属于 compact apply-edit。"""
    options = getattr(task, 'submission_options', None)
    if (not isinstance(options, Sequence)
            or isinstance(options, (str, bytes, bytearray))):
        return False
    try:
        _apply_edit_option(task)  # type: ignore[arg-type]
    except (AttributeError, Stage2ReviewerWireError):
        return False
    return True


def _field_for_json_key(
    model_cls: type[StrictModel], key: str,
) -> tuple[str, Any, Field]:
    for name, info in model_cls.model_fields.items():
        alias = info.serialization_alias or info.alias or name
        if alias == key:
            # rebuild_annotation 保留 min_length/pattern 等 Field metadata；新 Field
            # 只负责 required 与 wire alias，不复制生产 validator。
            return name, info.rebuild_annotation(), Field(..., alias=key)
    raise Stage2ReviewerWireError(
        f'可信 Stage2 模板含未登记字段：{model_cls.__name__}.{key}')


def _literal(values: Sequence[Any]) -> Any:
    """运行时构造 ``Literal[v1, ...]``，供动态 option/subject enum 使用。"""
    if not values:
        raise Stage2ReviewerWireError('不能为零个可信值构造 Literal')
    return Literal.__getitem__(tuple(values))


def _union(models: Sequence[type[StrictModel]]) -> Any:
    if not models:
        # tuple[()] 的 JSON Schema 明确 maxItems=0；比假造一个 sentinel 行更安全。
        return tuple[()]
    if len(models) == 1:
        return list[models[0]]
    return list[reduce(or_, models)]


def _patch_list_wire_type(
    *, field: str, rows: Any, model_cls: type[StrictModel],
    identities: tuple[str, ...], digest: str,
) -> Any:
    if not isinstance(rows, list) or any(not isinstance(row, dict) for row in rows):
        raise Stage2ReviewerWireError(f'可信模板 GlobalEdit.{field} 必须是 object 数组')
    seen: set[tuple[Any, ...]] = set()
    # 同一 patch 面的大多数主体开放完全相同的字段。若按主体各铸一个 model，
    # Pydantic 会把相同字段 schema 在 $defs 重复几十次，真实 GlobalEdit 在
    # Windows 上会把 Claude --json-schema argv 撑过 32767。按开放字段集合分组，
    # identity 只下发 enum；复合 identity 的合法配对仍由下方 trusted assembler
    # 按 exact template 逐行关闭，不能把 provider schema 当业务判据。
    groups: dict[tuple[str, ...], list[dict[str, Any]]] = {}
    for row in rows:
        identity = tuple(row.get(key) for key in identities)
        if any(value is None for value in identity) or identity in seen:
            raise Stage2ReviewerWireError(
                f'可信模板 GlobalEdit.{field} 的稳定身份缺失或重复')
        seen.add(identity)
        groups.setdefault(tuple(sorted(row)), []).append(row)

    row_models: list[type[StrictModel]] = []
    for index, (keys, grouped_rows) in enumerate(groups.items()):
        dynamic_fields: dict[str, tuple[Any, Any]] = {}
        for key in keys:
            name, annotation, required = _field_for_json_key(model_cls, key)
            if key in identities:
                annotation = _literal([row[key] for row in grouped_rows])
            dynamic_fields[name] = (annotation, required)
        row_models.append(create_model(
            f'Stage2{field.title().replace("_", "")}DeltaShape{index}_{digest}',
            __base__=StrictModel,
            **dynamic_fields,
        ))
    return _union(row_models)


def _setting_wire_type(value: Any, digest: str) -> Any:
    if not isinstance(value, dict) or not value:
        raise Stage2ReviewerWireError('可信模板 GlobalEdit.setting 必须是非空 object')
    fields: dict[str, tuple[Any, Any]] = {}
    for key in value:
        name, annotation, required = _field_for_json_key(SettingPatch, key)
        fields[name] = (annotation, required)
    model = create_model(
        f'Stage2SettingDelta_{digest}', __base__=StrictModel, **fields)
    return model | None


def _collection_list_wire_type(value: Any) -> Any:
    if not isinstance(value, list) or any(not isinstance(row, dict) for row in value):
        raise Stage2ReviewerWireError(
            '可信模板 GlobalEdit.collection_edits 必须是 object 数组')
    models: list[type[StrictModel]] = []
    seen: set[str] = set()
    for row in value:
        collection = row.get('collection')
        expected_sha = row.get('expected_collection_sha256')
        if (collection not in _COLLECTION_WIRE_MODELS
                or not isinstance(expected_sha, str)
                or set(row) != {
                    'collection', 'expected_collection_sha256', 'operations'}):
            raise Stage2ReviewerWireError('可信 Stage2 collection template 形状漂移')
        assert isinstance(collection, str)
        if collection in seen:
            raise Stage2ReviewerWireError('可信模板重复开放同一 collection')
        seen.add(collection)
        models.append(_COLLECTION_WIRE_MODELS[collection])
    return _union(models)


def _post_review_wire_type(value: Any, digest: str) -> type[StrictModel]:
    if not isinstance(value, dict):
        raise Stage2ReviewerWireError('reviewed GlobalEdit 缺 post_edit_review')
    scope = value.get('review_scope')
    if not isinstance(scope, list) or any(not isinstance(row, dict) for row in scope):
        raise Stage2ReviewerWireError('可信模板 post_edit_review.review_scope 形状漂移')
    ids: list[str] = []
    hashes: set[tuple[str, str]] = set()
    for row in scope:
        item_id = row.get('item_id')
        subject_sha = row.get('expected_subject_sha256')
        if not isinstance(item_id, str) or not isinstance(subject_sha, str):
            raise Stage2ReviewerWireError('可信模板 review scope 缺 item/hash 身份')
        if (item_id, subject_sha) in hashes or item_id in ids:
            raise Stage2ReviewerWireError('可信模板 review scope 身份重复或一项多 hash')
        ids.append(item_id)
        hashes.add((item_id, subject_sha))
    if not ids:
        raise Stage2ReviewerWireError('reviewed GlobalEdit 的 review scope 不能为空')

    decision_model = create_model(
        f'Stage2PostEditDecision_{digest}',
        __base__=StrictModel,
        item_id=(_literal(ids), ...),
        state=(Literal['PASS', 'FAIL', 'UNVER'], ...),
        reason=(Annotated[str, Field(min_length=1)], ...),
        evidence=(list[EvidenceRef], ...),
        generation_disposition=(GlobalReviewGenerationDisposition | None, ...),
    )
    return create_model(
        f'Stage2PostEditReviewDelta_{digest}',
        __base__=StrictModel,
        reason=(Annotated[str, Field(min_length=1)], ...),
        quarantine_groups=(list[GlobalEditQuarantineGroup], ...),
        decisions=(list[decision_model], ...),
    )


def response_model(task: _ReviewerTaskLike) -> type[StrictModel]:
    """从当前 exact request 铸一份 portable、关闭式的动态响应模型。"""
    option_id, exact = _apply_edit_option(task)
    exact_sha = _canonical_sha256(exact)
    digest = exact_sha[:12]
    action = exact.get('action')
    edit = action.get('input_payload') if isinstance(action, dict) else None
    if not isinstance(edit, dict):
        raise Stage2ReviewerWireError('apply-edit exact request 缺 GlobalEdit payload')
    try:
        GlobalEdit.model_validate(edit)
    except ValidationError as exc:
        raise Stage2ReviewerWireError('apply-edit exact request 不是现役 GlobalEdit') from exc

    edit_fields: dict[str, tuple[Any, Any]] = {}
    for key, value in edit.items():
        if key in _PATCH_MODELS:
            model_cls, identities = _PATCH_MODELS[key]
            annotation = _patch_list_wire_type(
                field=key, rows=value, model_cls=model_cls,
                identities=identities, digest=digest)
        elif key == 'setting':
            annotation = _setting_wire_type(value, digest)
        elif key in _REPLACEMENT_WIRE_TYPES:
            annotation = _REPLACEMENT_WIRE_TYPES[key]
        elif key == 'collection_edits':
            annotation = _collection_list_wire_type(value)
        elif key == 'post_edit_review':
            annotation = _post_review_wire_type(value, digest)
        else:
            # 新增 GlobalEdit 字段时必须在这里明确它属于 patch、replacement 还是
            # immutable；绝不把未知字段降成 Any 后交给 Commander。
            raise Stage2ReviewerWireError(
                f'compact reviewer wire 未登记 GlobalEdit 顶层字段：{key}')
        edit_fields[key] = (annotation, ...)

    edit_model = create_model(
        f'Stage2ReviewerEditDelta_{digest}',
        __base__=StrictModel,
        **edit_fields,
    )
    payload_model = create_model(
        f'Stage2ReviewerDeltaPayload_{digest}',
        __base__=StrictModel,
        edit=(edit_model, ...),
    )
    return create_model(
        f'Stage2ReviewerDeltaEnvelope_{digest}',
        __base__=StrictModel,
        schema_=(Literal[WIRE_SCHEMA], Field(..., alias='schema')),
        option_id=(_literal([option_id]), ...),
        expected_exact_request_sha256=(_literal([exact_sha]), ...),
        payload=(payload_model, ...),
    )


def response_schema(task: _ReviewerTaskLike) -> dict[str, Any]:
    """返回与 Executor 共用同一归一口的 provider structured-output schema。"""
    return agent_json_schema(response_model(task))


def output_schema_for(task: object) -> dict[str, Any] | None:
    """返回 Stage2 Commander task 的原生输出 schema；其他任务保持旧协议。

    ``Stage2PrereviewTask`` 与 ``ReviewerTask`` 都定义在 ``reviewer_tasks``，本模块
    不能反向 import 它们，否则调用方接线会形成环。这里按两种冻结 dataclass 的
    必要字段辨认，最终的本地 loader 仍会校验真实 task 类型与全部身份。
    """
    if (hasattr(task, 'input_template')
            and hasattr(task, 'business_sha256')
            and hasattr(task, 'workpack_sha256')
            and not hasattr(task, 'submission_options')):
        return agent_json_schema(Stage2PrereviewResponse)
    if uses_compact_wire(task):
        return response_schema(task)  # type: ignore[arg-type]
    return None


def prompt_addendum(task: object) -> str:
    """给 apply-edit 主提示/纠正提示追加唯一的 compact 输出例外。

    不嵌 response schema；同一份 schema 会由 CLI 原生 structured-output 参数发送。
    示例只说明 envelope 与保留语义，业务行的精确闭集以该原生 schema 为准。
    """
    if not uses_compact_wire(task):
        return ''
    option_id, exact = _apply_edit_option(task)  # type: ignore[arg-type]
    exact_sha = _canonical_sha256(exact)
    edit = exact['action']['input_payload']
    compact_edit: dict[str, Any] = {}
    for key in edit:
        if key in _PATCH_MODELS or key == 'collection_edits':
            compact_edit[key] = []
        elif key == 'setting' or key in _REPLACEMENT_WIRE_TYPES:
            compact_edit[key] = None
        elif key == 'post_edit_review':
            compact_edit[key] = {
                'reason': '填写修订后逐项、跨项复核的精确理由',
                'quarantine_groups': [],
                'decisions': [],
            }
        else:  # response_model 会给出更完整的配置错误。
            raise Stage2ReviewerWireError(
                f'compact reviewer prompt 未登记 GlobalEdit 顶层字段：{key}')
    example = {
        'schema': WIRE_SCHEMA,
        'option_id': option_id,
        'expected_exact_request_sha256': exact_sha,
        'payload': {'edit': compact_edit},
    }
    rendered = json.dumps(example, ensure_ascii=False, separators=(',', ':'))
    return f"""

【Stage2 apply-edit 的唯一 compact 输出例外】
上文“完整复制 exact_request”的要求在本轮 apply-edit 不适用；不要回抄完整 wrapper、
未变 patch 行、expected_collection_sha256、post_edit_review 的 actor/source/workpack/
chapter/default/review_scope，也不要在 decisions 行回抄 expected_subject_sha256。Supervisor
只会从当前 immutable exact_request 可信注入这些字段，并在组装后继续走现役 loader。

最终只输出 ``{WIRE_SCHEMA}`` 裸 JSON object。``payload.edit`` 的每个顶层键都必须按
CLI 原生 schema 出现：patch/collection 数组 ``[]`` 表示保留模板；setting/replacement
的 ``null`` 表示保留模板，绝不表示清空。patch 行缺席表示保留；行一旦出现，必须交回
该模板主体已经开放的完整字段集，不能新增主体或字段。collection 行只交 collection +
operations；add 用 ``{{"op":"add","item":...}}``，不得使用 ``value``。修后复核只交
reason/quarantine_groups/decisions；每条 decision 交 item_id/state/reason/evidence/
generation_disposition（无 disposition 时显式 null），Supervisor 按 item_id 注入 subject
hash。仍以同一 hash 存活的旧 FAIL/UNVER（包括集合完整性项）必须逐项列入 decisions；
不能让 ``decisions:[]`` 示例替代真实闭合结果。

最小 envelope（空数组/null 只演示保留语义，须按实际审阅填入改动和 decisions）：
{rendered}
""".strip()


def prompt_for(task: object, base_prompt: str) -> str:
    """把 compact 例外追加到现役主提示或 fresh-session 纠正提示。"""
    addendum = prompt_addendum(task)
    if not addendum:
        return base_prompt
    return f'{base_prompt.rstrip()}\n\n{addendum}\n'


def _schema_property_names(value: object) -> set[str]:
    """只从可信动态 schema 收集可出现在拒因中的 property 名。"""
    found: set[str] = set()
    if isinstance(value, Mapping):
        properties = value.get('properties')
        if isinstance(properties, Mapping):
            found.update(str(key) for key in properties)
        for child in value.values():
            found.update(_schema_property_names(child))
    elif isinstance(value, list):
        for child in value:
            found.update(_schema_property_names(child))
    return found


def _validation_locations(
    exc: ValidationError, *, model: type[StrictModel] | None = None,
) -> str:
    """输出无模型值、无任意 extra-key 的有界定位摘要。"""
    trusted = (
        set() if model is None
        else _schema_property_names(model.model_json_schema(by_alias=True)))

    def safe_part(value: object) -> str:
        if isinstance(value, int):
            return str(value)
        text = str(value)
        if text in trusted:
            return text
        return 'key_sha256_' + hashlib.sha256(
            text.encode('utf-8')).hexdigest()[:16]

    errors = exc.errors(include_url=False, include_context=False, include_input=False)
    locations = []
    for row in errors[:8]:
        location = '.'.join(safe_part(part) for part in row.get('loc', ())) or '$'
        error_type = str(row.get('type', 'validation_error'))
        if (not error_type or len(error_type) > 80
                or any(not (ch.isascii() and (ch.isalnum() or ch in '_-'))
                       for ch in error_type)):
            error_type = 'error_sha256_' + hashlib.sha256(
                error_type.encode('utf-8')).hexdigest()[:16]
        locations.append(f'{location}:{error_type}')
    suffix = '…' if len(errors) > 8 else ''
    return '、'.join(locations) + suffix


def _parsed_document(
    task: _ReviewerTaskLike, response_document: Mapping[str, Any] | str,
) -> dict[str, Any]:
    model = response_model(task)
    try:
        if isinstance(response_document, str):
            parsed = model.model_validate_json(response_document)
        elif isinstance(response_document, Mapping):
            parsed = model.model_validate(dict(response_document))
        else:
            raise Stage2ReviewerWireError('Stage2 compact reviewer 响应必须是 JSON object')
    except ValidationError as exc:
        raise Stage2ReviewerWireError(
            'Stage2 compact reviewer 响应不符合当前动态 schema：'
            f'{_validation_locations(exc, model=model)}') from exc
    return parsed.model_dump(mode='json', by_alias=True)


def _identity(row: Mapping[str, Any], fields: tuple[str, ...]) -> tuple[Any, ...]:
    return tuple(row.get(field) for field in fields)


def _overlay_patch_rows(
    *, field: str, baseline: Any, submitted: Any,
    identities: tuple[str, ...],
) -> tuple[list[dict[str, Any]], bool]:
    if not isinstance(baseline, list) or not isinstance(submitted, list):
        raise Stage2ReviewerWireError(f'GlobalEdit.{field} patch wire 不是数组')
    by_identity = {
        _identity(row, identities): row
        for row in baseline if isinstance(row, dict)
    }
    if len(by_identity) != len(baseline):
        raise Stage2ReviewerWireError(f'可信模板 GlobalEdit.{field} 身份不唯一')
    seen: set[tuple[Any, ...]] = set()
    changed = False
    for row in submitted:
        if not isinstance(row, dict):  # Pydantic 已查；保留防御式边界。
            raise Stage2ReviewerWireError(f'GlobalEdit.{field} patch 行不是 object')
        row_identity = _identity(row, identities)
        if row_identity in seen:
            raise Stage2ReviewerWireError(f'GlobalEdit.{field} compact patch 重复主体')
        seen.add(row_identity)
        target = by_identity.get(row_identity)
        if target is None:
            raise Stage2ReviewerWireError(f'GlobalEdit.{field} compact patch 越出模板主体')
        if set(row) != set(target):
            raise Stage2ReviewerWireError(
                f'GlobalEdit.{field} compact patch 越出模板开放字段')
        if row == target:
            raise Stage2ReviewerWireError(
                f'GlobalEdit.{field} compact patch 没有形成实际变更')
        target.clear()
        target.update(copy.deepcopy(row))
        changed = True
    return baseline, changed


def _wire_characters_replace(value: Any) -> dict[str, Any] | None:
    if value is None:
        return None
    if not isinstance(value, list):
        raise Stage2ReviewerWireError('characters_replace compact wire 必须是行数组或 null')
    output: dict[str, Any] = {}
    for row in value:
        if not isinstance(row, dict):
            raise Stage2ReviewerWireError('characters_replace compact wire 行不是 object')
        char_id = row.get('char_id')
        if not isinstance(char_id, str) or char_id in output:
            raise Stage2ReviewerWireError('characters_replace char_id 缺失或重复')
        output[char_id] = {
            key: copy.deepcopy(item) for key, item in row.items() if key != 'char_id'
        }
    return output


def _overlay_collection_rows(
    baseline: Any, submitted: Any,
) -> tuple[list[dict[str, Any]], bool]:
    if not isinstance(baseline, list) or not isinstance(submitted, list):
        raise Stage2ReviewerWireError('GlobalEdit.collection_edits compact wire 不是数组')
    by_collection = {
        row.get('collection'): row
        for row in baseline if isinstance(row, dict)
    }
    if len(by_collection) != len(baseline):
        raise Stage2ReviewerWireError('可信 collection template 身份不唯一')
    seen: set[str] = set()
    changed = False
    for row in submitted:
        if not isinstance(row, dict):
            raise Stage2ReviewerWireError('collection compact row 不是 object')
        collection = row.get('collection')
        if not isinstance(collection, str) or collection in seen:
            raise Stage2ReviewerWireError('collection compact row 身份缺失或重复')
        seen.add(collection)
        target = by_collection.get(collection)
        if target is None:
            raise Stage2ReviewerWireError('collection compact row 越出模板集合')
        operations = row.get('operations')
        if operations == target.get('operations'):
            raise Stage2ReviewerWireError(
                f'collection {collection} operations 没有形成实际变更')
        # expected_collection_sha256 只能来自可信模板，wire 没有这一格。
        target['operations'] = copy.deepcopy(operations)
        changed = True
    return baseline, changed


def _overlay_post_review(
    baseline: Any, submitted: Any,
) -> tuple[dict[str, Any], bool]:
    if not isinstance(baseline, dict) or not isinstance(submitted, dict):
        raise Stage2ReviewerWireError('post_edit_review compact wire 不是 object')
    scope = baseline.get('review_scope')
    if not isinstance(scope, list):
        raise Stage2ReviewerWireError('可信 post-edit review scope 不是数组')
    hashes: dict[str, str] = {}
    for row in scope:
        if not isinstance(row, dict):
            raise Stage2ReviewerWireError('可信 post-edit review scope 行不是 object')
        item_id = row.get('item_id')
        subject_sha = row.get('expected_subject_sha256')
        if (not isinstance(item_id, str) or not isinstance(subject_sha, str)
                or item_id in hashes):
            raise Stage2ReviewerWireError('可信 post-edit review scope 身份不唯一')
        hashes[item_id] = subject_sha

    submitted_decisions = submitted.get('decisions')
    if not isinstance(submitted_decisions, list):
        raise Stage2ReviewerWireError('post_edit_review.decisions compact wire 不是数组')
    decisions: list[dict[str, Any]] = []
    seen: set[str] = set()
    for row in submitted_decisions:
        if not isinstance(row, dict):
            raise Stage2ReviewerWireError('post-edit decision compact row 不是 object')
        item_id = row.get('item_id')
        if not isinstance(item_id, str) or item_id in seen:
            raise Stage2ReviewerWireError('post-edit decision item_id 缺失或重复')
        seen.add(item_id)
        subject_sha = hashes.get(item_id)
        if subject_sha is None:
            raise Stage2ReviewerWireError('post-edit decision 越出可信 review scope')
        decisions.append({
            'item_id': item_id,
            'expected_subject_sha256': subject_sha,
            **{
                key: copy.deepcopy(value)
                for key, value in row.items() if key != 'item_id'
            },
        })

    replacement = {
        'reason': copy.deepcopy(submitted.get('reason')),
        'quarantine_groups': copy.deepcopy(submitted.get('quarantine_groups')),
        'decisions': decisions,
    }
    changed = any(baseline.get(key) != value for key, value in replacement.items())
    for key, value in replacement.items():
        baseline[key] = value
    return baseline, changed


def assemble(
    task: _ReviewerTaskLike,
    response_document: Mapping[str, Any] | str,
) -> dict[str, Any]:
    """把 compact response 可信组装为完整 reviewer request document。

    返回值刻意不调用 ``reviewer_tasks.load_action``：调用方必须继续走现役 loader，
    让 wrapper binding、GlobalEdit 业务 schema、sentinel、HARD 与 producer completeness
    保持唯一判定源。
    """
    option_id, exact = _apply_edit_option(task)
    expected_sha = _canonical_sha256(exact)
    compact = _parsed_document(task, response_document)
    if compact.get('schema') != WIRE_SCHEMA:
        raise Stage2ReviewerWireError('Stage2 compact reviewer schema 漂移')
    if compact.get('option_id') != option_id:
        raise Stage2ReviewerWireError('Stage2 compact reviewer option_id 漂移')
    if compact.get('expected_exact_request_sha256') != expected_sha:
        raise Stage2ReviewerWireError('Stage2 compact reviewer exact-request SHA 已 stale')

    assembled = copy.deepcopy(exact)
    action = assembled.get('action')
    edit = action.get('input_payload') if isinstance(action, dict) else None
    payload = compact.get('payload')
    delta = payload.get('edit') if isinstance(payload, dict) else None
    if not isinstance(edit, dict) or not isinstance(delta, dict):
        raise Stage2ReviewerWireError('Stage2 compact reviewer edit payload 形状漂移')

    changed = False
    for key, value in delta.items():
        if key not in edit:
            raise Stage2ReviewerWireError(f'compact edit 越出模板顶层字段：{key}')
        if key in _PATCH_MODELS:
            _model, identities = _PATCH_MODELS[key]
            edit[key], local_changed = _overlay_patch_rows(
                field=key, baseline=edit[key], submitted=value,
                identities=identities)
        elif key == 'setting':
            if value is None:
                local_changed = False
            else:
                if value == edit[key]:
                    raise Stage2ReviewerWireError('GlobalEdit.setting 没有形成实际变更')
                edit[key] = copy.deepcopy(value)
                local_changed = True
        elif key in _REPLACEMENT_WIRE_TYPES:
            if value is None:
                # portable schema 中 null 是“不覆写可信模板”，绝不猜成“清空”。
                local_changed = False
            else:
                replacement = (
                    _wire_characters_replace(value)
                    if key == 'characters_replace' else copy.deepcopy(value))
                if replacement == edit[key]:
                    raise Stage2ReviewerWireError(
                        f'GlobalEdit.{key} 没有形成实际变更')
                edit[key] = replacement
                local_changed = True
        elif key == 'collection_edits':
            edit[key], local_changed = _overlay_collection_rows(edit[key], value)
        elif key == 'post_edit_review':
            edit[key], local_changed = _overlay_post_review(edit[key], value)
        else:  # response_model 已关闭；保留防御式边界。
            raise Stage2ReviewerWireError(f'compact edit 含未登记字段：{key}')
        changed = changed or local_changed

    if not changed or _canonical_sha256(assembled) == expected_sha:
        raise Stage2ReviewerWireError('Stage2 compact reviewer 响应是 no-op')
    try:
        # 这里只复验完整载体形状；现役 load_action 仍会再查 immutable binding、
        # placeholder 与业务拒因，producer 仍负责修后 completeness。
        GlobalEdit.model_validate(edit)
    except ValidationError as exc:
        raise Stage2ReviewerWireError(
            '可信组装后的 Stage2 GlobalEdit 不符合现役 typed 契约：'
            f'{_validation_locations(exc, model=GlobalEdit)}') from exc
    return assembled


__all__ = [
    'WIRE_SCHEMA',
    'Stage2ReviewerWireBinding',
    'Stage2ReviewerWireError',
    'Stage2PrereviewResponse',
    'assemble',
    'output_schema_for',
    'prompt_addendum',
    'prompt_for',
    'response_model',
    'response_schema',
    'uses_compact_wire',
    'wire_binding',
]
