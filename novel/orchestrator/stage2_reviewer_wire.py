#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Stage2 reviewed GlobalEdit 的紧凑 Commander wire 与可信组装器。

生产动作仍是 :class:`contracts.schema.stage2.GlobalEdit`，现役
``reviewer_tasks.load_action`` 仍拥有最终 binding 与业务判定。本模块只缩短
Commander 必须返回的运输形状：

* wrapper、collection SHA、review scope 与 subject SHA 等 immutable binding 都从
  ``submission_options[].exact_request`` 确定性注入；
* Commander 只返回当前模板已经开放的 patch 行、collection operations、replacement
  与修后复核判断；
* 最终仍组装成完整 ``novel-reviewer-action-v1`` document，再交现役 loader 复验。

不能用一份静态“所有字段都 Optional”的 Pydantic model：公共 agent JSON Schema 会
依 OpenAI strict 规则把所有 properties 列为 required，显式 ``null`` 随后既可能被
误当成清空，也会破坏生产 Patch 的 ``model_fields_set`` 语义。因此 schema 必须从
当前可信模板动态铸造。初始轮中 patch 行缺席表示本次不提交，可信模板只关闭可编辑
范围，不能被组装器复制成业务 patch；correction 的模板却是上一轮真实 failed sparse
edit，缺席必须表示保留，并只能通过 exact-request 动态铸出的 ``drop_targets`` 显式
撤销。这样既保持 portable structured output，又不会让 Supervisor 猜业务值或静默丢
掉前一席已经正确的修订。
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
    CharacterAddressReplacement,
    CharacterCollectionItem,
    GlobalCollectionAdd,
    GlobalCollectionDelete,
    GlobalCollectionSplit,
    GlobalCharacterPatch,
    SourceBlurbGrounding, EvidenceLocator, GlobalEdit,
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
from novel.orchestrator import queue, stage2_scope_wire


WIRE_SCHEMA = 'novel-stage2-reviewer-delta-v2'


# 这些文本逐字来自 ``GlobalCollectionEdit._operations_match_collection`` 的静态
# model-validator 分支。这里只在完全相等时投影成无输入的稳定 reason code；未知
# ``msg`` 仍只输出 Pydantic error type，绝不把可能含模型值的消息带进拒因/账本。
_SAFE_VALIDATION_REASONS: dict[str, tuple[str, str]] = {
    'Value error, split 输出不得复用被拆分 subject id': (
        'collection_split_reuses_subject_id',
        'split items 的稳定 id 不得复用被拆分的 subject_id',
    ),
    'Value error, 一个集合 subject 不能被 delete/split 多次': (
        'collection_subject_touched_more_than_once',
        '同一集合 subject 不得被 delete/split 多次',
    ),
    'Value error, 集合 add/split 输出 id 不得重复': (
        'collection_output_id_duplicated',
        'collection add/split 的输出 id 不得重复',
    ),
    'Value error, 集合操作的输入 id 与输出 id 不得重叠': (
        'collection_input_output_id_overlap',
        'collection operation 的输入 id 与输出 id 不得重叠',
    ),
    'Value error, 直接字段 patch 主体不得同时被同集合 delete/split': (
        'direct_patch_subject_removed_by_collection_edit',
        '直接字段 patch 主体不得同时被同集合 delete/split',
    ),
}


class Stage2ReviewerWireError(RuntimeError):
    """紧凑响应不能由当前 immutable option 唯一、无损地组装。"""


class _ReviewerTaskLike(Protocol):
    submission_options: Sequence[Mapping[str, Any]]


@dataclass(frozen=True)
class Stage2ReviewerWireBinding:
    """需要随 prompt 明示、也由动态 schema 固定的响应身份。"""

    option_id: str
    expected_exact_request_sha256: str
    # correction 的 failed edit 是必须默认保留的可信业务基线。只有这里按
    # exact request 动态铸出的 opaque target 才能被显式撤销；初始轮恒为空。
    allowed_drop_targets: tuple[str, ...] = ()


@dataclass(frozen=True)
class _DropTarget:
    """correction 中一个可撤销的 failed-edit 业务目标。"""

    kind: Literal['patch', 'field', 'collection']
    field: str
    identity: tuple[Any, ...] = ()
    description: str = ''


_PATCH_MODELS: dict[str, tuple[type[StrictModel], tuple[str, ...]]] = {
    'secrets': (SecretPatch, ('secret_id',)),
    'world_rules': (WorldRulePatch, ('rule_id',)),
    'characters': (GlobalCharacterPatch, ('char_id',)),
    'relations': (RelationPatch, ('relation_id',)),
}

_REPLACEMENT_WIRE_TYPES: dict[str, Any] = {
    'secrets_replace': list[SecretResponse] | None,
    'world_rules_replace': list[WorldRuleResponse] | None,
    'relations_replace': list[RelationResponse] | None,
    'source_blurb_replace': SourceBlurbGrounding[EvidenceLocator] | None,
}


_COLLECTION_MEMBER_MODELS: dict[str, tuple[str, type[StrictModel]]] = {
    'relations': ('Relation', RelationResponse),
    'secrets': ('Secret', SecretResponse),
    'world_rules': ('WorldRule', WorldRuleResponse),
}


def _collection_operation_type(
    collection: str,
    existing_subject_ids: tuple[str, ...],
    digest: str,
) -> Any:
    """从现役 model 专型化成员，并冻结 delete/split 的 current ID enum。

    add 的输出 ID 不能被现存主体枚举限制；它继续接受完整成员 model。当前集合
    为空时只保留 add 分支，不铸造一个可被模型提交的伪 sentinel。
    """
    try:
        label, member_model = _COLLECTION_MEMBER_MODELS[collection]
    except KeyError as exc:
        raise Stage2ReviewerWireError(
            f'可信模板含未知 collection：{collection!r}') from exc
    add_model = create_model(
        f'Stage2Reviewer{label}Add_{digest}',
        __base__=GlobalCollectionAdd,
        item=(member_model, copy.deepcopy(
            GlobalCollectionAdd.model_fields['item'])),
    )
    if not existing_subject_ids:
        return add_model
    subject_id_type = _literal(existing_subject_ids)
    delete_model = create_model(
        f'Stage2Reviewer{label}Delete_{digest}',
        __base__=GlobalCollectionDelete,
        subject_id=(subject_id_type, copy.deepcopy(
            GlobalCollectionDelete.model_fields['subject_id'])),
    )
    split_model = create_model(
        f'Stage2Reviewer{label}Split_{digest}',
        __base__=GlobalCollectionSplit,
        subject_id=(subject_id_type, copy.deepcopy(
            GlobalCollectionSplit.model_fields['subject_id'])),
        items=(list[member_model], copy.deepcopy(
            GlobalCollectionSplit.model_fields['items'])),
    )
    return Annotated[
        add_model | delete_model | split_model,
        Field(discriminator='op'),
    ]


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


def _is_correction_task(
    task: _ReviewerTaskLike, exact: Mapping[str, Any],
) -> bool:
    """只让冻结的 correction blocker 取得 merge/drop 语义。

    ``purpose='correction'`` 也可能只是同一初始 sparse task 的 typed-loader
    返答；那时没有一份已被 producer 拒绝的 durable business baseline，空数组
    仍必须表示“不提交”。只有 planner 已物化
    ``WAITING_ON_STAGE2_EDIT_CORRECTION``、且 exact request 携失败 edit 时，才允许
    merge/drop。wire 不能拿席位 purpose 猜这层业务语义。
    """
    exact_waiting_on = exact.get('waiting_on')
    task_waiting_on = getattr(task, 'waiting_on', exact_waiting_on)
    if ((exact_waiting_on == queue.WAITING_ON_STAGE2_EDIT_CORRECTION)
            != (task_waiting_on == queue.WAITING_ON_STAGE2_EDIT_CORRECTION)):
        raise Stage2ReviewerWireError(
            'Stage2 compact reviewer task/exact_request 的 correction 身份不一致')
    return exact_waiting_on == queue.WAITING_ON_STAGE2_EDIT_CORRECTION


def _drop_target_map(edit: Mapping[str, Any]) -> dict[str, _DropTarget]:
    """由 failed sparse edit 生成不需解析模型文本的撤销闭集。"""
    targets: dict[str, _DropTarget] = {}

    def register(target: _DropTarget) -> None:
        token = f'drop_{len(targets) + 1:04d}'
        targets[token] = target

    for field, value in edit.items():
        if field in _PATCH_MODELS:
            _model, identities = _PATCH_MODELS[field]
            if not isinstance(value, list):
                raise Stage2ReviewerWireError(
                    f'可信模板 GlobalEdit.{field} 必须是数组')
            for row in value:
                if not isinstance(row, Mapping):
                    raise Stage2ReviewerWireError(
                        f'可信模板 GlobalEdit.{field} 行不是 object')
                identity = _identity(row, identities)
                label = ','.join(
                    f'{key}={json.dumps(item, ensure_ascii=False)}'
                    for key, item in zip(identities, identity, strict=True)
                )
                register(_DropTarget(
                    kind='patch', field=field, identity=identity,
                    description=f'{field}[{label}]',
                ))
        elif field == 'setting' or field in _REPLACEMENT_WIRE_TYPES:
            if value is not None:
                register(_DropTarget(
                    kind='field', field=field, description=field))
        elif field == 'collection_edits':
            if not isinstance(value, list):
                raise Stage2ReviewerWireError(
                    '可信模板 GlobalEdit.collection_edits 必须是数组')
            for row in value:
                collection = row.get('collection') if isinstance(row, Mapping) else None
                if not isinstance(collection, str):
                    raise Stage2ReviewerWireError(
                        '可信 collection template 缺 collection 身份')
                register(_DropTarget(
                    kind='collection', field=field, identity=(collection,),
                    description=f'collection_edits[collection={collection}]',
                ))
        elif field == 'post_edit_review':
            continue
        else:
            raise Stage2ReviewerWireError(
                f'compact reviewer wire 未登记 GlobalEdit 顶层字段：{field}')
    return targets


def wire_binding(task: _ReviewerTaskLike) -> Stage2ReviewerWireBinding:
    """返回 compact response 必须逐字回传的 option 与 exact-request 指纹。"""
    option_id, exact = _apply_edit_option(task)
    edit = exact['action'].get('input_payload')
    if not isinstance(edit, Mapping):
        raise Stage2ReviewerWireError('apply-edit exact request 缺 GlobalEdit payload')
    drop_targets = (
        tuple(_drop_target_map(edit)) if _is_correction_task(task, exact) else ())
    return Stage2ReviewerWireBinding(
        option_id=option_id,
        expected_exact_request_sha256=_canonical_sha256(exact),
        allowed_drop_targets=drop_targets,
    )


def uses_compact_wire(task: object) -> bool:
    """只按真实模板判断本轮是否属于 compact scope 或 apply-edit。"""
    if stage2_scope_wire.exact_request(task) is not None:
        return True
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


def _collection_subject_enums(
    task: _ReviewerTaskLike,
    collections: Sequence[str],
) -> dict[str, tuple[str, ...]]:
    """读取 Supervisor 随 option 冻结的 current collection ID 闭集。"""
    option = task.submission_options[0]
    allowed = option.get('allowed_values') if isinstance(option, Mapping) else None
    raw = (
        allowed.get(
            'collection_operation_contract.existing_subject_ids_by_collection')
        if isinstance(allowed, Mapping) else None)
    if not isinstance(raw, Mapping):
        raise Stage2ReviewerWireError(
            'Stage2 compact collection 缺 current subject enum binding')
    output: dict[str, tuple[str, ...]] = {}
    for collection in collections:
        values = raw.get(collection)
        if (not isinstance(values, Sequence)
                or isinstance(values, (str, bytes, bytearray))
                or any(not isinstance(value, str) or not value for value in values)):
            raise Stage2ReviewerWireError(
                f'Stage2 {collection} current subject enum 形状漂移')
        frozen = tuple(values)
        if frozen != tuple(sorted(set(frozen))):
            raise Stage2ReviewerWireError(
                f'Stage2 {collection} current subject enum 必须去重升序')
        output[collection] = frozen
    return output


def _collection_object_wire_type(
    task: _ReviewerTaskLike, value: Any, digest: str,
) -> type[StrictModel]:
    """把 canonical collection 行铸成按集合固定键的关闭 object。

    provider 只填写每个集合的 operations 数组；collection identity 与 SHA 均由
    immutable template 注入。固定键让同一 collection 在 native schema 中不可重复，
    也比 ``[{collection, operations}]`` 少一层重复身份与 union 包装。
    """
    if not isinstance(value, list) or any(not isinstance(row, dict) for row in value):
        raise Stage2ReviewerWireError(
            '可信模板 GlobalEdit.collection_edits 必须是 object 数组')
    fields: dict[str, tuple[Any, Any]] = {}
    seen: set[str] = set()
    collections: list[str] = []
    for row in value:
        collection = row.get('collection')
        expected_sha = row.get('expected_collection_sha256')
        if (collection not in _COLLECTION_MEMBER_MODELS
                or not isinstance(expected_sha, str)
                or set(row) != {
                    'collection', 'expected_collection_sha256', 'operations'}):
            raise Stage2ReviewerWireError('可信 Stage2 collection template 形状漂移')
        assert isinstance(collection, str)
        if collection in seen:
            raise Stage2ReviewerWireError('可信模板重复开放同一 collection')
        seen.add(collection)
        collections.append(collection)
    subject_enums = _collection_subject_enums(task, collections)
    for collection in collections:
        operation_type = _collection_operation_type(
            collection, subject_enums[collection], digest)
        fields[collection] = (list[operation_type], ...)
    return create_model(
        f'Stage2CollectionEditsDelta_{digest}',
        __base__=StrictModel,
        **fields,
    )


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
    if stage2_scope_wire.exact_request(task) is not None:
        return stage2_scope_wire.response_model(task)
    option_id, exact = _apply_edit_option(task)
    # 原生 schema 只绑定可编辑形状与真实引用闭集。当前候选 token、理由与
    # 内容摘要由 assemble 精确核验，不进入 model 标题强制另开物理会话。
    digest = 'v2'
    action = exact.get('action')
    edit = action.get('input_payload') if isinstance(action, dict) else None
    if not isinstance(edit, dict):
        raise Stage2ReviewerWireError('apply-edit exact request 缺 GlobalEdit payload')
    try:
        GlobalEdit.model_validate(edit)
    except ValidationError as exc:
        raise Stage2ReviewerWireError('apply-edit exact request 不是现役 GlobalEdit') from exc

    correction = _is_correction_task(task, exact)
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
            annotation = _collection_object_wire_type(task, value, digest)
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
    payload_fields: dict[str, tuple[Any, Any]] = {'edit': (edit_model, ...)}
    if correction:
        drop_targets = tuple(_drop_target_map(edit))
        if not drop_targets:  # GlobalEdit 自身要求至少有一个业务 patch。
            raise Stage2ReviewerWireError(
                'Stage2 correction failed edit 没有可撤销业务目标')
        payload_fields['drop_targets'] = (
            list[_literal(drop_targets)], ...)
    payload_model = create_model(
        f'Stage2ReviewerDeltaPayload_{digest}',
        __base__=StrictModel,
        **payload_fields,
    )
    return create_model(
        f'Stage2ReviewerDeltaEnvelope_{digest}',
        __base__=StrictModel,
        schema_=(Literal[WIRE_SCHEMA], Field(..., alias='schema')),
        option_id=(_literal([option_id]), ...),
        expected_exact_request_sha256=(Sha256, ...),
        payload=(payload_model, ...),
    )


def response_schema(task: _ReviewerTaskLike) -> dict[str, Any]:
    """返回与 Executor 共用同一归一口的 provider structured-output schema。"""
    return agent_json_schema(response_model(task))


def output_schema_for(task: object) -> dict[str, Any] | None:
    """只向现役 Stage2 typed 动作下发原生 schema；非 Stage2 不在此接管。"""
    if stage2_scope_wire.exact_request(task) is not None:
        return agent_json_schema(stage2_scope_wire.response_model(task))
    if uses_compact_wire(task):
        return response_schema(task)  # type: ignore[arg-type]
    return None


def prompt_addendum(task: object) -> str:
    """给 apply-edit 主提示/纠正提示追加唯一的 compact 输出例外。

    不嵌 response schema；同一份 schema 会由 CLI 原生 structured-output 参数发送。
    示例只说明 envelope 与保留语义，业务行的精确闭集以该原生 schema 为准。
    """
    if stage2_scope_wire.exact_request(task) is not None:
        return stage2_scope_wire.prompt_addendum(task)
    if not uses_compact_wire(task):
        return ''
    option_id, exact = _apply_edit_option(task)  # type: ignore[arg-type]
    exact_sha = _canonical_sha256(exact)
    edit = exact['action']['input_payload']
    correction = _is_correction_task(task, exact)  # type: ignore[arg-type]
    drop_targets = _drop_target_map(edit) if correction else {}
    compact_edit: dict[str, Any] = {}
    for key in edit:
        if key in _PATCH_MODELS:
            compact_edit[key] = []
        elif key == 'collection_edits':
            if not isinstance(edit[key], list):
                raise Stage2ReviewerWireError(
                    '可信模板 GlobalEdit.collection_edits 必须是 object 数组')
            compact_edit[key] = {
                row['collection']: []
                for row in edit[key]
                if isinstance(row, dict) and isinstance(row.get('collection'), str)
            }
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
        'payload': {
            'edit': compact_edit,
            **({'drop_targets': []} if correction else {}),
        },
    }
    rendered = json.dumps(example, ensure_ascii=False, separators=(',', ':'))
    if correction:
        target_legend = json.dumps({
            token: target.description for token, target in drop_targets.items()
        }, ensure_ascii=False, separators=(',', ':'))
        delta_semantics = f"""
本轮是 correction：exact_request.input_payload 是上一轮在 canonical 边界被分类拒绝的
**failed sparse edit**（可能是 HARD、候选载体或 post-review 失败），也是本轮默认保留的
可信业务基线。patch 的 ``[]``、setting/replacement 的 ``null``，以及
``collection_edits`` 每个固定集合键的 ``[]`` 都表示不改上一轮该面，绝不表示删除。
显式 patch 行按稳定 identity 替换上一轮同一行；collection 固定键的非空数组完整替换
该 collection 的 operations。需要撤掉 failed edit 中
某个 patch 行、setting、replacement 或整条 collection edit 时，
把下面闭集里的 opaque token 放进 ``payload.drop_targets``；不得重复，也不得同时更新和
drop 同一目标。未列入 drop_targets 且未显式更新的上一轮改动必须保留。只有 post-review
需要补全时可以原样保留 failed business delta、只重做 post-review；若撤销后不再剩任何
业务 patch，则会被拒绝为 no-op。相同回答的无进展重演由持久 ledger 另行拦截。

本轮可撤销目标（token -> failed edit 目标）：{target_legend}
""".strip()
    else:
        delta_semantics = """
本轮不是 correction：patch/collection 数组 ``[]`` 表示本次不提交该面；setting/
replacement 的 ``null`` 表示本次不提交该面，绝不表示清空。只有显式交回的行才会成为
业务 patch。
""".strip()
    return f"""

【Stage2 apply-edit 的唯一 compact 输出例外】
上文“完整复制 exact_request”的要求在本轮 apply-edit 不适用；不要回抄完整 wrapper、
未变 patch 行、expected_collection_sha256、post_edit_review 的 actor/source/workpack/
chapter/default/review_scope，也不要在 decisions 行回抄 expected_subject_sha256。Supervisor
只会从当前 immutable exact_request 可信注入这些字段，并在组装后继续走现役 loader。

最终只输出 ``{WIRE_SCHEMA}`` 裸 JSON object。``payload.edit`` 的每个顶层键都必须按
CLI 原生 schema 出现。{delta_semantics}

patch 行一旦出现，必须交回该模板主体已经开放的完整字段集，不能新增主体或字段。
``collection_edits`` 是按模板开放集合固定键的 object，每个键的值只交 operations
数组；delete/split.subject_id 只能使用原生 schema 从 current collection 冻结的 enum，
该 enum 不限制 add.item 的新稳定 ID；同时 patch 后再 delete/split 同一主体仍会被
GlobalEdit 静态规则拒绝。add 用 ``{{"op":"add","item":...}}``，不得使用 ``value``。修后复核只交
reason/quarantine_groups/decisions；每条 decision 交 item_id/state/reason/evidence/
generation_disposition（无 disposition 时显式 null），Supervisor 按 item_id 注入 subject
hash。仍以同一 hash 存活的旧 FAIL/UNVER（包括集合完整性项）必须逐项列入 decisions；
不能让 ``decisions:[]`` 示例替代真实闭合结果。

最小 envelope（空数组/null 的精确语义见上文，须按实际审阅填入改动和 decisions）：
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
        safe_reason = _SAFE_VALIDATION_REASONS.get(str(row.get('msg', '')))
        if safe_reason is None:
            locations.append(f'{location}:{error_type}')
        else:
            reason_code, message = safe_reason
            locations.append(f'{location}:{reason_code}({message})')
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
    selected: list[dict[str, Any]] = []
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
        # 可信模板只负责关闭 identity / 开放键集。未出现的模板行绝不能
        # 渗入生产 GlobalEdit，否则 Patch.model_fields_set 会把它们全部当修改。
        selected.append(copy.deepcopy(row))
    return selected, bool(selected)


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


def _overlay_collection_object(
    baseline: Any, submitted: Any,
) -> tuple[list[dict[str, Any]], bool]:
    if not isinstance(baseline, list) or not isinstance(submitted, dict):
        raise Stage2ReviewerWireError(
            'GlobalEdit.collection_edits compact wire 不是固定键 object')
    template_rows: list[tuple[str, dict[str, Any]]] = []
    seen: set[str] = set()
    for row in baseline:
        collection = row.get('collection') if isinstance(row, dict) else None
        if not isinstance(collection, str) or collection in seen:
            raise Stage2ReviewerWireError('可信 collection template 身份不唯一')
        seen.add(collection)
        template_rows.append((collection, row))
    if set(submitted) != seen:
        raise Stage2ReviewerWireError(
            'collection compact object 的固定键与模板不一致')
    if len(template_rows) != len(baseline):
        raise Stage2ReviewerWireError('可信 collection template 身份不唯一')
    selected: list[dict[str, Any]] = []
    # canonical 输出顺序只认 immutable template；provider object 的插入顺序不是契约。
    for collection, target in template_rows:
        operations = submitted[collection]
        if not isinstance(operations, list):
            raise Stage2ReviewerWireError(
                f'collection {collection} compact operations 不是数组')
        if not operations:
            continue
        if operations == target.get('operations'):
            raise Stage2ReviewerWireError(
                f'collection {collection} operations 没有形成实际变更')
        # expected_collection_sha256 只能来自可信模板，wire 没有这一格；
        # 未显式提交的其他 collection 行不进业务 edit。
        selected.append({
            'collection': collection,
            'expected_collection_sha256': copy.deepcopy(
                target.get('expected_collection_sha256')),
            'operations': copy.deepcopy(operations),
        })
    return selected, bool(selected)


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

    # 修后复核不是业务 patch，但它的阅读身份必须从 immutable exact request
    # 完整注入；provider 只能填写下面三格。
    replacement = copy.deepcopy(baseline)
    editable = {
        'reason': copy.deepcopy(submitted.get('reason')),
        'quarantine_groups': copy.deepcopy(submitted.get('quarantine_groups')),
        'decisions': decisions,
    }
    changed = any(baseline.get(key) != value for key, value in editable.items())
    replacement.update(editable)
    return replacement, changed


def _correction_baseline(template_edit: Mapping[str, Any]) -> dict[str, Any]:
    """把 failed edit 还原为只含真实业务 delta 的 merge 基线。"""
    baseline: dict[str, Any] = {}
    for key, value in template_edit.items():
        if key in _PATCH_MODELS or key == 'collection_edits':
            if value:
                baseline[key] = copy.deepcopy(value)
        elif key == 'setting' or key in _REPLACEMENT_WIRE_TYPES:
            if value is not None:
                baseline[key] = copy.deepcopy(value)
        elif key == 'post_edit_review':
            continue
        else:
            raise Stage2ReviewerWireError(
                f'compact reviewer wire 未登记 GlobalEdit 顶层字段：{key}')
    return baseline


def _target_key(target: _DropTarget) -> tuple[str, str, tuple[Any, ...]]:
    return target.kind, target.field, target.identity


def _has_business_delta(edit: Mapping[str, Any]) -> bool:
    """按生产动作语义判断，而不是把空 collection scaffold 当改动。"""
    for key, value in edit.items():
        if key in _PATCH_MODELS:
            if isinstance(value, list) and value:
                return True
        elif key == 'collection_edits':
            if (isinstance(value, list)
                    and any(isinstance(row, Mapping) and row.get('operations')
                            for row in value)):
                return True
        elif key == 'setting' or key in _REPLACEMENT_WIRE_TYPES:
            if value is not None:
                return True
    return False


def _assemble_correction_edit(
    *, template_edit: dict[str, Any], delta: dict[str, Any],
    submitted_drop_targets: Any,
) -> dict[str, Any]:
    """以 failed sparse edit 为基线合并一轮显式 correction。"""
    if not isinstance(submitted_drop_targets, list):
        raise Stage2ReviewerWireError(
            'Stage2 correction compact wire 缺 drop_targets 数组')
    if len(submitted_drop_targets) != len(set(submitted_drop_targets)):
        raise Stage2ReviewerWireError(
            'Stage2 correction drop_targets 不得重复')

    allowed_targets = _drop_target_map(template_edit)
    reverse_targets = {
        _target_key(target): token for token, target in allowed_targets.items()
    }
    selected_targets: set[tuple[str, str, tuple[Any, ...]]] = set()
    merged = _correction_baseline(template_edit)

    for key, value in delta.items():
        if key not in template_edit:
            raise Stage2ReviewerWireError(f'compact edit 越出模板顶层字段：{key}')
        if key in _PATCH_MODELS:
            _model, identities = _PATCH_MODELS[key]
            selected, local_changed = _overlay_patch_rows(
                field=key, baseline=template_edit[key], submitted=value,
                identities=identities)
            if local_changed:
                current = merged.get(key)
                if not isinstance(current, list):  # failed edit 是可信 GlobalEdit。
                    raise Stage2ReviewerWireError(
                        f'可信 correction 基线缺 GlobalEdit.{key}')
                positions = {
                    _identity(row, identities): index
                    for index, row in enumerate(current)
                    if isinstance(row, Mapping)
                }
                for row in selected:
                    identity = _identity(row, identities)
                    target = ('patch', key, identity)
                    if target not in reverse_targets:
                        raise Stage2ReviewerWireError(
                            f'GlobalEdit.{key} correction 行越出 failed edit')
                    current[positions[identity]] = copy.deepcopy(row)
                    selected_targets.add(target)
        elif key == 'setting':
            if value is None:
                local_changed = False
            else:
                if value == template_edit[key]:
                    raise Stage2ReviewerWireError('GlobalEdit.setting 没有形成实际变更')
                if ('field', key, ()) not in reverse_targets:
                    raise Stage2ReviewerWireError(
                        'GlobalEdit.setting correction 越出 failed edit')
                merged[key] = copy.deepcopy(value)
                selected_targets.add(('field', key, ()))
                local_changed = True
        elif key in _REPLACEMENT_WIRE_TYPES:
            if value is None:
                local_changed = False
            else:
                replacement = (
                    _wire_characters_replace(value)
                    if key == 'characters_replace' else copy.deepcopy(value))
                if replacement == template_edit[key]:
                    raise Stage2ReviewerWireError(
                        f'GlobalEdit.{key} 没有形成实际变更')
                if ('field', key, ()) not in reverse_targets:
                    raise Stage2ReviewerWireError(
                        f'GlobalEdit.{key} correction 越出 failed edit')
                merged[key] = replacement
                selected_targets.add(('field', key, ()))
                local_changed = True
        elif key == 'collection_edits':
            selected, local_changed = _overlay_collection_object(
                template_edit[key], value)
            if local_changed:
                current = merged.get(key)
                if not isinstance(current, list):
                    raise Stage2ReviewerWireError(
                        '可信 correction 基线缺 GlobalEdit.collection_edits')
                by_collection = {
                    row.get('collection'): index
                    for index, row in enumerate(current)
                    if isinstance(row, Mapping)
                }
                for row in selected:
                    collection = row['collection']
                    target = ('collection', key, (collection,))
                    if target not in reverse_targets:
                        raise Stage2ReviewerWireError(
                            'collection correction 行越出 failed edit')
                    index = by_collection[collection]
                    current[index] = copy.deepcopy(row)
                    selected_targets.add(target)
                if not current:
                    merged.pop(key, None)
        elif key == 'post_edit_review':
            merged[key], _review_changed = _overlay_post_review(
                template_edit[key], value)
        else:  # response_model 已关闭；保留防御式边界。
            raise Stage2ReviewerWireError(f'compact edit 含未登记字段：{key}')

    for token in submitted_drop_targets:
        target = allowed_targets.get(token)
        if target is None:  # native schema 已查；保留防御式边界。
            raise Stage2ReviewerWireError(
                'Stage2 correction drop target 越出 failed edit 闭集')
        key = _target_key(target)
        if key in selected_targets:
            raise Stage2ReviewerWireError(
                'Stage2 correction 不得同时更新和 drop 同一目标')
        if target.kind == 'patch':
            _model, identities = _PATCH_MODELS[target.field]
            rows = merged.get(target.field)
            if not isinstance(rows, list):
                raise Stage2ReviewerWireError('correction drop patch 目标已不存在')
            remaining = [
                row for row in rows
                if not (isinstance(row, Mapping)
                        and _identity(row, identities) == target.identity)
            ]
            if len(remaining) == len(rows):
                raise Stage2ReviewerWireError('correction drop patch 目标已不存在')
            if remaining:
                merged[target.field] = remaining
            else:
                merged.pop(target.field, None)
        elif target.kind == 'field':
            if target.field not in merged:
                raise Stage2ReviewerWireError('correction drop field 目标已不存在')
            merged.pop(target.field)
        else:
            rows = merged.get('collection_edits')
            if not isinstance(rows, list):
                raise Stage2ReviewerWireError('correction drop collection 目标已不存在')
            remaining = [
                row for row in rows
                if not (isinstance(row, Mapping)
                        and (row.get('collection'),) == target.identity)
            ]
            if len(remaining) == len(rows):
                raise Stage2ReviewerWireError(
                    'correction drop collection 目标已不存在')
            if remaining:
                merged['collection_edits'] = remaining
            else:
                merged.pop('collection_edits', None)
    if not _has_business_delta(merged):
        raise Stage2ReviewerWireError(
            'Stage2 correction 撤销后没有任何业务 patch，结果是 no-op')
    return merged


def assemble(
    task: _ReviewerTaskLike,
    response_document: Mapping[str, Any] | str,
) -> dict[str, Any]:
    """把 compact response 可信组装为完整 reviewer request document。

    返回值刻意不调用 ``reviewer_tasks.load_action``：调用方必须继续走现役 loader，
    让 wrapper binding、GlobalEdit 业务 schema、sentinel、HARD 与 producer completeness
    保持唯一判定源。
    """
    if stage2_scope_wire.exact_request(task) is not None:
        try:
            return stage2_scope_wire.assemble(task, response_document)
        except ValueError as exc:
            raise Stage2ReviewerWireError(str(exc)) from exc
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
    template_edit = action.get('input_payload') if isinstance(action, dict) else None
    payload = compact.get('payload')
    delta = payload.get('edit') if isinstance(payload, dict) else None
    if not isinstance(template_edit, dict) or not isinstance(delta, dict):
        raise Stage2ReviewerWireError('Stage2 compact reviewer edit payload 形状漂移')

    if _is_correction_task(task, exact):
        sparse_edit = _assemble_correction_edit(
            template_edit=template_edit,
            delta=delta,
            submitted_drop_targets=(
                payload.get('drop_targets') if isinstance(payload, dict) else None),
        )
    else:
        sparse_edit = {}
        business_changed = False
        for key, value in delta.items():
            if key not in template_edit:
                raise Stage2ReviewerWireError(f'compact edit 越出模板顶层字段：{key}')
            if key in _PATCH_MODELS:
                _model, identities = _PATCH_MODELS[key]
                selected, local_changed = _overlay_patch_rows(
                    field=key, baseline=template_edit[key], submitted=value,
                    identities=identities)
                if local_changed:
                    sparse_edit[key] = selected
            elif key == 'setting':
                if value is None:
                    local_changed = False
                else:
                    if value == template_edit[key]:
                        raise Stage2ReviewerWireError(
                            'GlobalEdit.setting 没有形成实际变更')
                    sparse_edit[key] = copy.deepcopy(value)
                    local_changed = True
            elif key in _REPLACEMENT_WIRE_TYPES:
                if value is None:
                    # 初始轮中 null 是“本次不提交”，绝不猜成“清空”。
                    local_changed = False
                else:
                    replacement = (
                        _wire_characters_replace(value)
                        if key == 'characters_replace' else copy.deepcopy(value))
                    if replacement == template_edit[key]:
                        raise Stage2ReviewerWireError(
                            f'GlobalEdit.{key} 没有形成实际变更')
                    sparse_edit[key] = replacement
                    local_changed = True
            elif key == 'collection_edits':
                selected, local_changed = _overlay_collection_object(
                    template_edit[key], value)
                if local_changed:
                    sparse_edit[key] = selected
            elif key == 'post_edit_review':
                # post-review 的变化只是证明/binding，不能把一份无业务 patch 的
                # 回答包装成可执行 GlobalEdit。
                sparse_edit[key], _review_changed = _overlay_post_review(
                    template_edit[key], value)
                local_changed = False
            else:  # response_model 已关闭；保留防御式边界。
                raise Stage2ReviewerWireError(f'compact edit 含未登记字段：{key}')
            business_changed = business_changed or local_changed

        if not business_changed:
            raise Stage2ReviewerWireError('Stage2 compact reviewer 响应是 no-op')
    try:
        # 这里只复验完整载体形状；现役 load_action 仍会再查 immutable binding、
        # placeholder 与业务拒因，producer 仍负责修后 completeness。
        GlobalEdit.model_validate(sparse_edit)
    except ValidationError as exc:
        raise Stage2ReviewerWireError(
            '可信组装后的 Stage2 GlobalEdit 不符合现役 typed 契约：'
            f'{_validation_locations(exc, model=GlobalEdit)}') from exc
    assert isinstance(action, dict)  # 上方 template_edit 已关闭形状。
    action['input_payload'] = sparse_edit
    return assembled


__all__ = [
    'WIRE_SCHEMA',
    'Stage2ReviewerWireBinding',
    'Stage2ReviewerWireError',
    'assemble',
    'output_schema_for',
    'prompt_addendum',
    'prompt_for',
    'response_model',
    'response_schema',
    'uses_compact_wire',
    'wire_binding',
]
